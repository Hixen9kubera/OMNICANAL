"""
fanout_fuentes.py — ¿Las dos fuentes del stock cuentan la misma historia?

POR QUÉ EXISTE
--------------
Dos variables deciden de dónde sale el stock DROP, y cada una tiene su propio candado
contra las erratas, pero nadie miraba si las DOS juntas tienen sentido:

  · `STOCK_WATCH_FUENTE` (`stock_watch.fuente`): qué vigila stock_watch para detectar
    cambios y encolarlos al fan-out — odoo | libro. (`woo`, el modo puente, salió del
    código con la ruta directa: para stock_watch hoy es un valor desconocido.)
  · `FANOUT_FUENTE` (`stock_libro.fuente_fanout`): qué número reparte el fan-out a los
    canales — woo | kubera (el libro).

En el plan de salida de Odoo y Woo (ruta directa, sí de Eduardo D3 del 20-oct) sólo
hay tres momentos, y cada uno es UNA combinación:

  (odoo,  woo)     HOY. stock_watch sigue en Odoo (la API se cortó el 15-oct: la
                   pasada aborta sin escribir) y el fan-out reparte el `_stock` de Woo,
                   que bajan las ventas.
  (libro, woo)     SOMBRA antes de T0. stock_watch sólo REGISTRA el vendible del
                   libro (`vendible_registro`, no encola) y el fan-out sigue en Woo.
  (libro, kubera)  T0. stock_watch encola los cambios del libro (`vendible_cambio`) y
                   el fan-out reparte el libro.

T0 sólo es T0 si stock_watch de verdad ENCOLA: con (libro, kubera) y
`STOCK_WATCH_SOLO_REGISTRO` encendida (así nace la variable) la pasada sólo registra
(`vendible_registro`), y con `STOCK_WATCH_ENABLED` apagada no hay pasada. En los dos
casos las entradas, los ajustes y los SKUs que salen del libro (a 0) nunca llegan a los
canales: sólo las ventas se reparten. Es el momento `t0_sin_encolar`, NO coherente
(CRITICAL y aviso), aunque stock_watch sí se registre —registrar no estorba—.

Cualquier otra es un error de configuración:

  (odoo, kubera)   el fan-out reparte el libro, pero stock_watch vigila Odoo
                   (congelado desde el 15-oct): ningún cambio del libro llegaría a los
                   canales salvo con una venta, y stock_watch movería Woo con deltas
                   de un Odoo muerto.
  woo en STOCK_WATCH_FUENTE  el modo puente quedó descartado por la ruta directa
                   (20-oct): nunca se desplegó y ya no es un paso del plan.
  una errata en cualquiera de las dos.

QUÉ HACE
--------
`validar` dice si una combinación es coherente y por qué. Al arrancar, el scheduler
pregunta aquí (`al_arrancar`) ANTES de registrar stock_watch. Con una combinación
incoherente deja un CRITICAL en el log y avisa por Slack (`avisar`, tipo
`fanout_fuentes`, por cambio de estado), y:
  · con una `STOCK_WATCH_FUENTE` que stock_watch CONOCE (odoo, libro) y una
    `FANOUT_FUENTE` que no casa con ella → NO registra stock_watch: su pasada correría;
  · con una `STOCK_WATCH_FUENTE` que stock_watch NO conoce (`woo` incluido, o una
    errata) → SÍ lo registra: su pasada se NIEGA sola en cada vuelta y lo dice en su
    estado y por Slack (`stock_watch._fuente_sin_construir`, contrato de WOO SALIDA
    fijado en `test_stock_watch_fuente`). Frenarlo aquí escondería esa negativa.
El fan-out por ventas SIGUE: lee lo que diga `FANOUT_FUENTE` con su propio candado
(`stock_libro.fuente_fanout` se niega ante una errata). `estado()` deja el resultado a
la vista del Dashboard.

Sin red ni base salvo `avisar` (que el scheduler corre en un hilo: regla 11).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.fanout_fuentes")

# Los valores por omisión de cada lector: una variable vacía vale lo mismo aquí que allá.
_SW_DEFECTO = "odoo"     # = stock_watch.fuente()
_FF_DEFECTO = "woo"      # = stock_libro.fuente_fanout()
_SW_VALIDAS = ("odoo", "libro")
_FF_VALIDAS = ("woo", "kubera")

COHERENTES: dict[tuple[str, str], tuple[str, str]] = {
    ("odoo", "woo"): ("hoy", "stock_watch vigila Odoo y el fan-out reparte el _stock de Woo"),
    ("libro", "woo"): ("sombra", "antes de T0: stock_watch sólo registra el vendible del libro y "
                                 "el fan-out sigue repartiendo Woo"),
    ("libro", "kubera"): ("t0", "T0: stock_watch encola los cambios del libro y el fan-out "
                                "reparte el libro"),
}

# T0 con stock_watch sin encolar (ver arriba): las dos fuentes casan, pero el libro no llega.
T0_SIN_ENCOLAR = "t0_sin_encolar"

AVISO_TIPO = "fanout_fuentes"

_estado: dict[str, Any] = {}


def _norm(valor: Any, defecto: str) -> str:
    return str(valor or "").strip().lower() or defecto


def _sin_encolar(sw: str, ff: str, solo_registro: bool, encendido: bool) -> bool:
    """¿Es (libro, kubera) con stock_watch que no encola (sólo registra, o apagado)?"""
    return (sw, ff) == ("libro", "kubera") and (solo_registro or not encendido)


def validar(stock_watch_fuente: Any, fanout_fuente: Any, *, solo_registro: bool = False,
            stock_watch_encendido: bool = True) -> tuple[bool, str]:
    """(ok, motivo) de la combinación. Vacío = el valor por omisión de cada lector
    (odoo y woo), igual que ellos. `solo_registro` / `stock_watch_encendido`: cómo está
    stock_watch (`STOCK_WATCH_SOLO_REGISTRO`, `STOCK_WATCH_ENABLED`); sólo cuentan en T0
    (ver `T0_SIN_ENCOLAR`). Nunca lanza."""
    sw, ff = _norm(stock_watch_fuente, _SW_DEFECTO), _norm(fanout_fuente, _FF_DEFECTO)
    cabeza = f"STOCK_WATCH_FUENTE={sw} + FANOUT_FUENTE={ff}"
    if _sin_encolar(sw, ff, solo_registro, stock_watch_encendido):
        por = ("STOCK_WATCH_ENABLED está apagada: no hay pasada" if not stock_watch_encendido
               else "STOCK_WATCH_SOLO_REGISTRO está encendida: la pasada sólo registra "
                    "(vendible_registro) y no encola")
        return False, (f"{cabeza}: T0 sin encolar — {por}. Las entradas, los ajustes y los SKUs "
                       "que salen del libro (a 0) no llegan a los canales; sólo las ventas se "
                       "reparten. Para T0: STOCK_WATCH_ENABLED=true y STOCK_WATCH_SOLO_REGISTRO=false")
    if (sw, ff) in COHERENTES:
        return True, f"{cabeza}: {COHERENTES[(sw, ff)][1]}"
    faltas: list[str] = []
    if sw == "woo":
        faltas.append("el modo puente (STOCK_WATCH_FUENTE=woo) quedó descartado por la ruta "
                      "directa del 20-oct: stock_watch vigila odoo (hoy) o libro (sombra y T0)")
    elif sw not in _SW_VALIDAS:
        faltas.append(f"STOCK_WATCH_FUENTE={sw!r} no existe (odoo | libro)")
    if ff not in _FF_VALIDAS:
        faltas.append(f"FANOUT_FUENTE={ff!r} no existe (woo | kubera)")
    if not faltas:      # las dos existen y no casan: (odoo, kubera)
        faltas.append("el fan-out reparte el libro y stock_watch vigila Odoo, congelado desde el "
                      "15-oct: ningún cambio del libro llegaría a los canales. Con el libro, "
                      "STOCK_WATCH_FUENTE=libro (o FANOUT_FUENTE=woo para volver)")
    return False, f"{cabeza}: {'; '.join(faltas)}"


def momento(stock_watch_fuente: Any, fanout_fuente: Any, *, solo_registro: bool = False,
            stock_watch_encendido: bool = True) -> str | None:
    """hoy | sombra | t0 | t0_sin_encolar, o None si la combinación no es coherente."""
    clave = (_norm(stock_watch_fuente, _SW_DEFECTO), _norm(fanout_fuente, _FF_DEFECTO))
    if _sin_encolar(*clave, solo_registro, stock_watch_encendido):
        return T0_SIN_ENCOLAR
    return COHERENTES[clave][0] if clave in COHERENTES else None


def _actuales() -> tuple[Any, Any]:
    return getattr(settings, "stock_watch_fuente", ""), getattr(settings, "fanout_fuente", "")


def _stock_watch_actual() -> dict[str, bool]:
    """Cómo está stock_watch hoy (las mismas variables que leen `stock_watch.solo_registro`
    y `stock_watch.habilitado`, con sus mismos valores por omisión)."""
    return {"solo_registro": bool(getattr(settings, "stock_watch_solo_registro", True)),
            "stock_watch_encendido": bool(getattr(settings, "stock_watch_enabled", False))}


def registrable(stock_watch_fuente: Any, fanout_fuente: Any) -> bool:
    """¿Puede registrarse el job de stock_watch con esta combinación? Sí si es coherente,
    y también si stock_watch NO conoce su fuente (`woo` o una errata): entonces su
    pasada se niega sola en cada vuelta (contrato de WOO SALIDA). No, si stock_watch
    conoce su fuente y `FANOUT_FUENTE` no casa con ella: su pasada correría."""
    if validar(stock_watch_fuente, fanout_fuente)[0]:
        return True
    return _norm(stock_watch_fuente, _SW_DEFECTO) not in _SW_VALIDAS


def al_arrancar(stock_watch_quiere_job: bool) -> bool:
    """Lo llama `scheduler.iniciar()` antes de registrar stock_watch. True = el job se
    puede registrar (`registrable`: se registra si stock_watch lo pide). Con una
    combinación incoherente y stock_watch encendido: CRITICAL siempre; False (el job NO
    se registra) salvo que stock_watch no conozca su fuente (se registra y su pasada se
    niega sola). Apagado, sólo un WARNING: no hay job que frenar, pero el Dashboard lo
    muestra igual. Sin red ni base."""
    sw, ff = _actuales()
    ok, motivo = validar(sw, ff, **_stock_watch_actual())
    registra = registrable(sw, ff)
    sin_encolar = momento(sw, ff, **_stock_watch_actual()) == T0_SIN_ENCOLAR
    if not stock_watch_quiere_job:
        job = "apagado"
    elif sin_encolar:
        job = "registrado: sólo registra, no encola (STOCK_WATCH_SOLO_REGISTRO)"
    elif ok:
        job = "registrado"
    elif registra:
        job = "registrado: su pasada se niega (STOCK_WATCH_FUENTE desconocida)"
    else:
        job = "NO registrado: fuentes incoherentes"
    _estado.clear()
    # T0 sin encolar avisa aunque stock_watch esté apagado: justo ése es el problema.
    _estado.update(ok=ok, motivo=motivo, momento=momento(sw, ff, **_stock_watch_actual()),
                   stock_watch_fuente=_norm(sw, _SW_DEFECTO), fanout_fuente=_norm(ff, _FF_DEFECTO),
                   stock_watch_job=job, registra=registra, avisa=stock_watch_quiere_job or sin_encolar,
                   validado_en=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if ok:
        log.info("Fuentes del stock coherentes: %s", motivo)
    elif sin_encolar:
        log.critical("T0 SIN ENCOLAR — el fan-out reparte el libro pero stock_watch no encola sus "
                     "cambios (stock_watch: %s). %s", job, motivo)
    elif stock_watch_quiere_job and registra:
        log.critical("FUENTES DEL STOCK INCOHERENTES — stock_watch se registra, pero su pasada se "
                     "NIEGA con una STOCK_WATCH_FUENTE que no conoce: no vigila nada (el fan-out "
                     "por ventas sigue, con su propio candado). %s", motivo)
    elif stock_watch_quiere_job:
        log.critical("FUENTES DEL STOCK INCOHERENTES — stock_watch NO se registra (el fan-out por "
                     "ventas sigue, con su propio candado). %s", motivo)
    else:
        log.warning("Fuentes del stock incoherentes (stock_watch está apagado): %s", motivo)
    return registra


def avisar() -> bool:
    """El aviso por Slack de lo que vio `al_arrancar` (por cambio de estado: un despliegue
    con la misma falla no repite el aviso; uno coherente anuncia la recuperación). Sólo
    si stock_watch quería correr. SÍNCRONA: el scheduler la corre con `asyncio.to_thread`
    (lee y escribe la tabla del candado de alertas)."""
    if not _estado or not _estado.get("avisa"):
        return False
    from services import alertas
    if _estado["ok"]:
        return alertas.avisar_estado(
            AVISO_TIPO, "ok", "", texto_ok=f"*Fuentes del stock*: coherentes otra vez — {_estado['motivo']}")
    if _estado.get("momento") == T0_SIN_ENCOLAR:
        job = "el fan-out reparte el libro, pero stock_watch no encola sus cambios"
    elif _estado.get("registra"):
        job = "stock_watch se registró, pero su pasada se niega con esa fuente: no vigila nada"
    else:
        job = "stock_watch NO se registró al arrancar"
    return alertas.avisar_estado(
        AVISO_TIPO, "incoherente",
        f"*Fuentes del stock incoherentes*: {job} (el fan-out por ventas sigue). {_estado['motivo']}")


def estado() -> dict[str, Any]:
    """El resultado de la validación para el Dashboard: el del arranque si ya pasó por
    el scheduler; si no, el de la configuración actual (`validado_en` None)."""
    if _estado:
        return dict(_estado)
    sw, ff = _actuales()
    ok, motivo = validar(sw, ff, **_stock_watch_actual())
    return {"ok": ok, "motivo": motivo, "momento": momento(sw, ff, **_stock_watch_actual()),
            "stock_watch_fuente": _norm(sw, _SW_DEFECTO), "fanout_fuente": _norm(ff, _FF_DEFECTO),
            "stock_watch_job": None, "registra": registrable(sw, ff), "avisa": False,
            "validado_en": None}
