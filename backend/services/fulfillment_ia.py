"""
fulfillment_ia.py — El AGENTE de planeación de FULL: la IA arma, con el PROMPT
ESTÁNDAR (el que le pasaron a Brandon el 24-sep-2026), la lista de qué mandar esta
semana a cada tienda, y la persona la afina conversando con ella.

POR QUÉ LA IA NO SUMA
─────────────────────
Los números los pone el código: velocidad, objetivo, faltante y el tope de lo libre
en Odoo (`proponer.ts`), y los totales de la pantalla. Lo que la IA aporta es juicio
—qué mandar primero, qué reemplazar y por cuál, qué dejar— y cada cosa que propone
se VALIDA aquí contra los datos que se le dieron: la tienda existe, el SKU está en su
planeación o entre los reemplazos que el código encontró, y la cantidad no pasa de lo
libre. La primera prueba en vivo (28-sep) lo confirmó: los 37 SKUs que quitó eran
exactamente los correctos, pero la suma que escribió en su texto salió 24 piezas
corta. Por eso ya no escribe totales: el panel los calcula.

LA SEMANA (v0.583.0, Brandon, 28-sep: "al iniciar una nueva week la planeación estará
vacía para usar la IA… el chat deberá respetar el tiempo week over week"). Cada semana
tiene UN chat, compartido por el equipo y guardado en la bitácora
(`services/fulfillment_semana.py`): entrar a la pantalla ya no empieza una
conversación nueva ni llama a la IA. El plan de la semana nace VACÍO y la IA lo arma;
sus ajustes se aplican EN VIVO a la tabla mientras los escribe (la respuesta llega por
streaming y se valida por pedazos, `parcial`).

LO QUE DEVUELVE (Brandon, 28-sep: "el apartado de recomendaciones lo borras… deberá de
mandarse en un solo prompt los ajustes propuestos con las recomendaciones en esos
ajustes… elimina por completo las alertas de la IA… la confirmación y el resumen
bórralo"). Tres llaves: `respuesta` (1 a 3 frases), `ajustes` ([tienda, sku, piezas,
recomendación]) y `reemplazos` ([tienda, agotado, reemplazo, piezas, recomendación]),
en listas y no en objetos: la salida pesa casi la mitad.

EFICIENCIA. La planeación va al PRINCIPIO de la conversación y se congela UNA vez al
día; la conversación se rearma con los textos EXACTOS de cada turno (guardados). Así
DeepSeek relee de su caché todo lo anterior y cada seguimiento paga sólo lo nuevo
(la entrada de caché cuesta 30 veces menos). El plan de ese momento viaja en el último
mensaje, no en la tabla: cambiarlo no rompe la caché.

SÓLO DEEPSEEK (v0.579.0, Brandon, 27-sep: "no uses Claude, es muy caro"). Por omisión
DeepSeek V4 Pro; en la pantalla se elige DeepSeek Flash, el más barato. Claude no se
puede elegir: `_llamar_claude` se conserva por si algún día se pide. Si DeepSeek falla,
el error se enseña: no se cae a Claude sin decir nada. Cada turno dice cuánto costó.

Tarda (lee la planeación completa y razona): corre en un hilo y la pantalla pregunta
por él (`iniciar` / `estado`). Nada de esto escribe en Odoo ni en ningún marketplace.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from config import settings
from services import fulfillment_semana as fsem

log = logging.getLogger("omnicanal.fulfillment_ia")

MODELO = "claude-opus-5"      # el modelo de Claude (`_llamar_claude`, no se ofrece)
MAX_TRABAJOS = 2          # turnos a la vez en todo el panel
MAX_INSTRUCCION = 2000    # caracteres por instrucción
ESFUERZO = "medium"       # Claude: low | medium | high | xhigh | max
MAX_TURNOS = 8            # turnos de la semana que se le recuerdan a la IA
MAX_SALIDA = 80_000       # caracteres de la respuesta que se guardan para rearmar la conversación
VIDA_TRABAJO_S = 3600

# Los modelos que se pueden elegir en la pantalla.
MODELOS: dict[str, dict[str, str]] = {
    "deepseek-v4-pro": {"proveedor": "deepseek", "nombre": "DeepSeek V4 Pro",
                        "nota": "bueno y barato: la opción por omisión"},
    "deepseek-flash": {"proveedor": "deepseek", "nombre": "DeepSeek Flash", "nota": "el más barato"},
    # Claude Opus 5 fuera por decisión de Brandon (27-sep: "es muy caro"). Para volver a
    # ofrecerlo: {"proveedor": "claude", "nombre": "Claude Opus 5", ...} y su precio abajo.
}

# Precios de LISTA por millón de tokens (US$), consultados el 27-sep-2026 en las páginas de
# precios de DeepSeek. Sirven para ESTIMAR el costo de un turno; la factura manda. Son los
# de hora pico (01:00-04:00 y 06:00-10:00 UTC, entre semana); fuera de ella cobra la mitad.
# (Claude Opus 5, que ya no se ofrece, cuesta 5.00 de entrada y 25.00 de salida.)
PRECIOS: dict[str, dict[str, float]] = {
    "deepseek-v4-pro": {"entrada": 1.32, "cache_leida": 0.044, "salida": 3.96},
    "deepseek-flash": {"entrada": 0.30, "cache_leida": 0.006, "salida": 1.20},
}


# El prompt TAL CUAL lo pasaron (24-sep-2026). Lo que cambió en el panel se dice
# aparte (AJUSTES_DEL_PANEL) en vez de reescribirlo: así se puede comparar.
PROMPT_ESTANDAR = """PLANEACIÓN SEMANAL DE FULL — KUBERA / OMNICANAL

