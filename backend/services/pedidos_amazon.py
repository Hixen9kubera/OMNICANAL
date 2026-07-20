"""
pedidos_amazon.py — Ventas de Amazon → pedidos de WooCommerce (sondeo).

Amazon NO tiene webhooks simples (su vía "real-time" exige cuenta AWS + cola
SQS + suscripción ORDER_CHANGE). Con ~4 órdenes/día, un sondeo cada 5 min ES
tiempo real en la práctica (detección media 2.5 min) sin infraestructura nueva.
Si el volumen crece, SQS se monta encima de este mismo código: solo cambia el
timbre, la tubería de pedidos es la misma.

Reutiliza `pedidos_ml.sincronizar(orden=...)` — el mismo candado anti-duplicados,
la misma idempotencia, la misma tabla (`pedidos_ml` con cuenta='AMAZON') y el
mismo tab de Ventas. Reglas de stock, calcadas de ML:

  FBA (FulfillmentChannel=AFN) → sale del almacén de AMAZON → pedido protegido
  MFN (=MFN)                   → sale de TU bodega          → descuenta en Woo

La comisión de Amazon no viene en la API de órdenes (requiere Finances API);
por ahora se registra 0 — mejora futura.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from config import settings
from services import amazon, db, pedidos_ml

log = logging.getLogger("omnicanal.pedidos_amazon")

_MARGEN_MIN = 10       # re-mira este margen hacia atrás (updates tardíos)
_MAX_PAGINAS = 10      # 10×100 órdenes por pasada: tope de seguridad

# Estado Amazon → estado WooCommerce (directo, sin pasar por el mapa de ML).
_ESTADOS_WC = {
    "Pending": "on-hold",            # pago en proceso
    "Unshipped": "processing",
    "PartiallyShipped": "processing",
    "Shipped": "completed",
    "InvoiceUnconfirmed": "processing",
    "Canceled": "cancelled",
    "Unfulfillable": "cancelled",
}
_ultimo: dict[str, Any] = {"estado": "sin_ejecutar"}


def estado() -> dict[str, Any]:
    return _ultimo


async def _get(cli: httpx.AsyncClient, tok: str, ruta: str, params: dict) -> dict:
    r = await cli.get(f"{settings.amazon_sp_api_endpoint}{ruta}", params=params,
                      headers={"x-amz-access-token": tok})
    if r.status_code == 429:  # rate limit: una pausa y un reintento bastan aquí
        await asyncio.sleep(2.0)
        r = await cli.get(f"{settings.amazon_sp_api_endpoint}{ruta}", params=params,
                          headers={"x-amz-access-token": tok})
    r.raise_for_status()
    return r.json().get("payload") or {}


def _normalizar(o: dict, items: list[dict]) -> dict:
    """Orden de Amazon → el dict que espera pedidos_ml.construir_payload."""
    es_fba = str(o.get("FulfillmentChannel")) == "AFN"
    lineas = []
    for it in items:
        qty = int(it.get("QuantityOrdered") or 0) or 1
        total_linea = float((it.get("ItemPrice") or {}).get("Amount") or 0)
        lineas.append({
            "item_id": str(it.get("OrderItemId") or ""),
            "sku": (it.get("SellerSKU") or "").strip(),
            "titulo": it.get("Title") or "",
            "variacion_id": None,
            "cantidad": qty,
            # ItemPrice es el TOTAL de la línea; el unitario se deriva.
            "precio_unitario": round(total_linea / qty, 2) if qty else total_linea,
            "precio_lista": 0.0,
            "comision_ml": 0.0,  # Finances API pendiente
        })
    total = float((o.get("OrderTotal") or {}).get("Amount") or
                  sum(l["precio_unitario"] * l["cantidad"] for l in lineas))
    return {
        "id": str(o.get("AmazonOrderId")),
        "cuenta": "AMAZON",
        "estado": o.get("OrderStatus"),
        "detalle": o.get("FulfillmentChannel"),
        "etiquetas": [],
        "fecha": o.get("PurchaseDate"),      # creado = fecha de la VENTA
        "total": total,
        "pagado": total,
        "moneda": (o.get("OrderTotal") or {}).get("CurrencyCode") or "MXN",
        "envio_costo": 0.0,
        "items": lineas,
        "envio": {"logistica": "fulfillment" if es_fba else "mfn",
                  "estado": "delivered" if o.get("OrderStatus") == "Shipped" else ""},
        "es_full": es_fba,                    # FBA = almacén de Amazon (como FULL)
        "pago_estado": o.get("OrderStatus"),
        "pago_fecha": o.get("PurchaseDate"),
        "comprador": {"id": None, "nick": "",
                      "nombre": (o.get("BuyerInfo") or {}).get("BuyerName") or "Comprador",
                      "apellido": "Amazon"},
    }


def _desde() -> str:
    """LastUpdatedAfter: el último `actualizado` de AMAZON menos el margen."""
    fila = db.fetch_one(
        "SELECT MAX(actualizado) m FROM pedidos_ml WHERE cuenta='AMAZON'")
    base = (fila and fila.get("m")) or (datetime.now(timezone.utc).replace(tzinfo=None)
                                        - timedelta(days=7))
    base = base - timedelta(minutes=_MARGEN_MIN)
    return base.strftime("%Y-%m-%dT%H:%M:%SZ")


async def revisar(proteger_stock: bool = False,
                  desde_iso: str | None = None) -> dict[str, Any]:
    """
    Una pasada del sondeo: órdenes actualizadas desde la última vez → pedidos.
    `proteger_stock=True` solo para la carga histórica (piezas MFN que ya
    salieron antes de que Woo fuera el maestro — descontarlas hoy duplicaría).
    """
    creados = actualizados = sin_cambio = errores = 0
    try:
        tok = await amazon._access_token()
        async with httpx.AsyncClient(timeout=30.0) as cli:
            params = {"MarketplaceIds": settings.amazon_marketplace_id,
                      "LastUpdatedAfter": desde_iso or _desde()}
            ordenes: list[dict] = []
            for _ in range(_MAX_PAGINAS):
                pl = await _get(cli, tok, "/orders/v0/orders", params)
                ordenes += pl.get("Orders") or []
                nt = pl.get("NextToken")
                if not nt:
                    break
                params = {"NextToken": nt}
                await asyncio.sleep(0.6)

            previos = {f["ml_order_id"]: f["estado_wc"] for f in db.fetch_all(
                "SELECT ml_order_id, estado_wc FROM pedidos_ml WHERE cuenta='AMAZON'")}

            for o in ordenes:
                oid = str(o.get("AmazonOrderId"))
                destino = _ESTADOS_WC.get(str(o.get("OrderStatus")), "processing")
                if previos.get(oid) == destino:
                    sin_cambio += 1     # nada nuevo: ni items ni Woo se tocan
                    continue
                try:
                    it = await _get(cli, tok, f"/orders/v0/orders/{oid}/orderItems", {})
                    orden = _normalizar(o, it.get("OrderItems") or [])
                    r = await pedidos_ml.sincronizar(
                        oid, forzar_estado=destino, orden=orden,
                        proteger_stock=proteger_stock)
                    if r.get("ok"):
                        creados += (r.get("accion") == "creado")
                        actualizados += (r.get("accion") == "actualizado")
                    else:
                        errores += 1
                        log.warning("pedido Amazon %s falló: %s", oid, r.get("motivo"))
                    await asyncio.sleep(0.6)   # getOrderItems: 0.5 rps
                except Exception as exc:  # noqa: BLE001
                    errores += 1
                    log.warning("orden Amazon %s: %s", oid, exc)
    except Exception as exc:  # noqa: BLE001
        _ultimo.update(estado=f"error: {exc}", ts=datetime.now(timezone.utc).isoformat())
        log.warning("sondeo Amazon falló: %s", exc)
        return _ultimo
    _ultimo.update(estado="ok", ts=datetime.now(timezone.utc).isoformat(),
                   ordenes=len(ordenes), creados=creados,
                   actualizados=actualizados, sin_cambio=sin_cambio,
                   errores=errores)
    if creados or actualizados or errores:
        log.info("Pedidos Amazon: %d creados, %d actualizados, %d sin cambio, %d err",
                 creados, actualizados, sin_cambio, errores)
    return _ultimo
