"""Captura de devoluciones de Mercado Libre (`services/devoluciones_ml.py`) y
sus cinco fallas medidas el 5-oct-2026:

  F1  las devoluciones que ML abre como reclamo `mediations` se tiraban;
  F2  el cliente HTTP se cerraba antes de pedir el motivo (y no se guardaba);
  F3  el barrido solo miraba lo creado en 2 días: lo abierto viejo se quedaba;
  F4  `expired` no estaba en el mapa y se veía como 'abierta';
  F5  `destino` iba fijo en NULL aunque el payload lo traía.

Sin red y sin base: ML se simula con `httpx.MockTransport` y la base con
parches sobre las funciones del módulo (`_lineas_de_pedidos`,
`_cuentas_kubera`, `_duplicada_de`, `_returns_guardado`) o sobre `sdb`. Los
crudos son recortes con la forma de las respuestas de ML, SIN datos personales.

    cd backend && python -m unittest tests.test_devoluciones_ml -v
"""
from __future__ import annotations

import asyncio
import ast
import contextlib
import importlib.util
import io
import json
import re
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

import httpx

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import devoluciones_ml as dml  # noqa: E402

ML = "https://api.mercadolibre.com"
MIGRACION_0049 = BACKEND.parent / "supabase" / "migrations" / "0049_channel_devoluciones.sql"
OID = "2000000001"


def _run(coro):
    return asyncio.run(coro)


def _claim(cid: str = "5001", tipo: str = "returns", related=None,
           status: str = "opened", reason: str | None = "PDD9939") -> dict:
    c = {"id": int(cid), "type": tipo, "stage": "claim", "status": status,
         "resource": "order", "resource_id": int(OID), "reason_id": reason,
         "claimed_quantity": 1,
         "date_created": "2026-10-01T10:00:00.000-04:00",
         "last_updated": "2026-10-02T10:00:00.000-04:00"}
    if related is not None:
        c["related_entities"] = related
    return c


def _devol(status: str = "shipped", money: str = "retained",
           destinos: tuple = ("seller_address",), rid: int = 9001) -> dict:
    return {"id": rid, "status": status, "status_money": money,
            "subtype": "return_partial",
            "shipments": [{"shipment_id": 7000 + i, "type": "return",
                           "destination": {"name": d}}
                          for i, d in enumerate(destinos)],
            "orders": [{"order_id": int(OID), "item_id": "MLM111",
                        "return_quantity": "1.0"}]}


def _linea(oid: str) -> dict:
    return {"external_order_id": oid, "cuenta": "BEKURA", "estado_canal": "paid",
            "account_id": "acc-1", "linea": 1, "item_id": "MLM111",
            "sku": "SKU-0001-NEG", "cantidad": 1, "precio_unitario": 250.0,
            "es_fulfillment": True}


class FakeML:
    """Un ML de mentira. `rutas`: sufijo de ruta → (status, json) o callable(req)."""

    def __init__(self, claim: dict | None = None, returns=(200, None),
                 detalle=(200, {"title": "Devolución en camino"}),
                 motivo=(200, {"detail": "Llegó lo que compré pero no lo quiero"})):
        self.claim = claim
        self.returns = returns
        self.detalle = detalle
        self.motivo = motivo
        self.pedidas: list[httpx.Request] = []

    def rutas(self) -> list[str]:
        return [r.url.path for r in self.pedidas]

    def contar(self, patron: str) -> int:
        return sum(1 for p in self.rutas() if re.search(patron, p))

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.pedidas.append(req)
        p = req.url.path
        if "/claims/reasons/" in p:
            st, j = self.motivo
            return httpx.Response(st, json=j or {})
        if p.endswith("/returns"):
            st, j = self.returns
            if isinstance(j, (bytes, str)):
                return httpx.Response(st, content=j)
            return httpx.Response(st, json=j or {})
        if p.endswith("/detail"):
            st, j = self.detalle
            return httpx.Response(st, json=j or {})
        if re.search(r"/claims/\d+$", p):
            if self.claim is None:
                return httpx.Response(404, json={})
            return httpx.Response(200, json=self.claim)
        return httpx.Response(418, json={"inesperada": p})

    def cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self), follow_redirects=True)


class _Base(unittest.TestCase):
    TOK = {"BEKURA": "tok-b", "SANCORFASHION": "tok-s"}

    def setUp(self):
        dml._NO_DEVOLUCION.clear()
        dml._MOTIVOS.clear()
        dml._INTENTADOS.clear()
        self.guardadas: list[tuple[dict, list]] = []
        mock.patch.object(dml, "_lineas_de_pedidos",
                          side_effect=lambda oids: {o: [_linea(o)] for o in oids}).start()
        mock.patch.object(dml, "_cuentas_kubera",
                          return_value={(dml.CANAL, "BEKURA"): "acc-1"}).start()
        self.dup = mock.patch.object(dml, "_duplicada_de", return_value=None).start()
        self.previa = mock.patch.object(dml, "_returns_guardado", return_value=False).start()
        self.propias = mock.patch.object(dml, "_ya_guardados", return_value=set()).start()
        # Con `tokens` inyectado jamás se toca meli; y nada escribe en la base.
        mock.patch.object(dml.meli, "_access_token",
                          side_effect=AssertionError("no se debe tocar meli")).start()
        mock.patch.object(dml.sdb, "get_cursor",
                          side_effect=AssertionError("no se debe escribir")).start()
        self.addCleanup(mock.patch.stopall)

    def guardar(self, cab, lineas):
        self.guardadas.append((cab, lineas))

    def sinc(self, fake: FakeML, cid: str = "5001", **kw):
        async def _go():
            async with fake.cliente() as cli:
                return await dml.sincronizar(cid, "BEKURA", detectado_via="webhook",
                                             cli=cli, tokens=self.TOK,
                                             guardar=self.guardar, **kw)
        return _run(_go())


# ── F1 · mediaciones ─────────────────────────────────────────────────────────

