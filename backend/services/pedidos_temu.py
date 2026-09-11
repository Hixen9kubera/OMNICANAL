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

import json

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


def _lista_de_dicts(r: Any) -> list[dict[str, Any]]:
    """La primera lista de diccionarios de una respuesta de Temu, se llame como
    se llame la llave: cambia entre versiones de un mismo endpoint."""
    if isinstance(r, list):
        return [x for x in r if isinstance(x, dict)]
    if isinstance(r, dict):
        for v in r.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


async def _traer_guia_detalle(parent_sn: str, order_sn: str | None) -> dict[str, Any]:
    """
    La guía de una orden de Temu, DICIENDO de qué fuente salió y qué contestó
    cada una. `_traer_guia` es la envoltura de siempre para quien sólo quiere
    (guía, paquetería).

    TRES FUENTES, porque la guía vive en sitios distintos según el momento:

      bg.logistics.shipment.v2.get     envío YA CONFIRMADO (o auto-envío). Son
      bg.order.shippinginfo.v2.get     las que funcionaron con las órdenes de
                                       agosto, que ya habían completado el ciclo.

      bg.order.unshipped.package.get   etiqueta COMPRADA pero envío todavía NO
                                       confirmado. Es el estado en que queda una
                                       orden cuando alguien aprieta "Comprar
                                       envío" en el seller center y aún no
                                       aprieta "Confirmar envío": el paquete ya
                                       tiene número de rastreo, la orden sigue en
                                       "No enviado", y las dos primeras fuentes
                                       contestan vacío. Temu lo confirma solo a
                                       las 48 h: sin esta fuente, la guía llegaba
                                       al panel dos días tarde.

    Se descubrió el 10-sep: el refresco corrió cuatro veces sobre las siete
    órdenes del día y siempre dijo `sin_guia_aun`, mientras en Temu ya se
    estaban comprando las etiquetas.

    ⚠️ UN PAQUETE SÓLO SE ACEPTA SI MENCIONA ESTA VENTA. Se pide filtrado por
    `parentOrderSnList`, pero si Temu ignorara el filtro devolvería paquetes de
    OTRAS órdenes, y pegarle a una venta la guía de otra es peor que no ponerle
    ninguna: el paquete iría a la persona equivocada. Un envío combinado —dos
    ventas en una caja— menciona a las dos, y las dos reciben la misma guía, que
    es lo correcto.

    YA NO FALLA MUDA: el `log.debug` de antes hacía idéntico "Temu todavía no la
    asigna" y "la llamada está rota". Ahora cada error queda en `errores`.
    """
    from services import temu

    errores: dict[str, str] = {}
    for tipo in ("bg.logistics.shipment.v2.get", "bg.order.shippinginfo.v2.get"):
        params = {k: v for k, v in (("parentOrderSn", str(parent_sn)),
                                    ("orderSn", order_sn)) if v}
        try:
            r = await temu.llamar(tipo, params)
        except Exception as exc:  # noqa: BLE001
            errores[tipo] = str(exc)[:160]
            continue
        envios = (r or {}).get("shipmentInfoDTO") or []
        if isinstance(envios, dict):
            envios = [envios]
        for e in envios:
            if not isinstance(e, dict):
                continue
            guia = str(e.get("trackingNumber") or "").strip()
            if guia:
                return {"guia": guia, "paqueteria": str(e.get("carrierName") or "").strip(),
                        "fuente": tipo, "errores": errores,
                        "package_sn": e.get("packageSn")}

    tipo = "bg.order.unshipped.package.get"
    try:
        r = await temu.llamar(tipo, {"parentOrderSnList": [str(parent_sn)],
                                     "pageNumber": 1, "pageSize": 20})
        for paq in _lista_de_dicts(r):
            if str(parent_sn) not in json.dumps(paq, ensure_ascii=False, default=str):
                continue
            guia = str(paq.get("trackingNumber") or "").strip()
            if guia:
                return {"guia": guia, "paqueteria": str(paq.get("carrierName") or "").strip(),
                        "fuente": tipo, "errores": errores,
                        "package_sn": paq.get("packageSn")}
    except Exception as exc:  # noqa: BLE001
        errores[tipo] = str(exc)[:160]

    return {"guia": "", "paqueteria": "", "fuente": None, "errores": errores}


