"""Pruebas del rescate de JSON cortado por `max_tokens`.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
Reportado el 4-sep-2026 en la pantalla: «La IA no devolvió JSON válido:
Expecting ',' delimiter: line 22 column 128 (char 2671)».

No era el modelo devolviendo basura. Era la respuesta CORTADA en `max_tokens`, y
el parser tomaba `txt[find("{"):rfind("}")+1]` de un texto incompleto — un
recorte que nunca cierra. La palabra número 12 quedaba a medias y las once
buenas se tiraban con ella.

`_objetos_completos` recupera las entradas enteras. Media lista de palabras
clave sirve; un error rojo no.

    cd backend && python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import competencia_captura as CC  # noqa: E402


class ObjetosCompletos(unittest.TestCase):
    """`_objetos_completos` cuenta llaves; no usa expresiones regulares."""

    def test_rescata_las_enteras_y_tira_la_cortada(self):
        """EL CASO REAL: la respuesta se corta a media entrada."""
        crudo = (
            '{"palabras": ['
            '{"palabra": "tenis hombre", "porque": "es el mas buscado"},'
            '{"palabra": "tenis blancos", "porque": "segundo en volumen"},'
            '{"palabra": "tenis muj'          # ← aquí se acabaron los tokens
        )
        out = CC._objetos_completos(crudo)
        self.assertEqual(len(out), 2, "las dos enteras se rescatan")
        self.assertEqual(out[0]["palabra"], "tenis hombre")
        self.assertEqual(out[1]["palabra"], "tenis blancos")

    def test_json_entero_tambien_devuelve_las_entradas(self):
        crudo = '{"palabras": [{"palabra": "a"}, {"palabra": "b"}], "evitar": []}'
        # El objeto de AFUERA cierra, así que también es un objeto completo.
        palabras = [o for o in CC._objetos_completos(crudo) if o.get("palabra")]
        self.assertEqual([p["palabra"] for p in palabras], ["a", "b"])

    def test_no_se_confunde_con_llaves_dentro_de_los_valores(self):
        """Una regex se rompería aquí; contar llaves balanceadas no."""
        crudo = '{"palabra": "kit {2 piezas}", "porque": "trae { y }"} y basura'
        out = CC._objetos_completos(crudo)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["palabra"], "kit {2 piezas}")

    def test_los_anidados_tambien_salen_y_el_llamador_filtra(self):
        """Un objeto DENTRO de otro también se emite: es válido y no se sabe
        cuál interesa. Quien llama se queda con los que traen `palabra`, que es
        exactamente lo que hace `sugerir_palabras_subcategoria`."""
        crudo = '{"palabra": "a", "meta": {"x": 1}} {"palabra": "b"}'
        out = CC._objetos_completos(crudo)
        self.assertEqual([o["palabra"] for o in out if o.get("palabra")], ["a", "b"])
        self.assertIn({"x": 1}, out, "el anidado sale, y estorbar no estorba")

    def test_prosa_alrededor_no_estorba(self):
        crudo = 'Claro:\n{"palabra": "a"}\nEspero que sirva.'
        self.assertEqual(len(CC._objetos_completos(crudo)), 1)

    def test_una_entrada_rota_no_tumba_a_las_buenas(self):
        # La de en medio no es JSON válido (comilla sin cerrar).
        crudo = '{"palabra": "a"} {"palabra: "b"} {"palabra": "c"}'
        out = CC._objetos_completos(crudo)
        self.assertIn("a", [o.get("palabra") for o in out])
        self.assertIn("c", [o.get("palabra") for o in out])

    def test_sin_nada_que_rescatar_devuelve_vacio(self):
        # Vacío, NO una excepción: quien llama decide si eso es un error.
        self.assertEqual(CC._objetos_completos(""), [])
        self.assertEqual(CC._objetos_completos("lo siento, no puedo"), [])
        self.assertEqual(CC._objetos_completos("{{{"), [])

    def test_una_llave_de_cierre_suelta_no_revienta(self):
        self.assertEqual(CC._objetos_completos('} basura {"palabra": "a"}'),
                         [{"palabra": "a"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
