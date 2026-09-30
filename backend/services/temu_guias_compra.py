"""
temu_guias_compra.py — La COMPRA de guías de Temu, planeada por almacén.

EL ENCARGO (Brandon, 30-sep-2026)
─────────────────────────────────
Hoy una persona compra cada guía en el seller center. Esto la planea sola:
qué caja sale de qué almacén, con cuántas piezas, qué día se entrega al
repartidor, con qué paquetería y cuánto cuesta — y arma el payload EXACTO que
se le mandaría a Temu.

⚠️ AQUÍ NO SE COMPRA NADA POR OMISIÓN. Comprar una guía COBRA y Temu no deja
cancelarla por API. `comprar()` está escrita pero nace detrás de
`TEMU_COMPRA_GUIAS_ENABLED=false` (regla 3: encenderla es el dale de Brandon),
NINGÚN endpoint la invoca, compra UN GRUPO a la vez y sólo el que alguien
aprobó mirando su huella en la vista previa. Lo que corre hoy es
`plan_guias()`: SOLO LECTURAS.

LA FECHA DEL ENVÍO (día de entregar el paquete al repartidor)
─────────────────────────────────────────────────────────────
Regla de Brandon: día de compra + 2 días naturales; si cae en sábado, domingo
o día festivo, se recorre al siguiente día hábil. Lunes a viernes "hasta nuevo
aviso". Ejemplos suyos: compra mar 29-sep → jue 1-oct; compra sáb 3-oct → lun
5-oct. Zona: America/Mexico_City (sin horario de verano desde 2022).

Los FESTIVOS son los que Temu tiene configurados como "sin funcionamiento" en
su panel para 2026 (1-ene, 2-feb, 16-mar, 1-may, 16-sep, 16-nov, 25-dic), más
el 1-ene-2027 para que una compra del 30-dic no caiga en Año Nuevo. Viven en
`TEMU_GUIAS_FESTIVOS` y FALLAN CERRADO: si la fecha de envío cae después del
último festivo de la lista, no se planea compra (hay que agregar el año
siguiente) — una lista vieja entregaría en un festivo sin avisar.

La API NO tiene campo de fecha calendario. Lo que corresponde a la "Fecha del
envío" del panel es `shipLater=true` + `shipLaterLimitTime`: HORAS contadas
desde que la compra tiene éxito, una por LLAMADA (así todas las cajas de un
surtido dividido comparten la fecha por construcción). La regla se traduce a
`24 × días naturales`: mar→jue = 48, jue→lun = 96, vie→lun = 72. La ficha
acepta 24-120 en la petición pero su respuesta sólo lista 24/48/72/96: 120
queda FUERA de `TEMU_GUIAS_HORAS_VALIDAS` hasta que una compra real lo
confirme (seis días de compra al año caen ahí y no se compran solos).
`pickupStartTime/pickupEndTime` NO son la fecha y NO se mandan: en México los
tres canales devolvieron `infoNeeded`, `pickupRules` y las franjas en null.

LOS ALMACENES (confirmados por Brandon)
───────────────────────────────────────
    TEXCO    = Odoo 135 = Temu WH-04038973460631627 ("IFULL NAVE 2", predeterminado)
    TEXCO II = Odoo 150 = Temu WH-10610291507351627 ("Dirección texco 2")
Configurables en `TEMU_GUIAS_ALMACENES`.

EL REPARTO (reglas a/b/c de Brandon) — NO se inventa otra regla
───────────────────────────────────────────────────────────────
El reparto lo decide `odoo_ventas.planear_almacenes`, la MISMA función que arma
las órdenes de Odoo en Automatización (un almacén que cubra todo gana, TEXCO
por preferencia; si ninguno, POR SKU: el que un solo almacén cubre va entero
ahí, y sólo se parte el que ninguno cubre). Aquí sólo se traduce a cajas:
  a · SKU 1 en TEXCO y SKU 2 en TEXCO II → dos cajas, cada una desde su almacén.
  b · el SKU está en los dos → lo decide `planear_almacenes`.
  c · piden 5, TEXCO tiene 3 y TEXCO II 2 → el MISMO orderSn va en dos cajas,
      3 y 2 piezas (patrón oficial de la guía 38, escenario 1). NO se usa
      `splitSubPackage`: ése es para UNA pieza en varias cajas.
Sin stock suficiente en ninguno → NO se planea compra (sobreventa a la vista).

EL STOCK QUE QUEDA (las ventas en espera NO reservan en Odoo)
─────────────────────────────────────────────────────────────
La cola de espera se lee ENTERA (sin la muestra "mitad nuevas, mitad viejas"
de la cola de creación) y se recorre de la más vieja a la más nueva,
descontando de una copia local del `free_qty`:
  · lo que ya se llevan las guías YA COMPRADAS (a mano o por el panel) sale
    del almacén REAL de cada caja (`bg.logistics.shipment.result.get`); si no
    se puede saber, de ahí en adelante nada es comprable;
  · lo que esperan OTROS canales (TikTok) y las órdenes que Odoo dejó en
    borrador (`no_se_pudo_confirmar`) se resta de LOS DOS almacenes, a lo
    seguro: esa demanda no dice de cuál saldrá;
  · las ventas más NUEVAS que el lote no se leen: no le quitan nada a las de
    arriba. `comprar()` lee hasta el grupo que compra, así que su reparto es
    el de la vista previa aunque cambie el tamaño del lote.

CÓMO SE ARMA CADA LLAMADA (`sendType`, uno por llamada)
───────────────────────────────────────────────────────
  0 · un PO, una caja.
  1 · un PO repartido en varias cajas — TODO el PO en UNA llamada.
  2 · varios PO del mismo comprador en UNA caja (el combinado).
El combinado sólo se arma si TODO sale del mismo almacén. Si mezcla almacenes
se separa por almacén: los PO que salen enteros de un almacén van juntos en su
caja (sendType 2, o 0 si es uno); un PO que cruza almacenes va SOLO en su
propia llamada sendType 1.

⚠️ DOS warehouseId distintos en un mismo sendType 1 lo permite la ficha, pero
NO está probado por API: la primera compra real debe ser un caso controlado.

EL GRUPO SE APRUEBA Y SE COMPRA ENTERO
──────────────────────────────────────
Qué quiere Temu junto: `bg.order.combinedshipment.list.get` (sin datos del
comprador). Una sola APROBACIÓN cubre todas las llamadas del grupo y
`comprar()` las compra en secuencia; si una no sale, las siguientes tampoco y
se reporta "grupo a medias" con el estado exacto de cada una. Un PO cuya guía
ya compró el panel (bitácora) cuenta como HECHO y no bloquea a los demás.

LA APROBACIÓN
─────────────
La huella de un grupo firma {payloads, fecha de envío, horas, costo cotizado de
cada caja, momento de la vista previa} y vence a los
`TEMU_GUIAS_APROBACION_MIN` minutos (30). Si al comprar cambió el stock, la
cotización o el día, la huella no coincide: no compra. Justo antes de CADA
shipment.create se recalcula la fecha y se vuelve a leer el detalle de esos PO.

PESO Y MEDIDAS — NUNCA del catálogo
───────────────────────────────────
Las medidas de Woo/costos son CBM reconstruido (memoria
dimensiones-son-cbm-reconstruido). En orden de precedencia:
  0 · la medida CAPTURADA EN EL PANEL para esa caja (cualquier composición:
      es la única forma de comprar una caja con SKUs distintos);
  1 · medición de almacén (`core.products.almacen_*`), sólo caja de 1 pieza;
  2 · el historial de Temu: lo DECLARADO en guías ya compradas del MISMO
      (SKU, piezas), con ≥ `TEMU_GUIAS_EMPAQUE_MIN_MUESTRAS` muestras que no
      se dispersen más de 15 % NI en peso NI en volumen: peso = el MÁXIMO
      visto, caja = la MÁS GRANDE vista;
  3 · interpolación dentro del rango visto del SKU → sólo PROPUESTA;
  4 · nada → no se compra.
A 1 y 2 se les exige además: densidad entre 10 y 4,000 kg/m³, peso ≥ 10 g, y
que la caja del historial NO sea la del catálogo (`costing.costos_validados`):
si coincide, quien compró la guía copió el catálogo y eso no es una medida.
Los números se REDONDEAN HACIA ARRIBA a 2 decimales: declarar de menos es lo
que la paquetería ajusta y cobra.

PAQUETERÍA — `TEMU_GUIAS_PAQUETERIA` (por omisión "*:Pickup")
─────────────────────────────────────────────────────────────
"paquetería:tipo" separadas por coma; "*" es cualquiera. Gana la más barata de
las que entran; empate → la de menos días (`estimatedText`). La omisión es la
regla de Brandon del 30-sep ("siempre la más barata, entre todas las
paqueterías") SIN cambiar de recolección a drop-off, que es una decisión
operativa suya: la vista previa muestra además la más barata de TODAS. Un
canal que pide datos extra (`infoNeeded`) o sólo sirve contra entrega no se
elige.

NUNCA COMPRAR DOS VECES — la bitácora DURABLE `ops.temu_guias_compras` (0061)
─────────────────────────────────────────────────────────────────────────────
No hay cancelación por API, y Temu compra en ASÍNCRONO (guía 37): mientras la
etiqueta está "en aplicación" sus lecturas pueden no mostrarla. Así que:
  · antes de comprar se RECLAMA el grupo entero en kubera, en una transacción
    (`insert … on conflict … do update … where estado in ('rechazada',
    'no_enviada')`: sólo se toma una fila libre): sin fila, no hay compra. Sin
    la tabla (migración sin aplicar) o con kubera caída, no se compra;
  · la fila nace 'en_curso' y sólo sale de los estados que BLOQUEAN con un
    rechazo DOCUMENTADO de Temu (los códigos de validación de la ficha y los
    de la pasarela). Todo lo demás —4000000, timeout, código desconocido,
    cancelación de la tarea, reinicio del contenedor— deja la orden bloqueada
    hasta conciliarla (`conciliar()`, que mira Temu);
  · además: `packageSnInfo` vacío, estado 2, nada en `unshipped.package.get`
    ni en `temu.logistics.label.list.get`, y la relectura del detalle justo
    antes de cada compra. Después se espera el resultado asíncrono (1 = lista,
    2 = falló) antes de decir "comprada", y se exige un packageSn por caja.
Jamás se manda `confirmAcceptance` (DENY_CANCELLATION rechazaría la
cancelación del comprador) ni `SUCCESSFUL_RETRY` (volvería a cobrar).

NADA DEL COMPRADOR sale de aquí: sólo PO, orderSn, SKUs, piezas, almacenes,
medidas, paquetería e importes estimados. El detalle trae región de entrega y
no se copia.

TODO LO QUE BLOQUEA VA EN HILOS (regla 11): Odoo es XML-RPC y kubera psycopg2.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any, Iterable

from config import settings
from services import odoo_ventas

log = logging.getLogger("omnicanal.temu_guias_compra")

try:
    from zoneinfo import ZoneInfo

    ZONA: Any = ZoneInfo("America/Mexico_City")
except Exception:  # noqa: BLE001 — contenedor sin base de zonas horarias
    # México quitó el horario de verano en oct-2022: la CDMX es UTC-6 fijo.
    ZONA = timezone(timedelta(hours=-6), "CDMX")

CANAL = "temu"

# ── Configuración (todo con valor por omisión aquí y en config.py) ──────────

_FESTIVOS_OMISION = ("2026-01-01,2026-02-02,2026-03-16,2026-05-01,2026-09-16,"
                     "2026-11-16,2026-12-25,2027-01-01")
_ALMACENES_OMISION = "135:WH-04038973460631627,150:WH-10610291507351627"
_HORAS_OMISION = "24,48,72,96"
_PAQUETERIA_OMISION = "*"

# Cómo se llama cada almacén en el seller center de Temu. Sólo para leer.
NOMBRES_TEMU = {"WH-04038973460631627": "IFULL NAVE 2",
                "WH-10610291507351627": "Dirección texco 2"}

_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

ESTADOS_TEMU = {1: "pendiente", 2: "por enviar", 3: "cancelada", 4: "enviada",
                41: "parcialmente enviada", 5: "entregada", 51: "parcialmente entregada"}

EXPLICA_SEND_TYPE = {
    0: "sendType 0 · un PO en una caja",
    1: "sendType 1 · un PO repartido en varias cajas, todo en una sola llamada",
    2: "sendType 2 · varios PO del mismo comprador en una caja (combinado)",
}

# Lo único que puede viajar en cada caja. Lo demás se rechaza en la validación:
# `pickupStartTime/EndTime` (no aplican en México), `confirmAcceptance`
# (DENY_CANCELLATION rechazaría la cancelación del comprador),
# `splitSubPackage` (es para UNA pieza en varias cajas), `extendWeight` e
# `invoiceAccessKey` (EE. UU. y Brasil), `signServiceId` (firma: no se compra).
_CAMPOS_CAJA = {"warehouseId", "shipCompanyId", "channelId", "shipLogisticsType",
                "weight", "weightUnit", "length", "width", "height", "dimensionUnit",
                "orderSendInfoList"}
_CAMPOS_CAJA_OBLIGATORIOS = ("warehouseId", "shipCompanyId", "channelId", "weight",
                             "weightUnit", "length", "width", "height",
                             "dimensionUnit", "orderSendInfoList")
_CAMPOS_RENGLON = {"parentOrderSn", "orderSn", "goodsId", "skuId", "quantity"}
_CAMPOS_LLAMADA = {"sendType", "sendRequestList", "shipLater", "shipLaterLimitTime"}
_DOS_DECIMALES = re.compile(r"^\d{1,6}\.\d{2}$")

_ETIQUETAS_PO_BLOQUEAN = {
    "pending_buyer_cancellation": "el comprador pidió cancelar (120012030)",
    "pending_buyer_address_change": "hay un cambio de dirección pendiente (120012023)",
    "pending_risk_control_alert": "tiene una alerta de riesgo pendiente (120012031/120012061)",
    "signature_required_on_delivery": ("exige firma al entregar: canal especial y no se "
                                       "combina (120015543)"),
}
_ETIQUETAS_PO_AVISAN = {"soon_to_be_overdue": "Temu la marca POR VENCER",
                        "past_due": "Temu la marca VENCIDA"}
_ETIQUETAS_RENGLON_BLOQUEAN = {
    "Y2_advance_sale": ("preventa Y2: en México exige 192-360 h y no se mezcla "
                        "(120015532/120015534)"),
}

# Plausibilidad física de una caja declarada (fuentes automáticas). El 50 kg/m³
# de la memoria del catálogo deja fuera almohadas y peluches reales (13-20
# kg/m³); 10 sigue atrapando la caja de 70×45×46 cm que "pesa" 1 g (0.007) y
# la de 50×50×20 con 100 g (2 kg/m³). Arriba, 4,000: una mancuerna en su caja
# ronda 2,300.
DENSIDAD_MIN = 10.0        # kg/m³
DENSIDAD_MAX = 4000.0      # kg/m³
PESO_MIN_KG = 0.01
TOLERANCIA_CATALOGO_CM = 0.5

# El resultado de la compra es ASÍNCRONO (guía 37): segundos entre lecturas de
# `shipment.result.get` antes de decir comprada / fallida / pendiente.
_ESPERAS_RESULTADO: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0, 8.0, 10.0)

# ── La bitácora durable (migración 0061) ────────────────────────────────────
TABLA = "ops.temu_guias_compras"
ESTADOS_HECHOS = frozenset({"comprada", "pendiente"})
ESTADOS_ABIERTOS = frozenset({"en_curso", "desconocido", "fallida", "ya_solicitada"})
ESTADOS_LIBRES = frozenset({"rechazada", "no_enviada"})

# ── Qué respuesta de shipment.create significa "NO compró, seguro" ──────────
# La pasarela valida ANTES de llegar al negocio (08-common-error-codes): firma,
# credenciales, permisos, IP, cuota.
_RECHAZO_PASARELA = frozenset({
    "3000000", "3000001", "3000002", "3000003", "3000004", "3000010", "3000011",
    "3000012", "3000013", "3000014", "3000019", "3000020", "3000021", "3000022",
    "3000025", "3000026", "3000027", "3000028", "3000030", "3000031", "3000032",
    "3000033", "3000034", "3000040", "4000004", "5000000", "5000001", "5000002",
    "5000003"})
# Las validaciones de negocio DOCUMENTADAS en la ficha de shipment.create
# (error_param_list). Fuera a propósito: 120012013 (ya había compra: se trata
# aparte), 120011107 ("verification timed out"), 120018036 ("transaction is
# ongoing") y 120013007 ("query fail"): suenan a algo a medias, no a un "no".
_RECHAZO_NEGOCIO = frozenset({
    "120011035", "120011089", "120015577", "120015051", "120015533", "120015532",
    "120015534", "120015539", "120011111", "120011110", "120012015", "120011112",
    "120011113", "120019030", "120011018", "120015507", "120012044", "120011101",
    "120011102", "120011103", "120011104", "120011105", "120011106", "120011108",
    "120012061", "120011096", "120015569", "120011011", "120019009", "120011015",
    "120015538", "120011094", "120011088", "120015040", "120015545", "120015543",
    "120015037", "120019024", "120018028", "120011057", "120011053", "120011051",
    "120015032", "120011030", "120011082", "120015518", "120011043", "120011044",
    "120011045", "120012029", "120015520", "120012023", "120012030", "120019016",
    "120019017", "120015026", "120013008", "120013009", "120011047", "120011048",
    "120012031", "120018020", "120011020", "120015027", "120018025", "120012007",
    "120015521", "120011006", "120011072", "120012016", "120013002", "120012003"})
RECHAZO_SEGURO = _RECHAZO_PASARELA | _RECHAZO_NEGOCIO
YA_SOLICITADA = "120012013"


def compra_habilitada() -> bool:
    """¿Está encendida la compra? Nace APAGADA (regla 3)."""
    return bool(getattr(settings, "temu_compra_guias_enabled", False))


def _cfg(nombre: str, omision: str) -> str:
    v = getattr(settings, nombre, None)
    return str(omision if v is None else v)


def leer_festivos(crudo: str | None = None) -> frozenset[date]:
    """Los días inhábiles. ⚠️ LANZA ValueError con una fecha mal escrita: un
    festivo ignorado en silencio es una recolección en día festivo."""
    texto = _cfg("temu_guias_festivos", _FESTIVOS_OMISION) if crudo is None else crudo
    salida: set[date] = set()
    for t in re.split(r"[,\s;]+", texto or ""):
        if not t:
            continue
        salida.add(date.fromisoformat(t))    # ValueError si no es AAAA-MM-DD
    return frozenset(salida)


def horas_validas(crudo: str | None = None) -> tuple[int, ...]:
    texto = _cfg("temu_guias_horas_validas", _HORAS_OMISION) if crudo is None else crudo
    return tuple(sorted({int(t) for t in re.split(r"[,\s;]+", texto or "") if t}))


def mapa_almacenes(crudo: str | None = None) -> dict[int, str]:
    """{almacén de Odoo: warehouseId de Temu}. ⚠️ LANZA ValueError si está mal
    escrito: comprar desde el almacén equivocado declara un origen falso."""
    texto = _cfg("temu_guias_almacenes", _ALMACENES_OMISION) if crudo is None else crudo
    salida: dict[int, str] = {}
    for par in re.split(r"[,\s;]+", texto or ""):
        if not par:
            continue
        odoo_id, sep, wh = par.partition(":")
        if not sep or not odoo_id.strip().isdigit() or not wh.strip().startswith("WH-"):
            raise ValueError(f"TEMU_GUIAS_ALMACENES mal escrito: {par!r} "
                             "(forma: 135:WH-…,150:WH-…)")
        oid = int(odoo_id)
        if oid in salida or wh.strip() in salida.values():
            raise ValueError(f"TEMU_GUIAS_ALMACENES repite {par!r}")
        salida[oid] = wh.strip()
    if not salida:
        raise ValueError("TEMU_GUIAS_ALMACENES vacío")
    return salida


def preferencias_paqueteria(crudo: str | None = None) -> list[tuple[str, str]]:
    """[(paquetería, tipo)] en orden: "J&T express:Pickup", "*:Pickup", "*"."""
    texto = _cfg("temu_guias_paqueteria", _PAQUETERIA_OMISION) if crudo is None else crudo
    salida = []
    for par in (texto or "").split(","):
        emp, _sep, tipo = par.strip().partition(":")
        if emp.strip():
            salida.append((emp.strip(), tipo.strip()))
    return salida


def _min_muestras() -> int:
    return max(1, int(getattr(settings, "temu_guias_empaque_min_muestras", 2) or 2))


def _aprobacion_min() -> int:
    return max(1, int(getattr(settings, "temu_guias_aprobacion_min", 30) or 30))


def _cola_max() -> int:
    return max(10, int(getattr(settings, "temu_guias_cola_max", 400) or 400))


# ═════════════════════════════════════════════════════════════════════════════
#  1 · LA FECHA DEL ENVÍO (pura)
# ═════════════════════════════════════════════════════════════════════════════

def fecha_envio(compra: datetime, *, festivos: Iterable[date] | None = None,
                dias: int | None = None,
                validas: Iterable[int] | None = None) -> dict[str, Any]:
    """
    La fecha de entregar el paquete al repartidor y las horas de
    `shipLaterLimitTime` que la producen. PURA.

    `compra` es el instante de la compra; sin zona se toma como hora de México.
    Devuelve `valida=False` con su motivo cuando no se puede comprar con esa
    fecha (horas fuera de las opciones de Temu, o lista de festivos vencida):
    en ese caso NO se compra.
    """
    local = compra.astimezone(ZONA) if compra.tzinfo else compra.replace(tzinfo=ZONA)
    motivos: list[str] = []
    try:
        fest = frozenset(festivos) if festivos is not None else leer_festivos()
    except ValueError as exc:
        fest = frozenset()
        motivos.append(f"TEMU_GUIAS_FESTIVOS mal escrito ({exc}): no se calcula la fecha")
    n = int(getattr(settings, "temu_guias_dias_envio", 2) or 2) if dias is None else int(dias)
    ok_horas = tuple(validas) if validas is not None else horas_validas()

    base = local.date()
    objetivo = base + timedelta(days=n)
    saltados: list[dict[str, str]] = []
    # Lunes a viernes, y ningún festivo. `weekday()`: lunes 0 … domingo 6.
    while objetivo.weekday() >= 5 or objetivo in fest:
        saltados.append({"fecha": objetivo.isoformat(),
                         "por": ("festivo" if objetivo in fest
                                 else _DIAS[objetivo.weekday()])})
        objetivo += timedelta(days=1)
    dias_nat = (objetivo - base).days
    horas = 24 * dias_nat
    if horas not in ok_horas:
        motivos.append(f"la fecha cae a {horas} h de la compra y sólo se aceptan "
                       f"{'/'.join(str(h) for h in ok_horas)} h (TEMU_GUIAS_HORAS_VALIDAS): "
                       "no se compra")
    if not fest or not any(f >= objetivo for f in fest):
        ultimo = max(fest).isoformat() if fest else "—"
        motivos.append(f"la lista de festivos termina el {ultimo} y la fecha de envío es "
                       f"el {objetivo.isoformat()}: agrega los del año siguiente a "
                       "TEMU_GUIAS_FESTIVOS")
    vence = local + timedelta(hours=horas)
    return {
        "valida": not motivos, "motivos": motivos,
        "compra_local": local.isoformat(timespec="minutes"),
        "compra_dia": _DIAS[base.weekday()],
        "fecha_envio": objetivo.isoformat(),
        "dia_envio": _DIAS[objetivo.weekday()],
        "dias_naturales": dias_nat,
        "horas": horas,
        "ship_later_limit_time": str(horas),
        # Temu cuenta HORAS: la orden pasa a "enviada" a esta hora exacta si
        # antes no la escanea la paquetería.
        "vence_local": vence.isoformat(timespec="minutes"),
        "vence_ts": int(vence.timestamp()),
        "saltados": saltados,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  2 · LA VENTA, LEÍDA DEL DETALLE DE TEMU (pura)
# ═════════════════════════════════════════════════════════════════════════════

def _entero(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if not f.is_integer():
            return None
        n = int(f)
    return n


def _etiquetas(lista: Any) -> set[str]:
    """Los nombres de etiqueta con `value == 1`."""
    salida = set()
    for e in lista or []:
        if isinstance(e, dict) and _entero(e.get("value")) == 1 and e.get("name"):
            salida.add(str(e["name"]))
    return salida


def _hora_mx(ts: Any) -> str | None:
    n = _entero(ts)
    if not n:
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc).astimezone(ZONA).isoformat(
        timespec="minutes")


def leer_venta(parent_sn: str, det: dict[str, Any]) -> dict[str, Any]:
    """
    Lo que hace falta para planear la guía de UN PO, y lo que impide comprarla.
    PURA: recibe el `result` de `bg.order.detail.v2.get`.

    La cantidad a surtir es `quantity` (= original − cancelado antes del envío)
    TAL CUAL. No se cae a `originalOrderQuantity` —como hace `_normalizar` de
    pedidos—: un renglón cancelado entero pediría piezas que nadie compró.
    Los renglones en 0 se quedan fuera.

    `paquetes` = los packageSn que el detalle ya le cuelga (guía comprada, a
    mano o por el panel): con ellos se sabe de qué almacén sale lo que ya se
    llevó (`consumo_de_paquetes`).
    """
    sn = str(parent_sn)
    pm = det.get("parentOrderMap") or {}
    bloqueos: list[str] = []
    avisos: list[str] = []

    estado = _entero(pm.get("parentOrderStatus"))
    if estado != 2:
        txt = ESTADOS_TEMU.get(estado or -1, f"código {estado}")
        bloqueos.append(f"el PO está en estado {estado} ({txt}); sólo se compra en 2 (por enviar)"
                        + (" — todavía no se puede surtir" if estado == 1 else ""))
    etq = _etiquetas(pm.get("parentOrderLabel"))
    for nombre, txt in _ETIQUETAS_PO_BLOQUEAN.items():
        if nombre in etq:
            bloqueos.append(txt)
    for nombre, txt in _ETIQUETAS_PO_AVISAN.items():
        if nombre in etq:
            avisos.append(txt)
    for w in pm.get("fulfillmentWarning") or []:
        w = str(w)
        if w == "RESTRICT_CALL_SHIPPING":
            bloqueos.append("Temu le prohíbe comprar guía (RESTRICT_CALL_SHIPPING)")
        elif w.startswith("BLOCK_LOGISTICS_PROVIDERS"):
            bloqueos.append(f"el comprador bloqueó paqueterías ({w}): revísalo a mano (120015040)")
    if str(pm.get("orderPaymentType") or "").upper() == "COD":
        bloqueos.append("pago contra entrega (COD): no admite envío posterior (120011053)")

    renglones: list[dict[str, Any]] = []
    cancelados = 0
    cubre_plataforma = False
    psn_venta: set[str] = set()
    paquete_sin_numero = False
    for o in det.get("orderList") or []:
        if not isinstance(o, dict):
            continue
        osn = str(o.get("orderSn") or "").strip()
        if not osn:
            bloqueos.append("un renglón no trae orderSn")
            continue
        q = _entero(o.get("quantity"))
        if q is None or q < 0:
            bloqueos.append(f"{osn}: Temu no dio la cantidad a surtir (quantity)")
            continue
        if q == 0:
            cancelados += 1
            continue
        if _entero(o.get("orderStatus")) != 2:
            bloqueos.append(f"{osn}: renglón en estado {o.get('orderStatus')} "
                            f"({ESTADOS_TEMU.get(_entero(o.get('orderStatus')) or -1, '?')})")
        paquetes = [str((p or {}).get("packageSn") or "") for p in (o.get("packageSnInfo") or [])
                    if isinstance(p, dict)]
        if o.get("packageSnInfo"):
            bloqueos.append(f"{osn}: YA tiene paquete en Temu "
                            f"({', '.join(p for p in paquetes if p) or 'sin número'}) — "
                            "no se compra otra guía")
            psn_venta.update(p for p in paquetes if p)
            paquete_sin_numero = paquete_sin_numero or any(not p for p in paquetes)
        skus = sorted({str(p.get("extCode") or "").strip() for p in (o.get("productList") or [])
                       if isinstance(p, dict)} - {""})
        if len(skus) != 1:
            bloqueos.append(f"{osn}: {'no trae SKU (extCode)' if not skus else 'trae varios SKUs'}")
            continue
        et = _etiquetas(o.get("orderLabel"))
        for nombre, txt in _ETIQUETAS_RENGLON_BLOQUEAN.items():
            if nombre in et:
                bloqueos.append(f"{osn}: {txt}")
        if "platform_covered_shipping" in et:
            cubre_plataforma = True
        if str(o.get("orderPaymentType") or "").upper() == "COD":
            bloqueos.append(f"{osn}: pago contra entrega (COD)")
        r = {"parentOrderSn": sn, "orderSn": osn, "sku": skus[0], "cantidad": q}
        gid, kid = _entero(o.get("goodsId")), _entero(o.get("skuId"))
        if gid:
            r["goodsId"] = gid
        if kid:
            r["skuId"] = kid
        renglones.append(r)
    if not renglones and not bloqueos:
        bloqueos.append("no queda nada que surtir (todo cancelado antes del envío)"
                        if cancelados else "el detalle no trae renglones")
    if cancelados:
        avisos.append(f"{cancelados} renglón(es) cancelado(s) antes del envío: fuera del paquete")
    limite = _entero(pm.get("expectShipLatestTime"))
    return {"parent_order_sn": sn, "estado": estado,
            "estado_txt": ESTADOS_TEMU.get(estado or -1, "?"),
            "limite_envio_ts": limite or None, "limite_envio": _hora_mx(limite),
            "renglones": renglones, "bloqueos": bloqueos, "avisos": avisos,
            "cubre_plataforma": cubre_plataforma,
            "paquetes": sorted(psn_venta), "paquete_sin_numero": paquete_sin_numero}


# ═════════════════════════════════════════════════════════════════════════════
#  3 · EL REPARTO EN CAJAS (puro)
# ═════════════════════════════════════════════════════════════════════════════

def _sin_plan(ventas: list[dict[str, Any]], motivos: list[str],
              plan: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"planeado": False, "motivos": motivos, "llamadas": [], "paquetes": [],
            "consumo": {}, "cobertura": (plan or {}).get("cobertura"),
            "faltante": (plan or {}).get("faltante") or {},
            "stock_foto": (plan or {}).get("stock_foto") or {},
            "ventas": [v["parent_order_sn"] for v in ventas], "regla": None}


def planear_paquetes(ventas: list[dict[str, Any]], productos: dict[str, int],
                     libres: dict[int, dict[int, float]],
                     mapa: dict[int, str] | None = None) -> dict[str, Any]:
    """
    Las CAJAS de un grupo de PO (uno solo, o varios del mismo comprador que
    Temu quiere juntos), una por almacén, y cómo se reparten en llamadas. PURA.

    `ventas` = salidas de `leer_venta` (con sus bloqueos ya sumados).
    `productos` = {sku: product_id de Odoo}. `libres` = {product_id: {almacén:
    libres}} — lo que queda DESPUÉS de las ventas anteriores del lote; no se
    modifica: el que llama descuenta `consumo`.

    No planea (y dice por qué) si algún PO trae bloqueos —ya tiene paquete,
    estado, etiquetas—, si falta un SKU en Odoo, si NINGÚN almacén alcanza, o
    si un almacén no tiene su warehouseId de Temu.
    """
    mapa = mapa_almacenes() if mapa is None else mapa
    if not ventas:
        return _sin_plan([], ["sin ventas"])
    motivos = [f"{v['parent_order_sn']}: {b}" for v in ventas for b in (v.get("bloqueos") or [])]
    if motivos:
        return _sin_plan(ventas, motivos)
    if len(ventas) > 1 and len({bool(v.get("cubre_plataforma")) for v in ventas}) > 1:
        return _sin_plan(ventas, ["el grupo mezcla envío pagado por la plataforma con envío "
                                  "normal: no se combinan (120011035)"])

    renglones = [r for v in ventas for r in v["renglones"]]
    faltan = sorted({r["sku"] for r in renglones if r["sku"] not in productos})
    if faltan:
        return _sin_plan(ventas, [f"sin producto en Odoo: {', '.join(faltan)}"])

    # Por SKU, no por renglón: el mismo SKU en dos PO del combinado se mide
    # contra el stock UNA vez.
    total: dict[str, int] = {}
    for r in renglones:
        total[r["sku"]] = total.get(r["sku"], 0) + int(r["cantidad"])
    lineas = [{"product_id": productos[s], "sku": s, "cantidad": q} for s, q in total.items()]
    plan = odoo_ventas.planear_almacenes(lineas, libres)
    if plan.get("faltante"):
        det = ", ".join(f"{s} faltan {q}" for s, q in plan["faltante"].items())
        return _sin_plan(ventas, [f"sin stock suficiente en TEXCO ni TEXCO II ({det}): "
                                  "no se compra guía de mercancía que no hay"], plan)

    # De "N piezas del SKU en el almacén X" a renglones de Temu (orderSn), en
    # orden: el primer renglón se llena antes que el siguiente.
    pendiente = [int(r["cantidad"]) for r in renglones]
    paquetes: list[dict[str, Any]] = []
    for parte in plan["partes"]:
        wid = int(parte["almacen_id"])
        wh = mapa.get(wid)
        if not wh:
            return _sin_plan(ventas, [f"el almacén {parte['almacen']} (Odoo {wid}) no tiene "
                                      "warehouseId de Temu en TEMU_GUIAS_ALMACENES"], plan)
        envio: list[dict[str, Any]] = []
        for ln in parte["lineas"]:
            disponible = int(ln["cantidad"])
            for i, r in enumerate(renglones):
                if r["sku"] != ln["sku"] or disponible <= 0:
                    continue
                falta = pendiente[i]
                if falta <= 0:
                    continue
                toma = min(disponible, falta)
                envio.append({**{k: r[k] for k in ("parentOrderSn", "orderSn", "goodsId", "skuId")
                                 if k in r}, "sku": r["sku"], "quantity": toma})
                pendiente[i] -= toma
                disponible -= toma
        if envio:
            paquetes.append({"almacen_id": wid, "almacen": parte["almacen"],
                             "warehouse_id": wh, "warehouse_nombre": NOMBRES_TEMU.get(wh, wh),
                             "renglones": envio,
                             "piezas": sum(e["quantity"] for e in envio)})
    if any(pendiente):
        return _sin_plan(ventas, ["el reparto por almacén no cuadró con lo pedido"], plan)

    # Defensa independiente del planeador: ninguna caja saca de un almacén más
    # de lo que tiene libre. Si esto falla, el plan está mal y no se compra.
    consumo: dict[int, dict[int, int]] = {}
    for p in paquetes:
        for e in p["renglones"]:
            pid = productos[e["sku"]]
            consumo.setdefault(pid, {})
            consumo[pid][p["almacen_id"]] = consumo[pid].get(p["almacen_id"], 0) + e["quantity"]
    for pid, por in consumo.items():
        for wid, n in por.items():
            if n > int(libres.get(pid, {}).get(wid, 0) or 0):
                return _sin_plan(ventas, [f"el reparto saca {n} piezas del almacén {wid} y sólo "
                                          f"hay {int(libres.get(pid, {}).get(wid, 0) or 0)}"],
                                 plan)

    return {"planeado": True, "motivos": [], "paquetes": paquetes,
            "llamadas": _llamadas(ventas, paquetes), "consumo": consumo,
            "cobertura": plan["cobertura"], "faltante": {}, "stock_foto": plan["stock_foto"],
            "ventas": [v["parent_order_sn"] for v in ventas],
            "regla": _regla(paquetes, plan, libres, productos)}


def _sub(p: dict[str, Any], pos: set[str]) -> dict[str, Any]:
    ren = [e for e in p["renglones"] if e["parentOrderSn"] in pos]
    return {**p, "renglones": ren, "piezas": sum(e["quantity"] for e in ren)}


def _llamadas(ventas: list[dict[str, Any]],
              paquetes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Las cajas agrupadas en LLAMADAS a `shipment.create`. Ver el encabezado."""
    pos = [v["parent_order_sn"] for v in ventas]
    de: dict[str, set[str]] = {po: set() for po in pos}
    for p in paquetes:
        for e in p["renglones"]:
            de[e["parentOrderSn"]].add(p["warehouse_id"])

    def _ll(send: int, vs: list[str], ps: list[dict[str, Any]]) -> dict[str, Any]:
        return {"send_type": send, "explica": EXPLICA_SEND_TYPE[send], "ventas": vs,
                "paquetes": ps}

    if len(pos) == 1:
        return [_ll(0 if len(paquetes) == 1 else 1, pos, paquetes)]
    if len({p["warehouse_id"] for p in paquetes}) == 1:
        return [_ll(2, pos, paquetes)]
    # Combinado que MEZCLA almacenes: se separa por almacén.
    salida = []
    for p in paquetes:
        enteros = [po for po in pos if de[po] == {p["warehouse_id"]}]
        if enteros:
            salida.append(_ll(2 if len(enteros) > 1 else 0, enteros, [_sub(p, set(enteros))]))
    for po in pos:
        if len(de[po]) > 1:
            salida.append(_ll(1, [po], [_sub(p, {po}) for p in paquetes
                                         if any(e["parentOrderSn"] == po for e in p["renglones"])]))
    return salida


