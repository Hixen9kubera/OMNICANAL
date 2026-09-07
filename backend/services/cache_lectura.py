"""
cache_lectura.py — Un caché en memoria para las lecturas CARAS del panel.

═══════════════════════════════════════════════════════════════════════════════
POR QUÉ EXISTE — el problema medido, no el sospechado
═══════════════════════════════════════════════════════════════════════════════
La pestaña de Análisis tardaba 8-13 s en abrir. Medido contra producción el
7-sep-2026, descartando una por una las causas fáciles:

    round-trip vacío (GET /) .................. 0.17 s   → no es la red
    el SQL de /tabla, medido directo ..... 1.4-1.7 s   → no es la consulta
    el endpoint /tabla completo .............. 8-13 s
    con las llamadas a ML apagadas ............. 7.7 s   → solo 1.2 s eran ML
    pings a / MIENTRAS /tabla corre ........... 0.18 s   → el loop está SANO
    cinco /tabla en paralelo .............. 21 s c/u   → se estorban entre sí

La consulta tarda 1.7 s y el endpoint 9: la diferencia NO está en el SQL ni en
el event loop. Está en la CAPACIDAD para trabajo bloqueante: el pool tiene 6
conexiones (`supabase_db._get_pool`), las consultas salen por `asyncio.to_thread`
—que en este contenedor son ~6 hilos— y el backend procesa webhooks de ML sin
pausa, cada uno con sus propias escrituras. `/tabla` hace TRES consultas y
compite por esos seis carriles contra todo lo demás.

De ahí que cinco lecturas simultáneas no tarden 9 s cada una sino 21: no hay
dónde correrlas.

Por eso el caché ataca la causa y no solo el síntoma: **cada lectura servida de
memoria es una que no toca el pool**, así que acelera la pestaña Y descongestiona
los webhooks, el sync y todo lo demás que pelea por el mismo cuello.

═══════════════════════════════════════════════════════════════════════════════
POR QUÉ 2 MINUTOS
═══════════════════════════════════════════════════════════════════════════════
Los datos que pinta Análisis vienen del sync de 15 minutos y de los webhooks.
Guardar la respuesta 120 s no inventa antigüedad que no existiera ya: el número
más fresco posible tiene, en promedio, varios minutos. Lo que sí evita es que
tres personas mirando la misma pestaña paguen tres veces la misma consulta.

Y como la antigüedad SE MUESTRA (`_cache.edad_s` viaja en la respuesta), no hay
forma de que alguien lea un dato viejo creyéndolo de ahora — que es el único
daño real de un caché.

═══════════════════════════════════════════════════════════════════════════════
LO QUE NO SE CACHEA, Y POR QUÉ IMPORTA
═══════════════════════════════════════════════════════════════════════════════
Los errores no entran: un 502 guardado dos minutos convierte un tropiezo en una
caída. Como `producir()` propaga la excepción, eso sale gratis — nunca se llega
a `guardar`.

Y quien llama puede afinar más con `cachear_si`. Ojo con esa puerta: el primer
intento de `/tabla` la usó para no guardar mientras `envios_pendientes > 0`,
creyendo que era "la respuesta sigue armándose". No lo era —es el rezago de
piezas con envío estimado, 6,924 en la medición— así que la condición era
siempre falsa y el caché no habría entrado NUNCA. Una guarda que nunca deja
guardar no se nota: el caché simplemente no sirve, sin un solo error. Si se usa
`cachear_si`, hay que medir cuántas veces dice que sí.

El caché es POR PROCESO. Si mañana hay dos instancias, cada una tendrá la suya y
lo peor que pasa es que una sirva 2 minutos más vieja que la otra — para este
uso es aceptable, y decirlo aquí evita que alguien lo descubra depurando.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

# Cuánto vive una respuesta. 120 s por lo de arriba; se puede bajar sin tocar a
# los llamadores.
TTL_S = 120

# Techo de entradas. Cada combinación de filtros es una llave distinta y la
# pestaña tiene muchos (días × cuenta × estado × tipo × tam × búsqueda × orden ×
# página), así que sin tope esto crece sin freno en un proceso de larga vida.
# Al llenarse se tira lo más viejo: es un caché, no un almacén.
_MAX_ENTRADAS = 200

_datos: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def _podar(ahora: float) -> None:
    """Quita lo caducado y, si aún sobra, lo más antiguo. Con el lock tomado."""
    muertas = [k for k, (t, _) in _datos.items() if ahora - t > TTL_S]
    for k in muertas:
        _datos.pop(k, None)
    if len(_datos) > _MAX_ENTRADAS:
        for k, _ in sorted(_datos.items(), key=lambda kv: kv[1][0])[
                :len(_datos) - _MAX_ENTRADAS]:
            _datos.pop(k, None)


def llave(nombre: str, params: dict[str, Any]) -> str:
    """Una llave estable a partir del nombre del endpoint y sus parámetros.

    Se ordenan las claves para que el mismo filtro escrito en otro orden dé la
    MISMA entrada; si no, el caché tendría una copia por permutación y casi
    nunca acertaría.
    """
    partes = "|".join(f"{k}={params[k]!r}" for k in sorted(params))
    return f"{nombre}?{partes}"


def leer(k: str) -> tuple[Any, int] | None:
    """El valor guardado y su edad en segundos, o None si no hay o ya caducó."""
    ahora = time.monotonic()
    with _lock:
        v = _datos.get(k)
        if v is None:
            return None
        t, dato = v
        if ahora - t > TTL_S:
            _datos.pop(k, None)
            return None
        return dato, int(ahora - t)


def guardar(k: str, dato: Any) -> None:
    ahora = time.monotonic()
    with _lock:
        _datos[k] = (ahora, dato)
        _podar(ahora)


def invalidar(prefijo: str = "") -> int:
    """Tira lo guardado (todo, o lo que empiece con `prefijo`). Devuelve cuántas."""
    with _lock:
        victimas = [k for k in _datos if k.startswith(prefijo)]
        for k in victimas:
            _datos.pop(k, None)
        return len(victimas)


async def con_cache(
    nombre: str,
    params: dict[str, Any],
    producir: Callable[[], Any],
    *,
    refrescar: bool = False,
    cachear_si: Callable[[Any], bool] | None = None,
) -> Any:
    """Devuelve del caché o llama a `producir()` y guarda.

    `producir` es una corrutina sin argumentos (la llamada real al endpoint).

    `refrescar=True` SALTA la lectura pero SÍ guarda: es el botón "actualizar",
    que tiene que traer lo de ahora y además dejar el caché tibio para quien
    entre después. Saltar también el guardado haría que el botón beneficiara
    solo a quien lo aprieta.

    `cachear_si` decide si la respuesta merece guardarse — ver el encabezado:
    una foto a medio llenar no entra.

    La respuesta se devuelve TAL CUAL. Quien llama le pega su `_cache`; hacerlo
    aquí obligaría a asumir que siempre es un dict, y no todos los endpoints lo
    son.
    """
    k = llave(nombre, params)
    if not refrescar:
        hit = leer(k)
        if hit is not None:
            return hit
    dato = await producir()
    if cachear_si is None or cachear_si(dato):
        guardar(k, dato)
    return dato, 0


def estado() -> dict[str, Any]:
    """Para diagnóstico: cuántas entradas hay y de qué edad."""
    ahora = time.monotonic()
    with _lock:
        edades = sorted(int(ahora - t) for t, _ in _datos.values())
    return {"entradas": len(edades), "ttl_s": TTL_S,
            "edad_min_s": edades[0] if edades else None,
            "edad_max_s": edades[-1] if edades else None}
