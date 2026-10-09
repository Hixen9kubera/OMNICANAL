"""
fanout_devoluciones.py — La pestaña «Devoluciones» del fan-out (Operaciones ›
Fan-out › Devoluciones): las cajas de ML que regresan a NUESTRA bodega y si ya
entraron a Odoo (la bandeja), y por SKU, qué recepciones hicieron cada subida de
Odoo que stock_watch copió a Woo (el cuadre).

SOLO LEE. Dos fuentes:
  · kubera: `channel.returns` (+ `return_items`, `return_history` y el título de
    `channel.order_items`) y `ops.fanout_log` (las subidas de Odoo que anotó
    stock_watch y lo que hizo el reparto con cada una).
  · Odoo (XML-RPC, lista blanca de lectura y tiempo límite): las recepciones de
    devolución —partner «DEVOLUCIONES» desde Partner Locations/Vendors, y las del
    botón «Devolver» (Customers → TEXCO/Salida, con `return_id`)—, sus líneas, su
    destino y la NOTA del chatter. Pocas llamadas en bloque y caché de 10 min.

LA LIGA DURA. Bodega no liga la recepción con la venta: escribe A MANO en la nota
de la recepción el «Envío #» de la etiqueta de devolución de ML (11 dígitos:
«48096342888//MERCADO LIBRE»), a veces la guía de la paquetería (FedEx de 12
dígitos, Paquetexpress «CUL01WE0635094») o la orden de ML (16 dígitos, 2000…, que
también suele venir en el «Folio de retiro Full»). Las del botón «Devolver» traen
la venta (`sale_id` = «ML <orden>»). Una devolución de kubera se liga con la
recepción que trae, en este orden: el envío de su devolución, la guía de la
paquetería, su orden o la cadena «Devolver». Las dos últimas solo si en kubera no
hay OTRA devolución viva de la misma orden (si la hay, desempata el producto de la
recepción; si sigue empatada, no se liga). Nunca antes de abrirse la devolución.
Un envío de devolución cancelado o vencido nunca se liga: la caja no viajó, y su
gemela de la misma venta puede ser la que llegó (5566294012 cancelada y
5566301542 entregada, misma orden: IN/01226 es solo de la segunda). Medido en
producción el 8-oct: 36 de las 57 devoluciones a nuestra bodega en 60 días (63 %);
por fecha + producto solo 23 salían únicas y 4 mal. Una segunda pasada acepta una
guía de 12 o más con UN carácter de diferencia (Bodega la tecleó mal: IN/01167 trae
«3832142924750/FOLIO:1016169/FEDEX» y la 5558975718 es la 383142924750), solo si la
recepción no la nombra nadie, trae el mismo SKU y las mismas piezas, y el par es
único: sale como `por='guia_aprox'` y el texto dice qué trae la nota.

Trampas que la liga respeta (todas de producción, 8-oct):
  · Una recepción con OTRO producto: la guía de JUGU-0100-ROJ (5579601302) entró en
    IN/01541 como DEPO-0181-MET → `otro_sku`.
  · Una recepción con varias guías (IN/01319, 40 piezas de retiro con varias guías
    de TEC-0573-MET): varias devoluciones se ligan a la misma.
  · La misma guía en dos recepciones, una a SCRAP y otra a rack (IN/01480 a las
    14:40 e IN/01481 a las 14:43, guía 48075552134): gana la que mandó el SKU a rack
    (la que subió el stock) y el texto nombra la otra con su destino.
  · Bodega también recibe como «DEVOLUCIONES» los RETIROS de FULL: la nota o el folio
    traen dos o más cajas de la bodega de ML («1263041610-8») y ninguna guía. En el
    cuadre van como `retiro_full`, aparte de las devoluciones de clientes (las cajas
    solas no bastan: casi todas las devoluciones de ventas FULL las traen junto a su
    envío de devolución).
  · No hay cuarentena: validar la recepción a rack sube el `free_qty` (y stock_watch
    lo copia a Woo); SCRAP (interna SIN almacén) no sube. «rack» aquí es cualquier
    ubicación vendible (interna CON almacén, como `odoo._vendible`), incluida
    TEXCO/Salida de las del botón «Devolver».
  · Estados congelados: ML/kubera dan «en camino» a cajas que Odoo ya recibió
    (5581207287 → IN/01453): van al grupo «atrasada» («Odoo ya la tiene»).

LA BANDEJA (A). Las devoluciones de ML con destino nuestra bodega que se ABRIERON o
LLEGARON en el periodo, una fila por (devolución, SKU), en grupos (las de FULL y las
que ML no dice a dónde van se cuentan aparte, solo por apertura):
  buscar    llegó según ML y no hay recepción con su guía → «Buscar en Bodega».
  otro      la recepción con su guía recibió otro SKU.
  atrasada  Odoo ya la tiene y ML todavía no la da por entregada.
  odoo      recibida a rack: subió el stock.
  scrap     recibida a SCRAP: no sube el stock.
  camino    en camino o por enviar.
  noregresa vencida, cancelada o no entregada.
Sin liga dura, una que llegó lleva la liga PROBABLE por fecha de v0.628.0
(`fanout_vivo.ligar_devoluciones`) contra las subidas de Odoo que NO explican ya
recepciones con guía (las de otras cajas identificadas no se le ofrecen).

Odoo se lee desde la apertura más vieja de las filas que se van a mostrar (menos un
día), no desde el inicio del periodo: una fila entra por su LLEGADA aunque se haya
abierto antes, y Odoo la pudo recibir días antes de que ML la diera por entregada
(las «atrasadas»). Una sola caché para todos los periodos: la de 60 días sirve a la
de 14.

ODOO CAÍDO O LENTO (decisión de la pestaña). Si Odoo no contesta, se sirve la última
lectura buena de hasta 2 h (`odoo_ok` sigue true, `odoo_error` dice por qué y
`odoo_leido` de cuándo es), y por `ODOO_REINTENTO` no se vuelve a Odoo —ni la bandeja
ni el cuadre—: los hilos del executor por defecto de asyncio son de TODO el backend.
Una relectura a la vez; quien llega mientras otra corre espera a lo más
`ODOO_ESPERA` y se lleva la última buena, y cada lectura tiene un plazo total que se
descuenta llamada por llamada. Sin ninguna lectura: `odoo_ok=false`, ninguna fila tiene liga
dura y los grupos salen SOLO del estado de ML: lo que llegó va a «buscar» (es lo
único accionable y así se ve primero), pero con «Sin verificar en Odoo» en su texto
y la acción «Verificar cuando Odoo conteste»; «otro», «atrasada», «odoo» y «scrap»
quedan en 0 y el resumen no cuenta nada de Odoo (`en_odoo_guia`, `subieron`,
`scrap`, `otro_sku`, `atrasada`, `solo_odoo` = 0). La página pinta el aviso ámbar
con `odoo_ok=false`: «buscar» ahí significa «llegó y no se pudo verificar», no «no
está en Odoo». La liga probable sí se calcula (solo usa kubera), sin descontar
subidas: no hay recepciones con qué hacerlo.

EL CUADRE (B). Por SKU: cada subida de Odoo del periodo (renglón `odoo_delta` de
`ops.fanout_log` cuya foto SUBE, con la regla de reintentos de
`fanout_vivo._subidas_odoo`) con las recepciones que la hicieron —de devolución,
compra, traslado, ajuste o retiro de FULL, leídas de Odoo para ese SKU— y la
devolución de kubera que trae cada una por su guía. Cada recepción A RACK va a la
PRIMERA subida cuya hora es ≥ su `date_done` y a no más de `_SUBIDA_MAX_H`
(stock_watch pasa cada 20 min: medido de 3 a 19 min en TEC-0519-NAR-GRI); si no hay
ninguna, va a `sin_subida` con `espera=true` si se validó después de la última pasada
de stock_watch (aún no la copia) y si no, false (una salida en la misma pasada pudo
compensarla). Las de SCRAP no suben el stock y no se cuelgan de ninguna subida: van
en `a_scrap`. `reparto_txt` resume lo que hizo el fan-out con ese cambio (las filas
del mismo pase en la bitácora).

Las funciones puras (sin lecturas) van separadas para probarlas:
`tokens`, `guias`, `recepciones_de`, `tokens_devolucion`, `ligar_duro`,
`asignar_subidas`, `texto_reparto`, `armar_bandeja`, `armar_detalle`.
"""
from __future__ import annotations

import html
import logging
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from services import fanout_vivo as V
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.fanout_devoluciones")

ZONA = "America/Mexico_City"
_CDMX = timezone(timedelta(hours=-6))       # sin horario de verano desde 2022 (como fanout_bodegas)

ODOO_TTL = 600.0            # la lectura global de recepciones: 10 min
ODOO_REINTENTO = 60.0       # tras una falla, no se vuelve a Odoo antes
ODOO_VIEJA_MAX = 7200.0     # la última lectura buena se sirve (con aviso) hasta 2 h
ODOO_TIMEOUT = 45.0         # segundos por llamada XML-RPC (la bandeja)
ODOO_TIMEOUT_SKU = 20.0     # el cuadre de un SKU: alguien está esperando
ODOO_PLAZO = 90.0           # la lectura global entera (~15 llamadas; en frío tardó 12 s el 8-oct)
ODOO_PLAZO_SKU = 40.0       # el cuadre de un SKU entero (~8 llamadas; 4 s en frío)
ODOO_ESPERA = 15.0          # quien llega mientras otra relectura corre, espera a lo más esto
_SKU_TTL = 120.0            # el cuadre de un SKU en caché
_SKU_MAX = 64
_KUBERA_TTL = 30.0          # las devoluciones de kubera (la bandeja y el cuadre las comparten)
_CAMPOS_TTL = 86400.0       # los campos de stock.picking: cambian nunca
_LOTE = 200                 # ids por llamada a Odoo
METODOS_ODOO = frozenset({"search_read", "read", "fields_get"})

# Devoluciones de kubera abiertas hasta estos días ANTES del periodo: sus guías
# cuentan para saber si una recepción está en kubera (cobertura y cuadre).
_KUBERA_EXTRA_D = 90
# El cuadre busca los pendientes del SKU al menos en esta ventana (una caja que
# llegó hace 20 días y no entró sigue pendiente aunque el cuadre sea de 14).
_PENDIENTES_MIN_D = 60
# Una recepción va a la primera subida de Odoo que llega a lo más estas horas
# después (stock_watch pasa cada 20 min).
_SUBIDA_MAX_H = 3
# El reparto de una subida: las filas del fan-out hasta estos minutos después.
_REPARTO_MAX_MIN = 10
# Una recepción no puede ser de una devolución que se abrió después (con holgura
# por los relojes de ML y de Odoo).
_ANTES_DE_ABRIR_MIN = 60
# Bodega a veces recibe a rack y CORRIGE al momento mandando la pieza a SCRAP
# (MUE-0133-MET, IN/01318: Customers → TEXCO/Salida a las 14:08 y, tras PACK/PICK de
# regreso, FERRAFORME → SCRAP a las 14:11). Un traslado de una vendible a una interna
# sin almacén del mismo producto hasta estos minutos después de la recepción cuenta
# como «pasó a SCRAP»: el stock no subió. Medido: 93 traslados así en 60 días en toda
# la empresa, así que la ventana corta casi no se cruza con otro.
_PASO_A_SCRAP_MIN = 30

_PARTNER_DEVOLUCIONES = "DEVOLUCIONES"
# Campos de stock.picking que se piden si existen (los `ifull_`/`x_studio_` son
# del Odoo de producción; otro Odoo puede no tenerlos y `search_read` truena).
_CAMPOS_PICKING = ["name", "date_done", "partner_id", "origin", "note", "return_id", "sale_id",
                   "purchase_id", "location_id", "location_dest_id", "carrier_tracking_ref",
                   "ifull_return_withdrawal_folio", "x_studio_referencia_del_cliente"]
# Los campos donde Bodega (o Odoo) deja guías y órdenes, además de la nota del chatter.
_CAMPOS_TEXTO = ["origin", "note", "carrier_tracking_ref", "ifull_return_withdrawal_folio",
                 "x_studio_referencia_del_cliente"]

GRUPOS = ["buscar", "otro", "atrasada", "odoo", "scrap", "camino", "noregresa"]
# Un envío de devolución cancelado o vencido no viajó: nunca se liga.
_NO_LIGAN = ("cancelled", "expired")
_ESTADO_ML = {"label_generated": "por_enviar", "pending": "por_enviar", "ready_to_ship": "por_enviar",
              "opened": "por_enviar", "shipped": "en_camino", "delivered": "entregada",
              "expired": "vencida", "cancelled": "cancelada", "not_delivered": "no_entregada",
              "failed": "no_entregada"}