Rol: eres el asistente de planeación de reabasto a los almacenes de marketplace
(FULL de Mercado Libre — cuentas BEKURA=Kubera y SANCORFASHION=San Corpe —,
FBA de Amazon San Corpe, WFS de Walmart). Tu salida es una LISTA de qué SKUs y
cuántas piezas conviene mandar esta semana, lista para pegarse en el apartado
"Planeación semanal" de /fulfillment.

REGLAS DE LA CASA (no negociables):
1. Inventario: ODOO es el MASTER. "Libre Odoo" = free_qty por almacén (lo
   vendible, ya descontadas reservas). Nunca propongas más piezas que free_qty.
2. Ventas = fuente de verdad WooCommerce (pedidos con _ml_cuenta =
   BEKURA/SANCORFASHION). Stock ya en Full = MySQL canal_inventario
   (canal=mercado_libre, stock_full). Todo es LECTURA: no escribas en Woo,
   Odoo, kubera ni ningún marketplace.
3. Un dato ausente NO es un cero: si Bodega aún no revisa un renglón, va como
   "pendiente", no como recorte a 0.
4. Entrega los SKUs separados por coma (para capturarlos en Omnicanal).

CÁLCULO POR SKU:
- velocidad_dia = unidades_vendidas_en_ventana / dias_ventana
- objetivo = ceil(velocidad_dia * cobertura_objetivo)
- faltante = max(0, objetivo - stock_en_full_actual)
- enviar = min(free_qty_odoo, faltante)
- Estado del renglón:
    · "aprobado" si Bodega puede cubrir lo pedido (bodega >= pidió)
    · "recorte"  si free_qty no alcanza (bodega < pidió); Bodega puede = free_qty
    · "pendiente" si Bodega todavía no revisó ese renglón

GANADORES AGOTADOS → REEMPLAZO:
- Ganador agotado = vendió >= 3 en la ventana Y free_qty = 0 Y stock_full = 0.
- Para cada uno sugiere reemplazo con stock, en este orden:
    (1) MISMO MODELO (misma base CAT-NNNN, otro color/talla) con free_qty>0
    (2) MISMA CATEGORÍA ML (categorias_ml) con free_qty>0, mayor free_qty primero
- Solo reemplazos YA PUBLICADOS (en ml_progress con ml_item_id, o en
  canal_inventario ML). Marca si el match fue "misma categoría ML" o el más
  débil "misma por nombre".
- Cruza SIEMPRE contra free_qty antes de declarar "agotado": SOLD = demanda,
  no stockout. Cuidado con SKUs reciclados (categoría/atributos del producto
  viejo).

SALIDA (en este formato):
A) Tabla por SKU: SKU · nombre (usar nombre de Omnicanal) | Destino | Libre Odoo
   | Pidió | Bodega puede | Propuesta | Estado.
B) Totales: piezas pedidas, piezas propuestas, tasa de validado
   (bodega / pedido de renglones revisados), % final vs pedido.
C) Ganadores agotados y su reemplazo sugerido.
D) Lista de SKUs a enviar, separada por coma.
E) Alertas: SKUs sin categoría ML, dimensiones sospechosas (caja master:
   peso<=0.5kg con dims ~60x41x41), reciclados detectados.

Antes de entregar, confirma qué cuenta y ventana usaste, y marca claramente lo
que es dato en vivo vs. lo que quedó pendiente por falta de revisión de Bodega."""

AJUSTES_DEL_PANEL = """CÓMO SE CORRE ESTE PROMPT DENTRO DEL PANEL (manda sobre lo de arriba donde choquen):

- LA SEMANA EMPIEZA VACÍA. Cada semana (de lunes a domingo, hora de CDMX) el plan de FULL nace en cero y TÚ lo
  armas: tu lista es lo que se va a mandar esta semana. La columna "propuesta" es lo que sugiere el cálculo del
  prompt estándar para cada SKU: úsala como punto de partida y afínala con tu criterio y con lo que pida la persona.
- Los datos YA vienen leídos y calculados en el JSON del primer mensaje. No los busques ni los recalcules por tu
  cuenta y NUNCA inventes una cifra que no esté ahí. MySQL (canal_inventario, ml_progress) está congelado desde el
  13-ago-2026: el panel usa kubera, Mercado Libre verificado en vivo y Odoo en vivo.
- Cada tienda trae su planeación COMPLETA como tabla: "columnas" dice qué es cada posición y "filas" trae un SKU por
  renglón. "precio" = precio de venta de hoy en esa tienda, en pesos; null = no se sabe. "pidio" = faltante para la
  cobertura; "libre" = lo que bodega puede surtir a esa tienda (free_qty de Odoo menos el colchón para DROP, ya
  repartido si varias tiendas piden el mismo SKU); "propuesta" = lo que sugiere el prompt estándar. "titulo_mkt" sólo
  viene cuando el título del marketplace no se parece al producto (posible reciclado).
