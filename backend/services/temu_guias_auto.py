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
      y medidas que no son de catálogo, la paquetería más barata, payload)
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

LO QUE NO SE COMPRA SOLO — "requiere compra manual", NO es un error
───────────────────────────────────────────────────────────────────
  · ventas ANTERIORES al corte `TEMU_COMPRA_GUIAS_DESDE` (hora de México; la
    fecha es la de VENTA según Temu, `parentOrderTime`). Ésas las compra una
    persona, siempre. Un grupo que MEZCLA una anterior y una nueva no se compra
    (se compra entero o nada) y la nueva sí avisa ("mezcla_corte");
  · lo que el planeador no da por comprable (medidas dudosas, combinado de
    SKUs sin medir, sin stock, cancelación pedida, guía ya comprada a mano…);
  · fecha imposible: ni con 24 h se cumple el límite de envío de Temu en día
    hábil → "compra manual URGENTE";
  · lo que Temu cotiza por encima de los topes de costo, o en otra moneda, o
    lo que ya no cabe en los topes del día;
  · una venta cuya compra automática Temu RECHAZÓ: queda para una persona
    (nunca se vuelve a intentar sola; la bitácora lo recuerda);
  · en MODO SIMPLE (`TEMU_COMPRA_GUIAS_AUTO_SOLO_SIMPLE`, nace encendido) todo
    lo que no sea UNA caja en una llamada sendType 0: los pedidos partidos y
    los combinados no están probados por API y su estreno es a propósito.
La campana avisa en RESUMEN (por cambio de estado, no una alerta por venta).
Se vuelve a evaluar cada `TEMU_COMPRA_GUIAS_REVISAR_MIN` (su motivo puede
resolverse: llega stock, alguien captura la medida). Una vuelta que no la
volvió a cotizar NO le renueva ese reposo ni le cambia el motivo.

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

UN FALLO O UN "NO SÉ SI COMPRÓ" DETIENE EL JOB
──────────────────────────────────────────────
Toda compra que no terminó en "comprada" sin ser un "no compró, seguro"
(desconocido, ya solicitada, etiqueta fallida, grupo a medias, horas
distintas, rechazo de Temu, error), una fila ABIERTA en la bitácora, una
etiqueta que sigue "en aplicación" pasado el plazo, o una orden de Odoo que no
quedó como debía → `ops.automatizacion_flags`
`temu_compra_guias_auto_detenida = true` + campana, y no se compra nada más.
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
PREFIJO_AUTO = "auto"
FLAG_DETENIDA = "temu_compra_guias_auto_detenida"
FLAG_TURNO = "temu_compra_guias_auto_turno"
TURNO_MIN = 20
# Una fila 'en_curso' más joven que esto es una compra EN VUELO (de otra
# instancia): se espera. Más vieja, es un proceso que murió a media compra.
EN_CURSO_ATASCADA_MIN = 10
_CORTE_OMISION = "2026-09-30"

VERIF_OK = "auto:verificada"
VERIF_PEND = "auto:por_verificar"
VERIF_NO = "auto:no_verificada"
# Una etiqueta automática que sigue "en aplicación" en Temu y que una persona
# ACEPTÓ (miró el seller center) para liberar el job: se sigue conciliando sola
# —si Temu la marca fallida, detiene— pero su edad ya no detiene.
PEND_ACEPTADA = "auto:pendiente_aceptada"
# La detención, escrita en las filas cuando la bandera no se pudo guardar.
MARCA_DETENIDA = "auto:detenida"

# Esperas (s) entre intentos del refresco inmediato antes de dejar la
# verificación "por verificar" (Temu puede tardar en mostrar la guía).
_ESPERAS_VERIFICACION: tuple[float, ...] = (0.0, 20.0, 45.0)

# Lo que contesta `comprar()` y DETIENE el job: hubo (o pudo haber) dinero de
# por medio y algo no salió como se planeó. El resto de lo que no compró
# (plan_cambio, no_comprable, sin_plan, reclamada, en_curso, detenida,
# aprobacion_vencida, sin_bitacora…) es un "no compró, seguro": se sigue.
DETIENEN = frozenset({"grupo_a_medias", "rechazada", "ya_solicitada", "desconocido",
                      "desconocido_previo", "fallida", "eco_distinto", "error"})

