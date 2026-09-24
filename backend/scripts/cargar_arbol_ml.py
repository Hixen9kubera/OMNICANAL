"""
cargar_arbol_ml.py — llena `channel.ml_category_tree` (migración 0059) con el
árbol COMPLETO de categorías de Mercado Libre México.

DE DÓNDE SALE
`GET /sites/MLM/categories/all`: una sola llamada, pública, que devuelve las
~12,263 categorías con su ruta, si aceptan publicaciones y cuántas tienen.
Viene comprimida (~1.5 MB, ~2 s). Medido el 24-sep-2026.

CÓMO ESCRIBE
En UNA transacción: upsert de todo lo que llegó y borrado de lo que ML ya no
trae (categorías retiradas). La tabla es un espejo del árbol de ML: no guarda
nada nuestro, así que borrar lo retirado no pierde información.

LOS DOS CANDADOS
  · ML contestó menos de MINIMO categorías → ABORT sin tocar nada. Una
    respuesta truncada no debe vaciar el buscador.
  · Lo que llegó es CAIDA_MAX menos que lo que ya hay → ABORT. Un árbol que
    encoge 10% de un día para otro es un error de lectura, no una poda de ML.

Sin `--aplicar` es un ENSAYO: baja el árbol, lo compara contra el destino en
una transacción de solo lectura (`set transaction read only`, nunca
set_session: envenena el pooler) y dice qué haría.

Uso (desde backend/):
    python -m scripts.cargar_arbol_ml                           # ensayo, sandbox
    python -m scripts.cargar_arbol_ml --aplicar --destino sandbox
    python -m scripts.cargar_arbol_ml --aplicar --destino prod

La DSN sale de SUPABASE_DB_URL del entorno si existe (cron de Railway) o de
env.staging / .env en la raíz (a mano). En los dos casos se valida que sea el
proyecto del --destino pedido.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402
import psycopg2.errors  # noqa: E402
import psycopg2.extras  # noqa: E402

from services.categorias_arbol import filas_desde_arbol  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
URL = "https://api.mercadolibre.com/sites/MLM/categories/all"
MINIMO = 10_000      # el árbol medido el 24-sep-2026 tiene 12,263
CAIDA_MAX = 0.10
WATCHDOG_S = 300     # anti-cuelgue: nada de esto debería pasar de un minuto

_COLUMNAS = ("category_id, name, parent_id, root_id, path_ids, path_names, is_leaf, "
             "listing_allowed, total_items, catalog_domain, name_norm, path_norm")
_UPSERT = f"""
insert into channel.ml_category_tree ({_COLUMNAS}, leido_at) values %s
on conflict (category_id) do update set
  name = excluded.name, parent_id = excluded.parent_id, root_id = excluded.root_id,
  path_ids = excluded.path_ids, path_names = excluded.path_names,
  is_leaf = excluded.is_leaf, listing_allowed = excluded.listing_allowed,
  total_items = excluded.total_items, catalog_domain = excluded.catalog_domain,
  name_norm = excluded.name_norm, path_norm = excluded.path_norm,
  leido_at = excluded.leido_at
"""
_PLANTILLA = "(%s,%s,%s,%s,%s::text[],%s::text[],%s,%s,%s,%s,%s,%s,now())"


def _env_destino(destino: str) -> str:
    """DSN del destino: la variable SUPABASE_DB_URL del proceso (así corre como
    cron de Railway, donde no hay archivo) o, si no está, env.staging / .env de
    la raíz (así corre a mano). Venga de donde venga, se valida el proyecto."""
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
    return dsn


def bajar() -> dict:
    req = urllib.request.Request(URL, headers={"Accept-Encoding": "gzip",
                                               "User-Agent": "omnicanal-arbol-ml/1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        crudo = r.read()
    # ML manda gzip aunque no se pida; se detecta por los bytes, no por la cabecera.
    datos = json.loads(gzip.decompress(crudo) if crudo[:2] == b"\x1f\x8b" else crudo)
    if not isinstance(datos, dict):
        sys.exit(f"ABORT: ML devolvió {type(datos).__name__}, se esperaba un objeto por ID.")
    return datos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="sin esto NO escribe")
    ap.add_argument("--destino", choices=["sandbox", "prod"], default="sandbox")
    args = ap.parse_args()

    perro = threading.Timer(WATCHDOG_S, lambda: os._exit(3))
    perro.daemon = True  # no retiene el proceso si main() termina o revienta
    perro.start()
    socket.setdefaulttimeout(150)

    t0 = time.time()
    filas, stats = filas_desde_arbol(bajar())
    hojas = sum(1 for f in filas if f[6])
    publicables = sum(1 for f in filas if f[7])
    print(f"ML: {len(filas):,} categorías en {time.time() - t0:.1f}s · "
          f"{hojas:,} hojas · {publicables:,} aceptan publicar · "
          f"rutas corregidas: {stats['ruta_corregida']}")
    if len(filas) < MINIMO:
        print(f"ABORT: menos de {MINIMO:,} categorías; parece una respuesta truncada.")
        return 2

    dsn = _env_destino(args.destino)
    ids = [f[0] for f in filas]
    cx = psycopg2.connect(dsn, connect_timeout=20)
    try:
        with cx.cursor() as cur:
            if not args.aplicar:
                cur.execute("set transaction read only")
            cur.execute("set local statement_timeout = '120s'")
            try:
                cur.execute("select count(*) from channel.ml_category_tree")
            except psycopg2.errors.UndefinedTable:
                print(f"ABORT: {args.destino} no tiene channel.ml_category_tree "
                      "(falta aplicar la migración 0059).")
                return 2
            actual = cur.fetchone()[0]
            cur.execute("select count(*) from channel.ml_category_tree "
                        "where category_id <> all(%s)", (ids,))
            retiradas = cur.fetchone()[0]
            print(f"destino={args.destino}: hoy {actual:,} filas · llegan {len(filas):,} · "
                  f"se borrarían {retiradas:,} que ML ya no trae")
            if actual and len(filas) < actual * (1 - CAIDA_MAX):
                print(f"ABORT: el árbol encogió más de {CAIDA_MAX:.0%} "
                      f"({actual:,} → {len(filas):,}); no se toca nada.")
                cx.rollback()
                return 2
            if not args.aplicar:
                print("ENSAYO: no se escribió nada (usa --aplicar).")
                cx.rollback()
                return 0
            psycopg2.extras.execute_values(cur, _UPSERT, filas, template=_PLANTILLA,
                                           page_size=1000)
            cur.execute("delete from channel.ml_category_tree "
                        "where category_id <> all(%s)", (ids,))
            borradas = cur.rowcount
            cur.execute("select count(*), count(*) filter (where listing_allowed) "
                        "from channel.ml_category_tree")
            total, pub = cur.fetchone()
            if total != len(filas):
                cx.rollback()
                print(f"ABORT: quedaron {total:,} filas y llegaron {len(filas):,}; "
                      "se deshizo la transacción.")
                return 2
        cx.commit()
        print(f"OK: {total:,} categorías ({pub:,} publicables) · {borradas:,} retiradas "
              f"borradas · {time.time() - t0:.1f}s")
        return 0
    finally:
        cx.close()


if __name__ == "__main__":
    sys.exit(main())
