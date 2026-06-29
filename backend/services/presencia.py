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

from core.marketplaces import Canal
from services import db

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

    # Mercado Libre
    try:
        rows = db.fetch_all(
            f"""SELECT sku, ml_item_id, ml_url, success
                FROM ml_progress WHERE sku IN ({placeholders})""",
            tuple(skus),
        )
        for r in rows:
            _agregar(r["sku"], Canal.MERCADO_LIBRE.value,
                     bool(r.get("success")), r.get("ml_item_id"), r.get("ml_url"))
    except Exception as exc:  # noqa: BLE001
        log.warning("presencia ML falló: %s", exc)

    # Amazon
    try:
        rows = db.fetch_all(
            f"""SELECT sku, asin, success
                FROM amazon_progress WHERE sku IN ({placeholders})""",
            tuple(skus),
        )
        for r in rows:
            asin = r.get("asin")
            _agregar(r["sku"], Canal.AMAZON.value, bool(r.get("success")),
                     asin, f"https://www.amazon.com.mx/dp/{asin}" if asin else None)
    except Exception as exc:  # noqa: BLE001
        log.warning("presencia Amazon falló: %s", exc)

    return {sku: list(canales.values()) for sku, canales in acc.items()}
