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

Anti-spam, en dos capas (v0.31.0 — antes se colaba una alerta por deploy):

  1. Candado de enfriamiento POR TIPO, PERSISTIDO en MySQL (`alertas_estado`).
     El primer aviso sale al instante; los repetidos dentro de la ventana solo
     se cuentan y el siguiente aviso real anexa "(+N repetidas silenciadas)".
     Vive en la base y no en memoria porque el proceso MUERE en cada deploy de
     Railway: el candado en RAM se borraba y el vigilante volvía a avisar ~2.5
     min después de cada arranque (3 avisos en 30 min el 29-jul, uno por deploy).
     Si MySQL no está disponible, degrada solo al candado en memoria.
  2. Alertas por CAMBIO DE ESTADO (`avisar_estado`) para las condiciones que
     DURAN: un acta con deltas sigue con deltas todo el día. Avisa al entrar en
     falla, avisa la RECUPERACIÓN, y mientras nada cambie se calla (recordatorio
     a lo mucho 1 vez al día).

Sin SLACK_WEBHOOK_URL todo el módulo es un no-op: se enciende/apaga con la pura
variable, sin deploy.
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
_estados: dict[str, str] = {}          # tipo → último estado visto (respaldo RAM)


def disponible() -> bool:
    return bool(settings.slack_webhook_url)


# ── Candado PERSISTENTE (sobrevive a los deploys) ─────────────────────────────
# Tabla propia y desechable: borrarla solo hace que el primer aviso de cada tipo
# salga una vez más. Todo aquí es best-effort — un fallo de MySQL nunca impide
# avisar, solo devuelve el candado a la memoria del proceso.

