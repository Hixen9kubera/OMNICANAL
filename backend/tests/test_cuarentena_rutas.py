"""Las rutas del vigilante de cuarentena (routers/inventario.py), sin red ni base.

Qué fijan:
  1. El globo no le pregunta a Odoo.
  2. Odoo caído: el popup trae los pendientes igual y dice por qué falta lo demás.
  3. Sin usuario de sesión no se marca nada (401); con usuario, se marca lo dado.
  4. Sin la 0083, un 503 que lo dice, no un 500 mudo.
  5. `/cuarentena` no se lo traga el comodín `/{sku:path}`.
"""
import os
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest import mock

import psycopg2.errors

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import inventario as ruta_inv  # noqa: E402

UTC = timezone.utc
PEND = [{"sku": "ACC-1", "piezas": 5, "ultima": datetime(2026, 11, 7, tzinfo=UTC),
         "llegadas": [{"odoo_move_line_id": 7, "sku": "ACC-1", "fecha": datetime(2026, 11, 7, tzinfo=UTC),
                       "piezas": 5, "desde": "TEX2/FERRAFORME", "documento": "D", "quien": "Q",
                       "cedis": "TEX2"}]}]


@contextmanager
def _cursor():
    yield object()


def _cliente() -> TestClient:
    app = FastAPI()
    app.include_router(ruta_inv.router)
    return TestClient(app)


class Rutas(unittest.TestCase):
    def setUp(self):
        self.parches = [
            mock.patch.object(ruta_inv.sdb, "get_cursor", _cursor),
            mock.patch.object(ruta_inv.cuar, "pendientes", return_value=PEND),
            mock.patch.object(ruta_inv.cuar, "copia", return_value=[]),
            mock.patch.object(ruta_inv.cuar, "ultimas_llegadas", return_value={}),
        ]
        for p in self.parches:
            p.start()

    def tearDown(self):
        mock.patch.stopall()

    def test_pendientes_no_llama_a_odoo(self):
        with mock.patch.object(ruta_inv.cuar, "en_cuarentena") as odoo:
            r = _cliente().get("/api/inventario/cuarentena/pendientes")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["skus"], r.json()["piezas"]), (1, 5))
        odoo.assert_not_called()

    def test_popup_con_odoo_caido_trae_pendientes(self):
        with mock.patch.object(ruta_inv.cuar, "en_cuarentena",
                               return_value={"ok": False, "motivo": "No se pudo leer Odoo (OSError).",
                                             "skus": []}):
            r = _cliente().get("/api/inventario/cuarentena")
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertEqual(j["odoo"], {"ok": False, "motivo": "No se pudo leer Odoo (OSError)."})
        self.assertEqual([(f["sku"], f["nuevo"], f["piezas"]) for f in j["lista"]], [("ACC-1", True, 0)])

    def test_revisar_sin_sesion_401(self):
        with mock.patch.object(ruta_inv.core_actor, "actual", return_value=""), \
                mock.patch.object(ruta_inv.cuar, "revisar") as rev:
            r = _cliente().post("/api/inventario/cuarentena/revisar", json={"ids": [7]})
        self.assertEqual(r.status_code, 401)
        rev.assert_not_called()

    def test_revisar_con_sesion(self):
        with mock.patch.object(ruta_inv.core_actor, "actual", return_value="brandon@kubera.mx"), \
                mock.patch.object(ruta_inv.cuar, "revisar", return_value=2) as rev:
            r = _cliente().post("/api/inventario/cuarentena/revisar", json={"ids": [7, 8]})
        self.assertEqual((r.status_code, r.json()), (200, {"revisadas": 2}))
        self.assertEqual((rev.call_args.args[1], rev.call_args.args[2]), ([7, 8], "brandon@kubera.mx"))

    def test_sin_la_0083_dice_que_falta(self):
        with mock.patch.object(ruta_inv.cuar, "pendientes",
                               side_effect=psycopg2.errors.UndefinedTable("no existe")):
            r = _cliente().get("/api/inventario/cuarentena/pendientes")
        self.assertEqual(r.status_code, 503)
        self.assertIn("0083", r.json()["detail"])

    def test_cuarentena_no_la_traga_la_ruta_comodin(self):
        with mock.patch.object(ruta_inv, "ficha") as ficha, \
                mock.patch.object(ruta_inv.cuar, "en_cuarentena",
                                  return_value={"ok": True, "motivo": None, "skus": []}):
            r = _cliente().get("/api/inventario/cuarentena")
        self.assertEqual(r.status_code, 200)
        self.assertIn("lista", r.json())
        ficha.assert_not_called()


if __name__ == "__main__":
    unittest.main()
