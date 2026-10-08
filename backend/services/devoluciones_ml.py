# -*- coding: utf-8 -*-
"""
devoluciones_ml.py — Las devoluciones de Mercado Libre entran a kubera.

Es el traductor: del aviso de ML a `channel.returns` + `channel.return_items`.
Lo usan los tres caminos, y por eso vive aquí y no dentro de ninguno de ellos:

    webhook `post_purchase`  →  sincronizar(claim_id, cuenta)   ← segundos
    barrido de respaldo      →  barrer(dias=2)                   ← cada 60 min
    refresco de lo abierto   →  refrescar_abiertas(tope=…)       ← cada 60 min
    avisos que fallaron      →  reintentar_avisos()              ← cada 60 min
    venta_contaba vivo       →  recalcular_venta_contaba()       ← cada 60 min, sin ML
    mediaciones sin fila     →  barrer_mediaciones_amplio()      ← 1 vez al día
    recuperación a mano      →  scripts/recuperar_devoluciones_ml.py

DEVOLUCIÓN NO ES LO MISMO QUE RECLAMO `returns` (5-oct-2026)
─────────────────────────────────────────────────────────────
ML también abre devoluciones dentro de reclamos de tipo `mediations` (producto
distinto, defectuoso…). Hasta v0.625.0 se tiraban todas: de 9 mediaciones
medidas, 7 traían su objeto de devolución y ninguna estaba en
`channel.returns`; en 90 días hubo 1,970 mediaciones y se capturaron 0. Ahora
`clasificar` decide con lo que ya se le preguntó a ML: una mediación se guarda
si trae devolución, y si todavía no la trae se espera SIN memorizar el «no».
Se enciende con `DEVOLUCIONES_ML_MEDIACIONES` (cambia cifras que ven Brandon y
José: regla 3 de la casa).

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
from typing import Any, Callable, Iterable, Mapping

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

# Estado del paquete en ML → nuestro enum (el CHECK `ck_returns_estado` de la
# 0049 solo admite seis valores; `test_el_mapa_cabe_en_el_check` lo vigila).
# Lo que no esté en el mapa cae en 'abierta' —el estado menos comprometido— y
# `armar` deja un aviso en el log: `estado_canal` conserva el crudo.
#
# `expired` (5-oct-2026): el comprador no mandó la caja en el plazo. Hasta
# v0.625.0 no estaba en el mapa y 54 filas se veían como 'abierta', 48 con más
# de 20 días. Es otra forma de «la devolución no ocurrió», el mismo cajón que
# cancelled/failed/not_delivered: 'rechazada', que `returns_daily` excluye a
# propósito. 'cerrada' sería peor: la contaría como devolución en todas las
# vistas. Si ML sí reembolsó, el dinero manda y queda 'reembolsada' (`_estado`
# lo mira antes que el mapa). No hace falta ningún valor nuevo en el CHECK.
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
    "expired":          "rechazada",
}


# ── Memoria de lo que NO es devolución ───────────────────────────────────────
#
# El topic `post_purchase` trae mediaciones y cancelaciones además de
# devoluciones, y manda VARIOS avisos por el mismo caso: el reclamo, su
# `actions-history`, y reenvíos. Medido el 9-sep sobre `ops.webhook_events`:
# **141 avisos en 2 horas para 26 claims distintos** — 5.4 avisos por claim.
#
# Sin esto, cada aviso dispara un `GET /claims/{id}` a ML solo para volver a
# descubrir que es una cancelación y tirarla. Son ~115 llamadas inútiles cada
# dos horas, y ML YA nos cortó con 429 una vez hoy: el gasto no es teórico, es
# el mismo pozo del que salió el primer bug.
#
# Solo se cachea el veredicto NEGATIVO DEFINITIVO (`clasificar` → 'descartar').
# ⚠ El supuesto viejo —«una mediación no se convierte en devolución»— era
# FALSO (5-oct-2026): ML cuelga devoluciones de reclamos `mediations`, y la
# devolución puede aparecer DESPUÉS del primer aviso. Por eso una mediación sin
# devolución todavía ('esperar') no se memoriza: el aviso que anuncia su
# devolución no debe caer en un «no» guardado. Las devoluciones tampoco se
# cachean: su estado cambia y cada aviso es justamente la señal de que cambió.
#
# Se guarda el tipo del reclamo junto al vencimiento: una mediación descartada
# con las mediaciones APAGADAS no debe tapar a quien las pide encendidas (el
# refresco de F3 y el script de recuperación).
_NO_DEVOLUCION: dict[str, tuple[float, str]] = {}
_TTL_NO_DEVOLUCION = 6 * 3600
_TOPE_CACHE = 5000


def _ya_sabemos_que_no(claim_id: str, *, mediaciones: bool = False) -> bool:
    hit = _NO_DEVOLUCION.get(claim_id)
    if hit is None:
        return False
    exp, tipo = hit
    if exp < time.time():
        _NO_DEVOLUCION.pop(claim_id, None)
        return False
    if mediaciones and tipo == "mediations":
        return False     # se descartó con las mediaciones apagadas
    return True


def _recordar_que_no(claim_id: str, tipo: str = "") -> None:
    ahora = time.time()
    if len(_NO_DEVOLUCION) >= _TOPE_CACHE:
        # Poda simple: fuera lo vencido. Si aun así está lleno, se vacía — es
        # un caché de conveniencia, no una fuente de verdad.
        for k, (v, _t) in list(_NO_DEVOLUCION.items()):
            if v < ahora:
                del _NO_DEVOLUCION[k]
        if len(_NO_DEVOLUCION) >= _TOPE_CACHE:
            _NO_DEVOLUCION.clear()
    _NO_DEVOLUCION[claim_id] = (ahora + _TTL_NO_DEVOLUCION, tipo)


# ── ¿Este reclamo es una devolución? (F1, 5-oct-2026) ───────────────────────
#
# Decide con lo que `_traer` YA pidió: `/returns` se pide para todo claim, así
# que no hace falta ninguna llamada nueva. Dos señales, basta una:
#
#   · `claim.related_entities` trae 'return' (ML dice que hay devolución);
#   · `/returns` contestó con datos (un `id` —aunque sea 0, low_cost— o un
#     `status`).
#
# `claims/search` NO trae `related_entities`: por eso se decide con el claim
# completo y no con la búsqueda.

def _entidades(claim: dict[str, Any]) -> set[str]:
    """`related_entities` normalizado. Medido como lista de strings
    (`["return"]`); se tolera también una lista de objetos con `type`."""
    out: set[str] = set()
    for e in claim.get("related_entities") or []:
        if isinstance(e, dict):
            e = e.get("type") or e.get("entity") or ""
        if e:
            out.add(str(e).lower())
    return out


def _trae_devolucion(crudo: dict[str, Any]) -> bool:
    claim, devol = crudo["claim"], crudo.get("returns") or {}
    if "return" in _entidades(claim):
        return True
    return devol.get("id") is not None or bool(devol.get("status"))


def clasificar(crudo: dict[str, Any], *, mediaciones: bool) -> tuple[str, str]:
    """('guardar' | 'esperar' | 'descartar', motivo).

    · guardar   → se arma y se guarda. La llave sigue siendo el claim y `tipo`
                  sigue siendo 'devolucion'; el tipo del reclamo queda en
                  `payload->'claim'->>'type'`. Sin columna nueva.
    · esperar   → mediación sin devolución TODAVÍA. No se guarda y NO se
                  memoriza: la devolución puede aparecer después.
    · descartar → cancelaciones y demás tipos (y las mediaciones cuando están
                  apagadas). Se memoriza unas horas, como siempre.

    Si `/returns` falló con un código raro (`returns_error`) y el claim dice
    que hay devolución, se guarda con el envío en NULL —el criterio de
    `_traer`—; si no lo dice, se espera.
    """
    tipo = crudo["claim"].get("type") or ""
    if tipo == "returns":
        return "guardar", "returns"
    if tipo == "mediations" and mediaciones:
        if _trae_devolucion(crudo):
            return "guardar", "mediación con devolución"
        return "esperar", "mediación sin devolución (todavía)"
    return "descartar", f"tipo {tipo or '?'}"


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
    except (SinRespuesta, httpx.TransportError):
        # Un 429 o un timeout/conexión caída: se pierde el motivo legible, no
        # la devolución. Sin caché: se reintenta la próxima vez. Un
        # RuntimeError (p. ej. «client has been closed», la F2) NO se atrapa:
        # es un error de programación y tiene que sonar.
        return None
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
                 *, reintentos: int = 3,
                 params: Mapping[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
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
        r = await cli.get(f"{API}{ruta}", headers=cab, params=params)
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
        log.warning("DEVOLUCION ML claim %s (%s): /returns → %s, si se captura "
                    "va sin envío ni estado del dinero", claim_id,
                    claim.get("type") or "?", st_r)
        fallo_returns, devol = st_r, {}

    crudo = {"claim": claim, "returns": devol or {}, "detalle": {}}
    if fallo_returns:
        # Queda en el payload para poder distinguir después "esta devolución no
        # tiene envío" de "no pudimos leer su envío".
        crudo["returns_error"] = fallo_returns

    # Un reclamo que no es `returns` y no trae devolución no se va a guardar
    # (`clasificar`): su `/detail` sería un GET tirado. Se ahorra uno por cada
    # aviso de una mediación o cancelación sin devolución.
    if (claim.get("type") or "") != "returns" and not _trae_devolucion(crudo):
        return crudo

    try:
        _, detalle = await _pedir(cli, cab, f"/post-purchase/v1/claims/{claim_id}/detail",
                                  reintentos=1)
    except SinRespuesta:
        detalle = {}   # se pierde el motivo legible, no la devolución
    crudo["detalle"] = detalle or {}
    return crudo


# ── A dónde va la caja (F5, 5-oct-2026) ──────────────────────────────────────

def _destino(devol: dict[str, Any]) -> str | None:
    """`shipments[].destination.name`, el valor CRUDO de ML: 'seller_address'
    (nuestra dirección) o 'warehouse' (almacén de ML). Es la ÚNICA regla: la
    usan la captura (`armar`) y el relleno de las filas existentes (script de
    recuperación, sin llamar a ML).

    Si algún tramo va a nuestra dirección, es 'seller_address': la caja termina
    en nuestra puerta aunque pase antes por el almacén de ML. Si no, el último
    tramo. Que haya devoluciones con varios tramos es INFERIDO, no medido; con
    uno solo, la regla devuelve ese.

    Solo se toma `name`: el objeto `destination` puede traer la dirección
    completa, y ningún otro campo suyo se copia a ninguna columna.

    Un `destination` que no es objeto (texto, lista) cuenta como tramo sin
    nombre: esto corre dentro de `armar`, y una excepción aquí tiraría la
    devolución entera por un dato que solo es informativo.
    """
    nombres = []
    for s in (devol.get("shipments") or []):
        if not isinstance(s, dict):
            continue
        d = s.get("destination")
        nombres.append(d.get("name") if isinstance(d, dict) else None)
    nombres = [str(n) for n in nombres if n]
    if not nombres:
        return None
    return "seller_address" if "seller_address" in nombres else nombres[-1]


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
    if ml_status and ml_status not in _ESTADO and money != "refunded":
        # El próximo estado nuevo de ML se ve en el log en vez de esconderse
        # en 'abierta' (así se escondió `expired` hasta el 5-oct-2026).
        avisos.append(f"{claim_id}: estado de ML desconocido '{ml_status}' → abierta")

    res = det.get("resolution") or claim.get("resolution") or {}

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
        # F5: el valor crudo de ML ('seller_address' | 'warehouse'). Hasta
        # v0.625.0 iba fijo en None aunque el payload lo traía (481 de 525).
        "destino": _destino(devol),
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

# Columnas que una lectura nueva SIN el dato no debe borrar: atributos ESTABLES
# del caso, no estados (las demás columnas se pisan como siempre).
#
#   · `destino` (F5): una lectura de `/returns` fallida llega como `{}` y no
#     debe dejar en NULL un destino que ya se conocía.
#   · `motivo_texto`: si el catálogo de motivos no contesta (429, timeout,
#     conexión caída: `motivo_de` → None) y el reclamo ya está cerrado, no hay
#     `/detail.problem` de respaldo (3 de 3 cerrados sin él). Sin el coalesce el
#     upsert pisaría con NULL un texto bueno, y «por motivo» volvería a mostrar
#     `resolution.reason` ('item_returned'): el error que el encabezado de la
#     sección de motivos describe. Pasa con la primera consulta de cada código
#     tras cada deploy, cuando `_MOTIVOS` está vacío.
_NO_BORRAR = {"destino", "motivo_texto"}


def _set_de(c: str) -> str:
    if c in _NO_BORRAR:
        return f"{c} = coalesce(excluded.{c}, channel.returns.{c})"
    return f"{c} = excluded.{c}"


def _guardar(cab: dict[str, Any], lineas: list[dict[str, Any]]) -> None:
    """Cabecera + líneas en una sola transacción. Idempotente."""
    sets = ", ".join(_set_de(c) for c in _CAB_COLS if c not in _NO_PISAR)
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


# ── Candados antes de escribir ───────────────────────────────────────────────

def _returns_guardado(cuenta: str, claim_id: str) -> bool:
    """¿La fila guardada ya tiene un objeto `returns` con datos?

    CANDADO CONTRA LA DEGRADACIÓN (F3). Si `/returns` contesta un error raro
    (`returns_error`), la lectura nueva solo sabe lo que dice el claim, y el
    upsert pisaría `estado_dinero`, `estado_canal` y `estado` con eso: una fila
    'reembolsada' volvería a 'abierta' por una lectura fallida. Con un refresco
    que relee filas viejas cada hora, ese riesgo deja de ser raro (el claim
    5573786449 tiene un 401 interno de ML permanente en `/returns`).
    """
    fila = sdb.fetch_one(
        """select (payload->'returns'->>'id') is not null
                  or coalesce(payload->'returns'->>'status', '') <> '' as tiene
             from channel.returns
            where canal = %s and cuenta = %s and external_return_id = %s""",
        (CANAL, cuenta, claim_id))
    return bool(fila and fila.get("tiene"))


def _duplicada_de(cuenta: str, oid: str, claim_id: str, rid: Any) -> str | None:
    """Otro claim de la misma cuenta y la misma orden que ya guardó ESTA MISMA
    devolución (mismo `payload->'returns'->>'id'`). Devuelve su claim o None.

    La llave es el claim, así que si ML colgara la misma devolución de dos
    reclamos (p. ej. uno `returns` y una `mediations`) saldrían dos filas y las
    piezas se contarían dos veces. No está medido que pase; si nunca pasa, el
    candado no estorba. Se compara la DEVOLUCIÓN y no la orden, porque una orden
    sí puede tener dos reclamos legítimos (`fulfillment.py`, misma orden y pieza
    con uno cancelado y otro cumplido). Usa `idx_returns_orden`.
    """
    fila = sdb.fetch_one(
        """select external_return_id
             from channel.returns
            where canal = %s and cuenta = %s and external_order_id = %s
              and external_return_id <> %s
              and payload->'returns'->>'id' = %s
            order by length(external_return_id), external_return_id
            limit 1""",
        (CANAL, cuenta, oid, claim_id, str(rid)))
    return str(fila["external_return_id"]) if fila else None


# ── Una devolución, una fila: quién se queda (5-oct-2026) ───────────────────
#
# EL HUECO QUE CIERRA. Una mediación con `related_entities=['return']` cuyo
# `/returns` todavía da 404 se guarda SIN `returns.id`. Si después otro claim
# (p. ej. uno `returns`) trae esa misma devolución con id X, no encuentra
# ninguna fila con X y también se guarda: dos filas, piezas contadas dos veces
# en `returns_daily`. Cuando la mediación se relee ya con X, el candado de
# arriba la detecta, pero no escribir NO basta: su fila vieja seguiría viva.
#
# LA REGLA, sin depender de quién llegó primero: se queda el claim de MENOR id
# y la fila del otro se BORRA (sus líneas y su historia se van en cascada: FK
# `on delete cascade` de la 0049). Si el que se lee ahora es el menor, se
# escribe y después se retira el otro; si es el mayor, se retira su fila vieja
# y no se escribe. Así dos lecturas simultáneas de los dos claims borran la
# MISMA fila y nunca las dos.

def _se_queda(claim_id: str, otro: str) -> bool:
    """¿El claim que se está leyendo es el que se queda? El de menor id."""
    try:
        return int(claim_id) < int(otro)
    except (TypeError, ValueError):
        return str(claim_id) < str(otro)


def _retirar_duplicada(cuenta: str, perdedor: str, ganador: str, rid: Any) -> int:
    """Borra la fila del claim `perdedor` SOLO si la del `ganador` sigue viva
    con la misma devolución (`payload->'returns'->>'id' = rid`). Una sola
    sentencia: atómica. Devuelve cuántas filas borró (0 si no había nada que
    retirar, que es lo normal: el perdedor casi nunca tiene fila)."""
    with sdb.get_cursor() as cur:
        cur.execute(
            """delete from channel.returns r
                where r.canal = %s and r.cuenta = %s and r.external_return_id = %s
                  and exists (select 1 from channel.returns o
                               where o.canal = r.canal and o.cuenta = r.cuenta
                                 and o.external_return_id = %s
                                 and o.payload->'returns'->>'id' = %s)""",
            (CANAL, cuenta, str(perdedor), str(ganador), str(rid)))
        return cur.rowcount or 0


def _ya_guardados(cuenta: str, ids: Iterable[str]) -> set[str]:
    """De estos claims, los que ya tienen fila en `channel.returns`."""
    ids = [str(i) for i in ids]
    if not ids:
        return set()
    filas = sdb.fetch_all(
        """select external_return_id
             from channel.returns
            where canal = %s and cuenta = %s and external_return_id = any(%s)""",
        (CANAL, cuenta, ids))
    return {str(f["external_return_id"]) for f in filas}


# ── La entrada pública ───────────────────────────────────────────────────────

class _Ritmo(httpx.AsyncBaseTransport):
    """Espacia CADA GET a lo más `ritmo` por segundo, con un candado: el ritmo
    es del cliente entero, no de cada corrutina. Lo usa el refresco de F3, que
    hace 3–4 GET seguidos por reclamo (claim, /returns, /detail y a veces el
    motivo): una pausa entre reclamos no acotaba los GET por segundo."""

    def __init__(self, interno: httpx.AsyncBaseTransport, ritmo: float, *,
                 reloj: Callable[[], float] = time.monotonic,
                 dormir: Callable[[float], Any] = asyncio.sleep) -> None:
        self._interno = interno
        self._intervalo = 1.0 / ritmo
        self._reloj, self._dormir = reloj, dormir
        self._candado: asyncio.Lock | None = None
        self._ultimo: float | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._candado is None:
            self._candado = asyncio.Lock()
        async with self._candado:
            if self._ultimo is not None:
                espera = self._ultimo + self._intervalo - self._reloj()
                if espera > 0:
                    await self._dormir(espera)
            self._ultimo = self._reloj()
        return await self._interno.handle_async_request(request)

    async def aclose(self) -> None:
        await self._interno.aclose()


# EL FRENO DE LOS BARRIDOS (revisión del 7-oct). El barrido horario y el amplio
# llamaban a ML sin freno —concurrencia 4 y 2, sin espaciar los GET—, y ML ya
# contestó 429 por debajo de 2 GET/s en el sandbox. Ahora los tres jobs (barrido,
# refresco y reintento de avisos) espacian cada GET a `DEVOLUCIONES_ML_RITMO`
# (1.5 por omisión) y, dentro de `revisar`, comparten UN cliente: el refresco ya
# no arranca a toda velocidad justo detrás de la ráfaga del barrido. El webhook
# sigue sin freno: es un claim por aviso y no puede esperar turno.
_RITMO_POR_OMISION = 1.5


def _ritmo() -> float:
    """GET/s de los barridos. Un valor inválido o ≤0 cae al de omisión: un
    barrido sin freno es justo lo que esto existe para evitar."""
    try:
        r = float(getattr(settings, "devoluciones_ml_ritmo", _RITMO_POR_OMISION))
    except (TypeError, ValueError):
        return _RITMO_POR_OMISION
    return r if r > 0 else _RITMO_POR_OMISION


def _cliente(ritmo: float | None = None) -> httpx.AsyncClient:
    """El cliente HTTP propio (aparte para que las pruebas lo sustituyan). Con
    `ritmo` (GET/s), cada GET se espacia; sin él, sin freno (el webhook)."""
    if ritmo and ritmo > 0:
        return httpx.AsyncClient(transport=_Ritmo(httpx.AsyncHTTPTransport(), ritmo),
                                 follow_redirects=True, timeout=30)
    return httpx.AsyncClient(follow_redirects=True, timeout=30)


async def _token(cuenta: str, tokens: Mapping[str, str] | None) -> str | None:
    """El access_token de la cuenta. Con `tokens` inyectado NUNCA se llama a
    `meli`: el script de recuperación lleva el token de producción leído en
    memoria, y `meli._access_token` sin TOKENS_SOLO_KUBERA arbitra contra el
    MySQL. Este módulo no renueva nunca: un 401 se vuelve `SinRespuesta`."""
    if tokens is not None:
        return tokens.get(cuenta)
    return await asyncio.to_thread(meli._access_token, cuenta)


def _mediaciones_encendidas() -> bool:
    return bool(getattr(settings, "devoluciones_ml_mediaciones", False))


async def sincronizar(claim_id: str | int, cuenta: str, *,
                      detectado_via: str = "webhook",
                      cli: httpx.AsyncClient | None = None,
                      tokens: Mapping[str, str] | None = None,
                      guardar: Callable[[dict, list[dict]], None] | None = None,
                      mediaciones: bool | None = None) -> dict[str, Any]:
    """Un claim → una devolución en kubera. Nunca lanza: devuelve el motivo.

    Todo lo que espera a la red o al disco va en `await` o en `to_thread`
    (regla 11 de la casa): esto lo llama un webhook, y una llamada síncrona
    aquí congelaría el backend entero, no solo a quien llamó.

    Inyectables (el script de recuperación y las pruebas): `cli` (si viene, ni
    se abre ni se cierra), `tokens` (cuenta → access_token; con él no se toca
    `meli`), `guardar` (por omisión `_guardar`; el dry-run pasa un colector) y
    `mediaciones` (por omisión `DEVOLUCIONES_ML_MEDIACIONES`).

    Quien escribe decide también si se BORRA una fila duplicada (ver «Una
    devolución, una fila»): sin `guardar`, `_retirar_duplicada`; con `guardar`,
    su método `retirar` si lo tiene (el colector del dry-run solo lo anota). Un
    `guardar` sin `retirar` (las pruebas) no borra nada: solo informa. En el
    flujo vivo (sin `guardar`) el borrado exige `DEVOLUCIONES_ML_MEDIACIONES`
    encendida; apagada, nunca se borra ni se crea una segunda fila, pero el
    claim que YA tiene la suya la sigue actualizando (si no, se congelaría).

    `accion`: 'guardado' · 'ignorado' (con `decision` = descartar | esperar |
    duplicada) · 'conservado' (la lectura nueva vino incompleta y la fila
    guardada es mejor: no se escribe). `retirada` dice si se borró una fila
    duplicada.
    """
    claim_id = str(claim_id)
    if cuenta not in CUENTAS:
        return {"ok": False, "motivo": f"cuenta desconocida: {cuenta}"}
    if mediaciones is None:
        mediaciones = _mediaciones_encendidas()
    if _ya_sabemos_que_no(claim_id, mediaciones=mediaciones):
        return {"ok": True, "accion": "ignorado", "decision": "descartar",
                "motivo": "no es devolución (recordado)"}

    propio = cli is None
    try:
        tok = await _token(cuenta, tokens)
        if not tok:
            return {"ok": False, "motivo": "sin token vigente"}
        cab_http = {"Authorization": f"Bearer {tok}"}

        # F2 (5-oct-2026): UN SOLO TRAMO DE VIDA DEL CLIENTE para TODO lo que
        # habla con ML. Hasta v0.625.0 el cliente propio (el del webhook) se
        # cerraba justo después de `_traer` y el motivo se pedía con el cliente
        # ya cerrado: «Cannot send a request, as the client has been closed»
        # —31 avisos fallidos del 2 al 5-oct— y la devolución NO SE GUARDABA,
        # porque el error subía al `except` general. El `finally` cierra en
        # todos los caminos, también en los `return` tempranos.
        if propio:
            cli = _cliente()
        try:
            crudo = await _traer(cli, cab_http, claim_id)
            if crudo is None:
                return {"ok": False, "motivo": "el claim no existe"}

            decision, por_que = clasificar(crudo, mediaciones=mediaciones)
            if decision == "descartar":
                # Cancelaciones y demás tipos entran por el mismo topic y son
                # la mayoría del volumen. No son devoluciones.
                _recordar_que_no(claim_id, crudo["claim"].get("type") or "")
                return {"ok": True, "accion": "ignorado", "decision": decision,
                        "motivo": por_que}
            if decision == "esperar":
                # SIN caché: el aviso que traiga su devolución debe entrar.
                return {"ok": True, "accion": "ignorado", "decision": decision,
                        "motivo": por_que}

            # El motivo en español, del catálogo de ML. Va antes del cruce
            # porque `armar` es pura: todo le tiene que llegar resuelto.
            crudo["motivo_catalogo"] = await motivo_de(
                cli, cab_http, crudo["claim"].get("reason_id"))
        finally:
            if propio:
                await cli.aclose()
        # De aquí en adelante no se habla con ML.

        oid = str(crudo["claim"].get("resource_id"))
        if crudo.get("returns_error") and await asyncio.to_thread(
                _returns_guardado, cuenta, claim_id):
            log.warning("DEVOLUCION ML claim %s: /returns → %s y la fila guardada "
                        "ya tiene su devolución; se conserva sin escribir",
                        claim_id, crudo["returns_error"])
            return {"ok": True, "accion": "conservado",
                    "motivo": f"/returns → {crudo['returns_error']}; se conserva lo guardado"}

        retirar = (_retirar_duplicada if guardar is None
                   else getattr(guardar, "retirar", None))
        rid = (crudo.get("returns") or {}).get("id")
        perdedor: str | None = None      # el claim cuya fila se retira tras escribir
        duplicada_sin_borrar: str | None = None   # el otro claim, si se escribe sin retirar
        if rid not in (None, 0, "0", ""):
            otro = await asyncio.to_thread(_duplicada_de, cuenta, oid, claim_id, rid)
            if otro and guardar is None and not _mediaciones_encendidas():
                # EL BORRADO VA CON LA BANDERA (revisión del 7-oct). El único
                # DELETE del flujo vivo no puede entrar con el deploy: la regla
                # «se queda el menor id» se diseñó para las mediaciones, y con
                # ellas apagadas no hay medición de que dos `returns` compartan
                # devolución. Apagada NUNCA se borra ni se crea una SEGUNDA fila
                # (contaría las piezas dos veces); se informa en el log y en el
                # resultado para medirlo antes de encender.
                #
                # Pero si ESTE claim ya tiene su fila, se sigue actualizando
                # (segunda revisión del 7-oct): no escribir la congelaría para
                # siempre —el webhook, el barrido, el refresco y el reintento
                # contestarían 'duplicada' a los dos claims y una devolución
                # abierta nunca pasaría a 'reembolsada'—. Así se comportaba la
                # v0.625.0 con las dos filas: ambas seguían vivas y al día.
                if claim_id in await asyncio.to_thread(_ya_guardados, cuenta, [claim_id]):
                    log.warning("DEVOLUCION ML claim %s: la devolución %s también está "
                                "guardada con el claim %s; DEVOLUCIONES_ML_MEDIACIONES "
                                "apagada: se actualiza su fila sin borrar la otra",
                                claim_id, rid, otro)
                    duplicada_sin_borrar = otro
                else:
                    log.warning("DEVOLUCION ML claim %s: la devolución %s ya está guardada "
                                "con el claim %s; DEVOLUCIONES_ML_MEDIACIONES apagada: no se "
                                "borra ni se escribe", claim_id, rid, otro)
                    return {"ok": True, "accion": "ignorado", "decision": "duplicada",
                            "retirada": False,
                            "motivo": f"duplicada de claim {otro} (sin borrar: "
                                      "DEVOLUCIONES_ML_MEDIACIONES apagada)"}
            elif otro and not _se_queda(claim_id, otro):
                # Se queda el otro. Si ESTE claim tiene una fila vieja (la
                # guardó cuando todavía no traía `returns.id`), se retira.
                n = (await asyncio.to_thread(retirar, cuenta, claim_id, otro, rid)
                     if retirar else 0)
                log.warning("DEVOLUCION ML claim %s: la devolución %s ya está guardada "
                            "con el claim %s; no se duplica%s", claim_id, rid, otro,
                            " (se retiró la fila vieja de este claim)" if n else "")
                return {"ok": True, "accion": "ignorado", "decision": "duplicada",
                        "retirada": bool(n),
                        "motivo": f"duplicada de claim {otro}"
                                  + ("; se retiró su fila vieja" if n else "")}
            else:
                perdedor = otro

        pedidos, cuentas = await asyncio.gather(
            asyncio.to_thread(_lineas_de_pedidos, [oid]),
            asyncio.to_thread(_cuentas_kubera),
        )
        cab, lineas, avisos = armar(
            crudo, cuenta,
            lineas_pedido=pedidos.get(oid, []),
            account_id=cuentas.get((CANAL, cuenta)),
            detectado_via=detectado_via)

        await asyncio.to_thread(guardar or _guardar, cab, lineas)
        retirada = False
        if perdedor:
            # Se queda ESTE (menor id): ya escrito, se retira la fila del otro.
            # Primero escribir y después borrar: si algo cae en medio quedan
            # dos filas un rato (las resuelve la siguiente lectura), nunca cero.
            n = (await asyncio.to_thread(retirar, cuenta, perdedor, claim_id, rid)
                 if retirar else 0)
            retirada = bool(n)
            log.warning("DEVOLUCION ML claim %s: la devolución %s también estaba con el "
                        "claim %s; se queda este (menor id)%s", claim_id, rid, perdedor,
                        "; se retiró la otra fila" if n else "")
            avisos.append(f"{claim_id}: la devolución también estaba con el claim "
                          f"{perdedor}; se queda este"
                          + ("; se retiró la otra fila" if n else ""))
        if duplicada_sin_borrar:
            avisos.append(f"{claim_id}: la devolución también está guardada con el claim "
                          f"{duplicada_sin_borrar}; no se borra (DEVOLUCIONES_ML_MEDIACIONES "
                          "apagada)")
        for a in avisos:
            log.info("DEVOLUCION ML aviso · %s", a)
        return {"ok": True, "accion": "guardado", "decision": "guardar",
                "tipo_reclamo": crudo["claim"].get("type"),
                "devolucion": cab["external_return_id"], "orden": oid,
                "estado": cab["estado"], "piezas": sum(l["cantidad"] for l in lineas),
                "retirada": retirada, "avisos": avisos}
    except Exception as exc:  # noqa: BLE001
        log.exception("DEVOLUCION ML claim %s falló", claim_id)
        return {"ok": False, "motivo": str(exc)[:200]}


async def _buscar(cli: httpx.AsyncClient, cab: dict[str, str],
                  desde: str, hasta: str, tipo: str = "returns", *,
                  marcas: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Todos los claims de un `tipo` ('returns' | 'mediations') de una
    ventana por `date_created`, paginando.

    `range` por sí solo contesta 400: ML exige que vaya acompañado de `type`,
    `stage` o `status`. Aquí va con `type`, que además es el filtro que
    queremos. La búsqueda NO trae `related_entities`: si una mediación trae
    devolución lo decide `sincronizar` con el claim completo.

    UNA BÚSQUEDA CORTADA NO ES UNA BÚSQUEDA VACÍA (revisión del 7-oct). Hasta
    v0.625.0 un 429 en la página 2 hacía `break` y devolvía la página 1 como
    si fuera todo: el barrido reportaba «0 fallos» habiendo visto la mitad.
    Ahora cada página pasa por `_pedir` (reintenta 429/5xx) y, si aun así no
    hay respuesta, se devuelve lo leído y se anota en `marcas['incompleta']`
    el porqué, para que quien llama lo cuente como fallo.
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
        try:
            st, data = await _pedir(cli, cab, "/post-purchase/v1/claims/search",
                                    params={"type": tipo, "range": rango,
                                            "limit": limite, "offset": offset})
        except SinRespuesta as exc:
            st, data = 0, {}
            motivo = str(exc)
        else:
            motivo = f"claims/search {tipo} offset {offset} → {st}"
        if st != 200:
            log.warning("DEVOLUCIONES ML búsqueda INCOMPLETA (%s): %s; se usan los "
                        "%s claims leídos", tipo, motivo, len(todos))
            if marcas is not None:
                marcas["incompleta"] = motivo
            break
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


def _contar(res: list[dict[str, Any]]) -> dict[str, Any]:
    ma = [r for r in res if not r.get("ok")]
    return {
        "guardados": sum(1 for r in res if r.get("accion") == "guardado"),
        "ignorados": sum(1 for r in res if r.get("accion") == "ignorado"),
        "esperando": sum(1 for r in res if r.get("decision") == "esperar"),
        "conservados": sum(1 for r in res if r.get("accion") == "conservado"),
        "retiradas": sum(1 for r in res if r.get("retirada")),
        "fallos": len(ma),
        "motivos": [m.get("motivo") for m in ma[:5]],
    }


async def barrer(*, dias: int | None = 2, desde: str | None = None,
                 hasta: str | None = None,
                 cuentas: Iterable[str] = CUENTAS,
                 detectado_via: str = "sondeo",
                 concurrencia: int = 4,
                 tipos: Iterable[str] | None = None,
                 cli: httpx.AsyncClient | None = None,
                 tokens: Mapping[str, str] | None = None,
                 guardar: Callable[[dict, list[dict]], None] | None = None,
                 mediaciones: bool | None = None,
                 omitir_guardados: bool = False) -> dict[str, Any]:
    """Recorre una ventana y sincroniza todo lo que encuentre.

    Es la RED DE SEGURIDAD del webhook: un webhook perdido es invisible —nada
    avisa de lo que no llegó—, así que esto se corre cada hora sobre las
    últimas 48 h. Repetir una devolución ya guardada no cuesta nada: la
    escritura es idempotente.

    F1: con las mediaciones encendidas recorre `type=returns` Y
    `type=mediations` (`tipos` lo fuerza). Busca por fecha de CREACIÓN: lo que
    cambia después de la ventana lo refresca `refrescar_abiertas` (F3), y las
    mediaciones que ganan su devolución tarde las busca
    `barrer_mediaciones_amplio`.

    `omitir_guardados`: no relee los claims que ya tienen fila (esos los cubre
    el refresco de F3). Lo usan el barrido amplio y el script de recuperación.
    """
    if mediaciones is None:
        mediaciones = _mediaciones_encendidas()
    tipos = tuple(tipos) if tipos is not None else (
        ("returns", "mediations") if mediaciones else ("returns",))
    hoy = datetime.now(timezone.utc)
    hasta = hasta or hoy.strftime("%Y-%m-%d")
    desde = desde or (hoy - timedelta(days=dias or 2)).strftime("%Y-%m-%d")

    resumen: dict[str, Any] = {"desde": desde, "hasta": hasta, "tipos": list(tipos),
                               "cuentas": {}, "guardados": 0, "ignorados": 0,
                               "conservados": 0, "fallos": 0, "busquedas_incompletas": 0}
    sem = asyncio.Semaphore(concurrencia)

    # Con el cliente propio, cada GET va frenado a `DEVOLUCIONES_ML_RITMO`: el
    # semáforo acota corrutinas, no GET por segundo (ver `_ritmo`).
    propio = cli is None
    if propio:
        cli = _cliente(ritmo=_ritmo())
    try:
        for cuenta in cuentas:
            tok = await _token(cuenta, tokens)
            if not tok:
                resumen["cuentas"][cuenta] = {"error": "sin token"}
                continue
            cab = {"Authorization": f"Bearer {tok}"}
            claims: list[str] = []
            por_tipo: dict[str, int] = {}
            incompletas: list[str] = []
            for tipo in tipos:
                marcas: dict[str, Any] = {}
                encontrados = await _buscar(cli, cab, desde, hasta, tipo=tipo,
                                            marcas=marcas)
                if marcas.get("incompleta"):
                    incompletas.append(str(marcas["incompleta"]))
                por_tipo[tipo] = len(encontrados)
                for c in encontrados:
                    cid = str(c.get("id"))
                    if cid not in claims:
                        claims.append(cid)
            omitidos = 0
            if omitir_guardados and claims:
                ya = await asyncio.to_thread(_ya_guardados, cuenta, claims)
                omitidos = len(ya)
                claims = [cid for cid in claims if cid not in ya]

            async def _uno(cid: str, cuenta: str = cuenta) -> dict[str, Any]:
                async with sem:
                    return await sincronizar(cid, cuenta, detectado_via=detectado_via,
                                             cli=cli, tokens=tokens, guardar=guardar,
                                             mediaciones=mediaciones)

            res = await asyncio.gather(*(_uno(cid) for cid in claims))
            n = _contar(res)
            if incompletas:
                # Cada búsqueda cortada cuenta como un fallo: lo que no se leyó
                # no se puede dar por revisado.
                n["fallos"] += len(incompletas)
                n["motivos"] = (incompletas + n["motivos"])[:5]
            resumen["cuentas"][cuenta] = {"claims": len(claims), "por_tipo": por_tipo,
                                          "omitidos": omitidos,
                                          "busqueda_incompleta": incompletas, **n}
            for k in ("guardados", "ignorados", "conservados", "fallos"):
                resumen[k] += n[k]
            resumen["busquedas_incompletas"] += len(incompletas)
    finally:
        if propio:
            await cli.aclose()

    log.info("DEVOLUCIONES ML barrido %s→%s %s: %s guardadas, %s ignoradas, "
             "%s conservadas, %s fallos (%s búsquedas incompletas)", desde, hasta,
             "+".join(tipos), resumen["guardados"], resumen["ignorados"],
             resumen["conservados"], resumen["fallos"], resumen["busquedas_incompletas"])
    return resumen


# ── Mediaciones que ganan su devolución tarde (R2 de F1, 5-oct-2026) ────────
#
# Una mediación que llega sin devolución queda en 'esperar': no se guarda fila
# y, a propósito, no se memoriza nada. Si gana su devolución después del día 2
# y ese aviso del webhook se pierde (429, token vencido, deploy), nadie la
# vuelve a buscar: el barrido horario solo pide lo CREADO en 2 días y el
# refresco de F3 solo relee filas que ya existen. Con los `returns` no pasa: su
# fila existe desde el primer aviso.
#
# Esto lo cierra: una vez al día, las mediaciones creadas en los últimos
# `DEVOLUCIONES_ML_MEDIACIONES_AMPLIO_DIAS` que todavía NO tienen fila. Las que
# ya la tienen las cubre el refresco, así que no se releen. Costo: ~22
# mediaciones/día × N días, menos las ya guardadas, × 2 GET (claim y /returns)
# — con N = 21, unos 300 GET al día, a concurrencia 2. Nace APAGADO (0) y
# exige las mediaciones encendidas: sin ellas descartaría todo.
#
# Alternativa pendiente (D4 del SPEC, necesita el token): si `claims/search`
# acepta `range=last_updated:…`, el barrido horario podría pedir las
# mediaciones ACTUALIZADAS en 26 h y esto sobraría.

_AMPLIO_MAX_DIAS = 31   # una sola búsqueda; `paging.total` se vuelve dudoso en rangos largos


async def barrer_mediaciones_amplio() -> dict[str, Any]:
    """El job diario. Apagado si `DEVOLUCIONES_ML_ENABLED`,
    `DEVOLUCIONES_ML_MEDIACIONES` o `DEVOLUCIONES_ML_MEDIACIONES_AMPLIO_DIAS`
    no están encendidos."""
    dias = int(getattr(settings, "devoluciones_ml_mediaciones_amplio_dias", 0) or 0)
    if not (getattr(settings, "devoluciones_ml_enabled", False)
            and _mediaciones_encendidas() and dias > 0):
        return {"ok": False, "motivo": "apagado"}
    return await barrer(dias=min(dias, _AMPLIO_MAX_DIAS), tipos=("mediations",),
                        mediaciones=True, detectado_via="sondeo", concurrencia=2,
                        omitir_guardados=True)


# ── Refresco de lo que sigue abierto (F3, 5-oct-2026) ───────────────────────
#
# El barrido busca por fecha de CREACIÓN en los últimos 2 días. Una devolución
# abierta hace más tiempo solo se actualizaba si llegaba su webhook —y la F2
# hacía fallar justo esos avisos—: de 7 revisadas en vivo, 2 estaban viejas.
# Esto relee las devoluciones NO TERMINALES ya guardadas, la más olvidada
# primero, con tope por corrida y a ritmo bajo (secuencial, cada GET espaciado).

# El paquete o el dinero todavía se mueven.
_ABIERTOS = ("abierta", "en_transito", "recibida")
# Ya se reembolsó, pero la caja no ha llegado (`refund_at = shipped`: 84 en 90
# días). Bodega necesita saber cuándo llega aunque el dinero ya se haya soltado.
_EN_CAMINO = ("pending", "label_generated", "shipped", "in_transit")

# Lo que se intentó refrescar y NO se escribió (conservado, esperando, 404 o
# fallo) no mueve `actualizado_at`: sin esta memoria encabezaría la rotación en
# cada pasada y, con tantas como el tope, nada más se refrescaría nunca.
# Memoria del proceso; tras un reinicio, a lo más se repite una pasada.
_INTENTADOS: dict[tuple[str, str], float] = {}


def necesita_refresco(estado: str, estado_canal: str | None) -> bool:
    return estado in _ABIERTOS or (
        estado == "reembolsada" and (estado_canal or "") in _EN_CAMINO)


def _candidatas(tope: int | None, horas: int, max_dias: int, *,
                cuentas: Iterable[str] = CUENTAS,
                excluir: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Las devoluciones no terminales sin tocar en `horas`, abiertas hace a lo
    más `max_dias`, la más olvidada primero. `tope=None` = sin límite. La
    condición es la misma de `necesita_refresco` (mismas constantes)."""
    return sdb.fetch_all(
        """select cuenta, external_return_id
             from channel.returns
            where canal = %s
              and cuenta = any(%s)
              and (estado = any(%s)
                   or (estado = 'reembolsada' and coalesce(estado_canal, '') = any(%s)))
              and actualizado_at < now() - make_interval(hours => %s)
              and abierta_at >= now() - make_interval(days => %s)
              and not ((cuenta || ':' || external_return_id) = any(%s::text[]))
            order by actualizado_at
            limit %s""",
        (CANAL, list(cuentas), list(_ABIERTOS), list(_EN_CAMINO), int(horas),
         int(max_dias), list(excluir), tope))


