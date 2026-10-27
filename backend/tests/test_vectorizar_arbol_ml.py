"""
`scripts/vectorizar_arbol_ml.py --cron`: el paso que va al final del cron de las 06:15
(`railway.etl-core.json`) no puede tumbarlo. Sin la tabla de la 0078, sin la clave de
OpenRouter o sin su lista de precios avisa y sale en 0; a mano (sin `--cron`) aborta. Sin red
ni BD. Sin clave ni nada que hacer, no consulta la red.
"""
from __future__ import annotations

import sys
import unittest
from unittest import mock

from scripts import vectorizar_arbol_ml as va


class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self._ultimo = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        self._ultimo = sql

    def fetchone(self):
        return (self.conn.existe,)

    def fetchall(self):
        if "ml_category_tree" in self._ultimo:
            return [(f"MLM{i}", ["Raíz", f"Hoja {i}"]) for i in range(6_000)]
        return []


class _Conn:
    def __init__(self, existe):
        self.existe = existe
        self.sql = []

    def cursor(self):
        return _Cur(self)

    def rollback(self):
        pass

    def commit(self):
        pass

    def close(self):
        pass


def _correr(argv, existe=True, llave="", precios=None):
    conn = _Conn(existe)
    precios = precios or mock.Mock(return_value={va.MODELO_TEXTO: 1e-8, va.MODELO_FOTO: 1e-7})
    with mock.patch.object(sys, "argv", ["vectorizar_arbol_ml.py", *argv]), \
         mock.patch.object(va, "_env_destino", return_value="dsn"), \
         mock.patch.object(va.psycopg2, "connect", return_value=conn), \
         mock.patch.object(va, "precios", precios), \
         mock.patch.dict(va.os.environ, {"OPENROUTER_API_KEY": llave}), \
         mock.patch.object(va.httpx, "Client") as cliente:
        r = va.main()
    return r, conn, cliente


class ModoCron(unittest.TestCase):
    def test_sin_tabla_en_cron_sale_en_cero(self):
        r, conn, cliente = _correr(["--aplicar", "--destino", "prod", "--cron"], existe=False)
        self.assertEqual(r, 0)
        cliente.assert_not_called()
        self.assertFalse(any("ml_category_tree" in s for s in conn.sql))

    def test_sin_tabla_a_mano_aborta(self):
        r, _, _ = _correr(["--aplicar", "--destino", "prod"], existe=False)
        self.assertEqual(r, 2)

    def test_sin_clave_en_cron_sale_en_cero_sin_llamar(self):
        r, _, cliente = _correr(["--aplicar", "--destino", "prod", "--cron"])
        self.assertEqual(r, 0)
        cliente.assert_not_called()

    def test_sin_clave_a_mano_aborta(self):
        r, _, cliente = _correr(["--aplicar", "--destino", "prod"])
        self.assertEqual(r, 2)
        cliente.assert_not_called()

    def test_sin_clave_no_consulta_precios(self):
        precios = mock.Mock()
        r, _, _ = _correr(["--aplicar", "--destino", "prod", "--cron"], precios=precios)
        self.assertEqual(r, 0)
        precios.assert_not_called()

    def test_precios_caidos_en_cron_sale_en_cero(self):
        precios = mock.Mock(side_effect=OSError("sin red"))
        r, _, cliente = _correr(["--aplicar", "--destino", "prod", "--cron"], llave="k", precios=precios)
        self.assertEqual(r, 0)
        cliente.assert_not_called()

    def test_precios_caidos_a_mano_lanza(self):
        precios = mock.Mock(side_effect=OSError("sin red"))
        with self.assertRaises(OSError):
            _correr(["--aplicar", "--destino", "prod"], llave="k", precios=precios)


if __name__ == "__main__":
    unittest.main()
