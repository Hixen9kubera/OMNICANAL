"""
woocommerce.py — Cliente de la API REST de WooCommerce (chunche.shop).

WooCommerce es "el centro": la vista GENERAL lista directamente sus productos.
Usamos httpx async para paginar de 40 en 40 y leemos los headers
X-WP-Total / X-WP-TotalPages para la paginación.

También resolvemos la RUTA COMPLETA de categorías (todos los niveles), ya que
el endpoint de productos solo devuelve la categoría asignada sin su cadena de
padres. Cacheamos el árbol de categorías en memoria.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from config import settings

log = logging.getLogger("omnicanal.woocommerce")

_BASE = f"{settings.wc_url.rstrip('/')}/wp-json/wc/v3"
_AUTH = (settings.wc_consumer_key, settings.wc_consumer_secret)

# Cache del árbol de categorías: { cat_id: {"name":..., "parent":...} }
_cat_cache: dict[int, dict[str, Any]] = {}
_cat_cache_ts: float = 0.0
_CAT_TTL = 60 * 30  # 30 min


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=_BASE, auth=_AUTH, timeout=30.0)


async def _cargar_categorias() -> dict[int, dict[str, Any]]:
    """Descarga TODAS las categorías y arma { id: {name, parent} }."""
    global _cat_cache, _cat_cache_ts
    if _cat_cache and (time.time() - _cat_cache_ts) < _CAT_TTL:
        return _cat_cache

    cache: dict[int, dict[str, Any]] = {}
    async with _client() as cli:
        page = 1
        while True:
            r = await cli.get(
                "/products/categories",
                params={"per_page": 100, "page": page, "_fields": "id,name,parent"},
            )
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            for c in data:
                cache[c["id"]] = {"name": c["name"], "parent": c.get("parent", 0)}
            total_pages = int(r.headers.get("X-WP-TotalPages", page))
            if page >= total_pages:
                break
            page += 1

    _cat_cache = cache
    _cat_cache_ts = time.time()
    log.info("Categorías WooCommerce cacheadas: %d", len(cache))
    return cache


async def ruta_categoria(cat_id: int | None) -> list[dict[str, Any]]:
    """Devuelve la ruta completa [{id,nombre}, ...] desde la raíz hasta la hoja."""
    if not cat_id:
        return []
    cats = await _cargar_categorias()
    ruta: list[dict[str, Any]] = []
    visitados: set[int] = set()
    actual = cat_id
    while actual and actual in cats and actual not in visitados:
        visitados.add(actual)
        nodo = cats[actual]
        ruta.insert(0, {"id": actual, "nombre": nodo["name"]})
        actual = nodo.get("parent") or 0
    return ruta


def _img(producto: dict[str, Any]) -> str | None:
    imgs = producto.get("images") or []
    return imgs[0].get("src") if imgs else None


def _marca(producto: dict[str, Any]) -> str | None:
    marcas = producto.get("brands") or []
    if marcas:
        return marcas[0].get("name")
    return None


async def _categoria_de_producto(producto: dict[str, Any]) -> list[dict[str, Any]]:
    cats = producto.get("categories") or []
    if not cats:
        return []
    # Tomamos la categoría asignada más específica y resolvemos su ruta completa.
    leaf_id = cats[-1].get("id")
    ruta = await ruta_categoria(leaf_id)
    if ruta:
        return ruta
    # Fallback: lo que venga embebido
    return [{"id": c.get("id"), "nombre": c.get("name")} for c in cats]


async def listar_productos(
    page: int = 1,
    per_page: int = 40,
    search: str | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    Lista productos paginados. Devuelve (items_normalizados, total, total_pages).
    Cada item ya trae la ruta completa de categorías.
    """
    params: dict[str, Any] = {
        "per_page": per_page,
        "page": page,
        "status": "any",
        "orderby": "date",
        "order": "desc",
        "_fields": (
            "id,name,sku,price,regular_price,sale_price,stock_quantity,"
            "stock_status,status,type,categories,brands,images,permalink"
        ),
    }
    if search:
        params["search"] = search

    async with _client() as cli:
        r = await cli.get("/products", params=params)
        r.raise_for_status()
        data = r.json()
        total = int(r.headers.get("X-WP-Total", len(data)))
        total_pages = int(r.headers.get("X-WP-TotalPages", 1))

    # Resolvemos categorías en paralelo (cache compartida hace esto barato).
    rutas = await asyncio.gather(*[_categoria_de_producto(p) for p in data])

    items: list[dict[str, Any]] = []
    for p, ruta in zip(data, rutas):
        precio = _to_float(p.get("price"))
        base = _to_float(p.get("regular_price")) or precio
        items.append(
            {
                "sku": p.get("sku") or f"WC-{p.get('id')}",
                "wc_id": p.get("id"),
                "nombre": p.get("name", ""),
                "imagen": _img(p),
                "marca": _marca(p),
                "precio": precio,
                "precio_base": base,
                "stock": p.get("stock_quantity"),
                "estado": p.get("status"),
                "categoria_path": ruta,
                "categoria_id": ruta[-1]["id"] if ruta else None,
                "publicado": p.get("status") == "publish",
                "url": p.get("permalink"),
            }
        )
    return items, total, total_pages


async def obtener_producto_por_sku(sku: str) -> dict[str, Any] | None:
    async with _client() as cli:
        r = await cli.get("/products", params={"sku": sku, "_fields": "id,name,sku,price,regular_price,stock_quantity,status,categories,brands,images,description,permalink"})
        r.raise_for_status()
        data = r.json()
    if not data:
        return None
    p = data[0]
    ruta = await _categoria_de_producto(p)
    imgs = [i.get("src") for i in (p.get("images") or []) if i.get("src")]
    return {
        "sku": p.get("sku") or f"WC-{p.get('id')}",
        "wc_id": p.get("id"),
        "nombre": p.get("name", ""),
        "imagen": imgs[0] if imgs else None,
        "imagenes": imgs,
        "marca": _marca(p),
        "descripcion": p.get("description"),
        "precio": _to_float(p.get("price")),
        "precio_base": _to_float(p.get("regular_price")),
        "stock": p.get("stock_quantity"),
        "estado": p.get("status"),
        "categoria_path": ruta,
        "categoria_id": ruta[-1]["id"] if ruta else None,
        "url": p.get("permalink"),
    }


async def ping() -> bool:
    try:
        async with _client() as cli:
            r = await cli.get("/products", params={"per_page": 1, "_fields": "id"})
            return r.status_code == 200
    except Exception as exc:  # noqa: BLE001
        log.warning("WooCommerce ping falló: %s", exc)
        return False


def _to_float(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
