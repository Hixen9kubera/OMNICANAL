"""
amazon_mercado.py — A cuánto vende la competencia en Amazon México y qué medidas trae su paquete (v0.696.0).
Lo usa «Sugerir precio» del Estudio. GRATIS: solo la SP-API con nuestras credenciales, solo GET.

── EL PRECIO (`estudio`, dentro del modo completo) ─────────────────────────────
    El método del estudio de sábanas (memoria «amazon-estudio-de-mercado-sp-api», 6-oct-2026), recortado para que
    quepa en un botón:
      1. `searchCatalogItems` con la palabra clave: las primeras PAGINAS páginas (20 por página, por relevancia).
      2. `getCompetitivePricing`: la CAJA DE COMPRA de cada una (`CompetitivePriceId: "1"`); sin caja no entra.
      3. Una cifra por FAMILIA (el padre con todas sus variantes cuenta una vez, con la mediana de sus precios).
      4. El juez de títulos de Competencia (`competencia_juez`, el mismo del catálogo y de ML): match = «mismo».
      5. Solo el nodo de categoría donde cae la mayoría de los match: lo de otro nodo no es comparable.
    Lo que la API no da (visitas, ventas, estrellas) no se estima.

── LAS MEDIDAS (`medidas`) ─────────────────────────────────────────────────────
    Las del PAQUETE (`dimensions.package`) de las publicaciones que hicieron match; la mediana de cada lado,
    redondeada hacia arriba al cm, y el peso a 0.01 kg. Un ASIN PADRE no trae medidas (medido el 28-oct: 0 de 15
    padres): se baja a su primera variante. Sin match con medidas, las primeras de la búsqueda, y se dice.

Las pausas respetan los límites de Amazon: Catalog Items 2 por segundo, Pricing v0 una cada 2 s.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import statistics
from collections import Counter
from typing import Any

import httpx

from config import settings
from services import amazon

log = logging.getLogger("omnicanal.amazon_mercado")

PAGINAS = 2                  # 40 publicaciones: el estudio completo lee ~47 páginas, que no caben en un botón
POR_PRECIOS = 20             # ASINs por llamada de getCompetitivePricing
PAUSA_CATALOGO = 0.6
PAUSA_PRECIOS = 2.1
MAX_MEDIDAS = 10             # publicaciones que se miden
URL = "https://www.amazon.com.mx/dp/{}"
ES_ASIN = re.compile(r"[A-Z0-9]{10}")
_A_CM = {"inches": 2.54, "centimeters": 1.0, "millimeters": 0.1, "meters": 100.0}
_A_KG = {"pounds": 0.45359237, "kilograms": 1.0, "grams": 0.001, "ounces": 0.028349523}


class SinAmazon(Exception):
    """Sin credenciales o sin token: no se pudo preguntar."""


async def _cliente() -> httpx.AsyncClient:
    token = await amazon._access_token()
    if not token:
        raise SinAmazon("Sin token de Amazon (revisa las credenciales LWA)")
    return httpx.AsyncClient(base_url=settings.amazon_sp_api_endpoint, timeout=30.0,
                             headers={"x-amz-access-token": token})


async def _get(cli: httpx.AsyncClient, ruta: str, params: dict[str, Any], pausa: float) -> dict[str, Any] | None:
    """Un GET con un reintento si Amazon pide calma (429)."""
    for intento in range(2):
        r = await cli.get(ruta, params=params)
        await asyncio.sleep(pausa)
        if r.status_code == 200:
            return r.json()
        if r.status_code != 429 or intento:
            log.warning("Amazon %s → HTTP %s %s", ruta, r.status_code, r.text[:200])
            return None
        await asyncio.sleep(2.0)
    return None


def _resumen_item(it: dict[str, Any], mp: str) -> dict[str, Any]:
    s = next((x for x in it.get("summaries") or [] if x.get("marketplaceId") == mp), None) \
        or ((it.get("summaries") or [{}])[0])
    padre, hijos = None, []
    for rel in it.get("relationships") or []:
        for x in rel.get("relationships") or []:
            padre = padre or (x.get("parentAsins") or [None])[0]
            hijos += x.get("childAsins") or []
    return {"asin": it.get("asin"), "titulo": s.get("itemName") or "", "marca": s.get("brand") or "",
            "nodo": (s.get("browseClassification") or {}).get("classificationId"),
            "nodo_nombre": (s.get("browseClassification") or {}).get("displayName"),
            "padre": padre or it.get("asin"), "hijos": hijos}


async def buscar(cli: httpx.AsyncClient, q: str, paginas: int = PAGINAS) -> list[dict[str, Any]]:
    mp = settings.amazon_marketplace_id
    items, token = [], None
    for _ in range(paginas):
        params = {"marketplaceIds": mp, "keywords": q, "includedData": "summaries,relationships", "pageSize": 20}
        if token:
            params["pageToken"] = token
        j = await _get(cli, "/catalog/2022-04-01/items", params, PAUSA_CATALOGO)
        if not j:
            break
        items += [_resumen_item(it, mp) for it in j.get("items") or []]
        token = (j.get("pagination") or {}).get("nextToken")
        if not token:
            break
    return items


async def cajas_de_compra(cli: httpx.AsyncClient, asins: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in range(0, len(asins), POR_PRECIOS):
        j = await _get(cli, "/products/pricing/v0/competitivePrice", {
            "MarketplaceId": settings.amazon_marketplace_id, "ItemType": "Asin",
            "Asins": ",".join(asins[i:i + POR_PRECIOS])}, PAUSA_PRECIOS)
        for x in (j or {}).get("payload") or []:
            comp = (((x.get("Product") or {}).get("CompetitivePricing") or {}).get("CompetitivePrices")) or []
            caja = next((c for c in comp if str(c.get("CompetitivePriceId")) == "1"), None)
            precio = (caja or {}).get("Price") or {}
            monto = (precio.get("LandedPrice") or {}).get("Amount") or (precio.get("ListingPrice") or {}).get("Amount")
            if monto:
                out[x.get("ASIN")] = float(monto)
    return out


def familias(items: list[dict[str, Any]], precios: dict[str, float]) -> list[dict[str, Any]]:
    """PURA. Una fila por padre, con la mediana de sus variantes con caja de compra; el ASIN que se enlaza es la
    primera variante con precio (el padre no se puede comprar ni trae medidas)."""
    por_padre: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        if it["asin"] in precios:
            por_padre.setdefault(it["padre"], []).append(it)
    return [{"id": m[0]["asin"], "padre": padre, "titulo": m[0]["titulo"], "marca": m[0]["marca"],
             "nodo": m[0]["nodo"], "nodo_nombre": m[0]["nodo_nombre"],
             "precio": round(statistics.median(precios[x["asin"]] for x in m), 2), "url": URL.format(m[0]["asin"])}
            for padre, m in por_padre.items()]


def marcar(pubs: list[dict[str, Any]], veredictos: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    """PURA. Aplica el juez (`{i 1..N: {clase, motivo}}`) y el filtro de nodo; devuelve el nodo que quedó."""
    from services import competencia_juez

    for i, p in enumerate(pubs, 1):
        v = veredictos.get(i) or {}
        p["clase"] = v.get("clase") or "dudoso"
        p["motivo"] = v.get("motivo") or ""
        p["match"] = "match" if competencia_juez.es_comparable(p["clase"]) else ("sin_dato" if not v else "no_match")
        if p["clase"] == "otro_paquete" and v.get("unidades_rival"):
            p["unidades"] = v["unidades_rival"]
    nodos = Counter(p["nodo"] for p in pubs if p["match"] == "match" and p.get("nodo"))
    if not nodos:
        return None
    nodo = nodos.most_common(1)[0][0]
    for p in pubs:
        if p["match"] == "match" and p.get("nodo") and p["nodo"] != nodo:
            p["match"], p["motivo"] = "no_match", f"otra categoría de Amazon ({p.get('nodo_nombre') or p['nodo']})"
    return {"id": nodo, "nombre": next((p.get("nodo_nombre") for p in pubs if p.get("nodo") == nodo), None)}


async def estudio(q: str, titulo: str, unidades: int | None = None) -> dict[str, Any]:
    """El bloque de Amazon del modo completo, en la forma de `catalogo_precios.amazon_de` (+ `usd` del juez)."""
    from services import competencia_juez, precio_sugerido as ps

    async with await _cliente() as cli:
        items = await buscar(cli, q)
        precios = await cajas_de_compra(cli, list(dict.fromkeys(it["asin"] for it in items if it.get("asin"))))
    pubs = familias(items, precios)
    usd, nodo = 0.0, None
    if pubs:
        juez = await asyncio.to_thread(
            competencia_juez.juzgar_lote, {"titulo": titulo}, pubs, modelo=settings.competencia_juez_modelo,
            unidades_nuestras=unidades)
        usd = float(juez.get("usd") or 0)
        nodo = marcar(pubs, juez.get("veredictos") or {})
    clases = Counter(p.get("clase") for p in pubs)
    match = [p["precio"] for p in pubs if p.get("match") == "match"]
    lim = ps.limites(match)
    if lim:
        for p in pubs:
            if p.get("match") == "match" and not lim[0] <= p["precio"] <= lim[1]:
                p["match"], p["motivo"] = "no_match", "precio fuera de rango (2.5× la mediana)"
    est = ps.resumen([p["precio"] for p in pubs if p.get("match") == "match"]) or {}
    pubs.sort(key=lambda p: (("match", "sin_dato", "no_match").index(p.get("match", "sin_dato")), p["precio"]))
    return {"q": q, "vistos": len(pubs), "clases": dict(clases), "publicaciones": pubs, "nodo": nodo,
            "leidas": len(items), "con_precio": len(precios), "usd": round(usd, 6), **est}


def _paquete(it: dict[str, Any], mp: str) -> dict[str, float] | None:
    dm = next((d for d in it.get("dimensions") or [] if d.get("marketplaceId") == mp), None) \
        or ((it.get("dimensions") or [{}])[0])
    pk = (dm or {}).get("package") or {}
    try:
        lados = sorted((float(pk[k]["value"]) * _A_CM[pk[k]["unit"]] for k in ("length", "width", "height")),
                       reverse=True)
        peso = float(pk["weight"]["value"]) * _A_KG[pk["weight"]["unit"]]
    except (KeyError, TypeError, ValueError):
        return None
    if min(lados) <= 0 or peso <= 0:
        return None
    return {"largo_cm": round(lados[0], 1), "ancho_cm": round(lados[1], 1), "alto_cm": round(lados[2], 1),
            "peso_kg": round(peso, 3)}


def mediana_paquete(medidas: list[dict[str, float]]) -> dict[str, float] | None:
    """PURA. La mediana de cada lado hacia arriba al cm, y el peso hacia arriba a 0.01 kg."""
    if not medidas:
        return None
    out: dict[str, float] = {k: float(math.ceil(statistics.median(m[k] for m in medidas)))
                             for k in ("largo_cm", "ancho_cm", "alto_cm")}
    out["peso_kg"] = math.ceil(statistics.median(m["peso_kg"] for m in medidas) * 100) / 100
    return out


async def _items(cli: httpx.AsyncClient, asins: list[str]) -> list[dict[str, Any]]:
    if not asins:
        return []
    j = await _get(cli, "/catalog/2022-04-01/items", {
        "marketplaceIds": settings.amazon_marketplace_id, "identifiers": ",".join(asins[:20]),
        "identifiersType": "ASIN", "includedData": "dimensions,relationships,summaries"}, PAUSA_CATALOGO)
    return (j or {}).get("items") or []


async def medidas(asins: list[str], q: str | None = None) -> dict[str, Any]:
    """Medidas del paquete sugeridas: la mediana de las publicaciones que hicieron match (`asins`)."""
    mp = settings.amazon_marketplace_id
    leidas: list[dict[str, Any]] = []
    origen = "match"
    async with await _cliente() as cli:
        items = await _items(cli, list(dict.fromkeys(asins))[:MAX_MEDIDAS])
        hijos: dict[str, str] = {}
        for it in items:
            pk = _paquete(it, mp)
            r = _resumen_item(it, mp)
            if pk:
                leidas.append({**r, **pk})
            elif r["hijos"]:
                hijos[r["hijos"][0]] = r["asin"]
        for it in await _items(cli, list(hijos)):
            pk = _paquete(it, mp)
            if pk:
                leidas.append({**_resumen_item(it, mp), **pk})
        if not leidas and q:
            origen = "busqueda"
            j = await _get(cli, "/catalog/2022-04-01/items", {
                "marketplaceIds": mp, "keywords": q, "includedData": "dimensions,summaries", "pageSize": 20},
                PAUSA_CATALOGO)
            for it in (j or {}).get("items") or []:
                pk = _paquete(it, mp)
                if pk:
                    leidas.append({**_resumen_item(it, mp), **pk})
                if len(leidas) >= 5:
                    break
    pubs = [{"asin": x["asin"], "titulo": x["titulo"], "url": URL.format(x["asin"]),
             **{k: x[k] for k in ("largo_cm", "ancho_cm", "alto_cm", "peso_kg")}} for x in leidas]
    return {"sugeridas": mediana_paquete(leidas), "n": len(pubs), "origen": origen, "q": q, "publicaciones": pubs}
