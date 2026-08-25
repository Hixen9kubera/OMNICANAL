"""
walmart_panel.py — Lo que el PANEL necesita saber de Walmart: qué hay publicado.

Mismo papel que `tiktok_panel.py` y `temu_panel.py`, misma fuente
(`channel.listings` en kubera). Hasta el 17-ago la pestaña Walmart mostraba
datos de EJEMPLO encima de una cuenta con 235 artículos reales.

LO BUENO DE ESTE CANAL: el estado es una PALABRA
────────────────────────────────────────────────
`publishedStatus` viene como PUBLISHED / UNPUBLISHED / SYSTEM_PROBLEM. No hay
que decodificar nada — en Temu hicieron falta los totales del Seller Center para
adivinar qué significaban siete números. Aquí el panel puede afirmar qué está
publicado y qué no.

⚠️ LAS DOS TAXONOMÍAS DE WALMART — la trampa de este canal
──────────────────────────────────────────────────────────
Walmart usa DOS clasificaciones distintas y no coinciden en un solo valor
(medido: 100 `productType` contra 76 categorías del esquema, **0 en común**):

  · `productType` — lo que Walmart ASIGNÓ al artículo después de publicarlo.
    Es la hoja fina: "Licuadoras", "Flores Artificiales", "Abanicos de Mano".
    Es lo que se guarda en `listings.category_id` porque es lo que el canal
    dice del producto.

  · La categoría del ESQUEMA — "Electrónicos", "Juguetes", "Cocina, Decoración
    y Otros". Es la que decide **qué campos exige** y la que indexa
    `channel.field_requirements`.

Cruzar una con la otra devuelve CERO filas sin dar ningún error — el mismo
defecto que en Amazon (`product_type` vs `category_id`) y que ya costó una
mañana. Por eso `categoria_esquema()` no adivina: resuelve con la MISMA
configuración que usa el publicador (`CATEGORIAS_AUTORIZADAS`), para que el
semáforo y el alta no puedan contradecirse.

Y un dato que el censo destapó: **105 de 235 artículos están en «Por Defecto»**,
que no es una categoría sino el hueco — Walmart no supo dónde ponerlos.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from services import supabase_db as sdb

log = logging.getLogger("omnicanal.walmart_panel")

CANAL = "walmart"
ESTADO_VIVO = "PUBLISHED"
SIN_CATEGORIA = "Por Defecto"

_SEL = """
    select l.sku::text as sku, p.wc_id, p.name as nombre,
           l.price, l.stock_own, l.status, l.situacion, l.listing_id, l.url,
           l.category_id
      from channel.listings l
      join core.products p on p.sku = l.sku
     where l.canal = %(canal)s
