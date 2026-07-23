"""
kubera_mirror.py — Espejo (dual-write) hacia la BD centralizada "kubera".

Fase de DESCUBRIMIENTO de la migración: por cada escritor `.py` que hoy puebla
MySQL y aún no tiene espejo, este módulo replica la escritura en la tabla
equivalente del esquema v4 (Postgres/Supabase, `ESQUEMA_kubera_v4_propuesto.sql`)
y REGISTRA cada intento (éxito y error). Los errores que aparezcan (FKs
huérfanas, tipos, colisiones) son el insumo de la limpieza previa al corte; se
consultan en la página /migracion del panel.

Extiende el patrón de `channel_mirror.py` / `costing_mirror.py` (trabajo del
compañero, INTOCABLE — regla 4 de CLAUDE.md) a los escritores que ellos no
cubren. Lo que ellos ya espejan se marca `cubierto_por_companero` en el CENSO
y NO se duplica aquí.

Propiedades innegociables (misión 2026-07-22):
  - Un error del espejo JAMÁS interrumpe ni degrada el flujo actual: `espejar()`
    no lanza, no bloquea (solo encola; 2 workers daemon drenan en serie) y la
    conexión tiene timeout corto.
  - Se invoca DESPUÉS de la escritura MySQL exitosa, nunca antes.
  - Cada intento queda en el ring buffer (últimos 500) + contadores por
    (archivo, tabla); los ERRORES además se persisten en la tabla LOCAL MySQL
    `espejo_kubera_log` (local a propósito: si Supabase está caído, el error se
    tiene que poder guardar de todos modos).
  - Flags: KUBERA_MIRROR_ENABLED (default false) y KUBERA_MIRROR_TABLAS (CSV de
    tablas ORIGEN para encendido gradual). Apagable sin deploy en Railway.

Reglas del pooler transaccional (6543), heredadas de supabase_db.py: nada de
estado de sesión; los timeouts se fijan con set_config(..., true) = SET LOCAL.
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

from config import settings

log = logging.getLogger("omnicanal.kubera_mirror")

# ══════════════════════════════════════════════════════════════════════════════
# CENSO escritor → tablas (entregable 1 de la misión; alimenta /migracion)
#
# estado:
#   a_espejar             → este módulo lo espeja (activo si el flag lo permite)
#   cubierto_por_companero→ ya lo espeja channel/costing_mirror o el dual-write
#                           de webhooks (SUPABASE_DUAL_WRITE) — NO tocar
#   gap_sin_destino       → la tabla NO existe en el esquema v4: se reporta con
#                           propuesta de DDL, no se improvisa (regla dura)
#   no_aplica             → caché regenerable o foto local: no viaja
#   bloqueado             → secretos (P3): no espejar hasta resolver Vault
# ══════════════════════════════════════════════════════════════════════════════
CENSO: list[dict[str, Any]] = [
    {"archivo": "services/pedidos_ml.py", "funcion": "sincronizar",
     "tabla_mysql": "pedidos_ml", "tabla_kubera": "channel.orders",
     "operacion": "UPSERT", "disparador": "webhook orders_v2 (segundos)",
     "estado": "a_espejar",
     "nota": "GAP CERRADO: DDL channel.orders aplicado el 2026-07-22 con GO de "
             "Eduardo (propuesta_ops_orders.sql). Ventas BEKURA/SANCORFASHION."},
    {"archivo": "services/pedidos_amazon.py", "funcion": "→ pedidos_ml.sincronizar",
     "tabla_mysql": "pedidos_ml", "tabla_kubera": "channel.orders",
     "operacion": "UPSERT", "disparador": "sondeo SP-API cada 5 min",
     "estado": "a_espejar",
     "nota": "Mismo seam en pedidos_ml.sincronizar (cuenta AMAZON → canal amazon)."},
    {"archivo": "services/pedidos_m2e.py", "funcion": "→ pedidos_ml.sincronizar",
     "tabla_mysql": "pedidos_ml", "tabla_kubera": "channel.orders",
     "operacion": "UPSERT", "disparador": "sondeo M2E cada 10 min",
     "estado": "a_espejar",
     "nota": "Mismo seam (cuentas TEMU/TIKTOK → canal temu/tiktok)."},
    {"archivo": "routers/webhooks.py", "funcion": "_guardar/_guardar_supabase",
     "tabla_mysql": "webhook_eventos", "tabla_kubera": "ops.webhook_events",
     "operacion": "INSERT", "disparador": "webhook ML (ráfagas)",
     "estado": "cubierto_por_companero",
     "nota": "Dual-write idempotente ya existe (SUPABASE_DUAL_WRITE)."},
    {"archivo": "services/odoo_watch.py", "funcion": "_avisar_campana",
     "tabla_mysql": "webhook_eventos", "tabla_kubera": "ops.webhook_events",
     "operacion": "INSERT", "disparador": "scheduler cada 30 min",
     "estado": "a_espejar",
     "nota": "Eventos de campana canal='odoo' (cambios de stock)."},
    {"archivo": "services/odoo_watch.py", "funcion": "_guardar_foto",
     "tabla_mysql": "productos.stock_odoo", "tabla_kubera": None,
     "operacion": "UPDATE", "disparador": "scheduler cada 30 min",
     "estado": "no_aplica",
     "nota": "Foto local de stock Odoo (Odoo en retiro). El stock por canal ya "
             "viaja vía canal_inventario → channel.listings (compañero)."},
    {"archivo": "services/ventas_ml.py", "funcion": "asegurar_dia",
     "tabla_mysql": "ventas_horarias / ventas_sync", "tabla_kubera": None,
     "operacion": "REPLACE", "disparador": "tab Ventas + warmup",
     "estado": "no_aplica",
     "nota": "Caché regenerable de la API de ML (vista histórica). La fuente "
             "de verdad de ventas son los PEDIDOS (ver gap pedidos_ml)."},
    {"archivo": "services/publicar_ready.py", "funcion": "_backlog_ml",
     "tabla_mysql": "ml_backlog", "tabla_kubera": "ops.channel_submissions",
     "operacion": "INSERT", "disparador": "UI Studio (publicar Ready)",
     "estado": "a_espejar",
     "nota": "Resumen + detail_ref='mysql:ml_backlog:<id>'; blobs NO viajan."},
    {"archivo": "services/publicar_ready.py", "funcion": "_anotar_pausa_backlog",
     "tabla_mysql": "ml_backlog", "tabla_kubera": "ops.channel_submissions",
     "operacion": "UPDATE", "disparador": "UI Studio (verificación de pausa)",
     "estado": "a_espejar",
     "nota": "Evento operacion='pausa' cuando el item NO pudo pausarse."},
    {"archivo": "services/publicar_ready.py", "funcion": "_backlog_ml (progress)",
     "tabla_mysql": "ml_progress", "tabla_kubera": "channel.listings",
     "operacion": "UPSERT", "disparador": "UI Studio (publicar Ready)",
     "estado": "cubierto_por_companero",
     "nota": "El estado del listing viaja por canal_inventario → channel_mirror "
             "(sync 15 min) y el ETL fusiona ml_progress. Solo submissions van "
             "por este módulo."},
    {"archivo": "services/publicar.py", "funcion": "_guardar_backlog_ml",
     "tabla_mysql": "ml_backlog", "tabla_kubera": "ops.channel_submissions",
     "operacion": "INSERT", "disparador": "UI Studio (actualizar/publicar ML)",
     "estado": "a_espejar", "nota": "Resumen + detail_ref."},
    {"archivo": "services/publicar.py", "funcion": "_guardar_backlog_ml (progress)",
     "tabla_mysql": "ml_progress", "tabla_kubera": "channel.listings",
     "operacion": "UPSERT", "disparador": "UI Studio",
     "estado": "cubierto_por_companero", "nota": "Ídem ml_progress de arriba."},
    {"archivo": "services/publicar.py", "funcion": "_guardar_backlog_amazon",
     "tabla_mysql": "amazon_backlog", "tabla_kubera": "ops.channel_submissions",
     "operacion": "INSERT", "disparador": "UI Studio (publicar Amazon)",
     "estado": "a_espejar",
     "nota": "Resumen + detail_ref='mysql:amazon_backlog:<id>'; los blobs de "
             "186 MB se quedan en MySQL."},
    {"archivo": "services/publicar.py", "funcion": "_guardar_backlog_amazon (progress)",
     "tabla_mysql": "amazon_progress", "tabla_kubera": "channel.listings",
     "operacion": "UPSERT", "disparador": "UI Studio",
     "estado": "cubierto_por_companero", "nota": "Ídem ml_progress."},
    {"archivo": "services/imagenes_amazon.py", "funcion": "_cache_put",
     "tabla_mysql": "amazon_imagenes", "tabla_kubera": "enrich.product_media",
     "operacion": "UPSERT", "disparador": "pipeline imágenes Amazon",
     "estado": "a_espejar",
     "nota": "kind='amazon'. Upsert atómico vía uq_product_media_sku_kind_url "
             "(índice creado por Eduardo 2026-07-22)."},
    {"archivo": "services/imagenes_editor.py", "funcion": "_backlog",
     "tabla_mysql": "ml_image_edit_backlog", "tabla_kubera": "ops.channel_submissions",
     "operacion": "INSERT", "disparador": "UI editor de imágenes IA",
     "estado": "a_espejar", "nota": "operacion='imagen', resumen sin prompts largos."},
    {"archivo": "services/crear_producto.py", "funcion": "_persistir_log",
     "tabla_mysql": "crear_logs", "tabla_kubera": "ops.process_log",
     "operacion": "INSERT", "disparador": "UI Crear productos",
     "estado": "a_espejar", "nota": "proceso='crear'; detalle ya viene truncado a 4 KB."},
    {"archivo": "services/costos.py", "funcion": "_guardar_validados/_guardar_finales/_log_costo",
     "tabla_mysql": "costos_validados / costos_finales / costos_logs",
     "tabla_kubera": "costing.* / ops.process_log",
     "operacion": "UPSERT", "disparador": "UI Costos",
     "estado": "cubierto_por_companero", "nota": "costing_mirror (3 puntos)."},
    {"archivo": "services/inventario.py", "funcion": "_upsert",
     "tabla_mysql": "canal_inventario", "tabla_kubera": "channel.listings",
     "operacion": "UPSERT", "disparador": "scheduler cada 15 min",
     "estado": "cubierto_por_companero", "nota": "channel_mirror. NO TOCAR."},
    {"archivo": "services/meli.py", "funcion": "auto-refresh de tokens",
     "tabla_mysql": "ml_tokens / ml_tokens_dashboard", "tabla_kubera": "ops.ml_tokens",
     "operacion": "UPDATE", "disparador": "401 → refresh",
     "estado": "bloqueado",
     "nota": "P3: secretos van a Supabase Vault; no espejar texto plano."},
]

# ══════════════════════════════════════════════════════════════════════════════
# Estado en memoria: ring buffer + contadores
# ══════════════════════════════════════════════════════════════════════════════
_eventos: deque[dict[str, Any]] = deque(maxlen=500)
_contadores: dict[tuple[str, str], dict[str, Any]] = {}
_state_lock = threading.Lock()

_pool = None
_pool_lock = threading.Lock()
_tabla_log_lista = False

_MAX_ERROR_TXT = 1500
_MAX_PAYLOAD_JSON = 4000


def disponible() -> bool:
    return bool(settings.kubera_db_url)


def _tablas_filtro() -> set[str] | None:
    csv = (settings.kubera_mirror_tablas or "").strip()
    if not csv:
        return None
    return {t.strip().lower() for t in csv.split(",") if t.strip()}


def activo(tabla_mysql: str) -> bool:
    """¿Debe espejarse esta tabla origen? (flag global + filtro por tabla)."""
    if not settings.kubera_mirror_enabled or not disponible():
        return False
    filtro = _tablas_filtro()
    return True if filtro is None else tabla_mysql.lower() in filtro


def _get_pool():
    """Pool chico y perezoso, con timeout corto (jamás cuelga al backend)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                import psycopg2
                from dbutils.pooled_db import PooledDB
                _pool = PooledDB(
                    creator=psycopg2,
                    # 3 se quedaba corto en ráfagas del pipeline de Crear
                    # (23-jul: 60 eventos crear_logs→ops.process_log perdidos
                    # por TooManyConnections en una madrugada de altas).
                    maxconnections=6,
                    mincached=0,
                    maxcached=3,
                    blocking=False,   # pool lleno = error registrado, NO espera
                    ping=1,
                    dsn=settings.kubera_db_url,
                    connect_timeout=4,
                )
    return _pool


