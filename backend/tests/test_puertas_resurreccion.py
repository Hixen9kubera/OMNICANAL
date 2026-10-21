"""Pruebas de las PUERTAS DE RESURRECCIÓN cerradas en la salida de Odoo/Woo (1.9).

── POR QUÉ ────────────────────────────────────────────────────────────────────
Odoo salió de la operación el 15-oct-2026. Tres caminos podían volver a ofrecer
mercancía vendida —copiar un número viejo o absoluto encima del que ya bajaron
las ventas— o correr un vigilante que alguien apagó a propósito:

  (a) `POST /api/fanout/inventario/revisar?forzar=true` corría `stock_watch`
      ESTANDO APAGADO. Ahora apagado contesta 409, con o sin `forzar`; `forzar`
      sólo deja pasar el tope (la excepción del acta de T0).
  (b) `POST /api/sync/woo` (`services/sync_woo.py`) copiaba el stock ABSOLUTO de
      Odoo a Woo para todo el catálogo. Ahora la ruta contesta 410 y el módulo
      no existe.
  (c) `odoo_watch` (foto de Odoo → campana y, con `auto_push`, el valor absoluto
      de Odoo a Woo). Ni módulo, ni job, ni banderas, ni censo del espejo.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
Que ninguna de las tres vuelva por la puerta de atrás: un import, un job del
scheduler, una llave en una respuesta o una entrada del censo.

Sin red y sin base: `stock_watch` va suplantado y las rutas se prueban con un
FastAPI mínimo.

    cd backend && python -m unittest tests.test_puertas_resurreccion -v
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from config import settings  # noqa: E402


def _cliente(router_mod) -> TestClient:
    app = FastAPI()
    app.include_router(router_mod.router)
    return TestClient(app)


# ═════════════════════════════════════════════════════════════════════════════
# (a) forzar no enciende al vigilante
# ═════════════════════════════════════════════════════════════════════════════

class ForzarNoEnciende(unittest.TestCase):
    def setUp(self) -> None:
        from routers import fanout
        from services import stock_watch
        self.sw = stock_watch
        self.cli = _cliente(fanout)
        self.revisar = mock.AsyncMock(return_value={"estado": "ok"})

    def _post(self, habilitado: bool, query: str = ""):
        with mock.patch.object(self.sw, "habilitado", return_value=habilitado), \
                mock.patch.object(self.sw, "revisar", self.revisar):
            return self.cli.post(f"/api/fanout/inventario/revisar{query}")

    def test_apagado_y_forzado_contesta_409_sin_correr(self):
        r = self._post(False, "?forzar=true")
        self.assertEqual(r.status_code, 409)
        det = r.json()["detail"]
        self.assertEqual(det["estado"], "apagado")
        self.assertTrue(det["forzar"])
        self.assertIn("STOCK_WATCH_ENABLED", det["motivo"])
        self.revisar.assert_not_awaited()

    def test_apagado_sin_forzar_tambien_409(self):
        r = self._post(False)
        self.assertEqual(r.status_code, 409)
        self.assertFalse(r.json()["detail"]["forzar"])
        self.revisar.assert_not_awaited()

    def test_encendido_forzar_sigue_pasando_el_tope(self):
        r = self._post(True, "?forzar=true")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"estado": "ok"})
        self.revisar.assert_awaited_once_with(forzar=True)

    def test_encendido_sin_forzar(self):
        r = self._post(True)
        self.assertEqual(r.status_code, 200)
        self.revisar.assert_awaited_once_with(forzar=False)

    def test_el_docstring_ya_no_ofrece_correrlo_apagado(self):
        from routers import fanout
        doc = fanout.inventario_revisar.__doc__ or ""
        self.assertIn("409", doc)
        self.assertNotIn("correrlo estando apagado (una pasada", doc)


# ═════════════════════════════════════════════════════════════════════════════
# (b) /api/sync/woo retirado
# ═════════════════════════════════════════════════════════════════════════════

class SyncWooRetirado(unittest.TestCase):
    def setUp(self) -> None:
        from routers import sync
        self.cli = _cliente(sync)

    def test_post_contesta_410_con_el_porque(self):
        r = self.cli.post("/api/sync/woo?limite=5")
        self.assertEqual(r.status_code, 410)
        det = r.json()["detail"]
        self.assertTrue(det["retirado"])
        self.assertIn("retirado", det["motivo"])
        self.assertIn("stock absoluto de Odoo a Woo", det["motivo"])
        self.assertIn("15-oct", det["motivo"])

    def test_progreso_contesta_410(self):
        r = self.cli.get("/api/sync/woo/progreso")
        self.assertEqual(r.status_code, 410)
        self.assertTrue(r.json()["detail"]["retirado"])

    def test_el_modulo_ya_no_existe(self):
        self.assertIsNone(importlib.util.find_spec("services.sync_woo"))

    def test_la_regla_de_rbac_se_queda_mientras_la_ruta_exista(self):
        from core import rbac
        self.assertEqual(rbac.rol_requerido("POST", "/api/sync/woo"), "admin")

    def test_me_ya_no_anuncia_sync_masivo(self):
        from routers import auth
        quien = SimpleNamespace(autenticado=True, actor="prueba@ejemplo.mx", tipo="persona",
                                rol="admin", id=None)
        r = auth.me(SimpleNamespace(state=SimpleNamespace(identidad=quien)))
        self.assertNotIn("sync_masivo", r["puede"])
        self.assertIn("publicar", r["puede"])


# ═════════════════════════════════════════════════════════════════════════════
# (c) odoo_watch retirado
# ═════════════════════════════════════════════════════════════════════════════

class _SchedFalso:
    def __init__(self, *a, **k):
        self.jobs: list[tuple[tuple, dict]] = []

    def add_job(self, *a, **k):
        self.jobs.append((a, k))

    def start(self):
        pass


class OdooWatchRetirado(unittest.TestCase):
    def test_el_modulo_ya_no_existe(self):
        self.assertIsNone(importlib.util.find_spec("services.odoo_watch"))

    def test_el_scheduler_no_lo_registra_ni_con_mysql(self):
        from services import scheduler as sch
        viejo = sch._scheduler
        sch._scheduler = None
        try:
            with mock.patch.object(sch, "AsyncIOScheduler", _SchedFalso), \
                    mock.patch.object(sch.settings, "mysql_enabled", True):
                sch.iniciar()
                ids = {k.get("id") for _a, k in sch._scheduler.jobs}
        finally:
            sch._scheduler = viejo
        self.assertNotIn("odoo_watch", ids)
        self.assertTrue(ids, "el scheduler falso debió registrar otros jobs")

    def test_sus_banderas_ya_no_existen_y_no_estorban(self):
        for nombre in ("odoo_watch_enabled", "odoo_watch_min", "odoo_watch_auto_push"):
            self.assertFalse(hasattr(settings, nombre), nombre)
        from config import Settings
        # Si siguen puestas en Railway no truenan: `extra="ignore"`.
        with mock.patch.dict(os.environ, {"ODOO_WATCH_ENABLED": "true",
                                          "ODOO_WATCH_AUTO_PUSH": "true"}):
            s = Settings(_env_file=None)
        self.assertFalse(hasattr(s, "odoo_watch_auto_push"))

    def test_estado_de_webhooks_sin_la_llave(self):
        from routers import webhooks
        with mock.patch.object(webhooks.sdb, "disponible", return_value=False):
            r = asyncio.run(webhooks.estado_registro())
        self.assertNotIn("odoo_watch", r)
        self.assertIn("registro_activo", r)

    def test_fuera_del_censo_del_espejo(self):
        from services import kubera_mirror
        archivos = {e["archivo"] for e in kubera_mirror.CENSO}
        self.assertNotIn("services/odoo_watch.py", archivos)
        self.assertNotIn("services/sync_woo.py", archivos)


class NadieLosResucita(unittest.TestCase):
    """Ningún módulo del backend (ni script) los importa ni lee sus banderas."""

    RETIRADOS = {"odoo_watch", "sync_woo"}

    def _archivos(self):
        for carpeta in ("core", "routers", "services", "scripts"):
            yield from sorted((BACKEND / carpeta).rglob("*.py"))
        yield BACKEND / "main.py"
        yield BACKEND / "config.py"

    def test_sin_imports_ni_banderas(self):
        malos = []
        for ruta in self._archivos():
            arbol = ast.parse(ruta.read_text(encoding="utf-8-sig"), filename=str(ruta))
            for n in ast.walk(arbol):
                if isinstance(n, ast.ImportFrom):
                    mod = n.module or ""
                    if mod.split(".")[-1] in self.RETIRADOS or any(
                            a.name in self.RETIRADOS for a in n.names):
                        malos.append(f"{ruta.name}:{n.lineno} from {mod} import …")
                elif isinstance(n, ast.Import):
                    malos += [f"{ruta.name}:{n.lineno} import {a.name}" for a in n.names
                              if a.name.split(".")[-1] in self.RETIRADOS]
                elif isinstance(n, ast.Attribute) and n.attr.startswith("odoo_watch_"):
                    malos.append(f"{ruta.name}:{n.lineno} .{n.attr}")
        self.assertEqual(malos, [])


if __name__ == "__main__":
    unittest.main()
