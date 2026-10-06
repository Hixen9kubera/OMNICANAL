"""
imagenes.py — Las fotos de la página, empaquetadas en HOJAS (sprites).

POR QUÉ NO SE ENLAZAN DIRECTO: la página se publica en un visor que bloquea toda
imagen que no viaje con ella. 20,000 miniaturas sueltas tampoco son opción, así
que se pegan en hojas de 20 × 20 mosaicos de 72 px (`img/hNNN.jpg`) y la página
recorta cada foto por posición.

De dónde sale cada foto:
  · Odoo        `image_128` del producto, ya bajada por `f_odoo.py`
  · canales     la URL pública que cada marketplace devolvió en su listado
                (un GET sin credenciales a su CDN)

Dos fotos idénticas ocupan UN mosaico (las variantes de color suelen compartirla).
Lo que no se pudo bajar queda sin foto — nunca se rellena con la de otro producto.
"""
from __future__ import annotations

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from comun import Red, aviso, escribir_json, leer_json

LADO = 72
COLS = 20
POR_HOJA = COLS * COLS
CANALES = ("ml", "amazon", "tiktok", "temu", "walmart")


def _mosaico(datos: bytes) -> bytes | None:
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(datos))
        im.load()
    except Exception:  # noqa: BLE001
        return None
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        fondo = Image.new("RGB", im.size, (255, 255, 255))
        fondo.paste(im, mask=im.split()[-1])
        im = fondo
    else:
        im = im.convert("RGB")
    im.thumbnail((LADO, LADO), Image.LANCZOS)
    lienzo = Image.new("RGB", (LADO, LADO), (255, 255, 255))
    lienzo.paste(im, ((LADO - im.width) // 2, (LADO - im.height) // 2))
    salida = io.BytesIO()
    lienzo.save(salida, "JPEG", quality=76, optimize=True)
    return salida.getvalue()


def _chica(url: str) -> str:
    """La versión pequeña de la misma foto, cuando el CDN la ofrece."""
    if "m.media-amazon.com/images/I/" in url and "._" not in url.rsplit("/", 1)[-1]:
        base, _, ext = url.rpartition(".")
        return f"{base}._SL160_.{ext}"
    return url


def construir(salida: Path) -> dict[str, Any]:
    from PIL import Image

    datos = salida / "datos"
    cache_odoo = salida / "cache" / "img" / "odoo"
    cache_url = salida / "cache" / "img" / "url"
    cache_url.mkdir(parents=True, exist_ok=True)

    # 1 · Qué URLs de canal hay que tener
    urls: list[str] = []
    for canal in CANALES:
        doc = leer_json(datos / f"{canal}.json") or {}
        urls.extend(f["imagen"] for f in doc.get("filas") or [] if f.get("imagen"))
    urls = list(dict.fromkeys(urls))

    def _ruta(url: str) -> Path:
        return cache_url / (hashlib.sha1(url.encode()).hexdigest() + ".jpg")

    fallidas = set((leer_json(cache_url / "_fallidas.json") or []))
    pendientes = [u for u in urls if not _ruta(u).exists() and u not in fallidas]
    red = Red(rps=40.0, timeout=25.0)

    def _bajar(url: str) -> tuple[str, bool]:
        try:
            r = red.get(_chica(url), imagen=True, reintentos=2)
            mini = _mosaico(r.content) if r.status_code == 200 else None
        except Exception:  # noqa: BLE001
            mini = None
        if mini:
            _ruta(url).write_bytes(mini)
        return url, bool(mini)

    if pendientes:
        aviso(f"imágenes: bajando {len(pendientes)} miniaturas de los canales")
        with ThreadPoolExecutor(max_workers=16) as pool:
            for n, (url, ok) in enumerate(pool.map(_bajar, pendientes), 1):
                if not ok:
                    fallidas.add(url)
                if n % 1000 == 0 or n == len(pendientes):
                    aviso(f"imágenes: {n}/{len(pendientes)}")
        escribir_json(cache_url / "_fallidas.json", sorted(fallidas))

    # 2 · Mosaicos únicos, en orden estable (Odoo primero, por id)
    por_huella: dict[str, int] = {}
    mosaicos: list[bytes] = []

    def _indice(ruta: Path) -> int | None:
        try:
            b = ruta.read_bytes()
        except OSError:
            return None
        h = hashlib.md5(b).hexdigest()
        if h not in por_huella:
            por_huella[h] = len(mosaicos)
            mosaicos.append(b)
        return por_huella[h]

    mapa_odoo: dict[str, int] = {}
    odoo = leer_json(datos / "odoo.json") or {}
    for f in odoo.get("filas") or []:
        if f.get("foto"):
            i = _indice(cache_odoo / f"{f['id']}.jpg")
            if i is not None:
                mapa_odoo[str(f["id"])] = i
    mapa_url: dict[str, int] = {}
    for u in urls:
        i = _indice(_ruta(u))
        if i is not None:
            mapa_url[u] = i

    # 3 · Hojas
    carpeta = salida / "img"
    carpeta.mkdir(parents=True, exist_ok=True)
    for viejo in carpeta.glob("h*.jpg"):
        viejo.unlink()
    hojas = (len(mosaicos) + POR_HOJA - 1) // POR_HOJA
    peso = 0
    for h in range(hojas):
        trozo = mosaicos[h * POR_HOJA:(h + 1) * POR_HOJA]
        filas = (len(trozo) + COLS - 1) // COLS
        hoja = Image.new("RGB", (COLS * LADO, filas * LADO), (255, 255, 255))
        for n, b in enumerate(trozo):
            hoja.paste(Image.open(io.BytesIO(b)), ((n % COLS) * LADO, (n // COLS) * LADO))
        destino = carpeta / f"h{h:03d}.jpg"
        hoja.save(destino, "JPEG", quality=76, optimize=True)
        peso += destino.stat().st_size
    doc = {"lado": LADO, "cols": COLS, "por_hoja": POR_HOJA, "hojas": hojas,
           "mosaicos": len(mosaicos), "odoo": mapa_odoo, "url": mapa_url}
    escribir_json(datos / "imagenes.json", doc)
    aviso(f"imágenes: {len(mosaicos)} mosaicos únicos en {hojas} hojas ({peso / 1e6:.1f} MB) · "
          f"{len(mapa_odoo)} de Odoo, {len(mapa_url)} de canales, {len(fallidas)} no bajaron")
    return {"hojas": hojas, "mosaicos": len(mosaicos), "mb": round(peso / 1e6, 1),
            "fotos_odoo": len(mapa_odoo), "fotos_canales": len(mapa_url),
            "no_bajaron": len(fallidas)}
