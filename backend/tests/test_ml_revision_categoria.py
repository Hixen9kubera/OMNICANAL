"""Pruebas de la DOBLE VALIDACIÓN de la categoría antes de un alta en Mercado Libre
(`services/ml_revision_categoria.py`, Brandon 21-oct-2026: ML acepta categorías
equivocadas sin quejarse).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **El veredicto**: coincide (entre las propuestas de ML), parecida (mismo padre),
   distinta (otra rama), sin_sugerencia, no_publicable (una rama), sin_categoria.
2. **El candado**: sin categoría confirmada, o con una distinta de la que sale,
   `publicar_ready.crear_ml` NO llama al pipeline; con la correcta, sí.
3. **La categoría que sale** es la regla del vendor: la meta, salvo `override` del
   mapeo de Woo — y sin categorías de Woo no se baja nada por la red.
4. **El origen**: panel (elección humana) > crear (predictor) > woo.
5. `revisar` arma ruta y propuestas con lo que contesta ML, y no revienta si ML falla.

Sin red: httpx se sustituye por un transporte falso.

    cd backend && python -m unittest tests.test_ml_revision_categoria -v
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

import httpx

from services import ml_revision_categoria as rc


def _cat(cid, padre_ids, publicable=True):
    niveles = [{"id": i, "name": f"N{i}"} for i in padre_ids] + [{"id": cid, "name": f"N{cid}"}]
    return {"category_id": cid, "name": f"N{cid}", "ruta": " > ".join(n["name"] for n in niveles),
            "niveles": niveles, "publicable": publicable}


class Veredicto(unittest.TestCase):
    def test_coincide_si_esta_entre_las_propuestas(self):
        self.assertEqual(rc.veredicto(_cat("MLM3", ["MLM1"]), [_cat("MLM9", ["MLM8"]), _cat("MLM3", ["MLM1"])]),
                         "coincide")

    def test_parecida_si_comparte_el_padre(self):
        self.assertEqual(rc.veredicto(_cat("MLM3", ["MLM1", "MLM2"]), [_cat("MLM4", ["MLM1", "MLM2"])]),
                         "parecida")

    def test_distinta_si_solo_comparte_el_abuelo(self):
        self.assertEqual(rc.veredicto(_cat("MLM3", ["MLM1", "MLM2"]), [_cat("MLM4", ["MLM1", "MLM7"])]),
                         "distinta")

    def test_sin_sugerencia(self):
        self.assertEqual(rc.veredicto(_cat("MLM3", ["MLM1"]), []), "sin_sugerencia")

    def test_una_rama_no_es_publicable_aunque_coincida(self):
        self.assertEqual(rc.veredicto(_cat("MLM3", ["MLM1"], publicable=False), [_cat("MLM3", ["MLM1"])]),
                         "no_publicable")

    def test_sin_categoria_lo_dice(self):
        self.assertEqual(rc.veredicto(None, [_cat("MLM3", ["MLM1"])]), "sin_categoria")
        self.assertEqual(rc.veredicto(None, []), "sin_categoria")


class Candado(unittest.TestCase):
    def test_sin_confirmar_no_publica(self):
        self.assertEqual(rc.motivo_sin_confirmar(None, "MLM3"), rc.MSG_SIN_CONFIRMAR)
        self.assertEqual(rc.motivo_sin_confirmar("  ", "MLM3"), rc.MSG_SIN_CONFIRMAR)

    def test_otra_categoria_no_publica_y_dice_cuales(self):
        m = rc.motivo_sin_confirmar("MLM3", "MLM4")
        self.assertIn("MLM3", m)
        self.assertIn("MLM4", m)

    def test_sin_categoria_dice_eso_y_no_pide_confirmar(self):
        self.assertEqual(rc.motivo_sin_confirmar("", ""), rc.MSG_SIN_CATEGORIA)
        self.assertEqual(rc.motivo_sin_confirmar("MLM3", None), rc.MSG_SIN_CATEGORIA)

    def test_la_misma_pasa(self):
        self.assertIsNone(rc.motivo_sin_confirmar("mlm3", "MLM3"))


class CategoriaDeAlta(unittest.TestCase):
    def test_con_meta_y_sin_categorias_woo_es_la_meta_sin_red(self):
        with mock.patch("vendor.ml_ready.wc_category_mapping.load_mapping",
                        side_effect=AssertionError("no debe bajar el mapeo")):
            self.assertEqual(rc.categoria_de_alta({"ml_category_id": "MLM3", "wc_categories": []}), "MLM3")

    def test_el_mapeo_de_woo_pisa_cuando_dice_override(self):
        with mock.patch("vendor.ml_ready.wc_category_mapping.load_mapping", return_value={7: "MLM9"}):
            self.assertEqual(rc.categoria_de_alta({"ml_category_id": "", "wc_categories": [{"id": 7}]}), "MLM9")

    def test_sin_nada_es_vacia(self):
        self.assertEqual(rc.categoria_de_alta({"ml_category_id": "", "wc_categories": []}), "")


class Origen(unittest.TestCase):
    def test_panel_manda(self):
        prod = {"meta": {"ml_categoria_id": "MLM3", "ml_category_id": "MLM3"}}
        self.assertEqual(rc.origen(prod, "MLM3"), "panel")

    def test_predictor_de_crear(self):
        self.assertEqual(rc.origen({"meta": {"ml_category_id": "MLM3"}}, "MLM3"), "crear")

    def test_mapeo_de_woo(self):
        self.assertEqual(rc.origen({"meta": {}}, "MLM9"), "woo")

    def test_sin_categoria_no_culpa_a_woo(self):
        self.assertEqual(rc.origen({"meta": {"ml_category_id": ""}}, ""), "ninguna")
        self.assertIn("ninguna", rc.ORIGENES)


def _transporte(rutas: dict):
    def manejar(req: httpx.Request) -> httpx.Response:
        clave = req.url.path
        if clave in rutas:
            r = rutas[clave]
            if isinstance(r, Exception):
                raise r
            return httpx.Response(200, json=r)
        return httpx.Response(404, json={})
    return httpx.MockTransport(manejar)


def _cat_ml(cid, ruta_ids, publicable=True):
    return {"id": cid, "name": f"N{cid}",
            "path_from_root": [{"id": i, "name": f"N{i}"} for i in ruta_ids + [cid]],
            "settings": {"listing_allowed": publicable}}


class Revisar(unittest.TestCase):
    def setUp(self):
        rc._cache_cat.clear()
        self._cliente = httpx.AsyncClient

    def _con(self, rutas):
        transporte = _transporte(rutas)
        original = self._cliente
        return mock.patch.object(rc.httpx, "AsyncClient",
                                 lambda **kw: original(transport=transporte, **kw))

    def test_arma_la_ruta_y_las_propuestas(self):
        rutas = {
            "/categories/MLM3": _cat_ml("MLM3", ["MLM1", "MLM2"]),
            "/categories/MLM4": _cat_ml("MLM4", ["MLM1", "MLM2"]),
            "/sites/MLM/domain_discovery/search": [
                {"category_id": "MLM4", "category_name": "N4", "domain_name": "Peluches"}],
        }
        with self._con(rutas):
            r = asyncio.run(rc.revisar("MLM3", "Peluche de oso"))
        self.assertEqual(r["actual"]["ruta"], "NMLM1 > NMLM2 > NMLM3")
        self.assertEqual([s["category_id"] for s in r["sugeridas"]], ["MLM4"])
        self.assertEqual(r["sugeridas"][0]["dominio"], "Peluches")
        self.assertEqual(r["veredicto"], "parecida")

    def test_ml_caido_no_revienta(self):
        rutas = {"/categories/MLM3": httpx.ConnectError("caído"),
                 "/sites/MLM/domain_discovery/search": httpx.ConnectError("caído")}
        with self._con(rutas):
            r = asyncio.run(rc.revisar("MLM3", "Peluche"))
        self.assertEqual(r["actual"]["category_id"], "MLM3")
        self.assertTrue(r["actual"].get("ilegible"))
        self.assertEqual(r["sugeridas"], [])
        self.assertEqual(r["veredicto"], "sin_sugerencia")


class CrearMlExigeConfirmacion(unittest.TestCase):
    """El candado vive en el embudo de toda alta: `publicar_ready.crear_ml`."""

    def _crear(self, confirmada):
        from services import publicar_ready as pr
        prod = {"sku": "X-1", "ml_category_id": "MLM3", "wc_categories": [], "stock": 5,
                "title": "t", "price": 10.0}
        llamadas = []

        def publicar(p, token, dry, cuenta):
            llamadas.append(cuenta)
            return {"success": False, "error": "prueba"}

        with mock.patch.object(pr, "configurar"), \
             mock.patch.object(pr, "_candado_alta_ml", mock.AsyncMock(return_value=None)), \
             mock.patch.object(pr, "construir_prod", return_value=prod), \
             mock.patch.object(pr, "_stock_real", return_value=5), \
             mock.patch.object(pr.meli, "_access_token", return_value="tok"), \
             mock.patch.object(pr.publisher_core, "publish_product", side_effect=publicar), \
             mock.patch.object(pr, "MAX_INTENTOS_ML", 1):
            r = asyncio.run(pr.crear_ml("X-1", 1, {}, ["BEKURA"], categoria_confirmada=confirmada))
        return r, llamadas

    def test_sin_confirmar_no_llega_al_pipeline(self):
        r, llamadas = self._crear(None)
        self.assertFalse(r["ok"])
        self.assertEqual(r["motivo"], rc.MSG_SIN_CONFIRMAR)
        self.assertEqual(r["resultados"][0]["error"], rc.MSG_SIN_CONFIRMAR)
        self.assertEqual(llamadas, [])

    def test_confirmada_otra_no_llega_al_pipeline(self):
        r, llamadas = self._crear("MLM9")
        self.assertFalse(r["ok"])
        self.assertEqual(llamadas, [])

    def test_confirmada_la_que_sale_si_llega(self):
        r, llamadas = self._crear("MLM3")
        self.assertEqual(llamadas, ["BEKURA"])


if __name__ == "__main__":
    unittest.main()
