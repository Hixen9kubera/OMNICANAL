"""Pruebas del reintento por SATURACIÓN de Gemini (`imagenes_editor._gemini_edit`).

── QUÉ FIJAN ────────────────────────────────────────────
Andrea, 15-oct-2026: «¿por qué no se me procesan las imágenes de TEC-0289-NEG?».
7 de 8 intentos murieron en `Gemini HTTP 503: This model is currently experiencing
high demand` y el editor sólo daba UN reintento a los 8 s.

  1. 429/500/502/503/504 esperan 10, 30 y 60 s y reintentan, SIN gastar `retries`.
  2. Cada espera avisa a la etiqueta de la imagen («La IA está saturada; reintento…»).
  3. Agotadas las esperas, sigue el camino de siempre y devuelve el error de Google.
  4. Lo que NO es saturación (400, ReadTimeout) se comporta como antes.

── NO SE TOCA NADA REAL ─────────────────────────────────
httpx y asyncio.sleep van simulados.

    cd backend && python -m unittest tests.test_imagenes_saturacion -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import imagenes_editor as E  # noqa: E402

_FOTO = {"candidates": [{"content": {"parts": [
    {"inlineData": {"data": "aGk=", "mimeType": "image/png"}}]}}]}


class _Resp:
    def __init__(self, code: int):
        self.status_code = code
        self.text = "This model is currently experiencing high demand"

    def json(self):
        return _FOTO


def _cliente(respuestas: list):
    cola = iter(respuestas)

    class _Cli:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, *_a, **_k):
            r = next(cola)
            if isinstance(r, Exception):
                raise r
            return _Resp(r)

    return _Cli


def _correr(respuestas: list, **kw):
    esperas: list[float] = []
    avisos: list[str] = []

    async def _dormir(s):
        esperas.append(s)

    with mock.patch.object(E.httpx, "AsyncClient", _cliente(respuestas)), \
         mock.patch.object(E.asyncio, "sleep", _dormir), \
         mock.patch.object(E.settings, "gemini_api_key", "x"):
        foto, _mime, err = asyncio.run(
            E._gemini_edit("b64", "image/png", "p", avisar=avisos.append, **kw))
    return foto, err, esperas, avisos


class SaturacionGemini(unittest.TestCase):
    def test_503_espera_y_sale(self):
        foto, err, esperas, avisos = _correr([503, 503, 200])
        self.assertEqual(foto, b"hi")
        self.assertIsNone(err)
        self.assertEqual(esperas, [10, 30])
        self.assertEqual(len(avisos), 2)
        self.assertIn("saturada", avisos[0])

    def test_saturado_siempre_se_rinde_con_el_error_de_google(self):
        foto, err, esperas, _ = _correr([503] * 5)
        self.assertIsNone(foto)
        self.assertTrue(err.startswith("Gemini HTTP 503"))
        self.assertEqual(esperas, [10, 30, 60, 8])

    def test_no_gasta_el_unico_intento_del_candado_del_marco(self):
        foto, _, esperas, _ = _correr([429, 200], retries=1)
        self.assertEqual(foto, b"hi")
        self.assertEqual(esperas, [10])

    def test_400_no_es_saturacion(self):
        foto, err, esperas, avisos = _correr([400, 400])
        self.assertIsNone(foto)
        self.assertTrue(err.startswith("Gemini HTTP 400"))
        self.assertEqual(esperas, [8])
        self.assertEqual(avisos, [])

    def test_timeout_no_es_saturacion(self):
        foto, _, esperas, avisos = _correr([TimeoutError("lento"), 200])
        self.assertEqual(foto, b"hi")
        self.assertEqual(esperas, [8])
        self.assertEqual(avisos, [])


if __name__ == "__main__":
    unittest.main()
