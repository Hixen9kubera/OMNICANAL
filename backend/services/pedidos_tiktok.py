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

import asyncio
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
    # `isinstance` en los dos niveles: la URL es pública y un cuerpo que sea
    # lista, cadena o `data` no-objeto hacía tronar `.get` dentro del receptor.
    if not isinstance(payload, dict):
        return None
    d = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    for k in ("order_id", "orderId", "order_no", "id"):
        v = d.get(k)
        if v and isinstance(v, (str, int)):
            return str(v)
    return None


def estado_de_evento(payload: dict[str, Any]) -> str:
    """El `order_status` que anuncia el aviso, en mayúsculas ('' si no trae).

    Sólo sirve para DISPARAR cosas (la etiqueta al llegar AWAITING_COLLECTION):
    el estado con el que se escribe algo se vuelve a pedir a la API, como todo
    lo demás del evento.
    """
    if not isinstance(payload, dict):
        return ""
    d = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    v = d.get("order_status") or d.get("status") or ""
    return str(v).strip().upper() if isinstance(v, (str, int)) else ""


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
            # La imagen que VIO EL COMPRADOR, tal cual la sirve TikTok
            # (confirmado contra órdenes reales el 28-ago-2026: `sku_image` viene
            # en cada línea). Se guarda en la bitácora de órdenes de Odoo para
            # que quien revisa el tab reconozca el producto de un vistazo, sin
            # tener que ir a buscarlo por SKU.
            "imagen": it.get("sku_image") or "",
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
        # LA GUÍA, y ya viene desde la venta (verificado el 28-ago-2026 contra
        # dos órdenes reales: `tracking_number` está en el encabezado Y en cada
        # línea desde `AWAITING_COLLECTION`, con `shipping_provider` = "Estafeta
        # MX"). No hace falta esperar a un evento posterior de empaque.
        # `fulfillment_type` = FULFILLMENT_BY_SELLER confirma de paso lo que ya
        # asumía este módulo: la mercancía sale de NUESTRA bodega.
        "guia": o.get("tracking_number") or "",
        "paqueteria": o.get("shipping_provider") or "",
        "pago_estado": o.get("status"),
        "pago_fecha": fecha,
        "comprador": {"id": o.get("user_id"), "nick": "",
                      "nombre": recipiente.get("name") or "Comprador",
                      "apellido": "TikTok"},
    }


async def _traer(order_id: str) -> dict[str, Any] | None:
    """La orden COMPLETA desde TikTok. None si no se pudo."""
    from services import tiktok as tk
    # EN UN HILO (regla 11): las dos son lecturas de BD SÍNCRONAS (kubera o
    # MySQL). Aquí pasan el webhook y la recuperación, que la repite hasta 25
    # veces seguidas: en la corrutina, cada una paraba el backend ENTERO
    # mientras la base contestaba.
    token, cipher = await asyncio.to_thread(lambda: (tk.access_token(), tk.cipher()))
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


async def procesar(order_id: str, reintentable: bool = False) -> dict[str, Any]:
    """
    Una orden de TikTok → pedido de WooCommerce. Idempotente por id de orden.

    `reintentable=True` lo declaran quienes VUELVEN solos (el sondeo y el
    reprocesador de avisos): sin candado confirmable en kubera se saltan la
    pasada en vez de arriesgar un duplicado. El webhook no vuelve, y por eso
    conserva el default (ver `pedidos_ml.sincronizar`).

    Nunca lanza: la llama un webhook que debe responder 200 pase lo que pase.
    """
    if not settings.pedidos_tiktok_enabled:
        return {"ok": False, "motivo": "PEDIDOS_TIKTOK_ENABLED apagado", "id": order_id}
    if not str(order_id or "").strip().isdigit():
        # Los ids de orden de TikTok son NUMÉRICOS (el mismo criterio de
        # `tiktok_diagnostico.recuperar`). La URL del aviso es pública y la firma
        # sigue observando: un `order_id` como "abc1" no es una orden que TikTok
        # vaya a devolver nunca. TERMINAL, sin preguntar: antes contestaba "no se
        # pudo leer de TikTok", el marcado le programaba reintento y 25 de esas
        # filas tapaban el lote del reprocesador durante 48 h.
        return {"ok": False, "terminal": True, "id": str(order_id)[:40],
                "motivo": "el id de orden no es numérico: no es una orden de TikTok"}
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
                                         orden=orden, proteger_stock=False,
                                         reintentable=bool(reintentable))
        _ultimo.update(estado="ok", ts=datetime.now(timezone.utc).isoformat(),
                       pedidos=_ultimo.get("pedidos", 0) + (1 if r.get("ok") else 0))
        log.info("pedido TikTok %s → %s (%s)", order_id, r.get("accion"), destino)
        return r
    except Exception as exc:  # noqa: BLE001
        log.exception("pedidos_tiktok.procesar(%s) falló", order_id)
        return {"ok": False, "id": order_id, "motivo": str(exc)[:300]}


