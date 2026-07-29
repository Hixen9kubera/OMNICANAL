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

# El DRIVER es `amazon_progress`, NO `productos` (auditoría 29-jul).
# ------------------------------------------------------------------
# `productos` es el catálogo del robot KuberaPipelineV1.0, CONGELADO desde el
# 23-jul y con solo 5,381 filas. Al manejarlo como tabla principal, un SKU
# publicado en Amazon que no estuviera ahí devolvía CERO filas: el panel
# colapsaba a "General" y la tarjeta de Amazon simplemente no existía.
# Medido: 493 SKUs publicados en Amazon se caían por esto (p.ej. TEC-2366-NEG,
# con amazon_progress success=1 y PUBLISHED, daba (0, [])).
# `meli.py` siempre lo hizo al revés (FROM ml_progress) — por eso Mercado Libre
# sí aparecía y Amazon no. Ahora los dos espejan el mismo patrón, y `productos`
# queda como fuente OPCIONAL de nombre/precio vía COALESCE.
_SQL_LISTAR = """
    SELECT  ap.sku,
            COALESCE(p.wc_id, ap.wc_id)      AS wc_id,
            p.odoo_id,
            COALESCE(p.nombre, ap.sku)       AS nombre,
            p.stock_odoo,
            p.precio,
            ap.asin,
            ap.product_type,
            ap.status,
            ap.success      AS publicado,
            ap.published_at
    FROM amazon_progress ap
    LEFT JOIN productos p ON p.sku = ap.sku
    WHERE (%(solo_publicados)s = 0 OR ap.success = 1)
      AND (%(search)s IS NULL OR p.nombre LIKE %(like)s OR ap.sku LIKE %(like)s)
      __ESTADO__
      __SKUS__
    ORDER BY __ORDEN__
    LIMIT %(limit)s OFFSET %(offset)s
"""

_SQL_COUNT = """
    SELECT COUNT(*) AS total
    FROM amazon_progress ap
    LEFT JOIN productos p ON p.sku = ap.sku
    WHERE (%(solo_publicados)s = 0 OR ap.success = 1)
      AND (%(search)s IS NULL OR p.nombre LIKE %(like)s OR ap.sku LIKE %(like)s)
      __ESTADO__
      __SKUS__
"""


def _clausula_skus(skus_filtro: list[str] | None, prefijo: str) -> tuple[str, dict[str, Any]]:
    """Igual semántica que en meli.py: "Filtrar SKUs" filtra Y busca a la vez."""
    terminos = [t.strip() for t in (skus_filtro or []) if t.strip()]
    if not terminos:
        return "", {}
    piezas: list[str] = []
    params: dict[str, Any] = {}
    for i, t in enumerate(terminos):
        clave = f"{prefijo}{i}"
        piezas.append(f"(p.nombre LIKE %({clave})s OR ap.sku LIKE %({clave})s)")
        params[clave] = f"%{t}%"
    return f"AND ({' OR '.join(piezas)})", params


def por_sku(sku: str) -> dict[str, Any] | None:
    """
    La publicación de Amazon de ESE SKU exacto, o None.

    POR QUÉ EXISTE (auditoría 29-jul). El detalle 360° usaba
    `listar(search=sku, per_page=1)`, y `search` se traduce a `LIKE %sku%`. Con
    SKUs que son prefijo de otros (medido: **693** SKUs distintos lo son; el peor,
    `TEC-0377`, tiene 102 descendientes), el LIKE capturaba a los hijos y
    `per_page=1` se quedaba con UNO ARBITRARIO. Resultado: el panel pintaba el
    ASIN, el precio y el stock **de otro producto** como si fueran de éste.
    Comprobado: `listar(search='ACC-0091', per_page=1)` devolvía `ACC-0091-AST-PLA`.

    Un detalle NUNCA debe resolverse con búsqueda difusa.
    """
    filas = db.fetch_all(
        _SQL_LISTAR
        .replace("__ESTADO__", "AND ap.sku = %(sku_exacto)s")
        .replace("__SKUS__", "")
        .replace("__ORDEN__", "ap.published_at DESC"),
        {"solo_publicados": 0, "search": None, "like": None,
         "sku_exacto": sku, "limit": 1, "offset": 0},
    )
    return _normalizar(filas[0]) if filas else None


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


