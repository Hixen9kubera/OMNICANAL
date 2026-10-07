"""
mercado_amazon.py — A cuánto vende la competencia en Amazon México, por palabra clave. Solo SP-API, solo GET.

El método es el que se probó el 6-oct-2026 con sábanas, llevado a todo el catálogo:

  1. `searchCatalogItems` (Catalog Items 2022-04-01) con la palabra clave: las
     publicaciones en orden de relevancia.
  2. `getCompetitivePricing` (Pricing v0): la CAJA DE COMPRA de cada una
     (`CompetitivePriceId: "1"`). Lo que no tiene caja de compra no entra.
  3. Una cifra por FAMILIA: todas las variantes de un mismo padre cuentan una vez,
     con la mediana de sus precios. Si no, un producto con 40 colores pesa 40 veces.
  4. El juez (el mismo de Mercado Libre) deja solo las que son el mismo producto.

Lo que cambia contra el estudio de un solo producto, y hay que decirlo: aquí se lee
la PRIMERA página de cada palabra clave (10 publicaciones), no las 47, y no se
reconstruye el ranking de la categoría ni se abre la página pública (estrellas,
reseñas, «comprados el mes pasado»). Con miles de palabras clave eso no cabe en un
día: Amazon deja pedir precios dos veces por segundo… cada cuatro. Para fijar el
precio de UN producto sigue valiendo el estudio completo.

La API no da ventas ni visitas de publicaciones ajenas. No se estiman.

Dos palabras clave por vuelta: dos búsquedas y UNA llamada de precios (20 ASIN),
que es el límite que manda.
"""
from __future__ import annotations

import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from comun import Cfg, Red, ahora_iso, aviso, escribir_json
from f_amazon import _token
from ia_titulos import DeepSeek
from mercado_comun import cargar, firma, grupos_de_busqueda, juzgar, peso_grupo, pesos, resumir

POR_TERMINO = 10


def _buscar(red: Red, base: str, mp: str, h: dict[str, str], q: str) -> list[dict[str, Any]] | None:
    r = red.get(f"{base}/catalog/2022-04-01/items", rps=1.8, headers=h, params={
        "marketplaceIds": mp, "keywords": q, "includedData": "summaries,relationships", "pageSize": 20})
    if r.status_code != 200:
        return None
    salida = []
    for it in (r.json().get("items") or [])[:POR_TERMINO]:
        resumen = next((s for s in it.get("summaries") or [] if s.get("marketplaceId") == mp), None) \
            or ((it.get("summaries") or [{}])[0])
        padre = None
        for rel in it.get("relationships") or []:
            for x in rel.get("relationships") or []:
                if x.get("parentAsins"):
                    padre = x["parentAsins"][0]
        salida.append({"asin": it.get("asin"), "t": resumen.get("itemName") or "",
                       "marca": resumen.get("brand") or "", "padre": padre or it.get("asin")})
    return salida


def _precios(red: Red, base: str, mp: str, h: dict[str, str], asins: list[str]) -> dict[str, float] | None:
    if not asins:
        return {}
    r = red.get(f"{base}/products/pricing/v0/competitivePrice", rps=0.48, headers=h,
                params={"MarketplaceId": mp, "Asins": ",".join(asins), "ItemType": "Asin"})
    if r.status_code != 200:
        return None
    salida: dict[str, float] = {}
    for x in r.json().get("payload") or []:
        comp = (((x.get("Product") or {}).get("CompetitivePricing") or {}).get("CompetitivePrices")) or []
        caja = next((c for c in comp if str(c.get("CompetitivePriceId")) == "1"), None)
        if not caja:
            continue
        precio = (caja.get("Price") or {})
        monto = ((precio.get("LandedPrice") or {}).get("Amount")
                 or (precio.get("ListingPrice") or {}).get("Amount"))
        if monto:
            salida[x.get("ASIN")] = float(monto)
    return salida


def _familias(items: list[dict[str, Any]], precios: dict[str, float]) -> list[dict[str, Any]]:
    por_padre: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        if it["asin"] in precios:
            por_padre.setdefault(it["padre"], []).append(it)
    return [{"id": padre, "t": m[0]["t"], "p": round(statistics.median(precios[x["asin"]] for x in m), 2)}
            for padre, m in por_padre.items()]


