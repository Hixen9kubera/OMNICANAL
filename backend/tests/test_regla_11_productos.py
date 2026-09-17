"""Regla 11 en `routers/productos.py`: nada que espere a la base se llama
síncrono dentro de una corrutina.

── POR QUÉ ESTA PRUEBA ────────────────────────────────────────────────────────
El 17-sep-2026 el vigilante del event loop cachó dos veces al backend de
producción parado —5.1 s y 5.8 s— en `detalle_producto > walmart_panel.datos_de
> supabase_db.fetch_all`. Mientras el loop está parado NO se atiende nada: ni un
webhook de venta de Mercado Libre, ni el panel de nadie. Es el mismo defecto del
apagón del 13-ago (v0.157.0–v0.162.0), que estaba en cinco lugares a la vez.

Las pruebas con espía (`test_flujo_omnicanal.test_nada_bloqueante_...`) fijan las
llamadas de UNA ruta, la que se ejercita. Ésta es estática y mira el archivo
ENTERO: lee el AST, y por cada `async def` busca llamadas `modulo.funcion(...)`
donde `modulo` viene de `services` y la función está definida con `def` (no
`async def`). Si no están envueltas en `asyncio.to_thread`, falla — aunque la
ruta nueva todavía no tenga prueba propia.

No importa el backend ni abre red: solo parsea archivos.

    cd backend && python -m unittest tests.test_regla_11_productos -v
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
ROUTER = BACKEND / "routers" / "productos.py"
sys.path.insert(0, str(BACKEND))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import productos as ruta_prod  # noqa: E402

# Lo que SÍ puede llamarse síncrono, con su razón. Todo esto trabaja en memoria
# sobre la foto del flujo (índice ya armado) o lee una variable de entorno; nada
# de esto abre una conexión.
PERMITIDAS = {
    ("inventario_flujo", "foto_para_peticion"),  # devuelve la foto viva o la apagada
    ("inventario_flujo", "lista_etapa"),         # conjunto ya en memoria
    ("inventario_flujo", "expandir_padres"),     # cruce de conjuntos
    ("inventario_flujo", "ids_woo"),             # traducción SKU → wc_id, en memoria
    ("inventario_flujo", "sello"),               # el sello de una fila, puro
    ("modo_publicacion", "disponible"),          # bool(_dsn()): mira una variable
}


def _alias_de_services(arbol: ast.AST) -> dict[str, str]:
    """`from services import meli, wp_db as db` → {meli: meli, db: wp_db}."""
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


def sincronas_en_corrutinas(ruta: Path) -> list[str]:
    """['detalle_producto:1031 walmart_panel.datos_de', …]."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
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
            # `asyncio.to_thread(modulo.funcion, …)` pasa la función SIN
            # llamarla, así que ni siquiera llega aquí: lo que queda es una
            # llamada de verdad. Solo hay que descartar las que ya se esperan.
            if _funciones(modulo, cache).get(n.func.attr) is not False:
                continue
            if isinstance(padre.get(n), ast.Await):
                continue
            if (modulo, n.func.attr) in PERMITIDAS:
                continue
            fuera.append(f"{fn.name}:{n.lineno} {modulo}.{n.func.attr}")
    return sorted(fuera)


class ReglaOnce(unittest.TestCase):
    def test_ninguna_lectura_sincrona_en_una_corrutina(self):
        fuera = sincronas_en_corrutinas(ROUTER)
        self.assertEqual(fuera, [], "Envuélvelas en await asyncio.to_thread(...): "
                                    + ", ".join(fuera))

    def test_el_detalle_del_producto_espera_sus_seis_lecturas(self):
        """La ruta del atasco del 17-sep, fijada por nombre.

        Si alguien vuelve a poner una de estas seis en línea, la prueba de
        arriba ya lo caza; ésta dice cuál es y por qué importa.
        """
        fuente = ROUTER.read_text(encoding="utf-8")
        inicio = fuente.index("async def detalle_producto")
        cuerpo = fuente[inicio:fuente.index("\nclass ContenidoReq", inicio)]
        for llamada in ("inventario.leer_inventario", "meli.listar", "amazon.por_sku",
                        "tiktok_panel.datos_de", "temu_panel.datos_de",
                        "walmart_panel.datos_de"):
            modulo, funcion = llamada.split(".")
            self.assertIn(f"asyncio.to_thread({modulo}.{funcion}", cuerpo, llamada)
            self.assertNotIn(f" {llamada}(", cuerpo, llamada)


class DetalleEnHilos(unittest.TestCase):
    """La ruta del atasco, ejercitada de verdad: `GET /api/productos/{sku}` con
    los servicios simulados. Comprueba las dos cosas que importan — que las seis
    lecturas pasan por `asyncio.to_thread` y que la respuesta no cambió."""

    def setUp(self):
        app = FastAPI()
        app.include_router(ruta_prod.router)
        self.c = TestClient(app)

        async def _woo(sku):
            return {"sku": sku, "nombre": "Cosa", "wc_id": 7, "estado": "publish",
                    "precio": 100.0, "stock": 3}

        self.parches = [
            mock.patch.object(ruta_prod.woocommerce, "obtener_producto_por_sku", _woo),
            mock.patch.object(ruta_prod.costing_read, "validados",
                              return_value={"costo_total": 50.0}),
            mock.patch.object(ruta_prod.inventario, "leer_inventario",
                              return_value={"SKU-1": {"mercado_libre|BEKURA": {"stock_real": 2}}}),
            mock.patch.object(ruta_prod.meli, "listar", return_value=([{
                "cuenta": "BEKURA", "publicado": True, "item_id": "MLM1", "url": "u",
                "precio": 120.0, "precio_base": 130.0, "stock": 2, "full": True,
                "full_label": "FULL", "categoria_id": "MLM1", "categoria_path": [],
                "estado": "active"}], 1)),
            mock.patch.object(ruta_prod.amazon, "por_sku", return_value=None),
        ]
        for p in self.parches:
            p.start()
            self.addCleanup(p.stop)

    def _paneles(self, espia):
        from services import temu_panel, tiktok_panel, walmart_panel
        for modulo in (tiktok_panel, temu_panel, walmart_panel):
            p = mock.patch.object(modulo, "datos_de", return_value=None)
            p.start()
            self.addCleanup(p.stop)
        return tiktok_panel, temu_panel, walmart_panel

    def test_las_seis_lecturas_van_a_un_hilo_y_la_respuesta_no_cambia(self):
        espia = mock.Mock(wraps=ruta_prod.asyncio.to_thread)
        tk, tm, wm = self._paneles(espia)
        with mock.patch.object(ruta_prod.asyncio, "to_thread", espia):
            r = self.c.get("/api/productos/SKU-1")
        self.assertEqual(r.status_code, 200, r.text)
        cuerpo = r.json()
        self.assertEqual(cuerpo["sku"], "SKU-1")
        self.assertEqual(cuerpo["costo"], 50.0)
        canales = [c["canal"] for c in cuerpo["canales"]]
        self.assertEqual(canales, ["general", "mercado_libre"])
        # El inventario se aplicó sobre la fila de ML (stock_real del cache).
        self.assertEqual(cuerpo["canales"][1]["stock_real"], 2)

        vistas = {c.args[0] for c in espia.call_args_list if c.args}
        for fn in (ruta_prod.inventario.leer_inventario, ruta_prod.meli.listar,
                   ruta_prod.amazon.por_sku, tk.datos_de, tm.datos_de, wm.datos_de):
            self.assertIn(fn, vistas, getattr(fn, "__name__", fn))


if __name__ == "__main__":
    unittest.main()
