"""
vigilante_sync.py — ¿El sync de inventario de verdad RECORRE el catálogo?

POR QUÉ EXISTE (29-sep-2026). Los syncs de Mercado Libre y de Amazon pasaron
semanas releyendo las MISMAS 80 publicaciones cada 15 min sin un solo error en
los logs: cada vuelta «terminaba bien». El turno salía de `updated_at`, que en
kubera es la fecha del último CAMBIO y no de la última lectura, así que una
publicación quieta nunca parecía leída y volvía a salir en la vuelta siguiente.
En ML lo tapaban los avisos (99 % al día); en Amazon, que no avisa,
CAM-0030-MAT pasó un mes sin ASIN. Un vigilante que solo pregunte «¿corrió?» no
lo ve: hay que medir QUÉ leyó.

QUÉ HACE (con VIGILANTE_SYNC_ENABLED)
  · Cada vuelta del sync anota en `ops.process_log` (proceso `sync_cobertura`,
    accion `canal|cuenta`) el tamaño del universo, los ids que VISITÓ y cuántos
    contestó el canal.
  · Cada hora `revisar()` cuenta las publicaciones DISTINTAS visitadas en la
    ventana (24 h) contra el universo y avisa por Slack SOLO al cambiar de
    estado (`alertas.avisar_estado`: la falla, la recuperación y un recordatorio
    al día):
       baja           cobertura < VIGILANTE_SYNC_MIN_COBERTURA (90 %)
       sin_vueltas    la última vuelta tiene más de VIGILANTE_SYNC_MAX_SILENCIO_MIN
       sin_respuesta  se visitaron publicaciones y el canal no contestó ninguna
  · Mientras no haya una ventana entera de historia el estado es `calentando`
    y no se avisa nada.
  · Guarda 7 días: lo más viejo se borra en la misma revisión.

Con la rotación por reloj, ML recorre sus ~2,550 publicaciones por cuenta cada
~8 h y Amazon sus ~1,840 filas cada ~6 h: en 24 h, lo sano es ~100 %.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from config import settings
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.vigilante_sync")

_PROCESO = "sync_cobertura"
_RETENCION_DIAS = 7
_NOMBRES = {"mercado_libre|BEKURA": "ML BEKURA",
            "mercado_libre|SANCORFASHION": "ML SANCORFASHION",
            "amazon|": "Amazon"}


def anotar_ronda(canal: str, cuenta: str, universo: int, visitados: list[str],
                 respondidos: int) -> None:
    """⚠️ BLOQUEA (se llama con asyncio.to_thread). Una vuelta del sync. Nunca
    lanza: el vigilante jamás puede tumbar al sync que vigila."""
    if not settings.vigilante_sync_enabled:
        return
    try:
        detalle = {"n": int(universo or 0),
                   "leidos": sorted({str(v) for v in visitados if v}),
                   "respondidos": int(respondidos or 0)}
        sdb.execute(
            """insert into ops.process_log (proceso, origen, accion, estado, detalle)
               values (%s, 'backend', %s, 'ronda', %s::jsonb)""",
            (_PROCESO, f"{canal}|{cuenta or ''}", json.dumps(detalle, ensure_ascii=False)))
    except Exception as exc:  # noqa: BLE001
        log.warning("vigilante_sync: no se anotó la vuelta %s|%s (%s)", canal, cuenta, exc)


def _estado(f: dict[str, Any], ahora: datetime, horas: int) -> str:
    silencio = (ahora - f["ultima"]).total_seconds() / 60 if f.get("ultima") else None
    if silencio is None or silencio > settings.vigilante_sync_max_silencio_min:
        return "sin_vueltas"
    if f["desde"] > ahora - timedelta(hours=horas):
        return "calentando"
    if f["visitados"] and not f["respondidos"]:
        return "sin_respuesta"
    if f["cobertura"] is None or f["cobertura"] < settings.vigilante_sync_min_cobertura:
        return "baja"
    return "ok"


def cobertura(horas: int | None = None, ahora: datetime | None = None) -> list[dict[str, Any]]:
    """⚠️ BLOQUEA. Una fila por `canal|cuenta` que haya dado vueltas en los últimos
    7 días: universo (el de su última vuelta), publicaciones distintas
    visitadas en la ventana, vueltas, respuestas, cobertura y estado."""
    horas = int(horas or settings.vigilante_sync_horas)
    ahora = ahora or datetime.now(timezone.utc)
    claves = sdb.fetch_all(
        """select accion, min(created_at) desde, max(created_at) ultima
             from ops.process_log where proceso = %s group by accion""", (_PROCESO,))
    ventana = {f["accion"]: f for f in sdb.fetch_all(
        """with r as (
             select accion, created_at, detalle from ops.process_log
              where proceso = %(p)s and created_at > %(corte)s)
           select r.accion,
                  (select (r2.detalle->>'n')::int from r r2 where r2.accion = r.accion
                    order by r2.created_at desc limit 1) as universo,
                  (select count(distinct x) from r r3,
                          jsonb_array_elements_text(r3.detalle->'leidos') as x
                    where r3.accion = r.accion) as distintos,
                  count(*) as vueltas,
                  sum(jsonb_array_length(r.detalle->'leidos')) as visitados,
                  sum(coalesce((r.detalle->>'respondidos')::int, 0)) as respondidos
             from r group by r.accion""",
        {"p": _PROCESO, "corte": ahora - timedelta(hours=horas)})}
    salida = []
    for c in sorted(claves, key=lambda x: x["accion"]):
        v = ventana.get(c["accion"], {})
        n, distintos = int(v.get("universo") or 0), int(v.get("distintos") or 0)
        f = {"clave": c["accion"], "nombre": _NOMBRES.get(c["accion"], c["accion"]),
             "universo": n, "distintos": distintos,
             "cobertura": round(min(distintos / n, 1.0), 4) if n else None,
             "vueltas": int(v.get("vueltas") or 0),
             "visitados": int(v.get("visitados") or 0),
             "respondidos": int(v.get("respondidos") or 0),
             "desde": c["desde"], "ultima": c["ultima"]}
        f["estado"] = _estado(f, ahora, horas)
        salida.append(f)
    return salida


def _texto(f: dict[str, Any], horas: int) -> str:
    pct = f"{100 * (f['cobertura'] or 0):.0f} %"
    if f["estado"] == "sin_vueltas":
        return (f"*Sync de {f['nombre']}*: no ha dado una vuelta desde "
                f"{f['ultima']:%d-%b %H:%M} UTC. El inventario de ese canal no se está "
                f"releyendo; revisar SYNC_ENABLED y los logs del sync.")
    if f["estado"] == "sin_respuesta":
        return (f"*Sync de {f['nombre']}*: en {horas} h visitó {f['visitados']} publicaciones "
                f"y el canal no contestó ninguna. Probable token o API caída.")
    return (f"*Sync de {f['nombre']}*: en {horas} h leyó {f['distintos']} de "
            f"{f['universo']} publicaciones ({pct}). Si repite las mismas, el turno está "
            f"atorado como el 29-sep; revisar la rotación por reloj.")


def revisar() -> list[dict[str, Any]]:
    """⚠️ BLOQUEA. La revisión de cada hora: estado por canal, aviso al cambiar,
    poda de lo que pasó de 7 días. Nunca lanza."""
    if not settings.vigilante_sync_enabled:
        return []
    horas = int(settings.vigilante_sync_horas)
    try:
        filas = cobertura(horas)
    except Exception as exc:  # noqa: BLE001
        log.warning("vigilante_sync: no se pudo medir la cobertura (%s)", exc)
        return []
    from services import alertas
    for f in filas:
        if f["estado"] == "calentando":
            continue                     # sin historia completa no hay nada que juzgar
        alertas.avisar_estado(
            f"sync_cobertura:{f['clave']}", f["estado"], _texto(f, horas),
            texto_ok=(f"*Sync de {f['nombre']}* recorre el catálogo otra vez: "
                      f"{f['distintos']} de {f['universo']} en {horas} h."))
    log.info("vigilante_sync: %s", ", ".join(
        f"{f['nombre']} {f['estado']} ({f['distintos']}/{f['universo']})" for f in filas) or "sin vueltas anotadas")
    try:
        sdb.execute("delete from ops.process_log where proceso = %s "
                    "and created_at < now() - make_interval(days => %s)",
                    (_PROCESO, _RETENCION_DIAS))
    except Exception as exc:  # noqa: BLE001
        log.warning("vigilante_sync: no se podó la bitácora (%s)", exc)
    return filas
