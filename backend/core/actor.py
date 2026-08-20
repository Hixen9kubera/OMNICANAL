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
