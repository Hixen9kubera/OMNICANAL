"""Pruebas del PESO Y MEDIDAS HEREDADOS DEL PADRE al costear una variante.

── QUÉ FIJAN ────────────────────────────────────────────
Una variante sin peso ni medidas propias toma las de la familia como paquete
(`wp_db.medidas_del_padre`) y el Estudio las enseña marcadas «del padre». Al
costear hay que separar dos cosas:

  · CALCULAR con ellas: el precio tiene que salir con el paquete que se va a
    publicar. Mandarlas en null dejaba el cálculo con lo que la variante tiene en
    costos (casi siempre nada → 0.5 kg, caja de 10 cm y Flete CBM en $0).
  · NO GUARDARLAS como propias: ni en costos_validados ni en WooCommerce. Si se
    guardan, la variante deja de heredar y el dato de la familia llega a las guías
    de Temu, que a propósito no heredan.

  1. `RecalcularCostos`: `medidas_heredadas` no es un override y sólo cuenta lo
     que es peso o medida.
  2. `_sync_woo_costo`: no manda `weight` si el peso es heredado, ni `dimensions`
     si lo es cualquiera de las tres medidas.
  3. `costos.recalcular`: calcula con lo que llega y guarda en costos_validados
     lo que ya había en las medidas heredadas (un NULL sigue NULL).
  4. `costos_recalcular` le pasa la misma lista a los dos.

── NO SE TOCA NADA REAL ─────────────────────────────────
Woo y kubera van simulados.

    cd backend && python -m unittest tests.test_medidas_heredadas_costeo -v
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
from services import costos  # noqa: E402

_SKU = "ORG-0798-NEG-CUA"
# Lo que sale del cálculo con el paquete del padre (12 kg, 60x40x40).
_FILA = {"costo_fee_envio": 180, "costo_comision": 60.5, "costo_unitario": 250,
         "precio_base": 900.0, "precio_sugerido": 765.0, "peso": 12.0,
         "largo": 60.0, "ancho": 40.0, "alto": 40.0, "ml_cat_id": None,
         "pct_comision": 0.15}
_LAS_CUATRO = {"peso", "largo", "ancho", "alto"}


class _Resp:
    status_code = 200

    def raise_for_status(self):
        return None


class _Woo:
    def __init__(self):
        self.puts: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def put(self, ruta, json=None, timeout=None):  # noqa: A002
        self.puts.append((ruta, json))
        return _Resp()


def _sincronizar(no_escribir) -> tuple[str, dict]:
    woo = _Woo()

    async def _producto(_sku):
        return {"wc_id": 222, "tipo": "variation", "padre_id": 111}

    with mock.patch.object(C.woocommerce, "obtener_producto_por_sku", _producto), \
         mock.patch.object(C.woocommerce, "_client", lambda: woo):
        ok = asyncio.run(C._sync_woo_costo(_SKU, dict(_FILA), None, no_escribir))
    assert ok
    return woo.puts[0]


class PeticionDelEstudio(unittest.TestCase):
    def test_no_es_override_y_solo_cuenta_peso_y_medidas(self):
        req = C.RecalcularCostos(peso=12, largo=60, ancho=40, alto=40,
                                 medidas_heredadas=["peso", "largo", "costo", "x"])
        self.assertNotIn("medidas_heredadas", req._overrides())
        self.assertEqual(req._overrides()["peso"], 12)  # se calcula con él
        self.assertEqual(req._no_guardar(), {"peso", "largo"})

    def test_sin_la_lista_es_como_antes(self):
        self.assertEqual(C.RecalcularCostos(peso=2)._no_guardar(), set())


class WooNoRecibeLoHeredado(unittest.TestCase):
    def test_paquete_heredado_no_manda_peso_ni_caja(self):
        ruta, payload = _sincronizar(_LAS_CUATRO)
        self.assertEqual(ruta, "/products/111/variations/222")
        self.assertNotIn("weight", payload)
        self.assertNotIn("dimensions", payload)
        # El precio sí se escribe: es lo que se guardó.
        self.assertEqual(payload["sale_price"], "765.00")

    def test_solo_el_peso_heredado_manda_la_caja_propia(self):
        _, payload = _sincronizar({"peso"})
        self.assertNotIn("weight", payload)
        self.assertEqual(payload["dimensions"]["length"], "60.00")

    def test_una_medida_heredada_frena_la_caja_entera(self):
        _, payload = _sincronizar({"alto"})
        self.assertEqual(payload["weight"], "12.000")
        self.assertNotIn("dimensions", payload)

    def test_sin_heredadas_como_antes(self):
        _, payload = _sincronizar(frozenset())
        self.assertEqual(payload["weight"], "12.000")
        self.assertEqual(payload["dimensions"]["height"], "40.00")


class CostosValidadosConservaLoSuyo(unittest.TestCase):
    def _recalcular(self, conservar, guardado):
        calc = {**_FILA, "sku": _SKU, "costo_producto": 250.0, "costo_cbm": 0.0,
                "ml_cat_id": "MLM1"}
        validados: list[dict] = []
        finales: list[dict] = []

        def _finales(sku, base, pricing, cat):
            finales.append(base)
            return {**base, **pricing}

        with mock.patch.object(costos, "computar", lambda *a, **k: dict(calc)), \
             mock.patch.object(costos, "_guardar_validados",
                               lambda sku, base: validados.append(base)), \
             mock.patch.object(costos, "_guardar_finales", _finales), \
             mock.patch.object(costos, "_log_costo", lambda *a, **k: None), \
             mock.patch.object(costos.costing_write, "activo", lambda: True), \
             mock.patch.object(costos.costing_read, "validados", lambda _sku: guardado):
            fila = costos.recalcular(_SKU, {"peso": 12}, conservar=conservar)
        return fila, validados[0], finales[0]

    def test_lo_heredado_no_se_guarda_y_el_null_sigue_null(self):
        guardado = {"peso": None, "largo": None, "ancho": None, "alto": None}
        fila, validados, finales = self._recalcular(_LAS_CUATRO, guardado)
        for k in _LAS_CUATRO:
            self.assertIsNone(validados[k], k)
        # El costo y el precio sí son los calculados con el paquete del padre.
        self.assertEqual(validados["costo_unitario"], 250)
        self.assertEqual(fila["precio_sugerido"], 765.0)
        self.assertEqual(finales["peso"], 12.0)

    def test_solo_el_peso_conserva_el_suyo(self):
        guardado = {"peso": 0.3, "largo": 5, "ancho": 5, "alto": 5}
        _, validados, _ = self._recalcular({"peso"}, guardado)
        self.assertEqual(validados["peso"], 0.3)
        self.assertEqual(validados["largo"], 60.0)  # la caja sí es de la variante

    def test_sin_conservar_no_lee_nada_y_es_como_antes(self):
        def _no_leer(_sku):
            raise AssertionError("no debía leer costos_validados")

        calc = {**_FILA, "sku": _SKU, "costo_producto": 250.0, "costo_cbm": 0.0}
        validados: list[dict] = []
        with mock.patch.object(costos, "computar", lambda *a, **k: dict(calc)), \
             mock.patch.object(costos, "_guardar_validados",
                               lambda sku, base: validados.append(base)), \
             mock.patch.object(costos, "_guardar_finales",
                               lambda sku, base, pricing, cat: {**base, **pricing}), \
             mock.patch.object(costos, "_log_costo", lambda *a, **k: None), \
             mock.patch.object(costos.costing_read, "validados", _no_leer):
            costos.recalcular(_SKU, {"peso": 12})
        self.assertEqual(validados[0]["peso"], 12.0)


class ElEndpointPasaLaLista(unittest.TestCase):
    def test_recalcular_y_woo_reciben_lo_mismo(self):
        visto: dict = {}

        def _recalcular(sku, ov, incluir_envio, margen, cuenta, auto_cbm, conservar=()):
            visto["overrides"] = ov
            visto["conservar"] = set(conservar)
            return dict(_FILA)

        async def _hilo(fn, *a, **k):
            return fn(*a, **k)

        async def _sync(sku, fila, cat, no_escribir=frozenset()):
            visto["no_escribir"] = set(no_escribir)
            return True

        req = C.RecalcularCostos(peso=12, largo=60, ancho=40, alto=40, costo_producto=250,
                                 medidas_heredadas=["peso", "largo", "ancho", "alto"])
        with mock.patch.object(C.costos, "recalcular", _recalcular), \
             mock.patch.object(C, "run_in_threadpool", _hilo), \
             mock.patch.object(C, "_sync_woo_costo", _sync):
            r = asyncio.run(C.costos_recalcular(_SKU, req))
        self.assertTrue(r["sincronizado_woo"])
        self.assertEqual(visto["overrides"]["peso"], 12)
        self.assertEqual(visto["conservar"], _LAS_CUATRO)
        self.assertEqual(visto["no_escribir"], _LAS_CUATRO)


if __name__ == "__main__":
    unittest.main()
