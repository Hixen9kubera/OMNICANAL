"""
temu_guias_auto.py — La COMPRA AUTOMÁTICA de guías de Temu: de la venta a la
orden de Odoo confirmada con su guía y su PDF, sin aprobar cada una a mano.

EL ENCARGO (Brandon, 30-sep-2026, literal)
──────────────────────────────────────────
"…mandas las órdenes de venta a partir de hoy y automatízala desde que se
genera la orden hasta confirmarla con su guía y sus datos correctos."

LA CADENA (una vuelta cada `TEMU_COMPRA_GUIAS_AUTO_MIN`, 15 min)
───────────────────────────────────────────────────────────────
  venta que espera su guía (`ops.odoo_sale_orders`, accion='espera_guia')
    → `temu_guias_compra.plan_guias`: el MISMO planeador de la vista previa
      (almacén por las reglas a/b/c, cajas, fecha con el límite de Temu, peso
      y caja por la regla del MENOR creíble entre lo nuestro y lo de Temu,
      J&T primero —la más barata sólo si J&T no se ofrece—, payload)
    → `temu_guias_compra.comprar` con la huella que el plan acaba de emitir:
      re-planea en vivo, reclama el grupo en la bitácora durable (0061),
      relee la orden justo antes, compra y espera el resultado asíncrono
    → refresco INMEDIATO de guías de ESA venta
      (`pedidos_temu.refrescar_guias(solo_ids=…)`): la orden nace en Odoo con
      los almacenes de la guía, se confirma, lleva el número en cada entrega,
      el PDF en "Subir guía" y en la nota el DÍA de entregarla a la paquetería
      — sin esperar la vuelta de las dos horas
    → RELECTURA de Odoo (`odoo_ventas.leer_venta_odoo`): confirmada, en los
      almacenes de la guía con SUS piezas, la guía de SU caja en cada entrega
      (en un pedido partido, cada parte con la suya) y el PDF de esa guía. El
      resultado queda en el `motivo` de la fila de la bitácora de compras
      (`auto:verificada` / `auto:por_verificar` / `auto:no_verificada`), que
      es lo que pinta el panel.

"SE DEBEN DE COMPRAR TODAS LAS GUÍAS POSIBLES DE TODAS LAS ÓRDENES, SIN
PERDERSE NINGUNA, DE MANERA AUTOMÁTICA" (Brandon, 30-sep). Por eso:
  · el corte `TEMU_COMPRA_GUIAS_DESDE` se movió al 15-sep (toda la ventana de
    espera de 14 días) y un combinado que mezcla una venta anterior al corte
    con una nueva se compra ENTERO;
  · las VENCIDAS (el límite de envío de Temu ya pasó, o ningún plazo lo
    alcanza en día hábil) se compran SIEMPRE, con el plazo más corto, marcadas
    "comprada TARDE" — sin interruptor;
  · el peso y la caja salen del MENOR creíble entre lo nuestro (almacén,
    packing list, Woo) y lo de Temu (guías, publicación), con la caja master
    repartida como en Costos y los límites de J&T (ver `temu_guias_compra`);
  · sin límite de envío de Temu, o sin fecha de venta (con la de kubera), se
    compra igual;
  · un fallo TRANSITORIO (lectura, cotización, red, el plan que cambió entre la
    vista previa y la compra, el 4000004 de "demasiadas peticiones") se
    reintenta en la VUELTA SIGUIENTE, sin reposo; su racha se cuenta desde el
    PRIMER fallo (`_TRANSITORIO_DESDE`, aunque entre vueltas el plan la dé por
    comprable) y suena en la campana a los 60 min — una VENCIDA, a la segunda
    vuelta que falla;
  · la cola se planea ENTERA (`TEMU_GUIAS_COLA_MAX`), no las 120 de la vista
    previa del panel, y cada vuelta compra lo que cabe en su tiempo
    (`presupuesto_s`): lo demás, en la siguiente.

LO QUE SIGUE SIN COMPRARSE SOLO — "requiere compra manual", siempre con su
motivo en el panel y en la campana (en RESUMEN, por cambio de estado):
  · ventas ANTERIORES al corte (una persona las compra; mover el corte es una
    variable, sin deploy);
  · lo que es imposible o peligroso comprar: sin stock en ningún almacén
    (guía de mercancía que no hay), cancelación o cambio de dirección
    pendiente, COD, preventa Y2, Temu prohíbe la guía, sin peso y caja creíbles
    en NINGÚN escalón, la paquetería/tipo preferido no se ofreció, o Temu
    cotiza en otra moneda;
  · lo que pasa de los topes de Brandon (por caja, por venta, por día);
  · una venta cuya compra automática Temu RECHAZÓ con una validación de negocio
    documentada (seguro que NO compró): queda "compra manual · Temu la rechazó:
    <código y texto>" con campana, y EL JOB SIGUE comprando las demás (Brandon,
    1-oct: "que se sigan comprando, no es un bloqueante"). No se reintenta sola
    en bucle: UNA vez, cuando su plan cambie EN LO MATERIAL (otro peso, otra
    caja, otra paquetería o canal, otro almacén u otras piezas — lo que Temu
    juzga; el cambio de DÍA no cuenta, salvo que el rechazo haya sido por el
    plazo: `CODIGOS_DE_PLAZO`) —ese reintento queda escrito en la bitácora al
    reclamar (`aprobado_por` = `QUIEN_REINTENTO`), y si el reclamo termina sin
    que Temu la juzgue ('no_enviada') el siguiente lo sigue llevando— o cuando
    alguien pulse «Reintentar». A cada rechazada NUEVA se le echa además UNA
    MIRADA a Temu en la vuelta siguiente (`_mirar_rechazadas`: detalle,
    unshipped y label.list, entre 5 y 60 min después del rechazo): si Temu
    muestra una guía declarada como la nuestra, la fila lo dice y su orden se
    verifica; si la guía se declaró distinto (la compró una persona: el flujo
    normal) no se toca; si hay una etiqueta sin paquete, el job se detiene.
    La pasarela (3000xxx) y el 4000004 NO son un rechazo de la venta: su fila
    queda libre y se vuelve a intentar la vuelta siguiente;
  · en MODO SIMPLE (`TEMU_COMPRA_GUIAS_AUTO_SOLO_SIMPLE`, apagado en
    producción desde el 30-sep) lo que no sea UNA caja sendType 0.
Se vuelve a evaluar cada `TEMU_COMPRA_GUIAS_REVISAR_MIN` (su motivo puede
resolverse: llega stock, alguien captura la medida). Una vuelta que no la
volvió a cotizar NO le renueva ese reposo ni le cambia el motivo. Una venta
manual cuyo límite de Temu ya venció sale URGENTE en la campana (también la
que frena el tope del día). "Ya tiene guía" no suena, salvo que siga en la
cola 4 h: su etiqueta está en Temu pero su orden no nace.

BANDERAS — las tres, o no compra (sin las dos primeras el job ni se registra)
─────────────────────────────────────────────────────────────────────────────
`TEMU_COMPRA_GUIAS_ENABLED` · `TEMU_COMPRA_GUIAS_AUTO` · la tabla 0061. Y la
cadena de Automatización ENTERA: interruptor general, canal Temu, "la orden
nace con la guía", `ODOO_VENTAS_CONFIRMAR`, sin `SOLO_REGISTRO` y el trabajo
de guías de Temu. Comprar una guía cuya orden no va a nacer es mandar un
paquete que el almacén no ve.

Y dos frenos de ARRANQUE, que nacen encendidos:
  · `TEMU_COMPRA_GUIAS_AUTO_ENSAYO` (true): la vuelta hace TODO menos comprar
    —planea, cotiza, evalúa topes— y deja en el panel qué compraría ("Ensayo:
    la compraría …"). La primera compra real es al apagarla.
  · `TEMU_COMPRA_GUIAS_AUTO_SOLO_SIMPLE` (true): ver arriba.

TOPES (config.py) — los del día se cuentan de la bitácora DURABLE
──────────────────────────────────────────────────────────────────
`MAX_VUELTA` (1), `MAX_DIA` (1), `GASTO_MAX_DIA`, `MAX_CAJA` (MX$80),
`MAX_VENTA`. Un reinicio del contenedor (cambiar una variable) no los reinicia.
Con el tope del día alcanzado, las ventas nuevas quedan "requiere compra
manual · tope del día" (sin llamar a Temu) y la campana lo dice.

Y si una vuelta NO puede comprar por algo del sistema (cadena de
Automatización incompleta, bitácora o cola ilegibles, el plan a medias, o se le
acabó el tiempo sin empezar ninguna compra…) tres vueltas seguidas —aunque la
causa cambie de una a otra—, la campana lo dice (`temu_compra_auto_bloqueada`):
un "transitorio" que no se arregla solo tampoco se queda callado.

SÓLO EL DINERO EN DUDA DETIENE EL JOB (1-oct)
─────────────────────────────────────────────
Detiene lo que es "no sé si compró" o "compró y algo no cuadra": respuesta
desconocida o timeout tras enviar, ya solicitada (120012013), etiqueta
fallida, grupo a medias, horas distintas, error después de intentar, una fila
ABIERTA en la bitácora, una etiqueta que sigue "en aplicación" pasado el
plazo, o una orden de Odoo que no quedó como la guía dice →
`ops.automatizacion_flags` `temu_compra_guias_auto_detenida = true` + campana,
y no se compra nada más.
NO detiene (seguro que no compró): un RECHAZO documentado de shipment.create
(ver arriba), la pasarela (la vuelta termina y la siguiente lo reintenta; tres
seguidas suenan como bloqueo) y el 4000004. CORTACIRCUITOS: tres rechazos
SEGUIDOS con el mismo código —o cinco seguidos de cualquier código— sin una
compra en medio → la vuelta deja de comprar (el job NO se detiene) y suena
`temu_compra_auto_rechazos` con el código, para no quemar la cola contra un
fallo del sistema. La racha tiene MEMORIA entre vueltas: se cuenta de la
bitácora durable (`racha_de_rechazos`, últimas 24 h), así que con la racha
abierta la vuelta siguiente PRUEBA UNA sola venta — si Temu la compra, la racha
se cierra y se sigue; si la rechaza igual, se corta otra vez (antes cada vuelta
mandaba otras tres y la cola entera quedaba rechazada en un par de horas).
Y EL PLAN A MEDIAS NO COMPRA (`plan_incompleto`): tampoco si no se pudo leer el
catálogo (sin el flete se declararía el cartón master por una pieza), las
mediciones de almacén o el historial de guías (ya no se declararía el menor).
Se reintenta la vuelta siguiente; tres seguidas suenan.
Si esa bandera no se puede escribir, la detención queda además marcada en las
filas de la bitácora (`auto:detenida`) y se reintenta escribir en cada vuelta:
un reinicio no la borra. Se libera sólo cuando alguien concilia (botón
"Conciliar" del panel → `conciliar_y_liberar`), la conciliación sale bien y no
queda NINGUNA compra abierta ni etiqueta automática en aplicación. Mientras la
orden de una compra no esté verificada tampoco se compra la siguiente.

DOS PROCESOS: un TURNO en `ops.automatizacion_flags` (`…_turno`, vence a los
20 min) deja una sola vuelta a la vez en todo el despliegue; y aunque fallara,
el reclamo durable de `comprar()` impide comprar dos veces la misma venta.

SIN DATOS DEL COMPRADOR: PO, SKUs, almacenes, paquetería, costos y guías.
TODO LO QUE BLOQUEA VA EN HILOS (regla 11).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any, Iterable

from config import settings
from services import temu_guias_compra as tgc

log = logging.getLogger("omnicanal.temu_guias_auto")

CANAL = "temu"
# `aprobado_por` de las compras automáticas en la bitácora 0061. Todo lo que
# empieza con PREFIJO_AUTO es de este job (topes del día, panel, verificación).
QUIEN = "auto · TEMU_COMPRA_GUIAS_AUTO"
# El ÚNICO reintento automático de una venta que Temu rechazó (cuando cambia su
# plan). Va en `aprobado_por` al RECLAMAR —la misma transacción, ANTES de
# comprar—: si vuelve a rechazarse, la bitácora ya sabe que no hay otro, aunque
# el contenedor se reinicie a media compra. Empieza con PREFIJO_AUTO.
QUIEN_REINTENTO = "auto · reintento tras rechazo"
PREFIJO_AUTO = "auto"
FLAG_DETENIDA = "temu_compra_guias_auto_detenida"
FLAG_TURNO = "temu_compra_guias_auto_turno"
TURNO_MIN = 20
# Una fila 'en_curso' más joven que esto es una compra EN VUELO (de otra
# instancia): se espera. Más vieja, es un proceso que murió a media compra.
EN_CURSO_ATASCADA_MIN = 10
_CORTE_OMISION = "2026-09-15"

VERIF_OK = "auto:verificada"
VERIF_PEND = "auto:por_verificar"
VERIF_NO = "auto:no_verificada"
# Una etiqueta automática que sigue "en aplicación" en Temu y que una persona
# ACEPTÓ (miró el seller center) para liberar el job: se sigue conciliando sola
# —si Temu la marca fallida, detiene— pero su edad ya no detiene.
PEND_ACEPTADA = "auto:pendiente_aceptada"
# La detención, escrita en las filas cuando la bandera no se pudo guardar.
MARCA_DETENIDA = "auto:detenida"
# Una persona pidió REINTENTAR una venta rechazada (botón del panel): queda en
# el `motivo` de su fila 'rechazada' y la vuelta siguiente la vuelve a comprar.
MARCA_REINTENTAR = "auto:reintentar"
# Rechazos SEGUIDOS con el mismo código que cortan la vuelta (no el job).
RECHAZOS_SEGUIDOS_CORTE = 3
# …y rechazos seguidos de CUALQUIER código (sin una compra en medio): códigos
# que se alternan también son una cola quemándose.
RECHAZOS_SEGUIDOS_TOPE = 5
# La racha se cuenta de la bitácora durable, con los rechazos de estas horas.
RACHA_VENTANA_H = 24
# Los rechazos de shipment.create que SÍ dependen del plazo (shipLaterLimitTime):
# sólo con ellos un cambio de fecha u horas cuenta como "otro plan".
CODIGOS_DE_PLAZO = frozenset({"120015532", "120015534"})
# La mirada a Temu de una venta que Temu RECHAZÓ (el código lo clasificó una
# lista: si Temu sí compró, la fila tiene que decirlo). UNA mirada por rechazo,
# no antes de 5 min (Temu compra en asíncrono) ni después de 60: más tarde lo
# probable es que la guía que aparezca la haya comprado una persona —es lo que
# se espera de una venta en "compra manual"— y no habría cómo distinguirla. A
# lo más 5 ventas por vuelta.
RECHAZO_MIRAR_MIN = 5
RECHAZO_MIRAR_MAX_MIN = 60
RECHAZO_MIRAR_POR_VUELTA = 5

# Esperas (s) entre intentos del refresco inmediato antes de dejar la
# verificación "por verificar" (Temu puede tardar en mostrar la guía).
_ESPERAS_VERIFICACION: tuple[float, ...] = (0.0, 20.0, 45.0)

# Lo que contesta `comprar()` y DETIENE el job: SÓLO el dinero en duda — "no sé
# si compró" (desconocido, ya solicitada), "compró y algo no cuadra" (grupo a
# medias, etiqueta fallida, horas distintas) o un error después de intentar.
# Un RECHAZO documentado de Temu y la pasarela son un "no compró, seguro": NO
# detienen (Brandon, 1-oct: "que se sigan comprando, no es un bloqueante"), lo
# mismo que plan_cambio, no_comprable, sin_plan, reclamada, en_curso, detenida,
# aprobacion_vencida, sin_bitacora…
DETIENEN = frozenset({"grupo_a_medias", "ya_solicitada", "desconocido",
                      "desconocido_previo", "fallida", "eco_distinto", "error"})

# `rechazo` (Temu la rechazó y todavía le queda su único reintento) NO es
# permanente: se vuelve a planear cada `TEMU_COMPRA_GUIAS_REVISAR_MIN` para ver
# si su plan cambió. `rechazo_final` (ya se reintentó, o se liberó a mano) sí.
CLASES_PERMANENTES = frozenset({"anterior_corte", "sin_fecha_venta", "rechazo_final"})
# Fuera de la campana sólo lo que no requiere a nadie: ya tiene guía, el
# ensayo, y lo transitorio (se reintenta solo) mientras no pase de
# `TRANSITORIO_AVISA_MIN`. Las anteriores al corte SÍ suenan (30-sep: ninguna
# venta se queda sin guía en silencio).
CLASES_FUERA_DE_CAMPANA = frozenset({"transitorio", "ya_comprada", "ensayo"})
TRANSITORIO_AVISA_MIN = 60
# Una transitoria VENCIDA (el límite de Temu ya pasó) no espera la hora: suena
# en cuanto falla la segunda vuelta seguida.
TRANSITORIO_URGENTE_AVISA_MIN = 10
# "Ya tiene guía" que sigue en la cola tantas horas: su etiqueta existe pero su
# orden no nace (¿etiqueta fallida o anulada en Temu?). El refresco de guías
# corre cada 2 h: con 4 h ya se saltó dos.
YA_COMPRADA_AVISA_MIN = 240
# Clases que NO dejan una venta en reposo: se vuelven a mirar la vuelta
# siguiente (el tope del día se levanta solo a medianoche; un fallo
# transitorio se reintenta YA, no en media hora).
CLASES_SIN_REPOSO = frozenset({"tope", "transitorio"})
_NO_COTIZADO = "no se cotizó (fuera del grupo pedido)"
# Una vuelta que no pudo comprar por algo del SISTEMA; tres seguidas → campana.
ESTADOS_BLOQUEO = frozenset({"config_invalida", "cadena_incompleta", "sin_bitacora", "sin_cola",
                             "sin_plan", "plan_incompleto", "sin_turno", "turno_perdido",
                             "tope_vuelta", "pasarela", "error"})
BLOQUEO_AVISA_VUELTAS = 3

ETIQUETA_CLASE = {
    "anterior_corte": "venta anterior al corte", "sin_fecha_venta": "sin fecha de venta",
    "mezcla_corte": "combinado con una venta anterior al corte",
    "urgente": "URGENTE (límite de envío)", "cancelacion": "cancelación pedida",
    "tarde": "comprada TARDE (límite de Temu ya vencido)",
    "stock": "sin stock", "medidas": "medidas dudosas o sin medir",
    "paqueteria": "paquetería / cotización", "tope_costo": "Temu cotiza arriba del tope",
    "tope": "tope del día", "transitorio": "lectura fallida (se reintenta)",
    "ya_comprada": "ya tiene guía", "combinado_con_guia": "combinado con una guía ya comprada",
    "ya_comprada_atascada": "tiene etiqueta en Temu pero su orden no nace (¿fallida o anulada?)",
    "rechazo": "Temu la rechazó",
    "rechazo_final": "Temu la rechazó (ya no se reintenta sola)",
    "combinado_rechazado": "combinado con una venta rechazada",
    "simple": "modo simple (sólo una caja)",
    "ensayo": "ensayo: la compraría", "cola_larga": "fuera del alcance del plan",
    "otro": "otro motivo",
}

# ── Estado en memoria (sin PII) ──────────────────────────────────────────────
_VUELTA_LOCK = asyncio.Lock()
_ULTIMA: dict[str, Any] = {}
# {PO: {motivo, clase, urgente, visto, desde, ts}} — la última evaluación de
# las que requieren compra manual.
_MANUALES: dict[str, dict[str, Any]] = {}
# {PO: ISO del PRIMER fallo transitorio de su racha}. Vive aparte de _MANUALES
# (revisión del 30-sep): cuando el plan vuelve a dar la venta por comprable su
# entrada de _MANUALES se borra, y un fallo AL COMPRAR que se repite cada vuelta
# (plan que cambia, revisión final, reclamo) reiniciaba la hora cada vez y
# nunca llegaba a sonar. Se borra al comprarla, cuando sale de la cola o cuando
# queda manual por OTRA razón.
_TRANSITORIO_DESDE: dict[str, str] = {}
# {PO: límite de envío de Temu} visto en el último plan: con él, la marca del
# tope del día (que no llama a Temu) sabe qué venta ya está VENCIDA.
_LIMITES: dict[str, int] = {}
# Segunda red de la detención, por si kubera no deja escribirla. `guardada`
# dice si la bandera durable ya se escribió (si no, cada vuelta lo reintenta).
_DETENCION_MEM: dict[str, Any] | None = None
# Las URGENTES ya avisadas y el estado de campana que les tocó: una urgente
# NUEVA cambia el estado (y avisa); que una salga, no.
_URG_AVISADAS: set[str] = set()
_URG_ESTADO = "ok"
# Lo mismo para las RECHAZADAS por Temu: una nueva hace hablar a la campana.
_RECH_AVISADAS: set[str] = set()
_RECH_ESTADO = "ok"
# Los avisos de MEDIDAS ya dichos en la campana, por "SKU|tipo de aviso": el
# SKU que más se vende lleva el mismo aviso en cada guía, y sonar por cada
# compra sería ruido. Suena cuando aparece una combinación NUEVA.
_AVISOS_MEDIDA_VISTOS: set[str] = set()
# Cómo se reconoce cada tipo de aviso de `temu_guias_compra` (por su texto).
_TIPOS_AVISO_MEDIDA = (
    ("no cabe en J&T", "la caja NO cabe en J&T"),
    ("un lado mide", "un lado de más de 60 cm"),
    ("sus tres lados pasan", "no entra en el sobre de 60×60×40 de J&T"),
    ("sin flete con qué probarla", "medida sin flete con densidad de cartón master"),
    ("peso por pieza alto", "peso por pieza alto para una fila master"),
    ("se EXTRAPOLÓ", "peso extrapolado de la guía de 1 pieza"),
    ("menos de la mitad", "se declara menos de la mitad del volumen que a mano"),
    ("flete y columna", "el flete y piezas_por_caja no cuadran"),
    ("NO coinciden", "las fuentes no coinciden en el peso"),
)
# La racha de vueltas que no pudieron comprar por algo del sistema.
_RACHA: dict[str, Any] = {"estado": None, "n": 0, "avisada": False}
# ¿Se avisó que la cola pasa de TEMU_GUIAS_COLA_MAX?
_COLA_AVISADA = False
# Las rechazadas ya MIRADAS en Temu ("PO|reclamo": un reintento es otro reclamo
# y se vuelve a mirar). En memoria: tras un reinicio se miran una vez más.
_RECH_MIRADAS: set[str] = set()
# Las rechazadas cuyo ÚNICO reintento quedó listo (su plan cambió) y la vuelta
# terminó sin alcanzar a comprarlo (tope de la vuelta, tiempo, cortacircuitos):
# la vuelta siguiente no les renueva el reposo — van primero.
_REINTENTO_PENDIENTE: set[str] = set()


# ═════════════════════════════════════════════════════════════════════════════
#  1 · CONFIGURACIÓN
# ═════════════════════════════════════════════════════════════════════════════

def auto_habilitada() -> bool:
    """¿Compra sola? Exige LAS DOS banderas (nacen apagadas, regla 3)."""
    return tgc.compra_habilitada() and bool(getattr(settings, "temu_compra_guias_auto", False))


def ensayo() -> bool:
    """`TEMU_COMPRA_GUIAS_AUTO_ENSAYO`: la vuelta hace todo menos COMPRAR. Nace
    encendida: la primera compra real es un acto aparte (apagarla)."""
    return bool(getattr(settings, "temu_compra_guias_auto_ensayo", True))


def solo_simple() -> bool:
    """`TEMU_COMPRA_GUIAS_AUTO_SOLO_SIMPLE`: sólo compra sola UNA caja en UNA
    llamada sendType 0. Nace encendida: el sendType 1 con dos almacenes y el
    combinado (sendType 2) no están probados por API y se estrenan a mano."""
    return bool(getattr(settings, "temu_compra_guias_auto_solo_simple", True))


def compra_excede_jt() -> bool:
    """`TEMU_COMPRA_GUIAS_AUTO_EXCEDE_JT` (nace encendida: la especificación del
    1-oct): ¿se compra sola una caja que excede los límites de J&T si Temu la
    cotiza? Apagada, queda "compra manual · excede paquetería: partir en N"."""
    return bool(getattr(settings, "temu_compra_guias_auto_excede_jt", True))


def paqueteria_jt_primero() -> bool:
    """¿El PRIMER renglón de `TEMU_GUIAS_PAQUETERIA` deja a J&T adelante? Desde
    el 1-oct el orden es PRIORIDAD (antes sólo desempataba): una variable vieja
    con otra paquetería primero ("iMile,J&T") compraría TODO por ella."""
    prefs = tgc.preferencias_paqueteria()
    if not prefs:
        return False
    emp, _tipo = prefs[0]
    return bool(tgc._fila_es_jt(emp) or (emp == "*" and tgc.jt_ship_company_id()))  # noqa: SLF001


def _corte_txt(desde: datetime) -> str:
    """"2026-09-28" — o "2026-09-28 12:00" si el corte lleva hora (México): una
    venta del 28 a las 11:33 queda fuera de un corte del 28 a las 12:00, y con
    sólo el día nadie entendería por qué. PURA."""
    d = desde.astimezone(tgc.ZONA) if desde.tzinfo else desde
    return d.strftime("%Y-%m-%d" if (d.hour, d.minute, d.second) == (0, 0, 0) else "%Y-%m-%d %H:%M")


def _minutos() -> int:
    return max(5, int(getattr(settings, "temu_compra_guias_auto_min", 15) or 15))


def _revisar_min() -> int:
    return max(5, int(getattr(settings, "temu_compra_guias_revisar_min", 60) or 60))


def _verificar_max_min() -> int:
    return max(10, int(getattr(settings, "temu_compra_guias_verificar_max_min", 90) or 90))


def corte(crudo: str | None = None) -> datetime:
    """El inicio del corte (00:00 de ese día en México, o la hora dada). ⚠️
    LANZA ValueError si está mal escrito: la compra automática no corre."""
    texto = str(getattr(settings, "temu_compra_guias_desde", _CORTE_OMISION)
                if crudo is None else crudo).strip()
    if not texto:
        raise ValueError("TEMU_COMPRA_GUIAS_DESDE vacío")
    try:
        if len(texto) == 10:
            d = date.fromisoformat(texto)
            return datetime(d.year, d.month, d.day, tzinfo=tgc.ZONA)
        dt = datetime.fromisoformat(texto)
    except ValueError as exc:
        raise ValueError(f"TEMU_COMPRA_GUIAS_DESDE mal escrito ({texto!r}): usa AAAA-MM-DD") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=tgc.ZONA)


def topes() -> dict[str, float]:
    """Los topes vigentes. Un valor ilegible cuenta como 0 (no compra)."""
    def _n(nombre: str, omision: float) -> float:
        try:
            v = getattr(settings, nombre, omision)
            return max(0.0, float(omision if v is None else v))
        except (TypeError, ValueError):
            return 0.0
    return {"vuelta": int(_n("temu_compra_guias_max_vuelta", 1)),
            "dia": int(_n("temu_compra_guias_max_dia", 1)),
            "gasto_dia": _n("temu_compra_guias_gasto_max_dia", 1000.0),
            "caja": _n("temu_compra_guias_max_caja", 80.0),
            "venta": _n("temu_compra_guias_max_venta", 160.0)}


def presupuesto_s() -> float:
    """Cuántos segundos de una vuelta se dedican a EMPEZAR compras: los minutos
    del job (o los de la aprobación, si son menos) menos 4 de colchón para la
    última compra y su verificación. Con 15 min: 11. Lo que no cabe, la vuelta
    siguiente — nunca se enciman dos vueltas (el scheduler corre una a la vez)
    ni se compra con una aprobación a punto de vencer. PURA salvo config."""
    return float(max(120, min(_minutos(), tgc._aprobacion_min()) * 60 - 240))  # noqa: SLF001


def banderas() -> dict[str, Any]:
    fuentes, error = tgc.fuentes_medida()
    return {"compra_enabled": tgc.compra_habilitada(),
            "auto": bool(getattr(settings, "temu_compra_guias_auto", False)),
            "encendida": auto_habilitada(), "ensayo": ensayo(), "solo_simple": solo_simple(),
            "desde": str(getattr(settings, "temu_compra_guias_desde", _CORTE_OMISION) or ""),
            "paqueteria": str(getattr(settings, "temu_guias_paqueteria", "J&T,*") or "J&T,*"),
            "paqueteria_jt_primero": paqueteria_jt_primero(),
            "excede_jt_compra": compra_excede_jt(),
            "peso_guia_x1_por_piezas": tgc.guia_x1_por_piezas(),
            "jt_ship_company_id": tgc.jt_ship_company_id(),
            "limites_jt": tgc.limites_jt(), "peso_min_kg": tgc.piso_peso(),
            "sabado_alterno": tgc.sabado_alterno(),
            "minutos": _minutos(), "revisar_min": _revisar_min(),
            "verificar_max_min": _verificar_max_min(), "topes": topes(),
            "presupuesto_s": presupuesto_s(),
            "fuentes_medida": tgc.texto_fuentes(fuentes),
            "fuentes_medida_error": error}


def _dias_bitacora() -> int:
    """Cuántos días de la bitácora lee la vuelta: los de la ventana de espera
    (una compra sin verificar o un rechazo viven mientras su venta espere)."""
    try:
        from services import odoo_ventas
        return max(3, int(odoo_ventas._dias_espera()) + 1)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return 15


# ═════════════════════════════════════════════════════════════════════════════
#  2 · PIEZAS PURAS
# ═════════════════════════════════════════════════════════════════════════════

def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(v: Any) -> str | None:
    if isinstance(v, datetime):
        return v.astimezone(tgc.ZONA).isoformat(timespec="minutes") if v.tzinfo else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v) if v not in (None, "") else None


def _fecha(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def dia_mx(momento: datetime) -> date:
    return (momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)
            ).astimezone(tgc.ZONA).date()


def es_auto(fila: dict[str, Any]) -> bool:
    return str(fila.get("aprobado_por") or "").startswith(PREFIJO_AUTO)


def _lista(v: Any) -> list[dict[str, Any]]:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def reclamado_at(fila: dict[str, Any]) -> datetime | None:
    """Cuándo se reclamó (compró) la fila: lo que `comprar` guarda en su
    reparto; si no está, la última vez que se movió."""
    ts = [d for d in (_fecha(x.get("reclamado_at")) for x in _lista(fila.get("reparto"))) if d]
    return min(ts) if ts else _fecha(fila.get("actualizado_at"))


def resumen_hoy(filas: Iterable[dict[str, Any]], hoy: date) -> dict[str, Any]:
    """{compras, gasto_mxn, ventas} de las compras AUTOMÁTICAS reclamadas HOY
    (hora de México). Una compra es un reclamo (un grupo); el gasto suma cada
    caja una vez aunque la compartan dos PO. Lo 'rechazada'/'no_enviada' no
    costó nada y no cuenta. PURA."""
    reclamos: dict[str, set[str]] = {}
    cajas: dict[tuple[str, str], float] = {}
    for f in filas:
        if not es_auto(f) or f.get("estado") in tgc.ESTADOS_LIBRES:
            continue
        cuando = reclamado_at(f)
        if cuando is None or dia_mx(cuando) != hoy:
            continue
        rec = str(f.get("reclamo") or f.get("parent_order_sn"))
        reclamos.setdefault(rec, set()).add(str(f.get("parent_order_sn")))
        for x in _lista(f.get("reparto")):
            try:
                costo = float(x["costo_mxn"])
            except (KeyError, TypeError, ValueError):
                continue
            cajas[(rec, str(x.get("caja") or x.get("orderSn")))] = costo
    return {"compras": len(reclamos), "gasto_mxn": round(sum(cajas.values()), 2),
            "ventas": sorted({po for s in reclamos.values() for po in s})}


def costos_de_grupo(grupo: dict[str, Any]) -> dict[str, Any]:
    """Costo cotizado de cada caja del grupo, por venta (una caja compartida
    cuenta completa para cada venta: conservador) y total. PURA."""
    cajas: list[dict[str, Any]] = []
    por_venta: dict[str, float] = {}
    faltan: list[str] = []
    for ll in grupo.get("llamadas") or []:
        for p in ll.get("paquetes") or []:
            m = tgc.costo_de(p)
            cajas.append({"clave": p.get("clave"), "almacen": p.get("almacen"),
                          "costo_mxn": m, "paqueteria": tgc.paqueteria_de(p)})
            if m is None:
                faltan.append(str(p.get("almacen") or "?"))
                continue
            for po in {e.get("parentOrderSn") for e in p.get("renglones") or []}:
                por_venta[str(po)] = round(por_venta.get(str(po), 0.0) + m, 2)
    return {"cajas": cajas, "por_venta": por_venta, "faltan": faltan,
            "total": round(sum(c["costo_mxn"] or 0.0 for c in cajas), 2)}


def motivos_de(grupo: dict[str, Any]) -> list[str]:
    """Los motivos del grupo y de sus llamadas, sin repetir. PURA."""
    vistos: list[str] = []
    for m in list(grupo.get("motivos") or []) + [
            x for ll in grupo.get("llamadas") or [] for x in ll.get("motivos") or []]:
        if m and m not in vistos:
            vistos.append(str(m))
    return vistos


# Palabras que clasifican un "requiere compra manual" (para el resumen de la
# campana y el panel; no deciden si se compra). En orden: gana la primera.
_CLASES = (
    ("ya_comprada", ("ya tiene paquete", "ya muestra", "ya había una compra",
                     "compra abierta", "ya la compró", "la bitácora de compras la tiene")),
    ("cancelacion", ("pidió cancelar", "120012030", "cancelad")),
    ("stock", ("sin stock",)),
    ("transitorio", ("no se pudo", "no contestó", "se acabó el tiempo", "tope de ",
                     "todavía no se puede surtir", "incierto", "se reintenta")),
    # Antes que "medidas": todo motivo de una caja empieza con "caja de TEXCO:",
    # y "la paquetería preferida no se ofreció" no es un problema de medidas.
    ("paqueteria", ("paquetería", "cotiz", "moneda", "mxn")),
    ("medidas", ("peso", "medida", "medición", "caja", "densidad", "muestra", "catálogo",
                 "skus distintos", "interpolad")),
)


def clase_de(motivos: Iterable[str], urgente: bool = False) -> str:
    """La clase de un "requiere compra manual" por su MOTIVO. Una venta vencida
    ya no es una clase ("compra manual urgente" dejó de existir: se compra
    TARDE): si además no se puede comprar por otra cosa, la clase es esa otra
    cosa y la venta lleva `urgente=True` aparte. PURA."""
    txt = " ".join(motivos).lower()
    for clase, claves in _CLASES:
        if any(k in txt for k in claves):
            return clase
    return "otro"


def es_simple(grupo: dict[str, Any]) -> bool:
    """¿UNA caja en UNA llamada sendType 0? PURA."""
    lls = grupo.get("llamadas") or []
    return (len(lls) == 1 and lls[0].get("send_type") == 0
            and len(lls[0].get("paquetes") or []) == 1)


def reintento_pedido(fila: dict[str, Any] | None) -> bool:
    """¿Una persona pidió reintentar esta venta rechazada (botón «Reintentar»)? PURA."""
    return str((fila or {}).get("motivo") or "").startswith(MARCA_REINTENTAR)


def rechazo_agotado(fila: dict[str, Any] | None) -> bool:
    """¿A esta venta 'rechazada' ya NO le queda su reintento automático? PURA.
    Sí: la que ya se compró como reintento (`aprobado_por` = QUIEN_REINTENTO) y
    la que quedó sin respuesta y una persona liberó a mano (ahí hubo dinero en
    duda: su guía la compra una persona). No: la que alguien pidió reintentar."""
    if not fila:
        return True
    m = str(fila.get("motivo") or "")
    if m.startswith(MARCA_REINTENTAR):
        return False
    if m.startswith("liberada a mano"):
        return True
    return str(fila.get("aprobado_por") or "").startswith(QUIEN_REINTENTO)


def huella_de_plan(payload: Any, fecha_envio: Any = None, *, con_plazo: bool = False) -> str:
    """La huella MATERIAL del plan de una llamada: lo que Temu JUZGA de su
    payload de shipment.create — peso, caja, paquetería y canal, almacén,
    piezas, sendType—. SIN el plazo (`shipLaterLimitTime`) ni el día de entrega
    (revisión del 1-oct): con ellos, el "único reintento cuando cambie su plan"
    se disparaba solo al cambiar de día, con las mismas medidas y la misma
    paquetería, y Temu la rechazaba igual. `con_plazo=True` los incluye: sólo
    para un rechazo que fue POR el plazo (`CODIGOS_DE_PLAZO`). Tampoco lleva el
    momento de la vista previa ni el costo: dos planes iguales en vueltas
    distintas dan lo mismo. PURA."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    if isinstance(payload, dict) and not con_plazo:
        payload = {k: v for k, v in payload.items() if k not in ("shipLater", "shipLaterLimitTime")}
    return tgc.huella({"payload": payload,
                       "fecha_envio": str(_iso(fecha_envio) or "")[:10] if con_plazo else ""})


