"""
resincronizar_marcas_fba.py — Pone al día `ops.fba_watermark` con lo que MySQL
tiene HOY.

POR QUÉ SE DESFASARON
---------------------
Las 99 marcas se copiaron el 12-ago con `migrar_candados_paso0.py`. Desde
entonces el vigilante del FBA siguió avanzando la de MySQL —que vive dentro del
TEXTO de `fanout_log.resultado`— pero **no la de kubera**, porque la escritura
estaba detrás de la bandera de LECTURA. Sin encenderla no se escribía; y no se
podía encender sin que estuviera al día.

Se vio midiendo, no razonando: al comparar los tres candados antes de encender,
**10 SKUs tenían un número distinto**. `TEC-2353-MUL`: MySQL 40, kubera 20.

POR QUÉ ESE NÚMERO IMPORTA
--------------------------
No es un "sí/no" como los otros dos candados: es la referencia contra la que se
calcula `subio = fba_ahora - marca`. Con la marca atrasada, el vigilante ve un
ingreso que no ocurrió y **le descuenta a Woo piezas que nunca entraron**.

MySQL manda aquí, y no al revés: su marca es la que el vigilante viene usando de
verdad. La de kubera es una copia que se quedó vieja.

Uso:
  ...python backend/scripts/resincronizar_marcas_fba.py                # dry-run
  ...python backend/scripts/resincronizar_marcas_fba.py --sandbox --real
  ...python backend/scripts/resincronizar_marcas_fba.py --real --acepto-destino tukwcvsi
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--sandbox", action="store_true")
    ap.add_argument("--acepto-destino", default="")
    args = ap.parse_args()

    E = cargar(".env")
    dsn = cargar("env.staging")["SUPABASE_DB_URL"] if args.sandbox else E["SUPABASE_DB_URL"]
    ref = (re.search(r"postgres\.([a-z0-9]+):", dsn) or [None, ""])[1]
    if args.real and not args.sandbox and args.acepto_destino != ref[:8]:
        sys.exit(f"ABORT: con --real contra produccion hay que nombrar el destino "
                 f"(--acepto-destino {ref[:8]}).")
    print(f"[{'REAL' if args.real else 'DRY-RUN'}] destino: {ref[:8]}…\n")

    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    with my.cursor() as c:
        c.execute("""SELECT f.sku, f.resultado FROM fanout_log f
                      JOIN (SELECT sku, MAX(id) mx FROM fanout_log
                             WHERE accion LIKE 'fba_%%' GROUP BY sku) u ON u.mx = f.id""")
        origen = {}
        for r in c.fetchall():
            m = re.search(r"→\s*(\d+)", str(r["resultado"] or ""))
            if m:
                origen[str(r["sku"])] = int(m.group(1))
    my.close()

    pg = psycopg2.connect(dsn, connect_timeout=25)
    with pg.cursor() as c:
        c.execute("select sku::text, stock_fba from ops.fba_watermark")
        destino = {r[0]: int(r[1]) for r in c.fetchall()}

    distintos = {s: (destino[s], v) for s, v in origen.items()
                 if s in destino and destino[s] != v}
    faltan = {s: v for s, v in origen.items() if s not in destino}
    print(f"  marcas en MySQL : {len(origen):5d}")
    print(f"  marcas en kubera: {len(destino):5d}")
    print(f"  con numero DISTINTO: {len(distintos):5d}")
    print(f"  que faltan en kubera: {len(faltan):5d}")
    for s, (k, m) in list(distintos.items())[:6]:
        print(f"     {s:24s} kubera {k:5d}  ->  MySQL {m:5d}")

    if not distintos and not faltan:
        print("\n== ya estaban al dia ==")
        pg.close()
        return
    if not args.real:
        print("\n== DRY-RUN: no se escribio nada ==")
        pg.close()
        return

    with pg.cursor() as c:
        psycopg2.extras.execute_values(
            c,
            """insert into ops.fba_watermark (sku, stock_fba, cuenta, visto_at)
               values %s
               on conflict (sku) do update set
                 stock_fba = excluded.stock_fba,
                 visto_at  = excluded.visto_at""",
            [(s, v, "AMAZON") for s, v in origen.items()],
            template="(%s,%s,%s, now())")
        tocadas = c.rowcount
    pg.commit()

    # ── Verificacion CONTRA EL ORIGEN ───────────────────────────────────────
    with pg.cursor() as c:
        c.execute("select sku::text, stock_fba from ops.fba_watermark")
        ahora = {r[0]: int(r[1]) for r in c.fetchall()}
    malos = [(s, v, ahora.get(s)) for s, v in origen.items() if ahora.get(s) != v]
    ok = not malos
    print(f"\n── verificacion ──")
    print(f"  [{'OK  ' if tocadas else 'FALLA'}] filas tocadas: {tocadas}")
    print(f"  [{'OK  ' if ok else 'FALLA'}] TODAS las marcas de MySQL coinciden ahora "
          f"en kubera: {len(malos)} distintas")
    for s, m, k in malos[:5]:
        print(f"        {s}: MySQL {m} · kubera {k}")
    pg.close()
    print(f"\nRESULTADO: {'marcas al dia' if ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