def _excluir_intentados(horas: int) -> list[str]:
    if horas <= 0:
        return []
    limite = time.monotonic() - horas * 3600
    for k, t in list(_INTENTADOS.items()):
        if t < limite:
            del _INTENTADOS[k]
    return [f"{c}:{r}" for (c, r) in _INTENTADOS]


async def refrescar_abiertas(*, tope: int | None, horas: int = 6, max_dias: int = 120,
                             ritmo: float | None = None,
                             cuentas: Iterable[str] = CUENTAS,
                             cli: httpx.AsyncClient | None = None,
                             tokens: Mapping[str, str] | None = None,
                             guardar: Callable[[dict, list[dict]], None] | None = None
                             ) -> dict[str, Any]:
    """Relee con ML hasta `tope` devoluciones no terminales y las reescribe.

    Secuencial (concurrencia 1). Con el cliente propio, el transporte espacia
    CADA GET a lo más `ritmo` por segundo (`_Ritmo`): con tope 40 son ~120–160
    GET (claim, /returns, /detail y, si el código no está en caché, el motivo),
    ~1.5 min a 1.5 GET/s. `ritmo=None` toma `DEVOLUCIONES_ML_RITMO`. Con `cli`
    inyectado el ritmo lo pone quien lo inyecta (`revisar`, que comparte el suyo
    con el barrido; el script de recuperación, su transporte vigilado y `--ritmo`). Cada una pasa
    por `sincronizar` con sus candados: una lectura fallida de `/returns` no
    degrada una fila buena ('conservado').

    Las mediaciones se piden ENCENDIDAS: la fila ya existe, así que alguien ya
    decidió que era devolución; apagarlas aquí la descartaría al releerla.
    """
    excluir = _excluir_intentados(horas)
    filas = await asyncio.to_thread(_candidatas, tope, horas, max_dias,
                                    cuentas=tuple(cuentas), excluir=excluir)
    resumen: dict[str, Any] = {"candidatas": len(filas), "tope": tope}
    res: list[dict[str, Any]] = []
    propio = cli is None
    if propio and filas:
        cli = _cliente(ritmo=ritmo or _ritmo())
    try:
        for f in filas:
            cuenta, cid = str(f["cuenta"]), str(f["external_return_id"])
            r = await sincronizar(cid, cuenta, detectado_via="sondeo", cli=cli,
                                  tokens=tokens, guardar=guardar, mediaciones=True)
            if r.get("accion") != "guardado":
                _INTENTADOS[(cuenta, cid)] = time.monotonic()
            res.append(r)
    finally:
        if propio and filas:
            await cli.aclose()
    resumen.update(_contar(res))
    if filas:
        log.info("DEVOLUCIONES ML refresco: %s candidatas, %s guardadas, %s conservadas, "
                 "%s ignoradas, %s fallos", len(filas), resumen["guardados"],
                 resumen["conservados"], resumen["ignorados"], resumen["fallos"])
    return resumen


