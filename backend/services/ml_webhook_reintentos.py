"""
ml_webhook_reintentos.py — El aviso de VENTA de Mercado Libre cuyo pedido falló
queda PENDIENTE, y se reintenta solo.

POR QUÉ (24-sep-2026)
─────────────────────
La caída de DNS de Hostinger (.shop) dejó al backend sin Woo de 19:24 a 20:13 y
de 20:59 a 22:32 UTC. 84 ventas de ML fallaron al volverse pedido: 70 se curaron
solas con avisos posteriores de la misma orden y 13 pagadas quedaron sin pedido
hasta que se reprocesaron a mano. Nadie lo vio: el receptor marcaba
`procesado=true` aunque el pedido hubiera fallado, así que el indicador de /flujo
("webhooks sin procesar") no contó ni una — sus 120 pendientes eran avisos
informativos de TikTok. Y las ventas de ML no tienen sondeo: si el aviso falla,
solo otro aviso de la misma orden las rescata, y puede tardar días.

AHORA (mismo molde que `tiktok_webhook_reintentos`)
─────
  · `marcar_fallo` — el receptor lo llama cuando falla el pedido de un aviso
    `orders_v2`: `intentos+1`, `resultado` y `next_retry_at` con espera
    creciente. `procesado` se queda en false. Es bitácora: no cuelga de bandera.
  · `resolver_previos` — cuando un aviso POSTERIOR de la misma orden sale bien,
    los fallos anteriores quedan resueltos. El aviso no trae estado (todo se le
    pregunta a ML), así que el que salió bien ya aplicó lo más reciente. Solo los
    ANTERIORES: uno que llegó después pudo traer un cambio (una cancelación) que
    el que salió bien todavía no veía.
  · `reprocesar` — el job `ML_WEBHOOK_REINTENTOS_ENABLED` (nace APAGADO, regla
    3: crea pedidos) toma los vencidos, UNO por orden, y los vuelve a pasar por
    `meli.obtener_orden` + `pedidos_ml.sincronizar(reintentable=True)`: el mismo
    camino del webhook, con el candado anti-duplicados de los sondeos (si kubera
    no confirma el candado, se salta y se reintenta después).

LA ESPERA: 2, 4, 8, 16, 32 min y luego 1 h por paso. Con el tope de 10 intentos
cubre ~5 h — más que la caída más larga del 24-sep (93 min). La ventana de 48 h
impide que encender el job días después resucite fallos viejos.

Nada de aquí lanza. REGLA 11: `marcar_fallo`, `marcar_ok`, `resolver_previos` y
`_pendientes` BLOQUEAN (escriben o leen kubera): van en hilo.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.ml_webhook_reintentos")

ESPERA_BASE_S = 120
ESPERA_MAX_S = 3600
_VENTANA_HORAS = 48
_LOTE = 25

_ultimo: dict[str, Any] = {"estado": "sin_ejecutar"}


def estado() -> dict[str, Any]:
    return dict(_ultimo)


def tope() -> int:
    try:
        return max(1, int(getattr(settings, "ml_webhook_reintentos_tope", 10) or 10))
    except (TypeError, ValueError):
        return 10


def espera_reintento(fallo_n: int) -> int:
    """Segundos antes del reintento tras el fallo número `fallo_n` (1, 2, 3…)."""
    n = max(1, int(fallo_n))
    return int(min(ESPERA_MAX_S, ESPERA_BASE_S * (2 ** (n - 1))))


def orden_de(resource: Any) -> str | None:
    """'/orders/2000018614325180' → '2000018614325180'. Lo demás, None: la URL
    del webhook es pública y un `resource` inventado no debe llegar a ML."""
    m = re.fullmatch(r"/orders/(\d+)", str(resource or "").strip())
    return m.group(1) if m else None


def marcar_fallo(evento_id: int | None, resultado: str | None,
                 intentos_previos: int = 0, sku: str | None = None) -> dict[str, Any]:
    """
    ⚠️ BLOQUEA (escribe en kubera): en hilo. Nunca lanza.

    `intentos_previos` es lo que la fila tenía ANTES de este intento (0 para el
    aviso recién llegado). Al llegar al tope, `next_retry_at=null` y `agotado`:
    se deja de insistir y queda a la vista para una persona.
    """
    fuera: dict[str, Any] = {"evento_id": evento_id, "espera_s": None,
                             "agotado": False, "escrito": False}
    if not evento_id:
        return fuera
    fallo_n = max(0, int(intentos_previos or 0)) + 1
    espera = None if fallo_n >= tope() else espera_reintento(fallo_n)
    fuera.update(espera_s=espera, agotado=espera is None)
    texto = f"{'agotado' if espera is None else 'reintentar'} · {resultado or ''}"[:255]
    try:
        from services import supabase_db as sdb
        sdb.execute(
            """update ops.webhook_events
                  set intentos = intentos + 1, resultado = %(res)s,
                      sku = coalesce(%(sku)s, sku),
                      next_retry_at = case when %(espera)s is null then null
                                           else now() + make_interval(
                                               secs => %(espera)s::double precision)
                                      end
                where id = %(id)s and not procesado""",
            {"res": texto, "sku": sku, "espera": espera, "id": int(evento_id)})
        fuera["escrito"] = True
        if espera is None:
            log.warning("ML aviso %s: %d intentos fallidos, se deja de reintentar (%s)",
                        evento_id, fallo_n, texto)
    except Exception as exc:  # noqa: BLE001 — la bitácora nunca rompe la venta
        log.warning("ML aviso %s: no se pudo marcar en ops.webhook_events: %s",
                    evento_id, str(exc)[:200])
    return fuera


def marcar_ok(evento_id: int | None, resultado: str) -> bool:
    """⚠️ BLOQUEA. El reintento salió: el aviso queda procesado. Nunca lanza."""
    if not evento_id:
        return False
    try:
        from services import supabase_db as sdb
        sdb.execute(
            """update ops.webhook_events
                  set procesado = true, resultado = %(res)s, procesado_at = now(),
                      next_retry_at = null
                where id = %(id)s""",
            {"res": (resultado or "")[:255], "id": int(evento_id)})
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("ML aviso %s: no se pudo marcar como procesado: %s",
                    evento_id, str(exc)[:200])
        return False


def resolver_previos(resource: str | None, evento_id: int | None) -> int:
    """
    ⚠️ BLOQUEA. Un aviso de la orden salió bien: los fallos ANTERIORES de la
    misma orden quedan resueltos. Devuelve cuántos. Nunca lanza.
    """
    if not evento_id or not orden_de(resource):
        return 0
    try:
        from services import supabase_db as sdb
        return int(sdb.execute(
            """update ops.webhook_events
                  set procesado = true, procesado_at = now(), next_retry_at = null,
                      resultado = left('resuelto por un aviso posterior · '
                                       || coalesce(resultado, ''), 255)
                where env = %(env)s and canal = 'mercado_libre'
                  and topic = 'orders_v2' and external_id = %(res)s
                  and not procesado and id < %(id)s""",
            {"env": settings.app_env, "res": str(resource).strip(),
             "id": int(evento_id)}) or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("ML %s: no se pudieron resolver los avisos previos: %s",
                    resource, str(exc)[:200])
        return 0


def _pendientes(limite: int) -> list[dict[str, Any]]:
    """⚠️ BLOQUEA. Avisos de venta de ML vencidos para reintento, dentro del tope.
    Va por el índice parcial `idx_webhook_events_pendientes` (procesado,
    next_retry_at) where not procesado."""
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        """select id, external_id, intentos
             from ops.webhook_events
            where env = %(env)s
              and canal = 'mercado_libre'
              and topic = 'orders_v2'
              and not procesado
              and next_retry_at is not null
              and next_retry_at <= now()
              and intentos < %(tope)s
              and recibido_at >= now() - make_interval(hours => %(horas)s)
            order by next_retry_at
            limit %(lim)s""",
        {"env": settings.app_env, "tope": tope(), "horas": _VENTANA_HORAS,
         "lim": int(limite)})
    return [dict(f) for f in filas]


async def reprocesar(limite: int = _LOTE) -> dict[str, Any]:
    """Una pasada del reprocesador. Nunca lanza."""
    from services import meli, pedidos_ml

    r: dict[str, Any] = {"estado": "ok", "filas": 0, "ordenes": 0, "ok": 0,
                         "reintentar": 0, "agotados": 0, "no_orden": 0, "error": None}
    try:
        if not settings.pedidos_wc_enabled:
            # Sin pedidos, cada fila gastaría un intento sin que nada falle.
            r.update(estado="omitido", error="PEDIDOS_WC_ENABLED apagado")
            return _cerrar(r)
        filas = await asyncio.to_thread(_pendientes, limite)
        r["filas"] = len(filas)
        por_orden: dict[str, list[dict[str, Any]]] = {}
        for f in filas:
            oid = orden_de(f.get("external_id"))
            if oid:
                por_orden.setdefault(oid, []).append(f)
                continue
            # Recurso que no es una orden: se agota para que no tape el lote.
            r["no_orden"] += 1
            await asyncio.to_thread(marcar_fallo, f.get("id"),
                                    "el aviso no trae una orden válida", tope())
        r["ordenes"] = len(por_orden)
        for oid, eventos in por_orden.items():
            try:
                orden = await meli.obtener_orden(oid)
                if not orden:
                    rp: dict[str, Any] = {"ok": False, "motivo": "no se pudo traer la orden"}
                else:
                    rp = await pedidos_ml.sincronizar(
                        oid, orden=orden,
                        proteger_stock=not settings.pedidos_wc_descuenta_stock,
                        reintentable=True)
            except Exception as exc:  # noqa: BLE001
                rp = {"ok": False, "motivo": f"{type(exc).__name__}: {str(exc)[:150]}"}
            if rp.get("ok"):
                texto = (f"reintento ok · pedido WC #{rp.get('wc_order_id')} "
                         f"{rp.get('accion')} ({rp.get('estado_wc')})")
                for ev in eventos:
                    await asyncio.to_thread(marcar_ok, ev.get("id"), texto)
                r["ok"] += len(eventos)
                # Y los demás fallos de la orden que aún no vencían: este
                # reintento le preguntó a ML el estado de ahora y ya lo aplicó.
                tope_id = max(int(ev.get("id") or 0) for ev in eventos) + 1
                await asyncio.to_thread(resolver_previos, f"/orders/{oid}", tope_id)
                log.info("ML reintento de la orden %s → %s", oid, texto)
            else:
                texto = f"pedido WC falló: {rp.get('motivo')}"
                for ev in eventos:
                    m = await asyncio.to_thread(marcar_fallo, ev.get("id"), texto,
                                                int(ev.get("intentos") or 0))
                    r["agotados" if m.get("agotado") else "reintentar"] += 1
    except Exception as exc:  # noqa: BLE001 — nunca lanza
        log.exception("ml_webhook_reintentos.reprocesar falló")
        r.update(estado="error", error=f"{type(exc).__name__}: {str(exc)[:200]}")
    return _cerrar(r)


def _cerrar(r: dict[str, Any]) -> dict[str, Any]:
    _ultimo.clear()
    _ultimo.update(r)
    if r.get("filas") or r.get("estado") == "error":
        log.info("ML reintentos de avisos de venta: %s", r)
    return dict(r)
