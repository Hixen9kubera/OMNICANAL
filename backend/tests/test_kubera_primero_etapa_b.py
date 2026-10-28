"""Pruebas de lo que bloqueaba la ETAPA B de 2.11 (revisión independiente de MAESTRO, 28-oct-2026: #1, #2, #3).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
#1 · El registro previo a Woo no deja una venta sin pedido para siempre:
   · solo para una venta NUEVA, con su estado de ML (segunda revisión, #7; el dedupe de los sondeos ya no la
     confunde porque `estados_wc` no cuenta las filas sin pedido);
   · una venta que YA tiene pedido no se pre-registra: un PUT fallido deja kubera con el estado anterior;
   · Woo que contesta 5xx SIN excepción: la venta queda registrada y el reclamo no se suelta;
   · cancelación FULL/protegida con la bandera encendida: un solo registro, después de Woo;
   · `estados_wc` y la marca de agua (`ultimo_actualizado`) excluyen las filas sin pedido.
#2 · Un esqueleto VIEJO que se completa no es `nueva_en_kubera` (el planeador no crea OV de agosto), pero
     `vendible_cambia` sí avisa al fan-out si tiene líneas no FULL y no está cancelado.
#3 · Con FANOUT_FUENTE=kubera, una venta sin señal de kubera (cayó a MySQL o sin registro) se avisa.
B0.6 del acta (28-oct) · El reintento de una venta «Woo pendiente» (`sincronizar(reintentable=True)`, el job de
     avisos de ML que ya corre en producción) adopta o crea por la rama del reclamo huérfano, no repite la señal
     y nunca llama a `liberar`; tampoco con kubera primero apagado (la reversa de B).

Sin red ni base:
    cd backend && python -m unittest tests.test_kubera_primero_etapa_b -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from services import alertas, fanout_stock, orders_write, pedidos_ml, stock_libro  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

AHORA = datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc)
ENC = {"canal": "mercado_libre", "cuenta": "BEKURA", "external_order_id": "2000000001"}
DROP = {"sku": "ACC-0001-AZL", "cantidad": 2, "es_fulfillment": False}
FULL = {"sku": "ACC-0002-DOR", "cantidad": 1, "es_fulfillment": True}


def fila(esqueleto=False, estado="processing", lineas=(DROP,), wc=None, registrado=AHORA):
    return {"esqueleto": esqueleto, "estado_wc": estado, "wc_order_id": wc, "lineas": list(lineas),
            "registrado": registrado}


class EsqueletoViejo(unittest.TestCase):           # #2
    def test_reciente_es_nueva(self):
        s = orders_write.senal(fila(esqueleto=True, lineas=(), registrado=AHORA - timedelta(minutes=5)),
                               fila(), ENC, AHORA)
        self.assertTrue(s["nueva_en_kubera"])

    def test_viejo_no_es_nueva_pero_avisa_al_fanout(self):
        s = orders_write.senal(fila(esqueleto=True, lineas=(), registrado=AHORA - timedelta(days=40)),
                               fila(), ENC, AHORA)
        self.assertEqual((s["nueva_en_kubera"], s["vendible_cambia"]), (False, True))

    def test_viejo_cancelado_o_todo_full_no_cambia_el_vendible(self):
        viejo = fila(esqueleto=True, lineas=(), registrado=AHORA - timedelta(days=40))
        self.assertFalse(orders_write.senal(viejo, fila(estado="cancelled"), ENC, AHORA)["vendible_cambia"])
        self.assertFalse(orders_write.senal(viejo, fila(lineas=(FULL,)), ENC, AHORA)["vendible_cambia"])

    def test_sin_fila_previa_sigue_siendo_nueva(self):
        self.assertTrue(orders_write.senal(None, fila(), ENC, AHORA)["nueva_en_kubera"])


class DedupeYMarca(unittest.TestCase):              # #1
    def test_estados_wc_excluye_las_filas_sin_pedido(self):
        with mock.patch.object(sdb, "disponible", return_value=True), \
             mock.patch.object(sdb, "fetch_all", return_value=[]) as fa:
            orders_write.estados_wc(("AMAZON",))
        self.assertIn("and wc_order_id is not null", " ".join(fa.call_args.args[0].split()))

    def test_la_marca_excluye_las_filas_sin_pedido(self):
        with mock.patch.object(sdb, "disponible", return_value=True), \
             mock.patch.object(sdb, "fetch_one", return_value={"m": None}) as fo:
            orders_write.ultimo_actualizado("AMAZON")
        self.assertIn("and wc_order_id is not null", " ".join(fo.call_args.args[0].split()))


ORDEN = {"id": "2000000001", "cuenta": "BEKURA", "estado": "paid", "total": 499.0, "fecha": "2026-10-28T10:00:00Z",
         "es_full": False, "items": [{"sku": "ACC-0001-AZL", "cantidad": 2, "item_id": "MLM1"}]}


class Resp:
    def __init__(self, code, cuerpo=None):
        self.status_code, self._c, self.text = code, cuerpo or {}, ""

    def json(self):
        return self._c


class Woo:
    def __init__(self, codigo=201, llamadas=None):
        self.codigo, self.llamadas = codigo, llamadas

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, ruta, json=None):
        self.llamadas.append("woo POST")
        return Resp(self.codigo, {"id": 777})

    async def put(self, ruta, json=None):
        self.llamadas.append("woo PUT" if "status" in (json or {}) else "woo PUT meta")
        return Resp(200 if self.codigo < 300 else self.codigo, {"id": 555})


class Flujo(unittest.TestCase):                     # #1 y #3
    def setUp(self) -> None:
        self.addCleanup(mock.patch.stopall)
        self.llamadas: list[str] = []
        self.estado = "processing"
        mock.patch.object(pedidos_ml, "_schema_ok", True).start()
        mock.patch.object(pedidos_ml, "construir_payload", mock.AsyncMock(
            side_effect=lambda *a, **k: {"status": self.estado, "_skus": ["ACC-0001-AZL"],
                                         "_sin_mapear": [], "_comision": 87.3})).start()
        self.previo = mock.patch.object(orders_write, "wc_order_id_previo", return_value=None).start()
        mock.patch.object(orders_write, "reclamar", return_value=True).start()
        self.liberar = mock.patch.object(orders_write, "liberar").start()
        mock.patch.object(pedidos_ml, "_pedido_en_woo", mock.AsyncMock(return_value=None)).start()
        mock.patch.object(pedidos_ml, "_ya_compensado", return_value=False).start()
        self.registros = []

        def registrar(order_id, orden, estado, wc, *a):
            self.registros.append((estado, wc))
            self.llamadas.append(f"kubera(estado={estado}, wc={wc})")
            return {"nueva_en_kubera": len(self.registros) == 1, "vendible_cambia": False,
                    "escrito_en": self.escrito_en, "cuenta": "BEKURA", "lineas": [DROP]}
        self.escrito_en = "kubera"
        mock.patch.object(pedidos_ml, "_registrar_venta", side_effect=registrar).start()
        self.encolar = mock.patch.object(fanout_stock, "encolar_varios").start()
        mock.patch.object(fanout_stock, "seguro_encendido", return_value=False).start()
        mock.patch("services.odoo_ventas.canales_posibles", return_value=()).start()
        self.aviso = mock.patch.object(alertas, "avisar", return_value=True).start()

    def correr(self, codigo=201, orden=None, fuente="woo"):
        with mock.patch.object(settings, "pedidos_kubera_primero", True), \
             mock.patch.object(settings, "fanout_fuente", fuente), \
             mock.patch.object(pedidos_ml.httpx, "AsyncClient", Woo(codigo, self.llamadas)):
            return asyncio.run(pedidos_ml._sincronizar_serializado("2000000001", None, False,
                                                                    dict(orden or ORDEN)))

    def test_venta_nueva_se_pre_registra_con_su_estado(self):
        r = self.correr()
        self.assertTrue(r["ok"])
        self.assertEqual(self.registros, [("processing", None), ("processing", 777)])

    def test_venta_existente_no_se_pre_registra(self):
        self.previo.return_value = 555
        r = self.correr()
        self.assertTrue(r["ok"])
        self.assertEqual(self.llamadas, ["woo PUT", "kubera(estado=processing, wc=555)"])

    def test_cancelacion_full_con_la_bandera_un_solo_registro_despues_de_woo(self):
        self.previo.return_value = 555
        self.estado = "cancelled"
        r = self.correr(orden={**ORDEN, "es_full": True})
        self.assertTrue(r["ok"])
        self.assertEqual(self.registros, [("cancelled", 555)])
        self.assertEqual(self.llamadas[-1], "kubera(estado=cancelled, wc=555)")

    def test_woo_5xx_sin_excepcion_deja_la_venta_registrada_y_no_suelta(self):
        r = self.correr(codigo=500)
        self.assertFalse(r["ok"])
        self.assertEqual(self.registros, [("processing", None)])
        self.liberar.assert_not_called()

    def test_sin_senal_de_kubera_con_fanout_en_kubera_se_avisa(self):     # #3
        self.escrito_en = "mysql_respaldo"
        self.correr(fuente="kubera")
        self.assertIn("fanout_sin_senal", [c.args[0] for c in self.aviso.call_args_list])

    def test_con_senal_de_kubera_no_se_avisa(self):
        self.correr(fuente="kubera")
        self.assertNotIn("fanout_sin_senal", [c.args[0] for c in self.aviso.call_args_list])

    def test_ruta_del_worker_sin_senal_encola_los_sku_no_full(self):      # segunda revisión, #2
        # Cayó a MySQL: el worker del espejo la aplicará en milisegundos SIN señal y nunca llegará al
        # reproceso. Sus SKU no FULL se encolan aquí.
        self.escrito_en = "mysql_respaldo"
        self.correr(fuente="kubera")
        motivos = [c.kwargs.get("motivo", "") for c in self.encolar.call_args_list]
        self.assertTrue(any("(sin señal)" in m for m in motivos), motivos)
        self.assertIn(["ACC-0001-AZL"], [c.args[0] for c in self.encolar.call_args_list])

    def test_sin_senal_y_full_no_encola(self):
        self.escrito_en = "mysql_respaldo"
        # Una venta FULL pasa por la compensación (lee `_reduced_stock` de MySQL): aquí no se prueba eso.
        with mock.patch.object(pedidos_ml, "_compensar_stock_protegido",
                               mock.AsyncMock(return_value={"compensado": 0})):
            self.correr(orden={**ORDEN, "es_full": True}, fuente="kubera")
        motivos = [c.kwargs.get("motivo", "") for c in self.encolar.call_args_list]
        self.assertFalse(any("(sin señal)" in m for m in motivos), motivos)
        self.assertIn("fanout_sin_senal", [c.args[0] for c in self.aviso.call_args_list])

    def test_si_el_pre_registro_si_escribio_no_hay_alarma(self):          # segunda revisión, #6
        # El pre-registro llegó a kubera (y avisó como venta nueva); el registro de después cae a MySQL.
        escritos = iter(["kubera", "mysql_respaldo"])
        registrar_original = pedidos_ml._registrar_venta.side_effect

        def registrar(*a):
            self.escrito_en = next(escritos)
            return registrar_original(*a)
        pedidos_ml._registrar_venta.side_effect = registrar
        self.correr(fuente="kubera")
        self.assertNotIn("fanout_sin_senal", [c.args[0] for c in self.aviso.call_args_list])
        motivos = [c.kwargs.get("motivo", "") for c in self.encolar.call_args_list]
        self.assertFalse(any("(sin señal)" in m for m in motivos), motivos)

    def test_con_fanout_en_woo_no_se_avisa_aunque_no_haya_senal(self):
        self.escrito_en = "mysql_respaldo"
        with mock.patch.object(stock_libro, "lee_libro", return_value=False):
            self.correr()
        self.assertNotIn("fanout_sin_senal", [c.args[0] for c in self.aviso.call_args_list])


class ReintentoDeUnaPendiente(unittest.TestCase):    # acta 2.11, B0.6
    """El aviso falló en Woo con kubera primero: la venta quedó «Woo pendiente» (registrada, `wc_order_id` NULL,
    el reclamo puesto). La retoma el reintento de los avisos de ML, que YA corre en producción desde sep
    (`ml_webhook_reintentos.reprocesar` → `sincronizar(reintentable=True)`). Como la fila ya existe, el reintento
    PIERDE el reclamo y entra por la rama del reclamo huérfano: adopta lo que Woo tenga o lo crea, no repite la
    señal y nunca llama a `liberar`, que solo llama quien GANÓ el reclamo. Tampoco con kubera primero apagado,
    que es la reversa de B.

    La caída de Woo se simula con una EXCEPCIÓN (timeout, conexión): un 5xx que contesta no suelta el reclamo en
    ningún caso (`ok: False` sin pasar por el `except`), así que con él `liberar` nunca estaría en juego."""

    setUp = Flujo.setUp

    class WooReal(Woo):
        """Como el Woo de verdad: el PUT a /orders/<id> contesta con ESE id. `caido` = el POST no llega."""

        def __init__(self, codigo=201, llamadas=None, caido=False):
            super().__init__(codigo, llamadas)
            self.caido = caido

        async def post(self, ruta, json=None):
            if self.caido:
                self.llamadas.append("woo POST (caído)")
                raise pedidos_ml.httpx.ConnectError("Woo no contesta")
            return await super().post(ruta, json)

        async def put(self, ruta, json=None):
            await super().put(ruta, json)
            return Resp(200 if self.codigo < 300 else self.codigo, {"id": int(ruta.rsplit("/", 1)[1])})

    def sincronizar(self, caido=False, kubera_primero=True, reintentable=True):
        """`reintentable=True` = el job de reintentos; False = un aviso del webhook de la misma orden."""
        with mock.patch.object(settings, "pedidos_kubera_primero", kubera_primero),              mock.patch.object(settings, "fanout_fuente", "kubera"),              mock.patch.object(pedidos_ml.asyncio, "sleep", mock.AsyncMock()),              mock.patch.object(pedidos_ml.httpx, "AsyncClient", self.WooReal(201, self.llamadas, caido)):
            return asyncio.run(pedidos_ml.sincronizar("2000000001", proteger_stock=False, orden=dict(ORDEN),
                                                      reintentable=reintentable))

    def aviso_que_falla(self):
        """El aviso original con kubera primero: gana el reclamo, pre-registra, Woo no contesta. NO se suelta."""
        r = self.sincronizar(caido=True, reintentable=False)
        self.assertFalse(r["ok"])
        self.assertEqual(self.registros, [("processing", None)])
        self.assertEqual(self.encolar.call_count, 1)              # la señal de la venta nueva, una vez
        self.liberar.assert_not_called()
        orders_write.reclamar.return_value = False                # la fila ya existe: nadie más gana
        self.llamadas.clear()

    def test_crea_el_pedido_por_la_rama_huerfana_sin_repetir_la_senal(self):
        self.aviso_que_falla()
        r = self.sincronizar()
        self.assertTrue(r["ok"])
        self.assertEqual(orders_write.reclamar.call_args.args[-1], True)   # estricto: es un reintento
        self.assertEqual(self.llamadas, ["kubera(estado=processing, wc=None)", "woo POST",
                                         "kubera(estado=processing, wc=777)"])
        self.assertEqual(self.encolar.call_count, 1)
        self.liberar.assert_not_called()

    def test_si_woo_ya_lo_tenia_lo_adopta_y_no_crea_otro(self):
        self.aviso_que_falla()
        pedidos_ml._pedido_en_woo.return_value = 777
        r = self.sincronizar()
        self.assertTrue(r["ok"])
        self.assertNotIn("woo POST", self.llamadas)
        self.assertEqual(self.registros[-1], ("processing", 777))
        self.liberar.assert_not_called()

    def test_si_woo_sigue_caido_el_reintento_no_suelta_la_venta(self):
        self.aviso_que_falla()
        r = self.sincronizar(caido=True)
        self.assertFalse(r["ok"])
        self.assertEqual(self.registros, [("processing", None), ("processing", None)])
        self.assertEqual(self.encolar.call_count, 1)
        self.liberar.assert_not_called()

    def test_reversa_el_reintento_con_kubera_primero_apagado_no_la_libera(self):
        self.aviso_que_falla()
        r = self.sincronizar(caido=True, kubera_primero=False)
        self.assertFalse(r["ok"])
        self.assertEqual(self.registros, [("processing", None)])        # sin la bandera no hay registro previo
        self.liberar.assert_not_called()

    def test_reversa_un_aviso_nuevo_que_falla_tampoco_la_libera(self):
        # El webhook (no reintentable) con kubera primero apagado SÍ suelta lo que él reclamó. Aquí no reclamó
        # nada: la fila ya existía. La protege la guarda `if reclamo_mio` (un mutante sin ella cae aquí).
        self.aviso_que_falla()
        r = self.sincronizar(caido=True, kubera_primero=False, reintentable=False)
        self.assertFalse(r["ok"])
        self.liberar.assert_not_called()

    def test_contraprueba_el_aviso_que_si_gano_el_reclamo_lo_suelta_sin_kubera_primero(self):
        # Lo de hoy, sin la bandera: el aviso gana el reclamo, Woo no contesta y no hay rastro → `liberar`.
        r = self.sincronizar(caido=True, kubera_primero=False, reintentable=False)
        self.assertFalse(r["ok"])
        self.liberar.assert_called_once()


class LiberarSoloEsqueletos(unittest.TestCase):                      # segunda revisión, #8
    def test_el_delete_exige_esqueleto(self):
        with mock.patch.object(orders_write, "activo", return_value=True), \
             mock.patch.object(sdb, "execute") as ex:
            orders_write.liberar("mercado_libre", "BEKURA", "2000000001")
        sql = " ".join(ex.call_args.args[0].split())
        self.assertIn("wc_order_id is null and skus is null", sql)


class RafagaEnDosProcesos(unittest.TestCase):
    """El relevo de contenedores de un deploy: dos procesos con el MISMO aviso, sin el candado en memoria
    que los serializa (`_locks` es por proceso). Con kubera primero el ganador hace más trabajo antes de
    Woo (el pre-registro), lo que ensancha la carrera. El perdedor espera a que el ganador complete
    (`wc_order_id_previo`, hasta 4 vueltas de 1 s) y ADOPTA el pedido: un solo POST a Woo."""

    def test_el_perdedor_adopta_el_pedido_del_ganador(self):
        estado = {"wc": None}
        llamadas: list[str] = []
        registros: list = []
        sueltos = asyncio.Event()
        dormir = asyncio.sleep

        async def sleep_corto(_s):
            sueltos.set()                # el perdedor ya está esperando: el Woo del ganador contesta
            await dormir(0.01)

        def registrar(order_id, orden, est, wc, *a):
            registros.append((est, wc))
            if wc:
                estado["wc"] = wc
            return {"nueva_en_kubera": len(registros) == 1, "vendible_cambia": False, "escrito_en": "kubera",
                    "cuenta": "BEKURA", "lineas": [DROP]}

        class WooLento:
            def __call__(self, *a, **k):
                return self

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, ruta, json=None):
                llamadas.append("woo POST")
                await sueltos.wait()
                return Resp(201, {"id": 777})

            async def put(self, ruta, json=None):
                llamadas.append("woo PUT")
                return Resp(200, {"id": int(ruta.rsplit("/", 1)[1])})

        reclamos = iter([True, False])
        with mock.patch.object(pedidos_ml, "_schema_ok", True), \
             mock.patch.object(pedidos_ml, "construir_payload", mock.AsyncMock(
                 side_effect=lambda *a, **k: {"status": "processing", "_skus": ["ACC-0001-AZL"],
                                              "_sin_mapear": [], "_comision": 87.3})), \
             mock.patch.object(orders_write, "wc_order_id_previo", side_effect=lambda *_: estado["wc"]), \
             mock.patch.object(orders_write, "reclamar", side_effect=lambda *a: next(reclamos)), \
             mock.patch.object(orders_write, "liberar") as liberar, \
             mock.patch.object(pedidos_ml, "_pedido_en_woo", mock.AsyncMock(return_value=None)), \
             mock.patch.object(pedidos_ml, "_ya_compensado", return_value=False), \
             mock.patch.object(pedidos_ml, "_registrar_venta", side_effect=registrar), \
             mock.patch.object(fanout_stock, "encolar_varios"), \
             mock.patch.object(fanout_stock, "seguro_encendido", return_value=False), \
             mock.patch("services.odoo_ventas.canales_posibles", return_value=()), \
             mock.patch.object(alertas, "avisar", return_value=True), \
             mock.patch.object(settings, "pedidos_kubera_primero", True), \
             mock.patch.object(settings, "fanout_fuente", "woo"), \
             mock.patch.object(pedidos_ml.asyncio, "sleep", sleep_corto), \
             mock.patch.object(pedidos_ml.httpx, "AsyncClient", WooLento()):

            async def dos_procesos():
                a = asyncio.create_task(pedidos_ml._sincronizar_serializado("2000000001", None, False, dict(ORDEN)))
                await dormir(0)
                b = asyncio.create_task(pedidos_ml._sincronizar_serializado("2000000001", None, False, dict(ORDEN)))
                return await asyncio.gather(a, b)
            ra, rb = asyncio.run(dos_procesos())
        self.assertTrue(ra["ok"] and rb["ok"], (ra, rb))
        self.assertEqual(llamadas.count("woo POST"), 1, llamadas)        # sin duplicado
        self.assertIn("woo PUT", llamadas)                                # el perdedor adoptó
        self.assertEqual(registros[0], ("processing", None))             # solo el ganador pre-registró
        self.assertEqual(sum(1 for r in registros if r == ("processing", None)), 1, registros)
        liberar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