CLASES_PERMANENTES = frozenset({"anterior_corte", "sin_fecha_venta", "rechazo"})
CLASES_FUERA_DE_CAMPANA = frozenset({"anterior_corte", "transitorio", "ya_comprada", "ensayo"})
# Clases que NO dejan una venta en reposo: se vuelven a mirar la vuelta
# siguiente (el tope del día se levanta solo a medianoche).
CLASES_SIN_REPOSO = frozenset({"tope"})
_NO_COTIZADO = "no se cotizó (fuera del grupo pedido)"

ETIQUETA_CLASE = {
    "anterior_corte": "venta anterior al corte", "sin_fecha_venta": "sin fecha de venta",
    "mezcla_corte": "combinado con una venta anterior al corte",
    "urgente": "URGENTE (límite de envío)", "cancelacion": "cancelación pedida",
    "stock": "sin stock", "medidas": "medidas dudosas o sin medir",
    "paqueteria": "paquetería / cotización", "tope_costo": "Temu cotiza arriba del tope",
    "tope": "tope del día", "transitorio": "lectura fallida (se reintenta)",
    "ya_comprada": "ya tiene guía", "combinado_con_guia": "combinado con una guía ya comprada",
    "rechazo": "Temu rechazó la compra automática",
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
# Segunda red de la detención, por si kubera no deja escribirla. `guardada`
# dice si la bandera durable ya se escribió (si no, cada vuelta lo reintenta).
_DETENCION_MEM: dict[str, Any] | None = None
# Las URGENTES ya avisadas y el estado de campana que les tocó: una urgente
# NUEVA cambia el estado (y avisa); que una salga, no.
_URG_AVISADAS: set[str] = set()
_URG_ESTADO = "ok"


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


def banderas() -> dict[str, Any]:
    return {"compra_enabled": tgc.compra_habilitada(),
            "auto": bool(getattr(settings, "temu_compra_guias_auto", False)),
            "encendida": auto_habilitada(), "ensayo": ensayo(), "solo_simple": solo_simple(),
            "desde": str(getattr(settings, "temu_compra_guias_desde", _CORTE_OMISION) or ""),
            "paqueteria": str(getattr(settings, "temu_guias_paqueteria", "*") or "*"),
            "sabado_alterno": tgc.sabado_alterno(),
            "minutos": _minutos(), "revisar_min": _revisar_min(),
            "verificar_max_min": _verificar_max_min(), "topes": topes()}


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
                     "todavía no se puede surtir", "incierto")),
    # Antes que "medidas": todo motivo de una caja empieza con "caja de TEXCO:",
    # y "la paquetería preferida no se ofreció" no es un problema de medidas.
    ("paqueteria", ("paquetería", "cotiz", "moneda", "mxn")),
    ("medidas", ("peso", "medida", "medición", "caja", "densidad", "muestra", "catálogo",
                 "skus distintos", "interpolad")),
)


def clase_de(motivos: Iterable[str], urgente: bool = False) -> str:
    if urgente:
        return "urgente"
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


