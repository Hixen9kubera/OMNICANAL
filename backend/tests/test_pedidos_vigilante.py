"""Pruebas del VIGILANTE de ventas reclamadas sin pedido (PLAN MAESTRO 2.11/F2.3, 28-oct-2026).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Apagado no lee ni avisa.**
2. **Avisa por cambio de estado**: con filas, `pendientes` y un texto con el número y las
   llaves; sin filas, `ok` (la recuperación la decide `avisar_estado`).
3. **No lanza**: si kubera no contesta, queda `sin_lectura` y no se avisa nada.
4. **El SQL** lee `registrado_at` por `to_jsonb` (funciona con la 0079 o sin ella), cae a
   `creado_at`, filtra `wc_order_id IS NULL` y pasa umbral y exclusiones como parámetros.
5. **Solo lee**: el módulo no tiene ninguna escritura.

Sin red ni base:
    cd backend && python -m unittest tests.test_pedidos_vigilante -v
"""
from __future__ import annotations

import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from services import alertas, pedidos_vigilante  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

FILA = {"canal": "mercado_libre", "cuenta": "BEKURA", "external_order_id": "2000000001",
        "desde": datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc), "minutos": 42}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        p = mock.patch.multiple(settings, pedidos_vigilante_enabled=True, pedidos_vigilante_umbral_min=10,
                                pedidos_vigilante_excluir="temu:TEMU:PO-1, ")
        p.start()
        self.addCleanup(p.stop)
        self.aviso = mock.patch.object(alertas, "avisar_estado", return_value=True).start()
        self.addCleanup(mock.patch.stopall)


class Revisar(Base):
    def test_apagado_no_lee_ni_avisa(self):
        with mock.patch.object(settings, "pedidos_vigilante_enabled", False), \
             mock.patch.object(sdb, "fetch_all", side_effect=AssertionError("leyó")):
            self.assertEqual(pedidos_vigilante.revisar(), {"estado": "apagado"})
        self.aviso.assert_not_called()

    def test_con_pendientes_avisa_con_numero_y_llaves(self):
        with mock.patch.object(sdb, "fetch_all", return_value=[FILA] * 12) as fa:
            r = pedidos_vigilante.revisar()
        self.assertEqual((r["estado"], r["n"], len(r["muestra"])), ("pendientes", 12, 10))
        tipo, estado, texto = self.aviso.call_args.args[:3]
        self.assertEqual((tipo, estado), ("pedidos_sin_completar", "pendientes"))
        self.assertIn("12 venta(s)", texto)
        self.assertIn("mercado_libre/BEKURA 2000000001 (42 min)", texto)
        self.assertIn("y 2 más", texto)
        self.assertEqual(fa.call_args.args[1], {"umbral": 10, "excluir": ["temu:TEMU:PO-1"]})

    def test_sin_pendientes_es_ok(self):
        with mock.patch.object(sdb, "fetch_all", return_value=[]):
            r = pedidos_vigilante.revisar()
        self.assertEqual((r["estado"], r["n"]), ("ok", 0))
        self.assertEqual(self.aviso.call_args.args[1], "ok")

    def test_kubera_caida_no_lanza_ni_avisa(self):
        with mock.patch.object(sdb, "fetch_all", side_effect=RuntimeError("sin kubera")):
            r = pedidos_vigilante.revisar()
        self.assertEqual(r["estado"], "sin_lectura")
        self.aviso.assert_not_called()

    def test_estado_dice_si_esta_habilitado(self):
        with mock.patch.object(sdb, "fetch_all", return_value=[]):
            pedidos_vigilante.revisar()
        e = pedidos_vigilante.estado()
        self.assertTrue(e["habilitado"])
        self.assertEqual(e["estado"], "ok")


class ElSql(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = " ".join(pedidos_vigilante._SQL.split())

    def test_lee_registrado_at_sin_depender_de_la_0079(self):
        self.assertIn("coalesce((to_jsonb(o) ->> 'registrado_at')::timestamptz, o.creado_at)", self.sql)
        self.assertNotRegex(self.sql, r"\bo\.registrado_at\b")

    def test_solo_las_que_no_tienen_pedido(self):
        self.assertIn("where o.wc_order_id is null", self.sql)

    def test_umbral_y_exclusiones_son_parametros(self):
        self.assertIn("make_interval(mins => %(umbral)s)", self.sql)
        self.assertIn("= any(%(excluir)s)", self.sql)

    def test_el_modulo_no_escribe(self):
        fuente = Path(pedidos_vigilante.__file__).read_text(encoding="utf-8")
        self.assertFalse(re.search(r"\b(insert|update|delete)\s+(into\s+)?\w+\.\w+", fuente, re.I))
        self.assertNotIn("sdb.execute", fuente)


if __name__ == "__main__":
    unittest.main()
