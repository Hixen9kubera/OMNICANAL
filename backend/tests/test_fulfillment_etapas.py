"""Pruebas de las tres etapas que se reconstruyen: llegada, activación y 1ª venta.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
El 17-sep-2026 se midió que **Mercado Libre no tiene API de envíos a Full**: no
hay declaradas, ni estado, ni motivos. Lo único que se puede reconstruir es lo
que el sync ve en `channel.listing_history` y lo que venden esos SKUs. Estas
pruebas fijan las reglas que evitan que esa reconstrucción mienta:

  1. una salida SIN validar no tiene llegada (sería de otro envío del mismo SKU);
  2. sólo cuentan los movimientos DENTRO de la ventana (30 días llegada, 45
     activación): lo de tres meses después no es de este envío;
  3. las fechas reconstruidas viajan como `aprox` — son cuándo se OBSERVÓ;
  4. la cobertura se cuenta por SKU («4 de 6»), para que una pieza de un SKU no
     parezca el envío entero;
  5. si kubera no contesta, las etapas quedan en None y NADA se inventa.

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

ACC = "11111111-1111-1111-1111-111111111111"
SALIDA = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)


def _envio(validada: datetime | None = SALIDA, skus=("A", "B")):
    return {
        "canal": "meli", "cuenta": "Kubera", "estado_odoo": "done" if validada else "waiting",
        "etapas": [{"ts": (SALIDA - timedelta(days=2)).isoformat()},
                   {"ts": validada.isoformat()} if validada else None, None, None, None],
        "lineas": [{"sku": s, "nombre": s, "pedidas": 10, "enviadas": 10} for s in skus],
    }


def _datos(llegadas=None, activaciones=None, ventas=None):
    return {"cuentas": {"BEKURA": ACC, "SANCORFASHION": "otro", "AMAZON": "amz"},
            "llegadas": llegadas or {}, "activaciones": activaciones or {}, "ventas": ventas or {}}


class Atribucion(unittest.TestCase):
    def test_salida_sin_validar_no_tiene_llegada(self):
        e = _envio(validada=None)
        fe.aplicar([e], _datos(llegadas={("A", ACC): ([SALIDA + timedelta(hours=5)], [7])}))
        self.assertIsNone(e["etapas"][2], "sin salida validada no se atribuye nada")
        self.assertNotIn("cobertura", e)

    def test_llegada_dentro_de_la_ventana(self):
        e = _envio()
        cuando = SALIDA + timedelta(days=2)
        fe.aplicar([e], _datos(llegadas={("A", ACC): ([cuando], [7])}))
        self.assertEqual(e["etapas"][2]["ts"], cuando.isoformat())
        self.assertTrue(e["etapas"][2]["aprox"], "es cuándo se observó, no cuándo ocurrió")
        self.assertEqual(e["cobertura"], {"skus": 2, "llegaron": 1, "piezas_llegadas": 7,
                                          "activos": 0, "vendieron": 0})
        self.assertEqual(e["lineas"][0]["piezas_llegadas"], 7)
        self.assertNotIn("llegada", e["lineas"][1], "el SKU sin movimiento no inventa fecha")

    def test_fuera_de_la_ventana_no_cuenta(self):
        e = _envio()
        tarde = SALIDA + timedelta(days=fe.DIAS_LLEGADA + 5)
        antes = SALIDA - timedelta(days=3)
        fe.aplicar([e], _datos(llegadas={("A", ACC): ([antes, tarde], [3, 9])}))
        self.assertIsNone(e["etapas"][2])
        self.assertEqual(e["cobertura"]["llegaron"], 0)

    def test_activacion_y_primera_venta(self):
        e = _envio()
        act = SALIDA + timedelta(days=1)
        fe.aplicar([e], _datos(
            activaciones={("A", ACC): [SALIDA - timedelta(days=10), act]},
            ventas={("A", "BEKURA"): [date(2026, 8, 1), date(2026, 8, 25)]}))
        self.assertEqual(e["etapas"][3]["ts"], act.isoformat(), "la activación vieja no cuenta")
        self.assertEqual(e["etapas"][4]["ts"][:10], "2026-08-25", "la venta anterior a la salida no cuenta")
        # Es un DÍA de México: mediodía CDMX y marcado como día, no medianoche UTC
        # (que en CDMX caía a las 18:00 del día anterior).
        self.assertEqual(e["etapas"][4]["ts"], "2026-08-25T12:00:00-06:00")
        self.assertTrue(e["etapas"][4]["dia"])
        self.assertEqual((e["cobertura"]["activos"], e["cobertura"]["vendieron"]), (1, 1))

    def test_cuenta_equivocada_no_cruza(self):
        e = _envio()
        fe.aplicar([e], _datos(llegadas={("A", "otro"): ([SALIDA + timedelta(days=1)], [5])}))
        self.assertIsNone(e["etapas"][2], "el stock de la otra cuenta no es de este envío")

    def test_envios_viejos_sin_historia(self):
        viejo = datetime(2026, 3, 1, tzinfo=timezone.utc)
        e = _envio(validada=viejo)
        fe.aplicar([e], _datos(llegadas={("A", ACC): ([viejo + timedelta(days=1)], [5])}))
        self.assertIsNone(e["etapas"][2], "antes del 17-jul no hay historia que mirar")

    def test_sin_kubera_no_pasa_nada(self):
        e = _envio()
        fe.aplicar([e], None)
        self.assertEqual(e["etapas"][2:], [None, None, None])


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