- Al faltante ya se le restó lo que va en camino y lo que está en borradores, para no mandar dos veces lo mismo.
- Cada mensaje de la persona trae el PLAN ACTUAL de la semana: lo que ya va, con tus ajustes anteriores y lo que la
  persona cambió a mano. Trabaja sobre ese plan.
- Eres el agente de planeación de la persona: sigue sus instrucciones (p. ej. «sólo SKUs con precio menor a $300»,
  «prioriza lo que más vende esta semana») sobre la tabla completa sin romper las reglas de la casa: nunca más que
  "libre", nunca un SKU fuera de la tabla de esa tienda o de los candidatos de un ganador agotado, nunca una cifra
  inventada. Si una instrucción no se puede cumplir con estos datos, dilo. Si te hace una pregunta, contéstala con
  los datos y no cambies el plan.
- Devuelve SÓLO el JSON del formato de abajo:
  · "respuesta": de 1 a 3 frases para la persona: qué hiciste y por qué. NO sumes piezas ni cuentes SKUs: el panel
    calcula los totales exactos.
  · "ajustes": cada cambio al plan como [tienda, sku, piezas, recomendación]. Las piezas son lo que debe ir EN TOTAL
    de ese SKU a esa tienda (no una diferencia); 0 = sacarlo del plan. La recomendación dice por qué, en máximo 15
    palabras, como la diría un planeador («vende 8 al día y no le queda stock en FULL: mandar ya»). Con el plan
    vacío, pon todo lo que recomiendas mandar esta semana; después, sólo lo que cambia.
  · "reemplazos": para cada ganador agotado que convenga cubrir, [tienda, agotado, reemplazo, piezas,
    recomendación] con un candidato de SU lista ("candidatos": ya publicados y con libre > 0). Un mismo SKU no
    reemplaza a dos agotados.
- "tienda" es la LLAVE de la tienda tal como viene en el objeto "tiendas" ("meli:Kubera", "meli:San Corpe",
  "amazon", "walmart"), no su nombre.
- Temu y TikTok son únicamente DROP: no entran en esta planeación.
- Escribe en español de México, directo y sin tecnicismos."""

# DeepSeek no ata la salida a un esquema: el modo JSON pide que el prompt diga "json" y
# traiga un ejemplo de la forma. `validar` descarta lo que no cuadre. La `respuesta` va
# primero para que la pantalla la enseñe en cuanto llega.
FORMATO_JSON = """FORMATO DE SALIDA: responde con UN solo objeto JSON válido (json), sin texto antes ni después, con
exactamente estas tres llaves y en este orden (las listas pueden ir vacías):
{"respuesta": "…",
 "ajustes": [["meli:Kubera", "SKU-DE-LA-TABLA", 24, "vende 3 al día y le quedan 2 en FULL"]],
 "reemplazos": [["meli:San Corpe", "SKU-AGOTADO", "SKU-CANDIDATO", 12, "mismo modelo en otro color, con stock"]]}