class F1Mediaciones(_Base):
    def test_mediacion_con_devolucion_se_guarda(self):
        fake = FakeML(_claim(tipo="mediations", related=["return"]),
                      returns=(200, _devol(status="shipped")))
        r = self.sinc(fake, mediaciones=True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["accion"], "guardado")
        self.assertEqual(r["estado"], "en_transito")
        self.assertEqual(len(self.guardadas), 1)
        cab, lineas = self.guardadas[0]
        self.assertEqual(cab["external_return_id"], "5001")
        self.assertEqual(cab["tipo"], "devolucion")
        self.assertEqual(json.loads(cab["payload"])["claim"]["type"], "mediations")
        self.assertEqual(lineas[0]["sku"], "SKU-0001-NEG")
        self.assertEqual(fake.contar(r"/detail$"), 1)

    def test_mediacion_returns_200_sin_related_entities_se_guarda(self):
        fake = FakeML(_claim(tipo="mediations"), returns=(200, _devol(status="label_generated")))
        r = self.sinc(fake, mediaciones=True)
        self.assertEqual(r["accion"], "guardado")
        self.assertEqual(r["estado"], "abierta")

    def test_mediacion_solo_related_entities_se_guarda(self):
        fake = FakeML(_claim(tipo="mediations", related=["return"]), returns=(404, {}))
        r = self.sinc(fake, mediaciones=True)
        self.assertEqual(r["accion"], "guardado")

    def test_related_entities_como_objetos(self):
        crudo = {"claim": _claim(tipo="mediations", related=[{"type": "return"}]),
                 "returns": {}}
        self.assertEqual(dml.clasificar(crudo, mediaciones=True)[0], "guardar")

    def test_mediacion_sin_devolucion_espera_y_no_se_cachea(self):
        fake = FakeML(_claim(tipo="mediations", related=[]), returns=(404, {}))
        r = self.sinc(fake, mediaciones=True)
        self.assertTrue(r["ok"])
        self.assertEqual((r["accion"], r["decision"]), ("ignorado", "esperar"))
        self.assertNotIn("5001", dml._NO_DEVOLUCION)
        self.assertEqual(self.guardadas, [])
        self.assertEqual(fake.contar(r"/detail$"), 0)       # GET ahorrado
        # El siguiente aviso VUELVE a preguntar: la devolución puede llegar.
        self.sinc(fake, mediaciones=True)
        self.assertEqual(fake.contar(r"/claims/\d+$"), 2)

    def test_mediacion_que_gana_su_devolucion_despues_entra(self):
        fake = FakeML(_claim(tipo="mediations", related=[]), returns=(404, {}))
        self.assertEqual(self.sinc(fake, mediaciones=True)["decision"], "esperar")
        fake.claim = _claim(tipo="mediations", related=["return"])
        fake.returns = (200, _devol(status="label_generated"))
        self.assertEqual(self.sinc(fake, mediaciones=True)["accion"], "guardado")

    def test_mediacion_con_returns_error_y_related_return_se_guarda_sin_envio(self):
        fake = FakeML(_claim(tipo="mediations", related=["return"]),
                      returns=(401, b'{"message":"Error executing GET [client:shipments]"}'))
        with self.assertLogs(dml.log, "WARNING"):
            r = self.sinc(fake, mediaciones=True)
        self.assertEqual(r["accion"], "guardado")
        cab, _ = self.guardadas[0]
        self.assertIsNone(cab["estado_dinero"])
        self.assertIsNone(cab["destino"])
        self.assertEqual(json.loads(cab["payload"])["returns_error"], 401)

    def test_mediacion_con_returns_error_sin_related_espera(self):
        fake = FakeML(_claim(tipo="mediations"),
                      returns=(401, b'{"message":"Error executing GET [client:shipments]"}'))
        with self.assertLogs(dml.log, "WARNING"):
            r = self.sinc(fake, mediaciones=True)
        self.assertEqual(r["decision"], "esperar")
        self.assertEqual(self.guardadas, [])

    def test_cancelacion_se_sigue_cacheando(self):
        fake = FakeML(_claim(tipo="cancel_purchase"), returns=(404, {}))
        r = self.sinc(fake, mediaciones=True)
        self.assertEqual((r["accion"], r["decision"]), ("ignorado", "descartar"))
        self.assertIn("5001", dml._NO_DEVOLUCION)
        n = len(fake.pedidas)
        r2 = self.sinc(fake, mediaciones=True)
        self.assertEqual(r2["accion"], "ignorado")
        self.assertEqual(len(fake.pedidas), n)               # ni un GET más

    def test_mediaciones_apagadas_comportamiento_de_hoy(self):
        fake = FakeML(_claim(tipo="mediations", related=["return"]),
                      returns=(200, _devol()))
        r = self.sinc(fake, mediaciones=False)
        self.assertEqual((r["accion"], r["decision"]), ("ignorado", "descartar"))
        self.assertEqual(self.guardadas, [])
        self.assertIn("5001", dml._NO_DEVOLUCION)
        # …pero ese «no» no tapa a quien las pide encendidas (refresco, script).
        r2 = self.sinc(fake, mediaciones=True)
        self.assertEqual(r2["accion"], "guardado")

    def test_por_omision_lee_el_ajuste(self):
        fake = FakeML(_claim(tipo="mediations", related=["return"]),
                      returns=(200, _devol()))
        with mock.patch.object(dml.settings, "devoluciones_ml_mediaciones", True):
            self.assertEqual(self.sinc(fake)["accion"], "guardado")
        dml._NO_DEVOLUCION.clear()
        with mock.patch.object(dml.settings, "devoluciones_ml_mediaciones", False):
            self.assertEqual(self.sinc(fake)["accion"], "ignorado")

    def test_returns_sigue_igual(self):
        fake = FakeML(_claim(tipo="returns"), returns=(200, _devol(status="delivered")))
        r = self.sinc(fake, mediaciones=False)
        self.assertEqual((r["accion"], r["estado"]), ("guardado", "recibida"))

    def test_misma_devolucion_en_otro_claim_no_se_duplica(self):
        self.dup.return_value = "4999"
        fake = FakeML(_claim(tipo="mediations", related=["return"]),
                      returns=(200, _devol(rid=9001)))
        with self.assertLogs(dml.log, "WARNING") as logs:
            r = self.sinc(fake, mediaciones=True)
        self.assertIn("no se duplica", logs.output[0])
        self.assertEqual((r["accion"], r["decision"]), ("ignorado", "duplicada"))
        self.assertIn("4999", r["motivo"])
        self.assertFalse(r["retirada"])          # un guardar sin `retirar` no borra
        self.assertEqual(self.guardadas, [])
        self.dup.assert_called_once_with("BEKURA", OID, "5001", 9001)

    def _escritor(self, borradas: int):
        """Un `guardar` con `retirar`, como el del script."""
        retiros: list[tuple] = []

        class Escritor:
            def __call__(_s, cab, lineas):
                self.guardadas.append((cab, lineas))

            def retirar(_s, cuenta, perdedor, ganador, rid):
                retiros.append((cuenta, perdedor, ganador, rid))
                return borradas
        return Escritor(), retiros

    def test_duplicada_el_mayor_retira_su_fila_vieja(self):
        """La mediación 5001 se guardó SIN returns.id; después el claim 4999 trajo
        esa devolución. Al releerla ya con id: se queda 4999 (menor) y la fila
        vieja de 5001 se retira, no solo se deja de escribir."""
        self.dup.return_value = "4999"
        escritor, retiros = self._escritor(borradas=1)
        fake = FakeML(_claim(tipo="mediations", related=["return"]),
                      returns=(200, _devol(rid=9001)))

        async def _go():
            async with fake.cliente() as cli:
                return await dml.sincronizar("5001", "BEKURA", cli=cli, tokens=self.TOK,
                                             guardar=escritor, mediaciones=True)
        with self.assertLogs(dml.log, "WARNING") as logs:
            r = _run(_go())
        self.assertEqual((r["accion"], r["decision"], r["retirada"]),
                         ("ignorado", "duplicada", True))
        self.assertEqual(retiros, [("BEKURA", "5001", "4999", 9001)])
        self.assertEqual(self.guardadas, [])
        self.assertIn("se retiró la fila vieja", logs.output[0])

    def test_duplicada_el_menor_se_escribe_y_retira_al_otro(self):
        self.dup.return_value = "5002"
        escritor, retiros = self._escritor(borradas=1)
        fake = FakeML(_claim(tipo="mediations", related=["return"]),
                      returns=(200, _devol(rid=9001)))

        async def _go():
            async with fake.cliente() as cli:
                return await dml.sincronizar("5001", "BEKURA", cli=cli, tokens=self.TOK,
                                             guardar=escritor, mediaciones=True)
        with self.assertLogs(dml.log, "WARNING"):
            r = _run(_go())
        self.assertEqual((r["accion"], r["retirada"]), ("guardado", True))
        self.assertEqual(len(self.guardadas), 1)
        self.assertEqual(retiros, [("BEKURA", "5002", "5001", 9001)])
        self.assertTrue(any("5002" in a for a in r["avisos"]))

    def test_duplicada_flujo_vivo_usa_retirar_duplicada(self):
        """Sin `guardar` inyectado (el webhook), el retiro es `_retirar_duplicada`."""
        self.dup.return_value = "4999"
        ret = mock.patch.object(dml, "_retirar_duplicada", return_value=0).start()
        fake = FakeML(_claim(tipo="mediations", related=["return"]),
                      returns=(200, _devol(rid=9001)))

        async def _go():
            async with fake.cliente() as cli:
                return await dml.sincronizar("5001", "BEKURA", cli=cli, tokens=self.TOK,
                                             mediaciones=True)
        with mock.patch.object(dml.settings, "devoluciones_ml_mediaciones", True), \
             self.assertLogs(dml.log, "WARNING"):
            r = _run(_go())
        ret.assert_called_once_with("BEKURA", "5001", "4999", 9001)
        self.assertFalse(r["retirada"])

    def _vivo_sin_bandera(self, otro: str, *, propia: bool = False):
        """El flujo vivo (sin `guardar`) con DEVOLUCIONES_ML_MEDIACIONES apagada.
        El refresco relee con `mediaciones=True` aunque la bandera esté apagada:
        el candado mira la BANDERA, no el parámetro. `propia`: este claim ya
        tiene su fila (el upsert va a un colector, no a la base)."""
        self.dup.return_value = otro
        if propia:
            self.propias.return_value = {"5001"}
        mock.patch.object(dml, "_guardar", side_effect=self.guardar).start()
        ret = mock.patch.object(dml, "_retirar_duplicada",
                                side_effect=AssertionError("no se debe borrar")).start()
        fake = FakeML(_claim(tipo="returns"), returns=(200, _devol(rid=9001)))

        async def _go():
            async with fake.cliente() as cli:
                return await dml.sincronizar("5001", "BEKURA", cli=cli, tokens=self.TOK,
                                             mediaciones=True)
        with mock.patch.object(dml.settings, "devoluciones_ml_mediaciones", False), \
             self.assertLogs(dml.log, "WARNING") as logs:
            r = _run(_go())
        ret.assert_not_called()
        return r, logs

    def test_candado_apagado_el_mayor_solo_informa(self):
        """CANDADO DEL DEPLOY: sin la bandera, el único DELETE del flujo vivo no
        corre. `get_cursor` truena en `_Base`: si algo escribiera, ok sería False."""
        r, logs = self._vivo_sin_bandera("4999")
        self.assertEqual((r["ok"], r["accion"], r["decision"], r["retirada"]),
                         (True, "ignorado", "duplicada", False))
        self.assertIn("no se borra ni se escribe", logs.output[0])
        self.assertIn("4999", r["motivo"])
        self.assertEqual(self.guardadas, [])
        self.propias.assert_called_once_with("BEKURA", ["5001"])

    def test_candado_apagado_el_menor_tampoco_escribe(self):
        """Siendo el de menor id, con la bandera encendida se escribiría y se
        retiraría al otro; apagada no se hace ninguna de las dos (una segunda
        fila contaría las piezas dos veces)."""
        r, _ = self._vivo_sin_bandera("5002")
        self.assertEqual((r["ok"], r["decision"], r["retirada"]), (True, "duplicada", False))
        self.assertIn("5002", r["motivo"])
        self.assertEqual(self.guardadas, [])

    def test_candado_apagado_con_fila_propia_la_sigue_actualizando(self):
        """Segunda revisión del 7-oct: si las DOS filas ya existen (de antes del
        candado), contestar 'duplicada' a los dos claims congelaba la devolución
        para siempre. El que ya tiene fila la actualiza; nada se borra, sea el
        mayor o el menor."""
        for otro in ("4999", "5002"):
            with self.subTest(otro=otro):
                self.guardadas.clear()
                r, logs = self._vivo_sin_bandera(otro, propia=True)
                self.assertEqual((r["ok"], r["accion"], r["retirada"]),
                                 (True, "guardado", False))
                self.assertEqual(len(self.guardadas), 1)
                self.assertEqual(self.guardadas[0][0]["external_return_id"], "5001")
                self.assertIn("se actualiza su fila sin borrar la otra", logs.output[0])
                self.assertTrue(any(otro in a and "no se borra" in a for a in r["avisos"]))

    def test_candado_no_aplica_al_script(self):
        """Con `guardar` inyectado (el script, contra el sandbox) decide su
        `retirar`, no la bandera del flujo vivo."""
        self.dup.return_value = "4999"
        escritor, retiros = self._escritor(borradas=1)
        fake = FakeML(_claim(tipo="mediations", related=["return"]),
                      returns=(200, _devol(rid=9001)))

        async def _go():
            async with fake.cliente() as cli:
                return await dml.sincronizar("5001", "BEKURA", cli=cli, tokens=self.TOK,
                                             guardar=escritor, mediaciones=True)
        with mock.patch.object(dml.settings, "devoluciones_ml_mediaciones", False), \
             self.assertLogs(dml.log, "WARNING"):
            r = _run(_go())
        self.assertTrue(r["retirada"])
        self.assertEqual(retiros, [("BEKURA", "5001", "4999", 9001)])

    def test_se_queda_el_menor_id(self):
        self.assertTrue(dml._se_queda("4999", "5001"))
        self.assertFalse(dml._se_queda("5001", "4999"))
        self.assertTrue(dml._se_queda("999", "1000"))          # numérico, no texto
        self.assertFalse(dml._se_queda("1000", "999"))

    def test_retirar_duplicada_sql(self):
        sentencias = []

        class Cur:
            rowcount = 1

            def execute(self, sql, params=None):
                sentencias.append((sql, params))

        @contextlib.contextmanager
        def cursor():
            yield Cur()
        with mock.patch.object(dml.sdb, "get_cursor", side_effect=cursor):
            n = dml._retirar_duplicada("BEKURA", "5001", "4999", 9001)
        self.assertEqual(n, 1)
        sql, params = sentencias[0]
        self.assertIn("delete from channel.returns", sql)
        # Solo si la fila del ganador SIGUE viva con la misma devolución.
        self.assertRegex(sql, r"exists \(select 1 from channel\.returns o")
        self.assertEqual(params, ("mercado_libre", "BEKURA", "5001", "4999", "9001"))

    def test_low_cost_id_0_no_busca_duplicados(self):
        fake = FakeML(_claim(tipo="returns"), returns=(200, _devol(rid=0)))
        self.assertEqual(self.sinc(fake)["accion"], "guardado")
        self.dup.assert_not_called()

    def test_buscar_pide_el_tipo(self):
        vistas = []

        def handler(req):
            vistas.append(dict(req.url.params))
            return httpx.Response(200, json={"data": [{"id": 1}, {"id": 2}],
                                             "paging": {"total": 2}})

        async def _go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cli:
                return await dml._buscar(cli, {}, "2026-10-01", "2026-10-05",
                                         tipo="mediations")
        claims = _run(_go())
        self.assertEqual([c["id"] for c in claims], [1, 2])
        self.assertEqual(vistas[0]["type"], "mediations")
        self.assertIn("date_created:after:2026-10-01", vistas[0]["range"])

    def test_barrer_recorre_returns_y_mediations(self):
        por_tipo = {"returns": [{"id": 11}], "mediations": [{"id": 22}, {"id": 11}]}
        tipos_pedidos = []

        def handler(req):
            tipo = req.url.params["type"]
            tipos_pedidos.append(tipo)
            data = por_tipo[tipo]
            return httpx.Response(200, json={"data": data, "paging": {"total": len(data)}})

        sincronizados = []

        async def falso_sinc(cid, cuenta, **kw):
            sincronizados.append((cid, cuenta, kw["mediaciones"], kw["tokens"] is self.TOK))
            return {"ok": True, "accion": "guardado"}

        async def _go(mediaciones):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cli:
                return await dml.barrer(dias=2, cuentas=("BEKURA",), cli=cli,
                                        tokens=self.TOK, guardar=self.guardar,
                                        mediaciones=mediaciones)
        with mock.patch.object(dml, "sincronizar", side_effect=falso_sinc):
            res = _run(_go(True))
        self.assertEqual(tipos_pedidos, ["returns", "mediations"])
        self.assertEqual(sorted(s[0] for s in sincronizados), ["11", "22"])   # sin repetir
        self.assertTrue(all(s[2] and s[3] for s in sincronizados))
        self.assertEqual(res["cuentas"]["BEKURA"]["por_tipo"], {"returns": 1, "mediations": 2})
        self.assertEqual(res["guardados"], 2)

        tipos_pedidos.clear()
        with mock.patch.object(dml, "sincronizar", side_effect=falso_sinc):
            _run(_go(False))
        self.assertEqual(tipos_pedidos, ["returns"])

    def test_barrer_omite_los_que_ya_tienen_fila(self):
        def handler(req):
            data = [{"id": 31}, {"id": 32}, {"id": 33}]
            return httpx.Response(200, json={"data": data, "paging": {"total": 3}})
        ya = mock.patch.object(dml, "_ya_guardados", return_value={"32"}).start()
        sinc = mock.patch.object(dml, "sincronizar", new=mock.AsyncMock(
            return_value={"ok": True, "accion": "ignorado", "decision": "esperar"})).start()

        async def _go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cli:
                return await dml.barrer(dias=21, cuentas=("BEKURA",), tipos=("mediations",),
                                        cli=cli, tokens=self.TOK, mediaciones=True,
                                        omitir_guardados=True)
        res = _run(_go())
        ya.assert_called_once_with("BEKURA", ["31", "32", "33"])
        self.assertEqual(sorted(c.args[0] for c in sinc.await_args_list), ["31", "33"])
        self.assertEqual((res["cuentas"]["BEKURA"]["omitidos"],
                          res["cuentas"]["BEKURA"]["esperando"]), (1, 2))

    def test_barrer_por_omision_no_consulta_guardados(self):
        def handler(req):
            return httpx.Response(200, json={"data": [{"id": 31}], "paging": {"total": 1}})
        ya = mock.patch.object(dml, "_ya_guardados").start()
        mock.patch.object(dml, "sincronizar", new=mock.AsyncMock(
            return_value={"ok": True, "accion": "guardado"})).start()

        async def _go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cli:
                return await dml.barrer(dias=2, cuentas=("BEKURA",), cli=cli,
                                        tokens=self.TOK, mediaciones=False)
        _run(_go())
        ya.assert_not_called()

    def _amplio(self, *, enabled=True, med=True, dias=21):
        barrer = mock.patch.object(dml, "barrer",
                                   new=mock.AsyncMock(return_value={"guardados": 0})).start()
        with mock.patch.object(dml.settings, "devoluciones_ml_enabled", enabled), \
             mock.patch.object(dml.settings, "devoluciones_ml_mediaciones", med), \
             mock.patch.object(dml.settings, "devoluciones_ml_mediaciones_amplio_dias", dias):
            res = _run(dml.barrer_mediaciones_amplio())
        return barrer, res

    def test_barrido_amplio_solo_mediaciones_sin_fila(self):
        barrer, _ = self._amplio(dias=21)
        barrer.assert_awaited_once()
        kw = barrer.await_args.kwargs
        self.assertEqual((kw["dias"], kw["tipos"], kw["mediaciones"], kw["omitir_guardados"]),
                         (21, ("mediations",), True, True))
        self.assertLessEqual(kw["concurrencia"], 2)

    def test_barrido_amplio_tope_de_dias(self):
        barrer, _ = self._amplio(dias=90)
        self.assertEqual(barrer.await_args.kwargs["dias"], dml._AMPLIO_MAX_DIAS)

    def test_barrido_amplio_apagado(self):
        for kw in ({"dias": 0}, {"med": False}, {"enabled": False}):
            barrer, res = self._amplio(**kw)       # un mock nuevo por caso
            barrer.assert_not_awaited()
            self.assertEqual(res, {"ok": False, "motivo": "apagado"}, kw)

    def test_backfill_retirado_aborta_sin_importar_el_backend(self):
        ruta = BACKEND / "scripts" / "backfill_devoluciones_ml.py"
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        importados = set()
        for n in ast.walk(arbol):
            if isinstance(n, ast.Import):
                importados |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                importados.add((n.module or "").split(".")[0])
        # Ni config, ni services (meli, db), ni httpx: no puede leer MySQL ni ML.
        self.assertLessEqual(importados, {"__future__", "sys"})
        spec = importlib.util.spec_from_file_location("backfill_retirado", ruta)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            for argv in ([], ["--aplicar"], ["--cuenta", "BEKURA"]):
                self.assertEqual(mod.main(argv), 2, argv)
        self.assertIn("recuperar_devoluciones_ml.py", err.getvalue())


