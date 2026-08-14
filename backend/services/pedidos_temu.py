"""
pedidos_temu.py — La venta de Temu se vuelve pedido de WooCommerce.

    webhook `bg_order_status_change_event`  → id de la orden
      → `bg.order.detail.v2.get`            → SKUs y cantidades
        → `pedidos_ml.sincronizar`          → candado, idempotencia, pedido de
                                              Woo, channel.orders

**No se reimplanta nada de eso.** `pedidos_ml.sincronizar` es el único sitio
donde nace un pedido, venga del canal que venga; lo demás son traductores.

DEL EVENTO SOLO SE TOMA EL ID
─────────────────────────────
`bg_order_status_change_event` trae únicamente `mallId`, `parentOrderSn`,
`orderSn`, `orderStatus` y `updateTime`: **sin precio, sin SKU, sin cantidad**.
Es un aviso, no un documento. Todo lo demás se pregunta.

EL PRECIO NO EXISTE, Y HAY QUE DECIRLO
──────────────────────────────────────
Medido el 14-ago contra las dos órdenes reales que tenemos: `bg.order.detail.v2.get`
devuelve `productList[].extCode` (nuestro SKU ✓), `quantity` ✓ y `orderStatus` ✓,
pero **ni un solo campo de dinero** — `paymentInfo` viene en `null`. Y las dos
APIs de importes (`bg.order.amount.query`, `temu.order.amount.v2.query`) están
bloqueadas con `3000032`: son *sensitive APIs* y hay que pedirle el permiso a
Temu.

Consecuencia: a diferencia de Mercado Libre, **el pedido de Temu NO puede
congelar el precio real de venta**. Aquí se usa el precio del catálogo
(`channel.listings.price`) y se marca en el pedido que ese número NO es lo que
Temu nos pagó. Inventar un precio verosímil habría contaminado el análisis de
márgenes sin que nada diera error, que es la peor clase de dato malo.

EL ENUM DE `orderStatus` NO ESTÁ DOCUMENTADO
────────────────────────────────────────────
Temu no publica qué significa cada número y el sondeo lo marcó explícitamente
como no verificado. Lo único con evidencia: las dos órdenes reales que llegaron
por M2E traen `orderStatus = 4` y son ventas válidas.

Por eso el mapa de abajo tiene UN código y el resto cae en "registrar sin
crear". El modo de fallo importa: un código desconocido que no crea pedido se
arregla reprocesando; un código desconocido que SÍ crea pedido descuenta stock
de una venta que quizá se canceló, y eso ya cuesta dinero.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.pedidos_temu")

CANAL = "temu"
CUENTA = "TEMU"      # `pedidos_ml._ESPEJO_ORIGEN` ya lo mapea al canal temu

# Lo ÚNICO con evidencia: las 2 órdenes reales de agosto traen 4 y son ventas
# vivas. Cada código nuevo se agrega cuando se vea uno y se sepa qué era —
# nunca "por si acaso".
_ESTADOS_WC: dict[int, str] = {
    4: "processing",
}

_ultimo: dict[str, Any] = {"estado": "sin correr", "ts": None, "pedidos": 0}


def estado() -> dict[str, Any]:
    return dict(_ultimo)


def id_de_evento(payload: dict[str, Any]) -> str | None:
    """El `parentOrderSn` del aviso. Se prueban alias: el nombre exacto que usa
    Temu en el sobre cifrado se confirma con el primer evento real."""
    for clave in ("parentOrderSn", "parent_order_sn", "parentOrderSN",
                  "orderSn", "order_sn"):
        v = payload.get(clave)
        if v:
            return str(v)
    datos = payload.get("data") or payload.get("eventData") or {}
    if isinstance(datos, dict):
        for clave in ("parentOrderSn", "parent_order_sn", "orderSn", "order_sn"):
            v = datos.get(clave)
            if v:
                return str(v)
    return None


def _precio_catalogo(skus: list[str]) -> dict[str, float]:
    """Precio con el que ESTÁ publicado cada SKU en Temu. No es lo que Temu
    pagó — ver el encabezado —, es lo mejor que se puede saber hoy."""
    from services import supabase_db as sdb
    if not skus:
        return {}
    try:
        filas = sdb.fetch_all(
            """select sku::text sku, price from channel.listings
                where canal=%(c)s and sku::text = any(%(s)s)""",
            {"c": CANAL, "s": skus})
        return {f["sku"]: float(f["price"] or 0) for f in filas}
    except Exception as exc:  # noqa: BLE001
        log.warning("pedidos_temu._precio_catalogo: %s", exc)
        return {}


async def _traer(parent_sn: str) -> dict[str, Any] | None:
    from services import temu
    try:
        return await temu.llamar("bg.order.detail.v2.get",
                                 {"parentOrderSn": str(parent_sn)})
    except Exception as exc:  # noqa: BLE001
        log.warning("pedidos_temu: no se pudo traer %s: %s", parent_sn, exc)
        return None


def _normalizar(parent_sn: str, det: dict[str, Any]) -> dict[str, Any]:
    """Detalle de Temu → el dict que espera `pedidos_ml.construir_payload`."""
    padre = det.get("parentOrderMap") or {}
    renglones = det.get("orderList") or []

    crudas: list[dict[str, Any]] = []
    for o in renglones:
        cantidad = int(o.get("quantity") or o.get("originalOrderQuantity") or 1)
        # El SKU vive en productList[].extCode, no en el renglón.
        for p in (o.get("productList") or []):
            sku = str(p.get("extCode") or "").strip()
            if not sku:
                continue
            crudas.append({
                "item_id": str(o.get("goodsId") or ""),
                "sku": sku,
                "titulo": o.get("goodsName") or "",
                "variacion_id": None,
                "cantidad": cantidad,
                "precio_unitario": 0.0,   # se rellena abajo con el de catálogo
                "precio_lista": 0.0,
                # Temu no expone comisión por pedido (misma API bloqueada que el
                # importe). Cero antes que un número que nadie verificó.
                "comision_ml": 0.0,
            })

    precios = _precio_catalogo([c["sku"] for c in crudas])
    for c in crudas:
        c["precio_unitario"] = precios.get(c["sku"], 0.0)
        c["precio_lista"] = c["precio_unitario"]

    # Agrupado por (sku, precio), igual que TikTok.
    agrupadas: dict[tuple, dict] = {}
    for l in crudas:
        clave = (l["sku"], l["precio_unitario"])
        if clave in agrupadas:
            agrupadas[clave]["cantidad"] += l["cantidad"]
        else:
            agrupadas[clave] = l
    items = list(agrupadas.values())

    total = sum(i["precio_unitario"] * i["cantidad"] for i in items)
    creado = padre.get("parentOrderTime") or padre.get("parentConfirmTime")
    fecha = (datetime.fromtimestamp(int(creado), tz=timezone.utc).isoformat()
             if creado else None)
    estado_num = padre.get("parentOrderStatus")
    if estado_num is None and renglones:
        estado_num = renglones[0].get("orderStatus")

    return {
        "id": str(parent_sn),
        "cuenta": CUENTA,
        "estado": str(estado_num),
        "detalle": "temu",
        "etiquetas": [],
        "fecha": fecha,
        "total": total,
        "pagado": total,
        "moneda": "MXN",
        "envio_costo": 0.0,
        "items": items,
        "envio": {"logistica": "temu", "estado": str(estado_num or "")},
        # FALSO a propósito: la mercancía sale de NUESTRA bodega, así que el
        # pedido descuenta. Ponerlo en True lo protegería como si fuera FULL/FBA
        # y el inventario se quedaría alto tras cada venta.
        "es_full": False,
        "pago_estado": str(estado_num or ""),
        "pago_fecha": fecha,
        "comprador": {"id": None, "nick": "", "nombre": "Comprador",
                      "apellido": "Temu"},
        "_estado_num": estado_num,
    }


async def procesar(parent_sn: str) -> dict[str, Any]:
    """Trae la orden y la vuelve pedido de Woo. Nunca lanza."""
    from services import pedidos_ml

    if not getattr(settings, "pedidos_temu_enabled", False):
        return {"ok": False, "id": parent_sn, "accion": "observacion",
                "motivo": "PEDIDOS_TEMU_ENABLED apagado: se registra y no se crea"}

    det = await _traer(parent_sn)
    if not det:
        return {"ok": False, "id": parent_sn,
                "motivo": "no se pudo leer el detalle de la orden en Temu"}

    orden = _normalizar(parent_sn, det)
    if not any(i["sku"] for i in orden["items"]):
        return {"ok": False, "id": parent_sn,
                "motivo": "la orden no trae extCode (SKU) en ninguna línea"}

    estado_num = orden.pop("_estado_num", None)
    try:
        destino = _ESTADOS_WC.get(int(estado_num))
    except (TypeError, ValueError):
        destino = None
    if not destino:
        # Ver el encabezado: un código que no conocemos NO crea pedido. Queda el
        # registro para poder mapearlo cuando se sepa qué era.
        log.warning("TEMU orden %s con orderStatus=%s SIN MAPEAR: se registra y "
                    "no se crea pedido.", parent_sn, estado_num)
        return {"ok": False, "id": parent_sn, "accion": "sin_mapear",
                "estado_temu": estado_num,
                "motivo": f"orderStatus={estado_num} no está en el mapa verificado"}

    r = await pedidos_ml.sincronizar(parent_sn, forzar_estado=destino,
                                     orden=orden, proteger_stock=False)
    _ultimo.update(estado="ok" if r.get("ok") else "error",
                   ts=datetime.now(timezone.utc).isoformat(),
                   pedidos=_ultimo.get("pedidos", 0) + (1 if r.get("ok") else 0))
    if r.get("ok"):
        log.info("TEMU orden %s → pedido WC #%s (%s) · PRECIO DE CATÁLOGO, no el "
                 "cobrado: la API de importes está bloqueada (3000032)",
                 parent_sn, r.get("wc_order_id"), r.get("accion"))
    return r
