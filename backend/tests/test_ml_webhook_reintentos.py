"""Pruebas del reintento de avisos de VENTA de ML y del indicador de /flujo.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Un pedido que falla deja el aviso PENDIENTE**, con su espera (2, 4, 8… min,
   máximo 1 h) y, al llegar al tope, agotado. Antes se marcaba procesado igual y
   la falla no se veía (24-sep: 13 ventas pagadas sin pedido).
2. **Un aviso posterior que sale bien resuelve los fallos ANTERIORES** de la
   misma orden, no los que llegaron después.
3. **El reprocesador pasa UNA vez por orden**, por `obtener_orden` +
   `sincronizar(reintentable=True)`; sin PEDIDOS_WC_ENABLED no hace nada.
4. **El receptor**: orders_v2 que falla → pendiente; que sale → procesado y
   resuelve previos; otros topics, como siempre.
5. **El indicador cuenta solo lo que pide atención** y el texto empieza con el
   total (el KPI del frontend toma la primera palabra).
6. **TikTok**: el aviso que sale bien resuelve los fallos previos de su orden.

Sin red ni base de datos.

    cd backend && python -m unittest tests.test_ml_webhook_reintentos -v
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from services import alertas, meli, ml_webhook_reintentos as mr, pedidos_ml
from services import reintentos_freno as fr
from services import supabase_db as sdb
from services import tiktok_webhook_reintentos as tr


class Espera(unittest.TestCase):
    def test_crece_y_se_topa_en_una_hora(self):
        self.assertEqual([mr.espera_reintento(n) for n in range(1, 8)],
                         [120, 240, 480, 960, 1920, 3600, 3600])

    def test_diez_intentos_cubren_mas_que_la_caida_mas_larga(self):
        total = sum(mr.espera_reintento(n) for n in range(1, 10))  # 9 esperas
        self.assertGreater(total, 93 * 60 * 3)      # la caída del 24-sep: 93 min

    def test_orden_de(self):
        self.assertEqual(mr.orden_de("/orders/2000018614325180"), "2000018614325180")
        self.assertEqual(mr.orden_de(" /orders/12 "), "12")
        for malo in (None, "", "/orders/abc", "/items/MLM1", "/orders/12/x"):
            self.assertIsNone(mr.orden_de(malo), malo)


class MarcarFallo(unittest.TestCase):
    def setUp(self):
        self.ex = mock.patch.object(sdb, "execute", return_value=1).start()
        mock.patch.object(mr.settings, "ml_webhook_reintentos_tope", 10).start()
        self.addCleanup(mock.patch.stopall)

    def test_sin_evento_no_escribe(self):
        self.assertFalse(mr.marcar_fallo(None, "x")["escrito"])
        self.ex.assert_not_called()

    def test_primer_fallo_queda_pendiente_dos_minutos(self):
        m = mr.marcar_fallo(77, "pedido WC falló: DNS", 0, "SKU-1")
        self.assertEqual((m["espera_s"], m["agotado"], m["escrito"]), (120, False, True))
        sql, p = self.ex.call_args.args
        self.assertIn("not procesado", sql)
        self.assertNotIn("procesado = true", sql)
        self.assertTrue(p["res"].startswith("reintentar · pedido WC falló"))
        self.assertEqual((p["id"], p["sku"], p["espera"]), (77, "SKU-1", 120))

    def test_al_tope_se_agota(self):
        m = mr.marcar_fallo(77, "x", 9)
        self.assertEqual((m["espera_s"], m["agotado"]), (None, True))
        self.assertTrue(self.ex.call_args.args[1]["res"].startswith("agotado · "))

    def test_si_la_base_falla_no_lanza(self):
        self.ex.side_effect = RuntimeError("kubera caída")
        self.assertFalse(mr.marcar_fallo(77, "x")["escrito"])


class ResolverPrevios(unittest.TestCase):
    def test_solo_ordenes_y_solo_anteriores(self):
        with mock.patch.object(sdb, "execute", return_value=3) as ex:
            self.assertEqual(mr.resolver_previos("/items/MLM1", 50), 0)
            ex.assert_not_called()
            self.assertEqual(mr.resolver_previos("/orders/123", 50), 3)
        sql, p = ex.call_args.args
        self.assertIn("id < %(id)s", sql)
        self.assertIn("topic = 'orders_v2'", sql)
        self.assertEqual((p["res"], p["id"]), ("/orders/123", 50))

    def test_nunca_lanza(self):
        with mock.patch.object(sdb, "execute", side_effect=RuntimeError("x")):
            self.assertEqual(mr.resolver_previos("/orders/1", 5), 0)


class Reprocesar(unittest.TestCase):
    def setUp(self):
        mock.patch.object(mr.settings, "pedidos_wc_enabled", True).start()
        mock.patch.object(mr.settings, "pedidos_wc_descuenta_stock", True).start()
        self.filas = [
            {"id": 10, "external_id": "/orders/111", "intentos": 1},
            {"id": 12, "external_id": "/orders/111", "intentos": 2},
            {"id": 11, "external_id": "/orders/222", "intentos": 3},
            {"id": 13, "external_id": "/orders/basura", "intentos": 0},
        ]
        self.pend = mock.patch.object(mr, "_pendientes", return_value=self.filas).start()
        self.ok = mock.patch.object(mr, "marcar_ok", return_value=True).start()
        self.fallo = mock.patch.object(mr, "marcar_fallo",
                                       return_value={"agotado": False}).start()
        self.previos = mock.patch.object(mr, "resolver_previos", return_value=0).start()
        self.orden = mock.patch.object(meli, "obtener_orden",
                                       new=mock.AsyncMock(return_value={"id": 1})).start()
        self.sinc = mock.patch.object(pedidos_ml, "sincronizar", new=mock.AsyncMock(
            side_effect=lambda oid, **k: {"ok": True, "wc_order_id": 9, "accion": "creado",
                                          "estado_wc": "processing"} if oid == "111"
            else {"ok": False, "motivo": "error al crear pedido: DNS"})).start()
        self.detenido = mock.patch.object(fr.FRENO_ML, "detenido", return_value=None).start()
        self.anotar = mock.patch.object(fr.FRENO_ML, "anotar_creado", return_value=False).start()
        self.agotado = mock.patch.object(fr, "avisar_agotado").start()
        self.addCleanup(mock.patch.stopall)

    def correr(self):
        return asyncio.run(mr.reprocesar())

    def test_una_pasada_por_orden_con_el_candado_de_los_sondeos(self):
        r = self.correr()
        self.assertEqual(self.orden.await_count, 2)
        self.assertEqual(self.sinc.await_count, 2)
        for c in self.sinc.await_args_list:
            self.assertEqual(c.kwargs["reintentable"], True)
            self.assertEqual(c.kwargs["proteger_stock"], False)
        self.assertEqual((r["ordenes"], r["ok"], r["reintentar"], r["no_orden"]), (2, 2, 1, 1))

    def test_la_que_sale_marca_sus_avisos_y_resuelve_los_demas(self):
        self.correr()
        self.assertEqual(sorted(c.args[0] for c in self.ok.call_args_list), [10, 12])
        self.previos.assert_called_once_with("/orders/111", 13)

    def test_la_que_falla_suma_intento_desde_lo_que_tenia(self):
        self.correr()
        llamadas = {c.args[0]: c.args for c in self.fallo.call_args_list}
        self.assertEqual(llamadas[11][2], 3)
        self.assertIn("error al crear pedido", llamadas[11][1])
        self.assertEqual(llamadas[13][2], mr.tope())   # recurso basura: se agota

    def test_sin_orden_de_ml_se_reintenta(self):
        self.orden.return_value = None
        r = self.correr()
        self.sinc.assert_not_awaited()
        self.assertEqual(r["ok"], 0)
        self.assertTrue(all("no se pudo traer la orden" in c.args[1]
                            for c in self.fallo.call_args_list if c.args[0] != 13))

    def test_sin_pedidos_no_hace_nada(self):
        with mock.patch.object(mr.settings, "pedidos_wc_enabled", False):
            r = self.correr()
        self.assertEqual(r["estado"], "omitido")
        self.pend.assert_not_called()

    def test_frenado_no_toca_nada(self):
        self.detenido.return_value = {"desde": "x", "motivo": "20 pedidos en 1 h"}
        r = self.correr()
        self.assertEqual(r["estado"], "frenado")
        self.pend.assert_not_called()
        self.sinc.assert_not_awaited()

    def test_cuenta_el_creado_y_si_llega_al_limite_se_para_ahi(self):
        self.filas[:] = [{"id": 1, "external_id": "/orders/111", "intentos": 1},
                         {"id": 2, "external_id": "/orders/333", "intentos": 1}]
        self.sinc.side_effect = lambda oid, **k: {"ok": True, "wc_order_id": 9,
                                                  "accion": "creado", "estado_wc": "p"}
        self.anotar.return_value = True           # el primero ya llega al límite
        r = self.correr()
        self.assertEqual((r["estado"], r["creados"], self.sinc.await_count), ("frenado", 1, 1))

    def test_las_actualizaciones_no_cuentan_para_el_freno(self):
        self.sinc.side_effect = lambda oid, **k: {"ok": True, "wc_order_id": 9,
                                                  "accion": "actualizado", "estado_wc": "p"}
        self.correr()
        self.anotar.assert_not_called()

    def test_la_que_se_agota_avisa(self):
        self.fallo.return_value = {"agotado": True}
        self.correr()
        ordenes = sorted(c.args[1] for c in self.agotado.call_args_list)
        self.assertIn("222", ordenes)


class FrenoTest(unittest.TestCase):
    def setUp(self):
        self.f = fr.Freno("ml", "ml_webhook_reintentos_max_creados_hora")
        mock.patch.object(mr.settings, "ml_webhook_reintentos_max_creados_hora", 3).start()
        self.ex = mock.patch.object(sdb, "execute", return_value=1).start()
        self.avisar = mock.patch.object(alertas, "avisar").start()
        self.addCleanup(mock.patch.stopall)

    def test_se_detiene_al_llegar_al_limite_y_avisa_una_vez(self):
        self.assertEqual([self.f.anotar_creado(str(i)) for i in range(3)], [False, False, True])
        self.assertEqual(self.avisar.call_count, 1)
        sql, p = self.ex.call_args.args
        self.assertIn("'detenido'", sql)
        self.assertEqual(p[:2], ("reintentos_freno", "ml"))

    def test_la_ventana_es_de_una_hora(self):
        self.f._creados.extend([0.0, 1.0])
        self.assertEqual(self.f.creados_ultima_hora(ahora=3602.0), 0)

    def test_detenido_lee_la_bitacora(self):
        with mock.patch.object(sdb, "fetch_one", return_value={
                "estado": "detenido", "created_at": "t", "detalle": {"motivo": "m"}}):
            self.assertEqual(self.f.detenido()["motivo"], "m")
        with mock.patch.object(sdb, "fetch_one", return_value={"estado": "liberado"}):
            self.assertIsNone(self.f.detenido())
        with mock.patch.object(sdb, "fetch_one", return_value=None):
            self.assertIsNone(self.f.detenido())

    def test_si_no_puede_leer_falla_cerrado(self):
        with mock.patch.object(sdb, "fetch_one", side_effect=RuntimeError("kubera caída")):
            self.assertIsNotNone(self.f.detenido())

    def test_si_la_bitacora_no_acepta_la_detencion_se_detiene_igual(self):
        self.ex.side_effect = RuntimeError("x")
        self.f.detener("prueba")
        with mock.patch.object(sdb, "fetch_one", return_value=None):
            self.assertEqual(self.f.detenido()["motivo"], "prueba")

    def test_liberar_anota_y_limpia(self):
        self.f.detener("prueba")
        self.ex.reset_mock()
        self.assertTrue(self.f.liberar())
        self.assertIn("'liberado'", self.ex.call_args.args[0])
        with mock.patch.object(sdb, "fetch_one", return_value={"estado": "liberado"}):
            self.assertIsNone(self.f.detenido())
        self.assertEqual(self.f.creados_ultima_hora(), 0)


class TikTokFreno(unittest.TestCase):
    def test_frenado_no_toca_nada(self):
        with mock.patch.object(tr.settings, "pedidos_tiktok_enabled", True), \
             mock.patch.object(fr.FRENO_TIKTOK, "detenido", return_value={"motivo": "m"}), \
             mock.patch.object(tr, "_pendientes") as pend:
            r = asyncio.run(tr.reprocesar())
        self.assertEqual(r["estado"], "frenado")
        pend.assert_not_called()


class Receptor(unittest.TestCase):
    """El final de `_procesar_ml`: qué se marca según el resultado."""

    def setUp(self):
        from routers import webhooks as wh
        self.wh = wh
        mock.patch.object(wh, "_guardar_supabase", return_value=77).start()
        mock.patch.object(wh, "_anotar_salud").start()
        self.act = mock.patch.object(wh, "_actualizar_supabase").start()
        self.fallo = mock.patch.object(mr, "marcar_fallo").start()
        self.previos = mock.patch.object(mr, "resolver_previos").start()
        mock.patch.object(wh.settings, "sync_enabled", False).start()
        mock.patch.object(wh.settings, "pedidos_wc_enabled", True).start()
        mock.patch.object(wh.meli, "obtener_orden",
                          new=mock.AsyncMock(return_value={"items": []})).start()
        self.sinc = mock.patch.object(wh.pedidos_ml, "sincronizar", new=mock.AsyncMock()).start()
        self.addCleanup(mock.patch.stopall)

    def aviso(self, topic="orders_v2", resource="/orders/555"):
        asyncio.run(self.wh._procesar_ml(None, {"topic": topic, "resource": resource}))

    def test_venta_que_falla_queda_pendiente(self):
        self.sinc.return_value = {"ok": False, "motivo": "error al crear pedido: DNS"}
        self.aviso()
        self.fallo.assert_called_once()
        self.assertEqual(self.fallo.call_args.args[0], 77)
        self.assertIn("pedido WC falló", self.fallo.call_args.args[1])
        self.act.assert_not_called()

    def test_venta_que_sale_se_marca_y_resuelve_previos(self):
        self.sinc.return_value = {"ok": True, "wc_order_id": 1, "accion": "creado",
                                  "estado_wc": "processing"}
        self.aviso()
        self.act.assert_called_once()
        self.previos.assert_called_once_with("/orders/555", 77)
        self.fallo.assert_not_called()

    def test_otros_topics_como_siempre(self):
        self.aviso(topic="shipments", resource="/shipments/1")
        self.act.assert_called_once()
        self.fallo.assert_not_called()
        self.previos.assert_not_called()


class Indicador(unittest.TestCase):
    def salud(self, **k):
        from routers import flujo
        return flujo._salud_webhooks(k)

    def test_nada_que_atender(self):
        texto, estado = self.salud(por_reintentar=0, vencidos=0, agotados=0, interrumpidos=0)
        self.assertEqual((texto.split(" ")[0], estado), ("0", "ok"))

    def test_reintentos_en_curso_es_aviso(self):
        texto, estado = self.salud(por_reintentar=4, vencidos=0, agotados=0, interrumpidos=0)
        self.assertEqual((texto.split(" ")[0], estado), ("4", "aviso"))

    def test_vencidos_agotados_o_interrumpidos_es_mal(self):
        for k in ("vencidos", "agotados", "interrumpidos"):
            base = {"por_reintentar": 1, "vencidos": 0, "agotados": 0, "interrumpidos": 0}
            base[k] = 1
            self.assertEqual(self.salud(**base)[1], "mal", k)

    def test_el_sql_ignora_avisos_sin_orden_y_no_usa_porcentaje(self):
        from routers import flujo
        sql = flujo._SQL_WEBHOOKS_ACCIONABLES
        self.assertIn("left(external_id, 5) <> 'tipo:'", sql)
        self.assertNotIn("%", sql)


class TikTokResuelvePrevios(unittest.TestCase):
    def test_ok_resuelve_los_anteriores_de_su_orden(self):
        with mock.patch.object(sdb, "execute", return_value=1) as ex:
            tr.marcar(88, {"ok": True, "accion": "actualizado"})
        self.assertEqual(ex.call_count, 2)
        self.assertIn("p.id < e.id", ex.call_args_list[1].args[0])

    def test_terminal_no_resuelve(self):
        with mock.patch.object(sdb, "execute", return_value=1) as ex:
            tr.marcar(88, {"ok": False, "terminal": True, "motivo": "x"})
        self.assertEqual(ex.call_count, 1)

    def test_si_resolver_falla_la_marca_principal_queda(self):
        with mock.patch.object(sdb, "execute", side_effect=[1, RuntimeError("x")]):
            m = tr.marcar(88, {"ok": True})
        self.assertTrue(m["escrito"])


if __name__ == "__main__":
    unittest.main()
