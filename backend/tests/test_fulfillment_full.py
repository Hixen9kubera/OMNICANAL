"""Pruebas de «Crear FULL» (services/fulfillment_full.py) y de la ficha del SKU.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
  1. «En camino» no cuenta dos veces ni inventa: salida abierta reciente → lo
     pedido; validada y sin cerrar → lo que ML todavía no avisa; cerrada → nada
     (lo que falta ahí es rechazo); abierta de más de 21 días → orden olvidada,
     se enseña y NO se resta.
  2. La propuesta resta los borradores de SU cuenta y no confunde un hueco con
     un cero (sin publicación → stock en FULL null; sin producto → libre null).
  3. Una orden por almacén: un almacén que lo cubre todo gana; si no, cada
     renglón va ENTERO donde alcance; sólo se parte si ninguno lo cubre solo; y
     lo que Odoo no tiene se RECORTA diciendo por qué.
  4. Con el interruptor APAGADO, crear no escribe nada en Odoo (sólo lee).
  5. Encendido: borrador (nunca confirma), precio 0 y sin impuestos, socio fijo
     de la cuenta, y la misma clave no crea dos veces.

No se llama a Odoo ni a kubera: se sustituye `odoo_ventas._kw` por un falso que
anota cada llamada.

    cd backend && python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import fulfillment_full as ff  # noqa: E402
from services import fulfillment_sku as fs  # noqa: E402

AHORA = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)
TEXCO, TEXCO2 = 135, 150


def _envio(orden="S1", cuenta="Kubera", hecha=True, dias=3, lineas=(("A", 10, 10, 0),),
           cerrado=False, avisos=True):
    """lineas: (sku, pedidas, enviadas, llegadas)."""
    creada = AHORA - timedelta(days=dias + 1)
    salida = AHORA - timedelta(days=dias) if hecha else None
    e = {"canal": "meli", "cuenta": cuenta, "orden": orden, "salida": f"TEXCO/OUT/{orden}",
         "estado_odoo": "done" if hecha else "assigned", "pedidas": sum(l[1] for l in lineas),
         "etapas": [{"ts": creada.isoformat()}, {"ts": salida.isoformat()} if salida else None, None, None, None],
         "lineas": [{"sku": s, "pedidas": p, "enviadas": en if hecha else None, "llegadas": ll}
                    for s, p, en, ll in lineas]}
    if hecha and avisos:
        e["cobertura"] = {"fuente": "avisos", "cerrado": cerrado}
    return e


class EnCamino(unittest.TestCase):
    def test_salida_abierta_reciente_cuenta_lo_pedido(self):
        camino, zombis = ff.en_camino([_envio(hecha=False, dias=2)], AHORA)
        self.assertEqual(camino[("Kubera", "A")]["piezas"], 10)
        self.assertEqual(zombis, [])

    def test_orden_olvidada_no_se_resta(self):
        camino, zombis = ff.en_camino([_envio(orden="S26441", hecha=False, dias=40)], AHORA)
        self.assertNotIn(("Kubera", "A"), camino)
        self.assertEqual(zombis[0]["orden"], "S26441")

    def test_validada_sin_cerrar_cuenta_lo_que_falta_por_llegar(self):
        camino, _ = ff.en_camino([_envio(lineas=(("A", 10, 10, 4), ("B", 5, 5, 5)))], AHORA)
        self.assertEqual(camino[("Kubera", "A")]["piezas"], 6)
        self.assertNotIn(("Kubera", "B"), camino, "B ya llegó completo")

    def test_cerrada_no_esta_en_camino(self):
        camino, _ = ff.en_camino([_envio(lineas=(("A", 10, 10, 4),), cerrado=True, dias=15)], AHORA)
        self.assertEqual(camino, {}, "lo que falta en un envío cerrado es lo que ML no recibió")

    def test_sin_avisos_reciente_toma_lo_enviado(self):
        camino, _ = ff.en_camino([_envio(avisos=False, dias=5)], AHORA)
        self.assertEqual(camino[("Kubera", "A")]["piezas"], 10)

    def test_sin_cuenta_no_entra(self):
        camino, zombis = ff.en_camino([_envio(cuenta=None, hecha=False, dias=1)], AHORA)
        self.assertEqual((camino, zombis), ({}, []))

    def test_las_cuentas_no_se_mezclan(self):
        camino, _ = ff.en_camino([_envio(hecha=False, dias=1), _envio(cuenta="San Corpe", hecha=False, dias=1)],
                                 AHORA)
        self.assertEqual((camino[("Kubera", "A")]["piezas"], camino[("San Corpe", "A")]["piezas"]), (10, 10))


class Propuesta(unittest.TestCase):
    def setUp(self):
        self.ventas = [{"cuenta": "BEKURA", "sku": "A", "v30": 60, "v7": 20, "ultima": date(2026, 9, 23)},
                       {"cuenta": "BEKURA", "sku": "B", "v30": 9, "v7": 1, "ultima": None},
                       {"cuenta": "SANCORFASHION", "sku": "A", "v30": 30, "v7": 7, "ultima": None}]
        self.pubs = [{"cuenta": "BEKURA", "sku": "A", "listing_id": "MLM1", "url": "u", "situacion": "active",
                      "stock_full": 12}]
        self.borradores = [
            {"orden": "S38878", "cuenta": "Kubera", "lineas": [{"sku": "A", "cantidad": 20}]},
            {"orden": "S38919", "cuenta": None, "lineas": [{"sku": "A", "cantidad": 99}]},
        ]
        self.productos = {"A": {"id": 1, "name": "Producto A"}}
        self.libres = {1: {TEXCO: 30, TEXCO2: -4}}
        self.p = ff.armar_propuesta(self.ventas, self.pubs, [_envio(hecha=False, dias=1)], self.borradores,
                                    self.productos, self.libres, AHORA)

    def test_insumos_por_cuenta(self):
        a = self.p["cuentas"]["Kubera"][0]
        self.assertEqual((a["sku"], a["v30"], a["stock_full"], a["en_camino"], a["borrador"]),
                         ("A", 60, 12, 10, 20), "el borrador sin cuenta no se resta")
        self.assertEqual(a["libre"], {"TEXCO": 30, "TEXCO II": 0}, "libre negativo es cero para planear")
        self.assertEqual(self.p["cuentas"]["San Corpe"][0]["borrador"], 0)
        self.assertEqual(self.p["en_camino"], {"Kubera": {"piezas": 10, "skus": 1},
                                               "San Corpe": {"piezas": 0, "skus": 0}})

    def test_hueco_no_es_cero(self):
        b = self.p["cuentas"]["Kubera"][1]
        self.assertIsNone(b["stock_full"], "sin publicación en channel.listings no se sabe")
        self.assertIsNone(b["libre"], "sin producto en Odoo no se sabe")

    def test_semana_de_la_planeacion(self):
        self.assertEqual(self.p["semana"]["semana"], "S39")
        self.assertEqual(self.p["semana"]["lunes"], "2026-09-21")


class Almacenes(unittest.TestCase):
    def _l(self, sku, pid, n):
        return {"sku": sku, "product_id": pid, "cantidad": n}

    def test_un_almacen_que_cubre_todo_gana(self):
        r = ff.repartir_almacenes([self._l("A", 1, 5), self._l("B", 2, 5)],
                                  {1: {TEXCO: 9, TEXCO2: 50}, 2: {TEXCO: 5, TEXCO2: 0}})
        self.assertEqual([p["almacen"] for p in r["partes"]], ["TEXCO"])
        self.assertEqual(r["recortes"], [])

    def test_cada_renglon_entero_donde_alcance(self):
        r = ff.repartir_almacenes([self._l("A", 1, 10), self._l("B", 2, 5)],
                                  {1: {TEXCO: 4, TEXCO2: 20}, 2: {TEXCO: 5, TEXCO2: 0}})
        partes = {p["almacen"]: {l["sku"]: l["cantidad"] for l in p["lineas"]} for p in r["partes"]}
        self.assertEqual(partes, {"TEXCO": {"B": 5}, "TEXCO II": {"A": 10}},
                         "A no se parte 4 + 6 si TEXCO II lo tiene entero")

    def test_se_parte_solo_si_ninguno_alcanza(self):
        r = ff.repartir_almacenes([self._l("A", 1, 10)], {1: {TEXCO: 6, TEXCO2: 7}})
        partes = {p["almacen"]: p["lineas"][0]["cantidad"] for p in r["partes"]}
        self.assertEqual(partes, {"TEXCO": 6, "TEXCO II": 4})

    def test_lo_que_no_hay_se_recorta_y_se_dice(self):
        r = ff.repartir_almacenes([self._l("A", 1, 10), self._l("B", 2, 3)],
                                  {1: {TEXCO: 4, TEXCO2: 2}, 2: {TEXCO: 0, TEXCO2: 0}})
        self.assertEqual(r["recortes"], [
            {"sku": "A", "pedidas": 10, "van": 6, "porque": "sólo hay 6 libres en Odoo"},
            {"sku": "B", "pedidas": 3, "van": 0, "porque": "Odoo no tiene libre"}])
        self.assertEqual(sum(l["cantidad"] for p in r["partes"] for l in p["lineas"]), 6)
        self.assertNotIn("B", [l["sku"] for p in r["partes"] for l in p["lineas"]])


class Limpiar(unittest.TestCase):
    def test_un_renglon_por_sku_y_solo_piezas(self):
        r = ff.limpiar_lineas([{"sku": " A ", "cantidad": 3, "sugerido": 5}, {"sku": "A", "cantidad": 2},
                               {"sku": "B", "cantidad": 0}, {"sku": "", "cantidad": 4},
                               {"sku": "C", "cantidad": "x"}])
        self.assertEqual(r, [{"sku": "A", "cantidad": 5, "sugerido": 5}])


class OdooFalso:
    """Anota cada llamada. Sólo contesta lo que `crear` necesita."""

    def __init__(self, existentes=None, socio=None):
        self.llamadas: list[tuple] = []
        self.existentes = existentes or []
        self.socio = socio
        self.siguiente = 900

    def __call__(self, modelo, metodo, args, kwargs=None):
        self.llamadas.append((modelo, metodo, args, kwargs or {}))
        if (modelo, metodo) == ("product.product", "search_read"):
            return [{"id": 1, "default_code": "A", "name": "Producto A"},
                    {"id": 2, "default_code": "B", "name": "Producto B"}]
        if (modelo, metodo) == ("product.product", "read"):
            wid = (kwargs or {}).get("context", {}).get("warehouse")
            return [{"id": 1, "free_qty": 50 if wid == TEXCO else 0},
                    {"id": 2, "free_qty": 0 if wid == TEXCO else 8}]
        if (modelo, metodo) == ("sale.order", "search_read"):
            return self.existentes
        if (modelo, metodo) == ("res.partner", "search_read"):
            return [{"id": self.socio}] if self.socio else []
        if (modelo, metodo) == ("res.partner", "create"):
            return 777
        if (modelo, metodo) == ("sale.order", "create"):
            self.siguiente += 1
            return self.siguiente
        if (modelo, metodo) == ("sale.order", "read"):
            oid = args[0][0]
            return [{"id": oid, "name": f"S{oid}", "state": "draft", "picking_ids": [], "order_line": [1]}]
        raise AssertionError(f"llamada no esperada a Odoo: {modelo}.{metodo}")

    def escrituras(self):
        return [(m, x) for m, x, *_ in self.llamadas if x not in ("search_read", "read", "search", "search_count")]


LINEAS = [{"sku": "A", "cantidad": 10, "sugerido": 12}, {"sku": "B", "cantidad": 5, "sugerido": 5}]


class Crear(unittest.TestCase):
    def _crear(self, odoo, encendido, clave="a1b2c3d4e5"):
        with mock.patch.object(ff.odoo_ventas, "_kw", odoo), \
             mock.patch.object(ff, "habilitado", lambda refrescar=False: encendido), \
             mock.patch.object(ff, "_guardar_solicitud", lambda *a, **k: True), \
             mock.patch.object(ff.odoo_ventas, "url_orden_publica", lambda: "https://odoo/{id}"):
            return ff.crear("Kubera", LINEAS, "brandon@kubera.mx", clave, {"cobertura_dias": 30})

    def test_apagado_no_escribe_nada(self):
        odoo = OdooFalso()
        r = self._crear(odoo, encendido=False)
        self.assertEqual(r["accion"], "apagado")
        self.assertEqual(odoo.escrituras(), [], "con el interruptor apagado sólo se lee")
        self.assertEqual({p["almacen"]: p["piezas"] for p in r["partes"]}, {"TEXCO": 10, "TEXCO II": 5})

    def test_encendido_crea_borradores_uno_por_almacen(self):
        odoo = OdooFalso()
        r = self._crear(odoo, encendido=True)
        self.assertEqual(r["accion"], "creada")
        self.assertTrue(r["ok"])
        self.assertEqual(odoo.escrituras(), [("res.partner", "create"), ("sale.order", "create"),
                                             ("sale.order", "create")], "nunca action_confirm")
        creadas = [x for x in odoo.llamadas if x[:2] == ("sale.order", "create")]
        v = creadas[0][2][0]
        self.assertEqual(v["partner_id"], 777, "el socio fijo de la cuenta")
        self.assertIn("· a1b2c3d4e5 ·", v["origin"])
        self.assertIn("brandon", v["origin"])
        renglon = v["order_line"][0][2]
        self.assertEqual((renglon["price_unit"], renglon["tax_id"]), (0.0, [(6, 0, [])]))
        self.assertTrue(creadas[0][3]["context"]["tracking_disable"])
        self.assertEqual([o["orden"] for o in r["ordenes"]], ["S901", "S902"])

    def test_la_misma_clave_no_crea_dos_veces(self):
        existentes = [{"id": 5, "name": "S5", "state": "draft", "warehouse_id": [TEXCO, "TEXCO"]},
                      {"id": 6, "name": "S6", "state": "draft", "warehouse_id": [TEXCO2, "TEXCO II"]}]
        odoo = OdooFalso(existentes=existentes, socio=321)
        r = self._crear(odoo, encendido=True)
        self.assertEqual(r["accion"], "ya_existia")
        self.assertEqual(odoo.escrituras(), [])

    def test_sin_clave_no_hace_nada(self):
        odoo = OdooFalso()
        r = self._crear(odoo, encendido=True, clave="x")
        self.assertEqual(r["accion"], "sin_clave")
        self.assertEqual(odoo.llamadas, [])


class FichaSku(unittest.TestCase):
    def test_semanas_de_venta_seguidas_con_ceros(self):
        hoy = date(2026, 9, 24)                              # jueves de la S39
        filas = [{"cuenta": "BEKURA", "date": date(2026, 9, 22), "unidades": 3},
                 {"cuenta": "BEKURA", "date": date(2026, 9, 23), "unidades": 2},
                 {"cuenta": "BEKURA", "date": date(2026, 9, 9), "unidades": 4}]
        s = fs.semanas_de_venta(filas, hoy, n=3)
        self.assertEqual([(x["semana"], x["unidades"]) for x in s["Kubera"]], [("S37", 4), ("S38", 0), ("S39", 5)])
        self.assertTrue(s["Kubera"][-1]["actual"])
        self.assertEqual([x["unidades"] for x in s["San Corpe"]], [0, 0, 0])

    def test_envios_del_sku(self):
        e1 = _envio(orden="S1", lineas=(("A", 10, 10, 10), ("B", 5, 5, 0)))
        e2 = _envio(orden="S2", dias=1, lineas=(("A", 3, 3, 0),))
        r = fs.envios_del_sku("A", [e1, e2])
        self.assertEqual([x["orden"] for x in r], ["S2", "S1"], "lo más reciente primero")
        self.assertEqual((r[1]["enviadas"], r[1]["llegadas"]), (10, 10))


if __name__ == "__main__":
    unittest.main()
