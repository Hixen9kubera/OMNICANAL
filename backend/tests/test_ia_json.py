"""Pruebas de `ia_json.completar_json`: una llamada al LLM que devuelve JSON, dice
cuánto costó y NO cambia de proveedor ni de modelo a escondidas.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
`ia_json` nació porque `ia_generadores._completar` sirve para redactar y no para
clasificar: si DeepSeek falla cae a Claude Opus EN SILENCIO, tira el `usage` y
entrega como buena una respuesta cortada. Un juez que clasifica miles de rivales
encima de eso puede cobrarse entero al modelo caro sin que nadie se entere — ya
vació el saldo una vez (22-sep-2026).

Lo que estas pruebas amarran es el contrato que lo evita:

  1. FALLA CERRADO. Un modelo que no está en `MODELOS` no se llama: sin precio,
     el tope de gasto de quien llama sumaría cero mientras la cuenta corre.
  2. SIN CAÍDA A OTRO PROVEEDOR. Si el modelo pedido falla, la respuesta es
     `ok: False` y el otro proveedor ni se toca, aunque tenga llave.
  3. NUNCA LANZA. Red caída, 4xx, cuerpo ilegible, negativa del modelo: todo
     vuelve como `ok: False` con su `motivo`.
  4. LO CORTADO SE DICE Y SE CONSERVA. `cortado: True` y el texto crudo, para
     que quien llama rescate las entradas completas en vez de perder el lote.
  5. EL COSTO SALE DEL `usage` REAL, con la caché descontada.
  6. CADA MODELO RECIBE SOLO LO QUE ACEPTA. DeepSeek v4 con el razonamiento
     apagado (medido el 30-sep-2026: encendido, 32 de 45 lotes se cortaron antes
     de escribir JSON); Claude Haiku con `temperature`, y los 5.5 —que la
     rechazan con un 400— con el esfuerzo bajo en `extra_body`.

── NO SE LLAMA A NADIE ─────────────────────────────────────────────────────────
`_post_con_plazo` (el POST a DeepSeek) y el SDK de Anthropic van suplantados (el
SDK se importa DENTRO de `_claude`, así que se suplanta el módulo en
`sys.modules`); `PlazoDeReloj` prueba `_post_con_plazo` de verdad contra un
`httpx.stream` falso. `time.sleep` también: las esperas entre reintentos son de
segundos y aquí no aportan nada. Las llaves de estas pruebas son texto inventado.

    cd backend && python -m unittest tests.test_ia_json -v
"""
from __future__ import annotations

import json
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import ia_json as IJ  # noqa: E402

DS = "deepseek-flash"
HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"

# Tabla de precios DE PRUEBA, con números redondos: las cuentas de costo se
# hacen a mano contra estos y no se rompen el día que cambie un precio de lista.
PRECIOS_DE_PRUEBA = {
    "prueba-deepseek": {"proveedor": "deepseek", "entrada": 1.0, "cache": 0.1, "salida": 2.0},
    "prueba-claude": {"proveedor": "claude", "entrada": 1.0, "cache": 0.1, "salida": 2.0},
}


class Resp:
    """Lo mínimo que `_deepseek` lee de una respuesta de httpx."""

    def __init__(self, status: int = 200, cuerpo=None, texto: str = "",
                 ilegible: bool = False) -> None:
        self.status_code = status
        self.text = texto
        self._cuerpo = cuerpo
        self._ilegible = ilegible

    def json(self):
        if self._ilegible:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._cuerpo


def ds(contenido, fin: str = "stop", entrada: int = 1000, cache: int = 0,
       salida: int = 200) -> Resp:
    """Un 200 de DeepSeek con la forma de `/chat/completions`."""
    return Resp(200, {
        "choices": [{"message": {"content": contenido}, "finish_reason": fin}],
        "usage": {"prompt_tokens": entrada, "prompt_cache_hit_tokens": cache,
                  "completion_tokens": salida}})


def bloque(tipo: str, **campos):
    return types.SimpleNamespace(type=tipo, **campos)


def mensaje(bloques, stop: str = "end_turn", entrada: int = 1000, leidos: int = 0,
            salida: int = 200):
    """Un `Message` del SDK de Anthropic, con lo que `_claude` le lee."""
    return types.SimpleNamespace(
        content=bloques, stop_reason=stop,
        usage=types.SimpleNamespace(input_tokens=entrada, cache_read_input_tokens=leidos,
                                    output_tokens=salida))


