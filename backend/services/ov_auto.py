"""
ov_auto.py — El BARRIDO DE CANCELACIONES DEL CANAL de las órdenes de venta propias
(contrato: docs/MIGRACION_0064_0065_GUIA_AGENTE.md §4.9 f y g).

UN SOLO TRABAJO. Una orden propia ligada a una venta de marketplace (`mp_canal`,
`mp_cuenta`, `mp_orden`) sigue «viva» aunque el comprador ya haya cancelado:
nadie se lo dice. Aquí se cruza cada orden viva con su venta y, si el canal la
canceló, se le avisa al servicio —`ordenes_venta.canal_cancelo(...)`—, que es
quien decide y escribe:

  · confirmada y el paquete NO ha salido  → `cancelada` (suelta el apartado);
  · confirmada y el canal dice que ya va EN CAMINO → la marca `canal_cancelo`:
    la orden se queda esperando que Bodega conteste «¿salió?» (no se suelta nada);
  · entregada (o confirmada con piezas ya afuera) → `entregada_cancelada`, con
    la devolución pendiente;
  · borrador ligado a esa venta → se cancela (`origen = marketplace`): no
    aparta nada, pero dejarlo vivo es invitar a confirmar una venta muerta.

LO QUE YA NO HACE (y por qué no hay que «devolvérselo»). Antes este módulo
también generaba órdenes solas, rellenaba guía e importes y movía su propio
interruptor. Con la 0064 el barrido no rellena nada: el contenido de una
confirmada sólo lo corrige una persona, por la edición de la 0071 (con su
rastro en la bitácora); la generación automática es `crear_auto` —la
llama el planeador, que es otra tarea— y las banderas son filas que enciende un
ACTA, no la pantalla.

QUÉ TOCA, Y ES TODO LO QUE TOCA
  · LEE `ventas.ov_ordenes`, `channel.orders` y la bitácora de Automatización
    (`ops.odoo_sale_orders`, que puede NO existir en un ambiente: se sigue sin
    ella y se dice una vez).
  · ESCRIBE sólo por medio de `ordenes_venta.*`, firmado como `AUTOMATICO`.
    Aquí no hay ni un INSERT ni un UPDATE.
  · No habla con Odoo, WooCommerce ni ningún marketplace, y no toca
    `pedidos_ml`, `odoo_ventas`, `temu_*`, `tiktok*` ni ningún flujo vivo.

POR QUÉ SE MIRA TAMBIÉN LA BITÁCORA. Temu NO actualiza `channel.orders` cuando
cancela: la venta se queda con el estado con el que entró (2 pagada, 4 enviada,
5 entregada). Quien sí se entera es el vigilante de cancelaciones de
Automatización, que lo deja en `ops.odoo_sale_orders.accion`. Sin esa segunda
mirada las cancelaciones de Temu no se verían nunca.

«EN CAMINO» NO SE ADIVINA: son dos señales, y basta una.
  · La bitácora dice `cancelada_revisar` (la entrega ya salió del almacén) o
    `cancelada_devuelta` (salió y ya regresó).
  · El canal reporta un estado de envío o de entrega: IN_TRANSIT,
    AWAITING_COLLECTION, SHIPPED, DELIVERED o COMPLETED, y en Temu 4 (enviada)
    o 5 (entregada). «Entregada» cuenta: si el canal la vio llegar, salió.
Con cualquiera de las dos NO se cancela sola: soltar el apartado de una caja que
ya va en un camión es vender dos veces la misma pieza. Decide Bodega.

LA REGLA DE «CANCELADA» ES UNA SOLA: `ordenes_venta.cancelada_en_canal`, la misma
que pinta la pantalla. Se aplica en Python sobre lo leído (no hay una gemela en
SQL que se pueda desfasar): así el barrido y la pantalla nunca opinan distinto.

ES IDEMPOTENTE Y SE PUEDE REPETIR. El sondeo y el webhook de cada canal repiten
la misma cancelación; `canal_cancelo` no pide `rev` a quien llama (relee la
orden y su compare-and-set va por estado y por la `rev` de ESA relectura) y
contesta `ya_marcada` / `ya_cancelada` sin escribir. Una orden que alguien movió
entre la lectura y la escritura va en la siguiente pasada.

LA LIGA VIAJA CON EL AVISO. La lectura de aquí envejece: desde la 0071 una
persona puede corregir la venta a la que está ligada una confirmada (o
desligarla) después de que esta pasada la leyó. Por eso a `canal_cancelo` se le
pasa la liga CON LA QUE se decidió (`venta`): si la orden ya no es de esa venta,
no la toca. Sin eso se cancelaba —sin vuelta atrás— la orden de una venta viva.

LA BANDERA SE PREGUNTA EN CADA PASADA (`ordenes_venta.habilitado()`, una fila de
`ops.automatizacion_flags` con caché de 30 s): apagarla detiene el barrido sin
reiniciar el contenedor (regla 12). Apagada, o sin las migraciones 0064/0065,
`revisar()` contesta `ok: False` con su motivo y NO escribe una línea de log
por pasada.

TODO ES SÍNCRONO (psycopg2 bloquea): el scheduler y el router lo llaman con
`asyncio.to_thread` (regla 11). Y `revisar()` NUNCA lanza: un job que revienta
deja una traza de APScheduler que nadie busca.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from services import ordenes_venta
from services import supabase_db as sdb
from services.odoo_ventas_log import ACCION_CANCELADA_REVISAR, ACCIONES_CANCELADA_CANAL
from services.ordenes_venta import AUTOMATICO

log = logging.getLogger("omnicanal.ov_auto")

# Una orden ENTREGADA se sigue vigilando por si el canal cancela después (queda
# `entregada_cancelada` y pide devolución)… pero no para siempre: sin tope, cada
# pasada cruzaría TODAS las entregadas de la historia contra channel.orders y el
# costo crecería con los meses. Pasados estos días una cancelación tardía ya es
# una devolución que se atiende por su propio proceso.
_DIAS_ENTREGADA_VIVA = 45

# Los estados con los que el canal dice «el paquete ya salió». Se comparan en
# mayúsculas (TikTok los manda así; `shipped` llega en minúsculas de otros).
# Temu usa números y SÓLO valen para Temu: un «4» de otro canal no significa nada.
# DELIVERED y COMPLETED también: una venta que el canal vio entregada y después
# canceló SALIÓ de la bodega aunque aquí nadie haya marcado la entrega; soltar su
# apartado sin preguntar sería ofrecer otra vez una pieza que ya no está.
_ESTADOS_EN_CAMINO = frozenset({"IN_TRANSIT", "AWAITING_COLLECTION", "SHIPPED",
                                "DELIVERED", "COMPLETED"})
# Las acciones de la bitácora de Automatización que dicen lo mismo.
_ACCIONES_SALIO = frozenset({ACCION_CANCELADA_REVISAR, "cancelada_devuelta"})
_TEMU_EN_CAMINO = frozenset({"4", "5"})

# Cómo se le dice a cada canal. ⚠️ GEMELA de `CANALES` en
# frontend/components/ordenes/ui.tsx (los que llevan `mp: true`).
_ROTULO_CANAL = {"temu": "Temu", "tiktok": "TikTok", "mercado_libre": "Mercado Libre",
                 "amazon": "Amazon", "walmart": "Walmart", "shein": "Shein"}

_MSG_SIN_VENTAS = "Esta base no tiene las tablas de ventas del canal (channel.orders)."
_MSG_OCUPADO = "Otra revisión sigue corriendo; intenta de nuevo en un momento."
_MSG_FALLO = "No se pudo completar la revisión; revisa los logs del backend."


def _rotulo(canal: Any) -> str:
    c = str(canal or "").strip().lower()
    return _ROTULO_CANAL.get(c, c or "marketplace")


# ══════════════════════════════════════════════════════════════════════════════
# La lectura: cada orden viva con su venta en el canal y su fila de la bitácora
# ══════════════════════════════════════════════════════════════════════════════

# VIVAS = lo que una cancelación del canal todavía puede mover:
#   · borradores ligados a una venta;
#   · confirmadas SIN la marca (con la marca ya esperan a Bodega: el barrido no
#     tiene nada más que decirles, y traerlas cada 3 minutos sería releer en balde);
#   · entregadas de los últimos `_DIAS_ENTREGADA_VIVA` días.
# `mp_*` va todo o nada y con su forma fija (canal en minúsculas, cuenta en
# MAYÚSCULAS: ov_ordenes_mp_chk y ov_ordenes_mp_forma_chk), así que la cuenta
# SIEMPRE se compara: cancelar por la venta del mismo número en OTRA cuenta
# soltaría un apartado que sí hacía falta.
_SQL_VIVAS = """
with vivas as (
    select o.id, o.folio, o.estado, o.rev, o.mp_canal, o.mp_cuenta, o.mp_orden
      from ventas.ov_ordenes o
     where o.borrada_at is null and o.mp_orden is not null
       and (o.estado = 'borrador'
            or (o.estado = 'confirmada' and o.canal_cancelo_at is null)
            or (o.estado = 'entregada'
                and o.entregada_at > now() - make_interval(days => %(dias)s)))
)"""

# Con LEFT JOIN y sin filtrar por «cancelada» en SQL a propósito: la regla vive
# en `ordenes_venta.cancelada_en_canal` y se aplica abajo, en Python. Sólo se
# descartan aquí las órdenes de las que no hay NADA que mirar (ni venta en el
# canal ni fila en la bitácora).
_SQL_CON_BITACORA = _SQL_VIVAS + """
select v.id, v.folio, v.estado, v.rev, v.mp_canal, v.mp_cuenta, v.mp_orden,
       c.external_order_id is not null as en_canal, c.estado_canal, c.estado_wc,
       a.accion, a.motivo as motivo_bitacora
  from vivas v
  left join channel.orders c
    on c.canal = v.mp_canal and c.external_order_id = v.mp_orden
   and upper(c.cuenta) = v.mp_cuenta
  left join ops.odoo_sale_orders a
    on a.canal = v.mp_canal and a.external_order_id = v.mp_orden
   and upper(a.cuenta) = v.mp_cuenta
 where c.external_order_id is not null or a.external_order_id is not null
 order by v.id
