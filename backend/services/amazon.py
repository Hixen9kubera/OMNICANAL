"""
amazon.py — Servicio de Amazon (SP-API, seller San Corpe, marketplace México).

Estrategia híbrida:
  - LECTURA RÁPIDA (UI): join en MySQL de
        productos  +  amazon_progress (asin, product_type, status, published_at)
  - REFRESCO EN VIVO: flujo LWA (refresh_token → access_token) y consulta a
        SP-API (Catalog Items / Listings) para precio, stock y fulfillment (FBA).

El equivalente de "FULL" en Amazon es FBA (Fulfilled by Amazon).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from config import settings
from services import db

log = logging.getLogger("omnicanal.amazon")

_SQL_LISTAR = """
    SELECT  p.sku,
            p.wc_id,
            p.odoo_id,
            p.nombre,
            p.stock_odoo,
            p.precio,
            ap.asin,
            ap.product_type,
            ap.status,
            ap.success      AS publicado,
            ap.published_at
    FROM productos p
    LEFT JOIN amazon_progress ap ON ap.sku = p.sku
    WHERE (%(solo_publicados)s = 0 OR ap.success = 1)
      AND (%(search)s IS NULL OR p.nombre LIKE %(like)s OR p.sku LIKE %(like)s)
    ORDER BY (ap.success = 1) DESC, p.updated_at DESC
    LIMIT %(limit)s OFFSET %(offset)s
"""

_SQL_COUNT = """
    SELECT COUNT(*) AS total
    FROM productos p
    LEFT JOIN amazon_progress ap ON ap.sku = p.sku
    WHERE (%(solo_publicados)s = 0 OR ap.success = 1)
      AND (%(search)s IS NULL OR p.nombre LIKE %(like)s OR p.sku LIKE %(like)s)
"""


def _normalizar(row: dict[str, Any]) -> dict[str, Any]:
    publicado = bool(row.get("publicado"))
    asin = row.get("asin")
    return {
        "sku": row["sku"],
        "wc_id": row.get("wc_id"),
        "odoo_id": row.get("odoo_id"),
        "nombre": row.get("nombre") or row["sku"],
        "precio": _f(row.get("precio")),
        "precio_base": _f(row.get("precio")),
        "stock": row.get("stock_odoo"),
        "estado": row.get("status") or ("activo" if publicado else "sin publicar"),
        "categoria_id": row.get("product_type"),
        "categoria_path": (
            [{"id": row.get("product_type"), "nombre": row.get("product_type")}]
            if row.get("product_type") else []
        ),
        "publicado": publicado,
        "item_id": asin,
        "url": f"https://www.amazon.com.mx/dp/{asin}" if asin else None,
        "full": None,            # se completa al refrescar (FBA vs FBM)
        "full_label": "FBA",
        "origen": "db",
    }


def listar(
    page: int = 1,
    per_page: int = 40,
    search: str | None = None,
    solo_publicados: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    offset = (page - 1) * per_page
    like = f"%{search}%" if search else None
    params = {
        "limit": per_page,
        "offset": offset,
        "search": search,
        "like": like,
        "solo_publicados": 1 if solo_publicados else 0,
    }
    try:
        rows = db.fetch_all(_SQL_LISTAR, params)
        total = db.fetch_scalar(_SQL_COUNT, params) or 0
        return [_normalizar(r) for r in rows], int(total)
    except Exception as exc:  # noqa: BLE001
        log.error("Error listando Amazon desde DB: %s", exc)
        return [], 0


def contar_publicados() -> int:
    try:
        return int(db.fetch_scalar(
            "SELECT COUNT(*) FROM amazon_progress WHERE success = 1"
        ) or 0)
    except Exception:  # noqa: BLE001
        return 0


# ── LWA: refresh_token → access_token (cacheado) ──────────────────────────────

_token_cache: dict[str, Any] = {"value": None, "exp": 0.0}


async def _access_token() -> str | None:
    if _token_cache["value"] and time.time() < _token_cache["exp"]:
        return _token_cache["value"]
    if not settings.amazon_refresh_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as cli:
            r = await cli.post(
                settings.amazon_lwa_token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": settings.amazon_refresh_token,
                    "client_id": settings.amazon_lwa_client_id,
                    "client_secret": settings.amazon_lwa_client_secret,
                },
            )
            r.raise_for_status()
            tok = r.json()
        _token_cache["value"] = tok["access_token"]
        _token_cache["exp"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        return _token_cache["value"]
    except Exception as exc:  # noqa: BLE001
        log.warning("LWA token Amazon falló: %s", exc)
        return None


async def precios_por_sku(skus: list[str]) -> dict[str, float]:
    """
    Precio de venta (ListingPrice) por SKU usando la Pricing API v0, en lotes de
    20 SKUs por llamada. Devuelve { sku: precio }.
    """
    token = await _access_token()
    if not token or not skus:
        return {}
    base = settings.amazon_sp_api_endpoint
    mp = settings.amazon_marketplace_id
    out: dict[str, float] = {}
    headers = {"x-amz-access-token": token}
    try:
        async with httpx.AsyncClient(base_url=base, timeout=30.0) as cli:
            for i in range(0, len(skus), 20):
                lote = skus[i : i + 20]
                r = await cli.get(
                    "/products/pricing/v0/price",
                    params={"MarketplaceId": mp, "ItemType": "Sku", "Skus": lote},
                    headers=headers,
                )
                if r.status_code != 200:
                    continue
                for p in r.json().get("payload", []):
                    if p.get("status") != "Success":
                        continue
                    sku = p.get("SellerSKU")
                    offers = (p.get("Product") or {}).get("Offers") or []
                    if sku and offers:
                        amt = (offers[0].get("BuyingPrice") or {}).get("ListingPrice", {}).get("Amount")
                        if amt is not None:
                            out[sku] = float(amt)
    except Exception as exc:  # noqa: BLE001
        log.warning("Pricing Amazon falló: %s", exc)
    return out


async def refrescar_listing(sku: str, asin: str | None = None) -> dict[str, Any] | None:
    """Consulta SP-API para precio/stock/fulfillment de un SKU."""
    token = await _access_token()
    if not token:
        return None
    headers = {"x-amz-access-token": token}
    base = settings.amazon_sp_api_endpoint
    mp = settings.amazon_marketplace_id
    seller = settings.amazon_seller_id
    try:
        async with httpx.AsyncClient(base_url=base, timeout=25.0) as cli:
            r = await cli.get(
                f"/listings/2021-08-01/items/{seller}/{sku}",
                params={"marketplaceIds": mp, "includedData": "summaries,offers,fulfillmentAvailability,attributes"},
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("Refresco Amazon %s falló: %s", sku, exc)
        return None

    summaries = data.get("summaries") or [{}]
    offers = data.get("offers") or [{}]
    fulfillment = data.get("fulfillmentAvailability") or []
    es_fba = any(
        (f.get("fulfillmentChannelCode") or "").upper().startswith("AMAZON")
        for f in fulfillment
    )
    precio = None
    if offers and offers[0].get("price"):
        precio = _f(offers[0]["price"].get("amount"))

    return {
        "item_id": summaries[0].get("asin") or asin,
        "precio": precio,
        "estado": summaries[0].get("status", [None])[0] if isinstance(summaries[0].get("status"), list) else summaries[0].get("status"),
        "categoria_id": summaries[0].get("productType"),
        "full": es_fba,
        "full_label": "FBA" if es_fba else "FBM",
    }


def _f(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