def plan_cambio(fila: dict[str, Any] | None, grupo: dict[str, Any], po: str) -> bool:
    """¿El plan de HOY para `po` es distinto, EN LO MATERIAL, del que Temu
    rechazó (el `payload` que guardó su fila: peso, caja, paquetería, almacén,
    piezas)? El plazo y el día sólo cuentan si el rechazo fue por el plazo. Sin
    con qué comparar → False (no se reintenta a ciegas). PURA."""
    viejo = (fila or {}).get("payload")
    if isinstance(viejo, str):
        try:
            viejo = json.loads(viejo)
        except ValueError:
            viejo = None
    ll = next((x for x in grupo.get("llamadas") or [] if po in (x.get("ventas") or [])), None)
    if not isinstance(viejo, dict) or not ll or not isinstance(ll.get("payload"), dict):
        return False
    ap = grupo.get("aprobacion") or {}
    plazo = str((fila or {}).get("codigo") or "") in CODIGOS_DE_PLAZO
    return (huella_de_plan(ll["payload"], ap.get("fecha_envio"), con_plazo=plazo)
            != huella_de_plan(viejo, (fila or {}).get("fecha_envio"), con_plazo=plazo))


def evaluar_grupo(grupo: dict[str, Any], desde: datetime, t: dict[str, float], *,
                  simple: bool = False,
                  rechazadas: Any = (),
                  recientes: Iterable[str] = (),
                  excede_jt: bool = True) -> dict[str, Any]:
    """
    ¿Se compra SOLO este grupo? PURA. Devuelve {decision, clase, motivo,
    urgente, tarde, costo, costos, ventas, por_venta}; `decision` ∈ comprar |
    manual | hecha. `por_venta` (opcional) = {PO: {clase, motivo}} cuando no
    todas las ventas del grupo están igual.

    El orden importa: primero lo que ninguna medida arregla (un rechazo previo,
    ya tener guía, el corte), después el planeador, el modo simple y al final
    el dinero. `simple` = modo de arranque (sólo una caja sendType 0);
    `rechazadas` = {PO: su fila 'rechazada' de la bitácora} de las ventas cuya
    compra automática Temu ya rechazó (un conjunto de PO sin fila = sin
    reintento): se reintentan UNA vez, sólo si su plan cambió (`plan_cambio`) o
    si alguien lo pidió; `recientes` = las que kubera registró DESDE el corte
    (con ellas, una venta sin fecha de Temu no se queda sin guía);
    `excede_jt=False` (`TEMU_COMPRA_GUIAS_AUTO_EXCEDE_JT` apagada) = una caja
    que excede los límites de J&T no se compra sola aunque Temu la cotice.

    Lo que ya NO es "compra manual" (Brandon, 30-sep: "sin perderse ninguna"):
    la venta VENCIDA (el planeador la compra TARDE con el plazo más corto), la
    que no trae límite de Temu (el plazo más corto), la que no trae fecha de
    venta pero kubera registró desde el corte, y el combinado que mezcla una
    venta anterior al corte con una nueva (se compra entero).
    """
    a_comprar = [str(x) for x in grupo.get("a_comprar") or []]
    base: dict[str, Any] = {"ventas": a_comprar, "decision": "manual", "clase": "otro",
                            "motivo": "", "urgente": False, "tarde": False, "costo": None,
                            "costos": None}
    if not a_comprar:
        return {**base, "ventas": list(grupo.get("ventas") or []), "decision": "hecha",
                "clase": "hecha", "motivo": "la guía de este grupo ya la compró el panel"}
    motivos = motivos_de(grupo)
    fecha = grupo.get("fecha") or {}
    # Vencida: si se puede comprar, se compra TARDE; si no, es URGENTE que una
    # persona la compre (la campana lo dice aparte).
    urgente = bool(fecha.get("urgente") or fecha.get("tarde"))
    otras = lambda lst: (f" (el grupo incluye {', '.join(lst)})"  # noqa: E731
                         if len(a_comprar) > 1 else "")

    rech_info: dict[str, Any] = (dict(rechazadas) if isinstance(rechazadas, dict)
                                 else {str(x): None for x in rechazadas})
    rech = [po for po in a_comprar if po in rech_info]

    def _con_rechazo(clase: str, lista: list[str]) -> dict[str, Any]:
        """El grupo queda para una persona por las rechazadas de `lista`; la
        que sólo va en su grupo se vuelve a mirar (si la rechazada sale del
        grupo, puede comprarse)."""
        txt0 = (texto_rechazo(rech_info[lista[0]]) if rech_info.get(lista[0])
                else f"Temu rechazó la compra automática de {', '.join(lista)}: su guía la compra "
                     "una persona")
        return {**base, "clase": clase, "urgente": urgente, "motivo": (txt0 + otras(lista))[:500],
                "por_venta": {
                    **{po: {"clase": clase, "motivo": (texto_rechazo(rech_info[po])
                                                       if rech_info.get(po) else txt0)[:500]}
                       for po in lista},
                    **{po: {"clase": "combinado_rechazado",
                            "motivo": (f"va en el mismo envío que {', '.join(lista)}, cuya compra "
                                       "automática Temu rechazó: el grupo se compra entero — "
                                       "cómprala a mano")}
                       for po in a_comprar if po not in lista}}}

    finales = [po for po in rech if rechazo_agotado(rech_info[po])]
    if finales:
        # Ya se reintentó (o se liberó a mano): NUNCA más sola.
        return _con_rechazo("rechazo_final", finales)

    if not grupo.get("comprable"):
        # YA TIENE GUÍA (la compró una persona, o hay una compra abierta): no
        # hay nada que comprar. POR VENTA: en un combinado donde sólo una tiene
        # guía (el planeador ya separa ese caso; esto queda de red), la otra NO
        # "ya tiene guía" — requiere que alguien decida.
        propias = {po: [m for m in motivos if po in m] for po in a_comprar}
        ya = [po for po in a_comprar if clase_de(propias[po]) == "ya_comprada"]
        if ya and len(ya) == len(a_comprar):
            return {**base, "clase": "ya_comprada",
                    "motivo": ("; ".join(motivos) or "ya tiene guía")[:500]}
        if ya:
            resto = [po for po in a_comprar if po not in ya]
            txt = (f"combinado: {', '.join(ya)} ya tiene guía y {', '.join(resto)} no — el grupo "
                   "se compra entero o nada: revísalo a mano (¿comprar aparte la de "
                   f"{', '.join(resto)}?)")
            return {**base, "clase": "combinado_con_guia", "urgente": urgente, "motivo": txt,
                    "por_venta": {**{po: {"clase": "ya_comprada",
                                          "motivo": ("; ".join(propias[po]) or "ya tiene guía")[:500]}
                                     for po in ya},
                                  **{po: {"clase": "combinado_con_guia", "motivo": txt}
                                     for po in resto}}}

    ileg = [po for po in a_comprar if po in set(grupo.get("ilegibles") or [])]
    if ileg:
        # Una LECTURA fallida no es un dato: se reintenta (no es "sin fecha").
        return {**base, "clase": "transitorio",
                "motivo": (f"no se pudo leer el detalle de {', '.join(ileg)} en Temu: se "
                           "reintenta sola")}
    ts = grupo.get("ventas_ts") or {}
    fechas = {po: tgc._entero(ts.get(po)) for po in a_comprar}  # noqa: SLF001
    kubera = {str(x) for x in recientes}
    sin_fecha = [po for po in a_comprar if not fechas[po]]
    desconocidas = [po for po in sin_fecha if po not in kubera]
    if desconocidas:
        return {**base, "clase": "sin_fecha_venta",
                "motivo": (f"Temu no dio la fecha de venta de {', '.join(desconocidas)} y kubera no "
                           "la registró desde el corte: no se sabe si es anterior — la compra una "
                           "persona")}
    corte_ts = int(desde.timestamp())
    antes = [po for po in a_comprar if fechas[po] and fechas[po] < corte_ts]
    if antes and len(antes) == len(a_comprar):
        return {**base, "clase": "anterior_corte",
                "motivo": (f"venta anterior al corte ({_corte_txt(desde)}, "
                           "TEMU_COMPRA_GUIAS_DESDE): su guía la compra una persona")}
    notas: list[str] = []
    if antes:
        # Un combinado que MEZCLA: va entero (30-sep: ninguna se pierde).
        notas.append(f"incluye {', '.join(antes)}, anterior al corte ({_corte_txt(desde)}): "
                     "el grupo va entero")
    if sin_fecha:
        notas.append(f"Temu no dio la fecha de venta de {', '.join(sin_fecha)}: kubera la registró "
                     "desde el corte")
    if not grupo.get("comprable") or not grupo.get("aprobacion"):
        mot = "; ".join(motivos) or "el planeador no la da por comprable"
        if rech:
            mot = f"{texto_rechazo(rech_info[rech[0]])} · ahora además: {mot}"
        return {**base, "clase": clase_de(motivos), "urgente": urgente, "motivo": mot[:500]}
    reintento: str | None = None
    if rech:
        # Temu ya rechazó esta venta. NO se reintenta en bucle: sólo si alguien
        # lo pidió, o UNA vez si su plan cambió en lo MATERIAL (`plan_cambio`).
        pedidas = [po for po in rech if reintento_pedido(rech_info[po])]
        iguales = [po for po in rech if po not in pedidas
                   and not plan_cambio(rech_info[po], grupo, po)]
        if iguales:
            return _con_rechazo("rechazo", iguales)
        reintento = "manual" if len(pedidas) == len(rech) else "auto"
        notas.append("REINTENTO tras un rechazo de Temu ("
                     + ("lo pidió una persona" if reintento == "manual" else "su plan cambió")
                     + f"): {', '.join(rech)}")
    if simple and not es_simple(grupo):
        lls = grupo.get("llamadas") or []
        return {**base, "clase": "simple", "urgente": urgente,
                "motivo": (f"modo de arranque (TEMU_COMPRA_GUIAS_AUTO_SOLO_SIMPLE): sólo se "
                           f"compra sola UNA caja sendType 0; ésta lleva {len(lls)} llamada(s) "
                           f"sendType {sorted({ll.get('send_type') for ll in lls}, key=str)} con "
                           f"{sum(len(ll.get('paquetes') or []) for ll in lls)} caja(s) — "
                           "cómprala a mano")}
    if not excede_jt:
        # `TEMU_COMPRA_GUIAS_AUTO_EXCEDE_JT` apagada: la caja que excede los
        # límites de J&T no se compra sola aunque Temu la cotice.
        gordas = [m for m in (grupo.get("aprobacion") or {}).get("medidas") or []
                  if m.get("cabe_jt") is False]
        if gordas:
            g0 = gordas[0]
            return {**base, "clase": "medidas", "urgente": urgente,
                    "motivo": (f"excede paquetería: la caja de {g0.get('contenido') or '?'} no cabe en "
                               f"J&T — {g0.get('partir') or 'partir en varias cajas'}; "
                               "TEMU_COMPRA_GUIAS_AUTO_EXCEDE_JT está apagada: cómprala a mano")[:500]}
    cs = costos_de_grupo(grupo)
    if cs["faltan"]:
        return {**base, "clase": "paqueteria", "costos": cs, "urgente": urgente,
                "motivo": (f"sin costo cotizado EN PESOS para la caja de {', '.join(cs['faltan'])}")}
    caras = [c for c in cs["cajas"] if c["costo_mxn"] > t["caja"]]
    if caras:
        c = caras[0]
        return {**base, "clase": "tope_costo", "costos": cs, "urgente": urgente,
                "motivo": (f"Temu cotiza MX${c['costo_mxn']:.2f} por la caja de {c['almacen']} "
                           f"({c['paqueteria'] or '?'}) y el tope por caja es MX${t['caja']:.2f} "
                           "(TEMU_COMPRA_GUIAS_MAX_CAJA)")}
    caras_v = {po: m for po, m in cs["por_venta"].items() if m > t["venta"]}
    if caras_v:
        po, m = next(iter(sorted(caras_v.items())))
        return {**base, "clase": "tope_costo", "costos": cs, "urgente": urgente,
                "motivo": (f"la guía de {po} costaría MX${m:.2f} y el tope por venta es "
                           f"MX${t['venta']:.2f} (TEMU_COMPRA_GUIAS_MAX_VENTA)")}
    ap = grupo["aprobacion"]
    paqs = sorted({c["paqueteria"] for c in cs["cajas"] if c["paqueteria"]})
    medidas = tgc.texto_medidas(ap.get("medidas") or [])
    no_jt = [str(m.get("no_jt")) for m in ap.get("medidas") or [] if m.get("no_jt")]
    if no_jt:
        notas.append(f"NO va por J&T: {no_jt[0][:200]}")
    avisos_medida = [{"de": str(m.get("contenido") or ""), "aviso": str(a)}
                     for m in ap.get("medidas") or [] for a in m.get("avisos") or []]
    return {**base, "decision": "comprar", "clase": "comprable", "costo": cs["total"],
            "costos": cs, "tarde": bool(ap.get("tarde")), "reintento": reintento,
            "no_jt": no_jt, "avisos_medida": avisos_medida,
            "motivo": (f"{len(cs['cajas'])} caja(s) · {', '.join(paqs) or '?'} · "
                       f"MX${cs['total']:.2f} · entrega {ap.get('dia_envio')} "
                       f"{ap.get('fecha_envio')} ({ap.get('horas')} h)"
                       + (f" · {tgc.TEXTO_TARDE}" if ap.get("tarde")
                          else " · sin límite de Temu: el plazo más corto" if ap.get("sin_limite")
                          else " · fecha adelantada por el límite de Temu" if ap.get("ajustada")
                          else "")
                       + (f" · medidas: {medidas}" if medidas else "")
                       + "".join(f" · {n}" for n in notas))}