class _Base(unittest.TestCase):
    """Los dos proveedores suplantados y CON llave: así, cuando una prueba afirma
    «al otro no se le llamó», no es porque le faltara la llave."""

    def setUp(self) -> None:
        self.sdk = mock.MagicMock(name="anthropic")
        self.crear = self.sdk.Anthropic.return_value.messages.create
        self.crear.return_value = mensaje([bloque("text", text='{"a": 1}')])
        self.post = self._poner(mock.patch.object(IJ, "_post_con_plazo"))
        self.post.return_value = ds('{"a": 1}')
        self.dormir = self._poner(mock.patch.object(IJ.time, "sleep"))
        self._poner(mock.patch.dict(sys.modules, {"anthropic": self.sdk}))
        self._poner(mock.patch.dict(IJ.MODELOS, PRECIOS_DE_PRUEBA))
        self._poner(mock.patch.object(IJ.settings, "deepseek_api_key", "llave-inventada-ds"))
        self._poner(mock.patch.object(IJ.settings, "anthropic_api_key", "llave-inventada-cl"))
        self._poner(mock.patch.object(IJ.settings, "deepseek_base_url",
                                      "https://deepseek.invalid/"))

    def _poner(self, parche):
        valor = parche.start()
        self.addCleanup(parche.stop)
        return valor

    def completar(self, modelo: str = DS, **kw):
        return IJ.completar_json("eres un clasificador", "clasifica esto", modelo=modelo, **kw)

    def nadie_fue_llamado(self) -> None:
        self.post.assert_not_called()
        self.sdk.Anthropic.assert_not_called()


class FallaCerrado(_Base):
    """El nombre del modelo llega de una variable de entorno: texto libre."""

    def test_modelo_fuera_del_catalogo_no_llama_a_nadie(self):
        r = self.completar("gpt-inventado")
        self.assertIs(r["ok"], False)
        self.assertIn("gpt-inventado", r["motivo"])
        self.assertEqual(r["usd"], 0.0)
        self.assertIsNone(r["proveedor"])
        self.nadie_fue_llamado()
        self.dormir.assert_not_called()

    def test_un_nombre_parecido_tampoco_pasa(self):
        """No hay coincidencia aproximada: «deepseek-chat» es el alias viejo y
        aquí no tiene precio, así que no corre."""
        for nombre in ("deepseek-chat", "DEEPSEEK-FLASH", "claude-opus-5-5-20260401", ""):
            self.assertIs(self.completar(nombre)["ok"], False, nombre)
        self.nadie_fue_llamado()

    def test_el_fallo_trae_todas_las_llaves_del_contrato(self):
        """Quien llama suma `usd` y lee `uso`/`texto` sin preguntar si existen."""
        r = self.completar("gpt-inventado")
        for llave in ("ok", "motivo", "proveedor", "modelo", "texto", "cortado", "uso", "usd"):
            self.assertIn(llave, r)
        self.assertEqual((r["texto"], r["cortado"], r["uso"]), ("", False, {}))

    def test_sin_llave_de_deepseek_no_sale_a_la_red(self):
        with mock.patch.object(IJ.settings, "deepseek_api_key", ""):
            r = self.completar(DS)
        self.assertIs(r["ok"], False)
        self.assertIn("DEEPSEEK_API_KEY", r["motivo"])
        self.assertEqual(r["proveedor"], "deepseek")
        self.nadie_fue_llamado()

    def test_sin_llave_de_anthropic_no_crea_cliente(self):
        with mock.patch.object(IJ.settings, "anthropic_api_key", ""):
            r = self.completar(HAIKU)
        self.assertIs(r["ok"], False)
        self.assertIn("ANTHROPIC_API_KEY", r["motivo"])
        self.assertEqual(r["proveedor"], "claude")
        self.nadie_fue_llamado()

    def test_la_tabla_real_tiene_precio_y_proveedor_en_cada_modelo(self):
        """«Falla cerrado» descansa en que estar en la tabla SIGNIFIQUE tener
        precio. Y la caché leída es siempre lo más barato: si alguien cruza dos
        columnas, el costo sale por debajo y el tope deja de proteger."""
        for nombre, p in IJ.MODELOS.items():
            self.assertIn(p["proveedor"], ("deepseek", "claude"), nombre)
            self.assertGreater(p["cache"], 0, nombre)
            self.assertLess(p["cache"], p["entrada"], nombre)
            self.assertLess(p["entrada"], p["salida"], nombre)
            self.assertEqual(IJ.proveedor_de(nombre), p["proveedor"])
        self.assertIsNone(IJ.proveedor_de("gpt-inventado"))


