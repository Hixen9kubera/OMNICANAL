"""
f_walmart.py — El catálogo de Walmart México, EN VIVO: `GET /v3/items`, paginado.

Cada artículo trae SKU, nombre, tipo, precio y `publishedStatus`. Walmart MX NO
devuelve la imagen ni la comisión por API: la foto sale vacía y la comisión que
muestra la página es un SUPUESTO declarado (15%), marcado como tal.

Se pagina con `offset` y se deduplica por SKU (el catálogo puede moverse a media
lectura, y contar de más es peor que tardar un segundo).
"""
from __future__ import annotations

import base64
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from comun import Cfg, Red, ahora_iso, aviso, escribir_json, num

ANFITRION = "https://marketplace.walmartapis.com"
PAGINA = 50


def _cabeceras(token: str | None = None) -> dict[str, str]:
    d = {"WM_SVC.NAME": "Walmart Marketplace", "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
         "WM_MARKET": "mx", "Accept": "application/json"}
    if token:
        d["WM_SEC.ACCESS_TOKEN"] = token
    return d


def extraer(cfg: Cfg, salida: Path) -> dict[str, Any]:
    cid, sec = cfg("WM_CLIENT_ID"), cfg("WM_CLIENT_SECRET")
    if not (cid and sec):
        raise RuntimeError("Walmart sin configurar (WM_CLIENT_ID / WM_CLIENT_SECRET)")
    red = Red(rps=2.0, timeout=90.0)
    inicio = ahora_iso()
    cab = _cabeceras()
    cab["Authorization"] = "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode()
    cab["Content-Type"] = "application/x-www-form-urlencoded"
    r = red.lectura_sin_get(f"{ANFITRION}/v3/token", headers=cab,
                            data={"grant_type": "client_credentials"})
    if r.status_code != 200:
        raise RuntimeError(f"Walmart no dio token (HTTP {r.status_code})")
    token = r.json().get("access_token")

    vistos: dict[str, dict[str, Any]] = {}
    declarado: int | None = None
    avisos: list[str] = []
    offset = 0
    while offset < 20000:
        r = red.get(f"{ANFITRION}/v3/items", headers=_cabeceras(token),
                    params={"limit": str(PAGINA), "offset": str(offset)})
        if r.status_code != 200:
            avisos.append(f"HTTP {r.status_code} en offset {offset}: {r.text[:160]}")
            break
        j = r.json()
        declarado = j.get("totalItems", declarado)
        lote = j.get("ItemResponse") or []
        for it in lote:
            sku = str(it.get("sku") or "").strip()
            if not sku or sku in vistos:
                continue
            nombre = it.get("productName")
            estado = it.get("publishedStatus") or "SIN_ESTADO"
            vistos[sku] = {
                "sku": sku, "id": it.get("wpid"), "titulo": nombre, "imagen": None,
                "precio": num((it.get("price") or {}).get("amount")),
                "moneda": (it.get("price") or {}).get("currency") or "MXN",
                "estado": estado, "a_la_venta": estado == "PUBLISHED",
                "tipo": it.get("productType"), "upc": it.get("upc"), "gtin": it.get("gtin"),
                "link": ("https://www.walmart.com.mx/search?q=" + urllib.parse.quote(nombre)
                         if nombre else None),
            }
        if len(lote) < PAGINA:
            break
        offset += PAGINA
    if declarado is not None and len(vistos) < declarado:
        avisos.append(f"censo incompleto: {len(vistos)} leídos de {declarado} que declara Walmart")

    lista = sorted(vistos.values(), key=lambda f: f["sku"])
    doc = {
        "canal": "walmart", "cuenta": "Walmart MX",
        "fuente": "Walmart Marketplace MX · GET /v3/items",
        "leido_desde": inicio, "leido_hasta": ahora_iso(),
        "declarado_por_el_canal": declarado, "publicaciones": len(lista),
        "a_la_venta": sum(1 for f in lista if f["a_la_venta"]),
        "avisos": avisos, "filas": lista,
    }
    escribir_json(salida / "datos" / "walmart.json", doc)
    aviso(f"walmart: {len(lista)} artículos, {doc['a_la_venta']} publicados")
    return {k: v for k, v in doc.items() if k != "filas"}
