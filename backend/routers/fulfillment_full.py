"""
fulfillment_full.py — «Crear FULL» y la ficha de un SKU (pestaña FULLFILMENT).
Las reglas viven en services/fulfillment_full.py y services/fulfillment_sku.py.

  GET  /api/fulfillment/crear-full                 insumos de la propuesta (las dos
                                                   cuentas), borradores FULL en Odoo
                                                   y el estado del interruptor
  POST /api/fulfillment/crear-full/vista-previa    qué se crearía, con el libre de
                                                   Odoo releído ahora. No escribe.
  POST /api/fulfillment/crear-full                 crea la cotización en BORRADOR
                                                   (una por almacén) — sólo con el
                                                   interruptor encendido
  POST /api/fulfillment/crear-full/interruptor     enciende/apaga esa escritura
  GET  /api/fulfillment/sku/{sku}                  la ficha del SKU (la ventana
                                                   que se abre desde un envío)

Permisos (core/rbac.py): los GET heredan `operador` de /api/fulfillment; la
vista previa también es de `operador` (no escribe); crear y el interruptor son
de admin — ESCRIBEN en Odoo o encienden la escritura.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from routers.fulfillment_envios import _lectura as lectura_envios
from services import cache_lectura, fulfillment_full, fulfillment_sku

router = APIRouter(prefix="/api/fulfillment", tags=["fulfillment"])
log = logging.getLogger("omnicanal.fulfillment_full")


class Renglon(BaseModel):
    sku: str
    cantidad: int = Field(ge=0, le=100_000)
    sugerido: int | None = None


class Solicitud(BaseModel):
    cuenta: str
    lineas: list[Renglon] = Field(max_length=500)
    # La genera el navegador al abrir la confirmación: con ella, un doble clic o
    # un reintento no crea dos veces la misma orden.
    clave: str | None = None
    parametros: dict[str, Any] | None = None


def _actor(request: Request | None) -> str:
    try:
        return str(getattr(getattr(request.state, "identidad", None), "actor", "") or "")
    except Exception:  # noqa: BLE001
        return ""


@router.get("/crear-full")
async def propuesta(refrescar: bool = Query(False)) -> dict[str, Any]:
    envios, _ = await lectura_envios(False)

    async def _producir() -> dict[str, Any]:
        try:
            # kubera + Odoo, las dos bloquean: en un hilo (regla 11).
            return await asyncio.to_thread(fulfillment_full.leer_propuesta, envios["envios"])
        except Exception as exc:  # noqa: BLE001
            log.warning("crear FULL: la propuesta no se pudo leer: %s", exc)
            raise HTTPException(502, f"no se pudo armar la propuesta: {exc}") from exc

    datos, edad = await cache_lectura.con_cache("fulfillment_crear_full", {}, _producir, refrescar=refrescar)
    interruptor = await asyncio.to_thread(fulfillment_full.estado_interruptor)
    return {**datos, "interruptor": interruptor,
            "_cache": {"edad_s": edad, "ttl_s": cache_lectura.TTL_S}}


@router.post("/crear-full/vista-previa")
async def vista_previa(s: Solicitud) -> dict[str, Any]:
    try:
        r = await asyncio.to_thread(fulfillment_full.vista_previa, s.cuenta,
                                    [l.model_dump() for l in s.lineas])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Odoo no contestó: {exc}") from exc
    return {**r, "interruptor": await asyncio.to_thread(fulfillment_full.estado_interruptor)}


@router.post("/crear-full")
async def crear(s: Solicitud, request: Request) -> dict[str, Any]:
    try:
        r = await asyncio.to_thread(fulfillment_full.crear, s.cuenta, [l.model_dump() for l in s.lineas],
                                    _actor(request), s.clave or "", s.parametros or {})
    except Exception as exc:  # noqa: BLE001
        # Pudo quedar creada una parte: la clave hace que reintentar no duplique.
        log.exception("crear FULL: falló a medias")
        raise HTTPException(502, f"Odoo falló a medias; reintentar con la misma solicitud no duplica: {exc}") from exc
    if r.get("accion") == "creada":
        # La propuesta ya debe restar el borrador nuevo.
        cache_lectura.invalidar("fulfillment_crear_full")
    return r


@router.post("/crear-full/interruptor")
async def interruptor(encendido: bool = Query(...), motivo: str = Query(""),
                      request: Request = None) -> dict[str, Any]:  # noqa: B008
    """Enciende o apaga la escritura de «Crear FULL» en Odoo. Queda quién y por qué."""
    return await asyncio.to_thread(fulfillment_full.fijar_interruptor, bool(encendido), _actor(request), motivo)


@router.get("/sku/{sku}")
async def ficha_sku(sku: str) -> dict[str, Any]:
    envios, _ = await lectura_envios(False)
    try:
        return await asyncio.to_thread(fulfillment_sku.ficha, sku, envios["envios"])
    except Exception as exc:  # noqa: BLE001
        log.warning("ficha de %s: %s", sku, exc)
        raise HTTPException(502, f"no se pudo leer la ficha de {sku}: {exc}") from exc
