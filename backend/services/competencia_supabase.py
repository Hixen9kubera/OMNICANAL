"""Lectura del módulo de Competencia desde la BD kubera (esquema `enrich.market_*`).

POR QUÉ ESTE ARCHIVO
--------------------
El MVP guarda en SQLite (`backend/competencia.db`), que es local y con Railway no
sirve: su sistema de archivos es efímero, así que en producción el tab arrancaba
vacío. Los datos viven en `enrich.market_*` (migrados desde el esquema puente
`propuestas` el 11-ago; PLAN_COMPETENCIA_v2.md) y esta capa los lee con la
MISMA forma que devuelve `competencia_store` para que la vista y el router no
cambien. Las vistas `enrich.market_*_v` se verificaron byte a byte contra las
de `propuestas` antes del switch.

POR QUÉ psycopg2 Y NO PostgREST
-------------------------------
PostgREST de este proyecto solo expone `public,graphql_public` (`db_schema`), así
que `propuestas` no es alcanzable por REST. Agregarlo cambiaría la superficie REST
de TODO el proyecto y esa es decisión del equipo, no de este módulo. Por eso se
entra por `supabase_db` (psycopg2), que necesita `SUPABASE_DB_URL`.

Si `SUPABASE_DB_URL` no está definida, `disponible()` devuelve False y el store cae
a SQLite. No es un error: es el modo local.

QUÉ SALE DE DÓNDE
-----------------
Las vistas `enrich.market_*_v` ya hacen el JOIN con las tablas del equipo
(solo lectura): el nombre de `core.products`, la categoría de
`channel.product_category` + `channel.categories`, el precio de `channel.listings`.
Lo que es foto del periodo vive en `enrich.market_listing_metrics` (título por
tienda, sale_price, estado del listing, visitas); la raíz sale de
`channel.categories.root_id` (backfill desde wp_ml_categorias) y la imagen de
`enrich.product_media` kind='wc'.
"""
from __future__ import annotations

import logging
from typing import Any

from services import supabase_db

log = logging.getLogger("omnicanal.competencia.supabase")

# Mismo separador que usa channel.categories.path: U+203A, no '>'.
SEP_RUTA = " › "


def disponible() -> bool:
    """Hay conexión a la BD kubera. False = modo local (SQLite)."""
    return supabase_db.disponible()


def _niveles(ruta: str | None) -> dict[str, str | None]:
    """`cat1..cat4` a partir del path. La vista no los trae desglosados."""
    partes = [p.strip() for p in (ruta or "").split(SEP_RUTA) if p.strip()]
    return {f"cat{i}": (partes[i - 1] if len(partes) >= i else None) for i in range(1, 5)}


