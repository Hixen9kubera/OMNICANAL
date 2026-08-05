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

from typing import Any

from services import supabase_db as sdb

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
