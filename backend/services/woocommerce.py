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


def _resumen(html: str | None, limite: int = 160) -> str | None:
    """Convierte un HTML corto de WooCommerce en texto plano recortado."""
    if not html:
        return None
    import re
    texto = re.sub(r"<[^>]+>", " ", html)
    texto = re.sub(r"\s+", " ", texto).strip()
    if not texto:
        return None
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


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


_ESTADOS_WC = {
    "publicado": ["publish"],
    "inactivo": ["pending", "draft", "inprogress", "ready", "private"],
}
_ORDEN_SQL = {
    "stock_desc": "stock_odoo DESC",
    "stock_asc": "stock_odoo ASC",
    "precio_desc": "precio DESC",
    "precio_asc": "precio ASC",
    "reciente": "updated_at DESC",
}


async def listar_productos(
    page: int = 1,
    per_page: int = 40,
    search: str | None = None,
    orden: str = "reciente",
    estados: list[str] | None = None,
    categoria: int | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    Lista productos paginados. Devuelve (items_normalizados, total, total_pages).

    - `categoria` (id WC) → ruta nativa de WooCommerce (param category).
    - `search` / `estados` / `orden` por stock o precio → se resuelven contra la
      tabla maestra `productos` (más potente) y se traen de WooCommerce por wc_id.
    - Sin filtros → listado en vivo de WooCommerce.
    """
    campos = (
        "id,name,sku,price,regular_price,sale_price,stock_quantity,"
        "stock_status,status,type,categories,brands,images,short_description,permalink"
    )
    params: dict[str, Any] = {
        "per_page": per_page,
        "page": page,
        "status": "any",
        "orderby": "date",
        "order": "desc",
        "_fields": campos,
    }

    usa_db = bool(search or estados or orden in ("stock_desc", "stock_asc", "precio_desc", "precio_asc"))

    async with _client() as cli:
        data, total, total_pages = [], 0, 1

        if categoria:
            # Ruta nativa WooCommerce por categoría (+ orden por precio si aplica)
            p = {**params, "category": categoria}
            if orden in ("precio_desc", "precio_asc"):
                p["orderby"] = "price"
                p["order"] = "asc" if orden == "precio_asc" else "desc"
            r = await cli.get("/products", params=p)
            r.raise_for_status()
            data = r.json()
            total = int(r.headers.get("X-WP-Total", len(data)))
            total_pages = int(r.headers.get("X-WP-TotalPages", 1))

        elif usa_db:
            # Filtros/orden avanzados vía tabla `productos` → wc_ids → WooCommerce
            wc_ids, total_db = _buscar_wc_ids_db(search, page, per_page, orden, estados)
            if wc_ids:
                r = await cli.get("/products", params={
                    **params, "include": ",".join(str(i) for i in wc_ids),
                    "per_page": len(wc_ids),
                })
                if r.status_code == 200:
                    by_id = {p["id"]: p for p in r.json()}
                    data = [by_id[i] for i in wc_ids if i in by_id]  # preserva el orden
                    total = total_db
                    total_pages = max(1, (total_db + per_page - 1) // per_page)
            # Fallback SKU exacto / nombre si la DB no resolvió (p. ej. solo search)
            if not data and search:
                if " " not in search.strip():
                    rs = await cli.get("/products", params={**params, "sku": search.strip()})
                    if rs.status_code == 200 and rs.json():
                        data = rs.json()
                        total = int(rs.headers.get("X-WP-Total", len(data)))
                        total_pages = int(rs.headers.get("X-WP-TotalPages", 1))
                if not data:
                    rn = await cli.get("/products", params={**params, "search": search})
                    rn.raise_for_status()
                    data = rn.json()
                    total = int(rn.headers.get("X-WP-Total", len(data)))
                    total_pages = int(rn.headers.get("X-WP-TotalPages", 1))
        else:
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
                "descripcion_corta": _resumen(p.get("short_description")),
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


async def listar_categorias(limite: int = 300) -> list[dict[str, Any]]:
    """Categorías de WooCommerce con productos (id, nombre, count), para el filtro."""
    salida: list[dict[str, Any]] = []
    async with _client() as cli:
        page = 1
        while page <= 20 and len(salida) < limite:
            r = await cli.get("/products/categories", params={
                "per_page": 100, "page": page, "orderby": "count", "order": "desc",
                "hide_empty": True, "_fields": "id,name,parent,count",
            })
            if r.status_code != 200:
                break
            data = r.json()
            if not data:
                break
            for c in data:
                if c.get("count", 0) > 0:
                    salida.append({"id": c["id"], "nombre": c["name"],
                                   "parent": c.get("parent", 0), "count": c["count"]})
            if page >= int(r.headers.get("X-WP-TotalPages", page)):
                break
            page += 1
    return salida[:limite]


def _buscar_wc_ids_db(
    search: str | None,
    page: int,
    per_page: int,
    orden: str = "reciente",
    estados: list[str] | None = None,
) -> tuple[list[int], int]:
    """
    Resuelve búsqueda parcial + filtro de estado + orden (stock/precio) contra la
    tabla maestra `productos` y devuelve (wc_ids de la página, total).
    Habilita en GENERAL: búsqueda parcial, ordenar por stock/precio y filtrar por
    estado (publicado / inactivo).
    """
    from services import db  # import local para evitar ciclos

    where = ["wc_id IS NOT NULL"]
    args: list[Any] = []
    if search:
        where.append("(sku LIKE %s OR nombre LIKE %s)")
        like = f"%{search.strip()}%"
        args += [like, like]
    if estados:
        valores: list[str] = []
        for e in estados:
            valores += _ESTADOS_WC.get(e, [])
        if valores:
            where.append(f"status_wc IN ({','.join(['%s'] * len(valores))})")
            args += valores
    where_sql = " AND ".join(where)
    order_sql = _ORDEN_SQL.get(orden, "updated_at DESC")
    offset = (page - 1) * per_page
    try:
        total = int(db.fetch_scalar(
            f"SELECT COUNT(*) FROM productos WHERE {where_sql}", tuple(args)
        ) or 0)
        if not total:
            return [], 0
        rows = db.fetch_all(
            f"""SELECT wc_id FROM productos WHERE {where_sql}
                ORDER BY {order_sql} LIMIT %s OFFSET %s""",
            tuple(args + [per_page, offset]),
        )
        return [r["wc_id"] for r in rows], total
    except Exception as exc:  # noqa: BLE001
        log.warning("Búsqueda DB falló, usando WooCommerce: %s", exc)
        return [], 0


async def imagenes_por_wc_id(wc_ids: list[int]) -> dict[int, str]:
    """
    Trae la imagen principal de WooCommerce para una lista de wc_id en UNA sola
    llamada (param include). Se usa para mostrar imágenes en los canales de
    marketplace (que comparten el mismo producto vía wc_id).
    """
    ids = [str(i) for i in wc_ids if i]
    if not ids:
        return {}
    salida: dict[int, str] = {}
    # WooCommerce limita include/per_page a 100; los lotes vienen de a ≤40.
    async with _client() as cli:
        r = await cli.get(
            "/products",
            params={
                "include": ",".join(ids),
                "per_page": min(len(ids), 100),
                "_fields": "id,images",
            },
        )
        if r.status_code == 200:
            for p in r.json():
                imgs = p.get("images") or []
                if imgs and imgs[0].get("src"):
                    salida[p["id"]] = imgs[0]["src"]
    return salida


def _atributos(producto: dict[str, Any]) -> list[dict[str, Any]]:
    """Normaliza los atributos de WooCommerce a [{nombre, valor}]."""
    salida: list[dict[str, Any]] = []
    for a in producto.get("attributes") or []:
        nombre = a.get("name")
        if not nombre:
            continue
        opciones = a.get("options") or []
        valor = ", ".join(str(o) for o in opciones) if opciones else ""
        salida.append({"nombre": nombre, "valor": valor})
    return salida


async def obtener_producto_por_sku(sku: str) -> dict[str, Any] | None:
    try:
        async with _client() as cli:
            r = await cli.get("/products", params={"sku": sku, "_fields": "id,name,sku,price,regular_price,sale_price,stock_quantity,status,categories,brands,images,description,short_description,attributes,permalink"})
            r.raise_for_status()
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("WooCommerce obtener_producto_por_sku(%s) falló: %s", sku, exc)
        return None
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
        "descripcion_corta": p.get("short_description"),
        "atributos": _atributos(p),
        "precio": _to_float(p.get("price")),
        "precio_base": _to_float(p.get("regular_price")),
        "precio_oferta": _to_float(p.get("sale_price")),
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
