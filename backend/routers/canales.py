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
    """Refresca un SKU contra la API en vivo del marketplace."""
    if canal == Canal.MERCADO_LIBRE.value:
        items, _ = meli.listar(search=sku, per_page=1, cuenta=cuenta)
        if not items or not items[0].get("item_id"):
            raise HTTPException(404, "SKU sin publicación en Mercado Libre")
        data = await meli.refrescar_item(items[0]["item_id"], cuenta)
        if not data:
            raise HTTPException(502, "No se pudo refrescar contra la API de Mercado Libre")
        return {"canal": canal, "sku": sku, **data}

    if canal == Canal.AMAZON.value:
        data = await amazon.refrescar_listing(sku)
        if not data:
            raise HTTPException(502, "No se pudo refrescar contra SP-API de Amazon")
        return {"canal": canal, "sku": sku, **data}

    raise HTTPException(400, f"El canal '{canal}' no soporta refresco en vivo todavía")
