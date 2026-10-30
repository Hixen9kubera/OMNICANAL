"""Pruebas de «Sugerir precio» del Estudio (services/precio_sugerido.py + routers/precio_sugerido.py, v0.686.0).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Las cuentas**: resumen (mediana, P25/P75), redondeo a …9, utilidad con IVA, comisión sobre el precio CON IVA y
   envío, y el PISO por barrido (el envío sube al cruzar $299: una búsqueda binaria podía brincarse el precio bueno).
2. **La referencia** se elige por confianza: foto → título → más vendidos, con al menos 3 precios.
3. **La sugerencia** con los números reales de TEC-0605 (prueba del 22-oct): mediana del mismo modelo $159.
4. **El juez de fotos**: se lee su JSON, y un rival del mismo modelo con OTRA cantidad de piezas no entra.
5. **El catálogo maestro y Amazon** (v0.696.0): el veredicto de cada publicación de Amazon se reconstruye de lo que
   guardó el catálogo; la búsqueda en vivo cuenta una vez por familia, juzga por título y se queda con un nodo; las
   medidas son la mediana del paquete; el completo usa la palabra clave del catálogo y Amazon no lo tumba.
6. **Permisos**: las rutas piden `operador`, como Competencia.
7. **Búsqueda corta** (v0.687.0): nunca el título entero, lo del usuario manda, y el completo pide UN intento
   (v0.709.0; antes 3).
8. **Avisos de datos** (v0.687.0): VAR-0549-NEG (costo $456.45, 20 kg) avisa del peso y del costo; TEC-0605 no.
9. **La búsqueda se paga una vez** (v0.688.0): caché de Competencia de 30 días (v0.709.0; antes 7), un término por
   omisión (el 2.º solo si faltan resultados), nuestras publicaciones fuera, y el costo de Apify se corrige con su
   cobro final.
10. **El raspador** (v0.688.0): sin imágenes ni CSS, reintentos configurables y relectura del cobro final.
11. **Lo que se recortó** (v0.709.0, medido con las 6 consultas del 30-oct): cada consulta cuenta SOLO sus corridas
    de Apify aunque corran varias a la vez; el juez de fotos se detiene con 8 iguales; un color copia los veredictos
    de su hermana; Amazon sale del completo y va aparte.

Sin red: nada aquí habla con ML, Apify, OpenRouter ni kubera.

    cd backend && python -m unittest tests.test_precio_sugerido -v
"""
from __future__ import annotations

import asyncio
import json
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


class Parecido(unittest.TestCase):
    def test_parecido(self):
        a = "2 Pack Micrófono Inalambrico De Solapa Cancelacion Ruido Negro"
        self.assertGreater(ps.parecido(a, "Pack 2 Micrófonos Inalámbricos Lavalier de solapa con cancelación de ruido"),
                           ps.UMBRAL_PARECIDO)
        self.assertLess(ps.parecido(a, "Shure Beta 58a"), ps.UMBRAL_PARECIDO)
        self.assertEqual(ps.parecido(a, ""), 0.0)


class Cliente:
    async def aclose(self):
        pass


class TerminosDeBusqueda(unittest.TestCase):
    """v0.687.0: ML bloqueó el título LARGO en las dos primeras consultas reales (TEC-0605 y VAR-0549-NEG) y cada
    bloqueo se reintentaba 8 veces: 3 min 43 s. Ahora se busca corto y con 3 reintentos."""

    BOLSAS = "Bolsas plasticas thank you para regalo y tienda 50 pzas"

    def test_titulo_recortado(self):
        self.assertEqual(ps.termino_corto(self.BOLSAS), "Bolsas plasticas thank you regalo")
        self.assertEqual(ps.termino_corto("2 Pack Micrófono Inalambrico De Solapa Cancelacion Ruido Negro"),
                         "Micrófono Inalambrico Solapa Cancelacion Ruido")
        self.assertEqual(ps.termino_corto(""), "")

    def test_lo_del_usuario_manda(self):
        self.assertEqual(ps.terminos_busqueda(["bolsa thank you", "bolsa regalo", "otra"], "x", self.BOLSAS),
                         ["bolsa thank you", "bolsa regalo"])

    def test_ia_y_titulo_cortos_sin_repetir(self):
        t = ps.terminos_busqueda(None, "bolsas thank you regalo", self.BOLSAS)
        self.assertEqual(t, ["bolsas thank you regalo", "bolsas plasticas thank you regalo"])
        for termino in t:
            self.assertLessEqual(len(termino.split()), ps.PALABRAS_TERMINO + 1)
        self.assertEqual(ps.terminos_busqueda(None, "Bolsas plasticas thank you regalo", self.BOLSAS),
                         ["bolsas plasticas thank you regalo"])          # el mismo término no se busca dos veces
        self.assertEqual(ps.terminos_busqueda(None, None, self.BOLSAS), ["bolsas plasticas thank you regalo"])
        self.assertEqual(ps.terminos_busqueda(["  Bolsa  Thank You "], None, self.BOLSAS), ["bolsa thank you"])



def _filas(n: int, desde: int = 0, **extra) -> list[dict]:
    return [{"externo_id": f"MLM{desde + i}", "titulo": f"Bolsa regalo {desde + i}", "precio": 100.0 + i,
             "imagen": None, "url": f"https://x/{desde + i}", "posicion": i + 1, **extra} for i in range(n)]


