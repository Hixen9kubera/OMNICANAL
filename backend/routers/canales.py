"""
canales.py — Registro de canales y refresco contra la API en vivo.

  GET  /api/canales
       → config de las pestañas (id, label, color, habilitado, totales).

  POST /api/canales/{canal}/refrescar/{sku}
       → refresca en vivo precio/stock/FULL/categoría de ese SKU contra la API
         del marketplace (Mercado Libre o Amazon) — el "botón refrescar".
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from core.marketplaces import Canal, lista_canales, subcuentas
from models.schemas import CanalInfo, SubCuentaInfo
from services import amazon, meli, woocommerce

log = logging.getLogger("omnicanal.routers.canales")
router = APIRouter(prefix="/api/canales", tags=["canales"])


@router.get("", response_model=list[CanalInfo])
async def listar_canales(incluir_totales: bool = True):
    salida: list[CanalInfo] = []
    # Totales (best-effort)
    total_general = None
    if incluir_totales:
        try:
            _, total_general, _ = await woocommerce.listar_productos(page=1, per_page=1)
        except Exception:  # noqa: BLE001
            total_general = None

    for cfg in lista_canales():
        total = None
        subs: list[SubCuentaInfo] = []
        if cfg["id"] == Canal.GENERAL.value:
            total = total_general
        elif cfg["id"] == Canal.MERCADO_LIBRE.value:
            total = meli.contar_publicados() if incluir_totales else None
            for s in subcuentas(cfg["id"]):
                subs.append(SubCuentaInfo(
                    **s,
                    total_productos=(meli.contar_publicados(s["id"]) if incluir_totales else None),
                ))
        elif cfg["id"] == Canal.AMAZON.value:
            total = amazon.contar_publicados() if incluir_totales else None
        salida.append(CanalInfo(**cfg, total_productos=total, subcuentas=subs))
    return salida


@router.post("/{canal}/refrescar/{sku}")
async def refrescar(canal: str, sku: str, cuenta: str | None = None):
    """
    Refresca un SKU en vivo contra TODOS los canales y devuelve el inventario
    actualizado. Tolerante a fallos (un canal caído no rompe la respuesta).
    """
    from services import inventario
    res = await inventario.sincronizar_sku(sku)
    inv = inventario.leer_inventario([sku]).get(sku, {})
    return {"sku": sku, "ok": res.get("ok", True),
            "actualizados": res.get("actualizados", 0),
            "inventario": {k: dict(v) for k, v in inv.items()}}
