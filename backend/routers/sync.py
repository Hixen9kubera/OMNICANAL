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

  POST /api/sync/precios-venta?cuenta=&limite=
  GET  /api/sync/precios-venta
       → Barrido que CONFIRMA el precio de venta de las activas de ML
         (`/items/{id}/sale_price` → `channel.listings.price_sale`). El GET es
         lectura pura. Ver services/precios_venta.py para el porqué y la
         cadencia; el scheduler ya lo corre solo si PRECIOS_VENTA_BARRIDO=true.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from config import settings
from core.marketplaces import Canal, subcuentas
from services import (alertas, channel_read, db, inventario, lecturas_fuente,
                      sync_woo, woocommerce)

router = APIRouter(prefix="/api/sync", tags=["sincronizacion"])


@router.post("/catalogo")
async def refrescar_catalogo():
    """
    Fuerza el refresco de los índices de WooCommerce (catálogo + drafts) leyendo
    en vivo de la DB de WordPress. Lo llama el frontend al abrir la app para que
    los drafts nuevos aparezcan al instante sin esperar el TTL. No bloquea: el
    refresco corre en segundo plano.
    """
    import asyncio
    asyncio.create_task(woocommerce.indice_catalogo(refrescar=True))
    asyncio.create_task(woocommerce.indice_candidatos(refrescar=True))
    return {"ok": True, "mensaje": "Refresco de catálogo y drafts iniciado."}


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


@router.post("/precios-venta")
async def precios_venta_barrer(
    cuenta: str | None = Query(None, description="Cuenta ML (BEKURA/SANCORFASHION)"),
    limite: int | None = Query(None, ge=1, le=2000,
                               description="Las N más rancias. Sin límite = pasada completa."),
):
    """
    Dispara el BARRIDO de precios de venta de ML y contesta de inmediato.

    Es el mismo trabajo que hace el scheduler (`precios_venta.barrido_periodico`)
    pero a mano: sirve para drenar el atraso sin esperar al ciclo, o para barrer
    una sola cuenta. NO espera a que termine — son cientos de llamadas a ML y la
    petición se pasaría del timeout del proxy. El avance se lee con el GET.

    Escribe `channel.listings.price_sale` y habla con ML, así que obedece las
    mismas dos llaves que el job: `SYNC_ENABLED` y `PRECIOS_VENTA_BARRIDO`.
    Con cualquiera apagada no hace nada y lo dice.
    """
    from services import precios_venta
    if not settings.sync_enabled:
        return {"ok": False, "motivo": "SYNC_ENABLED apagado (modo puros pedidos)"}
    if not settings.precios_venta_barrido:
        return {"ok": False, "motivo": "PRECIOS_VENTA_BARRIDO apagado"}
    estado_actual = await precios_venta.refrescar_en_fondo(
        cuenta=cuenta, limite=limite, motivo="manual")
    return {"ok": True, "estado": estado_actual}


@router.get("/precios-venta")
def precios_venta_estado():
    """Avance del barrido de precios de venta. Lectura pura, no dispara nada."""
    from services import precios_venta
    return {
        "encendido": bool(settings.sync_enabled and settings.precios_venta_barrido),
        "sync_enabled": settings.sync_enabled,
        "barrido": settings.precios_venta_barrido,
        "por_hora": settings.precios_venta_por_hora,
        "pasada_al_arrancar": settings.precios_venta_arranque,
        "estado": precios_venta.estado(),
    }


@router.get("/plan")
def plan(limite: int = Query(200, ge=1, le=2000)):
    """Plan de sincronización en modo simulación (no escribe nada)."""
    return inventario.plan_dry_run(limite)


@router.get("/estado")
def estado():
    import logging
    # PASO 3 (12-ago-2026): sin fallback — `canal_inventario` congelada desde
    # el 11-ago; su resumen diría "última actualización: ayer" para siempre.
    if settings.supabase_read_channel:
        por_canal = channel_read.resumen_por_canal()
        lecturas_fuente.anotar("channel", "kubera")
        return {"resumen": por_canal}
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
