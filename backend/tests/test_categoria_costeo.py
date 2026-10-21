"""Pruebas de la CATEGORÍA ELEGIDA EN EL COSTEO y del mensaje de ME1.

── QUÉ FIJAN ────────────────────────────────────────────
JUGU-0085-PLA, 20-oct-2026: Cinthya eligió «Fundas para Penes» y guardó el costo.
El Estudio pintaba esa categoría, pero el guardado de costos sólo escribía
`ml_category_id` (la del predictor) y dejaba intacta `ml_categoria_id` (la del
panel, la que MANDA al publicar), que seguía en «Comidas Preparadas». ML rechazó
por un atributo de esa categoría y el panel lo tradujo como «categoría sin
Mercado Envíos 2», que era falso.

  1. Con categoría en la PETICIÓN, `_sync_woo_costo` escribe `ml_categoria_id` +
     niveles y registra la elección en kubera.
  2. Sin categoría en la petición, se comporta como antes (llave del predictor,
     sin tocar `ml_categoria_id` ni kubera).
  3. `_traducir_error_ml`: si ML trae un error real junto al aviso de ME1, se
     muestra el error real; si ME1 es lo único, la traducción de siempre.

── NO SE TOCA NADA REAL ─────────────────────────────────
Woo, ML y kubera van simulados.

    cd backend && python -m unittest tests.test_categoria_costeo -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from routers import crear as C  # noqa: E402
from services import publicar_ready as P  # noqa: E402

_CAT = {"id": "MLM46606", "name": "Fundas para Penes", "settings": {"catalog_domain": "MLM-X"},
        "path_from_root": [{"id": "MLM1953", "name": "Otras Categorías"},
                           {"id": "MLM46606", "name": "Fundas para Penes"}]}
_FILA = {"costo_fee_envio": 32, "costo_comision": 23.68, "costo_unitario": 44.43,
         "precio_base": 167.7, "precio_sugerido": 140.87, "peso": 0.1,
         "largo": 10, "ancho": 5, "alto": 5, "ml_cat_id": "MLM46606"}


class _Resp:
    status_code = 200

    def raise_for_status(self):
        return None


class _Woo:
    def __init__(self):
        self.puts: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def put(self, ruta, json=None, timeout=None):  # noqa: A002
        self.puts.append(json)
        return _Resp()


def _correr(cat_elegida):
    woo = _Woo()
    registrados: list[tuple] = []

    async def _producto(_sku):
        return {"wc_id": 113750, "tipo": "simple"}

    async def _meta_panel(cat):
        return ([{"key": "ml_categoria_id", "value": cat},
                 {"key": "ml_categoria_niveles", "value": "[...]"}], _CAT)

    async def _meta_predictor(cat):
        return [{"key": "ml_category_id", "value": cat}]

    async def _hilo(fn, *a):
        return fn(*a)

    with mock.patch.object(C.woocommerce, "obtener_producto_por_sku", _producto), \
         mock.patch.object(C.woocommerce, "_client", lambda: woo), \
         mock.patch.object(C, "_meta_categoria_panel", _meta_panel), \
         mock.patch.object(C, "_categoria_ml_meta", _meta_predictor), \
         mock.patch.object(C, "run_in_threadpool", _hilo), \
         mock.patch.object(C.categorias_write, "registrar",
                           lambda *a: registrados.append(a)):
        ok = asyncio.run(C._sync_woo_costo("JUGU-0085-PLA", dict(_FILA), cat_elegida))
    llaves = {m["key"]: m["value"] for m in woo.puts[0]["meta_data"]}
    return ok, llaves, registrados


class CategoriaDelCosteo(unittest.TestCase):
    def test_con_categoria_en_la_peticion_manda(self):
        ok, llaves, registrados = _correr("MLM46606")
        self.assertTrue(ok)
        self.assertEqual(llaves.get("ml_categoria_id"), "MLM46606")
        self.assertIn("ml_categoria_niveles", llaves)
        self.assertEqual(len(registrados), 1)
        self.assertEqual(registrados[0][:2], (113750, "MLM46606"))

    def test_sin_categoria_en_la_peticion_como_antes(self):
        ok, llaves, registrados = _correr(None)
        self.assertTrue(ok)
        self.assertNotIn("ml_categoria_id", llaves)
        self.assertEqual(llaves.get("ml_category_id"), "MLM46606")
        self.assertEqual(registrados, [])


class MensajeME1(unittest.TestCase):
    _ME1 = "NEEDS_MANUAL_CONFIG: ME1_INACTIVO (activar Mercado Envíos 1 en dashboard ML)"

    def test_error_real_junto_al_aviso_de_me1(self):
        resp = {"cause": [
            {"type": "warning", "code": "item.attribute.missing_catalog_required", "message": "aviso"},
            {"type": "error", "code": "item.attributes.missing_required",
             "message": "The attributes [PRODUCT_NAME] are required for category MLM194402"},
            {"type": "warning", "code": "shipping.lost_me1_by_user", "message": "User has not mode me1"},
        ]}
        msg = P._traducir_error_ml(self._ME1, {"ml_categoria_id": "MLM194402"}, resp)
        self.assertIn("PRODUCT_NAME", msg)
        self.assertNotIn("CATEGORIA_SIN_ME2", msg)

    def test_me1_solo_conserva_la_traduccion(self):
        resp = {"cause": [{"type": "error", "code": "shipping.lost_me1_by_user", "message": "m"}]}
        msg = P._traducir_error_ml(self._ME1, {"ml_categoria_id": "MLM5527"}, resp)
        self.assertIn("CATEGORIA_SIN_ME2", msg)

    def test_otros_errores_no_se_tocan(self):
        self.assertEqual(P._traducir_error_ml("GTIN_INVALIDO: x", {}, {"cause": []}),
                         "GTIN_INVALIDO: x")


if __name__ == "__main__":
    unittest.main()
