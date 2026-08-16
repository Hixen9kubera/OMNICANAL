"""
rescatar_product_type_amazon.py — Los `product_type` de Amazon que solo viven en
la bitácora (BLOQUE 1 del PASO 3, docs/PLAN_31_TABLAS.md).

    amazon_progress.product_type  →  channel.listings.product_type  (canal amazon)

POR QUÉ IMPORTA ESTE CAMPO
--------------------------
La **regla 2 de la casa** define la prioridad al publicar en Amazon:

    meta `amz_product_type` del panel  >  histórico `amazon_progress`  >  detección por título

Este rescate salva el escalón de en medio. Sin él, esos SKUs caen a la detección
por título — que es justo lo que puso una máquina de coser en la categoría
equivocada (caso TEC-1812-NEG). No es un dato decorativo: decide en qué
categoría de Amazon se publica.

SOLO RELLENA, NUNCA PISA
------------------------
`update ... where product_type is null`. Si kubera ya tiene un valor, ese vale
más: lo trajo el sync desde la API de Amazon y por lo tanto es el vigente,
mientras que la bitácora congela el que se usó al publicar. Es el mismo criterio
de arbitraje que se aplicó a los MLM republicados y a las publicaciones cerradas
después: **entre la bitácora y el estado vivo, gana el vivo.**

Idempotente por construcción: la segunda corrida no encuentra nulos que llenar.

Uso:
  ...python backend/scripts/rescatar_product_type_amazon.py               # dry-run
  ...python backend/scripts/rescatar_product_type_amazon.py --real --acepto-destino tukwcvsi
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
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
    with my.cursor() as c:
        c.execute("SELECT sku, product_type FROM amazon_progress "
                  "WHERE product_type IS NOT NULL AND product_type <> ''")
        origen = {str(r["sku"]).lower(): r["product_type"] for r in c.fetchall()}
    my.close()

    pg = psycopg2.connect(dsn, connect_timeout=25)
    with pg.cursor() as c:
        c.execute("select sku::text, product_type from channel.listings "
                  "where canal = 'amazon'")
        kb = {r[0].lower(): r[1] for r in c.fetchall()}

    huecos = {s: t for s, t in origen.items() if s in kb and not kb[s]}
    fuera = [s for s in origen if s not in kb]
    print(f"  amazon_progress con product_type : {len(origen):5d}")
    print(f"  ya lo tienen en kubera           : {sum(1 for s in origen if kb.get(s)):5d}")
    print(f"  HUECOS a rellenar                : {len(huecos):5d}")
    if fuera:
        print(f"  ⚠ {len(fuera)} sin fila en channel.listings — no se pueden colocar")
    if huecos:
        print(f"  tipos: {dict(Counter(huecos.values()).most_common(6))}")

    if not huecos:
        print("\n== Nada que rellenar ==")
        pg.close()
        return
    if not args.real:
        for s, t in list(huecos.items())[:5]:
            print(f"    {s:22s} → {t}")
        print("\n== DRY-RUN: no se escribió nada ==")
        pg.close()
        return

    with pg.cursor() as c:
        psycopg2.extras.execute_values(
            c,
            """update channel.listings l set product_type = v.pt
                 from (values %s) as v(sku, pt)
                where l.canal = 'amazon' and l.sku = v.sku::citext
                  and l.product_type is null""",
            list(huecos.items()), template="(%s,%s)")
        tocadas = c.rowcount
    pg.commit()

    # ── Verificación ────────────────────────────────────────────────────────
    print("\n── verificación ──")
    with pg.cursor() as c:
        c.execute("select sku::text, product_type from channel.listings "
                  "where canal = 'amazon'")
        ahora = {r[0].lower(): r[1] for r in c.fetchall()}
    resto = [s for s in huecos if not ahora.get(s)]
    malos = [(s, origen[s], ahora[s]) for s in huecos
             if ahora.get(s) and ahora[s] != origen[s]]
    # Que NO se haya pisado nada que ya tenía valor: el conteo de los que ya
    # estaban llenos no puede haber cambiado.
    ya_tenian = {s for s in origen if kb.get(s)}
    pisados = [(s, kb[s], ahora[s]) for s in ya_tenian if ahora.get(s) != kb[s]]

    ok = not resto and not malos and not pisados
    print(f"  [{'OK  ' if tocadas == len(huecos) else 'FALLA'}] filas actualizadas: "
          f"{tocadas} de {len(huecos)}")
    print(f"  [{'OK  ' if not resto else 'FALLA'}] huecos que quedan: {len(resto)}")
    print(f"  [{'OK  ' if not malos else 'FALLA'}] el valor copiado coincide con el "
          f"origen: {len(malos)} distintos")
    print(f"  [{'OK  ' if not pisados else 'FALLA'}] NO se pisó ningún valor que ya "
          f"existía: {len(pisados)} pisados")
    for s, a, b in (malos + pisados)[:4]:
        print(f"        {s}: {a} → {b}")

    pg.close()
    print(f"\nRESULTADO: {'rellenado y verificado' if ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
