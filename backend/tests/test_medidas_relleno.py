"""Peso y medidas de RELLENO: aviso, no freno (`publicar_ready.medidas_de_relleno_ml`).

── POR QUÉ ESTA PRUEBA ────────────────────────────────────────────────────────
Eduardo, 2-nov-2026. Cuando faltan el peso o las medidas, el vendor inventa 1 kg
(«1000 g») y 30×20×15 cm (publisher_core.py ~550-569), y ese relleno también quedó
GUARDADO en Woo: 94 publicados lo tienen exacto. El candado del alta de ML
(`faltan_medidas_ml`) lo deja pasar —son > 0 y su densidad, 0.111 g/cm³, cae dentro
de la ventana— y así debe seguir: puede existir un producto que de verdad mida eso.
Se trata como AVISO: Mercado Envíos cobra el envío con esas medidas.

Lo que fija:

1. La función pura: 1 kg y 30, 20 y 15 cm en CUALQUIER orden, con tolerancia de
   redondeo («1.0», «1.000», «30.0»). Woo guarda el peso en kg: 1000 NO es relleno.
   30×20×15 con 2 kg no lo es. Con alguna medida faltante o que no es número, False
   (de eso se encarga el candado).
2. La vista previa de ML con relleno: `ok` sigue en True (el botón de confirmar
   sigue), sin `frena` ni `motivo`, y el aviso va en su PROPIO campo,
   `aviso_medidas` —el Estudio lo pinta arriba, junto a las cuentas; la caja de
   `avisos` queda al fondo del modal y `preview()` le antepone otros—, una sola vez
   y fuera de `avisos`, corriera o no `preview_crear_ml`, y al re-crear donde la
   borraron. También de punta a punta por `publicar.preview()`. Sin relleno,
   `aviso_medidas` es None. `crear_ml` no cambia: llega al pipeline.
3. El semáforo del Publicador: `avisos_publicar = ["medidas_relleno"]` con la MISMA
   función, sobre las metas efectivas. En un simple, la fila sigue «completa»
   (`faltantes == []`); en una variante, también cuando el relleno viene del padre
   porque las cuatro medidas propias están en 0 (`wp_db.medidas_del_padre`: peso y
   medidas como paquete), y la familia lo cuenta en `variantes_listas.con_relleno`
   (con `relleno_del_padre` si el padre lo tiene guardado). Una variante a medias no
   hereda el relleno: le faltan medidas. Sin metas, ni `faltantes` ni avisos.

Sin red ni base: WordPress, la REST de Woo y Mercado Libre van simulados.

    cd backend && python -m unittest tests.test_medidas_relleno -v
"""
from __future__ import annotations

import asyncio
import itertools
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.schemas import Producto  # noqa: E402
from services import publicar, woocommerce, wp_db  # noqa: E402
from services import publicar_ready as pr  # noqa: E402

AVISO = pr.MSG_MEDIDAS_RELLENO_ML
RELLENO = {"weight": "1", "length": "30", "width": "20", "height": "15"}
REALES = {"weight": "1.2", "length": "30", "width": "20", "height": "10"}


def _prod(**cambios):
    base = {"sku": "X-1", "ml_category_id": "MLM3", "wc_categories": [], "stock": 5,
            "title": "Lámpara de mesa", "price": 500.0, "ml_attrs": {}, "wc_attrs": {},
            "meta": {"ml_categoria_id": "MLM3"}, **RELLENO}
    base.update(cambios)
    return base


def _de_relleno(aviso: str) -> bool:
    return "30×20×15" in aviso


