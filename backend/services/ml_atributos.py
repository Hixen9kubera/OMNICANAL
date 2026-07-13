"""
ml_atributos.py — Atributos de Mercado Libre por categoría (principales + secundarios).

Portado del pipeline canónico (KuberaPipeline/pipeline/atributos_ia.py):
  1. Consulta los atributos REALES de la categoría de ML
     (GET /categories/{id}/attributes), filtra hidden/read_only + SKIP_IDS y los
     separa en PRINCIPALES (required / catalog_required) y SECUNDARIOS, con sus
     valores válidos.
  2. Arma el prompt exacto (obligatorios + opcionales con valores válidos + 12
     reglas de inferencia) y llama a DeepSeek (json_object, temp 0.2, retry 429).
  3. Valida la salida: solo IDs válidos (+ BRAND/MODEL), fuerza BRAND, calcula
     atributos_str / validez.

Lo usan: crear_producto (paso de atributos) e ia_generadores.mejorar (canal ML).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

import httpx

from config import settings

log = logging.getLogger("omnicanal.ml_atributos")

MARCA = "Ferrahome"
MAX_SECUNDARIAS = 15

_ML_API = "https://api.mercadolibre.com"

# Atributos que NO pide llenar la IA (se gestionan aparte: marca, GTIN, dims, origen…).
_SKIP_IDS = {
    "BRAND", "MODEL", "SELLER_SKU", "GTIN", "EMPTY_GTIN_REASON",
    "SELLER_PACKAGE_WEIGHT", "SELLER_PACKAGE_LENGTH",
    "SELLER_PACKAGE_WIDTH", "SELLER_PACKAGE_HEIGHT",
    "ORIGIN", "OEM",
}
_ATTRS_BASICOS_RE = ("peso", "dimen", "medida", "talla", "tamaño", "marca", "brand",
                     "variante", "variant")
_ATTRS_EXCLUIDOS = {"url_alibaba", "alibaba_price", "alibaba_title_original",
                    "ml_category_id", "categoria_meli_id"}


def _format_atributos(atributos_dict: dict) -> str:
    return " | ".join(f"{k}: {v}" for k, v in atributos_dict.items() if v)


def _calc_atributos_validos_str(atributos_str: str) -> str:
    if not atributos_str or not atributos_str.strip():
        return "NO"
    parts = [p.strip() for p in atributos_str.split("|") if ":" in p.strip()]
    if not parts:
        return "NO"
    pairs = []
    for p in parts:
        name, _, val = p.partition(":")
        n, v = name.strip(), val.strip()
        if n.lower().replace(" ", "_") in _ATTRS_EXCLUIDOS:
            continue
        if not v:
            return "NO"
        pairs.append((n, v))
    if not pairs:
        return "NO"
    extra = [n for n, v in pairs if not any(k in n.lower() for k in _ATTRS_BASICOS_RE)]
    return "SI" if extra else "NO"


# ── Atributos de la categoría ML (cache) ───────────────────────────────────────
_cat_cache: dict[str, dict] = {}
_cat_lock = asyncio.Lock()


async def get_meli_all_attributes(cat_id: str) -> dict:
    """{'principales': [...], 'secundarias': [...]}. Cada uno: {id,name,value_type,valid_values}."""
    if not cat_id:
        return {"principales": [], "secundarias": []}
    if cat_id in _cat_cache:
        return _cat_cache[cat_id]
    async with _cat_lock:
        if cat_id in _cat_cache:
            return _cat_cache[cat_id]
        try:
            async with httpx.AsyncClient(timeout=15.0) as cli:
                r = await cli.get(f"{_ML_API}/categories/{cat_id}/attributes")
            r.raise_for_status()
            principales, secundarias = [], []
            for a in r.json():
                tags = a.get("tags", {}) or {}
                if tags.get("hidden") or tags.get("read_only"):
                    continue
                if a["id"] in _SKIP_IDS:
                    continue
                entry = {
                    "id": a["id"],
                    "name": a["name"],
                    "value_type": a.get("value_type", "string"),
                    "valid_values": [v["name"] for v in a.get("values", []) if v.get("name")],
                }
                if tags.get("required") or tags.get("catalog_required"):
                    principales.append(entry)
                else:
                    secundarias.append(entry)
            result = {"principales": principales, "secundarias": secundarias}
            _cat_cache[cat_id] = result
            return result
        except Exception as e:  # noqa: BLE001
            log.warning("atributos MeLi %s: %s", cat_id, e)
            return {"principales": [], "secundarias": []}


def _fmt_attr_list(attrs: list, label: str) -> str:
    if not attrs:
        return f"{label}: (ninguno)\n"
    lines = f"{label}:\n"
    for a in attrs:
        line = f"  - {a['id']} ({a['name']}, tipo: {a['value_type']})"
        if a["valid_values"]:
            vals = ", ".join(a["valid_values"][:15])
            if len(a["valid_values"]) > 15:
                vals += f" ... ({len(a['valid_values'])} opciones total)"
            line += f"\n    Valores válidos: {vals}"
        lines += line + "\n"
    return lines


def build_prompt(nombre, alibaba_titulo, atributos_actuales, caracteristicas_clave,
                 meli_attrs, sku: str = "") -> str:
    secundarias = meli_attrs.get("secundarias", [])[:MAX_SECUNDARIAS]
    principales_str = _fmt_attr_list(
        meli_attrs.get("principales", []),
        "ATRIBUTOS OBLIGATORIOS — debes llenarlos TODOS",
    )
    secundarias_str = _fmt_attr_list(
        secundarias,
        "ATRIBUTOS OPCIONALES — llena TODOS los que puedas, sé proactivo en inferir",
    )
    return f"""Eres un experto en comercio electronico para Mexico (MercadoLibre).
