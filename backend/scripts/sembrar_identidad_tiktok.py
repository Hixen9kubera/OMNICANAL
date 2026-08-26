"""
sembrar_identidad_tiktok.py — Le pone a `ops.tiktok_tokens` la identidad de la
tienda que el espejo nunca le pudo dar.

QUE FALTA, Y POR QUE NO ES UN BUG DEL ESPEJO
--------------------------------------------
kubera tiene el token de TikTok al segundo — se comprobo el 25-ago: los dos
lados con la misma fecha, `2026-08-24 19:23:03`. Lo que NO tiene es
`shop_cipher`, `seller_name` ni `open_id`.

El codigo esta bien. Hay dos caminos que escriben la tienda:

  autorizacion (OAuth)  manda TODO, cipher incluido.
  renovacion            manda solo el token — y a proposito: el cipher no
                        cambia al renovar, y pisarlo con NULL dejaria una
                        conexion con token bueno e inservible (migracion 0024).

La tienda se autorizo en MySQL ANTES de encender `SUPABASE_WRITE_TOKENS`. Desde
entonces solo ha habido renovaciones. La fila de kubera nacio de una renovacion,
y ninguna renovacion futura le va a dar lo que le falta.

POR QUE IMPORTA MAS QUE UN CAMPO VACIO
---------------------------------------
`shop_cipher` FALLA DISFRAZADO. Sin el, un token valido recibe
`shop_cipher is required`, que se lee como un problema de permisos. Se buscaria
en el lugar equivocado durante horas. El dia del corte, TikTok se apaga asi.

QUE HACE
--------
Copia SOLO la identidad (`shop_cipher`, `seller_name`, `open_id`) y SOLO donde
kubera la tiene vacia — `coalesce`, nunca pisa lo que ya haya. NO toca los
tokens: esos ya estan espejados y bien, y reescribirlos arriesga meter un valor
sin cifrar. Verifica CONTRA EL ORIGEN, no contra su propio contador.

Nunca imprime el cipher: solo si esta o no, y su huella.

Uso:
  ...python backend/scripts/sembrar_identidad_tiktok.py                    # dry-run prod
  ...python backend/scripts/sembrar_identidad_tiktok.py --sandbox --real
  ...python backend/scripts/sembrar_identidad_tiktok.py --real --acepto-destino tukwcvsi
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

import psycopg2
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_CAMPOS = ("shop_cipher", "seller_name", "open_id")


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


def huella(v) -> str:
    """Identifica un valor sin revelarlo."""
    if v is None:
        return "(vacio)"
    return hashlib.sha256(str(v).encode()).hexdigest()[:10]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--sandbox", action="store_true")
    ap.add_argument("--acepto-destino", default="")
    args = ap.parse_args()

    E = cargar(".env")
    dsn = (cargar("env.staging")["SUPABASE_DB_URL"] if args.sandbox
           else E["SUPABASE_DB_URL"])
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
        c.execute("SELECT shop_id, shop_cipher, seller_name, open_id FROM tiktok_tokens")
        origen = {str(r["shop_id"]): r for r in c.fetchall()}
    my.close()

    pg = psycopg2.connect(dsn, connect_timeout=25)
    with pg.cursor() as c:
        c.execute("select shop_id, shop_cipher, seller_name, open_id from ops.tiktok_tokens")
        destino = {str(r[0]): dict(zip(("shop_id",) + _CAMPOS, r)) for r in c.fetchall()}

    print(f"  tiendas en MySQL : {len(origen)}")
    print(f"  tiendas en kubera: {len(destino)}\n")

    # ── Que le falta a cada tienda ──────────────────────────────────────────
    pendientes: dict[str, dict[str, str]] = {}
    sin_fila = []
    for shop, o in origen.items():
        d = destino.get(shop)
        if d is None:
            sin_fila.append(shop)
            continue
        falta = {k: o[k] for k in _CAMPOS if o.get(k) is not None and d.get(k) is None}
        print(f"  tienda {shop}")
        for k in _CAMPOS:
            print(f"     {k:12s} MySQL {huella(o.get(k)):12s} · kubera {huella(d.get(k)):12s}"
                  f"{'   <- se copia' if k in falta else ''}")
        if falta:
            pendientes[shop] = falta

    if sin_fila:
        print(f"\n  AVISO: {len(sin_fila)} tienda(s) sin fila en kubera: {sin_fila}")
        print("  Este script NO las crea: crearlas exigiria copiar el token, y el")
        print("  token es trabajo del espejo. Se arregla re-autorizando la tienda.")

    if not pendientes:
        print("\n== no hay nada que sembrar: la identidad ya esta completa ==")
        pg.close()
        sys.exit(0 if not sin_fila else 1)

    print(f"\n  a sembrar: {sum(len(v) for v in pendientes.values())} campo(s) "
          f"en {len(pendientes)} tienda(s)")
    if not args.real:
        print("\n== DRY-RUN: no se escribio nada ==")
        pg.close()
        return

    tocadas = 0
    with pg.cursor() as c:
        for shop, falta in pendientes.items():
            # coalesce: jamas pisa un valor que kubera ya tenga, ni siquiera si
            # entre la lectura de arriba y este update alguien lo lleno.
            sets = ", ".join(f"{k} = coalesce({k}, %s)" for k in falta)
            c.execute(f"update ops.tiktok_tokens set {sets} where shop_id = %s",
                      (*falta.values(), shop))
            tocadas += c.rowcount
    pg.commit()

    # ── Verificacion CONTRA EL ORIGEN ───────────────────────────────────────
    with pg.cursor() as c:
        c.execute("select shop_id, shop_cipher, seller_name, open_id from ops.tiktok_tokens")
        ahora = {str(r[0]): dict(zip(("shop_id",) + _CAMPOS, r)) for r in c.fetchall()}
    malos = [(s, k) for s, o in origen.items() for k in _CAMPOS
             if o.get(k) is not None and s in ahora
             and str(ahora[s].get(k)) != str(o[k])]
    ok = not malos and tocadas > 0
    print("\n── verificacion ──")
    print(f"  [{'OK  ' if tocadas else 'FALLA'}] filas tocadas: {tocadas}")
    print(f"  [{'OK  ' if not malos else 'FALLA'}] la identidad de kubera coincide con "
          f"MySQL campo por campo: {len(malos)} distinto(s)")
    for s, k in malos[:5]:
        print(f"        tienda {s} campo {k}: MySQL {huella(origen[s][k])} · "
              f"kubera {huella(ahora[s].get(k))}")
    pg.close()
    print(f"\nRESULTADO: {'identidad completa' if ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
