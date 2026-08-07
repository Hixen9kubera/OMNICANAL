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

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request

from config import settings
from core.seguridad import requiere_api_key
from services import db, inventario, meli, pedidos_ml, stock_full
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


# user_id de ML → cuenta interna. El webhook trae el vendedor dueño del
# movimiento, y cada cuenta tiene su propio token.
_USER_A_CUENTA = {"3072519654": "BEKURA", "3064478475": "SANCORFASHION"}


def _cuenta_por_user(user_id) -> str:
    return _USER_A_CUENTA.get(str(user_id or ""), "BEKURA")


def _guardar(canal: str, topic, resource, user_id) -> int | None:
    """Inserta el evento recibido y devuelve su id."""
    if not settings.mysql_enabled:
        return None  # staging opción A: sin MySQL; el evento vive en Supabase
    if not settings.webhook_guarda_mysql:
        # Webhook DESVINCULADO de MySQL (pedido de Brandon 2026-07-17): se
        # procesa al vuelo; el único registro persistente es el espejo de
        # Supabase (si supabase_dual_write está encendido).
        return None
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
    if not settings.mysql_enabled or not settings.webhook_guarda_mysql:
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


async def _procesar_ml(evento_id: int | None, payload: dict[str, Any]) -> None:
    """Procesa la notificación en segundo plano (tras responder 200).

    El espejo a Supabase también vive AQUÍ (no en el receptor) y corre en un
    HILO (asyncio.to_thread): psycopg2 es I/O bloqueante y ejecutarlo directo
    en la corrutina congela el event loop entero — la respuesta 200 no alcanza
    a salir y las demás peticiones se atoran. [Defecto detectado en pruebas
    2026-07-15: con Supabase caído, cada webhook tardaba 20 s aun estando "en
    background"; con to_thread el 200 sale en <1 s y el timeout ocurre aparte.]
    """
    sb_id = await asyncio.to_thread(_guardar_supabase, "mercado_libre", payload)
    topic = payload.get("topic")
    resource = payload.get("resource") or ""
    sku = None
    resultado = ""
    try:
        # MOVIMIENTOS DE BODEGA FULL: ML nos avisa en SEGUNDOS y lo estábamos
        # tirando ("registrado sin acción"). Resolviendo la operación se sabe si
        # llegó mercancía (TRANSFER_DELIVERY → salió de nuestra bodega, hay que
        # restar en Woo) o si es un movimiento interno del marketplace.
        if topic == "fbm_stock_operations" and "/operations/" in resource:
            op_id = resource.rsplit("/", 1)[-1]
            cuenta = _cuenta_por_user(payload.get("user_id"))
            r = await stock_full.procesar_operacion(op_id, cuenta)
            sku = r.get("sku")
            resultado = (f"FULL {r.get('tipo')} x{r.get('cantidad')} → Woo "
                         f"{r.get('antes')}→{r.get('despues')}" if r.get("ok") and r.get("sku")
                         else f"FULL: {r.get('accion') or r.get('motivo')}")
        # OJO: ML manda el topic con GUION MEDIO (`stock-locations`) y el recurso
        # es `/user-products/…`, no `/items/…`. El filtro viejo pedía guion bajo
        # y un item, así que NUNCA entraba (verificado en logs de producción).
        elif topic in ("items", "items_prices", "stock_locations", "stock-locations") \
                and ("/items/" in resource or "/user-products/" in resource):
            if "/user-products/" in resource:
                # Cambió el reparto bodega-propia / bodega-ML de un user product.
                # El movimiento en sí llega por `fbm_stock_operations` (con tipo y
                # cantidad); aquí solo se deja constancia.
                resultado = "stock-locations: reparto propio/FULL notificado"
            elif settings.sync_enabled:
                # Refresco del espejo canal_inventario: es "sincronización de datos
                # con ML" — respeta el interruptor global (modo puros pedidos).
                item_id = resource.rsplit("/", 1)[-1]
                r = await inventario.refrescar_ml_item_id(item_id)
                sku = r.get("sku")
                resultado = "item actualizado" if r.get("ok") else f"item: {r.get('motivo')}"
            else:
                resultado = "item: sync apagado (modo pedidos)"
        elif topic == "orders_v2" and "/orders/" in resource:
            # La ORDEN siempre se trae (sin ella no hay pedido); el refresco de
            # los ítems en canal_inventario es sync y respeta el interruptor.
            order_id = resource.rsplit("/", 1)[-1]
            orden = await meli.obtener_orden(order_id)
            actualizados = []
            if settings.sync_enabled:
                items = [i["item_id"] for i in (orden or {}).get("items", [])
                         if i.get("item_id")]
                for it in items:
                    r = await inventario.refrescar_ml_item_id(it)
                    if r.get("ok"):
                        actualizados.append(r.get("sku"))
            sku = (",".join(a for a in actualizados if a)
                   or ",".join(i.get("sku") or "" for i in (orden or {}).get("items", []))
                   or None)
            resultado = (f"venta: {len(actualizados)} ítem(s) resincronizados"
                         if settings.sync_enabled else "venta (modo pedidos)")
            # PEDIDOS: la venta queda como pedido de Woo con el precio REAL
            # congelado. En modo registro (descuenta_stock=false) el pedido
            # NO baja inventario — Odoo sigue siendo el maestro.
            if settings.pedidos_wc_enabled and orden:
                rp = await pedidos_ml.sincronizar(
                    order_id, orden=orden,
                    proteger_stock=not settings.pedidos_wc_descuenta_stock)
                if rp.get("ok"):
                    resultado += (f" · pedido WC #{rp['wc_order_id']} "
                                  f"{rp['accion']} ({rp['estado_wc']})")
                    if rp.get("sin_mapear"):
                        resultado += f" · SKU sin mapear: {','.join(rp['sin_mapear'])}"
                else:
                    resultado += f" · pedido WC falló: {rp.get('motivo')}"
        else:
            resultado = f"topic '{topic}' registrado (sin acción de stock)"
    except Exception as exc:  # noqa: BLE001
        resultado = f"error: {exc}"
    if evento_id:
        _actualizar(evento_id, sku, resultado)
    await asyncio.to_thread(_actualizar_supabase, sb_id, sku, resultado)
    # application_id: identifica QUÉ app de ML nos manda este aviso (hay varias
    # apps en las cuentas: 2 del dashboard apuntan a Make; la que apunta aquí
    # es la que importa ahora que Make se va a abandonar).
    log.info("Webhook ML [%s] %s (app=%s, user=%s) → %s", topic, resource,
             payload.get("application_id"), payload.get("user_id"), resultado)


