"""
fanout_seguro.py — Seguro «stock 0 ⇒ fuera de la venta» en Temu y TikTok.

POR QUÉ EXISTE (medido el 7-oct-2026 en `ops.fanout_log`, SKU ACC-0574-LIL). El
1-oct a las 22:13 el fan-out escribió 0 a Temu y a TikTok y los dos contestaron
«ok». Aun así Temu aceptó ventas de 23, 10 y 1 piezas (2, 3 y 5-oct) con nuestro
stock en 0, y los excedentes (v0.618) la encontraron ofreciendo 14 y luego 10
piezas después de haberle escrito 0. Escribir 0 no basta: Temu pasa sola la
publicación a «agotada» (`3/1`) y la regresa sola a la venta (`2/8`) en cuanto su
contador sube. En 30 días: 15 ventas de Temu con Woo en 0.

QUÉ HACE. Cuando el objetivo del fan-out para un SKU es 0, SACA la publicación de
la venta en Temu y en TikTok —y lo hace ANTES de escribir el 0—. Cuando el stock
vuelve y se sostiene, la regresa, pero SÓLO si la apagó este seguro.

LAS REGLAS, CADA UNA CON SU PORQUÉ
  · SE DECIDE CON EL ESTADO VIVO, nunca con el censo: el censo pasa cada ~60 min
    y Temu cambia de estado sola. El censo sólo sirve de prefiltro sin red.
  · PRIMERO SE APAGA Y LUEGO SE ESCRIBE EL 0. Al revés, Temu la pasa sola a `3/1`
    y no sabemos si su endpoint acepta ese estado. El 0 se escribe SIEMPRE, falle
    o no el seguro (lo escribe el fan-out de siempre, no este módulo).
  · SÓLO SE REACTIVA LO PROPIO. Hoy hay 302 publicaciones de Temu en `3/2` y 273
    de TikTok en SELLER_DEACTIVATED apagadas a mano: un «hay stock ⇒ prender»
    ciego las encendería todas. La MARCA es una fila en `ops.fanout_log`
    (`cero_inactivar` real y con «ok»); sin marca legible, o si alguien más movió
    el estado después, no se reactiva. El «no sé» cae del lado seguro.
  · PRIMERO EL STOCK, LUEGO LA VENTA al reactivar, y sólo cuando el objetivo lleva
    FANOUT_CERO_ESPERA_MIN sostenido arriba de 0 (anti-parpadeo).
  · UNA VARIANTE HERMANA CON STOCK NO SE APAGA. La baja de TikTok es por producto
    completo: con más de una variante sólo se apaga si TODAS están en 0.
  · NUNCA UN SKU PADRE, ni FULL, ni lo que el fan-out ya descarta.
  · TOPES por vuelta, por hora (eventos sueltos) y por día, que cuentan LLAMADAS
    al canal y no éxitos: antes de cada llamada se sella una fila `cero_intento`,
    y sin poder sellarla no se llama. Llegar al tope del día avisa en rojo.
  · NADA SE QUEDA APAGADO EN SILENCIO: lo apagado que ya tiene stock, lo que el
    seguro soltó y sigue fuera de la venta, y lo que quedó a medias por un
    reinicio, se listan en `/api/fanout/seguro` y salen en un aviso.
  · A QUIÉN SE LE APLICA lo dice FANOUT_CERO_SOLO_SKUS: vacío = a nadie (ensayo
    forzado), una lista = el canario, `*` = todo el catálogo.

NACE APAGADO Y EN ENSAYO (regla 3 de CLAUDE.md). Con FANOUT_CERO_ENABLED apagado
este módulo no lee ni anota nada y el fan-out se comporta igual que antes. Con
FANOUT_CERO_ENSAYO encendido lee en vivo y anota lo que haría (`dry_run=true`),
sin apagar ni prender. Además cae en ENSAYO FORZADO si el fan-out no escribe de
verdad en ese canal o si la bitácora no se escribe en kubera (sin bitácora no hay
marca).

LA BITÁCORA (`ops.fanout_log`, sin migración; `accion` es texto libre):
  cero_inactivar   ok (2/8→3/2) · ENSAYO (apagaría; vivo 2/8, ofrece 2)
  cero_reactivar   ok (3/2→2/8) · ENSAYO (…)
  cero_sin_cambio  ya estaba fuera (marca vigente) · esperando stock · espera N min
  cero_omitir      padre · N variantes · excluido · tope · 3/1 sin sondear · 3 errores
  cero_soltar      ok (la prendió otro) · ok (estado X ajeno) · ok (manual)
  cero_error       ERROR: …
  cero_intento     apagar (vivo 2/8) · prender (vivo 3/2)   ← ANTES de llamar al canal
`dry_run` = ensayo, `stock_canal` = stock vivo leído, `item_id` = goodsId o
product_id. Las filas llevan el MISMO `ts` del evento del fan-out que las produjo,
para que el panel las junte. NO pasan por `fanout_stock._persistir` ni por
`fanout_read.espejar` (que se tragan los errores): se insertan directo y
verificadas (`sellar`), porque de una de ellas depende la reactivación.

REGLA 11. Todo aquí es SÍNCRONO y sólo corre en el hilo `fanout-stock`, en los
hilos de los censos (`asyncio.to_thread`) o en el threadpool de FastAPI. Nada de
esto se llama directo desde una corrutina.

⚠️ POR SONDEAR EN VIVO. Al 7-oct-2026 nadie ha llamado desde el proyecto a
`bg.local.goods.sale.status.set` (Temu) ni a `/products/deactivate|activate`
(TikTok). Qué estado dejan, si el token de Temu tiene permiso y si reactivar pasa
por revisión se confirma con el canario: `POST /api/fanout/seguro/aplicar`.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.fanout_seguro")

CANALES = ("temu", "tiktok")
NOMBRE = {"temu": "Temu", "tiktok": "TikTok"}
# Cómo llaman los pedidos (`pedidos_temu` / `pedidos_tiktok`) a cada canal.
_CANAL_DE_CUENTA_PEDIDO = {"TEMU": "temu", "TIKTOK": "tiktok"}

ACC_INACTIVAR = "cero_inactivar"
ACC_REACTIVAR = "cero_reactivar"
ACC_SIN_CAMBIO = "cero_sin_cambio"
ACC_OMITIR = "cero_omitir"
ACC_SOLTAR = "cero_soltar"
ACC_ERROR = "cero_error"
ACCIONES = [ACC_INACTIVAR, ACC_REACTIVAR, ACC_SIN_CAMBIO, ACC_OMITIR, ACC_SOLTAR, ACC_ERROR]
# EL INTENTO: una fila que se sella ANTES de llamar al canal (`apagar (vivo 2/8)` /
# `prender (vivo 3/2)`). No es una de las seis del panel —no pinta nada—: es el
# rastro durable de «le pedí al canal que la sacara de la venta». De ella salen
#   · los TOPES: se cuentan llamadas, no éxitos (una que el canal aceptó y no se vio
#     converger, o que rechazó, también gastó el tope);
#   · la RECONCILIACIÓN: si el proceso muere entre la llamada y el cierre (un
#     despliegue a media vuelta), el barrido del siguiente censo encuentra el intento
#     sin cierre, lee el canal y sella lo que de verdad pasó.
# Y si el intento no se puede sellar, NO se llama al canal: sin bitácora no se actúa.
ACC_INTENTO = "cero_intento"
INTENTO_APAGAR, INTENTO_PRENDER = "apagar", "prender"
_QUE_DE = {ACC_INACTIVAR: INTENTO_APAGAR, ACC_REACTIVAR: INTENTO_PRENDER}
# Las que abren o cierran la marca. La marca está vigente si la ÚLTIMA de estas
# (real y con «ok») es `cero_inactivar`.
_ACC_MARCA = [ACC_INACTIVAR, ACC_REACTIVAR, ACC_SOLTAR]
# Las filas de un evento del fan-out (las mismas de `fanout_recuperar`): son las
# únicas cuyo `objetivo` es el objetivo real del SKU en ese momento.
_ACC_FANOUT = ["escribir", "omitir", "sin_cambio", "sin_destinos"]

MOTIVO_BARRIDO = "seguro: barrido tras el censo de {canal}"
MOTIVO_ESPERA = "seguro: reactivar tras espera"
MOTIVO_MARCA = "seguro: marca con stock de vuelta"
MOTIVO_MANUAL = "seguro: manual"
MOTIVO_TARDE = "seguro: intento confirmado tarde"

# Los errores que ocurren DESPUÉS de llamar al canal. Se reconocen por su texto:
# son los únicos `cero_error` que CIERRAN un intento (uno anterior a la llamada
# —lectura ilegible, tope caído— no dice nada de un intento que quedó colgado).
ERR_RECHAZO = "ERROR: el canal rechazó la llamada"
ERR_NO_CONVERGE = "ERROR: no convergió"
ERR_SIN_CIERRE = "ERROR: intento sin cierre"
# Avisos que el barrido deja UNA vez al día por publicación (se reconocen por su prefijo).
ERR_AUDITORIA = "ERROR: auditoría"
CON_STOCK = "con stock y apagada"
MOVIDA = "movida por el canal"

TODOS = "*"                # en FANOUT_CERO_SOLO_SKUS: todo el catálogo
# Temu, además de lo apagable: estados desde los que la publicación puede volver
# SOLA a la venta (o que nadie ha sondeado). No se apagan, pero se anotan una vez
# al día para que el ensayo enseñe el tamaño real. `3/2` (apagada por el vendedor),
# los incompletos y los borradores no entran: de ahí no regresa sola.
_TEMU_VIGILADOS = {"3/1": "agotada", "3/3": "sin nombre: va y viene con 2/8",
                   "2/4": "familia «Active», sin sondear"}
# Temu: estados que sólo pone la PLATAFORMA (agotada, bloqueada). Si una publicación
# que el seguro dejó apagada aparece ahí, la movió Temu y no una persona: la marca NO
# se suelta. Medido en 45 días de `channel.listing_history`: `3/2→3/3` 10 veces y
# `3/3→2/8` de regreso 34; con la regla vieja cada una soltaba la marca sola.
_TEMU_DEL_CANAL = {"3/1", "3/3"}
# El estado en que queda lo que se apaga A MANO en el panel de cada canal.
_APAGADO_A_MANO = {"temu": "3/2", "tiktok": "SELLER_DEACTIVATED"}

_ZONA = "America/Mexico_City"
_ESPERA_S = 8.0            # tras apagar o prender se espera y se RELEE (la lectura de Temu va ~5 s atrás)
_RELECTURAS = 3            # y si aún no se ve el cambio, se insiste: una lectura rancia no es un fracaso
_MARGEN_ESPERA_S = 20.0    # colchón del anti-parpadeo: el evento que arranca la espera aún no está en la bitácora
_GRACIA_VENTA_MIN = 15     # una venta comprada hasta 15 min después de apagar pudo estar ya en camino
_BARRIDO_MAX = 60          # candidatos por barrido (los topes cortan mucho antes)
_MARCAS_TTL_S = 60.0
_DEDUP_S = 24 * 3600
_SELLAR_INTENTOS = 3
_NO_CONVERGE_MAX = 2       # «no convergió» seguidas en un canal ⇒ se deja de llamar un rato
_PAUSA_S = 3600.0
_PRESUPUESTO_S = 1800.0    # cuánto vale el presupuesto de una vuelta para los excedentes del mismo censo
_REENCOLAR_S = 24 * 3600   # una misma marca se devuelve a la cola a lo mucho una vez al día
_RECONCILIAR_MAX = 20      # intentos sin cierre que se revisan por barrido (una lectura en vivo cada uno)
_AVISOS_MAX = 200          # SKUs que esperan su aviso de «apagó» / «regresó»
_HORA_S = 3600.0           # ventana del tope «por hora» de los eventos sueltos (fuera de un censo)
_CON_STOCK_TARDE_S = 6 * 3600.0    # reactivación encendida: tantas horas con stock y aún apagada ⇒ se avisa
_AUDITORIA_DIAS = 7        # cuánto se vigila una reactivación de TikTok que quedó en auditoría (PENDING)
_AUDITORIA_LENTA_S = 24 * 3600.0   # PENDING más de esto ⇒ se avisa
_SOLTADAS_DIAS = 30        # cuánto se listan las soltadas que siguen fuera de la venta

_lock = threading.Lock()
_candados: dict[tuple[str, str], threading.Lock] = {}
_esperas: dict[str, float] = {}                       # sku → epoch en que vence su espera
_vistas: dict[tuple, float] = {}                      # dedupe de filas (canal, item, accion, ensayo, prefijo)
_marcas_cache: dict[str, Any] = {"t": 0.0, "v": None}
_ultimo: dict[str, dict[str, Any]] = {}               # última vuelta del barrido por canal
_huerfanas_avisadas: set[tuple[str, str]] = set()     # (canal, sku) ya avisadas en este proceso
_intentos_mem: dict[tuple[str, str, str], int] = {}   # (canal, apagar|prender, día CDMX) → llamadas de este proceso
_pausas: dict[str, dict[str, float]] = {}             # canal → {seguidas, hasta}: no confirma lo que se le pide
_presupuestos: dict[str, dict[str, Any]] = {}         # canal → {t, vuelta}: la vuelta del último barrido
_reencoladas: dict[tuple[str, str], float] = {}       # (canal, item) → cuándo se devolvió a la cola
_avisos_pend: dict[tuple[str, str], list[str]] = {}   # (apago|prendio, canal) → SKUs que aún no salen en un aviso
_avisos_dia: set[tuple] = set()                       # avisos de «una vez al día» ya mandados en este proceso
_sueltos: dict[str, list[float]] = {}                 # canal → cuándo se intentó apagar FUERA de un censo


# ── Reloj (aparte para poder suplantarlo en las pruebas) ─────────────────────

def _ahora() -> float:
    return time.time()


def _dormir(segundos: float) -> None:
    time.sleep(segundos)


# ── Configuración ────────────────────────────────────────────────────────────

def habilitado() -> bool:
    return bool(getattr(settings, "fanout_cero_enabled", False))


def canal_encendido(canal: str) -> bool:
    return bool(getattr(settings, f"fanout_cero_{(canal or '').lower()}", False))


def _csv(nombre: str) -> set[str]:
    crudo = str(getattr(settings, nombre, "") or "")
    return {x.strip().upper() for x in crudo.replace(";", ",").split(",") if x.strip()}


def _excluidos() -> set[str]:
    return _csv("fanout_cero_excluir")


def _solo_skus() -> set[str]:
    """A quién se le aplica de verdad: vacío = a nadie (ensayo forzado), una lista
    = sólo esos SKUs (canario), `*` = todo el catálogo."""
    return _csv("fanout_cero_solo_skus")


def _alcanza(sku: str, solo: set[str] | None = None) -> bool:
    """¿Este SKU entra en lo que el seguro mira? Con la lista vacía entran todos
    (en ensayo forzado: se quiere ver el tamaño real); con una lista, sólo ésos."""
    solo = _solo_skus() if solo is None else solo
    return not solo or TODOS in solo or str(sku or "").strip().upper() in solo


def estados_temu() -> set[str]:
    """Estados de Temu que se pueden apagar. Nace en `2/8`; `3/1` entra cuando el
    sondeo confirme que `sale.status.set` lo acepta."""
    crudo = str(getattr(settings, "fanout_cero_temu_estados", "2/8") or "")
    return {x.strip() for x in crudo.split(",") if x.strip()}


def _entero(nombre: str, omision: int) -> int:
    try:
        return max(0, int(getattr(settings, nombre, omision)))
    except (TypeError, ValueError):
        return omision


def _espera_s() -> float:
    try:
        return max(0.0, float(getattr(settings, "fanout_cero_espera_s", _ESPERA_S)))
    except (TypeError, ValueError):
        return _ESPERA_S


def _relecturas() -> int:
    return max(1, _entero("fanout_cero_relecturas", _RELECTURAS))


def _hoy() -> str:
    """El día de hoy en CDMX (el mismo corte que usa el tope del día)."""
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(_ahora(), ZoneInfo(_ZONA)).strftime("%Y-%m-%d")


def ensayo(canal: str) -> tuple[bool, list[str]]:
    """(¿corre en ensayo?, por qué está FORZADO). Forzado = el fan-out no escribe
    de verdad en ese canal, o la bitácora no llega a kubera: apagar una publicación
    mientras su stock no se sincroniza, o sin poder dejar la marca, sería peor que
    no hacer nada. Y forzado también mientras nadie diga A QUIÉN se le aplica
    (FANOUT_CERO_SOLO_SKUS vacío): apagar FANOUT_CERO_ENSAYO no puede, por sí
    solo, soltar el seguro sobre todo el catálogo."""
    from services import fanout_stock
    canal = (canal or "").lower()
    forzado: list[str] = []
    if not fanout_stock.habilitado():
        forzado.append("FANOUT_ENABLED apagado")
    if fanout_stock.dry_run():
        forzado.append("FANOUT_DRY_RUN encendido")
    activos = fanout_stock._canales_activos()
    if activos is not None and canal not in activos:
        forzado.append(f"'{canal}' fuera de FANOUT_CANALES")
    if canal in CANALES and not getattr(settings, f"fanout_{canal}", False):
        forzado.append(f"FANOUT_{canal.upper()} apagado")
    if not getattr(settings, "supabase_write_fanout_log", False):
        forzado.append("SUPABASE_WRITE_FANOUT_LOG apagado (sin bitácora en kubera no hay marca)")
    if not _solo_skus():
        forzado.append("FANOUT_CERO_SOLO_SKUS vacío (falta decir a quién: una lista de SKUs, "
                       "o * para todo el catálogo)")
    return bool(getattr(settings, "fanout_cero_ensayo", True)) or bool(forzado), forzado


# ── Estados ──────────────────────────────────────────────────────────────────

def a_la_venta(canal: str, estado: Any) -> bool:
    if (canal or "").lower() == "tiktok":
        return str(estado or "").upper() == "ACTIVATE"
    from services import temu
    return str(estado or "") in temu.VENDIBLES


def apagable(canal: str, estado: Any) -> bool:
    """¿Se le puede pedir al canal que la saque de la venta desde este estado?"""
    if (canal or "").lower() == "tiktok":
        return str(estado or "").upper() == "ACTIVATE"   # `deactivate` sólo aplica ahí
    return str(estado or "") in estados_temu()


def _igual(a: Any, b: Any) -> bool:
    return str(a or "").strip().upper() == str(b or "").strip().upper()


# ── La regla de NIVEL (pura) ─────────────────────────────────────────────────

def decidir_nivel(canal: str, sku: str, skus_vivos: list[dict[str, Any]],
                  objetivos: dict[str, int | None] | None = None,
                  nivel_sku_temu: bool = False) -> dict[str, Any]:
    """
    Qué se apaga: la publicación completa, una variante, o nada. PURA: no lee ni
    escribe; `skus_vivos` es lo que el canal dice tener AHORA
    (`[{id, seller_sku, cantidad}]`) y `objetivos` el objetivo de cada hermana
    (`{SELLER_SKU: objetivo | None}`; None = Woo ilegible).

      1 variante                               → publicación completa
      Temu con más de una y `nivel_sku_temu`   → sólo esa variante (operationType=2)
      Temu con más de una y sin la bandera     → nada («N variantes»)
      TikTok con más de una                    → producto completo SÓLO si todas las
                                                 hermanas tienen objetivo legible y en 0

    Devuelve `{nivel: "publicacion" | "variante" | None, sku_ids, motivo}`.
    """
    canal = (canal or "").lower()
    vivos = list(skus_vivos or [])
    n = len(vivos)
    if n == 0:
        return {"nivel": None, "sku_ids": None,
                "motivo": "la publicación no trae variantes legibles"}
    propias = [v for v in vivos if _igual(v.get("seller_sku"), sku)]
    if n == 1:
        ajeno = str(vivos[0].get("seller_sku") or "").strip()
        if ajeno and not propias:
            # La fila del censo apunta a una publicación que hoy es de OTRO SKU.
            return {"nivel": None, "sku_ids": None,
                    "motivo": f"la publicación es de otro SKU ({ajeno})"}
        return {"nivel": "publicacion", "sku_ids": None, "motivo": None}
    if canal == "temu":
        if not nivel_sku_temu:
            return {"nivel": None, "sku_ids": None,
                    "motivo": f"{n} variantes (apagar sólo una espera FANOUT_CERO_TEMU_NIVEL_SKU)"}
        if len(propias) != 1 or not propias[0].get("id"):
            return {"nivel": None, "sku_ids": None,
                    "motivo": f"{n} variantes y no identifico cuál es {sku}"}
        return {"nivel": "variante", "sku_ids": [propias[0]["id"]], "motivo": None}
    # TikTok: la baja es del producto entero.
    if not propias:
        return {"nivel": None, "sku_ids": None,
                "motivo": f"{n} variantes y ninguna es {sku}"}
    obj = {str(k).strip().upper(): v for k, v in (objetivos or {}).items()}
    for v in vivos:
        hermana = str(v.get("seller_sku") or "").strip()
        if not hermana:
            return {"nivel": None, "sku_ids": None,
                    "motivo": f"{n} variantes y una no trae seller_sku"}
        if _igual(hermana, sku):
            continue
        o = obj.get(hermana.upper())
        if o is None:
            return {"nivel": None, "sku_ids": None,
                    "motivo": f"{n} variantes: la hermana {hermana} no tiene stock legible en Woo"}
        if int(o) > 0:
            return {"nivel": None, "sku_ids": None,
                    "motivo": f"{n} variantes: la hermana {hermana} tiene {int(o)} piezas"}
    return {"nivel": "publicacion", "sku_ids": None, "motivo": None}


def _objetivos_hermanas(sku: str, skus_vivos: list[dict[str, Any]]) -> dict[str, int | None]:
    """El objetivo del fan-out para cada variante HERMANA (Woo en vivo). Si Woo
    no contesta por una, queda en None y `decidir_nivel` no apaga."""
    from services import fanout_stock
    salida: dict[str, int | None] = {}
    for v in skus_vivos or []:
        hermana = str(v.get("seller_sku") or "").strip()
        if not hermana or _igual(hermana, sku):
            continue
        try:
            stock = fanout_stock._stock_drop(hermana)
        except Exception:  # noqa: BLE001 — ilegible = no se apaga
            stock = None
        salida[hermana.upper()] = None if stock is None else max(0, int(stock) - fanout_stock._reserva())
    return salida


# ── Lectura EN VIVO ──────────────────────────────────────────────────────────

def _a_int(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _vivo_temu(item_id: str) -> dict[str, Any]:
    """Estado, stock y variantes de UN goods de Temu, ahora. `{ok: False, motivo}`
    si no se puede leer: con una lectura ilegible no se actúa."""
    from services import fanout_stock
    from services import temu as tm
    if not tm.disponible():
        return {"ok": False, "motivo": "Temu no configurado (faltan TEMU_*)"}
    try:
        goods_id = int(str(item_id))
    except (TypeError, ValueError):
        return {"ok": False, "motivo": f"goodsId ilegible: {item_id!r}"}
    try:
        res = fanout_stock._en_hilo(
            lambda: tm.llamar("bg.local.goods.list.query",
                              {"pageNo": 1, "pageSize": 10, "goodsIdList": [goods_id]}),
            "seguro: lectura temu")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": f"{type(exc).__name__}: {exc}"}
    fila = next((g for g in ((res or {}).get("goodsList") or [])
                 if str(g.get("goodsId")) == str(goods_id)), None)
    if fila is None:
        return {"ok": False, "motivo": "el goods no aparece en el listado (¿eliminado?)"}
    if fila.get("status4VO") is None:
        return {"ok": False, "motivo": "el listado no trae status4VO"}
    ids = [i for i in (fila.get("skuIdList") or []) if i not in (None, "")]
    sku_goods = str(fila.get("outGoodsSn") or "").strip()
    # Si algún día el listado trae el SKU de cada variante, se usa; hoy sólo se
    # conoce el del goods, que vale para la variante cuando es una sola.
    por_id = {str(d.get("skuId")): str(d.get("outSkuSn") or "").strip()
              for d in (fila.get("skuInfoList") or fila.get("skuList") or [])
              if isinstance(d, dict) and d.get("skuId") not in (None, "")}
    skus = [{"id": i,
             "seller_sku": por_id.get(str(i)) or (sku_goods if len(ids) == 1 else ""),
             "cantidad": _a_int(fila.get("quantity")) if len(ids) == 1 else None}
            for i in ids]
    return {"ok": True, "estado": f"{fila.get('status4VO')}/{fila.get('subStatus4VO')}",
            "stock": _a_int(fila.get("quantity")), "skus": skus}


def _vivo_tiktok(item_id: str) -> dict[str, Any]:
    """Estado y variantes (con el stock del almacén de VENTAS) de UN producto de
    TikTok, ahora."""
    from services import fanout_stock
    from services import tiktok as tk
    token, ciph = tk.access_token(), tk.cipher()
    if not (token and ciph):
        return {"ok": False, "motivo": "TikTok sin token o sin shop_cipher"}
    try:
        d = fanout_stock._en_hilo(
            lambda: tk.llamar(f"/product/202309/products/{item_id}", token,
                              {"shop_cipher": ciph}),
            "seguro: lectura tiktok")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": f"{type(exc).__name__}: {exc}"}
    estado = str((d or {}).get("status") or "").upper()
    if not estado:
        return {"ok": False, "motivo": "el producto no trae status"}
    skus = []
    for s in (d.get("skus") or []):
        cantidad = next((i.get("quantity") for i in (s.get("inventory") or [])
                         if str(i.get("warehouse_id") or "") == fanout_stock._ALMACEN_VENTAS_TIKTOK),
                        None)
        skus.append({"id": str(s.get("id") or ""),
                     "seller_sku": str(s.get("seller_sku") or "").strip(),
                     "cantidad": _a_int(cantidad)})
    return {"ok": True, "estado": estado, "stock": None, "skus": skus}


def _leer_vivo(canal: str, item_id: str) -> dict[str, Any]:
    return _vivo_temu(item_id) if canal == "temu" else _vivo_tiktok(item_id)


def _stock_vivo(vivo: dict[str, Any], sku: str) -> int | None:
    """El stock que el canal ofrece de NUESTRO SKU, de la lectura en vivo."""
    if vivo.get("stock") is not None:
        return _a_int(vivo.get("stock"))
    skus = vivo.get("skus") or []
    propia = next((v for v in skus if _igual(v.get("seller_sku"), sku)), None)
    if propia is None and len(skus) == 1:
        propia = skus[0]
    return _a_int((propia or {}).get("cantidad"))


# ── Las dos llamadas que SÍ mueven algo en el canal ──────────────────────────

def _apagar(canal: str, item_id: str, sku_ids: list[Any] | None = None) -> Any:
    from services import fanout_stock
    if canal == "temu":
        from services import temu as tm
        return fanout_stock._en_hilo(lambda: tm.cambiar_venta(item_id, False, sku_ids),
                                     "seguro: apagar temu")
    from services import tiktok as tk
    token, ciph = tk.access_token(), tk.cipher()
    return fanout_stock._en_hilo(lambda: tk.desactivar(item_id, token, ciph),
                                 "seguro: apagar tiktok")


def _prender(canal: str, item_id: str, sku_ids: list[Any] | None = None) -> Any:
    from services import fanout_stock
    if canal == "temu":
        from services import temu as tm
        return fanout_stock._en_hilo(lambda: tm.cambiar_venta(item_id, True, sku_ids),
                                     "seguro: prender temu")
    from services import tiktok as tk
    token, ciph = tk.access_token(), tk.cipher()
    return fanout_stock._en_hilo(lambda: tk.activar(item_id, token, ciph),
                                 "seguro: prender tiktok")


def _releer(canal: str, item_id: str, listo) -> tuple[str, dict[str, Any]]:
    """Espera y relee el estado vivo hasta FANOUT_CERO_RELECTURAS veces, o hasta
    que `listo(estado)`. Devuelve el último estado legible ('' si ninguno lo fue)."""
    nuevo, releida = "", {}
    for _ in range(_relecturas()):
        _dormir(_espera_s())
        releida = _leer_vivo(canal, item_id)
        nuevo = str(releida.get("estado") or "") if releida.get("ok") else ""
        if nuevo and listo(nuevo):
            break
    return nuevo, releida


def _candado(canal: str, item_id: str) -> threading.Lock:
    """Un candado por publicación: `bajar` corre en los hilos de los censos y
    `_aplicar` en el worker, y los dos pueden llegar a la misma a la vez."""
    with _lock:
        return _candados.setdefault((canal, str(item_id)), threading.Lock())


# ── La MARCA y las demás lecturas de kubera ──────────────────────────────────
#
# POR QUÉ AQUÍ SÍ SIRVE LA BITÁCORA COMO CANDADO. La migración 0022 sacó de
# `fanout_log` una marca de idempotencia porque un «no sé» se leía como «no lo
# hice» y el flujo repetía una escritura. Aquí el «no sé» cae del lado SEGURO: sin
# fila, o sin kubera, NO se reactiva. Y para apagar ni siquiera se consulta: es
# idempotente por el estado vivo de la publicación.

_SQL_MARCA = """
    select distinct on (canal, item_id) id, ts, sku::text as sku, canal, cuenta,
           item_id, accion, resultado
      from ops.fanout_log
     where accion in ('cero_inactivar','cero_reactivar','cero_soltar')
       and dry_run = false and resultado like 'ok%%'
       {filtro}
     order by canal, item_id, ts desc, id desc"""


def marca(canal: str, item_id: str) -> dict[str, Any] | None:
    """La marca VIGENTE del seguro para esa publicación, o None. LEVANTA si
    kubera no contesta: quien llama lo trata como «no sé» y no reactiva."""
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        _SQL_MARCA.format(filtro="and canal = %(canal)s and item_id = %(item)s"),
        {"canal": canal, "item": str(item_id)})
    fila = filas[0] if filas else None
    return fila if fila and fila.get("accion") == ACC_INACTIVAR else None


def marcas_vigentes(canal: str | None = None) -> list[dict[str, Any]]:
    """Todas las marcas vigentes (la misma consulta, sin filtrar por publicación)."""
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        _SQL_MARCA.format(filtro="and canal = %(canal)s" if canal else ""),
        {"canal": canal})
    return [f for f in filas if f.get("accion") == ACC_INACTIVAR]


def _marcas_por_item() -> dict[tuple[str, str], dict[str, Any]]:
    """Las marcas vigentes, con caché de un minuto. Es el prefiltro barato de la
    reactivación: casi ningún evento toca una publicación marcada, y así no cuesta
    una consulta por destino. Antes de actuar se confirma con `marca()`, fresca."""
    with _lock:
        if _marcas_cache["v"] is not None and _ahora() - _marcas_cache["t"] < _MARCAS_TTL_S:
            return _marcas_cache["v"]
    v = {(str(f["canal"]), str(f["item_id"])): f for f in marcas_vigentes()}
    with _lock:
        _marcas_cache.update(t=_ahora(), v=v)
    return v


def _olvidar_marcas() -> None:
    with _lock:
        _marcas_cache.update(t=0.0, v=None)


_RE_MARCA = re.compile(r"^ok \((?P<de>[^→()]*)→(?P<a>[^()·]*?)(?:\s*·\s*variante\s+(?P<v>\d+))?\)")


def leer_marca(resultado: Any) -> dict[str, Any] | None:
    """`ok (2/8→3/2)` → `{de: "2/8", a: "3/2", variante: None}`. None = ilegible."""
    m = _RE_MARCA.match(str(resultado or ""))
    if not m or not m.group("a").strip():
        return None
    return {"de": m.group("de").strip(), "a": m.group("a").strip(),
            "variante": int(m.group("v")) if m.group("v") else None}


_SQL_CORTADO = """
    select accion from ops.fanout_log
     where canal = %(c)s and item_id = %(i)s and dry_run = false
       and accion = any(%(a)s)
       and (accion = 'cero_error' or resultado like 'ok%%')
       and ts > now() - interval '24 hours'
     order by ts desc, id desc limit 3"""


def _cortado(canal: str, item_id: str) -> bool:
    """¿Los TRES últimos intentos reales de esa publicación en 24 h fueron error?
    Un «ok» (o soltarla a mano) lo destraba. Levanta si kubera no contesta."""
    from services import supabase_db as sdb
    filas = sdb.fetch_all(_SQL_CORTADO,
                          {"c": canal, "i": str(item_id), "a": _ACC_MARCA + [ACC_ERROR]})
    return len(filas) >= 3 and all(f.get("accion") == ACC_ERROR for f in filas)


_SQL_USADAS = """
    select count(*) as n from ops.fanout_log
     where accion = %(a)s and canal = %(c)s and dry_run = %(d)s
       and resultado like %(r)s
       and ts >= ((now() at time zone %(z)s)::date)::timestamp at time zone %(z)s"""


def usadas_hoy(canal: str, accion: str, ensayos: bool = False) -> int:
    """
    Cuántas veces se INTENTÓ `accion` hoy (día de CDMX) en ese canal. Reales: las
    filas `cero_intento` —una por LLAMADA al canal, saliera bien o no—, que se
    sellan antes de llamar. De ensayo: las filas de ensayo de esa acción.
    Levanta si kubera no contesta: sin el tope no se actúa.

    POR QUÉ INTENTOS Y NO ÉXITOS. Si Temu acepta `sale.status.set` y su listado
    tarda en enseñarlo, cada intento queda como «no convergió» y un contador de
    éxitos se queda en 0: 26 candidatas × 12 censos = 60 llamadas con el tope en
    0/20 (medido con el canal falseado). El tope tiene que acotar lo que se le
    PIDE al canal, que es justo lo que importa el día que algo vacía Woo.
    """
    from services import supabase_db as sdb
    if ensayos:
        p = {"a": accion, "d": True, "r": "%"}
    else:
        p = {"a": ACC_INTENTO, "d": False, "r": _QUE_DE[accion] + " %"}
    fila = sdb.fetch_one(_SQL_USADAS, {**p, "c": canal, "z": _ZONA}) or {}
    return int(fila.get("n") or 0)


def _contar_intento(canal: str, que: str) -> None:
    """El piso EN MEMORIA del tope: cada llamada de este proceso, se haya podido
    contar en kubera o no. Los días viejos se tiran."""
    hoy = _hoy()
    with _lock:
        for clave in [k for k in _intentos_mem if k[2] != hoy]:
            _intentos_mem.pop(clave, None)
        _intentos_mem[(canal, que, hoy)] = _intentos_mem.get((canal, que, hoy), 0) + 1


def _usadas(canal: str, accion: str) -> int:
    """Lo gastado hoy del tope: lo que diga kubera o lo que este proceso sabe que
    llamó, lo que sea MAYOR. Así el tope no depende de que la cuenta de kubera
    vaya al día. Levanta si kubera no contesta."""
    en_kubera = usadas_hoy(canal, accion)
    with _lock:
        en_memoria = _intentos_mem.get((canal, _QUE_DE[accion], _hoy()), 0)
    return max(int(en_kubera), int(en_memoria))


_RE_INTENTO = re.compile(r"^(?P<que>apagar|prender) \(vivo (?P<de>[^()·]*?)(?:\s*·\s*variante\s+(?P<v>\d+))?\)")


def leer_intento(resultado: Any) -> dict[str, Any] | None:
    """`apagar (vivo 2/8)` → `{que: "apagar", de: "2/8", variante: None}`."""
    m = _RE_INTENTO.match(str(resultado or ""))
    if not m or not m.group("de").strip():
        return None
    return {"que": m.group("que"), "de": m.group("de").strip(),
            "variante": int(m.group("v")) if m.group("v") else None}


# Lo último que se sabe de cada publicación entre lo que ABRE un intento y lo que
# lo CIERRA (un «ok», o un error posterior a la llamada). Queda pendiente la que
# termina en un intento con más de 3 minutos (nadie tarda eso: el proceso murió a
# media vuelta), o en un «no convergió» reciente (el canal pudo enseñar tarde un
# cambio que sí hizo).
_SQL_PENDIENTES = """
    with u as (
      select distinct on (canal, item_id) canal, item_id, sku::text as sku, cuenta,
             accion, resultado, ts
        from ops.fanout_log
       where canal = %(c)s and dry_run = false and ts > now() - interval '48 hours'
         and (accion = 'cero_intento'
              or (accion in ('cero_inactivar','cero_reactivar','cero_soltar') and resultado like 'ok%%')
              or (accion = 'cero_error' and (resultado like %(e1)s or resultado like %(e2)s
                                             or resultado like %(e3)s)))
       order by canal, item_id, ts desc, id desc),
    i as (
      select distinct on (canal, item_id) canal, item_id, resultado as intento
        from ops.fanout_log
       where canal = %(c)s and dry_run = false and accion = 'cero_intento'
         and ts > now() - interval '48 hours'
       order by canal, item_id, ts desc, id desc)
    select u.canal, u.item_id, u.sku, u.cuenta, u.accion, u.resultado, i.intento
      from u join i using (canal, item_id)
     where (u.accion = 'cero_intento' and u.ts < now() - interval '3 minutes')
        or (u.accion = 'cero_error' and u.resultado like %(e2)s
            and u.ts > now() - interval '6 hours')
     order by u.ts limit %(n)s"""


def pendientes(canal: str, limite: int = _RECONCILIAR_MAX) -> list[dict[str, Any]]:
    """Publicaciones con un intento que nadie cerró, o que «no convergió» hace
    poco. Sólo lee; levanta si kubera no contesta."""
    from services import supabase_db as sdb
    return sdb.fetch_all(_SQL_PENDIENTES, {
        "c": canal, "n": int(limite), "e1": ERR_RECHAZO + "%",
        "e2": ERR_NO_CONVERGE + "%", "e3": ERR_SIN_CIERRE + "%"})


def es_padre(sku: str) -> bool | None:
    """True = tiene variantes (es padre); False = no; None = no se pudo saber.
    Por `wc_parent_id`, la única relación padre→variante que kubera conserva viva."""
    try:
        from services import channel_read
        from services import supabase_db as sdb
        fila = sdb.fetch_one("select wc_id from core.products where sku = %s", (sku,))
        if not fila or not fila.get("wc_id"):
            return None
        wc_id = int(fila["wc_id"])
        return bool(channel_read.hijos_por_wc_id([wc_id]).get(wc_id))
    except Exception as exc:  # noqa: BLE001 — sin poder saberlo, no se toca
        log.warning("seguro: no pude determinar si %s es padre: %s", sku, exc)
        return None


_SQL_AJENO = """
    select h.valor_anterior, h.valor_nuevo, coalesce(h.detectado_via, '') as via
      from channel.listing_history h
     where h.canal = %(c)s and h.sku = %(s)s and h.campo = 'status'
       and h.changed_at > %(t)s
       and coalesce(h.detectado_via, '') <> 'fanout_cero'
       and coalesce(h.valor_nuevo, '') <> all(%(p)s::text[])
     order by h.changed_at limit 1"""


def cambio_ajeno(canal: str, sku: str, desde: Any,
                 propios: list[str] | None = None) -> dict[str, Any] | None:
    """
    El primer cambio de estado de esa publicación DESPUÉS de la marca que no hizo
    el seguro (lo vio un censo, o lo escribió otro flujo). Levanta si kubera no
    contesta. Límite conocido: el censo pasa cada ~60 min.

    `propios` = los estados cuya llegada NO es ajena (`_propios`): el que dejó el
    seguro —un cambio que TERMINA ahí es el censo viendo lo que hizo el propio
    seguro: su reflejo en `channel.listings` falló, o se cruzó con un censo en
    curso— y, en Temu, los que sólo pone la plataforma (`3/1`, `3/3`). Sin esto la
    marca se soltaba sola: medido con COM-0081-ROS, donde el `2/8→3/2` que anotó
    el censo se leía como «alguien más la movió», y con las diez `3/2→3/3` que
    Temu hizo por su cuenta en 45 días.
    """
    from services import supabase_db as sdb
    return sdb.fetch_one(_SQL_AJENO, {"c": canal, "s": sku, "t": desde,
                                      "p": [str(x) for x in (propios or []) if x]})


_SQL_BLOQUE = """
    select count(*) as n,
           to_char(date_trunc('hour', h.changed_at at time zone %(z)s), 'YYYY-MM-DD HH24:MI') as hora
      from channel.listing_history h
     where h.canal = %(c)s and h.campo = 'status' and h.changed_at > %(t)s
       and h.valor_nuevo = %(off)s and h.valor_anterior = any(%(venta)s)
       and coalesce(h.detectado_via, '') <> 'fanout_cero'
     group by 2
    having count(*) >= %(n)s
     order by 1 desc limit 1"""


def apagado_en_bloque(canal: str, desde: Any) -> dict[str, Any] | None:
    """
    ¿Alguien apagó el canal EN BLOQUE después de `desde` (la marca)? Devuelve
    `{n, hora}` de la hora con más publicaciones apagadas a mano, si llegan a
    FANOUT_CERO_BLOQUE_N; None si no (o si la guarda está en 0).

    Un apagado general a mano no deja rastro sobre lo que el seguro YA tenía
    apagado (su estado no cambia), así que el seguro lo regresaría a la venta al
    volver el stock, en contra de la decisión de pausar. Pasó dos veces: el
    21-ago (280 de TikTok y 306 de Temu en el mismo minuto) y el 18-sep (7). El
    rastro está en las DEMÁS publicaciones. Levanta si kubera no contesta.
    """
    umbral = _entero("fanout_cero_bloque_n", 10)
    canal = (canal or "").lower()
    if umbral <= 0 or canal not in _APAGADO_A_MANO or desde is None:
        return None
    from services import supabase_db as sdb
    from services import temu
    venta = sorted(temu.VENDIBLES) if canal == "temu" else ["ACTIVATE"]
    return sdb.fetch_one(_SQL_BLOQUE, {"c": canal, "t": desde, "off": _APAGADO_A_MANO[canal],
                                       "venta": venta, "n": umbral, "z": _ZONA})


def edad_con_stock_s(sku: str) -> float | None:
    """Segundos desde el PRIMER evento del fan-out con objetivo > 0 posterior al
    último con objetivo = 0. None = todavía no hay ninguno en la bitácora (el
    evento en curso aún no se guarda). Levanta si kubera no contesta."""
    from services import supabase_db as sdb
    fila = sdb.fetch_one(
        """with z as (
             select max(ts) as t from ops.fanout_log
              where sku = %(s)s and objetivo = 0 and coalesce(canal, '') <> 'woocommerce'
                and accion = any(%(cero)s))
           select extract(epoch from now() - min(f.ts))::float as edad
             from ops.fanout_log f cross join z
            where f.sku = %(s)s and f.objetivo > 0 and coalesce(f.canal, '') <> 'woocommerce'
              and f.accion = any(%(a)s) and (z.t is null or f.ts > z.t)""",
        {"s": sku, "a": _ACC_FANOUT, "cero": _ACC_FANOUT + ACCIONES}) or {}
    return None if fila.get("edad") is None else float(fila["edad"])


def _repetida(canal: str, item_id: str, accion: str, de_ensayo: bool, prefijo: str = "") -> bool:
    """¿Ya hay una fila así en 24 h? Las `cero_omitir` y las de ensayo se anotan
    una vez al día por publicación: repetirlas en cada evento sólo hace ruido.
    `prefijo` acota a las filas cuyo resultado empieza así (para las que comparten
    acción con otras que sí deben repetirse)."""
    clave = (canal, str(item_id), accion, bool(de_ensayo), prefijo)
    with _lock:
        t = _vistas.get(clave)
    if t and _ahora() - t < _DEDUP_S:
        return True
    try:
        from services import supabase_db as sdb
        hay = sdb.fetch_one(
            """select 1 as x from ops.fanout_log
                where canal = %(c)s and item_id = %(i)s and accion = %(a)s
                  and dry_run = %(d)s and resultado like %(p)s
                  and ts > now() - interval '24 hours' limit 1""",
            {"c": canal, "i": str(item_id), "a": accion, "d": bool(de_ensayo),
             "p": prefijo + "%"})
    except Exception:  # noqa: BLE001 — sin poder saberlo, se anota
        return False
    if hay:
        with _lock:
            _vistas[clave] = _ahora()
    return bool(hay)


# ── Rastro secundario: el estado vivo, reflejado en channel.listings ─────────

def _reflejar(canal: str, sku: str, item_id: str, estado: str) -> None:
    """Deja en `channel.listings.status` el estado que se acaba de RELEER en vivo,
    firmado `fanout_cero`: el panel deja de contarla «a la venta» sin esperar al
    censo y `listing_history` guarda quién lo hizo. Mejor esfuerzo."""
    try:
        from services import supabase_db as sdb
        with sdb.get_cursor() as cur:
            cur.execute("select set_config('app.via', 'fanout_cero', true)")
            cur.execute(
                """update channel.listings
                      set status = %(e)s, updated_at = now()
                    where canal = %(c)s and sku = %(s)s and listing_id = %(i)s
                      and status is distinct from %(e)s""",
                {"e": estado, "c": canal, "s": sku, "i": str(item_id)})
    except Exception as exc:  # noqa: BLE001 — el censo siguiente lo corrige
        log.warning("seguro: no pude reflejar %s/%s=%s en channel.listings: %s",
                    canal, sku, estado, exc)


def huerfanas(canal: str | None = None) -> list[dict[str, Any]]:
    """
    Publicaciones cuyo estado ACTUAL lo dejó el seguro (lo firmó `fanout_cero` en
    `channel.listing_history`) y que NO tienen la fila de esa acción en la bitácora.

    Las filas se sellan al cerrar el evento, segundos después de llamar al canal:
    si el proceso muere en medio (un despliegue a media vuelta), la publicación
    queda apagada y SIN marca, y nadie la reactivaría sola. El reflejo en
    `channel.listings` sí se escribe en el momento, así que de ahí se deduce. Se
    resuelve reactivándola a mano o con `soltar` (cualquiera deja su fila). Sólo lee.
    """
    from services import supabase_db as sdb
    return sdb.fetch_all(
        """with h as (
             select distinct on (canal, sku) canal, sku, valor_anterior, valor_nuevo, changed_at
               from channel.listing_history
              where campo = 'status' and detectado_via = 'fanout_cero'
                and changed_at > now() - interval '14 days'
                and changed_at < now() - interval '10 minutes'
              order by canal, sku, changed_at desc)
           select h.canal, h.sku::text as sku, l.listing_id as item_id,
                  h.valor_anterior as de, h.valor_nuevo as a,
                  to_char(h.changed_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as cuando
             from h join channel.listings l
               on l.canal = h.canal and l.sku = h.sku and l.status = h.valor_nuevo
            where (%(c)s::text is null or h.canal = %(c)s)
              and not exists (
                    select 1 from ops.fanout_log f
                     where f.canal = h.canal and f.item_id = l.listing_id and f.dry_run = false
                       and f.accion in ('cero_inactivar','cero_reactivar','cero_soltar')
                       and f.resultado like 'ok%%'
                       and f.ts >= h.changed_at - interval '5 minutes')
            order by h.changed_at desc""",
        {"c": canal, "z": _ZONA})


# ── Bitácora: sellado VERIFICADO ─────────────────────────────────────────────

def _a_mysql(fila: dict[str, Any]) -> bool:
    """La copia en el `fanout_log` de MySQL, a mejor esfuerzo."""
    if not getattr(settings, "mysql_enabled", True):
        return False
    try:
        from services import db, fanout_stock
        fanout_stock._asegurar_schema()
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO fanout_log
                   (ts, sku, motivo, dry_run, stock_drop, objetivo, canal, cuenta,
                    item_id, accion, stock_canal, resultado, ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (fila["ts"], str(fila["sku"])[:100], str(fila.get("motivo") or "")[:160],
                 1 if fila.get("dry_run") else 0, fila.get("stock_drop"), fila.get("objetivo"),
                 fila.get("canal"), str(fila.get("cuenta") or "")[:40],
                 str(fila.get("item_id") or "")[:64], str(fila["accion"])[:20],
                 fila.get("stock_canal"), str(fila.get("resultado") or "")[:255],
                 fila.get("ms")))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("seguro: no pude anotar %s/%s en MySQL: %s",
                    fila.get("sku"), fila.get("accion"), exc)
        return False


def sellar(fila: dict[str, Any]) -> bool:
    """
    Inserta UNA fila `cero_*` en `ops.fanout_log`, VERIFICADA: tres intentos, y
    dice si entró. No pasa por `espejar` (que se traga el error) porque de la fila
    `cero_inactivar` depende que la publicación se reactive algún día.
    """
    en_kubera = False
    if getattr(settings, "supabase_write_fanout_log", False):
        from services import fanout_read
        ultimo: Exception | None = None
        for intento in range(_SELLAR_INTENTOS):
            try:
                fanout_read.registrar(fila)
                en_kubera = True
                break
            except Exception as exc:  # noqa: BLE001
                ultimo = exc
                if intento < _SELLAR_INTENTOS - 1:
                    _dormir(0.5 * (intento + 1))
        if not en_kubera:
            log.error("seguro: NO se pudo sellar %s/%s/%s en kubera tras %d intentos: %s",
                      fila.get("canal"), fila.get("sku"), fila.get("accion"),
                      _SELLAR_INTENTOS, ultimo)
        _a_mysql(fila)
        return en_kubera
    # Sin bitácora en kubera el seguro está en ensayo forzado: sólo queda MySQL.
    return _a_mysql(fila)


# ── El contexto de un evento ─────────────────────────────────────────────────

def _avisos_nuevos() -> dict[str, list]:
    return {"apago": [], "prendio": [], "pidio": [], "con_stock": [], "solto": [], "corte": [],
            "sin_marca": [], "duda": [], "tope": [], "pausa": [], "huerfana": [], "auditoria": [],
            "no_quedo": []}


class Contexto:
    """Lo que el seguro va decidiendo dentro de UN evento del fan-out. Las filas
    se sellan al cerrar, con el `ts` del evento, para que el panel las junte."""

    __slots__ = ("sku", "motivo", "stock_drop", "objetivo", "filas", "vuelta",
                 "presupuesto", "avisos", "manual", "detalle", "ensayo")

    def __init__(self, sku: str, motivo: str, stock_drop: Any, objetivo: Any,
                 vuelta: dict[str, Any] | None = None, manual: bool = False):
        self.sku = sku
        self.motivo = motivo
        self.stock_drop = stock_drop
        self.objetivo = objetivo
        self.filas: list[dict[str, Any]] = []
        self.vuelta = vuelta
        # De dónde sale el tope «por vuelta». En el barrido, de su vuelta; en los
        # excedentes del MISMO censo, de esa misma vuelta (`abrir`); en un evento
        # suelto no hay: lo acota el tope del día.
        self.presupuesto = vuelta
        # En un barrido los avisos son de la VUELTA (una campana, no una por SKU).
        self.avisos: dict[str, list] = vuelta["avisos"] if vuelta else _avisos_nuevos()
        self.manual = manual
        self.detalle: dict[str, Any] = {}
        # Por canal: ¿este evento corre en ensayo? En ensayo TODAS sus filas van
        # con `dry_run`, no sólo la de «apagaría».
        self.ensayo: dict[str, bool] = {}


def _presupuesto_de(motivo: Any) -> dict[str, Any] | None:
    """La vuelta que dejó abierta el barrido, para los excedentes del mismo censo
    (`excedente:<canal>`). Sin esto el tope de la vuelta sólo existía dentro del
    barrido: con 30 publicaciones ofreciendo piezas y Woo en 0, el barrido apagaba
    5 y los excedentes, acto seguido, las demás."""
    m = str(motivo or "")
    if not m.startswith("excedente:"):
        return None
    canal = m.split(":", 1)[1].strip().lower()
    with _lock:
        p = _presupuestos.get(canal)
        if p and 0 <= _ahora() - p["t"] < _PRESUPUESTO_S:
            return p["vuelta"]
    return None


def abrir(sku: str, motivo: str, plan: dict[str, Any] | None,
          vuelta: dict[str, Any] | None = None, manual: bool = False) -> Contexto | None:
    """Abre el contexto de un evento. None si el seguro está apagado: entonces
    `antes`, `despues` y `cerrar` no hacen nada."""
    if not habilitado():
        return None
    p = plan or {}
    ctx = Contexto(str(sku or "").strip(), motivo, p.get("stock_drop"), p.get("objetivo"),
                   vuelta, manual)
    if vuelta is None and not manual:
        ctx.presupuesto = _presupuesto_de(motivo)
    return ctx


def _anotar(ctx: Contexto, a: dict[str, Any], accion: str, resultado: str,
            stock: Any = None, real: bool = False, una_al_dia: str | None = None) -> bool:
    """Suma una fila al evento; en ensayo va con `dry_run`. `real` = hubo una
    llamada que SÍ movió el canal. Las `cero_omitir`, las de ensayo y las que
    piden `una_al_dia` (el prefijo de su resultado) se anotan una vez cada 24 h
    por publicación. Devuelve False si la fila se deduplicó."""
    canal = (a.get("canal") or "").lower()
    item = str(a.get("item_id") or "")
    ensayo_ = bool(ctx.ensayo.get(canal, False))
    dedupe = una_al_dia if una_al_dia is not None else ("" if accion == ACC_OMITIR or ensayo_ else None)
    if dedupe is not None and _repetida(canal, item, accion, ensayo_, dedupe):
        return False
    ctx.filas.append({
        "canal": canal, "cuenta": a.get("cuenta"), "item_id": item, "accion": accion,
        "resultado": str(resultado)[:255], "dry_run": bool(ensayo_),
        "stock_canal": stock if stock is not None else a.get("stock_actual_canal"),
        "real": bool(real), "dedupe": dedupe,
    })
    return True


def _sellar_intento(ctx: Contexto, a: dict[str, Any], canal: str, que: str, estado: str,
                    stock: Any = None, variante: Any = None) -> bool:
    """Sella la fila del INTENTO, ya, con su propia hora, ANTES de llamar al canal.
    Si no entra en la bitácora devuelve False y quien llama NO actúa."""
    fila = {"ts": datetime.now(timezone.utc).replace(tzinfo=None), "sku": ctx.sku,
            "motivo": str(ctx.motivo or "")[:160], "dry_run": False,
            "stock_drop": ctx.stock_drop, "objetivo": ctx.objetivo, "canal": canal,
            "cuenta": a.get("cuenta"), "item_id": str(a.get("item_id") or ""),
            "accion": ACC_INTENTO, "stock_canal": stock,
            "resultado": f"{que} (vivo {estado}" + (f" · variante {variante}" if variante is not None else "") + ")",
            "ms": None}
    return sellar(fila)


def cerrar(ctx: Contexto | None, ts: datetime | None = None, ms: Any = None) -> None:
    """Sella las filas del evento (con su `ts`) y, fuera de un barrido, avisa."""
    if ctx is None:
        return
    ts = ts or datetime.now(timezone.utc).replace(tzinfo=None)
    for f in ctx.filas:
        if f.get("sellada"):
            continue
        fila = {"ts": ts, "sku": ctx.sku, "motivo": str(ctx.motivo or "")[:160],
                "dry_run": f["dry_run"], "stock_drop": ctx.stock_drop,
                "objetivo": ctx.objetivo, "canal": f["canal"], "cuenta": f["cuenta"],
                "item_id": f["item_id"], "accion": f["accion"],
                "stock_canal": f["stock_canal"], "resultado": f["resultado"], "ms": ms}
        entro = sellar(fila)
        f["sellada"] = entro
        if entro:
            if f.get("dedupe") is not None:
                with _lock:
                    _vistas[(f["canal"], f["item_id"], f["accion"], f["dry_run"], f["dedupe"])] = _ahora()
            if f["accion"] in _ACC_MARCA and not f["dry_run"]:
                _olvidar_marcas()
        elif f.get("real") and f["accion"] == ACC_INACTIVAR:
            # El canal SÍ la apagó y la marca no entró. Su INTENTO sí está en la
            # bitácora (sin él no se habría llamado): el barrido del siguiente censo
            # lo encuentra sin cierre, lee el canal y sella la marca. Se avisa igual.
            ctx.avisos["sin_marca"].append((f["canal"], ctx.sku))
            log.error("seguro: %s quedó APAGADA en %s y su marca no entró en la bitácora "
                      "(item %s): la sella el siguiente censo; si no, reactivar a mano",
                      ctx.sku, f["canal"], f["item_id"])
    if ctx.vuelta is None:
        _emitir_avisos(ctx.avisos)


def _lista(skus: list[str]) -> str:
    return ", ".join(skus[:8]) + (f" y {len(skus) - 8} más" if len(skus) > 8 else "")


def _avisar_juntas(alertas, clave: str, canal: str, nuevos: list[str], texto, nivel: str,
                   vaciar: bool) -> None:
    """
    «Apagó» y «regresó»: UN aviso por canal con todos los SKUs que falten por decir.

    `alertas.avisar` deja pasar un aviso por tipo cada 60 min y se traga los demás.
    Con el tipo por canal, el segundo SKU de la hora no salía NUNCA (medido: tres
    eventos en Temu daban 1 mensaje con 1 SKU y dos suprimidos). Aquí lo que no
    alcanza a salir se queda esperando y viaja en el siguiente aviso del canal; el
    barrido de cada censo (`vaciar`) empuja lo que siga pendiente.
    """
    with _lock:
        pend = _avisos_pend.setdefault((clave, canal), [])
        for s in nuevos:
            if s not in pend:
                pend.append(s)
        del pend[:-_AVISOS_MAX]
        lista = list(pend)
    if not lista or not (nuevos or vaciar):
        return
    if alertas.avisar(f"seguro_cero_{clave}:{canal}", texto(lista), nivel=nivel):
        with _lock:
            _avisos_pend[(clave, canal)] = [s for s in _avisos_pend.get((clave, canal), [])
                                            if s not in lista]


def _una_vez_al_dia(*clave) -> bool:
    """True la primera vez que se pregunta por `clave` hoy (en este proceso)."""
    marca_ = (_hoy(), *clave)
    with _lock:
        if marca_ in _avisos_dia:
            return False
        for vieja in [k for k in _avisos_dia if k[0] != marca_[0]]:
            _avisos_dia.discard(vieja)
        _avisos_dia.add(marca_)
        return True


def _emitir_avisos(avisos: dict[str, list], vaciar: bool = False) -> None:
    """La campana (Slack, `alertas.avisar`). «Apagó», «regresó», «pidió regresar» y
    «apagadas con stock» van juntas por canal, en un RESUMEN que no pierde SKUs
    (`_avisar_juntas`); lo que deja una TAREA para una persona (soltó, errores, sin
    marca, duda, auditoría) va por SKU, para que el candado anti-spam de un SKU no
    se trague el de otro. Jamás lanza."""
    try:
        from services import alertas
    except Exception:  # noqa: BLE001
        return

    def _por_canal(clave: str) -> dict[str, list[str]]:
        por: dict[str, list[str]] = {c: [] for c in CANALES}
        for canal, sku in avisos.get(clave) or []:
            por.setdefault(canal, []).append(sku)
        return por

    try:
        for canal, skus in _por_canal("apago").items():
            nombre = NOMBRE.get(canal, canal)
            _avisar_juntas(alertas, "apago", canal, skus,
                           lambda l, n=nombre: f"*Seguro stock 0* apagó {len(l)} en {n}: {_lista(l)}",
                           "🟡", vaciar)
        for canal, skus in _por_canal("prendio").items():
            nombre = NOMBRE.get(canal, canal)
            _avisar_juntas(alertas, "prendio", canal, skus,
                           lambda l, n=nombre: (f"*Seguro stock 0* regresó a la venta {len(l)} en "
                                                f"{n}: {_lista(l)}"),
                           "🟢", vaciar)
        for canal, skus in _por_canal("pidio").items():
            nombre = NOMBRE.get(canal, canal)
            _avisar_juntas(alertas, "pidio", canal, skus,
                           lambda l, n=nombre: (f"*Seguro stock 0* pidió regresar a la venta {len(l)} en "
                                                f"{n}: {_lista(l)}. Quedan en auditoría del canal (PENDING): "
                                                f"todavía NO venden. Si la auditoría las rechaza, avisa."),
                           "🟡", vaciar)
        for canal, skus in _por_canal("con_stock").items():
            nombre = NOMBRE.get(canal, canal)
            _avisar_juntas(alertas, "con_stock", canal, skus,
                           lambda l, n=nombre: (f"*Seguro stock 0*: {len(l)} en {n} siguen APAGADAS por el "
                                                f"seguro y Woo ya tiene stock: {_lista(l)}. No están regresando "
                                                f"solas: reactivarlas a mano (la lista y el porqué de cada una, "
                                                f"en el panel y en /api/fanout/seguro)."),
                           "🟡", vaciar)
        for canal, sku, por_que in avisos.get("solto") or []:
            alertas.avisar(f"seguro_cero_solto:{canal}:{sku}",
                           f"*Seguro stock 0* soltó {sku} en {NOMBRE.get(canal, canal)}: {por_que}. "
                           f"Ya NO la reactiva solo: si debe venderse y sigue apagada, reactívala a mano.",
                           nivel="🟡")
        for canal, sku, quedo in avisos.get("no_quedo") or []:
            alertas.avisar(f"seguro_cero_no_quedo:{canal}:{sku}",
                           f"*Seguro stock 0*: le pidió a {NOMBRE.get(canal, canal)} regresar {sku} a la "
                           f"venta y quedó en {quedo}, que NO es a la venta. No se da por reactivada: sigue "
                           f"en la lista de apagadas por el seguro. Revisarla a mano.", nivel="🟡")
        for canal, sku, que in avisos.get("auditoria") or []:
            alertas.avisar(f"seguro_cero_auditoria:{canal}:{sku}",
                           f"*Seguro stock 0*: {sku} NO regresó a la venta en {NOMBRE.get(canal, canal)}: "
                           f"{que}. El seguro ya la dio por reactivada y no la vuelve a tocar: revisarla "
                           f"a mano.", nivel="🔴")
        for canal, sku in avisos.get("corte") or []:
            alertas.avisar(f"seguro_cero_errores:{canal}:{sku}",
                           f"*Seguro stock 0*: 3 errores seguidos con {sku} en "
                           f"{NOMBRE.get(canal, canal)}. No se vuelve a intentar hasta revisarlo "
                           f"a mano (soltarla lo destraba).", nivel="🔴")
        for canal, sku, que in avisos.get("duda") or []:
            alertas.avisar(f"seguro_cero_duda:{canal}:{sku}",
                           f"*Seguro stock 0*: {NOMBRE.get(canal, canal)} contestó bien al {que} {sku}, "
                           f"pero la publicación no cambió de estado en la espera. Quedó SIN marca; "
                           f"el siguiente censo la vuelve a mirar y, si ya cambió, la sella.", nivel="🟡")
        for canal, sku in avisos.get("sin_marca") or []:
            alertas.avisar(f"seguro_cero_sin_marca:{canal}:{sku}",
                           f"*Seguro stock 0*: {sku} quedó APAGADA en {NOMBRE.get(canal, canal)} "
                           f"y su marca no está en la bitácora (kubera no contestó, o el proceso se "
                           f"reinició a media vuelta). El siguiente censo intenta sellarla; si este "
                           f"aviso se repite, reactivarla a mano.", nivel="🔴")
        for canal, accion, tope in avisos.get("tope") or []:
            if not _una_vez_al_dia("tope", canal, accion):
                continue
            if accion == ACC_INACTIVAR:
                alertas.avisar(f"seguro_cero_tope:{canal}",
                               f"*Seguro stock 0* llegó a su tope de {tope} apagados hoy en "
                               f"{NOMBRE.get(canal, canal)} y ya no apaga más hasta mañana. Tantos SKUs "
                               f"en 0 en un día no es normal: revisar qué vació Woo (¿una pasada de "
                               f"stock_watch con ceros?) antes de subir FANOUT_CERO_TOPE_DIA.", nivel="🔴")
            else:
                alertas.avisar(f"seguro_cero_tope_reactivar:{canal}",
                               f"*Seguro stock 0* llegó a su tope de {tope} reactivaciones hoy en "
                               f"{NOMBRE.get(canal, canal)}: lo que falte regresa mañana "
                               f"(FANOUT_CERO_TOPE_REACTIVAR_DIA).", nivel="🟡")
        for canal, minutos in avisos.get("pausa") or []:
            if _una_vez_al_dia("pausa", canal):
                alertas.avisar(f"seguro_cero_pausa:{canal}",
                               f"*Seguro stock 0*: {NOMBRE.get(canal, canal)} contestó «ok» a "
                               f"{_NO_CONVERGE_MAX} apagados seguidos y ninguno se vio fuera de la venta. "
                               f"Deja de pedirle apagados {minutos} min. Revisar a mano cómo quedaron.",
                               nivel="🔴")
        for canal, sku, de_marca, actual in avisos.get("huerfana") or []:
            if _una_vez_al_dia("huerfana", canal, str(de_marca)):
                alertas.avisar(f"seguro_cero_huerfana:{canal}:{sku}",
                               f"*Seguro stock 0*: la publicación de {sku} que el seguro apagó en "
                               f"{NOMBRE.get(canal, canal)} ({de_marca}) ya no es la que el censo tiene "
                               f"para ese SKU ({actual or 'ninguna'}). No la va a reactivar sola: "
                               f"reactivarla a mano y soltar la marca.", nivel="🟡")
    except Exception as exc:  # noqa: BLE001
        log.warning("seguro: avisos: %s", exc)


# ── ANTES del escritor: objetivo 0 ⇒ sacar de la venta ───────────────────────

def _es_destino(ctx: Contexto | None, a: dict[str, Any]) -> str | None:
    """El canal del destino si al seguro le toca mirarlo; None si no."""
    if ctx is None or not ctx.sku:
        return None
    canal = (a.get("canal") or "").lower()
    if canal not in CANALES or not canal_encendido(canal):
        return None
    # FULL, borrada, borrador de Temu: lo que el fan-out ya descarta por destino.
    if a.get("fuera") or not str(a.get("item_id") or "").strip():
        return None
    if not _alcanza(ctx.sku):
        return None
    return canal


def antes(ctx: Contexto | None, a: dict[str, Any]) -> None:
    """Lo llama el fan-out ANTES de escribirle el stock a un destino. Si el
    objetivo es 0, saca la publicación de la venta. No depende de la `accion` del
    plan: vale para `escribir` y para `sin_cambio` (ACC-0574-LIL vendió con el
    plan diciendo «el canal ya tiene 0»)."""
    canal = _es_destino(ctx, a)
    if canal is None:
        return
    objetivo = a.get("objetivo")
    if objetivo is None or int(objetivo) != 0:
        return                      # None ≠ 0: sin stock legible no se actúa
    _inactivar(ctx, a, canal)


def _sin_sondear(canal: str, estado: str) -> str | None:
    """El texto de «no la toco» para los estados de Temu que no se apagan pero
    desde los que la publicación puede volver sola a la venta. None si no aplica."""
    if canal != "temu" or estado in estados_temu() or estado not in _TEMU_VIGILADOS:
        return None
    return (f"Temu {estado} ({_TEMU_VIGILADOS[estado]}) sin sondear: no se apaga desde ese estado; "
            "si Temu la regresa a la venta, la apaga el barrido del siguiente censo")


def _en_pausa(canal: str) -> int:
    """Minutos que le quedan a la pausa del canal (0 = no hay)."""
    with _lock:
        falta = float((_pausas.get(canal) or {}).get("hasta") or 0.0) - _ahora()
    return max(1, math.ceil(falta / 60)) if falta > 0 else 0


def _no_convergio(canal: str) -> bool:
    """Anota un «no convergió» del canal. True si con éste se abre la pausa: el
    canal contesta «ok» y no se ve nada; seguir llamando sólo gasta el tope y
    puede estar dejando publicaciones apagadas sin marca."""
    with _lock:
        p = _pausas.setdefault(canal, {"seguidas": 0, "hasta": 0.0})
        p["seguidas"] += 1
        if p["seguidas"] < _NO_CONVERGE_MAX:
            return False
        p.update(seguidas=0, hasta=_ahora() + _PAUSA_S)
        return True


def _convergio(canal: str) -> None:
    with _lock:
        _pausas.pop(canal, None)


def _sueltos_en_la_hora(canal: str) -> int:
    """Intentos de apagar de la última hora hechos FUERA de un censo (en memoria:
    un reinicio lo pone en 0, y ahí manda el tope del día, que sí es durable)."""
    desde = _ahora() - _HORA_S
    with _lock:
        vivos = [t for t in _sueltos.get(canal, []) if t > desde]
        _sueltos[canal] = vivos
        return len(vivos)


def _contar_suelto(canal: str) -> None:
    with _lock:
        _sueltos.setdefault(canal, []).append(_ahora())


def _inactivar(ctx: Contexto, a: dict[str, Any], canal: str) -> None:
    sku, item = ctx.sku, str(a.get("item_id"))
    censo = str(a.get("estado_canal") or "")
    en_ensayo, _forzado = ensayo(canal)
    ctx.ensayo[canal] = en_ensayo

    # 1 · Prefiltro SIN RED: sólo se sigue si el censo la da por apagable, o no
    #     sabe. Así las 3/2, DRAFT y similares no cuestan llamadas ni filas. Las
    #     que pueden volver solas a la venta (3/1, 3/3, 2/4) dejan UNA fila al día,
    #     para que el ensayo enseñe cuántas son.
    if not ctx.manual and censo and not apagable(canal, censo):
        aviso = _sin_sondear(canal, censo)
        if aviso:
            _anotar(ctx, a, ACC_OMITIR, aviso)
        return
    if sku.upper() in _excluidos():
        _anotar(ctx, a, ACC_OMITIR, "excluido (FANOUT_CERO_EXCLUIR)")
        return
    if en_ensayo and _repetida(canal, item, ACC_INACTIVAR, True):
        return                      # ya se anotó hoy lo que haría: ni se lee
    try:
        cortado = _cortado(canal, item)
    except Exception as exc:  # noqa: BLE001
        _anotar(ctx, a, ACC_ERROR, f"ERROR: no pude leer la bitácora ({type(exc).__name__}); no se actúa")
        return
    if cortado:
        if _anotar(ctx, a, ACC_OMITIR, "3 errores seguidos en 24 h, revisar a mano (soltarla lo destraba)"):
            ctx.avisos["corte"].append((canal, sku))
        return
    padre = es_padre(sku)
    if padre is not False:
        _anotar(ctx, a, ACC_OMITIR,
                "SKU padre: nunca se opera sobre el padre" if padre
                else "SKU padre: no pude determinar si tiene variantes; no se toca")
        return
    tope_vuelta = _entero("fanout_cero_tope_vuelta", 5)
    tope_dia = _entero("fanout_cero_tope_dia", 5)
    vuelta = ctx.presupuesto
    # Tope de la VUELTA (el barrido de después del censo y sus excedentes):
    # agotado, ya ni se lee el canal. En ensayo no corta: ahí se quiere ver el
    # tamaño real.
    if not en_ensayo and vuelta is not None and vuelta["reales"] >= tope_vuelta:
        _anotar(ctx, a, ACC_OMITIR, f"tope de la vuelta ({tope_vuelta})")
        return
    # Un evento SUELTO del fan-out (fuera de un censo) no tiene vuelta: el mismo
    # número vale POR HORA. Lo que no alcance lo apaga el barrido del siguiente
    # censo, que lleva su propio tope. Sin esto, 40 SKUs que caen a 0 de golpe
    # eran 40 llamadas en una sola tanda (medido con el canal falseado).
    suelto = vuelta is None and not ctx.manual
    if not en_ensayo and suelto and _sueltos_en_la_hora(canal) >= tope_vuelta:
        _anotar(ctx, a, ACC_OMITIR,
                f"tope por hora ({tope_vuelta}): la apaga el barrido del siguiente censo")
        return
    # El canal lleva dos «ok» seguidos sin que se vea nada: no se le pide más un
    # rato. A mano (canario) sí: el sondeo quiere ver justo eso.
    if not en_ensayo and not ctx.manual:
        falta = _en_pausa(canal)
        if falta:
            _anotar(ctx, a, ACC_OMITIR,
                    f"pausa: {NOMBRE.get(canal, canal)} no confirmó {_NO_CONVERGE_MAX} apagados seguidos; "
                    f"se reintenta en {falta} min")
            return

    with _candado(canal, item):
        # 2 · Lectura EN VIVO obligatoria.
        vivo = _leer_vivo(canal, item)
        if not vivo.get("ok"):
            _anotar(ctx, a, ACC_ERROR, f"ERROR: lectura en vivo ilegible: {vivo.get('motivo')}")
            return
        estado = str(vivo.get("estado") or "")
        stock = _stock_vivo(vivo, sku)
        ctx.detalle.update(antes=estado, stock_vivo=stock)
        # A mano (canario) también se dejan probar los estados sin sondear de Temu
        # (3/1, 3/3, 2/4): es justo el sondeo.
        permitido = apagable(canal, estado) or (ctx.manual and canal == "temu" and estado in _TEMU_VIGILADOS)
        if not permitido:
            aviso = _sin_sondear(canal, estado)
            if aviso:
                _anotar(ctx, a, ACC_OMITIR, aviso, stock=stock)
            elif not a_la_venta(canal, estado):
                try:
                    propia = marca(canal, item)
                except Exception:  # noqa: BLE001
                    propia = None
                if propia:
                    _anotar(ctx, a, ACC_SIN_CAMBIO,
                            f"ya estaba fuera (marca vigente; vivo {estado})", stock=stock)
            return

        # 3 · Nivel: ¿la publicación, una variante, o nada?
        skus = vivo.get("skus") or []
        nivel = decidir_nivel(
            canal, sku, skus,
            _objetivos_hermanas(sku, skus) if canal == "tiktok" and len(skus) > 1 else {},
            nivel_sku_temu=bool(getattr(settings, "fanout_cero_temu_nivel_sku", False)))
        if not nivel["nivel"]:
            _anotar(ctx, a, ACC_OMITIR, str(nivel["motivo"]), stock=stock)
            return
        variante = (nivel.get("sku_ids") or [None])[0] if nivel["nivel"] == "variante" else None
        if variante is not None:
            # Por variante el goods sigue «a la venta»: el estado vivo no dice si
            # ESA variante ya se apagó. Lo dice la marca; sin poder leerla, no se repite.
            try:
                previa = leer_marca((marca(canal, item) or {}).get("resultado")) or {}
            except Exception as exc:  # noqa: BLE001
                _anotar(ctx, a, ACC_ERROR,
                        f"ERROR: no pude leer la marca ({type(exc).__name__}); no se repite la baja de la variante", stock=stock)
                return
            if str(previa.get("variante")) == str(variante):
                _anotar(ctx, a, ACC_SIN_CAMBIO, f"ya estaba fuera (marca vigente; variante {variante})", stock=stock)
                return

        # 4 · Ensayo: se anota lo que haría, con `dry_run`, y nada más. Los topes
        #     no cortan, pero se dice desde cuál quedaría fuera (el tamaño real).
        if en_ensayo:
            extra = ""
            if vuelta is not None and vuelta["ensayos"] >= tope_vuelta:
                extra = " · fuera de tope de la vuelta"
            else:
                try:
                    if usadas_hoy(canal, ACC_INACTIVAR, ensayos=True) >= tope_dia:
                        extra = " · fuera de tope del día"
                except Exception:  # noqa: BLE001
                    pass
            _anotar(ctx, a, ACC_INACTIVAR,
                    f"ENSAYO (apagaría; vivo {estado}, ofrece {stock if stock is not None else '?'})" + extra, stock=stock)
            if vuelta is not None:
                vuelta["ensayos"] += 1
            return

        # 5 · Tope del día: se lee en kubera ANTES de llamar al canal; si esa
        #     lectura falla, no se actúa. Cuenta INTENTOS (llamadas), no éxitos.
        try:
            usadas = _usadas(canal, ACC_INACTIVAR)
        except Exception as exc:  # noqa: BLE001
            _anotar(ctx, a, ACC_ERROR,
                    f"ERROR: no pude leer el tope del día en kubera ({type(exc).__name__}); no se actúa",
                    stock=stock)
            return
        if usadas >= tope_dia:
            _anotar(ctx, a, ACC_OMITIR, f"tope del día ({tope_dia})", stock=stock)
            # Llegar al tope es la señal de que algo masivo vació Woo: se avisa.
            ctx.avisos["tope"].append((canal, ACC_INACTIVAR, tope_dia))
            return

        # 6 · EL INTENTO, sellado ANTES de llamar: sin esa fila no se llama. Es lo
        #     que cuenta el tope y lo que deja rastro si el proceso muere a media
        #     vuelta. Luego: sacarla de la venta, esperar y RELEER.
        if vuelta is not None:
            vuelta["reales"] += 1
        elif suelto:
            _contar_suelto(canal)
        if not _sellar_intento(ctx, a, canal, INTENTO_APAGAR, estado, stock, variante):
            _anotar(ctx, a, ACC_ERROR,
                    "ERROR: no pude sellar el intento en la bitácora; no se llama al canal", stock=stock)
            return
        _contar_intento(canal, INTENTO_APAGAR)
        try:
            respuesta = _apagar(canal, item, nivel.get("sku_ids"))
        except Exception as exc:  # noqa: BLE001
            ctx.detalle.update(error=str(exc)[:400])
            _anotar(ctx, a, ACC_ERROR, f"{ERR_RECHAZO}: {type(exc).__name__}: {exc}", stock=stock)
            return
        ctx.detalle.update(respuesta=str(respuesta)[:400])
        nuevo, releida = _releer(
            canal, item,
            (lambda e: True) if variante is not None
            else (lambda e: e != estado and not a_la_venta(canal, e)))
        ctx.detalle.update(despues=nuevo or f"ilegible: {releida.get('motivo')}")
        if variante is not None:
            # Por variante el goods sigue a la venta: no hay estado que releer.
            # Vale el veredicto de la llamada (`result.success`).
            resultado = f"ok ({estado}→{nuevo or estado} · variante {variante})"
        elif not nuevo or nuevo == estado or a_la_venta(canal, nuevo):
            _anotar(ctx, a, ACC_ERROR,
                    f"{ERR_NO_CONVERGE}: tras apagar sigue en {nuevo or 'estado ilegible'}",
                    stock=stock)
            # El canal dijo «ok» y no se ve el cambio: si en realidad sí quedó
            # apagada, quedó SIN marca. El barrido del siguiente censo la vuelve a
            # leer y, si ya cambió, sella la marca tarde.
            ctx.avisos["duda"].append((canal, sku, "apagar"))
            if _no_convergio(canal):
                ctx.avisos["pausa"].append((canal, round(_PAUSA_S / 60)))
            return
        else:
            resultado = f"ok ({estado}→{nuevo})"
            _reflejar(canal, sku, item, nuevo)
            _convergio(canal)
        _anotar(ctx, a, ACC_INACTIVAR, resultado, stock=stock, real=True)
        ctx.avisos["apago"].append((canal, sku))
        log.warning("seguro stock 0: %s APAGADA en %s (%s) — item %s, ofrecía %s",
                    sku, canal, resultado, item, stock)


# ── DESPUÉS del escritor: stock de vuelta ⇒ regresar lo que apagó el seguro ──

def despues(ctx: Contexto | None, a: dict[str, Any], escritura_ok: bool | None = None) -> None:
    """Lo llama el fan-out DESPUÉS de escribirle el stock a un destino.
    `escritura_ok` = el escritor dio «ok» (None = no se escribió). Si el objetivo
    es mayor que 0 y la publicación lleva la marca del seguro, evalúa reactivarla.

    Sin FANOUT_CERO_REACTIVAR el seguro sólo apaga y aquí no pasa nada; lo que se
    quedó apagado y ya tiene stock lo dice el BARRIDO de cada censo
    (`_aviso_con_stock`: una fila al día y un resumen a Slack), que mira un stock
    que ya se sostuvo y no un parpadeo."""
    canal = _es_destino(ctx, a)
    if canal is None:
        return
    objetivo = a.get("objetivo")
    if objetivo is None or int(objetivo) <= 0:
        return
    if not getattr(settings, "fanout_cero_reactivar", False):
        return
    _reactivar(ctx, a, canal, escritura_ok)


def _reactivar(ctx: Contexto, a: dict[str, Any], canal: str,
               escritura_ok: bool | None = None) -> None:
    sku, item = ctx.sku, str(a.get("item_id"))
    objetivo = a.get("objetivo")
    en_ensayo, _forzado = ensayo(canal)
    ctx.ensayo[canal] = en_ensayo

    # 1 · MARCA vigente. Sin marca no hay nada que hacer ni que anotar; con la
    #     bitácora muda, «no sé» = no se reactiva.
    try:
        if not ctx.manual and (canal, item) not in _marcas_por_item():
            return
        propia = marca(canal, item)
    except Exception as exc:  # noqa: BLE001
        log.warning("seguro: no pude leer la marca de %s/%s (%s): no se reactiva", canal, sku, exc)
        return
    if not propia:
        ctx.detalle.update(motivo="sin marca vigente del seguro: no se reactiva")
        return
    dejado = leer_marca(propia.get("resultado"))
    if not dejado:
        _anotar(ctx, a, ACC_OMITIR, "marca ilegible: no se reactiva sola, reactivar a mano")
        return
    if sku.upper() in _excluidos():
        _anotar(ctx, a, ACC_OMITIR, "excluido (FANOUT_CERO_EXCLUIR)")
        return
    if en_ensayo and _repetida(canal, item, ACC_REACTIVAR, True):
        return
    try:
        if _cortado(canal, item):
            if _anotar(ctx, a, ACC_OMITIR, "3 errores seguidos en 24 h, revisar a mano (soltarla lo destraba)"):
                ctx.avisos["corte"].append((canal, sku))
            return
        # 3 · ¿Alguien más movió el estado después de la marca? (Lo que TERMINA
        #     en el estado que dejó el seguro es el censo viendo lo propio.)
        ajeno = cambio_ajeno(canal, sku, propia.get("ts"), _propios(canal, dejado["a"]))
        # 3b · ¿Alguien apagó el canal EN BLOQUE después de la marca? Eso no le
        #      cambia el estado a ésta (ya estaba apagada), así que no deja rastro
        #      en ella: se ve en las demás. A mano (canario) no se pregunta.
        bloque = None if (ajeno or ctx.manual) else apagado_en_bloque(canal, propia.get("ts"))
        # 4 · Objetivo > 0 SOSTENIDO (anti-parpadeo).
        edad = None if ctx.manual else edad_con_stock_s(sku)
    except Exception as exc:  # noqa: BLE001
        _anotar(ctx, a, ACC_ERROR, f"ERROR: no pude leer la bitácora ({type(exc).__name__}); no se reactiva")
        return
    if ajeno:
        ya_vende = False
        if a_la_venta(canal, ajeno.get("valor_nuevo")):
            # El cambio ajeno la dejó A LA VENTA (en Temu, casi siempre la propia
            # plataforma regresándola de `3/3`). Si lo sigue estando, no queda
            # tarea para nadie: se suelta la marca y no se manda aviso.
            with _candado(canal, item):
                vivo = _leer_vivo(canal, item)
            ya_vende = bool(vivo.get("ok")) and a_la_venta(canal, vivo.get("estado"))
        if _anotar(ctx, a, ACC_SOLTAR,
                   (f"ok (estado movido por {ajeno.get('via') or 'otro'}: "
                    f"{ajeno.get('valor_anterior')}→{ajeno.get('valor_nuevo')}; no se reactiva)")
                   ) and not en_ensayo and not ya_vende:
            ctx.avisos["solto"].append((canal, sku, (
                f"alguien más movió su estado ({ajeno.get('valor_anterior')}→{ajeno.get('valor_nuevo')}, "
                f"visto por {ajeno.get('via') or 'otro'})")))
        return
    if bloque:
        if _anotar(ctx, a, ACC_SOLTAR,
                   (f"ok (apagado en bloque: {bloque.get('n')} publicaciones de {NOMBRE.get(canal, canal)} "
                    f"se apagaron a mano hacia {bloque.get('hora')}; no se reactiva)")) and not en_ensayo:
            ctx.avisos["solto"].append((canal, sku, (
                f"después de que el seguro la apagó, alguien apagó {bloque.get('n')} publicaciones de "
                f"{NOMBRE.get(canal, canal)} a la vez (hacia {bloque.get('hora')}); regresarla iría contra "
                f"esa pausa")))
        return
    if not ctx.manual:
        espera_s = _entero("fanout_cero_espera_min", 30) * 60
        if edad is None or edad < espera_s:
            falta = espera_s - (edad or 0.0)
            if not en_ensayo:           # el ensayo sólo mira: no devuelve nada a la cola
                _programar(sku, falta)
            _anotar(ctx, a, ACC_SIN_CAMBIO,
                    f"espera {max(1, math.ceil(falta / 60))} min: el stock debe sostenerse antes de reactivar")
            return

    with _candado(canal, item):
        # 2 · Estado VIVO igual al que dejó el seguro (quedó escrito en la marca).
        vivo = _leer_vivo(canal, item)
        if not vivo.get("ok"):
            _anotar(ctx, a, ACC_ERROR, f"ERROR: lectura en vivo ilegible: {vivo.get('motivo')}")
            return
        estado = str(vivo.get("estado") or "")
        stock = _stock_vivo(vivo, sku)
        variante = dejado.get("variante")
        ctx.detalle.update(antes=estado, stock_vivo=stock)
        if variante is None:
            suelta = por_que = None
            if a_la_venta(canal, estado):
                # Ya vende: no queda tarea para nadie, así que no se manda aviso.
                suelta = "ok (la prendió otro)"
            elif estado != dejado["a"] and canal == "temu" and estado in _TEMU_DEL_CANAL:
                # La movió TEMU (agotada o bloqueada), no una persona. La marca se
                # CONSERVA: si Temu la regresa sola, el siguiente evento la cierra; y
                # mientras siga ahí con stock, sale en «apagadas con stock de vuelta».
                # No se le pide regresar desde un estado sin sondear.
                _anotar(ctx, a, ACC_SIN_CAMBIO,
                        (f"{MOVIDA}: Temu la pasó sola a {estado} (el seguro la dejó en {dejado['a']}); "
                         "no se le pide regresar desde ahí y la marca se conserva"),
                        stock=stock, una_al_dia=MOVIDA)
                return
            elif estado != dejado["a"]:
                suelta = f"ok (estado {estado} ajeno; el seguro la dejó en {dejado['a']})"
                por_que = f"la encontró en {estado} y el seguro la había dejado en {dejado['a']}"
            if suelta:
                if _anotar(ctx, a, ACC_SOLTAR, suelta, stock=stock) and not en_ensayo and por_que:
                    ctx.avisos["solto"].append((canal, sku, por_que))
                return

        # 5 · PRIMERO EL STOCK: la escritura dio ok, o el canal ya tiene el objetivo.
        con_stock = escritura_ok is True or (
            stock is not None and objetivo is not None and int(objetivo) > 0
            and int(stock) == int(objetivo))
        if not con_stock and not ctx.manual:
            _anotar(ctx, a, ACC_SIN_CAMBIO,
                    f"esperando stock (vivo {stock if stock is not None else '?'}, objetivo {objetivo})", stock=stock)
            return
        if en_ensayo:
            _anotar(ctx, a, ACC_REACTIVAR,
                    f"ENSAYO (prendería; vivo {estado}, stock {stock if stock is not None else '?'})", stock=stock)
            return

        # 6 · Tope del día (INTENTOS), leído en kubera antes de llamar al canal.
        tope = _entero("fanout_cero_tope_reactivar_dia", 20)
        try:
            usadas = _usadas(canal, ACC_REACTIVAR)
        except Exception as exc:  # noqa: BLE001
            _anotar(ctx, a, ACC_ERROR,
                    f"ERROR: no pude leer el tope del día en kubera ({type(exc).__name__}); no se reactiva",
                    stock=stock)
            return
        if usadas >= tope:
            _anotar(ctx, a, ACC_OMITIR, f"tope de reactivaciones del día ({tope})", stock=stock)
            ctx.avisos["tope"].append((canal, ACC_REACTIVAR, tope))
            return

        # 7 · El intento, sellado antes de llamar (igual que al apagar).
        if not _sellar_intento(ctx, a, canal, INTENTO_PRENDER, estado, stock, variante):
            _anotar(ctx, a, ACC_ERROR,
                    "ERROR: no pude sellar el intento en la bitácora; no se llama al canal", stock=stock)
            return
        _contar_intento(canal, INTENTO_PRENDER)
        try:
            respuesta = _prender(canal, item, [variante] if variante is not None else None)
        except Exception as exc:  # noqa: BLE001
            ctx.detalle.update(error=str(exc)[:400])
            _anotar(ctx, a, ACC_ERROR, f"{ERR_RECHAZO}: {type(exc).__name__}: {exc}", stock=stock)
            return
        ctx.detalle.update(respuesta=str(respuesta)[:400])

        nuevo, releida = _releer(canal, item,
                                 (lambda e: True) if variante is not None
                                 else (lambda e: _prendida(canal, e)))
        ctx.detalle.update(despues=nuevo or f"ilegible: {releida.get('motivo')}")
        if variante is not None:
            resultado = f"ok ({estado}→{nuevo or estado} · variante {variante})"
        elif nuevo and _prendida(canal, nuevo):
            resultado = f"ok ({estado}→{nuevo})"
            _reflejar(canal, sku, item, nuevo)
        elif nuevo and nuevo != estado:
            # El canal obedeció —salió del estado en que el seguro la dejó— pero NO
            # quedó a la venta (medido en 30 días: de las salidas de `3/2` en Temu,
            # 28 llegaron a `2/8`, 8 a `3/3` y 1 a `2/4`). No se anuncia «regresó a
            # la venta» ni se CIERRA la marca: sigue siendo del seguro, así que sale
            # en «apagadas con stock de vuelta» hasta que alguien la vea vender. Si
            # el canal sólo iba tarde, el barrido del siguiente censo la encuentra a
            # la venta y entonces sí la da por reactivada (`_reconciliar`).
            # (No se refleja en `channel.listings` firmado por el seguro: ese estado
            # no lo dejó él a propósito, y `huerfanas` lo tomaría por «apagada sin
            # marca». Lo anota el censo, y `_propios` sabe que no es un cambio ajeno.)
            _anotar(ctx, a, ACC_ERROR,
                    (f"{ERR_NO_CONVERGE}: se le pidió regresar y quedó en {nuevo}, NO a la venta; "
                     "la marca se conserva"), stock=stock)
            ctx.avisos["no_quedo"].append((canal, sku, nuevo))
            log.warning("seguro stock 0: %s NO quedó a la venta en %s (%s→%s) — item %s",
                        sku, canal, estado, nuevo, item)
            return
        else:
            _anotar(ctx, a, ACC_ERROR,
                    f"{ERR_NO_CONVERGE}: tras prender sigue en {nuevo or 'estado ilegible'}",
                    stock=stock)
            ctx.avisos["duda"].append((canal, sku, "prender"))
            return
        _anotar(ctx, a, ACC_REACTIVAR, resultado, stock=stock, real=True)
        # «Regresó a la venta» sólo si de verdad lo está. En TikTok la reactivación
        # pasa por auditoría (PENDING): se dice hasta que el censo la vea ACTIVATE,
        # y si la auditoría la rechaza se avisa en rojo (`_revisar_auditoria`).
        if variante is not None or a_la_venta(canal, nuevo):
            ctx.avisos["prendio"].append((canal, sku))
        else:
            ctx.avisos["pidio"].append((canal, sku))
        log.warning("seguro stock 0: %s %s en %s (%s) — item %s", sku,
                    "de VUELTA a la venta" if (variante is not None or a_la_venta(canal, nuevo))
                    else "pidió regresar y quedó EN AUDITORÍA", canal, resultado, item)


def _propios(canal: str, dejado: str) -> list[str]:
    """Los estados cuya LLEGADA no cuenta como «alguien más la movió»: el que dejó
    el seguro (es el censo viendo lo propio) y, en Temu, los que sólo pone la
    plataforma."""
    return [dejado] + (sorted(_TEMU_DEL_CANAL - {dejado}) if canal == "temu" else [])


def _prendida(canal: str, estado: str) -> bool:
    """¿La reactivación quedó donde debía? En Temu, A LA VENTA (no basta con que
    haya salido del estado en que la dejó el seguro). En TikTok, a la venta o en
    auditoría (PENDING): toda reactivación pasa por ahí."""
    if canal == "tiktok":
        return str(estado or "").upper() in ("ACTIVATE", "PENDING")
    return a_la_venta(canal, estado)


# ── La espera anti-parpadeo ──────────────────────────────────────────────────

def _programar(sku: str, falta_s: float) -> None:
    """Anota cuándo se vuelve a evaluar el SKU. Vive en memoria: si un reinicio
    la borra, el barrido de después del censo la recupera."""
    with _lock:
        _esperas[sku] = _ahora() + max(0.0, float(falta_s)) + _MARGEN_ESPERA_S
    try:
        from services import fanout_stock
        fanout_stock._asegurar_worker()      # quien drena las esperas es su worker
    except Exception:  # noqa: BLE001
        pass


def esperas_vencidas() -> list[str]:
    """Los SKUs cuya espera ya venció (y los saca). Lo pregunta el worker del
    fan-out en cada tic y los vuelve a encolar: `_aplicar` reevalúa todo."""
    ahora = _ahora()
    with _lock:
        listos = [s for s, t in _esperas.items() if t <= ahora]
        for s in listos:
            _esperas.pop(s, None)
    return listos


# ── Barrido de después de cada censo ─────────────────────────────────────────

_SQL_BARRIDO = """
    select l.sku::text as sku, l.listing_id, l.status, l.stock_own
      from channel.listings l join ops.stock_watch_photo p on p.sku = l.sku
     where l.canal = %(c)s and not coalesce(l.is_fulfillment, false)
       and l.listing_id is not null and l.status = any(%(apagables)s)
       and p.stock_woo is not null and p.stock_woo <= 0
     order by (l.stock_own > 0) desc, l.sku limit %(n)s"""


def candidatos(canal: str, limite: int = _BARRIDO_MAX) -> list[dict[str, Any]]:
    """Publicaciones de `canal` A LA VENTA (según el censo) con la foto de Woo en
    0. Primero las que todavía ofrecen piezas."""
    from services import supabase_db as sdb
    canal = (canal or "").lower()
    apagables = sorted(estados_temu()) if canal == "temu" else ["ACTIVATE"]
    return sdb.fetch_all(_SQL_BARRIDO, {"c": canal, "apagables": apagables, "n": int(limite)})


def barrer(canal: str) -> dict[str, Any]:
    """
    Una vuelta para `canal`, al terminar su censo y ANTES de los excedentes.
    SÍNCRONA a propósito: el censo la corre con `asyncio.to_thread` (regla 11).

      0. RECONCILIA lo que quedó a medias: intentos sin cierre (el proceso murió
         entre llamar al canal y sellar) y «no convergió» recientes. Lee el canal
         y sella lo que de verdad pasó.
      1. Apaga lo que está a la venta con Woo en 0. Es lo único que toca lo que
         YA estaba en 0 al encender, y lo que atrapa el regreso solo de Temu
         (`3/1` → `2/8`). Relee Woo con `plan()` antes de actuar.
      2. Mira las marcas vigentes: avisa de las HUÉRFANAS (el censo ya tiene otra
         publicación para ese SKU), devuelve a la cola las que ya tienen stock
         (si la reactivación está encendida) y deja dicho cuáles siguen APAGADAS
         CON STOCK (una fila al día y un resumen a Slack).
      3. TikTok: revisa las reactivaciones que quedaron en auditoría.

    El tope «por vuelta» de aquí vale también para los excedentes que el censo
    corre enseguida (`_presupuesto_de`). Cada paso va en su propio `try`: uno que
    falla no se lleva a los demás ni a los avisos.
    """
    from services import fanout_stock
    canal = (canal or "").lower()
    if canal not in CANALES:
        return {"ok": False, "motivo": f"'{canal}' no es Temu ni TikTok"}
    if not habilitado():
        return {"ok": False, "motivo": "FANOUT_CERO_ENABLED apagado"}
    if not canal_encendido(canal):
        return {"ok": False, "motivo": f"FANOUT_CERO_{canal.upper()} apagado"}
    t0 = _ahora()
    vuelta: dict[str, Any] = {"reales": 0, "ensayos": 0, "avisos": _avisos_nuevos()}
    with _lock:
        _presupuestos[canal] = {"t": t0, "vuelta": vuelta}
    cuenta: dict[str, int] = {}
    muestra: list[str] = []
    solo = _solo_skus()

    def paso(nombre: str, fn, omision: Any = None) -> Any:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — un paso no tumba la vuelta
            cuenta["error"] = cuenta.get("error", 0) + 1
            log.warning("seguro: barrido de %s, %s: %s", canal, nombre, exc)
            return omision

    for k, n in (paso("reconciliar", lambda: _reconciliar(canal, vuelta), {}) or {}).items():
        cuenta[k] = cuenta.get(k, 0) + n

    filas = paso("candidatos", lambda: candidatos(canal), []) or []
    for f in filas:
        sku = str(f["sku"])
        if not _alcanza(sku, solo):
            continue
        ctx = None
        try:
            p = fanout_stock.plan(sku)        # Woo EN VIVO: la foto pudo quedar atrás
            if not p.get("ok") or p.get("objetivo") != 0:
                cuenta["woo_ya_no_es_0"] = cuenta.get("woo_ya_no_es_0", 0) + 1
                continue
            a = next((x for x in p.get("acciones") or []
                      if (x.get("canal") or "").lower() == canal
                      and str(x.get("item_id") or "") == str(f["listing_id"])), None)
            if a is None:
                continue
            ctx = abrir(sku, MOTIVO_BARRIDO.format(canal=canal), p, vuelta=vuelta)
            antes(ctx, a)
        except Exception as exc:  # noqa: BLE001 — una publicación no tumba la vuelta
            cuenta["error"] = cuenta.get("error", 0) + 1
            log.warning("seguro: barrido de %s, %s: %s", canal, sku, exc)
        finally:
            if ctx is not None:
                cerrar(ctx)
                for fila in ctx.filas:
                    clave = fila["accion"] + ("_ensayo" if fila["dry_run"] else "")
                    cuenta[clave] = cuenta.get(clave, 0) + 1
                    if fila["accion"] == ACC_INACTIVAR and len(muestra) < 15:
                        muestra.append(f"{sku}: {fila['resultado']}"[:120])

    marcas = paso("marcas", lambda: apagadas(canal), []) or []
    for m in marcas:
        if m.get("huerfana"):
            vuelta["avisos"]["huerfana"].append(
                (canal, str(m["sku"]), str(m["item_id"]), m.get("listing_actual")))
    reencolados: list[str] = []
    if (getattr(settings, "fanout_cero_reactivar", False) and fanout_stock.habilitado()
            and not ensayo(canal)[0]):      # el ensayo sólo mira: no devuelve nada a la cola
        reencolados = paso("marcas con stock de vuelta",
                           lambda: _reencolar_marcas(canal, solo, marcas), []) or []
    nuevas_con_stock = paso("apagadas con stock",
                            lambda: _aviso_con_stock(canal, vuelta, marcas), []) or []
    if canal == "tiktok":
        paso("auditoría", lambda: _revisar_auditoria(canal, vuelta))

    def _sin_marca() -> None:
        # Segunda red: lo que el seguro dejó apagado (lo firmó en el historial) y
        # no tiene su fila en la bitácora. Se avisa una vez por proceso.
        for h in huerfanas(canal):
            clave = (canal, str(h["sku"]))
            if not a_la_venta(canal, h.get("a")) and clave not in _huerfanas_avisadas:
                _huerfanas_avisadas.add(clave)
                vuelta["avisos"]["sin_marca"].append(clave)
                log.error("seguro: %s está APAGADA en %s por el seguro (%s→%s, %s) y SIN marca "
                          "en la bitácora: reactivar a mano o soltarla", h["sku"], canal,
                          h.get("de"), h.get("a"), h.get("cuando"))
    paso("apagadas sin marca", _sin_marca)

    _emitir_avisos(vuelta["avisos"], vaciar=True)
    salida = {"ok": True, "canal": canal, "candidatos": len(filas), **cuenta,
              "llamadas": vuelta["reales"], "reencolados": reencolados[:20],
              "con_stock": nuevas_con_stock[:20], "muestra": muestra,
              "segundos": round(_ahora() - t0, 1), "ts": _ahora()}
    _ultimo[canal] = salida
    if vuelta["reales"] or cuenta.get("error") or cuenta.get(ACC_ERROR):
        log.warning("Seguro stock 0, barrido de %s: %s", canal, salida)
    return salida


def _reconciliar(canal: str, vuelta: dict[str, Any]) -> dict[str, int]:
    """
    Cierra lo que quedó A MEDIAS. Dos casos, los dos leyendo el canal EN VIVO:

      · un INTENTO sin cierre (nadie selló qué pasó: el contenedor se reinició
        entre la llamada y el sello). Si la publicación quedó como se pidió, se
        sella su marca —tarde, pero queda—; si no, se cierra como error;
      · un «no convergió» reciente: el canal contestó «ok» y no enseñó el cambio
        en la espera. Si ya lo enseña, se ADOPTA (se sella la marca). Si no, se
        deja: lo reintenta el barrido con sus reglas.

    No llama a apagar ni a prender: sólo lee y anota.
    """
    cuenta: dict[str, int] = {}

    def suma(k: str) -> None:
        cuenta[k] = cuenta.get(k, 0) + 1

    for p in pendientes(canal):
        sku, item = str(p.get("sku") or ""), str(p.get("item_id") or "")
        intento = leer_intento(p.get("intento"))
        if not intento or not sku or not item:
            continue
        colgado = p.get("accion") == ACC_INTENTO
        que, de, variante = intento["que"], intento["de"], intento["variante"]
        with _candado(canal, item):
            vivo = _leer_vivo(canal, item)
            if not vivo.get("ok"):
                suma("reconciliar_ilegible")      # se vuelve a mirar en el siguiente censo
                continue
            estado = str(vivo.get("estado") or "")
            stock = _stock_vivo(vivo, sku)
            a = {"canal": canal, "cuenta": p.get("cuenta"), "item_id": item,
                 "stock_actual_canal": stock}
            ctx = Contexto(sku, MOTIVO_TARDE, None, None, vuelta=vuelta)
            if variante is not None:
                # Por variante el goods sigue a la venta: el estado no dice nada.
                if colgado:
                    _anotar(ctx, a, ACC_ERROR,
                            f"{ERR_SIN_CIERRE}: no se puede comprobar la variante {variante}; revisar a mano",
                            stock=stock)
                    vuelta["avisos"]["sin_marca"].append((canal, sku))
            elif (que == INTENTO_APAGAR and estado != de and not a_la_venta(canal, estado)
                  and not (canal == "temu" and estado in _TEMU_DEL_CANAL)):
                # (`3/1` y `3/3` los pone Temu sola —con el 0 ya escrito, casi seguro
                # «agotada»—: verla ahí no prueba que la apagó el seguro.)
                _reflejar(canal, sku, item, estado)
                _anotar(ctx, a, ACC_INACTIVAR, f"ok ({de}→{estado}) · confirmada tarde",
                        stock=stock, real=True)
                vuelta["avisos"]["apago"].append((canal, sku))
                suma("confirmadas_tarde")
                log.warning("seguro stock 0: %s APAGADA en %s (%s→%s), confirmada tarde — item %s",
                            sku, canal, de, estado, item)
            elif que == INTENTO_PRENDER and _prendida(canal, estado):
                _reflejar(canal, sku, item, estado)
                _anotar(ctx, a, ACC_REACTIVAR, f"ok ({de}→{estado}) · confirmada tarde",
                        stock=stock, real=True)
                vuelta["avisos"]["prendio" if a_la_venta(canal, estado) else "pidio"].append((canal, sku))
                suma("confirmadas_tarde")
            elif colgado:
                _anotar(ctx, a, ACC_ERROR,
                        (f"{ERR_SIN_CIERRE}: el proceso se cortó a media vuelta; se le pidió {que} "
                         f"y la publicación sigue en {estado}"), stock=stock)
                suma("sin_cierre")
            cerrar(ctx)
    return cuenta


def _reencolar_marcas(canal: str, solo: set[str], marcas: list[dict[str, Any]]) -> list[str]:
    """Devuelve a la cola del fan-out las marcas vigentes cuyo Woo ya es mayor que
    0 (por si un reinicio borró su espera). Una misma marca, a lo mucho una vez
    al día: una que no puede cerrarse (SKU excluido, destino descartado) se
    reencolaba en cada censo, sin fin. Las huérfanas no se reencolan: el stock iría
    a otra publicación."""
    from services import fanout_stock
    with fanout_stock._lock:
        en_cola = {str(s).upper() for s in fanout_stock._pendientes}
    with _lock:
        esperando = {s.upper() for s in _esperas}
    fuera: list[str] = []
    for m in marcas:
        sku, item = str(m["sku"]), str(m["item_id"])
        woo = m.get("stock_woo")
        if m.get("huerfana") or woo is None or int(woo) <= 0:
            continue
        if sku.upper() in en_cola or sku.upper() in esperando or not _alcanza(sku, solo):
            continue
        with _lock:
            t = _reencoladas.get((canal, item))
            if t is not None and 0 <= _ahora() - t < _REENCOLAR_S:
                continue
            _reencoladas[(canal, item)] = _ahora()
        fanout_stock.encolar(sku, MOTIVO_MARCA)
        fuera.append(sku)
    return fuera


def _aviso_con_stock(canal: str, vuelta: dict[str, Any], marcas: list[dict[str, Any]]) -> list[str]:
    """
    Lo que el seguro tiene APAGADO y ya tiene stock en Woo. Con la reactivación
    apagada (pasos 1 a 3 del encendido) esas publicaciones se quedaban fuera de la
    venta sin ninguna señal: el fan-out les escribía el stock y seguían apagadas,
    con 50 piezas en Woo. Aquí queda una fila al día por publicación
    (`cero_sin_cambio «con stock y apagada…»`) y las NUEVAS de hoy salen juntas en
    un aviso; la lista completa está siempre en `/api/fanout/seguro` y en la matriz.

    Con la reactivación encendida sólo cuentan las que llevan horas con stock: el
    seguro debió regresarlas y algo se lo impide (espera, tope, error, Temu la
    movió a `3/3`…).
    """
    reactiva = bool(getattr(settings, "fanout_cero_reactivar", False)) and not ensayo(canal)[0]
    nuevas: list[str] = []
    for m in marcas:
        woo = m.get("stock_woo")
        if not m.get("fuera") or m.get("huerfana") or woo is None or int(woo) <= 0:
            continue
        sku = str(m["sku"])
        if reactiva:
            try:
                edad = edad_con_stock_s(sku)
            except Exception:  # noqa: BLE001 — sin poder saberlo, no se dice
                edad = None
            if edad is None or edad < _CON_STOCK_TARDE_S:
                continue
            razon = (f"lleva {round(edad / 3600)} h con stock y el seguro no la ha regresado "
                     "(su renglón dice por qué: espera, tope, error o estado movido)")
        else:
            razon = "la reactivación automática está apagada (FANOUT_CERO_REACTIVAR): reactivarla a mano"
        ctx = Contexto(sku, MOTIVO_BARRIDO.format(canal=canal), None, None, vuelta=vuelta)
        a = {"canal": canal, "cuenta": m.get("cuenta"), "item_id": str(m["item_id"]),
             "stock_actual_canal": m.get("stock_own")}
        if _anotar(ctx, a, ACC_SIN_CAMBIO,
                   (f"{CON_STOCK}: Woo ya tiene {int(woo)} y sigue fuera de la venta "
                    f"({m.get('status') or 'sin estado'}); {razon}"), una_al_dia=CON_STOCK):
            cerrar(ctx)
            vuelta["avisos"]["con_stock"].append((canal, f"{sku} (Woo {int(woo)})"))
            nuevas.append(sku)
    return nuevas


_SQL_AUDITORIA = "with m as (" + _SQL_MARCA.format(
    filtro="and canal = %(canal)s and ts > now() - make_interval(days => %(dias)s)") + """)
    select m.sku, m.cuenta, m.item_id, m.resultado, l.status, l.stock_own,
           extract(epoch from now() - m.ts)::float as edad_s
      from m left join channel.listings l
        on l.canal = m.canal and l.sku = m.sku::citext and l.listing_id = m.item_id
     where m.accion = 'cero_reactivar'"""


def en_auditoria(canal: str) -> list[dict[str, Any]]:
    """Reactivaciones del seguro de los últimos días que el canal dejó en
    auditoría (`ok (…→PENDING)`), con el estado que hoy les ve el censo. Sólo lee."""
    from services import supabase_db as sdb
    filas = sdb.fetch_all(_SQL_AUDITORIA, {"canal": canal, "dias": _AUDITORIA_DIAS})
    return [f for f in filas
            if str((leer_marca(f.get("resultado")) or {}).get("a") or "").upper() == "PENDING"]


def _revisar_auditoria(canal: str, vuelta: dict[str, Any]) -> None:
    """
    TikTok manda a auditoría toda reactivación: el seguro la da por hecha en
    `PENDING` y cierra la marca. Si la auditoría la RECHAZA (hubo 5
    `ACTIVATE→FAILED` en 30 días), la publicación queda fuera de la venta, ya sin
    marca y sin que nadie la mire. Aquí, tras cada censo, se revisan esas
    reactivaciones: lo que no llegó a `ACTIVATE` deja una fila al día y un aviso.
    """
    for f in en_auditoria(canal):
        status = str(f.get("status") or "").upper()
        if status == "ACTIVATE":
            continue                                  # la auditoría la aprobó: ya vende
        horas = round(float(f.get("edad_s") or 0.0) / 3600)
        if status == "PENDING":
            if float(f.get("edad_s") or 0.0) < _AUDITORIA_LENTA_S:
                continue                              # sigue en auditoría, dentro de lo normal
            que = f"lleva {horas} h en auditoría (PENDING)"
        else:
            que = f"la auditoría la dejó en {status or 'un estado ilegible'}"
        sku = str(f["sku"])
        ctx = Contexto(sku, MOTIVO_BARRIDO.format(canal=canal), None, None, vuelta=vuelta)
        a = {"canal": canal, "cuenta": f.get("cuenta"), "item_id": str(f["item_id"]),
             "stock_actual_canal": f.get("stock_own")}
        if _anotar(ctx, a, ACC_ERROR,
                   f"{ERR_AUDITORIA}: se le pidió regresar hace {horas} h y {que}; NO está a la venta",
                   una_al_dia=ERR_AUDITORIA):
            cerrar(ctx)
            vuelta["avisos"]["auditoria"].append((canal, sku, que))


# ── A mano: soltar una marca y la vía del canario ────────────────────────────

def soltar(sku: str, canal: str) -> dict[str, Any]:
    """Suelta la marca del seguro para `sku` en `canal`: deja de considerarla
    suya (no la reactivará) y destraba el corte de «3 errores». No toca el canal.
    Con el seguro apagado no hace nada, y sólo anota donde hay algo que soltar:
    una marca vigente o un corte por errores."""
    from services import supabase_db as sdb
    sku, canal = str(sku or "").strip(), (canal or "").strip().lower()
    if canal not in CANALES or not sku:
        return {"ok": False, "motivo": "hace falta sku y canal (temu | tiktok)"}
    if not habilitado():
        return {"ok": False, "motivo": "FANOUT_CERO_ENABLED apagado"}
    items: dict[str, dict[str, Any]] = {}
    for m in marcas_vigentes(canal):
        if _igual(m.get("sku"), sku):
            items[str(m["item_id"])] = {"cuenta": m.get("cuenta"), "por": "marca vigente"}
    for l in sdb.fetch_all(
            """select l.listing_id, a.legacy_code as cuenta
                 from channel.listings l left join core.accounts a on a.id = l.account_id
                where l.canal = %(c)s and l.sku = %(s)s and l.listing_id is not null""",
            {"c": canal, "s": sku}):
        item = str(l["listing_id"])
        if item not in items and _cortado(canal, item):
            items[item] = {"cuenta": l.get("cuenta"), "por": "3 errores"}
    if not items:
        return {"ok": False, "motivo": (f"{sku} no tiene marca vigente del seguro en {canal} "
                                        "(ni un corte por 3 errores): no hay nada que soltar")}
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    soltadas = []
    for item, d in items.items():
        entro = sellar({"ts": ts, "sku": sku, "motivo": MOTIVO_MANUAL, "dry_run": False,
                        "stock_drop": None, "objetivo": None, "canal": canal, "cuenta": d["cuenta"],
                        "item_id": item, "accion": ACC_SOLTAR, "stock_canal": None,
                        "resultado": "ok (manual)", "ms": 0})
        soltadas.append({"item_id": item, "por": d["por"], "sellada": entro})
    _olvidar_marcas()
    return {"ok": all(s["sellada"] for s in soltadas), "sku": sku, "canal": canal,
            "soltadas": soltadas}


def aplicar(sku: str, canal: str, accion: str) -> dict[str, Any]:
    """
    La vía del CANARIO: aplica el seguro a UN SKU a mano, desde producción (la
    API de Temu sólo contesta desde la IP de Railway). Sólo para SKUs que estén
    NOMBRADOS en FANOUT_CERO_SOLO_SKUS (`*` no cuenta), y respeta el ensayo: en
    ensayo anota lo que haría. También gasta el tope del día.

    `inactivar` exige objetivo 0 y se salta el prefiltro del censo (deja probar el
    `3/1` de Temu). `reactivar` exige la marca vigente del seguro y que nadie más
    haya movido el estado, pero se salta la espera y el «primero el stock» (el
    sondeo quiere ver qué pasa al prender con stock 0). Devuelve lo que contestó
    el canal y el estado antes y después.
    """
    from services import fanout_stock
    sku, canal, accion = str(sku or "").strip(), (canal or "").strip().lower(), (accion or "").strip().lower()
    if canal not in CANALES:
        return {"ok": False, "motivo": "canal: temu | tiktok"}
    if accion not in ("inactivar", "reactivar"):
        return {"ok": False, "motivo": "accion: inactivar | reactivar"}
    if not habilitado():
        return {"ok": False, "motivo": "FANOUT_CERO_ENABLED apagado"}
    if not canal_encendido(canal):
        return {"ok": False, "motivo": f"FANOUT_CERO_{canal.upper()} apagado"}
    if sku.upper() not in _solo_skus():
        return {"ok": False, "motivo": f"{sku} no está en FANOUT_CERO_SOLO_SKUS (la vía manual es sólo para el canario)"}
    p = fanout_stock.plan(sku)
    if not p.get("ok"):
        return {"ok": False, "motivo": p.get("motivo") or "sin plan"}
    destinos = [x for x in p.get("acciones") or []
                if (x.get("canal") or "").lower() == canal and str(x.get("item_id") or "").strip()]
    if not destinos:
        return {"ok": False, "motivo": f"{sku} no tiene publicación en {canal}"}
    if accion == "inactivar" and p.get("objetivo") != 0:
        return {"ok": False, "motivo": f"el objetivo de {sku} es {p.get('objetivo')}, no 0: el seguro sólo apaga en 0"}
    en_ensayo, forzado = ensayo(canal)
    ctx = Contexto(sku, MOTIVO_MANUAL, p.get("stock_drop"), p.get("objetivo"), manual=True)
    try:
        for a in destinos:
            if a.get("fuera"):
                ctx.detalle.update(motivo=f"el fan-out descarta ese destino: {a.get('omitido_por')}")
                continue
            if accion == "inactivar":
                _inactivar(ctx, a, canal)
            else:
                _reactivar(ctx, a, canal, None)
    finally:
        cerrar(ctx)
    return {"ok": True, "sku": sku, "canal": canal, "accion": accion,
            "ensayo": en_ensayo, "ensayo_forzado": forzado, "objetivo": p.get("objetivo"),
            "filas": [{k: v for k, v in f.items() if k != "real"} for f in ctx.filas],
            "detalle": ctx.detalle}


# ── Aviso: «vendió estando apagada» ──────────────────────────────────────────

def aviso_venta(cuenta: str, skus: list[str], fecha_compra: str | None,
                order_id: str | None = None) -> int:
    """
    Campana ROJA si un pedido NUEVO trae un SKU que el seguro tiene apagado en
    ese canal y la compra es posterior a la marca + 15 min: el canal vendió una
    publicación que debía estar fuera de la venta. SÍNCRONA: `pedidos_ml` la
    llama con `asyncio.to_thread` (regla 11: lee kubera). Jamás lanza.
    """
    try:
        if not habilitado():
            return 0
        canal = _CANAL_DE_CUENTA_PEDIDO.get(str(cuenta or "").upper())
        if canal is None or not skus or not fecha_compra:
            return 0
        compra = datetime.fromisoformat(str(fecha_compra).replace("Z", "+00:00"))
        if compra.tzinfo is None:
            compra = compra.replace(tzinfo=timezone.utc)
        marcas = [m for (c, _i), m in _marcas_por_item().items() if c == canal]
        avisados = 0
        for sku in {str(s).strip() for s in skus if s}:
            m = next((x for x in marcas if _igual(x.get("sku"), sku)), None)
            if not m or not m.get("ts"):
                continue
            desde = m["ts"] if m["ts"].tzinfo else m["ts"].replace(tzinfo=timezone.utc)
            if compra <= desde + timedelta(minutes=_GRACIA_VENTA_MIN):
                continue               # pudo comprarse antes de apagarla
            from services import alertas
            alertas.avisar(
                f"seguro_cero_venta:{canal}:{sku}",
                f"*{NOMBRE.get(canal, canal)} vendió {sku} estando apagada* por el seguro de "
                f"stock 0 (apagada {desde.astimezone(timezone.utc):%d-%b %H:%M} UTC, compra "
                f"{compra.astimezone(timezone.utc):%d-%b %H:%M} UTC"
                + (f", orden {order_id}" if order_id else "") + ").",
                nivel="🔴")
            log.error("seguro stock 0: %s vendió %s estando apagada (orden %s)", canal, sku, order_id)
            avisados += 1
        return avisados
    except Exception as exc:  # noqa: BLE001 — nunca rompe la venta
        log.warning("seguro: aviso de venta: %s", exc)
        return 0


# ── Para el panel y la ruta de estado (sólo lee) ─────────────────────────────

_SQL_ULTIMAS = """)
    select m.canal, m.sku, m.cuenta, m.item_id, m.ts, m.accion, m.resultado,
           l.status, l.stock_own, p.stock_woo, (l.listing_id is null) as huerfana,
           (select max(l2.listing_id) from channel.listings l2
             where l2.canal = m.canal and l2.sku = m.sku::citext) as listing_actual,
           extract(epoch from now() - m.ts)::float as edad_s,
           to_char(m.ts at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as desde
      from m
      left join channel.listings l
        on l.canal = m.canal and l.sku = m.sku::citext and l.listing_id = m.item_id
      left join ops.stock_watch_photo p on p.sku = m.sku::citext
     where m.accion in ('cero_inactivar', 'cero_soltar')
     order by m.ts desc"""


def _ultimas(canal: str | None = None) -> list[dict[str, Any]]:
    """La ÚLTIMA fila de marca de cada publicación —si es `cero_inactivar` (la
    tiene apagada el seguro) o `cero_soltar` (la soltó)—, con lo que hoy dicen de
    ella el censo y la foto de Woo. Una sola consulta para todo lo que pinta el
    panel. `fuera` = sigue fuera de la venta; `huerfana` = el censo ya no tiene esa
    publicación para ese SKU."""
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        "with m as (" + _SQL_MARCA.format(filtro="and canal = %(canal)s" if canal else "")
        + _SQL_ULTIMAS, {"canal": canal, "z": _ZONA})
    for f in filas:
        f["fuera"] = not a_la_venta(f["canal"], f.get("status"))
    return filas


def apagadas(canal: str | None = None, ultimas: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Las marcas VIGENTES con el estado que hoy tiene su publicación en el
    censo y el stock de Woo. Si alguien la prendió, la marca sigue ahí hasta que
    se suelte, pero `fuera` va en False y el panel no la pinta como apagada."""
    filas = _ultimas(canal) if ultimas is None else ultimas
    return [f for f in filas if f.get("accion") == ACC_INACTIVAR]


def con_stock(marcas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """De las apagadas por el seguro, las que siguen fuera de la venta y YA tienen
    stock en Woo: la lista que nadie debe tener que ir a buscar."""
    return [m for m in marcas if m.get("fuera") and m.get("stock_woo") is not None
            and int(m["stock_woo"]) > 0]


def soltadas(canal: str | None = None, ultimas: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Las que el seguro SOLTÓ solo (no a mano) en los últimos días y siguen fuera
    de la venta: ya no las reactiva, y nada más las señala."""
    filas = _ultimas(canal) if ultimas is None else ultimas
    return [f for f in filas
            if f.get("accion") == ACC_SOLTAR and f.get("fuera")
            and not str(f.get("resultado") or "").startswith("ok (manual")
            and float(f.get("edad_s") or 0.0) < _SOLTADAS_DIAS * 86400]


def _breve(m: dict[str, Any]) -> dict[str, Any]:
    return {"canal": m["canal"], "sku": m["sku"], "item_id": m["item_id"],
            "desde": m.get("desde"), "resultado": m.get("resultado"),
            "estado_censo": m.get("status"), "fuera": m.get("fuera"),
            "woo": m.get("stock_woo"), "huerfana": bool(m.get("huerfana"))}


def resumen_panel() -> dict[str, Any]:
    """El pie de /dashboard: apagado / ensayo / encendido por canal, cuántas tiene
    apagadas, cuántas de ésas ya tienen stock y cuánto del tope de hoy lleva cada
    canal. Sólo lee (una consulta, más el tope de los canales encendidos)."""
    canales: dict[str, dict[str, Any]] = {}
    for c in CANALES:
        en_ensayo, forzado = ensayo(c)
        canales[c] = {"nombre": NOMBRE[c], "encendido": habilitado() and canal_encendido(c),
                      "ensayo": en_ensayo, "forzado": forzado}
    activos = [c for c in CANALES if canales[c]["encendido"]]
    modo = ("apagado" if not activos
            else "ensayo" if all(canales[c]["ensayo"] for c in activos) else "encendido")
    ultimas = _ultimas()
    marcas = [m for m in apagadas(ultimas=ultimas) if m["fuera"]]
    pendientes_ = con_stock(marcas)
    sueltas = [s for s in soltadas(ultimas=ultimas)
               if s.get("stock_woo") is not None and int(s["stock_woo"]) > 0]
    tope = _entero("fanout_cero_tope_dia", 5)
    for c in CANALES:
        canales[c]["apagadas"] = sum(1 for m in marcas if m["canal"] == c)
        canales[c]["con_stock"] = sum(1 for m in pendientes_ if m["canal"] == c)
        canales[c]["hoy"] = usadas_hoy(c, ACC_INACTIVAR) if canales[c]["encendido"] else 0
    return {"modo": modo, "apagadas": len(marcas), "tope_dia": tope,
            "reactivar": bool(getattr(settings, "fanout_cero_reactivar", False)),
            "con_stock": [{"canal": m["canal"], "sku": m["sku"], "woo": int(m["stock_woo"]),
                           "desde": m.get("desde")} for m in pendientes_[:30]],
            "soltadas": [{"canal": s["canal"], "sku": s["sku"], "woo": int(s["stock_woo"]),
                          "por": str(s.get("resultado") or "")[:120]} for s in sueltas[:30]],
            "canales": canales}


def estado() -> dict[str, Any]:
    """`GET /api/fanout/seguro`: banderas efectivas, si corre en ensayo forzado y
    por qué, marcas vigentes, las apagadas que ya tienen stock, las soltadas que
    siguen fuera de la venta, lo que quedó a medias, usadas/tope de hoy y la
    última vuelta. Sólo lee: no llama a ningún canal."""
    salida: dict[str, Any] = {
        "habilitado": habilitado(),
        "ensayo": bool(getattr(settings, "fanout_cero_ensayo", True)),
        "reactivar": bool(getattr(settings, "fanout_cero_reactivar", False)),
        "espera_min": _entero("fanout_cero_espera_min", 30),
        "topes": {"vuelta": _entero("fanout_cero_tope_vuelta", 5),
                  "hora_fuera_de_censo": _entero("fanout_cero_tope_vuelta", 5),
                  "dia": _entero("fanout_cero_tope_dia", 5),
                  "reactivar_dia": _entero("fanout_cero_tope_reactivar_dia", 20),
                  "cuentan": "llamadas al canal (salgan bien o no), no éxitos"},
        "excluir": sorted(_excluidos()), "solo_skus": sorted(_solo_skus()),
        "temu_estados": sorted(estados_temu()),
        "temu_nivel_sku": bool(getattr(settings, "fanout_cero_temu_nivel_sku", False)),
        "relectura": {"espera_s": _espera_s(), "veces": _relecturas()},
        "bloque_n": _entero("fanout_cero_bloque_n", 10),
        "canales": {},
    }
    with _lock:
        salida["esperas"] = {s: max(0, round(t - _ahora())) for s, t in _esperas.items()}
    try:
        ultimas = _ultimas()
        marcas = apagadas(ultimas=ultimas)
        salida["marcas"] = [_breve(m) for m in marcas]
        # LAS DOS LISTAS QUE NO DEBEN QUEDAR EN SILENCIO: lo que el seguro tiene
        # apagado y ya tiene stock, y lo que soltó y sigue fuera de la venta.
        salida["con_stock"] = [_breve(m) for m in con_stock(marcas)]
        salida["soltadas"] = [_breve(s) for s in soltadas(ultimas=ultimas)]
    except Exception as exc:  # noqa: BLE001 — la consulta es informativa
        marcas, salida["marcas_error"] = [], str(exc)[:200]
    try:
        # Apagadas por el seguro que se quedaron sin su fila: hay que verlas a mano.
        salida["sin_marca"] = huerfanas()
    except Exception as exc:  # noqa: BLE001
        salida["sin_marca_error"] = str(exc)[:200]
    for c in CANALES:
        en_ensayo, forzado = ensayo(c)
        info: dict[str, Any] = {
            "encendido": habilitado() and canal_encendido(c),
            "en_ensayo": en_ensayo, "ensayo_forzado": forzado,
            "marcas_vigentes": sum(1 for m in marcas if m["canal"] == c),
            "pausa_min": _en_pausa(c),
            "intentos_ultima_hora_fuera_de_censo": _sueltos_en_la_hora(c),
            "ultima_vuelta": _ultimo.get(c),
        }
        try:
            info["hoy"] = {"intentos_apagar": usadas_hoy(c, ACC_INACTIVAR),
                           "intentos_reactivar": usadas_hoy(c, ACC_REACTIVAR),
                           "ensayos": usadas_hoy(c, ACC_INACTIVAR, ensayos=True)}
            info["ahora"] = [{"sku": f["sku"], "estado": f["status"], "ofrece": f["stock_own"]}
                             for f in candidatos(c)]
            # Intentos que nadie cerró y «no convergió» recientes: los mira el siguiente censo.
            info["a_medias"] = [{"sku": p["sku"], "item_id": p["item_id"], "ultimo": p["accion"],
                                 "resultado": str(p.get("resultado") or "")[:120]}
                                for p in pendientes(c)]
            if c == "tiktok":
                info["en_auditoria"] = [{"sku": f["sku"], "item_id": f["item_id"],
                                         "estado_censo": f.get("status"),
                                         "horas": round(float(f.get("edad_s") or 0.0) / 3600)}
                                        for f in en_auditoria(c)]
        except Exception as exc:  # noqa: BLE001
            info["error"] = str(exc)[:200]
        salida["canales"][c] = info
    return salida