class BusquedaSePagaUnaVez(unittest.TestCase):
    """v0.688.0: la búsqueda de un término se reusa 7 días desde el caché de Competencia, se busca UN término por
    omisión, nuestras publicaciones no son rivales, y el costo de Apify es el que Apify termina cobrando."""

    BOLSAS = TerminosDeBusqueda.BOLSAS
    DATOS = {"sku": "VAR-0549-NEG", "titulo": BOLSAS, "costo": 456.45, "ml_cat_id": None, "imagen": None,
             "peso_kg": 20.0, "envio_panel": None, "margen": 0.30}
    FOTO_PL = None          # lo que da la foto del packing list cuando el SKU no tiene foto en Woo

    def _completo(self, cache=None, medidas=None, terminos=None, corridas=None, catalogo=None, datos=None,
                  previa=None, juez=None, top=None, fotos=None, bloquea=()):
        """Corre `completo` sin red. `cache`: {término: {filas, dias}}; `medidas`: {término: filas} que «raspa»;
        `corridas`: lo que Apify cobra por cada raspado (se anota en la cuenta de la consulta, como el de verdad);
        `catalogo`: lo que da el catálogo maestro; `previa`: la consulta de una hermana que se puede reusar."""
        from services import (amazon_mercado, catalogo_precios, competencia_captura, competencia_juez,
                              competencia_scraper, competencia_terminos)
        cache, medidas = cache or {}, medidas or {}
        corridas = [{"run_id": "R1", "usd": 0.038}] if corridas is None else corridas
        estudio = mock.AsyncMock(return_value={"q": "x", "vistos": 0, "publicaciones": [], "usd": 0.002})

        async def medir(terms, limite=10, bloqueados=None, reintentos=8, filas_out=None):
            for t in terms:
                if t in bloquea and bloqueados is not None:
                    bloqueados.add(t)
                if t in medidas:
                    filas_out[t] = medidas[t]
            cuenta = competencia_scraper.corridas.get()
            for c in corridas:
                if cuenta is not None:
                    cuenta.append(dict(c))
            return {t: len(medidas.get(t, [])) for t in terms}

        medir_mock = mock.AsyncMock(side_effect=medir)
        juez_mock = mock.Mock(side_effect=juez or (lambda *a, **k: {"veredictos": {}, "usd": 0.0035,
                                                                   "unidades_nuestras": 1}))
        fotos_mock = mock.AsyncMock(side_effect=fotos) if fotos else             mock.AsyncMock(return_value={"usd": 0.004, "motivo": None, "juzgados": 6})
        anotado = []
        with mock.patch.object(ps, "datos_sku", mock.AsyncMock(return_value={**self.DATOS, **(datos or {})})),                 mock.patch.object(ps, "_foto_del_packing_list", return_value=self.FOTO_PL), \
                mock.patch.object(ps, "_cliente_ml", mock.AsyncMock(return_value=Cliente() if top else None)),                 mock.patch.object(ps, "mas_vendidos", mock.AsyncMock(return_value=list(top or []))),                 mock.patch.object(ps, "comisiones", mock.AsyncMock(return_value=[])), \
                mock.patch.object(ps, "_anotar", lambda res, dur: anotado.append(res)), \
                mock.patch.object(ps, "_del_cache", lambda t: cache.get(t)), \
                mock.patch.object(ps, "_hermana", return_value=previa), \
                mock.patch.object(ps, "juez_fotos", fotos_mock), \
                mock.patch.object(competencia_terminos, "proponer",
                                  lambda prods: {"VAR-0549-NEG": "bolsas thank you regalo"}), \
                mock.patch.object(competencia_juez, "juzgar_lote", juez_mock), \
                mock.patch.object(competencia_captura, "medir_busquedas", medir_mock), \
                mock.patch.object(catalogo_precios, "por_sku", return_value=catalogo or {"ml": None, "amazon": None}), \
                mock.patch.object(amazon_mercado, "estudio", estudio):
            r = asyncio.run(ps.completo({"sku": "VAR-0549-NEG", "terminos": terminos or []}))
        self.estudio, self.juez, self.fotos = estudio, juez_mock, fotos_mock
        return r, medir_mock

    def test_un_termino_con_un_solo_intento_y_queda_guardado(self):
        r, medir = self._completo(medidas={"bolsas thank you regalo": _filas(30)})
        medir.assert_called_once()
        args, kw = medir.call_args
        self.assertEqual(args[0], ["bolsas thank you regalo"])     # el 2.º no hizo falta
        self.assertEqual((kw["limite"], kw["reintentos"]), (48, 0))   # v0.709.0: un bloqueo no se paga 4 veces
        self.assertEqual(r["terminos"], ["bolsas thank you regalo"])
        self.assertEqual(r["cache"], {})
        self.assertEqual(len(r["rivales"]), 30)
        self.assertEqual(r["costo_consulta"]["apify"], 0.038)
        self.assertEqual(r["costo_consulta"]["apify_runs"], ["R1"])
        self.assertFalse(r["costo_consulta"]["apify_final"])
        self.assertAlmostEqual(r["costo_consulta"]["total"], 0.0415)

    def test_segundo_termino_solo_si_faltan_resultados(self):
        r, medir = self._completo(medidas={"bolsas thank you regalo": _filas(5),
                                              "bolsas plasticas thank you regalo": _filas(20, desde=100)})
        self.assertEqual([c.args[0] for c in medir.call_args_list],
                         [["bolsas thank you regalo"], ["bolsas plasticas thank you regalo"]])
        self.assertEqual(r["terminos"], ["bolsas thank you regalo", "bolsas plasticas thank you regalo"])
        self.assertEqual(len(r["rivales"]), 25)

    def test_del_cache_no_raspa_ni_cobra_apify(self):
        r, medir = self._completo(cache={"bolsas thank you regalo": {"filas": _filas(25), "dias": 2}})
        medir.assert_not_called()
        self.assertEqual(r["cache"], {"bolsas thank you regalo": 2})
        self.assertEqual(r["costo_consulta"]["apify"], 0.0)
        self.assertEqual((r["costo_consulta"]["apify_runs"], r["costo_consulta"]["apify_final"]), ([], True))
        self.assertEqual(len(r["rivales"]), 25)

    def test_si_ml_bloquea_el_primero_no_se_paga_el_segundo(self):
        r, medir = self._completo(medidas={}, bloquea={"bolsas thank you regalo"})
        self.assertEqual([c.args[0] for c in medir.call_args_list], [["bolsas thank you regalo"]])
        self.assertTrue(any("ML bloqueó" in a for a in r["avisos"]))

    def test_lo_que_escribe_el_usuario_se_busca_completo(self):
        _, medir = self._completo(medidas={"bolsa a": _filas(30)}, terminos=["Bolsa A", "bolsa b"])
        self.assertEqual(medir.call_args.args[0], ["bolsa a", "bolsa b"])

    def test_nuestras_publicaciones_no_son_rivales(self):
        filas = _filas(20) + _filas(2, desde=500, es_nuestro=1, sku_nuestro="VAR-0549-NEG")
        r, _ = self._completo(medidas={"bolsas thank you regalo": filas})
        self.assertEqual(len(r["rivales"]), 20)
        self.assertEqual(r["propias_fuera"], 2)

    def test_reglas_del_cache(self):
        from services import competencia_store
        casos = [({"termino": "t", "dias": 31, "estado": "ok", "resultados": 48}, None, None),     # viejo
                 ({"termino": "T", "dias": 8, "estado": "ok", "resultados": 48}, _filas(48), 48),   # 30 días (v0.709)
                 ({"termino": "t", "dias": 2, "estado": "bloqueado", "resultados": 0}, None, None),  # no se vio
                 ({"termino": "t", "dias": 2, "estado": "ok", "resultados": 10}, None, None),        # pocas filas
                 ({"termino": "t", "dias": 2, "estado": "vacio", "resultados": 0}, None, []),        # no repagar
                 ({"termino": "T", "dias": 6, "estado": "ok", "resultados": 48}, _filas(48), 48)]
        for est, filas, esperado in casos:
            with mock.patch.object(competencia_store, "estado_termino", return_value=est), \
                    mock.patch.object(competencia_store, "busqueda", return_value=filas) as leer:
                c = ps._del_cache("t")
            if esperado is None:
                self.assertIsNone(c, est)
            else:
                self.assertEqual(len(c["filas"]), len(esperado) if isinstance(esperado, list) else esperado)
                self.assertEqual(c["dias"], est["dias"])
                if filas:
                    leer.assert_called_once_with("T", 48)   # con el texto tal como está en el catálogo
        with mock.patch.object(competencia_store, "estado_termino", side_effect=RuntimeError("kubera caída")):
            self.assertIsNone(ps._del_cache("t"))       # sin caché se raspa, como antes

    def test_ultimo_relee_el_cobro_final_de_apify(self):
        from services import supabase_db
        guardado = {"sku": "X", "ok": True, "costo_consulta": {"apify": 0.0077, "juez": 0.0035, "fotos": 0.009,
                                                                "total": 0.0202, "apify_runs": ["R1"],
                                                                "apify_final": False}}
        with mock.patch.object(supabase_db, "fetch_one", return_value={"detalle": guardado, "created_at": None}), \
                mock.patch.object(ps, "_cobro_de_corridas",
                                  return_value={"usd": 0.038, "runs": ["R1"], "final": True}) as relee:
            r = ps.ultimo("X")
        relee.assert_called_once_with(["R1"])
        self.assertEqual(r["costo_consulta"]["apify"], 0.038)
        self.assertTrue(r["costo_consulta"]["apify_final"])
        self.assertAlmostEqual(r["costo_consulta"]["total"], 0.0505)

    def test_ultimo_ignora_el_modo_rapido(self):
        # v0.697.0: una consulta rápida vieja (TEC-0605-NEG-2PZ) le ganaba al catálogo maestro por ser la más nueva.
        from services import supabase_db
        with mock.patch.object(supabase_db, "fetch_one", return_value=None) as leer:
            self.assertIsNone(ps.ultimo("TEC-0605-NEG-2PZ"))
        self.assertIn("accion = 'completo'", leer.call_args.args[0])

    def test_suma_de_corridas(self):
        g = ps._sumar_apify([{"detalle": {"usd": 0.03, "run_id": "A", "usd_final": True}},
                             {"detalle": '{"usd": 0.008, "run_id": "B"}'}])
        self.assertEqual(g, {"usd": 0.038, "runs": ["A", "B"], "final": False})


    def test_la_palabra_del_catalogo_se_busca_en_ml(self):
        from services import competencia_terminos
        cat = {"ml": None, "amazon": {"q": "bolsas de regalo"}}
        with mock.patch.object(competencia_terminos, "proponer") as ia:
            r, medir = self._completo(medidas={"bolsas de regalo": _filas(30)}, catalogo=cat)
        ia.assert_not_called()                                    # la palabra del catálogo ahorra la IA
        self.assertEqual(medir.call_args.args[0], ["bolsas de regalo"])

    def test_el_completo_ya_no_paga_amazon(self):
        # v0.709.0: Amazon no entra al precio y su juez costaba ~$0.0025 en cada consulta. Va aparte.
        r, _ = self._completo(medidas={"bolsas thank you regalo": _filas(30)})
        self.estudio.assert_not_called()
        self.assertIsNone(r["amazon"])
        self.assertAlmostEqual(r["costo_consulta"]["juez"], 0.0035)   # solo el juez de ML