@router.post("/ml")
async def recibir_ml(request: Request, background: BackgroundTasks):
    """
    Recibe la notificación de Mercado Libre. Responde 200 de inmediato.

    GUARDA ABSOLUTA: pase lo que pase aquí dentro, a ML se le responde 200.
    Si le devolvemos otra cosa, reintenta con backoff durante 1 hora y después
    DESHABILITA el topic — y a partir de ese momento se dejan de capturar
    ventas reales sin que aparezca ningún error. El síntoma es silencioso: solo
    dejan de entrar pedidos.

    Por eso el cuerpo entero va dentro de un try. Un fallo de MySQL, de Supabase
    o del parseo se registra en logs, pero NUNCA cambia la respuesta.
    """
    try:
        # Si el registro está pausado, respondemos 200 pero NO guardamos ni procesamos.
        if not _registro_activo:
            return {"ok": True, "registro": "pausado"}
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            payload = {}
        evento_id = _guardar("mercado_libre", payload.get("topic"),
                             payload.get("resource"), payload.get("user_id"))
        # Procesar aparte para responder rápido (ML reintenta si tardas). El espejo
        # idempotente a Supabase ocurre dentro del background por la misma razón.
        background.add_task(_procesar_ml, evento_id, payload)
        return {"ok": True}
    except Exception:  # noqa: BLE001 — jamás propagar: ver GUARDA ABSOLUTA
        log.exception("Fallo recibiendo el webhook de ML; se responde 200 igual "
                      "para no perder la notificación por reintentos agotados.")
        return {"ok": True, "nota": "error registrado en logs"}


@router.api_route("/pausar", methods=["GET", "POST"], dependencies=[Depends(requiere_api_key)])
async def pausar():
    """Pausa el guardado de notificaciones en la tabla (y su procesamiento)."""
    global _registro_activo
    _registro_activo = False
    log.info("Registro de webhooks PAUSADO.")
    return {"ok": True, "registro_activo": False,
            "nota": "Las notificaciones de ML se responden 200 pero NO se guardan."}


@router.api_route("/reanudar", methods=["GET", "POST"], dependencies=[Depends(requiere_api_key)])
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
        "pedidos_wc": settings.pedidos_wc_enabled,
        "pedidos_descuentan_stock": settings.pedidos_wc_descuenta_stock,
        "webhook_guarda_mysql": settings.webhook_guarda_mysql,
        "odoo_watch": settings.odoo_watch_enabled,
    }


@router.get("/ml")
async def ping_ml():
    """Prueba de accesibilidad (ML valida que la URL responda)."""
    return {"ok": True, "servicio": "webhook Mercado Libre", "listo": True}


