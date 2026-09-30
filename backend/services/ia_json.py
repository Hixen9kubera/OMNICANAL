"""
ia_json.py — Una llamada al LLM que devuelve JSON, dice cuánto costó y NO cambia
de proveedor ni de modelo a escondidas.

── POR QUÉ NO ES `ia_generadores._completar` ──────────────────────────────────
`_completar` está hecha para REDACTAR (títulos, descripciones) y eso la hace mala
para CLASIFICAR:

  · temperature 0.7 fija → el mismo rival puede salir «mismo» hoy y «otra gama»
    mañana;
  · sin modo JSON → hay que rescatar el objeto con una regex;
  · tira el `usage` → no hay de dónde sacar el costo;
  · no mira `finish_reason` → una respuesta cortada llega como `ok: True`;
  · y si DeepSeek falla cae a Claude Opus EN SILENCIO: un lote de miles de
    rivales podría cobrarse entero al modelo caro sin que nadie se entere. Eso ya
    vació el saldo una vez (22-sep-2026).

Tiene 20 llamadores y pruebas que la suplantan con `{ok, texto}`, así que no se
toca: esto es una función hermana.

── EL CONTRATO ────────────────────────────────────────────────────────────────
`completar_json(system, user, modelo=...)` SIEMPRE devuelve un dict y NUNCA lanza:

    ok        bool   — hubo respuesta y traía un objeto JSON
    datos     dict   — el objeto (solo si ok)
    texto     str    — la respuesta cruda, para rescatar lo parcial si se cortó
    cortado   bool   — el modelo se quedó sin tokens a media respuesta
    proveedor, modelo
    uso       {entrada, cache, salida}  — tokens reales que reportó el proveedor
    usd       float  — costo ESTIMADO a precio de lista (la factura manda)
    motivo    str    — por qué falló (solo si no ok)

── FALLA CERRADO ──────────────────────────────────────────────────────────────
Solo corre un modelo que esté en `MODELOS`. El nombre del modelo llega de una
variable de entorno —texto libre— y un modelo sin precio dejaría ciego al tope de
gasto de quien llama: sumaría cero mientras la cuenta corre. Por eso un modelo
desconocido NO se llama: devuelve `ok: False` sin gastar.

Si el modelo pedido falla, la respuesta es `ok: False`. No hay segundo intento
con otro proveedor: quien llama decide.

── ES BLOQUEANTE ──────────────────────────────────────────────────────────────
httpx síncrono y SDK síncrono. Desde una corrutina va SIEMPRE en
`asyncio.to_thread` (regla 11); desde un hilo o un script se llama directo.
Con DeepSeek, `timeout` corta por RELOJ el goteo (`_post_con_plazo`), pero una
espera sin bytes la corta la inactividad de httpx: un intento dura hasta
≈2×`timeout`.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from config import settings

log = logging.getLogger("omnicanal.ia_json")

# USD por MILLÓN de tokens a precio de lista, consultados el 30-sep-2026: los de
# DeepSeek son los de `fulfillment_ia.PRECIOS`; los de Claude, la tabla pública de
# Anthropic. Es una ESTIMACIÓN y por arriba (DeepSeek cobra la mitad fuera de
# hora pico y aquí no se descuenta). La factura manda.
#
# `extra` son los parámetros que SOLO ese modelo acepta. Claude Haiku 4.5 admite
# temperature; los 5.5 la rechazan con un 400 y además piensan solos: se les baja
# el esfuerzo, porque para clasificar títulos pensar a fondo solo encarece.
MODELOS: dict[str, dict[str, Any]] = {
    "deepseek-flash": {"proveedor": "deepseek", "entrada": 0.30, "cache": 0.006, "salida": 1.20},
    "deepseek-v4-pro": {"proveedor": "deepseek", "entrada": 1.32, "cache": 0.044, "salida": 3.96},
    "claude-haiku-4-5": {"proveedor": "claude", "entrada": 1.00, "cache": 0.10, "salida": 5.00,
                         "extra": {"temperature": 0}, "holgura": 1},
    "claude-sonnet-5-5": {"proveedor": "claude", "entrada": 2.00, "cache": 0.20, "salida": 10.00,
                          "extra": {"extra_body": {"output_config": {"effort": "low"}}},
                          "holgura": 4},
    "claude-opus-5-5": {"proveedor": "claude", "entrada": 4.00, "cache": 0.20, "salida": 20.00,
                        "extra": {"extra_body": {"output_config": {"effort": "low"}}},
                        "holgura": 4},
}

# Esperas ante 429, 5xx y respuestas vacías. Cortas a propósito: un lote que
# falla se reintenta completo después, y hay trabajos que un usuario está mirando.
_ESPERAS = (2.0, 6.0)
_TIMEOUT = 120.0
_HTTP_REINTENTABLE = (429, 500, 502, 503, 504)
_ANTHROPIC_URL = "https://api.anthropic.com"


def proveedor_de(modelo: str) -> str | None:
    return (MODELOS.get(modelo) or {}).get("proveedor")


def costo_usd(modelo: str, uso: dict[str, Any] | None) -> float | None:
    """Costo estimado a precio de lista. `None` si el modelo no tiene precio."""
    p = MODELOS.get(modelo)
    if not p or not isinstance(uso, dict):
        return None
    entrada, cache, salida = (_tokens(uso.get(k)) for k in ("entrada", "cache", "salida"))
    cache = min(cache, entrada)
    return round(((entrada - cache) * p["entrada"] + cache * p["cache"]
                  + salida * p["salida"]) / 1_000_000, 6)


def _tokens(v: Any) -> int:
    """Un conteo de tokens como entero, o 0. El proveedor es quien manda este
    dato: si llega algo raro no debe tumbar la llamada. Ojo con `Infinity` o
    `1e999`: `json.loads` los acepta y `int(inf)` lanza OverflowError."""
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def leer_objeto(texto: str) -> dict[str, Any] | None:
    """El objeto JSON de la respuesta, o `None`. Aguanta cercas y prosa."""
    t = re.sub(r"^```(?:json)?|```$", "", (texto or "").strip(), flags=re.MULTILINE).strip()
    encontrado = re.search(r"\{.*\}", t, re.DOTALL)
    for candidato in (t, encontrado.group(0) if encontrado else None):
        if not candidato:
            continue
        try:
            d = json.loads(candidato)
        except (ValueError, TypeError):
            continue
        if isinstance(d, dict):
            return d
    return None


def _fallo(motivo: str, modelo: str, uso: dict[str, int] | None = None) -> dict[str, Any]:
    """Un fallo TAMBIÉN puede haber costado: una respuesta vacía o ilegible se
    cobra igual. Por eso lleva el uso acumulado de los intentos y su costo; un
    fallo que reportara cero dejaría ciego al tope de gasto de quien llama."""
    return {"ok": False, "motivo": motivo, "proveedor": proveedor_de(modelo), "modelo": modelo,
            "texto": "", "cortado": False, "uso": uso or {},
            "usd": costo_usd(modelo, uso) or 0.0}


class _PlazoAgotado(httpx.TimeoutException):
    """Se acabó el reloj de la llamada aunque el proveedor siguiera mandando bytes."""


def _post_con_plazo(url: str, *, json: Any, headers: dict[str, str],
                    timeout: float) -> httpx.Response:
    """`httpx.post` con plazo de RELOJ.

    El `timeout` de httpx es de INACTIVIDAD: cada trozo que llega reinicia su
    cuenta. DeepSeek, saturado, mantiene viva una petición sin streaming mandando
    líneas vacías hasta que la atiende, así que una llamada «de 30 s» podía
    retener minutos el hilo y un cupo de `competencia_juez._turno`. Aquí se lee a
    trozos y se corta al pasar `timeout` segundos desde el arranque.

    Lo que el reloj NO corta es una espera SIN bytes: el reloj se mira cuando algo
    llega (las cabeceras, cada trozo), y conectar o un silencio después del último
    trozo los corta la inactividad de httpx, `timeout` más tarde. Peor caso de un
    intento: ≈2×`timeout` (un trozo justo antes del plazo y luego silencio), sin
    contar el DNS, que httpx no acota. No hay un vigilante que cierre el socket
    desde otro hilo: en Linux cerrar el descriptor no despierta una lectura
    bloqueada, y hacer `shutdown` exige meterse en las tripas de httpcore y ssl."""
    fin = time.monotonic() + timeout
    with httpx.stream("POST", url, json=json, headers=headers, timeout=timeout) as r:
        # Las cabeceras también cuentan: si llegaron tarde se corta ya, sin esperar
        # al primer trozo, que podría ser otro `timeout` entero de silencio (≈3×).
        if time.monotonic() > fin:
            raise _PlazoAgotado("plazo agotado")
        trozos = []
        for trozo in r.iter_bytes():
            if time.monotonic() > fin:
                raise _PlazoAgotado("plazo agotado")
            trozos.append(trozo)
        return httpx.Response(r.status_code, content=b"".join(trozos))


def _deepseek(system: str, user: str, modelo: str, max_tokens: int, razonar: bool,
              timeout: float, intentos: int) -> dict[str, Any]:
    if not settings.deepseek_api_key:
        return _fallo("Falta DEEPSEEK_API_KEY.", modelo)
    cuerpo = {
        "model": modelo,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        # Los modelos v4 RAZONAN por defecto y ese razonamiento se cobra como
        # salida y cuenta contra `max_tokens`: medido el 30-sep-2026, con el
        # default 32 de 45 lotes se cortaron antes de escribir una sola línea de
        # JSON. Apagado se comporta como el alias `deepseek-chat`.
        "thinking": {"type": "enabled" if razonar else "disabled"},
        "max_tokens": max_tokens * (4 if razonar else 1),
    }
    url = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
    ultimo = "sin respuesta"
    # Tokens de TODOS los intentos, no solo del último: un 200 que se descarta
    # por venir vacío ya se cobró.
    acum = {"entrada": 0, "cache": 0, "salida": 0}
    for intento in range(max(1, intentos)):
        if intento:
            time.sleep(_ESPERAS[min(intento - 1, len(_ESPERAS) - 1)])
        try:
            r = _post_con_plazo(url, json=cuerpo, timeout=timeout,
                                headers={"Authorization": f"Bearer {settings.deepseek_api_key}"})
        except _PlazoAgotado:
            ultimo = "red: plazo agotado"
            continue
        except httpx.HTTPError as exc:
            ultimo = f"red: {type(exc).__name__}"
            continue
        except Exception as exc:                                   # noqa: BLE001
            # P. ej. una DEEPSEEK_BASE_URL mal escrita: no es de red y repetir no
            # lo arregla.
            ultimo = f"petición inválida: {type(exc).__name__}"
            break
        if r.status_code != 200:
            # El cuerpo de un 4xx dice POR QUÉ (saldo, modelo, formato) y no
            # lleva secretos: la llave viaja en la cabecera, no en la respuesta.
            ultimo = f"HTTP {r.status_code}: {r.text[:160]}"
            if r.status_code in _HTTP_REINTENTABLE:
                continue
            break
        try:
            d = r.json()
            u = d.get("usage") if isinstance(d.get("usage"), dict) else {}
            acum["entrada"] += _tokens(u.get("prompt_tokens"))
            acum["cache"] += _tokens(u.get("prompt_cache_hit_tokens"))
            acum["salida"] += _tokens(u.get("completion_tokens"))
            op = (d.get("choices") or [{}])[0]
            texto = ((op.get("message") or {}).get("content") or "").strip()
        except (ValueError, AttributeError, IndexError, TypeError, KeyError):
            ultimo = "HTTP 200 con cuerpo ilegible"
            continue
        if not texto:
            # El modo JSON de DeepSeek a veces contesta 200 con contenido vacío.
            ultimo = "HTTP 200 con respuesta vacía"
            continue
        return {"texto": texto, "cortado": op.get("finish_reason") == "length",
                "proveedor": "deepseek", "modelo": modelo, "uso": acum}
    return _fallo(f"DeepSeek no respondió ({ultimo}).", modelo, acum)


def _claude(system: str, user: str, modelo: str, max_tokens: int, razonar: bool,  # noqa: ARG001
            timeout: float, intentos: int) -> dict[str, Any]:
    if not settings.anthropic_api_key:
        return _fallo("Falta ANTHROPIC_API_KEY.", modelo)
    cfg = MODELOS[modelo]
    try:
        import anthropic

        # `base_url` EXPLÍCITA: sin ella el SDK toma ANTHROPIC_BASE_URL del
        # entorno del proceso, y una terminal que la traiga apuntando a otro lado
        # mandaría ahí la llave.
        # Ojo: el `timeout` del SDK tampoco es de reloj (lo aplica httpx por
        # fase, como en `_deepseek`). Aquí no se corta por reloj porque no se ha
        # medido que la API de Anthropic gotee bytes sin streaming; si pasara, el
        # corte de `_post_con_plazo` habría que traerlo a esta rama.
        cli = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=timeout,
                                  max_retries=max(0, intentos - 1), base_url=_ANTHROPIC_URL)
        # El pensamiento de los modelos que piensan solos cuenta contra
        # `max_tokens`: sin holgura, la respuesta se corta antes del JSON.
        msg = cli.messages.create(
            model=modelo, max_tokens=max(1024, max_tokens * cfg.get("holgura", 1)),
            system=system, messages=[{"role": "user", "content": user}],
            **cfg.get("extra", {}))
    except Exception as exc:                                       # noqa: BLE001
        return _fallo(f"Claude no respondió ({type(exc).__name__}: {str(exc)[:160]}).", modelo)
    # `_tokens` y no `int()`: esto va fuera del `try` y un conteo raro tiraría la
    # respuesta (y su costo) a la red de seguridad de `completar_json`.
    u = getattr(msg, "usage", None)
    leidos = _tokens(getattr(u, "cache_read_input_tokens", 0))
    uso = {"entrada": _tokens(getattr(u, "input_tokens", 0)) + leidos,
           "cache": leidos, "salida": _tokens(getattr(u, "output_tokens", 0))}
    # Una negativa llega con HTTP 200: hay que mirar `stop_reason` antes del texto.
    # Y se COBRA: el uso ya va leído, para que el tope de quien llama la vea.
    if getattr(msg, "stop_reason", None) == "refusal":
        return _fallo("Claude declinó la petición.", modelo, uso)
    texto = "".join(getattr(b, "text", "") or "" for b in (msg.content or [])
                    if getattr(b, "type", None) == "text").strip()
    return {"texto": texto, "cortado": getattr(msg, "stop_reason", None) == "max_tokens",
            "proveedor": "claude", "modelo": modelo, "uso": uso}


def completar_json(system: str, user: str, *, modelo: str, max_tokens: int = 2000,
                   razonar: bool = False, timeout: float = _TIMEOUT,
                   intentos: int = 3) -> dict[str, Any]:
    """Una llamada, un objeto JSON, y su costo. Ver el contrato en el docstring.

    `max_tokens` es el tamaño esperado de la RESPUESTA; el margen para que el
    modelo piense, si piensa, se suma aquí adentro. `timeout` e `intentos` dejan
    que un trabajo que alguien está mirando pida una llamada corta."""
    if modelo not in MODELOS:
        return _fallo(f"Modelo sin precio en ia_json.MODELOS: {modelo!r}. No se llama.", modelo)
    llamar = _deepseek if MODELOS[modelo]["proveedor"] == "deepseek" else _claude
    try:
        res = llamar(system, user, modelo, max_tokens, razonar, timeout, intentos)
    except Exception as exc:                                       # noqa: BLE001
        # El contrato es NUNCA lanzar: quien llama corre en hilos y una excepción
        # aquí tumbaría la corrida entera de un lote.
        return _fallo(f"Error inesperado del proveedor ({type(exc).__name__}).", modelo)
    if res.get("ok") is False:
        return res
    res["usd"] = costo_usd(modelo, res["uso"]) or 0.0
    datos = leer_objeto(res["texto"])
    if datos is None:
        # Se devuelve el texto igual: si se cortó, quien llama puede rescatar
        # las entradas completas en vez de perder el lote entero.
        return {**res, "ok": False,
                "motivo": "respuesta cortada" if res["cortado"] else "la respuesta no es JSON"}
    return {**res, "ok": True, "datos": datos}