"""

_SQL_SIN_BITACORA = _SQL_VIVAS + """
select v.id, v.folio, v.estado, v.rev, v.mp_canal, v.mp_cuenta, v.mp_orden,
       true as en_canal, c.estado_canal, c.estado_wc,
       null::text as accion, null::text as motivo_bitacora
  from vivas v
  join channel.orders c
    on c.canal = v.mp_canal and c.external_order_id = v.mp_orden
   and upper(c.cuenta) = v.mp_cuenta
 order by v.id
"""


def _sin_tabla(exc: BaseException) -> bool:
    """42P01 = tabla que no existe; 42703 = columna que no existe. Por CÓDIGO,
    nunca por el texto (un `savepoint … does not exist` también dice «does not exist»)."""
    return getattr(exc, "pgcode", None) in ("42P01", "42703")


_bitacora_avisada = False


def _leer() -> list[dict[str, Any]]:
    """Las órdenes vivas con lo que el canal y la bitácora dicen de su venta.
    SÓLO LEE. Si la bitácora de Automatización no existe en el ambiente (o tiene
    su forma vieja, sin `cuenta`) se sigue sin ella; si sin ella también falta
    algo, lo que falta son las tablas de ventas del canal."""
    global _bitacora_avisada
    dias = {"dias": _DIAS_ENTREGADA_VIVA}
    try:
        return sdb.fetch_all(_SQL_CON_BITACORA, dias)
    except Exception as exc:  # noqa: BLE001 — se clasifica abajo
        if not _sin_tabla(exc):
            raise
    try:
        filas = sdb.fetch_all(_SQL_SIN_BITACORA, dias)
    except Exception as exc:  # noqa: BLE001
        if _sin_tabla(exc):
            raise ordenes_venta.FaltaMigracion(_MSG_SIN_VENTAS) from exc
        raise
    # Sin la bitácora las cancelaciones de TEMU no se ven (es su única señal) ni
    # se sabe si una entrega ya salió. Se dice UNA vez por proceso, y en WARNING.
    (log.debug if _bitacora_avisada else log.warning)(
        "ov_auto: este ambiente no tiene la bitácora de Automatización "
        "(ops.odoo_sale_orders); se concilia sólo con channel.orders y las "
        "cancelaciones de Temu NO se ven")
    _bitacora_avisada = True
    return filas


# ══════════════════════════════════════════════════════════════════════════════
# El veredicto (puro: no toca la base)
# ══════════════════════════════════════════════════════════════════════════════

def _envio(canal: Any, estado_canal: Any) -> bool:
    """¿Ese estado del canal dice que el paquete ya salió?"""
    e = str(estado_canal or "").strip()
    if not e:
        return False
    return e.upper() in _ESTADOS_EN_CAMINO or (
        str(canal or "").strip().lower() == "temu" and e in _TEMU_EN_CAMINO)


def _recorte(texto: str, tope: int) -> str:
    return texto if len(texto) <= tope else texto[:tope - 1] + "…"


def _motivo_cancelacion(canal: Any, orden: Any, extra: Any = None) -> str:
    """«Venta cancelada en Temu (PO-…)», más lo que diga Automatización. Sin
    datos del comprador: el canal, el número de la venta y el texto que la
    propia bitácora escribió."""
    texto = f"Venta cancelada en {_rotulo(canal)} ({orden})"
    extra = str(extra or "").strip()
    if extra:
        texto += f" · Automatización: {extra}"
    return _recorte(texto, ordenes_venta.MAX_MOTIVO)      # más largo, el servicio lo rechaza


def _veredictos(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Una entrada por ORDEN cuya venta el canal canceló:
    `{id, folio, estado, rev, mp_canal, mp_cuenta, mp_orden, en_camino,
    ref_canal, motivo}`. PURA.

    · CANCELADA: lo dice channel.orders o lo dice la bitácora. Si la lectura
      trajo más de una fila de un lado (dos cuentas que sólo difieren en
      mayúsculas), cuenta sólo cuando lo dicen TODAS las de ese lado: cancelar
      por la venta equivocada suelta un apartado que sí hacía falta.
    · EN CAMINO: `cancelada_revisar` en la bitácora, o un estado de envío en el
      canal. Basta una señal, y en la duda gana «en camino» (se pregunta a Bodega).
    · `ref_canal`: CON QUÉ se supo —es lo que queda escrito en la marca—. Si va
      en camino, el estado de envío del canal (o `cancelada_revisar` si la señal
      fue la bitácora). Si no, el estado del canal cuando es él quien dice
      «cancelada», y la acción de la bitácora cuando lo dice ella (en Temu el
      canal se queda en «2», que no explica nada)."""
    por_orden: dict[int, dict[str, Any]] = {}
    for f in filas:
        g = por_orden.setdefault(f["id"], {
            **{k: f[k] for k in ("id", "folio", "estado", "rev", "mp_canal", "mp_cuenta",
                                 "mp_orden")},
            "canal": [], "bitacora": []})
        if f.get("en_canal"):
            par = (f.get("estado_canal"), f.get("estado_wc"))
            if par not in g["canal"]:
                g["canal"].append(par)
        if f.get("accion") is not None:
            par = (f["accion"], f.get("motivo_bitacora"))
            if par not in g["bitacora"]:
                g["bitacora"].append(par)

    salida: list[dict[str, Any]] = []
    for g in por_orden.values():
        canal = g["mp_canal"]
        por_canal = bool(g["canal"]) and all(
            ordenes_venta.cancelada_en_canal(canal, ec, ew) for ec, ew in g["canal"])
        por_bitacora = bool(g["bitacora"]) and all(
            accion in ACCIONES_CANCELADA_CANAL for accion, _ in g["bitacora"])
        if not (por_canal or por_bitacora):
            continue
        estados = [str(ec).strip() for ec, _ in g["canal"] if str(ec or "").strip()]
        acciones = [str(a) for a, _ in g["bitacora"] if a in ACCIONES_CANCELADA_CANAL]
        envios = [e for e in estados if _envio(canal, e)]
        salieron = [a for a in acciones if a in _ACCIONES_SALIO]
        salio = bool(salieron)
        if envios:
            ref = envios[0]
        elif salio:
            ref = salieron[0]
        elif por_canal:
            ref = estados[0] if estados else ""      # sin estado del canal: lo dijo estado_wc
        else:
            ref = acciones[0]
        extra = next((m for a, m in g["bitacora"]
                      if a in ACCIONES_CANCELADA_CANAL and str(m or "").strip()), None)
        salida.append({
            **{k: g[k] for k in ("id", "folio", "estado", "rev", "mp_canal", "mp_cuenta",
                                 "mp_orden")},
            "en_camino": bool(envios) or salio, "ref_canal": ref,
            "motivo": _motivo_cancelacion(canal, g["mp_orden"], extra)})
    return salida


