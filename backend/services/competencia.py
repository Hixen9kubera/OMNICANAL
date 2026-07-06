"""
competencia.py — Precio de competencia sugerido (SOLO para mostrar).

Estudio de producto → botón "Mejorar con IA". Se busca el producto por nombre +
marca + modelo en los marketplaces y una IA (DeepSeek/Claude) sintetiza un
precio de competencia sugerido. No cambia ningún campo; si el usuario lo quiere
usar, edita el precio a mano.

Opción A (por defecto): se ARMA la lista de competencia con datos reales —
  • Mercado Libre: API pública (api.mercadolibre.com/sites/MLM/search)
  • Amazon/Walmart/Temu/TikTok: SerpApi (google_shopping, gl=mx)
  y esa lista se le pasa a la IA para que filtre y sugiera el precio.

Plan B (con_lista=False): no se arma lista; se le pide a la IA que busque en todos
los marketplaces con su conocimiento y elija el precio más adecuado (menos preciso,
sin datos en vivo — DeepSeek no navega la web).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from config import settings
from services import ia_generadores

log = logging.getLogger("omnicanal.competencia")

_ML_SEARCH = "https://api.mercadolibre.com/sites/MLM/search"
_SERPAPI = "https://serpapi.com/search.json"

_PROMPT_CON_LISTA = (
    "Eres analista de precios de e-commerce en México. Te doy un PRODUCTO y una "
    "lista de PUBLICACIONES DE COMPETENCIA obtenidas en vivo (marketplace, título, "
    "precio en MXN, url).\n\n"
    "Tarea:\n"
    "1. Descarta las que NO sean el mismo producto o uno muy similar.\n"
    "2. Calcula el rango por marketplace (mín, mediana, máx).\n"
    "3. Sugiere UN 'precio de competencia' en MXN para Mercado Libre: competitivo "
    "pero rentable (considera el costo del producto si se indica).\n"
    "4. Explica en 1-2 líneas el porqué.\n\n"
    "REGLAS: usa SOLO los precios provistos, NO inventes. Si no hay datos "
    "suficientes, precio_sugerido = null y explica por qué.\n\n"
    "Responde SOLO en JSON válido (NO repitas la lista de fuentes):\n"
    '{"precio_sugerido": <MXN o null>, "moneda": "MXN", '
    '"rango": {"min": <n>, "max": <n>, "mediana": <n>}, '
    '"por_marketplace": [{"marketplace": "..", "min": <n>, "max": <n>, "n": <int>}], '
    '"razonamiento": ".."}'
)

_PROMPT_SIN_LISTA = (
    "Eres analista de precios de e-commerce en México con amplio conocimiento de "
    "Mercado Libre, Amazon, Walmart, Temu y TikTok Shop en México. Te doy un "
    "PRODUCTO. Con tu conocimiento del mercado mexicano, estima el precio típico "
    "de venta de este producto (o uno muy similar) en cada marketplace y sugiere "
    "el 'precio de competencia' más adecuado para Mercado Libre: competitivo pero "
    "rentable (considera el costo si se indica).\n\n"
    "IMPORTANTE: es una ESTIMACIÓN sin datos en vivo; si no tienes suficiente "
    "certeza, indícalo en el razonamiento y da un rango amplio.\n\n"
    "Responde SOLO en JSON válido:\n"
    '{"precio_sugerido": <MXN o null>, "moneda": "MXN", '
    '"rango": {"min": <n>, "max": <n>, "mediana": <n>}, '
    '"por_marketplace": [{"marketplace": "..", "estimado_min": <n>, "estimado_max": <n>}], '
    '"razonamiento": "..", "aviso": "estimación sin datos en vivo"}'
)


def _query(producto: dict[str, Any]) -> str:
    """
    Búsqueda GENÉRICA para hallar competencia (NO usa la marca/modelo propios,
    que son únicos de Kubera y no los tiene ningún competidor).
    """
    titulo = (producto.get("nombre") or "").strip()
    q = titulo
    marca = (producto.get("marca") or "").strip()
    if marca and len(marca) > 2 and marca.lower() in q.lower():
        q = re.sub(re.escape(marca), "", q, flags=re.IGNORECASE)
    palabras = [w for w in re.split(r"\s+", q) if len(w) > 2][:8]
    return " ".join(palabras).strip() or titulo


def _buscar_ml(query: str, limite: int = 15) -> list[dict[str, Any]]:
    try:
        r = requests.get(_ML_SEARCH, params={"q": query, "limit": limite}, timeout=15)
        if r.status_code != 200:
            log.info("ML search %s → HTTP %s", query, r.status_code)
            return []
        out = []
        for it in r.json().get("results", [])[:limite]:
            out.append({
                "marketplace": "mercado_libre",
                "titulo": it.get("title"),
                "precio": it.get("price"),
                "url": it.get("permalink"),
            })
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("_buscar_ml(%s): %s", query, exc)
        return []


def _buscar_serpapi(query: str, limite: int = 15) -> list[dict[str, Any]]:
    if not settings.serpapi_key:
        return []
    try:
        r = requests.get(_SERPAPI, params={
            "engine": "google_shopping", "q": query,
            "gl": "mx", "hl": "es", "api_key": settings.serpapi_key,
        }, timeout=25)
        if r.status_code != 200:
            log.info("SerpApi %s → HTTP %s", query, r.status_code)
            return []
        out = []
        for it in r.json().get("shopping_results", [])[:limite]:
            out.append({
                "marketplace": (it.get("source") or "google_shopping"),
                "titulo": it.get("title"),
                "precio": it.get("extracted_price"),
                "url": it.get("product_link") or it.get("link"),
            })
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("_buscar_serpapi(%s): %s", query, exc)
        return []


def _parse_json(texto: str) -> dict[str, Any]:
    """Extrae el primer objeto JSON del texto (tolera ```json ... ```)."""
    t = texto.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
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


def precio_competencia(producto: dict[str, Any], con_lista: bool = True) -> dict[str, Any]:
    """Devuelve el precio de competencia sugerido (solo para mostrar)."""
    query = _query(producto)
    fuentes: list[dict[str, Any]] = []
    if con_lista:
        # SerpApi (google_shopping, gl=mx) agrega ML, Amazon, Walmart, etc.
        # La API pública de ML dejó de permitir búsqueda anónima (403).
        fuentes = _buscar_serpapi(query, limite=25)
        fuentes = [f for f in fuentes if f.get("precio")]  # con precio válido

    if con_lista and fuentes:
        system = _PROMPT_CON_LISTA
        # Sin URLs para no inflar el prompt (las URLs se guardan aparte y se
        # devuelven al frontend para mostrar los enlaces).
        lista = "\n".join(
            f"- [{f['marketplace']}] {f.get('titulo','')} — ${f.get('precio')}"
            for f in fuentes
        )
        user = (
            f"PRODUCTO: {producto.get('nombre','')}\n"
            f"Categoría: {producto.get('categoria','')}\n"
            f"Costo (MXN): {producto.get('costo','n/d')}\n\n"
            f"COMPETENCIA (búsqueda: '{query}'):\n{lista}"
        )
    else:
        system = _PROMPT_SIN_LISTA
        user = (
            f"PRODUCTO: {producto.get('nombre','')}\n"
            f"Categoría: {producto.get('categoria','')}\n"
            f"Marca: {producto.get('marca','')} | Modelo: {producto.get('modelo','')}\n"
            f"Costo (MXN): {producto.get('costo','n/d')}"
        )

    res = ia_generadores._completar(system, user, max_tokens=1300)
    if not res.get("ok"):
        return {"ok": False, "motivo": res.get("motivo"), "query": query,
                "fuentes_encontradas": len(fuentes)}
    data = _parse_json(res.get("texto", ""))
    if not data:
        return {"ok": False, "motivo": "La IA no devolvió un JSON válido.",
                "query": query, "crudo": res.get("texto", "")[:400]}
    return {
        "ok": True,
        "con_lista": bool(con_lista and fuentes),
        "query": query,
        "proveedor": res.get("proveedor"),
        "fuentes_encontradas": len(fuentes),
        # Fuentes reales (con URL) para mostrar los enlaces en el frontend.
        "fuentes": fuentes[:12],
        **data,
    }