_DDL_ESTADO = """
CREATE TABLE IF NOT EXISTS alertas_estado (
  tipo         VARCHAR(120) NOT NULL,
  ultimo_envio DATETIME     NULL,
  suprimidas   INT          NOT NULL DEFAULT 0,
  estado       VARCHAR(30)  NULL,
  actualizado  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (tipo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_persistente_ok: bool | None = None    # None = todavía no se intentó


def _persistente() -> bool:
    global _persistente_ok
    if _persistente_ok is None:
        try:
            if not settings.mysql_enabled:
                raise RuntimeError("MySQL apagado")
            from services import db
            db.execute(_DDL_ESTADO)
            _persistente_ok = True
        except Exception as exc:  # noqa: BLE001
            log.warning("alertas: candado SOLO en memoria (%s)", exc)
            _persistente_ok = False
    return _persistente_ok


def _epoch(dt: Any) -> float:
    """DATETIME de MySQL (naive, UTC) → epoch. Sin fecha = 0 (nunca avisado)."""
    return dt.replace(tzinfo=timezone.utc).timestamp() if dt else 0.0


def _fila(tipo: str) -> dict[str, Any]:
    if not _persistente():
        return {}
    try:
        from services import db
        return db.fetch_one(
            "SELECT ultimo_envio, suprimidas, estado FROM alertas_estado "
            "WHERE tipo = %s", (tipo,)
        ) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("alertas: no se pudo leer el candado de %s (%s)", tipo, exc)
        return {}


def _sellar(tipo: str, estado: str | None = None) -> None:
    """Marca «aviso enviado AHORA» y limpia el contador de suprimidas."""
    _ultimo_envio[tipo] = time.time()
    _suprimidas.pop(tipo, None)
    if estado:
        _estados[tipo] = estado
    if not _persistente():
        return
    try:
        from services import db
        db.execute(
            "INSERT INTO alertas_estado (tipo, ultimo_envio, suprimidas, estado) "
            "VALUES (%s, UTC_TIMESTAMP(), 0, %s) "
            "ON DUPLICATE KEY UPDATE ultimo_envio = UTC_TIMESTAMP(), "
            "suprimidas = 0, estado = COALESCE(VALUES(estado), estado)",
            (tipo, estado),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("alertas: no se pudo sellar %s (%s)", tipo, exc)


def _contar_suprimida(tipo: str) -> None:
    _suprimidas[tipo] = _suprimidas.get(tipo, 0) + 1
    if not _persistente():
        return
    try:
        from services import db
        db.execute(
            "INSERT INTO alertas_estado (tipo, suprimidas) VALUES (%s, 1) "
            "ON DUPLICATE KEY UPDATE suprimidas = suprimidas + 1", (tipo,)
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("alertas: no se pudo contar la suprimida de %s (%s)", tipo, exc)


def _guardar_estado(tipo: str, estado: str) -> None:
    """Anota el estado observado SIN avisar (la condición no cambió)."""
    _estados[tipo] = estado
    if not _persistente():
        return
    try:
        from services import db
        db.execute(
            "INSERT INTO alertas_estado (tipo, estado) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE estado = VALUES(estado)", (tipo, estado)
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("alertas: no se pudo guardar el estado de %s (%s)", tipo, exc)


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
            fila = _fila(tipo)
            # El candado de la BD manda sobre el de RAM: tras un deploy la RAM
            # está vacía y sin esto el aviso se repetiría en cada arranque.
            ultimo = max(_ultimo_envio.get(tipo, 0.0), _epoch(fila.get("ultimo_envio")))
            if ahora - ultimo < ventana:
                _contar_suprimida(tipo)
                return False
            extra = max(_suprimidas.get(tipo, 0), int(fila.get("suprimidas") or 0))
            _sellar(tipo)
        if extra:
            texto += f"  _(+{extra} repetidas silenciadas)_"
        threading.Thread(
            target=_post_slack, args=(f"{nivel} {texto}",), daemon=True
        ).start()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("avisar(%s) falló: %s", tipo, exc)
        return False


def avisar_estado(tipo: str, estado: str, texto: str, texto_ok: str | None = None,
                  nivel: str = "🔴", recordatorio_h: int = 24) -> bool:
    """
    Alerta por CAMBIO DE ESTADO — para condiciones que DURAN horas o días.

    El acta de un dominio sale `con_deltas` a las 2 a.m. y sigue igual todo el
    día: con `avisar()` eso son 4 avisos idénticos (uno por ventana de
    enfriamiento) más uno por cada deploy. Aquí solo se habla cuando algo
    CAMBIA:

      · bien → mal  : avisa (`nivel`)
      · mal  → otro mal (p. ej. 'ausente' → 'con_deltas'): avisa
      · mal  → bien : avisa la RECUPERACIÓN con ✅ (antes no se avisaba nunca)
      · sigue igual : SILENCIO, salvo un recordatorio cada `recordatorio_h`

    `estado='ok'` es el único valor que significa "todo bien". La primera vez
    que se ve un tipo en 'ok' NO se avisa (no hay recuperación que anunciar).
    """
    if not disponible():
        return False
    try:
        with _lock:
            fila = _fila(tipo)
            previo = fila.get("estado") if fila else _estados.get(tipo)
            ultimo = max(_ultimo_envio.get(tipo, 0.0), _epoch(fila.get("ultimo_envio")))
            if estado == "ok":
                if previo in (None, "ok"):
                    _guardar_estado(tipo, "ok")   # nunca estuvo mal: nada que decir
                    return False
                _sellar(tipo, "ok")
                mensaje, marca = (texto_ok or f"*Resuelto:* {texto}"), "✅"
            elif previo == estado and time.time() - ultimo < recordatorio_h * 3600:
                _guardar_estado(tipo, estado)     # la misma falla de siempre
                return False
            else:
                _sellar(tipo, estado)
                mensaje, marca = texto, nivel
        threading.Thread(
            target=_post_slack, args=(f"{marca} {mensaje}",), daemon=True
        ).start()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("avisar_estado(%s) falló: %s", tipo, exc)
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
        # Por CAMBIO de estado: el acta de hoy no se arregla sola en 15 min, así
        # que repetir el aviso cada ventana solo es ruido. La recuperación
        # (vuelve a 'ok') sí se anuncia — antes había que ir a mirar /migracion.
        resuelto = f"*Acta de {etiqueta}* de vuelta en `ok` — racha a salvo."
        if acta is None:
            avisar_estado(f"acta:{dom}", "ausente",
                          f"*Acta de {etiqueta} NO generada hoy* (ya pasan de las "
                          f"{settings.alertas_actas_hora_utc}:00 UTC). Revisar el "
                          f"cron deltas en Railway.", texto_ok=resuelto)
        else:
            avisar_estado(f"acta:{dom}", acta["resultado"],
                          f"*Acta de {etiqueta} salió `{acta['resultado']}`* — hay "
                          f"deltas MySQL↔Supabase. Ver /migracion (una re-corrida "
                          f"en cero el mismo día rescata la racha).",
                          texto_ok=resuelto)


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
    """Para diagnóstico: candado, estados vigentes y avisos que se tragó."""
    filas: list[dict[str, Any]] = []
    if _persistente():
        try:
            from services import db
            filas = db.fetch_all(
                "SELECT tipo, ultimo_envio, suprimidas, estado FROM alertas_estado "
                "ORDER BY ultimo_envio DESC"
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("alertas: resumen sin persistencia (%s)", exc)
    with _lock:
        return {
            "webhook_configurado": disponible(),
            "candado_persistente": _persistente(),
            "persistido": [
                {"tipo": f["tipo"], "estado": f.get("estado"),
                 "hace_min": round((time.time() - _epoch(f.get("ultimo_envio"))) / 60, 1)
                             if f.get("ultimo_envio") else None,
                 "suprimidas": f.get("suprimidas")}
                for f in filas
            ],
            "en_enfriamiento": {
                t: round((time.time() - ts) / 60, 1) for t, ts in _ultimo_envio.items()
            },
            "suprimidas": dict(_suprimidas),
        }