# ═════════════════════════════════════════════════════════════════════════════
#  GUÍA Y ETIQUETA PDF DE TIKTOK → ODOO  (14-sep-2026)
# ═════════════════════════════════════════════════════════════════════════════
#
# El molde es `pedidos_temu.refrescar_guias` y por las mismas razones: la cola
# SALE DE ODOO (`odoo_ventas.pendientes_de_guia`: le falta el número en la
# entrega de salida o el PDF en la orden confirmada), Odoo va primero, y lo que
# falle se queda en la cola para la vuelta siguiente.
#
# LO QUE CAMBIA RESPECTO A TEMU:
#   · La guía SÍ viene en la orden (`tracking_number`, medido el 28-ago) y el
#     PDF sale por PAQUETE (`packages[].id`), no por orden.
#   · El detalle se pide EN LOTES de hasta 50 ids por llamada (máximo de
#     `/order/202309/orders`), no uno por uno.
#   · Sólo `shipping_type=TIKTOK` tiene etiqueta de TikTok: en `SELLER` la guía
#     la pone quien envía, y pedirla sólo gasta cuota.
#   · La ventana útil es CORTA: el PDF existe desde que se agenda la recolección
#     (AWAITING_COLLECTION) y deja de servir al recolectarse. Por eso el job
#     corre cada 20 min y hay un disparo inmediato opcional desde el aviso.
#   · ON_HOLD no da etiqueta: el comprador todavía puede cancelar. Se espera.
#   · IN_TRANSIT/DELIVERED/COMPLETED tampoco: el paquete ya se recogió y TikTok
#     contesta 21042102. Sólo se les escribe el número si la entrega lo espera.
#   · El límite de la vuelta se aplica DESPUÉS del estado en TikTok, con
#     AWAITING_COLLECTION primero (ver `COLA_MAX`).
#   · El PDF se nombra `<order_id>.pdf` (Brandon), la misma convención con la
#     que Gabriela subía las capturas a mano — y con la que el diagnóstico las
#     reconoce.
#
# SIN DATOS DEL COMPRADOR: de la orden cruda sólo sale la lista blanca de
# `tiktok_diagnostico._orden_sin_pii` más ids de paquete, guía y paquetería
# (`_guia_sin_pii`). El PDF, que SÍ trae la dirección, vive en memoria y va
# directo a Odoo; el resumen sólo lleva conteos.

LOTE_DETALLE = 50
_RUTA_DETALLE = "/order/202309/orders"
# LA COLA SE PIDE COMPLETA (el tope de `pendientes_de_guia`) y `limite` se aplica
# DESPUÉS de mirar el estado en TikTok. Antes el límite se cortaba en Odoo, que
# ordena de la venta más vieja a la más nueva: 50 ventas ya recolectadas (sin PDF
# posible por 14 días) ocupaban todos los lugares en cada vuelta y la venta
# nueva en AWAITING_COLLECTION —la de la ventana corta— nunca llegaba al job.
COLA_MAX = 400
# Sin etiqueta todavía (o nunca): se registra el motivo y se sigue.
_ESTADOS_SIN_ETIQUETA = frozenset({"UNPAID", "ON_HOLD", "CANCELLED"})
# YA RECOLECTADAS: TikTok no imprime la etiqueta de un paquete que ya recogieron
# (doc de shipping_documents, code 21042102 "Documents couldn't be printed after
# the package has been pickup"). NO se pide el PDF; el NÚMERO sí se escribe si a
# la entrega le falta, porque viene en el detalle de la orden.
_ESTADOS_RECOLECTADOS = frozenset({"IN_TRANSIT", "DELIVERED", "COMPLETED"})
# Lo que tiene la ventana corta va primero; después lo que sólo escribe el número
# (no gasta llamada a TikTok); al final lo demás (AWAITING_SHIPMENT…).
_PRIORIDAD_URGENTE, _PRIORIDAD_SOLO_GUIA, _PRIORIDAD_RESTO = 0, 1, 2

