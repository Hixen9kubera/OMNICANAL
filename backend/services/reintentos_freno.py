"""
reintentos_freno.py — El FRENO de los reintentos automáticos de avisos (ML y TikTok).

POR QUÉ (25-sep-2026)
─────────────────────
Eduardo pidió encender los reintentos con una condición: que no puedan quedarse
haciendo algo mal sin que nadie lo vea. Los reintentos ya son idempotentes
(candado de kubera, búsqueda del pedido en Woo, candado de huella de TikTok),
pero el riesgo que importa es el defecto que nadie conoce: uno que hiciera que
cada pasada creara pedidos de más, cada 5 minutos, mientras nadie mira. El
12-ago un candado que CONTESTABA en vez de fallar produjo 964 pedidos fantasma
en 4 h.

EL FRENO NO DEPENDE DE QUE LA IDEMPOTENCIA SEA PERFECTA
───────────────────────────────────────────────────────
  · Cuenta los pedidos CREADOS por el reintento (las actualizaciones no).
  · Si en la última hora llegan al límite (20 por omisión), el reintento de ese
    canal se DETIENE: los avisos se quedan pendientes y visibles en /flujo (como
    «vencidos») y sale UN aviso a Slack.
  · La detención se GUARDA en `ops.process_log`: un deploy o un reinicio NO la
    quitan. Solo `POST /api/webhooks/reintentos/liberar` (con llave), después de
    que una persona revisó.
  · Si no puede leer su estado, FALLA CERRADO: esa pasada no reintenta nada.

20/h está muy por encima de la peor caída real (13 ventas sin pedido en 3 h, el
24-sep) y muy por debajo de un desastre. Se ajusta con
ML_WEBHOOK_REINTENTOS_MAX_CREADOS_HORA / TIKTOK_WEBHOOK_REINTENTOS_MAX_CREADOS_HORA.

REGLA 11: `detenido`, `anotar_creado` (si dispara), `detener` y `liberar`
BLOQUEAN (kubera): van en hilo. Nada de aquí lanza.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Any

log = logging.getLogger("omnicanal.reintentos_freno")

_PROCESO = "reintentos_freno"
_NOMBRES = {"ml": "Mercado Libre", "tiktok": "TikTok"}


class Freno:
    def __init__(self, canal: str, ajuste: str, por_omision: int = 20):
        self.canal = canal
        self._ajuste = ajuste
        self._por_omision = por_omision
        self._creados: deque[float] = deque()
        # Respaldo en memoria por si la bitácora no acepta la detención: el
        # reintento se detiene igual en este proceso.
        self._detenido_local: str | None = None

    def limite(self) -> int:
        from config import settings
        try:
            return max(1, int(getattr(settings, self._ajuste, self._por_omision)
                              or self._por_omision))
        except (TypeError, ValueError):
            return self._por_omision

    def creados_ultima_hora(self, ahora: float | None = None) -> int:
        ahora = time.time() if ahora is None else ahora
        while self._creados and ahora - self._creados[0] > 3600:
            self._creados.popleft()
        return len(self._creados)

    def detenido(self) -> dict[str, Any] | None:
        """⚠️ BLOQUEA. {desde, motivo} si está detenido; None si puede seguir."""
        if self._detenido_local:
            return {"desde": None, "motivo": self._detenido_local}
        try:
            from services import supabase_db as sdb
            f = sdb.fetch_one(
                """select estado, created_at, detalle from ops.process_log
                    where proceso = %s and accion = %s
                    order by created_at desc, id desc limit 1""",
                (_PROCESO, self.canal))
        except Exception as exc:  # noqa: BLE001 — falla CERRADO
            return {"desde": None,
                    "motivo": f"no se pudo leer el freno ({type(exc).__name__}); "
                              f"esta pasada no reintenta"}
        if f and f.get("estado") == "detenido":
            detalle = f.get("detalle") or {}
            if isinstance(detalle, str):
                try:
                    detalle = json.loads(detalle)
                except ValueError:
                    detalle = {}
            return {"desde": str(f.get("created_at")), "motivo": detalle.get("motivo")}
        return None

    def anotar_creado(self, orden: str) -> bool:
        """Un pedido CREADO por el reintento. True si con él se llegó al límite
        (y el reintento ya quedó detenido). ⚠️ Si dispara, BLOQUEA."""
        self._creados.append(time.time())
        n = self.creados_ultima_hora()
        if n >= self.limite():
            self.detener(f"{n} pedidos creados por reintentos en 1 h (límite "
                         f"{self.limite()}); el último, la orden {orden}")
            return True
        return False

    def detener(self, motivo: str) -> None:
        """⚠️ BLOQUEA. Nunca lanza."""
        self._detenido_local = motivo
        nombre = _NOMBRES.get(self.canal, self.canal)
        log.error("REINTENTOS DE %s DETENIDOS: %s", nombre.upper(), motivo)
        try:
            from services import supabase_db as sdb
            sdb.execute(
                """insert into ops.process_log (proceso, origen, accion, estado, detalle)
                   values (%s, 'backend', %s, 'detenido', %s::jsonb)""",
                (_PROCESO, self.canal, json.dumps({"motivo": motivo}, ensure_ascii=False)))
        except Exception as exc:  # noqa: BLE001
            log.warning("freno %s: la detención no quedó en la bitácora (%s); "
                        "sigue detenido en este proceso", self.canal, exc)
        try:
            from services import alertas
            alertas.avisar(
                f"reintentos_freno:{self.canal}",
                f"🛑 *Reintentos de avisos de {nombre} DETENIDOS*: {motivo}. Los "
                f"avisos quedan pendientes en /flujo. Antes de liberar, revisar si "
                f"hay pedidos duplicados en Woo. Liberar: "
                f"`POST /api/webhooks/reintentos/liberar?canal={self.canal}`.")
        except Exception:  # noqa: BLE001
            pass

    def liberar(self) -> bool:
        """⚠️ BLOQUEA. Deja seguir al reintento. True si quedó anotado."""
        self._detenido_local = None
        self._creados.clear()
        try:
            from services import supabase_db as sdb
            sdb.execute(
                """insert into ops.process_log (proceso, origen, accion, estado, detalle)
                   values (%s, 'panel', %s, 'liberado', '{}'::jsonb)""",
                (_PROCESO, self.canal))
            log.warning("Reintentos de %s LIBERADOS.", _NOMBRES.get(self.canal, self.canal))
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("freno %s: no se pudo anotar la liberación: %s", self.canal, exc)
            return False


FRENO_ML = Freno("ml", "ml_webhook_reintentos_max_creados_hora")
FRENO_TIKTOK = Freno("tiktok", "tiktok_webhook_reintentos_max_creados_hora")


def avisar_agotado(canal: str, orden: str, motivo: str, intentos: int) -> None:
    """Una venta que agotó sus reintentos: que la vea una persona. Nunca lanza."""
    try:
        from services import alertas
        alertas.avisar(
            f"reintentos_agotados:{canal}",
            f"⚠️ *Venta de {_NOMBRES.get(canal, canal)} SIN pedido tras {intentos} "
            f"intentos*: orden {orden} ({str(motivo)[:120]}). Ya no se reintenta "
            f"sola; revisar y reprocesar a mano. Hay más en /flujo si se repite.")
    except Exception:  # noqa: BLE001
        pass
