"""Pruebas del rechazo de Mercado Libre en palabras (`services/ml_errores.py`).

Brandon, 22-oct-2026: «en los errores puedes integrar el porqué del error porque veo
únicamente los 400, y la solución en caso de que sea posible».

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. Cada causa REAL de los logs de producción (23-sep → 22-oct) sale con su porqué y, cuando
   la hay, su solución. Los textos de ML de los fixtures están copiados de esos logs.
2. TEC-1439-BLN: «POWER 0 W» se explica nombrando el atributo («Potencia») y el valor.
3. Los avisos sueltos (envío gratis, ME1 junto a otro error…) no se cuentan como causa.
4. Un código desconocido sale con el texto de ML y SIN solución inventada.
5. `crear_ml` no reintenta un 400 con causas y devuelve `explicacion` por cuenta.
6. `_backlog_ml` guarda en `ml_backlog` el porqué y la solución junto a la etiqueta del vendor.

Sin red.

    cd backend && python -m unittest tests.test_ml_errores -v
"""
from __future__ import annotations

import asyncio
import unittest
from contextlib import contextmanager
from unittest import mock

from services import ml_errores as me

# ── Respuestas reales de ML (copiadas de los logs de Railway) ─────────────────
TEC_1439 = {"message": "Validation error", "error": "validation_error", "status": 400, "cause": [
    {"department": "structured-data", "cause_id": 3708, "type": "error",
     "code": "item.attribute.number_invalid_format", "references": ["item.attributes"],
     "message": "Value must be greater than 0.0 W"},
    {"department": "items", "cause_id": 306, "type": "warning", "code": "item.attributes.omitted",
     "references": ["item.attributes"],
     "message": "Attribute POWER with value 0 W was omitted. The provided value must be greater than 0 W"}]}

GTIN = {"cause": [{"type": "error", "code": "item.attribute.missing_conditional_required",
                   "message": "The attributes [GTIN] are required for category [MLM190081]. Check the "
                              "attribute is present in the attributes list or in all variation's "
                              "attributes_combination"}]}

GENERO = {"cause": [{"type": "error", "code": "item.attributes.missing_required",
                     "message": "The attributes [GENDER] are required for category MLM190880 and channel "
                                "marketplace. Check the attribute is present in the attributes list"},
                    {"type": "warning", "code": "shipping.lost_me1_by_user", "message": "User has not mode me1"}]}

FOTOS = {"cause": [{"type": "error", "code": "item.pictures.invalid_size",
                    "message": "Las fotos deben tener un tamaño mínimo de 500 pixeles en al menos uno de sus "
                               "lados y 250 en el otro lado."}]}

PRECIO = {"cause": [{"type": "error", "code": "item.price.invalid",
                     "message": "The category MLM194120 requires a minimum of price 35"}]}

TALLAS = {"cause": [{"type": "error", "code": "missing.fashion_grid.grid_id.values",
                     "message": "Attribute [SIZE_GRID_ID] is missing"}]}

SOLO_ME1 = {"cause": [{"type": "warning", "code": "shipping.lost_me1_by_user", "message": "User has not mode me1"}]}

ENTRADA_AIRE = {"cause": [{"type": "error", "code": "item.attribute.number_invalid_format",
                           "message": 'El valor que ingresaste en "Entrada de aire" es incorrecto.'}]}

DOS_CEROS = {"cause": [
    {"type": "error", "code": "item.attribute.number_invalid_format", "message": "Value must be greater than 0.0 kg"},
    {"type": "error", "code": "item.attribute.number_invalid_format", "message": "Value must be greater than 0.0 cm"},
    {"type": "warning", "code": "item.attributes.omitted",
     "message": "Attribute WEIGHT with value 0.0 kg was omitted. The provided value must be greater than 0 kg"},
    {"type": "warning", "code": "item.attributes.omitted",
     "message": "Attribute DIAMETER with value 0 cm was omitted. The provided value must be greater than 0 cm"}]}


