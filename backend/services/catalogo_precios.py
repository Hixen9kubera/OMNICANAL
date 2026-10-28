"""
catalogo_precios.py — Los precios de mercado del CATÁLOGO MAESTRO para «Sugerir precio» del Estudio (v0.696.0).

QUÉ ES
    Una FOTO del catálogo maestro (CATALOGO_VIVO), no datos en vivo: `backend/data/catalogo_precios/precios.json.gz`,
    que arma `scripts/catalogo_precios_preparar.py` (ver su docstring: qué lleva, qué NO lleva y cómo reconstruye el
    match de cada publicación de Amazon). Por SKU:

      · Mercado Libre: la MEDIA (`precio_ml_final` del v3, el precio que manda —regla de Brandon del 28-oct—), el
        MÍNIMO y el MÁXIMO, y sus fuentes. Ojo: el rango es ENTRE FUENTES (más vendidos de la categoría en ML,
        Amazon ÷ 1.443, búsqueda en ML), no la dispersión de los rivales. No hay publicaciones de ML por producto.
      · Amazon: la palabra clave, la estadística de los comparables y cada publicación leída con su veredicto
        (match / no match / sin veredicto guardado).

    Si el SKU no está o no trae precio de ML, el Estudio pide el modo completo (`precio_sugerido.completo`).

SOLO LECTURA de un archivo del repo. El JSON se abre UNA vez por proceso (~3.7 MB comprimido, ~12 MB abierto) y
se queda en memoria: el catálogo solo cambia con un deploy.
"""
from __future__ import annotations

import gzip
import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("omnicanal.catalogo_precios")

ARCHIVO = Path(__file__).resolve().parent.parent / "data" / "catalogo_precios" / "precios.json.gz"
URL_AMAZON = "https://www.amazon.com.mx/dp/{}"
MATCH = {"m": "match", "x": "no_match", "?": "sin_dato"}

_datos: dict[str, Any] | None = None
_lock = threading.Lock()


def _cargar() -> dict[str, Any]:
    """BLOQUEANTE (disco, la primera vez). Sin archivo, un catálogo vacío: el Estudio pide el modo completo."""
    global _datos
    with _lock:
        if _datos is None:
            try:
                _datos = json.loads(gzip.decompress(ARCHIVO.read_bytes()))
            except FileNotFoundError:
                log.warning("catálogo de precios: falta %s", ARCHIVO)
                _datos = {"meta": {}, "skus": {}, "busquedas": {}}
        return _datos


def amazon_de(az: dict[str, Any], resultados: list[list[Any]]) -> dict[str, Any]:
    """PURA. El bloque de Amazon con cada publicación leída y su veredicto, en la forma que usa el Estudio (la misma
    que arma `amazon_mercado.estudio` en vivo)."""
    ver, unidades = az.get("v") or "", az.get("u") or {}
    pubs = []
    for i, (asin, titulo, precio) in enumerate(resultados):
        p = {"id": asin, "titulo": titulo, "precio": precio, "url": URL_AMAZON.format(asin),
             "match": MATCH.get(ver[i] if i < len(ver) else "?", "sin_dato")}
        if str(i) in unidades:
            p["unidades"] = unidades[str(i)]
        pubs.append(p)
    pubs.sort(key=lambda p: (("match", "sin_dato", "no_match").index(p["match"]), p["precio"] or 0))
    out = {"q": az.get("q"), "vistos": az.get("vistos", len(pubs)), "clases": az.get("clases") or {},
           "publicaciones": pubs}
    for k in ("n", "media", "mediana", "p25", "p75", "min", "max", "aprox"):
        if az.get(k) is not None:
            out[k] = az[k]
    return out


def por_sku(sku: str) -> dict[str, Any]:
    """BLOQUEANTE (disco la primera vez). Lo que el catálogo maestro sabe del SKU: `ml` y `amazon` (None si no hay)."""
    d = _cargar()
    meta = d.get("meta") or {}
    fila = (d.get("skus") or {}).get(sku) or {}
    az = fila.get("az")
    return {
        "sku": sku,
        "ml": fila.get("ml"),
        "amazon": amazon_de(az, (d.get("busquedas") or {}).get(az.get("q"), [])) if az else None,
        "fecha_ml": meta.get("ml_fecha"),
        "fecha_amazon": (meta.get("amazon_actualizado") or "")[:10] or None,
    }
