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
    · Pedidos DUPLICADOS: dos pedidos de Woo para la misma orden del
      marketplace. Faltaba, y por eso los 964 fantasma del 12-ago corrieron
      4 h 17 min sin que nadie se enterara: la única alerta de pedidos era la
      de SILENCIO, que mide lo contrario y esa tarde gritó "sin ventas"
      mientras se creaban 964. Se cuenta en WooCommerce porque channel.orders
      tiene llave por orden y un duplicado la sobreescribe: ahí es invisible.

  DIARIAS del costeo (mismo job, una sola corrida al día vía `_toca_hoy`;
  ver el bloque "REVISIONES DIARIAS DEL COSTEO" más abajo):
    · Margen NEGATIVO de lo evaluable: solo publicaciones con precio
      CONFIRMADO y costo VERIFICADO; lo demás va como conteo agregado. Dice si
      el negativo es pérdida real o costo dudoso, porque piden acciones
      opuestas.
    · Top 10 de más vendidos con el costo SIN VERIFICAR: un costo dudoso en un
      producto que vende 5 piezas es ruido; en uno que vende 600 decide dinero.

  Estas dos, ADEMÁS de Slack, dejan el aviso en la CAMPANA del panel (`_campana`
  más abajo): Slack es donde vive la alerta, pero el panel es donde mira
  Eduardo. Se escribe en las DOS tablas que la campana puede leer para que el
  aviso no dependa de cómo esté `SUPABASE_READ_WEBHOOKS`.

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

