"""Un atributo OPCIONAL de Mercado Libre con formato rechazado no tumba el alta
(`publicar_ready._opcionales_rechazados` / `_sin_atributos` / `crear_ml`).

TEC-1875-MUL (23-oct-2026), «Rotuladores Acuarela Doble Punta…» en Marcadores y
Marcatextos (MLM167976): ML contestó «El valor que ingresaste en "Grosor del trazo" es
incorrecto» (`item.attribute.number_invalid_format`). `STROKE_THICKNESS` es opcional y la
categoría lo declara TEXTO, pero ML lo valida como número con unidad.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. Se identifica el atributo por su nombre en el mensaje o por el aviso `omitted`.
2. Solo se quita si es OPCIONAL y si es lo único que falló; un obligatorio o un error de
   otro tipo deja todo como está (no se arregla a medias).
3. `crear_ml` lo quita, reintenta UNA vez, publica y la fila dice qué se dejó fuera; la
   segunda cuenta ya no lo manda.
4. El mensaje de error pide UN número con unidad (ej. «0.5 mm»), y la IA tiene la regla.

Sin red.

    cd backend && python -m unittest tests.test_ml_atributo_opcional -v
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from services import ml_atributos as ma
from services import ml_errores as me

CAT = "MLM167976"
CRUDOS = [
    {"id": "BRAND", "name": "Marca", "value_type": "string", "tags": {"required": True}},
    {"id": "STROKE_THICKNESS", "name": "Grosor del trazo", "value_type": "string",
     "tags": {"multivalued": True}},
    {"id": "POWER", "name": "Potencia", "value_type": "number_unit", "tags": {}},
    {"id": "GENDER", "name": "Género", "value_type": "list", "tags": {"required": True}},
]
# Copiada de los logs de Railway del 23-oct (17:57 UTC).
TEC_1875 = {"message": "Validation error", "status": 400, "cause": [
    {"department": "structured-data", "cause_id": 3708, "type": "error",
     "code": "item.attribute.number_invalid_format", "references": ["item.attributes"],
     "message": 'El valor que ingresaste en "Grosor del trazo" es incorrecto.'}]}


def _cache():
    from services import publicar_ready as pr
    return mock.patch.dict(pr.publisher_core._attr_cache, {CAT: CRUDOS})


class Identificar(unittest.TestCase):
    def test_tec_1875_por_el_nombre_del_mensaje(self):
        from services import publicar_ready as pr
        with _cache():
            self.assertEqual(pr._opcionales_rechazados(TEC_1875, CAT), ["STROKE_THICKNESS"])

    def test_por_el_aviso_omitted(self):
        from services import publicar_ready as pr
        r = {"cause": [{"type": "error", "code": "item.attribute.number_invalid_format",
                        "message": "Value must be greater than 0.0 W"},
                       {"type": "warning", "code": "item.attributes.omitted",
                        "message": "Attribute POWER with value 0 W was omitted."}]}
        with _cache():
            self.assertEqual(pr._opcionales_rechazados(r, CAT), ["POWER"])

    def test_un_obligatorio_no_se_quita(self):
        from services import publicar_ready as pr
        r = {"cause": [{"type": "error", "code": "item.attribute.number_invalid_format",
                        "message": 'El valor que ingresaste en "Género" es incorrecto.'}]}
        with _cache():
            self.assertEqual(pr._opcionales_rechazados(r, CAT), [])

    def test_si_hay_otro_error_no_se_toca_nada(self):
        from services import publicar_ready as pr
        r = {"cause": TEC_1875["cause"] + [{"type": "error", "code": "item.pictures.invalid_size",
                                            "message": "fotos"}]}
        with _cache():
            self.assertEqual(pr._opcionales_rechazados(r, CAT), [])

    def test_sin_identificar_no_se_toca_nada(self):
        from services import publicar_ready as pr
        r = {"cause": [{"type": "error", "code": "item.attribute.number_invalid_format",
                        "message": 'El valor que ingresaste en "Algo raro" es incorrecto.'}]}
        with _cache():
            self.assertEqual(pr._opcionales_rechazados(r, CAT), [])


class Quitar(unittest.TestCase):
    def test_sale_de_ml_attrs_y_de_lo_de_woo(self):
        from services import publicar_ready as pr
        prod = {"ml_attrs": {"STROKE_THICKNESS": "0.5-2 mm", "BRAND": "Ferrahome"},
                "wc_attrs": {"stroke_thickness": "fino", "color": "Multicolor"}}
        nuevo = pr._sin_atributos(prod, ["STROKE_THICKNESS"])
        self.assertEqual(nuevo["ml_attrs"], {"BRAND": "Ferrahome"})
        self.assertEqual(nuevo["wc_attrs"], {"color": "Multicolor"})
        self.assertIn("STROKE_THICKNESS", prod["ml_attrs"])  # no muta el original


class CrearMl(unittest.TestCase):
    def test_tec_1875_se_quita_se_reintenta_y_publica(self):
        from services import publicar_ready as pr
        prod = {"sku": "TEC-1875-MUL", "ml_category_id": CAT, "wc_categories": [], "stock": 5,
                "title": "Rotuladores", "price": 48.87, "ml_attrs": {"STROKE_THICKNESS": "Fino y grueso"},
                "wc_attrs": {}}
        mandados = []

        def publicar(p, token, dry, cuenta):
            mandados.append((cuenta, dict(p["ml_attrs"])))
            if "STROKE_THICKNESS" in p["ml_attrs"]:
                return {"success": False, "error": "HTTP 400: Validation error", "ml_status": 400,
                        "ml_response": TEC_1875}
            return {"success": True, "ml_item_id": f"MLM{len(mandados)}"}

        with mock.patch.object(pr, "configurar"), \
             mock.patch.object(pr, "_candado_alta_ml", mock.AsyncMock(return_value=None)), \
             mock.patch.object(pr, "construir_prod", return_value=prod), \
             mock.patch.object(pr, "_stock_real", return_value=5), \
             mock.patch.object(pr, "_atributos_ml_para_alta", side_effect=lambda p, c, cu: (p, [])), \
             mock.patch.object(pr.meli, "_access_token", return_value="tok"), \
             mock.patch.object(pr.publisher_core, "publish_product", side_effect=publicar), \
             mock.patch.object(pr, "asegurar_pausado", return_value={"pausado": True, "status": "paused"}), \
             _cache():
            r = asyncio.run(pr.crear_ml("TEC-1875-MUL", 1, {}, ["SANCORFASHION", "BEKURA"],
                                        categoria_confirmada=CAT))
        # SANCORFASHION: con el atributo (rechazo) y sin él (alta); BEKURA ya sin él.
        self.assertEqual([c for c, _ in mandados], ["SANCORFASHION", "SANCORFASHION", "BEKURA"])
        self.assertNotIn("STROKE_THICKNESS", mandados[2][1])
        self.assertTrue(r["ok"])
        self.assertIn("«Grosor del trazo»", r["resultados"][0]["aviso"])


class Textos(unittest.TestCase):
    def test_el_error_pide_un_numero_con_unidad(self):
        e = me.explicar(TEC_1875)[0]
        self.assertIn("«Grosor del trazo»", e["porque"])
        self.assertIn("0.5 mm", e["solucion"])

    def test_la_ia_tiene_la_regla(self):
        p = ma.build_prompt("x", "", "", "", {"principales": [], "secundarias": []})
        self.assertIn("MEDIDAS EN TEXTO", p)
        self.assertIn('"0.5 mm"', p)


if __name__ == "__main__":
    unittest.main()