def esperado_por_venta(reparto_real: Iterable[dict[str, Any]],
                       guias: dict[str, str]) -> dict[int, set[str]]:
    """{almacén de Odoo: {guías de las cajas de ese almacén}} de UNA venta,
    con el reparto que devolvió Temu y la guía de cada paquete. PURA."""
    salida: dict[int, set[str]] = {}
    for x in reparto_real or []:
        a = tgc._entero(x.get("almacen_id"))  # noqa: SLF001
        if a is None:
            continue
        salida.setdefault(a, set())
        g = str(guias.get(str(x.get("packageSn") or "")) or "").strip()
        if g:
            salida[a].add(g)
    return salida


def piezas_por_almacen(reparto_real: Iterable[dict[str, Any]]) -> dict[int, dict[str, int] | None]:
    """{almacén de Odoo: {sku: piezas}} de UNA venta según la guía (None si a
    un renglón de ese almacén le falta el SKU: no se sabe, no se compara). PURA."""
    salida: dict[int, dict[str, int] | None] = {}
    for x in reparto_real or []:
        a = tgc._entero(x.get("almacen_id"))  # noqa: SLF001
        if a is None:
            continue
        sku = str(x.get("sku") or "").strip()
        n = tgc._entero(x.get("quantity")) or 0  # noqa: SLF001
        if a in salida and salida[a] is None:
            continue
        if not sku:
            salida[a] = None
            continue
        d = salida.setdefault(a, {})
        d[sku] = d.get(sku, 0) + n  # type: ignore[union-attr]
    return salida


def verificar_contra_odoo(esperado: dict[int, set[str]],
                          ordenes: list[dict[str, Any]],
                          piezas: dict[int, dict[str, int] | None] | None = None
                          ) -> dict[str, Any]:
    """
    ¿La venta quedó en Odoo como la guía dice? PURA.

    `esperado` = {almacén: {guías de sus cajas}} (conjunto vacío = no se sabe
    cuál, basta con que tenga una). `piezas` = {almacén: {sku: piezas}} de la
    guía (None = no se sabe). `ordenes` = `odoo_ventas.leer_venta_odoo`.

    `ok` · cada orden confirmada, en un almacén de la guía, con UNA orden por
          almacén y las PIEZAS que la guía saca de ahí, la guía de SU caja en
          cada entrega de salida y el PDF de esa guía ("<guía>.pdf");
    `pendiente` · falta algo que la vuelta siguiente puede poner (la orden aún
          no nace, entrega sin guía, sin PDF, en borrador);
    `contradiccion` · algo quedó MAL escrito (almacén que no es el de la guía,
          piezas distintas de las de la guía, la guía de otra caja, el PDF de
          otra guía, dos órdenes en el mismo almacén): no se arregla solo y
          detiene el job.
    """
    contra: list[str] = []
    faltas: list[str] = []
    resumen: list[dict[str, Any]] = []
    if not ordenes:
        return {"estado": "pendiente", "motivos": ["la orden todavía no nace en Odoo"],
                "ordenes": []}
    almacenes = set(esperado)
    vistos: dict[int | None, int] = {}
    for o in ordenes:
        nombre = str(o.get("nombre") or o.get("odoo_id") or "?")
        a = o.get("almacen_id")
        vistos[a] = vistos.get(a, 0) + 1
        guias_orden = [str(e.get("guia") or "").strip() for e in o.get("entregas") or []]
        resumen.append({"nombre": nombre, "almacen_id": a, "estado": o.get("estado"),
                        "guias": sorted({g for g in guias_orden if g}), "pdf": bool(o.get("pdf"))})
        if almacenes and a not in almacenes:
            contra.append(f"{nombre} nació en el almacén {a} y la guía sale de {sorted(almacenes)}")
            continue
        if o.get("estado") not in ("sale", "done"):
            faltas.append(f"{nombre} está en '{o.get('estado')}' (sin confirmar)")
        if not guias_orden:
            faltas.append(f"{nombre} no tiene entrega de salida")
        propias = {g for g in esperado.get(a, set()) if g}
        for g in guias_orden:
            if not g:
                faltas.append(f"{nombre}: una entrega todavía sin guía")
            elif propias and g not in propias:
                contra.append(f"{nombre}: su entrega lleva la guía {g} y la de su caja es "
                              f"{' / '.join(sorted(propias))}")
        # LAS PIEZAS: lo que esta orden saca de SU almacén = lo que la guía dice.
        if piezas and "lineas" in o and piezas.get(a) is not None:
            lleva: dict[str, int] = {}
            for ln in o.get("lineas") or []:
                s = str(ln.get("sku") or "").strip()
                try:
                    n = float(ln.get("cantidad") or 0)
                except (TypeError, ValueError):
                    n = 0.0
                lleva[s] = lleva.get(s, 0) + (int(n) if n == int(n) else n)  # type: ignore[assignment]
            if lleva != piezas[a]:
                contra.append(f"{nombre} lleva {dict(sorted(lleva.items()))} y la guía de su almacén "
                              f"({a}) lleva {dict(sorted(piezas[a].items()))}")  # type: ignore[union-attr]
        # EL PDF: el de SU guía. El refresco lo sube como "<guía>.pdf" y nunca
        # pisa uno que ya existe: uno con otro nombre es de otra guía.
        if not o.get("pdf"):
            faltas.append(f"{nombre}: sin PDF en «Subir guía»")
        else:
            nombre_pdf = str(o.get("pdf_nombre") or "").strip()
            validos = {f"{g}.pdf".lower() for g in guias_orden if g}
            if "pdf_nombre" in o and not nombre_pdf:
                faltas.append(f"{nombre}: el PDF no tiene nombre: no se sabe de qué guía es")
            elif nombre_pdf and validos and nombre_pdf.lower() not in validos:
                contra.append(f"{nombre}: el PDF en «Subir guía» es {nombre_pdf} y su guía es "
                              f"{' / '.join(sorted(g for g in guias_orden if g))}")
    dobles = sorted(str(a) for a, n in vistos.items() if n > 1)
    if dobles:
        contra.append(f"hay más de una orden en el almacén {', '.join(dobles)}")
    faltan = sorted(almacenes - {a for a in vistos if a is not None})
    if faltan and not contra:
        faltas.append(f"falta la orden del almacén {', '.join(str(x) for x in faltan)}")
    estado = "contradiccion" if contra else ("pendiente" if faltas else "ok")
    return {"estado": estado, "motivos": contra + faltas, "ordenes": resumen}


