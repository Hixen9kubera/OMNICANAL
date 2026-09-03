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

import json
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


def prioridad(categorias: list[str] | None = None) -> list[dict[str, Any]]:
    """
    `enrich.market_categoria_prioridad_v` — una fila por subcategoría ACTIVA.

    De aquí salen el top 5 por venta y el bottom 5 por tráfico sin venta, y es la
    lista blanca del botón de refresco: si una categoría no está aquí, no es
    nuestra o no tiene publicación viva, y no hay por qué pagar por raspar.

    Trae también `tiene_ranking_ml` (¿ML publica más vendidos ahí?) y
    `dias_sin_captura`, que son los otros dos candados del botón.
    """
    sql = "SELECT * FROM enrich.market_categoria_prioridad_v"
    params: tuple = ()
    if categorias:
        sql += " WHERE categoria_id = ANY(%s)"
        params = (list(categorias),)
    sql += " ORDER BY pesos_30d DESC NULLS LAST"
    return supabase_db.fetch_all(sql, params)


def frescura(canal: str = "mercado_libre") -> dict[str, Any]:
    """
    De cuándo son los tres datos que el tab refresca solo: visitas, ventas y
    ranking. Una sola consulta porque el encabezado los muestra juntos.

    ⚠️ LAS TRES FECHAS NO SIGNIFICAN LO MISMO, y confundirlas es cómo se
    construye un medidor que miente en verde:

    · `visitas_medidas`  — `metrics_updated_at`. Es CUÁNDO SE MIDIERON. Desde
      v0.322.0 esa columna solo avanza si de verdad se escribió una visita o una
      unidad, así que no la mueve un upsert que no trajo dato.

    · `ranking_capturado` — `capturado_en`. Es CUÁNDO SE RASPÓ.

    · `ventas_hasta`     — `channel.sales_daily` NO tiene columna de "cuándo se
      trajo": solo el día que cubre. Así que esto es COBERTURA, no frescura, y
      la UI lo dice con esas palabras. A las 2 a.m. sin ventas todavía dirá
      "hasta ayer", que es literalmente cierto — el webhook escribe en segundos,
      pero un día sin ventas no genera fila. Poner aquí un `now()` disfrazado de
      frescura sería exactamente la mentira de la 0038.
    """
    sql = """
        SELECT
          (SELECT max(date) FROM channel.sales_daily
            WHERE canal = %s)                                  AS ventas_hasta,
          (SELECT max(metrics_updated_at) FROM enrich.market_listing_metrics
            WHERE canal = %s AND visits_30d IS NOT NULL)       AS visitas_medidas,
          (SELECT max(capturado_en) FROM enrich.market_bestsellers
            WHERE canal = %s)                                  AS ranking_capturado
    """
    filas = supabase_db.fetch_all(sql, (canal, canal, canal))
    return dict(filas[0]) if filas else {}


def movimiento_del_top(canal: str = "mercado_libre") -> dict[str, dict[str, Any]]:
    """
    `enrich.market_highlights` entera, indexada por categoría. Una sola consulta
    para todo el árbol; son ~1,133 filas de ~800 bytes.

    ── PARA QUÉ ────────────────────────────────────────────────────────────────
    Contesta las dos preguntas que la vista no podía contestar sola, y las dos
    salen GRATIS del sondeo diario de `/highlights`:

    1. **¿Se movió el top desde que lo capturamos?** La pantalla decía cuándo se
       raspó, pero no si eso sigue vigente. Medido el 1-sep-2026 en Cables de
       Audio y Video: capturado el 18-ago, y el top había cambiado ESE MISMO DÍA
       a las 15:24 — sólo 2 de 20 posiciones seguían iguales.

    2. **¿ML publica ranking ahí?** `vista()` sólo sabía lo que tenía guardado, y
       su propio comentario lo admitía: 174 de 176 subcategorías salían como "ML
       no publica" cuando en realidad SÍ tenían ranking; el mensaje mandaba a no
       reintentar justo donde había datos. `n = 0` lo resuelve, y nunca se
       escribe por un error de la llamada (ver 0041).
    """
    # `entradas` viene incluida: es lo que permite decir CUÁNTAS posiciones se
    # movieron en vez de sólo que algo se movió. Son ~800 KB para las 1,161
    # categorías y no salen del servidor — de aquí sólo viajan los conteos.
    sql = ("SELECT categoria_id, n, cambio_en, capturado_en, entradas "
           "FROM enrich.market_highlights WHERE canal = %s")
    return {f["categoria_id"]: dict(f)
            for f in supabase_db.fetch_all(sql, (canal,))}


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


