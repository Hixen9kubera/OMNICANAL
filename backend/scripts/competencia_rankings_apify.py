"""Captura el ranking de más vendidos de las subcategorías PENDIENTES, vía Apify.

POR QUÉ HACE FALTA
------------------
Auditoría del 4-ago sobre Hogar, Muebles y Jardín: de 152 subcategorías, **149
tienen ranking o términos publicados en ML** y solo 1 estaba capturada. La vista
las mostraba como "sin datos" porque no sabía distinguir "no lo hemos capturado"
de "ML no lo publica" — son cosas distintas y llevan a acciones opuestas.

QUÉ CUESTA Y QUÉ NO
-------------------
  • `/highlights` y `/trends` son API de ML: GRATIS. De ahí salen la posición
    oficial, el tipo de entrada y los términos más buscados.
  • La FICHA (foto, título, precio, vendidos, score) solo existe raspando, porque
    `/items/{id}` de un ajeno responde 403. Eso va por el navegador genérico de
    Apify, que cobra por cómputo (~$0.007/página).

Se raspa desde Apify y no con el navegador local porque ML empezó a exigir sesión
a nuestra IP tras ~50 consultas; Apify corre con proxy residencial propio.
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

COSTO_PAGINA = 0.007
TANDA = 20


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raiz", required=True, help="Raíz a capturar (p. ej. MLM1574)")
    ap.add_argument("--top", type=int, default=20, help="Filas por categoría")
    ap.add_argument("--limite", type=int, default=0, help="Corta tras N categorías")
    ap.add_argument("--solo", nargs="*", metavar="MLM####",
                    help="Solo estas subcategorías. Sirve para no pagar por las "
                         "que ya se sabe que ML no publica: de las 7 pendientes de "
                         "Herramientas, 6 tienen /highlights vacío y raspar su "
                         "página no devuelve nada.")
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    raiz = next((r for r in competencia_store.vista()
                 if r["raiz_id"] == args.raiz), None)
    if not raiz:
        print(f"No hay SKUs vigilados bajo {args.raiz}.")
        return 1

    # Pendiente = sin ranking guardado. Los términos se piden igual porque son
    # gratis y su ausencia es justo lo que hoy no sabemos distinguir.
    pend = [s for s in raiz["subcategorias"]
            if s["categoria_id"] and not s["n_ranking"]]
    if args.solo:
        quiere = {c.strip().upper() for c in args.solo if c and c.strip()}
        pend = [s for s in pend if s["categoria_id"] in quiere]
    if args.limite:
        pend = pend[:args.limite]

    costo = len(pend) * COSTO_PAGINA
    print("═══ Competencia · rankings de subcategoría por Apify ═══")
    print(f"{raiz['raiz_nombre']} · {len(raiz['subcategorias'])} subcategorías "
          f"· pendientes: {len(pend)}")
    print(f"costo estimado: ${costo:.2f} ({len(pend)} páginas × ${COSTO_PAGINA})")
    print("(/highlights y /trends van gratis por la API de ML)\n", flush=True)
    if not args.execute:
        print("DRY-RUN. Vuelve a correr con --execute para gastar.")
        for s in pend[:15]:
            print(f"   {s['categoria_id']:12} {s['categoria_nombre']}")
        return 0
    if not pend:
        return 0

    periodo = competencia_store.periodo_actual()
    nuestras = competencia_captura._nuestras_publicaciones()
    t0 = time.time()
    con_ranking = con_terminos = sin_nada = 0

    for ini in range(0, len(pend), TANDA):
        lote = pend[ini:ini + TANDA]
        cats = [s["categoria_id"] for s in lote]
        try:
            crudos = await competencia_scraper.mas_vendidos_categorias(
                cats, limite=args.top)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! tanda {ini}: {exc}", flush=True)
            crudos = {}

        for s in lote:
            cid = s["categoria_id"]
            filas = crudos.get(cid) or []
            if filas:
                competencia_captura._marcar(filas, nuestras)
                await competencia_captura._enriquecer_ranking(cid, filas, "hoja")
                competencia_store.reemplazar_ranking(cid, "hoja", periodo, filas)
                con_ranking += 1
            # Términos: gratis, y se piden aunque el raspado haya fallado.
            t = await asyncio.to_thread(competencia_ml.tendencias, cid)
            if t:
                competencia_store.reemplazar_terminos(
                    cid, periodo,
                    [{"termino": x["keyword"], "url": x.get("url")} for x in t])
                con_terminos += 1
            if not filas and not t:
                sin_nada += 1
        print(f"  {min(ini + TANDA, len(pend)):>3}/{len(pend)} "
              f"· con ranking={con_ranking} · con términos={con_terminos} "
              f"· sin nada={sin_nada} · {time.time() - t0:.0f}s", flush=True)

    print(f"\nLISTO en {time.time() - t0:.0f}s")
    print(f"  con ranking : {con_ranking}")
    print(f"  con términos: {con_terminos}")
    print(f"  sin nada    : {sin_nada}  ← estas sí son 'ML no publica'")
    print(f"gasto aproximado: ${len(pend) * COSTO_PAGINA:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
