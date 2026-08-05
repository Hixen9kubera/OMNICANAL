"""Recalcula las VISITAS de todos los resultados de búsqueda guardados.

POR QUÉ
-------
Los resultados del buscador traen dos clases de id que NO son publicaciones y con
las que `/visits` devuelve 0 en silencio — no falla, MIENTE:

  • `MLMU…`  producto de vendedor
  • `MLM…` de un URL `/p/`  producto de CATÁLOGO

El panel mostraba "0 visitas" en colchones con 25 mil. Los dos se resuelven con
`/products/{id}/items`, eligiendo el item con MÁS visitas: el más barato no siempre
es el que recibe el tráfico.

Es GRATIS: solo API de ML, sin Apify ni navegador.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from services import competencia_captura, competencia_store  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo-ceros", action="store_true",
                    help="Solo los términos con algún resultado en 0 o sin visitas")
    ap.add_argument("--limite", type=int, default=0)
    args = ap.parse_args()

    c = sqlite3.connect(competencia_store.RUTA_DB)
    c.row_factory = sqlite3.Row
    sql = "SELECT DISTINCT termino FROM busquedas"
    if args.solo_ceros:
        sql += " WHERE visitas_30d IS NULL OR visitas_30d = 0"
    terminos = [r["termino"] for r in c.execute(sql + " ORDER BY termino")]
    if args.limite:
        terminos = terminos[:args.limite]

    print(f"═══ Competencia · recalcular visitas ═══")
    print(f"términos: {len(terminos)} (gratis, solo API de ML)\n", flush=True)

    periodo = competencia_store.periodo_actual()
    t0 = time.time()
    resueltas = total = 0
    for i, termino in enumerate(terminos, 1):
        filas = [dict(r) for r in c.execute(
            "SELECT * FROM busquedas WHERE termino = ? ORDER BY posicion", (termino,))]
        if not filas:
            continue
        resueltas += await competencia_captura.enriquecer_visitas(filas)
        total += len(filas)
        competencia_store.reemplazar_busqueda(termino, periodo, filas)
        if i % 20 == 0 or i == len(terminos):
            print(f"  {i:>4}/{len(terminos)} · con visitas={resueltas}/{total} "
                  f"· {time.time() - t0:.0f}s", flush=True)

    print(f"\nLISTO en {time.time() - t0:.0f}s · {resueltas} de {total} con visitas")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
