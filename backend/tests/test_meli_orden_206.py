"""`meli.obtener_orden` y el 206 (orden incompleta) de Mercado Libre.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Un 206 con estado e ítems SE USA.** Hasta el 26-sep se tiraba igual que un
   403 ("es de la otra cuenta") y la venta no se volvía pedido nunca: la orden
   2000018309931922 agotó así sus 10 reintentos.
2. **Un 206 sin estado o sin ítems NO se usa**: sin eso no hay pedido que armar.
3. El 200 y el 403 de la otra cuenta siguen igual.

Sin red.

    cd backend && python -m unittest tests.test_meli_orden_206 -v
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from services import meli

_ORDEN = {"id": 2000018309931922, "status": "cancelled", "total_amount": 1619.73,
          "paid_amount": 0.0, "currency_id": "MXN", "shipping": {"id": None},
          "payments": [{"status": "rejected"}], "buyer": {"id": 1},
          "order_items": [{"item": {"id": "MLM5793156390", "seller_sku": "CAM-0030-IND"},
                           "quantity": 1, "unit_price": 1619.73}]}


def _cliente(respuestas: dict):
    """AsyncClient falso: contesta según (token, ruta)."""
    class Cliente:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, ruta, headers=None):
            token = (headers or {}).get("Authorization", "").split()[-1]
            codigo, cuerpo = respuestas.get((token, ruta), (404, {}))
            return mock.Mock(status_code=codigo, json=lambda c=cuerpo: c)
    return Cliente


class Orden206(unittest.TestCase):
    def setUp(self):
        meli._token_cache.clear()   # el caché de 60 s de otra prueba contestaría
        self.addCleanup(meli._token_cache.clear)
        mock.patch.object(meli, "_access_token", side_effect=lambda c: f"tok-{c}").start()
        self.addCleanup(mock.patch.stopall)

    def correr(self, respuestas):
        with mock.patch("httpx.AsyncClient", _cliente(respuestas)):
            return asyncio.run(meli.obtener_orden("2000018309931922"))

    def test_206_con_estado_e_items_se_usa_con_su_cuenta(self):
        o = self.correr({("tok-BEKURA", "/orders/2000018309931922"): (403, {}),
                         ("tok-SANCORFASHION", "/orders/2000018309931922"): (206, _ORDEN)})
        self.assertIsNotNone(o)
        self.assertEqual((o["cuenta"], o["estado"]), ("SANCORFASHION", "cancelled"))
        self.assertEqual(o["items"][0]["sku"], "CAM-0030-IND")

    def test_206_sin_items_no_se_usa(self):
        cuerpo = dict(_ORDEN, order_items=[])
        o = self.correr({("tok-SANCORFASHION", "/orders/2000018309931922"): (206, cuerpo)})
        self.assertIsNone(o)

    def test_206_sin_estado_no_se_usa(self):
        cuerpo = dict(_ORDEN, status=None)
        o = self.correr({("tok-SANCORFASHION", "/orders/2000018309931922"): (206, cuerpo)})
        self.assertIsNone(o)

    def test_200_sigue_igual(self):
        o = self.correr({("tok-BEKURA", "/orders/2000018309931922"): (200, dict(_ORDEN, status="paid"))})
        self.assertEqual((o["cuenta"], o["estado"]), ("BEKURA", "paid"))

    def test_403_en_las_dos_es_none(self):
        o = self.correr({("tok-BEKURA", "/orders/2000018309931922"): (403, {}),
                         ("tok-SANCORFASHION", "/orders/2000018309931922"): (403, {})})
        self.assertIsNone(o)


if __name__ == "__main__":
    unittest.main()
