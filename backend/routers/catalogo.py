"""
catalogo.py — La pestaña CATÁLOGO (v0.703.0). Ver `services/catalogo.py`.

  GET /api/catalogo            → el catálogo completo (JSON comprimido con gzip, tal como está en el repo)
  GET /api/catalogo/img/{hoja} → una hoja de fotos (`f0`, `f12`…), WebP

Solo lectura. `operador`, como Competencia (core/rbac.py): trae precios de mercado y el valor del inventario, no
costos.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from services import catalogo

router = APIRouter(prefix="/api/catalogo", tags=["catalogo"])


@router.get("")
async def datos() -> Response:
    try:
        crudo = await asyncio.to_thread(catalogo.datos_gz)
    except catalogo.SinCatalogo as exc:
        raise HTTPException(status_code=404, detail="Todavía no hay catálogo en el servidor.") from exc
    return Response(content=crudo, media_type="application/json",
                    headers={"Content-Encoding": "gzip", "Cache-Control": "private, max-age=3600"})


@router.get("/img/{hoja}")
async def imagen(hoja: str) -> Response:
    try:
        dato = await asyncio.to_thread(catalogo.hoja, hoja)
    except catalogo.SinCatalogo as exc:
        raise HTTPException(status_code=404, detail="Hoja de fotos desconocida.") from exc
    # La pantalla pide `?v=<preparado>`: un catálogo nuevo cambia la URL y no se mezcla con la hoja vieja.
    return Response(content=dato, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})
