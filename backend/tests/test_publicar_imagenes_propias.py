"""`campos.imagenes` llega al publicador (antes pydantic la tiraba).

── QUÉ FIJAN ────────────────────────────────────────────
`publicar_ready._imagenes_para` usa la lista propia de `campos` si viene («esa MANDA»), pero
`CamposPublicar` no declaraba el campo y pydantic lo descartaba: nunca llegaba. 3-nov-2026: hacía falta
para publicar con una foto mejor sin tocar la galería de Woo de un producto que ya existe.

  1. `imagenes` sobrevive a `PublicarRequest.a_dict()`.
  2. Sin `imagenes`, la lista va vacía y el adaptador sigue con la galería de Woo.
  3. Con lista propia, `_imagenes_para` la devuelve tal cual y no pregunta a Woo.

    cd backend && python -m unittest tests.test_publicar_imagenes_propias -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.publicar import PublicarRequest  # noqa: E402
from services import publicar_ready  # noqa: E402

FOTO = "https://chunche.shop/wp-content/uploads/2026/02/TEC-1406-NEG-VENTO-1.png"


class ImagenesPropias(unittest.TestCase):
    def test_llega_al_dict(self):
        req = PublicarRequest(sku="TEC-1406-NEG-AVEO-1.6", wc_id=65416, campos={"titulo": "x", "imagenes": [FOTO]})
        self.assertEqual(req.a_dict()["campos"]["imagenes"], [FOTO])

    def test_sin_lista_va_vacia(self):
        self.assertEqual(PublicarRequest(sku="X", campos={"titulo": "x"}).a_dict()["campos"]["imagenes"], [])

    def test_el_adaptador_la_usa_sin_preguntar_a_woo(self):
        with mock.patch.object(publicar_ready.wp_db, "imagenes", side_effect=AssertionError("no debía leer Woo")):
            self.assertEqual(publicar_ready._imagenes_para(65416, {"imagenes": [FOTO, " "]}), [FOTO])


if __name__ == "__main__":
    unittest.main()
