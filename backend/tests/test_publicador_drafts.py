"""Los drafts entran al Publicador (paso 1 de saltarse el estado draft).

── POR QUÉ ESTA PRUEBA ────────────────────────────────────────────────────────
Eduardo, 2-nov-2026: el estado draft se salta por completo. Los SKUs en
borrador aparecen directo en el Publicador (`frontend/app/productos/page.tsx`,
vista «productos») y la pestaña Crear Productos se apaga. Hasta hoy la vista
«productos» era publish/pending/ready/private y los ~3,700 drafts nacidos de
Odoo solo se veían en Crear.

Lo que fija:

1. draft e inprogress entran a la vista «productos» en los DOS espejos
   (`woocommerce.VISTAS` y `wp_db._VISTAS_SQL`) y en cada ruta del listado:
   índice cacheado, búsqueda SQL, índice aplanado, categorías y la red de
   seguridad. Crear no cambia.
2. Un SKU EXACTO en draft pegado en «Filtrar SKUs» se encuentra (el complemento
   por `?sku=` ya no los tira a mano), y el complemento respeta la vista.
3. El respaldo sobre la maestra ya no excluye drafts.
4. El ORDEN por stock: lo resuelve el índice cacheado (el stock de un padre es
   la suma de sus variantes), estable por fecha; la consulta SQL suma las hijas
   y desempata.
5. El SEMÁFORO (`faltantes`): qué le falta a cada fila con lo que ya trae, la
   variante heredando del padre, y nada que pintar si no hubo metas. Las medidas
   con la MISMA regla que el candado del alta de ML (densidad incluida), y una
   FAMILIA resumida sobre sus variantes (`variantes_listas`), nunca juzgada con
   las metas del padre, que no se publica.
6. Regla 11: las lecturas de MySQL del listado van en hilo.
7. «Solo borradores» (`estados=borrador`): la cola que daba Crear, igual en los
   dos espejos y en las dos consultas.
8. El refresco forzado del índice (cada carga de la app) no reconstruye un
   índice de menos de un minuto.

Sin red: WordPress, la REST de Woo y kubera van simulados.

    cd backend && python -m unittest tests.test_publicador_drafts -v
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.schemas import Producto  # noqa: E402
from services import woocommerce, wp_db  # noqa: E402

BORRADORES = {"draft", "inprogress"}


def _sql_plano(sql: str) -> str:
    return " ".join(sql.split())


# ─────────────────────────────────────────────────────────────────────────────
# 1 · LA VISTA
# ─────────────────────────────────────────────────────────────────────────────

class VistaProductos(unittest.TestCase):
    def test_draft_e_inprogress_entran_al_publicador(self):
        self.assertTrue(BORRADORES <= woocommerce.VISTAS["productos"])
        self.assertTrue(BORRADORES <= wp_db._VISTAS_SQL["productos"])

    def test_lo_resuelto_sigue_dentro(self):
        for e in ("publish", "pending", "ready", "private"):
            self.assertIn(e, woocommerce.VISTAS["productos"])

    def test_los_dos_espejos_son_iguales(self):
        # Si el índice cuenta una fila que la red de seguridad tira, el
        # Publicador dice «87» y pinta cero.
        self.assertEqual(woocommerce.VISTAS, wp_db._VISTAS_SQL)

    def test_crear_y_omnicanal_no_cambian(self):
        self.assertEqual(woocommerce.VISTAS["crear"], BORRADORES)
        self.assertIsNone(woocommerce.VISTAS["omnicanal"])

    def test_la_basura_de_wordpress_sigue_fuera(self):
        for e in ("trash", "auto-draft", "inherit", "future"):
            self.assertNotIn(e, woocommerce.VISTAS["productos"])

    def test_toda_variante_de_padre_en_borrador_entra_con_o_sin_marca(self):
        # `_estado_efectivo` deja de decidir en el Publicador: las dos ramas
        # del CASE caen dentro de la vista.
        for padre in BORRADORES:
            for marca in (None, "", "2026-09-29"):
                self.assertIn(wp_db._estado_variante(padre, marca),
                              wp_db._VISTAS_SQL["productos"])


class IndiceCacheado(unittest.IsolatedAsyncioTestCase):
    CATALOGO = [
        {"wc_id": 1, "estado": "draft", "stock": 0},
        {"wc_id": 2, "estado": "publish", "stock": 5},
        {"wc_id": 3, "estado": "inprogress", "stock": 9},
        {"wc_id": 4, "estado": "future", "stock": 99},
        {"wc_id": 5, "estado": "pending", "stock": None},
        {"wc_id": 6, "estado": "draft", "stock": 5},
    ]

    def _con(self):
        p = mock.patch.object(woocommerce, "indice_catalogo",
                              new=mock.AsyncMock(return_value=list(self.CATALOGO)))
        p.start()
        self.addCleanup(p.stop)

    async def test_productos_trae_los_borradores_en_orden_de_fecha(self):
        self._con()
        self.assertEqual(await woocommerce.wc_ids_para_vista("productos"),
                         [1, 2, 3, 5, 6])

    async def test_stock_desc_primero_lo_que_tiene_y_estable_por_fecha(self):
        self._con()
        # 3 (9) · 2 y 6 (5, en su orden de fecha) · 1 y 5 (0 y sin dato = 0).
        self.assertEqual(await woocommerce.wc_ids_para_vista("productos", "stock_desc"),
                         [3, 2, 6, 1, 5])

    async def test_stock_asc(self):
        self._con()
        self.assertEqual(await woocommerce.wc_ids_para_vista("productos", "stock_asc"),
                         [1, 5, 2, 6, 3])

    async def test_un_orden_que_no_es_de_stock_deja_la_fecha(self):
        self._con()
        self.assertEqual(await woocommerce.wc_ids_para_vista("productos", "precio_desc"),
                         [1, 2, 3, 5, 6])

    async def test_crear_no_cambia(self):
        self._con()
        self.assertEqual(await woocommerce.wc_ids_para_vista("crear"), [1, 3, 6])


class RefrescoForzadoDelIndice(unittest.IsolatedAsyncioTestCase):
    """`POST /api/sync/catalogo` (cada carga de la app) fuerza `indice_catalogo(
    refrescar=True)`. Con la consulta más cara, un índice de menos de un minuto
    no se reconstruye: cinco recargas seguidas no son cinco consultas."""

    def setUp(self):
        for nombre in ("_cand_cache", "_cand_cache_ts"):
            valor = getattr(woocommerce, nombre)
            self.addCleanup(setattr, woocommerce, nombre, valor)

    async def test_joven_no_reconstruye(self):
        woocommerce._cand_cache = [{"wc_id": 1}]
        woocommerce._cand_cache_ts = woocommerce.time.time() - 5
        with mock.patch.object(woocommerce, "_construir_indice_catalogo",
                               new=mock.AsyncMock(return_value=[])) as construir:
            self.assertEqual(await woocommerce.indice_catalogo(refrescar=True), [{"wc_id": 1}])
        construir.assert_not_awaited()

    async def test_pasado_el_minuto_si_reconstruye(self):
        woocommerce._cand_cache = [{"wc_id": 1}]
        woocommerce._cand_cache_ts = woocommerce.time.time() - 120
        with mock.patch.object(woocommerce, "_construir_indice_catalogo",
                               new=mock.AsyncMock(return_value=[{"wc_id": 2}])) as construir:
            self.assertEqual(await woocommerce.indice_catalogo(refrescar=True), [{"wc_id": 2}])
        construir.assert_awaited_once()


class IndiceCatalogoTraeStock(unittest.TestCase):
    def test_padre_suma_hijas_y_suelto_usa_el_propio(self):
        filas = [
            {"wc_id": 10, "nombre": "Padre", "estado": "draft", "sku": "FAM-1",
             "stock_propio": None, "stock_hijas": 7},
            {"wc_id": 11, "nombre": "Suelto", "estado": "publish", "sku": "SUE-1",
             "stock_propio": "3", "stock_hijas": None},
            {"wc_id": 12, "nombre": "Sin dato", "estado": "draft", "sku": None,
             "stock_propio": "", "stock_hijas": None},
        ]
        capt: list[str] = []
        with mock.patch.object(wp_db, "_fetch_all",
                               side_effect=lambda sql, *a: capt.append(sql) or filas):
            idx = wp_db.indice_catalogo()
        self.assertEqual([f["stock"] for f in idx], [7, 3, None])
        self.assertEqual(idx[2]["sku"], "WC-12")
        sql = _sql_plano(capt[0])
        self.assertIn("GROUP BY v.post_parent", sql)
        self.assertIn("'product_variation'", sql)
        # Una sola consulta: el stock no cuesta un viaje más.
        self.assertEqual(len(capt), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 2 · LAS CONSULTAS SQL
# ─────────────────────────────────────────────────────────────────────────────

class ConsultasSQL(unittest.TestCase):
    def _buscar(self, orden="reciente", vista="productos"):
        capt: dict[str, list] = {"sql": [], "args": []}

        def _fetch(sql, args=None):
            capt["sql"].append(_sql_plano(sql))
            capt["args"].append(args)
            return [{"n": 0}] if "COUNT" in sql else []

        with mock.patch.object(wp_db, "_fetch_all", side_effect=_fetch), \
                mock.patch.object(wp_db, "expandir_con_padres",
                                  side_effect=lambda t, estricto=False: (list(t), {})):
            woocommerce._buscar_wc_ids_wp("bolsa", 1, 40, orden, None, None, vista)
        return capt

    def test_la_busqueda_del_publicador_deja_pasar_borradores(self):
        capt = self._buscar()
        for args in capt["args"]:
            self.assertIn("draft", args)
            self.assertIn("inprogress", args)

    def test_stock_suma_las_hijas_y_desempata(self):
        capt = self._buscar("stock_desc")
        pagina = next(s for s in capt["sql"] if "COUNT" not in s)
        self.assertIn("SELECT SUM(", pagina)
        self.assertIn("vh.post_parent=p.ID", pagina)
        self.assertIn("DESC, p.post_date DESC, p.ID DESC", pagina)
        # La suma vive en el ORDER BY de la página, no en el COUNT.
        conteo = next(s for s in capt["sql"] if "COUNT" in s)
        self.assertNotIn("SUM(", conteo)

    def test_reciente_tambien_desempata(self):
        pagina = next(s for s in self._buscar()["sql"] if "COUNT" not in s)
        self.assertIn("ORDER BY p.post_date DESC, p.ID DESC", pagina)

    def test_indice_plano_del_publicador_trae_borradores_en_las_dos_mitades(self):
        capt: dict[str, list] = {"args": []}

        def _fetch(sql, args=None):
            capt["args"].append(args)
            return [{"n": 0}] if "COUNT" in sql else []

        with mock.patch.object(wp_db, "disponible", return_value=True), \
                mock.patch.object(wp_db, "_fetch_all", side_effect=_fetch):
            wp_db.indice_plano("productos", None, None, None, "stock_desc", 1, 40)
        args = list(capt["args"][0])
        # Productos sueltos + variantes: cada mitad lleva el set entero.
        self.assertEqual(args.count("draft"), 2)
        self.assertEqual(args.count("inprogress"), 2)

    def test_categorias_del_publicador_cuentan_borradores(self):
        capt: dict[str, list] = {"args": []}
        with mock.patch.object(wp_db, "_fetch_all",
                               side_effect=lambda sql, args=None: capt["args"].append(args) or []):
            wp_db.categorias_en_uso("productos")
        self.assertIn("draft", capt["args"][0])
        self.assertIn("inprogress", capt["args"][0])


class SoloBorradores(unittest.TestCase):
    """El filtro «Solo borradores» del Publicador (`estados=borrador`): la cola
    de trabajo que daba Crear, ahora que los borradores viven con lo publicado."""

    def test_los_dos_espejos_lo_conocen_igual(self):
        self.assertEqual(set(woocommerce._ESTADOS_WC["borrador"]), BORRADORES)
        self.assertEqual(wp_db._ESTADOS_PANEL["borrador"], BORRADORES)
        self.assertEqual({k: set(v) for k, v in woocommerce._ESTADOS_WC.items()},
                         wp_db._ESTADOS_PANEL)

    def test_inactivo_sigue_sin_draft(self):
        # Pasar «Inactivos» a la presencia en canales es el paso 4, aparte.
        self.assertNotIn("draft", woocommerce._ESTADOS_WC["inactivo"])

    def test_la_busqueda_sql_filtra_a_draft_e_inprogress(self):
        capt: list = []

        def _fetch(sql, args=None):
            capt.append((_sql_plano(sql), args))
            return [{"n": 0}] if "COUNT" in sql else []

        with mock.patch.object(wp_db, "_fetch_all", side_effect=_fetch):
            woocommerce._buscar_wc_ids_wp(None, 1, 40, "stock_desc", ["borrador"], None,
                                          "productos")
        sql, args = capt[0]
        self.assertEqual(sql.count("p.post_status IN"), 2)   # la vista Y el filtro
        self.assertIn("draft", args)
        self.assertIn("inprogress", args)
        # El filtro (el segundo IN) solo trae los dos estados de borrador.
        self.assertEqual(list(args).count("publish"), 1)      # solo en el de la vista

    def test_el_indice_plano_cruza_la_vista_con_el_filtro(self):
        capt: list = []

        def _fetch(sql, args=None):
            capt.append(args)
            return [{"n": 0}] if "COUNT" in sql else []

        with mock.patch.object(wp_db, "disponible", return_value=True), \
                mock.patch.object(wp_db, "_fetch_all", side_effect=_fetch):
            wp_db.indice_plano("productos", None, None, ["borrador"], "stock_desc", 1, 40)
        args = list(capt[0])
        self.assertNotIn("publish", args)
        self.assertEqual(args.count("draft"), 2)


class RespaldoMaestra(unittest.TestCase):
    def test_sin_wordpress_la_maestra_ya_no_excluye_drafts(self):
        from services import db
        capt: list[str] = []
        with mock.patch.object(wp_db, "disponible", return_value=False), \
                mock.patch.object(db, "fetch_scalar",
                                  side_effect=lambda sql, *a: capt.append(sql) or 0):
            self.assertEqual(
                woocommerce._buscar_wc_ids_db("bolsa", 1, 40, vista="productos"), ([], 0))
        self.assertNotIn("draft", capt[0])
        self.assertIn("wc_id IS NOT NULL", capt[0])


# ─────────────────────────────────────────────────────────────────────────────
# 3 · listar_productos DE PUNTA A PUNTA (REST y MySQL simulados)
# ─────────────────────────────────────────────────────────────────────────────

class _Respuesta:
    def __init__(self, cuerpo, status=200, headers=None):
        self._cuerpo = cuerpo
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self._cuerpo

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Cliente:
    """Un `_client()` de WooCommerce de mentira: registra cada GET."""

    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.llamadas: list[dict] = []

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, ruta, params=None):
        self.llamadas.append({"ruta": ruta, "params": params or {}})
        return self.respuestas.pop(0)


def _prod(wc_id, sku, status="draft", **extra):
    base = {"id": wc_id, "sku": sku, "name": f"Producto {sku}", "status": status,
            "type": "simple", "price": "100", "regular_price": "120",
            "sale_price": "", "stock_quantity": 3,
            "images": [{"src": f"https://x/{sku}.jpg"}]}
    base.update(extra)
    return base


METAS_COMPLETAS = {"ml_categoria_id": "MLM1", "_weight": "1.2", "_length": "30",
                   "_width": "20", "_height": "10"}


class _ListarBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.precios_hilo: list[int] = []
        self.precios: dict = {}

        def _precios(items):
            self.precios_hilo.append(threading.get_ident())
            self.entrada_precios = items
            return self.precios

        self.disponible_hilo: list[int] = []

        def _disponible():
            self.disponible_hilo.append(threading.get_ident())
            return True

        parches = [
            mock.patch.object(wp_db, "disponible", side_effect=_disponible),
            mock.patch.object(wp_db, "precios_y_costo_por_wc_id", side_effect=_precios),
            mock.patch.object(woocommerce, "variantes_de_productos",
                              new=mock.AsyncMock(side_effect=lambda cli, data: [[] for _ in data])),
            mock.patch.object(woocommerce, "_categoria_de_producto",
                              new=mock.AsyncMock(return_value=[])),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)

    def _con(self, respuestas):
        cli = _Cliente(list(respuestas))
        p = mock.patch.object(woocommerce, "_client", cli)
        p.start()
        self.addCleanup(p.stop)
        return cli


class SkuExactoEnDraft(_ListarBase):
    async def test_pegar_un_sku_en_draft_lo_encuentra(self):
        # La consulta de WordPress no lo resolvió (p. ej. índice atrasado) y el
        # complemento `?sku=` lo trae: antes un `!= "draft"` fijo lo tiraba.
        cli = self._con([_Respuesta([_prod(7, "DRA-0001", "draft")])])
        with mock.patch.object(woocommerce, "_buscar_wc_ids_db", return_value=([], 0)):
            items, total, _ = await woocommerce.listar_productos(
                skus=["DRA-0001"], vista="productos", aplanar=False)
        self.assertEqual([i["sku"] for i in items], ["DRA-0001"])
        self.assertEqual(items[0]["estado"], "draft")
        self.assertEqual(total, 1)
        self.assertEqual(cli.llamadas[0]["params"]["sku"], "DRA-0001")

    async def test_el_complemento_respeta_la_vista(self):
        # En Crear un `publish` no cabe: no debe contar en el total para luego
        # desaparecer en la red de seguridad («1 producto» con la lista vacía).
        self._con([_Respuesta([_prod(8, "PUB-0001", "publish")])])
        with mock.patch.object(woocommerce, "_buscar_wc_ids_db", return_value=([], 0)):
            items, total, _ = await woocommerce.listar_productos(
                skus=["PUB-0001"], vista="crear", aplanar=False)
        self.assertEqual((items, total), ([], 0))

    async def test_las_variaciones_siguen_fuera_del_complemento(self):
        self._con([_Respuesta([_prod(9, "DRA-0001-NEG", "publish", type="variation")])])
        with mock.patch.object(woocommerce, "_buscar_wc_ids_db", return_value=([], 0)):
            items, total, _ = await woocommerce.listar_productos(
                skus=["DRA-0001-NEG"], vista="productos", aplanar=False)
        self.assertEqual((items, total), ([], 0))


class OrdenPorStockSinFiltros(_ListarBase):
    async def test_por_padre_sin_filtros_ordena_en_el_indice_sin_consultar(self):
        catalogo = [{"wc_id": 1, "estado": "draft", "stock": 0},
                    {"wc_id": 2, "estado": "publish", "stock": 4},
                    {"wc_id": 3, "estado": "draft", "stock": 12}]
        cli = self._con([_Respuesta([_prod(1, "A"), _prod(2, "B", "publish"),
                                     _prod(3, "C")])])
        with mock.patch.object(woocommerce, "indice_catalogo",
                               new=mock.AsyncMock(return_value=catalogo)), \
                mock.patch.object(woocommerce, "_buscar_wc_ids_db",
                                  side_effect=AssertionError("no debió consultar")):
            items, total, _ = await woocommerce.listar_productos(
                orden="stock_desc", vista="productos", aplanar=False)
        self.assertEqual([i["wc_id"] for i in items], [3, 2, 1])
        self.assertEqual(total, 3)
        self.assertEqual(cli.llamadas[0]["params"]["include"], "3,2,1")

    async def test_el_precio_sigue_yendo_por_sql(self):
        self._con([])
        with mock.patch.object(woocommerce, "_buscar_wc_ids_db",
                               return_value=([], 0)) as buscar:
            await woocommerce.listar_productos(orden="precio_desc", vista="productos",
                                               aplanar=False)
        buscar.assert_called_once()

    async def test_por_variante_pasa_el_orden_al_indice_plano(self):
        self._con([])
        with mock.patch.object(wp_db, "indice_plano", return_value=([], 0)) as plano:
            await woocommerce.listar_productos(orden="stock_desc", vista="productos",
                                               aplanar=True)
        # (vista, search, skus, estados, orden, page, per_page)
        self.assertEqual(plano.call_args.args[0], "productos")
        self.assertEqual(plano.call_args.args[4], "stock_desc")


class SemaforoEnElListado(_ListarBase):
    async def _listar(self, producto, publicar):
        self.precios = {producto["id"]: {
            "precio": None, "precio_base": None, "precio_oferta": None,
            "costo": None, "costo_variantes": {},
            **({"publicar": publicar} if publicar is not None else {})}}
        self._con([_Respuesta([producto])])
        catalogo = [{"wc_id": producto["id"], "estado": producto["status"], "stock": 1}]
        with mock.patch.object(woocommerce, "indice_catalogo",
                               new=mock.AsyncMock(return_value=catalogo)):
            items, _, _ = await woocommerce.listar_productos(vista="productos",
                                                             aplanar=False)
        return items[0]

    async def test_completo_es_listo_para_publicar(self):
        it = await self._listar(_prod(1, "OK-1", "draft"), METAS_COMPLETAS)
        self.assertEqual(it["faltantes"], [])

    async def test_un_draft_de_odoo_marca_lo_que_le_falta(self):
        it = await self._listar(
            _prod(2, "ODO-1", "draft", price="", regular_price="", stock_quantity=0,
                  images=[]), {})
        self.assertEqual(it["faltantes"],
                         ["categoria_ml", "precio", "stock", "fotos", "medidas"])

    async def test_sin_metas_no_se_juzga(self):
        # La base no devolvió `publicar` (forma vieja o falla): ausente no es
        # «listo», así que la fila no lleva semáforo.
        it = await self._listar(_prod(3, "SIN-1", "draft"), None)
        self.assertNotIn("faltantes", it)
        self.assertIsNone(Producto(**{**it, "canales": []}).faltantes)

    async def test_las_lecturas_de_mysql_van_en_hilo(self):
        # Regla 11: `disponible()` (SELECT 1 cada 5 min) y la consulta de
        # precios ya no corren dentro de la corrutina.
        await self._listar(_prod(4, "HIL-1", "publish"), METAS_COMPLETAS)
        loop = threading.get_ident()
        self.assertTrue(self.precios_hilo)
        self.assertTrue(all(h != loop for h in self.precios_hilo))
        self.assertTrue(all(h != loop for h in self.disponible_hilo))

    async def test_la_variante_manda_su_padre_a_la_consulta(self):
        variante = _prod(50, "FAM-1-NEG", "draft", type="variation", parent_id=40)
        self.precios = {}
        self._con([])
        with mock.patch.object(wp_db, "indice_plano",
                               return_value=([{"wc_id": 50, "tipo": "product_variation",
                                               "parent_id": 40}], 1)), \
                mock.patch.object(wp_db, "variantes_como_productos",
                                  return_value={50: variante}):
            await woocommerce.listar_productos(vista="productos", aplanar=True)
        self.assertEqual(self.entrada_precios,
                         [{"wc_id": 50, "tipo": "variation", "padre": 40}])


class SemaforoDeUnaFamilia(_ListarBase):
    """«Por padre» (el modo por omisión): un padre con variantes NO se juzga con
    sus metas —no se publica en ningún canal—; se resume sobre sus variantes."""

    VARIANTES = [
        {"sku": "FAM-1-NEG", "wc_id": 61, "precio": 100.0, "stock": 4, "imagen": None},
        {"sku": "FAM-1-ROJ", "wc_id": 62, "precio": None, "stock": 0, "imagen": "https://x/r.jpg"},
    ]

    def _fresco(self, publicar, **precios):
        return {"precio": None, "precio_base": None, "precio_oferta": None, "costo": None,
                "costo_variantes": {}, "publicar": publicar, **precios}

    async def _listar(self, precios):
        padre = _prod(60, "FAM-1", "draft", type="variable", stock_quantity=None)
        self.precios = precios
        self._con([_Respuesta([padre])])
        variantes = [dict(v) for v in self.VARIANTES]
        with mock.patch.object(woocommerce, "variantes_de_productos",
                               new=mock.AsyncMock(return_value=[variantes])), \
                mock.patch.object(woocommerce, "indice_catalogo",
                                  new=mock.AsyncMock(return_value=[
                                      {"wc_id": 60, "estado": "draft", "stock": 4}])):
            items, _, _ = await woocommerce.listar_productos(vista="productos", aplanar=False)
        return items[0]

    async def test_el_padre_completo_no_dice_listo_y_resume_sus_variantes(self):
        # El padre trae TODO (categoría, medidas, precio, stock = suma): antes salía
        # «Listo para publicar» y cada canal lo rechazaba por ser padre.
        it = await self._listar({
            60: self._fresco(METAS_COMPLETAS, precio=100.0),
            61: self._fresco(METAS_COMPLETAS, precio_base=120.0),
            62: self._fresco(METAS_COMPLETAS),
        })
        self.assertIsNone(it["faltantes"])
        self.assertEqual(it["variantes_listas"], {"listas": 1, "total": 2})
        neg, roj = it["variantes"]
        self.assertEqual(neg["faltantes"], [])        # sin foto propia: usa la del padre
        # Sin precio propio cae al `_price` del padre (100): solo le falta stock.
        self.assertEqual(roj["faltantes"], ["stock"])
        # Las variantes van en el MISMO lote, con su padre (para heredar metas).
        self.assertIn({"wc_id": 61, "tipo": "variation", "padre": 60}, self.entrada_precios)
        self.assertIn({"wc_id": 62, "tipo": "variation", "padre": 60}, self.entrada_precios)
        modelo = Producto(**{**it, "canales": []})
        self.assertEqual(modelo.variantes_listas.listas, 1)
        self.assertEqual(modelo.variantes[1].faltantes, ["stock"])

    async def test_un_padre_sin_medidas_con_variantes_medidas_no_dice_falta_medidas(self):
        # Las medidas se capturaron variante por variante; el padre no tiene.
        sin_medidas = {"ml_categoria_id": "MLM1"}
        con_medidas = {k: v for k, v in METAS_COMPLETAS.items() if k != "ml_categoria_id"}
        it = await self._listar({
            60: self._fresco(sin_medidas, precio=100.0),
            # (las metas de `publicar` ya vienen con las del padre rellenando huecos)
            61: self._fresco({**sin_medidas, **con_medidas}, precio_base=120.0),
            62: self._fresco({**sin_medidas, **con_medidas}, precio_base=90.0),
        })
        self.assertIsNone(it["faltantes"])
        self.assertEqual(it["variantes"][0]["faltantes"], [])
        self.assertNotIn("medidas", it["variantes"][1]["faltantes"])

    async def test_sin_metas_de_las_variantes_no_hay_resumen(self):
        it = await self._listar({60: self._fresco(METAS_COMPLETAS, precio=100.0)})
        self.assertIsNone(it["faltantes"])
        self.assertIsNone(it["variantes_listas"])


class FaltantesPuro(unittest.TestCase):
    def _it(self, **kw):
        base = {"precio": 100.0, "precio_base": 120.0, "precio_oferta": None,
                "stock": 3, "imagen": "https://x/a.jpg"}
        base.update(kw)
        return base

    def test_orden_de_los_codigos(self):
        self.assertEqual(woocommerce.FALTANTES_PUBLICAR,
                         ("categoria_ml", "precio", "stock", "fotos", "medidas"))

    def test_cualquiera_de_las_dos_metas_de_categoria_vale(self):
        for meta in ("ml_categoria_id", "ml_category_id"):
            metas = {**METAS_COMPLETAS, "ml_categoria_id": ""}
            metas[meta] = "MLM9"
            self.assertNotIn("categoria_ml",
                             woocommerce.faltantes_para_publicar(self._it(), metas))

    def test_precio_cero_o_vacio_falta(self):
        it = self._it(precio=0.0, precio_base=None, precio_oferta=0.0)
        self.assertEqual(woocommerce.faltantes_para_publicar(it, METAS_COMPLETAS),
                         ["precio"])

    def test_solo_oferta_basta(self):
        it = self._it(precio=None, precio_base=None, precio_oferta=80.0)
        self.assertEqual(woocommerce.faltantes_para_publicar(it, METAS_COMPLETAS), [])

    def test_stock_cero_negativo_o_sin_dato_falta(self):
        for s in (0, -2, None):
            self.assertEqual(
                woocommerce.faltantes_para_publicar(self._it(stock=s), METAS_COMPLETAS),
                ["stock"], s)

    def test_una_medida_en_cero_o_basura_falta(self):
        for valor in ("0", "", "abc", None):
            metas = {**METAS_COMPLETAS, "_height": valor}
            self.assertEqual(woocommerce.faltantes_para_publicar(self._it(), metas),
                             ["medidas"], valor)

    def test_sin_foto_falta(self):
        self.assertEqual(
            woocommerce.faltantes_para_publicar(self._it(imagen=None), METAS_COMPLETAS),
            ["fotos"])

    def test_medidas_que_no_cuadran_faltan_como_en_el_candado(self):
        # 500 «kg» en 10×10×10 cm = 500 g/cm³: el candado del alta de ML lo frena,
        # así que el chip no puede decir «Listo para publicar».
        from services import publicar_ready
        metas = {**METAS_COMPLETAS, "_weight": "500", "_length": "10",
                 "_width": "10", "_height": "10"}
        self.assertEqual(woocommerce.faltantes_para_publicar(self._it(), metas), ["medidas"])
        self.assertIsNotNone(publicar_ready.faltan_medidas_ml(
            {"weight": "500", "length": "10", "width": "10", "height": "10"}))
        # Y lo que el candado deja pasar, el chip también.
        self.assertEqual(woocommerce.faltantes_para_publicar(self._it(), METAS_COMPLETAS), [])


class MetasDelSemaforoEnLaConsultaDePrecios(unittest.TestCase):
    """`wp_db.precios_y_costo_por_wc_id` trae las metas del semáforo en el MISMO
    viaje, y la variante hereda las del padre solo donde tiene hueco."""

    def _correr(self, items, filas):
        capt: list[tuple] = []

        def _fetch(sql, args=None):
            capt.append((_sql_plano(sql), args))
            return [f for f in filas if f["post_id"] in (args or ())]

        with mock.patch.object(wp_db, "_fetch_all", side_effect=_fetch):
            salida = wp_db.precios_y_costo_por_wc_id(items)
        return salida, capt

    def test_la_variante_hereda_categoria_y_medidas_del_padre(self):
        filas = [
            {"post_id": 50, "meta_key": "_price", "meta_value": "99"},
            {"post_id": 50, "meta_key": "_weight", "meta_value": "2"},
            {"post_id": 40, "meta_key": "ml_categoria_id", "meta_value": "MLM5"},
            {"post_id": 40, "meta_key": "_weight", "meta_value": "9"},
            {"post_id": 40, "meta_key": "_length", "meta_value": "30"},
            {"post_id": 40, "meta_key": "_price", "meta_value": "1"},
        ]
        salida, capt = self._correr(
            [{"wc_id": 50, "tipo": "variation", "padre": 40}], filas)
        pub = salida[50]["publicar"]
        self.assertEqual(pub["ml_categoria_id"], "MLM5")   # del padre
        self.assertEqual(pub["_weight"], "2")              # la propia gana
        # Peso y medidas van como paquete (`wp_db.medidas_del_padre`): con peso
        # propio, la caja del padre no se le pega.
        self.assertNotIn("_length", pub)
        # El padre se pidió para heredar, pero no es fila ni le roba el precio.
        self.assertNotIn(40, salida)
        self.assertEqual(salida[50]["precio"], 99.0)
        # Un solo viaje: el padre va en el mismo IN que la fila.
        self.assertEqual(len(capt), 1)
        self.assertIn(40, capt[0][1])
        self.assertIn("ml_categoria_id", capt[0][1])

    def test_un_producto_sin_metas_trae_publicar_vacio(self):
        salida, _ = self._correr([{"wc_id": 7, "tipo": "simple"}], [])
        self.assertEqual(salida[7]["publicar"], {})
        self.assertIsNone(salida[7]["precio"])


if __name__ == "__main__":
    unittest.main()
