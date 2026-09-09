# -*- coding: utf-8 -*-
"""
devoluciones_ml.py — Las devoluciones de Mercado Libre entran a kubera.

Es el traductor: del aviso de ML a `channel.returns` + `channel.return_items`.
Lo usan los tres caminos, y por eso vive aquí y no dentro de ninguno de ellos:

    webhook `post_purchase`  →  sincronizar(claim_id, cuenta)   ← segundos
    barrido de respaldo      →  barrer(dias=2)                   ← cada 60 min
    backfill histórico       →  barrer(desde=…, hasta=…)         ← a mano

DEL AVISO SOLO SE TOMA EL ID
────────────────────────────
La URL del webhook es pública. Si la fila se armara con lo que llega, cualquiera
podría inventar una devolución. Aquí el evento solo aporta el `claim_id`; todo
lo demás se le pregunta a ML. Un evento falso a lo más provoca una consulta que
no encuentra nada. Es el mismo criterio de `pedidos_tiktok.py`.

TRES LLAMADAS, PORQUE NINGUNA SOLA ALCANZA (probadas en vivo el 8-sep-2026)
──────────────────────────────────────────────────────────────────────────
    GET /post-purchase/v1/claims/{id}          quién, cuándo, qué orden
    GET /post-purchase/v2/claims/{id}/returns  piezas, envío, guía, ¿ya pagó?
    GET /post-purchase/v1/claims/{id}/detail   el motivo EN ESPAÑOL, y su plazo

⚠ La v1 de `/returns` está DEPRECADA desde el 6-may-2024 y contesta 400 con un
mensaje genérico. Si alguien copia esa ruta de un ejemplo viejo, no falla
ruidosamente: falla como si el recurso no existiera.

Y UN CRUCE CONTRA LO NUESTRO, porque la API de reclamos no sabe qué SKU es, ni
a qué precio se vendió, ni si salió de nuestra bodega. Eso solo lo sabe
`channel.order_items`.

LAS CUATRO TRAMPAS, TODAS MEDIDAS
─────────────────────────────────
1. `abierta_at` SE MANDA SIEMPRE. El trigger `tg_returns_touch` lo sella con
   `now()` si llega en NULL, así que un backfill descuidado colapsaría siete
   meses de historia en el día de hoy: `returns_daily` mostraría 771
   devoluciones hoy y cero antes. Aquí va SIEMPRE `claim.date_created`.

2. `es_fulfillment` SE LEE DE `order_items`, JAMÁS de `orders`. Las dos columnas
   discrepan en el 40.11% de las líneas de ML (10,941 de 27,280), siempre en el
   mismo sentido. Con la de `orders`, de 59 devoluciones FULL se reportarían 2.

3. `venta_contaba` usa el MISMO filtro que `channel.sales_daily` (migración
   0030), en minúsculas. Si esta lista se separa de la de allá, el KPI empieza a
   restar devoluciones de ventas que nunca contó — o sea, dos veces. En la
   ventana de agosto eran $14,734 de $49,182.

4. Las devoluciones `low_cost` (ML reembolsa sin pedir el retorno) llegan con
   `orders: []`. Para esas, las piezas salen de `claimed_quantity`, que fue el
   MISMO número en 62 de 62 medidas. Sin eso se perderían 3 de cada 62.

EL ESTADO: EL DINERO MANDA
──────────────────────────
ML tiene dos estados paralelos —dónde va el paquete (`status`) y dónde está el
dinero (`status_money`)— y nuestro enum tiene uno solo. La regla es que el
dinero gana: si ML ya reembolsó, la devolución NOS COSTÓ, aunque el paquete
figure como cancelado. El caso existe y está medido (1 de 62: `cancelled` +
`refunded`). El estado crudo de ML se conserva íntegro en `estado_canal`.

IDEMPOTENTE. `on conflict (canal, cuenta, external_return_id) do update`. Correr
esto dos veces sobre el mismo claim no duplica: reescribe. Y tiene que
reescribir, porque mientras la devolución está abierta su estado cambia.
"""
from __future__ import annotations

import asyncio
import json
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import httpx

from config import settings
from services import meli, supabase_db as sdb

log = logging.getLogger("omnicanal.devoluciones_ml")