# ══════════════════════════════════════════════════════════════════════════════
# API pública: espejar() — fire-and-forget, jamás lanza, jamás bloquea
#
# Despacho por COLAS ACOTADAS + workers dedicados (v0.15.3). El diseño original
# lanzaba un hilo/tarea por llamada: en ráfagas (crear_producto emite decenas
# de logs en segundos y cada escritura a Supabase tarda ~400 ms) más de 3
# trabajos concurrentes agotaban el pool (blocking=False) y ~10% de los
# intentos moría en TooManyConnectionsError (60 errores reales capturados en
# producción el 23-jul; complementa el pool 3→6 + reproceso de v0.15.2).
# Una cola por worker con AFINIDAD POR CLAVE: ningún intento se pierde por el
# pool, el llamador no espera nada, y dos eventos de la misma orden/SKU jamás
# se aplican invertidos (misma clave → mismo worker → FIFO).
# ══════════════════════════════════════════════════════════════════════════════

_N_WORKERS = 2      # ≤2 conexiones del pool en uso (el pool tiene 6)
_COLA_MAX = 500     # tope POR COLA; más allá se descarta CON registro

_colas: list[queue.Queue] | None = None
_workers_lock = threading.Lock()


class ColaLlenaError(Exception):
    """La cola del espejo se llenó (>_COLA_MAX pendientes): intento descartado."""


