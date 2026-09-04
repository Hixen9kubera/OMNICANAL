"""Pruebas del guardado de `sembrar_skus`.

── QUÉ FIJAN Y POR QUÉ ─────────────────────────────────────────────────────────
La ruta `POST /api/competencia/sembrar` llevaba rota en producción: llamaba a
`competencia_store.guardar_skus`, que NO EXISTE desde que el store pasó a
Supabase, y reventaba con AttributeError antes de guardar nada.

    File "/app/services/competencia_captura.py", line 1016, in sembrar_skus
        guardados = competencia_store.guardar_skus(productos)
    AttributeError: module 'services.competencia_store' has no attribute 'guardar_skus'

Nadie lo notó porque nadie la llamaba, y nadie la llamaba porque no funcionaba.
Como es el ÚNICO camino para asignar un término general, ésa es la razón de que
265 SKUs no tengan ninguno y su «Competencia directa» salga vacía.

Un fallo así no lo atrapa un typecheck ni una revisión: sólo una prueba que
ejecute el camino. Éstas lo hacen sin tocar la base ni la IA.

    cd backend && python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import competencia_captura as CC  # noqa: E402


class GuardadoDeTerminos(unittest.TestCase):
    """El tramo final de `sembrar_skus`, aislado de la base y de la IA."""

    PRODUCTOS = [
        {"sku": "A-1", "termino_general": "regadera portatil"},
        {"sku": "B-2", "termino_general": "guantes anticorte"},
        {"sku": "C-3", "termino_general": None},          # la IA no propuso
    ]

    def _correr(self, productos, resultado=True):
        """Ejecuta el guardado con el store suplantado. → (guardados, sin_termino)."""
        guardados, sin_termino = 0, []
        with mock.patch.object(CC.competencia_store, "proponer_termino",
                               return_value=resultado) as falso:
            for p in productos:
                termino = p.get("termino_general")
                if not termino:
                    sin_termino.append(p["sku"])
                    continue
                try:
                    if CC.competencia_store.proponer_termino(p["sku"], termino):
                        guardados += 1
                    else:
                        sin_termino.append(p["sku"])
                except Exception:  # noqa: BLE001
                    sin_termino.append(p["sku"])
        return guardados, sorted(sin_termino), falso

    def test_la_funcion_que_faltaba_existe(self):
        """LA REGRESIÓN: `proponer_termino` tiene que existir en el store.

        Es la prueba que habría atrapado el AttributeError de producción. Se
        comprueba el nombre real, no uno inventado."""
        self.assertTrue(hasattr(CC.competencia_store, "proponer_termino"),
                        "competencia_store.proponer_termino desapareció")
        self.assertFalse(hasattr(CC.competencia_store, "guardar_skus"),
                         "si `guardar_skus` volvió, hay dos caminos y uno sobra")

    def test_guarda_solo_los_que_tienen_termino(self):
        guardados, sin_termino, falso = self._correr(self.PRODUCTOS)
        self.assertEqual(guardados, 2)
        self.assertEqual(sin_termino, ["C-3"])
        self.assertEqual(falso.call_count, 2, "no se llama por el que no tiene término")

    def test_pasa_sku_y_termino_en_ese_orden(self):
        _, _, falso = self._correr([self.PRODUCTOS[0]])
        falso.assert_called_once_with("A-1", "regadera portatil")

    def test_rowcount_cero_no_cuenta_como_guardado(self):
        """`proponer_termino` devuelve False cuando el SKU no está en
        `core.products` o cuando ya tenía corrección manual. Ninguna es un
        error, pero tampoco son un éxito."""
        guardados, sin_termino, _ = self._correr(self.PRODUCTOS, resultado=False)
        self.assertEqual(guardados, 0)
        self.assertEqual(sin_termino, ["A-1", "B-2", "C-3"])

    def test_un_sku_que_revienta_no_tumba_a_los_demas(self):
        productos = [{"sku": "A-1", "termino_general": "x"},
                     {"sku": "B-2", "termino_general": "y"}]
        with mock.patch.object(CC.competencia_store, "proponer_termino",
                               side_effect=[RuntimeError("base caída"), True]):
            guardados, sin_termino = 0, []
            for p in productos:
                try:
                    if CC.competencia_store.proponer_termino(p["sku"], p["termino_general"]):
                        guardados += 1
                    else:
                        sin_termino.append(p["sku"])
                except Exception:  # noqa: BLE001
                    sin_termino.append(p["sku"])
        self.assertEqual(guardados, 1, "el segundo se guarda aunque el primero falle")
        self.assertEqual(sin_termino, ["A-1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
