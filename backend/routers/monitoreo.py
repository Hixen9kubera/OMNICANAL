"""
monitoreo.py — Qué ha hecho cada persona en el panel.

  GET /api/monitoreo/resumen       → por usuario y canal, con éxitos vs intentos
  GET /api/monitoreo/movimientos   → el detalle, uno por uno

Va en `operador` y no en `admin`: el equipo debe poder ver su propio avance. Lo
que NO se expone aquí es nada de costos ni márgenes — solo quién hizo qué.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query

from core.seguridad import requiere_api_key
from services import monitoreo

router = APIRouter(prefix="/api/monitoreo", tags=["monitoreo"],
                   dependencies=[Depends(requiere_api_key)])


@router.get("/resumen")
async def resumen(dias: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    """Cuántas acciones lleva cada usuario, y en qué canal.

    ⚠️ `to_thread` NO ES OPCIONAL. `monitoreo.resumen` habla con Postgres por
    psycopg2, que BLOQUEA: llamarlo directo desde una corrutina detiene el event
    loop —o sea, el backend ENTERO— mientras responde, no sólo a quien pidió
    esto. Es la regla 11 de la casa y el defecto exacto del apagón de cinco horas
    del 13-ago. Pesaba poco cuando era UNA consulta; desde el 4-sep son siete.
    """
    return await asyncio.to_thread(monitoreo.resumen, dias)


@router.get("/movimientos")
async def movimientos(
    limite: int = Query(100, ge=1, le=500),
    usuario: str | None = Query(None),
    canal: str | None = Query(None),
    dias: int = Query(30, ge=1, le=365),
) -> dict[str, Any]:
    """El detalle: quién, qué, sobre qué SKU y cuándo."""
    filas = await asyncio.to_thread(monitoreo.movimientos,
                                    limite, usuario, canal, dias)
    return {"total": len(filas), "movimientos": filas}
