"""Traer las fotos de una URL de Alibaba a la galería del SKU (Estudio, v0.733).

── QUÉ FIJAN ────────────────────────────────────────────
  1. Solo URLs de alibaba.com.
  2. De lo que devuelve el scraper de Crear se bajan las 6 primeras y entran por `agregar`
     (la misma ruta que el «+», con el marcador de variante intacto).
  3. La URL queda en la meta `url_alibaba`.
  4. Sin fotos del scraper, 422 y no se escribe nada.

── NO SE TOCA NADA REAL ─────────────────────────────────
Apify, la descarga, WordPress y Woo van simulados.

    cd backend && python -m unittest tests.test_imagenes_alibaba -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import imagenes  # noqa: E402
from services import crear_producto  # noqa: E402

URL = "https://www.alibaba.com/product-detail/Lampara_1600.html"


class DesdeAlibaba(unittest.TestCase):
    def _correr(self, scrape, req):
        bajadas = []

        async def bajar(urls):
            bajadas.append(list(urls))
            return [imagenes.ImagenNueva(filename=f"alibaba-{i}", data_b64="eA==") for i, _ in enumerate(urls, 1)]

        agregar = mock.AsyncMock(return_value={"ok": True, "agregadas": 6})
        meta = mock.AsyncMock(return_value=True)
        with mock.patch.object(crear_producto, "scrape_alibaba", mock.AsyncMock(return_value=scrape)), \
             mock.patch.object(imagenes, "_bajar_fotos", side_effect=bajar), \
             mock.patch.object(imagenes, "agregar", agregar), \
             mock.patch.object(imagenes.woocommerce, "guardar_meta", meta):
            r = asyncio.run(imagenes.desde_alibaba("LAM-0001-NEG", req))
        return r, bajadas, agregar, meta

    def test_baja_las_6_primeras_y_guarda_la_url(self):
        scrape = {"imagenes": [f"https://s.alicdn.com/{i}.jpg" for i in range(1, 9)]}
        r, bajadas, agregar, meta = self._correr(
            scrape, imagenes.AlibabaReq(wc_id=77, url=URL, variante=True))
        self.assertEqual(bajadas, [scrape["imagenes"][:6]])
        enviado = agregar.call_args.args[1]
        self.assertEqual((enviado.wc_id, enviado.variante, len(enviado.imagenes)), (77, True, 6))
        meta.assert_awaited_once_with(77, "url_alibaba", URL)
        self.assertEqual(r["alibaba"], {"encontradas": 8, "agregadas": 6})

    def test_sin_fotos_no_escribe(self):
        with self.assertRaises(HTTPException) as e:
            self._correr(None, imagenes.AlibabaReq(wc_id=77, url=URL))
        self.assertEqual(e.exception.status_code, 422)

    def test_solo_alibaba(self):
        with self.assertRaises(HTTPException) as e:
            asyncio.run(imagenes.desde_alibaba("X", imagenes.AlibabaReq(url="https://amazon.com/x")))
        self.assertEqual(e.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
