"""Las fotos que van a Mercado Libre: se agranda el PRODUCTO, porque ML recorta el fondo blanco.

── QUÉ FIJAN ────────────────────────────────────────────
2-nov-2026, lote de 51: `ml_api._ensure_min_size` agrandaba una foto de 366×159 a 501×250
(500 justos + bandas blancas). ML recorta el blanco al procesarla y guardó 246×159 —el
producto—, abajo del mínimo: `item.pictures.invalid_size`.

  1. Una foto chica con fondo blanco sale con el CONTENIDO de ≥ FOTO_ML_LADO px por el lado
     largo (lo que queda después del recorte de ML).
  2. Una foto cuyo producto ya mide eso se manda igual (mismo tamaño).
  3. Transparente → fondo blanco, no negro. Toda blanca → no revienta y cumple el mínimo.
  4. Instalar dos veces no cambia nada; si algo falla se usa el escalado de siempre.

── NO SE TOCA NADA REAL ─────────────────────────────────
Imágenes de juguete en memoria; sin red.

    cd backend && python -m unittest tests.test_publicar_fotos_ml -v
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import publicar_ready as pr  # noqa: E402
from vendor.ml_ready import ml_api  # noqa: E402


def _png(size, caja, color=(30, 60, 200), modo="RGB", fondo=(255, 255, 255)):
    img = Image.new(modo, size, fondo if modo == "RGB" else (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle(caja, fill=color if modo == "RGB" else color + (255,))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _recorte_de_ml(data: bytes) -> tuple[int, int]:
    """Lo que hace ML: quitar el blanco. Devuelve el tamaño del contenido."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    dif = ImageChops.difference(img, Image.new("RGB", img.size, (255, 255, 255))).convert("L")
    b = dif.point(lambda v: 255 if v > 20 else 0).getbbox()
    return (b[2] - b[0], b[3] - b[1])


class FotosParaML(unittest.TestCase):
    def test_el_vendor_dejaba_el_producto_bajo_el_minimo(self):
        # 366×159 con el producto de 180×116 al centro, como ORG-0722-NEG-M.
        crudo = _png((366, 159), (93, 21, 272, 136))
        self.assertLess(max(_recorte_de_ml(ml_api._ensure_min_size(crudo))), 500)   # el defecto

    def test_foto_chica_sale_con_el_producto_grande(self):
        crudo = _png((366, 159), (93, 21, 272, 136))
        sale = pr._foto_para_ml(crudo)
        img = Image.open(io.BytesIO(sale))
        self.assertEqual(img.format, "JPEG")
        self.assertGreaterEqual(max(_recorte_de_ml(sale)), pr.FOTO_ML_LADO - 2)
        self.assertGreaterEqual(min(img.size), 250)

    def test_foto_grande_no_se_toca(self):
        crudo = _png((1200, 1200), (50, 50, 1150, 1150))
        img = Image.open(io.BytesIO(pr._foto_para_ml(crudo)))
        self.assertEqual(img.size, (1200, 1200))

    def test_foto_que_ya_pasaba_no_se_toca(self):
        # Contenido de 700 px (sobre el umbral de riesgo): se manda como venía, igual que antes.
        crudo = _png((800, 800), (50, 50, 750, 600))
        img = Image.open(io.BytesIO(pr._foto_para_ml(crudo)))
        self.assertEqual(img.size, (800, 800))

    def test_objeto_delgado_respeta_el_lado_corto(self):
        crudo = _png((400, 60), (10, 25, 390, 29))      # una diadema de 4 px de alto
        self.assertGreaterEqual(min(_recorte_de_ml(pr._foto_para_ml(crudo))), pr.FOTO_ML_CORTO - 2)

    def test_transparente_va_sobre_blanco(self):
        crudo = _png((300, 300), (100, 100, 200, 200), modo="RGBA")
        img = Image.open(io.BytesIO(pr._foto_para_ml(crudo))).convert("RGB")
        self.assertEqual(img.getpixel((2, 2)), (255, 255, 255))
        self.assertGreaterEqual(max(_recorte_de_ml(pr._foto_para_ml(crudo))), pr.FOTO_ML_LADO - 2)

    def test_toda_blanca_no_revienta(self):
        img = Image.open(io.BytesIO(pr._foto_para_ml(_png((200, 100), (0, 0, 0, 0), color=(255, 255, 255)))))
        self.assertGreaterEqual(max(img.size), 500)
        self.assertGreaterEqual(min(img.size), 250)

    def test_instalar_dos_veces(self):
        original = ml_api._ensure_min_size
        try:
            pr._instalar_fotos_para_ml()
            pr._instalar_fotos_para_ml()
            self.assertIs(ml_api._ensure_min_size, pr._foto_para_ml)
            self.assertIs(pr._ensure_min_size_original, original)
        finally:
            ml_api._ensure_min_size = original

    def test_si_falla_usa_el_escalado_de_siempre(self):
        original = ml_api._ensure_min_size
        try:
            pr._instalar_fotos_para_ml()
            with self.assertLogs("omnicanal.publicar_ready", level="ERROR"):
                with unittest.mock.patch.object(pr, "_contenido", side_effect=ValueError("rara")):
                    sale = pr._foto_para_ml(_png((366, 159), (93, 21, 272, 136)))
            self.assertEqual(Image.open(io.BytesIO(sale)).size, Image.open(io.BytesIO(original(
                _png((366, 159), (93, 21, 272, 136))))).size)
        finally:
            ml_api._ensure_min_size = original


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
