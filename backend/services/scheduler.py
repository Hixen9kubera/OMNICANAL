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
    # INGRESOS A FBA (Amazon): sin webhook disponible — la Notifications API de
    # SP-API devuelve 403 (requiere rol extra + cola SQS). Se detectan comparando
    # el inventario FBA contra la última foto: si SUBIÓ, llegó mercancía y esas
    # piezas ya no están en la bodega propia → se restan de Woo. El FULL de ML NO
    # necesita esto: llega por webhook `fbm_stock_operations` en segundos.
    if settings.full_watch_enabled and settings.mysql_enabled:
        from services import stock_full
        _scheduler.add_job(
            stock_full.revisar_fba,
            "interval",
            minutes=settings.full_watch_fba_min,
            id="fba_watch",
            next_run_time=datetime.now() + timedelta(seconds=90),
            max_instances=1,
            coalesce=True,
        )
        log.info("Vigilante de ingresos a FBA cada %s min.", settings.full_watch_fba_min)
    # Pedidos de Temu/TikTok vía M2E (sondeo; ver pedidos_m2e.py).
    if settings.pedidos_m2e_enabled and settings.mysql_enabled and settings.m2e_api_token:
        from services import pedidos_m2e
        _scheduler.add_job(
            pedidos_m2e.revisar,
            "interval",
            minutes=settings.pedidos_m2e_min,
            id="pedidos_m2e",
            next_run_time=datetime.now() + timedelta(seconds=90),
            max_instances=1,
            coalesce=True,
        )
        log.info("Sondeo de pedidos M2E (Temu/TikTok) cada %s min.",
                 settings.pedidos_m2e_min)
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
    # Alta automática de SKUs nuevos de Odoo. Crea en Woo, como DRAFT, los SKUs
    # que existen en Odoo y faltan en la tienda — solo la identidad, sin
    # inventario (candado en woocommerce._borrador_wc). Era el único paso del
    # pipeline que esperaba a que alguien apretara un botón.
    if settings.sync_odoo_skus_enabled:
        from services import creacion

        async def _alta_skus_odoo() -> None:
            r = await creacion.sincronizar_drafts(settings.sync_odoo_skus_limite)
            if not r.get("ok"):
                log.warning("alta de SKUs de Odoo: %s", r.get("motivo"))
                return
            creados, errores = len(r.get("creados") or []), len(r.get("errores") or [])
            if creados or errores:
                log.info("Alta de SKUs de Odoo: %d creado(s), %d error(es), "
                         "quedan %s por crear.", creados, errores,
                         r.get("faltantes_restantes"))

        _scheduler.add_job(
            _alta_skus_odoo,
            "interval",
            minutes=settings.sync_odoo_skus_min,
            id="alta_skus_odoo",
            next_run_time=datetime.now() + timedelta(seconds=240),
            max_instances=1,
            coalesce=True,
        )
        log.info("Alta de SKUs de Odoo cada %s min (lote %s, stock=%s).",
                 settings.sync_odoo_skus_min, settings.sync_odoo_skus_limite,
                 settings.sync_odoo_incluir_stock)

    # Vigilante de inventario: Odoo --(delta)--> Woo --(cualquier cambio)-->
    # canales. Ver services/stock_watch.py. Nace apagado y en solo-registro.
    if getattr(settings, "stock_watch_enabled", False) and settings.mysql_enabled:
        from services import stock_watch
        _scheduler.add_job(
            stock_watch.revisar,
            "interval",
            minutes=settings.stock_watch_min,
            id="stock_watch",
            next_run_time=datetime.now() + timedelta(seconds=180),
            max_instances=1,
            coalesce=True,
        )
        log.info("Vigilante de inventario cada %s min (solo_registro=%s, tope=%s).",
                 settings.stock_watch_min, settings.stock_watch_solo_registro,
                 settings.stock_watch_tope)
    # F2 — Espejo del DROP: stock_watch_foto (Woo) → channel.listings 'general'.
    # Job propio y NO un gancho al final de stock_watch: si el vigilante está
    # apagado o su pasada aborta (Odoo mudo), el DROP del panel debe seguir
    # refrescándose igual. Solo copia lo que Woo ya dice; no mueve inventario.
    if getattr(settings, "drop_mirror_enabled", False) and settings.mysql_enabled:
        from services import channel_mirror
        _scheduler.add_job(
            channel_mirror.sincronizar_drop,
            "interval",
            minutes=settings.drop_mirror_min,
            id="drop_mirror",
            next_run_time=datetime.now() + timedelta(seconds=90),
            max_instances=1,
            coalesce=True,
        )
        log.info("Espejo DROP → channel.listings cada %s min.", settings.drop_mirror_min)
    # Token de TikTok: renovación PROACTIVA (~7 días de vida; renueva si faltan
    # <24 h). La renovación REACTIVA (105002 → refresh → reintento) vive en
    # tiktok.llamar y no depende de este job: esto solo evita que un canal sin
    # tráfico llegue con el token muerto a su siguiente escritura (pasó el
    # 15-ago: 3 días caído en silencio).
    # Reporte FBA: refresco diario por la Reports API (Eduardo, 18-ago). No
    # depende de MySQL — lee de Amazon y escribe ops.fba_snapshot en kubera.
    # Sin credenciales de Amazon el refresco marca error legible y no toca el
    # snapshot, así que en staging es inocuo.
    if settings.fba_refresco_auto:
        from services import fba_reporte
        _scheduler.add_job(
            fba_reporte.refrescar_programado,
            "cron",
            hour=settings.fba_refresco_hora_utc,
            minute=10,
            id="fba_reporte_diario",
            max_instances=1,
            coalesce=True,
        )
        log.info("Refresco diario del reporte FBA a las %02d:10 UTC.",
                 settings.fba_refresco_hora_utc)
    if settings.tiktok_refresh_enabled and settings.mysql_enabled:
        from services import tiktok as _tk
        _scheduler.add_job(
            _tk.refrescar_si_urge,
            "interval",
            minutes=settings.tiktok_refresh_min,
            id="tiktok_token",
            next_run_time=datetime.now() + timedelta(seconds=210),
            max_instances=1,
            coalesce=True,
        )
        log.info("Refresh proactivo de token TikTok cada %s min.",
                 settings.tiktok_refresh_min)
    # Censo de TikTok → channel.listings (status + stock vivos). Sin esto el
    # espejo se congela en el último censo manual y las activaciones de
    # tk_activar.py (escritorio) son invisibles para el fan-out.
    if settings.tiktok_censo_enabled and settings.mysql_enabled:
        from services import tiktok_censo
        _scheduler.add_job(
            tiktok_censo.censar,
            "interval",
            minutes=settings.tiktok_censo_min,
            id="tiktok_censo",
            next_run_time=datetime.now() + timedelta(seconds=300),
            max_instances=1,
            coalesce=True,
        )
        log.info("Censo de TikTok cada %s min.", settings.tiktok_censo_min)
    # Censo de Temu → channel.listings (status crudo + stock vivos). Sin esto,
    # el espejo de Temu se congela en el último `cargar_temu` manual.
    if settings.temu_censo_enabled and settings.mysql_enabled:
        from services import temu_censo
        _scheduler.add_job(
            temu_censo.censar,
            "interval",
            minutes=settings.temu_censo_min,
            id="temu_censo",
            next_run_time=datetime.now() + timedelta(seconds=390),
            max_instances=1,
            coalesce=True,
        )
        log.info("Censo de Temu cada %s min.", settings.temu_censo_min)
    # RECONSTRUCTOR DE PEDIDOS: rellena channel.orders con lo que exista en Woo
    # y le falte al registro. Es el colchón que reemplaza a MySQL — el webhook de
    # ML contesta 200 SIEMPRE (si no, ML deshabilita el topic), así que el canal
    # NO reintenta: si la escritura a kubera falla, nadie más lo va a apuntar.
    # Y un apunte perdido no es cosmético: es el candado de idempotencia, sin el
    # cual el siguiente aviso duplica el pedido. Ver el script para el detalle.
    if getattr(settings, "reconstruir_orders_enabled", False):
        from scripts.reconstruir_orders_desde_woo import reconstruir
        _scheduler.add_job(
            lambda: reconstruir(settings.reconstruir_orders_dias),
            "interval",
            minutes=settings.reconstruir_orders_min,
            id="reconstruir_orders",
            next_run_time=datetime.now() + timedelta(seconds=300),
            max_instances=1,
            coalesce=True,
        )
        log.info("Reconstructor de pedidos Woo→kubera cada %s min (ventana %s días).",
                 settings.reconstruir_orders_min, settings.reconstruir_orders_dias)
    # Vigilante de alertas (Slack): detecta AUSENCIAS — actas de migración
    # faltantes/con deltas, silencio de ventas, tokens rancios. Solo existe si
    # hay SLACK_WEBHOOK_URL; los errores push (espejo, refresh de tokens) no
    # pasan por aquí — avisan solos en el momento. Ver services/alertas.py.
    from services import alertas
    if alertas.disponible():
        _scheduler.add_job(
            alertas.vigilante,
            "interval",
            minutes=settings.alertas_min,
            id="alertas_vigilante",
            next_run_time=datetime.now() + timedelta(seconds=150),
            max_instances=1,
            coalesce=True,
        )
        log.info("Vigilante de alertas (Slack) cada %s min.", settings.alertas_min)
    else:
        log.info("Alertas Slack APAGADAS (sin SLACK_WEBHOOK_URL).")
    _scheduler.start()
    if settings.sync_enabled:
        log.info("Sync programado cada %s min.", settings.sync_interval_min)


def detener() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
