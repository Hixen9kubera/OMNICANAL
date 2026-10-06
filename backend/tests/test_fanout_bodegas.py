"""Pruebas de la pestaña Bodegas (`services/fanout_bodegas.py`).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
  1. «Woo esperado» es EXACTAMENTE lo de `stock_watch._deltas_odoo` con la bandera
     `stock_watch_lee_kubera` apagada: max(0, Odoo − vendidas sin orden) en
     absoluto, Odoo crudo sin la resta, nada en delta ni con los pendientes sin
     medir. Con la bandera encendida (y stock_watch sabiendo leerla) suma el libre
     de kubera que cuenta para Woo: max(0, max(0, Odoo) + kubera − pendientes).
  2. Encendida en la tabla pero sin soporte en stock_watch, kubera NO suma y la
     pestaña lo dice.
  3. El universo: algo en TEX2, saldo en kubera ≠ 0 (las filas en 0 de ENSAYO no
     entran) o renglón de formato; más el buscado.
  4. Odoo caído no truena: sin lectura previa, `odoo.ok = false` y siguen kubera
     y Woo; con lectura previa, se sirve la vieja con su edad.
  5. Sin las tablas de la 0064/0065, 200 con `tablas.ok = false` y ninguna
     consulta a esas tablas.
  6. Estado vacío (TEX3 apagada, sin banderas, sin formatos): «qué falta» en
     cuatro pasos sin hacer y las banderas apagadas por «sin fila».
  7. Odoo sólo se lee: la lista blanca rechaza todo lo que no sea search_read o
     read_group, y es la única puerta a Odoo del módulo. Y kubera sólo se lee:
     toda sentencia `_SQL_*` es un `select` sin `for update`.
  8. «Coincide» no se compara consigo mismo: si Odoo HOY no es el de la foto, o
     hubo una venta/orden después de la pasada, la fila queda `por_copiar` (no
     «de más / de menos»). Y `woo_esperado` + `coincide` dicen «igual» justo
     cuando la función REAL `stock_watch._deltas_odoo` no escribiría nada.
  9. Las vendidas sin orden se buscan con el código exacto, como stock_watch.
 10. El cajón respeta el minuto sin Odoo tras una falla y usa la caché.

No se llama a kubera ni a Odoo: `sdb.fetch_*` y `odoo._kw_flujo` van con mocks.

    cd backend && python -m unittest tests.test_fanout_bodegas -v
"""
from __future__ import annotations

import asyncio
import gzip
import json
import re
import sys
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import fanout_bodegas as fb  # noqa: E402
from services import odoo  # noqa: E402


def _limpiar() -> None:
    fb._cache.update(t=0.0, v=None, b=None, gz=None)
    fb._tablas_cache.update(t=0.0, v=None)
    fb._lento_cache.update(t=0.0, v=None)
    fb._vistas_cache.update(t=0.0, v=None)
    fb._odoo_cache.update(t=0.0, llaves=frozenset(), v=None, leido=None, fallo_t=0.0, fallo=None)
    fb._odoo_uno.clear()


ALMACENES = [  # la semilla de la 0064 (guía §2.1)
    {"codigo": "TEXCO", "nombre": "TEXCO", "fuente": "odoo", "odoo_warehouse_id": 135, "preferencia": 1,
     "surte_ventas": True, "admite_ov": False, "cuenta_para_woo": True, "motivo": None, "actualizado": None},
    {"codigo": "TEX2", "nombre": "TEXCO II", "fuente": "odoo", "odoo_warehouse_id": 150, "preferencia": 2,
     "surte_ventas": True, "admite_ov": False, "cuenta_para_woo": True, "motivo": None, "actualizado": None},
    {"codigo": "DROP", "nombre": "DROP OFF", "fuente": "odoo", "odoo_warehouse_id": 142, "preferencia": None,
     "surte_ventas": False, "admite_ov": False, "cuenta_para_woo": True, "motivo": None, "actualizado": None},
    {"codigo": "TEX3", "nombre": "TEXCO III", "fuente": "kubera", "odoo_warehouse_id": None, "preferencia": 3,
     "surte_ventas": False, "admite_ov": False, "cuenta_para_woo": False, "motivo": None, "actualizado": None},
    {"codigo": "ENSAYO", "nombre": "Bodega de ensayo", "fuente": "kubera", "odoo_warehouse_id": None,
     "preferencia": None, "surte_ventas": False, "admite_ov": True, "cuenta_para_woo": False, "motivo": None,
     "actualizado": None},
    {"codigo": "REVISION", "nombre": "Revisión de devoluciones", "fuente": "kubera", "odoo_warehouse_id": None,
     "preferencia": None, "surte_ventas": False, "admite_ov": False, "cuenta_para_woo": False, "motivo": None,
     "actualizado": None},
]
ALM = {a["codigo"]: a for a in ALMACENES}