API = "https://api.mercadolibre.com"
CANAL = "mercado_libre"
CUENTAS = ("BEKURA", "SANCORFASHION")

# El filtro de cancelación, IDÉNTICO al de `channel.sales_daily` (migración
# 0030). En minúsculas porque cada canal escribe la cancelación con su propia
# caja. Ver trampa 3 del encabezado.
_CANCELADOS = ("cancelled", "invalid", "canceled")

# Estado del paquete en ML → nuestro enum (el CHECK de channel.returns solo
# admite estos seis). Lo que no esté en el mapa cae en 'abierta': es el estado
# menos comprometido, y `estado_canal` conserva el crudo para poder auditarlo.
_ESTADO: dict[str, str] = {
    "pending":          "abierta",
    "label_generated":  "abierta",
    "shipped":          "en_transito",
    "in_transit":       "en_transito",
    "delivered":        "recibida",
    "closed":           "cerrada",
    "cancelled":        "rechazada",
    "failed":           "rechazada",
    "not_delivered":    "rechazada",
}


# ── Memoria de lo que NO es devolución ───────────────────────────────────────
#
# El topic `post_purchase` trae mediaciones y cancelaciones además de
# devoluciones, y manda VARIOS avisos por el mismo caso: el reclamo, su
# `actions-history`, y reenvíos. Medido el 9-sep sobre `ops.webhook_events`:
# **141 avisos en 2 horas para 26 claims distintos** — 5.4 avisos por claim.
#
# Sin esto, cada aviso dispara un `GET /claims/{id}` a ML solo para volver a
# descubrir que es una mediación y tirarla. Son ~115 llamadas inútiles cada dos
# horas, y ML YA nos cortó con 429 una vez hoy: el gasto no es teórico, es el
# mismo pozo del que salió el primer bug.
#
# Solo se cachea el veredicto NEGATIVO. Una mediación no se convierte en
# devolución, así que recordarla unas horas es seguro. Las devoluciones NO se
# cachean nunca: su estado cambia y cada aviso es justamente la señal de que
# cambió.
_NO_DEVOLUCION: dict[str, float] = {}
_TTL_NO_DEVOLUCION = 6 * 3600
_TOPE_CACHE = 5000


def _ya_sabemos_que_no(claim_id: str) -> bool:
    exp = _NO_DEVOLUCION.get(claim_id)
    if exp is None:
        return False
    if exp < time.time():
        _NO_DEVOLUCION.pop(claim_id, None)
        return False
    return True


def _recordar_que_no(claim_id: str) -> None:
    ahora = time.time()
    if len(_NO_DEVOLUCION) >= _TOPE_CACHE:
        # Poda simple: fuera lo vencido. Si aun así está lleno, se vacía — es
        # un caché de conveniencia, no una fuente de verdad.
        for k, v in list(_NO_DEVOLUCION.items()):
            if v < ahora:
                del _NO_DEVOLUCION[k]
        if len(_NO_DEVOLUCION) >= _TOPE_CACHE:
            _NO_DEVOLUCION.clear()
    _NO_DEVOLUCION[claim_id] = ahora + _TTL_NO_DEVOLUCION


# ── El catálogo de motivos ───────────────────────────────────────────────────
#
# `GET /post-purchase/v1/claims/reasons/{id}` traduce el código de ML a texto:
#
#     PDD9939 → repentant_buyer
#               «Llegó lo que compré en buenas condiciones pero no lo quiero»
#
# POR QUÉ IMPORTA. La primera versión sacaba el motivo de
# `/claims/{id}/detail.problem`, que solo existe mientras el reclamo está
# ABIERTO: llegaba en 53 de 392 devoluciones (13%). Para el resto la pantalla
# terminaba mostrando `resolution.reason` —`item_returned`, `low_cost`— que NO
# es un motivo: es cómo se resolvió, no por qué la devolvieron.
#
# El código sí está en las 392 de 392, y el catálogo lo traduce todo: 12 códigos
# distintos, 12 traducidos. De ahí sale la lectura que sirve para decidir —336
# arrepentimientos (86%, no es culpa nuestra) contra 37 por descripción, color o
# producto equivocado, que sí lo son.
#
# El catálogo es ESTÁTICO, así que se cachea sin caducidad: son 12 filas y no
# cambian de un día para otro.
_MOTIVOS: dict[str, str] = {}