def texto_verificacion(v: dict[str, Any]) -> str:
    """El `motivo` que queda en la bitácora de compras. Empieza con su marca."""
    marca = {"ok": VERIF_OK, "contradiccion": VERIF_NO}.get(v.get("estado"), VERIF_PEND)
    if v.get("estado") == "ok":
        partes = [f"{o['nombre']} ({o['almacen_id']}) confirmada, guía "
                  f"{'/'.join(o['guias']) or '?'}, PDF" for o in v.get("ordenes") or []]
        return f"{marca} · " + "; ".join(partes)
    return f"{marca} · " + "; ".join(v.get("motivos") or ["sin detalle"])


def ya_verificada(fila: dict[str, Any]) -> bool:
    m = str(fila.get("motivo") or "")
    return m.startswith(VERIF_OK) or m.startswith(VERIF_NO)


def plan_incompleto(plan: dict[str, Any]) -> str | None:
    """Fallas que dejan el plan ENTERO a ciegas: no se marca nada como manual
    (sería mentira) y no se compra; la vuelta siguiente lo reintenta. PURA."""
    faltas = []
    if plan.get("odoo_error"):
        faltas.append(f"Odoo no contestó ({str(plan['odoo_error'])[:100]})")
    if not (plan.get("combinado") or {}).get("consultado"):
        faltas.append("no se pudo consultar qué órdenes quiere Temu juntas")
    if not (plan.get("bitacora") or {}).get("leida"):
        faltas.append(str((plan.get("bitacora") or {}).get("error") or "bitácora de compras ilegible"))
    if plan.get("ajena_error"):
        faltas.append("no se pudo leer lo que esperan otros canales")
    # FALLA CERRADO en el PESO y la CAJA (revisión del 1-oct). Sin el catálogo
    # no hay flete con qué reconocer el cartón master (se declaraba 52×42×35
    # por un juguete, 15 kg facturables en vez de 1.89) y las guías de Temu no
    # se usan; sin las mediciones de almacén o sin el historial de guías (que
    # ya viene sin las que compró este sistema) no se declararía "el menor". Una
    # falla aislada la frenaba la huella (plan y re-plan no coinciden); una
    # persistente compraba así el lote entero.
    if plan.get("catalogo_error"):
        faltas.append("no se pudo leer el catálogo (costing.costos_validados: "
                      f"{str(plan['catalogo_error'])[:80]}): sin el flete se declararía el cartón "
                      "master por una pieza")
    if plan.get("medidas_error"):
        faltas.append(f"no se pudieron leer las mediciones de almacén ({str(plan['medidas_error'])[:80]})")
    errs = (plan.get("historial") or {}).get("errores") or {}
    if errs.get("kubera"):
        faltas.append("no se pudo leer el historial de guías de Temu (ni quitarle las que compró este "
                      f"sistema: {str(errs['kubera'])[:80]}): no se declararía el menor")
    # La cola TRUNCADA ya no detiene la vuelta (30-sep): se corta por lo más
    # NUEVO, así que todo lo que se compra ve completas las ventas anteriores
    # que le quitan stock. Las que no caben esperan y la campana lo dice.
    return "; ".join(faltas) or None


def texto_rechazo(fila: dict[str, Any]) -> str:
    """"compra manual · Temu la rechazó: <código y texto>" de una venta con una
    compra automática 'rechazada', y si le queda su reintento. PURA."""
    m = str(fila.get("motivo") or "")
    if m.startswith("liberada a mano"):
        return (f"la compra automática quedó sin respuesta y se liberó a mano ({m[:160]}): su "
                "guía la compra una persona")
    cod = fila.get("codigo") or "sin código"
    if m.startswith(MARCA_REINTENTAR):
        return (f"Temu la rechazó ({cod}) y una persona pidió reintentarla: se compra en la vuelta "
                "siguiente")
    cola = ("ya se reintentó una vez con otro plan y Temu la volvió a rechazar: su guía la compra "
            "una persona" if rechazo_agotado(fila)
            else "se reintenta UNA vez sola cuando cambie su plan (peso, caja, paquetería, almacén o "
                 "piezas; el cambio de día no cuenta), o al pulsar «Reintentar»")
    return f"compra manual · Temu la rechazó: {cod} {m[:160]} — {cola}"


def racha_de_rechazos(filas: Iterable[dict[str, Any]],
                      ahora: datetime | None = None) -> dict[str, Any]:
    """
    La racha ABIERTA de rechazos de Temu, contada de la bitácora DURABLE (el
    cortacircuitos tenía memoria sólo dentro de una vuelta: cortaba a los 3 y
    la vuelta siguiente mandaba otras 3 contra el mismo fallo). PURA (salvo el
    reloj, si no se pasa `ahora`).

    Cada RECLAMO automático es un evento (un grupo = una llamada a Temu), en el
    momento en que se reclamó: 'rechazada' con su código, o compra hecha
    ('comprada' / 'pendiente'). Del más nuevo hacia atrás, hasta la primera
    compra o hasta `RACHA_VENTANA_H` horas: `n` = rechazos seguidos con el MISMO
    código que el más nuevo, `total` = rechazos seguidos de cualquier código.
    Una compra cierra la racha; lo liberado a mano, lo 'no_enviada' y lo abierto
    no cuentan. Devuelve {codigo, n, total, ventas}.
    """
    momento = ahora or _ahora()
    eventos: dict[str, dict[str, Any]] = {}
    for f in filas or []:
        if not es_auto(f):
            continue
        cuando = reclamado_at(f)
        if cuando is None:
            continue
        estado = f.get("estado")
        if estado in tgc.ESTADOS_HECHOS:
            tipo, cod = "hecha", None
        elif (estado == "rechazada" and f.get("codigo")
              and not str(f.get("motivo") or "").startswith("liberada a mano")):
            tipo, cod = "rechazada", str(f.get("codigo"))
        else:
            continue
        ev = eventos.setdefault(str(f.get("reclamo") or f.get("parent_order_sn")),
                                {"tipo": tipo, "codigo": cod, "cuando": cuando, "ventas": []})
        if tipo == "hecha":
            ev["tipo"] = "hecha"
        elif ev["codigo"] is None:
            ev["codigo"] = cod
        ev["cuando"] = min(ev["cuando"], cuando)
        ev["ventas"].append(str(f.get("parent_order_sn")))
    codigo: str | None = None
    n = total = 0
    mismo = True
    ventas: list[str] = []
    for ev in sorted(eventos.values(), key=lambda e: e["cuando"], reverse=True):
        if ev["tipo"] == "hecha" or (momento - ev["cuando"]).total_seconds() > RACHA_VENTANA_H * 3600:
            break
        total += 1
        if codigo is None:
            codigo = ev["codigo"]
        if mismo and ev["codigo"] == codigo:
            n += 1
            ventas.extend(ev["ventas"])
        else:
            mismo = False
    return {"codigo": codigo if n else None, "n": n, "total": total, "ventas": sorted(ventas)}


def estado_urgentes(urgentes: Iterable[str], avisadas: set[str], previo: str,
                    prefijo: str = "urgente") -> str:
    """El estado de la campana de URGENTES (y, con otro `prefijo`, la de
    RECHAZADAS). PURA. Una NUEVA cambia el estado (la campana habla); que una
    salga, no (sigue el mismo). Sin ninguna, 'ok'. Cabe en
    `alertas_estado.estado` (30)."""
    lista = sorted(set(urgentes))
    if not lista:
        return "ok"
    if previo != "ok" and set(lista) <= avisadas:
        return previo
    return f"{prefijo}:" + hashlib.sha1(",".join(lista).encode("utf-8")).hexdigest()[:10]


# ═════════════════════════════════════════════════════════════════════════════
#  3 · LO DURABLE (kubera). ⚠️ Todo BLOQUEA: se llama desde hilos.
# ═════════════════════════════════════════════════════════════════════════════

def _leer_detencion() -> dict[str, Any] | None:
    """La detención guardada, o None si no está detenida. LANZA si no se puede
    leer (quien llama no compra)."""
    from services import supabase_db as sdb
    filas = sdb.fetch_all(
        """/* tga:detencion */ select valor, motivo, actualizado_at, actualizado_por
             from ops.automatizacion_flags where flag = %(f)s""", {"f": FLAG_DETENIDA})
    f = dict(filas[0]) if filas else None
    return f if f and f.get("valor") else None


def _escribir_detencion(valor: bool, motivo: str, quien: str) -> None:
    from services import supabase_db as sdb

    def _hacer() -> None:
        with sdb.get_cursor() as cur:
            cur.execute(
                """/* tga:detener */ insert into ops.automatizacion_flags
                       (flag, valor, motivo, actualizado_at, actualizado_por)
                   values (%(f)s, %(v)s, %(m)s, now(), %(q)s)
                   on conflict (flag) do update set
                       valor = excluded.valor, motivo = excluded.motivo,
                       actualizado_at = now(), actualizado_por = excluded.actualizado_por""",
                {"f": FLAG_DETENIDA, "v": bool(valor), "m": (motivo or "")[:300] or None,
                 "q": (quien or "")[:120] or None})

    sdb.reintentar_transitorio(_hacer)


def _marcar_filas(pos: list[str], motivo: str) -> int:
    """La detención, escrita en las filas AUTOMÁTICAS de esas ventas que no la
    sostienen solas (comprada, rechazada, no enviada: las abiertas y las
    'pendiente' ya bloquean). Es la red cuando la bandera no se pudo guardar:
    sobrevive a un reinicio. ⚠️ BLOQUEA. LANZA."""
    from services import supabase_db as sdb
    if not pos:
        return 0

    def _hacer() -> int:
        with sdb.get_cursor() as cur:
            cur.execute(
                """/* tga:marcar */ update ops.temu_guias_compras
                      set motivo = %(m)s, actualizado_at = now()
                    where parent_order_sn = any(%(p)s) and aprobado_por like %(a)s
                      and estado in ('comprada', 'rechazada', 'no_enviada')""",
                {"m": f"{MARCA_DETENIDA} · {motivo}"[:300], "p": list(pos), "a": f"{PREFIJO_AUTO}%"})
            return int(cur.rowcount or 0)

    return sdb.reintentar_transitorio(_hacer)


def _desmarcar_filas(quien: str) -> int:
    """Al liberar: las filas marcadas `auto:detenida` dejan de detener. La
    comprada queda "revisada a mano" (no se re-verifica: una persona ya la
    miró); las demás, "liberada por …". ⚠️ BLOQUEA. LANZA."""
    from services import supabase_db as sdb

    def _hacer() -> int:
        with sdb.get_cursor() as cur:
            cur.execute(
                """/* tga:desmarcar */ update ops.temu_guias_compras
                      set motivo = case when estado = 'comprada' then %(no)s else %(otro)s end,
                          actualizado_at = now()
                    where motivo like %(marca)s and aprobado_por like %(a)s""",
                {"no": f"{VERIF_NO} · revisada a mano por {quien or '?'} al liberar"[:300],
                 "otro": f"liberada por {quien or '?'}"[:300],
                 "marca": f"{MARCA_DETENIDA}%", "a": f"{PREFIJO_AUTO}%"})
            return int(cur.rowcount or 0)

    return sdb.reintentar_transitorio(_hacer)


def _bloqueantes() -> list[dict[str, Any]]:
    """Lo que impide LIBERAR el job: las compras abiertas (de cualquiera) y las
    etiquetas AUTOMÁTICAS que siguen en aplicación en Temu sin que nadie las
    haya aceptado. ⚠️ BLOQUEA. LANZA."""
    salida = [dict(a) for a in tgc.abiertas()]
    for f in tgc.pendientes_auto():
        if not str(f.get("motivo") or "").startswith(PEND_ACEPTADA):
            salida.append(dict(f))
    return salida


def _turno(sql_tag: str, sql: str, token: str) -> bool:
    from services import supabase_db as sdb

    def _hacer() -> bool:
        with sdb.get_cursor() as cur:
            cur.execute(f"/* {sql_tag} */ " + sql,
                        {"f": FLAG_TURNO, "t": token, "ttl": TURNO_MIN})
            fila = cur.fetchone()
            if not fila:
                return False
            valor = fila.get("motivo") if isinstance(fila, dict) else fila[0]
            return valor == token

    return bool(sdb.reintentar_transitorio(_hacer))


def _tomar_turno(token: str) -> bool:
    """Una sola vuelta a la vez en TODO el despliegue: el turno se toma si está
    libre o si el de otro venció (proceso muerto). LANZA si kubera no contesta."""
    return _turno("tga:turno",
                  """insert into ops.automatizacion_flags as f
                         (flag, valor, motivo, actualizado_at, actualizado_por)
                     values (%(f)s, true, %(t)s, now(), 'auto')
                     on conflict (flag) do update set
                         valor = true, motivo = excluded.motivo, actualizado_at = now(),
                         actualizado_por = 'auto'
                      where f.valor = false
                         or f.actualizado_at < now() - make_interval(mins => %(ttl)s)
                     returning motivo""", token)


def _renovar_turno(token: str) -> bool:
    """¿Sigue siendo mío el turno? (y lo refresca). Si no, no se compra más."""
    return _turno("tga:renovar",
                  """update ops.automatizacion_flags set actualizado_at = now()
                      where flag = %(f)s and motivo = %(t)s and valor = true
                     returning motivo""", token)


def _soltar_turno(token: str) -> None:
    from services import supabase_db as sdb

    def _hacer() -> None:
        with sdb.get_cursor() as cur:
            cur.execute(
                """/* tga:soltar */ update ops.automatizacion_flags
                      set valor = false, actualizado_at = now()
                    where flag = %(f)s and motivo = %(t)s""", {"f": FLAG_TURNO, "t": token})

    sdb.reintentar_transitorio(_hacer)


def _prerrequisitos() -> str | None:
    """Lo que falta de la cadena para que la orden NAZCA con su guía, o None.
    ⚠️ BLOQUEA (los interruptores se leen de kubera). Falla cerrado."""
    from services import odoo_ventas as ov
    faltan = []
    if not bool(getattr(settings, "temu_guias_enabled", False)):
        faltan.append("el trabajo de guías de Temu está apagado (TEMU_GUIAS_ENABLED)")
    if bool(getattr(settings, "odoo_ventas_solo_registro", True)):
        faltan.append("Odoo está en observación (ODOO_VENTAS_SOLO_REGISTRO)")
    if not bool(getattr(settings, "odoo_ventas_confirmar", False)):
        faltan.append("la orden no se confirmaría (ODOO_VENTAS_CONFIRMAR)")
    try:
        if not ov.habilitado():
            faltan.append("el interruptor general de Automatización está apagado")
        if not ov.canal_activo(CANAL):
            faltan.append("el canal Temu está apagado en Automatización")
        if not ov.espera_guia_activa(CANAL):
            faltan.append("Temu no está en «la orden nace con la guía»")
    except Exception as exc:  # noqa: BLE001
        faltan.append(f"no se pudieron leer los interruptores ({str(exc)[:100]})")
    return "; ".join(faltan) or None


async def _campana(tipo: str, estado: str, texto: str, texto_ok: str | None = None,
                   nivel: str = "🔴", recordatorio_h: int = 24) -> None:
    """La campana por CAMBIO DE ESTADO (no una alerta por venta). Nunca lanza."""
    try:
        from services import alertas
        await asyncio.to_thread(alertas.avisar_estado, tipo, estado, texto, texto_ok,
                                nivel, recordatorio_h)
    except Exception as exc:  # noqa: BLE001
        log.debug("campana %s: %s", tipo, exc)


# ═════════════════════════════════════════════════════════════════════════════
#  4 · LA DETENCIÓN
# ═════════════════════════════════════════════════════════════════════════════

async def _detener(motivo: str, pos: Iterable[str], accion: str) -> str:
    """Detiene el job: memoria + `ops.automatizacion_flags` + campana. Si la
    bandera no se puede escribir, la detención queda MARCADA en las filas de
    la bitácora (sobrevive a un reinicio) y cada vuelta reintenta la bandera.
    Nunca lanza. Devuelve el texto guardado."""
    global _DETENCION_MEM
    pos = [str(p) for p in pos]
    # Las ventas VAN PRIMERO ("[PO-…, PO-…] motivo"): el motivo se corta a 300
    # y el panel las lee de ahí para ofrecer "Conciliar" de cada una.
    txt = ((f"[{', '.join(pos)}] " if pos else "") + motivo)[:300]
    _DETENCION_MEM = {"desde": _iso(_ahora()), "motivo": txt, "ventas": pos, "accion": accion,
                      "guardada": False}
    log.error("COMPRA AUTOMÁTICA DE GUÍAS DE TEMU DETENIDA (%s): %s", accion, txt)
    try:
        await asyncio.shield(asyncio.to_thread(_escribir_detencion, True, txt, "auto"))
        _DETENCION_MEM["guardada"] = True
    except Exception as exc:  # noqa: BLE001
        log.error("compra automática: la detención no se pudo guardar en kubera (%s); queda en "
                  "memoria, se marca en las filas de la bitácora y se reintenta cada vuelta",
                  str(exc)[:150])
        try:
            n = await asyncio.shield(asyncio.to_thread(_marcar_filas, pos, txt))
            _DETENCION_MEM["marcadas"] = n
        except Exception as exc2:  # noqa: BLE001
            log.error("compra automática: tampoco se pudo marcar la detención en la bitácora "
                      "(%s): sólo la sostiene la memoria de este proceso", str(exc2)[:150])
    await _campana("temu_compra_auto_detenida", "detenida",
                   f"Compra automática de guías de Temu DETENIDA — {txt}. No compra nada "
                   "hasta que alguien concilie (Automatización → Temu → Conciliar).",
                   texto_ok="Compra automática de guías de Temu liberada: vuelve a comprar.",
                   recordatorio_h=4)
    return txt


async def _reintentar_detencion() -> None:
    """Si la bandera de la detención no se pudo escribir, se reintenta. Nunca
    lanza."""
    if not _DETENCION_MEM or _DETENCION_MEM.get("guardada"):
        return
    try:
        await asyncio.to_thread(_escribir_detencion, True, str(_DETENCION_MEM.get("motivo") or ""),
                                "auto")
        _DETENCION_MEM["guardada"] = True
        log.warning("compra automática: la detención ya quedó guardada en kubera")
    except Exception as exc:  # noqa: BLE001
        log.error("compra automática: la detención sigue sin guardarse en kubera (%s)",
                  str(exc)[:150])