# ── F2 · el cliente HTTP vive hasta el motivo ────────────────────────────────

class F2Cliente(_Base):
    def _con_cliente_propio(self, fake):
        creados: list[httpx.AsyncClient] = []

        def fabrica():
            c = fake.cliente()
            creados.append(c)
            return c
        return mock.patch.object(dml, "_cliente", side_effect=fabrica), creados

    def test_webhook_sin_cliente_guarda_con_motivo(self):
        """LA REGRESIÓN: con cli=None y el motivo fuera de caché, hasta v0.625.0
        esto daba «Cannot send a request, as the client has been closed» y la
        devolución no se guardaba."""
        fake = FakeML(_claim(), returns=(200, _devol()))
        parche, creados = self._con_cliente_propio(fake)
        with parche:
            r = _run(dml.sincronizar("5001", "BEKURA", detectado_via="webhook",
                                     tokens=self.TOK, guardar=self.guardar))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["accion"], "guardado")
        cab, _ = self.guardadas[0]
        self.assertEqual(cab["motivo_texto"], "Llegó lo que compré pero no lo quiero")
        self.assertEqual(fake.contar(r"/claims/reasons/PDD9939$"), 1)
        self.assertEqual(len(creados), 1)
        self.assertTrue(creados[0].is_closed)

    def test_cliente_propio_se_cierra_en_todos_los_caminos(self):
        casos = {
            "guardado": FakeML(_claim(), returns=(200, _devol())),
            "esperar": FakeML(_claim(tipo="mediations"), returns=(404, {})),
            "descartar": FakeML(_claim(tipo="cancel_purchase"), returns=(404, {})),
            "no existe": FakeML(None),
        }
        for nombre, fake in casos.items():
            dml._NO_DEVOLUCION.clear()
            parche, creados = self._con_cliente_propio(fake)
            with parche:
                _run(dml.sincronizar("5001", "BEKURA", tokens=self.TOK,
                                     guardar=self.guardar, mediaciones=True))
            self.assertEqual(len(creados), 1, nombre)
            self.assertTrue(creados[0].is_closed, nombre)

        # Y con una excepción a media lectura.
        def revienta(req):
            raise httpx.ConnectError("sin red", request=req)
        creados = []

        def fabrica():
            c = httpx.AsyncClient(transport=httpx.MockTransport(revienta))
            creados.append(c)
            return c
        with mock.patch.object(dml, "_cliente", side_effect=fabrica),              self.assertLogs(dml.log, "ERROR"):
            r = _run(dml.sincronizar("5001", "BEKURA", tokens=self.TOK,
                                     guardar=self.guardar))
        self.assertFalse(r["ok"])
        self.assertTrue(creados[0].is_closed)

    def test_cliente_ajeno_no_se_cierra(self):
        fake = FakeML(_claim(), returns=(200, _devol()))

        async def _go():
            cli = fake.cliente()
            r = await dml.sincronizar("5001", "BEKURA", cli=cli, tokens=self.TOK,
                                      guardar=self.guardar)
            cerrado = cli.is_closed
            await cli.aclose()
            return r, cerrado
        r, cerrado = _run(_go())
        self.assertEqual(r["accion"], "guardado")
        self.assertFalse(cerrado)

    def test_motivo_de_tolera_error_de_transporte(self):
        def revienta(req):
            raise httpx.ConnectError("sin red", request=req)

        async def _go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(revienta)) as cli:
                return await dml.motivo_de(cli, {}, "PDD1234")
        self.assertIsNone(_run(_go()))
        self.assertNotIn("PDD1234", dml._MOTIVOS)

    def test_motivo_caido_no_borra_el_texto_guardado(self):
        """El catálogo no contesta (timeout) y el reclamo ya cerró (sin
        `/detail.problem`): la lectura trae `motivo_texto` en None. El upsert
        no lo escribe encima del texto guardado: coalesce."""
        fake = FakeML(_claim(status="closed"), returns=(200, _devol(status="delivered")),
                      detalle=(200, {"title": "Reclamo cerrado"}))

        def ml(req):
            if "/claims/reasons/" in req.url.path:
                raise httpx.ReadTimeout("lento", request=req)
            return fake(req)

        async def _go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(ml)) as cli:
                return await dml.sincronizar("5001", "BEKURA", cli=cli, tokens=self.TOK,
                                             guardar=self.guardar)
        r = _run(_go())
        self.assertEqual(r["accion"], "guardado")
        cab, _ = self.guardadas[0]
        self.assertIsNone(cab["motivo_texto"])
        self.assertNotIn("PDD9939", dml._MOTIVOS)        # se reintenta la próxima vez
        self.assertEqual(dml._set_de("motivo_texto"),
                         "motivo_texto = coalesce(excluded.motivo_texto, "
                         "channel.returns.motivo_texto)")

    def test_motivo_de_no_tapa_un_cliente_cerrado(self):
        async def _go():
            cli = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
            await cli.aclose()
            return await dml.motivo_de(cli, {}, "PDD1234")
        with self.assertRaises(RuntimeError):
            _run(_go())