# ══════════════════════════════════════════════════════════════════════════════
# La pasada
# ══════════════════════════════════════════════════════════════════════════════

def _conciliar(canceladas: list[dict[str, Any]], marcadas: list[dict[str, Any]]) -> None:
    """UNA pasada. Va LLENANDO las dos listas que recibe (no las devuelve): si
    kubera se cae a media pasada, lo que ya se movió no se pierde del informe —ni
    del aviso al chat—. Sólo lanza si la LECTURA falla o si kubera se cae; el
    tropiezo de una orden no detiene a las demás."""
    for v in _veredictos(_leer()):
        venta = f"{v['mp_canal']} {v['mp_orden']}"
        try:
            if v["estado"] == "borrador":
                # Un borrador no aparta nada y `canal_cancelo` no lo toca: se
                # cancela como cualquier borrador, con su `rev` (si alguien lo
                # está editando gana quien llegó primero; el otro, a la siguiente).
                o = ordenes_venta.cancelar(v["id"], v["rev"], AUTOMATICO, motivo=v["motivo"],
                                           origen="marketplace")["orden"]
                resultado = "cancelada"
            else:
                # Con la liga que se LEYÓ: el veredicto es de esa venta. Si entre
                # la lectura y este aviso alguien la corrigió (0071), el servicio
                # contesta `nada` y la orden —que ya es de otra venta— no se toca.
                r = ordenes_venta.canal_cancelo(
                    v["id"], v["ref_canal"], v["motivo"], v["en_camino"],
                    venta=(v["mp_canal"], v["mp_cuenta"], v["mp_orden"]))
                o, resultado = r["orden"], r["resultado"]
        except ordenes_venta.Conflicto:
            log.info("ov_auto: %s cambió mientras se revisaba; va en la siguiente pasada",
                     v["folio"])
            continue
        except ordenes_venta.SinBase:
            raise                     # kubera se cayó a media pasada: no se insiste orden por orden
        except Exception as exc:  # noqa: BLE001 — una orden no detiene a las demás
            # De un `ErrorOV` sale su mensaje (es texto nuestro, en español); de
            # cualquier otra cosa sólo la clase: el texto de un error de la base
            # trae host, usuario y valores.
            detalle = str(exc) if isinstance(exc, ordenes_venta.ErrorOV) else type(exc).__name__
            log.warning("ov_auto: no se pudo aplicar la cancelación del canal a %s (venta %s): %s",
                        v["folio"], venta, detalle)
            continue
        if resultado in ("cancelada", "entregada_cancelada"):
            canceladas.append({"id": o["id"], "folio": o["folio"], "estado": o["estado"]})
            log.warning("ov_auto: %s → %s (el canal canceló la venta %s)", o["folio"],
                        o["estado"], venta)
        elif resultado == "marcada":
            marcadas.append({"id": o["id"], "folio": o["folio"]})
            log.warning("ov_auto: %s espera el «¿salió?» de Bodega (el canal canceló la venta "
                        "%s con el paquete en camino: %s)", o["folio"], venta, v["ref_canal"])
        # ya_marcada / ya_cancelada / nada: otro llegó antes (el webhook, una
        # persona), o la orden ya no es de esa venta. No hay nada que informar
        # ni que avisar.


