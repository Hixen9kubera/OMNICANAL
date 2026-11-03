"""Pruebas de LA BAJA DEL CATÁLOGO en el Publicador (PLAN MAESTRO 3.2, diseño de Eduardo del 2-nov).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Por omisión el Publicador oculta lo archivado** (General, vista productos): se quita de la página, padres y
   variantes, y viaja `archivados_ocultos`. Con kubera sin contestar (o sin la 0080) no se oculta nada.
2. **Las demás vistas y `archivados=todos` no ocultan.**
3. **`archivados=vivos`** (dados de baja y todavía publicados) se resuelve ANTES de listar, se cruza con la caja de
   SKUs, vacío da 0 sin llamar a Woo, y una falla da 503 (nunca un vacío falso). **En General se pinta desde kubera**
   (P1 de la verificación de 8c7dd6bb): Woo no lista la papelera ni lo que ya borró. No se combina con `etapa`.
4. **`core_read`**: `archivados_de` devuelve None si no puede leer; `archivados_vivos` lee la vista y LANZA.

Sin red ni base:
    cd backend && python -m unittest tests.test_publicador_archivados -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import productos as ruta_prod  # noqa: E402
from services import core_read, inventario_flujo as invf  # noqa: E402

PAGINA = [{"sku": "PAD-0001", "nombre": "padre", "wc_id": 1,
           "variantes": [{"sku": "PAD-0001-ROJ", "nombre": "rojo", "wc_id": 2},
                         {"sku": "PAD-0001-AZL", "nombre": "azul", "wc_id": 3}]},
          {"sku": "SUELTO-0002", "nombre": "suelto", "wc_id": 4, "variantes": []},
          {"sku": "BAJA-0003", "nombre": "dado de baja", "wc_id": 5, "variantes": []}]


class _Base(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(ruta_prod.router)
        self.c = TestClient(app)
        self.woo_kw: list[dict] = []

        async def _woo(**kw):
            self.woo_kw.append(kw)
            return ([{**i, "variantes": [dict(v) for v in i["variantes"]]} for i in PAGINA], 3, 1)

        parches = [
            mock.patch.object(ruta_prod.woocommerce, "listar_productos", side_effect=_woo),
            mock.patch.object(ruta_prod.woocommerce, "imagenes_por_wc_id", new=mock.AsyncMock(return_value={})),
            mock.patch.object(ruta_prod.costing_read, "revisados_por_sku", return_value={}),
            mock.patch.object(ruta_prod.costing_read, "validados_de", return_value={}),
            mock.patch.object(ruta_prod.channel_read, "rutas_de", return_value={}),
            mock.patch.object(ruta_prod.channel_read, "hijos_por_wc_id", return_value={}),
            mock.patch.object(ruta_prod.inventario, "leer_inventario", return_value={}),
            mock.patch.object(ruta_prod.presencia, "presencia_por_sku", return_value={}),
            mock.patch.object(ruta_prod.wp_db, "disponible", return_value=True),
            mock.patch.object(invf, "foto_para_peticion", return_value=invf.FOTO_APAGADA),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)

    def _get(self, **params):
        params.setdefault("canal", "general")
        return self.c.get("/api/productos", params=params)

    @staticmethod
    def _skus(r):
        j = r.json()
        return [i["sku"] for i in j["items"]], [v["sku"] for i in j["items"] for v in (i.get("variantes") or [])]


class OcultaPorOmision(_Base):
    def test_el_publicador_quita_padres_y_variantes_archivados(self):
        with mock.patch.object(core_read, "archivados_de", return_value={"BAJA-0003", "PAD-0001-AZL"}) as m:
            r = self._get()
        self.assertEqual(r.status_code, 200, r.text)
        padres, variantes = self._skus(r)
        self.assertEqual(padres, ["PAD-0001", "SUELTO-0002"])
        self.assertEqual(variantes, ["PAD-0001-ROJ"])
        self.assertEqual(r.json()["archivados_ocultos"], 2)
        pedidos = set(m.call_args.args[0])
        self.assertEqual(pedidos, {"PAD-0001", "PAD-0001-ROJ", "PAD-0001-AZL", "SUELTO-0002", "BAJA-0003"})

    def test_la_llave_es_citext(self):
        with mock.patch.object(core_read, "archivados_de", return_value={"BAJA-0003"}):
            with mock.patch.object(ruta_prod.woocommerce, "listar_productos", new=mock.AsyncMock(
                    return_value=([{"sku": "baja-0003", "nombre": "x", "wc_id": 5, "variantes": []}], 1, 1))):
                r = self._get()
        self.assertEqual(self._skus(r)[0], [])

    def test_sin_kubera_no_oculta_nada(self):
        with mock.patch.object(core_read, "archivados_de", return_value=None):
            r = self._get()
        self.assertEqual(len(self._skus(r)[0]), 3)
        self.assertEqual(r.json()["archivados_ocultos"], 0)

    def test_otras_vistas_y_todos_no_ocultan(self):
        for params in ({"vista": "crear"}, {"vista": "omnicanal"}, {"archivados": "todos"}):
            with mock.patch.object(core_read, "archivados_de", return_value={"BAJA-0003"}) as m:
                r = self._get(**params)
            self.assertEqual(len(self._skus(r)[0]), 3, params)
            m.assert_not_called()

    def test_valor_invalido_da_422(self):
        self.assertEqual(self._get(archivados="si").status_code, 422)


class ArchivadosVivos(_Base):
    FICHAS = [{"sku": "BAJA-0003", "nombre": "Silla rota", "wc_id": 5, "status": "trash"},
              {"sku": "OTRO-0009", "nombre": "Mesa", "wc_id": None, "status": "publish"}]

    def test_general_se_pinta_desde_kubera_aunque_woo_no_lo_liste(self):
        with mock.patch.object(core_read, "archivados_vivos", return_value=["BAJA-0003", "OTRO-0009"]), \
                mock.patch.object(core_read, "fichas_de", return_value=self.FICHAS) as f:
            r = self._get(archivados="vivos")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.woo_kw, [])                  # Woo no trae la papelera ni lo que borró
        self.assertEqual(f.call_args.args[0], ["BAJA-0003", "OTRO-0009"])
        self.assertEqual(self._skus(r)[0], ["BAJA-0003", "OTRO-0009"])
        j = r.json()
        self.assertEqual((j["paginacion"]["total"], j["items"][0]["estado"]), (2, "trash"))
        self.assertEqual(j["archivados_ocultos"], 0)       # con el filtro pedido, lo archivado NO se oculta

    def test_se_cruza_con_la_caja_de_skus(self):
        with mock.patch.object(core_read, "archivados_vivos", return_value=["BAJA-0003", "OTRO-0009"]), \
                mock.patch.object(core_read, "fichas_de", return_value=self.FICHAS[1:]) as f:
            self._get(archivados="vivos", skus="otro-0009, NADA-1")
        self.assertEqual(f.call_args.args[0], ["OTRO-0009"])

    def test_busqueda_y_paginas(self):
        fichas = [{"sku": f"BAJA-{i:04d}", "nombre": "silla" if i % 2 else "mesa", "wc_id": i, "status": "trash"}
                  for i in range(5)]
        with mock.patch.object(core_read, "archivados_vivos", return_value=[f["sku"] for f in fichas]), \
                mock.patch.object(core_read, "fichas_de", return_value=fichas):
            r = self._get(archivados="vivos", search="SILLA", per_page=1, page=2)
        j = r.json()
        self.assertEqual((j["paginacion"]["total"], j["paginacion"]["total_pages"]), (2, 2))
        self.assertEqual([i["sku"] for i in j["items"]], ["BAJA-0003"])

    def test_no_se_combina_con_etapa(self):
        self.assertEqual(self._get(archivados="vivos", etapa="en_full").status_code, 422)

    def test_ninguno_da_cero_sin_llamar_a_woo(self):
        with mock.patch.object(core_read, "archivados_vivos", return_value=[]):
            r = self._get(archivados="vivos")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["paginacion"]["total"], 0)
        self.assertEqual(self.woo_kw, [])

    def test_si_kubera_falla_da_503(self):
        with mock.patch.object(core_read, "archivados_vivos", side_effect=RuntimeError("caído")):
            r = self._get(archivados="vivos")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.woo_kw, [])

    def test_vale_en_los_marketplaces(self):
        with mock.patch.object(core_read, "archivados_vivos", return_value=[]):
            r = self._get(canal="mercado_libre", archivados="vivos")
        self.assertEqual((r.status_code, r.json()["paginacion"]["total"]), (200, 0))


class CoreRead(unittest.TestCase):
    def test_archivados_de_en_lotes_y_mayusculas(self):
        llamadas = []

        def _fa(sql, p=None):
            llamadas.append((sql, p))
            return [{"sku": s.upper()} for s in p[0] if s.endswith("x")]
        with mock.patch.object(core_read.sdb, "fetch_all", side_effect=_fa):
            r = core_read.archivados_de([f"s{i}x" if i % 2 else f"s{i}" for i in range(1000)] + ["", None])
        self.assertEqual(len(llamadas), 2)                       # 800 + 200
        self.assertIn("archivado_at is not null", llamadas[0][0])
        self.assertEqual(len(r), 500)
        self.assertIn("S1X", r)

    def test_archivados_de_sin_columna_da_none_y_avisa_una_vez(self):
        with mock.patch.object(core_read.sdb, "fetch_all", side_effect=RuntimeError("column archivado_at does not exist")),                 mock.patch.object(core_read, "_baja_avisada", False),                 mock.patch.object(core_read, "log") as lg:
            self.assertIsNone(core_read.archivados_de(["A"]))
            self.assertIsNone(core_read.archivados_de(["B"]))
        self.assertEqual(lg.warning.call_count, 1)
        self.assertEqual(lg.debug.call_count, 1)

    def test_tras_una_lectura_buena_la_siguiente_caida_vuelve_a_avisar(self):   # P4
        with mock.patch.object(core_read, "_baja_avisada", False), mock.patch.object(core_read, "log") as lg:
            with mock.patch.object(core_read.sdb, "fetch_all", side_effect=RuntimeError("x")):
                core_read.archivados_de(["A"])
            with mock.patch.object(core_read.sdb, "fetch_all", return_value=[]):
                core_read.archivados_de(["A"])
            with mock.patch.object(core_read.sdb, "fetch_all", side_effect=RuntimeError("y")):
                core_read.archivados_de(["A"])
        self.assertEqual(lg.warning.call_count, 2)

    def test_fichas_de_en_mayusculas_y_por_sku(self):
        with mock.patch.object(core_read.sdb, "fetch_all", return_value=[
                {"sku": "B-2", "nombre": "b", "wc_id": None, "status": "trash"},
                {"sku": "A-1", "nombre": "a", "wc_id": 1, "status": "publish"}]) as m:
            r = core_read.fichas_de(["b-2", "a-1", ""])
        self.assertEqual([f["sku"] for f in r], ["A-1", "B-2"])
        self.assertIn("upper(sku::text)", m.call_args.args[0])
        self.assertEqual(m.call_args.args[1], (["b-2", "a-1"],))

    def test_archivados_vivos_lee_la_vista_y_lanza(self):
        with mock.patch.object(core_read.sdb, "fetch_all", return_value=[{"sku": "A"}]) as m:
            self.assertEqual(core_read.archivados_vivos(), ["A"])
        self.assertIn("core.products_ciclo_v", m.call_args.args[0])
        self.assertIn("situacion = 'incoherente'", m.call_args.args[0])
        with mock.patch.object(core_read.sdb, "fetch_all", side_effect=RuntimeError("x")):
            with self.assertRaises(RuntimeError):
                core_read.archivados_vivos()


if __name__ == "__main__":
    unittest.main()
