"""Pruebas de la alineación EN SECO de T0 (`scripts/alinear_seco_t0.py`).

── POR QUÉ ────────────────────────────────────────────────────────────────────
En T0 el fan-out pasa a repartir el vendible del libro de kubera. El script dice,
antes de voltear la bandera, qué le escribiría a cada publicación DROP y cuánto
pesa la primera pasada de `stock_watch` contra su tope. Si clasifica mal, el acta
de T0 se firma con números equivocados; si escribe o envenena el pooler, rompe
producción mientras «sólo lee».

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
 1. LA CLASIFICACIÓN: igual / baja / sube / sin_numero_en_woo / a_cero_desconocido
    / canal_desconocido / sin_plan, con su objetivo, sentido y delta; viva o
    latente y si el seguro la apagaría. Con D4 el 0 de lo que el libro no trae lo
    da `plan()`, no el script. (`Clasificar`)
 2. LOS APROBADOS: SKU en mayúsculas, `objetivo` ignorado (ya no fija nada), sin
    columna `sku` → código 3; se VERIFICA que cada uno esté en el libro y se listan
    los que irían a 0. (`Aprobados`, `Medir.test_aprobados_se_verifican`)
 3. EL CANDADO DE ENTORNO: sandbox nunca con el DSN de producción, prod nunca con
    otro, y sin `SUPABASE_PROD_REF` no corre; los mensajes no llevan el DSN.
    (`Candado`)
 4. SÓLO LECTURA: UNA transacción `set transaction read only`, savepoint por
    consulta, ROLLBACK al final, nunca COMMIT ni sesión read-only; escrituras,
    MySQL y WordPress revientan mientras dura. (`SoloLectura`)
 5. LA MEDICIÓN usa `plan()` con `FANOUT_FUENTE=kubera`, deja fuera lo que
    `_destinos` descarta, nunca llama a Amazon en vivo, cuenta la primera pasada
    con `_movidos_libro` MÁS los ceros de D4 que esa función no ve (sin contar dos
    veces lo que sale del libro), separa vivas de latentes en lo que va a 0 y mide
    Amazon/Walmart aparte. (`Medir`, `CerosD4`)
 6. EL FLUJO DE `main`: libro ilegible → 2 y la conexión se cierra con ROLLBACK;
    candado → 3 sin conectar; el DSN por stdin. (`Main`)

Sin red ni base: el libro, la foto, las publicaciones y la conexión son dobles.
Los SKUs y los refs son INVENTADOS (el repo es público).

    cd backend && python -m unittest tests.test_alinear_seco_t0 -v
"""
from __future__ import annotations

import ast
import contextlib
import csv
import importlib.util
import io
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from config import settings  # noqa: E402
from services import channel_read, db, fanout_stock, stock_libro, stock_watch_read, wp_db  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

_RUTA = BACKEND / "scripts" / "alinear_seco_t0.py"
_spec = importlib.util.spec_from_file_location("alinear_seco_t0", _RUTA)
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

PROD = "prodprodprodprodprod"       # refs inventados
SANDBOX = "sandsandsandsandsand"
DSN_PROD = f"postgresql://postgres.{PROD}:secreto@pooler.ejemplo:6543/postgres"
DSN_SANDBOX = f"postgresql://postgres.{SANDBOX}:secreto@pooler.ejemplo:6543/postgres"


# ═════════════════════════════════════════════════════════════════════════════
# 1. Clasificación
# ═════════════════════════════════════════════════════════════════════════════