class LoQueSeRecorto(unittest.TestCase):
    """v0.709.0: las 6 consultas del 30-oct costaron $0.155 (Apify 53 %, fotos 27 %, títulos 20 %) y quedaron
    anotadas en $0.31. Brandon aprobó los cinco recortes y la corrección del costo anotado."""

    # ── Cada consulta cuenta solo sus corridas ──

    def test_dos_consultas_a_la_vez_no_se_llevan_las_corridas_de_la_otra(self):
        from services import competencia_scraper as cs

        async def consulta(run_id, espera):
            cuenta = []
            marca = cs.corridas.set(cuenta)
            try:
                await asyncio.sleep(espera)
                cs._registrar_gasto("apify/playwright-scraper", {"id": run_id, "usageTotalUsd": 0.02, "stats": {}})
            finally:
                cs.corridas.reset(marca)
            return cuenta

        async def ambas():
            return await asyncio.gather(consulta("A", 0.02), consulta("B", 0.01))

        with mock.patch.object(cs.supabase_db, "execute_returning", return_value=None):
            a, b = asyncio.run(ambas())
        self.assertEqual([c["run_id"] for c in a], ["A"])
        self.assertEqual([c["run_id"] for c in b], ["B"])

    def test_sin_cuenta_abierta_no_anota_nada_y_competencia_sigue_igual(self):
        from services import competencia_scraper as cs
        self.assertIsNone(cs.corridas.get())
        with mock.patch.object(cs.supabase_db, "execute_returning", return_value=None) as escribe:
            cs._registrar_gasto("x", {"id": "R9", "usageTotalUsd": 0.01, "stats": {}})
        escribe.assert_called_once()                      # la bitácora de Apify se escribe como siempre

    def test_gasto_de_corridas(self):
        self.assertEqual(ps.gasto_de_corridas([{"run_id": "A", "usd": 0.017}, {"run_id": "B", "usd": None}]),
                         {"usd": 0.017, "runs": ["A", "B"], "final": False})
        self.assertEqual(ps.gasto_de_corridas([]), {"usd": 0.0, "runs": [], "final": True})

    # ── El juez de fotos se detiene cuando ya alcanza ──

    def _fotos(self, veredictos, basta=None, unidades=None, status=200):
        llamadas = []

        class Resp:
            def __init__(self, v):
                self.status_code = status
                self._v = v

            def json(self):
                return {"choices": [{"message": {"content": json.dumps({"veredicto": self._v, "piezas_foto2": 1})}}],
                        "usage": {"cost": 0.0003}}

        class Cli:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, **k):
                llamadas.append(1)
                return Resp(veredictos[len(llamadas) - 1])

        rivales = [{"imagen": f"https://x/{i}.jpg"} for i in range(len(veredictos))]
        with mock.patch.object(ps.settings, "openrouter_api_key", "k"), \
                mock.patch.object(ps, "_bajar_miniatura", mock.AsyncMock(return_value="data:x")), \
                mock.patch.object(ps.httpx, "AsyncClient", Cli):
            r = asyncio.run(ps.juez_fotos("https://x/nuestra.jpg", rivales, unidades, basta))
        return r, len(llamadas), rivales

    def test_se_detiene_con_8_iguales(self):
        r, n, rivales = self._fotos(["mismo_modelo"] * 30, basta=ps.FOTOS_BASTAN)
        self.assertEqual(n, 12)                       # dos lotes de 6: al cerrar el 2.º ya hay 12 ≥ 8
        self.assertEqual(r["juzgados"], 12)
        self.assertAlmostEqual(r["usd"], 12 * 0.0003)
        self.assertNotIn("foto", rivales[12])         # el 13.º no se juzgó: no entra al grupo

    def test_sin_tope_juzga_todos(self):
        r, n, _ = self._fotos(["mismo_modelo"] * 30)
        self.assertEqual((n, r["juzgados"]), (30, 30))

    def test_los_diferentes_no_cuentan_para_el_tope(self):
        r, n, _ = self._fotos(["diferente"] * 20 + ["mismo_modelo"] * 10, basta=8)
        self.assertEqual(n, 30)                       # tras 4 lotes (24) solo hay 4 iguales; el 5.º llega a 10

    def test_otra_cantidad_de_piezas_no_cuenta_para_el_tope(self):
        _, n, _ = self._fotos(["mismo_modelo"] * 12, basta=8, unidades=2)   # el juez vio 1 pieza; somos 2
        self.assertEqual(n, 12)

    def test_sin_credito_no_sigue(self):
        r, n, _ = self._fotos(["mismo_modelo"] * 30, basta=8, status=402)
        self.assertEqual(n, 6)                        # el primer lote, y ahí se detiene
        self.assertIn("sin crédito", r["motivo"])

    # ── Un color no se vuelve a juzgar ──

    def test_firma_sin_color(self):
        self.assertEqual(ps.firma_variante([{"nombre": "Color", "valor": "Azul Marino"}]), [])
        self.assertEqual(ps.firma_variante([{"nombre": "pa_color", "valor": "Gris"}]), [])
        self.assertEqual(ps.firma_variante([{"nombre": "Color", "valor": "Negro"},
                                            {"nombre": "Cantidad", "valor": "5 piezas"}]), [["cantidad", "5 piezas"]])
        self.assertNotEqual(ps.firma_variante([{"nombre": "Cantidad", "valor": "4 piezas"}]),
                            ps.firma_variante([{"nombre": "Cantidad", "valor": "5 piezas"}]))
        self.assertEqual(ps.firma_variante([{"name": "Tamaño", "options": ["Mediano"]}]), [["tamano", "mediano"]])
        self.assertEqual(ps.firma_variante(None), [])

    def test_copiar_veredictos(self):
        previa = {"unidades_nuestras": 2, "rivales": [
            {"id": "MLM1", "clase_titulo": "mismo", "unidades": 2, "foto": "mismo_modelo", "foto_piezas": 2,
             "precio": 999, "en_referencia": True}]}
        rivales = [{"id": "MLM1", "precio": 300.0}, {"id": "MLM2", "precio": 310.0}]
        self.assertEqual(ps.copiar_veredictos(rivales, previa), 2)
        self.assertEqual(rivales[0]["clase_titulo"], "mismo")
        self.assertEqual(rivales[0]["precio"], 300.0)            # el precio es el de HOY, no el de la hermana
        self.assertNotIn("en_referencia", rivales[0])
        self.assertNotIn("clase_titulo", rivales[1])

    def test_hermana_solo_con_misma_firma_termino_y_rivales(self):
        from services import supabase_db
        d = {"sku": "MUE-0149-GRI", "padre_id": 29748, "firma": []}
        base = {"sku": "MUE-0149-AZLMAR", "firma": [], "terminos": ["colchoneta inflable camping"],
                "rivales": [{"id": "A"}, {"id": "B"}]}
        casos = [(base, "MUE-0149-AZLMAR"),
                 ({**base, "firma": [["cantidad", "2 piezas"]]}, None),          # otro paquete
                 ({**base, "terminos": ["colchon inflable"]}, None),             # otra búsqueda
                 ({**base, "rivales": [{"id": "A"}]}, None)]                     # otros rivales (caché nuevo)
        for det, esperado in casos:
            with mock.patch.object(supabase_db, "fetch_all", return_value=[{"sku": det["sku"], "detalle": det}]) as q:
                h = ps._hermana(d, ["colchoneta inflable camping"], {"A", "B"})
            self.assertEqual(h and h["sku"], esperado)
            self.assertEqual(q.call_args.args[1][2], "29748")
        with mock.patch.object(supabase_db, "fetch_all") as q:
            self.assertIsNone(ps._hermana({"sku": "X", "padre_id": None, "firma": []}, ["t"], {"A"}))
        q.assert_not_called()                          # un producto simple no tiene hermanas: ni pregunta
        with mock.patch.object(supabase_db, "fetch_all", side_effect=RuntimeError("kubera caída")):
            self.assertIsNone(ps._hermana(d, ["t"], {"A"}))   # sin kubera se juzga, como antes

    def test_completo_con_hermana_no_paga_jueces(self):
        caso = BusquedaSePagaUnaVez()
        filas = _filas(20)
        previa = {"sku": "MUE-0149-AZLMAR", "unidades_nuestras": 1,
                  "rivales": [{"id": f["externo_id"], "clase_titulo": "mismo", "foto": "mismo_modelo"} for f in filas]}
        r, _ = caso._completo(cache={"bolsas thank you regalo": {"filas": filas, "dias": 1}}, previa=previa,
                              datos={"imagen": "https://x/n.jpg", "padre_id": 29748, "firma": []})
        caso.juez.assert_not_called()
        caso.fotos.assert_not_called()
        self.assertEqual(r["reusado_de"], "MUE-0149-AZLMAR")
        self.assertEqual(r["costo_consulta"]["total"], 0.0)
        self.assertEqual(r["referencia"]["grupo"], "modelo")       # las copiadas eran «mismo_modelo»
        self.assertEqual(r["referencia"]["n"], 20)
        self.assertTrue(any("Veredictos copiados de MUE-0149-AZLMAR" in a for a in r["avisos"]))

    def test_completo_sin_hermana_pide_el_tope_de_fotos(self):
        caso = BusquedaSePagaUnaVez()
        filas = _filas(20)
        juez = lambda *a, **k: {"veredictos": {i: {"clase": "mismo", "motivo": "", "unidades_rival": 1}
                                              for i in range(1, 21)}, "usd": 0.0035, "unidades_nuestras": 1}
        r, _ = caso._completo(cache={"bolsas thank you regalo": {"filas": filas, "dias": 1}}, juez=juez,
                              datos={"imagen": "https://x/n.jpg", "padre_id": 29748, "firma": []})
        args = caso.fotos.call_args.args
        self.assertEqual((args[2], args[3]), (1, ps.FOTOS_BASTAN))
        self.assertIsNone(r["reusado_de"])
        self.assertEqual((r["padre_id"], r["firma"], r["fotos_juzgadas"]), (29748, [], 6))

    # ── Amazon aparte ──

    def test_solo_amazon_usa_la_palabra_del_catalogo_y_anota_su_costo(self):
        from services import amazon_mercado, catalogo_precios
        anotado = []
        with mock.patch.object(catalogo_precios, "por_sku", return_value={"amazon": {"q": "Colchoneta  Inflable"}}), \
                mock.patch.object(amazon_mercado, "estudio",
                                  mock.AsyncMock(return_value={"q": "x", "n": 3, "media": 300.0, "usd": 0.0025})) as e, \
                mock.patch.object(ps, "_anotar", lambda res, dur: anotado.append(res)):
            r = asyncio.run(ps.solo_amazon({"sku": "MUE-0149-GRI", "titulo": "Colchoneta inflable gris"}))
        self.assertEqual(e.call_args.args, ("colchoneta inflable", "Colchoneta inflable gris"))
        self.assertEqual(r["amazon"]["media"], 300.0)
        self.assertNotIn("usd", r["amazon"])
        self.assertEqual(r["costo_consulta"]["total"], 0.0025)
        self.assertEqual(anotado[0]["modo"], "amazon")          # no le gana al completo en `ultimo`


