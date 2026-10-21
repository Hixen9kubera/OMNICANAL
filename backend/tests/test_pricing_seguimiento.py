"""Pruebas del seguimiento semanal de PRICING (services/pricing_seguimiento.py, v0.678.0).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Semanas ISO** de lunes a domingo; la de hoy al final, «en curso», con sus días.
2. **Antes / después**: 28 días previos al cambio contra lo transcurrido desde él
   (el día del cambio cuenta como después), llevados a «por semana».
3. **Movimiento**: vendió, más visitas (+10 % por semana), sin movimiento; sin
   visitas de ML no se afirma nada.
4. **Un cambio por publicación**: manda el más reciente y se cuentan todos.
5. **Acceso**: lo ve quien ve PRICING; sin sesión, 401.

Sin red: nada aquí habla con ML ni con kubera.

    cd backend && python -m unittest tests.test_pricing_seguimiento -v
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import pricing as ruta  # noqa: E402
from services import pricing_seguimiento as ps  # noqa: E402

CDMX = timezone(timedelta(hours=-6))
HOY = date(2026, 10, 21)          # miércoles
CAMBIO = date(2026, 10, 15)       # el lote de descuentos


class Semanas(unittest.TestCase):
    def test_ocho_semanas_iso_y_la_de_hoy_en_curso(self):
        s = ps.semanas(HOY)
        self.assertEqual(len(s), 8)
        self.assertEqual((s[-1]["clave"], s[-1]["inicio"], s[-1]["fin"], s[-1]["en_curso"], s[-1]["dias"]),
                         ("2026-W43", "2026-10-19", "2026-10-25", True, 3))
        self.assertEqual((s[-2]["clave"], s[-2]["dias"], s[-2]["en_curso"]), ("2026-W42", 7, False))
        self.assertEqual(s[0]["inicio"], "2026-08-31")
        self.assertTrue(all(date.fromisoformat(x["inicio"]).weekday() == 0 for x in s))

    def test_por_semana_suma_y_respeta_los_dias_en_curso(self):
        s = ps.semanas(HOY)
        serie = {"2026-10-12": 1, "2026-10-18": 2, "2026-10-19": 5, "2026-10-24": 9}   # el 24 aún no llega
        self.assertEqual(ps.por_semana(serie, s)[-2:], [3, 5])
        self.assertEqual(ps.por_semana(None, s), [None] * 8)


class AntesDespues(unittest.TestCase):
    def test_ventanas_y_ritmo_por_semana(self):
        piezas = {"2026-09-17": 1, "2026-09-16": 7,       # el 16-sep queda fuera (29 días antes)
                  "2026-10-15": 1, "2026-10-20": 2}       # el día del cambio cuenta como «después»
        visitas = {**{(CAMBIO - timedelta(days=k)).isoformat(): 1 for k in range(1, 29)},
                   **{(CAMBIO + timedelta(days=k)).isoformat(): 4 for k in range(0, 7)}}
        r = ps.antes_despues(CAMBIO, HOY, piezas, visitas)
        self.assertEqual((r["antes"]["piezas"], r["antes"]["dias"], r["despues"]["piezas"], r["despues"]["dias"]), (1, 28, 3, 7))
        self.assertEqual((r["antes"]["piezas_sem"], r["despues"]["piezas_sem"]), (0.25, 3.0))
        self.assertEqual((r["antes"]["visitas_sem"], r["despues"]["visitas_sem"]), (7.0, 28.0))
        self.assertEqual(r["mov"], {"vendio": True, "vende_mas": True, "mas_visitas": True, "sin_movimiento": False})

    def test_sin_movimiento_y_sin_visitas(self):
        visitas = {(CAMBIO - timedelta(days=k)).isoformat(): 2 for k in range(1, 29)}
        visitas.update({(CAMBIO + timedelta(days=k)).isoformat(): 2 for k in range(0, 7)})
        r = ps.antes_despues(CAMBIO, HOY, {}, visitas)
        self.assertEqual(r["mov"], {"vendio": False, "vende_mas": False, "mas_visitas": False, "sin_movimiento": True})
        r = ps.antes_despues(CAMBIO, HOY, {}, None)                     # ML no dio visitas: no se afirma nada
        self.assertEqual((r["mov"]["mas_visitas"], r["mov"]["sin_movimiento"], r["antes"]["visitas"]), (None, False, None))

    def test_despues_topa_en_28_dias(self):
        r = ps.antes_despues(date(2026, 8, 1), HOY, {"2026-08-29": 1, "2026-08-28": 1}, None)
        self.assertEqual((r["despues"]["dias"], r["despues"]["piezas"]), (28, 1))     # 1-ago … 28-ago


class Cambios(unittest.TestCase):
    def test_manda_el_mas_reciente_y_se_cuentan_todos(self):
        f1 = datetime(2026, 10, 15, 17, 40, tzinfo=CDMX)
        f2 = datetime(2026, 10, 19, 12, 49, tzinfo=CDMX)
        lote = [{"id": "MLM1", "cuenta": "SANCORFASHION", "sku": "ORG-0448-VER", "fecha": f1, "tipo": "descuento",
                 "antes": 97, "nuevo": 97, "grupo": "lote:2026-10-15"},
                {"id": "MLM2", "cuenta": "BEKURA", "sku": "X", "fecha": f1, "tipo": "descuento", "antes": 10, "nuevo": 9,
                 "grupo": "lote:2026-10-15"}]
        panel = [{"id": "MLM1", "cuenta": "SANCORFASHION", "sku": None, "fecha": f2, "tipo": "precio", "antes": 97,
                  "nuevo": 90, "quien": "brandon@kubera.mx", "grupo": "panel"}]
        c = ps.unir_cambios(lote, panel)
        self.assertEqual((c["MLM1"]["grupo"], c["MLM1"]["nuevo"], c["MLM1"]["n"], c["MLM1"]["sku"]), ("panel", 90, 2, "ORG-0448-VER"))
        self.assertEqual((c["MLM2"]["n"], c["MLM2"]["grupo"]), (1, "lote:2026-10-15"))


def _cliente() -> TestClient:
    app = FastAPI()
    app.include_router(ruta.router)
    return TestClient(app)


def _como(quien):
    return mock.patch("routers.investigacion.core_identidad.resolver", new=mock.AsyncMock(return_value=quien))


class Acceso(unittest.TestCase):
    def test_lo_ve_quien_ve_pricing(self):
        invitado = SimpleNamespace(autenticado=True, tipo="persona", rol="operador", actor="haim@kubera.mx")
        anonimo = SimpleNamespace(autenticado=False, tipo="", rol="", actor="?")
        otro = SimpleNamespace(autenticado=True, tipo="persona", rol="operador", actor="valeria@kubera.mx")
        with mock.patch.object(ps, "seguimiento", new=mock.AsyncMock(return_value={"pubs": []})) as s:
            with _como(invitado):
                self.assertEqual(_cliente().get("/api/pricing/seguimiento").status_code, 200)
            with _como(otro):
                self.assertEqual(_cliente().get("/api/pricing/seguimiento").status_code, 403)
            with _como(anonimo):
                self.assertEqual(_cliente().get("/api/pricing/seguimiento").status_code, 401)
        self.assertEqual(s.await_count, 1)


if __name__ == "__main__":
    unittest.main()
