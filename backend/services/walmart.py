"""
walmart.py — Cliente de la API de Walmart Marketplace MX (solo lectura).

Nace SOLO LECTURA, igual que el de Temu: publicar en Walmart va por FEEDS
(`scripts/publicar_walmart.py`), que es un camino con su propio ritmo y sus
propias trampas medidas. Esto es lo que el PANEL necesita para saber qué hay
arriba.

AUTENTICACIÓN, en dos pasos
───────────────────────────
`client_id:client_secret` en Basic → `POST /v3/token` → `access_token`, que
viaja en la cabecera `WM_SEC.ACCESS_TOKEN`. Las otras tres cabeceras
(`WM_SVC.NAME`, `WM_QOS.CORRELATION_ID`, `WM_MARKET: mx`) son obligatorias en
TODAS las llamadas: sin ellas la API contesta 400 sin decir cuál falta.

LO QUE ESTE CANAL TIENE Y LOS OTROS NO
──────────────────────────────────────
`publishedStatus` es una palabra —PUBLISHED / UNPUBLISHED— y no un número sin
documentar como en Temu. Aquí sí se puede afirmar qué está publicado.

Lo que NO tiene: **no hay API de atributos por categoría**. Ese hueco se cubre
con el esquema público (ver `scripts/walmart_field_requirements.py`), y por eso
las reglas de `channel.field_requirements` de este canal salen de un archivo.
"""
from __future__ import annotations

import base64
import logging
import os
import uuid
from typing import Any

import httpx

log = logging.getLogger("omnicanal.walmart")

HOST = "https://marketplace.walmartapis.com"
PAGE_SIZE = 50          # el tope que acepta /v3/items


def _credenciales() -> tuple[str, str]:
    from config import settings
    cid = (os.getenv("WM_CLIENT_ID")
           or getattr(settings, "wm_client_id", "") or "").strip()
    sec = (os.getenv("WM_CLIENT_SECRET")
           or getattr(settings, "wm_client_secret", "") or "").strip()
    return cid, sec


def disponible() -> bool:
    cid, sec = _credenciales()
    return bool(cid and sec)


def _cabeceras(token: str | None = None) -> dict[str, str]:
    """Las tres cabeceras obligatorias (+ el token si ya lo hay)."""
    d = {
        "WM_SVC.NAME": "Walmart Marketplace",
        "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
        "WM_MARKET": "mx",
        "Accept": "application/json",
    }
    if token:
        d["WM_SEC.ACCESS_TOKEN"] = token
    return d


async def token() -> str:
    """Un `access_token` nuevo. No se cachea: dura poco y pedirlo es barato."""
    cid, sec = _credenciales()
    if not (cid and sec):
        raise RuntimeError("Walmart no está configurado (faltan WM_CLIENT_ID / "
                           "WM_CLIENT_SECRET).")
    cab = _cabeceras()
    cab["Authorization"] = "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode()
    cab["Content-Type"] = "application/x-www-form-urlencoded"
    async with httpx.AsyncClient(timeout=40.0) as cli:
        r = await cli.post(f"{HOST}/v3/token", headers=cab,
                           data={"grant_type": "client_credentials"})
    r.raise_for_status()
    tk = r.json().get("access_token")
    if not tk:
        raise RuntimeError("Walmart devolvió un token vacío.")
    return tk


async def listar_items(tope: int = 5000) -> list[dict[str, Any]]:
    """
    El catálogo COMPLETO de la cuenta, paginado.

    Se pagina con `offset`, no con cursor. Cada artículo trae `sku`, `wpid`,
    `productName`, `productType`, `publishedStatus`, `price`, `gtin` y `upc`.

    Se deduplica por SKU: la API puede devolver el mismo artículo en dos páginas
    si el catálogo cambia mientras se recorre, y contar de más es peor que
    tardar un segundo más.
    """
    tk = await token()
    vistos: dict[str, dict[str, Any]] = {}
    offset = 0
    async with httpx.AsyncClient(timeout=90.0) as cli:
        while offset < tope:
            r = await cli.get(f"{HOST}/v3/items", headers=_cabeceras(tk),
                              params={"limit": str(PAGE_SIZE), "offset": str(offset)})
            if r.status_code != 200:
                log.warning("walmart /v3/items HTTP %s en offset %s: %s",
                            r.status_code, offset, r.text[:200])
                break
            j = r.json()
            lote = j.get("ItemResponse") or []
            for it in lote:
                sku = str(it.get("sku") or "").strip()
                if sku:
                    vistos.setdefault(sku, it)
            if len(lote) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    return list(vistos.values())


async def total_items() -> int | None:
    """Cuántos artículos dice Walmart que hay, sin traerlos todos."""
    tk = await token()
    async with httpx.AsyncClient(timeout=60.0) as cli:
        r = await cli.get(f"{HOST}/v3/items", headers=_cabeceras(tk),
                          params={"limit": "1"})
    if r.status_code != 200:
        return None
    return r.json().get("totalItems")
