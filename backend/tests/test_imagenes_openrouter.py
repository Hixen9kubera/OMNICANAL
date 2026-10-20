"""Pruebas del editor de imágenes por OpenRouter (`imagenes_editor._editar`).

── QUÉ FIJAN ────────────────────────────────────────────
Brandon, 19-oct-2026: editar con el modelo más barato que aguanta cada tipo, en
vez de Gemini 3 Pro (~$0.134) para todo.

  1. Cada tipo arranca en su modelo: «Quitar fondo» en FLUX Klein ($0.009); los
     otros tres en Seedream ($0.018). Con varios flags manda el más exigente.
  2. Si un modelo falla (moderación, sin imagen…) sube al siguiente; el último
     es Gemini 3 Pro.
  3. 401/402 (clave mala, sin crédito) cortan la cadena y caen a Gemini directo.
  4. Sin OPENROUTER_API_KEY todo sigue como antes: Gemini directo.
  5. `_openrouter_edit` lee la respuesta real de `/images` (b64_json + cost) y
     reintenta UNA vez con el mismo modelo ante saturación.
  6. «Analizando persona»: OpenRouter si contesta (NO_PERSON incluido); si no,
     Gemini.

── NO SE TOCA NADA REAL ─────────────────────────────────
httpx, asyncio.sleep y Gemini van simulados.

    cd backend && python -m unittest tests.test_imagenes_openrouter -v
"""
from __future__ import annotations

import asyncio
import base64
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import imagenes_editor as E  # noqa: E402

FONDO = {"quitar_fondo": True, "traducir_texto": False, "quitar_logos": False, "cambiar_modelo": False}
TRADUCIR = {"quitar_fondo": False, "traducir_texto": True, "quitar_logos": False, "cambiar_modelo": False}


def _png(w: int, h: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PNG")
    return buf.getvalue()


class Cadena(unittest.TestCase):
    def test_fondo_arranca_en_klein(self):
        self.assertEqual(E._cadena_openrouter(FONDO), [E._OR_KLEIN, E._OR_SEEDREAM, E._OR_PRO])

    def test_los_otros_tres_arrancan_en_seedream(self):
        for k in ("traducir_texto", "quitar_logos", "cambiar_modelo"):
            flags = {**FONDO, "quitar_fondo": False, k: True}
            self.assertEqual(E._cadena_openrouter(flags), [E._OR_SEEDREAM, E._OR_PRO], k)

    def test_con_varios_manda_el_mas_exigente(self):
        self.assertEqual(E._cadena_openrouter({**FONDO, "traducir_texto": True}),
                         [E._OR_SEEDREAM, E._OR_PRO])


class Proporcion(unittest.TestCase):
    def test_cercana(self):
        self.assertEqual(E._proporcion(_png(800, 800)), "1:1")
        self.assertEqual(E._proporcion(_png(1600, 1205)), "4:3")   # MUE-0361-MUL
        self.assertEqual(E._proporcion(_png(383, 171)), "21:9")    # ROP-0121-AZL-L
        self.assertEqual(E._proporcion(_png(750, 1000)), "3:4")

    def test_ilegible_es_cuadrada(self):
        self.assertEqual(E._proporcion(b"no es una imagen"), "1:1")


def _correr_editar(guion: list, flags=TRADUCIR, gemini=(b"gem", "image/png", None),
                   or_key="x", gemini_key="g"):
    """`guion`: lo que devuelve `_openrouter_edit` en cada llamada."""
    llamados: list[str] = []
    avisos: list[str] = []
    cola = iter(guion)

    async def _or(img_b64, mime, prompt, modelo, proporcion, espera=180.0):
        llamados.append(modelo)
        return next(cola)

    async def _gem(*_a, **_k):
        llamados.append("gemini-directo")
        return gemini

    with mock.patch.object(E, "_openrouter_edit", _or), \
         mock.patch.object(E, "_gemini_edit", _gem), \
         mock.patch.object(E.settings, "openrouter_api_key", or_key), \
         mock.patch.object(E.settings, "gemini_api_key", gemini_key):
        res = asyncio.run(E._editar(_png(800, 800), "b64", "image/png", "p", flags,
                                    avisar=avisos.append))
    return res, llamados, avisos


class Editar(unittest.TestCase):
    def test_primer_modelo_responde(self):
        (foto, mime, err, modelo), llamados, _ = _correr_editar(
            [(b"img", "image/jpeg", None, 0.018, 200)])
        self.assertEqual((foto, mime, err), (b"img", "image/jpeg", None))
        self.assertEqual(modelo, f"openrouter:{E._OR_SEEDREAM}")
        self.assertEqual(llamados, [E._OR_SEEDREAM])

    def test_moderacion_sube_al_siguiente(self):
        (foto, _, err, modelo), llamados, avisos = _correr_editar([
            (None, "image/png", "seedream HTTP 400: content moderation", None, 400),
            (b"pro", "image/png", None, 0.136, 200)])
        self.assertEqual(foto, b"pro")
        self.assertEqual(modelo, f"openrouter:{E._OR_PRO}")
        self.assertEqual(llamados, [E._OR_SEEDREAM, E._OR_PRO])
        self.assertIn("Probando con otro modelo", avisos[0])

    def test_sin_credito_cae_a_gemini_directo(self):
        (foto, _, _, modelo), llamados, _ = _correr_editar([
            (None, "image/png", "HTTP 402: Insufficient credits", None, 402)])
        self.assertEqual(foto, b"gem")
        self.assertEqual(modelo, E.GEMINI_MODEL)
        self.assertEqual(llamados, [E._OR_SEEDREAM, "gemini-directo"])   # no prueba el Pro

    def test_toda_la_cadena_falla_y_no_hay_gemini(self):
        (foto, _, err, modelo), llamados, _ = _correr_editar(
            [(None, "image/png", "a", None, 400), (None, "image/png", "b", None, 502)],
            gemini_key="")
        self.assertIsNone(foto)
        self.assertEqual(err, "a · b")
        self.assertEqual(llamados, [E._OR_SEEDREAM, E._OR_PRO])

    def test_sin_clave_de_openrouter_es_gemini_como_siempre(self):
        (foto, _, _, modelo), llamados, _ = _correr_editar([], or_key="")
        self.assertEqual(foto, b"gem")
        self.assertEqual(modelo, E.GEMINI_MODEL)
        self.assertEqual(llamados, ["gemini-directo"])


class _Resp:
    def __init__(self, code: int, data: dict):
        self.status_code = code
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


def _cliente(respuestas: list, enviados: list):
    cola = iter(respuestas)

    class _Cli:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, url, json=None, headers=None):
            enviados.append((url, json, headers))
            return next(cola)

    return _Cli