class FakeKubera:
    """Contesta por la marca `/* bodegas:… */` de cada sentencia y anota cuáles corrieron."""

    def __init__(self, **datos: Any) -> None:
        self.d = {
            "tablas": {"almacenes": True, "stock_almacen": True, "stock_formato": True,
                       "stock_formato_linea": True, "stock_mov": True, "ov_ordenes": True, "ov_lineas": True,
                       "vigia": True, "stock_kubera": True},
            "pasada": {"ahora": "2026-10-06 12:00:00", "ultima": "2026-10-06 11:50:00",
                       "ultima_ts": "2026-10-06T17:50:00+00:00", "edad_s": 600, "filas": 3},
            "almacenes": ALMACENES, "banderas": [], "saldos": [], "puertas": [],
            "formatos": {"por_confirmar": 0, "confirmados": 0, "esperando": 0, "abiertas_hoy": 0,
                         "abiertas": 0, "movimientos": 0, "ov_abiertas": 0},
            "foto": {
                "SKU-A": {"sku": "SKU-A", "stock_woo": 8, "stock_odoo": 10, "stock_kubera": None,
                          "hora": "2026-10-06 11:50:00"},
                "SKU-B": {"sku": "SKU-B", "stock_woo": 6, "stock_odoo": 4, "stock_kubera": None,
                          "hora": "2026-10-06 11:50:00"},
                "SKU-T3": {"sku": "SKU-T3", "stock_woo": 2, "stock_odoo": 2, "stock_kubera": None,
                           "hora": "2026-10-06 11:50:00"},
            },
            "libro": [], "renglones_sku": [], "ov_sku": [], "saldos_sku": [], "recientes": [],
        }
        self.d.update(datos)
        self.corridas: list[str] = []
        self.sqls: list[str] = []

    def _marca(self, sql: str) -> str:
        m = re.search(r"/\* bodegas:(\w+) \*/", sql)
        if not m:
            raise AssertionError(f"sentencia sin marca: {sql[:80]}")
        self.corridas.append(m.group(1))
        self.sqls.append(sql)
        return m.group(1)

    def fetch_all(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        k = self._marca(sql)
        if k == "foto":
            return [dict(self.d["foto"][s.upper()]) for s in params["s"] if s.upper() in self.d["foto"]]
        if k == "banderas":
            return [dict(b) for b in self.d["banderas"] if b["flag"] in params["f"]]
        if k == "vigia":
            return []
        return [dict(x) for x in self.d[k]]

    def fetch_one(self, sql: str, params: Any = None) -> dict[str, Any] | None:
        return dict(self.d[self._marca(sql)])


class FakeOdoo:
    """Odoo de mentira: tres almacenes, quants por producto y el catálogo. El
    `free_qty` de cada producto es Σ (cantidad − reservado) de sus quants."""

    PRODUCTOS = [
        {"id": 1, "default_code": "SKU-A", "name": "Producto A", "active": True},
        {"id": 2, "default_code": "SKU-B", "name": "Producto B", "active": True},
        {"id": 3, "default_code": "SKU-T3", "name": "Producto T3", "active": True},
    ]
    # raíz de ubicación → {product_id: (quantity, reserved_quantity)}
    QUANTS = {11: {1: (4, 0), 3: (2, 0)},      # TEXCO
              12: {1: (6, 0), 2: (5, 1)},      # TEX2
              13: {}}                           # DROP

    def __init__(self, falla: Exception | None = None) -> None:
        self.falla = falla
        self.llamadas: list[tuple[str, str]] = []
        self.productos = [dict(p) for p in self.PRODUCTOS]
        self.quants = {r: dict(q) for r, q in self.QUANTS.items()}
        self.dominios: list[list] = []

    def free(self, pid: int) -> float:
        return float(sum(q - r for por in self.quants.values() for p, (q, r) in por.items() if p == pid))

    @staticmethod
    def _cumple(p: dict, dominio: list) -> bool:
        for t in dominio:
            if not isinstance(t, list):
                raise AssertionError(f"el fake sólo entiende dominios con AND: {dominio}")
            campo, op, val = t
            v = p.get(campo)
            if op == "in" and v not in val:
                return False
            if op == "not in" and v in val:
                return False
            if op == "=ilike":
                lit = val.replace("\\_", "_").replace("\\%", "%").replace("\\\\", "\\")
                if str(v or "").lower() != lit.lower():
                    return False
        return True

    def __call__(self, modelo: str, metodo: str, args: list, kwargs: dict | None = None, *,
                 timeout: float | None = None) -> Any:
        self.llamadas.append((modelo, metodo))
        if self.falla:
            raise self.falla
        if modelo == "stock.warehouse":
            return [{"id": 135, "code": "TEXCO", "view_location_id": [11, "TEXCO"]},
                    {"id": 150, "code": "TEX2", "view_location_id": [12, "TEX2"]},
                    {"id": 142, "code": "DROP", "view_location_id": [13, "DROP"]}]
        if modelo == "stock.quant":
            dominio = args[0]
            raiz = next(t[2] for t in dominio if isinstance(t, list) and t[1] == "child_of")
            ids = next((t[2] for t in dominio if isinstance(t, list) and t[0] == "product_id"), None)
            return [{"product_id": [pid, f"[x] {pid}"], "quantity": q, "reserved_quantity": r, "__count": 1}
                    for pid, (q, r) in self.quants[raiz].items() if ids is None or pid in ids]
        if modelo == "product.product":
            self.dominios.append(args[0])
            self.assertar_campos(kwargs or {})
            return [{**p, "free_qty": self.free(p["id"])} for p in self.productos if self._cumple(p, args[0])]
        raise AssertionError(f"llamada inesperada {modelo}.{metodo}")

    @staticmethod
    def assertar_campos(kwargs: dict) -> None:
        if "free_qty" not in kwargs.get("fields", []):
            raise AssertionError("product.product sin free_qty")
        if (kwargs.get("context") or {}).get("active_test") is not False:
            raise AssertionError("product.product sin active_test=False")


CFG = {"habilitado": True, "solo_registro": False, "tope": 300, "modo": "absoluto (Odoo master)",
       "absoluto": True, "resta": True, "dias": 14, "estado_memoria": None}


class ConMocks(unittest.TestCase):
    """Arma la pestaña con kubera y Odoo de mentira."""

    def setUp(self) -> None:
        _limpiar()
        self.kub = FakeKubera()
        self.odoo = FakeOdoo()
        self._parches = [
            mock.patch.object(fb.sdb, "fetch_all", side_effect=lambda s, p=None: self.kub.fetch_all(s, p)),
            mock.patch.object(fb.sdb, "fetch_one", side_effect=lambda s, p=None: self.kub.fetch_one(s, p)),
            mock.patch.object(odoo, "_kw_flujo", side_effect=lambda *a, **k: self.odoo(*a, **k)),
            mock.patch.object(fb, "_config_stock_watch", side_effect=lambda: dict(CFG)),
            mock.patch.object(fb, "_suma_kubera", return_value=None),
            mock.patch("services.stock_watch._pendientes",
                       return_value={"pend": {"SKU-A": 2}, "ventas": 1, "mas_vieja_h": 3.0, "muestra": []}),
        ]
        for p in self._parches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._parches):
            p.stop()
        _limpiar()

    def fila(self, d: dict, sku: str) -> dict:
        return next(f for f in d["filas"] if f["sku"] == sku)


# ── 1. Woo esperado ──────────────────────────────────────────────────────────

