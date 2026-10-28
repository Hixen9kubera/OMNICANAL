"""Pruebas del LADO DEL FAN-OUT de la señal «venta nueva en kubera» (PLAN MAESTRO 2.11,
contrato v2 entre WOO SALIDA e INVENTARIO FANOUT, 28-oct-2026).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
Con FANOUT_FUENTE=kubera (`stock_libro.lee_libro()`), `pedidos_ml._avisar_fanout`:
1. encola en la cola REAL del fan-out sólo los SKUs de líneas NO FULL, con el motivo
   «venta CUENTA ORDEN»; la matriz (`fanout_vivo._origen`) lo sigue leyendo como venta;
2. lo hace con `nueva_en_kubera` o con `vendible_cambia` (la cancelación), y con nada más;
3. no encola si todas las líneas son FULL, si no hay señal, si cayó a MySQL o si el
   fan-out sigue en Woo;
4. una venta registrada dos veces (antes y después de Woo) entra UNA vez: la segunda
   foto ya no es «nueva», y aun si lo fuera la cola la colapsa en una sola entrada;
5. nunca lanza.

Sin red ni base:
    cd backend && python -m unittest tests.test_fanout_senal_kubera -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import fanout_stock, fanout_vivo, orders_write, pedidos_ml, stock_libro  # noqa: E402

ENC = {"canal": "mercado_libre", "cuenta": "BEKURA", "external_order_id": "2000123"}


def _linea(sku: str, cantidad: int = 1, full: bool = False) -> dict:
    return {"sku": sku, "cantidad": cantidad, "es_fulfillment": full}


def _foto(lineas: list[dict], estado: str = "processing", esqueleto: bool = False) -> dict:
    return {"esqueleto": esqueleto, "estado_wc": estado, "wc_order_id": None, "lineas": lineas}


class _Cola(unittest.TestCase):
    """La cola de verdad (`fanout_stock._pendientes`), vacía y sin worker."""

    def setUp(self):
        for p in (mock.patch.object(fanout_stock, "habilitado", return_value=True),
                  mock.patch.object(fanout_stock, "_asegurar_worker"),
                  mock.patch.dict(fanout_stock._pendientes, clear=True),
                  mock.patch.object(stock_libro, "lee_libro", return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    def encolados(self) -> dict[str, str]:
        return {s: d["motivo"] for s, d in fanout_stock._pendientes.items()}


class SoloNoFull(_Cola):
    def test_nueva_encola_solo_lo_no_full_con_el_motivo_de_siempre(self):
        lineas = [_linea("AAA-0001"), _linea("BBB-0002", full=True), _linea("CCC-0003", 2)]
        pedidos_ml._avisar_fanout(orders_write.senal(None, _foto(lineas), ENC), "2000123")
        self.assertEqual(self.encolados(),
                         {"AAA-0001": "venta BEKURA 2000123", "CCC-0003": "venta BEKURA 2000123"})

    def test_la_matriz_lo_lee_como_venta_de_la_cuenta(self):
        for cuenta, texto in (("BEKURA", "venta en ML Kubera"),
                              ("SANCORFASHION", "venta en ML San Corpe"),
                              ("AMAZON", "venta en Amazon")):
            with self.subTest(cuenta=cuenta):
                fanout_stock._pendientes.clear()
                pedidos_ml._avisar_fanout(
                    orders_write.senal(None, _foto([_linea("AAA-0001")]), {**ENC, "cuenta": cuenta}), "77")
                motivo = self.encolados()["AAA-0001"]
                self.assertEqual(fanout_vivo._origen(motivo, None), ("venta", texto, None, None))

    def test_todas_full_no_encola(self):
        s = orders_write.senal(None, _foto([_linea("AAA-0001", full=True), _linea("BBB-0002", full=True)]), ENC)
        self.assertTrue(s["nueva_en_kubera"] and s["todas_full"])
        pedidos_ml._avisar_fanout(s, "2000123")
        self.assertEqual(self.encolados(), {})


class CuandoSiYCuandoNo(_Cola):
    def test_una_sola_vez_aunque_se_registre_antes_y_despues_de_woo(self):
        completa = _foto([_linea("AAA-0001")])
        previa = orders_write.senal(None, completa, ENC)                   # antes de Woo
        posterior = orders_write.senal(completa, {**completa, "wc_order_id": 555}, ENC)   # después
        self.assertEqual((previa["nueva_en_kubera"], posterior["nueva_en_kubera"],
                          posterior["vendible_cambia"]), (True, False, False))
        with mock.patch.object(fanout_stock, "encolar_varios", wraps=fanout_stock.encolar_varios) as ev:
            for s in (previa, posterior):
                pedidos_ml._avisar_fanout(s, "2000123")
        self.assertEqual(ev.call_count, 1)
        self.assertEqual(list(self.encolados()), ["AAA-0001"])

    def test_el_esqueleto_del_reclamo_se_vuelve_nueva_al_completarse(self):
        s = orders_write.senal(_foto([], esqueleto=True), _foto([_linea("AAA-0001")]), ENC)
        pedidos_ml._avisar_fanout(s, "2000123")
        self.assertEqual(list(self.encolados()), ["AAA-0001"])

    def test_cancelar_y_descancelar_encolan(self):
        viva = _foto([_linea("AAA-0001"), _linea("BBB-0002", full=True)])
        cancelada = {**viva, "estado_wc": "cancelled"}
        for antes, despues in ((viva, cancelada), (cancelada, viva)):
            with self.subTest(de=antes["estado_wc"], a=despues["estado_wc"]):
                fanout_stock._pendientes.clear()
                s = orders_write.senal(antes, despues, ENC)
                self.assertTrue(s["vendible_cambia"])
                pedidos_ml._avisar_fanout(s, "2000123")
                self.assertEqual(self.encolados(), {"AAA-0001": "venta BEKURA 2000123"})

    def test_cancelar_una_venta_solo_full_no_encola(self):
        viva = _foto([_linea("AAA-0001", full=True)])
        s = orders_write.senal(viva, {**viva, "estado_wc": "cancelled"}, ENC)
        self.assertFalse(s["vendible_cambia"])
        pedidos_ml._avisar_fanout(s, "2000123")
        self.assertEqual(self.encolados(), {})

    def test_sin_senal_o_en_mysql_no_encola(self):
        respaldo = {**orders_write.senal(None, None, ENC), "escrito_en": "mysql_respaldo"}
        for s in (None, {}, respaldo):
            with self.subTest(senal=s):
                pedidos_ml._avisar_fanout(s, "2000123")
                self.assertEqual(self.encolados(), {})

    def test_con_el_fanout_en_woo_no_hace_nada(self):
        with mock.patch.object(stock_libro, "lee_libro", return_value=False):
            pedidos_ml._avisar_fanout(orders_write.senal(None, _foto([_linea("AAA-0001")]), ENC), "1")
        self.assertEqual(self.encolados(), {})

    def test_dos_avisos_del_mismo_sku_son_una_entrada(self):
        s = orders_write.senal(None, _foto([_linea("AAA-0001")]), ENC)
        pedidos_ml._avisar_fanout(s, "2000123")
        desde = fanout_stock._pendientes["AAA-0001"]["desde"]
        pedidos_ml._avisar_fanout(s, "2000123")              # aun si llegara dos veces
        self.assertEqual(list(self.encolados()), ["AAA-0001"])
        self.assertEqual(fanout_stock._pendientes["AAA-0001"]["desde"], desde)


class NuncaLanza(_Cola):
    def test_una_falla_al_encolar_no_rompe_la_venta(self):
        s = orders_write.senal(None, _foto([_linea("AAA-0001")]), ENC)
        with mock.patch.object(fanout_stock, "encolar_varios", side_effect=RuntimeError("cola")):
            pedidos_ml._avisar_fanout(s, "2000123")          # no lanza
        with mock.patch.object(stock_libro, "lee_libro", side_effect=RuntimeError("config")):
            pedidos_ml._avisar_fanout(s, "2000123")          # tampoco


if __name__ == "__main__":
    unittest.main()
