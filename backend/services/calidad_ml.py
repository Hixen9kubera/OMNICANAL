"""
calidad_ml.py — La «Calidad» de cada publicación ACTIVA de Mercado Libre.

═══════════════════════════════════════════════════════════════════════════════
QUÉ HACE
═══════════════════════════════════════════════════════════════════════════════
Le pregunta a ML `GET /item/{ITEM_ID}/performance` por cada publicación activa y
guarda la respuesta NORMALIZADA en `enrich.listing_health` (migración 0053),
con su serie diaria en `enrich.listing_health_hist` (ver «LAS TABLAS», abajo).
De ahí la leen la lista y el mosaico de /omnicanal (`resumen_por_items`), la
tarjeta del cajón (`detalle_por_items`), los botones de filtro junto al stepper
(`conteo`) y el WHERE de la rejilla de ML (`filtro_sql`,
`filtro_sql_experiencia`). La «Experiencia de compra» es la otra métrica de la
misma publicación y sale del mismo barrido (ver abajo).

Lo que contesta ML (medido el 22-sep-2026 contra producción):

    {entity_type: "USER_PRODUCT", entity_id: "MLMU…", score: 60,
     level: "medium", level_wording: "Estándar", calculated_at: "…Z",
     buckets: [{key, type: "USER_PRODUCT"|"ITEM", status, score (float),
                title: "Datos del producto"|"Condiciones de venta",
                variables: [{key, status: "PENDING"|"COMPLETED", score, title,
                             rules: [{key, status, progress, mode,
                                      wordings: {title, label, link}}]}]}]}

Niveles vistos: "medium"/"Estándar" y "good"/"Profesional". El nivel bajo NO se
ha visto, así que aquí no hay ni una clave de nivel escrita a mano: todo lo que
depende del nivel viaja con `level`/`level_wording` tal como vienen.

═══════════════════════════════════════════════════════════════════════════════
LA EXPERIENCIA DE COMPRA: MISMO BARRIDO, MISMO LOTE
═══════════════════════════════════════════════════════════════════════════════
Por cada publicación, el mismo barrido pregunta también
`GET /reputation/items/{id}/purchase_experience/integrators?locale=es_MX` (sin
`locale` ML contesta 400) y guarda la respuesta normalizada
(`normalizar_experiencia`) como la métrica `experiencia` de la publicación, en
la MISMA transacción que su calidad. UN SOLO ESCRITOR: nada de un segundo job
que escriba estas filas (así nació el aleteo de `is_fulfillment` en
channel.listings).

    reputation: {color: "red"|"orange"|"green"|"gray", text: "Mala"|…, value}

Medido el 22-sep (150 activas): gray sin text y value -1 en 90 (ML no tiene
ventas para calificarla → `sin_datos`, NUNCA 0), green "Buena" 100/75,
orange "Media" 65/50, red "Mala" 30. Colores y textos viajan tal cual.

Reglas de escritura, por publicación:
  · la calidad falla (error, 401, 429 agotado) → NADA se escribe. La
    experiencia ni se pregunta: su respuesta se tiraría igual, y el cupo es
    por aplicación;
  · la calidad sale (200 o el 400 conocido) y la experiencia falla → se
    escribe la calidad (y su día en la historia); la experiencia de antes se
    CONSERVA intacta y ese día no escribe historia de experiencia;
  · las dos salen → las dos filas y los dos días de historia.
El 401 de cualquiera de las dos APIs mata a la MISMA cuenta (un solo token,
una sola renovación por cuenta y barrido).

═══════════════════════════════════════════════════════════════════════════════
LAS TABLAS (0053): FORMATO LARGO Y SERIE DIARIA
═══════════════════════════════════════════════════════════════════════════════
`enrich.listing_health` guarda lo ÚLTIMO de cada (canal, account_id,
listing_id, metrica) —aquí canal `mercado_libre` y métricas `calidad` y
`experiencia`—, pensada para que Walmart, Amazon, TikTok o Temu sean filas
nuevas y no columnas nuevas. `valor` y `nivel` (bueno|medio|malo, para cruzar
canales) son opcionales; la etiqueta de ML va en `nivel_canal` y lo propio de
ML en `detalle` (jsonb). `account_id` es el uuid de core.accounts: se resuelve
desde `legacy_code` UNA vez por barrido (`ids_de_cuentas`).

`enrich.listing_health_hist` guarda UNA fila por (publicación, métrica, DÍA DE
MÉXICO): una segunda medición del mismo día la reemplaza. Los escalares van
siempre; `pendientes` y `detalle` solo si su `huella` (md5 de los dos jsonb,
la calcula Postgres) cambió respecto al día guardado ANTERIOR, y si no, NULL.

`medir` sigue devolviendo una fila ANCHA por publicación (calidad + `exp_*`);
`guardar` la parte por métrica y las lecturas la vuelven a juntar, así que la
lista, el cajón y los filtros reciben las mismas formas de siempre.

═══════════════════════════════════════════════════════════════════════════════
LA DISTINCIÓN QUE IMPORTA: RESPUESTA ≠ FALLO
═══════════════════════════════════════════════════════════════════════════════
ML solo califica ACTIVAS. A lo demás contesta 400 «Entity not calculated: Only
status active is supported» (y a los items de catálogo «Product items are not
supported»). Eso ES una respuesta: se guarda `estado='no_calculada'` con el
`message` en `motivo`. De 120 que kubera cree activas, 11 dieron ese 400.

SOLO el 400 cuyo `message` empieza con «Entity not calculated» es respuesta de
calidad (lista blanca `_PREFIJO_NO_CALCULADA`). Cualquier otro 400 (p.ej.
«Malformed access_token: …») es «no pude preguntar»: cuenta como error, no
escribe fila y al log va solo el código, jamás el `message` (puede traer el
token).

Cualquier otro fallo (red, 401 sin renovar, 429 agotado, 5xx, cuerpo raro) NO
escribe fila: "no pude preguntar" no es "no tiene", y una fila de error pisaría
la medición buena de ayer. La siguiente vuelta del job lo reintenta solo.

Del mismo modo, al LEER: «no pude leer» (sin base, o la 0053 sin aplicar)
NO es «sin medir». `resumen_por_items`/`detalle_por_items` lanzan
`CalidadNoDisponible` y quien llama deja la calidad en None («—»); solo una
consulta que sí corrió devuelve dict, aunque venga vacío.

═══════════════════════════════════════════════════════════════════════════════
EL JOB
═══════════════════════════════════════════════════════════════════════════════
Molde de `precios_venta.py`: un `asyncio.Lock` compartido por el scheduler y el
botón manual, corrida en una task de fondo, avance en `estado()`. Corre por
INTERVALO (`CALIDAD_ML_MIN`) y no a hora fija, y cada vuelta mide solo lo que no
tenga captura de HOY en hora de México: así un deploy que cae a la hora del
cron no se come el día (`alertas._toca_hoy` explica esa trampa), y una vuelta
que se quedó a medias por un 429 se completa sola en la siguiente. «Captura de
HOY» exige las DOS métricas capturadas hoy; una publicación cuya experiencia
falló (o nunca se preguntó) vuelve a entrar, pero solo si su calidad tiene más
de 3 h: unos 3-4 reintentos al día, no 13. Antes de `CALIDAD_ML_HORA_UTC`
(11 UTC = 05:00 CDMX) no hace nada.

Nace APAGADO (`CALIDAD_ML_ENABLED=false`) y obedece `SYNC_ENABLED` por encima,
igual que todo lo que habla con ML para refrescar catálogo (regla 3).

El token: `meli._access_token` en `to_thread` (es bloqueante) y, ante un 401,
`meli._renovar_con_candado` — NUNCA `refrescar_token` pelado: ML rota el
refresh_token en cada uso y dos renovaciones a la vez acaban en invalid_grant.
Por eso el job vive DENTRO del backend y no como cron de Railway: solo aquí
comparte el candado por cuenta con los webhooks de pedidos.

REGLA 11: todo lo que toca la base (`objetivo`, `ids_de_cuentas`, `guardar`,
lecturas, bitácora) es BLOQUEANTE y se llama con `asyncio.to_thread` desde las
corrutinas; el HTTP va con `httpx.AsyncClient` y las esperas con
`asyncio.sleep`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable

import httpx

from config import settings
from services import meli
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.calidad_ml")

# Formato largo, una fila por (canal, cuenta, publicación, métrica), y su
# serie diaria (migración 0053).
TABLA = "enrich.listing_health"
TABLA_HIST = "enrich.listing_health_hist"

_API = "https://api.mercadolibre.com"
# Ocho a la vez, como precios_venta y visitas: ML aguanta más, pero el cupo es
# POR APLICACIÓN y lo comparten el sync, los webhooks y los crons.
_EN_PARALELO = 8
_TIMEOUT_S = 20.0
# Tanda = cada cuánto se guarda y se reporta avance.
_TANDA = 100
# Las mismas esperas que `competencia_ml._ESPERAS_429`, pero con asyncio.sleep.
_ESPERAS_429 = (0.5, 1.0, 2.0, 4.0, 8.0)
_TOPE_RETRY_AFTER_S = 30.0
_JITTER_S = 0.4
_TOP_PENDIENTES = 2
_ZONA = "America/Mexico_City"

ESTADO_MEDIDA = "medida"
ESTADO_NO_CALCULADA = "no_calculada"
SIN_MEDIR = "sin_medir"

# Experiencia de compra. Sin fila de experiencia = aún no se pregunta, y
# hacia fuera se dice SIN_MEDIR.
EXP_MEDIDA = "medida"
EXP_SIN_DATOS = "sin_datos"
_RUTA_EXPERIENCIA = "/reputation/items/{}/purchase_experience/integrators"
_PARAMS_EXPERIENCIA = {"locale": "es_MX"}   # sin locale, ML contesta 400
# El gris es «ML aún no tiene ventas para calificarla» (value -1).
_COLOR_SIN_DATOS = "gray"
# HEURÍSTICA sobre el texto de ML, no un campo: «lo calculamos a partir de tus
# ventas de productos en la misma categoría» (35 de 150 el 22-sep). Si ML
# cambia la redacción, `exp_por_categoria` sale false, no revienta.
_MARCA_POR_CATEGORIA = "misma categor"
# Los textos los escribe la IA de ML: se acotan para que un texto desbocado no
# infle la fila ni la respuesta de la lista.
_TOPE_TEXTO = 2000
_TOPE_LISTA = 20

_CLAVES_CONTEO = ("ok", "no_calculada", "limitada_429", "sin_token", "error",
                  "exp_ok", "exp_sin_datos", "exp_error", "exp_rechazada")
# 4xx DEFINITIVOS de la experiencia: ML contestó y dijo que no (los ítems de
# catálogo, una cuenta sin permiso para /reputation, una ruta que cambió).
# Reintentar en la hora siguiente no los cura, así que cuentan en
# `exp_rechazada`, que NO vuelve 'parcial' el barrido. 401 y 429 NO están: se
# resuelven en `_pedir` y, agotados, son exp_error.
_EXP_RECHAZOS = frozenset({400, 403, 404})
# Una fila cuya calidad YA es de hoy vuelve a entrar solo por la experiencia
# si su `capturado_en` tiene más de estas horas: si la experiencia falla
# siempre, el barrido la reintenta ~3-4 veces al día y no en las 13 vueltas.
_REINTENTO_EXP_H = 3
# Rechazos de la experiencia que, si son la mayoría, delatan un cambio en ML.
_RECHAZO_MASIVO_MIN = 10

Renovador = Callable[[str], Awaitable["str | None"]]

# Único 400 que ES respuesta de calidad («ML no la califica»). Todo otro 400 es
# un fallo de la petición: no se guarda ni se registra su `message`.
_PREFIJO_NO_CALCULADA = "Entity not calculated"


class CalidadNoDisponible(RuntimeError):
    """No se pudo LEER la calidad guardada (sin base, o sin la tabla o las
    columnas de la 0053).

    Distinta de «se leyó y no hay filas» (dict vacío): quien la recibe pinta
    «—» (None), nunca «sin medir»."""


# ═════════════════════════════════════════════════════════════════════════════
# Normalización (PURA)
# ═════════════════════════════════════════════════════════════════════════════

def _redondo(v: Any) -> int | None:
    """Número de ML → entero redondeado. Nada numérico → None (no 0)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
        # `1e400` y "Infinity" llegan del JSON como inf, y un entero de cientos
        # de dígitos no cabe en un float: nada de eso es un número de ML.
        return int(round(f)) if math.isfinite(f) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _fecha_iso(v: Any) -> str | None:
    """El `calculated_at` de ML tal cual, solo si es una fecha ISO legible.

    Se valida aquí porque va a una columna timestamptz dentro de un lote: una
    sola fecha ilegible tumbaría el upsert de la tanda entera.
    """
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return v.strip()


