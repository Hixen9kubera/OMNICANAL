"""
competencia_store.py — Persistencia del módulo de Competencia.

Vive en el esquema `competencia` de la BD kubera (Supabase), aplicado por
`supabase/migrations/0008_competencia.sql`.

Medio: **psycopg2 directo** vía `services/supabase_db.py`, no PostgREST. Es la
convención del repo para los esquemas no-`public` (core/channel/costing/ops), y
además PostgREST solo expone `public` — un `competencia.*` por REST daría 404.

SIN HISTÓRICO, por decisión de producto: cada corrida BORRA los resultados del
SKU y los reescribe. `competencia.resultados` es la foto del mes vigente, no una
serie. Ese borrado está concentrado en `reemplazar_resultados()`: si algún día se
quiere historial, ese es el único lugar que cambia.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from services import supabase_db

log = logging.getLogger("omnicanal.competencia.store")

_LOTE = 200   # filas por executemany


def disponible() -> bool:
    return supabase_db.disponible()


def periodo_actual(hoy: date | None = None) -> date:
    """El periodo mensual: siempre el primer día del mes."""
    return (hoy or date.today()).replace(day=1)


# ── competencia.skus ─────────────────────────────────────────────────────────

_SQL_UPSERT_SKU = """
    INSERT INTO competencia.skus
      (sku, nombre, categoria_id, categoria_nombre, ml_item_id, cuenta,
       termino_general, termino_origen, activo, actualizado_en)
    VALUES (%(sku)s, %(nombre)s, %(categoria_id)s, %(categoria_nombre)s,
            %(ml_item_id)s, %(cuenta)s, %(termino_general)s,
            %(termino_origen)s, %(activo)s, now())
    ON CONFLICT (sku) DO UPDATE SET
      nombre           = EXCLUDED.nombre,
      categoria_id     = COALESCE(EXCLUDED.categoria_id, competencia.skus.categoria_id),
      categoria_nombre = COALESCE(EXCLUDED.categoria_nombre, competencia.skus.categoria_nombre),
      ml_item_id       = COALESCE(EXCLUDED.ml_item_id, competencia.skus.ml_item_id),
      cuenta           = COALESCE(EXCLUDED.cuenta, competencia.skus.cuenta),
      -- El término NO se pisa si ya lo puso una persona: la corrección manual
      -- gana sobre cualquier propuesta posterior de la IA.
      termino_general  = CASE
                            WHEN competencia.skus.termino_origen = 'manual'
                              THEN competencia.skus.termino_general
                            ELSE COALESCE(EXCLUDED.termino_general,
                                          competencia.skus.termino_general)
                         END,
      termino_origen   = CASE
                            WHEN competencia.skus.termino_origen = 'manual'
                              THEN 'manual'
                            ELSE EXCLUDED.termino_origen
                         END,
      activo           = EXCLUDED.activo,
      actualizado_en   = now()