DOS CANALES, CON CAÍDA AL DE SIEMPRE (v0.271.0). Casi todo va a
#alertas-omnicanal (`SLACK_WEBHOOK_URL`). Las DOS revisiones diarias del costeo
van a #avisos-costos (`SLACK_WEBHOOK_COSTOS`), porque no son incidentes: un
margen negativo se lee con calma; "los pedidos pararon" se atiende ya.
Si `SLACK_WEBHOOK_COSTOS` está vacía, esas dos caen a `SLACK_WEBHOOK_URL` y
suena todo donde sonaba antes. El ruteo vive en `_WEBHOOK_POR_TIPO`, abajo.
"""
from __future__ import annotations

import hashlib
import json
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
    "token_tiktok": 360,
    # Duplicados: 60 min. Corto a propósito — mientras sigan naciendo copias hay
    # dinero moviéndose, y el aviso se apaga solo cuando dejan de aparecer.
    "pedidos_duplicados": 60,
    "publicar_500": 30,    # por SKU (tipo "publicar_500:<sku>")
    "woo_403": 60,
}

_lock = threading.Lock()
_ultimo_envio: dict[str, float] = {}   # tipo → epoch del último aviso enviado
_suprimidas: dict[str, int] = {}       # tipo → avisos tragados por el candado
_estados: dict[str, str] = {}          # tipo → último estado visto (respaldo RAM)


def disponible() -> bool:
    """
    ¿Está encendido el notificador? Es el interruptor del MÓDULO ENTERO.

    Mide SOLO `SLACK_WEBHOOK_URL`, y a propósito: es la misma pregunta que
    contestaba antes de que existiera el segundo canal, así que el vigilante y
    los tres `avisar*` se prenden y se apagan exactamente igual que siempre.

    El filo, dicho aquí para que nadie lo descubra en caliente: poner SOLO
    `SLACK_WEBHOOK_COSTOS` y vaciar ésta NO deja vivas las alarmas de costos —
    apaga el módulo completo, ellas incluidas. Las dos variables no son
    alternativas: la de costos es un DESVÍO sobre un notificador encendido.
    """
    return bool(settings.slack_webhook_url)


# ── A QUÉ CANAL VA CADA TIPO ──────────────────────────────────────────────────
# Por omisión TODO va a `SLACK_WEBHOOK_URL` (#alertas-omnicanal). Las dos
# revisiones diarias del costeo van a #avisos-costos porque no comparten
# urgencia con el resto: nadie tiene que saltar por un margen negativo, pero sí
# hay que leerlo. Revueltas con "los pedidos pararon", se pierden las dos cosas.
#
# EL RUTEO VA POR `tipo`, NO POR UN ARGUMENTO EN CADA LLAMADA, y no es pereza:
# cada una de estas revisiones llama a `avisar_estado` DOS veces —la alarma y su
# "resuelto"—, así que con un parámetro en el call site basta olvidarlo en uno
# para que el 🔴 salga en un canal y el ✅ en el otro, y entonces el canal de
# costos acumula alarmas que nunca se ven cerrar. Con la tabla, el par no se
# puede separar: la llave es la misma que ya identifica a la alarma.
#
# SIN LA VARIABLE NUEVA NO CAMBIA NADA: `_webhook_de` cae a la de siempre y las
# dos siguen sonando en #alertas-omnicanal, igual que hoy. Por eso esto se puede
# publicar antes de que el webhook exista.
#
# El valor es el NOMBRE del campo de `settings`, no la URL: una URL aquí sería
# un secreto en el repo.
_WEBHOOK_POR_TIPO: dict[str, str] = {
    "margen_negativo": "slack_webhook_costos",
    "top_costo_sin_revisar": "slack_webhook_costos",
}


def _webhook_de(tipo: str) -> str:
    """URL del canal de este `tipo`, con caída al canal general."""
    # Se corta en `:` igual que el enfriamiento (`acta:<dominio>`,
    # `publicar_500:<sku>`) para que un tipo con sufijo herede el canal de su
    # familia. Ojo: `_toca_hoy` sella con `<tipo>:corrida`, que nunca se manda a
    # Slack — solo se guarda el estado —, así que no hay riesgo de que el latch
    # diario se cuele por aquí.
    campo = _WEBHOOK_POR_TIPO.get(tipo.split(":")[0])
    propio = getattr(settings, campo, "") if campo else ""
    return propio or settings.slack_webhook_url


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


def _post_slack(texto: str, url: str) -> None:
    """
    POST crudo al webhook. Corre SIEMPRE en hilo aparte; nunca lanza.

    La URL llega como argumento —y no se lee de `settings` aquí— porque quien
    decide el canal es el `tipo` de la alerta, y eso se resuelve en el hilo que
    llama, con `_webhook_de`.
    """
    try:
        import httpx
        r = httpx.post(url, json={"text": texto}, timeout=10)
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
            target=_post_slack, args=(f"{nivel} {texto}", _webhook_de(tipo)),
            daemon=True
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
            target=_post_slack, args=(f"{marca} {mensaje}", _webhook_de(tipo)),
            daemon=True
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
    # etiquetas canónicas; los retirados NO se vigilan (su cron ya no escribe
    # acta a propósito, así que la ausencia es lo esperado — v0.95.1 con channel)
    from routers.migracion import (_DOMINIOS_DELTAS, _DOMINIOS_RETIRADOS,
                                   _DOMINIOS_SIN_ALERTA)
    from services import supabase_db as sdb
    if not sdb.disponible():
        return
    # Retirados: ya no escriben acta. Sin alerta: la escriben y se ve en
    # /migracion, pero no timbran (ver _DOMINIOS_SIN_ALERTA).
    vigilados = {d: e for d, e in _DOMINIOS_DELTAS.items()
                 if d not in _DOMINIOS_RETIRADOS and d not in _DOMINIOS_SIN_ALERTA}
    try:
        filas = sdb.fetch_all(
            "select distinct on (dominio) dominio, resultado, created_at "
            "from migration.reconciliation_runs "
            "where dominio = any(%(d)s) and created_at >= date_trunc('day', now()) "
            "order by dominio, created_at desc",
            {"d": list(vigilados)},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("vigilante actas: %s", exc)
        return
    por_dominio = {f["dominio"]: f for f in filas}
    for dom, etiqueta in vigilados.items():
        acta = por_dominio.get(dom)
        # Por CAMBIO de estado: el acta de hoy no se arregla sola en 15 min, así
        # que repetir el aviso cada ventana solo es ruido. La recuperación
        # (vuelve a 'ok') sí se anuncia — antes había que ir a mirar /migracion.
        resuelto = f"*Acta de {etiqueta}* de vuelta en `ok` — racha a salvo."
        # EL TEXTO NOMBRABA UNA COSA QUE YA NO EXISTE (18-ago-2026). Decía
        # "deltas MySQL↔Supabase" y "cron deltas" de cuando estos dominios eran
        # los crons `deltas-*`. Esos están retirados y ya no escriben acta: los
        # únicos que llegan hasta aquí son los dos ETLs de las 06:15, que NO
        # abren MySQL desde la v0.129.0 — comparan Woo y Odoo vivos contra
        # kubera. Lo que reportan es `seam_gap`: lo que cambió en la fuente y
        # ningún seam en vivo alcanzó a cubrir.
        #
        # No era cosmético. Con ese nombre el aviso se leía como residuo del
        # espejo apagado —algo que ya se dio por muerto— y por eso se ignoraba,
        # justo cuando es el ÚNICO auditor del seam que queda vivo.
        if acta is None:
            avisar_estado(f"acta:{dom}", "ausente",
                          f"*Acta de {etiqueta} NO generada hoy* (ya pasan de las "
                          f"{settings.alertas_actas_hora_utc}:00 UTC). Revisar el "
                          f"cron de las 06:15 en Railway.", texto_ok=resuelto)
        else:
            avisar_estado(f"acta:{dom}", acta["resultado"],
                          f"*Acta de {etiqueta} salió `{acta['resultado']}`* — hay "
                          f"cambios en Woo que ningún seam cubrió (`seam_gap`). "
                          f"Ver /migracion (una re-corrida en cero el mismo día "
                          f"rescata la racha).",
                          texto_ok=resuelto)


# Cuentas cuya venta alimenta el vigilante de silencio. Se miran TODAS: que una
# cuente sola bastaría para tapar la caída de otra.
_CUENTAS_VENTA = ("BEKURA", "SANCORFASHION", "AMAZON")


def _revisar_silencio_ventas() -> None:
    """Sin ventas nuevas por N horas en horario hábil de CDMX = arteria caída."""
    hora_mx = datetime.now(ZoneInfo("America/Mexico_City")).hour
    if not (9 <= hora_mx < 21):
        return
    # Del REGISTRO, no del espejo. Leía `pedidos_ml`, y cuando el paso 1 del
    # desmantelamiento la congeló (12-ago-2026) el vigilante empezó a gritar
    # "sin ventas" en pleno día récord — 1,861 pedidos, el último de hacía
    # segundos. Mismo error que el acta de channel: se retira un dominio y su
    # vigilante se queda mirando la tabla apagada.
    from services import orders_write
    try:
        ult = max(filter(None, (orders_write.ultimo_actualizado(c)
                                for c in _CUENTAS_VENTA)), default=None)
    except Exception as exc:  # noqa: BLE001
        log.warning("vigilante silencio: %s", exc)
        return
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


def _revisar_token_tiktok() -> None:
    """
    El access_token de TikTok dura ~7 días. Cuando venció el 15-ago nadie se
    enteró en 3 días: este vigilante solo miraba ML y el canal murió en
    silencio (4 errores 105002 en el fan-out). Desde v0.207 hay auto-refresh
    reactivo en `tiktok.llamar`, pero si el canal pasa días sin tráfico el
    token puede llegar vencido a la siguiente escritura — este aviso es la red
    para enterarse ANTES.
    """
    from services import db
    try:
        fila = db.fetch_one(
            "SELECT expira FROM tiktok_tokens ORDER BY updated_at DESC LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        log.warning("vigilante token tiktok: %s", exc)
        return
    expira = (fila or {}).get("expira")
    if not expira:
        return
    restante_h = (expira - datetime.now(timezone.utc).replace(tzinfo=None)
                  ).total_seconds() / 3600
    if restante_h <= 24:
        estado_txt = (f"VENCIDO hace {-restante_h:.0f} h" if restante_h < 0
                      else f"vence en {restante_h:.0f} h")
        avisar("token_tiktok",
               f"*Token de TikTok {estado_txt}.* El auto-refresh de "
               f"`tiktok.llamar` lo renueva al primer uso, pero sin tráfico "
               f"nadie lo toca. Renovar a mano: "
               f"`POST /api/tiktok/token/refrescar` o esperar la próxima "
               f"escritura del fan-out.", nivel="🟡")


# Ventana del vigilante de duplicados: se mira la COPIA más nueva. 24 h da
# margen para que un hueco del scheduler no deje pasar un episodio, y el
# enfriamiento de `avisar()` evita que se repita el mismo aviso todo el día.
_HORAS_DUP = 24


def _revisar_duplicados() -> None:
    """
    Dos pedidos de WooCommerce para la MISMA orden del marketplace.

    Este vigilante no existía y por eso los 964 pedidos fantasma del 12-ago
    corrieron 4 h 17 min sin que nadie se enterara: la única alerta de pedidos
    era la de SILENCIO, que mide el problema contrario. Aquella tarde gritó
    "sin ventas" mientras se creaban 964 — la señal estaba invertida.

    Se cuenta en WooCommerce, no en nuestras tablas: `channel.orders` tiene
    llave por orden del marketplace, así que un duplicado la SOBREESCRIBE y es
    invisible ahí. El pedido de más solo se ve en la tienda.

    Medido el 13-ago-2026: la consulta tarda 0.1 s y hay 7 casos en 7 días
    —dos de anoche— así que esto NO es hipotético, ya está pasando a goteo.
    """
    from services import wp_db
    if not wp_db.disponible():
        return
    # La ventana va sobre la COPIA MÁS NUEVA (el `having`), no sobre las dos.
    # Filtrando ambas se escapa el caso peor: un reintento que llega días
    # después del original —el pedido viejo queda fuera de la ventana, el grupo
    # se queda con una sola fila y el duplicado pasa invisible—. Y de paso el
    # aviso se apaga solo cuando la copia envejece, en vez de repetir para
    # siempre un duplicado que ya se atendió.
    filas = wp_db._fetch_all(
        """SELECT m.meta_value AS ml_order_id, COUNT(*) AS n,
                  GROUP_CONCAT(o.id ORDER BY o.id) AS pedidos
             FROM wp_wc_orders_meta m
             JOIN wp_wc_orders o ON o.id = m.order_id
            WHERE m.meta_key = '_ml_order_id'
              AND o.status NOT IN ('trash', 'wc-checkout-draft')
              AND o.date_created_gmt > UTC_TIMESTAMP() - INTERVAL 30 DAY
            GROUP BY m.meta_value
           HAVING n > 1
              AND MAX(o.date_created_gmt) > UTC_TIMESTAMP() - INTERVAL %s HOUR
            ORDER BY n DESC LIMIT 20""", (_HORAS_DUP,))
    # Por CAMBIO DE ESTADO, no por enfriamiento. Un duplicado dura hasta que
    # alguien lo atiende, así que con `avisar()` el mismo caso volvía a sonar
    # cada ventana: el 13-ago sonó a las 23:35 y otra vez a las 00:38 por los
    # MISMOS tres pedidos (+4 suprimidas). Repetir lo ya reportado no informa,
    # entrena a ignorar la alerta.
    #
    # El estado es la HUELLA del conjunto de órdenes duplicadas, no su número:
    # si aparece una nueva la huella cambia y vuelve a sonar (que es justo lo
    # que hay que saber), pero mientras sea el mismo caso hay silencio. Va
    # hasheada porque la columna `estado` es varchar(30) y no cabe la lista.
    if not filas:
        avisar_estado("pedidos_duplicados", "ok", "",
                      texto_ok="*Sin pedidos duplicados en Woo* — resuelto.")
        return
    ids = sorted(str(f["ml_order_id"]) for f in filas)
    huella = f"dup{len(ids)}:{hashlib.sha1('|'.join(ids).encode()).hexdigest()[:12]}"
    piezas = sum(int(f["n"]) - 1 for f in filas)
    muestra = " · ".join(f"{f['ml_order_id']}→#{f['pedidos']}" for f in filas[:4])
    avisar_estado(
        "pedidos_duplicados", huella,
        f"*{len(filas)} orden(es) con pedido DUPLICADO en Woo* en las últimas "
        f"{_HORAS_DUP} h ({piezas} pedido(s) de más). {muestra}. "
        f"Revisar el candado de idempotencia: agrupar por meta `_ml_order_id`. "
        f"Cancelar ANTES de mandar a la papelera los que hayan descontado "
        f"stock, o Woo devuelve piezas que nunca salieron.",
        texto_ok="*Sin pedidos duplicados en Woo* — resuelto.",
        # Semanal y no diario: un duplicado sin atender no cambia de urgencia
        # cada 24 h, y la ventana de 24 h del `having` ya apaga el aviso solo
        # cuando la copia envejece.
        recordatorio_h=168)


# ── La alarma también en la CAMPANA del panel ─────────────────────────────────
#
# POR QUÉ. Slack es donde vive la alerta, pero Eduardo mira el panel. Un aviso
# que solo existe en un canal que el destinatario no abre no es un aviso.
#
# SE ESCRIBE EN LAS DOS TABLAS, Y NO ES CINTURÓN Y TIRANTES. La campana
# (`GET /api/webhooks/notificaciones`) lee MySQL `webhook_eventos` **o** kubera
# `ops.webhook_events` según `SUPABASE_READ_WEBHOOKS`, que hoy está en `false`.
# Escribir en una sola ataría la visibilidad de la alarma a una decisión que no
# es suya: el día que ese flag cambie, el aviso desaparecería de la campana sin
# que nadie tocara este archivo.
#
# A KUBERA SE ESCRIBE DIRECTO, NO POR EL ESPEJO. `odoo_watch._avisar_campana`
# manda su evento con `kubera_mirror.espejar(...)`, y eso HOY NO LLEGA: `espejar`
# empieza por `activo("webhook_eventos")`, que consulta `KUBERA_MIRROR_TABLAS`, y
# esa tabla no está en el CSV. Por eso `ops.webhook_events` tiene **0 filas de
# canal 'odoo'** (medido el 26-ago) mientras MySQL acumula 754. Copiar ese patrón
# habría hecho que esta alarma nunca llegara a kubera y nadie se enterara —el
# espejo falla en silencio a propósito—. Además el espejo es andamiaje de la
# migración y se retira en F8: colgar código nuevo de él es ir hacia atrás
# cuando kubera YA es la fuente de verdad.
#
# `procesado = True`, Y NO ES LA MENTIRA QUE PARECE. La pregunta obvia es si se
# hereda el `procesado=1` que `odoo_watch` escribe al insertar (754 de 754
# "procesados" sin que nadie los abra). Pero en esta tabla `procesado` no
# significa "alguien lo leyó": va acompañada de `intentos`, `next_retry_at` y
# `procesado_at`, o sea que significa **"queda trabajo pendiente sobre esta
# fila"**. Sobre una alarma no queda ninguno —el evento ES la notificación, no
# hay nada que reprocesar—, así que `True` es correcto y `False` le inventaría a
# la columna un significado que no tiene, que es justo la deriva que dejó
# ilegible a `stock_cambio`.
#
# Y `False` tiene un filo: metería la fila en `idx_webhook_events_pendientes`
# (`where not procesado`), el índice de la cola de reintentos. Hoy NADIE la
# consume —verificado con grep: no existe el consumidor—, pero el día que se
# escriba se pondría a reintentar una alarma que no es un webhook.
#
# El "¿ya lo vi?" de la campana no vive en la base: vive en el `localStorage`
# del navegador (`omnicanal_ult_notif`), y es por persona.
#
# LO QUE LA CAMPANA PINTA DE CADA FILA (`frontend/components/NotificationBell.tsx`):
#   · negritas  = `etiquetaTopic(topic)` y, si el topic no está en su mapa,
#                 **sale el string crudo** — es exactamente lo que Eduardo ve
#                 con `stock_cambio`. Los dos topics de abajo TODAVÍA NO tienen
#                 etiqueta: handoff a omni-frontend del 26-ago-2026. Hasta que
#                 aterrice, el renglón de `resultado` es el que se lee.
#   · subtítulo = `resultado`, truncado a UNA línea → tiene que ser corto.
#   · chip      = `sku`.
_CAMPANA_CANAL = "alertas"


def _campana(topic: str, resultado: str, huella: str,
             sku: str | None = None) -> None:
    """
    Deja el aviso en la campana del panel. Best-effort: jamás lanza.

    Se llama SOLO cuando `avisar_estado` devolvió True, o sea cuando el aviso de
    Slack salió de verdad. Así hay UN solo punto de decisión —el de "esto
    cambió"— y la campana no puede terminar contando una historia distinta a la
    del canal.

    `huella` viaja como `external_id` y la fecha como `delivery_id`: la UNIQUE
    `(env, canal, topic, external_id, delivery_id)` de `ops.webhook_events` hace
    la idempotencia sola, así que el mismo aviso el mismo día no se duplica
    aunque el proceso se reinicie a media corrida.
    """
    ahora = datetime.now(timezone.utc)
    texto = resultado[:255]
    try:
        if settings.mysql_enabled:
            from services import db
            db.execute(
                """INSERT INTO webhook_eventos
                   (canal, topic, resource, user_id, cuenta, sku, procesado,
                    resultado, recibido)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (_CAMPANA_CANAL, topic, huella, None, None, sku, 1, texto,
                 ahora.replace(tzinfo=None)))
    except Exception as exc:  # noqa: BLE001
        log.warning("alertas: campana MySQL falló (%s)", exc)
    try:
        from services import supabase_db as sdb
        if sdb.disponible():
            sdb.execute(
                """insert into ops.webhook_events
                     (env, canal, topic, external_id, delivery_id, sku,
                      payload, procesado, resultado, recibido_at, procesado_at)
                   values (%s,%s,%s,%s,%s,%s,%s::jsonb,true,%s,%s,%s)
                   on conflict (env, canal, topic, external_id, delivery_id)
                   do nothing""",
                (settings.app_env, _CAMPANA_CANAL, topic, huella, _hoy_utc(),
                 sku, json.dumps({"huella": huella, "resultado": resultado},
                                 ensure_ascii=False),
                 texto, ahora, ahora))
    except Exception as exc:  # noqa: BLE001
        log.warning("alertas: campana kubera falló (%s)", exc)


