"""Pruebas de la pestaña FULL (`services/fanout_full.py`).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
  1. El aviso se lee del texto que deja `stock_full` («TIPO xN: …»), y un tipo
     que la tabla no conoce cae en «otro» con su nombre crudo: no se esconde.
  2. El cuadre compara la foto contra lo que mueve lo VENDIBLE: sin ajustes ni
     retiros. Caso real del 24-sep, San Corpe: retiró 454 pzs, la foto se movió
     −63 y lo vendible avisado fue −63 → cuadra; contando todo, no.
  3. Los umbrales: hasta 20 pzs cuadra, hasta 60 se revisa, más no cuadra.
  4. Las salidas de Odoo: abiertas por cuenta, en camino = enviado − llegado de
     los envíos aún no cerrados, y las que no tienen número se cuentan aparte.

No se llama a kubera ni a Odoo: las funciones reciben los datos ya leídos.

    cd backend && python -m unittest tests.test_fanout_full -v
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import fanout_full as ff  # noqa: E402


def _aviso(dia: str, cuenta: str, tipo: str, x: int) -> dict:
    return ff.aviso({"hora": f"{dia} 10:00:00", "cuenta": cuenta, "sku": "SKU-1", "accion": "full_ignorado",
                     "resultado": f"{tipo} x{x}: sin efecto en Woo"})


class LeerAviso(unittest.TestCase):
    def test_tipo_y_piezas(self):
        self.assertEqual(ff.leer_aviso("SALE_CONFIRMATION x-1: sin efecto en Woo"), ("SALE_CONFIRMATION", -1))
        self.assertEqual(ff.leer_aviso("TRANSFER_DELIVERY x75: sin efecto en Woo"), ("TRANSFER_DELIVERY", 75))
        self.assertEqual(ff.leer_aviso("ADJUSTMENT x0: AVISO: revisar"), ("ADJUSTMENT", 0))

    def test_texto_raro_no_inventa(self):
        self.assertEqual(ff.leer_aviso("ERROR leyendo la operación"), ("?", 0))
        self.assertEqual(ff.leer_aviso(None), ("?", 0))

    def test_tipo_desconocido_cae_en_otro_con_su_nombre(self):
        a = _aviso("2026-10-02", "BEKURA", "NEW_FANCY_TYPE", 2)
        self.assertEqual((a["grupo"], a["texto"], a["x"]), ("otro", "NEW_FANCY_TYPE", 2))

    def test_sin_sku(self):
        a = ff.aviso({"hora": "2026-10-02 09:00:00", "cuenta": "sancorfashion", "sku": "?",
                      "accion": "full_sin_sku", "resultado": "ADJUSTMENT x-1: no se pudo resolver el SKU"})
        self.assertIsNone(a["sku"])
        self.assertEqual((a["cuenta"], a["nombre"], a["grupo"]), ("SANCORFASHION", "San Corpe", "ajuste"))


class Cuadre(unittest.TestCase):
    def test_umbrales(self):
        self.assertEqual([ff.estado_dif(d) for d in (0, 20, -20, 21, 60, -61, 474)],
                         ["cuadra", "cuadra", "cuadra", "revisar", "revisar", "no_cuadra", "no_cuadra"])

    def test_24sep_san_corpe_cuadra_sin_retiros_ni_ajustes(self):
        d = "2026-09-24"
        avisos = [_aviso(d, "SANCORFASHION", "TRANSFER_DELIVERY", 5),
                  _aviso(d, "SANCORFASHION", "SALE_CONFIRMATION", -67),
                  _aviso(d, "SANCORFASHION", "SALE_DELIVERY_CANCELATION", 4),
                  _aviso(d, "SANCORFASHION", "TRANSFER_RESERVATION", -5),
                  _aviso(d, "SANCORFASHION", "ADJUSTMENT", -20),
                  _aviso(d, "SANCORFASHION", "WITHDRAWAL_DELIVERY", -454)]
        filas = ff.libro(avisos, {(d, "SANCORFASHION"): {"delta": -63, "cambios": 422}}, [d], "2026-10-02")
        sc = next(f for f in filas if f["cuenta"] == "SANCORFASHION")
        self.assertEqual((sc["vendible"], sc["todo"]), (-63, -537))
        self.assertEqual((sc["dif_vendible"], sc["estado_vendible"]), (0, "cuadra"))
        self.assertEqual((sc["dif_todo"], sc["estado_todo"]), (474, "no_cuadra"))
        self.assertEqual(sc["avisos"], 6)
        self.assertFalse(sc["parcial"])

    def test_dia_sin_nada_cuadra_en_cero_y_hoy_es_parcial(self):
        filas = ff.libro([], {}, ["2026-10-02"], "2026-10-02")
        self.assertEqual(len(filas), 2)                       # una fila por cuenta
        for f in filas:
            self.assertEqual((f["vendible"], f["foto"], f["dif_vendible"], f["estado_vendible"]), (0, 0, 0, "cuadra"))
            self.assertTrue(f["parcial"])

    def test_foto_quieta_con_ventas_no_cuadra(self):
        d = "2026-09-29"
        avisos = [_aviso(d, "BEKURA", "SALE_CONFIRMATION", -1) for _ in range(30)]
        k = next(f for f in ff.libro(avisos, {}, [d], "2026-10-02") if f["cuenta"] == "BEKURA")
        self.assertEqual((k["dif_vendible"], k["estado_vendible"]), (30, "revisar"))


class Camino(unittest.TestCase):
    AHORA = datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc)

    def test_abiertas_en_camino_y_sin_numero(self):
        envios = [
            {"canal": "meli", "cuenta": "Kubera", "estado": "abierta", "estado_odoo": "assigned", "pedidas": 700},
            {"canal": "meli", "cuenta": "San Corpe", "estado": "salio", "estado_odoo": "done", "salida": "TEXCO/OUT/06483",
             "etapas": [{"ts": "2026-09-28T15:00:00+00:00"}, {"ts": "2026-09-29T20:05:57+00:00"}, None, None, None],
             "cobertura": {"cerrado": False, "piezas_enviadas": 1095, "piezas_llegadas": 1044}},
            {"canal": "meli", "cuenta": "San Corpe", "estado": "salio", "estado_odoo": "done",
             "cobertura": {"cerrado": True, "piezas_enviadas": 474, "piezas_llegadas": 0}},     # cerrado: ya no va en camino
            {"canal": "meli", "cuenta": None, "estado": "sinEnlazar", "estado_odoo": "done", "cobertura": None},
            {"canal": "meli", "cuenta": "Kubera", "estado": "sinEnlazar", "estado_odoo": "cancel"},
            {"canal": "amazon", "cuenta": None, "estado": "abierta", "estado_odoo": "assigned", "pedidas": 300},
        ]
        r = ff.camino(envios, self.AHORA)
        k, s, sin = r["cuentas"]["BEKURA"], r["cuentas"]["SANCORFASHION"], r["cuentas"][""]
        self.assertEqual((k["abiertas"], k["pzs_abiertas"], k["sin_numero"]), (1, 700, 1))
        self.assertEqual((s["en_proceso"], s["enviadas"], s["llegadas"], s["en_camino"]), (1, 1095, 1044, 51))
        self.assertEqual((sin["sin_numero"], sin["abiertas"], sin["en_proceso"]), (1, 0, 0))
        self.assertEqual(r["faltan"], [{"salida": "TEXCO/OUT/06483", "cuenta": "SANCORFASHION", "nombre": "San Corpe",
                                        "enviadas": 1095, "llegadas": 1044, "falta": 51, "dias": 2}])


if __name__ == "__main__":
    unittest.main()
