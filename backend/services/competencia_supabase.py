"""Lectura del módulo de Competencia desde la BD kubera (esquema `propuestas`).

POR QUÉ ESTE ARCHIVO
--------------------
El MVP guarda en SQLite (`backend/competencia.db`), que es local y con Railway no
sirve: su sistema de archivos es efímero, así que en producción el tab arrancaba
vacío. Los datos ya están en Supabase, en el esquema `propuestas`, y esta capa los
lee con la MISMA forma que devuelve `competencia_store` para que la vista y el
router no cambien.

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
Las vistas `propuestas.competencia_*_v` ya hacen el JOIN con las tablas del equipo
(solo lectura): el nombre de `core.products`, la categoría de
`channel.product_category` + `channel.categories`, el precio de `channel.listings`.
Lo que no tiene hogar en ese esquema vive en `propuestas`: el título por tienda
(channel.listings no lo guarda), `raiz_id` (channel.categories.path trae los
NOMBRES de los niveles pero no el id de cada uno, y la API de ML necesita el id) y
la imagen (viene de WooCommerce; enrich.product_media no cubre estos SKUs).
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
    sql = "SELECT * FROM propuestas.competencia_skus_v"
    if solo_activos:
        sql += " WHERE activo"
    sql += " ORDER BY sku"
    filas = supabase_db.fetch_all(sql)

    # `publicado_ml`, `ml_item_id` y `cuenta` no están en la vista: se derivan de
    # las publicaciones, que es donde viven de verdad.
    pubs: dict[str, list[dict[str, Any]]] = {}
    for p in supabase_db.fetch_all(
            "SELECT sku, cuenta, ml_item_id FROM propuestas.competencia_publicaciones_v"):
        pubs.setdefault(p["sku"], []).append(p)

    out = []
    for f in filas:
        d = dict(f)
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
    sql = "SELECT * FROM propuestas.competencia_publicaciones_v"
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
    sql = ("SELECT * FROM propuestas.competencia_rankings_categoria "
           "WHERE categoria_id = %s")
    params: list[Any] = [categoria_id]
    if nivel:
        sql += " AND nivel = %s"
        params.append(nivel)
    sql += " ORDER BY posicion LIMIT %s"
    params.append(int(limite))
    filas = [dict(f) for f in supabase_db.fetch_all(sql, tuple(params))]
    for f in filas:
        for k in ("precio", "precio_lista", "rating"):
            if f.get(k) is not None:
                f[k] = float(f[k])
        f["es_nuestro"] = 1 if f.get("es_nuestro") else 0
    return filas


def resultados(sku: str, tipo: str | None = None,
               limite: int | None = None) -> list[dict[str, Any]]:
    """Las búsquedas guardadas de un SKU: 'general' (descubrimiento) o 'titulo'."""
    sql = "SELECT * FROM propuestas.competencia_resultados WHERE sku = %s"
    params: list[Any] = [sku]
    if tipo:
        sql += " AND tipo = %s"
        params.append(tipo)
    # Mismo orden que el store local, para que los dos modos den lo mismo.
    sql += " ORDER BY tipo, posicion IS NULL, posicion"
    if limite:
        sql += " LIMIT %s"
        params.append(int(limite))
    filas = [dict(f) for f in supabase_db.fetch_all(sql, tuple(params))]
    for f in filas:
        for k in ("precio", "rating"):
            if f.get(k) is not None:
                f[k] = float(f[k])
        f["es_nuestro"] = 1 if f.get("es_nuestro") else 0
    return filas


def busqueda(termino: str, limite: int = 5) -> list[dict[str, Any]]:
    """Resultados guardados de UN término. La tabla es por término, no por SKU."""
    filas = [dict(f) for f in supabase_db.fetch_all(
        "SELECT * FROM propuestas.competencia_busquedas WHERE termino = %s "
        "ORDER BY posicion LIMIT %s", (termino, int(limite)))]
    for f in filas:
        for k in ("precio", "precio_lista", "rating"):
            if f.get(k) is not None:
                f[k] = float(f[k])
        f["es_nuestro"] = 1 if f.get("es_nuestro") else 0
    return filas


def terminos_medidos() -> set[str]:
    return {f["termino"] for f in supabase_db.fetch_all(
        "SELECT DISTINCT termino FROM propuestas.competencia_busquedas")}


def rankings_por_categoria() -> dict[tuple[str, str], list[dict[str, Any]]]:
    """
    TODO el ranking de una vez, agrupado por (categoria_id, nivel).

    La vista lo pedía categoría por categoría. Con ~900 subcategorías eso eran ~900
    viajes a Supabase por carga de página, y /vista pasaba de 150 s. Una consulta.
    """
    filas = supabase_db.fetch_all(
        "SELECT * FROM propuestas.competencia_rankings_categoria ORDER BY posicion")
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for f in filas:
        d = dict(f)
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
        "FROM propuestas.competencia_terminos_categoria GROUP BY 1")}


def total_terminos(categoria_id: str) -> int:
    n = supabase_db.fetch_scalar(
        "SELECT COUNT(*) FROM propuestas.competencia_terminos_categoria "
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
        "SELECT * FROM propuestas.competencia_terminos_categoria "
        "WHERE categoria_id = %s ORDER BY posicion LIMIT %s",
        (categoria_id, int(limite)))]
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
