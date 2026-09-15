"""
pedidos_tiktok_sondeo.py — Las ventas de TikTok también entran por SONDEO, como
respaldo del webhook.

POR QUÉ EXISTE (14-sep-2026)
────────────────────────────
El aviso de pedido de TikTok (`ORDER_STATUS_CHANGE`) es la ÚNICA vía por la que
una venta de TikTok se vuelve pedido, y no tiene red: el receptor contesta 200
siempre (si no, TikTok deshabilita la suscripción) y TikTok no reintenta lo que
nosotros no pudimos procesar. Medido en el diagnóstico del 14-sep: el aviso dejó
de llegar el 4-sep sin que nada lo dijera, una venta quedó atorada en
AWAITING_SHIPMENT desde el 14-ago en channel.orders, y 7 ventas entregadas de
13–22 ago no tienen orden en Odoo. Un silencio se ve igual que "no hubo ventas".

Este sondeo pregunta a TikTok directamente, cada N minutos, y hace dos cosas:
  1. Una venta que TikTok tiene y el registro NO (`channel.orders`) y cuyo
     estado sí genera pedido → `pedidos_tiktok.procesar`, el MISMO camino del
     webhook. Si además ningún aviso la trajo, deja el CANARIO en el log:
     "TIKTOK sondeo: venta que el webhook no trajo" — la señal de que la
     suscripción se rompió.
  2. Una venta YA registrada cuyo `estado_canal` difiere del status de TikTok →
     también `procesar`: así las CANCELACIONES y los cambios de estado llegan a
     Woo (y del seam a Odoo) aunque el aviso se haya perdido.

VENTANA FIJA POR `update_time`, SIN MARCA DE AGUA
─────────────────────────────────────────────────
La lección de Temu (v0.508.0): una marca de agua basada en cuándo REGISTRAMOS
nosotros se empuja a "ahora" en cada pasada, cierra la ventana sola, y lo que
queda fuera no se reintenta nunca porque nunca se vio. Aquí cada pasada mira
los últimos `PEDIDOS_TIKTOK_SONDEO_DIAS` por `update_time` —la fecha de TikTok,
no la nuestra— y pagina hasta agotar `next_page_token`. Volver a ver lo mismo
no cuesta: lo ya registrado y al día se salta antes de tocar Woo.

Por `update_time` y no por `create_time`: una cancelación de una venta de hace
dos semanas cambia HOY su `update_time`, y es justo lo que hay que alcanzar.

EL CANDADO DE HUELLA (el de la recuperación)
────────────────────────────────────────────
Una venta que channel.orders no conoce pero que YA dejó huella en la bitácora
de Odoo o en Odoo mismo (captura a mano con PDF `<id>.pdf`, ref `<id>#n`, otro
cliente) NO se procesa: `sincronizar` sólo pregunta a channel.orders, crearía
otro pedido en Woo, y la idempotencia de `crear_orden` tampoco la vería. Es
`tiktok_diagnostico._registros` con la misma regla que `recuperar`, más WOO
(`_ml_order_id`): un POST que se cortó pero sí creó el pedido. Y falla
CERRADO: si algún registro no se pudo leer, esa pasada no crea nada.

SÓLO VENTAS RECIENTES SIN REGISTRO
──────────────────────────────────
Una venta que ningún registro conoce se crea sola sólo si NACIÓ en las últimas
`PEDIDOS_TIKTOK_SONDEO_MAX_HORAS` (48 por omisión). Las más viejas se cuentan en
`nuevas_viejas`/`ids_nuevas_viejas` y entran con `recuperar` por ids. El candado
de huella NO ve una captura a mano guardada sin ref y antes de subir el PDF;
por eso, antes de apagar SOLO_REGISTRO, la captura a mano de TikTok tiene que
detenerse ese mismo día (decisión operativa, no de código).

NACE APAGADO Y EN SOLO REGISTRO
───────────────────────────────
`PEDIDOS_TIKTOK_SONDEO_ENABLED=false` (regla 3: crea pedidos y descuenta stock)
y, encendido, `PEDIDOS_TIKTOK_SONDEO_SOLO_REGISTRO=true`: clasifica, cuenta y
deja el canario, sin procesar nada. Y aun con las dos en su lugar, `procesar`
no crea nada sin `PEDIDOS_TIKTOK_ENABLED`.

SIN DATOS DEL COMPRADOR
───────────────────────
Cada orden pasa por `tiktok_diagnostico._orden_sin_pii` al leerse; el resto del
módulo sólo ve id, status y tiempos. El resumen lleva conteos e ids.

REGLA 11
────────
Token/cipher, `supabase_db` y `orders_write` (psycopg2) y el cruce con Odoo
(XML-RPC) van en `asyncio.to_thread`. Nunca lanza: lo llama el scheduler.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.pedidos_tiktok_sondeo")

CANAL = "tiktok"
CUENTA = "TIKTOK"
_RUTA_BUSCAR = "/order/202309/orders/search"
_PAGE_SIZE = 100          # máximo de orders/search
_MAX_PAGINAS = 50         # techo contra un token que no avanza, no un límite esperado
# Techo de ventas que se PROCESAN por pasada. Una ráfaga (reapertura tras días
# sin aviso) entra en tandas y el resto en la siguiente pasada: el resumen dice
# cuántas quedaron `diferidas`.
TOPE_PROCESAR = 50
_T_TIKTOK = 90
_T_BD = 60
_T_ODOO = 180

_ultimo: dict[str, Any] = {"estado": "sin_ejecutar"}


def estado() -> dict[str, Any]:
    return dict(_ultimo)


def _dias() -> int:
    try:
        d = int(getattr(settings, "pedidos_tiktok_sondeo_dias", 2) or 2)
    except (TypeError, ValueError):
        d = 2
    return max(1, min(d, 15))


def _max_horas() -> int:
    """Antigüedad máxima (create_time) de una venta sin registro que se crea sola."""
    try:
        h = int(getattr(settings, "pedidos_tiktok_sondeo_max_horas", 48) or 48)
    except (TypeError, ValueError):
        h = 48
    return max(1, min(h, 360))


def _err(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "tiempo agotado"
    return f"{type(exc).__name__}: {str(exc)[:200]}"


# ── Lecturas bloqueantes (siempre en hilo) ───────────────────────────────────

def _registro(ids: list[str]) -> dict[str, dict[str, Any]]:
    """⚠️ BLOQUEA. {id: {wc_order_id, estado_canal}} de channel.orders (TikTok).

    Lanza si kubera no contesta: sin registro no se puede decidir nada, y un
    "no está" inventado crearía pedidos duplicados.
    """
    from services import supabase_db as sdb
    if not ids:
        return {}
    filas = sdb.fetch_all(
        """select external_order_id, wc_order_id, estado_canal
             from channel.orders
            where (cuenta = 'TIKTOK' or canal = 'tiktok')
              and external_order_id = any(%(ids)s)""", {"ids": list(ids)})
    return {str(f["external_order_id"]): {"wc_order_id": f.get("wc_order_id"),
                                          "estado_canal": f.get("estado_canal")}
            for f in filas}


def _previo(oid: str) -> int | None:
    """⚠️ BLOQUEA. `orders_write.wc_order_id_previo`: channel.orders MÁS la
    absorción reciente de MySQL (lo que el fallback escribió con kubera caída).
    Propaga el error: "no sé" no es "no existe"."""
    from services import orders_write
    return orders_write.wc_order_id_previo(str(oid))


def _en_woo(ids: list[str]) -> dict[str, int]:
    """⚠️ BLOQUEA. {venta: wc_order_id} de las que YA tienen pedido en Woo
    (meta `_ml_order_id`, fuera de la papelera). Propaga: "no sé" no es "no
    existe"."""
    from services import wp_db
    return wp_db.pedidos_por_ml_order_ids(list(ids))


