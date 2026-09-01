"""
bitacora.py — Quién hizo qué, cuándo, y sobre qué SKU.

POR QUÉ EXISTE
--------------
`ops.channel_submissions` guarda 26,104 publicaciones y NO tiene columna de
usuario: sabemos que un SKU se publicó en BEKURA a las 14:32, no si fue Thalía
o Andrea. Y los escritos a `ops.process_log` estaban dispersos — cada servicio
armaba su propio INSERT.

Esto es el único lugar por donde se anota una acción de PERSONA. Tres cosas se
registran, por decisión de Brandon (1-sep-2026): **publicar, editar precio y
cambiar stock**. La mejora con IA NO se registra: gasta créditos pero no cambia
lo que el comprador ve ni lo que se le cobra.

Sirve para las dos cosas a la vez —productividad y auditoría—, y por eso se
anotan también los INTENTOS FALLIDOS: para medir cuánto hizo alguien basta con
los éxitos, pero para auditar hace falta saber qué se intentó.

TRES REGLAS QUE NO SON CAPRICHO
-------------------------------
1. **Nunca revienta la operación.** Una bitácora que tumba una publicación es
   peor que no tenerla. Todo va dentro de un try y el fallo solo deja un warning.

2. **No bloquea el event loop.** `supabase_db.execute` es psycopg2 y detiene el
   backend ENTERO mientras responde (regla 11 de CLAUDE.md, costó el apagón del
   13-ago). Va por `actor.en_hilo()`, que además —a diferencia de
   `run_in_executor` pelado— SE LLEVA EL CONTEXTO, y con él el actor. Sin eso la
   fila saldría con el usuario en blanco justo en la tabla que existe para
   saber quién fue.

3. **El actor sale del contexto, no de un parámetro.** El middleware ya lo fijó
   al entrar la petición (`core/actor.py`). Pedirlo por argumento obligaría a
   atravesar decenas de firmas con un dato que a ninguna le importa.

QUÉ NO HACE
-----------
No registra procesos automáticos: para eso ya están el sondeo, el fan-out y sus
propias bitácoras. Si `actor.actual()` viene vacío es que no hubo una persona
detrás, y la fila se anota igual con el actor nulo — que es la verdad.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core import actor as core_actor
from services import supabase_db

log = logging.getLogger("omnicanal.bitacora")

# Lo que se registra. Se declara aquí para que un `proceso` mal escrito se note
# al leer el código y no al consultar la pestaña y encontrarla incompleta.
PUBLICAR = "publicar"
PRECIO = "precio"
STOCK = "stock"
# `costos` ya existia antes de este modulo: lo escriben crear_producto y el
# recalculo del panel. Se reusa el mismo nombre para que la pestana muestre
# el historico junto con lo nuevo, en vez de partir la vista en dos.
COSTO = "costos"


def _escribir(proceso: str, origen: str, accion: str, sku: str | None,
              estado: str, detalle: str | None, duracion_s: float | None,
              quien: str) -> None:
    """El INSERT. Corre en un hilo, nunca en el event loop."""
    try:
        supabase_db.execute(
            "INSERT INTO ops.process_log "
            "  (proceso, origen, sku, accion, estado, detalle, duracion_s, actor) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
            (proceso, origen, sku, accion, estado, detalle, duracion_s,
             quien or None),
        )
    except Exception as exc:  # noqa: BLE001 — ver regla 1
        log.warning("no se pudo anotar en la bitácora (%s/%s sku=%s): %s",
                    proceso, accion, sku, exc)


def anotar(proceso: str, accion: str, *, sku: str | None = None,
           estado: str = "ok", canal: str | None = None,
           cuenta: str | None = None, detalle: dict[str, Any] | None = None,
           duracion_s: float | None = None, origen: str = "panel") -> None:
    """
    Deja constancia de una acción de persona. Dispara y olvida.

        bitacora.anotar(bitacora.PUBLICAR, "confirmar", sku="ACC-0160-AZL",
                        canal="mercado_libre", cuenta="BEKURA")

    `canal` y `cuenta` entran al `detalle` en vez de a columnas propias: la
    tabla ya existe con su forma y agregarle columnas obligaría a una migración
    para un dato que solo se consulta agrupado.

    ⚠️ `estado` distingue el éxito del intento. Anotar solo lo que salió bien
    sirve para contar productividad y NO sirve para auditar — que es la mitad
    del encargo.
    """
    # ⚠️ TODO el armado va dentro del try, no solo el INSERT. El 1-sep esta
    # funcion era segura por dentro y aun asi TUMBO CUATRO PUBLICACIONES REALES:
    # el call site pasaba `req.precio_regular`, un campo que no existe en ese
    # modelo, y el AttributeError salta ANTES de entrar aqui. La publicacion ya
    # habia funcionado; el usuario vio "error" igual.
    #
    # De ahi dos reglas, y la segunda es la que faltaba:
    #   · el helper no revienta  ← ya estaba
    #   · NI SIQUIERA armar sus argumentos puede reventar  ← esto es nuevo
    # Por eso el call site tambien envuelve, y `serializar` tolera cualquier cosa.
    try:
        cuerpo = dict(detalle or {})
        if canal:
            cuerpo["canal"] = canal
        if cuenta:
            cuerpo["cuenta"] = cuenta
    except Exception as exc:  # noqa: BLE001
        log.warning("detalle ilegible en la bitácora (%s/%s): %s", proceso, accion, exc)
        cuerpo = {}
    core_actor.en_hilo(
        _escribir, proceso, origen, accion, sku, estado,
        json.dumps(cuerpo, ensure_ascii=False, default=str) if cuerpo else None,
        duracion_s, core_actor.actual(),
    )
