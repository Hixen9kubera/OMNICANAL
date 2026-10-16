"""Pruebas de PRICING (services/pricing_monitor.py + routers/pricing.py).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Acceso**: la regla RBAC es admin y ninguna otra la tapa; el router solo
   declara GET y rechaza sin sesión (401) o sin rol admin (403) SIN leer la foto.
2. **Foto local**: la entrega comprimida (`Content-Encoding: gzip`) a quien la
   acepta y plana a quien no; las hojas salen como JPEG.
3. **Nombres de hoja**: solo `p<n>`/`s<n>`; nada que arme una ruta (`..`, `/`).
4. **Sin foto**: 404 con el porqué; foto rota: 502 (no se sirve basura).
5. **GitHub**: sin carpeta y con token, pide al repo privado la ruta correcta con
   el token; un 404 de GitHub es «sin foto», un 401 es falla de la fuente.
6. **Script de preparación**: re-empaca las miniaturas de las publicaciones en
   hojas `p*`, reapunta su `img` y copia solo las hojas de rivales usadas.

Sin red: nada aquí habla con GitHub, kubera ni ningún marketplace.

    cd backend && python -m unittest tests.test_pricing_monitor -v
"""
from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import pricing as ruta  # noqa: E402
from services import pricing_monitor as pm  # noqa: E402

ADMIN = SimpleNamespace(autenticado=True, tipo="persona", rol="admin", actor="brandon@x")
FOTO = {"meta": {"generado": "2026-10-14 18:25", "hoy": "2026-10-14", "d0": "2026-06-17",
                 "img": {"celda": 90, "lado": 20, "pubs": "p", "riv": "s"}},
        "pubs": [{"id": "MLM1", "sku": "A", "img": 0}], "riv": {}}
JPEG = b"\xff\xd8\xff\xe0falso-jpeg"


def _cliente() -> TestClient:
    app = FastAPI()
    app.include_router(ruta.router)
    return TestClient(app)


def _como(quien):
    return mock.patch("routers.investigacion.core_identidad.resolver", new=mock.AsyncMock(return_value=quien))


class Base(unittest.TestCase):
    def setUp(self):
        pm.olvidar()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(pm.olvidar)
        for nombre, valor in (("pricing_foto_dir", str(self.dir)), ("pricing_github_token", "")):
            p = mock.patch.object(pm.settings, nombre, valor)
            p.start()
            self.addCleanup(p.stop)
        # Las relecturas en segundo plano hablan con ML: en pruebas no corren (cada prueba que las necesite las simula).
        for nombre in ("_refrescar_precios", "_refrescar_promos"):
            p = mock.patch.object(pm, nombre, new=mock.AsyncMock())
            p.start()
            self.addCleanup(p.stop)

    def poner_foto(self, foto=FOTO):
        (self.dir / "img").mkdir(exist_ok=True)
        (self.dir / "monitor.json.gz").write_bytes(gzip.compress(json.dumps(foto).encode()))
        (self.dir / "img" / "p0.jpg").write_bytes(JPEG)


class Acceso(Base):
    def test_regla_rbac_deja_pasar_al_operador_y_sin_prefijo_que_la_tape(self):
        # v0.661.0: el operador LLEGA al router (los invitados son operadores) y el router decide.
        from core import rbac
        self.assertIn(("GET", "/api/pricing", "operador"), rbac.REGLAS)
        self.assertEqual(rbac.rol_requerido("GET", "/api/pricing/monitor"), "operador")
        self.assertEqual(rbac.rol_requerido("GET", "/api/pricing/img/p0"), "operador")
        self.assertFalse(rbac.permite("lectura", "GET", "/api/pricing/monitor"))
        tapan = [r for r in rbac.REGLAS
                 if r[0] == "GET" and "/api/pricing".startswith(r[1]) and r[1] != "/api/pricing"]
        self.assertEqual(tapan, [])

    def test_router_solo_get_y_exige_admin_con_sesion(self):
        metodos = {m for r in ruta.router.routes for m in r.methods}
        self.assertTrue(metodos <= {"GET", "HEAD"}, metodos)
        self.poner_foto()
        cli = _cliente()
        anonimo = SimpleNamespace(autenticado=False, tipo="", rol="", actor="?")
        operador = SimpleNamespace(autenticado=True, tipo="persona", rol="operador", actor="kam@x")
        maquina = SimpleNamespace(autenticado=True, tipo="maquina", rol="admin", actor="llave")
        with _como(anonimo), mock.patch.object(pm, "foto") as f, mock.patch.object(pm, "hoja") as h:
            self.assertEqual(cli.get("/api/pricing/monitor").status_code, 401)
            self.assertEqual(cli.get("/api/pricing/img/p0").status_code, 401)
            f.assert_not_called()
            h.assert_not_called()
        for quien in (operador, maquina):
            with self.subTest(tipo=quien.tipo, rol=quien.rol), _como(quien), \
                 mock.patch.object(pm, "foto") as f:
                self.assertEqual(cli.get("/api/pricing/monitor").status_code, 403)
                f.assert_not_called()

    def test_invitados_entran_y_los_demas_operadores_no(self):
        from core import rbac
        self.poner_foto()
        cli = _cliente()
        for correo in ("haim@kubera.mx", "Andrea.Pardo@kubera.mx"):
            invitado = SimpleNamespace(autenticado=True, tipo="persona", rol="operador", actor=correo)
            with self.subTest(correo=correo), _como(invitado):
                self.assertTrue(rbac.ve_pricing(invitado))
                self.assertEqual(cli.get("/api/pricing/monitor").status_code, 200)
        otro = SimpleNamespace(autenticado=True, tipo="persona", rol="operador", actor="valeria@kubera.mx")
        with _como(otro), mock.patch.object(pm, "foto") as f:
            self.assertFalse(rbac.ve_pricing(otro))
            self.assertEqual(cli.get("/api/pricing/monitor").status_code, 403)
            f.assert_not_called()
        # una LLAVE de máquina con el correo de un invitado no cuenta: tiene que ser persona
        self.assertFalse(rbac.ve_pricing(SimpleNamespace(autenticado=True, tipo="maquina", rol="admin", actor="haim@kubera.mx")))