class ConsultaQueSiFunciona(unittest.TestCase):
    """v0.709.1, lo que Brandon vio el 30-oct («el proceso de sugerir precios no funciona correctamente»): TEC-1880-NEG
    buscó «kit para bicicleta» (la palabra del catálogo, de OTRO producto), ML bloqueó las dos búsquedas y el Estudio
    siguió con el precio del catálogo sin decir por qué; y ORG-0875-BEI salió en $151 con 8 cajas del mismo DISEÑO
    mientras las 2 del mismo MODELO exacto ($465 y $677) se recortaban por «extremas»."""

    PEDALERA = "Pedalera ejercitadora brazos piernas doble pedal gym casa"

    def test_se_parecen(self):
        self.assertFalse(ps.se_parecen("kit para bicicleta", self.PEDALERA))
        self.assertTrue(ps.se_parecen("pedal ejercicio", self.PEDALERA))            # «pedal» ~ «pedalera»
        self.assertTrue(ps.se_parecen("termos acero inoxidable", "Termo de acero con asa"))
        self.assertFalse(ps.se_parecen("", self.PEDALERA))

    def test_la_palabra_del_catalogo_que_no_se_parece_no_se_usa(self):
        caso = BusquedaSePagaUnaVez()
        r, medir = caso._completo(medidas={"bolsas thank you regalo": _filas(30)},
                                  catalogo={"ml": None, "amazon": {"q": "kit para bicicleta"}})
        self.assertEqual(medir.call_args.args[0], ["bolsas thank you regalo"])   # el de la IA, con el título
        self.assertTrue(any("kit para bicicleta" in a for a in r["avisos"]))

    def test_si_ml_bloquea_se_comparan_por_foto_los_mas_vendidos(self):
        caso = BusquedaSePagaUnaVez()
        top = [{"id": f"MLM9{i}", "posicion": i + 1, "titulo": f"Mini bicicleta {i}", "precio": 500.0 + 10 * i,
                "imagen": f"https://x/{i}.jpg", "url": f"https://x/p/{i}", "fuente": "mas_vendidos"} for i in range(6)]

        async def fotos(nuestra, candidatos, unidades, basta):
            for c in candidatos[:4]:
                c.update(foto="mismo_modelo", foto_piezas=1)
            return {"usd": 0.0018, "motivo": None, "juzgados": len(candidatos)}

        r, _ = caso._completo(medidas={}, top=top, fotos=fotos,
                              datos={"imagen": "https://x/n.jpg", "ml_cat_id": "MLM184769"})
        candidatos = caso.fotos.call_args.args[1]
        self.assertEqual(len(candidatos), 6)                   # todos: de los más vendidos decide la foto
        self.assertEqual(r["referencia"]["grupo"], "modelo")
        self.assertEqual(r["referencia"]["n"], 4)
        self.assertTrue(any("más vendidos de la categoría" in a for a in r["avisos"]))
        self.assertEqual(len(r["mas_vendidos"]), 6)
        self.assertNotIn("foto", r["mas_vendidos"][0])         # la lista de contexto no se ensucia

    def test_sin_foto_nuestra_no_hay_respaldo(self):
        caso = BusquedaSePagaUnaVez()
        top = [{"id": "MLM1", "posicion": 1, "titulo": "x", "precio": 500.0, "imagen": "https://x/1.jpg",
                "url": "u", "fuente": "mas_vendidos"}]
        r, _ = caso._completo(medidas={}, top=top, datos={"ml_cat_id": "MLM184769"})
        caso.fotos.assert_not_called()
        self.assertEqual(r["rivales"], [])

    def test_el_modelo_exacto_manda(self):
        g = {"modelo": [465, 677, 520], "foto": [129, 137, 138, 159, 160, 170, 175, 180, 465, 520, 677]}
        self.assertEqual(ps.elegir_referencia(g)["grupo"], "modelo")
        g["modelo"] = [465, 677]                               # con menos de 3, el de modelo o diseño
        self.assertEqual(ps.elegir_referencia(g)["grupo"], "foto")

    def test_org_0875_avisa_que_el_modelo_exacto_se_vende_lejos(self):
        ref = ps.elegir_referencia({"foto": [129, 137, 138, 159, 160, 170, 175, 180]})
        a = ps.aviso_modelo_lejos([465, 677], ref)
        self.assertIn("2 publicaciones del MISMO modelo exacto", a)
        self.assertIn("$465–$677", a)
        self.assertIn("$129–$180", a)
        self.assertIsNone(ps.aviso_modelo_lejos([165], ref))             # cerca: no estorba
        self.assertIsNone(ps.aviso_modelo_lejos([], ref))
        self.assertIsNone(ps.aviso_modelo_lejos([465], None))
        self.assertIsNone(ps.aviso_modelo_lejos([465, 677, 520], ps.elegir_referencia({"modelo": [465, 677, 520]})))

    def test_sin_foto_en_woo_el_juez_usa_la_del_packing_list(self):
        # v0.709.3: los SKUs del catálogo que no existen en Woo (OFI-0230-NEG y compañía) no tenían con qué
        # compararse por foto; el juez usa la del renglón del packing list, en memoria.
        caso = BusquedaSePagaUnaVez()
        caso.FOTO_PL = "data:image/jpeg;base64,AAAA"
        filas = _filas(10)
        juez = lambda *a, **k: {"veredictos": {i: {"clase": "mismo", "motivo": "", "unidades_rival": 1}
                                              for i in range(1, 11)}, "usd": 0.003, "unidades_nuestras": 1}
        r, _ = caso._completo(cache={"bolsas thank you regalo": {"filas": filas, "dias": 1}}, juez=juez)
        self.assertEqual(caso.fotos.call_args.args[0], "data:image/jpeg;base64,AAAA")
        self.assertIsNone(r["imagen"])                         # la data-URL no se guarda en la consulta
        self.assertTrue(any("foto del packing list" in a for a in r["avisos"]))

    def test_juez_de_fotos_acepta_data_url(self):
        class Cli:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, **k):
                class R:
                    status_code = 200

                    def json(self):
                        return {"choices": [{"message": {"content": '{"veredicto": "mismo_modelo"}'}}],
                                "usage": {"cost": 0.0003}}
                return R()

        bajar = mock.AsyncMock(return_value="data:rival")
        with mock.patch.object(ps.settings, "openrouter_api_key", "k"),                 mock.patch.object(ps, "_bajar_miniatura", bajar), mock.patch.object(ps.httpx, "AsyncClient", Cli):
            r = asyncio.run(ps.juez_fotos("data:image/jpeg;base64,AAAA", [{"imagen": "https://x/1.jpg"}]))
        self.assertEqual(r["juzgados"], 1)
        self.assertEqual([c.args[1] for c in bajar.call_args_list], ["https://x/1.jpg"])   # solo el del rival