def _avisadas(ids: list[str]) -> set[str]:
    """⚠️ BLOQUEA. Las ventas de las que SÍ llegó algún aviso de TikTok.

    `ops.webhook_events` guarda TikTok 90 días (migración 0050) y la ventana del
    sondeo es de días, así que "sin fila" significa "el aviso no llegó".
    """
    from services import supabase_db as sdb
    if not ids:
        return set()
    filas = sdb.fetch_all(
        """select distinct external_id
             from ops.webhook_events
            where canal = 'tiktok' and external_id = any(%(ids)s)""",
        {"ids": list(ids)})
    return {str(f["external_id"]) for f in filas}


# ── TikTok ───────────────────────────────────────────────────────────────────

async def _credenciales() -> tuple[str | None, str | None]:
    from services import tiktok as tk
    return await asyncio.wait_for(
        asyncio.to_thread(lambda: (tk.access_token(), tk.cipher())), _T_BD)


async def _buscar(token: str, ciph: str, desde: int) -> dict[str, Any]:
    """
    Pagina `orders/search` por `update_time_ge` hasta agotar `next_page_token`.

    `page_size`/`page_token` van en la query y el filtro en el cuerpo (que entra
    en la firma: `tk.llamar` ya lo hace). Deduplica por id y corta si el token
    se repite. Cada orden pasa por la lista blanca al leerse.
    """
    from services import tiktok as tk
    from services.tiktok_diagnostico import _orden_sin_pii

    vistas: dict[str, dict[str, Any]] = {}
    page_token = ""
    tokens_vistos: set[str] = set()
    paginas, cortado, error = 0, False, None
    while True:
        if paginas >= _MAX_PAGINAS:
            cortado = True
            break
        params: dict[str, Any] = {"shop_cipher": ciph, "page_size": _PAGE_SIZE}
        if page_token:
            params["page_token"] = page_token
        try:
            data = await asyncio.wait_for(
                tk.llamar(_RUTA_BUSCAR, token, params, {"update_time_ge": int(desde)},
                          "POST"), _T_TIKTOK)
        except Exception as exc:  # noqa: BLE001
            error = f"orders/search página {paginas + 1}: {_err(exc)}"
            break
        data = data if isinstance(data, dict) else {}
        paginas += 1
        lote = data.get("orders") or []
        for o in lote:
            if isinstance(o, dict):
                f = _orden_sin_pii(o)
                if f["id"]:
                    vistas[f["id"]] = f
        page_token = str(data.get("next_page_token") or "")
        if not page_token or not lote:
            break
        if page_token in tokens_vistos:
            error = "orders/search repitió el next_page_token; paginación detenida"
            break
        tokens_vistos.add(page_token)
    return {"ordenes": vistas, "paginas": paginas, "cortado_por_tope": cortado,
            "error": error}