# ── La sexta falla: `venta_contaba` congelada (revisión del 7-oct) ───────────
#
# `armar` fija `venta_contaba` con el estado de la orden AL CAPTURAR. Pero ML
# cancela la orden cuando reembolsa una devolución, y una fila que ya nadie
# relee se queda con `true`: la vista la resta de las ventas aunque la orden ya
# salió de `sales_daily` por cancelada. Medido en el sandbox el 5-oct: $35.8k
# contados dos veces en «restable». Releer con ML no hace falta: el estado vivo
# de la orden ya está en `channel.orders`, que el flujo de pedidos mantiene al día.
#
# Un solo UPDATE, sin llamar a ML, con la MISMA lista de `_CANCELADOS` (la de la
# 0030; `test_cancelados_igual_que_la_0030` lo vigila) y solo donde el valor
# CAMBIA (`is distinct from`): en régimen son unas cuantas filas por hora.
#
# QUÉ HACEN LOS TRIGGERS DE LA 0049 CON ESTE UPDATE (revisado):
#   · `returns_history` solo escribe si cambia `estado`, y aquí no cambia: no
#     se mete historia falsa. Que una orden se cancele no es una transición de
#     la devolución, así que tampoco debe quedar como una (decisión a propósito).
#   · `returns_touch` no toca `abierta_at` (solo en INSERT y si viene NULL) ni
#     `reembolsada_at` (solo si es NULL con estado 'reembolsada', y el mismo
#     trigger ya lo llenó al escribirla). SÍ mueve `actualizado_at` a now(): la
#     fila recalculada se ve «recién tocada» y el refresco de F3 la pospone a
#     lo más `DEVOLUCIONES_ML_REFRESCO_HORAS`, una sola vez (solo se toca cuando
#     el valor cambia). Si le falta `wc_order_id`, lo enlaza: es lo correcto.
#   · Una fila sin orden (`venta_contaba` NULL) pasa a true/false en cuanto su
#     orden aparece en `channel.orders`: es el estado vivo, igual que lo habría
#     calculado `armar` de haber tenido la orden.
#
# CAMBIA CIFRAS QUE SE VEN (segunda revisión del 7-oct). Rentabilidad suma
# `venta_contaba` desde `returns_daily`: la primera pasada mueve el «restable»
# sin que nadie encienda nada. Por eso tiene interruptor PROPIO,
# `DEVOLUCIONES_ML_RECALCULO` (encendido por omisión: corrige un doble conteo),
# que lo apaga sin apagar el barrido. Y como ni la historia ni la fila guardan
# el valor de antes, el log lo deja: cada claim cambiado con antes → después.
_RECALCULO_LOG_MAX = 50    # claims que se nombran por pasada; el resto, solo cuenta


