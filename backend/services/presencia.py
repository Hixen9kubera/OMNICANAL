"""
presencia.py — Calcula en qué canales está publicado cada SKU.

Se usa en la vista GENERAL para pintar los "puntos de colores" (como en la UI
actual de cloud.autoazur.com): cada SKU muestra en qué marketplaces existe.

Consulta una sola vez ml_progress + amazon_progress para un lote de SKUs,
evitando N consultas por producto.
"""
from __future__ import annotations

import logging
from typing import Any

from config import settings
from core.marketplaces import Canal
from services import alertas, channel_read, db, lecturas_fuente

log = logging.getLogger("omnicanal.presencia")


def presencia_por_sku(skus: list[str]) -> dict[str, list[dict[str, Any]]]:
    """
    Devuelve { sku: [ {canal, publicado, item_id, url, n}, ... ] } para el lote,
    con UN registro por canal (los "puntos de colores" de la vista GENERAL).
    Si un SKU tiene varias publicaciones en un canal, `n` indica cuántas.
    Solo incluye canales con datos reales (ML, Amazon).
    """
    if not skus:
        return {}

    # Acumulador por (sku, canal) para colapsar publicaciones múltiples.
    acc: dict[str, dict[str, dict[str, Any]]] = {s: {} for s in skus}
    placeholders = ",".join(["%s"] * len(skus))

    def _agregar(sku: str, canal: str, publicado: bool, item_id, url):
        if sku not in acc:
            return
        ent = acc[sku].get(canal)
        if ent is None:
            acc[sku][canal] = {
                "canal": canal, "publicado": publicado,
                "item_id": item_id, "url": url, "n": 1,
            }
        else:
            ent["n"] += 1
            ent["publicado"] = ent["publicado"] or publicado
            if not ent["item_id"] and item_id:
                ent["item_id"], ent["url"] = item_id, url

    # FUENTE MÁS FRESCA: canal_inventario (espejo de canales). Lo alimentan el
    # sync de 15 min Y los webhooks de ML (items/stock_locations/orders_v2), así
    # que refleja el estado REAL por cuenta — incluye las publicaciones PAUSADAS,
    # que también son "publicado" (existen en el canal). Va primero para que una
    # publicación recién creada aparezca sin esperar al snapshot diario.
    try:
        rows = None
        # PASO 3 (12-ago-2026): sin fallback a `canal_inventario` — está
        # congelada desde el 11-ago y serviría publicaciones con su estado
        # viejo. Ver la nota larga en inventario.leer_inventario.
        if settings.supabase_read_channel:
            rows = channel_read.presencia(list(skus))
            lecturas_fuente.anotar("channel", "kubera")
        if rows is None:
            rows = db.fetch_all(
                f"""SELECT sku, canal, cuenta, item_id, situacion
                    FROM canal_inventario
                    WHERE sku IN ({placeholders})
                      AND item_id IS NOT NULL AND item_id <> ''""",
                tuple(skus),
            )
        for r in rows:
            situacion = (r.get("situacion") or "").lower()
            # 'closed' = publicación dada de baja: ya NO cuenta como publicada.
            if situacion == "closed":
                continue
            _agregar(r["sku"], r.get("canal") or Canal.MERCADO_LIBRE.value,
                     True, r.get("item_id"), None)
    except Exception as exc:  # noqa: BLE001
        log.warning("presencia (canal_inventario) falló: %s", exc)

    # Aquí vivía una segunda fuente para Mercado Libre: `products_snapshot` del
    # proyecto dailytrackMeli, vía supabase_rest. SE RETIRA (Eduardo, 8-ago):
    # ese proyecto YA NO EXISTE — su hostname dejó de resolver, después de que
    # su Postgres se quedara sin espacio y restringiera la organización entera.
    # Desde entonces la llamada fallaba en CADA carga de la página Productos y
    # solo servía para llenar el log.
    #
    # No se reemplaza porque no hace falta: `channel_read.presencia()` de arriba
    # ya lee channel.listings, que es la misma información y mejor. Censo del
    # 7-ago contra la API de Mercado Libre: listings conoce las 4,586
    # publicaciones (el snapshot tenía 1,000 de las 2,320 de Sancor) y coincide
    # en estado con ML en 99.8%. En la muestra donde las dos fuentes discrepaban,
    # ML le dio la razón a listings en 44 de 44.
    #
    # Tampoco se pierde la URL: la de los puntos de esta vista no se usa, y el
    # enlace "Ver publicación" del Estudio sale de studio.metadata, no de aquí.
    try:
        rows = db.fetch_all(
            f"""SELECT sku, ml_item_id, ml_url, success
                FROM ml_progress WHERE sku IN ({placeholders})""",
            tuple(skus),
        )
        for r in rows:
            # Evitar duplicar si channel.listings ya marcó el canal para ese SKU.
            # ml_progress es la bitácora del publicador: sirve de red para lo
            # recién publicado que el espejo todavía no alcanzó.
            if Canal.MERCADO_LIBRE.value in acc.get(r["sku"], {}):
                continue
            _agregar(r["sku"], Canal.MERCADO_LIBRE.value,
                     bool(r.get("success")), r.get("ml_item_id"), r.get("ml_url"))
    except Exception as exc:  # noqa: BLE001
        log.warning("presencia ML (ml_progress) falló: %s", exc)

    # Amazon
    try:
        rows = db.fetch_all(
            f"""SELECT sku, asin, success
                FROM amazon_progress WHERE sku IN ({placeholders})""",
            tuple(skus),
        )
        for r in rows:
            # MISMA GUARDIA QUE ML, que aquí faltaba (medido el 14-ago-2026).
            # `_agregar` no reemplaza: si el canal ya existe, SUMA (`n += 1`).
            # Con `SUPABASE_READ_CHANNEL=true`, `channel_read.presencia()` ya
            # devuelve Amazon —`CANALES` lo incluye—, así que cada SKU presente
            # en las dos fuentes salía con **n=2 para una sola publicación**:
            # 1,387 SKUs, todos los que `channel.listings` tiene con item_id.
            #
            # El bloque NO se borra: sigue siendo la red de lo recién publicado,
            # igual que el de ML. Un SKU sin `listing_id` en listings (Amazon no
            # asigna el ASIN al publicar) no entra por arriba y necesita esta
            # vuelta. Lo que se corrige es que cuente dos veces al mismo.
            if Canal.AMAZON.value in acc.get(r["sku"], {}):
                continue
            asin = r.get("asin")
            _agregar(r["sku"], Canal.AMAZON.value, bool(r.get("success")),
                     asin, f"https://www.amazon.com.mx/dp/{asin}" if asin else None)
    except Exception as exc:  # noqa: BLE001
        log.warning("presencia Amazon falló: %s", exc)

    return {sku: list(canales.values()) for sku, canales in acc.items()}
