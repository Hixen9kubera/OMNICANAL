"""
La IA NUNCA escribe una garantía (Brandon, 28-oct-2026): la ponen las KAM en cada
canal. Medido ese día: 334 de 4,099 descripciones de Woo traían una garantía
inventada («Garantía de 3 años», «Garantia: 30 días»…) y el prompt de Amazon la
pedía en la 5.ª viñeta.

El prompt pide (`sin_garantia.REGLA`) y el código garantiza: Crear y el Estudio la
QUITAN del texto; Amazon, TikTok y Walmart la REPORTAN para que su ciclo la repare.

    cd backend && python -m unittest tests.test_sin_garantia -v
"""
import unittest
from unittest import mock

from services import (amazon_contenido, amazon_ia, crear_producto, ia_generadores,
                      sin_garantia, tiktok_contenido, tiktok_ia, walmart_contenido)


class Quitar(unittest.TestCase):
    def test_vineta_html_y_oracion(self):
        html = ("<p><strong>Características</strong></p><ul><li>Material PP.</li>"
                "<li>Garantía de 1 año contra defectos.</li></ul>"
                "<p>Ideal para casa. Incluye garantía del vendedor de 30 días.</p>")
        self.assertEqual(sin_garantia.quitar(html),
                         "<p><strong>Características</strong></p><ul><li>Material PP.</li></ul>"
                         "<p>Ideal para casa.</p>")

    def test_etiqueta_sin_llevarse_lo_de_al_lado(self):
        # ORG-0021, tal cual estaba en Woo el 28-oct.
        self.assertEqual(
            sin_garantia.quitar("Uso: Profesional y doméstico Garantia: 30 días"),
            "Uso: Profesional y doméstico")

    def test_texto_plano_de_ml_conserva_las_otras_vinetas(self):
        self.assertEqual(
            sin_garantia.quitar("- Resistente al agua\n- Garantía del vendedor: 30 días\n- Fácil de limpiar"),
            "- Resistente al agua\n- Fácil de limpiar")

    def test_lista_que_queda_vacia_desaparece(self):
        self.assertEqual(sin_garantia.quitar("<ul><li>Garantía: 1 año</li></ul><p>Fin.</p>"),
                         "<p>Fin.</p>")

    def test_salto_de_linea_dentro_del_parrafo(self):
        self.assertEqual(sin_garantia.quitar("<p>Uso: doméstico<br>Garantía: 30 días</p>"),
                         "<p>Uso: doméstico</p>")

    def test_ingles_tambien(self):
        self.assertEqual(sin_garantia.quitar("Durable steel. 2 year warranty included."),
                         "Durable steel.")

    def test_el_verbo_no_es_una_garantia(self):
        texto = "<p>Este modelo garantiza un secado uniforme y está garantizado por su diseño.</p>"
        self.assertFalse(sin_garantia.menciona(texto))
        self.assertEqual(sin_garantia.quitar(texto), texto)

    def test_vacio_y_none(self):
        self.assertEqual(sin_garantia.quitar(""), "")
        self.assertEqual(sin_garantia.quitar(None), "")
        self.assertFalse(sin_garantia.menciona(None))


class Crear(unittest.TestCase):
    def test_el_prompt_lo_pide(self):
        self.assertIn(sin_garantia.REGLA, crear_producto._prompt_seo("Kids set", "u", True))

    def test_la_descripcion_sale_sin_garantia_venga_de_quien_venga(self):
        r = crear_producto._seo_de_objeto({
            "titulo": "Set de limpieza", "descripcion": "<p>Ligero.</p><ul><li>Garantía: 1 año</li></ul>"})
        self.assertEqual(r, {"titulo": "Set de limpieza", "descripcion": "<p>Ligero.</p>"})