# ── Lecturas: MySQL (hoy) o Supabase (Fase 5, tras flag) ─────────────────────

def _leer_de_supabase() -> bool:
    """La lectura va a Supabase si el flag lo pide o si no hay MySQL (staging)."""
    return (settings.supabase_read_webhooks or not settings.mysql_enabled) and sdb.disponible()


def _eventos_supabase(limite: int) -> list[dict[str, Any]]:
    """Lee ops.webhook_events con la MISMA forma que la tabla MySQL (la campana
    del frontend no distingue el origen): external_id→resource, recibido_at→recibido."""
    return sdb.fetch_all(
        """select id, canal, topic, external_id as resource, cuenta, sku,
                  procesado, resultado, recibido_at as recibido
           from ops.webhook_events order by id desc limit %s""",
        (limite,),
    )


@router.get("/ml/log")
async def log_ml(limite: int = Query(50, ge=1, le=200)):
    """Últimas notificaciones recibidas (para ver tus pruebas en vivo)."""
    if _leer_de_supabase():
        try:
            eventos = await asyncio.to_thread(_eventos_supabase, limite)
            return {"total": len(eventos), "eventos": eventos, "origen": "supabase"}
        except Exception as exc:  # noqa: BLE001
            log.warning("lectura Supabase falló, caigo a MySQL: %s", exc)
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
    if _leer_de_supabase():
        try:
            eventos = await asyncio.to_thread(_eventos_supabase, limite)
            total_hoy = await asyncio.to_thread(
                sdb.fetch_scalar,
                "select count(*) from ops.webhook_events where recibido_at >= current_date",
            )
            return {"eventos": eventos, "total_hoy": int(total_hoy or 0), "origen": "supabase"}
        except Exception as exc:  # noqa: BLE001
            log.warning("lectura Supabase falló, caigo a MySQL: %s", exc)
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


# ═══════════════════════════════════════════════════════════════════════════
# TIKTOK SHOP — receptor en modo OBSERVACIÓN
# ═══════════════════════════════════════════════════════════════════════════
#
# FASE 1 A PROPÓSITO: recibe, verifica firma, registra en logs y NO TOCA LA
# BASE DE DATOS. Ni una tabla, ni un pedido, ni stock.
#
# Por qué así y no completo de una vez: no sabemos qué manda TikTok de verdad.
# La consola ofrece cuatro temas (tipos 2,3,4,5) y ninguno es "pedido creado",
# así que el catálogo real solo se descubre viéndolo llegar. Es lo mismo que se
# hizo con M2E — loguear el crudo hasta que la primera venta real confirme el
# esquema — y evita diseñar tablas contra un formato imaginado.
#
# El plan de Brandon es observar DOS SEMANAS después de publicar 300 productos.
# Con eso se decide qué persistir, en vez de adivinarlo.
from collections import deque

# Anillo en MEMORIA. Se pierde al reiniciar y está bien: para persistir hace
# falta saber qué se persiste, que es justo lo que este receptor va a averiguar.
_TIKTOK_LOG: deque = deque(maxlen=300)


def _firma_tiktok_ok(cuerpo: bytes, cabeceras: dict) -> bool | None:
    """
    ¿La firma corresponde? True/False, o None si no se pudo evaluar.

    ⚠️ HOY SOLO OBSERVA: el resultado se registra pero NO se rechaza nada. El
    algoritmo exacto de TikTok (sobre qué cadena se calcula el HMAC, con qué
    separadores) todavía no está confirmado contra su documentación, y un
    verificador mal implementado tiraría eventos legítimos sin dejar rastro.

    Cuando la investigación confirme el esquema, esto pasa a rechazar — y ahí sí
    deja de ser opcional: la URL es pública y sin firma cualquiera puede
    inyectar eventos falsos.
    """
    secreto = (settings.tiktok_app_secret or "").encode()
    if not secreto:
        return None
    recibida = (cabeceras.get("authorization")
                or cabeceras.get("x-tts-signature")
                or cabeceras.get("signature") or "").strip()
    if not recibida:
        return None
    try:
        calculada = hmac.new(secreto, cuerpo, hashlib.sha256).hexdigest()
        return hmac.compare_digest(calculada, recibida.lower())
    except Exception:  # noqa: BLE001
        return None


