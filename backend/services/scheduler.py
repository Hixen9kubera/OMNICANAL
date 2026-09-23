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

from datetime import datetime, timedelta, timezone

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


async def _tiktok_diagnostico_arranque() -> None:
    """
    El diagnóstico de TikTok, UNA vez; el estado vivo de TIKTOK_DIAGNOSTICO_IDS
    (sólo lectura) y la recuperación de TIKTOK_RECUPERAR_IDS, si las hay.

    POR QUÉ EN EL ARRANQUE Y NO SÓLO COMO ENDPOINT: el endpoint pide la llave del
    panel, y la pregunta ("¿seguimos suscritos a los pedidos, se perdió alguna
    venta?") tiene que quedar contestada en los logs de Railway aunque nadie
    tenga sesión. TikTok sólo le contesta a la IP de Railway (36009033 desde la
    laptop), así que éste es el único lugar donde se puede preguntar.

    El diagnóstico va PRIMERO y por separado: si la recuperación falla o no se
    pidió, la línea del diagnóstico ya quedó escrita. Nunca lanza — un job de
    arranque que revienta deja una traza de APScheduler que nadie busca.
    """
    from services import tiktok_diagnostico as td

    try:
        d = await td.diagnosticar(settings.tiktok_diagnostico_dias)
        log.info("%s", td.resumen_linea(d))
    except Exception as exc:  # noqa: BLE001
        log.warning("TIKTOK diagnostico al arrancar falló: %s", exc)

    # ESTADO VIVO de ventas nombradas (TIKTOK_DIAGNOSTICO_IDS). Sólo lectura. Va
    # antes de la recuperación y en su propio try: si falla, la recuperación
    # pedida sigue su curso, y al revés.
    ids_diag = (getattr(settings, "tiktok_diagnostico_ids", "") or "").strip()
    if ids_diag:
        try:
            r = await td.estado_ids(ids_diag)
            log.info("%s", td.resumen_ids(r))
        except Exception as exc:  # noqa: BLE001
            log.warning("TIKTOK ids (TIKTOK_DIAGNOSTICO_IDS) falló: %s", exc)

    ids = (getattr(settings, "tiktok_recuperar_ids", "") or "").strip()
    if not ids:
        return
    # SÍ ESCRIBE. Los ids son EXPLÍCITOS (TIKTOK_RECUPERAR_IDS) y `recuperar`
    # lleva los candados del endpoint: tope 25, omite lo que ya dejó huella en
    # la bitácora o en Odoo sin estar en channel.orders, y falla cerrado si no
    # pudo leer los registros. El detalle por id lo loguea
    # `recuperar` mismo; aquí va el veredicto y el recordatorio de vaciar.
    try:
        r = await td.recuperar(ids, aplicar=True)
        if r.get("modo") != "APLICADO":
            log.warning("TIKTOK recuperar (TIKTOK_RECUPERAR_IDS): NO se aplicó ninguno — %s %s",
                        r.get("motivo"), r.get("errores") or "")
            return
        log.warning("TIKTOK recuperar (TIKTOK_RECUPERAR_IDS): %s con éxito, %s sin éxito "
                    "[%s] — VACIAR la variable: cada reinicio la vuelve a procesar.",
                    r.get("con_exito"), r.get("sin_exito"),
                    ", ".join(f"{x.get('id')}:{x.get('accion')}"
                              for x in r.get("resultados") or []))
    except Exception as exc:  # noqa: BLE001
        log.warning("TIKTOK recuperar (TIKTOK_RECUPERAR_IDS) falló: %s", exc)


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
    # VINCULAR ventas que quedaron sin orden y después se crearon aparte en Odoo
    # (18-sep-2026: S38923 para la venta de TikTok con SKU padre). Sólo escribe
    # la bitácora del tab, nunca Odoo. Ver odoo_ventas_log.vincular_sin_orden.
    if getattr(settings, "odoo_ventas_vincular_enabled", False):
        from services import odoo_ventas_log as _ovl

        async def _vincular_sin_orden() -> None:
            import asyncio as _aio
            for _canal in ("tiktok", "temu"):
                try:
                    await _aio.to_thread(_ovl.vincular_sin_orden, _canal)
                except Exception as exc:  # noqa: BLE001 — nunca tumba al scheduler
                    log.warning("vincular_sin_orden(%s) falló: %s", _canal, exc)

        _min_vinc = max(5, int(settings.odoo_ventas_vincular_min))
        _scheduler.add_job(
            _vincular_sin_orden,
            "interval",
            minutes=_min_vinc,
            id="odoo_ventas_vincular",
            next_run_time=datetime.now(timezone.utc) + timedelta(seconds=150),
            max_instances=1,
            coalesce=True,
        )
        log.info("Vincular ventas sin orden con Odoo cada %s min (tiktok, temu).", _min_vinc)
    # NOTA DE ENVÍO COMBINADO en Odoo (23-sep-2026). Cuando dos o más órdenes
    # de venta comparten la guía de su entrega de salida, viajan en UNA caja:
    # cada una se entera de con cuáles va, y de que la etiqueta se imprime una
    # sola vez. ⚠️ A DIFERENCIA del job de arriba, ESTE SÍ ESCRIBE EN ODOO —
    # por eso obedece también el interruptor general y el del canal, que se
    # leen dentro del hilo (regla 11), y por eso su bandera NACE APAGADA: sin
    # encenderla a mano en Railway el job ni siquiera se registra, que es lo
    # que se quiere hasta que pase el canario y esté el dale de Brandon.
    # Ver services/odoo_notas_combinado.py y config.py.
    if getattr(settings, "odoo_ventas_notas_combinado_enabled", False):
        from services import odoo_notas_combinado as _onc

        async def _notas_combinado() -> None:
            import asyncio as _aio
            for _canal in ("tiktok", "temu"):
                try:
                    await _aio.to_thread(
                        _onc.notar_combinados, _canal,
                        settings.odoo_ventas_notas_combinado_dias,
                        settings.odoo_ventas_notas_combinado_limite)
                except Exception as exc:  # noqa: BLE001 — nunca tumba al scheduler
                    log.warning("notar_combinados(%s) falló: %s", _canal, exc)

        _min_notas = max(5, int(settings.odoo_ventas_notas_combinado_min))
        _scheduler.add_job(
            _notas_combinado,
            "interval",
            minutes=_min_notas,
            id="odoo_notas_combinado",
            # 210 s: después del vinculador (150 s) para no abrir dos
            # conversaciones con Odoo en el mismo segundo del arranque.
            next_run_time=datetime.now(timezone.utc) + timedelta(seconds=210),
            max_instances=1,
            coalesce=True,
        )
        log.info("Nota de envío combinado en Odoo cada %s min (tiktok, temu).",
                 _min_notas)
    # Refresco de guías de Temu. La guía NO existe cuando nace la orden: la
    # asigna la paquetería cuando se compra el envío. Y como Temu no manda
    # avisos, sin este trabajo la entrega se queda sin rastreo para siempre.
    #
    # A HORAS FIJAS desde las 00:00 de México (Brandon, 11-sep): 00:00, 02:00,
    # 04:00… Antes era "cada 120 min desde el despliegue", o sea a horas que
    # dependían de cuándo alguien subió código.
    if settings.temu_guias_enabled:
        from apscheduler.triggers.cron import CronTrigger
        from services import pedidos_temu
        horas = max(1, int(settings.temu_guias_cada_horas))
        async def _guias_temu():
            await pedidos_temu.refrescar_guias(
                dias=settings.temu_guias_dias,
                limite=settings.temu_guias_limite,
                # Techo por vuelta: 80% del intervalo, para que una corrida
                # lenta nunca pise a la siguiente ni la deje sin turno.
                segundos_max=int(horas * 3600 * 0.8),
            )
        # Si la imagen no trae la base de zonas horarias se cae a UTC: México es
        # UTC-6 FIJO desde 2022, así que con intervalos pares las horas cuadran.
        try:
            disparo = CronTrigger(hour=f"0-23/{horas}", minute=0,
                                  timezone="America/Mexico_City")
            zona = "America/Mexico_City"
        except Exception as exc:  # noqa: BLE001
            log.warning("temu_guias: sin zona America/Mexico_City (%s); se usa UTC", exc)
            disparo = CronTrigger(hour=f"0-23/{horas}", minute=0, timezone="UTC")
            zona = "UTC"
        _scheduler.add_job(
            _guias_temu,
            disparo,
            id="temu_guias",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=900,
        )
        log.info("Refresco de guías de Temu cada %s h desde las 00:00 (%s), "
                 "tope %s días, %s por vuelta.", horas, zona,
                 settings.temu_guias_dias, settings.temu_guias_limite)

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
    # Sondeo de ventas de TEMU por su propia API. Reemplaza a M2E, que quedó
    # desinstalado y prohibido: sin esto Temu no tiene NINGUNA vía de ingesta y
    # sus ventas solo entran si alguien las captura a mano.
    # Ver services/pedidos_temu_sondeo.py — arranca en modo solo-registro.
    if getattr(settings, "pedidos_temu_sondeo_enabled", False):
        from services import pedidos_temu_sondeo
        async def _sondeo_temu():
            # Varias páginas: la lista de Temu no viene ordenada por fecha y con
            # una sola se perdían ventas del mismo día. Ver config.
            await pedidos_temu_sondeo.revisar(
                paginas=settings.pedidos_temu_sondeo_paginas)
        _scheduler.add_job(
            _sondeo_temu,
            "interval",
            minutes=settings.pedidos_temu_sondeo_min,
            id="pedidos_temu_sondeo",
            next_run_time=datetime.now() + timedelta(seconds=120),
            max_instances=1,
            coalesce=True,
        )
        log.info("Sondeo de ventas Temu cada %s min, %s páginas (solo_registro=%s).",
                 settings.pedidos_temu_sondeo_min,
                 settings.pedidos_temu_sondeo_paginas,
                 settings.pedidos_temu_sondeo_solo_registro)
    # ── RESPALDOS DEL AVISO DE PEDIDOS DE TIKTOK (14-sep-2026) ─────────────────
    # Los tres nacen APAGADOS (regla 3) y cada uno cuelga de su propia bandera:
    # con la bandera apagada el job NI SE REGISTRA. Ver config.py.
    #
    # Sondeo: ventana fija por update_time; procesa lo que el aviso no trajo y
    # los cambios de estado. Ver services/pedidos_tiktok_sondeo.py.
    if getattr(settings, "pedidos_tiktok_sondeo_enabled", False):
        from services import pedidos_tiktok_sondeo
        _scheduler.add_job(
            pedidos_tiktok_sondeo.revisar,
            "interval",
            minutes=max(1, int(settings.pedidos_tiktok_sondeo_min)),
            id="pedidos_tiktok_sondeo",
            next_run_time=datetime.now() + timedelta(seconds=270),
            max_instances=1,
            coalesce=True,
        )
        log.info("Sondeo de ventas TikTok cada %s min, ventana %s días "
                 "(solo_registro=%s).", settings.pedidos_tiktok_sondeo_min,
                 settings.pedidos_tiktok_sondeo_dias,
                 settings.pedidos_tiktok_sondeo_solo_registro)
    # Reintentos de los avisos de TikTok que fallaron al procesarse.
    if getattr(settings, "tiktok_webhook_reintentos_enabled", False):
        from services import tiktok_webhook_reintentos
        _scheduler.add_job(
            tiktok_webhook_reintentos.reprocesar,
            "interval",
            minutes=max(1, int(settings.tiktok_webhook_reintentos_min)),
            id="tiktok_webhook_reintentos",
            next_run_time=datetime.now() + timedelta(seconds=330),
            max_instances=1,
            coalesce=True,
        )
        log.info("Reintentos de avisos de TikTok cada %s min (tope %s intentos).",
                 settings.tiktok_webhook_reintentos_min,
                 settings.tiktok_webhook_reintentos_tope)
    # Guía + etiqueta PDF de TikTok en Odoo. Cada 20 min: el PDF sólo existe
    # entre el agendado de la recolección y la recolección.
    if getattr(settings, "tiktok_guias_enabled", False):
        from services import pedidos_tiktok as _ptk
        _min_guias = max(1, int(settings.tiktok_guias_min))

        async def _guias_tiktok() -> None:
            await _ptk.refrescar_guias(
                dias=settings.tiktok_guias_dias,
                limite=settings.tiktok_guias_limite,
                # Techo por vuelta: 80% del intervalo, como en Temu.
                segundos_max=int(_min_guias * 60 * 0.8),
            )
        _scheduler.add_job(
            _guias_tiktok,
            "interval",
            minutes=_min_guias,
            id="tiktok_guias",
            next_run_time=datetime.now() + timedelta(seconds=360),
            max_instances=1,
            coalesce=True,
        )
        log.info("Guías/etiquetas de TikTok cada %s min (tope %s días, %s por vuelta).",
                 _min_guias, settings.tiktok_guias_dias, settings.tiktok_guias_limite)
    # Sondeo de ventas de WALMART (pieza 6). Es SONDEO y no webhook porque
    # `/v3/webhooks/subscriptions` devuelve 520 del lado de Walmart — el
    # catálogo de eventos sí contesta, la suscripción no. Ver
    # services/pedidos_walmart.py.
    if getattr(settings, "pedidos_walmart_sondeo_enabled", False):
        from services import pedidos_walmart
        _scheduler.add_job(
            pedidos_walmart.sondear,
            "interval",
            minutes=settings.pedidos_walmart_sondeo_min,
            id="pedidos_walmart_sondeo",
            next_run_time=datetime.now() + timedelta(seconds=150),
            max_instances=1,
            coalesce=True,
        )
        log.info("Sondeo de ventas Walmart cada %s min (solo_registro=%s).",
                 settings.pedidos_walmart_sondeo_min,
                 settings.pedidos_walmart_solo_registro)
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
    # Devoluciones de ML: la RED DE SEGURIDAD del webhook `post_purchase`.
    #
    # El webhook avisa en segundos, pero un webhook PERDIDO es invisible —nada
    # avisa de lo que no llegó— y ML deshabilita un topic al que se le contesta
    # mal. Este barrido revisa las últimas 48 h y repone lo que falte. Escribir
    # dos veces la misma devolución no cuesta nada: el upsert es idempotente.
    #
    # Arranca a los 4 min para no competir con el sync de inventario en el
    # despertar del contenedor.
    if getattr(settings, "devoluciones_ml_enabled", False):
        from services import devoluciones_ml
        _scheduler.add_job(
            devoluciones_ml.revisar,
            "interval",
            minutes=settings.devoluciones_ml_min,
            id="devoluciones_ml",
            next_run_time=datetime.now() + timedelta(seconds=240),
            max_instances=1,
            coalesce=True,
        )
        log.info("Devoluciones ML: barrido cada %s min sobre %s días.",
                 settings.devoluciones_ml_min, settings.devoluciones_ml_dias)

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
    # DIAGNÓSTICO DE TIKTOK, UNA SOLA VEZ (14-sep-2026): suscripción al aviso de
    # pedidos + ventas de N días cruzadas contra los tres registros. Solo lectura
    # (salvo TIKTOK_RECUPERAR_IDS, ver config). A los 3 min para no competir con
    # el despertar del contenedor. No depende de MySQL: el token lo lee
    # `tiktok.access_token` de donde toque (kubera o MySQL), y si no puede, el
    # diagnóstico lo dice en su línea.
    if getattr(settings, "tiktok_diagnostico_arranque", False):
        _scheduler.add_job(
            _tiktok_diagnostico_arranque,
            "date",
            # Con zona explícita: una fecha ingenua se lee en la zona del
            # scheduler (UTC) y, fuera de un servidor en UTC, caería horas en el
            # pasado y el job se daría por perdido sin correr.
            run_date=datetime.now(timezone.utc) + timedelta(minutes=3),
            id="tiktok_diagnostico_arranque",
            max_instances=1,
            misfire_grace_time=600,
        )
        log.info("Diagnóstico de TikTok al arrancar en 3 min (%s días%s%s).",
                 settings.tiktok_diagnostico_dias,
                 ", estado de TIKTOK_DIAGNOSTICO_IDS"
                 if (getattr(settings, "tiktok_diagnostico_ids", "") or "").strip() else "",
                 ", y recuperación por TIKTOK_RECUPERAR_IDS"
                 if (getattr(settings, "tiktok_recuperar_ids", "") or "").strip() else "")
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
    # Barrido de precios de venta de ML (v0.267.0). Confirma `price_sale` de las
    # activas preguntando `/items/{id}/sale_price`. NO reemplaza a los webhooks
    # de precio: ellos dan LATENCIA (segundos cuando ML avisa), esto da
    # COBERTURA (las 341 de 745 activas que en 3 días no recibieron ni un aviso
    # y por ese camino nunca se confirman). Ver services/precios_venta.py.
    #
    # Dos jobs y no uno: el de ARRANQUE hace una pasada completa para drenar el
    # atraso acumulado mientras el backend estuvo abajo; el GOTEO mantiene. Los
    # dos comparten el mismo candado, así que no pueden solaparse.
    if settings.sync_enabled and settings.precios_venta_barrido:
        from services import precios_venta
        if settings.precios_venta_arranque:
            _scheduler.add_job(
                precios_venta.barrido_arranque,
                "date",
                run_date=datetime.now() + timedelta(seconds=180),
                id="precios_venta_arranque",
                max_instances=1,
            )
        _scheduler.add_job(
            precios_venta.barrido_periodico,
            "interval",
            hours=1,
            id="precios_venta_goteo",
            # A los 15 min del boot, para no pisarse con el barrido de arranque
            # ni con la primera pasada del sync.
            next_run_time=datetime.now() + timedelta(minutes=15),
            max_instances=1,
            coalesce=True,
        )
        log.info("Barrido de precios de venta ML: %s por hora (ciclo ~%.1f h) "
                 "· pasada completa al arrancar=%s.",
                 settings.precios_venta_por_hora,
                 745 / max(settings.precios_venta_por_hora, 1),
                 settings.precios_venta_arranque)
    else:
        log.info("Barrido de precios de venta ML APAGADO "
                 "(PRECIOS_VENTA_BARRIDO=%s, SYNC_ENABLED=%s).",
                 settings.precios_venta_barrido, settings.sync_enabled)
    # Calidad de las publicaciones ML (/item/{id}/performance → 0053). Por
    # INTERVALO y no a hora fija: cada vuelta mide solo lo que no tenga captura
    # de HOY, así que un deploy a la hora del cron no se come el día y los
    # huecos de un 429 se llenan en la vuelta siguiente. Antes de
    # CALIDAD_ML_HORA_UTC la vuelta sale sin hacer nada. El job solo lanza la
    # task de fondo y vuelve; el candado de `calidad_ml` impide dos barridos.
    if settings.sync_enabled and settings.calidad_ml_enabled:
        from services import calidad_ml
        _scheduler.add_job(
            calidad_ml.corrida_programada,
            "interval",
            minutes=max(15, int(settings.calidad_ml_min)),
            id="calidad_ml",
            # Con zona (ver inventario_flujo) y a los 20 min del boot, detrás
            # del barrido de precios y de la primera pasada del sync.
            next_run_time=datetime.now(timezone.utc) + timedelta(minutes=20),
            max_instances=1,
            coalesce=True,
        )
        log.info("Calidad ML: vuelta cada %s min, desde las %02d UTC, tope %s.",
                 max(15, int(settings.calidad_ml_min)),
                 settings.calidad_ml_hora_utc,
                 settings.calidad_ml_por_corrida or "sin tope")
    else:
        log.info("Calidad ML APAGADA (CALIDAD_ML_ENABLED=%s, SYNC_ENABLED=%s).",
                 settings.calidad_ml_enabled, settings.sync_enabled)
    # Vigilante de alertas (Slack): detecta AUSENCIAS — actas de migración
    # faltantes/con deltas, silencio de ventas, tokens rancios. Solo existe si
    # hay SLACK_WEBHOOK_URL; los errores push (espejo, refresh de tokens) no
    # pasan por aquí — avisan solos en el momento. Ver services/alertas.py.
    #
    # El mismo job lleva las dos revisiones DIARIAS del costeo (margen negativo
    # y top 10 con costo sin verificar). No llevan job propio a propósito: se
    # auto-limitan a una corrida al día con `alertas._toca_hoy`, sellada en
    # `alertas_estado` para que sobreviva a los deploys de Railway. Un job
    # `cron` aparte se saltaría el día entero si el deploy cae en su hora.
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
    # FLUJO DEL SKU: rearma la foto del catálogo que lee el stepper de /omnicanal.
    # El job NO la arma: solo lanza el hilo propio de `inventario_flujo` y
    # vuelve. El armado tarda 12–35 s contra Odoo; esperarlo aquí ocuparía el
    # loop, y mandarlo a `asyncio.to_thread` ocuparía uno de los ~6 hilos que
    # comparten el pool de Supabase y los webhooks de ventas.
    #
    # `forzar=True` a propósito: el job corre cada 30 min y la foto vence a
    # los 1800 s, así que preguntando «¿ya venció?» la encontraría con 29 min y
    # medio y se saltaría una vuelta entera — la foto viviría una hora.
    #
    # +210 s: después de sync (30), odoo_watch (120) y stock_watch (180), para
    # que el primer golpe a Odoo tras un deploy no caiga encima de los otros.
    if settings.inventario_flujo_enabled and settings.odoo_url:
        from services import inventario_flujo

        async def _flujo_precalentar() -> None:
            inventario_flujo.calentar_en_fondo(forzar=True, motivo="scheduler")

        _scheduler.add_job(
            _flujo_precalentar,
            "interval",
            minutes=settings.inventario_flujo_min,
            id="inventario_flujo",
            # Con zona: el scheduler va en UTC y una fecha ingenua, fuera de un
            # servidor en UTC, cae horas en el pasado y la primera foto se pierde.
            next_run_time=datetime.now(timezone.utc) + timedelta(seconds=210),
            max_instances=1,
            coalesce=True,
        )
        log.info("Flujo del SKU: foto del catálogo cada %s min.",
                 settings.inventario_flujo_min)
    else:
        log.info("Flujo del SKU APAGADO (INVENTARIO_FLUJO_ENABLED=%s, ODOO_URL=%s).",
                 settings.inventario_flujo_enabled, bool(settings.odoo_url))
    _scheduler.start()
    if settings.sync_enabled:
        log.info("Sync programado cada %s min.", settings.sync_interval_min)


def detener() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
