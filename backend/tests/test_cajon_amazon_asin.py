"""La tarjeta de Amazon del cajón lleva el ASIN de kubera (29-sep-2026).

── POR QUÉ ────────────────────────────────────────────────────────────────────
`GET /api/productos/{sku}` armaba la tarjeta de Amazon con `amazon.por_sku`, que
lee la bitácora del publicador (`amazon_progress`, MySQL). Su columna `asin` está
vacía en las 1,710 publicaciones, así que la tarjeta nunca traía id: el pie con el
ASIN y el botón «Ver publicación» no salían en ningún producto (caso
CAM-0030-MAT, comprable en Amazon y sin enlace en el panel). El ASIN vive en
`channel.listings.listing_id` y llega al detalle en `inventario.leer_inventario`.

    cd backend && python -m unittest tests.test_cajon_amazon_asin -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import productos as ruta_prod  # noqa: E402

SKU = "CAM-0030-MAT"
ASIN = "B0HJ463BQB"
# Lo que devuelve hoy la bitácora para ese SKU: publicado, sin ASIN.
BITACORA = {"sku": SKU, "publicado": True, "item_id": None, "url": None,
            "precio": 1500.0, "stock": 7, "full": None, "full_label": "FBA",
            "categoria_id": "SUNGLASSES", "estado": "PUBLISHED",
            "categoria_path": [{"id": "SUNGLASSES", "nombre": "SUNGLASSES"}]}
# La fila de kubera (`channel_read._SEL`: listing_id viaja como item_id).
KUBERA = {"amazon|": {"item_id": ASIN, "precio": 1998.26, "stock_real": 20,
                      "stock_fba": None, "situacion": "BUYABLE", "es_full": 0}}


class TarjetaAmazon(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(ruta_prod.router)
        self.c = TestClient(app)

        async def _woo(sku):
            return {"sku": sku, "nombre": "Lentes", "wc_id": 7, "estado": "publish",
                    "precio": 100.0, "stock": 3}

        from services import temu_panel, tiktok_panel, walmart_panel
        parches = [
            mock.patch.object(ruta_prod.woocommerce, "obtener_producto_por_sku", _woo),
            mock.patch.object(ruta_prod.costing_read, "validados", return_value=None),
            mock.patch.object(ruta_prod.meli, "listar", return_value=([], 0)),
        ] + [mock.patch.object(m, "datos_de", return_value=None)
             for m in (tiktok_panel, temu_panel, walmart_panel)]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)

    def _amazon(self, bitacora, kubera):
        """La tarjeta de Amazon (o None) con esa bitácora y esa fila de kubera."""
        por_sku = (mock.patch.object(ruta_prod.amazon, "por_sku", side_effect=bitacora)
                   if isinstance(bitacora, Exception)
                   else mock.patch.object(ruta_prod.amazon, "por_sku", return_value=bitacora))
        with por_sku, mock.patch.object(ruta_prod.inventario, "leer_inventario",
                                        return_value={SKU: kubera} if kubera else {}):
            r = self.c.get(f"/api/productos/{SKU}")
        self.assertEqual(r.status_code, 200, r.text)
        return next((c for c in r.json()["canales"] if c["canal"] == "amazon"), None)

    def test_el_asin_de_kubera_llega_a_la_tarjeta(self):
        t = self._amazon(BITACORA, KUBERA)
        self.assertEqual(t["item_id"], ASIN)
        self.assertEqual(t["url"], f"https://www.amazon.com.mx/dp/{ASIN}")
        # Lo demás no cambia: precio y stock de kubera, estado y categoría de la bitácora.
        self.assertEqual((t["precio"], t["stock_real"], t["situacion"]), (1998.26, 20, "BUYABLE"))
        self.assertEqual((t["estado"], t["categoria_id"]), ("PUBLISHED", "SUNGLASSES"))
        self.assertTrue(t["publicado"])

    def test_lo_que_el_publicador_no_registro_tambien_tiene_tarjeta(self):
        t = self._amazon(None, KUBERA)
        self.assertIsNotNone(t, "una publicación que solo está en kubera se quedaba sin tarjeta")
        self.assertEqual((t["item_id"], t["estado"], t["publicado"]), (ASIN, "BUYABLE", True))
        self.assertEqual(t["categoria_path"], [])

    def test_si_mysql_no_contesta_el_cajon_sale_igual(self):
        caida = HTTPException(status_code=503, detail="MySQL deshabilitado")
        t = self._amazon(caida, KUBERA)
        self.assertEqual(t["item_id"], ASIN)

    def test_sin_publicacion_en_ningun_lado_no_hay_tarjeta(self):
        self.assertIsNone(self._amazon(None, None))

    def test_solo_bitacora_sigue_como_antes(self):
        t = self._amazon(BITACORA, None)
        self.assertIsNone(t["item_id"])
        self.assertIsNone(t["url"])
        self.assertEqual((t["precio"], t["estado"]), (1500.0, "PUBLISHED"))


if __name__ == "__main__":
    unittest.main()
