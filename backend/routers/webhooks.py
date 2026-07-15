"""
webhooks.py — Receptores de notificaciones (webhooks) de los marketplaces.

Mercado Libre envía un POST a la URL de callback cada vez que cambia un recurso
(item, orden, envío, etc.). Hay que responder 200 rápido (< 500 ms) y procesar
aparte.

  POST /api/webhooks/ml            → receptor de notificaciones de Mercado Libre
  GET  /api/webhooks/ml            → responde 200 (para probar accesibilidad)
  GET  /api/webhooks/ml/log        → últimas notificaciones (para pruebas)
  GET  /api/webhooks/notificaciones→ feed para la CAMPANA del frontend

⚙️ Persistencia: las notificaciones se GUARDAN en la tabla `webhook_eventos` de
MySQL (sobreviven reinicios; antes solo vivían en memoria).

🔁 DUAL-WRITE (piloto de migración a Supabase): con SUPABASE_DUAL_WRITE=true,
cada notificación se escribe ADEMÁS en `ops.webhook_events` (Supabase) con una
llave de IDEMPOTENCIA — UNIQUE(env, canal, topic, external_id, delivery_id) —
de modo que la base descarta los duplicados sola (hoy el 36% de las filas de
MySQL son repetidas). Reglas del patrón:
  - MySQL sigue siendo la fuente de verdad; Supabase es copia.
  - Un fallo de Supabase JAMÁS rompe la respuesta 200: se registra en
    ops.migration_issues (best-effort) y la operación continúa.
  - Revertir = SUPABASE_DUAL_WRITE=false (sin redeploy).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request

from config import settings
from services import db, inventario, meli
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.webhooks")
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# Interruptor en runtime: si está en False, el webhook responde 200 pero NO
# guarda en la tabla ni procesa (pausa el registro de datos sin redesplegar).
_registro_activo = settings.webhook_registro

_DDL = """
CREATE TABLE IF NOT EXISTS webhook_eventos (
    id        BIGINT AUTO_INCREMENT PRIMARY KEY,
    canal     VARCHAR(20)  NOT NULL DEFAULT 'mercado_libre',
    topic     VARCHAR(40),
    resource  VARCHAR(200),
    user_id   VARCHAR(40),
    cuenta    VARCHAR(50),
    sku       VARCHAR(60),
    procesado TINYINT(1)   NOT NULL DEFAULT 0,
    resultado VARCHAR(255),
    recibido  DATETIME     NOT NULL,
    INDEX idx_recibido (recibido)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""
_schema_ok = False


def _asegurar_schema() -> None:
    global _schema_ok
    if _schema_ok or not settings.mysql_enabled:
        return
    try:
        with db.get_cursor() as cur:
            cur.execute(_DDL)
        _schema_ok = True
    except Exception as exc:  # noqa: BLE001
        log.error("No se pudo crear webhook_eventos: %s", exc)


def _guardar(canal: str, topic, resource, user_id) -> int | None:
    """Inserta el evento recibido y devuelve su id."""
    if not settings.mysql_enabled:
        return None  # staging opción A: sin MySQL; el evento vive en Supabase
    _asegurar_schema()
    try:
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO webhook_eventos (canal, topic, resource, user_id, recibido)
                   VALUES (%s, %s, %s, %s, %s)""",
                (canal, topic, resource, str(user_id) if user_id else None,
                 datetime.now(timezone.utc)),
            )
            return cur.lastrowid
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo guardar webhook: %s", exc)
        return None


def _actualizar(evento_id: int, sku=None, resultado=None) -> None:
    if not settings.mysql_enabled:
        return
    try:
        with db.get_cursor() as cur:
            cur.execute(
                "UPDATE webhook_eventos SET procesado=1, sku=%s, resultado=%s WHERE id=%s",
                (sku, (resultado or "")[:255], evento_id),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo actualizar webhook %s: %s", evento_id, exc)


# ── Dual-write a Supabase (ops.webhook_events) ────────────────────────────────

def _dual_write_activo() -> bool:
    return settings.supabase_dual_write and sdb.disponible()


def _guardar_supabase(canal: str, payload: dict[str, Any]) -> int | None:
    """Escribe el evento en ops.webhook_events (idempotente). Devuelve su id.

    Llave de idempotencia = (env, canal, topic, external_id, delivery_id):
      - external_id = el `resource` de ML (p. ej. '/items/MLM123').
      - delivery_id = el `_id` de la notificación de ML; si no viene, hash del
        payload — dos entregas idénticas colapsan en una sola fila.
    Si el INSERT choca con el UNIQUE, la BD lo descarta (ON CONFLICT DO NOTHING)
    y aquí devolvemos None: el conteo de duplicados sale gratis del rowcount.
    """
    if not _dual_write_activo():
        return None
    try:
        topic = str(payload.get("topic") or "desconocido")
        external_id = str(payload.get("resource") or "sin-resource")
        delivery_id = str(
            payload.get("_id")
            or hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]
        )
        fila = sdb.execute_returning(
            """insert into ops.webhook_events
                 (env, canal, topic, external_id, delivery_id, cuenta, payload)
               values (%s, %s, %s, %s, %s, %s, %s::jsonb)
               on conflict do nothing
               returning id""",
            (settings.app_env, canal, topic, external_id, delivery_id,
             str(payload.get("user_id") or "") or None, json.dumps(payload)),
        )
        if fila is None:
            log.info("Webhook duplicado descartado por idempotencia: %s %s", topic, external_id)
            return None
        return int(fila["id"])
    except Exception as exc:  # noqa: BLE001
        # Regla del dual-write: NUNCA romper la operación por un fallo del espejo.
        log.warning("Dual-write a Supabase falló (la operación continúa): %s", exc)
        _registrar_issue("webhook_eventos", f"dual-write fallo: {exc}")
        return None


def _actualizar_supabase(sb_id: int | None, sku=None, resultado=None) -> None:
    if not sb_id or not _dual_write_activo():
        return
    try:
        sdb.execute(
            """update ops.webhook_events
               set procesado = true, sku = %s, resultado = %s, procesado_at = now()
               where id = %s""",
            (sku, (resultado or "")[:255], sb_id),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo actualizar ops.webhook_events %s: %s", sb_id, exc)


def _registrar_issue(tabla: str, motivo: str) -> None:
    """Anota un problema del dual-write en ops.migration_issues (best-effort)."""
    try:
        sdb.execute(
            """insert into ops.migration_issues (fase, tabla_origen, motivo)
               values ('F3-dualwrite', %s, %s)""",
            (tabla, motivo[:500]),
        )
    except Exception:  # noqa: BLE001
        pass  # si ni esto se pudo, ya quedó en los logs del paso anterior


async def _procesar_ml(evento_id: int | None, sb_id: int | None, payload: dict[str, Any]) -> None:
    """Procesa la notificación en segundo plano (tras responder 200)."""
    topic = payload.get("topic")
    resource = payload.get("resource") or ""
    sku = None
    resultado = ""
    try:
        if topic in ("items", "items_prices", "stock_locations") and "/items/" in resource:
            item_id = resource.rsplit("/", 1)[-1]
            r = await inventario.refrescar_ml_item_id(item_id)
            sku = r.get("sku")
            resultado = "item actualizado" if r.get("ok") else f"item: {r.get('motivo')}"
        elif topic == "orders_v2" and "/orders/" in resource:
            # Una venta cambia el stock: refrescamos los ítems de la orden.
            order_id = resource.rsplit("/", 1)[-1]
            items = await meli.obtener_orden_items(order_id)
            actualizados = []
            for it in items:
                r = await inventario.refrescar_ml_item_id(it)
                if r.get("ok"):
                    actualizados.append(r.get("sku"))
            sku = ",".join(a for a in actualizados if a) or None
            resultado = f"venta: {len(actualizados)} ítem(s) resincronizados"
        else:
            resultado = f"topic '{topic}' registrado (sin acción de stock)"
    except Exception as exc:  # noqa: BLE001
        resultado = f"error: {exc}"
    if evento_id:
        _actualizar(evento_id, sku, resultado)
    _actualizar_supabase(sb_id, sku, resultado)
    log.info("Webhook ML [%s] %s → %s", topic, resource, resultado)


@router.post("/ml")
async def recibir_ml(request: Request, background: BackgroundTasks):
    """Recibe la notificación de Mercado Libre. Responde 200 de inmediato."""
    # Si el registro está pausado, respondemos 200 pero NO guardamos ni procesamos.
    if not _registro_activo:
        return {"ok": True, "registro": "pausado"}
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = {}
    evento_id = _guardar("mercado_libre", payload.get("topic"),
                         payload.get("resource"), payload.get("user_id"))
    # Espejo idempotente en Supabase (piloto). Nunca rompe el 200.
    sb_id = _guardar_supabase("mercado_libre", payload)
    # Procesar aparte para responder rápido (ML reintenta si tardas).
    background.add_task(_procesar_ml, evento_id, sb_id, payload)
    return {"ok": True}


@router.api_route("/pausar", methods=["GET", "POST"])
async def pausar():
    """Pausa el guardado de notificaciones en la tabla (y su procesamiento)."""
    global _registro_activo
    _registro_activo = False
    log.info("Registro de webhooks PAUSADO.")
    return {"ok": True, "registro_activo": False,
            "nota": "Las notificaciones de ML se responden 200 pero NO se guardan."}


@router.api_route("/reanudar", methods=["GET", "POST"])
async def reanudar():
    """Reanuda el guardado de notificaciones en la tabla."""
    global _registro_activo
    _registro_activo = True
    log.info("Registro de webhooks REANUDADO.")
    return {"ok": True, "registro_activo": True}


@router.get("/estado")
async def estado_registro():
    """Estado del receptor: registro, flags del piloto y ambiente."""
    return {
        "registro_activo": _registro_activo,
        "ambiente": settings.app_env,
        "mysql_enabled": settings.mysql_enabled,
        "supabase_dual_write": settings.supabase_dual_write,
        "supabase_disponible": sdb.disponible(),
    }


@router.get("/ml")
async def ping_ml():
    """Prueba de accesibilidad (ML valida que la URL responda)."""
    return {"ok": True, "servicio": "webhook Mercado Libre", "listo": True}


@router.get("/ml/log")
async def log_ml(limite: int = Query(50, ge=1, le=200)):
    """Últimas notificaciones recibidas (para ver tus pruebas en vivo)."""
    _asegurar_schema()
    try:
        eventos = db.fetch_all(
            "SELECT * FROM webhook_eventos ORDER BY id DESC LIMIT %s", (limite,)
        )
    except Exception:  # noqa: BLE001
        eventos = []
    return {"total": len(eventos), "eventos": eventos}


@router.get("/notificaciones")
async def notificaciones(limite: int = Query(20, ge=1, le=100)):
    """Feed compacto para la campana de notificaciones del frontend."""
    _asegurar_schema()
    try:
        eventos = db.fetch_all(
            """SELECT id, canal, topic, resource, cuenta, sku, resultado, recibido
               FROM webhook_eventos ORDER BY id DESC LIMIT %s""",
            (limite,),
        )
        total_hoy = db.fetch_scalar(
            "SELECT COUNT(*) FROM webhook_eventos WHERE recibido >= CURDATE()"
        ) or 0
    except Exception:  # noqa: BLE001
        eventos, total_hoy = [], 0
    return {"eventos": eventos, "total_hoy": int(total_hoy)}