class WooEsperado(unittest.TestCase):
    def test_apagada_absoluto_resta_pendientes(self):
        v, motivo, _ = fb.woo_esperado(12, 2, absoluto=True, resta=True)
        self.assertEqual((v, motivo), (10, "calculado"))

    def test_apagada_kubera_no_suma(self):
        v, _, _ = fb.woo_esperado(12, 2, absoluto=True, resta=True, lee_kubera=False, libre_kub=50)
        self.assertEqual(v, 10)

    def test_pendientes_mayores_que_odoo_da_cero(self):
        self.assertEqual(fb.woo_esperado(3, 5, absoluto=True, resta=True)[0], 0)

    def test_sin_resta_copia_odoo_crudo(self):
        self.assertEqual(fb.woo_esperado(12, 2, absoluto=True, resta=False)[0], 12)

    def test_delta_no_tiene_esperado(self):
        v, motivo, texto = fb.woo_esperado(12, 2, absoluto=False, resta=False)
        self.assertIsNone(v)
        self.assertEqual(motivo, "delta")
        self.assertIn("delta", texto.lower())

    def test_pendientes_sin_medir_no_copia(self):
        v, motivo, _ = fb.woo_esperado(12, 0, absoluto=True, resta=True, ciega=True)
        self.assertEqual((v, motivo), (None, "ciega"))

    def test_odoo_no_lo_conoce(self):
        self.assertEqual(fb.woo_esperado(None, 0, absoluto=True, resta=True)[:2], (None, "sin_odoo"))

    def test_encendida_suma_kubera(self):
        v, _, texto = fb.woo_esperado(12, 2, absoluto=True, resta=True, lee_kubera=True, libre_kub=5)
        self.assertEqual(v, 15)
        self.assertIn("kubera 5", texto)

    def test_encendida_solo_kubera(self):
        # Odoo no lo conoce pero TEX3 sí: max(0, 0 + 4 − 1) = 3
        self.assertEqual(fb.woo_esperado(None, 1, absoluto=True, resta=True, lee_kubera=True, libre_kub=4)[0], 3)

    def test_encendida_sin_nada_no_toca(self):
        self.assertEqual(fb.woo_esperado(None, 0, absoluto=True, resta=True, lee_kubera=True)[1], "sin_odoo")

    def test_encendida_resta_y_tope_cero(self):
        self.assertEqual(fb.woo_esperado(1, 9, absoluto=True, resta=True, lee_kubera=True, libre_kub=2)[0], 0)


class LibreKubera(unittest.TestCase):
    def test_tex3_apagada_no_cuenta(self):
        self.assertEqual(fb.libre_kubera([{"almacen": "TEX3", "libre": 7}], ALM), 0)

    def test_cuenta_para_woo_suma_y_negativo_vale_cero(self):
        alm = {**ALM, "TEX3": {**ALM["TEX3"], "cuenta_para_woo": True, "surte_ventas": True, "admite_ov": True}}
        saldos = [{"almacen": "TEX3", "libre": 7}, {"almacen": "TEX3", "libre": -3},
                  {"almacen": "ENSAYO", "libre": 9}, {"almacen": "TEX2", "libre": 100}]
        self.assertEqual(fb.libre_kubera(saldos, alm), 7)


class Coincide(unittest.TestCase):
    def test_casos(self):
        self.assertEqual(fb.coincide(8, 8, "calculado")[:2], ("igual", 0))
        self.assertEqual(fb.coincide(4, 6, "calculado")[:2], ("mas", 2))
        self.assertEqual(fb.coincide(6, 4, "calculado")[:2], ("menos", -2))

    def test_sin_esperado_o_sin_numero_no_toca(self):
        self.assertEqual(fb.coincide(None, 4, "delta")[0], "no_toca")
        self.assertEqual(fb.coincide(0, None, "calculado")[0], "no_toca")
        k, dif, texto = fb.coincide(5, None, "calculado")
        self.assertEqual((k, dif), ("no_toca", None))
        self.assertIn("prendería", texto)


class Puerta(unittest.TestCase):
    def test_sin_formato(self):
        self.assertEqual(fb.estado_puerta(None)["estado"], "sin_formato")

    def test_por_confirmar_todavia_no_espera(self):
        self.assertEqual(fb.estado_puerta({"por_confirmar": 2, "esperando": 0, "abiertas": 0})["estado"],
                         "por_confirmar")

    def test_esperando_gana_a_abierta(self):
        p = fb.estado_puerta({"por_confirmar": 0, "esperando": 1, "piezas_esperando": 30, "abiertas": 3,
                              "via": "tex2_bajo", "folios_esperando": "FMT-00002"})
        self.assertEqual((p["estado"], p["piezas"], p["folios"]), ("esperando", 30, "FMT-00002"))

    def test_abierta_con_su_via(self):
        p = fb.estado_puerta({"por_confirmar": 0, "esperando": 0, "abiertas": 1, "via": "tex2_cero",
                              "abierta": "2026-10-06 10:00:00"})
        self.assertEqual(p["estado"], "abierta")
        self.assertIn("TEX2 en cero", p["texto"])


class Banderas(unittest.TestCase):
    def test_sin_fila_manda_la_variable_y_vale_false(self):
        b = fb.banderas([], variable=lambda _n: False)
        self.assertEqual([x["flag"] for x in b],
                         ["ordenes_venta", "stock_watch_lee_kubera", "ov_generacion_auto", "inventario_libro"])
        self.assertTrue(all(not x["encendida"] and x["fuente"] == "variable" for x in b))

    def test_fila_manda(self):
        b = fb.banderas([{"flag": "stock_watch_lee_kubera", "valor": True, "motivo": "acta"}],
                        variable=lambda _n: False)
        kub = next(x for x in b if x["flag"] == "stock_watch_lee_kubera")
        self.assertEqual((kub["encendida"], kub["fuente"], kub["motivo"]), (True, "fila", "acta"))

    def test_lectura_fallida_apaga_todo(self):
        b = fb.banderas(None, variable=lambda _n: True)
        self.assertTrue(all(not x["encendida"] and x["fuente"] == "error" for x in b))

    def test_sin_variable_de_respaldo_es_apagada(self):
        b = fb.banderas([], variable=lambda _n: True)
        self.assertTrue(next(x for x in b if x["flag"] == "ordenes_venta")["encendida"])
        self.assertFalse(next(x for x in b if x["flag"] == "inventario_libro")["encendida"])


