"""
imagenes_amazon.py — Deja las imágenes "Amazon-ready" antes de publicar.

Requisitos de Amazon: HTTP/HTTPS, JPEG/TIFF/PNG/GIF-no-animado (JPEG preferido),
RGB/CMYK (RGB preferido), nítida y sin pixelar, ≥72 ppp y entre 1,000 y 10,000
píxeles en el LADO MÁS LARGO (es lo que habilita el ZOOM de Amazon).

Las imágenes de WooCommerce son mayormente 720–1024 px → la mayoría NO cumple.
Este servicio genera una versión Amazon-ready SIN tocar la galería de Woo ni la
de Mercado Libre (esas siguen igual).

  L = lado más largo de la original
  · 1000 ≤ L ≤ 10000 → se usa la ORIGINAL tal cual (no se procesa ni se sube nada)
  · 500 ≤ L < 1000   → (A) Lanczos ×2 → queda en 1000–2000 px (calidad muy buena)
  · L < 500          → (B) FALLBACK IA: Real-ESRGAN ×4 en Replicate. Es
                       super-resolución PURA: aumenta la resolución sin regenerar
                       ni alterar el producto (a diferencia de un modelo
                       generativo como Gemini). Si aún falta, se ajusta con Lanczos.
                       Si la IA falla → Lanczos igual (para cumplir el mínimo).
  · L > 10000        → se reduce a 10000

La salida siempre va en RGB + JPEG (el formato que Amazon prefiere).
El resultado se cachea en la tabla `amazon_imagenes` (por hash de la URL de
origen) para no reprocesar ni duplicar medios en cada publicación. Si la imagen
original cambia (p. ej. la editas con IA), cambia su URL → cambia el hash →
se vuelve a procesar sola.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
from typing import Any, Optional

import httpx

from config import settings
from services import db, woocommerce

log = logging.getLogger("omnicanal.imagenes_amazon")

MIN_LADO = 1000       # mínimo que exige Amazon (habilita el zoom)
MAX_LADO = 10000      # máximo que acepta Amazon
FACTOR_LANCZOS = 2    # tope de reescalado clásico sin perder calidad
JPEG_QUALITY = 90
MAX_IMAGENES = 9      # 1 principal + 8 secundarias

# Amazon acepta JPEG, TIFF, PNG y GIF NO animado — y NO acepta WEBP. Las imágenes
# de esta tienda están en .webp, así que TODO lo que no esté aquí se convierte a
# JPEG (el formato que Amazon prefiere). GIF se convierte también, por si es animado.
FORMATOS_OK = {"JPEG", "PNG", "TIFF"}

_REPLICATE_MODEL = "nightmareai/real-esrgan"  # super-resolución (no generativo)

_tabla_lista = False


# ── Cache (tabla amazon_imagenes) ──────────────────────────────────────────────
def _asegurar_tabla() -> None:
    global _tabla_lista
    if _tabla_lista:
        return
    try:
        db.execute(
            """CREATE TABLE IF NOT EXISTS amazon_imagenes (
                   src_hash    VARCHAR(64) NOT NULL PRIMARY KEY,
                   sku         VARCHAR(60),
                   src_url     TEXT,
                   amz_url     TEXT,
                   wp_media_id INT,
                   ancho       INT,
                   alto        INT,
                   metodo      VARCHAR(20),
                   created_at  DATETIME
               ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
        )
        _tabla_lista = True
    except Exception as exc:  # noqa: BLE001
        log.warning("amazon_imagenes CREATE TABLE: %s", exc)


def _hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()


def _cache_get(src_url: str) -> Optional[str]:
    _asegurar_tabla()
    try:
        row = db.fetch_one(
            "SELECT amz_url FROM amazon_imagenes WHERE src_hash=%s", (_hash(src_url),)
        )
        return (row or {}).get("amz_url") or None
    except Exception:  # noqa: BLE001
        return None


def _cache_put(sku: str, src_url: str, amz_url: str, media_id: int,
               ancho: int, alto: int, metodo: str) -> None:
    _asegurar_tabla()
    try:
        db.execute(
            """INSERT INTO amazon_imagenes
                 (src_hash, sku, src_url, amz_url, wp_media_id, ancho, alto, metodo, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
               ON DUPLICATE KEY UPDATE amz_url=VALUES(amz_url), wp_media_id=VALUES(wp_media_id),
                 ancho=VALUES(ancho), alto=VALUES(alto), metodo=VALUES(metodo)""",
            (_hash(src_url), sku, src_url, amz_url, media_id, ancho, alto, metodo),
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("amazon_imagenes cache put (ignorado): %s", exc)


# ── Descarga / medición / transformación ───────────────────────────────────────
_DL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


async def _descargar(url: str) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=40.0, follow_redirects=True) as cli:
            r = await cli.get(url, headers=_DL_HEADERS)
        return r.content if r.status_code == 200 and r.content else None
    except Exception as exc:  # noqa: BLE001
        log.warning("descarga %s: %s", url[:80], exc)
        return None


