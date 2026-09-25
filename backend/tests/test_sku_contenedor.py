"""Pruebas de la tabla de contenedores (costing.sku_contenedor, 0060) conectada
a las pantallas, detrás de LEER_SKU_CONTENEDOR.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **El lector** (`services.sku_contenedor`): sin flag no consulta nada; una
   consulta por lote de 800; llave en minúsculas y N de mayor a menor; tabla
   ausente (42P01) o caída → `{}` con UN solo aviso y sin volver a preguntar.
2. **Inventario**: flag apagado = la fila de v0.575 (mismas llaves, mismos
   valores); la tabla gana a Odoo; respaldo cuando la tabla no tiene al SKU;
   multi; la discrepancia se mide contra la tabla («Odoo dice 12; la tabla dice
   11», el caso real de CALZ-0029-GRI-BLN-39); tabla caída → respaldo sin error;
   la regla del 404 de la ficha cuenta la tabla.
3. **Cotejo de cajas**: la N ÚNICA de la tabla abre el packing list antes que
   Odoo; con multi, sin tabla o sin archivo, lo de siempre.
4. **Crear**: tabla → costos_validados, multi como «A - 80 / B - 88».
5. **Costos**: la tabla es la TERCERA fuente del filtro, de los conteos y de la
   columna; las N que solo ella conoce crean su opción; «sin contenedor» la
   excluye. Sin flag, el SQL de v0.574 sin cambiar una letra.

Sin red: nada aquí habla con kubera, Odoo, Drive ni WooCommerce.

    cd backend && python -m unittest tests.test_sku_contenedor -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from config import settings  # noqa: E402
from routers import inventario as ruta_inv  # noqa: E402
from services import costing_read, creacion, packing_cajas  # noqa: E402
from services import embarques as emb  # noqa: E402
from services import inventario_maestro as inv  # noqa: E402
from services import sku_contenedor as sc  # noqa: E402


def _sql(llamada) -> str:
    return " ".join(llamada[0][0].split())


def _c(numero, codigo=None, multi=False, nivel="A", fuentes=("ferraforme",)):
    return {"numero": numero, "codigo": codigo, "nivel": nivel,
            "fuentes": list(fuentes), "multi": multi}


class _UndefinedTable(Exception):
    """Lo que psycopg2 lanza cuando la 0060 no está aplicada."""
    pgcode = "42P01"


class _ConFlag(unittest.TestCase):
    """Flag ENCENDIDO y caché del lector limpia en cada prueba."""
    flag = True

    def setUp(self):
        sc.limpiar_cache()
        self.addCleanup(sc.limpiar_cache)
        p = mock.patch.object(settings, "leer_sku_contenedor", self.flag)
        p.start()
        self.addCleanup(p.stop)


class _SinFlag(_ConFlag):
    flag = False


# ─────────────────────────────────────────────────────────────────────────────
# 1. EL LECTOR
# ─────────────────────────────────────────────────────────────────────────────
class LectorApagado(_SinFlag):
    def test_sin_flag_no_consulta_nada(self):
        with mock.patch.object(sc.sdb, "fetch_all",
                               side_effect=AssertionError("consultó sin flag")) as fa:
            self.assertFalse(sc.activo())
            self.assertEqual(sc.por_sku(["MIC-0001-GRI"]), {})
            self.assertEqual(sc.numeros(), {})
            self.assertEqual(sc.numeros(forzar=True), {})
        fa.assert_not_called()

    def test_el_flag_nace_apagado(self):
        self.assertFalse(type(settings).model_fields["leer_sku_contenedor"].default)


class Lector(_ConFlag):
    def test_una_consulta_por_lote_de_800(self):
        skus = [f"SKU-{i:04d}" for i in range(1700)]
        with mock.patch.object(sc.sdb, "fetch_all", return_value=[]) as fa:
            sc.por_sku(skus)
        self.assertEqual(fa.call_count, 3)
        self.assertIn("where sku = any(%s::citext[])", _sql(fa.call_args_list[0]))
        self.assertIn("from costing.sku_contenedor", _sql(fa.call_args_list[0]))
        self.assertEqual([len(c[0][1][0]) for c in fa.call_args_list], [800, 800, 100])

    def test_llave_en_minusculas_y_n_descendente(self):
        filas = [{"sku": "acc-0096-ros", "numero": 7, "codigo": "TXGU7222939",
                  "nivel": "A", "fuentes": ["ferraforme", "odoo_campo"], "multi": True},
                 {"sku": "ACC-0096-ROS", "numero": 64, "codigo": "149504230930",
                  "nivel": "A", "fuentes": ["ferraforme", "costos"], "multi": True},
                 {"sku": "MIC-0001-GRI", "numero": 80, "codigo": "",
                  "nivel": "B", "fuentes": ["ferraforme"], "multi": False}]
        with mock.patch.object(sc.sdb, "fetch_all", return_value=filas):
            r = sc.por_sku(["ACC-0096-ROS", "acc-0096-ros", " MIC-0001-GRI ", ""])
        self.assertEqual([c["numero"] for c in r["acc-0096-ros"]], [64, 7])
        self.assertEqual(r["mic-0001-gri"], [_c(80, None, nivel="B")])
        self.assertEqual(sc.de(r, "Mic-0001-Gri"), r["mic-0001-gri"])
        self.assertEqual(sc.de(r, "NO-EXISTE"), [])

    def test_leida_sin_esos_skus_es_dict_vacio(self):
        with mock.patch.object(sc.sdb, "fetch_all", return_value=[]):
            self.assertEqual(sc.por_sku(["A-1"]), {})
        self.assertEqual(sc.de(None, "A-1"), [])

    def test_repetidos_se_piden_una_vez(self):
        with mock.patch.object(sc.sdb, "fetch_all", return_value=[]) as fa:
            sc.por_sku(["A-1", "a-1", "A-1 "])
        self.assertEqual(fa.call_args[0][1], (["A-1"],))

    def test_tabla_ausente_avisa_una_vez_y_no_insiste(self):
        with mock.patch.object(sc.sdb, "fetch_all",
                               side_effect=_UndefinedTable("no existe")) as fa, \
                self.assertLogs("omnicanal.sku_contenedor", "WARNING") as logs:
            # None = NO SE LEYÓ (no es lo mismo que «la tabla no lo tiene»).
            self.assertIsNone(sc.por_sku(["A-1"]))
            self.assertIsNone(sc.por_sku(["A-1"]))        # ya no pregunta
            self.assertEqual(sc.numeros(), {})
        self.assertEqual(fa.call_count, 1)
        self.assertEqual(len(logs.records), 1)
        self.assertIn("0060", logs.output[0])

    def test_otra_falla_avisa_una_vez_y_reintenta(self):
        with mock.patch.object(sc.sdb, "fetch_all",
                               side_effect=RuntimeError("pooler")) as fa, \
                self.assertLogs("omnicanal.sku_contenedor", "DEBUG") as logs:
            self.assertIsNone(sc.por_sku(["A-1"]))
            self.assertIsNone(sc.por_sku(["A-1"]))
        self.assertEqual(fa.call_count, 2)
        self.assertEqual([r.levelname for r in logs.records], ["WARNING", "DEBUG"])

    def test_numeros_cache_30_s(self):
        filas = [{"numero": 80, "codigo": "TGHU6894814"}, {"numero": 11, "codigo": None}]
        with mock.patch.object(sc.sdb, "fetch_all", return_value=filas) as fa, \
                mock.patch.object(sc, "time") as reloj:
            reloj.monotonic.return_value = 1000.0
            self.assertEqual(sc.numeros(), {80: "TGHU6894814", 11: None})
            reloj.monotonic.return_value = 1029.0
            sc.numeros()
            self.assertEqual(fa.call_count, 1)
            reloj.monotonic.return_value = 1031.0
            sc.numeros()
            self.assertEqual(fa.call_count, 2)
            sc.numeros(forzar=True)
            self.assertEqual(fa.call_count, 3)
        self.assertIn("group by numero", _sql(fa.call_args))

    def test_etiquetas(self):
        self.assertEqual(sc.etiqueta(_c(11, "MEDU7316591")), "MEDU7316591 - 11")
        self.assertEqual(sc.etiqueta(_c(80)), "Contenedor 80")
        self.assertEqual(sc.etiquetas([_c(88, "B"), _c(80, "A")]), "B - 88 / A - 80")
        self.assertEqual(sc.con_etiqueta([_c(80, "A")])[0]["etiqueta"], "A - 80")


# ─────────────────────────────────────────────────────────────────────────────
# 2. INVENTARIO
# ─────────────────────────────────────────────────────────────────────────────
# Las llaves de una fila de v0.575: con el flag apagado no puede sobrar ni
# faltar ninguna.
LLAVES_V0575 = {
    "sku", "es_referencia", "existe_en_woo", "existe_en_odoo", "nombre", "imagen",
    "wc_id", "odoo_id", "tipo", "es_padre", "n_hijas", "padre_sku", "padre_status",
    "variantes_odoo", "n_variantes_odoo", "odoo_tmpl_id", "odoo_creado",
    "odoo_modificado", "odoo_categoria", "contenedor", "contenedor_fuente",
    "contenedor_es_booking", "embarque", "contenedor_odoo", "contenedor_costo",
    "contenedor_discrepa", "contenedor_no_comparable", "piezas_por_caja", "cajas",
    "cajas_por_llegar", "cbm_caja", "cotejo_cajas", "recorrido", "specs", "stock_woo",
    "stock_odoo", "stock_fisico", "reservado", "stock_full", "stock_fba", "descuadre",
    "recepcion_piezas", "recepcion_desde", "recepcion_dias", "recepcion_ref",
    "recepcion_docs", "ubicaciones", "bodegas", "rack", "bodega", "n_ubicaciones",
    "piezas_en_rack", "piezas_en_stage", "no_vendible", "odoo_duplicado",
    "odoo_archivado", "status_wc", "creado", "modificado", "canales",
    "validacion_bodega", "comercial", "ultimo_paso", "cuadre",
}
CONTENEDOR = ("contenedor", "contenedor_fuente", "contenedor_es_booking", "embarque",
              "contenedor_odoo", "contenedor_costo", "contenedor_discrepa",
              "contenedor_no_comparable")


def _fila(o=None, c=None, contenedores=None, sku="SKU-1"):
    return inv._fila(sku, None, o, c, [], None, None, [], [], contenedores=contenedores)


def _cont(fila):
    return {k: fila[k] for k in CONTENEDOR}


class FilaInventario(unittest.TestCase):
    def test_sin_tabla_la_fila_es_la_de_v0575(self):
        f = _fila({"contenedor": "SZLS50214500 cont 103"}, {"contenedor": "UETU7935912 - 103"})
        self.assertEqual(set(f), LLAVES_V0575)
        self.assertEqual(_cont(f), {
            "contenedor": "SZLS50214500 CONT 103", "contenedor_fuente": "odoo",
            "contenedor_es_booking": True, "embarque": "103",
            "contenedor_odoo": "SZLS50214500 CONT 103",
            "contenedor_costo": "UETU7935912 - 103",
            "contenedor_discrepa": False, "contenedor_no_comparable": False})
        f = _fila(None, {"contenedor": "BEAU6268641 - 97"})
        self.assertEqual((f["contenedor"], f["contenedor_fuente"], f["embarque"]),
                         ("BEAU6268641", "costos_validados", "97"))
        f = _fila({"contenedor": "cont 12"}, {"contenedor": "MEDU7316591 - 11"})
        self.assertTrue(f["contenedor_discrepa"])
        f = _fila({"contenedor": "SZLS50213900"}, {"contenedor": "BEAU6268641 - 97"})
        self.assertTrue(f["contenedor_no_comparable"])

    def test_tabla_gana_a_odoo_y_discrepa_contra_ella(self):
        # CALZ-0029-GRI-BLN-39: Odoo dice 12, la tabla (y costos) dicen 11.
        f = _fila({"contenedor": "MEDU7316591 contenedor 12"},
                  {"contenedor": "MEDU7316591 - 11"},
                  [_c(11, "MEDU7316591", fuentes=["ferraforme", "costos", "odoo_oc"])])
        self.assertEqual(set(f), LLAVES_V0575 | {"contenedores", "contenedor_discrepa_detalle"})
        self.assertEqual(f["contenedor"], "MEDU7316591 - 11")
        self.assertEqual(f["contenedor_fuente"], "tabla")
        self.assertEqual(f["embarque"], "11")
        self.assertFalse(f["contenedor_es_booking"])
        self.assertTrue(f["contenedor_discrepa"])
        self.assertFalse(f["contenedor_no_comparable"])
        self.assertEqual(f["contenedor_discrepa_detalle"], "Odoo dice 12; la tabla dice 11")
        # Los crudos se siguen devolviendo: la tabla no borra lo que dice cada fuente.
        self.assertEqual(f["contenedor_odoo"], "MEDU7316591 CONTENEDOR 12")
        self.assertEqual(f["contenedores"][0]["etiqueta"], "MEDU7316591 - 11")

    def test_costos_y_odoo_discrepan_de_la_tabla(self):
        f = _fila({"contenedor": "cont 12"}, {"contenedor": "X - 13"}, [_c(11, "A")])
        self.assertEqual(f["contenedor_discrepa_detalle"],
                         "Odoo dice 12 y costos 13; la tabla dice 11")
        f = _fila({"contenedor": "cont 11"}, {"contenedor": "X - 13"}, [_c(11, "A")])
        self.assertEqual(f["contenedor_discrepa_detalle"], "costos dice 13; la tabla dice 11")

    def test_sin_contenedor_hoy_la_tabla_lo_ubica(self):
        # MIC-0001-GRI: sin nada en Odoo ni en costos; la tabla dice 80.
        f = _fila(None, None, [_c(80, "TGHU6894814")])
        self.assertEqual((f["contenedor"], f["contenedor_fuente"], f["embarque"]),
                         ("TGHU6894814 - 80", "tabla", "80"))
        self.assertFalse(f["contenedor_discrepa"])
        self.assertEqual(f["contenedor_discrepa_detalle"], "")

    def test_multi_muestra_todas_las_n(self):
        lista = [_c(64, "149504230930", multi=True), _c(7, "TXGU7222939", multi=True)]
        f = _fila({"contenedor": "TXGU7222939 contenedor 7"}, None, lista)
        self.assertEqual(f["contenedor"], "149504230930 - 64 / TXGU7222939 - 7")
        self.assertEqual(f["embarque"], "64 / 7")
        self.assertEqual([c["numero"] for c in f["contenedores"]], [64, 7])
        self.assertFalse(f["contenedor_discrepa"])     # Odoo nombra una N que SÍ está
        self.assertFalse(f["contenedor_es_booking"])   # hay un ISO entre sus códigos

    def test_solo_guias_es_booking(self):
        f = _fila(None, None, [_c(1, "256059868")])
        self.assertTrue(f["contenedor_es_booking"])

    def test_referencia_sin_sufijo_no_inventa_una_n(self):
        # «PRY25-543» es una referencia entera (su N, la 75, sale del Ferraforme):
        # la regex vieja de `_empaque` le sacaba 543 y daría una discrepancia falsa.
        f = _fila(None, {"contenedor": "PRY25-543"}, [_c(75, "PRY25-543")])
        self.assertFalse(f["contenedor_discrepa"])
        f = _fila({"contenedor": "SZLS50213900"}, {"contenedor": "BEAU6268641 - 97"},
                  [_c(97, "BEAU6268641")])
        self.assertFalse(f["contenedor_no_comparable"])   # la tabla ya es el cotejo

    def test_respaldo_cuando_la_tabla_no_tiene_al_sku(self):
        o, c = {"contenedor": "cont 12"}, {"contenedor": "MEDU7316591 - 11"}
        sin = _fila(o, c)
        con = _fila(o, c, [])
        self.assertEqual(_cont(con), _cont(sin))
        self.assertEqual(con["contenedores"], [])
        self.assertEqual(con["contenedor_discrepa_detalle"], "Odoo dice 12; costos dice 11")
        self.assertEqual({k: v for k, v in con.items()
                          if k not in ("contenedores", "contenedor_discrepa_detalle")}, sin)

    def test_texto_del_respaldo_lee_la_n_como_la_tabla(self):
        # ACC-0703-NEG-AZL-L: la regex vieja le saca «0703» a
        # «INHERIT(ACC-0703-CAF) - 54»; el texto debe nombrar la 54.
        o, c = {"contenedor": "MSKU1234567 contenedor 78"}, {"contenedor": "INHERIT(ACC-0703-CAF) - 54"}
        sin, con = _fila(o, c), _fila(o, c, [])
        self.assertTrue(con["contenedor_discrepa"])
        self.assertEqual(_cont(con), _cont(sin))           # la bandera, la de siempre
        self.assertEqual(con["contenedor_discrepa_detalle"], "Odoo dice 78; costos dice 54")
        # Sin número que leer («PRY25-543»), queda lo de `_empaque`.
        con = _fila({"contenedor": "cont 75"}, {"contenedor": "PRY25-543"}, [])
        self.assertEqual(con["contenedor_discrepa_detalle"],
                         f"Odoo dice 75; costos dice {inv._empaque('PRY25-543')['embarque']}")


def _parches_filas(test, od=None, costos=None):
    """Todo lo que `inv.filas` lee, sin red."""
    od, costos = od or {}, costos or {}
    for obj, nombre, valor in (
            (inv.odoo, "detalle_por_sku", od), (inv.odoo, "ubicaciones_por_sku", {}),
            (inv.odoo, "variantes_por_sku", {}), (inv.odoo, "miniaturas_por_sku", {}),
            (inv.odoo, "recibido_por_sku", {}), (inv, "_costos", costos),
            (inv, "_canales", {}), (inv, "_ultimo_proceso", {}),
            (inv.wp_db, "maestro_por_sku", {}), (inv.packing_cajas, "por_sku", {}),
            (inv.packing_cajas, "calentando", set()), (inv.specs, "_por_sku_sync", {}),
            (inv.checklist, "almacen_de", {}), (inv, "_ultimo_empuje", {})):
        p = mock.patch.object(obj, nombre, return_value=valor)
        p.start()
        test.addCleanup(p.stop)


class FilasApagado(_SinFlag):
    def test_sin_flag_no_se_lee_la_tabla(self):
        _parches_filas(self, costos={"A-1": {"contenedor": "MEDU7316591 - 11"}})
        with mock.patch.object(inv.sku_contenedor, "por_sku",
                               side_effect=AssertionError("leyó la tabla")) as ps:
            filas = inv.filas(["A-1"])
        ps.assert_not_called()
        self.assertEqual(set(filas[0]), LLAVES_V0575)
        self.assertEqual(filas[0]["contenedor_fuente"], "costos_validados")


class FilasEncendido(_ConFlag):
    def test_una_lectura_para_toda_la_pagina(self):
        _parches_filas(self, od={"CALZ-0029-GRI-BLN-39": {"contenedor": "cont 12"}},
                       costos={"CALZ-0029-GRI-BLN-39": {"contenedor": "MEDU7316591 - 11"}})
        tabla = {"calz-0029-gri-bln-39": [_c(11, "MEDU7316591")],
                 "mic-0001-gri": [_c(80, "TGHU6894814")]}
        with mock.patch.object(inv.sku_contenedor, "por_sku", return_value=tabla) as ps:
            filas = inv.filas(["CALZ-0029-GRI-BLN-39", "MIC-0001-GRI", "OTRO-1"])
        ps.assert_called_once_with(["CALZ-0029-GRI-BLN-39", "MIC-0001-GRI", "OTRO-1"])
        por = {f["sku"]: f for f in filas}
        self.assertEqual(por["CALZ-0029-GRI-BLN-39"]["contenedor"], "MEDU7316591 - 11")
        self.assertTrue(por["CALZ-0029-GRI-BLN-39"]["contenedor_discrepa"])
        self.assertEqual(por["MIC-0001-GRI"]["contenedor_fuente"], "tabla")
        self.assertEqual(por["OTRO-1"]["contenedores"], [])
        self.assertEqual(por["OTRO-1"]["contenedor_fuente"], "")
        self.assertEqual(inv.resumen(filas)["alertas"]["contenedor_discrepa"], 1)

    def test_tabla_ausente_es_respaldo_sin_error(self):
        _parches_filas(self, costos={"A-1": {"contenedor": "MEDU7316591 - 11"}})
        with mock.patch.object(sc.sdb, "fetch_all", side_effect=_UndefinedTable("x")), \
                self.assertLogs("omnicanal.sku_contenedor", "WARNING"):
            filas = inv.filas(["A-1"])
        self.assertEqual(filas[0]["contenedor_fuente"], "costos_validados")
        self.assertEqual(filas[0]["contenedor"], "MEDU7316591")
        # Sin las llaves nuevas: la fila no afirma «la tabla no lo tiene»
        # de un SKU cuando la tabla no se pudo leer.
        self.assertEqual(set(filas[0]), LLAVES_V0575)
        # Y durante la espera de 5 min, igual, sin volver a preguntar.
        with mock.patch.object(sc.sdb, "fetch_all",
                               side_effect=AssertionError("volvió a preguntar")):
            self.assertEqual(set(inv.filas(["A-1"])[0]), LLAVES_V0575)

    def test_lectura_fallida_es_la_fila_de_v0575(self):
        _parches_filas(self, costos={"A-1": {"contenedor": "MEDU7316591 - 11"}})
        with mock.patch.object(sc.sdb, "fetch_all", side_effect=RuntimeError("pooler")),                 self.assertLogs("omnicanal.sku_contenedor", "WARNING"):
            filas = inv.filas(["A-1"])
        self.assertNotIn("contenedores", filas[0])
        self.assertEqual(set(filas[0]), LLAVES_V0575)


class Regla404(unittest.TestCase):
    def _get(self, fila):
        app = FastAPI()
        app.include_router(ruta_inv.router)
        with mock.patch.object(ruta_inv.inv, "filas", return_value=[fila]):
            return TestClient(app).get("/api/inventario/MIC-0001-GRI")

    def test_la_tabla_cuenta_como_tener_contenedor(self):
        base = {"sku": "MIC-0001-GRI", "existe_en_woo": False, "existe_en_odoo": False,
                "contenedor": ""}
        self.assertEqual(self._get(dict(base)).status_code, 404)
        self.assertEqual(self._get({**base, "contenedores": []}).status_code, 404)
        r = self._get({**base, "contenedores": [{**_c(80, "TGHU6894814"),
                                                 "etiqueta": "TGHU6894814 - 80"}]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._get({**base, "contenedor": "BEAU6268641"}).status_code, 200)


# ─────────────────────────────────────────────────────────────────────────────
# 3. COTEJO DE CAJAS
# ─────────────────────────────────────────────────────────────────────────────
def _ag_cotejo():
    return emb.indexar(emb.agrupar(
        [{"sha": "1" * 64, "nombre": "Ferraforme TGHU6894814 contenedor 80.xlsx",
          "miembro": None, "originales": ["SZLS50216600 CIPL.xlsx"], "skus": 50}], []))


class Cotejo(_ConFlag):
    INVENTARIO = {"f1": "SZLS50216600 CIPL.xlsx", "f2": "OTRO1234567 lista.xlsx",
                  "f3": "TXGU7222939.xlsx"}

    def _resolver(self, tabla, odoo_campo):
        abiertos = []

        def _indexar(archivo, contenedor, *_a, **_k):
            abiertos.append((archivo, contenedor))
            return None

        from services import odoo, packing_drive_carpeta as drive, supabase_db
        with mock.patch.object(supabase_db, "fetch_all", return_value=[]), \
                mock.patch.object(drive, "inventario", return_value=dict(self.INVENTARIO)), \
                mock.patch.object(odoo, "contenedores_por_sku", return_value=odoo_campo), \
                mock.patch.object(sc, "por_sku", return_value=tabla), \
                mock.patch.object(emb, "agrupacion", return_value=_ag_cotejo()), \
                mock.patch.object(packing_cajas, "_indexar", side_effect=_indexar), \
                mock.patch.dict(packing_cajas._cache, clear=True):
            packing_cajas._resolver(list(odoo_campo))
        return sorted(abiertos)

    def test_n_unica_de_la_tabla_abre_su_packing_antes_que_odoo(self):
        # La tabla dice 80; su código (TGHU…, el del Ferraforme) no nombra ningún
        # original, pero el embarque 80 sí trae el SZLS del original.
        abiertos = self._resolver({"mic-0001-gri": [_c(80, "TGHU6894814")]},
                                  {"MIC-0001-GRI": "OTRO1234567 contenedor 12"})
        self.assertEqual(abiertos, [("SZLS50216600 CIPL.xlsx", "SZLS50216600")])

    def test_multi_sigue_con_odoo(self):
        abiertos = self._resolver(
            {"acc-0096-ros": [_c(80, "TGHU6894814", multi=True), _c(7, "TXGU7222939", multi=True)]},
            {"ACC-0096-ROS": "OTRO1234567 contenedor 12"})
        self.assertEqual(abiertos, [("OTRO1234567 lista.xlsx", "OTRO1234567")])

    def test_sin_archivo_de_la_tabla_cae_a_odoo(self):
        abiertos = self._resolver({"calz-1": [_c(11, "MEDU7316591")]},
                                  {"CALZ-1": "OTRO1234567 contenedor 12"})
        self.assertEqual(abiertos, [("OTRO1234567 lista.xlsx", "OTRO1234567")])

    def test_refs_de_tabla(self):
        with mock.patch.object(sc, "por_sku", return_value={
                "a-1": [_c(80, "TGHU6894814")],
                "b-2": [_c(80, "X", multi=True), _c(7, "Y", multi=True)],
                "c-3": [_c(99)]}), \
                mock.patch.object(emb, "agrupacion", return_value=_ag_cotejo()):
            refs = packing_cajas._refs_de_tabla(["A-1", "B-2", "C-3", "D-4"])
        self.assertEqual(refs, {"A-1": ["TGHU6894814", "SZLS50216600"]})

    def test_tabla_ilegible_no_da_refs(self):
        with mock.patch.object(sc, "por_sku", return_value=None), \
                mock.patch.object(emb, "agrupacion", side_effect=AssertionError("agrupó")):
            self.assertEqual(packing_cajas._refs_de_tabla(["A-1"]), {})


class CotejoApagado(_SinFlag):
    def test_sin_flag_ni_se_pregunta(self):
        with mock.patch.object(sc, "por_sku", side_effect=AssertionError("leyó")) as ps:
            self.assertEqual(packing_cajas._refs_de_tabla(["A-1"]), {})
        ps.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 4. CREAR
# ─────────────────────────────────────────────────────────────────────────────
class Crear(_ConFlag):
    def test_tabla_luego_costos(self):
        tabla = {"tec-0002-neg": [_c(88, "B"), _c(80, "A")], "tec-0002-azl": [_c(11, "M")]}
        with mock.patch.object(sc, "por_sku", return_value=tabla), \
                mock.patch.object(creacion, "_contenedores_por_sku",
                                  return_value={"TEC-0002-ROS": "UETU7935912 - 103"}) as cv:
            r = creacion._contenedores_con_tabla(["TEC-0002-NEG", "TEC-0002-AZL", "TEC-0002-ROS"])
        cv.assert_called_once_with(["TEC-0002-ROS"])     # costos solo para el resto
        self.assertEqual(r, {"TEC-0002-NEG": "B - 88 / A - 80", "TEC-0002-AZL": "M - 11",
                             "TEC-0002-ROS": "UETU7935912 - 103"})

    def test_tabla_caida_es_costos(self):
        for leido in (None, {}):      # no se pudo leer · se leyó y no tiene a nadie
            with mock.patch.object(sc, "por_sku", return_value=leido), \
                    mock.patch.object(creacion, "_contenedores_por_sku",
                                      return_value={"A-1": "X - 1"}) as cv:
                self.assertEqual(creacion._contenedores_con_tabla(["A-1"]), {"A-1": "X - 1"})
            cv.assert_called_once_with(["A-1"])

    def _items(self):
        grupos = [
            {"base": "TEC-0002", "costo": 10.0, "valor": 0, "stock": 0, "miembros": [
                {"sku": "TEC-0002-NEG", "wc_id": 1, "sufijo": "NEG", "costo": 10.0},
                {"sku": "TEC-0002-AZL", "wc_id": 2, "sufijo": "AZL", "costo": 10.0}]},
            {"base": "MIC-0001", "costo": 5.0, "valor": 0, "stock": 0, "miembros": [
                {"sku": "MIC-0001-GRI", "wc_id": 3, "sufijo": "GRI", "costo": 5.0}]},
        ]
        vivos = [{"wc_id": i, "sku": s, "nombre": s, "tipo": "simple", "stock": 1}
                 for i, s in ((1, "TEC-0002-NEG"), (2, "TEC-0002-AZL"), (3, "MIC-0001-GRI"))]
        with mock.patch.object(creacion.woocommerce, "productos_por_wc_id",
                               new=mock.AsyncMock(return_value=vivos)):
            return asyncio.run(creacion.items_candidatos(grupos))

    def test_items_con_flag(self):
        tabla = {"tec-0002-azl": [_c(88, "B", multi=True), _c(80, "A", multi=True)],
                 "mic-0001-gri": [_c(80, "TGHU6894814")]}
        with mock.patch.object(sc, "por_sku", return_value=tabla), \
                mock.patch.object(creacion, "_contenedores_por_sku",
                                  return_value={"TEC-0002-NEG": "UETU7935912 - 103"}):
            items = self._items()
        padre, unico = items
        self.assertEqual(padre["contenedor"], "UETU7935912 - 103")   # primer miembro con dato
        self.assertEqual([v["contenedor"] for v in padre["variantes"]],
                         ["UETU7935912 - 103", "B - 88 / A - 80"])
        self.assertEqual(unico["contenedor"], "TGHU6894814 - 80")
        self.assertNotIn("contenedor_fuente", unico)


class CrearApagado(_SinFlag):
    def test_sin_flag_es_costos_validados(self):
        with mock.patch.object(sc, "por_sku", side_effect=AssertionError("leyó")) as ps, \
                mock.patch.object(creacion, "_contenedores_por_sku",
                                  return_value={"MIC-0001-GRI": "X - 1"}) as cv:
            items = Crear._items(self)
        ps.assert_not_called()
        cv.assert_called_once()
        self.assertEqual(items[1]["contenedor"], "X - 1")


# ─────────────────────────────────────────────────────────────────────────────
# 5. COSTOS
# ─────────────────────────────────────────────────────────────────────────────
SHA_1 = "1" * 64
SHA_2 = "2" * 64
NUMEROS = {1: "TRHU6215242", 80: "TGHU6894814", 64: "149504230930"}


def _agrupacion_prueba():
    return emb.indexar(emb.agrupar(
        [{"sha": SHA_1, "nombre": "256059868 TRHU6215242 contenedor 1.xlsx",
          "miembro": None, "originales": [], "skus": 10},
         {"sha": SHA_2, "nombre": "149504230930 contenedor 64.xlsx",
          "miembro": None, "originales": [], "skus": 10}],
        [{"valor": "256059868 - 1", "skus": 578},
         {"valor": "BEAU6268641 - 97", "skus": 312}]))


class SumarTabla(unittest.TestCase):
    def test_suma_fuente_y_crea_las_n_nuevas_sin_tocar_la_cache(self):
        ag = _agrupacion_prueba()
        antes = [dict(g) for g in ag.grupos]
        grupos = emb.sumar_tabla(ag.grupos, NUMEROS)
        self.assertEqual([g["clave"] for g in grupos], ["n:97", "n:80", "n:64", "n:1"])
        por = {g["clave"]: g for g in grupos}
        self.assertEqual(por["n:1"]["fuentes"], ["costos", "packing", "tabla"])
        self.assertEqual(por["n:97"]["fuentes"], ["costos"])
        self.assertEqual(por["n:80"], {"clave": "n:80", "etiqueta": "80 · TGHU6894814",
                                       "numero": 80, "codigos": ["TGHU6894814"],
                                       "valores_costos": [], "shas": [], "fuentes": ["tabla"]})
        self.assertEqual([dict(g) for g in ag.grupos], antes)
        self.assertIs(emb.sumar_tabla(ag.grupos, {}), ag.grupos)

    def test_etiqueta_sin_codigo(self):
        self.assertEqual(emb.etiqueta_tabla(80, None), "80")


class CostosApagado(_SinFlag):
    def test_sql_de_v0574(self):
        with mock.patch.object(costing_read.embarques, "agrupacion",
                               return_value=_agrupacion_prueba()), \
                mock.patch.object(sc.sdb, "fetch_all",
                                  side_effect=AssertionError("leyó la tabla")):
            self.assertEqual(costing_read._filtro_embarque("sin")[0], costing_read._SIN_CONTENEDOR)
            self.assertNotIn("sku_contenedor", costing_read._filtro_embarque("n:1")[0])
            self.assertIsNone(costing_read._filtro_embarque("n:80"))


class Costos(_ConFlag):
    def setUp(self):
        super().setUp()
        for obj, nombre, valor in ((costing_read.embarques, "agrupacion", _agrupacion_prueba()),
                                   (costing_read.sku_contenedor, "numeros", dict(NUMEROS))):
            p = mock.patch.object(obj, nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)

    def _listar(self, embarque, filas=(), por_sku=None):
        with mock.patch.object(costing_read.sdb, "fetch_scalar", return_value=len(filas)) as fs, \
                mock.patch.object(costing_read.sdb, "fetch_all",
                                  side_effect=[list(filas), []]) as fa, \
                mock.patch.object(costing_read.sku_contenedor, "por_sku",
                                  return_value=por_sku or {}):
            rows, total = costing_read.listado(
                1, 40, None, None, "reciente", [], False, None, False, embarque)
        return rows, total, fs, fa

    def test_filtro_suma_la_tabla_como_tercera_fuente(self):
        _, _, fs, _ = self._listar("n:1")
        self.assertIn("(v.contenedor = any(%s::text[]) or p.sku in (select u.sku from "
                      "costing.packing_ubicaciones u where u.ferraforme_sha256 = any(%s::text[])) "
                      "or p.sku in (select sc.sku from costing.sku_contenedor sc "
                      "where sc.numero = %s))", _sql(fs.call_args))
        self.assertEqual(fs.call_args[0][1], (["256059868 - 1"], [SHA_1], 1))
        self.assertNotIn("exists", _sql(fs.call_args))

    def test_n_que_solo_conoce_la_tabla(self):
        _, _, fs, _ = self._listar("n:80")
        self.assertIn("where (p.sku in (select sc.sku from costing.sku_contenedor sc "
                      "where sc.numero = %s))", _sql(fs.call_args))
        self.assertEqual(fs.call_args[0][1], (80,))
        rows, total, fs, fa = self._listar("n:9999")
        self.assertEqual((rows, total), ([], 0))
        fs.assert_not_called()

    def test_grupo_sin_n_en_la_tabla_no_la_consulta(self):
        _, _, fs, _ = self._listar("n:97")
        self.assertNotIn("sku_contenedor", _sql(fs.call_args))

    def test_sin_contenedor_excluye_lo_que_ubica_la_tabla(self):
        _, _, fs, _ = self._listar("sin")
        cuenta = _sql(fs.call_args)
        self.assertIn("coalesce(v.contenedor, '') = '' and not exists (select 1 from "
                      "costing.packing_ubicaciones u where u.sku = p.sku) and not exists "
                      "(select 1 from costing.sku_contenedor sc where sc.sku = p.sku)", cuenta)
        self.assertNotIn(" not in ", cuenta)

    def test_columna_con_la_tabla(self):
        filas = [{"sku": "A-1", "contenedor": "256059868 - 1"},
                 {"sku": "B-2", "contenedor": None},
                 {"sku": "C-3", "contenedor": "BEAU6268641 - 97"},
                 {"sku": "D-4", "contenedor": None},
                 {"sku": "E-5", "contenedor": None}]
        packing = [{"sku": "a-1", "sha": SHA_1}, {"sku": "E-5", "sha": SHA_2}]
        tabla = {"a-1": [_c(1, "TRHU6215242")], "b-2": [_c(80, "TGHU6894814")],
                 "c-3": [_c(64, "149504230930")], "d-4": [_c(200, "NUEV1234567")]}
        with mock.patch.object(costing_read.sdb, "fetch_scalar", return_value=5), \
                mock.patch.object(costing_read.sdb, "fetch_all",
                                  side_effect=[filas, packing]) as fa, \
                mock.patch.object(costing_read.sku_contenedor, "por_sku",
                                  return_value=tabla) as ps:
            rows, _ = costing_read.listado(1, 40, None, None, "reciente", [])
        self.assertEqual(fa.call_count, 2)
        ps.assert_called_once_with(["A-1", "B-2", "C-3", "D-4", "E-5"])
        por = {r["sku"]: r["embarques"] for r in rows}
        self.assertEqual(por["A-1"], [{"clave": "n:1", "etiqueta": "1 · TRHU6215242 · 256059868",
                                       "fuente": "ambos",
                                       "fuentes": ["costos", "packing", "tabla"]}])
        self.assertEqual(por["B-2"], [{"clave": "n:80", "etiqueta": "80 · TGHU6894814",
                                       "fuente": "tabla", "fuentes": ["tabla"]}])
        self.assertEqual(por["C-3"], [
            {"clave": "n:97", "etiqueta": "97 · BEAU6268641", "fuente": "costos",
             "fuentes": ["costos"]},
            {"clave": "n:64", "etiqueta": "64 · 149504230930", "fuente": "tabla",
             "fuentes": ["tabla"]}])
        # Una N que la caché de 30 s todavía no conoce no truena: su etiqueta sale de la fila.
        self.assertEqual(por["D-4"], [{"clave": "n:200", "etiqueta": "200 · NUEV1234567",
                                       "fuente": "tabla", "fuentes": ["tabla"]}])
        # «PL» sigue siendo SOLO el packing.
        self.assertEqual(por["E-5"], [{"clave": "n:64", "etiqueta": "64 · 149504230930",
                                       "fuente": "packing", "fuentes": ["packing"]}])

    def test_conteos_con_la_tabla(self):
        grupos = emb.sumar_tabla(_agrupacion_prueba().grupos, NUMEROS)
        with mock.patch.object(costing_read.sdb, "fetch_all", return_value=[]) as fa:
            costing_read.conteos_embarques(grupos, dict(NUMEROS))
        self.assertEqual(fa.call_count, 1)
        sql = _sql(fa.call_args)
        self.assertIn("g_tabla as ( select * from unnest(%s::text[], %s::int[]) "
                      "as t(clave, numero) )", sql)
        self.assertIn("union select g.clave, sc.sku from g_tabla g join "
                      "costing.sku_contenedor sc on sc.numero = g.numero", sql)
        self.assertEqual(sql.count("exists"), 2)          # los dos anti-join de «sin», con AND
        self.assertNotIn("or exists", sql)
        self.assertNotIn("or not exists", sql)
        self.assertIn("and not exists (select 1 from costing.sku_contenedor sc "
                      "where sc.sku = p.sku)", sql)
        params = fa.call_args[0][1]
        self.assertEqual(len(params), 6)
        self.assertEqual(list(zip(params[4], params[5])), [("n:80", 80), ("n:64", 64), ("n:1", 1)])

    def test_respuesta_de_embarques_con_la_tabla(self):
        filas = [{"clave": "n:1", "n": 578, "sin_costo": 4},
                 {"clave": "n:80", "n": 30, "sin_costo": 2},
                 {"clave": "n:64", "n": 0, "sin_costo": 0},
                 {"clave": "sin", "n": 3000, "sin_costo": 2900}]
        with mock.patch.object(costing_read.sdb, "fetch_all", return_value=filas):
            r = costing_read.embarques_con_conteos()
        costing_read.embarques.agrupacion.assert_called_once_with(forzar=True)
        costing_read.sku_contenedor.numeros.assert_called_once_with(forzar=True)
        self.assertEqual([e["clave"] for e in r["embarques"]], ["n:80", "n:1"])
        self.assertEqual(r["embarques"][0], {
            "clave": "n:80", "etiqueta": "80 · TGHU6894814", "numero": 80,
            "codigos": ["TGHU6894814"], "n": 30, "sin_costo": 2, "fuentes": ["tabla"]})
        self.assertEqual(r["embarques"][1]["fuentes"], ["costos", "packing", "tabla"])

    def test_tabla_caida_es_v0574(self):
        costing_read.sku_contenedor.numeros.return_value = {}
        _, _, fs, _ = self._listar("sin")
        self.assertNotIn("sku_contenedor", _sql(fs.call_args))
        self.assertIsNone(costing_read._filtro_embarque("n:80"))


if __name__ == "__main__":
    unittest.main()
