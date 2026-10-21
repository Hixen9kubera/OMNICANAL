"""Pruebas de «SIN NÚMERO» en el fan-out (`fanout_stock.plan` y `_persistir`).

── EL DEFECTO ───────────────────────────────────────────
Cuando el SKU no tiene número (Woo no gestiona su `_stock`), el plan sale sin
acciones y la bitácora lo anotaba como `sin_destinos`,
la misma fila que «no hay publicaciones a dónde escribir». El recuperador cuenta
`sin_destinos` como «ya repartido»: el cambio desaparecía sin que nadie lo viera, con
las publicaciones vivas ofreciendo el número viejo.

── CON EL LIBRO NO HAY «SIN NÚMERO» (D4) ───────────────
Con `FANOUT_FUENTE=kubera` un SKU que el libro no trae vale 0 (se reparte el 0) y un
libro ilegible —o por debajo del piso de SKUs— LANZA `LibroNoDisponible` (lectura
fallida: `test_fanout_lectura_fallida`). `sin_numero` sólo sale leyendo Woo; el texto
del libro se queda por si `stock_libro` volviera a devolver None.

── QUÉ FIJAN ────────────────────────────────────────────
  0. Con el libro, un SKU desconocido sale con número 0 y sin marca; bajo el piso, lanza.
  1. `plan` marca `sin_numero` con su texto («sin stock legible en WooCommerce» leyendo
     Woo; «sin número en el libro» si el libro algún día no diera número); el `motivo`
     de WOO SALIDA no cambia y no pregunta destinos.
  2. La bitácora anota la acción propia `sin_numero` (canal NULL, el texto en
     `resultado`), no `sin_destinos`. Un plan CON número y sin publicaciones sigue
     siendo `sin_destinos`.
  3. `_aplicar` no le escribe nada a ningún canal.
  4. Los lectores: la matriz (`fanout_vivo`) lo sigue tomando como evento del fan-out;
     el recuperador NO lo da por repartido (lo cuenta aparte: `test_fanout_recuperar`).

── NO SE TOCA NADA REAL ─────────────────────────────────
La base, Woo y la red revientan si se tocan (las trampas de `test_fanout_seguro`).
Los SKUs son INVENTADOS: el repo es público.

    cd backend && python -m unittest tests.test_fanout_sin_numero -v
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import fanout_read  # noqa: E402
from services import fanout_recuperar as R  # noqa: E402
from services import fanout_stock as F  # noqa: E402
from services import fanout_vivo as V  # noqa: E402
from tests import test_fanout_seguro as _TS  # noqa: E402

setUpModule = _TS.setUpModule
tearDownModule = _TS.tearDownModule

SKU = "PRB-0301-AZL"


def _no_destinos(*_a, **_k):
    raise AssertionError("sin número no pregunta destinos")


class ConElLibro(unittest.TestCase):
    """D4 de punta a punta: `plan` → `_stock_drop` → `stock_libro` con la base falsa."""

    BODEGAS = [{"codigo": "BOD-PRB"}]

    def _plan(self, con_piezas: int) -> dict:
        from services import stock_libro
        from services import supabase_db as sdb

        def uno(sql, params=None):
            if "to_regclass" in sql:
                return {"r": "almacen.existencias_bodega_v"}
            if "con_piezas" in sql:
                return {"con_piezas": con_piezas}
            raise AssertionError(f"consulta inesperada: {sql[:60]}")

        def todos(sql, params=None):
            return self.BODEGAS if sql == stock_libro._SQL_BODEGAS else []   # el SKU no está

        with mock.patch.multiple(F.settings, fanout_fuente="kubera", stock_libro_min_skus=4000,
                                 stock_libro_desde="2026-10-15T16:48:47+00:00",
                                 stock_libro_vista="almacen.existencias_bodega_v"), \
                mock.patch.dict(stock_libro._cobertura, {}, clear=True), \
                mock.patch.object(sdb, "fetch_one", side_effect=uno), \
                mock.patch.object(sdb, "fetch_all", side_effect=todos), \
                mock.patch.object(F, "_reserva", return_value=0), \
                mock.patch.object(F, "_destinos", return_value=[]):
            return F.plan(SKU)

    def test_un_sku_que_el_libro_no_trae_vale_0_y_no_es_sin_numero(self):
        p = self._plan(con_piezas=13_000)
        self.assertTrue(p["ok"])
        self.assertEqual(p["objetivo"], 0, "D4: lo que el libro no conoce sale en 0")
        self.assertNotIn("sin_numero", p)

    def test_bajo_el_piso_lanza_y_nunca_es_0(self):
        from services.stock_libro import LibroNoDisponible
        with self.assertRaises(LibroNoDisponible):
            self._plan(con_piezas=12)


class Plan(unittest.TestCase):
    def _plan(self, fuente: str) -> dict:
        with mock.patch.object(F.settings, "fanout_fuente", fuente), \
                mock.patch.object(F, "_stock_drop", return_value=None), \
                mock.patch.object(F, "_destinos", side_effect=_no_destinos):
            return F.plan(SKU)

    def test_si_el_libro_no_diera_numero(self):
        # Hoy no pasa (D4: `stock_libro` da 0 a lo que no trae); el texto queda por si cambia.
        p = self._plan("kubera")
        self.assertFalse(p["ok"])
        self.assertEqual(p["acciones"], [])
        self.assertEqual(p["sin_numero"], "sin número en el libro")
        self.assertEqual(p["sin_numero"], F.SIN_NUMERO_LIBRO)
        self.assertEqual(p["motivo"], "sin stock legible en el libro de kubera", "el motivo es de WOO SALIDA")

    def test_leyendo_woo(self):
        # El caso real: el producto no existe en Woo o no gestiona su `_stock`.
        p = self._plan("woo")
        self.assertEqual(p["sin_numero"], "sin stock legible en WooCommerce")
        self.assertEqual(p["motivo"], "sin stock legible en WooCommerce")

    def test_una_errata_en_la_fuente_tambien_es_el_libro(self):
        # `lee_libro` contesta «sí» con una errata (para que el lector se niegue).
        with mock.patch.object(F.settings, "fanout_fuente", "kubra"):
            from services import stock_libro
            self.assertTrue(stock_libro.lee_libro())

    def test_con_numero_no_hay_marca(self):
        with mock.patch.object(F, "_stock_drop", return_value=4), \
                mock.patch.object(F, "_destinos", return_value=[]):
            p = F.plan(SKU)
        self.assertTrue(p["ok"])
        self.assertNotIn("sin_numero", p)


class Bitacora(unittest.TestCase):
    """Las filas que `_persistir` manda al espejo (kubera) y a MySQL."""

    def _filas(self, evento: dict) -> tuple[list[dict], list[tuple]]:
        cursor = mock.MagicMock()
        ctx = mock.MagicMock()
        ctx.__enter__.return_value = cursor
        from services import db
        with mock.patch.object(fanout_read, "espejar") as espejar, \
                mock.patch.object(F, "_asegurar_schema"), \
                mock.patch.object(db, "get_cursor", return_value=ctx):
            F._persistir(evento)
        mysql = cursor.executemany.call_args.args[1] if cursor.executemany.called else []
        return [c.kwargs for c in espejar.call_args_list], mysql

    def _evento(self, **extra) -> dict:
        ahora = datetime.now(timezone.utc)
        return {"ts": ahora.isoformat(), "ts_dt": ahora.replace(tzinfo=None), "sku": SKU,
                "motivo": "cambio de vendible en kubera (PRB) 3 -> 2", "dry_run": False,
                "stock_drop": None, "objetivo": None, "ok": False, "acciones": [], "ms": 1.0, **extra}

    def test_sin_numero_tiene_su_accion_y_su_texto(self):
        espejo, mysql = self._filas(self._evento(motivo="woo_cambio (PRB) 3 -> 2",
                                                 detalle="sin stock legible en WooCommerce",
                                                 sin_numero=F.SIN_NUMERO_WOO))
        self.assertEqual(len(espejo), 1)
        f = espejo[0]
        self.assertEqual(f["accion"], "sin_numero")
        self.assertEqual(f["resultado"], "sin stock legible en WooCommerce",
                         "el recuperador sabe por este texto de qué fuente es la marca")
        self.assertIsNone(f["canal"], "no es de ningún canal: no se le escribió a nadie")
        self.assertEqual(f["motivo"], "woo_cambio (PRB) 3 -> 2")
        self.assertEqual(mysql[0][9], "sin_numero", "MySQL lleva la misma fila")
        self.assertLessEqual(len("sin_numero"), 20, "cabe en VARCHAR(20) de MySQL")

    def test_con_numero_y_sin_publicaciones_sigue_siendo_sin_destinos(self):
        espejo, _ = self._filas(self._evento(ok=True, stock_drop=5, objetivo=5, detalle=None))
        self.assertEqual(espejo[0]["accion"], "sin_destinos")
        self.assertEqual(espejo[0]["resultado"], "sin publicaciones vivas")


class Aplicar(unittest.TestCase):
    def test_no_escribe_a_ningun_canal_y_anota_sin_numero(self):
        escritores = {c: mock.MagicMock(side_effect=AssertionError(f"escribió a {c}")) for c in F._ESCRITORES}
        eventos: list[dict] = []
        with mock.patch.object(F.settings, "fanout_fuente", "woo"), \
                mock.patch.object(F, "_stock_drop", return_value=None), \
                mock.patch.object(F, "_destinos", side_effect=_no_destinos), \
                mock.patch.dict(F._ESCRITORES, escritores), \
                mock.patch.object(F, "seguro_encendido", return_value=False), \
                mock.patch.object(F, "_rancios", return_value={}), \
                mock.patch.object(F, "_persistir", side_effect=eventos.append), \
                mock.patch.dict(F._contadores, {k: 0 for k in F._contadores}):
            F._aplicar(SKU, "woo_cambio (PRB) 3 -> 2")
        self.assertEqual(len(eventos), 1)
        ev = eventos[0]
        self.assertEqual(ev["sin_numero"], "sin stock legible en WooCommerce")
        self.assertEqual(ev["acciones"], [])
        for e in escritores.values():
            e.assert_not_called()


class Lectores(unittest.TestCase):
    def test_la_matriz_lo_sigue_tomando_como_evento_del_fanout(self):
        # Antes era `sin_destinos`: la matriz no debe pintarlo de pronto como «cambio perdido».
        self.assertIn("sin_numero", V._ACC_FANOUT)
        self.assertIn("sin_destinos", V._ACC_FANOUT)

    def test_el_recuperador_no_lo_da_por_repartido(self):
        self.assertNotIn("sin_numero", R._ACC_FANOUT)
        self.assertIn("sin_numero", R._ACC_SIN_REPARTIR)
        self.assertIn("sin_destinos", R._ACC_FANOUT, "sin publicaciones sí es «ya procesado»")

    def test_la_accion_es_la_misma_en_los_tres(self):
        self.assertEqual(F.ACC_SIN_NUMERO, "sin_numero")
        self.assertEqual(F.ACC_LECTURA_FALLIDA, "lectura_fallida")
        self.assertEqual(set(R._ACC_SIN_REPARTIR), {F.ACC_SIN_NUMERO, F.ACC_LECTURA_FALLIDA})


if __name__ == "__main__":
    unittest.main()
