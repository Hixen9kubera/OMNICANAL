"""
tiktok_webhook_reintentos.py — Cada aviso de TikTok queda MARCADO con lo que le
pasó, y lo que falló se reintenta con espera creciente.

POR QUÉ (14-sep-2026)
─────────────────────
El receptor contesta 200 SIEMPRE (si no, TikTok deshabilita la suscripción), así
que TikTok da por entregado todo aviso aunque procesarlo haya fallado: token
vencido, Woo con 500, kubera caída. Hasta hoy la fila de `ops.webhook_events`
se quedaba con `procesado=false` PARA SIEMPRE, igual si la venta entró que si
se perdió — `intentos` y `next_retry_at` existían desde el esquema v4 y nadie
los escribía. Un fallo se veía idéntico a un éxito.

AHORA
─────
  · `marcar(evento_id, resultado, intentos_previos)` — lo llama el receptor al
    terminar `pedidos_tiktok.procesar`:
      ok / ignorado / terminal → `procesado=true`, `resultado`, `procesado_at`.
      falla reintentable       → `intentos+1`, `resultado`, `next_retry_at` =
                                 ahora + 2 min · 2^(intentos) (tope 6 h); al
                                 llegar al tope de intentos, `next_retry_at=null`
                                 y un warning: se deja de insistir.
  · `reprocesar()` — el job `TIKTOK_WEBHOOK_REINTENTOS_ENABLED` (nace APAGADO,
    regla 3: vuelve a crear pedidos): toma `canal='tiktok' AND NOT procesado
    AND next_retry_at <= now()` con `intentos < tope`, de las últimas
    `_VENTANA_HORAS`, sólo con id de orden numérico, y los vuelve a pasar por
    `procesar`.

⚠️ EL MARCADO NO CUELGA DE NINGUNA BANDERA NUEVA
────────────────────────────────────────────────
`marcar` lo llama el receptor en cuanto `PEDIDOS_TIKTOK_ENABLED` está encendido,
AUNQUE `TIKTOK_WEBHOOK_REINTENTOS_ENABLED` siga apagada: entra vivo con el deploy
(un UPDATE por aviso a `ops.webhook_events`, en hilo). Es bitácora, no flujo de
negocio — nada más lee `procesado`/`next_retry_at` de TikTok —, y apagarlo con
los reintentos devolvería el "un fallo se ve igual que un éxito". Consecuencia a
decir en el changelog y en el dale: con los reintentos apagados, las filas
fallidas acumulan `next_retry_at` sin consumidor; al encender el job se retoman
las de las últimas `_VENTANA_HORAS`, de `_LOTE` en `_LOTE` y con candado de huella.

EL REPROCESADOR LLEVA EL CANDADO DE HUELLA
──────────────────────────────────────────
Entre el fallo y el reintento alguien pudo capturar la venta a mano en Odoo.
Una venta que channel.orders no conoce pero que ya dejó huella en la bitácora,
en Odoo o EN WOO (el POST que se cortó pero sí creó el pedido: el webhook suelta
el reclamo y el reintento, sin fila en channel.orders, crearía el gemelo) se
marca terminal y NO se procesa (el mismo candado del sondeo). Y la ventana de
horas impide que encender el job semanas después resucite fallos viejos.

SIN DATOS DEL COMPRADOR: `resultado` sólo lleva la acción, el estado de Woo y
el motivo recortado — nunca la orden. REGLA 11: todo lo que toca kubera va en
hilo (`marcar` y `_pendientes` BLOQUEAN). Nada de aquí lanza.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.tiktok_webhook_reintentos")

ESPERA_BASE_S = 120            # 2 min, 4, 8, 16, 32, 64…
ESPERA_MAX_S = 6 * 3600
_VENTANA_HORAS = 48            # un fallo más viejo que esto ya no se reintenta solo
_LOTE = 25

# Motivos de `procesar` que NO se arreglan reintentando: la orden no cambia.
_TERMINALES = ("no trae SKU legible",)

_ultimo: dict[str, Any] = {"estado": "sin_ejecutar"}


def estado() -> dict[str, Any]:
    return dict(_ultimo)


def tope() -> int:
    try:
        return max(1, int(getattr(settings, "tiktok_webhook_reintentos_tope", 6) or 6))
    except (TypeError, ValueError):
        return 6


def espera_reintento(fallo_n: int) -> int:
    """Segundos antes del reintento tras el fallo número `fallo_n` (1, 2, 3…)."""
    n = max(1, int(fallo_n))
    return int(min(ESPERA_MAX_S, ESPERA_BASE_S * (2 ** (n - 1))))


def clasificar(r: dict[str, Any] | None) -> str:
    """"ok" | "terminal" | "reintentar" para el resultado de `procesar`."""
    if not isinstance(r, dict):
        return "reintentar"
    if r.get("ok") or r.get("ignorado"):
        return "ok"
    motivo = str(r.get("motivo") or "")
    if any(t in motivo for t in _TERMINALES) or r.get("terminal"):
        return "terminal"
    return "reintentar"


def _texto(r: dict[str, Any] | None, clase: str) -> str:
    r = r if isinstance(r, dict) else {}
    partes = [clase]
    for k in ("accion", "estado_wc", "wc_order_id"):
        if r.get(k):
            partes.append(f"{k}={r[k]}")
    if r.get("motivo"):
        partes.append(str(r["motivo"])[:160])
    return " · ".join(partes)[:255]


def marcar(evento_id: int | None, r: dict[str, Any] | None,
           intentos_previos: int = 0) -> dict[str, Any]:
    """
    ⚠️ BLOQUEA (escribe en kubera): en hilo. Nunca lanza.

    `intentos_previos` es lo que la fila tenía ANTES de este intento (0 para el
    aviso recién llegado; el valor leído para el reprocesador). La espera se
    calcula aquí para que sea visible y probable; el tope lo vuelve a aplicar
    la consulta del reprocesador contra la BD, que es la autoridad.
    """
    fuera: dict[str, Any] = {"evento_id": evento_id, "clase": None, "espera_s": None,
                             "agotado": False, "escrito": False}
    if not evento_id:
        return fuera
    clase = clasificar(r)
    fuera["clase"] = clase
    try:
        from services import supabase_db as sdb
        if clase in ("ok", "terminal"):
            sdb.execute(
                """update ops.webhook_events
                      set procesado = true, resultado = %(res)s,
                          procesado_at = now(), next_retry_at = null
                    where id = %(id)s""",
                {"res": _texto(r, clase), "id": int(evento_id)})
            if clase == "ok":
                _resolver_previos(sdb, int(evento_id))
        else:
            fallo_n = max(0, int(intentos_previos or 0)) + 1
            espera = None if fallo_n >= tope() else espera_reintento(fallo_n)
            fuera.update(espera_s=espera, agotado=espera is None)
            sdb.execute(
                """update ops.webhook_events
                      set intentos = intentos + 1, resultado = %(res)s,
                          next_retry_at = case when %(espera)s is null then null
                                               else now() + make_interval(
                                                   secs => %(espera)s::double precision)
                                          end
                    where id = %(id)s and not procesado""",
                {"res": _texto(r, "agotado" if espera is None else "reintentar"),
                 "espera": espera, "id": int(evento_id)})
            if espera is None:
                log.warning("TIKTOK aviso %s: %d intentos fallidos, se deja de "
                            "reintentar (%s)", evento_id, fallo_n,
                            _texto(r, "agotado"))
        fuera["escrito"] = True
    except Exception as exc:  # noqa: BLE001 — la bitácora nunca rompe la venta
        log.warning("TIKTOK aviso %s: no se pudo marcar en ops.webhook_events: %s",
                    evento_id, str(exc)[:200])
    return fuera


def _resolver_previos(sdb, evento_id: int) -> None:
    """
    Los fallos ANTERIORES de la misma orden quedan resueltos: la pasada que salió
    bien le preguntó a TikTok el estado de ahora y ya lo aplicó. Sin esto seguían
    "por reintentar" en /flujo con la venta ya bien (24-sep: 6 de los 7 avisos
    fallidos eran de pedidos completos). Nunca lanza: es bitácora.
    """
    try:
        sdb.execute(
            """update ops.webhook_events p
                  set procesado = true, procesado_at = now(), next_retry_at = null,
                      resultado = left('resuelto por un aviso posterior · '
                                       || coalesce(p.resultado, ''), 255)
                 from ops.webhook_events e
                where e.id = %(id)s
                  and p.canal = 'tiktok' and p.external_id = e.external_id
                  and p.id < e.id and not p.procesado""",
            {"id": evento_id})
    except Exception as exc:  # noqa: BLE001
        log.warning("TIKTOK aviso %s: no se pudieron resolver los previos: %s",
                    evento_id, str(exc)[:200])


def _pendientes(limite: int) -> list[dict[str, Any]]:
    """⚠️ BLOQUEA. Avisos de TikTok vencidos para reintento, dentro del tope.

    SÓLO ids de orden NUMÉRICOS. La consulta va por `next_retry_at` con LIMIT: una
    fila que el reprocesador lee y no puede procesar sigue siendo la más antigua
    en la pasada siguiente. 25 avisos con `order_id` basura (la URL es pública)
    tapaban así el lote entero durante 48 h y la venta real no entraba nunca.
    """
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        """select id, external_id, intentos
             from ops.webhook_events
            where env = %(env)s
              and canal = 'tiktok'
              and not procesado
              and next_retry_at is not null
              and next_retry_at <= now()
              and intentos < %(tope)s
              and recibido_at >= now() - make_interval(hours => %(horas)s)
              and external_id ~ '^[0-9]+$'
            order by next_retry_at
            limit %(lim)s""",
        {"env": settings.app_env, "tope": tope(), "horas": _VENTANA_HORAS,
         "lim": int(limite)})
    return [dict(f) for f in filas]


async def reprocesar(limite: int = _LOTE) -> dict[str, Any]:
    """Una pasada del reprocesador. Nunca lanza."""
    from services import pedidos_tiktok, pedidos_tiktok_sondeo as sondeo
    from services.reintentos_freno import FRENO_TIKTOK, avisar_agotado

    r: dict[str, Any] = {"estado": "ok", "filas": 0, "ordenes": 0, "ok": 0, "creados": 0,
                         "terminales": 0, "reintentar": 0, "agotados": 0,
                         "con_huella": 0, "no_numericos": 0, "error": None}
    try:
        if not settings.pedidos_tiktok_enabled:
            # Sin pedidos, `procesar` contestaría "apagado" y cada fila gastaría
            # un intento sin que nada haya fallado de verdad.
            r.update(estado="omitido", error="PEDIDOS_TIKTOK_ENABLED apagado")
            return _cerrar(r)
        # El FRENO (reintentos_freno): detenido por exceso de pedidos creados, o
        # sin poder leer su estado → esta pasada no reintenta nada.
        detenido = await asyncio.to_thread(FRENO_TIKTOK.detenido)
        if detenido:
            r.update(estado="frenado", error=detenido.get("motivo"))
            return _cerrar(r)
        filas = await asyncio.to_thread(_pendientes, limite)
        r["filas"] = len(filas)
        por_orden: dict[str, list[dict[str, Any]]] = {}
        for f in filas:
            oid = str(f.get("external_id") or "")
            if oid.isdigit():
                por_orden.setdefault(oid, []).append(f)
                continue
            # La consulta ya los excluye; si alguno se cuela (filas de antes del
            # filtro, otra consulta), se CIERRA como terminal. Soltarlo sin
            # marcar lo dejaba primero en la fila de la pasada siguiente.
            r["no_numericos"] += 1
            m = await asyncio.to_thread(
                marcar, f.get("id"),
                {"ok": False, "terminal": True,
                 "motivo": "el aviso no trae un id de orden numérico"},
                int(f.get("intentos") or 0))
            if m.get("clase") == "terminal":
                r["terminales"] += 1
        r["ordenes"] = len(por_orden)
        if not por_orden:
            return _cerrar(r)

        # ¿Registradas? Las que no, pasan por el candado de huella.
        sin_registro: list[str] = []
        ilegibles: set[str] = set()
        for oid in por_orden:
            try:
                previo = await asyncio.to_thread(sondeo._previo, oid)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                ilegibles.add(oid)
                continue
            if not previo:
                sin_registro.append(oid)
        huella = await sondeo.filtrar_por_huella(sin_registro) if sin_registro else {
            "limpias": [], "con_huella": {}, "error": None}

        for oid, eventos in por_orden.items():
            if oid in ilegibles:
                res: dict[str, Any] = {"ok": False, "motivo": "registro ilegible; se reintenta"}
            elif oid in sin_registro and huella["error"]:
                res = {"ok": False, "motivo": f"huella ilegible: {huella['error'][:120]}"}
            elif oid in huella["con_huella"]:
                r["con_huella"] += 1
                res = {"ok": False, "terminal": True, "accion": "omitida_ya_registrada",
                       "motivo": f"dejó huella fuera de channel.orders "
                                 f"[{huella['con_huella'][oid]}]"}
            else:
                try:
                    res = await pedidos_tiktok.procesar(oid, reintentable=True)
                except Exception as exc:  # noqa: BLE001
                    res = {"ok": False, "motivo": f"{type(exc).__name__}: {str(exc)[:150]}"}
            agotado = False
            for ev in eventos:
                m = await asyncio.to_thread(marcar, ev.get("id"), res,
                                            int(ev.get("intentos") or 0))
                clase = m.get("clase")
                if clase == "ok":
                    r["ok"] += 1
                elif clase == "terminal":
                    r["terminales"] += 1
                elif m.get("agotado"):
                    r["agotados"] += 1
                    agotado = True
                else:
                    r["reintentar"] += 1
            if agotado:
                await asyncio.to_thread(avisar_agotado, "tiktok", oid,
                                        (res or {}).get("motivo") or "", tope())
            if isinstance(res, dict) and res.get("ok") and res.get("accion") == "creado":
                r["creados"] += 1
                if await asyncio.to_thread(FRENO_TIKTOK.anotar_creado, oid):
                    r.update(estado="frenado", error="límite de pedidos creados por hora")
                    break
    except Exception as exc:  # noqa: BLE001 — nunca lanza
        log.exception("tiktok_webhook_reintentos.reprocesar falló")
        r.update(estado="error", error=f"{type(exc).__name__}: {str(exc)[:200]}")
    return _cerrar(r)


def _cerrar(r: dict[str, Any]) -> dict[str, Any]:
    _ultimo.clear()
    _ultimo.update(r)
    if r.get("filas") or r.get("estado") == "error":
        log.info("TIKTOK reintentos de avisos: %s", r)
    return dict(r)
