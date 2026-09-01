"""
monitoreo.py — Qué ha hecho cada persona en el panel.

  GET /api/monitoreo/resumen       → por usuario y canal, con éxitos vs intentos
  GET /api/monitoreo/movimientos   → el detalle, uno por uno

Va en `operador` y no en `admin`: el equipo debe poder ver su propio avance. Lo
que NO se expone aquí es nada de costos ni márgenes — solo quién hizo qué.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from core.seguridad import requiere_api_key
from services import monitoreo

router = APIRouter(prefix="/api/monitoreo", tags=["monitoreo"],
                   dependencies=[Depends(requiere_api_key)])


@router.get("/resumen")
async def resumen(dias: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    """Cuántas acciones lleva cada usuario, y en qué canal."""
    return monitoreo.resumen(dias)


@router.get("/movimientos")
async def movimientos(
    limite: int = Query(100, ge=1, le=500),
    usuario: str | None = Query(None),
    canal: str | None = Query(None),
    dias: int = Query(30, ge=1, le=365),
) -> dict[str, Any]:
    """El detalle: quién, qué, sobre qué SKU y cuándo."""
    filas = monitoreo.movimientos(limite, usuario, canal, dias)
    return {"total": len(filas), "movimientos": filas}
