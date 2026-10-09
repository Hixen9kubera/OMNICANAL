"""La foto de TEXCO II (Odoo → almacen.locations): las reglas del cargador, sin red."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import cargar_locations_tex2 as c  # noqa: E402


def _q(qid, pid, ruta, cantidad, entro="2026-06-10 19:22:53"):
    return {"id": qid, "product_id": [pid, "x"], "location_id": [1, ruta],
            "quantity": cantidad, "in_date": entro}


class Ubicacion(unittest.TestCase):
    def test_rack_completo_se_parte(self):
        self.assertEqual(c.ubicacion_de("TEX2/FERRAFORME/BLOQUE S1/FILA 2/T17"),
                         ("BLOQUE S1-FILA 2-T17", "S1", "2", "T17"))

    def test_la_raiz_de_la_nave_es_sin_ubicar(self):
        self.assertEqual(c.ubicacion_de("TEX2/FERRAFORME"), ("SIN UBICAR", None, None, None))

    def test_zona_que_no_es_rack_conserva_su_nombre(self):
        self.assertEqual(c.ubicacion_de("TEX2/FERRAFORME/REQUERIMENTOS")[0], "REQUERIMENTOS")
        self.assertEqual(c.ubicacion_de("TEX2/Zona de empaquetado")[0], "Zona de empaquetado")
        # No es la raíz de la nave: si cayera en SIN UBICAR chocaría con ella.
        self.assertEqual(c.ubicacion_de("TEX2/FERRAFORME Archivar"),
                         ("FERRAFORME Archivar", None, None, None))


class Filas(unittest.TestCase):
    def test_trae_el_negativo_con_su_signo_y_el_cedis(self):
        filas, s = c.filas_desde_odoo(
            [_q(1, 10, "TEX2/FERRAFORME/BLOQUE D/FILA 1/T6", 150.0),
             _q(2, 10, "TEX2/FERRAFORME", -2.0)],
            {10: {"default_code": "ACC-0886-NEG", "name": "x", "active": True}})
        self.assertEqual([(f[0], f[1], f[2], f[6]) for f in filas],
                         [("TEX2", "ACC-0886-NEG", "BLOQUE D-FILA 1-T6", 150),
                          ("TEX2", "ACC-0886-NEG", "SIN UBICAR", -2)])
        self.assertEqual((s["piezas"], s["negativos"], s["sin_ubicar"], s["skus"]), (148, 1, 1, 1))

    def test_el_gemelo_archivado_entra_marcado_y_no_choca(self):
        filas, s = c.filas_desde_odoo(
            [_q(1, 10, "TEX2/FERRAFORME", 480.0), _q(2, 11, "TEX2/FERRAFORME", 6720.0)],
            {10: {"default_code": "DEC-0020", "name": "x", "active": True},
             11: {"default_code": "DEC-0020", "name": "x", "active": False}})
        self.assertEqual([f[7] for f in filas], [False, True])
        self.assertEqual(s["archivados"], 1)

    def test_sin_sku_usa_el_nombre_solo_si_es_un_sku(self):
        filas, s = c.filas_desde_odoo(
            [_q(1, 10, "TEX2/FERRAFORME Archivar", -54.0)],
            {10: {"default_code": False, "name": "DEPO-0001-AZL", "active": True}})
        self.assertEqual(filas[0][1], "DEPO-0001-AZL")
        self.assertEqual(s["sin_sku_en_odoo"], 1)
        with self.assertRaises(ValueError):
            c.filas_desde_odoo([_q(1, 10, "TEX2/FERRAFORME", 5.0)],
                               {10: {"default_code": False, "name": "Flor artificial", "active": True}})

    def test_no_adivina(self):
        activo = {"default_code": "X-1", "name": "x", "active": True}
        with self.assertRaises(ValueError):   # decimales
            c.filas_desde_odoo([_q(1, 10, "TEX2/FERRAFORME", 1.5)], {10: activo})
        with self.assertRaises(ValueError):   # dos productos activos, mismo SKU y ubicación
            c.filas_desde_odoo([_q(1, 10, "TEX2/FERRAFORME", 1.0), _q(2, 11, "TEX2/FERRAFORME", 1.0)],
                               {10: activo, 11: dict(activo)})
        with self.assertRaises(ValueError):   # Odoo no devolvió el producto
            c.filas_desde_odoo([_q(1, 99, "TEX2/FERRAFORME", 1.0)], {})


if __name__ == "__main__":
    unittest.main()