class DeepSeekRespondeBien(_Base):
    def test_200_con_json(self):
        self.post.return_value = ds('{"veredictos": [], "unidades_nuestras": 1}',
                                    entrada=1000, cache=0, salida=200)
        r = self.completar(DS)
        self.assertIs(r["ok"], True)
        self.assertEqual(r["datos"], {"veredictos": [], "unidades_nuestras": 1})
        self.assertEqual((r["proveedor"], r["modelo"], r["cortado"]), ("deepseek", DS, False))
        self.assertEqual(r["uso"], {"entrada": 1000, "cache": 0, "salida": 200})
        self.assertEqual(r["usd"], IJ.costo_usd(DS, r["uso"]))
        self.assertGreater(r["usd"], 0)
        self.assertEqual(self.post.call_count, 1)
        self.dormir.assert_not_called()
        self.sdk.Anthropic.assert_not_called()

    def test_lo_que_se_manda(self):
        """Modo JSON y temperature 0: el mismo rival no puede salir «mismo» hoy y
        «otra gama» mañana. La llave viaja en la cabecera, nunca en el cuerpo."""
        self.completar(DS, max_tokens=700, timeout=30.0)
        (url,), kw = self.post.call_args
        self.assertEqual(url, "https://deepseek.invalid/chat/completions")
        self.assertEqual(kw["headers"], {"Authorization": "Bearer llave-inventada-ds"})
        self.assertEqual(kw["timeout"], 30.0)
        cuerpo = kw["json"]
        self.assertEqual(cuerpo["model"], DS)
        self.assertEqual(cuerpo["response_format"], {"type": "json_object"})
        self.assertEqual(cuerpo["temperature"], 0)
        self.assertEqual(cuerpo["messages"],
                         [{"role": "system", "content": "eres un clasificador"},
                          {"role": "user", "content": "clasifica esto"}])
        self.assertNotIn("llave-inventada-ds", repr(cuerpo))

    def test_el_razonamiento_va_apagado_por_omision(self):
        """Encendido se cobra como salida y cuenta contra `max_tokens`: la
        respuesta se corta antes de la primera línea de JSON."""
        self.completar(DS, max_tokens=700)
        cuerpo = self.post.call_args.kwargs["json"]
        self.assertEqual(cuerpo["thinking"], {"type": "disabled"})
        self.assertEqual(cuerpo["max_tokens"], 700)

    def test_razonar_lo_enciende_y_deja_holgura(self):
        self.completar(DS, max_tokens=700, razonar=True)
        cuerpo = self.post.call_args.kwargs["json"]
        self.assertEqual(cuerpo["thinking"], {"type": "enabled"})
        self.assertGreater(cuerpo["max_tokens"], 700, "pensar gasta del mismo tope")

    def test_200_con_cercas(self):
        self.post.return_value = ds('```json\n{"a": 1, "b": [2, 3]}\n```')
        r = self.completar(DS)
        self.assertIs(r["ok"], True)
        self.assertEqual(r["datos"], {"a": 1, "b": [2, 3]})

    def test_200_con_prosa_alrededor(self):
        self.post.return_value = ds('Claro, aquí tienes:\n{"a": {"b": 1}}\nEspero que sirva.')
        r = self.completar(DS)
        self.assertIs(r["ok"], True)
        self.assertEqual(r["datos"], {"a": {"b": 1}})

    def test_una_lista_no_es_un_objeto(self):
        """El contrato promete un dict en `datos`. Una lista suelta es `ok: False`
        y el texto se devuelve para que quien llama vea qué llegó."""
        self.post.return_value = ds("[1, 2, 3]")
        r = self.completar(DS)
        self.assertIs(r["ok"], False)
        self.assertEqual(r["motivo"], "la respuesta no es JSON")
        self.assertEqual(r["texto"], "[1, 2, 3]")
        self.assertNotIn("datos", r)
        self.assertEqual(self.post.call_count, 1, "lo que no es JSON no se reintenta: ya se pagó")


class DeepSeekRespuestaCortada(_Base):
    CORTADA = ('{"nuestro": "x", "veredictos": [{"i": 1, "clase": "mismo"}, '
               '{"i": 2, "clase": "otra_gama"}, {"i": 3, "cla')

    def test_conserva_el_texto_y_marca_cortado(self):
        self.post.return_value = ds(self.CORTADA, fin="length", salida=400)
        r = self.completar(DS)
        self.assertIs(r["ok"], False, "una respuesta cortada NO es una respuesta buena")
        self.assertIs(r["cortado"], True)
        self.assertEqual(r["motivo"], "respuesta cortada")
        self.assertEqual(r["texto"], self.CORTADA, "sin el texto no hay nada que rescatar")
        self.assertNotIn("datos", r)

    def test_lo_cortado_tambien_costo(self):
        """Los tokens se cobraron aunque el JSON no cerrara: si `usd` volviera en
        cero, un lote que se corta siempre gastaría sin tocar el tope."""
        self.post.return_value = ds(self.CORTADA, fin="length", entrada=1000, salida=400)
        r = self.completar(DS)
        self.assertEqual(r["uso"], {"entrada": 1000, "cache": 0, "salida": 400})
        self.assertGreater(r["usd"], 0)
        self.assertEqual(self.post.call_count, 1, "reintentar igual se cortaría igual")

    def test_cortado_con_json_completo_sigue_sirviendo(self):
        """Raro pero posible: el tope cae justo después de la última llave."""
        self.post.return_value = ds('{"a": 1}', fin="length")
        r = self.completar(DS)
        self.assertIs(r["ok"], True)
        self.assertIs(r["cortado"], True)