async def conciliar_y_liberar(po: str | None, *, liberar: bool = False,
                              quien: str = "", reintentar: bool = False) -> dict[str, Any]:
    """
    El botón "Conciliar" del panel (admin). Concilia la compra de `po` mirando
    Temu (`temu_guias_compra.conciliar`) y, si ya NO queda ninguna compra que
    bloquee, LIBERA el job. Sólo escribe en la bitácora y en la bandera; nunca
    compra. Nunca lanza (salvo cancelación).

    Lo que NO hace (revisión del 30-sep):
      · no libera si la conciliación falló o no se pudo anotar;
      · nunca reescribe el estado que tenía la fila: lo que queda es lo que
        Temu dijo AHORA (una etiqueta que resultó FALLIDA queda 'fallida' y
        bloquea);
      · da por "revisada a mano" SÓLO una compra que ya estaba 'comprada' y
        sigue así (su orden no quedó bien y una persona la miró): una
        'pendiente' sigue conciliándose sola;
      · una fila 'rechazada' de la compra automática se mira en Temu antes de
        creerle al código (si Temu sí compró, bloquea);
      · una etiqueta automática 'pendiente' bloquea la liberación, salvo que
        una persona la ACEPTE con `liberar=True` (ya la miró en el seller
        center): entonces se sigue conciliando sola, sin detener por edad.
    Con `liberar=True` y `quien`, además, una compra abierta sin rastro en
    Temu pasados 30 min se da por no hecha (`temu_guias_compra.conciliar`).

    `reintentar=True` (botón «Reintentar» de una venta que Temu RECHAZÓ): se
    mira Temu —detalle, unshipped y label.list— y, si de verdad no hay guía, la
    fila queda marcada `auto:reintentar` con quién lo pidió: la vuelta
    siguiente la vuelve a comprar (una vez; si Temu la rechaza otra vez, vuelve
    a "compra manual"). Sin `quien` no se marca. «Reintentar» NO libera una
    compra automática detenida por otra cosa.
    """
    global _DETENCION_MEM
    sn = str(po or "").strip()
    quien = str(quien or "").strip()[:120]
    out: dict[str, Any] = {"ok": False, "parent_order_sn": sn or None, "liberado": False}
    try:
        if sn:
            previa = (await asyncio.to_thread(tgc._reclamos_de, [sn])).get(sn)  # noqa: SLF001
            revisar = bool(previa and es_auto(previa) and previa.get("estado") == "rechazada")
            c = await tgc.conciliar(sn, liberar=liberar, quien=quien, revisar_rechazada=revisar)
            out["conciliacion"] = c
            if not c.get("ok") and c.get("accion") != "sin_registro":
                return {**out, "motivo": (f"no se pudo conciliar {sn} ("
                                          f"{c.get('motivo') or c.get('accion')}): no se libera")}
            if c.get("accion") == "conciliada" and c.get("anotada") is False:
                return {**out, "motivo": (f"Temu dice '{c.get('estado')}' de {sn} pero la "
                                          "bitácora no se pudo anotar: no se libera")}
            fresca = ((await asyncio.to_thread(tgc._reclamos_de, [sn])).get(sn)  # noqa: SLF001
                      if previa else None)
            if previa and fresca and es_auto(previa):
                antes, ahora_e = previa.get("estado"), fresca.get("estado")
                if antes == "comprada" and ahora_e == "comprada" and not ya_verificada(previa):
                    # YA estaba comprada y su orden no quedó verificada (por eso
                    # se detuvo): una persona la revisó, así que deja de
                    # re-verificarse sola — si no, una orden arreglada a mano
                    # volvería a detener el job en cada vuelta. (Una que ESTE
                    # conciliar acaba de resolver como comprada NO se marca: su
                    # orden se verifica la vuelta siguiente, como cualquier
                    # compra.)
                    await tgc._anotar(fresca["reclamo"], {sn: {  # noqa: SLF001
                        "estado": "comprada",
                        "motivo": f"{VERIF_NO} · revisada a mano por {quien or '?'} al conciliar"}})
                elif antes == "pendiente" and ahora_e == "pendiente" and liberar and quien:
                    await tgc._anotar(fresca["reclamo"], {sn: {  # noqa: SLF001
                        "estado": "pendiente",
                        "motivo": (f"{PEND_ACEPTADA} · aceptada por {quien}: sigue en "
                                   "aplicación en Temu; se sigue conciliando sola")}})
                out["estado_final"] = ahora_e
            if reintentar:
                if not (previa and es_auto(previa) and previa.get("estado") == "rechazada"):
                    out["reintento"] = False
                    out["reintento_motivo"] = f"{sn} no es una compra automática rechazada por Temu"
                elif not (fresca and fresca.get("estado") == "rechazada"
                          and c.get("accion") == "nada_que_conciliar" and c.get("mirado_en_temu")):
                    out["reintento"] = False
                    out["reintento_motivo"] = (f"al mirar Temu, {sn} ya no está simplemente rechazada "
                                               f"({(fresca or {}).get('estado') or c.get('accion')}): "
                                               "no se reintenta")
                elif not quien:
                    out["reintento"] = False
                    out["reintento_motivo"] = "reintentar exige saber quién (sesión)"
                else:
                    anotada = await tgc._anotar(fresca["reclamo"], {sn: {  # noqa: SLF001
                        "estado": "rechazada",
                        "motivo": (f"{MARCA_REINTENTAR} · pedido por {quien}: Temu no muestra guía de "
                                   f"esta venta; antes la rechazó con {previa.get('codigo') or '?'} "
                                   f"({str(previa.get('motivo') or '')[:120]})")}})
                    out["reintento"] = bool(anotada)
                    if anotada:
                        _MANUALES.pop(sn, None)      # sin reposo: candidata en la vuelta siguiente
                        out["reintento_motivo"] = (f"{sn} se vuelve a comprar en la vuelta siguiente "
                                                   "(una vez)")
                    else:
                        out["reintento_motivo"] = "no se pudo anotar el reintento en la bitácora"
                # «Reintentar» NO libera la compra automática: si está detenida
                # por OTRA cosa (dinero en duda), eso se concilia con su botón.
                return {**out, "ok": bool(out.get("reintento")), "motivo": out.get("reintento_motivo")}
        bloq = await asyncio.to_thread(_bloqueantes)
        det = await asyncio.to_thread(_leer_detencion)
        marcadas = [f for f in await asyncio.to_thread(tgc.compras_recientes, _dias_bitacora(), 2000)
                    if es_auto(f) and str(f.get("motivo") or "").startswith(MARCA_DETENIDA)]
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {**out, "motivo": tgc._motivo_bitacora(exc)}  # noqa: SLF001
    out["ok"] = True
    out["abiertas"] = [{"parent_order_sn": a.get("parent_order_sn"), "estado": a.get("estado")}
                       for a in bloq]
    if bloq:
        lista = ", ".join(str(a.get("parent_order_sn")) + " " + str(a.get("estado"))
                          for a in bloq[:5])
        return {**out, "motivo": (f"siguen {len(bloq)} compra(s) abierta(s) o en aplicación "
                                  f"({lista}): concilia cada una antes de liberar (una etiqueta "
                                  "'pendiente' se acepta con «liberar» si ya la revisaste en el "
                                  "seller center)")}
    if not (det or _DETENCION_MEM or marcadas):
        return {**out, "motivo": "la compra automática no estaba detenida"}
    if not quien:
        return {**out, "ok": False, "motivo": "liberar exige saber quién (sesión)"}
    try:
        await asyncio.to_thread(_escribir_detencion, False,
                                f"liberada por {quien} al conciliar {sn or '—'}", quien)
        if marcadas:
            await asyncio.to_thread(_desmarcar_filas, quien)
    except Exception as exc:  # noqa: BLE001
        return {**out, "ok": False, "motivo": f"no se pudo liberar en kubera ({str(exc)[:150]})"}
    _DETENCION_MEM = None
    log.warning("Compra automática de guías de Temu LIBERADA por %s (conciliando %s)",
                quien, sn or "—")
    await _campana("temu_compra_auto_detenida", "ok", "",
                   texto_ok=f"Compra automática de guías de Temu liberada por {quien}.")
    return {**out, "liberado": True}


# ═════════════════════════════════════════════════════════════════════════════
#  5 · LA VERIFICACIÓN (refresco inmediato + relectura de Odoo)
# ═════════════════════════════════════════════════════════════════════════════

async def _verificar(pos: list[str], esperado: dict[str, dict[int, set[str]]],
                     esperas: Iterable[float] | None = None,
                     piezas: dict[str, dict[int, dict[str, int] | None]] | None = None
                     ) -> dict[str, dict[str, Any]]:
    """Dispara el refresco de guías de ESAS ventas y relee Odoo; repite con las
    `esperas` mientras falte algo. {PO: veredicto de `verificar_contra_odoo`}."""
    from services import odoo_ventas, pedidos_temu
    res: dict[str, dict[str, Any]] = {}
    for espera in (esperas if esperas is not None else _ESPERAS_VERIFICACION):
        if espera:
            await asyncio.sleep(espera)
        try:
            ref = await pedidos_temu.refrescar_guias(solo_ids=list(pos), segundos_max=180)
            if ref.get("error"):
                log.warning("compra automática: el refresco de %s dijo: %s", ", ".join(pos),
                            str(ref["error"])[:150])
        except Exception as exc:  # noqa: BLE001
            log.warning("compra automática: el refresco de %s falló: %s", ", ".join(pos),
                        str(exc)[:150])
        res = {}
        for po in pos:
            try:
                ordenes = await asyncio.to_thread(odoo_ventas.leer_venta_odoo, CANAL, po)
                res[po] = verificar_contra_odoo(esperado.get(po, {}), ordenes,
                                                (piezas or {}).get(po))
            except Exception as exc:  # noqa: BLE001
                res[po] = {"estado": "pendiente", "ordenes": [],
                           "motivos": [f"Odoo no contestó la relectura ({str(exc)[:120]})"]}
        if (any(v["estado"] == "contradiccion" for v in res.values())
                or all(v["estado"] == "ok" for v in res.values())):
            break
    return res


def _veredicto(res: dict[str, dict[str, Any]]) -> str:
    if any(v["estado"] == "contradiccion" for v in res.values()):
        return "contradiccion"
    return "ok" if res and all(v["estado"] == "ok" for v in res.values()) else "pendiente"


async def _anotar_verificacion(reclamo: str, estado_fila: str,
                               res: dict[str, dict[str, Any]]) -> None:
    await tgc._anotar(reclamo, {po: {"estado": estado_fila,  # noqa: SLF001
                                     "motivo": texto_verificacion(v)}
                                for po, v in res.items()})


async def _completar_guias(guias: dict[str, str], psns: Iterable[str],
                           pos: list[str]) -> dict[str, str]:
    """Las guías que `shipment.result.get` no trajo (`trackingNumber` está "por
    verificar" en vivo) se buscan por packageSn en `unshipped.package.get`, la
    fuente del refresco. Sin guía esperada la verificación sólo puede mirar el
    almacén: se DICE en el log. Nunca lanza."""
    salida = {k: v for k, v in guias.items() if v}
    faltan = sorted({str(p) for p in psns if p and not salida.get(str(p))})
    if faltan and pos:
        try:
            extra = await tgc.guias_por_paquete(pos)
            for p in faltan:
                if extra.get(p):
                    salida[p] = extra[p]
        except Exception as exc:  # noqa: BLE001
            log.warning("compra automática: unshipped.package.get de %s falló: %s",
                        ", ".join(pos), str(exc)[:120])
    sin = [p for p in faltan if not salida.get(p)]
    if sin:
        log.warning("compra automática: no se sabe la guía de %s (%s): la verificación de su "
                    "orden sólo compara almacén y piezas", ", ".join(sin), ", ".join(pos))
    return salida