"""

_CAMPOS_SKU = ("sku", "nombre", "categoria_id", "categoria_nombre", "ml_item_id",
               "cuenta", "termino_general", "termino_origen", "activo")


def guardar_skus(skus: list[dict[str, Any]]) -> int:
    """Alta/actualización de los SKUs vigilados."""
    filas = []
    for s in skus:
        if not s.get("sku") or not s.get("nombre"):
            continue
        f = {k: s.get(k) for k in _CAMPOS_SKU}
        f["termino_origen"] = s.get("termino_origen") or "ia"
        f["activo"] = s.get("activo", True)
        filas.append(f)
    if not filas:
        return 0
    try:
        with supabase_db.get_cursor() as cur:
            cur.executemany(_SQL_UPSERT_SKU, filas)
        return len(filas)
    except Exception as exc:  # noqa: BLE001
        log.error("guardar_skus falló: %s", exc)
        return 0


def listar_skus(solo_activos: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM competencia.skus"
    if solo_activos:
        sql += " WHERE activo"
    sql += " ORDER BY categoria_nombre NULLS LAST, sku"
    try:
        return supabase_db.fetch_all(sql)
    except Exception as exc:  # noqa: BLE001
        log.error("listar_skus falló: %s", exc)
        return []


def actualizar_termino(sku: str, termino: str) -> bool:
    """Corrección manual del término general. Marca origen='manual' para que la
    IA no lo vuelva a pisar en la siguiente corrida."""
    try:
        return supabase_db.execute(
            "UPDATE competencia.skus SET termino_general = %s, "
            "termino_origen = 'manual', actualizado_en = now() WHERE sku = %s",
            (termino.strip(), sku),
        ) > 0
    except Exception as exc:  # noqa: BLE001
        log.error("actualizar_termino(%s) falló: %s", sku, exc)
        return False


# ── competencia.corridas ─────────────────────────────────────────────────────

def abrir_corrida(periodo: date | None = None, origen: str = "cron") -> str | None:
    try:
        row = supabase_db.execute_returning(
            "INSERT INTO competencia.corridas (periodo, origen, estado) "
            "VALUES (%s, %s, 'corriendo') RETURNING id",
            (periodo or periodo_actual(), origen),
        )
        return str(row["id"]) if row else None
    except Exception as exc:  # noqa: BLE001
        log.error("abrir_corrida falló: %s", exc)
        return None


def cerrar_corrida(corrida_id: str | None, skus_medidos: int, resultados: int,
                   visitas_ok: int, costo_usd: float | None = None,
                   error: str | None = None,
                   avisos: list[str] | None = None) -> None:
    if not corrida_id:
        return
    import json
    try:
        supabase_db.execute(
            "UPDATE competencia.corridas SET estado = %s, skus_medidos = %s, "
            "resultados = %s, visitas_ok = %s, costo_apify_usd = %s, error = %s, "
            "avisos = %s::jsonb, terminado_en = now() WHERE id = %s",
            ("error" if error else "listo", skus_medidos, resultados, visitas_ok,
             costo_usd, error, json.dumps(avisos or []), corrida_id),
        )
    except Exception as exc:  # noqa: BLE001
        log.error("cerrar_corrida falló: %s", exc)


def ultima_corrida() -> dict[str, Any] | None:
    try:
        return supabase_db.fetch_one(
            "SELECT * FROM competencia.corridas ORDER BY creado_en DESC LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        log.error("ultima_corrida falló: %s", exc)
        return None


# ── competencia.resultados ───────────────────────────────────────────────────

_COLUMNAS_RESULTADO = (
    "sku", "tipo", "termino", "periodo", "posicion", "externo_id", "titulo",
    "descripcion", "precio", "moneda", "imagen", "url", "seller", "marca",
    "categoria_id", "categoria_nombre", "visitas_30d", "vendidos", "reviews",
    "rating", "envio_gratis", "es_full", "es_nuestro", "sku_nuestro",
)

_SQL_INS_RESULTADO = f"""
    INSERT INTO competencia.resultados ({", ".join(_COLUMNAS_RESULTADO)})
    VALUES ({", ".join(f"%({c})s" for c in _COLUMNAS_RESULTADO)})
    ON CONFLICT (sku, tipo, externo_id) DO UPDATE SET
      posicion = EXCLUDED.posicion, titulo = EXCLUDED.titulo,
      descripcion = EXCLUDED.descripcion, precio = EXCLUDED.precio,
      imagen = EXCLUDED.imagen, url = EXCLUDED.url, seller = EXCLUDED.seller,
      marca = EXCLUDED.marca, visitas_30d = EXCLUDED.visitas_30d,
      vendidos = EXCLUDED.vendidos, reviews = EXCLUDED.reviews,
      rating = EXCLUDED.rating, envio_gratis = EXCLUDED.envio_gratis,
      es_full = EXCLUDED.es_full, es_nuestro = EXCLUDED.es_nuestro,
      sku_nuestro = EXCLUDED.sku_nuestro, capturado_en = now()
