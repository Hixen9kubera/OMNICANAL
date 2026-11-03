"""Pruebas del REPROCESO DEL RESPALDO DE PEDIDOS (PLAN MAESTRO 2.15, riesgo 21; 28-oct-2026).

Una venta cuyo registro en kubera falla va a MySQL y su evento espera en `espejo_kubera_log`; hasta que
el reproceso la aplica, el libro no la resta. Hasta hoy solo había un botón.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **El candado de vigencia** (`_superado`): un encabezado encolado NO se aplica si kubera ya tiene la
   venta completa y más nueva (se cierra con la nota «superado»); un esqueleto o una venta que no
   existe sí se llenan. Las líneas siempre se aplican (solo rellenan).
2. **El filtro por tablas** del job (`TABLAS_PEDIDO`) llega al SELECT.
3. **La señal por venta**: foto antes del PRIMER evento de la venta, foto después del último, y el
   mismo enganche del fan-out de `sincronizar` (`pedidos_ml._avisar_fanout`); sin cambio, nada.

Sin red ni base:
    cd backend && python -m unittest tests.test_respaldo_pedidos -v
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import db, kubera_mirror, orders_write, pedidos_ml  # noqa: E402

AHORA = datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc)
ENC = {"canal": "amazon", "cuenta": "AMAZON", "external_order_id": "701-1", "wc_order_id": 9,
       "estado_wc": "processing", "skus": ["ACC-0001-AZL"]}


def foto(esqueleto=False, act=AHORA, estado="processing", lineas=None):
    return {"esqueleto": esqueleto, "estado_wc": estado, "wc_order_id": 9, "actualizado_at": act,
            "lineas": lineas if lineas is not None else [{"sku": "ACC-0001-AZL", "cantidad": 1,
                                                          "es_fulfillment": False}]}


class Superado(unittest.TestCase):
    def test_sin_fila_o_esqueleto_no_esta_superado(self):
        ts = datetime(2026, 10, 28, 17, 0)
        self.assertFalse(kubera_mirror._superado(None, ts))
        self.assertFalse(kubera_mirror._superado(foto(esqueleto=True), ts))

    def test_completa_y_mas_nueva_esta_superado(self):
        self.assertTrue(kubera_mirror._superado(foto(act=AHORA), datetime(2026, 10, 28, 17, 0)))

    def test_completa_pero_mas_vieja_se_aplica(self):
        self.assertFalse(kubera_mirror._superado(foto(act=AHORA - timedelta(hours=2)),
                                                 datetime(2026, 10, 28, 17, 0)))


class Cursor:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Conn:
    def __init__(self):
        self.cur = Cursor()

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class Reproceso(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(kubera_mirror, "disponible", return_value=True).start()
        mock.patch.object(kubera_mirror, "_asegurar_tabla_log", return_value=True).start()
        mock.patch.object(kubera_mirror, "_get_pool", return_value=mock.Mock(connection=Conn)).start()
        self.updates = []
        mock.patch.object(db, "execute", side_effect=lambda sql, p=None: self.updates.append((sql, p))).start()
        self.up_orders = mock.Mock()
        self.up_items = mock.Mock()
        mock.patch.dict(kubera_mirror._UPSERTS, {"channel.orders": self.up_orders,
                                                 "channel.order_items": self.up_items}).start()
        self.avisar = mock.patch.object(pedidos_ml, "_avisar_fanout").start()

    def filas(self, *eventos):
        return [{"id": i, "ts": ts, "tabla_destino": t, "payload_json": json.dumps(pl)}
                for i, (t, ts, pl) in enumerate(eventos, start=1)]

    def test_encabezado_superado_se_cierra_sin_aplicar_y_las_lineas_si(self):
        ts = datetime(2026, 10, 28, 17, 0)
        eventos = self.filas(("channel.orders", ts, ENC), ("channel.order_items", ts, {**ENC, "lineas": []}))
        with mock.patch.object(db, "fetch_all", return_value=eventos), \
             mock.patch.object(orders_write, "_foto", return_value=foto(act=AHORA)):
            r = kubera_mirror.reprocesar_errores(50, kubera_mirror.TABLAS_PEDIDO)
        self.up_orders.assert_not_called()
        self.up_items.assert_called_once()
        self.assertEqual((r["superados"], r["aplicados"]), (1, 1))
        self.assertIn(kubera_mirror._NOTA_SUPERADO, [p[0] for _, p in self.updates if p and len(p) == 2])

    def test_esqueleto_se_llena_y_avisa_al_fanout_una_vez_por_venta(self):
        ts = datetime(2026, 10, 28, 17, 0)
        eventos = self.filas(("channel.orders", ts, ENC), ("channel.order_items", ts, {**ENC, "lineas": []}))
        fotos = [foto(esqueleto=True, lineas=[]), foto(), foto()]     # antes h, antes l, después
        with mock.patch.object(db, "fetch_all", return_value=eventos), \
             mock.patch.object(orders_write, "_foto", side_effect=lambda *a, **k: fotos.pop(0)):
            r = kubera_mirror.reprocesar_errores(50, kubera_mirror.TABLAS_PEDIDO)
        self.up_orders.assert_called_once()
        self.assertEqual((r["aplicados"], r["ventas_avisadas_al_fanout"]), (2, 1))
        senal, oid = self.avisar.call_args.args
        self.assertTrue(senal["nueva_en_kubera"])
        self.assertEqual(oid, "701-1")

    def test_sin_cambio_no_avisa(self):
        ts = datetime(2026, 10, 28, 17, 0)
        eventos = self.filas(("channel.order_items", ts, {**ENC, "lineas": []}))
        with mock.patch.object(db, "fetch_all", return_value=eventos), \
             mock.patch.object(orders_write, "_foto", return_value=foto()):
            r = kubera_mirror.reprocesar_errores(50, kubera_mirror.TABLAS_PEDIDO)
        self.assertEqual(r["ventas_avisadas_al_fanout"], 0)
        self.avisar.assert_not_called()

    def test_el_filtro_de_tablas_llega_al_select(self):
        with mock.patch.object(db, "fetch_all", return_value=[]) as fa:
            kubera_mirror.reprocesar_errores(50, kubera_mirror.TABLAS_PEDIDO)
        sql, params = fa.call_args.args
        self.assertIn("AND tabla_destino IN (%s,%s)", sql)
        self.assertEqual(params, ("channel.orders", "channel.order_items", 50))

    def test_sin_filtro_como_siempre(self):
        with mock.patch.object(db, "fetch_all", return_value=[]) as fa:
            kubera_mirror.reprocesar_errores(500)
        self.assertNotIn("tabla_destino IN", fa.call_args.args[0])
        self.assertEqual(fa.call_args.args[1], (500,))


if __name__ == "__main__":
    unittest.main()