# ── F3 · refresco de lo que sigue abierto ────────────────────────────────────

class F3Refresco(_Base):
    def test_necesita_refresco_tabla(self):
        casos = [
            ("abierta", None, True), ("en_transito", "shipped", True),
            ("recibida", "delivered", True), ("cerrada", "closed", False),
            ("rechazada", "expired", False), ("rechazada", "cancelled", False),
            ("reembolsada", "shipped", True), ("reembolsada", "label_generated", True),
            ("reembolsada", "pending", True), ("reembolsada", "in_transit", True),
            ("reembolsada", "delivered", False), ("reembolsada", "closed", False),
            ("reembolsada", None, False),
        ]
        for estado, canal, esperado in casos:
            self.assertEqual(dml.necesita_refresco(estado, canal), esperado, (estado, canal))

    def test_candidatas_usa_las_mismas_constantes(self):
        capturado = {}

        def falso(sql, params):
            capturado["sql"], capturado["params"] = sql, params
            return []
        with mock.patch.object(dml.sdb, "fetch_all", side_effect=falso):
            dml._candidatas(40, 6, 120, excluir=["BEKURA:1"])
        sql, p = capturado["sql"], capturado["params"]
        self.assertIn(list(dml._ABIERTOS), p)
        self.assertIn(list(dml._EN_CAMINO), p)
        self.assertEqual(p[-1], 40)
        self.assertIn(["BEKURA:1"], p)
        self.assertRegex(sql, r"order by actualizado_at\s+limit %s")
        self.assertIn("make_interval(hours => %s)", sql)
        self.assertIn("make_interval(days => %s)", sql)

    def _filas(self, n):
        return [{"cuenta": "BEKURA", "external_return_id": str(100 + i)} for i in range(n)]

    def test_refrescar_respeta_el_tope(self):
        filas = self._filas(3)
        cand = mock.patch.object(dml, "_candidatas",
                                 side_effect=lambda tope, h, d, **k: filas[:tope]).start()
        sinc = mock.patch.object(dml, "sincronizar",
                                 new=mock.AsyncMock(return_value={"ok": True,
                                                                  "accion": "guardado"})).start()
        res = _run(dml.refrescar_abiertas(tope=2, cli=object(),
                                          tokens=self.TOK, guardar=self.guardar))
        self.assertEqual(sinc.await_count, 2)
        self.assertEqual(cand.call_args.args[0], 2)
        self.assertEqual((res["candidatas"], res["guardados"]), (2, 2))

    def test_refrescar_usa_detectado_via_sondeo(self):
        mock.patch.object(dml, "_candidatas", return_value=self._filas(1)).start()
        sinc = mock.patch.object(dml, "sincronizar",
                                 new=mock.AsyncMock(return_value={"ok": True,
                                                                  "accion": "guardado"})).start()
        cli = object()
        _run(dml.refrescar_abiertas(tope=5, cli=cli, tokens=self.TOK,
                                    guardar=self.guardar))
        args, kw = sinc.await_args
        self.assertEqual(args, ("100", "BEKURA"))
        self.assertEqual(kw["detectado_via"], "sondeo")
        self.assertIs(kw["cli"], cli)
        self.assertIs(kw["tokens"], self.TOK)
        self.assertTrue(kw["mediaciones"])        # la fila ya existe: ya se decidió

    def test_refrescar_no_repite_lo_que_no_pudo_escribir(self):
        cand = mock.patch.object(dml, "_candidatas", return_value=self._filas(2)).start()
        mock.patch.object(dml, "sincronizar", new=mock.AsyncMock(side_effect=[
            {"ok": True, "accion": "conservado"}, {"ok": True, "accion": "guardado"},
        ])).start()
        _run(dml.refrescar_abiertas(tope=5, horas=6, cli=object(),
                                    tokens=self.TOK))
        self.assertEqual(cand.call_args.kwargs["excluir"], [])
        mock.patch.object(dml, "sincronizar",
                          new=mock.AsyncMock(return_value={"ok": True,
                                                           "accion": "guardado"})).start()
        _run(dml.refrescar_abiertas(tope=5, horas=6, cli=object(),
                                    tokens=self.TOK))
        self.assertEqual(cand.call_args.kwargs["excluir"], ["BEKURA:100"])
        # Con horas=0 (el script) no se excluye nada.
        _run(dml.refrescar_abiertas(tope=5, horas=0, cli=object(),
                                    tokens=self.TOK))
        self.assertEqual(cand.call_args.kwargs["excluir"], [])

    def test_refrescar_sin_candidatas_no_abre_cliente(self):
        mock.patch.object(dml, "_candidatas", return_value=[]).start()
        fab = mock.patch.object(dml, "_cliente").start()
        res = _run(dml.refrescar_abiertas(tope=40))
        fab.assert_not_called()
        self.assertEqual(res["candidatas"], 0)

    def test_refrescar_cliente_propio_lleva_ritmo(self):
        mock.patch.object(dml, "_candidatas", return_value=self._filas(1)).start()
        mock.patch.object(dml, "sincronizar", new=mock.AsyncMock(
            return_value={"ok": True, "accion": "guardado"})).start()
        fab = mock.patch.object(dml, "_cliente",
                                side_effect=lambda **kw: httpx.AsyncClient()).start()
        with mock.patch.object(dml.settings, "devoluciones_ml_ritmo", 1.5):
            _run(dml.refrescar_abiertas(tope=40, tokens=self.TOK))
        fab.assert_called_once_with(ritmo=1.5)

    def test_cliente_con_ritmo_usa_el_transporte(self):
        async def _go():
            async with dml._cliente(ritmo=2.0) as cli:
                return cli._transport
        self.assertIsInstance(_run(_go()), dml._Ritmo)

    def test_ritmo_espacia_cada_get_no_cada_reclamo(self):
        """La cota «a lo más 2 GET/s» es por GET: cuatro GET seguidos de un
        mismo reclamo (claim, /returns, /detail, motivo) también se espacian."""
        reloj = [100.0]
        dormidas = []

        async def dormir(s):
            dormidas.append(round(s, 6))
            reloj[0] += s
        t = dml._Ritmo(httpx.MockTransport(lambda r: httpx.Response(200, json={})), 2.0,
                       reloj=lambda: reloj[0], dormir=dormir)

        async def _go():
            async with httpx.AsyncClient(transport=t) as cli:
                for ruta in ("/claims/1", "/claims/1/returns", "/claims/1/detail",
                             "/claims/reasons/PDD1"):
                    await cli.get(f"{ML}/post-purchase/v1{ruta}")
        _run(_go())
        self.assertEqual(dormidas, [0.5, 0.5, 0.5])

    def _revisar(self, tope, recalculo=True):
        barrer = mock.patch.object(dml, "barrer",
                                   new=mock.AsyncMock(return_value={"guardados": 0})).start()
        refr = mock.patch.object(dml, "refrescar_abiertas",
                                 new=mock.AsyncMock(return_value={"candidatas": 0})).start()
        self.avisos = mock.patch.object(dml, "reintentar_avisos", new=mock.AsyncMock(
            return_value={"reintentados": 0})).start()
        self.hilos_recalc: list[int] = []
        self.recalc = mock.patch.object(
            dml, "recalcular_venta_contaba",
            side_effect=lambda: (self.hilos_recalc.append(threading.get_ident()), 3)[1]
        ).start()
        with mock.patch.object(dml.settings, "devoluciones_ml_enabled", True), \
             mock.patch.object(dml.settings, "devoluciones_ml_recalculo", recalculo), \
             mock.patch.object(dml.settings, "devoluciones_ml_refresco_tope", tope), \
             mock.patch.object(dml.settings, "devoluciones_ml_refresco_horas", 6), \
             mock.patch.object(dml.settings, "devoluciones_ml_refresco_dias", 120):
            res = _run(dml.revisar())
        return barrer, refr, res

    def test_revisar_refresca_si_hay_tope(self):
        barrer, refr, res = self._revisar(40)
        barrer.assert_awaited_once()
        refr.assert_awaited_once_with(tope=40, horas=6, max_dias=120, cli=mock.ANY)
        self.assertIn("refresco", res)
        # Barrido, refresco y avisos comparten UN cliente frenado: el freno no
        # se reinicia justo donde empieza la ráfaga del refresco.
        cli = barrer.await_args.kwargs["cli"]
        self.assertIs(refr.await_args.kwargs["cli"], cli)
        self.assertIs(self.avisos.await_args.kwargs["cli"], cli)
        self.assertIsInstance(cli._transport, dml._Ritmo)
        self.assertEqual(res["venta_contaba"], 3)

    def test_revisar_recalcula_fuera_del_loop(self):
        """Regla 11: el UPDATE es psycopg2 síncrono; llamado directo congelaría
        el backend entero cada hora. Debe correr en otro hilo (to_thread)."""
        self._revisar(0)
        self.recalc.assert_called_once_with()
        self.assertEqual(len(self.hilos_recalc), 1)
        self.assertNotEqual(self.hilos_recalc[0], threading.get_ident())

    def test_el_recalculo_nace_apagado(self):
        """Cambia el «restable» de Rentabilidad: nace apagado y se enciende con
        el dale de Brandon (Eduardo, 8-oct; regla 3)."""
        from config import Settings
        self.assertFalse(Settings.model_fields["devoluciones_ml_recalculo"].default)

    def test_revisar_con_recalculo_apagado_no_lo_corre(self):
        _, _, res = self._revisar(0, recalculo=False)
        self.recalc.assert_not_called()
        self.assertEqual(res["venta_contaba"], {"ok": False, "motivo": "apagado"})

    def test_revisar_sigue_si_avisos_y_recalculo_truenan(self):
        """Cada paso aunque el anterior truene: un fallo del reintento de avisos
        no se salta el recálculo, y un fallo del recálculo no tumba el job."""
        mock.patch.object(dml, "barrer", new=mock.AsyncMock(
            return_value={"guardados": 0})).start()
        avisos = mock.patch.object(dml, "reintentar_avisos", new=mock.AsyncMock(
            side_effect=RuntimeError("bitácora caída"))).start()
        recalc = mock.patch.object(dml, "recalcular_venta_contaba",
                                   side_effect=RuntimeError("deadlock")).start()
        with mock.patch.object(dml.settings, "devoluciones_ml_enabled", True), \
             mock.patch.object(dml.settings, "devoluciones_ml_recalculo", True), \
             mock.patch.object(dml.settings, "devoluciones_ml_refresco_tope", 0), \
             self.assertLogs(dml.log, "ERROR") as logs:
            res = _run(dml.revisar())
        avisos.assert_awaited_once()
        recalc.assert_called_once_with()
        self.assertEqual(res["avisos"], {"ok": False, "motivo": "bitácora caída"})
        self.assertEqual(res["venta_contaba"], {"ok": False, "motivo": "deadlock"})
        self.assertEqual(len([l for l in logs.output if l.startswith("ERROR")]), 2)

    def test_revisar_sigue_si_el_barrido_truena(self):
        """Una excepción del barrido ya no se salta el resto de la hora."""
        barrer = mock.patch.object(dml, "barrer", new=mock.AsyncMock(
            side_effect=RuntimeError("ML caído"))).start()
        avisos = mock.patch.object(dml, "reintentar_avisos", new=mock.AsyncMock(
            return_value={"reintentados": 0})).start()
        recalc = mock.patch.object(dml, "recalcular_venta_contaba", return_value=0).start()
        with mock.patch.object(dml.settings, "devoluciones_ml_enabled", True), \
             mock.patch.object(dml.settings, "devoluciones_ml_recalculo", True), \
             mock.patch.object(dml.settings, "devoluciones_ml_refresco_tope", 0), \
             self.assertLogs(dml.log, "ERROR"):
            res = _run(dml.revisar())
        barrer.assert_awaited_once()
        avisos.assert_awaited_once()
        recalc.assert_called_once_with()
        self.assertFalse(res["ok"])
        self.assertIn("ML caído", res["motivo"])

    def test_revisar_no_refresca_con_tope_cero(self):
        barrer, refr, res = self._revisar(0)
        barrer.assert_awaited_once()
        refr.assert_not_awaited()
        self.assertNotIn("refresco", res)

    def test_returns_error_no_degrada_una_fila_buena(self):
        self.previa.return_value = True
        fake = FakeML(_claim(),
                      returns=(401, b'{"message":"Error executing GET [client:shipments]"}'))
        with self.assertLogs(dml.log, "WARNING") as logs:
            r = self.sinc(fake)
        self.assertIn("se conserva sin escribir", logs.output[-1])
        self.assertEqual((r["ok"], r["accion"]), (True, "conservado"))
        self.assertEqual(self.guardadas, [])
        self.previa.assert_called_once_with("BEKURA", "5001")

    def test_returns_error_sin_fila_previa_se_guarda(self):
        fake = FakeML(_claim(),
                      returns=(401, b'{"message":"Error executing GET [client:shipments]"}'))
        with self.assertLogs(dml.log, "WARNING"):
            self.assertEqual(self.sinc(fake)["accion"], "guardado")