_ESTADO_ML_TXT = {"por_enviar": "por enviar", "en_camino": "en camino", "entregada": "entregada",
                  "vencida": "vencida", "cancelada": "cancelada", "no_entregada": "no entregada"}
_NO_REGRESAN = ("vencida", "cancelada", "no_entregada")
_SEMANA_CORTA = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
_CANAL_CORTO = {"mercado_libre": "ML", "amazon": "Amazon", "tiktok": "TikTok", "temu": "Temu",
                "walmart": "Walmart"}
# Prioridad de la liga dura (menor gana) y cómo se llama en la respuesta.
_RANGO = {"envio": 0, "guia": 1, "orden": 2, "devolver": 3, "guia_aprox": 4}
_POR = {"envio": "guia", "guia": "guia", "orden": "orden", "devolver": "devolver", "guia_aprox": "guia_aprox"}
_POR_TXT = {"guia": "por guía", "orden": "por orden", "devolver": "por «Devolver»",
            "guia_aprox": "por guía con un carácter de diferencia"}
# La guía con un carácter de diferencia: solo de este largo para arriba (FedEx 12,
# Estafeta 22…). Los envíos de ML (11) son seriales: el vecino de un dígito es OTRO envío.
_APROX_MIN = 12

# ── Guías y tokens ───────────────────────────────────────────────────────────
# Un token es una corrida de letras y dígitos de 8 o más (la nota es texto libre:
# «#2000017718464180\t47729212750/FOLIO: 01015903/ML»). Comparar corridas enteras
# es lo mismo que buscar el valor con frontera alfanumérica a los lados.
_RE_TAGS = re.compile(r"<[^>]+>")
_RE_TOKEN = re.compile(r"[0-9A-Z]{8,}")
# Las guías que se reconocen, en el orden en que se prefieren para mostrar.
_TIPOS_GUIA: list[tuple[str, str, re.Pattern]] = [
    # «Envío #» de la etiqueta de ML: 11 dígitos. La serie va en 47…–48… (47729212750 el
    # 10-ago, 48130101482 el 29-sep: ~8 M al día) y pasa a 5… hacia mayo de 2027, así
    # que el primero no se ancla en 4.
    ("ml", "", re.compile(r"[4-9]\d{10}")),
    ("fedex", "FedEx", re.compile(r"\d{12}")),
    ("paquetexpress", "Paquetexpress", re.compile(r"[A-Z]{3}\d{2}[A-Z]{2}\d{7,13}")),
    ("estafeta", "Estafeta", re.compile(r"\d{12}[0-9A-Z]{10}")),
]
_NOMBRE_GUIA = {k: n for k, n, _r in _TIPOS_GUIA} | {"dhl": "DHL"}
_ORDEN_GUIA = {k: i for i, k in enumerate(_NOMBRE_GUIA)}
_ORDEN_ML = re.compile(r"2000\d{12}")
_ORDEN_VENTA = re.compile(r"^ML\s+(\d{10,})")
# El número pegado a la paquetería que la nota nombra, antes o después
# («1397938851//ESTAFETA», «4313175926/FOLIO:01015464/DHL», «PAQUETEXPRESS //
# 1902252493597»): Estafeta, DHL y Paquetexpress también usan guías de 9 a 13 dígitos
# sin forma propia. El folio de Bodega («FOLIO: 01015214») nunca es la guía.
_PAQUETERIAS = {"FEDEX": "fedex", "ESTAFETA": "estafeta", "PAQUETEXPRESS": "paquetexpress", "DHL": "dhl"}
_RE_PAQ_NOMBRE = re.compile(r"(?<![A-Z])(FEDEX|ESTAFETA|PAQUETEXPRESS|DHL)(?![A-Z])")
_RE_PAQ = re.compile(r"(?<![A-Z])(FEDEX|ESTAFETA|PAQUETEXPRESS|DHL|MERCADO ?LIBRE)(?![A-Z])")
_RE_FOLIO = re.compile(r"FOLIO\s*:?\s*\d+")
_RE_ANTES = re.compile(r"([0-9A-Z]+)[^0-9A-Z]{0,4}$")
_RE_DESPUES = re.compile(r"[^0-9A-Z]{0,4}(\d{8,22})(?![0-9A-Z])")
# Las cajas de la bodega de ML («1263041610-8»). Medido en las 664 recepciones
# «DEVOLUCIONES» hasta el 8-oct: 345 notas las traen, casi siempre en pares caja +
# envío de devolución de ML («842103303-3 47905265109 …»: devoluciones de ventas FULL
# que regresan a nuestra bodega). Solo las que traen DOS o más cajas y NINGUNA guía
# son retiros de FULL (14: «Ingreso de 221 artículos en buen estado // 1263041610-8
# …»); con una caja y sin guía es una devolución con el envío mal escrito
# («1196916205-9 4761982756/FOLIO…», de 10 dígitos).
_RE_CAJA_FULL = re.compile(r"(?<![0-9])(\d{9,11})-\d{1,3}(?![0-9])")


def texto_plano(v: Any) -> str:
    """El cuerpo HTML de un mensaje (o un campo de Odoo) como texto de una línea."""
    if not v or v is True:
        return ""
    t = html.unescape(_RE_TAGS.sub(" ", str(v))).replace("\xa0", " ")
    return " ".join(t.split())


def tokens(texto: str) -> list[str]:
    """Las corridas alfanuméricas de 8+ en MAYÚSCULAS, únicas y en orden de aparición."""
    vistos: dict[str, None] = {}
    for t in _RE_TOKEN.findall((texto or "").upper()):
        vistos.setdefault(t, None)
    return list(vistos)


def guias(toks: list[str], texto: str = "") -> list[tuple[str, str]]:
    """[(tipo, valor)] de los tokens que son una guía (no las órdenes de ML). Con el
    texto de la nota también cuenta el número pegado a la paquetería que nombra. Un
    número de 12 que empieza con 52 puede ser un teléfono con lada («TEL
    525512345678»): es FedEx solo si la nota dice FEDEX. Una caja de FULL
    («80144739100-2») no es guía aunque tenga 11 dígitos."""
    up = (texto or "").upper()
    cajas = set(_RE_CAJA_FULL.findall(up))
    vistas: dict[str, str] = {}
    for t in toks:
        if _ORDEN_ML.fullmatch(t) or t in cajas:
            continue
        tipo = next((k for k, _n, rx in _TIPOS_GUIA if rx.fullmatch(t)), None)
        if tipo == "fedex" and t.startswith("52") and "FEDEX" not in up:
            continue
        if tipo:
            vistas.setdefault(t, tipo)
    sin_folio = _RE_FOLIO.sub(" ", up)
    for m in _RE_PAQ_NOMBRE.finditer(sin_folio):
        # El número de ANTES es la guía; si lo de antes es otra cosa con forma de guía
        # («CUL01WE0583887 // PAQUETEXPRESS 795758303»), lo de después no se toma.
        antes = _RE_ANTES.search(sin_folio[:m.start()])
        t = antes.group(1) if antes and len(antes.group(1)) >= 8 else ""
        if t and not _ORDEN_ML.fullmatch(t):
            if t.isdigit() and t not in cajas:
                vistas.setdefault(t, _PAQUETERIAS[m.group(1)])
            continue
        despues = _RE_DESPUES.match(sin_folio[m.end():])
        if despues and not _ORDEN_ML.fullmatch(despues.group(1)) and despues.group(1) not in cajas:
            vistas.setdefault(despues.group(1), _PAQUETERIAS[m.group(1)])
    return [(tipo, valor) for valor, tipo in vistas.items()]


def es_retiro_full(texto: str, gs: list[tuple[str, str]]) -> bool:
    """¿Recepción de un retiro de FULL? Dos o más cajas de la bodega de ML y NINGUNA guía."""
    return not gs and len({m.group(0) for m in _RE_CAJA_FULL.finditer((texto or "").upper())}) >= 2


def guia_no_reconocida(texto: str, gs: list[tuple[str, str]]) -> bool:
    """¿La nota nombra una paquetería (o ML) y trae un número largo, pero ninguna guía
    se reconoce? (IN/01167: «3832142924750/FOLIO:1016169/FEDEX», con un dígito de
    más). Entonces se dice «guía no reconocida», no «sin guía»."""
    if gs:
        return False
    up = (texto or "").upper()
    cajas = set(_RE_CAJA_FULL.findall(up))
    return bool(_RE_PAQ.search(up)) and any(t.isdigit() and len(t) >= 9 and not _ORDEN_ML.fullmatch(t)
                                            and t not in cajas for t in tokens(up))


def guia_txt(gs: list[tuple[str, str]], primero: str | None = None) -> str | None:
    """La guía para mostrar: `primero` si está (la de la devolución ligada), si no la
    de ML, luego la de paquetería con su nombre; si trae más, cuántas («(+2)»)."""
    if not gs:
        return None
    tipo, valor = next((g for g in gs if g[1] == primero), None) or sorted(gs, key=lambda g: _ORDEN_GUIA[g[0]])[0]
    nombre = _NOMBRE_GUIA[tipo]
    txt = f"{valor} ({nombre})" if nombre else valor
    return txt + (f" (+{len(gs) - 1})" if len(gs) > 1 else "")


# ── Utilidades ──────────────────────────────────────────────────────────────

def _id_de(campo: Any) -> int | None:
    return campo[0] if isinstance(campo, (list, tuple)) and campo else None


def _nombre_de(campo: Any) -> str:
    return campo[1] if isinstance(campo, (list, tuple)) and len(campo) > 1 else ""


def _ahora() -> str:
    return datetime.now(_CDMX).strftime("%Y-%m-%d %H:%M:%S")


