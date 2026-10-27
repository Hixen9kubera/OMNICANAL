"""
El título y la descripción de Crear salen en ESPAÑOL: OpenRouter viendo la foto
(GPT-6 Luna, luego Gemini 2.5 Flash), DeepSeek solo con texto, Claude directo con
la foto, y si nadie contesta, `None` — nunca el título de Alibaba, que está en
inglés.

POR QUÉ (27-oct-2026). Desde el 22-oct 02:02 UTC la llave de Anthropic de Railway
da 401 y este paso era SOLO Claude: 245 altas nacieron con el título crudo de
Alibaba ("… - Buy Product on Alibaba.com") y el resumen decía «completado».

    cd backend && python -m unittest tests.test_crear_titulo_espanol -v
"""
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import anthropic  # noqa: F401 — se importa ANTES de suplantar httpx.AsyncClient: hereda de él
import httpx
from PIL import Image

from services import crear_producto as cp

URL = "https://www.alibaba.com/product-detail/Kids-Pretend-Play-Cleaning-Set_1.html"
TITULO_EN = "Kids Pretend Play Cleaning Set - Buy Product on Alibaba.com"
FOTO = "https://s.alicdn.com/foto.jpg"
LUNA, GEMINI = cp.OPENROUTER_MODELOS_SEO
_AsyncClientReal = httpx.AsyncClient