class OdooPorSku(unittest.TestCase):
    def test_duplicado_gana_el_id_mas_alto_como_stock_watch(self):
        prods = [{"id": 5, "default_code": "X-1", "active": True, "name": "viejo"},
                 {"id": 9, "default_code": "x-1 ", "active": True, "name": "nuevo"}]
        g = {"TEX2": [{"product_id": [5, ""], "quantity": 3, "reserved_quantity": 0},
                      {"product_id": [9, ""], "quantity": 7, "reserved_quantity": 2}]}
        o = fb.odoo_por_sku(prods, g)["X-1"]
        self.assertTrue(o["duplicado"])
        self.assertEqual(o["nombre"], "nuevo")
        self.assertEqual(o["bodegas"]["TEX2"], {"fisico": 7, "reservado": 2, "libre": 5})

    def test_archivado(self):
        prods = [{"id": 2, "default_code": "Y-1", "active": False, "name": "arch"},
                 {"id": 3, "default_code": "Z-1", "active": True, "name": "z"},
                 {"id": 4, "default_code": "Z-1", "active": False, "name": "z viejo"}]
        g = {"TEX2": [{"product_id": [2, ""], "quantity": 10, "reserved_quantity": 0},
                      {"product_id": [4, ""], "quantity": 6, "reserved_quantity": 0}]}
        o = fb.odoo_por_sku(prods, g)
        self.assertFalse(o["Y-1"]["activo"])
        self.assertTrue(o["Y-1"]["en_tex2"])
        self.assertTrue(o["Z-1"]["activo"])
        self.assertEqual(o["Z-1"]["piezas_archivadas"], 6)
        self.assertTrue(o["Z-1"]["en_tex2"])        # el archivado está en TEX2: entra al universo


class Universo(unittest.TestCase):
    def test_tex2_kubera_formatos_y_buscado(self):
        od = {"A-1": {"en_tex2": True}, "B-1": {"en_tex2": False}}
        saldos = [{"sku": "c-1", "almacen": "TEX3", "fisico": 3, "apartado": 0},
                  {"sku": "ZZCONC-1", "almacen": "ENSAYO", "fisico": 0, "apartado": 0}]
        u = fb.universo(od, saldos, {"D-1": {}}, buscado="e-1")
        self.assertEqual(u, ["A-1", "C-1", "D-1", "E-1"])

    def test_sin_odoo(self):
        self.assertEqual(fb.universo(None, [], {"D-1": {}}), ["D-1"])

    def test_fila_en_cero_de_tex3_si_entra(self):
        # Entró por devolución, se vendió hasta 0 y no hay nada en TEX2: sigue siendo de kubera.
        saldos = [{"sku": "T-0", "almacen": "TEX3", "fisico": 0, "apartado": 0},
                  {"sku": "E-0", "almacen": "ENSAYO", "fisico": 0, "apartado": 0}]
        self.assertEqual(fb.universo({}, saldos, {}), ["T-0"])


# ── 2. La pestaña completa, con mocks ────────────────────────────────────────

class EstadoVacio(ConMocks):
    def test_tex3_apagada_sin_banderas_ni_formatos(self):
        d = fb.resumen()
        self.assertTrue(d["ok"])
        self.assertTrue(d["tablas"]["ok"])
        self.assertTrue(d["odoo"]["ok"])
        self.assertFalse(d["universo_parcial"])
        # El universo es TEX2: SKU-A y SKU-B (SKU-T3 sólo está en TEXCO).
        self.assertEqual(sorted(f["sku"] for f in d["filas"]), ["SKU-A", "SKU-B"])
        # Los peores primero: SKU-B ofrece 2 de más.
        self.assertEqual(d["filas"][0]["sku"], "SKU-B")
        b = self.fila(d, "SKU-B")
        self.assertEqual((b["esperado"], b["woo"], b["coincide"], b["dif"]), (4, 6, "mas", 2))
        a = self.fila(d, "SKU-A")
        self.assertEqual((a["esperado"], a["pend"], a["coincide"]), (8, 2, "igual"))
        self.assertEqual(a["odoo"]["TEX2"], {"fisico": 6, "reservado": 0, "libre": 6})
        self.assertEqual(a["odoo"]["TEXCO"]["libre"], 4)
        self.assertEqual(a["odoo"]["DROP"], {"fisico": 0, "reservado": 0, "libre": 0})
        self.assertEqual(a["otras"], 0)                     # 10 = 4 + 6 + 0
        self.assertEqual(a["puerta"]["estado"], "sin_formato")
        self.assertEqual(d["conteo"]["no_coincide"], 1)
        # Encabezado: TEX3 sola como columna, banderas apagadas por falta de fila, qué falta.
        self.assertEqual([c["codigo"] for c in d["columnas_kubera"]], ["TEX3"])
        self.assertTrue(all(not x["encendida"] and x["fuente"] == "variable" for x in d["banderas"]))
        self.assertEqual(len(d["que_falta"]), 4)
        self.assertFalse(any(p["hecho"] for p in d["que_falta"]))
        self.assertIn("kubera NO suma", d["formula"]["texto"])
        self.assertEqual(d["stock_watch"]["ultima"], "2026-10-06 11:50:00")
        # Odoo: sólo lectura y seis llamadas (almacenes, TEX2, productos de TEX2, sus
        # hermanos por código, TEXCO, DROP).
        self.assertEqual({m for _mod, m in self.odoo.llamadas}, {"search_read", "read_group"})
        self.assertEqual(len(self.odoo.llamadas), 6)
        self.assertEqual(d["conteo"]["por_copiar"], 0)
        self.assertTrue(d["odoo"]["tras_pasada"])
        self.assertEqual((a["odoo_hoy"], a["odoo_base"], a["kubera_base"]), (10, 10, None))

    def test_cache_de_odoo_no_relee(self):
        fb.resumen()
        fb._cache.update(t=0.0, v=None)            # vence la caché de 20 s, no la de Odoo
        fb.resumen()
        self.assertEqual(len(self.odoo.llamadas), 6)

    def test_pasada_mas_nueva_que_la_cache_relee(self):
        fb.resumen()
        fb._cache.update(t=0.0, v=None)
        fb._odoo_cache["t"] -= 30                   # la lectura de Odoo es de hace 30 s…
        self.kub.d["pasada"] = {**self.kub.d["pasada"], "edad_s": 5}    # …y la pasada, de hace 5
        d = fb.resumen()
        self.assertEqual(len(self.odoo.llamadas), 11)                   # relee (almacenes en caché)
        self.assertTrue(d["odoo"]["tras_pasada"])

    def test_formatos_y_vigia_con_cache_de_cinco_minutos(self):
        self.kub.d["puertas"] = [{"sku": "SKU-T3", "por_confirmar": 0, "esperando": 2, "piezas_esperando": 5,
                                  "abiertas": 1, "via": None, "abierta": None, "folios_esperando": "FMT-1"}]
        d = fb.resumen()
        fb._cache.update(t=0.0, v=None)
        fb.resumen()
        self.assertEqual(self.kub.corridas.count("formatos"), 1)
        self.assertEqual(self.kub.corridas.count("vigia"), 1)
        self.assertEqual(self.kub.corridas.count("puertas"), 2)
        # Lo que se ve por fila va fresco: sale de `puertas`, no del conteo con caché.
        self.assertEqual((d["formatos"]["esperando"], d["formatos"]["abiertas"]), (2, 1))
        self.assertTrue(d["que_falta"][0]["hecho"] and d["que_falta"][1]["hecho"])


