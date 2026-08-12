"""Mide TODOS los SKUs que se puedan: siembra + visitas de 30 días, por tandas.

QUÉ ES "SE PUEDA"
-----------------
Un SKU es medible si tiene publicación en Mercado Libre y categoría en
`categorias_ml`. Sin publicación no hay item al que pedirle visitas; sin categoría
no hay mercado contra el que compararlo. Hoy son ~1,581 de 1,917 publicados.

POR QUÉ POR TANDAS
------------------
Las visitas van de a UNA llamada por publicación — no hay multiget:
  • /visits/items?ids=A,B → 400 "maximum amount of items to query is 1"
  • /users/{uid}/items_visits/time_window → 200 pero da el TOTAL de la cuenta e
    ignora `ids`
Con ~3,773 publicaciones eso son miles de llamadas. Las tandas permiten ver avance,
cortar sin perder lo hecho y no abrir de golpe cientos de conexiones.

SIN IA
------
La siembra corre con `con_ia=False`: el término general lo propondría un LLM por
SKU y a esta escala serían ~1,581 llamadas para un dato que una persona corrige
después. Los términos se generan cuando alguien los vaya a usar.

ESCRIBE DIRECTO EN LA BD KUBERA
-------------------------------
Cada categoría se escribe en `enrich.market_*` en cuanto se captura, acotada a su
propia (canal, categoría, nivel) y en una sola transacción. Ya no hay paso de
"subir" ni base local: `competencia_subir.py` se retiró porque su `delete from` +
reinsert completo, apuntado a las tablas nuevas, habría borrado las 15,307 filas
migradas. Requiere `SUPABASE_DB_URL`; sin ella la captura revienta en vez de
escribir en un disco que nadie lee.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from services import competencia_captura, competencia_store, db  # noqa: E402

SQL_MEDIBLES = """
    SELECT DISTINCT c.sku
      FROM ml_progress p
      JOIN categorias_ml c ON c.sku = p.sku
     WHERE p.success = 1
       AND p.ml_item_id IS NOT NULL
       AND c.category_id IS NOT NULL
     ORDER BY c.sku
"""


def medibles() -> list[str]:
    return [r["sku"] for r in db.fetch_all(SQL_MEDIBLES)]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tanda", type=int, default=120,
                    help="SKUs por tanda (default 120)")
    ap.add_argument("--limite", type=int, default=0,
                    help="Corta después de N SKUs; 0 = todos")
    ap.add_argument("--solo-sembrar", action="store_true",
                    help="Siembra y no mide visitas (rápido, para revisar cobertura)")
    args = ap.parse_args()

    todos = medibles()
    if args.limite:
        todos = todos[:args.limite]
    print(f"═══ Competencia · medir todo ═══")
    print(f"medibles: {len(todos)} SKUs (publicados en ML y con categoría)")
    print(f"base    : {competencia_store.RUTA_DB}\n", flush=True)

    t0 = time.time()
    sembrados = visitas_ok = 0
    for i in range(0, len(todos), args.tanda):
        tanda = todos[i:i + args.tanda]
        n = i // args.tanda + 1
        # con_ia=False: el término general no se pide a un LLM a esta escala.
        r = competencia_captura.sembrar_skus(tanda, con_ia=False)
        sembrados += r.get("guardados") or 0
        msg = (f"tanda {n:>3} · {i + len(tanda):>5}/{len(todos)} · "
               f"sembrados={r.get('guardados')}")
        if r.get("sin_categoria"):
            msg += f" · sin_categoria={len(r['sin_categoria'])}"
        if not args.solo_sembrar:
            v = await competencia_captura.refrescar_visitas_propias(tanda)
            visitas_ok += v.get("con_visitas") or 0
            msg += (f" · pubs={v.get('publicaciones')}"
                    f" · visitas={v.get('con_visitas')}"
                    f" · unidades={v.get('fuente_unidades')}")
        print(f"{msg} · {time.time() - t0:.0f}s", flush=True)

    print(f"\nLISTO en {time.time() - t0:.0f}s")
    print(f"  sembrados : {sembrados}")
    print(f"  visitas ok: {visitas_ok}")
    print(f"  vigilados : {len(competencia_store.listar_skus(False))}")
    print(f"  publicaciones en la base: {len(competencia_store.publicaciones())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