def _recalculo_encendido() -> bool:
    return bool(getattr(settings, "devoluciones_ml_recalculo", True))


def recalcular_venta_contaba() -> int:
    """Pone `venta_contaba` = estado vivo de su orden en `channel.orders`, solo
    en las filas de Mercado Libre donde cambia. Devuelve cuántas cambió y deja
    en el log cada claim con su valor de antes y el nuevo.
    Síncrona (psycopg2): desde una corrutina va en `asyncio.to_thread`."""
    with sdb.get_cursor() as cur:
        # El CTE lee el valor de ANTES (un `returning` solo ve el nuevo); la
        # orden se cruza por su llave completa, así que es a lo más una.
        cur.execute(
            """with cambio as (
                   select r.cuenta, r.external_return_id,
                          r.venta_contaba as antes,
                          not (lower(coalesce(o.estado_canal, '')) = any(%s)) as despues
                     from channel.returns r
                     join channel.orders o
                       on o.canal = r.canal
                      and o.cuenta = r.cuenta
                      and o.external_order_id = r.external_order_id
                    where r.canal = %s
                      and r.venta_contaba is distinct from
                          (not (lower(coalesce(o.estado_canal, '')) = any(%s)))
               )
               update channel.returns r
                  set venta_contaba = c.despues
                 from cambio c
                where r.canal = %s
                  and r.cuenta = c.cuenta
                  and r.external_return_id = c.external_return_id
               returning r.cuenta, r.external_return_id, c.antes, c.despues""",
            (list(_CANCELADOS), CANAL, list(_CANCELADOS), CANAL))
        filas = cur.fetchall() if cur.description else []
        n = cur.rowcount or 0
    if filas:
        trozos = [f"{f['cuenta']}/{f['external_return_id']}: {f['antes']}→{f['despues']}"
                  for f in filas[:_RECALCULO_LOG_MAX]]
        if len(filas) > _RECALCULO_LOG_MAX:
            trozos.append(f"… y {len(filas) - _RECALCULO_LOG_MAX} más")
        log.info("DEVOLUCIONES ML venta_contaba (antes→después) · %s", " · ".join(trozos))
    return n