def evaluar_grupo(grupo: dict[str, Any], desde: datetime, t: dict[str, float], *,
                  simple: bool = False,
                  rechazadas: Iterable[str] = ()) -> dict[str, Any]:
    """
    ¿Se compra SOLO este grupo? PURA. Devuelve {decision, clase, motivo,
    urgente, costo, costos, ventas, por_venta}; `decision` ∈ comprar | manual |
    hecha. `por_venta` (opcional) = {PO: {clase, motivo}} cuando no todas las
    ventas del grupo están igual.

    El orden importa: primero lo que ninguna medida arregla (un rechazo previo,
    ya tener guía, el corte, la fecha de venta, el límite de envío), después el
    planeador, el modo simple y al final el dinero. `simple` = modo de arranque
    (sólo una caja sendType 0); `rechazadas` = ventas cuya compra automática
    Temu ya rechazó (nunca se reintentan solas).
    """
    a_comprar = [str(x) for x in grupo.get("a_comprar") or []]
    base: dict[str, Any] = {"ventas": a_comprar, "decision": "manual", "clase": "otro",
                            "motivo": "", "urgente": False, "costo": None, "costos": None}
    if not a_comprar:
        return {**base, "ventas": list(grupo.get("ventas") or []), "decision": "hecha",
                "clase": "hecha", "motivo": "la guía de este grupo ya la compró el panel"}
    motivos = motivos_de(grupo)
    fecha = grupo.get("fecha") or {}
    urgente = bool(fecha.get("urgente"))
    otras = lambda lst: (f" (el grupo incluye {', '.join(lst)})"  # noqa: E731
                         if len(a_comprar) > 1 else "")

    rech = [po for po in a_comprar if po in set(rechazadas)]
    if rech:
        txt = (f"Temu rechazó la compra automática de {', '.join(rech)}: su guía la compra una "
               f"persona{otras(rech)}")
        resto = [po for po in a_comprar if po not in rech]
        # La rechazada no se reintenta NUNCA sola; la que sólo va en su grupo
        # sí se vuelve a mirar (si la rechazada sale del grupo, puede comprarse).
        return {**base, "clase": "rechazo", "urgente": urgente, "motivo": txt,
                "por_venta": {po: {"clase": "combinado_rechazado",
                                   "motivo": (f"va en el mismo envío que {', '.join(rech)}, cuya "
                                              "compra automática Temu rechazó: el grupo se compra "
                                              "entero — cómprala a mano")}
                              for po in resto}}

    if not grupo.get("comprable"):
        # YA TIENE GUÍA (la compró una persona, o hay una compra abierta): no
        # hay nada que comprar. POR VENTA: en un combinado donde sólo una tiene
        # guía, la otra NO "ya tiene guía" — requiere que alguien decida.
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
    sin_fecha = [po for po in a_comprar if not tgc._entero(ts.get(po))]  # noqa: SLF001
    if sin_fecha:
        return {**base, "clase": "sin_fecha_venta",
                "motivo": (f"Temu no dio la fecha de venta de {', '.join(sin_fecha)}: sin ella no "
                           "se sabe si es anterior al corte — la compra una persona")}
    antes = [po for po in a_comprar if int(ts[po]) < int(desde.timestamp())]
    if antes and len(antes) == len(a_comprar):
        return {**base, "clase": "anterior_corte",
                "motivo": (f"venta anterior al corte ({desde.date().isoformat()}): su guía la "
                           f"compra una persona")}
    if antes:
        # Un combinado que MEZCLA: la nueva no se compra sola (el grupo va
        # entero), pero NO es "anterior al corte" — y si es urgente, avisa.
        nuevas = [po for po in a_comprar if po not in antes]
        txt = (f"combinado de {', '.join(nuevas)} (desde el corte) con {', '.join(antes)} "
               f"(anterior al corte {desde.date().isoformat()}): el grupo se compra entero — "
               "cómprala a mano"
               + ("; URGENTE: " + "; ".join(fecha.get("motivos") or [])[:200]
                  if urgente else ""))
        return {**base, "clase": "mezcla_corte", "urgente": urgente, "motivo": txt,
                "por_venta": {po: {"clase": "anterior_corte", "urgente": False, "motivo": (
                    f"venta anterior al corte ({desde.date().isoformat()}): su guía la compra "
                    f"una persona (el grupo incluye {', '.join(nuevas)})")} for po in antes}}
    if grupo.get("sin_limite"):
        return {**base, "clase": "otro",
                "motivo": (f"Temu no dio el límite de envío de {', '.join(grupo['sin_limite'])}: "
                           "sin él no se puede garantizar la fecha")}
    if not grupo.get("comprable") or not grupo.get("aprobacion"):
        return {**base, "clase": clase_de(motivos, urgente), "urgente": urgente,
                "motivo": ("; ".join(motivos) or "el planeador no la da por comprable")[:500]}
    if simple and not es_simple(grupo):
        lls = grupo.get("llamadas") or []
        return {**base, "clase": "simple",
                "motivo": (f"modo de arranque (TEMU_COMPRA_GUIAS_AUTO_SOLO_SIMPLE): sólo se "
                           f"compra sola UNA caja sendType 0; ésta lleva {len(lls)} llamada(s) "
                           f"sendType {sorted({ll.get('send_type') for ll in lls}, key=str)} con "
                           f"{sum(len(ll.get('paquetes') or []) for ll in lls)} caja(s) — "
                           "cómprala a mano")}
    cs = costos_de_grupo(grupo)
    if cs["faltan"]:
        return {**base, "clase": "paqueteria", "costos": cs,
                "motivo": (f"sin costo cotizado EN PESOS para la caja de {', '.join(cs['faltan'])}")}
    caras = [c for c in cs["cajas"] if c["costo_mxn"] > t["caja"]]
    if caras:
        c = caras[0]
        return {**base, "clase": "tope_costo", "costos": cs,
                "motivo": (f"Temu cotiza MX${c['costo_mxn']:.2f} por la caja de {c['almacen']} "
                           f"({c['paqueteria'] or '?'}) y el tope por caja es MX${t['caja']:.2f} "
                           "(TEMU_COMPRA_GUIAS_MAX_CAJA)")}
    caras_v = {po: m for po, m in cs["por_venta"].items() if m > t["venta"]}
    if caras_v:
        po, m = next(iter(sorted(caras_v.items())))
        return {**base, "clase": "tope_costo", "costos": cs,
                "motivo": (f"la guía de {po} costaría MX${m:.2f} y el tope por venta es "
                           f"MX${t['venta']:.2f} (TEMU_COMPRA_GUIAS_MAX_VENTA)")}
    ap = grupo["aprobacion"]
    paqs = sorted({c["paqueteria"] for c in cs["cajas"] if c["paqueteria"]})
    return {**base, "decision": "comprar", "clase": "comprable", "costo": cs["total"],
            "costos": cs,
            "motivo": (f"{len(cs['cajas'])} caja(s) · {', '.join(paqs) or '?'} · "
                       f"MX${cs['total']:.2f} · entrega {ap.get('dia_envio')} "
                       f"{ap.get('fecha_envio')} ({ap.get('horas')} h)"
                       + (" · fecha adelantada por el límite de Temu"
                          if ap.get("ajustada") else ""))}


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
    if plan.get("cola_truncada"):
        faltas.append("la cola de espera pasa del tope TEMU_GUIAS_COLA_MAX")
    return "; ".join(faltas) or None


