"""Pruebas de «Sugerir precio» del Estudio (services/precio_sugerido.py + routers/precio_sugerido.py, v0.686.0).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Las cuentas**: resumen (mediana, P25/P75), redondeo a …9, utilidad con IVA, comisión sobre el precio CON IVA y
   envío, y el PISO por barrido (el envío sube al cruzar $299: una búsqueda binaria podía brincarse el precio bueno).
2. **La referencia** se elige por confianza: foto → título → más vendidos, con al menos 3 precios.
3. **La sugerencia** con los números reales de TEC-0605 (prueba del 22-oct): mediana del mismo modelo $159.
4. **El juez de fotos**: se lee su JSON, y un rival del mismo modelo con OTRA cantidad de piezas no entra.
5. **El modo rápido** filtra los más vendidos por parecido de título y no llama a nada que cobre.
6. **Permisos**: las rutas piden `operador`, como Competencia.

Sin red: nada aquí habla con ML, Apify, OpenRouter ni kubera.

    cd backend && python -m unittest tests.test_precio_sugerido -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import rbac  # noqa: E402
from services import costos  # noqa: E402
from services import precio_sugerido as ps  # noqa: E402

COSTO = 82.08                     # TEC-0605
PCT = 0.18                        # FULL Premium en MLM10743, de listing_prices (22-oct)


def comision_fija(precio):
    return PCT, 0.0


def envio_007kg(precio):          # 0.07 kg → fila de 0.3 kg de la tabla oficial
    return costos.calc_fee_envio_ml(0.07, precio)


class Cuentas(unittest.TestCase):
    def test_resumen(self):
        r = ps.resumen([96, 128, 135, 150, 159, 159, 159, 177, 179, 182, 241, 245])
        self.assertEqual((r["n"], r["min"], r["mediana"], r["max"]), (12, 96, 159, 245))
        self.assertLess(r["p25"], r["mediana"])
        self.assertGreater(r["p75"], r["mediana"])
        self.assertEqual(ps.resumen([100, 200])["p25"], 100)          # con menos de 4: mín y máx
        self.assertIsNone(ps.resumen([]))
        self.assertIsNone(ps.resumen([0, None]))

    def test_redondeo_a_9(self):
        self.assertEqual([ps.abajo9(x) for x in (182, 159, 160, 159.99, 9.5)], [179, 159, 159, 159, 9])
        self.assertEqual([ps.arriba9(x) for x in (208, 209, 200.01, 1)], [209, 209, 209, 9])

    def test_utilidad_con_iva_comision_y_envio(self):
        # $179: 179/1.16 − 0.18×179 − $32 (envío 0.07 kg, tramo $99–198.99) − $82.08 = $8.01
        u = ps.utilidad(179, COSTO, PCT, 0.0, envio_007kg(179))
        self.assertAlmostEqual(u, 179 / 1.16 - 32.22 - 32.0 - COSTO, places=2)
        self.assertAlmostEqual(u, 8.01, places=2)

    def test_piso_30_por_ciento_sobre_costo(self):
        p = ps.piso(COSTO, 0.30, comision_fija, envio_007kg)
        self.assertEqual(p, 208.0)
        self.assertGreaterEqual(ps.utilidad(p, COSTO, PCT, 0, envio_007kg(p)), 0.30 * COSTO)
        self.assertLess(ps.utilidad(p - 1, COSTO, PCT, 0, envio_007kg(p - 1)), 0.30 * COSTO)

    def test_piso_no_se_brinca_el_escalon_de_envio(self):
        # A $298 el envío es $35 y a $299 sube a $52.40: la utilidad BAJA al cruzar. El barrido no debe saltar
        # al primer precio bueno de abajo del escalón por suponer que la utilidad siempre crece.
        meta = ps.utilidad(298, COSTO, PCT, 0, envio_007kg(298)) / COSTO
        self.assertEqual(ps.piso(COSTO, meta, comision_fija, envio_007kg), 298.0)

    def test_piso_imposible_y_sin_costo(self):
        self.assertIsNone(ps.piso(COSTO, 0.30, lambda p: (0.9, 0.0), envio_007kg))   # comisión que nunca deja ganar
        self.assertIsNone(ps.piso(0, 0.30, comision_fija, envio_007kg))

    def test_comision_por_tramo(self):
        f = ps.comision_de([{"hasta": 499.99, "pct": 0.195, "fijo": 0}, {"hasta": 1000.0, "pct": 0.16, "fijo": 0},
                            {"hasta": None, "pct": 0.16, "fijo": 0}])
        self.assertEqual(f(499.99), (0.195, 0))
        self.assertEqual(f(500), (0.16, 0))
        self.assertEqual(f(5000), (0.16, 0))

    def test_envio_sin_peso_usa_el_del_panel(self):
        self.assertEqual(ps.envio_de(None, 91.7)(179), 91.7)
        self.assertEqual(ps.envio_de(None, None)(179), 0.0)
        self.assertEqual(ps.envio_de(0.07, 91.7)(179), 32.0)

    def test_peso_de_la_ficha(self):
        self.assertEqual(ps.peso_de_atributos([{"name": "Peso", "options": ["0.07 kg"]}]), 0.07)
        self.assertEqual(ps.peso_de_atributos([{"name": "Peso", "options": ["70 g"]}]), 0.07)
        self.assertEqual(ps.peso_de_atributos([{"name": "peso", "options": ["1,2 kg"]}]), 1.2)
        # La forma normalizada de `woocommerce.obtener_producto_por_sku` ({nombre, valor}).
        self.assertEqual(ps.peso_de_atributos([{"nombre": "Peso", "valor": "0.07 kg"}]), 0.07)
        self.assertIsNone(ps.peso_de_atributos([{"name": "Color", "options": ["Negro"]}]))


class ReferenciaYSugerencia(unittest.TestCase):
    FOTO = [96, 128, 135, 150, 159, 159, 159, 177, 179, 182, 241, 245]      # TEC-0605, mismo modelo por foto

    def test_foto_manda_sobre_titulo(self):
        r = ps.elegir_referencia({"foto": self.FOTO, "titulo": [85, 90, 1173], "mas_vendidos": [128]})
        self.assertEqual((r["grupo"], r["mediana"], r["pocos"]), ("foto", 159, False))

    def test_con_menos_de_3_baja_al_siguiente(self):
        r = ps.elegir_referencia({"foto": [179, 182], "titulo": [115, 176, 288, 347], "mas_vendidos": []})
        self.assertEqual(r["grupo"], "titulo")
        r = ps.elegir_referencia({"foto": [179, 182], "titulo": [], "mas_vendidos": [128]})
        self.assertEqual((r["grupo"], r["pocos"]), ("foto", True))
        self.assertIsNone(ps.elegir_referencia({"foto": [], "titulo": []}))

    def test_tec_0605_con_margen_de_30(self):
        # Con IVA, comisión real y envío, ganar 30 % sobre $82.08 pide $208; 10 de los 12 del mismo modelo están entre
        # $96 y $182 (P75 ≈ $180). Es el caso honesto de «no compite», no un error de la cuenta.
        ref = ps.elegir_referencia({"foto": self.FOTO})
        p = ps.piso(COSTO, 0.30, comision_fija, envio_007kg)
        self.assertEqual(p, 208.0)
        self.assertEqual(ps.sugerir(ref, p), {"precio": 209, "estado": "no_compite"})
        # Con 10 % sobre costo el piso baja a ~$180 y ya cabe en el rango del mercado.
        p10 = ps.piso(COSTO, 0.10, comision_fija, envio_007kg)
        self.assertLess(p10, ref["p75"] + 10)

    def test_limites_alrededor_de_la_mediana(self):
        lo, hi = ps.limites([96, 98, 104, 120, 125, 149, 193, 300, 705, 1624])
        self.assertTrue(lo < 96 and 300 < hi < 705)        # mediana $137: el JBL y el Hollyland quedan fuera
        self.assertIsNone(ps.limites([100, 200]))           # con menos de 3 no se recorta

    def test_estados(self):
        ref = {"mediana": 182, "p75": 243, "max": 245}
        self.assertEqual(ps.sugerir(ref, 150), {"precio": 179, "estado": "compite"})
        self.assertEqual(ps.sugerir(ref, 208), {"precio": 209, "estado": "arriba_mediana"})
        self.assertEqual(ps.sugerir(ref, 260), {"precio": 269, "estado": "no_compite"})
        self.assertEqual(ps.sugerir(None, 208), {"precio": 209, "estado": "sin_mercado"})
        self.assertEqual(ps.sugerir(ref, None), {"precio": 179, "estado": "sin_piso"})
        self.assertEqual(ps.sugerir(None, None), {"precio": None, "estado": "sin_mercado"})


class JuezDeFotos(unittest.TestCase):
    def test_lee_el_json(self):
        v = ps.leer_veredicto_foto('```json\n{"veredicto": "mismo_modelo", "piezas_foto2": 2, "razon": "igual"}\n```')
        self.assertEqual(v, {"veredicto": "mismo_modelo", "razon": "igual", "piezas": 2})
        self.assertEqual(ps.leer_veredicto_foto('{"veredicto": "Mismo diseno"}')["veredicto"], "mismo_diseno")
        self.assertEqual(ps.leer_veredicto_foto("no sé")["veredicto"], "error")
        self.assertEqual(ps.leer_veredicto_foto('{"veredicto": "parecido"}')["veredicto"], "error")

    def test_otra_cantidad_de_piezas_no_entra(self):
        self.assertTrue(ps.comparable_por_foto({"foto": "mismo_modelo", "foto_piezas": 2}, 2))
        self.assertTrue(ps.comparable_por_foto({"foto": "mismo_diseno", "foto_piezas": None}, 2))
        self.assertFalse(ps.comparable_por_foto({"foto": "mismo_modelo", "foto_piezas": 1}, 2))
        self.assertFalse(ps.comparable_por_foto({"foto": "diferente", "foto_piezas": 2}, 2))
        self.assertTrue(ps.comparable_por_foto({"foto": "mismo_modelo", "foto_piezas": 1}, None))

    def test_sin_llave_no_llama(self):
        with mock.patch.object(ps.settings, "openrouter_api_key", ""):
            r = asyncio.run(ps.juez_fotos("https://x/y.jpg", [{"imagen": "https://x/z.jpg"}]))
        self.assertEqual(r["usd"], 0.0)
        self.assertIn("Sin llave", r["motivo"])


class ModoRapido(unittest.TestCase):
    def test_parecido(self):
        a = "2 Pack Micrófono Inalambrico De Solapa Cancelacion Ruido Negro"
        self.assertGreater(ps.parecido(a, "Pack 2 Micrófonos Inalámbricos Lavalier de solapa con cancelación de ruido"),
                           ps.UMBRAL_PARECIDO)
        self.assertLess(ps.parecido(a, "Shure Beta 58a"), ps.UMBRAL_PARECIDO)
        self.assertEqual(ps.parecido(a, ""), 0.0)

    def test_rapido_no_cobra_y_filtra_por_titulo(self):
        top = [{"id": "MLM1", "titulo": "Pack 2 Micrófonos Inalámbricos Lavalier Solapa Cancelación Ruido", "precio": 159.0},
               {"id": "MLM2", "titulo": "2 Pack Micrófono Inalámbrico De Solapa Para iPhone", "precio": 98.99},
               {"id": "MLM3", "titulo": "Micrófono Lavalier Inalámbrico Solapa Cancelación de Ruido 2 piezas", "precio": 129.0},
               {"id": "MLM4", "titulo": "Shure Beta 58a", "precio": 4388.0}]
        datos = {"sku": "TEC-0605", "titulo": "2 Pack Micrófono Inalambrico De Solapa Cancelacion Ruido Negro",
                 "costo": COSTO, "ml_cat_id": "MLM10743", "imagen": None, "peso_kg": 0.07, "envio_panel": 91.7,
                 "margen": 0.30}

        class Cli:
            async def aclose(self):
                pass

        async def bandas(cli, cat):
            return [{"hasta": 499.99, "pct": PCT, "fijo": 0}, {"hasta": None, "pct": 0.16, "fijo": 0}]

        anotado = []
        with mock.patch.object(ps, "datos_sku", mock.AsyncMock(return_value=datos)), \
                mock.patch.object(ps, "_cliente_ml", mock.AsyncMock(return_value=Cli())), \
                mock.patch.object(ps, "mas_vendidos", mock.AsyncMock(return_value=top)), \
                mock.patch.object(ps, "comisiones", bandas), \
                mock.patch.object(ps, "_anotar", lambda res, dur: anotado.append(res)), \
                mock.patch.object(ps, "juez_fotos") as fotos:
            r = asyncio.run(ps.rapido({"sku": "TEC-0605"}))
        fotos.assert_not_called()
        self.assertEqual(r["costo_consulta"]["total"], 0.0)
        self.assertEqual(r["referencia"]["grupo"], "mas_vendidos")
        self.assertEqual(r["referencia"]["n"], 3)                          # el Shure quedó fuera
        self.assertFalse(next(x for x in r["rivales"] if x["id"] == "MLM4")["en_referencia"])
        self.assertEqual(r["piso"], 208.0)
        self.assertEqual(r["sugerido"]["estado"], "no_compite")            # mediana $129 < piso $208 y P75 < $209
        self.assertEqual(r["sugerido"]["precio"], 209)
        self.assertAlmostEqual(r["sugerido"]["desglose"]["envio"], 35.0)
        self.assertEqual(len(anotado), 1)


class Permisos(unittest.TestCase):
    def test_rutas_piden_operador(self):
        for metodo, ruta in (("POST", "/api/precio-sugerido/rapido"), ("POST", "/api/precio-sugerido/completo"),
                             ("GET", "/api/precio-sugerido/ultimo/TEC-0605")):
            self.assertEqual(rbac.rol_requerido(metodo, ruta), "operador")
            self.assertFalse(rbac.permite("lectura", metodo, ruta))
            self.assertTrue(rbac.permite("operador", metodo, ruta))


if __name__ == "__main__":
    unittest.main()
