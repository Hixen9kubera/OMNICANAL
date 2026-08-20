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
from services import (alertas, core_read, db, fanout_stock, kubera_mirror,
                      lecturas_fuente, meli, orders_write, pii)

log = logging.getLogger("omnicanal.pedidos_ml")

# Espejo kubera: cuenta → (canal v4, archivo origen del censo, función). Los
# pedidos de Amazon/M2E entran por sincronizar() pero su tarjeta en /migracion
# es la de su sondeo — así los contadores cuentan donde el censo los espera.
_ESPEJO_ORIGEN = {
    "BEKURA": ("mercado_libre", "services/pedidos_ml.py", "sincronizar"),
    "SANCORFASHION": ("mercado_libre", "services/pedidos_ml.py", "sincronizar"),
    "AMAZON": ("amazon", "services/pedidos_amazon.py", "→ pedidos_ml.sincronizar"),
    "TEMU": ("temu", "services/pedidos_m2e.py", "→ pedidos_ml.sincronizar"),
    "TIKTOK": ("tiktok", "services/pedidos_m2e.py", "→ pedidos_ml.sincronizar"),
}

_WC = f"{settings.wc_url.rstrip('/')}/wp-json/wc/v3"

# Antigüedad a partir de la cual una venta se considera REGISTRO HISTÓRICO y ya
# NO mueve bodega (su salida física ocurrió hace semanas). El desfase real medido
# en los avisos tardíos de ML fue de 20 y 28 días; 5 días deja margen de sobra
# para los avisos normales (que llegan en segundos) sin dejar pasar los viejos.
DIAS_VENTA_VIEJA = 5
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

    Consulta a Woo DIRECTO y usa el registro civil (`core.products`) solo como
    atajo. Al revés no funciona: el atajo puede no tener el SKU. En el cruce de
    las últimas ~400 ventas contra el espejo MySQL viejo, 66 de 177 SKUs
    vendidos NO estaban ahí pero SÍ existían en Woo — si nos fiáramos del atajo
    tiraríamos el 37% de las ventas. Por eso el miss cae a Woo, no se descarta.
    """
    if not sku:
        return None
    fila = None
    # PASO 3 del desmantelamiento (12-ago-2026): el atajo es core.products y ya
    # NO se reconsulta MySQL. El fallback nació cuando el seam Crear→core no
    # existía y un SKU del día aparecía hasta el ETL de las 06:15; desde el
    # corte (v0.84) y el webhook de Woo (v0.92) ese hueco está cubierto, y
    # además el espejo MySQL era un subconjunto: 5,381 filas contra las 22,279
    # de core.products. Un miss aquí sigue cayendo a Woo, que es la autoridad.
    if settings.supabase_read_core:
        try:
            fila = core_read.wc_de_sku(sku)
            lecturas_fuente.anotar("core", "kubera")
        except Exception as exc:  # noqa: BLE001
            lecturas_fuente.anotar("core", "fallback", str(exc))
            alertas.avisar("lectura_fallback:core",
                           f"⚠️ Lectura de CORE falló (resolver_producto), se "
                           f"resuelve por Woo: {exc}")
            log.warning("lectura kubera falló (resolver_producto) — sigue a Woo: %s", exc)
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
        # PII: el nick identifica a una persona → se guarda CIFRADO (ver pii.py).
        {"key": "_ml_comprador", "value": pii.cifrar(comp.get("nick"))},
    ]
    # Venta FULL: la pieza sale del almacén de ML. Nacer con la bandera de
    # "stock ya descontado" evita que Woo baje nuestro stock_real.
    # EXCEPCIÓN: si el pedido nace ya CANCELADO, la bandera se omite — con ella
    # puesta, el hook de cancelación de Woo "devolvería" a bodega una pieza que
    # nunca salió de ahí (inventaría stock).
    # VENTA VIEJA que se registra HOY por primera vez: NO debe mover bodega.
    # ML manda avisos TARDÍOS del ciclo de vida (se midió +28 días al cerrar una
    # orden y +20 al expirar una impaga). Esas ventas ya salieron del almacén hace
    # semanas y ya están reflejadas en el stock actual — descontarlas otra vez es
    # restar dos veces. Pasó el 27-jul: 10 pedidos de junio nacieron `completed`
    # y bajaron 10 piezas reales (ACC-0250-NEG cayó 74→67 en 17 h), y el fan-out
    # replicó fielmente cada baja a Amazon.
    venta_vieja = False
    try:
        f = str(orden.get("fecha") or "")
        if f:
            dias = (datetime.now(timezone.utc)
                    - datetime.fromisoformat(f).astimezone(timezone.utc)).days
            venta_vieja = dias >= DIAS_VENTA_VIEJA
            if venta_vieja:
                log.info("Pedido %s: venta de hace %d días — se registra SIN mover stock",
                         orden.get("id"), dias)
    except Exception:  # noqa: BLE001 — sin fecha legible se trata como reciente
        pass

    estado_final = forzar_estado or estado_wc(orden)
    if (orden.get("es_full") or proteger_stock or venta_vieja) and estado_final != "cancelled":
        metas.append({"key": "_order_stock_reduced", "value": "yes"})

    return {
        "status": forzar_estado or estado_wc(orden),
        "currency": orden.get("moneda") or "MXN",
        # PII: nombre y apellido van CIFRADOS (ver pii.py). Sin nombre real se
        # deja el marcador legible — no hay dato personal que proteger.
        "billing": {"first_name": pii.cifrar(comp.get("nombre")) or "Comprador",
                    "last_name": pii.cifrar(comp.get("apellido")) or "Mercado Libre"},
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


def _leer_reducido(wc_id: int) -> list[dict]:
    """
    Lo que Woo REALMENTE descontó en un pedido: `_reduced_stock` por línea.

    OJO — hay que leerla ANTES de cancelar: al reponer, Woo BORRA la meta
    (`wc_maybe_increase_stock_levels`). Medido en producción: de 665 pedidos
    cancelados en julio, CERO conservan una sola línea con `_reduced_stock`.
    La línea tampoco guarda el SKU: guarda `_product_id`/`_variation_id`.
    """
    from services import wp_db
    P = wp_db._prefix()
    return wp_db._fetch_all(
        f"""SELECT red.meta_value AS unidades,
                   CAST(COALESCE(NULLIF(var.meta_value,'0'), pro.meta_value) AS UNSIGNED) AS producto
            FROM {P}woocommerce_order_items oi
            JOIN {P}woocommerce_order_itemmeta red
                 ON red.order_item_id = oi.order_item_id AND red.meta_key = '_reduced_stock'
            LEFT JOIN {P}woocommerce_order_itemmeta pro
                 ON pro.order_item_id = oi.order_item_id AND pro.meta_key = '_product_id'
            LEFT JOIN {P}woocommerce_order_itemmeta var
                 ON var.order_item_id = oi.order_item_id AND var.meta_key = '_variation_id'
            WHERE oi.order_id = %s""", (int(wc_id),))


async def _compensar_stock_protegido(wc_id: int, order_id: str, cuenta: str,
                                     signo: int = 1,
                                     filas: list[dict] | None = None) -> dict:
    """
    Devuelve a Woo las piezas que descontó de un pedido FULL/FBA.

    POR QUÉ EXISTE (hallazgo 28-jul): la protección `_order_stock_reduced=yes`
    **NUNCA se guarda** — se manda en el alta y la REST de Woo la FILTRA (se
    verificó: responde 200 pero no queda en `wc_orders_meta`). Los pedidos FULL de
    ML se salvan por accidente: nacen ya en su estado final y Woo no ejecuta la
    reducción al crear por API. Los de Amazon FBA nacen `on-hold` (Amazon los manda
    *Pending*) y al pasarlos a `completed` esa TRANSICIÓN sí dispara la reducción.
    Resultado: 6 pedidos FBA descontaron 7 piezas de MUE-0307-GRI (Woo llegó a −5).

    Como no se puede escribir la meta interna, se COMPENSA: se lee lo que Woo
    realmente descontó (`_reduced_stock` por línea, la contabilidad de verdad) y
    se devuelve. Queda registrado en `fanout_log` para poder auditarlo Y para
    saber, si el pedido se cancela después, que Woo va a reponer OTRA VEZ lo mismo
    (ahí hay que volver a restarlo: ver `_revertir_compensacion`).
    """
    from services import fanout_stock, woocommerce, wp_db
    P = wp_db._prefix()
    try:
        # `filas` llega precargada en la reversión: ahí la foto se toma ANTES de
        # cancelar, porque Woo borra `_reduced_stock` al reponer.
        if filas is None:
            filas = _leer_reducido(wc_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("compensar %s: no se pudo leer _reduced_stock: %s", wc_id, exc)
        return {"ok": False, "motivo": str(exc)[:120]}
    if not filas:
        return {"ok": True, "compensado": 0, "motivo": "Woo no descontó nada"}

    devueltos = []
    async with woocommerce._client() as cli:
        for f in filas:
            try:
                n = int(float(f["unidades"] or 0))
            except (TypeError, ValueError):
                continue
            producto = f.get("producto")
            if n <= 0 or not producto:
                continue
            info = wp_db._fetch_all(
                f"""SELECT p.ID wc_id, p.post_type, p.post_parent,
                           st.meta_value stock, sk.meta_value sku
                    FROM {P}posts p
                    LEFT JOIN {P}postmeta st ON st.post_id = p.ID AND st.meta_key = '_stock'
                    LEFT JOIN {P}postmeta sk ON sk.post_id = p.ID AND sk.meta_key = '_sku'
                    WHERE p.ID = %s LIMIT 1""", (int(producto),))
            if not info:
                continue
            sku = (info[0].get("sku") or f"wc:{producto}").strip()
            i = info[0]
            try:
                actual = int(float(i["stock"])) if i["stock"] not in (None, "") else 0
            except (TypeError, ValueError):
                actual = 0
            destino = max(0, actual + n * signo)   # signo=-1 revierte la compensación
            ruta = (f"/products/{i['post_parent']}/variations/{i['wc_id']}"
                    if i["post_type"] == "product_variation" else f"/products/{i['wc_id']}")
            r = await cli.put(ruta, json={"stock_quantity": destino}, timeout=60.0)
            ok = r.status_code in (200, 201)
            devueltos.append({"sku": sku, "unidades": n, "woo": f"{actual}→{destino}", "ok": ok})
            log.info("FULL/FBA #%s: devueltas %d pza(s) de %s a Woo (%s→%s)",
                     wc_id, n, sku, actual, destino)
    # Bitácora (misma tabla del panel; NO se crea tabla nueva)
    try:
        fanout_stock._asegurar_schema()
        with db.get_cursor() as cur:
            for d in devueltos:
                cur.execute(
                    """INSERT INTO fanout_log
                       (ts, sku, motivo, dry_run, stock_drop, objetivo, canal, cuenta,
                        item_id, accion, stock_canal, resultado, ms)
                       VALUES (%s,%s,%s,0,NULL,NULL,%s,%s,%s,%s,NULL,%s,0)""",
                    (datetime.now(timezone.utc).replace(tzinfo=None), d["sku"],
                     f"compensacion FULL/FBA pedido {order_id}", "woocommerce", cuenta,
                     str(wc_id)[:64],
                     ("full_compensado_revertido" if signo < 0 else "full_compensado")
                     if d["ok"] else "full_compensado_error",
                     (f"Cancelado: Woo repuso {d['unidades']} pza(s) que nunca salieron "
                      f"→ restadas ({d['woo']})" if signo < 0 else
                      f"Woo habia descontado {d['unidades']} pza(s) de un pedido FULL/FBA "
                      f"→ devueltas ({d['woo']})")[:255]))
    except Exception as exc:  # noqa: BLE001
        log.warning("compensar %s: no se pudo registrar: %s", wc_id, exc)
    return {"ok": True, "compensado": sum(d["unidades"] for d in devueltos), "detalle": devueltos}


def _sellar_candado(que: str, cuenta: str, order_id: str) -> None:
    """Deja la marca en kubera tras compensar o revertir.

    Va en su propio try/except, y aquí SÍ corresponde: la marca es constancia
    de algo que YA pasó en Woo. Si falla, lo peor es que la próxima vuelta
    compense otra vez —malo, pero visible en el stock— mientras que romper aquí
    dejaría la venta a medias por no poder escribir una bitácora.

    Lo que NO lleva except es la LECTURA (`_ya_compensado`): ahí un error
    silencioso decide mover mercancía.
    """
    if not settings.supabase_read_candados:
        return
    try:
        from services import candados_read
        if que == "compensado":
            candados_read.marcar_compensado("mercado_libre", cuenta, order_id)
        else:
            candados_read.marcar_revertido("mercado_libre", cuenta, order_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo sellar el candado %s de %s: %s", que, order_id, exc)


def _ya_compensado(wc_id: int, cuenta: str | None = None,
                   order_id: str | None = None) -> bool:
    """¿Ya le devolvimos el stock a este pedido? (evita compensar dos veces).

    PASO 0 (19-ago). Con `supabase_read_candados` la respuesta sale de
    `channel.orders` y **PROPAGA si la base falla**. El `except → False` de
    abajo es el defecto que este paso viene a quitar: convierte "no pude
    preguntar" en "no lo he hecho", y eso COMPENSA DE NUEVO — le devuelve al
    almacén piezas que nunca salieron.

    Se busca por la PK `(canal, cuenta, external_order_id)` y no por `wc_id`:
    desde el reclamo (v0.176.0) `wc_order_id` es NULL a propósito mientras el
    pedido está reclamado y todavía no creado, que es justo el momento revuelto.
    """
    if settings.supabase_read_candados and cuenta and order_id:
        from services import candados_read
        return candados_read.ya_compensado("mercado_libre", cuenta, str(order_id))
    try:
        return bool(db.fetch_one(
            "SELECT id FROM fanout_log WHERE item_id=%s AND accion='full_compensado' LIMIT 1",
            (str(wc_id)[:64],)))
    except Exception:  # noqa: BLE001
        return False


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
    # Candado de idempotencia contra el REGISTRO (channel.orders desde el corte
    # F6), no contra el espejo: `pedidos_ml` congelada contestaba siempre "no
    # existe" y cada aviso creaba otro pedido — 964 fantasma el 12-ago-2026.
    from services import orders_write
    # A un HILO: es una consulta a kubera (psycopg2, bloqueante) y está en el
    # camino de CADA aviso de ML. En la corrutina, el backend entero se paraba
    # aquí mientras Postgres contestaba.
    wc_previo = await asyncio.to_thread(orders_write.wc_order_id_previo, str(order_id))
    reclamo_mio = False
    if not wc_previo:
        # RECLAMO ANTES DE CREAR (14-ago-2026). `_locks` serializa dentro de UN
        # proceso; el relevo de contenedores de un deploy tiene dos. Aquí el
        # derecho a crear se gana con un insert atómico sobre la PK, que sí
        # cruza procesos. Ver orders_write.reclamar.
        cta = orden["cuenta"]
        cnl = _ESPEJO_ORIGEN.get(
            cta, (str(orden.get("detalle") or cta).lower(), "", ""))[0]
        reclamo_mio = await asyncio.to_thread(
            orders_write.reclamar, cnl, cta, str(order_id))
        if not reclamo_mio:
            # Lo tiene otro proceso. Si sigue vivo termina en un parpadeo, así
            # que se le da margen antes de decidir nada.
            for _ in range(4):
                await asyncio.sleep(1.0)
                wc_previo = await asyncio.to_thread(
                    orders_write.wc_order_id_previo, str(order_id))
                if wc_previo:
                    break
            if not wc_previo:
                # No completó: o murió a media petición, o creó en Woo sin
                # alcanzar a registrarlo — que es exactamente lo que dejó
                # #123068/#123069. Se le pregunta a Woo, que es donde el
                # duplicado se vería.
                from services import wp_db  # local: evita ciclo de importación
                wc_previo = await asyncio.to_thread(
                    wp_db.pedido_por_ml_order_id, str(order_id))
                if wc_previo:
                    log.warning("orden %s: el reclamo era de otro proceso que no "
                                "completó; se adopta el pedido %s que ya existía "
                                "en Woo", order_id, wc_previo)
                else:
                    # Nadie lo creó: tomamos el relevo.
                    log.warning("orden %s: reclamo huérfano sin pedido en Woo; "
                                "se crea aquí", order_id)
    previo = {"wc_order_id": wc_previo} if wc_previo else None
    ahora = datetime.now(timezone.utc)
    # `creado` = fecha de la VENTA en ML, no de nuestro registro: el tab
    # bucketiza por esta columna y un backfill fechado "hoy" deforma los días
    # (el hueco del sáb 18-jul apareció como pico del lunes 20).
    creado = ahora
    try:
        f = str(orden.get("fecha") or "")
        if f:
            creado = datetime.fromisoformat(f).astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        pass

    foto_previa: list[dict] | None = None   # `_reduced_stock` antes de cancelar
    try:
        async with httpx.AsyncClient(base_url=_WC, auth=_AUTH, timeout=45.0) as cli:
            if previo and previo.get("wc_order_id"):
                # Ya existía: solo movemos el estado (el precio no se re-toca).
                wc_id = int(previo["wc_order_id"])
                # CANDADO de cancelación (histórico): mandaba
                # `_order_stock_reduced=no` para que Woo no repusiera stock que
                # nunca salió de bodega. Se conserva porque la REST SÍ honra la
                # meta DENTRO de la petición, aunque NO la persista (28-jul);
                # pero por lo mismo no protege una transición posterior. La
                # defensa de verdad es la compensación de más abajo.
                if (payload["status"] == "cancelled"
                        and (orden.get("es_full") or proteger_stock)):
                    await cli.put(f"/orders/{wc_id}", json={
                        "meta_data": [{"key": "_order_stock_reduced",
                                       "value": "no"}]})
                    # Foto ANTES de cancelar: al reponer, Woo BORRA
                    # `_reduced_stock` y después ya no habría qué revertir.
                    if _ya_compensado(wc_id):
                        try:
                            foto_previa = _leer_reducido(wc_id)
                        except Exception:  # noqa: BLE001
                            foto_previa = None
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
        # El reclamo se suelta si no llegó a pedido: dejarlo puesto haría que
        # el siguiente aviso viera "ya reclamada" y no la creara NUNCA — la
        # venta se perdería en silencio, peor que el duplicado que se evita.
        if reclamo_mio:
            await asyncio.to_thread(
                orders_write.liberar, cnl, cta, str(order_id))
        return {"ok": False, "motivo": f"error al crear pedido: {exc}"}

    # ORDEN (v0.177.0): el REGISTRO va ANTES de la compensación, no al revés
    # como estaba. Con el orden viejo, si la compensación se caía —o si su
    # candado PROPAGABA, que es lo que el paso 0 viene a hacer— la excepción
    # subía con el pedido YA creado en Woo y la venta SIN registrar en kubera.
    #
    # El reclamo (v0.176.0) evita que eso DUPLIQUE: el reintento pierde el
    # reclamo, le pregunta a Woo y adopta el pedido. Pero no evita que la venta
    # se quede sin registro hasta que un reintento lo consiga, pagando 4 s de
    # espera cada vez. Los dos arreglos son complementarios, no alternativos.
    #
    # Registrar primero pone el registro a salvo de todo lo que venga después:
    # la compensación puede fallar RUIDOSAMENTE sin arrastrar la venta.
    # Es seguro por construcción: nada del registro depende de la compensación
    # (`encabezado` sale de `orden`/`payload`/`comision`/`skus`, calculados
    # todos antes de crear en Woo), y la compensación no lee nada que el
    # registro produzca.
    try:
        def _mysql() -> None:
            with db.get_cursor() as cur:
                cur.execute(
                    """INSERT INTO pedidos_ml (ml_order_id, cuenta, wc_order_id, estado_ml,
                           estado_wc, total, comision, es_full, skus, creado, actualizado)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON DUPLICATE KEY UPDATE wc_order_id=VALUES(wc_order_id),
                           estado_ml=VALUES(estado_ml), estado_wc=VALUES(estado_wc),
                           -- La comisión NO se re-toca (congela el dato histórico de la
                           -- venta), SALVO que esté en 0: un 0 no es histórico, es un
                           -- dato que nunca se calculó (token caído al crearse). Solo
                           -- se permite el paso 0 → valor real; un valor ya puesto es
                           -- inmutable (COALESCE(NULLIF...) evita re-pisar >0).
                           comision=IF(comision=0, VALUES(comision), comision),
                           -- MISMA REGLA para el TOTAL (hallazgo de Eduardo, 28-jul):
                           -- Amazon NO publica los importes mientras la orden está
                           -- "Pending" (OrderTotal e ItemPrice llegan vacíos), así que
                           -- la venta nacía congelada en $0 y ahí se quedaba. 14 pedidos
                           -- afectados, 6 de ellos ya cobrados. Un 0 no es un dato
                           -- histórico: es un dato que nunca se pudo capturar. Solo se
                           -- permite 0 → valor real; un total ya puesto es inmutable.
                           total=IF(total=0, VALUES(total), total),
                           actualizado=VALUES(actualizado)""",
                    (order_id, orden["cuenta"], wc_id, orden.get("estado"), payload["status"],
                     orden["total"], comision, 1 if orden.get("es_full") else 0,
                     ",".join(s for s in skus if s)[:255], creado, ahora))

        # Espejo kubera: el pedido viaja a channel.orders (DDL aplicado el
        # 2026-07-22 con GO de Eduardo). El array de SKUs va COMPLETO — el CSV
        # de MySQL trunca a 255; en conflicto solo se mueven estados (el total
        # congelado no se re-toca, igual que aquí).
        cuenta = orden["cuenta"]
        canal, origen_py, funcion = _ESPEJO_ORIGEN.get(
            cuenta, (str(orden.get("detalle") or cuenta).lower(),
                     "services/pedidos_m2e.py", "→ pedidos_ml.sincronizar"))
        encabezado = {
            "external_order_id": str(order_id), "canal": canal, "cuenta": cuenta,
            "wc_order_id": wc_id, "estado_canal": str(orden.get("estado") or ""),
            "estado_wc": payload["status"], "total": orden["total"],
            "comision": comision, "es_fulfillment": bool(orden.get("es_full")),
            "skus": [s for s in skus if s], "creado_at": creado}
        # LÍNEAS con cantidades e item_id → channel.order_items (F1 de la
        # absorción de dailytrack, GO Eduardo 2026-07-28). Los datos ya están
        # en memoria (meli/pedidos_amazon/pedidos_m2e normalizan igual): cero
        # llamadas extra. Tabla-origen virtual `pedidos_ml_items`: se enciende
        # sumándola a KUBERA_MIRROR_TABLAS (variable Railway, sin deploy). La
        # comisión de línea viaja como TOTAL (fee unitario × cantidad) — misma
        # base que daily_sales.sale_fee, y suma a la comisión del encabezado.
        lineas = {**encabezado, "lineas": [
            {"linea": n, "item_id": it.get("item_id") or None,
             "sku": (it.get("sku") or "").strip() or None,
             "titulo": (it.get("titulo") or "")[:200] or None,
             "cantidad": int(it.get("cantidad") or 1),
             "precio_unitario": it.get("precio_unitario"),
             "comision": round(float(it.get("comision_ml") or 0)
                               * int(it.get("cantidad") or 1), 2)}
            for n, it in enumerate(orden.get("items", []), start=1)]}
        # F6 (corte, opción A): kubera primaria + pedidos_ml de espejo inverso.
        # EN UN HILO: psycopg2 es bloqueante y esto son varios viajes a kubera.
        # Dentro de la corrutina congelaba el backend ENTERO en cada venta —
        # con la tormenta de avisos de ML, el panel dejaba de cargar (13-ago).
        def _registrar() -> None:
            if orders_write.activo():
                orders_write.guardar(origen_py, funcion, encabezado, lineas,
                                     f"{cuenta}:{order_id}", _mysql)
            else:
                _mysql()
                kubera_mirror.espejar(
                    origen_py, funcion, "pedidos_ml", "channel.orders", "UPSERT",
                    encabezado, clave=f"{cuenta}:{order_id}")
                kubera_mirror.espejar(
                    origen_py, "sincronizar (líneas)", "pedidos_ml_items",
                    "channel.order_items", "UPSERT", lineas,
                    clave=f"{cuenta}:{order_id}")

        await asyncio.to_thread(_registrar)
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo registrar pedidos_ml %s: %s", order_id, exc)

    # COMPENSACIÓN de pedidos FULL/FBA (opción A, 28-jul). La meta
    # `_order_stock_reduced` NO se puede escribir por REST (Woo la filtra), así
    # que un pedido protegido que CAMBIA de estado sí dispara la reducción de
    # Woo. Se le devuelven las piezas leyendo lo que Woo realmente descontó.
    # Los que nacen ya en su estado final (los FULL de ML) no reducen, y ahí
    # `_reduced_stock` viene vacío: la compensación simplemente no hace nada.
    protegido = bool(orden.get("es_full") or proteger_stock)

    # CANCELACIÓN de un pedido protegido que YA compensamos: Woo repone las
    # piezas por su cuenta (usa `_reduced_stock`, que sigue puesto). Como ya se
    # las habíamos devuelto nosotros, esa reposición las duplicaría → se restan.
    # El candado viejo (poner `_order_stock_reduced=no` antes de cancelar)
    # tampoco funcionaba: esa meta la filtra la REST igual que la de alta.
    if protegido and payload["status"] == "cancelled" and foto_previa:
        try:
            rev = await _compensar_stock_protegido(wc_id, str(order_id), orden["cuenta"],
                                                   signo=-1, filas=foto_previa)
            if rev.get("compensado"):
                _sellar_candado("revertido", orden["cuenta"], str(order_id))
                log.info("Pedido %s cancelado: revertidas %d pza(s) de la compensación",
                         order_id, rev["compensado"])
        except Exception as exc:  # noqa: BLE001
            log.warning("reversión de compensación de %s falló: %s", order_id, exc)

    if (protegido and payload["status"] != "cancelled"
            and not _ya_compensado(wc_id, orden["cuenta"], str(order_id))):
        try:
            comp = await _compensar_stock_protegido(wc_id, str(order_id), orden["cuenta"])
            if comp.get("compensado"):
                _sellar_candado("compensado", orden["cuenta"], str(order_id))
                log.info("Pedido %s (FULL/FBA): compensadas %d pza(s) que Woo había descontado",
                         order_id, comp["compensado"])
        except Exception as exc:  # noqa: BLE001 — nunca rompe la venta
            log.warning("compensación FULL/FBA de %s falló: %s", order_id, exc)

    # FAN-OUT del stock DROP: esta venta movió el almacén PROPIO, así que los
    # demás canales tienen que enterarse (si no, SANCORFASHION y Amazon siguen
    # ofreciendo el número viejo → sobreventa). Solo aplica cuando el pedido
    # DESCUENTA de verdad: una venta FULL/FBA sale de la bodega del marketplace
    # y no toca el almacén compartido. Fire-and-forget: nunca rompe la venta.
    try:
        descuenta = not (orden.get("es_full") or proteger_stock)
        if descuenta and accion == "creado":
            fanout_stock.encolar_varios([s for s in skus if s],
                                        motivo=f"venta {orden['cuenta']} {order_id}")
    except Exception as exc:  # noqa: BLE001
        log.warning("fan-out no encolado para %s: %s", order_id, exc)

    return {"ok": True, "accion": accion, "wc_order_id": wc_id, "ml_order_id": order_id,
            "cuenta": orden["cuenta"], "estado_wc": payload["status"],
            "estado_ml": orden.get("estado"), "total": orden["total"],
            "comision": comision, "neto": round(orden["total"] - comision, 2),
            "es_full": bool(orden.get("es_full")), "skus": skus,
            "sin_mapear": sin_mapear}