# ── F4 · expired ─────────────────────────────────────────────────────────────

def _armar(devol: dict, claim: dict | None = None):
    crudo = {"claim": claim or _claim(), "returns": devol, "detalle": {}}
    return dml.armar(crudo, "BEKURA", lineas_pedido=[_linea(OID)], account_id="acc-1",
                     detectado_via="sondeo")


class F4Expired(unittest.TestCase):
    def test_expired_es_rechazada(self):
        cab, _, avisos = _armar(_devol(status="expired", money="retained"))
        self.assertEqual((cab["estado"], cab["estado_canal"]), ("rechazada", "expired"))
        self.assertFalse(any("desconocido" in a for a in avisos))

    def test_expired_con_reembolso_es_reembolsada(self):
        cab, _, _ = _armar(_devol(status="expired", money="refunded"))
        self.assertEqual(cab["estado"], "reembolsada")
        self.assertIsNotNone(cab["reembolsada_at"])

    def test_estado_desconocido_abierta_con_aviso(self):
        cab, _, avisos = _armar(_devol(status="estado_nuevo_de_ml"))
        self.assertEqual(cab["estado"], "abierta")
        self.assertIn("5001: estado de ML desconocido 'estado_nuevo_de_ml' → abierta", avisos)

    def test_el_mapa_cabe_en_el_check(self):
        sql = MIGRACION_0049.read_text(encoding="utf-8")
        m = re.search(r"ck_returns_estado\s+check\s*\(estado\s+in\s*\(([^)]*)\)", sql,
                      re.IGNORECASE | re.DOTALL)
        self.assertIsNotNone(m, "no encontré ck_returns_estado en la 0049")
        permitidos = set(re.findall(r"'([a-z_]+)'", m.group(1)))
        usados = set(dml._ESTADO.values()) | {"reembolsada", "abierta", "cerrada"}
        self.assertLessEqual(usados, permitidos)


