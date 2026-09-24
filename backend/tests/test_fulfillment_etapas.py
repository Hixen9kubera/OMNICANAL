"""Pruebas de las etapas que se reconstruyen: recibido, activo y 1ª venta.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
Desde v0.545.0 la llegada de Mercado Libre sale de los AVISOS de FULL
(`fbm_stock_operations`, resueltos a tipo/piezas/SKU en `ops.fanout_log`). Medido
el 18-sep: su suma por SKU da exacto lo que salió de Odoo. Estas pruebas fijan
las reglas que evitan que esa medición mienta:

  1. la ventana empieza 4 días antes de que bodega valide (nunca antes de la
     orden): ML puede recibir antes (S35628: recibió el 23-ago, validó el 26),
     pero lo de más atrás es de otro envío;
  2. termina en la siguiente orden del mismo SKU y cuenta: lo de después es de ésa;
  3. RECHAZO sólo con el envío CERRADO (10 días tras la salida); antes, "en proceso";
  4. lo que llega DE MÁS se topa a lo enviado (ML baraja entre bodegas);
  5. si hubo avisos sin SKU legible (publicación con variantes) y faltan piezas,
     se avisa en vez de callar;
  6. lo que Odoo no surtió (0 entregadas) no se espera ni se cuenta como rechazo;
  7. antes del 12-ago (sin avisos resueltos) y sin kubera, NADA se inventa.

Amazon FBA sigue con lo que ve el sync (`channel.listing_history`), como `aprox`.

No se llama a kubera: `aplicar()` recibe los datos ya leídos.

    cd backend && python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import fulfillment_etapas as fe  # noqa: E402

ORDEN = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
SALIDA = ORDEN + timedelta(days=2)
CERRADO = SALIDA + timedelta(days=fe.DIAS_CIERRE + 1)
ABIERTO = SALIDA + timedelta(days=2)


def _envio(skus=("A", "B"), enviadas=10, orden=ORDEN, validada=SALIDA, cuenta="Kubera", canal="meli"):
    return {
        "canal": canal, "cuenta": cuenta, "estado_odoo": "done" if validada else "waiting",
        "etapas": [{"ts": orden.isoformat()},
                   {"ts": validada.isoformat()} if validada else None, None, None, None],
        "lineas": [{"sku": s, "nombre": s, "pedidas": 10,
                    "enviadas": enviadas if validada else None} for s in skus],
    }


def _datos(avisos=None, ventas=None, llegadas=None, activaciones=None):
    return {"cuentas": {"BEKURA": "kub", "SANCORFASHION": "sc", "AMAZON": "amz"},
            "avisos": avisos or {}, "ventas": ventas or {},
            "llegadas": llegadas or {}, "activaciones": activaciones or {}}


def _aviso(*tandas):
    """[(cuándo, piezas), ...] → (fechas, piezas) como las devuelve la consulta."""
    return ([t for t, _ in tandas], [n for _, n in tandas])


class LlegadaPorAvisos(unittest.TestCase):
    def test_la_suma_de_tandas_completa_y_lo_que_no_llego_es_rechazo(self):
        e = _envio()
        t1, t2 = SALIDA + timedelta(days=2), SALIDA + timedelta(days=3)
        fe.aplicar([e], _datos(avisos={("A", "BEKURA"): _aviso((t1, 4), (t2, 6))}), ahora=CERRADO)
        a, b = e["lineas"]
        self.assertEqual((a["llegadas"], a["estado_llegada"]), (10, "completo"))
        self.assertEqual(a["llegada"], t1.isoformat())
        self.assertEqual(a["llegada_ultima"], t2.isoformat())
        self.assertEqual((b["llegadas"], b["estado_llegada"], b["rechazadas"]), (0, "rechazo_total", 10))
        self.assertEqual(e["etapas"][2], {"ts": t1.isoformat()}, "hora del aviso, no del sync: sin aprox")
        c = e["cobertura"]
        self.assertEqual((c["skus"], c["llegaron"], c["completos"], c["rechazadas"]), (2, 1, 1, 10))
        self.assertEqual((c["piezas_enviadas"], c["piezas_llegadas"], c["cerrado"]), (20, 10, True))

    def test_completo_es_la_tanda_que_alcanza_lo_enviado(self):
        e = _envio(skus=("A",))
        t1, t2, t3 = (SALIDA + timedelta(days=d) for d in (1, 2, 6))
        fe.aplicar([e], _datos(avisos={("A", "BEKURA"): _aviso((t1, 4), (t2, 6), (t3, 2))}), ahora=CERRADO)
        self.assertEqual(e["lineas"][0]["completo_en"], t2.isoformat(), "el +2 del día 6 no alarga el envío")
        s = fe.resumir_semanas([e], ahora=CERRADO)["meli"]["semanas"][0]
        self.assertEqual(s["salida_a_completo_dias"], {"n": 1, "mediana": 2.0, "p90": 2.0})

    def test_antes_del_cierre_no_hay_rechazo(self):
        e = _envio()
        fe.aplicar([e], _datos(avisos={("A", "BEKURA"): _aviso((SALIDA + timedelta(days=1), 3))}),
                   ahora=ABIERTO)
        self.assertEqual(e["lineas"][0]["estado_llegada"], "en_proceso")
        self.assertEqual(e["lineas"][1]["estado_llegada"], "en_proceso")
        self.assertNotIn("rechazadas", e["lineas"][1])
        self.assertIsNone(e["cobertura"]["rechazadas"], "abierto: todavía no se sabe")
        self.assertFalse(e["cobertura"]["cerrado"])

    def test_rechazo_parcial(self):
        e = _envio(skus=("A",))
        fe.aplicar([e], _datos(avisos={("A", "BEKURA"): _aviso((SALIDA + timedelta(days=2), 7))}),
                   ahora=CERRADO)
        self.assertEqual((e["lineas"][0]["estado_llegada"], e["lineas"][0]["rechazadas"]),
                         ("rechazo_parcial", 3))

    def test_ml_recibe_antes_de_que_bodega_valide(self):
        e = _envio(skus=("A",))
        antes = ORDEN + timedelta(days=1)            # S35628: 23-ago contra validación del 26
        fe.aplicar([e], _datos(avisos={("A", "BEKURA"): _aviso((antes, 10))}), ahora=CERRADO)
        self.assertEqual(e["lineas"][0]["estado_llegada"], "completo")
        self.assertEqual(e["etapas"][2]["ts"], antes.isoformat())

    def test_lo_de_mas_de_4_dias_antes_de_validar_es_de_otro_envio(self):
        salida = ORDEN + timedelta(days=10)
        e = _envio(skus=("A",), validada=salida)
        avisos = {("A", "BEKURA"): _aviso((salida - timedelta(days=5), 10), (salida - timedelta(days=3), 10))}
        fe.aplicar([e], _datos(avisos=avisos), ahora=salida + timedelta(days=fe.DIAS_CIERRE + 1))
        self.assertEqual(e["lineas"][0]["llegadas"], 10, "la tanda de 5 días antes es del envío anterior")
        self.assertEqual(e["etapas"][2]["ts"], (salida - timedelta(days=3)).isoformat())

    def test_lo_anterior_a_la_orden_no_es_de_este_envio(self):
        e = _envio(skus=("A",))
        fe.aplicar([e], _datos(avisos={("A", "BEKURA"): _aviso((ORDEN - timedelta(hours=1), 10))}),
                   ahora=CERRADO)
        self.assertEqual(e["lineas"][0]["llegadas"], 0)
        self.assertIsNone(e["etapas"][2])

    def test_la_ventana_se_corta_en_la_siguiente_orden(self):
        e1 = _envio(skus=("A",))
        orden2 = ORDEN + timedelta(days=10)
        e2 = _envio(skus=("A",), orden=orden2, validada=orden2 + timedelta(days=1))
        avisos = {("A", "BEKURA"): _aviso((ORDEN + timedelta(days=3), 10), (orden2 + timedelta(days=2), 10))}
        fe.aplicar([e1, e2], _datos(avisos=avisos), ahora=orden2 + timedelta(days=20))
        self.assertEqual(e1["lineas"][0]["llegadas"], 10, "lo del día 12 es de la segunda orden")
        self.assertEqual(e2["lineas"][0]["llegadas"], 10)

    def test_llegar_de_mas_se_topa_a_lo_enviado(self):
        e = _envio(skus=("A",))
        fe.aplicar([e], _datos(avisos={("A", "BEKURA"): _aviso((SALIDA + timedelta(days=2), 12))}),
                   ahora=CERRADO)
        a = e["lineas"][0]
        self.assertEqual((a["estado_llegada"], a["llegadas_extra"]), ("completo", 2))
        self.assertEqual(e["cobertura"]["piezas_llegadas"], 10, "no se cuentan piezas que no se mandaron")

    def test_la_otra_cuenta_no_cruza(self):
        e = _envio(skus=("A",))
        fe.aplicar([e], _datos(avisos={("A", "SANCORFASHION"): _aviso((SALIDA + timedelta(days=1), 10))}),
                   ahora=CERRADO)
        self.assertEqual(e["lineas"][0]["llegadas"], 0)

    def test_avisos_sin_sku_se_advierten_si_faltan_piezas(self):
        e = _envio(skus=("A",))
        avisos = {("?", "BEKURA"): _aviso((SALIDA + timedelta(days=1), 20), (SALIDA + timedelta(days=2), 11))}
        fe.aplicar([e], _datos(avisos=avisos), ahora=CERRADO)
        self.assertEqual(e["cobertura"]["sin_sku"], {"piezas": 31, "avisos": 2})

    def test_lo_sin_sku_despues_del_cierre_no_se_le_cuelga(self):
        e = _envio(skus=("A",))
        tarde = SALIDA + timedelta(days=fe.DIAS_CIERRE + 2)
        fe.aplicar([e], _datos(avisos={("?", "BEKURA"): _aviso((tarde, 30))}),
                   ahora=tarde + timedelta(days=1))
        self.assertNotIn("sin_sku", e["cobertura"])

    def test_sin_faltantes_no_hay_advertencia(self):
        e = _envio(skus=("A",))
        avisos = {("A", "BEKURA"): _aviso((SALIDA + timedelta(days=1), 10)),
                  ("?", "BEKURA"): _aviso((SALIDA + timedelta(days=1), 20))}
        fe.aplicar([e], _datos(avisos=avisos), ahora=CERRADO)
        self.assertNotIn("sin_sku", e["cobertura"])

    def test_lo_que_odoo_no_surtio_no_es_rechazo(self):
        e = _envio(skus=("A",), enviadas=0)
        fe.aplicar([e], _datos(), ahora=CERRADO)
        self.assertIsNone(e["lineas"][0]["estado_llegada"])
        self.assertNotIn("rechazadas", e["lineas"][0])
        self.assertEqual(e["cobertura"]["rechazadas"], 0)

    def test_salida_abierta_no_se_juzga(self):
        e = _envio(skus=("A",), validada=None)
        fe.aplicar([e], _datos(avisos={("A", "BEKURA"): _aviso((ORDEN + timedelta(days=1), 4))}),
                   ahora=CERRADO)
        self.assertEqual(e["lineas"][0]["estado_llegada"], "llegando")
        self.assertIsNone(e["cobertura"]["rechazadas"])
        self.assertIsNotNone(e["etapas"][2], "lo que ya llegó se enseña aunque Odoo no valide")

    def test_activo_es_cuando_llego_el_ultimo_sku(self):
        e = _envio()
        t1, t2 = SALIDA + timedelta(days=1), SALIDA + timedelta(days=3)
        avisos = {("A", "BEKURA"): _aviso((t1, 10)), ("B", "BEKURA"): _aviso((t2, 10))}
        fe.aplicar([e], _datos(avisos=avisos), ahora=CERRADO)
        self.assertEqual((e["etapas"][2]["ts"], e["etapas"][3]["ts"]), (t1.isoformat(), t2.isoformat()))

    def test_primera_venta_desde_que_llego(self):
        e = _envio(skus=("A",))
        llega = datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc)
        fe.aplicar([e], _datos(avisos={("A", "BEKURA"): _aviso((llega, 10))},
                               ventas={("A", "BEKURA"): [date(2026, 8, 23), date(2026, 8, 25)]}),
                   ahora=CERRADO)
        # Es un DÍA de México: mediodía CDMX y marcado como día, no medianoche UTC
        # (que en CDMX caía a las 18:00 del día anterior).
        self.assertEqual(e["etapas"][4], {"ts": "2026-08-25T12:00:00-06:00", "dia": True},
                         "la venta anterior a la llegada no cuenta")

    def test_antes_de_los_avisos_no_se_juzga(self):
        viejo = datetime(2026, 7, 1, tzinfo=timezone.utc)
        e = _envio(orden=viejo, validada=viejo + timedelta(days=2))
        fe.aplicar([e], _datos(), ahora=CERRADO)
        self.assertNotIn("cobertura", e, "sin avisos no hay con qué decir 'rechazado'")
        self.assertEqual(e["etapas"][2:], [None, None, None])

    def test_sin_cuenta_no_se_mira_ninguna_bodega(self):
        e = _envio(cuenta=None)
        fe.aplicar([e], _datos(avisos={("A", "BEKURA"): _aviso((SALIDA, 10))}), ahora=CERRADO)
        self.assertNotIn("cobertura", e)

    def test_sin_kubera_no_pasa_nada(self):
        e = _envio()
        fe.aplicar([e], None)
        self.assertEqual(e["etapas"][2:], [None, None, None])


class Semanas(unittest.TestCase):
    """`resumir_semanas`: lo que pinta Análisis. Por semana de la SALIDA VALIDADA,
    lo enviado y lo que de ESO recibió ML. Cerrado aporta NO recibidas; abierto,
    «en recepción» (todavía no es rechazo). La tasa, sólo sobre lo cerrado."""

    def setUp(self):
        self.e1 = _envio(skus=("A", "B"))                           # S34, cierra: A llega, B no
        salida2 = CERRADO - timedelta(days=2)
        self.e2 = _envio(skus=("C",), orden=salida2 - timedelta(days=1), validada=salida2)   # S36, abierto
        avisos = {("A", "BEKURA"): _aviso((SALIDA + timedelta(days=2), 10)),
                  ("C", "BEKURA"): _aviso((salida2 + timedelta(days=1), 4))}
        fe.aplicar([self.e1, self.e2], _datos(avisos=avisos), ahora=CERRADO)
        self.r = fe.resumir_semanas([self.e1, self.e2], ahora=CERRADO)

    def test_semana_cerrada_y_semana_en_recepcion(self):
        serie = self.r["meli"]["semanas"]
        self.assertEqual([s["semana"] for s in serie], ["S34", "S35", "S36"])
        s34, s35, s36 = serie
        self.assertEqual((s34["enviadas"], s34["recibidas"], s34["no_recibidas"], s34["tasa"]),
                         (20, 10, 10, 50.0))
        self.assertEqual(s35["envios"], 0, "una semana sin salidas es un cero real, no un hueco")
        self.assertEqual((s36["enviadas"], s36["recibidas"], s36["en_recepcion"], s36["no_recibidas"]),
                         (10, 4, 6, 0), "lo que falta de un envío abierto es recepción, no rechazo")
        self.assertIsNone(s36["tasa"], "sin envíos cerrados no hay tasa")
        self.assertTrue(s36["actual"])
        self.assertFalse(s34["actual"])

    def test_por_cuenta(self):
        self.assertEqual(self.r["meli:Kubera"]["semanas"][0]["no_recibidas"], 10)
        self.assertNotIn("meli:San Corpe", self.r)

    def test_tiempos_de_la_semana(self):
        s34 = self.r["meli"]["semanas"][0]
        self.assertEqual(s34["salida_a_primera_llegada_dias"]["mediana"], 2.0)
        self.assertEqual(s34["orden_a_salida_dias"]["mediana"], 2.0)

    def test_lo_abierto_va_por_validar_y_no_a_una_semana(self):
        abierta = _envio(skus=("Z",), validada=None)
        fe.aplicar([abierta], _datos(), ahora=CERRADO)
        r = fe.resumir_semanas([abierta], ahora=CERRADO)
        self.assertEqual(r["meli"]["por_validar"], {"envios": 1, "pedidas": 10})
        self.assertEqual(r["meli"]["semanas"], [])

    def test_sin_medicion_es_enviado_y_no_no_recibido(self):
        sin_cuenta = _envio(skus=("A",), cuenta=None)
        fe.aplicar([sin_cuenta], _datos(), ahora=CERRADO)
        r = fe.resumir_semanas([sin_cuenta], ahora=CERRADO)
        s = r["meli"]["semanas"][0]
        self.assertEqual((s["enviadas"], s["medidos"], s["no_recibidas"], s["tasa"]), (10, 0, 0, None))
        self.assertIn("meli:sin_asignar", r)

    def test_odoo_no_surtio_y_sin_numero(self):
        e = _envio(skus=("A",))
        e.update(pedidas=12, piezas=10, faltante_odoo=2, envio=None)
        r = fe.resumir_semanas([e], ahora=CERRADO)
        s = r["meli"]["semanas"][0]
        self.assertEqual((s["pedidas"], s["enviadas"], s["no_surtidas"], s["sin_numero"]), (12, 10, 2, 1))


class VendidasDesdeQueLlego(unittest.TestCase):
    def _con_ventas(self, dias, unidades, llegan=10):
        e = _envio(skus=("A",))
        d = _datos(avisos={("A", "BEKURA"): _aviso((SALIDA + timedelta(days=2), llegan))})
        d["ventas_unidades"] = {("A", "BEKURA"): (dias, unidades)}
        fe.aplicar([e], d, ahora=CERRADO)
        return e

    def test_cuenta_desde_la_llegada_y_se_topa_a_lo_que_llego(self):
        # llega el 24-ago (mediodía CDMX): la venta del 23 no cuenta; 7 + 9 = 16, topado a 10
        e = self._con_ventas([date(2026, 8, 23), date(2026, 8, 25), date(2026, 8, 26)], [5, 7, 9])
        self.assertEqual(e["lineas"][0]["vendidas"], 10)
        self.assertEqual(e["cobertura"]["piezas_vendidas"], 10)

    def test_menos_ventas_que_piezas(self):
        e = self._con_ventas([date(2026, 8, 25)], [3])
        self.assertEqual(e["lineas"][0]["vendidas"], 3)


class AmazonConElSync(unittest.TestCase):
    def test_fba_sigue_con_listing_history_y_aprox(self):
        e = _envio(canal="amazon", cuenta="San Corpe")
        cuando = SALIDA + timedelta(days=2)
        fe.aplicar([e], _datos(llegadas={("A", "amz"): ([cuando], [7])}), ahora=CERRADO)
        self.assertEqual(e["etapas"][2], {"ts": cuando.isoformat(), "aprox": True})
        self.assertEqual(e["cobertura"]["fuente"], "sync")
        self.assertEqual(e["lineas"][0]["piezas_llegadas"], 7)


class Canales(unittest.TestCase):
    def test_fba_usa_la_cuenta_amazon(self):
        self.assertEqual(fe._codigo("amazon", "San Corpe"), "AMAZON")
        self.assertEqual(fe._codigo("meli", "San Corpe"), "SANCORFASHION")
        self.assertEqual(fe._codigo("meli", "Kubera"), "BEKURA")

    def test_sin_cuenta_no_hay_codigo(self):
        self.assertIsNone(fe._codigo("meli", None), "sin cuenta no se puede cruzar nada")
        self.assertIsNone(fe._codigo("walmart", None))


if __name__ == "__main__":
    unittest.main()