def _local(utc: Any) -> str | None:
    """'2026-09-29 20:30:38' de Odoo (UTC sin zona) → '2026-09-29 14:30:38' de CDMX."""
    if not utc or utc is True:
        return None
    try:
        t = datetime.strptime(str(utc)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return t.astimezone(_CDMX).strftime("%Y-%m-%d %H:%M:%S")


def _utc(local: str) -> str:
    """Hora local de CDMX → UTC sin zona, como la pide un dominio de Odoo."""
    t = datetime.strptime(local[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_CDMX)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _menos(local: str, dias: float = 0, horas: float = 0) -> str:
    t = datetime.strptime(local[:19], "%Y-%m-%d %H:%M:%S")
    return (t - timedelta(days=dias, hours=horas)).strftime("%Y-%m-%d %H:%M:%S")


def _corto(picking: str) -> str:
    """'TEXCO/IN/01478' → 'IN/01478' (así lo dice Bodega). Las de otro almacén, completas."""
    return picking[6:] if picking.startswith("TEXCO/") else picking


def _dias_entre(desde: str | None, hasta: str) -> int | None:
    a, b = V._hora_dt(desde), V._hora_dt(hasta)
    return (b.date() - a.date()).days if a and b else None


def _cuando(local: str | None, hoy: str) -> str:
    """'2026-10-02' (solo el día) → 'el vie 2-oct'; con hora → 'el 28-sep 13:13' u 'hoy 13:13'."""
    if not local:
        return "sin fecha"
    if len(local) == 10:
        t = V._hora_dt(local)
        return f"el {_SEMANA_CORTA[t.weekday()]} {V._dia(local)}" if t else local
    f = V._fecha(local, hoy)
    return f if f.startswith("hoy") else f"el {f}"


def _nombre_cuenta(cuenta: str) -> str:
    return V._nombre_destino("mercado_libre", (cuenta or "").upper())


def _escapar_like(s: str) -> str:
    """Para `=ilike` (igual sin distinguir mayúsculas): `%` y `_` son literales."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ── Odoo: lectura (BLOQUEA; SOLO LECTURA) ───────────────────────────────────

def _kw_solo_lectura(modelo: str, metodo: str, args: list[Any],
                     kwargs: dict[str, Any] | None = None, *, timeout: float = ODOO_TIMEOUT) -> Any:
    """La ÚNICA puerta a Odoo de este módulo: lista blanca de métodos de lectura."""
    if metodo not in METODOS_ODOO:
        raise PermissionError(f"fanout_devoluciones sólo lee Odoo: «{metodo}» no está permitido")
    from services import odoo
    return odoo._kw_flujo(modelo, metodo, args, kwargs or {}, timeout=timeout)


def _kw_con_plazo(plazo: float, por_llamada: float) -> Callable[..., Any]:
    """`_kw_solo_lectura` con un plazo TOTAL para toda la lectura: cada llamada lleva
    lo que queda (a lo más `por_llamada`) y, agotado, ya no se llama. Sin él, un Odoo
    lento pero vivo tendría el hilo hasta 15 llamadas × 45 s."""
    fin = time.monotonic() + plazo

    def kw(modelo: str, metodo: str, args: list[Any], kwargs: dict[str, Any] | None = None) -> Any:
        queda = fin - time.monotonic()
        if queda < 1:
            raise TimeoutError(f"la lectura de Odoo pasó de {plazo:.0f} s")
        return _kw_solo_lectura(modelo, metodo, args, kwargs, timeout=min(por_llamada, queda))
    return kw


_campos_cache: dict[str, Any] = {"t": 0.0, "v": None}


def _campos_picking(kw: Callable[..., Any]) -> list[str]:
    """Los de `_CAMPOS_PICKING` que este Odoo tiene. Caché de 24 h."""
    if _campos_cache["v"] and time.monotonic() - _campos_cache["t"] < _CAMPOS_TTL:
        return _campos_cache["v"]
    hay = kw("stock.picking", "fields_get", [_CAMPOS_PICKING], {"attributes": ["type"]}) or {}
    v = [c for c in _CAMPOS_PICKING if c in hay]
    _campos_cache.update(t=time.monotonic(), v=v)
    return v


def _en_lotes(kw: Callable[..., Any], modelo: str, ids: list[int], dominio: Callable[[list[int]], list],
              campos: list[str]) -> list[dict]:
    salida: list[dict] = []
    for i in range(0, len(ids), _LOTE):
        salida += kw(modelo, "search_read", [dominio(ids[i:i + _LOTE])], {"fields": campos}) or []
    return salida


def _leer_lado_odoo(kw: Callable[..., Any], picks: list[dict], movs: list[dict]) -> dict[str, Any]:
    """Lo que falta para normalizar: notas del chatter, códigos de producto y ubicaciones."""
    pids = sorted({p["id"] for p in picks})
    msgs = _en_lotes(kw, "mail.message", pids,
                     lambda lote: [["model", "=", "stock.picking"], ["res_id", "in", lote],
                                   ["message_type", "=", "comment"]],
                     ["res_id", "body"]) if pids else []
    prod_ids = sorted({i for m in movs if (i := _id_de(m.get("product_id")))})
    prods: list[dict] = []
    for i in range(0, len(prod_ids), _LOTE):
        prods += kw("product.product", "read", [prod_ids[i:i + _LOTE]], {"fields": ["default_code"]}) or []
    loc_ids = sorted({i for m in movs for c in ("location_id", "location_dest_id") if (i := _id_de(m.get(c)))}
                     | {i for p in picks for c in ("location_id", "location_dest_id") if (i := _id_de(p.get(c)))})
    locs = kw("stock.location", "read", [loc_ids], {"fields": ["usage", "warehouse_id", "complete_name"]}) if loc_ids else []
    return {"msgs": msgs, "prods": prods, "locs": locs or []}


def leer_odoo_crudo(desde_utc: str, kw: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Las recepciones de devolución validadas desde `desde_utc` (UTC), con sus
    líneas, notas, códigos y ubicaciones. LANZA si Odoo falla (quien llama decide)."""
    kw = kw or _kw_solo_lectura
    campos = _campos_picking(kw)
    dominio = [["picking_type_code", "=", "incoming"], ["state", "=", "done"], ["date_done", ">=", desde_utc],
               "|", ["partner_id.name", "=", _PARTNER_DEVOLUCIONES],
               "&", ["return_id", "!=", False], ["location_id.usage", "=", "customer"]]
    picks = kw("stock.picking", "search_read", [dominio], {"fields": campos, "order": "date_done asc, id asc"}) or []
    movs = _en_lotes(kw, "stock.move", sorted(p["id"] for p in picks),
                     lambda lote: [["picking_id", "in", lote], ["state", "=", "done"]],
                     ["picking_id", "product_id", "quantity", "location_id", "location_dest_id"]) if picks else []
    prod_ids = sorted({i for m in movs if (i := _id_de(m.get("product_id")))})
    return {"picks": picks, "movs": movs, "scrap": _leer_a_scrap(kw, prod_ids, desde_utc),
            **_leer_lado_odoo(kw, picks, movs)}


def _leer_a_scrap(kw: Callable[..., Any], prod_ids: list[int], desde_utc: str) -> list[dict]:
    """Los traslados de una ubicación vendible a una interna SIN almacén (SCRAP,
    CUARENTENA) de estos productos desde `desde_utc` (ver `_PASO_A_SCRAP_MIN`)."""
    return _en_lotes(kw, "stock.move", prod_ids,
                     lambda lote: [["product_id", "in", lote], ["state", "=", "done"], ["date", ">=", desde_utc],
                                   ["location_id.usage", "=", "internal"], ["location_id.warehouse_id", "!=", False],
                                   ["location_dest_id.usage", "=", "internal"],
                                   ["location_dest_id.warehouse_id", "=", False]],
                     ["product_id", "quantity", "date"]) if prod_ids else []


def leer_odoo_sku_crudo(sku: str, desde_utc: str, kw: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Las ENTRADAS de un SKU desde `desde_utc`: todo movimiento hecho de fuera de
    bodega a una ubicación interna (recepción, devolución, ajuste; a rack o a SCRAP)
    y de una interna sin almacén (cuarentena, SCRAP) a una vendible. Las ventas y los
    pasos PICK/PACK/OUT no entran. LANZA si Odoo falla."""
    kw = kw or _kw_solo_lectura
    prods = kw("product.product", "search_read", [[["default_code", "=ilike", _escapar_like(sku)]]],
               {"fields": ["default_code", "name"], "context": {"active_test": False}}) or []
    ids = sorted(int(p["id"]) for p in prods)
    if not ids:
        return {"productos": [], "picks": [], "movs": [], "msgs": [], "prods": [], "locs": []}
    dominio = [["product_id", "in", ids], ["state", "=", "done"], ["date", ">=", desde_utc],
               "|", "&", ["location_id.usage", "!=", "internal"], ["location_dest_id.usage", "=", "internal"],
               "&", "&", ["location_id.usage", "=", "internal"], ["location_id.warehouse_id", "=", False],
               ["location_dest_id.warehouse_id", "!=", False]]
    movs = kw("stock.move", "search_read", [dominio],
              {"fields": ["date", "quantity", "location_id", "location_dest_id", "picking_id", "is_inventory",
                          "reference", "product_id"], "order": "date asc, id asc", "limit": 2000}) or []
    pick_ids = sorted({i for m in movs if (i := _id_de(m.get("picking_id")))})
    campos = _campos_picking(kw)
    picks: list[dict] = []
    for i in range(0, len(pick_ids), _LOTE):
        picks += kw("stock.picking", "read", [pick_ids[i:i + _LOTE]], {"fields": campos}) or []
    return {"productos": prods, "picks": picks, "movs": movs, "scrap": _leer_a_scrap(kw, ids, desde_utc),
            **_leer_lado_odoo(kw, picks, movs)}


# ── Odoo: normalización (FUNCIONES PURAS) ────────────────────────────────────

def _vendible(loc: dict | None) -> bool:
    """Interna CON almacén (la regla de `odoo._vendible`): SCRAP y CUARENTENA son
    internas sin almacén y no cuentan en el `free_qty` que copia stock_watch."""
    return bool(loc) and loc.get("usage") == "internal" and bool(_id_de(loc.get("warehouse_id")))


def _indices(crudo: dict[str, Any]) -> tuple[dict, dict, dict]:
    locs = {int(l["id"]): l for l in crudo.get("locs") or []}
    codigos = {int(p["id"]): str(p.get("default_code") or "").strip() for p in crudo.get("prods") or []}
    notas: dict[int, list[str]] = defaultdict(list)
    for m in crudo.get("msgs") or []:
        t = texto_plano(m.get("body"))
        if t:
            notas[int(m["res_id"])].append(t)
    return locs, codigos, notas


def _a_scrap_por_sku(crudo: dict[str, Any], codigos: dict[int, str]) -> dict[str, list[list]]:
    """{SKU: [[hora local, piezas sin asignar]]} de los traslados a SCRAP, del más viejo al más nuevo."""
    salida: dict[str, list[list]] = defaultdict(list)
    for m in sorted(crudo.get("scrap") or [], key=lambda x: str(x.get("date") or "")):
        cod = codigos.get(_id_de(m.get("product_id")) or -1, "").upper()
        hora = _local(m.get("date"))
        if cod and hora:
            salida[cod].append([hora, float(m.get("quantity") or 0)])
    return salida


def _paso_a_scrap(pend: dict[str, list[list]], sku: str, hora: str, piezas: float) -> str | None:
    """¿Lo que entró a rack a esta hora pasó a SCRAP enseguida? Toma (y gasta) el
    primer traslado del SKU dentro de `_PASO_A_SCRAP_MIN` que cubra las piezas."""
    t = V._hora_dt(hora)
    if t is None or piezas <= 0:
        return None
    for s in pend.get((sku or "").upper(), []):
        ts = V._hora_dt(s[0])
        if ts is not None and t <= ts <= t + timedelta(minutes=_PASO_A_SCRAP_MIN) and s[1] >= piezas:
            s[1] -= piezas
            return s[0]
    return None


def _texto_picking(p: dict, notas: list[str]) -> tuple[str, str]:
    """(texto donde se buscan guías y órdenes, la nota de Bodega para mostrar)."""
    campos = [texto_plano(p.get(c)) for c in _CAMPOS_TEXTO]
    nota = " · ".join(notas)
    return " | ".join([*campos, nota]), nota


def _orden_de_venta(p: dict) -> str | None:
    """La orden de ML de una devolución del botón «Devolver» (`sale_id` = «ML <orden>»)."""
    if not _id_de(p.get("return_id")):
        return None
    m = _ORDEN_VENTA.match(_nombre_de(p.get("sale_id")))
    return m.group(1) if m else None


def _tipo_entrada(p: dict | None, mov: dict, u_org: dict | None, retiro_full: bool = False) -> str:
    socio = _nombre_de((p or {}).get("partner_id")).strip().upper()
    uso = (u_org or {}).get("usage") or ""
    if socio == _PARTNER_DEVOLUCIONES or uso == "customer":
        return "retiro_full" if retiro_full else "devolucion"
    if mov.get("is_inventory") or uso == "inventory":
        return "ajuste"
    if uso == "supplier":
        return "compra"
    if uso in ("internal", "transit"):
        return "traslado"
    return "otro"


def recepciones_de(crudo: dict[str, Any]) -> list[dict]:
    """Las recepciones de devolución de la lectura global, una por picking:
    {id, nombre, picking (corto), hora (local), tipo ('devoluciones'|'devolver'),
     lineas [{sku, cantidad, destino}], skus, destino, tokens, guias, guia, no_reconocida,
     retiro_full, orden_venta, nota}.
    `destino` es 'rack' si alguna línea entró a una ubicación vendible, si no 'scrap'."""
    locs, codigos, notas = _indices(crudo)
    lineas: dict[int, list[dict]] = defaultdict(list)
    for m in crudo.get("movs") or []:
        pid = _id_de(m.get("picking_id"))
        if pid is None:
            continue
        dest = locs.get(_id_de(m.get("location_dest_id")) or -1)
        lineas[pid].append({"sku": codigos.get(_id_de(m.get("product_id")) or -1, "")
                            or _nombre_de(m.get("product_id")),
                            "cantidad": float(m.get("quantity") or 0),
                            "destino": "rack" if _vendible(dest) else "scrap"})
    pend = _a_scrap_por_sku(crudo, codigos)
    salida = []
    for p in sorted(crudo.get("picks") or [], key=lambda x: (str(x.get("date_done") or ""), x["id"])):
        texto, nota = _texto_picking(p, notas.get(int(p["id"]), []))
        toks = tokens(texto)
        gs = guias(toks, texto)
        ls = lineas.get(int(p["id"]), [])
        for l in ls:
            l["a_scrap"] = None
            if l["destino"] == "rack":
                l["a_scrap"] = _paso_a_scrap(pend, l["sku"], _local(p.get("date_done")) or "", l["cantidad"])
                if l["a_scrap"]:
                    l["destino"] = "scrap"
        if not ls:
            dest = locs.get(_id_de(p.get("location_dest_id")) or -1)
            destino = "rack" if _vendible(dest) else "scrap"
        else:
            destino = "rack" if any(l["destino"] == "rack" for l in ls) else "scrap"
        salida.append({
            "id": int(p["id"]), "nombre": p.get("name") or "", "picking": _corto(p.get("name") or ""),
            "hora": _local(p.get("date_done")) or "",
            "tipo": "devolver" if _id_de(p.get("return_id")) else "devoluciones",
            "lineas": ls, "skus": {l["sku"].upper() for l in ls if l["sku"]}, "destino": destino,
            "tokens": set(toks), "guias": gs, "guia": guia_txt(gs),
            "no_reconocida": guia_no_reconocida(texto, gs), "retiro_full": es_retiro_full(texto, gs),
            "orden_venta": _orden_de_venta(p), "nota": nota[:300]})
    salida.sort(key=lambda r: (r["hora"], r["id"]))
    return salida


def entradas_sku_de(crudo: dict[str, Any]) -> list[dict]:
    """Las entradas de UN SKU (lectura por SKU), una por (picking, destino):
    {id, clave, nombre, picking, hora, tipo ('devolucion'|'retiro_full'|'compra'|'traslado'|
     'ajuste'|'otro'), destino, piezas, skus, tokens, guias, guia, no_reconocida,
     orden_venta, origen, nota}. `clave` es única aunque el nombre se repita (los ajustes
    sin picking llevan el `reference` de Odoo: «Cantidad de producto actualizada» ×3)."""
    locs, codigos, notas = _indices(crudo)
    picks = {int(p["id"]): p for p in crudo.get("picks") or []}
    grupos: dict[tuple, dict] = {}
    for m in crudo.get("movs") or []:
        pid = _id_de(m.get("picking_id"))
        p = picks.get(pid) if pid else None
        u_org = locs.get(_id_de(m.get("location_id")) or -1)
        destino = "rack" if _vendible(locs.get(_id_de(m.get("location_dest_id")) or -1)) else "scrap"
        clave = (pid or f"m{m.get('id') or m.get('reference')}", destino)
        g = grupos.get(clave)
        if g is None:
            texto, nota = _texto_picking(p, notas.get(pid, [])) if p else ("", "")
            toks = tokens(texto)
            gs = guias(toks, texto)
            nombre = (p or {}).get("name") or m.get("reference") or "—"
            g = grupos[clave] = {
                "id": pid or 0, "clave": f"{clave[0]}·{destino}", "nombre": nombre, "picking": _corto(nombre),
                "hora": _local((p or {}).get("date_done") or m.get("date")) or "",
                "tipo": _tipo_entrada(p, m, u_org, es_retiro_full(texto, gs)), "destino": destino,
                "piezas": 0.0, "skus": set(), "tokens": set(toks), "guias": gs, "guia": guia_txt(gs),
                "no_reconocida": guia_no_reconocida(texto, gs),
                "orden_venta": _orden_de_venta(p) if p else None,
                "origen": (u_org or {}).get("complete_name") or _nombre_de(m.get("location_id")),
                "socio": _nombre_de((p or {}).get("partner_id")),
                "documento": texto_plano((p or {}).get("origin")), "nota": nota[:300]}
        g["piezas"] += float(m.get("quantity") or 0)
        cod = codigos.get(_id_de(m.get("product_id")) or -1, "")
        if cod:
            g["skus"].add(cod.upper())
    salida = sorted(grupos.values(), key=lambda r: (r["hora"], r["picking"]))
    pend = _a_scrap_por_sku(crudo, codigos)
    for g in salida:
        g["a_scrap"] = None
        if g["destino"] == "rack" and g["skus"]:
            g["a_scrap"] = _paso_a_scrap(pend, sorted(g["skus"])[0], g["hora"], g["piezas"])
            if g["a_scrap"]:
                g["destino"] = "scrap"
    return salida


# ── La liga dura (FUNCIÓN PURA) ──────────────────────────────────────────────

def tokens_devolucion(d: dict) -> dict[str, Any]:
    """Lo que identifica a una devolución de ML en una nota de Odoo: el envío de su
    devolución (`shipments[].shipment_id`, el «Envío #» de la etiqueta), la guía de
    la paquetería (`tracking_number`) y la orden."""
    envios, guias_ = set(), set()
    for s in d.get("envios") or []:
        if s.get("envio"):
            envios.add(str(s["envio"]).strip().upper())
        if s.get("guia"):
            guias_.add(str(s["guia"]).strip().upper())
    return {"envio": envios, "guia": guias_ - envios, "orden": str(d.get("pedido") or "").strip()}


def _clave(d: dict) -> str:
    return f"{d['id']}|{str(d.get('sku') or '').upper()}"


def _a_rack(r: dict, sku: str) -> bool:
    """¿La recepción mandó este SKU a una ubicación vendible (la que sube el stock)?"""
    if "lineas" in r:
        return any(l["sku"].upper() == sku and l["destino"] == "rack" for l in r["lineas"])
    return r.get("destino") == "rack" and sku in (r.get("skus") or ())


def _piezas_en(r: dict, sku: str) -> float:
    """Las piezas de este SKU que trae la recepción (de la global o de la de un SKU)."""
    if "lineas" in r:
        return sum(l["cantidad"] for l in r["lineas"] if l["sku"].upper() == sku)
    return float(r.get("piezas") or 0) if sku in (r.get("skus") or ()) else 0.0


def _un_caracter(a: str, b: str) -> bool:
    """¿`b` es `a` con UN carácter de más, de menos o cambiado?"""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    corto, largo = (a, b) if len(a) < len(b) else (b, a)
    i = next((k for k in range(len(corto)) if corto[k] != largo[k]), len(corto))
    return corto[i:] == largo[i + 1:]


def txt_aprox(guia: str, en_nota: str) -> str:
    """'la nota trae 3832142924750: un dígito de más que la guía 383142924750'."""
    cual = ("un dígito de más" if len(en_nota) > len(guia) else
            "un dígito de menos" if len(en_nota) < len(guia) else "un carácter distinto")
    return f"la nota trae {en_nota}: {cual} que la guía {guia}"


def ligar_duro(devs: list[dict], recs: list[dict]) -> tuple[dict[str, dict], dict[int, list[dict]]]:
    """Liga cada devolución de ML con la recepción de Odoo que trae su guía.

    `devs`: [{id, cuenta, sku, pedido, abierta (local), estado_canal, envios}] (una
            por devolución y SKU; los envíos como los da `_SQL_DEVOLUCIONES`).
    `recs`: las de `recepciones_de` o `entradas_sku_de`.
    Devuelve ({clave (id|SKU): {rec, por, otras, otras_recs, aprox}}, {id de recepción:
    [{id, cuenta, sku, por}] de TODAS las devoluciones que la nombran}).

    Prioridad: envío de la devolución > guía de la paquetería > orden > «Devolver».
    La orden y «Devolver» solo cuentan si la devolución es la ÚNICA viva de esa orden
    (o la única cuyo SKU está en la recepción). Ninguna se liga a una recepción de
    antes de abrirse, y un envío cancelado o vencido no se liga nunca. Entre
    candidatas del mismo rango gana la que trae el SKU, luego la que lo mandó a rack
    (la misma guía en IN/01480 a SCRAP y en IN/01481 a rack: subió el stock) y, luego,
    la más vieja. Al final, la guía con un carácter de diferencia (`_APROX_MIN`)."""
    por_token: dict[str, list[dict]] = defaultdict(list)
    por_venta: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        for t in r.get("tokens") or ():
            por_token[t].append(r)
        if r.get("orden_venta"):
            por_venta[r["orden_venta"]].append(r)
    vivas = [d for d in devs if (d.get("estado_canal") or "") not in _NO_LIGAN]
    misma_orden: dict[str, list[dict]] = defaultdict(list)
    for d in vivas:
        if d.get("pedido"):
            misma_orden[str(d["pedido"]).strip()].append(d)

    def unica(d: dict, r: dict) -> bool:
        hermanas = misma_orden.get(str(d.get("pedido") or "").strip(), [])
        if len({h["id"] for h in hermanas}) <= 1:
            return True
        con_sku = {h["id"] for h in hermanas if str(h.get("sku") or "").upper() in (r.get("skus") or ())}
        return con_sku == {d["id"]}

    por_dev: dict[str, dict] = {}
    por_rec: dict[int, list[dict]] = defaultdict(list)
    for d in vivas:
        tk = tokens_devolucion(d)
        cands: list[tuple[str, dict]] = []
        for kind in ("envio", "guia"):
            for t in sorted(tk[kind]):
                cands += [(kind, r) for r in por_token.get(t, [])]
        if tk["orden"]:
            cands += [("orden", r) for r in por_token.get(tk["orden"], []) if unica(d, r)]
            cands += [("devolver", r) for r in por_venta.get(tk["orden"], []) if unica(d, r)]
        tope = _menos(d["abierta"], horas=_ANTES_DE_ABRIR_MIN / 60) if d.get("abierta") else ""
        cands = [(k, r) for k, r in cands if r.get("hora") and r["hora"] >= tope]
        if not cands:
            continue
        sku = str(d.get("sku") or "").upper()
        cands.sort(key=lambda c: (_RANGO[c[0]], sku not in (c[1].get("skus") or ()), not _a_rack(c[1], sku),
                                  c[1]["hora"], c[1]["id"]))
        kind, r = cands[0]
        otras = {id(c[1]): c[1] for c in cands if c[1] is not r and c[1]["picking"] != r["picking"]}
        por_dev[_clave(d)] = {"rec": r, "por": _POR[kind],
                              "otras": sorted({o["picking"] for o in otras.values()}),
                              "otras_recs": sorted(otras.values(), key=lambda o: (o["hora"], o["picking"])),
                              "aprox": None}
        vistos: set[int] = set()
        for k, rr in cands:
            if id(rr) in vistos:
                continue
            vistos.add(id(rr))
            por_rec[rr["id"]].append({"id": str(d["id"]), "cuenta": (d.get("cuenta") or "").upper(),
                                      "sku": d.get("sku"), "por": _POR[k], "_d": d})
    _ligar_aprox(vivas, recs, por_dev, por_rec)
    return por_dev, por_rec


def _ligar_aprox(vivas: list[dict], recs: list[dict], por_dev: dict[str, dict],
                 por_rec: dict[int, list[dict]]) -> None:
    """La segunda pasada de `ligar_duro`: una guía de `_APROX_MIN` o más con UN carácter
    de diferencia contra un token de la nota. Solo recepciones que ninguna devolución
    nombra, con el mismo SKU y las mismas piezas, y solo pares únicos (una devolución
    con una sola recepción, y esa recepción con una sola devolución). Medido el 9-oct:
    de las 6 de «buscar», solo la 5558975718 (IN/01167) coincide así."""
    por_sku: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        if r.get("id") and r["id"] not in por_rec:
            for s in r.get("skus") or ():
                por_sku[s].append(r)
    pares: dict[str, dict[int, tuple[dict, str, str]]] = {}
    for d in vivas:
        sku = str(d.get("sku") or "").upper()
        if _clave(d) in por_dev or not por_sku.get(sku):
            continue
        tk = tokens_devolucion(d)
        largos = sorted(t for t in tk["envio"] | tk["guia"] if len(t) >= _APROX_MIN)
        tope = _menos(d["abierta"], horas=_ANTES_DE_ABRIR_MIN / 60) if d.get("abierta") else ""
        piezas = float(d.get("piezas") or 0)
        for r in por_sku[sku]:
            if (r.get("hora") or "") < tope or (piezas and _piezas_en(r, sku) != piezas):
                continue
            u = next(((t, u) for t in largos for u in sorted(r.get("tokens") or ()) if _un_caracter(t, u)), None)
            if u:
                pares.setdefault(_clave(d), {})[id(r)] = (r, *u)
    cuantas = Counter(i for ps in pares.values() for i in ps)
    for d in vivas:
        ps = pares.get(_clave(d))
        if not ps or len(ps) != 1:
            continue
        i, (r, guia, en_nota) = next(iter(ps.items()))
        if cuantas[i] != 1:
            continue
        por_dev[_clave(d)] = {"rec": r, "por": "guia_aprox", "otras": [], "otras_recs": [], "aprox": (guia, en_nota)}
        por_rec[r["id"]].append({"id": str(d["id"]), "cuenta": (d.get("cuenta") or "").upper(),
                                 "sku": d.get("sku"), "por": "guia_aprox", "_d": d})


# ── Subidas de Odoo y su reparto (FUNCIONES PURAS) ───────────────────────────

def _woo_de(bitacora: list[dict]) -> list[dict]:
    """Los renglones `odoo_delta` de stock_watch como los «woo» de la trazabilidad
    (la foto de Odoo del motivo y si falló la escritura a Woo), del más viejo al más nuevo."""
    filas = sorted((f for f in bitacora if f.get("accion") == "odoo_delta"
                    and (f.get("canal") or "woocommerce") == "woocommerce"),
                   key=lambda f: (f.get("hora") or "", f.get("id") or 0))
    salida = []
    for f in filas:
        de, a = V._foto_odoo(f.get("motivo") or "")
        salida.append({"tipo": "woo", "hora": f.get("hora"), "origen": "odoo", "odoo_de": de, "odoo_a": a,
                       "fallo": "FALLÓ" in (f.get("resultado") or ""), "ts": f.get("ts"), "id": f.get("id"),
                       "resultado": f.get("resultado") or ""})
    return salida


def asignar_subidas(recs: list[dict], woo: list[dict]) -> tuple[dict[int, list[dict]], list[dict]]:
    """Cada recepción A RACK a la PRIMERA subida de Odoo con hora ≥ la suya y a no más
    de `_SUBIDA_MAX_H` (stock_watch pasa cada 20 min). ({índice en `woo`:
    [recepciones]}, [las que no tienen subida]). Las de SCRAP no se asignan —no suben
    el stock y su hora no dice en qué pasada se vieron: IN/01317 a SCRAP quedaba bajo
    la subida que hizo IN/01319 1 h 38 min después— y van en la segunda lista."""
    subidas = V._subidas_odoo(woo)
    por_sub: dict[int, list[dict]] = defaultdict(list)
    sin: list[dict] = []
    for r in sorted(recs, key=lambda x: (x.get("hora") or "", x.get("id") or 0)):
        t = V._hora_dt(r.get("hora")) if r.get("destino", "rack") == "rack" else None
        sub = next((i for i, ts, _s in subidas
                    if t is not None and ts is not None and t <= ts <= t + timedelta(hours=_SUBIDA_MAX_H)), None)
        if sub is None:
            sin.append(r)
        else:
            por_sub[sub].append(r)
    return por_sub, sin


def _reparto_de(fila_woo: dict, bitacora: list[dict]) -> list[dict]:
    """Las filas del fan-out del MISMO pase que una subida: el primer grupo (mismo `ts`)
    de filas de reparto hasta `_REPARTO_MAX_MIN` después."""
    ts = fila_woo.get("ts")
    if ts is None:
        return []
    tope = ts + timedelta(minutes=_REPARTO_MAX_MIN)
    filas = [f for f in bitacora if (f.get("canal") or "") not in ("", "woocommerce")
             and f.get("accion") in V._ACC_FANOUT and f.get("ts") is not None and ts < f["ts"] <= tope]
    if not filas:
        return []
    primero = min(f["ts"] for f in filas)
    return [f for f in filas if f["ts"] == primero]


def texto_reparto(fila_woo: dict, reparto: list[dict]) -> str:
    """'Woo 412 → 415 · reparto: omitido en las 3 publicaciones (ML FULL ×2, Amazon cerrada)'."""
    m = V._FLECHA.search(fila_woo.get("resultado") or "")
    if fila_woo.get("fallo"):
        base = "stock_watch no pudo escribirlo en Woo"
    elif m:
        base = f"Woo {int(m.group(1)):,} → {int(m.group(2)):,}"
    else:
        base = "stock_watch lo anotó"
    if not reparto:
        return f"{base} · sin reparto en la bitácora"
    if all(f.get("accion") == "sin_destinos" for f in reparto):
        return f"{base} · reparto: sin publicaciones a dónde repartir"
    escritos, fallas, iguales, omitidos = [], [], [], Counter()
    for f in reparto:
        c = V._celda_reparto([f])
        nombre = V._nombre_destino(f.get("canal") or "", (f.get("cuenta") or "").upper())
        if c["k"] == "ok":
            escritos.append(f"{nombre} {c['texto']}")
        elif c["k"] == "mal":
            fallas.append(f"{nombre} ({c['texto']})")
        elif c["k"] in ("igual", "sim"):
            iguales.append(f"{nombre} {c['texto']}")
        elif c["k"] in ("full", "omit"):
            omitidos[f"{_CANAL_CORTO.get(f.get('canal') or '', f.get('canal') or '')} {c['texto']}"] += 1
    partes = []
    if escritos:
        partes.append("escrito en " + ", ".join(escritos))
    if fallas:
        partes.append("falló en " + ", ".join(fallas))
    if iguales:
        partes.append(", ".join(iguales))
    if omitidos:
        n = sum(omitidos.values())
        det = ", ".join(f"{k} ×{v}" if v > 1 else k
                        for k, v in sorted(omitidos.items(), key=lambda kv: (-kv[1], kv[0])))
        todas = "las " if n == len(reparto) and n > 1 else ""
        partes.append(f"omitido en {todas}{n} {'publicación' if n == 1 else 'publicaciones'} ({det})")
    return f"{base} · reparto: " + ("; ".join(partes) or "sin cambios")


# ── La bandeja (FUNCIÓN PURA) ────────────────────────────────────────────────

def _estado_ml(d: dict, item: dict) -> str:
    if item.get("llego"):
        return "entregada"
    e = _ESTADO_ML.get(d.get("estado_canal") or "")
    if e:
        return e
    return "por_enviar" if d.get("estado") == "abierta" else "en_camino"


def _llegada_de(item: dict) -> str | None:
    if not item.get("llego") or not item.get("llegada"):
        return None
    rec = next((p for p in item.get("pasos") or [] if p["estado"] == "recibida"), None)
    if rec is None:
        return "cierre de ML"           # sin paso: la del reembolso (ML reembolsa al entregar)
    return rec["de"] if rec["de"] in ("texto de ML", "cierre de ML") else "en vivo"


def _pasos_txt(d: dict, item: dict, estado: str, grupo: str, hoy: str, ahora: str,
               gemela: dict | None) -> str:
    """'Despachada 29-sep 16:36 · llegó el vie 2-oct · reembolsada al despachar'."""
    pasos = item.get("pasos") or []
    salida = V._salida(d, pasos)
    partes: list[str] = []
    if salida and estado in ("en_camino", "entregada"):
        partes.append(f"Despachada {V._fecha(salida, hoy)}")
    if estado == "entregada":
        llegada = item.get("llegada")
        if not llegada:
            partes.append("ML la da por entregada sin decir qué día")
        elif len(llegada) == 10:
            partes.append(f"llegó {_cuando(llegada, hoy)}")
        elif llegada[:10] == hoy:
            partes.append(f"ML la dio por entregada hoy {llegada[11:16]}")
        else:
            partes.append(f"llegó {V._fecha(llegada, hoy)}")
    elif estado == "en_camino":
        if not salida:
            n = _dias_entre(d.get("abierta"), ahora)
            partes.append(f"ML la tiene en camino (abierta hace {n} días)" if n is not None else "ML la tiene en camino")
        elif grupo == "atrasada":
            partes.append("ML no ha marcado la entrega")
    elif estado == "por_enviar":
        partes.append("ML: guía generada, sin despacho" if grupo == "atrasada"
                      else "Guía generada, el comprador no la ha enviado")
    elif estado == "vencida":
        partes.append("El comprador nunca la envió")
    elif estado == "cancelada":
        partes.append("Envío cancelado" + (f"; la {gemela['id']} de la misma venta sí llegó" if gemela else ""))
    elif estado == "no_entregada":
        partes.append("ML dice que no se entregó")
    if V._reembolso_al_despachar(d) and estado in ("en_camino", "entregada"):
        partes.append("reembolsada al despachar")
    elif d.get("reembolsada"):
        partes.append(f"reembolsada {V._fecha(d['reembolsada'], hoy)}")
    return " · ".join(partes)


def _probables(items: list[dict], woo: list[dict], recs_sku: list[dict], ahora: str, desde: str,
               liga_dias: int, descontar: bool) -> dict[str, dict]:
    """La liga por fecha de v0.628.0 para las que llegaron sin liga dura, contra las
    subidas de Odoo que NO explican ya recepciones identificadas —con guía o retiro de
    FULL— (`descontar`). Las subidas llenas se le pasan a `ligar_devoluciones` como
    `excluir`, sin quitar renglones: así el reintento de una escritura a Woo fallida
    sigue contando como la misma subida. {clave (id|SKU): {hora, texto}}: una
    devolución con dos SKUs lleva la liga de cada uno."""
    if not items:
        return {}
    llenas: set[int] = set()
    if descontar and recs_sku:
        skus = {str(x.get("sku")).upper() for x in items}
        por_sub, _sin = asignar_subidas([r for r in recs_sku if r["destino"] == "rack"
                                         and (r["guias"] or r.get("retiro_full"))], woo)
        sube = {i: s for i, _t, s in V._subidas_odoo(woo)}
        for i, rs in por_sub.items():
            explicado = sum(l["cantidad"] for r in rs for l in r["lineas"]
                            if l["destino"] == "rack" and l["sku"].upper() in skus)
            if explicado >= sube.get(i, 0):
                llenas.add(i)
    copia = [dict(it) for it in items]
    por_dev, _por_sub = V.ligar_devoluciones(copia, woo, ahora, liga_dias, desde, excluir=llenas)
    salida = {}
    for it in copia:
        liga = por_dev.get(it["id"])
        if liga and liga.get("k") == "reingreso":
            salida[f"{it['id']}|{str(it.get('sku') or '').upper()}"] = {"hora": liga["hora"], "texto": liga["texto"]}
    return salida


def _en_periodo(abierta: str | None, llegada: str | None, desde: str) -> bool:
    """Una devolución a nuestra bodega entra al periodo si se ABRIÓ o LLEGÓ en él."""
    if (abierta or "") >= desde:
        return True
    lleg = llegada or ""
    return bool(lleg) and lleg >= (desde[:10] if len(lleg) == 10 else desde)


def desde_odoo(devs: list[dict], ahora: str, dias: int) -> str:
    """Desde cuándo leer Odoo para la bandeja (hora local, a las 00:00): la apertura
    más vieja de las filas a nuestra bodega que se van a mostrar —una entra por su
    llegada aunque se abriera antes, y Odoo pudo recibirla antes de que ML la diera
    por entregada— o el inicio del periodo, menos un día. FUNCIÓN PURA."""
    desde = _menos(ahora, dias=dias)
    items = V._armar_devoluciones(devs, [])
    viejas = [d.get("abierta") for it, d in zip(items, devs)
              if it["destino"] == "seller_address" and d.get("abierta")
              and _en_periodo(d.get("abierta"), it.get("llegada"), desde)]
    return _menos(min([desde, *viejas]), dias=1)[:10] + " 00:00:00"


def armar_bandeja(devs: list[dict], odoo: dict[str, Any], bitacora: list[dict], ahora: str, dias: int,
                  cuenta: str | None = None, liga_dias: int = 10) -> dict[str, Any]:
    """La respuesta de `GET /api/fanout/devoluciones`. FUNCIÓN PURA.

    `devs`: filas de `_SQL_DEVOLUCIONES` (una por devolución y SKU, todas las cuentas
            y destinos, con las de antes del periodo para el índice de guías).
    `odoo`: {ok, recepciones, leido, error} de `leer_odoo`.
    `bitacora`: los `odoo_delta` de los SKUs con devolución (`_SQL_SUBIDAS`)."""
    hoy = ahora[:10]
    desde = _menos(ahora, dias=dias)
    odoo_ok = bool(odoo.get("ok"))
    recs = odoo.get("recepciones") or [] if odoo_ok else []
    items = V._armar_devoluciones(devs, [])
    for it, d in zip(items, devs):
        it["_d"] = d
        it["sku"] = d.get("sku")
        it["abierta"] = d.get("abierta")
        it["estado_canal"] = d.get("estado_canal")
    por_dev, _por_rec = ligar_duro(devs, recs) if odoo_ok else ({}, {})

    bodega = [it for it in items if it["destino"] == "seller_address"
              and _en_periodo(it.get("abierta"), it.get("llegada"), desde)]
    # FULL y las que ML no dice a dónde van (el envío sin `destination`: medido el 9-oct,
    # 32 en 60 días, todas de ventas FULL y ninguna en Odoo) no tocan nuestra bodega:
    # solo se cuentan, por apertura.
    abiertas = [it for it in items if (it.get("abierta") or "") >= desde]
    a_full = [it for it in abiertas if it["destino"] == "warehouse"]
    sin_destino = [it for it in abiertas if not it["destino"]]
    if cuenta:
        bodega = [it for it in bodega if it["cuenta"] == cuenta]
        a_full = [it for it in a_full if it["cuenta"] == cuenta]
        sin_destino = [it for it in sin_destino if it["cuenta"] == cuenta]

    # Subidas por SKU: para «Odoo 411 → 412» de las recibidas y para la liga probable.
    bit_sku: dict[str, list[dict]] = defaultdict(list)
    for f in bitacora:
        bit_sku[str(f.get("sku") or "").upper()].append(f)
    woo_sku = {s: _woo_de(fs) for s, fs in bit_sku.items()}
    recs_sku: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        for s in r["skus"]:
            recs_sku[s].append(r)
    sub_de_rec: dict[tuple[str, int], dict] = {}
    for s, rs in recs_sku.items():
        woo = woo_sku.get(s) or []
        if woo:
            por_sub, _sin = asignar_subidas(rs, woo)
            for i, rr in por_sub.items():
                for r in rr:
                    sub_de_rec[(s, r["id"])] = woo[i]

    filas: list[dict] = []
    for it in bodega:
        d = it["_d"]
        sku = str(d.get("sku") or "")
        liga = por_dev.get(_clave(d))
        estado = _estado_ml(d, it)
        llego = bool(it.get("llego"))
        odoo_f = None
        if liga:
            r = liga["rec"]
            mismas = [l for l in r["lineas"] if l["sku"].upper() == sku.upper()]
            otro = not mismas
            destino = (("rack" if any(l["destino"] == "rack" for l in mismas) else "scrap") if mismas
                       else r["destino"])
            recibido = (mismas[0]["sku"] if mismas else
                        ", ".join(sorted({l["sku"] for l in r["lineas"] if l["sku"]})) or None)
            odoo_f = {"picking": r["picking"], "hora": r["hora"], "destino": destino, "por": liga["por"],
                      "sku_recibido": recibido, "otro_sku": otro}
        if not odoo_ok:
            grupo = "buscar" if llego else ("noregresa" if estado in _NO_REGRESAN else "camino")
        elif odoo_f:
            grupo = ("otro" if odoo_f["otro_sku"] else "atrasada" if not llego
                     else "scrap" if odoo_f["destino"] == "scrap" else "odoo")
        else:
            grupo = "buscar" if llego else ("noregresa" if estado in _NO_REGRESAN else "camino")
        filas.append({"_it": it, "_d": d, "_liga": liga, "grupo": grupo, "estado_ml": estado, "odoo": odoo_f})

    # Gemelas: otra devolución de la misma venta y SKU que sí entró a Odoo.
    ligadas_por_orden: dict[tuple[str, str], dict] = {}
    for f in filas:
        if f["odoo"]:
            ligadas_por_orden.setdefault((str(f["_d"].get("pedido")), str(f["_d"].get("sku")).upper()), f)

    # La liga probable por fecha, por SKU, para las que llegaron sin liga dura.
    probables: dict[str, dict] = {}
    sin_dura: dict[str, list[dict]] = defaultdict(list)
    for f in filas:
        if f["grupo"] == "buscar":
            sin_dura[str(f["_d"].get("sku")).upper()].append(f["_it"])
    for s, its in sin_dura.items():
        probables.update(_probables(its, woo_sku.get(s) or [], recs_sku.get(s) or [], ahora,
                                    _menos(ahora, dias=dias + 1), liga_dias, descontar=odoo_ok))

    salida_filas = []
    for f in filas:
        it, d, o, grupo, estado = f["_it"], f["_d"], f["odoo"], f["grupo"], f["estado_ml"]
        envio = next((str(s["envio"]) for s in reversed(d.get("envios") or [])
                      if s.get("envio") and s.get("destino") == "seller_address"), None) \
            or next((str(s["envio"]) for s in reversed(d.get("envios") or []) if s.get("envio")), None)
        despacho = V._salida(d, it.get("pasos") or [])
        llegada = it.get("llegada") if it.get("llego") else None
        dias_f = (_dias_entre(llegada, ahora) if llegada else
                  _dias_entre(despacho, ahora) if despacho and estado in ("en_camino", "entregada") else None)
        gemela = (ligadas_por_orden.get((str(d.get("pedido")), str(d.get("sku")).upper()))
                  if grupo == "noregresa" else None)
        gem = {"id": gemela["_d"]["id"], "picking": gemela["odoo"]["picking"]} if gemela else None
        odoo_txt, odoo_sub, accion = _textos_odoo(grupo, o, f["_liga"], estado, envio, dias_f, odoo_ok,
                                                  sub_de_rec, sku=str(d.get("sku") or "").upper(), hoy=hoy,
                                                  gemela=gem, desde_llegada=bool(llegada))
        salida_filas.append({
            "grupo": grupo, "id": str(d["id"]), "cuenta": it["cuenta"], "sku": d.get("sku"),
            "titulo": d.get("titulo") or "", "piezas": int(d.get("piezas") or 0), "abierta": d.get("abierta"),
            "estado_ml": estado, "estado_txt": _ESTADO_ML_TXT[estado],
            "reembolsada": bool(d.get("reembolsada")) or d.get("estado") == "reembolsada",
            "pasos_txt": _pasos_txt(d, it, estado, grupo, hoy, ahora, gem),
            "guia": envio, "despacho": despacho, "llegada": llegada, "llegada_de": _llegada_de(it),
            "odoo": o, "probable": probables.get(_clave(d)) if not o and grupo == "buscar" else None,
            "odoo_txt": odoo_txt, "odoo_sub": odoo_sub, "accion": accion, "dias": dias_f})
    # Por grupo y, dentro, la más reciente primero (orden estable: id, apertura, grupo).
    orden_g = {g: i for i, g in enumerate(GRUPOS)}
    salida_filas.sort(key=lambda x: x["id"])
    salida_filas.sort(key=lambda x: x["abierta"] or "", reverse=True)
    salida_filas.sort(key=lambda x: orden_g[x["grupo"]])
    cuenta_g = Counter(x["grupo"] for x in salida_filas)
    llegaron_ml = sum(1 for f in filas if f["_it"].get("llego"))
    con_odoo = [x for x in salida_filas if x["odoo"]]
    resumen = {
        "a_bodega": len(salida_filas), "piezas": sum(x["piezas"] for x in salida_filas),
        "llegaron": llegaron_ml + cuenta_g["atrasada"], "llegaron_ml": llegaron_ml,
        # `en_odoo_guia` es TODA liga dura (guía, orden o «Devolver»); el desglose, aparte.
        "solo_odoo": cuenta_g["atrasada"], "en_odoo_guia": len(con_odoo),
        "por_guia": sum(1 for x in con_odoo if x["odoo"]["por"] in ("guia", "guia_aprox")),
        "por_venta": sum(1 for x in con_odoo if x["odoo"]["por"] in ("orden", "devolver")),
        "subieron": sum(1 for x in con_odoo if not x["odoo"]["otro_sku"] and x["odoo"]["destino"] == "rack"),
        "scrap": sum(1 for x in con_odoo if not x["odoo"]["otro_sku"] and x["odoo"]["destino"] == "scrap"),
        "otro_sku": cuenta_g["otro"], "buscar": cuenta_g["buscar"], "atrasada": cuenta_g["atrasada"],
        "en_camino": cuenta_g["camino"], "no_regresan": cuenta_g["noregresa"], "a_full": len(a_full),
        "sin_destino": len(sin_destino)}
    filtros = [{"id": "todas", "n": len(salida_filas)}] + [{"id": g, "n": cuenta_g[g]} for g in GRUPOS]
    return {"ahora": ahora, "dias": dias, "odoo_ok": odoo_ok, "odoo_error": odoo.get("error"),
            "odoo_leido": odoo.get("leido"), "resumen": resumen, "filtros": filtros,
            "filas": salida_filas,
            "cobertura": _cobertura(devs, recs, desde, {l["aprox"][1] for l in por_dev.values() if l.get("aprox")})
            if odoo_ok else
            {"recepciones": 0, "retiros_full": 0, "guias_en_recepciones": 0, "guias_sin_kubera": 0}}


def _textos_odoo(grupo: str, o: dict | None, liga: dict | None, estado: str, envio: str | None,
                 dias: int | None, odoo_ok: bool, sub_de_rec: dict, *, sku: str, hoy: str,
                 gemela: dict | None, desde_llegada: bool = True) -> tuple[str, str, str]:
    """(odoo_txt, odoo_sub, accion) de una fila. `dias` cuenta desde la llegada si ML dio el
    día (`desde_llegada`), si no desde el despacho: el texto dice cuál."""
    guia = f"Guía {envio}" if envio else "Sin guía de ML"
    if grupo == "buscar":
        desde = "desde que llegó" if desde_llegada else "desde el despacho"
        sub = guia + (f" · {dias} {'día' if dias == 1 else 'días'} {desde}" if dias is not None else "")
        if not odoo_ok:
            return "Sin verificar en Odoo", f"Odoo no contestó · {sub}", "Verificar cuando Odoo conteste"
        return "Sin recepción con su guía", sub, "Buscar en Bodega"
    if grupo in ("camino", "noregresa") or not o:
        if grupo == "noregresa":
            return "No regresa", (f"Su gemela entró en {gemela['picking']}" if gemela else ""), "—"
        return ("Aún no sale" if estado == "por_enviar" else "Aún no llega"), "", "Esperar"
    cuando = V._fecha(o["hora"], hoy)
    por = (f"por guía: {txt_aprox(*liga['aprox'])}" if liga and liga.get("aprox")
           else _POR_TXT.get(o["por"], o["por"]))
    # Las otras recepciones que nombran la misma guía, con a dónde mandaron este SKU.
    otras = ""
    if liga and liga.get("otras_recs"):
        otras = " · también " + ", ".join(
            x["picking"] + (" a rack" if _a_rack(x, sku) else " a SCRAP" if sku in (x.get("skus") or ())
                            else " con otro SKU")
            for x in liga["otras_recs"][:2])
    if grupo == "otro":
        return (f"{o['picking']} recibió otro SKU", f"{o['sku_recibido'] or 'sin producto'} · {cuando}",
                "Revisar con Bodega")
    if o["destino"] == "scrap":
        paso = next((l.get("a_scrap") for l in (liga["rec"]["lineas"] if liga else [])
                     if l["sku"].upper() == sku and l.get("a_scrap")), None)
        motivo = (f"Entró a rack y pasó a SCRAP a las {paso[11:16]}: no sube el stock" if paso
                  else "No sube el stock")
        return f"A SCRAP · {o['picking']} · {cuando}", f"{motivo} · {por}{otras}", \
            ("Nada en Bodega" if grupo == "atrasada" else "—")
    w = sub_de_rec.get((sku, liga["rec"]["id"])) if liga else None
    foto = f" · Odoo {int(w['odoo_de']):,} → {int(w['odoo_a']):,}" if w else ""
    return f"Recibida · {o['picking']} · {cuando}", f"A rack · {por}{foto}{otras}", \
        ("Nada en Bodega" if grupo == "atrasada" else "—")


def _indice_kubera(devs: list[dict]) -> set[str]:
    """Todas las guías y envíos de devolución que kubera conoce."""
    idx: set[str] = set()
    for d in devs:
        tk = tokens_devolucion(d)
        idx |= tk["envio"] | tk["guia"]
    return idx


def _cobertura(devs: list[dict], recs: list[dict], desde: str, aprox: set[str] = frozenset()) -> dict[str, int]:
    """Cuántas recepciones de devolución hubo en el periodo, cuántas son retiros de FULL
    (las cajas de la bodega de ML en la nota o el folio), cuántas guías traen las
    demás y cuántas de ésas no están en ninguna devolución de kubera."""
    en = [r for r in recs if (r.get("hora") or "") >= desde]
    todas = {v for r in en if not r.get("retiro_full") for _t, v in r["guias"]}
    return {"recepciones": len(en), "retiros_full": sum(1 for r in en if r.get("retiro_full")),
            "guias_en_recepciones": len(todas), "guias_sin_kubera": len(todas - _indice_kubera(devs) - aprox)}


# ── El cuadre de un SKU (FUNCIÓN PURA) ───────────────────────────────────────

def _txt_scrap(r: dict) -> str:
    return (f" · entró a rack y pasó a SCRAP a las {r['a_scrap'][11:16]}: no cuenta para el stock"
            if r.get("a_scrap") else " · a SCRAP: no cuenta para el stock")


def _txt_entrada(r: dict, tipo: str, dev: dict | None, sku: str, item_de: dict[str, dict], *, mas: int = 0,
                 misma: str | None = None, sin_k: int = 0, aprox: tuple[str, str] | None = None) -> str:
    scrap = _txt_scrap(r) if r["destino"] == "scrap" else ""
    if tipo == "devolucion":
        if dev:
            it = item_de.get(dev["id"])
            nombre = _nombre_cuenta(dev["cuenta"])
            if str(dev.get("sku") or "").upper() != sku:
                base = f"La guía es de la devolución {dev['id']} de {dev.get('sku')}: entró otro producto"
            else:
                base = f"Devolución {dev['id']} · {nombre}"
                if it is not None and not it.get("llego"):
                    base += " (ML aún la da en camino)"
                if mas:                     # un retiro con varias guías (IN/01319: 6 de TEC-0573-MET)
                    base += f" y {mas} más de kubera"
            if aprox:                       # IN/01167: la guía FedEx con un dígito de más
                base += f" · {txt_aprox(*aprox)}"
            if misma:                       # la misma guía en otra recepción (IN/01480 e IN/01481)
                base += f" · misma guía que {misma}"
            if sin_k:                       # IN/01296: una guía de kubera y 3 que kubera no tiene
                base += f" · {sin_k} {'guía' if sin_k == 1 else 'guías'} más que kubera no tiene"
            return base + scrap
        sin_guia = "guía no reconocida en la nota" if r.get("no_reconocida") else "sin guía en la nota"
        if r["destino"] == "scrap":
            return scrap[3:4].upper() + scrap[4:] + ("" if r["guias"] else f" · {sin_guia}")
        return "No está en kubera" if r["guias"] else ("Guía no reconocida en la nota" if r.get("no_reconocida")
                                                       else "Sin guía en la nota de Odoo")
    if tipo == "retiro_full":
        return "Retiro de FULL: cajas de la bodega de ML, no una devolución de cliente" + scrap
    if tipo == "compra":
        return "Compra" + (f" · {r['documento']}" if r.get("documento") else
                           f" · {r['socio']}" if r.get("socio") else "")
    if tipo == "traslado":
        return f"Traslado desde {r['origen']}" if r.get("origen") else "Traslado"
    if tipo == "ajuste":
        return "Ajuste de inventario"
    return f"Entrada desde {r['origen']}" if r.get("origen") else "Entrada"


def _token_de(d: dict, r: dict) -> str | None:
    """El envío (o la guía) de la devolución que trae la nota de la recepción."""
    tk = tokens_devolucion(d)
    return next((t for t in [*sorted(tk["envio"]), *sorted(tk["guia"])] if t in (r.get("tokens") or ())), None)


def armar_detalle(sku: str, dias: int, bitacora: list[dict], odoo: dict[str, Any], devs: list[dict],
                  ahora: str, titulo: str = "", pasada: str | None = None) -> dict[str, Any]:
    """La respuesta de `GET /api/fanout/devoluciones/sku/{sku}`. FUNCIÓN PURA.

    `bitacora`: las filas de `ops.fanout_log` del SKU en el periodo (`_SQL_BITACORA`).
    `odoo`: {ok, entradas (de `entradas_sku_de`), error}.
    `devs`: las devoluciones de ML de kubera (todas: el índice de guías es global).
    `pasada`: hora local de la última pasada de stock_watch (`_SQL_PASADA`): una
              recepción a rack validada después todavía no puede tener subida.

    Cada recepción: {clave, picking, hora, destino, tipo, piezas, guia (la de la
    devolución ligada primero), guia_no_reconocida, devolucion, en_kubera (ligada y sin
    guías que kubera no tenga), guias_sin_kubera, txt}; las de `sin_subida` llevan
    además `espera`. Las de SCRAP van en `a_scrap`. El resumen cuenta DEVOLUCIONES
    ligadas (no recepciones) y GUÍAS que kubera no tiene; los retiros de FULL, aparte."""
    sku_u = sku.upper()
    hoy = ahora[:10]
    desde = _menos(ahora, dias=dias)
    odoo_ok = bool(odoo.get("ok"))
    woo = _woo_de(bitacora)
    entradas = odoo.get("entradas") or [] if odoo_ok else []
    por_dev, por_rec = ligar_duro(devs, entradas) if odoo_ok else ({}, {})
    items = V._armar_devoluciones(devs, [])
    item_de = {it["id"]: it for it, d in zip(items, devs) if str(d.get("sku") or "").upper() == sku_u}
    # La guía mal tecleada de una liga aproximada SÍ es de kubera (IN/01167: 3832142924750).
    idx_kubera = _indice_kubera(devs) | {l["aprox"][1] for l in por_dev.values() if l.get("aprox")}

    en_periodo = [r for r in entradas if (r.get("hora") or "") >= desde]
    por_sub, sin = asignar_subidas(en_periodo, woo)
    dev_ids: set[str] = set()
    guias_sin: set[str] = set()

    def recepcion(r: dict) -> dict:
        ligadas = sorted(por_rec.get(r["id"], []) if r["id"] else [],
                         key=lambda x: (_RANGO.get(x["por"], 9), str(x.get("sku") or "").upper() != sku_u))
        # Un retiro de FULL que trae la guía de una devolución de kubera es esa devolución.
        tipo = "devolucion" if ligadas and r["tipo"] in ("devolucion", "retiro_full") else r["tipo"]
        dev = ligadas[0] if ligadas and tipo == "devolucion" else None
        liga = por_dev.get(_clave(dev["_d"])) if dev else None
        misma = liga["rec"]["picking"] if liga and liga["rec"]["picking"] != r["picking"] else None
        sin_k = [v for _t, v in r["guias"] if v not in idx_kubera] if tipo == "devolucion" else []
        if dev:                             # la que se nombra y las demás de ESTE SKU (IN/01319: 6)
            dev_ids.update({dev["id"]} | {x["id"] for x in ligadas if str(x.get("sku") or "").upper() == sku_u})
        guias_sin.update(sin_k)
        return {"clave": r.get("clave") or f"{r['id']}·{r['destino']}",
                "picking": r["picking"], "hora": r["hora"], "destino": r["destino"], "tipo": tipo,
                "piezas": int(r["piezas"]) if float(r["piezas"]).is_integer() else r["piezas"],
                "guia": guia_txt(r["guias"], _token_de(dev["_d"], r) if dev else None),
                "guia_no_reconocida": bool(r.get("no_reconocida")),
                "devolucion": ({"id": dev["id"], "cuenta": dev["cuenta"], "sku": dev.get("sku")} if dev else None),
                "en_kubera": bool(ligadas) and not sin_k, "guias_sin_kubera": len(sin_k),
                "txt": _txt_entrada(r, tipo, dev, sku_u, item_de,
                                    mas=(len({x["id"] for x in ligadas if str(x.get("sku") or "").upper() == sku_u})
                                         - 1 if dev else 0),
                                    misma=misma, sin_k=len(sin_k),
                                    aprox=liga.get("aprox") if liga and liga["rec"]["picking"] == r["picking"] else None)}

    subidas = []
    for i, _t, sube in V._subidas_odoo(woo):
        w = woo[i]
        if (w.get("hora") or "") < desde:
            continue
        subidas.append({"hora": w["hora"], "de": int(w["odoo_de"]), "a": int(w["odoo_a"]), "sube": sube,
                        "reparto_txt": texto_reparto(w, _reparto_de(w, bitacora)),
                        "recepciones": [recepcion(r) for r in por_sub.get(i, [])]})
    subidas.sort(key=lambda s: s["hora"], reverse=True)
    # A rack sin subida: validada después de la última pasada de stock_watch (aún no la
    # copia: IN/01562 a las 16:13 y su subida a las 16:22) o antes (una salida en la
    # misma pasada pudo compensarla). Las de SCRAP, aparte: no suben el stock.
    sin_subida = [{**recepcion(r), "espera": bool(pasada) and r["hora"] > (pasada or "")}
                  for r in sin if r["destino"] == "rack"]
    a_scrap = [recepcion(r) for r in sin if r["destino"] != "rack"]
    todas = [x for s in subidas for x in s["recepciones"]] + sin_subida + a_scrap
    devol = [x for x in todas if x["tipo"] == "devolucion"]
    resumen = {"entraron": sum(x["piezas"] for x in devol if x["destino"] == "rack"),
               "scrap": sum(x["piezas"] for x in devol if x["destino"] == "scrap"),
               "ligadas": len(dev_ids),
               "sin_kubera": len(guias_sin),
               "retiro_full": sum(x["piezas"] for x in todas if x["tipo"] == "retiro_full" and x["destino"] == "rack")}

    # Pendientes: devoluciones del SKU a nuestra bodega que llegaron o vienen y no entraron.
    ventana = _menos(ahora, dias=max(dias, _PENDIENTES_MIN_D))
    ligadas_ids = {x["id"] for rs in por_rec.values() for x in rs
                   if str(x.get("sku") or "").upper() == sku_u}
    pendientes = []
    for it, d in zip(items, devs):
        if (str(d.get("sku") or "").upper() != sku_u or it["destino"] != "seller_address"
                or (d.get("estado_canal") or "") in _NO_LIGAN):
            continue
        llego = bool(it.get("llego"))
        estado = _estado_ml(d, it)
        if estado in _NO_REGRESAN or str(d["id"]) in ligadas_ids:
            continue
        if (d.get("abierta") or "") < ventana and (it.get("llegada") or "") < ventana[:10]:
            continue
        envio = next((str(s["envio"]) for s in reversed(d.get("envios") or []) if s.get("envio")), None)
        if llego:
            lleg = it.get("llegada")
            txt = ("entregada hoy" if lleg and lleg[:10] == hoy else f"llegó {_cuando(lleg, hoy)}" if lleg
                   else "ML la da por entregada sin fecha")
        else:
            salida = V._salida(d, it.get("pasos") or [])
            txt = (f"en camino, despachada {_cuando(salida, hoy)}" if estado == "en_camino" and salida
                   else _ESTADO_ML_TXT[estado])
        if not odoo_ok:
            txt += " · sin verificar (Odoo no contestó)"
        pendientes.append({"id": str(d["id"]), "cuenta": it["cuenta"], "guia": envio, "txt": txt,
                           "_o": (0 if llego else 1, it.get("llegada") or d.get("abierta") or "")})
    pendientes.sort(key=lambda p: p["_o"])
    for p in pendientes:
        del p["_o"]

    fotos = [w for w in woo if w.get("odoo_de") is not None and (w.get("hora") or "") >= desde]
    return {"sku": sku, "titulo": titulo, "dias": dias, "ahora": ahora, "odoo_ok": odoo_ok,
            "odoo_error": odoo.get("error"),
            "odoo_de": fotos[0]["odoo_de"] if fotos else None, "odoo_a": fotos[-1]["odoo_a"] if fotos else None,
            "pasada": pasada, "subidas": subidas, "sin_subida": sin_subida, "a_scrap": a_scrap,
            "resumen": resumen, "pendientes": pendientes}


# ── Lecturas de kubera (BLOQUEAN; SOLO LECTURA) ──────────────────────────────
# Cada sentencia lleva su marca `/* devoluciones:… */`: las pruebas la usan para
# contestar sin base. TODAS son `select`.

_SQL_DEVOLUCIONES = r"""/* devoluciones:ml */
with r as (
  select r.canal, r.cuenta, r.external_return_id as id, r.estado, r.estado_canal,
         {DESTINO} as destino,
         r.es_fulfillment, r.venta_contaba, r.estado_dinero, r.external_order_id as pedido,
         coalesce(r.motivo_texto, r.motivo, r.motivo_canal) as motivo,
         r.abierta_at, r.reembolsada_at,
         case when coalesce(r.payload->'returns'->>'date_closed', '') ~ '^\d{4}-\d{2}-\d{2}T'
              then (r.payload->'returns'->>'date_closed')::timestamptz end as cerrada_at,
         r.payload->'returns'->>'refund_at' as refund_at,
         r.payload->'detalle'->>'description' as texto_ml,
         case when jsonb_typeof(r.payload->'returns'->'shipments') = 'array'
              then r.payload->'returns'->'shipments' else '[]'::jsonb end as envios
    from channel.returns r
   where r.canal = 'mercado_libre' and r.abierta_at > now() - make_interval(days => %(d)s)),
it as (
  select i.cuenta, i.external_return_id as id, i.sku::text as sku, sum(i.cantidad)::int as piezas,
         max(coalesce(i.titulo, oi.titulo)) as titulo
    from r
    join channel.return_items i on i.canal = r.canal and i.cuenta = r.cuenta and i.external_return_id = r.id
    left join channel.order_items oi on oi.canal = i.canal and oi.cuenta = i.cuenta
         and oi.external_order_id = r.pedido and oi.linea = i.linea_pedido
   group by i.cuenta, i.external_return_id, i.sku)
select r.cuenta, r.id, r.estado, r.estado_canal, r.destino, r.es_fulfillment, r.venta_contaba,
       r.estado_dinero, r.pedido, r.motivo, it.sku, it.piezas, it.titulo, r.refund_at, r.texto_ml,
       coalesce(r.abierta_at, r.cerrada_at, r.reembolsada_at) as ts,
       to_char(r.abierta_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as abierta,
       to_char(r.cerrada_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as cerrada,
       to_char(r.reembolsada_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as reembolsada,
       (select coalesce(json_agg(json_build_object(
                 'envio', s->>'shipment_id', 'guia', s->>'tracking_number',
                 'destino', s->'destination'->>'name', 'status', s->>'status') order by n), '[]'::json)
          from jsonb_array_elements(r.envios) with ordinality as t(s, n)) as envios,
       (select coalesce(json_agg(json_build_object(
                 'estado', h.estado_nuevo, 'antes', h.estado_anterior, 'via', h.detectado_via,
                 'ec', h.estado_canal,
                 'hora', to_char(h.changed_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS'))
               order by h.changed_at, h.id), '[]'::json)
          from channel.return_history h
         where h.canal = r.canal and h.cuenta = r.cuenta and h.external_return_id = r.id) as historia
  from r join it on it.cuenta = r.cuenta and it.id = r.id
 order by r.abierta_at desc, r.id, it.sku""".replace("{DESTINO}", V._SQL_DESTINO)

_SQL_SUBIDAS = """/* devoluciones:subidas */
select sku::text as sku, id, ts, to_char(ts at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as hora,
       'woocommerce' as canal, accion, coalesce(motivo, '') as motivo,
       left(coalesce(resultado, ''), 300) as resultado
  from ops.fanout_log
 where canal = 'woocommerce' and accion = 'odoo_delta'
   and sku = any(%(s)s::citext[]) and ts > now() - make_interval(days => %(d)s)
 order by ts, id"""

_SQL_BITACORA = """/* devoluciones:bitacora */
select id, ts, to_char(ts at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as hora,
       coalesce(motivo, '') as motivo, coalesce(canal, '') as canal,
       upper(coalesce(cuenta, '')) as cuenta, accion, left(coalesce(resultado, ''), 300) as resultado,
       stock_canal, objetivo, coalesce(item_id, '') as item_id, dry_run
  from ops.fanout_log
 where sku = %(s)s and ts > now() - make_interval(days => %(d)s)
 order by ts desc, id desc limit 3000"""

_SQL_TITULO = """/* devoluciones:titulo */
select name from core.products where sku = %(s)s and coalesce(name, '') <> '' limit 1"""

# La última pasada de stock_watch: reescribe `actualizado` en TODA la foto en cada pasada.
_SQL_PASADA = """/* devoluciones:pasada */
select to_char(max(actualizado) at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as ultima
  from ops.stock_watch_photo"""

_kubera_cache: dict[int, tuple[float, list[dict]]] = {}
_kubera_lock = threading.Lock()


def _leer_devoluciones(dias_atras: int) -> list[dict]:
    """Las devoluciones de ML abiertas en los últimos `dias_atras` días (caché de 30 s)."""
    with _kubera_lock:
        c = _kubera_cache.get(dias_atras)
        if c and time.monotonic() - c[0] < _KUBERA_TTL:
            return c[1]
    filas = sdb.fetch_all(_SQL_DEVOLUCIONES, {"d": dias_atras, "z": ZONA})
    with _kubera_lock:
        _kubera_cache[dias_atras] = (time.monotonic(), filas)
    return filas


# ── Odoo con caché (NUNCA LANZA) ─────────────────────────────────────────────
# Dos candados como en fanout_bodegas: `_odoo_lock` solo protege los dicts (nunca se
# sostiene durante la red); `_odoo_leyendo` hace que haya UNA relectura a la vez, y
# quien llega mientras corre no hace fila más de `ODOO_ESPERA`. Una sola caché: la
# lectura cubre desde `desde` y sirve a todo periodo que empiece después. El freno tras
# una falla es de TODO Odoo (la bandeja y el cuadre): cada intento ocupa un hilo del
# executor por defecto de asyncio, el mismo de los demás `to_thread` del backend.
_odoo_cache: dict[str, Any] = {"t": 0.0, "v": None, "leido": None, "desde": None}
_odoo_freno: dict[str, Any] = {"motivo": None, "t": 0.0}
_odoo_lock = threading.Lock()
_odoo_leyendo = threading.Lock()


def _cubre(c: dict[str, Any], desde: str) -> bool:
    return c.get("v") is not None and bool(c.get("desde")) and c["desde"] <= desde


def _servir(c: dict[str, Any], ahora: float, desde: str, error: str | None = None) -> dict[str, Any] | None:
    """La lectura de la caché si cubre `desde` (con `error`, solo si tiene menos de
    `ODOO_VIEJA_MAX`); si no, None."""
    if not _cubre(c, desde):
        return None
    if error and ahora - c["t"] >= ODOO_VIEJA_MAX:
        return None
    return {"ok": True, "recepciones": c["v"], "leido": c["leido"], "edad_s": int(ahora - c["t"]),
            "error": (f"{error}; se muestra la lectura de las {c['leido'][11:16]}" if error else None)}


def _frenado(ahora: float) -> str | None:
    """El motivo de la última falla de Odoo si fue hace menos de `ODOO_REINTENTO`."""
    f = _odoo_freno
    return f["motivo"] if f["motivo"] and ahora - f["t"] < ODOO_REINTENTO else None


def _vigente_o_frenado(c: dict[str, Any], desde: str) -> dict[str, Any] | None:
    """Con `_odoo_lock`: la caché vigente, o lo que se sirve mientras dura el freno."""
    ahora = time.monotonic()
    if _cubre(c, desde) and ahora - c["t"] < ODOO_TTL:
        return _servir(c, ahora, desde)
    motivo = _frenado(ahora)
    if motivo:
        return _servir(c, ahora, desde, motivo) or {"ok": False, "error": motivo, "leido": None}
    return None


def leer_odoo(desde: str) -> dict[str, Any]:
    """Las recepciones de devolución validadas desde `desde` (hora local), con caché
    de 10 min.

    Si Odoo falla, sirve la última lectura buena de hasta `ODOO_VIEJA_MAX` que cubra
    `desde` (con el error) y no vuelve a Odoo antes de `ODOO_REINTENTO`; sin ninguna,
    `ok: False`. Si otra petición está releyendo, espera a lo más `ODOO_ESPERA`."""
    c = _odoo_cache
    with _odoo_lock:
        r = _vigente_o_frenado(c, desde)
        if r is not None:
            return r
    if not _odoo_leyendo.acquire(timeout=ODOO_ESPERA):
        motivo = "Odoo se está leyendo para otra petición"
        with _odoo_lock:
            return (_servir(c, time.monotonic(), desde, motivo)
                    or {"ok": False, "error": f"{motivo}; se reintenta en un minuto", "leido": None})
    try:
        with _odoo_lock:                         # otro hilo pudo releer (o fallar) mientras se esperaba
            r = _vigente_o_frenado(c, desde)
            if r is not None:
                return r
            # Se relee también lo que se pidió hace poco: la lectura de 60 días sirve a la de 14.
            reciente = c["desde"] and time.monotonic() - c["t"] < ODOO_VIEJA_MAX
            desde_leer = min(desde, c["desde"]) if reciente else desde
        inicio = time.monotonic()
        leido = _ahora()
        try:
            recs = recepciones_de(leer_odoo_crudo(_utc(desde_leer), _kw_con_plazo(ODOO_PLAZO, ODOO_TIMEOUT)))
        except Exception as exc:  # noqa: BLE001 — Odoo caído no tumba la pestaña
            motivo = f"Odoo no contestó ({str(exc)[:160]})"
            log.warning("fanout_devoluciones: %s", motivo)
            with _odoo_lock:
                _odoo_freno.update(motivo=motivo, t=time.monotonic())
                return _servir(c, time.monotonic(), desde, motivo) or {"ok": False, "error": motivo, "leido": None}
        with _odoo_lock:
            c.update(t=inicio, v=recs, leido=leido, desde=desde_leer)
            _odoo_freno.update(motivo=None, t=0.0)
            return _servir(c, time.monotonic(), desde)
    finally:
        _odoo_leyendo.release()


_sku_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}


def leer_odoo_sku(sku: str, dias: int) -> dict[str, Any]:
    """Las entradas de UN SKU (caché de 2 min). Nunca lanza: `ok: False` si Odoo falla
    o si falló hace menos de `ODOO_REINTENTO` (el freno es el de la bandeja, y aquí se
    anota la falla para ella). Plazo total `ODOO_PLAZO_SKU`: alguien está esperando."""
    clave = (sku.upper(), dias)
    with _odoo_lock:
        c = _sku_cache.get(clave)
        if c and time.monotonic() - c[0] < _SKU_TTL:
            return c[1]
        motivo = _frenado(time.monotonic())
        if motivo:
            return {"ok": False, "entradas": [], "error": f"{motivo}; se reintenta en un minuto"}
    try:
        crudo = leer_odoo_sku_crudo(sku, _utc(_menos(_ahora(), dias=dias + 1)),
                                    _kw_con_plazo(ODOO_PLAZO_SKU, ODOO_TIMEOUT_SKU))
        v = {"ok": True, "entradas": entradas_sku_de(crudo), "error": None,
             "nombre": next((p.get("name") for p in crudo.get("productos") or [] if p.get("name")), "")}
    except Exception as exc:  # noqa: BLE001 — Odoo caído no tumba el cuadre
        motivo = f"Odoo no contestó ({str(exc)[:160]})"
        log.warning("fanout_devoluciones: %s · %s", sku, motivo)
        with _odoo_lock:
            _odoo_freno.update(motivo=motivo, t=time.monotonic())
        return {"ok": False, "entradas": [], "error": motivo}
    with _odoo_lock:
        _odoo_freno.update(motivo=None, t=0.0)
        if len(_sku_cache) >= _SKU_MAX:
            _sku_cache.pop(min(_sku_cache, key=lambda k: _sku_cache[k][0]))
        _sku_cache[clave] = (time.monotonic(), v)
    return v


# ── Lo que llaman las rutas (BLOQUEA: va en un hilo) ─────────────────────────

CUENTAS = ("BEKURA", "SANCORFASHION")


def bandeja(dias: int = 60, cuenta: str | None = None) -> dict[str, Any]:
    """`GET /api/fanout/devoluciones`. Solo lee."""
    from config import settings
    dias = max(1, min(int(dias or 60), 90))
    cuenta = (cuenta or "").strip().upper() or None
    if cuenta and cuenta not in CUENTAS:
        raise ValueError(f"cuenta desconocida: {cuenta}")
    ahora = _ahora()
    devs = _leer_devoluciones(dias + _KUBERA_EXTRA_D)
    odoo = leer_odoo(desde_odoo(devs, ahora, dias))
    desde = _menos(ahora, dias=dias)
    skus = sorted({str(d.get("sku") or "") for d in devs
                   if d.get("destino") == "seller_address" and (d.get("abierta") or "") >= _menos(desde, dias=30)})
    bitacora = sdb.fetch_all(_SQL_SUBIDAS, {"s": skus, "d": dias + 1, "z": ZONA}) if skus else []
    liga = max(0, min(int(settings.fanout_devol_liga_dias), 30))
    return armar_bandeja(devs, odoo, bitacora, ahora, dias, cuenta, liga)


def detalle_sku(sku: str, dias: int = 14) -> dict[str, Any]:
    """`GET /api/fanout/devoluciones/sku/{sku}`. Solo lee."""
    sku = (sku or "").strip()
    dias = max(1, min(int(dias or 14), 60))
    ahora = _ahora()
    bitacora = sdb.fetch_all(_SQL_BITACORA, {"s": sku, "d": dias + 1, "z": ZONA})
    devs = _leer_devoluciones(max(dias, _PENDIENTES_MIN_D) + _KUBERA_EXTRA_D)
    odoo = leer_odoo_sku(sku, max(dias, _PENDIENTES_MIN_D))
    titulo = (sdb.fetch_one(_SQL_TITULO, {"s": sku}) or {}).get("name") or odoo.get("nombre") or ""
    pasada = (sdb.fetch_one(_SQL_PASADA, {"z": ZONA}) or {}).get("ultima")
    return armar_detalle(sku, dias, bitacora, odoo, devs, ahora, titulo, pasada)
