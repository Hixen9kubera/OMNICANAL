"""
studio.py — Metadata completa de un producto para el Estudio (pestaña PRODUCTOS).

Reúne, para un SKU, lo que la ficha del Estudio muestra/edita y que NO viene en
el detalle 360° normal: costo, precio regular/oferta, URL+precio Alibaba,
producto correcto, peso, dimensiones (+ volumen m³), atributos 1×1 y la
categoría de Mercado Libre con TODOS sus subniveles.

Fuentes (inmunes al 403 del hosting):
  1) WordPress postmeta (vía wp_db)  ← FUENTE DE VERDAD (lo que está en el producto)
  2) kubera_ml (costos_finales, categorias_ml) ← fallback si no hay WPDB_*
"""
from __future__ import annotations

import logging
from typing import Any

from services import db

log = logging.getLogger("omnicanal.studio")


def _f(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _volumen(dinero: dict[str, Any]) -> None:
    l, a, h = dinero.get("largo"), dinero.get("ancho"), dinero.get("alto")
    dinero["volumen_m3"] = round((l * a * h) / 1_000_000, 4) if (l and a and h) else None


# ── Fallback: kubera_ml (cuando no hay WPDB_*) ───────────────────────────────
def _dinero_mysql(sku: str) -> dict[str, Any]:
    try:
        cf = db.fetch_one(
            """SELECT costo_unitario, precio_base, precio_sugerido,
                      peso, largo, alto, ancho, ml_cat_id
               FROM costos_finales WHERE sku=%s""",
            (sku,),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("studio._dinero_mysql(%s): %s", sku, exc)
        cf = None
    if not cf:
        return {}
    return {
        "costo": _f(cf.get("costo_unitario")),
        "precio_regular": _f(cf.get("precio_base")),
        "precio_oferta": _f(cf.get("precio_sugerido")),
        "peso": _f(cf.get("peso")),
        "largo": _f(cf.get("largo")),
        "ancho": _f(cf.get("ancho")),
        "alto": _f(cf.get("alto")),
    }


def _categoria_mysql(sku: str) -> dict[str, Any] | None:
    try:
        cat = db.fetch_one(
            """SELECT category_id, category_name, ruta, cat1, cat2, cat3, cat4
               FROM categorias_ml WHERE sku=%s""",
            (sku,),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("studio._categoria_mysql(%s): %s", sku, exc)
        return None
    if not cat:
        return None
    niveles = [
        cat[c] for c in ("cat1", "cat2", "cat3", "cat4")
        if cat.get(c) and str(cat[c]).lower() != "none"
    ]
    hoja = cat.get("category_name")
    if hoja and (not niveles or niveles[-1] != hoja):
        niveles.append(hoja)
    return {
        "category_id": cat.get("category_id"),
        "ruta": cat.get("ruta"),
        "niveles": niveles,
    }


def _resolver_wc_id(sku: str) -> int | None:
    try:
        from services import wp_db
        if wp_db.disponible():
            got = wp_db.productos_por_sku([sku])
            return (got.get(sku) or {}).get("wc_id")
    except Exception:  # noqa: BLE001
        pass
    return None


def metadata(sku: str, wc_id: int | None = None) -> dict[str, Any]:
    """Ensambla la metadata del Estudio para un SKU (postmeta primero)."""
    base: dict[str, Any] = {
        "sku": sku, "wc_id": wc_id, "fuente": None,
        "dinero": {}, "categoria_ml": None,
        "alibaba_url": None, "alibaba_precio": None, "producto_correcto": None,
        "atributos": [],
    }

    # 1) Postmeta de WordPress (fuente de verdad)
    try:
        from services import wp_db
        if wp_db.disponible():
            if not wc_id:
                wc_id = _resolver_wc_id(sku)
                base["wc_id"] = wc_id
            if wc_id:
                m = wp_db.metadata_producto(int(wc_id))
                _volumen(m["dinero"])
                base.update({
                    "fuente": "postmeta",
                    "dinero": m["dinero"],
                    "categoria_ml": m["categoria_ml"],
                    "alibaba_url": m["alibaba_url"],
                    "alibaba_precio": m["alibaba_precio"],
                    "producto_correcto": m["producto_correcto"],
                    "atributos": m["atributos"],
                })
                return base
    except Exception as exc:  # noqa: BLE001
        log.warning("studio.metadata(%s) postmeta: %s", sku, exc)

    # 2) Fallback kubera_ml
    dinero = _dinero_mysql(sku)
    _volumen(dinero)
    base.update({
        "fuente": "kubera_ml",
        "dinero": dinero,
        "categoria_ml": _categoria_mysql(sku),
    })
    return base