class Explicar(unittest.TestCase):
    def test_tec_1439_nombra_el_atributo_y_el_valor(self):
        e = me.explicar(TEC_1439, {"POWER": "Potencia"})
        self.assertEqual(len(e), 1)
        self.assertIn("«Potencia» (POWER) va en 0 W", e[0]["porque"])
        self.assertIn("vacío", e[0]["solucion"])
        self.assertIn("pestaña Mercado Libre", e[0]["solucion"])

    def test_sin_nombres_usa_el_id(self):
        self.assertIn("POWER va en 0 W", me.explicar(TEC_1439)[0]["porque"])

    def test_dos_medidas_en_cero_son_dos_causas_distintas(self):
        e = me.explicar(DOS_CEROS, {"WEIGHT": "Peso", "DIAMETER": "Diámetro"})
        self.assertEqual(len(e), 2)
        self.assertIn("Peso", e[0]["porque"])
        self.assertIn("Diámetro", e[1]["porque"])

    def test_medida_en_espanol_toma_el_nombre_entre_comillas(self):
        e = me.explicar(ENTRADA_AIRE)
        self.assertIn("«Entrada de aire»", e[0]["porque"])

    def test_gtin_dice_que_falta_el_codigo_de_barras(self):
        e = me.explicar(GTIN)
        self.assertIn("código de barras", e[0]["porque"])
        self.assertIn("GTIN", e[0]["solucion"])

    def test_atributo_obligatorio_y_el_aviso_de_me1_no_cuenta(self):
        e = me.explicar(GENERO, {"GENDER": "Género"})
        self.assertEqual(len(e), 1)
        self.assertIn("«Género» (GENDER)", e[0]["porque"])
        self.assertIn("categoría probablemente está mal", e[0]["solucion"])

    def test_fotos_chicas(self):
        self.assertIn("500 × 250", me.explicar(FOTOS)[0]["porque"])

    def test_precio_minimo_dice_cuanto(self):
        e = me.explicar(PRECIO)[0]
        self.assertIn("$35", e["porque"])
        self.assertIn("$35", e["solucion"])

    def test_guia_de_tallas_no_inventa_solucion(self):
        self.assertIsNone(me.explicar(TALLAS)[0]["solucion"])

    def test_solo_me1_explica_que_no_hay_nada_que_activar(self):
        e = me.explicar(SOLO_ME1)
        self.assertEqual(e[0]["codigo"], "shipping.lost_me1_by_user")
        self.assertIn("No hay nada que activar", e[0]["solucion"])

    def test_codigo_desconocido_sale_tal_cual_y_sin_solucion(self):
        e = me.explicar({"cause": [{"type": "error", "code": "algo.nuevo", "message": "Texto de ML"}]})
        self.assertEqual(e[0]["porque"], "Mercado Libre dice: Texto de ML")
        self.assertIsNone(e[0]["solucion"])

    def test_sin_respuesta_o_sin_causas_no_explica(self):
        self.assertEqual(me.explicar(None), [])
        self.assertEqual(me.explicar({"message": "x"}), [])
        self.assertEqual(me.explicar("HTTP 500"), [])


class Mensaje(unittest.TestCase):
    def test_una_causa(self):
        m = me.mensaje(me.explicar(TEC_1439, {"POWER": "Potencia"}))
        self.assertTrue(m.startswith("Mercado Libre la rechazó. Por qué: «Potencia»"))
        self.assertIn("Solución:", m)

    def test_varias_van_numeradas(self):
        m = me.mensaje(me.explicar(DOS_CEROS))
        self.assertIn("1) Por qué", m)
        self.assertIn("2) Por qué", m)

    def test_sin_solucion_no_pone_la_etiqueta(self):
        self.assertNotIn("Solución", me.mensaje(me.explicar(TALLAS)))

    def test_vacio(self):
        self.assertEqual(me.mensaje([]), "")


class CrearMl(unittest.TestCase):
    """El rechazo de validación no se reintenta y sale explicado por cuenta."""

    def test_un_400_con_causas_se_intenta_una_vez_y_se_explica(self):
        from services import publicar_ready as pr
        prod = {"sku": "TEC-1439-BLN", "ml_category_id": "MLM189697", "wc_categories": [], "stock": 5,
                "title": "t", "price": 10.0}
        llamadas = []

        def publicar(p, token, dry, cuenta):
            llamadas.append(cuenta)
            return {"success": False, "error": "HTTP 400: Validation error", "ml_status": 400,
                    "ml_response": TEC_1439}

        with mock.patch.object(pr, "configurar"), \
             mock.patch.object(pr, "_candado_alta_ml", mock.AsyncMock(return_value=None)), \
             mock.patch.object(pr, "construir_prod", return_value=prod), \
             mock.patch.object(pr, "_stock_real", return_value=5), \
             mock.patch.object(pr.meli, "_access_token", return_value="tok"), \
             mock.patch.object(pr.publisher_core, "publish_product", side_effect=publicar), \
             mock.patch.dict(pr.publisher_core._attr_cache, {"MLM189697": [{"id": "POWER", "name": "Potencia"}]}):
            r = asyncio.run(pr.crear_ml("TEC-1439-BLN", 1, {}, ["SANCORFASHION", "BEKURA"],
                                        categoria_confirmada="MLM189697"))
        self.assertEqual(llamadas, ["SANCORFASHION", "BEKURA"])  # una vez por cuenta, no tres
        fila = r["resultados"][0]
        self.assertFalse(fila["ok"])
        self.assertIn("«Potencia» (POWER) va en 0 W", fila["error"])
        self.assertEqual(fila["explicacion"][0]["codigo"], "item.attribute.number_invalid_format")


class BacklogMl(unittest.TestCase):
    def test_ml_backlog_guarda_el_porque_junto_a_la_etiqueta(self):
        from services import publicar_ready as pr
        guardado = {}

        class Cur:
            lastrowid = 7

            def execute(self, sql, params=None):
                if "INSERT INTO ml_backlog" in sql:
                    guardado["error"] = params[7]

        @contextmanager
        def cursor():
            yield Cur()

        entry = {"cuenta": "BEKURA", "wc_id": 1, "payload": {"category_id": "MLM189697"},
                 "ml_response": TEC_1439, "ml_status": 400,
                 "result": {"success": False, "error": "HTTP 400: Validation error"}}
        with mock.patch.object(pr.db, "get_cursor", cursor), \
             mock.patch("services.kubera_mirror.espejar") as espejar, \
             mock.patch.dict(pr.publisher_core._attr_cache, {"MLM189697": [{"id": "POWER", "name": "Potencia"}]}):
            pr._backlog_ml("BEKURA:TEC-1439-BLN", entry)
        self.assertTrue(guardado["error"].startswith("HTTP 400: Validation error · Mercado Libre la rechazó."))
        self.assertIn("«Potencia»", guardado["error"])
        self.assertIn("«Potencia»", espejar.call_args[0][5]["error_resumen"])


if __name__ == "__main__":
    unittest.main()
