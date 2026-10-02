"""Pruebas de los estados de Temu (`services/temu.py`) y de cómo los leen sus consumidores.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
  1. Vende `2/8`: con el estado de la publicación AL MOMENTO de cada pedido, 363 de
     388 pedidos de 30 días (al 2-oct-2026) se hicieron en `2/8` y NINGUNO en `4/7`.
  2. `5/None` se sigue llamando «Borrador»: `fanout_stock` y `fanout_vivo` deciden
     con ESA etiqueta saltarse los borradores (Temu rechaza `stock.edit` ahí).
  3. Lo que no tiene nombre verificado se enseña crudo («Temu 3/3»): no se inventa.
  4. Un código nuevo cae en `desconocido`, no se aplasta a pausada ni a activa.
  5. El literal del fan-out en vivo (`_TEMU_A_LA_VENTA`, va dentro de un SQL)
     coincide con `temu.VENDIBLES`.

    cd backend && python -m unittest tests.test_temu_estados -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import fanout_vivo, temu, temu_panel  # noqa: E402
from services import publicaciones_panel as pp  # noqa: E402


class EstadosTemu(unittest.TestCase):
    def test_vende_2_8_y_no_4_7(self):
        self.assertEqual(temu.VENDIBLES, {"2/8"})
        self.assertEqual(pp.normalizar_estado("temu", None, "2/8"), pp.ACTIVA)
        self.assertEqual(pp.normalizar_estado("temu", None, "4/7"), pp.PAUSADA)
        self.assertEqual(pp.normalizar_estado("temu", None, "3/1"), pp.PAUSADA, "agotada: existe y hoy no vende")
        self.assertEqual(pp.valores_activos("temu"), ["2/8"])

    def test_borrador_sigue_llamandose_borrador(self):
        self.assertEqual(temu.ESTADOS["5/None"], "Borrador")
        self.assertEqual(pp.normalizar_estado("temu", None, "5/None"), pp.BORRADOR)

    def test_lo_sin_nombre_se_ensena_crudo(self):
        self.assertEqual(temu_panel.etiqueta_estado("2/8"), "A la venta")
        self.assertEqual(temu_panel.etiqueta_estado("3/1"), "Agotada")
        self.assertEqual(temu_panel.etiqueta_estado("3/3"), "Temu 3/3")
        self.assertEqual(temu_panel.etiqueta_estado(None), "sin publicar")

    def test_un_codigo_nuevo_no_se_aplasta(self):
        self.assertEqual(pp.normalizar_estado("temu", None, "4/10"), pp.DESCONOCIDO)

    def test_el_fanout_en_vivo_usa_la_misma_regla(self):
        self.assertIn(fanout_vivo._TEMU_A_LA_VENTA, temu.VENDIBLES)


if __name__ == "__main__":
    unittest.main()
