"""
competencia_scraper.py — La ficha de la competencia, que la API de ML no da.

Por qué existe (sondeado contra la API con el token real de BEKURA):

  • `GET /items/{id}` de una publicación ajena → **403**. Ni título, ni precio,
    ni imagen, ni permalink. El multiget `GET /items?ids=` también, uno por uno.
  • `GET /sites/MLM/search` → **403** en las dos apps. O sea que la posición
    orgánica de búsqueda tampoco existe por API.
  • `sold_quantity` de un competidor: no hay endpoint.

Y raspar a pelo tampoco: ML sirve `suspicious-traffic-frontend` a IPs de
datacenter, y **ni el proxy residencial MX lo evita** — probado, la home carga
(515 KB) pero las páginas de artículo y de más-vendidos caen igual. ML corre
`security.js` + `snoopy-matt`, que exige ejecutar JS. Por eso esto va con actores
de Apify (navegador real), no con `requests`.

TRAMPA VERIFICADA — no usar `categoryUrls` ni `startUrls`
---------------------------------------------------------
El actor los acepta y los IGNORA en silencio, devolviendo su consulta por
defecto. Le pasé la URL de `mas-vendidos/MLM162997` (tapetes) por las dos vías:
terminó SUCCEEDED y devolvió **iPhone 15**. No falla, MIENTE — y eso se habría
guardado como competencia de tapetes. Este módulo solo usa `searchQueries`, que
sí se respeta. El ranking por categoría NO se raspa: sale de la API
(`/highlights`), que además da la posición oficial.

COSTO — el detalle no es gratis
-------------------------------
`includeProductDetail=false` → $0.003/item: título, precio, imagen, url, seller,
posición. `=true` → $0.025/item (8×) y agrega **descripcion** (la corta, derivada
de atributos) y **vendidos** (`soldQuantity`), que es la mejor señal de demanda
disponible de un competidor. Por eso el detalle se pide solo del top N.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from config import settings

log = logging.getLogger("omnicanal.competencia.scraper")

_APIFY = "https://api.apify.com/v2"
_ESPERA = 5
_MAX_SONDEOS = 84          # ~7 min
_sem = asyncio.Semaphore(2)

# Tarifas del actor (PAY_PER_EVENT), para poder reportar el gasto de una corrida.
COSTO_ITEM = 0.003
COSTO_ITEM_DETALLE = 0.025


def disponible() -> bool:
    return bool(settings.apify_api_key)


def _proxy() -> dict[str, Any]:
    return {
        "useApifyProxy": True,
        "apifyProxyGroups": ["RESIDENTIAL"],
        "apifyProxyCountry": settings.apify_proxy_pais,
    }


async def _correr_actor(actor: str, payload: dict[str, Any],
                        limite_lectura: int = 200) -> list[dict[str, Any]]:
    """
    Corre un actor y devuelve las filas de su dataset. Devuelve [] ante cualquier
    fallo: un SKU que no raspa no debe tumbar la corrida mensual completa.
    """
    if not disponible():
        log.warning("APIFY_API_KEY no configurada; el scraping de competencia no corre")
        return []
    token = {"token": settings.apify_api_key}
    async with _sem:
        try:
            async with httpx.AsyncClient(timeout=120.0) as cli:
                r = await cli.post(f"{_APIFY}/acts/{actor}/runs",
                                   params={**token, "memory": 2048}, json=payload)
                if r.status_code >= 300:
                    log.warning("Apify %s no arrancó: %s %s", actor,
                                r.status_code, r.text[:200])
                    return []
                run_id = r.json()["data"]["id"]

                datos: dict[str, Any] = {}
                for _ in range(_MAX_SONDEOS):
                    await asyncio.sleep(_ESPERA)
                    rs = await cli.get(f"{_APIFY}/actor-runs/{run_id}", params=token)
                    datos = rs.json().get("data", {})
                    if datos.get("status") not in ("RUNNING", "READY"):
                        break

                if datos.get("status") != "SUCCEEDED":
                    log.warning("Apify %s terminó en %s", actor, datos.get("status"))
                    return []

                rd = await cli.get(
                    f"{_APIFY}/datasets/{datos['defaultDatasetId']}/items",
                    params={**token, "limit": limite_lectura},
                )
                filas = rd.json()
                return filas if isinstance(filas, list) else []
        except Exception as exc:  # noqa: BLE001
            log.warning("Apify %s falló: %s", actor, exc)
            return []


def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _marca(it: dict[str, Any]) -> str | None:
    attrs = it.get("attributes") or {}
    if isinstance(attrs, dict):
        m = attrs.get("brand") or attrs.get("Marca")
        if m:
            # El actor a veces trae el campo Marca lleno de keyword-stuffing del
            # vendedor ("Malla Sombra Black/malla sombra/sombras para jardin/…").
            return str(m).split("/")[0].strip()[:120]
    return None


def _normalizar(it: dict[str, Any], posicion: int) -> dict[str, Any]:
    """Fila cruda del actor → fila de `competencia.resultados`."""
    vendedor = it.get("seller") or {}
    imgs = it.get("images") or []
    return {
        "externo_id": it.get("id") or it.get("itemId"),
        "posicion": posicion,
        "titulo": it.get("title"),
        # Descripción CORTA derivada de atributos ("Largo: 4 m | Ancho: 6 m").
        # ML no expone la descripción larga de publicaciones ajenas.
        "descripcion": it.get("description"),
        "precio": _num(it.get("price")),
        "moneda": it.get("currency") or "MXN",
        "imagen": it.get("thumbnailUrl") or (imgs[0] if imgs else None),
        "url": it.get("permalink") or it.get("url"),
        "seller": vendedor.get("nickname") or vendedor.get("storeName"),
        "marca": _marca(it),
        # El actor NO devuelve categoría (categoryId viene null): la pone el
        # orquestador desde nuestra taxonomía.
        "categoria_id": None,
        "categoria_nombre": None,
        "vendidos": it.get("soldQuantity"),
        "reviews": it.get("reviewCount"),
        "rating": _num(it.get("ratingAverage")),
        "envio_gratis": it.get("freeShipping"),
        "es_full": it.get("fullShipping"),
        "catalog_product_id": it.get("catalogProductId"),
    }


async def buscar(termino: str, limite: int = 30,
                 con_detalle: bool = True) -> list[dict[str, Any]]:
    """
    Resultados de búsqueda de ML para un término, **en orden**: el índice ES la
    posición orgánica, el único lugar de donde se puede sacar ese dato.

    `con_detalle=True` agrega descripción y vendidos, a 8× el costo por item.
    """
    filas = await _correr_actor(settings.apify_ml_actor, {
        "siteId": settings.ml_site_id,
        "searchQueries": [termino],
        "maxItems": limite,
        "maxPagesPerQuery": max(1, limite // 50 + 1),
        "sort": "relevance",
        "includeProductDetail": con_detalle,
        "proxyConfiguration": _proxy(),
    }, limite_lectura=limite)

    out = []
    for i, it in enumerate(filas[:limite], start=1):
        fila = _normalizar(it, i)
        if fila["externo_id"]:
            out.append(fila)
    log.info("buscar(%r, detalle=%s) → %s resultados", termino, con_detalle, len(out))
    return out


def costo_estimado(busquedas: int, items_por_busqueda: int,
                   con_detalle: bool = True) -> float:
    """Gasto estimado de Apify en USD, para reportarlo antes y después de correr."""
    por_item = COSTO_ITEM_DETALLE if con_detalle else COSTO_ITEM
    return round(busquedas * items_por_busqueda * por_item, 3)
