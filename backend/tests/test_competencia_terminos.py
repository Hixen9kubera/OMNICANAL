"""Pruebas de `competencia_terminos`: el término general que propone la IA.

── POR QUÉ ESTE MÓDULO Y NO OTRO ───────────────────────────────────────────────
Porque es el único de Competencia donde un fallo SILENCIOSO cuesta dinero dos
veces: un término malo se mide con Apify (~$0.007) y encima mide la competencia
equivocada, así que la pantalla enseña rivales que no lo son. El módulo ya lleva
un bug documentado de esa clase —el SKU `TEC-1284-NEG-27"` volvía del modelo sin
la comilla y el cruce exacto lo descartaba sin avisar— y estas pruebas lo fijan.

── SE USA `unittest`, NO `pytest` ──────────────────────────────────────────────
El repo no tenía suite de pruebas y `pytest` no está instalado. `unittest` viene
con Python, así que estas corren sin agregar una dependencia:

    cd backend && python -m unittest discover -s tests -v

Si algún día se adopta pytest, las descubre igual: hereda de `unittest.TestCase`.

── NO SE LLAMA A LA IA ─────────────────────────────────────────────────────────
`proponer` se prueba con la respuesta del modelo SUPLANTADA. Una prueba que
depende de un LLM no es determinista, cuesta, y no prueba nuestro código: prueba
el suyo.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import competencia_terminos as CT  # noqa: E402


class LimpiarTermino(unittest.TestCase):
    """`_limpiar` poda lo que el modelo deja pasar de más."""

    def test_quita_comillas_y_baja_a_minusculas(self):
        self.assertEqual(CT._limpiar('"Lona Para Exterior"'), "lona para exterior")

    def test_quita_medidas(self):
        # El prompt las prohíbe, pero el modelo las cuela. Una medida en el
        # término convierte una búsqueda amplia en una casi sin volumen.
        self.assertEqual(CT._limpiar("lona 4x6m para exterior"), "lona para exterior")
        self.assertEqual(CT._limpiar("cable 20 cm usb"), "cable usb")

    def test_recorta_a_cinco_palabras(self):
        largo = "regadera portatil recargable con pantalla led para camping"
        self.assertEqual(len(CT._limpiar(largo).split()), 5)

    def test_colapsa_espacios(self):
        self.assertEqual(CT._limpiar("  tapetes   para  auto "), "tapetes para auto")

    def test_vacio_no_revienta(self):
        self.assertEqual(CT._limpiar(""), "")
        self.assertEqual(CT._limpiar(None), "")


class ParsearRespuesta(unittest.TestCase):
    """`_parse_json` aguanta lo que los modelos suelen devolver."""

    def test_json_limpio(self):
        self.assertEqual(CT._parse_json('{"terminos": []}'), {"terminos": []})

    def test_json_entre_cercas(self):
        crudo = '```json\n{"terminos": [{"sku": "A", "termino": "x"}]}\n```'
        self.assertEqual(len(CT._parse_json(crudo)["terminos"]), 1)

    def test_json_con_prosa_alrededor(self):
        crudo = 'Claro, aquí tienes:\n{"terminos": [{"sku": "A", "termino": "x"}]}\nEspero sirva.'
        self.assertEqual(len(CT._parse_json(crudo)["terminos"]), 1)

    def test_basura_devuelve_vacio(self):
        # Vacío, NO una excepción: el llamador decide qué hacer con "no sé".
        self.assertEqual(CT._parse_json("no pude procesar eso"), {})
        self.assertEqual(CT._parse_json(""), {})


class Proponer(unittest.TestCase):
    """El contrato de `proponer`: o propone bien, o no propone."""

    PRODUCTOS = [
        {"sku": "VIA-0023-NEG", "nombre": "Regadera portatil recargable 8000mah",
         "categoria_nombre": "Duchas Portátiles"},
        {"sku": "TEC-1284-NEG-27\"", "nombre": "Monitor gamer curvo 27 pulgadas"},
    ]

    def _con_respuesta(self, texto: str, ok: bool = True):
        return mock.patch.object(CT.ia_generadores, "_completar",
                                 return_value={"ok": ok, "texto": texto})

    def test_propone_lo_pedido(self):
        resp = ('{"terminos": [{"sku": "VIA-0023-NEG", "termino": "regadera portatil"}]}')
        with self._con_respuesta(resp):
            out = CT.proponer(self.PRODUCTOS)
        self.assertEqual(out["VIA-0023-NEG"], "regadera portatil")

    def test_reconcilia_el_sku_que_el_modelo_normaliza(self):
        """EL BUG DOCUMENTADO: el modelo devuelve el SKU sin la comilla.

        Antes el cruce era exacto y ese SKU se perdía en silencio — un monitor
        gamer se quedó sin término por una comilla."""
        resp = '{"terminos": [{"sku": "TEC-1284-NEG-27", "termino": "monitor gamer"}]}'
        with self._con_respuesta(resp):
            out = CT.proponer(self.PRODUCTOS)
        self.assertIn('TEC-1284-NEG-27"', out, "el SKU con comilla debe recuperarse")
        self.assertEqual(out['TEC-1284-NEG-27"'], "monitor gamer")

    def test_ignora_un_sku_que_no_pedimos(self):
        resp = '{"terminos": [{"sku": "INVENTADO-99", "termino": "lo que sea"}]}'
        with self._con_respuesta(resp):
            out = CT.proponer(self.PRODUCTOS)
        self.assertEqual(out, {})

    def test_si_la_ia_falla_no_inventa(self):
        """Devuelve {}, no un término derivado del título.

        Está escrito en el docstring del módulo y es la regla que importa: un
        término malo mide la competencia equivocada, y eso es PEOR que no medir.
        """
        with self._con_respuesta("", ok=False):
            self.assertEqual(CT.proponer(self.PRODUCTOS), {})

    def test_si_la_respuesta_es_basura_no_inventa(self):
        with self._con_respuesta("lo siento, no puedo"):
            self.assertEqual(CT.proponer(self.PRODUCTOS), {})

    def test_descarta_productos_sin_nombre(self):
        # Sin título no hay nada que interpretar; no se le pregunta a la IA por él.
        with self._con_respuesta('{"terminos": []}') as falso:
            CT.proponer([{"sku": "SIN-NOMBRE"}, self.PRODUCTOS[0]])
            enviado = falso.call_args[0][1]
        self.assertIn("VIA-0023-NEG", enviado)
        self.assertNotIn("SIN-NOMBRE", enviado)

    def test_lista_vacia_no_llama_a_la_ia(self):
        with self._con_respuesta('{"terminos": []}') as falso:
            self.assertEqual(CT.proponer([]), {})
        falso.assert_not_called()

    def test_el_termino_propuesto_pasa_por_la_limpieza(self):
        resp = '{"terminos": [{"sku": "VIA-0023-NEG", "termino": "\\"Regadera 20 cm Portatil\\""}]}'
        with self._con_respuesta(resp):
            out = CT.proponer(self.PRODUCTOS)
        self.assertEqual(out["VIA-0023-NEG"], "regadera portatil")


if __name__ == "__main__":
    unittest.main(verbosity=2)
