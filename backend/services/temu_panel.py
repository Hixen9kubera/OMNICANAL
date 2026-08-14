"""
temu_panel.py — Lo que el PANEL necesita saber de Temu: qué hay publicado.

Mismo papel que `tiktok_panel.py` y misma fuente (`channel.listings` en kubera).
Hasta el 14-ago la pestaña Temu mostraba **datos de ejemplo** (`ejemplos.py`),
porque `routers/productos.py` mandaba ahí todo canal que no fuera General, ML,
Amazon o TikTok — una maqueta encima de un canal con 160 publicaciones vivas.

LA DIFERENCIA CON TIKTOK, Y POR QUÉ IMPORTA
-------------------------------------------
TikTok dice `ACTIVATE` cuando algo está a la venta, así que el panel puede
afirmarlo. **Temu contesta números** (`status4VO`/`subStatus4VO`: 2/8, 3/2,
4/7…) y no publica qué significan. Solo dos están VERIFICADOS, cruzando
productos cuyo estado real se conocía por el Seller Center:

    2/8     → Incompleto   (los 4 publicados el 13-ago)
    5/None  → Borrador     (los 2 con precio 0.00)

Los otros cinco códigos —87 publicaciones— **no se traducen**. Se muestran
crudos y se dicen crudos. Suponerles significado sería repetir el error que en
TikTok habría atado el fan-out a una casualidad del dato.

CONSECUENCIA OPERATIVA: mientras no se sepa qué código significa "a la venta",
**Temu no entra al fan-out de stock**. No se le escribe inventario a un canal
del que no se sabe qué publicaciones venden.
"""
from __future__ import annotations

import logging
from typing import Any

from services import supabase_db as sdb
from services.temu import ESTADOS

log = logging.getLogger("omnicanal.temu_panel")

CANAL = "temu"

_SEL = """
    select l.sku::text as sku, p.wc_id, p.name as nombre,
           l.price, l.stock_own, l.status, l.situacion, l.listing_id, l.url,
           l.category_id, c.name as categoria_nombre
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


def etiqueta_estado(codigo: str | None) -> str:
    """El código de Temu en palabras, SOLO si está verificado."""
    if not codigo:
        return "sin publicar"
    return ESTADOS.get(codigo) or f"Temu {codigo}"


def _normalizar(r: dict[str, Any]) -> dict[str, Any]:
    codigo = r.get("status")
    return {
        "sku": r["sku"],
        "wc_id": r.get("wc_id"),
        "nombre": r.get("nombre") or r["sku"],
        "precio": float(r["price"]) if r.get("price") is not None else None,
        "precio_base": float(r["price"]) if r.get("price") is not None else None,
        "stock": r.get("stock_own"),
        "estado": etiqueta_estado(codigo),
        # El código crudo viaja aparte: la etiqueta es para leer, el código es
        # para depurar y para el día que se decodifiquen los cinco que faltan.
        "situacion": codigo,
        "categoria_id": r.get("category_id"),
        "categoria_path": ([{"id": r.get("category_id"),
                             "nombre": r.get("categoria_nombre") or r.get("category_id")}]
                           if r.get("category_id") else []),
        # "publicado" = existe en Temu. NO significa "se vende": eso todavía no
        # se puede afirmar (ver el encabezado).
        "publicado": True,
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
    """Publicaciones de Temu con los filtros de la pantalla. (items, total)."""
    where, params = [], {"canal": CANAL}
    if search:
        where.append("(l.sku::text ilike %(like)s or p.name ilike %(like)s)")
        params["like"] = f"%{search}%"
    if skus_filtro:
        where.append("l.sku::text = any(%(skus)s)")
        params["skus"] = list(skus_filtro)
    # `solo_publicados` no filtra nada aquí a propósito: todas las filas de esta
    # tabla SON publicaciones de Temu. El día que se sepa qué código vende, este
    # es el lugar donde se filtra.

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
        log.warning("temu_panel.listar falló: %s", exc)
        return [], 0


def contar_publicados() -> int:
    """TODAS las publicaciones del canal (mismo criterio que TikTok: lo que
    existe se ve, aunque no se venda — un borrador es trabajo por destrabar)."""
    try:
        r = sdb.fetch_all("select count(*) as n from channel.listings where canal=%(c)s",
                          {"c": CANAL})
        return int((r or [{}])[0].get("n") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("temu_panel.contar_publicados falló: %s", exc)
        return 0


def resumen_estados() -> list[dict[str, Any]]:
    """Cuántas publicaciones hay por estado, con su etiqueta cuando se conoce."""
    try:
        filas = sdb.fetch_all(
            """select status, count(*) n, coalesce(sum(stock_own),0) piezas
                 from channel.listings where canal=%(c)s
                group by status order by n desc""", {"c": CANAL})
        return [{"codigo": f["status"], "etiqueta": etiqueta_estado(f["status"]),
                 "publicaciones": int(f["n"]), "piezas": int(f["piezas"] or 0)}
                for f in filas]
    except Exception as exc:  # noqa: BLE001
        log.warning("temu_panel.resumen_estados falló: %s", exc)
        return []


def datos_de(sku: str) -> dict[str, Any] | None:
    """La publicación de Temu de UN SKU, ya normalizada, o None."""
    try:
        filas = sdb.fetch_all(f"{_SEL} and l.sku = %(sku)s::citext limit 1",
                              {"canal": CANAL, "sku": sku})
        return _normalizar(filas[0]) if filas else None
    except Exception as exc:  # noqa: BLE001
        log.warning("temu_panel.datos_de(%s) falló: %s", sku, exc)
        return None
