"""
fanout_stock.py — Fan-out del stock DROP hacia los canales.

PROBLEMA QUE RESUELVE: hoy una venta no-FULL descuenta en WooCommerce (30→29)
pero NADIE le avisa a los demás canales — SANCORFASHION y Amazon siguen
ofreciendo 30. Verificado 2026-07-24: `sync_woo.py` solo empuja Odoo→Woo y el
sync de 15 min solo LEE los canales. Riesgo real de sobreventa.

QUÉ HACE: cuando el stock DROP de un SKU cambia, lo replica a todas las
publicaciones ACTIVAS y NO-FULL de ese SKU en todos los canales.

    venta en BEKURA (no-FULL) → Woo 30→29 → [fan-out] → SANCOR 29 · Amazon 29

DECISIONES DE DISEÑO (cada una nace de un incidente real o de una regla viva):

  1. SE ENCOLA EL SKU, NUNCA UN DELTA. Al procesarlo se LEE el stock actual de
     Woo. Así el flujo es idempotente por naturaleza (un mensaje repetido da el
     mismo resultado) y auto-sanable (si un evento se pierde, el siguiente
     corrige). Con deltas ("resta 1") un duplicado descuadra el inventario para
     siempre — y ML manda webhooks EN RÁFAGA (regla 6 de CLAUDE.md).

  2. SOLO PUBLICACIONES `active`. Escribir stock/precio a una publicación
     PAUSADA la REACTIVA (ML avisa: "se reactivaron porque hiciste cambios en su
     stock o estado"; pasado real con CAM-0030 el 2026-07-24). Además una
     pausada no vende: no necesita stock.

  3. SOLO ítems NO-FULL. Las piezas de FULL/FBA viven en la bodega del
     marketplace, no son del almacén compartido, y ML no deja fijarles cantidad.

  4. COMPARAR ANTES DE ESCRIBIR. Si el canal ya tiene el valor, no se escribe:
     ahorra rate-limit y MATA EL ECO (al escribir en ML llega de vuelta un
     webhook `items` que volvería a encolar el SKU; como el valor ya coincide,
     el ciclo muere solo).

  5. DEBOUNCE por SKU: las ráfagas de la misma venta se colapsan en UNA sola
     escritura por canal.

  6. NUNCA rompe la venta: se invoca fire-and-forget después de que el pedido ya
     quedó guardado; cualquier excepción muere aquí dentro.

FLAGS (Railway, apagable sin deploy):
  FANOUT_ENABLED   default False — nace apagado.
  FANOUT_DRY_RUN   default True  — calcula y REGISTRA lo que haría, sin escribir.
  FANOUT_CANALES   CSV de canales habilitados para escribir (encendido gradual).
  FANOUT_RESERVA   piezas de colchón que NO se publican (cubre la ventana de
                   latencia entre la venta y la escritura).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.fanout")

# Ventana de coalescing: las ráfagas del mismo SKU se funden en una escritura.
DEBOUNCE_S = 5.0
_TICK_S = 1.0          # cada cuánto revisa el worker si algo ya "reposó"
_EVENTOS_MAX = 300     # ring buffer para la pantalla de monitoreo

# Cada canal nombra distinto "está vendiendo": ML usa `active` (paused/closed/
# under_review/inactive NO venden), Amazon usa `PUBLISHED` (ACCEPTED aún no
# publica) y WooCommerce `publish`. Sin esta normalización el fan-out ignoraba
# las 1,616 publicaciones vivas de Amazon — que son el destino DROP más grande.
_SITUACIONES_VIVAS = {"active", "published", "publish"}

_pendientes: dict[str, dict[str, Any]] = {}   # sku → {listo_en, motivo, encolado}
_lock = threading.Lock()
_worker_iniciado = False
_eventos: deque[dict[str, Any]] = deque(maxlen=_EVENTOS_MAX)
_contadores: dict[str, int] = {
    "encolados": 0, "procesados": 0, "escrituras": 0, "simuladas": 0,
    "sin_cambio": 0, "errores": 0, "omitidos_full": 0, "omitidos_pausados": 0,
}


# ── Configuración ────────────────────────────────────────────────────────────

def habilitado() -> bool:
    return bool(getattr(settings, "fanout_enabled", False))


def dry_run() -> bool:
    """True = NO escribe en los canales, solo registra lo que haría."""
    return bool(getattr(settings, "fanout_dry_run", True))


def _canales_activos() -> set[str] | None:
    """Canales con escritura habilitada. None = todos (cuando el CSV va vacío)."""
    csv = (getattr(settings, "fanout_canales", "") or "").strip()
    if not csv:
        return None
    return {c.strip().lower() for c in csv.split(",") if c.strip()}


def _reserva() -> int:
    try:
        return max(0, int(getattr(settings, "fanout_reserva", 0) or 0))
    except (TypeError, ValueError):
        return 0


# ── Lectura de la VERDAD (WooCommerce vía MySQL directo) ─────────────────────

def _stock_drop(sku: str) -> int | None:
    """
    Stock DROP del SKU = `_stock` en WooCommerce (que ya trae el de Odoo).

    Se lee por MySQL directo (wp_db) a propósito: la REST de Woo devuelve 403
    intermitente por el CDN de Hostinger (pendiente #1) y un fallo de lectura
    NUNCA debe convertirse en un stock inventado.
    """
    from services import wp_db
    P = wp_db._prefix()
    rows = wp_db._fetch_all(
        f"""SELECT st.meta_value AS stock
            FROM {P}postmeta s
            JOIN {P}posts p ON p.ID = s.post_id AND p.post_status <> 'trash'
            LEFT JOIN {P}postmeta st ON st.post_id = p.ID AND st.meta_key = '_stock'
            WHERE s.meta_key = '_sku' AND s.meta_value = %s
            LIMIT 1""",
        (sku,),
    )
    if not rows:
        return None
    v = rows[0].get("stock")
    if v in (None, ""):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _destinos(sku: str) -> list[dict[str, Any]]:
    """
    Publicaciones que DEBEN recibir el stock DROP: activas y no-FULL.

    Fuente: `canal_inventario` (espejo de canales, refrescado por el sync de 15
    min y por los webhooks de ML). Devuelve también los descartes, para que la
    pantalla explique POR QUÉ un canal no recibió nada.
    """
    from services import db
    # OJO: NO se filtra por item_id. En Mercado Libre el identificador es el
    # `item_id` (MLM…), pero en AMAZON es el PROPIO SKU (la Listings Items API
    # direcciona /items/{sellerId}/{sku}) y sus 1,631 filas tienen item_id NULL.
    # Filtrar por item_id dejaba fuera el canal DROP más grande.
    filas = db.fetch_all(
        """SELECT sku, canal, cuenta, item_id, stock_real, stock_full, stock_fba,
                  es_full, situacion
           FROM canal_inventario
           WHERE sku = %s""",
        (sku,),
    )
    salida: list[dict[str, Any]] = []
    for f in filas:
        situacion = (f.get("situacion") or "").lower()
        # `es_full` es la bandera CONFIABLE (logistic_type=fulfillment / canal AFN):
        # está poblada en los 3 canales. El stock en bodega es solo red de
        # seguridad — hay 319 publicaciones FULL con 0 piezas que el heurístico
        # por stock clasificaría mal (y les escribiríamos stock que ML no acepta).
        es_full = bool(f.get("es_full")) or int(f.get("stock_full") or 0) > 0 \
            or int(f.get("stock_fba") or 0) > 0
        canal = (f.get("canal") or "").lower()
        # Identificador de escritura: ML → item_id (MLM…); Amazon → el SKU.
        identificador = f.get("item_id") or (sku if canal == "amazon" else None)
        if es_full:
            motivo = "FULL/FBA (bodega del marketplace, no se toca)"
        elif situacion not in _SITUACIONES_VIVAS:
            motivo = f"situacion={situacion or 'desconocida'} (escribirle la REACTIVARÍA)"
        elif not identificador:
            motivo = "sin identificador de publicación en el canal"
        else:
            motivo = None
        salida.append({
            "canal": f.get("canal"), "cuenta": f.get("cuenta"),
            "item_id": identificador,
            "stock_actual_canal": int(f.get("stock_real") or 0),
            "omitido_por": motivo,
        })
    return salida


# ── Escritores por canal (solo se usan FUERA de dry-run) ─────────────────────

def _escribir_ml(cuenta: str, item_id: str, cantidad: int) -> tuple[bool, str]:
    """PUT del stock a una publicación de Mercado Libre."""
    import httpx
    from services import meli
    token = meli._access_token(cuenta)
    if not token:
        return False, f"sin token de {cuenta}"
    try:
        r = httpx.put(
            f"https://api.mercadolibre.com/items/{item_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"available_quantity": int(cantidad)}, timeout=30.0,
        )
        if r.status_code == 200:
            return True, "ok"
        return False, f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _escribir_amazon(cuenta: str, sku: str, cantidad: int) -> tuple[bool, str]:
    """
    PATCH del stock a un listing de Amazon (MFN/DROP).

    En Amazon el identificador NO es un item_id: la Listings Items API direcciona
    /items/{sellerId}/{sku}. El stock vive en `fulfillment_availability` con el
    canal DEFAULT (= MFN, nuestro almacén). Los FBA no se tocan (los filtra
    `_destinos`): esa bodega la administra Amazon.
    """
    import asyncio

    import httpx
    from services import amazon
    try:
        token = asyncio.run(amazon._access_token())
    except RuntimeError:  # ya hay loop (llamado desde async): usar uno propio
        loop = asyncio.new_event_loop()
        try:
            token = loop.run_until_complete(amazon._access_token())
        finally:
            loop.close()
    except Exception as exc:  # noqa: BLE001
        return False, f"token Amazon: {exc}"
    if not token:
        return False, "sin token de Amazon"
    try:
        r = httpx.patch(
            f"{settings.amazon_sp_api_endpoint}/listings/2021-08-01/items/"
            f"{settings.amazon_seller_id}/{sku}",
            params={"marketplaceIds": settings.amazon_marketplace_id},
            headers={"x-amz-access-token": token},
            json={"productType": "PRODUCT", "patches": [{
                "op": "replace",
                "path": "/attributes/fulfillment_availability",
                "value": [{"fulfillment_channel_code": "DEFAULT",
                           "quantity": int(cantidad)}],
            }]},
            timeout=30.0,
        )
        if r.status_code in (200, 202):
            estado = (r.json() or {}).get("status", "")
            if str(estado).upper() == "INVALID":
                return False, f"Amazon rechazó: {str(r.json().get('issues'))[:120]}"
            return True, f"ok ({estado or r.status_code})"
        return False, f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


_ESCRITORES = {"mercado_libre": _escribir_ml, "amazon": _escribir_amazon}
# temu / tiktok: se suman aquí cuando su escritura por M2E esté probada. En
# dry-run igual aparecen en el plan, así se ve el alcance completo.


# ── Núcleo: calcular el plan de un SKU ───────────────────────────────────────

def plan(sku: str) -> dict[str, Any]:
    """
    Qué haría el fan-out con este SKU AHORA MISMO. No escribe ni encola nada:
    es lo que consume el dry-run, el endpoint de simulación y el worker.
    """
    stock = _stock_drop(sku)
    if stock is None:
        return {"sku": sku, "ok": False, "motivo": "sin stock legible en WooCommerce",
                "acciones": []}
    objetivo = max(0, stock - _reserva())
    canales_ok = _canales_activos()
    acciones: list[dict[str, Any]] = []
    for d in _destinos(sku):
        accion = dict(d)
        accion["objetivo"] = objetivo
        if d["omitido_por"]:
            accion["accion"] = "omitir"
        elif d["stock_actual_canal"] == objetivo:
            accion["accion"] = "sin_cambio"
            accion["omitido_por"] = f"el canal ya tiene {objetivo}"
        elif canales_ok is not None and (d["canal"] or "").lower() not in canales_ok:
            accion["accion"] = "omitir"
            accion["omitido_por"] = f"canal '{d['canal']}' no habilitado en FANOUT_CANALES"
        elif (d["canal"] or "").lower() not in _ESCRITORES:
            accion["accion"] = "omitir"
            accion["omitido_por"] = f"sin escritor implementado para '{d['canal']}'"
        else:
            accion["accion"] = "escribir"
        acciones.append(accion)
    return {"sku": sku, "ok": True, "stock_drop": stock, "reserva": _reserva(),
            "objetivo": objetivo, "acciones": acciones}


_schema_listo = False


def _asegurar_schema() -> None:
    """
    Tabla LOCAL de bitácora del fan-out (MySQL kubera_ml).

    Local A PROPÓSITO, igual que `espejo_kubera_log`: es operación del panel, NO
    entra en la migración a la BD centralizada. Sin esto, cada deploy de Railway
    reinicia el proceso y se pierde todo el historial del dry-run.
    """
    global _schema_listo
    if _schema_listo:
        return
    from services import db
    try:
        with db.get_cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fanout_log (
                    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
                    ts          DATETIME NOT NULL,
                    sku         VARCHAR(100) NOT NULL,
                    motivo      VARCHAR(160),
                    dry_run     TINYINT(1) NOT NULL DEFAULT 1,
                    stock_drop  INT,
                    objetivo    INT,
                    canal       VARCHAR(40),
                    cuenta      VARCHAR(40),
                    item_id     VARCHAR(64),
                    accion      VARCHAR(20),
                    stock_canal INT,
                    resultado   VARCHAR(255),
                    ms          DECIMAL(10,1),
                    INDEX idx_fanout_ts (ts),
                    INDEX idx_fanout_sku (sku),
                    INDEX idx_fanout_accion (accion, ts)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
        _schema_listo = True
    except Exception as exc:  # noqa: BLE001
        log.warning("fanout_log: no se pudo asegurar el schema: %s", exc)


def _persistir(evento: dict[str, Any]) -> None:
    """Guarda una fila por ACCIÓN (así el dashboard filtra por canal/resultado)."""
    from services import db
    _asegurar_schema()
    try:
        filas = [
            (evento["ts_dt"], evento["sku"], (evento.get("motivo") or "")[:160],
             1 if evento["dry_run"] else 0, evento.get("stock_drop"),
             evento.get("objetivo"), a.get("canal"), a.get("cuenta"),
             str(a.get("item_id") or "")[:64], a.get("accion"),
             a.get("stock_actual_canal"),
             str(a.get("resultado") or a.get("omitido_por") or "")[:255],
             evento.get("ms"))
            for a in (evento.get("acciones") or [])
        ] or [(evento["ts_dt"], evento["sku"], (evento.get("motivo") or "")[:160],
               1 if evento["dry_run"] else 0, evento.get("stock_drop"),
               evento.get("objetivo"), None, None, "", "sin_destinos", None,
               str(evento.get("detalle") or "sin publicaciones vivas")[:255],
               evento.get("ms"))]
        with db.get_cursor() as cur:
            cur.executemany(
                """INSERT INTO fanout_log
                   (ts, sku, motivo, dry_run, stock_drop, objetivo, canal, cuenta,
                    item_id, accion, stock_canal, resultado, ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", filas)
    except Exception as exc:  # noqa: BLE001
        log.warning("fanout_log: no se pudo persistir %s: %s", evento.get("sku"), exc)


def _aplicar(sku: str, motivo: str) -> None:
    """Calcula el plan y (si no es dry-run) lo ejecuta. Registra siempre."""
    inicio = time.time()
    p = plan(sku)
    simulacion = dry_run()
    resultados: list[dict[str, Any]] = []
    for a in p.get("acciones", []):
        if a["accion"] != "escribir":
            _contadores["sin_cambio" if a["accion"] == "sin_cambio" else (
                "omitidos_full" if "FULL" in (a.get("omitido_por") or "")
                else "omitidos_pausados")] += 1
            resultados.append(a)
            continue
        if simulacion:
            _contadores["simuladas"] += 1
            resultados.append({**a, "resultado": "DRY-RUN (no se escribió)"})
            continue
        escritor = _ESCRITORES[(a["canal"] or "").lower()]
        ok, det = escritor(a["cuenta"], a["item_id"], a["objetivo"])
        _contadores["escrituras" if ok else "errores"] += 1
        resultados.append({**a, "resultado": ("ok" if ok else f"ERROR: {det}")})

    _contadores["procesados"] += 1
    ahora = datetime.now(timezone.utc)
    evento = {
        "ts": ahora.isoformat(timespec="seconds"),
        "ts_dt": ahora.replace(tzinfo=None),
        "sku": sku, "motivo": motivo, "dry_run": simulacion,
        "stock_drop": p.get("stock_drop"), "objetivo": p.get("objetivo"),
        "ok": p.get("ok"), "detalle": p.get("motivo"),
        "acciones": resultados, "ms": round((time.time() - inicio) * 1000, 1),
    }
    _eventos.appendleft({k: v for k, v in evento.items() if k != "ts_dt"})
    _persistir(evento)   # sobrevive a los deploys (el ring buffer no)
    escrituras = sum(1 for r in resultados if r.get("accion") == "escribir")
    log.info("fan-out %s%s: stock=%s objetivo=%s → %d destino(s) a escribir",
             sku, " [DRY-RUN]" if simulacion else "", p.get("stock_drop"),
             p.get("objetivo"), escrituras)


# ── Cola con debounce ────────────────────────────────────────────────────────

def _worker() -> None:
    """Drena los SKUs que ya 'reposaron' su ventana de debounce."""
    while True:
        try:
            time.sleep(_TICK_S)
            ahora = time.time()
            listos: list[tuple[str, str]] = []
            with _lock:
                for sku, info in list(_pendientes.items()):
                    if info["listo_en"] <= ahora:
                        listos.append((sku, info["motivo"]))
                        _pendientes.pop(sku, None)
            for sku, motivo in listos:
                try:
                    _aplicar(sku, motivo)
                except Exception as exc:  # noqa: BLE001
                    _contadores["errores"] += 1
                    log.warning("fan-out %s falló: %s", sku, exc)
        except Exception as exc:  # noqa: BLE001 — el worker NUNCA muere
            log.warning("worker de fan-out: %s", exc)


def _asegurar_worker() -> None:
    global _worker_iniciado
    if not _worker_iniciado:
        with _lock:
            if not _worker_iniciado:
                threading.Thread(target=_worker, daemon=True,
                                 name="fanout-stock").start()
                _worker_iniciado = True


def encolar(sku: str, motivo: str = "venta") -> None:
    """
    Pide replicar el stock DROP de `sku` a los canales. Fire-and-forget: solo
    encola y regresa — el camino crítico de la venta NUNCA se bloquea ni falla
    por esto. Re-encolar el mismo SKU dentro de la ventana solo REINICIA el
    debounce (las ráfagas se colapsan en una escritura).
    """
    try:
        if not habilitado() or not (sku or "").strip():
            return
        _asegurar_worker()
        with _lock:
            _pendientes[sku.strip()] = {"listo_en": time.time() + DEBOUNCE_S,
                                        "motivo": motivo}
            _contadores["encolados"] += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("fan-out encolar(%s): %s", sku, exc)


def encolar_varios(skus: list[str], motivo: str = "venta") -> None:
    for s in skus or []:
        encolar(s, motivo)


# ── Monitoreo ────────────────────────────────────────────────────────────────

def historial(limite: int = 100, solo_errores: bool = False) -> list[dict[str, Any]]:
    """Bitácora PERSISTIDA (sobrevive deploys). Es lo que pinta el dashboard."""
    from services import db
    _asegurar_schema()
    where = "WHERE resultado LIKE 'ERROR%%'" if solo_errores else ""
    try:
        return db.fetch_all(
            f"""SELECT ts, sku, motivo, dry_run, stock_drop, objetivo, canal,
                       cuenta, item_id, accion, stock_canal, resultado, ms
                FROM fanout_log {where} ORDER BY id DESC LIMIT %s""",
            (int(limite),))
    except Exception as exc:  # noqa: BLE001
        log.warning("fanout_log historial: %s", exc)
        return []


def resumen() -> dict[str, Any]:
    """Totales acumulados desde la tabla (no desde memoria)."""
    from services import db
    _asegurar_schema()
    try:
        por_accion = db.fetch_all(
            "SELECT accion, COUNT(*) n FROM fanout_log GROUP BY accion")
        por_canal = db.fetch_all(
            """SELECT canal, accion, COUNT(*) n FROM fanout_log
               WHERE canal IS NOT NULL GROUP BY canal, accion""")
        tot = db.fetch_one(
            """SELECT COUNT(*) eventos, COUNT(DISTINCT sku) skus,
                      MIN(ts) desde, MAX(ts) hasta,
                      SUM(resultado LIKE 'ERROR%%') errores FROM fanout_log""") or {}
        return {"por_accion": {r["accion"]: r["n"] for r in por_accion},
                "por_canal": por_canal, **tot}
    except Exception as exc:  # noqa: BLE001
        log.warning("fanout_log resumen: %s", exc)
        return {}


def estado() -> dict[str, Any]:
    with _lock:
        pendientes = sorted(_pendientes.keys())
    return {
        "habilitado": habilitado(),
        "dry_run": dry_run(),
        "canales_habilitados": sorted(_canales_activos()) if _canales_activos() else "todos",
        "escritores_implementados": sorted(_ESCRITORES.keys()),
        "reserva": _reserva(),
        "debounce_s": DEBOUNCE_S,
        "pendientes": pendientes,
        "contadores": dict(_contadores),
        "eventos": list(_eventos)[:50],
        "resumen": resumen(),           # acumulado persistido (sobrevive deploys)
        "historial": historial(60),     # bitácora para el dashboard
    }
