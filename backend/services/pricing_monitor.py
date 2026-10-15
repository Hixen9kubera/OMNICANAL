"""
pricing_monitor.py — La foto del «Monitor de precios FULL» que sirve la pestaña
PRICING (v0.650.0, 15-oct-2026). Reemplaza en la barra a Radar.

QUÉ ES
    El monitor que armó José como artifact (14-oct-2026): las publicaciones FULL
    de BEKURA y SANCORFASHION con su precio (`sale_price` de ML), el precio
    normal, el stock en FULL, las ventas por día de kubera, las visitas por día
    de ML, la competencia revisada con fotos y la categoría revisada. Aquí NO se
    recalcula nada de eso: se sirve la foto tal cual la dejó
    `scripts/pricing_preparar_foto.py`.

    Las reglas de «Qué hacer» viven en el frontend
    (`frontend/components/pricing/reglas.ts`), como en el artifact: sus umbrales
    se editan en la pantalla y la tabla se recalcula al instante.

DE DÓNDE SALE LA FOTO
    1. `PRICING_FOTO_DIR` (por omisión `backend/data/pricing`):
       `monitor.json.gz` + `img/p*.jpg` (nuestras) + `img/s*.jpg` (rivales).
    2. Si ahí no hay `monitor.json.gz`: el repo PRIVADO `PRICING_GITHUB_REPO`
       (`PRICING_GITHUB_RUTA`, rama `PRICING_GITHUB_REF`) con
       `PRICING_GITHUB_TOKEN`. Los datos de venta por publicación no deben vivir
       en un repo público; esta es la puerta para tenerlos fuera de OMNICANAL.

SOLO LECTURA. No toca kubera, Woo, ML ni Odoo. Los archivos locales se leen en
`asyncio.to_thread` (regla 11: el disco también espera) y GitHub con httpx
asíncrono.

CACHÉ: 10 min en memoria, por proceso. El JSON se guarda COMPRIMIDO (≈0.9 MB):
el router lo entrega con `Content-Encoding: gzip` sin volver a comprimirlo.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import re
import time
from pathlib import Path

import httpx

from config import settings

log = logging.getLogger("omnicanal.pricing")

TTL_S = 600
ARCHIVO_DATOS = "monitor.json.gz"
# Las hojas de miniaturas: `p0`… (nuestras publicaciones) y `s0`… (rivales).
# Cualquier otro nombre se rechaza ANTES de armar una ruta o una URL.
NOMBRE_HOJA = re.compile(r"^[ps]\d{1,3}$")
_BACKEND = Path(__file__).resolve().parent.parent

_cache: dict[str, tuple[float, bytes]] = {}       # hojas de miniaturas
_foto: tuple[float, bytes, dict] | None = None     # (cuándo, JSON comprimido, meta)
_candado = asyncio.Lock()


class SinFoto(Exception):
    """No hay foto en ningún lado: ni en la carpeta ni en GitHub."""


class FalloFuente(Exception):
    """La foto existe en GitHub pero no se pudo leer (red, token, permiso)."""


def _carpeta() -> Path:
    ruta = Path(settings.pricing_foto_dir)
    return ruta if ruta.is_absolute() else _BACKEND / ruta


def origen() -> str:
    """De dónde saldría la foto ahora mismo: 'local', 'github' o 'ninguno'."""
    if (_carpeta() / ARCHIVO_DATOS).is_file():
        return "local"
    return "github" if settings.pricing_github_token.strip() else "ninguno"


def validar(gz: bytes) -> dict:
    """PURA. Abre la foto y devuelve su `meta`; si no es una foto, ValueError."""
    datos = json.loads(gzip.decompress(gz))
    if not isinstance(datos, dict) or not all(k in datos for k in ("meta", "pubs", "riv")):
        raise ValueError("la foto no trae meta, pubs y riv")
    if not isinstance(datos["pubs"], list) or not isinstance(datos["riv"], dict):
        raise ValueError("pubs debe ser lista y riv un objeto")
    return datos["meta"]


async def _github(relativa: str) -> bytes:
    repo = settings.pricing_github_repo.strip().strip("/")
    ruta = settings.pricing_github_ruta.strip().strip("/")
    url = f"https://api.github.com/repos/{repo}/contents/{ruta}/{relativa}"
    cab = {"Authorization": f"Bearer {settings.pricing_github_token.strip()}",
           "Accept": "application/vnd.github.raw+json",
           "X-GitHub-Api-Version": "2022-11-28"}
    try:
        async with httpx.AsyncClient(timeout=30) as cli:
            r = await cli.get(url, params={"ref": settings.pricing_github_ref}, headers=cab)
    except httpx.HTTPError as exc:
        raise FalloFuente(f"GitHub no contestó: {type(exc).__name__}") from exc
    if r.status_code == 404:
        raise SinFoto(f"{repo}/{ruta}/{relativa} no existe (o el token no lo ve)")
    if r.status_code != 200:
        raise FalloFuente(f"GitHub contestó {r.status_code} al pedir {relativa}")
    return r.content


def _leer_local(relativa: str) -> bytes | None:
    """BLOQUEANTE (disco). None si la carpeta no tiene foto."""
    if origen() != "local":
        return None
    archivo = _carpeta() / relativa
    if not archivo.is_file():
        raise SinFoto(f"falta {relativa} en {_carpeta()}")
    return archivo.read_bytes()


async def _leer(relativa: str) -> bytes:
    """Un archivo de la foto: de la carpeta si la hay; si no, de GitHub."""
    local = await asyncio.to_thread(_leer_local, relativa)
    if local is not None:
        return local
    if settings.pricing_github_token.strip():
        return await _github(relativa)
    raise SinFoto("No hay foto del monitor: ni en PRICING_FOTO_DIR ni PRICING_GITHUB_TOKEN.")


async def _con_cache(llave: str, leer) -> bytes:
    ahora = time.monotonic()
    hit = _cache.get(llave)
    if hit and ahora - hit[0] <= TTL_S:
        return hit[1]
    dato = await leer()
    _cache[llave] = (time.monotonic(), dato)
    return dato


async def foto() -> tuple[bytes, dict]:
    """El JSON comprimido de la foto y su `meta` (validada UNA vez por lectura)."""
    global _foto
    async with _candado:   # dos pestañas que abren a la vez no la bajan dos veces
        ahora = time.monotonic()
        if _foto and ahora - _foto[0] <= TTL_S:
            return _foto[1], _foto[2]
        gz = await _leer(ARCHIVO_DATOS)
        try:
            meta = await asyncio.to_thread(validar, gz)
        except (ValueError, OSError) as exc:   # JSON roto o gzip roto
            raise FalloFuente(f"La foto no se pudo abrir: {exc}") from exc
        _foto = (time.monotonic(), gz, meta)
        return gz, meta


async def hoja(nombre: str) -> bytes:
    """Una hoja de miniaturas (`p0`, `s12`…). Nombre inválido → SinFoto."""
    if not NOMBRE_HOJA.match(nombre or ""):
        raise SinFoto(f"hoja desconocida: {nombre!r}")
    relativa = f"img/{nombre}.jpg"
    return await _con_cache(relativa, lambda: _leer(relativa))


def olvidar() -> None:
    """Vacía la caché (pruebas, o tras dejar una foto nueva en la carpeta)."""
    global _foto
    _foto = None
    _cache.clear()