"""

_ORDEN = {
    "reciente": "l.updated_at desc nulls last",
    "precio_desc": "l.price desc nulls last",
    "precio_asc": "l.price asc nulls last",
    "stock_desc": "l.stock_own desc nulls last",
    "stock_asc": "l.stock_own asc nulls last",
}


def _normalizar(r: dict[str, Any]) -> dict[str, Any]:
    estado = (r.get("status") or "").upper()
    cat = r.get("category_id")
    return {
        "sku": r["sku"],
        "wc_id": r.get("wc_id"),
        "nombre": r.get("nombre") or r["sku"],
        "precio": float(r["price"]) if r.get("price") is not None else None,
        "precio_base": float(r["price"]) if r.get("price") is not None else None,
        # Walmart no devuelve stock en /v3/items: NULL es "no lo sabemos", que
        # es distinto de 0 ("agotado"). No se inventa.
        "stock": r.get("stock_own"),
        "estado": estado or "sin publicar",
        "situacion": estado or None,
        "categoria_id": cat,
        "categoria_path": ([{"id": cat, "nombre": cat}] if cat else []),
        "publicado": estado == ESTADO_VIVO,
        "item_id": r.get("listing_id"),      # wpid
        "url": r.get("url"),
        "full": None,
        "full_label": None,
        "origen": "db",
    }


def listar(page: int = 1, per_page: int = 40, search: str | None = None,
           solo_publicados: bool = False, orden: str = "reciente",
           estados: list[str] | None = None,
           skus_filtro: list[str] | None = None,
           solo_activas: bool = False) -> tuple[list[dict[str, Any]], int]:
    """
    Publicaciones de Walmart con los filtros de la pantalla. (items, total).

    `solo_activas` usa el criterio de `publicaciones_panel`, que aquí coincide
    con `ESTADO_VIVO` (`PUBLISHED`, 207 de 235). Se pide en vez de re-escribirse
    para que los dos filtros no puedan separarse.
    """
    where, params = [], {"canal": CANAL}
    if search:
        where.append("(l.sku::text ilike %(like)s or p.name ilike %(like)s)")
        params["like"] = f"%{search}%"
    if skus_filtro:
        where.append("l.sku::text = any(%(skus)s)")
        params["skus"] = list(skus_filtro)
    if solo_activas:
        from services import publicaciones_panel
        frag = publicaciones_panel.filtro_sql_activas(CANAL)
        if frag:
            where.append(frag[0])
            params.update(frag[1])
    elif solo_publicados or (estados and "publicado" in estados and "inactivo" not in estados):
        where.append("upper(l.status) = %(vivo)s")
        params["vivo"] = ESTADO_VIVO
    elif estados and "inactivo" in estados and "publicado" not in estados:
        where.append("upper(l.status) is distinct from %(vivo)s")
        params["vivo"] = ESTADO_VIVO

    filtro = (" and " + " and ".join(where)) if where else ""
    params["limit"] = per_page
    params["offset"] = max(0, (page - 1) * per_page)
    try:
        filas = sdb.fetch_all(
            f"{_SEL}{filtro} order by {_ORDEN.get(orden, _ORDEN['reciente'])} "
            f"limit %(limit)s offset %(offset)s", params)
        total = sdb.fetch_all(
            f"""select count(*) as n from channel.listings l
                  join core.products p on p.sku = l.sku
                 where l.canal = %(canal)s{filtro}""", params)
        return [_normalizar(f) for f in filas], int((total or [{}])[0].get("n") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("walmart_panel.listar falló: %s", exc)
        return [], 0


def contar_publicados() -> int:
    """TODAS las publicaciones del canal — mismo criterio que TikTok y Temu:
    un artículo despublicado es trabajo por destrabar, no algo que esconder."""
    try:
        r = sdb.fetch_all("select count(*) as n from channel.listings where canal=%(c)s",
                          {"c": CANAL})
        return int((r or [{}])[0].get("n") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("walmart_panel.contar_publicados falló: %s", exc)
        return 0


def resumen_estados() -> list[dict[str, Any]]:
    try:
        return [{"estado": f["status"], "publicaciones": int(f["n"])}
                for f in sdb.fetch_all(
                    """select status, count(*) n from channel.listings
                        where canal=%(c)s group by status order by n desc""",
                    {"c": CANAL})]
    except Exception as exc:  # noqa: BLE001
        log.warning("walmart_panel.resumen_estados falló: %s", exc)
        return []


def datos_de(sku: str) -> dict[str, Any] | None:
    """La publicación de Walmart de UN SKU, ya normalizada, o None."""
    try:
        filas = sdb.fetch_all(f"{_SEL} and l.sku = %(sku)s::citext limit 1",
                              {"canal": CANAL, "sku": sku})
        return _normalizar(filas[0]) if filas else None
    except Exception as exc:  # noqa: BLE001
        log.warning("walmart_panel.datos_de(%s) falló: %s", sku, exc)
        return None


# ── La categoría del ESQUEMA (la que decide qué campos se exigen) ────────────

def categoria_esquema(nombre: str | None, categoria_woo: str | None = None) -> str | None:
    """
    Qué categoría del esquema le toca a un producto — la que indexa
    `channel.field_requirements`.

    NO se deduce del `productType` (son taxonomías distintas, ver el encabezado):
    se resuelve con los MISMOS patrones que usa el publicador, importados de
    `scripts.publicar_walmart`. Si el panel y el alta usaran reglas distintas,
    el semáforo diría verde sobre unos campos y se publicarían otros.

    Devuelve None cuando ninguna categoría autorizada aplica: ese producto no se
    puede publicar todavía, y decirlo es más útil que asignarle una al azar.
    """
    texto = f"{nombre or ''} {categoria_woo or ''}"
    if not texto.strip():
        return None
    try:
        from scripts.publicar_walmart import CATEGORIAS_AUTORIZADAS
    except Exception as exc:  # noqa: BLE001
        log.warning("walmart_panel: no se pudo leer la config del publicador: %s", exc)
        return None
    for cfg in CATEGORIAS_AUTORIZADAS.values():
        patron = cfg.get("patron_titulo") or cfg.get("patron_categoria")
        if patron and re.search(patron, texto, re.I):
            return cfg.get("clave_visible")
    return None
