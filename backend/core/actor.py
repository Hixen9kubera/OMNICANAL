"""
actor.py — Quién está haciendo esto, disponible en cualquier capa sin pasarlo
por parámetro.

POR QUÉ EXISTE
--------------
La base YA pregunta quién fue. El trigger de `costing.cost_history` guarda
`current_setting('app.usuario')`, y el docstring de `Identidad.actor` dice
literalmente "lo que va a la bitácora de auditoría". Lo que faltaba era el cable
entre los dos: hoy `cambiado_por` solo dice "backend" o nada, nunca una persona.

El problema de pasarlo por parámetro es que el actor tendría que atravesar
routers, servicios y ayudantes hasta llegar al cursor — decenas de firmas
tocadas para cargar un dato que no le importa a ninguna de ellas. Un ContextVar
lo lleva por debajo: el middleware lo deja al entrar, `supabase_db.get_cursor`
lo recoge al escribir, y nadie en medio se entera.

LA TRAMPA QUE HACE FALTA CONOCER: `run_in_executor` PIERDE EL CONTEXTO
----------------------------------------------------------------------
`asyncio.to_thread` copia el contexto al hilo — 39 archivos lo usan y ahí no hay
problema. Pero `loop.run_in_executor(None, fn)` **no lo copia**, y justo los
caminos que más importan para la auditoría van por ahí: `costing_write`,
`costing_mirror`, `crear_producto`, `orders_write`, `channel_mirror` y
`publicacion_seam`.

Sin `en_hilo()`, el ContextVar saldría VACÍO exactamente en las escrituras que
queremos atribuir, y la bitácora se vería funcionando mientras deja en blanco lo
único que importa. Es peor que no tenerla: una bitácora en la que no se puede
confiar se consulta igual, y se le cree.

Por eso `en_hilo()` vive aquí y no en cada servicio: es la versión que sí se
lleva el contexto, y es la única que se debe usar para trabajo de fondo que
escriba en la base.

QUÉ NO HACE
-----------
No identifica: eso es de `core/identidad.py`. Aquí solo se guarda el nombre ya
resuelto y se le da un aventón hasta el cursor.

Tampoco cubre a quien entra DIRECTO a la base (por el dashboard de Supabase o
con psql). Eso no lo puede ver ningún código nuestro; se cubre con el modelo de
roles, que es otra conversación.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Callable

log = logging.getLogger("omnicanal.actor")

# Vacío = nadie lo fijó (cron, sondeo, arranque). NO es un error: significa que
# no hubo una petición detrás, y la bitácora lo va a dejar nulo igual que hoy.
_actor: contextvars.ContextVar[str] = contextvars.ContextVar(
    "omnicanal_actor", default="")

# `app.usuario` es text y no tiene límite, pero un valor absurdo solo puede venir
# de un encabezado manipulado. Se recorta y ya.
_TOPE = 200


def fijar(nombre: str) -> None:
    """Deja el nombre de quien pide, para esta petición y lo que cuelgue de ella."""
    _actor.set((nombre or "").strip()[:_TOPE])


def actual() -> str:
    """Quién pide, o cadena vacía si nadie lo fijó."""
    return _actor.get()


def limpiar() -> None:
    """Vuelve a 'nadie'. Útil en pruebas y en trabajos de fondo de larga vida."""
    _actor.set("")


def capturar() -> contextvars.Context:
    """
    Foto del contexto actual, para cruzar una COLA hacia un hilo que YA EXISTÍA.

    `en_hilo()` no sirve cuando el trabajo no se lanza: se ENCOLA, y lo recoge un
    hilo daemon que arrancó mucho antes de que existiera esta petición. Ese hilo
    nunca hereda nada — se quedó con el contexto vacío del arranque.

    Es el caso de `kubera_mirror`, y es por donde pasan las creaciones de
    producto. Medido: sin esto el hilo de la cola lee cadena vacía, así que las
    altas quedarían sin firmar justo en la tabla que existe para saber quién las
    hizo.

    Se usa en pareja: `actor.capturar()` al encolar, y `ctx.run(fn, *args)` en el
    worker. La foto se toma AQUÍ, donde el actor todavía existe.
    """
    return contextvars.copy_context()


# ═══════════════════════════════════════════════════════════════════════════
# LOS SCRIPTS: quién corre algo que no tiene sesión
# ═══════════════════════════════════════════════════════════════════════════
#
# POR QUÉ HIZO FALTA. Un script no pasa por el middleware, así que el ContextVar
# nace vacío y todo lo que escriba queda sin firmar. No es hipotético: 307 altas
# de Temu, 127 de Walmart y ~2,000 de TikTok se publicaron desde scripts, y en
# `ops.channel_submissions` no hay forma de saber quién las hizo.
#
# EL MECANISMO ELEGIDO, y por qué éste y no otro. El criterio no fue cuál es más
# cómodo: fue **cuál es más difícil de saltarse SIN QUERER**.
#
#   · Variable de entorno sola      → se olvida, y falla en silencio. Descartada:
#                                     el fallo silencioso es justo lo que estamos
#                                     tratando de eliminar.
#   · Usuario de git                → dice quién configuró la máquina, no quién
#                                     corrió el script. En una compartida, miente.
#   · Preguntarlo al arrancar       → rompe cualquier corrida no interactiva.
#   · `--como` OBLIGATORIO          → ELEGIDA. Si falta, el script ABORTA antes
#                                     de tocar nada, con un mensaje que dice qué
#                                     escribir.
#
# LA CONCESIÓN QUE LO HACE VIABLE: un cron legítimo no debe quedar bloqueado, así
# que `--como automatico` es una respuesta VÁLIDA — y deja el actor VACÍO a
# propósito, que en la base es NULL. Eso es la verdad: no hubo una persona.
#
# Se aceptan las dos, pero **no se acepta el silencio**. La diferencia entre "lo
# hizo Andrea", "lo hizo una máquina" y "no lo sabemos" es la razón de ser de
# toda la pestaña de Monitoreo; dejar que un olvido produzca la tercera cuando la
# respuesta era una de las dos primeras es el error que esto viene a impedir.

AUTOMATICO = "automatico"

_AYUDA = (
    "Quién corre esto: tu correo (p. ej. andrea.pardo@kubera.mx), o "
    f"'{AUTOMATICO}' si es un cron. También se puede dejar en la variable de "
    "entorno OMNICANAL_ACTOR."
)


def agregar_argumento(parser) -> None:
    """Le pone `--como` a un `argparse.ArgumentParser` de un script."""
    parser.add_argument("--como", default=None, metavar="QUIEN", help=_AYUDA)


def fijar_desde_cli(valor: str | None) -> str:
    """
    Fija el actor de un script. **Aborta si no se declaró.**

    Devuelve el actor efectivo ('' cuando es automático, que en la base es NULL).
    El orden es: lo que venga en `--como`, y si no, `OMNICANAL_ACTOR`.

    No se valida contra `core.usuarios` a propósito: obligaría a abrir la base
    antes de saber si el script puede correr, y un fallo de red se volvería "no
    puedes publicar". Se comprueba la FORMA y se avisa de lo raro; el nombre real
    lo audita después quien lea la bitácora.
    """
    import os
    import sys

    quien = (valor or os.environ.get("OMNICANAL_ACTOR") or "").strip()

    if not quien:
        print(
            "\n  FALTA DECIR QUIÉN CORRE ESTO.\n\n"
            "  Este script escribe en la bitácora, y una fila sin dueño no se\n"
            "  puede auditar después. Agrega:\n\n"
            "      --como tu.correo@kubera.mx\n"
            f"      --como {AUTOMATICO}      (si lo dispara un cron, no una persona)\n",
            file=sys.stderr)
        raise SystemExit(2)

    if quien.lower() == AUTOMATICO:
        # Vacío = NULL en la base. Es la verdad: no hubo persona detrás.
        fijar("")
        log.info("actor: automático (la bitácora quedará sin persona, a propósito)")
        return ""

    if "@" not in quien or quien.startswith("@") or quien.endswith("@"):
        print(f"\n  '{quien}' no parece un correo. Usa tu correo de Kubera "
              f"o '{AUTOMATICO}'.\n", file=sys.stderr)
        raise SystemExit(2)
    if not quien.lower().endswith("@kubera.mx"):
        # Se avisa y se sigue: bloquear aquí sería inventar una regla de negocio
        # dentro de un ayudante de scripts.
        log.warning("el actor '%s' no es de @kubera.mx — se registra igual", quien)

    fijar(quien)
    log.info("actor: %s", quien)
    return quien


def en_hilo(fn: Callable, *args) -> None:
    """
    `run_in_executor` que SÍ se lleva el contexto (y con él, el actor).

    Reemplaza al patrón `loop.run_in_executor(None, fn, *args)` que estaba
    duplicado en seis servicios. Misma semántica —dispara y olvida, y si no hay
    event loop corre directo—, con la única diferencia de que el hilo recibe una
    copia del contexto actual.

    `copy_context()` se toma AQUÍ, en el hilo que llama, que es donde el actor
    todavía existe. Tomarlo dentro del hilo sería tarde.
    """
    ctx = contextvars.copy_context()
    try:
        asyncio.get_running_loop().run_in_executor(None, lambda: ctx.run(fn, *args))
    except RuntimeError:  # sin loop (contexto síncrono puro): directo
        fn(*args)