def _jpeg(lado=1600):
    b = io.BytesIO()
    Image.new("RGB", (lado, lado // 2), "pink").save(b, "JPEG")
    return b.getvalue()


def _respuesta_or(titulo, descripcion="<p>d</p>", costo=0.0004):
    contenido = json.dumps({"titulo": titulo, "descripcion": descripcion}, ensure_ascii=False)
    return httpx.Response(200, json={"choices": [{"message": {"content": contenido}}],
                                     "usage": {"cost": costo}})


class _OpenRouterFalso:
    """Transporte de httpx: sirve la foto y contesta por modelo lo que se le dé."""

    def __init__(self, por_modelo, foto_status=200):
        self.por_modelo, self.foto_status = por_modelo, foto_status
        self.cuerpos = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(self.foto_status, content=_jpeg())
        cuerpo = json.loads(req.content)
        self.cuerpos.append(cuerpo)
        r = self.por_modelo.get(cuerpo["model"], httpx.Response(500, text="caído"))
        return r() if callable(r) else r

    def cliente(self, **kw):
        return _AsyncClientReal(transport=httpx.MockTransport(self), **kw)


def _respuesta_claude(texto):
    return SimpleNamespace(stop_reason="end_turn",
                           content=[SimpleNamespace(type="text", text=texto)])


class _ClaudeFalso:
    """`AsyncAnthropic` de mentira: graba lo que se le pidió y contesta lo dado."""

    def __init__(self, respuesta=None, error=None):
        self.pedidos = []
        self._respuesta, self._error = respuesta, error
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kw):
        self.pedidos.append(kw)
        if self._error:
            raise self._error
        return self._respuesta

    def __call__(self, **_kw):
        return self


class _Base(unittest.IsolatedAsyncioTestCase):
    async def _correr(self, *, openrouter=None, ds=None, claude=None,
                      llaves=("sk-or", "sk-ds", "sk-ant"), imagen=FOTO, detalles=""):
        openrouter = openrouter or _OpenRouterFalso({})
        ds = ds or mock.AsyncMock(side_effect=AssertionError("DeepSeek no debía llamarse"))
        claude = claude or _ClaudeFalso(error=AssertionError("Claude no debía llamarse"))
        with mock.patch.multiple(cp.settings, openrouter_api_key=llaves[0],
                                 deepseek_api_key=llaves[1], anthropic_api_key=llaves[2]), \
                mock.patch.object(cp.httpx, "AsyncClient", openrouter.cliente), \
                mock.patch("services.ml_atributos._deepseek_json", ds), \
                mock.patch("anthropic.AsyncAnthropic", claude):
            return await cp.titulo_descripcion_ia(TITULO_EN, imagen, URL, detalles)


class OpenRouterPrimero(_Base):
    async def test_luna_ve_la_foto_y_nadie_mas_se_llama(self):
        orf = _OpenRouterFalso({LUNA: _respuesta_or("Set de limpieza de juguete para niños")})
        r = await self._correr(openrouter=orf, detalles="<b>Material:</b> PP")
        self.assertEqual(r["titulo"], "Set de limpieza de juguete para niños")
        self.assertEqual(r["modelo"], LUNA)
        (cuerpo,) = orf.cuerpos
        self.assertEqual(cuerpo["response_format"], {"type": "json_object"})
        # Luna rechaza el modo JSON (400) si el mensaje no dice «JSON».
        self.assertIn("JSON", cuerpo["messages"][0]["content"])
        foto, texto = cuerpo["messages"][1]["content"]
        self.assertTrue(foto["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertIn("Observa la imagen", texto["text"])
        self.assertIn("Material: PP", texto["text"])
        self.assertIn("acentos", texto["text"])  # la regla vieja hacía quitarlos

    async def test_la_foto_se_reduce_antes_de_mandarla(self):
        orf = _OpenRouterFalso({LUNA: _respuesta_or("Set de limpieza")})
        await self._correr(openrouter=orf)
        url = orf.cuerpos[0]["messages"][1]["content"][0]["image_url"]["url"]
        im = Image.open(io.BytesIO(__import__("base64").b64decode(url.split(",", 1)[1])))
        self.assertEqual(max(im.size), cp._FOTO_PX)

    async def test_si_la_foto_no_baja_viaja_la_url(self):
        orf = _OpenRouterFalso({LUNA: _respuesta_or("Set de limpieza")}, foto_status=404)
        await self._correr(openrouter=orf)
        self.assertEqual(orf.cuerpos[0]["messages"][1]["content"][0]["image_url"]["url"], FOTO)

    async def test_luna_cae_y_contesta_gemini(self):
        orf = _OpenRouterFalso({LUNA: httpx.Response(503, text="sobrecarga"),
                                GEMINI: _respuesta_or("Set de limpieza infantil")})
        r = await self._correr(openrouter=orf)
        self.assertEqual((r["titulo"], r["modelo"]), ("Set de limpieza infantil", GEMINI))
        self.assertEqual([c["model"] for c in orf.cuerpos], [LUNA, GEMINI])

    async def test_sin_credito_no_insiste_y_pasa_a_deepseek(self):
        # 402 es de la CUENTA: el segundo modelo daría lo mismo (pasó el 21-oct).
        orf = _OpenRouterFalso({LUNA: httpx.Response(402, text="Insufficient credits"),
                                GEMINI: _respuesta_or("no debía llamarse")})
        ds = mock.AsyncMock(return_value={"titulo": "Set de limpieza", "descripcion": "d"})
        r = await self._correr(openrouter=orf, ds=ds)
        self.assertEqual([c["model"] for c in orf.cuerpos], [LUNA])
        self.assertTrue(r["modelo"].startswith("deepseek/"))
        self.assertNotIn("Observa la imagen", ds.await_args.args[1])  # DeepSeek no ve fotos

    async def test_salto_de_linea_crudo_en_la_descripcion_se_acepta(self):
        crudo = '{"titulo": "Set de limpieza", "descripcion": "<p>uno</p>\n<p>dos</p>"}'
        orf = _OpenRouterFalso({LUNA: httpx.Response(200, json={
            "choices": [{"message": {"content": crudo}}]})})
        r = await self._correr(openrouter=orf)
        self.assertEqual(r["descripcion"], "<p>uno</p>\n<p>dos</p>")


class Respaldos(_Base):
    async def test_sin_llave_de_openrouter_va_a_deepseek(self):
        ds = mock.AsyncMock(return_value={"titulo": "Set de limpieza", "descripcion": "d"})
        orf = _OpenRouterFalso({})
        r = await self._correr(openrouter=orf, ds=ds, llaves=("", "sk-ds", "sk-ant"))
        self.assertEqual(orf.cuerpos, [])
        self.assertEqual(r["titulo"], "Set de limpieza")

    async def test_deepseek_sin_titulo_cuenta_como_fallo_y_sigue_claude_con_foto(self):
        claude = _ClaudeFalso(_respuesta_claude('{"titulo": "Set de limpieza", "descripcion": ""}'))
        ds = mock.AsyncMock(return_value={"descripcion": "sin título"})
        r = await self._correr(ds=ds, claude=claude)
        self.assertEqual((r["titulo"], r["modelo"]), ("Set de limpieza", cp.CLAUDE_MODEL))
        contenido = claude.pedidos[0]["messages"][0]["content"]
        self.assertEqual(contenido[0]["type"], "image")

    async def test_sin_llave_de_deepseek_ni_se_intenta(self):
        # Sin llave, `_deepseek_json` caería SOLA a Claude sin foto: aquí se salta.
        claude = _ClaudeFalso(_respuesta_claude('{"titulo": "Set de limpieza", "descripcion": ""}'))
        ds = mock.AsyncMock(side_effect=AssertionError("no debía llamarse"))
        r = await self._correr(ds=ds, claude=claude, llaves=("", "", "sk-ant"))
        self.assertEqual(r["titulo"], "Set de limpieza")
        ds.assert_not_awaited()

    async def test_ninguna_ia_devuelve_none_y_no_el_titulo_de_alibaba(self):
        # El caso del 22-oct, completo: OpenRouter caído, DeepSeek caído, Claude 401.
        claude = _ClaudeFalso(error=RuntimeError("401 invalid x-api-key"))
        ds = mock.AsyncMock(side_effect=RuntimeError("timeout"))
        r = await self._correr(ds=ds, claude=claude)
        self.assertIsNone(r)
        self.assertEqual(len(claude.pedidos), 3)  # con foto, sin foto, sin foto
        self.assertNotIn("Observa la imagen",
                         claude.pedidos[1]["messages"][0]["content"][0]["text"])


class Piezas(unittest.TestCase):
    def test_detalles_sin_html_y_recortados(self):
        d = cp._detalles_proveedor("<p>Color:\n<b>Rosa</b></p>" + "x" * 5000)
        self.assertTrue(d.startswith("Color: Rosa "))
        self.assertEqual(len(d), cp._DETALLES_MAX)

    def test_objeto_raro_no_rompe(self):
        self.assertIsNone(cp._seo_de_objeto(None))
        self.assertIsNone(cp._seo_de_objeto(["titulo"]))
        self.assertIsNone(cp._seo_de_objeto({"titulo": "  "}))
        self.assertEqual(cp._seo_de_objeto({"titulo": 123}), {"titulo": "123", "descripcion": ""})

    def test_parse_con_ruido_sigue_igual(self):
        self.assertEqual(cp._parse_seo_json('ruido {"titulo": "T", "descripcion": "D"} ruido'),
                         {"titulo": "T", "descripcion": "D"})
        self.assertIsNone(cp._parse_seo_json("no es json"))

    def test_foto_ilegible_no_rompe(self):
        self.assertIsNone(cp._foto_data_url(b"no es una imagen"))


if __name__ == "__main__":
    unittest.main()
