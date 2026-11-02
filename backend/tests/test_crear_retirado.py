"""Pruebas del RETIRO de la pestaña «Crear Productos» (2-nov-2026).

── POR QUÉ ────────────────────────────────────────────────────────────────────
Eduardo: saltarse por completo el estado draft. Los borradores aparecen directo en
el Publicador (/productos) y ahí se trabajan; la pestaña /crear se apagó. Con ella
se fueron las rutas que SOLO le servían a esa pestaña y al alta vieja Odoo→Woo
(Odoo salió el 15-oct): ahora contestan 410 con el porqué, el mismo molde que
`/api/sync/woo` (tests.test_puertas_resurreccion).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
  1. Las siete rutas retiradas contestan 410, apuntan al Publicador y no tocan
     ningún servicio (ni Odoo, ni Woo, ni la cola en memoria).
  2. El router SIGUE MONTADO con todo lo compartido: el Estudio del Publicador y
     /costos guardan categoría ML, GTIN y costos por /api/crear. Si alguien
     desmontara el router o borrara una de esas rutas, el Estudio se rompería sin
     que nadie lo notara aquí.
  3. El alta automática `alta_skus_odoo` nace apagada (sin la variable no hay job).
  4. El arranque ya no precalienta el índice de drafts de Crear.
  5. Nadie del frontend (fuera de lib/api.ts) vuelve a llamar a las rutas retiradas.

Sin red y sin base: las rutas se prueban con un FastAPI mínimo y el scheduler con
el planificador suplantado.

    cd backend && python -m unittest tests.test_crear_retirado -v
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from fastapi import FastAPI  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import crear as C  # noqa: E402

FRONTEND = BACKEND.parent / "frontend"

# (método, ruta) que servían SOLO a la pestaña Crear y al alta Odoo→Woo.
RETIRADAS = [
    ("GET", "/api/crear/drafts/plan"),
    ("POST", "/api/crear/drafts/sincronizar"),
    ("GET", "/api/crear/candidatos"),
    ("POST", "/api/crear/productos"),
    ("POST", "/api/crear/destrabar"),
    ("GET", "/api/crear/progreso"),
    ("GET", "/api/crear/categorias"),
]

# (método, ruta declarada) que usan el Estudio, /costos y la bitácora: NO se retiran.
COMPARTIDAS = [
    ("POST", "/api/crear/gtin"),
    ("POST", "/api/crear/categoria-ml"),
    ("GET", "/api/crear/categorias-ml"),
    ("GET", "/api/crear/categorias-ml/{cat_id}"),
    ("GET", "/api/crear/costos"),
    ("GET", "/api/crear/costos/_contenedores"),
    ("GET", "/api/crear/costos/_embarques"),
    ("GET", "/api/crear/costos/{sku:path}"),
    ("POST", "/api/crear/costos/{sku:path}/preview"),
    ("POST", "/api/crear/costos/{sku:path}/revisar"),
    ("DELETE", "/api/crear/costos/{sku:path}/revisar"),
    ("POST", "/api/crear/costos/{sku:path}/recalcular"),
    ("POST", "/api/crear/costos/bulk"),
    ("GET", "/api/crear/historial"),
    ("GET", "/api/crear/historial/{sku:path}"),
    ("GET", "/api/crear/auditoria"),
]


def _cliente() -> TestClient:
    app = FastAPI()
    app.include_router(C.router)
    return TestClient(app)


def _declaradas() -> set[tuple[str, str]]:
    return {(m, r.path) for r in C.router.routes if isinstance(r, APIRoute) for m in r.methods}


# ═════════════════════════════════════════════════════════════════════════════
# 1. Las rutas de la pestaña contestan 410
# ═════════════════════════════════════════════════════════════════════════════

class RutasRetiradas(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _cliente()

    def test_las_siete_contestan_410_y_mandan_al_publicador(self):
        for metodo, ruta in RETIRADAS:
            with self.subTest(ruta=f"{metodo} {ruta}"):
                r = self.cli.request(metodo, ruta)
                self.assertEqual(r.status_code, 410)
                det = r.json()["detail"]
                self.assertTrue(det["retirado"])
                self.assertIn("retirado", det["motivo"])
                self.assertIn("Publicador", det["motivo"])
                self.assertEqual(det["ir_a"], "/productos")

    def test_la_llamada_vieja_con_su_cuerpo_tambien_es_410_y_no_422(self):
        # El botón «Crear» mandaba sku + wc_id + URL de Alibaba. Un 422 diría «te
        # faltó un campo»; lo que hay que decir es que la ruta ya no existe.
        r = self.cli.post("/api/crear/productos", json={
            "items": [{"sku": "TEC-0001", "wc_id": 1, "alibaba_url": "https://x"}],
            "permitir_sin_costo": False})
        self.assertEqual(r.status_code, 410)
        r = self.cli.post("/api/crear/drafts/sincronizar?limite=5")
        self.assertEqual(r.status_code, 410)
        r = self.cli.post("/api/crear/destrabar?aplicar=true&max_items=10")
        self.assertEqual(r.status_code, 410)

    def test_no_tocan_ningun_servicio(self):
        from services import creacion, crear_producto, woocommerce
        espias = {
            "plan_drafts": mock.AsyncMock(), "sincronizar_drafts": mock.AsyncMock(),
            "listar_candidatos_agrupados": mock.AsyncMock(),
            "encolar": mock.MagicMock(), "destrabar_inprogress": mock.AsyncMock(),
            "progreso": mock.MagicMock(), "_cargar_categorias": mock.AsyncMock(),
        }
        with mock.patch.object(creacion, "plan_drafts", espias["plan_drafts"]), \
                mock.patch.object(creacion, "sincronizar_drafts", espias["sincronizar_drafts"]), \
                mock.patch.object(creacion, "listar_candidatos_agrupados",
                                  espias["listar_candidatos_agrupados"]), \
                mock.patch.object(crear_producto, "encolar", espias["encolar"]), \
                mock.patch.object(crear_producto, "destrabar_inprogress",
                                  espias["destrabar_inprogress"]), \
                mock.patch.object(crear_producto, "progreso", espias["progreso"]), \
                mock.patch.object(woocommerce, "_cargar_categorias", espias["_cargar_categorias"]):
            for metodo, ruta in RETIRADAS:
                self.assertEqual(self.cli.request(metodo, ruta).status_code, 410)
        for nombre, espia in espias.items():
            with self.subTest(servicio=nombre):
                self.assertEqual(espia.call_count, 0)

    def test_el_router_ya_no_importa_el_alta_odoo(self):
        # `creacion` (plan/sincronizar drafts y la lista de candidatos) sólo lo usaban
        # las rutas retiradas. Si vuelve a aparecer aquí, alguien revivió una.
        self.assertFalse(hasattr(C, "creacion"))

    def test_las_reglas_de_rbac_se_quedan_mientras_la_ruta_exista(self):
        from core import rbac
        self.assertEqual(rbac.rol_requerido("POST", "/api/crear/destrabar"), "admin")
        self.assertEqual(rbac.rol_requerido("POST", "/api/crear/productos"), "operador")


# ═════════════════════════════════════════════════════════════════════════════
# 2. Lo compartido sigue montado (y vivo)
# ═════════════════════════════════════════════════════════════════════════════

class RutasCompartidas(unittest.TestCase):
    def test_siguen_declaradas(self):
        declaradas = _declaradas()
        for metodo, ruta in COMPARTIDAS:
            with self.subTest(ruta=f"{metodo} {ruta}"):
                self.assertIn((metodo, ruta), declaradas)

    def test_sus_handlers_corren_y_no_contestan_410(self):
        cli = _cliente()
        # Las dos validan ANTES de ir a la red: si contestan su 422, el handler de
        # verdad está montado (no el de retiro).
        r = cli.post("/api/crear/gtin", json={"wc_id": 1, "gtin": "123"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("GTIN", r.json()["detail"])
        r = cli.post("/api/crear/categoria-ml", json={"wc_id": 1, "category_id": "  "})
        self.assertEqual(r.status_code, 422)
        self.assertIn("category_id", r.json()["detail"])

    def test_lo_que_importan_otros_modulos_sigue_existiendo(self):
        # crear_producto se queda entero aunque la pestaña muera: lo importan
        # ia_generadores, studio, channel_read, bitacora_read y costing_mirror.
        from services import crear_producto as cp
        for nombre in ("_persistir_log", "_accion_y_mensaje", "_categoria_curada",
                       "_foto_data_url", "_OR_URL", "OPENROUTER_MODELOS_SEO",
                       "asegurar_schema_logs"):
            with self.subTest(nombre=nombre):
                self.assertTrue(hasattr(cp, nombre))
        self.assertTrue(callable(C._sync_woo_costo))      # el guardado de costos del Estudio

    def test_main_sigue_montando_el_router(self):
        arbol = ast.parse((BACKEND / "main.py").read_text(encoding="utf-8-sig"))
        montados = {ast.unparse(n.args[0]) for n in ast.walk(arbol)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "include_router" and n.args}
        self.assertIn("crear.router", montados)


# ═════════════════════════════════════════════════════════════════════════════
# 3. El alta automática Odoo→Woo nace apagada
# ═════════════════════════════════════════════════════════════════════════════

class AltaSkusOdooApagada(unittest.TestCase):
    def test_el_default_es_false(self):
        from config import Settings
        self.assertIs(Settings.model_fields["sync_odoo_skus_enabled"].default, False)

    def _jobs(self, encendido: bool) -> set[str]:
        try:
            from services import scheduler
        except ImportError as exc:            # sin APScheduler no hay scheduler que probar
            self.skipTest(f"no se puede importar el scheduler: {exc}")
        from services import alertas
        falso = mock.MagicMock()
        with mock.patch.object(scheduler, "AsyncIOScheduler", return_value=falso), \
                mock.patch.object(scheduler, "_scheduler", None), \
                mock.patch.object(alertas, "disponible", return_value=False), \
                mock.patch.object(scheduler.settings, "sync_odoo_skus_enabled", encendido):
            scheduler.iniciar()
        return {c.kwargs.get("id") for c in falso.add_job.call_args_list}

    def test_apagado_no_se_programa(self):
        self.assertNotIn("alta_skus_odoo", self._jobs(False))

    def test_la_variable_sigue_siendo_el_interruptor(self):
        # Sin esto, la prueba de arriba pasaría aunque el job se llamara distinto.
        self.assertIn("alta_skus_odoo", self._jobs(True))


# ═════════════════════════════════════════════════════════════════════════════
# 4. El arranque ya no precalienta el índice de Crear
# ═════════════════════════════════════════════════════════════════════════════

class SinCalentamientoDeCrear(unittest.TestCase):
    def test_main_no_llama_indice_candidatos(self):
        fuente = (BACKEND / "main.py").read_text(encoding="utf-8-sig")
        self.assertNotIn("indice_candidatos", fuente)


# ═════════════════════════════════════════════════════════════════════════════
# 5. El frontend ya no llama a las rutas retiradas
# ═════════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(FRONTEND.is_dir(), "sin frontend/ junto al backend")
class FrontendSinLlamadas(unittest.TestCase):
    # Las funciones de lib/api.ts que pegan a las rutas retiradas. Pueden seguir
    # declaradas ahí; lo que no puede volver es alguien que las USE.
    FUNCIONES = ("listarCandidatos", "planDrafts", "sincronizarDrafts",
                 "categoriasDisponibles", "progresoCreacion", "crearProductos")

    def _fuentes(self):
        for p in list(FRONTEND.glob("app/**/*.tsx")) + list(FRONTEND.glob("components/**/*.tsx")) \
                + list(FRONTEND.glob("lib/**/*.ts")):
            if p.name == "api.ts" and p.parent.name == "lib":
                continue
            yield p, p.read_text(encoding="utf-8", errors="replace")

    def test_nadie_usa_las_funciones_de_las_rutas_retiradas(self):
        patron = re.compile(r"\b(" + "|".join(self.FUNCIONES) + r")\b")
        usos = [f"{p.relative_to(FRONTEND)}: {m.group(1)}"
                for p, texto in self._fuentes() for m in patron.finditer(texto)]
        self.assertEqual(usos, [])

    def test_la_barra_ya_no_lleva_a_crear(self):
        barra = (FRONTEND / "components" / "AppNavbar.tsx").read_text(encoding="utf-8")
        self.assertNotIn('href: "/crear"', barra)
        self.assertNotIn('label: "Crear Productos"', barra)

    def test_crear_redirige_al_publicador(self):
        pagina = (FRONTEND / "app" / "crear" / "page.tsx").read_text(encoding="utf-8")
        # Con el filtro «Solo borradores» puesto: la misma cola que había en Crear.
        self.assertIn('"/productos?borradores=1"', pagina)
        self.assertIn("router.replace", pagina)
        publicador = (FRONTEND / "app" / "productos" / "page.tsx").read_text(encoding="utf-8")
        self.assertIn('get("borradores") === "1"', publicador)


if __name__ == "__main__":
    unittest.main()
