"""Pruebas de REGRESIÓN del juez de rivales y de la mejora de términos.

── QUÉ FIJAN ───────────────────────────────────────────────────────────────────
Los módulos nuevos (`ia_json`, `competencia_juez`, `competencia_mejora` y sus
rutas) ya tienen sus pruebas de contrato. Estas son otra cosa: cada clase de este
archivo amarra UN defecto que la primera ronda de pruebas encontró y que ya se
corrigió, para que no vuelva. Por eso cada docstring dice qué pasaba ANTES y por
qué importaba; si una de estas se pone roja, alguien deshizo un arreglo.

Casi todos son de la misma familia: dinero que se gasta sin que el tope lo vea, o
un «no sé» que se guardaba como si fuera una respuesta.

    R01  ia_json      los 200 vacíos o ilegibles que se reintentan SÍ cuestan
    R02  ia_json      nunca lanza (usage raro, tokens raros, URL mal escrita)
    R03  ia_json      el cliente de Anthropic lleva su dirección EXPLÍCITA
    R04  juez         un lote descartado por sospechoso deja un motivo legible
    R05  juez         `resumen` avisa cuando no hay título nuestro
    R06  juez         no poder GUARDAR no es un fallo del proveedor
    R07  juez         `_guardar` pasa por el reintento del pooler
    R08  mejora       `llave` conserva la ñ
    R09  mejora       un juicio INCOMPLETO queda en `error`, no en `sin_mejora`
    R10  mejora       `error` y `bloqueado` no castigan al SKU
    R11  mejora       si el paso 3 revienta, el gasto queda en la bitácora
    R12  mejora       `sin_cubrir` y el tope de páginas
    R13  mejora       `sin_mejora` no depende del orden; `usd_ia` incluye al juez
    R14  rutas        `_juicio_de` no deja llaves sueltas si falla a medias
    R15  rutas        POST /juez/juzgar dice por qué no juzgó (502 / 503 / 409)

── SIN RED Y SIN BASE ──────────────────────────────────────────────────────────
`_post_con_plazo` (el POST de `_deepseek`), el SDK de Anthropic, `supabase_db`,
Apify y el LLM van suplantados. OJO: suplantar `httpx.post` ya NO corta la red:
`_deepseek` usa `httpx.stream`.
El archivo no importa nada de las otras suites: trae sus propias fábricas, para
que correr `discover` o este módulo solo dé exactamente lo mismo.

Los títulos, términos y SKUs son INVENTADOS: el repo es público.

    cd backend && python -m unittest tests.test_competencia_regresiones -v
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import os
import re
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from config import settings  # noqa: E402
from routers import competencia as RC  # noqa: E402
from services import competencia_juez as J  # noqa: E402
from services import competencia_mejora as CM  # noqa: E402
from services import ia_json as IJ  # noqa: E402

MODELO = "deepseek-flash"
HAIKU = "claude-haiku-4-5"
LOG_JUEZ = "omnicanal.competencia.juez"
LOG_ROUTER = "omnicanal.routers.competencia"
LOG_SUPABASE = "omnicanal.supabase"


# ═════════════════════════════════════════════════════════════════════════════
#  ia_json
# ═════════════════════════════════════════════════════════════════════════════

# Precios DE PRUEBA con números redondos (USD por millón): las cuentas de abajo se
# hacen a mano contra estos y no se rompen el día que cambie un precio de lista.
BARATO = "prueba-deepseek"
PRECIOS_DE_PRUEBA = {
    BARATO: {"proveedor": "deepseek", "entrada": 1.0, "cache": 0.1, "salida": 2.0},
}
BUENO = '{"veredictos": []}'


class Resp:
    """Lo mínimo que `_deepseek` lee de una respuesta de httpx."""

    def __init__(self, cuerpo: Any = None, *, status: int = 200, ilegible: bool = False) -> None:
        self.status_code = status
        self.text = ""
        self._cuerpo = cuerpo
        self._ilegible = ilegible

    def json(self) -> Any:
        if self._ilegible:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._cuerpo


def ds(contenido: Any, *, entrada: int = 1000, salida: int = 200, **cambios: Any) -> Resp:
    """Un 200 de DeepSeek con la forma de `/chat/completions`. `cambios` pisa
    llaves del cuerpo (así se arma un `usage` o unos `choices` torcidos)."""
    return Resp({"choices": [{"message": {"content": contenido}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": entrada, "prompt_cache_hit_tokens": 0,
                           "completion_tokens": salida}, **cambios})


class _IA(unittest.TestCase):
    """DeepSeek suplantado y CON llave; las esperas entre reintentos no duermen.
    Se suplanta `_post_con_plazo`, el POST con plazo de reloj de `_deepseek`: su
    firma es la de `httpx.post`, así que lo de abajo no cambia."""

    def setUp(self) -> None:
        self.post = self._poner(mock.patch.object(IJ, "_post_con_plazo"))
        self.post.return_value = ds(BUENO)
        self.dormir = self._poner(mock.patch.object(IJ.time, "sleep"))
        self._poner(mock.patch.dict(IJ.MODELOS, PRECIOS_DE_PRUEBA))
        self._poner(mock.patch.object(IJ.settings, "deepseek_api_key", "llave-inventada-ds"))
        self._poner(mock.patch.object(IJ.settings, "anthropic_api_key", "llave-inventada-cl"))
        self._poner(mock.patch.object(IJ.settings, "deepseek_base_url",
                                      "https://deepseek.invalid/"))

    def _poner(self, parche: Any) -> Any:
        valor = parche.start()
        self.addCleanup(parche.stop)
        return valor

    def completar(self, modelo: str = BARATO, **kw: Any) -> dict[str, Any]:
        return IJ.completar_json("eres un clasificador", "clasifica esto", modelo=modelo, **kw)


class R01_LosReintentosDescartadosTambienCuestan(_IA):
    """DEFECTO: un 200 con contenido vacío o ilegible se descartaba y se
    reintentaba, pero sus tokens —que el proveedor SÍ cobró— no se contaban. Si los
    tres intentos fallaban, `usd` salía 0.0 y `uso` vacío; si el último acertaba,
    solo se reportaba el de ese.

    POR QUÉ IMPORTABA: el tope de gasto de quien llama (`Presupuesto`,
    `gastado_24h`) suma lo que `completar_json` le dice. Un fallo que reporta cero
    lo deja ciego justo cuando el proveedor anda mal y más se reintenta."""

    def test_tres_vacios_fallan_pero_cobran_los_tres(self):
        self.post.side_effect = [ds("", entrada=5000, salida=300) for _ in range(3)]
        r = self.completar()
        self.assertIs(r["ok"], False)
        self.assertIn("vacía", r["motivo"])
        self.assertEqual(self.post.call_count, 3)
        self.assertEqual(r["uso"], {"entrada": 15000, "cache": 0, "salida": 900})
        # 15,000 de entrada a $1 + 900 de salida a $2, por millón.
        self.assertAlmostEqual(r["usd"], 0.0168)
        self.assertGreater(r["usd"], 0)

    def test_un_vacio_y_luego_uno_bueno_cobra_las_dos_llamadas(self):
        self.post.side_effect = [ds("", entrada=5000, salida=300),
                                 ds(BUENO, entrada=1000, salida=200)]
        r = self.completar()
        self.assertIs(r["ok"], True)
        self.assertEqual(r["uso"], {"entrada": 6000, "cache": 0, "salida": 500})
        self.assertAlmostEqual(r["usd"], 0.007)
        self.assertGreater(r["usd"], IJ.costo_usd(BARATO, {"entrada": 1000, "salida": 200}),
                           "si solo contara la llamada buena, el tope vería la sexta parte")

    def test_un_cuerpo_ilegible_que_trae_usage_tambien_cuenta(self):
        """`choices` torcidos pero `usage` presente: la llamada se cobró. Falle o
        acierte el reintento, esos tokens están en la cuenta."""
        torcido = ds("", entrada=5000, salida=300, choices=[None])
        for siguientes, ok, uso in [
                ([ds(BUENO, entrada=1000, salida=200)], True,
                 {"entrada": 6000, "cache": 0, "salida": 500}),
                ([torcido, torcido], False, {"entrada": 15000, "cache": 0, "salida": 900})]:
            with self.subTest(al_final_acierta=ok):
                self.post.side_effect = [torcido, *siguientes]
                r = self.completar()
                self.assertIs(r["ok"], ok)
                self.assertEqual(r["uso"], uso)
                self.assertEqual(r["usd"], IJ.costo_usd(BARATO, uso))
                self.assertGreater(r["usd"], 0)

    def test_lo_que_no_se_pudo_leer_no_se_inventa(self):
        """El otro lado de la regla: un cuerpo que ni siquiera es JSON no trae
        `usage`, así que no hay de dónde sacar un costo. Se cuenta lo leído."""
        self.post.side_effect = [Resp(ilegible=True), ds(BUENO, entrada=1000, salida=200)]
        r = self.completar()
        self.assertIs(r["ok"], True)
        self.assertEqual(r["uso"], {"entrada": 1000, "cache": 0, "salida": 200})


class R02_NuncaLanza(_IA):
    """DEFECTO: el contrato dice «NUNCA lanza» y había tres huecos: un `usage`
    que no era dict (`'n/d'.get` → AttributeError, fuera del `try`), tokens no
    numéricos (`int('muchos')` dentro de `costo_usd`) y `httpx.InvalidURL`, que NO
    hereda de `httpx.HTTPError` y se escapaba del `except`. Después apareció un
    cuarto: un conteo `Infinity` o `1e999` (`json.loads` los acepta) hacía que
    `int(inf)` lanzara OverflowError, que `_tokens` no atrapaba.

    POR QUÉ IMPORTABA: `juzgar_skus` corre en hilos y `ex.map` re-lanza: una sola
    respuesta rara tumbaba la corrida entera de un lote, y POST /juez/juzgar daba
    un 500 sin explicación. Con la red de seguridad ya no revienta, pero el
    OverflowError caía en ella: `ok: False` con `uso` vacío, y se perdían la
    respuesta buena y los tokens ya cobrados de los intentos anteriores."""

    def test_un_usage_que_no_es_dict_no_tumba_la_llamada(self):
        for raro in ("n/d", [1, 2], 7, None, True):
            with self.subTest(usage=raro):
                self.post.return_value = ds(BUENO, usage=raro)
                r = self.completar()
                self.assertIs(r["ok"], True)
                self.assertEqual(r["datos"], {"veredictos": []})
                self.assertEqual(r["uso"], {"entrada": 0, "cache": 0, "salida": 0})
                self.assertEqual(r["usd"], 0.0)

    def test_tokens_no_numericos_cuentan_cero_y_los_demas_se_respetan(self):
        for raro in ("muchos", None, [], {}, "12.5x", float("inf")):
            with self.subTest(prompt_tokens=raro):
                self.post.return_value = ds(BUENO, usage={
                    "prompt_tokens": raro, "prompt_cache_hit_tokens": raro,
                    "completion_tokens": 300})
                r = self.completar()
                self.assertIs(r["ok"], True)
                self.assertEqual(r["uso"], {"entrada": 0, "cache": 0, "salida": 300})
                self.assertAlmostEqual(r["usd"], 0.0006)

    def test_costo_usd_tampoco_lanza_con_tokens_raros(self):
        """`costo_usd` la llama también quien arma un fallo: no puede ser ella la
        que reviente."""
        self.assertEqual(IJ.costo_usd(BARATO, {"entrada": "muchos", "salida": None}), 0.0)
        self.assertEqual(IJ.costo_usd(BARATO, {"entrada": float("inf")}), 0.0)
        self.assertEqual(IJ.costo_usd(BARATO, {"salida": float("-inf")}), 0.0)
        self.assertIsNone(IJ.costo_usd(BARATO, "n/d"))

    def test_un_conteo_infinito_no_pierde_lo_ya_cobrado(self):
        """El primer intento vuelve vacío y se cobra; el segundo trae la respuesta
        buena con `prompt_tokens` infinito. Antes: `ok: False`, `uso` {} y `usd`
        0.0 (el vacío de 5,000 tokens desaparecía de la cuenta)."""
        self.post.side_effect = [ds("", entrada=5000, salida=300),
                                 ds(BUENO, entrada=float("inf"), salida=200)]
        r = self.completar()
        self.assertIs(r["ok"], True)
        self.assertEqual(r["datos"], {"veredictos": []})
        self.assertEqual(r["uso"], {"entrada": 5000, "cache": 0, "salida": 500})
        # 5,000 de entrada a $1 + 500 de salida a $2, por millón.
        self.assertAlmostEqual(r["usd"], 0.006)

    def test_una_url_mal_escrita_no_lanza_ni_se_reintenta(self):
        """Una `DEEPSEEK_BASE_URL` mal escrita no es un error de red: repetirla
        no la arregla, así que sale a la primera y sin dormir."""
        self.assertFalse(issubclass(httpx.InvalidURL, httpx.HTTPError),
                         "la premisa de esta prueba: InvalidURL NO es un HTTPError")
        self.post.side_effect = httpx.InvalidURL("Invalid port: 'puerto'")
        r = self.completar()
        self.assertIs(r["ok"], False)
        self.assertIn("InvalidURL", r["motivo"])
        self.assertEqual(r["usd"], 0.0)
        self.assertEqual(self.post.call_count, 1)
        self.dormir.assert_not_called()

    def test_la_red_de_seguridad_atrapa_lo_que_nadie_previo(self):
        """Lo que sea que lance el proveedor por dentro vuelve como `ok: False`
        con todas las llaves del contrato: quien llama suma `usd` sin preguntar."""
        with mock.patch.object(IJ, "_deepseek", side_effect=ZeroDivisionError("imprevisto")):
            r = self.completar()
        self.assertIs(r["ok"], False)
        self.assertIn("ZeroDivisionError", r["motivo"])
        for llave in ("motivo", "proveedor", "modelo", "texto", "cortado", "uso", "usd"):
            self.assertIn(llave, r)


class R03_ClienteAnthropicConDireccionExplicita(_IA):
    """DEFECTO: `_claude` creaba `anthropic.Anthropic(api_key=...)` sin fijar
    `base_url`. El SDK toma `ANTHROPIC_BASE_URL` directo del entorno del proceso,
    sin pasar por `config`.

    POR QUÉ IMPORTABA: un proceso lanzado desde una terminal que traiga esa
    variable apuntando a otro lado le mandaría AHÍ la llave de Anthropic (viaja en
    la cabecera `x-api-key` de cada petición)."""

    OTRA = "https://otro-destino.invalid"

    def test_el_cliente_se_crea_con_base_url_explicita(self):
        sdk = mock.MagicMock(name="anthropic")
        sdk.Anthropic.return_value.messages.create.return_value = types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text=BUENO)], stop_reason="end_turn",
            usage=types.SimpleNamespace(input_tokens=10, cache_read_input_tokens=0,
                                        output_tokens=5))
        with mock.patch.dict(sys.modules, {"anthropic": sdk}), \
             mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": self.OTRA}):
            r = self.completar(HAIKU)
        self.assertIs(r["ok"], True)
        kw = sdk.Anthropic.call_args.kwargs
        self.assertEqual(kw["base_url"], "https://api.anthropic.com")
        self.assertEqual(kw["api_key"], "llave-inventada-cl")

    @unittest.skipUnless(importlib.util.find_spec("anthropic"),
                         "el SDK de Anthropic no está instalado")
    def test_con_el_sdk_real_la_direccion_del_entorno_no_gana(self):
        """La misma regla contra el SDK de verdad: se construye el cliente real
        (eso no sale a la red) y se corta ANTES de la petición."""
        import anthropic

        real = anthropic.Anthropic
        destinos: list[str] = []

        def espia(**kw: Any) -> Any:
            cli = real(**kw)
            destinos.append(str(cli.base_url))
            cli.close()
            raise RuntimeError("hasta aquí: esta prueba no sale a la red")

        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": self.OTRA}):
            testigo = real(api_key="llave-inventada")
            toma_el_entorno = str(testigo.base_url).startswith(self.OTRA)
            testigo.close()
            if not toma_el_entorno:
                self.skipTest("este SDK ya no toma ANTHROPIC_BASE_URL del entorno")
            with mock.patch.object(anthropic, "Anthropic", espia):
                r = self.completar(HAIKU)
        self.assertIs(r["ok"], False, "el espía corta antes de la petición")
        self.assertEqual(len(destinos), 1)
        self.assertTrue(destinos[0].startswith("https://api.anthropic.com"), destinos[0])


# ═════════════════════════════════════════════════════════════════════════════
#  competencia_juez
# ═════════════════════════════════════════════════════════════════════════════

NUESTRO = "Lámpara de escritorio LED plegable"


def v(i: int, clase: str = "mismo", unidades: int = 1) -> dict[str, Any]:
    return {"i": i, "clase": clase, "unidades": unidades, "razon": "mismo tipo de lámpara"}


def respuesta_juez(veredictos: list, usd: float = 0.001) -> dict[str, Any]:
    """Lo que devuelve `ia_json.completar_json` cuando el modelo contesta."""
    return {"ok": True, "texto": "", "cortado": False, "proveedor": "deepseek", "modelo": MODELO,
            "datos": {"nuestro": "lámpara de escritorio", "unidades_nuestras": 1,
                      "veredictos": veredictos},
            "uso": {"entrada": 100, "cache": 0, "salida": 50}, "usd": usd}


CAIDA = {"ok": False, "motivo": "DeepSeek no respondió (HTTP 503: upstream).", "usd": 0.0,
         "proveedor": "deepseek", "modelo": MODELO, "texto": "", "cortado": False, "uso": {}}


def obediente(system: str, user: str, **kw: Any) -> dict[str, Any]:
    """Un modelo que contesta bien: un `mismo` por cada número de la lista."""
    n = int(re.search(r"RIVALES \((\d+)\)", user).group(1))
    return respuesta_juez([v(i) for i in range(1, n + 1)])


def renumera(system: str, user: str, **kw: Any) -> dict[str, Any]:
    """Un modelo que SIEMPRE inventa un número que nadie pidió (el 99)."""
    n = int(re.search(r"RIVALES \((\d+)\)", user).group(1))
    return respuesta_juez([v(i) for i in range(1, n + 1)] + [v(99)])


def fila(sku: str = "SKU-A", externo_id: str = "RIVAL-01", *, titulo: str = "Lámpara con brazo",
         precio: float | None = 300.0, es_nuestro: bool = False, vigente: bool = False,
         clase: str | None = None, titulo_nuestro: str | None = NUESTRO,
         posicion: int = 1) -> dict[str, Any]:
    """Una fila CRUDA de `_SQL_FILAS`: un rival sin veredicto, salvo que se diga."""
    return {"sku": sku, "termino_id": 11, "externo_id": externo_id, "posicion": posicion,
            "titulo": titulo, "precio": precio, "es_nuestro": es_nuestro,
            "capturado_en": dt.datetime(2026, 9, 30, 12, 0), "titulo_nuestro": titulo_nuestro,
            "categoria_nombre": "Iluminación", "clase": clase, "motivo": None,
            "unidades_nuestras": 1 if vigente else None,
            "unidades_rival": 1 if vigente else None,
            "juzgado_en": dt.datetime(2026, 9, 30, 13, 0) if vigente else None,
            "modelo": MODELO if vigente else None,
            "version_prompt": J.VERSION_PROMPT if vigente else None, "vigente": vigente}


def pendientes_de(*skus: str, por_sku: int = 2, **kw: Any) -> list[dict[str, Any]]:
    return [fila(s, f"RIVAL-{s}-{k}", titulo=f"Lámpara de escritorio modelo {k}", posicion=k, **kw)
            for s in skus for k in range(1, por_sku + 1)]


class BaseJuez:
    """`supabase_db` suplantada para el juez: contesta la lectura de filas, apunta
    las transacciones CONFIRMADAS y la bitácora, y puede fallar al abrir cursor.

    `fallas`  excepciones que lanzan los siguientes `get_cursor`, en orden; al
              acabarse, el cursor vuelve a funcionar.
    `rota`    una excepción que lanza SIEMPRE (la base no está guardando)."""

    def __init__(self, filas: list[dict[str, Any]], *, fallas: list[Exception] | None = None,
                 rota: Exception | None = None) -> None:
        self.filas = filas
        self.fallas = list(fallas or [])
        self.rota = rota
        self.cursores = 0                     # cuántas veces se pidió un cursor
        self.transacciones: list[list] = []   # una lista de (sql, params) por transacción
        self.bitacora: list[dict[str, Any]] = []

    def fetch_all(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        return [dict(f) for f in self.filas if f["sku"] in params["skus"]]

    @contextmanager
    def get_cursor(self):
        self.cursores += 1
        if self.rota:
            raise self.rota
        if self.fallas:
            raise self.fallas.pop(0)
        tx: list = []
        cur = mock.Mock()
        cur.execute.side_effect = lambda sql, params=None: tx.append((sql, params))
        yield cur
        self.transacciones.append(tx)

    def execute(self, sql: str, params: Any = None) -> int:
        origen, accion, estado, detalle, _duracion = params
        self.bitacora.append({"origen": origen, "accion": accion, "estado": estado,
                              "detalle": json.loads(detalle)})
        return 1

    def guardados(self) -> list[tuple]:
        return [p for tx in self.transacciones for sql, p in tx if "insert into" in sql]

    def puesta(self) -> Any:
        return mock.patch.multiple(J.supabase_db, fetch_all=self.fetch_all,
                                   get_cursor=self.get_cursor, execute=self.execute)


def correr_juez(base: BaseJuez, llm: Any, skus: list[str], **kw: Any) -> dict[str, Any]:
    kw.setdefault("presupuesto", J.Presupuesto(1.0))
    kw.setdefault("modelo", MODELO)
    kw.setdefault("hilos", 1)
    with base.puesta(), mock.patch.object(J.ia_json, "completar_json", llm):
        return J.juzgar_skus(skus, **kw)


class R04_LoteSospechosoDejaMotivo(unittest.TestCase):
    """DEFECTO: un lote que se descarta por sospechoso (el modelo devolvió un
    número que nadie pidió, dos veces) dejaba `faltan` lleno y `motivo` en None:
    la llamada había sido `ok` y no traía motivo, y `juzgar_lote` no ponía uno.

    POR QUÉ IMPORTABA: `juzgar_skus` copia ese motivo a `ultimo_motivo` y a
    `ops.process_log`. Con None, en la bitácora no se distinguía «el modelo
    renumeró» de cualquier otra causa, y el panel decía «sin detalle»."""

    def test_el_lote_descartado_dice_que_el_modelo_renumero(self):
        llm = mock.Mock(side_effect=renumera)
        with mock.patch.object(J.ia_json, "completar_json", llm):
            r = J.juzgar_lote({"titulo": NUESTRO}, [{"titulo": "a"}, {"titulo": "b"}],
                              modelo=MODELO)
        self.assertEqual(llm.call_count, 2, "se reintenta UNA vez y se tira")
        self.assertEqual((r["ok"], r["veredictos"], r["faltan"]), (False, {}, [1, 2]))
        self.assertIsInstance(r["motivo"], str)
        self.assertIn("renumer", r["motivo"])

    def test_ese_motivo_llega_a_la_corrida_y_a_la_bitacora(self):
        base = BaseJuez(pendientes_de("SKU-A"))
        r = correr_juez(base, mock.Mock(side_effect=renumera), ["SKU-A"])
        self.assertEqual((r["veredictos"], r["fallidos"]), (0, 1))
        self.assertIn("renumer", r["ultimo_motivo"])
        self.assertEqual(base.transacciones, [], "un lote torcido no se guarda")
        self.assertIn("renumer", base.bitacora[0]["detalle"]["ultimo_motivo"])

    def test_si_el_reintento_sale_limpio_no_queda_motivo(self):
        """El motivo es del lote que SE TIRÓ: si la segunda respuesta es buena y
        completa, no hay nada que explicar."""
        llm = mock.Mock(side_effect=[renumera("", "RIVALES (2)"), obediente("", "RIVALES (2)")])
        with mock.patch.object(J.ia_json, "completar_json", llm):
            r = J.juzgar_lote({"titulo": NUESTRO}, [{"titulo": "a"}, {"titulo": "b"}],
                              modelo=MODELO)
        self.assertEqual((sorted(r["veredictos"]), r["faltan"], r["motivo"]), ([1, 2], [], None))

    def test_si_el_reintento_sale_limpio_pero_parcial_tampoco(self):
        """El reintento se GUARDÓ; lo que falta es un rival que no contestó. El
        motivo del lote tirado se arrastraba y decía «renumeró (lote descartado)»
        sobre un lote que no se descartó."""
        llm = mock.Mock(side_effect=[renumera("", "RIVALES (2)"), respuesta_juez([v(1)])])
        with mock.patch.object(J.ia_json, "completar_json", llm):
            r = J.juzgar_lote({"titulo": NUESTRO}, [{"titulo": "a"}, {"titulo": "b"}],
                              modelo=MODELO)
        self.assertEqual((sorted(r["veredictos"]), r["faltan"]), ([1], [2]))
        self.assertNotIn("renumer", r["motivo"] or "")

    def test_el_motivo_de_otro_trozo_si_se_conserva(self):
        """Lo que se limpia es el motivo del intento TIRADO del mismo trozo, no el
        de un trozo anterior que de verdad quedó sin juzgar."""
        caida_y_luego_bien = [dict(CAIDA), renumera("", "RIVALES (1)"),
                              obediente("", "RIVALES (1)")]
        llm = mock.Mock(side_effect=caida_y_luego_bien)
        rivales_ = [{"titulo": f"r{k}"} for k in range(J.TROZO + 1)]
        with mock.patch.object(J.ia_json, "completar_json", llm):
            r = J.juzgar_lote({"titulo": NUESTRO}, rivales_, modelo=MODELO)
        self.assertEqual(r["faltan"], list(range(1, J.TROZO + 1)))
        self.assertIn("HTTP 503", r["motivo"])


class R05_ResumenAvisaSinTituloNuestro(unittest.TestCase):
    """DEFECTO: `filas` marca `pendiente` a todo rival ajeno con precio sin
    veredicto, tenga o no título el SKU; pero `juzgar_skus` solo juzga filas con
    `titulo_nuestro`. Para un SKU sin título los pendientes no bajaban nunca y
    nada lo decía.

    POR QUÉ IMPORTABA: el usuario apretaba «Juzgar rivales», recibía «0
    veredictos» y volvía a apretar. `sin_titulo` es lo que le permite a la ruta y
    al panel decir la causa."""

    def _resumen(self, crudas: list[dict[str, Any]]) -> dict[str, Any]:
        with mock.patch.object(J.supabase_db, "fetch_all", return_value=crudas):
            return J.resumen(J.filas(["SKU-A"]))

    def test_sin_titulo_nuestro_lo_marca(self):
        for vacio in (None, ""):
            with self.subTest(titulo_nuestro=vacio):
                r = self._resumen(pendientes_de("SKU-A", titulo_nuestro=vacio))
                self.assertIs(r["sin_titulo"], True)
                self.assertEqual((r["pendientes"], r["completo"]), (2, False))

    def test_con_titulo_no_lo_marca(self):
        self.assertIs(self._resumen(pendientes_de("SKU-A"))["sin_titulo"], False)

    def test_sin_rivales_que_cuenten_no_hay_nada_que_avisar(self):
        """Sin rivales no hay pendientes que expliquen: el aviso sería ruido."""
        self.assertIs(self._resumen([])["sin_titulo"], False)
        solo_nuestra = [fila("SKU-A", "R1", es_nuestro=True, titulo_nuestro=None),
                        fila("SKU-A", "R2", precio=None, titulo_nuestro=None)]
        self.assertIs(self._resumen(solo_nuestra)["sin_titulo"], False)

    def test_es_justo_el_sku_que_el_juez_nunca_va_a_bajar(self):
        """Las dos mitades del defecto juntas: el juez no gasta en ese SKU y el
        resumen lo explica."""
        base, llm = BaseJuez(pendientes_de("SKU-A", titulo_nuestro=None)), mock.Mock()
        r = correr_juez(base, llm, ["SKU-A"])
        llm.assert_not_called()
        self.assertEqual((r["veredictos"], r["llamadas"]), (0, 0))
        self.assertIs(self._resumen(base.filas)["sin_titulo"], True)


class R06_NoPoderGuardarNoEsFalloDelProveedor(unittest.TestCase):
    """DEFECTO: si `_guardar` fallaba, el SKU se contaba como fallo del
    PROVEEDOR: cinco seguidos detenían con «5 fallos seguidos del proveedor» y
    `ultimo_motivo` None, aunque el LLM había contestado bien las cinco veces.

    POR QUÉ IMPORTABA: el diagnóstico mandaba a revisar DeepSeek (saldo, llave)
    cuando lo roto era la base, y mientras tanto se pagaban cinco llamadas cuyos
    veredictos se perdían. Ahora se cuenta aparte (`sin_guardar`), la bitácora
    queda `parcial` y a la TERCERA se detiene."""

    def _sin_poder_guardar(self, n_skus: int) -> tuple[dict, BaseJuez, mock.Mock, J.Presupuesto]:
        skus = [f"SKU-{i}" for i in range(1, n_skus + 1)]
        # Un error que NO es transitorio: el reintento del pooler no lo tapa.
        base = BaseJuez(pendientes_de(*skus), rota=RuntimeError("deadlock detected"))
        llm, p = mock.Mock(side_effect=obediente), J.Presupuesto(1.0)
        with self.assertLogs(LOG_JUEZ, "WARNING"):
            r = correr_juez(base, llm, skus, presupuesto=p)
        return r, base, llm, p

    def test_a_la_tercera_se_detiene_y_no_culpa_al_proveedor(self):
        r, base, llm, p = self._sin_poder_guardar(8)
        self.assertEqual(llm.call_count, 3, "a la tercera deja de gastar a fondo perdido")
        self.assertEqual(r["sin_guardar"], 3)
        self.assertEqual(r["fallidos"], 0, "el LLM contestó bien: no es su fallo")
        self.assertEqual(r["detenido"], "la base no está guardando los veredictos")
        self.assertNotIn("proveedor", r["detenido"])
        self.assertEqual(r["ultimo_motivo"], "no se pudo guardar en la base")
        self.assertEqual((r["veredictos"], r["juzgados_skus"], r["sin_juzgar"]), (0, 0, 6))
        # Lo que no se guardó TAMBIÉN se pagó: el tope y la bitácora lo ven.
        self.assertAlmostEqual(p.gastado, 0.003)
        self.assertEqual(r["usd"], 0.003)
        self.assertEqual(len(base.bitacora), 1)
        self.assertEqual((base.bitacora[0]["accion"], base.bitacora[0]["estado"]),
                         ("juez", "parcial"))
        self.assertEqual(base.bitacora[0]["detalle"]["sin_guardar"], 3)

    def test_con_dos_sigue_pero_la_bitacora_ya_no_dice_ok(self):
        r, base, llm, _ = self._sin_poder_guardar(2)
        self.assertEqual(llm.call_count, 2)
        self.assertEqual((r["sin_guardar"], r["fallidos"], r["detenido"]), (2, 0, None))
        self.assertEqual(base.bitacora[0]["estado"], "parcial")

    def test_no_suma_a_la_racha_de_fallos_del_proveedor(self):
        """Cuatro caídas del proveedor y un fallo de la base NO son «5 fallos
        seguidos del proveedor»: el sexto SKU sí se intenta."""
        skus = [f"SKU-{i}" for i in range(1, 7)]
        # El primer cursor que se pida (el del 5.º SKU) sale roto; el del 6.º, bien.
        base = BaseJuez(pendientes_de(*skus), fallas=[RuntimeError("deadlock detected")])
        llamadas = {"n": 0}

        def llm(system: str, user: str, **kw: Any) -> dict[str, Any]:
            llamadas["n"] += 1
            return obediente(system, user) if llamadas["n"] > 4 else dict(CAIDA)

        with self.assertLogs(LOG_JUEZ, "WARNING"):
            r = correr_juez(base, llm, skus)
        self.assertEqual(llamadas["n"], 6, "ninguno se saltó")
        self.assertEqual((r["fallidos"], r["sin_guardar"], r["juzgados_skus"]), (4, 1, 1))
        self.assertIsNone(r["detenido"])


class R07_GuardarPasaPorElReintentoDelPooler(unittest.TestCase):
    """DEFECTO: `_guardar` abría `supabase_db.get_cursor()` directo. Es el hueco
    que `supabase_db` documenta desde el 28-ago: los escritores por tanda que
    abren su propio cursor no reciben el reintento.

    POR QUÉ IMPORTABA: una conexión muerta que entrega Supavisor (pasa en cada
    reciclado del pooler) tiraba la transacción, y los veredictos de ese SKU —ya
    PAGADOS— se perdían y se volvían a pagar en la siguiente pasada."""

    R = {"veredictos": {1: {"clase": "mismo", "unidades_rival": 1, "motivo": "igual"},
                        2: {"clase": "refaccion", "unidades_rival": None, "motivo": ""}},
         "unidades_nuestras": 1, "proveedor": "deepseek", "modelo": MODELO}

    def test_la_transaccion_entera_va_dentro_del_reintento(self):
        base = BaseJuez([])
        antes: list[int] = []

        def espia(fn: Any) -> Any:
            antes.append(base.cursores)
            return fn()

        with base.puesta(), mock.patch.object(J.supabase_db, "reintentar_transitorio",
                                              side_effect=espia) as reintento:
            n = J._guardar("SKU-A", pendientes_de("SKU-A"), self.R)
        reintento.assert_called_once()
        self.assertEqual(antes, [0], "el cursor se abre DENTRO de lo que se reintenta")
        self.assertEqual((n, base.cursores, len(base.transacciones)), (2, 1, 1))

    def test_una_conexion_muerta_no_pierde_lo_ya_pagado(self):
        """Con el reintento REAL de `supabase_db`: el primer cursor sale muerto,
        el pool se reconstruye y la segunda vuelta guarda todo."""
        base = BaseJuez(pendientes_de("SKU-A"),
                        fallas=[RuntimeError("connection already closed")])
        with mock.patch.object(J.supabase_db, "_reiniciar_pool") as reiniciar, \
             self.assertLogs(LOG_SUPABASE, "WARNING"):
            r = correr_juez(base, mock.Mock(side_effect=obediente), ["SKU-A"])
        reiniciar.assert_called_once()
        self.assertEqual(base.cursores, 2)
        self.assertEqual((r["veredictos"], r["juzgados_skus"], r["fallidos"]), (2, 1, 0))
        self.assertNotIn("sin_guardar", r)
        self.assertEqual([p[2] for p in base.guardados()], ["RIVAL-SKU-A-1", "RIVAL-SKU-A-2"])
        self.assertEqual(base.bitacora[0]["estado"], "ok")

    def test_el_reintento_repite_la_transaccion_completa(self):
        """Lo que se repite es TODO el `with` (alinear unidades + upserts), no
        solo el último insert: es una transacción y Postgres deshizo la primera."""
        base = BaseJuez([], fallas=[RuntimeError("server closed the connection unexpectedly")])
        with base.puesta(), mock.patch.object(J.supabase_db, "_reiniciar_pool"), \
             self.assertLogs(LOG_SUPABASE, "WARNING"):
            J._guardar("SKU-A", pendientes_de("SKU-A"), self.R)
        self.assertEqual(len(base.transacciones), 1)
        sentencias = [sql.split()[0] for sql, _ in base.transacciones[0]]
        self.assertEqual(sentencias, ["update", "insert", "insert"])

    def test_un_error_que_no_es_transitorio_no_se_reintenta(self):
        base = BaseJuez([], rota=RuntimeError("deadlock detected"))
        with base.puesta(), self.assertRaises(RuntimeError):
            J._guardar("SKU-A", pendientes_de("SKU-A"), self.R)
        self.assertEqual(base.cursores, 1)


# ═════════════════════════════════════════════════════════════════════════════
#  competencia_mejora
# ═════════════════════════════════════════════════════════════════════════════

SKU = "PRU-0001-NEG"
SKU2 = "PRU-0002-BLA"
TITULO = "Soporte de pared para cámara de seguridad metálico"
TERMINO = "soporte para camara"
CAND = "soporte pared camara seguridad"
CAND2 = "base pared camara vigilancia"


def hace(dias: float) -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=dias)


def rival(sku: str, n: int, clase: str = "otro_producto", *, precio: float = 500.0,
          juzgado: bool = True, nuestro: bool = False) -> dict[str, Any]:
    """Una fila como las que entrega `competencia_juez.filas`, ya resuelta."""
    cuenta = (not nuestro) and precio > 0
    return {"sku": sku, "termino_id": 10, "externo_id": f"MLM{n:09d}", "posicion": n,
            "titulo": f"artículo genérico número {n}", "precio": precio, "es_nuestro": nuestro,
            "titulo_nuestro": TITULO, "categoria_nombre": "Soportes",
            "clase": clase if juzgado else None, "motivo": "motivo de prueba" if juzgado else None,
            "unidades_nuestras": 1, "unidades_rival": 1,
            "juzgado_en": hace(1) if juzgado else None, "modelo": MODELO,
            "version_prompt": J.VERSION_PROMPT if juzgado else None,
            "cuenta": cuenta, "vigente": juzgado,
            "comparable": cuenta and juzgado and clase == "mismo",
            "pendiente": cuenta and not juzgado}


def rivales(sku: str, mismos: int = 0, otros: int = 0, pendientes: int = 0) -> list[dict]:
    """`mismos` comparables con precios parejos, `otros` descartados y
    `pendientes` sin juzgar."""
    out = [rival(sku, i + 1, "mismo", precio=500.0 + 10 * i) for i in range(mismos)]
    out += [rival(sku, mismos + i + 1) for i in range(otros)]
    out += [rival(sku, mismos + otros + i + 1, juzgado=False) for i in range(pendientes)]
    return out


def grupo(*skus: str, termino: str = TERMINO, tid: int = 10,
          intentados: list[str] | None = None) -> dict[str, Any]:
    """Un grupo como los de `elegibles`: cada SKU con 0 comparables de 10
    rivales ya juzgados."""
    miembros = []
    for sku in skus or (SKU,):
        fs = rivales(sku, otros=10)
        miembros.append({"sku": sku, "titulo": TITULO, "resumen": J.resumen(fs), "filas": fs,
                         "intentados": list(intentados or [])})
    return {"termino_id": tid, "termino": termino, "categoria_id": None,
            "categoria_nombre": "Soportes", "ruta": "Hogar > Seguridad > Soportes",
            "skus": miembros}


def propone(*candidatos: str, usd: float = 0.001) -> dict[str, Any]:
    """Lo que devuelve `ia_json.completar_json` al proponer."""
    return {"ok": True, "usd": usd, "proveedor": "deepseek", "modelo": MODELO,
            "datos": {"diagnostico": "el término es demasiado general", "termino_ok": False,
                      "candidatos": [{"termino": c, "porque": "nombra el producto exacto"}
                                     for c in candidatos]}}


class BaseMejora:
    """`supabase_db` suplantada para la mejora. Reconoce cada consulta por un
    trozo de su SQL; una que no conoce revienta la prueba a propósito."""

    def __init__(self, *, universo: list | None = None, historia: list | None = None,
                 catalogo: list | None = None, ids: dict | None = None) -> None:
        self.universo = universo or []
        self.historia = historia or []
        self.catalogo = catalogo or []
        self.ids = ids or {}                    # término medido → id del catálogo
        self.reclamos: list[tuple[str, str, str]] = []   # (sku, candidato, estado)
        self.iids: dict[tuple[str, str], int] = {}
        self.resueltos: dict[int, tuple] = {}   # iid → (estado, termino_id, comp., total)
        self.bitacora: list[dict[str, Any]] = []
        self._iid = 100

    def fetch_all(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        if "market_sku_config cfg" in sql:
            return self.universo
        if CM.TABLA in sql:
            return self.historia
        if "market_terms" in sql:
            return []
        if "market_search_term" in sql:
            return self.catalogo
        raise AssertionError(f"lectura que la prueba no esperaba: {sql[:80]}")

    def fetch_scalar(self, sql: str, params: Any = None) -> Any:
        return self.ids.get(params[1])

    def execute_returning(self, sql: str, params: Any = None) -> dict[str, Any]:
        sku, candidato, estado = params[0], params[4], params[6]
        self.reclamos.append((sku, candidato, estado))
        self._iid += 1
        self.iids[(sku, candidato)] = self._iid
        return {"id": self._iid}

    def execute(self, sql: str, params: Any = None) -> int:
        if "ops.process_log" in sql:
            origen, accion, estado, detalle, _duracion = params
            self.bitacora.append({"origen": origen, "accion": accion, "estado": estado,
                                  "detalle": json.loads(detalle)})
            return 1
        if "termino_candidato_id = coalesce" in sql:             # _resolver
            estado, tid, comparables, total, _estado, iid = params
            self.resueltos[iid] = (estado, tid, comparables, total)
            return 1
        raise AssertionError(f"escritura que la prueba no esperaba: {sql[:80]}")

    def puesta(self) -> Any:
        return mock.patch.multiple(CM.supabase_db, fetch_all=self.fetch_all,
                                   fetch_scalar=self.fetch_scalar, execute=self.execute,
                                   execute_returning=self.execute_returning)

    def estado_de(self, sku: str, candidato: str) -> str | None:
        """El estado FINAL del intento, o `None` si nunca se resolvió."""
        return (self.resueltos.get(self.iids.get((sku, candidato), -1)) or (None,))[0]


def correr_mejora(grupos: list[dict[str, Any]], *, ia: list[dict[str, Any]],
                  base: BaseMejora | None = None, medidos: dict[str, int] | None = None,
                  filas: Any = None, juez: Any = None, tope_usd: float = 1.0,
                  **kw: Any) -> SimpleNamespace:
    """Corre `mejorar` con todo lo de afuera suplantado y devuelve los testigos.

    `ia`      respuestas de `completar_json`, en orden; pedir una de más revienta.
    `medidos` {candidato: filas que guardó la medición de Apify}
    `filas`   {termino_id: filas ya juzgadas}, o una función (skus, termino_id)
    `juez`    función (skus, termino_id) → lo que devuelve `juzgar_skus`; su `usd`
              se suma al presupuesto, como hace el juez de verdad.
    Si `mejorar` lanza, la excepción queda en `error` (y `out` en None)."""
    base = base or BaseMejora()
    t = SimpleNamespace(base=base, lotes=[], juzgados=[], out=None, error=None,
                        ia=mock.Mock(side_effect=list(ia)), presupuesto=J.Presupuesto(tope_usd))

    async def medir(terminos, limite=10, bloqueados=None):
        t.lotes.append(list(terminos))
        return {x: (medidos or {}).get(x, 0) for x in terminos}

    def juzgar(skus, *, presupuesto, termino_id=None, modelo=None, **_):
        t.juzgados.append((list(skus), termino_id))
        r = juez(skus, termino_id) if juez else {}
        presupuesto.sumar(r.get("usd") or 0)
        return {"veredictos": 0, "usd": 0.0, "detenido": None, **r}

    def filas_de(skus, termino_id=None):
        if callable(filas):
            return filas(skus, termino_id)
        return [f for f in (filas or {}).get(termino_id, []) if f["sku"] in skus]

    with base.puesta(), \
         mock.patch.object(CM.ia_json, "completar_json", t.ia), \
         mock.patch.object(CM.competencia_juez, "juzgar_skus", juzgar), \
         mock.patch.object(CM.competencia_juez, "filas", filas_de), \
         mock.patch.object(CM.competencia_captura, "medir_busquedas", medir), \
         mock.patch.object(CM.competencia_scraper, "disponible", return_value=True):
        try:
            t.out = CM.mejorar(grupos, presupuesto=t.presupuesto, modelo=MODELO, **kw)
        except Exception as exc:                                    # noqa: BLE001
            t.error = exc
    return t


class R08_LlaveConservaLaEnie(unittest.TestCase):
    """DEFECTO: `llave` quitaba acentos con NFKD, que parte la «ñ» en «n» + tilde
    combinante; la tilde se iba ANTES de la expresión regular que sí la permitía.
    «moño para niña» y «mono para nina» daban la misma llave.

    POR QUÉ IMPORTABA: son dos búsquedas distintas en Mercado Libre. Con la misma
    llave, un candidato válido se descartaba como «ya probado» o como «es el
    término actual», y el SKU se quedaba sin la propuesta buena."""

    def test_mono_y_mono_con_enie_son_busquedas_distintas(self):
        self.assertEqual(CM.llave("moño para niña"), "moño para niña")
        self.assertNotEqual(CM.llave("moño para niña"), CM.llave("mono para nina"))
        self.assertEqual(CM.llave("MOÑO para Niña"), "moño para niña", "en mayúsculas también")

    def test_lo_demas_de_la_llave_sigue_igual(self):
        """El arreglo no debe traerse de vuelta los acentos ni la diéresis."""
        self.assertEqual(CM.llave("Cañón Eléctrico-Pingüino"), "cañon electrico pinguino")
        self.assertEqual(CM.llave("bujías-iridium"), CM.llave("Bujias Iridium"))

    def test_coincide_con_lo_que_conserva_normalizar(self):
        """`normalizar` (lo que se BUSCA) siempre conservó la ñ; `llave` (lo que se
        COMPARA) tiene que distinguir lo mismo."""
        self.assertEqual(CM.normalizar("Moño para Niña"), "moño para niña")
        self.assertEqual(CM.llave(CM.normalizar("Moño para Niña")), "moño para niña")

    def test_proponer_no_descarta_el_candidato_con_enie(self):
        g = grupo(termino="mono para nina", intentados=["mono para bebe"])
        with mock.patch.object(CM.ia_json, "completar_json",
                               return_value=propone("moño para niña", "moño para bebé")), \
             mock.patch.object(CM, "_tendencias", return_value=[]):
            p = CM.proponer(g, modelo=MODELO)
        self.assertEqual([c["termino"] for c in p["candidatos"]],
                         ["moño para niña", "moño para bebé"])

    def test_una_enie_descompuesta_tambien_se_conserva(self):
        """El arreglo apartaba la «ñ» COMPUESTA; una que llega partida («n» +
        tilde combinante, NFD) se volvía «n» en la llave, y `normalizar` la
        partía en dos palabras («mon o»). Se compone primero."""
        import unicodedata      # aquí y no arriba: solo lo usa esta prueba

        nfd = unicodedata.normalize("NFD", "Moño para Niña de cámara")
        self.assertNotEqual(nfd, unicodedata.normalize("NFC", nfd), "de verdad viene partida")
        self.assertEqual(CM.llave(nfd), "moño para niña de camara")
        self.assertEqual(CM.normalizar(nfd), "moño para niña de cámara")


class R09_JuicioIncompletoQuedaEnError(unittest.TestCase):
    """DEFECTO: un candidato cuyos rivales NO quedaron todos juzgados (tope de
    gasto agotado en las propuestas, proveedor caído a media corrida, un rival
    que el modelo no contestó) caía al `else` y se cerraba como `sin_mejora`.

    POR QUÉ IMPORTABA: `sin_mejora` es CONCLUYENTE: no se re-reclama, cuenta
    contra `MAX_POR_SKU`, entra a «ya probados» y dispara 60 días de
    enfriamiento. La búsqueda de Apify ya se había pagado y el candidato se
    quemaba sin haberse evaluado. «Sin juzgar» es «no sé»: queda `error`."""

    def _incompleto(self, **lo_que_dice_el_juez: Any) -> SimpleNamespace:
        base = BaseMejora(ids={CAND: 77})
        return correr_mejora(
            [grupo(SKU)], ia=[propone(CAND)], base=base, medidos={CAND: 10},
            # 5 comparables parejos: de estar completo, GANARÍA de sobra.
            filas={77: rivales(SKU, mismos=5, otros=4, pendientes=1)},
            juez=lambda skus, tid: lo_que_dice_el_juez)

    def test_queda_en_error_y_no_en_sin_mejora(self):
        t = self._incompleto(detenido="tope de gasto")
        self.assertEqual(t.juzgados, [([SKU], 77)], "sí se intentó juzgar el candidato")
        self.assertEqual(t.base.estado_de(SKU, CAND), "error")
        self.assertEqual(t.base.resueltos[t.base.iids[(SKU, CAND)]], ("error", 77, 5, 10),
                         "lo que se alcanzó a medir queda anotado")
        self.assertEqual((t.out["errores"], t.out["sin_mejora"], t.out["sugerencias"]), (1, 0, 0))
        self.assertEqual(t.out["paginas"], 1, "la página de Apify sí se pagó")

    def test_quien_llama_se_entera_de_por_que(self):
        """Antes `detenido` quedaba en None y la corrida parecía limpia."""
        t = self._incompleto(detenido="tope de gasto")
        self.assertEqual(t.out["detenido"], "juez: tope de gasto")
        self.assertEqual(t.base.bitacora[0]["estado"], "parcial")

    def test_un_solo_rival_sin_contestar_tampoco_concluye(self):
        """Sin que el juez se detenga: el modelo dejó un número sin contestar."""
        t = self._incompleto()
        self.assertEqual(t.base.estado_de(SKU, CAND), "error")
        self.assertEqual(t.out["sin_mejora"], 0)

    def test_con_el_juicio_completo_la_regla_de_siempre_sigue_viva(self):
        """El control: el arreglo no convirtió todo en `error`."""
        base = BaseMejora(ids={CAND: 77})
        t = correr_mejora([grupo(SKU)], ia=[propone(CAND)], base=base, medidos={CAND: 10},
                          filas={77: rivales(SKU, mismos=1, otros=9)})
        self.assertEqual(base.estado_de(SKU, CAND), "sin_mejora")
        self.assertEqual((t.out["errores"], t.out["sin_mejora"]), (0, 1))

    def test_sin_rivales_contables_no_es_juicio_incompleto(self):
        """SEGUNDO DEFECTO, del propio arreglo: `resumen` da `completo=False`
        también con CERO rivales contables (todo lo que trajo el candidato es
        nuestro o no tiene precio). Eso no es «no sé»: no queda nada que juzgar y
        el candidato sí se probó. Caía en `error` y, como `error` no concluye, se
        volvía a proponer en cada corrida. Lo que decide son los PENDIENTES."""
        nuestras = [{**f, "es_nuestro": True, "cuenta": False, "comparable": False,
                     "pendiente": False} for f in rivales(SKU, mismos=10)]
        self.assertEqual(J.resumen(nuestras)["completo"], False)
        base = BaseMejora(ids={CAND: 77})
        t = correr_mejora([grupo(SKU)], ia=[propone(CAND)], base=base, medidos={CAND: 10},
                          filas={77: nuestras})
        self.assertEqual(base.estado_de(SKU, CAND), "sin_mejora")
        self.assertEqual((t.out["errores"], t.out["sin_mejora"]), (0, 1))


def fila_universo(sku: str) -> dict[str, Any]:
    """Un SKU sano tal como sale de `_SQL_UNIVERSO`."""
    return {"sku": sku, "termino_id": 10, "termino_origen": "ia", "termino": TERMINO,
            "estado": "ok", "medido_en": hace(2), "categoria_id": "MLM0001",
            "categoria_nombre": "Soportes", "ruta": "Hogar > Seguridad > Soportes",
            "activa": True}


def intento(estado: str, *, candidato: str = "Soporte Pared Cámara",
            resuelto: dt.datetime | None = None, creado: float = 3) -> dict[str, Any]:
    return {"sku": SKU, "termino_candidato": candidato, "termino_anterior": TERMINO,
            "estado": estado, "creado_en": hace(creado), "resuelto_en": resuelto}


class R10_ErrorYBloqueadoNoCastiganAlSku(unittest.TestCase):
    """DEFECTO: `_resolver` ponía `resuelto_en = now()` a todo estado distinto de
    `propuesto`, y `elegibles` contaba como enfriamiento CUALQUIER intento con
    `resuelto_en` reciente, sin mirar el estado.

    POR QUÉ IMPORTABA: `error` (Apify no volvió, o el juicio quedó incompleto) y
    `bloqueado` (ML no dejó ver) no son respuestas: son «no se pudo probar». Aun
    así mandaban al SKU 60 días fuera, del lote Y del botón del panel, y el
    re-reclamo que `_reclamar` sí permite para esos estados no se alcanzaba."""

    def _elegibles(self, historia: list[dict[str, Any]]) -> dict[str, Any]:
        base = BaseMejora(universo=[fila_universo(SKU)], historia=historia)
        flojo = rivales(SKU, mismos=1, otros=9)
        with base.puesta(), mock.patch.object(
                CM.competencia_juez, "filas",
                lambda skus, termino_id=None: [f for f in flojo if f["sku"] in skus]):
            return CM.elegibles()

    def test_un_intento_no_concluyente_no_enfria_ni_cuenta_como_probado(self):
        """Hasta con `resuelto_en` de ayer (filas escritas por el código viejo):
        lo que decide es el ESTADO.

        Ajustada después (hallazgo mejora-error-bloqueado-sin-tope): un `error` o
        un `bloqueado` RECIENTE sí va a «ya probados» durante `REINTENTO_DIAS`,
        para que el modelo no repita el mismo candidato muerto en cada corrida.
        Sigue sin enfriar ni contar, y pasado ese plazo se vuelve a proponer."""
        viejo = CM.REINTENTO_DIAS + 1
        for estado in ("error", "bloqueado", "propuesto"):
            with self.subTest(estado=estado):
                e = self._elegibles([intento(estado, resuelto=hace(1), creado=viejo)])
                self.assertEqual(e["fuera"], {})
                self.assertEqual(len(e["grupos"]), 1)
                intentados = e["grupos"][0]["skus"][0]["intentados"]
                self.assertNotIn(CM.llave("Soporte Pared Cámara"), intentados,
                                 "un candidato que no se pudo probar se puede volver a proponer")
                e = self._elegibles([intento(estado, resuelto=hace(1), creado=1)])
                self.assertEqual(e["fuera"], {}, "reciente tampoco enfría")
                self.assertEqual(CM.llave("Soporte Pared Cámara") in
                                 e["grupos"][0]["skus"][0]["intentados"],
                                 estado != "propuesto",
                                 "un propuesto en curso no se esconde: el reclamo lo detecta")

    def test_lo_concluyente_si_enfria_y_si_cuenta(self):
        """El control: lo que SÍ se probó sigue descansando 60 días y sigue en la
        lista de «no repetir»."""
        reciente = self._elegibles([intento("sin_mejora", resuelto=hace(1))])
        self.assertEqual((reciente["grupos"], reciente["fuera"]), ([], {"en enfriamiento": 1}))
        viejo = self._elegibles([intento("sin_mejora", resuelto=hace(CM.ENFRIAMIENTO_DIAS + 1))])
        self.assertIn(CM.llave("Soporte Pared Cámara"),
                      viejo["grupos"][0]["skus"][0]["intentados"])

    def test_resolver_no_le_pone_fecha_a_lo_que_no_concluyo(self):
        """Sin base no se puede ejecutar el `case`, así que se lee qué valor viaja
        en su parámetro. Si alguien reescribe el SQL de `_resolver`, esta prueba
        pide que lo haga a conciencia.

        Ajustada después: la decisión de la fecha pasó a Python (un booleano),
        porque ahora depende también de la ronda: en la ronda `RONDAS_MAX` un
        fallo del candidato ya concluye (`error` → `sin_mejora`; `bloqueado` se
        queda, con fecha)."""
        casos = [("error", None, "error", False), ("bloqueado", None, "bloqueado", False),
                 ("propuesto", None, "propuesto", False), ("sin_mejora", None, "sin_mejora", True),
                 ("medido", None, "medido", True), ("error", 1, "error", False),
                 ("error", CM.RONDAS_MAX, "sin_mejora", True),
                 ("bloqueado", CM.RONDAS_MAX, "bloqueado", True)]
        for estado, ronda, final, con_fecha in casos:
            with self.subTest(estado=estado, ronda=ronda):
                vistos: list[tuple[str, tuple]] = []
                with mock.patch.object(CM.supabase_db, "execute",
                                       side_effect=lambda sql, p=None: vistos.append((sql, p)) or 1):
                    devuelto = CM._resolver(7, estado, termino_id=77, ronda=ronda,
                                            despues={"comparables": 4, "total": 10})
                (sql, params), = vistos
                m = re.search(r"resuelto_en = case when %s then now\(\) else null end", sql)
                self.assertIsNotNone(m, "cambió la forma del SQL de _resolver: revisar esta prueba")
                self.assertEqual((devuelto, params[0]), (final, final))
                self.assertIs(params[sql[:m.start()].count("%s")], con_fecha)

    def test_las_dos_listas_de_estados_no_se_pisan(self):
        self.assertEqual(set(CM._NO_CONCLUYENTES), {"propuesto", "error", "bloqueado"})
        self.assertFalse(set(CM._NO_CONCLUYENTES) & set(CM._PROBADOS))


class R11_ElGastoQuedaAunqueRevienteElPaso3(unittest.TestCase):
    """DEFECTO: `mejorar` no tenía `try/finally`. Si el paso 3 (juzgar y decidir)
    lanzaba —una conexión muerta en `filas`, en `juzgar_skus` o en
    `fetch_scalar`—, la función salía sin llamar a `registrar`.

    POR QUÉ IMPORTABA: para entonces las propuestas de IA y las páginas de Apify
    YA se pagaron. Sin la fila en `ops.process_log` ese gasto no existía para
    nadie: ni para el tope de 24 h ni para quien revisa cuánto costó la corrida.

    SEGUNDO DEFECTO, del propio arreglo (reg-2): el `finally` registraba el gasto
    pero con estado 'ok', porque solo miraba `detenido`, y una excepción no lo
    pone. Una corrida muerta con intentos colgados quedaba como limpia. Ahora es
    'error'. (Y `usd` lleva solo las propuestas: el juez tiene su propia fila.)"""

    def _revienta(self, **kw: Any) -> SimpleNamespace:
        return correr_mejora([grupo(SKU)], ia=[propone(CAND, usd=0.002)],
                             base=BaseMejora(ids={CAND: 77}), medidos={CAND: 10}, **kw)

    def _lo_pagado_quedo_anotado(self, t: SimpleNamespace) -> None:
        self.assertIsInstance(t.error, RuntimeError, "la excepción SUBE: no se esconde")
        self.assertEqual(len(t.base.bitacora), 1)
        fila_log = t.base.bitacora[0]
        self.assertEqual((fila_log["accion"], fila_log["estado"]), ("terminos", "error"))
        self.assertEqual((fila_log["detalle"]["usd"], fila_log["detalle"]["usd_propuestas"]),
                         (0.002, 0.002))
        self.assertEqual(fila_log["detalle"]["paginas"], 1)
        self.assertAlmostEqual(t.presupuesto.gastado, 0.002)
        self.assertEqual(t.lotes, [[CAND]], "la página de Apify se pidió antes del tropiezo")

    def test_si_truena_la_lectura_de_filas(self):
        def muerta(skus, termino_id=None):
            raise RuntimeError("connection already closed")

        self._lo_pagado_quedo_anotado(self._revienta(filas=muerta))

    def test_si_truena_el_juez(self):
        def muerto(skus, termino_id):
            raise RuntimeError("pool agotado")

        self._lo_pagado_quedo_anotado(self._revienta(juez=muerto))

    def test_si_truena_la_busqueda_del_termino_medido(self):
        base = BaseMejora()
        base.fetch_scalar = mock.Mock(side_effect=RuntimeError("server closed the connection"))
        t = correr_mejora([grupo(SKU)], ia=[propone(CAND, usd=0.002)], base=base,
                          medidos={CAND: 10})
        self._lo_pagado_quedo_anotado(t)


class R12_SinCubrirYElTopeDePaginas(unittest.TestCase):
    """TRES DEFECTOS del mismo tope (`max_paginas`, las búsquedas nuevas de Apify):

      · `sin_cubrir` se sumaba por CANDIDATO: un grupo con dos candidatos fuera
        del tope contaba doble, y el número superaba al total de SKUs.
      · Alcanzado el tope el bucle seguía llamando a `proponer` (IA pagada) para
        todos los grupos restantes, aunque sus candidatos ya no se podían medir.
      · El corte no debe llevarse de paso el reuso: con `max_paginas=0` un
        candidato que YA está medido en el catálogo es gratis y se evalúa.

    POR QUÉ IMPORTABA: `sin_cubrir` es lo que el operador lee para saber cuánto
    faltó; y cada propuesta inútil es dinero y un intento que queda colgado."""

    def test_sin_cubrir_cuenta_una_vez_por_grupo(self):
        t = correr_mejora([grupo(SKU)], ia=[propone(CAND, CAND2)], max_paginas=0)
        self.assertEqual((t.out["skus"], t.out["sin_cubrir"]), (1, 1),
                         "dos candidatos sin cupo siguen siendo UN SKU sin cubrir")
        self.assertEqual(t.out["detenido"], "tope de páginas de Apify")
        self.assertEqual((t.base.reclamos, t.lotes, t.out["paginas"]), ([], [], 0),
                         "lo que no cabe ni se reclama ni se mide")

    def test_un_grupo_con_un_candidato_que_si_entro_no_esta_sin_cubrir(self):
        """De dos candidatos, uno cabe y el otro no: el grupo SÍ se atendió."""
        base = BaseMejora(ids={CAND: 77})
        t = correr_mejora([grupo(SKU, SKU2)], ia=[propone(CAND, CAND2)], base=base,
                          medidos={CAND: 10}, max_paginas=1,
                          filas={77: rivales(SKU, otros=10) + rivales(SKU2, otros=10)})
        self.assertEqual(t.out["sin_cubrir"], 0)
        self.assertEqual(t.lotes, [[CAND]])
        self.assertEqual(t.out["detenido"], "tope de páginas de Apify", "pero se dice")

    def test_con_el_tope_alcanzado_deja_de_proponer(self):
        """Tres grupos y una sola página: UNA propuesta. `ia` trae una sola
        respuesta, así que una segunda llamada reventaría la prueba."""
        grupos = [grupo(SKU, termino="termino uno", tid=1),
                  grupo(SKU2, "PRU-0003-GRI", termino="termino dos", tid=2),
                  grupo("PRU-0004-AZU", termino="termino tres", tid=3)]
        t = correr_mejora(grupos, ia=[propone(CAND)], medidos={CAND: 0}, max_paginas=1)
        self.assertIsNone(t.error)
        self.assertEqual(t.ia.call_count, 1)
        self.assertEqual(t.out["detenido"], "tope de páginas de Apify")
        self.assertEqual(t.out["sin_cubrir"], 3, "los SKUs de los dos grupos que no se vieron")
        self.assertAlmostEqual(t.out["usd_ia"], 0.001)
        self.assertEqual(t.base.bitacora[0]["estado"], "parcial")

    def test_con_cero_paginas_el_reuso_del_catalogo_sigue_funcionando(self):
        """`max_paginas=0` = «no pagues ni una búsqueda nueva», no «no hagas
        nada»: lo que ya está medido se juzga y puede ganar."""
        base = BaseMejora(catalogo=[{"id": 55, "termino": "Soporte Pared Cámara Seguridad",
                                     "estado": "ok", "medido_en": hace(2), "resultados": 10}])
        t = correr_mejora([grupo(SKU)], ia=[propone(CAND)], base=base, max_paginas=0,
                          filas={55: rivales(SKU, mismos=4, otros=6)})
        self.assertEqual((t.out["reusados"], t.out["paginas"], t.lotes), (1, 0, []))
        self.assertEqual(t.juzgados, [([SKU], 55)])
        self.assertEqual(t.base.estado_de(SKU, "Soporte Pared Cámara Seguridad"), "medido")
        self.assertEqual((t.out["sugerencias"], t.out["sin_cubrir"], t.out["detenido"]),
                         (1, 0, None))

    def test_el_corte_por_tope_de_ia_suma_a_lo_ya_sin_cubrir(self):
        """CUARTO DEFECTO (reg-3): el corte de arriba del bucle ASIGNABA
        `sin_cubrir` y pisaba lo ya sumado por grupos sin cupo, y cambiaba el
        motivo. Con `max_paginas=0`, tres grupos: los dos primeros se quedan sin
        página y el tercero ya no alcanza tope de IA. Son TRES SKUs sin cubrir, y
        el primer motivo es el que se dice."""
        grupos = [grupo(SKU, termino="termino uno", tid=1),
                  grupo(SKU2, termino="termino dos", tid=2),
                  grupo("PRU-0003-GRI", termino="termino tres", tid=3)]
        t = correr_mejora(grupos, ia=[propone(CAND, usd=0.002), propone(CAND2, usd=0.002)],
                          tope_usd=0.003, max_paginas=0)
        self.assertIsNone(t.error)
        self.assertEqual(t.ia.call_count, 2)
        self.assertEqual((t.out["skus"], t.out["sin_cubrir"]), (3, 3))
        self.assertEqual(t.out["detenido"], "tope de páginas de Apify")


class R13_ContadoresYGastoDeLaCorrida(unittest.TestCase):
    """DOS DEFECTOS del resumen que va a `ops.process_log`:

      · `sin_mejora` dependía del ORDEN de los candidatos: cuando el segundo
        desplazaba al ganador anterior, ese intento se cerraba `sin_mejora` en la
        base pero no se sumaba al contador.
      · `usd_ia` solo sumaba las propuestas; lo que gastaba el juez al evaluar
        los candidatos entraba al `Presupuesto` pero no a ese número, que es el
        que la bitácora guardaba como `usd`.

    POR QUÉ IMPORTABA: son los números con los que se decide si la corrida valió
    lo que costó. Un contador que cambia según cómo contestó el modelo, y un
    costo que deja fuera la mitad, no sirven para eso.

    Ajustada después: `usd_ia` sigue incluyendo al juez, pero la BITÁCORA ya no.
    `juzgar_skus` registra su propia fila 'juez', así que sumarlo también aquí lo
    contaba dos veces; la fila 'terminos' lleva en `usd` solo las propuestas y
    el juez aparte, como `usd_juez`."""

    FILAS = {71: rivales(SKU, mismos=3, otros=7),      # CAND gana con 3
             72: rivales(SKU, mismos=5, otros=5)}      # CAND2 gana con 5: es el mejor

    def _dos_ganadores(self, *orden: str, **kw: Any) -> SimpleNamespace:
        return correr_mejora([grupo(SKU)], ia=[propone(*orden)],
                             base=BaseMejora(ids={CAND: 71, CAND2: 72}),
                             medidos={CAND: 10, CAND2: 10}, filas=self.FILAS, **kw)

    def test_sin_mejora_no_depende_del_orden_de_los_candidatos(self):
        for orden in ((CAND, CAND2), (CAND2, CAND)):
            with self.subTest(orden=orden):
                t = self._dos_ganadores(*orden)
                # Los estados finales son los mismos en los dos órdenes…
                self.assertEqual(t.base.estado_de(SKU, CAND2), "medido")
                self.assertEqual(t.base.estado_de(SKU, CAND), "sin_mejora")
                # …y el resumen tiene que contarlos igual.
                self.assertEqual((t.out["sugerencias"], t.out["sin_mejora"]), (1, 1))
                self.assertEqual(t.out["detalle"], [{"sku": SKU, "termino": CAND2, "antes": 0,
                                                     "despues": 5}])
                self.assertEqual(t.base.bitacora[0]["detalle"]["sin_mejora"], 1)

    def test_usd_ia_incluye_lo_que_gasto_el_juez(self):
        t = self._dos_ganadores(CAND, CAND2, juez=lambda skus, tid: {"usd": 0.004})
        # Una propuesta de 0.001 y dos candidatos juzgados a 0.004 cada uno.
        self.assertAlmostEqual(t.out["usd_ia"], 0.009)
        self.assertAlmostEqual(t.out["usd_ia"], t.presupuesto.gastado,
                               msg="el resumen y el tope tienen que decir lo mismo")
        self.assertAlmostEqual(t.out["usd_propuestas"], 0.001)
        # La fila 'terminos' no repite lo que el juez ya dejó en su fila 'juez'.
        detalle = t.base.bitacora[0]["detalle"]
        self.assertAlmostEqual(detalle["usd"], 0.001)
        self.assertAlmostEqual(detalle["usd_juez"], 0.008)


# ═════════════════════════════════════════════════════════════════════════════
#  routers/competencia
# ═════════════════════════════════════════════════════════════════════════════

def juzgado(externo_id: str, clase: str = "mismo") -> dict[str, Any]:
    """Una fila de `competencia_juez.filas` con veredicto vigente."""
    return {"sku": SKU, "externo_id": externo_id, "clase": clase, "motivo": f"motivo {clase}",
            "unidades_rival": 1, "juzgado_en": None, "precio": 500.0, "cuenta": True,
            "vigente": True, "comparable": clase == "mismo", "pendiente": False,
            "titulo_nuestro": TITULO}


def pintadas() -> list[dict[str, Any]]:
    """Las filas de `busqueda_general` que el detalle ya iba a devolver."""
    return [{"externo_id": "MLM1", "titulo": "artículo genérico uno", "precio": 500.0},
            {"externo_id": "MLM2", "titulo": "artículo genérico dos", "precio": 120.0},
            {"externo_id": "MLM9", "titulo": "artículo que el juez no conoce", "precio": 90.0}]


class R14_JuicioDeNoDejaLlavesSueltas(unittest.TestCase):
    """DEFECTO: `_juicio_de` anotaba las filas DENTRO del `try` y en el `except`
    solo quitaba `veredicto`. Si algo fallaba después de anotar (la sugerencia se
    lee al final, y su tabla no la comprueba `tablas_listas`), cada fila salía con
    `veredicto_motivo` y `veredicto_unidades` colgando y sin el bloque `juez`.

    POR QUÉ IMPORTABA: la promesa de esa función es que, si la capa del juez
    falla, el detalle del SKU queda EXACTAMENTE como antes de que existiera. Es
    lo que permite tener el código en producción con la migración a medias."""

    def _llamar(self, general: list, **parches: Any) -> dict[str, Any]:
        with mock.patch.object(settings, "competencia_juez_visible", True), \
             mock.patch.object(RC.competencia_juez, "tablas_listas", return_value=True), \
             mock.patch.object(RC.competencia_juez, "filas", return_value=[
                 juzgado("MLM1"), juzgado("MLM2", "refaccion")]), \
             mock.patch.object(RC.competencia_mejora, "sugerencia",
                               parches.get("sugerencia") or mock.Mock(return_value=None)), \
             mock.patch.object(RC.competencia_juez, "resumen",
                               parches.get("resumen") or mock.Mock(wraps=J.resumen)):
            return RC._juicio_de(SKU, general)

    def test_si_falla_despues_de_leer_las_filas_no_queda_ninguna_llave_nueva(self):
        for donde in ("sugerencia", "resumen"):
            with self.subTest(falla=donde):
                general = pintadas()
                antes = copy.deepcopy(general)
                with self.assertLogs(LOG_ROUTER, "WARNING"):
                    r = self._llamar(general, **{donde: mock.Mock(
                        side_effect=RuntimeError('relation "market_termino_intento" no existe'))})
                self.assertEqual(r, {})
                self.assertEqual(general, antes, "ni veredicto, ni motivo, ni unidades")

    def test_cuando_todo_sale_bien_las_tres_llaves_si_estan(self):
        """El control: la prueba de arriba no pasa porque nunca se anote nada."""
        general = pintadas()
        r = self._llamar(general)
        self.assertIn("juez", r)
        for g in general:
            for llave in ("veredicto", "veredicto_motivo", "veredicto_unidades"):
                self.assertIn(llave, g)
        self.assertEqual([g["veredicto"] for g in general], ["mismo", "refaccion", None])


class R15_JuzgarDicePorQueNoJuzgo(unittest.TestCase):
    """DEFECTO: POST /juez/juzgar solo daba error si la corrida venía `detenido`.
    Con UN SKU el proveedor caído nunca llega a «5 fallos seguidos», así que la
    ruta respondía 200 con `veredictos: 0` y tiraba `ultimo_motivo`. Y para un SKU
    sin título nuestro contestaba lo mismo, siempre.

    POR QUÉ IMPORTABA: el admin veía «listo, 0 veredictos» sin saber si faltaba la
    llave, el saldo, el título, o si no había nada que juzgar. Ahora: 502 con el
    motivo cuando la IA no contestó, 503 cuando contestó pero la BASE no guardó,
    409 cuando no hay título con qué comparar.

    Aquí corren `juzgar_skus` y `resumen_sku` DE VERDAD sobre una base falsa: una
    prueba con el juez suplantado no notaría si `ultimo_motivo` cambia de nombre."""

    RUTA = "/api/competencia/juez/juzgar"
    ADMIN = SimpleNamespace(autenticado=True, tipo="persona", rol="admin", actor="admin@prueba")

    def setUp(self) -> None:
        self.app = FastAPI()
        self.app.include_router(RC.router)
        self.app.dependency_overrides[RC.solo_admin] = lambda: self.ADMIN
        self.cli = TestClient(self.app)

    def _juzgar(self, base: BaseJuez, llm: Any) -> Any:
        with mock.patch.object(settings, "competencia_juez_escritura", True), \
             mock.patch.object(settings, "competencia_juez_modelo", MODELO), \
             mock.patch.object(RC.competencia_juez, "tablas_listas", return_value=True), \
             base.puesta(), mock.patch.object(J.ia_json, "completar_json", llm):
            return self.cli.post(self.RUTA, json={"sku": "SKU-A"})

    def test_502_con_el_motivo_cuando_la_ia_no_contesta(self):
        base, llm = BaseJuez(pendientes_de("SKU-A")), mock.Mock(return_value=dict(CAIDA))
        r = self._juzgar(base, llm)
        self.assertEqual(r.status_code, 502)
        self.assertIn("HTTP 503", r.json()["detail"], "el motivo del proveedor llega al panel")
        llm.assert_called_once()
        self.assertEqual(base.transacciones, [])

    def test_409_cuando_el_sku_no_tiene_titulo(self):
        base, llm = BaseJuez(pendientes_de("SKU-A", titulo_nuestro=None)), mock.Mock()
        r = self._juzgar(base, llm)
        self.assertEqual(r.status_code, 409)
        self.assertIn("título", r.json()["detail"])
        llm.assert_not_called()

    def test_si_lo_que_falla_es_la_base_el_detalle_lo_nombra(self):
        """El LLM contestó bien y no se pudo guardar: no es un 200, y tampoco es
        culpa de la IA. Antes salía 502 «La IA no contestó: no se pudo guardar en
        la base», justo el mal diagnóstico que R06 quería evitar —y ese veredicto
        ya se pagó. Esta prueba solo pedía «guardar» y no lo notaba."""
        base = BaseJuez(pendientes_de("SKU-A"), rota=RuntimeError("deadlock detected"))
        with self.assertLogs(LOG_JUEZ, "WARNING"):
            r = self._juzgar(base, mock.Mock(side_effect=obediente))
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"], "Los veredictos se pagaron pero no se pudieron "
                                             "guardar en la base. Intenta de nuevo.")
        self.assertNotIn("IA no contestó", r.json()["detail"])

    def test_sin_nada_pendiente_sigue_siendo_200(self):
        """El control: «0 veredictos» porque ya todo estaba juzgado NO es un
        error, y la ruta no debe inventar uno."""
        ya = [fila("SKU-A", "R1", vigente=True, clase="mismo"),
              fila("SKU-A", "R2", vigente=True, clase="refaccion", posicion=2)]
        llm = mock.Mock()
        r = self._juzgar(BaseJuez(ya), llm)
        self.assertEqual(r.status_code, 200)
        cuerpo = r.json()
        self.assertEqual((cuerpo["veredictos"], cuerpo["sin_juzgar"]), (0, 0))
        self.assertEqual((cuerpo["juez"]["pendientes"], cuerpo["juez"]["sin_titulo"]), (0, False))
        llm.assert_not_called()

    def test_cuando_juzga_devuelve_200_con_el_resumen(self):
        base = BaseJuez(pendientes_de("SKU-A"))
        r = self._juzgar(base, mock.Mock(side_effect=obediente))
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["veredictos"], r.json()["sin_juzgar"]), (2, 0))
        self.assertEqual(len(base.guardados()), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
