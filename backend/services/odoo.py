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
    Se usa para el diff Odoo↔WooCommerce (crear los faltantes como draft).
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
                    "fields": ["default_code", "name", "list_price", "qty_available"],
                    "limit": lote, "offset": offset, "order": "id asc",
                },
            )
            for p in productos:
                sku = (p.get("default_code") or "").strip()
                if not sku:
                    continue
                salida.append({
                    "sku": sku,
                    "nombre": p.get("name") or sku,
                    "precio": p.get("list_price"),
                    "stock": p.get("qty_available"),
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
