"""Pruebas de las visitas de RIVALES como LEGACY (v0.664.0).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. `/api/competencia/estado` manda `visitas_rivales` con `legacy: true` y la
   fecha REAL de la última medición: la más reciente de búsqueda y ranking.
2. Sin fechas (BD caída o sin remoto) sigue diciendo legacy, con fechas vacías:
   nunca inventa una.
3. La consulta deja fuera nuestras publicaciones (`es_nuestro`): sus visitas
   siguen vivas y moverían la fecha a una captura sin una sola visita ajena.
4. `limites.visitas` ya no dice que las visitas funcionan para cualquier
   publicación.
5. «Visitas del nicho» (`visitas_mercado`) suma SOLO rivales. En producción
   había 4 subcategorías cuya única visita del top era nuestra (captura del
   16-oct): el panel las pintaba como visitas del nicho, legacy y con una fecha
   en la que no se midió ninguna ajena.

Sin red ni BD.

    cd backend && python -m unittest tests.test_competencia_visitas_legacy -v
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import competencia as RC  # noqa: E402
from services import competencia_store as ST  # noqa: E402
from services import competencia_supabase as CS  # noqa: E402


class EstadoVisitasRivales(unittest.TestCase):
    def _estado(self, fechas):
        with mock.patch.object(RC.competencia_store, "visitas_rivales_legacy", return_value=fechas), \
             mock.patch.object(RC.competencia_store, "disponible", return_value=True), \
             mock.patch.object(RC.competencia_scraper, "disponible", return_value=True), \
             mock.patch.object(RC.competencia_scraper, "costo_medido_por_pagina", return_value=None):
            return RC.estado()

    def test_la_ultima_medicion_es_la_mas_reciente_de_las_dos(self):
        busqueda = datetime(2026, 9, 29, 18, 11, tzinfo=timezone.utc)
        ranking = datetime(2026, 10, 1, 15, 1, tzinfo=timezone.utc)
        v = self._estado({"busqueda": busqueda, "ranking": ranking, "denegado_en": None})["visitas_rivales"]
        self.assertIs(v["legacy"], True)
        self.assertEqual(v["ultima_medicion"], ranking.isoformat())
        self.assertEqual(v["busqueda"], busqueda.isoformat())
        self.assertIsNone(v["denegado_en"])
        self.assertIn("ML dejó de darlas", v["motivo"])

    def test_sin_fechas_sigue_siendo_legacy(self):
        v = self._estado({})["visitas_rivales"]
        self.assertIs(v["legacy"], True)
        self.assertIsNone(v["ultima_medicion"])

    def test_el_limite_ya_no_promete_visitas_ajenas(self):
        texto = self._estado({})["limites"]["visitas"]
        self.assertNotIn("cualquier publicación", texto)
        self.assertIn("403", texto)


class ConsultaSinLasNuestras(unittest.TestCase):
    def test_excluye_es_nuestro_en_las_dos_tablas(self):
        visto = {}

        def fetch_all(sql, params):
            visto["sql"], visto["params"] = sql, params
            return [{"busqueda": None, "ranking": None, "denegado_en": None}]

        with mock.patch.object(CS.supabase_db, "fetch_all", fetch_all):
            CS.visitas_rivales_legacy()
        self.assertEqual(visto["sql"].count("NOT coalesce(es_nuestro, false)"), 2)
        self.assertIn("visitas_ajenas", visto["sql"])
        self.assertEqual(visto["params"], ("mercado_libre",))


def _sub(top):
    fila_sku = {"sku": "LAM-0001-NEG", "raiz_id": "MLM1574", "raiz_nombre": "Hogar",
                "categoria_id": "MLM1718", "categoria_nombre": "Lámparas",
                "tiendas": [{"canal": "mercado_libre", "precio": 249.0,
                             "estado": "active", "visitas_30d": 10}],
                "unidades_30d": 3}
    rk = {("MLM1718", "hoja"): top}
    with mock.patch.object(ST, "tabla", return_value=[fila_sku]), \
            mock.patch.object(ST, "rankings_por_categoria", return_value=rk), \
            mock.patch.object(ST, "conteo_terminos", return_value={}), \
            mock.patch.object(ST, "stock_por_sku", return_value={}), \
            mock.patch.object(ST, "movimiento_del_top", return_value={}):
        return ST.vista()[0]["subcategorias"][0]


def _fila(i, visitas, nuestro=False):
    return {"posicion": i, "titulo": f"Lámpara solar {i}", "seller": f"TIENDA{i}",
            "precio": 200.0 + i, "vendidos": 100, "visitas_30d": visitas,
            "capturado_en": "2026-10-16T12:00:00+00:00", "item_categoria_id": "MLM1718",
            "id_pagina": f"MLM{i}", "sku_nuestro": "LAM-0001-NEG" if nuestro else None,
            "es_nuestro": 1 if nuestro else 0}


class VisitasDelNicho(unittest.TestCase):
    def test_solo_la_nuestra_no_es_visita_del_nicho(self):
        sub = _sub([_fila(1, None), _fila(2, 2058, nuestro=True), _fila(3, None)])
        self.assertIsNone(sub["visitas_mercado"])

    def test_suma_solo_rivales(self):
        sub = _sub([_fila(1, 100), _fila(2, 2058, nuestro=True), _fila(3, 50)])
        self.assertEqual(sub["visitas_mercado"], 150)


if __name__ == "__main__":
    unittest.main()