def _regla(paquetes: list[dict[str, Any]], plan: dict[str, Any],
           libres: dict[int, dict[int, float]], productos: dict[str, int]) -> dict[str, str]:
    """Cuál de las reglas de Brandon se aplicó, en palabras."""
    if len(paquetes) == 1:
        p = paquetes[0]
        todos = [w for w, _n in odoo_ventas._ALMACENES]  # noqa: SLF001
        ambos = any(all(int(libres.get(productos[e["sku"]], {}).get(w, 0) or 0) > 0
                        for w in todos) for e in p["renglones"])
        if ambos:
            return {"id": "b", "texto": (f"Todo sale de {p['almacen']}: había stock en los dos "
                                         "almacenes y decidió la regla de Automatización "
                                         "(gana el primero que cubra todo; TEXCO por preferencia).")}
        return {"id": "un_almacen", "texto": f"Todo sale de {p['almacen']}: sólo ahí alcanza."}
    skus_por: dict[str, int] = {}
    for p in paquetes:
        for s in {e["sku"] for e in p["renglones"]}:
            skus_por[s] = skus_por.get(s, 0) + 1
    partidos = sorted(s for s, n in skus_por.items() if n > 1)
    if partidos and len(partidos) < len(skus_por):
        return {"id": "a+c", "texto": ("SKUs distintos por almacén y además "
                                       f"{', '.join(partidos)} partido entre los dos.")}
    if partidos:
        return {"id": "c", "texto": (f"{', '.join(partidos)} partido: cada almacén manda lo "
                                     "que tiene, en su propia caja, con la misma fecha.")}
    return {"id": "a", "texto": ("SKUs distintos en almacenes distintos: una caja por almacén, "
                                 "con la misma fecha.")}