class CatalogoMaestro(unittest.TestCase):
    """v0.696.0: el catálogo no guardó el veredicto de cada publicación de Amazon; se reconstruye de sus cuentas."""

    # TEC-0686-NEG tal como está en el catálogo: 9 «mismo» (6 del mismo paquete + 3 de 2 piezas) y 1 otra gama.
    TEC_0686 = {"vistos": 10, "clases": {"m": 9, "g": 1}, "n": 6, "media": 377.73, "mediana": 370.22, "p25": 295.48,
                "p75": 472.38, "min": 199.99, "max": 549.0,
                "ej": [{"t": "a", "p": 72.74, "id": "B08XK6PQ6Z", "u": 2}, {"t": "b", "p": 99.5, "id": "B0F8VBZ1KW", "u": 2},
                       {"t": "c", "p": 109.5, "id": "B0D8Q1B6JV", "u": 2}, {"t": "d", "p": 199.99, "id": "B0GVJQ7YQ4"}]}
    RES_0686 = [{"id": "B0HHRK2M4S", "p": 498.0}, {"id": "B0D3BNJWGR", "p": 395.5}, {"id": "B0DYDRZK8Y", "p": 344.93},
                {"id": "B0F8VBZ1KW", "p": 199.0}, {"id": "B0D29P28G6", "p": 278.99}, {"id": "B0GVJQ7YQ4", "p": 199.99},
                {"id": "B08XK6PQ6Z", "p": 145.48}, {"id": "B0D8Q1B6JV", "p": 219.0}, {"id": "B0H8RZN1VY", "p": 149.98},
                {"id": "B0DL8WD46K", "p": 549.0}]

    def _prep(self):
        from scripts import catalogo_precios_preparar as prep
        return prep

    def test_estadistica_igual_a_la_del_catalogo(self):
        e = self._prep().estadistica([498.0, 395.5, 344.93, 278.99, 199.99, 549.0])
        llaves = ("n", "media", "mediana", "p25", "p75", "min", "max")
        self.assertEqual({k: e[k] for k in llaves}, {k: self.TEC_0686[k] for k in llaves})

    def test_tec_0686_queda_todo_decidido(self):
        ver, unidades = self._prep().veredictos(self.TEC_0686, self.RES_0686)
        self.assertEqual(ver, "mmmmmmmmxm")              # solo el de $149.98 no hizo match
        self.assertEqual(unidades, {3: 2, 6: 2, 7: 2})

    def test_sin_match_y_combinaciones_que_no_deciden(self):
        prep = self._prep()
        self.assertEqual(prep.veredictos({"clases": {"g": 3}}, [{"id": "A", "p": 1}] * 3)[0], "xxx")
        # 2 «mismo» del mismo paquete entre 3 con el mismo precio: no se sabe cuáles son.
        g = {"clases": {"m": 2, "o": 1}, "n": 2, "media": 100.0, "mediana": 100.0, "p25": 100.0, "p75": 100.0,
             "min": 100.0, "max": 100.0, "ej": []}
        res = [{"id": "A", "p": 100.0}, {"id": "B", "p": 100.0}, {"id": "C", "p": 100.0}]
        self.assertEqual(prep.veredictos(g, res)[0], "???")
        res[2]["p"] = 300.0
        self.assertEqual(prep.veredictos(g, res)[0], "mmx")

    def test_el_extracto_no_lleva_costos(self):
        prep = self._prep()
        fila = {"precio_ml_final": 162.0, "min": 162.0, "max": 256.56, "estimado_costo": 68.21,
                "topado_10x_costo": True, "notas": ["sin fuentes: estimado 3.59× costo"],
                "fuentes": {"keyword_amazon": 256.56, "banda_v3": 162.0}, "fuente": "banda_v3",
                "categoria_banda_v3": "Linternas"}
        self.assertEqual(set(prep._ml(fila)), {"media", "min", "max", "fuentes", "fuente", "categoria"})
        self.assertIsNone(prep._ml({"precio_ml_final": None, "estimado_costo": 68.21}))

    def test_amazon_de_ordena_y_enlaza(self):
        from services import catalogo_precios as cp
        az = {"q": "linterna uv", "vistos": 3, "clases": {"m": 2, "g": 1}, "v": "xm?", "u": {"1": 2}, "n": 1,
              "media": 200.0, "min": 200.0, "max": 200.0}
        a = cp.amazon_de(az, [["B1", "uno", 300.0], ["B2", "dos", 200.0], ["B3", "tres", 100.0]])
        self.assertEqual([(p["id"], p["match"]) for p in a["publicaciones"]],
                         [("B2", "match"), ("B3", "sin_dato"), ("B1", "no_match")])
        self.assertEqual(a["publicaciones"][0]["unidades"], 2)
        self.assertEqual(a["publicaciones"][0]["url"], "https://www.amazon.com.mx/dp/B2")
        self.assertEqual((a["media"], a["n"]), (200.0, 1))

    def test_por_sku_sin_sku(self):
        from services import catalogo_precios as cp
        with mock.patch.object(cp, "_datos", {"meta": {"ml_fecha": "2026-10-12"}, "skus": {}, "busquedas": {}}):
            r = cp.por_sku("NO-EXISTE")
        self.assertEqual((r["ml"], r["amazon"], r["fecha_ml"]), (None, None, "2026-10-12"))