class DeepSeekReintentos(_Base):
    """Se reintenta lo que puede ser pasajero y nada más."""

    def test_200_con_contenido_vacio_reintenta(self):
        """El modo JSON de DeepSeek a veces contesta 200 sin contenido."""
        self.post.side_effect = [ds(""), ds('{"a": 1}')]
        r = self.completar(DS)
        self.assertIs(r["ok"], True)
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(self.dormir.call_args_list, [mock.call(IJ._ESPERAS[0])])

    def test_vacio_siempre_se_rinde_sin_lanzar(self):
        self.post.return_value = ds("   ")
        r = self.completar(DS, intentos=3)
        self.assertIs(r["ok"], False)
        self.assertIn("vacía", r["motivo"])
        self.assertEqual(self.post.call_count, 3)
        self.assertEqual(self.dormir.call_args_list, [mock.call(e) for e in IJ._ESPERAS[:2]])

    def test_sin_choices_cuenta_como_vacio(self):
        self.post.side_effect = [Resp(200, {"choices": []}), ds('{"a": 1}')]
        self.assertIs(self.completar(DS)["ok"], True)
        self.assertEqual(self.post.call_count, 2)

    def test_429_reintenta(self):
        self.post.side_effect = [Resp(429, texto="rate limit"), ds('{"a": 1}')]
        r = self.completar(DS)
        self.assertIs(r["ok"], True)
        self.assertEqual(self.post.call_count, 2)

    def test_5xx_reintenta(self):
        for codigo in (500, 502, 503, 504):
            self.post.reset_mock()
            self.post.side_effect = [Resp(codigo, texto="upstream"), ds('{"a": 1}')]
            self.assertIs(self.completar(DS)["ok"], True, codigo)
            self.assertEqual(self.post.call_count, 2, codigo)

    def test_400_no_reintenta(self):
        """Un 4xx dice que la PETICIÓN está mal (saldo, modelo, formato): repetirla
        es pagar tiempo de un usuario que está mirando para recibir lo mismo."""
        self.post.return_value = Resp(400, texto='{"error": {"message": "Model Not Exist"}}')
        r = self.completar(DS)
        self.assertIs(r["ok"], False)
        self.assertEqual(self.post.call_count, 1)
        self.dormir.assert_not_called()
        self.assertIn("HTTP 400", r["motivo"])
        self.assertIn("Model Not Exist", r["motivo"], "el cuerpo del 4xx dice POR QUÉ")

    def test_401_y_402_tampoco(self):
        for codigo in (401, 402, 404, 422):
            self.post.reset_mock()
            self.post.return_value = Resp(codigo, texto="no")
            self.assertIs(self.completar(DS)["ok"], False, codigo)
            self.assertEqual(self.post.call_count, 1, codigo)

    def test_el_cuerpo_del_error_se_recorta(self):
        """El motivo acaba en `ops.process_log` y en la pantalla: un HTML de error
        de 40 KB no cabe en ninguno de los dos."""
        self.post.return_value = Resp(400, texto="x" * 5000)
        self.assertLess(len(self.completar(DS)["motivo"]), 300)

    def test_intentos_1_es_una_sola_llamada(self):
        """Un trabajo que alguien está mirando pide una llamada corta."""
        self.post.return_value = Resp(503, texto="upstream")
        r = self.completar(DS, intentos=1)
        self.assertIs(r["ok"], False)
        self.assertEqual(self.post.call_count, 1)
        self.dormir.assert_not_called()

    def test_error_de_red_no_lanza_y_reintenta(self):
        self.post.side_effect = httpx.ConnectError("sin red")
        r = self.completar(DS, intentos=3)
        self.assertIs(r["ok"], False)
        self.assertIn("ConnectError", r["motivo"])
        self.assertEqual(self.post.call_count, 3)

    def test_timeout_y_luego_bien(self):
        self.post.side_effect = [httpx.ReadTimeout("lento"), ds('{"a": 1}')]
        self.assertIs(self.completar(DS)["ok"], True)

    def test_cuerpo_ilegible_no_lanza(self):
        """Un 200 con HTML de un proxy, o con una forma que no es la del API."""
        ilegibles = [
            Resp(200, ilegible=True),                                  # no es JSON
            Resp(200, ["no", "es", "un", "objeto"]),                   # JSON, pero lista
            Resp(200, {"choices": [None]}),                            # opción nula
            Resp(200, {"choices": [{"message": "texto suelto"}]}),     # mensaje sin forma
            Resp(200, {"choices": [{"message": {"content": [{"type": "text"}]}}]}),
        ]
        for resp in ilegibles:
            self.post.reset_mock()
            self.post.side_effect = None
            self.post.return_value = resp
            r = self.completar(DS, intentos=2)
            self.assertIs(r["ok"], False, resp._cuerpo)
            self.assertIn("ilegible", r["motivo"], resp._cuerpo)
            self.assertEqual(self.post.call_count, 2, "lo ilegible puede ser pasajero")

    def test_ilegible_y_luego_bien(self):
        self.post.side_effect = [Resp(200, ilegible=True), ds('{"a": 1}')]
        self.assertIs(self.completar(DS)["ok"], True)


