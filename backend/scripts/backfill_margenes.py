"""
backfill_margenes.py — Copia a kubera las tres cachés del tab de Márgenes que
todavía viven en el MySQL que se va a retirar (GRUPO 5 del plan).

    ml_envio_real  → enrich.order_shipping_cost   (13,735 filas)
    ml_ficha       → enrich.listing_weight        (971)
    ml_visitas     → enrich.listing_visits        (1,485)

Idempotente: reejecutar no duplica ni pisa con datos peores (`on conflict do
update` con `coalesce`, igual que el flujo vivo). Al final VERIFICA por su
cuenta —conteos por tabla y una muestra valor por valor— en vez de confiar en
que el insert no tronó: un backfill que solo reporta "listo" no es verificable.

`consultado_at` se preserva del origen A PROPÓSITO. Es lo que decide si hay que
volver a llamar a ML, y ML acepta un ítem por llamada en visitas y en costos de
envío: si se re-sellara con `now()`, el primer barrido después de migrar creería
que todo está fresco y la página serviría datos viejos durante un TTL entero.

Uso:
  ...python backend/scripts/backfill_margenes.py               # dry-run
  ...python backend/scripts/backfill_margenes.py --real --acepto-destino tukwcvsi
  ...python backend/scripts/backfill_margenes.py --real --acepto-destino yvootpbz   # sandbox
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

# (tabla MySQL, tabla kubera, columnas origen, columnas destino, llave)
TABLAS = [
    ("ml_envio_real", "enrich.order_shipping_cost",
     ["cuenta", "external_order_id", "shipment_id", "costo_vendedor", "consultado_at"],
     ["cuenta", "external_order_id", "shipment_id", "costo_vendedor", "consultado_at"],
     ["cuenta", "external_order_id"]),
    ("ml_ficha", "enrich.listing_weight",
     ["listing_id", "cuenta", "titulo", "peso_g", "medido", "consultado_at"],
     ["listing_id", "cuenta", "titulo", "peso_g", "medido", "consultado_at"],
     ["listing_id"]),
    ("ml_visitas", "enrich.listing_visits",
     ["listing_id", "dias", "cuenta", "visitas", "dias_datos", "consultado_at"],
     ["listing_id", "dias", "cuenta", "visitas", "dias_datos", "consultado_at"],
     ["listing_id", "dias"]),
]


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
    ap.add_argument("--real", action="store_true", help="escribe (default: dry-run)")
    ap.add_argument("--acepto-destino", default="",
                    help="primeros 8 chars de la ref destino (obligatorio con --real)")
    ap.add_argument("--sandbox", action="store_true",
                    help="destino = env.staging en vez de .env")
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

    resumen = []
    for t_my, t_kb, cols_o, cols_d, llave in TABLAS:
        with my.cursor() as c:
            c.execute(f"SELECT {', '.join(cols_o)} FROM {t_my}")
            filas = c.fetchall()
        with pg.cursor() as c:
            c.execute(f"select count(*) from {t_kb}")
            antes = c.fetchone()[0]
        print(f"  {t_my:16s} MySQL {len(filas):6,}  →  {t_kb:32s} tenía {antes:6,}")
        resumen.append((t_my, t_kb, len(filas), antes, cols_d, llave))

        if not args.real or not filas:
            continue
        ph = "(" + ", ".join(["%s"] * len(cols_d)) + ")"
        sets = ", ".join(f"{c} = coalesce(excluded.{c}, {t_kb.split('.')[1]}.{c})"
                         for c in cols_d if c not in llave and c != "medido")
        if "medido" in cols_d:      # booleano: false es informativo, no ausencia
            sets += ", medido = excluded.medido"
        sql = (f"insert into {t_kb} ({', '.join(cols_d)}) values %s "
               f"on conflict ({', '.join(llave)}) do update set {sets}")
        # `medido` es TINYINT(1) en MySQL y boolean en Postgres: 0/1 no se
        # convierte solo. Se traduce aquí y no con un cast en el SQL para que
        # el NULL siga siendo NULL ("no sé") y no se vuelva false ("no medido").
        def _valor(fila, col):
            v = fila[col]
            return None if v is None else (bool(v) if col == "medido" else v)

        with pg.cursor() as c:
            psycopg2.extras.execute_values(
                c, sql, [tuple(_valor(f, k) for k in cols_o) for f in filas],
                template=ph, page_size=1000)
        pg.commit()

    if not args.real:
        print("\n== DRY-RUN: no se escribió nada ==")
        my.close(); pg.close()
        return

    # ── VERIFICACIÓN: conteos y muestra valor por valor ─────────────────────
    print("\n── verificación ──")
    todo_ok = True
    for t_my, t_kb, n_my, _antes, cols_d, llave in resumen:
        with pg.cursor() as c:
            c.execute(f"select count(*) from {t_kb}")
            n_kb = c.fetchone()[0]
        ok = n_kb >= n_my
        todo_ok &= ok
        print(f"  [{'OK  ' if ok else 'FALLA'}] {t_kb:32s} kubera {n_kb:6,} vs MySQL {n_my:6,}")

        with my.cursor() as c:
            c.execute(f"SELECT {', '.join(cols_d)} FROM {t_my} "
                      f"ORDER BY {', '.join(llave)} LIMIT 200")
            muestra = c.fetchall()
        difs = 0
        for f in muestra:
            cond = " and ".join(f"{k} = %s" for k in llave)
            with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
                c.execute(f"select {', '.join(cols_d)} from {t_kb} where {cond}",
                          tuple(f[k] for k in llave))
                r = c.fetchone()
            if not r:
                difs += 1
                continue
            for col in cols_d:
                a, b = f[col], r[col]
                if col == "consultado_at":
                    a = a.replace(tzinfo=None) if a else None
                    b = b.replace(tzinfo=None) if b else None
                if a is None and b is None:
                    continue
                if str(a) != str(b) and not (
                        isinstance(a, (int, float)) and float(a or 0) == float(b or 0)):
                    difs += 1
                    break
        todo_ok &= difs == 0
        print(f"  [{'OK  ' if difs == 0 else 'FALLA'}] muestra de {len(muestra)} filas "
              f"valor por valor: {difs} con diferencia")

    my.close(); pg.close()
    print(f"\nRESULTADO: {'copiado y verificado' if todo_ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if todo_ok else 1)


if __name__ == "__main__":
    main()
