"""Corre las DOS búsquedas de cada SKU vigilado y guarda el resultado.

QUÉ MIDE
--------
Por cada SKU activo con categoría:
  • TÉRMINO GENERAL → con quién compites por DESCUBRIMIENTO ("lona para exterior")
  • TÍTULO COMPLETO → tu competencia DIRECTA, el mismo producto

Ambas raspando el buscador: `GET /sites/MLM/search` responde 403 y no hay posición
orgánica por API. Los anuncios se descartan (ver competencia_busqueda).

POR QUÉ UNA SOLA CORRIDA
------------------------
El navegador NO puede ser headless —ML detecta `--headless=new` y sirve un 404 a
todo—, así que esto abre una ventana de Chrome durante toda la corrida. A ~8 s por
consulta, 289 SKUs son 578 búsquedas ≈ 77 min. Por eso se corre UNA vez y el
resultado se sube a Supabase (`competencia_subir.py`): el panel lee de ahí y nadie
vuelve a abrir el navegador para verlo.

REANUDABLE
----------
`--pendientes` salta los SKUs que ya tienen las dos búsquedas guardadas, para poder
cortar la corrida y retomarla sin repetir trabajo.
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

from services import (  # noqa: E402
    competencia_busqueda, competencia_captura, competencia_store,
)

# Largo máximo de la consulta por título. Los títulos de ML llegan a 60+ caracteres
# y una consulta larguísima devuelve pocos resultados o ninguno.
LARGO_TITULO = 60


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raiz", help="Solo esta raíz (p. ej. MLM44011)")
    ap.add_argument("--limite-skus", type=int, default=0, help="Corta tras N SKUs")
    ap.add_argument("--top", type=int, default=5, help="Resultados por búsqueda")
    ap.add_argument("--pendientes", action="store_true",
                    help="Salta los SKUs que ya tienen las dos búsquedas")
    args = ap.parse_args()

    skus = [s for s in competencia_store.listar_skus()
            if s.get("categoria_id") and s.get("termino_general")]
    if args.raiz:
        skus = [s for s in skus if s.get("raiz_id") == args.raiz]
    if args.pendientes:
        skus = [s for s in skus
                if not (competencia_store.resultados(s["sku"], "general")
                        and competencia_store.resultados(s["sku"], "titulo"))]
    if args.limite_skus:
        skus = skus[:args.limite_skus]

    print("═══ Competencia · las dos búsquedas ═══")
    print(f"SKUs: {len(skus)} · consultas: {len(skus) * 2} "
          f"· ~{len(skus) * 2 * 8 / 60:.0f} min con la ventana de Chrome abierta\n",
          flush=True)
    if not skus:
        print("Nada que medir.")
        return 0

    periodo = competencia_store.periodo_actual()
    nuestras = competencia_captura._nuestras_publicaciones()
    t0 = time.time()
    hechos = vacios = 0

    for i, s in enumerate(skus, 1):
        consultas = {
            s["termino_general"]: "general",
            (s["nombre"] or "")[:LARGO_TITULO]: "titulo",
        }
        # Un SKU cuyo título ES el término general no necesita dos búsquedas.
        res = competencia_busqueda.buscar([q for q in consultas if q], limite=args.top)
        for q, tipo in consultas.items():
            filas = res.get(q) or []
            if not filas:
                vacios += 1
                continue
            for f in filas:
                f["termino"] = q
            competencia_captura._marcar(filas, nuestras)
            await competencia_captura.enriquecer_visitas(filas)
            competencia_store.reemplazar_resultados(s["sku"], tipo, periodo, filas)
            hechos += 1
        if i % 5 == 0 or i == len(skus):
            print(f"  {i:>4}/{len(skus)} · guardadas={hechos} · vacías={vacios} "
                  f"· {time.time() - t0:.0f}s", flush=True)

    print(f"\nLISTO en {time.time() - t0:.0f}s · búsquedas guardadas: {hechos} "
          f"· sin resultados: {vacios}")
    print("Ahora: python scripts/competencia_subir.py --token sbp_…")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
