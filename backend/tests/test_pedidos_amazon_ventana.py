"""Pruebas de la VENTANA del sondeo de Amazon (PLAN MAESTRO 2.11/F2.3, 28-oct-2026).

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
1. **Con 0 (por omisión), como siempre**: la marca de agua menos 10 min.
2. **Con HORAS, nunca después de `ahora − HORAS`**: en un día normal la marca va pegada a
   «ahora» y la ventana fija re-mira lo de atrás.
3. **Tras una caída larga manda la marca**: el 27-oct la marca del 21-oct recuperó solos los
   5 días del LWA vencido; una ventana fija sola los habría perdido.
4. **Sin marca**, 7 días atrás (como siempre).

Sin red ni base:
    cd backend && python -m unittest tests.test_pedidos_amazon_ventana -v
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from services import orders_write, pedidos_amazon  # noqa: E402

AHORA = datetime(2026, 10, 28, 18, 0)


class RelojFijo(datetime):
    @classmethod
    def now(cls, tz=None):
        return AHORA.replace(tzinfo=timezone.utc) if tz else AHORA


def desde(marca, horas):
    with mock.patch.object(orders_write, "ultimo_actualizado", return_value=marca), \
         mock.patch.object(settings, "pedidos_amazon_ventana_horas", horas), \
         mock.patch.object(pedidos_amazon, "datetime", RelojFijo):
        return pedidos_amazon._desde()


class Ventana(unittest.TestCase):
    def test_por_omision_la_marca_menos_el_margen(self):
        self.assertEqual(desde(AHORA - timedelta(minutes=3), 0), "2026-10-28T17:47:00Z")

    def test_con_horas_nunca_despues_de_ahora_menos_horas(self):
        self.assertEqual(desde(AHORA - timedelta(minutes=3), 24), "2026-10-27T18:00:00Z")

    def test_tras_una_caida_larga_manda_la_marca(self):
        marca = datetime(2026, 10, 21, 2, 41)
        self.assertEqual(desde(marca, 24), "2026-10-21T02:31:00Z")

    def test_sin_marca_siete_dias(self):
        self.assertEqual(desde(None, 24), "2026-10-21T17:50:00Z")



class Cuota(unittest.TestCase):
    """2.11: con la ventana encendida hay que poder ver cuánto se acerca el sondeo al límite de
    getOrders. `_get` cuenta las llamadas (también el reintento), los 429 y el límite anunciado."""

    def test_cuenta_llamadas_429_y_limite(self):
        import asyncio

        class R:
            def __init__(self, code):
                self.status_code, self.headers = code, {"x-amzn-RateLimit-Limit": "0.0167"}

            def raise_for_status(self):
                pass

            def json(self):
                return {"payload": {"Orders": []}}

        respuestas = [R(429), R(200), R(200)]
        cli = mock.Mock(get=mock.AsyncMock(side_effect=lambda *a, **k: respuestas.pop(0)))
        cuota: dict = {}
        with mock.patch.object(pedidos_amazon.asyncio, "sleep", mock.AsyncMock()):
            asyncio.run(pedidos_amazon._get(cli, "t", "/orders/v0/orders", {}, cuota, "orders"))
            asyncio.run(pedidos_amazon._get(cli, "t", "/orders/v0/orders", {}, cuota, "orders"))
        self.assertEqual((cuota["orders"], cuota["orders_429"], cuota["orders_limite"]), (3, 1, "0.0167"))

    def test_el_tope_de_paginas_avisa(self):
        # #9: el tope cortaba sin avisar.
        import asyncio

        from services import alertas, amazon

        class R:
            status_code, headers = 200, {}

            def raise_for_status(self):
                pass

            def json(self):
                return {"payload": {"Orders": [], "NextToken": "sigue"}}

        cli = mock.MagicMock()
        cli.get = mock.AsyncMock(return_value=R())
        cli.__aenter__ = mock.AsyncMock(return_value=cli)
        cli.__aexit__ = mock.AsyncMock(return_value=False)
        with mock.patch.object(pedidos_amazon.httpx, "AsyncClient", return_value=cli), \
             mock.patch.object(amazon, "_access_token", mock.AsyncMock(return_value="t")), \
             mock.patch.object(pedidos_amazon, "_desde", return_value="2026-10-27T18:00:00Z"), \
             mock.patch.object(orders_write, "estados_wc", return_value={}), \
             mock.patch.object(pedidos_amazon.asyncio, "sleep", mock.AsyncMock()), \
             mock.patch.object(alertas, "avisar", return_value=True) as avisar:
            r = asyncio.run(pedidos_amazon.revisar())
        self.assertTrue(r["cuota"]["tope_paginas"])
        self.assertEqual(r["cuota"]["orders"], pedidos_amazon._MAX_PAGINAS)
        self.assertEqual(avisar.call_args.args[0], "amazon_tope_paginas")

    def test_sin_cuota_como_siempre(self):
        import asyncio

        class R:
            status_code, headers = 200, {}

            def raise_for_status(self):
                pass

            def json(self):
                return {"payload": {"x": 1}}
        cli = mock.Mock(get=mock.AsyncMock(return_value=R()))
        self.assertEqual(asyncio.run(pedidos_amazon._get(cli, "t", "/r", {})), {"x": 1})


if __name__ == "__main__":
    unittest.main()