Las piezas son enteros. Los SKU son los de la tabla (o los candidatos de un ganador agotado), nunca los de este
ejemplo."""

SISTEMA = PROMPT_ESTANDAR + "\n\n" + AJUSTES_DEL_PANEL + "\n\n" + FORMATO_JSON

# La tienda va por su LLAVE: en la primera corrida real (24-sep) la IA escribió «ML Kubera»
# y el validador descartó los 14 ajustes y todos los reemplazos por no reconocerla.
_LLAVES = ["meli:Kubera", "meli:San Corpe", "amazon", "walmart"]

# Sólo lo usa `_llamar_claude` (Claude ata la salida a un esquema; DeepSeek no).
ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "respuesta": {"type": "string"},
        "ajustes": {"type": "array", "items": {
            "type": "object",
            "properties": {"tienda": {"type": "string", "enum": _LLAVES}, "sku": {"type": "string"},
                           "cantidad": {"type": "integer"}, "motivo": {"type": "string"}},
            "required": ["tienda", "sku", "cantidad", "motivo"], "additionalProperties": False}},
        "reemplazos": {"type": "array", "items": {
            "type": "object",
            "properties": {"tienda": {"type": "string", "enum": _LLAVES}, "agotado": {"type": "string"},
                           "reemplazo": {"type": "string"}, "cantidad": {"type": "integer"},
                           "motivo": {"type": "string"}},
            "required": ["tienda", "agotado", "reemplazo", "cantidad", "motivo"], "additionalProperties": False}},
    },
    "required": ["respuesta", "ajustes", "reemplazos"],
    "additionalProperties": False,
}

_trabajos: dict[str, dict[str, Any]] = {}
_candado = threading.Lock()


def _hay_llave(proveedor: str) -> bool:
    return bool(settings.deepseek_api_key if proveedor == "deepseek" else settings.anthropic_api_key)


def disponible() -> bool:
    return any(_hay_llave(m["proveedor"]) for m in MODELOS.values())


def modelos_disponibles() -> list[dict[str, Any]]:
    """Los modelos para el selector de la pantalla, con si hay llave para usarlos."""
    return [{"id": k, **v, "disponible": _hay_llave(v["proveedor"])} for k, v in MODELOS.items()]


def modelo_por_omision() -> str:
    """El de la configuración si se puede usar; si no, el primero que tenga llave."""
    elegido = settings.fulfillment_ia_modelo
    if elegido in MODELOS and _hay_llave(MODELOS[elegido]["proveedor"]):
        return elegido
    return next((k for k, v in MODELOS.items() if _hay_llave(v["proveedor"])), elegido)


def es_hora_pico(cuando: datetime) -> bool:
    """Las horas pico de DeepSeek: 01:00-04:00 y 06:00-10:00 UTC, de lunes a viernes."""
    u = cuando.astimezone(timezone.utc)
    return u.weekday() < 5 and (1 <= u.hour < 4 or 6 <= u.hour < 10)


def costo_usd(modelo: str, tokens: dict[str, Any], cuando: datetime) -> float | None:
    """
    Lo que costó un turno, en dólares, con los precios de lista. `tokens` trae
    `entrada_sin_cache`, `cache` (leídos de caché), `cache_escrita` y `salida`.
    """
    p = PRECIOS.get(modelo)
    if not p:
        return None
    total = (int(tokens.get("entrada_sin_cache") or 0) * p["entrada"]
             + int(tokens.get("cache") or 0) * p["cache_leida"]
             + int(tokens.get("cache_escrita") or 0) * p.get("cache_escrita", p["entrada"])
             + int(tokens.get("salida") or 0) * p["salida"]) / 1_000_000
    if MODELOS.get(modelo, {}).get("proveedor") == "deepseek" and not es_hora_pico(cuando):
        total /= 2
    return round(total, 4)


_SINONIMOS = {"kubera": "meli:Kubera", "bekura": "meli:Kubera", "ml kubera": "meli:Kubera",
              "san corpe": "meli:San Corpe", "sancorfashion": "meli:San Corpe", "ml san corpe": "meli:San Corpe",
              "amazon fba": "amazon", "fba": "amazon", "walmart wfs": "walmart", "wfs": "walmart"}


def tienda_de(valor: Any, tiendas: dict[str, Any]) -> str:
    """La llave de la tienda aunque la IA la nombre como la ve («ML Kubera», «BEKURA», «meli_bekura»)."""
    v = str(valor or "").strip()
    if v in tiendas:
        return v
    bajo = v.lower()
    for t, d in tiendas.items():
        if bajo in (t.lower(), str((d or {}).get("nombre") or "").lower(), str((d or {}).get("destino") or "").lower()):
            return t
    return _SINONIMOS.get(bajo, v)


def renglones_de(d: dict[str, Any]) -> list[dict[str, Any]]:
    """Los renglones de una tienda, vengan como objetos («renglones») o como tabla compacta."""
    if d.get("renglones"):
        return list(d["renglones"])
    cols = d.get("columnas") or []
    return [dict(zip(cols, f)) for f in d.get("filas") or [] if isinstance(f, list)]


def _campos(x: Any, llaves: list[str], alias: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Un elemento de la respuesta como dict, venga en lista (lo que se pide) o como objeto."""
    if isinstance(x, (list, tuple)):
        return {k: (x[i] if i < len(x) else None) for i, k in enumerate(llaves)}
    if isinstance(x, dict):
        d = {k: x.get(k) for k in llaves}
        for otro, k in (alias or {}).items():
            if d.get(k) is None and x.get(otro) is not None:
                d[k] = x[otro]
        return d
    return None


def validar(respuesta: dict[str, Any], datos: dict[str, Any]) -> dict[str, Any]:
    """
    Lo que la IA contestó, pasado por los datos que se le dieron. Función pura.
    Un ajuste fuera de la planeación o por encima de lo libre NO pasa: se descarta
    (o se topa) y se dice por qué. Un SKU ajustado dos veces: manda el último.
    """
    tiendas = datos.get("tiendas") or {}
    permitidos: dict[str, dict[str, int]] = {}
    candidatos: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for t, d in tiendas.items():
        libres = {r["sku"]: int(r.get("libre") or 0) for r in renglones_de(d) if r.get("sku")}
        for g in d.get("ganadores_agotados") or []:
            cs = {c["sku"]: {"libre": int(c.get("libre") or 0), "tipo": str(c.get("tipo") or "")}
                  for c in g.get("candidatos") or [] if c.get("sku")}
            candidatos[(t, g["sku"])] = cs
            for s, c in cs.items():
                libres.setdefault(s, c["libre"])
        permitidos[t] = libres
    descartados: list[dict[str, Any]] = []

    ajustes: dict[tuple[str, str], dict[str, Any]] = {}
    for crudo in respuesta.get("ajustes") or []:
        a = _campos(crudo, ["tienda", "sku", "cantidad", "motivo"], {"piezas": "cantidad", "recomendacion": "motivo"})
        if a is None:
            continue
        t, sku = tienda_de(a.get("tienda"), tiendas), str(a.get("sku") or "").strip()
        try:
            n = int(a.get("cantidad"))
        except (TypeError, ValueError):
            descartados.append({**a, "porque": "cantidad no numérica"})
            continue
        if t not in permitidos or sku not in permitidos[t]:
            descartados.append({**a, "porque": "el SKU no está en la planeación de esa tienda"})
            continue
        if n < 0:
            descartados.append({**a, "porque": "cantidad negativa"})
            continue
        tope, nota = permitidos[t][sku], None
        if n > tope:
            nota = f"la IA pidió {n}; se topó a lo libre ({tope})"
            n = tope
        ajustes.pop((t, sku), None)          # el último manda y queda al final
        ajustes[(t, sku)] = {"tienda": t, "sku": sku, "cantidad": n, "motivo": str(a.get("motivo") or "")[:300],
                             "nota": nota}

    reemplazos: list[dict[str, Any]] = []
    usados: dict[tuple[str, str], str] = {}
    for crudo in respuesta.get("reemplazos") or []:
        r = _campos(crudo, ["tienda", "agotado", "reemplazo", "cantidad", "motivo"],
                    {"piezas": "cantidad", "recomendacion": "motivo"})
        if r is None:
            continue
        t, ag, re_ = tienda_de(r.get("tienda"), tiendas), str(r.get("agotado") or ""), str(r.get("reemplazo") or "")
        c = candidatos.get((t, ag), {}).get(re_)
        if not c:
            descartados.append({**r, "porque": "el reemplazo no está entre los candidatos publicados con stock"})
            continue
        if (t, re_) in usados:
            descartados.append({**r, "porque": f"{re_} ya es el reemplazo de {usados[(t, re_)]}"})
            continue
        try:
            n = max(0, int(r.get("cantidad")))
        except (TypeError, ValueError):
            n = 0
        nota = None
        if n > c["libre"]:
            nota = f"la IA pidió {n}; se topó a lo libre ({c['libre']})"
            n = c["libre"]
        usados[(t, re_)] = ag
        reemplazos.append({"tienda": t, "agotado": ag, "reemplazo": re_, "cantidad": n,
                           "tipo_match": c["tipo"][:40], "motivo": str(r.get("motivo") or "")[:300], "nota": nota})
    return {"respuesta": str(respuesta.get("respuesta") or "")[:3000], "ajustes": list(ajustes.values()),
            "reemplazos": reemplazos, "descartados": descartados[:200]}