def _texto(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _completada(nodo: dict[str, Any]) -> bool:
    return str(nodo.get("status") or "").strip().upper() == "COMPLETED"


def _pendiente(var: dict[str, Any]) -> dict[str, Any]:
    """Una variable pendiente → {clave, titulo, accion, link}.

    La regla que habla es la primera NO completada (hoy cada variable trae una
    sola regla); si todas dicen completada, la primera.
    """
    reglas = [r for r in (var.get("rules") or []) if isinstance(r, dict)]
    regla = next((r for r in reglas if not _completada(r)),
                 reglas[0] if reglas else {})
    w = regla.get("wordings") if isinstance(regla.get("wordings"), dict) else {}
    return {
        "clave": _texto(var.get("key")),
        "titulo": _texto(w.get("title")) or _texto(var.get("title")),
        "accion": _texto(w.get("label")),
        "link": _texto(w.get("link")),
    }


def normalizar(crudo: dict) -> dict:
    """
    PURA. La respuesta 200 de `/item/{id}/performance` → lo que se guarda:

        {score, level, level_wording, calculated_at, user_product_id,
         buckets, n_pendientes}

    `score` es el entero redondeado, o None si ML no mandó un número entre 0 y
    100 (quien llama decide: `medir` lo trata como fallo, no como 0).
    `buckets` va en la forma normalizada de la 0053; `pendientes` son las
    variables con status != COMPLETED, en el orden en que las manda ML.
    """
    c = crudo if isinstance(crudo, dict) else {}
    score = _redondo(c.get("score"))
    if score is not None and not 0 <= score <= 100:
        score = None

    buckets: list[dict[str, Any]] = []
    n_pendientes = 0
    for b in c.get("buckets") or []:
        if not isinstance(b, dict):
            continue
        variables = [v for v in (b.get("variables") or []) if isinstance(v, dict)]
        pendientes = [_pendiente(v) for v in variables if not _completada(v)]
        n_pendientes += len(pendientes)
        buckets.append({
            "clave": _texto(b.get("key")),
            "tipo": _texto(b.get("type")),
            "titulo": _texto(b.get("title")),
            "score": _redondo(b.get("score")),
            "estado": _texto(b.get("status")),
            "n_variables": len(variables),
            "pendientes": pendientes,
        })

    return {
        "score": score,
        "level": _texto(c.get("level")),
        "level_wording": _texto(c.get("level_wording")),
        "calculated_at": _fecha_iso(c.get("calculated_at")),
        "user_product_id": _texto(c.get("entity_id")),
        "buckets": buckets,
        "n_pendientes": n_pendientes,
    }


def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _texto_ml(nodo: Any) -> str | None:
    """El `text` de un nodo `{text, order}` de ML; vacío → None, acotado."""
    t = _texto(_dict(nodo).get("text"))
    return t[:_TOPE_TEXTO] if t else None


def _textos_ml(lista: Any) -> list[str]:
    """`[{order, text}]` → los textos no vacíos, por `order` (y, a falta de
    él, en el orden en que llegan)."""
    nodos = [n for n in lista if isinstance(n, dict)] if isinstance(lista, list) else []

    def _orden(par: tuple[int, dict]) -> tuple[int | float, int]:
        # Sin float(): Python compara int con float de forma exacta, así que un
        # `order` de cientos de dígitos ordena bien en vez de desbordar. NaN
        # (sin orden posible) va al final, como el que no trae `order`.
        o = par[1].get("order")
        ok = (isinstance(o, (int, float)) and not isinstance(o, bool)
              and not (isinstance(o, float) and math.isnan(o)))
        return (o if ok else math.inf, par[0])

    textos = [_texto_ml(n) for _, n in sorted(enumerate(nodos), key=_orden)]
    return [t for t in textos if t][:_TOPE_LISTA]


def normalizar_experiencia(crudo: dict) -> dict:
    """
    PURA. La respuesta 200 de la experiencia de compra → las columnas `exp_*`:

        {exp_estado, exp_valor, exp_color, exp_texto, exp_consecuencia,
         exp_accion_principal, exp_razon, exp_recomendaciones, exp_ia,
         exp_por_categoria, exp_status_ml, exp_status_texto}

    · `reputation.color` gris o `value` < 0 → `exp_estado='sin_datos'` con
      `exp_valor` None: ML aún no tiene ventas para calificarla. Nunca 0.
    · color presente y `value` entre 0 y 100 → `'medida'`.
    · Cualquier otra cosa (sin reputation, sin color, value ilegible o > 100)
      → `exp_estado` None: quien llama (`medir`) lo cuenta como error de
      experiencia y no escribe ninguna `exp_*`.
    Los strings vacíos van como None. `exp_por_categoria` es una HEURÍSTICA
    sobre el texto de ML: algún texto de `reasoning` contiene «misma categor».
    `actions` no trae enlace y no se guarda (no se inventa una URL).
    """
    c = _dict(crudo)
    rep = _dict(c.get("reputation"))
    color = _texto(rep.get("color"))
    valor = _redondo(rep.get("value"))
    if (color or "").lower() == _COLOR_SIN_DATOS or (valor is not None and valor < 0):
        estado, valor = EXP_SIN_DATOS, None
    elif color and valor is not None and valor <= 100:
        estado = EXP_MEDIDA
    else:
        estado, valor = None, None

    razon = _textos_ml(_dict(c.get("reasoning")).get("subtitles"))
    ia = c.get("ai_generated")
    status = _dict(c.get("status"))
    return {
        "exp_estado": estado,
        "exp_valor": valor,
        "exp_color": color,
        "exp_texto": _texto(rep.get("text")),
        "exp_consecuencia": _texto_ml(_dict(c.get("consequence")).get("title")),
        "exp_accion_principal": _texto_ml(c.get("principal_actionable")),
        "exp_razon": razon,
        "exp_recomendaciones": _textos_ml(
            _dict(c.get("recommendations")).get("subtitles")),
        "exp_ia": bool(_texto_ml(ia)) if isinstance(ia, dict) else None,
        "exp_por_categoria": any(_MARCA_POR_CATEGORIA in t.lower() for t in razon),
        "exp_status_ml": _texto(status.get("id")),
        "exp_status_texto": _texto_ml(status),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Medición contra ML (async)
# ═════════════════════════════════════════════════════════════════════════════

def _nuevo_cliente() -> httpx.AsyncClient:
    """Fábrica del cliente HTTP. Existe para que las pruebas le pongan un
    transporte falso (`httpx.MockTransport`) sin tocar la firma de `medir`.

    SIGUE REDIRECCIONES, y no es opcional: la experiencia de un item contesta
    302 hacia la de su «user product» en el MISMO host (medido el 22-sep:
    `/reputation/items/MLM…/purchase_experience/integrators` → `/reputation/
    user_products/MLMU…/purchase_experience/integrators?locale=es_MX`). Sin
    esto las 535 experiencias del sandbox salieron «error». Es seguro con el
    token: httpx conserva `Authorization` solo si la redirección se queda en el
    mismo origen y la quita si cambia de host."""
    return httpx.AsyncClient(base_url=_API, timeout=_TIMEOUT_S, follow_redirects=True,
                             max_redirects=3)


def _espera_429(r: httpx.Response, intento: int) -> float:
    ra = (r.headers.get("Retry-After") or "").strip()
    if ra:
        try:
            return max(0.0, min(float(ra), _TOPE_RETRY_AFTER_S))
        except ValueError:
            pass  # Retry-After con fecha HTTP: se usa la escalera propia
    return _ESPERAS_429[intento] + random.uniform(0, _JITTER_S)


def _motivo_400(r: httpx.Response) -> str | None:
    """El `message` de un 400 SOLO si es de la lista blanca («Entity not
    calculated…»); cualquier otro 400 → None (error, sin fila). Nunca se usa
    `r.text`: un 400 ajeno puede traer el token en el cuerpo."""
    try:
        cuerpo = r.json()
    except ValueError:
        return None
    if not isinstance(cuerpo, dict):
        return None
    msg = cuerpo.get("message")
    if not isinstance(msg, str):
        return None
    msg = msg.strip()
    if not msg.startswith(_PREFIJO_NO_CALCULADA):
        return None
    return msg[:500]


def _fila_medida(item_id: str, cuenta: str, datos: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": item_id, "cuenta": cuenta, "estado": ESTADO_MEDIDA,
        "score": datos["score"], "level": datos["level"],
        "level_wording": datos["level_wording"],
        "calculated_at": datos["calculated_at"],
        "user_product_id": datos["user_product_id"],
        "buckets": datos["buckets"], "n_pendientes": datos["n_pendientes"],
        "motivo": None,
    }


def _fila_no_calculada(item_id: str, cuenta: str, motivo: str) -> dict[str, Any]:
    return {
        "item_id": item_id, "cuenta": cuenta, "estado": ESTADO_NO_CALCULADA,
        "score": None, "level": None, "level_wording": None,
        "calculated_at": None, "user_product_id": None,
        "buckets": [], "n_pendientes": 0, "motivo": motivo,
    }


async def medir(pares: list[tuple[str, str]], tokens: dict[str, str],
                renovar: Renovador | None = None,
                renovados: set[str] | None = None
                ) -> tuple[list[dict], dict]:
    """
    Pregunta la calidad —y, si sale, la experiencia de compra— de cada
    `(item_id, cuenta)` y devuelve `(filas para guardar, conteo)`, con conteo =
    {ok, no_calculada, limitada_429, sin_token, error, exp_ok, exp_sin_datos,
    exp_error, exp_rechazada}. Las cinco primeras son de la CALIDAD (una por
    publicación); las `exp_*`, de la experiencia (solo de las que tuvieron
    fila).

    CALIDAD (`/item/{id}/performance`), la que decide si hay fila:
    · 200 → fila `medida` (conteo ok). Un 200 sin score legible cuenta como
      error y NO escribe fila.
    · 400 «Entity not calculated…» → fila `no_calculada` con ese `message` en
      `motivo`. Cualquier otro 400 → error, sin fila, y al log solo el código.
    · Lo demás (red, 403, 404, 5xx, cuerpo que no es JSON) → error, sin fila.

    EXPERIENCIA (`/reputation/items/{id}/purchase_experience/integrators`),
    SOLO si la calidad dejó fila (si no, su respuesta se tiraría igual):
    · 200 con `reputation` legible → las `exp_*` de `normalizar_experiencia`
      se suman a la fila (exp_ok, o exp_sin_datos si ML no tiene ventas para
      calificarla).
    · 400, 403 o 404 (ML dijo que no, y en una hora dirá lo mismo) →
      exp_rechazada, que NO vuelve 'parcial' el barrido.
    · Cualquier otra cosa (5xx, sin reputation, 401 sin remedio, 429
      agotado, red) → exp_error.
      En los dos casos la fila sale SIN llaves `exp_*` y `guardar` conserva
      las que ya había. Al log solo el código, nunca el cuerpo.

    Una excepción inesperada al procesar UNA publicación (una respuesta rara
    que la normalización no previó) cuesta ESA publicación —error si fue en
    la calidad, exp_error si fue en la experiencia— y no el barrido: `_una`
    la atrapa, y el `gather` además usa return_exceptions para que ninguna
    tarea quede viva contra el cliente ya cerrado.

    Para las DOS APIs, el mismo semáforo (a lo más `_EN_PARALELO` peticiones
    en vuelo) y el mismo manejo de 401 y 429:
    · 401 → si hay `renovar` (async cuenta → token|None) se renueva UNA vez por
      petición y se reintenta; varias peticiones de la misma cuenta que chocan
      con el 401 a la vez comparten UNA renovación. Si `renovar` es None no se
      renueva jamás. Sin token válido: sin_token (o exp_error) y sin fila (o
      sin `exp_*`). La cuenta queda MUERTA —y su token sale de `tokens`, así
      que las tandas siguientes del barrido contestan sin_token sin
      preguntar— si la renovación falla o devuelve el mismo token, o si el
      token renovado vuelve a dar 401, DE CUALQUIERA DE LAS DOS APIs. Un token
      que ya salió de `renovar` (conjunto `renovados`, que el barrido comparte
      entre tandas) no se renueva otra vez: renovar corre como mucho una vez
      por cuenta y por barrido.
    · 429 → espera `Retry-After` (tope 30 s) o 0.5/1/2/4/8 s + jitter, con
      `asyncio.sleep`; agotado: limitada_429 (o exp_error).

    Una cuenta sin token en `tokens` cuenta sin_token y ni siquiera se pregunta.
    `tokens` (y `renovados`) se actualizan EN SITIO, para que la siguiente
    tanda del mismo barrido nazca con el token bueno o sepa que no hay.
    """
    conteo = dict.fromkeys(_CLAVES_CONTEO, 0)
    vistos: set[str] = set()
    unicos: list[tuple[str, str]] = []
    for item_id, cuenta in pares or []:
        iid = str(item_id or "").strip()
        if iid and iid not in vistos:
            vistos.add(iid)
            unicos.append((iid, str(cuenta or "").strip()))
    if not unicos:
        return [], conteo

    sem = asyncio.Semaphore(_EN_PARALELO)
    candados: dict[str, asyncio.Lock] = {}
    muertas: set[str] = set()   # cuentas cuyo token ya no sirve en esta corrida
    if renovados is None:
        renovados = set()       # tokens que ya salieron de `renovar`

    def _matar(cuenta: str) -> None:
        # Muerta para TODO el barrido: sin token en `tokens` (compartido entre
        # tandas), `_pedir` contesta sin_token sin pedir ni renovar.
        muertas.add(cuenta)
        tokens.pop(cuenta, None)

    async def _token_nuevo(cuenta: str, usado: str) -> str | None:
        async with candados.setdefault(cuenta, asyncio.Lock()):
            actual = tokens.get(cuenta)
            if actual and actual != usado:
                return actual               # otra petición ya renovó
            if renovar is None or cuenta in muertas or usado in renovados:
                # Sin renovador, ya muerta, o el token que falló YA salió de
                # renovar en este barrido: no se vuelve a renovar.
                _matar(cuenta)
                return None
            try:
                nuevo = await renovar(cuenta)
            except Exception as exc:  # noqa: BLE001
                log.warning("calidad ML: no se pudo renovar el token de %s: %s",
                            cuenta, type(exc).__name__)
                nuevo = None
            if not nuevo or nuevo == usado:
                _matar(cuenta)
                return None
            renovados.add(nuevo)
            tokens[cuenta] = nuevo
            return nuevo

    async def _pedir(cli: httpx.AsyncClient, ruta: str,
                     params: dict[str, str] | None, item_id: str, cuenta: str
                     ) -> tuple[str, httpx.Response | None]:
        """Una petición con el token, el 401 y el 429 resueltos. Devuelve
        ("respuesta", r) con cualquier código que no sea 401/429 —quien llama
        lo interpreta— o (sin_token | limitada_429 | error, None)."""
        if cuenta in muertas or not tokens.get(cuenta):
            return "sin_token", None
        async with sem:
            token = tokens.get(cuenta)
            if cuenta in muertas or not token:
                return "sin_token", None
            renovado = False
            intento_429 = 0
            while True:
                try:
                    r = await cli.get(ruta, params=params,
                                      headers={"Authorization": f"Bearer {token}"})
                except httpx.HTTPError as exc:
                    log.debug("calidad ML %s: %s", item_id, type(exc).__name__)
                    return "error", None
                code = r.status_code
                if code == 401:
                    if renovado:
                        # El token recién renovado tampoco sirve: la cuenta
                        # muere para el resto del barrido.
                        _matar(cuenta)
                        return "sin_token", None
                    renovado = True
                    nuevo = await _token_nuevo(cuenta, token)
                    if not nuevo:
                        return "sin_token", None
                    token = nuevo
                    continue
                if code == 429:
                    if intento_429 >= len(_ESPERAS_429):
                        return "limitada_429", None
                    espera = _espera_429(r, intento_429)
                    intento_429 += 1
                    await asyncio.sleep(espera)
                    continue
                return "respuesta", r

    def _calidad(r: httpx.Response, item_id: str, cuenta: str
                 ) -> tuple[str, dict[str, Any] | None]:
        code = r.status_code
        if code == 400:
            motivo = _motivo_400(r)
            if motivo is not None:
                return "no_calculada", _fila_no_calculada(item_id, cuenta, motivo)
            # 400 ajeno a la calidad: al log solo el código.
            log.debug("calidad ML %s: HTTP 400 no reconocido", item_id)
            return "error", None
        if code != 200:
            log.debug("calidad ML %s: HTTP %s", item_id, code)
            return "error", None
        try:
            cuerpo = r.json()
        except ValueError:
            return "error", None
        datos = normalizar(cuerpo)
        if datos["score"] is None:
            log.warning("calidad ML %s: 200 sin score legible (%r)",
                        item_id, (cuerpo or {}).get("score")
                        if isinstance(cuerpo, dict) else None)
            return "error", None
        return "ok", _fila_medida(item_id, cuenta, datos)

    def _experiencia(r: httpx.Response, item_id: str
                     ) -> tuple[str, dict[str, Any] | None]:
        if r.status_code != 200:
            # Al log solo el código (un 400 puede traer el token en el
            # cuerpo). 400/403/404: ML dijo que no (definitivo); el resto
            # (5xx…) es «no pude preguntar».
            log.debug("experiencia ML %s: HTTP %s", item_id, r.status_code)
            return ("exp_rechazada" if r.status_code in _EXP_RECHAZOS
                    else "exp_error"), None
        try:
            cuerpo = r.json()
        except ValueError:
            return "exp_error", None
        if not isinstance(cuerpo, dict) or not isinstance(cuerpo.get("reputation"), dict):
            log.warning("experiencia ML %s: 200 sin reputation", item_id)
            return "exp_error", None
        datos = normalizar_experiencia(cuerpo)
        if datos["exp_estado"] is None:
            log.warning("experiencia ML %s: reputation ilegible (color %r, value %r)",
                        item_id, cuerpo["reputation"].get("color"),
                        cuerpo["reputation"].get("value"))
            return "exp_error", None
        return ("exp_sin_datos" if datos["exp_estado"] == EXP_SIN_DATOS
                else "exp_ok"), datos

    async def _una(cli: httpx.AsyncClient, item_id: str, cuenta: str
                   ) -> tuple[str, str | None, dict[str, Any] | None]:
        # Una excepción inesperada (p. ej. un número que la normalización no
        # previó) cuesta ESTA publicación, no el barrido. Al log solo el tipo:
        # el mensaje puede traer pedazos de la respuesta.
        try:
            clase, r = await _pedir(cli, f"/item/{item_id}/performance", None,
                                    item_id, cuenta)
            if r is None:
                return clase, None, None
            clase, fila = _calidad(r, item_id, cuenta)
        except Exception as exc:  # noqa: BLE001
            log.warning("calidad ML %s: excepción inesperada (%s)",
                        item_id, type(exc).__name__)
            return "error", None, None
        if fila is None:
            # Sin fila de calidad no hay fila: la experiencia no se pregunta.
            return clase, None, None
        try:
            clase_e, r_e = await _pedir(cli, _RUTA_EXPERIENCIA.format(item_id),
                                        _PARAMS_EXPERIENCIA, item_id, cuenta)
            if r_e is None:
                log.debug("experiencia ML %s: %s", item_id, clase_e)
                return clase, "exp_error", fila
            clase_e, datos = _experiencia(r_e, item_id)
        except Exception as exc:  # noqa: BLE001
            # La calidad sí salió: su fila va, sin `exp_*`.
            log.warning("experiencia ML %s: excepción inesperada (%s)",
                        item_id, type(exc).__name__)
            return clase, "exp_error", fila
        if datos is not None:
            fila.update(datos)
        return clase, clase_e, fila

    async with _nuevo_cliente() as cli:
        # return_exceptions: el gather espera a TODAS antes de cerrar el
        # cliente; ninguna queda huérfana pidiendo contra un cliente cerrado.
        resultados = await asyncio.gather(*[_una(cli, i, c) for i, c in unicos],
                                          return_exceptions=True)

    filas: list[dict] = []
    for (item_id, _c), res in zip(unicos, resultados):
        if isinstance(res, BaseException):
            # `_una` ya atrapa Exception: esto es solo la red de seguridad.
            log.warning("calidad ML %s: la tarea tronó (%s)",
                        item_id, type(res).__name__)
            conteo["error"] += 1
            continue
        clase, clase_e, fila = res
        conteo[clase] += 1
        if clase_e is not None:
            conteo[clase_e] += 1
        if fila is not None:
            filas.append(fila)
    return filas, conteo


# ═════════════════════════════════════════════════════════════════════════════
# Base de datos (TODO BLOQUEANTE → asyncio.to_thread desde corrutinas)
# ═════════════════════════════════════════════════════════════════════════════
#
# FORMATO LARGO (0053). `medir` sigue devolviendo UNA fila ANCHA por
# publicación —la calidad más las `exp_*`—, que es como contesta ML y como la
# prueban las pruebas de medición. Al guardar, `_filas_salud` la parte en UNA
# fila por métrica de `enrich.listing_health`; al leer, `_fila_ancha` la vuelve
# a juntar. Así la lista, el mosaico, el cajón y los filtros reciben EXACTAMENTE
# las formas de antes de la 0053 y ninguno se enteró del cambio de tabla.

_CANAL = "mercado_libre"
METRICA_CALIDAD = "calidad"
METRICA_EXPERIENCIA = "experiencia"

# `nivel` va NORMALIZADO para cruzar canales; la etiqueta de ML viaja intacta
# en `nivel_canal` y la clave cruda (`level`, `color`) en `detalle`, que es lo
# que filtran y cuentan la pantalla y los botones. Un level o un color que ML
# no haya mandado nunca queda con `nivel` NULL: no se adivina.
_NIVEL_CALIDAD = {"good": "bueno", "medium": "medio", "bad": "malo"}
_NIVEL_EXPERIENCIA = {"green": "bueno", "orange": "medio", "yellow": "medio",
                      "red": "malo"}

# Las llaves de la experiencia en la fila ancha (`normalizar_experiencia`).
_COLS_EXP = ("exp_estado", "exp_valor", "exp_color", "exp_texto", "exp_consecuencia",
             "exp_accion_principal", "exp_razon", "exp_recomendaciones", "exp_ia",
             "exp_por_categoria", "exp_status_ml", "exp_status_texto")
# Lo que de la experiencia va en `detalle` (las `exp_*` sin columna propia, sin
# el prefijo). SIEMPRE las nueve, aunque valgan null: la huella de la historia
# sale del jsonb, y una llave que aparece y desaparece la cambiaría sin que ML
# cambiara nada. Misma forma que la copia de la 0053 en el sandbox.
_DETALLE_EXP = ("color", "consecuencia", "accion_principal", "razon",
                "recomendaciones", "ia", "por_categoria", "status_ml",
                "status_texto")
_LISTAS_EXP = ("razon", "recomendaciones")
# Un pendiente de calidad tal como lo arma `_pendiente` (y como lo esperan
# `pendientes_top` y la tarjeta del cajón).
_LLAVES_PENDIENTE = ("clave", "titulo", "accion", "link")

_HOY_MX = f"(now() at time zone '{_ZONA}')::date"


def _con_experiencia(f: dict) -> bool:
    """La fila trae experiencia si `medir` le sumó un `exp_estado` válido."""
    return f.get("exp_estado") in (EXP_MEDIDA, EXP_SIN_DATOS)


def _sin_nulos(d: dict[str, Any]) -> dict[str, Any]:
    """Como `jsonb_strip_nulls`: un campo que ML no da no se guarda."""
    return {k: v for k, v in d.items() if v is not None}


def _salud_calidad(f: dict) -> dict[str, Any]:
    """PURA. La calidad de una fila ancha → su fila de `listing_health`.

    medida: valor = score, nivel normalizado, nivel_canal = level_wording,
    medido_en = calculated_at; los pendientes de cada bucket van UNA sola vez
    en `pendientes`, en el orden de ML, con `grupo` = clave del bucket, y el
    bucket sin ellos en `detalle.grupos`.
    no_calculada: sin número, sin nivel y sin pendientes (n_pendientes NULL:
    ML no la calificó, no es que no pida nada); el message de ML en `motivo`.
    """
    if f.get("estado") != ESTADO_MEDIDA:
        return {"metrica": METRICA_CALIDAD, "estado": f.get("estado"),
                "valor": None, "nivel": None, "nivel_canal": None,
                "n_pendientes": None, "pendientes": [], "detalle": {},
                "motivo": f.get("motivo"), "medido_en": None}
    buckets = [b for b in (f.get("buckets") or []) if isinstance(b, dict)]
    level = f.get("level")
    return {
        "metrica": METRICA_CALIDAD, "estado": ESTADO_MEDIDA,
        "valor": f.get("score"), "nivel": _NIVEL_CALIDAD.get(level),
        "nivel_canal": f.get("level_wording"),
        "n_pendientes": int(f.get("n_pendientes") or 0),
        "pendientes": [_sin_nulos({**p, "grupo": b.get("clave")})
                       for b in buckets for p in (b.get("pendientes") or [])
                       if isinstance(p, dict)],
        "detalle": {"level": level, "level_wording": f.get("level_wording"),
                    "user_product_id": f.get("user_product_id"),
                    "grupos": [{k: v for k, v in b.items() if k != "pendientes"}
                               for b in buckets]},
        "motivo": None, "medido_en": f.get("calculated_at"),
    }


def _salud_experiencia(f: dict) -> dict[str, Any] | None:
    """PURA. La experiencia de una fila ancha → su fila de `listing_health`, o
    None si esta vuelta no la pudo leer (no se toca la de antes).

    medida: valor = reputation.value y nivel por color. sin_datos (gris): valor
    y nivel NULL, NUNCA 0. La experiencia no tiene pendientes: [] y
    n_pendientes NULL."""
    if not _con_experiencia(f):
        return None
    medida = f["exp_estado"] == EXP_MEDIDA
    detalle = {k: f.get(f"exp_{k}") for k in _DETALLE_EXP}
    for k in _LISTAS_EXP:
        detalle[k] = list(detalle[k] or [])
    return {"metrica": METRICA_EXPERIENCIA, "estado": f["exp_estado"],
            "valor": f.get("exp_valor") if medida else None,
            "nivel": _NIVEL_EXPERIENCIA.get(f.get("exp_color")) if medida else None,
            "nivel_canal": f.get("exp_texto"), "n_pendientes": None,
            "pendientes": [], "detalle": detalle, "motivo": None,
            "medido_en": None}


def _filas_salud(f: dict) -> list[dict[str, Any]]:
    """PURA. Una fila ancha de `medir` → sus filas de `listing_health`: la
    calidad siempre; la experiencia solo si esta vuelta la leyó."""
    filas = [_salud_calidad(f)]
    exp = _salud_experiencia(f)
    if exp is not None:
        filas.append(exp)
    return filas


# Las columnas que manda el escritor, en el orden de la plantilla. Los casts
# van en CADA valor: en un VALUES el tipo lo fija la primera fila, y un NULL
# ahí (una no_calculada primero) volvería texto la columna entera.
_COLS_SALUD = ("canal", "account_id", "listing_id", "metrica", "estado", "valor",
               "nivel", "nivel_canal", "n_pendientes", "pendientes", "detalle",
               "motivo", "medido_en")
_PLANTILLA = ("(%s::text, %s::uuid, %s::text, %s::text, %s::text, %s::numeric, "
              "%s::text, %s::text, %s::smallint, %s::jsonb, %s::jsonb, %s::text, "
              "%s::timestamptz)")
_PAGINA = 200
_COLS_HIST = ("canal, account_id, listing_id, metrica, dia, estado, valor, nivel, "
              "nivel_canal, n_pendientes, huella, pendientes, detalle, capturado_en")

# UNA sentencia por página del lote: el upsert de lo último y, sobre lo que ESE
# upsert devolvió (lo que de verdad quedó en la tabla), el upsert del día en la
# historia. La huella del día guardado ANTERIOR (`dia < hoy`, nunca la de hoy:
# una segunda medición del mismo día se compararía consigo misma y tiraría el
# jsonb del día en que cambió) se lee en la misma sentencia; si es igual, la
# historia guarda los escalares y deja `pendientes`/`detalle` en NULL.
_SQL_GUARDAR = f"""
with v ({", ".join(_COLS_SALUD)}) as (values %s),
actual as (
    insert into {TABLA} as h ({", ".join(_COLS_SALUD)}, capturado_en)
    select {", ".join(_COLS_SALUD)}, now() from v
    on conflict (canal, account_id, listing_id, metrica) do update set
        estado       = excluded.estado,
        valor        = excluded.valor,
        nivel        = excluded.nivel,
        nivel_canal  = excluded.nivel_canal,
        n_pendientes = excluded.n_pendientes,
        pendientes   = excluded.pendientes,
        detalle      = excluded.detalle,
        motivo       = excluded.motivo,
        medido_en    = excluded.medido_en,
        capturado_en = excluded.capturado_en
    returning h.canal, h.account_id, h.listing_id, h.metrica, h.estado, h.valor,
              h.nivel, h.nivel_canal, h.n_pendientes, h.pendientes, h.detalle,
              h.capturado_en, md5(h.pendientes::text || h.detalle::text) as huella
)
insert into {TABLA_HIST} as hh ({_COLS_HIST})
select a.canal, a.account_id, a.listing_id, a.metrica, {_HOY_MX}, a.estado,
       a.valor, a.nivel, a.nivel_canal, a.n_pendientes, a.huella,
       case when ant.huella is distinct from a.huella then a.pendientes end,
       case when ant.huella is distinct from a.huella then a.detalle end,
       a.capturado_en
  from actual a
  left join lateral (
        select p.huella
          from {TABLA_HIST} p
         where p.canal = a.canal and p.account_id = a.account_id
           and p.listing_id = a.listing_id and p.metrica = a.metrica
           and p.dia < {_HOY_MX}
         order by p.dia desc
         limit 1) ant on true
on conflict (canal, account_id, listing_id, metrica, dia) do update set
    estado       = excluded.estado,
    valor        = excluded.valor,
    nivel        = excluded.nivel,
    nivel_canal  = excluded.nivel_canal,
    n_pendientes = excluded.n_pendientes,
    huella       = excluded.huella,
    pendientes   = excluded.pendientes,
    detalle      = excluded.detalle,
    capturado_en = excluded.capturado_en
"""


def ids_de_cuentas() -> dict[str, str]:
    """BLOQUEANTE. {legacy_code: account_id} de las cuentas de ML.

    `listing_health` lleva el uuid de `core.accounts` (convención de
    channel.listings) y `medir` trae el código (BEKURA, SANCORFASHION). UNA
    lectura por barrido: `_refrescar` la hace una vez y se la pasa a cada
    `guardar`."""
    filas = sdb.fetch_all(
        "select legacy_code, id::text as id from core.accounts "
        "where channel_id = %(canal)s and legacy_code is not null",
        {"canal": _CANAL})
    return {str(f["legacy_code"]).strip().upper(): str(f["id"]) for f in filas}


def _unicas(filas: Iterable[dict]) -> dict[tuple[str, str], dict]:
    """Las filas que se pueden guardar, sin llaves repetidas (gana la última):
    con dos filas de la misma llave en un solo `insert … on conflict do
    update`, Postgres revienta con «cannot affect row a second time».

    La llave es (cuenta, publicación): el mismo listing_id en otra cuenta es
    OTRA fila (la PK de la 0053 lleva account_id), no una que se pisa."""
    unicas: dict[tuple[str, str], dict] = {}
    for f in filas or []:
        iid = str(f.get("item_id") or "").strip()
        cuenta = _cuenta(f.get("cuenta"))
        if iid and cuenta and f.get("estado") in (ESTADO_MEDIDA, ESTADO_NO_CALCULADA):
            unicas[(cuenta, iid)] = f
    return unicas


def _valores_salud(unicas: dict[tuple[str, str], dict],
                   cuentas: dict[str, str]) -> tuple[list[tuple], int]:
    """PURA. Las tuplas del lote (en el orden de `_COLS_SALUD`) y cuántas
    publicaciones cubren. Una cuenta sin id en `core.accounts` no se guarda
    (se avisa): sin fila, el barrido siguiente la vuelve a medir."""
    valores: list[tuple] = []
    publicaciones = 0
    faltan: set[str] = set()
    for (cuenta, iid), f in unicas.items():
        account_id = cuentas.get(cuenta)
        if not account_id:
            faltan.add(cuenta)
            continue
        publicaciones += 1
        for m in _filas_salud(f):
            valores.append((
                _CANAL, account_id, iid, m["metrica"], m["estado"], m["valor"],
                m["nivel"], m["nivel_canal"], m["n_pendientes"],
                json.dumps(m["pendientes"], ensure_ascii=False),
                json.dumps(m["detalle"], ensure_ascii=False),
                m["motivo"], m["medido_en"]))
    if faltan:
        log.warning("calidad ML: sin id en core.accounts para %s; esas "
                    "publicaciones no se guardaron", ", ".join(sorted(faltan)))
    return valores, publicaciones


def _escribir_lote(cur: Any, valores: list[tuple]) -> None:
    """El lote en el cursor que le den, dentro de la transacción de quien
    llama. Aparte de `guardar` para que la prueba contra el sandbox corra el
    MISMO SQL dentro de un rollback."""
    from psycopg2.extras import execute_values

    cur.execute("select set_config('app.via', 'calidad_ml', true)")
    execute_values(cur, _SQL_GUARDAR, valores, template=_PLANTILLA,
                   page_size=_PAGINA)


def guardar(filas: list[dict], cuentas: dict[str, str] | None = None) -> int:
    """BLOQUEANTE. Guarda un lote de filas de `medir` en UNA transacción y
    devuelve cuántas publicaciones escribió.

    Por publicación: upsert de la calidad —y de la experiencia, si esta vuelta
    la leyó— en `listing_health`, y upsert del día de México en
    `listing_health_hist` (si ya había fila de hoy, se reemplaza). Una fila sin
    experiencia NO toca la experiencia guardada ni escribe su historia: «no pude
    preguntar» no borra la medición de ayer.

    `cuentas` = `ids_de_cuentas()`; el barrido la pasa para leerla una sola
    vez. Sin ella (el sembrador, un lote suelto) se lee aquí, y solo si hay algo
    que guardar.
    """
    unicas = _unicas(filas)
    if not unicas:
        return 0
    valores, publicaciones = _valores_salud(
        unicas, cuentas if cuentas is not None else ids_de_cuentas())
    if not valores:
        return 0

    def _escribir() -> None:
        with sdb.get_cursor() as cur:
            _escribir_lote(cur, valores)

    sdb.reintentar_transitorio(_escribir)
    return publicaciones


def _activas_ml(alias: str = "l") -> tuple[str, dict]:
    """El criterio de «activa» de ML sale de publicaciones_panel, no de aquí.

    Import perezoso: publicaciones_panel también importa este módulo (la
    tarjeta del cajón) y así ninguno de los dos depende del orden de carga.
    """
    from services import publicaciones_panel as pp
    activas = pp.filtro_sql_activas("mercado_libre", alias=alias)
    if activas is None:  # pragma: no cover — ML sí decide por `situacion`
        raise RuntimeError("publicaciones_panel no sabe qué es una activa de ML")
    return activas


def _cuenta(cuenta: str | None) -> str | None:
    c = (cuenta or "").strip().upper()
    return c or None


def _join_salud(corr: str, alias: str, metrica: str) -> str:
    """La condición que ata una fila de `listing_health` (alias `corr`) a una de
    channel.listings (alias `alias`) para una métrica: la PK completa, así el
    mismo listing_id en otra cuenta o en otro canal nunca se cruza."""
    return (f"{corr}.canal = {alias}.canal and {corr}.account_id = {alias}.account_id "
            f"and {corr}.listing_id = {alias}.listing_id "
            f"and {corr}.metrica = '{metrica}'")


def objetivo(limite: int | None, cuenta: str | None = None,
             forzar: bool = False) -> list[dict]:
    """
    BLOQUEANTE. Las publicaciones ML ACTIVAS que NO tienen medición COMPLETA de
    HOY (día de México): entra toda la que no tenga fila de calidad, o cuya
    calidad no sea de hoy, o cuya experiencia no sea de hoy (o no exista: la
    experiencia falló o nunca se preguntó) SI ADEMÁS su calidad tiene más de
    `_REINTENTO_EXP_H` horas: si la experiencia falla siempre, se reintenta
    unas 3-4 veces al día y no en cada vuelta horaria (cada reintento vuelve a
    pedir las DOS APIs, y el cupo es compartido). Una por (listing_id,
    cuenta); las nunca medidas primero, luego la calidad más vieja y, a igual
    calidad, la experiencia más vieja.
    `limite` None o <= 0 = sin tope (`LIMIT NULL`). `forzar` = todas las
    activas, aunque tengan captura de hoy (solo el sembrador del sandbox lo
    usa; el job nunca).

    Devuelve [{"item_id", "cuenta"}].
    """
    donde, params = _activas_ml("l")
    tope = limite if (limite is not None and int(limite) > 0) else None
    sql = f"""
    select l.listing_id  as item_id,
           a.legacy_code as cuenta
      from channel.listings l
      join core.accounts a on a.id = l.account_id
      left join {TABLA} c on {_join_salud('c', 'l', METRICA_CALIDAD)}
      left join {TABLA} e on {_join_salud('e', 'l', METRICA_EXPERIENCIA)}
     where l.canal = '{_CANAL}'
       and nullif(l.listing_id, '') is not null
       and a.legacy_code is not null
       and {donde}
       and (%(cuenta)s::text is null or a.legacy_code = %(cuenta)s)
       and (%(forzar)s or c.listing_id is null
            or (c.capturado_en at time zone '{_ZONA}')::date
               <> (now() at time zone '{_ZONA}')::date
            or ((e.capturado_en is null
                 or (e.capturado_en at time zone '{_ZONA}')::date
                    <> (now() at time zone '{_ZONA}')::date)
                and c.capturado_en < now() - interval '{int(_REINTENTO_EXP_H)} hours'))
     group by 1, 2
     order by max(c.capturado_en) asc nulls first,
              max(e.capturado_en) asc nulls first, 1
     limit %(limite)s
    """
    filas = sdb.fetch_all(sql, {**params, "cuenta": _cuenta(cuenta), "limite": tope,
                                "forzar": bool(forzar)})
    return [{"item_id": str(f["item_id"]), "cuenta": str(f["cuenta"])} for f in filas]


# ── Lecturas para la lista y la tarjeta ──────────────────────────────────────

_AVISADO_SIN_TABLA = False


def _sin_tabla(exc: Exception) -> bool:
    """La tabla o sus columnas no existen, sin importar psycopg2 aquí:
    42P01 (UndefinedTable: la 0053 sin aplicar) o 42703 (UndefinedColumn: una
    tabla que no es la de la 0053). Las dos son «no pude leer», no «sin
    medir»."""
    return getattr(exc, "pgcode", None) in ("42P01", "42703")


def _avisar_sin_tabla() -> None:
    global _AVISADO_SIN_TABLA
    if not _AVISADO_SIN_TABLA:
        _AVISADO_SIN_TABLA = True
        log.warning("%s no existe o le faltan columnas (migración 0053 sin "
                    "aplicar): la calidad de ML se muestra como no disponible",
                    TABLA)


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _como_lista(v: Any) -> list:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return v if isinstance(v, list) else []


def _como_dict(v: Any) -> dict:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def _numero(v: Any) -> int | float | None:
    """`valor` es numeric(5,2) (psycopg2 lo da como Decimal): el entero de
    siempre cuando no tiene decimales, float si los tiene, None si no hay."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return int(f) if f.is_integer() else f


def _buckets(grupos: Any, pendientes: Any) -> list[dict]:
    """`detalle.grupos` + `pendientes` → los buckets de siempre, cada uno con
    SUS pendientes ({clave, titulo, accion, link}, en el orden de ML) según
    `grupo`. Un campo que no se guardó (ML no lo dio) vuelve como None."""
    por_grupo: dict[Any, list[dict]] = {}
    for p in _como_lista(pendientes):
        if isinstance(p, dict):
            por_grupo.setdefault(p.get("grupo"), []).append(
                {k: p.get(k) for k in _LLAVES_PENDIENTE})
    out: list[dict] = []
    for g in _como_lista(grupos):
        if isinstance(g, dict):
            # `pop`: si dos buckets compartieran clave, los pendientes van una
            # sola vez (al primero), como se guardaron.
            out.append({**g, "pendientes": por_grupo.pop(g.get("clave"), [])})
    return out


def _fila_ancha(cal: dict[str, Any], exp: dict[str, Any] | None) -> dict[str, Any]:
    """Las filas de calidad y experiencia de UNA publicación → la fila ancha
    que arman `_armar_calidad` y `_armar_experiencia` (la forma de antes de la
    0053). Sin fila de experiencia, las `exp_*` no van: `sin_medir`."""
    d = _como_dict(cal.get("detalle"))
    f: dict[str, Any] = {
        "estado": cal.get("estado"),
        "score": _numero(cal.get("valor")),
        "level": d.get("level"),
        "level_wording": cal.get("nivel_canal"),
        "n_pendientes": cal.get("n_pendientes"),
        "buckets": _buckets(d.get("grupos"), cal.get("pendientes")),
        "capturado_en": cal.get("capturado_en"),
        "calculated_at": cal.get("medido_en"),
        "motivo": cal.get("motivo"),
        "user_product_id": d.get("user_product_id"),
    }
    if exp is not None:
        de = _como_dict(exp.get("detalle"))
        f.update({f"exp_{k}": de.get(k) for k in _DETALLE_EXP})
        f.update({"exp_estado": exp.get("estado"),
                  "exp_valor": _numero(exp.get("valor")),
                  "exp_texto": exp.get("nivel_canal"),
                  "exp_capturado_en": exp.get("capturado_en")})
    return f


def _pendientes_top(buckets: list, n: int = _TOP_PENDIENTES) -> list[dict]:
    """Hasta `n` pendientes, del bucket de MENOR score primero (lo más flojo
    es lo que más sube la calidad), y dentro de cada bucket en el orden de ML."""
    validos = [b for b in buckets if isinstance(b, dict)]
    orden = sorted(
        enumerate(validos),
        key=lambda ib: (not isinstance(ib[1].get("score"), (int, float)),
                        ib[1].get("score") if isinstance(ib[1].get("score"),
                                                         (int, float)) else 0,
                        ib[0]))
    top: list[dict] = []
    for _, b in orden:
        for p in b.get("pendientes") or []:
            if not isinstance(p, dict):
                continue
            top.append({"titulo": p.get("titulo"), "accion": p.get("accion"),
                        "link": p.get("link")})
            if len(top) >= n:
                return top
    return top


def _armar_calidad(f: dict[str, Any], detalle: bool) -> dict[str, Any]:
    buckets = _como_lista(f.get("buckets"))
    out: dict[str, Any] = {
        "estado": f.get("estado"),
        "score": f.get("score"),
        "level": f.get("level"),
        "level_wording": f.get("level_wording"),
        "n_pendientes": int(f.get("n_pendientes") or 0),
        "pendientes_top": _pendientes_top(buckets),
        "capturado_en": _iso(f.get("capturado_en")),
        "calculated_at": _iso(f.get("calculated_at")),
        # También en el resumen: sin él, la lista y el mosaico no pueden decir
        # POR QUÉ ML no califica una publicación.
        "motivo": f.get("motivo"),
    }
    if detalle:
        out["buckets"] = buckets
        out["user_product_id"] = f.get("user_product_id")
    return out


def _textos_guardados(v: Any) -> list[str]:
    return [t for t in _como_lista(v) if isinstance(t, str) and t.strip()]


def _armar_experiencia(f: dict[str, Any], detalle: bool) -> dict[str, Any]:
    """Las `exp_*` de la fila ancha → la experiencia hacia fuera.

    Sin experiencia guardada (aún no se pregunta) → estado `sin_medir` y TODO
    en None/[]: nada que se pinte como un 0. En `sin_datos` el valor es None
    aunque la base trajera otra cosa."""
    est = f.get("exp_estado")
    medida = est == EXP_MEDIDA
    conocida = est in (EXP_MEDIDA, EXP_SIN_DATOS)

    def _si(clave: str) -> Any:
        return f.get(clave) if conocida else None

    out: dict[str, Any] = {
        "estado": est if conocida else SIN_MEDIR,
        "valor": f.get("exp_valor") if medida else None,
        "color": _si("exp_color"),
        "texto": _si("exp_texto"),
        "consecuencia": _si("exp_consecuencia"),
        "accion_principal": _si("exp_accion_principal"),
        "por_categoria": _si("exp_por_categoria"),
        "capturado_en": _iso(_si("exp_capturado_en")),
    }
    if detalle:
        out["razon"] = _textos_guardados(f.get("exp_razon")) if conocida else []
        out["recomendaciones"] = (_textos_guardados(f.get("exp_recomendaciones"))
                                  if conocida else [])
        out["ia"] = _si("exp_ia")
        out["status_ml"] = _si("exp_status_ml")
        out["status_texto"] = _si("exp_status_texto")
    return out


def _armar(f: dict[str, Any], detalle: bool) -> dict[str, Any]:
    return {"calidad": _armar_calidad(f, detalle),
            "experiencia": _armar_experiencia(f, detalle)}


_COLS_LEER = ("listing_id, metrica, estado, valor, nivel_canal, n_pendientes, "
              "pendientes, motivo, medido_en, capturado_en")
# El resumen (lista y mosaico) no carga los textos largos de la experiencia:
# solo la tarjeta del cajón pinta la razón y las recomendaciones.
_DETALLE_RESUMEN = "detalle - 'razon' - 'recomendaciones' as detalle"


def _leer(item_ids: Iterable[str], detalle: bool) -> dict[str, dict]:
    """Dict (aunque vacío) SOLO si la consulta corrió. Sin base o sin la tabla
    de la 0053 → `CalidadNoDisponible`: «no pude leer» no es «sin medir».

    Una publicación sale si tiene fila de CALIDAD (la experiencia nunca se
    escribe sin ella: van en el mismo lote); su experiencia, si la tiene."""
    ids = sorted({str(i).strip() for i in (item_ids or []) if i and str(i).strip()})
    if not ids:
        return {}
    if not sdb.disponible():
        raise CalidadNoDisponible("kubera no disponible: no se leyó la calidad de ML")
    try:
        filas = sdb.fetch_all(
            f"""select {_COLS_LEER}, {"detalle" if detalle else _DETALLE_RESUMEN}
                  from {TABLA}
                 where canal = %(canal)s
                   and listing_id = any(%(ids)s)
                   and metrica = any(%(metricas)s)""",
            {"canal": _CANAL, "ids": ids,
             "metricas": [METRICA_CALIDAD, METRICA_EXPERIENCIA]})
    except Exception as exc:  # noqa: BLE001
        if _sin_tabla(exc):
            _avisar_sin_tabla()
            raise CalidadNoDisponible(
                f"{TABLA} no existe o le faltan columnas "
                "(migración 0053 sin aplicar)") from exc
        raise
    por_id: dict[str, dict[str, dict]] = {}
    for f in filas:
        por_id.setdefault(str(f["listing_id"]), {})[f.get("metrica")] = f
    return {iid: _armar(_fila_ancha(m[METRICA_CALIDAD], m.get(METRICA_EXPERIENCIA)),
                        detalle)
            for iid, m in por_id.items() if METRICA_CALIDAD in m}


def resumen_por_items(item_ids: list[str]) -> dict[str, dict]:
    """
    BLOQUEANTE. Para la LISTA y el mosaico, UNA consulta por página:

        {item_id: {
            "calidad": {estado, score, level, level_wording, n_pendientes,
                        pendientes_top: [{titulo, accion, link}] (≤ 2, del
                        bucket de menor score primero), capturado_en,
                        calculated_at, motivo},
            "experiencia": {estado: "medida"|"sin_datos"|"sin_medir", valor,
                            color, texto, consecuencia, accion_principal,
                            por_categoria, capturado_en}}}

    La experiencia es `sin_medir` (y todo lo demás None) mientras no tenga
    fila; en `sin_datos` el valor es None, nunca 0.
    Un item sin fila de calidad no aparece (quien llama decide si es «sin
    medir» o «pausada»). Sin base o sin la tabla (42P01 / 42703, un solo
    aviso) NO degrada a {}: lanza `CalidadNoDisponible` y quien llama pinta
    «—», no «sin medir».
    """
    return _leer(item_ids, detalle=False)


def detalle_por_items(item_ids: list[str]) -> dict[str, dict]:
    """BLOQUEANTE. Para la TARJETA del cajón: lo de `resumen_por_items` más,
    en "calidad", `buckets` y `user_product_id`, y en "experiencia", `razon`
    y `recomendaciones` (listas de textos), `ia`, `status_ml` y
    `status_texto`. Mismo `CalidadNoDisponible` si no se pudo leer."""
    return _leer(item_ids, detalle=True)


def conteo(cuenta: str | None) -> dict:
    """
    BLOQUEANTE. Sobre las publicaciones ML ACTIVAS (de `cuenta` o de todas):

        {"niveles": [{"level", "level_wording", "n"}], "no_calculada": n,
         "sin_medir": n, "total_activas": n, "ultima_captura": ISO|null,
         "experiencia": {"colores": [{"color", "texto", "n"}],
                         "sin_datos": n, "sin_medir": n}}

    `niveles` va del score promedio más alto al más bajo, y `colores` del
    valor promedio más BAJO al más alto (lo que pide atención primero: "Mala",
    "Media", "Buena"): los dos órdenes salen de los datos, no de una lista
    escrita a mano. `texto` es el que más se repite para ese color. En la
    experiencia, `sin_medir` junta las activas sin ninguna fila y las que
    tienen calidad pero aún no experiencia. `ultima_captura` es la de la
    CALIDAD más reciente.

    NO degrada: sin base o sin la tabla (0053 sin aplicar) la excepción SUBE,
    y el router contesta 503 para que la pantalla esconda el grupo de botones.
    Un conteo inventado en ceros pintaría «0 Profesional» donde no se sabe
    nada.
    """
    donde, params = _activas_ml("l")
    # Una fila por publicación activa con sus dos métricas lado a lado (los
    # alias de siempre), agrupada por (calidad × experiencia).
    sql = f"""
    with activas as (
        select distinct l.canal, l.account_id, l.listing_id
          from channel.listings l
          join core.accounts a on a.id = l.account_id
         where l.canal = '{_CANAL}'
           and nullif(l.listing_id, '') is not null
           and {donde}
           and (%(cuenta)s::text is null or a.legacy_code = %(cuenta)s)
    )
    select c.estado,
           c.detalle ->> 'level'  as level,
           c.nivel_canal          as level_wording,
           e.estado               as exp_estado,
           e.detalle ->> 'color'  as exp_color,
           e.nivel_canal          as exp_texto,
           count(*)::int          as n,
           avg(c.valor)::float    as score_medio,
           avg(e.valor)::float    as exp_medio,
           max(c.capturado_en)    as ultima
      from activas x
      left join {TABLA} c on {_join_salud('c', 'x', METRICA_CALIDAD)}
      left join {TABLA} e on {_join_salud('e', 'x', METRICA_EXPERIENCIA)}
     group by 1, 2, 3, 4, 5, 6
    """
    try:
        filas = sdb.fetch_all(sql, {**params, "cuenta": _cuenta(cuenta)})
    except Exception as exc:  # noqa: BLE001
        if _sin_tabla(exc):
            _avisar_sin_tabla()
        raise

    total = sin_medir = no_calculada = 0
    exp_sin_datos = exp_sin_medir = 0
    ultima = None
    niveles: dict[str, dict[str, Any]] = {}
    colores: dict[str, dict[str, Any]] = {}
    for f in filas:
        n = int(f.get("n") or 0)
        total += n
        est = f.get("estado")
        if f.get("ultima") is not None and (ultima is None or f["ultima"] > ultima):
            ultima = f["ultima"]
        if est is None:
            sin_medir += n
        elif est == ESTADO_NO_CALCULADA:
            no_calculada += n
        elif est == ESTADO_MEDIDA and f.get("level"):
            nv = niveles.setdefault(f["level"], {"n": 0, "suma": 0.0,
                                                 "wordings": {}})
            nv["n"] += n
            if f.get("score_medio") is not None:
                nv["suma"] += float(f["score_medio"]) * n
            w = f.get("level_wording")
            if w:
                nv["wordings"][w] = nv["wordings"].get(w, 0) + n

        est_e = f.get("exp_estado")
        if est_e == EXP_MEDIDA:
            # `normalizar_experiencia` no deja pasar una medida sin color.
            cl = colores.setdefault(f.get("exp_color"), {"n": 0, "suma": 0.0,
                                                         "textos": {}})
            cl["n"] += n
            if f.get("exp_medio") is not None:
                cl["suma"] += float(f["exp_medio"]) * n
            t = f.get("exp_texto")
            if t:
                cl["textos"][t] = cl["textos"].get(t, 0) + n
        elif est_e == EXP_SIN_DATOS:
            exp_sin_datos += n
        else:
            exp_sin_medir += n

    lista = []
    for level, nv in niveles.items():
        wording = (max(nv["wordings"].items(), key=lambda kv: kv[1])[0]
                   if nv["wordings"] else None)
        lista.append({"level": level, "level_wording": wording, "n": nv["n"],
                      "_medio": nv["suma"] / nv["n"] if nv["n"] else 0.0})
    lista.sort(key=lambda d: (-d["_medio"], d["level"]))
    for d in lista:
        d.pop("_medio")

    lista_c = []
    for color, cl in colores.items():
        texto = (max(cl["textos"].items(), key=lambda kv: kv[1])[0]
                 if cl["textos"] else None)
        lista_c.append({"color": color, "texto": texto, "n": cl["n"],
                        "_medio": cl["suma"] / cl["n"] if cl["n"] else 0.0})
    lista_c.sort(key=lambda d: (d["_medio"], str(d["color"])))
    for d in lista_c:
        d.pop("_medio")

    return {"niveles": lista, "no_calculada": no_calculada, "sin_medir": sin_medir,
            "total_activas": total, "ultima_captura": _iso(ultima),
            "experiencia": {"colores": lista_c, "sin_datos": exp_sin_datos,
                            "sin_medir": exp_sin_medir}}


# ── Filtro de la rejilla (PURO) ──────────────────────────────────────────────

_ALIAS_OK = re.compile(r"^[a-z_][a-z0-9_]*$")


def filtro_sql(valor: str, alias: str = "l") -> tuple[str, dict]:
    """
    PURA. Fragmento para el WHERE de la rejilla de `channel.listings` (alias
    `alias`), con el parámetro con nombre `%(calidad_ml)s`:

      · un level tal cual (p.ej. "medium") → existe calidad medida con ese
                                               level (`detalle.level`)
      · "no_calculada"                     → existe calidad no_calculada
      · "sin_medir"                        → NO existe fila de calidad

    ValueError si el valor viene vacío o el alias no es un identificador.
    """
    if not isinstance(alias, str) or not _ALIAS_OK.match(alias):
        raise ValueError(f"alias inválido para el filtro de calidad: {alias!r}")
    v = (valor or "").strip() if isinstance(valor, str) else ""
    if not v or len(v) > 64:
        raise ValueError("filtro de calidad vacío o demasiado largo")
    sub = (f"select 1 from {TABLA} lq_f "
           f"where {_join_salud('lq_f', alias, METRICA_CALIDAD)}")
    if v == SIN_MEDIR:
        return f"not exists ({sub})", {"calidad_ml": v}
    if v == ESTADO_NO_CALCULADA:
        return f"exists ({sub} and lq_f.estado = %(calidad_ml)s)", {"calidad_ml": v}
    return (f"exists ({sub} and lq_f.estado = '{ESTADO_MEDIDA}' "
            "and lq_f.detalle ->> 'level' = %(calidad_ml)s)", {"calidad_ml": v})


def filtro_sql_experiencia(valor: str, alias: str = "l") -> tuple[str, dict]:
    """
    PURA. Gemela de `filtro_sql` para la experiencia de compra, con el
    parámetro con nombre `%(experiencia_ml)s` y su propio alias de subconsulta
    (`lq_e`), así que se combina con `filtro_sql` en el mismo WHERE (AND):

      · un color tal cual (p.ej. "red")   → existe experiencia medida de ese
                                              color (`detalle.color`)
      · "sin_datos"                        → existe experiencia sin_datos
      · "sin_medir"                        → NO existe fila de experiencia
                                              (sin ninguna fila, o con calidad
                                              pero la experiencia aún no se
                                              pregunta)

    ValueError si el valor viene vacío o el alias no es un identificador.
    """
    if not isinstance(alias, str) or not _ALIAS_OK.match(alias):
        raise ValueError(f"alias inválido para el filtro de experiencia: {alias!r}")
    v = (valor or "").strip() if isinstance(valor, str) else ""
    if not v or len(v) > 64:
        raise ValueError("filtro de experiencia vacío o demasiado largo")
    sub = (f"select 1 from {TABLA} lq_e "
           f"where {_join_salud('lq_e', alias, METRICA_EXPERIENCIA)}")
    params = {"experiencia_ml": v}
    if v == SIN_MEDIR:
        return f"not exists ({sub})", params
    if v == EXP_SIN_DATOS:
        return f"exists ({sub} and lq_e.estado = %(experiencia_ml)s)", params
    return (f"exists ({sub} and lq_e.estado = '{EXP_MEDIDA}' "
            "and lq_e.detalle ->> 'color' = %(experiencia_ml)s)", params)


# ═════════════════════════════════════════════════════════════════════════════
# El job
# ═════════════════════════════════════════════════════════════════════════════

_lock = asyncio.Lock()
# Referencia viva a la task de fondo: sin ella el recolector puede tirarla a
# media corrida (asyncio solo guarda referencias débiles a sus tasks).
_tareas: set[asyncio.Task] = set()
_estado: dict[str, Any] = {
    "fase": "inactivo", "detalle": None, "motivo": None, "inicio": None,
    "fin": None, "objetivo": 0, "consultadas": 0, "guardadas": 0,
    "cuentas_sin_token": [], **dict.fromkeys(_CLAVES_CONTEO, 0),
}


def estado() -> dict[str, Any]:
    return dict(_estado)


def _marcar(fase: str, detalle: str | None = None, **extra: Any) -> None:
    _estado["fase"] = fase
    _estado["detalle"] = detalle
    _estado.update(extra)
    if fase in ("listo", "error"):
        _estado["fin"] = time.strftime("%Y-%m-%d %H:%M:%S")
    log.info("calidad ML: %s%s", fase, f" — {detalle}" if detalle else "")


def _bitacora(estado_: str, detalle: dict[str, Any], duracion_s: float) -> None:
    """BLOQUEANTE. Una fila en ops.process_log por barrido. Nunca revienta: una
    bitácora que tumba el job es peor que no tenerla."""
    try:
        sdb.execute(
            "insert into ops.process_log "
            "  (proceso, origen, accion, estado, detalle, duracion_s) "
            "values ('calidad_ml', 'backend', 'barrido', %s, %s::jsonb, %s)",
            (estado_, json.dumps(detalle, default=str, ensure_ascii=False),
             round(float(duracion_s), 2)))
    except Exception as exc:  # noqa: BLE001
        log.warning("calidad ML: no se escribió la bitácora: %s", exc)


async def _anotar(estado_: str, detalle: dict[str, Any], t0: float) -> None:
    try:
        await asyncio.to_thread(_bitacora, estado_, detalle, time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001
        log.warning("calidad ML: bitácora fallida: %s", exc)


async def _refrescar(limite: int | None, motivo: str) -> None:
    t0 = time.monotonic()
    try:
        lista = await asyncio.to_thread(objetivo, limite)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        _marcar("error", f"no se pudo leer el objetivo: {err}", motivo=motivo)
        await _anotar("parcial", {"motivo": motivo, "error": err}, t0)
        return
    if not lista:
        # Nada que medir: todas las activas ya tienen su foto de hoy. No se
        # anota en la bitácora: serían ~13 filas vacías al día.
        _marcar("listo", "nada que medir: las activas ya tienen captura de hoy",
                motivo=motivo, objetivo=0)
        return

    # UNA lectura de core.accounts por barrido: legacy_code → account_id, que
    # es la llave de listing_health. Sin ella no hay dónde escribir: el barrido
    # no pregunta a ML para tirar las respuestas.
    try:
        ids_cuenta = await asyncio.to_thread(ids_de_cuentas)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        _marcar("error", f"no se pudieron leer las cuentas: {err}", motivo=motivo)
        await _anotar("parcial", {"motivo": motivo, "error": err}, t0)
        return

    cuentas = sorted({f["cuenta"] for f in lista})
    tokens: dict[str, str] = {}
    for c in cuentas:
        try:
            t = await asyncio.to_thread(meli._access_token, c)
        except Exception:  # noqa: BLE001
            t = None
        if t:
            tokens[c] = t
    sin_token = [c for c in cuentas if c not in tokens]

    total = dict.fromkeys(_CLAVES_CONTEO, 0)
    guardadas = consultadas = 0
    _marcar("consultando", f"0 de {len(lista)} ({motivo})", motivo=motivo,
            objetivo=len(lista), consultadas=0, guardadas=0,
            cuentas_sin_token=sin_token, **total)
    fatal: str | None = None
    # Compartido entre tandas, como `tokens`: un token renovado en este barrido
    # que vuelve a dar 401 no se renueva otra vez.
    renovados: set[str] = set()
    try:
        for i in range(0, len(lista), _TANDA):
            trozo = lista[i:i + _TANDA]
            pares = [(f["item_id"], f["cuenta"]) for f in trozo]
            filas, n = await medir(pares, tokens, renovar=meli._renovar_con_candado,
                                   renovados=renovados)
            for k in total:
                total[k] += int(n.get(k, 0))
            guardadas += await asyncio.to_thread(guardar, filas, ids_cuenta)
            consultadas += len(trozo)
            _marcar("consultando", f"{consultadas} de {len(lista)}",
                    consultadas=consultadas, guardadas=guardadas, **total)
    except Exception as exc:  # noqa: BLE001
        fatal = f"{type(exc).__name__}: {exc}"

    detalle = {"motivo": motivo, "objetivo": len(lista), "consultadas": consultadas,
               "guardadas": guardadas, "cuentas_sin_token": sin_token, **total}
    # Una experiencia que falló deja la fila a medias (vuelve a entrar en
    # unas horas): el barrido es parcial, igual que con un error de calidad.
    # `exp_rechazada` (400/403/404: ML dijo que no) NO lo vuelve parcial: es
    # una respuesta, y reintentar no la cambia. SALVO cuando es la mayoría: sin
    # `locale` ML también contesta 400, así que un cambio en los parámetros que
    # exige volvería «rechazada» a TODO el catálogo y el barrido diría «ok»
    # mientras la experiencia deja de medirse. Con 10 o más y más de la mitad
    # de lo que se preguntó, se trata como falla del barrido.
    preguntadas = (total["exp_ok"] + total["exp_sin_datos"]
                   + total["exp_rechazada"] + total["exp_error"])
    rechazo_masivo = (total["exp_rechazada"] >= _RECHAZO_MASIVO_MIN
                      and total["exp_rechazada"] * 2 > preguntadas)
    if rechazo_masivo:
        detalle["rechazo_masivo"] = True
    sano = (fatal is None and not sin_token and not rechazo_masivo
            and total["error"] == 0 and total["limitada_429"] == 0
            and total["sin_token"] == 0 and total["exp_error"] == 0)
    if fatal:
        detalle["error"] = fatal
        _marcar("error", fatal, consultadas=consultadas, guardadas=guardadas, **total)
    else:
        partes = [f"{total['ok']} medidas", f"{total['no_calculada']} que ML no califica"]
        for k, txt in (("limitada_429", "limitadas por 429"),
                       ("sin_token", "sin token válido"), ("error", "con error")):
            if total[k]:
                partes.append(f"{total[k]} {txt}")
        exp = [f"experiencia: {total['exp_ok']} medidas",
               f"{total['exp_sin_datos']} sin datos"]
        if total["exp_error"]:
            exp.append(f"{total['exp_error']} con error")
        if total["exp_rechazada"]:
            exp.append(f"{total['exp_rechazada']} rechazadas por ML")
        partes.append(", ".join(exp))
        _marcar("listo", f"{len(lista)} publicaciones: " + " · ".join(partes),
                consultadas=consultadas, guardadas=guardadas, **total)
    await _anotar("ok" if sano else "parcial", detalle, t0)


async def refrescar_en_fondo(limite: int | None = None,
                             motivo: str = "manual") -> dict[str, Any]:
    """Dispara el barrido si no hay uno corriendo y contesta de inmediato.

    Quien llame NO espera: son cientos de llamadas a ML y una petición HTTP que
    las aguarde se pasa del timeout del proxy. El avance se lee en `estado()`.
    Si ya hay uno corriendo devuelve su estado y no encola otro.
    """
    if _lock.locked() or any(not t.done() for t in _tareas):
        return estado()

    async def _con_candado() -> None:
        async with _lock:
            try:
                await _refrescar(limite, motivo)
            except Exception as exc:  # noqa: BLE001
                log.exception("calidad ML: el barrido tronó")
                _marcar("error", f"{type(exc).__name__}: {exc}", motivo=motivo)

    _estado.update({"fase": "arrancando", "detalle": None, "motivo": motivo,
                    "inicio": time.strftime("%Y-%m-%d %H:%M:%S"), "fin": None,
                    "objetivo": 0, "consultadas": 0, "guardadas": 0,
                    "cuentas_sin_token": [], **dict.fromkeys(_CLAVES_CONTEO, 0)})
    tarea = asyncio.create_task(_con_candado(), name="calidad_ml")
    _tareas.add(tarea)
    tarea.add_done_callback(_tareas.discard)
    return estado()


async def corrida_programada() -> None:
    """La vuelta del scheduler. Sale sin hacer nada con cualquiera de las dos
    llaves apagada o antes de `CALIDAD_ML_HORA_UTC`; si no, mide lo que falte
    de HOY (con tope `CALIDAD_ML_POR_CORRIDA`, 0 = sin tope)."""
    if not (settings.sync_enabled and settings.calidad_ml_enabled):
        return
    if datetime.now(timezone.utc).hour < int(settings.calidad_ml_hora_utc):
        return
    await refrescar_en_fondo(int(settings.calidad_ml_por_corrida or 0) or None,
                             "programada")
