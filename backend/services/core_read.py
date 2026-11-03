"""
core_read.py — Lecturas del dominio CORE (maestro de productos) desde la BD
kubera (F5, flag SUPABASE_READ_CORE).

Gemelas de los lectores standalone de la tabla MySQL `productos`:
  - wc_de_sku      → pedidos_ml.resolver_producto (ruta CALIENTE: cada venta)
  - wc_id_de_sku   → costos.py (categoría ML vía wc_id)
  - buscar_wc_ids  → gemela del respaldo DB de woocommerce.py, SIN cablear:
    ese respaldo lee `productos` CONGELADO (5,381/7,151, nota 30-jul) y su
    camino primario es wp_db en vivo — se cablea en F6, cuando core.products
    (que sí absorbe Woo vía ETL) sustituya al respaldo completo.

Notas de traducción (MySQL productos → core.products):
  - nombre≡name, status_wc≡status, variaciones≡has_variations; sku es citext
    (case-insensitive como la collation de MySQL). LIKE → ILIKE.
  - `precio` y `stock_odoo` NO existen en core.products: ejemplos.py y los
    órdenes stock_/precio_ del listado quedan FUERA del flag (siguen en MySQL).
  - HUECO CONOCIDO: el seam Crear → core.products no existe aún (README aviso
    23-jul); un SKU recién creado aparece en kubera hasta el ETL de las 06:15.
    Por eso el llamador trata "None en kubera" como NO CONCLUYENTE y reconsulta
    MySQL — no es fallback por error, es la regla del dominio.
"""
from __future__ import annotations

import logging
from typing import Any

from services import supabase_db as sdb

log = logging.getLogger(__name__)

# Mismo contrato que _ORDEN_SQL de woocommerce.py; solo lo traducible.
ORDEN = {"reciente": "updated_at desc"}


def wc_de_sku(sku: str) -> dict[str, Any] | None:
    """{sku, wc_id, wc_parent_id} o None — gemela del lookup de pedidos_ml."""
    return sdb.fetch_one(
        "select sku, wc_id, wc_parent_id from core.products "
        "where sku = %s and wc_id is not null", (sku,))


def wc_id_de_sku(sku: str) -> int | None:
    v = sdb.fetch_scalar(
        "select wc_id from core.products where sku = %s", (sku,))
    return int(v) if v else None


def buscar_wc_ids(search: str | None, skus: list[str] | None,
                  estados_wc: list[str], orden: str,
                  page: int, per_page: int) -> tuple[list[int], int]:
    """(wc_ids, total) — gemela del respaldo DB de woocommerce.py. El llamador
    solo debe invocarla con orden traducible (ver ORDEN)."""
    where = ["wc_id is not null", "(status is null or status <> 'draft')"]
    args: list[Any] = []
    if search:
        like = f"%{search.strip()}%"
        where.append("(sku::text ilike %s or name ilike %s)")
        args += [like, like]
    if skus:
        terminos = [t.strip() for t in skus if t.strip()]
        if terminos:
            or_grupo = " or ".join(["(sku::text ilike %s or name ilike %s)"] * len(terminos))
            where.append(f"({or_grupo})")
            for t in terminos:
                like_t = f"%{t}%"
                args += [like_t, like_t]
    if estados_wc:
        where.append(f"status in ({','.join(['%s'] * len(estados_wc))})")
        args += estados_wc
    where_sql = " and ".join(where)
    orden_sql = ORDEN.get(orden, ORDEN["reciente"])
    total = int(sdb.fetch_scalar(
        f"select count(*) from core.products where {where_sql}", tuple(args)) or 0)
    if not total:
        return [], 0
    offset = (page - 1) * per_page
    rows = sdb.fetch_all(
        f"select wc_id from core.products where {where_sql} "
        f"order by {orden_sql} limit %s offset %s",
        tuple(args + [per_page, offset]))
    return [int(r["wc_id"]) for r in rows], total


def nombres_y_wc(skus: list[str]) -> dict[str, dict[str, Any]]:
    """
    { sku: {nombre, wc_id} } — gemela del lookup a la tabla maestra `productos`.

    kubera no solo la cubre, la corrige: `productos` tiene 5,381 SKUs con wc_id
    contra los 14,617 de aquí y, en los 332 en que discrepan, WordPress le da
    la razón a kubera en los 332 (el wc_id de MySQL apunta a un post que ya no
    existe — SKUs reciclados). Ese wc_id se usa para traer la foto y el título
    del producto: con el viejo se traía el del producto ANTERIOR.
    """
    salida: dict[str, dict[str, Any]] = {}
    for i in range(0, len(skus), 800):
        chunk = skus[i:i + 800]
        idx = {s.lower(): s for s in chunk}  # citext: la llave la pone el llamador
        for r in sdb.fetch_all(
            """select sku::text as sku, name, wc_id from core.products
                where sku = any(%s::citext[])""", (chunk,)):
            salida[idx.get(r["sku"].lower(), r["sku"])] = {
                "nombre": r.get("name") or "", "wc_id": r.get("wc_id")}
    return salida


# ── LA BAJA DEL CATÁLOGO (0080, PLAN MAESTRO 3.2, diseño de Eduardo del 2-nov) ──
# «Archivado» (`archivado_at`) es una decisión: el SKU ya no se trabaja ni se vende.
# «Publicado» se calcula (`core.sku_publicado`). El Publicador oculta lo archivado por
# omisión y deja ver aparte lo archivado que sigue vivo en un canal (la situación
# «incoherente» de `core.products_ciclo_v`).

_baja_avisada = False


def archivados_de(skus: list[str]) -> set[str] | None:
    """Los SKUs de `skus` dados de baja, en MAYÚSCULAS (citext). ``None`` = no se pudo
    saber (la 0080 todavía no está aplicada, o kubera no contestó): el llamador NO oculta
    nada. Es solo para no estorbar en la cola; ningún flujo decide con esto."""
    salida: set[str] = set()
    limpios = [str(s).strip() for s in skus if str(s or "").strip()]
    try:
        for i in range(0, len(limpios), 800):
            for r in sdb.fetch_all(
                """select upper(sku::text) as sku from core.products
                    where archivado_at is not null and sku = any(%s::citext[])""",
                    (limpios[i:i + 800],)):
                salida.add(r["sku"])
    except Exception as exc:  # noqa: BLE001
        # Entre el push del código y la 0080 esto falla en CADA listado: un aviso por
        # proceso basta (el resto a debug), para no llenar el log del Publicador.
        global _baja_avisada
        (log.debug if _baja_avisada else log.warning)(
            "no se pudo leer la baja del catálogo (no se oculta nada): %s", exc)
        _baja_avisada = True
        return None
    return salida


def archivados_vivos() -> list[str]:
    """Los SKUs archivados que siguen publicados (activa o pausada, por familia): lo que
    hay que bajar del canal o desarchivar. LANZA si no puede leer: el filtro da 503,
    nunca una lista vacía que parezca «no hay ninguno»."""
    return [r["sku"] for r in sdb.fetch_all(
        """select upper(sku::text) as sku from core.products_ciclo_v
            where archivado_at is not null and situacion = 'incoherente'
            order by 1""")]
