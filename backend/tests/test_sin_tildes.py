"""
El título de Mercado Libre va SIN acentos y la ñ se queda (Brandon, 3-nov-2026). Solo ML:
Woo, Temu, TikTok y Walmart conservan su ortografía.

El prompt pide (`sin_tildes.REGLA`) y el código garantiza: el Estudio lo quita de lo que
devuelve la IA, y lo que SALE a ML —el alta armada por el vendor y el PUT de una
publicación viva— va sin acentos venga de donde venga el título.

    cd backend && python -m unittest tests.test_sin_tildes -v
"""
import asyncio
import unittest
from unittest import mock

from services import ia_generadores, publicar, publicar_ready, sin_tildes
from vendor.ml_ready import publisher_core


class Quitar(unittest.TestCase):
    def test_tildes_y_dieresis_fuera(self):
        self.assertEqual(sin_tildes.quitar("Caminadora Eléctrica Plegable, Pingüino Ártico Ú"),
                         "Caminadora Electrica Plegable, Pinguino Artico U")

    def test_la_n_con_tilde_se_queda(self):
        self.assertEqual(sin_tildes.quitar("Tienda de Campaña para Niño, BAÑO, año"),
                         "Tienda de Campaña para Niño, BAÑO, año")

    def test_ene_descompuesta_tambien_se_queda(self):
        self.assertEqual(sin_tildes.quitar("Campaña"), "Campaña")

    def test_lo_demas_no_se_toca(self):
        for texto in ("Repisa 1/2 m² 25 kN 7x7 ½ 30°", "Set 5 Piezas - Rosa", ""):
            self.assertEqual(sin_tildes.quitar(texto), texto)

    def test_vacio(self):
        self.assertEqual(sin_tildes.quitar(None), "")


class EstudioDeML(unittest.TestCase):
    def test_los_prompts_del_titulo_de_ml_lo_piden(self):
        for system in (ia_generadores._ML_TITULO, ia_generadores._ML_CON_FOTO,  # noqa: SLF001
                       ia_generadores._MEJORAR["mercado_libre"]["system"]):  # noqa: SLF001
            self.assertIn(sin_tildes.REGLA, system)

    def test_el_prompt_general_no(self):
        self.assertNotIn(sin_tildes.REGLA, ia_generadores._MEJORAR["general"]["system"])  # noqa: SLF001

    def test_el_boton_titulo_de_ml_los_quita(self):
        with mock.patch.object(ia_generadores, "_completar",
                               return_value={"ok": True, "texto": "Caminadora Eléctrica Niño\n(25 caracteres)"}):
            r = ia_generadores.generar("mercado_libre", "titulo", {"nombre": "x"})
        self.assertEqual(r["texto"], "Caminadora Electrica Niño\n(25 caracteres)")

    def test_el_titulo_de_la_tienda_conserva_su_ortografia(self):
        with mock.patch.object(ia_generadores, "_completar",
                               return_value={"ok": True, "texto": "Caminadora Eléctrica"}):
            r = ia_generadores.generar("general", "titulo", {"nombre": "x"})
        self.assertEqual(r["texto"], "Caminadora Eléctrica")

    def test_mejorar_con_ia_de_ml_lo_quita_y_la_descripcion_no(self):
        crudo = {"titulo": "Tienda de Campaña Térmica", "descripcion": "Ideal para acampar en montaña."}
        res = {"ok": True, "texto": __import__("json").dumps(crudo)}
        sin_foto = {"ok": False, "vision": None}
        with mock.patch.object(ia_generadores, "_del_catalogo", return_value=[]), \
                mock.patch.object(ia_generadores, "_mercado_ml", mock.AsyncMock(return_value=None)), \
                mock.patch.object(ia_generadores, "_ml_con_foto", mock.AsyncMock(return_value=sin_foto)), \
                mock.patch.object(ia_generadores, "_completar", return_value=res), \
                mock.patch.object(ia_generadores, "_guardar_ml", mock.AsyncMock(return_value=None)):
            r = asyncio.run(ia_generadores.mejorar("mercado_libre", {"nombre": "Tienda", "sku": "X"}))
        self.assertEqual(r["campos"]["titulo"], "Tienda de Campaña Termica")
        self.assertEqual(r["campos"]["descripcion"], "Ideal para acampar en montaña.")


class LoQueSaleAML(unittest.TestCase):
    def test_el_alta_sale_sin_acentos_en_title_y_family_name(self):
        pl = {"title": "Caminadora Eléctrica Niño", "family_name": "Caminadora Eléctrica Niño",
              "attributes": [{"id": "BRAND", "value_name": "Ferrahome"}]}
        vendor = mock.Mock(return_value=pl)
        with mock.patch.object(publicar_ready, "_build_payload_vendor", vendor):
            r = publicar_ready._build_payload_con_marca({"sku": "X", "ml_attrs": {}}, "tok")  # noqa: SLF001
        self.assertEqual((r["title"], r["family_name"]),
                         ("Caminadora Electrica Niño", "Caminadora Electrica Niño"))

    def test_si_falla_la_marca_el_titulo_igual_sale_sin_acentos(self):
        pl = {"family_name": "Bicicleta Fija Ánima", "attributes": []}
        with mock.patch.object(publicar_ready, "_build_payload_vendor", mock.Mock(return_value=pl)), \
                mock.patch.object(publicar_ready, "_con_marca_declarada", side_effect=RuntimeError("bum")), \
                self.assertLogs("omnicanal.publicar_ready", "ERROR"):
            r = publicar_ready._build_payload_con_marca({"sku": "X"}, "tok")  # noqa: SLF001
        self.assertEqual(r["family_name"], "Bicicleta Fija Anima")

    def test_el_vendor_sin_envio_no_revienta(self):
        with mock.patch.object(publicar_ready, "_build_payload_vendor", mock.Mock(return_value=None)):
            self.assertIsNone(publicar_ready._build_payload_con_marca({"sku": "X"}, "tok"))  # noqa: SLF001

    def test_el_envoltorio_instalado_es_el_que_quita_los_acentos(self):
        actual = publisher_core.build_payload
        self.addCleanup(setattr, publisher_core, "build_payload", actual)
        publicar_ready._instalar_marca_declarada()  # noqa: SLF001
        self.assertIs(publisher_core.build_payload, publicar_ready._build_payload_con_marca)  # noqa: SLF001

    def test_el_put_de_una_publicacion_viva_va_sin_acentos(self):
        pubs = [{"cuenta": "BEKURA", "item_id": "MLM1"}]
        una = mock.AsyncMock(return_value={"cuenta": "BEKURA", "item_id": "MLM1", "ok": True, "error": None,
                                           "ml_status": 200, "desc_status": None})
        req = {"sku": "X", "wc_id": 1, "campos": {"titulo": "Caminadora Eléctrica Niño"}}
        with mock.patch.object(publicar, "_ml_publicaciones", return_value=pubs), \
                mock.patch.object(publicar, "_estados_items_ml",
                                  mock.AsyncMock(return_value={"BEKURA": {"vivo": True}})), \
                mock.patch.object(publicar, "_cuentas_faltantes_ml", return_value=[]), \
                mock.patch.object(publicar, "_guardar_backlog_ml") as backlog, \
                mock.patch.object(publicar, "_update_ml_una", una):
            asyncio.run(publicar._confirmar_ml(req))  # noqa: SLF001
        self.assertEqual(una.call_args.args[2]["title"], "Caminadora Electrica Niño")  # `cambios` (v0.721)
        self.assertEqual(backlog.call_args.args[8]["title"], "Caminadora Electrica Niño")


if __name__ == "__main__":
    unittest.main()