class FotoLocal(Base):
    def test_monitor_comprimido_y_plano(self):
        self.poner_foto()
        cli = _cliente()
        with _como(ADMIN):
            r = cli.get("/api/pricing/monitor", headers={"Accept-Encoding": "gzip"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.headers.get("content-encoding"), "gzip")
            self.assertEqual(r.json()["pubs"][0]["sku"], "A")
            self.assertEqual(r.headers.get("x-pricing-generado"), "2026-10-14 18:25")
            plano = cli.get("/api/pricing/monitor", headers={"Accept-Encoding": "identity"})
            self.assertEqual(plano.status_code, 200)
            self.assertIsNone(plano.headers.get("content-encoding"))
            self.assertEqual(plano.json()["meta"]["hoy"], "2026-10-14")

    def test_hoja_jpeg_y_nombres_raros_fuera(self):
        self.poner_foto()
        (self.dir / "secreto.jpg").write_bytes(b"no")
        cli = _cliente()
        with _como(ADMIN):
            r = cli.get("/api/pricing/img/p0?v=2026-10-14")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.headers["content-type"], "image/jpeg")
            self.assertEqual(r.content, JPEG)
            for malo in ("x1", "p0.jpg", "..%2Fsecreto", "s", "p1234", "P0"):
                with self.subTest(malo=malo):
                    self.assertEqual(cli.get(f"/api/pricing/img/{malo}").status_code, 404)
            self.assertEqual(cli.get("/api/pricing/img/s3").status_code, 404)   # válida pero no está

    def test_sin_foto_es_404_y_foto_rota_es_502(self):
        cli = _cliente()
        with _como(ADMIN):
            r = cli.get("/api/pricing/monitor")
            self.assertEqual(r.status_code, 404)
            self.assertIn("No hay foto", r.json()["detail"])
            (self.dir / "monitor.json.gz").write_bytes(gzip.compress(b'{"meta": {}}'))
            pm.olvidar()
            self.assertEqual(cli.get("/api/pricing/monitor").status_code, 502)
            (self.dir / "monitor.json.gz").write_bytes(b"no es gzip")
            pm.olvidar()
            self.assertEqual(cli.get("/api/pricing/monitor").status_code, 502)