def extraer(cfg: Cfg, salida: Path, limite: int = 0) -> dict[str, Any]:
    base, mp = cfg("AMAZON_SP_API_ENDPOINT").rstrip("/"), cfg("AMAZON_MARKETPLACE_ID")
    if not (base and mp and cfg("AMAZON_REFRESH_TOKEN")):
        raise RuntimeError("Amazon sin configurar (.env.amazon)")
    ruta = salida / "datos" / "mercado_amazon.json"
    doc = cargar(ruta)
    doc.setdefault("busquedas", {})
    grupos, de_sku = grupos_de_busqueda(salida)
    doc["sku"] = de_sku
    pendientes = {c: g for c, g in grupos.items() if (doc["grupos"].get(c) or {}).get("h") != firma(g)}
    por_termino: dict[str, list[str]] = {}
    for c, g in pendientes.items():
        por_termino.setdefault(g["q"], []).append(c)
    w = pesos(salida)                 # primero las palabras clave con más piezas detrás
    terminos = sorted(por_termino, key=lambda q: -sum(peso_grupo(pendientes[c], w) for c in por_termino[q]))
    if limite:
        terminos = terminos[:limite]
    aviso(f"mercado Amazon: {len(grupos)} grupos · {len(pendientes)} por juzgar · {len(terminos)} palabras clave")
    red, ia = Red(rps=1.8, timeout=40.0), DeepSeek(cfg)
    estado = {"token": _token(cfg, red), "desde": time.monotonic(), "hechos": 0, "fallos": 0}
    candado = threading.Lock()

    def cabecera() -> dict[str, str]:
        if time.monotonic() - estado["desde"] > 45 * 60:       # el token de Amazon dura una hora
            estado["token"], estado["desde"] = _token(cfg, red), time.monotonic()
        return {"x-amz-access-token": estado["token"]}

    def guardar() -> None:
        doc["actualizado"], doc["uso_ia"], doc["costo_ia_usd"] = ahora_iso(), ia.uso, ia.costo_usd()
        escribir_json(ruta, doc)

    def juzgar_grupo(clave: str, rivales: list[dict[str, Any]]) -> None:
        g = pendientes[clave]
        veredictos = juzgar(ia, g["t"], g["u"], rivales) if rivales else []
        if rivales and not veredictos:
            return
        fila = resumir(g["u"], rivales, veredictos)
        fila.update({"q": g["q"], "h": firma(g)})
        with candado:
            doc["grupos"][clave] = fila

    with ThreadPoolExecutor(max_workers=5) as jueces:
        for i in range(0, len(terminos), 2):
            par = terminos[i:i + 2]
            encontrados: dict[str, list[dict[str, Any]]] = {}
            for q in par:
                with candado:
                    listo = doc["busquedas"].get(q)
                if listo is not None:
                    for c in por_termino[q]:
                        jueces.submit(juzgar_grupo, c, listo)
                    continue
                items = _buscar(red, base, mp, cabecera(), q)
                if items is None:
                    estado["fallos"] += 1
                    continue
                encontrados[q] = items
            asins = [it["asin"] for items in encontrados.values() for it in items if it.get("asin")]
            precios = _precios(red, base, mp, cabecera(), asins[:20]) if asins else {}
            if precios is None:
                estado["fallos"] += 1
                continue
            for q, items in encontrados.items():
                rivales = _familias(items, precios)
                with candado:
                    doc["busquedas"][q] = rivales
                for c in por_termino[q]:
                    jueces.submit(juzgar_grupo, c, rivales)
            estado["hechos"] += len(par)
            if estado["hechos"] % 100 < 2:
                with candado:
                    guardar()
                    con = sum(1 for x in doc["grupos"].values() if x.get("n"))
                aviso(f"mercado Amazon: {estado['hechos']}/{len(terminos)} palabras clave · {con} grupos con precio · "
                      f"fallos {estado['fallos']} · IA ≈ ${ia.costo_usd():.3f}")
    guardar()
    con = sum(1 for x in doc["grupos"].values() if x.get("n"))
    return {"grupos": len(grupos), "grupos_juzgados": len(doc["grupos"]), "grupos_con_precio": con,
            "palabras_clave": len(doc["busquedas"]), "fallos": estado["fallos"], "costo_ia_usd": ia.costo_usd()}
