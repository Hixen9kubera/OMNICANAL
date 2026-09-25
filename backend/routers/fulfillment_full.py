"""
fulfillment_full.py — «Crear FULL»: la PLANEACIÓN SEMANAL por tienda y su orden en
Odoo, y la ficha de un SKU (pestaña FULLFILMENT). Las reglas viven en
services/fulfillment_full.py, services/fulfillment_ia.py, services/fulfillment_excel.py
y services/fulfillment_sku.py.

  GET  /api/fulfillment/crear-full               insumos de las 4 tiendas (ML Kubera, ML San
                                                 Corpe, Amazon FBA, Walmart WFS), borradores,
                                                 lo que sale esta semana y el interruptor
  GET  /api/fulfillment/crear-full/buscar        SKUs publicados en una tienda (ML en vivo)
  GET  /api/fulfillment/crear-full/imagenes      la foto de Odoo de cada SKU (títulos que no coinciden)
  POST /api/fulfillment/crear-full/vista-previa  qué se crearía, releyendo Odoo y ML. No escribe.
  POST /api/fulfillment/crear-full/excel         la planeación en .xlsx. No escribe.
  POST /api/fulfillment/crear-full/ia            arranca la revisión con IA (Claude). No escribe.
  GET  /api/fulfillment/crear-full/ia/{id}       su resultado
  POST /api/fulfillment/crear-full               crea las cotizaciones en BORRADOR (interruptor)
  POST /api/fulfillment/crear-full/guia          número de envío + guía PDF en una orden del panel
  POST /api/fulfillment/crear-full/interruptor   enciende/apaga la escritura en Odoo
  GET  /api/fulfillment/sku/{sku}                la ficha del SKU (la ventana del detalle)

Permisos (core/rbac.py): los GET heredan `operador` de /api/fulfillment; vista previa,
Excel e IA también son de `operador` (no escriben); crear, guía e interruptor, de admin.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from routers.fulfillment_envios import _lectura as lectura_envios
from services import cache_lectura, fulfillment_excel, fulfillment_full, fulfillment_ia, fulfillment_sku

router = APIRouter(prefix="/api/fulfillment", tags=["fulfillment"])
log = logging.getLogger("omnicanal.fulfillment_full")

_MAX_PDF = 15 * 1024 * 1024


class Renglon(BaseModel):
    sku: str
    cantidad: int = Field(ge=0, le=100_000)
    sugerido: int | None = None


class PedidoTienda(BaseModel):
    tienda: str
    lineas: list[Renglon] = Field(max_length=1000)


class Solicitud(BaseModel):
    tiendas: list[PedidoTienda] = Field(max_length=4)
    # La genera el navegador al abrir la confirmación: con ella, un doble clic o
    # un reintento no crea dos veces la misma orden.
    clave: str | None = None
    parametros: dict[str, Any] | None = None
    # Modo prueba: la orden dice «PRUEBA · NO CONFIRMAR NI SURTIR» y no se resta
    # de la siguiente planeación.
    prueba: bool = True


def _actor(request: Request | None) -> str:
    try:
        return str(getattr(getattr(request.state, "identidad", None), "actor", "") or "")
    except Exception:  # noqa: BLE001
        return ""


@router.get("/crear-full")
async def propuesta(ventana: int = Query(30), refrescar: bool = Query(False)) -> dict[str, Any]:
    envios, _ = await lectura_envios(False)

    async def _producir() -> dict[str, Any]:
        try:
            # kubera, Odoo y ML bloquean: en un hilo (regla 11).
            return await asyncio.to_thread(fulfillment_full.leer_propuesta, envios["envios"], ventana)
        except Exception as exc:  # noqa: BLE001
            log.warning("planeación: no se pudo leer: %s", exc)
            raise HTTPException(502, f"no se pudo armar la planeación: {exc}") from exc

    datos, edad = await cache_lectura.con_cache("fulfillment_crear_full", {"ventana": ventana}, _producir,
                                                refrescar=refrescar)
    interruptor = await asyncio.to_thread(fulfillment_full.estado_interruptor)
    return {**datos, "interruptor": interruptor, "ia_disponible": fulfillment_ia.disponible(),
            "_cache": {"edad_s": edad, "ttl_s": cache_lectura.TTL_S}}


@router.get("/crear-full/buscar")
async def buscar(tienda: str = Query(...), q: str = Query(..., max_length=2000),
                 ventana: int = Query(30)) -> dict[str, Any]:
    envios, _ = await lectura_envios(False)
    try:
        return await asyncio.to_thread(fulfillment_full.buscar, tienda, q, envios["envios"], ventana)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"no se pudo buscar: {exc}") from exc


@router.get("/crear-full/imagenes")
async def imagenes(skus: str = Query(..., max_length=4000)) -> dict[str, Any]:
    """La foto de Odoo de cada SKU (hasta 60), para compararla con la del marketplace."""
    lista = [s for s in skus.split(",") if s.strip()]
    try:
        return {"imagenes": await asyncio.to_thread(fulfillment_full.imagenes_odoo, lista)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Odoo no contestó: {exc}") from exc


@router.post("/crear-full/vista-previa")
async def vista_previa(s: Solicitud) -> dict[str, Any]:
    try:
        r = await asyncio.to_thread(fulfillment_full.vista_previa, [t.model_dump() for t in s.tiendas], s.prueba)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Odoo o Mercado Libre no contestaron: {exc}") from exc
    return {**r, "interruptor": await asyncio.to_thread(fulfillment_full.estado_interruptor)}


@router.post("/crear-full/excel")
async def excel(plan: dict[str, Any] = Body(...)) -> Response:
    contenido = await asyncio.to_thread(fulfillment_excel.armar, plan)
    nombre = f"planeacion_full_{str(plan.get('semana') or 'semana').split(' ')[0]}.xlsx"
    return Response(contenido, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.post("/crear-full/ia")
async def ia(datos: dict[str, Any] = Body(...), request: Request = None) -> dict[str, Any]:  # noqa: B008
    return fulfillment_ia.iniciar(datos, _actor(request))


@router.get("/crear-full/ia/{tid}")
async def ia_estado(tid: str) -> dict[str, Any]:
    return fulfillment_ia.estado(tid)


@router.post("/crear-full")
async def crear(s: Solicitud, request: Request) -> dict[str, Any]:
    try:
        r = await asyncio.to_thread(fulfillment_full.crear, [t.model_dump() for t in s.tiendas],
                                    _actor(request), s.clave or "", s.parametros or {}, s.prueba)
    except Exception as exc:  # noqa: BLE001
        # Pudo quedar creada una parte: la clave hace que reintentar no duplique.
        log.exception("crear FULL: falló a medias")
        raise HTTPException(502, f"Odoo falló a medias; reintentar con la misma planeación no duplica: {exc}") from exc
    if r.get("accion") == "creada":
        cache_lectura.invalidar("fulfillment_crear_full")
    return r


@router.post("/crear-full/guia")
async def guia(orden_id: int = Form(...), numero: str = Form(""), pdf: UploadFile | None = File(None),
               request: Request = None) -> dict[str, Any]:  # noqa: B008
    contenido = None
    nombre = None
    if pdf is not None:
        contenido = await pdf.read()
        nombre = pdf.filename
        if len(contenido) > _MAX_PDF:
            raise HTTPException(413, "la guía pesa más de 15 MB")
    try:
        r = await asyncio.to_thread(fulfillment_full.adjuntar_guia, orden_id, numero, contenido, nombre,
                                    _actor(request))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Odoo no contestó: {exc}") from exc
    if r.get("ok"):
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
