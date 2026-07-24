"""
alertas.py — Notificador de alertas a Slack (webhook entrante, Fase 1).

Canal destino: #alertas-omnicanal. El remitente es ESTE backend (en Slack se ve
como el bot "Kubera Alertas", pero no hay bot real: es un buzón de un solo
sentido — el backend habla, Slack pinta el mensaje).

Dos caminos de detección (regla mnemónica: si algo TRUENA avisa el que trona;
si algo FALTA avisa el que vigila):

  PUSH (tiempo real, segundos) — el código que falla llama avisar() en el
  momento, en hilo aparte para no frenar jamás la operación original:
    · kubera_mirror._persistir_error → error nuevo del espejo
    · meli.refrescar_token → refresh de token ML fallido

  VIGILANTE (job del scheduler cada ALERTAS_MIN) — detecta AUSENCIAS, que no
  truenan en ningún lado:
    · Actas de migración (migration.reconciliation_runs): después de
      ALERTAS_ACTAS_HORA_UTC, cada dominio debe tener acta HOY y en 'ok'.
    · Silencio de ventas: sin filas nuevas en pedidos_ml por más de
      ALERTAS_SILENCIO_HORAS dentro del horario hábil de CDMX (9-21 h).
    · Tokens ML rancios: ml_tokens_dashboard sin renovar en 12 h (el proceso
      externo renueva cada ~6 h; el doble = el renovador está caído).

Anti-spam: candado de enfriamiento POR TIPO de alerta. El primer aviso sale al
instante; los repetidos dentro de la ventana solo se cuentan y el siguiente
aviso real anexa "(+N repetidas silenciadas)". Sin SLACK_WEBHOOK_URL todo el
módulo es un no-op: se enciende/apaga con la pura variable, sin deploy.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from config import settings

log = logging.getLogger("omnicanal.alertas")

# Enfriamiento por tipo (minutos). Tipos no listados usan _COOLDOWN_DEFAULT.
_COOLDOWN_DEFAULT = 60
_COOLDOWN_MIN: dict[str, int] = {
    "espejo": 30,          # errores del espejo llegan en ráfaga con los bursts
    "tokens_ml": 60,
    "acta": 360,           # una acta ausente se re-avisa a lo mucho 2-3 veces/día
    "silencio_ventas": 240,
    "tokens_rancios": 360,
    "publicar_500": 30,    # por SKU (tipo "publicar_500:<sku>")
    "woo_403": 60,
}

_lock = threading.Lock()
_ultimo_envio: dict[str, float] = {}   # tipo → epoch del último aviso enviado
_suprimidas: dict[str, int] = {}       # tipo → avisos tragados por el candado


def disponible() -> bool:
    return bool(settings.slack_webhook_url)


def _post_slack(texto: str) -> None:
    """POST crudo al webhook. Corre SIEMPRE en hilo aparte; nunca lanza."""
    try:
        import httpx
        r = httpx.post(settings.slack_webhook_url, json={"text": texto}, timeout=10)
        if r.status_code != 200:
            log.warning("Slack respondió %s: %s", r.status_code, r.text[:120])
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo enviar la alerta a Slack: %s", exc)


def avisar(tipo: str, texto: str, nivel: str = "🔴") -> bool:
    """
    Manda una alerta al canal (con candado anti-spam por `tipo`).
    Devuelve True si el aviso salió, False si se suprimió o no hay webhook.
    Jamás lanza: una alerta rota no puede romper al que avisa.
    """
    if not disponible():
        return False
    try:
        ahora = time.time()
        ventana = _COOLDOWN_MIN.get(tipo.split(":")[0], _COOLDOWN_DEFAULT) * 60
        with _lock:
            if ahora - _ultimo_envio.get(tipo, 0.0) < ventana:
                _suprimidas[tipo] = _suprimidas.get(tipo, 0) + 1
                return False
            extra = _suprimidas.pop(tipo, 0)
            _ultimo_envio[tipo] = ahora
        if extra:
            texto += f"  _(+{extra} repetidas silenciadas)_"
        threading.Thread(
            target=_post_slack, args=(f"{nivel} {texto}",), daemon=True
        ).start()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("avisar(%s) falló: %s", tipo, exc)
        return False


_rachas: dict[str, list[float]] = {}  # tipo → timestamps de ocurrencias


def avisar_si_racha(tipo: str, texto: str, umbral: int = 5,
                    ventana_min: int = 10, nivel: str = "🟡") -> bool:
    """
    Para fallas INTERMITENTES que solas no ameritan alerta (p. ej. un 403 de
    Woo que parpadea): cuenta ocurrencias del tipo en una ventana deslizante y
    solo alerta al llegar al umbral. `{n}` en el texto se sustituye por el
    conteo. El candado de enfriamiento de avisar() aplica igual después.
    """
    if not disponible():
        return False
    ahora = time.time()
    with _lock:
        serie = _rachas.setdefault(tipo, [])
        serie.append(ahora)
        corte = ahora - ventana_min * 60
        while serie and serie[0] < corte:
            serie.pop(0)
        n = len(serie)
    if n < umbral:
        return False
    return avisar(tipo, texto.replace("{n}", str(n)), nivel=nivel)


# ── VIGILANTE de ausencias (job del scheduler) ────────────────────────────────

def _revisar_actas() -> None:
    """Después de la hora límite, cada dominio debe tener acta HOY y en 'ok'."""
    ahora = datetime.now(timezone.utc)
    if ahora.hour < settings.alertas_actas_hora_utc:
        return
    from routers.migracion import _DOMINIOS_DELTAS  # etiquetas canónicas
    from services import supabase_db as sdb
    if not sdb.disponible():
        return
    try:
        filas = sdb.fetch_all(
            "select distinct on (dominio) dominio, resultado, created_at "
            "from migration.reconciliation_runs "
            "where dominio = any(%(d)s) and created_at >= date_trunc('day', now()) "
            "order by dominio, created_at desc",
            {"d": list(_DOMINIOS_DELTAS)},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vigilante actas: %s", exc)
        return
    por_dominio = {f["dominio"]: f for f in filas}
    for dom, etiqueta in _DOMINIOS_DELTAS.items():
        acta = por_dominio.get(dom)
        if acta is None:
            avisar(f"acta:{dom}",
                   f"*Acta de {etiqueta} NO generada hoy* (ya pasan de las "
                   f"{settings.alertas_actas_hora_utc}:00 UTC). Revisar el cron "
                   f"deltas en Railway.")
        elif acta["resultado"] != "ok":
            avisar(f"acta:{dom}",
                   f"*Acta de {etiqueta} salió `{acta['resultado']}`* — hay "
                   f"deltas MySQL↔Supabase. Ver /migracion (una re-corrida en "
                   f"cero el mismo día rescata la racha).")


def _revisar_silencio_ventas() -> None:
    """Sin ventas nuevas por N horas en horario hábil de CDMX = arteria caída."""
    hora_mx = datetime.now(ZoneInfo("America/Mexico_City")).hour
    if not (9 <= hora_mx < 21):
        return
    from services import db
    try:
        fila = db.fetch_one("SELECT MAX(actualizado) AS ult FROM pedidos_ml")
    except Exception as exc:  # noqa: BLE001
        log.warning("vigilante silencio: %s", exc)
        return
    ult = (fila or {}).get("ult")
    if not ult:
        return
    # MySQL guarda DATETIME naive en UTC → comparar con "ahora UTC" naive.
    horas = (datetime.now(timezone.utc).replace(tzinfo=None) - ult).total_seconds() / 3600
    if horas >= settings.alertas_silencio_horas:
        avisar("silencio_ventas",
               f"*Sin ventas nuevas en {horas:.1f} h* (horario hábil). Puede ser "
               f"día flojo… o webhooks/tokens caídos: revisar logs de Railway "
               f"(`orders_v2`) y `/api/webhooks/registro`.", nivel="🟡")


def _revisar_tokens_rancios() -> None:
    """El renovador externo refresca ~cada 6 h; 12 h sin tocar = está caído."""
    from services import db
    try:
        fila = db.fetch_one("SELECT MAX(updated_at) AS ult FROM ml_tokens_dashboard")
    except Exception as exc:  # noqa: BLE001
        log.warning("vigilante tokens: %s", exc)
        return
    ult = (fila or {}).get("ult")
    if not ult:
        return
    # MySQL guarda DATETIME naive en UTC → comparar con "ahora UTC" naive.
    horas = (datetime.now(timezone.utc).replace(tzinfo=None) - ult).total_seconds() / 3600
    if horas >= 12:
        avisar("tokens_rancios",
               f"*Tokens ML sin renovar hace {horas:.0f} h* (el renovador externo "
               f"corre ~cada 6 h). El backend se auto-sana al primer 401, pero si "
               f"el refresh_token muere, los pedidos paran. Probar `/users/me`.",
               nivel="🟡")


async def vigilante() -> None:
    """Job del scheduler: cada revisión es independiente y best-effort."""
    if not disponible():
        return
    for revision in (_revisar_actas, _revisar_silencio_ventas, _revisar_tokens_rancios):
        try:
            revision()
        except Exception as exc:  # noqa: BLE001
            log.warning("vigilante %s: %s", revision.__name__, exc)


def resumen_estado() -> dict[str, Any]:
    """Para diagnóstico: qué tipos están en enfriamiento y cuántas suprimidas."""
    with _lock:
        return {
            "webhook_configurado": disponible(),
            "en_enfriamiento": {
                t: round((time.time() - ts) / 60, 1) for t, ts in _ultimo_envio.items()
            },
            "suprimidas": dict(_suprimidas),
        }