# ── Leer la respuesta A MEDIAS (streaming) ──────────────────────────────────

def _lista_parcial(texto: str, llave: str) -> list[Any]:
    """Los elementos YA completos de la lista `llave` en un JSON que se sigue escribiendo."""
    m = re.search(r'"' + re.escape(llave) + r'"\s*:\s*\[', texto)
    if not m:
        return []
    dec, i, n, salida = json.JSONDecoder(), m.end(), len(texto), []
    while i < n:
        while i < n and texto[i] in " \t\r\n,":
            i += 1
        if i >= n or texto[i] == "]":
            break
        try:
            obj, i = dec.raw_decode(texto, i)
        except json.JSONDecodeError:
            break                     # el elemento todavía no termina de llegar
        salida.append(obj)
    return salida


def _texto_parcial(texto: str, llave: str) -> str | None:
    """El valor de un campo de texto si ya llegó completo."""
    m = re.search(r'"' + re.escape(llave) + r'"\s*:\s*"', texto)
    if not m:
        return None
    try:
        valor, _ = json.JSONDecoder().raw_decode(texto, m.end() - 1)
    except json.JSONDecodeError:
        return None
    return valor if isinstance(valor, str) else None


def parcial(texto: str, datos: dict[str, Any]) -> dict[str, Any]:
    """Lo que la IA lleva escrito, ya validado: se aplica a la tabla mientras sigue escribiendo."""
    r = validar({"respuesta": _texto_parcial(texto, "respuesta") or "",
                 "ajustes": _lista_parcial(texto, "ajustes"), "reemplazos": _lista_parcial(texto, "reemplazos")}, datos)
    return {"respuesta": r["respuesta"], "ajustes": r["ajustes"], "reemplazos": r["reemplazos"]}


# ── La conversación ─────────────────────────────────────────────────────────

def _instruccion(texto: str) -> str:
    texto = (texto or "").strip()[:MAX_INSTRUCCION]
    return (f"Instrucción de la persona que planea: {texto}" if texto
            else "Sin instrucciones: arma (o revisa) el FULL de esta semana con el prompt estándar.")


def plan_actual(plan: Any) -> dict[str, list[list[Any]]]:
    """
    El plan de este momento, compacto y ORDENADO (tienda → [sku, piezas]): lo que
    ya va. Llega de la pantalla como [[tienda, sku, piezas], …]; lo que va en 0 no entra.
    """
    salida: dict[str, dict[str, int]] = {}
    for x in plan if isinstance(plan, list) else []:
        if not isinstance(x, (list, tuple)) or len(x) < 3 or x[0] not in _LLAVES:
            continue
        try:
            n = int(x[2])
        except (TypeError, ValueError):
            continue
        if n > 0:
            salida.setdefault(x[0], {})[str(x[1])[:60]] = min(n, 100_000)
    return {t: [[s, n] for s, n in sorted(v.items())] for t, v in sorted(salida.items())}


def mensaje_turno(instruccion: str, plan: dict[str, list[list[Any]]]) -> str:
    """El mensaje de la persona en un turno: el plan de ese momento y su instrucción."""
    if any(plan.values()):
        estado = ("PLAN ACTUAL de la semana (lo que ya va, tienda → [SKU, piezas]):\n"
                  + json.dumps(plan, ensure_ascii=False, separators=(",", ":")))
    else:
        estado = "PLAN ACTUAL de la semana: vacío (la semana empieza en cero; tú armas la lista)."
    return estado + "\n\n" + _instruccion(instruccion)


