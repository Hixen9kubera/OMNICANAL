"""
copiar_almacen_odoo.py — La pasada de la copia del historial de Odoo, a mano.

Copia de Odoo a kubera el historial de movimientos
(`almacen.historial_movimientos`, 0070) de un cedis. Hace EXACTAMENTE lo que
`services/almacen_odoo.py` hace solo cada hora (mismas funciones), pero con
ensayo por omisión y contra el destino que se le diga. Sirve para la carga
inicial, para una base local y para forzar una pasada. Ya no copia la foto de
ubicaciones (`almacen.locations`): desde la 0076 es de la copia de Eduardo.

QUÉ TRAE Y QUÉ NO: ver el encabezado de `services/almacen_odoo.py`. En corto:
todos los movimientos hechos del cedis con su `cedis`, y NINGÚN producto
archivado en Odoo (regla de Brandon, 9-oct-2026).

CANDADOS
  · Sin `--aplicar` no escribe: cada transacción se abre de sólo lectura (por
    transacción, nunca la sesión — regla 13) y sólo se cuenta lo que cambiaría.
  · No corre si el cedis ya es de kubera (`almacenes.fuente <> 'odoo'`).
  · Si el historial llega mucho más chico que lo que ya hay, no toca nada
    (lectura trunca). `--forzar` lo acepta, cuando de verdad encogió.
  · En Odoo sólo `search_read` y `read`.
  · No deja una transacción abierta mientras lee Odoo.

Uso:
  python backend/scripts/copiar_almacen_odoo.py --cedis TEXCO                      # ensayo en el sandbox
  python backend/scripts/copiar_almacen_odoo.py --cedis TEXCO --medir-cuadre       # + Σ delta contra la existencia de Odoo
  python backend/scripts/copiar_almacen_odoo.py --cedis DROP --destino local --aplicar
  python backend/scripts/copiar_almacen_odoo.py --destino prod --delta             # sólo lo escrito desde la última línea vista
"""
from __future__ import annotations

import argparse
import io
import os
import re
import socket
import sys
import threading
from contextlib import contextmanager
from datetime import timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))

WATCHDOG_S = 900


def _env_destino(destino: str) -> str:
    """DSN del destino: `SUPABASE_DB_URL` del proceso o, si no está, env.staging /
    .env de la raíz. Venga de donde venga, se valida el proyecto."""
    archivo = ROOT / ("env.staging" if destino == "sandbox" else ".env")
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn and archivo.exists():
        for line in io.open(archivo, encoding="utf-8"):
            s = line.strip()
            if s.startswith("SUPABASE_DB_URL="):
                dsn = s.partition("=")[2].split("#")[0].strip().strip('"').strip("'")
    if not dsn:
        sys.exit(f"ABORT: no hay SUPABASE_DB_URL para destino={destino} "
                 f"(ni en el entorno ni en {archivo.name}).")
    if destino == "sandbox" and "yvootpbz" not in dsn:
        sys.exit("ABORT: --destino sandbox pero la DSN no es del sandbox.")
    if destino == "prod" and "tukwcvsi" not in dsn:
        sys.exit("ABORT: --destino prod pero la DSN no es de producción.")
    if destino == "local" and not re.search(r"@(127\.0\.0\.1|localhost)[:/]", dsn):
        sys.exit("ABORT: --destino local pero la DSN no es de esta máquina.")
    return dsn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cedis", default="TEX2", choices=["TEX2", "TEXCO", "DROP"])
    ap.add_argument("--aplicar", action="store_true", help="sin esto NO escribe")
    ap.add_argument("--destino", choices=["sandbox", "prod", "local"], default="sandbox")
    ap.add_argument("--delta", action="store_true",
                    help="sólo lo escrito en Odoo desde la última línea vista (por omisión: todo)")
    ap.add_argument("--forzar", action="store_true",
                    help="acepta un historial que encogió")
    ap.add_argument("--medir-cuadre", action="store_true",
                    help="lee también la existencia de Odoo y mide Σ delta = Σ piezas por SKU "
                         "(sólo con el historial completo, o sea sin --delta)")
    args = ap.parse_args()

    perro = threading.Timer(WATCHDOG_S, lambda: os._exit(3))
    perro.daemon = True  # no retiene el proceso si main() termina o revienta
    perro.start()
    socket.setdefaulttimeout(240)

    dsn = _env_destino(args.destino)
    # Después de validar el destino: importar el servicio carga `config`.
    from services import almacen_odoo as ao

    @contextmanager
    def abrir():
        """Una transacción corta por uso, como `supabase_db.get_cursor`: confirma al
        salir si se pidió aplicar; si no, se abre de sólo lectura y se deshace."""
        cx = psycopg2.connect(dsn, connect_timeout=20, cursor_factory=RealDictCursor)
        try:
            with cx.cursor() as cur:
                if not args.aplicar:
                    cur.execute("set transaction read only")
                cur.execute("set local statement_timeout = '120s'")
                cur.execute("set local lock_timeout = '5s'")
                yield cur
            if args.aplicar:
                cx.commit()
            else:
                cx.rollback()
        except BaseException:
            cx.rollback()
            raise
        finally:
            cx.close()

    try:
        r = ao.sincronizar(args.cedis, completo=not args.delta, abrir=abrir,
                           aplicar=args.aplicar, forzar=args.forzar,
                           medir_cuadre=args.medir_cuadre)
    except ao.NoAplica as exc:
        print(f"ABORT ({args.destino}): {exc}.")
        return 2
    except (ValueError, RuntimeError) as exc:
        print(f"ABORT ({args.destino}): {exc}")
        return 2

    h, c = r["historial"], r["cuadre"]
    print(f"Odoo · {r['nombre']} → {args.destino}"
          + ("" if r["completo"]
             else f" · delta desde {r['desde'].astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC"))
    print(f"  HISTORIAL  {h.get('renglones', 0):,} líneas · {h.get('skus', 0):,} SKUs · "
          f"entran {h.get('entran', 0):,} · salen {h.get('salen', 0):,} · "
          f"adentro {h.get('adentro', 0):,} · "
          f"de producto archivado (no se traen) {h.get('archivados_omitidos', 0)} · "
          f"fuera del cedis {h.get('fuera_del_cedis', 0)}")
    print(f"             nuevas {h['nuevos']:,} · cambiadas {h['cambiados']:,} · "
          f"borradas {h['borrados']:,}")
    if c is not None:
        print(f"  CUADRE     {c['skus'] - c['descuadres']:,} de {c['skus']:,} SKUs: el historial "
              f"reproduce la existencia de Odoo"
              + (f" · {c['descuadres']} NO cuadran" if c["descuadres"] else ""))
    print("LISTO." if args.aplicar else "ENSAYO: no se escribió nada (usa --aplicar).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
