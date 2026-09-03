"""Mide la búsqueda general de los términos PENDIENTES con el actor de Apify.

POR QUÉ APIFY Y NO EL NAVEGADOR LOCAL
-------------------------------------
ML empezó a exigir sesión tras ~50 consultas seguidas desde nuestra IP. Apify corre
desde su infraestructura con proxy residencial, así que el muro no nos toca. El
navegador local sigue sirviendo para los RANKINGS de categoría, que son pocas
páginas y nunca se bloquearon.

COSTO, Y POR QUÉ NO SE PUEDE ABARATAR AGRUPANDO
-----------------------------------------------
El actor cobra $0.003 por item MÁS $0.09 por corrida. Lo lógico sería mandar 25
consultas en una sola corrida y pagar la cuota fija una vez… pero MEDIDO: el actor
NO etiqueta cada resultado con la consulta que lo trajo (`searchQuery` viene vacío)
y los devuelve mezclados, así que no hay forma de repartirlos. Agrupar es
inservible y va UNA consulta por corrida: ~$0.105 cada término.

SIN DETALLE
-----------
`includeProductDetail` cuesta 8x por item ($0.025 contra $0.003) y solo agrega
`vendidos`, la descripción corta y el id de catálogo. Reseñas, rating y visitas se
consiguen GRATIS por la API de ML, así que se piden aparte. `vendidos` queda en
NULL: para publicaciones ajenas no existe por API y solo lo da el detalle.

POR TÉRMINO, NO POR SKU
-----------------------
Se guarda en `busquedas`, cuya llave es el término. Varios SKUs comparten término y
así se mide y se paga una sola vez.
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
    competencia_captura, competencia_ml, competencia_scraper, competencia_store,
)

# El navegador genérico cobra por CÓMPUTO, no por corrida ni por item: ~$0.007 por
# página, y cada página es UNA consulta. Sustituyó al actor de ML, que cobraba
# $0.09 por corrida y no decía de qué consulta venía cada resultado.
COSTO_PAGINA = 0.007
LARGO_TITULO = 60


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raiz", help="Solo esta raíz (p. ej. MLM44011)")
    ap.add_argument("--top", type=int, default=10, help="Resultados por término")
    ap.add_argument("--sin-titulo", action="store_true",
                    help="Solo el término general, sin la búsqueda por título")
    ap.add_argument("--limite", type=int, default=0, help="Corta tras N términos")
    ap.add_argument("--execute", action="store_true",
                    help="Sin esto solo estima el gasto y no llama a Apify")
    args = ap.parse_args()

    skus = [s for s in competencia_store.listar_skus() if s.get("termino_general")]
    if args.raiz:
        skus = [s for s in skus if s.get("raiz_id") == args.raiz]
    ya = competencia_store.terminos_medidos()
    # Los términos ya medidos NO se vuelven a pagar: esa es la razón de que la
    # tabla sea por término y no por SKU. El TÍTULO también es un término, así que
    # entra por la misma puerta y se guarda igual.
    quiere = {s["termino_general"] for s in skus}
    if not args.sin_titulo:
        quiere |= {(s["nombre"] or "")[:LARGO_TITULO] for s in skus if s.get("nombre")}
    pendientes = sorted(q for q in quiere if q and q not in ya)
    if args.limite:
        pendientes = pendientes[:args.limite]

    costo = len(pendientes) * COSTO_PAGINA
    print("═══ Competencia · búsquedas con el navegador de Apify ═══")
    print(f"SKUs: {len(skus)} · términos pendientes: {len(pendientes)} "
          f"· ya medidos: {len(ya)}")
    print(f"costo estimado: ${costo:.2f} "
          f"({len(pendientes)} páginas × ${COSTO_PAGINA})\n", flush=True)
    if not args.execute:
        print("DRY-RUN. Vuelve a correr con --execute para gastar.")
        for t in pendientes[:15]:
            print(f"   {t}")
        return 0
    if not pendientes:
        return 0

    t0 = time.time()
    ok = vacios = 0

    # Por tandas: una corrida del actor con varias URLs. La atribución es por URL,
    # así que agrupar aquí SÍ funciona (con el actor de ML no: no etiquetaba).
    TANDA = 20
    for ini in range(0, len(pendientes), TANDA):
        lote = pendientes[ini:ini + TANDA]
        try:
            # La receta completa —raspar, reseñas, visitas, marcar lo nuestro,
            # guardar— vive en `competencia_captura.medir_busquedas`, porque el
            # botón del panel mide UN término por la misma puerta. Aquí sólo se
            # arma la tanda.
            guardadas = await competencia_captura.medir_busquedas(lote, limite=args.top)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! tanda {ini}: {exc}", flush=True)
            continue
        for termino in lote:
            n = guardadas.get(termino, 0)
            if n:
                ok += n
            else:
                vacios += 1
        print(f"  {min(ini + TANDA, len(pendientes)):>3}/{len(pendientes)} "
              f"· filas={ok} · vacías={vacios} · {time.time() - t0:.0f}s", flush=True)

    print(f"\nLISTO en {time.time() - t0:.0f}s · filas guardadas: {ok} "
          f"· términos sin resultados: {vacios}")
    print(f"gasto real aproximado: ${len(pendientes) * COSTO_PAGINA:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