class Clasificar(unittest.TestCase):
    def c(self, libro, obj, woo, actual):
        return A.clasificar(libro, obj, woo, actual)

    def test_con_libro_y_woo(self):
        self.assertEqual(self.c(10, 10, 10, 10)["categoria"], "igual")
        r = self.c(10, 9, 10, 12)
        self.assertEqual((r["categoria"], r["objetivo"], r["delta"], r["cambia"]),
                         ("baja", 9, -3, True))
        r = self.c(10, 10, 3, 4)
        self.assertEqual((r["categoria"], r["delta"]), ("sube", 6))

    def test_sin_numero_en_woo_dice_su_sentido(self):
        r = self.c(5, 5, None, 0)
        self.assertEqual((r["categoria"], r["sentido"], r["delta"]), ("sin_numero_en_woo", "sube", 5))
        r = self.c(5, 5, None, 999)
        self.assertEqual((r["categoria"], r["sentido"], r["delta"]), ("sin_numero_en_woo", "baja", -994))

    def test_desconocido_toma_el_cero_del_plan(self):
        # D4: el 0 lo da `plan()` (numero() = 0 para lo que el libro no trae); el script
        # sólo nombra la categoría, no fija el objetivo.
        r = self.c(None, 0, 7, 7)
        self.assertEqual((r["categoria"], r["objetivo"], r["delta"], r["cambia"]),
                         ("a_cero_desconocido", 0, -7, True))
        r = self.c(None, 0, None, 0)
        self.assertEqual((r["categoria"], r["sentido"], r["cambia"]), ("a_cero_desconocido", "igual", False))

    def test_sin_plan_no_se_toca(self):
        r = self.c(None, None, 7, 7)
        self.assertEqual((r["categoria"], r["sentido"], r["cambia"]), ("sin_plan", "no_se_toca", False))

    def test_canal_desconocido_no_cambia(self):
        r = self.c(4, 4, 4, None)
        self.assertEqual((r["categoria"], r["sentido"], r["cambia"]),
                         ("canal_desconocido", "desconocido", False))
        r = self.c(None, 0, 4, None)
        self.assertEqual((r["categoria"], r["cambia"]), ("a_cero_desconocido", False))

    def test_viva_o_latente(self):
        self.assertEqual(A.vida("mercado_libre", "active", None), "viva")
        self.assertEqual(A.vida("mercado_libre", "paused", None), "latente")
        self.assertEqual(A.vida("tiktok", "APPROVED", "ACTIVATE"), "viva")
        self.assertEqual(A.vida("tiktok", None, "SELLER_DEACTIVATED"), "latente")
        self.assertEqual(A.vida("temu", None, "2/8"), "viva")
        self.assertEqual(A.vida("temu", None, "3/1"), "latente")

    def test_seguro_sólo_con_cero_en_temu_y_tiktok(self):
        with mock.patch.object(settings, "fanout_cero_temu_estados", "2/8,3/1", create=True):
            self.assertTrue(A.seguro_apagaria("tiktok", "ACTIVATE", 0))
            self.assertTrue(A.seguro_apagaria("temu", "3/1", 0))       # agotada: latente y apagable
            self.assertFalse(A.seguro_apagaria("temu", "3/2", 0))      # ya fuera de la venta
            self.assertFalse(A.seguro_apagaria("tiktok", "ACTIVATE", 2))
            self.assertFalse(A.seguro_apagaria("mercado_libre", "active", 0))


class Aprobados(unittest.TestCase):
    def test_lee_e_ignora_objetivo(self):
        filas = csv.DictReader(io.StringIO("sku,objetivo,motivo\nabc-1,3,recodificado\nDEF-2,muchos,\n,9,\n"))
        self.assertEqual(A.leer_aprobados(filas), {"ABC-1": "recodificado", "DEF-2": "aprobado"})

    def test_solo_sku(self):
        self.assertEqual(A.leer_aprobados(csv.DictReader(io.StringIO("SKU\nx-1\n"))), {"X-1": "aprobado"})

    def test_sin_columna_sku(self):
        with self.assertRaises(A.Abortar) as cm:
            A.leer_aprobados(csv.DictReader(io.StringIO("codigo,motivo\nX-1,a\n")))
        self.assertEqual(cm.exception.codigo, 3)


# ═════════════════════════════════════════════════════════════════════════════
# 3. Candado de entorno
# ═════════════════════════════════════════════════════════════════════════════

