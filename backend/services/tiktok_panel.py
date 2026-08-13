"""
tiktok_panel.py — Lo que el PANEL necesita saber de TikTok: qué hay publicado.

QUÉ RESUELVE
------------
La pestaña TikTok existía desde siempre y mostraba **datos de ejemplo**
(`services/ejemplos.py`), porque `routers/productos.py` mandaba ahí todo canal
que no fuera General, ML o Amazon. Con la tienda publicando desde julio, esa
pantalla era una maqueta encima de un canal vivo.

DE DÓNDE LEE, Y POR QUÉ DE AHÍ
------------------------------
De `channel.listings` en la BD kubera, que es donde el censo dejó las 900
publicaciones. **No de MySQL**: desde el 13-ago los espejos inversos están
apagados y `canal_inventario` no recibe nada — leer de ahí sería consultar una
foto que ya nadie actualiza (el mismo error que dejó 964 pedidos fantasma).

ML y Amazon todavía se listan desde MySQL (`meli.listar`, `amazon.listar`); este
módulo es el primero que hace la lectura del panel contra kubera directamente.
Cuando esos dos se muden, éste es el molde.

EL NOMBRE DEL PRODUCTO SALE DE `core.products`
----------------------------------------------
`channel.listings` no guarda título — a propósito: el título por canal vive en
`enrich.channel_content` y el del catálogo en el maestro. Se une por SKU con
`core.products.name`, que es el registro civil.
"""
from __future__ import annotations

import logging
from typing import Any

from services import supabase_db as sdb

log = logging.getLogger("omnicanal.tiktok_panel")

CANAL = "tiktok"

# TikTok llama ACTIVATE a lo que está a la venta. El resto (DRAFT, PENDING,
# FAILED) existe pero no se vende, y esa diferencia es la que el panel pinta.
ESTADO_VIVO = "ACTIVATE"

_SEL = """
    select l.sku::text as sku, p.wc_id, p.name as nombre,
           l.price, l.stock_own, l.status, l.situacion, l.listing_id, l.url,
           l.category_id, c.name as categoria_nombre, c.path as categoria_path
      from channel.listings l
      join core.products p on p.sku = l.sku
      left join channel.categories c
             on c.channel_id = %(canal)s and c.category_id = l.category_id
     where l.canal = %(canal)s
"""

_ORDEN = {
    "reciente": "l.updated_at desc nulls last",
    "stock_desc": "l.stock_own desc nulls last",
    "stock_asc": "l.stock_own asc nulls last",
    "precio_desc": "l.price desc nulls last",
    "precio_asc": "l.price asc nulls last",
}


def _normalizar(r: dict[str, Any]) -> dict[str, Any]:
    publicado = (r.get("status") or "") == ESTADO_VIVO
    return {
        "sku": r["sku"],
        "wc_id": r.get("wc_id"),
        "nombre": r.get("nombre") or r["sku"],
        "precio": float(r["price"]) if r.get("price") is not None else None,
        "precio_base": float(r["price"]) if r.get("price") is not None else None,
        "stock": r.get("stock_own"),
        # `status` es del producto y `situacion` es de la AUDITORÍA de TikTok:
        # un ACTIVATE con auditoría FAILED existe, y aplastarlos en un solo
        # texto escondería justo el motivo por el que algo no se vende.
        "estado": r.get("status") or "sin publicar",
        "situacion": r.get("situacion"),
        "categoria_id": r.get("category_id"),
        "categoria_path": ([{"id": r.get("category_id"),
                             "nombre": r.get("categoria_nombre") or r.get("category_id")}]
                           if r.get("category_id") else []),
        "publicado": publicado,
        "item_id": r.get("listing_id"),
        "url": r.get("url"),
        "full": None,
        "full_label": None,
        "origen": "db",
    }


def listar(page: int = 1, per_page: int = 40, search: str | None = None,
           solo_publicados: bool = False, orden: str = "reciente",
           estados: list[str] | None = None,
           skus_filtro: list[str] | None = None) -> tuple[list[dict[str, Any]], int]:
    """Publicaciones de TikTok con los filtros de la pantalla. (items, total)."""
    where, params = [], {"canal": CANAL}
    if search:
        where.append("(l.sku::text ilike %(like)s or p.name ilike %(like)s)")
        params["like"] = f"%{search}%"
    if solo_publicados or (estados and "publicado" in estados and "inactivo" not in estados):
        where.append("l.status = %(vivo)s")
        params["vivo"] = ESTADO_VIVO
    elif estados and "inactivo" in estados and "publicado" not in estados:
        where.append("l.status is distinct from %(vivo)s")
        params["vivo"] = ESTADO_VIVO
    if skus_filtro:
        where.append("l.sku::text = any(%(skus)s)")
        params["skus"] = list(skus_filtro)

    filtro = (" and " + " and ".join(where)) if where else ""
    orden_sql = _ORDEN.get(orden, _ORDEN["reciente"])
    params["limit"] = per_page
    params["offset"] = max(0, (page - 1) * per_page)
    try:
        filas = sdb.fetch_all(
            f"{_SEL}{filtro} order by {orden_sql} limit %(limit)s offset %(offset)s",
            params)
        total = sdb.fetch_all(
            f"""select count(*) as n from channel.listings l
                  join core.products p on p.sku = l.sku
                 where l.canal = %(canal)s{filtro}""", params)
        return [_normalizar(f) for f in filas], int((total or [{}])[0].get("n") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_panel.listar falló: %s", exc)
        return [], 0


def contar_publicados() -> int:
    """Los que están A LA VENTA, no los que existen: el número de la pestaña."""
    try:
        filas = sdb.fetch_all(
            "select count(*) as n from channel.listings where canal=%(canal)s and status=%(vivo)s",
            {"canal": CANAL, "vivo": ESTADO_VIVO})
        return int((filas or [{}])[0].get("n") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_panel.contar_publicados falló: %s", exc)
        return 0


def categoria_de(sku: str) -> str | None:
    """
    El `category_id` de TikTok para ese SKU — la llave con la que se buscan sus
    requisitos.

    ⚠️ En TikTok la categoría vive en `listings.category_id`; en Amazon vive en
    `listings.product_type`. Cruzar `field_requirements` por la columna
    equivocada devuelve cero filas SIN dar error, y el semáforo diría
    "sin requisitos" con 1,779 cargados.
    """
    try:
        filas = sdb.fetch_all(
            """select category_id from channel.listings
                where canal=%(canal)s and sku=%(sku)s::citext and category_id is not null
                limit 1""", {"canal": CANAL, "sku": sku})
        return (filas or [{}])[0].get("category_id")
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_panel.categoria_de(%s) falló: %s", sku, exc)
        return None