# ── El candado de huella (compartido con el reprocesador de avisos) ─────────

async def filtrar_por_huella(ids: list[str], desde: int | None = None) -> dict[str, Any]:
    """
    De ventas que channel.orders NO conoce, cuáles se pueden procesar.

    Misma regla que `tiktok_diagnostico.recuperar._registro_previo`: si la venta
    apareció en channel.orders entretanto, pasa (sincronizar es idempotente);
    si no está ahí pero sí en la bitácora o en Odoo, se OMITE — procesarla
    duplicaría el pedido de Woo y/o la orden de Odoo.

    EL CUARTO REGISTRO ES WOO. El webhook procesa con `reintentable=False`: si el
    POST a Woo se corta DESPUÉS de crear el pedido y Woo tarda más que la mirada
    de `sincronizar` en hacerlo visible, el reclamo se SUELTA (la fila de
    channel.orders desaparece). A los 2 min el reprocesador —o el sondeo— gana
    un reclamo nuevo, y con reclamo propio `sincronizar` hace el POST sin
    preguntarle a Woo: pedido gemelo y la pieza descontada dos veces. Aquí se le
    pregunta a Woo (por `_ml_order_id`) por lo que channel.orders no conoce; si
    Woo lo tiene, se omite con huella `woo` y lo concilia una persona.
    (Con fila en channel.orders no hace falta: el reclamo ajeno ya adopta.)

    Falla CERRADO: con cualquier registro ilegible no pasa ninguna.

    → {"limpias": [ids], "con_huella": {id: "bitacora+odoo_pdf+woo"}, "error": str|None}
    """
    from services import tiktok_diagnostico as td

    fuera: dict[str, Any] = {"limpias": [], "con_huella": {}, "error": None}
    if not ids:
        return fuera
    try:
        reg = await td._registros(list(ids), desde)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        fuera["error"] = _err(exc)
        return fuera
    if reg.get("errores"):
        fuera["error"] = "; ".join(reg["errores"])[:300]
        return fuera
    canal, bitacora, odoo = reg["channel_orders"], reg["bitacora"], reg["odoo"]
    fuera_de_canal = [oid for oid in ids if oid not in canal]
    woo: dict[str, int] = {}
    if fuera_de_canal:
        try:
            woo = await asyncio.wait_for(asyncio.to_thread(_en_woo, fuera_de_canal), _T_BD)
        except Exception as exc:  # noqa: BLE001 — "no sé" no es "no existe"
            fuera["error"] = f"woo: {_err(exc)}"
            return fuera
    for oid in ids:
        if oid in canal:
            fuera["limpias"].append(oid)
            continue
        huellas = (["bitacora"] if oid in bitacora else []) + (
            [f"odoo_{odoo[oid]}"] if odoo.get(oid) else []) + (
            ["woo"] if woo.get(oid) else [])
        if huellas:
            fuera["con_huella"][oid] = "+".join(huellas)
        else:
            fuera["limpias"].append(oid)
    return fuera