def apartar(ventas: list[dict[str, Any]], productos: dict[str, int],
            libres: dict[int, dict[int, float]]) -> dict[int, dict[int, int]]:
    """
    Las piezas que una venta NO comprable se lleva igual del lote. PURA.

    Una venta bloqueada (alerta pendiente, sin stock completo…) sigue viva: su
    orden de Odoo nacerá y se llevará sus piezas, pero HOY no reserva nada. Si
    no se apartan, la venta siguiente del lote se planea con esas mismas
    piezas. Se apartan con el mismo reparto de `planear_almacenes` y nunca más
    de lo que hay (lo que falte, falta). Lo que ya cubre una guía comprada NO
    pasa por aquí: sale de SU almacén (`consumo_de_paquetes`).
    """
    total: dict[str, int] = {}
    for v in ventas:
        for r in v.get("renglones") or []:
            if r["sku"] in productos:
                total[r["sku"]] = total.get(r["sku"], 0) + int(r["cantidad"])
    if not total:
        return {}
    lineas = [{"product_id": productos[s], "sku": s, "cantidad": q} for s, q in total.items()]
    plan = odoo_ventas.planear_almacenes(lineas, libres)
    consumo: dict[int, dict[int, int]] = {}
    for parte in plan["partes"]:
        wid = int(parte["almacen_id"])
        for ln in parte["lineas"]:
            pid = int(ln["product_id"])
            ya = consumo.get(pid, {}).get(wid, 0)
            disp = max(0, int(libres.get(pid, {}).get(wid, 0) or 0) - ya)
            n = min(int(ln["cantidad"]), disp)
            if n > 0:
                consumo.setdefault(pid, {})[wid] = ya + n
    return consumo


def descontar(libres: dict[int, dict[int, float]],
              consumo: dict[int, dict[int, int]]) -> None:
    """Resta del stock libre LOCAL lo que se llevó un plan (el lote no reserva
    en Odoo). Modifica `libres`. Nunca deja un almacén en negativo: lo que no
    hay, no hay."""
    for pid, por in consumo.items():
        for wid, n in por.items():
            libres.setdefault(pid, {})
            libres[pid][wid] = max(0.0, float(libres[pid].get(wid, 0) or 0) - int(n))


def consumo_de_paquetes(venta: dict[str, Any], productos: dict[str, int],
                        info: dict[str, dict[str, Any]],
                        mapa_inv: dict[str, int]) -> tuple[dict[int, dict[int, int]],
                                                           dict[str, Any], list[str]]:
    """
    Lo que ya se llevan de una venta sus guías YA COMPRADAS (a mano o por el
    panel), desde el almacén REAL de cada caja. PURA.

    `info` = {packageSn: fila de `bg.logistics.shipment.result.get`} (trae
    warehouseId y `orderSendInfoList` por caja; probado en vivo el 24-sep con
    guías compradas a mano). `mapa_inv` = {warehouseId: almacén de Odoo}.

    Devuelve (consumo {product_id: {almacén: piezas}}, la venta con SÓLO lo que
    ninguna caja cubre, problemas). Una caja que no aparece, que sale de un
    almacén que no es TEXCO/TEXCO II o cuya etiqueta falló deja `problemas`:
    quien llama no sabe de dónde saldrá eso y trata el lote como incierto.
    """
    problemas: list[str] = []
    consumo: dict[int, dict[int, int]] = {}
    sku_de = {r["orderSn"]: r["sku"] for r in venta.get("renglones") or []}
    cubierto: dict[str, int] = {}
    if venta.get("paquete_sin_numero"):
        problemas.append("una guía comprada no trae número de paquete")
    for psn in venta.get("paquetes") or []:
        p = info.get(psn)
        if not isinstance(p, dict):
            problemas.append(f"{psn}: shipment.result.get no lo devolvió")
            continue
        if _entero(p.get("shippingLabelStatus")) == 2:
            problemas.append(f"{psn}: su etiqueta FALLÓ en Temu (estado 2)")
            continue
        wh = str(p.get("warehouseId") or "").strip()
        wid = mapa_inv.get(wh)
        if wid is None:
            problemas.append(f"{psn}: sale de '{wh or '?'}', que no es TEXCO ni TEXCO II")
            continue
        for o in _lista(p, "orderSendInfoList"):
            osn = str(o.get("orderSn") or "")
            q = _entero(o.get("quantity")) or 0
            if osn not in sku_de or q <= 0:
                continue      # renglón de otro PO de la misma caja: lo cuenta su dueño
            pid = productos.get(sku_de[osn])
            if pid is None:
                problemas.append(f"{psn}: {sku_de[osn]} sin producto en Odoo")
                continue
            consumo.setdefault(pid, {})[wid] = consumo.get(pid, {}).get(wid, 0) + q
            cubierto[osn] = cubierto.get(osn, 0) + q
    resto = []
    for r in venta.get("renglones") or []:
        falta = int(r["cantidad"]) - cubierto.get(r["orderSn"], 0)
        if falta > 0:
            resto.append({**r, "cantidad": falta})
    return consumo, {**venta, "renglones": resto}, problemas


def consumo_de_reparto(fila: dict[str, Any], productos: dict[str, int]) -> dict[int, dict[int, int]]:
    """{product_id: {almacén: piezas}} de lo que la bitácora dice que se compró
    (`reparto_real`) o se reclamó (`reparto`, lo planeado). PURA."""
    reparto = fila.get("reparto_real") or fila.get("reparto") or []
    consumo: dict[int, dict[int, int]] = {}
    for x in reparto if isinstance(reparto, list) else []:
        try:
            wid, q, sku = int(x["almacen_id"]), int(x["quantity"]), str(x["sku"])
        except (KeyError, TypeError, ValueError):
            continue
        pid = productos.get(sku)
        if pid is None or q <= 0:
            continue
        consumo.setdefault(pid, {})[wid] = consumo.get(pid, {}).get(wid, 0) + q
    return consumo


def clave_caja(paquete: dict[str, Any]) -> str:
    """Nombre ESTABLE de una caja (almacén + renglones y piezas): con él se le
    pega la medida capturada en el panel."""
    ren = sorted((str(e["orderSn"]), int(e["quantity"])) for e in paquete.get("renglones") or [])
    return f"{paquete.get('warehouse_id')}|" + ",".join(f"{o}x{q}" for o, q in ren)


# ═════════════════════════════════════════════════════════════════════════════
#  4 · PESO Y MEDIDAS (puro)
# ═════════════════════════════════════════════════════════════════════════════

def _num(v: Any) -> float | None:
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f if f > 0 else None


def _vol(m: dict[str, Any]) -> float:
    return float(m["largo_cm"]) * float(m["ancho_cm"]) * float(m["alto_cm"])


def muestra_de_paquete(entrada: dict[str, Any]) -> dict[str, Any] | None:
    """Peso y caja de un paquete ya comprado (`packageDimensionInfo` de
    label.list.get o el paquete de shipment.result.get), en kg y cm. None si no
    se puede leer con certeza (otras unidades, ceros)."""
    d = entrada.get("packageDimensionInfo") if isinstance(
        entrada.get("packageDimensionInfo"), dict) else entrada
    peso = _num(d.get("weight"))
    u_peso = str(d.get("weightUnit") or "").strip().lower()
    if peso is None or u_peso not in ("kg", "g"):
        return None
    if u_peso == "g":
        peso = peso / 1000.0
    if str(d.get("dimensionUnit") or "").strip().lower() != "cm":
        return None
    l_, a_, h_ = _num(d.get("length")), _num(d.get("width")), _num(d.get("height"))
    if not (l_ and a_ and h_):
        return None
    return {"peso_kg": round(peso, 3), "largo_cm": l_, "ancho_cm": a_, "alto_cm": h_}


def plausible(peso: float | None, largo: float | None, ancho: float | None,
              alto: float | None) -> str | None:
    """None si la caja es físicamente creíble; si no, POR QUÉ. PURA."""
    if not (peso and largo and ancho and alto):
        return "faltan peso o medidas"
    if peso < PESO_MIN_KG:
        return f"pesa {peso:.3f} kg (menos de 10 g): no es un paquete real"
    vol_m3 = largo * ancho * alto / 1_000_000.0
    densidad = peso / vol_m3
    if densidad < DENSIDAD_MIN:
        return (f"densidad de {densidad:.1f} kg/m³ (mínimo {DENSIDAD_MIN:.0f}): caja enorme para "
                "su peso — así se ven las medidas de catálogo, y Temu cobraría por volumen")
    if densidad > DENSIDAD_MAX:
        return (f"densidad de {densidad:,.0f} kg/m³ (máximo {DENSIDAD_MAX:,.0f}): caja chica "
                "para su peso — la paquetería ajustaría el cobro")
    return None


def es_caja_de_catalogo(largo: float, ancho: float, alto: float,
                        catalogo: dict[str, Any] | None) -> bool:
    """¿La caja es la medida de catálogo (costos_validados, CBM reconstruido),
    en cualquier orden, ±0.5 cm? PURA."""
    if not catalogo:
        return False
    try:
        c = sorted(float(catalogo[k]) for k in ("largo_cm", "ancho_cm", "alto_cm"))
    except (KeyError, TypeError, ValueError):
        return False
    m = sorted((float(largo), float(ancho), float(alto)))
    return all(abs(x - y) <= TOLERANCIA_CATALOGO_CM for x, y in zip(m, c))


def elegir_empaque(contenido: dict[str, int], historial: list[dict[str, Any]],
                   medidas_almacen: dict[str, dict[str, Any]] | None = None,
                   min_muestras: int | None = None,
                   dispersion_max: float = 0.15, *,
                   manual: dict[str, Any] | None = None,
                   catalogo: dict[str, dict[str, Any]] | None = None,
                   catalogo_leido: bool = True) -> dict[str, Any]:
    """
    Peso y caja de UNA caja, diciendo de dónde salen. PURA.

    `contenido` = {sku: piezas}. `historial` = muestras de guías ya compradas:
    [{sku, cantidad, peso_kg, largo_cm, ancho_cm, alto_cm, parent_order_sn}].
    `manual` = la medida capturada en el panel para ESTA caja (manda).
    `catalogo` = {sku: {largo_cm, ancho_cm, alto_cm}} de costos_validados; con
    `catalogo_leido=False` (no se pudo leer) el historial no es comprable.
    `ok=True` sólo con confianza ALTA; lo demás se muestra y no se compra.
    """
    minimo = _min_muestras() if min_muestras is None else max(1, int(min_muestras))
    medidas_almacen = medidas_almacen or {}
    base = {"ok": False, "peso_kg": None, "largo_cm": None, "ancho_cm": None,
            "alto_cm": None, "fuente": None, "confianza": "ninguna", "muestras": 0,
            "detalle": "", "motivo": None, "aviso": None}

    # 0 · La medida capturada en el panel para ESTA caja: es una persona que la
    #     pesó y la midió, y la aprueba al aprobar el payload. Manda sobre todo
    #     y es la única forma de comprar una caja con varios SKUs.
    if manual:
        m = {k: _num(manual.get(k)) for k in ("peso_kg", "largo_cm", "ancho_cm", "alto_cm")}
        if not all(m.values()):
            return {**base, "fuente": "manual",
                    "motivo": "la medida capturada está incompleta: peso y las tres medidas, positivos"}
        if m["peso_kg"] < PESO_MIN_KG:
            return {**base, **m, "fuente": "manual",
                    "motivo": f"el peso capturado ({m['peso_kg']} kg) es menor a 10 g"}
        rara = plausible(m["peso_kg"], m["largo_cm"], m["ancho_cm"], m["alto_cm"])
        return {**base, **m, "ok": True, "confianza": "alta", "fuente": "manual",
                "detalle": "medida capturada en el panel para esta caja",
                "aviso": (f"revisa la captura: {rara}" if rara else None)}

    if len(contenido) != 1:
        estimado = 0.0
        completo = True
        for sku, q in contenido.items():
            porp = [m["peso_kg"] / m["cantidad"] for m in historial
                    if m["sku"] == sku and m.get("cantidad")]
            if not porp:
                completo = False
                break
            estimado += max(porp) * q
        return {**base, "peso_kg": round(estimado, 2) if completo else None,
                "fuente": "suma_estimada" if completo else None,
                "detalle": ("peso = Σ piezas × el mayor peso por pieza visto; SIN caja"
                            if completo else ""),
                "motivo": (f"caja con {len(contenido)} SKUs distintos: no hay historial de esa "
                           "composición ni catálogo de cajas — NO se compra sola: pésala, mídela "
                           "y captura la medida en el panel para poder aprobarla")}
    sku, q = next(iter(contenido.items()))
    cat = (catalogo or {}).get(sku)

    # 1 · Medición de almacén, sólo para 1 pieza (es la del producto empacado).
    alm = medidas_almacen.get(sku) or {}
    if q == 1 and all(_num(alm.get(k)) for k in ("peso_kg", "largo_cm", "ancho_cm", "alto_cm")):
        vals = {k: float(alm[k]) for k in ("peso_kg", "largo_cm", "ancho_cm", "alto_cm")}
        mala = plausible(vals["peso_kg"], vals["largo_cm"], vals["ancho_cm"], vals["alto_cm"])
        if mala:
            return {**base, **vals, "fuente": "almacen", "confianza": "media",
                    "detalle": "medido por almacén (Checklist, core.products.almacen_*)",
                    "motivo": f"la medición de almacén no es creíble: {mala} — vuelve a medir"}
        return {**base, **vals, "ok": True, "confianza": "alta", "fuente": "almacen",
                "detalle": "medido por almacén (Checklist, core.products.almacen_*)"}

    # 2 · Historial del MISMO (SKU, piezas).
    mismas = [m for m in historial if m["sku"] == sku and int(m["cantidad"]) == int(q)]
    if mismas:
        pesos = [float(m["peso_kg"]) for m in mismas]
        vols = [_vol(m) for m in mismas]
        disp_p = (max(pesos) - min(pesos)) / max(pesos) if max(pesos) else 1.0
        disp_v = (max(vols) - min(vols)) / max(vols) if max(vols) else 1.0
        # El peso MÁS ALTO y la caja MÁS GRANDE vistos, aunque no sean de la
        # misma guía: declarar de menos es lo que la paquetería ajusta y cobra.
        caja = max(mismas, key=lambda m: (_vol(m), float(m["peso_kg"])))
        salida = {**base, "peso_kg": max(pesos), "largo_cm": caja["largo_cm"],
                  "ancho_cm": caja["ancho_cm"], "alto_cm": caja["alto_cm"],
                  "fuente": "historial_temu", "muestras": len(mismas),
                  "detalle": (f"lo declarado en {len(mismas)} guía(s) de {sku} × {q}: peso = el "
                              f"mayor visto; caja = la más grande vista "
                              f"({caja.get('parent_order_sn') or '—'})")}
        motivos: list[str] = []
        if len(mismas) < minimo:
            motivos.append(f"sólo {len(mismas)} muestra(s) de {sku} × {q} (se piden {minimo})")
        if disp_p > dispersion_max:
            motivos.append(f"los pesos de {sku} × {q} se dispersan {disp_p:.0%} (tope "
                           f"{dispersion_max:.0%})")
        if disp_v > dispersion_max:
            motivos.append(f"las cajas de {sku} × {q} se dispersan {disp_v:.0%} en volumen (tope "
                           f"{dispersion_max:.0%})")
        mala = plausible(salida["peso_kg"], caja["largo_cm"], caja["ancho_cm"], caja["alto_cm"])
        if mala:
            motivos.append(mala)
        if not catalogo_leido:
            motivos.append("no se pudo comparar la caja contra la medida de catálogo")
        elif es_caja_de_catalogo(caja["largo_cm"], caja["ancho_cm"], caja["alto_cm"], cat):
            motivos.append("la caja declarada en esas guías es IDÉNTICA a la medida de catálogo "
                           "(CBM reconstruido, no es una medida): quien compró la guía copió "
                           "el catálogo")
        if not motivos:
            return {**salida, "ok": True, "confianza": "alta"}
        return {**salida, "confianza": "media",
                "motivo": "; ".join(motivos) + ": confirma peso y caja (captúralos en el panel)"}

    # 3 · Interpolación dentro del rango visto del SKU: sólo PROPUESTA.
    por_q: dict[int, dict[str, Any]] = {}
    for m in historial:
        if m["sku"] != sku:
            continue
        k = int(m["cantidad"])
        if k not in por_q or m["peso_kg"] > por_q[k]["peso_kg"]:
            por_q[k] = m
    abajo = [k for k in por_q if k < q]
    arriba = [k for k in por_q if k > q]
    if abajo and arriba:
        qa, qb = max(abajo), min(arriba)
        pa, pb = por_q[qa]["peso_kg"], por_q[qb]["peso_kg"]
        peso = pa + (pb - pa) * (q - qa) / (qb - qa)
        caja = por_q[qb]
        return {**base, "peso_kg": round(peso, 2), "largo_cm": caja["largo_cm"],
                "ancho_cm": caja["ancho_cm"], "alto_cm": caja["alto_cm"],
                "fuente": "interpolado_historial", "confianza": "media",
                "muestras": len([m for m in historial if m["sku"] == sku]),
                "detalle": (f"peso interpolado entre {sku} × {qa} y × {qb}; caja de la de "
                            f"{qb} piezas (ya se usó y cupo)"),
                "motivo": f"no hay guías de {sku} × {q}: la propuesta es interpolada, confírmala"}
    return {**base, "motivo": (f"sin medición de almacén ni historial de {sku} × {q}: "
                               "hay que pesar y medir la caja (y capturarla en el panel)")}