# ── Avisos post_purchase que fallaron (revisión del 7-oct) ───────────────────
#
# Un aviso que falla (429, timeout, deploy a media lectura) queda en
# `ops.webhook_events` como procesado con `resultado = 'devolución N falló: …'`
# y NADIE lo reintenta: el reproceso de `ml_webhook_reintentos` es solo de
# ventas. Para un claim creado hace más de 48 h el barrido ya no lo ve (busca
# por fecha de CREACIÓN) y el refresco nace apagado: el cambio se pierde. Lo
# mismo con un aviso que se quedó sin procesar (el deploy mató la tarea).
#
# Esto relee de la bitácora SOLO el id del claim —nunca el payload, la misma
# regla del webhook— y lo pasa por `sincronizar`, que le pregunta todo a ML.
#
# NO se salta el claim cuya fila «se tocó después» del aviso: `actualizado_at`
# lo mueve también `recalcular_venta_contaba`, y el caso típico —ML reembolsa,
# el aviso falla, la orden se cancela, el recálculo toca la fila— quedaría
# tapado justo cuando más importa. Releer un claim que ya estaba bien cuesta 3
# GET; perder el reembolso, una cifra mal para siempre.
#
# ⚠ La bitácora de Mercado Libre se purga a los 3 días (0050): la ventana de 7
# es un techo; en la práctica se ven 3.
_AVISOS_DIAS = 7
_AVISOS_MIN_SIN_PROCESAR = 10      # menos que esto puede seguir en proceso
_AVISOS_MAX_INTENTOS = 3           # por aviso fallido, uno por pasada horaria
_AVISOS_MEMORIA_S = 8 * 24 * 3600  # más que la ventana: no hace falta recordar más