class KuberaConSaldo(ConMocks):
    def setUp(self) -> None:
        super().setUp()
        self.kub.d["saldos"] = [{"sku": "SKU-T3", "almacen": "TEX3", "fisico": 5, "apartado": 1, "libre": 4,
                                 "ubicacion": "A-1"},
                                {"sku": "SKU-A", "almacen": "TEX3", "fisico": 4, "apartado": 0, "libre": 4,
                                 "ubicacion": None}]
        self.kub.d["puertas"] = [{"sku": "SKU-T3", "por_confirmar": 0, "esperando": 1, "piezas_esperando": 5,
                                  "abiertas": 0, "via": None, "abierta": None, "folios_esperando": "FMT-00001"}]

    def test_apagada_kubera_no_suma_y_universo(self):
        d = fb.resumen()
        t3 = self.fila(d, "SKU-T3")
        self.assertIn("kubera", t3["tags"])
        self.assertIn("esperando", t3["tags"])
        self.assertEqual(t3["kubera"]["TEX3"], {"fisico": 5, "apartado": 1, "libre": 4})
        self.assertEqual(t3["libre_kubera"], 0)              # TEX3 no cuenta para Woo
        self.assertEqual(t3["esperado"], 2)                  # sólo Odoo
        self.assertEqual(self.fila(d, "SKU-A")["esperado"], 8)

    def test_encendida_pero_stock_watch_no_la_lee(self):
        self.kub.d["banderas"] = [{"flag": "stock_watch_lee_kubera", "valor": True, "motivo": "acta",
                                   "actualizado_por": "x", "actualizado": None}]
        d = fb.resumen()
        self.assertIn("todavía no la lee", d["formula"]["texto"])
        self.assertFalse(d["formula"]["suma_kubera"])
        self.assertEqual(self.fila(d, "SKU-A")["esperado"], 8)
        self.assertFalse(d["que_falta"][3]["hecho"])

    def test_encendida_y_tex3_cuenta(self):
        tex3 = {**ALM["TEX3"], "cuenta_para_woo": True, "surte_ventas": True, "admite_ov": True}
        self.kub.d["almacenes"] = [a if a["codigo"] != "TEX3" else tex3 for a in ALMACENES]
        self.kub.d["banderas"] = [{"flag": "stock_watch_lee_kubera", "valor": True, "motivo": "acta",
                                   "actualizado_por": "x", "actualizado": None}]
        with mock.patch.object(fb, "_suma_kubera", return_value=True):
            d = fb.resumen()
        a = self.fila(d, "SKU-A")
        self.assertEqual((a["libre_kubera"], a["esperado"]), (4, 12))     # 10 + 4 − 2
        self.assertEqual(a["coincide"], "menos")                          # Woo 8 < 12
        self.assertIn("kubera SÍ suma", d["formula"]["texto"])
        self.assertTrue(d["que_falta"][2]["hecho"] and d["que_falta"][3]["hecho"])


class OdooCaido(ConMocks):
    def test_sin_lectura_previa_no_truena(self):
        self.odoo.falla = OSError("timed out")
        self.kub.d["saldos"] = [{"sku": "SKU-T3", "almacen": "TEX3", "fisico": 5, "apartado": 0, "libre": 5,
                                 "ubicacion": None}]
        with self.assertLogs("omnicanal.fanout_bodegas", level="WARNING"):
            d = fb.resumen()
        self.assertTrue(d["ok"])
        self.assertFalse(d["odoo"]["ok"])
        self.assertIn("Odoo no contestó", d["odoo"]["motivo"])
        self.assertTrue(d["universo_parcial"])
        t3 = self.fila(d, "SKU-T3")
        self.assertIsNone(t3["odoo"])                 # «—», no cero
        self.assertEqual(t3["esperado"], 2)           # sale de la foto, no de Odoo en vivo
        self.assertEqual(t3["woo"], 2)

    def test_no_reintenta_antes_de_un_minuto(self):
        self.odoo.falla = OSError("timed out")
        with self.assertLogs("omnicanal.fanout_bodegas", level="WARNING"):
            fb.resumen()
        n = len(self.odoo.llamadas)
        fb._cache.update(t=0.0, v=None)
        fb.resumen()
        self.assertEqual(len(self.odoo.llamadas), n)

    def test_con_lectura_previa_sirve_la_vieja(self):
        fb.resumen()
        fb._cache.update(t=0.0, v=None)
        fb._odoo_cache["t"] -= fb.ODOO_TTL + 1         # vence la de Odoo
        self.odoo.falla = OSError("timed out")
        with self.assertLogs("omnicanal.fanout_bodegas", level="WARNING"):
            d = fb.resumen()
        self.assertTrue(d["odoo"]["ok"])
        self.assertTrue(d["odoo"]["viejo"])
        self.assertGreater(d["odoo"]["edad_s"], fb.ODOO_TTL)
        self.assertEqual(self.fila(d, "SKU-A")["odoo"]["TEX2"]["libre"], 6)