class GitHub(Base):
    def _transporte(self, respuestas: dict[str, httpx.Response], vistos: list):
        def manejar(req: httpx.Request) -> httpx.Response:
            vistos.append(req)
            return respuestas.get(req.url.path, httpx.Response(404))
        real = httpx.AsyncClient
        return mock.patch.object(pm.httpx, "AsyncClient",
                                 lambda **kw: real(transport=httpx.MockTransport(manejar), **kw))

    def test_sin_carpeta_lee_del_repo_privado_con_el_token(self):
        vistos: list[httpx.Request] = []
        gz = gzip.compress(json.dumps(FOTO).encode())
        base = "/repos/joseKubera/precios_full/contents/monitor/foto"
        with mock.patch.object(pm.settings, "pricing_github_token", "tok-123"), \
             mock.patch.object(pm.settings, "pricing_foto_dir", str(self.dir / "no-existe")), \
             self._transporte({f"{base}/monitor.json.gz": httpx.Response(200, content=gz),
                               f"{base}/img/s2.jpg": httpx.Response(200, content=JPEG)}, vistos), \
             _como(ADMIN):
            self.assertEqual(pm.origen(), "github")
            cli = _cliente()
            self.assertEqual(cli.get("/api/pricing/monitor").json()["pubs"][0]["id"], "MLM1")
            self.assertEqual(cli.get("/api/pricing/img/s2").content, JPEG)
            self.assertEqual(cli.get("/api/pricing/img/s9").status_code, 404)
        self.assertEqual(vistos[0].headers["authorization"], "Bearer tok-123")
        self.assertEqual(vistos[0].url.params["ref"], "main")
        self.assertIn("raw", vistos[0].headers["accept"])

    def test_token_rechazado_es_falla_de_fuente(self):
        vistos: list[httpx.Request] = []
        base = "/repos/joseKubera/precios_full/contents/monitor/foto"
        with mock.patch.object(pm.settings, "pricing_github_token", "vencido"), \
             self._transporte({f"{base}/monitor.json.gz": httpx.Response(401)}, vistos), _como(ADMIN):
            r = _cliente().get("/api/pricing/monitor")
        self.assertEqual(r.status_code, 502)
        self.assertIn("401", r.json()["detail"])


class FotoDelRepo(unittest.TestCase):
    """La foto que viaja en `backend/data/pricing` (la que lee producción sin variables)."""

    def test_abre_y_trae_todas_las_hojas_que_usa(self):
        carpeta = Path(__file__).resolve().parents[1] / "data" / "pricing"
        archivo = carpeta / "monitor.json.gz"
        if not archivo.is_file():
            self.skipTest("sin foto en el repo")
        gz = archivo.read_bytes()
        meta = pm.validar(gz)
        datos = json.loads(gzip.decompress(gz))
        lado = meta["img"]["lado"]
        usadas = {f"{meta['img']['pubs']}{p['img'] // (lado * lado)}" for p in datos["pubs"] if p.get("img") is not None}
        usadas |= {f"{meta['img']['riv']}{r[7] // (lado * lado)}" for v in datos["riv"].values() for r in v["r"]
                   if r[7] is not None}
        faltan = sorted(h for h in usadas if not (carpeta / "img" / f"{h}.jpg").is_file())
        self.assertEqual(faltan, [])
        self.assertTrue(all(pm.NOMBRE_HOJA.match(h) for h in usadas))
        self.assertGreater(len(datos["pubs"]), 0)


class Preparar(unittest.TestCase):
    def test_reempaca_las_nuestras_y_copia_solo_las_hojas_usadas(self):
        from PIL import Image
        from scripts import pricing_preparar_foto as prep

        with tempfile.TemporaryDirectory() as t:
            raiz = Path(t)
            hojas = raiz / "hojas"
            hojas.mkdir()
            for h, color in ((0, "red"), (1, "blue"), (2, "green")):
                Image.new("RGB", (1800, 1800), color).save(hojas / f"s{h}.jpg")
            datos = {"meta": {"img": {"celda": 90, "lado": 20}, "hoy": "2026-10-14", "d0": "2026-06-17",
                              "generado": "x"},
                     "pubs": [{"id": "A", "img": 805}, {"id": "B", "img": None}, {"id": "C", "img": 3}],
                     "riv": {"SKU": {"termino": "t", "capturado": "c",
                                     "r": [["r", 1, "v", "u", "m", None, None, 401]]}}}
            entrada = raiz / "pagina.html"
            entrada.write_text('<p>x</p><script id="datos" type="application/json">' + json.dumps(datos) +
                               "</script>", encoding="utf-8")
            res = prep.preparar(entrada, hojas, raiz / "foto")

            self.assertEqual((res["pubs"], res["hojas_pubs"], res["hojas_riv"]), (3, 1, 1))
            foto = json.loads(gzip.decompress((raiz / "foto" / "monitor.json.gz").read_bytes()))
            self.assertEqual([p["img"] for p in foto["pubs"]], [0, None, 1])
            self.assertEqual(foto["riv"]["SKU"]["r"][0][7], 401)          # los rivales no se tocan
            self.assertEqual(foto["meta"]["img"]["pubs"], "p")
            self.assertEqual(sorted(x.name for x in (raiz / "foto" / "img").iterdir()), ["p0.jpg", "s1.jpg"])
            # A (celda 805) venía de s2, verde; C (celda 3) de s0, rojo. En p0 quedan
            # en las celdas 0 y 1, en ese orden.
            p0 = Image.open(raiz / "foto" / "img" / "p0.jpg").convert("RGB")
            r, g, _ = p0.getpixel((45, 45))
            self.assertTrue(g > 100 and r < 100, (r, g))
            r, g, _ = p0.getpixel((135, 45))
            self.assertTrue(r > 150 and g < 100, (r, g))
            self.assertEqual(pm.validar((raiz / "foto" / "monitor.json.gz").read_bytes())["generado"], "x")