# ── La pasada ────────────────────────────────────────────────────────────────

async def revisar(dias: int | None = None,
                  solo_registro: bool | None = None) -> dict[str, Any]:
    """Una pasada del sondeo. Nunca lanza. Ver el encabezado del módulo."""
    from services import pedidos_tiktok
    from services.tiktok_diagnostico import _genera_pedido

    if solo_registro is None:
        solo_registro = bool(getattr(settings, "pedidos_tiktok_sondeo_solo_registro", True))
    dias = _dias() if dias is None else max(1, min(int(dias), 15))
    # VENTANA FIJA: ahora menos N días, siempre. Nada de marca de agua.
    ahora = int(time.time())
    desde = ahora - dias * 86400
    max_horas = _max_horas()
    # Una venta SIN REGISTRO sólo se crea sola si NACIÓ hace poco (create_time).
    corte_nuevas = ahora - max_horas * 3600
    r: dict[str, Any] = {
        "estado": "ok", "ts": datetime.now(timezone.utc).isoformat(),
        "solo_registro": solo_registro, "dias": dias, "max_horas": max_horas,
        "desde": datetime.fromtimestamp(desde, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pedidos_tiktok_enabled": bool(settings.pedidos_tiktok_enabled),
        "vistas": 0, "paginas": 0, "cortado_por_tope": False,
        "al_dia": 0, "cambios_estado": 0, "cambio_sin_mapear": 0,
        "nuevas": 0, "nuevas_no_vendibles": 0, "nuevas_viejas": 0, "con_huella": 0,
        "sin_webhook": 0, "canario_sin_verificar": False,
        "habria_procesado": 0, "procesadas_ok": 0, "procesadas_error": 0,
        "diferidas": 0, "registro_ilegible": 0,
        "ids_sin_webhook": [], "ids_con_huella": {}, "ids_cambio_estado": [],
        "ids_nuevas_viejas": [],
        "errores": [], "error": None,
    }
    try:
        token, ciph = await _credenciales()
        if not (token and ciph):
            r.update(estado="error", error="TikTok sin token o sin shop_cipher")
            return _cerrar(r)
        b = await _buscar(token, ciph, desde)
        vistas: dict[str, dict[str, Any]] = b["ordenes"]
        r.update(vistas=len(vistas), paginas=b["paginas"],
                 cortado_por_tope=b["cortado_por_tope"])
        if b["error"]:
            r["errores"].append(b["error"])
        if not vistas:
            return _cerrar(r)

        ids = list(vistas)
        try:
            reg = await asyncio.wait_for(asyncio.to_thread(_registro, ids), _T_BD)
        except Exception as exc:  # noqa: BLE001 — sin registro no se decide nada
            r.update(estado="error", error=f"channel.orders: {_err(exc)}")
            return _cerrar(r)

        reprocesar: list[str] = []
        nuevas: list[str] = []
        viejas: list[str] = []
        for oid in ids:
            status = vistas[oid]["status"]
            fila = reg.get(oid)
            if fila and fila.get("wc_order_id"):
                previo_estado = str(fila.get("estado_canal") or "").strip().upper()
                if previo_estado == status:
                    r["al_dia"] += 1
                elif status in pedidos_tiktok._ESTADOS_WC:  # noqa: SLF001
                    reprocesar.append(oid)
                else:
                    r["cambio_sin_mapear"] += 1
                continue
            # Sin fila (o reclamada sin pedido): se confirma con la lectura
            # completa del candado, que también ve la absorción de MySQL.
            try:
                previo = await asyncio.wait_for(asyncio.to_thread(_previo, oid), _T_BD)
            except Exception as exc:  # noqa: BLE001 — "no sé" no es "no existe"
                r["registro_ilegible"] += 1
                r["errores"].append(f"registro {oid}: {_err(exc)}")
                continue
            if previo:
                # Registrada en la absorción: sin estado_canal que comparar. La
                # alcanzará la pasada en que kubera la tenga.
                r["al_dia"] += 1
                continue
            if not _genera_pedido(status):
                # UNPAID, ON_HOLD, CANCELLED sin registrar: no es venta que surtir.
                r["nuevas_no_vendibles"] += 1
                continue
            creada = vistas[oid]["create_time"]
            if oid not in reg and (creada is None or creada < corte_nuevas):
                # VIEJA SIN REGISTRO: no se crea sola. La ventana es por
                # update_time, y un cambio de estado de una venta de hace semanas
                # la mete aquí — justo las que más probablemente ya se
                # capturaron a mano (el candado de huella no ve una captura sin
                # ref y sin PDF). Se nombra para `recuperar`, que va por ids.
                # (Un reclamo sin pedido en channel.orders sí pasa a cualquier
                # edad: lo empezó nuestra tubería y `sincronizar` lo adopta.)
                viejas.append(oid)
                continue
            nuevas.append(oid)
        r["nuevas"] = len(nuevas)
        r["nuevas_viejas"] = len(viejas)
        r["ids_nuevas_viejas"] = viejas[:30]
        if viejas:
            log.warning("TIKTOK sondeo: %d venta(s) sin registrar con más de %d h de "
                        "creadas: NO se crean solas; revisar y entrar con "
                        "/tiktok/recuperar por ids: %s", len(viejas), max_horas,
                        ", ".join(viejas[:30]))
        r["cambios_estado"] = len(reprocesar)
        r["ids_cambio_estado"] = [f"{o}:{reg[o].get('estado_canal')}→{vistas[o]['status']}"
                                  for o in reprocesar[:30]]

        # ── EL CANARIO ──────────────────────────────────────────────────────
        if nuevas:
            try:
                avisadas = await asyncio.wait_for(asyncio.to_thread(_avisadas, nuevas), _T_BD)
                sin_aviso = [o for o in nuevas if o not in avisadas]
                r["sin_webhook"] = len(sin_aviso)
                r["ids_sin_webhook"] = sin_aviso[:30]
                for oid in sin_aviso:
                    log.warning("TIKTOK sondeo: venta que el webhook no trajo: %s "
                                "status=%s — revisar la suscripción ORDER_STATUS_CHANGE",
                                oid, vistas[oid]["status"])
            except Exception as exc:  # noqa: BLE001
                r["canario_sin_verificar"] = True
                r["errores"].append(f"ops.webhook_events: {_err(exc)}")
                log.warning("TIKTOK sondeo: %d venta(s) sin registrar y NO se pudo "
                            "verificar si el webhook las trajo: %s", len(nuevas), _err(exc))

        # ── EL CANDADO DE HUELLA ────────────────────────────────────────────
        limpias: list[str] = []
        if nuevas:
            viejo = min((vistas[o]["create_time"] for o in nuevas
                         if vistas[o]["create_time"] is not None), default=desde)
            try:
                h = await asyncio.wait_for(filtrar_por_huella(nuevas, min(viejo, desde)),
                                           _T_ODOO + 3 * _T_BD)
            except Exception as exc:  # noqa: BLE001 — un techo vencido cierra, no aborta
                h = {"limpias": [], "con_huella": {}, "error": _err(exc)}
            if h["error"]:
                r["errores"].append(f"huella (falla cerrado, no se crea ninguna): {h['error']}")
            else:
                limpias = h["limpias"]
                r["con_huella"] = len(h["con_huella"])
                r["ids_con_huella"] = dict(list(h["con_huella"].items())[:30])

        # ── PROCESAR ────────────────────────────────────────────────────────
        cola = reprocesar + limpias
        if len(cola) > TOPE_PROCESAR:
            r["diferidas"] = len(cola) - TOPE_PROCESAR
            cola = cola[:TOPE_PROCESAR]
        if solo_registro:
            r["habria_procesado"] = len(cola)
        else:
            for oid in cola:
                try:
                    res = await pedidos_tiktok.procesar(oid, reintentable=True)
                except Exception as exc:  # noqa: BLE001 — una mala no tumba las demás
                    res = {"ok": False, "motivo": _err(exc)}
                res = res if isinstance(res, dict) else {}
                if res.get("ok"):
                    r["procesadas_ok"] += 1
                else:
                    r["procesadas_error"] += 1
                    r["errores"].append(f"{oid}: {str(res.get('motivo'))[:120]}")
        if r["errores"]:
            r["error"] = "; ".join(r["errores"])[:500]
    except Exception as exc:  # noqa: BLE001 — nunca lanza
        log.exception("pedidos_tiktok_sondeo.revisar falló")
        r.update(estado="error", error=_err(exc))
    return _cerrar(r)


def _cerrar(r: dict[str, Any]) -> dict[str, Any]:
    """Guarda la pasada y deja UNA línea en el log (conteos e ids, sin PII)."""
    r["errores"] = r.get("errores", [])[:10]
    _ultimo.clear()
    _ultimo.update(r)
    try:
        log.info(
            "TIKTOK sondeo: %s · %s días · %d vistas en %d pág%s · %d al día · "
            "%d cambios de estado · %d nuevas (%d sin webhook, %d con huella) · "
            "%d viejas sin registro · %d no vendibles · %s%s",
            r.get("estado"), r.get("dias"), r.get("vistas", 0), r.get("paginas", 0),
            " CORTADO" if r.get("cortado_por_tope") else "", r.get("al_dia", 0),
            r.get("cambios_estado", 0), r.get("nuevas", 0), r.get("sin_webhook", 0),
            r.get("con_huella", 0), r.get("nuevas_viejas", 0),
            r.get("nuevas_no_vendibles", 0),
            (f"habría procesado {r.get('habria_procesado', 0)}" if r.get("solo_registro")
             else f"procesadas ok={r.get('procesadas_ok', 0)} "
                  f"error={r.get('procesadas_error', 0)}"),
            f" · error={str(r.get('error'))[:300]}" if r.get("error") else "")
    except Exception:  # noqa: BLE001
        pass
    return dict(r)
