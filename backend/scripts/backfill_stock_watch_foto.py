"""
backfill_stock_watch_foto.py — Copia a kubera la foto del vigilante de
inventario (PASO 2 del plan, docs/PLAN_31_TABLAS.md).

    stock_watch_foto  →  ops.stock_watch_photo   (14,640 filas)

POR QUÉ ESTA COPIA ES DISTINTA A LA DEL PASO 1
----------------------------------------------
Las tres cachés de Márgenes se podían copiar y ya: si una fila salía vieja, a
lo sumo se re-consultaba a ML. Esta foto NO es un dato, es la MEMORIA contra la
que el vigilante calcula `delta = odoo_ahora − odoo_en_la_foto` y con eso
ESCRIBE STOCK EN WOO. Una foto vieja aquí no se "re-consulta": produce un delta
que no existió, y ese delta se aplica.

De ahí las dos protecciones que este script tiene y el del paso 1 no:

1. **Se verifica que la foto de origen esté FRESCA** antes de copiar. Si el
   vigilante lleva más de `--max-atraso-min` sin escribir (default 60, que son
   tres pasadas de 20 min), se aborta: copiar una foto detenida es sembrar el
   error, no migrarlo.

2. **La copia se compara ENTERA al terminar**, fila por fila y columna por
   columna — no una muestra. Son 14 mil filas: cabe en memoria y no hay excusa.

`actualizado` se preserva del origen A PROPÓSITO, igual que el `consultado_at`
del paso 1: es la señal de vida del vigilante. Re-sellarla con `now()` haría
parecer que la foto de kubera se acaba de tomar cuando en realidad es una copia.

Idempotente: reejecutar pisa con el valor de origen (que es lo correcto para una
foto — aquí NO hay `coalesce`, un NULL de origen debe poder pisar un número).

Uso:
  ...python backend/scripts/backfill_stock_watch_foto.py               # dry-run
  ...python backend/scripts/backfill_stock_watch_foto.py --real --acepto-destino tukwcvsi
  ...python backend/scripts/backfill_stock_watch_foto.py --real --acepto-destino yvootpbz --sandbox
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COLS = ["sku", "stock_woo", "stock_odoo", "actualizado"]


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


def _sin_tz(v):
    return v.replace(tzinfo=None) if hasattr(v, "tzinfo") and v is not None else v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="escribe (default: dry-run)")
    ap.add_argument("--acepto-destino", default="",
                    help="primeros 8 chars de la ref destino (obligatorio con --real)")
    ap.add_argument("--sandbox", action="store_true", help="destino = env.staging")
    ap.add_argument("--max-atraso-min", type=int, default=60,
                    help="aborta si la foto de origen lleva más de esto sin refrescarse")
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

    # ── 1) ¿La foto de origen está viva? ────────────────────────────────────
    with my.cursor() as c:
        c.execute("SELECT COUNT(*) n, MAX(actualizado) ult FROM stock_watch_foto")
        f = c.fetchone()
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    atraso = (ahora - f["ult"]) if f["ult"] else timedelta(days=999)
    print(f"  origen: {f['n']:,} filas · última escritura {f['ult']} UTC "
          f"(hace {atraso.total_seconds() / 60:.0f} min)")
    if atraso > timedelta(minutes=args.max_atraso_min):
        sys.exit(f"\nABORT: la foto de origen lleva {atraso.total_seconds() / 60:.0f} min "
                 f"sin refrescarse (tope {args.max_atraso_min}). El vigilante está "
                 f"detenido o apagado: copiar una foto congelada siembra deltas falsos. "
                 f"Revisar STOCK_WATCH_ENABLED antes de insistir.")

    with my.cursor() as c:
        c.execute(f"SELECT {', '.join(COLS)} FROM stock_watch_foto")
        filas = c.fetchall()
    with pg.cursor() as c:
        c.execute("select count(*) from ops.stock_watch_photo")
        antes = c.fetchone()[0]
    print(f"  destino: tenía {antes:,} filas · se van a escribir {len(filas):,}\n")

    if not args.real:
        print("== DRY-RUN: no se escribió nada ==")
        my.close(); pg.close()
        return

    # ── 2) Copiar ───────────────────────────────────────────────────────────
    # SIN `coalesce`: en una foto el NULL es informativo ("Woo no gestiona
    # stock de este SKU") y tiene que poder pisar a un número anterior.
    with pg.cursor() as c:
        psycopg2.extras.execute_values(
            c,
            """insert into ops.stock_watch_photo
                 (sku, stock_woo, stock_odoo, actualizado) values %s
               on conflict (sku) do update set
                 stock_woo   = excluded.stock_woo,
                 stock_odoo  = excluded.stock_odoo,
                 actualizado = excluded.actualizado""",
            [tuple(r[k] for k in COLS) for r in filas],
            template="(%s, %s, %s, %s)", page_size=1000)
    pg.commit()

    # ── 3) Verificación COMPLETA, no muestra ────────────────────────────────
    print("── verificación (todas las filas, todas las columnas) ──")
    A = {str(r["sku"]).lower(): tuple(_sin_tz(r[k]) for k in COLS[1:]) for r in filas}
    with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute("select sku::text as sku, stock_woo, stock_odoo, actualizado "
                  "from ops.stock_watch_photo")
        B = {str(r["sku"]).lower(): tuple(_sin_tz(r[k]) for k in COLS[1:])
             for r in c.fetchall()}

    solo_my, solo_kb = set(A) - set(B), set(B) - set(A)
    difs = [k for k in set(A) & set(B) if A[k] != B[k]]
    ok = not solo_my and not solo_kb and not difs
    print(f"  [{'OK  ' if not solo_my else 'FALLA'}] SKUs solo en MySQL : {len(solo_my)}")
    print(f"  [{'OK  ' if not solo_kb else 'FALLA'}] SKUs solo en kubera: {len(solo_kb)}")
    print(f"  [{'OK  ' if not difs else 'FALLA'}] SKUs con algún valor distinto: {len(difs)}")
    for k in (difs[:5] or []):
        print(f"        {k}: mysql={A[k]} kubera={B[k]}")
    for k in (list(solo_my)[:5] or []):
        print(f"        solo MySQL: {k}")
    if ok:
        print(f"  ✓ {len(A):,} filas × {len(COLS)} columnas = {len(A) * len(COLS):,} "
              f"celdas idénticas")

    my.close(); pg.close()
    print(f"\nRESULTADO: {'copiado y verificado' if ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