class Hechos(Base):
    """«Hecho» y «Promoción hoy» (v0.654.0): admin, sin archivos = nada movido, y kubera solo suma lo de después."""

    HECHO = {"id": "MLM1", "cuenta": "BEKURA", "sku": "A", "antes": 200.0, "nuevo": 180, "normal": 400.0,
             "promo": "PRICE_DISCOUNT", "hasta": "2026-11-11", "desde": "2026-10-15T17:00:00-06:00", "se_ve": True}

    def test_exige_admin_y_no_lee_nada_sin_sesion(self):
        anonimo = SimpleNamespace(autenticado=False, tipo="", rol="", actor="?")
        with _como(anonimo), mock.patch.object(pm, "hechos") as h:
            self.assertEqual(_cliente().get("/api/pricing/hechos").status_code, 401)
            h.assert_not_called()
        self.assertEqual(__import__("core.rbac", fromlist=["x"]).rol_requerido("GET", "/api/pricing/hechos"), "operador")

    def test_sin_archivos_no_hay_movidos(self):
        with _como(ADMIN), mock.patch.object(pm, "_despues", return_value={}) as d:
            r = _cliente().get("/api/pricing/hechos")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["hechos"], r.json()["promos"], r.json()["promos_leido"]), ([], {}, None))
        d.assert_called_once_with([])

    def test_suma_lo_de_despues_y_aguanta_kubera_caida(self):
        (self.dir / "hechos.json").write_text(json.dumps({"hechos": [self.HECHO, {"id": "", "desde": "x"}]}), encoding="utf-8")
        (self.dir / "promos.json").write_text(json.dumps({"leido": "2026-10-15T19:11-06:00", "promos": {"MLM1": {"cobra": 180}}}),
                                              encoding="utf-8")
        despues = {"MLM1": {"pzs": 2, "ordenes": 2, "pesos": 360.0, "cobra_hoy": 180.0}}
        with _como(ADMIN), mock.patch.object(pm, "_despues", return_value=despues):
            d = _cliente().get("/api/pricing/hechos").json()
        self.assertEqual(len(d["hechos"]), 1)                 # el renglón sin id no entra
        self.assertEqual(d["hechos"][0]["despues"]["pzs"], 2)
        self.assertTrue(d["kubera"])
        self.assertEqual(d["promos"]["MLM1"]["cobra"], 180)
        pm.olvidar()
        with _como(ADMIN), mock.patch.object(pm, "_despues", side_effect=RuntimeError("kubera caída")):
            d = _cliente().get("/api/pricing/hechos").json()
        self.assertFalse(d["kubera"])
        self.assertIsNone(d["hechos"][0]["despues"])           # el movimiento se ve igual, sin lo de después


class Vivo(Base):
    """«Se agota» y low stock (v0.655.0): stock de ML + ventas de kubera de ahora; si una fuente falla, la otra sigue."""

    def test_exige_admin(self):
        anonimo = SimpleNamespace(autenticado=False, tipo="", rol="", actor="?")
        with _como(anonimo), mock.patch.object(pm, "vivo") as v:
            self.assertEqual(_cliente().get("/api/pricing/vivo").status_code, 401)
            v.assert_not_called()

    def test_junta_stock_y_ventas_y_aguanta_fallas(self):
        self.poner_foto({**FOTO, "pubs": [{"id": "MLM1", "c": "BEKURA"}, {"id": "MLM2", "c": "SANCORFASHION"}]})
        stock = {"MLM1": {"stock": 3, "status": "active"}}
        ventas = {"MLM1": {"u14": 6, "u30": 10}}
        with _como(ADMIN), mock.patch.object(pm, "_stock_vivo", new=mock.AsyncMock(return_value=stock)),              mock.patch.object(pm, "_ventas_vivo", return_value=ventas):
            d = _cliente().get("/api/pricing/vivo").json()
        self.assertEqual(d["pubs"]["MLM1"], {"stock": 3, "status": "active", "u14": 6, "u30": 10})
        self.assertEqual(d["pubs"]["MLM2"], {"stock": None, "status": None, "u14": 0, "u30": 0})   # sin dato de ML: no se inventa
        self.assertEqual((d["con_stock"], d["total"], d["kubera"]), (1, 2, True))
        pm.olvidar()
        with _como(ADMIN), mock.patch.object(pm, "_stock_vivo", new=mock.AsyncMock(side_effect=RuntimeError("ML caído"))),              mock.patch.object(pm, "_ventas_vivo", side_effect=RuntimeError("kubera caída")):
            d = _cliente().get("/api/pricing/vivo").json()
        self.assertEqual((d["pubs"], d["kubera"]), ({}, False))                          # todo falló: vacío, sin 500


