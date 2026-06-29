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


def ping() -> bool:
    return _uid() is not None