def texto_rechazo(fila: dict[str, Any]) -> str:
    """Por qué una venta con una compra automática 'rechazada' queda para una
    persona (nunca se reintenta sola). PURA."""
    m = str(fila.get("motivo") or "")
    if m.startswith("liberada a mano"):
        return (f"la compra automática quedó sin respuesta y se liberó a mano ({m[:160]}): su "
                "guía la compra una persona")
    return (f"Temu rechazó la compra automática ({fila.get('codigo') or 'sin código'}: "
            f"{m[:160]}): su guía la compra una persona")


def estado_urgentes(urgentes: Iterable[str], avisadas: set[str], previo: str) -> str:
    """El estado de la campana de URGENTES. PURA. Una urgente NUEVA cambia el
    estado (la campana habla); que una salga, no (sigue el mismo). Sin
    urgentes, 'ok'. Cabe en `alertas_estado.estado` (30)."""
    lista = sorted(set(urgentes))
    if not lista:
        return "ok"
    if previo != "ok" and set(lista) <= avisadas:
        return previo
    return "urgente:" + hashlib.sha1(",".join(lista).encode("utf-8")).hexdigest()[:10]


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
                              quien: str = "") -> dict[str, Any]:
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
        ts = time.monotonic() if (renovar or not previo) else float(previo.get("ts") or 0.0)
        _MANUALES[po] = {"motivo": str(propio.get("motivo") or ev.get("motivo") or "")[:500],
                         "clase": propio.get("clase") or ev.get("clase") or "otro",
                         "urgente": bool(propio.get("urgente", ev.get("urgente"))), "visto": ahora,
                         "desde": previo.get("desde") or ahora, "ts": ts}


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
        if ev.get("urgente"):
            _marcar_manual(ev, renovar=False, solo=[po])
        elif previo:
            continue                           # su motivo y su reposo, intactos
        elif _NO_COTIZADO in str(ev.get("motivo") or ""):
            continue                           # no se miró: nada que decir
        else:
            _marcar_manual(ev, solo=[po])


