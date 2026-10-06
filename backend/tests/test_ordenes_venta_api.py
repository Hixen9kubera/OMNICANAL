"""Pruebas de la API de las órdenes de venta propias (routers/ordenes_venta.py), del
bus en memoria (services/ov_bus.py), del job del scheduler y de lo que el barrido
de cancelaciones del canal (services/ov_auto.py) promete SIN base. Sin red y sin
kubera: el servicio va sustituido por dobles con su FIRMA real (autospec). Lo que
toca Postgres —el servicio, el barrido y la API de punta a punta— está en
tests/test_ordenes_venta_bd.py.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
  1. REGLA 11, de tres maneras: estática (el AST del router: ningún `async def`
     llama directo al servicio ni al barrido; toda función suya viaja como
     argumento de `_hilo`), de fontanería (`_hilo` es `asyncio.to_thread`) y en
     ejecución (el doble del servicio corre en un hilo que NO es el del loop —
     también el nombre visible, que lee la base).
  2. Cada `ErrorOV` sale con SU código y su mensaje; lo inesperado es un 502
     genérico que no filtra el detalle; kubera caída, un 502 de una línea.
  3. Quién es quién: persona (con su nombre, o lo de antes de la arroba),
     máquina (`api`) y máquina con `X-Origen: claude` o User-Agent de Claude;
     sin credencial es admin sólo con AUTH_ENFORCED apagado, y un 401 encendido.
  4. LAS RUTAS SON LAS DE frontend/components/ordenes/api.ts, ni una más: se
     fueron /reservar, /regresar, /devolucion y /auto; llegaron /salio y
     /salio-tarde. El PUT es «guardar borrador» y le pasa al servicio SÓLO las
     llaves que llegaron (la bodega va POR RENGLÓN; el «almacén» de encabezado
     ya no existe). Entregar viaja con o sin `lineas`.
  5. Las rutas fijas no se las come `/{ref}`, y un folio no entra donde va un id.
  6. El PDF: lleva su `tipo` (campo del formulario) tal cual al servicio; vacío
     o que no es PDF → 400, de más → 413 ANTES de leer el cuerpo, y ninguno
     llega al servicio. La descarga trae sus cabeceras y exige quién.
  7. El chat en vivo: contesta al instante si hay mensajes; con `esperar` y sin
     mensajes DESPIERTA cuando alguien escribe y también VENCE; un aviso entre la
     lectura y la espera no se pierde; el tope global y el tope por identidad
     degradan a sondeo; si el navegador se va, la espera se suelta (a nivel ASGI).
  8. Toda escritura avisa al bus; un error no avisa.
  9. `/estado` es EstadoModulo tal cual lo arma el servicio (sin interruptor
     movible) y `/conciliar` es de quien escribe y avisa de canceladas y marcadas.
 10. EL JOB DEL BARRIDO SE REGISTRA SIEMPRE y decide EN CADA PASADA con la
     bandera (`ordenes_venta.habilitado()`, en un hilo): apagada no llama al
     barrido ni escribe log; encendida lo corre en un hilo y avisa al bus.
 11. El bus: versiones que sólo suben, despertar, vencer, los topes, y que
     limpiar el diccionario nunca hace pasar por vigente una versión vieja.
 12. El barrido sin base: el veredicto (cancelada / en camino / con qué
     referencia) con la MISMA regla de la pantalla; `revisar` nunca lanza; con
     la bandera apagada o sin las migraciones contesta `ok: false` con su motivo
     y no llena el log; lo que ya no hace (generar, rellenar, mover banderas)
     ya no está.

    cd backend && python -m unittest tests.test_ordenes_venta_api -v
"""
from __future__ import annotations

import ast
import asyncio
import re
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
ROUTER = BACKEND / "routers" / "ordenes_venta.py"
API_TS = BACKEND.parent / "frontend" / "components" / "ordenes" / "api.ts"
TIPOS_TS = BACKEND.parent / "frontend" / "components" / "ordenes" / "tipos.ts"
sys.path.insert(0, str(BACKEND))

import psycopg2  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request as RequestStarlette  # noqa: E402

from core.identidad import Identidad  # noqa: E402
from routers import ordenes_venta as ruta  # noqa: E402
from services import ordenes_venta as ov  # noqa: E402
from services import ov_auto, ov_bus  # noqa: E402

RAIZ = "/api/ordenes-venta"

PERSONA = Identidad(actor="ana@prueba.test", tipo="persona", rol="operador", id="u-1")
ADMIN = Identidad(actor="ada@prueba.test", tipo="persona", rol="admin", id="u-2")
LECTURA = Identidad(actor="leo@prueba.test", tipo="persona", rol="lectura", id="u-3")
MAQUINA = Identidad(actor="servicio", tipo="maquina", rol="admin")
ANONIMO = Identidad(actor="anonimo", tipo="anonimo", rol="")

PDF = b"%PDF-1.4\n%comprobante de prueba\n" + b"0" * 512


def resp_orden(orden_id: int = 7, **mas) -> dict:
    return {"ok": True, "orden": {"id": orden_id, "folio": f"OV-{orden_id:05d}", "rev": 2, **mas},
            "mensaje": "Hecho."}


def resp_mensajes(*ids: int) -> dict:
    return {"mensajes": [{"id": i, "cuerpo": f"m{i}"} for i in ids],
            "ultimo_id": max(ids, default=0), "total": len(ids), "rev": 1, "estado": "borrador"}


# ══════════════════════════════════════════════════════════════════════════════
# Regla 11, estática: se lee el AST del router ENTERO
# ══════════════════════════════════════════════════════════════════════════════

# Lo que SÍ puede llamarse síncrono desde una corrutina, con su razón: el bus es
# un diccionario en memoria del propio loop; no abre nada.
PERMITIDAS = {
    ("ov_bus", "avisar"),
    ("ov_bus", "version"),
    ("ov_bus", "lleno"),
    ("ov_bus", "en_espera"),
}
# Los módulos que BLOQUEAN (psycopg2, requests a Storage): jamás directo.
BLOQUEANTES = ("ordenes_venta", "ov_auto")


def _alias_de_services(arbol: ast.AST) -> dict[str, str]:
    """`from services import ordenes_venta as ov, ov_bus` → {ov: ordenes_venta, ov_bus: ov_bus}."""
    alias: dict[str, str] = {}
    for n in ast.walk(arbol):
        if isinstance(n, ast.ImportFrom) and n.module == "services":
            for a in n.names:
                alias[a.asname or a.name] = a.name
    return alias


def _funciones(modulo: str, cache: dict[str, dict[str, bool]]) -> dict[str, bool]:
    """nombre → es_async, para las funciones de nivel superior de un servicio."""
    if modulo not in cache:
        p = BACKEND / "services" / f"{modulo}.py"
        encontradas: dict[str, bool] = {}
        if p.exists():
            for n in ast.parse(p.read_text(encoding="utf-8")).body:
                if isinstance(n, ast.FunctionDef):
                    encontradas[n.name] = False
                elif isinstance(n, ast.AsyncFunctionDef):
                    encontradas[n.name] = True
        cache[modulo] = encontradas
    return cache[modulo]


def sincronas_en_corrutinas(ruta_py: Path) -> list[str]:
    """['confirmar:231 ordenes_venta.confirmar', …]: llamadas síncronas a un
    servicio hechas DENTRO de un `async def` (clon de test_regla_11_productos)."""
    arbol = ast.parse(ruta_py.read_text(encoding="utf-8"))
    alias = _alias_de_services(arbol)
    cache: dict[str, dict[str, bool]] = {}
    fuera: list[str] = []
    for fn in ast.walk(arbol):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        padre = {h: n for n in ast.walk(fn) for h in ast.iter_child_nodes(n)}
        for n in ast.walk(fn):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name)):
                continue
            modulo = alias.get(n.func.value.id)
            if modulo is None:
                continue
            if _funciones(modulo, cache).get(n.func.attr) is not False:
                continue
            if isinstance(padre.get(n), ast.Await):
                continue
            if (modulo, n.func.attr) in PERMITIDAS:
                continue
            fuera.append(f"{fn.name}:{n.lineno} {modulo}.{n.func.attr}")
    return sorted(fuera)


def referencias_fuera_del_hilo(ruta_py: Path) -> list[str]:
    """Toda función del servicio o del barrido que un `async def` NOMBRE tiene que
    ir como argumento de `_hilo(...)`. Ni llamada, ni guardada en una variable."""
    arbol = ast.parse(ruta_py.read_text(encoding="utf-8"))
    alias = _alias_de_services(arbol)
    cache: dict[str, dict[str, bool]] = {}
    fuera: list[str] = []
    for fn in ast.walk(arbol):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        padre = {h: n for n in ast.walk(fn) for h in ast.iter_child_nodes(n)}
        for n in ast.walk(fn):
            if not (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)):
                continue
            modulo = alias.get(n.value.id)
            if modulo not in BLOQUEANTES or n.attr not in _funciones(modulo, cache):
                continue
            p = padre.get(n)
            if (isinstance(p, ast.Call) and isinstance(p.func, ast.Name) and p.func.id == "_hilo"
                    and n in p.args):
                continue
            fuera.append(f"{fn.name}:{n.lineno} {modulo}.{n.attr}")
    return sorted(fuera)


def rutas_del_router() -> set[tuple[str, str]]:
    """{(MÉTODO, ruta sin el prefijo)} tal como las declara el router."""
    return {(next(iter(r.methods - {"HEAD", "OPTIONS"})), r.path[len(RAIZ):])
            for r in ruta.router.routes}


class ReglaOnce(unittest.TestCase):
    def test_ninguna_llamada_sincrona_al_servicio_en_una_corrutina(self):
        fuera = sincronas_en_corrutinas(ROUTER)
        self.assertEqual(fuera, [], "Envuélvelas en await _hilo(...): " + ", ".join(fuera))

    def test_el_servicio_y_el_barrido_solo_viajan_dentro_de_hilo(self):
        fuera = referencias_fuera_del_hilo(ROUTER)
        self.assertEqual(fuera, [], "Sólo como argumento de _hilo(...): " + ", ".join(fuera))

    def test_el_detector_si_detecta(self):
        """Un detector que nunca falla no prueba nada: con un router que SÍ llama
        directo, las dos revisiones lo cazan."""
        malo = BACKEND / "tests" / "_ov_router_malo_tmp.py"
        malo.write_text(
            "from services import ordenes_venta as ov\nfrom services import ov_auto\n"
            "async def a(i):\n    return ov.mensajes(i)\n"
            "async def b():\n    f = ov_auto.revisar\n    return f\n", encoding="utf-8")
        try:
            self.assertEqual(sincronas_en_corrutinas(malo), ["a:4 ordenes_venta.mensajes"])
            self.assertEqual(referencias_fuera_del_hilo(malo),
                             ["a:4 ordenes_venta.mensajes", "b:6 ov_auto.revisar"])
        finally:
            malo.unlink()

    def test_todo_handler_es_async_y_hilo_es_to_thread(self):
        arbol = ast.parse(ROUTER.read_text(encoding="utf-8"))
        handlers = [n for n in arbol.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                            and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"
                            for d in n.decorator_list)]
        self.assertEqual(len(handlers), 20, "las 20 rutas de frontend/components/ordenes/api.ts")
        self.assertEqual([h.name for h in handlers if not isinstance(h, ast.AsyncFunctionDef)], [])
        hilo = next(n for n in arbol.body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "_hilo")
        self.assertIn("asyncio.to_thread(fn, *args, **kwargs)", ast.unparse(hilo))


class Rutas(unittest.TestCase):
    """El mapa de rutas, contra el contrato nuevo."""

    ESPERADAS = {
        ("GET", "/estado"), ("GET", ""), ("POST", ""), ("GET", "/skus"),
        ("GET", "/marketplace/venta"), ("GET", "/marketplace/pendientes"),
        ("POST", "/conciliar"), ("GET", "/{ref}"), ("PUT", "/{orden_id:int}"),
        ("POST", "/{orden_id:int}/confirmar"), ("POST", "/{orden_id:int}/entregar"),
        ("POST", "/{orden_id:int}/cancelar"), ("POST", "/{orden_id:int}/salio"),
        ("POST", "/{orden_id:int}/salio-tarde"), ("DELETE", "/{orden_id:int}"),
        ("GET", "/{orden_id:int}/mensajes"), ("POST", "/{orden_id:int}/mensajes"),
        ("POST", "/{orden_id:int}/archivos"),
        ("GET", "/{orden_id:int}/archivos/{archivo_id:int}"),
        ("DELETE", "/{orden_id:int}/archivos/{archivo_id:int}")}

    def test_son_exactamente_las_del_contrato(self):
        self.assertEqual(rutas_del_router(), self.ESPERADAS)

    def test_las_que_se_fueron_ya_no_estan(self):
        rutas = {r for _, r in rutas_del_router()}
        for vieja in ("reservar", "regresar", "devolucion", "auto"):
            self.assertEqual([r for r in rutas if r.rstrip("/").endswith("/" + vieja)], [], vieja)

    def test_las_fijas_van_antes_que_el_detalle(self):
        orden = [r.path[len(RAIZ):] for r in ruta.router.routes]
        for fija in ("/estado", "/skus", "/marketplace/venta", "/marketplace/pendientes",
                     "/conciliar"):
            self.assertLess(orden.index(fija), orden.index("/{ref}"), fija)

    @unittest.skipUnless(API_TS.exists(), "sin frontend/components/ordenes/api.ts")
    def test_cada_llamada_de_api_ts_tiene_su_ruta(self):
        """Las rutas que la pantalla llama (api.ts) existen aquí, con su verbo. Se
        leen las plantillas de `leer(...)`, `mandar(MÉTODO, ...)` y las dos que
        van por `fetchSesion`/`descargar` a mano."""
        texto = API_TS.read_text(encoding="utf-8")

        def forma(plantilla: str) -> str:
            sin_query = re.sub(r"\?.*$", "", plantilla)
            return re.sub(r"\$\{[^}]+\}", "{}", sin_query)

        llamadas: set[tuple[str, str]] = set()
        for metodo, plantilla in re.findall(r'mandar<[^>]+>\("(POST|PUT|DELETE)",\s*[`"]([^`"]*)[`"]',
                                            texto):
            llamadas.add((metodo, forma(plantilla)))
        for plantilla in re.findall(r'leer<[^>]+>\(\s*[`"]([^`"]*)[`"]', texto):
            llamadas.add(("GET", forma(plantilla)))
        self.assertGreaterEqual(len(llamadas), 15, "el lector de api.ts dejó de entenderlo")
        declaradas = {(m, re.sub(r"\{[^}]+\}", "{}", r)) for m, r in rutas_del_router()}
        self.assertEqual(llamadas - declaradas, set(), "api.ts llama rutas que el router no tiene")
        # Las que api.ts arma aparte (multipart, descarga y la lista con su query).
        for metodo, r in (("POST", "/{}/archivos"), ("GET", "/{}/archivos/{}"), ("GET", "")):
            self.assertIn((metodo, r), declaradas)
        self.assertIn('fd.append("tipo", tipo)', texto, "el PDF viaja con su tipo")