# MEMORIA de las ventas que no van a dar etiqueta (cancelada, envío del vendedor,
# ya recolectada, código terminal): la vuelta siguiente no les vuelve a pedir
# detalle ni etiqueta mientras no tengan número pendiente en la entrega. En
# memoria y con caducidad a propósito: un reinicio o 6 h después se vuelven a
# mirar una vez, por si alguna se clasificó mal. El disparo inmediato
# (`solo_ids`) la ignora: si el aviso dice AWAITING_COLLECTION, se pregunta.
_MEMO_TTL_S = 6 * 3600
_MEMO_MAX = 5000
_sin_etiqueta: dict[str, tuple[float, str]] = {}

# Una vuelta a la vez: el job de cada 20 min y el disparo inmediato del aviso
# comparten cola y escriben en las mismas órdenes de Odoo.
_candado_guias = asyncio.Lock()
_ultimo_guias: dict[str, Any] = {"estado": "sin_ejecutar"}


def estado_guias() -> dict[str, Any]:
    """La última vuelta COMPLETA del refresco de guías (conteos, sin PII)."""
    return dict(_ultimo_guias)


def _recordar(oid: str, motivo: str) -> None:
    import time as _time
    _sin_etiqueta[str(oid)] = (_time.monotonic(), motivo)


def _recordado(oid: str) -> str | None:
    import time as _time
    v = _sin_etiqueta.get(str(oid))
    if not v:
        return None
    if _time.monotonic() - v[0] > _MEMO_TTL_S:
        _sin_etiqueta.pop(str(oid), None)
        return None
    return v[1]


def _podar_memoria() -> None:
    import time as _time
    ahora = _time.monotonic()
    for k in [k for k, (t, _m) in _sin_etiqueta.items() if ahora - t > _MEMO_TTL_S]:
        _sin_etiqueta.pop(k, None)
    if len(_sin_etiqueta) > _MEMO_MAX:
        for k, _v in sorted(_sin_etiqueta.items(), key=lambda kv: kv[1][0])[
                :len(_sin_etiqueta) - _MEMO_MAX]:
            _sin_etiqueta.pop(k, None)


def _guia_sin_pii(o: dict[str, Any]) -> dict[str, Any]:
    """La lista blanca del diagnóstico + lo que hace falta para la etiqueta.

    Se construye campo por campo (nunca copiando la orden y borrando): un campo
    nuevo de TikTok con datos del comprador no puede colarse por aquí.
    """
    from services.tiktok_diagnostico import _orden_sin_pii  # noqa: PLC0415

    base = _orden_sin_pii(o)
    lineas = [l for l in (o.get("line_items") if isinstance(o.get("line_items"), list)
                          else []) if isinstance(l, dict)]
    paquetes: list[str] = []
    for p in (o.get("packages") if isinstance(o.get("packages"), list) else []):
        pid = str(p.get("id") or "").strip() if isinstance(p, dict) else ""
        if pid and pid not in paquetes:
            paquetes.append(pid)
    if not paquetes:
        # Algunas órdenes sólo traen el paquete en la línea.
        for l in lineas:
            pid = str(l.get("package_id") or "").strip()
            if pid and pid not in paquetes:
                paquetes.append(pid)
    guia = str(o.get("tracking_number") or "").strip() or next(
        (str(l.get("tracking_number")).strip() for l in lineas
         if str(l.get("tracking_number") or "").strip()), "")
    paqueteria = str(o.get("shipping_provider") or "").strip() or next(
        (str(l.get("shipping_provider_name")).strip() for l in lineas
         if str(l.get("shipping_provider_name") or "").strip()), "")
    return {"id": base["id"], "status": base["status"],
            "shipping_type": (base["shipping_type"] or "").upper(),
            "paquetes": paquetes, "guia": guia, "paqueteria": paqueteria}


