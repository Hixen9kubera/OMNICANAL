"""
radar_precios.py — Radar de precios por contenedor · F1 (solo Mercado Libre).

  GET /api/radar-precios          → la lista (filtros, conteos, página)
  GET /api/radar-precios?todos=1  → la lista SIN el recorte del piloto
  GET /api/radar-precios/{sku}    → el item + comparables, serie 90 d, categoría

PILOTO: mientras `services.radar_precios.PILOTO` tenga SKUs, la lista y sus
conteos muestran solo esos (la respuesta lo dice en `piloto`). El universo se
arma completo igual —una vez, en la caché— y el recorte va encima; el detalle
contesta para cualquier SKU con publicación activa.

SOLO LECTURA. Ninguna ruta escribe: no hay POST/PUT/PATCH/DELETE aquí, y el
servicio (`services/radar_precios.py`) solo hace SELECT.

ACCESO: sólo admin con sesión del panel (`solo_admin` de routers/investigacion:
rechaza la X-API-Key y no depende de AUTH_ENFORCED/RBAC_ENFORCED). Además la
regla explícita ("GET", "/api/radar-precios", "admin") en core/rbac.py.

CACHÉ: 10 minutos en memoria, por proceso. El universo (todas las filas ya
calculadas) se arma UNA vez y los filtros se aplican encima, así que cambiar de
tarjeta o de página no vuelve a tocar la base. La edad viaja en `_cache`.
`cache_lectura` no sirve tal cual: su TTL (2 min) es global a todos sus
llamadores y cambiarlo movería otras pantallas.

La BD va SIEMPRE por `asyncio.to_thread` (regla 11: psycopg2 es bloqueante).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from config import settings
from routers.investigacion import solo_admin
from services import radar_precios as radar

log = logging.getLogger("omnicanal.radar_precios")
router = APIRouter(prefix="/api/radar-precios", tags=["radar-precios"])

TTL_S = 600
_MAX_DETALLES = 200

_universo: tuple[float, dict] | None = None
_detalles: dict[str, tuple[float, dict]] = {}
_candado = asyncio.Lock()


async def _obtener_universo(refrescar: bool = False) -> tuple[dict, int]:
    """El universo cacheado 10 min; con candado para que dos pestañas que
    abren a la vez no paguen dos veces la misma lectura."""
    global _universo
    ahora = time.monotonic()
    if not refrescar and _universo and ahora - _universo[0] <= TTL_S:
        return _universo[1], int(ahora - _universo[0])
    async with _candado:
        ahora = time.monotonic()
        if not refrescar and _universo and ahora - _universo[0] <= TTL_S:
            return _universo[1], int(ahora - _universo[0])
        dato = await asyncio.to_thread(radar.construir_universo)
        _universo = (time.monotonic(), dato)
        _detalles.clear()
        return dato, 0


@router.get("")
async def listar(
    cuenta: str | None = Query(None),
    clase: str | None = Query(None),
    direccion: str | None = Query(None),
    contenedor: str | None = Query(None),
    q: str | None = Query(None),
    limite: int = Query(200, ge=1, le=1000),
    pagina: int = Query(1, ge=1),
    refrescar: bool = Query(False),
    # `todos=1` o `todos=true` (FastAPI acepta los dos): sin recorte del piloto.
    todos: bool = Query(False),
    quien=Depends(solo_admin),
) -> dict[str, Any]:
    if clase and clase not in radar.CLASES:
        raise HTTPException(status_code=400, detail=f"clase desconocida: {clase}")
    if direccion and direccion not in radar.DIRECCIONES:
        raise HTTPException(status_code=400, detail=f"dirección desconocida: {direccion}")
    try:
        universo, edad = await _obtener_universo(refrescar)
    except Exception as exc:  # noqa: BLE001
        log.exception("radar: no se pudo armar el universo")
        raise HTTPException(status_code=502,
                            detail=f"No se pudo leer el radar: {type(exc).__name__}") from exc
    datos = radar.filtrar(universo, cuenta=cuenta, clase=clase, direccion=direccion,
                          contenedor=contenedor, q=q, limite=limite, pagina=pagina,
                          ambiente=settings.app_env, todos=todos)
    return {**datos, "_cache": {"edad_s": edad, "ttl_s": TTL_S}}


@router.get("/{sku}")
async def detalle(sku: str, quien=Depends(solo_admin)) -> dict[str, Any]:
    clave = (sku or "").strip().upper()
    try:
        universo, _ = await _obtener_universo()
        ahora = time.monotonic()
        hit = _detalles.get(clave)
        if hit and ahora - hit[0] <= TTL_S:
            return {**hit[1], "_cache": {"edad_s": int(ahora - hit[0]), "ttl_s": TTL_S}}
        dato = await asyncio.to_thread(radar.detalle_de, universo, clave)
    except Exception as exc:  # noqa: BLE001
        log.exception("radar: detalle de %s falló", clave)
        raise HTTPException(status_code=502,
                            detail=f"No se pudo leer el radar: {type(exc).__name__}") from exc
    if dato is None:
        raise HTTPException(status_code=404,
                            detail="Ese SKU no tiene publicación activa de Mercado Libre.")
    if len(_detalles) >= _MAX_DETALLES:
        _detalles.clear()
    _detalles[clave] = (time.monotonic(), dato)
    return {**dato, "_cache": {"edad_s": 0, "ttl_s": TTL_S}}
