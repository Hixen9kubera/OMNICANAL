"""El tope de corridas de Apify no puede amarrarse a un event loop.

Caso real (29-sep-2026): tres capturas del panel a la vez, cada una en su hilo con
su propio loop, dejaron el `asyncio.Semaphore` compartido «locked, waiters:1» y
toda captura posterior fallaba con «is bound to a different event loop».
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


class SemaforoEntreLoops(unittest.TestCase):
    def test_tres_capturas_en_hilos_con_su_loop(self):
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

        hilos = [threading.Thread(target=hilo) for _ in range(4)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(10)
        self.assertEqual(errores, [])
        self.assertLessEqual(maximo[0], 2)          # el tope se respeta
        # y el semáforo queda libre para la siguiente captura
        self.assertTrue(cs._sem.acquire(blocking=False))
        self.assertTrue(cs._sem.acquire(blocking=False))
        cs._sem.release()
        cs._sem.release()

    def test_cancelar_mientras_espera_no_se_queda_el_lugar(self):
        async def principal():
            async with cs._turno():
                async with cs._turno():
                    espera = asyncio.ensure_future(_tercera())
                    await asyncio.sleep(0.2)
                    espera.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await espera

        async def _tercera():
            async with cs._turno():
                pass

        asyncio.run(principal())
        self.assertTrue(cs._sem.acquire(blocking=False))
        self.assertTrue(cs._sem.acquire(blocking=False))
        cs._sem.release()
        cs._sem.release()


if __name__ == "__main__":
    unittest.main()