_ORDEN_AZ = {
    "stock_desc": "p.stock_odoo DESC",
    "stock_asc": "p.stock_odoo ASC",
    "precio_desc": "p.precio DESC",
    "precio_asc": "p.precio ASC",
    "reciente": "(ap.success = 1) DESC, p.updated_at DESC",
}


def listar(
    page: int = 1,
    per_page: int = 40,
    search: str | None = None,
    solo_publicados: bool = False,
    orden: str = "reciente",
    estados: list[str] | None = None,
    skus_filtro: list[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    offset = (page - 1) * per_page
    like = f"%{search}%" if search else None
    estado_sql = ""
    if estados:
        if "publicado" in estados and "inactivo" not in estados:
            estado_sql = " AND ap.success = 1"
        elif "inactivo" in estados and "publicado" not in estados:
            estado_sql = " AND (ap.success IS NULL OR ap.success = 0)"
    order_sql = _ORDEN_AZ.get(orden, _ORDEN_AZ["reciente"])
    skus_sql, skus_params = _clausula_skus(skus_filtro, "sku_")
    params = {
        "limit": per_page, "offset": offset, "search": search, "like": like,
        "solo_publicados": 1 if solo_publicados else 0,
        **skus_params,
    }
    sql = (_SQL_LISTAR.replace("__ESTADO__", estado_sql)
           .replace("__ORDEN__", order_sql).replace("__SKUS__", skus_sql))
    sql_count = _SQL_COUNT.replace("__ESTADO__", estado_sql).replace("__SKUS__", skus_sql)
    try:
        rows = db.fetch_all(sql, params)
        total = db.fetch_scalar(sql_count, params) or 0
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
                    # `Skus` va SEPARADO POR COMAS. httpx serializa una lista de
                    # Python como parámetros REPETIDOS (Skus=A&Skus=B&…) y SP-API
                    # responde 200 pero solo honra el PRIMERO: de cada lote de 20
                    # volvía 1 item. Medido: misma lista con lista → 1 item; con
                    # join(",") → 20 items. Techo de 4 precios por corrida contra
                    # 80 SKUs — por eso el caché tenía 1,628 de 1,660 sin precio.
                    params={"MarketplaceId": mp, "ItemType": "Sku",
                            "Skus": ",".join(lote)},
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


async def detalle_sku(sku: str) -> dict[str, Any] | None:
    """
    Lee en vivo un SKU de Amazon en UNA llamada (Listings Items API):
    precio, stock (FBA si está en bodega Amazon, FBM si lo surte el vendedor),
    situación y ASIN. Amazon NO usa FULL (eso es de Mercado Libre).
    """
    token = await _access_token()
    if not token:
        return None
    base = settings.amazon_sp_api_endpoint
    mp = settings.amazon_marketplace_id
    seller = settings.amazon_seller_id
    try:
        async with httpx.AsyncClient(base_url=base, timeout=25.0) as cli:
            r = await cli.get(
                f"/listings/2021-08-01/items/{seller}/{sku}",
                params={"marketplaceIds": mp,
                        "includedData": "summaries,offers,fulfillmentAvailability"},
                headers={"x-amz-access-token": token},
            )
            if r.status_code != 200:
                return None
            d = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("Amazon detalle_sku %s falló: %s", sku, exc)
        return None

    summaries = d.get("summaries") or [{}]
    asin = summaries[0].get("asin")
    estado = summaries[0].get("status")
    if isinstance(estado, list):
        estado = estado[0] if estado else None

    offers = d.get("offers") or []
    precio = None
    if offers and offers[0].get("price"):
        precio = _f(offers[0]["price"].get("amount"))

    stock_fba = None
    stock_fbm = None
    for fa in d.get("fulfillmentAvailability") or []:
        code = (fa.get("fulfillmentChannelCode") or "").upper()
        qty = fa.get("quantity")
        if code.startswith("AMAZON"):
            stock_fba = qty
        else:  # DEFAULT / MFN → lo surte el vendedor (FBM = stock real)
            stock_fbm = qty

    return {
        "asin": asin, "precio": precio, "estado": estado,
        "stock_fba": stock_fba, "stock_real": stock_fbm,
        "es_fba": bool(stock_fba),
    }


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
