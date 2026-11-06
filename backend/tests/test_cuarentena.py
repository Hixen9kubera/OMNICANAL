"""El vigilante de cuarentena (services/cuarentena.py), sin red ni base.

Qué fijan:
  1. El orden del popup: los nuevos arriba, de la llegada más reciente a la más
     vieja; un pendiente que ya salió de cuarentena se enseña con 0 piezas.
  2. `revisar` marca exactamente lo mostrado, sin repetir y sin tocar la base si
     no hay nada.
  3. Lo que hay hoy en Odoo: suma por SKU; si Odoo falla o renombraron la
     ubicación, lo dice en vez de quedarse callado.
"""
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import cuarentena as cu  # noqa: E402

UTC = timezone.utc


def _llegada(linea, sku, dia, piezas=5):
    return {"odoo_move_line_id": linea, "sku": sku, "fecha": datetime(2026, 11, dia, 20, 0, tzinfo=UTC),
            "piezas": piezas, "desde": "TEX2/FERRAFORME", "documento": f"TEX2/INT/{linea}",
            "quien": "Calidad", "cedis": "TEX2"}


class Cursor:
    def __init__(self, filas=()):
        self.filas = list(filas)
        self.hechas = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.hechas.append((" ".join(sql.split()), params))
        self.rowcount = len(params.get("ids", [])) if isinstance(params, dict) else 0

    def fetchall(self):
        return self.filas


class Pendientes(unittest.TestCase):
    def test_agrupa_por_sku_y_ordena_por_la_mas_reciente(self):
        cur = Cursor([_llegada(1, "ACC-1", 7, 3), _llegada(2, "ACC-2", 8), _llegada(3, "ACC-1", 9, 4)])
        p = cu.pendientes(cur)
        self.assertEqual([(x["sku"], x["piezas"]) for x in p], [("ACC-1", 7), ("ACC-2", 5)])
        self.assertEqual([l["odoo_move_line_id"] for l in p[0]["llegadas"]], [3, 1])
        sql, params = cur.hechas[0]
        self.assertIn("not exists (select 1 from almacen.cuarentena_revisadas", sql)
        self.assertEqual((params["ruta"], params["desde"]),
                         ("TEXCO/FERRAFORME/CUARENTENA", datetime(2026, 11, 6, 18, 0, tzinfo=UTC)))


class Popup(unittest.TestCase):
    def test_orden_del_popup(self):
        pend = cu.pendientes(Cursor([_llegada(1, "NUEVO-7", 7), _llegada(2, "NUEVO-8", 8)]))
        en_cuar = [{"sku": "VIEJO-1", "nombre": "a", "piezas": 50},
                   {"sku": "NUEVO-7", "nombre": "b", "piezas": 5},
                   {"sku": "SIN-LLEGADA", "nombre": "c", "piezas": 99},
                   {"sku": "NUEVO-8", "nombre": "d", "piezas": 5}]
        ultimas = {"VIEJO-1": _llegada(9, "VIEJO-1", 1)}
        filas = cu.armar_popup(pend, en_cuar, ultimas)
        self.assertEqual([f["sku"] for f in filas], ["NUEVO-8", "NUEVO-7", "VIEJO-1", "SIN-LLEGADA"])
        self.assertEqual([f["nuevo"] for f in filas], [True, True, False, False])
        self.assertIsNone(filas[3]["llegada"])

    def test_pendiente_que_ya_salio_aparece_con_cero(self):
        pend = cu.pendientes(Cursor([_llegada(1, "SALIO-1", 7)]))
        filas = cu.armar_popup(pend, [], {})
        self.assertEqual([(f["sku"], f["piezas"], f["nuevo"]) for f in filas], [("SALIO-1", 0, True)])

    def test_el_sku_se_empata_sin_importar_mayusculas(self):
        pend = cu.pendientes(Cursor([_llegada(1, "mue-0359-muÑ", 7)]))
        filas = cu.armar_popup(pend, [{"sku": "MUE-0359-MUÑ", "nombre": "x", "piezas": 2}], {})
        self.assertEqual(len(filas), 1)
        self.assertTrue(filas[0]["nuevo"])


class Revisar(unittest.TestCase):
    def test_revisar_sin_ids_no_toca_la_base(self):
        cur = Cursor()
        self.assertEqual(cu.revisar(cur, [], "brandon@kubera.mx"), 0)
        self.assertEqual(cur.hechas, [])

    def test_revisar_solo_marca_lo_mostrado(self):
        cur = Cursor()
        self.assertEqual(cu.revisar(cur, [12, 11, 12], "brandon@kubera.mx"), 2)
        sql, params = cur.hechas[0]
        self.assertEqual((params["ids"], params["quien"]), ([11, 12], "brandon@kubera.mx"))
        self.assertIn("on conflict (odoo_move_line_id) do nothing", sql)
        self.assertIn("h.fecha >= %(desde)s", sql)          # solo llegadas del vigilante


class EnCuarentena(unittest.TestCase):
    def _odoo(self, nombre=cu.RUTA, quants=(), productos=()):
        def kw(modelo, metodo, args, kwargs=None, *, timeout=None):
            if modelo == "stock.location":
                return [{"id": cu.UBICACION_ID, "complete_name": nombre}] if nombre else []
            if modelo == "stock.quant":
                return list(quants)
            return list(productos)
        return kw

    def test_suma_por_sku_y_salta_los_que_no_tienen_sku(self):
        quants = [{"product_id": [1, "x"], "quantity": 3.0}, {"product_id": [1, "x"], "quantity": 4.0},
                  {"product_id": [2, "x"], "quantity": 10.0}, {"product_id": [3, "x"], "quantity": 1.0}]
        productos = [{"id": 1, "default_code": "ACC-1", "name": "Uno"},
                     {"id": 2, "default_code": "ACC-2", "name": "Dos"},
                     {"id": 3, "default_code": False, "name": "sin código"}]
        with mock.patch.object(cu.od, "_kw_flujo", self._odoo(quants=quants, productos=productos)):
            r = cu.en_cuarentena()
        self.assertTrue(r["ok"])
        self.assertEqual([(s["sku"], s["piezas"]) for s in r["skus"]], [("ACC-2", 10), ("ACC-1", 7)])

    def test_en_cuarentena_ruta_cambiada(self):
        with mock.patch.object(cu.od, "_kw_flujo", self._odoo(nombre="TEXCO/FERRAFORME/MERMA")):
            r = cu.en_cuarentena()
        self.assertEqual((r["ok"], r["skus"]), (False, []))
        self.assertIn("TEXCO/FERRAFORME/MERMA", r["motivo"])
        self.assertIn(cu.RUTA, r["motivo"])

    def test_en_cuarentena_odoo_caido(self):
        with mock.patch.object(cu.od, "_kw_flujo", side_effect=OSError("https://odoo/xmlrpc caído")):
            r = cu.en_cuarentena()
        self.assertEqual((r["ok"], r["skus"]), (False, []))
        self.assertNotIn("https", r["motivo"])               # la URL de Odoo no sale a la pantalla


if __name__ == "__main__":
    unittest.main()
