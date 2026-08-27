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

from services import channel_read
from services import supabase_db as sdb

CANAL = "mercado_libre"

# Mismo contrato de orden que _ORDEN_COSTOS del router.
# El SKU sale de `core.products` (p), que desde v0.213.0 es el lado izquierdo del
# listado; lo de `costos_validados` (v) puede venir NULL, de ahí los NULLS LAST:
# sin ellos, Postgres pone los NULL primero en DESC y los SKUs sin costear
# tapaban el listado por defecto.
ORDEN = {
    "reciente": "v.created_at DESC NULLS LAST, p.sku ASC",
    "sku_asc": "p.sku ASC",
    "sku_desc": "p.sku DESC",
    "costo_desc": "v.costo_total DESC NULLS LAST",
    "costo_asc": "v.costo_total ASC NULLS LAST",
    "contenedor": "v.contenedor ASC NULLS LAST, p.sku ASC",
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


def bloqueado(sku: str) -> dict[str, Any] | None:
    """
    ``{revisado_at, revisado_por}`` si el costo del SKU esta VALIDADO, o None.

    Un costo validado se reconstruyo a mano desde el packing list y no debe
    moverse: el candado real vive en el UPSERT
    (``costing_mirror.upsert_validados``); esto es para poder AVISARLO en la
    pantalla en vez de que el usuario crea que guardo y no paso nada.
    """
    return sdb.fetch_one(
        "select revisado_at, revisado_por from costing.costos_validados "
        " where sku = %s and revisado_at is not null", (sku,))


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


# "Publicado en Mercado Libre" NO se redacta aquí: se pide. La insignia de esta
# tabla, su chip "Solo publicados en ML" y el validador de costos tienen que
# contestar lo MISMO, y cuando cada uno tenía su propio WHERE dejaron de
# hacerlo: la insignia decía "no publicado" de 76 SKUs que el validador sí
# aceptaba. La definición —y el porqué de `situacion` sobre `status`, y el
# porqué de dejar fuera `under_review`— vive en channel_read.
_EXISTE_PUB_ML = channel_read.sql_publicado_ml("p.sku")


def listado(page: int, per_page: int, search: str | None, contenedor: str | None,
            orden: str, skus_lista: list[str],
            sin_costo: bool = False,
            revisado: str | None = None,
            solo_publicados_ml: bool = False) -> tuple[list[dict], int]:
    """
    (rows, total) con las MISMAS columnas/alias que el SELECT MySQL del router.

    Desde v0.213.0 el lado izquierdo es `core.products`, no `costos_validados`.
    Antes la tabla nacía de costos_validados, así que un SKU sin fila ahí era
    INVISIBLE en la pantalla de Costos aunque estuviera publicado y vendiendo —
    y como la pantalla es el único lugar donde se captura, no había manera de
    darlo de alta desde ahí. Medido el 18-ago-2026: 123 SKUs con ventas en 60
    días ($469,546) no aparecían, y 6,461 productos del catálogo no tienen fila
    de costo. Con el LEFT JOIN salen en blanco y se pueden capturar.

    El cruce no pierde nada: los 15,837 de costos_validados tienen producto en
    core.products (verificado, 0 huérfanos).

    `sin_costo=True` deja solo los que NO tienen fila de costo — el filtro para
    trabajar el hueco.
    """
    where, params = [], []
    if search:
        where.append("(p.sku ilike %s or p.name ilike %s)")
        params += [f"%{search}%", f"%{search}%"]
    if skus_lista:
        or_grupo = " or ".join(["(p.sku ilike %s or p.name ilike %s)"] * len(skus_lista))
        where.append(f"({or_grupo})")
        for t in skus_lista:
            like_t = f"%{t}%"
            params += [like_t, like_t]
    if contenedor:
        # Filtrar por contenedor implica tener fila de costo: el contenedor vive ahí.
        where.append("v.contenedor = %s")
        params.append(contenedor)
    if sin_costo:
        where.append("v.sku is null")
    # Marca de revisión (0032). `movido` son los que se tocaron DESPUÉS de
    # revisarse: no es un error, es un aviso de que hay que volver a mirarlos.
    if revisado == "si":
        where.append("v.revisado_at is not null")
    elif revisado == "no":
        where.append("v.sku is not null and v.revisado_at is null")
    elif revisado == "movido":
        where.append("v.revisado_at is not null and v.updated_at > v.revisado_at")
    if solo_publicados_ml:
        where.append(_EXISTE_PUB_ML)
    where_sql = ("where " + " and ".join(where)) if where else ""
    orden_sql = ORDEN.get(orden, ORDEN["reciente"])

    total = sdb.fetch_scalar(
        f"select count(*) from core.products p "
        f"left join costing.costos_validados v on v.sku = p.sku {where_sql}",
        tuple(params)) or 0
    offset = (page - 1) * per_page
    rows = sdb.fetch_all(
        f"""select p.sku, p.name as nombre, v.contenedor,
                   v.largo, v.alto, v.ancho, v.peso,
                   v.costo_producto, v.costo_cbm, v.costo_total,
                   f.costo_unitario, f.precio_base, f.precio_sugerido,
                   f.costo_comision, f.costo_fee_envio, f.ml_cat_id,
                   v.revisado_at, v.revisado_por,
                   (v.revisado_at is not null and v.updated_at > v.revisado_at)
                     as revision_movida,
                   {_EXISTE_PUB_ML} as publicado_ml
            from core.products p
            left join costing.costos_validados v on v.sku = p.sku
            left join costing.costos_finales f on f.sku = p.sku and f.canal = %s
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
        idx = {s.lower(): s for s in chunk}  # citext: la llave la pone el llamador
        for r in sdb.fetch_all(
            """select sku::text as sku, costo_unitario, costo_producto
                 from costing.costos_finales
                where sku = any(%s::citext[]) and canal = %s""",
                (chunk, CANAL)):
            costo = r.get("costo_unitario") or r.get("costo_producto")
            if costo:
                salida[idx.get(r["sku"].lower(), r["sku"])] = float(costo)
    return salida


def contenedores_por_sku(skus: list[str]) -> dict[str, str]:
    """{ sku: nº de contenedor } desde costing.costos_validados."""
    salida: dict[str, str] = {}
    for i in range(0, len(skus), 800):
        chunk = skus[i:i + 800]
        idx = {s.lower(): s for s in chunk}
        for r in sdb.fetch_all(
            """select sku::text as sku, contenedor
                 from costing.costos_validados
                where sku = any(%s::citext[])
                  and nullif(contenedor, '') is not null""", (chunk,)):
            if r.get("contenedor"):
                salida[idx.get(r["sku"].lower(), r["sku"])] = r["contenedor"]
    return salida


def revisados_por_sku(skus: list[str]) -> dict[str, dict[str, Any]]:
    """
    ``{ sku: {revisado_at, revisado_por, movida} }`` para los SKUs YA revisados.

    Gemela por lotes de `contenedores_por_sku`: mismos 800 por tanda (el límite
    de placeholders es el mismo) y misma forma de índice para el citext.

    Devuelve SOLO los revisados. Un SKU ausente del dict = sin revisar, que es
    el caso mayoritario (15,838 de 15,838 al abrir la migración 0032) y que no
    tiene sentido acarrear como miles de valores nulos hasta el navegador.
    """
    salida: dict[str, dict[str, Any]] = {}
    for i in range(0, len(skus), 800):
        chunk = skus[i:i + 800]
        idx = {s.lower(): s for s in chunk}
        for r in sdb.fetch_all(
            """select sku::text as sku, revisado_at, revisado_por,
                      (updated_at > revisado_at) as movida
                 from costing.costos_validados
                where sku = any(%s::citext[]) and revisado_at is not null""",
                (chunk,)):
            salida[idx.get(r["sku"].lower(), r["sku"])] = {
                "revisado_at": r["revisado_at"],
                "revisado_por": r.get("revisado_por"),
                "movida": bool(r.get("movida")),
            }
    return salida


def validados_de(skus: list[str]) -> dict[str, dict[str, Any]]:
    """
    ``{ sku: {nombre, contenedor, costo_*, dimensiones, revisado_at, ...} }``.

    El costo GUARDADO de un lote de SKUs, con el nombre del producto pegado.
    Es lo que el validador de publicados necesita para tres cosas a la vez:
    contrastar el costo nuevo contra el viejo, saber si el candado de COSTO
    VALIDADO ya está puesto (``revisado_at``), y conservar el costo de producto
    cuando el packing list viene sin precios.

    Sale de `core.products` por la izquierda, igual que el listado: un SKU sin
    fila de costo tiene que aparecer con todo en nulo, no desaparecer.
    """
    salida: dict[str, dict[str, Any]] = {}
    for i in range(0, len(skus), 800):
        chunk = [str(s) for s in skus[i:i + 800] if s]
        if not chunk:
            continue
        idx = {s.lower(): s for s in chunk}
        for r in sdb.fetch_all(
            """select p.sku::text as sku, p.name as nombre, v.contenedor,
                      v.largo, v.alto, v.ancho, v.peso,
                      v.costo_producto, v.costo_cbm, v.costo_total,
                      v.revisado_at, v.revisado_por,
                      (v.sku is not null) as tiene_costo
                 from core.products p
                 left join costing.costos_validados v on v.sku = p.sku
                where p.sku = any(%s::citext[])""", (chunk,)):
            salida[idx.get(r["sku"].lower(), r["sku"])] = dict(r)
    return salida


def costos_todos() -> dict[str, float]:
    """
    { sku: costo_unitario } (respaldo costo_producto) de TODO el catálogo.

    Gemela de `sync_woo._costos_finales`, que es la que EMPUJA el costo a la
    meta del producto en WooCommerce. Leerlo del espejo congelado no daría un
    dato viejo y ya: al comparar contra Woo vería una diferencia y le
    ESCRIBIRÍA el valor viejo encima, deshaciendo cada recálculo del panel.
    """
    salida: dict[str, float] = {}
    for r in sdb.fetch_all(
        """select sku::text as sku, costo_unitario, costo_producto
             from costing.costos_finales where canal = %s""", (CANAL,)):
        costo = r.get("costo_unitario") or r.get("costo_producto")
        if costo:
            salida[r["sku"]] = round(float(costo), 2)
    return salida


def precios_de(skus: list[str]) -> dict[str, dict[str, Any]]:
    """{ sku: {precio_sugerido, precio_base, ml_cat_id} } para el listado ML."""
    salida: dict[str, dict[str, Any]] = {}
    for i in range(0, len(skus), 800):
        chunk = skus[i:i + 800]
        idx = {s.lower(): s for s in chunk}
        for r in sdb.fetch_all(
            """select sku::text as sku, precio_sugerido, precio_base, ml_cat_id
                 from costing.costos_finales
                where sku = any(%s::citext[]) and canal = %s""", (chunk, CANAL)):
            salida[idx.get(r["sku"].lower(), r["sku"])] = {
                "precio_sugerido": r.get("precio_sugerido"),
                "precio_base": r.get("precio_base"),
                "ml_cat_id": r.get("ml_cat_id")}
    return salida
