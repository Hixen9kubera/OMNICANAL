"""Las medidas de la CAJA no se copian a los atributos del PRODUCTO al publicar en ML.

── QUÉ FIJAN ────────────────────────────────────────────
2-nov-2026, vista previa de un lote de 51: `attribute_mapper.build_secondary_attributes`
llenaba todo atributo cuyo id/nombre dijera largo/ancho/alto/peso con el paquete que manda
el panel (`campos.peso/largo/ancho/alto`): barras para lagartijas con «Peso máximo
soportado = 460 kg» (la caja pesa 0.46 kg), rodilleras con «Grosor = 170 mm», la
«superficie para correr» de una caminadora del tamaño de su caja.

  1. Con el adaptador configurado, un atributo de medida que el panel NO mandó queda fuera.
  2. Lo demás se sigue llenando igual (un COLOR que viene de Woo).
  3. El paquete (`SELLER_PACKAGE_*`) no pasa por aquí: lo sigue armando `publisher_core`.

── NO SE TOCA NADA REAL ─────────────────────────────────
Sin red: se llama al mapper con una categoría de juguete.

    cd backend && python -m unittest tests.test_publicar_medidas_caja -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import publicar_ready  # noqa: E402
from vendor.ml_ready import attribute_mapper  # noqa: E402

CATEGORIA = [
    {"id": "MAX_WEIGHT_SUPPORTED", "name": "Peso máximo soportado", "value_type": "number_unit",
     "allowed_units": [{"id": "g"}, {"id": "kg"}, {"id": "lb"}], "tags": {}},
    {"id": "LENGTH", "name": "Largo", "value_type": "number_unit", "allowed_units": [{"id": "cm"}], "tags": {}},
    {"id": "RUNNING_SURFACE_WIDTH", "name": "Ancho de la superficie para correr", "value_type": "number_unit",
     "allowed_units": [{"id": "cm"}], "tags": {}},
    {"id": "COLOR", "name": "Color", "value_type": "string", "tags": {}},
]
PROD = {"weight": 0.46, "length": 25, "width": 15, "height": 9, "wc_attrs": {"color": "Negro"}, "ml_attrs": {}}


class MedidasDeCaja(unittest.TestCase):
    def setUp(self):
        self._original = attribute_mapper._get_dimension_value

    def tearDown(self):
        attribute_mapper._get_dimension_value = self._original

    def test_sin_el_adaptador_el_mapper_copiaba_la_caja(self):
        ids = {a["id"] for a in attribute_mapper.build_secondary_attributes(PROD, CATEGORIA, set())}
        self.assertIn("MAX_WEIGHT_SUPPORTED", ids)    # el defecto que se corrige

    def test_con_el_adaptador_no_se_copian_medidas(self):
        publicar_ready._instalar_sin_medidas_de_caja()
        res = {a["id"]: a for a in attribute_mapper.build_secondary_attributes(PROD, CATEGORIA, set())}
        self.assertNotIn("MAX_WEIGHT_SUPPORTED", res)
        self.assertNotIn("LENGTH", res)
        self.assertNotIn("RUNNING_SURFACE_WIDTH", res)
        self.assertEqual(res["COLOR"]["value_name"], "Negro")   # lo demás, igual

    def test_instalar_dos_veces_no_cambia_nada(self):
        publicar_ready._instalar_sin_medidas_de_caja()
        publicar_ready._instalar_sin_medidas_de_caja()
        self.assertIs(attribute_mapper._get_dimension_value, publicar_ready._sin_medidas_de_caja)


if __name__ == "__main__":
    unittest.main()
