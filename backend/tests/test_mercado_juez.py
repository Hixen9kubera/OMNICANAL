"""v0.649.0 · El precio de mercado de Publicaciones con el juez de rivales.

Con COMPETENCIA_MERCADO_JUEZ encendida, un SKU que tiene AL MENOS UN veredicto
vigente cuenta solo a los rivales «mismo»; los pendientes y los de otra clase
quedan fuera (`n_fuera_juez`). Sin veredictos —o con la bandera apagada— todo
queda como antes. Las marcas de renombre siguen fuera como siempre.
"""
import unittest
from unittest import mock

from services import publicaciones_panel as PP


def _f(sku, precio, titulo="Producto generico", vigente=None, comparable=None):
    return {"sku": sku, "termino": "t", "precio": precio, "titulo": titulo, "seller": "X",
            "capturado_en": None, "juez_vigente": vigente, "juez_comparable": comparable}


FILAS = (
    # A: juzgado, 3 de 5 son el mismo producto
    [_f("A", 100, vigente=True, comparable=True), _f("A", 110, vigente=True, comparable=True),
     _f("A", 120, vigente=True, comparable=True), _f("A", 900, vigente=True, comparable=False),
     _f("A", 15, vigente=True, comparable=False)]
    # B: sin ningún veredicto → como antes
    + [_f("B", 50), _f("B", 60), _f("B", 70), _f("B", 500)]
    # C: juzgado, solo 2 comparables → basta con dos
    + [_f("C", 200, vigente=True, comparable=True), _f("C", 220, vigente=True, comparable=True),
       _f("C", 999, vigente=True, comparable=False)]
    # D: juzgado, 1 comparable → sin referencia
    + [_f("D", 300, vigente=True, comparable=True), _f("D", 10, vigente=True, comparable=False),
       _f("D", 20, vigente=True, comparable=False)]
    # E: un veredicto vigente y dos pendientes (sin veredicto): los pendientes no cuentan
    + [_f("E", 40, vigente=True, comparable=True), _f("E", 44, vigente=True, comparable=True),
       _f("E", 400), _f("E", 420)]
    # F: juzgado, con un rival de marca de renombre que además es «mismo»
    + [_f("F", 80, vigente=True, comparable=True), _f("F", 90, vigente=True, comparable=True),
       _f("F", 700, titulo="Tenis Nike Air Max", vigente=True, comparable=True)]
)


class MercadoConJuez(unittest.TestCase):
    def test_encendido_solo_cuentan_los_mismo(self):
        m = PP.agregar_mercado(FILAS, sin_marcas=True, con_juez=True)
        a = m["A"]
        self.assertEqual((a["n"], a["n_fuera_juez"], a["juez"]), (3, 2, True))
        self.assertEqual((a["promedio"], a["mediana"]), (110.0, 110.0))

    def test_sin_veredictos_queda_como_antes(self):
        m = PP.agregar_mercado(FILAS, sin_marcas=True, con_juez=True)
        b = m["B"]
        self.assertEqual((b["n"], b["n_fuera_juez"], b["juez"]), (4, 0, False))
        self.assertEqual(b["promedio"], (50 + 60 + 70 + 500) / 4)

    def test_dos_comparables_bastan_y_uno_no(self):
        m = PP.agregar_mercado(FILAS, sin_marcas=True, con_juez=True)
        self.assertEqual((m["C"]["n"], m["C"]["promedio"]), (2, 210.0))
        self.assertNotIn("D", m)

    def test_pendientes_no_cuentan_si_ya_hay_juicio(self):
        m = PP.agregar_mercado(FILAS, sin_marcas=True, con_juez=True)
        e = m["E"]
        self.assertEqual((e["n"], e["n_fuera_juez"], e["promedio"]), (2, 2, 42.0))

    def test_las_marcas_siguen_fuera_y_no_se_cuentan_como_juez(self):
        m = PP.agregar_mercado(FILAS, sin_marcas=True, con_juez=True)
        f = m["F"]
        self.assertEqual((f["n"], f["n_marcas"], f["n_fuera_juez"]), (2, 1, 0))

    def test_apagado_es_identico_a_antes(self):
        con = PP.agregar_mercado(FILAS, sin_marcas=True, con_juez=False)
        sin_campos = [{k: v for k, v in f.items() if not k.startswith("juez_")} for f in FILAS]
        antes = PP.agregar_mercado(sin_campos, sin_marcas=True, con_juez=False)
        for sku in antes:
            for k in ("promedio", "mediana", "n", "n_marcas"):
                self.assertEqual(con[sku][k], antes[sku][k], (sku, k))
            self.assertFalse(con[sku]["juez"])
        self.assertEqual(set(con), set(antes))
        self.assertEqual(con["A"]["n"], 5)            # apagado: el juez no filtra

    def test_la_bandera_viene_de_la_configuracion(self):
        with mock.patch.object(PP.settings, "competencia_mercado_juez", True):
            self.assertEqual(PP.agregar_mercado(FILAS, sin_marcas=True)["A"]["n"], 3)
        with mock.patch.object(PP.settings, "competencia_mercado_juez", False):
            self.assertEqual(PP.agregar_mercado(FILAS, sin_marcas=True)["A"]["n"], 5)


if __name__ == "__main__":
    unittest.main()