class Salud(Base):
    """Calidad y experiencia (v0.659.0): admin, solo lo que guardó calidad_ml, y kubera caída no tumba la pantalla."""

    def test_exige_admin(self):
        anonimo = SimpleNamespace(autenticado=False, tipo="", rol="", actor="?")
        with _como(anonimo), mock.patch.object(pm, "salud") as s:
            self.assertEqual(_cliente().get("/api/pricing/salud").status_code, 401)
            s.assert_not_called()

    def test_lee_lo_guardado_y_aguanta_kubera_caida(self):
        self.poner_foto({**FOTO, "pubs": [{"id": "MLM1", "c": "BEKURA"}, {"id": "MLM2", "c": "SANCORFASHION"}]})
        guardado = {"MLM1": {"c": {"nivel": "bueno", "texto": "Profesional", "puntaje": 76.0},
                             "e": {"nivel": "malo", "texto": "Mala", "color": "red"}}}
        with _como(ADMIN), mock.patch.object(pm, "_salud_kubera", return_value=guardado) as k:
            d = _cliente().get("/api/pricing/salud").json()
        k.assert_called_once_with(["MLM1", "MLM2"])               # solo las publicaciones de la foto
        self.assertEqual((d["pubs"], d["kubera"], d["total"]), (guardado, True, 2))
        pm.olvidar()
        with _como(ADMIN), mock.patch.object(pm, "_salud_kubera", side_effect=pm.FalloFuente("kubera caída")):
            r = _cliente().get("/api/pricing/salud")
        self.assertEqual(r.status_code, 200)                       # sin 500: la tabla se ve igual
        self.assertEqual((r.json()["pubs"], r.json()["kubera"]), ({}, False))

    def test_arma_calidad_y_experiencia_de_las_filas(self):
        ahora = __import__("datetime").datetime(2026, 10, 16, 11, 42, tzinfo=__import__("datetime").timezone.utc)
        filas = [
            {"listing_id": "MLM1", "metrica": "calidad", "estado": "medida", "valor": 76, "nivel": "bueno",
             "nivel_canal": "Profesional", "n_pendientes": 2, "detalle": {}, "motivo": None, "medido_en": ahora, "capturado_en": ahora},
            {"listing_id": "MLM1", "metrica": "experiencia", "estado": "medida", "valor": 30, "nivel": "malo",
             "nivel_canal": "Mala", "n_pendientes": None, "motivo": None, "medido_en": None, "capturado_en": ahora,
             "detalle": {"color": "red", "razon": ["Kits incompletos"], "consecuencia": "Muy baja exposición", "por_categoria": False}},
        ]
        sdb = SimpleNamespace(disponible=lambda: True, fetch_all=mock.Mock(return_value=filas))
        with mock.patch.dict(sys.modules, {"services.supabase_db": sdb}), \
             mock.patch("services.supabase_db", sdb, create=True):
            d = pm._salud_kubera(["MLM1"])
        sql = sdb.fetch_all.call_args[0][0].lower()
        self.assertIn("enrich.listing_health", sql)
        self.assertNotIn("insert", sql)
        self.assertNotIn("update", sql)
        self.assertEqual(d["MLM1"]["c"], {"estado": "medida", "nivel": "bueno", "texto": "Profesional", "puntaje": 76.0,
                                          "medido": ahora.isoformat(), "pendientes": 2, "motivo": None})
        self.assertEqual((d["MLM1"]["e"]["texto"], d["MLM1"]["e"]["color"], d["MLM1"]["e"]["razon"]), ("Mala", "red", "Kits incompletos"))