# ══════════════════════════════════════════════════════════════════════════════
# REVISIONES DIARIAS DEL COSTEO
# ══════════════════════════════════════════════════════════════════════════════
#
# Las dos de abajo van juntas porque comparten compuerta, patrón y motivo.
#
# EL ERROR QUE NO SE QUISO COMETER. Una alarma de "margen negativo" a secas
# tenía, medido el 26-ago-2026, cientos de casos que reportar. Y ninguno se
# puede sostener todavía, por dos razones ya documentadas en el repo:
#
#   · EL PRECIO ESTÁ INFLADO. `l.price` NO baja cuando la promoción la monta
#     una campaña de ML. Muestra viva del 25-ago: de 60 publicaciones activas,
#     `l.price` coincidía con lo que ML cobra en 19; la mediana del cociente es
#     1.443 y el p90 2.95 (docstring de `publicaciones_panel._oferta`). Un
#     margen calculado contra ese precio sale negativo por construcción.
#   · EL COSTO ESTÁ INVENTADO. ~30% del catálogo trae un `costo_producto` que
#     es un precio en dólares redondeado ×19, no un costo medido
#     (`frontend/lib/margen.ts`). TEC-0406-AZL "cuesta" 111× su precio.
#
# Una alarma que escupe cientos de casos que YA SABEMOS mal medidos se deja de
# abrir a la tercera mañana — y entonces deja de avisar también de los que sí
# importan. Por eso estas dos revisiones se construyen sobre tres reglas:
#
#   1. AVISAN DE CAMBIOS, NO DE ESTADO. Vía `avisar_estado` con la huella del
#      conjunto, igual que `_revisar_duplicados`. No "estas 211 están en
#      negativo" todos los días para siempre, sino "el conjunto cambió".
#   2. SOLO ALARMAN SOBRE LO EVALUABLE. Dos compuertas —precio CONFIRMADO y
#      costo VERIFICADO— y lo que no las pasa NO desaparece: va como conteo
#      agregado, sin lista y sin ruido. Un número que dice "hay 779 que no sé
#      medir" es información; una lista de 779 es basura.
#   3. DICEN POR QUÉ. Un negativo por costo dudoso pide REVISAR EL COSTEO; uno
#      por pérdida real pide MOVER EL PRECIO. Son acciones opuestas: sin la
#      distinción, la alarma manda a bajar publicaciones sanas.
#
# LO QUE ESTO NO ES: un medidor de cuántas publicaciones pierden dinero. Ese
# número lo da `publicaciones_panel` para la pestaña Omnicanal. Aquí solo se
# alarma de lo que se puede sostener.


