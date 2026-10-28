"""
pedidos_ml_sondeo.py — Sondeo de las ventas de Mercado Libre (PLAN MAESTRO 2.11, F2.3).

POR QUÉ EXISTE (28-oct-2026). Las ventas de ML entran SOLO por el webhook `orders_v2`.
Si el aviso no llega o falla y nadie lo reintenta, la venta no existe para nosotros
hasta que llegue otro aviso de la misma orden, que puede tardar días o no llegar:
  · caída de DNS del 24-sep: 13 ventas pagadas sin pedido, rescatadas a mano;
  · brecha de Railway del 21/22-oct: 162 ventas (02:48–17:14 UTC) medidas desde fuera
    contra ML y recuperadas a mano el 23-oct.
Amazon, TikTok y Temu tienen sondeo; ML no tenía.

QUÉ HACE (con PEDIDOS_ML_SONDEO_ENABLED)
  · Cada PEDIDOS_ML_SONDEO_MIN (15) pregunta a ML, por cuenta, las órdenes ACTUALIZADAS
    en las últimas PEDIDOS_ML_SONDEO_HORAS (24): `GET /orders/search` con
    `order.date_last_updated.from`, 50 por página, hasta PEDIDOS_ML_SONDEO_PAGINAS.
    Una ventana FIJA hacia atrás, nunca una marca de agua: la lección de Temu
    (`pedidos_temu_sondeo._desde`) es que una marca que avanza con nuestro trabajo
    convierte cualquier hueco en permanente.
  · Las compara con `channel.orders` y clasifica cada una:
       falta          ML la tiene y kubera no.
       sin_pedido     kubera la tiene sin `wc_order_id` hace más de UMBRAL_MIN.
       atrasada       ML la cambió de estado hace más de UMBRAL_MIN y kubera conserva
                      otro (`estado_canal`): un aviso de cancelación que no llegó.
       al_dia         lo demás.
  · SOLO REGISTRO (PEDIDOS_ML_SONDEO_SOLO_REGISTRO, por omisión): cuenta, deja el
    resultado en `estado()` y avisa por Slack al cambiar de estado. No toca nada.
  · Sin solo registro: las de las tres clases (hasta PEDIDOS_ML_SONDEO_LOTE por pasada)
    pasan por el MISMO camino que los reintentos del aviso: `meli.obtener_orden` +
    `pedidos_ml.sincronizar(reintentable=True)` — con el candado anti-duplicados de
    los sondeos — y por el MISMO freno de pedidos creados por hora (FRENO_ML).

Solo GET contra ML. Un 401 se resuelve con el renovador del backend
(`meli._renovar_con_candado`, compartido y con candado por cuenta): este módulo no
renueva tokens por su cuenta. Nunca lanza.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.pedidos_ml_sondeo")

CUENTAS = ("BEKURA", "SANCORFASHION")
_POR_PAGINA = 50
_TIPO_ALERTA = "pedidos_ml_sondeo"
_uids: dict[str, int] = {}
_ultimo: dict[str, Any] = {"estado": "sin_ejecutar"}


def estado() -> dict[str, Any]:
    return {"habilitado": settings.pedidos_ml_sondeo_enabled,
            "solo_registro": settings.pedidos_ml_sondeo_solo_registro, **_ultimo}


async def _get(cli, cuenta: str, ruta: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
    """GET con el token de la cuenta. Un 401 pasa por el renovador compartido del backend
    UNA vez; lo demás se devuelve tal cual (el llamador decide)."""
    from services import meli
    token = await meli.access_token_async(cuenta)
    if not token:
        return 0, None
    r = await cli.get(ruta, params=params, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 401:
        nuevo = await meli._renovar_con_candado(cuenta)
        if not nuevo:
            return 401, None
        r = await cli.get(ruta, params=params, headers={"Authorization": f"Bearer {nuevo}"})
    try:
        return r.status_code, (r.json() if r.status_code in (200, 206) else None)
    except Exception:  # noqa: BLE001
        return r.status_code, None


async def _uid(cli, cuenta: str) -> int | None:
    if cuenta not in _uids:
        code, d = await _get(cli, cuenta, "/users/me")
        if code == 200 and isinstance(d, dict) and d.get("id"):
            _uids[cuenta] = int(d["id"])
    return _uids.get(cuenta)


def _fecha(v: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v)).astimezone(timezone.utc) if v else None
    except ValueError:
        return None


async def leer_ml(cuenta: str, horas: int, paginas: int, cli=None) -> dict[str, Any]:
    """Las órdenes de `cuenta` actualizadas en las últimas `horas`, según ML.
    → {"ordenes": {id: {"estado", "creada", "actualizada"}}, "paginas", "total", "error"}"""
    import httpx
    from services import meli
    propio = cli is None
    cli = cli or httpx.AsyncClient(base_url=meli._API, timeout=30.0)
    salida: dict[str, Any] = {"ordenes": {}, "paginas": 0, "total": None, "error": None}
    try:
        uid = await _uid(cli, cuenta)
        if not uid:
            salida["error"] = "sin user_id (token o /users/me)"
            return salida
        desde = (datetime.now(timezone.utc) - timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%S.000-00:00")
        offset = 0
        for _ in range(paginas):
            code, d = await _get(cli, cuenta, "/orders/search", {
                "seller": uid, "order.date_last_updated.from": desde,
                "sort": "date_asc", "limit": _POR_PAGINA, "offset": offset})
            if code != 200 or not isinstance(d, dict):
                salida["error"] = f"/orders/search → HTTP {code} en offset {offset}"
                return salida
            salida["paginas"] += 1
            filas = d.get("results") or []
            for o in filas:
                if o.get("id"):
                    salida["ordenes"][str(o["id"])] = {
                        "estado": o.get("status"), "pack": str(o.get("pack_id") or "") or None,
                        "creada": _fecha(o.get("date_created")),
                        "actualizada": _fecha(o.get("last_updated") or o.get("date_last_updated"))}
            salida["total"] = (d.get("paging") or {}).get("total") or 0
            offset += _POR_PAGINA
            if offset >= salida["total"] or not filas:
                break
        else:
            salida["error"] = f"tope de {paginas} páginas: la ventana trae {salida['total']} órdenes"
    except Exception as exc:  # noqa: BLE001
        salida["error"] = f"{type(exc).__name__}: {str(exc)[:150]}"
    finally:
        if propio:
            await cli.aclose()
    return salida


_SQL_KUBERA = """
select external_order_id, wc_order_id, estado_canal,
       coalesce((to_jsonb(o) ->> 'registrado_at')::timestamptz, o.creado_at) as registrado
  from channel.orders o
 where canal = 'mercado_libre' and cuenta = %(c)s and external_order_id = any(%(ids)s)