@router.post("/tiktok")
async def recibir_tiktok(request: Request):
    """
    Recibe la notificación de TikTok Shop. Responde 200 SIEMPRE.

    GUARDA ABSOLUTA, igual que en ML: si devolvemos otra cosa, TikTok reintenta
    y puede terminar deshabilitando la suscripción. El síntoma sería silencioso
    — simplemente dejan de llegar eventos — así que el cuerpo entero va dentro
    de un try y ningún fallo cambia la respuesta.
    """
    try:
        crudo = await request.body()
        cab = {k.lower(): v for k, v in request.headers.items()}
        try:
            payload = json.loads(crudo or b"{}")
        except Exception:  # noqa: BLE001
            payload = {"_no_json": (crudo or b"")[:2000].decode("utf-8", "replace")}

        firma = _firma_tiktok_ok(crudo, cab)
        evento = {
            "recibido": datetime.now(timezone.utc).isoformat(),
            "tipo": payload.get("type"),
            "shop_id": payload.get("shop_id"),
            "timestamp": payload.get("timestamp"),
            "firma_ok": firma,          # None = no evaluable (aún no confirmado)
            "bytes": len(crudo or b""),
            "cabeceras": {k: v for k, v in cab.items()
                          if k.startswith(("x-tts", "content-type", "user-agent"))},
            "payload": payload,
        }
        _TIKTOK_LOG.append(evento)
        # A los logs de Railway, completo: es el único registro de la fase 1.
        log.info("TIKTOK webhook tipo=%s shop=%s firma=%s bytes=%s :: %s",
                 evento["tipo"], evento["shop_id"], firma, evento["bytes"],
                 json.dumps(payload, ensure_ascii=False)[:1500])
        return {"code": 0, "message": "success"}
    except Exception:  # noqa: BLE001 — jamás propagar: ver GUARDA ABSOLUTA
        log.exception("Fallo recibiendo el webhook de TikTok; se responde 200 "
                      "igual para no perder la suscripción por reintentos.")
        return {"code": 0, "message": "success"}


@router.get("/tiktok")
async def ping_tiktok():
    """Prueba de accesibilidad. TikTok valida que la URL responda al guardarla."""
    return {"ok": True, "canal": "tiktok", "modo": "observacion",
            "persistencia": "ninguna — solo logs",
            "eventos_en_memoria": len(_TIKTOK_LOG)}


@router.get("/tiktok/log", dependencies=[Depends(requiere_api_key)])
async def log_tiktok(limite: int = Query(50, ge=1, le=300)):
    """
    Lo que TikTok ha mandado, para la pestaña de administración.

    Va CERRADO (a diferencia de POST /tiktok, que TikTok no puede autenticar):
    el payload trae datos de pedido y el scope `seller.order.info` viene marcado
    por TikTok como "Datos confidenciales — contiene información personal de los
    clientes".
    """
    eventos = list(_TIKTOK_LOG)[-limite:]
    eventos.reverse()
    tipos: dict[str, int] = {}
    for e in _TIKTOK_LOG:
        tipos[str(e.get("tipo"))] = tipos.get(str(e.get("tipo")), 0) + 1
    return {"total_en_memoria": len(_TIKTOK_LOG), "por_tipo": tipos,
            "eventos": eventos}


@router.get("/activos", dependencies=[Depends(requiere_api_key)])
async def webhooks_activos():
    """
    Panel de administración: qué webhooks existen y cómo están funcionando.

    Un canal puede estar en tres situaciones distintas y conviene no
    confundirlas: SIN CONSTRUIR (no hay endpoint), CONSTRUIDO PERO APAGADO
    (existe y su interruptor está en false), o VIVO.
    """
    base = (settings.tiktok_redirect_uri or "").split("/api/")[0]
    return {
        "canales": [
            {
                "canal": "mercado_libre",
                "estado": "vivo" if _registro_activo else "pausado",
                "url": f"{base}/api/webhooks/ml",
                "persistencia": ("mysql+supabase" if settings.webhook_guarda_mysql
                                 else "supabase" if settings.supabase_dual_write
                                 else "ninguna — se procesa al vuelo"),
                "procesa_pedidos": settings.pedidos_wc_enabled,
                "descuenta_stock": settings.pedidos_wc_descuenta_stock,
            },
            {
                "canal": "tiktok",
                "estado": "observacion",
                "url": f"{base}/api/webhooks/tiktok",
                "persistencia": "ninguna — solo logs (fase 1)",
                "eventos_en_memoria": len(_TIKTOK_LOG),
                "app_configurada": bool(settings.tiktok_app_key),
                "canal_encendido": settings.tiktok_enabled,
            },
            {
                "canal": "amazon",
                "estado": "sin webhook — es SONDEO cada 5 min (SP-API)",
                "url": None,
            },
            {
                "canal": "temu",
                "estado": "sin webhook — es SONDEO cada 10 min (M2E Cloud)",
                "url": None,
            },
        ],
        "ambiente": settings.app_env,
    }
