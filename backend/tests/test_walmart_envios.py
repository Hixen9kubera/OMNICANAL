"""Los cuatro arreglos de Walmart del 7-oct-2026, uno por clase.

── POR QUÉ ESTAS PRUEBAS ──────────────────────────────────────────────────────
En tres semanas (17-sep → 7-oct) el botón de publicar de Walmart mandó 49 altas
y no entró ni una. Lo que las tumbó, medido feed por feed:

  · 38 por la FOTO — "We are not authorized to download the image": Walmart
    dejó de poder bajar las de chunche.shop.
  · 3 por `screenSize` en «Juguetes de bebé»: producción lo exige y el esquema
    publicado no.
  · 2 por REENVÍO — "under compliance review": el mismo SKU, mandado otra vez
    antes del veredicto.
  · 3 por falta de la foto adicional.

Y encima los datos que sí viajaban iban mal: una variante perdía su color y su
material, el nombre decía «Sin marca» con `brand: Ferrahome`, y un espejo de
bebé salía con los 30 kg de la caja del proveedor.

Nada de esto abre red ni base: son las funciones puras y la ruta pública con
el descargador cambiado por uno de mentira.

    cd backend && python -m unittest tests.test_walmart_envios -v
"""
from __future__ import annotations

import asyncio
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from core import middleware  # noqa: E402
from routers import publico  # noqa: E402
from scripts import publicar_walmart as script  # noqa: E402
from scripts.walmart_field_requirements import CORRECCIONES_MEDIDAS  # noqa: E402
from services import imagenes_walmart as iw  # noqa: E402
from services import publicar_walmart as pw  # noqa: E402
from services import walmart_contenido as wc  # noqa: E402
from services import walmart_ia  # noqa: E402