# ── F5 · destino ─────────────────────────────────────────────────────────────

class F5Destino(unittest.TestCase):
    def test_destino_seller_address(self):
        self.assertEqual(dml._destino(_devol(destinos=("seller_address",))), "seller_address")

    def test_destino_warehouse(self):
        self.assertEqual(dml._destino(_devol(destinos=("warehouse",))), "warehouse")

    def test_destino_varios_tramos_gana_nuestra_puerta(self):
        self.assertEqual(dml._destino(_devol(destinos=("warehouse", "seller_address"))),
                         "seller_address")
        self.assertEqual(dml._destino(_devol(destinos=("seller_address", "warehouse"))),
                         "seller_address")
        self.assertEqual(dml._destino(_devol(destinos=("otro", "warehouse"))), "warehouse")

    def test_destino_sin_envios_es_none(self):
        self.assertIsNone(dml._destino({}))
        self.assertIsNone(dml._destino({"shipments": []}))
        self.assertIsNone(dml._destino({"shipments": None}))

    def test_destino_sin_name_es_none(self):
        self.assertIsNone(dml._destino({"shipments": [{"destination": {}}, {"type": "return"},
                                                      None]}))

    def test_destino_solo_toma_el_nombre(self):
        devol = {"shipments": [{"destination": {"name": "seller_address",
                                                "shipping_address": {"street": "x"}}}]}
        self.assertEqual(dml._destino(devol), "seller_address")

    def test_armar_llena_destino(self):
        cab, _, _ = _armar(_devol(destinos=("warehouse",)))
        self.assertEqual(cab["destino"], "warehouse")
        cab, _, _ = _armar({})
        self.assertIsNone(cab["destino"])

    def test_guardar_no_borra_destino_conocido(self):
        sentencias = []

        class Cur:
            def execute(self, sql, params=None):
                sentencias.append(sql)

        @contextlib.contextmanager
        def cursor():
            yield Cur()
        cab, lineas, _ = _armar(_devol())
        with mock.patch.object(dml.sdb, "get_cursor", side_effect=cursor):
            dml._guardar(cab, lineas)
        upsert = sentencias[0]
        self.assertIn("destino = coalesce(excluded.destino, channel.returns.destino)", upsert)
        self.assertIn("motivo_texto = coalesce(excluded.motivo_texto, "
                      "channel.returns.motivo_texto)", upsert)
        # Son los ÚNICOS coalesce (atributos estables): los ESTADOS se pisan
        # como siempre, también cuando la lectura nueva no los trae.
        self.assertEqual(upsert.count("coalesce("), 2)
        self.assertIn("estado = excluded.estado", upsert)
        self.assertIn("estado_titulo = excluded.estado_titulo", upsert)
        self.assertNotIn("abierta_at = excluded", upsert)


# ── Revisión del 7-oct: lo que entra con v0.626.0 ────────────────────────────

class FakeML429(FakeML):
    """Un ML que contesta 429 las primeras `n` veces a las rutas que casan con
    cada patrón de `fallas` (n grande = siempre)."""

    def __init__(self, *a, fallas: dict[str, int], **kw):
        super().__init__(*a, **kw)
        self.fallas = dict(fallas)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        for patron, n in self.fallas.items():
            if n > 0 and re.search(patron, req.url.path):
                self.fallas[patron] = n - 1
                self.pedidas.append(req)
                return httpx.Response(429, json={"message": "too_many_requests"})
        return super().__call__(req)


class Limite429(_Base):
    """La falla más frecuente medida (ML contesta 429 por debajo de 2 GET/s) no
    tenía ni una prueba. `asyncio.sleep` va parchado: los reintentos no esperan."""

    def setUp(self):
        super().setUp()
        self.dormir = mock.patch.object(dml.asyncio, "sleep", new=mock.AsyncMock()).start()

    def test_returns_429_cuatro_veces_no_guarda(self):
        fake = FakeML429(_claim(), returns=(200, _devol()), fallas={r"/returns$": 99})
        with self.assertLogs(dml.log, "ERROR"):
            r = self.sinc(fake)
        self.assertFalse(r["ok"])
        self.assertIn("429", r["motivo"])
        self.assertEqual(fake.contar(r"/returns$"), 4)        # 1 + 3 reintentos
        self.assertEqual(self.guardadas, [])                    # 429 no es «sin envío»

    def test_claim_429_no_guarda(self):
        fake = FakeML429(_claim(), returns=(200, _devol()), fallas={r"/claims/\d+$": 99})
        with self.assertLogs(dml.log, "ERROR"):
            r = self.sinc(fake)
        self.assertFalse(r["ok"])
        self.assertEqual(fake.contar(r"/returns$"), 0)
        self.assertEqual(self.guardadas, [])

    def test_429_y_luego_200_guarda(self):
        fake = FakeML429(_claim(), returns=(200, _devol()), fallas={r"/returns$": 1})
        r = self.sinc(fake)
        self.assertEqual((r["ok"], r["accion"]), (True, "guardado"))
        self.assertEqual(len(self.guardadas), 1)
        self.assertEqual(self.guardadas[0][0]["estado_dinero"], "retained")
        self.dormir.assert_awaited()                            # sí esperó antes de reintentar

    def _busqueda_cortada(self, status: int):
        def handler(req):
            if int(req.url.params["offset"]) == 0:
                data = [{"id": 100 + i} for i in range(50)]
                return httpx.Response(200, json={"data": data, "paging": {"total": 80}})
            return httpx.Response(status, json={})
        sinc = mock.patch.object(dml, "sincronizar", new=mock.AsyncMock(
            return_value={"ok": True, "accion": "guardado"})).start()

        async def _go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cli:
                return await dml.barrer(dias=2, cuentas=("BEKURA",), cli=cli,
                                        tokens=self.TOK, mediaciones=False)
        with self.assertLogs(dml.log, "WARNING") as logs:
            res = _run(_go())
        self.assertTrue(any("INCOMPLETA" in l for l in logs.output))
        self.assertEqual(sinc.await_count, 50)                  # lo leído sí se procesa
        self.assertEqual(res["busquedas_incompletas"], 1)
        self.assertEqual(res["fallos"], 1)
        cuenta = res["cuentas"]["BEKURA"]
        self.assertEqual(len(cuenta["busqueda_incompleta"]), 1)
        self.assertIn(str(status), cuenta["motivos"][0])

    def test_busqueda_cortada_en_la_pagina_2_es_fallo(self):
        """Hasta v0.625.0: `break` en silencio y «0 fallos» con media ventana leída."""
        self._busqueda_cortada(429)

    def test_busqueda_con_403_en_la_pagina_2_es_fallo(self):
        """Un status que `_pedir` no convierte en excepción (el token caído del
        18-sep daba 401/403) también es una búsqueda incompleta, no un final."""
        for status in (403, 401, 400):
            with self.subTest(status=status):
                self._busqueda_cortada(status)

    def test_busqueda_completa_no_marca_nada(self):
        marcas: dict = {}

        def handler(req):
            return httpx.Response(200, json={"data": [{"id": 1}], "paging": {"total": 1}})

        async def _go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cli:
                return await dml._buscar(cli, {}, "2026-10-01", "2026-10-05", marcas=marcas)
        self.assertEqual(len(_run(_go())), 1)
        self.assertEqual(marcas, {})