class Motivos(Base):
    """Motivos de cancelaciones y devoluciones (v0.659.0): devolución de kubera, cancelación de ML, y nada se inventa."""

    ORDENES = [
        {"orden": "2001", "cuenta": "BEKURA", "dia": "2026-09-07", "cancelada": True, "piezas": 1, "precio": 1368},
        {"orden": "2002", "cuenta": "BEKURA", "dia": "2026-09-07", "cancelada": True, "piezas": 2, "precio": 1368},
        {"orden": "2003", "cuenta": "SANCORFASHION", "dia": "2026-09-08", "cancelada": False, "piezas": 1, "precio": 1305},
        {"orden": "2004", "cuenta": "BEKURA", "dia": "2026-09-09", "cancelada": True, "piezas": 1, "precio": 1305},
    ]
    DEVS = {"2003": {"external_order_id": "2003", "motivo_texto": "No es el color que elegí", "estado_titulo": "Devolución finalizada",
                     "motivo": "warehouse_decision", "tipo": "return"}}

    def test_exige_admin_y_valida_la_publicacion(self):
        anonimo = SimpleNamespace(autenticado=False, tipo="", rol="", actor="?")
        with _como(anonimo), mock.patch.object(pm, "motivos") as m:
            self.assertEqual(_cliente().get("/api/pricing/motivos/MLM123456789").status_code, 401)
            m.assert_not_called()
        with _como(ADMIN), mock.patch.object(pm, "_motivos_kubera") as k:
            self.assertEqual(_cliente().get("/api/pricing/motivos/..%2Fx").status_code, 404)
            self.assertEqual(_cliente().get("/api/pricing/motivos/MLM1;drop").status_code, 404)
            k.assert_not_called()

    def test_junta_devoluciones_y_cancelaciones_sin_inventar(self):
        cancel = {"2001": {"group": "internal", "code": "pack_splitted", "requested_by": "meli"},
                  "2002": {"group": "otro_grupo", "code": "algo_nuevo", "description": "Something new", "requested_by": "buyer"}}
        ml = mock.AsyncMock(return_value=cancel)               # 2004: ML no contestó
        with _como(ADMIN), mock.patch.object(pm, "_motivos_kubera", return_value=(self.ORDENES, self.DEVS)), \
             mock.patch.object(pm, "_cancelaciones_ml", new=ml):
            d = _cliente().get("/api/pricing/motivos/MLM123456789").json()
        ml.assert_awaited_once_with([("2001", "BEKURA"), ("2002", "BEKURA"), ("2004", "BEKURA")])   # la devuelta no va a ML
        ev = {e["orden"]: e for e in d["eventos"]}
        self.assertEqual((ev["2001"]["tipo"], ev["2001"]["quien"]), ("cancelacion", "Mercado Libre"))
        self.assertIn("separó el carrito", ev["2001"]["motivo"])
        self.assertEqual(ev["2002"]["motivo"], "otro_grupo · Something new")    # código y grupo desconocidos: el texto de ML tal cual
        self.assertEqual(pm._texto_cancelacion({"group": "mediations", "code": "x", "requested_by": "buyer"}),
                         ("Cancelada por una mediación (reclamo del comprador)", "comprador"))
        self.assertEqual((ev["2003"]["tipo"], ev["2003"]["motivo"], ev["2003"]["estado"]),
                         ("devolucion", "No es el color que elegí", "Devolución finalizada"))
        self.assertIsNone(ev["2004"]["motivo"])                                  # sin dato de ML: no se inventa
        self.assertEqual((d["kubera"], d["ml"], d["sin_consultar"]), (True, True, 0))

    def test_tope_de_consultas_y_kubera_caida(self):
        muchas = [{"orden": str(9000 + i), "cuenta": "BEKURA", "dia": "2026-09-01", "cancelada": True, "piezas": 1, "precio": 10}
                  for i in range(pm.MAX_ML_MOTIVOS + 5)]
        ml = mock.AsyncMock(return_value={})
        with _como(ADMIN), mock.patch.object(pm, "_motivos_kubera", return_value=(muchas, {})), \
             mock.patch.object(pm, "_cancelaciones_ml", new=ml):
            d = _cliente().get("/api/pricing/motivos/MLM123456789").json()
        self.assertEqual(len(ml.await_args[0][0]), pm.MAX_ML_MOTIVOS)
        self.assertEqual(d["sin_consultar"], 5)
        pm.olvidar()
        with _como(ADMIN), mock.patch.object(pm, "_motivos_kubera", side_effect=pm.FalloFuente("kubera caída")):
            r = _cliente().get("/api/pricing/motivos/MLM123456789")
        self.assertEqual((r.status_code, r.json()["kubera"], r.json()["eventos"]), (200, False, []))

    def test_la_consulta_a_kubera_solo_lee(self):
        sdb = SimpleNamespace(disponible=lambda: True, fetch_all=mock.Mock(side_effect=[self.ORDENES, list(self.DEVS.values())]))
        with mock.patch.dict(sys.modules, {"services.supabase_db": sdb}), mock.patch("services.supabase_db", sdb, create=True):
            ordenes, devs = pm._motivos_kubera("MLM123456789")
        for llamada in sdb.fetch_all.call_args_list:
            sql = llamada[0][0].lower()
            self.assertNotIn("insert", sql)
            self.assertNotIn("update ", sql)
            self.assertNotIn("delete", sql)
        self.assertEqual((len(ordenes), list(devs)), (4, ["2003"]))