# ═══════════════════════════ 1. LA FUNCIÓN PURA ════════════════════════════════
class Pura(unittest.TestCase):
    def test_las_tres_medidas_en_cualquier_orden(self):
        for largo, ancho, alto in itertools.permutations(("30", "20", "15")):
            self.assertTrue(pr.medidas_de_relleno_ml(
                {"weight": "1", "length": largo, "width": ancho, "height": alto}),
                (largo, ancho, alto))

    def test_redondeo_y_numeros(self):
        for peso in ("1.0", "1.000", " 1 ", 1, 1.0):
            self.assertTrue(pr.medidas_de_relleno_ml({**RELLENO, "weight": peso}), peso)
        self.assertTrue(pr.medidas_de_relleno_ml(
            {"weight": 1.0, "length": 15.0, "width": "30.000", "height": "20.0"}))

    def test_mil_no_es_relleno_porque_woo_guarda_kg(self):
        self.assertFalse(pr.medidas_de_relleno_ml({**RELLENO, "weight": "1000"}))

    def test_otro_peso_u_otra_medida_no_lo_es(self):
        self.assertFalse(pr.medidas_de_relleno_ml({**RELLENO, "weight": "2"}))
        self.assertFalse(pr.medidas_de_relleno_ml({**RELLENO, "weight": "1.01"}))
        self.assertFalse(pr.medidas_de_relleno_ml({**RELLENO, "height": "16"}))
        self.assertFalse(pr.medidas_de_relleno_ml({**RELLENO, "length": "20"}))   # 20×20×15
        self.assertFalse(pr.medidas_de_relleno_ml(REALES))

    def test_con_faltantes_no_lo_es(self):
        for clave in RELLENO:
            for valor in (None, "", "0", "abc", "1,0"):
                self.assertFalse(pr.medidas_de_relleno_ml({**RELLENO, clave: valor}),
                                 (clave, valor))
            sin = dict(RELLENO)
            sin.pop(clave)
            self.assertFalse(pr.medidas_de_relleno_ml(sin), clave)

    def test_el_relleno_pasa_el_candado(self):
        # Por eso es aviso y no freno: el candado del alta lo deja pasar.
        self.assertIsNone(pr.faltan_medidas_ml(RELLENO))

    def test_el_mensaje_dice_la_consecuencia_sin_jerga(self):
        m = pr.MSG_MEDIDAS_RELLENO_ML
        self.assertIn("1 kg y 30×20×15 cm", m)
        # Empieza por la consecuencia, no por una orden.
        self.assertTrue(m.startswith("Mercado Envíos cobrará el envío"), m)
        self.assertIn("Costos del Estudio", m)
        for jerga in ("vendor", "pipeline", "relleno de publisher"):
            self.assertNotIn(jerga, m)


