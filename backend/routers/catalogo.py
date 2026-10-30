"""
catalogo.py — La pestaña CATÁLOGO (v0.703.0). Ver `services/catalogo.py`.

  GET /api/catalogo            → el catálogo completo (JSON comprimido con gzip, tal como está en el repo)
  GET /api/catalogo/img/{hoja} → una hoja de fotos (`f0`, `f12`…), WebP
  GET /api/catalogo/publicados → qué SKUs están publicados HOY en Mercado Libre, por cuenta (v0.708.0)

Solo lectura. `operador`, como Competencia (core/rbac.py): trae precios de mercado y el valor del inventario, no
costos.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from services import catalogo

log = logging.getLogger("omnicanal.catalogo")

router = APIRouter(prefix="/api/catalogo", tags=["catalogo"])


@router.get("")
async def datos() -> Response:
    try:
        crudo = await asyncio.to_thread(catalogo.datos_gz)
    except catalogo.SinCatalogo as exc:
        raise HTTPException(status_code=404, detail="Todavía no hay catálogo en el servidor.") from exc
    return Response(content=crudo, media_type="application/json",
                    headers={"Content-Encoding": "gzip", "Cache-Control": "private, max-age=3600"})


@router.get("/publicados")
async def publicados() -> dict[str, Any]:
    """Qué SKUs están publicados en Mercado Libre hoy (activa o pausada), por cuenta, y la hora del último sync."""
    try:
        return await asyncio.to_thread(catalogo.publicados)
    except Exception as exc:  # noqa: BLE001
        log.warning("catálogo: no se pudo leer lo publicado en ML: %s", exc)
        raise HTTPException(status_code=503, detail="No se pudo leer qué está publicado en Mercado Libre.") from exc


@router.get("/img/{hoja}")
async def imagen(hoja: str) -> Response:
    try:
        dato = await asyncio.to_thread(catalogo.hoja, hoja)
    except catalogo.SinCatalogo as exc:
        raise HTTPException(status_code=404, detail="Hoja de fotos desconocida.") from exc
    # La pantalla pide `?v=<preparado>`: un catálogo nuevo cambia la URL y no se mezcla con la hoja vieja.
    return Response(content=dato, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})
