"""
ejemplos.py — Datos de muestra para los marketplaces sin credenciales todavía
(TikTok Shop, Walmart, Temu, Shein).

Para que las pestañas se vean realistas y listas para conectar, derivamos los
items de productos REALES del cache (tabla `productos`) y les aplicamos precio /
stock / categoría sintéticos por canal de forma determinista (sin azar, para que
sea reproducible). Cuando lleguen las credenciales, basta con reemplazar este
servicio por el cliente real del canal.
"""
from __future__ import annotations

import logging
from typing import Any

from services import db

log = logging.getLogger("omnicanal.ejemplos")

# Multiplicador de precio por canal (estrategia típica por comisiones del canal)
_FACTOR_PRECIO = {
    "tiktok": 1.05,
    "walmart": 1.08,
    "temu": 0.95,
    "shein": 0.98,
}

_CATEGORIA_DEMO = {
    "tiktok": ["Inicio", "Hogar y Herramientas", "Demo TikTok Shop"],
    "walmart": ["Home", "Tools & Home Improvement", "Demo Walmart"],
    "temu": ["Home", "Tools", "Demo Temu"],
    "shein": ["Home & Living", "Tools", "Demo Shein"],
}


def listar(
    canal: str,
    page: int = 1,
    per_page: int = 40,
    search: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Genera items de ejemplo para un canal sin credenciales."""
    offset = (page - 1) * per_page
    like = f"%{search}%" if search else None
    factor = _FACTOR_PRECIO.get(canal, 1.0)
    ruta = [{"id": None, "nombre": n} for n in _CATEGORIA_DEMO.get(canal, ["Demo"])]

    try:
        where = "WHERE (%(search)s IS NULL OR nombre LIKE %(like)s OR sku LIKE %(like)s)"
        rows = db.fetch_all(
            f"""
            SELECT sku, wc_id, odoo_id, nombre, precio, stock_odoo
            FROM productos {where}
            ORDER BY updated_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"limit": per_page, "offset": offset, "search": search, "like": like},
        )
        total = int(db.fetch_scalar(
            f"SELECT COUNT(*) FROM productos {where}",
            {"search": search, "like": like},
        ) or 0)
    except Exception as exc:  # noqa: BLE001
        log.error("Ejemplos %s desde DB falló: %s", canal, exc)
        return [], 0

    items: list[dict[str, Any]] = []
    for i, r in enumerate(rows):
        precio = _f(r.get("precio"))
        precio_canal = round(precio * factor, 2) if precio else None
        # "Publicado" determinista: ~60% de los productos como muestra.
        publicado = (offset + i) % 5 < 3
        items.append({
            "sku": r["sku"],
            "wc_id": r.get("wc_id"),
            "odoo_id": r.get("odoo_id"),
            "nombre": r.get("nombre") or r["sku"],
            "precio": precio_canal,
            "precio_base": precio,
            "stock": r.get("stock_odoo"),
            "estado": "activo (demo)" if publicado else "sin publicar (demo)",
            "categoria_id": None,
            "categoria_path": ruta,
            "publicado": publicado,
            "item_id": f"{canal.upper()[:3]}-{r['sku']}" if publicado else None,
            "url": None,
            "full": (offset + i) % 2 == 0,
            "full_label": "FULL",
            "origen": "ejemplo",
        })
    return items, total


def _f(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