async def _detalles_en_lotes(ids: list[str], token: str, ciph: str,
                             r: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """El detalle de TODOS los ids en llamadas de hasta 50. Un lote que falla no
    tumba a los demás: sus ids quedan sin detalle y la vuelta siguiente los
    vuelve a pedir."""
    from services import tiktok as tk

    detalles: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ids), LOTE_DETALLE):
        lote = ids[i:i + LOTE_DETALLE]
        r["lotes"] += 1
        try:
            data = await tk.llamar(_RUTA_DETALLE, token,
                                   {"shop_cipher": ciph, "ids": ",".join(lote)})
        except Exception as exc:  # noqa: BLE001
            r["fallos_tiktok"] += len(lote)
            r["errores"].append(f"detalle lote {r['lotes']}: {str(exc)[:160]}")
            continue
        pedidos = set(lote)
        for o in (data or {}).get("orders") or []:
            if isinstance(o, dict):
                f = _guia_sin_pii(o)
                if f["id"] in pedidos:
                    detalles[f["id"]] = f
    return detalles


def _contar(r: dict[str, Any], clave: str, subclave: str) -> None:
    r[clave][subclave] = r[clave].get(subclave, 0) + 1


async def refrescar_guias(dias: int = 14, limite: int = 50, segundos_max: int = 900,
                          solo_ids: list[str] | None = None) -> dict[str, Any]:
    """
    Completa guía (número en la entrega) y etiqueta (PDF en la orden) de las
    ventas de TikTok que ya tienen orden en Odoo. Nunca lanza.

    `solo_ids` es el disparo inmediato del aviso AWAITING_COLLECTION: mira sólo
    esas ventas, y si ya hay una vuelta en curso NO espera (la del job las
    alcanza en ≤20 min). Sin `solo_ids` espera su turno.

    `limite` cuenta ventas CON TRABAJO (etiqueta que pedir o número que
    escribir), no renglones de la cola: lo que se descarta sin llamar a TikTok
    (cancelada, SELLER, ON_HOLD, ya recolectada, recordada) no ocupa lugar, y lo
    que no cupo sale en `diferidas` con AWAITING_COLLECTION siempre primero.
    """
    if solo_ids is not None and _candado_guias.locked():
        return {"omitido": "otra vuelta de guías en curso", "pendientes": 0}
    async with _candado_guias:
        try:
            r = await _refrescar_guias(dias, limite, segundos_max, solo_ids)
        except Exception as exc:  # noqa: BLE001 — cinturón: nunca lanza
            log.exception("pedidos_tiktok.refrescar_guias falló")
            r = {"error": f"{type(exc).__name__}: {str(exc)[:200]}", "pendientes": 0}
        if solo_ids is None:
            _ultimo_guias.clear()
            _ultimo_guias.update({k: v for k, v in r.items() if k != "errores"},
                                 ts=datetime.now(timezone.utc).isoformat())
        return r


