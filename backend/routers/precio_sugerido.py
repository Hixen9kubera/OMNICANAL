"""
precio_sugerido.py — «Sugerir precio» del Estudio (v0.686.0). Ver `services/precio_sugerido.py`.

  POST /api/precio-sugerido/rapido          → GRATIS, segundos: más vendidos de la categoría + comisión real + piso
  POST /api/precio-sugerido/completo        → $0.02–0.10, ~1–2 min: arranca el trabajo y devuelve su `jid`
  GET  /api/precio-sugerido/trabajo/{jid}   → cómo va (y el resultado al terminar)
  GET  /api/precio-sugerido/ultimo/{sku}    → la última consulta del SKU, para no volver a pagar

Solo sugiere: ninguna ruta escribe precios. Lo único que escribe es su bitácora en `ops.process_log`.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import precio_sugerido as ps

log = logging.getLogger("omnicanal.routers.precio_sugerido")
router = APIRouter(prefix="/api/precio-sugerido", tags=["precio-sugerido"])


class Entrada(BaseModel):
    """Lo que se ve en el Estudio manda; lo que falte sale del panel y de Woo."""
    sku: str = Field(min_length=1)
    titulo: str | None = None
    ml_cat_id: str | None = None
    costo: float | None = None
    imagen: str | None = None
    peso_kg: float | None = None
    margen: float = Field(default=ps.MARGEN_DEFAULT, ge=0, le=5)
    terminos: list[str] | None = None


@router.post("/rapido")
async def rapido(req: Entrada) -> dict:
    try:
        return await ps.rapido(req.model_dump())
    except Exception as exc:  # noqa: BLE001
        log.exception("precio sugerido rápido de %s falló", req.sku)
        raise HTTPException(status_code=502, detail=f"No se pudo sugerir el precio: {exc}") from exc


@router.post("/completo")
async def completo(req: Entrada) -> dict:
    return ps.arrancar_completo(req.model_dump())


@router.get("/trabajo/{jid}")
async def trabajo(jid: str) -> dict:
    e = ps.estado(jid)
    if not e:
        raise HTTPException(status_code=404, detail="Ese trabajo ya no existe: si terminó, su resultado quedó guardado; "
                                                    "vuelve a abrir el producto.")
    return e


@router.get("/ultimo/{sku:path}")
async def ultimo(sku: str) -> dict:
    r = await asyncio.to_thread(ps.ultimo, sku)
    return {"resultado": r}