def busqueda(termino: str, limite: int = 5,
             canal: str = "mercado_libre") -> list[dict[str, Any]]:
    """
    Resultados guardados de UN término.

    El texto del término ya no vive en la fila del resultado: desde la 0017 está
    UNA vez en `market_search_term` y el resultado lo referencia por FK. Es lo que
    garantiza que un término medido (una corrida de Apify, ~$0.007) lo reusen
    todos los SKUs que lo comparten sin volver a pagarlo.
    """
    filas = [dict(f) for f in supabase_db.fetch_all(
        "SELECT r.* FROM enrich.market_search_results r "
        "  JOIN enrich.market_search_term st ON st.id = r.termino_id "
        " WHERE st.termino = %s AND st.canal = %s "
        " ORDER BY r.posicion LIMIT %s", (termino, canal, int(limite)))]
    for f in filas:
        f.pop("termino_id", None)     # llave interna, no viaja al API
        for k in ("precio", "precio_lista", "rating"):
            if f.get(k) is not None:
                f[k] = float(f[k])
        f["es_nuestro"] = 1 if f.get("es_nuestro") else 0
    return filas


def terminos_medidos(canal: str = "mercado_libre") -> set[str]:
    """Los términos que YA se corrieron. Sale del catálogo, no de los resultados:
    un término puede estar medido y haber devuelto cero filas, y eso también
    cuenta como medido (no hay que volver a pagarlo)."""
    return {f["termino"] for f in supabase_db.fetch_all(
        "SELECT termino FROM enrich.market_search_term "
        " WHERE canal = %s AND medido_en IS NOT NULL", (canal,))}


def estado_termino(termino: str, canal: str = "mercado_libre") -> dict[str, Any] | None:
    """
    ¿Existe ese término y de cuándo es su medición? None si no está en el catálogo.

    El botón del panel lo necesita para DOS cosas distintas: la lista blanca —no
    se raspa una cadena arbitraria, que es dinero— y el candado de días. Devolver
    None y "medido hace 0 días" son respuestas distintas y llevan a códigos HTTP
    distintos (422 contra 409).
    """
    filas = supabase_db.fetch_all(
        "SELECT termino, origen, medido_en, resultados, "
        "       (now()::date - medido_en::date) AS dias "
        "  FROM enrich.market_search_term "
        " WHERE canal = %s AND lower(termino) = lower(%s) LIMIT 1", (canal, termino))
    return filas[0] if filas else None


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
    """Cuántos términos hay por categoría, en una sola consulta.

    Desde la 0017 `market_terms` es UNA fila por categoría con un array JSON, así
    que el conteo es la longitud del array y no un GROUP BY sobre 5,853 filas.
    """
    return {f["categoria_id"]: f["n"] for f in supabase_db.fetch_all(
        "SELECT categoria_id, jsonb_array_length(terminos)::int AS n "
        "FROM enrich.market_terms")}


