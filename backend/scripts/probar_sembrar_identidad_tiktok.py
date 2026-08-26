"""
probar_sembrar_identidad_tiktok.py — Prueba en SANDBOX del sembrado de identidad.

QUE SE PRUEBA, Y POR QUE ESTAS TRES COSAS
------------------------------------------
  1. Rellena lo vacio      — el caso real: la fila nacio de una renovacion y le
                             falta `shop_cipher`.
  2. NO pisa lo que hay    — el `coalesce` es la unica proteccion contra que una
                             corrida distraida borre una identidad buena.
  3. Es idempotente        — correrlo dos veces no cambia nada la segunda. Un
                             sembrado que "funciona" pero no se puede repetir no
                             sirve el dia que haya que repetirlo.

El caso 2 se arma A PROPOSITO con un `seller_name` EQUIVOCADO: si el script lo
respeta, respeta cualquier cosa. Si lo corrige, el `coalesce` no esta haciendo
nada y el mismo bug puede pisar un cipher bueno.

Deja el sandbox como lo encontro.

Uso:
  ...python backend/scripts/probar_sembrar_identidad_tiktok.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import psycopg2
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_TOKEN_FALSO = "gAAAAA-PRUEBA-NO-ES-UN-TOKEN-REAL"
_SELLER_MALO = "NOMBRE-EQUIVOCADO-A-PROPOSITO"
_ok = True


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for l in (ROOT / nombre).read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


def sembrar(cur, shop: str, seller=None) -> None:
    cur.execute("""insert into ops.tiktok_tokens
                     (shop_id, seller_name, open_id, shop_cipher,
                      access_token, refresh_token, updated_at)
                   values (%s,%s,null,null,%s,%s, now())
                   on conflict (shop_id) do update set
                     seller_name=excluded.seller_name, open_id=null,
                     shop_cipher=null, access_token=excluded.access_token""",
                (shop, seller, _TOKEN_FALSO, _TOKEN_FALSO))


def leer(cur, shop: str):
    cur.execute("""select shop_cipher, seller_name, open_id
                     from ops.tiktok_tokens where shop_id=%s""", (shop,))
    return cur.fetchone()


def correr() -> int:
    return subprocess.run(
        [sys.executable, str(ROOT / "backend/scripts/sembrar_identidad_tiktok.py"),
         "--sandbox", "--real"],
        capture_output=True, text=True).returncode


def main() -> None:
    E, S = cargar(".env"), cargar("env.staging")
    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    with my.cursor() as c:
        c.execute("SELECT shop_id, shop_cipher, seller_name, open_id "
                  "FROM tiktok_tokens LIMIT 1")
        origen = c.fetchone()
    my.close()
    if not origen:
        sys.exit("ABORT: MySQL no tiene ninguna tienda de TikTok que copiar.")
    shop = str(origen["shop_id"])
    print(f"PRUEBA EN SANDBOX — tienda {shop}\n")

    pg = psycopg2.connect(S["SUPABASE_DB_URL"], connect_timeout=25)
    pg.autocommit = True
    habia = None
    try:
        with pg.cursor() as c:
            c.execute("select count(*) from ops.tiktok_tokens where shop_id=%s", (shop,))
            habia = c.fetchone()[0]
            if habia:
                # La prueba pisa el token de la fila con uno falso. Si el sandbox
                # ya tiene una tienda de verdad, abortar es lo correcto: dejarla
                # con un token inventado seria peor que no probar.
                sys.exit(f"ABORT: el sandbox ya tiene la tienda {shop}. Esta prueba "
                         f"reescribe su token; borrala a mano si quieres correrla.")

            # ── 1. rellena lo vacio ─────────────────────────────────────────
            print("── 1. rellena lo que esta vacio ──")
            sembrar(c, shop)
            antes = leer(c, shop)
            rc = correr()
            despues = leer(c, shop)
            check("la fila empezo sin identidad", all(v is None for v in antes),
                  f"cipher/seller/open = {antes}")
            check("el script termino bien", rc == 0, f"codigo de salida {rc}")
            check("shop_cipher quedo igual que en MySQL",
                  despues[0] is not None and str(despues[0]) == str(origen["shop_cipher"]))
            check("seller_name quedo igual que en MySQL",
                  str(despues[1]) == str(origen["seller_name"]),
                  f"kubera={despues[1]!r} MySQL={origen['seller_name']!r}")
            check("open_id quedo igual que en MySQL",
                  str(despues[2]) == str(origen["open_id"]))

            # ── 2. no pisa lo que ya hay ────────────────────────────────────
            print("\n── 2. NO pisa un valor que ya estaba (aunque sea malo) ──")
            sembrar(c, shop, seller=_SELLER_MALO)
            correr()
            d2 = leer(c, shop)
            check("respeto el seller_name equivocado en vez de corregirlo",
                  d2[1] == _SELLER_MALO,
                  f"quedo {d2[1]!r}; si dice {origen['seller_name']!r} el coalesce no protege")
            check("y aun asi lleno el cipher, que si estaba vacio",
                  d2[0] is not None and str(d2[0]) == str(origen["shop_cipher"]))

            # ── 3. idempotente ──────────────────────────────────────────────
            print("\n── 3. correrlo de nuevo no cambia nada ──")
            sembrar(c, shop)
            correr()
            uno = leer(c, shop)
            rc2 = correr()
            dos = leer(c, shop)
            check("la segunda corrida dejo la fila identica", uno == dos)
            check("y avisa que no habia nada que sembrar (salida 0)", rc2 == 0,
                  f"codigo de salida {rc2}")
    finally:
        with pg.cursor() as c:
            if habia:
                # La fila era del sandbox antes de la prueba: se deja vacia de
                # identidad, que es como estaba.
                sembrar(c, shop)
            else:
                c.execute("delete from ops.tiktok_tokens where shop_id=%s", (shop,))
        pg.close()
        print("\n(sandbox devuelto a como estaba)")

    print(f"\nRESULTADO: {'todo en verde' if _ok else 'HAY FALLAS'}")
    sys.exit(0 if _ok else 1)


if __name__ == "__main__":
    main()
