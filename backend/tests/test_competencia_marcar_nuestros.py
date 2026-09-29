"""Nuestras publicaciones tienen que salir como NUESTRAS en una búsqueda.

Caso real (29-sep-2026): la búsqueda de ML devuelve el PRODUCTO DE VENDEDOR
(`MLMU…`), no el item. `_marcar` comparaba solo ese id crudo contra nuestros ids de
publicación y nunca coincidía: el kit de doctor de BEKURA a $159 era el #2 «de la
competencia» y entraba a la mediana del Radar.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import competencia_captura as cc  # noqa: E402

NUESTRAS = {"MLM5741078908": {"sku": "JUGU-0268-ROS", "cuenta": "BEKURA"}}


class Marcar(unittest.TestCase):
    def test_item_directo(self):
        filas = [{"externo_id": "MLM5741078908"}]
        cc._marcar(filas, NUESTRAS)
        self.assertEqual((filas[0].get("es_nuestro"), filas[0].get("sku_nuestro")),
                         (True, "JUGU-0268-ROS"))

    def test_producto_de_vendedor_resuelto(self):
        filas = [{"externo_id": "MLMU4358094486", "item_real": "MLM5741078908"}]
        cc._marcar(filas, NUESTRAS)
        self.assertTrue(filas[0].get("es_nuestro"))
        self.assertEqual(filas[0].get("sku_nuestro"), "JUGU-0268-ROS")

    def test_ajeno_sigue_ajeno(self):
        filas = [{"externo_id": "MLMU999", "item_real": "MLM111"}, {"externo_id": "MLM222"}]
        cc._marcar(filas, NUESTRAS)
        self.assertFalse(any(f.get("es_nuestro") for f in filas))


class EnriquecerDejaElItemReal(unittest.TestCase):
    def test_mlmu_queda_con_su_item_real_y_se_marca(self):
        filas = [{"externo_id": "MLMU4358094486", "url": "https://www.mercadolibre.com.mx/x/up/MLMU4358094486"},
                 {"externo_id": "MLM333", "url": "https://articulo.mercadolibre.com.mx/MLM-333-x"}]
        with mock.patch.object(cc.competencia_ml, "competidores_de_producto",
                               return_value=[{"externo_id": "MLM5741078908"}]) as cp, \
             mock.patch.object(cc.competencia_ml, "visitas_30d", return_value=10):
            asyncio.run(cc.enriquecer_visitas(filas))
        cp.assert_called_once_with("MLMU4358094486", 1)
        self.assertEqual(filas[0].get("item_real"), "MLM5741078908")
        self.assertNotIn("item_real", filas[1])          # un item directo no se toca
        cc._marcar(filas, NUESTRAS)
        self.assertTrue(filas[0].get("es_nuestro"))
        self.assertFalse(filas[1].get("es_nuestro"))

    def test_item_real_no_se_guarda_en_la_base(self):
        from services import competencia_supabase as cs
        self.assertNotIn("item_real", cs._COLS_SERP)


class Candados(unittest.TestCase):
    def test_catalogo_no_se_marca_aunque_resuelva_a_nuestro(self):
        filas = [{"externo_id": "MLM2039953812", "url": "https://www.mercadolibre.com.mx/pista/p/MLM2039953812"}]
        with mock.patch.object(cc.competencia_ml, "competidores_de_producto",
                               return_value=[{"externo_id": "MLM5741078908"}]),              mock.patch.object(cc.competencia_ml, "visitas_30d", return_value=10):
            asyncio.run(cc.enriquecer_visitas(filas))
        self.assertNotIn("item_real", filas[0])
        cc._marcar(filas, NUESTRAS)
        self.assertFalse(filas[0].get("es_nuestro"))

    def test_llave_vacia_nunca_es_nuestra(self):
        nuestras = dict(NUESTRAS, **{"": {"sku": "X", "cuenta": "BEKURA"}})
        filas = [{"externo_id": ""}, {"externo_id": None}, {}, {"externo_id": "MLMU1", "item_real": ""}]
        cc._marcar(filas, nuestras)
        self.assertFalse(any(f.get("es_nuestro") for f in filas))


if __name__ == "__main__":
    unittest.main()