def total_terminos(categoria_id: str) -> int:
    n = supabase_db.fetch_scalar(
        "SELECT jsonb_array_length(terminos) FROM enrich.market_terms "
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

    # El array se desempaqueta EN SQL con jsonb_array_elements_text + ordinality:
    # así el LIMIT sigue aplicándose en la base y la posición sale del índice del
    # array, que es donde vive el orden que publica ML.
    filas = [dict(f) for f in supabase_db.fetch_all(
        "SELECT e.ord::int AS posicion, e.termino "
        "  FROM enrich.market_terms t, "
        "       jsonb_array_elements_text(t.terminos) WITH ORDINALITY AS e(termino, ord) "
        " WHERE t.categoria_id = %s ORDER BY e.ord LIMIT %s",
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
    # Se deduplica por externo_id Y POR POSICIÓN. La PK es
    # (canal, categoria_id, nivel, posicion), y `_enriquecer_ranking`
    # SOBREESCRIBE la posición con la de /highlights: dos filas distintas pueden
    # terminar en la misma. Deduplicar sólo por id dejaba pasar el choque y el
    # INSERT reventaba.
    #
    # Costó una tanda entera el 1-sep-2026: "duplicate key ... (mercado_libre,
    # MLM455588, hoja, 9) already exists" mató las 20 categorías del lote.
    # Se conserva la PRIMERA de cada posición, que viene en el orden del ranking.
    listas, vistos, posiciones = [], set(), set()
    for f in filas:
        ident = f.get("externo_id")
        pos = f.get("posicion")
        if not ident or ident in vistos or pos is None or pos in posiciones:
            continue
        vistos.add(ident)
        posiciones.add(pos)
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
    # Un array ORDENADO, sin duplicados y conservando el orden que da ML: es la
    # única cosa que /trends publica (no hay volumen). El orden se respeta con
    # `posicion` cuando viene y con el de llegada cuando no.
    ordenados = sorted(
        ((t.get("posicion") or i, (t.get("termino") or t.get("keyword") or "").strip())
         for i, t in enumerate(terminos, start=1)),
        key=lambda x: x[0])
    lista, vistos = [], set()
    for _, termino in ordenados:
        if not termino or termino in vistos:
            continue
        vistos.add(termino)
        lista.append(termino)

    # Una sola fila por categoría: upsert, no delete+insert. La 0016 empaquetó
    # los términos en JSON justamente porque son gratis, masivos y solo se leen
    # en bloque (5,853 términos en 222 categorías).
    supabase_db.execute(
        "INSERT INTO enrich.market_terms (canal, categoria_id, terminos, capturado_en) "
        "VALUES (%s, %s, %s::jsonb, now()) "
        "ON CONFLICT (canal, categoria_id) DO UPDATE "
        "   SET terminos = EXCLUDED.terminos, capturado_en = EXCLUDED.capturado_en",
        (canal, categoria_id, json.dumps(lista, ensure_ascii=False)))
    log.info("market_terms %s/%s ← %s términos (JSON)", canal, categoria_id, len(lista))
    return len(lista)


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

    Desde la 0023 la vista deriva su base de channel.listings, así que un SKU
    recién publicado puede NO tener fila en market_sku_config todavía (su
    activo=true es el default de la vista). Por eso esto es un INSERT…ON
    CONFLICT y no un UPDATE: apagar una raíz también debe alcanzar a esos, y la
    decisión humana necesita fila donde persistir.

    Devuelve cuántas filas cambiaron. Es idempotente: el `WHERE … IS DISTINCT
    FROM` del UPDATE hace que correrlo dos veces no toque nada la segunda
    (las altas nuevas de la primera corrida ya quedaron con el valor pedido).
    """
    return supabase_db.execute(
        "INSERT INTO enrich.market_sku_config (sku, canal, activo, updated_at) "
        "SELECT v.sku, v.canal, %s, now() "
        "  FROM enrich.market_skus_v v "
        " WHERE v.raiz_id = %s AND v.canal = %s "
        "ON CONFLICT (sku, canal) DO UPDATE "
        "   SET activo = EXCLUDED.activo, updated_at = now() "
        " WHERE market_sku_config.activo IS DISTINCT FROM EXCLUDED.activo",
        (activo, raiz_id, canal))


def _id_de_termino(cur, termino: str, canal: str, origen: str | None = None,
                   medido: bool = False, resultados: int | None = None) -> int:
    """
    Id del término en el catálogo, creándolo si no existe. → `market_search_term.id`

    Es el único lugar donde nace un término. Concentrarlo aquí es lo que hace que
    el FK cumpla su promesa: dos SKUs que comparten término apuntan a la MISMA
    fila y su medición —que costó una corrida de Apify— se paga una vez.

    `medido` solo se marca cuando de verdad se corrió el buscador; no cuando se le
    asigna el término a un SKU.
    """
    cur.execute(
        "INSERT INTO enrich.market_search_term (canal, termino, origen, medido_en, resultados) "
        "VALUES (%s, %s, %s, CASE WHEN %s THEN now() ELSE NULL END, %s) "
        "ON CONFLICT (canal, termino) DO UPDATE SET "
        # coalesce al revés en `origen`: el que ya estaba manda, para que una
        # propuesta de IA posterior no borre el rastro de una corrección humana.
        "  origen     = COALESCE(enrich.market_search_term.origen, EXCLUDED.origen), "
        "  medido_en  = COALESCE(EXCLUDED.medido_en, enrich.market_search_term.medido_en), "
        "  resultados = COALESCE(EXCLUDED.resultados, enrich.market_search_term.resultados) "
        "RETURNING id",
        (canal, termino.strip(), origen, medido, resultados))
    return int(cur.fetchone()["id"])


def actualizar_termino(sku: str, termino: str, canal: str = CANAL_DEFAULT) -> bool:
    """Corrección manual del término de un SKU. Marca origen='manual' en la
    ASIGNACIÓN para que una propuesta posterior de la IA no la pise.

    INSERT…ON CONFLICT (no UPDATE) desde la 0023: un SKU derivado de
    channel.listings puede no tener fila de config aún, y la corrección la crea.
    El SELECT contra core.products conserva el contrato viejo: un SKU que no
    existe devuelve False (rowcount 0) en vez de reventar por la FK."""
    with supabase_db.get_cursor() as cur:
        tid = _id_de_termino(cur, termino, canal, origen="manual")
        cur.execute(
            "INSERT INTO enrich.market_sku_config "
            "      (sku, canal, termino_id, termino_origen, updated_at) "
            "SELECT p.sku, %s, %s, 'manual', now() "
            "  FROM core.products p WHERE p.sku = %s "
            "ON CONFLICT (sku, canal) DO UPDATE "
            "   SET termino_id = EXCLUDED.termino_id, "
            "       termino_origen = 'manual', updated_at = now()",
            (canal, tid, sku))
        return cur.rowcount > 0


def proponer_termino(sku: str, termino: str, canal: str = CANAL_DEFAULT) -> bool:
    """Término propuesto por la IA. NO pisa una corrección humana previa.

    Mismo INSERT…ON CONFLICT que `actualizar_termino`; el WHERE del UPDATE
    conserva la regla de siempre: 'manual' gana sobre cualquier propuesta."""
    with supabase_db.get_cursor() as cur:
        tid = _id_de_termino(cur, termino, canal, origen="ia")
        cur.execute(
            "INSERT INTO enrich.market_sku_config "
            "      (sku, canal, termino_id, termino_origen, updated_at) "
            "SELECT p.sku, %s, %s, 'ia', now() "
            "  FROM core.products p WHERE p.sku = %s "
            "ON CONFLICT (sku, canal) DO UPDATE "
            "   SET termino_id = EXCLUDED.termino_id, "
            "       termino_origen = COALESCE(market_sku_config.termino_origen, 'ia'), "
            "       updated_at = now() "
            " WHERE market_sku_config.termino_origen IS DISTINCT FROM 'manual'",
            (canal, tid, sku))
        return cur.rowcount > 0


# Columnas de enrich.market_search_results, en el orden del insert.
# `visitas_30d` va en la lista: la captura las pide por API antes de guardar
# (competencia_buscar_apify.py:121) y sin la columna ese trabajo se tiraba.
_COLS_SERP = ("termino_id", "posicion", "externo_id", "titulo", "precio", "imagen",
              "url", "seller", "rating", "visitas_30d", "es_nuestro", "sku_nuestro")


def reemplazar_busqueda(termino: str, periodo: str, filas: list[dict[str, Any]],
                        canal: str = CANAL_DEFAULT) -> int:
    """
    Reescribe los resultados de UN término medido con el buscador.

    Marca el término como medido AUNQUE venga vacío: un término que se corrió y
    no devolvió nada también está pagado, y sin la marca se volvería a correr en
    cada barrido. `periodo` se acepta por compatibilidad de firma; la tabla nueva
    no lo tiene (el momento lo dice `capturado_en` / `medido_en`).
    """
    listas, vistos = [], set()
    for f in filas:
        ident = f.get("externo_id")
        if not ident or ident in vistos:
            continue
        vistos.add(ident)
        listas.append(f)

    marcas = "(" + ",".join(["%s"] * len(_COLS_SERP)) + ")"
    with supabase_db.get_cursor() as cur:
        tid = _id_de_termino(cur, termino, canal, medido=True, resultados=len(listas))
        cur.execute("DELETE FROM enrich.market_search_results WHERE termino_id = %s", (tid,))
        for f in listas:
            d = {k: f.get(k) for k in _COLS_SERP}
            d["termino_id"] = tid
            d["es_nuestro"] = bool(f.get("es_nuestro"))
            cur.execute(
                f"INSERT INTO enrich.market_search_results ({','.join(_COLS_SERP)}) "
                f"VALUES {marcas}", tuple(d[k] for k in _COLS_SERP))
    log.info("market_search_results %s/%r ← %s filas", canal, termino, len(listas))
    return len(listas)


def guardar_publicaciones(filas: list[dict[str, Any]]) -> int:
    """
    Upsert de NUESTRAS publicaciones en `enrich.market_listing_metrics`.

    Una fila por (sku, canal, cuenta, periodo). El COALESCE del UPDATE es lo que
    permite refrescos PARCIALES: el paso de precios solo trae precio, el de
    visitas solo visitas, y ninguno debe borrar lo que escribió el otro.

    El precio que se guarda en `sale_price` es el que el comprador PAGA
    (`/items/{id}/sale_price`), no el de lista. Medido: ORG-0385-NEG tenía $598
    guardado cuando ML ya lo vendía en $399, y MES-0061-CAF $1,088 contra $599
    reales — con el de lista, la brecha contra el mercado sale al doble.
    """
    listos = [f for f in filas if f.get("sku") and f.get("cuenta")]
    if not listos:
        return 0
    n = 0
    with supabase_db.get_cursor() as cur:
        for f in listos:
            cur.execute(
                "INSERT INTO enrich.market_listing_metrics "
                "  (sku, canal, cuenta, periodo, listing_id, title, estado, "
                "   sale_price, list_price, visits_30d, units_30d, metrics_updated_at) "
                "VALUES (%s,%s,%s,date_trunc('month', now())::date,%s,%s,%s,%s,%s,%s,%s, now()) "
                "ON CONFLICT (sku, canal, cuenta, periodo) DO UPDATE SET "
                "  listing_id = COALESCE(EXCLUDED.listing_id, market_listing_metrics.listing_id), "
                "  title      = COALESCE(EXCLUDED.title,      market_listing_metrics.title), "
                "  estado     = COALESCE(EXCLUDED.estado,     market_listing_metrics.estado), "
                "  sale_price = COALESCE(EXCLUDED.sale_price, market_listing_metrics.sale_price), "
                "  list_price = COALESCE(EXCLUDED.list_price, market_listing_metrics.list_price), "
                "  visits_30d = COALESCE(EXCLUDED.visits_30d, market_listing_metrics.visits_30d), "
                "  units_30d  = COALESCE(EXCLUDED.units_30d,  market_listing_metrics.units_30d), "
                # La marca de tiempo SOLO avanza si de verdad llegó una medición.
                # Antes era `now()` a secas y eso dejaba una mentira en verde: una
                # corrida con el token de ML caído escribe las tres columnas en
                # NULL —el COALESCE las protege— pero estampaba la fila como
                # "medida hoy". El panel entero se veía fresco con datos viejos, y
                # cualquier cola de prioridad que se ordene por antigüedad se
                # ordenaría con esa mentira.
                "  metrics_updated_at = CASE WHEN EXCLUDED.visits_30d IS NOT NULL "
                "                              OR EXCLUDED.units_30d  IS NOT NULL "
                "                              OR EXCLUDED.sale_price IS NOT NULL "
                "                            THEN now() "
                "                            ELSE market_listing_metrics.metrics_updated_at END",
                (f["sku"], f.get("canal") or "mercado_libre", f["cuenta"],
                 f.get("ml_item_id") or f.get("listing_id"), f.get("titulo") or f.get("title"),
                 f.get("estado"), f.get("precio"), f.get("precio_lista"),
                 f.get("visitas_30d"), f.get("unidades_30d")))
            n += 1
    log.info("market_listing_metrics ← %s publicaciones", n)
    return n