# El job (cada 3 min) y el botón «Revisar cancelaciones» pueden coincidir. La
# pasada es idempotente, así que juntas no hacen daño; el candado sólo evita el
# trabajo doble y los 409 de pelearse por la misma orden.
_candado = threading.Lock()
_ESPERA_CANDADO_S = 10.0
_migracion_avisada = False


def revisar() -> dict[str, Any]:
    """La pasada. Devuelve `RespConciliar` (tipos.ts):
    `{ok, motivo?, canceladas: [{id, folio, estado}], marcadas: [{id, folio}]}`.

    NUNCA lanza, y lo que no corre lo dice en `motivo` (un texto fijo: el
    detalle de un error se queda en el log):
      · la bandera `ordenes_venta` apagada → `ok: False`, sin una línea de log;
      · sin las migraciones 0064/0065 → `ok: False`; se dice en el log UNA vez;
      · kubera caída → `ok: False`; una línea por minuto, no un traceback por pasada."""
    global _migracion_avisada
    resp: dict[str, Any] = {"ok": True, "canceladas": [], "marcadas": []}

    def no(motivo: str) -> dict[str, Any]:
        return {**resp, "ok": False, "motivo": motivo}

    try:
        if not ordenes_venta.habilitado():
            # Una bandera que no se pudo LEER también vale apagada (falla
            # cerrado), pero el motivo no es el mismo y la pantalla lo enseña.
            return no(str(ordenes_venta.SinBase()) if ordenes_venta.en_pausa()
                      else str(ordenes_venta.Apagado()))
        if not ordenes_venta.tablas_listas():
            if ordenes_venta.en_pausa():
                return no(str(ordenes_venta.SinBase()))
            raise ordenes_venta.FaltaMigracion()
        if not _candado.acquire(timeout=_ESPERA_CANDADO_S):
            return no(_MSG_OCUPADO)
        try:
            _conciliar(resp["canceladas"], resp["marcadas"])
        finally:
            _candado.release()
        _migracion_avisada = False
    except ordenes_venta.FaltaMigracion as exc:
        resp.update(ok=False, motivo=str(exc))
        (log.debug if _migracion_avisada else log.warning)("ov_auto: %s", exc)
        _migracion_avisada = True
    except ordenes_venta.SinBase as exc:       # el servicio ya dejó su línea en el log
        resp.update(ok=False, motivo=str(exc))
    except Exception as exc:  # noqa: BLE001 — «nunca lanza» es el contrato
        if ordenes_venta.es_caida(exc):
            # kubera no contesta: una línea (una por minuto), no un traceback
            # cada 3 minutos mientras dure la caída.
            ordenes_venta.anotar_caida(exc, "barrido")
            resp.update(ok=False, motivo=str(ordenes_venta.SinBase()))
        else:
            log.exception("ov_auto: la pasada falló")
            resp.update(ok=False, motivo=_MSG_FALLO)
    return resp