class Ritmo(_Base):
    def test_ritmo_por_omision_e_invalidos(self):
        for valor, esperado in ((1.5, 1.5), (0.8, 0.8), (0, 1.5), (-2, 1.5), ("x", 1.5)):
            with mock.patch.object(dml.settings, "devoluciones_ml_ritmo", valor):
                self.assertEqual(dml._ritmo(), esperado, valor)

    def test_barrer_cliente_propio_lleva_ritmo(self):
        fab = mock.patch.object(dml, "_cliente",
                                side_effect=lambda **kw: httpx.AsyncClient()).start()
        with mock.patch.object(dml.settings, "devoluciones_ml_ritmo", 1.5):
            _run(dml.barrer(dias=2, cuentas=(), tokens=self.TOK, mediaciones=False))
        fab.assert_called_once_with(ritmo=1.5)


class VentaContaba(unittest.TestCase):
    """La sexta falla: `venta_contaba` se congelaba con el estado de la orden al
    capturar; ML cancela la orden al reembolsar y el «restable» contaba doble."""

    def test_cancelados_igual_que_la_0030(self):
        sql = (BACKEND.parent / "supabase" / "migrations"
               / "0030_sales_daily_cancelado_sin_caja.sql").read_text(encoding="utf-8")
        m = re.search(r"lower\(coalesce\(o\.estado_canal, ''\)\) not in \(([^)]*)\)", sql)
        self.assertIsNotNone(m)
        self.assertEqual(set(re.findall(r"'([^']*)'", m.group(1))), set(dml._CANCELADOS))

    def test_armar_con_y_sin_orden(self):
        crudo = {"claim": _claim(), "returns": _devol(), "detalle": {}}
        for estado, esperado in (("paid", True), ("cancelled", False),
                                 ("Cancelled", False), ("invalid", False), ("", True)):
            linea = dict(_linea(OID), estado_canal=estado)
            cab, _, _ = dml.armar(crudo, "BEKURA", lineas_pedido=[linea],
                                  account_id="acc-1", detectado_via="webhook")
            self.assertIs(cab["venta_contaba"], esperado, estado)
        cab, _, _ = dml.armar(crudo, "BEKURA", lineas_pedido=[], account_id="acc-1",
                              detectado_via="webhook")
        self.assertIsNone(cab["venta_contaba"])

    def _recalcular(self, rowcount, filas=()):
        sentencias = []

        class Cur:
            description = None

            def execute(self, sql, params=None):
                sentencias.append((sql, params))
                if filas:
                    self.description = [("cuenta",)]

            def fetchall(self):
                return list(filas)
        cur = Cur()
        cur.rowcount = rowcount

        @contextlib.contextmanager
        def cursor():
            yield cur
        with mock.patch.object(dml.sdb, "get_cursor", side_effect=cursor):
            n = dml.recalcular_venta_contaba()
        return n, sentencias

    def test_recalcular_un_solo_update_solo_lo_que_cambia(self):
        n, sentencias = self._recalcular(7)
        self.assertEqual(n, 7)
        self.assertEqual(len(sentencias), 1)
        sql, params = sentencias[0]
        plano = " ".join(sql.split())
        self.assertIn("r.venta_contaba as antes, not (lower(coalesce(o.estado_canal, '')) "
                      "= any(%s)) as despues from channel.returns r join channel.orders o",
                      plano)
        self.assertIn("update channel.returns r set venta_contaba = c.despues from cambio c "
                      "where r.canal = %s and r.cuenta = c.cuenta "
                      "and r.external_return_id = c.external_return_id", plano)
        self.assertIn("returning r.cuenta, r.external_return_id, c.antes, c.despues", plano)
        # La orden por su llave completa, solo Mercado Libre, y solo si cambia.
        for trozo in ("where r.canal = %s", "o.canal = r.canal", "o.cuenta = r.cuenta",
                      "o.external_order_id = r.external_order_id",
                      "r.venta_contaba is distinct from"):
            self.assertIn(trozo, plano)
        self.assertEqual(params, (list(dml._CANCELADOS), "mercado_libre",
                                  list(dml._CANCELADOS), "mercado_libre"))
        # Solo esa columna: ni estado (historia) ni abierta_at.
        set_ = plano.split(" set ", 1)[1].split(" from ", 1)[0]
        self.assertTrue(set_.startswith("venta_contaba = "), set_)
        self.assertNotRegex(set_, r",\s*\w+\s*=")

    def test_recalcular_sin_cambios_devuelve_cero(self):
        with self.assertNoLogs(dml.log, "INFO"):
            self.assertEqual(self._recalcular(None)[0], 0)

    def test_recalcular_deja_el_valor_de_antes_en_el_log(self):
        """Ni la historia ni la fila guardan el valor anterior: el log sí."""
        filas = [{"cuenta": "BEKURA", "external_return_id": "5001",
                  "antes": True, "despues": False},
                 {"cuenta": "SANCORFASHION", "external_return_id": "5002",
                  "antes": None, "despues": True}]
        with self.assertLogs(dml.log, "INFO") as logs:
            n, _ = self._recalcular(2, filas)
        self.assertEqual(n, 2)
        self.assertIn("BEKURA/5001: True→False", logs.output[0])
        self.assertIn("SANCORFASHION/5002: None→True", logs.output[0])

    def test_recalcular_log_con_tope(self):
        filas = [{"cuenta": "BEKURA", "external_return_id": str(i),
                  "antes": True, "despues": False}
                 for i in range(dml._RECALCULO_LOG_MAX + 7)]
        with self.assertLogs(dml.log, "INFO") as logs:
            self._recalcular(len(filas), filas)
        self.assertIn("… y 7 más", logs.output[0])
        self.assertNotIn(f"BEKURA/{dml._RECALCULO_LOG_MAX}:", logs.output[0])

    def test_el_trigger_de_historia_no_ve_este_update(self):
        """`returns_history` solo escribe si cambia `estado`; si alguien lo
        ampliara, el recálculo empezaría a meter historia falsa."""
        sql = MIGRACION_0049.read_text(encoding="utf-8")
        cuerpo = sql.split("function channel.fn_return_history()", 1)[1].split("end $$", 1)[0]
        self.assertIn("old.estado is distinct from new.estado", cuerpo)
        self.assertNotIn("venta_contaba", cuerpo)


