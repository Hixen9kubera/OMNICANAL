"""Actualiza el PRECIO de nuestras publicaciones al que el comprador PAGA.

POR QUÉ
-------
`channel.listings.price` y `/items/{id}.price` son el precio de LISTA, y puede
estar muy por encima del real. Medido en CAM-0030-IND: lista $7,755.92 contra
$3,294 de venta en BEKURA y $3,899 en SANCORFASHION — 58% y 50% de descuento.

Mostrar el de lista no es un detalle cosmético: falsea la BRECHA contra el
mercado, que es la columna que manda en la vista. Ese colchón aparecía a 3.5x la
mediana cuando en realidad está a 1.5x.

El precio real sale de `/items/{id}/sale_price?context=channel_marketplace`, que
solo funciona con publicaciones PROPIAS (en ajenas responde 403). Es GRATIS.
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

from services import competencia_ml, competencia_store  # noqa: E402

# La cuenta del token importa: cada tienda solo puede leer sus propias
# publicaciones, así que el precio se pide con el token de SU cuenta.
TOKEN_DE = {"BEKURA": "bekura", "SANCORFASHION": "sancorfashion"}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sku", help="Solo este SKU")
    ap.add_argument("--limite", type=int, default=0)
    args = ap.parse_args()

    pubs = [p for p in competencia_store.publicaciones(args.sku)
            if p.get("ml_item_id") and p.get("cuenta") in TOKEN_DE]
    if args.limite:
        pubs = pubs[:args.limite]

    print("═══ Competencia · precio de venta de nuestras publicaciones ═══")
    print(f"publicaciones: {len(pubs)} (gratis, solo API de ML)\n", flush=True)

    t0 = time.time()
    con_desc = sin_cambio = fallo = 0
    LOTE = 40
    for ini in range(0, len(pubs), LOTE):
        lote = pubs[ini:ini + LOTE]
        res = await asyncio.gather(*(
            asyncio.to_thread(competencia_ml.precio_venta,
                              p["ml_item_id"], TOKEN_DE[p["cuenta"]])
            for p in lote), return_exceptions=True)
        filas = []
        for p, r in zip(lote, res):
            if not isinstance(r, dict) or r.get("precio") is None:
                fallo += 1
                continue
            lista = r.get("precio_lista")
            if lista and r["precio"] < lista:
                con_desc += 1
            else:
                sin_cambio += 1
            filas.append({**p, "precio": r["precio"], "precio_lista": lista})
        if filas:
            competencia_store.guardar_publicaciones(filas)
        print(f"  {min(ini + LOTE, len(pubs)):>4}/{len(pubs)} · con descuento={con_desc} "
              f"· sin descuento={sin_cambio} · sin dato={fallo} "
              f"· {time.time() - t0:.0f}s", flush=True)

    print(f"\nLISTO en {time.time() - t0:.0f}s")
    print(f"  con descuento : {con_desc}")
    print(f"  sin descuento : {sin_cambio}")
    print(f"  sin dato      : {fallo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
