"""
competencia_captura.py — La corrida mensual: tres mediciones por SKU.

Las tres preguntas del módulo, cada una con su fuente:

  1. GENERAL   — "¿dónde estoy en la búsqueda genérica?" ("lona para exterior")
                 Compites por DESCUBRIMIENTO contra todo el mundo.
                 Fuente: scraper (la posición orgánica no existe por API).
  2. TÍTULO    — "¿dónde estoy contra mi competencia directa?"
                 Fuente: scraper, con el título completo del producto.
  3. CATEGORÍA — "¿quiénes son los mejores de mi categoría?"
                 Fuente: API `/highlights`, que da el ranking OFICIAL con posición.

Y sobre todas ellas se pegan las VISITAS de la API (`/visits/items`), que sí
funcionan para publicaciones ajenas y son el dato más confiable del módulo.

Sin histórico: cada corrida borra la medición anterior del SKU y la reescribe
(ver `competencia_store.reemplazar_resultados`).

AHORRO REAL: varios SKUs comparten término general (los 3 de Tapetes dan todos
"tapetes para auto"). Las búsquedas se deduplican y el resultado se reusa entre
los SKUs que lo comparten — solo cambia a quién se le marca `es_nuestro`. Con 7
SKUs eso baja de 14 búsquedas a 12, y en catálogos grandes la diferencia crece.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from config import settings
from services import (
    competencia_ml, competencia_scraper, competencia_store, competencia_terminos, db,
)

log = logging.getLogger("omnicanal.competencia.captura")

# Cuántas publicaciones de cada medición se enriquecen con visitas. Cada visita
# es UNA llamada a la API (ML no acepta multiget), así que este número es el
# costo en tiempo de la corrida.
TOPE_VISITAS = 25


# ── Nuestras publicaciones, para saber "dónde estoy" ────────────────────────

def _nuestras_publicaciones() -> dict[str, dict[str, str]]:
    """{ ml_item_id: {sku, cuenta} } de TODO lo publicado en ML (ambas cuentas)."""
    try:
        filas = db.fetch_all(
            "SELECT ml_item_id, sku, cuenta FROM ml_progress "
            "WHERE ml_item_id IS NOT NULL AND ml_item_id <> ''")
        return {f["ml_item_id"]: {"sku": f["sku"], "cuenta": f.get("cuenta") or ""}
                for f in filas}
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudieron leer nuestras publicaciones: %s", exc)
        return {}


def _marcar(filas: list[dict[str, Any]], nuestras: dict[str, dict[str, str]]) -> None:
    for f in filas:
        mio = nuestras.get(f.get("externo_id") or "")
        if mio:
            f["es_nuestro"] = True
            f["sku_nuestro"] = mio["sku"]


async def _visitas(filas: list[dict[str, Any]], tope: int = TOPE_VISITAS) -> int:
    """Visitas de 30 días, de a una llamada (ML no acepta multiget)."""
    objetivo = [f for f in filas[:tope]
                if (f.get("externo_id") or "").startswith("MLM")
                and not (f.get("externo_id") or "").startswith("MLMU")]
    if not objetivo:
        return 0
    res = await asyncio.gather(
        *(asyncio.to_thread(competencia_ml.visitas_30d, f["externo_id"]) for f in objetivo),
        return_exceptions=True)
    ok = 0
    for fila, v in zip(objetivo, res):
        if isinstance(v, int):
            fila["visitas_30d"] = v
            ok += 1
    return ok


# ── Alta de los SKUs vigilados ──────────────────────────────────────────────

def sembrar_skus(skus: list[str], con_ia: bool = True) -> dict[str, Any]:
    """
    Da de alta (o refresca) los SKUs del MVP: nombre, categoría y publicación
    salen de MySQL; el término general lo propone la IA.

    Los SKUs que no estén en `productos` se reportan y se omiten: sin nombre no
    hay título que buscar ni término que generar.
    """
    if not skus:
        return {"ok": False, "motivo": "Lista de SKUs vacía."}

    marcas = ",".join(["%s"] * len(skus))
    filas = db.fetch_all(f"""
        SELECT p.sku, p.nombre, c.category_id, c.category_name,
               MAX(CASE WHEN mp.cuenta = 'BEKURA' THEN mp.ml_item_id END) AS item_bekura,
               MAX(mp.ml_item_id) AS item_cualquiera,
               MAX(CASE WHEN mp.cuenta = 'BEKURA' THEN 'BEKURA' END) AS tiene_bekura
        FROM productos p
        LEFT JOIN categorias_ml c ON c.sku = p.sku
        LEFT JOIN ml_progress mp ON mp.sku = p.sku
             AND mp.ml_item_id IS NOT NULL AND mp.ml_item_id <> ''
        WHERE p.sku IN ({marcas})
        GROUP BY p.sku, p.nombre, c.category_id, c.category_name
    """, tuple(skus))

    encontrados = {f["sku"] for f in filas}
    faltantes = [s for s in skus if s not in encontrados]

    productos = [{
        "sku": f["sku"],
        "nombre": f["nombre"],
        "categoria_id": f.get("category_id"),
        "categoria_nombre": f.get("category_name"),
        # Se guarda la de BEKURA como referencia; si no hay, cualquiera.
        "ml_item_id": f.get("item_bekura") or f.get("item_cualquiera"),
        "cuenta": "BEKURA" if f.get("tiene_bekura") else None,
    } for f in filas]

    terminos: dict[str, str] = {}
    if con_ia and productos:
        terminos = competencia_terminos.proponer(productos)
    for p in productos:
        p["termino_general"] = terminos.get(p["sku"])
        p["termino_origen"] = "ia"

    guardados = competencia_store.guardar_skus(productos)
    sin_publicacion = [p["sku"] for p in productos if not p["ml_item_id"]]
    sin_termino = [p["sku"] for p in productos if not p["termino_general"]]

    return {
        "ok": True,
        "guardados": guardados,
        "sin_registro_en_productos": faltantes,
        "sin_publicacion_ml": sin_publicacion,
        "sin_termino_general": sin_termino,
    }


# ── Las tres mediciones de un SKU ───────────────────────────────────────────

async def _medir_categoria(sku: dict[str, Any], periodo: date,
                           nuestras: dict[str, dict[str, str]]) -> tuple[int, int, list[str]]:
    """Ranking OFICIAL de la categoría, por API. Sin scraper y sin costo."""
    cat_id = sku.get("categoria_id")
    if not cat_id:
        return 0, 0, [f"{sku['sku']}: sin categoría de ML, no hay ranking que leer."]

    crudo = await asyncio.to_thread(competencia_ml.mas_vendidos_categoria, cat_id)
    if not crudo:
        return 0, 0, [f"{sku['sku']}: ML no publica ranking de más vendidos para "
                      f"«{sku.get('categoria_nombre') or cat_id}»."]

    filas = [await asyncio.to_thread(competencia_ml.resolver_highlight, e) for e in crudo]
    sin_ficha = sum(1 for f in filas if not f.get("titulo"))
    for f in filas:
        f["categoria_id"] = cat_id
        f["categoria_nombre"] = sku.get("categoria_nombre")
        f.pop("tipo_highlight", None)

    _marcar(filas, nuestras)
    ok = await _visitas(filas)
    n = competencia_store.reemplazar_resultados(sku["sku"], "categoria", periodo, filas)

    avisos = []
    if sin_ficha:
        avisos.append(f"{sku['sku']}: {sin_ficha} de {len(filas)} del ranking son de "
                      "tipo ITEM — la API de ML no permite leerlas (403) y quedan sin ficha.")
    return n, ok, avisos


async def _medir_busqueda(sku: dict[str, Any], tipo: str, termino: str, periodo: date,
                          nuestras: dict[str, dict[str, str]],
                          cache: dict[str, list[dict[str, Any]]]) -> tuple[int, int, list[str]]:
    """
    Una búsqueda ('general' o 'titulo') por scraper. El resultado se cachea por
    término: varios SKUs comparten término general y no hay razón para pagarlo
    dos veces.
    """
    clave = termino.strip().lower()
    if clave in cache:
        crudas = cache[clave]
    else:
        crudas = await competencia_scraper.buscar(
            termino, limite=settings.competencia_top,
            con_detalle=settings.competencia_con_detalle)
        cache[clave] = crudas

    if not crudas:
        return 0, 0, [f"{sku['sku']} ({tipo}): el scraper no devolvió resultados "
                      f"para «{termino}»."]

    # Copia por SKU: el marcado de es_nuestro y las visitas son por medición.
    filas = [dict(c) for c in crudas]
    for f in filas:
        f["categoria_id"] = sku.get("categoria_id")
        f["categoria_nombre"] = sku.get("categoria_nombre")
        f.pop("catalog_product_id", None)

    _marcar(filas, nuestras)
    ok = await _visitas(filas)
    n = competencia_store.reemplazar_resultados(sku["sku"], tipo, periodo, filas,
                                                termino=termino)
    return n, ok, []


async def medir_sku(sku: dict[str, Any], periodo: date,
                    nuestras: dict[str, dict[str, str]],
                    cache: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Las tres mediciones de un SKU."""
    total = visitas_ok = 0
    avisos: list[str] = []

    # 1. Búsqueda general (descubrimiento).
    if sku.get("termino_general"):
        n, ok, av = await _medir_busqueda(sku, "general", sku["termino_general"],
                                         periodo, nuestras, cache)
        total += n; visitas_ok += ok; avisos += av
    else:
        avisos.append(f"{sku['sku']}: sin término general, no se mide descubrimiento.")

    # 2. Título completo (competencia directa).
    if sku.get("nombre"):
        n, ok, av = await _medir_busqueda(sku, "titulo", sku["nombre"],
                                         periodo, nuestras, cache)
        total += n; visitas_ok += ok; avisos += av

    # 3. Ranking de la categoría (los mejores).
    n, ok, av = await _medir_categoria(sku, periodo, nuestras)
    total += n; visitas_ok += ok; avisos += av

    return {"sku": sku["sku"], "resultados": total, "visitas_ok": visitas_ok,
            "avisos": avisos}