def _asegurar_workers() -> list[queue.Queue]:
    global _colas
    if _colas is None:
        with _workers_lock:
            if _colas is None:
                qs = [queue.Queue(maxsize=_COLA_MAX) for _ in range(_N_WORKERS)]
                for i, q in enumerate(qs):
                    threading.Thread(target=_worker, args=(q,), daemon=True,
                                     name=f"kubera-mirror-{i}").start()
                _colas = qs
    return _colas


def _worker(q: queue.Queue) -> None:
    while True:
        args = q.get()
        try:
            _trabajar(*args)
        except Exception as exc:  # noqa: BLE001 — cinturón: _trabajar ya captura todo
            log.warning("worker del espejo kubera: %s", exc)
        finally:
            q.task_done()


def espejar(origen_py: str, funcion: str, tabla_mysql: str, tabla_kubera: str,
            operacion: str, payload: dict[str, Any], clave: str | None = None) -> None:
    """Replica una escritura MySQL YA EXITOSA hacia la BD kubera.

    Se llama justo después del INSERT/UPDATE en MySQL. Solo encola (put_nowait)
    y regresa; los workers daemon hacen la escritura. Cualquier excepción muere
    aquí. Si la cola está llena, el intento se descarta pero queda REGISTRADO
    en memoria (evento ColaLlenaError) — sin tocar MySQL en el camino crítico.

    AFINIDAD POR CLAVE: la misma (tabla, clave) cae siempre en el mismo worker
    → los eventos de una misma orden/SKU se aplican en orden FIFO (dos updates
    en ráfaga no pueden invertirse); claves distintas van en paralelo.
    """
    try:
        if not activo(tabla_mysql):
            return
        args = (origen_py, funcion, tabla_mysql, tabla_kubera, operacion,
                dict(payload or {}), clave)
        colas = _asegurar_workers()
        idx = hash((tabla_mysql, clave or "")) % len(colas)
        try:
            colas[idx].put_nowait(args)
        except queue.Full:
            _registrar(origen_py, funcion, tabla_mysql, tabla_kubera, operacion,
                       clave, ok=False, ms=0.0,
                       exc=ColaLlenaError(f"cola llena ({_COLA_MAX} pendientes)"))
    except Exception as exc:  # noqa: BLE001 — el espejo nunca rompe el flujo
        log.debug("kubera_mirror.espejar (ignorado): %s", exc)