# ═══════════════════════════ 2. LA VISTA PREVIA DE ML ══════════════════════════
class VistaPrevia(unittest.TestCase):
    REQ = {"sku": "X-1", "wc_id": 5, "campos": {"titulo": "Lámpara de mesa"}}

    def _preview(self, r_crear, prod_alta=None, pubs=(), estados=None):
        revision = mock.AsyncMock(return_value={"actual": None})
        with mock.patch.object(publicar, "_ml_publicaciones", return_value=list(pubs)), \
             mock.patch.object(publicar, "_estados_items_ml", mock.AsyncMock(return_value=estados or {})), \
             mock.patch.object(publicar, "_payload_crear_ml", mock.AsyncMock(return_value=r_crear)), \
             mock.patch.object(publicar, "_prod_alta", mock.AsyncMock(return_value=prod_alta)), \
             mock.patch.object(publicar, "_revision_categoria_ml", revision), \
             mock.patch.object(publicar, "_precio_de_costos", return_value=None):
            r = asyncio.run(publicar._preview_ml(dict(self.REQ)))
        return r, revision

    def _sigue_en_pie(self, r, revision):
        # No frena: `ok` en True (el modal pinta «Confirmar y publicar»), sin motivo,
        # y con la tarjeta de categoría que se confirma.
        self.assertTrue(r["ok"])
        self.assertNotIn("frena", r)
        self.assertNotIn("motivo", r)
        revision.assert_awaited_once()

    def _avisa(self, r):
        # En su propio campo, y NO repetido en `avisos` (se pintaría dos veces).
        self.assertEqual(r["aviso_medidas"], AVISO)
        self.assertFalse(any(_de_relleno(a) for a in r["avisos"]), r["avisos"])

    def _no_avisa(self, r):
        self.assertIsNone(r.get("aviso_medidas"))
        self.assertFalse(any(_de_relleno(a) for a in r["avisos"]), r["avisos"])

    def test_alta_con_relleno_avisa_en_su_campo_y_no_frena(self):
        r_crear = {"ok": True, "payload": {"price": 500.0, "pictures": []}, "prod": _prod(),
                   "avisos": ["Otro aviso de atributos"]}
        r, revision = self._preview(r_crear)
        self._sigue_en_pie(r, revision)
        self._avisa(r)
        self.assertIn("Otro aviso de atributos", r["avisos"])
        self.assertTrue(any("se CREARÁ" in a for a in r["avisos"]))

    def test_alta_sin_relleno_no_avisa(self):
        r_crear = {"ok": True, "payload": {"pictures": []}, "prod": _prod(**REALES), "avisos": []}
        r, revision = self._preview(r_crear)
        self._sigue_en_pie(r, revision)
        self._no_avisa(r)

    def test_si_preview_crear_ml_no_corrio_tambien_avisa(self):
        # Sin payload (no se pudo armar): las medidas se miden aquí con el `prod` leído.
        r, revision = self._preview(None, prod_alta=_prod())
        self._sigue_en_pie(r, revision)
        self._avisa(r)

    def test_un_rechazo_que_no_frena_tambien_avisa(self):
        # «Sin token», «Faltan datos»: no son candados; el relleno se sigue viendo.
        r, revision = self._preview({"ok": False, "motivo": "Faltan datos: categoría ML"},
                                    prod_alta=_prod())
        self._sigue_en_pie(r, revision)
        self._avisa(r)
        self.assertIn("Faltan datos: categoría ML", r["avisos"])

    def test_recrear_donde_la_borraron_avisa_y_conserva_el_crear(self):
        pubs = [{"cuenta": "BEKURA", "item_id": "MLM1"}, {"cuenta": "SANCORFASHION", "item_id": "MLM2"}]
        estados = {"BEKURA": {"vivo": True}, "SANCORFASHION": {"vivo": False}}
        r, revision = self._preview(None, prod_alta=_prod(), pubs=pubs, estados=estados)
        self._sigue_en_pie(r, revision)
        self.assertEqual(r["modo"], "actualizar")
        self._avisa(r)
        self.assertTrue(any(a.startswith("SANCORFASHION:") and "se CREARÁ" in a
                            for a in r["avisos"]), r["avisos"])

    def test_actualizar_una_viva_no_avisa(self):
        # Sin alta no se leen medidas: actualizar no manda peso ni medidas.
        pubs = [{"cuenta": "BEKURA", "item_id": "MLM1"}, {"cuenta": "SANCORFASHION", "item_id": "MLM2"}]
        estados = {"BEKURA": {"vivo": True}, "SANCORFASHION": {"vivo": True}}
        r, revision = self._preview(None, prod_alta=_prod(), pubs=pubs, estados=estados)
        self.assertTrue(r["ok"])
        self._no_avisa(r)
        revision.assert_not_awaited()

    def test_de_punta_a_punta_con_preview_crear_ml_el_aviso_va_una_vez(self):
        # Sin doble del dry-run: `_payload_crear_ml` → `preview_crear_ml` de verdad
        # (candados, token y payload simulados). El candado deja pasar el relleno y
        # el aviso lo pone la vista previa, una sola vez.
        revision = mock.AsyncMock(return_value={"actual": None})
        with mock.patch.object(publicar, "_ml_publicaciones", return_value=[]), \
             mock.patch.object(wp_db, "disponible", return_value=True), \
             mock.patch.object(pr, "configurar"), \
             mock.patch.object(pr, "construir_prod", return_value=_prod()), \
             mock.patch.object(pr.wp_db, "hijas_vivas", return_value=0), \
             mock.patch.object(pr.meli, "_access_token", return_value="tok"), \
             mock.patch.object(pr, "_atributos_ml_para_alta", side_effect=lambda p, c, cu: (p, [])), \
             mock.patch.object(pr.publisher_core, "build_payload",
                               return_value={"price": 500.0, "pictures": [], "category_id": "MLM3"}), \
             mock.patch.object(publicar, "_revision_categoria_ml", revision), \
             mock.patch.object(publicar, "_precio_de_costos", return_value=None):
            r = asyncio.run(publicar._preview_ml(dict(self.REQ)))
        self._sigue_en_pie(r, revision)
        self._avisa(r)
        self.assertEqual(r["payload"]["category_id"], "MLM3")

    def test_por_preview_los_otros_avisos_no_lo_tapan(self):
        # `publicar.preview()` (lo que llama el endpoint) antepone a `avisos` los de
        # fotos, título y padre DESPUÉS de `_preview_ml`: por eso el relleno va en
        # su propio campo y llega intacto, una sola vez.
        r_crear = {"ok": True, "payload": {"price": 500.0, "pictures": []}, "prod": _prod(),
                   "avisos": []}
        revision = mock.AsyncMock(return_value={"actual": None})
        with mock.patch.object(publicar, "_rellenar_desde_guardado", mock.AsyncMock()), \
             mock.patch.object(publicar, "_asegurar_wc_id", mock.AsyncMock()), \
             mock.patch.object(publicar, "_candado_padre", mock.AsyncMock(return_value=[])), \
             mock.patch.object(publicar, "_aviso_imagenes",
                               mock.AsyncMock(return_value="Fotos: vienen del contenido guardado.")), \
             mock.patch.object(publicar, "_aviso_titulo",
                               return_value="Título: esta variante no tiene título propio."), \
             mock.patch.object(publicar, "_ml_publicaciones", return_value=[]), \
             mock.patch.object(publicar, "_payload_crear_ml", mock.AsyncMock(return_value=r_crear)), \
             mock.patch.object(publicar, "_revision_categoria_ml", revision), \
             mock.patch.object(publicar, "_precio_de_costos", return_value=None):
            r = asyncio.run(publicar.preview({**self.REQ, "canal": "mercado_libre"}))
        self._sigue_en_pie(r, revision)
        self._avisa(r)
        self.assertEqual(r["avisos"][0], "Título: esta variante no tiene título propio.")
        self.assertIn("Fotos: vienen del contenido guardado.", r["avisos"])


