"""Pruebas del CANDADO DE PROVISIONALES (25-sep-2026).

Eduardo decidió borrar los 6,252 identificadores provisionales del app viejo
(«5070-0020», «0759-0057-PURPLE») de ``core.products`` y
``costing.costos_validados``. Borrar no basta si la app los vuelve a crear; esto
fija que ya no puede.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Un solo predicado.** El análisis, el ubicador de contenedores y el escritor
   usan el mismo patrón; los SKUs reales —incluso uno que empieza con dígito o
   un número suelto— no caen.
2. **La invariante vive abajo.** ``_asegurar_identidad``, ``upsert_validados`` y
   ``upsert_finales`` lanzan ``SkuProvisional`` SIN ejecutar SQL.
3. **Un rechazo no es una caída.** ``costing_write`` no lo manda a MySQL de
   respaldo ni lo encola en ``espejo_kubera_log``; ``kubera_mirror`` no lo
   persiste como error y el reproceso lo cierra en vez de reintentarlo siempre.
   Contraprueba: una caída de verdad SÍ cae a MySQL y SÍ se encola.
4. **``costos.py`` no lo escribe ni con el corte apagado** (MySQL primaria).
5. **La pantalla de Costos**: el lote salta el provisional con su aviso y
   guarda el resto; el recálculo individual responde 422.
6. **Con kubera CAÍDA tampoco**: la guarda de ``costing_write`` corre antes de
   abrir el cursor, así que el provisional no se va por el camino de caída
   (MySQL + cola). La bitácora (``registrar_log``) sí puede nombrarlo.
7. **Las otras puertas de ``core.products``**: el reproceso de identidad
   (``_up_core_product``), el sync de canales y el DROP (``channel_mirror``) y
   el publicador (``publicacion_seam``). El descarte de la cola queda marcado.
8. **El ETL de las 06:15** (``etl_core_products_v2``) no da de alta un
   provisional venga de donde venga (ni por alias de ``id_map``) y solo anota
   issue para los que alguien tiene que corregir en su origen.

Sin red ni base: ``sdb``, ``db`` y el pool de kubera van simulados.
    cd backend && python -m unittest tests.test_candado_provisionales -v
"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import crear as ruta_crear  # noqa: E402
from services import (channel_mirror, costing_mirror, costing_write,  # noqa: E402
                      costos, db, kubera_mirror, packing_comparador,
                      publicacion_seam, sku_provisional, supabase_db as sdb,
                      ubicar_contenedores)
from services.sku_provisional import MENSAJE, SkuProvisional  # noqa: E402

PROVISIONALES = ("5070-0020", "0759-0057-PURPLE", "2791-0015-L",
                 "1330-0083-WHITE-A+A", " 4814-0001 ", "123-45678")
REALES = ("BAÑ-0486-EST", "ROP-AZL-GRICLA-GRIOBS-S",
          "1CALZ-0108-BLN-ROJ-NEG-44", "25", "802G", "MX-7661", "ACC-0710-MET")

FILA_VALIDADOS = {"largo": 10, "alto": 10, "ancho": 10, "peso": 1,
                  "costo_producto": 50, "costo_cbm": 5, "costo_total": 55}
FILA_FINALES = {"costo_producto": 50, "costo_cbm": 5, "costo_unitario": 55,
                "costo_comision": 10, "costo_fee_envio": 20,
                "precio_sugerido": 150, "precio_base": 180,
                "ml_cat_id": "MLM1234", "pct_comision": 0.15,
                "peso_origen": "costos_validados"}


class _Cursor:
    """Cursor que anota cada sentencia. ``inserts`` = lo que de verdad escribe."""

    def __init__(self, rowcount: int = 0) -> None:
        self.sql: list[tuple[str, object]] = []
        self.rowcount = rowcount

    def execute(self, sql, params=None) -> None:
        self.sql.append((sql, params))

    def fetchone(self):
        return None

    @property
    def inserts(self) -> list[str]:
        return [s for s, _ in self.sql if "insert into" in s.lower()]

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _get_cursor_falso(cur: _Cursor):
    @contextlib.contextmanager
    def _gc():
        yield cur
    return _gc


# ─────────────────────────────────────────────────────────────────────────────
# 1. EL PREDICADO
# ─────────────────────────────────────────────────────────────────────────────
class Predicado(unittest.TestCase):
    def test_provisionales(self):
        for sku in PROVISIONALES:
            self.assertTrue(sku_provisional.es_provisional(sku), sku)

    def test_reales_no_caen(self):
        for sku in REALES + ("", None, "  "):
            self.assertFalse(sku_provisional.es_provisional(sku), repr(sku))

    def test_es_el_mismo_en_los_tres_modulos(self):
        # packing_comparador lo re-exporta (packing_publicados y packing_resolver
        # llaman `comp.es_provisional`); el ubicador delega.
        self.assertIs(packing_comparador.es_provisional, sku_provisional.es_provisional)
        self.assertIs(ubicar_contenedores.RE_PROVISIONAL, sku_provisional.RE_PROVISIONAL)
        for sku in PROVISIONALES + REALES:
            self.assertEqual(ubicar_contenedores.es_provisional(sku),
                             sku_provisional.es_provisional(sku), sku)

    def test_la_excepcion_es_value_error_y_trae_el_sku(self):
        exc = SkuProvisional(" 5070-0020 ")
        self.assertIsInstance(exc, ValueError)
        self.assertEqual(exc.sku, "5070-0020")
        self.assertIn(MENSAJE, str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# 2. LA INVARIANTE, EN EL NIVEL MÁS BAJO
# ─────────────────────────────────────────────────────────────────────────────
class EscritoresRechazan(unittest.TestCase):
    ESCRITORES = (
        ("_asegurar_identidad", lambda cur, sku: costing_mirror._asegurar_identidad(cur, sku)),
        ("upsert_validados", lambda cur, sku: costing_mirror.upsert_validados(
            cur, sku, dict(FILA_VALIDADOS))),
        ("upsert_finales", lambda cur, sku: costing_mirror.upsert_finales(
            cur, sku, dict(FILA_FINALES))),
    )

    def test_provisional_no_ejecuta_sql(self):
        for nombre, escribir in self.ESCRITORES:
            for sku in PROVISIONALES:
                with self.subTest(escritor=nombre, sku=sku):
                    cur = _Cursor()
                    with self.assertRaises(SkuProvisional):
                        escribir(cur, sku)
                    self.assertEqual(cur.sql, [])

    def test_sku_real_si_escribe(self):
        for nombre, escribir in self.ESCRITORES:
            for sku in REALES:
                with self.subTest(escritor=nombre, sku=sku):
                    cur = _Cursor()
                    escribir(cur, sku)
                    self.assertEqual(len(cur.inserts), 1)

    def test_espejo_f3_ni_abre_conexion_ni_anota_issue(self):
        gc = mock.MagicMock()
        with self.assertLogs("omnicanal.costing_mirror", "WARNING"), \
                mock.patch.object(costing_mirror, "activo", return_value=True), \
                mock.patch.object(sdb, "get_cursor", gc), \
                mock.patch.object(costing_mirror, "_registrar_issue") as issue:
            costing_mirror.espejar_validados("5070-0020", dict(FILA_VALIDADOS))
            costing_mirror.espejar_finales("0759-0057-PURPLE", dict(FILA_FINALES))
        gc.assert_not_called()
        issue.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 3. EL CORTE F6: UN RECHAZO NO ES KUBERA CAÍDA
# ─────────────────────────────────────────────────────────────────────────────
class CorteNoLoRevive(unittest.TestCase):
    def _guardar(self, funcion, sku, fila, primaria_falla=None):
        cur = _Cursor()
        mysql = mock.MagicMock(name="escribir_mysql")
        gc = _get_cursor_falso(cur)
        if primaria_falla is not None:
            def gc():  # noqa: F811 — kubera "caída" al abrir el cursor
                raise primaria_falla
        with mock.patch.object(sdb, "get_cursor", gc), \
                mock.patch.object(costing_write, "_encolar_kubera") as encolar, \
                mock.patch.object(kubera_mirror, "_persistir_error") as persistir, \
                mock.patch.object(costing_write, "_en_hilo") as en_hilo, \
                mock.patch.object(costing_write.settings, "costing_espejo_inverso", True):
            error = None
            try:
                funcion(sku, dict(fila), mysql)
            except Exception as exc:  # noqa: BLE001
                error = exc
        return error, cur, mysql, encolar, persistir, en_hilo

    def test_provisional_ni_mysql_ni_cola_ni_espejo(self):
        for funcion, fila in ((costing_write.guardar_validados, FILA_VALIDADOS),
                              (costing_write.guardar_finales, FILA_FINALES)):
            with self.subTest(funcion=funcion.__name__), \
                    self.assertLogs("omnicanal.costing_write", "WARNING") as bitacora:
                error, cur, mysql, encolar, persistir, en_hilo = self._guardar(
                    funcion, "5070-0020", fila)
                self.assertIn("identificador provisional", bitacora.output[0])
                self.assertIsInstance(error, SkuProvisional)   # se le avisa a quien llamó
                self.assertEqual(cur.inserts, [])               # kubera no se tocó
                mysql.assert_not_called()                       # sin respaldo MySQL
                encolar.assert_not_called()                     # sin cola
                persistir.assert_not_called()
                en_hilo.assert_not_called()                     # sin espejo inverso

    def test_sku_real_escribe_kubera_y_espeja(self):
        error, cur, mysql, encolar, _p, en_hilo = self._guardar(
            costing_write.guardar_validados, "BAÑ-0486-EST", FILA_VALIDADOS)
        self.assertIsNone(error)
        self.assertEqual(len(cur.inserts), 2)   # identidad + costos_validados
        encolar.assert_not_called()
        en_hilo.assert_called_once()            # espejo inverso, en hilo

    def test_con_kubera_caida_el_provisional_tampoco_cae_a_mysql(self):
        # La caída se simula al ABRIR el cursor, antes de _asegurar_identidad:
        # sin la guarda previa, el provisional se iba por el camino de caída.
        for funcion, fila in ((costing_write.guardar_validados, FILA_VALIDADOS),
                              (costing_write.guardar_finales, FILA_FINALES)):
            with self.subTest(funcion=funcion.__name__), \
                    self.assertLogs("omnicanal.costing_write", "WARNING"):
                error, _c, mysql, encolar, persistir, en_hilo = self._guardar(
                    funcion, "5070-0020", fila,
                    primaria_falla=RuntimeError("kubera caída"))
                self.assertIsInstance(error, SkuProvisional)
                mysql.assert_not_called()
                encolar.assert_not_called()
                persistir.assert_not_called()
                en_hilo.assert_not_called()

    def test_la_bitacora_si_puede_nombrar_un_provisional(self):
        # ops.process_log no le da de alta a nadie en el catálogo.
        cur = _Cursor()
        with mock.patch.object(sdb, "get_cursor", _get_cursor_falso(cur)), \
                mock.patch.object(costing_mirror, "insertar_log") as insertar, \
                mock.patch.object(costing_write, "_encolar_kubera") as encolar, \
                mock.patch.object(costing_write.settings, "costing_espejo_inverso", False):
            costing_write.registrar_log("5070-0020", "descartar", "backend", {},
                                        mock.MagicMock())
        insertar.assert_called_once()
        self.assertEqual(insertar.call_args.args[1], "5070-0020")
        encolar.assert_not_called()

    def test_contraprueba_una_caida_real_si_cae_a_mysql_y_se_encola(self):
        # Sin esto, las aserciones de arriba podrían pasar por un arnés ciego.
        with self.assertLogs("omnicanal.costing_write", "WARNING"):
            error, _c, mysql, encolar, _p, _h = self._guardar(
                costing_write.guardar_finales, "BAÑ-0486-EST", FILA_FINALES,
                primaria_falla=RuntimeError("kubera caída"))
        self.assertIsNone(error)
        mysql.assert_called_once()
        encolar.assert_called_once()


class CostosNoLoEscribe(unittest.TestCase):
    """Con el corte APAGADO, MySQL es la primaria: el candado de costing_mirror
    no alcanza y la guarda tiene que estar en costos.py."""

    def test_guardar_sin_corte_no_toca_mysql(self):
        base = {"costo_producto": 50, "costo_cbm": 5, "costo_unitario": 55,
                "largo": 10, "alto": 10, "ancho": 10, "peso": 1}
        pricing = {k: FILA_FINALES[k] for k in ("costo_comision", "costo_fee_envio",
                                                 "precio_sugerido", "precio_base",
                                                 "pct_comision")}
        with mock.patch.object(costing_write, "activo", return_value=False), \
                mock.patch.object(costos.db, "execute") as ejecutar, \
                mock.patch.object(costing_mirror, "en_hilo") as espejo:
            with self.assertRaises(SkuProvisional):
                costos._guardar_validados("5070-0020", dict(base))
            with self.assertRaises(SkuProvisional):
                costos._guardar_finales("5070-0020", dict(base), dict(pricing), "MLM1")
            ejecutar.assert_not_called()
            espejo.assert_not_called()
            # El SKU real sigue su camino de siempre.
            costos._guardar_validados("1CALZ-0108-BLN-ROJ-NEG-44", dict(base))
            costos._guardar_finales("1CALZ-0108-BLN-ROJ-NEG-44", dict(base),
                                    dict(pricing), "MLM1")
            self.assertEqual(ejecutar.call_count, 2)

    def test_recalcular_rechaza_antes_de_calcular(self):
        with mock.patch.object(costos, "computar") as computar:
            with self.assertRaises(SkuProvisional):
                costos.recalcular("0759-0057-PURPLE")
        computar.assert_not_called()

    def test_asegurar_finales_da_none_sin_leer(self):
        with self.assertLogs("uvicorn.error", "WARNING"), \
                mock.patch.object(costos, "costo_desde_validados") as leer, \
                mock.patch.object(costos.costing_read, "finales") as finales:
            self.assertIsNone(costos.asegurar_finales("5070-0020", "MLM1"))
        leer.assert_not_called()
        finales.assert_not_called()


class ColaNoLoGuarda(unittest.TestCase):
    def test_persistir_error_no_encola_ni_alerta(self):
        with self.assertLogs("omnicanal.kubera_mirror", "WARNING"), \
                mock.patch.object(kubera_mirror, "_asegurar_tabla_log") as tabla, \
                mock.patch.object(db, "execute") as ejecutar, \
                mock.patch("services.alertas.avisar") as avisar:
            kubera_mirror._persistir_error(
                "services/costos.py", "guardar_validados", "costos_validados",
                "costing.costos_validados", "UPSERT", "5070-0020",
                SkuProvisional("5070-0020"), {"sku": "5070-0020"})
        tabla.assert_not_called()
        ejecutar.assert_not_called()
        avisar.assert_not_called()

    def test_reproceso_cierra_el_provisional_y_aplica_el_real(self):
        import json
        cursores: list[_Cursor] = []

        class _Conn:
            def cursor(self):
                cursores.append(_Cursor())
                return cursores[-1]

            def commit(self):
                pass

            def close(self):
                pass

        pool = mock.MagicMock()
        pool.connection.side_effect = lambda: _Conn()
        filas = [
            {"id": 1, "tabla_destino": "costing.costos_validados",
             "payload_json": json.dumps({**FILA_VALIDADOS, "sku": "5070-0020",
                                         "accion": "auto", "origen": "backend"})},
            {"id": 2, "tabla_destino": "costing.costos_finales",
             "payload_json": json.dumps({**FILA_FINALES, "sku": "ROP-AZL-GRICLA-GRIOBS-S",
                                         "accion": "auto", "origen": "backend"})},
            {"id": 3, "tabla_destino": "costing.costos_finales",
             "payload_json": json.dumps({**FILA_FINALES, "sku": "0759-0057-PURPLE"})},
        ]
        with mock.patch.object(kubera_mirror, "disponible", return_value=True), \
                mock.patch.object(kubera_mirror, "_asegurar_tabla_log", return_value=True), \
                mock.patch.object(kubera_mirror, "_get_pool", return_value=pool), \
                mock.patch.object(db, "fetch_all", return_value=filas), \
                mock.patch.object(db, "execute") as ejecutar, \
                self.assertLogs("omnicanal.kubera_mirror", "WARNING") as bitacora:
            r = kubera_mirror.reprocesar_errores()
        self.assertEqual(len(bitacora.output), 2)   # uno por provisional

        self.assertEqual((r["aplicados"], r["fallidos"], r["descartados_provisionales"]),
                         (1, 0, 2))
        self.assertEqual(r["skus_provisionales"], ["5070-0020", "0759-0057-PURPLE"])
        # Los tres eventos quedan cerrados: ninguno se reintenta para siempre.
        cerrados = {c.args[1][-1]: c for c in ejecutar.call_args_list
                    if "resuelto=1" in c.args[0]}
        self.assertEqual(sorted(cerrados), [1, 2, 3])
        # ...pero un descarte NO se ve igual que uno aplicado: lleva la nota.
        for id_ in (1, 3):
            self.assertIn("error_texto", cerrados[id_].args[0])
            self.assertEqual(cerrados[id_].args[1],
                             (kubera_mirror._NOTA_DESCARTE_PROVISIONAL, id_))
        self.assertNotIn("error_texto", cerrados[2].args[0])
        # Y solo el real llegó a escribir.
        escritos = [params for cur in cursores for s, params in cur.sql
                    if "insert into" in s.lower()]
        skus = {(p["sku"] if isinstance(p, dict) else p[0]) for p in escritos}
        self.assertEqual(skus, {"ROP-AZL-GRICLA-GRIOBS-S"})


class IdentidadNoLoRevive(unittest.TestCase):
    """``kubera_mirror._up_core_product``: la identidad del panel y su reproceso."""

    def test_provisional_sin_wc_id_no_ejecuta_sql(self):
        cur = _Cursor()
        with self.assertRaises(SkuProvisional):
            kubera_mirror._up_core_product(cur, {"sku": "5070-0020", "name": "x"})
        self.assertEqual(cur.sql, [])

    def test_provisional_con_acta_por_wc_id_solo_actualiza(self):
        # El UPDATE por wc_id no da de alta a nadie: se deja pasar.
        cur = _Cursor(rowcount=1)
        kubera_mirror._up_core_product(cur, {"sku": "5070-0020", "wc_id": 77,
                                             "name": "x"})
        self.assertEqual(len(cur.sql), 1)
        self.assertEqual(cur.inserts, [])

    def test_sku_real_si_nace(self):
        cur = _Cursor()
        kubera_mirror._up_core_product(cur, {"sku": "ACC-0710-MET", "name": "x"})
        self.assertEqual(len(cur.inserts), 1)


class CanalNoLoRevive(unittest.TestCase):
    """El sync de canales, el DROP y el publicador también daban de alta."""

    def setUp(self):
        channel_mirror._provisionales_avisados.clear()

    def test_sync_salta_el_provisional_y_lo_anota_una_sola_vez(self):
        filas = [{"sku": "5070-0020", "canal": "mercado_libre", "cuenta": "BEKURA"},
                 {"sku": "BAÑ-0486-EST", "canal": "mercado_libre", "cuenta": "BEKURA"}]
        cur = _Cursor()
        with mock.patch.object(channel_mirror, "_cuenta_uuid", return_value="uuid-1"), \
                mock.patch.object(channel_mirror, "_registrar_issue") as issue, \
                self.assertLogs("omnicanal.channel_mirror", "WARNING"):
            channel_mirror.escribir_tanda(cur, filas)
            channel_mirror.escribir_tanda(_Cursor(), filas)   # la pasada siguiente
        self.assertEqual({p[0] for s, p in cur.sql if "insert into" in s.lower()},
                         {"BAÑ-0486-EST"})
        issue.assert_called_once()
        self.assertEqual(issue.call_args.args[0], "5070-0020")

    def test_drop_salta_el_provisional(self):
        crudas = [{"sku": "0759-0057-PURPLE", "stock_woo": 3},
                  {"sku": "ACC-0710-MET", "stock_woo": 5}]
        lotes: list[tuple[str, list]] = []
        with mock.patch.object(channel_mirror, "activo", return_value=True), \
                mock.patch.object(channel_mirror, "_cuenta_uuid", return_value="uuid-g"), \
                mock.patch.object(channel_mirror, "_registrar_issue") as issue, \
                mock.patch("services.stock_watch.kubera_decide", return_value=True), \
                mock.patch("services.stock_watch_read.drop_leer", return_value=crudas), \
                mock.patch.object(sdb, "get_cursor", _get_cursor_falso(_Cursor())), \
                mock.patch("psycopg2.extras.execute_values",
                           side_effect=lambda _c, sql, filas, *a, **k:
                           lotes.append((sql, list(filas)))), \
                self.assertLogs("omnicanal.channel_mirror", "WARNING"):
            r = channel_mirror.sincronizar_drop()
        self.assertTrue(r["ok"], r)
        self.assertEqual({f[0] for _s, filas in lotes for f in filas}, {"ACC-0710-MET"})
        issue.assert_called_once()

    def test_publicador_no_registra_y_lo_deja_visible(self):
        gc = mock.MagicMock()
        with mock.patch.object(publicacion_seam, "activo", return_value=True), \
                mock.patch.object(publicacion_seam.actor, "en_hilo",
                                  side_effect=lambda fn, *a: fn(*a)), \
                mock.patch.object(channel_mirror, "_cuenta_uuid") as cuenta, \
                mock.patch.object(channel_mirror, "_registrar_issue") as issue, \
                mock.patch.object(sdb, "get_cursor", gc), \
                self.assertLogs("omnicanal.publicacion_seam", "WARNING"):
            publicacion_seam.registrar("mercado_libre", "BEKURA", "5070-0020",
                                       listing_id="MLM1")
        cuenta.assert_not_called()
        gc.assert_not_called()
        issue.assert_called_once()
        self.assertIn(MENSAJE, issue.call_args.args[1])


# ─────────────────────────────────────────────────────────────────────────────
# 5. EL ETL DE LAS 06:15
# ─────────────────────────────────────────────────────────────────────────────
_ETL = Path(__file__).resolve().parent.parent / "scripts" / "etl_core_products_v2.py"
# Credenciales FALSAS a propósito: si algún simulacro fallara, la conexión va a
# un host que no existe y no a producción.
_DSN_FALSO = "postgresql://postgres.tukwcvsiprueba:x@host-inexistente.invalid:6543/db"
_PROD_FALSO = {"DB_HOST": "host-inexistente.invalid", "WPDB_USER": "u",
               "WPDB_PASSWORD": "p", "WPDB_NAME": "n",
               "ODOO_URL": "http://host-inexistente.invalid", "ODOO_DB": "d",
               "ODOO_USER": "u", "ODOO_PASSWORD": "p"}


def _cargar_etl():
    """Importa el script SIN leer .env ni env.staging (traen credenciales de
    producción) y sin dejarle al proceso de pruebas su timeout global."""
    import importlib.util
    import socket
    exists = Path.exists

    def _exists(self, *a, **k):
        return False if self.name in (".env", "env.staging") else exists(self, *a, **k)

    antes = socket.getdefaulttimeout()
    spec = importlib.util.spec_from_file_location("etl_core_v2_prueba", _ETL)
    mod = importlib.util.module_from_spec(spec)
    try:
        with mock.patch.object(Path, "exists", _exists):
            spec.loader.exec_module(mod)
    finally:
        socket.setdefaulttimeout(antes)
    return mod


class _PgCur:
    """Contesta cada SELECT por un pedazo de su texto; anota lo que escribe."""

    def __init__(self, respuestas, escrito):
        self.respuestas, self.escrito, self._filas = respuestas, escrito, []

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        if s.startswith("insert"):
            self.escrito.append((s, params))
            return
        for pedazo, filas in self.respuestas:
            if pedazo in s:
                self._filas = list(filas)
                return
        raise AssertionError(f"consulta no prevista: {s[:90]}")

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return self._filas[0] if self._filas else None

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _PgConn:
    autocommit = False

    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def close(self):
        pass


class EtlCoreNoLoRevive(unittest.TestCase):
    """``incorporar()`` y el lazo de ``marketplace_only``: las dos altas del ETL."""

    def test_ningun_provisional_nace_y_solo_se_anota_lo_corregible(self):
        etl = _cargar_etl()
        escrito: list = []
        lotes: list[tuple[str, list]] = []
        fuentes_kubera = _PgCur([
            ("from costing.costos_validados", [("5070-0020", None),
                                               ("ROP-AZL-GRICLA-GRIOBS-S", None)]),
            ("from channel.product_category", [("2791-0015-L",)]),
            ("from costing.costos_finales", [("1330-0083-WHITE-A+A",)]),
        ], escrito)
        destino = _PgCur([
            ("count(*)", [(3,)]),
            # Antes del borrado el provisional sigue en el maestro: no se toca.
            ("from core.products", [("5070-0020", None, None, None, None,
                                      "packing_list_only", False, "costos_validados")]),
            # Un alias curado que manda un SKU de Woo a un provisional.
            ("from migration.id_map", [("ACC-VIEJO", "0759-0057-PURPLE")]),
            ("from channel.listings", [("0031-0001",), ("0032-0002",), ("MX-7661",)]),
            # Ya anotado otro día: no se repite.
            ("from ops.migration_issues", [("0031-0001", "provisional")]),
        ], escrito)
        conexiones = iter([_PgConn(fuentes_kubera), _PgConn(destino)])

        wcur = mock.MagicMock()
        wcur.fetchall.return_value = [
            {"wc_id": 10, "post_status": "publish", "post_title": "Tapete de baño",
             "post_parent": 0, "sku": "BAÑ-0486-EST"},
            {"wc_id": 11, "post_status": "publish", "post_title": "Algo viejo",
             "post_parent": 0, "sku": "ACC-VIEJO"},
        ]
        wp = mock.MagicMock()
        wp.cursor.return_value = wcur

        odoo = mock.MagicMock()
        odoo.authenticate.return_value = 1
        odoo.execute_kw.side_effect = [[
            {"id": 1, "default_code": "4814-0001", "name": "tecleado a mano"},
            {"id": 2, "default_code": "5070 - 0021", "name": "con espacios"},
            {"id": 3, "default_code": "1CALZ-0108-BLN-ROJ-NEG-44", "name": "Tenis"},
        ], []]

        with mock.patch.object(etl, "DEST", {"SUPABASE_DB_URL": _DSN_FALSO}), \
                mock.patch.object(etl, "PROD", dict(_PROD_FALSO)), \
                mock.patch.object(etl, "_armar_watchdog"), \
                mock.patch.object(etl.psycopg2, "connect",
                                  side_effect=lambda *a, **k: next(conexiones)), \
                mock.patch.object(etl.pymysql, "connect", return_value=wp), \
                mock.patch.object(etl.xmlrpc.client, "ServerProxy", return_value=odoo), \
                mock.patch.object(etl.psycopg2.extras, "execute_values",
                                  side_effect=lambda _c, sql, filas, **k:
                                  lotes.append((" ".join(sql.split()).lower(),
                                                list(filas)))), \
                mock.patch.object(sys, "argv", ["etl", "--real",
                                                "--acepto-destino", "tukwcvsi"]), \
                contextlib.redirect_stdout(io.StringIO()):
            etl.main()

        def _lote(tabla):
            return [f for sql, filas in lotes if f"insert into {tabla}" in sql
                    for f in filas]

        # 1) El maestro: entran los reales —la contraprueba de que el arnés ve
        #    las altas— y ningún provisional, ni el que llega por alias.
        altas = {f[0] for f in _lote("core.products")}
        self.assertEqual(altas, {"BAÑ-0486-EST", "ROP-AZL-GRICLA-GRIOBS-S",
                                 "1CALZ-0108-BLN-ROJ-NEG-44", "MX-7661"})
        # 2) Tampoco se aprende un alias hacia un provisional.
        for original, canonico, _fase in _lote("migration.id_map"):
            self.assertFalse(sku_provisional.es_provisional(canonico), original)
        # 3) Issues: solo lo que alguien corrige en su origen, sin repetir.
        anotados = {(t, s) for (_f, t, s, m, _v) in _lote("ops.migration_issues")
                    if m == "provisional"}
        self.assertEqual(anotados, {("woocommerce", "ACC-VIEJO"),
                                    ("odoo", "4814-0001"), ("odoo", "5070 - 0021"),
                                    ("channel.listings", "0032-0002")})
        # 4) El acta: los provisionales NO rompen la racha; se cuentan aparte.
        import json
        acta = [p for s, p in escrito if "reconciliation_runs" in s]
        self.assertEqual(len(acta), 1)
        conteos = json.loads(acta[0][1])
        self.assertEqual(conteos["seam_gap"], 4)
        self.assertEqual(conteos["provisionales_omitidos"], 8)
        self.assertFalse(any(sku_provisional.es_provisional(x["sku"])
                             for x in conteos["seam_gap_skus"]))


# ─────────────────────────────────────────────────────────────────────────────
# 6. LA PANTALLA DE COSTOS
# ─────────────────────────────────────────────────────────────────────────────
class PantallaCostos(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(ruta_crear.router)
        self.c = TestClient(app)

    @staticmethod
    def _recalcular_falso(sku, *_a, **_k):
        return {"sku": sku, "costo_unitario": 55.0, "precio_base": 180.0,
                "precio_sugerido": 150.0, "costo_cbm": 5.0}

    def test_bulk_salta_el_provisional_y_guarda_el_resto(self):
        items = ["5070-0020", "BAÑ-0486-EST", "0759-0057-PURPLE",
                 "1CALZ-0108-BLN-ROJ-NEG-44", "25"]
        with mock.patch.object(ruta_crear.costos, "recalcular",
                               side_effect=self._recalcular_falso) as rec:
            r = self.c.post("/api/crear/costos/bulk", json={
                "items": [{"sku": s, "costo_producto": 50} for s in items],
                "sincronizar_woo": False})
        self.assertEqual(r.status_code, 200)
        cuerpo = r.json()
        self.assertEqual((cuerpo["total"], cuerpo["exitosos"]), (5, 3))
        por_sku = {x["sku"]: x for x in cuerpo["resultados"]}
        for sku in ("5070-0020", "0759-0057-PURPLE"):
            self.assertFalse(por_sku[sku]["ok"])
            self.assertTrue(por_sku[sku]["provisional"])
            self.assertEqual(por_sku[sku]["error"], MENSAJE)
        for sku in ("BAÑ-0486-EST", "1CALZ-0108-BLN-ROJ-NEG-44", "25"):
            self.assertTrue(por_sku[sku]["ok"], sku)
        # El orden del lote se respeta y el provisional nunca llega al motor.
        self.assertEqual([x["sku"] for x in cuerpo["resultados"]], items)
        self.assertEqual([c.args[0] for c in rec.call_args_list],
                         ["BAÑ-0486-EST", "1CALZ-0108-BLN-ROJ-NEG-44", "25"])

    def test_recalcular_individual_responde_422(self):
        with mock.patch.object(ruta_crear.costos, "recalcular") as rec:
            r = self.c.post("/api/crear/costos/5070-0020/recalcular",
                            json={"sincronizar_woo": False})
        self.assertEqual(r.status_code, 422)
        self.assertIn(MENSAJE, r.json()["detail"])
        rec.assert_not_called()

    def test_recalcular_individual_sku_real_pasa(self):
        with mock.patch.object(ruta_crear.costos, "recalcular",
                               side_effect=self._recalcular_falso) as rec:
            r = self.c.post(f"/api/crear/costos/{quote('BAÑ-0486-EST')}/recalcular",
                            json={"sincronizar_woo": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(rec.call_args.args[0], "BAÑ-0486-EST")


if __name__ == "__main__":
    unittest.main()
