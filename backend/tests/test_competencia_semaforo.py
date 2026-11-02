"""El tope de corridas de Apify no puede amarrarse a un event loop.

Caso real (29-sep-2026): tres capturas del panel a la vez, cada una en su hilo con
su propio loop, dejaron el `asyncio.Semaphore` compartido «locked, waiters:1» y
toda captura posterior fallaba con «is bound to a different event loop».

El tope pasó de 2 a `TOPE_APIFY` (24) en la v0.710.1: las pruebas lo leen de ahí.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import competencia_scraper as cs  # noqa: E402

TOPE = cs.TOPE_APIFY


def _queda_libre(test: unittest.TestCase) -> None:
    """El semáforo entero vuelve a estar disponible para la siguiente captura."""
    for _ in range(TOPE):
        test.assertTrue(cs._sem.acquire(blocking=False))
    test.assertFalse(cs._sem.acquire(blocking=False))
    for _ in range(TOPE):
        cs._sem.release()


class SemaforoEntreLoops(unittest.TestCase):
    def test_capturas_en_hilos_con_su_loop(self):
        adentro, maximo, errores = [0], [0], []
        candado = threading.Lock()

        async def captura():
            async with cs._turno():
                with candado:
                    adentro[0] += 1
                    maximo[0] = max(maximo[0], adentro[0])
                await asyncio.sleep(0.3)
                with candado:
                    adentro[0] -= 1

        def hilo():
            try:
                asyncio.run(captura())
            except Exception as exc:  # noqa: BLE001
                errores.append(repr(exc))

        hilos = [threading.Thread(target=hilo) for _ in range(TOPE + 2)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(10)
        self.assertEqual(errores, [])
        self.assertLessEqual(maximo[0], TOPE)        # el tope se respeta
        _queda_libre(self)

    def test_cancelar_mientras_espera_no_se_queda_el_lugar(self):
        # Se ocupan todos los lugares menos dos; las dos capturas anidadas llenan el resto y la tercera espera.
        for _ in range(TOPE - 2):
            self.assertTrue(cs._sem.acquire(blocking=False))

        async def principal():
            async with cs._turno():
                async with cs._turno():
                    espera = asyncio.ensure_future(_tercera())
                    await asyncio.sleep(0.2)
                    self.assertFalse(espera.done())   # de verdad estaba esperando
                    espera.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await espera

        async def _tercera():
            async with cs._turno():
                pass

        try:
            asyncio.run(principal())
        finally:
            for _ in range(TOPE - 2):
                cs._sem.release()
        _queda_libre(self)


if __name__ == "__main__":
    unittest.main()