def _dos(x: float) -> str:
    """Texto con 2 decimales REDONDEADO HACIA ARRIBA: 0.705 → '0.71', 0.004 →
    '0.01'. Declarar de menos es lo que la paquetería ajusta y cobra. Se quita
    antes el ruido binario del float (0.1 + 0.2) para no subir un centavo de
    más."""
    try:
        d = Decimal(repr(round(float(x), 6)))
    except (InvalidOperation, TypeError, ValueError):
        d = Decimal("0")
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_CEILING))


def leer_medidas(crudo: Any) -> dict[str, dict[str, float]]:
    """Las medidas capturadas en el panel, validadas: {clave de caja: {peso_kg,
    largo_cm, ancho_cm, alto_cm}}. ⚠️ LANZA ValueError con cualquier cosa rara:
    una medida mal leída es un payload que alguien aprobaría sin saberlo."""
    if crudo in (None, "", {}):
        return {}
    if isinstance(crudo, str):
        crudo = json.loads(crudo)
    if not isinstance(crudo, dict) or len(crudo) > 60:
        raise ValueError("medidas: un objeto {clave de caja: {peso_kg, largo_cm, ancho_cm, "
                         "alto_cm}} de a lo más 60 cajas")
    topes = {"peso_kg": 70.0, "largo_cm": 300.0, "ancho_cm": 300.0, "alto_cm": 300.0}
    salida: dict[str, dict[str, float]] = {}
    for k, v in crudo.items():
        if not isinstance(k, str) or not k.startswith("WH-") or "|" not in k or len(k) > 2000:
            raise ValueError(f"medidas: clave de caja inválida {str(k)[:60]!r}")
        if not isinstance(v, dict):
            raise ValueError(f"medidas de {k[:40]}: tiene que ser un objeto")
        m: dict[str, float] = {}
        for campo, tope in topes.items():
            n = _num(v.get(campo))
            if n is None or n > tope:
                raise ValueError(f"medidas de {k[:40]}: {campo} tiene que ser un número entre "
                                 f"0 y {tope:g}")
            m[campo] = n
        salida[k] = m
    return salida


# ═════════════════════════════════════════════════════════════════════════════
#  5 · COTIZACIÓN Y PAYLOAD (puros)
# ═════════════════════════════════════════════════════════════════════════════

def payload_cotizacion(paquete: dict[str, Any]) -> dict[str, Any]:
    """Los parámetros EXACTOS de `bg.logistics.shippingservices.get` para una
    caja (sin el sobre, que arma `temu.llamar`). `shipOrderInfoList` y no
    `orderSnList`: se excluyen entre sí (120018070) y sólo el primero lleva
    cantidades, que es lo que distingue la caja de 3 de la de 2."""
    emp = paquete["empaque"]
    return {
        "warehouseId": paquete["warehouse_id"],
        "shipOrderInfoList": [{"parentOrderSn": e["parentOrderSn"], "orderSn": e["orderSn"],
                               "quantity": int(e["quantity"])} for e in paquete["renglones"]],
        "weight": _dos(emp["peso_kg"]), "weightUnit": "kg",
        "length": _dos(emp["largo_cm"]), "width": _dos(emp["ancho_cm"]),
        "height": _dos(emp["alto_cm"]), "dimensionUnit": "cm",
    }


def _norm(t: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(t or "").lower())


def _monto(t: Any) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", str(t or "").replace(",", ""))
    return float(m.group(1)) if m else None


def _dias_texto(t: Any) -> tuple[float, float]:
    """(días máximos, días mínimos) de `estimatedText` ("MX$34.90,10-15 days").
    Sin dato → infinito: en un empate gana la que sí dice cuánto tarda."""
    txt = str(t or "")
    m = re.search(r"(\d+)\s*[-–~]\s*(\d+)\s*(?:business\s*)?d", txt, re.I)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return (max(a, b), min(a, b))
    m = re.search(r"(\d+)\s*(?:business\s*)?days?", txt, re.I)
    if m:
        n = float(m.group(1))
        return (n, n)
    return (math.inf, math.inf)


def elegir_canal(respuesta: dict[str, Any],
                 preferencias: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """De la cotización, el canal a comprar: el MÁS BARATO de los que entran en
    las preferencias ("*" = cualquier paquetería o tipo); empate → el de menos
    días. PURA. Nunca cambia sola a algo que no esté en la lista. `mas_barata`
    es la más barata de TODAS (para que se vea lo que la regla dejó fuera)."""
    prefs = preferencias_paqueteria() if preferencias is None else preferencias
    opciones = []
    for c in (respuesta or {}).get("onlineChannelDtoList") or []:
        if not isinstance(c, dict):
            continue
        opciones.append({
            "channelId": _entero(c.get("channelId")),
            "shipCompanyId": _entero(c.get("shipCompanyId")),
            "shippingCompanyName": str(c.get("shippingCompanyName") or ""),
            "shipLogisticsType": str(c.get("shipLogisticsType") or ""),
            "estimatedAmount": str(c.get("estimatedAmount") or ""),
            "estimatedText": str(c.get("estimatedText") or ""),
            "monto": _monto(c.get("estimatedAmount")),
            "dias": list(_dias_texto(c.get("estimatedText"))),
            "pide_datos": list(c.get("infoNeeded") or []),
            "solo_cod": _entero(c.get("payWayCode")) == 2,
        })
    no_disp = [{"shippingCompanyName": str(c.get("shippingCompanyName") or ""),
                "shipLogisticsType": str(c.get("shipLogisticsType") or ""),
                "motivo": str(c.get("unavailableReason") or "")[:200]}
               for c in (respuesta or {}).get("unavailableChannelDtoList") or []
               if isinstance(c, dict)]

    def _usable(o: dict[str, Any]) -> bool:
        return not (o["pide_datos"] or o["solo_cod"] or not o["channelId"]
                    or not o["shipCompanyId"])

    def _clave(o: dict[str, Any], rango: int) -> tuple[float, float, float, int]:
        return (o["monto"] if o["monto"] is not None else math.inf,
                o["dias"][0], o["dias"][1], rango)

    candidatos: dict[int, tuple[int, dict[str, Any]]] = {}
    for rango, (emp, tipo) in enumerate(prefs):
        for o in opciones:
            if emp != "*" and _norm(emp) not in _norm(o["shippingCompanyName"]):
                continue
            if tipo and tipo != "*" and _norm(tipo) != _norm(o["shipLogisticsType"]):
                continue
            if not _usable(o):
                continue
            if o["channelId"] not in candidatos or rango < candidatos[o["channelId"]][0]:
                candidatos[o["channelId"]] = (rango, o)
    todas = sorted((o for o in opciones if _usable(o)), key=lambda o: _clave(o, 0))
    mas_barata = todas[0] if todas else None
    if not candidatos:
        ofrecidas = ", ".join(f"{o['shippingCompanyName']} {o['shipLogisticsType']} "
                              f"{o['estimatedAmount']}" for o in opciones) or "ninguna"
        return {"elegido": None, "opciones": opciones, "no_disponibles": no_disp,
                "mas_barata": mas_barata,
                "motivo": (f"la paquetería preferida ({', '.join(':'.join(p) for p in prefs)}) "
                           f"no se ofreció para esta caja; Temu ofreció: {ofrecidas}")}
    elegido = min(candidatos.values(), key=lambda t: _clave(t[1], t[0]))[1]
    return {"elegido": elegido, "opciones": opciones, "no_disponibles": no_disp,
            "mas_barata": mas_barata, "motivo": None}


def payload_compra(llamada: dict[str, Any], horas: int | str) -> dict[str, Any]:
    """
    Los parámetros EXACTOS de `bg.logistics.shipment.create` para UNA llamada
    (sin el sobre). PURA. ⚠️ LANZA ValueError si a una caja le falta empaque o
    canal: un payload a medias no se arma.

    `shipLater=true` siempre: con false la orden pasa a "enviada" al comprar.
    `autoConfirmAfterPickup` se omite (true por omisión: Temu la marca enviada
    al escanear la recolección).
    """
    cajas = []
    for p in llamada["paquetes"]:
        emp = p.get("empaque") or {}
        canal = (p.get("cotizacion") or {}).get("elegido") or {}
        if not emp.get("ok"):
            raise ValueError(f"la caja de {p.get('almacen')} no tiene peso/medidas confiables")
        if not canal.get("channelId") or not canal.get("shipCompanyId"):
            raise ValueError(f"la caja de {p.get('almacen')} no tiene paquetería elegida")
        renglones = []
        for e in p["renglones"]:
            r: dict[str, Any] = {"parentOrderSn": e["parentOrderSn"], "orderSn": e["orderSn"]}
            if e.get("goodsId"):
                r["goodsId"] = int(e["goodsId"])
            if e.get("skuId"):
                r["skuId"] = int(e["skuId"])
            r["quantity"] = int(e["quantity"])
            renglones.append(r)
        cajas.append({
            "warehouseId": p["warehouse_id"],
            "shipCompanyId": int(canal["shipCompanyId"]),
            "channelId": int(canal["channelId"]),
            "weight": _dos(emp["peso_kg"]), "weightUnit": "kg",
            "length": _dos(emp["largo_cm"]), "width": _dos(emp["ancho_cm"]),
            "height": _dos(emp["alto_cm"]), "dimensionUnit": "cm",
            "orderSendInfoList": renglones,
        })
    return {"sendType": int(llamada["send_type"]), "shipLater": True,
            "shipLaterLimitTime": str(int(horas)), "sendRequestList": cajas}


def validar_payload_compra(payload: dict[str, Any],
                           esperado: dict[str, int] | None = None,
                           validas: Iterable[int] | None = None) -> list[str]:
    """Errores del payload contra la ficha oficial de `shipment.create` (campos,
    tipos, formatos) y contra nuestras reglas. Lista vacía = pasa. PURA.

    `esperado` = {orderSn: piezas a surtir}: la suma entre cajas tiene que
    cuadrar (si no, Temu contesta 120013002 "Item quantity does not match")."""
    err: list[str] = []
    ok_horas = tuple(validas) if validas is not None else horas_validas()
    if not isinstance(payload, dict):
        return ["el payload no es un objeto"]
    sobran = set(payload) - _CAMPOS_LLAMADA
    if sobran:
        err.append(f"campos no permitidos en la llamada: {sorted(sobran)}")
    st = payload.get("sendType")
    if not isinstance(st, int) or isinstance(st, bool) or st not in (0, 1, 2):
        err.append("sendType tiene que ser el ENTERO 0, 1 o 2")
    if payload.get("shipLater") is not True:
        err.append("shipLater tiene que ser true (con false la orden pasa a enviada al comprar)")
    slt = payload.get("shipLaterLimitTime")
    if not isinstance(slt, str) or not slt.isdigit() or int(slt) not in ok_horas:
        err.append(f"shipLaterLimitTime tiene que ser TEXTO con {ok_horas}")
    cajas = payload.get("sendRequestList")
    if not isinstance(cajas, list) or not cajas:
        return err + ["sendRequestList vacío"]
    suma: dict[str, int] = {}
    pos_total: set[str] = set()
    for i, c in enumerate(cajas):
        pre = f"caja {i + 1}"
        if not isinstance(c, dict):
            err.append(f"{pre}: no es un objeto")
            continue
        for k in _CAMPOS_CAJA_OBLIGATORIOS:
            if k not in c:
                err.append(f"{pre}: falta {k}")
        sobran = set(c) - _CAMPOS_CAJA
        if sobran:
            err.append(f"{pre}: campos no permitidos {sorted(sobran)}")
        if not (isinstance(c.get("warehouseId"), str) and c["warehouseId"].startswith("WH-")):
            err.append(f"{pre}: warehouseId tiene que ser texto 'WH-…'")
        for k in ("shipCompanyId", "channelId"):
            v = c.get(k)
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                err.append(f"{pre}: {k} tiene que ser un entero (LONG) positivo")
        for k in ("weight", "length", "width", "height"):
            v = c.get(k)
            if not (isinstance(v, str) and _DOS_DECIMALES.match(v) and float(v) > 0):
                err.append(f"{pre}: {k} tiene que ser texto positivo con 2 decimales ('0.70')")
        if c.get("weightUnit") != "kg":
            err.append(f"{pre}: weightUnit tiene que ser 'kg' (México)")
        if c.get("dimensionUnit") != "cm":
            err.append(f"{pre}: dimensionUnit tiene que ser 'cm' (México)")
        ren = c.get("orderSendInfoList")
        if not isinstance(ren, list) or not ren:
            err.append(f"{pre}: orderSendInfoList vacío")
            continue
        for j, r in enumerate(ren):
            pr = f"{pre}, renglón {j + 1}"
            if not isinstance(r, dict):
                err.append(f"{pr}: no es un objeto")
                continue
            sobran = set(r) - _CAMPOS_RENGLON
            if sobran:
                err.append(f"{pr}: campos no permitidos {sorted(sobran)}")
            if not (isinstance(r.get("parentOrderSn"), str) and r["parentOrderSn"].startswith("PO-")):
                err.append(f"{pr}: parentOrderSn tiene que ser texto 'PO-…'")
            if not (isinstance(r.get("orderSn"), str) and r["orderSn"]):
                err.append(f"{pr}: falta orderSn (120011113)")
            q = r.get("quantity")
            if not isinstance(q, int) or isinstance(q, bool) or q <= 0:
                err.append(f"{pr}: quantity tiene que ser un entero positivo")
            else:
                suma[str(r.get("orderSn"))] = suma.get(str(r.get("orderSn")), 0) + q
            for k in ("goodsId", "skuId"):
                if k in r and (not isinstance(r[k], int) or isinstance(r[k], bool)):
                    err.append(f"{pr}: {k} tiene que ser entero (LONG)")
            pos_total.add(str(r.get("parentOrderSn")))
    if st == 0 and (len(cajas) != 1 or len(pos_total) != 1):
        err.append("sendType 0 es UN PO en UNA caja")
    if st == 1 and (len(cajas) < 2 or len(pos_total) != 1):
        err.append("sendType 1 es UN PO en VARIAS cajas (todo el PO en esta llamada)")
    if st == 2 and (len(cajas) != 1 or len(pos_total) < 2):
        err.append("sendType 2 es VARIOS PO en UNA caja")
    if esperado is not None and suma != {str(k): int(v) for k, v in esperado.items()}:
        err.append(f"las piezas no cuadran con lo pedido (120013002): en cajas {suma}, "
                   f"pedidas {dict(esperado)}")
    return err


def huella(obj: Any) -> str:
    """sha256 del JSON canónico. Para una llamada: la huella de SU payload
    (informativa). Lo que se APRUEBA es `huella_aprobacion`."""
    crudo = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


def contenido_aprobacion(ventas: list[str], llamadas: list[dict[str, Any]],
                         fecha: dict[str, Any], emitida: int) -> dict[str, Any]:
    """Lo que firma la aprobación de un GRUPO: los payloads de TODAS sus
    llamadas, la FECHA de envío (el payload sólo lleva horas), el costo
    cotizado de cada caja y el momento de la vista previa. PURA."""
    return {"version": 2, "ventas": list(ventas), "emitida": int(emitida),
            "fecha_envio": fecha["fecha_envio"], "horas": int(fecha["horas"]),
            "llamadas": [{"payload": ll["payload"],
                          "costos": [((p.get("cotizacion") or {}).get("elegido") or {})
                                     .get("estimatedAmount") for p in ll["paquetes"]]}
                         for ll in llamadas]}


def huella_aprobacion(contenido: dict[str, Any]) -> str:
    return huella(contenido)


# ═════════════════════════════════════════════════════════════════════════════
#  6 · LECTURAS (red, kubera, Odoo)
# ═════════════════════════════════════════════════════════════════════════════

class _SinPresupuesto(RuntimeError):
    pass


class _Sesion:
    """Cuenta y acota las llamadas a Temu de UNA vista previa (la cuota es la de
    producción) y el tiempo total."""

    def __init__(self, tope: int, segundos: float):
        self.tope = int(tope)
        self.limite = time.monotonic() + float(segundos)
        self.n = 0
        self.por_tipo: dict[str, int] = {}
        self.errores: dict[str, str] = {}

    async def llamar(self, tipo: str, datos: dict[str, Any]) -> dict[str, Any]:
        from services import temu
        if self.n >= self.tope:
            raise _SinPresupuesto(f"se llegó al tope de {self.tope} llamadas a Temu")
        if time.monotonic() > self.limite:
            raise _SinPresupuesto("se acabó el tiempo de la vista previa")
        self.n += 1
        self.por_tipo[tipo] = self.por_tipo.get(tipo, 0) + 1
        try:
            return await asyncio.wait_for(temu.llamar(tipo, datos), timeout=45)
        except Exception as exc:  # noqa: BLE001
            self.errores.setdefault(tipo, _texto_error(exc))
            raise


def _texto_error(exc: BaseException) -> str:
    try:
        from services.investigacion_temu import redactar_texto
        return redactar_texto(str(exc) or type(exc).__name__, 300)
    except Exception:  # noqa: BLE001
        return (str(exc) or type(exc).__name__)[:300]


def _lista(r: Any, *llaves: str) -> list[dict[str, Any]]:
    if isinstance(r, dict):
        for k in llaves:
            v = r.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


async def _grupos_combinados(s: _Sesion) -> dict[str, frozenset[str]]:
    """{PO: su grupo} según `bg.order.combinedshipment.list.get`. LANZA si falla:
    sin esto no se sabe quién va con quién."""
    res = await s.llamar("bg.order.combinedshipment.list.get", {})
    salida: dict[str, frozenset[str]] = {}
    for g in _lista(res, "combinedShippingGroups"):
        pos = frozenset(str(x.get("parentOrderSn") or "").strip()
                        for x in _lista(g, "combinedShippingGroup")) - {""}
        if len(pos) > 1:
            for po in pos:
                salida[po] = pos
    return salida


async def _paquetes_sin_enviar(s: _Sesion, pos: list[str]) -> dict[str, list[str]]:
    """{PO: [packageSn]} con etiqueta comprada y envío sin confirmar. LANZA si
    una consulta falla."""
    salida: dict[str, list[str]] = {}
    for i in range(0, len(pos), 20):
        lote = pos[i:i + 20]
        for pagina in range(1, 6):
            res = await s.llamar("bg.order.unshipped.package.get",
                                 {"parentOrderSnList": lote, "pageNumber": pagina,
                                  "pageSize": 20})
            filas = _lista(res, "unshippedPackage")
            for q in filas:
                # Por MENCIÓN, igual que `pedidos_temu`: si Temu ignorara el
                # filtro, un paquete ajeno no bloquea a nadie; uno propio, sí.
                txt = json.dumps(q, ensure_ascii=False, default=str)
                for po in lote:
                    if po in txt:
                        salida.setdefault(po, []).append(str(q.get("packageSn") or "?"))
            if len(filas) < 20:
                break
    return salida


async def _etiquetas_de(s: _Sesion, pos: list[str]) -> dict[str, list[dict[str, Any]]]:
    """{PO: [etiquetas de Temu en CUALQUIER estado]} de `temu.logistics.label.list.get`.
    LANZA si una consulta falla."""
    salida: dict[str, list[dict[str, Any]]] = {}
    for i in range(0, len(pos), 20):
        lote = pos[i:i + 20]
        for pagina in range(1, 6):
            res = await s.llamar("temu.logistics.label.list.get",
                                 {"parentOrderSnList": lote, "pageNumber": pagina,
                                  "pageSize": 50})
            filas = _lista(res, "shippingLabelInfoList")
            for et in filas:
                dueños = {str(o.get("parentOrderSn") or "")
                          for o in _lista(et, "orderInfoList")}
                for po in dueños & set(lote):
                    salida.setdefault(po, []).append(et)
            if len(filas) < 50:
                break
    return salida


async def _resultados_de(s: _Sesion, psns: list[str]) -> dict[str, dict[str, Any]]:
    """{packageSn: fila de `bg.logistics.shipment.result.get`} (almacén, piezas
    por renglón, estado de la etiqueta). LANZA si una consulta falla."""
    salida: dict[str, dict[str, Any]] = {}
    lista = sorted({p for p in psns if p})
    for i in range(0, len(lista), 20):
        res = await s.llamar("bg.logistics.shipment.result.get",
                             {"packageSnList": lista[i:i + 20]})
        for p in _lista(res, "packageInfoResultList"):
            if p.get("packageSn"):
                salida[str(p["packageSn"])] = p
    return salida


def _cola_espera(dias: int, tope: int) -> list[dict[str, Any]]:
    """TODAS las ventas de Temu que esperan su guía, de la más vieja a la más
    nueva. SIN muestreo: la cola de creación (`pendientes_sin_orden`) toma la
    mitad nueva y la mitad vieja, y las del medio no se descontarían del stock.
    ⚠️ BLOQUEA. Sólo SELECT. LANZA si falla (no se planea a ciegas)."""
    from services import odoo_ventas_log
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        """/* tgc:cola */ select external_order_id, cuenta, creado_at
             from ops.odoo_sale_orders
            where canal = %(c)s and odoo_order_id is null and accion = %(a)s
              and creado_at > now() - make_interval(days => %(d)s)
            order by creado_at asc, external_order_id asc
            limit %(l)s""",
        {"c": CANAL, "a": odoo_ventas_log.ACCION_ESPERA, "d": int(dias), "l": int(tope)})
    return [{"order_id": str(f["external_order_id"]), "cuenta": f.get("cuenta"),
             "creado_at": f.get("creado_at")} for f in filas]