def _png(ancho: int, alto: int, modo: str = "RGB", color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new(modo, (ancho, alto), color).save(buf, "PNG")
    return buf.getvalue()


def _webp(ancho: int, alto: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (ancho, alto), (10, 120, 200)).save(buf, "WEBP")
    return buf.getvalue()


def _abrir(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


# ═════════════════════════════════════════════════════════════════════════════
# 1 · LAS FOTOS
# ═════════════════════════════════════════════════════════════════════════════
class Conversion(unittest.TestCase):
    """Lo que sale SIEMPRE es un JPEG RGB de lado corto ≥ 1000 px y ≤ 1 MB."""

    def test_webp_chico_sale_jpeg_de_1000(self):
        # La foto real de ELEC-0145: WEBP de 720×720.
        data, info = iw.convertir(_webp(720, 720))
        im = _abrir(data)
        self.assertEqual(im.format, "JPEG")
        self.assertEqual(im.mode, "RGB")
        self.assertEqual(im.size, (1000, 1000))
        self.assertEqual(info["formato_origen"], "WEBP")
        self.assertTrue(info["agrandada"])
        self.assertFalse(info["rellenada"])

    def test_muy_chica_se_agranda_el_doble_y_se_rellena(self):
        # TEC-0620-MET-1.jpg mide 288×500. Agrandarla 3.5× solo inventa
        # borrosidad: se dobla (576×1000) y el resto es lienzo blanco.
        data, info = iw.convertir(_png(288, 500))
        im = _abrir(data)
        self.assertEqual(im.size, (1000, 1000))
        self.assertTrue(info["agrandada"] and info["rellenada"])
        self.assertEqual((info["ancho_origen"], info["alto_origen"]), (288, 500))
        # El borde es BLANCO (lienzo), el centro es la foto.
        self.assertGreater(min(im.getpixel((5, 500))), 240)
        self.assertLess(im.getpixel((500, 500))[1], 120)

    def test_transparencia_va_sobre_blanco_no_sobre_negro(self):
        # ACC-0166-NEG-1.png es RGBA. Convertir RGBA→RGB a secas la deja en
        # negro, y un fondo negro es de lo poco que Walmart sí rechaza.
        data, _ = iw.convertir(_png(1200, 1200, "RGBA", (0, 0, 0, 0)))
        im = _abrir(data)
        self.assertEqual(im.mode, "RGB")
        self.assertGreater(min(im.getpixel((600, 600))), 240)

    def test_grande_no_se_toca_de_tamano(self):
        data, info = iw.convertir(_png(1500, 1500))
        self.assertEqual(_abrir(data).size, (1500, 1500))
        self.assertFalse(info["agrandada"] or info["rellenada"])

    def test_enorme_se_reduce_y_pesa_menos_de_un_mega(self):
        ruido = Image.effect_noise((3200, 3200), 90).convert("RGB")
        buf = io.BytesIO()
        ruido.save(buf, "PNG")
        data, info = iw.convertir(buf.getvalue())
        self.assertLessEqual(max(_abrir(data).size), iw.MAX_LADO)
        self.assertLessEqual(len(data), iw.MAX_BYTES)
        self.assertGreaterEqual(min(_abrir(data).size), iw.MIN_LADO)

    def test_alargada_no_revienta_el_tope(self):
        # 300×2000: agrandar el lado corto la sacaría de MAX_LADO.
        data, _ = iw.convertir(_png(300, 2000))
        w, h = _abrir(data).size
        self.assertLessEqual(max(w, h), iw.MAX_LADO)
        self.assertGreaterEqual(min(w, h), iw.MIN_LADO)

    def test_lo_que_no_es_imagen_revienta(self):
        with self.assertRaises(Exception):
            iw.convertir(b"<html>Mantenimiento</html>")


class Rutas(unittest.TestCase):
    """La URL pública dice qué archivo es — y SOLO puede ser uno de la tienda."""

    def setUp(self):
        p = mock.patch.object(iw, "host_tienda", return_value="chunche.shop")
        p.start()
        self.addCleanup(p.stop)
        b = mock.patch.object(iw, "base_publica",
                              return_value="https://back.ejemplo.app")
        b.start()
        self.addCleanup(b.stop)

    def test_foto_de_la_tienda(self):
        self.assertEqual(
            iw.ruta_de("https://chunche.shop/wp-content/uploads/2026/04/ROP-0010-BEI-1.jpg"),
            "2026/04/ROP-0010-BEI-1.jpg")

    def test_la_url_propia_termina_en_jpg_aunque_la_fuente_sea_webp(self):
        u = iw.url_propia("2026/04/H677R.webp")
        self.assertEqual(u, "https://back.ejemplo.app/pub/img/wm/2026/04/H677R.webp.jpg")
        self.assertNotIn("?", u)          # sin cadena de consulta, a propósito

    def test_otro_host_no_es_de_la_tienda(self):
        self.assertIsNone(iw.ruta_de("https://m.media-amazon.com/images/I/71x.jpg"))
        self.assertIsNone(iw.ruta_de("https://chunche.shop.evil.com/wp-content/uploads/a.jpg"))
        self.assertIsNone(iw.ruta_de("https://chunche.shop/wp-json/wc/v3/products"))

    def test_rutas_que_no_se_sirven(self):
        for mala in ("../wp-config.php", "2026/../../wp-config.php", "/etc/passwd",
                     "2026/04/a.php", "2026/04/a.jpg?x=1", "2026//a.jpg", "",
                     "2026/04/a.svg", "a\\b.jpg", "2026/04/a.jpg#x"):
            self.assertFalse(iw._ruta_valida(mala), mala)
        for buena in ("2026/04/ROP-0010-BEI-1.jpg", "2026/10/Hf74f2eeH.webp",
                      "2026/02/MUE-0182-NEG-GRI-220-1.png",
                      "2026/05/61G17SJ7hIL._AC_SL1500_.jpg"):
            self.assertTrue(iw._ruta_valida(buena), buena)

    def test_weserv_es_la_receta_de_agosto(self):
        self.assertEqual(
            iw.url_weserv("https://chunche.shop/wp-content/uploads/2026/04/a.webp"),
            "https://images.weserv.nl/?url=chunche.shop/wp-content/uploads/2026/04/a.webp&output=jpg")
        self.assertIn("w=1000&h=1000", iw.url_weserv("https://chunche.shop/a.jpg", True))


class Preparar(unittest.TestCase):
    """Lo que llama el publicador: URLs comprobadas, o un motivo para no mandar."""

    U = "https://chunche.shop/wp-content/uploads/2026/04/"

    def setUp(self):
        for nombre, valor in (("host_tienda", "chunche.shop"),
                              ("base_publica", "https://back.ejemplo.app"),
                              ("modo", "propio")):
            p = mock.patch.object(iw, nombre, return_value=valor)
            p.start()
            self.addCleanup(p.stop)
        self.viva = mock.patch.object(iw, "ruta_publica_viva",
                                      mock.AsyncMock(return_value=True))
        self.viva.start()
        self.addCleanup(self.viva.stop)

    def _con(self, tabla):
        async def _obtener(ruta, cli=None):
            return tabla.get(ruta)
        return mock.patch.object(iw, "obtener", _obtener)

    @staticmethod
    def _info(w, h, agrandada=False):
        return (b"x", {"ancho_origen": w, "alto_origen": h, "agrandada": agrandada,
                       "rellenada": False})

    def test_dos_fotos_buenas(self):
        tabla = {"2026/04/a.webp": self._info(1500, 1500),
                 "2026/04/b.webp": self._info(1500, 1500)}
        with self._con(tabla):
            r = asyncio.run(iw.preparar([self.U + "a.webp", self.U + "b.webp"], "X"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["urls"], [
            "https://back.ejemplo.app/pub/img/wm/2026/04/a.webp.jpg",
            "https://back.ejemplo.app/pub/img/wm/2026/04/b.webp.jpg"])
        self.assertEqual(r["avisos"], [])

    def test_una_sola_foto_no_se_manda(self):
        # DEC-0182-BLN-3D, 18-sep: "'Foto adicional' requires a minimum of '1'
        # entries". Tres feeds gastados para leer lo mismo.
        with self._con({"2026/04/a.webp": self._info(1500, 1500)}):
            r = asyncio.run(iw.preparar([self.U + "a.webp"], "DEC-0182-BLN-3D"))
        self.assertFalse(r["ok"])
        self.assertIn("adicional", r["motivo"])

    def test_la_ilegible_se_salta_y_se_dice(self):
        tabla = {"2026/04/a.webp": self._info(1500, 1500),
                 "2026/04/c.webp": self._info(1500, 1500)}
        with self._con(tabla):
            r = asyncio.run(iw.preparar(
                [self.U + "a.webp", self.U + "rota.webp", self.U + "c.webp"], "X"))
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["urls"]), 2)
        self.assertTrue(any("no se pudieron leer" in a for a in r["avisos"]))

    def test_las_chicas_se_avisan(self):
        tabla = {"2026/04/a.jpg": self._info(288, 500, True),
                 "2026/04/b.jpg": self._info(500, 454, True)}
        with self._con(tabla):
            r = asyncio.run(iw.preparar([self.U + "a.jpg", self.U + "b.jpg"], "TEC-0620-MET"))
        self.assertTrue(r["ok"])
        self.assertTrue(any("288×500" in a and "borrosas" in a for a in r["avisos"]))

    def test_nunca_mas_de_cinco(self):
        tabla = {f"2026/04/{i}.webp": self._info(1500, 1500) for i in range(9)}
        with self._con(tabla):
            r = asyncio.run(iw.preparar([f"{self.U}{i}.webp" for i in range(9)], "X"))
        self.assertEqual(len(r["urls"]), iw.MAX_FOTOS)

    def test_sin_ninguna_no_se_manda(self):
        with self._con({}):
            r = asyncio.run(iw.preparar([self.U + "a.webp"], "X"))
        self.assertFalse(r["ok"])
        self.assertIn("principal", r["motivo"])

    def test_si_la_ruta_publica_no_contesta_cae_al_proxy(self):
        # Un local o un staging sin dominio: mandar URLs que nadie puede abrir
        # sería repetir el problema con otro host.
        self.viva.stop()
        with mock.patch.object(iw, "ruta_publica_viva",
                               mock.AsyncMock(return_value=False)), \
             mock.patch.object(iw, "_medir", mock.AsyncMock(return_value={
                 "formato": "JPEG", "ancho": 1500, "alto": 1500, "bytes": 9})):
            r = asyncio.run(iw.preparar([self.U + "a.webp", self.U + "b.webp"], "X"))
        self.viva.start()
        self.assertTrue(r["ok"])
        self.assertEqual(r["modo"], "weserv")
        self.assertTrue(all(u.startswith("https://images.weserv.nl/") for u in r["urls"]))
        self.assertTrue(any("no contesta" in a for a in r["avisos"]))


class RutaPublica(unittest.TestCase):
    """`/pub/img/wm/…` y `/robots.txt`: lo único que se sirve sin credencial."""

    def setUp(self):
        app = FastAPI()
        app.middleware("http")(middleware.identidad)
        app.include_router(publico.router)

        @app.get("/api/productos")
        def _cerrada():
            return {"dato": "privado"}

        self.cli = TestClient(app)
        # Enforcement ENCENDIDO: la prueba es que aun así estas dos pasan.
        for attr, val in (("auth_enforced", True), ("api_key", "secreta")):
            p = mock.patch.object(middleware.settings, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def test_robots_contesta_200_y_abre_solo_las_fotos(self):
        r = self.cli.get("/robots.txt")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Allow: /pub/img/", r.text)
        self.assertIn("Disallow: /", r.text)
        self.assertTrue(r.headers["content-type"].startswith("text/plain"))

    def test_la_foto_sale_como_jpeg_sin_credencial(self):
        jpeg, _ = iw.convertir(_png(1200, 1200))
        with mock.patch.object(iw, "servir", mock.AsyncMock(return_value=jpeg)) as m:
            r = self.cli.get("/pub/img/wm/2026/04/ROP-0010-BEI-1.jpg.jpg")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertEqual(r.content, jpeg)
        self.assertIn("max-age", r.headers["cache-control"])
        m.assert_awaited_once_with("2026/04/ROP-0010-BEI-1.jpg.jpg")

    def test_head_dice_el_tamano_sin_mandar_el_cuerpo(self):
        jpeg, _ = iw.convertir(_png(1200, 1200))
        with mock.patch.object(iw, "servir", mock.AsyncMock(return_value=jpeg)):
            r = self.cli.head("/pub/img/wm/2026/04/a.jpg.jpg")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-length"], str(len(jpeg)))
        self.assertEqual(r.content, b"")

    def test_lo_que_no_existe_es_404(self):
        with mock.patch.object(iw, "servir", mock.AsyncMock(return_value=None)):
            self.assertEqual(self.cli.get("/pub/img/wm/2026/04/no.jpg.jpg").status_code, 404)

    def test_el_resto_de_la_api_sigue_cerrado(self):
        self.assertEqual(self.cli.get("/api/productos").status_code, 401)

    def test_el_prefijo_no_abre_a_sus_vecinos(self):
        self.assertTrue(middleware.es_ruta_abierta("/robots.txt"))
        self.assertTrue(middleware.es_ruta_abierta("/pub/img/wm/2026/04/a.jpg.jpg"))
        for cerrada in ("/pub/imgs-privadas/x", "/pub", "/pub/img", "/pub/otra/x.jpg",
                        "/api/pub/img/x", "/robots.txt.bak", "/api/productos"):
            self.assertFalse(middleware.es_ruta_abierta(cerrada), cerrada)

    def test_servir_solo_acepta_jpg_y_rutas_validas(self):
        async def _nadie(*a, **k):
            raise AssertionError("no debió salir a la red")
        with mock.patch.object(iw, "_bajar", _nadie):
            for mala in ("2026/04/a.webp", "../../etc/passwd.jpg", "x.php.jpg"):
                self.assertIsNone(asyncio.run(iw.servir(mala)), mala)

    def test_servir_convierte_lo_que_baja_de_la_tienda(self):
        iw._CACHE.clear()
        iw._NO_ESTA.clear()
        pedidas = []

        async def _bajar(url, cli=None):
            pedidas.append(url)
            return _webp(800, 800)
        with mock.patch.object(iw, "_bajar", _bajar), \
             mock.patch.object(iw, "host_tienda", return_value="chunche.shop"):
            data = asyncio.run(iw.servir("2026/04/H677R.webp.jpg"))
            otra = asyncio.run(iw.servir("2026/04/H677R.webp.jpg"))
        self.assertEqual(pedidas, ["https://chunche.shop/wp-content/uploads/2026/04/H677R.webp"])
        self.assertEqual(_abrir(data).format, "JPEG")
        self.assertEqual(_abrir(data).size, (1000, 1000))
        self.assertEqual(data, otra)          # la segunda salió del caché
        iw._CACHE.clear()


# ═════════════════════════════════════════════════════════════════════════════
# 2 · screenSize EN «JUGUETES DE BEBÉ»
# ═════════════════════════════════════════════════════════════════════════════
def _producto(**extra):
    p = {"sku": "JUGU-0049-MUL", "name": "Sonajero Espiral para Cuna Oruga",
         "weight": "0.7", "dimensions": {"length": "34", "width": "20", "height": "7"},
         "_precio_lista": 569.43, "attributes": [
             {"name": "BRAND", "options": ["Ferrahome"]},
             {"name": "COLOR", "options": ["Multicolor"]}]}
    p.update(extra)
    return p


class ScreenSize(unittest.TestCase):
    IMGS = ["https://x/1.jpg", "https://x/2.jpg"]

    def test_juguetes_de_bebe_lleva_screensize(self):
        cfg = script.CATEGORIAS_AUTORIZADAS["baby_toys"]
        vis = script._item(_producto(), self.IMGS, "baby_toys", cfg)["Visible"]["Juguetes de bebé"]
        # Sin pantalla: 0 pulgadas. Es lo cierto, y el esquema lo admite.
        self.assertEqual(vis["screenSize"], {"measure": 0.0, "unit": "in"})
        self.assertIn("educationalFocus", vis)

    def test_si_woo_trae_la_pantalla_se_usa(self):
        cfg = script.CATEGORIAS_AUTORIZADAS["baby_toys"]
        p = _producto(attributes=[{"name": "SCREEN_SIZE", "options": ["2.4 in"]}])
        vis = script._item(p, self.IMGS, "baby_toys", cfg)["Visible"]["Juguetes de bebé"]
        self.assertEqual(vis["screenSize"]["measure"], 2.4)

    def test_las_demas_categorias_no_lo_llevan(self):
        # Un campo de más tumba el artículo: solo va donde está medido.
        for clave, cfg in script.CATEGORIAS_AUTORIZADAS.items():
            if clave == "baby_toys":
                continue
            vis = script._item(_producto(), self.IMGS, clave, cfg)["Visible"][cfg["clave_visible"]]
            self.assertNotIn("screenSize", vis, clave)

    def test_esta_medido_y_el_semaforo_lo_da_por_automatico(self):
        self.assertEqual(CORRECCIONES_MEDIDAS[("Juguetes de bebé", "screenSize")][0],
                         "OBLIGATORIO")
        self.assertIn("screenSize", walmart_ia._DEL_PUBLICADOR)

    def test_pulgadas(self):
        for texto, esperado in (("2.4 in", 2.4), ("7 pulgadas", 7.0), ("5,5″", 5.5),
                                (None, 0.0), ("sin pantalla", 0.0), ("", 0.0)):
            self.assertEqual(script._pulgadas(texto), esperado, texto)


# ═════════════════════════════════════════════════════════════════════════════
# 3 · EL CANDADO CONTRA REENVÍOS
# ═════════════════════════════════════════════════════════════════════════════
def _feed(sku, estado, feed="PROCESSED", errores=None):
    return {"ok": True, "estado": feed,
            "articulos": [{"sku": sku, "estado": estado, "errores": errores or []}]}


class Veredicto(unittest.TestCase):
    def test_en_proceso(self):
        self.assertEqual(pw.veredicto_de(_feed("A-1", "INPROGRESS", "INPROGRESS"), "A-1")[0],
                         "EN_PROCESO")

    def test_recien_recibido_y_aun_no_lista_el_articulo(self):
        self.assertEqual(pw.veredicto_de({"ok": True, "estado": "RECEIVED",
                                          "articulos": []}, "A-1")[0], "EN_PROCESO")

    def test_rechazado_trae_sus_errores(self):
        err = [{"codigo": "ERR_EXT_DATA_0101312", "campo": "ASSET",
                "mensaje": "We are not authorized to download the image"}]
        estado, errores = pw.veredicto_de(_feed("a-1", "DATA_ERROR", errores=err), "A-1")
        self.assertEqual(estado, "DATA_ERROR")      # el SKU casa sin importar mayúsculas
        self.assertEqual(errores, err)
        self.assertIn("no pudo descargar la foto", pw._resumen_errores(errores))

    def test_consulta_fallida_no_es_un_veredicto(self):
        self.assertEqual(pw.veredicto_de({"ok": False}, "A-1")[0], "DESCONOCIDO")
        self.assertEqual(pw.veredicto_de(None, "A-1")[0], "DESCONOCIDO")


class Reenvio(unittest.TestCase):
    @staticmethod
    def _e(estado, hace_min, feed="F1"):
        return {"estado": estado, "hace_min": hace_min, "feed_id": feed, "resumen": ""}

    def test_sin_envios_previos_pasa(self):
        self.assertFalse(pw.decidir_reenvio([])["bloquea"])

    def test_en_proceso_bloquea(self):
        # ORG-1078-BLN, 3-oct: reenviado a los 2 minutos → "under compliance review".
        d = pw.decidir_reenvio([self._e("EN_PROCESO", 2)])
        self.assertTrue(d["bloquea"])
        self.assertIn("SOURCE_DP_PENDING", d["motivo"])

    def test_rechazado_se_puede_reenviar(self):
        self.assertFalse(pw.decidir_reenvio([self._e("DATA_ERROR", 5)])["bloquea"])

    def test_aceptado_hace_poco_bloquea(self):
        # ELEC-0146-PC6-NAR, 12-sep: SUCCESS y, 77 minutos después, el reenvío
        # rebotó por la revisión de cumplimiento.
        d = pw.decidir_reenvio([self._e("SUCCESS", 77)])
        self.assertTrue(d["bloquea"])
        self.assertIn("ACEPTÓ", d["motivo"])

    def test_aceptado_hace_tres_dias_ya_no_bloquea(self):
        self.assertFalse(pw.decidir_reenvio([self._e("SUCCESS", 72 * 60)])["bloquea"])

    def test_un_exito_viejo_bloquea_aunque_el_ultimo_haya_fallado(self):
        d = pw.decidir_reenvio([self._e("DATA_ERROR", 600, "F2"),
                                self._e("SUCCESS", 1800, "F1")])
        self.assertTrue(d["bloquea"])
        self.assertIn("F1", d["motivo"])

    def test_sin_veredicto_y_reciente_bloquea_un_rato(self):
        self.assertTrue(pw.decidir_reenvio([self._e("DESCONOCIDO", 5)])["bloquea"])
        self.assertFalse(pw.decidir_reenvio([self._e("DESCONOCIDO", 90)])["bloquea"])

    def test_el_aviso_dice_que_paso(self):
        e = dict(self._e("DATA_ERROR", 200), resumen="Walmart no pudo descargar la foto")
        aviso = pw.aviso_ultimo_envio({"ultimo": e})
        self.assertIn("RECHAZADO", aviso)
        self.assertIn("no pudo descargar la foto", aviso)
        self.assertIsNone(pw.aviso_ultimo_envio({"ultimo": None}))

    def test_estado_envios_pregunta_a_walmart_y_anota(self):
        filas = [{"submission_id": "F9", "status": "ENVIADO", "hace_min": 40,
                  "error_resumen": None, "actor": "cinthya@kubera.mx"}]
        err = [{"codigo": "EXT_DATA_ERROR_72600149546850", "campo": "screenSize",
                "mensaje": "`screenSize` is a required attribute"}]
        anotado = []
        from services import walmart
        with mock.patch.object(pw, "_envios_recientes", return_value=filas), \
             mock.patch.object(pw, "_anotar_veredicto",
                               side_effect=lambda *a: anotado.append(a) or 1), \
             mock.patch.object(walmart, "feed_estado", mock.AsyncMock(
                 return_value=_feed("JUGU-0049-MUL", "DATA_ERROR", errores=err))):
            r = asyncio.run(pw.estado_envios("JUGU-0049-MUL"))
        self.assertFalse(r["bloquea"])                 # rechazado: se puede reenviar
        self.assertEqual(r["ultimo"]["estado"], "DATA_ERROR")
        self.assertEqual(anotado[0][:3], ("F9", "JUGU-0049-MUL", "DATA_ERROR"))
        self.assertIn("screenSize", anotado[0][3])

    def test_sin_bitacora_no_se_inutiliza_el_canal(self):
        with mock.patch.object(pw, "_envios_recientes", side_effect=RuntimeError("BD")):
            r = asyncio.run(pw.estado_envios("X"))
        self.assertFalse(r["bloquea"])
        self.assertTrue(r["sin_bitacora"])


# ═════════════════════════════════════════════════════════════════════════════
# 4 · LOS DATOS QUE VIAJAN
# ═════════════════════════════════════════════════════════════════════════════
PADRE = {"id": 57039, "sku": "ROP-0010", "name": "Pijama para Mujer 2 Piezas Borrega",
         "categories": [{"name": "Pijamas"}], "brands": [],
         "description": "<p>Pijama de borrega.</p>", "short_description": "",
         "weight": "0.45", "dimensions": {"length": "30", "width": "25", "height": "5"},
         "attributes": [
             {"name": "BRAND", "options": ["Ferrahome"], "variation": False},
             {"name": "MAIN_MATERIAL", "options": ["Poliéster"], "variation": False},
             {"name": "Color", "options": ["Beige", "Café", "Lila"], "variation": True},
             {"name": "Talla", "options": ["CH", "M", "G"], "variation": True}]}
VARIANTE = {"id": 63577, "sku": "ROP-0010-BEI", "type": "variation", "parent_id": 57039,
            "name": "Pijama para Mujer 2 Piezas Borrega - Beige", "categories": [],
            "description": "", "weight": "0.0",
            "dimensions": {"length": "46.00", "width": "", "height": "44.00"},
            "attributes": [{"name": "Color", "option": "Beige"}],
            "_precio_lista": 392.74}


class Variante(unittest.TestCase):
    def test_hereda_lo_del_padre_y_conserva_lo_suyo(self):
        v = pw.fundir_padre(VARIANTE, PADRE)
        atrs = {a["name"]: a["options"] for a in v["attributes"]}
        self.assertEqual(atrs["Color"], ["Beige"])          # el suyo
        self.assertEqual(atrs["BRAND"], ["Ferrahome"])      # del padre
        self.assertEqual(atrs["MAIN_MATERIAL"], ["Poliéster"])
        self.assertEqual(v["categories"], [{"name": "Pijamas"}])
        self.assertEqual(v["description"], "<p>Pijama de borrega.</p>")
        self.assertEqual(v["weight"], "0.45")               # el suyo era 0
        self.assertEqual(v["dimensions"], {"length": "46.00", "width": "25", "height": "44.00"})
        self.assertEqual(v["_padre"]["sku"], "ROP-0010")

    def test_no_toma_la_talla_de_otra_hermana(self):
        # El padre trae TODAS las tallas; esta variante no fija la suya. Tomar
        # la primera de la lista publicaría todas las hermanas como «CH».
        v = pw.fundir_padre(VARIANTE, PADRE)
        self.assertNotIn("Talla", {a["name"] for a in v["attributes"]})

    def test_no_muta_lo_que_recibe(self):
        antes = repr(VARIANTE)
        pw.fundir_padre(VARIANTE, PADRE)
        self.assertEqual(repr(VARIANTE), antes)

    def test_sin_padre_devuelve_la_variante(self):
        self.assertIs(pw.fundir_padre(VARIANTE, None), VARIANTE)

    def test_el_feed_de_la_variante_ya_no_sale_con_los_respaldos(self):
        cfg = script.CATEGORIAS_AUTORIZADAS["clothing_other"]
        notas: dict = {}
        item = script._item(pw.fundir_padre(VARIANTE, PADRE),
                            ["https://x/1.jpg", "https://x/2.jpg"],
                            "clothing_other", cfg, notas)
        vis = item["Visible"]["Ropa"]
        self.assertEqual(vis["colorCategory"], ["Beige"])     # era «Multicolor»
        self.assertEqual(vis["material"], "Poliéster")        # era «Plástico»
        self.assertEqual(vis["activity"], ["Dormir"])         # era «Juego»
        self.assertEqual(item["Orderable"]["brand"], "Ferrahome")
        self.assertEqual(item["Orderable"]["ShippingWeight"]["measure"], 0.45)
        # Nada salió de un respaldo a ciegas: «Dormir» se dedujo del título
        # ("pijama") y lo demás lo trae Woo.
        self.assertEqual(notas["respaldos"], {})

    def test_la_variante_sola_lee_su_option(self):
        # Sin fundir: `option` (singular) ya no se pierde.
        cfg = script.CATEGORIAS_AUTORIZADAS["clothing_other"]
        vis = script._item(VARIANTE, ["a", "b"], "clothing_other", cfg)["Visible"]["Ropa"]
        self.assertEqual(vis["colorCategory"], ["Beige"])

    def test_ropa_que_no_es_pijama_no_dice_dormir(self):
        cfg = script.CATEGORIAS_AUTORIZADAS["clothing_other"]
        p = _producto(name="Chamarra Deportiva para Mujer", sku="ROP-0200")
        vis = script._item(p, ["a", "b"], "clothing_other", cfg)["Visible"]["Ropa"]
        self.assertEqual(vis["activity"], ["Uso diario"])


class SinMarca(unittest.TestCase):
    def test_se_quita_del_nombre_y_va_la_marca_del_feed(self):
        # JUGU-0264-ROS quedó publicado así en Walmart.
        t, tocado = pw.titulo_con_marca(
            "Sin marca Set de Limpieza de Juguete con Cubeta", "Ferrahome")
        self.assertEqual(t, "Ferrahome Set de Limpieza de Juguete con Cubeta")
        self.assertTrue(tocado)

    def test_variantes_del_mismo_vicio(self):
        for mal in ("SIN MARCA - Cocina de Juguete", "Genérico Cocina de Juguete",
                    "Sin  marca: Cocina de Juguete", "Marca genérica Cocina de Juguete"):
            self.assertEqual(pw.titulo_con_marca(mal, "Ferrahome")[0],
                             "Ferrahome Cocina de Juguete", mal)

    def test_un_titulo_bueno_no_se_toca(self):
        t = "Ferrahome Pijama para Mujer 2 Piezas Borrega Beige"
        self.assertEqual(pw.titulo_con_marca(t, "Ferrahome"), (t, False))
        # "Sin marcas de agua" no es «Sin marca».
        t2 = "Protector Sin marcas de agua para pantalla"
        self.assertEqual(pw.titulo_con_marca(t2, "Ferrahome"), (t2, False))

    def test_no_duplica_la_marca(self):
        self.assertEqual(pw.titulo_con_marca("Sin marca Ferrahome Cocina", "Ferrahome")[0],
                         "Ferrahome Cocina")

    def test_el_validador_lo_marca_para_que_la_ia_lo_repare(self):
        _c, problemas = wc.validar_contenido({
            "titulo": "Sin marca Set de Limpieza de Juguete con Cubeta Escoba y Recogedor",
            "descripcion": "Set de limpieza de juguete para niños.",
            "beneficios": ["Fomenta el orden", "Tamaño infantil", "Fácil de guardar"]})
        self.assertTrue(any("Sin marca" in p for p in problemas))

    def test_el_prompt_pide_la_marca_del_feed(self):
        p = wc.build_prompt_contenido(sku="X", categoria="Juguetes", titulo_woo="Cocina",
                                      descripcion_woo="", marca="")
        self.assertIn("EXACTAMENTE «Ferrahome»", p)
        self.assertNotIn('usa el fabricante o "Sin marca"', p)
        self.assertIn('"marca": "Ferrahome"', p)

    def test_la_marca_del_feed_es_la_de_item(self):
        self.assertEqual(walmart_ia.marca_del_feed({}), "Ferrahome")
        self.assertEqual(walmart_ia.marca_del_feed(None), "Ferrahome")
        self.assertEqual(walmart_ia.marca_del_feed({"BRAND": "Infantino"}), "Infantino")

    def test_aplicar_ia_limpia_el_contenido_ya_guardado(self):
        cfg = script.CATEGORIAS_AUTORIZADAS["toys_other"]
        item = script._item(_producto(), ["a", "b"], "toys_other", cfg)
        doc = {"contenido": {"titulo": "Sin marca Cocina de Juguete para Niños"},
               "categoria": "Juguetes"}
        item, comparativa, _ = pw._aplicar_ia(item, doc, "Juguetes", cfg, None)
        self.assertEqual(item["Orderable"]["productName"],
                         "Ferrahome Cocina de Juguete para Niños")
        self.assertTrue(any("Sin marca" in (c.get("nota") or "") for c in comparativa))


class DatosDudosos(unittest.TestCase):
    def _armar(self, p, clave="toys_other"):
        cfg = script.CATEGORIAS_AUTORIZADAS[clave]
        notas: dict = {}
        item = script._item(p, ["a", "b"], clave, cfg, notas)
        return pw.avisos_de_datos(p, item, cfg, notas), item, cfg, notas

    def test_treinta_kilos_para_un_espejo_de_bebe(self):
        # BEB-0014-MUL: 30 kg y 65×65×50 cm a $434.15 — la caja del proveedor.
        p = _producto(sku="BEB-0014-MUL", weight="30.0", _precio_lista=434.15,
                      dimensions={"length": "65", "width": "65", "height": "50"})
        avisos, *_ = self._armar(p)
        self.assertTrue(any("PESO DUDOSO" in a and "30 kg" in a for a in avisos))

    def test_un_mueble_pesado_y_caro_no_alarma(self):
        p = _producto(sku="MUE-0001", weight="32", _precio_lista=4500.0)
        avisos, *_ = self._armar(p)
        self.assertFalse(any("PESO DUDOSO" in a for a in avisos))

    def test_los_respaldos_se_dicen(self):
        p = _producto(weight="", dimensions={}, attributes=[])
        avisos, *_ = self._armar(p, "home_other")
        junto = " ".join(avisos)
        self.assertIn("DATOS POR OMISIÓN", junto)
        for dato in ("material «Plástico»", "color «Multicolor»", "peso 0.3 kg", "medidas 10 cm"):
            self.assertIn(dato, junto)

    def test_lo_que_la_ia_ya_corrigio_no_se_avisa(self):
        p = _producto(attributes=[])
        avisos, item, cfg, notas = self._armar(p, "home_other")
        self.assertTrue(any("color «Multicolor»" in a for a in avisos))
        item["Visible"][cfg["clave_visible"]]["colorCategory"] = ["Azul"]
        item["Visible"][cfg["clave_visible"]]["material"] = "Madera"
        despues = " ".join(pw.avisos_de_datos(p, item, cfg, notas))
        self.assertNotIn("color", despues)
        self.assertNotIn("material", despues)

    def test_un_producto_completo_no_avisa_nada(self):
        p = _producto(attributes=[{"name": "BRAND", "options": ["Ferrahome"]},
                                  {"name": "COLOR", "options": ["Rojo"]},
                                  {"name": "MATERIAL", "options": ["Madera"]},
                                  {"name": "ACTIVITY", "options": ["Juego de roles"]}])
        avisos, *_ = self._armar(p)
        self.assertEqual(avisos, [])


# ═════════════════════════════════════════════════════════════════════════════
# 5 · UN SCRIPT QUE EL BACKEND IMPORTA NO PUEDE APAGARLE LOS LOGS
# ═════════════════════════════════════════════════════════════════════════════
class ScriptsImportadosNoTocanElLogging(unittest.TestCase):
    """
    `scripts/publicar_walmart.py` tenía `logging.disable(logging.WARNING)` a
    nivel de módulo, y los servicios de Walmart lo importan. Es global al
    proceso: en producción, la primera vez que alguien abría Walmart después de
    un reinicio el backend dejaba de escribir INFO y WARNING hasta el siguiente
    deploy (medido el 7-oct: siete horas y media con solo [ERROR]).

    Estática y sobre TODOS los scripts que la aplicación importa — no solo el
    de Walmart: el siguiente que alguien importe desde un servicio queda
    cubierto sin que nadie se acuerde de esta prueba.
    """

    def _scripts_que_importa_la_app(self) -> set[str]:
        import re
        encontrados: set[str] = set()
        archivos = [BACKEND / "main.py"]
        for carpeta in ("services", "routers", "core"):
            archivos += list((BACKEND / carpeta).rglob("*.py"))
        for archivo in archivos:
            texto = archivo.read_text(encoding="utf-8", errors="replace")
            encontrados |= set(re.findall(r"from scripts\.(\w+) import", texto))
            for grupo in re.findall(r"from scripts import ([\w, ]+)", texto):
                encontrados |= {n.strip() for n in grupo.split(",") if n.strip()}
        return encontrados

    @staticmethod
    def _toca_el_logging(fuente: str) -> list[str]:
        """Llamadas a `logging.disable` / `logging.basicConfig` que corren AL
        IMPORTAR: las de nivel de módulo, no las de dentro de una función ni
        las del bloque `if __name__ == "__main__":`."""
        import ast
        halladas = []
        for nodo in ast.parse(fuente).body:
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef, ast.If)):
                continue
            for sub in ast.walk(nodo):
                if (isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr in ("disable", "basicConfig")
                        and isinstance(sub.func.value, ast.Name)
                        and sub.func.value.id == "logging"):
                    halladas.append(f"línea {sub.lineno}: logging.{sub.func.attr}")
        return halladas

    def test_el_detector_si_detecta(self):
        # Sin esto la prueba de abajo podría pasar por no mirar nada.
        malo = "\n".join(["import logging", "logging.disable(logging.WARNING)", ""])
        bueno = "\n".join(["import logging", "", "def main():",
                           "    logging.disable(50)", "",
                           "if __name__ == '__main__':",
                           "    logging.disable(30)", ""])
        self.assertEqual(len(self._toca_el_logging(malo)), 1)
        self.assertEqual(self._toca_el_logging(bueno), [])

    def test_ninguno_toca_el_logging_al_importarse(self):
        scripts = self._scripts_que_importa_la_app()
        self.assertIn("publicar_walmart", scripts)      # la prueba sí mira algo
        culpables = []
        for nombre in sorted(scripts):
            ruta = BACKEND / "scripts" / f"{nombre}.py"
            if ruta.is_file():
                culpables += [f"scripts/{nombre}.py {c}" for c in
                              self._toca_el_logging(ruta.read_text(encoding="utf-8"))]
        self.assertEqual(culpables, [], "Un script importado por el backend cambia "
                         "el logging de TODO el proceso al importarse. Muévelo al "
                         "bloque `if __name__ == \"__main__\":`.")


if __name__ == "__main__":
    unittest.main()
