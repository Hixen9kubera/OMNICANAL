"""
costing_read.py — Lecturas del dominio COSTOS desde la BD kubera (F5, flag
SUPABASE_READ_COSTING).

Cada función devuelve EXACTAMENTE la misma forma que su gemela MySQL de
routers/crear.py; el router decide la fuente (flag) y hace el fallback a MySQL
ante cualquier error — apagar el flag = volver al instante.

Notas de traducción (MySQL → Postgres/kubera):
  - P4: `costing.costos_finales` es POR CANAL (PK sku+canal). El motor actual
    calcula un solo precio ML-céntrico, así que estas lecturas fijan
    canal='mercado_libre' — cuando el motor sea multi-canal, el llamador
    pasará el canal.
  - `productos.nombre` (MySQL) ≡ `core.products.name` (kubera): se alias-ea
    como `nombre` para que la forma no cambie.
  - LIKE de MySQL es case-insensitive (collation); el equivalente en Postgres
    es ILIKE.
  - Los logs de costos (`costos_logs`) NO viajan por aquí: la bitácora sigue
    leyéndose de MySQL en ambas rutas (es cosmética y su destino final es
    ops.process_log — pendiente de F5-bitácoras).
"""
from __future__ import annotations

from typing import Any

from services import supabase_db as sdb

CANAL = "mercado_libre"

# Mismo contrato de orden que _ORDEN_COSTOS del router (v = costos_validados)
ORDEN = {
    "reciente": "v.created_at DESC",
    "sku_asc": "v.sku ASC",
    "sku_desc": "v.sku DESC",
    "costo_desc": "v.costo_total DESC",
    "costo_asc": "v.costo_total ASC",
    "contenedor": "v.contenedor ASC, v.sku ASC",
}


def contenedores() -> list[dict]:
    rows = sdb.fetch_all(
        "select contenedor, count(*) as n from costing.costos_validados "
        "where contenedor is not null and contenedor <> '' "
        "group by contenedor order by contenedor")
    return [{"contenedor": r["contenedor"], "n": int(r["n"])} for r in rows]


def finales(sku: str) -> dict[str, Any] | None:
    return sdb.fetch_one(
        "select * from costing.costos_finales where sku = %s and canal = %s",
        (sku, CANAL))


def validados(sku: str) -> dict[str, Any] | None:
    return sdb.fetch_one(
        "select * from costing.costos_validados where sku = %s", (sku,))


def detalle(sku: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(finales, validados) del SKU — superset de columnas del par MySQL."""
    return finales(sku), validados(sku)


def pct_comision_categoria(cat_id: str) -> float | None:
    """La comisión más frecuente cacheada para un ml_cat_id (gemela de
    costos._comision_categoria_db). None si nunca se costeó esa categoría."""
    row = sdb.fetch_one(
        """select pct_comision from costing.costos_finales
           where ml_cat_id = %s and pct_comision > 0
           group by pct_comision order by count(*) desc limit 1""",
        (cat_id,))
    return float(row["pct_comision"]) if row and row.get("pct_comision") else None


def listado(page: int, per_page: int, search: str | None, contenedor: str | None,
            orden: str, skus_lista: list[str]) -> tuple[list[dict], int]:
    """(rows, total) con las MISMAS columnas/alias que el SELECT MySQL del router."""
    where, params = [], []
    if search:
        where.append("(v.sku ilike %s or p.name ilike %s)")
        params += [f"%{search}%", f"%{search}%"]
    if skus_lista:
        or_grupo = " or ".join(["(v.sku ilike %s or p.name ilike %s)"] * len(skus_lista))
        where.append(f"({or_grupo})")
        for t in skus_lista:
            like_t = f"%{t}%"
            params += [like_t, like_t]
    if contenedor:
        where.append("v.contenedor = %s")
        params.append(contenedor)
    where_sql = ("where " + " and ".join(where)) if where else ""
    orden_sql = ORDEN.get(orden, ORDEN["reciente"])

    total = sdb.fetch_scalar(
        f"select count(*) from costing.costos_validados v "
        f"left join core.products p on p.sku = v.sku {where_sql}",
        tuple(params)) or 0
    offset = (page - 1) * per_page
    rows = sdb.fetch_all(
        f"""select v.sku, p.name as nombre, v.contenedor,
                   v.largo, v.alto, v.ancho, v.peso,
                   v.costo_producto, v.costo_cbm, v.costo_total,
                   f.costo_unitario, f.precio_base, f.precio_sugerido,
                   f.costo_comision, f.costo_fee_envio, f.ml_cat_id
            from costing.costos_validados v
            left join core.products p on p.sku = v.sku
            left join costing.costos_finales f on f.sku = v.sku and f.canal = %s
            {where_sql} order by {orden_sql} limit %s offset %s""",
        tuple([CANAL] + params + [per_page, offset]))
    return rows, int(total)


# ── Lotes para la vista Crear Productos (paso 0, 12-ago-2026) ───────────────
# Gemelas de creacion._costos_por_sku / _contenedores_por_sku. Van en lotes de
# 800 como las originales: el límite de placeholders es el mismo aquí.

def costos_por_sku(skus: list[str]) -> dict[str, float]:
    """{ sku: costo_unitario } (respaldo costo_producto), como el par MySQL."""
    salida: dict[str, float] = {}
    for i in range(0, len(skus), 800):
        chunk = skus[i:i + 800]
        for r in sdb.fetch_all(
            """select sku::text as sku, costo_unitario, costo_producto
                 from costing.costos_finales
                where sku = any(%s::citext[]) and canal = %s""",
                (chunk, CANAL)):
            costo = r.get("costo_unitario") or r.get("costo_producto")
            if costo:
                salida[r["sku"]] = float(costo)
    return salida


def contenedores_por_sku(skus: list[str]) -> dict[str, str]:
    """{ sku: nº de contenedor } desde costing.costos_validados."""
    salida: dict[str, str] = {}
    for i in range(0, len(skus), 800):
        chunk = skus[i:i + 800]
        for r in sdb.fetch_all(
            """select sku::text as sku, contenedor
                 from costing.costos_validados
                where sku = any(%s::citext[])
                  and nullif(contenedor, '') is not null""", (chunk,)):
            if r.get("contenedor"):
                salida[r["sku"]] = r["contenedor"]
    return salida