class AltaSinCambios(unittest.TestCase):
    """`crear_ml` no cambia de comportamiento: con relleno llega al pipeline."""

    def test_con_relleno_llega_a_las_dos_cuentas(self):
        llamadas = []

        def publicar_(p, token, dry, cuenta):
            llamadas.append(cuenta)
            return {"success": False, "error": "prueba"}

        with mock.patch.object(pr, "configurar"), \
             mock.patch.object(pr, "construir_prod", return_value=_prod()), \
             mock.patch.object(pr.wp_db, "hijas_vivas", return_value=0), \
             mock.patch.object(pr.categoria_vectores, "candado_alta", mock.AsyncMock(return_value=None)), \
             mock.patch.object(pr, "_atributos_ml_para_alta", side_effect=lambda p, c, cu: (p, [])), \
             mock.patch.object(pr.meli, "_access_token", return_value="tok"), \
             mock.patch.object(pr.publisher_core, "publish_product", side_effect=publicar_), \
             mock.patch.object(pr, "MAX_INTENTOS_ML", 1):
            asyncio.run(pr.crear_ml("X-1", 1, {}, ["BEKURA", "SANCORFASHION"],
                                    categoria_confirmada="MLM3"))
        self.assertEqual(llamadas, ["BEKURA", "SANCORFASHION"])


# ═══════════════════════════ 3. EL SEMÁFORO DEL PUBLICADOR ═════════════════════
VARIANTE, PADRE, SIMPLE = 50, 40, 7
METAS_RELLENO = {"ml_categoria_id": "MLM1", "_weight": "1.000", "_length": "15",
                 "_width": "30", "_height": "20"}


def _fetch_de(filas):
    """`wp_db._fetch_all` simulado: las filas de postmeta de los ids pedidos."""
    def _fetch(sql, args=None):
        return [f for f in filas if f["post_id"] in (args or ())]
    return _fetch


def _filas(post_id, metas):
    return [{"post_id": post_id, "meta_key": k, "meta_value": v} for k, v in metas.items()]


class AvisosPuros(unittest.TestCase):
    def test_codigos_y_misma_regla_que_la_vista_previa(self):
        self.assertEqual(woocommerce.AVISOS_PUBLICAR, ("medidas_relleno",))
        self.assertEqual(woocommerce.avisos_para_publicar(METAS_RELLENO), ["medidas_relleno"])
        self.assertEqual(woocommerce.avisos_para_publicar(
            {**METAS_RELLENO, "_weight": "1.2", "_height": "10"}), [])
        self.assertEqual(woocommerce.avisos_para_publicar({}), [])

    def test_el_relleno_no_es_faltante(self):
        it = {"precio": 100.0, "precio_base": 120.0, "precio_oferta": None,
              "stock": 3, "imagen": "https://x/a.jpg"}
        self.assertEqual(woocommerce.faltantes_para_publicar(it, METAS_RELLENO), [])


