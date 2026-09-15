"""
imagenes.py — Editor de imágenes de producto (galería WooCommerce + IA).

  GET  /api/imagenes/{sku}            → galería (con id/posición) + progreso en curso
  POST /api/imagenes/{sku}/procesar   → edita con IA las imágenes con flags (background)
  GET  /api/imagenes/{sku}/progreso   → estado por imagen (para el label de carga)
  POST /api/imagenes/{sku}/eliminar   → quita una imagen de la galería del producto
  POST /api/imagenes/{sku}/agregar    → sube imágenes y las agrega a la galería
  POST /api/imagenes/{sku}/principal  → (variante) esa foto propia pasa a principal
  POST /api/imagenes/{sku}/reordenar  → (variante) orden completo de las propias
  POST /api/imagenes/{sku}/adoptar    → (variante) una heredada pasa a ser propia

GALERÍA POR VARIANTE (`settings.galeria_variante`, env GALERIA_VARIANTE)
────────────────────────────────────────────────────────────────────────
Apagada: todo responde EXACTAMENTE como antes (la galería del padre) y las tres
rutas nuevas dan 409. Encendida y con un SKU que es VARIACIÓN, las rutas operan
sobre las fotos PROPIAS de esa variación (`services/imagenes_variante.py`):
nunca `PUT /products/{padre}` con `images[]`, nunca el padre ni las hermanas.
Toda escritura devuelve la galería RELEÍDA con la forma del GET (+ `ok`); un
`ok: false` con 200 significa "WooCommerce no guardó lo pedido" y `aviso` dice
qué quedó. Errores del pedido → 400; sin base de WordPress → 503 (sin ella no
se sabe qué fotos son de la variante, y caer a la galería del padre editaría la
de toda la familia).

MARCADOR `variante: true` (A5) en el cuerpo de eliminar / agregar / procesar
─────────────────────────────────────────────────────────────────────────────
El Estudio lo manda cuando la galería que tiene abierta está en modo variante.
Sin él, esas tres rutas deciden solas (flag + resolución) y, si alguien apaga
GALERIA_VARIANTE con el Estudio abierto sobre una variante, el siguiente clic
en "quitar" editaba la galería del PADRE —la de toda la familia— creyendo que
quitaba una foto de la variante. Con el marcador:
  • flag apagado → 409 con el mismo texto de las rutas nuevas, y no se toca nada;
  • flag encendido y el SKU no es variación → 400;
  • sin base de WordPress → 503 (nunca cae a la rama del padre).
Sin marcador, todo como antes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config import settings
from services import imagenes_editor as editor, imagenes_variante, woocommerce

log = logging.getLogger("omnicanal.routers.imagenes")
router = APIRouter(prefix="/api/imagenes", tags=["imagenes"])

_APAGADA = "La galería por variante está apagada (GALERIA_VARIANTE)"


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
    variante: bool = False  # A5: la pantalla está en modo variante


class EliminarReq(BaseModel):
    wc_id: int | None = None
    image_id: int
    variante: bool = False  # A5


class ImagenNueva(BaseModel):
    filename: str = "imagen"
    mime: str = "image/jpeg"
    data_b64: str  # base64 (sin el prefijo data:...;base64,)


class AgregarReq(BaseModel):
    wc_id: int | None = None
    imagenes: list[ImagenNueva] = []
    variante: bool = False  # A5


class ImagenVarianteReq(BaseModel):
    wc_id: int | None = None
    image_id: int


class ReordenarReq(BaseModel):
    wc_id: int | None = None
    ids: list[int] = []


# ── Galería por variante: resolución y errores ───────────────────────────────

async def _variante_o_none(sku: str, wc_id: int | None) -> tuple[int, int] | None:
    """
    Para las rutas EXISTENTES que escriben. (wc_id, padre) si el flag está
    encendido y el SKU es una variación; None → la ruta sigue como siempre.

    Sin base de WordPress no se puede saber qué fotos son de la variante: si la
    REST dice que es variación, 503 en vez de editar la galería del padre.
    """
    if not settings.galeria_variante:
        return None
    try:
        return await asyncio.to_thread(imagenes_variante.resolver, sku, wc_id)
    except imagenes_variante.SinBaseWP:
        try:
            g = await woocommerce.galeria_producto(wc_id, sku)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"No se pudo leer el producto: {exc}")
        if (g or {}).get("es_variacion"):
            raise HTTPException(503, "Sin la base de WordPress no se puede editar la "
                                     "galería de una variante (se editaría la del padre).")
        return None


async def _variante_obligatoria(sku: str, wc_id: int | None) -> tuple[int, int]:
    """
    Para las rutas NUEVAS, y para eliminar/agregar/procesar cuando llega el
    marcador `variante: true` (A5): 409 con el flag apagado, 400 si no es
    variación, 503 sin base de WordPress. Nunca devuelve None.
    """
    if not settings.galeria_variante:
        raise HTTPException(409, _APAGADA)
    try:
        v = await asyncio.to_thread(imagenes_variante.resolver, sku, wc_id)
    except imagenes_variante.SinBaseWP as exc:
        raise HTTPException(503, f"No se puede editar la galería de la variante: {exc}")
    if not v:
        raise HTTPException(400, f"{sku} no es una variación: sus fotos se editan en la "
                                 f"galería del producto.")
    return v


async def _escribir(sku: str, op: Any, *args: Any) -> dict[str, Any]:
    """Corre una operación de `imagenes_variante` y la viste con la forma del GET."""
    try:
        res = await op(*args)
    except imagenes_variante.GaleriaInvalida as exc:
        raise HTTPException(400, str(exc))
    except imagenes_variante.SinBaseWP as exc:
        raise HTTPException(503, str(exc))
    res["sku"] = sku
    res["progreso"] = editor.progreso(sku)
    return res


# ── Rutas ────────────────────────────────────────────────────────────────────

async def galeria(sku: str, wc_id: int | None = Query(None)):
    """
    Galería completa del producto (portada + imágenes con id/posición).

    Con GALERIA_VARIANTE y un SKU que es variación: la vista de
    `imagenes_variante.vista` (solo fotos propias + heredadas + regla + aviso).
    Si la base de WordPress no responde, la de siempre: leer no daña, y las
    escrituras de variante contestan 503 por su cuenta.
    """
    if settings.galeria_variante:
        v = None
        try:
            var = await asyncio.to_thread(imagenes_variante.resolver, sku, wc_id)
            if var:
                v = await asyncio.to_thread(imagenes_variante.vista, var[0])
        except imagenes_variante.SinBaseWP as exc:
            log.warning("galeria %s: sin base de WordPress (%s); galería del padre", sku, exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("galeria variante %s: %s", sku, exc)
            raise HTTPException(502, f"No se pudo leer la galería de la variante: {exc}")
        if v:
            v["sku"] = sku
            v["progreso"] = editor.progreso(sku)
            return v
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


@router.post("/{sku:path}/procesar")
async def procesar(sku: str, req: ProcesarReq):
    """Lanza la edición con IA (según flags) en segundo plano y responde de inmediato."""
    con_flags = [
        i for i in req.imagenes
        if i.quitar_fondo or i.traducir_texto or i.quitar_logos or i.cambiar_modelo
    ]
    if not con_flags:
        raise HTTPException(400, "Ninguna imagen tiene flags seleccionados.")
    if req.variante:
        # A5: 409/400/503 ANTES de gastar Gemini. `iniciar` vuelve a resolver
        # (lectura barata) y toma la rama de variante con el mismo resultado.
        await _variante_obligatoria(sku, req.wc_id)
    try:
        return await editor.iniciar(sku, req.wc_id, [i.model_dump() for i in con_flags])
    except imagenes_variante.GaleriaInvalida as exc:  # solo la rama de variante lanza
        raise HTTPException(400, str(exc))
    except imagenes_variante.SinBaseWP as exc:
        raise HTTPException(503, str(exc))


@router.get("/{sku:path}/progreso")
async def progreso(sku: str):
    j = editor.progreso(sku)
    if not j:
        return {"sku": sku, "estado": "sin_datos", "total": 0, "procesadas": 0,
                "paso_global": "", "imagenes": []}
    return j


@router.post("/{sku:path}/eliminar")
async def eliminar(sku: str, req: EliminarReq):
    """Quita una imagen de la galería (resuelve el padre si es variación)."""
    var = await (_variante_obligatoria(sku, req.wc_id) if req.variante
                 else _variante_o_none(sku, req.wc_id))
    if var:
        res = await _escribir(sku, imagenes_variante.quitar, var[0], req.image_id)
        res["image_id"] = req.image_id
        return res
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


async def _subir_a_media(sku: str, imagenes: list[ImagenNueva]) -> list[int]:
    """Sube cada imagen (base64) a WP Media; devuelve los ids que sí subieron."""
    import base64
    media_ids: list[int] = []
    for i, im in enumerate(imagenes):
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
    return media_ids


@router.post("/{sku:path}/agregar")
async def agregar(sku: str, req: AgregarReq):
    """Sube imágenes nuevas (base64) a WP Media y las agrega a la galería del producto."""
    if not req.imagenes:
        raise HTTPException(400, "No se enviaron imágenes.")
    # La variante se resuelve ANTES de subir: un 400/409/503 no deja adjuntos huérfanos.
    var = await (_variante_obligatoria(sku, req.wc_id) if req.variante
                 else _variante_o_none(sku, req.wc_id))
    if var:
        media_ids = await _subir_a_media(sku, req.imagenes)
        if not media_ids:
            raise HTTPException(502, "No se pudo subir ninguna imagen a WordPress.")
        res = await _escribir(sku, imagenes_variante.agregar, var[0], media_ids)
        res["agregadas"] = len(media_ids)
        return res
    try:
        g = await woocommerce.galeria_producto(req.wc_id, sku)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"No se pudo leer el producto: {exc}")
    parent_id = (g or {}).get("parent_id") or req.wc_id
    if not parent_id:
        raise HTTPException(400, "No se pudo resolver el producto en WooCommerce.")

    media_ids = await _subir_a_media(sku, req.imagenes)
    if not media_ids:
        raise HTTPException(502, "No se pudo subir ninguna imagen a WordPress.")
    imagenes = await woocommerce.agregar_imagenes_galeria(int(parent_id), media_ids)
    return {"ok": True, "agregadas": len(media_ids), "imagenes": imagenes}


@router.post("/{sku:path}/principal")
async def principal(sku: str, req: ImagenVarianteReq):
    """(Variante) Esa foto propia pasa a principal; la anterior, al frente de la galería."""
    var = await _variante_obligatoria(sku, req.wc_id)
    return await _escribir(sku, imagenes_variante.hacer_principal, var[0], req.image_id)


@router.post("/{sku:path}/reordenar")
async def reordenar(sku: str, req: ReordenarReq):
    """(Variante) Orden completo de las fotos propias; el primero queda de principal."""
    var = await _variante_obligatoria(sku, req.wc_id)
    return await _escribir(sku, imagenes_variante.reordenar, var[0], req.ids)


@router.post("/{sku:path}/adoptar")
async def adoptar(sku: str, req: ImagenVarianteReq):
    """(Variante) Una foto heredada del padre pasa a la galería propia (mismo adjunto)."""
    var = await _variante_obligatoria(sku, req.wc_id)
    return await _escribir(sku, imagenes_variante.adoptar, var[0], req.image_id)


# -- Registro DIFERIDO de la ruta comodín -------------------------------------
# `{sku}` pasó a `{sku:path}` para que los 293 SKUs con diagonal
# (`CALZ-0194-BLN/AZL-40`) dejen de dar 404: el parámetro normal no puede
# abarcar un `/`, y el servidor decodifica el `%2F` ANTES de enrutar, así que
# tampoco servía escaparlo desde el frontend.
#
# El precio de `:path` es que compila a `.*`, que es GOLOSO. Registrada en su
# lugar original, esta comodín se tragaría a sus hermanas GET de más abajo
# resolviéndolas como un SKU llamado "TEC-0935-ROS/progreso"
# — y no fallaría: devolvería 200 con la respuesta EQUIVOCADA y NINGÚN
# error en los logs. Starlette devuelve la PRIMERA ruta que casa entera,
# así que la comodín se declara arriba (donde se lee) y se REGISTRA aquí,
# la última.
router.get("/{sku:path}")(galeria)