class AmazonEnVivo(unittest.TestCase):
    """v0.696.0: el método del estudio de sábanas (memoria «amazon-estudio-de-mercado-sp-api») en un botón."""

    def test_una_cifra_por_familia_con_caja_de_compra(self):
        from services import amazon_mercado as am
        base = {"marca": "", "nodo": "N", "nodo_nombre": "x"}
        items = [{"asin": "H1", "padre": "P1", "titulo": "Linterna rojo", **base},
                 {"asin": "H2", "padre": "P1", "titulo": "Linterna azul", **base},
                 {"asin": "H3", "padre": "P1", "titulo": "Linterna verde", **base},
                 {"asin": "S1", "padre": "S1", "titulo": "Linterna sola", **base},
                 {"asin": "S2", "padre": "S2", "titulo": "Sin caja", **base}]
        f = am.familias(items, {"H1": 100.0, "H2": 120.0, "H3": 300.0, "S1": 90.0})
        self.assertEqual([(x["id"], x["padre"], x["precio"]) for x in f], [("H1", "P1", 120.0), ("S1", "S1", 90.0)])
        self.assertEqual(f[0]["url"], "https://www.amazon.com.mx/dp/H1")   # la variante: el padre no se compra

    def test_juez_y_nodo_de_la_mayoria(self):
        from services import amazon_mercado as am
        pubs = [{"precio": 100.0, "nodo": "A", "nodo_nombre": "Linternas"},
                {"precio": 110.0, "nodo": "A", "nodo_nombre": "Linternas"},
                {"precio": 120.0, "nodo": "B", "nodo_nombre": "Lámparas"},
                {"precio": 130.0, "nodo": "A", "nodo_nombre": "Linternas"},
                {"precio": 140.0, "nodo": "A", "nodo_nombre": "Linternas"}]
        nodo = am.marcar(pubs, {1: {"clase": "mismo"}, 2: {"clase": "mismo"}, 3: {"clase": "mismo"},
                                4: {"clase": "otro_paquete", "unidades_rival": 2}})
        self.assertEqual(nodo, {"id": "A", "nombre": "Linternas"})
        self.assertEqual([p["match"] for p in pubs], ["match", "match", "no_match", "no_match", "sin_dato"])
        self.assertIn("otra categoría", pubs[2]["motivo"])
        self.assertEqual(pubs[3]["unidades"], 2)

    def test_medidas_del_paquete(self):
        from services import amazon_mercado as am
        it = {"dimensions": [{"marketplaceId": "MX", "package": {
            "length": {"value": 6.7322834577, "unit": "inches"}, "width": {"value": 2.7165354303, "unit": "inches"},
            "height": {"value": 1.9291338563, "unit": "inches"}, "weight": {"value": 0.6393405598, "unit": "pounds"}}}]}
        self.assertEqual(am._paquete(it, "MX"), {"largo_cm": 17.1, "ancho_cm": 6.9, "alto_cm": 4.9, "peso_kg": 0.29})
        self.assertIsNone(am._paquete({"dimensions": [{"marketplaceId": "MX", "item": {}}]}, "MX"))   # un padre
        m = am.mediana_paquete([{"largo_cm": 40.3, "ancho_cm": 29.5, "alto_cm": 4.8, "peso_kg": 0.181},
                                {"largo_cm": 43.2, "ancho_cm": 22.4, "alto_cm": 7.5, "peso_kg": 0.259},
                                {"largo_cm": 39.8, "ancho_cm": 11.4, "alto_cm": 8.4, "peso_kg": 1.08}])
        self.assertEqual(m, {"largo_cm": 41.0, "ancho_cm": 23.0, "alto_cm": 8.0, "peso_kg": 0.26})
        self.assertIsNone(am.mediana_paquete([]))