Tu tarea es generar el MAYOR NUMERO POSIBLE de atributos para publicar un producto.
DEBES INTENTAR LLENAR CADA ATRIBUTO. Solo omite si es absolutamente imposible determinarlo.

## Producto
- SKU: {sku or 'N/A'}
- Nombre en tienda: {nombre}
- Titulo de Alibaba (extrae datos de aqui): {alibaba_titulo or 'N/A'}

## Atributos actuales en WooCommerce (base, respeta los correctos)
{atributos_actuales or 'Sin atributos'}

## Caracteristicas de Alibaba (extrae TODOS los datos posibles)
{caracteristicas_clave or 'N/A'}

## {principales_str}
## {secundarias_str}

## REGLAS DE INFERENCIA (aplica en este orden)
1. USA EL ID del atributo como clave JSON (ej: "COLOR" no "Color"; "BATTERY_TYPE" no "Tipo de bateria")
2. BRAND: siempre "{MARCA}" — nunca la del proveedor
3. MODEL: extrae del titulo Alibaba. Si no hay, genera uno corto logico (ej: FH-BT24V, FH-LED50W)
4. Atributos con valores validos: elige el MAS LOGICO para el tipo de producto
5. Texto libre: usa datos de caracteristicas/titulo. Estima con logica si no hay dato exacto
   - Capacidades: "280Ah-314Ah" -> usa "280 Ah"; rangos -> usa el valor minimo
   - Voltaje Mexico: "Multi voltage" o "100-240V" -> usa "120 V"
   - Potencia: si viene en W, mantener con unidad (ej: "200 W")
6. UNITS_PER_PACK / PACKS_NUMBER: si se vende por unidad -> "1"
7. SALE_FORMAT: si es producto individual -> "Unidad"
8. COLOR desde el SKU: NEG=Negro, BLN=Blanco, ROJ=Rojo, AZU=Azul, VER=Verde,
   NAR=Naranja, GRI=Gris, MOR=Morado, AMR=Amarillo, PLA=Plata, ORO=Dorado, MUL=Multicolor,
   HIT=Multicolor, ROS=Rosa, LIL=Lila, CAF=Cafe, BEI=Beige, MET=Plateado