class Estudio(unittest.TestCase):
    def test_los_prompts_de_descripcion_lo_piden(self):
        for system in (ia_generadores._ML_DESCRIPCION, ia_generadores._GEN_DESCRIPCION,
                       ia_generadores._MEJORAR["mercado_libre"]["system"],
                       ia_generadores._MEJORAR["general"]["system"]):
            self.assertIn(sin_garantia.REGLA, system)

    def test_el_boton_descripcion_la_quita(self):
        texto = "Práctico y ligero.\n- Material PP\n- Garantía de 3 años"
        with mock.patch.object(ia_generadores, "_completar",
                               return_value={"ok": True, "texto": texto}):
            r = ia_generadores.generar("mercado_libre", "descripcion", {"nombre": "x"})
        self.assertEqual(r["texto"], "Práctico y ligero.\n- Material PP")

    def test_otro_boton_no_se_toca(self):
        with mock.patch.object(ia_generadores, "_completar",
                               return_value={"ok": True, "texto": "Guantes con garantía"}):
            r = ia_generadores.generar("mercado_libre", "titulo", {"nombre": "x"})
        self.assertEqual(r["texto"], "Guantes con garantía")


class CanalesConValidador(unittest.TestCase):
    def test_amazon_ya_no_la_pide_y_la_prohibe(self):
        self.assertNotIn("(5) garantía", amazon_ia._SISTEMA)
        self.assertIn(sin_garantia.REGLA, amazon_ia._SISTEMA)

    def test_amazon_la_reporta_en_cualquier_campo_para_repararla(self):
        bullets = ["Beneficio claro " + "x" * 150] * 4 + ["Incluye garantía de 1 año " + "x" * 140]
        _, problemas = amazon_contenido.validar({"titulo": "Guantes Para Soldar", "bullets": bullets,
                                                 "descripcion": "Texto. Garantía del vendedor: 30 días."})
        con_garantia = [p for p in problemas if "garantía" in p]
        self.assertEqual([p.split(":")[0] for p in con_garantia], ["descripcion", "bullet 5"])

    def test_amazon_la_que_sobrevive_a_la_reparacion_se_quita_sin_rechazar_el_campo(self):
        import asyncio
        # Termina en punto, como un bullet real: se corta por ORACIONES.
        bueno = "Beneficio claro y concreto para el uso diario " + "x" * 120 + "."
        crudo = {"titulo": "Guantes Para Soldar", "highlights": "Piel resistente",
                 "bullets": [bueno] * 4 + [bueno + " Incluye garantía de 1 año."],
                 "descripcion": "Protege tus manos. Garantía del vendedor: 30 días."}
        res = {"ok": True, "texto": __import__("json").dumps(crudo), "proveedor": "x", "modelo": "y"}
        with mock.patch.object(amazon_ia, "_product_type", return_value=("SAFETY_GLOVE", "panel")), \
                mock.patch("services.channel_content.requisitos", mock.AsyncMock(return_value=[])), \
                mock.patch.object(ia_generadores, "_completar", return_value=res), \
                mock.patch.object(amazon_ia, "_reparar", mock.AsyncMock(return_value=None)):
            r = asyncio.run(amazon_ia.mejorar({"nombre": "Guantes"}, guardar=False))
        self.assertEqual(r["campos"]["descripcion"], "Protege tus manos.")
        self.assertEqual(r["campos"]["bullets"][4], bueno)
        self.assertFalse([x for x in r["rechazados"] if x["campo"] in ("bullets", "descripcion")])

    def test_amazon_el_verbo_pasa(self):
        _, problemas = amazon_contenido.validar({"titulo": "Lampara", "descripcion": "Garantiza un secado uniforme."})
        self.assertFalse([p for p in problemas if "garantía" in p])

    def test_tiktok(self):
        self.assertIn("NINGUNA garantía", tiktok_ia._SISTEMA)
        self.assertIn("garantia", tiktok_contenido._promocionales("Garantía de 1 año"))
        self.assertEqual(tiktok_contenido._promocionales("Garantiza un secado uniforme"), [])

    def test_walmart(self):
        self.assertIn("garantia", walmart_contenido._penalizadas("Garantía: 30 días"))
        self.assertNotIn("garantia", walmart_contenido._penalizadas("Garantiza un secado uniforme"))


if __name__ == "__main__":
    unittest.main()
