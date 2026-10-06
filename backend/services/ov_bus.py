"""
ov_bus.py — El AVISO EN MEMORIA que hace que el chat de una orden de venta se vea
en vivo sin preguntarle a la base cada segundo (docs/ORDENES_VENTA.md §4.5).

QUÉ RESUELVE. El chat pide `GET /api/ordenes-venta/{id}/mensajes?esperar=25`. Si
no hay nada nuevo, la corrutina se queda ESPERANDO aquí; cualquier escritura
sobre esa orden llama a `avisar(id)` y la respuesta sale en milisegundos. Mientras
espera no ocupa ni un hilo ni una conexión del pool (que es de 6): son veinte
personas con el chat abierto contra cero conexiones.

LA BASE ES LA VERDAD; ESTO SÓLO ACORTA LA ESPERA. Aquí no viaja ningún mensaje:
viaja «algo cambió en la orden N», y quien despierta RELEE la base. Por eso
perder un aviso no pierde nada: lo peor que pasa es que el mensaje llegue cuando
vence la espera (≤ 25 s). Y por eso se persiste primero y se avisa después.

POR QUÉ NO ES OTRA COSA
  · SSE / EventSource / WebSocket: no pueden mandar la cabecera `Authorization`
    (o no pasan por el middleware de identidad), y desde que se encendió el
    enforcement todo lo que no va por `fetchSesion` es un 401.
  · LISTEN / NOTIFY de Postgres: el backend entra por el pooler en modo
    transacción (6543), donde la conexión de servidor se comparte entre clientes
    —la misma razón de la regla 13—. Un LISTEN ahí se queda escuchando en una
    conexión que mañana es de otro.
  · Redis u otro intermediario: producción corre UN uvicorn con UNA réplica. Un
    proceso se avisa a sí mismo con un diccionario.

EL LÍMITE, DICHO DE FRENTE: es de UN proceso. Durante el relevo de un deploy
puede haber dos contenedores unos segundos; quien espera en el viejo no ve el
aviso del nuevo y se entera al vencer su espera. Si algún día hay dos réplicas,
esto degrada a un sondeo de 25 s —sigue siendo correcto, sólo más lento— y
entonces sí toca un intermediario.

CÓMO NO SE PIERDE UN AVISO ENTRE LA LECTURA Y LA ESPERA. El router toma
`version(id)` ANTES de leer la base y se la pasa a `esperar()`: si entre la
lectura y la espera alguien avisó, la versión ya avanzó y `esperar` regresa de
inmediato. Las versiones salen de UN contador global que sólo sube, y una orden
sin entrada contesta el «piso» (la mayor versión que se haya limpiado): así
limpiar el diccionario nunca puede hacer que una versión vieja parezca vigente.
En el peor caso despierta a alguien de más, que relee y vuelve a esperar.

EL TOPE ES DOBLE. Uno global (`TOPE_ESPERAS`) y uno POR IDENTIDAD
(`TOPE_POR_ACTOR`): con sólo el global, una sola sesión —o una pestaña con un
bucle roto— podía ocupar las 300 esperas y mandar el chat de TODOS a sondeo.
Quien rebasa el suyo degrada él solo a sondeo; los demás siguen en vivo.

SI EL NAVEGADOR SE VA, NADIE CANCELA ESTA ESPERA POR SÍ SOLO. Ni uvicorn ni
Starlette cancelan el handler cuando el cliente cierra: el lugar seguiría
ocupado hasta vencer. Quien lo suelta es el router, que corre la espera en
carrera contra la desconexión y la CANCELA (el `finally` de `esperar` libera el
lugar; ver `routers/ordenes_venta.py::_esperar_o_irse`).

TODO CORRE EN EL EVENT LOOP: no hay candados porque no hay hilos. `avisar` se
llama desde una corrutina (el router después de escribir, el job del barrido).
Desde un hilo sería `loop.call_soon_threadsafe(ov_bus.avisar, id)`.
"""
from __future__ import annotations

import asyncio

# Esperas sostenidas a la vez en TODO el proceso. Cada una es una petición HTTP
# abierta; rebasado el tope, `esperar` contesta de inmediato y ese chat degrada
# a sondeo en vez de amontonar peticiones colgadas.
TOPE_ESPERAS = 300
# Esperas a la vez de UNA identidad (el correo de la sesión, «servicio» para la
# llave de API, «anonimo» sin credencial). Seis pestañas con el chat abierto son
# de sobra para una persona; un navegador no abre más conexiones por dominio.
TOPE_POR_ACTOR = 6
# A partir de cuántas órdenes con versión guardada se limpia el diccionario.
_LIMITE_VERSIONES = 2000