class Raspador(unittest.TestCase):
    """v0.688.0 en `competencia_scraper`: lo que cobra Apify es el proxy, y llega después."""

    def test_busqueda_sin_imagenes_y_con_reintentos(self):
        from services import competencia_scraper as cs
        visto = {}

        async def correr(payload, limite_lectura, util):
            visto.update(payload)
            return []

        with mock.patch.object(cs, "_con_respaldo", correr):
            asyncio.run(cs.buscar_terminos(["bolsas thank you regalo"], reintentos=3))
        self.assertEqual((visto["downloadMedia"], visto["downloadCss"], visto["maxRequestRetries"]), (False, False, 3))
        with mock.patch.object(cs, "_con_respaldo", correr):
            asyncio.run(cs.buscar_terminos(["x"], sin_media=False))
        self.assertEqual((visto["downloadMedia"], visto["maxRequestRetries"]), (True, 8))   # Competencia: igual

    def test_el_gasto_se_corrige_con_el_cobro_final(self):
        from services import competencia_scraper as cs
        with mock.patch.object(cs.supabase_db, "execute_returning", return_value={"id": 7}), \
                mock.patch.object(cs.threading, "Thread") as hilo:
            cs._registrar_gasto("apify/playwright-scraper", {"id": "R1", "status": "SUCCEEDED",
                                                             "usageTotalUsd": 0.0077, "stats": {}})
        hilo.assert_called_once()
        self.assertEqual(hilo.call_args.kwargs["args"], (7, "R1", 0.0077))
        self.assertTrue(hilo.call_args.kwargs["daemon"])

        resp = mock.Mock()
        resp.json.return_value = {"data": {"usageTotalUsd": 0.038}}
        with mock.patch.object(cs.time, "sleep") as dormir, \
                mock.patch.object(cs.httpx, "get", return_value=resp), \
                mock.patch.object(cs.supabase_db, "execute") as escribir:
            cs._cobro_final(7, "R1", 0.0077)
        dormir.assert_called_once_with(cs._ESPERA_COBRO)
        sql, params = escribir.call_args.args
        self.assertIn("UPDATE ops.process_log", sql)
        self.assertEqual(json.loads(params[0]), {"usd": 0.038, "usd_al_terminar": 0.0077, "usd_final": True})
        self.assertEqual(params[1], 7)

    def test_medir_busquedas_pasa_reintentos_y_entrega_filas(self):
        from services import competencia_captura as cc
        filas = [{"externo_id": "MLM1", "precio": 100}]
        with mock.patch.object(cc.competencia_scraper, "buscar_terminos",
                               mock.AsyncMock(return_value={"t": filas})) as buscar, \
                mock.patch.object(cc, "_nuestras_publicaciones", return_value={}), \
                mock.patch.object(cc.competencia_ml, "reviews", return_value={}), \
                mock.patch.object(cc, "enriquecer_visitas", mock.AsyncMock()), \
                mock.patch.object(cc.competencia_store, "periodo_actual", return_value="2026-10-01"), \
                mock.patch.object(cc.competencia_store, "reemplazar_busqueda", side_effect=RuntimeError("caída")):
            out = {}
            with self.assertRaises(RuntimeError):
                asyncio.run(cc.medir_busquedas(["t"], limite=48, reintentos=3, filas_out=out))
        self.assertEqual(buscar.call_args.kwargs["reintentos"], 3)
        self.assertEqual(out["t"], filas)      # lo pagado llegó aunque el guardado falló