class Costo(_Base):
    """`costo_usd` con la tabla de prueba: 1.00 entrada, 0.10 caché, 2.00 salida
    por millón de tokens."""

    def test_sin_cache(self):
        uso = {"entrada": 1_000_000, "cache": 0, "salida": 500_000}
        self.assertEqual(IJ.costo_usd("prueba-deepseek", uso), 2.0)

    def test_con_cache(self):
        """La caché NO se suma: se RESTA de la entrada y se cobra a su precio.
        600k frescos + 400k de caché + 500k de salida = 0.60 + 0.04 + 1.00."""
        uso = {"entrada": 1_000_000, "cache": 400_000, "salida": 500_000}
        self.assertEqual(IJ.costo_usd("prueba-deepseek", uso), 1.64)

    def test_la_cache_nunca_pasa_de_la_entrada(self):
        """Si el proveedor reportara más caché que entrada, la parte fresca
        saldría NEGATIVA y el costo bajaría al crecer el uso."""
        uso = {"entrada": 100_000, "cache": 500_000, "salida": 0}
        self.assertEqual(IJ.costo_usd("prueba-deepseek", uso), 0.01)

    def test_uso_incompleto_cuenta_cero_no_revienta(self):
        self.assertEqual(IJ.costo_usd("prueba-deepseek", {}), 0.0)
        self.assertEqual(IJ.costo_usd("prueba-deepseek", {"entrada": None, "salida": None}), 0.0)

    def test_sin_precio_o_sin_uso_es_none(self):
        """`None`, no cero: «no sé cuánto costó» no es «fue gratis»."""
        self.assertIsNone(IJ.costo_usd("gpt-inventado", {"entrada": 10, "salida": 10}))
        self.assertIsNone(IJ.costo_usd("prueba-deepseek", None))

    def test_completar_cobra_con_el_usage_de_deepseek(self):
        """600 frescos + 400 de caché + 200 de salida = (600 + 40 + 400) / 1e6."""
        self.post.return_value = ds('{"a": 1}', entrada=1000, cache=400, salida=200)
        r = self.completar("prueba-deepseek")
        self.assertEqual(r["uso"], {"entrada": 1000, "cache": 400, "salida": 200})
        self.assertEqual(r["usd"], 0.00104)

    def test_completar_cobra_con_el_usage_de_claude(self):
        """Anthropic reporta `input_tokens` SIN lo leído de caché; aquí `entrada`
        es el total, para que la misma fórmula sirva a los dos proveedores."""
        self.crear.return_value = mensaje([bloque("text", text='{"a": 1}')],
                                          entrada=600, leidos=400, salida=200)
        r = self.completar("prueba-claude")
        self.assertEqual(r["uso"], {"entrada": 1000, "cache": 400, "salida": 200})
        self.assertEqual(r["usd"], 0.00104)


class NuncaCaeAOtroProveedor(_Base):
    """Lo que vació el saldo el 22-sep-2026. Los dos proveedores tienen llave en
    estas pruebas: si el otro no se toca es porque el código no lo intenta."""

    def test_deepseek_caido_no_llama_a_claude(self):
        self.post.return_value = Resp(503, texto="upstream")
        r = self.completar(DS)
        self.assertIs(r["ok"], False)
        self.assertEqual((r["proveedor"], r["modelo"]), ("deepseek", DS))
        self.sdk.Anthropic.assert_not_called()
        self.crear.assert_not_called()

    def test_deepseek_sin_llave_no_llama_a_claude(self):
        with mock.patch.object(IJ.settings, "deepseek_api_key", ""):
            r = self.completar(DS)
        self.assertIs(r["ok"], False)
        self.sdk.Anthropic.assert_not_called()

    def test_deepseek_sin_json_no_prueba_con_otro(self):
        self.post.return_value = ds("No puedo ayudar con eso.")
        r = self.completar(DS)
        self.assertIs(r["ok"], False)
        self.assertEqual(self.post.call_count, 1)
        self.sdk.Anthropic.assert_not_called()

    def test_deepseek_con_red_caida_no_llama_a_claude(self):
        self.post.side_effect = httpx.ConnectError("sin red")
        self.assertIs(self.completar(DS)["ok"], False)
        self.sdk.Anthropic.assert_not_called()

    def test_claude_caido_no_llama_a_deepseek(self):
        self.crear.side_effect = RuntimeError("overloaded")
        r = self.completar(OPUS)
        self.assertIs(r["ok"], False)
        self.assertEqual((r["proveedor"], r["modelo"]), ("claude", OPUS))
        self.post.assert_not_called()

    def test_claude_no_baja_a_un_modelo_mas_barato(self):
        """Tampoco hay «segundo intento» dentro del mismo proveedor."""
        self.crear.side_effect = RuntimeError("overloaded")
        self.completar(OPUS)
        self.assertEqual(self.crear.call_count, 1)
        self.assertEqual(self.crear.call_args.kwargs["model"], OPUS)

    def test_el_modelo_que_responde_es_el_que_se_pidio(self):
        for modelo in (DS, "deepseek-v4-pro"):
            self.assertEqual(self.completar(modelo)["modelo"], modelo)
            self.assertEqual(self.post.call_args.kwargs["json"]["model"], modelo)
        for modelo in (HAIKU, SONNET, OPUS):
            self.assertEqual(self.completar(modelo)["modelo"], modelo)
            self.assertEqual(self.crear.call_args.kwargs["model"], modelo)