def _demanda_ajena(dias: int) -> dict[str, float]:
    """{sku: piezas} prometidas que Odoo NO reserva y que no son de la cola de
    Temu: ventas de OTROS canales que esperan su guía, y órdenes de cualquier
    canal que Odoo dejó en borrador (`no_se_pudo_confirmar`). ⚠️ BLOQUEA. Sólo
    SELECT. LANZA si falla (a diferencia de `piezas_sin_orden`, que devuelve
    {} = "no restes nada")."""
    from services import odoo_ventas_log
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        """/* tgc:ajena */ select i.sku::text as sku, sum(i.cantidad) as piezas
             from ops.odoo_sale_orders o
             join ops.odoo_sale_order_items i
               on i.canal = o.canal and i.cuenta = o.cuenta
              and i.external_order_id = o.external_order_id
            where o.creado_at > now() - make_interval(days => %(d)s)
              and ((o.canal <> %(c)s and o.odoo_order_id is null and o.accion = %(a)s)
                   or o.accion = %(nc)s)
            group by 1""",
        {"d": int(dias), "c": CANAL, "a": odoo_ventas_log.ACCION_ESPERA,
         "nc": odoo_ventas_log.ACCION_SIN_RESERVA})
    return {str(f["sku"]).strip(): float(f["piezas"] or 0) for f in filas
            if str(f.get("sku") or "").strip() and float(f.get("piezas") or 0) > 0}


def _historial_candidatos(skus: list[str], por_cantidad: int = 3,
                          por_sku: int = 12) -> list[dict[str, Any]]:
    """Ventas de Temu YA SURTIDAS de UN solo SKU, con guía propia (no
    compartida, no dividida): de ahí sale lo que se declaró al comprar su guía.
    El tope va POR SKU dentro del SQL (antes era un `limit 600` global: un SKU
    con muchas ventas dejaba sin muestras a otro según qué más hubiera en el
    lote, y la vista previa y la compra veían historiales distintos).
    ⚠️ BLOQUEA (psycopg2): llamar desde un hilo. Sólo SELECT."""
    from services import supabase_db as sdb
    if not skus:
        return []
    filas = sdb.fetch_all(
        """/* tgc:historial */ with v as (
             select o.external_order_id po, o.guia, max(o.creado_at) creado_at,
                    count(*) lineas, min(i.sku::text) sku, sum(i.cantidad) q
               from ops.odoo_sale_orders o
               join ops.odoo_sale_order_items i using (canal, external_order_id)
              where o.canal = 'temu' and o.odoo_order_id is not null
                and coalesce(o.guia, '') <> '' and position('+' in o.guia) = 0
                and o.creado_at > now() - interval '120 days'
              group by 1, 2),
           g as (select guia, count(*) n from v group by 1),
           c as (select v.po, v.sku, v.q, v.creado_at
                   from v join g using (guia)
                  where v.lineas = 1 and g.n = 1 and v.q > 0 and v.sku = any(%(s)s)),
           r1 as (select c.*, row_number() over (partition by sku, q
                                                 order by creado_at desc, po) rq
                    from c),
           r2 as (select r1.*, row_number() over (partition by sku
                                                  order by creado_at desc, po) rs
                    from r1 where rq <= %(pq)s)
           select po, sku, q, creado_at from r2
            where rs <= %(ps)s
            order by sku, creado_at desc, po""",
        {"s": list(skus), "pq": int(por_cantidad), "ps": int(por_sku)})
    salida: list[dict[str, Any]] = []
    cuenta_q: dict[tuple[str, int], int] = {}
    cuenta_s: dict[str, int] = {}
    for f in filas:
        k = (str(f["sku"]), int(f["q"] or 0))
        if k[1] <= 0 or cuenta_q.get(k, 0) >= por_cantidad or cuenta_s.get(k[0], 0) >= por_sku:
            continue
        cuenta_q[k] = cuenta_q.get(k, 0) + 1
        cuenta_s[k[0]] = cuenta_s.get(k[0], 0) + 1
        salida.append({"po": str(f["po"]), "sku": k[0], "cantidad": k[1]})
    return salida


def _medidas_almacen(skus: list[str]) -> dict[str, dict[str, Any]]:
    """Lo que almacén midió del producto empacado (Checklist). ⚠️ BLOQUEA. SELECT."""
    from services import supabase_db as sdb
    if not skus:
        return {}
    filas = sdb.fetch_all(
        """/* tgc:medidas */ select sku::text sku, almacen_largo_cm largo_cm,
                  almacen_ancho_cm ancho_cm, almacen_alto_cm alto_cm, almacen_peso_kg peso_kg
             from core.products
            where sku = any(%(s)s::citext[]) and almacen_peso_kg is not null
              and almacen_largo_cm is not null and almacen_ancho_cm is not null
              and almacen_alto_cm is not null""", {"s": list(skus)})
    return {str(f["sku"]): {k: float(f[k]) for k in ("largo_cm", "ancho_cm", "alto_cm", "peso_kg")}
            for f in filas}


def _medidas_catalogo(skus: list[str]) -> dict[str, dict[str, float]]:
    """Las medidas de CATÁLOGO (costing.costos_validados: CBM reconstruido, NO
    medidas). Sólo para detectar un historial que las copió. ⚠️ BLOQUEA. SELECT.
    LANZA si falla."""
    from services import supabase_db as sdb
    if not skus:
        return {}
    filas = sdb.fetch_all(
        """/* tgc:catalogo */ select sku::text sku, largo largo_cm, ancho ancho_cm, alto alto_cm
             from costing.costos_validados
            where sku = any(%(s)s::citext[]) and largo is not null and ancho is not null
              and alto is not null""", {"s": list(skus)})
    return {str(f["sku"]): {k: float(f[k]) for k in ("largo_cm", "ancho_cm", "alto_cm")}
            for f in filas}


def _stock_odoo(skus: list[str]) -> tuple[dict[str, int], dict[int, dict[int, float]]]:
    """({sku: product_id}, {product_id: {almacén: libres}}). ⚠️ BLOQUEA (XML-RPC)."""
    prods = odoo_ventas.productos_por_sku(skus)
    ids = {s: int(p["id"]) for s, p in prods.items()}
    return ids, odoo_ventas.libre_por_almacen(sorted(set(ids.values())))


# ── La bitácora durable (lecturas) ──────────────────────────────────────────

def es_tabla_ausente(exc: BaseException) -> bool:
    """¿El error es que `ops.temu_guias_compras` no existe (migración 0061 sin
    aplicar)? pgcode 42P01."""
    if getattr(exc, "pgcode", None) == "42P01":
        return True
    txt = str(exc)
    return "temu_guias_compras" in txt and ("does not exist" in txt or "no existe" in txt)


def _motivo_bitacora(exc: BaseException) -> str:
    if es_tabla_ausente(exc):
        return ("falta la tabla ops.temu_guias_compras (migración 0061, la aplica Eduardo): "
                "sin bitácora durable no se compra")
    return f"no se pudo leer la bitácora de compras de guías ({str(exc)[:120]}): no se compra"


def _reclamos_de(pos: list[str]) -> dict[str, dict[str, Any]]:
    """{PO: su fila en la bitácora de compras}. ⚠️ BLOQUEA. Sólo SELECT. LANZA."""
    from services import supabase_db as sdb
    if not pos:
        return {}
    filas = sdb.fetch_all(
        """/* tgc:bitacora */ select parent_order_sn, estado, reclamo, grupo, huella,
                  aprobado_por, send_type, reparto, reparto_real, package_sn,
                  fecha_envio, horas, codigo, motivo, creado_at, actualizado_at
             from ops.temu_guias_compras
            where parent_order_sn = any(%(p)s)""", {"p": list(pos)})
    return {str(f["parent_order_sn"]): dict(f) for f in filas}


def reparto_comprado(order_id: str) -> list[dict[str, Any]] | None:
    """
    [{almacen_id, sku, cantidad}] de la guía que compró EL PANEL para esa venta,
    o None si no la compró el panel (la compraron a mano, o no hay guía).
    ⚠️ BLOQUEA. LANZA si la bitácora no se puede leer: quien llama decide
    (`es_tabla_ausente` → no hay compras del panel).

    Primero lo que dijo Temu (`reparto_real`); si le falta algo, lo planeado
    (`reparto`), que es exactamente el payload que se mandó.
    """
    fila = _reclamos_de([str(order_id)]).get(str(order_id))
    if not fila or fila.get("estado") not in ESTADOS_HECHOS:
        return None

    def _agg(reparto: Any) -> list[dict[str, Any]] | None:
        if not isinstance(reparto, list) or not reparto:
            return None
        acc: dict[tuple[int, str], int] = {}
        for x in reparto:
            try:
                w, s, n = int(x["almacen_id"]), str(x["sku"]).strip(), int(x["quantity"])
            except (KeyError, TypeError, ValueError):
                return None
            if not s or n <= 0:
                return None
            acc[(w, s)] = acc.get((w, s), 0) + n
        return [{"almacen_id": w, "sku": s, "cantidad": n} for (w, s), n in sorted(acc.items())]

    return _agg(fila.get("reparto_real")) or _agg(fila.get("reparto"))


def almacenes_de_paquetes(psns: list[str]) -> dict[str, int]:
    """{packageSn: almacén de Odoo} de las cajas que compró el panel. ⚠️
    BLOQUEA. SELECT. Nunca lanza: sin bitácora, {} (todo queda como antes)."""
    from services import supabase_db as sdb
    lista = sorted({str(p) for p in psns if p})
    if not lista:
        return {}
    try:
        filas = sdb.fetch_all(
            """/* tgc:paquetes */ select e->>'packageSn' as psn, e->>'almacen_id' as almacen_id
                 from ops.temu_guias_compras t,
                      jsonb_array_elements(coalesce(t.reparto_real, '[]'::jsonb)) e
                where t.estado in ('comprada', 'pendiente')
                  and t.package_sn && %(p)s::text[]""", {"p": lista})
    except Exception as exc:  # noqa: BLE001
        log.debug("almacenes_de_paquetes: %s", str(exc)[:150])
        return {}
    salida: dict[str, int] = {}
    dudosos: set[str] = set()
    for f in filas:
        psn, wid = str(f.get("psn") or ""), _entero(f.get("almacen_id"))
        if not psn or wid is None or psn not in lista:
            continue
        if psn in salida and salida[psn] != wid:
            dudosos.add(psn)
        salida[psn] = wid
    return {k: v for k, v in salida.items() if k not in dudosos}


# Lo que ya se midió de guías históricas: no cambia (una guía comprada no se
# re-declara), así que se guarda por PO un buen rato para no gastar cuota.
_HIST_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_HIST_TTL = 6 * 3600.0


async def _historial_empaque(s: _Sesion, skus: list[str]) -> dict[str, Any]:
    """Muestras de peso y caja declaradas en guías ya compradas. Nunca lanza:
    sin historial, el empaque sale "ninguna" y no se compra."""
    info: dict[str, Any] = {"muestras": [], "fuente": None, "errores": {}, "candidatos": 0}
    try:
        cand = await asyncio.to_thread(_historial_candidatos, skus)
    except Exception as exc:  # noqa: BLE001
        info["errores"]["kubera"] = str(exc)[:200]
        return info
    info["candidatos"] = len(cand)
    ahora = time.monotonic()
    faltan = [c for c in cand if not (c["po"] in _HIST_CACHE
                                      and ahora - _HIST_CACHE[c["po"]][0] < _HIST_TTL)]
    if faltan:
        pos = [c["po"] for c in faltan]
        leidas: dict[str, dict[str, Any] | None] = {}
        try:
            etq = await _etiquetas_de(s, pos)
            info["fuente"] = "temu.logistics.label.list.get"
            for po in pos:
                buenas = [e for e in etq.get(po, [])
                          if _entero(e.get("shippingLabelStatus")) == 1]
                # Sólo paquetes que llevan ESTE PO y nada más, y uno por PO.
                if len(buenas) != 1 or {str(o.get("parentOrderSn")) for o in
                                        _lista(buenas[0], "orderInfoList")} != {po}:
                    leidas[po] = None
                    continue
                m = muestra_de_paquete(buenas[0])
                q = sum(_entero(o.get("quantity")) or 0 for o in _lista(buenas[0], "orderInfoList"))
                leidas[po] = ({**m, "cantidad_paquete": q,
                               "package_sn": str(buenas[0].get("packageSn") or "")} if m else None)
        except _SinPresupuesto as exc:
            info["errores"]["presupuesto"] = str(exc)
        except Exception as exc:  # noqa: BLE001 — respaldo con endpoints ya probados
            info["errores"]["temu.logistics.label.list.get"] = _texto_error(exc)
            leidas = await _historial_respaldo(s, pos[:10], info)
        for po, m in leidas.items():
            _HIST_CACHE[po] = (ahora, m)
    for c in cand:
        m = (_HIST_CACHE.get(c["po"]) or (0, None))[1]
        if not m:
            continue
        if m.get("cantidad_paquete") and int(m["cantidad_paquete"]) != int(c["cantidad"]):
            continue     # el paquete no lleva lo que dice la bitácora: no sirve
        info["muestras"].append({"sku": c["sku"], "cantidad": c["cantidad"],
                                 "parent_order_sn": c["po"],
                                 "package_sn": m.get("package_sn"),
                                 **{k: m[k] for k in ("peso_kg", "largo_cm", "ancho_cm",
                                                      "alto_cm")}})
    return info


