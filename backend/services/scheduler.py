"""
scheduler.py — Sincronización programada del inventario (cada N minutos).

Usa APScheduler para correr el LECTOR de inventario en segundo plano dentro del
backend. Lee de ML (ambas cuentas) y Amazon y actualiza el cache canal_inventario.

⚠️ Estrategia de transición: este "polling" cada 15 min es el método inicial.
Cuando se implementen los WEBHOOKS de Mercado Libre y Amazon (ver README),
basta con poner SYNC_ENABLED=false para apagarlo y depender de los webhooks.

En Railway también puede ejecutarse como un servicio Cron aparte que llame a
POST /api/sync/leer, en vez de este scheduler embebido.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from core.marketplaces import Canal, subcuentas
from services import inventario

log = logging.getLogger("omnicanal.scheduler")

_scheduler: AsyncIOScheduler | None = None


async def _job():
    """Corre el lector de inventario para ML (ambas cuentas) y Amazon."""
    log.info("⏱  Sync de inventario iniciado (batch=%s)", settings.sync_batch)
    try:
        for c in subcuentas(Canal.MERCADO_LIBRE.value):
            r = await inventario.sincronizar_ml(c["id"], settings.sync_batch)
            log.info("  ML %s: %s", c["id"], r.get("actualizados"))
        ra = await inventario.sincronizar_amazon(settings.sync_batch)
        log.info("  Amazon: %s", ra.get("actualizados"))
    except Exception as exc:  # noqa: BLE001
        log.error("Sync de inventario falló: %s", exc)


def iniciar() -> None:
    global _scheduler
    if not settings.sync_enabled:
        log.info("Sync programado DESACTIVADO (SYNC_ENABLED=false).")
        return
    if _scheduler:
        return
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        _job,
        "interval",
        minutes=settings.sync_interval_min,
        id="sync_inventario",
        next_run_time=None,  # no corre al instante de arrancar
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    log.info("Sync programado cada %s min.", settings.sync_interval_min)


def detener() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
