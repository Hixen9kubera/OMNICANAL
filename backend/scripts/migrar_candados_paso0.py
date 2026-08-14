"""
migrar_candados_paso0.py — Saca de la bitácora `fanout_log` los tres estados que
nunca debieron vivir ahí (PASO 0, docs/PASO_0_CANDADOS.md).

    accion='full_compensado'      →  channel.orders.stock_compensado_at
    accion IN (_APLICADAS)        →  ops.fulfillment_operations
    resultado ~ '→\\s*(\\d+)'      →  ops.fba_watermark

Es una copia CHICA —23 filas de estado y 99 marcas— y aun así lleva verificación
completa: lo que se está mudando no es un dato, es lo que impide devolver stock
dos veces y mover inventario dos veces.

TRES DECISIONES QUE NO SON OBVIAS
---------------------------------

1. **Las fechas se preservan.** `stock_compensado_at`, `aplicada_at` y
   `visto_at` salen del `ts` de la bitácora, no de `now()`. Ya costó corregir
   21,816 filas de `ops.channel_submissions` por sellar historia con la fecha de
   la copia.

2. **Solo viajan las operaciones que se APLICARON.** En `fanout_log` eso se
   distinguía filtrando `resultado NOT LIKE 'ERROR%'`, un filtro que existe
   porque un 502 del WAF sellaba movimientos para siempre (auditoría 27-jul).
   Aquí ese filtro se aplica UNA VEZ, al migrar: lo que falló no deja fila y
   sigue siendo reintentable, sin que nadie tenga que volver a leer texto.

3. **La marca de agua se copia TAL CUAL**, no se recalcula desde
   `channel.listings.stock_fba`. Medido: 96 de 99 difieren, y no es desfase —
   son cosas distintas. Recalcular sería reintroducir el bug del doble conteo
   que el propio `stock_full.py` documenta.

Idempotente: reejecutar no duplica ni pisa fechas originales.

Uso:
  ...python backend/scripts/migrar_candados_paso0.py --sandbox --real --acepto-destino yvootpbz
  ...python backend/scripts/migrar_candados_paso0.py                       # dry-run vs prod
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_APLICADAS = ("full_ingreso", "full_retiro", "fba_ingreso")


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    p = ROOT / nombre
    if not p.exists():
        return d
    for l in p.read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


def leer_origen(my) -> dict[str, list]:
    """Los tres estados, sacados de la bitácora."""
    with my.cursor() as c:
        c.execute("""SELECT item_id, MIN(ts) ts FROM fanout_log
                      WHERE accion = 'full_compensado' AND item_id IS NOT NULL
                      GROUP BY item_id""")
        comp = c.fetchall()
        ph = ",".join(["%s"] * len(_APLICADAS))
        # Mismo criterio que el candado vivo: un intento con ERROR no cuenta
        # como aplicado (auditoría 27-jul). El filtro se aplica AQUÍ, una vez.
        c.execute(f"""SELECT item_id, MIN(ts) ts,
                             SUBSTRING_INDEX(GROUP_CONCAT(sku), ',', 1) sku,
                             SUBSTRING_INDEX(GROUP_CONCAT(cuenta), ',', 1) cuenta,
                             SUBSTRING_INDEX(GROUP_CONCAT(accion), ',', 1) accion
                        FROM fanout_log
                       WHERE accion IN ({ph}) AND item_id IS NOT NULL
                         AND (resultado IS NULL OR resultado NOT LIKE 'ERROR%%')
                       GROUP BY item_id""", _APLICADAS)
        oper = c.fetchall()
        # La marca de agua: la ÚLTIMA fila fba_% de cada SKU, y el número sale
        # del texto — es justo lo que esta migración viene a eliminar.
        c.execute("""SELECT f.sku, f.cuenta, f.resultado, f.ts FROM fanout_log f
                     JOIN (SELECT sku, MAX(id) mx FROM fanout_log
                            WHERE accion LIKE 'fba_%%' GROUP BY sku) u ON u.mx = f.id""")
        marcas = []
        sin_parsear = []
        for r in c.fetchall():
            m = re.search(r"→\s*(\d+)", str(r["resultado"] or ""))
            (marcas if m else sin_parsear).append(
                {**r, "stock_fba": int(m.group(1)) if m else None})
    return {"comp": comp, "oper": oper, "marcas": marcas, "sin_parsear": sin_parsear}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--acepto-destino", default="")
    ap.add_argument("--sandbox", action="store_true")
    args = ap.parse_args()

    E = cargar(".env")
    dsn = cargar("env.staging")["SUPABASE_DB_URL"] if args.sandbox else E["SUPABASE_DB_URL"]
    ref = (re.search(r"postgres\.([a-z0-9]+):", dsn) or [None, ""])[1]
    if args.real and args.acepto_destino != ref[:8]:
        sys.exit(f"ABORT: con --real hay que nombrar el destino "
                 f"(--acepto-destino {ref[:8]}).")
    print(f"[{'REAL' if args.real else 'DRY-RUN'}] destino: {ref[:8]}…\n", flush=True)

    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    pg = psycopg2.connect(dsn, connect_timeout=25)
    o = leer_origen(my)

    print(f"  compensaciones por pedido : {len(o['comp']):4d}")
    print(f"  operaciones APLICADAS     : {len(o['oper']):4d}")
    print(f"  marcas de agua FBA        : {len(o['marcas']):4d}"
          + (f"  ⚠ {len(o['sin_parsear'])} sin parsear" if o["sin_parsear"] else ""))

    # ¿Los pedidos a compensar existen en kubera? Si no, la marca no tiene dónde ir.
    wc_ids = [int(r["item_id"]) for r in o["comp"] if str(r["item_id"]).isdigit()]
    with pg.cursor() as c:
        c.execute("select wc_order_id from channel.orders where wc_order_id = any(%s)",
                  (wc_ids,))
        existen = {r[0] for r in c.fetchall()}
    huerfanos = [w for w in wc_ids if w not in existen]
    if huerfanos:
        print(f"  ⚠ {len(huerfanos)} pedido(s) compensado(s) NO están en channel.orders: "
              f"{huerfanos[:6]} — su marca no se puede colocar")

    if not args.real:
        print("\n== DRY-RUN: no se escribió nada ==")
        my.close(); pg.close()
        return

    with pg.cursor() as c:
        for r in o["comp"]:
            if str(r["item_id"]).isdigit():
                c.execute(
                    "update channel.orders set stock_compensado_at = "
                    "coalesce(stock_compensado_at, %s) where wc_order_id = %s",
                    (r["ts"], int(r["item_id"])))
        if o["oper"]:
            psycopg2.extras.execute_values(
                c,
                """insert into ops.fulfillment_operations
                     (operacion_id, sku, cuenta, accion, aplicada_at) values %s
                   on conflict (operacion_id) do nothing""",
                [(str(r["item_id"])[:64], r["sku"], r["cuenta"], r["accion"], r["ts"])
                 for r in o["oper"]], template="(%s,%s,%s,%s,%s)")
        if o["marcas"]:
            psycopg2.extras.execute_values(
                c,
                """insert into ops.fba_watermark (sku, stock_fba, cuenta, visto_at)
                   values %s
                   on conflict (sku) do update set
                     stock_fba = excluded.stock_fba,
                     cuenta    = coalesce(excluded.cuenta, fba_watermark.cuenta),
                     visto_at  = excluded.visto_at""",
                [(r["sku"], r["stock_fba"], r["cuenta"], r["ts"]) for r in o["marcas"]],
                template="(%s,%s,%s,%s)")
    pg.commit()

    # ── Verificación ────────────────────────────────────────────────────────
    print("\n── verificación ──")
    todo_ok = True

    def check(etiqueta: str, ok: bool, detalle: str = "") -> None:
        nonlocal todo_ok
        todo_ok &= ok
        print(f"  [{'OK  ' if ok else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))

    with pg.cursor() as c:
        c.execute("select count(*) from channel.orders where stock_compensado_at is not null")
        n_comp = c.fetchone()[0]
        c.execute("select count(*) from ops.fulfillment_operations")
        n_oper = c.fetchone()[0]
        c.execute("select sku::text, stock_fba, visto_at from ops.fba_watermark")
        marcas_kb = {r[0].lower(): (r[1], r[2]) for r in c.fetchall()}

    check("compensaciones colocadas", n_comp >= len(existen),
          f"{n_comp} en kubera vs {len(existen)} colocables de {len(o['comp'])}")
    check("operaciones aplicadas", n_oper >= len(o["oper"]),
          f"{n_oper} de {len(o['oper'])}")

    difs = [(r["sku"], r["stock_fba"], marcas_kb.get(str(r["sku"]).lower(), (None,))[0])
            for r in o["marcas"]
            if marcas_kb.get(str(r["sku"]).lower(), (None,))[0] != r["stock_fba"]]
    check("las marcas de agua viajaron con SU valor", not difs, f"{len(difs)} distintas")
    for d in difs[:4]:
        print(f"        {d[0]}: origen={d[1]} kubera={d[2]}")

    # Que NO se hayan recalculado desde el sync: si coincidieran todas con
    # channel.listings sería señal de que alguien "arregló" la fuente.
    with pg.cursor() as c:
        c.execute("""select count(*) from ops.fba_watermark w
                      join channel.listings l
                        on l.sku = w.sku and l.canal = 'amazon'
                     where l.stock_fba is distinct from w.stock_fba""")
        distintas_del_sync = c.fetchone()[0]
    print(f"  [info] {distintas_del_sync} marcas difieren de channel.listings.stock_fba "
          f"— eso es lo ESPERADO (miden cosas distintas)")

    # Las fechas: ninguna puede ser de hoy si el evento fue de julio.
    with pg.cursor() as c:
        c.execute("""select count(*) from ops.fulfillment_operations
                      where aplicada_at::date > date '2026-08-01'""")
        tarde = c.fetchone()[0]
    check("fechas del evento preservadas (no selladas con la de la copia)",
          tarde == 0, f"{tarde} operaciones con fecha posterior al 1-ago")

    my.close(); pg.close()
    print(f"\nRESULTADO: {'migrado y verificado' if todo_ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if todo_ok else 1)


if __name__ == "__main__":
    main()