async def _historial_respaldo(s: _Sesion, pos: list[str],
                              info: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
    """Si `label.list.get` no contesta: el detalle da el packageSn y
    `shipment.result.get` (probado en vivo el 24-sep) da peso y caja."""
    info["fuente"] = "bg.order.detail.v2.get + bg.logistics.shipment.result.get"
    salida: dict[str, dict[str, Any] | None] = {}
    psn_de: dict[str, str] = {}
    for po in pos:
        try:
            det = await s.llamar("bg.order.detail.v2.get", {"parentOrderSn": po})
        except Exception:  # noqa: BLE001
            continue
        psns = {str((p or {}).get("packageSn") or "") for o in (det.get("orderList") or [])
                for p in (o.get("packageSnInfo") or []) if isinstance(p, dict)} - {""}
        if len(psns) == 1:
            psn_de[po] = next(iter(psns))
        else:
            salida[po] = None
    if psn_de:
        try:
            res = await s.llamar("bg.logistics.shipment.result.get",
                                 {"packageSnList": sorted(set(psn_de.values()))})
            por_psn = {str(p.get("packageSn")): p for p in _lista(res, "packageInfoResultList")}
            for po, psn in psn_de.items():
                p = por_psn.get(psn)
                ren = _lista(p, "orderSendInfoList") if p else []
                if (not p or _entero(p.get("shippingLabelStatus")) != 1
                        or {str(o.get("parentOrderSn")) for o in ren} != {po}):
                    salida[po] = None
                    continue
                m = muestra_de_paquete(p)
                salida[po] = ({**m, "cantidad_paquete": sum(_entero(o.get("quantity")) or 0
                                                            for o in ren),
                               "package_sn": psn} if m else None)
        except Exception as exc:  # noqa: BLE001
            info["errores"]["bg.logistics.shipment.result.get"] = _texto_error(exc)
    return salida


# ═════════════════════════════════════════════════════════════════════════════
#  7 · LA BITÁCORA DURABLE (escrituras: sólo `comprar` y `conciliar`)
# ═════════════════════════════════════════════════════════════════════════════

class _Ocupado(Exception):
    """Algún PO del grupo ya tenía una compra abierta: se deshace el reclamo."""


def _reclamar(filas: list[dict[str, Any]]) -> list[str]:
    """
    Reclama TODAS las filas en UNA transacción, o ninguna. ⚠️ BLOQUEA.

    Devuelve [] si las tomó todas, o los PO que ya tenían una compra que BLOQUEA
    (entonces no escribió nada). LANZA si la bitácora no se puede escribir
    (tabla ausente, kubera caída): quien llama NO compra.

    `on conflict … do update … where estado in ('rechazada','no_enviada')`: dos
    procesos que reclaman el mismo PO a la vez se forman en el índice único; el
    segundo ve la fila del primero en 'en_curso', no la toma y se deshace.
    """
    from services import supabase_db as sdb

    def _hacer() -> list[str]:
        with sdb.get_cursor() as cur:
            ocupados: list[str] = []
            for f in filas:
                cur.execute(
                    """/* tgc:reclamar */ insert into ops.temu_guias_compras as t
                         (parent_order_sn, estado, reclamo, grupo, huella, aprobado_por,
                          send_type, payload, reparto, fecha_envio, horas)
                       values (%(po)s, 'en_curso', %(reclamo)s, %(grupo)s, %(huella)s,
                               %(quien)s, %(send_type)s, %(payload)s::jsonb,
                               %(reparto)s::jsonb, %(fecha)s, %(horas)s)
                       on conflict (parent_order_sn) do update set
                          estado = 'en_curso', reclamo = excluded.reclamo,
                          grupo = excluded.grupo, huella = excluded.huella,
                          aprobado_por = excluded.aprobado_por,
                          send_type = excluded.send_type, payload = excluded.payload,
                          reparto = excluded.reparto, reparto_real = null,
                          package_sn = null, fecha_envio = excluded.fecha_envio,
                          horas = excluded.horas, codigo = null, motivo = null,
                          intentos = t.intentos + 1, actualizado_at = now()
                        where t.estado in ('rechazada', 'no_enviada')
                       returning parent_order_sn""",
                    {"po": f["po"], "reclamo": f["reclamo"], "grupo": list(f["grupo"]),
                     "huella": f["huella"], "quien": f["quien"], "send_type": f["send_type"],
                     "payload": json.dumps(f["payload"], ensure_ascii=False),
                     "reparto": json.dumps(f["reparto"], ensure_ascii=False),
                     "fecha": f["fecha"], "horas": f["horas"]})
                if not cur.fetchone():
                    ocupados.append(f["po"])
            if ocupados:
                raise _Ocupado(ocupados)        # get_cursor hace rollback: nada queda
            return []

    try:
        return sdb.reintentar_transitorio(_hacer)
    except _Ocupado as exc:
        return list(exc.args[0])


def _actualizar_reclamo(reclamo: str, cambios: dict[str, dict[str, Any]]) -> int:
    """Pasa las filas de ESTE reclamo a su nuevo estado. ⚠️ BLOQUEA. LANZA."""
    from services import supabase_db as sdb

    def _hacer() -> int:
        n = 0
        with sdb.get_cursor() as cur:
            for po, c in cambios.items():
                rr = c.get("reparto_real")
                cur.execute(
                    """/* tgc:actualizar */ update ops.temu_guias_compras
                          set estado = %(e)s,
                              package_sn = coalesce(%(psn)s::text[], package_sn),
                              reparto_real = coalesce(%(rr)s::jsonb, reparto_real),
                              codigo = coalesce(%(cod)s, codigo),
                              motivo = coalesce(%(m)s, motivo),
                              actualizado_at = now()
                        where parent_order_sn = %(po)s and reclamo = %(r)s""",
                    {"e": c["estado"], "psn": c.get("package_sn"),
                     "rr": json.dumps(rr, ensure_ascii=False) if rr else None,
                     "cod": c.get("codigo"), "m": (c.get("motivo") or None) and
                     str(c["motivo"])[:300], "po": po, "r": reclamo})
                n += cur.rowcount or 0
        return n

    return sdb.reintentar_transitorio(_hacer)


async def _anotar(reclamo: str, cambios: dict[str, dict[str, Any]], *,
                  blindado: bool = False) -> bool:
    """Escribe el nuevo estado en la bitácora. Si NO se puede, la fila se queda
    como estaba ('en_curso' o 'pendiente'), que BLOQUEA: fallar aquí es seguro.
    `blindado`: la escritura sigue aunque cancelen la tarea (va en el camino de
    una cancelación a media compra)."""
    if not cambios:
        return True
    try:
        trabajo = asyncio.to_thread(_actualizar_reclamo, reclamo, cambios)
        if blindado:
            await asyncio.shield(trabajo)
        else:
            await trabajo
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("bitácora de compras de guías: no se pudo anotar %s → %s (%s); la fila "
                  "se queda como estaba y BLOQUEA hasta conciliar", ", ".join(sorted(cambios)),
                  sorted({c["estado"] for c in cambios.values()}), str(exc)[:150])
        return False


# ═════════════════════════════════════════════════════════════════════════════
#  8 · LA VISTA PREVIA (sólo lecturas)
# ═════════════════════════════════════════════════════════════════════════════

_PLAN_LOCK = asyncio.Lock()
_PLAN_CACHE: dict[str, Any] = {"ts": 0.0, "clave": None, "res": None}
_PLAN_TTL = 45.0


def _limite_omision() -> int:
    return max(1, min(60, int(getattr(settings, "temu_guias_plan_limite", 20) or 20)))


def _hora_txt(v: Any) -> str:
    if isinstance(v, datetime):
        return v.astimezone(ZONA).isoformat(timespec="minutes") if v.tzinfo else v.isoformat()
    return str(v or "—")


async def plan_guias(limite: int | None = None, *, solo: str | None = None,
                     ahora: datetime | None = None, usar_cache: bool = True,
                     tope_llamadas: int = 150, segundos_max: float = 150.0,
                     emitida: int | None = None,
                     medidas: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    El plan de guías de las ventas de Temu que esperan su guía. SOLO LECTURAS:
    no compra, no escribe en Temu, ni en Odoo, ni en kubera. Nunca lanza.

    Por grupo: cajas, almacén, piezas, fecha de envío, peso y caja con su
    fuente, la paquetería elegida con su costo, el payload EXACTO que se
    mandaría a `shipment.create`, y —si se puede comprar— la APROBACIÓN (su
    huella y cuándo vence). `limite` = cuántas ventas, de la más vieja, se
    planean. `solo` = un PO: se lee hasta su grupo (lo anterior se descuenta
    del stock) y sólo se cotiza ése. `medidas` = las cajas pesadas y medidas
    en el panel ({clave de caja: {peso_kg, largo_cm, ancho_cm, alto_cm}}).
    `emitida` la pasa `comprar()` para rehacer la huella que se aprobó.
    """
    lim = _limite_omision() if limite is None else max(1, min(60, int(limite)))
    try:
        med = leer_medidas(medidas)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": f"medidas inválidas: {exc}", "grupos": []}
    clave = (lim, solo, json.dumps(med, sort_keys=True) if med else None)
    fresco = (emitida is None and usar_cache and _PLAN_CACHE["res"] is not None
              and _PLAN_CACHE["clave"] == clave and time.monotonic() - _PLAN_CACHE["ts"] < _PLAN_TTL)
    if fresco:
        return {**_PLAN_CACHE["res"], "de_cache": True}
    async with _PLAN_LOCK:
        if (emitida is None and usar_cache and _PLAN_CACHE["res"] is not None
                and _PLAN_CACHE["clave"] == clave
                and time.monotonic() - _PLAN_CACHE["ts"] < _PLAN_TTL):
            return {**_PLAN_CACHE["res"], "de_cache": True}
        try:
            res = await _plan(lim, solo, ahora, tope_llamadas, segundos_max, emitida, med)
        except Exception as exc:  # noqa: BLE001 — la vista previa nunca tumba nada
            log.exception("plan_guias falló")
            res = {"ok": False, "error": str(exc)[:300], "grupos": []}
        if usar_cache and emitida is None and res.get("ok"):
            _PLAN_CACHE.update(ts=time.monotonic(), clave=clave, res=res)
        return res


async def _plan(lim: int, solo: str | None, ahora: datetime | None,
                tope_llamadas: int, segundos_max: float, emitida: int | None,
                medidas: dict[str, dict[str, float]]) -> dict[str, Any]:
    from services import temu

    momento = ahora or datetime.now(timezone.utc)
    emitida = int(emitida) if emitida is not None else int(momento.timestamp())
    ttl = _aprobacion_min()
    vence = datetime.fromtimestamp(emitida + ttl * 60, tz=timezone.utc).astimezone(ZONA)
    r: dict[str, Any] = {
        "ok": False, "modo": "VISTA PREVIA · no compra nada",
        "compra_habilitada": compra_habilitada(),
        "generado_at": momento.astimezone(ZONA).isoformat(timespec="seconds"),
        "emitida": emitida, "aprobacion_min": ttl,
        "aprobacion_vence": vence.isoformat(timespec="minutes"),
        "paqueteria_preferida": [f"{a}:{b}" if b else a for a, b in preferencias_paqueteria()],
        "medidas_capturadas": len(medidas),
        "reglas": {
            "fecha": ("compra + 2 días naturales; sábado, domingo o festivo → siguiente día "
                      "hábil. shipLaterLimitTime = 24 × días naturales (24-96 h)."),
            "reparto": ("odoo_ventas.planear_almacenes (la de Automatización): un almacén que "
                        "cubra todo; si no, por SKU. La cola se lee ENTERA y se descuenta de la "
                        "más vieja a la más nueva; lo ya comprado sale de su almacén real y lo "
                        "de otros canales se resta de los dos almacenes."),
            "empaque": ("medida capturada en el panel > medición de almacén (1 pieza) > "
                        "historial del mismo SKU × piezas (≥ N muestras, sin dispersión en peso "
                        "ni volumen, peso = el mayor, caja = la más grande) > nada. Densidad "
                        "creíble y nunca el catálogo."),
            "paqueteria": ("la más barata de TEMU_GUIAS_PAQUETERIA ('*' = cualquiera); empate → "
                           "la de menos días; nunca cambia sola."),
            "combinado": ("mismo almacén → una caja (sendType 2); almacenes mezclados → se "
                          "separa por almacén; un PO que cruza almacenes va solo (sendType 1). "
                          "El grupo se aprueba y se compra ENTERO."),
            "aprobacion": (f"la huella firma payloads + fecha de envío + costo por caja + "
                           f"momento de la vista previa, y vence a los {ttl} min."),
        },
        "grupos": [], "resumen": {}, "llamadas_temu": 0,
    }
    if not temu.disponible():
        return {**r, "error": "Temu no está configurado (faltan credenciales)"}
    try:
        mapa = mapa_almacenes()
    except ValueError as exc:
        return {**r, "error": str(exc)}
    mapa_inv = {wh: wid for wid, wh in mapa.items()}
    r["almacenes"] = [{"odoo_id": k, "almacen": dict(odoo_ventas._ALMACENES).get(k, str(k)),  # noqa: SLF001
                       "warehouse_id": v, "nombre_temu": NOMBRES_TEMU.get(v, v)}
                      for k, v in mapa.items()]
    fecha = fecha_envio(momento)
    r["fecha_envio"] = fecha
    dias = odoo_ventas._dias_espera()  # noqa: SLF001
    tope_cola = _cola_max()

    # 0 · LA COLA ENTERA, sin muestreo.
    try:
        cola = await asyncio.to_thread(_cola_espera, dias, tope_cola + 1)
    except Exception as exc:  # noqa: BLE001
        return {**r, "error": (f"no se pudo leer la cola de ventas que esperan guía "
                               f"({str(exc)[:160]}): sin ella no se sabe qué stock está "
                               "prometido")}
    truncada = len(cola) > tope_cola
    cola = cola[:tope_cola]
    ids = [c["order_id"] for c in cola]
    r["esperando"] = len(ids)
    r["cola_truncada"] = truncada
    if solo and solo not in ids:
        return {**r, "ok": True, "error": f"{solo} no está en la cola de ventas que esperan guía"}
    if not cola:
        return {**r, "ok": True, "resumen": {"grupos": 0, "comprables": 0}}

    s = _Sesion(tope_llamadas, segundos_max)

    # 1 · Quién va con quién.
    combinado: dict[str, Any] = {"consultado": False, "error": None, "grupos": 0}
    grupos_temu: dict[str, frozenset[str]] = {}
    try:
        grupos_temu = await _grupos_combinados(s)
        combinado.update(consultado=True, grupos=len(set(grupos_temu.values())))
    except Exception as exc:  # noqa: BLE001
        combinado["error"] = _texto_error(exc)
    r["combinado"] = combinado

    # 2 · Los grupos, del más viejo al más nuevo, y hasta dónde se leen. Lo que
    #     viene DESPUÉS del último grupo leído no le quita nada a los de arriba.
    visto: set[str] = set()
    todos: list[list[str]] = []
    for po in ids:
        if po in visto:
            continue
        g = grupos_temu.get(po)
        miembros = [x for x in ids if g and x in g] if g else [po]
        visto.update(miembros)
        todos.append(miembros)
    leer: list[list[str]] = []
    cuenta = 0
    for g in todos:
        if solo:
            leer.append(g)
            if solo in g:
                break
        else:
            if cuenta >= lim:
                break
            leer.append(g)
            cuenta += len(g)
    objetivo = next((g for g in leer if solo in g), None) if solo else None
    pos = [po for g in leer for po in g]
    r["leidas"] = len(pos)
    r["no_leidas"] = len(ids) - len(pos)

    # 3 · La bitácora de compras del panel (lo que ya compramos o quedó abierto).
    extra_pos = sorted({x for po in pos for x in (grupos_temu.get(po) or ())} - set(pos))
    bitacora: dict[str, dict[str, Any]] = {}
    bit_error: str | None = None
    try:
        bitacora = await asyncio.to_thread(_reclamos_de, pos + extra_pos)
    except Exception as exc:  # noqa: BLE001
        bit_error = _motivo_bitacora(exc)
    r["bitacora"] = {
        "leida": bit_error is None, "error": bit_error,
        "abiertas": sorted(po for po, f in bitacora.items() if f.get("estado") in ESTADOS_ABIERTOS),
        "hechas": sorted(po for po, f in bitacora.items() if f.get("estado") in ESTADOS_HECHOS)}

    # 4 · El detalle de cada venta leída.
    ventas: dict[str, dict[str, Any]] = {}
    for po in pos:
        try:
            det = await s.llamar("bg.order.detail.v2.get", {"parentOrderSn": po})
            ventas[po] = leer_venta(po, det or {})
        except Exception as exc:  # noqa: BLE001
            ventas[po] = {"parent_order_sn": po, "estado": None, "estado_txt": "?",
                          "renglones": [], "avisos": [], "limite_envio_ts": None,
                          "limite_envio": None, "cubre_plataforma": False,
                          "ilegible": True, "paquetes": [], "paquete_sin_numero": False,
                          "bloqueos": [f"no se pudo leer el detalle en Temu: {_texto_error(exc)}"]}

    # 5 · ¿Ya tiene guía por otro lado? Las dos fuentes que no son el detalle.
    #     Si una consulta falla, NADIE es comprable (no se sabe).
    for nombre, fn in (("bg.order.unshipped.package.get", _paquetes_sin_enviar),
                       ("temu.logistics.label.list.get", _etiquetas_de)):
        try:
            hallados = await fn(s, pos)
        except Exception as exc:  # noqa: BLE001
            for v in ventas.values():
                v["bloqueos"].append(f"no se pudo verificar en {nombre} que no tenga guía "
                                     f"({_texto_error(exc)[:120]}): no se compra a ciegas")
            continue
        for po, cosas in hallados.items():
            if po in ventas and cosas:
                v = ventas[po]
                v["bloqueos"].append(f"{nombre} ya muestra {len(cosas)} paquete(s)/etiqueta(s) "
                                     "de este PO: no se compra otra guía")
                if nombre == "bg.order.unshipped.package.get":
                    # Una guía comprada a mano: de qué almacén sale se lee abajo.
                    nums = {str(c) for c in cosas if c and c != "?"}
                    v["paquetes"] = sorted(set(v.get("paquetes") or []) | nums)
                    v["paquete_sin_numero"] = (v.get("paquete_sin_numero")
                                               or any(c == "?" for c in cosas))

    # 6 · Stock de Odoo, una lectura para todo el lote.
    skus = sorted({ren["sku"] for v in ventas.values() for ren in v["renglones"]})
    productos: dict[str, int] = {}
    libres: dict[int, dict[int, float]] = {}
    error_odoo = None
    if skus:
        try:
            productos, libres = await asyncio.to_thread(_stock_odoo, skus)
        except Exception as exc:  # noqa: BLE001
            error_odoo = str(exc)[:200]
    r["odoo_error"] = error_odoo
    foto_inicial = {s_: {str(w): n for w, n in libres.get(productos.get(s_, -1), {}).items()}
                    for s_ in skus}

    # 7 · Lo prometido que Odoo no reserva y no es de esta cola.
    ajena: dict[str, float] = {}
    ajena_error = None
    try:
        ajena = await asyncio.to_thread(_demanda_ajena, dias)
    except Exception as exc:  # noqa: BLE001
        ajena_error = str(exc)[:160]

    # 8 · Las guías YA compradas: de qué almacén sale cada caja.
    psns = sorted({p for v in ventas.values() for p in v.get("paquetes") or []})
    info_paquetes: dict[str, dict[str, Any]] = {}
    paquetes_error = None
    if psns:
        try:
            info_paquetes = await _resultados_de(s, psns)
        except Exception as exc:  # noqa: BLE001
            paquetes_error = _texto_error(exc)[:160]

    # 9 · Historial de empaque, mediciones y catálogo (sólo de lo que se cotiza).
    cotizados = [g for g in leer if objetivo is None or g == objetivo]
    skus_cot = sorted({ren["sku"] for g in cotizados for po in g for ren in ventas[po]["renglones"]})
    medidas_alm: dict[str, dict[str, Any]] = {}
    try:
        medidas_alm = await asyncio.to_thread(_medidas_almacen, skus_cot)
    except Exception as exc:  # noqa: BLE001
        r["medidas_error"] = str(exc)[:200]
    catalogo: dict[str, dict[str, float]] = {}
    catalogo_leido = True
    try:
        catalogo = await asyncio.to_thread(_medidas_catalogo, skus_cot)
    except Exception as exc:  # noqa: BLE001
        catalogo_leido = False
        r["catalogo_error"] = str(exc)[:200]
    hist = await _historial_empaque(s, skus_cot)
    r["historial"] = {"fuente": hist["fuente"], "candidatos": hist["candidatos"],
                      "muestras": len(hist["muestras"]), "errores": hist["errores"]}

    # 10 · El stock que queda. Lo de otros canales, A LO SEGURO, de los dos
    #      almacenes: no se sabe de cuál saldrá, y restarlo de ambos garantiza
    #      que el almacén que aquí se elija todavía lo tenga.
    restante = {pid: dict(por) for pid, por in libres.items()}
    r["demanda_ajena"] = {}
    for sku, n in sorted(ajena.items()):
        pid = productos.get(sku)
        if pid is None:
            continue
        for wid in mapa:
            descontar(restante, {pid: {wid: int(math.ceil(n))}})
        r["demanda_ajena"][sku] = n

    comprables = 0
    lote_incierto = False
    for miembros in leer:
        estados = {po: (bitacora.get(po) or {}).get("estado") for po in miembros}
        hechas = [po for po in miembros if estados[po] in ESTADOS_HECHOS]
        abiertas = [po for po in miembros if estados[po] in ESTADOS_ABIERTOS]
        a_comprar = [po for po in miembros if po not in hechas]
        g_todas = [ventas[po] for po in miembros]
        gs: dict[str, Any] = {"ventas": miembros, "a_comprar": a_comprar, "hechas": hechas,
                              "combinado": len(miembros) > 1,
                              "limites": {v["parent_order_sn"]: v.get("limite_envio")
                                          for v in g_todas},
                              "avisos": [f"{v['parent_order_sn']}: {a}" for v in g_todas
                                         for a in v.get("avisos") or []],
                              "motivos": [], "llamadas": [], "comprable": False,
                              "aprobacion": None, "apartado": False}
        extra: list[str] = []
        if error_odoo:
            extra.append(f"Odoo no contestó ({error_odoo}): sin stock no hay reparto")
        if not combinado["consultado"]:
            extra.append("no se pudo consultar qué órdenes quiere Temu juntas "
                         "(combinedshipment.list.get): no se compra a ciegas")
        if lote_incierto:
            extra.append("una venta anterior del lote no se pudo leer (o no se sabe de qué "
                         "almacén sale su guía ya comprada): el stock que queda es incierto")
        if bit_error:
            extra.append(bit_error)
        if ajena_error:
            extra.append(f"no se pudo leer lo que esperan otros canales ({ajena_error}): no se "
                         "sabe cuánto stock está prometido")
        for po in abiertas:
            f = bitacora[po]
            extra.append(f"{po}: la bitácora de compras la tiene '{f.get('estado')}' desde "
                         f"{_hora_txt(f.get('actualizado_at'))}: concilia en Temu antes de "
                         "comprar otra guía")
        g_temu = grupos_temu.get(miembros[0])
        if g_temu:
            fuera = (set(g_temu) - set(miembros)
                     - {x for x in g_temu if (bitacora.get(x) or {}).get("estado") in ESTADOS_HECHOS})
            if fuera:
                extra.append(f"Temu agrupa este PO con {len(fuera)} orden(es) que no están en la "
                             "cola de espera: el grupo se compra entero o no se compra — "
                             "revísalo a mano")

        # 10a · Lo ya comprometido: guías compradas (a mano o por el panel) desde
        #       su almacén REAL, y compras abiertas desde lo que se reclamó.
        resto: dict[str, dict[str, Any] | None] = {}
        incierto_aqui = False
        for po in miembros:
            v = ventas[po]
            f = bitacora.get(po) or {}
            if f.get("estado") in (ESTADOS_HECHOS | ESTADOS_ABIERTOS):
                # La compró (o la está comprando) el PANEL: la bitácora sabe
                # qué salió de dónde (lo real de result.get, o lo reclamado).
                c = consumo_de_reparto(f, productos)
                if not c and v.get("renglones"):
                    incierto_aqui = True
                    extra.append(f"{po}: la bitácora no dice de qué almacén sale su compra")
                prob: list[str] = []
                rv: dict[str, Any] = {**v, "renglones": []}
            elif v.get("paquetes") or v.get("paquete_sin_numero"):
                # Guía comprada A MANO: su almacén real, de shipment.result.get.
                if paquetes_error:
                    c, rv, prob = {}, v, [f"shipment.result.get falló: {paquetes_error}"]
                else:
                    c, rv, prob = consumo_de_paquetes(v, productos, info_paquetes, mapa_inv)
            else:
                resto[po] = v
                continue
            if prob:
                incierto_aqui = True
                extra.append(f"{po}: no se sabe de qué almacén sale su guía ya comprada "
                             f"({'; '.join(prob)[:160]})")
            if c:
                descontar(restante, c)
                gs["apartado"] = True
            resto[po] = rv if rv.get("renglones") else None
        if hechas and not a_comprar:
            gs["regla"] = {"id": "hecha", "texto": "La guía de este grupo ya la compró el panel."}

        # 10b · El plan de lo que falta. Una compra abierta no se planea (ya se
        #       descontó lo reclamado) y su bloqueo detiene al grupo entero.
        plan_ventas = [({**ventas[po], "bloqueos": list(ventas[po]["bloqueos"])
                         + ["tiene una compra abierta en la bitácora"]}
                        if po in abiertas else ventas[po]) for po in a_comprar]
        if plan_ventas and not error_odoo:
            plan = planear_paquetes(plan_ventas, productos, restante, mapa)
        else:
            plan = _sin_plan(plan_ventas, [])
        if plan["planeado"]:
            descontar(restante, plan["consumo"])
        elif not error_odoo:
            # No se compra, pero la venta sigue viva y sus piezas se van a ir:
            # que la siguiente del lote no cuente con ellas.
            ap = apartar([resto[po] for po in a_comprar if resto.get(po)], productos, restante)
            if ap:
                descontar(restante, ap)
                gs["apartado"] = True
        lote_incierto = (lote_incierto or incierto_aqui
                         or any(ventas[po].get("ilegible") for po in miembros))
        gs["regla"] = plan.get("regla") or gs.get("regla")
        gs["cobertura"] = plan.get("cobertura")
        gs["stock"] = {s_: foto_inicial.get(s_, {}) for s_ in
                       sorted({ren["sku"] for v in g_todas for ren in v["renglones"]})}
        gs["motivos"] = extra + plan["motivos"]
        cotizar = objetivo is None or miembros == objetivo
        for ll in plan["llamadas"]:
            gs["llamadas"].append(await _armar_llamada(
                s, ll, fecha, plan_ventas, hist["muestras"], medidas_alm, cotizar,
                medidas, catalogo, catalogo_leido))
        gs["comprable"] = bool(plan["planeado"] and not gs["motivos"] and gs["llamadas"]
                               and all(x["comprable"] for x in gs["llamadas"]))
        if gs["comprable"]:
            cont = contenido_aprobacion(plan["ventas"], gs["llamadas"], fecha, emitida)
            gs["aprobacion"] = {
                "huella": huella_aprobacion(cont), "emitida": emitida,
                "vence": r["aprobacion_vence"], "fecha_envio": fecha["fecha_envio"],
                "dia_envio": fecha["dia_envio"], "horas": fecha["horas"],
                "llamadas": len(gs["llamadas"]),
                "cajas": sum(len(ll["paquetes"]) for ll in gs["llamadas"]),
                "costo_mxn": round(sum(((p.get("cotizacion") or {}).get("elegido") or {})
                                       .get("monto") or 0 for ll in gs["llamadas"]
                                       for p in ll["paquetes"]), 2)}
        comprables += 1 if gs["comprable"] else 0
        r["grupos"].append(gs)

    r["llamadas_temu"] = s.n
    r["llamadas_por_tipo"] = s.por_tipo
    r["errores_temu"] = s.errores
    r["resumen"] = {
        "grupos": len(leer), "ventas": len(pos), "comprables": comprables,
        "bloqueados": len(leer) - comprables,
        "cajas": sum(len(ll["paquetes"]) for g in r["grupos"] for ll in g["llamadas"]),
        "costo_estimado_mxn": round(sum(
            ((p.get("cotizacion") or {}).get("elegido") or {}).get("monto") or 0
            for g in r["grupos"] if g["comprable"] for ll in g["llamadas"]
            for p in ll["paquetes"]), 2),
    }
    r["ok"] = True
    log.info("plan de guías Temu: %s grupos, %s comprables, %s llamadas a Temu",
             len(leer), comprables, s.n)
    return r


async def _armar_llamada(s: _Sesion, ll: dict[str, Any], fecha: dict[str, Any],
                         g_ventas: list[dict[str, Any]], muestras: list[dict[str, Any]],
                         medidas_alm: dict[str, dict[str, Any]], cotizar: bool,
                         manuales: dict[str, dict[str, float]],
                         catalogo: dict[str, dict[str, float]],
                         catalogo_leido: bool) -> dict[str, Any]:
    """Empaque, cotización y payload de UNA llamada. Nunca lanza."""
    motivos: list[str] = list(fecha["motivos"])
    paquetes = []
    for p in ll["paquetes"]:
        contenido: dict[str, int] = {}
        for e in p["renglones"]:
            contenido[e["sku"]] = contenido.get(e["sku"], 0) + int(e["quantity"])
        clave = clave_caja(p)
        emp = elegir_empaque(contenido, muestras, medidas_alm, manual=manuales.get(clave),
                             catalogo=catalogo, catalogo_leido=catalogo_leido)
        caja = {**p, "clave": clave, "empaque": emp, "cotizacion": None,
                "payload_cotizacion": None}
        if not emp["ok"]:
            motivos.append(f"caja de {p['almacen']}: {emp['motivo']}")
        if emp.get("peso_kg") and emp.get("largo_cm") and cotizar:
            caja["payload_cotizacion"] = payload_cotizacion(caja)
            try:
                res = await s.llamar("bg.logistics.shippingservices.get",
                                     caja["payload_cotizacion"])
                caja["cotizacion"] = elegir_canal(res or {})
                if caja["cotizacion"]["motivo"]:
                    motivos.append(f"caja de {p['almacen']}: {caja['cotizacion']['motivo']}")
            except Exception as exc:  # noqa: BLE001
                caja["cotizacion"] = {"elegido": None, "opciones": [], "mas_barata": None,
                                      "motivo": f"no se pudo cotizar: {_texto_error(exc)}"}
                motivos.append(f"caja de {p['almacen']}: no se pudo cotizar")
        elif not cotizar:
            motivos.append("no se cotizó (fuera del grupo pedido)")
        paquetes.append(caja)

    # La fecha contra el límite de envío de Temu, por PO.
    pos = set(ll["ventas"])
    for v in g_ventas:
        if v["parent_order_sn"] in pos and v.get("limite_envio_ts") and fecha.get("vence_ts") \
                and fecha["vence_ts"] > int(v["limite_envio_ts"]):
            motivos.append(f"{v['parent_order_sn']}: la fecha de envío ({fecha['fecha_envio']}) "
                           f"cae DESPUÉS del límite de Temu ({v['limite_envio']}): decide "
                           "Brandon — riesgo de retraso en el cumplimiento")

    salida = {"send_type": ll["send_type"], "explica": ll["explica"], "ventas": ll["ventas"],
              "paquetes": paquetes, "payload": None, "huella": None, "motivos": motivos,
              "comprable": False, "errores_payload": []}
    try:
        payload = payload_compra({**ll, "paquetes": paquetes}, fecha["horas"])
    except ValueError as exc:
        salida["motivos"] = motivos + ([] if motivos else [str(exc)])
        return salida
    esperado: dict[str, int] = {}
    for e in (x for p in paquetes for x in p["renglones"]):
        esperado[e["orderSn"]] = esperado.get(e["orderSn"], 0) + int(e["quantity"])
    pedido = {ren["orderSn"]: int(ren["cantidad"]) for v in g_ventas
              if v["parent_order_sn"] in pos for ren in v["renglones"]}
    if esperado != pedido:
        motivos.append("las cajas no llevan exactamente lo pedido en Temu")
    errores = validar_payload_compra(payload, pedido)
    salida.update(payload=payload, huella=huella(payload), errores_payload=errores,
                  motivos=motivos + errores)
    salida["comprable"] = not salida["motivos"]
    return salida


# ═════════════════════════════════════════════════════════════════════════════
#  9 · LA COMPRA — escrita, APAGADA, y no se invoca desde ningún lado
# ═════════════════════════════════════════════════════════════════════════════

# UNA compra a la vez en este proceso; entre procesos manda la bitácora.
_COMPRA_LOCK = asyncio.Lock()
# Segunda red, en memoria: compras cuyo resultado no se supo. La primera es la
# bitácora durable ('desconocido' / 'en_curso').
_DESCONOCIDAS: set[str] = set()


def _codigo(exc: BaseException) -> str | None:
    """El errorCode de Temu, si Temu CONTESTÓ con uno; None = no se sabe qué pasó."""
    m = re.search(r"errorCode=(\d+)", str(exc))
    return m.group(1) if m else None


def clasificar_error(exc: BaseException) -> str:
    """Qué significa un error de `shipment.create`:
      · 'rechazada'     — un rechazo DOCUMENTADO que ocurre antes de comprar
                          (pasarela o validación de negocio de la ficha);
      · 'ya_solicitada' — 120012013: ya había una compra de esa orden;
      · 'desconocido'   — todo lo demás: 4000000, códigos no documentados,
                          timeouts, cancelación. NO se sabe si compró."""
    if not isinstance(exc, Exception):
        return "desconocido"
    cod = _codigo(exc)
    if cod == YA_SOLICITADA:
        return "ya_solicitada"
    if cod in RECHAZO_SEGURO:
        return "rechazada"
    return "desconocido"


async def _verificar_antes(ll: dict[str, Any], aprob: dict[str, Any],
                           momento: datetime | None = None) -> list[str]:
    """
    La última revisión, JUSTO antes de `shipment.create` de UNA llamada. Vacía
    = se puede comprar. Relee el detalle de SUS PO (estado 2, sin paquete, sin
    cancelación ni cambio de dirección pendientes, mismas piezas),
    `unshipped.package.get`, y recalcula la fecha: si el día ya no da la fecha
    aprobada (o las horas del payload), no se compra.
    """
    motivos: list[str] = []
    payload = ll["payload"]
    f = fecha_envio(momento or datetime.now(timezone.utc))
    if not f["valida"]:
        motivos.extend(f["motivos"])
    if f["fecha_envio"] != aprob["fecha_envio"] or str(f["horas"]) != str(payload["shipLaterLimitTime"]):
        motivos.append(f"la fecha de envío ya no es la aprobada: comprando ahora sería el "
                       f"{f['fecha_envio']} a {f['horas']} h y se aprobó el "
                       f"{aprob['fecha_envio']} a {payload['shipLaterLimitTime']} h — vuelve a "
                       "revisar la vista previa")
    esperado: dict[str, dict[str, int]] = {}
    for caja in payload["sendRequestList"]:
        for rr in caja["orderSendInfoList"]:
            d = esperado.setdefault(rr["parentOrderSn"], {})
            d[rr["orderSn"]] = d.get(rr["orderSn"], 0) + int(rr["quantity"])
    s = _Sesion(4 + 2 * len(ll["ventas"]), 90)
    for po in ll["ventas"]:
        try:
            det = await s.llamar("bg.order.detail.v2.get", {"parentOrderSn": po})
        except Exception as exc:  # noqa: BLE001
            motivos.append(f"{po}: no se pudo releer el detalle ({_texto_error(exc)[:120]})")
            continue
        v = leer_venta(po, det or {})
        motivos.extend(f"{po}: {b}" for b in v["bloqueos"])
        pedido = {x["orderSn"]: int(x["cantidad"]) for x in v["renglones"]}
        if pedido != esperado.get(po, {}):
            motivos.append(f"{po}: lo pedido en Temu cambió desde la aprobación")
    try:
        hallados = await _paquetes_sin_enviar(s, list(ll["ventas"]))
        for po, cosas in hallados.items():
            if cosas:
                motivos.append(f"{po}: unshipped.package.get ya muestra {len(cosas)} paquete(s)")
    except Exception as exc:  # noqa: BLE001
        motivos.append(f"no se pudo releer unshipped.package.get ({_texto_error(exc)[:120]})")
    return motivos


async def _esperar_resultado(psns: list[str]) -> tuple[str, dict[str, dict[str, Any]]]:
    """El resultado ASÍNCRONO de la compra (guía 37): 'comprada' (todas en 1),
    'fallida' (alguna en 2: hay que rehacerla con shipment.update, a mano) o
    'pendiente' (siguen en 0 tras la espera: se concilia después)."""
    from services import temu
    filas: dict[str, dict[str, Any]] = {}
    for espera in _ESPERAS_RESULTADO:
        if espera:
            await asyncio.sleep(espera)
        try:
            rr = await temu.llamar("bg.logistics.shipment.result.get",
                                   {"packageSnList": list(psns)}, timeout=30)
        except Exception as exc:  # noqa: BLE001
            log.warning("compra de guía Temu: result.get falló (%s); se reintenta",
                        _texto_error(exc)[:120])
            continue
        filas.update({str(p.get("packageSn")): p for p in _lista(rr, "packageInfoResultList")
                      if p.get("packageSn")})
        st = [_entero((filas.get(p) or {}).get("shippingLabelStatus")) for p in psns]
        if all(x in (1, 2) for x in st):
            break
    st = [_entero((filas.get(p) or {}).get("shippingLabelStatus")) for p in psns]
    if any(x == 2 for x in st):
        return "fallida", filas
    if st and all(x == 1 for x in st):
        return "comprada", filas
    return "pendiente", filas


def _reparto_real(ll: dict[str, Any], filas: dict[str, dict[str, Any]],
                  mapa_inv: dict[str, int]) -> dict[str, list[dict[str, Any]]]:
    """{PO: [{packageSn, warehouseId, almacen_id, orderSn, sku, quantity}]} de lo
    que Temu dice que compró. PURA."""
    sku_de = {e["orderSn"]: e["sku"] for p in ll["paquetes"] for e in p["renglones"]}
    po_de = {e["orderSn"]: e["parentOrderSn"] for p in ll["paquetes"] for e in p["renglones"]}
    salida: dict[str, list[dict[str, Any]]] = {}
    for psn, f in filas.items():
        wh = str(f.get("warehouseId") or "")
        for o in _lista(f, "orderSendInfoList"):
            osn = str(o.get("orderSn") or "")
            po = str(o.get("parentOrderSn") or po_de.get(osn) or "")
            if not po:
                continue
            salida.setdefault(po, []).append({
                "packageSn": psn, "warehouseId": wh, "almacen_id": mapa_inv.get(wh),
                "orderSn": osn, "sku": sku_de.get(osn), "quantity": _entero(o.get("quantity")) or 0})
    return salida


async def comprar(parent_order_sn: str, huella_aprobada: str, emitida: int | str | None,
                  aprobado_por: str, *,
                  medidas: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Compra las guías del GRUPO que contiene `parent_order_sn` —todas sus
    llamadas, en secuencia y con la misma fecha—, tal como se aprobó en la
    vista previa (misma huella de aprobación, misma `emitida`, mismas
    `medidas`).

    ⚠️ APAGADA: con `TEMU_COMPRA_GUIAS_ENABLED=false` sale antes de tocar nada
    (ni Temu, ni Odoo, ni kubera). Encenderla es decisión de Brandon (regla 3).
    `aprobado_por` lo debe poner el endpoint desde la SESIÓN (no hay endpoint
    todavía), nunca texto libre del cliente.

    El camino: aprobación vigente → re-plan en vivo con todas las guardas →
    misma huella → RECLAMO durable de todo el grupo (sin fila no hay compra) →
    por llamada: revisión final, `shipment.create`, un packageSn por caja,
    espera del resultado asíncrono → bitácora. Ver el encabezado.

    No lanza, salvo la CANCELACIÓN de la tarea (CancelledError), que se deja
    subir DESPUÉS de anotar la orden como 'desconocido'.
    """
    sn = str(parent_order_sn or "").strip()
    base: dict[str, Any] = {"ok": False, "compro": False, "parent_order_sn": sn}
    try:
        if not compra_habilitada():
            return {**base, "accion": "apagado",
                    "motivo": "TEMU_COMPRA_GUIAS_ENABLED está apagada: no se llamó a nadie"}
        h = str(huella_aprobada or "").strip()
        quien = str(aprobado_por or "").strip()
        if not sn or not h or not quien or emitida in (None, ""):
            return {**base, "accion": "sin_aprobacion",
                    "motivo": "falta el PO, la huella aprobada, cuándo se emitió o quién aprobó"}
        try:
            emitida_i = int(emitida)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return {**base, "accion": "sin_aprobacion", "motivo": "`emitida` no es un número"}
        edad = time.time() - emitida_i
        if edad < -60 or edad > _aprobacion_min() * 60:
            return {**base, "accion": "aprobacion_vencida",
                    "motivo": (f"la aprobación es de hace {edad / 60:.0f} min y vale "
                               f"{_aprobacion_min()}: vuelve a revisar la vista previa")}
        if sn in _DESCONOCIDAS:
            return {**base, "accion": "desconocido_previo",
                    "motivo": ("una compra anterior de esta orden quedó sin respuesta: concilia "
                               "en Temu antes de volver a intentar")}
        if _COMPRA_LOCK.locked():
            return {**base, "accion": "en_curso",
                    "motivo": "ya hay una compra de guías en curso: una a la vez"}
        async with _COMPRA_LOCK:
            plan = await plan_guias(solo=sn, usar_cache=False, emitida=emitida_i,
                                    medidas=medidas)
            if not plan.get("ok"):
                return {**base, "accion": "sin_plan", "motivo": plan.get("error") or "sin plan"}
            grupo = next((g for g in plan.get("grupos") or [] if sn in g["ventas"]), None)
            if not grupo:
                return {**base, "accion": "sin_plan",
                        "motivo": plan.get("error") or "la orden no está en el plan"}
            for po in grupo["ventas"]:
                if po in _DESCONOCIDAS:
                    return {**base, "accion": "desconocido_previo",
                            "motivo": f"{po} tiene una compra sin respuesta: concilia antes"}
            if not grupo["comprable"] or not grupo.get("aprobacion"):
                return {**base, "accion": "no_comprable",
                        "motivos": list(grupo["motivos"]) + [
                            m for ll in grupo["llamadas"] for m in ll["motivos"]]}
            aprob = grupo["aprobacion"]
            if aprob["huella"] != h:
                return {**base, "accion": "plan_cambio",
                        "motivo": ("el plan cambió desde que se aprobó (stock, cotización, "
                                   "medidas o día): vuelve a revisar la vista previa"),
                        "huella_nueva": aprob["huella"]}
            reclamo = uuid.uuid4().hex
            filas = []
            for ll in grupo["llamadas"]:
                for po in ll["ventas"]:
                    reparto = [{"orderSn": e["orderSn"], "sku": e["sku"],
                                "quantity": int(e["quantity"]), "warehouse_id": p["warehouse_id"],
                                "almacen_id": p["almacen_id"]}
                               for p in ll["paquetes"] for e in p["renglones"]
                               if e["parentOrderSn"] == po]
                    filas.append({"po": po, "reclamo": reclamo, "grupo": list(grupo["a_comprar"]),
                                  "huella": h, "quien": quien[:120],
                                  "send_type": int(ll["send_type"]), "payload": ll["payload"],
                                  "reparto": reparto, "fecha": aprob["fecha_envio"],
                                  "horas": int(aprob["horas"])})
            try:
                ocupados = await asyncio.to_thread(_reclamar, filas)
            except Exception as exc:  # noqa: BLE001
                return {**base, "accion": "sin_bitacora", "motivo": _motivo_bitacora(exc)}
            if ocupados:
                return {**base, "accion": "reclamada",
                        "motivo": (f"{', '.join(ocupados)} ya tiene(n) una compra abierta en la "
                                   "bitácora (otro proceso o un intento anterior): no se compra")}
            log.warning("COMPRA de guías Temu: grupo %s (%s llamada(s)), aprobada por %s, "
                        "reclamo %s", ", ".join(grupo["a_comprar"]), len(grupo["llamadas"]),
                        quien, reclamo)
            return await _comprar_grupo(grupo, reclamo, base, mapa_almacenes())
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("comprar(%s) falló", sn)
        return {**base, "accion": "error", "motivo": str(exc)[:300]}


async def _comprar_grupo(grupo: dict[str, Any], reclamo: str, base: dict[str, Any],
                         mapa: dict[int, str]) -> dict[str, Any]:
    """Las llamadas del grupo, en secuencia, con la bitácora ya reclamada."""
    from services import temu

    mapa_inv = {wh: wid for wid, wh in mapa.items()}
    aprob = grupo["aprobacion"]
    llamadas = grupo["llamadas"]
    detalle: list[dict[str, Any]] = []
    compradas = 0
    detenida: str | None = None
    for i, ll in enumerate(llamadas):
        pos_ll = list(ll["ventas"])
        resto = [po for x in llamadas[i + 1:] for po in x["ventas"]]
        no_salio = {po: {"estado": "no_enviada",
                         "motivo": "no salió: se detuvo una llamada anterior del grupo"}
                    for po in resto}
        payload = ll["payload"]

        # 1 · La última revisión.
        motivos = await _verificar_antes(ll, aprob)
        if motivos:
            await _anotar(reclamo, {**{po: {"estado": "no_enviada",
                                           "motivo": "revisión final: " + "; ".join(motivos)}
                                       for po in pos_ll}, **no_salio})
            detalle.append({"ventas": pos_ll, "estado": "no_enviada", "motivos": motivos})
            detenida = "detenida"
            break

        # 2 · La compra. CUALQUIER excepción —también la cancelación— se anota
        #     antes de hacer nada más.
        log.warning("COMPRA de guía Temu: %s · sendType %s · %s caja(s) · %s h · llamada %s de %s",
                    ", ".join(pos_ll), payload["sendType"], len(payload["sendRequestList"]),
                    payload["shipLaterLimitTime"], i + 1, len(llamadas))
        try:
            res = await temu.llamar("bg.logistics.shipment.create", payload, timeout=60)
        except BaseException as exc:  # noqa: BLE001
            cod = _codigo(exc) if isinstance(exc, Exception) else None
            clase = clasificar_error(exc)
            if clase != "rechazada":
                _DESCONOCIDAS.update(pos_ll)
            txt = _texto_error(exc) if isinstance(exc, Exception) else type(exc).__name__
            await _anotar(reclamo, {**{po: {"estado": clase, "codigo": cod, "motivo": txt}
                                       for po in pos_ll}, **no_salio}, blindado=True)
            log.error("COMPRA de guía Temu %s: %s (%s)", ", ".join(pos_ll), clase,
                      cod or type(exc).__name__)
            if not isinstance(exc, Exception):
                raise
            detalle.append({"ventas": pos_ll, "estado": clase, "codigo": cod, "motivo": txt})
            detenida = clase
            break

        # 3 · Un packageSn por caja, o no se sabe qué compró.
        psns = [str(x) for x in ((res or {}).get("packageSnList") or []) if str(x or "").strip()]
        eco = str((res or {}).get("shipLaterLimitTime") or "").strip()
        avisos = (res or {}).get("warningMessage")
        avisos = [str(a) for a in avisos] if isinstance(avisos, list) else (
            [str(avisos)] if avisos else [])
        if len(psns) != len(payload["sendRequestList"]):
            _DESCONOCIDAS.update(pos_ll)
            motivo = (f"Temu devolvió {len(psns)} packageSn para {len(payload['sendRequestList'])} "
                      "caja(s): no se sabe qué compró")
            await _anotar(reclamo, {**{po: {"estado": "desconocido", "package_sn": psns,
                                           "motivo": motivo} for po in pos_ll}, **no_salio})
            detalle.append({"ventas": pos_ll, "estado": "desconocido", "package_sn": psns,
                            "motivo": motivo})
            compradas += 1 if psns else 0
            detenida = "desconocido"
            break
        await _anotar(reclamo, {po: {"estado": "pendiente", "package_sn": psns} for po in pos_ll})
        compradas += 1

        # 4 · El resultado asíncrono.
        estado, filas = await _esperar_resultado(psns)
        real = _reparto_real(ll, filas, mapa_inv)
        falla = "; ".join(str(f.get("failReasonText") or "") for f in filas.values()
                          if _entero(f.get("shippingLabelStatus")) == 2).strip("; ") or None
        await _anotar(reclamo, {po: {"estado": estado, "reparto_real": real.get(po) or None,
                                     "motivo": falla} for po in pos_ll})
        eco_mal = bool(eco) and eco != str(payload["shipLaterLimitTime"])
        if eco_mal:
            log.error("COMPRA de guía Temu %s: se mandó shipLaterLimitTime=%s y Temu contestó "
                      "%s — la fecha de envío NO es la aprobada; revísala en el seller center",
                      ", ".join(pos_ll), payload["shipLaterLimitTime"], eco)
        detalle.append({"ventas": pos_ll, "estado": estado, "package_sn": psns,
                        "ship_later_limit_time": eco or None, "eco_distinto": eco_mal,
                        "avisos_temu": avisos,
                        "resultado": [{"packageSn": k,
                                       "shippingLabelStatus": v.get("shippingLabelStatus"),
                                       "warehouseId": v.get("warehouseId"),
                                       "trackingNumber": v.get("trackingNumber"),
                                       "failReasonText": v.get("failReasonText")}
                                      for k, v in filas.items()]})
        if estado != "comprada" or eco_mal:
            await _anotar(reclamo, no_salio)
            detenida = "eco_distinto" if (eco_mal and estado == "comprada") else estado
            break

    faltan = len(llamadas) - compradas       # llamadas que ni siquiera compraron
    if detenida is None:
        accion = "comprada"
    elif compradas and faltan:
        accion = "grupo_a_medias"
    else:
        accion = detenida
    motivos_txt = {
        "comprada": None,
        "grupo_a_medias": ("se compró parte del grupo y el resto NO: revisa cada llamada; lo "
                           "que no salió queda libre para comprarse con una nueva aprobación"),
        "detenida": "la revisión final encontró un cambio: no se compró nada",
        "rechazada": "Temu rechazó la compra con un error documentado: no se compró",
        "ya_solicitada": ("Temu dice que ya había una compra (120012013): la orden queda "
                          "bloqueada hasta conciliar"),
        "desconocido": ("no se sabe si Temu compró la guía: NO reintentar; concilia con el "
                        "detalle (packageSnInfo)"),
        "pendiente": ("Temu dio el packageSn pero la etiqueta sigue en aplicación: se concilia "
                      "después (no se vuelve a comprar)"),
        "fallida": "la etiqueta FALLÓ en Temu (estado 2): hay que rehacerla a mano",
        "eco_distinto": ("Temu devolvió otras horas de envío que las mandadas: revisa la "
                         "fecha en el seller center"),
    }
    return {**base, "ok": accion == "comprada", "compro": compradas > 0, "accion": accion,
            "ventas": list(grupo["a_comprar"]), "llamadas": detalle,
            "package_sn": [p for d in detalle for p in d.get("package_sn") or []],
            "huella": aprob["huella"], "reclamo": reclamo, "motivo": motivos_txt.get(accion)}


async def conciliar(parent_order_sn: str, *, liberar: bool = False,
                    quien: str = "") -> dict[str, Any]:
    """
    Concilia la compra ABIERTA de un PO mirando Temu (detalle, unshipped,
    result.get). SÓLO escribe en la bitácora; nunca compra. Ningún endpoint la
    invoca todavía.

    Si Temu muestra la guía → 'comprada' / 'pendiente' / 'fallida' con su
    reparto real. Si no hay rastro y pasaron ≥ 30 min, `liberar=True` con
    `quien` (verificado a mano en el seller center) la deja 'rechazada', y el PO
    vuelve a poder comprarse con una aprobación nueva. No lanza (salvo
    cancelación).
    """
    sn = str(parent_order_sn or "").strip()
    try:
        fila = (await asyncio.to_thread(_reclamos_de, [sn])).get(sn)
        if not fila:
            return {"ok": False, "accion": "sin_registro", "parent_order_sn": sn}
        if fila.get("estado") in ESTADOS_LIBRES or fila.get("estado") == "comprada":
            return {"ok": True, "accion": "nada_que_conciliar", "estado": fila.get("estado"),
                    "parent_order_sn": sn}
        s = _Sesion(10, 90)
        det = await s.llamar("bg.order.detail.v2.get", {"parentOrderSn": sn})
        v = leer_venta(sn, det or {})
        psns = sorted(set(v.get("paquetes") or []) | set(fila.get("package_sn") or []))
        if not psns:
            sin = await _paquetes_sin_enviar(s, [sn])
            psns = sorted({p for p in sin.get(sn, []) if p and p != "?"})
        if psns:
            filas = await _resultados_de(s, psns)
            st = [_entero((filas.get(p) or {}).get("shippingLabelStatus")) for p in psns]
            estado = ("fallida" if any(x == 2 for x in st) else
                      "comprada" if st and all(x == 1 for x in st) else "pendiente")
            mapa_inv = {wh: wid for wid, wh in mapa_almacenes().items()}
            ll = {"paquetes": [{"renglones": [
                {"orderSn": x.get("orderSn"), "sku": x.get("sku"), "parentOrderSn": sn}
                for x in (fila.get("reparto") or []) if isinstance(x, dict)]}]}
            real = _reparto_real(ll, filas, mapa_inv).get(sn)
            await _anotar(fila["reclamo"], {sn: {"estado": estado, "package_sn": psns,
                                                 "reparto_real": real,
                                                 "motivo": "conciliada contra Temu"}})
            return {"ok": True, "accion": "conciliada", "estado": estado, "package_sn": psns,
                    "parent_order_sn": sn}
        actualizado = fila.get("actualizado_at")
        edad_min = ((datetime.now(timezone.utc) - actualizado).total_seconds() / 60
                    if isinstance(actualizado, datetime) and actualizado.tzinfo else 0.0)
        if liberar and quien.strip() and edad_min >= 30:
            await _anotar(fila["reclamo"], {sn: {"estado": "rechazada", "motivo": (
                f"liberada a mano por {quien.strip()[:60]}: Temu no mostró guía en el detalle "
                f"ni en unshipped tras {edad_min:.0f} min")}})
            _DESCONOCIDAS.discard(sn)
            return {"ok": True, "accion": "liberada", "parent_order_sn": sn}
        return {"ok": True, "accion": "sin_rastro", "edad_min": round(edad_min),
                "parent_order_sn": sn,
                "motivo": ("Temu no muestra guía de esta orden. Verifícalo en el seller center "
                           "y, pasados 30 min, libérala con conciliar(…, liberar=True, quien=…)")}
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("conciliar(%s) falló", sn)
        return {"ok": False, "accion": "error", "motivo": str(exc)[:300], "parent_order_sn": sn}