class Candado(unittest.TestCase):
    def test_ref_del_dsn(self):
        self.assertEqual(A.ref_de_dsn(DSN_PROD), PROD)
        self.assertEqual(A.ref_de_dsn(f"postgresql://postgres:x@db.{SANDBOX}.supabase.co:5432/postgres"),
                         SANDBOX)
        self.assertIsNone(A.ref_de_dsn("postgresql://yo:x@localhost/db"))

    def test_sandbox_nunca_con_produccion(self):
        self.assertEqual(A.candado("sandbox", DSN_SANDBOX, PROD), SANDBOX)
        with self.assertRaises(A.Abortar) as cm:
            A.candado("sandbox", DSN_PROD, PROD)
        self.assertEqual(cm.exception.codigo, 3)
        self.assertIn("PRODUCCIÓN", str(cm.exception))

    def test_prod_nunca_con_otro(self):
        self.assertEqual(A.candado("prod", DSN_PROD, PROD), PROD)
        with self.assertRaises(A.Abortar):
            A.candado("prod", DSN_SANDBOX, PROD)

    def test_falla_cerrada(self):
        for dsn, ref in (("", PROD), ("postgresql://yo:x@localhost/db", PROD), (DSN_SANDBOX, "")):
            with self.assertRaises(A.Abortar) as cm:
                A.candado("sandbox", dsn, ref)
            self.assertEqual(cm.exception.codigo, 3)
            self.assertNotIn("secreto", str(cm.exception))

    def test_sin_secretos(self):
        t = A.sin_secretos(f"fallo con {DSN_PROD} y password=otro123 host=x")
        self.assertNotIn("secreto", t)
        self.assertNotIn("otro123", t)
        self.assertIn("<dsn>", t)

    def test_dsn_por_stdin(self):
        kv = f"OTRA=1\nSUPABASE_DB_URL={DSN_PROD}\nSUPABASE_PROD_REF={PROD}\n"
        self.assertEqual(A.de_stdin(kv), (DSN_PROD, PROD))
        self.assertEqual(A.de_stdin(DSN_SANDBOX + "\n"), (DSN_SANDBOX, ""))


# ═════════════════════════════════════════════════════════════════════════════
# 4. Sólo lectura
# ═════════════════════════════════════════════════════════════════════════════

class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self._filas = []

    def execute(self, sql, params=None):
        self.conn.sentencias.append(sql.strip().split()[0].lower() if "savepoint" not in sql
                                    else sql.strip())
        if "revienta" in sql:
            raise RuntimeError("error de la base")
        self._filas = self.conn.respuestas.get(sql.strip(), [])

    def fetchall(self):
        return list(self._filas)


class _Conn:
    def __init__(self, respuestas=None):
        self.sentencias: list[str] = []
        self.respuestas = respuestas or {}
        self.cerrada = self.commit_llamado = False
        self.rollbacks = 0

    def cursor(self):
        return _Cursor(self)

    def rollback(self):
        self.rollbacks += 1

    def commit(self):  # pragma: no cover — si se llama, la prueba lo detecta
        self.commit_llamado = True

    def close(self):
        self.cerrada = True


