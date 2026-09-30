"""
temu_cancelaciones.py — El vigilante de cancelaciones de Temu.

EL ENCARGO (Brandon, 30-sep-2026)
─────────────────────────────────
"Veo que algunas tienen cancelaciones, por lo que tenemos que capturarlas
INMEDIATAMENTE y mostrarlas en el panel."

POR QUÉ HACÍA FALTA
───────────────────
Nadie volvía a preguntarle a Temu por una venta ya registrada:
  · el sondeo de ventas (`pedidos_temu_sondeo`, 15 min) SALTA lo registrado y
    su ventana es de 2 días;
  · el trabajo de guías (cada 2 h) sí re-lee el estado de las que esperan guía,
    pero el 3 no estaba mapeado: la venta cancelada contaba como "sin mapear" y
    se quedaba esperando los 14 días enteros;
  · Temu no manda webhooks (0 eventos desde siempre).
Y mientras una venta cancelada sigue `espera_guia`, `stock_watch`
(STOCK_WATCH_RESTA_PENDIENTES) RESTA su pieza antes de copiar el stock de Odoo a
Woo: la mercancía de una venta muerta queda escondida de Woo y de los canales.
Medido el 30-sep: PO-128-09246640169592260 llevaba 4 días "Esperando guía" en el
panel con Temu diciendo "Pedido cancelado".

LO QUE MIRA (cada `TEMU_CANCELACIONES_MIN`, 10 min)
──────────────────────────────────────────────────
Las ventas VIVAS de la bitácora (`odoo_ventas_log.vigilables_cancelacion`):
  · todas las que esperan su guía (sin orden en Odoo), y
  · las que ya tienen orden en Odoo, de los últimos 14 días.
Y, SÓLO DEL LADO DE ODOO (sin volver a preguntarle a Temu: una cancelación no
se deshace), las que ya marcó y todavía piden algo, hasta 30 días — ver
"LAS QUE YA SE MARCARON" abajo.

LO QUE HACE CON CADA CANCELADA (orderStatus 3 — ver `pedidos_temu._ESTADOS_WC`)
──────────────────────────────────────────────────────────────────────────────
  · ESPERABA GUÍA (sin orden) → primero le pregunta a ODOO si alguien la
    capturó a mano con la venta como referencia (y todavía no se vinculó: eso
    lo hace el trabajo de guías cada 2 h).
      - Sin orden viva → `cancelada_sin_orden`. UN solo UPDATE la saca de la
        cola de creación Y de la resta de stock: su pieza vuelve al anaquel en
        la siguiente pasada de `stock_watch`.
      - Con orden viva → se VINCULA (`fijar_orden`) y sigue como "con orden".
        Sin esto la orden a mano quedaba huérfana, reservando stock de una
        venta muerta, sin que ninguna pantalla lo dijera.
      - Odoo no contesta → no se marca nada; la vuelta siguiente reintenta.
  · CON ORDEN EN ODOO Y SIN SURTIR → con `TEMU_CANCELACIONES_CANCELAR_ODOO`
    encendida, `odoo_ventas.cancelar_orden` sobre ESA orden (por su id, no por
    referencia), justo después de RE-LEERLA: si entre la primera lectura y la
    cancelación se surtió o salió, no se cancela. Después se vuelve a leer y se
    exige que no quede ninguna viva. Apagada —como nace—, la fila queda
    `cancelada_por_cancelar`: roja en el panel, pidiendo que alguien la cancele.
  · YA SALIÓ (alguna entrega de salida en `done`) → NO se toca Odoo:
    `cancelada_revisar`, posible devolución.
  · YA SE SURTIÓ (PICK o PACK en `done`, la salida pendiente) → tampoco se
    cancela sola: `cancelada_revisar`, "regresar la mercancía al anaquel".
    Cancelarla dejaría la pieza en la zona de salida o empaque, ofrecida otra
    vez como libre, sin que nadie avise.
  · SURTIDO DIVIDIDO (varias órdenes vivas) → no se cancela solo: por cancelar.
  · LA BITÁCORA DICE QUE TIENE ORDEN Y ODOO NO LA ENCUENTRA →
    `cancelada_sin_rastro` (no se sabe si salió: no se rotula como tal).

LAS QUE YA SE MARCARON (el paso 3 de cada vuelta; sólo Odoo)
────────────────────────────────────────────────────────────
Corre SIEMPRE que el vigilante esté encendido, con la bandera de Odoo apagada
o encendida. Apagada sólo LEE Odoo y escribe la bitácora:
  · `cancelada_por_cancelar` → si alguien ya la canceló a mano, `ya_cancelada`
    (sale del rojo); si mientras tanto salió, `cancelada_revisar`. Con la
    bandera encendida, además la cancela (el rezago: ver LAS BANDERAS).
  · `cancelada_revisar` → con la devolución validada en Odoo,
    `cancelada_devuelta` (no pide nada); si la orden ya se canceló,
    `ya_cancelada`.
  · `cancelada_sin_rastro` → si la orden aparece, sigue el camino normal.
  · El aviso "REVISAR · Temu canceló parte…" se retira cuando la orden de Odoo
    ya lleva las piezas vivas (ver abajo).

LA CANCELACIÓN PARCIAL (piezas de un renglón; la venta sigue viva)
──────────────────────────────────────────────────────────────────
  · Espera su guía → sus renglones de la bitácora bajan a las piezas VIVAS
    (`ajustar_piezas_espera`, que además re-numera sin huecos: `registrar`
    escribe por posición), que son las que resta `stock_watch`; la orden nacerá
    con las vivas (`pedidos_temu._normalizar`).
  · Ya tiene orden → Odoo no se toca; queda el aviso "REVISAR · Temu canceló
    parte…" al principio del motivo y el panel la pide. En cuanto las líneas de
    la orden en Odoo ya no llevan más que las piezas vivas, el aviso cambia a
    "…ya lleva las piezas vivas" y deja de pedir.
  · Si las cantidades no cuadran (`quantity + cancelada != original`), o un
    renglón en 3 conserva piezas, NO se decide nada con ellas: cuenta en
    `no_fiables` y se espera. Lo único medido es un caso total (0 + 1 == 1);
    una parcial real todavía no se ha visto.

LAS BANDERAS (regla 3)
──────────────────────
  TEMU_CANCELACIONES_ENABLED        true  → marcar la bitácora, liberar la
                                            resta y re-mirar Odoo (SÓLO
                                            LECTURA de Odoo). Sólo escribe
                                            NUESTRA bitácora.
  TEMU_CANCELACIONES_CANCELAR_ODOO  false → cancelar órdenes en Odoo. Escribe en
                                            Odoo: espera el dale de Brandon.
Para cancelar en Odoo, además de la bandera, tienen que estar encendidos el
interruptor general de Automatización, el interruptor DEL CANAL Temu, y
apagado `ODOO_VENTAS_SOLO_REGISTRO` (los mismos que respetan `crear_orden`,
`fijar_guia` y `fijar_etiqueta`). Si no, la fila queda por cancelar diciendo
cuál falta.
⚠️ EL REZAGO. Al encender CANCELAR_ODOO, el paso 3 cancela las
`cancelada_por_cancelar` acumuladas (hasta 30 días), 20 por vuelta, sin volver
a preguntarle a Temu. Para verlas ANTES:
`POST …/temu/cancelaciones/vigilar?simular=true&como_si_cancelar_odoo=true`
dice orden por orden qué S… cancelaría.

LA CUOTA
────────
La de Temu es de la app, compartida con el sondeo, las guías y el investigador.
Por vuelta, a lo más `TEMU_CANCELACIONES_MAX_LLAMADAS` (30):
  1. La lectura por lote: `bg.order.list.v2.get` pidiendo sólo canceladas
     (`parentOrderStatus=3`). El filtro NO se da por bueno: cada venta que
     vuelve se juzga por el estado que ella misma trae, y lo que no esté en la
     vigilancia se ignora. `lote.filtro_honrado` dice si Temu lo respetó. Si
     no lo respeta, cuesta UNA llamada; si lo respeta, se siguen pidiendo
     páginas (hasta `TEMU_CANCELACIONES_LOTE_PAGINAS`) y ES la captura
     inmediata, sin importar cuántas ventas haya en la vigilancia.
  2. Detalle por venta con REPARTO JUSTO (`repartir`): las que esperan guía
     primero —esconden stock— con un tercio garantizado para las de orden; en
     cada grupo, la que lleva más tiempo sin mirarse.
Una venta vista entregada (5) deja de preguntarse; una enviada (4), cada 6 h.
Tres fallos seguidos de Temu cortan la vuelta (`temu_caido`). Un detalle que
contesta OTRA venta que la pedida no se usa para decidir (`respuestas_ajenas`).

LO QUE NO HACE, y por qué
─────────────────────────
  · NO cancela el pedido de Woo ni toca `channel.orders`. El camino de siempre
    (`pedidos_ml.sincronizar(forzar_estado="cancelled")`) llamaría además a
    `cancelar_orden` sin esta bandera. Es una decisión aparte.
  · NO decide con el filtro del lote (ver arriba).
  · NO ajusta órdenes de Odoo por una cancelación parcial: lo pide.

Regla 11: bitácora y Odoo van en `asyncio.to_thread`. Nunca lanza. Sin datos
del comprador: ids de venta, SKUs, cantidades y estados.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.temu_cancelaciones")

CANAL = "temu"
CUENTA = "TEMU"
ESTADO_PAGADA = 2
ESTADO_ENVIADA = 4
ESTADO_ENTREGADA = 5

# Las acciones de la bitácora que dicen "hay orden VIVA en Odoo".
ACCIONES_CON_ORDEN = ("confirmada", "creada", "ya_existia", "no_se_pudo_confirmar")
ACCION_POR_CANCELAR = "cancelada_por_cancelar"
ACCION_REVISAR = "cancelada_revisar"
ACCION_SIN_RASTRO = "cancelada_sin_rastro"
ACCION_DEVUELTA = "cancelada_devuelta"
# Las que ya se marcaron y todavía piden algo: se re-miran SÓLO en Odoo. Es
# `odoo_ventas_log.ACCIONES_CANCELADA_REMIRAR`; se repite aquí para que las
# funciones puras no importen la bitácora.
ACCIONES_REMIRAR = (ACCION_POR_CANCELAR, ACCION_REVISAR, ACCION_SIN_RASTRO)
TIPOS_REMIRAR = ("por_cancelar", "revisar", "sin_rastro", "parcial")

_PAUSA_S = 0.25                 # entre llamadas de detalle: sin ráfagas
_FALLOS_SEGUIDOS_MAX = 3        # y con tres seguidas, Temu está caído
_LOTE_PAGINA = 50               # el tamaño que ya usa el sondeo
_REPOSO_ENVIADA_S = 6 * 3600
_REPOSO_ENTREGADA_S = 15 * 86400
_DETALLE_MAX = 60               # renglones del resumen (sólo ids y decisiones)
_ODOO_POR_VUELTA = 20           # re-miradas del lado de Odoo por vuelta

# CDMX es UTC-6 fijo desde 2022: sin base de zonas horarias.
_MX = timezone(timedelta(hours=-6))
_RE_DETECTADA = re.compile(r"detectada (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) CDMX")
# "SKU-A 2→1" dentro del aviso de cancelación parcial. Los SKUs no llevan
# espacios ni comas.
_RE_PAR = re.compile(r"([^\s,:|]+) (\d+)→(\d+)")

_ultimo: dict[str, Any] = {"estado": "sin_ejecutar"}
_ultima_simulacion: dict[str, Any] = {"estado": "sin_ejecutar"}
# Memoria del reparto: cuándo se leyó bien cada venta, y hasta cuándo no hace
# falta volver a preguntar (enviadas y entregadas). Se pierde al reiniciar, y
# eso sólo cuesta que la primera vuelta empiece por las más nuevas.
_visto: dict[str, float] = {}
_reposo: dict[str, float] = {}
# Lo mismo para las re-miradas del lado de Odoo (paso 3): rota el cupo.
_visto_odoo: dict[str, float] = {}
_candado_loop: dict[str, Any] = {"loop": None, "lock": None}


# ── utilidades puras ─────────────────────────────────────────────────────────

def _b(nombre: str, omision: bool) -> bool:
    return bool(getattr(settings, nombre, omision))


def _i(nombre: str, omision: int) -> int:
    try:
        return max(0, int(getattr(settings, nombre, omision)))
    except (TypeError, ValueError):
        return omision


def banderas() -> dict[str, Any]:
    """Cómo está configurado, para el panel. Sólo lectura de `settings`."""
    return {"enabled": _b("temu_cancelaciones_enabled", True),
            "cancelar_odoo": _b("temu_cancelaciones_cancelar_odoo", False),
            "minutos": _i("temu_cancelaciones_min", 10),
            "max_llamadas": _i("temu_cancelaciones_max_llamadas", 30),
            "lote": _b("temu_cancelaciones_lote", True)}


def estado() -> dict[str, Any]:
    """La última vuelta del trabajo (no las simulaciones)."""
    return dict(_ultimo)


def estado_simulacion() -> dict[str, Any]:
    return dict(_ultima_simulacion)


def _hora_mx(cuando: datetime | None = None) -> str:
    d = cuando or datetime.now(timezone.utc)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(_MX).strftime("%Y-%m-%d %H:%M")


def _detectada(fila: dict[str, Any] | None) -> str:
    """El momento de la detección. Si la fila ya lo dice en su motivo, ése —así
    el texto no cambia de una vuelta a otra y la fila no se re-escribe—; si no,
    ahora."""
    m = _RE_DETECTADA.search(str((fila or {}).get("motivo") or ""))
    return m.group(1) if m else _hora_mx()


def _prefijo(detectada: str) -> str:
    return f"Temu la canceló (orderStatus 3) · detectada {detectada} CDMX"


def motivo_sin_orden(detectada: str | None = None) -> str:
    """El motivo de `cancelada_sin_orden` para Temu. Lo usa también el trabajo
    de guías, para que las dos vías escriban lo mismo."""
    return (f"{_prefijo(detectada or _hora_mx())} · esperaba su guía: no se crea orden "
            "en Odoo y su pieza deja de restarse del stock.")


def _sn_de(cruda: dict[str, Any]) -> str:
    padre = cruda.get("parentOrderMap") or {}
    renglones = cruda.get("orderList") or [{}]
    return str(padre.get("parentOrderSn")
               or (renglones[0] or {}).get("parentOrderSn")
               or (renglones[0] or {}).get("orderSn") or "").strip()


def repartir(filas: list[dict[str, Any]], tope: int,
             visto: dict[str, float]) -> list[dict[str, Any]]:
    """
    A quién preguntarle esta vuelta, con `tope` llamadas. Pura.

    Las que esperan guía PRIMERO: esconden stock y son las que el panel pinta
    "Esperando guía". Pero un tercio del cupo queda GARANTIZADO para las de
    orden en Odoo —si no, una cola de espera larga las dejaría sin turno— y lo
    que un grupo no use se lo queda el otro.

    Dentro de cada grupo, las NUNCA MIRADAS primero, INTERCALANDO la más nueva y
    la más vieja: una venta se cancela o recién comprada (el comprador se
    arrepiente) o vieja (Temu cancela sola la que no se envió a tiempo; la del
    30-sep llevaba 4 días). Empezar sólo por un extremo dejaría el otro para
    horas después de cada despliegue, que es cuando esta memoria se vacía.
    Después, la que lleva más tiempo sin mirarse.
    """
    tope = max(0, int(tope))
    if not tope:
        return []

    def _ts(f: dict[str, Any]) -> float:
        c = f.get("creado_at")
        return c.timestamp() if isinstance(c, datetime) else 0.0

    def ordenar(grupo: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nuevas = sorted((f for f in grupo if visto.get(f["order_id"]) is None),
                        key=_ts, reverse=True)
        intercaladas: list[dict[str, Any]] = []
        i, j = 0, len(nuevas) - 1
        while i <= j:
            intercaladas.append(nuevas[i])
            if j != i:
                intercaladas.append(nuevas[j])
            i, j = i + 1, j - 1
        vistas = sorted((f for f in grupo if visto.get(f["order_id"]) is not None),
                        key=lambda f: visto[f["order_id"]])
        return intercaladas + vistas

    esp = ordenar([f for f in filas if f.get("tipo") == "espera"])
    con = ordenar([f for f in filas if f.get("tipo") == "con_orden"])
    reserva_con = min(len(con), tope // 3)
    n_esp = min(len(esp), tope - reserva_con)
    n_con = min(len(con), tope - n_esp)
    return esp[:n_esp] + con[:n_con]


def _por_cancelar(detectada: str, nombres: str, razon: str) -> dict[str, Any]:
    return {"cancelar": False, "accion": ACCION_POR_CANCELAR, "estado": "",
            "motivo": f"{_prefijo(detectada)} · {nombres or 'la orden'} sigue viva en "
                      f"Odoo SIN surtir: {razon}"}


def decidir_con_orden(odoo: dict[str, Any], cancelar_odoo: bool,
                      detectada: str) -> dict[str, Any]:
    """
    Qué hacer con una venta cancelada que YA tiene orden en Odoo. Pura.

    `odoo` = lo que devolvió `_odoo_de_venta`. Devuelve `{cancelar, accion,
    estado, motivo}`; con `cancelar=True` quien llama pide `cancelar_orden` y
    traduce su resultado con `traducir_cancelar`.

    El orden de las preguntas es la guarda: lo que YA SALIÓ o YA SE SURTIÓ
    nunca se cancela, ni con la bandera encendida. Y el MOTIVO depende sólo del
    estado de Odoo y del momento de la detección (que sale del motivo viejo):
    con el mismo estado, el mismo texto — la fila no se re-escribe cada vuelta.
    """
    pre = _prefijo(detectada)
    nombres = odoo.get("nombres") or "la orden"
    if not odoo.get("encontradas"):
        return {"cancelar": False, "accion": ACCION_SIN_RASTRO, "estado": "",
                "motivo": f"{pre} · la bitácora dice que tiene orden, pero Odoo no la "
                          "encuentra (ni por id ni por referencia): revisar a mano si "
                          "se borró o se capturó con otra referencia."}
    if not odoo.get("vivas"):
        return {"cancelar": False, "accion": "ya_cancelada", "estado": "cancel",
                "motivo": f"{pre} · {nombres} ya estaba cancelada en Odoo."}
    if odoo.get("entrega") == "hecha":
        if odoo.get("devuelta"):
            return {"cancelar": False, "accion": ACCION_DEVUELTA, "estado": "",
                    "motivo": f"{pre} · la entrega de {nombres} había salido y la "
                              "devolución ya está validada en Odoo: la mercancía "
                              "regresó."}
        return {"cancelar": False, "accion": ACCION_REVISAR, "estado": "",
                "motivo": f"{pre} · la entrega de {nombres} YA SALIÓ del almacén: "
                          "posible devolución. Revisar si la mercancía regresó; la "
                          "orden de Odoo no se toca."}
    if odoo.get("surtida"):
        return {"cancelar": False, "accion": ACCION_REVISAR, "estado": "",
                "motivo": f"{pre} · {nombres} YA SE SURTIÓ (PICK/PACK validado) y la "
                          "salida sigue pendiente: NO se cancela sola. Regresar la "
                          "mercancía al anaquel y cancelar la orden a mano."}
    if int(odoo.get("vivas") or 0) > 1:
        return {"cancelar": False, "accion": ACCION_POR_CANCELAR, "estado": "",
                "motivo": f"{pre} · surtido dividido ({nombres}) sin surtir: cancelar "
                          "las órdenes a mano en Odoo (la cancelación automática "
                          "sólo sabe cancelar una)."}
    if not cancelar_odoo:
        return _por_cancelar(detectada, nombres,
                             "reserva stock y hay que cancelarla. (La cancelación "
                             "automática está apagada: TEMU_CANCELACIONES_CANCELAR_ODOO.)")
    return {"cancelar": True, "accion": None, "estado": "", "motivo": pre}


def traducir_cancelar(res: dict[str, Any], nombres: str, detectada: str) -> dict[str, Any]:
    """El resultado de `_cancelar_en_odoo` (o de `odoo_ventas.cancelar_orden`) →
    acción y motivo de la bitácora. Pura. Lo que no terminó en cancelada se
    queda POR CANCELAR, para que la vuelta siguiente lo reintente y el panel lo
    siga pidiendo."""
    pre = _prefijo(detectada)
    res = res or {}
    acc = str(res.get("accion") or "")
    nombre = str(res.get("nombre") or nombres or "la orden")
    if acc == "_redecidida":
        # Al re-leer justo antes de cancelar, Odoo contó otra historia (se
        # surtió, salió, se canceló o apareció otra orden): manda la nueva.
        dec = dict(res.get("dec") or {})
        if dec.get("cancelar") or not dec.get("accion"):
            return _por_cancelar(detectada, nombre,
                                 "cambió entre la lectura y la cancelación; se "
                                 "reintenta la próxima vuelta.")
        return dec
    if acc == "_sigue_viva":
        return _por_cancelar(detectada, nombre,
                             f"Odoo contestó '{res.get('contesto')}' pero al re-leer "
                             "la venta sigue con una orden viva: cancelarla a mano.")
    if acc == "cancelada":
        return {"accion": "cancelada", "estado": "cancel",
                "motivo": f"{pre} · {nombre} se CANCELÓ en Odoo (estado re-leído)."}
    if acc == "ya_cancelada":
        return {"accion": "ya_cancelada", "estado": "cancel",
                "motivo": f"{pre} · {nombre} ya estaba cancelada en Odoo."}
    if acc == "no_se_pudo_cancelar":
        return {"accion": "no_se_pudo_cancelar", "estado": str(res.get("estado") or ""),
                "motivo": f"{pre} · Odoo NO canceló {nombre} (quedó en "
                          f"'{res.get('estado')}'): probablemente tiene entrega hecha "
                          "o factura; cancelarla a mano."}
    razones = {
        "solo_registro_cancelar": "Odoo está en observación (solo registro): no se canceló.",
        "apagado": "el interruptor de Automatización está apagado: no se canceló.",
        "sin_orden": "`cancelar_orden` no la encontró: cancelarla a mano.",
    }
    razon = razones.get(acc) or (f"el intento de cancelar falló "
                                 f"({str(res.get('motivo') or '?')[:80]}): se "
                                 "reintenta la próxima vuelta.")
    return _por_cancelar(detectada, nombre, razon)


def pares_parcial(motivo: str | None) -> dict[str, tuple[int, int]]:
    """{sku: (original, viva)} del aviso de cancelación parcial que abre el
    motivo ("…Temu canceló parte de la venta: SKU-A 2→1, SKU-B 3→1. …"). Pura.
    Sólo mira el primer tramo (antes de " | "), que es donde vive el aviso."""
    tramo = str(motivo or "").split(" | ", 1)[0]
    if "Temu canceló parte" not in tramo:
        return {}
    return {s: (int(o), int(v)) for s, o, v in _RE_PAR.findall(tramo)}


def texto_parcial(bajaron: dict[str, tuple[int, int]], odoo_name: str | None,
                  ajustada: bool) -> str:
    """El aviso de cancelación parcial de una venta CON orden. Pura y
    determinista (mismas piezas → mismo texto): el UPDATE que lo escribe es
    idempotente por el texto."""
    from services import odoo_ventas_log
    pares = ", ".join(f"{s} {o}→{v}" for s, (o, v) in sorted(bajaron.items()))
    if ajustada:
        return (f"{odoo_ventas_log.PREFIJO_PARCIAL} de la venta: {pares}. La orden "
                f"{odoo_name or ''} de Odoo ya lleva sólo las piezas vivas.")
    return (f"{odoo_ventas_log.PREFIJO_PARCIAL_REVISAR} de la venta: {pares}. La orden "
            f"{odoo_name or ''} ya existía con las piezas originales: ajustarla a mano "
            "en Odoo.")


def _vacio(simular: bool) -> dict[str, Any]:
    def _cont() -> dict[str, int]:
        return {"canceladas_en_odoo": 0, "ya_canceladas": 0, "no_se_pudo": 0,
                "por_cancelar": 0, "revisar": 0, "sin_rastro": 0, "devueltas": 0,
                "cancelaria": 0}
    return {"estado": "ok", "ts": None, "simular": simular, "error": None,
            "cancelar_odoo": _b("temu_cancelaciones_cancelar_odoo", False),
            "como_si_cancelar_odoo": False,
            "tope": 0, "llamadas": 0, "vigilables": 0,
            "por_tipo": {"espera": 0, "con_orden": 0, "por_cancelar": 0,
                         "revisar": 0, "sin_rastro": 0, "parcial": 0},
            "en_reposo": 0, "consultadas": 0, "sin_consultar": 0,
            "lote": {"pedido": False, "paginas": 0, "total_temu": None,
                     "devueltas": 0, "estados": {},
                     "coincidencias": 0, "filtro_honrado": None, "error": None},
            "vivas": 0, "pagadas_por_enviar": 0, "enviadas": 0, "entregadas": 0,
            "canceladas_detectadas": 0, "canceladas_sin_orden": 0, "vinculadas": 0,
            "con_orden": _cont(), "reintentos_odoo": _cont(),
            "remiradas": {"por_cancelar": 0, "revisar": 0, "sin_rastro": 0,
                          "parcial": 0, "sin_mirar": 0},
            "parciales": 0, "parciales_espera": 0, "parciales_con_orden": 0,
            "parciales_ajustadas": 0,
            "no_fiables": 0, "contradictorias": 0, "sin_piezas_vivas": 0,
            "respuestas_ajenas": 0,
            "estados_sin_mapear": {}, "fallos_temu": 0, "fallos_odoo": 0,
            "temu_caido": False, "carreras": 0, "cortado_por_tiempo": False,
            "solo_no_vigiladas": [], "detalle": []}


def _anotar(r: dict[str, Any], renglon: dict[str, Any], siempre: bool = False) -> None:
    """Un renglón del resumen: SÓLO ids, SKUs, estados y la decisión."""
    if (siempre or renglon.get("decision")) and len(r["detalle"]) < _DETALLE_MAX:
        r["detalle"].append(renglon)


def _podar(ahora: float) -> None:
    """La memoria del reparto no crece sin fin: lo que ya salió de la ventana
    (o del reposo) se olvida."""
    viejo = ahora - 35 * 86400
    for memoria in (_visto, _visto_odoo):
        for k in [k for k, t in memoria.items() if t < viejo]:
            memoria.pop(k, None)
    for k in [k for k, t in _reposo.items() if t < ahora]:
        _reposo.pop(k, None)


def _candado() -> asyncio.Lock:
    """Uno por event loop (un Lock no se comparte entre loops)."""
    loop = asyncio.get_running_loop()
    if _candado_loop["loop"] is not loop:
        _candado_loop.update(loop=loop, lock=asyncio.Lock())
    return _candado_loop["lock"]


# ── Odoo (BLOQUEA: sólo desde un hilo) ───────────────────────────────────────

def _odoo_de_venta(fila: dict[str, Any], con_devolucion: bool = False) -> dict[str, Any]:
    """
    Las órdenes de Odoo de esta venta y en qué punto está su mercancía. ⚠️
    BLOQUEA (XML-RPC). Nunca lanza: `{ok: False, error}` si Odoo no contesta.

    Se buscan por el `odoo_order_id` de la bitácora Y por referencia (`<venta>`
    y `<venta>#n`, el surtido dividido, con el partner de Temu). Sirve también
    para una venta SIN orden en la bitácora (la captura a mano sin vincular).

      entrega   la regla de la conciliación (`clasificar`): alguna ENTREGA DE
                SALIDA en `done` = "hecha" (la mercancía se fue);
      surtida   algún picking INTERNO (PICK/PACK de la ruta de 2 o 3 pasos) en
                `done`: la mercancía ya salió del anaquel aunque no del almacén;
      devuelta  todas las órdenes vivas con salida hecha tienen su devolución
                validada. Con `con_devolucion` se buscan además las
                devoluciones que apuntan a la salida por `return_id` aunque no
                cuelguen de la orden (una llamada más).
    Sin datos del comprador: id y nombre S…, estado y pickings.
    """
    from services import odoo_ventas as ov
    from services.odoo_ventas_conciliacion import clasificar

    campos_pk = ["picking_type_code", "state", "return_id"]
    sn = str(fila["order_id"])
    por_ref: list[Any] = ["|", ["client_order_ref", "=", sn],
                          ["client_order_ref", "=like", f"{sn}#%"]]
    partner = ov._PARTNER.get(CANAL)  # noqa: SLF001
    if partner:
        por_ref = ["&", ["partner_id", "=", partner], *por_ref]
    oid = fila.get("odoo_order_id")
    dominio = ["|", ["id", "=", int(oid)], *por_ref] if oid else por_ref
    try:
        ords = ov._kw("sale.order", "search_read", [dominio],  # noqa: SLF001
                      {"fields": ["name", "state", "client_order_ref", "picking_ids"],
                       "limit": 20}) or []
        ids_pk = sorted({p for o in ords for p in (o.get("picking_ids") or [])})
        pks: dict[int, dict[str, Any]] = {}
        if ids_pk:
            for p in ov._kw("stock.picking", "read",  # noqa: SLF001
                            [ids_pk, campos_pk]) or []:
                pks[p["id"]] = p
        de_retorno: dict[int, list[dict[str, Any]]] = {}
        salidas = sorted(i for i, p in pks.items() if p.get("picking_type_code") == "outgoing")
        if con_devolucion and salidas:
            for p in ov._kw("stock.picking", "search_read",  # noqa: SLF001
                            [[["return_id", "in", salidas]]], {"fields": campos_pk}) or []:
                origen = p.get("return_id")
                o_id = origen[0] if isinstance(origen, (list, tuple)) and origen else origen
                if o_id:
                    de_retorno.setdefault(int(o_id), []).append(p)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:150]}
    ordenes = []
    for o in ords:
        propios = [pks[i] for i in (o.get("picking_ids") or []) if i in pks]
        ids_salida = {p["id"] for p in propios if p.get("picking_type_code") == "outgoing"}
        ya = {p["id"] for p in propios}
        extra = [p for s in ids_salida for p in de_retorno.get(s, []) if p.get("id") not in ya]
        c = clasificar(propios + extra, ids_salida)
        ordenes.append({"id": o.get("id"), "nombre": str(o.get("name") or ""),
                        "estado": o.get("state"), "entrega": c["entrega"],
                        "devolucion_hecha": c["devolucion_hecha"],
                        "surtida": any(p.get("picking_type_code") == "internal"
                                       and p.get("state") == "done" for p in propios)})
    vivas = [o for o in ordenes if o["estado"] != "cancel"]
    entrega = ("hecha" if any(o["entrega"] == "hecha" for o in vivas)
               else "pendiente" if any(o["entrega"] == "pendiente" for o in vivas)
               else "sin_entrega")
    salieron = [o for o in vivas if o["entrega"] == "hecha"]
    nombres = " + ".join(o["nombre"] for o in (vivas or ordenes) if o["nombre"])
    return {"ok": True, "encontradas": len(ordenes), "vivas": len(vivas),
            "ids_vivas": [o["id"] for o in vivas],
            "estados_vivas": [str(o["estado"] or "") for o in vivas],
            "entrega": entrega, "surtida": any(o["surtida"] for o in vivas),
            "devuelta": bool(salieron) and all(o["devolucion_hecha"] for o in salieron),
            "nombres": nombres or str(fila.get("odoo_name") or "")}


def _puede_cancelar() -> tuple[bool, str]:
    """¿Se puede escribir en Odoo? Los MISMOS interruptores que respetan
    `crear_orden`, `fijar_guia` y `fijar_etiqueta`: el general, el DEL CANAL y
    el modo observación. ⚠️ BLOQUEA (lee `ops.automatizacion_flags`). Nunca
    lanza; si no puede leerlos, dice que no (falla cerrado)."""
    from services import odoo_ventas as ov
    try:
        if not ov.habilitado():
            return False, "el interruptor general de Automatización está apagado: no se canceló."
        if not ov.canal_activo(CANAL):
            return False, "el canal Temu está APAGADO en Automatización: no se canceló."
        if bool(getattr(settings, "odoo_ventas_solo_registro", True)):
            return False, "Odoo está en observación (ODOO_VENTAS_SOLO_REGISTRO): no se canceló."
    except Exception as exc:  # noqa: BLE001
        return False, f"no se pudieron leer los interruptores ({str(exc)[:60]}): no se canceló."
    return True, ""


def _cancelar_en_odoo(fila: dict[str, Any], odoo_id: int, detectada: str) -> dict[str, Any]:
    """
    Cancela LA orden `odoo_id` de esta venta, con las guardas pegadas a la
    escritura. ⚠️ BLOQUEA. Nunca lanza.

    1. RE-LEE la venta en Odoo y vuelve a decidir: si entre la primera lectura
       y ahora se surtió, salió, se canceló o apareció otra orden, no cancela y
       devuelve `_redecidida` con la decisión nueva.
    2. Cancela POR ID (`cancelar_orden(odoo_id=…)`), no por referencia.
    3. Si Odoo dice cancelada (o ya cancelada), vuelve a leer la venta y exige
       que no quede NINGUNA orden viva; si queda, `_sigue_viva`.
    """
    from services import odoo_ventas as ov
    ahora = _odoo_de_venta(fila)
    if not ahora.get("ok"):
        return {"accion": "error", "motivo": f"Odoo no contestó al re-leer ({ahora.get('error')})"}
    dec = decidir_con_orden(ahora, True, detectada)
    if not dec["cancelar"] or (ahora.get("ids_vivas") or [None])[0] != odoo_id:
        return {"accion": "_redecidida", "dec": dec, "nombre": ahora.get("nombres")}
    res = ov.cancelar_orden(CANAL, str(fila["order_id"]), odoo_id=odoo_id) or {}
    if res.get("accion") in ("cancelada", "ya_cancelada"):
        despues = _odoo_de_venta(fila)
        if not despues.get("ok") or despues.get("vivas"):
            return {"accion": "_sigue_viva", "contesto": res.get("accion"),
                    "nombre": res.get("nombre")}
    return res


def _piezas_en_odoo(fila: dict[str, Any]) -> dict[str, Any]:
    """Las piezas por SKU de las órdenes VIVAS de esta venta en Odoo (las líneas
    de la orden, por `default_code`). ⚠️ BLOQUEA. Nunca lanza."""
    from services import odoo_ventas as ov
    info = _odoo_de_venta(fila)
    if not info.get("ok"):
        return info
    por_sku: dict[str, float] = {}
    ids = [int(i) for i in (info.get("ids_vivas") or []) if i]
    if ids:
        try:
            lineas = ov._lineas_de_ordenes(ids)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)[:150]}
        for ls in (lineas or {}).values():
            for ln in ls or []:
                s = str(ln.get("sku") or "").strip()
                if s:
                    por_sku[s] = por_sku.get(s, 0.0) + float(ln.get("cantidad") or 0)
    return {**info, "por_sku": por_sku}


# ── atender una venta ya leída ───────────────────────────────────────────────

async def _cancelacion_con_orden(fila: dict[str, Any], r: dict[str, Any],
                                 simular: bool, cancelar_odoo: bool,
                                 renglon: dict[str, Any],
                                 reintento: bool = False,
                                 info: dict[str, Any] | None = None) -> None:
    from services import odoo_ventas_log

    sn = fila["order_id"]
    cuenta = fila.get("cuenta") or CUENTA
    cont = r["reintentos_odoo"] if reintento else r["con_orden"]
    detectada = _detectada(fila)
    if info is None:
        info = await asyncio.to_thread(_odoo_de_venta, fila, fila.get("tipo") == "revisar")
    if not info.get("ok"):
        # Sin saber si ya salió, no se decide nada: la vuelta siguiente reintenta.
        r["fallos_odoo"] += 1
        renglon["decision"] = f"Odoo no contestó ({info.get('error')}); se reintenta"
        return
    renglon.update(odoo=info.get("nombres"), entrega=info.get("entrega"))
    if info.get("surtida"):
        renglon["surtida"] = True
    dec = decidir_con_orden(info, cancelar_odoo, detectada)
    if dec["cancelar"]:
        puede, razon = await asyncio.to_thread(_puede_cancelar)
        if not puede:
            dec = _por_cancelar(detectada, info.get("nombres") or "", razon)
        elif simular:
            cont["cancelaria"] += 1
            renglon["decision"] = f"cancelaría {info.get('nombres')} en Odoo"
            return
        else:
            res = await asyncio.to_thread(_cancelar_en_odoo, fila,
                                          int((info.get("ids_vivas") or [0])[0]), detectada)
            dec = traducir_cancelar(res, info.get("nombres") or "", detectada)
    k = {"cancelada": "canceladas_en_odoo", "ya_cancelada": "ya_canceladas",
         "no_se_pudo_cancelar": "no_se_pudo", ACCION_POR_CANCELAR: "por_cancelar",
         ACCION_REVISAR: "revisar", ACCION_SIN_RASTRO: "sin_rastro",
         ACCION_DEVUELTA: "devueltas"}.get(dec["accion"], "por_cancelar")
    cont[k] += 1
    if simular:
        renglon["decision"] = f"marcaría {dec['accion']}"
        return
    desde = (ACCIONES_REMIRAR if reintento
             else (*ACCIONES_CON_ORDEN, *ACCIONES_REMIRAR))
    escrito = await asyncio.to_thread(
        odoo_ventas_log.marcar_cancelacion_con_orden, CANAL, cuenta, sn,
        dec["accion"], dec["motivo"], dec.get("estado") or "", desde)
    renglon["decision"] = dec["accion"] + ("" if escrito else " (sin cambios)")
    if escrito:
        log.warning("TEMU cancelada con orden: %s → %s (%s, entrega %s%s)", sn,
                    dec["accion"], info.get("nombres"), info.get("entrega"),
                    ", surtida" if info.get("surtida") else "")


async def _vincular_y_seguir(fila: dict[str, Any], info: dict[str, Any],
                             r: dict[str, Any], simular: bool, cancelar_odoo: bool,
                             renglon: dict[str, Any]) -> None:
    """
    Una venta que ESPERABA su guía resultó tener orden VIVA en Odoo (alguien la
    capturó a mano y el trabajo de guías todavía no la vinculaba). Se vincula
    —el HECHO es que la orden existe— y sigue por el camino "con orden":
    marcarla `cancelada_sin_orden` dejaba esa orden huérfana, reservando stock
    de una venta muerta. Nunca lanza (lo de adentro atrapa).
    """
    from services import odoo_ventas_log

    sn = fila["order_id"]
    cuenta = fila.get("cuenta") or CUENTA
    oid = int((info.get("ids_vivas") or [0])[0])
    estados = info.get("estados_vivas") or []
    accion = ("confirmada" if estados and all(e in ("sale", "done") for e in estados)
              else "creada")
    nombres = info.get("nombres") or ""
    if not simular:
        fijada = await asyncio.to_thread(
            odoo_ventas_log.fijar_orden, CANAL, cuenta, sn, oid, nombres,
            estados[0] if estados else "", accion,
            f"Vinculada por el vigilante de cancelaciones: {nombres} se capturó a "
            "mano en Odoo con esta venta como referencia (antes: espera_guia).")
        if not fijada:
            # La fila ya tenía orden (otra vía llegó primero) o kubera no
            # contestó: la vuelta siguiente la ve como es.
            r["carreras"] += 1
            renglon["decision"] = f"no se pudo vincular {nombres}; se reintenta"
            return
    r["vinculadas"] += 1
    fila2 = {**fila, "tipo": "con_orden", "odoo_order_id": oid, "odoo_name": nombres,
             "accion": accion, "motivo": None}
    await _cancelacion_con_orden(fila2, r, simular, cancelar_odoo, renglon, info=info)
    renglon["decision"] = (("vincularía " if simular else "vinculada a ") + nombres
                           + " · " + str(renglon.get("decision") or ""))


async def _parcial_con_orden(fila: dict[str, Any], bajaron: dict[str, tuple[int, int]],
                             r: dict[str, Any], simular: bool,
                             renglon: dict[str, Any]) -> None:
    """
    Temu canceló PIEZAS de una venta cuya orden de Odoo YA existe. Odoo no se
    toca; se mira si la orden ya lleva sólo las piezas vivas:
      · sí → el aviso dice que ya está ajustada y deja de pedir;
      · no → "REVISAR · Temu canceló parte…" y el panel la pide;
      · Odoo no contesta → no se escribe nada (no se retira un aviso a ciegas).
    """
    from services import odoo_ventas_log

    sn = fila["order_id"]
    cuenta = fila.get("cuenta") or CUENTA
    lectura = await asyncio.to_thread(_piezas_en_odoo, fila)
    if not lectura.get("ok"):
        r["fallos_odoo"] += 1
        renglon["decision"] = f"parcial con orden: Odoo no contestó ({lectura.get('error')})"
        return
    por_sku = lectura.get("por_sku") or {}
    ajustada = bool(lectura.get("vivas")) and all(
        por_sku.get(s, 0.0) <= v for s, (_o, v) in bajaron.items())
    texto = texto_parcial(bajaron, fila.get("odoo_name") or lectura.get("nombres"), ajustada)
    if simular:
        renglon["decision"] = ("anotaría parcial ya ajustada en Odoo" if ajustada
                               else "anotaría REVISAR (parcial con orden)")
        return
    anotado = await asyncio.to_thread(odoo_ventas_log.anotar_parcial_con_orden,
                                      CANAL, cuenta, sn, texto)
    if anotado:
        if ajustada:
            r["parciales_ajustadas"] += 1
        else:
            r["parciales_con_orden"] += 1
            log.warning("TEMU cancelación PARCIAL con orden ya creada: %s (%s) — "
                        "revisar la orden de Odoo", sn, fila.get("odoo_name"))
    renglon["decision"] = (("parcial ya ajustada en Odoo" if ajustada
                            else "REVISAR: parcial con orden")
                           + ("" if anotado else " (sin cambios)"))


async def _atender(fila: dict[str, Any], det: dict[str, Any], r: dict[str, Any],
                   simular: bool, cancelar_odoo: bool,
                   siempre: bool = False) -> None:
    """Una venta de la vigilancia con su detalle recién leído. Nunca lanza
    (lo de adentro ya atrapa); los errores quedan en el resumen."""
    from services import odoo_ventas_log, pedidos_temu

    sn = fila["order_id"]
    cuenta = fila.get("cuenta") or CUENTA
    renglon: dict[str, Any] = {"venta": sn, "tipo": fila.get("tipo")}
    try:
        # ¿ES LA VENTA QUE SE PIDIÓ? Una respuesta de Temu que trae otra venta
        # (o ninguna) no decide nada sobre ésta: cancelaría la orden de una
        # venta viva por el estado de otra.
        sn_det = _sn_de(det)
        if sn_det != sn:
            r["respuestas_ajenas"] += 1
            renglon["decision"] = (f"Temu contestó otra venta ({sn_det or 'sin id'}): "
                                   "no se decide nada")
            return
        an = pedidos_temu.analizar_cancelacion(det)
        est = an["estado"]
        renglon["estado_temu"] = est
        if est == pedidos_temu.ESTADO_CANCELADA:
            if not an["cancelada"]:
                # El 3 sin la forma medida (padre ausente, o algún renglón en otro
                # estado: 2/4/5, 41…): no se toca.
                r["contradictorias"] += 1
                renglon["decision"] = "contradictoria (3 sin la forma medida): no se toca"
                return
            r["canceladas_detectadas"] += 1
            if fila.get("tipo") == "espera":
                # ¿ALGUIEN LA CAPTURÓ A MANO? Antes de darla por "sin orden".
                info = await asyncio.to_thread(_odoo_de_venta, fila)
                if not info.get("ok"):
                    r["fallos_odoo"] += 1
                    renglon["decision"] = (f"Odoo no contestó ({info.get('error')}): no se "
                                           "marca; se reintenta")
                    return
                if info.get("vivas"):
                    await _vincular_y_seguir(fila, info, r, simular, cancelar_odoo, renglon)
                    return
                if simular:
                    renglon["decision"] = ("marcaría cancelada_sin_orden: sale de la cola "
                                           "de guías y de la resta de stock")
                    return
                marcada = await asyncio.to_thread(
                    odoo_ventas_log.marcar_cancelada_sin_orden, CANAL, cuenta, sn,
                    str(est), motivo_sin_orden())
                if marcada:
                    r["canceladas_sin_orden"] += 1
                    renglon["decision"] = "cancelada_sin_orden"
                    log.warning("TEMU cancelada esperando guía: %s → sale de la cola y "
                                "de la resta de stock", sn)
                else:
                    # La fila dejó de esperar entre la lectura y ahora (su orden
                    # nació, o kubera no contestó): la vuelta siguiente la ve
                    # con su estado nuevo.
                    r["carreras"] += 1
                    renglon["decision"] = "no se marcó: la fila ya no espera; se reintenta"
                return
            await _cancelacion_con_orden(fila, r, simular, cancelar_odoo, renglon)
            return

        if est == ESTADO_ENTREGADA:
            r["entregadas"] += 1
            _reposo[sn] = time.time() + _REPOSO_ENTREGADA_S
        elif est == ESTADO_ENVIADA:
            r["enviadas"] += 1
            _reposo[sn] = time.time() + _REPOSO_ENVIADA_S
        elif est == ESTADO_PAGADA:
            r["pagadas_por_enviar"] += 1
        else:
            clave = str(est)
            r["estados_sin_mapear"][clave] = r["estados_sin_mapear"].get(clave, 0) + 1
            renglon["decision"] = "estado sin mapear: no se toca"
            return
        r["vivas"] += 1

        # ¿CANCELÓ PIEZAS? La venta sigue viva pero algún renglón bajó.
        if an["no_fiables"]:
            r["no_fiables"] += 1
            renglon["decision"] = "cantidades que no cuadran: no se decide nada"
            return
        if an["sin_piezas_vivas"]:
            r["sin_piezas_vivas"] += 1
            renglon["decision"] = "viva pero sin piezas vivas: no se toca"
            return
        bajaron = {s: (an["originales"].get(s, 0), v) for s, v in an["vivas"].items()
                   if v < an["originales"].get(s, 0)}
        if not bajaron:
            return
        r["parciales"] += 1
        renglon["parcial"] = {s: f"{o}→{v}" for s, (o, v) in bajaron.items()}
        if fila.get("tipo") == "espera":
            if simular:
                renglon["decision"] = "bajaría la espera a las piezas vivas"
                return
            res = await asyncio.to_thread(
                odoo_ventas_log.ajustar_piezas_espera, CANAL, cuenta, sn, an["vivas"],
                f"{odoo_ventas_log.PREFIJO_PARCIAL} de la venta · detectada "
                f"{_hora_mx()} CDMX")
            if res.get("cambios"):
                r["parciales_espera"] += 1
                log.warning("TEMU cancelación PARCIAL esperando guía: %s → %s", sn,
                            ", ".join(f"{c['sku']} {c['antes']}→{c['ahora']}"
                                      for c in res["cambios"]))
            renglon["decision"] = ("espera ajustada a las piezas vivas" if res.get("cambios")
                                   else "espera sin cambios" + (
                                       f" (ambiguos: {res.get('ambiguos')})"
                                       if res.get("ambiguos") else ""))
            return
        await _parcial_con_orden(fila, bajaron, r, simular, renglon)
    except Exception as exc:  # noqa: BLE001 — una venta mala no detiene la vuelta
        renglon["decision"] = f"error: {str(exc)[:120]}"
        log.warning("temu_cancelaciones: %s falló: %s", sn, str(exc)[:150])
    finally:
        _anotar(r, renglon, siempre)


async def _remirar(fila: dict[str, Any], r: dict[str, Any], simular: bool,
                   cancelar_odoo: bool, siempre: bool) -> None:
    """Paso 3: una fila ya marcada (o una parcial vieja), SÓLO contra Odoo.
    Nunca lanza."""
    tipo = fila.get("tipo")
    renglon: dict[str, Any] = {"venta": fila["order_id"], "tipo": tipo}
    r["remiradas"][tipo] = r["remiradas"].get(tipo, 0) + 1
    try:
        if tipo == "parcial":
            bajaron = pares_parcial(fila.get("motivo"))
            if not bajaron:
                renglon["decision"] = "aviso parcial ilegible: no se toca"
                return
            await _parcial_con_orden(fila, bajaron, r, simular, renglon)
        else:
            renglon["estado_temu"] = 3
            await _cancelacion_con_orden(fila, r, simular, cancelar_odoo, renglon,
                                         reintento=True)
    except Exception as exc:  # noqa: BLE001
        renglon["decision"] = f"error: {str(exc)[:120]}"
    finally:
        _visto_odoo[fila["order_id"]] = time.time()
        _anotar(r, renglon, siempre)


# ── la vuelta ────────────────────────────────────────────────────────────────

def _segundos_max() -> float:
    # 60% del intervalo, con techo de 4 min: una vuelta lenta nunca pisa a la
    # siguiente.
    return float(min(240, max(60, _i("temu_cancelaciones_min", 10) * 60 * 0.6)))


async def _vuelta(r: dict[str, Any], tope: int | None, solo: list[str] | None,
                  simular: bool, como_si_cancelar_odoo: bool = False) -> None:
    from services import odoo_ventas, odoo_ventas_log, pedidos_temu, temu

    if not temu.disponible():
        r.update(estado="error", error="Temu no está configurado (faltan TEMU_*)")
        return
    tope = _i("temu_cancelaciones_max_llamadas", 30) if tope is None else max(0, int(tope))
    r["tope"] = tope
    # `como_si_cancelar_odoo` SÓLO vale simulando: es para ver, antes del dale,
    # qué órdenes cancelaría la bandera —incluido el rezago del paso 3—.
    como_si = bool(simular and como_si_cancelar_odoo)
    cancelar_odoo = _b("temu_cancelaciones_cancelar_odoo", False) or como_si
    r["cancelar_odoo"] = cancelar_odoo
    r["como_si_cancelar_odoo"] = como_si
    try:
        filas = await asyncio.to_thread(
            odoo_ventas_log.vigilables_cancelacion, CANAL,
            odoo_ventas._dias_espera(),  # noqa: SLF001 — la MISMA ventana de la resta
            _i("temu_cancelaciones_dias", 14), ACCIONES_CON_ORDEN)
    except Exception as exc:  # noqa: BLE001
        # Sin bitácora no se sabe a quién mirar: no se gasta cuota a ciegas.
        r.update(estado="error", error=f"bitácora: {str(exc)[:200]}")
        return
    siempre = bool(solo) or simular
    if solo:
        pedidas = {str(x).strip() for x in solo if str(x).strip()}
        r["solo_no_vigiladas"] = sorted(pedidas - {f["order_id"] for f in filas})
        filas = [f for f in filas if f["order_id"] in pedidas]
    ahora = time.time()
    _podar(ahora)
    r["vigilables"] = len(filas)
    for f in filas:
        r["por_tipo"][f["tipo"]] = r["por_tipo"].get(f["tipo"], 0) + 1
    por_venta = {f["order_id"]: f for f in filas if f["tipo"] in ("espera", "con_orden")}
    preguntables = [f for f in por_venta.values()
                    if solo or _reposo.get(f["order_id"], 0) <= ahora]
    r["en_reposo"] = len(por_venta) - len(preguntables)
    atendidas: set[str] = set()
    limite_reloj = time.monotonic() + _segundos_max()

    # 1 · EL LOTE. Una llamada que puede traer todas las canceladas de golpe.
    #     PÁGINAS ADAPTATIVAS: la 2ª y siguientes sólo se piden si TODO lo que
    #     llegó hasta ahí viene en 3 (el filtro se está respetando) y la página
    #     vino llena. Si Temu ignora el filtro, el lote cuesta UNA llamada y se
    #     deja; si lo respeta, recorre las canceladas hasta el tope de páginas.
    if _b("temu_cancelaciones_lote", True) and tope > 0 and por_venta and not solo:
        r["lote"]["pedido"] = True
        for pagina in range(1, max(1, _i("temu_cancelaciones_lote_paginas", 4)) + 1):
            if r["llamadas"] >= tope:
                break
            if pagina > 1 and set(r["lote"]["estados"]) != {"3"}:
                break
            r["llamadas"] += 1
            try:
                res = await temu.llamar("bg.order.list.v2.get", {
                    "pageNumber": pagina, "pageSize": _LOTE_PAGINA,
                    "parentOrderStatus": pedidos_temu.ESTADO_CANCELADA})
            except Exception as exc:  # noqa: BLE001 — el lote es un extra
                r["lote"]["error"] = str(exc)[:160]
                break
            lote = pedidos_temu._lista_de_dicts(res)  # noqa: SLF001
            r["lote"]["devueltas"] += len(lote)
            r["lote"]["paginas"] = pagina
            if isinstance(res, dict) and res.get("totalItemNum") is not None:
                r["lote"]["total_temu"] = res.get("totalItemNum")
            for cruda in lote:
                e = str(pedidos_temu.estado_de(cruda))
                r["lote"]["estados"][e] = r["lote"]["estados"].get(e, 0) + 1
                sn = _sn_de(cruda)
                f = por_venta.get(sn)
                if not f or sn in atendidas:
                    continue
                r["lote"]["coincidencias"] += 1
                atendidas.add(sn)
                _visto[sn] = time.time()
                await _atender(f, cruda, r, simular, cancelar_odoo, siempre)
            if len(lote) < _LOTE_PAGINA:
                break
        if r["lote"]["devueltas"]:
            r["lote"]["filtro_honrado"] = set(r["lote"]["estados"]) == {"3"}

    # 2 · EL DETALLE, con reparto justo.
    restantes = [f for f in preguntables if f["order_id"] not in atendidas]
    elegidas = repartir(restantes, max(0, tope - r["llamadas"]), _visto)
    r["sin_consultar"] = len(restantes) - len(elegidas)
    seguidos = hechas = 0
    for f in elegidas:
        if time.monotonic() > limite_reloj:
            r["cortado_por_tiempo"] = True
            break
        if seguidos >= _FALLOS_SEGUIDOS_MAX:
            r["temu_caido"] = True
            break
        if r["llamadas"]:
            await asyncio.sleep(_PAUSA_S)
        r["llamadas"] += 1
        hechas += 1
        det = await pedidos_temu._traer(f["order_id"])  # noqa: SLF001 — nunca lanza
        if not isinstance(det, dict) or not det:
            seguidos += 1
            r["fallos_temu"] += 1
            continue
        seguidos = 0
        r["consultadas"] += 1
        _visto[f["order_id"]] = time.time()
        await _atender(f, det, r, simular, cancelar_odoo, siempre)
    # Las elegidas que no alcanzaron turno (Temu caído o reloj) tampoco se miraron.
    r["sin_consultar"] += len(elegidas) - hechas

    # 3 · LAS YA MARCADAS que todavía piden algo, y las parciales viejas: SÓLO
    #     Odoo, sin llamar a Temu. Corre con la bandera de Odoo apagada o
    #     encendida: apagada, sólo LEE Odoo y escribe la bitácora (saca del
    #     rojo lo que alguien ya resolvió a mano); encendida, además cancela el
    #     rezago de "por cancelar". Rota: primero las que llevan más sin mirarse.
    remirar = sorted((f for f in filas if f["tipo"] in TIPOS_REMIRAR),
                     key=lambda f: _visto_odoo.get(f["order_id"], 0.0))
    hechas_odoo = 0
    for f in remirar[:_ODOO_POR_VUELTA]:
        if time.monotonic() > limite_reloj:
            r["cortado_por_tiempo"] = True
            break
        hechas_odoo += 1
        await _remirar(f, r, simular, cancelar_odoo, siempre)
    r["remiradas"]["sin_mirar"] = len(remirar) - hechas_odoo


def _cerrar(r: dict[str, Any]) -> dict[str, Any]:
    r["ts"] = datetime.now(timezone.utc).isoformat()
    if r.get("simular"):
        _ultima_simulacion.clear()
        _ultima_simulacion.update(r)
    elif r.get("estado") not in ("en_curso",):
        _ultimo.clear()
        _ultimo.update(r)
    return r


async def vigilar(tope: int | None = None, solo: list[str] | None = None,
                  simular: bool = False,
                  como_si_cancelar_odoo: bool = False) -> dict[str, Any]:
    """
    Una vuelta del vigilante. La llama el scheduler cada
    `TEMU_CANCELACIONES_MIN`; a mano, `POST /api/automatizacion/temu/cancelaciones/vigilar`.

    `simular=True` LEE Temu y Odoo pero no escribe nada —ni bitácora ni Odoo— y
    dice qué haría con cada venta. `solo` acota a esas ventas (y las mira
    aunque estén en reposo). `como_si_cancelar_odoo=True` (SÓLO simulando)
    decide como si `TEMU_CANCELACIONES_CANCELAR_ODOO` estuviera encendida: es la
    vista previa del dale, rezago incluido. Nunca lanza.
    """
    r = _vacio(simular)
    if not simular and not _b("temu_cancelaciones_enabled", True):
        r.update(estado="apagado", error="TEMU_CANCELACIONES_ENABLED está apagada")
        return _cerrar(r)
    candado = _candado()
    if candado.locked():
        r.update(estado="en_curso", error="ya hay una vuelta corriendo")
        return _cerrar(r)
    try:
        async with candado:
            await _vuelta(r, tope, solo, simular, como_si_cancelar_odoo)
    except Exception as exc:  # noqa: BLE001 — la llama el scheduler
        log.exception("temu_cancelaciones.vigilar falló")
        r.update(estado="error", error=str(exc)[:300])
    hubo = (r["canceladas_detectadas"] or r["parciales"] or r["contradictorias"]
            or r["error"] or r["temu_caido"] or r["respuestas_ajenas"]
            or any(v for k, v in r["reintentos_odoo"].items()
                   if k not in ("por_cancelar", "revisar", "sin_rastro")))
    (log.warning if hubo else log.info)(
        "TEMU cancelaciones%s: %d vigiladas (%d esperan guía, %d con orden, %d ya "
        "marcadas por re-mirar en Odoo) · %d llamadas/%d · lote %s · %d consultadas · "
        "%d canceladas (%d sin orden, %d vinculadas, con orden %s) · re-miradas %s · "
        "%d parciales · %d sin consultar%s%s",
        " [SIMULACIÓN]" if simular else "", r["vigilables"],
        r["por_tipo"].get("espera", 0), r["por_tipo"].get("con_orden", 0),
        sum(r["por_tipo"].get(t, 0) for t in TIPOS_REMIRAR), r["llamadas"], r["tope"],
        (f"{r['lote']['devueltas']} ({'filtro OK' if r['lote']['filtro_honrado'] else 'filtro ignorado' if r['lote']['filtro_honrado'] is False else '-'})"
         if r["lote"]["pedido"] else "no"),
        r["consultadas"], r["canceladas_detectadas"], r["canceladas_sin_orden"],
        r["vinculadas"], {k: v for k, v in r["con_orden"].items() if v},
        {k: v for k, v in r["reintentos_odoo"].items() if v}, r["parciales"],
        r["sin_consultar"], " · TEMU CAÍDO" if r["temu_caido"] else "",
        f" · error: {r['error']}" if r["error"] else "")
    return _cerrar(r)


async def atender_vistas(vistas: list[Any], origen: str = "sondeo") -> dict[str, Any]:
    """
    Ventas que OTRO camino ya le leyó a Temu (el sondeo, el webhook): si alguna
    está en la vigilancia y viene cancelada —o con piezas canceladas— se atiende
    aquí mismo, sin gastar otra llamada. Es lo que hace "inmediata" la captura
    de las ventas que el sondeo ve pasar.

    `vistas` = dicts con la forma del detalle, o pares `(venta, detalle)` cuando
    el que llama ya sabe el id (si el detalle trae OTRA venta, no se decide
    nada: ver `_atender`). Respeta las mismas banderas que el vigilante. Sin
    candado a propósito: el sondeo no puede esperar a que termine una vuelta
    (hasta 4 min), y todo lo de aquí es idempotente y con guardas en el WHERE.
    Nunca lanza.
    """
    from services import odoo_ventas, odoo_ventas_log

    r = _vacio(False)
    r["origen"] = origen
    if not _b("temu_cancelaciones_enabled", True):
        r.update(estado="apagado")
        return _corto(r)
    try:
        filas = await asyncio.to_thread(
            odoo_ventas_log.vigilables_cancelacion, CANAL,
            odoo_ventas._dias_espera(),  # noqa: SLF001
            _i("temu_cancelaciones_dias", 14), ACCIONES_CON_ORDEN)
        por_venta = {f["order_id"]: f for f in filas if f["tipo"] in ("espera", "con_orden")}
        cancelar_odoo = _b("temu_cancelaciones_cancelar_odoo", False)
        for v in vistas or []:
            if isinstance(v, (tuple, list)) and len(v) == 2:
                sn, cruda = str(v[0]), v[1]
            else:
                sn, cruda = _sn_de(v or {}), v
            f = por_venta.get(sn)
            if not f or not isinstance(cruda, dict):
                r["sin_fila"] = r.get("sin_fila", 0) + 1
                continue
            _visto[sn] = time.time()
            r["consultadas"] += 1
            await _atender(f, cruda, r, False, cancelar_odoo)
    except Exception as exc:  # noqa: BLE001
        r.update(estado="error", error=str(exc)[:200])
        log.warning("temu_cancelaciones.atender_vistas(%s): %s", origen, str(exc)[:150])
    if r["canceladas_detectadas"] or r["parciales"]:
        log.warning("TEMU cancelaciones vistas por el %s: %d canceladas (%d sin orden, "
                    "%d vinculadas, con orden %s), %d parciales", origen,
                    r["canceladas_detectadas"], r["canceladas_sin_orden"], r["vinculadas"],
                    {k: v for k, v in r["con_orden"].items() if v}, r["parciales"])
    return _corto(r)


def _corto(r: dict[str, Any]) -> dict[str, Any]:
    """El resumen de `atender_vistas`: sólo lo que pasó."""
    return {k: r.get(k) for k in ("estado", "origen", "error", "consultadas", "sin_fila",
                                  "canceladas_detectadas", "canceladas_sin_orden",
                                  "vinculadas", "con_orden", "parciales", "contradictorias",
                                  "no_fiables", "respuestas_ajenas", "detalle")}