async def _avisar_resumen() -> None:
    """La campana EN RESUMEN: una por cambio de estado, no una por venta."""
    global _URG_ESTADO, _URG_AVISADAS
    visibles = {po: m for po, m in _MANUALES.items()
                if m["clase"] not in CLASES_FUERA_DE_CAMPANA}
    urgentes = sorted(po for po, m in visibles.items() if m["urgente"])
    por_clase: dict[str, int] = {}
    for m in visibles.values():
        por_clase[m["clase"]] = por_clase.get(m["clase"], 0) + 1
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
                   (f"{len(urgentes)} venta(s) de Temu ya no alcanzan su límite de envío con la "
                    f"regla de fechas: compra su guía A MANO HOY — {', '.join(urgentes[:12])}"
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
                         "compradas": [], "intentos": [], "ensayo": [], "hoy": None}
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
                                      else "ensayo" if r["ensayo"] else "sin_compras")
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
    en_cola = {str(x["order_id"]) for x in cola}
    for po in [p for p in _MANUALES if p not in en_cola]:
        _MANUALES.pop(po, None)          # ya salió de la espera (comprada a mano, cancelada…)
    ocupadas = {str(f.get("parent_order_sn")) for f in filas
                if f.get("estado") not in tgc.ESTADOS_LIBRES}
    # Las que Temu ya RECHAZÓ en una compra automática: nunca se reintentan
    # solas (la bitácora lo recuerda aunque el contenedor se reinicie). Sin
    # esto, cada liberación del job volvía a intentar la misma venta y a
    # detenerse con ella, en bucle.
    rechazos = {str(f.get("parent_order_sn")): f for f in filas
                if es_auto(f) and f.get("estado") == "rechazada"}
    for po, f in rechazos.items():
        if po in en_cola and (_MANUALES.get(po) or {}).get("clase") != "rechazo":
            _marcar_manual({"ventas": [po], "clase": "rechazo", "motivo": texto_rechazo(f)})
    nuevas: list[tuple[int, str]] = []
    for i, x in enumerate(cola):
        po = str(x["order_id"])
        creado = _fecha(x.get("creado_at"))
        if po in ocupadas or po in rechazos or creado is None or creado < desde:
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
        for _i, po in nuevas:
            previo = _MANUALES.get(po)
            if previo and previo["clase"] not in ("transitorio", "tope"):
                continue
            _marcar_manual({"ventas": [po], "clase": "tope", "motivo": (
                f"tope del día alcanzado ({tope[1]}): cómprala a mano o espera a mañana")})
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
    alcance = tgc._LIMITE_MAX  # noqa: SLF001
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
    try:
        await _planear_y_comprar(r, min(alcance, dentro[-1] + 1 + 5),
                                 {str(cola[i]["order_id"]) for i in dentro}, desde, t, hoy,
                                 ahora, token, set(rechazos))
    finally:
        try:
            await asyncio.to_thread(_soltar_turno, token)
        except Exception as exc:  # noqa: BLE001
            log.warning("compra automática: no se pudo soltar el turno (%s); vence solo en "
                        "%s min", str(exc)[:120], TURNO_MIN)
    await _avisar_resumen()


