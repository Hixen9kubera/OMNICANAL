"""
vigilante_loop.py — Dice QUIÉN congela el backend, en vez de deducirlo desde fuera.

Existe por el incidente del 13-ago: el panel se quedaba cargando, la CPU al 1.5%
y la memoria estable. Con la CPU ociosa el proceso no computa: ESPERA. Pero
"espera algo" no es un diagnóstico, y medir desde afuera (curl, logs del proxy,
métricas) ya había producido dos atribuciones equivocadas — primero el webhook de
TikTok, después el llenado del envío real. Las dos eran defectos reales; ninguna
era la causa de que el backend dejara de contestar.

CÓMO FUNCIONA. Dos piezas que se vigilan entre sí:

  · un LATIDO dentro del event loop, que solo apunta la hora cada 2 s;
  · un HILO aparte que compara esa hora contra la suya.

Si el latido se atrasa más que el umbral, el loop está bloqueado — y el hilo,
que NO depende del loop, sigue vivo para contarlo. Ahí vuelca la pila de TODOS
los hilos: la del hilo principal muestra la línea exacta donde se quedó parado.

Se usa `sys._current_frames()` y no `faulthandler` porque este último escribe a
un descriptor de archivo real y no se puede mandar al logger (y en Railway lo que
no pasa por el logger, no se ve).

Solo observa y registra. No cancela nada, no reinicia nada.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

log = logging.getLogger("omnicanal.vigilante_loop")

_iniciado = False


def _cadena(marco, solo_nuestros: bool = False, tope: int = 14) -> str:
    """La pila de un hilo en UNA línea: `archivo:línea función > …`.

    El último eslabón es donde está parado. Con `solo_nuestros`, devuelve vacío
    si el hilo no tiene código de la app (los del pool esperando trabajo).
    """
    if marco is None:
        return "(sin marco)"
    eslabones = []
    nuestros = False
    for f in traceback.extract_stack(marco)[-tope:]:
        archivo = f.filename.replace("\\", "/")
        if "/app/" in archivo or "\\omnicanal\\" in f.filename:
            archivo = archivo.split("/app/")[-1]
            nuestros = True
        else:  # librerías: solo el nombre del archivo, para no perder el rastro
            archivo = archivo.rsplit("/", 1)[-1]
        eslabones.append(f"{archivo}:{f.lineno} {f.name}")
    if solo_nuestros and not nuestros:
        return ""
    return " > ".join(eslabones)


def iniciar(umbral_s: float = 5.0, cada_s: float = 2.0, silencio_s: float = 60.0) -> None:
    """Arranca el latido y su vigilante. Idempotente."""
    global _iniciado
    if _iniciado:
        return
    _iniciado = True

    loop = asyncio.get_running_loop()
    principal = threading.get_ident()  # el hilo donde vive el event loop
    ultimo = {"latido": time.monotonic()}

    async def _latir() -> None:
        while True:
            ultimo["latido"] = time.monotonic()
            await asyncio.sleep(cada_s)

    loop.create_task(_latir())

    def _vigilar() -> None:
        ultimo_aviso = 0.0
        while True:
            time.sleep(cada_s)
            atraso = time.monotonic() - ultimo["latido"]
            if atraso < umbral_s:
                continue
            # Un volcado por minuto como mucho: si no, el propio vigilante
            # llenaría los logs justo cuando hay que leerlos.
            if time.monotonic() - ultimo_aviso < silencio_s:
                continue
            ultimo_aviso = time.monotonic()
            pilas = sys._current_frames()
            # UNA SOLA LÍNEA. Railway parte los mensajes multilínea en registros
            # sueltos y los reordena por milisegundo: en el primer incidente el
            # volcado llegó revuelto y no se pudo saber qué marcos eran del hilo
            # principal — que es justo el dato. Aquí la pila viaja encadenada con
            # " > ", del más antiguo al más reciente (el último es dónde está
            # parado), y solo se nombran los marcos NUESTROS.
            log.error("EVENT LOOP ATASCADO %.1f s - %d hilos vivos | PRINCIPAL: %s",
                      atraso, len(pilas), _cadena(pilas.get(principal)))
            # Los demás hilos, uno por línea y solo si tienen código nuestro:
            # los del pool esperando trabajo no dicen nada y tapan el volcado.
            for tid, m in pilas.items():
                if tid == principal:
                    continue
                cadena = _cadena(m, solo_nuestros=True)
                if not cadena:
                    continue
                nombre = next((h.name for h in threading.enumerate()
                               if h.ident == tid), str(tid))
                log.error("  hilo %s: %s", nombre, cadena)

    threading.Thread(target=_vigilar, name="vigilante-loop", daemon=True).start()
    log.info("Vigilante del event loop encendido (umbral %.0f s).", umbral_s)
