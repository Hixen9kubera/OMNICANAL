"""Pruebas de `categorias_arbol`: la búsqueda de categorías de ML por texto.

── QUÉ SE FIJA AQUÍ ────────────────────────────────────────────────────────────
La regla que decide qué categorías ve una persona en el picker del Estudio.
Nace de casos medidos en septiembre de 2026 que el predictor de ML
(`domain_discovery`) no resolvía: "lavabo" nunca devolvía Hogar › Baños ›
Lavabos, "bocinas" nunca devolvía Audio › Bocinas, y los "Otros" de
cosmetología eran inalcanzables. La coincidencia contra el árbol real se
prueba en el sandbox; aquí va lo que se puede fijar sin base ni red:

  · `normalizar` — la ÚNICA normalización, compartida por el cargador y el
    buscador. Si divergen, la búsqueda falla en silencio.
  · `terminos` — vacías fuera, plural a raíz.
  · `filas_desde_arbol` — del JSON de ML a las columnas de la 0059.
  · `buscar` — que el SQL exija TODAS las palabras y solo ofrezca publicables.
  · `mezclar` — el orden final: primero el árbol, después lo que solo sugiere ML.

    cd backend && python -m unittest tests.test_categorias_arbol -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import categorias_arbol as ca  # noqa: E402


def _nodo(cid, nombre, ruta, hijos=0, publicable=True, items=10, dominio="MLM-X"):
    """Un nodo con la forma de GET /sites/MLM/categories/all."""
    return {
        "id": cid, "name": nombre,
        "path_from_root": [{"id": i, "name": n} for i, n in ruta],
        "children_categories": [{"id": f"H{k}", "name": "h"} for k in range(hijos)],
        "settings": {"listing_allowed": publicable, "catalog_domain": dominio},
        "total_items_in_this_category": items,
    }


class Normalizar(unittest.TestCase):
    def test_acentos_enie_y_mayusculas(self):
        self.assertEqual(ca.normalizar("Hogar, Muebles y Jardín › Baños"),
                         "hogar muebles y jardin banos")

    def test_puntuacion_se_vuelve_espacio_unico(self):
        self.assertEqual(ca.normalizar("  Toallas, Toallones  y Batas/Kits  "),
                         "toallas toallones y batas kits")

    def test_vacio_y_none(self):
        self.assertEqual(ca.normalizar(None), "")
        self.assertEqual(ca.normalizar("¡¿…?!"), "")


class Terminos(unittest.TestCase):
    def test_quita_vacias(self):
        self.assertEqual(ca.terminos("Lavabos para Baño"), ["lavabo", "bano"])

    def test_plural_a_raiz(self):
        # 'motores' debe encontrar "Motor"; 'soportes' sigue encontrando "Soportes".
        self.assertEqual(ca.terminos("motores soportes vehiculos"),
                         ["motor", "soport", "vehiculo"])

    def test_palabras_cortas_no_se_recortan(self):
        self.assertEqual(ca.terminos("gas tres"), ["gas", "tre"])
        self.assertEqual(ca.terminos("bus"), ["bus"])

    def test_sin_repetidos_y_con_tope(self):
        self.assertEqual(ca.terminos("bocina bocinas BOCINA"), ["bocina"])
        largo = " ".join(f"palabra{i}" for i in range(20))
        self.assertEqual(len(ca.terminos(largo)), ca._MAX_TERMINOS)

    def test_solo_vacias_no_busca(self):
        self.assertEqual(ca.terminos("de para y"), [])


class FilasDesdeArbol(unittest.TestCase):
    def test_hoja_publicable(self):
        arbol = {"MLM31513": _nodo(
            "MLM31513", "Lavabos para Baño",
            [("MLM1574", "Hogar, Muebles y Jardín"), ("MLM1613", "Baños"),
             ("MLM31513", "Lavabos para Baño")], items=15377, dominio="MLM-BATHROOM_SINKS")}
        filas, stats = ca.filas_desde_arbol(arbol)
        (cid, nombre, padre, raiz, ids, nombres, hoja, publicable, items,
         dominio, name_norm, path_norm), = filas
        self.assertEqual((cid, nombre, padre, raiz), ("MLM31513", "Lavabos para Baño",
                                                      "MLM1613", "MLM1574"))
        self.assertEqual(ids, ["MLM1574", "MLM1613", "MLM31513"])
        self.assertTrue(hoja and publicable)
        self.assertEqual((items, dominio), (15377, "MLM-BATHROOM_SINKS"))
        self.assertEqual(name_norm, "lavabos para bano")
        self.assertEqual(path_norm, "hogar muebles y jardin banos lavabos para bano")
        self.assertEqual(stats["ruta_corregida"], 0)

    def test_rama_no_publicable(self):
        arbol = {"MLM455802": _nodo(
            "MLM455802", "Equipos de Cosmetología",
            [("MLM1246", "Belleza"), ("MLM455802", "Equipos de Cosmetología")],
            hijos=6, publicable=False)}
        (fila,), _ = ca.filas_desde_arbol(arbol)
        self.assertFalse(fila[6])  # is_leaf
        self.assertFalse(fila[7])  # listing_allowed

    def test_raiz_sin_padre(self):
        arbol = {"MLM1000": _nodo("MLM1000", "Electrónica", [("MLM1000", "Electrónica")], hijos=3)}
        (fila,), _ = ca.filas_desde_arbol(arbol)
        self.assertIsNone(fila[2])
        self.assertEqual(fila[3], "MLM1000")

    def test_ruta_sin_la_propia_categoria_se_completa_y_se_cuenta(self):
        # El CHECK de la 0059 exige que la ruta termine en la categoría.
        arbol = {"MLM9": _nodo("MLM9", "Hoja", [("MLM1", "Raíz")])}
        (fila,), stats = ca.filas_desde_arbol(arbol)
        self.assertEqual(fila[4], ["MLM1", "MLM9"])
        self.assertEqual(stats["ruta_corregida"], 1)

    def test_nombres_con_espacios_sobrantes(self):
        # ML trae "Deportes y Fitness " con un espacio al final.
        arbol = {"MLM2": _nodo("MLM2", "Termos", [("MLM1276", "Deportes y Fitness "), ("MLM2", "Termos")])}
        (fila,), _ = ca.filas_desde_arbol(arbol)
        self.assertEqual(fila[5], ["Deportes y Fitness", "Termos"])


class Buscar(unittest.TestCase):
    def _sql(self, q, limite=8):
        with mock.patch.object(ca, "_leer", return_value=[]) as leer:
            ca.buscar(q, limite)
        return leer.call_args[0] if leer.called else None

    def test_exige_todas_las_palabras_y_solo_publicables(self):
        sql, params = self._sql("otros cosmetología")
        self.assertIn("where listing_allowed and path_norm like %(t0)s and path_norm like %(t1)s", sql)
        self.assertEqual((params["t0"], params["t1"]), ("%otro%", "%cosmetologia%"))
        self.assertEqual(params["lim"], 8)

    def test_ordena_por_palabras_en_el_nombre_y_despues_volumen(self):
        sql, _ = self._sql("soportes vehiculos")
        self.assertIn("order by ((name_norm like %(t0)s)::int + (name_norm like %(t1)s)::int) desc, "
                      "total_items desc nulls last", sql)

    def test_sin_terminos_no_consulta(self):
        self.assertIsNone(self._sql("de para"))

    def test_resultado_con_ruta_legible(self):
        fila = {"category_id": "MLM31513", "name": "Lavabos para Baño", "listing_allowed": True,
                "path_names": ["Hogar, Muebles y Jardín", "Baños", "Lavabos para Baño"]}
        with mock.patch.object(ca, "_leer", return_value=[fila]):
            (r,) = ca.buscar("lavabo")
        self.assertEqual(r["path"], "Hogar, Muebles y Jardín > Baños > Lavabos para Baño")
        self.assertTrue(r["publicable"])

    def test_sin_base_devuelve_vacio_sin_reventar(self):
        with mock.patch("services.supabase_db.disponible", return_value=False):
            self.assertEqual(ca.buscar("lavabo"), [])

    def test_error_de_base_devuelve_vacio_sin_reventar(self):
        with mock.patch("services.supabase_db.disponible", return_value=True), \
             mock.patch("services.supabase_db.fetch_all", side_effect=RuntimeError("no existe")):
            self.assertEqual(ca.buscar("lavabo"), [])


class Mezclar(unittest.TestCase):
    ARBOL = [
        {"category_id": "MLM189323", "name": "Lavabos para Baño", "path": "Construcción > …",
         "domain": "", "publicable": True},
        {"category_id": "MLM31513", "name": "Lavabos para Baño", "path": "Hogar > Baños > …",
         "domain": "", "publicable": True},
    ]
    SUGERIDAS = [
        {"category_id": "MLM189323", "name": "Lavabos para Baño", "path": "Construcción > …",
         "domain": "Lavabos para baño", "publicable": True},
        {"category_id": "MLM455948", "name": "Lavabos para Baño", "path": "Náuticos > …",
         "domain": "Lavabos para baño", "publicable": True},
    ]

    def test_primero_arbol_despues_solo_sugeridas(self):
        ids = [r["category_id"] for r in ca.mezclar(self.ARBOL, self.SUGERIDAS)]
        self.assertEqual(ids, ["MLM189323", "MLM31513", "MLM455948"])

    def test_marca_sugerida_y_toma_el_dominio_legible(self):
        a, b, c = ca.mezclar(self.ARBOL, self.SUGERIDAS)
        self.assertEqual((a["sugerida"], a["domain"]), (True, "Lavabos para baño"))
        self.assertEqual((b["sugerida"], b["domain"]), (False, ""))
        self.assertTrue(c["sugerida"])

    def test_sin_arbol_es_el_comportamiento_de_antes(self):
        self.assertEqual([r["category_id"] for r in ca.mezclar([], self.SUGERIDAS)],
                         ["MLM189323", "MLM455948"])


if __name__ == "__main__":
    unittest.main()
