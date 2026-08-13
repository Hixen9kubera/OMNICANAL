"""
competencia_terminos.py — El "término general" de cada SKU, propuesto por IA.

El módulo mide dos competencias distintas, y la diferencia es el punto:

  • TÉRMINO GENERAL — "lona para exterior". Es la búsqueda amplia con la que un
    comprador descubre la categoría. Aquí compites por VISIBILIDAD contra todo
    el mundo, y tu posición dice si existes o no para ese comprador.
  • TÍTULO COMPLETO — "Lona Sombra Reforzada 4x6m Para Exterior Protección Uv".
    Aquí compites de FRENTE contra el mismo producto.

El término general no se puede derivar del título con reglas (quitar palabras no
da "lona para exterior" a partir de "Lona Sombra Reforzada 4x6m Protección Uv
Beige"): hace falta entender el producto. Por eso lo propone un LLM.

La propuesta es solo eso: una propuesta. En cuanto una persona lo corrige, el
store marca `termino_origen='manual'` y ninguna corrida posterior lo vuelve a
pisar. Ver `competencia_store.guardar_skus`.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from services import ia_generadores

log = logging.getLogger("omnicanal.competencia.terminos")

_SYSTEM = (
    "Eres experto en búsqueda de Mercado Libre México. Te doy productos con su "
    "título y categoría. Para cada uno, devuelve el TÉRMINO GENERAL de búsqueda: "
    "las 2 a 4 palabras que teclearía un comprador que NO conoce la marca ni el "
    "modelo y solo busca este tipo de producto.\n\n"
    "REGLAS:\n"
    "- Sin marca, sin modelo, sin medidas, sin color, sin código.\n"
    "- En español de México, minúsculas, singular o plural según se busque de verdad.\n"
    "- Debe ser un término con volumen real de búsqueda, no una descripción.\n"
    "- Nada de palabras de relleno ('para', 'de') si no hacen falta.\n\n"
    "EJEMPLOS:\n"
    "'Lona Sombra Reforzada 4x6m Para Exterior Protección Uv Beige' → 'lona para exterior'\n"
    "'Tapetes Para Auto con Glitter Morado 4pz' → 'tapetes para auto'\n"
    "'Kit 5 Bujias Ngk Iridium Vw Jetta Mk6 Beetle Bora' → 'bujias iridium'\n"
    "'Cartucho Turbo Para K03 1.8 De 12 Propelas Lado Admision' → 'cartucho turbo'\n\n"
    "Responde SOLO un JSON: {\"terminos\": [{\"sku\": \"...\", \"termino\": \"...\"}]}"
)


def _parse_json(texto: str) -> dict[str, Any]:
    t = re.sub(r"^```(?:json)?|```$", "", texto.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                pass
    return {}


def _limpiar(termino: str) -> str:
    """Poda lo que el LLM a veces deja pasar: comillas, medidas, exceso de palabras."""
    t = re.sub(r"[\"']", "", termino or "").strip().lower()
    t = re.sub(r"\b\d+\s*(x\s*\d+)?\s*(m|cm|mm|kg|g|pz|pzs|piezas|lts?|ml)\b", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return " ".join(t.split()[:5])


def proponer(productos: list[dict[str, Any]]) -> dict[str, str]:
    """
    { sku: termino_general } para una lista de {sku, nombre, categoria_nombre}.

    Si la IA no está disponible o responde mal, devuelve {} — el llamador decide
    qué hacer. NO se inventa un término con reglas: un término malo mide la
    competencia equivocada, y eso es peor que no medir.
    """
    pendientes = [p for p in productos if p.get("sku") and p.get("nombre")]
    if not pendientes:
        return {}

    lista = "\n".join(
        f"- {p['sku']}: {p['nombre']}"
        + (f"  [categoría: {p['categoria_nombre']}]" if p.get("categoria_nombre") else "")
        for p in pendientes
    )
    res = ia_generadores._completar(_SYSTEM, f"PRODUCTOS:\n{lista}", max_tokens=1200)
    if not res.get("ok"):
        log.warning("La IA no propuso términos: %s", res.get("motivo"))
        return {}

    datos = _parse_json(res.get("texto", ""))

    # El modelo NORMALIZA el SKU al repetirlo, y si el nuestro trae comillas o
    # espacios devuelve otra cadena: `TEC-1284-NEG-27"` volvió como
    # `TEC-1284-NEG-27` y el cruce exacto lo descartó en silencio — un monitor
    # gamer se quedó sin término por una comilla. Se reconcilia por forma
    # normalizada antes de darlo por perdido.
    def _clave(s: str) -> str:
        return "".join(c for c in (s or "").upper() if c.isalnum())

    por_clave = {_clave(p["sku"]): p["sku"] for p in pendientes}
    out: dict[str, str] = {}
    for t in datos.get("terminos") or []:
        sku, termino = t.get("sku"), _limpiar(t.get("termino", ""))
        if not (sku and termino):
            continue
        real = sku if any(p["sku"] == sku for p in pendientes) else por_clave.get(_clave(sku))
        if real:
            out[real] = termino
        else:
            log.warning("La IA devolvió un SKU que no pedimos: %r", sku)

    faltan = {p["sku"] for p in pendientes} - set(out)
    if faltan:
        log.warning("Sin término general para %s SKUs: %s", len(faltan), sorted(faltan))
    return out
