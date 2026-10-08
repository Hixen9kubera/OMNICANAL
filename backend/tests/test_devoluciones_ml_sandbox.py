"""El recálculo de `venta_contaba` (`services/devoluciones_ml.py`, la sexta
falla) contra el SANDBOX: el UPDATE de verdad sobre `channel.returns` y los
triggers de la 0049.

`test_devoluciones_ml.py` fija la FORMA de la sentencia con un cursor simulado;
aquí se prueba lo que solo Postgres puede decir:

  · la primera pasada cambia N filas y la segunda, 0 (solo toca lo que cambia);
  · `channel.return_history` no crece: el trigger de historia no ve el cambio;
  · `abierta_at` no se mueve en las filas recalculadas;
  · una fila con `venta_contaba` NULL y su orden en `channel.orders` toma valor,
    el mismo que daría el estado vivo de la orden;
  · el log deja el valor de antes de cada fila cambiada.

APAGADA POR DEFECTO: la suite corre sin red. Se enciende con
`OMNI_PRUEBAS_SANDBOX=1` y toma `SUPABASE_DB_URL` de `env.staging` (raíz del
repo, o la ruta de `OMNI_ENV_STAGING`). Se NIEGA a correr si ese DSN no es el sandbox (yvootpbz) o si es la BD
kubera de producción (tukwcvsi / SUPABASE_PROD_REF).

Nada queda escrito: cada prueba corre en UNA transacción (puerto 5432, la
conexión es nuestra) que termina en ROLLBACK. La sesión NUNCA se marca
read-only (regla 13).

    cd backend && OMNI_PRUEBAS_SANDBOX=1 python -m unittest tests.test_devoluciones_ml_sandbox -v
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import devoluciones_ml as dml  # noqa: E402

_ENCENDIDA = os.environ.get("OMNI_PRUEBAS_SANDBOX") == "1"
_REF_SANDBOX = "yvootpbz"
_REF_PROD = "tukwcvsi"


def _dsn_sandbox() -> str:
    """El DSN del sandbox, en modo sesión (5432). Aborta ante cualquier duda."""
    valores: dict[str, str] = {}
    # Un worktree no trae `env.staging` (no está en git): OMNI_ENV_STAGING
    # apunta al de la copia principal sin tener que copiar el archivo.
    ruta = Path(os.environ.get("OMNI_ENV_STAGING") or BACKEND.parent / "env.staging")
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        s = linea.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            valores[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    url = valores.get("SUPABASE_DB_URL", "")
    m = re.search(r"postgres\.([a-z0-9]+):", url)
    ref = m.group(1) if m else ""
    prod = valores.get("SUPABASE_PROD_REF", "").strip()
    if (not ref.startswith(_REF_SANDBOX) or _REF_PROD in url
            or (prod and ref == prod)):
        raise RuntimeError("SUPABASE_DB_URL de env.staging no es el sandbox: "
                           "no se corre nada")
    return re.sub(r"(pooler\.supabase\.com):6543\b", r"\1:5432", url)


@unittest.skipUnless(_ENCENDIDA, "prueba contra el sandbox: OMNI_PRUEBAS_SANDBOX=1")
class RecalculoEnSandbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        from psycopg2.extras import RealDictCursor

        cls.cn = psycopg2.connect(_dsn_sandbox(), connect_timeout=20,
                                  cursor_factory=RealDictCursor)
        cls.cn.autocommit = False
        cur = cls.cn.cursor()
        cur.execute("select to_regclass('channel.returns') as r, "
                    "to_regclass('channel.return_history') as h")
        f = cur.fetchone()
        cls.cn.rollback()
        if not (f["r"] and f["h"]):
            cls.cn.close()
            raise unittest.SkipTest("el sandbox no tiene la 0049 aplicada")

    @classmethod
    def tearDownClass(cls):
        cls.cn.rollback()
        cls.cn.close()

    def setUp(self):
        self.cur = self.cn.cursor()
        self.addCleanup(self.cn.rollback)

        # `recalcular_venta_contaba` usa `sdb.get_cursor`, que hace COMMIT al
        # salir. Aquí se le da el cursor de NUESTRA transacción, sin commit.
        @contextlib.contextmanager
        def cursor():
            yield self.cur
        mock.patch.object(dml.sdb, "get_cursor", side_effect=cursor).start()
        self.addCleanup(mock.patch.stopall)

    def _uno(self, sql, params=None):
        self.cur.execute(sql, params)
        return self.cur.fetchone()

    def test_primera_pasada_cambia_y_la_segunda_no(self):
        historia = self._uno("select count(*) as n from channel.return_history")["n"]
        abiertas = {(f["cuenta"], f["external_return_id"]): f["abierta_at"]
                    for f in self._todas()}
        with self.assertLogs(dml.log, "INFO") if self._pendientes() else \
                contextlib.nullcontext():
            n1 = dml.recalcular_venta_contaba()
        n2 = dml.recalcular_venta_contaba()
        print(f"\n  sandbox: la primera pasada cambiaría {n1} fila(s) de "
              f"{len(abiertas)}; la segunda, {n2}")
        self.assertEqual(n2, 0)
        self.assertEqual(self._uno("select count(*) as n from channel.return_history")["n"],
                         historia)
        despues = {(f["cuenta"], f["external_return_id"]): f["abierta_at"]
                   for f in self._todas()}
        self.assertEqual(despues, abiertas)

    def test_una_fila_null_toma_el_estado_vivo_de_su_orden(self):
        f = self._uno(
            """select r.cuenta, r.external_return_id,
                      not (lower(coalesce(o.estado_canal, '')) = any(%s)) as esperado
                 from channel.returns r
                 join channel.orders o
                   on o.canal = r.canal and o.cuenta = r.cuenta
                  and o.external_order_id = r.external_order_id
                where r.canal = %s
                limit 1""", (list(dml._CANCELADOS), dml.CANAL))
        if not f:
            self.skipTest("el sandbox no tiene devoluciones con su orden")
        llave = (dml.CANAL, f["cuenta"], f["external_return_id"])
        self.cur.execute("""update channel.returns set venta_contaba = null
                             where canal = %s and cuenta = %s and external_return_id = %s""",
                         llave)
        with self.assertLogs(dml.log, "INFO") as logs:
            self.assertGreaterEqual(dml.recalcular_venta_contaba(), 1)
        valor = self._uno("""select venta_contaba from channel.returns
                              where canal = %s and cuenta = %s
                                and external_return_id = %s""", llave)["venta_contaba"]
        self.assertIs(valor, f["esperado"])
        self.assertTrue(any(f"{f['cuenta']}/{f['external_return_id']}: None→" in l
                            for l in logs.output))

    def _todas(self):
        self.cur.execute("""select cuenta, external_return_id, abierta_at
                              from channel.returns where canal = %s""", (dml.CANAL,))
        return self.cur.fetchall()

    def _pendientes(self) -> int:
        return self._uno(
            """select count(*) as n
                 from channel.returns r
                 join channel.orders o
                   on o.canal = r.canal and o.cuenta = r.cuenta
                  and o.external_order_id = r.external_order_id
                where r.canal = %s
                  and r.venta_contaba is distinct from
                      (not (lower(coalesce(o.estado_canal, '')) = any(%s)))""",
            (dml.CANAL, list(dml._CANCELADOS)))["n"]


if __name__ == "__main__":
    unittest.main()