_seq = 0                                   # contador global: sólo sube
_piso = 0                                  # la mayor versión ya limpiada
_versiones: dict[int, int] = {}            # orden → versión de su último aviso
_eventos: dict[int, asyncio.Event] = {}    # orden → el evento que esperan AHORA
_esperas: dict[int, int] = {}              # orden → cuántos esperan
_por_actor: dict[str, int] = {}            # identidad → cuántas esperas sostiene
_total = 0                                 # esperas vivas en todo el proceso


def version(orden_id: int) -> int:
    """La versión actual de la orden. Se toma ANTES de leer la base."""
    return _versiones.get(int(orden_id), _piso)


def en_espera() -> int:
    """Cuántas peticiones están sostenidas ahora mismo (todas las órdenes)."""
    return _total


def lleno(actor: str = "") -> bool:
    """¿Ya no cabe otra espera (en el proceso, o de ese `actor`)? El router
    pregunta para no releer en balde."""
    return _total >= TOPE_ESPERAS or (
        bool(actor) and _por_actor.get(actor, 0) >= TOPE_POR_ACTOR)


def avisar(orden_id: int) -> None:
    """Algo cambió en la orden: despierta a quien la esté esperando. Desde el loop."""
    global _seq
    orden_id = int(orden_id)
    _seq += 1
    _versiones[orden_id] = _seq
    # Se SACA el evento además de encenderlo: los que esperaban despiertan con
    # él, y el siguiente que llegue estrena uno apagado. Así nunca hay que
    # «apagar» un evento que alguien todavía no terminó de leer.
    evento = _eventos.pop(orden_id, None)
    if evento is not None:
        evento.set()
    if len(_versiones) > _LIMITE_VERSIONES:
        _limpiar()


def _limpiar() -> None:
    """Olvida las versiones de las órdenes que nadie espera, para no crecer sin
    fin. El piso sube hasta la mayor versión olvidada: quien traiga una versión
    vieja de esas órdenes la verá distinta y releerá (nunca al revés)."""
    global _piso
    for orden_id in [o for o in _versiones if o not in _esperas]:
        _piso = max(_piso, _versiones.pop(orden_id))


async def esperar(orden_id: int, version_vista: int, segundos: float, actor: str = "") -> bool:
    """Espera hasta `segundos` a que alguien avise de la orden. True si hubo aviso
    (o si la versión ya había avanzado: regresa de inmediato); False si venció o
    si ya no cabe otra espera (en el proceso, o de ese `actor`). No ocupa hilo ni
    conexión mientras espera."""
    global _total
    orden_id = int(orden_id)
    if version(orden_id) != version_vista:
        return True
    if segundos <= 0 or lleno(actor):
        return False
    evento = _eventos.get(orden_id)
    if evento is None:
        evento = _eventos[orden_id] = asyncio.Event()
    _esperas[orden_id] = _esperas.get(orden_id, 0) + 1
    _total += 1
    if actor:
        _por_actor[actor] = _por_actor.get(actor, 0) + 1
    try:
        await asyncio.wait_for(evento.wait(), timeout=segundos)
        return True
    except asyncio.TimeoutError:
        # El aviso pudo llegar en el mismo instante en que venció.
        return version(orden_id) != version_vista
    finally:
        # También corre si la tarea se CANCELA: así suelta el router la espera
        # de un navegador que ya se fue (por sí sola no se cancela).
        _total -= 1
        if actor:
            suyas = _por_actor.get(actor, 1) - 1
            if suyas > 0:
                _por_actor[actor] = suyas
            else:
                _por_actor.pop(actor, None)
        quedan = _esperas.get(orden_id, 1) - 1
        if quedan > 0:
            _esperas[orden_id] = quedan
        else:
            _esperas.pop(orden_id, None)
            # Nadie más espera este evento: se suelta. Si `avisar` ya lo sacó,
            # el que está guardado es de otros y no se toca.
            if _eventos.get(orden_id) is evento:
                _eventos.pop(orden_id, None)


def _reiniciar() -> None:
    """Deja el bus como recién importado. Sólo para las pruebas."""
    global _seq, _piso, _total
    _seq = _piso = _total = 0
    _versiones.clear()
    _eventos.clear()
    _esperas.clear()
    _por_actor.clear()