"""


def kubera(cuenta: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    """⚠️ BLOQUEA. Lo que kubera tiene de esas órdenes (o de sus packs: `ids` puede traer
    los dos números; la medición del hueco del 22-oct comparó igual)."""
    from services import supabase_db as sdb
    if not ids:
        return {}
    return {f["external_order_id"]: f for f in sdb.fetch_all(_SQL_KUBERA, {"c": cuenta, "ids": ids})}


def clasificar(ml: dict[str, dict[str, Any]], kb: dict[str, dict[str, Any]], ahora: datetime,
               umbral_min: int) -> dict[str, list[str]]:
    """Cada orden de ML en su clase. Una orden recién tocada (menos de `umbral_min`) se
    deja al webhook: puede venir en camino."""
    corte = ahora - timedelta(minutes=umbral_min)
    clases: dict[str, list[str]] = {"falta": [], "sin_pedido": [], "atrasada": [], "al_dia": [],
                                    "reciente": []}
    for oid, o in ml.items():
        k = kb.get(oid) or (kb.get(o["pack"]) if o.get("pack") else None)
        act = o.get("actualizada") or o.get("creada")
        if act and act > corte:
            clases["reciente"].append(oid)
        elif k is None:
            clases["falta"].append(oid)
        elif k.get("wc_order_id") is None and (k.get("registrado") is None or k["registrado"] < corte):
            clases["sin_pedido"].append(oid)
        elif o.get("estado") and (k.get("estado_canal") or "") != o["estado"]:
            clases["atrasada"].append(oid)
        else:
            clases["al_dia"].append(oid)
    return clases


async def _reparar(cuenta: str, ids: list[str], r: dict[str, Any]) -> None:
    """Las pasa por el camino de los reintentos del aviso. Para en el freno."""
    from services import meli, pedidos_ml
    from services.reintentos_freno import FRENO_ML
    for oid in ids:
        try:
            orden = await meli.obtener_orden(oid)
            rp = (await pedidos_ml.sincronizar(oid, orden=orden,
                                               proteger_stock=not settings.pedidos_wc_descuenta_stock,
                                               reintentable=True)
                  if orden else {"ok": False, "motivo": "no se pudo traer la orden"})
        except Exception as exc:  # noqa: BLE001
            rp = {"ok": False, "motivo": f"{type(exc).__name__}: {str(exc)[:150]}"}
        if rp.get("ok"):
            r["reparadas"] += 1
            if rp.get("accion") == "creado":
                r["creados"] += 1
                if await asyncio.to_thread(FRENO_ML.anotar_creado, oid):
                    r.update(estado="frenado", error="límite de pedidos creados por hora")
                    return
        else:
            r["fallidas"] += 1
            log.warning("sondeo ML %s %s: %s", cuenta, oid, rp.get("motivo"))


async def revisar(solo_registro: bool | None = None) -> dict[str, Any]:
    """Una pasada del sondeo. Nunca lanza."""
    global _ultimo
    solo = settings.pedidos_ml_sondeo_solo_registro if solo_registro is None else solo_registro
    horas, paginas = int(settings.pedidos_ml_sondeo_horas), int(settings.pedidos_ml_sondeo_paginas)
    umbral, lote = int(settings.pedidos_ml_sondeo_umbral_min), int(settings.pedidos_ml_sondeo_lote)
    r: dict[str, Any] = {"estado": "ok", "solo_registro": solo, "horas": horas, "cuentas": {},
                         "reparadas": 0, "creados": 0, "fallidas": 0, "error": None,
                         "inicio": datetime.now(timezone.utc).isoformat()}
    try:
        from services.reintentos_freno import FRENO_ML
        if not solo:
            if not settings.pedidos_wc_enabled:
                r.update(estado="omitido", error="PEDIDOS_WC_ENABLED apagado")
                solo = True
            else:
                detenido = await asyncio.to_thread(FRENO_ML.detenido)
                if detenido:
                    r.update(estado="frenado", error=detenido.get("motivo"))
                    solo = True
        pendientes: list[tuple[str, str]] = []
        for cuenta in CUENTAS:
            ml = await leer_ml(cuenta, horas, paginas)
            kb = await asyncio.to_thread(kubera, cuenta, sorted(
                set(ml["ordenes"]) | {o["pack"] for o in ml["ordenes"].values() if o.get("pack")}))
            cl = clasificar(ml["ordenes"], kb, datetime.now(timezone.utc), umbral)
            r["cuentas"][cuenta] = {"ml": len(ml["ordenes"]), "paginas": ml["paginas"], "error": ml["error"],
                                    **{k: len(v) for k, v in cl.items()},
                                    "muestra": {k: v[:5] for k, v in cl.items()
                                                if k in ("falta", "sin_pedido", "atrasada") and v}}
            if ml["error"]:
                r["estado"] = "incompleto"
            pendientes += [(cuenta, oid) for k in ("falta", "sin_pedido", "atrasada") for oid in cl[k]]
        r["por_reparar"] = len(pendientes)
        if not solo and pendientes:
            for cuenta in CUENTAS:
                ids = [o for c, o in pendientes if c == cuenta][:max(0, lote - r["reparadas"] - r["fallidas"])]
                if ids and r["estado"] != "frenado":
                    await _reparar(cuenta, ids, r)
        from services import alertas
        malas = {c: v for c, v in r["cuentas"].items() if v["falta"] or v["sin_pedido"] or v["atrasada"]}
        texto = "; ".join(f"{c}: {v['falta']} faltan, {v['sin_pedido']} sin pedido, {v['atrasada']} atrasadas"
                          for c, v in malas.items())
        alertas.avisar_estado(
            _TIPO_ALERTA, "diferencias" if malas else "ok",
            f"*Sondeo de ventas ML* ({horas} h): {texto}. "
            + ("Solo registro: nadie las repara." if solo else f"Reparadas {r['reparadas']}, creadas {r['creados']}."),
            texto_ok="*Sondeo de ventas ML*: kubera tiene todas las ventas de ML de la ventana.")
    except Exception as exc:  # noqa: BLE001
        log.exception("pedidos_ml_sondeo.revisar falló")
        r.update(estado="error", error=f"{type(exc).__name__}: {str(exc)[:200]}")
    r["fin"] = datetime.now(timezone.utc).isoformat()
    _ultimo = r
    log.info("sondeo ML: %s", {c: {k: v for k, v in d.items() if k != "muestra"} for c, d in r["cuentas"].items()})
    return r
