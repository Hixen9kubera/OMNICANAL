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

Segunda revisión de MAESTRO (3-nov):
4. **#1:** dos o más encabezados de la MISMA venta en una corrida se miden contra la venta como estaba antes
   del primero; antes solo entraba el más viejo (una cancelación se perdía).
5. **#3:** la marca de compensación sube `actualizado_at` y no dice cuándo se escribió la venta → 'ambiguo'
   si el estado difiere (queda pendiente con su nota, una vez); `evento_at` del payload manda sobre el `ts`
   del log; el worker (`_trabajar`) aplica el mismo candado.
6. **#9:** una venta que falla al armar su señal no corta el lote; el botón y el job no corren a la vez.

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


def foto(esqueleto=False, act=AHORA, estado="processing", lineas=None, wc=9, compensado=None, revertido=None):
    return {"esqueleto": esqueleto, "estado_wc": estado, "wc_order_id": wc, "actualizado_at": act,
            "stock_compensado_at": compensado, "stock_revertido_at": revertido,
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


class Vigencia(unittest.TestCase):                  # segunda revisión, #3
    T = datetime(2026, 10, 28, 17, 0, tzinfo=timezone.utc)

    def test_una_marca_de_compensacion_posterior_con_el_mismo_estado_se_aplica(self):
        antes = foto(act=AHORA, compensado=AHORA)
        self.assertEqual(kubera_mirror._vigencia(antes, self.T, dict(ENC)), "aplicar")

    def test_una_marca_de_compensacion_posterior_con_otro_estado_es_ambigua(self):
        for llave in ("compensado", "revertido"):
            antes = foto(act=AHORA, **{llave: AHORA})
            self.assertEqual(kubera_mirror._vigencia(antes, self.T, {**ENC, "estado_wc": "cancelled"}),
                             "ambiguo", llave)

    def test_una_escritura_de_la_venta_posterior_sigue_siendo_superado(self):
        antes = foto(act=AHORA, compensado=AHORA - timedelta(hours=3))
        self.assertEqual(kubera_mirror._vigencia(antes, self.T, {**ENC, "estado_wc": "cancelled"}), "superado")

    def test_todo_anterior_al_evento_se_aplica_aunque_sea_una_marca(self):
        antes = foto(act=AHORA - timedelta(hours=2), compensado=AHORA - timedelta(hours=2))
        self.assertEqual(kubera_mirror._vigencia(antes, self.T, {**ENC, "estado_wc": "cancelled"}), "aplicar")

    def test_evento_at_manda_sobre_el_ts_del_log(self):
        payload = {**ENC, "evento_at": "2026-10-28T16:30:00+00:00"}
        self.assertEqual(kubera_mirror._ts_del_evento(payload, datetime(2026, 10, 28, 17, 30)),
                         datetime(2026, 10, 28, 16, 30, tzinfo=timezone.utc))
        self.assertEqual(kubera_mirror._ts_del_evento(dict(ENC), datetime(2026, 10, 28, 17, 30)),
                         datetime(2026, 10, 28, 17, 30, tzinfo=timezone.utc))
        self.assertIsNone(kubera_mirror._ts_del_evento(dict(ENC), None))


class Cursor:
    def __init__(self):
        self.sql = []
        self.fila = None                 # lo que contesta `fetchone` (el actualizado_at que dejó el worker)

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchone(self):
        return self.fila

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

    def test_dos_encabezados_de_la_misma_venta_se_aplican_los_dos(self):     # #1, el arnés de MAESTRO
        # La venta antes de la corrida es de las 16:00; los eventos, de las 17:00 (creada) y 17:05 (cancelada).
        # Cada aplicación sube actualizado_at (orders_touch): contra la foto del momento, el segundo se veía
        # «superado» y kubera se quedaba en processing.
        eventos = self.filas(("channel.orders", datetime(2026, 10, 28, 17, 0), {**ENC, "estado_wc": "processing"}),
                             ("channel.orders", datetime(2026, 10, 28, 17, 5), {**ENC, "estado_wc": "cancelled"}))
        fotos = iter([foto(act=AHORA - timedelta(hours=2))] + [foto(act=datetime.now(timezone.utc))] * 5)
        with mock.patch.object(db, "fetch_all", return_value=eventos), \
             mock.patch.object(orders_write, "_foto", side_effect=lambda *a, **k: next(fotos)):
            r = kubera_mirror.reprocesar_errores(50, kubera_mirror.TABLAS_PEDIDO)
        self.assertEqual((r["aplicados"], r["superados"]), (2, 0))
        self.assertEqual([c.args[1]["estado_wc"] for c in self.up_orders.call_args_list],
                         ["processing", "cancelled"])

    def test_kubera_primero_el_pre_registro_y_el_registro_con_pedido(self):
        # Los dos encabezados de kubera primero cayeron al respaldo: el registro con pedido ya no se cierra.
        eventos = self.filas(("channel.orders", datetime(2026, 10, 28, 17, 0), {**ENC, "wc_order_id": None}),
                             ("channel.orders", datetime(2026, 10, 28, 17, 0, 3), {**ENC, "wc_order_id": 777}))
        fotos = iter([None] + [foto(act=datetime.now(timezone.utc), wc=None)] * 5)
        with mock.patch.object(db, "fetch_all", return_value=eventos), \
             mock.patch.object(orders_write, "_foto", side_effect=lambda *a, **k: next(fotos)):
            r = kubera_mirror.reprocesar_errores(50, kubera_mirror.TABLAS_PEDIDO)
        self.assertEqual((r["aplicados"], r["superados"]), (2, 0))
        self.assertEqual(self.up_orders.call_args_list[-1].args[1]["wc_order_id"], 777)

    def test_si_kubera_es_mas_nueva_que_los_dos_se_cierran_los_dos(self):
        eventos = self.filas(("channel.orders", datetime(2026, 10, 28, 17, 0), dict(ENC)),
                             ("channel.orders", datetime(2026, 10, 28, 17, 5), {**ENC, "estado_wc": "cancelled"}))
        with mock.patch.object(db, "fetch_all", return_value=eventos), \
             mock.patch.object(orders_write, "_foto", return_value=foto(act=AHORA)):
            r = kubera_mirror.reprocesar_errores(50, kubera_mirror.TABLAS_PEDIDO)
        self.assertEqual((r["aplicados"], r["superados"]), (0, 2))
        self.up_orders.assert_not_called()

    def test_ambiguo_queda_pendiente_con_su_nota_una_vez(self):            # #3a
        eventos = self.filas(("channel.orders", datetime(2026, 10, 28, 17, 0), {**ENC, "estado_wc": "cancelled"}))
        with mock.patch.object(db, "fetch_all", return_value=eventos), \
             mock.patch.object(orders_write, "_foto", return_value=foto(act=AHORA, compensado=AHORA)):
            r = kubera_mirror.reprocesar_errores(50, kubera_mirror.TABLAS_PEDIDO)
        self.assertEqual((r["aplicados"], r["superados"], r["ambiguos"]), (0, 0, 1))
        self.up_orders.assert_not_called()
        sql, params = self.updates[0]
        self.assertNotIn("resuelto=1", sql)                                # sigue pendiente
        self.assertIn("NOT LIKE", sql)                                     # la nota, una sola vez
        self.assertEqual(params[0], kubera_mirror._NOTA_AMBIGUO)

    def test_evento_at_decide_aunque_el_log_diga_otra_hora(self):           # #3b
        # Kubera escribió a las 17:00; el evento es de las 16:30, pero el worker falló a las 17:30.
        evento = {**ENC, "estado_wc": "processing", "evento_at": "2026-10-28T16:30:00+00:00"}
        eventos = self.filas(("channel.orders", datetime(2026, 10, 28, 17, 30), evento))
        with mock.patch.object(db, "fetch_all", return_value=eventos), \
             mock.patch.object(orders_write, "_foto",
                               return_value=foto(act=datetime(2026, 10, 28, 17, 0, tzinfo=timezone.utc))):
            r = kubera_mirror.reprocesar_errores(50, kubera_mirror.TABLAS_PEDIDO)
        self.assertEqual((r["aplicados"], r["superados"]), (0, 1))

    def test_una_venta_que_falla_al_avisar_no_corta_las_demas(self):        # #9
        otra = {**ENC, "external_order_id": "701-2"}
        eventos = self.filas(("channel.order_items", datetime(2026, 10, 28, 17, 0), {**ENC, "lineas": []}),
                             ("channel.order_items", datetime(2026, 10, 28, 17, 0), {**otra, "lineas": []}))
        llamadas = {"n": 0}

        def _foto(cur, p, tomar=False):
            llamadas["n"] += 1
            if llamadas["n"] == 3:                 # la foto de DESPUÉS de la primera venta
                raise RuntimeError("se cayó")
            return foto(esqueleto=True, lineas=[]) if llamadas["n"] <= 2 else foto()
        with mock.patch.object(db, "fetch_all", return_value=eventos), \
             mock.patch.object(orders_write, "_foto", side_effect=_foto):
            r = kubera_mirror.reprocesar_errores(50, kubera_mirror.TABLAS_PEDIDO)
        self.assertEqual(r["ventas_avisadas_al_fanout"], 1)
        self.assertEqual(self.avisar.call_args.args[1], "701-2")

    def test_el_boton_y_el_job_no_corren_a_la_vez(self):                   # #9
        self.assertTrue(kubera_mirror._reproceso_lock.acquire(blocking=False))
        try:
            with mock.patch.object(db, "fetch_all") as fa:
                r = kubera_mirror.reprocesar_errores(50, kubera_mirror.TABLAS_PEDIDO)
            self.assertFalse(r["ok"])
            self.assertIn("otro reproceso en curso", r["motivo"])
            fa.assert_not_called()
        finally:
            kubera_mirror._reproceso_lock.release()
        with mock.patch.object(db, "fetch_all", return_value=[]):
            self.assertTrue(kubera_mirror.reprocesar_errores(50, kubera_mirror.TABLAS_PEDIDO)["ok"])

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


class WorkerConVigencia(unittest.TestCase):          # segunda revisión, #3c
    def setUp(self) -> None:
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(kubera_mirror, "_get_pool", return_value=mock.Mock(connection=Conn)).start()
        self.up_orders = mock.Mock()
        mock.patch.dict(kubera_mirror._UPSERTS, {"channel.orders": self.up_orders}).start()
        mock.patch.object(kubera_mirror, "_registrar").start()
        self.persistir = mock.patch.object(kubera_mirror, "_persistir_error").start()
        self.aviso = mock.patch.object(kubera_mirror, "_avisar_espejo_aplicado").start()

    def trabajar(self, payload, antes):
        with mock.patch.object(orders_write, "_foto", return_value=antes):
            kubera_mirror._trabajar("o.py", "f", "pedidos_ml", "channel.orders", "UPSERT", payload, "701-1")

    def test_no_pisa_lo_mas_nuevo(self):
        self.trabajar({**ENC, "evento_at": "2026-10-28T17:00:00+00:00"}, foto(act=AHORA))
        self.up_orders.assert_not_called()
        self.aviso.assert_not_called()
        self.persistir.assert_not_called()

    def test_lo_ambiguo_va_a_la_cola_del_respaldo(self):
        self.trabajar({**ENC, "estado_wc": "cancelled", "evento_at": "2026-10-28T17:00:00+00:00"},
                      foto(act=AHORA, compensado=AHORA))
        self.up_orders.assert_not_called()
        self.assertIsInstance(self.persistir.call_args.args[6], kubera_mirror.VigenciaAmbigua)

    def test_mas_nuevo_que_kubera_se_aplica_y_avisa(self):
        self.trabajar({**ENC, "evento_at": "2026-10-28T19:00:00+00:00"}, foto(act=AHORA))
        self.up_orders.assert_called_once()
        self.aviso.assert_called_once()

    def test_sin_evento_at_como_siempre(self):
        with mock.patch.object(orders_write, "_foto") as f:
            kubera_mirror._trabajar("o.py", "f", "pedidos_ml", "channel.orders", "UPSERT", dict(ENC), "701-1")
        f.assert_not_called()
        self.up_orders.assert_called_once()


class WorkerDosEventosDeLaMismaVenta(unittest.TestCase):   # V1 de la verificación de 6b0350bf
    """E1 (processing) y E2 (cancelled) de la MISMA venta cayeron a MySQL un segundo aparte; el worker los aplica
    en orden minutos después. Con el `orders_touch` real, la escritura de E1 deja `actualizado_at` = la hora de la
    escritura, POSTERIOR al `evento_at` de E2. La fila de la base se simula: guarda lo que escribe el upsert."""

    E1 = "2026-10-28T17:00:00+00:00"
    E2 = "2026-10-28T17:00:01+00:00"

    def setUp(self) -> None:
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(kubera_mirror, "_ESCRITO_POR_WORKER", kubera_mirror.OrderedDict()).start()
        self.conn = Conn()
        mock.patch.object(kubera_mirror, "_get_pool", return_value=mock.Mock(connection=lambda: self.conn)).start()
        self.fila = foto(act=datetime(2026, 10, 28, 16, 0, tzinfo=timezone.utc))
        self.reloj = [datetime(2026, 10, 28, 17, 5, tzinfo=timezone.utc)]

        def _upsert(cur, p):                       # orders_touch: actualizado_at = now() de la escritura
            self.reloj[0] += timedelta(seconds=1)
            self.fila = {**self.fila, "estado_wc": p.get("estado_wc"), "actualizado_at": self.reloj[0]}
            self.conn.cur.fila = (self.reloj[0],)
        self.up_orders = mock.Mock(side_effect=_upsert)
        mock.patch.dict(kubera_mirror._UPSERTS, {"channel.orders": self.up_orders}).start()
        mock.patch.object(kubera_mirror, "_registrar").start()
        self.persistir = mock.patch.object(kubera_mirror, "_persistir_error").start()
        mock.patch.object(kubera_mirror, "_avisar_espejo_aplicado").start()
        mock.patch.object(orders_write, "_foto", side_effect=lambda cur, p, tomar=False: dict(self.fila)).start()

    def trabajar(self, **cambios):
        kubera_mirror._trabajar("o.py", "f", "pedidos_ml", "channel.orders", "UPSERT", {**ENC, **cambios}, "701-1")

    def test_e1_y_luego_e2_la_cancelacion_entra(self):
        self.trabajar(evento_at=self.E1)
        self.trabajar(estado_wc="cancelled", evento_at=self.E2)
        self.assertEqual(self.up_orders.call_count, 2)
        self.assertEqual(self.fila["estado_wc"], "cancelled")
        self.persistir.assert_not_called()

    def test_si_alguien_mas_escribio_despues_si_esta_superado(self):
        self.trabajar(evento_at=self.E1)
        # una escritura directa (kubera ya volvió) después de la del worker: es más nueva que E2
        self.fila = {**self.fila, "estado_wc": "completed",
                     "actualizado_at": self.reloj[0] + timedelta(seconds=30)}
        self.trabajar(estado_wc="cancelled", evento_at=self.E2)
        self.assertEqual(self.up_orders.call_count, 1)
        self.assertEqual(self.fila["estado_wc"], "completed")

    def test_un_evento_mas_viejo_que_el_escrito_sigue_superado(self):
        self.trabajar(estado_wc="cancelled", evento_at=self.E2)
        self.trabajar(evento_at=self.E1)              # llega después pero es anterior: no descancela
        self.assertEqual(self.up_orders.call_count, 1)
        self.assertEqual(self.fila["estado_wc"], "cancelled")

    def test_otra_venta_no_se_confunde(self):
        self.trabajar(evento_at=self.E1)
        llave = kubera_mirror._llave_venta({**ENC, "external_order_id": "701-2"})
        self.assertNotIn(llave, kubera_mirror._ESCRITO_POR_WORKER)
        self.assertIn(kubera_mirror._llave_venta(ENC), kubera_mirror._ESCRITO_POR_WORKER)

    def test_la_memoria_tiene_tope(self):
        with mock.patch.object(kubera_mirror, "_ESCRITO_MAX", 3):
            for i in range(5):
                kubera_mirror._anotar_escrito({**ENC, "external_order_id": f"X-{i}"}, self.reloj[0], self.reloj[0])
        self.assertEqual([k[2] for k in kubera_mirror._ESCRITO_POR_WORKER], ["X-2", "X-3", "X-4"])


class GuardarLlevaLaHoraDelEvento(unittest.TestCase):  # segunda revisión, #3b
    def test_al_caer_a_mysql_el_encabezado_viaja_con_evento_at(self):
        from services import supabase_db as sdb

        class Falla:
            def __enter__(self):
                raise RuntimeError("kubera caída")

            def __exit__(self, *a):
                return False
        with mock.patch.object(sdb, "get_cursor", return_value=Falla()), \
             mock.patch.object(kubera_mirror, "espejar") as esp, \
             mock.patch("services.alertas.avisar"):
            r = orders_write.guardar("o.py", "f", dict(ENC), {**ENC, "lineas": []}, "701-1", mock.Mock())
        self.assertEqual(r["escrito_en"], "mysql_respaldo")
        enc = next(c.args[5] for c in esp.call_args_list if c.args[4] == "UPSERT" and c.args[3] == "channel.orders")
        self.assertIn("evento_at", enc)
        self.assertIsNotNone(kubera_mirror._ts_del_evento(enc, None))


if __name__ == "__main__":
    unittest.main()
