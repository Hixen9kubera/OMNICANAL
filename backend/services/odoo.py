"""
odoo.py — Cliente Odoo vía XML-RPC (ERP / inventario maestro).

Uso en esta primera versión: cruce de stock real por SKU (default_code) cuando
se quiere verificar inventario contra Odoo. WooCommerce sigue siendo la fuente
de la vista GENERAL; Odoo es la verdad del inventario.

XML-RPC es parte de la stdlib, no requiere dependencias extra.
"""
from __future__ import annotations

import logging
import xmlrpc.client
from functools import lru_cache
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.odoo")


@lru_cache
def _uid() -> int | None:
    try:
        common = xmlrpc.client.ServerProxy(f"{settings.odoo_url}/xmlrpc/2/common")
        uid = common.authenticate(
            settings.odoo_db, settings.odoo_user, settings.odoo_password, {}
        )
        return uid or None
    except Exception as exc:  # noqa: BLE001
        log.warning("Odoo auth falló: %s", exc)
        return None


def _models() -> xmlrpc.client.ServerProxy:
    return xmlrpc.client.ServerProxy(f"{settings.odoo_url}/xmlrpc/2/object")


def stock_por_sku(skus: list[str]) -> dict[str, float]:
    """Devuelve { sku: qty_available } para los SKUs dados."""
    uid = _uid()
    if not uid or not skus:
        return {}
    try:
        productos = _models().execute_kw(
            settings.odoo_db, uid, settings.odoo_password,
            "product.product", "search_read",
            [[["default_code", "in", skus]]],
            {"fields": ["default_code", "qty_available"]},
        )
        return {
            p["default_code"]: p.get("qty_available", 0)
            for p in productos if p.get("default_code")
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("Odoo stock_por_sku falló: %s", exc)
        return {}


def listar_catalogo() -> list[dict[str, Any]]:
    """
    Devuelve TODO el catálogo activo de Odoo que tiene SKU (default_code):
    [{sku, nombre, precio, stock}]. Pagina de 500 en 500 para no cargar Odoo.
    Se usa para el diff Odoo↔WooCommerce y para alimentar el stock de los canales.

    `stock` = **`free_qty`** ("Disponible"), NO `qty_available` ("A la mano").
    ------------------------------------------------------------------------
    Cambiado el 2026-07-29 (dale de Brandon; lo señaló el equipo de Odoo con el
    caso `JUGU-0066-MUL`: 60 a la mano, 50 en Saliente, **10 libres** — y
    publicábamos 60).

    `qty_available` es el stock FÍSICO en bodega e incluye piezas ya
    COMPROMETIDAS (`outgoing_qty`): órdenes de venta por surtir y, sobre todo,
    la mercancía que se está enviando a FULL. `free_qty` ya les resta esas
    reservas, que es lo que de verdad se puede prometer a un comprador.

    Medido al cambiar: **382 SKUs** con salida comprometida y **14,097 piezas**
    que estábamos publicando de más. El peor, `CAM-0030-IND`: 230 a la mano y
    `free_qty` = 0 — ofrecíamos 230 sin una sola disponible.

    EFECTO LATERAL BUENO: esto CIERRA el hueco que perseguía el vigilante de FULL
    (`stock_full`, apagado). Las piezas que salen a la bodega de ML dejan de
    contarse en cuanto Odoo las reserva, sin depender de detectar el movimiento
    por webhook. Odoo ya llevaba esa cuenta; solo estábamos leyendo la columna
    equivocada.

    Se pide `free_qty` Y `qty_available`: si Odoo no devolviera `free_qty` (no
    existe antes de la v13), se cae a `qty_available` en vez de mandar 0 y vaciar
    el catálogo entero.
    """
    uid = _uid()
    if not uid:
        return []
    salida: list[dict[str, Any]] = []
    lote, offset = 500, 0
    try:
        while True:
            productos = _models().execute_kw(
                settings.odoo_db, uid, settings.odoo_password,
                "product.product", "search_read",
                [[["default_code", "!=", False]]],
                {
                    "fields": ["default_code", "name", "list_price",
                               "free_qty", "qty_available"],
                    "limit": lote, "offset": offset, "order": "id asc",
                },
            )
            for p in productos:
                sku = (p.get("default_code") or "").strip()
                if not sku:
                    continue
                # `free_qty` puede venir 0 legítimamente (todo comprometido), así
                # que NO se puede usar `or`: solo se cae a `qty_available` cuando
                # el campo NO existe / viene nulo.
                libre = p.get("free_qty")
                if libre is None or libre is False:
                    libre = p.get("qty_available")
                salida.append({
                    "sku": sku,
                    "nombre": p.get("name") or sku,
                    "precio": p.get("list_price"),
                    "stock": libre,
                    "stock_fisico": p.get("qty_available"),   # informativo
                })
            if len(productos) < lote:
                break
            offset += lote
    except Exception as exc:  # noqa: BLE001
        log.warning("Odoo listar_catalogo falló (offset %d): %s", offset, exc)
    return salida


def imagenes_por_sku(skus: list[str]) -> dict[str, str]:
    """
    Devuelve { sku: image_1024 en base64 } para los SKUs dados. Se consulta en
    lotes chicos porque cada imagen pesa ~100-500 KB. Los productos sin imagen
    en Odoo simplemente no aparecen en el resultado.
    """
    uid = _uid()
    if not uid or not skus:
        return {}
    salida: dict[str, str] = {}
    LOTE = 10
    for i in range(0, len(skus), LOTE):
        chunk = skus[i:i + LOTE]
        try:
            productos = _models().execute_kw(
                settings.odoo_db, uid, settings.odoo_password,
                "product.product", "search_read",
                [[["default_code", "in", chunk]]],
                {"fields": ["default_code", "image_1024"]},
            )
            for p in productos:
                sku = (p.get("default_code") or "").strip()
                img = p.get("image_1024")
                if sku and img:  # image_1024 es False si no hay imagen
                    salida[sku] = img
        except Exception as exc:  # noqa: BLE001
            log.warning("Odoo imagenes_por_sku (lote %d) falló: %s", i // LOTE + 1, exc)
    return salida


def imagenes_1920_por_sku(skus: list[str]) -> dict[str, bytes]:
    """
    ``{ sku: bytes de image_1920 }`` — la foto de Odoo YA decodificada.

    Existe aparte de :func:`imagenes_por_sku` por una razón que no es cosmética:
    el primer peldaño del empate contra el packing list es un **sha256 del
    archivo**, y la foto de Odoo y la del packing list son el mismo archivo en
    el 92% de los casos medidos. Un sha256 solo empata a resolución IDÉNTICA:
    pedir ``image_1024`` (que es lo que devuelve la gemela, pensada para
    enseñarla en pantalla) degrada ese peldaño gratis al de dHash, que sí
    funciona pero exige margen y falla más.

    Se decodifica aquí porque quien la usa la quiere en bytes para hashear, no
    en base64 para pintar.
    """
    uid = _uid()
    if not uid or not skus:
        return {}
    import base64

    salida: dict[str, bytes] = {}
    LOTE = 10          # cada imagen pesa 100-500 KB: lotes chicos o Odoo se ahoga
    for i in range(0, len(skus), LOTE):
        chunk = [s for s in skus[i:i + LOTE] if s]
        if not chunk:
            continue
        try:
            productos = _models().execute_kw(
                settings.odoo_db, uid, settings.odoo_password,
                "product.product", "search_read",
                [[["default_code", "in", chunk]]],
                {"fields": ["default_code", "name", "image_1920"]},
            )
            for p in productos:
                sku = (p.get("default_code") or "").strip()
                img = p.get("image_1920")
                if sku and img:      # image_1920 viene False cuando no hay foto
                    try:
                        salida[sku.upper()] = base64.b64decode(img)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as exc:  # noqa: BLE001
            log.warning("Odoo imagenes_1920_por_sku (lote %d) falló: %s",
                        i // LOTE + 1, exc)
    return salida


def skus_con_imagen(skus: list[str]) -> set[str]:
    """
    Qué SKUs de la lista TIENEN foto en Odoo, sin traerse las fotos.

    Es para el pronóstico que se enseña antes de arrancar un lote: ~18% del
    catálogo publicado no tiene foto en Odoo y esos caen directo al peldaño
    caro. Filtrar por ``image_1920 != False`` en el dominio deja el trabajo del
    lado de Odoo; pedir el campo y contar los vacíos serían megabytes por una
    respuesta de sí/no.

    Si Odoo no admite el filtro sobre el binario, devuelve vacío y se registra:
    un pronóstico incompleto es molesto, tumbar la pantalla no.
    """
    uid = _uid()
    if not uid or not skus:
        return set()
    limpios = [s for s in skus if s]
    salida: set[str] = set()
    LOTE = 400
    for i in range(0, len(limpios), LOTE):
        chunk = limpios[i:i + LOTE]
        try:
            filas = _models().execute_kw(
                settings.odoo_db, uid, settings.odoo_password,
                "product.product", "search_read",
                [[["default_code", "in", chunk], ["image_1920", "!=", False]]],
                {"fields": ["default_code"]},
            )
            salida.update((f.get("default_code") or "").strip().upper()
                          for f in filas if f.get("default_code"))
        except Exception as exc:  # noqa: BLE001
            log.warning("Odoo skus_con_imagen falló (lote %d): %s", i // LOTE + 1, exc)
    return salida


def contenedores_por_sku(skus: list[str] | None = None) -> dict[str, str]:
    """
    ``{ SKU EN MAYÚSCULAS: container_numbers }`` de ``product.product``.

    Es lo ÚNICO que se le pide a Odoo sobre embarques, y a propósito: la regla
    de la casa es que de Odoo se toman la foto y a qué contenedor pertenece el
    SKU — sus números no se usan para nada (58% se autocontradicen).

    El campo es texto libre y está sucísimo (``PCIU9532241=CI&PL contenedor
    56``, ``256059868 TRHU6215242 contenedor 1``, con NBSP de por medio): aquí
    se devuelve CRUDO y quien lo consume extrae los códigos con regex — ver
    ``packing_drive_carpeta.codigos_de``. Parsear el campo entero es perder el
    tiempo: 201 de sus 350 valores distintos no caen en ningún patrón limpio.

    Sin ``skus`` trae el catálogo completo (13k productos, ~2 s): es lo que
    conviene cuando además hay que EXPANDIR padres a variantes, porque eso
    necesita el diccionario entero de ``default_code``.
    """
    uid = _uid()
    if not uid:
        return {}
    dominio: list[Any] = [["default_code", "!=", False]]
    if skus:
        dominio = [["default_code", "in", [s for s in skus if s]]]
    try:
        filas = _models().execute_kw(
            settings.odoo_db, uid, settings.odoo_password,
            "product.product", "search_read", [dominio],
            {"fields": ["default_code", "container_numbers"]},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Odoo contenedores_por_sku falló: %s", exc)
        return {}
    salida: dict[str, str] = {}
    for r in filas:
        sku = (r.get("default_code") or "").strip().upper()
        if not sku:
            continue
        # El SKU va aunque no tenga contenedor: el diccionario también sirve
        # para saber si un SKU EXISTE en Odoo (y por tanto si es hoja o padre).
        salida[sku] = (r.get("container_numbers") or "").strip()
    return salida


def ping() -> bool:
    return _uid() is not None


# ─────────────────────────────────────────────────────────────────────────────
# INVENTARIO MAESTRO (pestaña Inventario, v0.372.0)
#
# Lo de aquí abajo es lo que la pestaña necesita de Odoo y que ningún otro
# módulo pedía: el detalle de existencias por SKU y el LIBRO DE MOVIMIENTOS.
#
# Tres cosas medidas el 2-sep-2026 que explican por qué está escrito así:
#
# 1. `stock.move.product_qty` es lo PEDIDO; `stock.move.quantity` es lo HECHO.
#    Difieren en 3,470 de 55,956 movimientos (6.2%). Reconstruir el saldo con
#    `product_qty` da 90.8% de aciertos; con `quantity`, 97.1%. Caso real:
#    TEC-2348-MUL, recepción con product_qty=3548 y quantity=496 — sumando lo
#    pedido el libro dice 3,498 y sumando lo hecho dice 446, que es EXACTAMENTE
#    el `qty_available`. Aquí se usa SIEMPRE `quantity`.
#
# 2. `incoming_qty` NO es "mercancía en camino". El 99.7% de las 11,843
#    recepciones abiertas llevan 91-180 días vencidas y NINGUNA está programada
#    a futuro: son recepciones de mayo-junio que nadie validó. Por eso
#    `detalle_por_sku` devuelve además la FECHA de la recepción más vieja, para
#    que la pestaña pueda decir "abierta desde el 13-may" en vez de mentir con
#    un "llegando".
#
# 3. `free_qty = qty_available − reservado_en_ubicaciones_internas`, exacto a la
#    pieza sobre 26,999 productos. `reserved_quantity` NO es campo de
#    product.product (vive en stock.quant), así que no se pide aquí.
# ─────────────────────────────────────────────────────────────────────────────

# Cómo se traduce un movimiento de Odoo a una causa que un humano entiende.
# El `usage` de la ubicación es lo que manda: Odoo no tiene un campo "tipo de
# movimiento", tiene de dónde sale y a dónde entra.
_USOS_EXTERNOS = {"supplier", "customer", "inventory", "production", "transit"}


def detalle_por_sku(skus: list[str]) -> dict[str, dict[str, Any]]:
    """
    Existencias COMPLETAS por SKU: lo físico, lo libre, lo comprometido y lo que
    hay en recepciones abiertas — con la fecha de la más vieja.

    ``{ SKU: {odoo_id, nombre, fisico, libre, reservado, entrante, saliente,
              contenedor, recepcion_desde, recepcion_ref, activo} }``

    `reservado` se deriva (`fisico - libre`) en vez de pedirse: es la identidad
    que se verificó exacta sobre el catálogo entero, y evita una segunda vuelta
    a `stock.quant` por cada SKU de la tabla.
    """
    limpios = [s for s in {(x or "").strip() for x in skus} if s]
    if not limpios:
        return {}
    uid = _uid()
    if not uid:
        return {}
    salida: dict[str, dict[str, Any]] = {}
    LOTE = 200
    for i in range(0, len(limpios), LOTE):
        chunk = limpios[i:i + LOTE]
        try:
            filas = _models().execute_kw(
                settings.odoo_db, uid, settings.odoo_password,
                "product.product", "search_read",
                [[["default_code", "in", chunk]]],
                {
                    "fields": ["default_code", "name", "qty_available", "free_qty",
                               "incoming_qty", "outgoing_qty", "container_numbers",
                               "active"],
                    # active_test:False para VER los archivados: 13,831 productos
                    # archivados guardan 22,262 piezas reales en racks. Ocultarlos
                    # haría que la pestaña dijera "no existe" de mercancía que sí
                    # está. Se devuelven marcados con `activo=False`.
                    "context": {"active_test": False},
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Odoo detalle_por_sku falló (lote %d): %s", i // LOTE + 1, exc)
            continue
        for r in filas:
            sku = (r.get("default_code") or "").strip()
            if not sku:
                continue
            fisico = float(r.get("qty_available") or 0)
            libre = r.get("free_qty")
            libre = fisico if libre is None or libre is False else float(libre)
            previo = salida.get(sku)
            nuevo = {
                "odoo_id": r.get("id"),
                "nombre": r.get("name") or "",
                "fisico": fisico,
                "libre": libre,
                "reservado": max(0.0, fisico - libre),
                "entrante": float(r.get("incoming_qty") or 0),
                "saliente": float(r.get("outgoing_qty") or 0),
                "contenedor": (r.get("container_numbers") or "").strip(),
                "activo": bool(r.get("active")),
                "recepcion_desde": None,
                "recepcion_ref": None,
                "duplicado": False,
            }
            # 5 SKUs tienen DOS productos ACTIVOS en Odoo (TEC-2241-MET,
            # DEC-0010-EST, HERR-0129-EST, VAR-0508-PLA, VEH-0126-MET) y
            # `listar_catalogo` se queda callado con el último. Aquí gana el
            # ACTIVO con más existencia física, y se MARCA — un empate silencioso
            # es cómo se tiran 19 piezas sin que nadie se entere.
            if previo is not None:
                nuevo["duplicado"] = True
                mejor = max((previo, nuevo),
                            key=lambda d: (d["activo"], d["fisico"], d["libre"]))
                mejor["duplicado"] = True
                salida[sku] = mejor
            else:
                salida[sku] = nuevo
    _anotar_recepciones(salida)
    return salida


def _anotar_recepciones(detalle: dict[str, dict[str, Any]]) -> None:
    """
    Rellena `recepcion_desde` / `recepcion_ref` de los SKUs con `entrante > 0`.

    Existe porque `incoming_qty` solo es un número, y el número miente: dice
    "20 piezas entrando" de una recepción del 26 de mayo que nadie validó. Con
    la fecha, la pestaña puede pintar "recepción abierta desde hace 99 días",
    que es la verdad y además es accionable.
    """
    pendientes = [s for s, d in detalle.items() if d["entrante"] > 0]
    if not pendientes:
        return
    uid = _uid()
    if not uid:
        return
    LOTE = 200
    for i in range(0, len(pendientes), LOTE):
        chunk = pendientes[i:i + LOTE]
        try:
            movs = _models().execute_kw(
                settings.odoo_db, uid, settings.odoo_password,
                "stock.move", "search_read",
                [[["product_id.default_code", "in", chunk],
                  ["state", "not in", ["done", "cancel", "draft"]],
                  ["location_dest_id.usage", "=", "internal"]]],
                {"fields": ["product_id", "date", "reference", "origin", "state"],
                 "order": "date asc"},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Odoo _anotar_recepciones falló (lote %d): %s",
                        i // LOTE + 1, exc)
            continue
        for m in movs:
            prod = m.get("product_id") or []
            etiqueta = prod[1] if len(prod) > 1 else ""
            # product_id viene como [id, "[SKU] Nombre"]; el SKU va entre corchetes.
            sku = etiqueta.split("]")[0].lstrip("[").strip() if "[" in etiqueta else ""
            d = detalle.get(sku)
            if d is None or d["recepcion_desde"]:
                continue          # ya tiene la más vieja: el order es date asc
            d["recepcion_desde"] = m.get("date")
            d["recepcion_ref"] = m.get("reference") or m.get("origin") or ""


def movimientos_por_sku(sku: str, limite: int = 400) -> list[dict[str, Any]]:
    """
    EL LIBRO DE BODEGA de un SKU: entradas, ventas, devoluciones, ajustes,
    traspasos, mermas y envíos a FULL/FBA, del más reciente al más viejo.

    Es la única fuente real de movimiento que existe en la casa. Medido: 55,956
    movimientos `done` desde el 15-dic-2025, y reconstruir el saldo con ellos
    reproduce el `qty_available` de Odoo en el 97.5% de los productos activos.
    `channel.listing_history` NO sirve para esto (8 SKUs rotos generan el 87%
    de su "movimiento" en Mercado Libre) y `ops.fanout_log` no guarda cantidades
    en las acciones que mueven inventario.

    Cada renglón trae `delta` FIRMADO (+ entra a bodega, − sale) y su `causa`.
    """
    sku = (sku or "").strip()
    if not sku:
        return []
    uid = _uid()
    if not uid:
        return []
    try:
        movs = _models().execute_kw(
            settings.odoo_db, uid, settings.odoo_password,
            "stock.move", "search_read",
            [[["product_id.default_code", "=", sku], ["state", "=", "done"]]],
            {
                # `quantity` es lo HECHO; `product_qty` es lo pedido y difiere en
                # el 6.2% de los movimientos. Ver la nota de cabecera de bloque.
                "fields": ["date", "quantity", "product_qty", "reference", "origin",
                           "location_id", "location_dest_id", "picking_id",
                           "is_inventory", "scrapped", "create_uid"],
                "order": "date desc, id desc",
                "limit": max(1, min(limite, 2000)),
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Odoo movimientos_por_sku(%s) falló: %s", sku, exc)
        return []

    ubis = {u for m in movs
            for u in (_id_de(m.get("location_id")), _id_de(m.get("location_dest_id")))
            if u}
    ubic = _ubicaciones(sorted(ubis))
    # A qué canal salió cada envío. Un envío a FULL/FBA es INDISTINGUIBLE de una
    # venta mirando el movimiento: los dos terminan en `Customers`. Lo único que
    # los separa es el `partner_id` del picking (medido: FULL 2,494 movimientos,
    # AMAZON 141, shein 886, tiktokshop 162, temu 41). Por eso esta segunda
    # consulta existe, y por eso NO se puede clasificar por ubicación.
    socios = _socios_de_picking(
        sorted({p for m in movs if (p := _id_de(m.get("picking_id")))}))

    salida: list[dict[str, Any]] = []
    for m in movs:
        org, dst = m.get("location_id"), m.get("location_dest_id")
        u_org = ubic.get(_id_de(org), ("", ""))
        u_dst = ubic.get(_id_de(dst), ("", ""))
        entra, sale = _vendible(u_dst), _vendible(u_org)
        if entra == sale:
            # vendible→vendible, o fuera→fuera: el saldo vendible no cambia.
            delta = 0.0
        else:
            delta = float(m.get("quantity") or 0) * (1 if entra else -1)
        socio = socios.get(_id_de(m.get("picking_id")), "")
        salida.append({
            "fecha": m.get("date"),
            "delta": delta,
            "cantidad": float(m.get("quantity") or 0),
            "pedido": float(m.get("product_qty") or 0),
            "causa": _causa(m, u_org, u_dst, entra, sale, socio),
            "documento": m.get("reference") or "",
            "referencia": m.get("origin") or "",
            "contraparte": socio,
            "origen": _nombre_de(org),
            "destino": _nombre_de(dst),
            "almacen_origen": u_org[1],
            "almacen_destino": u_dst[1],
            "quien": _nombre_de(m.get("create_uid")),
        })
    return salida


def _id_de(campo: Any) -> int | None:
    """Odoo devuelve los many2one como [id, nombre]; a veces False."""
    return campo[0] if isinstance(campo, (list, tuple)) and campo else None


def _nombre_de(campo: Any) -> str:
    return campo[1] if isinstance(campo, (list, tuple)) and len(campo) > 1 else ""


@lru_cache(maxsize=8)
def _ubicaciones_cache(clave: tuple[int, ...]) -> dict[int, tuple[str, str]]:
    """``{ id: (usage, nombre del almacén) }``. Cacheado: las ubicaciones no
    cambian, y la misma docena se repite en todos los historiales."""
    uid = _uid()
    if not uid or not clave:
        return {}
    try:
        filas = _models().execute_kw(
            settings.odoo_db, uid, settings.odoo_password,
            "stock.location", "read", [list(clave)],
            {"fields": ["usage", "warehouse_id"]},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Odoo _ubicaciones falló: %s", exc)
        return {}
    return {f["id"]: (f.get("usage") or "", _nombre_de(f.get("warehouse_id")))
            for f in filas}


def _ubicaciones(ids: list[int]) -> dict[int, tuple[str, str]]:
    return _ubicaciones_cache(tuple(ids))


def _socios_de_picking(ids: list[int]) -> dict[int, str]:
    """``{ picking_id: nombre del socio }`` — quién está del otro lado."""
    if not ids:
        return {}
    uid = _uid()
    if not uid:
        return {}
    salida: dict[int, str] = {}
    for i in range(0, len(ids), 300):
        try:
            filas = _models().execute_kw(
                settings.odoo_db, uid, settings.odoo_password,
                "stock.picking", "read", [ids[i:i + 300]], {"fields": ["partner_id"]},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Odoo _socios_de_picking falló: %s", exc)
            continue
        for f in filas:
            salida[f["id"]] = _nombre_de(f.get("partner_id"))
    return salida


# Contrapartes que NO son un cliente final sino una bodega de marketplace.
_CANALES = ("FULL", "AMAZON", "TIKTOK", "TEMU", "SHEIN", "MERCADO LIBRE", "WALMART")


def _vendible(ubicacion: tuple[str, str]) -> bool:
    """
    ¿Esta ubicación cuenta como stock VENDIBLE?

    No basta con `usage == 'internal'`, y esto costó una tarde de medición.
    `TEXCO/FERRAFORME/SCRAP` y `TEXCO/FERRAFORME/CUARENTENA` son `internal` en
    la topología de Odoo **pero no cuelgan de ningún almacén** (`warehouse_id`
    vacío), y Odoo las EXCLUYE de `qty_available`. En todo el catálogo son
    11,083 piezas — parte del "hueco de 33,732" que nadie había explicado.

    Caso que lo destapó: TEC-0009-PLA tiene 1 pieza en SCRAP y 2 en CUARENTENA;
    contándolas como bodega, el libro daba 6 y Odoo decía 3. La diferencia era
    exactamente esas 3.

    Por eso la regla es *interno Y con almacén*: así el saldo reconstruido
    reproduce el número que el panel publica a los canales.
    """
    return ubicacion[0] == "internal" and bool(ubicacion[1])


def _causa(mov: dict[str, Any], u_org: tuple[str, str], u_dst: tuple[str, str],
           entra: bool, sale: bool, socio: str) -> str:
    """
    Traduce un movimiento de Odoo a una categoría que un humano de bodega
    entiende. Devuelve una de: merma · ajuste · preparacion · traspaso ·
    devolucion · entrada · envio_full · venta · otro.

    El orden importa: `is_inventory` y `scrapped` son banderas que ganan sobre
    la topología, porque un ajuste de conteo también viaja entre ubicaciones.

    La distinción cara es `preparacion` vs `traspaso`. Odoo entrega en tres
    pasos (PICK → PACK → OUT), así que una sola venta deja DOS movimientos
    internos además de la salida: en TEC-0004-BLN son 91 traspasos contra 19
    ventas. Esos pasos no mueven mercancía entre almacenes y son ruido para
    bodega. Un traspaso DE VERDAD (TEXCO → TEX2) sí importa: explica por qué la
    pieza no está donde debería. Se separan por `warehouse_id`, no por el
    nombre de la ubicación.
    """
    if mov.get("scrapped"):
        return "merma"
    if mov.get("is_inventory"):
        return "ajuste"
    if entra and sale:
        return "traspaso" if u_org[1] != u_dst[1] else "preparacion"
    if entra:
        # Una devolución de cliente entra desde `customer`; una compra, desde
        # `supplier`. Las dos suman, pero la bodega las trata distinto.
        return "devolucion" if u_org[0] == "customer" else "entrada"
    if sale:
        # Salir de lo vendible hacia otra ubicación INTERNA solo puede ser
        # cuarentena o scrap (ver `_vendible`): la pieza sigue en el edificio
        # pero ya no se puede vender, y bodega necesita distinguirlo de una
        # venta o el conteo físico nunca le va a cuadrar.
        if u_dst[0] == "internal":
            return "cuarentena"
        socio_may = (socio or "").upper()
        if any(t in socio_may for t in _CANALES):
            return "envio_full"
        return "venta"
    return "otro"
