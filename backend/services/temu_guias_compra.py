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
compra UN GRUPO a la vez y sólo con la huella de una vista previa. Ningún
endpoint la invoca con una aprobación humana; la única que la llama es la
COMPRA AUTOMÁTICA (`services/temu_guias_auto.py`, detrás además de
`TEMU_COMPRA_GUIAS_AUTO=false`), que se aprueba a sí misma sólo lo que este
planeador da por comprable, desde el corte de fecha y dentro de sus topes.
Lo que corre hoy es `plan_guias()`: SOLO LECTURAS.

LA FECHA DEL ENVÍO (día de entregar el paquete al repartidor)
─────────────────────────────────────────────────────────────
Regla de Brandon: día de compra + 2 días naturales; si cae en sábado, domingo
o día festivo, se recorre al siguiente día hábil. Lunes a viernes "hasta nuevo
aviso". Ejemplos suyos: compra mar 29-sep → jue 1-oct; compra sáb 3-oct → lun
5-oct. Zona: America/Mexico_City (sin horario de verano desde 2022).

NUNCA DESPUÉS DEL LÍMITE DE ENVÍO DE TEMU (Brandon, 30-sep): si +2 rebasa el
`expectShipLatestTime` de la orden (de un grupo, el más cercano), se usa el
MAYOR plazo permitido que sí lo cumpla y caiga en día hábil. Si ninguno lo
cumple —el límite ya pasó, o ni 24 h cae en día hábil antes de él— la guía SE
COMPRA IGUAL, SIEMPRE, con el plazo MÁS CORTO que caiga en día hábil, y queda
marcada "comprada TARDE (límite de Temu ya vencido)" en la bitácora, el panel
y la nota de Odoo. Sin interruptor (Brandon: "no hagas el nuevo interruptor").
`shipLater=false` NO es "entregar hoy": según la ficha de shipment.create
("apply to ship the package with these tracking numbers and mark this package
as shipped") marca la orden ENVIADA al comprar, sin que la paquetería la haya
escaneado, y el paquete deja de salir en `unshipped.package.get` —de donde el
refresco toma la guía para la orden de Odoo—. No se usa. Si Temu no da el
límite de un PO, también el plazo más corto (`fecha_de_grupo`).

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
    se puede saber de cuál, se resta de LOS DOS (a lo seguro) en vez de dejar
    incierto todo lo que viene después (antes una sola guía rara congelaba la
    cola entera, para siempre);
  · una venta cuyo detalle no se pudo leer resta sus piezas de LOS DOS
    almacenes con los renglones que kubera guardó al registrarla
    (`ops.odoo_sale_order_items`); sólo si tampoco eso se lee, lo que viene
    después queda incierto (y se reintenta la vuelta siguiente);
  · un PO del grupo combinado que YA tiene guía sale del grupo y los demás se
    compran entre sí; uno que Temu agrupa pero no está en la cola se espera 2 h
    (va a entrar) y después se compra sin él;
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
NO está probado por API. Desde el 30-sep entran a la compra automática
(`TEMU_COMPRA_GUIAS_AUTO_SOLO_SIMPLE=false`): la verificación posterior compara
en Odoo cada almacén y cada SKU contra lo que Temu dice que salió de cada caja,
y cualquier diferencia DETIENE el job (`temu_guias_auto.verificar_contra_odoo`).

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

PESO Y CAJA — EL MENOR CREÍBLE entre lo nuestro y lo de Temu (Brandon, 1-oct)
──────────────────────────────────────────────────────────────────────────────
"Tienes un peso declarado en Woo y en Temu, siempre escoge el de menor peso, e
inclusive por las piezas: si Temu con su multiplicación es menor te vas por
Temu, pero internamente antes de decidir se hace una suma para ver cuánto peso
tenemos nosotros, y si es menos le pones el nuestro." Y para la caja: "ocupar
Temu y Woo como para el peso", con la proporcionalidad de la pestaña de Costos
(ahí se manejan cajas master) y el máximo de J&T. Sustituye a la ESCALERA por
orden del 30-sep (`TEMU_GUIAS_FUENTES_MEDIDA` ya sólo dice QUIÉN opina).

  0 · la medida CAPTURADA EN EL PANEL para esa caja manda siempre, sin comparar.

EL PESO (`_empaque_sku`, parte A)
  · NUESTRO por pieza (`lo_nuestro`): el de almacén (`core.products.almacen_*`,
    medición real) si existe; si no, el MENOR creíble entre el packing list
    (`costing.costos_validados`) y Woo (_weight). Creíble = 0.01-30 kg y
    densidad 10-1,500 kg/m³ contra la pieza unitaria YA CORREGIDA (ver abajo).
    NUESTRO(N) = por pieza × N (la "suma interna").
  · TEMU(N) = el menor de: lo declarado en una guía de exactamente (SKU, N);
    la guía de 1 pieza × N (la "multiplicación": es una EXTRAPOLACIÓN de una
    guía a mano, no un dato de Temu para esas piezas — la caja lo dice en su
    aviso, y `TEMU_GUIAS_PESO_GUIA_X1_POR_PIEZAS=false` la quita); y el peso de
    la PUBLICACIÓN (`bg.local.goods.sku.list.query`) × N si no es el relleno
    por omisión (Temu: 100 g y 10×20×30; `publicar_temu`: 500 g y 20×20×20 —
    que rellena CADA CAMPO por separado: un peso de exactamente 0.1 o 0.5 kg
    que no coincide con lo nuestro no opina en el peso, y unas medidas de
    relleno no opinan para la caja).
    ⚠️ Las guías que compró ESTE sistema (bitácora 0061) NO cuentan: son el eco
    de esta misma regla, y con ellas un dato nuestro corregido nunca subiría.
    Se excluyen DENTRO del SQL del historial (`_historial_candidatos`), antes
    de tomar las 3 más nuevas por (SKU, piezas): si se quitaran después, cada
    compra automática desplazaría a una guía a mano y el dato de Temu se
    apagaría solo a media cola (el mismo SKU con dos pesos el mismo día).
  · DECLARADO = el MENOR de los dos, con piso `TEMU_GUIAS_PESO_MIN_KG` (0.10)
    y redondeado HACIA ARRIBA a 2 decimales. Empate → lo nuestro.
  · Sin nada de eso: una guía de OTRA cantidad del mismo SKU (peso/k × N) y
    después las variantes HERMANAS (misma familia `CAT-NNNN`, fuera las
    `FAMILIAS_RECICLADAS`), con confianza baja. Sin nada → compra manual
    ("hay que pesar y medir"); las demás ventas siguen.
  · Caja con VARIOS SKUs: la suma de lo declarado de cada SKU, un solo redondeo;
    si hay una guía a mano con esa MISMA composición, su peso también compite
    (gana el menor; en empate, la suma).

LA CAJA (`_empaque_sku`, parte B)
  · VOLUMEN DE REFERENCIA: `v_ref = costo_cbm / 7500` (m³ por pieza por los que
    se pagó flete; 7500 es `costos.TARIFA_CBM_M3`). Con R = volumen ÷ v_ref:
  · PIEZA UNITARIA de cada fila de catálogo (`unitaria_de_catalogo`): R < 0.67
    no opina; 0.67-3 es la pieza, tal cual; R ≥ 3 es el cartón MASTER y la
    pieza sale con la MISMA función de la pestaña de Costos
    (`packing_costos.dims_pieza`, importada: hasta 10 piezas se divide el lado
    mayor; más de 10, raíz cúbica de los tres) dividiendo entre R. Entre 1.5 y
    3, si la columna `piezas_por_caja` (≥ 2) coincide con R dentro de ± 25 %,
    también es un cartón —de 2 o 3 piezas— y se reparte igual. Sin flete:
    densidad < 50 kg/m³ con `piezas_por_caja` → master ÷ esa columna; < 10 sin
    columna → no opina; lo demás tal cual, con AVISO si su densidad es menor de
    50 (ahí caen los cartones master reales). La de almacén es unitaria directa.
    La NUESTRA es UNA por SKU: almacén, o la de menor volumen entre packing y
    Woo (empate dentro de 5 % → packing).
    ⚠️ Si el CATÁLOGO no se pudo leer (costing.costos_validados no contestó),
    Woo y la publicación NO opinan para la caja —sin el flete no se sabe si la
    medida es el cartón master— y las guías de Temu no se usan: la caja queda
    sin medida esa vuelta (falla CERRADO), y la compra automática no compra
    con el plan a medias (`temu_guias_auto.plan_incompleto`).
  · N PIEZAS (`mejor_acomodo`, sustituye a `apilar`): se prueban las rejillas
    (i, j, k) con i·j·k ≥ N y gana la que CABE en J&T y paga menos: clave
    (no cabe, facturable = max(peso, L×A×H/5000), suma de lados, lado mayor).
  · CANDIDATAS DE TEMU (sin el eco): la guía de exactamente (SKU, N) tal cual
    si 0.90 ≤ volumen ÷ (N·v_ref) < 3 —debajo las piezas no caben, arriba es el
    master copiado—; la de 1 pieza como unitaria, acomodada; la publicación
    como unitaria; y la de k > N piezas (la más cercana) tal cual.
  · Entre la nuestra y las de Temu que pasan la cordura gana la MENOR con esa
    misma clave; empate → la nuestra. Sin dato nuestro: sólo Temu; después las
    hermanas (una caja de hermana que NO cabe en J&T no se cotiza: manual).
  · VARIOS SKUs (`envolvente`, sustituye a `crecer`): la mejor caja de cada
    SKU sumadas por la envolvente mínima, contra la guía de esa composición.
  · LÍMITES DE J&T (T&C de J&T México, cláusulas 4.9 y 6.1; config
    `TEMU_GUIAS_JT_*`): 30 kg, 100 cm por lado, 160 cm sumando los tres,
    volumétrico ÷ 5000. Una caja que NO cabe se cotiza igual con su aviso y
    cuántas cajas harían falta: el árbitro es la cotización de Temu (si ofrece
    J&T se compra; si no, la más barata; si nadie la acepta, manual). No se
    parte sola en dos cajas.
  · AVISOS que no bloquean: la caja no entra en el sobre de 60×60×40 cm que la
    propia J&T publica en sus preguntas frecuentes (sus T&C dicen 100/160;
    `TEMU_GUIAS_LADO_AVISO_CM` y `TEMU_GUIAS_LADO_CORTO_AVISO_CM`); se declara
    menos de la MITAD del volumen que el equipo declaraba a mano (J&T puede
    re-medir y cobrar la diferencia); el flete y la columna piezas_por_caja no
    cuadran; las fuentes no coinciden en el peso; el peso se extrapoló de la
    guía de 1 pieza; una medida sin flete con densidad de cartón master; un
    peso por pieza con el que su cartón master pesaría más de 40 kg.
LA CORDURA sigue: ceros, ≤ 30 kg por pieza salvo medición real, ≤ 70 kg y
≤ 300 cm por caja (bloqueo duro, `plausible`), densidad 10-4,000 kg/m³. Odoo
NO es fuente (su peso es basura: 181 kg un plato de bebé). Cada caja dice en
el plan, en el panel y en la bitácora 0061 (columna `reparto`, sin columnas
nuevas) de dónde salió su PESO y su CAJA, R, volumétrico y facturable, y qué
candidatos se descartaron y por qué.

PAQUETERÍA — `TEMU_GUIAS_PAQUETERIA` (por omisión "J&T,*")
──────────────────────────────────────────────────────────
Brandon (1-oct): "solamente selecciona J&T como el repartidor principal
siempre; en caso de que no se encuentre en las opciones, utilizar el más
barato". "paquetería:tipo" separadas por coma y EN ORDEN DE PRIORIDAD: gana el
primer renglón que tenga un canal usable; dentro de ese renglón, el más barato;
empate → el de menos días (`estimatedText`). "*" es cualquiera — y dentro de
"*" J&T va primero (`TEMU_GUIAS_JT_SHIP_COMPANY_ID`, 202398511; con 0 se
apaga), para que una variable vieja en "*" no devuelva "la más barata de todas".
Entre los servicios de J&T gana el más barato (hoy el DROP OFF: MX$32.40 contra
MX$34.90 de la recolección — ese paquete alguien lo LLEVA a un punto); para
fijar el tipo: "J&T:Pickup,J&T,*". Un canal que pide datos extra
(`infoNeeded`), sólo sirve contra entrega o cotiza en otra moneda no se elige:
si eso deja fuera a J&T, o Temu lo manda a `unavailableChannelDtoList`, se usa
la más barata de las demás y el plan, el panel y la bitácora dicen POR QUÉ no
fue J&T (`porque_no_jt`, con el `unavailableReason` de Temu).

NUNCA COMPRAR DOS VECES — la bitácora DURABLE `ops.temu_guias_compras` (0061)
─────────────────────────────────────────────────────────────────────────────
No hay cancelación por API, y Temu compra en ASÍNCRONO (guía 37): mientras la
etiqueta está "en aplicación" sus lecturas pueden no mostrarla. Así que:
  · antes de comprar se RECLAMA el grupo entero en kubera, en una transacción
    (`insert … on conflict … do update … where estado in ('rechazada',
    'no_enviada')`: sólo se toma una fila libre): sin fila, no hay compra. Sin
    la tabla (migración sin aplicar) o con kubera caída, no se compra;
  · la fila nace 'en_curso' y sólo sale de los estados que BLOQUEAN con un
    rechazo DOCUMENTADO de Temu: 'rechazada' con los códigos de validación de
    la ficha (Temu juzgó la venta), 'no_enviada' con los de la pasarela y el
    4000004 de velocidad (no la juzgó). Todo lo demás —4000000, timeout,
    código desconocido, cancelación de la tarea, reinicio del contenedor—
    deja la orden bloqueada hasta conciliarla (`conciliar()`, que mira Temu);
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
import copy
import hashlib
import itertools
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
# La proporcionalidad de la pestaña de COSTOS, importada (no copiada): cómo se
# reparte el cartón master entre sus piezas, y la tarifa con que se cobra el
# flete por volumen (de ahí sale el volumen de referencia por pieza).
from services.costos import TARIFA_CBM_M3 as TARIFA_CBM
from services.packing_costos import dims_pieza

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
_PAQUETERIA_OMISION = "J&T,*"
_JT_SHIP_COMPANY_ID_OMISION = 202398511

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
# ronda 2,300. CALIBRADO con el catálogo real del 30-sep (medidas por pieza de
# omnicanal): PASAN JUGU-0036-MUL 1.885 kg en 52×42×35 (24.7 kg/m³),
# JUGU-0248-MUL 0.943 kg en 45×33×22 (28.9) y MASC-0019-MUL 2.16 kg en
# 45×43×40 (27.9, historial); se RECHAZAN MASC-0016-NEG-M 0.099 kg en 60×40×40
# (1.0: es la caja MASTER, no la pieza) y ACC-0574-LIL 0.704 kg en 57×43×43
# (6.7).
DENSIDAD_MIN = 10.0        # kg/m³
DENSIDAD_MAX = 4000.0      # kg/m³
# Un dato de CATÁLOGO (packing list, Woo, la publicación) por pieza más denso
# que esto es, casi siempre, el peso de la caja MASTER con las medidas de UNA
# pieza (revisión del 30-sep): el mismo umbral de 1.5 kg/L con que
# `scripts/radar_estado_contenedores.py` reconoce ese error en
# costing.costos_validados. Declararlo sería pagar de más por cada pieza; la
# medición real (almacén, panel) conserva el tope de 4,000.
DENSIDAD_MAX_CATALOGO = 1500.0   # kg/m³
PESO_MIN_KG = 0.01
# Por CAJA, los mismos topes que la captura del panel (`leer_medidas`): nada de
# cajas de 3 m. Y POR PIEZA, 30 kg salvo que la fuente sea una medición real
# (almacén o panel): el peso de Odoo dice 181 kg por un plato de bebé, y un
# dato así en cualquier catálogo no se compra.
PESO_MAX_KG = 70.0
PESO_MAX_PIEZA_KG = 30.0
LADO_MAX_CM = 300.0
TOLERANCIA_CATALOGO_CM = 0.5

# ── La pieza contra el flete (1-oct) ────────────────────────────────────────
# R = volumen de la fila ÷ volumen por el que se pagó flete (costo_cbm / 7500).
R_MIN = 0.67        # debajo, la pieza no cabe en esa medida: la fuente no opina
R_PIEZA = 1.5       # hasta aquí, confianza media; hasta R_MASTER, baja
R_MASTER = 3.0      # de aquí para arriba es el cartón MASTER, no la pieza
PISO_TEMU = 0.90    # una guía de Temu con menos volumen que sus piezas: no caben
# Sin flete con qué probarla: una fila con menos densidad que esto y con
# `piezas_por_caja` es el cartón master.
DENSIDAD_MASTER = 50.0   # kg/m³
# Un peso POR PIEZA de una fila master con el que su cartón (peso × piezas por
# cartón) pasaría de esto: quizá es el peso del CARTÓN. Sólo aviso — medido el
# 1-oct en costing.costos_validados: de 1,966 filas master creíbles, 130 pasan
# de 40 kg, y entre ellas hay pesos reales (mesas, piezas chicas en cartones
# grandes) junto a los del cartón (calzado de 17 kg): no se puede rechazar.
PESO_CARTON_AVISO_KG = 40.0
# Con el catálogo ilegible, una medida de catálogo no opina para la caja.
_SIN_CATALOGO = ("no se pudo leer el catálogo (costing.costos_validados no contestó): sin el flete no "
                 "se sabe si esa medida es la pieza o el cartón MASTER — no opina para la caja")

# ── La escalera de fuentes de peso y caja (Brandon, 30-sep) ─────────────────
# id → cómo se dice en el panel. El orden vive en `TEMU_GUIAS_FUENTES_MEDIDA`.
FUENTES_MEDIDA: dict[str, str] = {
    "almacen": "omnicanal · Checklist de almacén",
    "historial_temu": "Temu · guías del mismo SKU",
    "omnicanal_packing": "omnicanal · packing list",
    "omnicanal_woo": "omnicanal · Woo",
    "temu_ultima_guia": "Temu · guías del mismo SKU",
    "temu_hermanas": "Temu · variante hermana",
    "temu_publicacion": "Temu · publicación",
}
# Todo lo que puede salir como fuente del PESO o de la CAJA de un empaque: los
# ids de la variable (que desde el 1-oct sólo dicen QUIÉN opina, no en qué
# orden) y los de cada candidato de Temu, la captura del panel y la suma de una
# caja con varios SKUs.
FUENTES_TXT: dict[str, str] = {
    "manual": "panel · medida capturada",
    **FUENTES_MEDIDA,
    "temu_guia": "Temu · guía de esas piezas",
    "temu_guia_x1": "Temu · guía de 1 pieza × piezas",
    "temu_guia_otra": "Temu · guía de otra cantidad",
    "suma_skus": "suma de SKUs",
}
FUENTES_OMISION: tuple[str, ...] = tuple(FUENTES_MEDIDA)
# La de antes del 30-sep: sólo medición de almacén e historial con ≥ N muestras.
FUENTES_ESTRICTAS: tuple[str, ...] = ("almacen", "historial_temu")
_ALIAS_FUENTE = {"checklist": "almacen", "historial": "historial_temu",
                 "packing": "omnicanal_packing", "packing_list": "omnicanal_packing",
                 "costos_validados": "omnicanal_packing", "woo": "omnicanal_woo",
                 "woocommerce": "omnicanal_woo", "ultima_guia": "temu_ultima_guia",
                 "temu_guia": "temu_ultima_guia", "hermanas": "temu_hermanas",
                 "hermana": "temu_hermanas", "familia": "temu_hermanas",
                 "publicacion": "temu_publicacion"}
# Las fuentes que son una MEDICIÓN real (una persona pesó y midió): no llevan
# el tope de 30 kg por pieza.
FUENTES_MEDIDAS_REALES = frozenset({"manual", "almacen"})
# Las que son un dato de CATÁLOGO por pieza: llevan además el tope de densidad
# de catálogo (`DENSIDAD_MAX_CATALOGO`).
FUENTES_CATALOGO = frozenset({"omnicanal_packing", "omnicanal_woo", "temu_publicacion"})
# Familias con SKUs RECICLADOS (CLAUDE.md, pendiente 7: el mismo número de
# producto es OTRO producto en otra variante o cuenta — EST-0091 es una cómoda
# y una repisa). Sus "hermanas" no son referencia de peso ni de caja.
FAMILIAS_RECICLADAS = frozenset({"ORG-0579", "EST-0091", "TEC-0492", "ORG-0398", "ORG-0934",
                                 "MAN-0490", "ACC-0653"})
_CONFIANZA_NIVEL = {"ninguna": 0, "baja": 1, "media": 2, "alta": 3}

# Lo que Temu (y `publicar_temu`) ponen cuando la publicación no trae paquete:
# no es una medida, es relleno. (peso kg, lados ordenados en cm)
_RELLENOS_PUBLICACION = ((0.1, (10.0, 20.0, 30.0)), (0.5, (20.0, 20.0, 20.0)))
_PUB_CACHE: dict[int, tuple[float, dict[str, Any] | None]] = {}
_PUB_TTL = 6 * 3600.0

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
#
# ⚠️ FUERA A PROPÓSITO LOS 5xxxxxx (revisión del 30-sep, antes de encender la
# compra AUTOMÁTICA): "5000000 system error" y compañía suenan a que el
# servidor tropezó, no a que la pasarela rechazó antes de llegar al negocio —
# nada documenta que ocurran ANTES de comprar. Con ellos como "rechazo seguro"
# la fila quedaba libre, el job se liberaba de un clic sin mirar Temu y la
# vuelta siguiente podía volver a comprar la misma guía si las lecturas de
# Temu todavía no la mostraban. Ahora son "desconocido": bloquean hasta
# conciliar contra Temu.
#
# Y la pasarela NO juzga la venta (revisión del 30-sep): con un 3000xxx
# (firma, credenciales, permisos, IP) el problema es del sistema, así que la
# fila queda 'no_enviada' —libre, NO un "Temu rechazó esta venta"— y el job se
# detiene; con 4000004 (demasiadas peticiones) es pasajero: 'no_enviada', sin
# detener, y la venta se reintenta la vuelta siguiente. Antes los dos eran
# 'rechazada': la venta quedaba excluida para siempre de la compra automática.
_RECHAZO_PASARELA = frozenset({
    "3000000", "3000001", "3000002", "3000003", "3000004", "3000010", "3000011",
    "3000012", "3000013", "3000014", "3000019", "3000020", "3000021", "3000022",
    "3000025", "3000026", "3000027", "3000028", "3000030", "3000031", "3000032",
    "3000033", "3000034", "3000040"})
LIMITE_VELOCIDAD = frozenset({"4000004"})
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
# Lo que `clasificar_error` da cuando Temu NO compró, seguro: la fila queda
# libre ('rechazada' o 'no_enviada') y la venta no va a `_DESCONOCIDAS`.
NO_COMPRO = frozenset({"rechazada", "pasarela", "limite_velocidad"})


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
    """[(paquetería, tipo)] EN ORDEN DE PRIORIDAD: "J&T", "J&T:Pickup",
    "*:Pickup", "*". Por omisión "J&T,*" (Brandon, 1-oct)."""
    texto = _cfg("temu_guias_paqueteria", _PAQUETERIA_OMISION) if crudo is None else crudo
    salida = []
    for par in (texto or "").split(","):
        emp, _sep, tipo = par.strip().partition(":")
        if emp.strip():
            salida.append((emp.strip(), tipo.strip()))
    return salida


def _min_muestras() -> int:
    return max(1, int(getattr(settings, "temu_guias_empaque_min_muestras", 2) or 2))


def _cfg_num(nombre: str, omision: float) -> float:
    """Un número POSITIVO de la configuración; ilegible, cero o negativo → el de
    fábrica (un límite en 0 dejaría pasar todo, o nada)."""
    try:
        v = getattr(settings, nombre, None)
        f = float(omision if v is None else v)
    except (TypeError, ValueError):
        return float(omision)
    return f if (f > 0 and not math.isnan(f) and not math.isinf(f)) else float(omision)


def limites_jt() -> dict[str, float]:
    """Los LÍMITES DE J&T Express México (sus T&C, cláusulas 4.9 y 6.1; los
    mismos que TikTok Shop MX publica para J&T e iMile): 30 kg, ningún lado de
    más de 100 cm, 160 cm sumando los tres, y volumétrico = L×A×H ÷ 5000 (cobra
    el mayor entre real y volumétrico). `aviso` y `aviso_corto` (60 y 40 cm)
    son sólo campana: el sobre de 60×60×40 que la PROPIA J&T publica en las
    preguntas frecuentes de su sitio ("envíos de hasta 30 kg con medidas
    máximas de 60 cm de largo × 60 cm de ancho × 40 cm de alto"), más estricto
    que sus T&C. Variables `TEMU_GUIAS_JT_*` y `TEMU_GUIAS_LADO_*_AVISO_CM`."""
    return {"peso": _cfg_num("temu_guias_jt_peso_max_kg", 30.0),
            "lado": _cfg_num("temu_guias_jt_lado_max_cm", 100.0),
            "suma": _cfg_num("temu_guias_jt_suma_lados_max_cm", 160.0),
            "divisor": _cfg_num("temu_guias_divisor_volumetrico", 5000.0),
            "aviso": _cfg_num("temu_guias_lado_aviso_cm", 60.0),
            "aviso_corto": _cfg_num("temu_guias_lado_corto_aviso_cm", 40.0)}


def piso_peso() -> float:
    """El piso del peso DECLARADO por caja (`TEMU_GUIAS_PESO_MIN_KG`, 0.10 kg)."""
    return max(PESO_MIN_KG, _cfg_num("temu_guias_peso_min_kg", 0.10))


def guia_x1_por_piezas() -> bool:
    """`TEMU_GUIAS_PESO_GUIA_X1_POR_PIEZAS` (nace ENCENDIDA: la especificación
    del 1-oct): ¿la guía de 1 pieza × piezas cuenta como "la multiplicación de
    Temu" en el peso? Es una EXTRAPOLACIÓN de una guía comprada a mano, no un
    dato de Temu para esas piezas (las guías a mano de JUGU-0089-PLA × 2/3/4
    declararon 5.40/8.10/10.80 = el peso de Woo × piezas, y la de × 1, 1.00).
    Apagada (la lectura literal de Brandon): Temu(N) = la guía de exactamente N
    piezas o la publicación × N, y la guía de 1 pieza sólo sirve de respaldo."""
    return bool(getattr(settings, "temu_guias_peso_guia_x1_por_piezas", True))


def jt_ship_company_id() -> int:
    """El `shipCompanyId` de J&T en Temu (`TEMU_GUIAS_JT_SHIP_COMPANY_ID`,
    202398511 en la bitácora de compras). 0 = sin prioridad de J&T dentro de
    "*" (los renglones que nombran a J&T siguen sirviendo, por nombre)."""
    v = getattr(settings, "temu_guias_jt_ship_company_id", _JT_SHIP_COMPANY_ID_OMISION)
    try:
        n = int(_JT_SHIP_COMPANY_ID_OMISION if v is None else v)
    except (TypeError, ValueError):
        return _JT_SHIP_COMPANY_ID_OMISION
    return n if n > 0 else 0


def texto_fuentes(orden: Iterable[str]) -> list[str]:
    """Quién opina, en palabras y sin repetir (para el panel). PURA."""
    vistos: list[str] = []
    for f in orden:
        t = FUENTES_TXT.get(f, f)
        if t not in vistos:
            vistos.append(t)
    return vistos


def leer_fuentes_medida(crudo: str | None = None) -> tuple[str, ...]:
    """Las fuentes de peso y caja que PARTICIPAN (desde el 1-oct el orden ya no
    manda: gana el menor creíble). ⚠️ LANZA ValueError si está mal escrita,
    vacía, repite un escalón o nombra a Odoo."""
    texto = (_cfg("temu_guias_fuentes_medida", ",".join(FUENTES_OMISION))
             if crudo is None else crudo)
    salida: list[str] = []
    for t in re.split(r"[,\s;>]+", str(texto or "").strip().lower()):
        if not t:
            continue
        if t.startswith("odoo"):
            raise ValueError(f"{t!r}: Odoo NO es fuente de peso (su ficha dice 181 kg por un plato "
                             "de bebé)")
        n = _ALIAS_FUENTE.get(t, t)
        if n not in FUENTES_MEDIDA:
            raise ValueError(f"escalón desconocido {t!r} (válidos: {', '.join(FUENTES_MEDIDA)})")
        if n in salida:
            raise ValueError(f"escalón repetido {t!r}")
        salida.append(n)
    if not salida:
        raise ValueError("vacía")
    return tuple(salida)


def fuentes_medida() -> tuple[tuple[str, ...], str | None]:
    """(escalera vigente, error). Mal escrita → la ESTRICTA, con el error para
    la vista previa: falla cerrado hacia lo que ya se compraba antes."""
    try:
        return leer_fuentes_medida(), None
    except ValueError as exc:
        return FUENTES_ESTRICTAS, (f"TEMU_GUIAS_FUENTES_MEDIDA mal escrita ({exc}): se usa la "
                                   f"escalera ESTRICTA ({', '.join(FUENTES_ESTRICTAS)})")


_FAMILIA = re.compile(r"^([A-Z]+-\d+)-[A-Z0-9].*$")


def familia(sku: Any) -> str | None:
    """La FAMILIA de un SKU: sus dos primeros segmentos cuando el segundo es el
    número de producto — MASC-0016-NEG-M → MASC-0016, DEC-0015-ROJ →
    DEC-0015. Sin variante (LIB-0001) o con otra forma → None: no tiene
    hermanas. PURA."""
    s = str(sku or "").strip().upper()
    if not s or "+" in s or "*" in s:
        return None
    m = _FAMILIA.match(s)
    return m.group(1) if m else None


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


# Colchón contra el límite de envío de Temu: la fecha elegida tiene que vencer
# al menos esto ANTES del límite. Cubre los minutos entre la vista previa y la
# compra (que igual se re-verifica justo antes) y el reloj de Temu, que cuenta
# las horas desde que la compra TERMINA (es asíncrona).
MARGEN_LIMITE_S = 30 * 60


def sabado_alterno() -> bool:
    """¿El plazo ALTERNO (el adelantado por el límite de Temu, o el más corto
    de una venta que va tarde) puede caer en SÁBADO? `TEMU_GUIAS_SABADO_ALTERNO`,
    nace apagada (sábado = inhábil, como la regla de siempre). Con el límite de
    Temu a ~48 h de la venta, una venta de viernes no alcanza su límite en día
    hábil: se compra TARDE con entrega el lunes; encendida, el sábado."""
    return bool(getattr(settings, "temu_guias_sabado_alterno", False))


# El texto con que se marca una guía comprada después del límite de Temu: la
# bitácora (`reparto`), el panel, la nota de la orden de Odoo y el Excel.
TEXTO_TARDE = "comprada TARDE (límite de Temu ya vencido)"


def _es_habil(dia: date, fest: frozenset[date], sab: bool) -> bool:
    return not (dia.weekday() == 6 or (dia.weekday() == 5 and not sab) or dia in fest)


def _con_plazo(out: dict[str, Any], local: datetime, h: int, dia: date,
               **extra: Any) -> dict[str, Any]:
    vence = local + timedelta(hours=h)
    return {**out, "valida": True, "motivos": [], "fecha_envio": dia.isoformat(),
            "dia_envio": _DIAS[dia.weekday()], "dias_naturales": h // 24, "horas": h,
            "ship_later_limit_time": str(h), "vence_local": vence.isoformat(timespec="minutes"),
            "vence_ts": int(vence.timestamp()), "saltados": [], **extra}


def _plazo_corto(out: dict[str, Any], local: datetime, ok_horas: Iterable[int],
                 fest: frozenset[date], sab: bool, **extra: Any) -> dict[str, Any]:
    """El plazo MÁS CORTO de `TEMU_GUIAS_HORAS_VALIDAS` que cae en día hábil
    (lunes a viernes, no festivo; el sábado sólo con `TEMU_GUIAS_SABADO_ALTERNO`).
    Con 24-96 h y los festivos de México siempre hay uno (vie → lun a 72 h, jue
    antes de un viernes festivo → lun a 96 h); si no lo hubiera, el más corto
    de todos, diciéndolo — nunca "no se compra". La lista de festivos vencida
    SÍ falla cerrado: no se sabe si ese día es festivo. PURA."""
    horas = sorted(h for h in ok_horas if h > 0 and not h % 24)
    if not horas:
        return {**out, "valida": False, "urgente": False,
                "motivos": ["TEMU_GUIAS_HORAS_VALIDAS no trae plazos de días completos"]}
    for h in horas:
        dia = local.date() + timedelta(days=h // 24)
        if not fest or not any(f >= dia for f in fest):
            return {**out, "valida": False, "urgente": False,
                    "motivos": [f"la lista de festivos termina antes del {dia.isoformat()}: agrega "
                                "los del año siguiente a TEMU_GUIAS_FESTIVOS"]}
        if _es_habil(dia, fest, sab):
            return _con_plazo(out, local, h, dia, **extra)
    h = horas[0]
    dia = local.date() + timedelta(days=h // 24)
    return _con_plazo(out, local, h, dia, inhabil=True, **extra)


def _texto_plazo(r: dict[str, Any]) -> str:
    txt = f"{r['dia_envio']} {r['fecha_envio']} ({r['horas']} h)"
    return txt + (" — ningún plazo permitido cae en día hábil" if r.get("inhabil") else "")


def fecha_con_limite(compra: datetime, limite_ts: int | None, *,
                     festivos: Iterable[date] | None = None, dias: int | None = None,
                     validas: Iterable[int] | None = None,
                     margen_s: int = MARGEN_LIMITE_S,
                     sabado: bool | None = None) -> dict[str, Any]:
    """
    La fecha de envío con el LÍMITE DE ENVÍO de Temu encima
    (`expectShipLatestTime` de la orden; de un grupo, el MÁS CERCANO). PURA
    (salvo `sabado=None`, que lee `TEMU_GUIAS_SABADO_ALTERNO`).

    Regla de Brandon (30-sep): la fecha nunca después del límite, y ninguna
    venta se queda sin guía.
      · la regla de siempre (+2 naturales → hábil) es un plazo permitido y
        vence antes del límite (menos `margen_s`) → esa, sin cambios;
      · si no → el MAYOR plazo de `TEMU_GUIAS_HORAS_VALIDAS`, más corto que el
        de la regla, que SÍ lo cumpla y caiga en día hábil (la regla de los
        días hábiles sigue mandando);
      · si ni así (el límite ya pasó, o ningún plazo lo alcanza en día hábil)
        → se compra IGUAL con el plazo MÁS CORTO en día hábil: `valida=True`,
        `tarde=True`, "comprada TARDE (límite de Temu ya vencido)". Sin
        interruptor (Brandon: "no hagas el nuevo interruptor").
    `urgente` ya no sale nunca en True (se conserva la llave para el panel).

    `limite_ts=None` (Temu no lo dio) → la de la regla tal cual, con
    `limite_ts=None`. Para un GRUPO, `fecha_de_grupo` decide.
    """
    sab = sabado_alterno() if sabado is None else bool(sabado)
    base = fecha_envio(compra, festivos=festivos, dias=dias, validas=validas)
    lim = _entero(limite_ts)
    out: dict[str, Any] = {**base, "limite_ts": lim or None, "limite": _hora_mx(lim),
                           "ajustada": False, "urgente": False, "tarde": False,
                           "sin_limite": False, "ajuste": None,
                           "regla_fecha": base["fecha_envio"], "regla_horas": base["horas"]}
    if not lim:
        return out
    tope = int(lim) - int(margen_s)
    if base["valida"] and base["vence_ts"] <= tope:
        return out
    local = compra.astimezone(ZONA) if compra.tzinfo else compra.replace(tzinfo=ZONA)
    try:
        fest = frozenset(festivos) if festivos is not None else leer_festivos()
    except ValueError:
        return out          # `base` ya dice "TEMU_GUIAS_FESTIVOS mal escrito": no se calcula
    ok_horas = tuple(validas) if validas is not None else horas_validas()
    por_regla = (f"rebasa el límite de envío de Temu ({out['limite']})"
                 if base["vence_ts"] > tope else "no es un plazo permitido (TEMU_GUIAS_HORAS_VALIDAS)")
    descartes: list[str] = []
    for h in sorted(ok_horas, reverse=True):
        if h >= int(base["horas"]) or h <= 0 or h % 24:
            continue
        dia = local.date() + timedelta(days=h // 24)
        vence = local + timedelta(hours=h)
        if int(vence.timestamp()) > tope:
            descartes.append(f"{h} h rebasa el límite")
            continue
        if dia.weekday() == 6 or (dia.weekday() == 5 and not sab) or dia in fest:
            descartes.append(f"{h} h cae en {'festivo' if dia in fest else _DIAS[dia.weekday()]} "
                             f"{dia.isoformat()}")
            continue
        if not fest or not any(f >= dia for f in fest):
            return {**out, "valida": False, "urgente": False,
                    "motivos": [f"la lista de festivos termina antes del {dia.isoformat()}: agrega "
                                "los del año siguiente a TEMU_GUIAS_FESTIVOS"]}
        return _con_plazo(out, local, h, dia, ajustada=True,
                          ajuste=(f"la regla daba el {base['fecha_envio']} ({base['horas']} h) y "
                                  f"{por_regla}: se usa el {_DIAS[dia.weekday()]} "
                                  f"{dia.isoformat()} ({h} h)"))
    # Ningún plazo cumple el límite en día hábil: la guía se compra IGUAL con
    # lo más pronto que el almacén puede entregar, y se dice que va tarde.
    ya = int(lim) <= int(local.timestamp())
    detalle = "; ".join(descartes) or "ningún plazo permitido es más corto"
    por = "ya venció" if ya else f"no se alcanza con ningún plazo en día hábil ({detalle})"
    r = _plazo_corto(out, local, ok_horas, fest, sab, ajustada=True, tarde=True)
    if r["valida"]:
        r["ajuste"] = (f"{TEXTO_TARDE}: el límite de envío ({out['limite']}) {por}; se compra IGUAL "
                       f"con el plazo más corto en día hábil: {_texto_plazo(r)}")
    return r


def fecha_corta(compra: datetime, motivo: str, *,
                festivos: Iterable[date] | None = None, dias: int | None = None,
                validas: Iterable[int] | None = None,
                sabado: bool | None = None) -> dict[str, Any]:
    """El plazo MÁS CORTO en día hábil, cuando Temu no dio el límite de envío
    de alguna orden del grupo (`sin_limite=True`): sin límite no se sabe
    cuánto se puede esperar, y entregar lo antes posible nunca lo rebasa. PURA
    (salvo `sabado=None`)."""
    sab = bool(sabado) if sabado is not None else sabado_alterno()
    base = fecha_envio(compra, festivos=festivos, dias=dias, validas=validas)
    out: dict[str, Any] = {**base, "limite_ts": None, "limite": None, "ajustada": False,
                           "urgente": False, "tarde": False, "sin_limite": True, "ajuste": None,
                           "regla_fecha": base["fecha_envio"], "regla_horas": base["horas"]}
    local = compra.astimezone(ZONA) if compra.tzinfo else compra.replace(tzinfo=ZONA)
    try:
        fest = frozenset(festivos) if festivos is not None else leer_festivos()
    except ValueError:
        return out
    ok_horas = tuple(validas) if validas is not None else horas_validas()
    r = _plazo_corto(out, local, ok_horas, fest, sab, ajustada=True)
    if r["valida"]:
        r["ajuste"] = f"{motivo}: se usa el plazo más corto en día hábil, {_texto_plazo(r)}"
    return r


def fecha_de_grupo(compra: datetime, limites: dict[str, Any], **kw: Any) -> dict[str, Any]:
    """La fecha de un GRUPO (todas sus cajas comparten fecha): con el límite de
    envío MÁS CERCANO de sus órdenes (`fecha_con_limite`); si Temu no dio el de
    alguna, el plazo más corto en día hábil (`fecha_corta`) — antes eso era
    "compra manual". `limites` = {PO: expectShipLatestTime o None}. PURA.

    Sin el límite de una y CON el de otras (revisión del 30-sep): el plazo más
    corto se compara además contra el más cercano de los conocidos; si ni así lo
    alcanza (ya venció, o cae después), la guía va TARDE —se compra igual y se
    dice— en vez de que la otra orden la frene como "riesgo de retraso"."""
    lims = {str(po): _entero(x) for po, x in (limites or {}).items()}
    sin = sorted(po for po, x in lims.items() if not x)
    con = [x for x in lims.values() if x]
    if sin:
        margen = int(kw.pop("margen_s", MARGEN_LIMITE_S))
        r = fecha_corta(compra, f"Temu no dio el límite de envío de {', '.join(sin)}", **kw)
        if con and r.get("valida"):
            lim = min(con)
            r["limite_ts"], r["limite"] = lim, _hora_mx(lim)
            if int(r["vence_ts"]) > lim - margen:
                local = compra.astimezone(ZONA) if compra.tzinfo else compra.replace(tzinfo=ZONA)
                por = ("ya venció" if lim <= int(local.timestamp())
                       else "no se alcanza ni con el plazo más corto en día hábil")
                r["tarde"] = True
                r["ajuste"] = (f"{TEXTO_TARDE}: el límite de envío ({r['limite']}) {por} y Temu no "
                               f"dio el de {', '.join(sin)}; se compra IGUAL con el plazo más corto "
                               f"en día hábil: {_texto_plazo(r)}")
        return r
    return fecha_con_limite(compra, min(con) if con else None, **kw)


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
    # CUÁNDO SE VENDIÓ, según Temu (la misma precedencia que `pedidos_temu.
    # _normalizar`). Es lo que decide el corte de la compra automática
    # (`TEMU_COMPRA_GUIAS_DESDE`): la fila de kubera dice cuándo la PROCESAMOS,
    # y una venta recuperada días después pasaría el corte sin serlo.
    vendida = _entero(pm.get("parentOrderTime")) or _entero(pm.get("parentConfirmTime"))
    return {"parent_order_sn": sn, "estado": estado,
            "estado_txt": ESTADOS_TEMU.get(estado or -1, "?"),
            "venta_ts": vendida or None, "venta": _hora_mx(vendida),
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
    """None si la CAJA es físicamente creíble; si no, POR QUÉ. PURA. Es LA
    CORDURA de todos los escalones automáticos (con `cordura_pieza` para el
    dato por pieza): lo que no pasa, no se compra — salta al escalón
    siguiente."""
    if not (peso and largo and ancho and alto) or min(peso, largo, ancho, alto) <= 0:
        return "faltan peso o medidas"
    if peso < PESO_MIN_KG:
        return f"pesa {peso:.3f} kg (menos de 10 g): no es un paquete real"
    if peso > PESO_MAX_KG:
        return f"pesa {peso:,.2f} kg (más de {PESO_MAX_KG:.0f}): no es un peso posible para esta guía"
    if max(largo, ancho, alto) > LADO_MAX_CM:
        return (f"un lado mide {max(largo, ancho, alto):,.1f} cm (más de {LADO_MAX_CM:.0f}): no es "
                "una caja posible")
    vol_m3 = largo * ancho * alto / 1_000_000.0
    densidad = peso / vol_m3
    if densidad < DENSIDAD_MIN:
        return (f"densidad de {densidad:.1f} kg/m³ (mínimo {DENSIDAD_MIN:.0f}): caja enorme para "
                "su peso — así se ven las medidas de catálogo, y Temu cobraría por volumen")
    if densidad > DENSIDAD_MAX:
        return (f"densidad de {densidad:,.0f} kg/m³ (máximo {DENSIDAD_MAX:,.0f}): caja chica "
                "para su peso — la paquetería ajustaría el cobro")
    return None


def cordura_pieza(m: dict[str, Any] | None, real: bool = False,
                  catalogo: bool = False) -> str | None:
    """La cordura de UNA pieza (un catálogo, o una guía dividida entre sus
    piezas): la de la caja (`plausible`) y, salvo que la fuente sea una
    medición real (almacén o panel), ≤ 30 kg. `catalogo=True` (packing list,
    Woo, publicación) exige además ≤ `DENSIDAD_MAX_CATALOGO`: más denso es el
    peso de la caja master con las medidas de una pieza. None = pasa. PURA."""
    if not m:
        return "faltan peso o medidas"
    mala = plausible(m.get("peso_kg"), m.get("largo_cm"), m.get("ancho_cm"), m.get("alto_cm"))
    if mala:
        return mala
    if not real and float(m["peso_kg"]) > PESO_MAX_PIEZA_KG:
        return (f"pesa {float(m['peso_kg']):,.2f} kg por pieza (más de {PESO_MAX_PIEZA_KG:.0f} sin "
                "una medición real): no se declara")
    if catalogo and not real:
        dens = float(m["peso_kg"]) / (float(m["largo_cm"]) * float(m["ancho_cm"])
                                      * float(m["alto_cm"]) / 1_000_000.0)
        if dens > DENSIDAD_MAX_CATALOGO:
            return (f"densidad de {dens:,.0f} kg/m³ (máximo {DENSIDAD_MAX_CATALOGO:,.0f} para un dato "
                    "de catálogo): parece el peso de la caja MASTER con las medidas de una pieza")
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


def relleno_por_campo(m: dict[str, Any] | None) -> tuple[bool, bool]:
    """(¿el PESO es el de relleno?, ¿las MEDIDAS son las de relleno?) del
    paquete de una publicación: lo que Temu (100 g, 10×20×30) o `publicar_temu`
    (500 g, 20×20×20) ponen cuando la publicación no trae uno. Eso no es una
    medida. CADA CAMPO por separado: `publicar_temu` rellena así (peso por
    omisión 500 g; largo, ancho y alto por omisión 20 cm, cada uno aparte), y
    un producto que en Woo tenía medidas pero no peso quedó publicado con 500 g
    y sus medidas reales — exigir las cuatro cosas juntas no lo reconocía, y
    0.5 kg × piezas le ganaba a todo por ser el menor. PURA."""
    if not m:
        return False, False
    try:
        peso = float(m["peso_kg"])
        lados = tuple(sorted(float(m[k]) for k in ("largo_cm", "ancho_cm", "alto_cm")))
    except (KeyError, TypeError, ValueError):
        return False, False
    return (any(abs(peso - p) < 0.005 for p, _ls in _RELLENOS_PUBLICACION),
            any(all(abs(x - y) < 0.05 for x, y in zip(lados, ls)) for _p, ls in _RELLENOS_PUBLICACION))


def _medida(d: Any) -> dict[str, float] | None:
    """{peso_kg, largo_cm, ancho_cm, alto_cm} positivos, o None si falta uno. PURA."""
    if not isinstance(d, dict):
        return None
    m = {k: _num(d.get(k)) for k in ("peso_kg", "largo_cm", "ancho_cm", "alto_cm")}
    return m if all(m.values()) else None  # type: ignore[return-value]


def _arriba2(x: float) -> float:
    """Redondeo HACIA ARRIBA a 2 decimales (el mismo del payload)."""
    return float(_dos(x))


def _caja_txt(c: Iterable[float]) -> str:
    """"24.13×12.06×25.77" (sin ceros de sobra). PURA."""
    return "×".join(f"{float(x):.2f}".rstrip("0").rstrip(".") for x in c)


def _vol3(c: Iterable[float]) -> float:
    l_, a_, h_ = (float(x) for x in c)
    return l_ * a_ * h_


def medir_caja(peso: float, caja: Iterable[float],
               lim: dict[str, float] | None = None) -> dict[str, Any]:
    """Una caja contra los LÍMITES DE J&T (`limites_jt`: 30 kg, 100 cm por lado,
    160 cm sumando los tres) y lo que la paquetería cobraría: el MAYOR entre el
    peso y el volumétrico (L×A×H ÷ 5000). Es un DATO —`cabe` ordena los
    acomodos y avisa—; el bloqueo duro sigue en `plausible` (70 kg / 300 cm).
    PURA (salvo `lim=None`, que lee la configuración)."""
    lim = lim or limites_jt()
    l_, a_, h_ = (float(x) for x in caja)
    p = float(peso or 0.0)
    suma, mayor = l_ + a_ + h_, max(l_, a_, h_)
    vol_kg = l_ * a_ * h_ / float(lim["divisor"])
    motivos: list[str] = []
    if p > lim["peso"] + 1e-9:
        motivos.append(f"pesa {p:.2f} kg (máximo {lim['peso']:g})")
    if mayor > lim["lado"] + 1e-9:
        motivos.append(f"un lado mide {mayor:g} cm (máximo {lim['lado']:g})")
    if suma > lim["suma"] + 1e-9:
        motivos.append(f"sus tres lados suman {suma:g} cm (máximo {lim['suma']:g})")
    # Cuántos cm se pasa (de lado y de suma): entre dos cajas que NO caben, la
    # que menos se pasa es la que un mostrador aceptaría antes.
    exceso = max(0.0, mayor - lim["lado"]) + max(0.0, suma - lim["suma"])
    return {"cabe": not motivos, "motivos": motivos, "suma_cm": round(suma, 2),
            "lado_max_cm": round(mayor, 2), "volumetrico_kg": round(vol_kg, 2),
            "facturable_kg": round(max(p, vol_kg), 2), "exceso_cm": round(exceso, 2)}


def aviso_sobre(caja: Iterable[float], lim: dict[str, float] | None = None) -> str | None:
    """El aviso (NO bloquea) de una caja que no entra en el sobre de 60×60×40 cm
    que J&T publica en sus preguntas frecuentes: un lado de más de 60, o los
    tres de más de 40 (50×50×50 cabe en los T&C y no en ese sobre). None si
    entra. PURA (salvo `lim=None`, que lee la configuración)."""
    lim = lim or limites_jt()
    c3 = tuple(float(x) for x in caja)
    menor, _medio, mayor = sorted(c3)
    largo, corto = float(lim["aviso"]), float(lim.get("aviso_corto", 40.0))
    sobre = f"{largo:g}×{largo:g}×{corto:g}"
    cola = (f"no entra en el sobre de {sobre} cm que J&T publica en sus preguntas frecuentes — un "
            "mostrador puede rechazar la caja")
    if mayor > largo + 1e-9:
        return f"un lado mide {round(mayor, 2):g} cm (más de {largo:g}): {cola}"
    if menor > corto + 1e-9:
        return f"sus tres lados pasan de {corto:g} cm ({_caja_txt(c3)}): {cola}"
    return None


def orden_caja(peso: float, caja: Iterable[float],
               lim: dict[str, float] | None = None) -> tuple[int, float, float, float, float]:
    """La CLAVE con que se comparan dos cajas (gana la menor): primero la que
    cabe en J&T; entre las que caben, la que paga menos (facturable = el mayor
    entre peso y volumétrico), la de menor suma de lados y la de lado mayor más
    corto. Entre las que NO caben, antes que nada la que menos se pasa: 5 piezas
    de 28×28×33 en fila dan 140 cm de lado, y en 3×2 una caja de 84×56×33 que
    sólo se pasa 13 cm de suma. PURA."""
    m = medir_caja(peso, caja, lim)
    return (0 if m["cabe"] else 1, 0.0 if m["cabe"] else m["exceso_cm"], m["facturable_kg"],
            m["suma_cm"], m["lado_max_cm"])


def mejor_acomodo(unitaria: Iterable[float], n: int, peso: float,
                  lim: dict[str, float] | None = None) -> dict[str, Any]:
    """
    La caja de `n` piezas iguales: se prueban TODAS las rejillas (i, j, k) con
    i·j·k ≥ n y sin capa sobrante —en fila, 2×2, dos pisos…— y gana la de menor
    `orden_caja`: la que cabe en J&T y paga menos. Cada lado es un múltiplo
    entero del lado de la pieza y el volumen nunca es menor que n piezas. PURA.

    Sustituye a `apilar()` (el lado más corto × n): con 28×28×33 × 4 daba
    112×33×28 —no cabe en J&T— cuando 2×2 da 56×56×33, que sí.
    Devuelve {caja, rejilla, cabe, medida}.
    """
    lim = lim or limites_jt()
    l_, a_, h_ = (float(x) for x in unitaria)
    n = max(1, int(n))
    mejor: tuple[tuple[int, float, float, float, float], tuple[float, float, float],
                 tuple[int, int, int]] | None = None
    for i in range(1, n + 1):
        for j in range(1, -(-n // i) + 1):
            k = -(-n // (i * j))
            if (i - 1) * j * k >= n or i * (j - 1) * k >= n:
                continue                  # una fila o una columna entera de sobra
            caja = (round(l_ * i, 2), round(a_ * j, 2), round(h_ * k, 2))
            clave = orden_caja(peso, caja, lim)
            if mejor is None or clave < mejor[0]:
                mejor = (clave, caja, (i, j, k))
    assert mejor is not None
    return {"caja": mejor[1], "rejilla": mejor[2], "cabe": mejor[0][0] == 0,
            "medida": medir_caja(peso, mejor[1], lim)}


def piezas_que_caben(unitaria: Iterable[float], n: int, peso: float,
                     lim: dict[str, float] | None = None) -> int:
    """Cuántas piezas (≤ n) caben en UNA caja que J&T acepte, con el peso
    repartido parejo; 0 si ni una. Con eso se dice "partir en ⌈n/n_max⌉ cajas".
    PURA."""
    u = tuple(float(x) for x in unitaria)
    n = max(1, int(n))
    for k in range(n, 0, -1):
        if mejor_acomodo(u, k, float(peso) * k / n, lim)["cabe"]:
            return k
    return 0


def envolvente(cajas: list[tuple[float, float, float]], peso: float,
               lim: dict[str, float] | None = None) -> tuple[float, float, float]:
    """La caja de una caja con VARIOS SKUs: la mejor caja de cada SKU, de la
    más grande a la más chica, sumadas por la envolvente mínima — cada una se
    prueba en sus 6 giros pegada por cada uno de los 3 ejes (en ese eje se
    suma, en los otros dos manda el mayor) y gana la de menor `orden_caja`.
    Sustituye a `crecer()`. PURA."""
    lim = lim or limites_jt()
    orden = sorted((tuple(float(x) for x in c) for c in cajas), key=lambda c: -_vol3(c))
    acc = orden[0]
    for b in orden[1:]:
        mejor = None
        for giro in sorted(set(itertools.permutations(b))):
            for eje in range(3):
                nueva = tuple(round(acc[x] + giro[x], 2) if x == eje else max(acc[x], giro[x])
                              for x in range(3))
                clave = orden_caja(peso, nueva, lim)
                if mejor is None or clave < mejor[0]:
                    mejor = (clave, nueva)
        acc = mejor[1]  # type: ignore[index]
    return acc  # type: ignore[return-value]


def unitaria_de_catalogo(medida: dict[str, Any] | None, v_ref: float | None = None,
                         piezas_por_caja: Any = None) -> dict[str, Any]:
    """
    La caja de UNA pieza a partir de una fila de catálogo (packing list o Woo),
    que puede traer la pieza… o el cartón MASTER entero (en 4 de los 8 SKUs de
    la cola del 1-oct: se declaraba 52×42×35 por un solo juguete). PURA.

    `v_ref` = m³ por pieza por los que se pagó flete (`costo_cbm / 7500`, la
    tarifa de Costos). Con R = volumen de la fila ÷ v_ref:
      · R < 0.67        → la pieza no cabe en esa medida: la fuente NO OPINA;
      · 0.67 ≤ R < 3    → es la pieza: tal cual (confianza media si R ≤ 1.5)
                          — salvo que, con 1.5 ≤ R < 3, la columna
                          `piezas_por_caja` (≥ 2) coincida con R dentro de
                          ± 25 %: entonces es un cartón de 2 o 3 piezas y se
                          reparte como el master (77 filas el 1-oct; 5 de ellas
                          publicadas en Temu: 50×41×41 por una pieza de 2);
      · R ≥ 3           → es el cartón master: la pieza sale con la MISMA
                          función de Costos (`packing_costos.dims_pieza`: hasta
                          10 piezas se divide el lado mayor, más de 10 la raíz
                          cúbica de los tres) dividiendo entre R.
    Sin `v_ref`: con densidad < 50 kg/m³ y `piezas_por_caja` > 1, master entre
    esa columna; con densidad < 10 y sin columna, no opina (su peso sí sirve);
    lo demás, tal cual con confianza baja — y con AVISO si su densidad es menor
    de 50 kg/m³: ahí caen los cartones master reales (24-29 kg/m³ los de la
    cola del 1-oct) y no hay flete con qué probar que sea la pieza.
    Devuelve {caja | None, confianza, R, master, como, aviso}.
    """
    base: dict[str, Any] = {"caja": None, "confianza": "ninguna", "R": None, "master": False,
                            "como": "", "aviso": None}
    lados = [_num((medida or {}).get(k)) for k in ("largo_cm", "ancho_cm", "alto_cm")]
    if not all(lados):
        return {**base, "como": "sin las tres medidas"}
    l_, a_, h_ = (float(x) for x in lados)  # type: ignore[arg-type]
    vol = l_ * a_ * h_ / 1_000_000.0
    ppc = _num(piezas_por_caja)
    crudo = _caja_txt((l_, a_, h_))
    tal_cual = (round(l_, 2), round(a_, 2), round(h_, 2))
    if v_ref and float(v_ref) > 0:
        r_ = vol / float(v_ref)
        if r_ < R_MIN:
            return {**base, "R": round(r_, 2),
                    "como": (f"{crudo} cm ocupa {r_:.2f}× el volumen por el que pagó flete (menos de "
                             f"{R_MIN:g}): la pieza no cabe en esa medida — no opina")}
        if r_ < R_MASTER:
            if r_ >= R_PIEZA and ppc and ppc >= 2 and abs(r_ / ppc - 1.0) <= 0.25:
                # Un cartón de 2 o 3 piezas: por el flete van R piezas en esa
                # medida y la columna piezas_por_caja dice lo mismo.
                u2 = dims_pieza(l_, a_, h_, r_)
                if all(x > 0 for x in u2):
                    return {**base, "caja": tuple(float(x) for x in u2), "R": round(r_, 2),
                            "master": True, "confianza": "baja",
                            "como": (f"{crudo} cm es un cartón de {ppc:g} piezas (R={r_:.2f} contra el "
                                     "flete y la columna piezas_por_caja coincide): pieza = cartón ÷ "
                                     f"{r_:.2f} (lado mayor, como en Costos) = {_caja_txt(u2)} cm")}
            return {**base, "caja": tal_cual, "R": round(r_, 2),
                    "confianza": "media" if r_ <= R_PIEZA else "baja",
                    "como": f"{crudo} cm tal cual: es la pieza (R={r_:.2f} contra el flete)"}
        u = dims_pieza(l_, a_, h_, r_)
        if not all(x > 0 for x in u):
            return {**base, "R": round(r_, 2), "como": f"{crudo} cm: no se pudo repartir el master"}
        aviso = None
        if ppc and ppc > 1 and abs(r_ / ppc - 1.0) > 0.25:
            aviso = (f"flete y columna no cuadran: por el flete van {r_:.1f} piezas por caja y la "
                     f"columna piezas_por_caja dice {ppc:g}")
        return {**base, "caja": tuple(float(x) for x in u), "R": round(r_, 2), "master": True,
                "confianza": "baja", "aviso": aviso,
                "como": (f"{crudo} cm es el cartón MASTER (R={r_:.2f} ≥ {R_MASTER:g}): pieza = master ÷ "
                         f"{r_:.2f} ({'lado mayor' if r_ <= 10 else 'raíz cúbica'}, como en Costos) = "
                         f"{_caja_txt(u)} cm")}
    peso = _num((medida or {}).get("peso_kg"))
    dens = (peso / vol) if peso else None
    if dens is not None and dens < DENSIDAD_MASTER and ppc and ppc > 1:
        u = dims_pieza(l_, a_, h_, ppc)
        if all(x > 0 for x in u):
            return {**base, "caja": tuple(float(x) for x in u), "master": True, "confianza": "baja",
                    "como": (f"{crudo} cm con {dens:.1f} kg/m³ es el cartón MASTER (sin flete con qué "
                             f"probarlo): pieza = master ÷ {ppc:g} piezas por caja = {_caja_txt(u)} cm")}
    if dens is not None and dens < DENSIDAD_MIN:
        return {**base, "como": (f"{crudo} cm con {peso:g} kg ({dens:.1f} kg/m³) es un cartón master y no "
                                 "hay flete ni piezas por caja para repartirlo — no opina para la caja")}
    aviso_sf = None
    if dens is not None and dens < DENSIDAD_MASTER:
        aviso_sf = (f"sin flete con qué probarla y con {dens:.1f} kg/m³ (menos de {DENSIDAD_MASTER:g}): "
                    f"{crudo} cm puede ser el cartón MASTER y no la pieza — mídela (Checklist de "
                    "almacén o captura en el panel)")
    return {**base, "caja": tal_cual, "confianza": "baja", "aviso": aviso_sf,
            "como": f"{crudo} cm tal cual (sin flete con qué probar que sea la pieza)"}


def _fuentes_activas(orden: Iterable[str]) -> frozenset[str]:
    """Qué fuentes PARTICIPAN, según `TEMU_GUIAS_FUENTES_MEDIDA`. Ya no es una
    escalera por orden (gana el MENOR creíble): la variable sólo dice quién
    opina. `historial_temu` y `temu_ultima_guia` son lo mismo: las guías del
    mismo SKU. PURA."""
    o = set(orden)
    act: set[str] = set()
    if "almacen" in o:
        act.add("almacen")
    if "omnicanal_packing" in o:
        act.add("packing")
    if "omnicanal_woo" in o:
        act.add("woo")
    if o & {"historial_temu", "temu_ultima_guia"}:
        act.add("guias")
    if "temu_hermanas" in o:
        act.add("hermanas")
    if "temu_publicacion" in o:
        act.add("publicacion")
    return frozenset(act)


def v_ref_de(cat: dict[str, Any] | None) -> float | None:
    """m³ POR PIEZA por los que se pagó flete: `costo_cbm / 7500` (la tarifa de
    la pestaña de Costos, `costos.TARIFA_CBM_M3`). None si la fila no lo trae.
    PURA."""
    cc = _num((cat or {}).get("costo_cbm"))
    return (cc / TARIFA_CBM) if cc else None


def lo_nuestro(sku: str, medidas_almacen: dict[str, dict[str, Any]] | None = None,
               omni: dict[str, Any] | None = None, cat: dict[str, Any] | None = None,
               activas: Iterable[str] | None = None, *,
               catalogo_leido: bool = True) -> dict[str, Any]:
    """
    Lo que OMNICANAL sabe de UNA pieza del SKU: su peso y su caja. PURA.

    PESO: si almacén lo pesó (`core.products.almacen_peso_kg`), ése —es una
    medición real y sustituye a packing y Woo—; si no, el MENOR creíble entre
    el packing list y Woo. Creíble = 0.01-30 kg y densidad 10-1,500 kg/m³
    contra la pieza unitaria YA CORREGIDA (no contra el L×A×H crudo de la fila:
    con el cartón master, 0.099 kg en 60×40×40 daba 1 kg/m³ y se rechazaba).
    CAJA (una sola, igual para toda cantidad): la medición de almacén; si no,
    la de MENOR volumen entre la unitaria del packing y la de Woo
    (`unitaria_de_catalogo`); si empatan dentro de 5 %, el packing.
    Odoo NO es fuente: aquí sólo se leen `almacen`, `packing` y `woo`.
    `catalogo_leido=False` (costing.costos_validados no contestó): Woo NO opina
    para la caja —sin el flete no se sabe si su medida es la pieza o el cartón
    master (se declaraba 52×42×35 por un juguete)—; la medición de almacén sí.
    `avisos` son los de la CAJA y `avisos_peso` los del PESO (valen por
    separado: cada uno acompaña a lo que gane de lo nuestro).
    """
    act = frozenset(activas) if activas is not None else frozenset({"almacen", "packing", "woo"})
    omni = omni or {}
    v_ref = v_ref_de(cat)
    ppc = _num((cat or {}).get("piezas_por_caja"))
    out: dict[str, Any] = {"peso_kg": None, "peso_fuente": None, "peso_conf": "ninguna",
                           "unitaria": None, "caja_fuente": None, "caja_conf": "ninguna",
                           "caja_como": "", "R": None, "v_ref": v_ref, "master": False,
                           "descartes_peso": [], "descartes_caja": [], "avisos": [],
                           "avisos_peso": [], "pesos_por_pieza": {}}

    def _dp(fuente: str, kg: float | None, por: str) -> None:
        out["descartes_peso"].append({"fuente": fuente, "kg_pieza": kg, "por": por})

    def _dc(fuente: str, por: str) -> None:
        out["descartes_caja"].append({"fuente": fuente, "por": por})

    # ── Las unitarias de catálogo (packing list y Woo). Sólo esas dos llaves:
    #    cualquier otra cosa que viniera en `omni` (Odoo) no se mira.
    unidades: dict[str, dict[str, Any]] = {}
    #    La columna piezas_por_caja acompaña también a la medida de Woo (es
    #    del SKU, no de la fila): sin flete, es lo único que la reconoce master.
    for fuente, llave, columna in (("omnicanal_packing", "packing", ppc),
                                   ("omnicanal_woo", "woo", ppc)):
        m = omni.get(llave) if llave in act else None
        if not isinstance(m, dict) or not any(_num(m.get(k)) for k in (
                "largo_cm", "ancho_cm", "alto_cm")):
            continue
        if catalogo_leido:
            u = unitaria_de_catalogo(m, v_ref, columna)
        else:
            u = {"caja": None, "confianza": "ninguna", "R": None, "master": False, "aviso": None,
                 "como": _SIN_CATALOGO}                     # falla cerrado
        unidades[fuente] = u
        if not u["caja"]:
            _dc(fuente, u["como"])

    # ── La medición de almacén (Checklist): por pieza, real, sin pasar por R.
    #    El peso y la caja valen por separado: si almacén sólo pesó, ese peso es
    #    el nuestro (y la caja sale del catálogo); si sólo midió, al revés.
    alm = (medidas_almacen or {}).get(sku) if "almacen" in act else None
    alm_peso: float | None = None
    alm_caja: tuple[float, float, float] | None = None
    if isinstance(alm, dict) and any(_num(alm.get(k)) for k in (
            "peso_kg", "largo_cm", "ancho_cm", "alto_cm")):
        w_alm = _num(alm.get("peso_kg"))
        ld = [_num(alm.get(k)) for k in ("largo_cm", "ancho_cm", "alto_cm")]
        if w_alm and all(ld):
            mala = cordura_pieza({"peso_kg": w_alm, "largo_cm": ld[0], "ancho_cm": ld[1],
                                  "alto_cm": ld[2]}, real=True)
            if mala:
                _dp("almacen", w_alm, f"la medición de almacén no es creíble: {mala}")
                _dc("almacen", f"la medición de almacén no es creíble: {mala}")
            else:
                alm_peso, alm_caja = w_alm, (float(ld[0]), float(ld[1]), float(ld[2]))  # type: ignore[arg-type]
        else:
            if w_alm and PESO_MIN_KG <= w_alm <= PESO_MAX_KG:
                alm_peso = w_alm
            elif w_alm:
                _dp("almacen", w_alm, f"la medición de almacén no es creíble: {w_alm:g} kg por pieza")
            if all(ld) and max(ld) <= LADO_MAX_CM:  # type: ignore[type-var]
                alm_caja = (float(ld[0]), float(ld[1]), float(ld[2]))  # type: ignore[arg-type]
            elif any(ld):
                _dc("almacen", "la medición de almacén no trae sus tres medidas (o un lado pasa de "
                               f"{LADO_MAX_CM:.0f} cm)")
    alm_ok = alm_peso is not None          # el peso de almacén sustituye a packing y Woo

    # ── B.2 · La unitaria NUESTRA.
    if alm_caja:
        out.update(unitaria=alm_caja, caja_fuente="almacen", caja_conf="alta",
                   caja_como="medición de almacén (Checklist), por pieza")
        for fuente, u in unidades.items():
            if u["caja"]:
                _dc(fuente, "la sustituye la medición de almacén")
    else:
        con = [(f, u) for f, u in unidades.items() if u["caja"]]
        if con:
            gana = con[0]
            if len(con) == 2:
                vp, vw = _vol3(con[0][1]["caja"]), _vol3(con[1][1]["caja"])
                # Empate dentro de 5 % → el packing (con[0]); si no, la menor.
                if abs(vp - vw) > 0.05 * max(vp, vw) and vw < vp:
                    gana = con[1]
                pierde = con[1] if gana is con[0] else con[0]
                _dc(pierde[0], (f"su pieza ({_caja_txt(pierde[1]['caja'])} cm, "
                                f"{_vol3(pierde[1]['caja']) / 1000:.2f} L) no es la de menor volumen"
                                + (" (empate dentro de 5 %: manda el packing list)"
                                   if gana is con[0] and abs(vp - vw) <= 0.05 * max(vp, vw) else "")))
            f, u = gana
            out.update(unitaria=tuple(u["caja"]), caja_fuente=f, caja_conf=u["confianza"],
                       caja_como=u["como"], R=u["R"], master=bool(u["master"]))
            if u.get("aviso"):
                out["avisos"].append(u["aviso"])

    # ── A.2 · El peso NUESTRO por pieza.
    if alm_ok:
        out.update(peso_kg=alm_peso, peso_fuente="almacen", peso_conf="alta")
        out["pesos_por_pieza"]["almacen"] = alm_peso
    creibles: list[tuple[float, str]] = []
    for fuente, llave in (("omnicanal_packing", "packing"), ("omnicanal_woo", "woo")):
        m = omni.get(llave) if llave in act else None
        w = _num((m or {}).get("peso_kg")) if isinstance(m, dict) else None
        if not w:
            continue
        if alm_ok:
            _dp(fuente, w, "lo sustituye el peso medido por almacén")
            continue
        if w < PESO_MIN_KG:
            _dp(fuente, w, f"{w:g} kg por pieza (menos de 10 g): no es un peso real")
            continue
        if w > PESO_MAX_PIEZA_KG:
            _dp(fuente, w, (f"{w:,.2f} kg por pieza (más de {PESO_MAX_PIEZA_KG:.0f} sin una medición "
                            "real): no se declara"))
            continue
        ref = (unidades.get(fuente) or {}).get("caja") or out["unitaria"]
        if ref:
            dens = w / (_vol3(ref) / 1_000_000.0)
            if dens < DENSIDAD_MIN:
                _dp(fuente, w, (f"densidad de {dens:.1f} kg/m³ contra su pieza ({_caja_txt(ref)} cm; "
                                f"mínimo {DENSIDAD_MIN:.0f}): caja enorme para ese peso"))
                continue
            if dens > DENSIDAD_MAX_CATALOGO:
                _dp(fuente, w, (f"densidad de {dens:,.0f} kg/m³ contra su pieza ({_caja_txt(ref)} cm; "
                                f"máximo {DENSIDAD_MAX_CATALOGO:,.0f} para un dato de catálogo): parece "
                                "el peso de la caja MASTER con las medidas de una pieza"))
                continue
        creibles.append((w, fuente))
        out["pesos_por_pieza"][fuente] = w
    if creibles and not alm_ok:
        w, f = min(creibles, key=lambda t: t[0])       # empate → el packing (va primero)
        out.update(peso_kg=w, peso_fuente=f, peso_conf="media")
        for w2, f2 in creibles:
            if f2 != f:
                _dp(f2, w2, f"{w2:g} kg por pieza: no es el menor de lo nuestro ({w:g} kg)")
        # ¿Es el peso del CARTÓN? Una fila master cuyo peso "por pieza", por
        # las piezas que van en el cartón, da un cartón de más de 40 kg. Sólo
        # aviso (ver `PESO_CARTON_AVISO_KG`): con una guía de Temu creíble gana
        # Temu; sin ella se declara éste, y se dice.
        ug = unidades.get(f) or {}
        por_carton = _num(ug.get("R")) or ppc
        if ug.get("master") and por_carton and w * por_carton > PESO_CARTON_AVISO_KG:
            out["avisos_peso"].append(
                f"peso por pieza alto para una fila master: {w:g} kg × {por_carton:.1f} piezas por "
                f"cartón = {w * por_carton:.0f} kg de cartón (más de {PESO_CARTON_AVISO_KG:g}) — "
                f"¿{FUENTES_TXT[f]} trae el peso del CARTÓN? pésala")
    return out


def guia_contra_flete(caja: Iterable[float], piezas: float,
                      v_ref: float | None) -> tuple[bool, float | None, str | None]:
    """¿La caja declarada en una guía de Temu sirve para `piezas` piezas? Con
    R = volumen ÷ (piezas × v_ref): debajo de 0.90 las piezas NO CABEN en ella
    y de 3 para arriba es el cartón master (o una caja estándar) copiado. Sin
    `v_ref` no hay con qué probarla: pasa. (ok, R, por qué no). PURA."""
    if not v_ref or float(v_ref) <= 0 or float(piezas) <= 0:
        return True, None, None
    r_ = (_vol3(caja) / 1_000_000.0) / (float(piezas) * float(v_ref))
    if r_ < PISO_TEMU:
        return False, round(r_, 2), (f"las piezas no caben en esa caja (R={r_:.2f} contra el flete, "
                                     f"menos de {PISO_TEMU:g})")
    if r_ >= R_MASTER:
        return False, round(r_, 2), (f"es el cartón master o una caja estándar copiada (R={r_:.2f} "
                                     f"contra el flete, {R_MASTER:g} o más)")
    return True, round(r_, 2), None


def _fecha_txt(v: Any) -> str:
    if isinstance(v, datetime):
        return (v.astimezone(ZONA) if v.tzinfo else v).date().isoformat()
    return str(v)[:10] if v else ""


def _reciente(m: dict[str, Any]) -> str:
    """Para ordenar muestras por fecha (texto ISO; sin fecha, la más vieja)."""
    v = m.get("creado_at")
    if isinstance(v, datetime):
        return (v.astimezone(timezone.utc) if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    return str(v or "")


def _ref(m: dict[str, Any]) -> str:
    """"PO-… del 2026-09-26" de una muestra del historial (sin datos del comprador)."""
    po = str(m.get("parent_order_sn") or "—")
    f = _fecha_txt(m.get("creado_at"))
    return f"{po} del {f}" if f else po


def _conf_min(*niveles: str) -> str:
    return min(niveles, key=lambda c: _CONFIANZA_NIVEL.get(str(c), 0))


def _base_empaque() -> dict[str, Any]:
    return {"ok": False, "peso_kg": None, "largo_cm": None, "ancho_cm": None,
            "alto_cm": None, "fuente": None, "fuente_peso": None, "fuente_txt": None,
            "confianza": "ninguna", "muestras": 0, "detalle": "", "motivo": None, "aviso": None,
            "avisos": [], "descartes": [], "peso": None, "caja": None}


def _mala_guia(g: dict[str, Any]) -> str | None:
    """Por qué lo declarado en una guía de Temu NO es creíble (None = pasa): la
    cordura de la caja con su propio peso, y ≤ 30 kg por pieza. PURA."""
    try:
        k = max(1, int(g["cantidad"]))
        w = float(g["peso_kg"])
        mala = plausible(w, float(g["largo_cm"]), float(g["ancho_cm"]), float(g["alto_cm"]))
    except (KeyError, TypeError, ValueError):
        return "la guía no trae peso y caja legibles"
    if not mala and w / k > PESO_MAX_PIEZA_KG:
        mala = f"{w / k:,.2f} kg por pieza (más de {PESO_MAX_PIEZA_KG:.0f})"
    return mala


def elegir_empaque(contenido: dict[str, int], historial: list[dict[str, Any]],
                   medidas_almacen: dict[str, dict[str, Any]] | None = None,
                   min_muestras: int | None = None,
                   dispersion_max: float = 0.15, *,
                   manual: dict[str, Any] | None = None,
                   catalogo: dict[str, dict[str, Any]] | None = None,
                   catalogo_leido: bool = True,
                   omnicanal: dict[str, dict[str, Any]] | None = None,
                   publicacion: dict[str, dict[str, Any]] | None = None,
                   fuentes: Iterable[str] | None = None,
                   eco: Iterable[str] | None = None,
                   limites: dict[str, float] | None = None) -> dict[str, Any]:
    """
    Peso y caja de UNA caja, diciendo de dónde sale cada uno y qué candidatos
    se descartaron. PURA (salvo `fuentes=None` / `limites=None`, que leen la
    configuración). Ver el encabezado: EL MENOR CREÍBLE entre lo nuestro y lo
    de Temu (Brandon, 1-oct).

    `contenido` = {sku: piezas}. `historial` = muestras de guías ya compradas
    (así las da `_historial_empaque`): [{sku, cantidad, peso_kg, largo_cm,
    ancho_cm, alto_cm, parent_order_sn, creado_at?, composicion?}] — las de las
    variantes hermanas vienen con su propio SKU, y las de un PO con varios SKUs
    con `composicion` {sku: piezas}.
    `eco` = los PO cuya guía compró ESTE sistema (bitácora 0061): lo que ahí se
    declaró salió de esta misma regla y NO cuenta como dato de Temu (ni en peso
    ni en caja); si contara, un dato nuestro corregido nunca volvería a subir.
    El plan ya NO lo pasa: el historial que arma (`_historial_candidatos`) lo
    trae quitado desde el SQL, antes del tope por (SKU, piezas). Queda para
    quien llame con su propio historial.
    `manual` = la medida capturada en el panel para ESTA caja (manda, sin
    comparar). `catalogo` = {sku: {largo_cm, ancho_cm, alto_cm, peso_kg?,
    costo_cbm?, piezas_por_caja?}} de costos_validados (de ahí sale el volumen
    de referencia del flete); con `catalogo_leido=False` las guías de Temu no
    se usan (no se sabe cuáles copiaron el cartón master).
    `omnicanal` = {sku: {"packing": medida por pieza, "woo": medida por pieza}}.
    Y con `catalogo_leido=False` tampoco opinan para la CAJA ni Woo ni la
    publicación (sin el flete no se sabe si su medida es el cartón master):
    falla cerrado, la caja queda sin medida.
    `publicacion` = {sku: medida por pieza} del paquete de la publicación.
    `fuentes` = quién opina (`TEMU_GUIAS_FUENTES_MEDIDA`; el ORDEN ya no manda).
    `min_muestras` y `dispersion_max` se conservan por compatibilidad: con "el
    menor creíble" basta UNA guía.
    `ok=True` = se puede comprar con él; lo demás se muestra con su motivo.
    """
    orden = tuple(fuentes) if fuentes is not None else fuentes_medida()[0]
    lim = limites or limites_jt()
    base = _base_empaque()

    # 0 · La medida capturada en el panel para ESTA caja: es una persona que la
    #     pesó y la midió, y la aprueba al aprobar el payload. Manda sobre todo.
    if manual:
        tm = FUENTES_TXT["manual"]
        m = {k: _num(manual.get(k)) for k in ("peso_kg", "largo_cm", "ancho_cm", "alto_cm")}
        if not all(m.values()):
            return {**base, "fuente": "manual", "fuente_peso": "manual", "fuente_txt": tm,
                    "motivo": "la medida capturada está incompleta: peso y las tres medidas, positivos"}
        if m["peso_kg"] < PESO_MIN_KG:
            return {**base, **m, "fuente": "manual", "fuente_peso": "manual", "fuente_txt": tm,
                    "motivo": f"el peso capturado ({m['peso_kg']} kg) es menor a 10 g"}
        rara = plausible(m["peso_kg"], m["largo_cm"], m["ancho_cm"], m["alto_cm"])
        md = medir_caja(m["peso_kg"], (m["largo_cm"], m["ancho_cm"], m["alto_cm"]), lim)
        avisos = ([f"revisa la captura: {rara}"] if rara else [])
        if not md["cabe"] and not rara:
            avisos.append(f"no cabe en J&T ({_limites_txt(lim)}): {'; '.join(md['motivos'])}")
        return {**base, **m, "ok": True, "confianza": "alta", "fuente": "manual",
                "fuente_peso": "manual", "fuente_txt": tm,
                "detalle": "medida capturada en el panel para esta caja (manda, sin comparar)",
                "avisos": avisos, "aviso": " · ".join(avisos) or None,
                "peso": {"kg": m["peso_kg"], "fuente": "manual", "fuente_txt": tm, "candidatos": []},
                "caja": {"cm": [m["largo_cm"], m["ancho_cm"], m["alto_cm"]], "fuente": "manual",
                         "fuente_txt": tm, "cabe_jt": md["cabe"], **md, "candidatos": []}}

    ecos = {str(x) for x in (eco or ())}
    hist = [m for m in (historial or []) if str(m.get("parent_order_sn") or "") not in ecos]
    ctx: dict[str, Any] = {"historial": hist, "medidas_almacen": medidas_almacen or {},
                           "catalogo": catalogo or {}, "catalogo_leido": catalogo_leido,
                           "omnicanal": omnicanal or {}, "publicacion": publicacion or {},
                           "activas": _fuentes_activas(orden), "lim": lim,
                           "ecos": len(historial or []) - len(hist)}
    if len(contenido) != 1:
        return _empaque_mixto(contenido, **ctx)
    sku, q = next(iter(contenido.items()))
    return _empaque_sku(str(sku), int(q), **ctx)


def _limites_txt(lim: dict[str, float]) -> str:
    return f"{lim['peso']:g} kg / {lim['lado']:g} cm por lado / {lim['suma']:g} cm sumando los tres"


def _empaque_sku(sku: str, q: int, *, historial: list[dict[str, Any]],
                 medidas_almacen: dict[str, dict[str, Any]],
                 catalogo: dict[str, dict[str, Any]], catalogo_leido: bool,
                 omnicanal: dict[str, dict[str, Any]], publicacion: dict[str, dict[str, Any]],
                 activas: frozenset[str], lim: dict[str, float],
                 ecos: int = 0) -> dict[str, Any]:
    """Peso y caja de UNA caja de UN SKU × `q` piezas. PURA. Ver el encabezado."""
    base = _base_empaque()
    q = max(1, int(q))
    nu = lo_nuestro(sku, medidas_almacen, omnicanal.get(sku) or {}, catalogo.get(sku), activas,
                    catalogo_leido=catalogo_leido)
    v_ref = nu["v_ref"]
    fam = familia(sku)
    piso = piso_peso()

    del_sku = [m for m in historial if m.get("sku") == sku]
    usar_guias = "guias" in activas and catalogo_leido
    guias = del_sku if usar_guias else []
    buenas = [g for g in guias if not _mala_guia(g)]
    hermanas: list[dict[str, Any]] = []
    reciclada = bool(fam and fam in FAMILIAS_RECICLADAS)
    if "hermanas" in activas and fam and not reciclada and catalogo_leido:
        hermanas = [m for m in historial if m.get("sku") != sku and not m.get("composicion")
                    and familia(m.get("sku")) == fam and not _mala_guia(m)]

    # ═══ A · EL PESO ═══════════════════════════════════════════════════════
    cp: list[dict[str, Any]] = []

    def _peso(lado: str, fuente: str, kg: float, txt: str, conf: str) -> None:
        cp.append({"lado": lado, "fuente": fuente, "kg": float(kg), "txt": txt, "confianza": conf,
                   "ok": True, "por": None})

    def _no_peso(fuente: str, txt: str, por: str, kg: float | None = None) -> None:
        cp.append({"lado": "—", "fuente": fuente, "kg": kg, "txt": txt, "confianza": "ninguna",
                   "ok": False, "por": por})

    if nu["peso_kg"]:
        _peso("nuestro", nu["peso_fuente"], nu["peso_kg"] * q,
              f"{FUENTES_TXT[nu['peso_fuente']]}: {nu['peso_kg']:g} kg por pieza × {q}", nu["peso_conf"])
    for d in nu["descartes_peso"]:
        _no_peso(d["fuente"], FUENTES_TXT[d["fuente"]], d["por"],
                 (d["kg_pieza"] * q) if d.get("kg_pieza") else None)
    if del_sku and "guias" in activas and not catalogo_leido:
        _no_peso("temu_guia", FUENTES_TXT["temu_guia"],
                 "no se pudo leer el catálogo (costing.costos_validados no contestó): sin él no se "
                 "sabe qué guías copiaron el cartón master — no se usan")
    for g in guias:
        mala = _mala_guia(g)
        if mala:
            _no_peso("temu_guia", f"guía de {sku} × {g.get('cantidad')} ({_ref(g)})",
                     f"lo que declaró no es creíble: {mala}")
    exactas = [g for g in buenas if int(g["cantidad"]) == q]
    if exactas:
        g = min(exactas, key=lambda m: float(m["peso_kg"]))
        _peso("temu", "temu_guia", float(g["peso_kg"]),
              (f"guía de {sku} × {q} ({_ref(g)}): {float(g['peso_kg']):g} kg"
               + (f", la menor de {len(exactas)}" if len(exactas) > 1 else "")), "media")
    unas = [g for g in buenas if int(g["cantidad"]) == 1]
    x1 = guia_x1_por_piezas()
    if q > 1 and unas and x1:
        g = min(unas, key=lambda m: float(m["peso_kg"]))
        _peso("temu", "temu_guia_x1", float(g["peso_kg"]) * q,
              (f"guía de 1 pieza ({_ref(g)}): {float(g['peso_kg']):g} kg × {q} — EXTRAPOLADO, no es "
               f"una guía de {q} piezas"), "media")
    elif q > 1 and unas:
        g = min(unas, key=lambda m: float(m["peso_kg"]))
        _no_peso("temu_guia_x1", f"guía de 1 pieza ({_ref(g)}): {float(g['peso_kg']):g} kg × {q}",
                 "TEMU_GUIAS_PESO_GUIA_X1_POR_PIEZAS está apagada: la guía de 1 pieza no se multiplica "
                 "(de Temu sólo cuenta la guía de esas piezas y la publicación)",
                 float(g["peso_kg"]) * q)
    pub = publicacion.get(sku) if "publicacion" in activas else None
    pm = _medida(pub) if isinstance(pub, dict) else None
    pub_ok = False                # ¿su CAJA opina?
    pub_caja_rell = False         # sus medidas son las de relleno
    if isinstance(pub, dict) and not pm and any(_num(pub.get(k)) for k in (
            "peso_kg", "largo_cm", "ancho_cm", "alto_cm")):
        _no_peso("temu_publicacion", FUENTES_TXT["temu_publicacion"],
                 "el paquete de la publicación está incompleto")
    if pm:
        # EL RELLENO, CADA CAMPO POR SEPARADO (`publicar_temu` rellena así): un
        # peso de exactamente 0.1 o 0.5 kg con medidas reales no es el paquete
        # de relleno entero, y × piezas le ganaría a todo por ser el menor.
        peso_rell, pub_caja_rell = relleno_por_campo(pm)
        if peso_rell and pub_caja_rell:
            _no_peso("temu_publicacion", FUENTES_TXT["temu_publicacion"],
                     "la publicación trae el paquete de RELLENO (100 g · 10×20×30 de Temu, o 500 g · "
                     "20×20×20 de publicar_temu): no es una medida", pm["peso_kg"] * q)
        else:
            mala = cordura_pieza(pm, catalogo=True)
            if mala:
                _no_peso("temu_publicacion", FUENTES_TXT["temu_publicacion"],
                         f"el paquete de la publicación no es creíble por pieza: {mala}",
                         pm["peso_kg"] * q)
            else:
                if peso_rell and nu["peso_kg"] and abs(pm["peso_kg"] - nu["peso_kg"]) < 0.005:
                    peso_rell = False           # coincide con lo nuestro: es su peso
                if peso_rell:
                    _no_peso("temu_publicacion", FUENTES_TXT["temu_publicacion"],
                             (f"su peso ({pm['peso_kg']:g} kg) es exactamente el de RELLENO por omisión "
                              "(100 g de Temu, 500 g de publicar_temu, que rellena cada campo por "
                              "separado) y no coincide con lo nuestro: no opina en el peso"),
                             pm["peso_kg"] * q)
                else:
                    _peso("temu", "temu_publicacion", pm["peso_kg"] * q,
                          f"paquete de la publicación en Temu: {pm['peso_kg']:g} kg por pieza × {q}",
                          "baja")
                pub_ok = not pub_caja_rell

    if not any(c["ok"] for c in cp):
        # A.6 · Sin lo nuestro y sin Temu propio de esas piezas: una guía de
        #       OTRA cantidad del mismo SKU, y después las variantes hermanas.
        otras = [g for g in buenas if int(g["cantidad"]) not in ((1, q) if x1 else (q,))]
        if otras:
            g = min(otras, key=lambda m: float(m["peso_kg"]) / int(m["cantidad"]))
            por_pz = float(g["peso_kg"]) / int(g["cantidad"])
            _peso("respaldo", "temu_guia_otra", por_pz * q,
                  (f"guía de {sku} × {g['cantidad']} ({_ref(g)}): {por_pz:.3f} kg por pieza × {q}"),
                  "baja")
        elif hermanas:
            de_una = [g for g in hermanas if int(g["cantidad"]) == 1]
            g = max(de_una or hermanas, key=_reciente)
            por_pz = float(g["peso_kg"]) / int(g["cantidad"])
            _peso("respaldo", "temu_hermanas", por_pz * q,
                  (f"variante {g.get('sku')} × {g['cantidad']} ({_ref(g)}): {por_pz:.3f} kg por pieza "
                   f"× {q} — misma familia; que sea el mismo producto NO está verificado"), "baja")
        elif reciclada and "hermanas" in activas:
            _no_peso("temu_hermanas", FUENTES_TXT["temu_hermanas"],
                     f"la familia {fam} tiene SKUs RECICLADOS (el mismo número es otro producto, "
                     "CLAUDE.md): sus variantes no sirven de referencia")

    vivos_p = [c for c in cp if c["ok"]]
    if not vivos_p:
        descartes = [f"peso · {c['txt']}: {c['por']}" for c in cp if not c["ok"]]
        quien = ", ".join(sorted({FUENTES_TXT[f] for f in ("almacen", "omnicanal_packing",
                                                           "omnicanal_woo", "temu_guia",
                                                           "temu_hermanas", "temu_publicacion")}))
        return {**base, "descartes": descartes,
                "peso": {"kg": None, "fuente": None, "candidatos": cp},
                "motivo": (f"ninguna fuente ({quien}) tiene un peso creíble de {sku} × {q}"
                           + (f" — {'; '.join(descartes)[:400]}" if descartes else "")
                           + ": hay que pesar y medir la caja (y capturarla en el panel)")}
    # El MENOR; en empate, lo nuestro.
    pe = min(vivos_p, key=lambda c: (round(c["kg"], 6), 0 if c["lado"] == "nuestro" else 1))
    pe["gana"] = True
    peso = _arriba2(max(piso, pe["kg"]))

    # ═══ B · LA CAJA ═══════════════════════════════════════════════════════
    cc: list[dict[str, Any]] = []

    def _caja(lado: str, fuente: str, caja: Iterable[float], txt: str, conf: str, *,
              rejilla: tuple[int, int, int] | None = None,
              unitaria: Iterable[float] | None = None, r_: float | None = None) -> None:
        c3 = tuple(float(x) for x in caja)
        mala = plausible(peso, c3[0], c3[1], c3[2])
        cc.append({"lado": lado, "fuente": fuente, "caja": c3, "txt": txt, "confianza": conf,
                   "rejilla": list(rejilla) if rejilla else None,
                   "unitaria": list(unitaria) if unitaria else None, "R": r_,
                   "ok": not mala, "por": (f"con {peso:.2f} kg: {mala}" if mala else None),
                   "medida": medir_caja(peso, c3, lim)})

    def _no_caja(fuente: str, txt: str, por: str, caja: Iterable[float] | None = None) -> None:
        cc.append({"lado": "—", "fuente": fuente, "caja": tuple(caja) if caja else None, "txt": txt,
                   "confianza": "ninguna", "rejilla": None, "unitaria": None, "R": None,
                   "ok": False, "por": por, "medida": None})

    def _rejilla_txt(ac: dict[str, Any]) -> str:
        i, j, k = ac["rejilla"]
        return f"{q} piezas en rejilla {i}×{j}×{k}"

    if nu["unitaria"]:
        ac = mejor_acomodo(nu["unitaria"], q, peso, lim)
        _caja("nuestro", nu["caja_fuente"], ac["caja"],
              f"{FUENTES_TXT[nu['caja_fuente']]}: {nu['caja_como']}"
              + (f"; {_rejilla_txt(ac)}" if q > 1 else ""),
              nu["caja_conf"] if q == 1 else _conf_min(nu["caja_conf"], "baja"),
              rejilla=ac["rejilla"], unitaria=nu["unitaria"], r_=nu["R"])
    for d in nu["descartes_caja"]:
        _no_caja(d["fuente"], FUENTES_TXT[d["fuente"]], d["por"])

    def _copio_master(g: dict[str, Any]) -> bool:
        """Sin flete con qué probarla: ¿la guía declaró la caja IDÉNTICA a una
        fila de catálogo (de su SKU o del que se compra) que NO es una pieza
        (cartón master, o no opina)? Entonces copió el master."""
        for s in {str(g.get("sku")), sku}:
            fila = catalogo.get(s)
            crudas = [fila] if fila else []
            crudas += [x for x in (omnicanal.get(s) or {}).values() if isinstance(x, dict)]
            for cr in crudas:
                if not es_caja_de_catalogo(g["largo_cm"], g["ancho_cm"], g["alto_cm"], cr):
                    continue
                u = unitaria_de_catalogo({**cr, "peso_kg": cr.get("peso_kg")
                                          or float(g["peso_kg"]) / max(1, int(g["cantidad"]))},
                                         v_ref_de(catalogo.get(s)),
                                         (catalogo.get(s) or {}).get("piezas_por_caja"))
                if u["master"] or not u["caja"]:
                    return True
        return False

    def _de_guias(lista: list[dict[str, Any]], lado: str) -> None:
        """Las candidatas de Temu (B.4) de una lista de guías creíbles: del
        mismo SKU (`lado='temu'`) o de sus variantes hermanas."""
        vistas: set[tuple[Any, ...]] = set()

        def _quien(g: dict[str, Any]) -> str:
            return sku if lado == "temu" else f"la variante {g.get('sku')}"

        def _vref(g: dict[str, Any]) -> float | None:
            return v_ref if lado == "temu" else (v_ref_de(catalogo.get(str(g.get("sku")))) or v_ref)

        def _probar(g: dict[str, Any], piezas: int, etiqueta: str) -> tuple[bool, float | None]:
            caja = (float(g["largo_cm"]), float(g["ancho_cm"]), float(g["alto_cm"]))
            vr = _vref(g)
            ok, r_, por = guia_contra_flete(caja, piezas, vr)
            if ok and vr is None and _copio_master(g):
                ok, por = False, ("es IDÉNTICA a la medida de catálogo, que es el cartón master "
                                  "(quien compró la guía copió el catálogo)")
            if not ok:
                _no_caja(etiqueta_fuente(piezas), f"{etiqueta} ({_ref(g)}, {_caja_txt(caja)} cm)",
                         str(por), caja)
            return ok, r_

        def etiqueta_fuente(piezas: int) -> str:
            if lado != "temu":
                return "temu_hermanas"
            return "temu_guia" if piezas == q else ("temu_guia_x1" if piezas == 1 else "temu_guia_otra")

        nota = "" if lado == "temu" else " — misma familia; que sea el mismo producto NO está verificado"
        # · de exactamente esas piezas: tal cual.
        for g in lista:
            if int(g["cantidad"]) != q:
                continue
            caja = (float(g["largo_cm"]), float(g["ancho_cm"]), float(g["alto_cm"]))
            llave = ("=", str(g.get("sku")), tuple(sorted(caja)))
            if llave in vistas:
                continue
            vistas.add(llave)
            ok, r_ = _probar(g, q, f"caja de la guía de {_quien(g)} × {q}")
            if ok:
                _caja(lado, etiqueta_fuente(q), caja,
                      (f"caja de la guía de {_quien(g)} × {q} ({_ref(g)}) tal cual"
                       + (f" (R={r_:.2f} contra el flete)" if r_ is not None else "") + nota),
                      "media" if lado == "temu" else "baja", r_=r_)
        # · la de 1 pieza como unitaria, acomodada.
        if q > 1:
            for g in lista:
                if int(g["cantidad"]) != 1:
                    continue
                caja = (float(g["largo_cm"]), float(g["ancho_cm"]), float(g["alto_cm"]))
                llave = ("1", str(g.get("sku")), tuple(sorted(caja)))
                if llave in vistas:
                    continue
                vistas.add(llave)
                ok, r_ = _probar(g, 1, f"caja de la guía de 1 pieza de {_quien(g)}")
                if ok:
                    ac = mejor_acomodo(caja, q, peso, lim)
                    _caja(lado, etiqueta_fuente(1), ac["caja"],
                          (f"caja de la guía de 1 pieza de {_quien(g)} ({_ref(g)}, {_caja_txt(caja)} "
                           f"cm); {_rejilla_txt(ac)}" + nota), "baja",
                          rejilla=ac["rejilla"], unitaria=caja, r_=r_)
        # · la de MÁS piezas (la más cercana): le cupieron más, le caben éstas.
        mayores = sorted({int(g["cantidad"]) for g in lista if int(g["cantidad"]) > q})
        if mayores:
            k0 = mayores[0]
            for g in lista:
                if int(g["cantidad"]) != k0:
                    continue
                caja = (float(g["largo_cm"]), float(g["ancho_cm"]), float(g["alto_cm"]))
                llave = (">", str(g.get("sku")), tuple(sorted(caja)))
                if llave in vistas:
                    continue
                vistas.add(llave)
                ok, r_ = _probar(g, k0, f"caja de la guía de {_quien(g)} × {k0}")
                if ok:
                    _caja(lado, etiqueta_fuente(k0) if lado == "temu" else "temu_hermanas", caja,
                          (f"caja de la guía de {_quien(g)} × {k0} ({_ref(g)}) tal cual: le cupieron "
                           f"{k0}" + nota), "baja", r_=r_)

    if del_sku and "guias" in activas and not catalogo_leido:
        _no_caja("temu_guia", FUENTES_TXT["temu_guia"],
                 "no se pudo comparar la caja de sus guías contra el catálogo (costing.costos_validados "
                 "no contestó): no se usan")
    _de_guias(buenas, "temu")
    if pm and pub_caja_rell:
        _no_caja("temu_publicacion", FUENTES_TXT["temu_publicacion"],
                 (f"sus medidas ({_caja_txt((pm['largo_cm'], pm['ancho_cm'], pm['alto_cm']))} cm) son "
                  "las de RELLENO por omisión (10×20×30 de Temu, 20×20×20 de publicar_temu): no "
                  "opina para la caja"))
    elif pm and pub_ok and not catalogo_leido:
        _no_caja("temu_publicacion", FUENTES_TXT["temu_publicacion"], _SIN_CATALOGO)
        pub_ok = False
    if pm and pub_ok:
        caja_p = (pm["largo_cm"], pm["ancho_cm"], pm["alto_cm"])
        ok, r_, por = guia_contra_flete(caja_p, 1, v_ref)
        if not ok:
            _no_caja("temu_publicacion", f"paquete de la publicación ({_caja_txt(caja_p)} cm)",
                     str(por), caja_p)
        else:
            ac = mejor_acomodo(caja_p, q, peso, lim)
            _caja("temu", "temu_publicacion", ac["caja"],
                  (f"paquete de la publicación en Temu ({_caja_txt(caja_p)} cm por pieza)"
                   + (f"; {_rejilla_txt(ac)}" if q > 1 else "")), "baja",
                  rejilla=ac["rejilla"], unitaria=caja_p, r_=r_)
    if not any(c["ok"] for c in cc):
        # B.6 · Sin caja nuestra ni de Temu del mismo SKU: las variantes hermanas.
        if hermanas:
            _de_guias(hermanas, "hermanas")
        elif reciclada and "hermanas" in activas:
            _no_caja("temu_hermanas", FUENTES_TXT["temu_hermanas"],
                     f"la familia {fam} tiene SKUs RECICLADOS (el mismo número es otro producto, "
                     "CLAUDE.md): sus variantes no sirven de referencia")

    def _clave(c: dict[str, Any]) -> tuple[Any, ...]:
        return (*orden_caja(peso, c["caja"], lim), 0 if c["lado"] == "nuestro" else 1)

    vivas = [c for c in cc if c["ok"]]
    ce = min(vivas, key=_clave) if vivas else None
    if ce is not None:
        ce["gana"] = True

    # ═══ El resultado, con todos sus candidatos ═══════════════════════════
    descartes: list[str] = []
    for c in cp:
        if not c["ok"]:
            descartes.append(f"peso · {c['txt']}: {c['por']}")
        elif c is not pe:
            descartes.append(f"peso · {c['txt']} = {c['kg']:.2f} kg: no es el menor")
    for c in cc:
        if not c["ok"]:
            descartes.append(f"caja · {c['txt']}: {c['por']}")
        elif c is not ce:
            descartes.append(f"caja · {c['txt']} = {_caja_txt(c['caja'])} cm (facturable "
                             f"{c['medida']['facturable_kg']:.2f} kg"
                             + ("" if c["medida"]["cabe"] else ", NO cabe en J&T")
                             + "): no es la menor")
    if ecos:
        descartes.append(f"Temu · {ecos} guía(s) que compró este sistema no cuentan como dato de Temu "
                         "(son el eco de esta misma regla)")
    peso_txt = (f"{peso:.2f} kg ← {pe['txt']}"
                + (f" (piso de {piso:g} kg)" if pe["kg"] < piso else ""))
    info_peso = {"kg": peso, "crudo_kg": round(pe["kg"], 4), "fuente": pe["fuente"],
                 "fuente_txt": FUENTES_TXT[pe["fuente"]], "lado": pe["lado"],
                 "confianza": pe["confianza"],
                 "candidatos": [{"fuente": c["fuente"], "txt": c["txt"],
                                 "kg": (round(c["kg"], 3) if c["kg"] is not None else None),
                                 "ok": c["ok"], "por": c["por"], "gana": c is pe} for c in cp]}
    info_cand_caja = [{"fuente": c["fuente"], "txt": c["txt"],
                       "cm": (list(c["caja"]) if c["caja"] else None), "ok": c["ok"],
                       "por": c["por"], "gana": c is ce,
                       "facturable_kg": (c["medida"] or {}).get("facturable_kg"),
                       "cabe_jt": (c["medida"] or {}).get("cabe")} for c in cc]
    if ce is None:
        return {**base, "peso_kg": peso, "peso_crudo_kg": pe["kg"], "fuente_peso": pe["fuente"],
                "fuente_txt": f"peso: {FUENTES_TXT[pe['fuente']]} · caja: ninguna",
                "confianza": "ninguna", "descartes": descartes, "peso": info_peso,
                "caja": {"cm": None, "fuente": None, "candidatos": info_cand_caja},
                "detalle": f"PESO {peso_txt}",
                "motivo": ((f"no se pudo leer el catálogo (costing.costos_validados no contestó): sin "
                            f"el flete no se sabe si la medida de {sku} es la pieza o el cartón master, "
                            "y las guías de Temu no se usan — se reintenta sola (si urge: pésala, "
                            "mídela y captúrala en el panel)") if not catalogo_leido else
                           (f"hay peso de {sku} × {q} ({peso:.2f} kg) pero ninguna fuente da una caja "
                            f"creíble" + (f" — {'; '.join(d for d in descartes if d.startswith('caja'))[:400]}"
                                          if any(d.startswith("caja") for d in descartes) else "")
                            + ": hay que pesar y medir la caja (y capturarla en el panel)"))}

    md = ce["medida"]
    caja = ce["caja"]
    avisos: list[str] = []
    if ce["lado"] == "nuestro":
        avisos.extend(nu["avisos"])
    if pe["lado"] == "nuestro":
        avisos.extend(nu["avisos_peso"])
    if pe["fuente"] == "temu_guia_x1":
        avisos.append(f"el peso ({peso:.2f} kg) se EXTRAPOLÓ de la guía de 1 pieza × {q}: no es un "
                      f"dato de Temu para {q} piezas"
                      + (f" (sus guías de × {q} declararon "
                         f"{min(float(g['peso_kg']) for g in exactas):g} kg)" if exactas else "")
                      + (f"; lo nuestro suma {nu['peso_kg'] * q:g} kg" if nu["peso_kg"] else ""))
    n_max: int | None = None
    partir: str | None = None
    if not md["cabe"]:
        u = ce.get("unitaria") or nu["unitaria"]
        n_max = piezas_que_caben(u, q, peso, lim) if u else 0
        if n_max and q > 1:
            partir = f"partir en {math.ceil(q / n_max)} cajas (caben {n_max} por caja)"
        else:
            partir = "ni una pieza cabe con esas medidas: revísalas"
    sobre = aviso_sobre(caja, lim)
    if sobre:
        avisos.append(sobre)
    # Sub-declaración: lo que se declara contra lo que el equipo declaraba a
    # mano para ese SKU (las guías de este sistema no cuentan).
    ref_vol, ref_txt = None, ""
    ex_todas = [g for g in del_sku if int(g.get("cantidad") or 0) == q]
    de_una_todas = [g for g in del_sku if int(g.get("cantidad") or 0) == 1]
    if ex_todas:
        g = max(ex_todas, key=_reciente)
        ref_vol, ref_txt = _vol(g), f"la guía de × {q} ({_ref(g)})"
    elif de_una_todas:
        g = max(de_una_todas, key=_reciente)
        ref_vol, ref_txt = _vol(g) * q, f"la guía de 1 pieza × {q} ({_ref(g)})"
    if ref_vol and _vol3(caja) < 0.5 * ref_vol:
        avisos.append(f"se declaran {_vol3(caja) / 1000:.1f} L y a mano se declaraban "
                      f"{ref_vol / 1000:.1f} L ({ref_txt}): menos de la mitad — J&T puede re-medir y "
                      "cobrar la diferencia")
    # Las fuentes no coinciden en el peso por pieza: se declara el menor, y se dice.
    por_pieza = [c["kg"] / q for c in cp if c["ok"]]
    if len(por_pieza) > 1 and min(por_pieza) > 0 and max(por_pieza) / min(por_pieza) >= 1.5:
        txt = " · ".join(f"{c['txt'].split(':')[0]} {c['kg'] / q:.3f} kg" for c in cp if c["ok"])
        avisos.append(f"las fuentes NO coinciden en el peso por pieza de {sku} ({txt}); se declara "
                      f"el MENOR ({peso:.2f} kg para {q} pieza(s)) — si la paquetería ajusta, corrige "
                      "el dato que está mal")
    conf = _conf_min(pe["confianza"], ce["confianza"])
    ftxt = (FUENTES_TXT[pe["fuente"]] if pe["fuente"] == ce["fuente"]
            else f"peso: {FUENTES_TXT[pe['fuente']]} · caja: {FUENTES_TXT[ce['fuente']]}")
    info_caja = {"cm": list(caja), "fuente": ce["fuente"], "fuente_txt": FUENTES_TXT[ce["fuente"]],
                 "lado": ce["lado"], "confianza": ce["confianza"], "rejilla": ce["rejilla"],
                 "unitaria_cm": ce["unitaria"], "R": ce["R"], "cabe_jt": md["cabe"],
                 "piezas_por_caja_max": n_max, "partir": partir, **md,
                 "candidatos": info_cand_caja}
    res = {**base, "peso_kg": peso, "peso_crudo_kg": pe["kg"], "largo_cm": caja[0],
           "ancho_cm": caja[1], "alto_cm": caja[2], "fuente": ce["fuente"],
           "fuente_peso": pe["fuente"], "fuente_txt": ftxt, "confianza": conf,
           "muestras": len(exactas) if "temu_guia" in (pe["fuente"], ce["fuente"]) else 0,
           "descartes": descartes, "peso": info_peso, "caja": info_caja,
           "detalle": (f"PESO {peso_txt} · CAJA {_caja_txt(caja)} cm ← {ce['txt']} · volumétrico "
                       f"{md['volumetrico_kg']:.2f} kg, facturable {md['facturable_kg']:.2f} kg, lados "
                       f"suman {md['suma_cm']:g} cm")}
    if not md["cabe"]:
        no_cabe = f"no cabe en J&T ({_limites_txt(lim)}): {'; '.join(md['motivos'])}"
        if ce["lado"] == "hermanas":
            # Una caja armada con datos de OTRA variante (sin verificar) que
            # además no cabe: no se cotiza a ciegas.
            return {**res, "avisos": avisos, "aviso": " · ".join(avisos) or None,
                    "motivo": (f"excede paquetería: {partir} — {no_cabe}; la caja sale de una variante "
                               "hermana (sin verificar): pésala, mídela y captúrala en el panel")}
        avisos.insert(0, f"{no_cabe} — {partir}; se cotiza igual: si Temu ofrece J&T se compra, si no "
                         "la más barata disponible")
    return {**res, "ok": True, "avisos": avisos, "aviso": " · ".join(avisos) or None}


def _empaque_mixto(contenido: dict[str, int], **ctx: Any) -> dict[str, Any]:
    """Una caja con VARIOS SKUs: peso = la SUMA de lo que se declara de cada
    SKU con sus piezas (un solo redondeo al final), contra el peso de una guía
    a mano con esa MISMA composición — el menor; en empate, la suma—; caja = la
    mejor de cada SKU sumadas por la envolvente mínima (`envolvente`), contra
    la de esa guía — gana la menor; en empate, la nuestra. PURA."""
    base = _base_empaque()
    lim = ctx["lim"]
    comp = {str(s): int(q) for s, q in contenido.items()}
    n = len(comp)
    partes = {s: _empaque_sku(s, q, **ctx) for s, q in sorted(comp.items())}
    resumen = {s: {"fuente": e.get("fuente"), "fuente_peso": e.get("fuente_peso"),
                   "fuente_txt": e.get("fuente_txt"), "confianza": e.get("confianza"),
                   "peso_kg": e.get("peso_kg"),
                   "caja_cm": ([e["largo_cm"], e["ancho_cm"], e["alto_cm"]] if e.get("largo_cm")
                               else None), "ok": e["ok"]}
               for s, e in partes.items()}
    malas = [(s, e) for s, e in partes.items() if not e["ok"]]
    if malas:
        txt = " | ".join(f"{s}: {e.get('motivo') or 'sin datos'}" for s, e in malas)
        return {**base, "fuente": "suma_skus", "fuente_peso": "suma_skus",
                "fuente_txt": f"suma de {n} SKUs", "partes": resumen,
                "descartes": [f"{s}: {e.get('motivo')}" for s, e in malas],
                "motivo": (f"caja con {n} SKUs distintos: falta el peso o la caja de "
                           f"{', '.join(s for s, _e in malas)} ({txt[:400]}) — NO se compra sola: "
                           "pésala, mídela y captura la medida en el panel")}
    crudo = sum(float(e["peso_crudo_kg"]) for e in partes.values())
    # Las guías a mano con esa MISMA composición (sin el eco): su caja compite
    # con la envolvente y su PESO con la suma.
    iguales = [m for m in ctx["historial"] if isinstance(m.get("composicion"), dict)
               and {str(k): int(v) for k, v in m["composicion"].items()} == comp
               and "guias" in ctx["activas"] and ctx["catalogo_leido"] and not _mala_guia(m)]
    g_peso = min(iguales, key=lambda m: float(m["peso_kg"])) if iguales else None
    de_temu = bool(g_peso and float(g_peso["peso_kg"]) < crudo - 1e-9)
    peso_crudo = float(g_peso["peso_kg"]) if (g_peso and de_temu) else crudo
    f_peso = "temu_guia" if de_temu else "suma_skus"
    peso = _arriba2(max(piso_peso(), peso_crudo))
    cajas = [(float(e["largo_cm"]), float(e["ancho_cm"]), float(e["alto_cm"])) for e in partes.values()]
    nuestra = envolvente(cajas, peso, lim)
    cands: list[dict[str, Any]] = []

    def _cand(lado: str, caja: Iterable[float], txt: str, conf: str, r_: float | None = None) -> None:
        c3 = tuple(float(x) for x in caja)
        mala = plausible(peso, c3[0], c3[1], c3[2])
        cands.append({"lado": lado, "caja": c3, "txt": txt, "confianza": conf, "R": r_,
                      "ok": not mala, "por": (f"con {peso:.2f} kg: {mala}" if mala else None),
                      "medida": medir_caja(peso, c3, lim)})

    _cand("nuestro", nuestra, ("la mejor caja de cada SKU sumadas por la envolvente mínima ("
                               + " + ".join(_caja_txt(c) for c in cajas) + ")"), "baja")
    refs = [v_ref_de(ctx["catalogo"].get(s)) for s in comp]
    v_total = sum(v * comp[s] for s, v in zip(comp, refs)) if all(refs) else None  # type: ignore[operator]
    vistas: set[tuple[float, ...]] = set()
    for g in iguales:
        caja_g = (float(g["largo_cm"]), float(g["ancho_cm"]), float(g["alto_cm"]))
        if tuple(sorted(caja_g)) in vistas:
            continue
        vistas.add(tuple(sorted(caja_g)))
        ok, r_, por = guia_contra_flete(caja_g, 1, v_total)
        if not ok:
            cands.append({"lado": "—", "caja": caja_g, "txt": f"guía con esa composición ({_ref(g)})",
                          "confianza": "ninguna", "R": r_, "ok": False, "por": por, "medida": None})
            continue
        _cand("temu", caja_g, f"caja de una guía anterior con esa MISMA composición ({_ref(g)})",
              "media", r_)
    vivas = [c for c in cands if c["ok"]]
    fuentes_txt = sorted({str(e["fuente_txt"]) for e in partes.values()})
    # Cada SKU con SU fuente (antes sólo "SKU × piezas": la bitácora perdía de
    # dónde salió cada peso), y la guía de esa composición si la hay.
    cand_peso = [{"fuente": e["fuente_peso"],
                  "txt": (f"{s} × {comp[s]} ← "
                          + str((e.get("peso") or {}).get("fuente_txt")
                                or FUENTES_TXT.get(str(e["fuente_peso"]), e["fuente_peso"]))),
                  "kg": round(float(e["peso_crudo_kg"]), 3), "ok": True, "por": None,
                  "gana": not de_temu} for s, e in partes.items()]
    if g_peso:
        cand_peso.append({"fuente": "temu_guia", "kg": round(float(g_peso["peso_kg"]), 3), "ok": True,
                          "txt": f"guía a mano con esa MISMA composición ({_ref(g_peso)})",
                          "por": (None if de_temu else f"no es menos que la suma ({crudo:.3f} kg)"),
                          "gana": de_temu})
    info_peso = {"kg": peso, "crudo_kg": round(peso_crudo, 4), "fuente": f_peso,
                 "fuente_txt": ("Temu · guía de esa composición" if de_temu else f"suma de {n} SKUs"),
                 "lado": "temu" if de_temu else "nuestro", "candidatos": cand_peso}
    detalle_peso = ("PESO " + " + ".join(f"{s} × {comp[s]} {float(e['peso_crudo_kg']):.3f} kg "
                                         f"({(e['peso'] or {}).get('fuente_txt')})"
                                         for s, e in partes.items())
                    + (f" = {crudo:.3f} kg; la guía a mano con esa composición ({_ref(g_peso)}) declaró "
                       f"{float(g_peso['peso_kg']):g} kg, que es MENOS → {peso:.2f} kg"
                       if (g_peso and de_temu) else f" = {peso:.2f} kg"))
    descartes = [f"{s} · {d}" for s, e in partes.items() for d in e.get("descartes") or []]
    descartes += [f"caja · {c['txt']}: {c['por']}" for c in cands if not c["ok"]]
    if not vivas:
        return {**base, "peso_kg": peso, "peso_crudo_kg": peso_crudo, "fuente": "suma_skus",
                "fuente_peso": f_peso, "fuente_txt": f"suma de {n} SKUs ({' + '.join(fuentes_txt)})",
                "partes": resumen, "descartes": descartes, "peso": info_peso, "detalle": detalle_peso,
                "motivo": (f"la suma de {n} SKUs no da una caja creíble: "
                           + "; ".join(str(c["por"]) for c in cands if c["por"])[:300])}
    ce = min(vivas, key=lambda c: (*orden_caja(peso, c["caja"], lim), 0 if c["lado"] == "nuestro" else 1))
    md = ce["medida"]
    caja = ce["caja"]
    descartes += [f"caja · {c['txt']} = {_caja_txt(c['caja'])} cm (facturable "
                  f"{c['medida']['facturable_kg']:.2f} kg): no es la menor"
                  for c in vivas if c is not ce]
    avisos = [f"{s}: {a}" for s, e in partes.items() for a in e.get("avisos") or []
              if "no cabe en J&T" not in a]
    if not md["cabe"]:
        avisos.insert(0, f"no cabe en J&T ({_limites_txt(lim)}): {'; '.join(md['motivos'])} — partir en "
                         "varias cajas; se cotiza igual: si Temu ofrece J&T se compra, si no la más "
                         "barata disponible")
    sobre = aviso_sobre(caja, lim)
    if sobre:
        avisos.append(sobre)
    # La confianza: la de la caja elegida y la del PESO de cada SKU (la caja de
    # cada parte ya no cuenta si se usa la de una guía de esa composición); la
    # envolvente nuestra es una estimación: baja.
    conf = _conf_min(ce["confianza"], *(str((e.get("peso") or {}).get("confianza")
                                            or e["confianza"]) for e in partes.values()))
    if ce["lado"] == "nuestro":
        conf = "baja"
    return {**base, "ok": True, "peso_kg": peso, "peso_crudo_kg": peso_crudo, "largo_cm": caja[0],
            "ancho_cm": caja[1], "alto_cm": caja[2], "fuente": "suma_skus", "fuente_peso": f_peso,
            "fuente_txt": f"suma de {n} SKUs ({' + '.join(fuentes_txt)})", "confianza": conf,
            "muestras": len(iguales), "partes": resumen, "descartes": descartes,
            "avisos": avisos, "aviso": " · ".join(avisos) or None, "peso": info_peso,
            "caja": {"cm": list(caja), "fuente": "suma_skus", "fuente_txt": ce["txt"],
                     "lado": ce["lado"], "R": ce["R"], "cabe_jt": md["cabe"],
                     "piezas_por_caja_max": None,
                     "partir": (None if md["cabe"] else "partir en varias cajas"), **md,
                     "candidatos": [{"fuente": "suma_skus", "txt": c["txt"], "cm": list(c["caja"]),
                                     "ok": c["ok"], "por": c["por"], "gana": c is ce,
                                     "facturable_kg": (c["medida"] or {}).get("facturable_kg"),
                                     "cabe_jt": (c["medida"] or {}).get("cabe")} for c in cands]},
            "detalle": (f"{detalle_peso} · CAJA {_caja_txt(caja)} cm ← {ce['txt']} · volumétrico "
                        f"{md['volumetrico_kg']:.2f} kg, facturable {md['facturable_kg']:.2f} kg, lados "
                        f"suman {md['suma_cm']:g} cm")}


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


def es_mxn(codigo: Any, texto: Any) -> bool:
    """¿La cotización está en PESOS? PURA. Los topes de la compra automática
    son en MX$: un "US$60.00" leído como 60 pasaría el tope de MX$80 y costaría
    ~MX$1,100. Con código (`estimatedCurrencyCode`) manda el código; sin él,
    sólo "MX$…" cuenta como pesos (el "$" a secas también es el del dólar). Y
    un texto con OTRA moneda desmiente al código."""
    cod = str(codigo or "").strip().upper()
    txt = str(texto or "").strip().upper()
    otra = bool(re.match(r"^[A-Z]{1,3}\$", txt)) and not txt.startswith("MX$")
    if cod:
        return cod == "MXN" and not otra and not re.search(r"USD|EUR|€|CNY|¥", txt)
    return txt.startswith("MX$")


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


def _fila_es_jt(emp: str) -> bool:
    n = _norm(emp)
    return n.startswith("j&t") or n in ("jt", "jtexpress", "jyt")


def es_jt(canal: dict[str, Any], jt_id: int | None = None) -> bool:
    """¿El canal es de J&T? Por su `shipCompanyId` (`TEMU_GUIAS_JT_SHIP_COMPANY_ID`)
    o por su nombre. PURA (salvo `jt_id=None`, que lee la configuración)."""
    jid = jt_ship_company_id() if jt_id is None else int(jt_id or 0)
    return bool((jid and _entero(canal.get("shipCompanyId")) == jid)
                or "j&t" in _norm(canal.get("shippingCompanyName")))


def jt_excede_usa_otra() -> bool:
    """`TEMU_GUIAS_JT_EXCEDE_USA_OTRA` (nace APAGADA): si la caja excede los
    límites de J&T y Temu IGUAL ofrece J&T, ¿se compra la más barata de las
    demás? Apagada (la orden de Brandon del 1-oct, "J&T siempre; si no se
    encuentra en las opciones, la más barata"): se compra J&T —el árbitro es la
    cotización de Temu— y la caja lleva su aviso."""
    return bool(getattr(settings, "temu_guias_jt_excede_usa_otra", False))


def elegir_canal(respuesta: dict[str, Any],
                 preferencias: list[tuple[str, str]] | None = None,
                 jt_id: int | None = None, jt_excede: str | None = None) -> dict[str, Any]:
    """
    De la cotización, el canal a comprar. PURA (salvo los `None`, que leen la
    configuración). Regla de Brandon (1-oct): J&T SIEMPRE primero; sólo si J&T
    no se ofrece —o no es usable— se toma la más barata de las demás.

    `preferencias` va EN ORDEN DE PRIORIDAD: gana el primer renglón que tenga
    un canal usable; dentro de ese renglón, el más barato; empate → el de menos
    días. "*" es cualquier paquetería — y dentro de "*" J&T va primero
    (`TEMU_GUIAS_JT_SHIP_COMPANY_ID`; con 0, no). Entre los servicios de J&T
    (drop-off / recolección) gana el más barato salvo que un renglón fije el
    tipo ("J&T:Pickup,J&T,*"). No es usable el canal que pide datos extra
    (`infoNeeded`), sólo sirve contra entrega o cotiza en otra moneda. Nunca
    cambia sola a algo que no esté en la lista.

    `jt_excede` = por qué la caja excede los límites de J&T: con él, los
    canales de J&T dejan de ser usables aunque Temu los ofrezca (sólo lo pasa
    quien llama con `TEMU_GUIAS_JT_EXCEDE_USA_OTRA` encendida).

    Devuelve además `mas_barata` (la más barata de TODAS), `es_jt`,
    `porque_no_jt` (por qué no fue J&T cuando no lo fue, con el
    `unavailableReason` de Temu) y, por opción, sus `reglas` (`channelRules`:
    el único lugar donde Temu dice el límite real de cada canal).
    """
    prefs = preferencias_paqueteria() if preferencias is None else preferencias
    jid = jt_ship_company_id() if jt_id is None else int(jt_id or 0)
    opciones = []
    for c in (respuesta or {}).get("onlineChannelDtoList") or []:
        if not isinstance(c, dict):
            continue
        mxn = es_mxn(c.get("estimatedCurrencyCode"), c.get("estimatedAmount"))
        opciones.append({
            "channelId": _entero(c.get("channelId")),
            "shipCompanyId": _entero(c.get("shipCompanyId")),
            "shippingCompanyName": str(c.get("shippingCompanyName") or ""),
            "shipLogisticsType": str(c.get("shipLogisticsType") or ""),
            "estimatedAmount": str(c.get("estimatedAmount") or ""),
            "estimatedText": str(c.get("estimatedText") or ""),
            "moneda": (str(c.get("estimatedCurrencyCode") or "").strip().upper()
                       or ("MXN" if mxn else "?")),
            # En pesos o nada: un monto en otra moneda no se compara con los
            # topes ni con las demás opciones (ver `es_mxn`).
            "monto": _monto(c.get("estimatedAmount")) if mxn else None,
            "dias": list(_dias_texto(c.get("estimatedText"))),
            "pide_datos": list(c.get("infoNeeded") or []),
            "solo_cod": _entero(c.get("payWayCode")) == 2,
            "no_mxn": not mxn,
            "es_jt": es_jt(c, jid),
            "reglas": (str(c.get("channelRules"))[:300] if c.get("channelRules") else None),
        })
    no_disp = [{"shippingCompanyName": str(c.get("shippingCompanyName") or ""),
                "shipLogisticsType": str(c.get("shipLogisticsType") or ""),
                "shipCompanyId": _entero(c.get("shipCompanyId")),
                "channelId": _entero(c.get("channelId")),
                "es_jt": es_jt(c, jid),
                "motivo": str(c.get("unavailableReason") or "")[:200]}
               for c in (respuesta or {}).get("unavailableChannelDtoList") or []
               if isinstance(c, dict)]

    def _usable(o: dict[str, Any]) -> bool:
        return not (o["pide_datos"] or o["solo_cod"] or not o["channelId"]
                    or not o["shipCompanyId"] or o["no_mxn"] or (jt_excede and o["es_jt"]))

    def _clave(o: dict[str, Any]) -> tuple[float, float, float]:
        return (o["monto"] if o["monto"] is not None else math.inf, o["dias"][0], o["dias"][1])

    def _entra(o: dict[str, Any], emp: str, tipo: str) -> bool:
        if emp != "*" and not (_norm(emp) in _norm(o["shippingCompanyName"])
                               or (_fila_es_jt(emp) and o["es_jt"])):
            return False
        return not (tipo and tipo != "*" and _norm(tipo) != _norm(o["shipLogisticsType"]))

    def _nombre(o: dict[str, Any]) -> str:
        return " ".join(x for x in (o["shippingCompanyName"], o["shipLogisticsType"],
                                    o.get("estimatedAmount") or "") if x)

    # EL ORDEN ES PRIORIDAD: el primer renglón con un canal usable gana.
    elegido: dict[str, Any] | None = None
    fila: tuple[str, str] | None = None
    for emp, tipo in prefs:
        entran = [o for o in opciones if _entra(o, emp, tipo) and _usable(o)]
        if not entran:
            continue
        if emp == "*" and jid:
            entran = [o for o in entran if o["es_jt"]] or entran      # J&T primero
        elegido = min(entran, key=_clave)
        fila = (emp, tipo)
        break
    todas = sorted((o for o in opciones if _usable(o)), key=_clave)
    mas_barata = todas[0] if todas else None
    regla = ("J&T siempre primero; si no se ofrece o no es usable, la más barata de las demás "
             f"(TEMU_GUIAS_PAQUETERIA = {', '.join(':'.join(x for x in p if x) for p in prefs)}, en "
             "orden de prioridad)")
    nd_txt = "; ".join(f"{x['shippingCompanyName']} {x['shipLogisticsType']}: "
                       f"{x['motivo'] or 'sin motivo'}" for x in no_disp)
    if elegido is None:
        ofrecidas = ", ".join(_nombre(o) for o in opciones) or "ninguna"
        otra_moneda = sorted({o["moneda"] for o in opciones if o["no_mxn"]})
        return {"elegido": None, "opciones": opciones, "no_disponibles": no_disp,
                "mas_barata": mas_barata, "es_jt": False, "porque_no_jt": None, "regla": regla,
                "motivo": (f"la paquetería preferida ({', '.join(':'.join(p) for p in prefs)}) "
                           f"no se ofreció para esta caja; Temu ofreció: {ofrecidas}"
                           + (f" (cotizado en {', '.join(otra_moneda)}, no en MXN: no se "
                              "compara con los topes en pesos)" if otra_moneda else "")
                           + (f"; no disponibles: {nd_txt}" if nd_txt else ""))}
    porque: str | None = None
    if not elegido["es_jt"]:
        jt_ofr = [o for o in opciones if o["es_jt"]]
        jt_nd = [x for x in no_disp if x["es_jt"]]
        if any(_usable(o) for o in jt_ofr):
            porque = (f"TEMU_GUIAS_PAQUETERIA pone antes el renglón «{':'.join(x for x in fila if x)}»"  # type: ignore[union-attr]
                      f" y J&T ({', '.join(_nombre(o) for o in jt_ofr if _usable(o))}) no entra en él")
        elif jt_ofr:
            peros = []
            for o in jt_ofr:
                por = ([f"pide datos extra ({', '.join(str(x) for x in o['pide_datos'])})"]
                       if o["pide_datos"] else [])
                por += ["sólo contra entrega"] if o["solo_cod"] else []
                por += [f"cotiza en {o['moneda']}, no en MXN"] if o["no_mxn"] else []
                por += (["sin channelId o shipCompanyId"]
                        if not (o["channelId"] and o["shipCompanyId"]) else [])
                por += [f"la caja excede sus límites ({jt_excede})"] if jt_excede else []
                peros.append(f"{o['shipLogisticsType'] or 'J&T'}: {', '.join(por) or 'no usable'}")
            porque = f"J&T se ofreció pero no es usable ({'; '.join(peros)})"
        elif jt_nd:
            porque = ("J&T no está disponible para esta caja — Temu: "
                      + "; ".join(f"{x['shipLogisticsType'] or 'J&T'}: {x['motivo'] or 'sin motivo'}"
                                  for x in jt_nd))
        else:
            porque = "Temu no ofreció J&T para esta caja"
        porque += f": se usa la más barata de las demás ({_nombre(elegido)})"
    return {"elegido": elegido, "opciones": opciones, "no_disponibles": no_disp,
            "mas_barata": mas_barata, "motivo": None, "es_jt": bool(elegido["es_jt"]),
            "porque_no_jt": porque, "regla": regla}


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


def costo_de(caja: dict[str, Any]) -> float | None:
    """El costo cotizado (MX$) de la paquetería ELEGIDA para una caja, o None
    si no se sabe — incluido el que no está en PESOS (`es_mxn`): un None aquí
    es "compra manual", nunca un monto en otra moneda contra un tope en MX$.
    PURA."""
    el = ((caja.get("cotizacion") or {}).get("elegido") or {})
    if el.get("no_mxn") or (el.get("moneda") not in (None, "", "MXN")):
        return None
    m = el.get("monto")
    if m is None:
        if not es_mxn(None, el.get("estimatedAmount")):
            return None
        m = _monto(el.get("estimatedAmount"))
    try:
        return round(float(m), 2) if m is not None else None
    except (TypeError, ValueError):
        return None


def paqueteria_de(caja: dict[str, Any]) -> str | None:
    """"J&T express · Pickup" de la paquetería elegida para una caja. PURA."""
    el = ((caja.get("cotizacion") or {}).get("elegido") or {})
    txt = " · ".join(x for x in (str(el.get("shippingCompanyName") or "").strip(),
                                 str(el.get("shipLogisticsType") or "").strip()) if x)
    return txt or None


def porque_no_jt_de(caja: dict[str, Any]) -> str | None:
    """Por qué la caja NO va por J&T (None si va por J&T, o si no se cotizó). PURA."""
    return (caja.get("cotizacion") or {}).get("porque_no_jt") or None


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


def clave_composicion(contenido: dict[str, int]) -> str:
    """"A*1+B*4": la composición de una caja con varios SKUs, ordenada por SKU
    (como el `string_agg … collate "C"` de `_historial_candidatos`). PURA."""
    return "+".join(f"{s}*{int(q)}" for s, q in sorted((str(k), v) for k, v in contenido.items()))


def _leer_composicion(k: str) -> dict[str, int] | None:
    salida: dict[str, int] = {}
    for parte in str(k or "").split("+"):
        s, sep, q = parte.rpartition("*")
        if not sep or not s or not q.isdigit():
            return None
        salida[s] = salida.get(s, 0) + int(q)
    return salida or None


# El eco, fuera del historial: las ventas cuya guía compró este sistema.
_ECO_SQL = """
                and not exists (select 1 from ops.temu_guias_compras b
                                 where b.parent_order_sn = o.external_order_id
                                   and b.estado not in ('rechazada', 'no_enviada'))"""


def _historial_candidatos(skus: list[str], por_cantidad: int = 3,
                          por_sku: int = 12, *, familias: Iterable[str] | None = None,
                          composiciones: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Ventas de Temu YA SURTIDAS con guía propia (no compartida, no dividida):
    de ahí sale lo que se declaró al comprar su guía. Tres clases:
      · de UN solo SKU pedido (`skus`);
      · de UN solo SKU de la misma FAMILIA (`familias`, "MASC-0016": las
        variantes hermanas, escalón `temu_hermanas`);
      · de VARIOS SKUs con una composición pedida (`composiciones`,
        "A*1+B*4"): la caja de un PO con dos SKUs.
    El tope va POR SKU (o composición) dentro del SQL (antes era un `limit 600`
    global: un SKU con muchas ventas dejaba sin muestras a otro según qué más
    hubiera en el lote, y la vista previa y la compra veían historiales
    distintos).
    EL ECO SE QUITA AQUÍ, ANTES DEL TOPE (revisión del 1-oct): las ventas cuya
    guía compró ESTE sistema (fila en `ops.temu_guias_compras` que no quedó
    'rechazada' ni 'no_enviada') no son un dato de Temu. Si se quitaran después
    del `row_number`, cada compra automática ocuparía un lugar de las 3 más
    nuevas y desplazaría a las guías a mano: JUGU-0089-PLA pasaba de 1.00 a
    1.25 kg a media cola. Sin la tabla (migración 0061 sin aplicar) no hay eco
    que quitar: se consulta sin ese filtro.
    ⚠️ BLOQUEA (psycopg2): llamar desde un hilo. Sólo SELECT."""
    from services import supabase_db as sdb
    fams = sorted({f for f in (familias or []) if f and re.fullmatch(r"[A-Z]+-\d+", f)})
    comps = sorted({c for c in (composiciones or []) if c})
    if not skus and not fams and not comps:
        return []
    sql = (
        """/* tgc:historial */ with v as (
             select o.external_order_id po, o.guia, max(o.creado_at) creado_at,
                    count(*) lineas, min(i.sku::text) sku, sum(i.cantidad) q,
                    string_agg(i.sku::text || '*' || i.cantidad::text, '+'
                               order by i.sku::text collate "C") comp
               from ops.odoo_sale_orders o
               join ops.odoo_sale_order_items i using (canal, external_order_id)
              where o.canal = 'temu' and o.odoo_order_id is not null
                and coalesce(o.guia, '') <> '' and position('+' in o.guia) = 0
                and o.creado_at > now() - interval '120 days'/*ECO*/
              group by 1, 2),
           g as (select guia, count(*) n from v group by 1),
           c as (select v.po, case when v.lineas = 1 then v.sku else v.comp end k,
                        v.lineas, v.q, v.creado_at
                   from v join g using (guia)
                  where g.n = 1 and v.q > 0
                    and ((v.lineas = 1 and (v.sku = any(%(s)s::text[])
                                            or v.sku like any(%(f)s::text[])))
                         or (v.lineas > 1 and v.comp = any(%(k)s::text[])))),
           r1 as (select c.*, row_number() over (partition by k, q
                                                 order by creado_at desc, po) rq
                    from c),
           r2 as (select r1.*, row_number() over (partition by k
                                                  order by creado_at desc, po) rs
                    from r1 where rq <= %(pq)s)
           select po, k sku, lineas, q, creado_at from r2
            where rs <= %(ps)s
            order by k, creado_at desc, po""")
    params = {"s": list(skus), "f": [f"{x}-%" for x in fams], "k": comps,
              "pq": int(por_cantidad), "ps": int(por_sku)}
    try:
        filas = sdb.fetch_all(sql.replace("/*ECO*/", _ECO_SQL), params)
    except Exception as exc:  # noqa: BLE001
        if not es_tabla_ausente(exc):
            raise
        filas = sdb.fetch_all(sql.replace("/*ECO*/", ""), params)
    salida: list[dict[str, Any]] = []
    cuenta_q: dict[tuple[str, int], int] = {}
    cuenta_s: dict[str, int] = {}
    for f in filas:
        k = (str(f["sku"]), int(f["q"] or 0))
        if k[1] <= 0 or cuenta_q.get(k, 0) >= por_cantidad or cuenta_s.get(k[0], 0) >= por_sku:
            continue
        comp = _leer_composicion(k[0]) if int(f.get("lineas") or 1) > 1 else None
        if int(f.get("lineas") or 1) > 1 and not comp:
            continue
        cuenta_q[k] = cuenta_q.get(k, 0) + 1
        cuenta_s[k[0]] = cuenta_s.get(k[0], 0) + 1
        # En el orden del SQL: por SKU, de la MÁS RECIENTE a la más vieja (el
        # escalón "Temu · última guía" toma la primera).
        salida.append({"po": str(f["po"]), "sku": k[0], "cantidad": k[1],
                       "creado_at": f.get("creado_at"), "composicion": comp})
    return salida


def _items_kubera(pos: list[str]) -> dict[str, dict[str, int]]:
    """{PO: {sku: piezas}} que kubera guardó al registrar cada venta de Temu
    (`ops.odoo_sale_order_items`). Para restar del stock, A LO SEGURO, una
    venta cuyo detalle Temu no contestó. ⚠️ BLOQUEA. Sólo SELECT. LANZA."""
    from services import supabase_db as sdb
    if not pos:
        return {}
    filas = sdb.fetch_all(
        """/* tgc:items */ select external_order_id po, sku::text sku, sum(cantidad) piezas
             from ops.odoo_sale_order_items
            where canal = %(c)s and external_order_id = any(%(p)s::text[])
            group by 1, 2""", {"c": CANAL, "p": list(pos)})
    salida: dict[str, dict[str, int]] = {}
    for f in filas:
        s, n = str(f.get("sku") or "").strip(), int(f.get("piezas") or 0)
        if s and n > 0:
            salida.setdefault(str(f["po"]), {})[s] = n
    return salida


_UNIDAD_PESO_PUB = {"kg": 1.0, "g": 0.001, "lb": 0.45359237, "oz": 0.028349523125}
_UNIDAD_LADO_PUB = {"cm": 1.0, "mm": 0.1, "m": 100.0, "in": 2.54}


def medida_de_publicacion(fila: dict[str, Any]) -> dict[str, float] | None:
    """{peso_kg, largo_cm, ancho_cm, alto_cm} de UNA fila de
    `bg.local.goods.sku.list.query` (`weightInfo`, `volumeInfo`), o None si
    falta algo o la unidad no se sabe convertir (falla cerrado: una unidad mal
    leída es un peso 1,000 veces distinto). PURA."""
    w = fila.get("weightInfo") if isinstance(fila.get("weightInfo"), dict) else {}
    v = fila.get("volumeInfo") if isinstance(fila.get("volumeInfo"), dict) else {}
    fp = _UNIDAD_PESO_PUB.get(str(w.get("unit") or "").strip().lower())
    fd = _UNIDAD_LADO_PUB.get(str(v.get("unit") or "").strip().lower())
    peso = _num(w.get("weight"))
    lados = [_num(v.get(k)) for k in ("length", "width", "height")]
    if fp is None or fd is None or not peso or not all(lados):
        return None
    return {"peso_kg": round(peso * fp, 4), "largo_cm": round(lados[0] * fd, 2),  # type: ignore[operator]
            "ancho_cm": round(lados[1] * fd, 2), "alto_cm": round(lados[2] * fd, 2)}  # type: ignore[operator]


async def _medidas_publicacion(s: "_Sesion", ids: dict[str, int]) -> dict[str, dict[str, float]]:
    """{sku: medida por pieza} del paquete de la PUBLICACIÓN de Temu, por el
    skuId que trae el detalle de la orden: `bg.local.goods.sku.list.query`
    (termina en `query`: lectura, pasa el candado de /investigacion). Sólo se
    usa una fila cuyo `skuSn` ES el SKU (el skuId del detalle no se da por
    bueno a ciegas). Se guarda 6 h por skuId: el paquete de una publicación
    casi no cambia y la cuota es la de producción. LANZA si Temu no contesta."""
    salida: dict[str, dict[str, float]] = {}
    ahora = time.monotonic()
    pedir: dict[int, str] = {}
    for sku, kid in ids.items():
        k = _entero(kid)
        if not k:
            continue
        c = _PUB_CACHE.get(k)
        if c is None or ahora - c[0] >= _PUB_TTL:
            pedir[k] = sku
    lista = sorted(pedir)
    for i in range(0, len(lista), 100):
        lote = lista[i:i + 100]
        res = await s.llamar("bg.local.goods.sku.list.query",
                             {"skuIdList": lote, "pageNo": 1, "pageSize": 100})
        vistas: dict[int, dict[str, Any]] = {}
        for f in _lista(res, "skuList"):
            k = _entero(f.get("skuId"))
            if k:
                vistas[k] = {"sku": str(f.get("skuSn") or "").strip(), "medida": medida_de_publicacion(f)}
        for k in lote:
            _PUB_CACHE[k] = (ahora, vistas.get(k))
    for sku, kid in ids.items():
        c = _PUB_CACHE.get(_entero(kid) or -1)
        dato = c[1] if c else None
        if dato and dato.get("medida") and dato["sku"].upper() == str(sku).strip().upper():
            salida[sku] = dict(dato["medida"])
    return salida


def _medidas_almacen(skus: list[str]) -> dict[str, dict[str, Any]]:
    """Lo que almacén midió del producto empacado (Checklist), POR PIEZA. Trae
    la fila si tiene el PESO o las TRES medidas (valen por separado: con sólo
    el peso, ése es el nuestro y la caja sale del catálogo). ⚠️ BLOQUEA. SELECT."""
    from services import supabase_db as sdb
    if not skus:
        return {}
    filas = sdb.fetch_all(
        """/* tgc:medidas */ select sku::text sku, almacen_largo_cm largo_cm,
                  almacen_ancho_cm ancho_cm, almacen_alto_cm alto_cm, almacen_peso_kg peso_kg
             from core.products
            where sku = any(%(s)s::citext[])
              and (almacen_peso_kg is not null
                   or (almacen_largo_cm is not null and almacen_ancho_cm is not null
                       and almacen_alto_cm is not null))""", {"s": list(skus)})
    return {str(f["sku"]): {k: float(f[k]) for k in ("largo_cm", "ancho_cm", "alto_cm", "peso_kg")
                            if f.get(k) is not None}
            for f in filas}


def _medidas_catalogo(skus: list[str],
                      familias: Iterable[str] | None = None) -> dict[str, dict[str, float]]:
    """La fila del PACKING LIST (costing.costos_validados) de cada SKU:
    {sku: {largo_cm, ancho_cm, alto_cm, peso_kg?, costo_cbm?, piezas_por_caja?}}.
    ⚠️ Sus medidas pueden ser las de la PIEZA o las del cartón MASTER: lo dice
    `costo_cbm` (el flete por pieza: ÷ 7500 = el volumen por el que se pagó,
    `v_ref_de`) y, sin él, `piezas_por_caja` (`unitaria_de_catalogo`).
    `familias` ("MASC-0016"): también los SKUs de esas familias, para probar la
    guía de una variante HERMANA contra SU flete. Una fila sin medidas se trae
    igual (su flete sirve para probar la medida de Woo). ⚠️ BLOQUEA. SELECT.
    LANZA si falla."""
    from services import supabase_db as sdb
    fams = sorted({f for f in (familias or []) if f and re.fullmatch(r"[A-Z]+-\d+", f)})
    if not skus and not fams:
        return {}
    filas = sdb.fetch_all(
        """/* tgc:catalogo */ select sku::text sku, largo largo_cm, ancho ancho_cm, alto alto_cm,
                  peso peso_kg, costo_cbm, piezas_por_caja
             from costing.costos_validados
            where (sku = any(%(s)s::citext[]) or sku::text like any(%(f)s::text[]))""",
        {"s": list(skus), "f": [f"{x}-%" for x in fams]})
    salida: dict[str, dict[str, float]] = {}
    for f in filas:
        m: dict[str, float] = {}
        lados = {k: _num(f.get(k)) for k in ("largo_cm", "ancho_cm", "alto_cm")}
        if all(lados.values()):
            m.update({k: float(v) for k, v in lados.items()})  # type: ignore[arg-type]
        for k in ("peso_kg", "costo_cbm", "piezas_por_caja"):
            v = _num(f.get(k))
            if v:
                m[k] = v
        if m:
            salida[str(f["sku"])] = m
    return salida


_UNIDAD_PESO = {"kg": 1.0, "g": 0.001}
_UNIDAD_LADO = {"cm": 1.0, "mm": 0.1, "m": 100.0}


def _medidas_woo(skus: list[str]) -> dict[str, dict[str, float]]:
    """Peso y caja POR PIEZA de la ficha de Woo (`_weight`, `_length`,
    `_width`, `_height`), en kg y cm según las unidades de la tienda. Escalón
    `omnicanal_woo`. Sólo la ficha DEL SKU: una variación sin medidas propias
    NO hereda las del padre (el padre agrupa tallas y largos distintos: el
    ACC-0696 de 90, 120 y 140 cm). Un SKU en dos fichas que no coinciden no se
    usa. ⚠️ BLOQUEA (pymysql): llamar desde un hilo. Sólo SELECT. LANZA."""
    from services import wp_db
    if not skus:
        return {}
    P = wp_db._prefix()  # noqa: SLF001
    unidades = {str(f["option_name"]): str(f.get("option_value") or "").strip().lower()
                for f in wp_db._fetch_all(  # noqa: SLF001
                    f"""/* tgc:woo_unidades */ SELECT option_name, option_value FROM {P}options
                        WHERE option_name IN ('woocommerce_weight_unit',
                                              'woocommerce_dimension_unit')""")}
    # Sin la opción, Woo usa kg y cm (sus valores por omisión).
    fp = _UNIDAD_PESO.get(unidades.get("woocommerce_weight_unit") or "kg")
    fd = _UNIDAD_LADO.get(unidades.get("woocommerce_dimension_unit") or "cm")
    if fp is None or fd is None:
        raise ValueError(f"unidades de Woo que no se saben convertir: {unidades}")
    pedidos = {str(s).strip().lower(): str(s).strip() for s in skus if str(s).strip()}
    ph = ",".join(["%s"] * len(pedidos))
    filas = wp_db._fetch_all(  # noqa: SLF001
        f"""/* tgc:woo */ SELECT sk.meta_value AS sku, p.ID AS id,
                   MAX(CASE WHEN m.meta_key = '_weight' THEN m.meta_value END) AS peso,
                   MAX(CASE WHEN m.meta_key = '_length' THEN m.meta_value END) AS largo,
                   MAX(CASE WHEN m.meta_key = '_width'  THEN m.meta_value END) AS ancho,
                   MAX(CASE WHEN m.meta_key = '_height' THEN m.meta_value END) AS alto
              FROM {P}posts p
              JOIN {P}postmeta sk ON sk.post_id = p.ID AND sk.meta_key = '_sku'
              LEFT JOIN {P}postmeta m ON m.post_id = p.ID
                   AND m.meta_key IN ('_weight', '_length', '_width', '_height')
             WHERE p.post_type IN ('product', 'product_variation')
               AND p.post_status <> 'trash' AND sk.meta_value IN ({ph})
             GROUP BY p.ID, sk.meta_value""", tuple(pedidos.values()))
    por_sku: dict[str, list[dict[str, float | None]]] = {}
    for f in filas:
        sku = pedidos.get(str(f.get("sku") or "").strip().lower())
        if not sku:
            continue
        crudo = {"peso_kg": (_num(f.get("peso")), fp), "largo_cm": (_num(f.get("largo")), fd),
                 "ancho_cm": (_num(f.get("ancho")), fd), "alto_cm": (_num(f.get("alto")), fd)}
        if not any(v for v, _f in crudo.values()):
            continue                                   # ficha sin medidas: no opina
        # Incompleta se pasa tal cual: el escalón dice "incompleto" y no la usa.
        por_sku.setdefault(sku, []).append(
            {k: (round(v * fac, 4) if v else None) for k, (v, fac) in crudo.items()})
    salida: dict[str, dict[str, float]] = {}
    for sku, lista in por_sku.items():
        if len({json.dumps(m, sort_keys=True) for m in lista}) == 1:
            salida[sku] = lista[0]  # type: ignore[assignment]
    return salida


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


class CompraAbierta(RuntimeError):
    """La compra de la guía de esta venta está ABIERTA en la bitácora (en curso,
    sin saber si compró, etiqueta fallida, ya solicitada): no se sabe qué
    compró el panel, así que su orden de Odoo NO se crea todavía."""

    def __init__(self, order_id: str, estado: str):
        super().__init__(f"{order_id}: compra de guía '{estado}' sin conciliar")
        self.order_id = order_id
        self.estado = estado


def reparto_comprado(order_id: str) -> list[dict[str, Any]] | None:
    """
    [{almacen_id, sku, cantidad}] de la guía que compró EL PANEL para esa venta,
    o None si no la compró el panel (la compraron a mano, o no hay guía).
    ⚠️ BLOQUEA. LANZA si la bitácora no se puede leer: quien llama decide
    (`es_tabla_ausente` → no hay compras del panel).

    ⚠️ LANZA `CompraAbierta` si la fila está ABIERTA (revisión del 30-sep): con
    un 'desconocido' tras un timeout en el que Temu SÍ compró, la vuelta de
    guías veía el paquete en Temu y creaba la orden con un reparto
    RECALCULADO (otro almacén) mientras la guía salía de los de la compra.
    Hasta conciliarla no se sabe qué compró: no se crea.

    Primero lo que dijo Temu (`reparto_real`); si le falta algo, lo planeado
    (`reparto`), que es exactamente el payload que se mandó.
    """
    fila = _reclamos_de([str(order_id)]).get(str(order_id))
    if fila and fila.get("estado") in ESTADOS_ABIERTOS:
        raise CompraAbierta(str(order_id), str(fila.get("estado")))
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


def entrega_comprada(order_id: str) -> dict[str, Any] | None:
    """{fecha_envio (date|str), horas} de la guía que compró EL PANEL para esa
    venta —el día en que hay que entregar el paquete a la paquetería, que con
    el límite de Temu puede ser 24 h y no la costumbre de +2 días—, o None.
    ⚠️ BLOQUEA. NUNCA LANZA: es un dato para el almacén (nota de la orden,
    Excel de guías del día), no una decisión."""
    try:
        fila = _reclamos_de([str(order_id)]).get(str(order_id))
    except Exception as exc:  # noqa: BLE001
        log.debug("entrega_comprada(%s): %s", order_id, str(exc)[:120])
        return None
    if not fila or fila.get("estado") not in ESTADOS_HECHOS or not fila.get("fecha_envio"):
        return None
    return {"fecha_envio": fila.get("fecha_envio"), "horas": _entero(fila.get("horas")),
            "tarde": fue_tarde(fila)}


def fue_tarde(fila: dict[str, Any]) -> bool:
    """¿La guía de esa fila de la bitácora se compró después del límite de envío
    de Temu? Lo dice su `reparto` (la columna que ya existe: la migración 0061
    no cambia). PURA."""
    rep = fila.get("reparto")
    if isinstance(rep, str):
        try:
            rep = json.loads(rep)
        except ValueError:
            return False
    return any(isinstance(x, dict) and x.get("tarde") for x in (rep or []))


def texto_entrega(entrega: dict[str, Any] | None) -> str | None:
    """"jueves 01-10-2026 (24 h)" para el almacén — y, si se compró después del
    límite de Temu, "… · comprada TARDE (límite de Temu ya vencido)". PURA."""
    if not entrega or not entrega.get("fecha_envio"):
        return None
    f = entrega["fecha_envio"]
    try:
        d = f if isinstance(f, date) else date.fromisoformat(str(f)[:10])
    except ValueError:
        return None
    h = entrega.get("horas")
    return (f"{_DIAS[d.weekday()]} {d:%d-%m-%Y}" + (f" ({h} h)" if h else "")
            + (f" · {TEXTO_TARDE}" if entrega.get("tarde") else ""))


def entregas_compradas(pos: list[str]) -> dict[str, dict[str, Any]]:
    """{PO: {fecha_envio, horas, tarde}} de las guías que compró el panel. ⚠️
    BLOQUEA. Sólo SELECT. LANZA (quien llama decide; la tabla puede no existir)."""
    filas = _reclamos_de(sorted({str(p) for p in pos if p}))
    return {po: {"fecha_envio": f.get("fecha_envio"), "horas": _entero(f.get("horas")),
                 "tarde": fue_tarde(f)}
            for po, f in filas.items()
            if f.get("estado") in ESTADOS_HECHOS and f.get("fecha_envio")}


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


def compras_recientes(dias: int = 3, limite: int = 500) -> list[dict[str, Any]]:
    """Las filas de la bitácora que se movieron en los últimos `dias` (compras
    del panel, aprobadas a mano o automáticas). Para los topes del día y el
    panel de la compra automática. ⚠️ BLOQUEA. Sólo SELECT. LANZA (tabla
    ausente incluida: `es_tabla_ausente`)."""
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        """/* tgc:recientes */ select parent_order_sn, estado, reclamo, grupo, aprobado_por,
                  send_type, payload, reparto, reparto_real, package_sn, fecha_envio, horas,
                  codigo, motivo, intentos, creado_at, actualizado_at
             from ops.temu_guias_compras
            where actualizado_at > now() - make_interval(days => %(d)s)
            order by actualizado_at desc
            limit %(l)s""", {"d": max(1, int(dias)), "l": max(1, int(limite))})
    return [dict(f) for f in filas]


def pendientes_auto() -> list[dict[str, Any]]:
    """Las compras AUTOMÁTICAS cuya etiqueta sigue 'pendiente' (en aplicación en
    Temu), de cualquier antigüedad. No están en `ESTADOS_ABIERTOS` (cuentan
    como hechas para el planeador), pero mientras Temu no las resuelva la
    compra automática no se libera: una etiqueta que termina FALLIDA no se
    puede esconder con un clic. ⚠️ BLOQUEA. Sólo SELECT. LANZA."""
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        """/* tgc:pendientes_auto */ select parent_order_sn, estado, reclamo, aprobado_por,
                  codigo, motivo, actualizado_at, reparto
             from ops.temu_guias_compras
            where estado = 'pendiente' and aprobado_por like %(p)s
            order by actualizado_at asc
            limit 200""", {"p": "auto%"})
    return [dict(f) for f in filas]


async def guias_por_paquete(pos: list[str], s: "_Sesion | None" = None) -> dict[str, str]:
    """{packageSn: guía} de `bg.order.unshipped.package.get` —la fuente que usa
    el refresco de guías— para ESTAS ventas. Es el respaldo de la verificación
    de la compra automática cuando `shipment.result.get` no trae
    `trackingNumber` (campo "por verificar" en vivo). LANZA si Temu no
    contesta."""
    s = s or _Sesion(8, 60)
    salida: dict[str, str] = {}
    lista = sorted({str(p) for p in pos if p})
    for i in range(0, len(lista), 20):
        lote = lista[i:i + 20]
        for pagina in range(1, 6):
            res = await s.llamar("bg.order.unshipped.package.get",
                                 {"parentOrderSnList": lote, "pageNumber": pagina,
                                  "pageSize": 20})
            filas = _lista(res, "unshippedPackage")
            for q in filas:
                psn = str(q.get("packageSn") or "").strip()
                g = str(q.get("trackingNumber") or "").strip()
                if psn and g:
                    salida[psn] = g
            if len(filas) < 20:
                break
    return salida


def abiertas() -> list[dict[str, Any]]:
    """Las compras que BLOQUEAN (en curso, sin saber si compró, etiqueta fallida,
    ya solicitada), de cualquier antigüedad. ⚠️ BLOQUEA. Sólo SELECT. LANZA."""
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        """/* tgc:abiertas */ select parent_order_sn, estado, reclamo, aprobado_por, codigo,
                  motivo, actualizado_at
             from ops.temu_guias_compras
            where estado = any(%(e)s)
            order by actualizado_at asc
            limit 200""", {"e": sorted(ESTADOS_ABIERTOS)})
    return [dict(f) for f in filas]


# Lo que ya se midió de guías históricas: no cambia (una guía comprada no se
# re-declara), así que se guarda por PO un buen rato para no gastar cuota.
_HIST_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_HIST_TTL = 6 * 3600.0

# EL DETALLE YA LEÍDO de cada venta (lo de `leer_venta`: SIN datos del
# comprador), {PO: (monotonic, venta)}. Revisión del 30-sep: con ~93 ventas en
# espera, cada compra automática leía el detalle de TODAS dos veces (el plan del
# job y el re-plan de `comprar()`), ~220 llamadas con la cuota de producción,
# y un solo error pasajero en cualquiera de las viejas dejaba "incierto" el lote
# entero. Sólo se REUSA para las ventas que NO se van a comprar en esa llamada
# (las que sólo se leen para descontar su stock) y sólo cuando el plan es de la
# compra (`solo` o `cotizar`): lo que se compra se lee SIEMPRE fresco, y
# `comprar()` lo relee otra vez justo antes de `shipment.create`. La vista
# previa del panel lee todo fresco, como siempre (y alimenta este caché).
#   · se reusa sin leer si tiene menos de `_DET_TTL`;
#   · si la lectura FALLA, se reusa hasta `_DET_RESPALDO` (con aviso) en vez de
#     dejar la venta ilegible. Un detalle viejo de otra venta sólo sirve para
#     descontar stock: si mientras tanto se canceló, se descuenta de más (lo
#     conservador); si le compraron la guía a mano, `unshipped`/`label.list`
#     —que se leen siempre— lo dicen.
#   · 20 min y no 10 (30-sep): con hasta 10 compras por vuelta, las últimas de
#     la vuelta releían la cola entera en su re-plan porque el caché del plan
#     del job ya había vencido.
_DET_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DET_TTL = 1200.0
_DET_RESPALDO = 3600.0


def _podar_detalles() -> None:
    ahora = time.monotonic()
    for k in [k for k, (ts, _v) in _DET_CACHE.items() if ahora - ts > _DET_RESPALDO]:
        _DET_CACHE.pop(k, None)


async def _historial_empaque(s: _Sesion, skus: list[str], *,
                             familias: Iterable[str] | None = None,
                             composiciones: Iterable[str] | None = None) -> dict[str, Any]:
    """Muestras de peso y caja declaradas en guías ya compradas: del mismo SKU,
    de sus variantes hermanas (`familias`) y de cajas con varios SKUs
    (`composiciones`). Nunca lanza: sin historial, esos escalones no tienen
    datos y se prueba el siguiente."""
    info: dict[str, Any] = {"muestras": [], "fuente": None, "errores": {}, "candidatos": 0}
    try:
        cand = await asyncio.to_thread(
            lambda: _historial_candidatos(skus, familias=list(familias or []),
                                          composiciones=list(composiciones or [])))
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
                                 "parent_order_sn": c["po"], "creado_at": c.get("creado_at"),
                                 "composicion": c.get("composicion"),
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


# Cuántas ventas en espera puede planear una vista previa (antes 60). El
# default sigue siendo `temu_guias_plan_limite` (20).
_LIMITE_MAX = 120


def limite_max_auto() -> int:
    """Cuántas ventas en espera puede planear la COMPRA AUTOMÁTICA (`cotizar`):
    la cola ENTERA (`TEMU_GUIAS_COLA_MAX`), no las 120 de la vista previa del
    panel — antes lo que pasaba de 120 quedaba "fuera del alcance del plan",
    es decir, sin comprarse nunca."""
    return max(_LIMITE_MAX, _cola_max())


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
                     medidas: dict[str, dict[str, Any]] | None = None,
                     cotizar: Iterable[str] | None = None) -> dict[str, Any]:
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
    `cotizar` = sólo se cotizan (en Temu) los grupos que contienen alguno de
    esos PO; los demás se leen para descontar su stock y salen "no se cotizó".
    Lo usa la compra automática: no gasta cuota cotizando ventas anteriores al
    corte, que nunca va a comprar.
    """
    # La compra automática (`cotizar`) puede planear la cola ENTERA; la vista
    # previa del panel, hasta 120 (es una petición: el proxy la cortaría).
    tope_lim = limite_max_auto() if cotizar is not None else _LIMITE_MAX
    lim = _limite_omision() if limite is None else max(1, min(tope_lim, int(limite)))
    if lim > 60:
        # "¿Se puede comprar la guía de TODAS?" (Brandon, 30-sep, con ~92 en
        # espera): más de 60 ventas piden más lecturas (detalle + cotización por
        # caja ≈ 3 por venta) y más tiempo. Sólo se sube cuando se pide; lo que
        # no alcance a leerse sale en `no_leidas`, nunca en silencio.
        tope_llamadas = max(tope_llamadas, 3 * lim + 30)
        segundos_max = max(segundos_max, 270.0, 1.5 * lim)
    try:
        med = leer_medidas(medidas)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": f"medidas inválidas: {exc}", "grupos": []}
    solo_cot = frozenset(str(x) for x in cotizar) if cotizar is not None else None
    clave = (lim, solo, json.dumps(med, sort_keys=True) if med else None,
             tuple(sorted(solo_cot)) if solo_cot is not None else None)
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
            res = await _plan(lim, solo, ahora, tope_llamadas, segundos_max, emitida, med,
                              solo_cot)
        except Exception as exc:  # noqa: BLE001 — la vista previa nunca tumba nada
            log.exception("plan_guias falló")
            res = {"ok": False, "error": str(exc)[:300], "grupos": []}
        if usar_cache and emitida is None and res.get("ok"):
            _PLAN_CACHE.update(ts=time.monotonic(), clave=clave, res=res)
        return res


def tiene_guia(v: dict[str, Any]) -> bool:
    """¿La venta YA tiene paquete o etiqueta en Temu (comprada a mano, o una
    etiqueta en cualquier estado)? Lo dicen el detalle, `unshipped` y
    `label.list`. PURA."""
    return bool(v.get("paquetes") or v.get("paquete_sin_numero")
                or any(("YA tiene paquete" in str(b)) or (" ya muestra " in str(b))
                       for b in v.get("bloqueos") or []))


def consumo_ambos(renglones: Iterable[dict[str, Any]], productos: dict[str, int],
                  mapa: dict[int, str]) -> dict[int, dict[int, int]]:
    """{product_id: {almacén: piezas}} con las piezas de `renglones` restadas de
    TODOS los almacenes: lo que no se sabe de cuál sale, A LO SEGURO (la misma
    regla que la demanda de otros canales). PURA."""
    total: dict[str, int] = {}
    for r in renglones or []:
        s = str(r.get("sku") or "").strip()
        n = int(r.get("cantidad") or r.get("quantity") or 0)
        if s and n > 0:
            total[s] = total.get(s, 0) + n
    salida: dict[int, dict[int, int]] = {}
    for s, n in total.items():
        pid = productos.get(s)
        if pid is not None:
            salida[pid] = {wid: n for wid in mapa}
    return salida


def _sumar_consumo(a: dict[int, dict[int, int]], b: dict[int, dict[int, int]]) -> dict[int, dict[int, int]]:
    salida = {pid: dict(por) for pid, por in a.items()}
    for pid, por in b.items():
        for wid, n in por.items():
            salida.setdefault(pid, {})[wid] = salida.get(pid, {}).get(wid, 0) + n
    return salida


# Cuánto se espera a una orden que Temu agrupa con las de la cola pero que
# todavía no entra a ella (recién vendida): después, el grupo se compra sin ella.
_FUERA_ESPERA_H = 2.0


async def _plan(lim: int, solo: str | None, ahora: datetime | None,
                tope_llamadas: int, segundos_max: float, emitida: int | None,
                medidas: dict[str, dict[str, float]],
                solo_cot: frozenset[str] | None = None) -> dict[str, Any]:
    from services import temu

    momento = ahora or datetime.now(timezone.utc)
    emitida = int(emitida) if emitida is not None else int(momento.timestamp())
    ttl = _aprobacion_min()
    vence = datetime.fromtimestamp(emitida + ttl * 60, tz=timezone.utc).astimezone(ZONA)
    fuentes, fuentes_error = fuentes_medida()
    if fuentes_error:
        log.warning("plan de guías Temu: %s", fuentes_error)
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
                      "hábil. shipLaterLimitTime = 24 × días naturales (24-96 h). Nunca "
                      "después del límite de envío de Temu: si lo rebasa, el mayor plazo que "
                      "lo cumpla en día hábil (sábado sólo con TEMU_GUIAS_SABADO_ALTERNO: "
                      + ("encendida" if sabado_alterno() else "apagada")
                      + "). Si ninguno lo cumple (el límite ya pasó), la guía se compra IGUAL "
                        "con el plazo más corto en día hábil y queda marcada 'comprada TARDE'; "
                        "sin límite de Temu, también el más corto. Ver 'limites'."),
            "reparto": ("odoo_ventas.planear_almacenes (la de Automatización): un almacén que "
                        "cubra todo; si no, por SKU. La cola se lee ENTERA y se descuenta de la "
                        "más vieja a la más nueva; lo ya comprado sale de su almacén real, y lo "
                        "de otros canales —o lo que no se sabe de qué almacén sale— se resta de "
                        "los dos almacenes."),
            "empaque": ("la medida capturada en el panel manda. Si no: EL MENOR CREÍBLE entre lo "
                        "nuestro y lo de Temu (opinan: " + ", ".join(texto_fuentes(fuentes)) + "). "
                        "PESO = min(nuestro por pieza × piezas, guía de Temu de esas piezas, "
                        + ("guía de 1 pieza × piezas (extrapolada), " if guia_x1_por_piezas() else "")
                        + "publicación × piezas), con piso de "
                        f"{piso_peso():g} kg. CAJA = la menor (la que cabe en J&T y paga menos) "
                        "entre la nuestra —pieza unitaria × piezas en la mejor rejilla; si la fila "
                        "trae el cartón master (≥ 3 veces el volumen del flete) se reparte como en "
                        "Costos— y la de Temu, que sólo cuenta si las piezas caben en ella y no es "
                        "el master copiado. Varios SKUs: suma de pesos y envolvente mínima. Las "
                        "guías que compró este sistema no cuentan como dato de Temu. Cordura: ≤ 30 "
                        "kg por pieza salvo medición real, densidad 10-4,000 kg/m³, ≤ 70 kg y ≤ 300 "
                        f"cm por caja. Límites de J&T: {_limites_txt(limites_jt())} (una caja que "
                        "no cabe se cotiza igual, con aviso). Odoo nunca."),
            "paqueteria": ("J&T SIEMPRE primero; si no se ofrece o no es usable (pide datos, contra "
                           "entrega, otra moneda), la más barata de las demás. TEMU_GUIAS_PAQUETERIA "
                           "va en orden de prioridad: gana el primer renglón con un canal usable; "
                           "dentro, el más barato; empate → menos días."),
            "combinado": ("mismo almacén → una caja (sendType 2); almacenes mezclados → se "
                          "separa por almacén; un PO que cruza almacenes va solo (sendType 1). "
                          "El grupo se aprueba y se compra ENTERO; un PO del grupo que ya tiene "
                          "guía sale de él y los demás se compran entre sí."),
            "aprobacion": (f"la huella firma payloads + fecha de envío + costo por caja + "
                           f"momento de la vista previa, y vence a los {ttl} min."),
        },
        "grupos": [], "resumen": {}, "llamadas_temu": 0,
        "fuentes_medida": {"escalera": [{"id": t_, "txt": t_} for t_ in texto_fuentes(fuentes)],
                           "error": fuentes_error,
                           "regla": "el menor creíble entre lo nuestro y lo de Temu"},
        "limites_jt": limites_jt(),
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

    # 4 · El detalle de cada venta leída. Lo que se va a COMPRAR (el grupo de
    #     `solo`, los grupos con algún PO de `cotizar`) siempre fresco; lo que
    #     sólo se lee para descontar su stock puede venir del caché cuando el
    #     plan es de la compra (ver `_DET_CACHE`).
    reusar = solo is not None or solo_cot is not None
    frescos = set(objetivo or []) | {po for g in leer if solo_cot is not None
                                     and any(x in solo_cot for x in g) for po in g}
    _podar_detalles()
    ventas: dict[str, dict[str, Any]] = {}
    r["detalles_reusados"] = 0
    for po in pos:
        guardado = _DET_CACHE.get(po)
        edad = (time.monotonic() - guardado[0]) if guardado else None
        puede = reusar and po not in frescos and guardado is not None
        if puede and edad is not None and edad < _DET_TTL:
            ventas[po] = copy.deepcopy(guardado[1])
            r["detalles_reusados"] += 1
            continue
        try:
            det = await s.llamar("bg.order.detail.v2.get", {"parentOrderSn": po})
            ventas[po] = leer_venta(po, det or {})
            _DET_CACHE[po] = (time.monotonic(), copy.deepcopy(ventas[po]))
        except Exception as exc:  # noqa: BLE001
            if puede and edad is not None and edad < _DET_RESPALDO:
                ventas[po] = copy.deepcopy(guardado[1])
                ventas[po]["avisos"] = list(ventas[po].get("avisos") or []) + [
                    f"detalle de hace {edad / 60:.0f} min: Temu no contestó ahora "
                    f"({_texto_error(exc)[:80]})"]
                r["detalles_reusados"] += 1
                continue
            ventas[po] = {"parent_order_sn": po, "estado": None, "estado_txt": "?",
                          "renglones": [], "avisos": [], "limite_envio_ts": None,
                          "limite_envio": None, "venta_ts": None, "venta": None,
                          "cubre_plataforma": False,
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

    # 5b · Un PO del grupo que YA tiene guía (comprada a mano, o con etiqueta en
    #      Temu) no bloquea a los demás (antes el grupo entero quedaba "compra
    #      manual"): sale del grupo —queda solo, "ya tiene guía"— y el resto se
    #      compra entre sí. Va PRIMERO: sus piezas ya salieron. Lo que compró
    #      el PANEL no se separa (ya cuenta como hecho dentro de su grupo), ni
    #      una compra ABIERTA (bloquea a su grupo hasta conciliarla).
    con_guia = {po for po, v in ventas.items() if tiene_guia(v)
                and (bitacora.get(po) or {}).get("estado") not in (ESTADOS_HECHOS | ESTADOS_ABIERTOS)}
    partido: list[list[str]] = []
    for g in leer:
        if len(g) > 1 and con_guia & set(g) and not set(g) <= con_guia:
            partido.extend([po] for po in g if po in con_guia)
            partido.append([po for po in g if po not in con_guia])
        else:
            partido.append(g)
    leer = partido
    objetivo = next((g for g in leer if solo in g), None) if solo else None

    # 5c · Los que Temu agrupa con los de la cola pero NO están en ella: ¿les
    #      falta guía? Enviados, cancelados o con paquete → no cuentan. Por
    #      enviar y sin paquete: recién vendidos (< 2 h) se esperan —van a
    #      entrar a la cola—; más viejos, el grupo se compra SIN ellos (su guía
    #      va aparte) en vez de quedarse "revísalo a mano" para siempre.
    hechas_bit = {x for x, f in bitacora.items() if f.get("estado") in ESTADOS_HECHOS}
    fuera_pos = sorted({x for po in pos for x in (grupos_temu.get(po) or ())}
                       - set(pos) - hechas_bit)
    fuera_info: dict[str, dict[str, Any]] = {}
    for x in fuera_pos:
        if len(fuera_info) >= 40:
            fuera_info[x] = {"estado": "ilegible", "txt": "no se alcanzó a leer"}
            continue
        try:
            vx = leer_venta(x, await s.llamar("bg.order.detail.v2.get", {"parentOrderSn": x}) or {})
        except Exception as exc:  # noqa: BLE001
            fuera_info[x] = {"estado": "ilegible", "txt": _texto_error(exc)[:100]}
            continue
        if vx.get("estado") != 2 or vx.get("paquetes") or vx.get("paquete_sin_numero"):
            fuera_info[x] = {"estado": "no_cuenta", "txt": vx.get("estado_txt")}
            continue
        vts = _entero(vx.get("venta_ts"))
        edad_h = (momento.timestamp() - vts) / 3600.0 if vts else None
        fuera_info[x] = {"estado": ("espera" if edad_h is not None and edad_h < _FUERA_ESPERA_H
                                    else "sin_ella"),
                         "edad_h": round(edad_h, 1) if edad_h is not None else None}
    r["fuera_de_cola"] = {x: i.get("estado") for x, i in fuera_info.items()}

    # 5d · Las ventas cuyo detalle no se pudo leer: sus renglones, de kubera
    #      (lo que se registró al entrar la venta), para restar sus piezas de
    #      los DOS almacenes en vez de dejar incierto todo lo que viene después.
    ilegibles = [po for po in pos if ventas[po].get("ilegible")]
    items_ileg: dict[str, dict[str, int]] = {}
    if ilegibles:
        try:
            items_ileg = await asyncio.to_thread(_items_kubera, ilegibles)
        except Exception as exc:  # noqa: BLE001
            r["items_error"] = str(exc)[:160]

    # 6 · Stock de Odoo, una lectura para todo el lote.
    skus = sorted({ren["sku"] for v in ventas.values() for ren in v["renglones"]}
                  | {s_ for it in items_ileg.values() for s_ in it})
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
    r["ajena_error"] = ajena_error

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
    cotizados = [g for g in leer if (objetivo is None or g == objetivo)
                 and (solo_cot is None or any(po in solo_cot for po in g))]
    skus_cot = sorted({ren["sku"] for g in cotizados for po in g for ren in ventas[po]["renglones"]})
    medidas_alm: dict[str, dict[str, Any]] = {}
    try:
        medidas_alm = await asyncio.to_thread(_medidas_almacen, skus_cot)
    except Exception as exc:  # noqa: BLE001
        r["medidas_error"] = str(exc)[:200]
    # Las variantes hermanas (misma familia): su historial y SU catálogo (para
    # reconocer una guía de hermana que copió la caja de catálogo). Las familias
    # recicladas no se piden: sus variantes no son referencia.
    familias = (sorted({f for f in (familia(x) for x in skus_cot)
                        if f and f not in FAMILIAS_RECICLADAS})
                if "temu_hermanas" in fuentes else [])
    catalogo: dict[str, dict[str, float]] = {}
    catalogo_leido = True
    try:
        catalogo = await asyncio.to_thread(_medidas_catalogo, skus_cot, familias)
    except Exception as exc:  # noqa: BLE001
        catalogo_leido = False
        r["catalogo_error"] = str(exc)[:200]
    # Lo que omnicanal tiene POR PIEZA (packing list y Woo). Sólo se lee lo que
    # la variable deja opinar: con la estricta, Woo ni se consulta. Si una
    # lectura falla, esa fuente no opina (queda lo demás). El packing sirve con
    # peso O con medidas: desde el 1-oct el peso y la caja se eligen aparte.
    omnicanal: dict[str, dict[str, Any]] = {}
    if "omnicanal_packing" in fuentes:
        for sku_, c in catalogo.items():
            if c.get("peso_kg") or c.get("largo_cm"):
                omnicanal.setdefault(sku_, {})["packing"] = c
    if "omnicanal_woo" in fuentes and skus_cot:
        try:
            for sku_, c in (await asyncio.to_thread(_medidas_woo, skus_cot)).items():
                omnicanal.setdefault(sku_, {})["woo"] = c
        except Exception as exc:  # noqa: BLE001
            r["woo_error"] = str(exc)[:200]
    # Las cajas con varios SKUs que probablemente se armen (cada PO y cada
    # grupo): su historial también.
    comps: set[str] = set()
    for g in cotizados:
        total: dict[str, int] = {}
        for po in g:
            propio: dict[str, int] = {}
            for ren in ventas[po]["renglones"]:
                propio[ren["sku"]] = propio.get(ren["sku"], 0) + int(ren["cantidad"])
                total[ren["sku"]] = total.get(ren["sku"], 0) + int(ren["cantidad"])
            if len(propio) > 1:
                comps.add(clave_composicion(propio))
        if len(total) > 1:
            comps.add(clave_composicion(total))
    hist = await _historial_empaque(s, skus_cot, familias=familias, composiciones=sorted(comps))
    # EL ECO —las guías que compró ESTE sistema (bitácora 0061): declararon lo
    # que esta misma regla calculó, no son un dato de Temu— ya viene QUITADO del
    # historial dentro de su SQL, antes del tope de 3 por (SKU, piezas)
    # (`_historial_candidatos`; la relectura que se hacía aquí llegaba tarde:
    # las compras automáticas ya habían desplazado a las guías a mano). Si esa
    # lectura falla no hay muestras (`errores.kubera`) y la compra automática no
    # compra con el plan a medias (`temu_guias_auto.plan_incompleto`).
    r["historial"] = {"fuente": hist["fuente"], "candidatos": hist["candidatos"],
                      "muestras": len(hist["muestras"]), "errores": hist["errores"],
                      "familias": familias, "composiciones": len(comps)}
    # El paquete de la PUBLICACIÓN de Temu: es "el peso declarado en Temu" de la
    # regla de Brandon (1-oct), así que se pide el de TODOS los SKUs que se
    # cotizan (antes sólo el de los que omnicanal no cubría): una lectura por
    # cada 100, con caché de 6 h. Si falla, la publicación no opina.
    publicacion: dict[str, dict[str, float]] = {}
    if "temu_publicacion" in fuentes:
        ids_pub: dict[str, int] = {}
        for g in cotizados:
            for po in g:
                for ren in ventas[po]["renglones"]:
                    if ren.get("skuId"):
                        ids_pub.setdefault(ren["sku"], int(ren["skuId"]))
        pedir = dict(ids_pub)
        if pedir:
            try:
                publicacion = await _medidas_publicacion(s, pedir)
            except Exception as exc:  # noqa: BLE001
                r["publicacion_error"] = _texto_error(exc)[:200]
        r["publicacion"] = {"pedidas": len(pedir), "con_paquete": len(publicacion)}

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
            extra.append("una venta anterior del lote no se pudo leer ni en Temu ni en kubera: "
                         "el stock que queda es incierto — se reintenta la vuelta siguiente")
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
            fuera = (set(g_temu) - set(miembros) - con_guia
                     - {x for x in g_temu if (bitacora.get(x) or {}).get("estado") in ESTADOS_HECHOS})
            for x in sorted(fuera):
                inf = fuera_info.get(x) or {"estado": "ilegible", "txt": "no se leyó"}
                if inf["estado"] == "no_cuenta":
                    continue
                if inf["estado"] == "sin_ella":
                    gs["avisos"].append(
                        f"Temu agrupa este envío con {x}, que sigue por enviar y no está en la cola "
                        f"de espera (vendida hace {inf.get('edad_h') or '?'} h): se compra SIN ella "
                        "— su guía va aparte")
                elif inf["estado"] == "espera":
                    extra.append(f"Temu agrupa este PO con {x}, vendida hace {inf.get('edad_h')} h, "
                                 "que todavía no entra a la cola de espera: se reintenta sola "
                                 f"(se espera hasta {_FUERA_ESPERA_H:g} h para comprarlas juntas)")
                else:
                    extra.append(f"no se pudo leer {x}, que Temu agrupa con este PO "
                                 f"({inf.get('txt')}): se reintenta sola")

        # 10a · Lo ya comprometido: guías compradas (a mano o por el panel) desde
        #       su almacén REAL, y compras abiertas desde lo que se reclamó. Lo
        #       que no se sabe de qué almacén sale, de los DOS (a lo seguro).
        resto: dict[str, dict[str, Any] | None] = {}
        incierto_aqui = False
        for po in miembros:
            v = ventas[po]
            f = bitacora.get(po) or {}
            if f.get("estado") in (ESTADOS_HECHOS | ESTADOS_ABIERTOS):
                # La compró (o la está comprando) el PANEL: la bitácora sabe
                # qué salió de dónde (lo real de result.get, o lo reclamado).
                c = consumo_de_reparto(f, productos)
                prob: list[str] = []
                if not c and v.get("renglones"):
                    prob = ["la bitácora no dice de qué almacén sale su compra"]
                    rv: dict[str, Any] = v
                else:
                    rv = {**v, "renglones": []}
            elif v.get("paquetes") or v.get("paquete_sin_numero"):
                # Guía comprada A MANO: su almacén real, de shipment.result.get.
                if paquetes_error:
                    c, rv, prob = {}, v, [f"shipment.result.get falló: {paquetes_error}"]
                else:
                    c, rv, prob = consumo_de_paquetes(v, productos, info_paquetes, mapa_inv)
            elif v.get("ilegible"):
                # Sin detalle: sus renglones de kubera, de los DOS almacenes.
                propios = items_ileg.get(po)
                c = consumo_ambos([{"sku": k, "cantidad": n} for k, n in (propios or {}).items()],
                                  productos, mapa)
                if propios:
                    gs["avisos"].append(f"{po}: Temu no dio su detalle; sus piezas (según kubera) se "
                                        "restan de los dos almacenes")
                else:
                    incierto_aqui = True
                if c:
                    descontar(restante, c)
                    gs["apartado"] = True
                resto[po] = None
                continue
            else:
                resto[po] = v
                continue
            if prob:
                # No se sabe de qué almacén sale lo que ninguna caja conocida
                # cubre: se resta de LOS DOS en vez de dejar incierto el lote.
                c = _sumar_consumo(c, consumo_ambos(rv.get("renglones") or [], productos, mapa))
                gs["avisos"].append(f"{po}: no se sabe de qué almacén sale su guía ya comprada "
                                    f"({'; '.join(prob)[:160]}): sus piezas se restan de los dos "
                                    "almacenes")
                rv = {**rv, "renglones": []}
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
        lote_incierto = lote_incierto or incierto_aqui
        gs["regla"] = plan.get("regla") or gs.get("regla")
        gs["cobertura"] = plan.get("cobertura")
        gs["stock"] = {s_: foto_inicial.get(s_, {}) for s_ in
                       sorted({ren["sku"] for v in g_todas for ren in v["renglones"]})}
        gs["motivos"] = extra + plan["motivos"]
        # LA FECHA DEL GRUPO, con el límite de envío de Temu encima (30-sep): el
        # MÁS CERCANO de los PO que se compran, porque todas sus cajas comparten
        # fecha (una por llamada); sin el de alguno, el plazo más corto. Una
        # venta que ya no alcanza su límite se compra IGUAL, "TARDE". Ver
        # `fecha_de_grupo`.
        limites_g = {po: _entero(ventas[po].get("limite_envio_ts")) for po in a_comprar}
        fecha_g = fecha_de_grupo(momento, limites_g)
        gs["ventas_ts"] = {po: ventas[po].get("venta_ts") for po in miembros}
        gs["limites_ts"] = {po: _entero(ventas[po].get("limite_envio_ts")) for po in miembros}
        # Las que NO se pudieron leer: su "sin fecha de venta" es una lectura
        # fallida (se reintenta), no un dato que Temu no tenga.
        gs["ilegibles"] = sorted(po for po in miembros if ventas[po].get("ilegible"))
        gs["sin_limite"] = sorted(po for po, x in limites_g.items() if not x)
        gs["con_guia"] = sorted(po for po in miembros if po in con_guia)
        gs["fecha"] = {k: fecha_g.get(k) for k in (
            "fecha_envio", "dia_envio", "horas", "valida", "ajustada", "urgente", "tarde",
            "sin_limite", "ajuste", "limite", "limite_ts", "regla_fecha", "regla_horas",
            "motivos")}
        cotizar = ((objetivo is None or miembros == objetivo)
                   and (solo_cot is None or any(po in solo_cot for po in miembros)))
        for ll in plan["llamadas"]:
            gs["llamadas"].append(await _armar_llamada(
                s, ll, fecha_g, plan_ventas, hist["muestras"], medidas_alm, cotizar,
                medidas, catalogo, catalogo_leido, omnicanal=omnicanal, publicacion=publicacion,
                fuentes=fuentes))
        gs["comprable"] = bool(plan["planeado"] and not gs["motivos"] and gs["llamadas"]
                               and all(x["comprable"] for x in gs["llamadas"]))
        if gs["comprable"]:
            cont = contenido_aprobacion(plan["ventas"], gs["llamadas"], fecha_g, emitida)
            gs["aprobacion"] = {
                "huella": huella_aprobacion(cont), "emitida": emitida,
                "vence": r["aprobacion_vence"], "fecha_envio": fecha_g["fecha_envio"],
                "dia_envio": fecha_g["dia_envio"], "horas": fecha_g["horas"],
                "limite_ts": fecha_g.get("limite_ts"), "ajustada": fecha_g.get("ajustada"),
                "tarde": bool(fecha_g.get("tarde")),
                "sin_limite": bool(fecha_g.get("sin_limite")),
                "ajuste": fecha_g.get("ajuste"),
                "medidas": medidas_de(gs),
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
    r["limites"] = resumen_limites(r["grupos"])
    r["resumen"] = {
        "grupos": len(leer), "ventas": len(pos), "comprables": comprables,
        "bloqueados": len(leer) - comprables,
        "cajas": sum(len(ll["paquetes"]) for g in r["grupos"] for ll in g["llamadas"]),
        "costo_estimado_mxn": round(sum(
            ((p.get("cotizacion") or {}).get("elegido") or {}).get("monto") or 0
            for g in r["grupos"] if g["comprable"] for ll in g["llamadas"]
            for p in ll["paquetes"]), 2),
        # De qué fuente salió cada caja que se podría comprar (y cuántas no
        # tienen ninguna): la pregunta de "¿cuántas salen solas con qué dato?".
        "cajas_por_fuente": _cajas_por_fuente(r["grupos"]),
        "tardes": sum(1 for g in r["grupos"] if g["comprable"] and (g.get("fecha") or {}).get("tarde")),
    }
    r["ok"] = True
    log.info("plan de guías Temu: %s grupos, %s comprables, %s llamadas a Temu",
             len(leer), comprables, s.n)
    return r


def _medida_para_bitacora(e: dict[str, Any]) -> dict[str, Any]:
    """Lo que la bitácora 0061 guarda del empaque de una caja (columna
    `reparto`, sin columnas nuevas): de dónde salió su PESO y su CAJA, la
    confianza, lo declarado, R, volumétrico y facturable, si cabe en J&T, y
    TODOS los candidatos con el porqué de los descartados. PURA."""
    peso = e.get("peso") if isinstance(e.get("peso"), dict) else {}
    caja = e.get("caja") if isinstance(e.get("caja"), dict) else {}

    def _corto(c: dict[str, Any], llave: str) -> dict[str, Any]:
        return {"de": str(c.get("txt") or "")[:160], llave: c.get(llave), "ok": bool(c.get("ok")),
                "gana": bool(c.get("gana")), "por": (str(c["por"])[:200] if c.get("por") else None)}
    return {"fuente": e.get("fuente"), "fuente_peso": e.get("fuente_peso"),
            "fuente_txt": e.get("fuente_txt") or FUENTES_TXT.get(str(e.get("fuente"))),
            "confianza": e.get("confianza"), "peso_kg": e.get("peso_kg"),
            "caja_cm": ([e.get("largo_cm"), e.get("ancho_cm"), e.get("alto_cm")]
                        if e.get("largo_cm") else None),
            "peso_de": peso.get("fuente_txt"), "caja_de": caja.get("fuente_txt"),
            "R": caja.get("R"), "rejilla": caja.get("rejilla"),
            "volumetrico_kg": caja.get("volumetrico_kg"),
            "facturable_kg": caja.get("facturable_kg"), "suma_lados_cm": caja.get("suma_cm"),
            "cabe_jt": caja.get("cabe_jt"), "partir": caja.get("partir"),
            "candidatos_peso": [_corto(c, "kg") for c in (peso.get("candidatos") or [])[:12]],
            "candidatos_caja": [_corto(c, "cm") for c in (caja.get("candidatos") or [])[:12]],
            "avisos": [str(a)[:240] for a in (e.get("avisos") or [])[:6]] or None,
            "detalle": str(e.get("detalle") or "")[:400] or None}


def medidas_de(grupo: dict[str, Any]) -> list[dict[str, Any]]:
    """De dónde salió el peso y la caja de CADA caja del grupo, sin datos del
    comprador: [{caja, contenido, fuente, fuente_txt, confianza, ok}]. PURA."""
    salida = []
    for ll in grupo.get("llamadas") or []:
        for p in ll.get("paquetes") or []:
            e = p.get("empaque") or {}
            salida.append({"caja": p.get("clave"), "almacen": p.get("almacen"),
                           "contenido": ", ".join(f"{x.get('sku')} × {x.get('quantity')}"
                                                  for x in p.get("renglones") or []),
                           "fuente": e.get("fuente"), "fuente_peso": e.get("fuente_peso"),
                           "fuente_txt": e.get("fuente_txt") or FUENTES_TXT.get(
                               str(e.get("fuente")), e.get("fuente")),
                           "avisos": list(e.get("avisos") or []),
                           "cabe_jt": (e.get("caja") or {}).get("cabe_jt"),
                           "partir": (e.get("caja") or {}).get("partir"),
                           "no_jt": porque_no_jt_de(p),
                           "confianza": e.get("confianza"), "ok": bool(e.get("ok")),
                           "peso_kg": e.get("peso_kg"),
                           "caja_cm": ([e.get("largo_cm"), e.get("ancho_cm"), e.get("alto_cm")]
                                       if e.get("largo_cm") else None),
                           "detalle": str(e.get("detalle") or "")[:300] or None})
    return salida


def texto_medidas(medidas: Iterable[dict[str, Any]]) -> str:
    """"omnicanal · packing list (media)" por caja, sin repetir. PURA."""
    vistos: list[str] = []
    for m in medidas or []:
        t = f"{m.get('fuente_txt') or 'sin fuente'} ({m.get('confianza') or '?'})"
        if t not in vistos:
            vistos.append(t)
    return " + ".join(vistos)


def _cajas_por_fuente(grupos: list[dict[str, Any]]) -> dict[str, int]:
    """{fuente: cajas} de los grupos leídos; 'ninguna' = sin peso ni caja. PURA."""
    cuenta: dict[str, int] = {}
    for g in grupos or []:
        for m in medidas_de(g):
            k = (m["fuente_txt"] or "ninguna") if m["ok"] else "sin medida comprable"
            cuenta[k] = cuenta.get(k, 0) + 1
    return dict(sorted(cuenta.items(), key=lambda kv: -kv[1]))


def resumen_limites(grupos: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Cuánto deja Temu entre la VENTA y su LÍMITE DE ENVÍO, por día de la semana
    de la venta (hora de México), y cuántos grupos salieron con la fecha
    adelantada, o comprados TARDE (ningún plazo alcanzaba el límite en día
    hábil). PURA.

    Es la medición que pide la decisión de Brandon (revisión del 30-sep): con el
    límite a ~48 h de la venta la regla de +2 días casi nunca cabe (se adelanta
    a 24 h) y las ventas de viernes y sábado van TARDE porque 24 h cae en fin de
    semana (`TEMU_GUIAS_SABADO_ALTERNO`). Sin datos del comprador.
    """
    por_dia: dict[str, dict[str, Any]] = {}
    for g in grupos or []:
        f = g.get("fecha") or {}
        for po in g.get("a_comprar") or []:
            v = _entero((g.get("ventas_ts") or {}).get(po))
            lim = _entero((g.get("limites_ts") or {}).get(po))
            if not v or not lim:
                continue
            dia = _DIAS[datetime.fromtimestamp(v, tz=timezone.utc).astimezone(ZONA).weekday()]
            d = por_dia.setdefault(dia, {"ventas": 0, "horas": [], "ajustadas": 0, "urgentes": 0,
                                         "tardes": 0})
            d["ventas"] += 1
            d["horas"].append(round((lim - v) / 3600.0, 1))
            d["ajustadas"] += 1 if f.get("ajustada") and not f.get("tarde") else 0
            d["urgentes"] += 1 if f.get("urgente") else 0
            d["tardes"] += 1 if f.get("tarde") else 0
    salida: dict[str, Any] = {}
    for dia in _DIAS:
        d = por_dia.get(dia)
        if not d:
            continue
        hs = sorted(d["horas"])
        salida[dia] = {"ventas": d["ventas"], "ajustadas": d["ajustadas"],
                       "urgentes": d["urgentes"], "tardes": d["tardes"], "horas_min": hs[0],
                       "horas_mediana": hs[len(hs) // 2], "horas_max": hs[-1]}
    return {"por_dia_de_venta": salida,
            "explica": ("horas = límite de envío de Temu − hora de la venta. 'ajustadas': la "
                        "regla de +2 días rebasaba el límite y se adelantó; 'tardes': ningún "
                        "plazo cabía en día hábil antes del límite y se compran IGUAL con el más "
                        "corto; 'urgentes' ya no ocurre (se conserva por el panel)")}


async def _armar_llamada(s: _Sesion, ll: dict[str, Any], fecha: dict[str, Any],
                         g_ventas: list[dict[str, Any]], muestras: list[dict[str, Any]],
                         medidas_alm: dict[str, dict[str, Any]], cotizar: bool,
                         manuales: dict[str, dict[str, float]],
                         catalogo: dict[str, dict[str, float]],
                         catalogo_leido: bool, *,
                         omnicanal: dict[str, dict[str, Any]] | None = None,
                         publicacion: dict[str, dict[str, float]] | None = None,
                         fuentes: Iterable[str] | None = None) -> dict[str, Any]:
    """Empaque, cotización y payload de UNA llamada. Nunca lanza. (`muestras`
    ya viene sin el eco: lo quita el SQL del historial.)"""
    motivos: list[str] = list(fecha["motivos"])
    avisos: list[str] = []
    paquetes = []
    for p in ll["paquetes"]:
        contenido: dict[str, int] = {}
        for e in p["renglones"]:
            contenido[e["sku"]] = contenido.get(e["sku"], 0) + int(e["quantity"])
        clave = clave_caja(p)
        emp = elegir_empaque(contenido, muestras, medidas_alm, manual=manuales.get(clave),
                             catalogo=catalogo, catalogo_leido=catalogo_leido,
                             omnicanal=omnicanal, publicacion=publicacion, fuentes=fuentes)
        caja = {**p, "clave": clave, "empaque": emp, "cotizacion": None,
                "payload_cotizacion": None}
        if not emp["ok"]:
            motivos.append(f"caja de {p['almacen']}: {emp['motivo']}")
        if emp.get("peso_kg") and emp.get("largo_cm") and cotizar:
            caja["payload_cotizacion"] = payload_cotizacion(caja)
            try:
                res = await s.llamar("bg.logistics.shippingservices.get",
                                     caja["payload_cotizacion"])
                info_caja = emp.get("caja") if isinstance(emp.get("caja"), dict) else {}
                excede = None
                if info_caja.get("cabe_jt") is False and jt_excede_usa_otra():
                    excede = "; ".join(str(x) for x in info_caja.get("motivos") or []) or "no cabe"
                caja["cotizacion"] = elegir_canal(res or {}, jt_excede=excede)
                cot = caja["cotizacion"]
                if cot["motivo"] and info_caja.get("cabe_jt") is False:
                    # La caja no cabe en J&T y NINGÚN canal la aceptó: manual,
                    # diciendo cuántas cajas harían falta. Las demás siguen.
                    motivos.append(f"caja de {p['almacen']}: excede paquetería: "
                                   f"{info_caja.get('partir') or 'partir en varias cajas'} — "
                                   f"{cot['motivo']}")
                elif cot["motivo"]:
                    motivos.append(f"caja de {p['almacen']}: {cot['motivo']}")
                elif cot.get("porque_no_jt"):
                    avisos.append(f"caja de {p['almacen']}: no va por J&T — {cot['porque_no_jt']}")
            except Exception as exc:  # noqa: BLE001
                caja["cotizacion"] = {"elegido": None, "opciones": [], "mas_barata": None,
                                      "no_disponibles": [], "es_jt": False, "porque_no_jt": None,
                                      "motivo": f"no se pudo cotizar: {_texto_error(exc)}"}
                motivos.append(f"caja de {p['almacen']}: no se pudo cotizar")
        elif not cotizar:
            motivos.append("no se cotizó (fuera del grupo pedido)")
        paquetes.append(caja)

    # La fecha contra el límite de envío de Temu, por PO. Una fecha "TARDE" es
    # la decisión de Brandon (comprar igual con el plazo más corto): se AVISA,
    # no bloquea. Cualquier otra fecha después del límite es un error del plan.
    pos = set(ll["ventas"])
    for v in g_ventas:
        if v["parent_order_sn"] in pos and v.get("limite_envio_ts") and fecha.get("vence_ts") \
                and fecha["vence_ts"] > int(v["limite_envio_ts"]):
            txt = (f"{v['parent_order_sn']}: la fecha de envío ({fecha['fecha_envio']}) cae "
                   f"DESPUÉS del límite de Temu ({v['limite_envio']})")
            if fecha.get("tarde"):
                avisos.append(f"{txt} — {TEXTO_TARDE}: se entrega a la paquetería en cuanto se pueda")
            else:
                motivos.append(f"{txt}: riesgo de retraso en el cumplimiento, no se compra así")

    salida = {"send_type": ll["send_type"], "explica": ll["explica"], "ventas": ll["ventas"],
              "paquetes": paquetes, "payload": None, "huella": None, "motivos": motivos,
              "avisos": avisos, "comprable": False, "errores_payload": []}
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
#  9 · LA COMPRA — APAGADA; sólo la invoca la compra automática (temu_guias_auto)
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
      · 'rechazada'        — Temu rechazó ESTA venta con una validación de
                             negocio DOCUMENTADA en la ficha (no compró);
      · 'pasarela'         — la pasarela rechazó la llamada antes de llegar al
                             negocio (firma, credenciales, permisos, IP): no
                             compró, y no es culpa de la venta;
      · 'limite_velocidad' — 4000004, demasiadas peticiones: no compró y es
                             pasajero (se reintenta la vuelta siguiente);
      · 'ya_solicitada'    — 120012013: ya había una compra de esa orden;
      · 'desconocido'      — todo lo demás: 4000000, códigos no documentados,
                             timeouts, cancelación. NO se sabe si compró."""
    if not isinstance(exc, Exception):
        return "desconocido"
    cod = _codigo(exc)
    if cod == YA_SOLICITADA:
        return "ya_solicitada"
    if cod in LIMITE_VELOCIDAD:
        return "limite_velocidad"
    if cod in _RECHAZO_PASARELA:
        return "pasarela"
    if cod in RECHAZO_SEGURO:
        return "rechazada"
    return "desconocido"


async def _verificar_antes(ll: dict[str, Any], aprob: dict[str, Any],
                           momento: datetime | None = None, *,
                           revisar_tarde: bool = True) -> list[str]:
    """
    La última revisión, JUSTO antes de `shipment.create` de UNA llamada. Vacía
    = se puede comprar. Relee el detalle de SUS PO (estado 2, sin paquete, sin
    cancelación ni cambio de dirección pendientes, mismas piezas),
    `unshipped.package.get`, y recalcula la fecha: si el día ya no da la fecha
    aprobada (o las horas del payload), no se compra. Con `revisar_tarde`
    (la PRIMERA llamada del grupo), tampoco si la guía cambió de "a tiempo" a
    "TARDE" (o al revés): la marca ya se escribió al reclamar. En las llamadas
    siguientes de un grupo partido no se revisa: la primera ya compró con esa
    marca, y detenerse a la mitad dejaría el grupo a medias por un aviso.
    """
    motivos: list[str] = []
    payload = ll["payload"]
    esperado: dict[str, dict[str, int]] = {}
    for caja in payload["sendRequestList"]:
        for rr in caja["orderSendInfoList"]:
            d = esperado.setdefault(rr["parentOrderSn"], {})
            d[rr["orderSn"]] = d.get(rr["orderSn"], 0) + int(rr["quantity"])
    # El límite de envío con el que se aprobó (el del grupo) y los que Temu
    # dice AHORA de estos PO: manda el más cercano. Si Temu lo adelantó, la
    # fecha recalculada ya no es la aprobada y no se compra. Sin el límite de
    # alguno (ahora o al aprobar) → el plazo más corto, igual que el plan.
    limites: dict[str, Any] = {}
    if _entero(aprob.get("limite_ts")):
        limites["(aprobado)"] = int(aprob["limite_ts"])
    if aprob.get("sin_limite"):
        limites["(aprobado sin límite)"] = None
    s = _Sesion(4 + 2 * len(ll["ventas"]), 90)
    for po in ll["ventas"]:
        try:
            det = await s.llamar("bg.order.detail.v2.get", {"parentOrderSn": po})
        except Exception as exc:  # noqa: BLE001
            motivos.append(f"{po}: no se pudo releer el detalle ({_texto_error(exc)[:120]})")
            continue
        v = leer_venta(po, det or {})
        limites[po] = _entero(v.get("limite_envio_ts"))
        motivos.extend(f"{po}: {b}" for b in v["bloqueos"])
        pedido = {x["orderSn"]: int(x["cantidad"]) for x in v["renglones"]}
        if pedido != esperado.get(po, {}):
            motivos.append(f"{po}: lo pedido en Temu cambió desde la aprobación")
    f = fecha_de_grupo(momento or datetime.now(timezone.utc), limites)
    if not f["valida"]:
        motivos.extend(f["motivos"])
    if f["fecha_envio"] != aprob["fecha_envio"] or str(f["horas"]) != str(payload["shipLaterLimitTime"]):
        motivos.append(f"la fecha de envío ya no es la aprobada: comprando ahora sería el "
                       f"{f['fecha_envio']} a {f['horas']} h y se aprobó el "
                       f"{aprob['fecha_envio']} a {payload['shipLaterLimitTime']} h — vuelve a "
                       "revisar la vista previa")
    elif revisar_tarde and f["valida"] and bool(f.get("tarde")) != bool(aprob.get("tarde")):
        # Mismo día y mismas horas, pero el límite de Temu quedó del otro lado
        # (revisión del 30-sep): comprar así dejaría la bitácora, el panel y la
        # nota de Odoo diciendo lo contrario de lo que pasó. Se vuelve a planear.
        motivos.append("la guía " + ("pasó a comprarse TARDE (el límite de Temu ya no se alcanza)"
                                     if f.get("tarde") else "ya no va TARDE (Temu movió el límite)")
                       + " desde la aprobación: se vuelve a planear para que quede dicho")
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
    `aprobado_por` lo pone la compra automática (`temu_guias_auto.QUIEN`, que
    empieza con "auto") o, si algún día existe, un endpoint desde la SESIÓN (no hay endpoint
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
            # Presupuesto amplio: el re-plan lee el detalle de TODAS las ventas
            # más viejas que el grupo (hasta ~120 con la cola de hoy) y, sin
            # cuota o sin tiempo, las que no alcanzara a leer volverían el lote
            # "incierto" y la compra no saldría nunca.
            plan = await plan_guias(solo=sn, usar_cache=False, emitida=emitida_i,
                                    medidas=medidas, tope_llamadas=400, segundos_max=270.0)
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
            reclamado_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            filas = []
            for ll in grupo["llamadas"]:
                for po in ll["ventas"]:
                    # Además de qué sale de dónde: la caja, la paquetería y el
                    # costo cotizado, y cuándo se reclamó. Con eso la bitácora
                    # dice "comprada (paquetería, costo, fecha)" y los topes
                    # del día de la compra automática sobreviven a un reinicio.
                    # Y (30-sep) DE DÓNDE salió el peso y la caja de cada caja
                    # y si se compró después del límite de Temu ("TARDE"): la
                    # columna `reparto` ya existe, la migración 0061 no cambia.
                    reparto = [{"orderSn": e["orderSn"], "sku": e["sku"],
                                "quantity": int(e["quantity"]), "warehouse_id": p["warehouse_id"],
                                "almacen_id": p["almacen_id"], "caja": p.get("clave"),
                                "paqueteria": paqueteria_de(p), "costo_mxn": costo_de(p),
                                # Por qué NO fue J&T (si no lo fue), lo que Temu
                                # dijo de los canales no disponibles y las reglas
                                # del canal comprado: la única forma de conocer
                                # el límite real que Temu aplica.
                                "no_jt": porque_no_jt_de(p),
                                "no_disponibles": [
                                    {"paqueteria": x.get("shippingCompanyName"),
                                     "tipo": x.get("shipLogisticsType"), "motivo": x.get("motivo")}
                                    for x in ((p.get("cotizacion") or {}).get("no_disponibles")
                                              or [])[:6]] or None,
                                "reglas_canal": (((p.get("cotizacion") or {}).get("elegido") or {})
                                                 .get("reglas")),
                                "reclamado_at": reclamado_at,
                                "medida": _medida_para_bitacora(p.get("empaque") or {}),
                                "tarde": bool(aprob.get("tarde")),
                                "sin_limite": bool(aprob.get("sin_limite"))}
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
        motivos = await _verificar_antes(ll, aprob, revisar_tarde=not i)
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
            if clase not in NO_COMPRO:
                _DESCONOCIDAS.update(pos_ll)
            txt = _texto_error(exc) if isinstance(exc, Exception) else type(exc).__name__
            # La pasarela y el límite de velocidad no juzgan la venta: su fila
            # queda 'no_enviada' (libre), NUNCA 'rechazada' (que la compra
            # automática ya no reintenta).
            estado_fila = "no_enviada" if clase in ("pasarela", "limite_velocidad") else clase
            if estado_fila != clase:
                quien_txt = ("Temu pidió bajar la velocidad" if clase == "limite_velocidad"
                             else "la pasarela de Temu rechazó la llamada")
                txt = f"{quien_txt} ({cod}): no compró — {txt}"
            await _anotar(reclamo, {**{po: {"estado": estado_fila, "codigo": cod, "motivo": txt}
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
        # DISTINTOS: el mismo packageSn para dos cajas cuadra en número y deja
        # una caja real fuera de la bitácora (y de su orden de Odoo).
        if (len(psns) != len(payload["sendRequestList"])
                or len(set(psns)) != len(psns)):
            _DESCONOCIDAS.update(pos_ll)
            motivo = (f"Temu devolvió {len(psns)} packageSn ({len(set(psns))} distintos) para "
                      f"{len(payload['sendRequestList'])} caja(s): no se sabe qué compró")
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
                        "avisos_temu": avisos, "reparto_real": real,
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
        "pasarela": ("la pasarela de Temu rechazó la llamada (firma, credenciales, permisos o IP): "
                     "no se compró y la venta queda libre — el problema es del sistema"),
        "limite_velocidad": ("Temu pidió bajar la velocidad (4000004, demasiadas peticiones): no se "
                             "compró y la venta queda libre — se reintenta la vuelta siguiente"),
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


def paquetes_ajenos(payload: Any, paquetes: dict[str, dict[str, Any]]) -> bool:
    """¿Se puede PROBAR que los paquetes que Temu muestra NO salieron de ESTA
    llamada (`payload` de shipment.create)? True sólo si algún paquete legible
    (almacén, peso y caja de `shipment.result.get`) no coincide con NINGUNA caja
    del payload: lo declaró otra persona, con otras medidas o desde otro
    almacén. Sin con qué comparar (Temu no dio medidas, payload ilegible) →
    False: no se prueba nada. PURA."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return False
    if not isinstance(payload, dict):
        return False
    cajas: list[tuple[str, float, list[float]]] = []
    for c in payload.get("sendRequestList") or []:
        try:
            cajas.append((str(c["warehouseId"]), float(c["weight"]),
                          sorted(float(c[k]) for k in ("length", "width", "height"))))
        except (KeyError, TypeError, ValueError):
            return False
    if not cajas:
        return False
    for p in (paquetes or {}).values():
        m = muestra_de_paquete(p) if isinstance(p, dict) else None
        wh = str((p or {}).get("warehouseId") or "").strip() if isinstance(p, dict) else ""
        if not m or not wh:
            continue                           # ilegible: no prueba nada
        lados = sorted((float(m["largo_cm"]), float(m["ancho_cm"]), float(m["alto_cm"])))
        if not any(wh == w and abs(float(m["peso_kg"]) - kg) < 0.006
                   and all(abs(x - y) < 0.006 for x, y in zip(lados, ls)) for w, kg, ls in cajas):
            return True
    return False


async def conciliar(parent_order_sn: str, *, liberar: bool = False,
                    quien: str = "", revisar_rechazada: bool = False,
                    payload_propio: Any = None) -> dict[str, Any]:
    """
    Concilia la compra ABIERTA de un PO mirando Temu (detalle, unshipped,
    result.get). SÓLO escribe en la bitácora; nunca compra. La invoca el botón
    "Conciliar" de la compra automática (`temu_guias_auto.conciliar_y_liberar`).

    Si Temu muestra la guía → 'comprada' / 'pendiente' / 'fallida' con su
    reparto real. Si no hay rastro y pasaron ≥ 30 min, `liberar=True` con
    `quien` (verificado a mano en el seller center) la deja 'rechazada', y el PO
    vuelve a poder comprarse con una aprobación nueva. No lanza (salvo
    cancelación).

    `revisar_rechazada=True`: una fila 'rechazada' (que no bloquea) TAMBIÉN se
    mira en Temu —detalle, unshipped y `label.list`— antes de dar el rechazo
    por bueno. Es lo que pide la compra automática antes de liberarse: el
    rechazo lo clasificó una lista de códigos, y si Temu sí compró, la fila
    tiene que decirlo (y bloquear) en vez de quedar libre.

    `payload_propio` (con `revisar_rechazada`; lo pasa la mirada AUTOMÁTICA de
    la compra automática, no el botón) = el payload de la llamada que Temu
    rechazó: si la guía que Temu muestra se declaró DISTINTO (otro peso, otra
    caja u otro almacén: `paquetes_ajenos`), la compró una persona después —el
    flujo normal de una venta en "compra manual"— y la fila NO se toca
    (`accion='guia_ajena'`).
    """
    sn = str(parent_order_sn or "").strip()
    try:
        fila = (await asyncio.to_thread(_reclamos_de, [sn])).get(sn)
        if not fila:
            return {"ok": False, "accion": "sin_registro", "parent_order_sn": sn}
        mirar_rechazada = revisar_rechazada and fila.get("estado") == "rechazada"
        if not mirar_rechazada and (fila.get("estado") in ESTADOS_LIBRES
                                    or fila.get("estado") == "comprada"):
            return {"ok": True, "accion": "nada_que_conciliar", "estado": fila.get("estado"),
                    "parent_order_sn": sn}
        s = _Sesion(12, 90)
        det = await s.llamar("bg.order.detail.v2.get", {"parentOrderSn": sn})
        v = leer_venta(sn, det or {})
        psns = sorted(set(v.get("paquetes") or []) | set(fila.get("package_sn") or []))
        if not psns:
            sin = await _paquetes_sin_enviar(s, [sn])
            psns = sorted({p for p in sin.get(sn, []) if p and p != "?"})
        if mirar_rechazada and not psns:
            # La tercera fuente: una etiqueta en CUALQUIER estado. Si Temu la
            # tiene y no hay packageSn con qué conciliarla, no se sabe qué pasó:
            # la fila deja de estar libre y bloquea (como un "no sé si compró").
            etq = (await _etiquetas_de(s, [sn])).get(sn) or []
            if etq:
                anotada = await _anotar(fila["reclamo"], {sn: {
                    "estado": "desconocido", "motivo": (
                        f"se había dado por rechazada ({fila.get('codigo') or '?'}) y Temu "
                        f"muestra {len(etq)} etiqueta(s) sin packageSn: concilia en el seller "
                        "center")}})
                _DESCONOCIDAS.add(sn)
                return {"ok": True, "accion": "conciliada", "estado": "desconocido",
                        "package_sn": [], "parent_order_sn": sn, "anotada": anotada}
            return {"ok": True, "accion": "nada_que_conciliar", "estado": "rechazada",
                    "mirado_en_temu": True, "parent_order_sn": sn}
        if psns:
            filas = await _resultados_de(s, psns)
            if mirar_rechazada and payload_propio is not None and paquetes_ajenos(payload_propio, filas):
                return {"ok": True, "accion": "guia_ajena", "estado": "rechazada",
                        "package_sn": psns, "mirado_en_temu": True, "parent_order_sn": sn}
            st = [_entero((filas.get(p) or {}).get("shippingLabelStatus")) for p in psns]
            estado = ("fallida" if any(x == 2 for x in st) else
                      "comprada" if st and all(x == 1 for x in st) else "pendiente")
            mapa_inv = {wh: wid for wid, wh in mapa_almacenes().items()}
            ll = {"paquetes": [{"renglones": [
                {"orderSn": x.get("orderSn"), "sku": x.get("sku"), "parentOrderSn": sn}
                for x in (fila.get("reparto") or []) if isinstance(x, dict)]}]}
            real = _reparto_real(ll, filas, mapa_inv).get(sn)
            anotada = await _anotar(fila["reclamo"], {sn: {"estado": estado, "package_sn": psns,
                                                           "reparto_real": real,
                                                           "motivo": "conciliada contra Temu"}})
            if anotada:
                # La bitácora durable ya sabe qué pasó: la red en memoria sobra
                # (y dejarla trabaría al resto de su grupo).
                _DESCONOCIDAS.discard(sn)
            return {"ok": True, "accion": "conciliada", "estado": estado, "package_sn": psns,
                    "parent_order_sn": sn, "anotada": anotada}
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
