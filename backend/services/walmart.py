"""
walmart.py — Cliente de la API de Walmart Marketplace MX (solo lectura).

Nace SOLO LECTURA, igual que el de Temu: publicar en Walmart va por FEEDS
(`scripts/publicar_walmart.py`), que es un camino con su propio ritmo y sus
propias trampas medidas. Esto es lo que el PANEL necesita para saber qué hay
arriba.

AUTENTICACIÓN, en dos pasos
───────────────────────────
`client_id:client_secret` en Basic → `POST /v3/token` → `access_token`, que
viaja en la cabecera `WM_SEC.ACCESS_TOKEN`. Las otras tres cabeceras
(`WM_SVC.NAME`, `WM_QOS.CORRELATION_ID`, `WM_MARKET: mx`) son obligatorias en
TODAS las llamadas: sin ellas la API contesta 400 sin decir cuál falta.

LO QUE ESTE CANAL TIENE Y LOS OTROS NO
──────────────────────────────────────
`publishedStatus` es una palabra —PUBLISHED / UNPUBLISHED— y no un número sin
documentar como en Temu. Aquí sí se puede afirmar qué está publicado.

Lo que NO tiene: **no hay API de atributos por categoría**. Ese hueco se cubre
con el esquema público (ver `scripts/walmart_field_requirements.py`), y por eso
las reglas de `channel.field_requirements` de este canal salen de un archivo.
"""
from __future__ import annotations

import base64
import logging
import os
import uuid
from typing import Any

import httpx

log = logging.getLogger("omnicanal.walmart")

HOST = "https://marketplace.walmartapis.com"
PAGE_SIZE = 50          # el tope que acepta /v3/items


def _credenciales() -> tuple[str, str]:
    from config import settings
    cid = (os.getenv("WM_CLIENT_ID")
           or getattr(settings, "wm_client_id", "") or "").strip()
    sec = (os.getenv("WM_CLIENT_SECRET")
           or getattr(settings, "wm_client_secret", "") or "").strip()
    return cid, sec


def disponible() -> bool:
    cid, sec = _credenciales()
    return bool(cid and sec)


def _cabeceras(token: str | None = None) -> dict[str, str]:
    """Las tres cabeceras obligatorias (+ el token si ya lo hay)."""
    d = {
        "WM_SVC.NAME": "Walmart Marketplace",
        "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
        "WM_MARKET": "mx",
        "Accept": "application/json",
    }
    if token:
        d["WM_SEC.ACCESS_TOKEN"] = token
    return d


async def token() -> str:
    """Un `access_token` nuevo. No se cachea: dura poco y pedirlo es barato."""
    cid, sec = _credenciales()
    if not (cid and sec):
        raise RuntimeError("Walmart no está configurado (faltan WM_CLIENT_ID / "
                           "WM_CLIENT_SECRET).")
    cab = _cabeceras()
    cab["Authorization"] = "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode()
    cab["Content-Type"] = "application/x-www-form-urlencoded"
    async with httpx.AsyncClient(timeout=40.0) as cli:
        r = await cli.post(f"{HOST}/v3/token", headers=cab,
                           data={"grant_type": "client_credentials"})
    r.raise_for_status()
    tk = r.json().get("access_token")
    if not tk:
        raise RuntimeError("Walmart devolvió un token vacío.")
    return tk


async def listar_items(tope: int = 5000) -> list[dict[str, Any]]:
    """
    El catálogo COMPLETO de la cuenta, paginado.

    Se pagina con `offset`, no con cursor. Cada artículo trae `sku`, `wpid`,
    `productName`, `productType`, `publishedStatus`, `price`, `gtin` y `upc`.

    Se deduplica por SKU: la API puede devolver el mismo artículo en dos páginas
    si el catálogo cambia mientras se recorre, y contar de más es peor que
    tardar un segundo más.
    """
    tk = await token()
    vistos: dict[str, dict[str, Any]] = {}
    offset = 0
    async with httpx.AsyncClient(timeout=90.0) as cli:
        while offset < tope:
            r = await cli.get(f"{HOST}/v3/items", headers=_cabeceras(tk),
                              params={"limit": str(PAGE_SIZE), "offset": str(offset)})
            if r.status_code != 200:
                log.warning("walmart /v3/items HTTP %s en offset %s: %s",
                            r.status_code, offset, r.text[:200])
                break
            j = r.json()
            lote = j.get("ItemResponse") or []
            for it in lote:
                sku = str(it.get("sku") or "").strip()
                if sku:
                    vistos.setdefault(sku, it)
            if len(lote) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    return list(vistos.values())


async def total_items() -> int | None:
    """Cuántos artículos dice Walmart que hay, sin traerlos todos."""
    tk = await token()
    async with httpx.AsyncClient(timeout=60.0) as cli:
        r = await cli.get(f"{HOST}/v3/items", headers=_cabeceras(tk),
                          params={"limit": "1"})
    if r.status_code != 200:
        return None
    return r.json().get("totalItems")