class AvisosFallidos(_Base):
    """Los avisos post_purchase que fallaban se marcaban procesados y nadie los
    reintentaba (el reproceso de `ml_webhook_reintentos` es solo de ventas)."""

    def setUp(self):
        super().setUp()
        dml._REINTENTADOS.clear()
        self.addCleanup(dml._REINTENTADOS.clear)

    def _filas(self, *trios):
        return [{"claim_id": c, "user_id": u, "ultimo_id": i} for c, u, i in trios]

    def _correr(self, filas, resultado=None, tope=30):
        mock.patch.object(dml, "_avisos_fallidos", return_value=filas).start()
        sinc = mock.patch.object(dml, "sincronizar", new=mock.AsyncMock(
            return_value=resultado or {"ok": True, "accion": "guardado"})).start()
        cli = httpx.AsyncClient()
        try:
            res = _run(dml.reintentar_avisos(tope=tope, cli=cli))
        finally:
            _run(cli.aclose())
        return sinc, res

    def test_sql_lee_solo_lo_que_fallo(self):
        fetch = mock.patch.object(dml.sdb, "fetch_all", return_value=[]).start()
        dml._avisos_fallidos(40)
        sql, params = fetch.call_args.args
        plano = " ".join(sql.split())
        self.assertIn("from ops.webhook_events e", plano)
        self.assertIn("e.topic = 'post_purchase'", plano)
        self.assertIn("substring(e.external_id from '/claims/([0-9]+)')", plano)
        self.assertIn("not e.procesado and e.recibido_at < now() - make_interval(mins => %s)",
                      plano)
        self.assertNotIn("payload", plano)           # del aviso solo se toma el id
        self.assertEqual(params[0], "mercado_libre")
        # Staging y prod comparten ops.webhook_events: cada uno solo lo suyo.
        self.assertIn("e.env = %s", plano)
        self.assertEqual(params[1], dml.settings.app_env)
        self.assertEqual(params[2:6], (7, "devolución % falló%", "error:%", 10))
        self.assertEqual(params[-1], 40)

    def test_reintenta_con_la_cuenta_del_aviso(self):
        sinc, res = self._correr(self._filas(("7001", "3064478475", 10),
                                             ("7002", "999", 9)))
        llamadas = [(c.args[0], c.args[1], c.kwargs["detectado_via"])
                    for c in sinc.await_args_list]
        self.assertEqual(llamadas, [("7001", "SANCORFASHION", "webhook"),
                                    ("7002", "BEKURA", "webhook")])
        self.assertTrue(all(c.kwargs["cli"] is not None for c in sinc.await_args_list))
        self.assertEqual((res["reintentados"], res["guardados"]), (2, 2))

    def test_lo_resuelto_no_se_repite(self):
        filas = self._filas(("7001", "3072519654", 10))
        self._correr(filas)
        sinc, res = self._correr(filas)
        sinc.assert_not_awaited()
        self.assertEqual(res["reintentados"], 0)

    def test_lo_que_sigue_fallando_tiene_tope_de_intentos(self):
        filas = self._filas(("7001", "3072519654", 10))
        malo = {"ok": False, "motivo": "claim 7001 → 429 tras 3 reintentos"}
        hechos = sum(self._correr(filas, malo)[0].await_count for _ in range(5))
        self.assertEqual(hechos, dml._AVISOS_MAX_INTENTOS)
        # Un aviso NUEVO del mismo claim que vuelve a fallar sí se reintenta.
        sinc, _ = self._correr(self._filas(("7001", "3072519654", 11)), malo)
        self.assertEqual(sinc.await_count, 1)

    def test_tope_por_corrida(self):
        filas = self._filas(*((str(7000 + i), "3072519654", 100 - i) for i in range(5)))
        sinc, res = self._correr(filas, tope=2)
        self.assertEqual([c.args[0] for c in sinc.await_args_list], ["7000", "7001"])
        self.assertEqual(res["reintentados"], 2)

    def test_tope_por_variable_e_invalidos(self):
        for valor, esperado in ((30, 30), (5, 5), (0, 0), (-1, 0), (None, 0), ("x", 30)):
            with mock.patch.object(dml.settings, "devoluciones_ml_reintento_tope", valor):
                self.assertEqual(dml._tope_avisos(), esperado, valor)

    def test_sin_tope_explicito_lee_la_variable(self):
        filas = self._filas(*((str(7000 + i), "3072519654", 100 - i) for i in range(5)))
        with mock.patch.object(dml.settings, "devoluciones_ml_reintento_tope", 3):
            sinc, res = self._correr(filas, tope=None)
        self.assertEqual(sinc.await_count, 3)
        self.assertEqual(res["tope"], 3)

    def test_lo_que_salta_la_memoria_no_se_come_el_tope(self):
        """Con la memoria llena de resueltos y tope=1, el claim nuevo entra: se
        piden de más a la bitácora justo para esto."""
        viejos = [(str(6000 + i), "3072519654", 500 - i) for i in range(10)]
        for cid, _, ultimo in viejos:
            dml._REINTENTADOS[("BEKURA", cid)] = (ultimo, 1, True, dml.time.monotonic())
        fetch = mock.patch.object(dml, "_avisos_fallidos", return_value=self._filas(
            *viejos, ("7001", "3072519654", 1))).start()
        sinc = mock.patch.object(dml, "sincronizar", new=mock.AsyncMock(
            return_value={"ok": True, "accion": "guardado"})).start()
        cli = httpx.AsyncClient()
        try:
            _run(dml.reintentar_avisos(tope=1, cli=cli))
        finally:
            _run(cli.aclose())
        fetch.assert_called_once_with(11)
        self.assertEqual([c.args[0] for c in sinc.await_args_list], ["7001"])

    def test_la_bitacora_se_lee_fuera_del_loop(self):
        """Regla 11: `_avisos_fallidos` es psycopg2 síncrono."""
        hilos: list[int] = []
        mock.patch.object(dml, "_avisos_fallidos", side_effect=lambda limite: (
            hilos.append(threading.get_ident()), [])[1]).start()
        _run(dml.reintentar_avisos(tope=5))
        self.assertEqual(len(hilos), 1)
        self.assertNotEqual(hilos[0], threading.get_ident())

    def test_apagado_no_lee_la_bitacora(self):
        fetch = mock.patch.object(dml, "_avisos_fallidos").start()
        res = _run(dml.reintentar_avisos(tope=0))
        fetch.assert_not_called()
        self.assertEqual(res["motivo"], "apagado")

    def test_mapa_de_cuentas_igual_que_el_webhook(self):
        fuente = (BACKEND / "routers" / "webhooks.py").read_text(encoding="utf-8")
        m = re.search(r"^_USER_A_CUENTA = (\{[^}]*\})", fuente, re.M)
        self.assertEqual(ast.literal_eval(m.group(1)), dml._USER_A_CUENTA)


class Robustez(unittest.TestCase):
    def test_destino_que_no_es_objeto_no_truena(self):
        self.assertIsNone(dml._destino({"shipments": [{"destination": "warehouse"},
                                                      {"destination": ["x"]}]}))
        self.assertEqual(dml._destino({"shipments": [{"destination": "raro"},
                                                     {"destination": {"name": "warehouse"}}]}),
                         "warehouse")

    def test_hora_amplio_valida(self):
        self.assertEqual(dml.hora_amplio_utc("10:20"), (10, 20))
        self.assertEqual(dml.hora_amplio_utc(" 07:05 "), (7, 5))
        for malo in ("25:00", "10:75", "-1:00", "diez", "", None, "10"):
            with self.assertLogs(dml.log, "ERROR"):
                self.assertEqual(dml.hora_amplio_utc(malo), (10, 20), malo)

    def test_scheduler_delega_el_alta_del_barrido_amplio(self):
        """El alta vive en `programar_barrido_amplio` (probada abajo con un
        scheduler de mentira); `iniciar` solo la llama, sin `add_job` propio."""
        fuente = (BACKEND / "services" / "scheduler.py").read_text(encoding="utf-8")
        self.assertIn("devoluciones_ml.programar_barrido_amplio(_scheduler)", fuente)
        self.assertNotIn("barrer_mediaciones_amplio", fuente)

    def _programar(self, *, mediaciones=True, dias=21, hora="10:20", truena=False):
        sched = mock.Mock()
        if truena:
            sched.add_job.side_effect = ValueError("hora inválida")
        with mock.patch.object(dml.settings, "devoluciones_ml_mediaciones", mediaciones), \
             mock.patch.object(dml.settings, "devoluciones_ml_mediaciones_amplio_dias", dias), \
             mock.patch.object(dml.settings, "devoluciones_ml_mediaciones_amplio_hora_utc",
                               hora):
            return dml.programar_barrido_amplio(sched), sched

    def test_programar_amplio_con_hora_valida(self):
        ok, sched = self._programar(hora="07:05")
        self.assertTrue(ok)
        sched.add_job.assert_called_once_with(
            dml.barrer_mediaciones_amplio, "cron", hour=7, minute=5,
            id="devoluciones_ml_mediaciones_amplio", max_instances=1, coalesce=True)

    def test_programar_amplio_hora_mala_usa_la_de_omision(self):
        with self.assertLogs(dml.log, "ERROR"):
            ok, sched = self._programar(hora="25:00")
        self.assertTrue(ok)
        self.assertEqual(sched.add_job.call_args.kwargs["hour"], 10)
        self.assertEqual(sched.add_job.call_args.kwargs["minute"], 20)

    def test_programar_amplio_que_truena_no_lanza(self):
        """Si `add_job` lanza, se registra y se devuelve False: los jobs que
        `iniciar` registra después siguen dándose de alta."""
        with self.assertLogs(dml.log, "ERROR") as logs:
            ok, sched = self._programar(truena=True)
        self.assertFalse(ok)
        sched.add_job.assert_called_once()
        self.assertIn("el resto del scheduler sigue", logs.output[0])

    def test_programar_amplio_apagado_no_da_de_alta(self):
        for kw in ({"mediaciones": False}, {"dias": 0}):
            with self.subTest(**kw):
                ok, sched = self._programar(**kw)
                self.assertFalse(ok)
                sched.add_job.assert_not_called()


if __name__ == "__main__":
    unittest.main()