async def motivo_de(cli: httpx.AsyncClient, cab: dict[str, str],
                    reason_id: str | None) -> str | None:
    """Texto en español del código de motivo. None si ML no lo conoce."""
    if not reason_id:
        return None
    if reason_id in _MOTIVOS:
        return _MOTIVOS[reason_id] or None
    try:
        st, j = await _pedir(cli, cab, f"/post-purchase/v1/claims/reasons/{reason_id}",
                             reintentos=1)
    except SinRespuesta:
        return None            # sin caché: se reintenta la próxima vez
    texto = (j.get("detail") or "").strip() if st == 200 else ""
    _MOTIVOS[reason_id] = texto
    return texto or None


def _num(v: Any, defecto: int | None = None) -> int | None:
    """'1.0' → 1. La API manda las cantidades como string decimal."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return defecto


def _estado(ml_status: str | None, status_money: str | None,
            claim_status: str | None) -> str:
    """Traduce a nuestro enum. El dinero manda — ver encabezado."""
    if status_money == "refunded":
        return "reembolsada"
    if ml_status:
        return _ESTADO.get(ml_status, "abierta")
    # Sin objeto `returns` todavía: solo se sabe si el reclamo está abierto.
    return "cerrada" if claim_status == "closed" else "abierta"


# ── Traer ────────────────────────────────────────────────────────────────────

class SinRespuesta(Exception):
    """ML no contestó (429 o 5xx). NO significa que el dato no exista."""


async def _pedir(cli: httpx.AsyncClient, cab: dict[str, str], ruta: str,
                 *, reintentos: int = 3) -> tuple[int, dict[str, Any]]:
    """GET con reintento en 429 y 5xx. Devuelve (status, json).

    ⚠️ ESTO NO ES CORTESÍA CON ML: ES CORRECCIÓN. Medido el 9-sep con
    concurrencia 6 sobre 23 claims, `/returns` contestó **429 en 5 de 23** — el
    22%. La primera versión trataba cualquier fallo del opcional como "{}", así
    que esos cinco se guardaban como devoluciones sin envío, sin estado del
    dinero y con una llave inventada. Un límite de tasa se convertía en un dato
    falso, en silencio y sin un solo error en el log.

    Es la misma lección de los 964 pedidos fantasma escrita de otra forma: un
    hueco no es un cero, y "no contestó" no es "no existe".
    """
    espera = 1.0
    for intento in range(reintentos + 1):
        r = await cli.get(f"{API}{ruta}", headers=cab)
        if r.status_code == 429 or r.status_code >= 500:
            if intento == reintentos:
                raise SinRespuesta(f"{ruta} → {r.status_code} tras {reintentos} reintentos")
            await asyncio.sleep(espera)
            espera *= 2
            continue
        try:
            return r.status_code, (r.json() if r.status_code == 200 else {})
        except ValueError:
            return r.status_code, {}
    return 0, {}  # inalcanzable; calla al analizador


async def _traer(cli: httpx.AsyncClient, cab: dict[str, str],
                 claim_id: str) -> dict[str, Any] | None:
    """Las tres llamadas de un claim. Devuelve None si el claim NO EXISTE.

    Las dos primeras son obligatorias y se distinguen dos fracasos que parecen
    iguales y no lo son:

      404  →  el recurso no existe. Es un HECHO y se guarda como tal: una
              devolución recién abierta todavía no tiene objeto `returns`.
      429  →  ML no quiso contestar. NO se sabe nada, así que se aborta el
              claim entero y lo repone el siguiente barrido. Guardar aquí sería
              inventar.

    `/detail` sí es prescindible: solo aporta el motivo legible, y los claims
    ya CERRADOS no lo traen (medido: 3 de 3 cerrados sin `problem`). Para esos
    el motivo sale de `resolution.reason`, que viene en el claim.
    """
    st, claim = await _pedir(cli, cab, f"/post-purchase/v1/claims/{claim_id}")
    if st == 404:
        return None
    if st != 200:
        raise SinRespuesta(f"claim {claim_id} → {st}")

    st_r, devol = await _pedir(cli, cab, f"/post-purchase/v2/claims/{claim_id}/returns")
    fallo_returns: int | None = None
    if st_r not in (200, 404):
        # ⚠️ NO SE DESCARTA EL CLAIM. La primera versión lanzaba aquí, y eso
        # dejaba una devolución REAL invisible mientras el problema durara.
        #
        # Medido el 9-sep con el claim 5573786449 (SANCORFASHION): el claim
        # contesta 200 y dice `type: returns`, pero su `/returns` devuelve
        # 401 «Error executing GET [client:shipments]» — un fallo INTERNO de
        # ML, su servicio de devoluciones no pudo hablar con el de envíos. Se
        # repitió en dos corridas con una hora de diferencia. Cruzándolo con la
        # otra cuenta se confirma que no es permiso nuestro: con BEKURA da 403
        # «User does not have access to claim», que es la respuesta correcta
        # para un claim ajeno.
        #
        # Así que el claim YA nos dijo lo importante —que existe y que es una
        # devolución—. Guardarlo con el envío y el dinero en NULL no es
        # inventar: NULL significa «no se sabe», que es la verdad. El barrido
        # de la hora siguiente lo completa cuando ML se recupere. Perderlo
        # entero, en cambio, no se recupera nunca y nadie se entera.
        log.warning("DEVOLUCION ML claim %s: /returns → %s, se captura sin "
                    "envío ni estado del dinero", claim_id, st_r)
        fallo_returns, devol = st_r, {}

    try:
        _, detalle = await _pedir(cli, cab, f"/post-purchase/v1/claims/{claim_id}/detail",
                                  reintentos=1)
    except SinRespuesta:
        detalle = {}   # se pierde el motivo legible, no la devolución

    crudo = {"claim": claim, "returns": devol or {}, "detalle": detalle or {}}
    if fallo_returns:
        # Queda en el payload para poder distinguir después "esta devolución no
        # tiene envío" de "no pudimos leer su envío".
        crudo["returns_error"] = fallo_returns
    return crudo


# ── Cruzar contra lo nuestro ─────────────────────────────────────────────────

def _lineas_de_pedidos(oids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """SKU, precio congelado y `es_fulfillment` BUENO, por pedido.

    `es_fulfillment` sale de order_items — ver trampa 2. `estado_canal` viene de
    la cabecera para poder calcular `venta_contaba`.
    """
    if not oids:
        return {}
    filas = sdb.fetch_all("""
        select o.external_order_id, o.cuenta, o.estado_canal, o.account_id,
               i.linea, i.item_id, i.sku::text as sku,
               i.cantidad, i.precio_unitario, i.es_fulfillment
        from channel.orders o
        join channel.order_items i using (canal, cuenta, external_order_id)
        where o.canal = %s and o.external_order_id = any(%s)
        order by i.linea
    """, (CANAL, oids))
    por_orden: dict[str, list[dict[str, Any]]] = {}
    for f in filas:
        por_orden.setdefault(str(f["external_order_id"]), []).append(f)
    return por_orden


def _cuentas_kubera() -> dict[tuple[str, str], Any]:
    filas = sdb.fetch_all(
        "select id, channel_id, legacy_code from core.accounts")
    return {(f["channel_id"], f["legacy_code"]): f["id"] for f in filas}


# ── Armar ────────────────────────────────────────────────────────────────────

def armar(crudo: dict[str, Any], cuenta: str, *,
          lineas_pedido: list[dict[str, Any]],
          account_id: Any, detectado_via: str) -> tuple[dict, list[dict], list[str]]:
    """Cruza un claim contra su pedido. Devuelve (cabecera, líneas, avisos).

    Función PURA: no toca red ni base. Todo lo que necesita ya viene resuelto.
    Es lo que permite probarla sin producción.
    """
    avisos: list[str] = []
    claim = crudo["claim"]
    devol = crudo["returns"]
    det = crudo["detalle"]

    claim_id = str(claim.get("id"))
    oid = str(claim.get("resource_id"))

    # ── LA LLAVE: EL CLAIM, NO EL ID DE DEVOLUCIÓN ──────────────────────────
    # En Mercado Libre un claim tiene A LO MÁS una devolución, así que el claim
    # identifica el caso sin ambigüedad. El `id` que devuelve `/returns` NO
    # sirve como llave, por dos razones medidas el 9-sep:
    #
    #   · LLEGA DESPUÉS. Una devolución recién abierta todavía no tiene objeto
    #     `returns`; el id aparece cuando ML genera el envío de retorno. Usarlo
    #     de llave crearía la fila con una llave provisional y una SEGUNDA fila
    #     el día que apareciera la definitiva. Duplicado garantizado.
    #   · PUEDE SER 0. Las devoluciones `low_cost` —ML reembolsa sin pedir el
    #     retorno— traen `id: 0`. Con varias low_cost, todas compartirían la
    #     llave "0" y se pisarían entre sí, dejando una sola fila viva.
    #
    # El id real de ML se conserva en `payload`; si algún día hace falta
    # consultarlo, es una columna más, no un rediseño.
    external_return_id = claim_id
    rid = devol.get("id")
    if rid in (0, "0"):
        avisos.append(f"{claim_id}: devolución sin retorno físico (id 0, low_cost)")

    ml_status = devol.get("status")
    money = devol.get("status_money")
    estado = _estado(ml_status, money, claim.get("status"))

    res = det.get("resolution") or claim.get("resolution") or {}
    env = (devol.get("shipments") or [{}])[0]

    # `reembolsada_at`: solo cuando el dinero YA se soltó. Si se dejara en NULL
    # con estado 'reembolsada', el trigger lo sellaría con now() y el flujo de
    # caja de una devolución de marzo aparecería en septiembre.
    reembolsada_at = None
    if estado == "reembolsada":
        reembolsada_at = (res.get("date_created") or devol.get("date_closed")
                          or devol.get("last_updated") or claim.get("last_updated"))

    cab = {
        "canal": CANAL, "cuenta": cuenta,
        "external_return_id": external_return_id,
        "account_id": account_id,
        "external_order_id": oid,
        "external_claim_id": claim_id,
        "tipo": "devolucion",
        "estado": estado,
        "estado_canal": ml_status or claim.get("status"),
        "estado_dinero": money,
        "motivo": (res.get("reason") or None),
        "motivo_canal": claim.get("reason_id"),
        # El catálogo primero: existe para el 100% y su redacción es
        # estable. `/detail.problem` solo vive mientras el reclamo está
        # abierto (13% de cobertura) y lo redacta distinto.
        "motivo_texto": crudo.get("motivo_catalogo") or det.get("problem"),
        "estado_titulo": det.get("title"),
        "accion_responsable": det.get("action_responsible"),
        "fecha_limite": det.get("due_date"),
        # ML no expone ninguno de los tres en estos endpoints. Se dejan en NULL
        # a propósito: un 0 diría "no costó nada", que es distinto de "no se sabe".
        "monto_reembolsado": None,
        "comision_reintegrada": None,
        "costo_envio_retorno": None,
        "destino": None,
        "reintegro_woo": None,          # solo lo sabe quien cancela en Woo
        "es_fulfillment": False,        # se corrige abajo con order_items
        "venta_contaba": None,
        # TRAMPA 1: esto SIEMPRE se manda.
        "abierta_at": claim.get("date_created"),
        "reembolsada_at": reembolsada_at,
        "detectado_via": detectado_via,
        "payload": json.dumps(crudo, ensure_ascii=False),
    }

    # ── El cruce ────────────────────────────────────────────────────────────
    if not lineas_pedido:
        avisos.append(f"{claim_id}: la orden {oid} no está en channel.orders "
                      f"→ sin SKU ni precio")
    else:
        estado_orden = (lineas_pedido[0].get("estado_canal") or "")
        cab["venta_contaba"] = estado_orden.lower() not in _CANCELADOS
        # TRAMPA 2: es_fulfillment de la LÍNEA, no de la orden.
        cab["es_fulfillment"] = bool(lineas_pedido[0].get("es_fulfillment"))

    # ── Las líneas ──────────────────────────────────────────────────────────
    devueltas = devol.get("orders") or []
    if not devueltas:
        # TRAMPA 4: low_cost sin retorno físico. Una sola línea, con las piezas
        # que reclamó el comprador.
        piezas = _num(claim.get("claimed_quantity"), 1) or 1
        devueltas = [{"order_id": oid, "item_id": None, "return_quantity": piezas}]
        if devol:
            avisos.append(f"{claim_id}: sin orders[] "
                          f"({devol.get('subtype') or 'sin subtipo'}) → "
                          f"{piezas} pza(s) desde claimed_quantity")

    lineas: list[dict[str, Any]] = []
    for i, d in enumerate(devueltas, start=1):
        item_id = d.get("item_id")
        # Si el item_id empata, esa línea; si el pedido tiene una sola, esa.
        cand = [l for l in lineas_pedido if str(l["item_id"]) == str(item_id)]
        lp = cand[0] if cand else (lineas_pedido[0] if len(lineas_pedido) == 1 else None)
        if lineas_pedido and not lp:
            avisos.append(f"{claim_id}: el pedido {oid} tiene {len(lineas_pedido)} "
                          f"líneas y el item {item_id} no empató → línea sin SKU")
        piezas = _num(d.get("return_quantity")) or _num(claim.get("claimed_quantity"), 1) or 1
        lineas.append({
            "canal": CANAL, "cuenta": cuenta,
            "external_return_id": external_return_id,
            "linea": i,
            "item_id": item_id or (lp["item_id"] if lp else None),
            "sku": (lp["sku"] if lp else None),
            "linea_pedido": (lp["linea"] if lp else None),
            "titulo": None,
            "cantidad": piezas,
            # Precio de venta CONGELADO, no lo reembolsado. Es lo que permite
            # valorar la devolución el día que se abre.
            "monto_unitario": (lp["precio_unitario"] if lp else None),
            "reintegra_stock": None,
        })
    return cab, lineas, avisos


# ── Guardar ──────────────────────────────────────────────────────────────────

_CAB_COLS = ("canal", "cuenta", "external_return_id", "account_id",
             "external_order_id", "wc_order_id", "tipo", "estado", "estado_canal",
             "motivo", "motivo_canal", "monto_reembolsado", "comision_reintegrada",
             "costo_envio_retorno", "external_claim_id", "es_fulfillment",
             "destino", "reintegro_woo", "venta_contaba", "estado_dinero",
             "motivo_texto", "estado_titulo", "accion_responsable", "fecha_limite",
             "abierta_at", "reembolsada_at", "detectado_via", "payload")
_ITEM_COLS = ("canal", "cuenta", "external_return_id", "linea", "item_id", "sku",
              "linea_pedido", "titulo", "cantidad", "monto_unitario",
              "reintegra_stock")

# `abierta_at` NO se pisa en el update: es la fecha en que supimos por primera
# vez, y el barrido de respaldo vuelve a ver la misma devolución todos los días.
# Sin esta excepción, cada pasada la movería al presente y la devolución
# migraría de día en `returns_daily` — un dato que cambia solo, sin que nadie
# lo edite. Lo mismo con `detectado_via`: el primero que la vio es el que cuenta.
_NO_PISAR = {"canal", "cuenta", "external_return_id", "abierta_at", "detectado_via"}


def _guardar(cab: dict[str, Any], lineas: list[dict[str, Any]]) -> None:
    """Cabecera + líneas en una sola transacción. Idempotente."""
    sets = ", ".join(f"{c} = excluded.{c}" for c in _CAB_COLS if c not in _NO_PISAR)
    with sdb.get_cursor() as cur:
        cur.execute(
            f"""insert into channel.returns ({", ".join(_CAB_COLS)})
                values ({", ".join("%s" for _ in _CAB_COLS)})
                on conflict (canal, cuenta, external_return_id)
                do update set {sets}""",
            tuple(cab.get(c) for c in _CAB_COLS))

        for ln in lineas:
            sets_i = ", ".join(f"{c} = excluded.{c}" for c in _ITEM_COLS
                               if c not in ("canal", "cuenta", "external_return_id", "linea"))
            cur.execute(
                f"""insert into channel.return_items ({", ".join(_ITEM_COLS)})
                    values ({", ".join("%s" for _ in _ITEM_COLS)})
                    on conflict (canal, cuenta, external_return_id, linea)
                    do update set {sets_i}""",
                tuple(ln.get(c) for c in _ITEM_COLS))

        # Si la devolución ENCOGIÓ (ML retiró una línea), las sobrantes quedarían
        # sumando piezas y dinero que ya no existen. Se borran por número de
        # línea, que es determinista.
        cur.execute("""delete from channel.return_items
                       where canal=%s and cuenta=%s and external_return_id=%s
                         and linea > %s""",
                    (cab["canal"], cab["cuenta"], cab["external_return_id"],
                     len(lineas)))


# ── La entrada pública ───────────────────────────────────────────────────────

async def sincronizar(claim_id: str | int, cuenta: str, *,
                      detectado_via: str = "webhook",
                      cli: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Un claim → una devolución en kubera. Nunca lanza: devuelve el motivo.

    Todo lo que espera a la red o al disco va en `await` o en `to_thread`
    (regla 11 de la casa): esto lo llama un webhook, y una llamada síncrona
    aquí congelaría el backend entero, no solo a quien llamó.
    """
    claim_id = str(claim_id)
    if cuenta not in CUENTAS:
        return {"ok": False, "motivo": f"cuenta desconocida: {cuenta}"}
    if _ya_sabemos_que_no(claim_id):
        return {"ok": True, "accion": "ignorado", "motivo": "no es devolución (recordado)"}

    propio = cli is None
    try:
        tok = await asyncio.to_thread(meli._access_token, cuenta)
        if not tok:
            return {"ok": False, "motivo": "sin token vigente"}
        cab_http = {"Authorization": f"Bearer {tok}"}

        if propio:
            cli = httpx.AsyncClient(follow_redirects=True, timeout=30)
        try:
            crudo = await _traer(cli, cab_http, claim_id)
        finally:
            if propio:
                await cli.aclose()

        if crudo is None:
            return {"ok": False, "motivo": "el claim no existe"}
        if (crudo["claim"].get("type") or "") != "returns":
            # Mediaciones y cancelaciones entran por el mismo topic y son la
            # mayoría del volumen. No son devoluciones y no se guardan aquí.
            _recordar_que_no(claim_id)
            return {"ok": True, "accion": "ignorado",
                    "motivo": f"tipo {crudo['claim'].get('type')}"}

        # El motivo en español, del catálogo de ML. Va antes del cruce porque
        # `armar` es pura: todo lo que necesita tiene que llegarle resuelto.
        crudo["motivo_catalogo"] = await motivo_de(
            cli, cab_http, crudo["claim"].get("reason_id"))

        oid = str(crudo["claim"].get("resource_id"))
        pedidos, cuentas = await asyncio.gather(
            asyncio.to_thread(_lineas_de_pedidos, [oid]),
            asyncio.to_thread(_cuentas_kubera),
        )
        cab, lineas, avisos = armar(
            crudo, cuenta,
            lineas_pedido=pedidos.get(oid, []),
            account_id=cuentas.get((CANAL, cuenta)),
            detectado_via=detectado_via)

        await asyncio.to_thread(_guardar, cab, lineas)
        for a in avisos:
            log.info("DEVOLUCION ML aviso · %s", a)
        return {"ok": True, "accion": "guardado",
                "devolucion": cab["external_return_id"], "orden": oid,
                "estado": cab["estado"], "piezas": sum(l["cantidad"] for l in lineas),
                "avisos": avisos}
    except Exception as exc:  # noqa: BLE001
        log.exception("DEVOLUCION ML claim %s falló", claim_id)
        return {"ok": False, "motivo": str(exc)[:200]}