def llaves_ts(interfaz: str) -> tuple[set[str], set[str]]:
    """(obligatorias, opcionales) de una `export interface` de tipos.ts, sólo su
    primer nivel (el mismo lector de tests/test_ordenes_venta_bd.py)."""
    texto = TIPOS_TS.read_text(encoding="utf-8")
    m = re.search(r"export interface " + interfaz + r"\b[^{]*\{", texto)
    assert m, f"tipos.ts ya no tiene la interfaz {interfaz}"
    nivel, cuerpo = 1, []
    for c in texto[m.end():]:
        nivel += (c == "{") - (c == "}")
        if nivel == 0:
            break
        if nivel == 1 and c != "}":
            cuerpo.append(c)
    limpio = re.sub(r"/\*.*?\*/", "", "".join(cuerpo), flags=re.S)
    limpio = re.sub(r"//[^\n]*", "", limpio)
    pares = re.findall(r"(?:^|;)\s*(\w+)(\??)\s*:", limpio, flags=re.M)
    return ({k for k, opcional in pares if not opcional}, {k for k, opcional in pares if opcional})


@unittest.skipUnless(TIPOS_TS.exists(), "sin frontend/components/ordenes/tipos.ts")
class ContratoDeCuerpos(unittest.TestCase):
    """Lo que el router ACEPTA es lo que tipos.ts dice que la pantalla manda. Si
    alguien le devuelve el «almacén» al encabezado (no existe en la 0064) o le
    quita la bodega al renglón, truena aquí y no en la pantalla."""

    def campos(self, interfaz: str) -> set[str]:
        obligatorias, opcionales = llaves_ts(interfaz)
        return obligatorias | opcionales

    def test_los_cuerpos_son_los_de_tipos_ts(self):
        self.assertEqual(set(ruta._Datos.model_fields), self.campos("DatosOrden"))
        self.assertEqual(set(ruta._Linea.model_fields), self.campos("LineaEntrada"))
        self.assertEqual(set(ruta._Salida.model_fields), self.campos("EntregaLinea"))
        self.assertNotIn("almacen", ruta._Datos.model_fields, "la bodega es del renglón")
        self.assertIn("almacen", ruta._Linea.model_fields)
        # Lo que el alta y el guardado le suman al documento.
        base = set(ruta._Datos.model_fields)
        self.assertEqual(set(ruta._Alta.model_fields) - base, {"clave", "tipo"})
        self.assertEqual(set(ruta._Guardado.model_fields) - base, {"rev"})
        self.assertEqual(set(ruta._Entrega.model_fields), {"rev", "lineas"})
        self.assertEqual(set(ruta._Salio.model_fields), {"rev", "salio"})
        self.assertTrue(ruta._Salio.model_fields["salio"].is_required(), "la respuesta es obligatoria")
        self.assertFalse(ruta._Entrega.model_fields["lineas"].is_required())

    def test_la_respuesta_del_barrido_es_resp_conciliar(self):
        obligatorias, opcionales = llaves_ts("RespConciliar")
        self.assertEqual((obligatorias, opcionales), ({"ok", "canceladas", "marcadas"}, {"motivo"}))
        with mock.patch.object(ov, "habilitado", return_value=False), \
                mock.patch.object(ov, "en_pausa", return_value=False):
            apagada = ov_auto.revisar()
        with mock.patch.object(ov, "habilitado", return_value=True), \
                mock.patch.object(ov, "tablas_listas", return_value=True), \
                mock.patch.object(ov_auto.sdb, "fetch_all", return_value=[]):
            encendida = ov_auto.revisar()
        for r in (apagada, encendida):
            self.assertEqual(obligatorias - set(r), set())
            self.assertEqual(set(r) - obligatorias - opcionales, set())
        self.assertEqual((apagada["ok"], "motivo" in apagada, encendida),
                         (False, True, {"ok": True, "canceladas": [], "marcadas": []}))


# ══════════════════════════════════════════════════════════════════════════════
# La API, con el servicio sustituido
# ══════════════════════════════════════════════════════════════════════════════