class TablasAusentes(ConMocks):
    def test_200_con_aviso_y_sin_tocar_las_tablas(self):
        sin = {"ok": False, "faltan": ["ops.almacenes", "ops.stock_almacen"], "vigia": False,
               "stock_kubera": False}
        with mock.patch.object(fb, "_tablas", return_value=sin):
            d = fb.resumen()
        self.assertTrue(d["ok"])
        self.assertFalse(d["tablas"]["ok"])
        prohibidas = {"almacenes", "saldos", "puertas", "formatos", "vigia"}
        self.assertFalse(prohibidas & set(self.kub.corridas), self.kub.corridas)
        self.assertTrue(any("null::integer" in s for s in self.kub.sqls if "bodegas:foto" in s))
        self.assertEqual(sorted(f["sku"] for f in d["filas"]), ["SKU-A", "SKU-B"])
        self.assertEqual(d["que_falta"], [])
        self.assertEqual(d["columnas_kubera"], [])

    def test_detalle_sin_tablas(self):
        sin = {"ok": False, "faltan": ["ops.stock_mov"], "vigia": False, "stock_kubera": False}
        with mock.patch.object(fb, "_tablas", return_value=sin):
            d = fb.detalle_sku("SKU-A")
        self.assertTrue(d["ok"])
        self.assertEqual(d["libro"], [])
        self.assertNotIn("libro", self.kub.corridas)


class KuberaCaida(ConMocks):
    def test_dice_que_no_contesto(self):
        with mock.patch.object(fb, "_tablas", side_effect=RuntimeError("pool agotado")),                 self.assertLogs("omnicanal.fanout_bodegas", level="ERROR"):
            d = fb.resumen()
        self.assertFalse(d["ok"])
        self.assertIn("kubera no contestó", d["motivo"])


class Pendientes(ConMocks):
    def test_sin_medir_no_hay_esperado(self):
        from services import stock_watch
        with mock.patch.object(stock_watch, "_pendientes", side_effect=RuntimeError("centinela")):
            d = fb.resumen()
        self.assertTrue(d["stock_watch"]["pendientes"]["ciega"])
        a = self.fila(d, "SKU-A")
        self.assertEqual((a["esperado"], a["esperado_motivo"], a["coincide"]), (None, "ciega", "no_toca"))


class Detalle(ConMocks):
    def test_libro_formatos_y_ov(self):
        self.kub.d["saldos_sku"] = [{"sku": "SKU-T3", "almacen": "TEX3", "fisico": 5, "apartado": 0, "libre": 5,
                                     "ubicacion": None, "actualizado": None}]
        self.kub.d["libro"] = [{"id": 2, "almacen": "TEX3", "delta": 5, "saldo_despues": 5, "motivo": "entrada",
                                "ref": "FMT-00001", "nota": None, "quien": "Bodega", "via": "panel",
                                "hora": "2026-10-06 10:00:00", "total": 1}]
        self.kub.d["renglones_sku"] = [{"folio": "FMT-00001", "estado": "confirmado", "almacen": "TEX3", "fila": 1,
                                        "cantidad": 5, "cantidad_archivo": 5, "sku_archivo": "SKU-T3",
                                        "ubicacion": None, "nota": None, "aviso": None, "via": "tex2_bajo",
                                        "ref_odoo": None, "odoo_tex2_al_cargar": 7, "odoo_tex2_al_confirmar": None,
                                        "odoo_tex2_al_abrir": 2, "abierta": "2026-10-06 10:00:00",
                                        "cargado": None, "confirmado": None}]
        self.kub.d["puertas"] = [{"sku": "SKU-T3", "por_confirmar": 0, "esperando": 0, "piezas_esperando": 0,
                                  "abiertas": 1, "via": "tex2_bajo", "abierta": "2026-10-06 10:00:00",
                                  "folios_esperando": None}]
        d = fb.detalle_sku("sku-t3")
        self.assertTrue(d["ok"] and d["existe"])
        self.assertEqual(d["sku"], "SKU-T3")
        self.assertEqual(d["libro_total"], 1)
        self.assertNotIn("total", d["libro"][0])
        self.assertEqual(d["renglones"][0]["via_t"], "TEX2 bajó")
        self.assertEqual(d["fila"]["puerta"]["estado"], "abierta")
        self.assertEqual(d["fila"]["odoo"]["TEXCO"]["libre"], 2)      # lectura de un solo SKU

    def test_sku_inexistente(self):
        d = fb.detalle_sku("NO-EXISTE")
        self.assertTrue(d["ok"])
        self.assertFalse(d["existe"])


# ── 3. Coincide no se compara consigo mismo ──────────────────────────────────

class PorCopiar(ConMocks):
    def test_escritura_fallida_no_dice_coincide(self):
        # stock_watch calculó otro número para SKU-A, el batch de Woo falló y la foto
        # conservó lo viejo: Odoo 10 (retenido) y Woo 8. Contra sí misma «coincide»;
        # contra Odoo HOY (TEX2 bajó de 6 a 1 → free 5) no.
        self.odoo.quants[12][1] = (1, 0)
        d = fb.resumen()
        a = self.fila(d, "SKU-A")
        self.assertEqual(a["coincide"], "por_copiar")
        self.assertIsNone(a["dif"])
        self.assertEqual(a["esperado"], 3)                  # max(0, 5 − 2): lo que copia la próxima
        self.assertEqual((a["odoo_total"], a["odoo_hoy"], a["odoo_base"]), (10, 5, 5))
        self.assertIn("la próxima pasada copia 3", a["coincide_t"])
        self.assertIn("escritura fallida", a["coincide_t"])
        self.assertIn("por_copiar", a["tags"])
        self.assertNotIn("no_coincide", a["tags"])
        self.assertEqual((d["conteo"]["por_copiar"], d["conteo"]["no_coincide"]), (1, 1))
        self.assertEqual(a["otras"], 0)                     # misma lectura: 5 = 4 + 1 + 0

    def test_odoo_cambio_pero_la_proxima_copia_ya_es_woo(self):
        # Odoo cambió desde la foto (10 → 5), pero lo que copiaría la próxima pasada
        # (max(0, 5 − 2) = 3) ya es lo que tiene Woo: no hay nada por copiar.
        self.odoo.quants[12][1] = (1, 0)
        self.kub.d["foto"]["SKU-A"] = {**self.kub.d["foto"]["SKU-A"], "stock_woo": 3}
        d = fb.resumen()
        a = self.fila(d, "SKU-A")
        self.assertEqual((a["coincide"], a["dif"], a["esperado"]), ("igual", 0, 3))
        self.assertIn("ya es lo que tiene Woo", a["coincide_t"])
        self.assertNotIn("por_copiar", a["tags"])
        self.assertEqual(d["conteo"]["por_copiar"], 0)

    def test_lectura_de_odoo_anterior_a_la_pasada_no_compara(self):
        fb.resumen()                                        # caché buena (free de A = 10)
        fb._cache.update(t=0.0, v=None)
        self.odoo.quants[12][1] = (1, 0)                    # Odoo cambia, pero no se relee:
        fb._odoo_cache["t"] -= 5
        fb._odoo_cache.update(fallo="Odoo no contestó (x)", fallo_t=time.monotonic())
        self.kub.d["pasada"] = {**self.kub.d["pasada"], "edad_s": 0}
        self.kub.d["foto"]["SKU-A"] = {**self.kub.d["foto"]["SKU-A"], "stock_odoo": 5, "stock_woo": 3}
        d = fb.resumen()
        # La pasada (que ya vio Odoo 5) es más nueva que la caché (10), y Odoo está en
        # su minuto de freno: se sirve la vieja y NO se compara contra la foto.
        self.assertTrue(d["odoo"]["viejo"])
        self.assertFalse(d["odoo"]["tras_pasada"])
        self.assertEqual(self.fila(d, "SKU-A")["coincide"], "igual")

    def test_venta_posterior_a_la_pasada_no_es_de_mas(self):
        # SKU-B: foto Woo 6 / Odoo 4. Una venta sin orden después de la pasada no
        # mueve la foto: la pestaña no la marca «de más», la deja por copiar.
        self.kub.d["recientes"] = [{"sku": "SKU-B"}]
        d = fb.resumen()
        b = self.fila(d, "SKU-B")
        self.assertEqual(b["coincide"], "por_copiar")
        self.assertIn("Venta u orden nueva", b["coincide_t"])
        self.assertEqual(d["conteo"]["no_coincide"], 0)
        self.assertEqual(d["stock_watch"]["pendientes"]["recientes"], 1)

    def test_recientes_sin_resta_no_se_consulta(self):
        with mock.patch.object(fb, "_config_stock_watch", side_effect=lambda: {**CFG, "resta": False}):
            fb.resumen()
        self.assertNotIn("recientes", self.kub.corridas)


