"""Pruebas del veredicto de lo que le falta a un envío a FULL (`fulfillment_ml_inventario`).

── QUÉ FIJAN (casos reales de TEXCO/OUT/06483, San Corpe, 2-oct-2026) ──────────
  1. Lo que ML tiene en RETIRO y cubre el faltante es «en retiro», no «no recibido»
     (ACC-0441-PLA-21PZ: 25 enviadas, 0 avisadas, 25 en withdrawal).
  2. Si lo que hay en FULL + lo vendido − lo que ya había ≥ lo enviado, llegó todo
     aunque los avisos no lo contaran (TEC-0107-RO-NE-4CE: 53 enviadas, 44 avisadas,
     48 a la venta + 1 en retiro + 5 vendidas, 0 antes).
  3. Lo que ML no tiene ni vendió es «no aparece», con cuántas faltan
     (TEC-0383-MET: 30 enviadas, 29 avisadas y 29 vendidas, 0 en FULL → falta 1).
  4. Sin dato de ML no se juzga; sin stock de antes, el veredicto va `aprox`.

    cd backend && python -m unittest tests.test_fulfillment_ml_inventario -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import fulfillment_ml_inventario as fmi  # noqa: E402


def _inv(disponible=0, **detalle):
    return {"disponible": disponible, "no_disponible": sum(detalle.values()), "detalle": detalle}


class Veredicto(unittest.TestCase):
    def test_en_retiro(self):
        v = fmi.veredicto(25, 0, _inv(0, withdrawal=25), vendidas=0, antes=0)
        self.assertEqual((v["veredicto"], v["faltan"], v["retiro"]), ("en_retiro", 25, 25))

    def test_llego_sin_aviso(self):
        v = fmi.veredicto(53, 44, _inv(48, withdrawal=1), vendidas=5, antes=0)
        self.assertEqual(v["veredicto"], "llego_sin_aviso")
        self.assertFalse(v["aprox"])

    def test_se_vendio_todo_tambien_es_llegada(self):
        # 30 enviadas, 29 avisadas, 0 en FULL y 30 vendidas desde la llegada: llegó todo.
        self.assertEqual(fmi.veredicto(30, 29, _inv(0), vendidas=30, antes=0)["veredicto"], "llego_sin_aviso")

    def test_tec_0383_met_falta_una(self):
        v = fmi.veredicto(30, 29, _inv(0), vendidas=29, antes=0)
        self.assertEqual((v["veredicto"], v["no_aparecen"]), ("no_aparece", 1))

    def test_lo_que_ya_habia_no_cuenta_como_llegada(self):
        v = fmi.veredicto(10, 6, _inv(12), vendidas=0, antes=5)
        self.assertEqual((v["veredicto"], v["no_aparecen"]), ("no_aparece", 3), "12 − 5 de antes = 7 de 10")

    def test_no_vendible_por_otro_motivo(self):
        v = fmi.veredicto(10, 7, _inv(7, damaged=3), vendidas=0, antes=0)
        self.assertEqual(v["veredicto"], "no_vendible")

    def test_sin_dato_no_se_juzga(self):
        self.assertEqual(fmi.veredicto(10, 4, None, vendidas=0, antes=0)["veredicto"], "sin_dato")

    def test_sin_stock_de_antes_es_aproximado(self):
        v = fmi.veredicto(6, 5, _inv(6), vendidas=0, antes=None)
        self.assertEqual((v["veredicto"], v["aprox"]), ("llego_sin_aviso", True))

    def test_cada_veredicto_tiene_texto(self):
        for clave in ("en_retiro", "no_vendible", "llego_sin_aviso", "no_aparece", "sin_publicacion", "sin_dato"):
            self.assertIn(clave, fmi.TEXTO)


if __name__ == "__main__":
    unittest.main()