def _trabajar(origen_py: str, funcion: str, tabla_mysql: str, tabla_kubera: str,
              operacion: str, payload: dict[str, Any], clave: str | None) -> None:
    t0 = time.perf_counter()
    conn = None
    try:
        upsert = _UPSERTS.get(tabla_kubera)
        if upsert is None:
            raise ValueError(f"sin upsert definido para {tabla_kubera!r}")
        conn = _get_pool().connection()
        with conn.cursor() as cur:
            # SET LOCAL (transaccional): compatible con el pooler 6543.
            cur.execute("select set_config('statement_timeout', '4000', true)")
            cur.execute("select set_config('app.via', 'kubera_mirror', true)")
            upsert(cur, payload)
        conn.commit()
        _registrar(origen_py, funcion, tabla_mysql, tabla_kubera, operacion,
                   clave, ok=True, ms=(time.perf_counter() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001
        try:
            if conn is not None:
                conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        ms = (time.perf_counter() - t0) * 1000
        _registrar(origen_py, funcion, tabla_mysql, tabla_kubera, operacion,
                   clave, ok=False, ms=ms, exc=exc)
        _persistir_error(origen_py, funcion, tabla_mysql, tabla_kubera,
                         operacion, clave, exc, payload)
    finally:
        try:
            if conn is not None:
                conn.close()  # devuelve al pool
        except Exception:  # noqa: BLE001
            pass


# ══════════════════════════════════════════════════════════════════════════════
# Upserts idempotentes por tabla destino (PKs/uniques del esquema v4)
# ══════════════════════════════════════════════════════════════════════════════

def _up_webhook_events(cur, p: dict[str, Any]) -> None:
    """ops.webhook_events — UNIQUE (env,canal,topic,external_id,delivery_id)."""
    recibido = p.get("recibido") or datetime.now(timezone.utc)
    external_id = str(p.get("external_id") or "sin-recurso")
    delivery = p.get("delivery_id") or hashlib.sha256(
        f"{external_id}|{recibido}".encode()).hexdigest()[:32]
    cur.execute(
        """insert into ops.webhook_events
             (env, canal, topic, external_id, delivery_id, cuenta, sku,
              payload, procesado, resultado, recibido_at)
           values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
           on conflict (env, canal, topic, external_id, delivery_id) do nothing""",
        (settings.app_env, p.get("canal") or "odoo",
         p.get("topic") or "evento", external_id, delivery,
         p.get("cuenta"), p.get("sku"),
         json.dumps(p.get("payload") or {}, ensure_ascii=False, default=str),
         bool(p.get("procesado", True)),
         (p.get("resultado") or "")[:255] or None, recibido),
    )


def _up_channel_submissions(cur, p: dict[str, Any]) -> None:
    """ops.channel_submissions — bitácora; idempotencia por detail_ref."""
    detail_ref = p.get("detail_ref")
    if detail_ref:
        cur.execute("select 1 from ops.channel_submissions where detail_ref = %s limit 1",
                    (detail_ref,))
        if cur.fetchone():
            return
    cur.execute(
        """insert into ops.channel_submissions
             (canal, cuenta, sku, submission_id, operacion, status, success,
              error_resumen, detail_ref, submitted_at, published_at)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (p.get("canal"), p.get("cuenta"), p.get("sku"),
         p.get("submission_id"), p.get("operacion"), p.get("status"),
         p.get("success"), (p.get("error_resumen") or "")[:500] or None,
         detail_ref, p.get("submitted_at"), p.get("published_at")),
    )


def _up_process_log(cur, p: dict[str, Any]) -> None:
    """ops.process_log — bitácora; idempotencia por detail_ref."""
    detail_ref = p.get("detail_ref")
    if detail_ref:
        cur.execute("select 1 from ops.process_log where detail_ref = %s limit 1",
                    (detail_ref,))
        if cur.fetchone():
            return
    detalle = p.get("detalle")
    cur.execute(
        """insert into ops.process_log
             (proceso, origen, sku, accion, estado, detalle, detail_ref, duracion_s)
           values (%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
        (p.get("proceso") or "desconocido", p.get("origen") or "backend",
         p.get("sku"), p.get("accion"), p.get("estado"),
         json.dumps(detalle, ensure_ascii=False, default=str) if detalle is not None else None,
         detail_ref, p.get("duracion_s")),
    )


def _up_product_media(cur, p: dict[str, Any]) -> None:
    """enrich.product_media — upsert atómico sobre el índice único
    uq_product_media_sku_kind_url (creado por Eduardo el 2026-07-22 a raíz del
    hallazgo del censo)."""
    cur.execute(
        """insert into enrich.product_media (sku, kind, source_url, cdn_url)
           values (%s,%s,%s,%s)
           on conflict (sku, kind, source_url)
           do update set cdn_url = excluded.cdn_url""",
        (p.get("sku"), p.get("kind") or "amazon", p.get("source_url"),
         p.get("cdn_url")),
    )


def _up_channel_orders(cur, p: dict[str, Any]) -> None:
    """channel.orders — PK (canal, cuenta, external_order_id). Fiel a la
    semántica de MySQL pedidos_ml: el conflicto solo mueve wc_order_id y los
    estados (total/comisión/skus/creado_at quedan CONGELADOS al primer
    registro, igual que el pedido histórico)."""
    cur.execute(
        """insert into channel.orders
             (external_order_id, canal, cuenta, wc_order_id, estado_canal,
              estado_wc, total, comision, es_fulfillment, skus, creado_at)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::citext[],%s)
           on conflict (canal, cuenta, external_order_id) do update set
             wc_order_id  = excluded.wc_order_id,
             estado_canal = excluded.estado_canal,
             estado_wc    = excluded.estado_wc,
             actualizado_at = now()""",
        (str(p.get("external_order_id") or ""), p.get("canal"), p.get("cuenta"),
         p.get("wc_order_id"), p.get("estado_canal"), p.get("estado_wc"),
         p.get("total"), p.get("comision"), bool(p.get("es_fulfillment")),
         list(p.get("skus") or []), p.get("creado_at")),
    )


_UPSERTS: dict[str, Callable] = {
    "ops.webhook_events": _up_webhook_events,
    "ops.channel_submissions": _up_channel_submissions,
    "ops.process_log": _up_process_log,
    "enrich.product_media": _up_product_media,
    "channel.orders": _up_channel_orders,
}


# ══════════════════════════════════════════════════════════════════════════════
# Registro: ring buffer + contadores + persistencia LOCAL de errores
# ══════════════════════════════════════════════════════════════════════════════

def _registrar(origen_py: str, funcion: str, tabla_mysql: str, tabla_kubera: str,
               operacion: str, clave: str | None, ok: bool, ms: float,
               exc: Exception | None = None) -> None:
    ev = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "archivo": origen_py, "funcion": funcion,
        "tabla_origen": tabla_mysql, "tabla_destino": tabla_kubera,
        "operacion": operacion, "clave": clave, "ok": ok, "ms": round(ms, 1),
        "error_tipo": type(exc).__name__ if exc else None,
        "error_texto": (str(exc)[:300] if exc else None),
    }
    with _state_lock:
        _eventos.appendleft(ev)
        c = _contadores.setdefault((origen_py, funcion, tabla_mysql), {
            "ok": 0, "error": 0, "ms_total": 0.0, "n": 0, "ultimo": None})
        c["ok" if ok else "error"] += 1
        c["ms_total"] += ms
        c["n"] += 1
        c["ultimo"] = ev
    if not ok:
        log.warning("espejo kubera %s→%s falló (%s): %s",
                    tabla_mysql, tabla_kubera, clave, exc)


_DDL_LOG = """
CREATE TABLE IF NOT EXISTS espejo_kubera_log (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  ts            DATETIME NOT NULL,
  archivo_py    VARCHAR(80) NOT NULL,
  funcion       VARCHAR(80),
  tabla_origen  VARCHAR(80) NOT NULL,
  tabla_destino VARCHAR(120),
  operacion     VARCHAR(20),
  clave         VARCHAR(190),
  error_tipo    VARCHAR(120),
  error_texto   TEXT,
  payload_json  MEDIUMTEXT,
  resuelto      TINYINT NOT NULL DEFAULT 0,
  resuelto_ts   DATETIME NULL,
  INDEX idx_espejo_grupo (archivo_py, tabla_origen, error_tipo, resuelto),
  INDEX idx_espejo_ts (ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _asegurar_tabla_log() -> bool:
    global _tabla_log_lista
    if _tabla_log_lista:
        return True
    try:
        from services import db
        db.execute(_DDL_LOG)
        _tabla_log_lista = True
    except Exception as exc:  # noqa: BLE001
        log.warning("espejo_kubera_log CREATE TABLE: %s", exc)
    return _tabla_log_lista


def _persistir_error(origen_py: str, funcion: str, tabla_mysql: str,
                     tabla_kubera: str, operacion: str, clave: str | None,
                     exc: Exception, payload: dict[str, Any]) -> None:
    """El error se guarda en MySQL (LOCAL): sobrevive aunque kubera esté caída."""
    try:
        if not _asegurar_tabla_log():
            return
        from services import db
        try:
            pj = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            pj = str(payload)
        if len(pj) > _MAX_PAYLOAD_JSON:
            pj = pj[:_MAX_PAYLOAD_JSON] + "…(truncado)"
        db.execute(
            """INSERT INTO espejo_kubera_log
                 (ts, archivo_py, funcion, tabla_origen, tabla_destino,
                  operacion, clave, error_tipo, error_texto, payload_json)
               VALUES (UTC_TIMESTAMP(),%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (origen_py[:80], (funcion or "")[:80], tabla_mysql[:80],
             (tabla_kubera or "")[:120], (operacion or "")[:20],
             (clave or "")[:190] or None, type(exc).__name__[:120],
             str(exc)[:_MAX_ERROR_TXT], pj),
        )
    except Exception as exc2:  # noqa: BLE001
        log.warning("no se pudo persistir el error del espejo: %s", exc2)


def reprocesar_errores(max_items: int = 500) -> dict[str, Any]:
    """Re-aplica los errores pendientes (resuelto=0) desde su payload_json.

    Secuencial y con una conexión del pool a la vez: cero presión extra sobre
    kubera. Es seguro re-aplicar: los upserts destino son idempotentes y los
    errores típicos (TooManyConnections) fallaron ANTES de escribir nada.
    Payloads ilegibles (p. ej. truncados a _MAX_PAYLOAD_JSON) se saltan y se
    reportan para revisión manual.
    """
    if not disponible():
        return {"ok": False, "motivo": "KUBERA_DB_URL no configurada."}
    if not _asegurar_tabla_log():
        return {"ok": False, "motivo": "Sin tabla espejo_kubera_log."}
    from services import db
    filas = db.fetch_all(
        """SELECT id, tabla_destino, payload_json FROM espejo_kubera_log
           WHERE resuelto = 0 AND payload_json IS NOT NULL
           ORDER BY id LIMIT %s""",
        (int(max_items),),
    )
    aplicados = ilegibles = fallidos = 0
    detalle_fallos: list[str] = []
    for f in filas:
        upsert = _UPSERTS.get(f["tabla_destino"] or "")
        try:
            payload = json.loads(f["payload_json"])
        except Exception:  # noqa: BLE001 — truncado o corrupto
            payload = None
        if upsert is None or not isinstance(payload, dict):
            ilegibles += 1
            continue
        try:
            conn = _get_pool().connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("select set_config('statement_timeout', '4000', true)")
                    cur.execute("select set_config('app.via', 'kubera_mirror', true)")
                    upsert(cur, payload)
                conn.commit()
            finally:
                conn.close()
            db.execute(
                "UPDATE espejo_kubera_log SET resuelto=1, resuelto_ts=UTC_TIMESTAMP() WHERE id=%s",
                (f["id"],),
            )
            aplicados += 1
        except Exception as exc:  # noqa: BLE001
            fallidos += 1
            if len(detalle_fallos) < 5:
                detalle_fallos.append(
                    f"id {f['id']}: {type(exc).__name__}: {str(exc)[:120]}")
    return {"ok": True, "pendientes_leidos": len(filas), "aplicados": aplicados,
            "ilegibles": ilegibles, "fallidos": fallidos,
            "detalle_fallos": detalle_fallos}


def backfill_product_media(max_items: int = 1000) -> dict[str, Any]:
    """Copia el caché histórico amazon_imagenes (MySQL) → enrich.product_media.

    One-shot e idempotente (upsert por el índice único de Eduardo): el espejo
    en vivo solo captura eventos NUEVOS; esto trae las imágenes procesadas
    antes del encendido de la tabla. Secuencial, una conexión a la vez —
    misma disciplina que reprocesar_errores(). De paso sirve de verificación:
    si el índice único no existiera, el ON CONFLICT fallaría aquí y no en el
    flujo vivo."""
    if not disponible():
        return {"ok": False, "motivo": "KUBERA_DB_URL no configurada."}
    from services import db
    filas = db.fetch_all(
        """SELECT sku, src_url, amz_url FROM amazon_imagenes
           WHERE sku IS NOT NULL AND src_url IS NOT NULL AND amz_url IS NOT NULL
           ORDER BY created_at LIMIT %s""",
        (int(max_items),),
    )
    aplicadas = fallidas = 0
    errores: list[str] = []
    for f in filas:
        try:
            conn = _get_pool().connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("select set_config('statement_timeout', '4000', true)")
                    cur.execute("select set_config('app.via', 'kubera_mirror', true)")
                    _up_product_media(cur, {"sku": f["sku"], "kind": "amazon",
                                            "source_url": f["src_url"],
                                            "cdn_url": f["amz_url"]})
                conn.commit()
            finally:
                conn.close()
            aplicadas += 1
        except Exception as exc:  # noqa: BLE001
            fallidas += 1
            if len(errores) < 5:
                errores.append(f"{f['sku']}: {type(exc).__name__}: {str(exc)[:100]}")
    return {"ok": True, "leidas": len(filas), "aplicadas": aplicadas,
            "fallidas": fallidas, "errores": errores}


_BACKFILL_CANAL = {
    "BEKURA": "mercado_libre", "SANCORFASHION": "mercado_libre",
    "AMAZON": "amazon", "TEMU": "temu", "TIKTOK": "tiktok",
}


def backfill_channel_orders(max_items: int = 5000) -> dict[str, Any]:
    """Copia el histórico pedidos_ml (MySQL) → channel.orders.

    One-shot e idempotente: usa el mismo upsert del seam v0.16.0, cuyo
    conflicto solo mueve wc_order_id/estados y deja congelados total/comisión/
    skus/creado_at — los pedidos que ya viajaron en vivo no se alteran.
    Mismo mapeo cuenta→canal que _ESPEJO_ORIGEN de pedidos_ml.
    Limitación conocida: los SKUs vienen del CSV MySQL (truncado a 255);
    los pedidos espejados EN VIVO llevan el array completo.
    Reporta CADA pedido que falle (hasta 100) para revisión."""
    if not disponible():
        return {"ok": False, "motivo": "KUBERA_DB_URL no configurada."}
    from services import db
    filas = db.fetch_all(
        """SELECT ml_order_id, cuenta, wc_order_id, estado_ml, estado_wc,
                  total, comision, es_full, skus, creado
           FROM pedidos_ml ORDER BY creado LIMIT %s""",
        (int(max_items),),
    )
    aplicadas = fallidas = 0
    errores: list[dict[str, Any]] = []
    for f in filas:
        cuenta = f.get("cuenta") or ""
        payload = {
            "external_order_id": str(f["ml_order_id"]),
            "canal": _BACKFILL_CANAL.get(cuenta, cuenta.lower() or "desconocido"),
            "cuenta": cuenta,
            "wc_order_id": f.get("wc_order_id"),
            "estado_canal": str(f.get("estado_ml") or ""),
            "estado_wc": str(f.get("estado_wc") or ""),
            "total": f.get("total"),
            "comision": f.get("comision"),
            "es_fulfillment": bool(f.get("es_full")),
            "skus": [s for s in (f.get("skus") or "").split(",") if s],
            "creado_at": f.get("creado"),
        }
        try:
            conn = _get_pool().connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("select set_config('statement_timeout', '4000', true)")
                    cur.execute("select set_config('app.via', 'kubera_mirror', true)")
                    _up_channel_orders(cur, payload)
                conn.commit()
            finally:
                conn.close()
            aplicadas += 1
        except Exception as exc:  # noqa: BLE001
            fallidas += 1
            if len(errores) < 100:
                errores.append({"pedido": str(f["ml_order_id"]), "cuenta": cuenta,
                                "error": f"{type(exc).__name__}: {str(exc)[:150]}"})
    return {"ok": True, "leidas": len(filas), "aplicadas": aplicadas,
            "fallidas": fallidas, "errores": errores}


# ══════════════════════════════════════════════════════════════════════════════
# Lecturas para /api/migracion/* (censo, eventos, errores agrupados)
# ══════════════════════════════════════════════════════════════════════════════

def estado() -> dict[str, Any]:
    """Censo + contadores + flags → tarjeta por escritor en /migracion."""
    with _state_lock:
        tarjetas = []
        for e in CENSO:
            key = (e["archivo"], e["funcion"], e["tabla_mysql"])
            c = _contadores.get(key) or {}
            if e["estado"] == "a_espejar":
                estado_espejo = "activo" if activo(e["tabla_mysql"]) else "apagado"
            else:
                estado_espejo = e["estado"]
            n = c.get("n") or 0
            tarjetas.append({
                **e,
                "estado_espejo": estado_espejo,
                "ok": c.get("ok", 0), "error": c.get("error", 0),
                "latencia_ms": round(c["ms_total"] / n, 1) if n else None,
                "ultimo": c.get("ultimo"),
            })
        # Red de seguridad: si alguien llama espejar() con un escritor que no
        # está en el CENSO, igual aparece en el panel (nada se escapa de la UI).
        vistos = {(e["archivo"], e["funcion"], e["tabla_mysql"]) for e in CENSO}
        for (arch, fn, tabla), c in _contadores.items():
            if (arch, fn, tabla) in vistos:
                continue
            n = c.get("n") or 0
            ult = c.get("ultimo") or {}
            tarjetas.append({
                "archivo": arch, "funcion": fn or "?",
                "tabla_mysql": tabla,
                "tabla_kubera": ult.get("tabla_destino"),
                "operacion": ult.get("operacion") or "?",
                "disparador": "—", "estado": "no_censado",
                "estado_espejo": "no_censado",
                "nota": "Escritor con espejo pero FUERA del censo: agregarlo a "
                        "CENSO en kubera_mirror.py.",
                "ok": c.get("ok", 0), "error": c.get("error", 0),
                "latencia_ms": round(c["ms_total"] / n, 1) if n else None,
                "ultimo": c.get("ultimo"),
            })
    return {
        "flags": {
            "KUBERA_MIRROR_ENABLED": settings.kubera_mirror_enabled,
            "KUBERA_DB_URL_definida": disponible(),
            "KUBERA_MIRROR_TABLAS": sorted(_tablas_filtro() or []) or None,
        },
        "escritores": tarjetas,
        "totales": {
            "ok": sum(t["ok"] for t in tarjetas),
            "error": sum(t["error"] for t in tarjetas),
        },
    }


def eventos(limit: int = 100) -> list[dict[str, Any]]:
    with _state_lock:
        return list(_eventos)[: max(1, min(limit, 500))]


def errores_agrupados(incluir_resueltos: bool = False) -> list[dict[str, Any]]:
    """Errores agrupados por (archivo, tabla, tipo): el plan de limpieza."""
    try:
        from services import db
        if not _asegurar_tabla_log():
            return []
        where = "" if incluir_resueltos else "WHERE resuelto=0"
        grupos = db.fetch_all(
            f"""SELECT archivo_py, tabla_origen, tabla_destino, error_tipo,
                       COUNT(*) n, MAX(ts) ultimo_ts,
                       SUM(resuelto=0) abiertos
                FROM espejo_kubera_log {where}
                GROUP BY archivo_py, tabla_origen, tabla_destino, error_tipo
                ORDER BY abiertos DESC, ultimo_ts DESC""")
        ejemplos = db.fetch_all(
            f"""SELECT t.archivo_py, t.tabla_origen, t.error_tipo,
                       t.error_texto, t.clave, t.payload_json
                FROM (SELECT e.*,
                             ROW_NUMBER() OVER (PARTITION BY archivo_py, tabla_origen, error_tipo
                                                ORDER BY id DESC) rn
                      FROM espejo_kubera_log e {where}) t
                WHERE t.rn = 1""")
        ej = {(x["archivo_py"], x["tabla_origen"], x["error_tipo"]): x for x in ejemplos}
        for g in grupos:
            x = ej.get((g["archivo_py"], g["tabla_origen"], g["error_tipo"])) or {}
            g["ejemplo"] = x.get("error_texto")
            g["ejemplo_clave"] = x.get("clave")
            g["ejemplo_payload"] = x.get("payload_json")
        return grupos
    except Exception as exc:  # noqa: BLE001
        log.warning("errores_agrupados: %s", exc)
        return []


def resolver_grupo(archivo_py: str, tabla_origen: str, error_tipo: str) -> int:
    """Marca un grupo de errores como resuelto. Devuelve filas afectadas."""
    from services import db
    return db.execute(
        """UPDATE espejo_kubera_log
           SET resuelto=1, resuelto_ts=UTC_TIMESTAMP()
           WHERE archivo_py=%s AND tabla_origen=%s AND error_tipo=%s AND resuelto=0""",
        (archivo_py, tabla_origen, error_tipo),
    )