class SoloLectura(unittest.TestCase):
    def test_una_transaccion_read_only_con_savepoints(self):
        conn = _Conn({"select 1 as x": [{"x": 1}]})
        lec = A.SoloLectura(conn)
        self.assertEqual(lec.fetch_all("select 1 as x"), [{"x": 1}])
        self.assertEqual(lec.fetch_one("select 1 as x"), {"x": 1})
        self.assertEqual(lec.fetch_scalar("select 1 as x"), 1)
        with self.assertRaises(RuntimeError):
            lec.fetch_all("select revienta")
        self.assertEqual(lec.fetch_all("select 1 as x"), [{"x": 1}])   # la transacción sigue viva
        lec.cerrar()
        self.assertEqual(conn.sentencias[0], "set")
        self.assertIn("rollback to savepoint alinear_seco", conn.sentencias)
        self.assertEqual(conn.sentencias.count("savepoint alinear_seco"), 5)
        self.assertEqual(conn.rollbacks, 1)
        self.assertTrue(conn.cerrada)
        self.assertFalse(conn.commit_llamado)

    def test_desviar_corta_escrituras_mysql_y_wordpress(self):
        lec = A.SoloLectura(_Conn({"select 2 as y": [{"y": 2}]}))
        with A.desviar(lec):
            self.assertEqual(sdb.fetch_all("select 2 as y"), [{"y": 2}])
            for llamada in (lambda: sdb.execute("update x set y=1"),
                            lambda: sdb.execute_returning("insert into x values (1)"),
                            lambda: db.fetch_all("select 1"),
                            lambda: wp_db._fetch_all("select 1")):
                with self.assertRaises(RuntimeError) as cm:
                    llamada()
                self.assertIn("SÓLO LECTURA", str(cm.exception))
        self.assertIsNot(sdb.fetch_all, lec.fetch_all)      # al salir, todo vuelve

    def test_el_script_nunca_hace_commit_ni_sesion_read_only(self):
        arbol = ast.parse(_RUTA.read_text(encoding="utf-8"))
        llamadas = {n.func.attr for n in ast.walk(arbol)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertNotIn("commit", llamadas)
        self.assertNotIn("set_session", llamadas)
        self.assertNotIn("SESSION CHARACTERISTICS", _RUTA.read_text(encoding="utf-8").upper())


# ═════════════════════════════════════════════════════════════════════════════
# 5. La medición
# ═════════════════════════════════════════════════════════════════════════════

ACT = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

# El libro: libre y pendientes. ZZZ-9 no tiene publicación; VEN-0 vendió sin OV.
LIBRO = {
    "AAA-1": {"libre": 10, "pendientes": 0},
    "BBB-2": {"libre": 6, "pendientes": 1},
    "ZZZ-9": {"libre": 3, "pendientes": 0},
    "VEN-0": {"libre": None, "pendientes": 2},
    "AMZ-5": {"libre": 4, "pendientes": 0},
}
# La foto de Woo (`stock_kubera` vacío: primera pasada tras T0).
FOTO = {
    "AAA-1": {"woo": 10, "odoo": None, "kubera": None, "act": ACT},
    "BBB-2": {"woo": None, "odoo": None, "kubera": None, "act": ACT},
    "CCC-3": {"woo": 7, "odoo": None, "kubera": None, "act": ACT},
    "ZZZ-9": {"woo": 1, "odoo": None, "kubera": None, "act": ACT},
}


def _fila(sku, canal, cuenta, item, stock, situacion=None, estado=None, full=0):
    return {"sku": sku, "canal": canal, "cuenta": cuenta, "item_id": item, "stock_real": stock,
            "situacion": situacion, "estado_canal": estado, "es_full": full,
            "stock_full": 0, "stock_fba": 0}


INVENTARIO = {
    "AAA-1": {"mercado_libre|BEKURA": _fila("AAA-1", "mercado_libre", "BEKURA", "MLM1", 12, "active"),
              "mercado_libre|SANCORFASHION": _fila("AAA-1", "mercado_libre", "SANCORFASHION", "MLM2",
                                                   10, "paused"),
              "mercado_libre|BEKURA2": _fila("AAA-1", "mercado_libre", "BEKURA", "MLM3", 0, "active",
                                             full=1),
              "amazon|": _fila("AAA-1", "amazon", "", None, 3, "published")},
    "BBB-2": {"tiktok|KUBERA": _fila("BBB-2", "tiktok", "KUBERA", "TK1", 0, "NONE", "DRAFT"),
              "temu|TEMU": _fila("BBB-2", "temu", "TEMU", "T1", 2, None, "5/None")},
    "CCC-3": {"mercado_libre|BEKURA": _fila("CCC-3", "mercado_libre", "BEKURA", "MLM4", 7, "active")},
    "DDD-4": {"mercado_libre|BEKURA": _fila("DDD-4", "mercado_libre", "BEKURA", "MLM5", 9, "paused"),
              "tiktok|KUBERA": _fila("DDD-4", "tiktok", "KUBERA", "TK2", None, "NONE", "ACTIVATE"),
              "temu|TEMU": _fila("DDD-4", "temu", "TEMU", "T2", 5, None, "3/2")},
    # Ni el libro ni Woo lo conocen; Temu «agotada» (3/1): latente, y el seguro la apagaría.
    "EEE-5": {"temu|TEMU": _fila("EEE-5", "temu", "TEMU", "T3", 3, None, "3/1")},
}
D2 = [{"sku": "AMZ-5", "canal": "amazon", "cuenta": "AMAZON", "item_id": None, "stock_own": 2,
       "situacion": "BUYABLE", "estado_canal": "PUBLISHED"},
      {"sku": "WAL-6", "canal": "walmart", "cuenta": "WALMART", "item_id": "W1", "stock_own": None,
       "situacion": None, "estado_canal": "PUBLISHED"}]


class Medir(unittest.TestCase):
    def setUp(self) -> None:
        self.leer_inv = mock.Mock(side_effect=lambda skus: {s: INVENTARIO[s] for s in skus
                                                          if s in INVENTARIO})
        self.desglose = mock.Mock(side_effect=lambda skus=None: {
            s: d for s, d in LIBRO.items() if skus is None or s in {x.upper() for x in skus}})

        def fetch_all(sql, params=None):
            if "distinct upper(sku::text)" in sql:
                self.assertEqual(params["c"], list(A.CANALES_FANOUT))
                return [{"sku": s} for s in INVENTARIO]
            if "l.canal = any(%(c)s) and not coalesce(l.is_fulfillment" in sql:
                self.assertEqual(params["c"], list(A.CANALES_D2))
                return [dict(r) for r in D2]
            raise AssertionError(f"consulta inesperada: {sql[:60]}")

        self.pila = contextlib.ExitStack()
        for obj, nombre, valor in (
                (stock_libro, "desglose", self.desglose),
                (stock_watch_read, "foto_leer", mock.Mock(return_value=FOTO)),
                (channel_read, "leer_inventario", self.leer_inv),
                (sdb, "fetch_all", fetch_all),
                (wp_db, "_fetch_all", mock.Mock(side_effect=AssertionError("WordPress"))),
                (settings, "fanout_enabled", False),
                (settings, "fanout_fuente", "woo"),
                (settings, "fanout_reserva", 0),
                (settings, "fanout_canales", ""),
                (settings, "fanout_tiktok", False),
                (settings, "fanout_temu", False),
                (settings, "stock_watch_tope", 300)):
            self.pila.enter_context(mock.patch.object(obj, nombre, valor))
        self.addCleanup(self.pila.close)

    def _por(self, m):
        return {(f["sku"], f["canal"], f["item_id"]): f for f in m["filas"]}

    def test_clasifica_cada_publicacion_viva_o_latente(self):
        m = A.medir({})
        f = self._por(m)
        self.assertEqual(set(f), {("AAA-1", "mercado_libre", "MLM1"), ("AAA-1", "mercado_libre", "MLM2"),
                                  ("BBB-2", "tiktok", "TK1"), ("CCC-3", "mercado_libre", "MLM4"),
                                  ("DDD-4", "mercado_libre", "MLM5"), ("DDD-4", "tiktok", "TK2"),
                                  ("DDD-4", "temu", "T2"), ("EEE-5", "temu", "T3")})
        a1 = f[("AAA-1", "mercado_libre", "MLM1")]
        self.assertEqual((a1["categoria"], a1["objetivo"], a1["delta"], a1["plan_accion"], a1["vida"]),
                         ("baja", 10, -2, "escribir", "viva"))
        a2 = f[("AAA-1", "mercado_libre", "MLM2")]
        self.assertEqual((a2["categoria"], a2["vida"]), ("igual", "latente"))            # pausada = latente
        b = f[("BBB-2", "tiktok", "TK1")]
        self.assertEqual((b["categoria"], b["sentido"], b["objetivo"], b["libro"], b["vida"]),
                         ("sin_numero_en_woo", "sube", 5, 5, "latente"))                 # 6 − 1 pendiente
        # El candado del canal no cambia la clasificación: el CSV dice por qué no escribiría.
        self.assertEqual(b["plan_accion"], "omitir")
        self.assertIn("FANOUT_TIKTOK", b["plan_motivo"])
        # D4: el libro no trae CCC-3 y `plan()` mismo da objetivo 0 y escribiría.
        c = f[("CCC-3", "mercado_libre", "MLM4")]
        self.assertEqual((c["categoria"], c["objetivo"], c["delta"], c["plan_accion"], c["vida"]),
                         ("a_cero_desconocido", 0, -7, "escribir", "viva"))
        self.assertIsNone(c["libro"])
        self.assertFalse(c["seguro_apagaria"])                                            # ML: sin seguro
        tk2 = f[("DDD-4", "tiktok", "TK2")]
        self.assertEqual((tk2["categoria"], tk2["cambia"], tk2["vida"], tk2["seguro_apagaria"]),
                         ("a_cero_desconocido", False, "viva", True))                      # canal sin número
        t2 = f[("DDD-4", "temu", "T2")]
        self.assertEqual((t2["vida"], t2["seguro_apagaria"], t2["delta"]), ("latente", False, -5))
        t3 = f[("EEE-5", "temu", "T3")]
        self.assertEqual((t3["categoria"], t3["vida"], t3["seguro_apagaria"], t3["delta"]),
                         ("a_cero_desconocido", "latente", True, -3))
        self.assertFalse(any(x["categoria"] == "sin_plan" for x in m["filas"]))
        self.assertEqual(m["fuera"], {"mercado_libre: FULL/FBA": 1, "temu: Temu 5/None = Borrador": 1})

    def test_d4_vivas_y_latentes_en_el_resumen(self):
        texto = "\n".join(A.resumen(A.medir({}), "prueba"))
        self.assertIn("D4 · EL LIBRO NO LOS TRAE → 0", texto)
        self.assertIn("5 publicaciones de 3 SKUs · bajan 24 pzs", texto)                 # 7 + 9 + 5 + 3
        self.assertRegex(texto, r"vivas\s+2 pubs · 1 con piezas en el canal \(7 pzs\) · el seguro "
                                r"sacaría de la venta 1")
        self.assertRegex(texto, r"latentes\s+3 pubs · 3 con piezas en el canal \(17 pzs\) · el seguro "
                                r"sacaría de la venta 1")
        self.assertNotIn("explícitamente", texto)

    def test_usa_plan_con_el_libro_y_sin_amazon_en_vivo(self):
        vistas = []
        real = fanout_stock.plan

        def espia(sku):
            vistas.append(stock_libro.fuente_fanout())
            return real(sku)
        with mock.patch.object(fanout_stock, "plan", espia):
            A.medir({})
        self.assertEqual(set(vistas), {"kubera"})
        self.assertEqual(settings.fanout_fuente, "woo")                  # sólo en el proceso, y vuelve
        self.desglose.assert_called_once_with(None)                       # el libro, UNA vez
        self.leer_inv.assert_called_once()                                # las publicaciones, UNA vez
        # Amazon nunca llega a `_destinos` (si llegara, `_amazon_en_vivo` revienta).
        self.assertFalse(any(k[1] == "amazon" for k in self._por(A.medir({}))))

    def test_reserva(self):
        m = A.medir({}, reserva=1)
        f = self._por(m)
        self.assertEqual(f[("AAA-1", "mercado_libre", "MLM1")]["objetivo"], 9)
        self.assertEqual(f[("CCC-3", "mercado_libre", "MLM4")]["objetivo"], 0)        # nunca negativo
        self.assertEqual(m["reserva"], 1)
        self.assertEqual(settings.fanout_reserva, 0)

    def test_aprobados_se_verifican_no_fijan_objetivo(self):
        # AAA-1 está en el libro (número 10); CCC-3 y NOP-0 no: «iría a 0».
        m = A.medir({"AAA-1": "ok", "CCC-3": "recodificado", "NOP-0": "aprobado"})
        c = self._por(m)[("CCC-3", "mercado_libre", "MLM4")]
        self.assertEqual((c["categoria"], c["objetivo"], c["aprobado_motivo"]),
                         ("a_cero_desconocido", 0, "recodificado"))
        ap = m["aprobados"]
        self.assertEqual((ap["total"], ap["con_numero"], ap["en_cero"]), (3, 1, 0))
        self.assertEqual(ap["sin_numero"],
                         [{"sku": "CCC-3", "motivo": "recodificado", "woo": 7, "vivas": 1, "latentes": 0,
                           "pzs_canal": 7},
                          {"sku": "NOP-0", "motivo": "aprobado", "woo": None, "vivas": 0, "latentes": 0,
                           "pzs_canal": 0}])
        texto = "\n".join(A.resumen(m, "prueba"))
        self.assertIn("2 aprobado pero sin número en el libro: iría a 0", texto)
        self.assertIn("CCC-3", texto)
        self.assertNotIn("LISTA APROBADA", "\n".join(A.resumen(A.medir({}), "prueba")))

    def test_primera_pasada_contra_el_tope_cuenta_los_ceros_d4(self):
        m = A.medir({}, tope=4)
        p = m["primera"]
        # AAA-1 coincide con Woo; BBB-2 (Woo sin número → 5) y ZZZ-9 (1 → 3) cambian.
        self.assertEqual(sorted((s, a, b) for s, a, b in p["cambios"]),
                         [("AMZ-5", None, 4), ("BBB-2", None, 5), ("CCC-3", 7, 0), ("ZZZ-9", 1, 3)])
        # D4 (fa981c2): CCC-3 (Woo 7, el libro no lo trae) ya lo cuenta `_movidos_libro`;
        # DDD-4 y EEE-5 (sin número, con publicación) los suma el script.
        self.assertIn("CCC-3", p["salen"])
        self.assertEqual(p["ceros_d4"], [("DDD-4", None, 0), ("EEE-5", None, 0)])
        self.assertEqual(m["tope"], 4)
        texto = "\n".join(A.resumen(m, "prueba"))
        self.assertIn("6 cambios contra el tope de 4 → FRENARÍA", texto)              # 3 + 3 > 4
        self.assertIn("4 los cuenta `_movidos_libro` (1 que el libro no trae, a 0) + 2 a 0 por D4", texto)
        self.assertIn("0 con número ≠ 0, 2 sin número y con publicación", texto)
        self.assertIn("Sin ellos contaría 4 → pasaría sin forzar", texto)
        self.assertIn("neto +4", texto)                                               # +4 +5 +2 −7
        por = self._por(m)
        self.assertEqual(por[("BBB-2", "tiktok", "TK1")]["primera_pasada"], "libro")
        self.assertEqual(por[("CCC-3", "mercado_libre", "MLM4")]["primera_pasada"], "libro")
        self.assertEqual(por[("AAA-1", "mercado_libre", "MLM1")]["primera_pasada"], "")
        self.assertIn("pasa sin forzar", "\n".join(A.resumen(A.medir({}), "prueba")))

    def test_amazon_y_walmart_aparte(self):
        d2 = {r["sku"]: r for r in A.medir({})["d2"]}
        self.assertEqual((d2["AMZ-5"]["alcance"], d2["AMZ-5"]["categoria"], d2["AMZ-5"]["objetivo"]),
                         ("fuera_d2", "sin_numero_en_woo", 4))
        self.assertEqual((d2["WAL-6"]["categoria"], d2["WAL-6"]["objetivo"]), ("a_cero_desconocido", 0))
        self.assertFalse(d2["WAL-6"]["cambia"])

    def test_cotejo_y_csv(self):
        m = A.medir({})
        cot = {q: (s, mio) for q, s, mio, _n in A.cotejo(m)}
        self.assertEqual(cot["huérfanos (el libro no conoce, Woo > 0)"], (A.MAESTRO["huerfanos"], (1, 7)))
        self.assertEqual(cot["mercado_libre DROP sin número en Woo"][1], (1, 9))   # la pausada de DDD-4
        self.assertEqual(cot["tiktok DROP sin número en Woo"][1], (2, 0))         # BBB-2 y DDD-4
        self.assertEqual(cot["recodificaciones"][1], (None, None))
        ruta = Path(self._tmp()) / "x.csv"
        A.escribir_csv(m["filas"] + m["d2"], ruta)
        with ruta.open(encoding="utf-8") as fh:
            filas = list(csv.DictReader(fh))
        self.assertEqual(len(filas), len(m["filas"]) + len(m["d2"]))
        self.assertEqual(list(filas[0]), A.CAMPOS_CSV)

    def _tmp(self) -> str:
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return d

    def test_libro_ilegible_sube(self):
        self.desglose.side_effect = stock_libro.LibroNoDisponible("vacío o a medio cargar")
        with self.assertRaises(stock_libro.LibroNoDisponible):
            A.medir({})

    def test_foto_ilegible_aborta_con_2(self):
        with mock.patch.object(stock_watch_read, "foto_leer", side_effect=RuntimeError("caída")):
            with self.assertRaises(A.Abortar) as cm:
                A.medir({})
        self.assertEqual(cm.exception.codigo, 2)


class CerosD4(unittest.TestCase):
    """Desde fa981c2 `_movidos_libro` cuenta (X -> 0) TODO SKU de la foto que el libro no
    trae y cuyo último número no era 0 —el que salió y el que nunca estuvo—; el script
    no lo cuenta otra vez y sólo suma lo que la foto no trae o no tiene número."""

    def test_no_cuenta_dos_veces_lo_que_sale_del_libro(self):
        from services import stock_watch
        libro = {"A": 5}
        foto = {"A": {"woo": 5, "kubera": 5}, "S": {"woo": 2, "kubera": 2},   # S sale del libro
                "Z": {"woo": 4, "kubera": 0},                                 # ya estaba en 0
                "W": {"woo": 3, "kubera": None},                              # nunca en el libro
                "N": {"woo": None, "kubera": None}}                           # sin número
        cambios, salen = stock_watch._movidos_libro(libro, foto)
        self.assertEqual((sorted(cambios), salen), ([("S", 2, 0), ("W", 3, 0)], {"S", "Z", "W", "N"}))
        ya = {s for s, _a, _b in cambios}
        self.assertEqual(A.ceros_d4(libro, foto, ya, set()), [])
        # Sin número (o fuera de la foto) sólo cuenta si tiene publicación viva o latente.
        self.assertEqual(A.ceros_d4(libro, foto, ya, {"N", "X"}), [("N", None, 0), ("X", None, 0)])


# ═════════════════════════════════════════════════════════════════════════════
# 6. main
# ═════════════════════════════════════════════════════════════════════════════

class Main(unittest.TestCase):
    def _main(self, argv, dsn=DSN_SANDBOX, prod_ref=PROD, conn=None, stdin=None, medir=None):
        conectar = mock.Mock(return_value=conn or _Conn())
        err, out = io.StringIO(), io.StringIO()
        with mock.patch.object(settings, "supabase_db_url", dsn), \
                mock.patch.object(settings, "supabase_prod_ref", prod_ref), \
                mock.patch.object(A, "medir", medir or mock.Mock()), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            codigo = A.main(argv, conectar=conectar, stdin=stdin)
        return codigo, out.getvalue(), err.getvalue(), conectar

    def test_libro_ilegible_sale_con_2_y_cierra(self):
        conn = _Conn()
        medir = mock.Mock(side_effect=stock_libro.LibroNoDisponible("la vista no existe"))
        codigo, out, err, conectar = self._main([], conn=conn, medir=medir)
        self.assertEqual(codigo, 2)
        self.assertIn("LibroNoDisponible", err)
        self.assertIn("Candado de entorno OK: sandbox", out)
        self.assertNotIn("secreto", out + err)
        self.assertTrue(conn.cerrada)
        self.assertEqual(conn.rollbacks, 1)

    def test_aprobados_con_objetivo_avisa_que_se_ignora(self):
        d = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        ruta = d / "aprobados.csv"
        ruta.write_text("sku,objetivo\nX-1,5\n", encoding="utf-8")
        vistos = {}

        def medir(aprobados, reserva=None, tope=None):
            vistos["aprobados"] = aprobados
            raise stock_libro.LibroNoDisponible("sin bodegas")
        codigo, out, _e, _c = self._main(["--aprobados", str(ruta)], medir=medir)
        self.assertEqual(codigo, 2)
        self.assertEqual(vistos["aprobados"], {"X-1": "aprobado"})
        self.assertIn("la columna `objetivo` se ignora", out)

    def test_candado_sale_con_3_sin_conectar(self):
        codigo, _o, err, conectar = self._main([], dsn=DSN_PROD)
        self.assertEqual(codigo, 3)
        self.assertIn("PRODUCCIÓN", err)
        conectar.assert_not_called()

    def test_prod_por_stdin_y_desde_solo_en_el_proceso(self):
        vistos = {}

        def medir(aprobados, reserva=None, tope=None):
            vistos["desde"] = settings.stock_libro_desde
            raise stock_libro.LibroNoDisponible("sin bodegas")
        stdin = io.StringIO(f"SUPABASE_DB_URL={DSN_PROD}\nSUPABASE_PROD_REF={PROD}\n")
        antes = settings.stock_libro_desde
        codigo, out, _e, conectar = self._main(
            ["--entorno", "prod", "--dsn-stdin", "--desde", "2026-10-15T16:48:47Z"],
            dsn="", prod_ref="", stdin=stdin, medir=medir)
        self.assertEqual(codigo, 2)
        conectar.assert_called_once_with(DSN_PROD)
        self.assertIn("Candado de entorno OK: prod", out)
        self.assertEqual(vistos["desde"], "2026-10-15T16:48:47Z")
        self.assertEqual(settings.stock_libro_desde, antes)


if __name__ == "__main__":
    unittest.main()
