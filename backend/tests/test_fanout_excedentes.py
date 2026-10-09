"""Pruebas de los EXCEDENTES de TikTok/Temu (`fanout_excedentes` + `fanout_stock.bajar`).

── QUÉ FIJAN ────────────────────────────────────────────
TikTok y Temu suben su número solos cuando se cancela un pedido sin pagar; el
fan-out no se enteraba hasta el siguiente movimiento del SKU (2-oct: Temu 51 veces
por encima de Woo en 14 días; un SKU vendió 39 con Woo en 0). Tras cada censo se
baja lo que esté ARRIBA de Woo, y nada más:

  1. SÓLO BAJA: escribe sólo a las publicaciones del canal con MÁS que el objetivo;
     las que están por debajo (un pedido sin pagar apartado) y los otros canales no
     se tocan.
  2. RESPETA EL PLAN DEL FAN-OUT: si el plan omite ese destino (bandera, borrador de
     Temu) no escribe; en dry-run sólo simula. Lo que escribe queda en la bitácora.
  3. EL ESCRITOR RELEE EN VIVO Y NO SUBE: Temu no escribe si ya tiene lo mismo o
     menos, y si tras bajar la relectura queda por debajo, se detiene; TikTok no
     escribe sin leer su almacén de ventas.
  4. LA VUELTA existe sólo con sus dos banderas y sólo para TikTok y Temu.

── NO SE TOCA NADA REAL ─────────────────────────────────
Plan, escritores, API y bitácora van suplantados. Los SKUs son INVENTADOS: el
repo es público.

    cd backend && python -m unittest tests.test_fanout_excedentes -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import fanout_excedentes as E  # noqa: E402
from services import fanout_stock as F  # noqa: E402
from services import fanout_vivo as V  # noqa: E402

# El seguro «stock 0 ⇒ fuera de la venta» ya no tiene interruptor propio: corre siempre
# que el fan-out esté encendido. Aquí se prueban los excedentes SOLOS, así que se deja
# fuera: estas pruebas no deben depender de cómo venga FANOUT_ENABLED en el entorno (con
# el fan-out encendido, `bajar` le abriría un contexto al seguro y éste iría a la base).
# Los dos juntos se prueban en `test_fanout_seguro.Excedentes`.
_SIN_SEGURO = mock.patch.object(F, "seguro_encendido", return_value=False)


def setUpModule():
    _SIN_SEGURO.start()


def tearDownModule():
    _SIN_SEGURO.stop()


def _accion(canal, actual, objetivo, accion="escribir", omitido=None, cuenta="X", item="1"):
    return {"canal": canal, "cuenta": cuenta, "item_id": item, "stock_actual_canal": actual,
            "objetivo": objetivo, "accion": accion, "omitido_por": omitido}


def _plan(*acciones, objetivo=5):
    return {"sku": "ZZZ-0001-AZL", "ok": True, "stock_drop": objetivo, "objetivo": objetivo,
            "acciones": list(acciones)}


class Bajar(unittest.TestCase):
    def setUp(self):
        self.escritas: list[tuple] = []
        self.eventos: list[dict] = []
        self.respuesta = (True, "ok (8→5)")

        def escritor(cuenta, item, cantidad, solo_bajar=False):
            self.escritas.append((item, cantidad, solo_bajar))
            return self.respuesta

        self.parches = [
            mock.patch.dict(F._ESCRITORES_SOLO_BAJAR, {"temu": escritor, "tiktok": escritor}),
            mock.patch.object(F, "dry_run", return_value=False),
            mock.patch.object(F, "_persistir", side_effect=self.eventos.append),
        ]
        for p in self.parches:
            p.start()

    def tearDown(self):
        for p in self.parches:
            p.stop()

    def _bajar(self, plan, canal="temu"):
        with mock.patch.object(F, "plan", return_value=plan):
            return F.bajar("ZZZ-0001-AZL", canal, "excedente:" + canal)

    def test_baja_solo_lo_que_esta_arriba_y_solo_en_su_canal(self):
        r = self._bajar(_plan(_accion("temu", 8, 5, item="T1"), _accion("tiktok", 9, 5, item="K1"),
                              _accion("mercado_libre", 7, 5, item="M1")))
        self.assertEqual(r["resultado"], "bajado")
        self.assertEqual(self.escritas, [("T1", 5, True)], "sólo Temu, con solo_bajar")
        self.assertEqual(self.eventos[0]["motivo"], "excedente:temu")

    def test_por_debajo_no_se_toca(self):
        # Un pedido sin pagar apartado: el canal tiene MENOS que Woo. Subirlo lo liberaría.
        r = self._bajar(_plan(_accion("temu", 3, 5)))
        self.assertEqual((r["resultado"], self.escritas, self.eventos), ("sin_excedente", [], []))

    def test_si_el_plan_omite_el_destino_no_escribe(self):
        r = self._bajar(_plan(_accion("temu", 8, 5, accion="omitir", omitido="FANOUT_TEMU apagado")))
        self.assertEqual((r["resultado"], self.escritas, self.eventos), ("omitido", [], []))

    def test_en_dry_run_solo_simula(self):
        with mock.patch.object(F, "dry_run", return_value=True):
            r = self._bajar(_plan(_accion("tiktok", 8, 5)), canal="tiktok")
        self.assertEqual((r["resultado"], self.escritas), ("simulado", []))
        self.assertIn("DRY-RUN", self.eventos[0]["acciones"][0]["resultado"])

    def test_si_en_vivo_ya_no_estaba_arriba_no_cuenta_como_bajada(self):
        self.respuesta = (True, f"{F.NO_BAJA}: Temu tiene 5 en vivo (objetivo 5)")
        r = self._bajar(_plan(_accion("temu", 8, 5)))
        self.assertEqual((r["resultado"], self.eventos), ("sin_cambio", []))

    def test_un_error_queda_en_la_bitacora(self):
        self.respuesta = (False, "Temu no aplicó")
        r = self._bajar(_plan(_accion("temu", 8, 5)))
        self.assertEqual(r["resultado"], "error")
        self.assertTrue(self.eventos[0]["acciones"][0]["resultado"].startswith("ERROR"))

    def test_otros_canales_no_bajan_excedentes(self):
        self.assertEqual(F.bajar("ZZZ-0001-AZL", "mercado_libre", "x")["resultado"], "omitido")


class TemuSoloBajar(unittest.TestCase):
    """El escritor de Temu edita por DIFERENCIA y relee: con solo_bajar nunca sube."""

    def setUp(self):
        from services import temu as tm
        self.lecturas: list[int] = []
        self.ediciones: list[int] = []

        async def llamar(tipo, datos):
            if tipo == "bg.local.goods.list.query":
                return {"goodsList": [{"goodsId": 111, "quantity": self.lecturas.pop(0), "skuIdList": [222]}]}
            self.ediciones.append(datos["skuStockChangeList"][0]["stockDiff"])
            return {"operateResult": True, "skuStockEditStatusInfoList": [{"stockEditStatus": True}]}

        self.parches = [
            mock.patch.object(tm, "llamar", side_effect=llamar),
            mock.patch.object(tm, "disponible", return_value=True),
            mock.patch.object(F, "_en_hilo", side_effect=lambda fabrica, etiqueta, timeout=60: asyncio.run(fabrica())),
            mock.patch("time.sleep"),
            mock.patch.dict(F._temu_escrito_en, {}, clear=True),
        ]
        for p in self.parches:
            p.start()

    def tearDown(self):
        for p in self.parches:
            p.stop()

    def test_si_ya_tiene_lo_mismo_o_menos_no_escribe(self):
        self.lecturas = [4]
        ok, det = F._escribir_temu("TEMU", "111", 5, solo_bajar=True)
        self.assertTrue(ok and det.startswith(F.NO_BAJA))
        self.assertEqual(self.ediciones, [])

    def test_baja_y_verifica(self):
        self.lecturas = [9, 5]
        ok, det = F._escribir_temu("TEMU", "111", 5, solo_bajar=True)
        self.assertEqual((ok, self.ediciones), (True, [-4]))

    def test_si_tras_bajar_queda_por_debajo_no_sube(self):
        self.lecturas = [9, 4]            # una venta entre la escritura y la relectura
        ok, det = F._escribir_temu("TEMU", "111", 5, solo_bajar=True)
        self.assertEqual((ok, self.ediciones), (True, [-4]))
        self.assertIn("no se sube", det)

    def test_sin_solo_bajar_sigue_subiendo_como_siempre(self):
        self.lecturas = [4, 5]
        ok, _ = F._escribir_temu("TEMU", "111", 5)
        self.assertEqual((ok, self.ediciones), (True, [1]))


class TikTokSoloBajar(unittest.TestCase):
    def setUp(self):
        from services import tiktok as tk
        self.inventario: list[dict] | None = None
        self.escrito: list[int] = []

        async def llamar(ruta, token, params, cuerpo=None, metodo="GET"):
            if ruta.endswith("/inventory/update"):
                self.escrito.append(cuerpo["skus"][0]["inventory"][0]["quantity"])
                return {}
            return {"skus": [{"id": "S1", "inventory": self.inventario or []}]}

        self.parches = [
            mock.patch.object(tk, "llamar", side_effect=llamar),
            mock.patch.object(tk, "access_token", return_value="t"),
            mock.patch.object(tk, "cipher", return_value="c"),
            mock.patch.object(F, "_en_hilo", side_effect=lambda fabrica, etiqueta, timeout=60: asyncio.run(fabrica())),
        ]
        for p in self.parches:
            p.start()

    def tearDown(self):
        for p in self.parches:
            p.stop()

    def _vivo(self, n):
        self.inventario = [{"warehouse_id": F._ALMACEN_VENTAS_TIKTOK, "quantity": n}]

    def test_si_ya_tiene_lo_mismo_o_menos_no_escribe(self):
        self._vivo(4)
        ok, det = F._escribir_tiktok("KUBERA", "P1", 6, solo_bajar=True)
        self.assertTrue(ok and det.startswith(F.NO_BAJA))
        self.assertEqual(self.escrito, [])

    def test_baja_cuando_esta_arriba(self):
        self._vivo(9)
        self.assertTrue(F._escribir_tiktok("KUBERA", "P1", 6, solo_bajar=True)[0])
        self.assertEqual(self.escrito, [6])

    def test_sin_leer_el_almacen_no_escribe(self):
        self.inventario = []
        ok, _ = F._escribir_tiktok("KUBERA", "P1", 6, solo_bajar=True)
        self.assertEqual((ok, self.escrito), (False, []))


class Vuelta(unittest.TestCase):
    def setUp(self):
        E._ultimo.clear()
        self.bajadas: list[str] = []
        self.parches = [
            mock.patch.multiple(E.settings, fanout_excedentes_enabled=True, fanout_excedentes_tope=50),
            mock.patch.object(F, "habilitado", return_value=True),
            mock.patch.object(E, "candidatos", return_value=[
                {"sku": "ZZZ-0001-AZL", "canal_stock": 3, "stock_woo": 0},
                {"sku": "ZZZ-0002-ROJ", "canal_stock": 40, "stock_woo": 30}]),
            mock.patch.object(F, "bajar", side_effect=lambda sku, canal, motivo: (
                self.bajadas.append(motivo) or {"resultado": "bajado" if sku.endswith("AZL") else "sin_cambio",
                                                "antes": 3, "objetivo": 0})),
        ]
        for p in self.parches:
            p.start()

    def tearDown(self):
        for p in self.parches:
            p.stop()

    def test_cuenta_lo_que_hizo_por_resultado(self):
        r = E.revisar("temu")
        self.assertEqual((r["candidatos"], r.get("bajado"), r.get("sin_cambio")), (2, 1, 1))
        self.assertEqual(self.bajadas, ["excedente:temu", "excedente:temu"])
        self.assertEqual(r["muestra"], ["ZZZ-0001-AZL 3→0"])

    def test_con_la_bandera_apagada_no_hace_nada(self):
        with mock.patch.object(E.settings, "fanout_excedentes_enabled", False):
            self.assertFalse(E.revisar("temu")["ok"])
        self.assertEqual(self.bajadas, [])

    def test_con_el_fanout_apagado_no_hace_nada(self):
        with mock.patch.object(F, "habilitado", return_value=False):
            self.assertFalse(E.revisar("tiktok")["ok"])
        self.assertEqual(self.bajadas, [])

    def test_solo_tiktok_y_temu(self):
        self.assertFalse(E.revisar("mercado_libre")["ok"])


class Etiqueta(unittest.TestCase):
    def test_la_pagina_dice_que_el_canal_ofrecia_de_mas(self):
        self.assertEqual(V._origen("excedente:temu", None)[:2], ("otro", "Temu ofrecía de más"))


if __name__ == "__main__":
    unittest.main()