async def _traer_guia(parent_sn: str, order_sn: str | None) -> tuple[str, str]:
    """(guía, paquetería) de una orden de Temu. Cadenas vacías si no hay.

    Envoltura de `_traer_guia_detalle`, que es donde vive la explicación de las
    tres fuentes. Falla suave: sin guía la venta se registra igual.
    """
    d = await _traer_guia_detalle(parent_sn, order_sn)
    return d["guia"], d["paqueteria"]


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
    Completa la guía de las ventas de Temu que ya tienen orden en Odoo.

    Por cada venta hace, en este orden, lo que pidió Brandon (11-sep):
      1. el número de rastreo en la ENTREGA de salida, verificado al re-leer;
      2. el PDF de la etiqueta en la ORDEN ("Subir guía"), verificado al re-leer;
      3. la guía en la bitácora, que es lo que muestra el panel.

    POR QUÉ HACE FALTA UN TRABAJO APARTE. La guía no existe cuando nace la
    orden: la asigna la paquetería cuando se compra el envío. El único momento
    en que la volveríamos a mirar sería al llegar otro aviso de esa venta — y
    Temu no manda avisos.

    LA COLA SALE DE ODOO, no de la bitácora: una venta sigue pendiente mientras
    le falte la guía en la entrega O el PDF en la orden (ver
    `odoo_ventas.pendientes_de_guia`). Odoo va primero; si algo falla, la venta
    sigue en la cola y la vuelta siguiente lo reintenta.

    ENVÍO COMBINADO: dos ventas en una caja comparten paquete y etiqueta. El PDF
    se baja una vez por vuelta y se sube a las dos.

    TECHO DE TIEMPO. `xmlrpc` no lleva timeout en este proyecto, así que una
    llamada colgada ocuparía un hilo del pool compartido y —con
    `max_instances=1`— mataría el trabajo en silencio para siempre. El corte por
    reloj lo convierte en "esta vuelta rindió menos", que se ve en el resumen.

    Nunca lanza.
    """
    from services import odoo_ventas, odoo_ventas_log, temu

    r: dict[str, Any] = {"pendientes": 0, "miradas": 0, "con_guia": 0,
                         "sin_guia_aun": 0, "guias_escritas": 0,
                         "guias_verificadas": 0, "guias_no_escritas": 0,
                         "pdf_subidos": 0, "pdf_verificados": 0, "pdf_fallos": 0,
                         "pdf_sin_paquete": 0, "fallos_temu": 0,
                         "cortado_por_tiempo": False, "document_type": None,
                         # De qué endpoint salió cada guía, y el primer error
                         # visto de cada uno. Sin esto, cuatro vueltas seguidas
                         # de "sin_guia_aun: 7" no decían si Temu no la tenía o
                         # si la estábamos buscando en el sitio equivocado.
                         "fuentes": {}, "errores_fuentes": {}, "errores_pdf": {}}
    if not temu.disponible():
        return {**r, "error": "Temu no está configurado (falta app_key/secret/token)"}

    try:
        cola = await asyncio.to_thread(odoo_ventas.pendientes_de_guia,
                                       "temu", dias, limite)
    except Exception as exc:  # noqa: BLE001
        log.warning("refrescar_guias: no se pudo armar la cola: %s", exc)
        return {**r, "error": str(exc)[:200]}
    r["pendientes"] = len(cola)

    pdf_por_paquete: dict[str, dict[str, Any]] = {}
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
            d = await _traer_guia_detalle(sn, order_sn)
            for k, v in (d.get("errores") or {}).items():
                r["errores_fuentes"].setdefault(k, v)
            if d.get("fuente"):
                r["fuentes"][d["fuente"]] = r["fuentes"].get(d["fuente"], 0) + 1
        except Exception as exc:  # noqa: BLE001 — una mala no detiene las demás
            r["fallos_temu"] += 1
            log.warning("refrescar_guias: %s falló contra Temu: %s", sn, str(exc)[:150])
            continue

        guia, paqueteria, paquete = d["guia"], d["paqueteria"], d.get("package_sn")
        if not guia:
            # Lo NORMAL mientras nadie compre el envío. No es un fallo: contarlo
            # como tal haría que un contador de errores gritara todos los días
            # sin que nada esté mal.
            r["sin_guia_aun"] += 1
            continue
        r["con_guia"] += 1

        # 1 · EL NÚMERO EN LA ENTREGA — sólo si le falta.
        if item.get("pickings"):
            res = await asyncio.to_thread(odoo_ventas.fijar_guia, "temu", sn, guia,
                                          item["pickings"])
            if res.get("accion") == "ya_tenia":
                pass      # alguien la puso entre que se armó la cola y ahora
            elif res.get("ok"):
                r["guias_escritas"] += 1
                r["guias_verificadas"] += 1 if res.get("verificada") else 0
            else:
                r["guias_no_escritas"] += 1
                log.warning("refrescar_guias: guía %s de %s NO llegó a Odoo (%s)",
                            guia, sn, res.get("accion"))

        # 2 · EL PDF EN LA ORDEN ("Subir guía") — sólo si le falta.
        if item.get("sin_pdf"):
            if not paquete:
                r["pdf_sin_paquete"] += 1
            else:
                if paquete not in pdf_por_paquete:
                    pdf_por_paquete[paquete] = await temu.descargar_etiqueta(paquete)
                et = pdf_por_paquete[paquete]
                for k, v in (et.get("errores") or {}).items():
                    r["errores_pdf"].setdefault(k, v)
                if not et.get("ok"):
                    r["pdf_fallos"] += 1
                    log.warning("refrescar_guias: la etiqueta de %s (paquete %s) no se "
                                "pudo bajar: %s", sn, paquete, et.get("errores"))
                else:
                    r["document_type"] = et["document_type"]
                    res = await asyncio.to_thread(odoo_ventas.fijar_etiqueta, "temu", sn,
                                                  item["sin_pdf"], et["pdf"], f"{guia}.pdf")
                    if res.get("accion") == "ya_tenia":
                        pass
                    elif res.get("ok"):
                        r["pdf_subidos"] += 1
                        r["pdf_verificados"] += 1 if res.get("verificada") else 0
                    else:
                        r["pdf_fallos"] += 1
                        log.warning("refrescar_guias: el PDF de %s NO quedó en Odoo (%s)",
                                    sn, res.get("accion"))

        # 3 · EL PANEL — la bitácora que pinta la pestaña Automatización.
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
