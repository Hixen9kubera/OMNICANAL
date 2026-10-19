"""
pricing_precio.py — Cambiar a mano el precio de UNA publicación de Mercado Libre
desde PRICING, con descuento visible (precio normal tachado y % OFF), y deshacerlo
(v0.665.0, pedido de Brandon 19-oct-2026).

QUÉ HACE, EN ORDEN
    1. Lee de ML cómo está la publicación: precio normal (`item.price`), lo que
       cobra (`sale_price`) y sus promociones de precio.
    2. Si cambia el precio normal: `PUT /items/{id}` con `price`.
    3. Si ya había un DESCUENTO INDIVIDUAL (PRICE_DISCOUNT), lo quita: ML no deja
       dos a la vez.
    4. Si se pidió descuento: `POST /seller-promotions/items/{id}` PRICE_DISCOUNT
       con `deal_price` = precio normal × (1 − %). Empieza en 1 minuto (ML no acepta
       un inicio en el pasado) y se espera a que arranque.
    5. Si se pidió, saca la publicación de las campañas propias (SELLER_CAMPAIGN)
       y de ML (DEAL) que cobran menos que el precio nuevo: el comprador paga
       SIEMPRE la promoción más baja, así que mientras sigan ahí el precio nuevo no
       se ve. Se hace DESPUÉS de que el descuento arrancó, para que el comprador
       nunca vea el precio normal sin descuento de por medio.
    6. Vuelve a leer y devuelve lo que ML cobra ahora.

LA TRAMPA DEL PRECIO DE REFERENCIA (medido el 19-oct con TEC-0017-MET)
    ML NO deja subir el precio y poner el descuento al momento: tras subir de
    $1,199 a $4,083, el descuento individual solo se aceptaba entre $816.60 y
    $1,163.03 (0.97 × el precio VIEJO). Por eso, antes de publicar el descuento se
    relee el rango que ML acepta y, si el precio con descuento queda fuera, se
    DESHACE lo ya hecho (precio normal y descuento anterior) y se dice el rango.

DESHACER
    Cada cambio se anota en `ops.process_log` (proceso `precio`, origen `pricing`)
    con la foto de ANTES. «Deshacer» la relee y devuelve la publicación a como
    estaba: precio normal, su descuento individual (mismo precio y misma fecha de
    fin) y las campañas de las que se sacó, con su precio.

QUÉ NO TOCA
    Relámpago, oferta del día, co-fondeadas (SMART), precio ganador y stock lento
    en Full: ML no deja manejarlas igual por API. Si cobran menos que el precio
    nuevo, se avisa y siguen ganando. Cupones y «lleva 2» se aplican encima.

Solo admin (ver `routers/pricing.py`). El token es el del backend (no se renueva
aquí). Todo lo de red va con httpx asíncrono; la bitácora, en un hilo (regla 11).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

log = logging.getLogger("omnicanal.pricing.precio")

CUENTAS = ("BEKURA", "SANCORFASHION")
ITEM_ML = re.compile(r"^MLM\d{6,14}$")
CDMX = timezone(timedelta(hours=-6))
PROCESO, ORIGEN = "precio", "pricing"
DIAS_DESCUENTO = 28          # vigencia por omisión del descuento individual
MAX_DESCUENTO = 80           # % — más que esto casi seguro es un dedazo
ESPERA_ARRANQUE_S = 150      # cuánto se espera a que el descuento quede «started»
ESPERA_CANDIDATA_S = 240     # cuánto se espera a que ML VUELVA a ofrecer el descuento tras quitar uno (medido: 1-2 min)
TIPOS_PRECIO = {"PRICE_DISCOUNT", "SELLER_CAMPAIGN", "DEAL", "SMART", "LIGHTNING", "DOD", "PRICE_MATCHING",
                "UNHEALTHY_STOCK", "SMART_LIGHTNING"}
SE_PUEDEN_QUITAR = {"SELLER_CAMPAIGN", "DEAL"}
NOMBRE = {"PRICE_DISCOUNT": "Descuento individual", "SELLER_CAMPAIGN": "Campaña propia", "DEAL": "Campaña de ML",
          "SMART": "Co-fondeada con ML", "LIGHTNING": "Oferta relámpago", "DOD": "Oferta del día",
          "PRICE_MATCHING": "Precio ganador", "UNHEALTHY_STOCK": "Acelera tus ventas de Full",
          "SMART_LIGHTNING": "Relámpago co-fondeada"}
_candados: dict[str, asyncio.Lock] = {}


class ErrorPrecio(Exception):
    """Algo que la persona tiene que leer: qué pasó y qué hacer."""


# ── PURO ─────────────────────────────────────────────────────────────────────

def _f(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def resumir_promos(promos: list[dict]) -> list[dict]:
    """PURA. Las promociones de PRECIO de la publicación, en la forma que usa la pantalla."""
    salida = []
    for x in promos or []:
        tipo = x.get("type")
        if tipo not in TIPOS_PRECIO:
            continue
        salida.append({
            "tipo": tipo, "nombre_tipo": NOMBRE.get(tipo, tipo), "id": x.get("id"), "nombre": (x.get("name") or "").strip(),
            "estado": x.get("status"), "precio": _f(x.get("price")),
            "inicio": x.get("start_date"), "fin": x.get("finish_date"),
            "min": _f(x.get("min_discounted_price")), "max": _f(x.get("max_discounted_price")),
        })
    return salida


def planear(estado: dict, precio: float, pct: float | None, salir: bool) -> dict:
    """
    PURA. Qué va a pasar con (precio normal, % de descuento) sobre el estado leído de ML.
    Lanza ErrorPrecio si la entrada no tiene sentido.
    """
    precio = _f(precio)
    if not precio:
        raise ErrorPrecio("Escribe un precio normal mayor a cero.")
    pct = float(pct or 0)
    if pct < 0 or pct > MAX_DESCUENTO:
        raise ErrorPrecio(f"El descuento va de 0 a {MAX_DESCUENTO} %.")
    final = round(precio * (1 - pct / 100), 2) if pct else precio
    vivas = [p for p in estado["promos"] if p["estado"] in ("started", "pending") and p["tipo"] != "PRICE_DISCOUNT"]
    abajo = [p for p in vivas if p["precio"] and p["precio"] < final - 0.5]
    quitar = [p for p in abajo if salir and p["tipo"] in SE_PUEDEN_QUITAR and p["id"]]
    quedan = [p for p in abajo if p not in quitar]
    cobrara = min([final] + [p["precio"] for p in quedan if p["estado"] == "started"])
    pd = next((p for p in estado["promos"] if p["tipo"] == "PRICE_DISCOUNT" and p["estado"] == "candidate"), None)
    avisos = []
    if pct and pd and pd["min"] and pd["max"] and abs(precio - (estado["precio"] or 0)) < 0.5 and not (pd["min"] <= final <= pd["max"]):
        avisos.append(f"Hoy ML acepta el descuento entre ${pd['min']:,.2f} y ${pd['max']:,.2f}; ${final:,.2f} queda fuera.")
    if pct and estado["precio"] and precio > estado["precio"] + 0.5:
        avisos.append("Subir el precio normal y descontar al momento casi nunca pasa: ML mide el descuento contra el "
                      "precio anterior. Si lo rechaza, se regresa todo a como estaba.")
    for p in quedan:
        motivo = "no se puede quitar por API" if p["tipo"] not in SE_PUEDEN_QUITAR else "marcaste no salir de campañas"
        avisos.append(f"{p['nombre_tipo']} «{p['nombre'] or p['id'] or ''}» cobra ${p['precio']:,.2f} y seguirá ganando ({motivo}).")
    return {"precio": precio, "pct": pct, "final": final, "cobrara": cobrara, "quitar": quitar, "quedan": quedan, "avisos": avisos,
            "cambia_normal": abs(precio - (estado["precio"] or 0)) >= 0.5}


# ── ML ───────────────────────────────────────────────────────────────────────

async def _cliente(cuenta: str) -> httpx.AsyncClient:
    from services import meli

    tk = await meli.access_token_async(cuenta)
    if not tk:
        raise ErrorPrecio(f"No hay token de Mercado Libre para {cuenta}.")
    return httpx.AsyncClient(base_url="https://api.mercadolibre.com", timeout=30,
                             headers={"Authorization": f"Bearer {tk}"})


async def leer_estado(cli: httpx.AsyncClient, item_id: str) -> dict:
    it, sp, pr = await asyncio.gather(
        cli.get(f"/items/{item_id}", params={"attributes": "id,title,price,status,available_quantity"}),
        cli.get(f"/items/{item_id}/sale_price", params={"context": "channel_marketplace"}),
        cli.get(f"/seller-promotions/items/{item_id}", params={"app_version": "v2"}))
    if it.status_code != 200:
        raise ErrorPrecio(f"Mercado Libre no devolvió la publicación ({it.status_code}).")
    i = it.json()
    s = sp.json() if sp.status_code == 200 else {}
    promos = pr.json() if pr.status_code == 200 and isinstance(pr.json(), list) else []
    return {"item": item_id, "titulo": i.get("title"), "precio": _f(i.get("price")), "status": i.get("status"),
            "stock": i.get("available_quantity"), "cobra": _f(s.get("amount")), "tachado": _f(s.get("regular_amount")),
            "por": s.get("metadata") or {}, "promos": resumir_promos(promos)}


def _ahora_ml(minutos: int = 1) -> str:
    return (datetime.now(CDMX) + timedelta(minutes=minutos)).strftime("%Y-%m-%dT%H:%M:%S")


def _fin_ml(hasta: str | None) -> str:
    d = date.fromisoformat(hasta) if hasta else (datetime.now(CDMX) + timedelta(days=DIAS_DESCUENTO)).date()
    return f"{d.isoformat()}T23:59:59"


async def _put_precio(cli, item_id: str, precio: float) -> httpx.Response:
    return await cli.put(f"/items/{item_id}", json={"price": precio})


async def _quitar(cli, item_id: str, tipo: str, promo_id: str | None = None) -> httpx.Response:
    params = {"promotion_type": tipo, "app_version": "v2"}
    if promo_id:
        params["promotion_id"] = promo_id
    return await cli.delete(f"/seller-promotions/items/{item_id}", params=params)


REINTENTO_DESCUENTO_S = 240  # cuánto se reintenta poner un descuento que ML todavía no acepta
_NO_CANDIDATA = "no candidates found"


async def _poner_descuento(cli, item_id: str, precio: float, fin: str, pasos: list | None = None) -> httpx.Response:
    """
    POST del descuento individual, con reintentos. Medido el 19-oct: después de QUITAR un descuento o de cambiar el
    precio normal, ML contesta 400 «No candidates found for item» durante 1 a 4 minutos, aunque su lista de promociones
    ya diga «candidate». Se reintenta cada 20 s; cualquier otro error se devuelve al momento.
    """
    limite = time.monotonic() + REINTENTO_DESCUENTO_S
    avisado = False
    while True:
        cuerpo = {"promotion_type": "PRICE_DISCOUNT", "deal_price": round(precio, 2), "start_date": _ahora_ml(), "finish_date": fin}
        r = await cli.post(f"/seller-promotions/items/{item_id}", params={"app_version": "v2"}, json=cuerpo)
        if r.status_code in (200, 201) or _NO_CANDIDATA not in r.text.lower() or time.monotonic() >= limite:
            return r
        if pasos is not None and not avisado:
            pasos.append({"paso": "ML todavía no acepta el descuento: se reintenta cada 20 s (hasta 4 min)"})
            avisado = True
        await asyncio.sleep(20)


async def _volver_a_campana(cli, item_id: str, p: dict) -> httpx.Response:
    cuerpo = {"promotion_id": p["id"], "promotion_type": p["tipo"], "deal_price": p["precio"]}
    return await cli.post(f"/seller-promotions/items/{item_id}", params={"app_version": "v2"}, json=cuerpo)


async def _esperar_descuento(cli, item_id: str) -> dict | None:
    """Hasta que el descuento individual quede «started» (o se acabe la espera)."""
    limite = time.monotonic() + ESPERA_ARRANQUE_S
    while time.monotonic() < limite:
        await asyncio.sleep(6)
        estado = await leer_estado(cli, item_id)
        pd = next((p for p in estado["promos"] if p["tipo"] == "PRICE_DISCOUNT" and p["estado"] == "started"), None)
        if pd:
            return pd
    return None


async def _esperar_candidata(cli, item_id: str, pasos: list | None = None) -> dict | None:
    """
    El descuento individual que ML ofrece HOY, con su rango. Tras QUITAR uno, ML tarda en volver a ofrecerlo:
    medido el 19-oct, 1 a 2 minutos en los que cualquier POST contesta 400 «No candidates found for item».
    """
    limite = time.monotonic() + ESPERA_CANDIDATA_S
    avisado = False
    while True:
        estado = await leer_estado(cli, item_id)
        cand = next((p for p in estado["promos"] if p["tipo"] == "PRICE_DISCOUNT" and p["estado"] == "candidate"), None)
        if cand or time.monotonic() >= limite:
            return cand
        if pasos is not None and not avisado:
            pasos.append({"paso": "esperando a que ML vuelva a ofrecer el descuento (1 a 2 min)"})
            avisado = True
        await asyncio.sleep(8)


def _txt(r: httpx.Response) -> str:
    try:
        j = r.json()
        return str(j.get("message") or j.get("error") or j)[:300]
    except Exception:  # noqa: BLE001
        return r.text[:300]


# ── BITÁCORA ─────────────────────────────────────────────────────────────────

def _anotar(sku: str | None, accion: str, estado: str, detalle: dict, quien: str) -> None:
    """BLOQUEANTE (kubera). Se espera a propósito: «Deshacer» lee de aquí la foto de antes."""
    from services import supabase_db as sdb

    try:
        sdb.execute(
            "INSERT INTO ops.process_log (proceso, origen, sku, accion, estado, detalle, detail_ref, actor) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
            (PROCESO, ORIGEN, sku, accion, estado, json.dumps(detalle, ensure_ascii=False, default=str),
             f"pricing:{detalle.get('item')}", quien or None))
    except Exception as exc:  # noqa: BLE001 — la bitácora nunca tumba el cambio ya hecho en ML
        log.warning("pricing precio: no se pudo anotar %s de %s: %s", accion, detalle.get("item"), exc)


def _ultimo_cambio(item_id: str) -> dict | None:
    """BLOQUEANTE (kubera, SELECT). El último cambio aplicado a la publicación desde PRICING."""
    from services import supabase_db as sdb

    fila = sdb.fetch_one(
        "select id, detalle, created_at, actor from ops.process_log "
        "where proceso = %s and origen = %s and accion = 'aplicar' and estado = 'ok' and detail_ref = %s "
        "order by created_at desc limit 1",
        (PROCESO, ORIGEN, f"pricing:{item_id}"))
    return dict(fila) if fila else None


# ── API DEL SERVICIO ─────────────────────────────────────────────────────────

def _validar(item_id: str, cuenta: str) -> str:
    if not ITEM_ML.match(item_id or ""):
        raise ErrorPrecio("Publicación no válida.")
    cuenta = (cuenta or "").strip().upper()
    if cuenta not in CUENTAS:
        raise ErrorPrecio("Cuenta no válida.")
    return cuenta


async def vista_previa(item_id: str, cuenta: str, precio: float, pct: float | None, salir: bool = True) -> dict:
    """Solo GET. Cómo está en ML y qué pasaría; no escribe nada."""
    cuenta = _validar(item_id, cuenta)
    async with await _cliente(cuenta) as cli:
        estado = await leer_estado(cli, item_id)
    plan = planear(estado, precio, pct, salir)
    try:
        u = await asyncio.to_thread(_ultimo_cambio, item_id)
    except Exception as exc:  # noqa: BLE001 — sin bitácora la vista previa sirve igual
        log.warning("pricing precio: no se pudo leer el último cambio de %s: %s", item_id, exc)
        u = None
    ultimo = {"id": u["id"], "cuando": str(u.get("created_at") or ""), "quien": u.get("actor")} if u else None
    return {"estado": estado, "plan": plan, "ultimo": ultimo}


def validar_pedido(precio: float, pct: float | None) -> None:
    """PURA. Lo que se puede revisar sin ir a ML."""
    if not _f(precio):
        raise ErrorPrecio("Escribe un precio normal mayor a cero.")
    if pct is not None and not (0 <= float(pct) <= MAX_DESCUENTO):
        raise ErrorPrecio(f"El descuento va de 0 a {MAX_DESCUENTO} %.")


def _fin_vigente(pd: dict) -> str:
    """La fecha de fin de un descuento que se va a volver a poner; si ya pasó, la de omisión."""
    fin = (pd.get("fin") or "")[:19]
    if not fin or fin < datetime.now(CDMX).strftime("%Y-%m-%dT%H:%M:%S"):
        fin = _fin_ml(None)
    return fin


async def aplicar(item_id: str, cuenta: str, sku: str | None, precio: float, pct: float | None, salir: bool = True,
                  hasta: str | None = None, quien: str = "", pasos: list | None = None) -> dict:
    cuenta = _validar(item_id, cuenta)
    pasos = pasos if pasos is not None else []
    candado = _candados.setdefault(item_id, asyncio.Lock())
    if candado.locked():
        raise ErrorPrecio("Ya se está cambiando el precio de esta publicación. Espera a que termine.")
    async with candado, await _cliente(cuenta) as cli:
        antes = await leer_estado(cli, item_id)
        plan = planear(antes, precio, pct, salir)
        pd_antes = next((p for p in antes["promos"] if p["tipo"] == "PRICE_DISCOUNT" and p["estado"] in ("started", "pending")), None)
        detalle = {"item": item_id, "cuenta": cuenta, "pedido": {"precio": plan["precio"], "pct": plan["pct"], "salir": salir, "hasta": hasta},
                   "antes": antes, "pasos": pasos, "quitadas": []}

        async def regresar(motivo: str) -> None:
            """Deja la publicación como estaba ANTES de este intento y dice si de verdad quedó igual."""
            quedo_bien = True
            if plan["cambia_normal"]:
                r = await _put_precio(cli, item_id, antes["precio"])
                pasos.append({"paso": "regresar precio normal", "http": r.status_code})
                quedo_bien &= r.status_code == 200
            if pd_antes:
                actual = await leer_estado(cli, item_id)
                if any(p["tipo"] == "PRICE_DISCOUNT" and p["estado"] in ("started", "pending") for p in actual["promos"]):
                    await _quitar(cli, item_id, "PRICE_DISCOUNT")
                await _esperar_candidata(cli, item_id, pasos)
                r = await _poner_descuento(cli, item_id, pd_antes["precio"], _fin_vigente(pd_antes), pasos)
                pasos.append({"paso": "regresar descuento anterior", "http": r.status_code, "ml": _txt(r) if r.status_code >= 300 else None})
                quedo_bien &= r.status_code in (200, 201)
            detalle["error"] = motivo
            await asyncio.to_thread(_anotar, sku, "aplicar", "error", detalle, quien)
            if quedo_bien:
                raise ErrorPrecio(motivo + " Se regresó la publicación a como estaba.")
            raise ErrorPrecio(motivo + f" OJO: no se pudo regresar todo; antes tenía precio normal ${antes['precio']:,.2f}"
                              + (f" y descuento a ${pd_antes['precio']:,.2f}" if pd_antes else "") + ". Revísala en Mercado Libre.")

        # 1. precio normal
        if plan["cambia_normal"]:
            r = await _put_precio(cli, item_id, plan["precio"])
            pasos.append({"paso": "precio normal", "http": r.status_code})
            if r.status_code != 200:
                detalle["error"] = f"ML no aceptó el precio normal: {_txt(r)}"
                await asyncio.to_thread(_anotar, sku, "aplicar", "error", detalle, quien)
                raise ErrorPrecio(f"Mercado Libre no aceptó el precio normal ({r.status_code}): {_txt(r)}. No se cambió nada.")

        # 2. el descuento individual anterior se quita (ML no deja dos)
        if pd_antes:
            r = await _quitar(cli, item_id, "PRICE_DISCOUNT")
            pasos.append({"paso": "quitar descuento anterior", "http": r.status_code})
            if r.status_code not in (200, 204):
                await regresar(f"No se pudo quitar el descuento anterior: {_txt(r)}.")

        # 3. el descuento nuevo, dentro del rango que ML acepta HOY (tras quitar uno, ML tarda en volver a ofrecerlo)
        pd_nuevo = None
        if plan["pct"]:
            cand = await _esperar_candidata(cli, item_id, pasos)
            if not cand:
                await regresar("Mercado Libre no ofrece descuento individual para esta publicación ahora.")
            if cand["min"] and cand["max"] and not (cand["min"] - 0.01 <= plan["final"] <= cand["max"] + 0.01):
                await regresar(f"Mercado Libre solo acepta el precio con descuento entre ${cand['min']:,.2f} y ${cand['max']:,.2f}; "
                               f"${plan['final']:,.2f} queda fuera.")
            r = await _poner_descuento(cli, item_id, plan["final"], _fin_ml(hasta), pasos)
            pasos.append({"paso": "descuento nuevo", "http": r.status_code, "ml": _txt(r) if r.status_code >= 300 else None})
            if r.status_code not in (200, 201):
                await regresar(f"Mercado Libre no aceptó el descuento: {_txt(r)}.")
            pasos.append({"paso": "esperando a que arranque el descuento (1 min)"})
            pd_nuevo = await _esperar_descuento(cli, item_id)
            pasos.append({"paso": "descuento arrancó", "ok": bool(pd_nuevo)})

        # 4. salir de lo que cobra menos (después de que el descuento arrancó)
        avisos = list(plan["avisos"])
        if plan["quitar"] and plan["pct"] and not pd_nuevo:
            avisos.append("El descuento no arrancó a tiempo: no se sacó la publicación de las campañas que cobran menos.")
        else:
            for p in plan["quitar"]:
                r = await _quitar(cli, item_id, p["tipo"], p["id"])
                ok = r.status_code in (200, 204)
                pasos.append({"paso": f"salir de {p['nombre_tipo']} «{p['nombre'] or p['id']}»", "http": r.status_code})
                if ok:
                    detalle["quitadas"].append(p)
                else:
                    avisos.append(f"No se pudo salir de «{p['nombre'] or p['id']}»: {_txt(r)}.")

        await asyncio.sleep(3)
        despues = await leer_estado(cli, item_id)
        detalle.update(despues=despues, avisos=avisos)
        await asyncio.to_thread(_anotar, sku, "aplicar", "ok", detalle, quien)
        return {"antes": antes, "despues": despues, "pasos": pasos, "avisos": avisos, "quitadas": detalle["quitadas"]}


async def deshacer(item_id: str, cuenta: str, sku: str | None, quien: str = "", pasos: list | None = None) -> dict:
    """Devuelve la publicación a la foto de ANTES del último cambio hecho desde PRICING."""
    cuenta = _validar(item_id, cuenta)
    pasos = pasos if pasos is not None else []
    ultimo = await asyncio.to_thread(_ultimo_cambio, item_id)
    if not ultimo:
        raise ErrorPrecio("No hay un cambio de precio hecho desde PRICING para deshacer en esta publicación.")
    d = ultimo["detalle"] if isinstance(ultimo["detalle"], dict) else json.loads(ultimo["detalle"])
    antes = d["antes"]
    candado = _candados.setdefault(item_id, asyncio.Lock())
    if candado.locked():
        raise ErrorPrecio("Ya se está cambiando el precio de esta publicación. Espera a que termine.")
    async with candado, await _cliente(cuenta) as cli:
        ahora = await leer_estado(cli, item_id)
        avisos: list[str] = []
        pd_antes = next((p for p in antes["promos"] if p["tipo"] == "PRICE_DISCOUNT" and p["estado"] in ("started", "pending")), None)
        pd_ahora = next((p for p in ahora["promos"] if p["tipo"] == "PRICE_DISCOUNT" and p["estado"] in ("started", "pending")), None)
        quitado = False
        if pd_ahora:
            r = await _quitar(cli, item_id, "PRICE_DISCOUNT")
            pasos.append({"paso": "quitar descuento puesto", "http": r.status_code})
            quitado = r.status_code in (200, 204)
        if antes["precio"] and abs((ahora["precio"] or 0) - antes["precio"]) >= 0.5:
            r = await _put_precio(cli, item_id, antes["precio"])
            pasos.append({"paso": "precio normal de antes", "http": r.status_code})
            if r.status_code != 200:
                avisos.append(f"ML no aceptó regresar el precio normal: {_txt(r)}.")
        if pd_antes:
            if quitado:
                await _esperar_candidata(cli, item_id, pasos)
            r = await _poner_descuento(cli, item_id, pd_antes["precio"], _fin_vigente(pd_antes), pasos)
            pasos.append({"paso": "descuento de antes", "http": r.status_code, "ml": _txt(r) if r.status_code >= 300 else None})
            if r.status_code not in (200, 201) and quitado and pd_ahora:
                # ML solo deja BAJAR el precio con descuento (medido el 19-oct): regresar a uno más alto puede no
                # pasar. Antes que dejarla sin descuento, se vuelve a poner el del cambio.
                await _esperar_candidata(cli, item_id, pasos)
                r2 = await _poner_descuento(cli, item_id, pd_ahora["precio"], _fin_vigente(pd_ahora), pasos)
                pasos.append({"paso": "se vuelve a poner el descuento del cambio", "http": r2.status_code,
                              "ml": _txt(r2) if r2.status_code >= 300 else None})
                if r2.status_code in (200, 201):
                    avisos.append(f"ML no dejó regresar el descuento a ${pd_antes['precio']:,.2f} (solo deja bajarlo): "
                                  f"se quedó en ${pd_ahora['precio']:,.2f}, como con el cambio.")
                    await _esperar_descuento(cli, item_id)
                else:
                    avisos.append(f"ML no aceptó ningún descuento ({_txt(r)}): la publicación quedó SIN descuento. Ponlo en Mercado Libre.")
            elif r.status_code not in (200, 201):
                avisos.append(f"ML no aceptó el descuento de antes (${pd_antes['precio']:,.2f}): {_txt(r)}. Ponlo en Mercado Libre.")
            else:
                pasos.append({"paso": "esperando a que arranque el descuento (1 min)"})
                await _esperar_descuento(cli, item_id)
        for p in d.get("quitadas") or []:
            r = await _volver_a_campana(cli, item_id, p)
            pasos.append({"paso": f"volver a {p['nombre_tipo']} «{p['nombre'] or p['id']}»", "http": r.status_code,
                          "ml": _txt(r) if r.status_code >= 300 else None})
            if r.status_code not in (200, 201):
                avisos.append(f"No se pudo volver a «{p['nombre'] or p['id']}»: {_txt(r)}. Hay que hacerlo en Mercado Libre.")
        await asyncio.sleep(3)
        despues = await leer_estado(cli, item_id)
        if antes.get("cobra") and despues.get("cobra") and abs(despues["cobra"] - antes["cobra"]) >= 0.5:
            avisos.append(f"Antes cobraba ${antes['cobra']:,.2f} y ahora cobra ${despues['cobra']:,.2f}.")
        detalle = {"item": item_id, "cuenta": cuenta, "deshace": ultimo["id"], "antes_del_cambio": antes, "despues": despues,
                   "pasos": pasos, "avisos": avisos}
        await asyncio.to_thread(_anotar, sku, "deshacer", "ok" if not avisos else "parcial", detalle, quien)
        return {"antes": ahora, "despues": despues, "objetivo": antes, "pasos": pasos, "avisos": avisos}


# ── TRABAJOS EN SEGUNDO PLANO ────────────────────────────────────────────────
#
# Un cambio tarda de 1 a 5 minutos (ML tarda en volver a ofrecer el descuento y
# en arrancarlo). La pantalla no se queda colgada de una sola petición: arranca
# un trabajo y pregunta cada pocos segundos, viendo los pasos conforme pasan.
# Viven en memoria: si el backend se reinicia a medio cambio, lo hecho queda en
# la bitácora de pasos de ML, no aquí.

_trabajos: dict[str, dict] = {}
_tareas: set[asyncio.Task] = set()


def iniciar(tipo: str, item_id: str, hacer) -> dict:
    """`hacer(pasos)` es la corrutina (aplicar o deshacer). Devuelve el trabajo."""
    import uuid

    t = {"id": uuid.uuid4().hex[:12], "tipo": tipo, "item": item_id, "estado": "corriendo", "pasos": [],
         "inicio": datetime.now(CDMX).isoformat(timespec="seconds"), "resultado": None, "error": None}
    _trabajos[t["id"]] = t

    async def correr() -> None:
        try:
            t["resultado"] = await hacer(t["pasos"])
            t["estado"] = "listo"
        except ErrorPrecio as exc:
            t["estado"], t["error"] = "error", str(exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("pricing precio: falla inesperada en %s de %s", tipo, item_id)
            t["estado"], t["error"] = "error", f"Falla inesperada ({type(exc).__name__}). Revisa la publicación en Mercado Libre."
        t["fin"] = datetime.now(CDMX).isoformat(timespec="seconds")

    tarea = asyncio.create_task(correr())
    _tareas.add(tarea)
    tarea.add_done_callback(_tareas.discard)
    return t


def trabajo(tid: str) -> dict | None:
    return _trabajos.get(tid)
