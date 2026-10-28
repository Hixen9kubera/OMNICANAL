"""Pruebas de KUBERA PRIMERO y de la señal «venta nueva en kubera» (PLAN MAESTRO 2.11/F2.2, 28-oct-2026).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **La señal** (`orders_write.senal`, contrato v2 con INVENTARIO FANOUT):
   `nueva_en_kubera` una vez por venta (de nada o esqueleto a completa); `vendible_cambia` al
   entrar o salir de 'cancelled' con alguna línea no FULL, o al cambiar SKU/cantidad;
   `todas_full` de las LÍNEAS; la cuenta y la orden tal cual.
2. **`guardar`** toma la fila (FOR UPDATE) ANTES del upsert y devuelve la señal; si kubera falla,
   `escrito_en='mysql_respaldo'` y nada en True.
3. **Con PEDIDOS_KUBERA_PRIMERO**: la venta se registra ANTES de Woo y otra vez después; si Woo
   falla, el reclamo NO se suelta (la venta ya está con sus líneas).
4. **Apagado, como siempre**: un solo registro, DESPUÉS de Woo; un webhook fallido suelta el reclamo.
5. **Fan-out**: con FANOUT_FUENTE=kubera, SOLO desde la señal (una vez, los no FULL, motivo
   «venta CUENTA ORDEN»), aunque Woo esté caído; con woo, desde el «creado» de Woo, como hoy.

Sin red ni base:
    cd backend && python -m unittest tests.test_kubera_primero -v
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from services import fanout_stock, kubera_mirror, orders_write, pedidos_ml, stock_libro  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

ENC = {"canal": "mercado_libre", "cuenta": "BEKURA", "external_order_id": 2000000001}
DROP = {"sku": "ACC-0001-AZL", "cantidad": 2, "es_fulfillment": False}
FULL = {"sku": "ACC-0002-DOR", "cantidad": 1, "es_fulfillment": True}


def fila(esqueleto=False, estado="processing", lineas=(DROP,), wc=None):
    return {"esqueleto": esqueleto, "estado_wc": estado, "wc_order_id": wc, "lineas": list(lineas)}


class LaSenal(unittest.TestCase):
    def test_nueva_desde_nada_y_desde_el_esqueleto(self):
        self.assertTrue(orders_write.senal(None, fila(), ENC)["nueva_en_kubera"])
        self.assertTrue(orders_write.senal(fila(esqueleto=True, lineas=()), fila(), ENC)["nueva_en_kubera"])

    def test_una_sola_vez(self):
        s = orders_write.senal(fila(), fila(wc=9), ENC)
        self.assertEqual((s["nueva_en_kubera"], s["vendible_cambia"], s["wc_order_id"]), (False, False, 9))

    def test_un_esqueleto_que_sigue_esqueleto_no_es_nueva(self):
        self.assertFalse(orders_write.senal(None, fila(esqueleto=True, lineas=()), ENC)["nueva_en_kubera"])

    def test_cancelar_o_descancelar_con_linea_drop_cambia_el_vendible(self):
        self.assertTrue(orders_write.senal(fila(), fila(estado="cancelled"), ENC)["vendible_cambia"])
        self.assertTrue(orders_write.senal(fila(estado="cancelled"), fila(), ENC)["vendible_cambia"])

    def test_cancelar_una_venta_toda_full_no_cambia_el_vendible(self):
        s = orders_write.senal(fila(lineas=(FULL,)), fila(estado="cancelled", lineas=(FULL,)), ENC)
        self.assertFalse(s["vendible_cambia"])
        self.assertTrue(s["todas_full"])

    def test_cambiar_las_lineas_cambia_el_vendible(self):
        otra = {**DROP, "cantidad": 3}
        self.assertTrue(orders_write.senal(fila(), fila(lineas=(otra,)), ENC)["vendible_cambia"])

    def test_todas_full_sale_de_las_lineas(self):
        self.assertFalse(orders_write.senal(None, fila(lineas=(DROP, FULL)), ENC)["todas_full"])
        self.assertFalse(orders_write.senal(None, fila(lineas=()), ENC)["todas_full"])

    def test_llaves_del_contrato(self):
        s = orders_write.senal(None, fila(lineas=(DROP, FULL)), ENC)
        self.assertEqual((s["canal"], s["cuenta"], s["external_order_id"], s["escrito_en"]),
                         ("mercado_libre", "BEKURA", "2000000001", "kubera"))
        self.assertEqual(s["lineas"], [DROP, FULL])
        self.assertEqual(set(s), {"nueva_en_kubera", "vendible_cambia", "canal", "cuenta", "external_order_id",
                                  "estado_wc", "todas_full", "wc_order_id", "lineas", "escrito_en"})


class CursorFalso:
    def __init__(self, fotos):
        self.sql, self._fotos, self.description = [], list(fotos), None

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))

    def fetchone(self):
        return self._fotos.pop(0)


class Guardar(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(kubera_mirror, "activo", return_value=True).start()
        mock.patch.object(settings, "orders_espejo_inverso", False).start()
        self.up = mock.patch.object(kubera_mirror, "_up_channel_orders").start()
        mock.patch.object(kubera_mirror, "_up_channel_order_items").start()

    def test_toma_la_fila_antes_del_upsert_y_devuelve_la_senal(self):
        cur = CursorFalso([None, fila()])
        orden_de_llamadas = []
        self.up.side_effect = lambda c, e: orden_de_llamadas.append(("upsert", len(cur.sql)))

        @contextlib.contextmanager
        def gc():
            yield cur
        with mock.patch.object(sdb, "get_cursor", gc):
            s = orders_write.guardar("x.py", "f", dict(ENC), {"lineas": []}, "BEKURA:1", lambda: None)
        fotos = [i for i, q in enumerate(cur.sql) if "from channel.orders o" in q]
        # #6: no choca con el `for key share` de las FK de order_items
        self.assertTrue(cur.sql[fotos[0]].endswith("for no key update of o"))
        self.assertLess(fotos[0], orden_de_llamadas[0][1])        # la foto de antes va primero
        self.assertTrue(s["nueva_en_kubera"])

    def test_una_senal_que_falla_no_dispara_el_respaldo(self):
        # #7: kubera YA escribió y confirmó; armar la señal fuera del try.
        cur = CursorFalso([None, fila()])

        @contextlib.contextmanager
        def gc():
            yield cur
        mysql = mock.Mock()
        with mock.patch.object(sdb, "get_cursor", gc), \
             mock.patch.object(orders_write, "senal", side_effect=[RuntimeError("x"), orders_write.senal(None, None, ENC)]), \
             mock.patch.object(kubera_mirror, "espejar") as espejar:
            s = orders_write.guardar("x.py", "f", dict(ENC), {"lineas": []}, "BEKURA:1", mysql)
        mysql.assert_not_called()
        espejar.assert_not_called()
        self.assertEqual((s["escrito_en"], s["nueva_en_kubera"]), ("kubera", False))
        self.assertIn("error_senal", s)

    def test_kubera_caida_es_mysql_respaldo_sin_senal(self):
        @contextlib.contextmanager
        def gc():
            raise RuntimeError("sin kubera")
            yield
        mysql = mock.Mock()
        with mock.patch.object(sdb, "get_cursor", gc), mock.patch.object(kubera_mirror, "espejar"), \
             mock.patch("services.alertas.avisar"):
            s = orders_write.guardar("x.py", "f", dict(ENC), {"lineas": []}, "BEKURA:1", mysql)
        mysql.assert_called_once()
        self.assertEqual((s["escrito_en"], s["nueva_en_kubera"], s["vendible_cambia"]),
                         ("mysql_respaldo", False, False))


# ── el flujo de sincronizar ───────────────────────────────────────────────────
ORDEN = {"id": "2000000001", "cuenta": "BEKURA", "estado": "paid", "total": 499.0, "fecha": "2026-10-28T10:00:00Z",
         "es_full": False, "items": [{"sku": "ACC-0001-AZL", "cantidad": 2, "item_id": "MLM1"}]}


class Resp:
    def __init__(self, code, cuerpo=None):
        self.status_code, self._c, self.text = code, cuerpo or {}, ""

    def json(self):
        return self._c


class Woo:
    def __init__(self, falla=False):
        self.falla, self.llamadas = falla, []

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, ruta, json=None):
        self.llamadas.append("woo")
        if self.falla:
            raise RuntimeError("Woo caído")
        return Resp(201, {"id": 777})

    async def put(self, ruta, json=None):
        self.llamadas.append("woo")
        return Resp(200, {"id": 777})


class Flujo(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(mock.patch.stopall)
        self.llamadas: list[str] = []
        mock.patch.object(pedidos_ml, "_schema_ok", True).start()
        mock.patch.object(pedidos_ml, "construir_payload", mock.AsyncMock(
            side_effect=lambda *a, **k: {"status": "processing", "_skus": ["ACC-0001-AZL"],
                                         "_sin_mapear": [], "_comision": 87.3})).start()
        mock.patch.object(orders_write, "wc_order_id_previo", return_value=None).start()
        mock.patch.object(orders_write, "reclamar", return_value=True).start()
        self.liberar = mock.patch.object(orders_write, "liberar").start()
        mock.patch.object(pedidos_ml, "_pedido_en_woo", mock.AsyncMock(return_value=None)).start()
        self.senales = []

        def registrar(order_id, orden, estado, wc, *a):
            self.llamadas.append(f"kubera(wc={wc})")
            s = orders_write.senal(None if not self.senales else fila(wc=self.senales[-1]["wc_order_id"]),
                                   fila(wc=wc), {"canal": "mercado_libre", "cuenta": "BEKURA",
                                                 "external_order_id": order_id})
            self.senales.append(s)
            return s
        mock.patch.object(pedidos_ml, "_registrar_venta", side_effect=registrar).start()
        self.encolar = mock.patch.object(fanout_stock, "encolar_varios").start()
        mock.patch.object(fanout_stock, "seguro_encendido", return_value=False).start()
        mock.patch("services.odoo_ventas.canales_posibles", return_value=()).start()

    def correr(self, primero: bool, fuente: str, woo: Woo):
        self.woo = woo

        class Espia(list):
            def append(s, x):
                self.llamadas.append(x)
        woo.llamadas = Espia()
        with mock.patch.object(settings, "pedidos_kubera_primero", primero), \
             mock.patch.object(settings, "fanout_fuente", fuente), \
             mock.patch.object(pedidos_ml.httpx, "AsyncClient", woo):
            r = asyncio.run(pedidos_ml._sincronizar_serializado("2000000001", None, False, dict(ORDEN)))
        return r

    def test_apagado_registra_despues_de_woo_como_siempre(self):
        r = self.correr(False, "woo", Woo())
        self.assertEqual(self.llamadas, ["woo", "kubera(wc=777)"])
        self.assertTrue(r["ok"])
        self.encolar.assert_called_once_with(["ACC-0001-AZL"], motivo="venta BEKURA 2000000001")

    def test_encendido_registra_antes_y_despues_de_woo(self):
        r = self.correr(True, "woo", Woo())
        self.assertEqual(self.llamadas, ["kubera(wc=None)", "woo", "kubera(wc=777)"])
        self.assertTrue(r["ok"])
        self.assertEqual(r["kubera"]["wc_order_id"], 777)

    def test_woo_caido_con_kubera_primero_no_suelta_el_reclamo(self):
        r = self.correr(True, "woo", Woo(falla=True))
        self.assertFalse(r["ok"])
        self.assertEqual(self.llamadas, ["kubera(wc=None)", "woo"])
        self.liberar.assert_not_called()

    def test_woo_caido_apagado_suelta_el_reclamo_como_siempre(self):
        self.correr(False, "woo", Woo(falla=True))
        self.liberar.assert_called_once()

    def test_fanout_desde_kubera_una_vez_aunque_woo_responda(self):
        self.correr(True, "kubera", Woo())
        self.encolar.assert_called_once_with(["ACC-0001-AZL"], motivo="venta BEKURA 2000000001")

    def test_fanout_desde_kubera_aunque_woo_este_caido(self):
        self.correr(True, "kubera", Woo(falla=True))
        self.encolar.assert_called_once_with(["ACC-0001-AZL"], motivo="venta BEKURA 2000000001")

    def test_fanout_desde_kubera_sin_kubera_primero_sale_del_registro_de_despues(self):
        self.correr(False, "kubera", Woo())
        self.encolar.assert_called_once_with(["ACC-0001-AZL"], motivo="venta BEKURA 2000000001")

    def test_sin_senal_no_hay_fanout_desde_kubera(self):
        with mock.patch.object(stock_libro, "lee_libro", return_value=True):
            pedidos_ml._avisar_fanout(None, "1")
            pedidos_ml._avisar_fanout({"nueva_en_kubera": False, "vendible_cambia": False, "lineas": [DROP]}, "1")
            pedidos_ml._avisar_fanout({"nueva_en_kubera": True, "lineas": [FULL], "cuenta": "BEKURA"}, "1")
        self.encolar.assert_not_called()

    def test_senal_sin_lineas_deja_rastro(self):
        with mock.patch.object(stock_libro, "lee_libro", return_value=True),              self.assertLogs("omnicanal.pedidos_ml", level="WARNING") as lg:
            pedidos_ml._avisar_fanout({"nueva_en_kubera": True, "lineas": [], "cuenta": "BEKURA"}, "1")
        self.encolar.assert_not_called()
        self.assertIn("sin líneas", lg.output[0])


if __name__ == "__main__":
    unittest.main()