async def _verificar_compra(compra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Justo después de `comprar()` = 'comprada': la orden tiene que nacer YA
    con la guía de SU caja en cada entrega, sus piezas y el PDF."""
    guias: dict[str, str] = {}
    for d in compra.get("llamadas") or []:
        for x in d.get("resultado") or []:
            if x.get("packageSn"):
                guias[str(x["packageSn"])] = str(x.get("trackingNumber") or "")
    pos = [str(p) for p in compra.get("ventas") or []]
    guias = await _completar_guias(guias, list(guias), pos)
    esperado: dict[str, dict[int, set[str]]] = {}
    piezas: dict[str, dict[int, dict[str, int] | None]] = {}
    for d in compra.get("llamadas") or []:
        for po, entradas in (d.get("reparto_real") or {}).items():
            e = esperado_por_venta(entradas or [], guias)
            for a, gs in e.items():
                esperado.setdefault(str(po), {}).setdefault(a, set()).update(gs)
            piezas.setdefault(str(po), {}).update(piezas_por_almacen(entradas or []))
    await _anotar_verificacion(compra["reclamo"], "comprada",
                               {po: {"estado": "pendiente", "motivos": [
                                   "compra automática: la orden de Odoo se verifica ahora"]}
                                for po in pos})
    res = await _verificar(pos, esperado, piezas=piezas)
    await _anotar_verificacion(compra["reclamo"], "comprada", res)
    return _veredicto(res), res


async def _guias_de(psns: list[str], pos: list[str]) -> dict[str, str]:
    """{packageSn: guía} de `shipment.result.get`, completado con
    `unshipped.package.get`. {} si Temu no contesta."""
    guias: dict[str, str] = {}
    if psns:
        try:
            filas = await tgc._resultados_de(tgc._Sesion(6, 60), psns)  # noqa: SLF001
            guias = {k: str(v.get("trackingNumber") or "") for k, v in filas.items()}
        except Exception as exc:  # noqa: BLE001
            log.warning("compra automática: result.get de %s falló: %s", ", ".join(psns),
                        str(exc)[:120])
    return await _completar_guias(guias, psns, pos)


def _edad_min(fila: dict[str, Any], clave: str | None = None) -> float:
    cuando = _fecha(fila.get(clave)) if clave else reclamado_at(fila)
    if cuando is None:
        return 0.0
    return max(0.0, (_ahora() - cuando).total_seconds() / 60.0)


async def _revisar_pendientes(filas: list[dict[str, Any]], r: dict[str, Any]) -> str:
    """
    Las compras automáticas ANTERIORES que todavía no quedaron verificadas:
    se concilian las que Temu tenía en aplicación y se re-verifican en Odoo.
    'ok' (se puede comprar), 'esperando' (todavía no; no se compra otra) o
    'detenido'.
    """
    pend = [f for f in filas if es_auto(f) and f.get("estado") in tgc.ESTADOS_HECHOS
            and not ya_verificada(f)]
    if not pend:
        return "ok"
    tope = _verificar_max_min()
    r["por_verificar"] = sorted(f["parent_order_sn"] for f in pend)
    esperando = False
    for f in [x for x in pend if x.get("estado") == "pendiente"]:
        po = f["parent_order_sn"]
        aceptada = str(f.get("motivo") or "").startswith(PEND_ACEPTADA)
        c = await tgc.conciliar(po)
        if c.get("estado") == "fallida":
            txt = await _detener(f"la etiqueta de {po} FALLÓ en Temu (hay que rehacerla a mano)",
                                 [po], "fallida")
            r.update(estado="detenido", motivo=txt)
            return "detenido"
        if c.get("estado") == "comprada":
            esperando = True          # su orden se verifica la vuelta siguiente
            continue
        if aceptada:
            # Una persona la aceptó: se sigue mirando (si falla, detiene), pero
            # su edad ya no detiene ni frena la compra de las demás. La
            # conciliación reescribe el motivo: se le devuelve su marca.
            if c.get("accion") == "conciliada":
                await tgc._anotar(f["reclamo"], {po: {  # noqa: SLF001
                    "estado": "pendiente", "motivo": str(f.get("motivo"))[:300]}})
            continue
        if _edad_min(f) > tope:
            txt = await _detener(f"la etiqueta de {po} sigue en aplicación en Temu tras "
                                 f"{_edad_min(f):.0f} min", [po], "pendiente")
            r.update(estado="detenido", motivo=txt)
            return "detenido"
        esperando = True
    por_reclamo: dict[str, list[dict[str, Any]]] = {}
    for f in pend:
        if f.get("estado") == "comprada":
            por_reclamo.setdefault(str(f.get("reclamo")), []).append(f)
    for reclamo, fs in por_reclamo.items():
        pos = [f["parent_order_sn"] for f in fs]
        guias = await _guias_de(sorted({p for f in fs for p in (f.get("package_sn") or [])}), pos)
        esperado = {f["parent_order_sn"]: esperado_por_venta(_lista(f.get("reparto_real")), guias)
                    for f in fs}
        piezas = {f["parent_order_sn"]: piezas_por_almacen(_lista(f.get("reparto_real")))
                  for f in fs}
        res = await _verificar(pos, esperado, esperas=(0.0,), piezas=piezas)
        await _anotar_verificacion(reclamo, "comprada", res)
        v = _veredicto(res)
        if v == "contradiccion":
            txt = await _detener(
                "la orden de Odoo NO quedó con los datos de su guía: "
                + "; ".join(m for x in res.values() for m in x["motivos"])[:180], pos, "verificacion")
            r.update(estado="detenido", motivo=txt)
            return "detenido"
        if v != "ok":
            edad = max(_edad_min(f) for f in fs)
            if edad > tope:
                txt = await _detener(
                    f"la orden de Odoo no quedó confirmada con su guía y su PDF en {tope} min "
                    f"({'; '.join(m for x in res.values() for m in x['motivos'])[:140]})",
                    pos, "verificacion_tardia")
                r.update(estado="detenido", motivo=txt)
                return "detenido"
            esperando = True
    return "esperando" if esperando else "ok"


async def _mirar_rechazadas(filas: list[dict[str, Any]], r: dict[str, Any]) -> bool:
    """
    UNA MIRADA A TEMU de cada venta que Temu RECHAZÓ hace poco. Desde el 1-oct
    un rechazo documentado ya no detiene el job, así que nadie pulsa «Conciliar»
    y nadie miraba Temu: si el código estuviera mal clasificado y Temu SÍ
    compró, la fila se quedaba 'rechazada' para siempre — su gasto fuera de los
    topes y su orden de Odoo creada por el refresco de las 2 h con un reparto
    recalculado, sin verificar. `tgc.conciliar(revisar_rechazada=True)` mira el
    detalle, unshipped y label.list (2-3 lecturas; nunca compra). UNA mirada por
    rechazo, entre `RECHAZO_MIRAR_MIN` y `RECHAZO_MIRAR_MAX_MIN` minutos después:
      · paquete en Temu declarado como NUESTRA llamada (o sin medidas con qué
        compararlo) → la fila pasa a 'comprada' / 'pendiente' con su reparto
        real, suena la campana y esta vuelta termina (la siguiente verifica su
        orden);
      · paquete declarado DISTINTO (otro peso, otra caja, otro almacén) → lo
        compró una persona después del rechazo, que es lo que se espera de una
        venta en "compra manual": la fila no se toca;
      · etiqueta sin paquete, o fallida → queda abierta y el job SE DETIENE;
      · nada → sigue 'rechazada' y no se vuelve a mirar.
    Si Temu no contesta, se mira la vuelta siguiente. True = la vuelta termina
    aquí. Nunca lanza (salvo cancelación).
    """
    global _RECH_MIRADAS
    _RECH_MIRADAS &= {f"{f.get('parent_order_sn')}|{f.get('reclamo')}" for f in filas}
    pend: list[tuple[float, str, dict[str, Any]]] = []
    for f in filas:
        if not (es_auto(f) and f.get("estado") == "rechazada" and f.get("codigo")):
            continue
        if str(f.get("motivo") or "").startswith(("liberada a mano", MARCA_REINTENTAR,
                                                    MARCA_DETENIDA)):
            continue                  # ya la miró una persona, o el job la marcó al detenerse
        clave = f"{f.get('parent_order_sn')}|{f.get('reclamo')}"
        edad = _edad_min(f, "actualizado_at")
        if clave in _RECH_MIRADAS or not (RECHAZO_MIRAR_MIN <= edad <= RECHAZO_MIRAR_MAX_MIN):
            continue
        pend.append((edad, clave, f))
    for edad, clave, f in sorted(pend, key=lambda x: -x[0])[:RECHAZO_MIRAR_POR_VUELTA]:
        po = str(f["parent_order_sn"])
        c = await tgc.conciliar(po, revisar_rechazada=True, payload_propio=f.get("payload") or {})
        if c.get("accion") == "conciliada":
            _RECH_MIRADAS.add(clave)
            estado = str(c.get("estado") or "?")
            cod = f.get("codigo")
            r.setdefault("rechazadas_con_guia", []).append({"venta": po, "estado": estado})
            log.error("Compra automática de guías de Temu: %s se había dado por RECHAZADA (%s) y "
                      "Temu SÍ muestra su guía: la fila queda '%s'", po, cod, estado)
            if estado in tgc.ESTADOS_ABIERTOS or c.get("anotada") is False:
                txt = await _detener(
                    f"{po} se había dado por rechazada ({cod}) y Temu muestra su etiqueta "
                    f"('{estado}'): no se sabe qué compró", [po], estado)
                r.update(estado="detenido", motivo=txt)
            else:
                r.update(estado="verificando", motivo=(
                    f"{po} se había dado por RECHAZADA ({cod}) y Temu SÍ compró su guía ('{estado}'): "
                    "la fila ya lo dice y su orden de Odoo se verifica la vuelta siguiente (no se "
                    "compra otra hasta entonces)"))
                await _campana("temu_compra_auto_rechazo_falso",
                               "po:" + hashlib.sha1(po.encode("utf-8")).hexdigest()[:10],
                               (f"Compra automática de guías de Temu: {po} se dio por RECHAZADA "
                                f"({cod}) y {edad:.0f} min después Temu muestra su guía, declarada "
                                "como la nuestra: o ese código NO es un «no compró», o alguien la "
                                "compró a mano con las mismas medidas. La bitácora ya la tiene "
                                f"'{estado}' con el reparto real de Temu y su orden de Odoo se "
                                "verifica sola; si no la compró nadie a mano, avisa del código."),
                               recordatorio_h=24)
            return True
        if c.get("ok") and c.get("mirado_en_temu") and c.get("accion") in ("nada_que_conciliar",
                                                                             "guia_ajena"):
            # Mirada limpia (Temu no tiene nada), o la guía la compró una
            # persona después (declarada distinto): la fila se queda como está.
            _RECH_MIRADAS.add(clave)
        # Temu no contestó: se mira la vuelta siguiente.
    return False


# ═════════════════════════════════════════════════════════════════════════════
#  6 · LA VUELTA
# ═════════════════════════════════════════════════════════════════════════════

def _en_reposo(po: str) -> bool:
    """¿Esta venta ya se evaluó como "manual" hace poco? (no dispara un plan)."""
    m = _MANUALES.get(po)
    if not m:
        return False
    if m["clase"] in CLASES_PERMANENTES:
        return True
    if m["clase"] in CLASES_SIN_REPOSO:
        return False
    espera = 30 * 60 if m["clase"] == "transitorio" else _revisar_min() * 60
    return time.monotonic() - float(m.get("ts") or 0.0) < espera


def _marcar_manual(ev: dict[str, Any], *, renovar: bool = True,
                   solo: Iterable[str] | None = None) -> None:
    """Anota (o actualiza) "requiere compra manual". `renovar=False` conserva
    el reposo que ya tenía (una vuelta que NO la volvió a cotizar no se lo
    reinicia). `ev["por_venta"]` pisa clase y motivo de una venta en
    particular. `solo` = sólo esas ventas del grupo."""
    ahora = _iso(_ahora())
    por_venta = ev.get("por_venta") or {}
    for po in (list(solo) if solo is not None else ev.get("ventas") or []):
        previo = _MANUALES.get(po) or {}
        propio = por_venta.get(po) or {}
        clase = propio.get("clase") or ev.get("clase") or "otro"
        ts = time.monotonic() if (renovar or not previo) else float(previo.get("ts") or 0.0)
        desde = previo.get("desde") or ahora
        if clase == "transitorio":
            # La racha transitoria cuenta desde su PRIMER fallo, aunque entre
            # vuelta y vuelta el plan la haya dado por comprable.
            desde = _TRANSITORIO_DESDE.setdefault(
                po, (previo.get("desde") if previo.get("clase") == "transitorio" else None) or ahora)
        else:
            _TRANSITORIO_DESDE.pop(po, None)
        _MANUALES[po] = {"motivo": str(propio.get("motivo") or ev.get("motivo") or "")[:500],
                         "clase": clase,
                         "urgente": bool(propio.get("urgente", ev.get("urgente"))), "visto": ahora,
                         "desde": desde, "ts": ts}


def _anotar_evaluacion(g: dict[str, Any], ev: dict[str, Any], candidatas: set[str]) -> None:
    """
    Lo que una vuelta con plan aprendió de UN grupo, SIN mentir sobre lo que no
    miró (revisión del 30-sep): un grupo que no tiene ninguna candidata NO se
    cotizó, así que su "no se cotizó (fuera del grupo pedido)" no es un motivo
    y no le renueva el reposo. Sólo se toca si lo que se supo NO depende de
    cotizar (una urgencia nueva, el corte, ya tener guía…) o si no había nada
    anotado.
    """
    if ev["decision"] != "manual":
        for po in ev["ventas"]:
            _MANUALES.pop(po, None)
        return
    cotizado = any(po in candidatas for po in g.get("ventas") or [])
    if cotizado:
        _marcar_manual(ev)
        return
    for po in ev["ventas"]:
        previo = _MANUALES.get(po)
        if previo:
            if ev.get("urgente") and not previo.get("urgente"):
                previo["urgente"] = True       # una urgencia nueva SÍ se dice...
            continue                           # ...pero su motivo y su reposo, intactos
        if _NO_COTIZADO in str(ev.get("motivo") or ""):
            continue                           # no se miró: nada que decir
        _marcar_manual(ev, solo=[po])


def _minutos_desde(m: dict[str, Any]) -> float:
    d = _fecha(m.get("desde"))
    return max(0.0, (_ahora() - d).total_seconds() / 60.0) if d else 0.0


def atascada_con_guia(m: dict[str, Any]) -> bool:
    """¿"Ya tiene guía" desde hace `YA_COMPRADA_AVISA_MIN` y sigue esperando? Su
    etiqueta está en Temu pero su orden no nace. PURA (salvo el reloj)."""
    return m["clase"] == "ya_comprada" and _minutos_desde(m) >= YA_COMPRADA_AVISA_MIN


def visibles_en_campana(manuales: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Las "requiere compra manual" que tienen que SONAR: todas menos las que no
    piden nada a nadie (ya tiene guía, ensayo) y las transitorias recientes —
    una transitoria que lleva más de `TRANSITORIO_AVISA_MIN` sin resolverse
    suena también (se reintenta sola, pero no se está arreglando), y una
    VENCIDA desde la segunda vuelta que falla (`TRANSITORIO_URGENTE_AVISA_MIN`).
    "Ya tiene guía" suena si lleva `YA_COMPRADA_AVISA_MIN` en la cola: su orden
    no nace. PURA (salvo el reloj)."""
    def _suena(m: dict[str, Any]) -> bool:
        if m["clase"] not in CLASES_FUERA_DE_CAMPANA:
            return True
        if m["clase"] == "transitorio":
            return _minutos_desde(m) >= (TRANSITORIO_URGENTE_AVISA_MIN if m.get("urgente")
                                         else TRANSITORIO_AVISA_MIN)
        return atascada_con_guia(m)
    return {po: m for po, m in manuales.items() if _suena(m)}


async def _avisar_resumen() -> None:
    """La campana EN RESUMEN: una por cambio de estado, no una por venta."""
    global _URG_ESTADO, _URG_AVISADAS, _RECH_ESTADO, _RECH_AVISADAS
    # Las que Temu RECHAZÓ: su propia campana (una rechazada NUEVA la hace
    # hablar aunque ya hubiera otras ventas en "compra manual").
    rech = {po: m for po, m in _MANUALES.items() if m["clase"] in ("rechazo", "rechazo_final")}
    estado_r = estado_urgentes(rech, _RECH_AVISADAS, _RECH_ESTADO, prefijo="rechazo")
    if estado_r != _RECH_ESTADO:
        _RECH_AVISADAS = set(rech)
    _RECH_ESTADO = estado_r
    await _campana("temu_compra_auto_rechazo", estado_r,
                   (f"Temu RECHAZÓ la compra automática de {len(rech)} guía(s): "
                    + "; ".join(f"{po} — {str(m.get('motivo') or '')[:110]}"
                                for po, m in sorted(rech.items())[:6])
                    + (" …" if len(rech) > 6 else "")
                    + ". Quedan para COMPRA MANUAL (o «Reintentar» en Automatización → Temu); la "
                      "compra automática SIGUE con las demás."),
                   texto_ok="Ya no quedan guías de Temu rechazadas esperando compra manual.",
                   nivel="🟠", recordatorio_h=6)
    visibles = visibles_en_campana(_MANUALES)
    urgentes = sorted(po for po, m in visibles.items() if m["urgente"])
    por_clase: dict[str, int] = {}
    for m in visibles.values():
        k = "ya_comprada_atascada" if m["clase"] == "ya_comprada" else m["clase"]
        por_clase[k] = por_clase.get(k, 0) + 1
    detalle = ", ".join(f"{ETIQUETA_CLASE.get(k, k)}: {n}" for k, n in
                        sorted(por_clase.items(), key=lambda kv: -kv[1]))
    await _campana("temu_compra_auto_manual", "manual" if visibles else "ok",
                   (f"Compra automática de guías de Temu: {len(visibles)} venta(s) nueva(s) "
                    f"requieren compra MANUAL de guía ({detalle}). Ver Automatización → Temu."),
                   texto_ok="Ya no quedan ventas nuevas de Temu esperando compra manual de guía.",
                   nivel="🟠", recordatorio_h=6)
    # Una urgente NUEVA cambia el estado y la campana habla aunque ya hubiera
    # otras urgentes (antes se quedaba callada hasta el recordatorio de 2 h).
    estado_u = estado_urgentes(urgentes, _URG_AVISADAS, _URG_ESTADO)
    if estado_u != _URG_ESTADO:
        _URG_AVISADAS = set(urgentes)
    _URG_ESTADO = estado_u
    await _campana("temu_compra_auto_urgente", estado_u,
                   (f"{len(urgentes)} venta(s) de Temu con el límite de envío VENCIDO que la "
                    "compra automática NO puede comprar (el motivo de cada una sale en su "
                    f"renglón): compra su guía A MANO HOY — {', '.join(urgentes[:12])}"
                    + (" …" if len(urgentes) > 12 else "")),
                   texto_ok="Ya no hay ventas de Temu con compra de guía urgente.",
                   recordatorio_h=2)


_TAREA: asyncio.Task | None = None


def lanzar_vuelta() -> dict[str, Any]:
    """Una vuelta YA, en segundo plano (el botón del panel). Se lanza como tarea
    y no dentro de la petición: una vuelta con compra tarda minutos y el proxy
    cortaría la petición a media compra. Su resultado sale en el panel
    (`ultima_vuelta`). Mismas banderas, topes y modo (ensayo / simple) que el
    job. Se llama dentro del loop (desde un endpoint async)."""
    global _TAREA
    if not auto_habilitada():
        return {"ok": False, "estado": "apagado",
                "motivo": "TEMU_COMPRA_GUIAS_ENABLED y TEMU_COMPRA_GUIAS_AUTO tienen que estar "
                          "encendidas"}
    if _VUELTA_LOCK.locked() or (_TAREA is not None and not _TAREA.done()):
        return {"ok": False, "estado": "en_curso", "motivo": "ya hay una vuelta en curso"}
    _TAREA = asyncio.get_running_loop().create_task(vuelta())
    return {"ok": True, "estado": "lanzada",
            "motivo": ("vuelta lanzada" + (" (ENSAYO: no compra)" if ensayo() else "")
                       + ": su resultado sale en «Última vuelta» en unos minutos")}


async def vuelta(*, ahora: datetime | None = None) -> dict[str, Any]:
    """
    Una vuelta del job. Nunca lanza (salvo cancelación). Con las banderas
    apagadas sale ANTES de tocar nada: ni Temu, ni Odoo, ni kubera.
    """
    r: dict[str, Any] = {"ts": _iso(_ahora()), "estado": None, "motivo": None,
                         "candidatas": 0, "evaluadas": 0, "manuales": 0,
                         "compradas": [], "intentos": [], "ensayo": [], "rechazadas": [],
                         "hoy": None}
    if not auto_habilitada():
        return {**r, "estado": "apagado",
                "motivo": "TEMU_COMPRA_GUIAS_ENABLED y TEMU_COMPRA_GUIAS_AUTO tienen que estar "
                          "encendidas: no se llamó a nadie"}
    if _VUELTA_LOCK.locked():
        return {**r, "estado": "en_curso", "motivo": "ya hay una vuelta en curso"}
    async with _VUELTA_LOCK:
        try:
            await _vuelta(r, ahora)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("compra automática de guías: la vuelta falló")
            r.update(estado="error", motivo=str(exc)[:300])
            if r["intentos"]:
                # Pasó algo DESPUÉS de intentar comprar: no se sigue a ciegas.
                await _detener(f"la vuelta falló después de intentar comprar ({str(exc)[:120]})",
                               [po for x in r["intentos"] for po in x.get("ventas") or []],
                               "error")
        r["estado"] = r["estado"] or ("compro" if r["compradas"]
                                      else "ensayo" if r["ensayo"]
                                      else "rechazos" if r["rechazadas"] else "sin_compras")
        await _avisar_bloqueo(r)
        await _avisar_compras(r)
        _ULTIMA.clear()
        _ULTIMA.update(r)
        if r["compradas"] or r["estado"] in ("detenido", "error"):
            log.warning("Compra automática de guías de Temu: %s — %s", r["estado"],
                        r.get("motivo") or ", ".join(
                            po for c in r["compradas"] for po in c["ventas"]))
        else:
            log.info("Compra automática de guías de Temu: %s (%s candidatas, %s manuales%s)",
                     r["estado"], r["candidatas"], r["manuales"],
                     f", ENSAYO compraría {len(r['ensayo'])}" if r["ensayo"] else "")
        return r


async def _avisar_bloqueo(r: dict[str, Any]) -> None:
    """Una vuelta que no pudo comprar por algo del SISTEMA se reintenta sola la
    siguiente; si pasa `BLOQUEO_AVISA_VUELTAS` veces seguidas, la campana lo
    dice (y avisa cuando se destraba). Nunca lanza."""
    est = str(r.get("estado") or "")
    # Cuenta la racha por PERTENECER a los bloqueos, no por repetir el mismo
    # (revisión del 30-sep): fallos que se alternan (sin_plan, plan_incompleto,
    # sin_turno…) también son una compra que no corre. Y una vuelta a la que se
    # le acabó el tiempo sin comprar nada teniendo comprables, también.
    sin_tiempo = bool(r.get("presupuesto_agotado")) and not r.get("compradas")
    if est in ESTADOS_BLOQUEO or sin_tiempo:
        causa = est if est in ESTADOS_BLOQUEO else "presupuesto_agotado"
        _RACHA["n"] += 1
        _RACHA["estado"] = causa
        if _RACHA["n"] >= BLOQUEO_AVISA_VUELTAS:
            _RACHA["avisada"] = True
            motivo = str(r.get("motivo") or "")
            if not motivo and causa == "presupuesto_agotado":
                motivo = "se le acabó el tiempo de la vuelta antes de empezar una compra"
            # Un estado FIJO: que la causa cambie de vuelta en vuelta no la
            # vuelve a sonar cada 15 min (el recordatorio sí).
            await _campana("temu_compra_auto_bloqueada", "bloqueada",
                           (f"La compra automática de guías de Temu lleva {_RACHA['n']} vueltas "
                            f"seguidas sin poder comprar (la última: {causa}): {motivo[:200]}. "
                            "Se reintenta sola cada vuelta; si no se arregla, revisa la causa."),
                           texto_ok="La compra automática de guías de Temu volvió a correr.",
                           recordatorio_h=4)
        return
    if est in ("en_curso", "otro_proceso", "otra_compra_en_curso"):
        return
    if _RACHA["avisada"]:
        await _campana("temu_compra_auto_bloqueada", "ok", "",
                       texto_ok="La compra automática de guías de Temu volvió a correr.")
    _RACHA.update(estado=None, n=0, avisada=False)


def tipo_aviso_medida(texto: str) -> str:
    """De qué TIPO es un aviso de medidas de `temu_guias_compra`. PURA."""
    for clave, tipo in _TIPOS_AVISO_MEDIDA:
        if clave in str(texto):
            return tipo
    return "otro aviso de medidas"


async def _avisar_compras(r: dict[str, Any]) -> None:
    """Las campanas NO bloqueantes de lo que la vuelta compró. Nunca lanza.
      · `temu_compra_auto_no_jt`: guías que NO fueron por J&T, con el porqué
        (J&T es el repartidor principal: que no vaya por J&T se dice siempre);
      · `temu_compra_auto_medidas`: avisos de medidas —la caja no cabe en J&T,
        un lado de más de 60 cm, se declara menos de la mitad del volumen que a
        mano (J&T puede re-medir y cobrar la diferencia), flete y columna que
        no cuadran, fuentes que no coinciden en el peso—. Suena cuando aparece
        una combinación NUEVA de SKU y tipo de aviso, no en cada compra;
      · y, si la vuelta compró, se apaga la de los rechazos seguidos."""
    compradas = r.get("compradas") or []
    if not compradas:
        return
    if r.get("estado") != "rechazos_seguidos":       # la que cortó la vuelta no se apaga sola
        await _campana("temu_compra_auto_rechazos", "ok", "",
                       texto_ok=("La compra automática de guías de Temu volvió a comprar tras los "
                                 "rechazos."))
    sin_jt = [c for c in compradas if c.get("no_jt")]
    if sin_jt:
        pos = sorted(po for c in sin_jt for po in c.get("ventas") or [])
        await _campana("temu_compra_auto_no_jt",
                       "nojt:" + hashlib.sha1(",".join(pos).encode("utf-8")).hexdigest()[:10],
                       (f"Compra automática de guías de Temu: {len(sin_jt)} guía(s) NO fueron por J&T — "
                        + " | ".join(f"{', '.join(c.get('ventas') or [])} ({', '.join(c.get('paqueteria') or []) or '?'}): "
                                     f"{str((c.get('no_jt') or [''])[0])[:220]}" for c in sin_jt[:6])
                        + (" …" if len(sin_jt) > 6 else "")),
                       nivel="🟠", recordatorio_h=24)
    nuevos: dict[str, str] = {}
    for c in compradas:
        for a in c.get("avisos") or []:
            de = str((a or {}).get("de") or "") if isinstance(a, dict) else ""
            txt = str((a or {}).get("aviso") or "") if isinstance(a, dict) else str(a)
            # "JUGU-0089-PLA × 3, ACC-0696 × 1" → por SKU, sin las piezas.
            skus = ", ".join(sorted({x.split(" × ")[0].strip() for x in de.split(",") if x.strip()})) or "?"
            llave = f"{skus}|{tipo_aviso_medida(txt)}"
            if llave not in _AVISOS_MEDIDA_VISTOS:
                nuevos.setdefault(llave, txt)
    if not nuevos:
        return
    _AVISOS_MEDIDA_VISTOS.update(nuevos)
    await _campana("temu_compra_auto_medidas",
                   "aviso:" + hashlib.sha1(",".join(sorted(_AVISOS_MEDIDA_VISTOS)).encode("utf-8"))
                   .hexdigest()[:10],
                   ("Compra automática de guías de Temu: guías compradas CON AVISO de medidas (no "
                    "bloquea; revísalas si J&T ajusta el cobro) — "
                    + " | ".join(f"{k.split('|')[0]}: {k.split('|')[1]} ({v[:150]})"
                                 for k, v in list(sorted(nuevos.items()))[:8])
                    + (" …" if len(nuevos) > 8 else "")),
                   nivel="🟠", recordatorio_h=24)


async def _avisar_cola(truncada: bool, n: int) -> None:
    """La cola de espera pasa de `TEMU_GUIAS_COLA_MAX`: lo más nuevo ni se ve.
    Se avisa por cambio de estado. Nunca lanza."""
    global _COLA_AVISADA
    if truncada:
        _COLA_AVISADA = True
        await _campana("temu_compra_auto_cola", "truncada",
                       (f"La cola de ventas de Temu que esperan guía pasa de {n} "
                        "(TEMU_GUIAS_COLA_MAX): las más nuevas no se ven ni se compran hasta que "
                        "baje — súbelo o compra las viejas."),
                       texto_ok="La cola de guías de Temu ya cabe entera en el plan.",
                       recordatorio_h=6)
    elif _COLA_AVISADA:
        _COLA_AVISADA = False
        await _campana("temu_compra_auto_cola", "ok", "",
                       texto_ok="La cola de guías de Temu ya cabe entera en el plan.")


async def _vuelta(r: dict[str, Any], ahora: datetime | None) -> None:
    t = topes()
    r["topes"] = t
    r["modo"] = {"ensayo": ensayo(), "solo_simple": solo_simple()}
    try:
        desde = corte()
    except ValueError as exc:
        r.update(estado="config_invalida", motivo=str(exc))
        return
    r["desde"] = _iso(desde)
    if not paqueteria_jt_primero():
        r["paqueteria_aviso"] = (
            "TEMU_GUIAS_PAQUETERIA = «" + str(getattr(settings, "temu_guias_paqueteria", "") or "")
            + "» NO pone a J&T primero: desde el 1-oct el orden es PRIORIDAD y las guías saldrían "
              "por el primer renglón que tenga canal")
        log.warning("Compra automática de guías de Temu: %s", r["paqueteria_aviso"])

    # 1 · La cadena entera: sin ella la orden no nacería con su guía.
    falta = await asyncio.to_thread(_prerrequisitos)
    if falta:
        r.update(estado="cadena_incompleta",
                 motivo=f"no se compra: la orden de Odoo no nacería con su guía — {falta}")
        return

    # 2 · ¿Detenida? ¿Algo abierto? (sin bitácora no se compra)
    try:
        det = await asyncio.to_thread(_leer_detencion)
        filas = await asyncio.to_thread(tgc.compras_recientes, _dias_bitacora(), 2000)
        abiertas = await asyncio.to_thread(tgc.abiertas)
    except Exception as exc:  # noqa: BLE001
        r.update(estado="sin_bitacora", motivo=tgc._motivo_bitacora(exc))  # noqa: SLF001
        return
    await _reintentar_detencion()
    marcadas = [f for f in filas
                if es_auto(f) and str(f.get("motivo") or "").startswith(MARCA_DETENIDA)]
    if det or _DETENCION_MEM or marcadas:
        info = det or _DETENCION_MEM or {}
        mot = (info.get("motivo") or (str(marcadas[0].get("motivo") or "")
                                      .removeprefix(f"{MARCA_DETENIDA} · ")
                                      if marcadas else "?"))
        cuando = (info.get("actualizado_at") or info.get("desde")
                  or (marcadas[0].get("actualizado_at") if marcadas else None))
        r.update(estado="detenido",
                 motivo=f"detenida desde {_iso(cuando) or '?'}: {mot} — concilia para liberarla")
        return
    atascadas = [a for a in abiertas if a.get("estado") != "en_curso"
                 or _edad_min(a, "actualizado_at") >= EN_CURSO_ATASCADA_MIN]
    if atascadas:
        txt = await _detener(
            f"{len(atascadas)} compra(s) de guía abierta(s) sin conciliar ("
            + ", ".join(f"{a['parent_order_sn']} {a.get('estado')}" for a in atascadas[:5]) + ")",
            [a["parent_order_sn"] for a in atascadas], "abiertas")
        r.update(estado="detenido", motivo=txt)
        return
    if abiertas:
        r.update(estado="otra_compra_en_curso",
                 motivo=f"{abiertas[0]['parent_order_sn']} se está comprando ahora mismo")
        return

    # 2b · Las que Temu RECHAZÓ hace poco: una mirada a Temu antes de creerle al
    #      código. Si Temu sí compró, esta vuelta termina (o el job se detiene).
    if await _mirar_rechazadas(filas, r):
        return

    # 3 · Lo comprado antes tiene que haber quedado bien antes de comprar más.
    rev = await _revisar_pendientes(filas, r)
    if rev == "detenido":
        return
    if rev == "esperando":
        r.update(estado="verificando",
                 motivo=("una compra automática anterior todavía no queda confirmada en Odoo "
                         f"con su guía y su PDF ({', '.join(r.get('por_verificar') or [])}): "
                         "no se compra otra hasta verificarla"))
        return

    # 4 · La cola: ventas en espera desde el corte, sin compra. Sin candidatas
    #     NO se llama a Temu. Se lee ANTES de los topes: con el tope del día
    #     alcanzado, las nuevas tienen que quedar "requiere compra manual".
    from services import odoo_ventas
    try:
        cola = await asyncio.to_thread(tgc._cola_espera, odoo_ventas._dias_espera(),  # noqa: SLF001
                                       tgc._cola_max() + 1)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        r.update(estado="sin_cola", motivo=f"no se pudo leer la cola de espera ({str(exc)[:150]})")
        return
    truncada = len(cola) > tgc._cola_max()  # noqa: SLF001
    cola = cola[:tgc._cola_max()]  # noqa: SLF001
    r["cola"] = {"esperando": len(cola), "truncada": truncada}
    await _avisar_cola(truncada, tgc._cola_max())  # noqa: SLF001
    en_cola = {str(x["order_id"]) for x in cola}
    for po in [p for p in _MANUALES if p not in en_cola]:
        _MANUALES.pop(po, None)          # ya salió de la espera (comprada a mano, cancelada…)
    for memo in (_TRANSITORIO_DESDE, _LIMITES):
        for po in [p for p in memo if p not in en_cola]:
            memo.pop(po, None)
    _REINTENTO_PENDIENTE.intersection_update(en_cola)
    ocupadas = {str(f.get("parent_order_sn")) for f in filas
                if f.get("estado") not in tgc.ESTADOS_LIBRES}
    # Las que Temu ya RECHAZÓ en una compra automática (la bitácora lo recuerda
    # aunque el contenedor se reinicie). NO se reintentan en bucle: quedan
    # "compra manual · Temu la rechazó" y se vuelven a PLANEAR cada
    # `TEMU_COMPRA_GUIAS_REVISAR_MIN` sólo para ver si su plan cambió (entonces,
    # UN reintento) — o enseguida si alguien pulsó «Reintentar». La que ya gastó
    # su reintento (o se liberó a mano) no vuelve a ser candidata.
    rechazos = {str(f.get("parent_order_sn")): f for f in filas
                if es_auto(f) and f.get("estado") == "rechazada"}
    for po, f in rechazos.items():
        if po not in en_cola:
            continue
        previo = _MANUALES.get(po) or {}
        clase = "rechazo_final" if rechazo_agotado(f) else "rechazo"
        if reintento_pedido(f) or (po in _REINTENTO_PENDIENTE and clase == "rechazo"):
            # La pidió una persona, o su único reintento ya estaba listo y la
            # vuelta anterior no alcanzó a comprarlo: sin reposo, candidata YA
            # (antes dormía otra hora entera).
            _MANUALES.pop(po, None)
        elif not previo or (clase == "rechazo_final" and previo.get("clase") != clase):
            _marcar_manual({"ventas": [po], "clase": clase, "motivo": texto_rechazo(f)})
    nuevas: list[tuple[int, str]] = []
    for i, x in enumerate(cola):
        po = str(x["order_id"])
        creado = _fecha(x.get("creado_at"))
        if po in ocupadas or (po in rechazos and rechazo_agotado(rechazos[po])):
            continue
        if creado is None or creado < desde:
            # Kubera la registró ANTES del corte: su venta también lo es. Se
            # dice en el panel y en la campana (antes no se decía nada: la
            # venta ni se evaluaba), sin llamar a Temu.
            if (_MANUALES.get(po) or {}).get("clase") != "anterior_corte":
                _marcar_manual({"ventas": [po], "clase": "anterior_corte", "motivo": (
                    f"venta anterior al corte ({_corte_txt(desde)}, "
                    "TEMU_COMPRA_GUIAS_DESDE): su guía la compra una persona")})
            continue
        nuevas.append((i, po))
    candidatas = [i for i, po in nuevas if not _en_reposo(po)]

    # 5 · Los topes del día (de la bitácora durable).
    hoy = resumen_hoy(filas, dia_mx(ahora or _ahora()))
    r["hoy"] = hoy
    if t["vuelta"] <= 0:
        r.update(estado="tope_vuelta", motivo="TEMU_COMPRA_GUIAS_MAX_VUELTA = 0")
        return
    tope = None
    if hoy["compras"] >= t["dia"]:
        tope = ("tope_dia", f"ya van {hoy['compras']} compra(s) automática(s) hoy (tope "
                            f"{int(t['dia'])}, TEMU_COMPRA_GUIAS_MAX_DIA)")
    elif hoy["gasto_mxn"] >= t["gasto_dia"]:
        tope = ("tope_gasto", f"ya van MX${hoy['gasto_mxn']:.2f} hoy (tope "
                              f"MX${t['gasto_dia']:.2f}, TEMU_COMPRA_GUIAS_GASTO_MAX_DIA)")
    if tope:
        # Sin llamar a Temu: las nuevas que no tenían otro motivo quedan
        # "requiere compra manual · tope del día", para que quien compra a mano
        # sepa HOY que el job no las va a comprar (su límite de Temu corre).
        n = 0
        ya_ts = int((ahora or _ahora()).timestamp())
        for _i, po in nuevas:
            previo = _MANUALES.get(po)
            if previo and previo["clase"] not in ("transitorio", "tope"):
                continue
            # Vencida (según el límite que dio Temu en el último plan) → a la
            # campana de URGENTES: el job no la va a comprar hoy y su límite ya pasó.
            lim = _LIMITES.get(po)
            _marcar_manual({"ventas": [po], "clase": "tope",
                            "urgente": bool(lim and lim <= ya_ts)
                            or bool((previo or {}).get("urgente")),
                            "motivo": (f"tope del día alcanzado ({tope[1]}): cómprala a mano o "
                                       "espera a mañana")})
            n += 1
        r.update(estado=tope[0], motivo=tope[1] + (f" — {n} venta(s) nueva(s) quedan para "
                                                   "compra manual" if n else ""))
        await _avisar_resumen()
        return

    r["candidatas"] = len(candidatas)
    if not candidatas:
        r["estado"] = "sin_candidatas"
        # La campana también se entera de lo que SALIÓ de la espera (comprada a
        # mano, cancelada): su resumen no se queda con un número viejo.
        await _avisar_resumen()
        return
    # El plan de la compra automática lee la cola ENTERA (antes, 120: lo que
    # quedaba más allá nunca se compraba). Con la cola dentro del tope esto ya
    # no deja nada fuera; queda de red por si el tope del plan bajara.
    alcance = tgc.limite_max_auto()
    fuera = [str(cola[i]["order_id"]) for i in candidatas if i >= alcance]
    for po in fuera:
        _marcar_manual({"ventas": [po], "clase": "cola_larga", "motivo": (
            f"hay {alcance}+ ventas más viejas esperando guía: el plan no alcanza a leer "
            "ésta — cómprala a mano o compra las viejas")})
    dentro = [i for i in candidatas if i < alcance]
    if not dentro:
        r.update(estado="cola_larga", motivo=f"{len(fuera)} candidata(s) fuera del alcance del plan")
        await _avisar_resumen()
        return

    # 6 · El turno (una vuelta a la vez en todo el despliegue).
    token = uuid.uuid4().hex
    try:
        tomado = await asyncio.to_thread(_tomar_turno, token)
    except Exception as exc:  # noqa: BLE001
        r.update(estado="sin_turno", motivo=f"no se pudo tomar el turno ({str(exc)[:150]})")
        return
    if not tomado:
        r.update(estado="otro_proceso", motivo="otra instancia del backend está en su vuelta")
        return
    # La racha de rechazos que viene de vueltas anteriores (bitácora durable), y
    # las ventas cuyo reintento se reclamó y terminó sin que Temu las juzgara
    # ('no_enviada': pasarela, 4000004, revisión final): su reintento sigue
    # siendo EL reintento — si no, cada pasajero le regalaba otro.
    # (Con el reloj REAL: `reclamado_at` lo escribe `comprar()` con la hora real.)
    racha = racha_de_rechazos(filas)
    reintentadas = {str(f.get("parent_order_sn")) for f in filas
                    if es_auto(f) and f.get("estado") == "no_enviada"
                    and str(f.get("aprobado_por") or "").startswith(QUIEN_REINTENTO)}
    memo: dict[str, Any] = {"reintentos": None, "tocadas": set()}
    try:
        await _planear_y_comprar(r, min(alcance, dentro[-1] + 1 + 5),
                                 {str(cola[i]["order_id"]) for i in dentro}, desde, t, hoy,
                                 ahora, token, rechazos, recientes={po for _i, po in nuevas},
                                 racha=racha, reintentadas=reintentadas, memo=memo)
    finally:
        if memo["reintentos"] is not None:
            # Los reintentos que el plan dio por comprables y esta vuelta no tocó.
            _REINTENTO_PENDIENTE.clear()
            _REINTENTO_PENDIENTE.update(set(memo["reintentos"]) - set(memo["tocadas"]))
        try:
            await asyncio.to_thread(_soltar_turno, token)
        except Exception as exc:  # noqa: BLE001
            log.warning("compra automática: no se pudo soltar el turno (%s); vence solo en "
                        "%s min", str(exc)[:120], TURNO_MIN)
    await _avisar_resumen()


async def _planear_y_comprar(r: dict[str, Any], limite: int, candidatas: set[str],
                             desde: datetime, t: dict[str, float], hoy: dict[str, Any],
                             ahora: datetime | None, token: str,
                             rechazadas: dict[str, dict[str, Any]] | None = None, *,
                             recientes: set[str] | None = None,
                             racha: dict[str, Any] | None = None,
                             reintentadas: set[str] | None = None,
                             memo: dict[str, Any] | None = None) -> None:
    # EL TIEMPO DE LA VUELTA: con hasta `MAX_VUELTA` compras (10 en
    # producción) y cada una con su re-plan, su espera del resultado y la
    # verificación de su orden, sólo se EMPIEZAN compras mientras quede
    # `presupuesto_s()`; las demás, la vuelta siguiente (y así la aprobación
    # del plan, de 30 min, nunca vence a media vuelta).
    inicio = time.monotonic()
    presupuesto = presupuesto_s()
    plan = await tgc.plan_guias(limite=limite, usar_cache=False, ahora=ahora,
                                cotizar=candidatas, tope_llamadas=max(150, 3 * limite + 30),
                                segundos_max=max(270.0, 1.5 * limite))
    r["plan"] = {"ok": bool(plan.get("ok")), "grupos": (plan.get("resumen") or {}).get("grupos"),
                 "comprables": (plan.get("resumen") or {}).get("comprables"),
                 "tardes": (plan.get("resumen") or {}).get("tardes"),
                 "cajas_por_fuente": (plan.get("resumen") or {}).get("cajas_por_fuente"),
                 "llamadas_temu": plan.get("llamadas_temu"),
                 "detalles_reusados": plan.get("detalles_reusados")}
    if not plan.get("ok"):
        r.update(estado="sin_plan", motivo=str(plan.get("error") or "el plan no salió")[:300])
        return
    for g in plan.get("grupos") or []:
        for po, lim in (g.get("limites_ts") or {}).items():
            if tgc._entero(lim):  # noqa: SLF001
                _LIMITES[str(po)] = int(lim)
    falla = plan_incompleto(plan)
    if falla:
        r.update(estado="plan_incompleto", motivo=f"no se compra a ciegas: {falla}")
        return

    simple = solo_simple()
    ensayando = ensayo()
    evals = [(g, evaluar_grupo(g, desde, t, simple=simple, rechazadas=rechazadas or {},
                               recientes=recientes or set(), excede_jt=compra_excede_jt()))
             for g in plan.get("grupos") or []]
    memo = memo if memo is not None else {"reintentos": None, "tocadas": set()}
    memo["reintentos"] = {po for _g, ev in evals
                          if ev["decision"] == "comprar" and ev.get("reintento") == "auto"
                          for po in ev["ventas"]}
    reint = set(reintentadas or ())
    for g, ev in evals:
        _anotar_evaluacion(g, ev, candidatas)
    r["evaluadas"] = len(evals)
    r["manuales"] = sum(len(ev["ventas"]) for _g, ev in evals if ev["decision"] == "manual")

    compradas = 0
    # EL CORTACIRCUITOS: rechazos SEGUIDOS de Temu con el mismo código (una
    # compra que sale bien lo reinicia). Arranca con la racha que la bitácora
    # trae de las vueltas anteriores: con ella abierta, el PRIMER rechazo igual
    # de esta vuelta la corta — se prueba UNA sola venta.
    racha_cod: str | None = (racha or {}).get("codigo")
    racha_n = int((racha or {}).get("n") or 0)
    seguidos = int((racha or {}).get("total") or 0)      # de cualquier código
    previos = racha_n
    if racha_n >= RECHAZOS_SEGUIDOS_CORTE or seguidos >= RECHAZOS_SEGUIDOS_TOPE:
        r["racha_abierta"] = {"codigo": racha_cod, "rechazos": max(racha_n, seguidos)}
    for g, ev in evals:
        if ev["decision"] != "comprar":
            continue
        # Si NO se compra en esta vuelta (tope, rechazo, fallo pasajero…), una
        # venta que va TARDE queda "requiere compra manual" URGENTE: su límite
        # de Temu ya pasó (revisión del 30-sep: antes salía sin urgente).
        ev = {**ev, "urgente": bool(ev.get("urgente") or ev.get("tarde"))}
        pos = ev["ventas"]
        if compradas >= t["vuelta"]:
            r["esperan_turno"] = r.get("esperan_turno", 0) + 1
            continue
        if not ensayo() and time.monotonic() - inicio > presupuesto:
            # Se acabó el tiempo de la vuelta: no es "compra manual", es la
            # siguiente vuelta (sin reposo: siguen siendo candidatas).
            r["esperan_turno"] = r.get("esperan_turno", 0) + 1
            r["presupuesto_agotado"] = True
            continue
        memo["tocadas"].update(pos)                # de aquí en adelante, esta vuelta la atendió
        if hoy["compras"] + 1 > t["dia"]:
            _marcar_manual({**ev, "clase": "tope", "urgente": bool(ev.get("tarde")), "motivo": (
                f"tope diario de compras automáticas alcanzado ({int(t['dia'])}): cómprala a "
                "mano o espera a mañana")})
            continue
        if hoy["gasto_mxn"] + float(ev["costo"] or 0) > t["gasto_dia"]:
            _marcar_manual({**ev, "clase": "tope", "urgente": bool(ev.get("tarde")), "motivo": (
                f"con esta guía (MX${ev['costo']:.2f}) se pasaría el tope de gasto del día "
                f"(MX${t['gasto_dia']:.2f})")})
            continue
        ap = g["aprobacion"]
        paqs = sorted({c["paqueteria"] for c in ev["costos"]["cajas"] if c["paqueteria"]})
        if ensayando:
            # ENSAYO: todo lo de arriba corrió de verdad (plan, cotización,
            # topes); aquí NO se compra. Queda en el panel qué compraría, y la
            # venta descansa su reposo (no se re-planea cada 15 min).
            compradas += 1
            r["ensayo"].append({"ventas": pos, "costo_mxn": ev["costo"], "paqueteria": paqs,
                                "fecha_envio": ap.get("fecha_envio"), "horas": ap.get("horas"),
                                "ajustada": bool(ap.get("ajustada")),
                                "tarde": bool(ap.get("tarde")),
                                "medidas": tgc.texto_medidas(ap.get("medidas") or []),
                                "llamadas": [{"send_type": ll.get("send_type"),
                                              "cajas": len(ll.get("paquetes") or [])}
                                             for ll in g.get("llamadas") or []]})
            _marcar_manual({**ev, "clase": "ensayo", "motivo": f"ENSAYO: la compraría — {ev['motivo']}"})
            continue
        try:
            sigue = await asyncio.to_thread(_renovar_turno, token)
        except Exception:  # noqa: BLE001
            sigue = False
        if not sigue:
            r.update(estado="turno_perdido", motivo="el turno venció a media vuelta: no se compra más")
            return
        # El reintento AUTOMÁTICO de una rechazada se reclama con su propio
        # `aprobado_por`: si Temu la rechaza otra vez, no hay otro. Y lo SIGUE
        # llevando si un reclamo anterior del reintento terminó 'no_enviada'
        # (Temu no la juzgó): sin eso, cada pasajero le regalaba otro reintento.
        es_reintento = ev.get("reintento") == "auto" or any(po in reint for po in pos)
        res = await tgc.comprar(pos[0], ap["huella"], ap["emitida"],
                                QUIEN_REINTENTO if es_reintento else QUIEN)
        acc = str(res.get("accion") or "")
        intento = {"ventas": pos, "accion": acc, "motivo": res.get("motivo"),
                   "costo_mxn": ev["costo"], "package_sn": res.get("package_sn") or []}
        r["intentos"].append(intento)
        if res.get("compro"):
            compradas += 1
            racha_cod, racha_n = None, 0
            seguidos = previos = 0
            hoy["compras"] += 1
            hoy["gasto_mxn"] = round(hoy["gasto_mxn"] + float(ev["costo"] or 0), 2)
            for po in pos:
                _TRANSITORIO_DESDE.pop(po, None)
        if acc == "comprada":
            r["compradas"].append({
                "ventas": pos, "costo_mxn": ev["costo"], "paqueteria": paqs,
                "fecha_envio": ap.get("fecha_envio"), "horas": ap.get("horas"),
                "tarde": bool(ap.get("tarde")),
                "medidas": tgc.texto_medidas(ap.get("medidas") or []),
                "no_jt": list(ev.get("no_jt") or []),
                "avisos": list(ev.get("avisos_medida") or [])[:8],
                "reintento": ev.get("reintento"),
                "package_sn": res.get("package_sn") or []})
            veredicto, detalle = await _verificar_compra(res)
            intento["verificacion"] = veredicto
            if veredicto == "contradiccion":
                txt = await _detener(
                    "la orden de Odoo NO quedó con los datos de su guía: "
                    + "; ".join(m for x in detalle.values() for m in x["motivos"])[:180],
                    pos, "verificacion")
                r.update(estado="detenido", motivo=txt)
                return
            if veredicto != "ok":
                r.update(estado="verificando", motivo=(
                    f"{', '.join(pos)} comprada; su orden de Odoo todavía no queda confirmada "
                    "con guía y PDF: se re-verifica la vuelta siguiente (no se compra otra)"))
                return
            continue
        if acc == "pendiente":
            r.update(estado="verificando", motivo=(
                f"{', '.join(pos)}: Temu dio el packageSn y la etiqueta sigue en aplicación; se "
                "concilia la vuelta siguiente"))
            return
        if acc == "rechazada":
            # UN RECHAZO DOCUMENTADO DE TEMU NO DETIENE EL JOB (Brandon, 1-oct:
            # "que se sigan comprando, no es un bloqueante"): seguro que NO
            # compró. ESA venta queda "compra manual · Temu la rechazó: <código
            # y texto>", con campana, y se sigue con la siguiente. Durable: la
            # bitácora ya la tiene 'rechazada' (con su payload, para saber si su
            # plan cambia); aquí sólo el panel de este proceso.
            d_rech = next((d for d in res.get("llamadas") or []
                           if d.get("estado") == "rechazada"), {})
            cod = str(d_rech.get("codigo") or "sin código")
            txt_temu = str(d_rech.get("motivo") or res.get("motivo") or "")[:200]
            rech = [str(po) for po in d_rech.get("ventas") or []] or list(pos)
            final = es_reintento
            _marcar_manual({**ev, "clase": "rechazo_final" if final else "rechazo", "motivo": (
                f"compra manual · Temu la rechazó: {cod} {txt_temu} — "
                + ("ya era su reintento: su guía la compra una persona" if final
                   else "se reintenta UNA vez sola cuando cambie su plan (peso, caja, paquetería, "
                        "almacén o piezas; el cambio de día no cuenta), o al pulsar «Reintentar»"))},
                           solo=rech)
            if len(rech) < len(pos):
                _marcar_manual({**ev, "clase": "combinado_rechazado", "motivo": (
                    f"va en el mismo envío que {', '.join(rech)}, cuya compra automática Temu "
                    "rechazó — cómprala a mano")}, solo=[po for po in pos if po not in rech])
            r.setdefault("rechazadas", []).append({"ventas": rech, "codigo": cod, "motivo": txt_temu})
            log.warning("Compra automática de guías de Temu: Temu RECHAZÓ %s (%s: %s); queda para "
                        "compra manual y se sigue con las demás", ", ".join(rech), cod, txt_temu[:120])
            if cod != racha_cod:
                previos = 0                        # otra racha: la de la bitácora ya no cuenta
            racha_n = racha_n + 1 if cod == racha_cod else 1
            racha_cod = cod
            seguidos += 1
            if racha_n >= RECHAZOS_SEGUIDOS_CORTE:
                mot = (f"{racha_n} rechazos SEGUIDOS de Temu con el mismo código ({cod}: "
                       f"{txt_temu[:120]})"
                       + (f", {previos} de vueltas anteriores" if previos else "")
                       + ": esta vuelta deja de comprar para no quemar la cola "
                         "contra un fallo del sistema; el job NO se detiene (la vuelta siguiente "
                         "PRUEBA UNA sola venta: si Temu la compra, la racha se cierra y se sigue)")
                r.update(estado="rechazos_seguidos", motivo=mot)
                await _campana("temu_compra_auto_rechazos", f"cod:{cod}"[:30],
                               f"Compra automática de guías de Temu: {mot}. Ventas: "
                               + ", ".join(po for x in r["rechazadas"][-racha_n:] for po in x["ventas"]),
                               texto_ok=("La compra automática de guías de Temu volvió a comprar "
                                         "tras los rechazos."), recordatorio_h=4)
                return
            if seguidos >= RECHAZOS_SEGUIDOS_TOPE:
                mot = (f"{seguidos} rechazos SEGUIDOS de Temu sin una sola compra en medio (códigos "
                       f"distintos; el último, {cod}: {txt_temu[:100]}): esta vuelta deja de comprar "
                       "para no quemar la cola; el job NO se detiene (la vuelta siguiente prueba UNA "
                       "sola venta)")
                r.update(motivo=mot, estado="rechazos_seguidos")
                await _campana("temu_compra_auto_rechazos", "varios",
                               f"Compra automática de guías de Temu: {mot}. Ventas: "
                               + ", ".join(po for x in r["rechazadas"][-seguidos:] for po in x["ventas"]),
                               texto_ok=("La compra automática de guías de Temu volvió a comprar "
                                         "tras los rechazos."), recordatorio_h=4)
                return
            continue
        if acc == "pasarela":
            # La pasarela (firma, credenciales, permisos, IP) rechazó la llamada
            # ANTES de llegar al negocio: no compró y la venta queda libre
            # ('no_enviada'). Es del sistema: la vuelta termina aquí (la
            # siguiente lo reintenta; tres vueltas así suenan como BLOQUEO) y el
            # job NO se detiene: no hay dinero en duda.
            _marcar_manual({**ev, "clase": "transitorio", "motivo": (
                f"la pasarela de Temu rechazó la llamada ({str(res.get('motivo') or '')[:160]}): no "
                "compró; se reintenta sola la vuelta siguiente")})
            r["pausa_temu"] = True
            r.update(estado="pasarela", motivo=(
                f"la pasarela de Temu rechazó la compra de {', '.join(pos)} (firma, credenciales, "
                "permisos o IP): no compró; esta vuelta ya no le pide nada más a Temu"))
            return
        if acc in DETIENEN:
            txt = await _detener(f"la compra de {', '.join(pos)} terminó en '{acc}': "
                                 f"{str(res.get('motivo') or '')[:150]}", pos, acc)
            r.update(estado="detenido", motivo=txt)
            return
        if acc == "limite_velocidad":
            # 4000004 (demasiadas peticiones): Temu NO compró y la fila quedó
            # libre. Es pasajero: la venta se reintenta la vuelta siguiente
            # (sin reposo) y esta vuelta ya no le pide nada más a Temu.
            _marcar_manual({**ev, "clase": "transitorio", "motivo": (
                "Temu pidió bajar la velocidad (4000004, demasiadas peticiones): no compró; se "
                "reintenta sola la vuelta siguiente")})
            r["pausa_temu"] = True
            return
        if acc == "no_comprable":
            mot = [str(m) for m in res.get("motivos") or []]
            _marcar_manual({**ev, "clase": clase_de(mot), "motivo": "; ".join(mot)[:500]
                            or "al volver a planear ya no es comprable"})
        else:
            # No compró y no es grave (el plan cambió entre la vista previa y la
            # compra, la reclamó otro proceso, la relectura final vio un
            # cambio, la aprobación venció…): se sigue con el siguiente grupo y
            # ÉSTE se reintenta la vuelta siguiente (transitorio: sin reposo,
            # 30-sep — antes descansaba media hora).
            _marcar_manual({**ev, "clase": "transitorio", "motivo": (
                f"no se compró esta vuelta ({acc}): {str(res.get('motivo') or '')[:200]} — se "
                "reintenta sola")})


# ═════════════════════════════════════════════════════════════════════════════
#  7 · LO QUE VE EL PANEL (sin datos del comprador)
# ═════════════════════════════════════════════════════════════════════════════

def _venta_comprada(f: dict[str, Any]) -> dict[str, Any]:
    rep = _lista(f.get("reparto"))
    cajas: dict[str, float] = {}
    paqs: set[str] = set()
    for x in rep:
        if x.get("paqueteria"):
            paqs.add(str(x["paqueteria"]))
        try:
            cajas[str(x.get("caja") or x.get("orderSn"))] = float(x["costo_mxn"])
        except (KeyError, TypeError, ValueError):
            pass
    m = str(f.get("motivo") or "")
    verif = ("ok" if m.startswith(VERIF_OK) else "no" if m.startswith(VERIF_NO)
             else "detenida" if m.startswith(MARCA_DETENIDA) else "pendiente")
    estado = f.get("estado")
    # De dónde salió el peso y la caja de cada caja (la bitácora lo guarda en
    # el `reparto` desde el 30-sep), sin repetir.
    medidas: list[str] = []
    for x in rep:
        md = x.get("medida") if isinstance(x.get("medida"), dict) else None
        if md and md.get("fuente_txt"):
            t_ = f"{md['fuente_txt']} ({md.get('confianza') or '?'})"
            if t_ not in medidas:
                medidas.append(t_)
    return {"estado": ("comprada_auto" if estado in tgc.ESTADOS_HECHOS
                       else f"compra_{estado}"),
            "tarde": tgc.fue_tarde(f), "medidas": " + ".join(medidas) or None,
            "estado_bitacora": estado, "paqueteria": sorted(paqs),
            "costo_mxn": round(sum(cajas.values()), 2) if cajas else None,
            "fecha_envio": _iso(f.get("fecha_envio")), "horas": f.get("horas"),
            "package_sn": list(f.get("package_sn") or []),
            "comprada_at": _iso(reclamado_at(f)), "verificacion": verif,
            "en_aplicacion": estado == "pendiente",
            "aceptada": m.startswith(PEND_ACEPTADA),
            "detalle": m.split(" · ", 1)[1] if " · " in m else (m or None)}


def estado_panel() -> dict[str, Any]:
    """
    Lo que pinta Automatización → Temu: banderas, si está detenida y por qué,
    compras y gasto de hoy, las compras abiertas y las etiquetas en aplicación
    (para "Conciliar"), lo que el ENSAYO compraría y, por venta, "Guía comprada
    automáticamente" o "Requiere compra manual". ⚠️ BLOQUEA (lee kubera).
    Nunca lanza. Sin datos del comprador.
    """
    # Lo que la campana está diciendo, venta por venta: el panel cuenta lo mismo
    # (una transitoria que no se resuelve, o una "ya tiene guía" que no nace).
    en_campana = visibles_en_campana(_MANUALES)
    out: dict[str, Any] = {
        "banderas": banderas(), "encendida": auto_habilitada(), "detenido": None,
        "hoy": {"compras": 0, "gasto_mxn": 0.0}, "abiertas": [], "pendientes": [],
        "rechazadas": [], "error": None,
        "ultima_vuelta": {k: v for k, v in _ULTIMA.items() if k != "intentos"},
        "ventas": {po: {"estado": "manual", "clase": m["clase"],
                        "clase_txt": (ETIQUETA_CLASE["ya_comprada_atascada"]
                                      if atascada_con_guia(m)
                                      else ETIQUETA_CLASE.get(m["clase"], m["clase"])),
                        "urgente": m["urgente"], "motivo": m["motivo"],
                        "en_campana": po in en_campana,
                        "visto": m["visto"], "desde": m.get("desde")}
                   for po, m in _MANUALES.items()},
    }
    if _DETENCION_MEM:
        out["detenido"] = {k: v for k, v in _DETENCION_MEM.items()}
    if not tgc.compra_habilitada():
        # Con la compra apagada no pudo comprarse nada: no se lee la bitácora
        # (que puede ni existir todavía).
        return out
    try:
        filas = tgc.compras_recientes(_dias_bitacora(), 2000)
        abiertas = tgc.abiertas()
        pendientes = tgc.pendientes_auto()
        det = _leer_detencion()
    except Exception as exc:  # noqa: BLE001
        out["error"] = tgc._motivo_bitacora(exc)  # noqa: SLF001
        return out
    marcadas = [f for f in filas
                if es_auto(f) and str(f.get("motivo") or "").startswith(MARCA_DETENIDA)]
    if det:
        m = str(det.get("motivo") or "")
        entre = m[1:m.index("]")] if m.startswith("[") and "]" in m else ""
        ventas = (_DETENCION_MEM or {}).get("ventas") or [
            x.strip() for x in entre.split(",") if x.strip().startswith("PO-")]
        out["detenido"] = {"desde": _iso(det.get("actualizado_at")), "motivo": m or None,
                           "por": det.get("actualizado_por"), "ventas": ventas}
    elif marcadas and not out["detenido"]:
        out["detenido"] = {"desde": _iso(marcadas[0].get("actualizado_at")),
                           "motivo": str(marcadas[0].get("motivo") or "")
                           .removeprefix(f"{MARCA_DETENIDA} · ") or None,
                           "por": "auto (marcada en la bitácora)",
                           "ventas": sorted({str(f["parent_order_sn"]) for f in marcadas})}
    out["hoy"] = resumen_hoy(filas, dia_mx(_ahora()))
    out["abiertas"] = [{"parent_order_sn": a.get("parent_order_sn"), "estado": a.get("estado"),
                        "automatica": es_auto(a), "codigo": a.get("codigo"),
                        "motivo": str(a.get("motivo") or "")[:200] or None,
                        "desde": _iso(a.get("actualizado_at"))} for a in abiertas]
    out["pendientes"] = [{"parent_order_sn": p.get("parent_order_sn"), "estado": "pendiente",
                          "aceptada": str(p.get("motivo") or "").startswith(PEND_ACEPTADA),
                          "motivo": str(p.get("motivo") or "")[:200] or None,
                          "desde": _iso(p.get("actualizado_at"))} for p in pendientes]
    # Las 'rechazada' de la compra automática salen por `_MANUALES` (clase
    # 'rechazo' / 'rechazo_final') mientras su venta siga esperando guía, y
    # aquí con su código y texto de Temu para el botón «Reintentar».
    out["rechazadas"] = [
        {"parent_order_sn": str(f["parent_order_sn"]), "codigo": f.get("codigo"),
         "motivo": texto_rechazo(f)[:300], "desde": _iso(f.get("actualizado_at")),
         "reintento": ("pedido" if reintento_pedido(f)
                       else "agotado" if rechazo_agotado(f) else "disponible")}
        for f in filas if es_auto(f) and f.get("estado") == "rechazada"
        and str(f["parent_order_sn"]) in _MANUALES]
    for f in filas:
        if es_auto(f) and f.get("estado") not in tgc.ESTADOS_LIBRES:
            out["ventas"][str(f["parent_order_sn"])] = _venta_comprada(f)
    return out
