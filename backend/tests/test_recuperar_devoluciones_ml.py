"""El script de recuperación de devoluciones de ML
(`scripts/recuperar_devoluciones_ml.py`): sus candados y sus fases.

Lo que se prueba es lo que protege a producción y a ML:

  · el destino solo puede ser el sandbox, y la configuración queda aislada
    (sin env.staging, sin MySQL ni Woo);
  · el transporte vigilado: solo GET a ML, a ritmo, con tope, y un 401/403 lo
    DETIENE (salvo el 401 interno ya medido de `/returns`); detenido, no sale
    ni un GET más y no se escribe nada;
  · el token: si es viejo o falta, se aborta antes de la primera llamada;
  · el dry-run no escribe; la fase local no degrada (nunca NULL, nunca saca ni
    mete a una fila en 'reembolsada') y no llama a ML.

Sin red, sin base y sin secretos reales: DSNs y llaves de mentira.

    cd backend && python -m unittest tests.test_recuperar_devoluciones_ml -v
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import logging
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import devoluciones_ml as dml  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "recuperar_devoluciones_ml", BACKEND / "scripts" / "recuperar_devoluciones_ml.py")
rec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rec)

SANDBOX = "postgresql://postgres.yvootpbzprueba:x@pooler.invalido:6543/postgres"
PROD = "postgresql://postgres.tukwcvsiprueba:x@pooler.invalido:6543/postgres"
ML = "https://api.mercadolibre.com"


def _run(coro):
    return asyncio.run(coro)


def _vigilado(handler, **kw) -> "rec.TransporteVigilado":
    kw.setdefault("ritmo", 0)
    return rec.TransporteVigilado(httpx.MockTransport(handler), **kw)


async def _gets(t, rutas, metodo="GET"):
    out = []
    async with httpx.AsyncClient(transport=t) as cli:
        for r in rutas:
            try:
                resp = await cli.request(metodo, r if r.startswith("http") else f"{ML}{r}")
                out.append(resp.status_code)
            except rec.CorridaDetenida:
                out.append("detenido")
    return out


# ── Candados 1 y 2 ───────────────────────────────────────────────────────────

class Destino(unittest.TestCase):
    def test_destino_rechaza_produccion(self):
        with self.assertRaises(rec.Candado):
            rec.verificar_destino(PROD)
        # Aunque traiga las dos refs.
        with self.assertRaises(rec.Candado):
            rec.verificar_destino(SANDBOX + "?x=tukwcvsi")

    def test_destino_acepta_sandbox(self):
        rec.verificar_destino(SANDBOX)

    def test_destino_rechaza_vacio_y_otro(self):
        for url in ("", "postgresql://postgres.otraref:x@h/db"):
            with self.assertRaises(rec.Candado):
                rec.verificar_destino(url)

    def test_aislar_config(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = Path(d)
            env = {"APP_ENV": "staging"}
            with self.assertRaises(rec.Candado):
                rec.aislar_config(SANDBOX, env=env, raiz=raiz)
            env = {}
            (raiz / ".env").write_text("X=1", encoding="utf-8")
            with self.assertRaises(rec.Candado):
                rec.aislar_config(SANDBOX, env=env, raiz=raiz)
            (raiz / ".env").unlink()
            rec.aislar_config(SANDBOX, env=env, raiz=raiz)
            self.assertEqual(env["SUPABASE_DB_URL"], SANDBOX)

    def _settings(self, **kw):
        base = {"supabase_db_url": SANDBOX, "supabase_prod_ref": "tukwcvsi"}
        base.update(kw)
        return SimpleNamespace(model_dump=lambda: dict(base), **base)

    def test_verificar_config(self):
        rec.verificar_config(self._settings(), SANDBOX)
        with self.assertRaises(rec.Candado):
            rec.verificar_config(self._settings(supabase_db_url=PROD), SANDBOX)
        with self.assertRaises(rec.Candado):
            rec.verificar_config(self._settings(db_host="mysql.invalido"), SANDBOX)
        with self.assertRaises(rec.Candado):
            rec.verificar_config(self._settings(wc_consumer_key="ck_x"), SANDBOX)
        with self.assertRaises(rec.Candado):
            rec.verificar_config(self._settings(kubera_db_url=PROD), SANDBOX)

    def test_cables_trampa(self):
        db = SimpleNamespace(fetch_one=lambda *a: 1, get_cursor=lambda: 1, _get_pool=lambda: 1)
        meli = SimpleNamespace(_access_token=lambda c: "tok", refrescar_token=lambda c: "t")
        puestos = rec.poner_cables_trampa(db, meli)
        self.assertIn("meli.refrescar_token", puestos)
        for f in (lambda: db.fetch_one("x"), db.get_cursor, db._get_pool,
                  lambda: meli._access_token("BEKURA"), lambda: meli.refrescar_token("B")):
            with self.assertRaises(RuntimeError):
                f()


# ── Candado 4: el token ──────────────────────────────────────────────────────

class Token(unittest.TestCase):
    def setUp(self):
        from cryptography.fernet import Fernet
        self.clave = Fernet.generate_key().decode()
        self.f = Fernet(self.clave.encode())
        self.ahora = datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)

    def _fila(self, cuenta, minutos):
        return (cuenta, self.f.encrypt(b"APP_USR-prueba").decode(),
                self.ahora - timedelta(minutes=minutos))

    def test_token_viejo_aborta(self):
        filas = [self._fila("BEKURA", 30), self._fila("SANCORFASHION", 301)]
        with self.assertRaises(rec.Candado) as cm:
            rec.descifrar_tokens(filas, self.clave, cuentas=rec.CUENTAS,
                                 max_edad_min=300, ahora=self.ahora)
        self.assertIn("SANCORFASHION", str(cm.exception))
        self.assertNotIn("APP_USR", str(cm.exception))

    def test_token_fresco_se_descifra(self):
        filas = [self._fila("BEKURA", 30)]
        tok = rec.descifrar_tokens(filas, self.clave, cuentas=("BEKURA",),
                                   max_edad_min=300, ahora=self.ahora)
        self.assertEqual(tok, {"BEKURA": "APP_USR-prueba"})

    def test_token_faltante_o_sin_fecha_aborta(self):
        with self.assertRaises(rec.Candado):
            rec.descifrar_tokens([self._fila("BEKURA", 1)], self.clave,
                                 cuentas=rec.CUENTAS, max_edad_min=300, ahora=self.ahora)
        sin_fecha = [("BEKURA", self.f.encrypt(b"t").decode(), None)]
        with self.assertRaises(rec.Candado):
            rec.descifrar_tokens(sin_fecha, self.clave, cuentas=("BEKURA",),
                                 max_edad_min=300, ahora=self.ahora)

    def test_llave_invalida_aborta(self):
        with self.assertRaises(rec.Candado):
            rec.descifrar_tokens([], "no-es-fernet", cuentas=("BEKURA",),
                                 max_edad_min=300, ahora=self.ahora)


# ── Candado 5: el transporte vigilado ────────────────────────────────────────

class Transporte(unittest.TestCase):
    def test_transporte_detiene_en_401_y_ya_no_llama(self):
        llamadas = []

        def handler(req):
            llamadas.append(req.url.path)
            if req.url.path.endswith("/5001"):
                return httpx.Response(401, json={"message": "invalid_token"})
            return httpx.Response(200, json={})
        t = _vigilado(handler)
        out = _run(_gets(t, ["/post-purchase/v1/claims/5000",
                             "/post-purchase/v1/claims/5001",
                             "/post-purchase/v1/claims/5002"]))
        self.assertEqual(out, [200, 401, "detenido"])
        self.assertEqual(len(llamadas), 2)            # el tercero ni salió
        self.assertIn("401", t.detenido)

    def test_transporte_detiene_en_403(self):
        t = _vigilado(lambda req: httpx.Response(403, json={}))
        out = _run(_gets(t, ["/post-purchase/v1/claims/1", "/post-purchase/v1/claims/2"]))
        self.assertEqual(out, [403, "detenido"])

    def test_transporte_no_detiene_en_401_interno_de_returns(self):
        def handler(req):
            if req.url.path.endswith("/returns"):
                return httpx.Response(401, content=b'{"message":"Error executing GET '
                                                    b'[client:shipments]"}')
            return httpx.Response(200, json={})
        t = _vigilado(handler)
        out = _run(_gets(t, ["/post-purchase/v2/claims/5573786449/returns",
                             "/post-purchase/v1/claims/5573786449"]))
        self.assertEqual(out, [401, 200])
        self.assertIsNone(t.detenido)
        self.assertEqual(t.internos_401, 1)

    def test_401_de_returns_sin_la_firma_si_detiene(self):
        t = _vigilado(lambda req: httpx.Response(401, json={"message": "invalid_token"}))
        _run(_gets(t, ["/post-purchase/v2/claims/1/returns"]))
        self.assertIsNotNone(t.detenido)

    def test_transporte_respeta_el_ritmo(self):
        reloj = [100.0]
        dormidas = []

        async def dormir(s):
            dormidas.append(round(s, 6))
            reloj[0] += s
        t = rec.TransporteVigilado(httpx.MockTransport(lambda r: httpx.Response(200, json={})),
                                   ritmo=2, reloj=lambda: reloj[0], dormir=dormir)
        _run(_gets(t, ["/a/1", "/a/2", "/a/3"]))
        self.assertEqual(dormidas, [0.5, 0.5])        # 2 por segundo
        self.assertEqual(t.llamadas, 3)

    def test_transporte_respeta_el_tope(self):
        llamadas = []

        def handler(req):
            llamadas.append(req)
            return httpx.Response(200, json={})
        t = _vigilado(handler, tope=2)
        out = _run(_gets(t, ["/a/1", "/a/2", "/a/3", "/a/4"]))
        self.assertEqual(out, [200, 200, "detenido", "detenido"])
        self.assertEqual(len(llamadas), 2)
        self.assertIn("tope", t.detenido)

    def test_transporte_solo_get_y_solo_ml(self):
        llamadas = []

        def handler(req):
            llamadas.append(req)
            return httpx.Response(200, json={})
        t = _vigilado(handler)
        self.assertEqual(_run(_gets(t, ["/a/1"], metodo="POST")), ["detenido"])
        self.assertIn("POST", t.detenido)
        t2 = _vigilado(handler)
        self.assertEqual(_run(_gets(t2, ["https://chunche.shop/wp-json/wc/v3/orders"])),
                         ["detenido"])
        self.assertIn("host", t2.detenido)
        self.assertEqual(llamadas, [])

    def test_cuenta_por_ruta_normalizada(self):
        t = _vigilado(lambda r: httpx.Response(200, json={}))
        _run(_gets(t, ["/post-purchase/v1/claims/1", "/post-purchase/v1/claims/2",
                       "/post-purchase/v1/claims/reasons/PDD9939"]))
        self.assertEqual(t.por_ruta[("/post-purchase/v1/claims/{id}", 200)], 2)
        self.assertEqual(t.por_ruta[("/post-purchase/v1/claims/reasons/{id}", 200)], 1)

    def test_detenido_no_se_escribe(self):
        t = _vigilado(lambda r: httpx.Response(200))
        t.detenido = "401 en /x"
        col = rec.Colector(t)
        with self.assertRaises(rec.CorridaDetenida):
            col({"external_return_id": "1"}, [])
        g = rec.GuardarEnSandbox(mock.Mock(), SimpleNamespace(supabase_db_url=SANDBOX), t)
        with self.assertRaises(rec.CorridaDetenida):
            g({"external_return_id": "1"}, [])

    def test_guardar_en_sandbox_vuelve_a_comprobar_el_destino(self):
        fake_dml = mock.Mock()
        g = rec.GuardarEnSandbox(fake_dml, SimpleNamespace(supabase_db_url=PROD), None)
        with self.assertRaises(rec.Candado):
            g({"external_return_id": "1"}, [])
        fake_dml._guardar.assert_not_called()


# ── Las fases ────────────────────────────────────────────────────────────────

def _claim_med(cid, related=("return",)):
    return {"id": int(cid), "type": "mediations", "status": "opened",
            "resource_id": 2000000001, "reason_id": "PDD9963", "claimed_quantity": 1,
            "related_entities": list(related),
            "date_created": "2026-09-20T10:00:00.000-04:00"}


class Fases(unittest.TestCase):
    def setUp(self):
        dml._NO_DEVOLUCION.clear()
        dml._MOTIVOS.clear()
        mock.patch.object(dml, "_lineas_de_pedidos", return_value={}).start()
        mock.patch.object(dml, "_cuentas_kubera", return_value={}).start()
        mock.patch.object(dml, "_duplicada_de", return_value=None).start()
        mock.patch.object(dml, "_returns_guardado", return_value=False).start()
        self.ya = mock.patch.object(dml, "_ya_guardados", return_value=set()).start()
        mock.patch.object(dml.meli, "_access_token",
                          side_effect=AssertionError("no se debe tocar meli")).start()
        self.cursor = mock.patch.object(dml.sdb, "get_cursor",
                                        side_effect=AssertionError("¡escribió!")).start()
        self.addCleanup(mock.patch.stopall)
        self.salida = self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def _ml(self, req):
        p = req.url.path
        if p.endswith("/claims/search"):
            data = [{"id": 801}, {"id": 802}, {"id": 803}]
            return httpx.Response(200, json={"data": data, "paging": {"total": 3}})
        if "/claims/reasons/" in p:
            return httpx.Response(200, json={"detail": "Es distinto a lo que pedí"})
        if p.endswith("/802/returns"):
            return httpx.Response(404, json={})
        if p.endswith("/returns"):
            return httpx.Response(200, json={"id": 77, "status": "label_generated",
                                             "status_money": "retained",
                                             "shipments": [{"destination": {"name": "warehouse"}}]})
        if p.endswith("/detail"):
            return httpx.Response(200, json={})
        cid = p.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_claim_med(cid, () if cid == "802" else ("return",)))

    def test_dry_run_no_escribe(self):
        t = _vigilado(self._ml)
        col = rec.Colector(t)

        async def _go():
            async with httpx.AsyncClient(transport=t) as cli:
                return await rec.fase_mediaciones(
                    dml, cli, {"BEKURA": "tok"}, col, cuentas=("BEKURA",), dias=10,
                    limite=None, hoy=date(2026, 10, 5), transporte=t)
        res = _run(_go())
        self.cursor.assert_not_called()
        self.assertEqual([r["claim"] for r in res], ["801", "802", "803"])
        self.assertEqual([r.get("decision") for r in res], ["guardar", "esperar", "guardar"])
        self.assertEqual(len(col.cabeceras), 2)
        self.assertTrue(all(c["detectado_via"] == "backfill" for c in col.cabeceras))
        self.assertEqual({c["destino"] for c in col.cabeceras}, {"warehouse"})
        self.assertIsNone(t.detenido)

    def test_limite_claims_corta_la_fase(self):
        t = _vigilado(self._ml)
        col = rec.Colector(t)

        async def _go():
            async with httpx.AsyncClient(transport=t) as cli:
                return await rec.fase_mediaciones(
                    dml, cli, {"BEKURA": "tok"}, col, cuentas=("BEKURA",), dias=10,
                    limite=1, hoy=date(2026, 10, 5), transporte=t)
        self.assertEqual(len(_run(_go())), 1)

    def test_corrida_detenida_no_escribe_lo_que_falta(self):
        def ml(req):
            if req.url.path.endswith("/803"):
                return httpx.Response(401, json={"message": "invalid_token"})
            return self._ml(req)
        t = _vigilado(ml)
        col = rec.Colector(t)

        async def _go():
            async with httpx.AsyncClient(transport=t) as cli:
                return await rec.fase_mediaciones(
                    dml, cli, {"BEKURA": "tok"}, col, cuentas=("BEKURA",), dias=10,
                    limite=None, hoy=date(2026, 10, 5), transporte=t)
        with self.assertLogs(dml.log, "ERROR"):
            res = _run(_go())
        self.assertIsNotNone(t.detenido)
        self.assertFalse(res[-1]["ok"])
        self.assertEqual(len(col.cabeceras), 1)        # solo la 801

    def test_meses(self):
        self.assertEqual(rec.meses("2026-08-30", "2026-10-02"),
                         [("2026-08-30", "2026-08-31"), ("2026-09-01", "2026-09-30"),
                          ("2026-10-01", "2026-10-02")])

    def test_mediaciones_con_fila_no_se_releen(self):
        """Las que ya tienen fila las cubrió `abiertas` (que ahora va antes): ni
        un GET. Y una corrida detenida se repite sin repetir lo hecho."""
        self.ya.return_value = {"801", "803"}
        t = _vigilado(self._ml)
        col = rec.Colector(t)

        async def _go():
            async with httpx.AsyncClient(transport=t) as cli:
                return await rec.fase_mediaciones(
                    dml, cli, {"BEKURA": "tok"}, col, cuentas=("BEKURA",), dias=10,
                    limite=None, hoy=date(2026, 10, 5), transporte=t)
        res = _run(_go())
        self.ya.assert_called_once_with("BEKURA", ["801", "802", "803"])
        self.assertEqual([r["claim"] for r in res], ["802"])
        rutas = [r for (r, _st) in t.por_ruta]
        self.assertNotIn("/post-purchase/v1/claims/{id}/detail", rutas)

    def test_fases_abiertas_antes_que_mediaciones(self):
        self.assertEqual(rec.FASES, ("local", "abiertas", "mediaciones"))


class Retiros(unittest.TestCase):
    def test_colector_anota_sin_borrar_y_solo_si_hay_fila(self):
        fake_dml = mock.Mock()
        fake_dml._ya_guardados.side_effect = lambda cuenta, ids: {"5002"} & set(ids)
        col = rec.Colector(None, fake_dml)
        self.assertEqual(col.retirar("BEKURA", "5001", "4999", 9001), 0)   # sin fila
        self.assertEqual(col.retirar("BEKURA", "5002", "5001", 9001), 0)   # dry-run: 0
        self.assertEqual(col.retiros, [("BEKURA", "5002", "5001")])
        fake_dml._retirar_duplicada.assert_not_called()

    def test_guardar_en_sandbox_retira_comprobando_el_destino(self):
        fake_dml = mock.Mock()
        fake_dml._retirar_duplicada.return_value = 1
        g = rec.GuardarEnSandbox(fake_dml, SimpleNamespace(supabase_db_url=SANDBOX), None)
        self.assertEqual(g.retirar("BEKURA", "5002", "5001", 9001), 1)
        fake_dml._retirar_duplicada.assert_called_once_with("BEKURA", "5002", "5001", 9001)
        self.assertEqual(g.retiros, [("BEKURA", "5002", "5001")])
        prod = rec.GuardarEnSandbox(fake_dml, SimpleNamespace(supabase_db_url=PROD), None)
        with self.assertRaises(rec.Candado):
            prod.retirar("BEKURA", "5002", "5001", 9001)
        self.assertEqual(fake_dml._retirar_duplicada.call_count, 1)

    def test_retirar_detenido_no_borra(self):
        t = _vigilado(lambda r: httpx.Response(200))
        t.detenido = "401 en /x"
        fake_dml = mock.Mock()
        g = rec.GuardarEnSandbox(fake_dml, SimpleNamespace(supabase_db_url=SANDBOX), t)
        with self.assertRaises(rec.CorridaDetenida):
            g.retirar("BEKURA", "5002", "5001", 9001)
        fake_dml._retirar_duplicada.assert_not_called()

    def test_ordenes_en_transicion(self):
        filas_db = [
            # orden A: mediación guardada SIN id + otro claim CON id → sospechosa
            {"cuenta": "BEKURA", "external_order_id": "A", "external_return_id": "11", "rid": None},
            {"cuenta": "BEKURA", "external_order_id": "A", "external_return_id": "12", "rid": "77"},
            # orden B: low_cost (id 0) junto a una con id → no
            {"cuenta": "BEKURA", "external_order_id": "B", "external_return_id": "21", "rid": "0"},
            {"cuenta": "BEKURA", "external_order_id": "B", "external_return_id": "22", "rid": "78"},
            # orden C: guardada sin id; la corrida le trae su id → ya no
            {"cuenta": "BEKURA", "external_order_id": "C", "external_return_id": "31", "rid": None},
            {"cuenta": "BEKURA", "external_order_id": "C", "external_return_id": "32", "rid": "79"},
            # orden D: sin id + con id, pero la fila sin id se retira → no
            {"cuenta": "BEKURA", "external_order_id": "D", "external_return_id": "41", "rid": None},
            {"cuenta": "BEKURA", "external_order_id": "D", "external_return_id": "42", "rid": "80"},
        ]
        cabs = [{"cuenta": "BEKURA", "external_order_id": "C", "external_return_id": "31",
                 "payload": json.dumps({"returns": {"id": 79}})}]
        retiros = [("BEKURA", "41", "42")]
        self.assertEqual(rec.ordenes_en_transicion(filas_db, cabs, retiros), ["11"])


class LogSinDatos(unittest.TestCase):
    def _log(self, fn):
        flujo = io.StringIO()
        h = logging.StreamHandler(flujo)
        h.addFilter(rec.FiltroSinDatos())
        lg = logging.getLogger("prueba.recuperar.log")
        lg.addHandler(h)
        lg.propagate = False
        try:
            fn(lg)
        finally:
            lg.removeHandler(h)
        return flujo.getvalue()

    def test_log_exception_sin_detail_ni_traza(self):
        def fn(lg):
            try:
                raise RuntimeError('new row violates check constraint "ck_returns_estado"\n'
                                   'DETAIL:  Failing row contains (mercado_libre, BEKURA, '
                                   '5001, {"claim": {"players": [{"user_id": 123456789}]}})')
            except RuntimeError:
                lg.exception("DEVOLUCION ML claim %s falló", "5001")
        out = self._log(fn)
        self.assertEqual(out.strip(), "DEVOLUCION ML claim 5001 falló [RuntimeError]")
        for prohibido in ("DETAIL", "Failing row", "players", "user_id", "Traceback"):
            self.assertNotIn(prohibido, out)

    def test_mensaje_con_detail_en_los_argumentos(self):
        out = self._log(lambda lg: lg.warning("fallo: %s", "x\nDETAIL: Failing row contains (y)"))
        self.assertEqual(out.strip(), "fallo: x")

    def test_instalar_cubre_los_handlers(self):
        raiz = logging.getLogger()
        h = logging.StreamHandler(io.StringIO())
        raiz.addHandler(h)
        todos = [raiz, *(lg for lg in logging.root.manager.loggerDict.values()
                         if isinstance(lg, logging.Logger))]
        antes = {id(x): list(x.filters) for lg in todos for x in lg.handlers}
        try:
            self.assertGreaterEqual(rec.instalar_filtro_log(), 1)
            self.assertTrue(any(isinstance(f, rec.FiltroSinDatos) for f in h.filters))
            n = len(h.filters)
            rec.instalar_filtro_log()
            self.assertEqual(len(h.filters), n)          # no se duplica
        finally:
            raiz.removeHandler(h)
            for lg in todos:                             # el estado global, como estaba
                for x in lg.handlers:
                    if id(x) in antes:
                        x.filters[:] = antes[id(x)]


def _fila(rid, estado, status=None, money="retained", destinos=(), destino=None):
    devol = {}
    if status:
        devol = {"id": 1, "status": status, "status_money": money,
                 "shipments": [{"destination": {"name": d}} for d in destinos]}
    return {"cuenta": "BEKURA", "external_return_id": rid, "estado": estado,
            "estado_canal": status, "destino": destino, "es_fulfillment": True,
            "payload": {"claim": {"id": int(rid), "status": "opened"}, "returns": devol}}


class FaseLocal(unittest.TestCase):
    def test_fase_local_no_degrada(self):
        filas = [
            _fila("1", "abierta", "expired"),                                 # F4
            _fila("2", "reembolsada", "expired", money="refunded"),          # no se toca
            _fila("3", "reembolsada", "expired", money="retained"),          # el dinero manda
            _fila("4", "abierta", "shipped", destinos=("warehouse",)),        # entra a 'en_transito'
            _fila("5", "recibida", "delivered", destinos=(), destino="warehouse"),  # nunca a NULL
            _fila("6", "abierta", "label_generated", destinos=("seller_address",)),  # F5
            _fila("7", "abierta", "delivered", money="refunded"),             # no entra a reembolsada
            _fila("8", "en_transito", "shipped", destinos=("warehouse",), destino="warehouse"),
        ]
        cambios = {c["external_return_id"]: c for c in rec.cambios_locales(filas, dml)}
        self.assertEqual(cambios["1"]["estado"], ("abierta", "rechazada"))
        self.assertNotIn("2", cambios)
        self.assertNotIn("3", cambios)
        self.assertEqual(cambios["4"]["estado"], ("abierta", "en_transito"))
        self.assertEqual(cambios["4"]["destino"], (None, "warehouse"))
        self.assertNotIn("5", cambios)
        self.assertEqual(cambios["6"], {**cambios["6"], "destino": (None, "seller_address")})
        self.assertNotIn("estado", cambios["6"])
        self.assertNotIn("7", cambios)
        self.assertNotIn("8", cambios)
        for c in cambios.values():
            if "destino" in c:
                self.assertIsNotNone(c["destino"][1])
            if "estado" in c:
                self.assertNotEqual(c["estado"][0], "reembolsada")
                self.assertNotEqual(c["estado"][1], "reembolsada")

    def test_payload_como_texto(self):
        f = _fila("1", "abierta", "expired")
        f["payload"] = json.dumps(f["payload"])
        self.assertEqual(rec.cambios_locales([f], dml)[0]["estado"], ("abierta", "rechazada"))

    def test_sql_local_candado(self):
        sql, params = rec.sql_local({"cuenta": "BEKURA", "external_return_id": "1",
                                     "estado": ("abierta", "rechazada"),
                                     "destino": (None, "warehouse")})
        self.assertIn("destino = coalesce(%s, destino)", sql)
        self.assertIn("and estado = %s and estado <> 'reembolsada'", sql)
        self.assertEqual(params, ("rechazada", "warehouse", "mercado_libre", "BEKURA", "1",
                                  "abierta"))
        sql, params = rec.sql_local({"cuenta": "BEKURA", "external_return_id": "2",
                                     "destino": (None, "seller_address")})
        self.assertNotIn("estado", sql)

    def test_relleno_de_destino_no_llama_a_ml(self):
        sentencias = []

        class Cur:
            rowcount = 1

            def execute(self, sql, params=None):
                sentencias.append((sql, params))

        @contextlib.contextmanager
        def cursor():
            yield Cur()
        sdb = SimpleNamespace(get_cursor=cursor)
        filas = [_fila("6", "abierta", "label_generated", destinos=("seller_address",))]
        with mock.patch.object(httpx.AsyncClient, "send",
                               side_effect=AssertionError("llamó a ML")), \
             mock.patch.object(httpx.Client, "send",
                               side_effect=AssertionError("llamó a ML")):
            cambios = rec.cambios_locales(filas, dml)
            n = rec.aplicar_locales(sdb, SimpleNamespace(supabase_db_url=SANDBOX), cambios)
        self.assertEqual(n, 1)
        self.assertEqual(sentencias[0][1][0], "seller_address")

    def test_aplicar_locales_rechaza_produccion(self):
        sdb = SimpleNamespace(get_cursor=mock.Mock(side_effect=AssertionError("¡escribió!")))
        with self.assertRaises(rec.Candado):
            rec.aplicar_locales(sdb, SimpleNamespace(supabase_db_url=PROD),
                                [{"cuenta": "BEKURA", "external_return_id": "1",
                                  "destino": (None, "warehouse")}])


class Informe(unittest.TestCase):
    def test_limpiar_motivo_sin_detail(self):
        m = ('new row violates check constraint "ck"\nDETAIL:  Failing row contains '
             '(mercado_libre, BEKURA, 5001, datos)')
        self.assertEqual(rec._limpiar_motivo(m), 'new row violates check constraint "ck"')

    def test_formas_solo_enumeraciones(self):
        f = rec.Formas()
        f.registrar({"claim": {"id": 1, "type": "mediations", "related_entities": ["return"]},
                     "returns": {"status": "expired", "status_money": "retained",
                                 "shipments": [{"type": "return",
                                                "destination": {"name": "warehouse",
                                                                "shipping_address": {"x": 1}}}]}})
        fila = f._por_claim["1"]
        self.assertEqual(fila["envios"], ("return→warehouse",))
        self.assertNotIn("shipping_address", json.dumps(fila))


if __name__ == "__main__":
    unittest.main()
