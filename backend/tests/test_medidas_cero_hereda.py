"""Peso y medidas de la variante: el cero es hueco y el padre aporta su PAQUETE.

── POR QUÉ ESTA PRUEBA ────────────────────────────────────────────────────────
Eduardo, 2-nov-2026: «que el 0 herede del padre en medidas». Medido ese día:
282 de los 548 publicados que el candado de medidas del alta de ML frenaba eran
variantes con '0', '0.0' o '0.000' propio y el padre con el peso o las medidas
reales. La herencia solo rellenaba metas VACÍAS, así que el cero tapaba lo del
padre. Heredando clave por clave, 150 de las 159 que se destrababan juntaban el
PESO del padre con una CAJA propia distinta; por eso Eduardo pidió «herencia en
bloque» (`wp_db.medidas_del_padre`).

Lo que fija:

1. `_es_hueco`: vacío siempre es hueco; en las cuatro medidas (`_weight`,
   `_length`, `_width`, `_height`) también todo número que no sea real > 0. En
   un precio o en el stock el cero significa algo y no se toca. Un texto que no
   es número no es hueco: el candado lo reporta.
2. El PAQUETE: sin ninguna medida real la variante toma las del padre juntas;
   con su caja y sin peso, el peso del padre SOLO si la caja es la misma; en
   cualquier otro caso, nada.
3. Las TRES herencias usan la misma regla: la del alta
   (`postmeta_con_herencia`, que lee `construir_prod`), la que prellena el
   Estudio (`metadata_producto`) y la del semáforo del Publicador
   (`precios_y_costo_por_wc_id`). Si una se queda atrás, el Estudio o la fila
   dicen una cosa y el alta hace otra.
4. Un cero del padre no rellena nada, y la variante sin dato real sigue frenando.

Sin red: WordPress va simulado.

    cd backend && python -m unittest tests.test_medidas_cero_hereda -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import wp_db  # noqa: E402
from services.publicar_ready import faltan_medidas_ml  # noqa: E402

VARIANTE, PADRE = 50, 40
MEDIDAS_PADRE = {"_weight": "1.2", "_length": "30", "_width": "20", "_height": "10"}


class EsHueco(unittest.TestCase):
    def test_vacio_siempre_es_hueco(self):
        for clave in ("_weight", "_price", "ml_categoria_id"):
            for valor in (None, "", "   "):
                self.assertTrue(wp_db._es_hueco(clave, valor), (clave, valor))

    def test_el_cero_es_hueco_solo_en_las_medidas(self):
        for clave in ("_weight", "_length", "_width", "_height"):
            for valor in ("0", "0.0", "0.000", " 0 ", 0, 0.0):
                self.assertTrue(wp_db._es_hueco(clave, valor), (clave, valor))
        for clave in ("_regular_price", "_price", "_sale_price", "costo"):
            self.assertFalse(wp_db._es_hueco(clave, "0"), clave)

    def test_un_valor_real_o_un_texto_no_es_hueco(self):
        for valor in ("0.5", "12", "abc", "1,5"):
            self.assertFalse(wp_db._es_hueco("_weight", valor), valor)

    def test_un_numero_que_no_es_real_es_hueco_en_las_medidas(self):
        for valor in ("-1", "-0", "nan", "inf", "-inf"):
            self.assertTrue(wp_db._es_hueco("_weight", valor), valor)


CAJA_GRANDE = {"_length": "75.5", "_width": "58.5", "_height": "37"}


class ElPaquete(unittest.TestCase):
    """`medidas_del_padre`: qué toma la variante, siempre como paquete."""

    def test_sin_ninguna_medida_real_toma_las_del_padre_juntas(self):
        self.assertEqual(wp_db.medidas_del_padre({"_weight": "0.0", "_length": "0"}, MEDIDAS_PADRE),
                         MEDIDAS_PADRE)

    def test_caja_propia_distinta_y_peso_cero_no_toma_nada(self):
        # El caso que domina en los datos (ORG-0798-NEG-CUA): su caja con el peso
        # de la pieza del padre sería una pareja que no existe.
        self.assertEqual(wp_db.medidas_del_padre(dict(CAJA_GRANDE, _weight="0.0"), MEDIDAS_PADRE), {})

    def test_misma_caja_en_otro_orden_toma_el_peso(self):
        propias = {"_weight": "0", "_length": "10.00", "_width": "30", "_height": "20"}
        self.assertEqual(wp_db.medidas_del_padre(propias, MEDIDAS_PADRE), {"_weight": "1.2"})

    def test_peso_propio_y_caja_vacia_no_toma_la_caja(self):
        self.assertEqual(wp_db.medidas_del_padre({"_weight": "0.4"}, MEDIDAS_PADRE), {})

    def test_un_texto_propio_no_se_tapa(self):
        self.assertEqual(wp_db.medidas_del_padre({"_weight": "abc"}, MEDIDAS_PADRE), {})

    def test_completa_no_toma_nada_y_padre_sin_datos_no_da_nada(self):
        self.assertEqual(wp_db.medidas_del_padre(dict(CAJA_GRANDE, _weight="2"), MEDIDAS_PADRE), {})
        self.assertEqual(wp_db.medidas_del_padre({}, {"_weight": "0", "_length": ""}), {})


class HerenciaDelAlta(unittest.TestCase):
    """`postmeta_con_herencia`: lo que lee `construir_prod` para el alta."""

    def _correr(self, propias, del_padre):
        tablas = {VARIANTE: propias, PADRE: del_padre}
        with mock.patch.object(wp_db, "postmeta_todo", side_effect=lambda i: dict(tablas[i])), \
                mock.patch.object(wp_db, "padre_de", side_effect=lambda i: PADRE if i == VARIANTE else None):
            return wp_db.postmeta_con_herencia(VARIANTE)

    def test_los_ceros_de_la_variante_toman_las_medidas_del_padre(self):
        metas, heredadas = self._correr(
            {"_weight": "0.0", "_length": "0.000", "_width": "0", "_height": "", "_regular_price": "99"},
            dict(MEDIDAS_PADRE, _regular_price="50"))
        for k, v in MEDIDAS_PADRE.items():
            self.assertEqual(metas[k], v, k)
        self.assertTrue(set(MEDIDAS_PADRE) <= heredadas)
        self.assertEqual(metas["_regular_price"], "99")
        self.assertIsNone(faltan_medidas_ml({"weight": metas["_weight"], "length": metas["_length"],
                                             "width": metas["_width"], "height": metas["_height"]}))

    def test_una_medida_propia_real_sigue_ganando_y_no_se_mezcla(self):
        metas, heredadas = self._correr({"_weight": "0.4"}, MEDIDAS_PADRE)
        self.assertEqual(metas["_weight"], "0.4")
        self.assertFalse(heredadas & set(MEDIDAS_PADRE))
        self.assertNotIn("_length", metas)

    def test_caja_propia_y_peso_cero_sigue_frenando(self):
        metas, heredadas = self._correr(dict(CAJA_GRANDE, _weight="0.0"), MEDIDAS_PADRE)
        self.assertEqual(metas["_weight"], "0.0")
        self.assertFalse(heredadas & set(MEDIDAS_PADRE))
        self.assertIsNotNone(faltan_medidas_ml({"weight": metas["_weight"], "length": metas["_length"],
                                                "width": metas["_width"], "height": metas["_height"]}))

    def test_el_cero_de_un_precio_no_se_hereda(self):
        metas, heredadas = self._correr({"_sale_price": "0"}, {"_sale_price": "80"})
        self.assertEqual(metas["_sale_price"], "0")
        self.assertNotIn("_sale_price", heredadas)

    def test_un_cero_del_padre_no_rellena_y_la_variante_sigue_frenando(self):
        metas, heredadas = self._correr({"_weight": "0.0"}, {"_weight": "0", "_length": "30",
                                                             "_width": "20", "_height": "10"})
        self.assertEqual(metas["_weight"], "0.0")
        self.assertNotIn("_weight", heredadas)
        self.assertIsNotNone(faltan_medidas_ml({"weight": metas["_weight"], "length": metas["_length"],
                                                "width": metas["_width"], "height": metas["_height"]}))


class HerenciaDelEstudio(unittest.TestCase):
    """`metadata_producto`: lo que el Estudio prellena tiene que ser lo que el alta lee."""

    def test_el_estudio_prellena_las_medidas_del_padre_y_las_marca(self):
        tablas = {VARIANTE: {"_weight": "0.000", "_length": "0"}, PADRE: dict(MEDIDAS_PADRE)}
        with mock.patch.object(wp_db, "postmeta", side_effect=lambda i, claves: dict(tablas[i])), \
                mock.patch.object(wp_db, "padre_de", side_effect=lambda i: PADRE if i == VARIANTE else None), \
                mock.patch.object(wp_db, "_atributos_de_variacion", return_value=[]):
            m = wp_db.metadata_producto(VARIANTE)
        self.assertEqual(m["dinero"]["peso"], 1.2)
        self.assertEqual(m["dinero"]["largo"], 30.0)
        self.assertIn("_weight", m["heredado"])
        self.assertIn("_length", m["heredado"])

    def test_el_estudio_no_mezcla_el_peso_del_padre_con_la_caja_propia(self):
        tablas = {VARIANTE: dict(CAJA_GRANDE, _weight="0.0"), PADRE: dict(MEDIDAS_PADRE)}
        with mock.patch.object(wp_db, "postmeta", side_effect=lambda i, claves: dict(tablas[i])), \
                mock.patch.object(wp_db, "padre_de", side_effect=lambda i: PADRE if i == VARIANTE else None), \
                mock.patch.object(wp_db, "_atributos_de_variacion", return_value=[]):
            m = wp_db.metadata_producto(VARIANTE)
        self.assertEqual(m["dinero"]["peso"], 0.0)
        self.assertEqual(m["dinero"]["largo"], 75.5)
        self.assertNotIn("_weight", m["heredado"])


class HerenciaDelSemaforo(unittest.TestCase):
    """`precios_y_costo_por_wc_id`: las metas del semáforo del Publicador."""

    def test_la_fila_de_la_variante_ve_las_medidas_del_padre(self):
        filas = [{"post_id": VARIANTE, "meta_key": k, "meta_value": "0.0"} for k in MEDIDAS_PADRE]
        filas += [{"post_id": PADRE, "meta_key": k, "meta_value": v} for k, v in MEDIDAS_PADRE.items()]
        filas.append({"post_id": VARIANTE, "meta_key": "_price", "meta_value": "0"})

        def _fetch(sql, args=None):
            return [f for f in filas if f["post_id"] in (args or ())]

        with mock.patch.object(wp_db, "_fetch_all", side_effect=_fetch):
            salida = wp_db.precios_y_costo_por_wc_id([{"wc_id": VARIANTE, "tipo": "variation", "padre": PADRE}])
        pub = salida[VARIANTE]["publicar"]
        for k, v in MEDIDAS_PADRE.items():
            self.assertEqual(pub[k], v, k)
        self.assertEqual(salida[VARIANTE]["precio"], 0.0)   # el precio no se toca

    def test_la_fila_no_mezcla_el_peso_del_padre_con_la_caja_propia(self):
        filas = [{"post_id": VARIANTE, "meta_key": k, "meta_value": v}
                 for k, v in dict(CAJA_GRANDE, _weight="0.0").items()]
        filas += [{"post_id": PADRE, "meta_key": k, "meta_value": v} for k, v in MEDIDAS_PADRE.items()]

        def _fetch(sql, args=None):
            return [f for f in filas if f["post_id"] in (args or ())]

        with mock.patch.object(wp_db, "_fetch_all", side_effect=_fetch):
            salida = wp_db.precios_y_costo_por_wc_id([{"wc_id": VARIANTE, "tipo": "variation", "padre": PADRE}])
        self.assertEqual(salida[VARIANTE]["publicar"]["_weight"], "0.0")
        self.assertEqual(salida[VARIANTE]["publicar"]["_length"], "75.5")


if __name__ == "__main__":
    unittest.main()
