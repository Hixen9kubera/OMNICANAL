"""Vigilante de cobertura del sync (v0.599.0).

Fija que cada vuelta del sync anote QUÉ visitó (no qué cambió), que el estado
distinga un sync sano de uno atorado, callado o sin respuesta, y que nada de
esto pueda tumbar al sync. El SQL real se prueba contra el sandbox en
`scripts/probar_vigilante_sync_sandbox.py`.

    cd backend && python -m unittest tests.test_vigilante_sync -v
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402
from services import inventario as inv  # noqa: E402
from services import vigilante_sync as vs  # noqa: E402

AHORA = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


class AnotarRonda(unittest.TestCase):
    def test_apagado_no_escribe(self):
        with mock.patch.object(settings, "vigilante_sync_enabled", False), \
             mock.patch.object(vs.sdb, "execute") as ex:
            vs.anotar_ronda("mercado_libre", "BEKURA", 10, ["MLM1"], 1)
        ex.assert_not_called()

    def test_encendido_anota_universo_visitados_y_respuestas(self):
        with mock.patch.object(settings, "vigilante_sync_enabled", True), \
             mock.patch.object(vs.sdb, "execute") as ex:
            vs.anotar_ronda("mercado_libre", "BEKURA", 2550, ["MLM2", "MLM1", "MLM2", None], 2)
        sql, params = ex.call_args.args
        self.assertIn("insert into ops.process_log", sql)
        self.assertEqual(params[:2], ("sync_cobertura", "mercado_libre|BEKURA"))
        self.assertEqual(json.loads(params[2]),
                         {"n": 2550, "leidos": ["MLM1", "MLM2"], "respondidos": 2})

    def test_nunca_lanza(self):
        with mock.patch.object(settings, "vigilante_sync_enabled", True), \
             mock.patch.object(vs.sdb, "execute", side_effect=RuntimeError("base caída")):
            vs.anotar_ronda("amazon", "", 1836, ["SKU-1"], 1)   # no revienta


def _cobertura(claves, ventana):
    with mock.patch.object(vs.sdb, "fetch_all", side_effect=[claves, ventana]):
        return {f["clave"]: f for f in vs.cobertura(24, ahora=AHORA)}


def _clave(accion, desde_h, ultima_min):
    return {"accion": accion, "desde": AHORA - timedelta(hours=desde_h),
            "ultima": AHORA - timedelta(minutes=ultima_min)}


def _ventana(accion, n, distintos, vueltas=96, visitados=7680, respondidos=7000):
    return {"accion": accion, "universo": n, "distintos": distintos, "vueltas": vueltas,
            "visitados": visitados, "respondidos": respondidos}


class Estados(unittest.TestCase):
    def setUp(self):
        p = mock.patch.multiple(settings, vigilante_sync_min_cobertura=0.9,
                                vigilante_sync_max_silencio_min=60)
        p.start()
        self.addCleanup(p.stop)

    def test_sano_atorado_calentando_callado_y_mudo(self):
        f = _cobertura(
            [_clave("mercado_libre|BEKURA", 30, 10), _clave("mercado_libre|SANCORFASHION", 30, 10),
             _clave("amazon|", 5, 10), _clave("zz|callado", 30, 180), _clave("zz|mudo", 30, 10)],
            [_ventana("mercado_libre|BEKURA", 2554, 2554),
             _ventana("mercado_libre|SANCORFASHION", 2549, 118),      # las mismas 80 + algo
             _ventana("amazon|", 1836, 400),
             _ventana("zz|callado", 100, 100),
             _ventana("zz|mudo", 100, 100, respondidos=0)])
        self.assertEqual(f["mercado_libre|BEKURA"]["estado"], "ok")
        self.assertEqual(f["mercado_libre|BEKURA"]["cobertura"], 1.0)
        self.assertEqual(f["mercado_libre|SANCORFASHION"]["estado"], "baja")
        self.assertEqual(f["amazon|"]["estado"], "calentando")       # sin 24 h de historia
        self.assertEqual(f["zz|callado"]["estado"], "sin_vueltas")   # última hace 3 h
        self.assertEqual(f["zz|mudo"]["estado"], "sin_respuesta")
        self.assertEqual(f["amazon|"]["nombre"], "Amazon")

    def test_sin_vueltas_en_la_ventana_pero_si_en_la_semana(self):
        f = _cobertura([_clave("amazon|", 100, 26 * 60)], [])
        self.assertEqual((f["amazon|"]["estado"], f["amazon|"]["cobertura"]), ("sin_vueltas", None))

    def test_la_cobertura_no_pasa_de_100(self):
        f = _cobertura([_clave("amazon|", 30, 5)], [_ventana("amazon|", 1800, 1836)])
        self.assertEqual(f["amazon|"]["cobertura"], 1.0)


class Revisar(unittest.TestCase):
    def test_avisa_por_estado_salta_calentando_y_poda(self):
        filas = [{"clave": "mercado_libre|BEKURA", "nombre": "ML BEKURA", "estado": "ok",
                  "distintos": 2554, "universo": 2554, "cobertura": 1.0, "visitados": 1,
                  "respondidos": 1, "ultima": AHORA},
                 {"clave": "mercado_libre|SANCORFASHION", "nombre": "ML SANCORFASHION",
                  "estado": "baja", "distintos": 118, "universo": 2549, "cobertura": 0.046,
                  "visitados": 1, "respondidos": 1, "ultima": AHORA},
                 {"clave": "amazon|", "nombre": "Amazon", "estado": "calentando",
                  "distintos": 0, "universo": 0, "cobertura": None, "visitados": 0,
                  "respondidos": 0, "ultima": AHORA}]
        from services import alertas
        with mock.patch.object(settings, "vigilante_sync_enabled", True), \
             mock.patch.object(vs, "cobertura", return_value=filas), \
             mock.patch.object(alertas, "avisar_estado") as av, \
             mock.patch.object(vs.sdb, "execute") as ex:
            vs.revisar()
        tipos = {c.args[0]: c.args[1] for c in av.call_args_list}
        self.assertEqual(tipos, {"sync_cobertura:mercado_libre|BEKURA": "ok",
                                 "sync_cobertura:mercado_libre|SANCORFASHION": "baja"})
        texto = [c.args[2] for c in av.call_args_list if c.args[1] == "baja"][0]
        self.assertIn("118 de 2549", texto)
        self.assertIn("delete from ops.process_log", ex.call_args.args[0])

    def test_apagado_no_hace_nada(self):
        with mock.patch.object(settings, "vigilante_sync_enabled", False), \
             mock.patch.object(vs, "cobertura") as cob:
            self.assertEqual(vs.revisar(), [])
        cob.assert_not_called()


class GanchoEnElSync(unittest.TestCase):
    """El sync anota lo que VISITÓ; el barrido de cierre no cuenta."""

    def test_ml_anota_visitados_sin_cierre_y_las_respuestas(self):
        lote = [{"sku": None, "ml_item_id": "MLM1"}, {"sku": None, "ml_item_id": "MLM2"},
                {"sku": None, "ml_item_id": "MLM3"},
                {"sku": None, "ml_item_id": "MLM9", "cierre": True}]

        async def _lote(cli, cuenta, token, limite):
            inv._UNIVERSO["mercado_libre|BEKURA"] = 2554
            return lote

        async def _leer(cli, item_id, token, cuenta):
            return None if item_id == "MLM2" else {"id": item_id, "status": "active",
                                                   "seller_custom_field": f"SKU-{item_id}"}

        async def _token(cuenta):
            return "tok"

        async def _upsert(rows):
            return len(rows)

        with mock.patch.multiple(settings, vigilante_sync_enabled=True, sync_desde_ml=True,
                                 ml_precio_venta=False), \
             mock.patch.object(inv, "_lote_desde_ml", _lote), \
             mock.patch.object(inv, "_leer_ml_item", _leer), \
             mock.patch.object(inv, "_respaldo_identidad_ml", return_value={}), \
             mock.patch.object(inv.meli, "access_token_async", _token), \
             mock.patch.object(inv, "_upsert_async", _upsert), \
             mock.patch.object(inv.vigilante_sync, "anotar_ronda") as anota:
            asyncio.run(inv.sincronizar_ml("BEKURA", 80))
        anota.assert_called_once_with("mercado_libre", "BEKURA", 2554,
                                      ["MLM1", "MLM2", "MLM3"], 2)

    def test_amazon_anota_lo_visitado_y_lo_que_amazon_contesto(self):
        class _SinRed:
            async def __aenter__(self):
                raise RuntimeError("sin red en la prueba")

            async def __aexit__(self, *a):
                return False

        async def _token():
            return "tok"

        async def _resto(pubs, skus, fba):
            return {"canal": "amazon", "ok": True, "actualizados": 2, "skus_leidos_en_vivo": 1}

        def _pubs(limite):
            inv._UNIVERSO["amazon|"] = 1836
            return [{"sku": "A-1"}, {"sku": "A-2"}]

        with mock.patch.object(settings, "vigilante_sync_enabled", True), \
             mock.patch.object(inv.amazon, "_access_token", _token), \
             mock.patch.object(inv.httpx, "AsyncClient", lambda **k: _SinRed()), \
             mock.patch.object(inv, "_pubs_amazon", _pubs), \
             mock.patch.object(inv, "_sincronizar_amazon_resto", _resto), \
             mock.patch.object(inv.vigilante_sync, "anotar_ronda") as anota:
            r = asyncio.run(inv.sincronizar_amazon(80))
        self.assertTrue(r["ok"])
        anota.assert_called_once_with("amazon", "", 1836, ["A-1", "A-2"], 1)

    def test_apagado_el_sync_no_anota(self):
        async def _resto(pubs, skus, fba):
            return {"canal": "amazon", "ok": True, "skus_leidos_en_vivo": 0}

        async def _token():
            return None                      # sin token: sale antes de todo

        with mock.patch.object(settings, "vigilante_sync_enabled", False), \
             mock.patch.object(inv.amazon, "_access_token", _token), \
             mock.patch.object(inv.vigilante_sync, "anotar_ronda") as anota:
            asyncio.run(inv.sincronizar_amazon(80))
        anota.assert_not_called()


if __name__ == "__main__":
    unittest.main()
