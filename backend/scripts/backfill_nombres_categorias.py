"""
backfill_nombres_categorias.py — Rellena `channel.categories.name`/`path` de las
categorías que el panel eligió y que nunca se resolvieron contra ML (paso 0 del
desmantelamiento, 12-ago-2026).

POR QUÉ. `crear_producto._categoria_curada` dejó de leer la tabla congelada
`categorias_ml` y ahora lee el mapa de kubera. Ese mapa es mejor —13,733 SKUs
contra 12,399, y la elección del PANEL en vez de la del predictor— pero 75
category_id en uso tienen la fila creada y el `name` en NULL. Sin nombre,
`get_or_create_wc_categoria` devuelve None y el producto se publica sin
categoría de WooCommerce. El MySQL congelado NO puede taparlo: de esas 75 llena
CERO (son elecciones hechas después de que dejó de escribirse).

El nombre se pide a la API PÚBLICA de ML (`/categories/{id}`, sin token) y solo
se escribe sobre NULL — nunca pisa un nombre existente.

Uso:
  ...python backend/scripts/backfill_nombres_categorias.py               # dry-run
  ...python backend/scripts/backfill_nombres_categorias.py --real --acepto-destino tukwcvsi
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import httpx
import psycopg2
import psycopg2.extras

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_API = "https://api.mercadolibre.com"


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for l in (ROOT / nombre).read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--acepto-destino", default="")
    args = ap.parse_args()

    E = cargar(".env")
    ref = (re.search(r"postgres\.([a-z0-9]+):", E["SUPABASE_DB_URL"]) or [None, ""])[1]
    if args.real and args.acepto_destino != ref[:8]:
        sys.exit(f"ABORT: con --real hay que nombrar el destino "
                 f"(--acepto-destino {ref[:8]}).")
    print(f"[{'REAL' if args.real else 'DRY-RUN'}] destino: {ref[:8]}…\n", flush=True)

    pg = psycopg2.connect(E["SUPABASE_DB_URL"], connect_timeout=25)
    with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute("""select distinct pc.category_id, count(*) over
                            (partition by pc.category_id) skus
                       from channel.product_category pc
                       left join channel.categories ct
                              on ct.category_id = pc.category_id
                             and ct.channel_id = pc.channel_id
                      where pc.channel_id = 'mercado_libre' and ct.name is null""")
        huecos = [(r["category_id"], r["skus"]) for r in c.fetchall()]

    print(f"  category_id en uso SIN nombre: {len(huecos)}"
          f"  ({sum(n for _, n in huecos)} SKUs afectados)\n", flush=True)
    if not huecos:
        print("== nada que hacer ==")
        pg.close()
        return

    resueltos: list[tuple[str, str, str]] = []
    fallidos: list[tuple[str, int]] = []
    with httpx.Client(base_url=_API, timeout=20.0) as cli:
        for cat_id, n_skus in huecos:
            try:
                r = cli.get(f"/categories/{cat_id}")
                if r.status_code != 200:
                    fallidos.append((cat_id, r.status_code))
                    continue
                d = r.json()
                camino = " › ".join(p.get("name", "") for p in (d.get("path_from_root") or []))
                resueltos.append((cat_id, str(d.get("name") or ""), camino))
            except Exception as exc:  # noqa: BLE001
                fallidos.append((cat_id, -1))
                print(f"    {cat_id}: {exc}", flush=True)
            time.sleep(0.15)  # la API pública no necesita castigo

    print(f"  resueltos por la API de ML: {len(resueltos)}   sin resolver: {len(fallidos)}")
    for cat_id, nombre, camino in resueltos[:10]:
        print(f"    {cat_id:12s} {nombre}   ({camino[:60]})")
    if fallidos:
        print(f"  sin resolver (siguen sin nombre): {fallidos[:8]}")

    if not args.real:
        print("\n== DRY-RUN: no se escribió nada ==")
        pg.close()
        return

    with pg.cursor() as c:
        c.execute("select set_config('app.via', 'backfill_nombres_cat', true)")
        for cat_id, nombre, camino in resueltos:
            if not nombre:
                continue
            c.execute("""update channel.categories
                            set name = coalesce(name, %s),
                                path = coalesce(path, nullif(%s, ''))
                          where category_id = %s and channel_id = 'mercado_libre'""",
                      (nombre, camino, cat_id))
    pg.commit()
    print(f"\n== APLICADO: {len([r for r in resueltos if r[1]])} nombre(s) ==")
    pg.close()


if __name__ == "__main__":
    main()