def conversacion_openai(datos: dict[str, Any], turnos: list[dict[str, Any]], mensaje: str) -> list[dict[str, Any]]:
    """
    La conversación en el formato de DeepSeek (el de OpenAI). Función pura.

    La planeación va al PRINCIPIO del primer mensaje y cada turno anterior se repite con
    su texto EXACTO (el que se guardó): así el prefijo es idéntico de un turno al
    siguiente y DeepSeek lo relee de su caché. Al final, el mensaje de ahora.
    """
    primera = turnos[0]["mensaje"] if turnos else mensaje
    salida: list[dict[str, Any]] = [{"role": "user", "content": (
        json.dumps(datos, ensure_ascii=False, separators=(",", ":")) + "\n\n" + primera)}]
    for i, t in enumerate(turnos):
        salida.append({"role": "assistant", "content": t["salida"]})
        salida.append({"role": "user", "content": turnos[i + 1]["mensaje"] if i + 1 < len(turnos) else mensaje})
    return salida


def _json_de(texto: str) -> dict[str, Any]:
    t = (texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    return json.loads(t)


# ── DeepSeek, por streaming ─────────────────────────────────────────────────

def leer_sse(lineas: Iterable[str], al_avanzar: Callable[[str, int], None] | None = None) -> dict[str, Any]:
    """
    Lee el stream de DeepSeek (Server-Sent Events). Función pura sobre las líneas.
    El razonamiento llega en `reasoning_content` y la respuesta en `content`; el
    último pedazo trae el uso de tokens (`stream_options.include_usage`). Cada ~1 s
    avisa lo que lleva: así la pantalla aplica los ajustes mientras se escriben.
    """
    contenido: list[str] = []
    razon, uso, fin, modelo, ultimo = 0, {}, None, None, 0.0
    for linea in lineas:
        if not linea or not linea.startswith("data:"):
            continue                          # vacías y «: keep-alive»
        dato = linea[5:].strip()
        if dato == "[DONE]":
            break
        try:
            pedazo = json.loads(dato)
        except ValueError:
            continue
        modelo = pedazo.get("model") or modelo
        if pedazo.get("usage"):
            uso = pedazo["usage"]
        for ch in pedazo.get("choices") or []:
            d = ch.get("delta") or {}
            razon += len(d.get("reasoning_content") or "")
            if d.get("content"):
                contenido.append(d["content"])
            fin = ch.get("finish_reason") or fin
        if al_avanzar and time.monotonic() - ultimo >= 1.0:
            ultimo = time.monotonic()
            al_avanzar("".join(contenido), razon)
    texto = "".join(contenido)
    if al_avanzar:
        al_avanzar(texto, razon)
    return {"texto": texto, "razonamiento_chars": razon, "uso": uso, "fin": fin, "modelo": modelo}


def _pedir(cuerpo: dict[str, Any], al_avanzar: Callable[[str, int], None] | None) -> dict[str, Any]:
    """Una petición por streaming a DeepSeek. BLOQUEA. {"status", "error"} o lo de `leer_sse`."""
    import httpx

    url = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
    # `read` es la espera ENTRE pedazos, no el total: mientras razona, llegan pedazos.
    with httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0)) as cli:
        with cli.stream("POST", url, json=cuerpo,
                        headers={"Authorization": f"Bearer {settings.deepseek_api_key}"}) as r:
            if r.status_code >= 400:
                return {"status": r.status_code, "error": r.read().decode("utf-8", "replace")[:300]}
            return {"status": r.status_code, "error": None, **leer_sse(r.iter_lines(), al_avanzar)}


