"""Pruebas del SONDEO DE VENTAS DE ML (PLAN MAESTRO 2.11/F2.3, 28-oct-2026).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Clasificar**: falta / sin_pedido / atrasada / al_dia, y una orden recién tocada se deja
   al webhook (`reciente`). Una orden se reconoce en kubera por su número o por su pack.
2. **Solo registro no toca nada**: ni ML de escritura ni `sincronizar`.
3. **Reparar** usa el camino de los reintentos (`sincronizar(reintentable=True)`), respeta el
   LOTE y se detiene en el freno de pedidos creados por hora.
4. **Sin PEDIDOS_WC_ENABLED o con el freno puesto** cae a solo registro.
5. **ML**: pagina hasta el total con ventana fija (`order.date_last_updated.from`), y un 401 pasa
   por el renovador compartido del backend (`meli._renovar_con_candado`), una sola vez.

Sin red ni base:
    cd backend && python -m unittest tests.test_pedidos_ml_sondeo -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from services import alertas, meli, pedidos_ml, pedidos_ml_sondeo as S  # noqa: E402
from services.reintentos_freno import FRENO_ML  # noqa: E402

AHORA = datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc)
VIEJA = AHORA - timedelta(hours=2)


def orden(estado="paid", pack=None, act=VIEJA):
    return {"estado": estado, "pack": pack, "creada": act, "actualizada": act}


class Clasificar(unittest.TestCase):
    def test_cada_clase(self):
        ml = {"1": orden(), "2": orden(), "3": orden("cancelled"), "4": orden(), "5": orden(act=AHORA)}
        kb = {"2": {"wc_order_id": None, "estado_canal": "paid", "registrado": VIEJA},
              "3": {"wc_order_id": 9, "estado_canal": "paid", "registrado": VIEJA},
              "4": {"wc_order_id": 9, "estado_canal": "paid", "registrado": VIEJA}}
        cl = S.clasificar(ml, kb, AHORA, 10)
        self.assertEqual({k: v for k, v in cl.items() if v},
                         {"falta": ["1"], "sin_pedido": ["2"], "atrasada": ["3"], "al_dia": ["4"],
                          "reciente": ["5"]})

    def test_sin_pedido_reciente_no_cuenta(self):
        kb = {"2": {"wc_order_id": None, "estado_canal": "paid", "registrado": AHORA - timedelta(minutes=3)}}
        self.assertEqual(S.clasificar({"2": orden()}, kb, AHORA, 10)["al_dia"], ["2"])

    def test_se_reconoce_por_el_pack(self):
        kb = {"P1": {"wc_order_id": 9, "estado_canal": "paid", "registrado": VIEJA}}
        self.assertEqual(S.clasificar({"1": orden(pack="P1")}, kb, AHORA, 10)["al_dia"], ["1"])


class Revisar(unittest.TestCase):
    def setUp(self) -> None:
        mock.patch.multiple(settings, pedidos_ml_sondeo_enabled=True, pedidos_ml_sondeo_solo_registro=True,
                            pedidos_ml_sondeo_lote=2, pedidos_wc_enabled=True,
                            pedidos_wc_descuenta_stock=True).start()
        self.addCleanup(mock.patch.stopall)
        self.aviso = mock.patch.object(alertas, "avisar_estado", return_value=True).start()
        ml = {"1": orden(), "2": orden(), "3": orden()}
        mock.patch.object(S, "leer_ml", mock.AsyncMock(
            side_effect=lambda c, h, p: {"ordenes": ml if c == "BEKURA" else {}, "paginas": 1, "total": 3,
                                         "error": None})).start()
        mock.patch.object(S, "kubera", return_value={}).start()
        self.obtener = mock.patch.object(meli, "obtener_orden", mock.AsyncMock(return_value={"id": "x"})).start()
        self.sinc = mock.patch.object(pedidos_ml, "sincronizar",
                                      mock.AsyncMock(return_value={"ok": True, "accion": "creado"})).start()
        mock.patch.object(FRENO_ML, "detenido", return_value=None).start()
        self.anotar = mock.patch.object(FRENO_ML, "anotar_creado", return_value=False).start()

    def test_solo_registro_cuenta_y_avisa_sin_tocar(self):
        r = asyncio.run(S.revisar())
        self.assertEqual((r["cuentas"]["BEKURA"]["falta"], r["por_reparar"], r["reparadas"]), (3, 3, 0))
        self.sinc.assert_not_called()
        self.assertEqual(self.aviso.call_args.args[1], "diferencias")

    def test_repara_hasta_el_lote_por_el_camino_de_los_reintentos(self):
        r = asyncio.run(S.revisar(solo_registro=False))
        self.assertEqual((r["reparadas"], r["creados"]), (2, 2))
        self.assertEqual(self.sinc.await_count, 2)
        self.assertTrue(all(c.kwargs["reintentable"] for c in self.sinc.await_args_list))

    def test_se_detiene_en_el_freno(self):
        self.anotar.return_value = True
        r = asyncio.run(S.revisar(solo_registro=False))
        self.assertEqual((r["estado"], r["reparadas"]), ("frenado", 1))

    def test_sin_pedidos_wc_cae_a_solo_registro(self):
        with mock.patch.object(settings, "pedidos_wc_enabled", False):
            r = asyncio.run(S.revisar(solo_registro=False))
        self.assertEqual(r["estado"], "omitido")
        self.sinc.assert_not_called()

    def test_con_el_freno_puesto_no_repara(self):
        with mock.patch.object(FRENO_ML, "detenido", return_value={"motivo": "manual"}):
            r = asyncio.run(S.revisar(solo_registro=False))
        self.assertEqual(r["estado"], "frenado")
        self.sinc.assert_not_called()

    def test_todo_al_dia_es_ok(self):
        with mock.patch.object(S, "leer_ml", mock.AsyncMock(
                return_value={"ordenes": {}, "paginas": 1, "total": 0, "error": None})):
            r = asyncio.run(S.revisar())
        self.assertEqual(r["estado"], "ok")
        self.assertEqual(self.aviso.call_args.args[1], "ok")


class Respuesta:
    def __init__(self, code, cuerpo=None):
        self.status_code, self._c = code, cuerpo

    def json(self):
        return self._c


class LeerMl(unittest.TestCase):
    def setUp(self) -> None:
        S._uids.clear()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(meli, "access_token_async", mock.AsyncMock(return_value="viejo")).start()
        self.renovar = mock.patch.object(meli, "_renovar_con_candado", mock.AsyncMock(return_value="nuevo")).start()

    def test_pagina_con_ventana_fija_y_un_401_pasa_por_el_renovador(self):
        llamadas = []

        async def get(ruta, params=None, headers=None):
            llamadas.append((ruta, dict(params or {}), headers["Authorization"]))
            if ruta == "/users/me":
                return Respuesta(401) if headers["Authorization"] == "Bearer viejo" else Respuesta(200, {"id": 7})
            off = params["offset"]
            res = [{"id": off + i + 1, "status": "paid", "date_created": "2026-10-28T10:00:00.000-04:00"}
                   for i in range(50 if off == 0 else 10)]
            return Respuesta(200, {"results": res, "paging": {"total": 60}})

        cli = mock.Mock(get=get)
        r = asyncio.run(S.leer_ml("BEKURA", 24, 40, cli=cli))
        self.assertEqual((len(r["ordenes"]), r["paginas"], r["error"]), (60, 2, None))
        self.renovar.assert_awaited_once_with("BEKURA")
        busquedas = [p for ruta, p, _ in llamadas if ruta == "/orders/search"]
        self.assertTrue(all("order.date_last_updated.from" in p and p["seller"] == 7 for p in busquedas))

    def test_un_error_de_ml_deja_el_error_y_no_lanza(self):
        async def get(ruta, params=None, headers=None):
            return Respuesta(200, {"id": 7}) if ruta == "/users/me" else Respuesta(500)

        r = asyncio.run(S.leer_ml("BEKURA", 24, 40, cli=mock.Mock(get=get)))
        self.assertIn("HTTP 500", r["error"])


if __name__ == "__main__":
    unittest.main()