async def _refrescar_guias(dias: int, limite: int, segundos_max: int,
                           solo_ids: list[str] | None) -> dict[str, Any]:
    import time as _time

    from services import odoo_ventas, odoo_ventas_log
    from services import tiktok as tk

    r: dict[str, Any] = {"pendientes": 0, "miradas": 0, "lotes": 0, "fallos_tiktok": 0,
                         "con_guia": 0, "sin_guia_aun": 0,
                         "guias_escritas": 0, "guias_verificadas": 0, "guias_no_escritas": 0,
                         "pdf_subidos": 0, "pdf_verificados": 0, "pdf_fallos": 0,
                         "pdf_sin_agendar": 0, "pdf_terminales": 0, "multi_paquete": 0,
                         "diferidas": 0, "recordadas": 0,
                         "motivos": {}, "codigos": {}, "cortado_por_tiempo": False,
                         "errores": [], "error": None}
    try:
        # SIEMPRE la cola completa: el límite va después del estado en TikTok.
        cola = await asyncio.to_thread(
            odoo_ventas.pendientes_de_guia, "tiktok", int(dias), COLA_MAX)
    except Exception as exc:  # noqa: BLE001
        log.warning("TIKTOK guías: no se pudo armar la cola: %s", str(exc)[:200])
        return {**r, "error": f"cola: {str(exc)[:200]}"}
    if solo_ids is not None:
        pedidos = {str(x) for x in solo_ids}
        cola = [c for c in cola if str(c.get("order_id")) in pedidos]
    r["pendientes"] = len(cola)
    if not cola:
        return r

    token, ciph = await asyncio.to_thread(lambda: (tk.access_token(), tk.cipher()))
    if not (token and ciph):
        return {**r, "error": "TikTok sin token o sin shop_cipher"}

    _podar_memoria()
    # 1 · LO QUE SE DESCARTA SIN PREGUNTAR A TIKTOK: ref no numérica, y lo que la
    #     memoria ya sabe que no da etiqueta (si no le falta el número).
    por_mirar: list[dict[str, Any]] = []
    for item in cola:
        oid = str(item.get("order_id") or "")
        r["miradas"] += 1
        if not oid.isdigit():
            _contar(r, "motivos", "ref_no_numerica")
            continue
        rec = None if solo_ids is not None else _recordado(oid)
        if rec and not item.get("pickings"):
            r["recordadas"] += 1
            _contar(r, "motivos", rec)
            continue
        por_mirar.append(item)
    ids = [str(c["order_id"]) for c in por_mirar]
    detalles = await _detalles_en_lotes(ids, token, ciph, r) if ids else {}

    # 2 · CLASIFICAR CON EL DETALLE. Lo descartado no ocupa lugar del límite.
    trabajo: list[tuple[int, int, dict[str, Any], dict[str, Any], bool]] = []
    for n, item in enumerate(por_mirar):
        oid = str(item["order_id"])
        f = detalles.get(oid)
        if not f:
            _contar(r, "motivos", "sin_detalle_tiktok")
            continue
        st = f["status"]
        if st in _ESTADOS_SIN_ETIQUETA:
            # ON_HOLD: esperar. UNPAID/CANCELLED: no hay nada que enviar.
            _contar(r, "motivos", f"estado_{st.lower()}")
            if st == "CANCELLED":
                _recordar(oid, "estado_cancelled")
            continue
        if f["shipping_type"] != "TIKTOK":
            _contar(r, "motivos", "envio_no_tiktok")
            _recordar(oid, "envio_no_tiktok")
            continue
        solo_guia = bool(item.get("pickings") and f["guia"])
        if st in _ESTADOS_RECOLECTADOS:
            # Ya recogida: sin PDF posible. El número, si la entrega lo espera.
            if solo_guia:
                trabajo.append((_PRIORIDAD_SOLO_GUIA, n, item, f, False))
            else:
                _contar(r, "motivos", f"estado_{st.lower()}")
                _recordar(oid, f"estado_{st.lower()}")
            continue
        if not f["paquetes"]:
            _contar(r, "motivos", "sin_paquete")
            continue
        rec = None if solo_ids is not None else _recordado(oid)
        if rec:
            # Etiqueta terminal ya vista: no se vuelve a pedir; sólo el número.
            if solo_guia:
                trabajo.append((_PRIORIDAD_SOLO_GUIA, n, item, f, False))
            else:
                r["recordadas"] += 1
                _contar(r, "motivos", rec)
            continue
        trabajo.append((_PRIORIDAD_URGENTE if st == "AWAITING_COLLECTION"
                        else _PRIORIDAD_RESTO, n, item, f, True))
    trabajo.sort(key=lambda t: (t[0], t[1]))
    tope = len(trabajo) if solo_ids is not None else max(1, int(limite))
    if len(trabajo) > tope:
        r["diferidas"] = len(trabajo) - tope
        trabajo = trabajo[:tope]

    # 3 · EL TRABAJO: etiqueta (si cabe), número a la entrega, PDF a la orden.
    etiquetas: dict[str, dict[str, Any]] = {}      # por paquete: combinado = 1 PDF
    limite_reloj = _time.monotonic() + max(30, int(segundos_max))
    try:
        for _prioridad, _n, item, f, pedir_pdf in trabajo:
            if _time.monotonic() > limite_reloj:
                r["cortado_por_tiempo"] = True
                break
            oid = str(item["order_id"])
            if pedir_pdf and len(f["paquetes"]) > 1:
                # Surtido en varios paquetes: la orden tiene UN campo de PDF.
                # Se sube el del primero y queda contado para revisarlo.
                r["multi_paquete"] += 1
            paquete = f["paquetes"][0] if f["paquetes"] else ""
            guia, paqueteria = f["guia"], f["paqueteria"]
            necesita_pdf = pedir_pdf and bool(item.get("sin_pdf"))
            et: dict[str, Any] | None = None
            if pedir_pdf and (necesita_pdf or (item.get("pickings") and not guia)):
                if paquete not in etiquetas:
                    etiquetas[paquete] = await tk.descargar_etiqueta(paquete, token, ciph)
                et = etiquetas[paquete]
                if not guia and et.get("tracking_number"):
                    guia = str(et["tracking_number"])
                if not et.get("ok"):
                    if et.get("codigo"):
                        _contar(r, "codigos", str(et["codigo"]))
                    if et.get("clase") == "no_agendado":
                        r["pdf_sin_agendar"] += 1        # lo normal antes del agendado
                    elif et.get("terminal"):
                        r["pdf_terminales"] += 1
                        # No se vuelve a pedir en cada vuelta (ver la memoria).
                        _recordar(oid, "etiqueta_terminal")
                        log.warning("TIKTOK guías: el paquete de %s no da etiqueta "
                                    "(code=%s)", oid, et.get("codigo"))
                    else:
                        r["pdf_fallos"] += 1
            if not guia and not (et and et.get("ok")):
                # Nada que escribir todavía. Si se pidió la etiqueta, su fallo ya
                # quedó contado arriba con su clase: no se cuenta dos veces.
                if et is None:
                    r["sin_guia_aun"] += 1
                continue
            if guia:
                r["con_guia"] += 1

            # 1 · EL NÚMERO EN LA ENTREGA (fijar_guia re-lee después de escribir).
            if item.get("pickings") and guia:
                res = await asyncio.to_thread(odoo_ventas.fijar_guia, "tiktok", oid,
                                              guia, item["pickings"])
                if res.get("accion") == "ya_tenia":
                    pass
                elif res.get("ok") and res.get("verificada"):
                    r["guias_escritas"] += 1
                    r["guias_verificadas"] += 1
                else:
                    r["guias_no_escritas"] += 1
                    log.warning("TIKTOK guías: la guía de %s NO quedó en Odoo (%s)",
                                oid, res.get("accion"))

            # 2 · EL PDF EN LA ORDEN, `<order_id>.pdf` (fijar_etiqueta re-lee).
            if necesita_pdf and et and et.get("ok"):
                res = await asyncio.to_thread(odoo_ventas.fijar_etiqueta, "tiktok", oid,
                                              item["sin_pdf"], et["pdf"], f"{oid}.pdf")
                if res.get("accion") in ("ya_tenia", "sin_confirmar"):
                    pass
                elif res.get("ok") and res.get("verificada"):
                    r["pdf_subidos"] += 1
                    r["pdf_verificados"] += 1
                else:
                    r["pdf_fallos"] += 1
                    log.warning("TIKTOK guías: el PDF de %s NO quedó en Odoo (%s)",
                                oid, res.get("accion"))

            # 3 · LA BITÁCORA que pinta el tab.
            if guia:
                try:
                    await asyncio.to_thread(odoo_ventas_log.actualizar_guia, "tiktok",
                                            CUENTA, oid, guia, paqueteria)
                except Exception as exc:  # noqa: BLE001
                    log.warning("TIKTOK guías: bitácora de %s: %s", oid, str(exc)[:150])
    finally:
        # El PDF trae la dirección del comprador: fuera de memoria en cuanto se
        # usó, pase lo que pase.
        etiquetas.clear()

    if r["pendientes"]:
        log.info("TIKTOK guías: %s", {k: v for k, v in r.items() if k != "errores"})
    return r