# user_id de ML → cuenta. Es el MISMO mapa de `routers/webhooks.py`
# (`_USER_A_CUENTA`), copiado para no importar el router desde un servicio;
# `test_mapa_de_cuentas_igual_que_el_webhook` vigila que no se separen. Lo
# desconocido cae en BEKURA, igual que en el webhook: así el reintento hace
# exactamente la llamada que hizo el aviso.
_USER_A_CUENTA = {"3072519654": "BEKURA", "3064478475": "SANCORFASHION"}

# (cuenta, claim) → (último evento reintentado, intentos, resuelto, cuándo).
# Memoria del proceso: sin ella, un claim que sigue fallando se reintentaría
# cada hora durante toda la ventana. Tras un reinicio, a lo más se repiten
# `_AVISOS_MAX_INTENTOS` intentos por aviso.
_REINTENTADOS: dict[tuple[str, str], tuple[int, int, bool, float]] = {}


def _avisos_fallidos(limite: int, dias: int = _AVISOS_DIAS,
                     minutos: int = _AVISOS_MIN_SIN_PROCESAR) -> list[dict[str, Any]]:
    """Claims de avisos post_purchase de ML que fallaron, o que siguen sin
    procesar después de `minutos`, en los últimos `dias`. Uno por (claim,
    user_id), el más reciente primero."""
    return sdb.fetch_all(
        """with fallidos as (
               select substring(e.external_id from '/claims/([0-9]+)') as claim_id,
                      e.cuenta as user_id, e.id
                 from ops.webhook_events e
                where e.canal = %s
                  and e.topic = 'post_purchase'
                  and e.env = %s
                  and e.recibido_at >= now() - make_interval(days => %s)
                  and ((e.procesado and (e.resultado like %s or e.resultado like %s))
                       or (not e.procesado
                           and e.recibido_at < now() - make_interval(mins => %s)))
           ), por_claim as (
               select claim_id, user_id, max(id) as ultimo_id
                 from fallidos
                where claim_id is not null
                group by claim_id, user_id
           )
           select p.claim_id, p.user_id, p.ultimo_id
             from por_claim p
            order by p.ultimo_id desc
            limit %s""",
        (CANAL, getattr(settings, "app_env", "prod"), int(dias),
         "devolución % falló%", "error:%", int(minutos), int(limite)))


