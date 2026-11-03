"""
pedidos_vigilante.py — Ventas RECLAMADAS que se quedaron sin pedido (PLAN MAESTRO 2.11, F2.3).

POR QUÉ EXISTE (28-oct-2026). Cada venta nace en `channel.orders` con el reclamo de
`orders_write.reclamar` y `wc_order_id` NULL hasta que su pedido existe. Ese NULL
dura segundos cuando todo va bien. Cuando no —Woo caído, un relevo de contenedores,
un aviso que nadie reintenta— se queda, y hoy nadie lo ve: la caída de DNS del
24-sep dejó 13 ventas pagadas sin pedido hasta que alguien las buscó a mano, y el
hueco del 21/22-oct se midió desde fuera, contra ML. Con kubera primero (F2.2) ese
estado es además el normal de «Woo pendiente», así que alguien tiene que contar
cuánto dura.

QUÉ HACE (con PEDIDOS_VIGILANTE_ENABLED)
  · Cada PEDIDOS_VIGILANTE_MIN cuenta las filas con `wc_order_id IS NULL` cuyo
    registro tiene más de PEDIDOS_VIGILANTE_UMBRAL_MIN (10). El registro es
    `registrado_at` (0079) y, en una fila anterior a la 0079, `creado_at` — que en
    las reclamadas es la hora del reclamo. Se lee por `to_jsonb(o)` para que el
    vigilante funcione igual con la 0079 aplicada o sin ella.
  · Avisa por Slack solo al CAMBIAR de estado (`alertas.avisar_estado`: la falla,
    la recuperación y un recordatorio al día), con el número y las primeras llaves.
  · PEDIDOS_VIGILANTE_EXCLUIR: llaves `canal:cuenta:orden` conocidas que no se
    cuentan (hoy, el reclamo huérfano de Temu de septiembre).
  · VENTAS EN EL RESPALDO (2.15, riesgo 21): si `orders_write.guardar` no pudo escribir
    en kubera, la venta fue a MySQL y su evento espera en `espejo_kubera_log` hasta que
    el reproceso la aplique; mientras tanto el libro NO la resta. Se cuentan esos
    eventos pendientes (encabezado y líneas) y se avisa aparte (`pedidos_en_respaldo`),
    también solo al cambiar de estado. Si MySQL no contesta, no se avisa nada de esto.

Solo lee y avisa: no crea, no suelta ni reintenta nada. ⚠️ `revisar` y `pendientes`
BLOQUEAN (leen kubera): van en hilo (regla 11).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from config import settings
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.pedidos_vigilante")

_TIPO = "pedidos_sin_completar"
_TIPO_RESPALDO = "pedidos_en_respaldo"
_MUESTRA = 10
_ultimo: dict[str, Any] = {"estado": "sin_ejecutar"}

_SQL = """
with p as (
  select o.canal, o.cuenta, o.external_order_id,
         coalesce((to_jsonb(o) ->> 'registrado_at')::timestamptz, o.creado_at) as desde
    from channel.orders o
   where o.wc_order_id is null)
select canal, cuenta, external_order_id, desde,
       round(extract(epoch from (now() - desde)) / 60)::int as minutos
  from p
 where desde < now() - make_interval(mins => %(umbral)s)
   and not (canal || ':' || cuenta || ':' || external_order_id = any(%(excluir)s))
 order by desde