def listar_skus(solo_activos: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM enrich.market_skus_v"
    if solo_activos:
        sql += " WHERE activo"
    sql += " ORDER BY sku"
    filas = supabase_db.fetch_all(sql)

    # `publicado_ml`, `ml_item_id` y `cuenta` no están en la vista: se derivan de
    # las publicaciones, que es donde viven de verdad.
    pubs: dict[str, list[dict[str, Any]]] = {}
    for p in supabase_db.fetch_all(
            "SELECT sku, cuenta, ml_item_id FROM enrich.market_publicaciones_v"):
        pubs.setdefault(p["sku"], []).append(p)

    out = []
    for f in filas:
        d = dict(f)
        # market_skus_v ya es multicanal; `canal` se expondra en el API cuando
        # sea a proposito, no como efecto del switch (fidelidad del diff).
        d.pop("canal", None)
        d.update(_niveles(d.get("ruta")))
        mis = pubs.get(d["sku"]) or []
        # La de BEKURA es la de referencia si existe, como en el store local.
        ref = next((p for p in mis if p["cuenta"] == "BEKURA"), mis[0] if mis else None)
        d["ml_item_id"] = (ref or {}).get("ml_item_id")
        d["cuenta"] = (ref or {}).get("cuenta")
        d["publicado_ml"] = 1 if mis else 0
        d["origen_nombre"] = "supabase"
        d["activo"] = 1 if d.get("activo") else 0
        out.append(d)
    return out


def publicaciones(sku: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM enrich.market_publicaciones_v"
    params: tuple = ()
    if sku:
        sql += " WHERE sku = %s"
        params = (sku,)
    sql += " ORDER BY sku, cuenta"
    filas = [dict(f) for f in supabase_db.fetch_all(sql, params)]
    for f in filas:
        vis, uni = f.get("visitas_30d"), f.get("unidades_30d")
        # Con 0 visitas la conversión es INDEFINIDA, no 0%.
        f["conversion_30d"] = round(uni / vis * 100, 2) if vis and uni is not None else None
        f["actualizado_en"] = str(f.get("periodo") or "")
        if f.get("precio") is not None:
            f["precio"] = float(f["precio"])
    return filas


def ranking_categoria(categoria_id: str, nivel: str | None = None,
                      limite: int = 10) -> list[dict[str, Any]]:
    sql = ("SELECT * FROM enrich.market_bestsellers "
           "WHERE categoria_id = %s")
    params: list[Any] = [categoria_id]
    if nivel:
        sql += " AND nivel = %s"
        params.append(nivel)
    sql += " ORDER BY posicion LIMIT %s"
    params.append(int(limite))
    filas = [dict(f) for f in supabase_db.fetch_all(sql, tuple(params))]
    for f in filas:
        f.pop("canal", None)          # multicanal en la tabla, no en el API aun
        for k in ("precio", "precio_lista", "rating"):
            if f.get(k) is not None:
                f[k] = float(f[k])
        f["es_nuestro"] = 1 if f.get("es_nuestro") else 0
    return filas


# resultados() PODADO (paso 6): era la ultima lectura de `propuestas`
# (competencia_resultados). Su unico consumidor era GET /detalle, tambien
# podado. Este modulo ya lee 100% enrich.market_*.


def busqueda(termino: str, limite: int = 5) -> list[dict[str, Any]]:
    """Resultados guardados de UN término. La tabla es por término, no por SKU."""
    filas = [dict(f) for f in supabase_db.fetch_all(
        "SELECT * FROM enrich.market_search_results WHERE termino = %s "
        "ORDER BY posicion LIMIT %s", (termino, int(limite)))]
    for f in filas:
        f.pop("canal", None)          # multicanal en la tabla, no en el API aun
        for k in ("precio", "precio_lista", "rating"):
            if f.get(k) is not None:
                f[k] = float(f[k])
        f["es_nuestro"] = 1 if f.get("es_nuestro") else 0
    return filas


def terminos_medidos() -> set[str]:
    return {f["termino"] for f in supabase_db.fetch_all(
        "SELECT DISTINCT termino FROM enrich.market_search_results")}


def rankings_por_categoria() -> dict[tuple[str, str], list[dict[str, Any]]]:
    """
    TODO el ranking de una vez, agrupado por (categoria_id, nivel).

    La vista lo pedía categoría por categoría. Con ~900 subcategorías eso eran ~900
    viajes a Supabase por carga de página, y /vista pasaba de 150 s. Una consulta.
    """
    filas = supabase_db.fetch_all(
        "SELECT * FROM enrich.market_bestsellers ORDER BY posicion")
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for f in filas:
        d = dict(f)
        d.pop("canal", None)          # multicanal en la tabla, no en el API aun
        for k in ("precio", "precio_lista", "rating"):
            if d.get(k) is not None:
                d[k] = float(d[k])
        d["es_nuestro"] = 1 if d.get("es_nuestro") else 0
        out.setdefault((d["categoria_id"], d["nivel"]), []).append(d)
    return out


def conteo_terminos() -> dict[str, int]:
    """Cuántos términos hay por categoría, en una sola consulta."""
    return {f["categoria_id"]: f["n"] for f in supabase_db.fetch_all(
        "SELECT categoria_id, COUNT(*)::int AS n "
        "FROM enrich.market_terms GROUP BY 1")}


def total_terminos(categoria_id: str) -> int:
    n = supabase_db.fetch_scalar(
        "SELECT COUNT(*) FROM enrich.market_terms "
        "WHERE categoria_id = %s", (categoria_id,))
    return int(n or 0)


def terminos_categoria(categoria_id: str,
                       titulos_por_tienda: dict[str, str] | None = None,
                       limite: int = 20) -> list[dict[str, Any]]:
    """
    Igual que el store local: la cobertura se mide POR TIENDA porque el título lo
    es. El cruce se hace en Python y no en SQL para reusar exactamente la misma
    función de coincidencia y que los dos modos den el mismo resultado.
    """
    from services import competencia_store

    filas = [dict(f) for f in supabase_db.fetch_all(
        "SELECT * FROM enrich.market_terms "
        "WHERE categoria_id = %s ORDER BY posicion LIMIT %s",
        (categoria_id, int(limite)))]
    for f in filas:
        f.pop("canal", None)          # multicanal en la tabla, no en el API aun
    porv = {k: (v or "").lower() for k, v in (titulos_por_tienda or {}).items() if v}
    for f in filas:
        if not porv:
            f["cubierto"], f["cubierto_por"] = None, []
            continue
        quienes = [c for c, t in porv.items()
                   if competencia_store._cubre(f["termino"], [t])]
        f["cubierto"] = bool(quienes)
        f["cubierto_por"] = quienes
    return filas


# ── ESCRITURA ────────────────────────────────────────────────────────────────
# Antes la captura escribía en SQLite y un script aparte (`competencia_subir.py`)
# empujaba la foto completa a `propuestas` con `delete from` + reinsert. Las dos
# piezas se retiraron: SQLite era invisible en Railway (FS efímero) y el reinsert
# completo, apuntado a `enrich.market_*`, habría borrado las 15,307 filas
# migradas para reemplazarlas con lo que hubiera en el disco de quien corriera el
# script. Ahora la captura escribe aquí, directo y ACOTADO a lo que recapturó.

CANAL_DEFAULT = "mercado_libre"

# Columnas de enrich.market_bestsellers, en el orden del insert. `periodo` y
# `descuento` NO existen ahí: el periodo lo dice `capturado_en` y el descuento la
# UI lo infiere de precio_lista > precio (ver la migración 0011).
_COLS_BEST = ("canal", "categoria_id", "nivel", "posicion", "externo_id",
              "id_pagina", "tipo", "titulo", "precio", "precio_lista",
              "vendidos", "rating", "reviews", "seller", "imagen", "url",
              "visitas_30d", "item_categoria_id", "item_categoria_nombre",
              "es_nuestro", "sku_nuestro")


def reemplazar_ranking(categoria_id: str, nivel: str, periodo: str,
                       filas: list[dict[str, Any]],
                       canal: str = CANAL_DEFAULT) -> int:
    """
    Reescribe el top de UNA (canal, categoría, nivel). Nada más.

    El borrado va acotado a esa llave y en la MISMA transacción que el insert: si
    el insert falla, el top viejo sigue ahí. Recapturar Hogar no puede tocar
    Herramientas.

    `periodo` se acepta por compatibilidad con la firma del store y no se
    escribe: la tabla nueva no tiene esa columna.
    """
    listas, vistos = [], set()
    for f in filas:
        ident = f.get("externo_id")
        if not ident or ident in vistos or f.get("posicion") is None:
            continue
        vistos.add(ident)
        d = {k: f.get(k) for k in _COLS_BEST}
        d.update(canal=canal, categoria_id=categoria_id, nivel=nivel)
        d["es_nuestro"] = bool(f.get("es_nuestro"))
        listas.append(tuple(d[k] for k in _COLS_BEST))

    marcas = "(" + ",".join(["%s"] * len(_COLS_BEST)) + ")"
    with supabase_db.get_cursor() as cur:
        cur.execute("DELETE FROM enrich.market_bestsellers "
                    "WHERE canal = %s AND categoria_id = %s AND nivel = %s",
                    (canal, categoria_id, nivel))
        for f in listas:
            cur.execute(
                f"INSERT INTO enrich.market_bestsellers ({','.join(_COLS_BEST)}) "
                f"VALUES {marcas}", f)
    log.info("market_bestsellers %s/%s/%s ← %s filas", canal, categoria_id, nivel,
             len(listas))
    return len(listas)


def reemplazar_terminos(categoria_id: str, periodo: str,
                        terminos: list[dict[str, Any]],
                        canal: str = CANAL_DEFAULT) -> int:
    """
    Reescribe los términos más buscados de UNA (canal, categoría).

    `url` no se escribe: la tabla nueva la descartó (se raspaba y nadie la leía).
    `periodo` se acepta por compatibilidad de firma y tampoco se escribe.
    """
    listas, vistos = [], set()
    for i, t in enumerate(terminos, start=1):
        termino = (t.get("termino") or t.get("keyword") or "").strip()
        if not termino or termino in vistos:
            continue
        vistos.add(termino)
        listas.append((canal, categoria_id, t.get("posicion") or i, termino))

    with supabase_db.get_cursor() as cur:
        cur.execute("DELETE FROM enrich.market_terms "
                    "WHERE canal = %s AND categoria_id = %s", (canal, categoria_id))
        for f in listas:
            cur.execute(
                "INSERT INTO enrich.market_terms (canal, categoria_id, posicion, termino) "
                "VALUES (%s,%s,%s,%s)", f)
    log.info("market_terms %s/%s ← %s términos", canal, categoria_id, len(listas))
    return len(listas)


def activar_raiz(raiz_id: str, activo: bool = True,
                 canal: str = CANAL_DEFAULT) -> int:
    """
    Prende (o apaga) los SKUs de una categoría RAÍZ en `market_sku_config`.

    Es lo que hace que una categoría padre "exista" para el módulo: la captura y
    la vista parten de `listar_skus()`, que filtra `WHERE activo`. Un SKU
    inactivo está en la tabla pero es invisible — por eso las 1,584 filas rendían
    solo 393 SKUs en pantalla.

    La raíz sale de `market_skus_v` (que la resuelve con
    `channel.categories.root_id`), no de una columna propia: así una categoría
    reclasificada por ML no deja SKUs prendidos en la raíz vieja.

    Devuelve cuántas filas cambiaron. Es idempotente: el `AND activo IS DISTINCT
    FROM` hace que correrlo dos veces no toque nada la segunda.
    """
    return supabase_db.execute(
        "UPDATE enrich.market_sku_config c "
        "   SET activo = %s, updated_at = now() "
        "  FROM enrich.market_skus_v v "
        " WHERE v.sku = c.sku AND v.canal = c.canal "
        "   AND v.raiz_id = %s AND c.canal = %s "
        "   AND c.activo IS DISTINCT FROM %s",
        (activo, raiz_id, canal, activo))
