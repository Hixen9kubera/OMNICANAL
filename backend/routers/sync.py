"""
sync.py — Endpoints del sistema de sincronización de inventario.

  POST /api/sync/leer?canal=&cuenta=&limite=
       → Lee en vivo de los canales y actualiza el cache canal_inventario.
         (ML por cuenta, Amazon FBA, WooCommerce).

  GET  /api/sync/plan?limite=
       → Modo SIMULACIÓN (dry-run): qué stock_real habría que escribir en cada
         canal para igualarlo al maestro (Odoo). No escribe nada.

  GET  /api/sync/estado
       → Resumen del cache (cuántos SKU por canal, última actualización).
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from core.marketplaces import Canal, subcuentas
from services import db, inventario, sync_woo

router = APIRouter(prefix="/api/sync", tags=["sincronizacion"])


@router.post("/woo")
async def sincronizar_woo(
    limite: int | None = Query(None, ge=1, description="Máx. de SKUs (para pruebas); vacío = todos"),
):
    """
    Lanza en segundo plano la sincronización masiva hacia WooCommerce:
    stock de Odoo (todos los status, nivel variante) + costo de costos_finales
    (meta `costo`). Avance en GET /api/sync/woo/progreso.
    """
    import asyncio
    if sync_woo.progreso().get("estado") == "corriendo":
        return {"ok": False, "motivo": "Ya hay una sincronización corriendo.", "progreso": sync_woo.progreso()}
    asyncio.create_task(sync_woo.sincronizar_stock_y_costos(limite))
    return {"ok": True, "mensaje": "Sincronización de stock y costos iniciada.", "progreso_en": "/api/sync/woo/progreso"}


@router.get("/woo/progreso")
def progreso_woo():
    return sync_woo.progreso()


@router.post("/leer")
async def leer(
    canal: str = Query("todos", description="mercado_libre | amazon | todos"),
    cuenta: str | None = Query(None, description="Cuenta ML (BEKURA/SANCORFASHION)"),
    limite: int = Query(60, ge=1, le=500),
):
    resultados = []
    if canal in ("mercado_libre", "todos"):
        cuentas = [cuenta] if cuenta else [c["id"] for c in subcuentas(Canal.MERCADO_LIBRE.value)]
        for c in cuentas:
            resultados.append(await inventario.sincronizar_ml(c, limite))
    if canal in ("amazon", "todos"):
        resultados.append(await inventario.sincronizar_amazon(limite))
    return {"ok": True, "resultados": resultados}


@router.get("/plan")
def plan(limite: int = Query(200, ge=1, le=2000)):
    """Plan de sincronización en modo simulación (no escribe nada)."""
    return inventario.plan_dry_run(limite)


@router.get("/estado")
def estado():
    inventario.asegurar_schema()
    try:
        por_canal = db.fetch_all(
            """SELECT canal, cuenta, COUNT(*) AS skus,
                      MAX(updated_at) AS ultima_actualizacion,
                      SUM(stock_full) AS total_full, SUM(stock_fba) AS total_fba,
                      SUM(stock_real) AS total_real
               FROM canal_inventario GROUP BY canal, cuenta"""
        )
    except Exception:  # noqa: BLE001
        por_canal = []
    return {"resumen": por_canal}
