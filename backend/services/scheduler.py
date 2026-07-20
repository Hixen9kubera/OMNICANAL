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

from datetime import datetime, timedelta

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
    if _scheduler:
        return
    _scheduler = AsyncIOScheduler(timezone="UTC")
    # El sync de inventario (lecturas a ML/Amazon) y el vigilante de Odoo son
    # INDEPENDIENTES: apagar SYNC_ENABLED (modo "puros pedidos de Woo") no debe
    # matar al vigilante, que no habla con Mercado Libre.
    if settings.sync_enabled:
        _scheduler.add_job(
            _job,
            "interval",
            minutes=settings.sync_interval_min,
            id="sync_inventario",
            next_run_time=datetime.now() + timedelta(seconds=30),  # arranca a llenar el cache
            max_instances=1,
            coalesce=True,
        )
    else:
        log.info("Sync de inventario DESACTIVADO (SYNC_ENABLED=false).")
    # Pedidos de AMAZON por sondeo (no hay webhook simple): cada N min trae las
    # órdenes actualizadas y las vuelve pedidos de Woo. Ver pedidos_amazon.py.
    if settings.pedidos_amazon_enabled and settings.mysql_enabled:
        from services import pedidos_amazon
        _scheduler.add_job(
            pedidos_amazon.revisar,
            "interval",
            minutes=settings.pedidos_amazon_min,
            id="pedidos_amazon",
            next_run_time=datetime.now() + timedelta(seconds=60),
            max_instances=1,
            coalesce=True,
        )
        log.info("Sondeo de pedidos Amazon cada %s min.", settings.pedidos_amazon_min)
    # Vigilante de Odoo: detecta cambios de qty_available (foto vs foto) y los
    # avisa en la campana; con auto_push los empuja a Woo. Ver odoo_watch.py.
    if settings.odoo_watch_enabled and settings.mysql_enabled:
        from services import odoo_watch
        _scheduler.add_job(
            odoo_watch.revisar,
            "interval",
            minutes=settings.odoo_watch_min,
            id="odoo_watch",
            next_run_time=datetime.now() + timedelta(seconds=120),
            max_instances=1,
            coalesce=True,
        )
        log.info("Vigilante de Odoo cada %s min (auto_push=%s).",
                 settings.odoo_watch_min, settings.odoo_watch_auto_push)
    _scheduler.start()
    if settings.sync_enabled:
        log.info("Sync programado cada %s min.", settings.sync_interval_min)


def detener() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
