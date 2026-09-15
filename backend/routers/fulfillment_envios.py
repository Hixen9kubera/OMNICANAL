"""
fulfillment_envios.py — Envíos a los almacenes de los marketplaces (pestaña
FULLFILMENT). LECTURA PURA de Odoo; las reglas viven en
services/fulfillment_envios.py.

  GET /api/fulfillment/envios        salidas a ML FULL / Amazon FBA / Walmart WFS
                                     con canal, cuenta (y de dónde salió), orden
                                     de venta, salida y piezas; + el resumen.
  GET /api/fulfillment/envios/{id}   una salida con sus renglones por SKU.

La lista va SIN renglones: con ellos pesaba ~450 KB y la tabla no los usa. Los
dos endpoints comparten la misma lectura de Odoo en caché, así que abrir un
detalle no vuelve a preguntar a Odoo.

Cuelga de `/api/fulfillment` a propósito (Brandon, 15-sep-2026), así que hereda
`GET /api/fulfillment → operador` de core/rbac.py. El día que esta pestaña tenga
POST (lista de Andy, validación de Bodega) cada uno necesita SU línea en REGLAS:
un POST no listado nace de admin.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from services import cache_lectura, fulfillment_envios

router = APIRouter(prefix="/api/fulfillment/envios", tags=["fulfillment"])
log = logging.getLogger("omnicanal.fulfillment_envios")


async def _lectura(refrescar: bool) -> tuple[dict[str, Any], int]:
    async def _producir() -> dict[str, Any]:
        try:
            # XML-RPC es bloqueante: en un hilo, o congela el backend entero
            # (regla 11 del CLAUDE.md).
            return await asyncio.to_thread(fulfillment_envios.leer)
        except Exception as exc:  # noqa: BLE001
            log.warning("envíos fulfillment: lectura de Odoo falló: %s", exc)
            raise HTTPException(502, f"lectura de Odoo falló: {exc}") from exc

    return await cache_lectura.con_cache(
        "fulfillment_envios", {}, _producir, refrescar=refrescar)


@router.get("")
async def envios(refrescar: bool = Query(False)) -> dict[str, Any]:
    datos, edad = await _lectura(refrescar)
    lista = [{**{k: v for k, v in e.items() if k != "lineas"}, "n_skus": len(e["lineas"])}
             for e in datos["envios"]]
    return {**datos, "envios": lista, "_cache": {"edad_s": edad, "ttl_s": cache_lectura.TTL_S}}


@router.get("/{salida_id}")
async def envio(salida_id: int) -> dict[str, Any]:
    datos, edad = await _lectura(False)
    for e in datos["envios"]:
        if e["id"] == salida_id:
            return {**e, "generado": datos.get("generado"),
                    "_cache": {"edad_s": edad, "ttl_s": cache_lectura.TTL_S}}
    raise HTTPException(404, f"la salida {salida_id} no es un envío a FULL, FBA ni WFS")