async def listar_pedidos(desde: str, tope: int = 200) -> list[dict[str, Any]]:
    """
    Las ventas desde una fecha (`YYYY-MM-DD`), paginadas.

    ⚠️ Este endpoint NUNCA se había llamado. La primera vez contestó 8 ventas
    reales entre el 14-ago y el 2-sep-2026 que nadie estaba ingiriendo.

    ⚠️ EL PAGINADO REPITE LA MISMA PÁGINA, Y ESO SALIÓ CARO (4-sep-2026).
    `nextCursorMark` es una marca de INICIO, no un "siguiente": cuando ya no hay
    más, Walmart la devuelve igual y contesta **el mismo lote otra vez**. La
    primera versión cortaba comparando cursores, así que alcanzaba a hacer una
    segunda vuelta y devolvía **16 órdenes para 8 ventas**. Con eso el llenado
    inicial creó 8 pedidos de Woo duplicados — que hubo que mandar a la papelera.

    Por eso ahora se corta por el CONTENIDO, no por el cursor: se lleva registro
    de los `purchaseOrderId` ya vistos y, si una página no aporta ninguno nuevo,
    se acabó. Y la salida va deduplicada de todas formas: el que consume esto
    crea PEDIDOS, y ahí un duplicado no es un dato de más, es dinero contado dos
    veces.
    """
    tk = await token()
    salida: list[dict[str, Any]] = []
    vistos: set[str] = set()
    cursor: str | None = None
    LIMITE = 100
    async with httpx.AsyncClient(timeout=90.0) as cli:
        while len(salida) < tope:
            params: dict[str, str] = {"limit": str(LIMITE), "createdStartDate": desde}
            if cursor:
                params["cursor"] = cursor
            r = await cli.get(f"{HOST}/v3/orders", params=params,
                              headers=_cabeceras(tk))
            if r.status_code != 200:
                log.warning("walmart.listar_pedidos: HTTP %s — %s",
                            r.status_code, r.text[:200])
                break
            d = r.json()
            lote = d.get("order") or []
            nuevas = [o for o in lote
                      if str(o.get("purchaseOrderId") or "") not in vistos]
            for o in nuevas:
                vistos.add(str(o.get("purchaseOrderId") or ""))
            salida.extend(nuevas)
            # Se acabó si: no vino nada, la página no aportó NADA nuevo (es la
            # repetición), vino incompleta, o no hay cursor para seguir.
            cursor = (d.get("meta") or {}).get("nextCursorMark")
            if not lote or not nuevas or len(lote) < LIMITE or not cursor:
                break
    return salida[:tope]


async def feed_estado(feed_id: str, con_detalle: bool = True) -> dict[str, Any]:
    """
    El VEREDICTO de un feed: qué SKU entró, cuál no y por qué.

    Es la otra mitad del botón de publicar. Walmart contesta el envío con un
    `feedId` y nada más; el resultado real llega minutos después y SOLO se sabe
    preguntando aquí. Dar por bueno el acuse fue lo que produjo los "9 feeds sin
    fallos" del 4-ago que en realidad fueron cero.

    ⚠️ El detalle pagina y TOPA EN 50 entidades. Con lotes más grandes el
    resumen por SKU sale INCOMPLETO **y en silencio** — por eso `TAM_LOTE` del
    publicador por tandas es 50.
    """
    tk = await token()
    async with httpx.AsyncClient(timeout=90.0) as cli:
        r = await cli.get(f"{HOST}/v3/feeds/{feed_id}",
                          params={"includeDetails": "true" if con_detalle else "false"},
                          headers=_cabeceras(tk))
    if r.status_code != 200:
        return {"ok": False, "feed_id": feed_id,
                "motivo": f"HTTP {r.status_code}: {r.text[:200]}"}
    d = r.json()
    articulos = []
    for it in ((d.get("itemDetails") or {}).get("itemIngestionStatus") or []):
        errores = [
            {"tipo": e.get("type"), "campo": e.get("field"),
             "codigo": e.get("code"), "mensaje": e.get("description")}
            for e in ((it.get("ingestionErrors") or {}).get("ingestionError") or [])
        ]
        articulos.append({
            "sku": it.get("sku"),
            "estado": it.get("ingestionStatus"),
            "item_id": it.get("itemid"),
            "errores": errores,
        })
    return {
        "ok": True, "feed_id": feed_id,
        "estado": d.get("feedStatus"),
        "recibidos": d.get("itemsReceived"),
        "exitosos": d.get("itemsSucceeded"),
        "fallidos": d.get("itemsFailed"),
        "en_proceso": d.get("itemsProcessing"),
        "enviado_at": d.get("feedSubmissionDate"),
        "articulos": articulos,
        # El corte de 50: si el feed traía más, este resumen está incompleto.
        "detalle_completo": (d.get("itemsReceived") or 0) <= 50,
    }