class Fresco(Base):
    """v0.656.0: «en vivo» se recalcula SOLO cuando entra una venta (o a los 5 min); promociones en segundo plano."""

    def _poner(self):
        self.poner_foto({**FOTO, "pubs": [{"id": "MLM1", "c": "BEKURA"}]})

    def test_recalcula_solo_con_venta_nueva(self):
        self._poner()
        stock = mock.AsyncMock(return_value={"MLM1": {"stock": 3, "status": "active"}})
        marcas = iter(["2026-10-16T10:00:00", "2026-10-16T10:00:00", "2026-10-16T10:05:00"])
        with _como(ADMIN), mock.patch.object(pm, "_stock_vivo", new=stock),              mock.patch.object(pm, "_ventas_vivo", return_value={}), mock.patch.object(pm, "_ultimas", return_value=[]),              mock.patch.object(pm, "_marca_ventas", side_effect=lambda: next(marcas)):
            cli = _cliente()
            for _ in range(3):
                cli.get("/api/pricing/vivo")
        self.assertEqual(stock.await_count, 2)          # 1ª lectura + la de la venta nueva; la de en medio fue caché

    def test_promos_se_releen_en_segundo_plano_y_mientras_sirve_la_foto(self):
        import asyncio
        self._poner()
        (self.dir / "promos.json").write_text(json.dumps({"leido": "foto", "promos": {"MLM1": {"cobra": 10}}}), encoding="utf-8")
        llamado = asyncio.Event()

        async def refrescar():
            pm._promos = (pm.time.monotonic(), {"leido": "ml", "promos": {"MLM1": {"cobra": 9}}, "origen": "ml"})
            llamado.set()

        async def correr():
            with mock.patch.object(pm, "_refrescar_promos", new=refrescar):
                primero = await pm.promos_actuales()
                await asyncio.wait_for(llamado.wait(), 2)
                segundo = await pm.promos_actuales()
            return primero, segundo

        primero, segundo = asyncio.run(correr())
        self.assertEqual((primero["origen"], primero["promos"]["MLM1"]["cobra"]), ("foto", 10))
        self.assertEqual((segundo["origen"], segundo["promos"]["MLM1"]["cobra"]), ("ml", 9))

    def test_candidatas_y_relampago(self):
        from datetime import datetime
        ahora = datetime.fromisoformat("2026-10-16T12:00:00-06:00")
        promos = [
            {"id": "LGH-MLM1000", "type": "LIGHTNING", "status": "candidate", "suggested_discounted_price": 850,
             "min_discounted_price": 200, "max_discounted_price": 900, "stock": {"min": 5, "max": 6}},
            {"id": "P-BF", "type": "DEAL", "status": "candidate", "name": "Buen Fin 26", "suggested_discounted_price": 700},
            {"id": "C-1", "type": "SELLER_COUPON_CAMPAIGN", "status": "candidate", "fixed_amount": 39},
            {"id": "V-1", "type": "VOLUME", "status": "candidate"},
        ]
        r = pm.resumir({"amount": 1000, "regular_amount": None, "metadata": {}}, promos, ahora)
        self.assertTrue(r["relampago"])
        self.assertEqual([c["tipo"] for c in r["candidatas"]], ["LIGHTNING", "DEAL"])   # cupón y volumen no son «puede entrar»
        self.assertEqual((r["candidatas"][0]["sugerido"], r["candidatas"][0]["stock_max"]), (850, 6))
        self.assertIsNone(r["por"])                                                        # sin promoción que ponga precio
        r = pm.resumir({"amount": 1000, "metadata": {}}, promos[1:], ahora)
        self.assertFalse(r["relampago"])