class RamaClaude(_Base):
    def test_json_limpio(self):
        self.crear.return_value = mensaje([bloque("text", text='{"a": 1}')],
                                          entrada=900, leidos=0, salida=120)
        r = self.completar(HAIKU)
        self.assertIs(r["ok"], True)
        self.assertEqual(r["datos"], {"a": 1})
        self.assertEqual((r["proveedor"], r["modelo"], r["cortado"]), ("claude", HAIKU, False))
        self.assertEqual(r["uso"], {"entrada": 900, "cache": 0, "salida": 120})
        self.assertEqual(r["usd"], IJ.costo_usd(HAIKU, r["uso"]))
        self.post.assert_not_called()

    def test_system_y_user_llegan_a_messages_create(self):
        self.completar(HAIKU)
        kw = self.crear.call_args.kwargs
        self.assertEqual(kw["system"], "eres un clasificador")
        self.assertEqual(kw["messages"], [{"role": "user", "content": "clasifica esto"}])

    def test_refusal_es_fallo_aunque_traiga_texto(self):
        """Una negativa llega con HTTP 200 y puede traer texto a medias: hay que
        mirar `stop_reason` ANTES de leer el contenido."""
        self.crear.return_value = mensaje([bloque("text", text='{"veredictos": []}')],
                                          stop="refusal")
        r = self.completar(OPUS)
        self.assertIs(r["ok"], False)
        self.assertIn("declinó", r["motivo"])
        self.assertNotIn("datos", r)
        self.assertEqual(r["proveedor"], "claude")

    def test_una_negativa_tambien_cuesta(self):
        """La negativa se cobra aunque no sirva. Antes volvía con `usd` 0.0 y `uso`
        vacío: el tope de quien llama (`Presupuesto`, `gastado_24h`) no la veía."""
        self.crear.return_value = mensaje([bloque("text", text="")], stop="refusal",
                                          entrada=1500, leidos=500, salida=40)
        r = self.completar("prueba-claude")
        self.assertIs(r["ok"], False)
        self.assertIn("declinó", r["motivo"])
        self.assertEqual(r["uso"], {"entrada": 2000, "cache": 500, "salida": 40})
        # 1,500 frescos a $1 + 500 de caché a $0.10 + 40 de salida a $2, por millón.
        self.assertEqual(r["usd"], 0.00163)

    def test_tokens_raros_no_tiran_una_respuesta_buena(self):
        """El `usage` se lee fuera del `try`: con `int()` un conteo raro lanzaba y
        la red de seguridad devolvía `ok: False` con `uso` vacío, aunque el JSON
        estuviera bien y la llamada se hubiera cobrado."""
        for raro in (float("inf"), "muchos"):
            with self.subTest(input_tokens=raro):
                self.crear.return_value = mensaje([bloque("text", text='{"a": 1}')],
                                                  entrada=raro, leidos=raro, salida=40)
                r = self.completar("prueba-claude")
                self.assertIs(r["ok"], True)
                self.assertEqual(r["datos"], {"a": 1})
                self.assertEqual(r["uso"], {"entrada": 0, "cache": 0, "salida": 40})
                # 40 de salida a $2 por millón.
                self.assertEqual(r["usd"], 0.00008)

    def test_solo_se_leen_los_bloques_de_texto(self):
        """Los modelos que piensan devuelven bloques `thinking` antes del texto.
        Este trae además un atributo `text` a propósito: si el código juntara
        todo lo que tenga `.text`, el JSON quedaría roto."""
        self.crear.return_value = mensaje([
            bloque("thinking", thinking="veamos…", text='{"esto no": '),
            bloque("text", text='{"a": '),
            bloque("tool_use", text="basura", input={}),
            bloque("text", text="1}"),
        ])
        r = self.completar(OPUS)
        self.assertIs(r["ok"], True)
        self.assertEqual(r["texto"], '{"a": 1}')
        self.assertEqual(r["datos"], {"a": 1})

    def test_sin_bloques_de_texto_no_es_json(self):
        for contenido in ([], None, [bloque("thinking", thinking="…")]):
            self.crear.return_value = mensaje(contenido)
            r = self.completar(OPUS)
            self.assertIs(r["ok"], False)
            self.assertEqual(r["motivo"], "la respuesta no es JSON")

    def test_max_tokens_marca_cortado_y_conserva_el_texto(self):
        cortada = '{"veredictos": [{"i": 1, "clase": "mismo"}, {"i": 2, "cl'
        self.crear.return_value = mensaje([bloque("text", text=cortada)], stop="max_tokens")
        r = self.completar(SONNET)
        self.assertIs(r["ok"], False)
        self.assertIs(r["cortado"], True)
        self.assertEqual(r["motivo"], "respuesta cortada")
        self.assertEqual(r["texto"], cortada)
        self.assertGreater(r["usd"], 0)

    def test_haiku_lleva_temperature_y_no_esfuerzo(self):
        self.completar(HAIKU, max_tokens=2000)
        kw = self.crear.call_args.kwargs
        self.assertEqual(kw["temperature"], 0)
        self.assertNotIn("extra_body", kw)
        self.assertEqual(kw["max_tokens"], 2000, "Haiku no piensa solo: sin holgura")

    def test_los_55_llevan_esfuerzo_bajo_y_nunca_temperature(self):
        """Los 5.5 rechazan `temperature` con un 400: si se colara, TODA llamada
        del juez con esos modelos fallaría. Y piensan solos, así que se les baja
        el esfuerzo y se les da holgura: su pensamiento cuenta contra el tope."""
        for modelo in (SONNET, OPUS):
            self.completar(modelo, max_tokens=2000)
            kw = self.crear.call_args.kwargs
            self.assertNotIn("temperature", kw, modelo)
            self.assertNotIn("thinking", kw, modelo)
            self.assertEqual(kw["extra_body"], {"output_config": {"effort": "low"}}, modelo)
            self.assertGreater(kw["max_tokens"], 2000, modelo)

    def test_los_extra_salen_de_la_tabla(self):
        """Lo que llega a `messages.create` es exactamente el `extra` del modelo:
        agregar un modelo a `MODELOS` no exige tocar `_claude`."""
        for modelo in (HAIKU, SONNET, OPUS):
            self.completar(modelo)
            kw = self.crear.call_args.kwargs
            for llave, valor in IJ.MODELOS[modelo]["extra"].items():
                self.assertEqual(kw[llave], valor, (modelo, llave))

    def test_max_tokens_tiene_piso(self):
        self.completar(HAIKU, max_tokens=300)
        self.assertEqual(self.crear.call_args.kwargs["max_tokens"], 1024)

    def test_el_cliente_lleva_timeout_y_reintentos_explicitos(self):
        """Sin esto el SDK usa 10 minutos y 2 reintentos por omisión: media hora
        colgado de un trabajo que alguien está mirando."""
        self.completar(HAIKU, timeout=30.0, intentos=1)
        kw = self.sdk.Anthropic.call_args.kwargs
        self.assertEqual(kw["api_key"], "llave-inventada-cl")
        self.assertEqual(kw["timeout"], 30.0)
        self.assertEqual(kw["max_retries"], 0)
        self.completar(HAIKU, intentos=3)
        self.assertEqual(self.sdk.Anthropic.call_args.kwargs["max_retries"], 2)

    def test_una_excepcion_del_sdk_no_lanza(self):
        self.crear.side_effect = RuntimeError("credit balance is too low")
        r = self.completar(OPUS)
        self.assertIs(r["ok"], False)
        self.assertIn("RuntimeError", r["motivo"])
        self.assertIn("credit balance", r["motivo"])
        self.assertEqual(r["usd"], 0.0)

    def test_si_el_sdk_no_esta_instalado_no_lanza(self):
        with mock.patch.dict(sys.modules, {"anthropic": None}):
            r = self.completar(OPUS)
        self.assertIs(r["ok"], False)
        self.assertEqual(r["proveedor"], "claude")


