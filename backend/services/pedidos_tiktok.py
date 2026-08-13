"""
pedidos_tiktok.py — La venta de TikTok se convierte en PEDIDO de WooCommerce.

CÓMO ENCAJA
-----------
El receptor (`routers/webhooks.py::recibir_tiktok`) ya existía y estaba en modo
OBSERVAR: registraba el evento y no escribía nada. Este módulo es la otra mitad
— el que convierte esa notificación en un pedido, con el mismo aparato que ya
usan Mercado Libre y Amazon:

    evento de TikTok → id de la orden → la orden COMPLETA por API
                     → `pedidos_ml.sincronizar` (candado, precio congelado,
                       idempotencia, pedido de Woo, channel.orders)

**No se reimplanta nada de eso.** `pedidos_ml.sincronizar` es el único sitio
donde nace un pedido, cualquiera que sea el canal; lo demás son traductores.

POR QUÉ EL EVENTO NO SE CREE Y SE PREGUNTA
------------------------------------------
Del webhook solo se toma **el id**. Todo lo demás —líneas, precios, estado,
comprador— se pide a la API, porque el evento es una notificación de que algo
cambió, no un documento contable. Además la URL es pública: si el pedido se
armara con lo que llega, cualquiera podría inventar una venta. Con este diseño,
un evento falso a lo más provoca una consulta que no encuentra nada.

⚠️ TIKTOK DESCUENTA STOCK. La mercancía sale de NUESTRA bodega (el
`warehouse_id` de TikTok es dónde la recogen, no quién la guarda), así que estos
pedidos NO llevan la protección que sí llevan ML FULL y Amazon FBA.

Nace APAGADO (`PEDIDOS_TIKTOK_ENABLED`): crear pedidos toca inventario y
contabilidad, y eso se enciende con el dale de Brandon, no con un deploy.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.pedidos_tiktok")

CUENTA = "TIKTOK"

# Estado de TikTok → estado del pedido de WooCommerce.
# `UNPAID` NO se trae a propósito: todavía no es una venta, y crear el pedido
# ahí descontaría stock por algo que puede no pagarse nunca.
_ESTADOS_WC = {
    "AWAITING_SHIPMENT": "processing",
    "AWAITING_COLLECTION": "processing",
    "PARTIALLY_SHIPPING": "processing",
    "IN_TRANSIT": "processing",
    "DELIVERED": "completed",
    "COMPLETED": "completed",
    "CANCELLED": "cancelled",
}

_ultimo: dict[str, Any] = {"estado": "sin correr", "ts": None, "pedidos": 0}


def estado() -> dict[str, Any]:
    return {**_ultimo, "habilitado": settings.pedidos_tiktok_enabled}


def id_de_evento(payload: dict[str, Any]) -> str | None:
    """
    El id de la orden dentro del evento, probando los alias posibles.

    Se prueban varios A PROPÓSITO en vez de fijar uno: el esquema real se
    confirma con el PRIMER evento verdadero, y hasta entonces afirmar la forma
    exacta sería inventar. Es el mismo criterio que el receptor de Temu.
    """
    d = payload.get("data") or payload
    for k in ("order_id", "orderId", "order_no", "id"):
        v = d.get(k)
        if v:
            return str(v)
    return None


def _normalizar(o: dict[str, Any]) -> dict[str, Any]:
    """Orden de TikTok → el dict que espera `pedidos_ml.construir_payload`."""
    lineas = []
    for it in (o.get("line_items") or []):
        # TikTok manda UNA línea por unidad vendida, no una línea con cantidad:
        # dos piezas del mismo SKU llegan como dos entradas. Se agrupan abajo.
        lineas.append({
            "item_id": str(it.get("id") or ""),
            "sku": (it.get("seller_sku") or it.get("sku_id") or "").strip(),
            "titulo": it.get("product_name") or "",
            "variacion_id": None,
            "cantidad": 1,
            "precio_unitario": float(it.get("sale_price") or it.get("original_price") or 0),
            "precio_lista": float(it.get("original_price") or 0),
            # La comisión llega en `platform_discount`/`payment` según el evento
            # y no está confirmada contra una venta real: se deja en 0 antes que
            # apuntar un número que nadie verificó. Mismo criterio que Amazon.
            "comision_ml": 0.0,
        })
    # Agrupado por (sku, precio): conserva el importe y evita 20 líneas iguales.
    agrupadas: dict[tuple, dict] = {}
    for l in lineas:
        clave = (l["sku"], l["precio_unitario"])
        if clave in agrupadas:
            agrupadas[clave]["cantidad"] += 1
        else:
            agrupadas[clave] = l
    items = list(agrupadas.values())

    pago = o.get("payment") or {}
    total = float(pago.get("total_amount") or
                  sum(i["precio_unitario"] * i["cantidad"] for i in items))
    creado = o.get("create_time")
    fecha = (datetime.fromtimestamp(int(creado), tz=timezone.utc).isoformat()
             if creado else None)
    recipiente = o.get("recipient_address") or {}
    return {
        "id": str(o.get("id") or ""),
        "cuenta": CUENTA,
        "estado": o.get("status"),
        "detalle": o.get("delivery_option_name") or "",
        "etiquetas": [],
        "fecha": fecha,
        "total": total,
        "pagado": total,
        "moneda": pago.get("currency") or "MXN",
        "envio_costo": float(pago.get("shipping_fee") or 0),
        "items": items,
        "envio": {"logistica": "tiktok", "estado": o.get("status") or ""},
        # Falso a propósito: el stock sale de NUESTRA bodega, así que el pedido
        # descuenta. Ponerlo en True lo protegería como si fuera FULL/FBA y el
        # inventario se quedaría alto tras cada venta.
        "es_full": False,
        "pago_estado": o.get("status"),
        "pago_fecha": fecha,
        "comprador": {"id": o.get("user_id"), "nick": "",
                      "nombre": recipiente.get("name") or "Comprador",
                      "apellido": "TikTok"},
    }


async def _traer(order_id: str) -> dict[str, Any] | None:
    """La orden COMPLETA desde TikTok. None si no se pudo."""
    from services import tiktok as tk
    token, cipher = tk.access_token(), tk.cipher()
    if not (token and cipher):
        log.warning("pedidos_tiktok: sin token o sin shop_cipher")
        return None
    try:
        data = await tk.llamar("/order/202309/orders", token,
                               {"shop_cipher": cipher, "ids": order_id})
        ordenes = data.get("orders") or []
        return ordenes[0] if ordenes else None
    except Exception as exc:  # noqa: BLE001
        log.warning("pedidos_tiktok: no se pudo traer la orden %s: %s", order_id, exc)
        return None


async def procesar(order_id: str) -> dict[str, Any]:
    """
    Una orden de TikTok → pedido de WooCommerce. Idempotente por id de orden.

    Nunca lanza: la llama un webhook que debe responder 200 pase lo que pase.
    """
    if not settings.pedidos_tiktok_enabled:
        return {"ok": False, "motivo": "PEDIDOS_TIKTOK_ENABLED apagado", "id": order_id}
    try:
        cruda = await _traer(order_id)
        if not cruda:
            return {"ok": False, "motivo": "la orden no se pudo leer de TikTok",
                    "id": order_id}
        orden = _normalizar(cruda)
        if not any(i["sku"] for i in orden["items"]):
            return {"ok": False, "id": order_id,
                    "motivo": "la orden no trae SKU legible en ninguna línea"}
        destino = _ESTADOS_WC.get(str(cruda.get("status") or "").upper())
        if not destino:
            # `UNPAID` cae aquí: se registra y NO se crea pedido.
            return {"ok": False, "id": order_id, "ignorado": True,
                    "motivo": f"estado '{cruda.get('status')}' no genera pedido"}
        from services import pedidos_ml
        r = await pedidos_ml.sincronizar(order_id, forzar_estado=destino,
                                         orden=orden, proteger_stock=False)
        _ultimo.update(estado="ok", ts=datetime.now(timezone.utc).isoformat(),
                       pedidos=_ultimo.get("pedidos", 0) + (1 if r.get("ok") else 0))
        log.info("pedido TikTok %s → %s (%s)", order_id, r.get("accion"), destino)
        return r
    except Exception as exc:  # noqa: BLE001
        log.exception("pedidos_tiktok.procesar(%s) falló", order_id)
        return {"ok": False, "id": order_id, "motivo": str(exc)[:300]}
