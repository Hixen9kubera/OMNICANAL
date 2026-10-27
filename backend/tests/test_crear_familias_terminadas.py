"""
Lo que ya pasó por Crear no se vuelve a enseñar en Crear (Brandon, 27-oct-2026):

  · un padre cuyas variantes vivas YA pasaron TODAS por Crear sale del índice
    «Por padre» (`wp_db.indice_drafts(..., sin_familias_terminadas=True)`);
  · debajo de un padre que sigue, las variantes ya procesadas no se enseñan, y el
    stock y el valor son de lo que falta (`creacion.items_candidatos`).

Medido ese día contra WordPress: «Por padre» pasó de 3,774 a 3,765 (las 9
familias terminadas: ROP-0246 23/23, VAR-0004 15/15…), las 3 a medias se
quedaron con sus 12 variantes hechas ocultas, y «Por variante» no cambió (6,628).

    cd backend && python -m unittest tests.test_crear_familias_terminadas -v
"""
import unittest
from unittest import mock

from services import creacion, wp_db


class IndiceDrafts(unittest.TestCase):
    def _sql(self, **kw):
        llamadas = []
        with mock.patch.object(wp_db, "_fetch_all", side_effect=lambda sql, *a: llamadas.append(sql) or []), \
                mock.patch.object(wp_db, "_categorias_por_post", return_value={}):
            wp_db.indice_drafts(**kw)
        return llamadas[0]

    def test_por_omision_no_filtra_familias(self):
        # `variables.agrupar*` lee este mismo índice: ahí el padre sí cuenta.
        self.assertNotIn(wp_db.META_PROCESADA, self._sql(aplanar=False))

    def test_con_la_bandera_saca_a_la_familia_terminada(self):
        sql = self._sql(aplanar=False, sin_familias_terminadas=True)
        self.assertIn(f"AND NOT {wp_db._familia_terminada('p')}", sql)

    def test_familia_terminada_exige_hijas_y_ninguna_sin_marca(self):
        expr = wp_db._familia_terminada("p")
        self.assertTrue(expr.startswith("(EXISTS ("))
        self.assertIn("AND NOT EXISTS (", expr)
        self.assertIn(f"'{wp_db.META_PROCESADA}'", expr)
        self.assertIn("IS NULL", expr)  # mismo criterio que `_estado_efectivo`
        self.assertIn("h.post_status <> 'trash'", expr)

    def test_por_variante_no_cambia(self):
        self.assertEqual(self._sql(aplanar=True),
                         self._sql(aplanar=True, sin_familias_terminadas=True))


class CrearPideElIndiceSinFamiliasTerminadas(unittest.IsolatedAsyncioTestCase):
    async def test_listar_pasa_la_bandera(self):
        indice = mock.Mock(return_value=[])
        with mock.patch.object(wp_db, "disponible", return_value=True), \
                mock.patch.object(wp_db, "indice_drafts", indice), \
                mock.patch.object(creacion, "_costos_por_sku", return_value={}):
            await creacion.listar_candidatos_agrupados(aplanar=False)
        indice.assert_called_once_with(False, sin_familias_terminadas=True)


def _padre(variantes):
    return {"wc_id": 10, "sku": "ROP-0371", "tipo": "variable", "nombre": "Playera",
            "stock": sum(v["stock"] for v in variantes), "variantes": variantes}


class VariantesProcesadasNoSeEnsenan(unittest.IsolatedAsyncioTestCase):
    VARS = [{"sku": "ROP-0371-NEG-M", "stock": 5}, {"sku": "ROP-0371-NEG-L", "stock": 3},
            {"sku": "ROP-0371-GRI-M", "stock": 2}]
    GRUPO = [{"base": "ROP-0371", "costo": 100.0, "valor": 1000.0, "stock": 10,
              "miembros": [{"wc_id": 10, "sku": "ROP-0371", "sufijo": None}]}]

    async def _items(self, procesadas=None, error=None, variantes=None):
        vp = mock.Mock(side_effect=error) if error else mock.Mock(return_value=procesadas or set())
        with mock.patch.object(creacion.woocommerce, "productos_por_wc_id",
                               mock.AsyncMock(return_value=[_padre(variantes or self.VARS)])), \
                mock.patch.object(creacion.sku_contenedor, "activo", return_value=False), \
                mock.patch.object(creacion, "_contenedores_por_sku", return_value={}), \
                mock.patch.object(wp_db, "variantes_procesadas", vp):
            return await creacion.items_candidatos(self.GRUPO)

    async def test_las_procesadas_salen_y_el_stock_es_de_lo_que_falta(self):
        (it,) = await self._items({"ROP-0371-NEG-M"})
        self.assertEqual([v["sku"] for v in it["variantes"]], ["ROP-0371-NEG-L", "ROP-0371-GRI-M"])
        self.assertEqual(it["stock"], 5)
        self.assertEqual(it["valor"], 500.0)

    async def test_la_marca_se_compara_sin_importar_mayusculas(self):
        # `variantes_procesadas` devuelve mayúsculas; Woo puede traer el SKU como se capturó.
        variantes = [{"sku": " rop-0371-neg-l ", "stock": 3}, {"sku": "ROP-0371-GRI-M", "stock": 2}]
        (it,) = await self._items({"ROP-0371-NEG-L"}, variantes=variantes)
        self.assertEqual([v["sku"] for v in it["variantes"]], ["ROP-0371-GRI-M"])

    async def test_familia_entera_procesada_no_se_ensena(self):
        # El índice ya la saca; esto cubre la carrera entre índice y marca.
        self.assertEqual(await self._items({v["sku"] for v in self.VARS}), [])

    async def test_sin_procesadas_queda_igual(self):
        (it,) = await self._items(set())
        self.assertEqual(len(it["variantes"]), 3)
        self.assertEqual(it["stock"], 10)

    async def test_si_la_base_falla_la_lista_sigue_como_antes(self):
        (it,) = await self._items(error=RuntimeError("MySQL caído"))
        self.assertEqual(len(it["variantes"]), 3)


if __name__ == "__main__":
    unittest.main()
