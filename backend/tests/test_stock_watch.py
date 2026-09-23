"""Pruebas del vigilante de stock (`services/stock_watch.py`): la decisión
Odoo -> Woo de cada pasada, `_deltas_odoo`.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Woo sin número no es Woo en cero.** Un simple con "Gestionar inventario"
   apagado y piezas en Odoo recibe el número de Odoo. Antes se saltaba siempre
   y el candado de publicar lo leía como 0 (TEC-2370-MET, 60 piezas, 23-sep).
2. **Opción «a»: solo si Odoo tiene piezas.** Sin número y con 0 en Odoo NO se
   toca: prenderlo lo pasaría a "agotado" sin que nadie lo decidiera.
3. **Nunca a un padre variable**: el stock lo llevan sus variaciones.
4. **Solo en modo absoluto**: el delta no tiene base de la cual partir.
5. Lo que ya funcionaba sigue igual: absoluto copia, delta suma la variación.

Sin red: la función es pura.

    cd backend && python -m unittest tests.test_stock_watch -v
"""
from __future__ import annotations

import unittest

from services.stock_watch import _deltas_odoo


def _w(stock, *, variable=False, tipo="product", padre=0, wc_id=1):
    return {"stock": stock, "id": wc_id, "tipo": tipo, "padre": padre, "variable": variable}


class DeltasOdoo(unittest.TestCase):
    def test_simple_sin_numero_con_piezas_recibe_odoo(self):
        w = _w(None)
        self.assertEqual(_deltas_odoo({"TEC-2370-MET": 60}, {"TEC-2370-MET": w}, {}, True),
                         [("TEC-2370-MET", 60, w)])

    def test_simple_sin_numero_en_cero_no_se_toca(self):
        self.assertEqual(_deltas_odoo({"MES-0049-BLN": 0}, {"MES-0049-BLN": _w(None)}, {}, True), [])

    def test_padre_variable_sin_numero_no_se_toca(self):
        self.assertEqual(_deltas_odoo({"CAM-0030": 60}, {"CAM-0030": _w(None, variable=True)}, {}, True), [])

    def test_variacion_sin_numero_con_piezas_recibe_odoo(self):
        w = _w(None, tipo="product_variation", padre=99)
        self.assertEqual(_deltas_odoo({"ACC-0001-NEG": 5}, {"ACC-0001-NEG": w}, {}, True),
                         [("ACC-0001-NEG", 5, w)])

    def test_modo_delta_no_prende_sin_numero(self):
        foto = {"TEC-2370-MET": {"odoo": 50, "woo": None}}
        self.assertEqual(_deltas_odoo({"TEC-2370-MET": 60}, {"TEC-2370-MET": _w(None)}, foto, False), [])

    def test_absoluto_copia_y_no_repite_lo_igual(self):
        w = _w(5)
        self.assertEqual(_deltas_odoo({"A": 7}, {"A": w}, {}, True), [("A", 7, w)])
        self.assertEqual(_deltas_odoo({"A": 7}, {"A": _w(7)}, {}, True), [])

    def test_delta_suma_la_variacion_y_sin_foto_no_decide(self):
        w = _w(5)
        foto = {"A": {"odoo": 10, "woo": 5}}
        self.assertEqual(_deltas_odoo({"A": 12}, {"A": w}, foto, False), [("A", 7, w)])
        self.assertEqual(_deltas_odoo({"A": 12}, {"A": w}, {}, False), [])

    def test_sku_de_odoo_que_no_esta_en_woo(self):
        self.assertEqual(_deltas_odoo({"NO-EXISTE": 9}, {}, {}, True), [])


if __name__ == "__main__":
    unittest.main()