class PrecioConCupon(unittest.TestCase):
    """v0.662.0: el precio con cupón respeta compra mínima y tope (caso TEC-1804-BLN: $1,599 − $39 = $1,560)."""

    def test_mejor_cupon(self):
        cupones = [{"monto": 39, "pct": None, "minimo": 399}, {"monto": 29, "pct": None, "minimo": 299},
                   {"monto": None, "pct": 10, "minimo": 399, "tope": 100}]
        self.assertEqual(pm.mejor_cupon(1599, cupones), 1499)          # 10 % topado a $100 gana a $39
        self.assertEqual(pm.mejor_cupon(1599, cupones[:2]), 1560)
        self.assertEqual(pm.mejor_cupon(350, cupones), 321)            # a $350 solo alcanza el de $29
        self.assertIsNone(pm.mejor_cupon(250, cupones))                # no llega a ninguna compra mínima
        self.assertIsNone(pm.mejor_cupon(1599, []))

    def test_resumir_toma_minimo_de_la_campana(self):
        from datetime import datetime
        ahora = datetime.fromisoformat("2026-10-16T12:00:00-06:00")
        promos = [{"id": "C-1", "type": "SELLER_COUPON_CAMPAIGN", "status": "candidate", "fixed_amount": 39,
                   "start_date": "2026-10-01T00:00:00", "finish_date": "2026-10-31T23:59:59"}]
        sp = {"amount": 1599, "regular_amount": 3655.95, "metadata": {}}
        self.assertEqual(pm.resumir(sp, promos, ahora, {"C-1": {"min_purchase_amount": 399}})["con_cupon"], 1560)
        self.assertIsNone(pm.resumir({**sp, "amount": 300}, promos, ahora, {"C-1": {"min_purchase_amount": 399}})["con_cupon"])


class ScriptsHechoYPromos(unittest.TestCase):
    def test_bitacora_a_hechos_sin_lo_revertido_ni_lo_fallido(self):
        from scripts import pricing_hechos as ph
        base = {"cuenta": "BEKURA", "sku": "A", "cobraba": 200.0, "normal": 400.0, "nuevo": 180, "regla": "sv_extra",
                "cuerpo": {"promotion_type": "PRICE_DISCOUNT", "finish_date": "2026-11-11T23:59:59"}, "cuando": "2026-10-15T17:00:00-06:00"}
        bit = [{**base, "id": "M1", "accion": "aplicar", "ok": True},
               {**base, "id": "M2", "accion": "aplicar", "ok": False},
               {**base, "id": "M3", "accion": "aplicar", "ok": True},
               {"id": "M3", "accion": "revertir", "ok": True},
               {"accion": "hecho_manual", "id": "M4", "cuenta": "BEKURA", "sku": "B", "antes": 10, "nuevo": 9, "desde": "2026-10-15T18:00:00-06:00"}]
        hs = ph.armar(bit, [{"id": "M1", "se_ve_descuento": False}])
        self.assertEqual([h["id"] for h in hs], ["M1", "M4"])
        self.assertEqual((hs[0]["antes"], hs[0]["nuevo"], hs[0]["hasta"], hs[0]["se_ve"]), (200.0, 180, "2026-11-11", False))

    def test_resumir_dice_que_promocion_cobra(self):
        from datetime import datetime
        from scripts import pricing_promos as pp
        ahora = datetime.fromisoformat("2026-10-15T19:00:00-06:00")
        promos = [
            {"id": "LGH-MLM1000", "type": "LIGHTNING", "status": "started", "price": 90},
            {"type": "PRICE_DISCOUNT", "status": "started", "price": 166},
            {"id": "C-1", "type": "SELLER_COUPON_CAMPAIGN", "status": "candidate", "fixed_amount": 39,
             "start_date": "2026-09-24T00:00:00", "finish_date": "2026-10-24T23:59:59", "name": "Seguidores"},
            {"id": "C-2", "type": "SELLER_COUPON_CAMPAIGN", "status": "candidate", "fixed_amount": 29,
             "start_date": "2026-09-01T00:00:00", "finish_date": "2026-10-01T23:59:59", "name": "Vencido"},
            {"id": "V-1", "type": "VOLUME", "status": "started", "buy_quantity": 2, "discount_percentage": 10, "name": "MAYOREO"},
            {"id": "P-BF", "type": "DEAL", "status": "pending", "price": 150, "name": "Buen Fin 26", "start_date": "2026-11-12T22:00:00-06:00"},
        ]
        r = pp.resumir({"amount": 90, "regular_amount": 175.75, "metadata": {"promotion_type": "lightning"}}, promos, ahora)
        self.assertEqual(r["por"]["etiqueta"], "Oferta relámpago")
        self.assertEqual([c["monto"] for c in r["cupones"]], [39])            # el cupón vencido no cuenta
        self.assertEqual((r["volumen"]["lleva"], r["volumen"]["pct"]), (2, 10))
        self.assertEqual(r["programadas"][0]["nombre"], "Buen Fin 26")
        r = pp.resumir({"amount": 166, "regular_amount": 175.75, "metadata": {"promotion_id": "OFFER-X", "promotion_type": "custom"}},
                       promos[1:], ahora)
        self.assertEqual(r["por"]["etiqueta"], "Descuento individual")


if __name__ == "__main__":
    unittest.main()