async def _buscar(cli: httpx.AsyncClient, cab: dict[str, str],
                  desde: str, hasta: str) -> list[dict[str, Any]]:
    """Todos los claims de tipo `returns` de una ventana, paginando.

    `range` por sí solo contesta 400: ML exige que vaya acompañado de `type`,
    `stage` o `status`. Aquí va con `type=returns`, que además es el filtro que
    queremos.
    """
    rango = (f"date_created:after:{desde}T00:00:00.000-06:00,"
             f"before:{hasta}T23:59:59.000-06:00")
    # ⚠ SE DEDUPLICA, Y NO ES PARANOIA. `claims/search` ordena por un campo que
    # CAMBIA mientras se pagina, así que un claim que se actualiza entre la
    # página 1 y la 2 se corre de sitio y sale en las dos. Medido el 9-sep:
    # julio devolvió 66 filas con 63 claims distintos; agosto, 108 con 102. Un
    # 5% de repetidos.
    #
    # No corrompe nada —la escritura es idempotente— pero infla los conteos del
    # dry-run y gasta tres llamadas de más por cada repetido. Y sobre todo:
    # hace que el número que se le enseña a alguien para decidir no sea el
    # número real.
    vistos: set[str] = set()
    todos: list[dict[str, Any]] = []
    offset, limite = 0, 50
    while True:
        r = await cli.get(f"{API}/post-purchase/v1/claims/search", headers=cab,
                          params={"type": "returns", "range": rango,
                                  "limit": limite, "offset": offset})
        if r.status_code != 200:
            log.warning("claims/search %s → %s %s", offset, r.status_code, r.text[:150])
            break
        data = r.json()
        pagina = data.get("data") or []
        for c in pagina:
            cid = str(c.get("id"))
            if cid not in vistos:
                vistos.add(cid)
                todos.append(c)
        total = (data.get("paging") or {}).get("total") or 0
        offset += limite
        if offset >= total or not pagina:
            break
    return todos