# ── La corrida completa ─────────────────────────────────────────────────────

async def correr(periodo: date | None = None, origen: str = "cron",
                 skus: list[str] | None = None) -> dict[str, Any]:
    """
    Mide todos los SKUs vigilados (o los que se indiquen) y deja la foto del mes.

    Los SKUs se corren EN SERIE: cada uno lanza búsquedas de Apify y decenas de
    llamadas de visitas; en paralelo solo se gana rate-limit.
    """
    periodo = periodo or competencia_store.periodo_actual()
    if not competencia_store.disponible():
        return {"ok": False, "motivo": "SUPABASE_DB_URL no está configurada: no hay "
                                       "dónde guardar la corrida."}

    vigilados = competencia_store.listar_skus()
    if skus:
        pedidos = set(skus)
        vigilados = [s for s in vigilados if s["sku"] in pedidos]
    if not vigilados:
        return {"ok": False, "motivo": "No hay SKUs vigilados. Corre la siembra "
                                       "primero (POST /api/competencia/sembrar).",
                "periodo": str(periodo)}

    corrida_id = competencia_store.abrir_corrida(periodo, origen)
    nuestras = _nuestras_publicaciones()
    cache: dict[str, list[dict[str, Any]]] = {}
    detalle, avisos = [], []
    total = visitas_ok = 0

    try:
        for s in vigilados:
            r = await medir_sku(s, periodo, nuestras, cache)
            detalle.append(r)
            total += r["resultados"]
            visitas_ok += r["visitas_ok"]
            avisos += r["avisos"]

        costo = competencia_scraper.costo_estimado(
            len(cache), settings.competencia_top, settings.competencia_con_detalle)
        competencia_store.cerrar_corrida(corrida_id, len(vigilados), total,
                                         visitas_ok, costo, avisos=avisos)
        log.info("Corrida %s: %s SKUs, %s resultados, %s visitas, %s búsquedas (~$%s)",
                 periodo, len(vigilados), total, visitas_ok, len(cache), costo)
        return {"ok": True, "periodo": str(periodo), "corrida_id": corrida_id,
                "skus": len(vigilados), "resultados": total, "visitas_ok": visitas_ok,
                "busquedas": len(cache), "costo_apify_usd": costo,
                "avisos": avisos, "detalle": detalle}

    except Exception as exc:  # noqa: BLE001
        log.error("Corrida de competencia falló: %s", exc)
        competencia_store.cerrar_corrida(corrida_id, len(vigilados), total,
                                         visitas_ok, error=str(exc)[:500])
        return {"ok": False, "motivo": str(exc), "corrida_id": corrida_id}