def _llamar_deepseek(mensajes: list[dict[str, Any]], modelo: str,
                     al_avanzar: Callable[[str, int], None] | None = None) -> dict[str, Any]:
    """
    Un turno con DeepSeek. BLOQUEA (corre en su hilo). Su documentación no dice si el
    razonamiento (encendido por omisión) convive con el modo JSON, y reconoce que a
    veces contesta vacío: en cualquiera de los dos casos se reintenta UNA vez sin
    razonamiento.
    """
    base = {"model": modelo, "max_tokens": 64000, "stream": True, "stream_options": {"include_usage": True},
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SISTEMA}] + mensajes}
    ultimo_error = "la IA no devolvió texto"
    for intento, extra in enumerate(({}, {"thinking": {"type": "disabled"}})):
        s = _pedir({**base, **extra}, al_avanzar)
        if s["status"] == 400 and intento == 0:
            ultimo_error = f"DeepSeek rechazó la petición: {s['error']}"
            log.warning("planeación IA: %s; reintento sin razonamiento", ultimo_error)
            continue
        if s["status"] >= 400:
            raise RuntimeError(f"DeepSeek contestó {s['status']}: {s['error']}")
        if s["fin"] == "length":
            raise RuntimeError("la respuesta de la IA se cortó (demasiados renglones): prueba con menos tiendas")
        texto = (s["texto"] or "").strip()
        if not texto:
            ultimo_error = "DeepSeek devolvió la respuesta vacía"
            log.warning("planeación IA: %s (intento %s)", ultimo_error, intento + 1)
            continue
        uso = s["uso"] or {}
        entrada = int(uso.get("prompt_tokens") or 0)
        hit = int(uso.get("prompt_cache_hit_tokens") or 0)
        razon = int(((uso.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0)
        log.info("planeación IA: %s · entrada %s (%s de caché) · salida %s (%s de razonamiento)%s", modelo,
                 entrada, hit, uso.get("completion_tokens"), razon, " · sin razonamiento" if extra else "")
        return {"respuesta": _json_de(texto), "texto": texto, "modelo": s["modelo"] or modelo,
                "tokens": {"entrada": entrada, "entrada_sin_cache": max(0, entrada - hit), "cache": hit,
                           "salida": int(uso.get("completion_tokens") or 0), "razonamiento": razon}}
    raise RuntimeError(ultimo_error)


def _llamar(mensajes: list[dict[str, Any]], modelo: str | None = None,
            al_avanzar: Callable[[str, int], None] | None = None) -> dict[str, Any]:
    """Un turno con el modelo elegido. BLOQUEA (corre en su hilo)."""
    modelo = modelo if modelo in MODELOS else modelo_por_omision()
    if MODELOS[modelo]["proveedor"] == "deepseek":
        return _llamar_deepseek(mensajes, modelo, al_avanzar)
    return _llamar_claude(mensajes)


def _llamar_claude(mensajes: list[dict[str, Any]]) -> dict[str, Any]:
    """La llamada a Claude (no se ofrece desde el 27-sep). BLOQUEA (corre en su hilo)."""
    import anthropic

    cli = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=900.0, max_retries=1)
    cuerpo_extra: dict[str, Any] = {"thinking": {"type": "adaptive"},
                                    "output_config": {"effort": ESFUERZO,
                                                      "format": {"type": "json_schema", "schema": ESQUEMA}}}
    raw = cli.messages.with_raw_response.create(
        model=MODELO, max_tokens=64000, system=PROMPT_ESTANDAR + "\n\n" + AJUSTES_DEL_PANEL,
        messages=mensajes, extra_body=cuerpo_extra)
    # El SDK instalado (0.42) devuelve un LegacyAPIResponse: sin `.json()`, con `.text`.
    cuerpo = json.loads(raw.text)
    uso = cuerpo.get("usage") or {}
    if cuerpo.get("stop_reason") in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude no terminó la respuesta ({cuerpo.get('stop_reason')})")
    texto = next((b.get("text") for b in cuerpo.get("content") or [] if b.get("type") == "text"), None)
    if not texto:
        raise RuntimeError("la IA no devolvió texto")
    leidos, escritos = int(uso.get("cache_read_input_tokens") or 0), int(uso.get("cache_creation_input_tokens") or 0)
    return {"respuesta": json.loads(texto), "texto": texto, "modelo": cuerpo.get("model"),
            "tokens": {"entrada": int(uso.get("input_tokens") or 0) + leidos + escritos,
                       "entrada_sin_cache": int(uso.get("input_tokens") or 0), "cache_escrita": escritos,
                       "salida": uso.get("output_tokens"), "cache": leidos}}


# ── Los turnos: arrancan en un hilo y la pantalla pregunta ──────────────────

def _podar() -> None:
    ahora = time.time()
    for k in [k for k, v in _trabajos.items() if ahora - v["inicio"] > VIDA_TRABAJO_S]:
        _trabajos.pop(k, None)


def _huella(datos: dict[str, Any]) -> str:
    """Qué planeación es: tiendas y parámetros. Si cambian, la IA recibe otra tabla."""
    corrida = {k: v for k, v in (datos.get("corrida") or {}).items()
               if k in ("ventana", "cobertura_dias", "min_piezas", "ganador_desde", "dejar_en_bodega")}
    return json.dumps({"tiendas": sorted((datos.get("tiendas") or {}).keys()), "corrida": corrida},
                      ensure_ascii=False, sort_keys=True, default=str)


def corriendo_en(clave: str) -> dict[str, Any] | None:
    """El turno que está corriendo en el chat de esa semana, si hay."""
    # Una copia: la pantalla pregunta desde el event loop mientras los hilos agregan turnos.
    for tid, t in list(_trabajos.items()):
        if t["estado"] == "corriendo" and t["semana"] == clave:
            return {"id": tid, "quien": t["quien"], "instruccion": t["instrucciones"], "modelo": t["modelo"],
                    "segundos": round(time.time() - t["inicio"])}
    return None


def iniciar(cuerpo: dict[str, Any], quien: str = "", ahora: datetime | None = None) -> dict[str, Any]:
    """
    Arranca un turno del chat de la SEMANA EN CURSO en un hilo y devuelve su id.
    El cuerpo es `{semana, datos, plan, instrucciones, modelo}`: la planeación de hoy
    (se le da a la IA una vez al día), el plan de este momento y lo que pide la persona.
    """
    if not disponible():
        return {"ok": False, "motivo": "La IA no está configurada en este ambiente (falta DEEPSEEK_API_KEY)."}
    modelo = str(cuerpo.get("modelo") or "") or modelo_por_omision()
    if modelo not in MODELOS:
        return {"ok": False, "motivo": f"No conozco el modelo «{modelo}»."}
    if not _hay_llave(MODELOS[modelo]["proveedor"]):
        return {"ok": False, "motivo": f"{MODELOS[modelo]['nombre']} no está configurado en este ambiente."}
    semana = fsem.semana_de(ahora)
    clave = str(cuerpo.get("semana") or semana["clave"])
    if clave != semana["clave"]:
        return {"ok": False, "motivo": f"Sólo se planea la semana en curso ({semana['semana']} · {semana['rango']})."}
    datos = cuerpo.get("datos") if isinstance(cuerpo.get("datos"), dict) else {}
    tiendas = datos.get("tiendas") or {}
    if not any((d.get("renglones") or d.get("filas") or d.get("ganadores_agotados")) for d in tiendas.values()):
        return {"ok": False, "motivo": "La planeación no trae renglones que revisar."}
    instrucciones = str(cuerpo.get("instrucciones") or "").strip()[:MAX_INSTRUCCION]
    plan = plan_actual(cuerpo.get("plan"))
    with _candado:
        _podar()
        ya = corriendo_en(clave)
        if ya:
            return {"ok": False, "id": ya["id"],
                    "motivo": f"Ya hay un turno corriendo en el chat de la {semana['semana']}"
                              f" ({(ya['quien'] or '?').split('@')[0]}): espera a que termine."}
        if sum(1 for v in _trabajos.values() if v["estado"] == "corriendo") >= MAX_TRABAJOS:
            return {"ok": False, "motivo": "Ya hay turnos de IA corriendo; espera a que terminen."}
        tid = uuid.uuid4().hex[:12]
        _trabajos[tid] = {"estado": "corriendo", "inicio": time.time(), "quien": quien, "semana": clave,
                          "instrucciones": instrucciones, "modelo": modelo, "fase": "leyendo",
                          "razonamiento_chars": 0, "parcial": None}

    def correr() -> None:
        t = _trabajos[tid]
        registro: dict[str, Any] = {"id": tid, "semana": clave, "instruccion": instrucciones, "modelo": modelo,
                                    "modelo_nombre": MODELOS[modelo]["nombre"]}
        try:
            conv = fsem.conversacion(clave)
            base = conv["datos"]
            hoy = fsem.dia_cdmx(ahora)
            # La planeación se le da a la IA UNA vez al día (y otra si cambian las tiendas o
            # los parámetros): con la misma, DeepSeek relee de su caché todo lo anterior.
            if base and base.get("dia") == hoy and base.get("huella") == _huella(datos) and base.get("datos"):
                tabla, datos_id = base["datos"], base["id"]
            else:
                tabla = datos
                datos_id = fsem.guardar(clave, "datos", {"dia": hoy, "huella": _huella(datos), "datos": datos,
                                                         "tiendas": sorted(tiendas)}, quien, sufijo=hoy)
            turnos = conv["turnos"][-MAX_TURNOS:]
            mensaje = mensaje_turno(instrucciones, plan)

            def avance(texto: str, razon: int) -> None:
                t["razonamiento_chars"] = razon
                t["fase"] = "escribiendo" if texto else "pensando"
                if texto:
                    t["parcial"] = parcial(texto, tabla)

            r = _llamar(conversacion_openai(tabla, turnos, mensaje), modelo, avance)
            revisado = validar(r["respuesta"], tabla)
            resultado = {**revisado, "modelo": r["modelo"], "modelo_id": modelo,
                         "modelo_nombre": MODELOS[modelo]["nombre"], "tokens": r["tokens"],
                         "costo_usd": costo_usd(modelo, r["tokens"], datetime.now(timezone.utc)),
                         "datos_nuevos": base is None or base.get("id") != datos_id}
            registro.update(estado="listo", mensaje=mensaje, salida=r["texto"][:MAX_SALIDA], datos_id=datos_id,
                            resultado=resultado, segundos=round(time.time() - t["inicio"]))
            t.update(estado="listo", resultado=resultado)
        except Exception as exc:  # noqa: BLE001 — el error se enseña, no se esconde
            log.warning("planeación IA: falló (%s)", exc)
            registro.update(estado="error", error=str(exc)[:400], segundos=round(time.time() - t["inicio"]))
            t.update(estado="error", error=str(exc)[:400])
        finally:
            t["fin"] = time.time()
            try:
                fsem.guardar(clave, "turno", registro, quien, sufijo=tid)
            except Exception as exc:  # noqa: BLE001 — sin bitácora el turno se ve, pero no sobrevive a recargar
                log.warning("planeación IA: el turno %s no se guardó: %s", tid, exc)
                t["sin_guardar"] = True

    threading.Thread(target=correr, name=f"planeacion-ia-{tid}", daemon=True).start()
    log.info("planeación IA: %s arrancó con %s (%s, %s%s)", tid, modelo, (quien or "?").split("@")[0], clave,
             f", «{instrucciones[:80]}»" if instrucciones else "")
    return {"ok": True, "id": tid}


def estado(tid: str) -> dict[str, Any]:
    t = _trabajos.get(tid)
    if not t:
        return {"estado": "desconocido", "motivo": "No existe ese turno (el servidor pudo reiniciarse)."}
    salida: dict[str, Any] = {"estado": t["estado"], "semana": t["semana"],
                              "segundos": round((t.get("fin") or time.time()) - t["inicio"]),
                              "fase": t.get("fase"), "razonamiento": round(t.get("razonamiento_chars", 0) / 3.5)}
    if t["estado"] == "corriendo" and t.get("parcial"):
        salida["parcial"] = t["parcial"]
    if t["estado"] == "listo":
        salida["resultado"] = t["resultado"]
    if t["estado"] == "error":
        salida["motivo"] = t.get("error")
    if t.get("sin_guardar"):
        salida["sin_guardar"] = True
    return salida