def _hoy_utc() -> str:
    """Fecha de hoy en UTC, 10 caracteres — cabe en `alertas_estado.estado`."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _toca_hoy(tipo: str, hora_utc: int) -> bool:
    """
    ¿Le toca correr HOY a esta revisión diaria?

    El vigilante despierta cada `ALERTAS_MIN` (15 min). Sin esta compuerta, las
    dos consultas de abajo —que barren 30 días de `channel.order_items` y las
    ~5,000 publicaciones de ML— correrían unas 64 veces al día para contestar
    exactamente lo mismo.

    El sello va en la MISMA tabla `alertas_estado` y por la MISMA razón que el
    candado de enfriamiento: el proceso MUERE en cada deploy de Railway, y un
    latch en RAM dejaría pasar otra corrida en cada arranque (el bug de los 3
    avisos en 30 min del 29-jul). Usa un `tipo` aparte (`…:corrida`) para no
    pisar el `estado`, que lleva la huella del conjunto.

    Se sella DESPUÉS de una corrida exitosa, a propósito: si la consulta truena
    —kubera caída, timeout— el latch no queda puesto y se reintenta en 15 min,
    en vez de perder el aviso del día entero.
    """
    if datetime.now(timezone.utc).hour < hora_utc:
        return False
    clave = f"{tipo}:corrida"
    return (_fila(clave).get("estado") or _estados.get(clave)) != _hoy_utc()


def _sellar_corrida(tipo: str) -> None:
    _guardar_estado(f"{tipo}:corrida", _hoy_utc())


# 1.5× ES LA REGLA DE LA CASA y su dueño es `frontend/lib/margen.ts`
# (`FACTOR_COSTO_IMPLAUSIBLE`; Eduardo lo bajó de 3× a 1.5× el 11-ago-2026).
# Se re-declara aquí porque no hay forma de importar TypeScript desde Python, y
# tiene que ser EL MISMO número: si el panel pinta ⚠ "tómalo con reserva" y la
# alerta del mismo SKU dice "pérdida real", el equipo recibe dos veredictos
# opuestos del mismo dato. Quien mueva el de allá tiene que mover este.
_FACTOR_COSTO_DUDOSO = 1.5

# Motivos de por qué una publicación NO se pudo evaluar. Son EXCLUYENTES y se
# aplican en este orden, para que la suma dé exactamente el total: un conteo
# con motivos que se traslapan no se puede leer.
_NO_EVAL = {
    "canal": "el canal no tiene costo propio",
    "costo": "costo sin verificar",
    "precio": "precio sin confirmar",
    "insumos": "sin comisión/peso/precio",
}


def _censo_margen() -> dict[str, Any] | None:
    """
    Recorre lo COMPRABLE hoy en los cinco canales de venta y parte el universo
    en dos: lo evaluable (con su margen) y lo que no, con el motivo.

    LAS DOS COMPUERTAS

      · PRECIO CONFIRMADO — `price_sale_at >= listings.updated_at`. Léase: la
        promoción se observó DESPUÉS del último cambio de esa fila. La regla es
        de `publicaciones_panel._oferta` (`oferta_confirmada`) y aquí va en SQL
        porque así el filtro es barato; SI ALLÁ CAMBIA, ESTO SE MUEVE CON ELLA.
        Sin confirmar, el precio es un techo, no lo que ML cobra.
      · COSTO VERIFICADO — `costing.costos_validados.revisado_at` no nulo (la
        marca de la migración 0032: "lo comparé contra el packing list") Y la
        fila sin moverse desde entonces (`updated_at <= revisado_at`). Una
        revisión que quedó atrás ya no cubre los números de hoy.

    La aritmética del margen NO se reimplementa: es `publicaciones_panel.
    margen_de`, la misma que pinta la pestaña Omnicanal. Lo único propio es el
    SELECT, y es más angosto que el `_BASE` de allá (no pide url, stock ni
    situación cruda) — así esta alarma no se cuelga de un archivo que está
    cambiando por otro lado.
    """
    from services import publicaciones_panel as pp
    from services import supabase_db as sdb
    if not sdb.disponible():
        return None

    partes: list[str] = []
    params: dict[str, Any] = {}
    for canal in pp.CANALES_VENTA:
        filtro = pp.filtro_sql_activas(canal, alias="l", clave=f"act_{canal}")
        if filtro is None:
            # El canal no decide por ninguna columna. NO se filtra a cero en
            # silencio: se queda fuera del censo (y sin costo propio, no habría
            # entrado a lo evaluable de todos modos).
            continue
        frag, par = filtro
        partes.append(f"(l.canal = '{canal}' and {frag})")
        params.update(par)
    if not partes:
        return None

    sql = f"""
    select l.sku::text as sku, l.canal as canal, a.legacy_code as tienda,
           p.name as titulo,
           l.price as precio_ml, l.price_sale as price_sale,
           (l.price_sale is not null and l.price_sale_at >= l.updated_at)
                                                          as precio_confirmado,
           (v.revisado_at is not null
            and (v.updated_at is null or v.updated_at <= v.revisado_at))
                                                          as costo_verificado,
           f.costo_unitario as costo_unitario, f.pct_comision as pct_comision,
           v.peso as peso, v.largo as largo, v.ancho as ancho, v.alto as alto
      from channel.listings l
      join core.accounts a on a.id = l.account_id
      left join core.products      p on p.sku = l.sku
      left join costing.costos_finales   f on f.sku = l.sku and f.canal = l.canal
      left join costing.costos_validados v on v.sku = l.sku
     where ({' or '.join(partes)})
    """
    filas = sdb.fetch_all(sql, params)

    negativas: list[dict[str, Any]] = []
    motivos: dict[str, int] = {k: 0 for k in _NO_EVAL}
    evaluadas = 0
    for r in filas:
        canal = r["canal"]
        # El orden importa: los motivos son excluyentes y se cuentan una vez.
        if canal not in pp.CANALES_CON_COSTO:
            motivos["canal"] += 1
            continue
        if not r["costo_verificado"]:
            motivos["costo"] += 1
            continue
        if not r["precio_confirmado"]:
            motivos["precio"] += 1
            continue
        # Con la compuerta de precio puesta, el precio vigente ES `price_sale`:
        # no hay que elegir entre dos candidatos como en el panel.
        precio = r["price_sale"]
        m = pp.margen_de(precio=precio, costo_unitario=r["costo_unitario"],
                         pct_comision=r["pct_comision"], peso=r["peso"],
                         largo=r["largo"], ancho=r["ancho"], alto=r["alto"],
                         canal=canal)
        if m["margen_pct"] is None:
            motivos["insumos"] += 1
            continue
        evaluadas += 1
        if m["margen_pct"] >= 0:
            continue
        cu, pv = float(r["costo_unitario"]), float(precio)
        negativas.append({
            "sku": r["sku"], "canal": canal, "tienda": r.get("tienda"),
            "precio": round(pv, 2), "costo": round(cu, 2),
            "margen_pct": round(m["margen_pct"] * 100, 1),
            # POR QUÉ es negativo, que es lo que decide la ACCIÓN. Ojo: un
            # "costo dudoso" que llega hasta aquí YA pasó la compuerta de
            # verificado — alguien lo comparó contra el packing list y aun así
            # supera al precio por más de 1.5×. Eso no es el ruido de siempre:
            # o la revisión se hizo mal, o el precio se desplomó después.
            "dudoso": cu > pv * _FACTOR_COSTO_DUDOSO,
        })
    return {"negativas": negativas, "motivos": motivos, "evaluadas": evaluadas,
            "universo": len(filas)}


def _revisar_margen_negativo() -> None:
    """Publicaciones EVALUABLES cuyo margen se fue a negativo. Una vez al día."""
    tipo = "margen_negativo"
    if not _toca_hoy(tipo, settings.alertas_costos_hora_utc):
        return
    censo = _censo_margen()
    if censo is None:
        return
    _sellar_corrida(tipo)

    negativas = censo["negativas"]
    sin_eval = censo["universo"] - censo["evaluadas"]
    # El conteo agregado viaja SIEMPRE, también cuando no hay ninguna negativa:
    # "0 en negativo" sobre 2 evaluadas de 781 no significa lo mismo que sobre
    # 781 de 781, y sin este renglón las dos se leen igual.
    cola = (f"\n_Evaluadas {censo['evaluadas']} de {censo['universo']} "
            f"publicaciones comprables; {sin_eval} sin evaluar_")
    if sin_eval:
        detalle = " · ".join(f"{censo['motivos'][k]} {_NO_EVAL[k]}"
                             for k in _NO_EVAL if censo["motivos"][k])
        cola += f" — {detalle}."

    if not negativas:
        if avisar_estado(tipo, "ok", "",
                         texto_ok=f"*Sin publicaciones evaluables en margen "
                                  f"negativo.*{cola}"):
            _campana("margen_negativo",
                     "Sin publicaciones evaluables en margen negativo",
                     f"ok:{_hoy_utc()}")
        return

    # La huella es del CONJUNTO (sku+canal), no de su tamaño: si entra una nueva
    # cambia y vuelve a sonar —que es justo lo que hay que saber— y mientras sea
    # el mismo caso hay silencio. Va hasheada porque `estado` es varchar(30).
    claves = sorted(f"{n['sku']}|{n['canal']}" for n in negativas)
    huella = "neg{}:{}".format(
        len(claves), hashlib.sha1("|".join(claves).encode()).hexdigest()[:12])

    lineas = []
    for n in sorted(negativas, key=lambda x: x["margen_pct"])[:10]:
        que = (f"COSTO DUDOSO — el costo verificado sigue siendo "
               f"{n['costo'] / n['precio']:.1f}× el precio: revisar el COSTEO, "
               f"no bajar la publicación"
               if n["dudoso"] else
               "pérdida real — el precio no cubre costo + comisión + envío")
        lineas.append(f"· `{n['sku']}` {n['canal']}/{n['tienda']} — "
                      f"margen {n['margen_pct']}% (precio ${n['precio']:,.2f} · "
                      f"costo ${n['costo']:,.2f}) → {que}")
    mas = f"\n_…y {len(negativas) - 10} más._" if len(negativas) > 10 else ""

    hablo = avisar_estado(
        tipo, huella,
        f"*{len(negativas)} publicación(es) evaluable(s) con margen NEGATIVO.*\n"
        + "\n".join(lineas) + mas +
        "\n_Lista completa de hoy: esto suena cuando el CONJUNTO cambia, no "
        "todos los días._" + cola,
        texto_ok=f"*Ninguna publicación evaluable en margen negativo.*{cola}",
        # Semanal: un margen negativo sin atender no cambia de urgencia cada
        # 24 h, y la revisión ya corre una sola vez al día.
        recordatorio_h=168)
    if hablo:
        # El resumen de la campana NO es el texto de Slack recortado: ahí solo
        # cabe UNA línea. Dice cuántas, de qué tipo y cuál es la peor — que es
        # lo que decide si vale la pena abrir el panel ahora mismo.
        peor = min(negativas, key=lambda x: x["margen_pct"])
        dud = sum(1 for x in negativas if x["dudoso"])
        _campana("margen_negativo",
                 f"{len(negativas)} en margen negativo · "
                 f"{len(negativas) - dud} pérdida real, {dud} costo dudoso · "
                 f"peor {peor['margen_pct']}% ({peor['sku']})",
                 huella, sku=peor["sku"])


# Ventana y tamaño del top: los MISMOS que trae por omisión
# `GET /api/fulfillment/margenes-reales`, para que la alerta y la pantalla nunca
# se contradigan. Si esto dice "TEC-X está en el top 10", el panel lo tiene que
# estar mostrando en el top 10.
_TOP_DIAS = 30
_TOP_LIMITE = 10


def _revisar_top_sin_costo_revisado() -> None:
    """
    Un SKU entra al top 10 de más vendidos y su costo NO está verificado.

    LA LÓGICA: un costo dudoso en un producto que vende 5 piezas es ruido; en
    uno que vende 600 decide dinero. Esta alarma no mide el costo — mide dónde
    IMPORTA que esté mal.

    EL RANKING NO SE REESCRIBE. Se corre `_SQL_MARGEN_REAL_TOP` de
    `routers/fulfillment.py`, el mismo que alimenta la pantalla de Márgenes
    reales, y se lee su `rn_g`: el ranking por SKU SUMANDO las cuentas. Ese
    `rn_g` existe porque fundir dos top-10 por cuenta ya causó un incidente —un
    SKU con 200 piezas en cada cuenta no entra a ningún top-10 por separado y
    aun así es de los más vendidos—. Escribir un segundo top 10 aquí repetiría
    exactamente ese error.

    Ojo con `t.uds`: la consulta devuelve una fila POR CUENTA, así que las
    unidades hay que SUMARLAS por SKU. Quedarse con la primera fila da el número
    de una sola cuenta (MUE-0163-TEL sale con 17 uds en una y es el #1 del
    catálogo).
    """
    tipo = "top_costo_sin_revisar"
    if not _toca_hoy(tipo, settings.alertas_costos_hora_utc):
        return
    from routers.fulfillment import _SQL_MARGEN_REAL_TOP
    from services import supabase_db as sdb
    if not sdb.disponible():
        return
    # LAS TRES PESTAÑAS, NO SOLO "Todas" (Eduardo, 21-ago-2026). El filtro de
    # estado se aplica ANTES de numerar —pedir "activas" da el top 10 DE LAS
    # ACTIVAS—, así que cada pestaña tiene su propio ranking y un SKU puede ser
    # #3 entre las activas sin aparecer en el top 10 general, tapado por
    # pausadas que venden más. Esos quedaban fuera de la alarma teniendo el
    # costo sin verificar, que es justo lo que la alarma existe para atrapar.
    #
    # `None` va PRIMERO a propósito: cuando un SKU sale en varias pestañas se
    # conserva la primera que lo vio, y la general es la que el lector abre por
    # omisión — decir "#4 en Todas" ubica mejor que "#2 en Pausadas".
    PESTANAS = ((None, "Todas"), ("activa", "Activas"), ("pausada", "Pausadas"))
    top: dict[str, dict[str, Any]] = {}
    for est, etiqueta in PESTANAS:
        filas = sdb.fetch_all(_SQL_MARGEN_REAL_TOP,
                              {"dias": _TOP_DIAS, "limite": _TOP_LIMITE,
                               "estado": est})
        for f in filas:
            if not f.get("rn_g") or f["rn_g"] > _TOP_LIMITE:
                continue
            sku = f["sku"]
            d = top.get(sku)
            if d is None:
                d = top[sku] = {"rn": f["rn_g"], "uds": 0, "donde": etiqueta,
                                "pestanas": [],
                                "revisado": bool(f.get("revisado_at")),
                                "movida": bool(f.get("revision_movida"))}
            if etiqueta not in d["pestanas"]:
                d["pestanas"].append(etiqueta)
            # La consulta trae una fila POR CUENTA, así que las unidades se
            # SUMAN dentro de la pestaña. Pero solo se acumulan las de la
            # pestaña que registró al SKU: sumarlas entre pestañas contaría las
            # mismas piezas dos y hasta tres veces (una activa sale en "Todas"
            # y en "Activas").
            if d["donde"] == etiqueta:
                d["uds"] += int(f.get("uds") or 0)
    _sellar_corrida(tipo)
    if not top:
        return   # sin ventas en la ventana: no hay ranking del que hablar
    # "Sin verificar" incluye la revisión que quedó ATRÁS: si la fila se movió
    # después de marcarse, la marca ya no cubre los números de hoy.
    sin_rev = {s: d for s, d in top.items() if not d["revisado"] or d["movida"]}
    ok_txt = (f"*Los {len(top)} más vendidos ya tienen el costo verificado* "
              f"({_TOP_DIAS} d, las tres pestañas).")
    if not sin_rev:
        if avisar_estado(tipo, "ok", "", texto_ok=ok_txt):
            _campana("top_costo_sin_revisar",
                     f"Los {len(top)} más vendidos ya tienen el costo "
                     f"verificado", f"ok:{_hoy_utc()}")
        return

    claves = sorted(sin_rev)
    huella = "top{}:{}".format(
        len(claves), hashlib.sha1("|".join(claves).encode()).hexdigest()[:12])
    # El "#3" solo se entiende junto a SU pestaña: hay tres rankings y el mismo
    # número significa cosas distintas en cada uno. Cuando el SKU sale en varias
    # se listan todas: que esté en el top de "Activas" Y de "Todas" dice más
    # que cualquiera de las dos por separado.
    def _donde(d: dict[str, Any]) -> str:
        ps = d["pestanas"]
        return d["donde"] if len(ps) == 1 else " y ".join(ps)

    lineas = " · ".join(
        f"#{d['rn']} en {_donde(d)} `{s}` ({d['uds']} uds"
        f"{', revisión movida' if d['movida'] else ''})"
        for s, d in sorted(sin_rev.items(),
                           key=lambda kv: (kv[1]["donde"] != "Todas", kv[1]["rn"])))
    hablo = avisar_estado(
        tipo, huella,
        f"*{len(sin_rev)} de los {len(top)} más vendidos tienen el costo SIN "
        f"VERIFICAR* (ventana de {_TOP_DIAS} d, los mismos rankings que Márgenes "
        f"reales — se miran las TRES pestañas: Todas, Activas y Pausadas, porque "
        f"cada una numera aparte y un SKU puede ser de los más vendidos entre "
        f"las activas sin entrar al top general).\n{lineas}\n"
        f"_Un costo dudoso en un producto que vende 5 piezas es ruido; en estos "
        f"decide dinero. Verificar contra el packing list y marcarlo en Costos "
        f"(`revisado_at`). Suena cuando el conjunto CAMBIA, no todos los días._",
        texto_ok=ok_txt, nivel="🟡", recordatorio_h=168)
    if hablo:
        # Ante empate manda la pestaña general: un #2 de Todas pesa más que
        # un #2 de Pausadas.
        peor = min(sin_rev.items(),
                   key=lambda kv: (kv[1]["rn"], kv[1]["donde"] != "Todas"))
        _campana("top_costo_sin_revisar",
                 f"{len(sin_rev)} de los {len(top)} más vendidos con el costo "
                 f"sin verificar · el más vendido de ellos es el "
                 f"#{peor[1]['rn']} de {_donde(peor[1])}",
                 huella, sku=peor[0])


async def vigilante() -> None:
    """Job del scheduler: cada revisión es independiente y best-effort."""
    if not disponible():
        return
    for revision in (_revisar_actas, _revisar_silencio_ventas, _revisar_tokens_rancios,
                     _revisar_token_tiktok, _revisar_duplicados,
                     # Diarias: se auto-limitan con `_toca_hoy`, no con la
                     # frecuencia del job (ver el bloque de arriba).
                     _revisar_margen_negativo, _revisar_top_sin_costo_revisado):
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
            # Booleano, NUNCA la URL: este resumen es de diagnóstico y la URL es
            # la llave del canal. False aquí = las dos alarmas del costeo siguen
            # cayendo a #alertas-omnicanal, que es el comportamiento de siempre.
            "webhook_costos_configurado": bool(settings.slack_webhook_costos),
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
