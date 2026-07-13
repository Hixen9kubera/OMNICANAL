"""
imagenes.py — Editor de imágenes de producto (galería WooCommerce + IA).

  GET  /api/imagenes/{sku}            → galería (con id/posición) + progreso en curso
  POST /api/imagenes/{sku}/procesar   → edita con IA las imágenes con flags (background)
  GET  /api/imagenes/{sku}/progreso   → estado por imagen (para el label de carga)
  POST /api/imagenes/{sku}/eliminar   → quita una imagen de la galería del producto
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services import imagenes_editor as editor, woocommerce

log = logging.getLogger("omnicanal.routers.imagenes")
router = APIRouter(prefix="/api/imagenes", tags=["imagenes"])


class ImagenFlags(BaseModel):
    wc_image_id: int | None = None
    src: str
    quitar_fondo: bool = False
    traducir_texto: bool = False
    quitar_logos: bool = False
    cambiar_modelo: bool = False


class ProcesarReq(BaseModel):
    wc_id: int | None = None
    imagenes: list[ImagenFlags] = []


class EliminarReq(BaseModel):
    wc_id: int | None = None
    image_id: int


class ImagenNueva(BaseModel):
    filename: str = "imagen"
    mime: str = "image/jpeg"
    data_b64: str  # base64 (sin el prefijo data:...;base64,)


class AgregarReq(BaseModel):
    wc_id: int | None = None
    imagenes: list[ImagenNueva] = []


@router.get("/{sku}")
async def galeria(sku: str, wc_id: int | None = Query(None)):
    """Galería completa del producto (portada + imágenes con id/posición)."""
    g = None
    try:
        g = await woocommerce.galeria_producto(wc_id, sku)
    except Exception as exc:  # noqa: BLE001
        log.warning("galeria %s: %s", sku, exc)
    if not g:
        g = {"wc_id": wc_id, "parent_id": wc_id, "es_variacion": False,
             "portada": None, "imagenes": []}
    g["sku"] = sku
    g["progreso"] = editor.progreso(sku)  # si hay un procesamiento en curso
    return g


@router.post("/{sku}/procesar")
async def procesar(sku: str, req: ProcesarReq):
    """Lanza la edición con IA (según flags) en segundo plano y responde de inmediato."""
    con_flags = [
        i for i in req.imagenes
        if i.quitar_fondo or i.traducir_texto or i.quitar_logos or i.cambiar_modelo
    ]
    if not con_flags:
        raise HTTPException(400, "Ninguna imagen tiene flags seleccionados.")
    return await editor.iniciar(sku, req.wc_id, [i.model_dump() for i in con_flags])


@router.get("/{sku}/progreso")
async def progreso(sku: str):
    j = editor.progreso(sku)
    if not j:
        return {"sku": sku, "estado": "sin_datos", "total": 0, "procesadas": 0,
                "paso_global": "", "imagenes": []}
    return j


@router.post("/{sku}/eliminar")
async def eliminar(sku: str, req: EliminarReq):
    """Quita una imagen de la galería (resuelve el padre si es variación)."""
    try:
        g = await woocommerce.galeria_producto(req.wc_id, sku)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"No se pudo leer el producto: {exc}")
    parent_id = (g or {}).get("parent_id") or req.wc_id
    if not parent_id:
        raise HTTPException(400, "No se pudo resolver el producto en WooCommerce.")
    ok = await woocommerce.eliminar_imagen_galeria(int(parent_id), req.image_id)
    if not ok:
        raise HTTPException(502, "No se pudo eliminar la imagen en WooCommerce.")
    return {"ok": True, "image_id": req.image_id}


@router.post("/{sku}/agregar")
async def agregar(sku: str, req: AgregarReq):
    """Sube imágenes nuevas (base64) a WP Media y las agrega a la galería del producto."""
    if not req.imagenes:
        raise HTTPException(400, "No se enviaron imágenes.")
    try:
        g = await woocommerce.galeria_producto(req.wc_id, sku)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"No se pudo leer el producto: {exc}")
    parent_id = (g or {}).get("parent_id") or req.wc_id
    if not parent_id:
        raise HTTPException(400, "No se pudo resolver el producto en WooCommerce.")

    import base64
    media_ids: list[int] = []
    for i, im in enumerate(req.imagenes):
        try:
            data = base64.b64decode(im.data_b64)
        except Exception:  # noqa: BLE001
            continue
        if not data:
            continue
        nombre = (im.filename or f"img{i + 1}").rsplit(".", 1)[0][:60]
        subida = await woocommerce.subir_imagen_wp(f"{sku}-{nombre}", data, im.mime or "image/jpeg")
        if subida:
            media_ids.append(subida[0])
    if not media_ids:
        raise HTTPException(502, "No se pudo subir ninguna imagen a WordPress.")
    imagenes = await woocommerce.agregar_imagenes_galeria(int(parent_id), media_ids)
    return {"ok": True, "agregadas": len(media_ids), "imagenes": imagenes}
