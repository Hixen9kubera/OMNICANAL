"""
fanout.py — Monitoreo y simulación del fan-out de stock DROP.

  GET  /api/fanout/estado           → flags, cola, contadores y últimos eventos.
  GET  /api/fanout/simular?sku=     → QUÉ haría con ese SKU ahora mismo, sin
                                      encolar ni escribir (seguro siempre).
  POST /api/fanout/encolar?sku=     → lo mete a la cola real (respeta dry-run).
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from services import fanout_stock

router = APIRouter(prefix="/api/fanout", tags=["fanout"])


@router.get("/estado")
def estado():
    """Estado del fan-out: flags, pendientes, contadores y bitácora reciente."""
    return fanout_stock.estado()


@router.get("/simular")
def simular(sku: str = Query(..., description="SKU a simular")):
    """
    Plan de fan-out para un SKU: stock DROP leído, objetivo y qué pasaría con
    cada publicación (escribir / sin cambio / omitir, con el motivo).
    NO escribe ni encola: es seguro aunque el fan-out esté encendido.
    """
    return fanout_stock.plan(sku)


@router.post("/encolar")
def encolar(sku: str = Query(..., description="SKU a encolar"),
            motivo: str = Query("manual", description="Origen del encolado")):
    """Encola el SKU en el fan-out real (respeta FANOUT_ENABLED y DRY_RUN)."""
    fanout_stock.encolar(sku, motivo)
    return {"ok": True, "sku": sku,
            "habilitado": fanout_stock.habilitado(),
            "dry_run": fanout_stock.dry_run()}