class Api(unittest.TestCase):
    """FastAPI mínimo con SÓLO el router. La identidad la pone un middleware de
    prueba (lo que en producción hace core/middleware.py)."""

    def setUp(self) -> None:
        ov_bus._reiniciar()
        self.identidad: Identidad | None = PERSONA
        self.hilo_loop: int | None = None
        app = FastAPI()
        app.include_router(ruta.router)

        @app.middleware("http")
        async def _identidad(request: Request, call_next):
            self.hilo_loop = threading.get_ident()
            if self.identidad is not None:
                request.state.identidad = self.identidad
            return await call_next(request)

        # Con `with`: UN solo loop para todas las peticiones de la prueba. Sin
        # él cada petición estrena loop, y el long-poll no podría despertar.
        self.c = TestClient(app)
        self.c.__enter__()
        self.addCleanup(self.c.__exit__, None, None, None)
        self.parchar(ov, "nombre_de", return_value="Nombre Visible")

    def parchar(self, objeto, nombre: str, **kw) -> mock.Mock:
        # autospec: el doble lleva la FIRMA de la función real. Un argumento de
        # más, o con otro nombre, truena aquí y no en producción.
        p = mock.patch.object(objeto, nombre, autospec=True, **kw)
        m = p.start()
        self.addCleanup(p.stop)
        return m

    def quien(self, m: mock.Mock) -> ov.Quien:
        return m.call_args.kwargs["quien"]

    # ── errores ──────────────────────────────────────────────────────────────
    def test_cada_error_del_servicio_sale_con_su_codigo_y_su_mensaje(self):
        casos = [(ov.Invalido("Renglón 1: el SKU es obligatorio."), 400),
                 (ov.SinPermiso("Sólo un administrador puede borrar una orden."), 403),
                 (ov.NoExiste("No existe la orden de venta 7."), 404),
                 (ov.Conflicto(), 409),
                 (ov.Conflicto("No alcanzó el stock para apartar: ZZPRUEBA-A pide 3 y hay 1 "
                               "libre en ENSAYO. No se apartó nada."), 409),
                 (ov.FaltaMigracion(), 409),
                 (ov.Apagado(), 409),
                 (ov.Grande("El PDF pesa 16.0 MB; el tope es 15 MB."), 413),
                 (ov.FallaStorage("No se pudo guardar el PDF en Storage; intenta de nuevo."), 502),
                 (ov.SinBase(), 502),
                 (ov.ErrorOV("No se pudo completar la operación; quedó registrado.", status=502),
                  502)]
        for exc, codigo in casos:
            with self.subTest(exc=type(exc).__name__), \
                    mock.patch.object(ov, "confirmar", autospec=True, side_effect=exc):
                r = self.c.post(f"{RAIZ}/7/confirmar", json={"rev": 1})
                self.assertEqual((r.status_code, r.json()["detail"]), (codigo, str(exc)))
        # Un error no avisa al bus… salvo el 409 de CONFIRMAR: cuando no alcanza,
        # el servicio dejó `no_alcanzo` en el chat antes de lanzar, y sin el aviso
        # ese renglón no aparecía hasta que vencía el long-poll.
        self.assertEqual(ov_bus.version(7), sum(1 for _, codigo in casos if codigo == 409))

    def test_solo_el_409_de_confirmar_avisa_al_bus(self):
        no_alcanzo = ov.Conflicto("No alcanzó el stock para apartar: ZZPRUEBA-A pide 5 y hay 1 "
                                  "libre en ENSAYO. No se apartó nada.")
        self.parchar(ov, "confirmar", side_effect=no_alcanzo)
        self.parchar(ov, "cancelar", side_effect=ov.Conflicto())
        self.assertEqual(self.c.post(f"{RAIZ}/8/cancelar", json={"rev": 1}).status_code, 409)
        self.assertEqual(ov_bus.version(8), 0, "el 409 de las demás no escribió nada: no avisa")
        r = self.c.post(f"{RAIZ}/8/confirmar", json={"rev": 1})
        self.assertEqual((r.status_code, r.json()["detail"]), (409, str(no_alcanzo)))
        self.assertEqual(ov_bus.version(8), 1, "el chat despierta y lee el renglón `no_alcanzo`")

    def test_la_rev_es_un_entero_de_verdad(self):
        """Con `rev: int` pydantic convertía `true`, "1" y 1.0 en la rev 1: un
        `{"rev": true}` pasaba el candado de cualquier documento en rev 1."""
        self.identidad = ADMIN
        servicios = [self.parchar(ov, n, return_value=resp_orden()) for n in (
            "guardar", "confirmar", "entregar", "cancelar", "responder_salio", "salio_tarde",
            "borrar")]
        for mala in (True, "1", 1.0, 1.5, None):
            for metodo, url, cuerpo in (
                    ("PUT", f"{RAIZ}/7", {"rev": mala}),
                    ("POST", f"{RAIZ}/7/confirmar", {"rev": mala}),
                    ("POST", f"{RAIZ}/7/entregar", {"rev": mala}),
                    ("POST", f"{RAIZ}/7/cancelar", {"rev": mala, "motivo": "ya no la quiso"}),
                    ("POST", f"{RAIZ}/7/salio", {"rev": mala, "salio": True}),
                    ("POST", f"{RAIZ}/7/salio-tarde", {"rev": mala}),
                    ("DELETE", f"{RAIZ}/7", {"rev": mala, "motivo": "se capturó dos veces"})):
                with self.subTest(url=f"{metodo} {url}", rev=mala):
                    self.assertEqual(self.c.request(metodo, url, json=cuerpo).status_code, 422)
        self.assertEqual([m.call_count for m in servicios], [0] * 7, "ninguna llegó al servicio")
        self.assertEqual(self.c.post(f"{RAIZ}/7/confirmar", json={"rev": 1}).status_code, 200)

    def test_lo_inesperado_es_un_502_que_no_filtra_el_detalle(self):
        self.parchar(ov, "obtener", side_effect=RuntimeError("password=hunter2 host=db.interno"))
        with self.assertLogs("omnicanal.routers.ordenes_venta", level="ERROR") as logs:
            r = self.c.get(f"{RAIZ}/7")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["detail"], "No se pudo completar la operación; intenta de nuevo.")
        self.assertNotIn("hunter2", r.text)
        self.assertIn("hunter2", "\n".join(logs.output), "el detalle sí queda en el log")

    def test_kubera_caida_es_un_502_de_una_linea_sin_traceback(self):
        """Ni traza por petición, ni el host del pooler hacia afuera."""
        ov._reiniciar_caida()
        self.addCleanup(ov._reiniciar_caida)
        self.parchar(ov, "obtener", side_effect=psycopg2.OperationalError(
            'connection to server at "db.interno", port 6543 failed: timeout'))
        with self.assertLogs("omnicanal.ordenes_venta", level="WARNING") as logs, \
                self.assertNoLogs("omnicanal.routers.ordenes_venta", level="ERROR"):
            r = self.c.get(f"{RAIZ}/7")
            self.c.get(f"{RAIZ}/7")
        self.assertEqual((r.status_code, r.json()["detail"]),
                         (502, "kubera no contesta; intenta de nuevo en un momento."))
        self.assertNotIn("db.interno", r.text)
        self.assertEqual(len(logs.output), 1, "una línea por minuto, no una por petición")

    # ── quién ────────────────────────────────────────────────────────────────
    def test_persona_con_su_nombre_y_su_rol_y_todo_en_un_hilo(self):
        hilos: dict[str, int] = {}

        def _nombre(correo):
            hilos["nombre"] = threading.get_ident()
            return "Ana Operadora"

        def _confirmar(orden_id, rev, quien, plan=None, cur=None):
            hilos["servicio"] = threading.get_ident()
            hilos["quien"] = quien
            return resp_orden(orden_id)

        ov.nombre_de.side_effect = _nombre                 # ya va parchada desde setUp
        self.parchar(ov, "confirmar", side_effect=_confirmar)
        r = self.c.post(f"{RAIZ}/7/confirmar", json={"rev": 4})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(hilos["quien"],
                         ov.Quien("ana@prueba.test", "Ana Operadora", "panel", "operador"))
        self.assertIsNotNone(self.hilo_loop)
        self.assertNotEqual(hilos["servicio"], self.hilo_loop, "el servicio corrió en el loop")
        self.assertNotEqual(hilos["nombre"], self.hilo_loop, "nombre_de (lee la base) corrió en el loop")

    def test_persona_sin_nombre_en_core_usuarios_usa_lo_de_antes_de_la_arroba(self):
        ov.nombre_de.return_value = None
        m = self.parchar(ov, "obtener", return_value={"id": 7})
        self.c.get(f"{RAIZ}/7")
        self.assertEqual(self.quien(m), ov.Quien("ana@prueba.test", "ana", "panel", "operador"))

    def test_maquina_es_api_y_con_x_origen_claude_es_claude(self):
        self.identidad = MAQUINA
        m = self.parchar(ov, "obtener", return_value={"id": 7})
        self.c.get(f"{RAIZ}/7")
        self.assertEqual(self.quien(m), ov.Quien("servicio", "API", "api", "admin"))
        for cabecera in ("claude", "Claude", " CLAUDE "):
            self.c.get(f"{RAIZ}/7", headers={"X-Origen": cabecera})
            self.assertEqual(self.quien(m), ov.Quien("servicio", "Claude", "claude", "admin"), cabecera)
        self.c.get(f"{RAIZ}/7", headers={"X-Origen": "otro"})
        self.assertEqual(self.quien(m).via, "api")
        ov.nombre_de.assert_not_called()

    def test_una_persona_no_se_vuelve_claude_por_mandar_la_cabecera(self):
        m = self.parchar(ov, "obtener", return_value={"id": 7})
        self.c.get(f"{RAIZ}/7", headers={"X-Origen": "claude"})
        self.assertEqual((self.quien(m).via, self.quien(m).rol), ("panel", "operador"))
        self.c.get(f"{RAIZ}/7", headers={"User-Agent": "claude-code/2.0"})
        self.assertEqual(self.quien(m).via, "panel", "ni por el User-Agent")

    def test_la_llave_con_user_agent_de_claude_tambien_es_claude(self):
        """`X-Origen` es voluntaria y nadie la manda; el UA sí."""
        self.identidad = MAQUINA
        m = self.parchar(ov, "obtener", return_value={"id": 7})
        for ua in ("claude-cli/1.0.0 (external, cli)", "Mozilla/5.0 Claude-User", "CLAUDE"):
            self.c.get(f"{RAIZ}/7", headers={"User-Agent": ua})
            self.assertEqual(self.quien(m), ov.Quien("servicio", "Claude", "claude", "admin"), ua)
        self.c.get(f"{RAIZ}/7", headers={"User-Agent": "python-requests/2.32"})
        self.assertEqual(self.quien(m).via, "api")

    def test_sin_credencial_es_admin_solo_con_el_sistema_abierto(self):
        m = self.parchar(ov, "obtener", return_value={"id": 7})
        for identidad in (ANONIMO, None):                  # anónimo, o sin middleware
            self.identidad = identidad
            with mock.patch.object(ruta.settings, "auth_enforced", False):
                self.assertEqual(self.c.get(f"{RAIZ}/7").status_code, 200)
            self.assertEqual(self.quien(m), ov.Quien("anonimo", "Sin sesión", "panel", "admin"))
            m.reset_mock()
            with mock.patch.object(ruta.settings, "auth_enforced", True):
                r = self.c.get(f"{RAIZ}/7")
            self.assertEqual(r.status_code, 401)
            self.assertIn("credencial", r.json()["detail"])
            m.assert_not_called()

    def test_quien_siempre_cabe_en_el_catalogo_de_vias_de_la_base(self):
        """Guía §4.4: la `via` sólo acepta panel | api | claude | automatico, y el
        actor nunca va vacío. Lo que arma el router pasa por la firma del servicio."""
        m = self.parchar(ov, "obtener", return_value={"id": 7})
        casos = ((PERSONA, {}), (ADMIN, {}), (MAQUINA, {}), (MAQUINA, {"X-Origen": "claude"}),
                 (ANONIMO, {}))
        with mock.patch.object(ruta.settings, "auth_enforced", False):
            for identidad, cabeceras in casos:
                self.identidad = identidad
                self.c.get(f"{RAIZ}/7", headers=cabeceras)
                firma = ov._firma(self.quien(m))
                self.assertIn(firma["via"], ov.VIAS)
                self.assertTrue(firma["q"])

    # ── alta y guardado ──────────────────────────────────────────────────────
    def test_put_es_guardar_borrador_y_manda_solo_lo_que_llego(self):
        m = self.parchar(ov, "guardar", return_value=resp_orden())
        r = self.c.put(f"{RAIZ}/7", json={"rev": 3, "cliente": "Temu", "total": None})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(m.call_args.args, (7, 3, {"cliente": "Temu", "total": None}),
                         "ni la rev ni los campos que no llegaron; el null explícito SÍ")
        self.assertEqual(self.quien(m).actor, "ana@prueba.test")
        self.c.put(f"{RAIZ}/7", json={"rev": 3})
        self.assertEqual(m.call_args.args, (7, 3, {}))
        # La bodega va POR RENGLÓN; lo que el renglón no trae tampoco se inventa.
        self.c.put(f"{RAIZ}/7", json={"rev": 3, "lineas": [
            {"sku": "ZZPRUEBA-A", "cantidad": 2, "precio_unitario": 10.5, "almacen": "ENSAYO"},
            {"sku": "ZZPRUEBA-B", "cantidad": "3", "titulo": None}]})
        self.assertEqual(m.call_args.args[2], {"lineas": [
            {"sku": "ZZPRUEBA-A", "cantidad": 2, "precio_unitario": 10.5, "almacen": "ENSAYO"},
            {"sku": "ZZPRUEBA-B", "cantidad": "3", "titulo": None}]})
        self.assertEqual(ov_bus.version(7), 3, "cada escritura avisó")

    def test_el_almacen_de_encabezado_ya_no_existe(self):
        """`ov_ordenes.almacen` no existe en la 0064: si un cliente viejo lo manda,
        no viaja al servicio (ni como llave desconocida)."""
        m = self.parchar(ov, "guardar", return_value=resp_orden())
        self.c.put(f"{RAIZ}/7", json={"rev": 3, "almacen": "TEX2", "guia": "G-1"})
        self.assertEqual(m.call_args.args, (7, 3, {"guia": "G-1"}))
        alta = self.parchar(ov, "crear_borrador", return_value=resp_orden())
        self.c.post(RAIZ, json={"almacen": "TEX2", "cliente": "directa"})
        self.assertEqual(alta.call_args.args, ({"cliente": "directa"},))

    def test_guardar_fuera_de_borrador_es_el_400_del_servicio(self):
        """«PUT sólo en borrador»: la regla es del servicio (y de la base); el
        router no la duplica, sólo entrega su 400 con sus palabras y no avisa."""
        m = self.parchar(ov, "guardar", side_effect=ov.Invalido(
            "La orden ya está confirmada: su contenido no cambia. Para corregirla hay que "
            "cancelarla (o que un administrador la borre)."))
        r = self.c.put(f"{RAIZ}/7", json={"rev": 3, "cliente": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("su contenido no cambia", r.json()["detail"])
        m.assert_called_once()
        self.assertEqual(ov_bus.version(7), 0)

    def test_el_alta_separa_la_clave_y_avisa(self):
        m = self.parchar(ov, "crear_borrador", return_value=resp_orden(12))
        r = self.c.post(RAIZ, json={"clave": "c-1", "cliente": "Temu", "mp_canal": "temu",
                                    "lineas": [{"sku": "ZZPRUEBA-A", "cantidad": 1,
                                                "almacen": "ENSAYO"}], "desconocido": 9})
        self.assertEqual((r.status_code, r.json()["orden"]["folio"]), (200, "OV-00012"))
        self.assertEqual(m.call_args.args, ({"cliente": "Temu", "mp_canal": "temu",
                                             "lineas": [{"sku": "ZZPRUEBA-A", "cantidad": 1,
                                                         "almacen": "ENSAYO"}]},))
        self.assertEqual(m.call_args.kwargs["clave"], "c-1")
        self.assertEqual(ov_bus.version(12), 1)
        self.c.post(RAIZ, json={})
        self.assertEqual((m.call_args.args, m.call_args.kwargs["clave"]), (({},), None))

    def test_el_tipo_full_llega_al_servicio_para_que_lo_rechace_con_palabras(self):
        """Sin dejarlo pasar, quien pidiera un `full` por la API recibiría una
        VENTA creada, callando lo que pidió."""
        m = self.parchar(ov, "crear_borrador", side_effect=ov.Invalido(
            "Los envíos a FULL se crean desde «Crear FULL», no desde esta pantalla."))
        r = self.c.post(RAIZ, json={"tipo": "full", "lineas": []})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Crear FULL", r.json()["detail"])
        self.assertEqual(m.call_args.args, ({"tipo": "full", "lineas": []},))

    def test_lo_que_la_validacion_del_servicio_explica_no_se_queda_en_un_422(self):
        """Un «2.5 piezas» o un total con letras llegan al servicio, que contesta
        el 400 con el renglón; el router sólo acota tamaños."""
        m = self.parchar(ov, "crear_borrador", side_effect=ov.Invalido("Renglón 1: la cantidad…"))
        r = self.c.post(RAIZ, json={"total": "doce", "lineas": [{"sku": "A", "cantidad": 2.5}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(m.call_args.args[0], {"total": "doce",
                                               "lineas": [{"sku": "A", "cantidad": 2.5}]})

    def test_un_booleano_no_se_vuelve_un_numero_por_el_camino(self):
        """pydantic convertía `true` en 1 antes de que el servicio lo viera: una
        pieza, un peso o «salió 1» que nadie pidió. Ahora llega como lo que es y
        el servicio (que no acepta booleanos como números) lo rechaza."""
        m = self.parchar(ov, "crear_borrador", return_value=resp_orden())
        self.c.post(RAIZ, json={"total": True, "comision": False, "lineas": [
            {"sku": "ZZPRUEBA-A", "cantidad": True, "precio_unitario": True}]})
        datos = m.call_args.args[0]
        self.assertIs(datos["total"], True)
        self.assertIs(datos["comision"], False)
        self.assertIs(datos["lineas"][0]["cantidad"], True)
        self.assertIs(datos["lineas"][0]["precio_unitario"], True)
        e = self.parchar(ov, "entregar", return_value=resp_orden())
        self.c.post(f"{RAIZ}/7/entregar", json={"rev": 2, "lineas": [{"id": 11, "n": True}]})
        self.assertIs(e.call_args.kwargs["lineas"][0]["n"], True)
        # …y lo que sí es un número llega con su tipo, sin redondeos.
        self.c.post(f"{RAIZ}/7/entregar", json={"rev": 2, "lineas": [{"id": 11, "n": 2},
                                                                    {"id": "12", "n": 2.0}]})
        self.assertEqual([(type(l["id"]), type(l["n"])) for l in e.call_args.kwargs["lineas"]],
                         [(int, int), (str, float)])

    def test_los_topes_del_cuerpo(self):
        m = self.parchar(ov, "crear_borrador", return_value=resp_orden())
        linea = {"sku": "A", "cantidad": 1}
        self.assertEqual(self.c.post(RAIZ, json={"lineas": [linea] * 200}).status_code, 200)
        self.assertEqual(self.c.post(RAIZ, json={"lineas": [linea] * 201}).status_code, 422)
        self.assertEqual(self.c.post(RAIZ, json={"clave": "x" * 80}).status_code, 200)
        self.assertEqual(self.c.post(RAIZ, json={"clave": "x" * 81}).status_code, 422)
        self.assertEqual(m.call_count, 2)
        salida = {"id": 1, "n": 1}
        for metodo, url, cuerpo in (
                ("post", f"{RAIZ}/7/confirmar", {}),
                ("post", f"{RAIZ}/7/confirmar", {"rev": 0}),
                ("put", f"{RAIZ}/7", {"cliente": "x"}),
                ("post", f"{RAIZ}/7/cancelar", {"rev": 1, "motivo": "x" * 501}),
                ("post", f"{RAIZ}/7/entregar", {"lineas": [salida]}),
                ("post", f"{RAIZ}/7/entregar", {"rev": 1, "lineas": [salida] * 201}),
                ("post", f"{RAIZ}/7/entregar", {"rev": 1, "lineas": [{"id": 1}]}),
                ("post", f"{RAIZ}/7/salio", {"rev": 1}),
                ("post", f"{RAIZ}/7/salio", {"salio": True}),
                ("post", f"{RAIZ}/7/salio-tarde", {}),
                ("post", f"{RAIZ}/7/mensajes", {"cuerpo": ""}),
                ("post", f"{RAIZ}/7/mensajes", {"cuerpo": "x" * 4001})):
            with self.subTest(url=url, cuerpo=str(cuerpo)[:40]):
                self.assertEqual(getattr(self.c, metodo)(url, json=cuerpo).status_code, 422)
        self.assertEqual(self.c.delete(f"{RAIZ}/7").status_code, 422, "borrar exige la rev")

    # ── transiciones ─────────────────────────────────────────────────────────
    def test_cada_transicion_llama_a_su_servicio_y_avisa(self):
        salen = [{"id": 11, "n": 2}, {"id": 12, "n": 0}]
        for n, (ruta_, funcion, cuerpo, esperado) in enumerate((
                ("confirmar", "confirmar", {"rev": 2}, ((7, 2), {})),
                ("entregar", "entregar", {"rev": 2}, ((7, 2), {"lineas": None})),
                ("entregar", "entregar", {"rev": 2, "lineas": salen}, ((7, 2), {"lineas": salen})),
                ("cancelar", "cancelar", {"rev": 2, "motivo": "se arrepintió"},
                 ((7, 2), {"motivo": "se arrepintió"})),
                ("cancelar", "cancelar", {"rev": 2}, ((7, 2), {"motivo": ""})),
                ("salio", "responder_salio", {"rev": 2, "salio": True}, ((7, 2), {"salio": True})),
                ("salio", "responder_salio", {"rev": 2, "salio": False},
                 ((7, 2), {"salio": False})),
                ("salio-tarde", "salio_tarde", {"rev": 2}, ((7, 2), {}))), 1):
            with self.subTest(ruta=ruta_, cuerpo=cuerpo), \
                    mock.patch.object(ov, funcion, autospec=True, return_value=resp_orden()) as m:
                r = self.c.post(f"{RAIZ}/7/{ruta_}", json=cuerpo)
                self.assertEqual((r.status_code, r.json()["mensaje"]), (200, "Hecho."), r.text)
                otros = {k: v for k, v in m.call_args.kwargs.items() if k != "quien"}
                self.assertEqual((m.call_args.args, otros), esperado)
                self.assertEqual(self.quien(m).actor, "ana@prueba.test")
                self.assertEqual(ov_bus.version(7), n)

    def test_entregar_sin_lineas_no_es_una_lista_vacia(self):
        """Sin `lineas` salen todos los renglones completos (None); una lista
        VACÍA es otra cosa —«no salió ninguno»— y el servicio la rechaza: el
        router no convierte una en la otra."""
        m = self.parchar(ov, "entregar", return_value=resp_orden())
        self.c.post(f"{RAIZ}/7/entregar", json={"rev": 2})
        self.assertIsNone(m.call_args.kwargs["lineas"])
        self.c.post(f"{RAIZ}/7/entregar", json={"rev": 2, "lineas": None})
        self.assertIsNone(m.call_args.kwargs["lineas"])
        self.c.post(f"{RAIZ}/7/entregar", json={"rev": 2, "lineas": []})
        self.assertEqual(m.call_args.kwargs["lineas"], [])
        # Las piezas llegan como vengan: el servicio dice cuál renglón está mal.
        self.c.post(f"{RAIZ}/7/entregar", json={"rev": 2, "lineas": [{"id": "11", "n": 1.5,
                                                                    "sku": "de más"}]})
        self.assertEqual(m.call_args.kwargs["lineas"], [{"id": "11", "n": 1.5}])

    def test_la_respuesta_de_salio_llega_sin_convertir(self):
        """«sí», 1 o "true" NO son una respuesta: la decisión mueve stock y no se
        deshace. El router no convierte nada; el servicio exige un booleano."""
        m = self.parchar(ov, "responder_salio", return_value=resp_orden())
        for valor in ("true", 1, "sí", None, [True]):
            self.c.post(f"{RAIZ}/7/salio", json={"rev": 2, "salio": valor})
            recibido = m.call_args.kwargs["salio"]
            self.assertEqual((type(recibido), recibido), (type(valor), valor))
        m.side_effect = ov.Invalido("«salio» es verdadero (sí salió) o falso (no salió).")
        r = self.c.post(f"{RAIZ}/7/salio", json={"rev": 2, "salio": "sí"})
        self.assertEqual((r.status_code, r.json()["detail"]),
                         (400, "«salio» es verdadero (sí salió) o falso (no salió)."))

    def test_salio_tarde_es_de_admin_y_lo_dice_el_servicio(self):
        """El piso del RBAC para ese POST es `operador`: por eso `quien` viaja."""
        m = self.parchar(ov, "salio_tarde", side_effect=ov.SinPermiso(
            "Sólo un administrador puede registrar la salida de una orden ya cancelada."))
        r = self.c.post(f"{RAIZ}/7/salio-tarde", json={"rev": 4})
        self.assertEqual(r.status_code, 403)
        self.assertEqual((m.call_args.args, self.quien(m).rol), ((7, 4), "operador"))
        self.identidad = ADMIN
        m.side_effect = None
        m.return_value = resp_orden()
        self.assertEqual(self.c.post(f"{RAIZ}/7/salio-tarde", json={"rev": 4}).status_code, 200)
        self.assertEqual((self.quien(m).rol, ov_bus.version(7)), ("admin", 1))

    def test_cancelar_desde_la_api_nunca_dice_que_fue_el_marketplace(self):
        m = self.parchar(ov, "cancelar", return_value=resp_orden())
        self.c.post(f"{RAIZ}/7/cancelar", json={"rev": 2, "motivo": "x", "origen": "marketplace"})
        self.assertNotIn("origen", m.call_args.kwargs)

    def test_confirmar_no_acepta_un_plan_desde_la_pantalla(self):
        """api.ts manda sólo la rev: de qué bodega sale cada renglón ya lo dice el
        renglón. El `plan` del servicio es para quien lo llama desde código."""
        m = self.parchar(ov, "confirmar", return_value=resp_orden())
        self.c.post(f"{RAIZ}/7/confirmar", json={"rev": 2, "plan": [{"id": 1, "almacen": "TEX3"}]})
        self.assertEqual({k for k in m.call_args.kwargs if k != "quien"}, set())

    def test_borrar_lleva_rev_y_motivo_por_la_direccion(self):
        self.identidad = ADMIN
        m = self.parchar(ov, "borrar", return_value=resp_orden())
        r = self.c.delete(f"{RAIZ}/7", params={"rev": 5, "motivo": "se capturó dos veces"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((m.call_args.args, m.call_args.kwargs["motivo"]),
                         ((7, 5), "se capturó dos veces"))
        self.assertEqual((self.quien(m).rol, ov_bus.version(7)), ("admin", 1))

    def test_borrar_lleva_rev_y_motivo_en_el_cuerpo(self):
        """El motivo es texto libre: en la dirección quedaba en el log de accesos.
        Si viene cuerpo, manda el cuerpo; la dirección es sólo compatibilidad."""
        self.identidad = ADMIN
        m = self.parchar(ov, "borrar", return_value=resp_orden())
        r = self.c.request("DELETE", f"{RAIZ}/7", json={"rev": 5, "motivo": "se capturó dos veces"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((m.call_args.args, m.call_args.kwargs["motivo"]),
                         ((7, 5), "se capturó dos veces"))
        self.assertEqual((self.quien(m).rol, ov_bus.version(7)), ("admin", 1))
        # Con cuerpo, la dirección ni se mira (ni su rev ni su motivo).
        r = self.c.request("DELETE", f"{RAIZ}/7", params={"rev": 9, "motivo": "el de la dirección"},
                           json={"rev": 6})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((m.call_args.args, m.call_args.kwargs["motivo"]), ((7, 6), ""))
        # Los topes del cuerpo son los de cancelar; y sin rev en ningún lado, 422.
        for cuerpo in ({"motivo": "sin rev, sí motivo"}, {"rev": 0}, {"rev": 1, "motivo": "x" * 501}):
            self.assertEqual(self.c.request("DELETE", f"{RAIZ}/7", json=cuerpo).status_code, 422)
        r = self.c.delete(f"{RAIZ}/7", params={"motivo": "sin rev"})
        self.assertEqual((r.status_code, r.json()["detail"]),
                         (422, "Falta la rev: va en el cuerpo, como {rev, motivo}."))
        self.assertEqual(m.call_count, 2)

    # ── rutas ────────────────────────────────────────────────────────────────
    def test_las_rutas_fijas_no_se_las_come_el_detalle(self):
        detalle = self.parchar(ov, "obtener", return_value={"id": 7})
        estado = self.parchar(ov, "estado_modulo", return_value={"ok": True})
        skus = self.parchar(ov, "buscar_skus", return_value={"opciones": []})
        venta = self.parchar(ov, "venta_marketplace", return_value={"ok": True, "ventas": []})
        pend = self.parchar(ov, "ventas_pendientes", return_value={"ok": True, "ventas": []})
        lista = self.parchar(ov, "listar", return_value={"ok": True, "ordenes": []})

        self.assertEqual(self.c.get(f"{RAIZ}/estado").json(), {"ok": True})
        estado.assert_called_once()
        self.c.get(f"{RAIZ}/skus", params={"q": "cab"})
        skus.assert_called_once_with("cab")
        self.c.get(f"{RAIZ}/marketplace/venta", params={"orden": "PO-1", "canal": "temu"})
        venta.assert_called_once_with("PO-1", "temu")
        self.c.get(f"{RAIZ}/marketplace/pendientes", params={"dias": 3})
        pend.assert_called_once_with(3, None)
        self.c.get(RAIZ, params={"estado": "confirmada", "q": "ov-1", "canal": "temu",
                                 "pagina": 2, "por_pagina": 10})
        lista.assert_called_once_with("confirmada", "ov-1", "temu", 2, 10)
        self.c.get(RAIZ)
        lista.assert_called_with(None, None, None, 1, 40)
        detalle.assert_not_called()

        self.c.get(f"{RAIZ}/OV-00012")
        self.assertEqual(detalle.call_args.args, ("OV-00012",))
        self.c.get(f"{RAIZ}/12")
        self.assertEqual(detalle.call_args.args, ("12",))

    def test_un_folio_no_entra_donde_va_un_id(self):
        m = self.parchar(ov, "confirmar", return_value=resp_orden())
        self.assertEqual(self.c.post(f"{RAIZ}/OV-00007/confirmar", json={"rev": 1}).status_code, 404)
        self.assertIn(self.c.put(f"{RAIZ}/OV-00007", json={"rev": 1}).status_code, (404, 405))
        m.assert_not_called()

    def test_las_rutas_que_se_fueron_contestan_que_no_existen(self):
        """Un cliente viejo (o un script) que siga llamándolas no mueve nada."""
        detalle = self.parchar(ov, "obtener", return_value={"id": 7})
        for vieja in ("reservar", "regresar", "devolucion"):
            r = self.c.post(f"{RAIZ}/7/{vieja}", json={"rev": 1, "estado": "recibida"})
            self.assertEqual(r.status_code, 404, vieja)
        r = self.c.post(f"{RAIZ}/auto", json={"encendido": True, "motivo": "ya"})
        self.assertEqual(r.status_code, 405, "las banderas las enciende un acta, no la API")
        detalle.assert_not_called()
        self.assertEqual(ov_bus.version(7), 0)

    def test_el_estado_es_el_del_servicio_con_quien_soy(self):
        """EstadoModulo lo arma el servicio (banderas de SÓLO LECTURA, bodegas,
        si hay bucket): el router le pasa quién pide y no le agrega nada."""
        self.identidad = ADMIN
        cuerpo = {
            "ok": True, "falta_migracion": False, "habilitado": False,
            "banderas": {"ordenes_venta": {"encendido": False, "persistido": False},
                         "ov_generacion_auto": {"encendido": False, "persistido": False}},
            "bodegas": [{"codigo": "ENSAYO", "admite_ov": True}],
            "archivos": {"disponible": False, "motivo": "falta el bucket"}}
        estado = self.parchar(ov, "estado_modulo", side_effect=lambda quien, cur=None: {
            **cuerpo, "yo": {"actor": quien.actor, "admin": quien.admin}})
        r = self.c.get(f"{RAIZ}/estado")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {**cuerpo, "yo": {"actor": "ada@prueba.test", "admin": True}})
        self.assertNotIn("auto", r.json(), "el interruptor movible ya no existe")
        estado.assert_called_once()
        self.assertEqual(ov_bus.version(7), 0, "leer el estado no avisa a nadie")

    # ── PDF ──────────────────────────────────────────────────────────────────
    def test_pdf_valido_llega_al_servicio_con_su_tipo_y_su_nombre(self):
        m = self.parchar(ov, "subir_archivo", return_value=resp_orden())
        r = self.c.post(f"{RAIZ}/7/archivos", data={"tipo": "comprobante"},
                        files={"pdf": ("Comprobante T-1.pdf", PDF, "application/pdf")})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(m.call_args.args, (7,))
        self.assertEqual((m.call_args.kwargs["tipo"], m.call_args.kwargs["nombre"],
                          m.call_args.kwargs["datos"]), ("comprobante", "Comprobante T-1.pdf", PDF))
        self.assertEqual((self.quien(m).actor, ov_bus.version(7)), ("ana@prueba.test", 1))
        for tipo in ("factura", "envio_full"):
            self.c.post(f"{RAIZ}/7/archivos", data={"tipo": tipo}, files={"pdf": ("x.pdf", PDF)})
            self.assertEqual(m.call_args.kwargs["tipo"], tipo)

    def test_sin_tipo_decide_el_servicio_y_lo_dice_con_palabras(self):
        """El `tipo` es obligatorio (la columna no tiene valor por omisión), pero
        la lista de tipos válidos es del servicio: sin él viaja vacío y el 400
        dice cuáles son y que las guías con dirección no se guardan."""
        m = self.parchar(ov, "subir_archivo", side_effect=ov.Invalido(
            "Indica qué es el PDF: comprobante, factura o envío a FULL (envio_full). "
            "Las guías con la dirección del comprador NO se guardan aquí."))
        r = self.c.post(f"{RAIZ}/7/archivos", files={"pdf": ("x.pdf", PDF)})
        self.assertEqual(r.status_code, 400)
        self.assertIn("comprobante, factura", r.json()["detail"])
        self.assertEqual(m.call_args.kwargs["tipo"], "")
        self.c.post(f"{RAIZ}/7/archivos", data={"tipo": "guia"}, files={"pdf": ("x.pdf", PDF)})
        self.assertEqual(m.call_args.kwargs["tipo"], "guia", "tal cual: lo rechaza el servicio")
        self.c.post(f"{RAIZ}/7/archivos", data={"tipo": "x" * 500}, files={"pdf": ("x.pdf", PDF)})
        self.assertEqual(len(m.call_args.kwargs["tipo"]), 40)
        self.assertEqual(ov_bus.version(7), 0)

    def test_sin_el_bucket_es_el_409_del_servicio(self):
        """Mientras no exista el bucket `ordenes-venta` no hay dónde guardar: el
        servicio lo dice con un 409 y el router lo entrega sin avisar al bus."""
        self.parchar(ov, "subir_archivo", side_effect=ov.Conflicto(
            "Todavía no se pueden adjuntar PDF: falta crear el bucket «ordenes-venta» en Storage."))
        r = self.c.post(f"{RAIZ}/7/archivos", data={"tipo": "factura"},
                        files={"pdf": ("x.pdf", PDF)})
        self.assertEqual(r.status_code, 409)
        self.assertIn("falta crear el bucket", r.json()["detail"])
        self.assertEqual(ov_bus.version(7), 0)

    def test_pdf_invalido_o_grande_no_llega_al_servicio(self):
        m = self.parchar(ov, "subir_archivo", return_value=resp_orden())
        tipo = {"tipo": "comprobante"}
        for datos, codigo, texto in ((b"PK\x03\x04 un zip", 400, "no es un PDF"),
                                     (b"<html>", 400, "no es un PDF"),
                                     (b"", 400, "vacío")):
            r = self.c.post(f"{RAIZ}/7/archivos", data=tipo,
                            files={"pdf": ("x.pdf", datos, "application/pdf")})
            self.assertEqual(r.status_code, codigo, datos[:8])
            self.assertIn(texto, r.json()["detail"])
        with mock.patch.object(ruta, "_MAX_PDF", 1024):
            justo = b"%PDF" + b"0" * 1020
            self.assertEqual(self.c.post(f"{RAIZ}/7/archivos", data=tipo,
                                         files={"pdf": ("x.pdf", justo)}).status_code, 200)
            m.reset_mock()
            r = self.c.post(f"{RAIZ}/7/archivos", data=tipo, files={"pdf": ("x.pdf", justo + b"0")})
            self.assertEqual(r.status_code, 413)
        m.assert_not_called()
        self.assertEqual(self.c.post(f"{RAIZ}/7/archivos", data=tipo,
                                     files={"otro": ("x.pdf", PDF)}).status_code, 422,
                         "el campo se llama `pdf`")
        # Un formulario con campos de sobra no se lee entero: lo corta Starlette.
        self.assertEqual(self.c.post(f"{RAIZ}/7/archivos",
                                     data={"tipo": "factura", "a": "1", "b": "2"},
                                     files={"pdf": ("x.pdf", PDF)}).status_code, 400)
        m.assert_not_called()

    def test_el_tope_del_pdf_es_el_del_servicio(self):
        self.assertEqual(ruta._MAX_PDF, 15 * 1024 * 1024)
        self.assertEqual(ruta._MAX_PDF, ov.MAX_PDF)

    def test_la_descarga_trae_sus_cabeceras(self):
        m = self.parchar(ov, "bajar_archivo", return_value=('Factura "T-1" ñ.pdf', PDF))
        r = self.c.get(f"{RAIZ}/7/archivos/3")
        self.assertEqual((r.status_code, r.content), (200, PDF))
        self.assertEqual(m.call_args.args, (7, 3))
        self.assertEqual(self.quien(m).actor, "ana@prueba.test", "la descarga ya sabe quién pide")
        self.assertEqual(r.headers["content-type"], "application/pdf")
        self.assertEqual(r.headers["content-disposition"],
                         'attachment; filename="Factura _T-1_ n.pdf"; '
                         "filename*=UTF-8''Factura%20%22T-1%22%20%C3%B1.pdf")
        self.assertEqual(r.headers["access-control-expose-headers"], "Content-Disposition")
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_lectura_no_baja_el_pdf_ni_se_le_pregunta_a_storage(self):
        """El GET entra por el piso `lectura` del RBAC, pero bajar un PDF pide
        operador. El servicio REAL lo corta antes de tocar la base o Storage."""
        self.identidad = LECTURA
        with mock.patch.object(ov.ov_storage, "bajar", side_effect=AssertionError("no")), \
                mock.patch.object(ov.sdb, "get_cursor", side_effect=AssertionError("no")):
            r = self.c.get(f"{RAIZ}/7/archivos/3")
        self.assertEqual(r.status_code, 403)
        self.assertIn("sólo lectura", r.json()["detail"])

    def test_el_tope_del_pdf_se_mira_antes_de_leer_el_cuerpo(self):
        """Con `File(...)` en la firma, FastAPI volcaba el multipart ENTERO a disco
        antes de entrar. Ahora un Content-Length que ya no cabe es 413 sin abrir
        el formulario; lo que sí cabe se mide de verdad después."""
        m = self.parchar(ov, "subir_archivo", return_value=resp_orden())
        real = RequestStarlette.form
        abiertos: list[int] = []

        def _form(req, *a, **k):
            abiertos.append(1)
            return real(req, *a, **k)

        tipo = {"tipo": "comprobante"}
        with mock.patch.object(ruta, "_MAX_PDF", 1024), \
                mock.patch.object(ruta, "_MARGEN_MULTIPART", 512), \
                mock.patch.object(RequestStarlette, "form", _form):
            r = self.c.post(f"{RAIZ}/7/archivos", data=tipo,
                            files={"pdf": ("x.pdf", b"%PDF" + b"0" * 4000)})
            self.assertEqual((r.status_code, abiertos), (413, []), "ni se abrió el formulario")
            self.assertIn("MB", r.json()["detail"])
            ok = self.c.post(f"{RAIZ}/7/archivos", data=tipo,
                             files={"pdf": ("x.pdf", b"%PDF" + b"0" * 900)})
            self.assertEqual((ok.status_code, abiertos), (200, [1]))
        m.assert_called_once()

    def test_el_tope_del_pdf_no_depende_de_que_el_cliente_diga_cuanto_pesa(self):
        """Sin Content-Length (cuerpo por trozos) el multipart ENTERO llegaba al
        disco antes del 413; y como urlencoded, a la memoria. Ahora ninguno de los
        dos abre el formulario: 411 y 415, antes de leer un byte del cuerpo."""
        m = self.parchar(ov, "subir_archivo", return_value=resp_orden())
        real = RequestStarlette.form
        abiertos: list[int] = []
        leidos: list[int] = []

        def _form(req, *a, **k):
            abiertos.append(1)
            return real(req, *a, **k)

        def trozos():
            for _ in range(64):
                leidos.append(1)
                yield b"0" * 65536

        with mock.patch.object(RequestStarlette, "form", _form):
            # Un generador viaja como `Transfer-Encoding: chunked`, sin Content-Length.
            r = self.c.post(f"{RAIZ}/7/archivos", content=trozos(),
                            headers={"content-type": "multipart/form-data; boundary=x"})
            self.assertNotIn("content-length", r.request.headers)
            self.assertEqual((r.status_code, abiertos), (411, []), r.text)
            self.assertIn("Content-Length", r.json()["detail"])
            for tipo in ("application/x-www-form-urlencoded", "application/json", "text/plain"):
                r = self.c.post(f"{RAIZ}/7/archivos", content=b"pdf=" + b"0" * 4096,
                                headers={"content-type": tipo})
                self.assertEqual((r.status_code, abiertos), (415, []), tipo)
            self.assertIn("multipart/form-data", r.json()["detail"])
            ok = self.c.post(f"{RAIZ}/7/archivos", data={"tipo": "comprobante"},
                             files={"pdf": ("x.pdf", PDF)})
            self.assertEqual((ok.status_code, abiertos), (200, [1]), ok.text)
        m.assert_called_once()

    def test_un_nombre_imposible_no_rompe_la_cabecera(self):
        self.parchar(ov, "bajar_archivo", return_value=("発送.pdf", PDF))
        r = self.c.get(f"{RAIZ}/7/archivos/3")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-disposition"],
                         'attachment; filename="documento.pdf"; '
                         "filename*=UTF-8''%E7%99%BA%E9%80%81.pdf")

    def test_quitar_un_pdf_avisa(self):
        self.identidad = ADMIN
        m = self.parchar(ov, "borrar_archivo", return_value=resp_orden())
        self.assertEqual(self.c.delete(f"{RAIZ}/7/archivos/3").status_code, 200)
        self.assertEqual((m.call_args.args, self.quien(m).rol, ov_bus.version(7)), ((7, 3), "admin", 1))

    # ── chat en vivo ─────────────────────────────────────────────────────────
    def test_con_mensajes_contesta_al_instante(self):
        m = self.parchar(ov, "mensajes", return_value=resp_mensajes(4, 5))
        t0 = time.monotonic()
        r = self.c.get(f"{RAIZ}/7/mensajes", params={"desde_id": 3, "esperar": 20})
        self.assertLess(time.monotonic() - t0, 3)
        self.assertEqual((r.status_code, r.json()["ultimo_id"]), (200, 5))
        m.assert_called_once_with(7, 3)
        self.assertEqual(ov_bus.en_espera(), 0)

    def test_sin_esperar_y_sin_mensajes_tambien_contesta_al_instante(self):
        m = self.parchar(ov, "mensajes", return_value=resp_mensajes())
        r = self.c.get(f"{RAIZ}/7/mensajes")
        self.assertEqual((r.status_code, r.json()["mensajes"]), (200, []))
        m.assert_called_once_with(7, 0)

    def test_esperando_despierta_cuando_alguien_escribe(self):
        lecturas: list[int] = []

        def _mensajes(orden_id, desde_id=0, cur=None):
            lecturas.append(desde_id)
            return resp_mensajes() if len(lecturas) == 1 else resp_mensajes(4)

        self.parchar(ov, "mensajes", side_effect=_mensajes)
        self.parchar(ov, "enviar_mensaje", return_value=resp_mensajes(4))
        salida: dict = {}

        def _pedir() -> None:
            t0 = time.monotonic()
            salida["r"] = self.c.get(f"{RAIZ}/7/mensajes", params={"desde_id": 3, "esperar": 20})
            salida["t"] = time.monotonic() - t0

        hilo = threading.Thread(target=_pedir)
        hilo.start()
        self.hasta(lambda: ov_bus.en_espera() == 1, "la petición nunca llegó a esperar")
        self.assertEqual(lecturas, [3], "mientras espera no vuelve a leer la base")
        self.assertEqual(self.c.post(f"{RAIZ}/7/mensajes", json={"cuerpo": "hola"}).status_code, 200)
        hilo.join(10)
        self.assertFalse(hilo.is_alive(), "el aviso no despertó a quien esperaba")
        self.assertLess(salida["t"], 10, "despertó por el aviso, no porque venciera")
        self.assertEqual([m["id"] for m in salida["r"].json()["mensajes"]], [4])
        self.assertEqual((lecturas, ov_bus.en_espera()), ([3, 3], 0))

    def test_una_transicion_tambien_despierta_al_chat_de_esa_orden(self):
        """No sólo los mensajes: confirmar, entregar o contestar «¿salió?» dejan
        su renglón de bitácora, y el chat abierto lo ve sin esperar a vencer."""
        lecturas: list[int] = []
        self.parchar(ov, "mensajes", side_effect=lambda o, d=0, cur=None: (
            lecturas.append(o) or (resp_mensajes() if len(lecturas) == 1 else resp_mensajes(9))))
        self.parchar(ov, "responder_salio", return_value=resp_orden(7))
        salida: dict = {}
        hilo = threading.Thread(target=lambda: salida.update(
            r=self.c.get(f"{RAIZ}/7/mensajes", params={"esperar": 20})))
        t0 = time.monotonic()
        hilo.start()
        self.hasta(lambda: ov_bus.en_espera() == 1, "la petición nunca llegó a esperar")
        self.c.post(f"{RAIZ}/7/salio", json={"rev": 2, "salio": True})
        hilo.join(10)
        self.assertFalse(hilo.is_alive())
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual([m["id"] for m in salida["r"].json()["mensajes"]], [9])

    def test_el_aviso_de_otra_orden_no_despierta(self):
        lecturas: list[int] = []
        self.parchar(ov, "mensajes",
                     side_effect=lambda o, d=0, cur=None: lecturas.append(o) or resp_mensajes())
        self.parchar(ov, "confirmar", return_value=resp_orden(8))
        hilo = threading.Thread(
            target=lambda: self.c.get(f"{RAIZ}/7/mensajes", params={"esperar": 1.2}))
        t0 = time.monotonic()
        hilo.start()
        self.hasta(lambda: ov_bus.en_espera() == 1, "la petición nunca llegó a esperar")
        self.c.post(f"{RAIZ}/8/confirmar", json={"rev": 1})          # avisa de la 8, no de la 7
        hilo.join(10)
        self.assertGreaterEqual(time.monotonic() - t0, 1.0, "la 7 despertó con el aviso de la 8")

    def test_esperando_tambien_vence_y_relee(self):
        m = self.parchar(ov, "mensajes", return_value=resp_mensajes())
        t0 = time.monotonic()
        r = self.c.get(f"{RAIZ}/7/mensajes", params={"desde_id": 9, "esperar": 0.4})
        dt = time.monotonic() - t0
        self.assertEqual((r.status_code, r.json()["mensajes"]), (200, []))
        self.assertGreaterEqual(dt, 0.35, "no esperó")
        self.assertLess(dt, 5)
        self.assertEqual(m.call_args_list, [mock.call(7, 9), mock.call(7, 9)],
                         "al vencer se relee: la base es la verdad")
        self.assertEqual(ov_bus.en_espera(), 0)

    def test_la_espera_se_sostiene_en_tramos_y_no_se_corta_antes(self):
        """Los tramos de 3 s son para notar que el navegador se fue, no un tope:
        una espera más larga que un tramo dura lo que se pidió."""
        self.assertEqual(ruta._TRAMO_ESPERA_S, 3.0)
        self.assertEqual(ruta._MAX_ESPERA_S, 25)
        m = self.parchar(ov, "mensajes", return_value=resp_mensajes())
        with mock.patch.object(ruta, "_TRAMO_ESPERA_S", 0.15):
            t0 = time.monotonic()
            self.c.get(f"{RAIZ}/7/mensajes", params={"esperar": 0.7})
            dt = time.monotonic() - t0
        self.assertGreaterEqual(dt, 0.65, "un tramo cortó la espera")
        self.assertLess(dt, 5)
        self.assertEqual(m.call_count, 2)

    def test_un_aviso_entre_la_lectura_y_la_espera_no_se_pierde(self):
        lecturas: list[int] = []

        def _mensajes(orden_id, desde_id=0, cur=None):
            lecturas.append(desde_id)
            if len(lecturas) == 1:
                ov_bus.avisar(orden_id)      # alguien escribió justo después de esta lectura
                return resp_mensajes()
            return resp_mensajes(4)

        self.parchar(ov, "mensajes", side_effect=_mensajes)
        t0 = time.monotonic()
        r = self.c.get(f"{RAIZ}/7/mensajes", params={"esperar": 20})
        self.assertLess(time.monotonic() - t0, 5, "se quedó esperando un aviso que ya había pasado")
        self.assertEqual([m["id"] for m in r.json()["mensajes"]], [4])

    def test_una_orden_que_no_existe_es_404_sin_esperar(self):
        self.parchar(ov, "mensajes", side_effect=ov.NoExiste("No existe la orden de venta 999."))
        t0 = time.monotonic()
        r = self.c.get(f"{RAIZ}/999/mensajes", params={"esperar": 20})
        self.assertEqual(r.status_code, 404)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertEqual(ov_bus.en_espera(), 0)

    def test_rebasado_el_tope_de_esperas_degrada_a_sondeo(self):
        m = self.parchar(ov, "mensajes", return_value=resp_mensajes())
        with mock.patch.object(ov_bus, "TOPE_ESPERAS", 0):
            t0 = time.monotonic()
            r = self.c.get(f"{RAIZ}/7/mensajes", params={"esperar": 20})
        self.assertEqual(r.status_code, 200)
        self.assertLess(time.monotonic() - t0, 3)
        m.assert_called_once()

    def test_esperar_tiene_tope(self):
        self.parchar(ov, "mensajes", return_value=resp_mensajes())
        self.assertEqual(self.c.get(f"{RAIZ}/7/mensajes", params={"esperar": 26}).status_code, 422)
        self.assertEqual(self.c.get(f"{RAIZ}/7/mensajes", params={"esperar": -1}).status_code, 422)

    def test_mandar_un_mensaje_avisa(self):
        m = self.parchar(ov, "enviar_mensaje", return_value=resp_mensajes(4))
        r = self.c.post(f"{RAIZ}/7/mensajes", json={"cuerpo": "¿ya salió?"})
        self.assertEqual((r.status_code, r.json()["ultimo_id"]), (200, 4))
        self.assertEqual((m.call_args.args, m.call_args.kwargs["cuerpo"]), ((7,), "¿ya salió?"))
        self.assertIsNone(m.call_args.kwargs["clave"])
        self.assertEqual(ov_bus.version(7), 1)

    def test_el_mensaje_lleva_su_clave_de_idempotencia(self):
        """Si la respuesta se pierde y se reenvía, no queda dos veces."""
        m = self.parchar(ov, "enviar_mensaje", return_value=resp_mensajes(4))
        self.c.post(f"{RAIZ}/7/mensajes", json={"cuerpo": "hola", "clave": "k-1"})
        self.assertEqual(m.call_args.kwargs["clave"], "k-1")
        self.assertEqual(self.c.post(f"{RAIZ}/7/mensajes",
                                     json={"cuerpo": "hola", "clave": "k" * 81}).status_code, 422)
        self.assertEqual(m.call_count, 1)

    def test_el_tope_de_esperas_es_tambien_por_identidad(self):
        """Una sola sesión ya no puede llenar el tope de todos."""
        self.parchar(ov, "mensajes", return_value=resp_mensajes())
        self.parchar(ov, "confirmar", return_value=resp_orden(7))
        with mock.patch.object(ov_bus, "TOPE_POR_ACTOR", 1):
            hilo = threading.Thread(target=lambda: self.c.get(f"{RAIZ}/7/mensajes",
                                                              params={"esperar": 20}))
            hilo.start()
            self.hasta(lambda: ov_bus.en_espera() == 1, "la primera nunca llegó a esperar")
            t0 = time.monotonic()
            self.assertEqual(self.c.get(f"{RAIZ}/8/mensajes", params={"esperar": 20}).status_code,
                             200)
            self.assertLess(time.monotonic() - t0, 2, "la segunda de Ana debió contestar ya")
            self.identidad = ADMIN                          # otra persona sí espera
            t0 = time.monotonic()
            self.c.get(f"{RAIZ}/8/mensajes", params={"esperar": 0.5})
            self.assertGreaterEqual(time.monotonic() - t0, 0.45)
            self.c.post(f"{RAIZ}/7/confirmar", json={"rev": 1})      # despierta a la de Ana
            hilo.join(10)
        self.assertFalse(hilo.is_alive())
        self.assertEqual((ov_bus.en_espera(), ov_bus._por_actor), (0, {}))

    def hasta(self, condicion, mensaje: str, segundos: float = 5.0) -> None:
        fin = time.monotonic() + segundos
        while time.monotonic() < fin:
            if condicion():
                return
            time.sleep(0.01)
        self.fail(mensaje)

    # ── barrido a mano ───────────────────────────────────────────────────────
    def test_conciliar_es_de_quien_escribe_y_avisa_de_canceladas_y_marcadas(self):
        revisar = self.parchar(ov_auto, "revisar", return_value={
            "ok": True,
            "canceladas": [{"id": 3, "folio": "OV-00003", "estado": "cancelada"},
                           {"id": 4, "folio": "OV-00004", "estado": "entregada_cancelada"}],
            "marcadas": [{"id": 5, "folio": "OV-00005"}]})
        self.identidad = LECTURA
        r = self.c.post(f"{RAIZ}/conciliar")
        self.assertEqual(r.status_code, 403)
        self.assertIn("no permite", r.json()["detail"])
        revisar.assert_not_called()
        self.identidad = PERSONA
        r = self.c.post(f"{RAIZ}/conciliar")
        self.assertEqual((r.status_code, r.json()), (200, revisar.return_value))
        self.assertEqual((ov_bus.version(3), ov_bus.version(4), ov_bus.version(5),
                          ov_bus.version(7)), (1, 2, 3, 0),
                         "también avisa de las que quedaron esperando el «¿salió?»")

    def test_conciliar_con_la_bandera_apagada_lo_dice_y_no_truena(self):
        """Las compuertas viven en `revisar` (para que el job y el botón hagan lo
        mismo): el router entrega su `ok: false` con el motivo, como un 200."""
        revisar = self.parchar(ov_auto, "revisar", return_value={
            "ok": False, "motivo": str(ov.Apagado()), "canceladas": [], "marcadas": []})
        r = self.c.post(f"{RAIZ}/conciliar")
        self.assertEqual((r.status_code, r.json()["ok"]), (200, False))
        self.assertIn("modo prueba", r.json()["motivo"])
        revisar.assert_called_once_with()


# ══════════════════════════════════════════════════════════════════════════════
# El long-poll cuando el navegador se va (a nivel ASGI: el TestClient no sabe
# abortar una petición)
# ══════════════════════════════════════════════════════════════════════════════

class NavegadorQueSeVa(unittest.TestCase):
    """Ni uvicorn ni Starlette cancelan el handler cuando el cliente cierra.
    Antes, cada pestaña que se ocultaba dejaba su espera viva hasta 25 s (un
    lugar del tope del bus) y una lectura a la base para nadie al vencer.

    El `receive` de aquí se porta como el de uvicorn 0.34 (httptools_impl):
    entrega el cuerpo, después ESPERA; al irse el cliente o al terminar la
    respuesta, contesta `http.disconnect`. Y la app lleva un
    `app.middleware("http")` como el de identidad de main.py: detrás de él
    `request.is_disconnected()` contesta siempre False (por eso no basta)."""

    def setUp(self) -> None:
        ov_bus._reiniciar()
        self.addCleanup(ov_bus._reiniciar)
        self.lecturas: list[int] = []
        p = mock.patch.object(
            ov, "mensajes", autospec=True,
            side_effect=lambda o, d=0, cur=None: self.lecturas.append(o) or resp_mensajes())
        p.start()
        self.addCleanup(p.stop)

    def app(self, con_middleware: bool) -> FastAPI:
        app = FastAPI()
        app.include_router(ruta.router)
        if con_middleware:
            @app.middleware("http")
            async def _identidad(request: Request, call_next):
                request.state.identidad = PERSONA
                return await call_next(request)
        return app

    async def pedir(self, app: FastAPI, esperar: float, se_va_a: float | None,
                    avisa_a: float | None = None) -> tuple[float, int, list[str]]:
        estado = {"cliente_fuera": False, "respuesta_completa": False}
        hay = asyncio.Event()
        hay.set()                                   # el cuerpo (vacío) ya llegó
        enviados: list[str] = []

        async def receive() -> dict:
            if not (estado["cliente_fuera"] or estado["respuesta_completa"]):
                await hay.wait()
                hay.clear()
            if estado["cliente_fuera"] or estado["respuesta_completa"]:
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg: dict) -> None:
            enviados.append(msg["type"])
            if msg["type"] == "http.response.body" and not msg.get("more_body"):
                estado["respuesta_completa"] = True
                hay.set()

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "GET", "scheme": "http", "path": f"{RAIZ}/7/mensajes",
                 "raw_path": f"{RAIZ}/7/mensajes".encode(), "root_path": "",
                 "query_string": f"esperar={esperar}".encode(), "headers": [],
                 "client": ("1.1.1.1", 1), "server": ("x", 80)}
        t0 = time.monotonic()
        tarea = asyncio.create_task(app(scope, receive, send))
        en_espera_dentro = 0
        while not tarea.done() and time.monotonic() - t0 < esperar + 5:
            await asyncio.sleep(0.05)
            en_espera_dentro = max(en_espera_dentro, ov_bus.en_espera())
            dt = time.monotonic() - t0
            if se_va_a is not None and dt >= se_va_a and not estado["cliente_fuera"]:
                estado["cliente_fuera"] = True      # connection_lost: bandera + despertar
                hay.set()
            if avisa_a is not None and dt >= avisa_a:
                ov_bus.avisar(7)
                avisa_a = None
        await tarea
        await asyncio.sleep(0.05)                    # el oyente termina tras la respuesta
        self.assertEqual(en_espera_dentro, 1, "la petición nunca llegó a esperar")
        return time.monotonic() - t0, ov_bus.en_espera(), enviados

    def test_si_el_navegador_se_va_la_espera_se_suelta_y_no_se_relee(self):
        for con_middleware in (True, False):
            with self.subTest(middleware=con_middleware):
                self.lecturas.clear()
                dt, quedan, _ = asyncio.run(self.pedir(self.app(con_middleware), 20, se_va_a=0.3))
                self.assertLess(dt, 2, "la espera siguió viva después de que el cliente se fue")
                self.assertEqual(quedan, 0, "el lugar del tope quedó ocupado")
                self.assertEqual(self.lecturas, [7], "se releyó la base para nadie")

    def test_con_el_cliente_ahi_despierta_con_el_aviso_y_vence_y_relee(self):
        app = self.app(True)
        dt, quedan, enviados = asyncio.run(self.pedir(app, 20, se_va_a=None, avisa_a=0.4))
        self.assertLess(dt, 3)
        self.assertEqual((quedan, self.lecturas), (0, [7, 7]))
        self.assertIn("http.response.body", enviados)
        self.lecturas.clear()
        with mock.patch.object(ruta, "_TRAMO_ESPERA_S", 0.2):  # varios tramos: no suelta antes
            dt, quedan, _ = asyncio.run(self.pedir(app, 1.0, se_va_a=None))
        self.assertGreaterEqual(dt, 0.95, "los tramos no deben cortar la espera")
        self.assertEqual((quedan, self.lecturas), (0, [7, 7]))
        self.assertEqual(ruta._OYENTES, set(), "ningún oyente de cierre quedó vivo")


# ══════════════════════════════════════════════════════════════════════════════
# El job del barrido en el scheduler
# ══════════════════════════════════════════════════════════════════════════════

class ProgramadorFalso:
    """Apunta lo que `scheduler.iniciar()` registraría. JAMÁS arranca nada: con
    el de verdad, la prueba echaría a andar el sync de inventario y los sondeos."""

    def __init__(self, **kw) -> None:
        self.jobs: dict[str, tuple] = {}

    def add_job(self, fn, trigger, **kw) -> None:
        self.jobs[kw.get("id")] = (fn, trigger, kw)

    def start(self) -> None:
        pass

    def shutdown(self, wait: bool = False) -> None:
        pass


DSN_FALSO = "postgresql://nadie@127.0.0.1:1/no_se_usa"      # nunca se abre: todo va con dobles


class Programador(unittest.TestCase):
    def registrar(self, variable: bool = False) -> dict[str, tuple]:
        from services import scheduler
        self.assertIsNone(scheduler._scheduler, "ya había un scheduler vivo en este proceso")
        with mock.patch.object(scheduler, "AsyncIOScheduler", ProgramadorFalso), \
                mock.patch.object(scheduler.settings, "ordenes_venta_enabled", variable):
            scheduler.iniciar()
            try:
                return dict(scheduler._scheduler.jobs)
            finally:
                scheduler.detener()

    def correr(self, fn, *, habilitado, revisar=None, dsn: str = DSN_FALSO) -> dict:
        """Corre el job UNA vez con la bandera y el barrido sustituidos. Devuelve
        en qué hilo corrió cada cosa y cuántas veces se llamó."""
        from services import scheduler
        visto: dict = {"habilitado": 0, "revisar": 0}

        def _dar(valor):
            """Un valor tal cual, el resultado de una función, o una excepción LANZADA."""
            if isinstance(valor, BaseException):
                raise valor
            return valor() if callable(valor) else valor

        def _habilitado(*a, **k):
            visto["habilitado"] += 1
            visto["hilo_bandera"] = threading.get_ident()
            return _dar(habilitado)

        def _revisar():
            visto["revisar"] += 1
            visto["hilo_barrido"] = threading.get_ident()
            return _dar(revisar)

        async def caso() -> None:
            visto["hilo_loop"] = threading.get_ident()
            await fn()

        with mock.patch.object(ov, "habilitado", side_effect=_habilitado), \
                mock.patch.object(ov_auto, "revisar", side_effect=_revisar), \
                mock.patch.object(scheduler.settings, "supabase_db_url", dsn):
            asyncio.run(caso())
        return visto

    def test_el_job_se_registra_siempre_diga_lo_que_diga_la_variable(self):
        """La bandera es una FILA: si el job se registrara según la variable al
        arrancar, apagar (o encender) la fila no surtiría hasta el reinicio."""
        for variable in (False, True):
            self.assertIn("ov_auto", self.registrar(variable), f"ORDENES_VENTA_ENABLED={variable}")

    def test_si_este_job_no_se_puede_registrar_el_scheduler_arranca_igual(self):
        """El bloque ahora corre SIEMPRE y es el último antes de `start()`: un
        tropiezo suyo no puede llevarse a los jobs de ventas, stock y guías."""
        from services import scheduler
        arrancados: list[bool] = []

        class Tropieza(ProgramadorFalso):
            def add_job(self, fn, trigger, **kw) -> None:
                if kw.get("id") == "ov_auto":
                    raise RuntimeError("no se pudo registrar")
                super().add_job(fn, trigger, **kw)

            def start(self) -> None:
                arrancados.append(True)

        self.assertIsNone(scheduler._scheduler, "ya había un scheduler vivo en este proceso")
        with mock.patch.object(scheduler, "AsyncIOScheduler", Tropieza), \
                self.assertLogs("omnicanal.scheduler", level="ERROR") as logs:
            scheduler.iniciar()
            try:
                jobs = dict(scheduler._scheduler.jobs)
            finally:
                scheduler.detener()
        self.assertEqual(arrancados, [True], "el scheduler no arrancó")
        self.assertNotIn("ov_auto", jobs)
        self.assertIn("NO se pudo registrar el barrido", logs.output[0])
        # Y los demás jobs que `iniciar()` registra sin condiciones siguen ahí.
        self.assertEqual(set(jobs), set(self.registrar()) - {"ov_auto"})

    def test_corre_cada_3_minutos_sin_encimarse(self):
        antes = datetime.now(timezone.utc)
        fn, disparo, kw = self.registrar()["ov_auto"]
        self.assertEqual((disparo, kw["minutes"], kw["max_instances"], kw["coalesce"]),
                         ("interval", 3, 1, True))
        self.assertIsNotNone(kw["next_run_time"].tzinfo, "con zona: el scheduler va en UTC")
        espera = (kw["next_run_time"] - antes).total_seconds()
        self.assertTrue(110 <= espera <= 130, f"la primera pasada, a los 2 min del boot ({espera})")
        self.assertTrue(asyncio.iscoroutinefunction(fn))

    def test_apagada_no_llama_al_barrido_ni_escribe_una_linea(self):
        fn = self.registrar()["ov_auto"][0]
        ov_bus._reiniciar()
        self.addCleanup(ov_bus._reiniciar)
        with self.assertNoLogs(level="INFO"):
            visto = self.correr(fn, habilitado=False, revisar=AssertionError("no debió correr"))
        self.assertEqual((visto["habilitado"], visto["revisar"]), (1, 0))
        self.assertNotEqual(visto["hilo_bandera"], visto["hilo_loop"],
                            "la bandera lee la base: no puede preguntarse en el loop")

    def test_la_bandera_se_pregunta_en_cada_pasada(self):
        """Encenderla o apagarla surte en la siguiente pasada, sin reiniciar nada."""
        fn = self.registrar()["ov_auto"][0]
        ov_bus._reiniciar()
        self.addCleanup(ov_bus._reiniciar)
        vacio = {"ok": True, "canceladas": [], "marcadas": []}
        corridas = []
        for encendida in (False, True, True, False):
            visto = self.correr(fn, habilitado=encendida, revisar=vacio)
            corridas.append((visto["habilitado"], visto["revisar"]))
        self.assertEqual(corridas, [(1, 0), (1, 1), (1, 1), (1, 0)])

    def test_encendida_corre_en_un_hilo_y_avisa_al_bus_de_canceladas_y_marcadas(self):
        fn = self.registrar()["ov_auto"][0]
        ov_bus._reiniciar()
        self.addCleanup(ov_bus._reiniciar)
        visto = self.correr(fn, habilitado=True, revisar={
            "ok": True, "canceladas": [{"id": 3, "folio": "OV-00003", "estado": "cancelada"}],
            "marcadas": [{"id": 9, "folio": "OV-00009"}]})
        self.assertEqual((visto["habilitado"], visto["revisar"]), (1, 1))
        self.assertNotEqual(visto["hilo_barrido"], visto["hilo_loop"], "el barrido corrió en el loop")
        self.assertEqual((ov_bus.version(3), ov_bus.version(9), ov_bus.version(1)), (1, 2, 0))

    def test_sin_kubera_configurada_ni_pregunta(self):
        """Sin SUPABASE_DB_URL no hay bandera que leer: preguntarla dejaría un
        aviso de «no se pudo leer» cada 3 minutos en un backend local."""
        fn = self.registrar()["ov_auto"][0]
        with self.assertNoLogs(level="INFO"):
            visto = self.correr(fn, habilitado=AssertionError("no debió preguntarse"),
                                revisar=AssertionError("no debió correr"), dsn="")
        self.assertEqual((visto["habilitado"], visto["revisar"]), (0, 0))

    def test_el_job_nunca_lanza_hacia_el_scheduler(self):
        fn = self.registrar()["ov_auto"][0]

        def _se_cae():
            raise RuntimeError("password=hunter2 host=db.interno")

        for donde in ("bandera", "barrido"):
            with self.subTest(donde=donde), \
                    self.assertLogs("omnicanal.scheduler", level="WARNING") as logs:
                if donde == "bandera":
                    self.correr(fn, habilitado=_se_cae, revisar={})
                else:
                    self.correr(fn, habilitado=True, revisar=_se_cae)
            self.assertIn("RuntimeError", logs.output[0])
            self.assertNotIn("hunter2", "\n".join(logs.output), "sólo la clase del error")


# ══════════════════════════════════════════════════════════════════════════════
# El bus en memoria
# ══════════════════════════════════════════════════════════════════════════════

class Bus(unittest.TestCase):
    def setUp(self) -> None:
        ov_bus._reiniciar()
        self.addCleanup(ov_bus._reiniciar)

    def test_la_version_solo_sube_y_es_por_orden(self):
        self.assertEqual((ov_bus.version(1), ov_bus.version(2)), (0, 0))
        ov_bus.avisar(1)
        a = ov_bus.version(1)
        ov_bus.avisar(2)
        ov_bus.avisar(1)
        self.assertGreater(ov_bus.version(1), a)
        self.assertGreater(ov_bus.version(1), ov_bus.version(2))
        self.assertEqual(ov_bus.version(3), 0)
        self.assertEqual(ov_bus.version("1"), ov_bus.version(1), "el id puede llegar como texto")

    def test_esperar_regresa_de_inmediato_si_la_version_ya_avanzo(self):
        async def caso() -> tuple[bool, float]:
            vista = ov_bus.version(5)
            ov_bus.avisar(5)
            t0 = time.monotonic()
            return await ov_bus.esperar(5, vista, 10), time.monotonic() - t0

        hubo, dt = asyncio.run(caso())
        self.assertTrue(hubo)
        self.assertLess(dt, 1)
        self.assertEqual(ov_bus.en_espera(), 0)

    def test_esperar_despierta_con_el_aviso_a_todos_los_que_esperan_esa_orden(self):
        async def caso():
            vista = ov_bus.version(5)
            otra = ov_bus.version(6)
            esperas = [asyncio.create_task(ov_bus.esperar(5, vista, 10)) for _ in range(3)]
            ajena = asyncio.create_task(ov_bus.esperar(6, otra, 0.3))
            await asyncio.sleep(0.05)
            dentro = ov_bus.en_espera()
            t0 = time.monotonic()
            ov_bus.avisar(5)
            return dentro, await asyncio.gather(*esperas), time.monotonic() - t0, await ajena

        dentro, resultados, dt, ajena = asyncio.run(caso())
        self.assertEqual((dentro, resultados), (4, [True, True, True]))
        self.assertLess(dt, 1)
        self.assertFalse(ajena, "el aviso de la orden 5 despertó a quien esperaba la 6")
        self.assertEqual((ov_bus.en_espera(), ov_bus._eventos, ov_bus._esperas), (0, {}, {}))

    def test_esperar_vence_y_no_deja_rastro(self):
        async def caso() -> tuple[bool, float]:
            t0 = time.monotonic()
            return await ov_bus.esperar(5, ov_bus.version(5), 0.2), time.monotonic() - t0

        hubo, dt = asyncio.run(caso())
        self.assertFalse(hubo)
        self.assertGreaterEqual(dt, 0.18)
        self.assertEqual((ov_bus.en_espera(), ov_bus._eventos, ov_bus._esperas), (0, {}, {}))

    def test_sin_segundos_no_espera(self):
        self.assertFalse(asyncio.run(ov_bus.esperar(5, ov_bus.version(5), 0)))

    def test_cancelar_la_espera_no_deja_rastro(self):
        async def caso() -> None:
            t = asyncio.create_task(ov_bus.esperar(5, ov_bus.version(5), 10))
            await asyncio.sleep(0.05)
            self.assertEqual(ov_bus.en_espera(), 1)
            t.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await t

        asyncio.run(caso())
        self.assertEqual((ov_bus.en_espera(), ov_bus._eventos, ov_bus._esperas), (0, {}, {}))

    def test_el_tope_de_esperas(self):
        async def caso():
            with mock.patch.object(ov_bus, "TOPE_ESPERAS", 2):
                dos = [asyncio.create_task(ov_bus.esperar(n, ov_bus.version(n), 0.3)) for n in (1, 2)]
                await asyncio.sleep(0.05)
                lleno = ov_bus.lleno()
                t0 = time.monotonic()
                tercera = await ov_bus.esperar(3, ov_bus.version(3), 10)
                return lleno, tercera, time.monotonic() - t0, await asyncio.gather(*dos)

        lleno, tercera, dt, _ = asyncio.run(caso())
        self.assertTrue(lleno)
        self.assertFalse(tercera)
        self.assertLess(dt, 0.25, "la tercera tenía que contestar de inmediato")
        self.assertFalse(ov_bus.lleno())

    def test_el_tope_por_identidad(self):
        async def caso():
            with mock.patch.object(ov_bus, "TOPE_POR_ACTOR", 2):
                dos = [asyncio.create_task(ov_bus.esperar(n, ov_bus.version(n), 0.3, "ana"))
                       for n in (1, 2)]
                await asyncio.sleep(0.05)
                llena_ana, llena_luis, global_ = (ov_bus.lleno("ana"), ov_bus.lleno("luis"),
                                                  ov_bus.lleno())
                t0 = time.monotonic()
                tercera = await ov_bus.esperar(3, ov_bus.version(3), 10, "ana")
                rapida = time.monotonic() - t0
                de_luis = await ov_bus.esperar(4, ov_bus.version(4), 0.1, "luis")
                await asyncio.gather(*dos)
                return llena_ana, llena_luis, global_, tercera, rapida, de_luis

        llena_ana, llena_luis, global_, tercera, rapida, de_luis = asyncio.run(caso())
        self.assertEqual((llena_ana, llena_luis, global_), (True, False, False))
        self.assertFalse(tercera)
        self.assertLess(rapida, 0.25, "la tercera de Ana tenía que contestar de inmediato")
        self.assertFalse(de_luis, "Luis esperó y venció (no lo frenó el tope de Ana)")
        self.assertEqual((ov_bus.en_espera(), ov_bus._por_actor), (0, {}))

    def test_cancelar_suelta_tambien_el_lugar_de_la_identidad(self):
        async def caso() -> None:
            t = asyncio.create_task(ov_bus.esperar(5, ov_bus.version(5), 10, "ana"))
            await asyncio.sleep(0.05)
            self.assertEqual(ov_bus._por_actor, {"ana": 1})
            t.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await t

        asyncio.run(caso())
        self.assertEqual(ov_bus._por_actor, {})

    def test_limpiar_no_hace_pasar_por_vigente_una_version_vieja(self):
        async def caso():
            with mock.patch.object(ov_bus, "_LIMITE_VERSIONES", 3):
                ov_bus.avisar(1)
                vista_1 = ov_bus.version(1)                     # alguien leyó la 1 aquí…
                vista_9 = ov_bus.version(9)                     # …y la 9, que nadie ha tocado
                espera_2 = asyncio.create_task(ov_bus.esperar(2, ov_bus.version(2), 5))
                await asyncio.sleep(0.02)
                ov_bus.avisar(1)                                # …y después la 1 cambió
                for n in (3, 4, 5):
                    ov_bus.avisar(n)                            # el 4.º rebasa el límite: limpia
                limpio = dict(ov_bus._versiones)
                r1 = await ov_bus.esperar(1, vista_1, 5)        # la 1 se olvidó, pero NO es vigente
                r9 = await ov_bus.esperar(9, vista_9, 5)        # de más: relee y ya (nunca de menos)
                ov_bus.avisar(2)
                return limpio, r1, r9, await espera_2

        t0 = time.monotonic()
        limpio, r1, r9, r2 = asyncio.run(caso())
        self.assertLess(time.monotonic() - t0, 3)
        self.assertEqual(limpio, {}, "sin nadie esperando, las versiones se olvidan")
        self.assertTrue(r1, "una versión vieja pasó por vigente después de limpiar")
        self.assertTrue(r9)
        self.assertTrue(r2, "quien esperaba sobrevive a la limpieza y despierta")
        self.assertGreater(ov_bus.version(77), 0, "el piso subió")

    def test_tras_limpiar_un_aviso_nuevo_sigue_despertando(self):
        async def caso():
            with mock.patch.object(ov_bus, "_LIMITE_VERSIONES", 1):
                ov_bus.avisar(1)
                ov_bus.avisar(2)                                # limpia la 1 y la 2
                vista = ov_bus.version(1)
                t = asyncio.create_task(ov_bus.esperar(1, vista, 5))
                await asyncio.sleep(0.02)
                dentro = ov_bus.en_espera()
                ov_bus.avisar(1)
                return dentro, await t

        self.assertEqual(asyncio.run(caso()), (1, True))


# ══════════════════════════════════════════════════════════════════════════════
# El barrido de cancelaciones del canal, lo que promete SIN base
# ══════════════════════════════════════════════════════════════════════════════

class ErrorPg(Exception):
    def __init__(self, pgcode: str) -> None:
        super().__init__(f"error {pgcode}")
        self.pgcode = pgcode


def fila(orden_id: int = 3, estado: str = "confirmada", canal: str = "tiktok", **mas) -> dict:
    """Una fila de la lectura del barrido (`ov_auto._leer`)."""
    return {"id": orden_id, "folio": f"OV-{orden_id:05d}", "estado": estado, "rev": 2,
            "mp_canal": canal, "mp_cuenta": "CUENTAPRUEBA", "mp_orden": f"V-{orden_id}",
            "en_canal": True, "estado_canal": None, "estado_wc": None, "accion": None,
            "motivo_bitacora": None, **mas}


class Veredicto(unittest.TestCase):
    """`ov_auto._veredictos` es puro: de lo leído, a quién canceló el canal, si el
    paquete iba en camino y con qué referencia."""

    def uno(self, *filas: dict) -> dict | None:
        v = ov_auto._veredictos(list(filas))
        self.assertLessEqual(len(v), 1)
        return v[0] if v else None

    def test_si_el_canal_la_vio_entregada_o_devuelta_se_pregunta_a_bodega(self):
        """Una venta que el canal vio ENTREGADA y después canceló SALIÓ de la
        bodega aunque aquí nadie haya marcado la entrega: soltar su apartado sin
        preguntar sería ofrecer otra vez una pieza que ya no está. Lo mismo si la
        bitácora de Automatización dice que salió y ya regresó."""
        for cols, ref in (({"estado_wc": "cancelled", "estado_canal": "DELIVERED"}, "DELIVERED"),
                          ({"estado_wc": "cancelled", "estado_canal": "completed"}, "completed"),
                          ({"accion": "cancelada_devuelta", "en_canal": False}, "cancelada_devuelta")):
            with self.subTest(**cols):
                v = self.uno(fila(**cols))
                self.assertIsNotNone(v, "el canal la canceló")
                self.assertTrue(v["en_camino"], "no se cancela sola: decide Bodega")
                self.assertEqual(v["ref_canal"], ref)

    def test_cancelada_sin_salir(self):
        for cols in ({"estado_wc": "cancelled", "estado_canal": "CANCELLED"},
                     {"estado_wc": "processing", "estado_canal": "CANCELLED"},
                     {"estado_wc": "cancelled", "estado_canal": "AWAITING_SHIPMENT"},
                     {"estado_wc": " Cancelled ", "estado_canal": None}):
            with self.subTest(**cols):
                v = self.uno(fila(**cols))
                self.assertIsNotNone(v, "el canal la canceló")
                self.assertFalse(v["en_camino"])
                self.assertEqual((v["id"], v["folio"], v["estado"], v["rev"]),
                                 (3, "OV-00003", "confirmada", 2))
        # La referencia es CON QUÉ se supo: el estado del canal cuando lo dice él.
        self.assertEqual(self.uno(fila(estado_wc="cancelled", estado_canal="CANCELLED"))["ref_canal"],
                         "CANCELLED")
        self.assertEqual(self.uno(fila(estado_wc="cancelled",
                                       estado_canal="AWAITING_SHIPMENT"))["ref_canal"],
                         "AWAITING_SHIPMENT")
        self.assertEqual(self.uno(fila(estado_wc="cancelled", estado_canal=None))["ref_canal"], "",
                         "sin estado del canal viaja vacía (el servicio pone la suya)")

    def test_una_venta_viva_no_se_toca(self):
        for cols in ({"estado_wc": "processing", "estado_canal": "AWAITING_SHIPMENT"},
                     {"estado_wc": "processing", "estado_canal": "IN_TRANSIT"},
                     {"estado_wc": "completed", "estado_canal": "DELIVERED", "accion": "creada"},
                     {"estado_wc": None, "estado_canal": None},
                     {"canal": "temu", "estado_canal": "4", "accion": "creada"},
                     {"en_canal": False, "accion": "espera_guia"}):
            with self.subTest(**cols):
                self.assertIsNone(self.uno(fila(**cols)))
        self.assertEqual(ov_auto._veredictos([]), [])

    def test_en_camino_por_el_estado_de_envio_del_canal(self):
        for estado in ("IN_TRANSIT", "AWAITING_COLLECTION", "SHIPPED", "shipped", " in_transit "):
            with self.subTest(estado=estado):
                v = self.uno(fila(estado_wc="cancelled", estado_canal=estado))
                self.assertTrue(v["en_camino"])
                self.assertEqual(v["ref_canal"], estado.strip(), "queda escrito lo que dijo el canal")

    def test_los_numeros_de_temu_solo_valen_para_temu(self):
        # Temu NO actualiza channel.orders al cancelar: la venta se queda en 4
        # (enviada) o 5 (entregada) y quien avisa es la bitácora.
        for estado in ("4", "5"):
            v = self.uno(fila(canal="temu", estado_canal=estado, estado_wc="processing",
                              accion="cancelada"))
            self.assertEqual((v["en_camino"], v["ref_canal"]), (True, estado))
        v = self.uno(fila(canal="temu", estado_canal="2", estado_wc="processing",
                          accion="cancelada"))
        self.assertEqual((v["en_camino"], v["ref_canal"]), (False, "cancelada"),
                         "2 = pagada, por enviar: no va en camino, y la referencia es quien lo dijo")
        # El «3» de Temu ES la cancelación; y un «4» de otro canal no significa nada.
        self.assertFalse(self.uno(fila(canal="temu", estado_canal="3"))["en_camino"])
        self.assertFalse(self.uno(fila(canal="tiktok", estado_canal="4",
                                       estado_wc="cancelled"))["en_camino"])

    def test_en_camino_por_la_bitacora_de_automatizacion(self):
        """`cancelada_revisar` = la entrega ya salió del almacén."""
        v = self.uno(fila(canal="temu", estado_canal="2", estado_wc="processing",
                          accion="cancelada_revisar"))
        self.assertEqual((v["en_camino"], v["ref_canal"]), (True, "cancelada_revisar"))
        # Con las dos señales, la referencia es lo que dijo el CANAL.
        v = self.uno(fila(canal="temu", estado_canal="4", accion="cancelada_revisar"))
        self.assertEqual((v["en_camino"], v["ref_canal"]), (True, "4"))
        # Sólo la bitácora la conoce (la venta no está en channel.orders).
        v = self.uno(fila(canal="temu", en_canal=False, accion="cancelada_sin_orden"))
        self.assertEqual((v["en_camino"], v["ref_canal"]), (False, "cancelada_sin_orden"))

    def test_toda_accion_cancelada_de_la_bitacora_cuenta_y_ninguna_otra(self):
        from services.odoo_ventas_log import ACCIONES_CANCELADA_CANAL
        for accion in ACCIONES_CANCELADA_CANAL:
            self.assertIsNotNone(self.uno(fila(canal="temu", estado_canal="2", accion=accion)), accion)
        for accion in ("creada", "espera_guia", "espera_caducada", "no_se_pudo_confirmar", ""):
            self.assertIsNone(self.uno(fila(canal="temu", estado_canal="2", accion=accion)), accion)

    def test_la_regla_de_cancelada_es_la_misma_de_la_pantalla(self):
        """Una sola fuente: `ordenes_venta.cancelada_en_canal`. Sobre una matriz
        de estados, el barrido y la pantalla opinan lo mismo."""
        for canal in ("tiktok", "temu", "mercado_libre"):
            for ec in (None, "", "3", "4", "CANCELLED", "canceled", "IN_TRANSIT", "DELIVERED"):
                for ew in (None, "processing", "cancelled", "CANCELLED", "completed"):
                    for accion in (None, "creada", "cancelada", "cancelada_revisar"):
                        v = self.uno(fila(canal=canal, estado_canal=ec, estado_wc=ew, accion=accion))
                        self.assertEqual(v is not None,
                                         ov.cancelada_en_canal(canal, ec, ew, accion),
                                         (canal, ec, ew, accion))

    def test_con_dos_filas_de_un_lado_solo_cuenta_si_lo_dicen_todas(self):
        """Dos cuentas que sólo difieren en mayúsculas: cancelar por la venta
        equivocada soltaría un apartado que sí hacía falta."""
        a = fila(estado_wc="cancelled", estado_canal="CANCELLED")
        b = fila(estado_wc="processing", estado_canal="AWAITING_SHIPMENT")
        self.assertIsNone(self.uno(a, b))
        self.assertIsNotNone(self.uno(a, dict(a)))
        # Varias órdenes en la misma lectura: una entrada por orden, en su orden.
        v = ov_auto._veredictos([a, fila(4, estado_wc="processing"),
                                 fila(5, "entregada", estado_wc="cancelled")])
        self.assertEqual([(x["id"], x["estado"]) for x in v], [(3, "confirmada"), (5, "entregada")])

    def test_el_motivo_dice_que_venta_fue_y_cabe(self):
        v = self.uno(fila(canal="mercado_libre", estado_wc="cancelled"))
        self.assertEqual(v["motivo"], "Venta cancelada en Mercado Libre (V-3)")
        v = self.uno(fila(canal="temu", estado_canal="2", accion="cancelada",
                          motivo_bitacora=" Temu canceló la venta "))
        self.assertEqual(v["motivo"],
                         "Venta cancelada en Temu (V-3) · Automatización: Temu canceló la venta")
        v = self.uno(fila(canal="temu", estado_canal="2", accion="cancelada",
                          motivo_bitacora="x" * 900))
        self.assertEqual(len(v["motivo"]), ov.MAX_MOTIVO)
        self.assertGreaterEqual(len(v["motivo"]), ov.MIN_MOTIVO_CANCELAR)
        self.assertEqual(ov_auto._motivo_cancelacion("canal_nuevo", "X-1"),
                         "Venta cancelada en canal_nuevo (X-1)")

    def test_los_rotulos_son_los_de_la_pantalla(self):
        """⚠️ GEMELA de CANALES en frontend/components/ordenes/ui.tsx."""
        ui = BACKEND.parent / "frontend" / "components" / "ordenes" / "ui.tsx"
        if not ui.exists():
            self.skipTest("sin frontend/components/ordenes/ui.tsx")
        pantalla = dict(re.findall(r'\{ id: "(\w+)", rotulo: "([^"]+)", cuentas: \[[^\]]*\], mp: true',
                                   ui.read_text(encoding="utf-8")))
        self.assertEqual(ov_auto._ROTULO_CANAL, pantalla)


class BarridoSinBase(unittest.TestCase):
    def setUp(self) -> None:
        self.reiniciar()
        self.addCleanup(self.reiniciar)
        ov._reiniciar_caida()
        self.addCleanup(ov._reiniciar_caida)

    def reiniciar(self) -> None:
        ov_auto._migracion_avisada = False
        ov_auto._bitacora_avisada = False

    def encendido(self, habilitado: bool = True, tablas: bool = True):
        """La bandera y las tablas, sin tocar la base."""
        pila = mock.patch.object(ov, "habilitado", return_value=habilitado)
        pila2 = mock.patch.object(ov, "tablas_listas", return_value=tablas)
        pila.start()
        pila2.start()
        self.addCleanup(pila.stop)
        self.addCleanup(pila2.stop)

    # ── lo que ya no hace ────────────────────────────────────────────────────
    def test_solo_queda_el_barrido_de_cancelaciones(self):
        """Se fueron la generación automática, el relleno y el interruptor movible
        (las banderas las enciende un acta; `crear_auto` vive en el servicio)."""
        for ido in ("generar", "rellenar", "fijar_interruptor", "habilitado_auto",
                    "estado_interruptor", "conciliar_cancelaciones", "_confirmar_rezagadas"):
            self.assertFalse(hasattr(ov_auto, ido), ido)
        publicas = {n for n in vars(ov_auto) if not n.startswith("_")
                    and callable(getattr(ov_auto, n)) and getattr(ov_auto, n).__module__
                    == ov_auto.__name__}
        self.assertEqual(publicas, {"revisar"})

    def test_el_barrido_solo_lee(self):
        """Aquí no hay ni un INSERT, UPDATE ni DELETE: quien escribe es el servicio."""
        for sql in (ov_auto._SQL_CON_BITACORA, ov_auto._SQL_SIN_BITACORA):
            self.assertTrue(sql.lstrip().lower().startswith("with vivas as"))
            self.assertIsNone(re.search(r"\b(insert|update|delete|truncate|alter|drop)\b", sql,
                                        flags=re.I))
            self.assertNotIn("for update", sql.lower())
            self.assertEqual(sql.count("%(dias)s"), 1)
        fuente = (BACKEND / "services" / "ov_auto.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"sdb\.(execute|execute_returning|get_cursor)\b", fuente))
        # Lo que mira: sólo órdenes vivas ligadas a una venta, y sin las que ya
        # esperan el «¿salió?».
        vivas = ov_auto._SQL_VIVAS
        for trozo in ("o.borrada_at is null", "o.mp_orden is not null", "o.estado = 'borrador'",
                      "o.estado = 'confirmada' and o.canal_cancelo_at is null",
                      "o.estado = 'entregada'", "make_interval(days => %(dias)s)"):
            self.assertIn(trozo, vivas)
        self.assertEqual(ov_auto._DIAS_ENTREGADA_VIVA, 45)
        # La cuenta SIEMPRE se compara (mp_cuenta va en mayúsculas por CHECK).
        self.assertEqual(ov_auto._SQL_CON_BITACORA.count("upper(c.cuenta) = v.mp_cuenta"), 1)
        self.assertEqual(ov_auto._SQL_CON_BITACORA.count("upper(a.cuenta) = v.mp_cuenta"), 1)

    # ── compuertas ───────────────────────────────────────────────────────────
    def test_con_la_bandera_apagada_no_hace_nada_y_no_llena_el_log(self):
        self.encendido(habilitado=False)
        with mock.patch.object(ov_auto.sdb, "fetch_all", side_effect=AssertionError("no")), \
                mock.patch.object(ov, "canal_cancelo", side_effect=AssertionError("no")), \
                mock.patch.object(ov, "cancelar", side_effect=AssertionError("no")), \
                self.assertNoLogs(level="DEBUG"):
            for _ in range(3):
                r = ov_auto.revisar()
        self.assertEqual(r, {"ok": False, "canceladas": [], "marcadas": [],
                             "motivo": str(ov.Apagado())})
        self.assertIn("modo prueba", r["motivo"])

    def test_una_bandera_que_no_se_pudo_leer_dice_que_kubera_no_contesta(self):
        """Apagada y «no se pudo leer» valen lo mismo para decidir (falla cerrado),
        pero el motivo que ve la persona no es el mismo."""
        self.encendido(habilitado=False)
        with mock.patch.object(ov, "en_pausa", return_value=True), \
                mock.patch.object(ov_auto.sdb, "fetch_all", side_effect=AssertionError("no")):
            r = ov_auto.revisar()
        self.assertEqual((r["ok"], r["motivo"]),
                         (False, "kubera no contesta; intenta de nuevo en un momento."))

    def test_sin_las_migraciones_se_dice_una_vez_en_el_log(self):
        self.encendido(tablas=False)
        with mock.patch.object(ov_auto.sdb, "fetch_all", side_effect=AssertionError("no")):
            with self.assertLogs("omnicanal.ov_auto", level="WARNING") as logs:
                r = ov_auto.revisar()
            self.assertEqual(r, {"ok": False, "canceladas": [], "marcadas": [],
                                 "motivo": str(ov.FaltaMigracion())})
            self.assertIn("0064", r["motivo"])
            self.assertEqual(len(logs.output), 1)
            with self.assertNoLogs("omnicanal.ov_auto", level="INFO"):
                self.assertFalse(ov_auto.revisar()["ok"])
                self.assertFalse(ov_auto.revisar()["ok"])

    def test_sin_las_tablas_de_ventas_del_canal_tambien_se_dice(self):
        self.encendido()
        with mock.patch.object(ov_auto.sdb, "fetch_all", side_effect=ErrorPg("42P01")), \
                self.assertLogs("omnicanal.ov_auto", level="WARNING"):
            r = ov_auto.revisar()
        self.assertEqual((r["ok"], r["motivo"]), (False, ov_auto._MSG_SIN_VENTAS))

    # ── nunca lanza ──────────────────────────────────────────────────────────
    def test_revisar_nunca_lanza_y_el_detalle_no_sale(self):
        self.encendido()
        with mock.patch.object(ov_auto.sdb, "fetch_all",
                               side_effect=RuntimeError("password=hunter2 host=db.interno")), \
                self.assertLogs("omnicanal.ov_auto", level="ERROR") as logs:
            r = ov_auto.revisar()
        self.assertEqual(r, {"ok": False, "canceladas": [], "marcadas": [],
                             "motivo": "No se pudo completar la revisión; revisa los logs "
                                       "del backend."})
        self.assertIn("hunter2", "\n".join(logs.output), "el detalle queda en el log")
        # Ni si lo que truena es la propia bandera.
        with mock.patch.object(ov, "habilitado", side_effect=RuntimeError("x")), \
                self.assertLogs("omnicanal.ov_auto", level="ERROR"):
            self.assertFalse(ov_auto.revisar()["ok"])
        self.assertFalse(ov_auto._candado.locked(), "el candado quedó suelto")

    def test_kubera_caida_en_el_barrido_es_una_linea_no_un_traceback(self):
        self.encendido()
        caida = psycopg2.OperationalError('connection to server at "db.interno" failed')
        with mock.patch.object(ov_auto.sdb, "fetch_all", side_effect=caida), \
                self.assertLogs("omnicanal.ordenes_venta", level="WARNING") as logs, \
                self.assertNoLogs("omnicanal.ov_auto", level="ERROR"):
            r = ov_auto.revisar()
            ov_auto.revisar()
        self.assertEqual((r["ok"], r["motivo"]),
                         (False, "kubera no contesta; intenta de nuevo en un momento."))
        self.assertNotIn("db.interno", r["motivo"])
        self.assertEqual(len(logs.output), 1)

    def test_dos_revisiones_a_la_vez_no_se_enciman(self):
        """El job y el botón pueden coincidir: la segunda espera un poco y, si la
        primera sigue, lo dice en vez de pelearse por las mismas órdenes."""
        self.encendido()
        self.assertTrue(ov_auto._candado.acquire(timeout=1))
        try:
            with mock.patch.object(ov_auto, "_ESPERA_CANDADO_S", 0.05), \
                    mock.patch.object(ov_auto.sdb, "fetch_all") as leer:
                r = ov_auto.revisar()
            leer.assert_not_called()
        finally:
            ov_auto._candado.release()
        self.assertEqual((r["ok"], r["canceladas"], r["marcadas"]), (False, [], []))
        self.assertIn("Otra revisión", r["motivo"])
        with mock.patch.object(ov_auto.sdb, "fetch_all", return_value=[]):
            self.assertEqual(ov_auto.revisar(), {"ok": True, "canceladas": [], "marcadas": []},
                             "y el candado quedó suelto")

    # ── la lectura ───────────────────────────────────────────────────────────
    def test_sin_la_bitacora_de_automatizacion_se_sigue_sin_ella(self):
        una = fila(estado_wc="cancelled")
        for codigo in ("42P01", "42703"):          # no existe, o su forma vieja sin `cuenta`
            ov_auto._bitacora_avisada = False
            with mock.patch.object(ov_auto.sdb, "fetch_all",
                                   side_effect=[ErrorPg(codigo), [una]]) as leer, \
                    self.assertLogs("omnicanal.ov_auto", level="WARNING") as logs:
                self.assertEqual(ov_auto._leer(), [una])
            self.assertIn("ops.odoo_sale_orders", leer.call_args_list[0].args[0])
            self.assertNotIn("ops.odoo_sale_orders", leer.call_args_list[1].args[0])
            self.assertEqual(leer.call_args_list[1].args[1], {"dias": 45})
            self.assertIn("Temu", logs.output[0])
        # …y se dice UNA vez por proceso, no cada 3 minutos.
        with mock.patch.object(ov_auto.sdb, "fetch_all", side_effect=[ErrorPg("42P01"), []]), \
                self.assertNoLogs("omnicanal.ov_auto", level="INFO"):
            ov_auto._leer()
        # Otro error NO se confunde con «falta una tabla».
        with mock.patch.object(ov_auto.sdb, "fetch_all", side_effect=ErrorPg("3B001")):
            with self.assertRaises(ErrorPg):
                ov_auto._leer()

    # ── qué se le pide al servicio ───────────────────────────────────────────
    def pasada(self, filas: list[dict], canal_cancelo=None, cancelar=None) -> tuple:
        self.encendido()
        with mock.patch.object(ov_auto.sdb, "fetch_all", return_value=filas), \
                mock.patch.object(ov, "canal_cancelo", autospec=True,
                                  side_effect=canal_cancelo) as cc, \
                mock.patch.object(ov, "cancelar", autospec=True, side_effect=cancelar) as ca:
            return ov_auto.revisar(), cc, ca

    @staticmethod
    def quedo(resultado: str, orden_id: int, estado: str) -> dict:
        return {"resultado": resultado,
                "orden": {"id": orden_id, "folio": f"OV-{orden_id:05d}", "estado": estado}}

    def test_cada_caso_llama_al_servicio_con_lo_suyo(self):
        filas = [fila(1, estado_wc="cancelled", estado_canal="CANCELLED"),
                 fila(2, estado_wc="cancelled", estado_canal="IN_TRANSIT"),
                 fila(3, "entregada", estado_wc="cancelled", estado_canal="CANCELLED"),
                 fila(4, "borrador", estado_wc="cancelled", estado_canal="CANCELLED"),
                 fila(5, estado_wc="processing", estado_canal="AWAITING_SHIPMENT")]
        resultados = {1: self.quedo("cancelada", 1, "cancelada"),
                      2: self.quedo("marcada", 2, "confirmada"),
                      3: self.quedo("entregada_cancelada", 3, "entregada_cancelada")}
        with self.assertLogs("omnicanal.ov_auto", level="WARNING") as logs:
            r, cc, ca = self.pasada(
                filas, canal_cancelo=lambda i, ref, motivo, en_camino, cur=None: resultados[i],
                cancelar=lambda i, rev, quien, motivo="", origen="manual", cur=None: {
                    "ok": True, "orden": {"id": i, "folio": f"OV-{i:05d}", "estado": "cancelada"}})
        self.assertEqual(r, {
            "ok": True,
            "canceladas": [{"id": 1, "folio": "OV-00001", "estado": "cancelada"},
                           {"id": 3, "folio": "OV-00003", "estado": "entregada_cancelada"},
                           {"id": 4, "folio": "OV-00004", "estado": "cancelada"}],
            "marcadas": [{"id": 2, "folio": "OV-00002"}]})
        self.assertEqual([c.args for c in cc.call_args_list], [
            (1, "CANCELLED", "Venta cancelada en TikTok (V-1)", False),
            (2, "IN_TRANSIT", "Venta cancelada en TikTok (V-2)", True),
            (3, "CANCELLED", "Venta cancelada en TikTok (V-3)", False)])
        # El borrador se cancela como cualquier borrador: con su rev, firmado
        # AUTOMATICO y con origen marketplace. La venta viva (5) no se toca.
        ca.assert_called_once_with(4, 2, ov.AUTOMATICO, motivo="Venta cancelada en TikTok (V-4)",
                                   origen="marketplace")
        self.assertEqual(len(logs.output), 4, "una línea por orden movida")
        self.assertIn("¿salió?", logs.output[1])

    def test_lo_que_otro_ya_hizo_no_se_informa_ni_se_avisa(self):
        """El webhook o una persona llegaron antes: es éxito, sin nada que decir."""
        filas = [fila(n, estado_wc="cancelled") for n in (1, 2, 3)]
        resultados = {1: self.quedo("ya_marcada", 1, "confirmada"),
                      2: self.quedo("ya_cancelada", 2, "cancelada"),
                      3: self.quedo("nada", 3, "confirmada")}
        with self.assertNoLogs("omnicanal.ov_auto", level="INFO"):
            r, cc, _ = self.pasada(filas, canal_cancelo=lambda i, *a, **k: resultados[i])
        self.assertEqual(r, {"ok": True, "canceladas": [], "marcadas": []})
        self.assertEqual(cc.call_count, 3)

    def test_un_conflicto_se_salta_y_otro_error_no_detiene_a_las_demas(self):
        filas = [fila(n, estado_wc="cancelled") for n in (1, 2, 3, 4)]

        def _canal(orden_id, ref, motivo, en_camino, cur=None):
            if orden_id == 1:
                raise ov.Conflicto()
            if orden_id == 2:
                raise RuntimeError("password=hunter2")
            if orden_id == 3:
                raise ov.ErrorOV("No se pudo completar la operación; quedó registrado.", status=502)
            return self.quedo("cancelada", 4, "cancelada")

        with self.assertLogs("omnicanal.ov_auto", level="INFO") as logs:
            r, cc, _ = self.pasada(filas, canal_cancelo=_canal)
        self.assertEqual(r, {"ok": True, "marcadas": [],
                             "canceladas": [{"id": 4, "folio": "OV-00004", "estado": "cancelada"}]})
        self.assertEqual(cc.call_count, 4)
        texto = "\n".join(logs.output)
        self.assertIn("siguiente pasada", texto)
        self.assertIn("RuntimeError", texto)
        self.assertNotIn("hunter2", texto, "de un error ajeno sólo sale la clase")
        self.assertIn("quedó registrado", texto, "de un ErrorOV sale su mensaje (es texto nuestro)")

    def test_si_kubera_se_cae_a_media_pasada_lo_ya_hecho_no_se_pierde_del_informe(self):
        filas = [fila(n, estado_wc="cancelled") for n in (1, 2, 3)]

        def _canal(orden_id, ref, motivo, en_camino, cur=None):
            if orden_id == 2:
                raise ov.SinBase()
            return self.quedo("cancelada", orden_id, "cancelada")

        with self.assertLogs("omnicanal.ov_auto", level="WARNING"):
            r, cc, _ = self.pasada(filas, canal_cancelo=_canal)
        self.assertEqual((r["ok"], r["motivo"]),
                         (False, "kubera no contesta; intenta de nuevo en un momento."))
        self.assertEqual(r["canceladas"], [{"id": 1, "folio": "OV-00001", "estado": "cancelada"}],
                         "la que sí se canceló se informa (y se avisa a su chat)")
        self.assertEqual(cc.call_count, 2, "no se insiste orden por orden con la base caída")
        self.assertFalse(ov_auto._candado.locked())


if __name__ == "__main__":
    unittest.main()
