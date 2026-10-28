"""Pruebas de los atributos de Mercado Libre que la IA llena y que el alta manda
(`services/ml_atributos.py` + `publicar_ready._atributos_ml_para_alta`).

Brandon, 23-oct-2026: «checa la mejora con IA para que estos errores no se produzcan; hay
que enviar los atributos obligatorios para que la IA pueda llenar los campos, y al igual
los secundarios». Thalia, ACC-0446-MUL: «ya se le puso el género MUJER y NIÑAS pero
ninguno lo toma en cuenta».

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. La IA ve TODOS los secundarios (antes 15) y las reglas de Género, ceros y listas.
2. `depurar`: Género = el del título (ACC-0446-MUL «Niña Bebé» → Niñas); un solo valor;
   «0 W» fuera (TEC-1439-BLN); lista cerrada al valor exacto; obligatorios faltantes.
3. Al publicar, lo del Estudio con ID de ML gana a la meta vieja de WordPress, y se
   revisa igual; la etiqueta legible sola (atributos de Woo) no pisa nada.
4. El rechazo se explica con TODAS las respuestas del intento: «el título no concuerda
   con el Género» y no el «falta Género» que deja el reintento del pipeline.
5. «Mejorar con IA» de ML devuelve el parte (obligatorios, rechazados, avisos).
6. Datos FISCALES (28-oct): la IA no los ve ni los devuelve, el alta no los manda y «Guardar contenido» los deja
   en Woo como estaban.

Sin red.

    cd backend && python -m unittest tests.test_ml_atributos_revision -v
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from services import ml_atributos as ma
from services import ml_errores as me

GENEROS = ["Mujer", "Niñas", "Bebés", "Sin género", "Sin género infantil"]

# Atributos de MLM190885 «Tocados, Tiaras y Diademas» como los da ML (recortados), más
# POWER de MLM189697 «Cortadoras de Azulejos» para el caso de TEC-1439-BLN.
CRUDOS = [
    {"id": "BRAND", "name": "Marca", "value_type": "string", "tags": {"required": True, "catalog_required": True}},
    {"id": "MODEL", "name": "Modelo", "value_type": "string", "tags": {"required": True, "catalog_required": True}},
    {"id": "AGE_GROUP", "name": "Edad", "value_type": "list", "tags": {"hidden": True, "read_only": True},
     "values": [{"id": "1", "name": "Adultos"}]},
    {"id": "GENDER", "name": "Género", "value_type": "list", "tags": {"required": True},
     "values": [{"id": str(i), "name": n} for i, n in enumerate(GENEROS, start=1)]},
    {"id": "COLOR", "name": "Color", "value_type": "string", "tags": {},
     "values": [{"id": "52049", "name": "Negro"}, {"id": "51993", "name": "Rosa"}]},
    {"id": "MATERIAL", "name": "Material", "value_type": "list", "tags": {"multivalued": True},
     "values": [{"id": "m1", "name": "Tela"}, {"id": "m2", "name": "Plástico"}]},
    {"id": "POWER", "name": "Potencia", "value_type": "number_unit", "tags": {},
     "allowed_units": [{"id": "W", "name": "W"}, {"id": "kW", "name": "kW"}]},
    {"id": "GTIN", "name": "Código universal de producto", "value_type": "string",
     "tags": {"conditional_required": True}},
]
META = ma.clasificar(CRUDOS)


class Clasificar(unittest.TestCase):
    def test_separa_obligatorios_y_quita_ocultos_y_los_que_se_llenan_aparte(self):
        ids_p = [a["id"] for a in META["principales"]]
        ids_s = [a["id"] for a in META["secundarias"]]
        self.assertEqual(ids_p, ["GENDER"])            # BRAND/MODEL van aparte (_SKIP_IDS)
        self.assertNotIn("AGE_GROUP", ids_s)          # oculto
        self.assertNotIn("GTIN", ids_s)               # se gestiona aparte
        self.assertIn("POWER", ids_s)
        self.assertTrue(next(a for a in META["secundarias"] if a["id"] == "MATERIAL")["varios"])
        self.assertEqual(next(a for a in META["secundarias"] if a["id"] == "POWER")["unidades"], ["W", "kW"])


class Prompt(unittest.TestCase):
    def test_van_todos_los_secundarios_y_las_reglas_nuevas(self):
        muchos = {"principales": META["principales"],
                  "secundarias": [{"id": f"ATTR_{i}", "name": f"A{i}", "value_type": "string",
                                   "valid_values": []} for i in range(40)]}
        p = ma.build_prompt("Moños Niña", "", "", "", muchos)
        self.assertIn("ATTR_39", p)                   # antes se cortaba en 15
        self.assertIn("NUNCA dos valores juntos", p)
        self.assertIn("PROHIBIDO poner 0", p)
        self.assertIn("GENDER (Género, tipo: list; lista cerrada", p)

    def test_marca_unidades_y_varios(self):
        p = ma.build_prompt("x", "", "", "", META)
        self.assertIn("unidades: W, kW", p)
        self.assertIn("admite varios valores", p)


class GeneroDelTitulo(unittest.TestCase):
    def test_gana_la_primera_palabra(self):
        self.assertEqual(ma.genero_del_titulo("Moños para Cabello Niña Bebé Listón", GENEROS), "Niñas")
        self.assertEqual(ma.genero_del_titulo("Babero para bebé niña", GENEROS), "Bebés")

    def test_sin_acentos_y_plurales(self):
        self.assertEqual(ma.genero_del_titulo("DIADEMAS PARA NINAS", GENEROS), "Niñas")
        self.assertEqual(ma.genero_del_titulo("Set para dama", GENEROS), "Mujer")

    def test_los_dos_son_sin_genero(self):
        self.assertEqual(ma.genero_del_titulo("Para niños y niñas", GENEROS + ["Niños"]), "Sin género infantil")
        self.assertEqual(ma.genero_del_titulo("Para hombre y mujer", GENEROS + ["Hombre"]), "Sin género")

    def test_sin_palabra_no_adivina(self):
        self.assertIsNone(ma.genero_del_titulo("Moños para cabello colores surtidos", GENEROS))

    def test_si_la_categoria_no_lo_tiene_no_lo_propone(self):
        self.assertIsNone(ma.genero_del_titulo("Gorra para hombre", GENEROS))  # sin "Hombre"


class Depurar(unittest.TestCase):
    def test_acc_0446_genero_contra_el_titulo(self):
        r = ma.depurar({"GENDER": "Mujer"}, META, titulo="Moños para Cabello Niña Bebé Listón")
        self.assertEqual(r["atributos"]["GENDER"], "Niñas")
        self.assertTrue(any("Niñas" in a and "Mujer" in a for a in r["avisos"]))

    def test_dos_generos_juntos_queda_uno(self):
        r = ma.depurar({"GENDER": "Mujer y Niñas"}, META, titulo="Moños para cabello")
        self.assertEqual(r["atributos"]["GENDER"], "Mujer")
        self.assertTrue(any("una sola opción" in a for a in r["avisos"]))
        r = ma.depurar({"GENDER": "MUJER, NIÑAS"}, META, titulo="Moños niña")
        self.assertEqual(r["atributos"]["GENDER"], "Niñas")

    def test_tec_1439_potencia_en_cero_se_va(self):
        r = ma.depurar({"POWER": "0 W"}, META, titulo="Cortadora manual")
        self.assertNotIn("POWER", r["atributos"])
        self.assertIn("Potencia (POWER)", r["rechazados"][0]["campo"])
        self.assertEqual(ma.depurar({"POWER": "250 W"}, META)["atributos"]["POWER"], "250 W")

    def test_lista_cerrada_al_valor_exacto_o_fuera(self):
        r = ma.depurar({"GENDER": "ninas", "MATERIAL": "Madera"}, META)
        self.assertEqual(r["atributos"]["GENDER"], "Niñas")
        self.assertNotIn("MATERIAL", r["atributos"])
        self.assertIn("no está en la lista", r["rechazados"][0]["motivo"])

    def test_texto_libre_con_sugerencias_no_se_toca(self):
        self.assertEqual(ma.depurar({"COLOR": "Multicolor"}, META)["atributos"]["COLOR"], "Multicolor")

    def test_obligatorio_faltante(self):
        self.assertEqual(ma.depurar({"COLOR": "Rosa"}, META, titulo="Moños")["faltan"], ["GENDER"])

    def test_marca_y_ids_ajenos_pasan(self):
        r = ma.depurar({"BRAND": "Ferrahome", "gender": "x"}, META)
        self.assertEqual(r["atributos"]["BRAND"], "Ferrahome")
        self.assertEqual(r["atributos"]["gender"], "x")


class GenerarAtributos(unittest.TestCase):
    def test_lo_que_contesta_la_ia_sale_revisado(self):
        respuesta = {"atributos": {"GENDER": "Mujer y Niñas", "POWER": "0 W", "COLOR": "Rosa"}, "flags": []}
        with mock.patch.object(ma, "get_meli_all_attributes", mock.AsyncMock(return_value=META)), \
             mock.patch.object(ma, "_deepseek_json", mock.AsyncMock(return_value=respuesta)):
            r = asyncio.run(ma.generar_atributos("MLM190885", "Moños para Cabello Niña Bebé"))
        self.assertEqual(r["atributos"]["GENDER"], "Niñas")
        self.assertNotIn("POWER", r["atributos"])
        self.assertEqual(r["atributos"]["BRAND"], "Ferrahome")
        self.assertEqual(len(r["rechazados"]), 1)
        self.assertEqual(r["faltan"], [])


class AltaML(unittest.TestCase):
    """`publicar_ready._atributos_ml_para_alta`: lo del panel manda y se revisa."""

    def _alta(self, prod, campos):
        from services import publicar_ready as pr
        with mock.patch.object(pr.meli, "_access_token", return_value="tok"), \
             mock.patch.object(pr.publisher_core, "get_category_attrs_cached", return_value=CRUDOS), \
             mock.patch.object(pr.ml_revision_categoria, "categoria_de_alta", return_value="MLM190885"):
            return pr._atributos_ml_para_alta(prod, campos, "BEKURA")

    def test_acc_0446_meta_vieja_mujer_sale_ninas(self):
        prod = {"sku": "ACC-0446-MUL", "title": "Moños para Cabello Niña Bebé Listón Colores Surtidos",
                "ml_attrs": {"GENDER": "Mujer", "POWER": "0 W"}}
        nuevo, avisos = self._alta(prod, {})
        self.assertEqual(nuevo["ml_attrs"]["GENDER"], "Niñas")
        self.assertNotIn("POWER", nuevo["ml_attrs"])
        self.assertTrue(any("Género" in a for a in avisos))
        self.assertTrue(any(a.startswith("No se manda Potencia") for a in avisos))
        self.assertEqual(prod["ml_attrs"]["GENDER"], "Mujer")  # no muta el original

    def test_lo_del_estudio_con_id_gana_a_la_meta(self):
        prod = {"title": "Moños", "ml_attrs": {"COLOR": "Negro"}}
        campos = {"atributos": [{"nombre": "Color", "campo": "COLOR", "valor": "Rosa"}]}
        self.assertEqual(self._alta(prod, campos)[0]["ml_attrs"]["COLOR"], "Rosa")
        campos = {"atributos": [{"nombre": "COLOR", "valor": "Rosa"}]}  # Bodega: nombre = ID
        self.assertEqual(self._alta(prod, campos)[0]["ml_attrs"]["COLOR"], "Rosa")

    def test_la_etiqueta_legible_sola_no_pisa(self):
        prod = {"title": "Moños", "ml_attrs": {"COLOR": "Negro"}}
        campos = {"atributos": [{"nombre": "Color", "valor": "Rojo, Azul"}]}  # lista de la familia en Woo
        self.assertEqual(self._alta(prod, campos)[0]["ml_attrs"]["COLOR"], "Negro")

    def test_si_falla_algo_sale_igual(self):
        from services import publicar_ready as pr
        prod = {"title": "x", "ml_attrs": {"GENDER": "Mujer"}}
        with mock.patch.object(pr.meli, "_access_token", return_value=None):
            self.assertIs(pr._atributos_ml_para_alta(prod, {}, "BEKURA")[0], prod)


class RechazoConTodasLasRespuestas(unittest.TestCase):
    TITULO_GENERO = {"cause": [{"type": "error", "code": "invalid.title.gender",
                                "message": "Make sure that the words in the title match the gender you entered previously."}]}
    FALTA_GENERO = {"cause": [{"type": "error", "code": "item.attributes.missing_required",
                               "message": "The attributes [GENDER] are required for category MLM190885 and channel marketplace."},
                              {"type": "warning", "code": "shipping.lost_me1_by_user", "message": "User has not mode me1"}]}

    def test_acc_0446_se_explica_el_titulo_no_el_falta_genero(self):
        e = me.explicar_varias([self.TITULO_GENERO, self.FALTA_GENERO], {"GENDER": "Género"}, "Mujer")
        self.assertEqual([x["codigo"] for x in e], ["invalid.title.gender"])
        self.assertIn("«Mujer»", e[0]["porque"])

    def test_sin_el_rechazo_del_titulo_el_falta_genero_se_queda(self):
        e = me.explicar_varias([self.FALTA_GENERO], {"GENDER": "Género"})
        self.assertEqual(e[0]["codigo"], "item.attributes.missing_required")

    def test_el_anotador_registra_cada_respuesta_del_hilo(self):
        from services import publicar_ready as pr
        llamadas = []

        def original(payload, token):
            llamadas.append(payload)
            return (self.TITULO_GENERO if len(llamadas) == 1 else self.FALTA_GENERO), 400

        def publicar(prod, token, dry, cuenta):
            pr.ml_api.create_item({"category_id": "MLM190885",
                                   "attributes": [{"id": "GENDER", "value_id": "1", "value_name": "Mujer"}]}, token)
            pr.ml_api.create_item({"category_id": "MLM190885", "attributes": []}, token)
            return {"success": False, "ml_status": 400, "ml_response": self.FALTA_GENERO}

        with mock.patch.object(pr, "_create_item_original", original), \
             mock.patch.object(pr.ml_api, "create_item", pr._create_item_que_anota), \
             mock.patch.object(pr.publisher_core, "publish_product", side_effect=publicar):
            r = pr._publicar_anotando({}, "tok", "BEKURA")
        self.assertEqual(len(r["_respuestas_ml"]), 2)
        e = pr._explicar_rechazo(r["_respuestas_ml"], r["ml_response"], "MLM190885")
        self.assertEqual(e[0]["codigo"], "invalid.title.gender")
        self.assertIn("«Mujer»", e[0]["porque"])
        self.assertIsNone(getattr(pr._registro_ml, "respuestas", None))  # se apaga al salir


class MejorarConIA(unittest.TestCase):
    def test_ml_devuelve_el_parte_de_atributos(self):
        from services import ia_generadores as ig
        gen = {"atributos": {"BRAND": "Ferrahome", "GENDER": "Niñas"}, "flags": [],
               "meli_attrs": META, "avisos": ["Género: «Niñas» porque el título lo dice."],
               "rechazados": [{"campo": "Potencia (POWER)", "motivo": "«0 W»…"}], "faltan": []}
        with mock.patch.object(ig, "_completar", return_value={"ok": True, "texto": '{"titulo": "Moños Niña"}'}), \
             mock.patch("services.ml_atributos.generar_atributos", mock.AsyncMock(return_value=gen)) as g, \
             mock.patch.object(ig, "_guardar_ml", mock.AsyncMock(return_value=None)):
            r = asyncio.run(ig.mejorar("mercado_libre", {"sku": "ACC-0446-MUL", "nombre": "Moños",
                                                        "ml_cat_id": "MLM190885", "categoria": "Tocados"}))
        self.assertEqual(g.call_args.kwargs["nombre"], "Moños Niña")  # el título NUEVO
        self.assertEqual(r["requisitos"]["estado"], "ok")
        self.assertEqual(r["requisitos"]["cubiertos"], ["GENDER"])
        self.assertEqual(r["rechazados"][0]["campo"], "Potencia (POWER)")
        self.assertIn("Niñas", r["avisos"][0])
        self.assertEqual(r["product_type"], "Tocados")


class DatosFiscales(unittest.TestCase):
    """Brandon, 28-oct-2026: «todos que sean datos fiscales NO SE LLENAN ni se tocan». MUE-0080-NEG salió a ML con
    IVA 16 %, IEPS 0 % y las claves SAT en «No existe en el catálogo», todo puesto por la IA."""

    FISCALES_CRUDOS = [
        {"id": "INVOICE_PRODUCT_NAME", "name": "Nombre del producto en la factura", "value_type": "string",
         "tags": {}},
        {"id": "SAT_KEY", "name": "Clave del producto", "value_type": "string", "tags": {}},
        {"id": "IVA_FOR_RESALE", "name": "IVA para reventa", "value_type": "list", "tags": {},
         "values": [{"id": "1", "name": "16%"}]},
        {"id": "IEPS", "name": "IEPS", "value_type": "list", "tags": {}, "values": [{"id": "2", "name": "0%"}]},
        {"id": "IMPORT_DECLARATION_NUMBER", "name": "Número de pedimento", "value_type": "string", "tags": {}},
    ]

    def test_reconoce_id_meta_y_nombre(self):
        for fiscal in ("IEPS", "ieps", "ml_attr_SAT_KEY", "IVA_FOR_RESALE", "IVA para reventa",
                       "Número de pedimento", "  nombre del producto en la factura ", "MEASURE_UNIT_DESCRIPTION",
                       "VALUE_ADDED_TAX"):
            self.assertTrue(ma.es_fiscal(fiscal), fiscal)
        for normal in ("COLOR", "Color", "ml_attr_COLOR", "UNITS_PER_PACK", "Material", "", None):
            self.assertFalse(ma.es_fiscal(normal), normal)

    def test_la_ia_no_los_ve(self):
        m = ma.clasificar(CRUDOS + self.FISCALES_CRUDOS)
        ids = {a["id"] for a in m["principales"] + m["secundarias"]}
        self.assertFalse(ids & ma.FISCALES)
        self.assertIn("COLOR", ids)
        self.assertNotIn("IEPS", ma.build_prompt("Tapete", "", "", "", m, "MUE-0080-NEG"))

    def test_si_la_ia_los_contesta_no_salen(self):
        respuesta = {"atributos": {"COLOR": "Rosa", "IEPS": "0%", "SAT_KEY": "No existe en el catálogo"}, "flags": []}
        with mock.patch.object(ma, "get_meli_all_attributes",
                               mock.AsyncMock(return_value=ma.clasificar(CRUDOS + self.FISCALES_CRUDOS))), \
             mock.patch.object(ma, "_deepseek_json", mock.AsyncMock(return_value=respuesta)):
            r = asyncio.run(ma.generar_atributos("MLM190885", "Moños"))
        self.assertEqual(r["atributos"]["COLOR"], "Rosa")
        self.assertFalse(set(r["atributos"]) & ma.FISCALES)

    def test_depurar_los_quita_sin_avisar(self):
        r = ma.depurar({"COLOR": "Rosa", "IEPS": "0%", "IVA_FOR_RESALE": "16%"}, META)
        self.assertEqual(r["atributos"], {"COLOR": "Rosa"})
        self.assertEqual((r["avisos"], r["rechazados"]), ([], []))

    def test_el_alta_no_los_manda_ni_de_la_meta_ni_del_estudio(self):
        prod = {"title": "Moños", "ml_attrs": {"COLOR": "Negro", "IEPS": "0%", "SAT_KEY": "No existe en el catálogo"}}
        campos = {"atributos": [{"nombre": "IVA para reventa", "campo": "IVA_FOR_RESALE", "valor": "16%"}]}
        nuevo, avisos = AltaML._alta(self, prod, campos)
        self.assertEqual(nuevo["ml_attrs"], {"COLOR": "Negro"})
        self.assertFalse(any("IEPS" in a or "IVA" in a for a in avisos))

    def test_guardar_contenido_conserva_los_fiscales_de_woo(self):
        from services import woocommerce as wc
        actuales = [{"id": 0, "name": "IEPS", "options": ["0%"], "variation": False},
                    {"id": 0, "name": "Material", "options": ["Espuma"], "variation": False}]
        enviado = {}

        class Resp:
            def __init__(self, code, body=None):
                self.status_code, self._body, self.text = code, body or {}, ""

            def json(self):
                return self._body

        class Cli:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, params=None):
                return Resp(200, {"attributes": actuales})

            async def put(self, url, json=None, timeout=None):
                enviado.update(json)
                return Resp(200)

        with mock.patch.object(wc, "_client", lambda: Cli()), \
             mock.patch("services.wp_db.disponible", return_value=False):
            ok = asyncio.run(wc.guardar_contenido_wc(1, atributos=[{"nombre": "Material", "valor": "Microfibra"}]))
        self.assertTrue(ok)
        self.assertEqual([(a["name"], a["options"]) for a in enviado["attributes"]],
                         [("IEPS", ["0%"]), ("Material", ["Microfibra"])])   # el fiscal, intacto


if __name__ == "__main__":
    unittest.main()
