"""
backfill_crear_logs.py — Rescata a `ops.process_log` la semana de historial de
creaciones que quedó fuera del espejo (paso previo a repuntar la vista Crear).

QUÉ FALTA. `crear_logs` (MySQL) arrancó el 15-jul; el espejo a kubera se
encendió el 23-jul. Esas 378 filas del intervalo no viajaron nunca. Sin ellas,
repuntar la vista de historial a kubera le borraría al panel su primera semana.

DOS COSAS QUE ESTE BACKFILL ARREGLA DE PASO:

1. `wc_id` SE PERDÍA. El espejo lo excluye del detalle a propósito
   (`{k: v for k, v in extra.items() if k != "wc_id"}`) y `ops.process_log` no
   tiene esa columna. Pero `/auditoria` lo necesita: es con lo que le pregunta
   a WooCommerce si el producto sigue vivo. Aquí entra dentro de `detalle`,
   que es donde debió ir siempre.

2. EL ORDEN NO PUEDE SER POR `id`. Los lectores buscan el último evento de cada
   SKU con `MAX(id)`. En kubera el id es una secuencia, así que estas filas de
   JULIO recibirían ids MÁS ALTOS que las de agosto y el "último evento" saldría
   invertido — el historial mostraría el estado equivocado. Por eso se preserva
   `created_at` explícitamente y las gemelas de lectura ordenan POR FECHA.

Idempotente: se salta lo ya cargado comparando `detail_ref`
(`mysql:crear_logs:<id>`), que es la huella que dejó el espejo.

Uso:
  ...python backend/scripts/backfill_crear_logs.py               # dry-run
  ...python backend/scripts/backfill_crear_logs.py --real --acepto-destino tukwcvsi
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for l in (ROOT / nombre).read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--acepto-destino", default="")
    args = ap.parse_args()

    E = cargar(".env")
    ref = (re.search(r"postgres\.([a-z0-9]+):", E["SUPABASE_DB_URL"]) or [None, ""])[1]
    if args.real and args.acepto_destino != ref[:8]:
        sys.exit(f"ABORT: con --real hay que nombrar el destino "
                 f"(--acepto-destino {ref[:8]}).")
    print(f"[{'REAL' if args.real else 'DRY-RUN'}] destino: {ref[:8]}…\n", flush=True)

    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    with my.cursor() as c:
        c.execute("""SELECT id, sku, wc_id, estado, paso, detalle, creado
                       FROM crear_logs ORDER BY id""")
        origen = c.fetchall()
    my.close()

    pg = psycopg2.connect(E["SUPABASE_DB_URL"], connect_timeout=25)
    with pg.cursor() as c:
        c.execute("""select detail_ref from ops.process_log
                      where proceso = 'crear' and detail_ref is not null""")
        ya = {r[0] for r in c.fetchall()}

    plan, sin_sku = [], 0
    for r in origen:
        ref_fila = f"mysql:crear_logs:{r['id']}"
        if ref_fila in ya:
            continue
        if not (r.get("sku") or "").strip():
            sin_sku += 1
            continue
        try:
            detalle = json.loads(r["detalle"]) if r.get("detalle") else {}
            if not isinstance(detalle, dict):
                detalle = {"valor": detalle}
        except (ValueError, TypeError):
            # detalle truncado a 4 KB por la salvaguarda: deja de ser JSON
            # válido. Se conserva como texto en vez de tirarlo.
            detalle = {"crudo": str(r["detalle"])[:4000]}
        if r.get("wc_id"):
            detalle["wc_id"] = int(r["wc_id"])
        plan.append((r["sku"], (r.get("paso") or "")[:255], r.get("estado"),
                     json.dumps(detalle, ensure_ascii=False, default=str),
                     ref_fila, r["creado"]))

    print(f"  filas en MySQL crear_logs      : {len(origen)}")
    print(f"  ya en kubera (por detail_ref)  : {len(ya)}")
    print(f"  sin sku (se descartan)         : {sin_sku}")
    print(f"  >>> A CARGAR                   : {len(plan)}")
    if plan:
        print(f"      rango: {plan[0][5]} .. {plan[-1][5]}")
        for p in plan[:5]:
            print(f"      {p[0]:24s} {str(p[2]):12s} {str(p[1])[:28]:30s} {p[5]}")

    # ── RELLENO de wc_id en lo que YA espejó el mirror ──────────────────────
    # Esas filas viajaron sin wc_id (el espejo lo excluye del detalle) y sin él
    # `/auditoria` no puede preguntarle a WooCommerce si el producto sigue vivo.
    # Se rellena por `detail_ref`, la huella que dejó el propio espejo.
    por_ref = {f"mysql:crear_logs:{r['id']}": int(r["wc_id"])
               for r in origen if r.get("wc_id")}
    with pg.cursor() as c:
        # `detalle is null or …`: sobre un jsonb NULO el operador `?` devuelve
        # NULL, no FALSE, y `not NULL` es NULL — la fila se caía del WHERE en
        # silencio. Y son justo las que más importan: el espejo guarda
        # `detalle` NULO cuando lo único que traía era el wc_id que él excluye.
        c.execute("""select detail_ref from ops.process_log
                      where proceso = 'crear' and detail_ref is not null
                        and (detalle is null or not (detalle ? 'wc_id'))""")
        refs_sin = [r[0] for r in c.fetchall() if r[0] in por_ref]
    relleno = [(json.dumps({"wc_id": por_ref[ref]}), ref) for ref in refs_sin]
    print(f"  >>> A RELLENAR con wc_id       : {len(relleno)}")

    # ── CORRECCIÓN de fechas ────────────────────────────────────────────────
    # `ops.process_log.created_at` se llenaba con el `now()` de la ESCRITURA del
    # espejo, no con la hora del evento. Para casi todo da igual (el espejo
    # corre en un hilo, décimas de diferencia), pero las filas que se
    # reprocesaron desde `espejo_kubera_log` entraron horas después: 60 filas
    # con más de 1 h de desfase, la peor de 17.6 h.
    #
    # No es cosmético. El historial busca el ÚLTIMO evento de cada SKU, así que
    # una fila con la hora equivocada se cuela al frente y el panel muestra un
    # producto como "procesando" cuando terminó bien. Medido: invertía el estado
    # de 50 SKUs.
    fecha_real = {f"mysql:crear_logs:{r['id']}": r["creado"] for r in origen}
    with pg.cursor() as c:
        c.execute("""select detail_ref, created_at from ops.process_log
                      where proceso = 'crear' and detail_ref is not null""")
        actuales = c.fetchall()
    fechas = [(fecha_real[ref], ref) for ref, ts in actuales
              if ref in fecha_real
              and abs((ts.replace(tzinfo=None) - fecha_real[ref]).total_seconds()) > 60]
    print(f"  >>> A CORREGIR la fecha        : {len(fechas)}")

    if not args.real:
        print("\n== DRY-RUN: no se escribió nada ==")
        pg.close()
        return

    with pg.cursor() as c:
        c.execute("select set_config('app.via', 'backfill_crear_logs', true)")
        psycopg2.extras.execute_batch(c, """
            insert into ops.process_log
                (proceso, origen, sku, accion, estado, detalle, detail_ref, created_at)
            values ('crear', 'backfill', %s, %s, %s, %s::jsonb, %s, %s)
        """, plan, page_size=500)
        # coalesce: con `detalle` nulo el `||` de jsonb devuelve NULO y borraría
        # el dato en vez de agregarlo.
        psycopg2.extras.execute_batch(c, """
            update ops.process_log
               set detalle = coalesce(detalle, '{}'::jsonb) || %s::jsonb
             where detail_ref = %s
        """, relleno, page_size=500)
        psycopg2.extras.execute_batch(c, """
            update ops.process_log set created_at = %s where detail_ref = %s
        """, fechas, page_size=500)
    pg.commit()
    print(f"\n== APLICADO: {len(plan)} alta(s) · {len(relleno)} relleno(s) de wc_id "
          f"· {len(fechas)} fecha(s) corregida(s) ==")
    pg.close()


if __name__ == "__main__":
    main()