def _tope_avisos() -> int:
    try:
        return max(0, int(getattr(settings, "devoluciones_ml_reintento_tope", 30) or 0))
    except (TypeError, ValueError):
        return 30


async def reintentar_avisos(*, tope: int | None = None,
                            cli: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Pasa por `sincronizar` hasta `tope` claims de avisos post_purchase
    fallidos (`DEVOLUCIONES_ML_REINTENTO_TOPE`, 30 por omisión; 0 = apagado).
    Secuencial: con el cliente de `revisar`, al ritmo de los barridos."""
    tope = _tope_avisos() if tope is None else max(0, int(tope))
    if tope <= 0:
        return {"ok": False, "motivo": "apagado"}
    ahora = time.monotonic()
    for k, (_, _, _, t) in list(_REINTENTADOS.items()):
        if ahora - t > _AVISOS_MEMORIA_S:
            del _REINTENTADOS[k]
    # Se piden de más: los que la memoria salta no deben comerse el tope.
    filas = await asyncio.to_thread(_avisos_fallidos, min(tope + len(_REINTENTADOS), 500))
    pendientes: list[tuple[str, str, int, int]] = []
    for f in filas:
        cuenta = _USER_A_CUENTA.get(str(f.get("user_id") or ""), "BEKURA")
        cid, ultimo = str(f["claim_id"]), int(f["ultimo_id"])
        previo = _REINTENTADOS.get((cuenta, cid))
        intentos = 0
        if previo and previo[0] >= ultimo:
            if previo[2] or previo[1] >= _AVISOS_MAX_INTENTOS:
                continue        # ya resuelto, o ya se le dieron sus intentos
            intentos = previo[1]
        pendientes.append((cuenta, cid, ultimo, intentos))
        if len(pendientes) >= tope:
            break
    res: list[dict[str, Any]] = []
    propio = cli is None and bool(pendientes)
    if propio:
        cli = _cliente(ritmo=_ritmo())
    try:
        for cuenta, cid, ultimo, intentos in pendientes:
            # 'webhook': lo detectó el aviso; este job solo lo repite.
            r = await sincronizar(cid, cuenta, detectado_via="webhook", cli=cli)
            _REINTENTADOS[(cuenta, cid)] = (ultimo, intentos + 1, bool(r.get("ok")),
                                            time.monotonic())
            res.append(r)
    finally:
        if propio:
            await cli.aclose()
    resumen: dict[str, Any] = {"candidatos": len(filas), "reintentados": len(pendientes),
                               "tope": tope, **_contar(res)}
    if pendientes:
        log.info("DEVOLUCIONES ML avisos fallidos: %s reintentados, %s guardados, "
                 "%s ignorados, %s fallos", len(pendientes), resumen["guardados"],
                 resumen["ignorados"], resumen["fallos"])
    return resumen


async def revisar() -> dict[str, Any]:
    """El job del scheduler. Apagable con `DEVOLUCIONES_ML_ENABLED=false`.

    En orden, y cada paso aunque el anterior truene (una excepción del barrido
    ya no se salta el resto de la hora):
      1. barre lo creado en la ventana reciente;
      2. si `DEVOLUCIONES_ML_REFRESCO_TOPE` > 0, refresca lo que sigue abierto (F3);
      3. reintenta los avisos post_purchase que fallaron;
      4. recalcula `venta_contaba` contra el estado vivo de las órdenes (sin ML;
         apagable solo con `DEVOLUCIONES_ML_RECALCULO=false`).
    Los tres primeros comparten UN cliente frenado a `DEVOLUCIONES_ML_RITMO`.
    """
    if not getattr(settings, "devoluciones_ml_enabled", False):
        return {"ok": False, "motivo": "apagado"}
    dias = getattr(settings, "devoluciones_ml_dias", 2)
    resumen: dict[str, Any] = {}
    cli = _cliente(ritmo=_ritmo())
    try:
        try:
            resumen = await barrer(dias=dias, detectado_via="sondeo", cli=cli)
        except Exception as exc:  # noqa: BLE001
            log.exception("DEVOLUCIONES ML: el barrido falló; la hora sigue")
            resumen = {"ok": False, "motivo": f"barrido: {str(exc)[:200]}"}
        tope = int(getattr(settings, "devoluciones_ml_refresco_tope", 0) or 0)
        if tope > 0:
            try:
                resumen["refresco"] = await refrescar_abiertas(
                    tope=tope,
                    horas=int(getattr(settings, "devoluciones_ml_refresco_horas", 6)),
                    max_dias=int(getattr(settings, "devoluciones_ml_refresco_dias", 120)),
                    cli=cli)
            except Exception as exc:  # noqa: BLE001
                log.exception("DEVOLUCIONES ML: el refresco falló")
                resumen["refresco"] = {"ok": False, "motivo": str(exc)[:200]}
        try:
            resumen["avisos"] = await reintentar_avisos(cli=cli)
        except Exception as exc:  # noqa: BLE001
            log.exception("DEVOLUCIONES ML: el reintento de avisos falló")
            resumen["avisos"] = {"ok": False, "motivo": str(exc)[:200]}
    finally:
        await cli.aclose()
    if not _recalculo_encendido():
        resumen["venta_contaba"] = {"ok": False, "motivo": "apagado"}
        return resumen
    try:
        n = await asyncio.to_thread(recalcular_venta_contaba)
        resumen["venta_contaba"] = n
        if n:
            log.info("DEVOLUCIONES ML venta_contaba recalculada en %s fila(s) "
                     "(la orden cambió de estado)", n)
    except Exception as exc:  # noqa: BLE001
        log.exception("DEVOLUCIONES ML: el recálculo de venta_contaba falló")
        resumen["venta_contaba"] = {"ok": False, "motivo": str(exc)[:200]}
    return resumen


# ── La hora del barrido amplio (revisión del 7-oct) ─────────────────────────

def hora_amplio_utc(texto: Any, defecto: tuple[int, int] = (10, 20)) -> tuple[int, int]:
    """'HH:MM' de `DEVOLUCIONES_ML_MEDIACIONES_AMPLIO_HORA_UTC` → (hh, mm).

    Un valor ilegible o fuera de rango ('25:00', '10:75') NO puede tumbar el
    arranque del backend: hasta v0.625.0 `add_job` lanzaba dentro de
    `scheduler.iniciar` y se llevaba todos los jobs registrados después. Se
    registra el error y se usa la hora por omisión."""
    try:
        hh, mm = (int(x) for x in str(texto).strip().split(":"))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm
    except (TypeError, ValueError):
        pass
    log.error("DEVOLUCIONES_ML_MEDIACIONES_AMPLIO_HORA_UTC=%r no es una hora HH:MM "
              "válida; se usa %02d:%02d", texto, *defecto)
    return defecto


def programar_barrido_amplio(scheduler: Any) -> bool:
    """Da de alta el barrido amplio diario en `scheduler` si toca
    (`DEVOLUCIONES_ML_MEDIACIONES` y `..._AMPLIO_DIAS` > 0). Devuelve si quedó
    programado. NUNCA lanza: lo llama `scheduler.iniciar` a media lista de
    jobs, y una excepción aquí se llevaba todos los que se registran después."""
    dias = int(getattr(settings, "devoluciones_ml_mediaciones_amplio_dias", 0) or 0)
    if not (_mediaciones_encendidas() and dias > 0):
        return False
    hh, mm = hora_amplio_utc(getattr(settings, "devoluciones_ml_mediaciones_amplio_hora_utc",
                                     "10:20"))
    try:
        scheduler.add_job(barrer_mediaciones_amplio, "cron", hour=hh, minute=mm,
                          id="devoluciones_ml_mediaciones_amplio",
                          max_instances=1, coalesce=True)
    except Exception as exc:  # noqa: BLE001
        log.error("Devoluciones ML: no se pudo programar el barrido amplio (%s); "
                  "el resto del scheduler sigue", exc)
        return False
    log.info("Devoluciones ML: mediaciones sin fila de los últimos %s días, "
             "diario a las %02d:%02d UTC.", min(dias, 31), hh, mm)
    return True
