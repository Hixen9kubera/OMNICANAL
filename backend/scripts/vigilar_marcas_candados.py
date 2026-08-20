"""
vigilar_marcas_candados.py — ¿De verdad quedó viva la escritura de los candados?

LA PREGUNTA, Y POR QUÉ NO BASTA MIRAR
--------------------------------------
`SUPABASE_WRITE_CANDADOS=true` está puesta y el contenedor arrancó con ella. Pero
"puesta" y "viva" no son lo mismo, y aquí la diferencia mueve mercancía.

Comparar los dos lados AHORA no sirve: acaban de resincronizarse, así que
coinciden aunque la escritura esté muerta. **Lo único que separa "vivo" de
"empatados por la resincronización" es que algo SE MUEVA.**

QUÉ ESPERA, Y QUÉ CONCLUYE
--------------------------
Vigila la marca de agua del FBA, que es la que se mueve seguido (la avanza el
vigilante cada pasada). Cuando MySQL cambie alguna:

    kubera cambió igual   → ESCRITURA VIVA. Se puede pensar en encender la
                            lectura tras unos días en verde.
    kubera NO cambió      → ESCRITURA MUERTA. La bandera no esta en el proceso,
                            y encender la lectura moveria stock con una marca
                            atrasada — el defecto de los 10 SKUs, otra vez.

Avisa en LOS DOS casos, y también si pasa la ventana **sin que nada se mueva** —
porque eso no prueba nada y decirlo importa tanto como lo demás.

SOLO LECTURA.

Uso:
  ...python backend/scripts/vigilar_marcas_candados.py --horas 3
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import psycopg2
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cargar() -> dict[str, str]:
    d: dict[str, str] = {}
    for l in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


def marcas_mysql(E) -> dict[str, int]:
    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    try:
        with my.cursor() as c:
            c.execute("""SELECT f.sku, f.resultado FROM fanout_log f
                          JOIN (SELECT sku, MAX(id) mx FROM fanout_log
                                 WHERE accion LIKE 'fba_%%' GROUP BY sku) u
                            ON u.mx = f.id""")
            out = {}
            for r in c.fetchall():
                m = re.search(r"→\s*(\d+)", str(r["resultado"] or ""))
                if m:
                    out[str(r["sku"])] = int(m.group(1))
            return out
    finally:
        my.close()


def marcas_kubera(E) -> dict[str, int]:
    pg = psycopg2.connect(E["SUPABASE_DB_URL"], connect_timeout=25)
    try:
        with pg.cursor() as c:
            c.execute("select sku::text, stock_fba from ops.fba_watermark")
            return {r[0]: int(r[1]) for r in c.fetchall()}
    finally:
        pg.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horas", type=float, default=3.0)
    ap.add_argument("--cada-seg", type=int, default=300)
    args = ap.parse_args()

    E = cargar()
    base_my, base_kb = marcas_mysql(E), marcas_kubera(E)
    print(f"linea base: {len(base_my)} marcas en MySQL, {len(base_kb)} en kubera. "
          f"Vigilando {args.horas} h.", flush=True)
    limite = time.time() + args.horas * 3600

    while time.time() < limite:
        time.sleep(args.cada_seg)
        try:
            my, kb = marcas_mysql(E), marcas_kubera(E)
        except Exception as exc:  # noqa: BLE001
            print(f"  (no se pudo medir: {exc}) — se reintenta", flush=True)
            continue

        movidas = {s: (base_my[s], v) for s, v in my.items()
                   if s in base_my and base_my[s] != v}
        nuevas = {s: v for s, v in my.items() if s not in base_my}
        if not movidas and not nuevas:
            continue

        print(f"\n  MySQL movio {len(movidas)} marca(s) y creo {len(nuevas)}",
              flush=True)
        siguio, no_siguio = [], []
        for s, (antes, ahora) in {**movidas,
                                  **{k: (None, v) for k, v in nuevas.items()}}.items():
            if kb.get(s) == ahora:
                siguio.append(s)
            else:
                no_siguio.append((s, antes, ahora, kb.get(s)))
        for s in siguio[:5]:
            print(f"     {s:24s} -> kubera la siguió")
        for s, a, b, k in no_siguio[:5]:
            print(f"     {s:24s} MySQL {a}->{b} · kubera se quedó en {k}")

        if no_siguio:
            print("\nESCRITURA MUERTA: MySQL movio la marca y kubera no la siguió.")
            print("  NO encender la lectura: el vigilante calcularia el ingreso")
            print("  contra una marca atrasada y descontaria de Woo piezas que no")
            print("  entraron — el defecto de los 10 SKUs, otra vez.")
            sys.exit(2)
        print("\nESCRITURA VIVA: kubera siguió todas las marcas que se movieron.")
        print("  Falta dejarla correr unos dias antes de mover la lectura.")
        sys.exit(0)

    print(f"\nSIN NOVEDAD: {args.horas} h y NINGUNA marca se movio.")
    print("  No prueba nada — ni bien ni mal. El vigilante del FBA solo avanza la")
    print("  marca cuando Amazon reporta un ingreso; puede pasar un rato sin que")
    print("  toque. Volver a lanzarlo.")
    sys.exit(1)


if __name__ == "__main__":
    main()