def _medir(data: bytes) -> tuple[int, int, str]:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return im.width, im.height, (im.format or "").upper()


def _a_jpeg(data: bytes, lado_destino: int) -> tuple[bytes, int, int]:
    """Reescala (Lanczos) al lado_destino en el lado más largo, RGB + JPEG."""
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")  # descarta alpha/CMYK/paleta → RGB (lo que prefiere Amazon)
        w, h = im.size
        largo = max(w, h)
        if largo != lado_destino and largo > 0:
            escala = lado_destino / largo
            im = im.resize((max(1, round(w * escala)), max(1, round(h * escala))),
                           Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        return buf.getvalue(), im.width, im.height


# ── Fallback IA: Real-ESRGAN (Replicate) ──────────────────────────────────────
async def _upscale_ia(data: bytes, mime: str = "image/jpeg", escala: int = 4) -> Optional[bytes]:
    """
    Super-resolución con Real-ESRGAN (Replicate). NO es generativo: sube la
    resolución sin reinventar el contenido, así que el producto no cambia.
    """
    if not settings.replicate_api_key:
        return None
    data_uri = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    headers = {
        "Authorization": f"Bearer {settings.replicate_api_key}",
        "Content-Type": "application/json",
        "Prefer": "wait",  # respuesta síncrona si alcanza
    }
    try:
        async with httpx.AsyncClient(timeout=180.0) as cli:
            r = await cli.post(
                f"https://api.replicate.com/v1/models/{_REPLICATE_MODEL}/predictions",
                headers=headers,
                json={"input": {"image": data_uri, "scale": escala, "face_enhance": False}},
            )
            if r.status_code not in (200, 201):
                log.warning("Replicate HTTP %d: %s", r.status_code, r.text[:200])
                return None
            pred = r.json()

            # Si `Prefer: wait` no alcanzó, sondear hasta terminar.
            intentos = 0
            while pred.get("status") in ("starting", "processing") and intentos < 40:
                await asyncio.sleep(3)
                get_url = (pred.get("urls") or {}).get("get")
                if not get_url:
                    break
                rr = await cli.get(get_url, headers={"Authorization": headers["Authorization"]})
                if rr.status_code != 200:
                    break
                pred = rr.json()
                intentos += 1

            if pred.get("status") != "succeeded":
                log.warning("Replicate status=%s error=%s", pred.get("status"),
                            str(pred.get("error"))[:120])
                return None

            out = pred.get("output")
            url = out[0] if isinstance(out, list) and out else out
            if not isinstance(url, str):
                return None
            ri = await cli.get(url, timeout=90.0)
            return ri.content if ri.status_code == 200 else None
    except Exception as exc:  # noqa: BLE001
        log.warning("Real-ESRGAN falló: %s", exc)
        return None


# ── Orquestador ────────────────────────────────────────────────────────────────
async def _una(sku: str, idx: int, url: str) -> tuple[str, Optional[str]]:
    """Devuelve (url_para_amazon, aviso|None)."""
    cacheada = _cache_get(url)
    if cacheada:
        return cacheada, None

    data = await _descargar(url)
    if not data:
        return url, f"Imagen {idx + 1}: no se pudo descargar; se envía la original."

    try:
        w, h, fmt = await asyncio.to_thread(_medir, data)
    except Exception as exc:  # noqa: BLE001
        log.warning("medir imagen %s: %s", url[:60], exc)
        return url, f"Imagen {idx + 1}: no se pudo leer; se envía la original."

    largo = max(w, h)
    tamano_ok = MIN_LADO <= largo <= MAX_LADO
    formato_ok = fmt in FORMATOS_OK

    # Ya cumple TODO → se usa tal cual (no se procesa ni se sube nada).
    if tamano_ok and formato_ok:
        return url, None

    metodo = "lanczos"
    aviso: Optional[str] = None

    if largo > MAX_LADO:
        destino = MAX_LADO
    elif tamano_ok:
        # El tamaño ya cumple; lo que falla es el FORMATO (p. ej. WEBP, que Amazon
        # NO acepta). Se convierte a JPEG CONSERVANDO la resolución (sin reescalar).
        destino = largo
        metodo = "convert"
        aviso = (f"Imagen {idx + 1}: formato {fmt} (Amazon no lo acepta) → "
                 f"convertida a JPEG {w}x{h}.")
    elif largo >= MIN_LADO / FACTOR_LANCZOS:   # ≥500 → Lanczos ×2 alcanza los 1000
        destino = min(largo * FACTOR_LANCZOS, MAX_LADO)
        aviso = (f"Imagen {idx + 1}: {fmt} {w}x{h} → JPEG {int(destino)}px "
                 f"(Amazon exige ≥1000 px para el zoom).")
    else:
        # (B) Demasiado chica para Lanczos: super-resolución con IA.
        mejorada = await _upscale_ia(data, "image/jpeg" if fmt == "JPEG" else "image/png")
        if mejorada:
            data = mejorada
            metodo = "real-esrgan"
            try:
                w, h, _ = await asyncio.to_thread(_medir, data)
                largo = max(w, h)
            except Exception:  # noqa: BLE001
                pass
            aviso = f"Imagen {idx + 1}: era muy pequeña ({largo}px) — reescalada con IA (Real-ESRGAN)."
        else:
            metodo = "lanczos-forzado"
            aviso = (f"Imagen {idx + 1}: muy pequeña y la IA no estuvo disponible — "
                     f"se reescaló con Lanczos (puede verse menos nítida).")
        destino = max(MIN_LADO, min(largo, MAX_LADO)) if largo >= MIN_LADO else MIN_LADO

    try:
        jpeg, nw, nh = await asyncio.to_thread(_a_jpeg, data, int(destino))
    except Exception as exc:  # noqa: BLE001
        log.warning("convertir imagen %s: %s", url[:60], exc)
        return url, f"Imagen {idx + 1}: no se pudo optimizar; se envía la original."

    subida = await woocommerce.subir_imagen_wp(f"{sku}-amz-{idx + 1}", jpeg, "image/jpeg")
    if not subida:
        return url, f"Imagen {idx + 1}: no se pudo subir la versión optimizada; se envía la original."

    media_id, nueva_url = subida
    _cache_put(sku, url, nueva_url, media_id, nw, nh, metodo)
    return nueva_url, aviso


async def preparar_para_amazon(sku: str, urls: list[str]) -> tuple[list[str], list[str]]:
    """
    Devuelve (urls_listas_para_amazon, avisos). Procesa en paralelo (máx 3 a la vez).
    Las que ya cumplen se devuelven sin tocar.
    """
    urls = [u for u in (urls or []) if u][:MAX_IMAGENES]
    if not urls:
        return [], []

    sem = asyncio.Semaphore(3)

    async def _wrap(i: int, u: str):
        async with sem:
            return await _una(sku, i, u)

    res = await asyncio.gather(*[_wrap(i, u) for i, u in enumerate(urls)],
                               return_exceptions=True)
    finales: list[str] = []
    avisos: list[str] = []
    for i, r in enumerate(res):
        if isinstance(r, Exception) or not isinstance(r, tuple):
            log.warning("preparar imagen %d de %s: %s", i + 1, sku, r)
            finales.append(urls[i])
            continue
        u, aviso = r
        finales.append(u)
        if aviso:
            avisos.append(aviso)
    return finales, avisos


async def diagnostico(urls: list[str]) -> dict[str, Any]:
    """
    Solo MIDE (no sube nada): cuántas imágenes cumplen el mínimo de Amazon.
    Se usa en la vista previa para avisar sin costo ni subir medios.
    """
    urls = [u for u in (urls or []) if u][:MAX_IMAGENES]
    if not urls:
        return {"total": 0, "cumplen": 0, "chicas": 0, "detalle": []}

    sem = asyncio.Semaphore(4)

    async def _dim(u: str) -> Optional[tuple[int, int, str]]:
        async with sem:
            data = await _descargar(u)
            if not data:
                return None
            try:
                return await asyncio.to_thread(_medir, data)
            except Exception:  # noqa: BLE001
                return None

    res = await asyncio.gather(*[_dim(u) for u in urls], return_exceptions=True)
    ok, chicas, formato_malo, detalle = 0, 0, 0, []
    for r in res:
        if not isinstance(r, tuple):
            continue
        w, h, fmt = r
        detalle.append(f"{w}x{h} {fmt}")
        if max(w, h) >= MIN_LADO:
            ok += 1
        else:
            chicas += 1
        if fmt not in FORMATOS_OK:
            formato_malo += 1
    return {"total": len(urls), "cumplen": ok, "chicas": chicas,
            "formato_malo": formato_malo, "detalle": detalle}