class PendientesExactos(ConMocks):
    def test_variante_en_minusculas_no_se_resta(self):
        # stock_watch hace pend.get("SKU-A"): la venta registrada como «sku-a» no resta.
        from services import stock_watch
        with mock.patch.object(stock_watch, "_pendientes",
                               return_value={"pend": {"sku-a": 2}, "ventas": 1, "mas_vieja_h": 1.0}):
            d = fb.resumen()
        a = self.fila(d, "SKU-A")
        self.assertEqual((a["pend"], a["esperado"], a["coincide"], a["dif"]), (0, 10, "menos", -2))


class Hermanos(ConMocks):
    def test_archivado_en_tex2_trae_al_activo_del_mismo_codigo(self):
        # X: el archivado (id 100) conserva 3 pzs en TEX2; el activo (id 200) tiene
        # 40 en TEXCO y nada en TEX2. stock_watch copia el activo.
        self.odoo.productos += [{"id": 100, "default_code": "SKU-X", "name": "X viejo", "active": False},
                                {"id": 200, "default_code": "SKU-X", "name": "X", "active": True}]
        self.odoo.quants[12][100] = (3, 0)
        self.odoo.quants[11][200] = (40, 0)
        self.kub.d["foto"]["SKU-X"] = {"sku": "SKU-X", "stock_woo": 40, "stock_odoo": 40, "stock_kubera": None,
                                       "hora": "2026-10-06 11:50:00"}
        d = fb.resumen()
        x = self.fila(d, "SKU-X")
        self.assertEqual(x["nombre"], "X")
        self.assertEqual(x["odoo"]["TEXCO"]["libre"], 40)
        self.assertEqual(x["odoo"]["TEX2"]["libre"], 0)
        self.assertNotIn("Archivado en Odoo: stock_watch no lo cuenta", x["avisos"])
        self.assertTrue(any("archivado con 3 pzs" in a for a in x["avisos"]))
        self.assertEqual(x["coincide"], "igual")
        self.assertEqual(d["odoo"]["archivados_tex2"], 0)
        # La segunda búsqueda es por código y excluye lo ya leído.
        por_codigo = self.odoo.dominios[1]
        self.assertIn(["id", "not in", [1, 2, 100]], por_codigo)
        self.assertIn("SKU-X", next(t[2] for t in por_codigo if t[0] == "default_code"))


class KuberaFoto(ConMocks):
    def setUp(self) -> None:
        super().setUp()
        tex3 = {**ALM["TEX3"], "cuenta_para_woo": True, "surte_ventas": True, "admite_ov": True}
        self.kub.d["almacenes"] = [a if a["codigo"] != "TEX3" else tex3 for a in ALMACENES]
        self.kub.d["foto"]["SKU-A"] = {**self.kub.d["foto"]["SKU-A"], "stock_woo": 12, "stock_kubera": 4}

    def _saldo(self, libre: int) -> None:
        self.kub.d["saldos"] = [{"sku": "SKU-A", "almacen": "TEX3", "fisico": libre, "apartado": 0,
                                 "libre": libre, "ubicacion": None}]

    def test_usa_la_mitad_kubera_de_la_foto(self):
        self._saldo(4)
        with mock.patch.object(fb, "_suma_kubera", return_value=True):
            d = fb.resumen()
        a = self.fila(d, "SKU-A")
        self.assertEqual((a["esperado"], a["coincide"], a["kubera_base"]), (12, "igual", 4))   # 10 + 4 − 2

    def test_kubera_cambio_desde_la_pasada(self):
        self._saldo(2)                          # una OV apartó 2 después de la pasada
        with mock.patch.object(fb, "_suma_kubera", return_value=True):
            d = fb.resumen()
        a = self.fila(d, "SKU-A")
        self.assertEqual((a["coincide"], a["esperado"], a["kubera_base"]), ("por_copiar", 10, 2))
        self.assertIn("kubera cambió", a["coincide_t"])