"""


def reemplazar_resultados(sku: str, tipo: str, periodo: date,
                          filas: list[dict[str, Any]], termino: str | None = None) -> int:
    """
    Borra la medición anterior de (sku, tipo) y escribe la nueva, en UNA
    transacción. Aquí es donde se materializa el "sin histórico": si esto se
    quisiera acumular, este es el único método que habría que cambiar.
    """
    normalizadas = []
    for f in filas:
        if not f.get("externo_id"):
            continue
        fila = {k: f.get(k) for k in _COLUMNAS_RESULTADO}
        fila.update(sku=sku, tipo=tipo, periodo=periodo,
                    termino=termino if termino is not None else f.get("termino"))
        fila["moneda"] = fila.get("moneda") or "MXN"
        fila["es_nuestro"] = bool(fila.get("es_nuestro"))
        if fila.get("titulo"):
            fila["titulo"] = str(fila["titulo"])[:500]
        if fila.get("descripcion"):
            fila["descripcion"] = str(fila["descripcion"])[:2000]
        normalizadas.append(fila)

    try:
        with supabase_db.get_cursor() as cur:
            cur.execute(
                "DELETE FROM competencia.resultados WHERE sku = %s AND tipo = %s",
                (sku, tipo))
            for i in range(0, len(normalizadas), _LOTE):
                cur.executemany(_SQL_INS_RESULTADO, normalizadas[i:i + _LOTE])
        return len(normalizadas)
    except Exception as exc:  # noqa: BLE001
        log.error("reemplazar_resultados(%s, %s) falló: %s", sku, tipo, exc)
        return 0


def resultados(sku: str, tipo: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM competencia.resultados WHERE sku = %(sku)s"
    params: dict[str, Any] = {"sku": sku}
    if tipo:
        sql += " AND tipo = %(tipo)s"
        params["tipo"] = tipo
    sql += " ORDER BY tipo, posicion NULLS LAST"
    try:
        return supabase_db.fetch_all(sql, params)
    except Exception as exc:  # noqa: BLE001
        log.error("resultados(%s) falló: %s", sku, exc)
        return []


def posiciones(sku: str | None = None) -> list[dict[str, Any]]:
    """La vista `competencia.posiciones`: mi posición y el contexto de precios."""
    sql = "SELECT * FROM competencia.posiciones"
    params: tuple = ()
    if sku:
        sql += " WHERE sku = %s"
        params = (sku,)
    sql += " ORDER BY sku, tipo"
    try:
        return supabase_db.fetch_all(sql, params)
    except Exception as exc:  # noqa: BLE001
        log.error("posiciones falló: %s", exc)
        return []


def por_categoria() -> list[dict[str, Any]]:
    """
    Los SKUs vigilados agrupados por categoría, con su posición en cada tipo de
    medición. Es exactamente lo que pinta la vista de tabla del tab.
    """
    sql = """
        SELECT s.categoria_id, s.categoria_nombre, s.sku, s.nombre,
               s.termino_general, s.termino_origen, s.ml_item_id, s.cuenta,
               p_gen.mi_posicion       AS pos_general,
               p_gen.total_resultados  AS total_general,
               p_gen.mis_visitas_30d   AS visitas_general,
               p_gen.precio_mediana_rivales AS mediana_general,
               p_tit.mi_posicion       AS pos_titulo,
               p_tit.total_resultados  AS total_titulo,
               p_tit.precio_mediana_rivales AS mediana_titulo,
               p_cat.mi_posicion       AS pos_categoria,
               p_cat.total_resultados  AS total_categoria,
               p_gen.mi_precio         AS mi_precio,
               p_gen.periodo           AS periodo
        FROM competencia.skus s
        LEFT JOIN competencia.posiciones p_gen
               ON p_gen.sku = s.sku AND p_gen.tipo = 'general'
        LEFT JOIN competencia.posiciones p_tit
               ON p_tit.sku = s.sku AND p_tit.tipo = 'titulo'
        LEFT JOIN competencia.posiciones p_cat
               ON p_cat.sku = s.sku AND p_cat.tipo = 'categoria'
        WHERE s.activo
        ORDER BY s.categoria_nombre NULLS LAST, s.sku
    """
    try:
        return supabase_db.fetch_all(sql)
    except Exception as exc:  # noqa: BLE001
        log.error("por_categoria falló: %s", exc)
        return []
