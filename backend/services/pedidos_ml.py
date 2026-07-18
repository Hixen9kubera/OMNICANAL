"""
pedidos_ml.py — Convierte una venta de Mercado Libre en un pedido de WooCommerce.

POR QUÉ EXISTE ESTO
-------------------
Los precios del catálogo cambian todo el tiempo, así que el catálogo NO sirve
para saber en cuánto se vendió algo: si hoy consultas el producto te da el
precio de HOY, no el de la venta. El pedido sí lo congela.

Ejemplo real (venta #2000017449895988): el SKU TEC-1576-NEG-400ML se vendió en
$598.05 y ese mismo producto hoy está en $2,125.93 en el catálogo — 3.5× más.
Sin el pedido, ese $598.05 se pierde para siempre.

Con esto, WooCommerce pasa a ser el REGISTRO HISTÓRICO DE VENTAS.

CÓMO FUNCIONA
-------------
El webhook de ML solo avisa ("cambió la orden 123"), no trae el precio. Con ese
aviso vamos a `/orders/{id}` (ver `meli.obtener_orden`), que sí trae
`unit_price`, `seller_sku`, `sale_fee` y el estado. Con eso armamos el pedido.

EL PRECIO SE MANDA EXPLÍCITO (`subtotal`/`total` por línea). Si solo mandáramos
producto + cantidad, WooCommerce le pondría el precio de HOY y el registro
nacería mal — que es justo lo que queremos evitar.

STOCK (FULL vs. propio)
-----------------------
En una venta FULL (`logistic_type == "fulfillment"`) la pieza sale del almacén
de ML, no del nuestro: el stock que baja es `stock_full`, no `stock_real`. Por
eso el pedido NO debe descontar stock en Woo.

Para lograrlo sin plugins usamos `_order_stock_reduced = yes`: es la bandera con
la que WooCommerce marca los pedidos a los que YA les descontó stock. Al nacer
con ella puesta, Woo da por hecho que el descuento ya ocurrió y nunca lo repite.
En ventas no-FULL no se pone y el stock baja normal.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

from config import settings
from services import db, meli

log = logging.getLogger("omnicanal.pedidos_ml")

_WC = f"{settings.wc_url.rstrip('/')}/wp-json/wc/v3"
_AUTH = (settings.wc_consumer_key, settings.wc_consumer_secret)

# ML → WooCommerce. `paid` depende del envío: si ya llegó, el pedido está cerrado.
_ESTADOS = {
    "paid": "processing",
    "confirmed": "pending",
    "payment_required": "pending",
    "payment_in_process": "on-hold",
    "partially_paid": "on-hold",
    "cancelled": "cancelled",
    "invalid": "cancelled",
}

_DDL = """
CREATE TABLE IF NOT EXISTS pedidos_ml (
    ml_order_id  VARCHAR(30) PRIMARY KEY,
    cuenta       VARCHAR(50),
    wc_order_id  INT,
    estado_ml    VARCHAR(30),
    estado_wc    VARCHAR(30),
    total        DECIMAL(12,2),
    comision     DECIMAL(12,2),
    es_full      TINYINT(1) DEFAULT 0,
    skus         VARCHAR(255),
    creado       DATETIME,
    actualizado  DATETIME,
    INDEX idx_creado (creado)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""
_schema_ok = False


def _asegurar_schema() -> None:
    global _schema_ok
    if _schema_ok:
        return
    try:
        with db.get_cursor() as cur:
            cur.execute(_DDL)
        _schema_ok = True
    except Exception as exc:  # noqa: BLE001
        log.error("No se pudo crear pedidos_ml: %s", exc)


async def resolver_producto(sku: str) -> dict | None:
    """
    SKU de ML → producto de WooCommerce.

    Consulta a Woo DIRECTO y usa el espejo local (`productos`) solo como atajo.
    Al revés no funciona: el espejo está incompleto. En el cruce de las últimas
    ~400 ventas, 66 de 177 SKUs vendidos NO estaban en el espejo pero SÍ existían
    en Woo — si nos fiáramos del espejo tiraríamos el 37% de las ventas.
    """
    if not sku:
        return None
    fila = db.fetch_one(
        "SELECT sku, wc_id, wc_parent_id FROM productos WHERE sku=%s AND wc_id IS NOT NULL",
        (sku,))
    if fila and fila.get("wc_id"):
        return {"wc_id": int(fila["wc_id"]),
                "parent_id": int(fila["wc_parent_id"]) if fila.get("wc_parent_id") else None,
                "origen": "espejo"}
    try:
        async with httpx.AsyncClient(base_url=_WC, auth=_AUTH, timeout=30.0) as cli:
            r = await cli.get("/products", params={
                "sku": sku, "status": "any",
                "_fields": "id,sku,name,type,parent_id",
                "_cb": str(time.time()),  # LiteSpeed cachea; sin esto lee basura vieja
            })
            if r.status_code == 200 and r.json():
                p = r.json()[0]
                return {"wc_id": int(p["id"]), "nombre": p.get("name"),
                        "parent_id": int(p["parent_id"]) if p.get("parent_id") else None,
                        "origen": "woo"}
    except Exception as exc:  # noqa: BLE001
        log.warning("Búsqueda de SKU %s en Woo falló: %s", sku, exc)
    return None


def estado_wc(orden: dict) -> str:
    """Estado de WooCommerce que le toca a la venta según ML."""
    est = str(orden.get("estado") or "")
    if est == "paid" and (orden.get("envio") or {}).get("estado") == "delivered":
        return "completed"
    return _ESTADOS.get(est, "pending")


async def construir_payload(orden: dict, forzar_estado: str | None = None,
                            proteger_stock: bool = False) -> dict:
    """
    Arma el pedido de WooCommerce a partir de la orden de ML (sin enviarlo).

    `proteger_stock=True` obliga a que el pedido NO descuente stock aunque la
    venta no sea FULL. Es para pruebas y para cargar ventas históricas: esas
    piezas ya salieron del almacén hace semanas, descontarlas hoy dejaría el
    inventario en negativo.
    """
    lineas, sin_mapear, skus = [], [], []
    for it in orden.get("items", []):
        total = round(it["precio_unitario"] * it["cantidad"], 2)
        skus.append(it["sku"])
        prod = await resolver_producto(it["sku"])
        if prod:
            linea = {"product_id": prod["parent_id"] or prod["wc_id"],
                     "quantity": it["cantidad"],
                     # Precio EXPLÍCITO: congela el de la venta. Sin esto Woo
                     # cobraría el precio de hoy.
                     "subtotal": f"{total:.2f}", "total": f"{total:.2f}"}
            if prod["parent_id"]:
                linea["variation_id"] = prod["wc_id"]
        else:
            # Sin producto en Woo la venta NO se pierde: entra como línea suelta
            # con su precio real y el SKU queda visible para darlo de alta.
            sin_mapear.append(it["sku"])
            linea = {"name": f"[{it['sku']}] {it['titulo']}"[:120],
                     "quantity": it["cantidad"],
                     "subtotal": f"{total:.2f}", "total": f"{total:.2f}"}
        lineas.append(linea)

    comision = round(sum(i["comision_ml"] * i["cantidad"] for i in orden["items"]), 2)
    comp = orden.get("comprador") or {}
    metas = [
        {"key": "_ml_order_id", "value": orden["id"]},
        {"key": "_ml_cuenta", "value": orden["cuenta"]},
        {"key": "_ml_estado", "value": str(orden.get("estado"))},
        {"key": "_ml_comision", "value": str(comision)},
        {"key": "_ml_logistica", "value": str((orden.get("envio") or {}).get("logistica") or "")},
        {"key": "_ml_es_full", "value": "yes" if orden.get("es_full") else "no"},
        {"key": "_ml_neto", "value": f"{orden['total'] - comision:.2f}"},
        {"key": "_ml_comprador", "value": str(comp.get("nick") or "")},
    ]
    # Venta FULL: la pieza sale del almacén de ML. Nacer con la bandera de
    # "stock ya descontado" evita que Woo baje nuestro stock_real.
    # EXCEPCIÓN: si el pedido nace ya CANCELADO, la bandera se omite — con ella
    # puesta, el hook de cancelación de Woo "devolvería" a bodega una pieza que
    # nunca salió de ahí (inventaría stock).
    estado_final = forzar_estado or estado_wc(orden)
    if (orden.get("es_full") or proteger_stock) and estado_final != "cancelled":
        metas.append({"key": "_order_stock_reduced", "value": "yes"})

    return {
        "status": forzar_estado or estado_wc(orden),
        "currency": orden.get("moneda") or "MXN",
        "billing": {"first_name": comp.get("nombre") or "Comprador",
                    "last_name": comp.get("apellido") or "Mercado Libre"},
        "customer_note": (f"Venta Mercado Libre #{orden['id']} · {orden['cuenta']}"
                          f"{' · FULL' if orden.get('es_full') else ''}"),
        "line_items": lineas,
        "shipping_lines": ([{"method_id": "flat_rate", "method_title": "Envío Mercado Libre",
                             "total": f"{orden.get('envio_costo') or 0:.2f}"}]
                           if orden.get("envio_costo") else []),
        "meta_data": metas,
        "_skus": skus,            # internos: se quitan antes del POST
        "_sin_mapear": sin_mapear,
        "_comision": comision,
    }


# ML manda RÁFAGAS de avisos por la misma venta (creada→pagada→enviada, con
# segundos o milisegundos entre sí) que se procesan en tareas concurrentes.
# Sin candado, todas veían "no existe previo" y cada una CREABA su propio
# pedido en Woo: el 2026-07-17 amanecieron 86 órdenes con 2-7 copias (160
# pedidos fantasma). Un lock por orden serializa: la primera crea, las demás
# ya encuentran el registro y solo actualizan estado.
_locks: dict[str, asyncio.Lock] = {}


async def sincronizar(order_id: str, forzar_estado: str | None = None,
                      proteger_stock: bool = False,
                      orden: dict | None = None) -> dict:
    """
    Trae la orden de ML y la crea (o actualiza) como pedido en WooCommerce.

    Idempotente Y serializada por orden: los webhooks repetidos de la misma
    venta actualizan el estado del mismo pedido, nunca duplican. `orden`
    permite pasar la orden ya traída (el webhook la consulta primero).
    """
    if len(_locks) > 4000:  # poda: candados de órdenes viejas ya sin uso
        for k in [k for k, l in _locks.items() if not l.locked()][:2000]:
            _locks.pop(k, None)
    lock = _locks.setdefault(str(order_id), asyncio.Lock())
    async with lock:
        return await _sincronizar_serializado(order_id, forzar_estado,
                                              proteger_stock, orden)


async def _sincronizar_serializado(order_id: str, forzar_estado: str | None,
                                   proteger_stock: bool,
                                   orden: dict | None) -> dict:
    _asegurar_schema()
    orden = orden or await meli.obtener_orden(order_id)
    if not orden:
        return {"ok": False, "motivo": "orden no encontrada en ML"}

    payload = await construir_payload(orden, forzar_estado, proteger_stock)
    skus = payload.pop("_skus"); sin_mapear = payload.pop("_sin_mapear")
    comision = payload.pop("_comision")
    previo = db.fetch_one("SELECT wc_order_id FROM pedidos_ml WHERE ml_order_id=%s", (order_id,))
    ahora = datetime.now(timezone.utc)

    try:
        async with httpx.AsyncClient(base_url=_WC, auth=_AUTH, timeout=45.0) as cli:
            if previo and previo.get("wc_order_id"):
                # Ya existía: solo movemos el estado (el precio no se re-toca).
                wc_id = int(previo["wc_order_id"])
                # CANDADO de cancelación: en pedidos protegidos (FULL/registro)
                # Woo "devolvería" a bodega stock que nunca salió de ahí. Se le
                # quita la marca ANTES del cambio de estado; con la marca en
                # "no", el hook de restock no repone nada. Los no-FULL conservan
                # su marca y Woo SÍ repone (la pieza se quedó en la bodega).
                if (payload["status"] == "cancelled"
                        and (orden.get("es_full") or proteger_stock)):
                    await cli.put(f"/orders/{wc_id}", json={
                        "meta_data": [{"key": "_order_stock_reduced",
                                       "value": "no"}]})
                r = await cli.put(f"/orders/{wc_id}", json={"status": payload["status"]})
                accion = "actualizado"
            else:
                r = await cli.post("/orders", json=payload)
                accion = "creado"
            if r.status_code not in (200, 201):
                return {"ok": False, "motivo": f"WooCommerce HTTP {r.status_code}",
                        "detalle": r.text[:200]}
            pedido = r.json()
            wc_id = int(pedido["id"])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": f"error al crear pedido: {exc}"}

    try:
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO pedidos_ml (ml_order_id, cuenta, wc_order_id, estado_ml,
                       estado_wc, total, comision, es_full, skus, creado, actualizado)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE wc_order_id=VALUES(wc_order_id),
                       estado_ml=VALUES(estado_ml), estado_wc=VALUES(estado_wc),
                       actualizado=VALUES(actualizado)""",
                (order_id, orden["cuenta"], wc_id, orden.get("estado"), payload["status"],
                 orden["total"], comision, 1 if orden.get("es_full") else 0,
                 ",".join(s for s in skus if s)[:255], ahora, ahora))
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo registrar pedidos_ml %s: %s", order_id, exc)

    return {"ok": True, "accion": accion, "wc_order_id": wc_id, "ml_order_id": order_id,
            "cuenta": orden["cuenta"], "estado_wc": payload["status"],
            "estado_ml": orden.get("estado"), "total": orden["total"],
            "comision": comision, "neto": round(orden["total"] - comision, 2),
            "es_full": bool(orden.get("es_full")), "skus": skus,
            "sin_mapear": sin_mapear}