async def barrer(*, dias: int | None = 2, desde: str | None = None,
                 hasta: str | None = None,
                 cuentas: Iterable[str] = CUENTAS,
                 detectado_via: str = "sondeo",
                 concurrencia: int = 4) -> dict[str, Any]:
    """Recorre una ventana y sincroniza todo lo que encuentre.

    Es la RED DE SEGURIDAD del webhook, y también el motor del backfill: la
    única diferencia entre los dos es el tamaño de la ventana y `detectado_via`.

    Un webhook perdido es invisible —nada avisa de lo que no llegó—, así que
    esto se corre cada hora sobre las últimas 48 h. Repetir una devolución ya
    guardada no cuesta nada: la escritura es idempotente.
    """
    hoy = datetime.now(timezone.utc)
    hasta = hasta or hoy.strftime("%Y-%m-%d")
    desde = desde or (hoy - timedelta(days=dias or 2)).strftime("%Y-%m-%d")

    resumen: dict[str, Any] = {"desde": desde, "hasta": hasta, "cuentas": {},
                               "guardados": 0, "ignorados": 0, "fallos": 0}
    sem = asyncio.Semaphore(concurrencia)

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as cli:
        for cuenta in cuentas:
            tok = await asyncio.to_thread(meli._access_token, cuenta)
            if not tok:
                resumen["cuentas"][cuenta] = {"error": "sin token"}
                continue
            cab = {"Authorization": f"Bearer {tok}"}
            claims = await _buscar(cli, cab, desde, hasta)

            async def _uno(cid: str) -> dict[str, Any]:
                async with sem:
                    return await sincronizar(cid, cuenta,
                                             detectado_via=detectado_via, cli=cli)

            res = await asyncio.gather(*(_uno(str(c["id"])) for c in claims))
            ok = sum(1 for r in res if r.get("accion") == "guardado")
            ig = sum(1 for r in res if r.get("accion") == "ignorado")
            ma = [r for r in res if not r.get("ok")]
            resumen["cuentas"][cuenta] = {
                "claims": len(claims), "guardados": ok, "ignorados": ig,
                "fallos": len(ma),
                "motivos": [m.get("motivo") for m in ma[:5]],
            }
            resumen["guardados"] += ok
            resumen["ignorados"] += ig
            resumen["fallos"] += len(ma)

    log.info("DEVOLUCIONES ML barrido %s→%s: %s guardadas, %s ignoradas, %s fallos",
             desde, hasta, resumen["guardados"], resumen["ignorados"], resumen["fallos"])
    return resumen


async def revisar() -> dict[str, Any]:
    """El job del scheduler. Apagable con `DEVOLUCIONES_ML_ENABLED=false`."""
    if not getattr(settings, "devoluciones_ml_enabled", False):
        return {"ok": False, "motivo": "apagado"}
    dias = getattr(settings, "devoluciones_ml_dias", 2)
    return await barrer(dias=dias, detectado_via="sondeo")
