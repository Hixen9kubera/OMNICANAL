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


def _texto(filas: list[dict[str, Any]], umbral: int) -> str:
    muestra = ", ".join(f"{f['canal']}/{f['cuenta']} {f['external_order_id']} ({f['minutos']} min)"
                        for f in filas[:_MUESTRA])
    resto = f" y {len(filas) - _MUESTRA} más" if len(filas) > _MUESTRA else ""
    return (f"*{len(filas)} venta(s) reclamada(s) sin pedido hace más de {umbral} min*: {muestra}{resto}. "
            f"Revisar Woo y los logs de `pedidos_ml`; el siguiente aviso o el sondeo las adopta o las crea.")


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
        _ultimo = {"estado": "sin_lectura", "error": str(exc)[:200],
                   "revisado_at": datetime.now(timezone.utc).isoformat()}
        return _ultimo
    estado = "pendientes" if filas else "ok"
    from services import alertas
    alertas.avisar_estado(
        _TIPO, estado, _texto(filas, umbral) if filas else "",
        texto_ok=f"*Ventas reclamadas:* ninguna sin pedido hace más de {umbral} min.")
    _ultimo = {"estado": estado, "n": len(filas), "umbral_min": umbral,
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
