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

import asyncio
import time

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
# EL ENUM, DEDUCIDO DE LOS HECHOS (10-sep-2026). Temu no lo publica, y hasta hoy
# aquí vivía un solo código —el 4— que salió de DOS ventas de agosto. Medido
# contra 10 órdenes reales con el sondeo (`/api/automatizacion/temu/sondeo`,
# campo `estados.perfil_por_estado`), mirando lo único que no miente: si la
# orden trae `parentShippingTime`, ya se envió.
#
#   estado 2 · 6 órdenes · enviadas 0 de 6 · confirmadas 6 de 6  → PAGADA, POR ENVIAR
#   estado 4 · 1 orden   · enviadas 1 de 1                       → ENVIADA
#   estado 5 · 3 órdenes · enviadas 3 de 3                       → ENTREGADA
#
# Y las fechas confirman el ciclo: las de estado 2 son del 8-sep (recientes) y
# las de 4 y 5 del 19-ago. O sea `2 → 4 → 5`.
#
# ⚠️ POR QUÉ ESTO ERA EL TAPÓN. La venta NACE en 2, y 2 no estaba mapeado: cada
# orden nueva se descartaba con un warning y no llegaba a crear pedido. Para
# cuando alcanzaba el 4 —el único que conocíamos— ya se había enviado sola, así
# que la orden de venta llegaba tarde o no llegaba. Nueve de cada diez órdenes
# de la muestra caían fuera.
_ESTADOS_WC: dict[int, str] = {
    2: "processing",   # pagada y sin enviar: ES la que hay que surtir
    4: "processing",   # ya enviada; si no la vimos en 2, la venta sigue siendo real
    5: "completed",    # entregada: se registra, pero ya no hay nada que surtir
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


async def _traer_guia(parent_sn: str, order_sn: str | None) -> tuple[str, str]:
    """
    (guía, paquetería) de una orden de Temu. Cadenas vacías si no se pudo.

    HACE FALTA UNA SEGUNDA LLAMADA, y esto costó entenderlo. La guía **NO viene
    en el pedido**: se buscó en toda la respuesta de `bg.order.detail.v2.get` y
    del listado, en los dos vocabularios —`trackingNumber` en inglés y
    `mailNo`/`waybill` en el chino de paquetería— y no aparece.

    Los endpoints de envío sí existen, pero hay que leer los CÓDIGOS de error
    para verlo, porque un "falla" a secas los confunde con los inexistentes:

        bg.logistics.shipment.v2.get   120012016  "The parentOrder or Order is invalid"
        bg.order.shippinginfo.v2.get   180020003  "Invalid param"
        bg.shipping.order.get          3000003    "type not exists"  ← este sí no existe

    Los dos primeros decían **"me faltan los parámetros"**, no "no existo": se
    estaban llamando sin `parentOrderSn`/`orderSn`. Con ellos contestan.
    Verificado contra una orden real el 2026-09-01:

        shipmentInfoDTO[].trackingNumber = JMX600983301165
        shipmentInfoDTO[].carrierName    = J&T express

    FALLA SUAVE a propósito: sin guía la venta se registra igual y la columna
    queda vacía. Que Temu no conteste no puede costar un pedido — y la guía
    llega tarde de todos modos (Temu la asigna al generar la etiqueta).
    """
    from services import temu

    for tipo in ("bg.logistics.shipment.v2.get", "bg.order.shippinginfo.v2.get"):
        params = {k: v for k, v in (("parentOrderSn", str(parent_sn)),
                                    ("orderSn", order_sn)) if v}
        try:
            r = await temu.llamar(tipo, params)
        except Exception as exc:  # noqa: BLE001
            log.debug("pedidos_temu: %s no dio guía de %s: %s",
                      tipo, parent_sn, str(exc)[:120])
            continue
        envios = (r or {}).get("shipmentInfoDTO") or []
        if isinstance(envios, dict):
            envios = [envios]
        for e in envios:
            if not isinstance(e, dict):
                continue
            guia = str(e.get("trackingNumber") or "").strip()
            if guia:
                return guia, str(e.get("carrierName") or "").strip()
    return "", ""


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


async def refrescar_guias(dias: int = 14, limite: int = 60,
                          segundos_max: int = 900) -> dict[str, Any]:
    """
    Le pone número de rastreo a las entregas de Temu que aún no lo tienen.

    POR QUÉ HACE FALTA UN TRABAJO APARTE. La guía no existe cuando nace la
    orden: la asigna la paquetería cuando el paquete sale, o sea DESPUÉS de que
    el almacén surta. El único momento en que la volveríamos a mirar sería al
    llegar otro aviso de esa venta — y Temu no manda avisos.

    LA COLA SALE DE ODOO, no de la bitácora. La primera versión preguntaba
    `guia = ''` en `ops.odoo_sale_orders`, y eso estaba mal: esa columna la
    rellena el seam en cualquier re-aviso sin tocar Odoo, así que la venta salía
    de la cola y la entrega se quedaba sin rastreo para siempre. Preguntándole a
    Odoo, la cola se vacía cuando el trabajo está hecho y un fallo se reintenta
    solo a las dos horas.

    TECHO DE TIEMPO. `xmlrpc` no lleva timeout en este proyecto, así que una
    llamada colgada ocuparía un hilo del pool compartido y —con
    `max_instances=1`— mataría el trabajo en silencio para siempre. El corte por
    reloj lo convierte en "esta vuelta rindió menos", que se ve en el resumen.

    Nunca lanza.
    """
    from services import odoo_ventas, odoo_ventas_log, temu

    r: dict[str, Any] = {"pendientes": 0, "miradas": 0, "con_guia": 0,
                         "sin_guia_aun": 0, "escritas_en_odoo": 0,
                         "no_se_pudo_escribir": 0, "fallos_temu": 0,
                         "cortado_por_tiempo": False}
    if not temu.disponible():
        return {**r, "error": "Temu no está configurado (falta app_key/secret/token)"}

    try:
        cola = await asyncio.to_thread(odoo_ventas.pendientes_de_guia,
                                       "temu", dias, limite)
    except Exception as exc:  # noqa: BLE001
        log.warning("refrescar_guias: no se pudo armar la cola: %s", exc)
        return {**r, "error": str(exc)[:200]}
    r["pendientes"] = len(cola)

    limite_reloj = time.monotonic() + segundos_max
    for item in cola:
        if time.monotonic() > limite_reloj:
            r["cortado_por_tiempo"] = True
            break
        sn = item["order_id"]
        r["miradas"] += 1
        try:
            det = await _traer(sn)
            if not det:
                r["fallos_temu"] += 1
                continue
            renglones = det.get("orderList") or []
            order_sn = (renglones[0] or {}).get("orderSn") if renglones else None
            guia, paqueteria = await _traer_guia(sn, order_sn)
        except Exception as exc:  # noqa: BLE001 — una mala no detiene las demás
            r["fallos_temu"] += 1
            log.warning("refrescar_guias: %s falló contra Temu: %s", sn, str(exc)[:150])
            continue

        if not guia:
            # Lo NORMAL mientras no se envíe. No es un fallo: contarlo como tal
            # haría que un contador de errores gritara todos los días sin que
            # nada esté mal.
            r["sin_guia_aun"] += 1
            continue
        r["con_guia"] += 1

        # ODOO PRIMERO. Es lo que define la cola, así que si esto falla la venta
        # sigue siendo candidata a la vuelta siguiente. Al revés, marcar la
        # bitácora antes la sacaría de la cola aunque Odoo se hubiera quedado sin
        # el dato.
        res = await asyncio.to_thread(odoo_ventas.fijar_guia, "temu", sn, guia,
                                      item["pickings"])
        if res.get("ok"):
            r["escritas_en_odoo"] += 1
        else:
            r["no_se_pudo_escribir"] += 1
            log.warning("refrescar_guias: guía %s de %s NO llegó a Odoo (%s)",
                        guia, sn, res.get("accion"))
        try:
            await asyncio.to_thread(odoo_ventas_log.actualizar_guia,
                                    "temu", "TEMU", sn, guia, paqueteria)
        except Exception as exc:  # noqa: BLE001
            log.warning("refrescar_guias: bitácora de %s: %s", sn, str(exc)[:150])

    if r["pendientes"]:
        log.info("Refresco de guías de Temu: %s", r)
    return r


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

    # LA GUÍA, en su propia llamada. Ver `_traer_guia`: no viene en el pedido y
    # hace falta pedirla aparte con `parentOrderSn` + `orderSn`. Va DESPUÉS de
    # comprobar que hay SKU: si la venta no sirve, no vale la pena el viaje.
    orden["guia"], orden["paqueteria"] = await _traer_guia(
        parent_sn, ((det.get("orderList") or [{}])[0] or {}).get("orderSn"))
    if orden["guia"]:
        log.info("TEMU orden %s · guía %s (%s)", parent_sn,
                 orden["guia"], orden["paqueteria"] or "sin paquetería")

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
