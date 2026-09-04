# -*- coding: utf-8 -*-
"""
pedidos_walmart.py — las ventas de Walmart MX se vuelven pedidos de Woo.

Pieza 6. Hermano de `pedidos_temu.py` y `pedidos_amazon.py`.

⚠️ LO QUE ESTO DESTAPÓ, Y ES LO IMPORTANTE
──────────────────────────────────────────
Al construirlo se preguntó por primera vez `GET /v3/orders` y contestó **8
ventas reales entre el 14-ago y el 2-sep-2026**, todas `fulfillmentType: S2H`
(la surtimos NOSOTROS, no Walmart) y todas ya Shipped o Delivered. Ocho piezas
salieron de la bodega y ni Woo ni Odoo se enteraron por esta vía.

Por eso el sondeo nace en **SOLO REGISTRO**: descontar hoy piezas que se
enviaron hace tres semanas sería descontarlas DOS VECES si el almacén ya ajustó
a mano en Odoo — que es el mismo defecto que ya mordió en TikTok (memoria
`doble-descuento-tiktok-odoo`). Primero se ven las ventas; encender el descuento
es una decisión aparte, con la mano en el interruptor.

NO HAY WEBHOOK, Y NO ES POR NO BUSCARLO
───────────────────────────────────────
Walmart MX **sí publica** el catálogo de eventos (`GET /v3/webhooks/eventTypes`
contesta 200 y trae `PO_CREATED`, `ORDER_STATUS_UPDATE`, `ORDER_UPDATES`…), pero
`GET /v3/webhooks/subscriptions` devuelve **520 "Internal Error Occured"** con
cualquier combinación de parámetros — es un fallo del lado de ellos, no de las
credenciales (el mismo token lee pedidos, feeds y catálogo sin problema). Sin
poder listar ni crear suscripciones, el webhook no se puede registrar.

Así que esto es un SONDEO, como Amazon. Cuando la suscripción funcione, el
traductor (`_normalizar`) se reusa tal cual: solo cambia quién lo dispara.

LA FORMA DE MÉXICO NO ES LA DE LA DOCUMENTACIÓN
──────────────────────────────────────────────
Medido contra las 8 órdenes reales:
  · `orderLines` es una **lista plana**, no `{"orderLine": [...]}`.
  · el estado vive en `orderLine.orderLineStatus` (SINGULAR, directo en la
    línea), no en `orderLineStatuses.orderLineStatus`.
Calcar el ejemplo de la documentación de EE.UU. habría devuelto cero líneas sin
lanzar un solo error.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.pedidos_walmart")

CANAL = "walmart"
CUENTA = "WALMART"

# El mapa de estados. Un código que NO esté aquí se REGISTRA y no crea pedido:
# es la lección de Temu — inventar la traducción de un estado desconocido crea
# pedidos en un estado que nadie eligió.
_ESTADOS_WC: dict[str, str] = {
    "Created": "processing",
    "Acknowledged": "processing",
    "Shipped": "completed",
    "Delivered": "completed",
    "Cancelled": "cancelled",
}

_ultimo: dict[str, Any] = {"estado": "sin correr", "ts": None, "pedidos": 0,
                           "vistos": 0, "descartes": {}}


def estado() -> dict[str, Any]:
    return dict(_ultimo)


def _num(v: Any, x: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return x


def _estado_linea(linea: dict[str, Any]) -> str | None:
    """El estado de una línea. Ojo con la forma de México (ver encabezado)."""
    st = linea.get("orderLineStatus")
    if isinstance(st, dict):                      # forma de la documentación
        st = st.get("orderLineStatus")
    if isinstance(st, list) and st:
        return str((st[0] or {}).get("status") or "") or None
    if isinstance(st, str):
        return st or None
    return None


def _nombre(o: dict[str, Any]) -> tuple[str, str]:
    """(nombre, apellido) del comprador. `pedidos_ml` los CIFRA al escribirlos."""
    dir_ = ((o.get("shippingInfo") or {}).get("postalAddress") or {})
    completo = str(dir_.get("name") or "").strip()
    if not completo:
        return "Comprador", "Walmart"
    partes = completo.split()
    return (partes[0], " ".join(partes[1:]) or "Walmart")


def _guia(o: dict[str, Any]) -> tuple[str, str]:
    """(número de guía, paquetería) del primer envío que la traiga."""
    for env in (o.get("shipments") or []):
        if env.get("trackingNumber"):
            return str(env["trackingNumber"]), str(env.get("carrier") or "")
    return "", ""


def _normalizar(o: dict[str, Any]) -> dict[str, Any]:
    """Orden de Walmart → el dict que espera `pedidos_ml.construir_payload`."""
    po = str(o.get("purchaseOrderId") or "")
    lineas = o.get("orderLines")
    if isinstance(lineas, dict):                  # forma de la documentación
        lineas = lineas.get("orderLine") or []
    lineas = lineas or []

    items: list[dict[str, Any]] = []
    estados: list[str] = []
    wfs = False
    for l in lineas:
        it = l.get("item") or {}
        sku = str(it.get("sku") or "").strip()
        if not sku:
            continue
        est = _estado_linea(l)
        if est:
            estados.append(est)
        if str(l.get("isWFSEnabled") or "").upper() == "Y":
            wfs = True
        items.append({
            "item_id": str(it.get("offerId") or ""),
            "sku": sku,
            "titulo": it.get("productName") or "",
            "variacion_id": None,
            "cantidad": int(_num((l.get("orderLineQuantity") or {}).get("amount"), 1)),
            # El precio CON impuesto: es lo que pagó el cliente y lo que hace
            # cuadrar el pedido contra el total de Walmart.
            "precio_unitario": _num((it.get("unitPrice") or {}).get("amount")),
            "precio_lista": _num((it.get("unitPrice") or {}).get("amount")),
            # Walmart NO manda la comisión en el pedido (misma situación que
            # Amazon sin Finances API). Cero antes que un número inventado.
            "comision_ml": 0.0,
        })

    total = _num((o.get("orderTotal") or {}).get("amount")) or sum(
        i["precio_unitario"] * i["cantidad"] for i in items)
    fecha = o.get("orderDate")
    nombre, apellido = _nombre(o)
    guia, paqueteria = _guia(o)

    return {
        "id": po,
        "cuenta": CUENTA,
        "estado": (estados[0] if estados else ""),
        "detalle": "walmart",
        "etiquetas": [],
        "fecha": fecha,
        "total": total,
        "pagado": total,
        "moneda": ((o.get("orderTotal") or {}).get("currency") or "MXN"),
        "envio_costo": 0.0,
        "items": items,
        "envio": {"logistica": "wfs" if wfs else "s2h",
                  "estado": (estados[0] if estados else "")},
        # ⚠️ WFS ES EL "FULL" DE WALMART: la mercancía ya está en su almacén, así
        # que esas ventas NO deben tocar nuestra bodega. S2H la surtimos
        # nosotros y SÍ descuenta — igual que ML no-FULL y Amazon MFN.
        # Hoy las 8 órdenes reales son S2H; el día que se convierta un SKU a WFS
        # (ya hay feeds OMNI_WFSCONVERT corriendo) esta línea es la que evita
        # que se descuente de más.
        "es_full": wfs,
        "pago_estado": (estados[0] if estados else ""),
        "pago_fecha": fecha,
        "comprador": {"id": None, "nick": "", "nombre": nombre,
                      "apellido": apellido},
        "guia": guia,
        "paqueteria": paqueteria,
        "_estados": estados,
        "_wfs": wfs,
    }


async def procesar(o: dict[str, Any], *, solo_registro: bool | None = None,
                   proteger_stock: bool = False) -> dict[str, Any]:
    """
    Una orden ya traída → pedido de Woo. Nunca lanza.

    `proteger_stock=True` registra la venta SIN mover bodega. Es lo que hace
    falta para el PRIMER llenado: las 8 ventas que Walmart llevaba acumuladas
    ya salieron físicamente, y si el almacén las ajustó a mano en Odoo,
    crearlas ahora con descuento las restaría dos veces. El candado de
    `DIAS_VENTA_VIEJA` (5 días) cubre a las viejas solo; esto cubre a TODAS.
    """
    from services import pedidos_ml

    orden = _normalizar(o)
    po = orden["id"]
    if not orden["items"]:
        return {"ok": False, "id": po, "accion": "sin_sku",
                "motivo": "la orden no trae SKU en ninguna línea"}

    estados = orden.pop("_estados", [])
    wfs = orden.pop("_wfs", False)
    crudo = estados[0] if estados else None
    destino = _ESTADOS_WC.get(str(crudo or ""))
    if not destino:
        log.warning("WALMART orden %s con estado %r SIN MAPEAR: se registra y no "
                    "se crea pedido.", po, crudo)
        return {"ok": False, "id": po, "accion": "sin_mapear",
                "estado_walmart": crudo,
                "motivo": f"estado '{crudo}' no está en el mapa verificado"}

    if solo_registro is None:
        solo_registro = getattr(settings, "pedidos_walmart_solo_registro", True)
    if solo_registro:
        log.info("WALMART orden %s · %s · %s · $%s — SOLO REGISTRO, no se crea "
                 "pedido (PEDIDOS_WALMART_SOLO_REGISTRO)",
                 po, [i["sku"] for i in orden["items"]], destino, orden["total"])
        return {"ok": True, "id": po, "accion": "solo_registro",
                "estado_wc": destino, "wfs": wfs,
                "skus": [i["sku"] for i in orden["items"]],
                "total": orden["total"]}

    # `proteger_stock` NO es lo contrario de `es_full`: es la protección extra
    # que `pedidos_ml` aplica al CANCELAR. Se deja como en los demás canales.
    r = await pedidos_ml.sincronizar(po, forzar_estado=destino, orden=orden,
                                     proteger_stock=proteger_stock)
    if r.get("ok"):
        log.info("WALMART orden %s → pedido WC #%s (%s) · %s · guía %s",
                 po, r.get("wc_order_id"), r.get("accion"),
                 "WFS (no descuenta)" if wfs
                 else ("S2H protegido (no descuenta)" if proteger_stock
                       else "S2H (descuenta)"),
                 orden.get("guia") or "sin guía")
    return r


async def sondear(dias: int | None = None, *, solo_registro: bool | None = None,
                  proteger_stock: bool = False) -> dict[str, Any]:
    """Trae las ventas recientes de Walmart y las procesa. Nunca lanza."""
    from services import walmart

    if not walmart.disponible():
        # ⚠️ ESTO PASABA EN SILENCIO ABSOLUTO. Sin WM_CLIENT_ID/WM_CLIENT_SECRET
        # el sondeo se rendía sin log y sin tocar `_ultimo`, así que el estado
        # seguía diciendo "sin correr" mientras el arranque anunciaba ENCENDIDO.
        # Medido el 4-sep: las credenciales NO estaban en Railway y el canal
        # entero era inerte en producción sin una sola señal.
        motivo = ("Walmart no está configurado: faltan WM_CLIENT_ID / "
                  "WM_CLIENT_SECRET en el ambiente.")
        _ultimo.update(estado="sin_credenciales",
                       ts=datetime.now(timezone.utc).isoformat())
        log.warning("WALMART sondeo NO corre — %s", motivo)
        return {"ok": False, "motivo": motivo}

    dias = dias or int(getattr(settings, "pedidos_walmart_max_dias", 7) or 7)
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%d")
    try:
        ordenes = await walmart.listar_pedidos(desde)
    except Exception as exc:  # noqa: BLE001
        _ultimo.update(estado="error", ts=datetime.now(timezone.utc).isoformat())
        log.warning("WALMART sondeo: no se pudieron leer los pedidos: %s", exc)
        return {"ok": False, "motivo": str(exc)}

    # ⚠️ SEGUNDA RED CONTRA DUPLICADOS. `listar_pedidos` ya deduplica, pero el
    # 4-sep su paginado devolvió el mismo lote dos veces y este bucle creó 8
    # pedidos de Woo de más. Aquí un duplicado no es un dato repetido: es una
    # venta contada dos veces en el tab de Ventas y, sin la protección de stock,
    # una pieza descontada dos veces. Dos candados para el mismo fallo es barato.
    unicas: list[dict[str, Any]] = []
    ya: set[str] = set()
    for o in ordenes:
        po = str(o.get("purchaseOrderId") or "")
        if po and po in ya:
            log.warning("WALMART sondeo: la orden %s vino repetida en el listado; "
                        "se ignora la copia.", po)
            continue
        ya.add(po)
        unicas.append(o)
    ordenes = unicas

    hechos, fallos = [], []
    for o in ordenes:
        try:
            r = await procesar(o, solo_registro=solo_registro,
                               proteger_stock=proteger_stock)
        except Exception as exc:  # noqa: BLE001
            r = {"ok": False, "id": str(o.get("purchaseOrderId") or ""),
                 "motivo": str(exc)}
        (hechos if r.get("ok") else fallos).append(r)

    # Los descartes se CUENTAN por motivo. `sin_mapear` y `sin_sku` devuelven
    # ok=False y hasta ahora solo dejaban un log: una venta que Walmart cobró y
    # que la tubería tiró se veía igual que una tarde sin ventas. Con el conteo
    # en `estado()`, el panel puede enseñar que algo se está cayendo.
    descartes: dict[str, int] = {}
    for f in fallos:
        k = str(f.get("accion") or "error")
        descartes[k] = descartes.get(k, 0) + 1
    _ultimo.update(estado="ok", ts=datetime.now(timezone.utc).isoformat(),
                   vistos=len(ordenes),
                   pedidos=_ultimo.get("pedidos", 0) + len(hechos),
                   descartes=descartes)
    log.info("WALMART sondeo · %d orden(es) desde %s · %d procesadas · %d con aviso%s",
             len(ordenes), desde, len(hechos), len(fallos),
             (" · descartes: " + ", ".join(f"{k}={v}" for k, v in descartes.items()))
             if descartes else "")
    return {"ok": True, "desde": desde, "vistos": len(ordenes),
            "procesados": hechos, "avisos": fallos}
