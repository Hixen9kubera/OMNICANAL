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
    global _foto, _hechos
    _foto = None
    _hechos = None
    _cache.clear()


# ── HECHO (v0.654.0): los precios que sí se movieron en ML ───────────────────
#
# `hechos.json` (en la misma carpeta que la foto) lo arma
# `scripts/pricing_hechos.py` con la bitácora de lo aplicado: precio antes,
# precio nuevo, precio normal, desde cuándo. Aquí se le suma lo que pasó
# DESPUÉS del cambio, leído de kubera: piezas pagadas desde ese momento y el
# precio y stock que la publicación tiene hoy (`channel.listings`, sync de
# 15 min). Solo SELECT; si kubera no está, se entregan los movimientos sin eso.

ARCHIVO_HECHOS = "hechos.json"
# La promoción que tiene HOY cada publicación (columna «Promoción hoy»): foto con
# su hora que deja `scripts/pricing_promos.py` (solo GET a ML).
ARCHIVO_PROMOS = "promos.json"
TTL_HECHOS_S = 300
_hechos: tuple[float, dict] | None = None
_candado_hechos = asyncio.Lock()


def _leer_hechos() -> list[dict]:
    """BLOQUEANTE (disco). Sin archivo = ningún precio movido."""
    archivo = _carpeta() / ARCHIVO_HECHOS
    if not archivo.is_file():
        return []
    datos = json.loads(archivo.read_text(encoding="utf-8"))
    return [h for h in (datos.get("hechos") or []) if isinstance(h, dict) and h.get("id") and h.get("desde")]


def _leer_promos() -> dict:
    """BLOQUEANTE (disco). {"leido": …, "promos": {id: …}}; sin archivo, vacío."""
    archivo = _carpeta() / ARCHIVO_PROMOS
    if not archivo.is_file():
        return {"leido": None, "promos": {}}
    datos = json.loads(archivo.read_text(encoding="utf-8"))
    return {"leido": datos.get("leido"), "promos": datos.get("promos") or {}}


def _despues(hechos: list[dict]) -> dict[str, dict]:
    """BLOQUEANTE (kubera, solo SELECT). Por publicación: lo vendido desde su cambio y su precio de hoy."""
    from services import supabase_db as sdb

    if not hechos or not sdb.disponible():
        return {}
    ids = [h["id"] for h in hechos]
    desdes = [h["desde"] for h in hechos]
    ventas = sdb.fetch_all(
        """
        select h.item_id, sum(oi.cantidad)::int as pzs, count(distinct o.external_order_id)::int as ordenes,
               round(sum(oi.cantidad * oi.precio_unitario)::numeric, 2) as pesos
          from unnest(%(ids)s::text[], %(desdes)s::timestamptz[]) as h(item_id, desde)
          join channel.order_items oi on oi.canal = 'mercado_libre' and oi.item_id = h.item_id
          join channel.orders o on o.canal = oi.canal and o.cuenta = oi.cuenta and o.external_order_id = oi.external_order_id
         where o.creado_at >= h.desde and lower(coalesce(o.estado_canal, '')) <> 'cancelled'
         group by h.item_id
        """,
        {"ids": ids, "desdes": desdes},
    )
    hoy = sdb.fetch_all(
        """
        select distinct on (l.listing_id) l.listing_id, l.price_sale, l.stock_full, l.situacion, l.updated_at
          from channel.listings l
         where l.canal = 'mercado_libre' and l.listing_id = any(%(ids)s)
         order by l.listing_id, l.updated_at desc nulls last
        """,
        {"ids": ids},
    )
    out: dict[str, dict] = {i: {"pzs": 0, "ordenes": 0, "pesos": 0.0} for i in ids}
    for v in ventas:
        out[v["item_id"]].update(pzs=v["pzs"] or 0, ordenes=v["ordenes"] or 0, pesos=float(v["pesos"] or 0))
    for l in hoy:
        out[l["listing_id"]].update(
            cobra_hoy=float(l["price_sale"]) if l["price_sale"] is not None else None,
            stock_hoy=l["stock_full"], situacion=l["situacion"],
            leido=l["updated_at"].isoformat() if l["updated_at"] else None,
        )
    return out


async def hechos() -> dict:
    """Los precios movidos, con lo vendido desde el cambio, y la promoción de hoy de cada publicación. Caché de 5 min."""
    global _hechos
    async with _candado_hechos:
        ahora = time.monotonic()
        if _hechos and ahora - _hechos[0] <= TTL_HECHOS_S:
            return _hechos[1]
        lista = await asyncio.to_thread(_leer_hechos)
        promos = await asyncio.to_thread(_leer_promos)
        kubera = True
        try:
            despues = await asyncio.to_thread(_despues, lista)
        except Exception as exc:  # kubera caída o lenta: los movimientos se ven igual, sin lo de después
            log.warning("pricing: no se pudo leer lo vendido desde el cambio: %s", exc)
            despues, kubera = {}, False
        datos = {"hechos": [{**h, "despues": despues.get(h["id"])} for h in lista],
                 "kubera": kubera and bool(despues or not lista),
                 "promos": promos["promos"], "promos_leido": promos["leido"],
                 "leido": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _hechos = (time.monotonic(), datos)
        return datos
