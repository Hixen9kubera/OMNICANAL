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


def ping() -> bool:
    return _uid() is not None