def _correr_or(respuestas: list):
    enviados: list = []
    esperas: list = []

    async def _dormir(s):
        esperas.append(s)

    with mock.patch.object(E.httpx, "AsyncClient", _cliente(respuestas, enviados)), \
         mock.patch.object(E.asyncio, "sleep", _dormir), \
         mock.patch.object(E.settings, "openrouter_api_key", "sk-prueba"):
        res = asyncio.run(E._openrouter_edit("QUJD", "image/webp", "p", E._OR_SEEDREAM, "4:3"))
    return res, enviados, esperas


_OK = {"data": [{"b64_json": base64.b64encode(b"hola").decode(), "media_type": "image/jpeg"}],
       "usage": {"cost": 0.018}}


class OpenRouterEdit(unittest.TestCase):
    def test_respuesta_real(self):
        (foto, mime, err, costo, http), enviados, _ = _correr_or([_Resp(200, _OK)])
        self.assertEqual((foto, mime, err, costo, http), (b"hola", "image/jpeg", None, 0.018, 200))
        url, body, headers = enviados[0]
        self.assertTrue(url.endswith("/images"))
        self.assertEqual(body["input_references"][0]["image_url"]["url"],
                         "data:image/webp;base64,QUJD")
        self.assertEqual((body["resolution"], body["aspect_ratio"]), ("1K", "4:3"))
        self.assertEqual(headers["Authorization"], "Bearer sk-prueba")

    def test_saturado_reintenta_una_vez_con_el_mismo(self):
        (foto, *_), enviados, esperas = _correr_or([_Resp(503, {}), _Resp(200, _OK)])
        self.assertEqual(foto, b"hola")
        self.assertEqual(esperas, [E._OR_ESPERA_SATURADO])
        self.assertEqual(len(enviados), 2)

    def test_moderacion_devuelve_el_mensaje(self):
        (foto, _, err, _, http), _, esperas = _correr_or([_Resp(400, {"error": {
            "message": "Black Forest Labs blocked this request through content moderation."}})])
        self.assertIsNone(foto)
        self.assertEqual(http, 400)
        self.assertIn("content moderation", err)
        self.assertEqual(esperas, [])

    def test_200_sin_imagen(self):
        (foto, _, err, _, _), _, _ = _correr_or([_Resp(200, {"data": []})])
        self.assertIsNone(foto)
        self.assertIn("sin imagen", err)


class DescribirPersona(unittest.TestCase):
    def _correr(self, resp, gemini="desc-gemini"):
        enviados: list = []

        async def _gem(*_a):
            return gemini

        with mock.patch.object(E.httpx, "AsyncClient", _cliente([resp], enviados)), \
             mock.patch.object(E, "_gemini_describe_person", _gem), \
             mock.patch.object(E.settings, "openrouter_api_key", "x"):
            return asyncio.run(E._describir_persona("QUJD", "image/webp"))

    def test_contesta_openrouter(self):
        r = _Resp(200, {"choices": [{"message": {"content": " woman approximately 30 years old "}}]})
        self.assertEqual(self._correr(r), "woman approximately 30 years old")

    def test_no_person_no_pregunta_a_gemini(self):
        r = _Resp(200, {"choices": [{"message": {"content": "NO_PERSON"}}]})
        self.assertIsNone(self._correr(r))

    def test_si_falla_pregunta_a_gemini(self):
        self.assertEqual(self._correr(_Resp(402, {})), "desc-gemini")


if __name__ == "__main__":
    unittest.main()
