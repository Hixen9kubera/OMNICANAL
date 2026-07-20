"""
ventas_ml.py — Ventas por hora desde Mercado Libre, con comparativa semanal.

Alimenta el tab VENTAS del panel: cuánto se vendió cada hora del día
(00:00–23:00), por cuenta (BEKURA / SANCORFASHION) o sumado, siempre comparado
contra el MISMO rango de la semana pasada.

DE DÓNDE SALEN LOS DATOS
------------------------
Directo de la API de órdenes de ML (`/orders/search` filtrado por
`order.date_created`), NO de Supabase ni del catálogo: el catálogo cambia de
precio todo el tiempo; la orden trae el precio REAL al que se vendió.

CACHÉ (tabla `ventas_horarias`)
-------------------------------
Un día de una cuenta son ~4-10 páginas de la API de ML. Sin caché, "últimos
7 días + su comparativa" serían ~150 llamadas por carga de página. Por eso cada
(cuenta, día) se agrega UNA vez a 24 renglones por hora y se guarda en MySQL:

  - día pasado (>2 días): FINAL, nunca se vuelve a pedir a ML
  - ayer/antier: se refresca si tiene >15 min (cancelaciones tardías)
  - HOY: se refresca si tiene >3 min (ventas en vivo)

HORA LOCAL
----------
Todo se bucketiza en hora de Ciudad de México. México abolió el horario de
verano en 2022, así que CDMX es UTC-6 fijo — no hace falta tzdata.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import httpx

from config import settings
from services import db, meli

log = logging.getLogger("omnicanal.ventas")

_API = "https://api.mercadolibre.com"
_TZ_MX = timezone(timedelta(hours=-6))
_CUENTAS_ML = ("BEKURA", "SANCORFASHION")
_MAX_PAGINAS = 60  # tope de seguridad: 60*50 = 3,000 órdenes/día/cuenta

# TTL de frescura del caché según qué tan viejo es el día consultado.
_TTL_HOY = 180        # 3 min: ventas en vivo
_TTL_RECIENTE = 900   # 15 min: ayer/antier (cancelaciones tardías)
_DIAS_FINALES = 2     # más viejo que esto ya no se re-consulta

_DDL_HORAS = """
CREATE TABLE IF NOT EXISTS ventas_horarias (
    canal     VARCHAR(20) NOT NULL DEFAULT 'mercado_libre',
    cuenta    VARCHAR(50) NOT NULL,
    fecha     DATE        NOT NULL,
    hora      TINYINT     NOT NULL,
    pedidos   INT         NOT NULL DEFAULT 0,
    unidades  INT         NOT NULL DEFAULT 0,
    monto     DECIMAL(14,2) NOT NULL DEFAULT 0,
    canceladas INT        NOT NULL DEFAULT 0,
    monto_cancelado DECIMAL(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (canal, cuenta, fecha, hora)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""
_DDL_SYNC = """
CREATE TABLE IF NOT EXISTS ventas_sync (
    canal       VARCHAR(20) NOT NULL DEFAULT 'mercado_libre',
    cuenta      VARCHAR(50) NOT NULL,
    fecha       DATE        NOT NULL,
    actualizado DATETIME    NOT NULL,
    final       TINYINT(1)  NOT NULL DEFAULT 0,
    PRIMARY KEY (canal, cuenta, fecha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""
_schema_ok = False
_seller_ids: dict[str, int] = {}
# Un solo refresco a la vez por (cuenta, fecha): si el frontend pide General
# (2 cuentas × N días) no queremos martillar a ML con lo mismo en paralelo.
_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _asegurar_schema() -> None:
    global _schema_ok
    if _schema_ok:
        return
    try:
        with db.get_cursor() as cur:
            cur.execute(_DDL_HORAS)
            cur.execute(_DDL_SYNC)
        _schema_ok = True
    except Exception as exc:  # noqa: BLE001
        log.error("No se pudo crear tablas de ventas: %s", exc)


def hoy_mx() -> date:
    return datetime.now(_TZ_MX).date()


async def _seller_id(cuenta: str, token: str, cli: httpx.AsyncClient) -> int | None:
    if cuenta in _seller_ids:
        return _seller_ids[cuenta]
    r = await cli.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        return None
    sid = r.json().get("id")
    if sid:
        _seller_ids[cuenta] = int(sid)
    return _seller_ids.get(cuenta)


def _hora_mx(iso: str) -> int:
    """Hora local CDMX (0–23) de un timestamp ISO de ML (trae su propio offset)."""
    try:
        return datetime.fromisoformat(iso).astimezone(_TZ_MX).hour
    except Exception:  # noqa: BLE001
        return 0


async def _agregar_dia(cuenta: str, fecha: date) -> list[dict] | None:
    """
    Pide a ML TODAS las órdenes de la cuenta creadas ese día (hora CDMX) y las
    agrega en 24 buckets. None si no hay token o falló la API.
    """
    token = meli._access_token(cuenta)
    if not token:
        log.warning("Ventas: sin token para %s", cuenta)
        return None
    buckets = [{"pedidos": 0, "unidades": 0, "monto": 0.0,
                "canceladas": 0, "monto_cancelado": 0.0} for _ in range(24)]
    desde = f"{fecha.isoformat()}T00:00:00.000-06:00"
    hasta = f"{fecha.isoformat()}T23:59:59.999-06:00"
    try:
        async with httpx.AsyncClient(base_url=_API, timeout=25.0) as cli:
            sid = await _seller_id(cuenta, token, cli)
            if not sid:
                return None
            cab = {"Authorization": f"Bearer {token}"}
            offset, total = 0, None
            for _ in range(_MAX_PAGINAS):
                r = await cli.get(
                    "/orders/search",
                    params={"seller": sid, "limit": 50, "offset": offset,
                            "sort": "date_asc",
                            "order.date_created.from": desde,
                            "order.date_created.to": hasta},
                    headers=cab)
                if r.status_code != 200:
                    log.warning("Ventas %s %s: HTTP %s", cuenta, fecha, r.status_code)
                    return None
                data = r.json()
                total = (data.get("paging") or {}).get("total", 0)
                filas = data.get("results") or []
                if not filas:
                    break
                for o in filas:
                    h = _hora_mx(str(o.get("date_created") or ""))
                    monto = float(o.get("total_amount") or 0)
                    unidades = sum(int(oi.get("quantity") or 0)
                                   for oi in o.get("order_items", []))
                    if o.get("status") == "cancelled":
                        buckets[h]["canceladas"] += 1
                        buckets[h]["monto_cancelado"] += monto
                    elif o.get("status") == "paid":
                        buckets[h]["pedidos"] += 1
                        buckets[h]["unidades"] += unidades
                        buckets[h]["monto"] += monto
                    # otros estados (pago pendiente/en proceso) no se cuentan
                offset += 50
                if offset >= (total or 0):
                    break
    except Exception as exc:  # noqa: BLE001
        log.warning("Ventas %s %s: %s", cuenta, fecha, exc)
        return None
    return buckets


def _guardar_dia(cuenta: str, fecha: date, buckets: list[dict], final: bool) -> None:
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with db.get_cursor() as cur:
            filas = [("mercado_libre", cuenta, fecha, h,
                      b["pedidos"], b["unidades"], round(b["monto"], 2),
                      b["canceladas"], round(b["monto_cancelado"], 2))
                     for h, b in enumerate(buckets)]
            cur.executemany(
                """REPLACE INTO ventas_horarias
                   (canal, cuenta, fecha, hora, pedidos, unidades, monto,
                    canceladas, monto_cancelado)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", filas)
            cur.execute(
                """REPLACE INTO ventas_sync (canal, cuenta, fecha, actualizado, final)
                   VALUES ('mercado_libre',%s,%s,%s,%s)""",
                (cuenta, fecha, ahora, 1 if final else 0))
    except Exception as exc:  # noqa: BLE001
        log.warning("Ventas: no se pudo guardar %s %s: %s", cuenta, fecha, exc)


def _vencido(fila: dict | None, fecha: date) -> bool:
    """¿El caché de ese día falta o ya caducó su TTL?"""
    if not fila:
        return True
    if fila.get("final"):
        return False
    edad = (datetime.now(timezone.utc).replace(tzinfo=None)
            - fila["actualizado"]).total_seconds()
    ttl = _TTL_HOY if fecha == hoy_mx() else _TTL_RECIENTE
    return edad > ttl


def _necesita_refresco(cuenta: str, fecha: date) -> bool:
    fila = db.fetch_one(
        """SELECT actualizado, final FROM ventas_sync
           WHERE canal='mercado_libre' AND cuenta=%s AND fecha=%s""",
        (cuenta, fecha))
    return _vencido(fila, fecha)


async def asegurar_dia(cuenta: str, fecha: date) -> None:
    """Garantiza que (cuenta, fecha) esté en caché y fresco según su TTL."""
    _asegurar_schema()
    if not settings.ventas_ml_refresh:
        return  # modo "puros pedidos": servir caché sin pedirle nada a ML
    if fecha > hoy_mx():
        return
    if not _necesita_refresco(cuenta, fecha):
        return
    lock = _locks.setdefault((cuenta, fecha.isoformat()), asyncio.Lock())
    async with lock:
        if not _necesita_refresco(cuenta, fecha):  # otro request ya lo hizo
            return
        buckets = await _agregar_dia(cuenta, fecha)
        if buckets is None:
            return
        final = fecha < (hoy_mx() - timedelta(days=_DIAS_FINALES))
        _guardar_dia(cuenta, fecha, buckets, final)


def _leer_rango(cuentas: list[str], desde: date, hasta: date) -> dict:
    """
    Lee del caché y agrega: 24 buckets por hora-del-día (sumando los días del
    rango) + totales + desglose por cuenta.
    """
    horas = [{"pedidos": 0, "unidades": 0, "monto": 0.0} for _ in range(24)]
    tot = {"pedidos": 0, "unidades": 0, "monto": 0.0,
           "canceladas": 0, "monto_cancelado": 0.0}
    por_cuenta = {c: {"pedidos": 0, "unidades": 0, "monto": 0.0} for c in cuentas}
    # monto por hora POR CUENTA: para la comparativa "a la misma hora" de las
    # tarjetas de cuenta cuando el rango es HOY.
    cuenta_horas = {c: [0.0] * 24 for c in cuentas}
    if not cuentas:
        return {"horas": horas, "totales": tot, "cuentas": por_cuenta,
                "cuenta_horas": cuenta_horas}
    marcas = ",".join(["%s"] * len(cuentas))
    filas = db.fetch_all(
        f"""SELECT cuenta, hora,
                   SUM(pedidos) p, SUM(unidades) u, SUM(monto) m,
                   SUM(canceladas) c, SUM(monto_cancelado) mc
            FROM ventas_horarias
            WHERE canal='mercado_libre' AND cuenta IN ({marcas})
              AND fecha BETWEEN %s AND %s
            GROUP BY cuenta, hora""",
        (*cuentas, desde, hasta))
    for f in filas:
        h = int(f["hora"])
        p, u, m = int(f["p"] or 0), int(f["u"] or 0), float(f["m"] or 0)
        horas[h]["pedidos"] += p
        horas[h]["unidades"] += u
        horas[h]["monto"] += m
        tot["pedidos"] += p
        tot["unidades"] += u
        tot["monto"] += m
        tot["canceladas"] += int(f["c"] or 0)
        tot["monto_cancelado"] += float(f["mc"] or 0)
        cta = por_cuenta.get(f["cuenta"])
        if cta is not None:
            cta["pedidos"] += p
            cta["unidades"] += u
            cta["monto"] += m
            cuenta_horas[f["cuenta"]][h] += m
    return {"horas": horas, "totales": tot, "cuentas": por_cuenta,
            "cuenta_horas": cuenta_horas}


def _delta_pct(actual: float, previo: float) -> float | None:
    """Variación % vs. semana pasada. None (= "s/ base") cuando no hay contra
    qué comparar — un "+100%" contra cero es ruido, no información."""
    if previo:
        return round((actual - previo) / previo * 100, 1)
    return None


def _pedidos_rango(cuentas: list[str], desde: date, hasta: date) -> dict | None:
    """
    PEDIDOS de WooCommerce creados por el flujo ML→WC (tabla pedidos_ml) dentro
    del rango, por cuenta. Es el "registro vivo" que el tab muestra junto a las
    ventas de ML. `creado` está en UTC; el rango llega en fechas CDMX (UTC-6).
    """
    try:
        ini = datetime.combine(desde, datetime.min.time()) + timedelta(hours=6)
        fin = datetime.combine(hasta, datetime.min.time()) + timedelta(hours=30)
        marcas = ",".join(["%s"] * len(cuentas))
        filas = db.fetch_all(
            f"""SELECT cuenta, estado_wc, COUNT(*) n, SUM(total) m, SUM(es_full) f
                FROM pedidos_ml
                WHERE cuenta IN ({marcas}) AND creado >= %s AND creado < %s
                GROUP BY cuenta, estado_wc""",
            (*cuentas, ini, fin))
    except Exception as exc:  # noqa: BLE001
        log.warning("Ventas: no se pudo leer pedidos_ml: %s", exc)
        return None
    tot = {"total": 0, "monto": 0.0, "full": 0, "propios": 0, "cancelados": 0}
    por_cuenta = {c: {"pedidos": 0, "monto": 0.0} for c in cuentas}
    for f in filas:
        n, m, nf = int(f["n"] or 0), float(f["m"] or 0), int(f["f"] or 0)
        tot["total"] += n
        tot["monto"] += m
        tot["full"] += nf
        tot["propios"] += n - nf
        if f.get("estado_wc") == "cancelled":
            tot["cancelados"] += n
        cta = por_cuenta.get(f["cuenta"])
        if cta is not None:
            cta["pedidos"] += n
            cta["monto"] += m
    tot["monto"] = round(tot["monto"], 2)
    return {**tot, "cuentas": {c: {"pedidos": v["pedidos"],
                                   "monto": round(v["monto"], 2)}
                               for c, v in por_cuenta.items()}}


def _pedidos_horario(cuentas: list[str], desde: date, hasta: date) -> dict:
    """
    Agregado por hora-del-día desde `pedidos_ml` (hora CDMX del `creado`, que
    va segundos después de la venta). Cuentan como VENTA los pedidos pagados
    (processing/completed/on-hold); `pending` aún no es dinero y `cancelled`
    va aparte con su monto.
    """
    horas = [{"pedidos": 0, "monto": 0.0} for _ in range(24)]
    tot = {"pedidos": 0, "monto": 0.0, "cancelados": 0, "monto_cancelado": 0.0}
    por_cuenta = {c: {"pedidos": 0, "monto": 0.0} for c in cuentas}
    cuenta_horas = {c: [0.0] * 24 for c in cuentas}
    ini = datetime.combine(desde, datetime.min.time()) + timedelta(hours=6)
    fin = datetime.combine(hasta, datetime.min.time()) + timedelta(hours=30)
    marcas = ",".join(["%s"] * len(cuentas))
    filas = db.fetch_all(
        f"""SELECT HOUR(DATE_SUB(creado, INTERVAL 6 HOUR)) h, cuenta, estado_wc,
                   COUNT(*) n, SUM(total) m
            FROM pedidos_ml
            WHERE cuenta IN ({marcas}) AND creado >= %s AND creado < %s
            GROUP BY h, cuenta, estado_wc""",
        (*cuentas, ini, fin))
    for f in filas:
        h, n, m = int(f["h"]), int(f["n"] or 0), float(f["m"] or 0)
        est = str(f.get("estado_wc") or "")
        if est == "cancelled":
            tot["cancelados"] += n
            tot["monto_cancelado"] += m
            continue
        if est in ("pending", "on-hold"):
            continue  # sin pago confirmado todavía: no es venta (ML y Amazon)
        horas[h]["pedidos"] += n
        horas[h]["monto"] += m
        tot["pedidos"] += n
        tot["monto"] += m
        cta = por_cuenta.get(f["cuenta"])
        if cta is not None:
            cta["pedidos"] += n
            cta["monto"] += m
            cuenta_horas[f["cuenta"]][h] += m
    return {"horas": horas, "totales": tot, "cuentas": por_cuenta,
            "cuenta_horas": cuenta_horas}


# Cuentas que viven en pedidos_ml: las 2 de ML + Amazon (sondeo cada 5 min).
_CUENTAS_PEDIDOS = ("BEKURA", "SANCORFASHION", "AMAZON")


async def resumen_pedidos(cuenta: str | None, desde: date, hasta: date) -> dict:
    """
    El tab VENTAS alimentado 100% por los PEDIDOS de WooCommerce (pedidos_ml):
    la operación vive de pedidos y webhooks (Brandon, 2026-07-17). General =
    todos los pedidos (ML + Amazon); el canal/cuenta filtra. Cero llamadas a
    APIs: una consulta a nuestra tabla. La comparativa semanal se llena sola
    cuando el registro cumpla 7 días (antes: "s/ base").
    """
    cuentas = [cuenta] if cuenta else list(_CUENTAS_PEDIDOS)
    p_desde, p_hasta = desde - timedelta(days=7), hasta - timedelta(days=7)
    act = await asyncio.to_thread(_pedidos_horario, cuentas, desde, hasta)
    prev = await asyncio.to_thread(_pedidos_horario, cuentas, p_desde, p_hasta)

    horas = []
    for h in range(24):
        a, p = act["horas"][h], prev["horas"][h]
        horas.append({
            "hora": h,
            "monto": round(a["monto"], 2), "pedidos": a["pedidos"], "unidades": 0,
            "prev_monto": round(p["monto"], 2), "prev_pedidos": p["pedidos"],
            "prev_unidades": 0,
            "delta_monto": _delta_pct(a["monto"], p["monto"]),
        })

    ta, tp = act["totales"], prev["totales"]
    ticket_a = ta["monto"] / ta["pedidos"] if ta["pedidos"] else 0
    ticket_p = tp["monto"] / tp["pedidos"] if tp["pedidos"] else 0

    parcial = None
    if desde == hasta == hoy_mx():
        corte = datetime.now(_TZ_MX).hour
        pm = sum(h["monto"] for h in prev["horas"][:corte + 1])
        pp = sum(h["pedidos"] for h in prev["horas"][:corte + 1])
        parcial = {
            "hora_corte": corte,
            "prev_monto": round(pm, 2), "prev_pedidos": pp, "prev_unidades": 0,
            "delta": {"monto": _delta_pct(ta["monto"], pm),
                      "pedidos": _delta_pct(ta["pedidos"], pp), "unidades": None},
        }

    etiquetas = {"BEKURA": "Kubera", "SANCORFASHION": "San Corpe",
                 "AMAZON": "Amazon"}
    cuentas_out = []
    for c in cuentas:
        ca, cp = act["cuentas"][c], prev["cuentas"][c]
        fila = {
            "cuenta": c, "label": etiquetas.get(c, c),
            "monto": round(ca["monto"], 2), "pedidos": ca["pedidos"], "unidades": 0,
            "prev_monto": round(cp["monto"], 2),
            "delta_monto": _delta_pct(ca["monto"], cp["monto"]),
        }
        if parcial:
            pmc = sum(prev["cuenta_horas"][c][:parcial["hora_corte"] + 1])
            fila["prev_monto_parcial"] = round(pmc, 2)
            fila["delta_parcial"] = _delta_pct(ca["monto"], pmc)
        cuentas_out.append(fila)

    return {
        "fuente": "pedidos",
        "canal": "mercado_libre" if cuenta else "general",
        "cuenta": cuenta,
        "desde": desde.isoformat(), "hasta": hasta.isoformat(),
        "prev_desde": p_desde.isoformat(), "prev_hasta": p_hasta.isoformat(),
        "horas": horas,
        "totales": {
            "monto": round(ta["monto"], 2), "pedidos": ta["pedidos"],
            "unidades": 0, "ticket": round(ticket_a, 2),
            "canceladas": ta["cancelados"],
            "monto_cancelado": round(ta["monto_cancelado"], 2),
            "prev": {"monto": round(tp["monto"], 2), "pedidos": tp["pedidos"],
                     "unidades": 0, "ticket": round(ticket_p, 2),
                     "canceladas": tp["cancelados"]},
            "delta": {"monto": _delta_pct(ta["monto"], tp["monto"]),
                      "pedidos": _delta_pct(ta["pedidos"], tp["pedidos"]),
                      "unidades": None,
                      "ticket": _delta_pct(ticket_a, ticket_p)},
            "parcial": parcial,
        },
        "cuentas": cuentas_out,
        "pedidos_wc": _pedidos_rango(cuentas, desde, hasta),
        "actualizado": datetime.now(_TZ_MX).isoformat(timespec="seconds"),
    }


async def resumen(cuenta: str | None, desde: date, hasta: date) -> dict:
    """
    Respuesta completa para el tab Ventas: buckets por hora del rango pedido y
    del MISMO rango 7 días atrás, con deltas %.

    `cuenta=None` → General: suma BEKURA + SANCORFASHION.
    """
    cuentas = [cuenta] if cuenta else list(_CUENTAS_ML)
    p_desde, p_hasta = desde - timedelta(days=7), hasta - timedelta(days=7)

    fechas: list[date] = []
    d = desde
    while d <= hasta:
        fechas.append(d)
        d += timedelta(days=1)
    d = p_desde
    while d <= p_hasta:
        fechas.append(d)
        d += timedelta(days=1)

    # UNA consulta de frescura para todo el rango (28 consultas sueltas contra
    # Hostinger costaban ~8 s por carga; así la vista cacheada baja a ~1 s).
    _asegurar_schema()
    hoy = hoy_mx()
    pendientes: list[tuple[str, date]] = []
    try:
        marcas_c = ",".join(["%s"] * len(cuentas))
        marcas_f = ",".join(["%s"] * len(fechas))
        filas = db.fetch_all(
            f"""SELECT cuenta, fecha, actualizado, final FROM ventas_sync
                WHERE canal='mercado_libre' AND cuenta IN ({marcas_c})
                  AND fecha IN ({marcas_f})""",
            (*cuentas, *fechas))
        estado = {(f["cuenta"], f["fecha"]): f for f in filas}
        pendientes = [(c, f) for c in cuentas for f in fechas
                      if f <= hoy and _vencido(estado.get((c, f)), f)]
    except Exception as exc:  # noqa: BLE001
        log.warning("Ventas: chequeo de frescura falló (%s); refresco completo", exc)
        pendientes = [(c, f) for c in cuentas for f in fechas if f <= hoy]

    # Días pendientes en paralelo con tope (ML rate-limita; 5 va sobrado).
    if pendientes:
        sem = asyncio.Semaphore(5)

        async def _uno(c: str, f: date) -> None:
            async with sem:
                await asegurar_dia(c, f)

        await asyncio.gather(*(_uno(c, f) for c, f in pendientes))

    act = _leer_rango(cuentas, desde, hasta)
    prev = _leer_rango(cuentas, p_desde, p_hasta)
    pedidos_wc = await asyncio.to_thread(_pedidos_rango, cuentas, desde, hasta)

    horas = []
    for h in range(24):
        a, p = act["horas"][h], prev["horas"][h]
        horas.append({
            "hora": h,
            "monto": round(a["monto"], 2), "pedidos": a["pedidos"],
            "unidades": a["unidades"],
            "prev_monto": round(p["monto"], 2), "prev_pedidos": p["pedidos"],
            "prev_unidades": p["unidades"],
            "delta_monto": _delta_pct(a["monto"], p["monto"]),
        })

    ta, tp = act["totales"], prev["totales"]
    ticket_a = ta["monto"] / ta["pedidos"] if ta["pedidos"] else 0
    ticket_p = tp["monto"] / tp["pedidos"] if tp["pedidos"] else 0

    # HOY va incompleto: comparar sus totales contra el día COMPLETO de la
    # semana pasada da un -60% engañoso. Cuando el rango es "solo hoy",
    # agregamos la comparativa honesta: la semana pasada A LA MISMA HORA.
    parcial = None
    if desde == hasta == hoy_mx():
        corte = datetime.now(_TZ_MX).hour
        pm = sum(h["monto"] for h in prev["horas"][:corte + 1])
        pp = sum(h["pedidos"] for h in prev["horas"][:corte + 1])
        pu = sum(h["unidades"] for h in prev["horas"][:corte + 1])
        parcial = {
            "hora_corte": corte,
            "prev_monto": round(pm, 2), "prev_pedidos": pp, "prev_unidades": pu,
            "delta": {"monto": _delta_pct(ta["monto"], pm),
                      "pedidos": _delta_pct(ta["pedidos"], pp),
                      "unidades": _delta_pct(ta["unidades"], pu)},
        }
    etiquetas = {"BEKURA": "Kubera", "SANCORFASHION": "San Corpe"}
    cuentas_out = []
    for c in cuentas:
        ca, cp = act["cuentas"][c], prev["cuentas"][c]
        fila = {
            "cuenta": c, "label": etiquetas.get(c, c),
            "monto": round(ca["monto"], 2), "pedidos": ca["pedidos"],
            "unidades": ca["unidades"],
            "prev_monto": round(cp["monto"], 2),
            "delta_monto": _delta_pct(ca["monto"], cp["monto"]),
        }
        if parcial:  # rango = HOY → delta honesto por cuenta, a la misma hora
            pmc = sum(prev["cuenta_horas"][c][:parcial["hora_corte"] + 1])
            fila["prev_monto_parcial"] = round(pmc, 2)
            fila["delta_parcial"] = _delta_pct(ca["monto"], pmc)
        cuentas_out.append(fila)

    return {
        "canal": "mercado_libre" if cuenta else "general",
        "cuenta": cuenta,
        "desde": desde.isoformat(), "hasta": hasta.isoformat(),
        "prev_desde": p_desde.isoformat(), "prev_hasta": p_hasta.isoformat(),
        "horas": horas,
        "totales": {
            "monto": round(ta["monto"], 2), "pedidos": ta["pedidos"],
            "unidades": ta["unidades"], "ticket": round(ticket_a, 2),
            "canceladas": ta["canceladas"],
            "monto_cancelado": round(ta["monto_cancelado"], 2),
            "prev": {"monto": round(tp["monto"], 2), "pedidos": tp["pedidos"],
                     "unidades": tp["unidades"], "ticket": round(ticket_p, 2),
                     "canceladas": tp["canceladas"]},
            "delta": {"monto": _delta_pct(ta["monto"], tp["monto"]),
                      "pedidos": _delta_pct(ta["pedidos"], tp["pedidos"]),
                      "unidades": _delta_pct(ta["unidades"], tp["unidades"]),
                      "ticket": _delta_pct(ticket_a, ticket_p)},
            "parcial": parcial,
        },
        "cuentas": cuentas_out,
        "pedidos_wc": pedidos_wc,
        "actualizado": datetime.now(_TZ_MX).isoformat(timespec="seconds"),
    }
