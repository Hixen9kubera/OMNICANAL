"""
vigilar_espejo_tokens.py — ¿De verdad quedó viva la escritura doble de tokens?

LA PREGUNTA, Y POR QUÉ NO SE CONTESTA MIRANDO
----------------------------------------------
`SUPABASE_WRITE_TOKENS=true` está puesta en Railway. Pero "puesta en la
configuración" y "viva en el proceso" son cosas distintas, y esta migración ya
tropezó con esa diferencia: la variable se aplicó a las 16:12 y el contenedor
que la recogió no arrancó hasta las **17:08:35**. Entre medias, a las 17:05:27,
SANCORFASHION renovó su token — en el contenedor VIEJO, sin la bandera. Por eso
`ops.ml_tokens` seguía en cero, y eso NO era un defecto: era un reloj.

Mirar la tabla y ver cero no distingue esos dos mundos:

    a) la bandera no está viva            → hay que arreglar algo
    b) la bandera está viva y nadie renovó → hay que esperar

**El único hecho que los separa es una renovación DESPUÉS del arranque del
contenedor.** Este script espera exactamente eso.

CÓMO DECIDE
-----------
Vigila `ml_tokens.updated_at` en MySQL. Cuando alguna cuenta renueva con fecha
posterior al arranque, mira si esa renovación llegó a `ops.ml_tokens`:

    llegó         → ESPEJO VIVO. La escritura doble funciona de punta a punta.
    no llegó      → ESPEJO MUERTO. La bandera no está en el proceso, o el
                    espejo falla (va en try/except, así que solo deja un
                    warning y no rompe la renovación).

Avisa en LOS DOS casos. Un vigilante que solo habla cuando hay buenas noticias
deja el silencio significando dos cosas a la vez, que es el defecto que este
proyecto lleva toda la migración desarmando.

NUNCA IMPRIME UN TOKEN. Solo fechas y cuentas.

Uso:
  ...python backend/scripts/vigilar_espejo_tokens.py --desde "2026-08-20 17:08:35"
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg2
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
    ap.add_argument("--desde", required=True,
                    help='arranque del contenedor, "YYYY-MM-DD HH:MM:SS" en UTC')
    ap.add_argument("--horas", type=float, default=4.0)
    ap.add_argument("--cada-seg", type=int, default=300)
    args = ap.parse_args()

    corte = datetime.fromisoformat(args.desde)
    E = cargar(".env")
    limite = time.time() + args.horas * 3600

    print(f"vigilando renovaciones posteriores a {corte} UTC "
          f"(hasta {args.horas} h, cada {args.cada_seg}s)", flush=True)

    while time.time() < limite:
        try:
            my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                                 user=E["DB_USER"], password=E["DB_PASSWORD"],
                                 database=E["DB_NAME"], connect_timeout=25,
                                 cursorclass=pymysql.cursors.DictCursor)
            with my.cursor() as c:
                c.execute("SELECT cuenta, updated_at FROM ml_tokens")
                nuevas = [r for r in c.fetchall() if r["updated_at"] > corte]
            my.close()
        except Exception as exc:  # noqa: BLE001
            print(f"  (no se pudo leer MySQL: {exc}) — se reintenta", flush=True)
            time.sleep(args.cada_seg)
            continue

        if nuevas:
            pg = psycopg2.connect(E["SUPABASE_DB_URL"], connect_timeout=25)
            with pg.cursor() as c:
                c.execute("select cuenta, updated_at from ops.ml_tokens")
                espejo = {r[0]: r[1] for r in c.fetchall()}
            pg.close()
            faltan = [r["cuenta"] for r in nuevas if r["cuenta"] not in espejo]
            print()
            for r in nuevas:
                marca = "ESPEJADA" if r["cuenta"] in espejo else "NO espejada"
                print(f"  {r['cuenta']:15s} renovo {r['updated_at']}  -> {marca}")
            if faltan:
                print("\nESPEJO MUERTO: hubo renovacion despues del arranque y NO "
                      "llego a ops.ml_tokens.")
                print(f"  cuentas sin espejar: {faltan}")
                print("  Revisar: la bandera no esta en el proceso, o el espejo "
                      "fallo (deja warning\n           'no se pudo espejar el "
                      "token', no rompe la renovacion).")
                sys.exit(2)
            print("\nESPEJO VIVO: la renovacion llego a kubera. La escritura "
                  "doble funciona de punta a punta.")
            sys.exit(0)

        time.sleep(args.cada_seg)

    print(f"\nSIN NOVEDAD: {args.horas} h sin una sola renovacion posterior al "
          f"arranque.\n  No prueba nada — ni bien ni mal. ML renueva de forma "
          f"reactiva (ante un 401),\n  asi que puede pasar un rato sin que toque. "
          f"Volver a lanzar el vigilante.")
    sys.exit(1)


if __name__ == "__main__":
    main()
