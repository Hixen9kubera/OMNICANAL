"""
checklist.py — Endpoints de INVENTARIO · Checklist (validación de almacén).

Router PROPIO y no colgado de /api/inventario a propósito: ese router termina en
`GET /{sku:path}` (la ficha), una ruta glotona que se come cualquier cosa que
se declare después. Aquí no hay SKUs con diagonal que disputen la ruta.

Todo el trabajo va en `asyncio.to_thread` (regla 11): psycopg2 a kubera y
urllib a la API de ML son bloqueantes. `to_thread` copia el contextvar del
actor, así que la autoría llega intacta al hilo.

Ver la cabecera de `services/checklist.py` para el qué y el porqué.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from services import checklist as ck

log = logging.getLogger("omnicanal.routers.checklist")

router = APIRouter(prefix="/api/checklist", tags=["checklist"])

_MAX_ARCHIVO = 15 * 1024 * 1024   # un Excel de 500 SKUs pesa ~200 KB


class _Lote(BaseModel):
    semana: str | None = None
    skus: list[str] | str = []


class _Matriz(BaseModel):
    campos: dict[str, bool] = {}


class _Almacen(BaseModel):
    valores: dict[str, Any] = {}


@router.get("")
async def tablero(semana: str | None = Query(
        None, description="Cualquier día de la semana (YYYY-MM-DD). Sin nada, la de hoy.")):
    """Los SKUs de la semana con su estado: atributos de ML y datos de almacén."""
    return await asyncio.to_thread(ck.tablero_sync, semana)


@router.post("/lote")
async def agregar(body: _Lote):
    """Agrega SKUs al lote de la semana. Acepta lista o texto pegado de Excel."""
    return await asyncio.to_thread(ck.agregar_sync, body.semana, body.skus)


@router.post("/lote/quitar")
async def quitar(body: _Lote):
    return await asyncio.to_thread(ck.quitar_sync, body.semana, body.skus)


@router.get("/matriz/{categoria}")
async def matriz(categoria: str):
    """Los atributos de una categoría de ML con su nivel: obligatorio de ML,
    obligatorio por la matriz, principal o secundario."""
    return await asyncio.to_thread(ck.matriz_sync, categoria.strip().upper())


@router.put("/matriz/{categoria}")
async def guardar_matriz(categoria: str, body: _Matriz):
    """Sube opcionales a obligatorios (o los regresa). Los de ML no se tocan."""
    return await asyncio.to_thread(ck.guardar_matriz_sync, categoria.strip().upper(),
                                   body.campos)


def _archivo(datos: bytes, nombre: str, tipo: str) -> Response:
    return Response(content=datos, media_type=tipo,
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.get("/excel")
async def excel(semana: str | None = Query(None),
                skus: str | None = Query(None, description="Separados por coma. "
                                         "Sin nada, todo el lote de la semana.")):
    try:
        datos, nombre = await asyncio.to_thread(ck.excel_sync, semana, skus)
    except ck.FaltaMigracion as exc:
        raise HTTPException(409, str(exc)) from exc
    return _archivo(datos, nombre, "application/vnd.openxmlformats-officedocument"
                                   ".spreadsheetml.sheet")


@router.get("/csv")
async def csv_(semana: str | None = Query(None), skus: str | None = Query(None)):
    try:
        datos, nombre = await asyncio.to_thread(ck.csv_sync, semana, skus)
    except ck.FaltaMigracion as exc:
        raise HTTPException(409, str(exc)) from exc
    return _archivo(datos, nombre, "text/csv; charset=utf-8")


@router.post("/importar")
async def importar(archivo: UploadFile = File(...), aplicar: bool = Form(False)):
    """Carga el Excel o CSV llenado. Con `aplicar=false` solo dice qué CAMBIARÍA
    (la pantalla lo enseña antes de guardar); con `true` lo guarda."""
    datos = await archivo.read()
    if len(datos) > _MAX_ARCHIVO:
        raise HTTPException(413, "El archivo pesa más de 15 MB: ¿es el que "
                                 "descargaste del panel?")
    return await asyncio.to_thread(ck.importar_sync, datos, archivo.filename or "",
                                   aplicar)


@router.put("/almacen/{sku:path}")
async def guardar_almacen(sku: str, body: _Almacen):
    """Captura en pantalla de medidas, cajas y piezas de UN SKU."""
    return await asyncio.to_thread(ck.guardar_almacen_sync, sku, body.valores)