class Cajon(ConMocks):
    def test_odoo_caido_no_vuelve_a_odoo_desde_el_cajon(self):
        self.odoo.falla = OSError("timed out")
        with self.assertLogs("omnicanal.fanout_bodegas", level="WARNING"):
            fb.resumen()
        n = len(self.odoo.llamadas)
        d = fb.detalle_sku("SKU-T3")
        self.assertEqual(len(self.odoo.llamadas), n)
        self.assertFalse(d["odoo"]["ok"])
        self.assertIsNone(d["fila"]["odoo"])

    def test_falla_del_cajon_queda_anotada(self):
        self.odoo.falla = OSError("timed out")
        with self.assertLogs("omnicanal.fanout_bodegas", level="WARNING"):
            fb.detalle_sku("SKU-T3")
        n = len(self.odoo.llamadas)
        fb.detalle_sku("SKU-T3")
        self.assertEqual(len(self.odoo.llamadas), n)
        self.assertIn("Odoo no contestó", fb._odoo_cache["fallo"])

    def test_sku_de_la_tabla_sale_de_la_cache(self):
        fb.resumen()
        n = len(self.odoo.llamadas)
        d = fb.detalle_sku("sku-a")
        self.assertEqual(len(self.odoo.llamadas), n)
        self.assertEqual(d["fila"]["odoo"]["TEX2"]["libre"], 6)

    def test_sku_suelto_sin_distinguir_mayusculas_y_con_cache(self):
        self.odoo.productos.append({"id": 7, "default_code": "abc_1", "name": "minúsculas", "active": True})
        self.odoo.productos.append({"id": 8, "default_code": "ABCX1", "name": "otro", "active": True})
        self.odoo.quants[11][7] = (3, 0)
        d = fb.detalle_sku("ABC_1")
        self.assertTrue(d["existe"])
        self.assertEqual(d["fila"]["odoo"]["TEXCO"]["libre"], 3)
        self.assertEqual(d["fila"]["nombre"], "minúsculas")     # `_` es literal: no trae ABCX1
        self.assertIn([["default_code", "=ilike", "ABC\\_1"]], self.odoo.dominios)
        n = len(self.odoo.llamadas)
        fb.detalle_sku("ABC_1")
        self.assertEqual(len(self.odoo.llamadas), n)


class EquivaleAStockWatch(unittest.TestCase):
    """`woo_esperado` + `coincide` contra la función REAL de stock_watch: «igual»
    exactamente cuando `_deltas_odoo` no escribiría nada, y si escribe, el
    esperado es su destino. Si alguien cambia `_deltas_odoo`, esto se pone rojo."""

    def test_rejilla(self):
        from services import stock_watch
        for od in (0, 3, 10):
            for pend in (0, 2, 12):
                for woo in (0, 1, 8, 10):
                    esp, mot, _ = fb.woo_esperado(od, pend, absoluto=True, resta=True)
                    k = fb.coincide(esp, woo, mot)[0]
                    d = stock_watch._deltas_odoo({"S": od}, {"S": {"stock": woo}}, {}, True, {"S": pend})
                    with self.subTest(od=od, pend=pend, woo=woo):
                        self.assertEqual(k == "igual", not d)
                        if d:
                            self.assertEqual(d[0][1], esp)

    def test_sin_resta_copia_tal_cual(self):
        from services import stock_watch
        esp, _mot, _ = fb.woo_esperado(7, 3, absoluto=True, resta=False)
        d = stock_watch._deltas_odoo({"S": 7}, {"S": {"stock": 4}}, {}, True, {})
        self.assertEqual(d[0][1], esp)


class SqlSoloLectura(unittest.TestCase):
    PROHIBIDO = re.compile(r"\b(insert|update|delete|merge|truncate|create|alter|drop|grant|revoke|copy|"
                           r"call|do|lock)\b|for\s+(no\s+key\s+)?(update|share)|\bset\s")

    def test_toda_sentencia_es_un_select(self):
        nombres = [n for n in dir(fb) if n.startswith("_SQL_")]
        self.assertGreaterEqual(len(nombres), 14)
        for n in nombres:
            sql = re.sub(r"/\*.*?\*/", " ", getattr(fb, n), flags=re.S)
            sql = re.sub(r"--[^\n]*", " ", sql).strip().lower()
            with self.subTest(sentencia=n):
                self.assertTrue(sql.startswith("select"), sql[:60])
                self.assertIsNone(self.PROHIBIDO.search(sql), sql)

    def test_odoo_tiene_una_sola_puerta(self):
        fuente = Path(fb.__file__).read_text(encoding="utf-8")
        self.assertEqual(fuente.count("_kw_flujo("), 1)
        self.assertNotIn("execute_kw", fuente)
        self.assertNotIn("_models(", fuente)


class SoloLectura(unittest.TestCase):
    def test_lista_blanca(self):
        with mock.patch.object(odoo, "_kw_flujo") as kw:
            for metodo in ("write", "create", "unlink", "action_confirm", "button_validate"):
                with self.assertRaises(PermissionError):
                    fb._kw_solo_lectura("stock.quant", metodo, [[]])
            kw.assert_not_called()


class _Peticion:
    def __init__(self, encoding: str = "") -> None:
        self.headers = {"accept-encoding": encoding}


class Ruta(unittest.TestCase):
    def setUp(self) -> None:
        _limpiar()

    def tearDown(self) -> None:
        _limpiar()

    def test_la_ruta_devuelve_bytes_codificados_en_el_hilo(self):
        from routers import fanout
        v = {"ok": True, "x": "año"}
        with mock.patch.object(fb, "_resumen", return_value=v):
            r = asyncio.run(fanout.bodegas(_Peticion("gzip, deflate, br")))
            self.assertEqual(r.headers["content-encoding"], "gzip")
            self.assertEqual(json.loads(gzip.decompress(r.body)), v)
            r = asyncio.run(fanout.bodegas(_Peticion()))
            self.assertNotIn("content-encoding", r.headers)
            self.assertEqual(json.loads(r.body), v)
        self.assertIs(fb._cache["v"], v)
        self.assertIsNotNone(fb._cache["b"])            # los bytes quedan en la caché
        with mock.patch.object(fb, "detalle_sku", side_effect=lambda s: {"ok": True, "sku": s}):
            r = asyncio.run(fanout.bodegas_sku("ABC-1"))
            self.assertEqual(json.loads(r.body), {"ok": True, "sku": "ABC-1"})

    def test_error_de_kubera_no_se_guarda_en_bytes(self):
        with mock.patch.object(fb, "_resumen", side_effect=RuntimeError("x")),                 self.assertLogs("omnicanal.fanout_bodegas", level="ERROR"):
            b, z = fb.resumen_bytes(False)
        self.assertFalse(json.loads(b)["ok"])
        self.assertIsNone(fb._cache["b"])


if __name__ == "__main__":
    unittest.main()