class _Cliente:
    """`woocommerce._client()` de mentira (un GET, una respuesta)."""

    def __init__(self, cuerpo):
        self.cuerpo = cuerpo

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, ruta, params=None):
        cuerpo = self.cuerpo

        class _R:
            status_code = 200
            headers: dict = {}

            def json(self):
                return cuerpo

            def raise_for_status(self):
                return None
        return _R()


class SemaforoSimple(unittest.IsolatedAsyncioTestCase):
    """Un simple de punta a punta: la consulta de metas real (`_fetch_all` simulado)
    y `listar_productos` con la REST de Woo simulada."""

    async def _listar(self, metas):
        producto = {"id": SIMPLE, "sku": "SIM-1", "name": "Producto SIM-1", "status": "publish",
                    "type": "simple", "price": "100", "regular_price": "120", "sale_price": "",
                    "stock_quantity": 3, "images": [{"src": "https://x/s.jpg"}]}
        filas = _filas(SIMPLE, {"_price": "100", "_regular_price": "120", **metas})
        catalogo = [{"wc_id": SIMPLE, "estado": "publish", "stock": 3}]
        with mock.patch.object(wp_db, "disponible", return_value=True), \
             mock.patch.object(wp_db, "_fetch_all", side_effect=_fetch_de(filas)), \
             mock.patch.object(woocommerce, "_client", _Cliente([producto])), \
             mock.patch.object(woocommerce, "variantes_de_productos",
                               new=mock.AsyncMock(side_effect=lambda cli, data: [[] for _ in data])), \
             mock.patch.object(woocommerce, "_categoria_de_producto", new=mock.AsyncMock(return_value=[])), \
             mock.patch.object(woocommerce, "indice_catalogo", new=mock.AsyncMock(return_value=catalogo)):
            items, _, _ = await woocommerce.listar_productos(vista="productos", aplanar=False)
        return items[0]

    async def test_con_relleno_la_fila_esta_completa_y_trae_el_aviso(self):
        it = await self._listar(METAS_RELLENO)
        self.assertEqual(it["faltantes"], [])
        self.assertEqual(it["avisos_publicar"], ["medidas_relleno"])
        self.assertEqual(Producto(**{**it, "canales": []}).avisos_publicar, ["medidas_relleno"])

    async def test_sin_relleno_no_hay_aviso(self):
        it = await self._listar({**METAS_RELLENO, "_weight": "1.2", "_height": "10"})
        self.assertEqual(it["faltantes"], [])
        self.assertEqual(it["avisos_publicar"], [])

    async def test_metas_vacias_se_juzgan_sin_aviso(self):
        # La consulta respondió y el producto no tiene ninguna meta: le faltan cosas,
        # y sin medidas no hay relleno que avisar.
        it = await self._listar({})
        self.assertIn("medidas", it["faltantes"])
        self.assertEqual(it["avisos_publicar"], [])

    async def test_sin_metas_no_se_juzga(self):
        # La consulta no trajo `publicar` (forma vieja o falla): ni faltantes ni
        # avisos. Ausente no es «nada que revisar».
        sin = {SIMPLE: {"precio": 100.0, "precio_base": 120.0, "precio_oferta": None,
                        "costo": None, "costo_variantes": {}}}
        with mock.patch.object(wp_db, "precios_y_costo_por_wc_id", return_value=sin):
            it = await self._listar(METAS_RELLENO)
        self.assertNotIn("faltantes", it)
        self.assertNotIn("avisos_publicar", it)
        modelo = Producto(**{**it, "canales": []})
        self.assertIsNone(modelo.faltantes)
        self.assertIsNone(modelo.avisos_publicar)