"""


def _excluir() -> list[str]:
    return [x.strip() for x in (settings.pedidos_vigilante_excluir or "").split(",") if x.strip()]


def pendientes(umbral_min: int | None = None) -> list[dict[str, Any]]:
    """⚠️ BLOQUEA. Las ventas reclamadas sin pedido hace más de `umbral_min`, la más
    vieja primero."""
    umbral = int(umbral_min if umbral_min is not None else settings.pedidos_vigilante_umbral_min)
    return sdb.fetch_all(_SQL, {"umbral": umbral, "excluir": _excluir()})


def respaldo() -> dict[str, Any] | None:
    """⚠️ BLOQUEA (lee MySQL). Eventos de pedidos que esperan en `espejo_kubera_log`: cuántos,
    de cuántas ventas y desde cuándo. None si MySQL no está o no contesta."""
    if not settings.mysql_enabled:
        return None
    try:
        from services import db
        from services.kubera_mirror import TABLAS_PEDIDO
        f = db.fetch_one(
            "SELECT COUNT(*) n, COUNT(DISTINCT clave) ventas, MIN(ts) desde FROM espejo_kubera_log "
            "WHERE resuelto = 0 AND tabla_destino IN (%s, %s)", TABLAS_PEDIDO)
        return {"n": int((f or {}).get("n") or 0), "ventas": int((f or {}).get("ventas") or 0),
                "desde": (f or {}).get("desde")}
    except Exception as exc:  # noqa: BLE001
        log.warning("pedidos_vigilante: no se pudo leer el respaldo (%s)", exc)
        return None


def _texto(filas: list[dict[str, Any]], umbral: int) -> str:
    muestra = ", ".join(f"{f['canal']}/{f['cuenta']} {f['external_order_id']} ({f['minutos']} min)"
                        for f in filas[:_MUESTRA])
    resto = f" y {len(filas) - _MUESTRA} más" if len(filas) > _MUESTRA else ""
    return (f"*{len(filas)} venta(s) reclamada(s) sin pedido hace más de {umbral} min*: {muestra}{resto}. "
            f"Revisar Woo y los logs de `pedidos_ml`; el siguiente aviso o el sondeo las adopta o las crea.")


def _avisar_respaldo(alertas) -> dict[str, Any] | None:
    """Cuenta el respaldo de MySQL y avisa al cambiar de estado. None si no se pudo leer."""
    resp = respaldo()
    if resp is not None:
        alertas.avisar_estado(
            _TIPO_RESPALDO, "pendientes" if resp["n"] else "ok",
            # V3 (verificación de 6b0350bf): el evento pendiente NO dice que a kubera le falte la venta; casi
            # siempre la tiene (el 271 del 18-sep está completo y se cerrará «superado»). Y /migracion → Errores
            # se retiró en v0.308.0.
            f"*{resp['ventas']} venta(s) con eventos pendientes en el respaldo de MySQL* ({resp['n']} evento(s), "
            f"el más viejo de {resp['desde']:%d-%b %H:%M} UTC): puede que kubera no tenga su último estado. "
            # W5 (verificación de 8181cf88): decir lo que pasa HOY con el job.
            + ("Los aplica el job PEDIDOS_RESPALDO_REPROCESO, que no pisa lo más nuevo."
               if settings.pedidos_respaldo_reproceso_enabled else
               "El job PEDIDOS_RESPALDO_REPROCESO está apagado: nadie los aplica todavía.")
            + " No se reprocesan a mano (acta 2.15)."
            if resp["n"] else "",
            texto_ok="*Respaldo de pedidos* vacío: kubera tiene todas las ventas.")
    return resp


def _respaldo_json(resp: dict[str, Any] | None) -> dict[str, Any] | None:
    if not resp:
        return None
    return {**resp, "desde": resp["desde"].isoformat() if resp.get("desde") else None}


def revisar() -> dict[str, Any]:
    """⚠️ BLOQUEA. Una revisión: cuenta, avisa al cambiar de estado y deja el resultado
    en `estado()`. Nunca lanza."""
    global _ultimo
    if not settings.pedidos_vigilante_enabled:
        return {"estado": "apagado"}
    umbral = int(settings.pedidos_vigilante_umbral_min)
    try:
        filas = pendientes(umbral)
    except Exception as exc:  # noqa: BLE001
        log.warning("pedidos_vigilante: no se pudo contar (%s)", exc)
        # Revisión de MAESTRO, #9: un vigilante que no puede mirar tiene que decirlo.
        from services import alertas
        alertas.avisar_estado(
            _TIPO, "sin_lectura",
            f"*Vigilante de ventas reclamadas*: no pudo leer kubera ({type(exc).__name__}: "
            f"{str(exc)[:120]}). Mientras tanto nadie cuenta las ventas sin pedido.",
            texto_ok=f"*Ventas reclamadas:* ninguna sin pedido hace más de {umbral} min.")
        # Segunda revisión, #9: con kubera sin contestar es cuando las ventas caen al respaldo de MySQL;
        # justo entonces hay que contarlas.
        resp = _avisar_respaldo(alertas)
        _ultimo = {"estado": "sin_lectura", "error": str(exc)[:200],
                   "respaldo": _respaldo_json(resp),
                   "revisado_at": datetime.now(timezone.utc).isoformat()}
        return _ultimo
    estado = "pendientes" if filas else "ok"
    from services import alertas
    alertas.avisar_estado(
        _TIPO, estado, _texto(filas, umbral) if filas else "",
        texto_ok=f"*Ventas reclamadas:* ninguna sin pedido hace más de {umbral} min.")
    resp = _avisar_respaldo(alertas)
    _ultimo = {"estado": estado, "n": len(filas), "umbral_min": umbral,
               "respaldo": _respaldo_json(resp),
               "muestra": [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in f.items()}
                           for f in filas[:_MUESTRA]],
               "excluidas": _excluir(),
               "revisado_at": datetime.now(timezone.utc).isoformat()}
    if filas:
        log.warning("pedidos_vigilante: %d venta(s) reclamada(s) sin pedido hace más de %d min",
                    len(filas), umbral)
    return _ultimo


def estado() -> dict[str, Any]:
    return {"habilitado": settings.pedidos_vigilante_enabled, **_ultimo}