class AvisosDeDatos(unittest.TestCase):
    """v0.687.0: VAR-0549-NEG (50 bolsas) salió en $1,139 con costo $456.45 y 20 kg, cuando la categoría vende a
    ~$159. La pantalla debe decir que lo raro son los datos, no el mercado."""

    def _avisos(self, precio, peso, piso_, costo, mediana, estado):
        return ps.revisar_datos(precio, costos.calc_fee_envio_ml(peso, precio), costos.calc_fee_envio_ml(0.1, precio),
                                peso, piso_, costo, {"mediana": mediana}, estado)

    def test_var_0549_avisa_peso_y_costo(self):
        a = self._avisos(1139, 20.0, 1138.0, 456.45, 159.0, "sin_mercado")
        self.assertEqual(len(a), 2)
        self.assertIn("20 kg", a[0])
        self.assertIn("7.2×", a[1])

    def test_tec_0605_no_avisa(self):
        # $35 de $209 es el 17 %, pero es lo que paga CUALQUIER paquete ligero a ese precio: no es error.
        self.assertEqual(self._avisos(209, 0.132, 208.0, COSTO, 119.89, "no_compite"), [])

    def test_pesado_y_caro_no_avisa_por_envio(self):
        self.assertEqual(self._avisos(3999, 20.0, 3990.0, 2000.0, 3500.0, "compite"), [])

    def test_el_piso_vs_categoria_solo_sin_mercado_o_sin_competir(self):
        self.assertEqual(self._avisos(1139, 0.3, 1138.0, 456.45, 159.0, "compite"), [])
        self.assertEqual(len(self._avisos(1139, 0.3, 1138.0, 456.45, 159.0, "no_compite")), 1)


class Permisos(unittest.TestCase):
    def test_rutas_piden_operador(self):
        for metodo, ruta in (("GET", "/api/precio-sugerido/catalogo/TEC-0605"),
                             ("POST", "/api/precio-sugerido/completo"),
                             ("POST", "/api/precio-sugerido/amazon"),
                             ("GET", "/api/precio-sugerido/ultimo/TEC-0605"),
                             ("POST", "/api/precio-sugerido/medidas-amazon")):
            self.assertEqual(rbac.rol_requerido(metodo, ruta), "operador")
            self.assertFalse(rbac.permite("lectura", metodo, ruta))
            self.assertTrue(rbac.permite("operador", metodo, ruta))


if __name__ == "__main__":
    unittest.main()