9. ORIGIN: productos Alibaba/China -> "China"
10. IS_WIRELESS, IS_RECHARGEABLE, WITH_LED_LIGHT, etc.: infiere SI/NO del contexto
11. Dimensiones: si vienen en titulo o caracteristicas, extraelas (convierte a cm si es necesario)
12. EXCLUIR SOLO: codigos OEM especificos del proveedor, datos de fabricacion interna, MOQ

## RESTRICCION ABSOLUTA
- Las UNICAS claves permitidas en "atributos" son los IDs listados arriba + "BRAND" y "MODEL"
- PROHIBIDO inventar claves nuevas
- Valores en ESPAÑOL con ortografia EXACTA a los valores validos listados
- Usa flags SOLO para IDs que sea absolutamente imposible determinar

## SALIDA — devuelve SOLO este JSON:
{{
  "atributos": {{
    "BRAND": "{MARCA}",
    "MODEL": "FH-BT24V",
    "COLOR": "Negro"
  }},
  "flags": ["ID_ATRIBUTO: razon por la que no se pudo determinar"]
}}"""


# ── DeepSeek (json_object, temp 0.2, retry 429) ────────────────────────────────
def _parse_json(texto: str) -> dict:
    t = re.sub(r"^```(?:json)?|```$", "", (texto or "").strip(), flags=re.MULTILINE).strip()
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


async def _deepseek_json(system: str, user: str, max_tokens: int = 4096) -> dict:
    """Llama a DeepSeek pidiendo JSON. Si no hay clave DeepSeek, cae a _completar (Claude)."""
    if not settings.deepseek_api_key:
        from services.ia_generadores import _completar
        r = await asyncio.to_thread(_completar, system, user, max_tokens)
        return _parse_json(r.get("texto", "")) if r.get("ok") else {}

    backoff = [10, 20, 10]
    intento = 0
    async with httpx.AsyncClient(timeout=120.0) as cli:
        while True:
            r = await cli.post(
                f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                json={
                    "model": settings.deepseek_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                    "max_tokens": max_tokens,
                },
            )
            if r.status_code == 429 and intento < len(backoff):
                await asyncio.sleep(backoff[intento])
                intento += 1
                continue
            r.raise_for_status()
            return json.loads(r.json()["choices"][0]["message"]["content"])


# ── Orquestador ────────────────────────────────────────────────────────────────
async def generar_atributos(
    cat_id: Optional[str],
    nombre: str,
    alibaba_titulo: str = "",
    atributos_actuales: str = "",
    caracteristicas_clave: str = "",
    sku: str = "",
) -> dict[str, Any]:
    """
    Devuelve:
      { "atributos": {ID: valor}, "flags": [...], "atributos_str": str,
        "num": int, "validos": bool, "meli_attrs": {principales, secundarias} }
    """
    meli_attrs = await get_meli_all_attributes(cat_id) if cat_id else {"principales": [], "secundarias": []}
    system = "Eres un experto en e-commerce para Mexico. Respondes siempre con JSON valido."
    user = build_prompt(nombre, alibaba_titulo, atributos_actuales, caracteristicas_clave, meli_attrs, sku)

    try:
        result = await _deepseek_json(system, user)
    except Exception as e:  # noqa: BLE001
        log.warning("generar_atributos %s: %s", sku or "?", e)
        result = {}

    atributos_raw = result.get("atributos", {}) or {}
    flags = result.get("flags", []) or []

    todos = meli_attrs.get("principales", []) + meli_attrs.get("secundarias", [])
    ids_validos = {a["id"] for a in todos} | {"BRAND", "MODEL"}
    atributos = {k: str(v) for k, v in atributos_raw.items() if k in ids_validos and v}
    atributos["BRAND"] = MARCA  # forzar marca

    atributos_str = _format_atributos(atributos)
    return {
        "atributos": atributos,
        "flags": flags,
        "atributos_str": atributos_str,
        "num": len(atributos),
        "validos": _calc_atributos_validos_str(atributos_str) == "SI",
        "meli_attrs": meli_attrs,
    }