def cuerpo_ds(contenido: str) -> bytes:
    """Los bytes de un 200 de DeepSeek, como llegan por el cable."""
    return json.dumps({"choices": [{"message": {"content": contenido}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 0,
                                 "completion_tokens": 200}}, ensure_ascii=False).encode()


GOTEO = [b"\n"] * 50          # un proveedor saturado que mantiene viva la conexión


class PlazoDeReloj(unittest.TestCase):
    """El `timeout` de httpx es de INACTIVIDAD: cada trozo que llega reinicia su
    cuenta. DeepSeek saturado gotea líneas vacías para mantener viva una petición
    sin streaming, y con `httpx.post` una llamada «de 30 s» retenía minutos el
    hilo y un turno del juez. `_post_con_plazo` corta por RELOJ.

    Aquí corre `_post_con_plazo` DE VERDAD: `httpx.stream` va suplantado y el
    reloj es falso (cada trozo «tarda» la pausa que se le diga)."""

    def setUp(self) -> None:
        self.reloj = {"t": 1000.0}
        self.leidos = 0
        self.peticiones: list = []
        self._poner(mock.patch.object(IJ, "time", types.SimpleNamespace(
            monotonic=lambda: self.reloj["t"], sleep=lambda s: None)))
        self._poner(mock.patch.dict(IJ.MODELOS, PRECIOS_DE_PRUEBA))
        self._poner(mock.patch.object(IJ.settings, "deepseek_api_key", "llave-inventada-ds"))
        self._poner(mock.patch.object(IJ.settings, "deepseek_base_url",
                                      "https://deepseek.invalid/"))

    def _poner(self, parche):
        valor = parche.start()
        self.addCleanup(parche.stop)
        return valor

    def servir(self, *respuestas) -> None:
        """Cada `httpx.stream` toma la siguiente (código, trozos, pausa[, cabeceras,
        silencio]): `cabeceras` es lo que tardan en llegar, y `silencio`, lo que
        httpx espera sin bytes tras el último trozo antes de lanzar ReadTimeout."""
        pendientes = list(respuestas)

        @contextmanager
        def stream(metodo, url, **kw):
            self.peticiones.append((metodo, url, kw))
            r = pendientes.pop(0)
            codigo, trozos, pausa, cabeceras, silencio = (*r, *(0.0, None)[len(r) - 3:])
            self.reloj["t"] += cabeceras

            def iterar():
                for trozo in trozos:
                    self.reloj["t"] += pausa
                    self.leidos += 1
                    yield trozo
                if silencio is not None:
                    self.reloj["t"] += silencio
                    raise httpx.ReadTimeout("sin bytes")

            yield types.SimpleNamespace(status_code=codigo, iter_bytes=iterar)

        self._poner(mock.patch.object(IJ.httpx, "stream", stream))

    def post(self, timeout: float = 1.0):
        return IJ._post_con_plazo("https://deepseek.invalid/chat/completions", json={"k": 1},
                                  headers={"Authorization": "Bearer x"}, timeout=timeout)

    def test_un_goteo_que_pasa_el_plazo_se_corta(self):
        self.servir((200, GOTEO + [cuerpo_ds('{"a": 1}')], 0.4))
        with self.assertRaises(IJ._PlazoAgotado):
            self.post(timeout=1.0)
        self.assertEqual(self.leidos, 3, "se corta a los 1.2 s de reloj, no a los 20 s")
        self.assertTrue(issubclass(IJ._PlazoAgotado, httpx.TimeoutException),
                        "es un error de red: quien llama lo reintenta como tal")

    def test_unas_cabeceras_tardias_se_cortan_sin_esperar_al_cuerpo(self):
        """Las cabeceras llegan a los 1.5 s y después no llega nada. Si el reloj
        solo se mirara al llegar un trozo, el corte lo daría la inactividad de
        httpx a los 2.5 s (≈3× con una conexión lenta): se corta al entrar."""
        self.servir((200, [], 0.0, 1.5, 1.0))
        with self.assertRaises(IJ._PlazoAgotado):
            self.post(timeout=1.0)
        self.assertEqual(self.reloj["t"] - 1000.0, 1.5)

    def test_un_silencio_tras_el_ultimo_trozo_lo_corta_httpx(self):
        """El contrato REAL, el que dice el docstring: el reloj no corta una espera
        sin bytes. Un trozo a los 0.9 s y luego silencio lo corta la inactividad de
        httpx a los 1.9 s: por debajo de 2×`timeout`, y como error de red que
        `_deepseek` reintenta."""
        self.servir((200, [b"\n"], 0.9, 0.0, 1.0))
        with self.assertRaises(httpx.ReadTimeout):
            self.post(timeout=1.0)
        self.assertLess(self.reloj["t"] - 1000.0, 2.0)

    def test_dentro_del_plazo_devuelve_la_respuesta_entera(self):
        self.servir((200, [b"\n", b"\n", cuerpo_ds('{"a": "ñ"}')], 0.2))
        r = self.post(timeout=1.0)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["choices"][0]["message"]["content"], '{"a": "ñ"}')

    def test_lo_que_se_manda_llega_al_stream(self):
        self.servir((200, [cuerpo_ds("{}")], 0.0))
        self.post(timeout=7.0)
        self.assertEqual(self.peticiones, [(
            "POST", "https://deepseek.invalid/chat/completions",
            {"json": {"k": 1}, "headers": {"Authorization": "Bearer x"}, "timeout": 7.0})])

    def test_completar_json_lo_reintenta_como_error_de_red(self):
        self.servir((200, GOTEO, 0.4), (200, [cuerpo_ds('{"a": 1}')], 0.1))
        r = IJ.completar_json("s", "u", modelo="prueba-deepseek", timeout=1.0, intentos=3)
        self.assertIs(r["ok"], True)
        self.assertEqual(r["datos"], {"a": 1})
        self.assertEqual(len(self.peticiones), 2)

    def test_si_nunca_alcanza_el_motivo_lo_dice_y_el_reloj_manda(self):
        self.servir((200, GOTEO, 0.4), (200, GOTEO, 0.4))
        r = IJ.completar_json("s", "u", modelo="prueba-deepseek", timeout=1.0, intentos=2)
        self.assertIs(r["ok"], False)
        self.assertIn("red: plazo agotado", r["motivo"])
        self.assertLess(self.reloj["t"] - 1000.0, 3.0,
                        "dos intentos de 1 s no duran los 40 s que dura el goteo")

    def test_un_4xx_conserva_su_cuerpo(self):
        """El motivo de un 4xx sale del cuerpo: la respuesta rearmada lo trae."""
        self.servir((400, [b'{"error": {"message": "Model Not Exist"}}'], 0.0))
        r = IJ.completar_json("s", "u", modelo="prueba-deepseek", timeout=1.0)
        self.assertIs(r["ok"], False)
        self.assertIn("HTTP 400", r["motivo"])
        self.assertIn("Model Not Exist", r["motivo"])


class LeerObjeto(unittest.TestCase):
    """`leer_objeto`: el objeto JSON de la respuesta, o `None`."""

    def test_limpio(self):
        self.assertEqual(IJ.leer_objeto('{"a": 1}'), {"a": 1})

    def test_cercas_con_y_sin_etiqueta(self):
        self.assertEqual(IJ.leer_objeto('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(IJ.leer_objeto('```\n{"a": 1}\n```'), {"a": 1})

    def test_prosa_antes_y_despues(self):
        self.assertEqual(IJ.leer_objeto('Va:\n{"a": [1, {"b": 2}]}\nListo.'),
                         {"a": [1, {"b": 2}]})

    def test_lo_que_no_es_objeto_es_none(self):
        for crudo in ("", None, "sin llaves", "[1, 2]", '"texto"', "12", '{"a": 1'):
            self.assertIsNone(IJ.leer_objeto(crudo), crudo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