async def _planear_y_comprar(r: dict[str, Any], limite: int, candidatas: set[str],
                             desde: datetime, t: dict[str, float], hoy: dict[str, Any],
                             ahora: datetime | None, token: str,
                             rechazadas: set[str] | None = None) -> None:
    plan = await tgc.plan_guias(limite=limite, usar_cache=False, ahora=ahora,
                                cotizar=candidatas, tope_llamadas=max(150, 3 * limite + 30),
                                segundos_max=270.0)
    r["plan"] = {"ok": bool(plan.get("ok")), "grupos": (plan.get("resumen") or {}).get("grupos"),
                 "comprables": (plan.get("resumen") or {}).get("comprables"),
                 "llamadas_temu": plan.get("llamadas_temu"),
                 "detalles_reusados": plan.get("detalles_reusados")}
    if not plan.get("ok"):
        r.update(estado="sin_plan", motivo=str(plan.get("error") or "el plan no salió")[:300])
        return
    falla = plan_incompleto(plan)
    if falla:
        r.update(estado="plan_incompleto", motivo=f"no se compra a ciegas: {falla}")
        return

    simple = solo_simple()
    ensayando = ensayo()
    evals = [(g, evaluar_grupo(g, desde, t, simple=simple, rechazadas=rechazadas or set()))
             for g in plan.get("grupos") or []]
    for g, ev in evals:
        _anotar_evaluacion(g, ev, candidatas)
    r["evaluadas"] = len(evals)
    r["manuales"] = sum(len(ev["ventas"]) for _g, ev in evals if ev["decision"] == "manual")

    compradas = 0
    for g, ev in evals:
        if ev["decision"] != "comprar":
            continue
        pos = ev["ventas"]
        if compradas >= t["vuelta"]:
            r["esperan_turno"] = r.get("esperan_turno", 0) + 1
            continue
        if hoy["compras"] + 1 > t["dia"]:
            _marcar_manual({**ev, "clase": "tope", "motivo": (
                f"tope diario de compras automáticas alcanzado ({int(t['dia'])}): cómprala a "
                "mano o espera a mañana")})
            continue
        if hoy["gasto_mxn"] + float(ev["costo"] or 0) > t["gasto_dia"]:
            _marcar_manual({**ev, "clase": "tope", "motivo": (
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
        res = await tgc.comprar(pos[0], ap["huella"], ap["emitida"], QUIEN)
        acc = str(res.get("accion") or "")
        intento = {"ventas": pos, "accion": acc, "motivo": res.get("motivo"),
                   "costo_mxn": ev["costo"], "package_sn": res.get("package_sn") or []}
        r["intentos"].append(intento)
        if res.get("compro"):
            compradas += 1
            hoy["compras"] += 1
            hoy["gasto_mxn"] = round(hoy["gasto_mxn"] + float(ev["costo"] or 0), 2)
        if acc == "comprada":
            r["compradas"].append({
                "ventas": pos, "costo_mxn": ev["costo"], "paqueteria": paqs,
                "fecha_envio": ap.get("fecha_envio"), "horas": ap.get("horas"),
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
        if acc in DETIENEN:
            if acc == "rechazada":
                # Durable: la bitácora ya la tiene 'rechazada' por el job, y la
                # vuelta siguiente la excluye. Aquí sólo el panel de este proceso.
                rech = [po for d in res.get("llamadas") or [] if d.get("estado") == "rechazada"
                        for po in d.get("ventas") or []] or list(pos)
                _marcar_manual({**ev, "clase": "rechazo", "motivo": (
                    f"Temu rechazó la compra automática: {str(res.get('motivo') or '')[:200]} — su "
                    "guía la compra una persona")}, solo=rech)
                if len(rech) < len(pos):
                    _marcar_manual({**ev, "clase": "combinado_rechazado", "motivo": (
                        f"va en el mismo envío que {', '.join(rech)}, cuya compra automática Temu "
                        "rechazó — cómprala a mano")}, solo=[po for po in pos if po not in rech])
            txt = await _detener(f"la compra de {', '.join(pos)} terminó en '{acc}': "
                                 f"{str(res.get('motivo') or '')[:150]}", pos, acc)
            r.update(estado="detenido", motivo=txt)
            return
        if acc == "no_comprable":
            mot = [str(m) for m in res.get("motivos") or []]
            _marcar_manual({**ev, "clase": clase_de(mot), "motivo": "; ".join(mot)[:500]
                            or "al volver a planear ya no es comprable"})
        else:
            # No compró y no es grave (el plan cambió entre la vista previa y la
            # compra, la reclamó otro proceso, la relectura final vio un
            # cambio…): se sigue con el siguiente grupo y ÉSTE descansa media
            # hora — si no, un precio que cambia en cada cotización volvería a
            # planear la cola entera cada vuelta sin comprar nunca.
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
    return {"estado": ("comprada_auto" if estado in tgc.ESTADOS_HECHOS
                       else f"compra_{estado}"),
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
    out: dict[str, Any] = {
        "banderas": banderas(), "encendida": auto_habilitada(), "detenido": None,
        "hoy": {"compras": 0, "gasto_mxn": 0.0}, "abiertas": [], "pendientes": [],
        "error": None,
        "ultima_vuelta": {k: v for k, v in _ULTIMA.items() if k != "intentos"},
        "ventas": {po: {"estado": "manual", "clase": m["clase"],
                        "clase_txt": ETIQUETA_CLASE.get(m["clase"], m["clase"]),
                        "urgente": m["urgente"], "motivo": m["motivo"],
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
    # (Las 'rechazada' de la compra automática salen por `_MANUALES`, clase
    # 'rechazo', mientras su venta siga esperando guía: la vuelta las anota.)
    for f in filas:
        if es_auto(f) and f.get("estado") not in tgc.ESTADOS_LIBRES:
            out["ventas"][str(f["parent_order_sn"])] = _venta_comprada(f)
    return out
