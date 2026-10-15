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

    def poner_foto(self, foto=FOTO):
        (self.dir / "img").mkdir(exist_ok=True)
        (self.dir / "monitor.json.gz").write_bytes(gzip.compress(json.dumps(foto).encode()))
        (self.dir / "img" / "p0.jpg").write_bytes(JPEG)


class Acceso(Base):
    def test_regla_rbac_admin_y_sin_prefijo_que_la_tape(self):
        from core import rbac
        self.assertIn(("GET", "/api/pricing", "admin"), rbac.REGLAS)
        self.assertEqual(rbac.rol_requerido("GET", "/api/pricing/monitor"), "admin")
        self.assertEqual(rbac.rol_requerido("GET", "/api/pricing/img/p0"), "admin")
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


if __name__ == "__main__":
    unittest.main()
