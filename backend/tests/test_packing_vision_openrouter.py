"""Pruebas de la verificación visual del packing list por OpenRouter (`packing_sku._relacion`).

── QUÉ FIJAN ────────────────────────────────────────────
20-oct-2026: la clave de Gemini de producción está muerta desde el 16-oct y el
paso D del Resolver de costos fallaba EN SILENCIO (no fusionaba nada).

  1. Con OPENROUTER_API_KEY se pregunta a OpenRouter con el mismo modelo
     (google/gemini-2.5-flash), las dos fotos y el prompt de siempre.
  2. Si OpenRouter no contesta y hay GEMINI_API_KEY, se pregunta a Gemini directo.
  3. Sin clave de OpenRouter todo sigue como antes: Gemini directo.
  4. 401/402 no se reintentan; 429/503 sí.
  5. Sin ninguna de las dos claves se omite la verificación (no truena).

── NO SE TOCA NADA REAL ─────────────────────────────────
httpx y asyncio.sleep van simulados.

    cd backend && python -m unittest tests.test_packing_vision_openrouter -v
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

from services import packing_sku as P  # noqa: E402


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buf, "PNG")
    return buf.getvalue()


class _Resp:
    def __init__(self, code: int, contenido: str = ""):
        self.status_code = code
        self.text = contenido

    def json(self):
        return {"choices": [{"message": {"content": self.text}}]}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Cli:
    def __init__(self, respuestas: list):
        self.cola = iter(respuestas)
        self.enviados: list = []

    async def post(self, url, json=None, headers=None, timeout=None, params=None):
        self.enviados.append((url, json, headers))
        return next(self.cola)


def _correr(respuestas: list, or_key="x", gemini_key="", gemini=None):
    cli = _Cli(respuestas)
    llamadas_gemini: list = []

    async def _gem(*_a):
        llamadas_gemini.append(1)
        return gemini

    async def _dormir(_s):
        return None

    with mock.patch.object(P.settings, "openrouter_api_key", or_key), \
         mock.patch.object(P.settings, "gemini_api_key", gemini_key), \
         mock.patch.object(P, "_gemini_relacion", _gem), \
         mock.patch.object(P.asyncio, "sleep", _dormir):
        rel = asyncio.run(P._relacion(cli, _png(), _png(), "colchón A", "colchón B"))
    return rel, cli.enviados, llamadas_gemini


class Relacion(unittest.TestCase):
    def test_openrouter_con_el_mismo_modelo(self):
        rel, enviados, gem = _correr([_Resp(200, '{"relation": "variant", "rationale": "x"}')])
        self.assertEqual(rel, "variant")
        self.assertEqual(gem, [])
        url, body, headers = enviados[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual(body["model"], "google/gemini-2.5-flash")
        partes = body["messages"][0]["content"]
        self.assertEqual([p["type"] for p in partes], ["image_url", "image_url", "text"])
        self.assertTrue(partes[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertIn("colchón A", partes[2]["text"])
        self.assertEqual(headers["Authorization"], "Bearer x")

    def test_json_entre_backticks(self):
        rel, _, _ = _correr([_Resp(200, '```json\n{"relation": "SAME"}\n```')])
        self.assertEqual(rel, "same")

    def test_saturado_reintenta(self):
        rel, enviados, _ = _correr([_Resp(503), _Resp(429), _Resp(200, '{"relation": "different"}')])
        self.assertEqual(rel, "different")
        self.assertEqual(len(enviados), 3)

    def test_sin_credito_no_insiste_y_cae_a_gemini(self):
        rel, enviados, gem = _correr([_Resp(402, "Insufficient credits")],
                                     gemini_key="g", gemini="same")
        self.assertEqual(rel, "same")
        self.assertEqual(len(enviados), 1)
        self.assertEqual(gem, [1])

    def test_falla_y_sin_gemini_devuelve_none(self):
        rel, _, gem = _correr([_Resp(401, "bad key")])
        self.assertIsNone(rel)
        self.assertEqual(gem, [])

    def test_sin_openrouter_es_gemini_como_siempre(self):
        rel, enviados, gem = _correr([], or_key="", gemini_key="g", gemini="different")
        self.assertEqual(rel, "different")
        self.assertEqual(enviados, [])
        self.assertEqual(gem, [1])


class Fusionar(unittest.TestCase):
    def test_sin_ninguna_clave_omite(self):
        conceptos = {"a": {"label": "silla plegable"}, "b": {"label": "silla plegable de metal"}}
        with mock.patch.object(P.settings, "openrouter_api_key", ""), \
             mock.patch.object(P.settings, "gemini_api_key", ""):
            self.assertEqual(asyncio.run(P._fusionar_por_vision(conceptos, {})), 0)


if __name__ == "__main__":
    unittest.main()