class SemaforoDeLaVarianteQueHereda(unittest.TestCase):
    """La variante con las cuatro medidas propias en 0 hereda el paquete del padre
    (`wp_db.medidas_del_padre`), relleno incluido, y el semáforo lo avisa con la
    misma función."""

    def _frescos(self, metas_variante, metas_padre):
        filas = _filas(VARIANTE, {"_price": "100", **metas_variante}) + _filas(PADRE, metas_padre)
        with mock.patch.object(wp_db, "_fetch_all", side_effect=_fetch_de(filas)):
            return wp_db.precios_y_costo_por_wc_id(
                [{"wc_id": VARIANTE, "tipo": "variation", "padre": PADRE}])

    def _familia(self, frescos, f_padre=None):
        it = {"sku": "FAM-1", "nombre": "Familia", "imagen": "https://x/p.jpg",
              "variantes": [{"sku": "FAM-1-NEG", "wc_id": VARIANTE, "precio": 100.0,
                             "stock": 4, "imagen": None}]}
        woocommerce._semaforo_familia(it, f_padre or {"precio": 100.0}, frescos)
        return it

    def test_el_cero_propio_hereda_el_relleno_y_se_avisa(self):
        frescos = self._frescos({"_weight": "0", "_length": "0.0", "_width": "0", "_height": "0.000"},
                                METAS_RELLENO)
        pub = frescos[VARIANTE]["publicar"]
        self.assertEqual(pub["_weight"], "1.000")            # el 0 propio heredó
        it = self._familia(frescos)
        v = it["variantes"][0]
        self.assertEqual(v["faltantes"], [])                 # completa: es aviso, no faltante
        self.assertEqual(v["avisos_publicar"], ["medidas_relleno"])
        self.assertEqual(it["variantes_listas"], {"listas": 1, "total": 1, "con_relleno": 1})
        modelo = Producto(**{**it, "canales": []})
        self.assertEqual(modelo.variantes[0].avisos_publicar, ["medidas_relleno"])
        self.assertEqual(modelo.variantes_listas.con_relleno, 1)

    def test_el_padre_con_el_mismo_relleno_se_senala(self):
        # El relleno sale de UN lugar: el padre lo tiene guardado y las variantes en
        # 0 lo toman de él. El semáforo lo dice para que se corrija ahí.
        frescos = self._frescos({"_weight": "0", "_length": "0", "_width": "0", "_height": "0"},
                                METAS_RELLENO)
        it = self._familia(frescos, {"precio": 100.0, "publicar": dict(METAS_RELLENO)})
        self.assertEqual(it["variantes_listas"],
                         {"listas": 1, "total": 1, "con_relleno": 1, "relleno_del_padre": True})
        self.assertTrue(Producto(**{**it, "canales": []}).variantes_listas.relleno_del_padre)
        # Con el padre sin relleno no se señala.
        it = self._familia(frescos, {"precio": 100.0, "publicar": {**METAS_RELLENO, "_weight": "2"}})
        self.assertNotIn("relleno_del_padre", it["variantes_listas"])

    def test_una_variante_a_medias_no_hereda_el_relleno_del_padre(self):
        # Peso y alto propios, sin largo ni ancho: no es el mismo paquete que el del
        # padre, así que no hereda nada (`wp_db.medidas_del_padre`, caso 3). No trae
        # el relleno, pero le faltan medidas y no cuenta como lista.
        frescos = self._frescos({"_height": "10", "_weight": "1.2"}, METAS_RELLENO)
        pub = frescos[VARIANTE]["publicar"]
        self.assertNotIn("_length", pub)
        self.assertNotIn("_width", pub)
        it = self._familia(frescos)
        v = it["variantes"][0]
        self.assertEqual(v["faltantes"], ["medidas"])
        self.assertEqual(v["avisos_publicar"], [])
        self.assertEqual(it["variantes_listas"], {"listas": 0, "total": 1})
        self.assertEqual(Producto(**{**it, "canales": []}).variantes_listas.con_relleno, 0)

    def test_una_variante_con_su_paquete_real_no_trae_el_relleno(self):
        frescos = self._frescos({"_weight": "1.2", "_length": "30", "_width": "20",
                                 "_height": "10"}, METAS_RELLENO)
        it = self._familia(frescos)
        self.assertEqual(it["variantes"][0]["avisos_publicar"], [])
        self.assertEqual(it["variantes_listas"], {"listas": 1, "total": 1})


if __name__ == "__main__":
    unittest.main()
