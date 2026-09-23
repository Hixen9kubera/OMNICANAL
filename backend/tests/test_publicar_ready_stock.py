"""Pruebas del motivo de rechazo por stock al dar de alta en Mercado Libre
(`services/publicar_ready.py`).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Woo sin número no es Odoo en cero.** Si `construir_prod` marcó
   `sin_inventario_woo` ("Gestionar inventario" apagado), el motivo dice eso,
   no "Sin stock en Odoo": TEC-2370-MET tenía 60 piezas libres en Odoo y el
   modal mandaba a buscarlas allá (23-sep).
2. **Woo en cero sigue siendo "Sin stock en Odoo"**: Woo copia `free_qty`.
3. Un `prod` sin la llave (armado por otro camino) cae en el mensaje de siempre.

Sin red.

    cd backend && python -m unittest tests.test_publicar_ready_stock -v
"""
from __future__ import annotations

import unittest

from services.publicar_ready import (MSG_SIN_INVENTARIO_WOO, MSG_SIN_STOCK_ML,
                                     _motivo_sin_stock)


class MotivoSinStock(unittest.TestCase):
    def test_woo_sin_numero_lo_dice(self):
        self.assertEqual(_motivo_sin_stock({"stock": 0, "sin_inventario_woo": True}),
                         MSG_SIN_INVENTARIO_WOO)

    def test_woo_en_cero_culpa_a_odoo(self):
        self.assertEqual(_motivo_sin_stock({"stock": 0, "sin_inventario_woo": False}),
                         MSG_SIN_STOCK_ML)

    def test_prod_sin_la_llave_usa_el_mensaje_de_siempre(self):
        self.assertEqual(_motivo_sin_stock({"stock": 0}), MSG_SIN_STOCK_ML)

    def test_el_texto_nuevo_no_culpa_a_odoo(self):
        self.assertNotIn("Sin stock en Odoo", MSG_SIN_INVENTARIO_WOO)
        self.assertIn("Gestionar inventario", MSG_SIN_INVENTARIO_WOO)


if __name__ == "__main__":
    unittest.main()
