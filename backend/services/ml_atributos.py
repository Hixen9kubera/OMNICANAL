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
  4. La REVISA sin IA (`depurar`, 23-oct-2026): lo que Mercado Libre rechazaría no
     sale de aquí. Ver «LO QUE ML RECHAZA» abajo.

Lo usan: crear_producto (paso de atributos) e ia_generadores.mejorar (canal ML).

LO QUE ML RECHAZA Y LA IA PROPONÍA (Brandon, 23-oct-2026: «revisa la mejora con IA para
que estos errores no se produzcan»). Medido en los rechazos de producción del 23-sep al
22-oct: 156 medidas en cero y 61 atributos obligatorios faltantes. Dos casos del 22-oct:
  · TEC-1439-BLN, cortadora MANUAL: «Potencia» = 0 W → ML exige > 0. Una herramienta sin
    motor no tiene potencia: el atributo se OMITE, no se pone en cero.
  · ACC-0446-MUL, «Moños para Cabello Niña Bebé»: Género = Mujer → ML rechaza porque el
    título dice «niña» (`invalid.title.gender`); el pipeline quitó el Género, reintentó y
    ML contestó que faltaba. El Género tiene que decir lo mismo que el título, y es UNA
    sola opción (la lista de la categoría no admite «Mujer y Niñas»).
Antes solo viajaban 15 secundarios: los demás la IA ni los veía. Ahora van todos.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from typing import Any, Optional

import httpx

from config import settings

log = logging.getLogger("omnicanal.ml_atributos")

MARCA = "Ferrahome"
# Cuántos secundarios ve la IA. Eran 15 y en categorías de 60 la IA no veía la mayoría
# (Brandon, 23-oct: «al igual con los atributos secundarios»). Tope solo de seguridad.
MAX_SECUNDARIAS = 80
# Valores válidos que se le enseñan por atributo (las listas cerradas largas se cortan).
MAX_VALORES = 30

_ML_API = "https://api.mercadolibre.com"

# Atributos que NO pide llenar la IA (se gestionan aparte: marca, GTIN, dims, origen…).
_SKIP_IDS = {
    "BRAND", "MODEL", "SELLER_SKU", "GTIN", "EMPTY_GTIN_REASON",
    "SELLER_PACKAGE_WEIGHT", "SELLER_PACKAGE_LENGTH",
    "SELLER_PACKAGE_WIDTH", "SELLER_PACKAGE_HEIGHT",
    "ORIGIN", "OEM",
}

# DATOS FISCALES: NO SE LLENAN NI SE TOCAN (Brandon, 28-oct-2026). Mercado Libre los guarda como atributos de la
# publicación (jerarquía ITEM, grupo OTHERS) y los enseña en la ficha bajo «Otros». Desde que la IA ve TODOS los
# secundarios (23-oct) los llenaba sola: MUE-0080-NEG salió con IVA 16 %, IEPS 0 % y las claves SAT en «No existe en
# el catálogo». Aquí no los ve la IA, no salen en el alta ni en la actualización y no aparecen en el editor de
# Bodega. Lo que ya tienen las publicaciones y las metas `ml_attr_*` de Woo se queda como está: no se borra.
FISCALES = frozenset({
    "INVOICE_PRODUCT_NAME", "SAT_KEY", "MEASURE_UNIT_KEY", "MEASURE_UNIT_DESCRIPTION", "IVA_FOR_RESALE", "IEPS",
    "IMPORT_DECLARATION_NUMBER", "VALUE_ADDED_TAX", "IMPORT_DUTY",
})
# Sus nombres en ML (sin acentos ni mayúsculas): un atributo de Woo o del Estudio puede venir con la etiqueta.
_FISCALES_NOMBRES = frozenset({
    "nombre del producto en la factura", "clave del producto", "clave de la unidad de medida", "unidad de medida",
    "iva para reventa", "ieps", "numero de pedimento", "iva", "impuesto interno",
})


def es_fiscal(llave: Any) -> bool:
    """¿Es un dato fiscal de ML? Por su ID (`IEPS`, `ml_attr_SAT_KEY`) o por su nombre («IVA para reventa»)."""
    t = str(llave or "").strip()
    if t.lower().startswith("ml_attr_"):
        t = t[len("ml_attr_"):]
    if t.upper() in FISCALES:
        return True
    plano = unicodedata.normalize("NFKD", t.lower())
    plano = " ".join("".join(c for c in plano if not unicodedata.combining(c)).replace("_", " ").split())
    return plano in _FISCALES_NOMBRES


_ATTRS_BASICOS_RE =("peso", "dimen", "medida", "talla", "tamaño", "marca", "brand",
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
def clasificar(crudos: list) -> dict:
    """La respuesta de `GET /categories/{id}/attributes` en {principales, secundarias}.
    Pura. La usan la IA (`get_meli_all_attributes`) y el publicador
    (`publicar_ready._atributos_ml_para_alta`), para que los dos revisen igual."""
    principales, secundarias = [], []
    for a in crudos or []:
        if not isinstance(a, dict) or not a.get("id"):
            continue
        tags = a.get("tags", {}) or {}
        if tags.get("hidden") or tags.get("read_only"):
            continue
        if a["id"] in _SKIP_IDS or es_fiscal(a["id"]):
            continue
        obligatorio =bool(tags.get("required") or tags.get("catalog_required"))
        entry = {
            "id": a["id"],
            "name": a.get("name") or a["id"],
            "value_type": a.get("value_type", "string"),
            "valid_values": [v["name"] for v in a.get("values", []) if v.get("name")],
            "obligatorio": obligatorio,
            "varios": bool(tags.get("multivalued")),
            "unidades": [u.get("id") for u in (a.get("allowed_units") or []) if u.get("id")],
        }
        (principales if obligatorio else secundarias).append(entry)
    return {"principales": principales, "secundarias": secundarias}


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
            result = clasificar(r.json())
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
        notas = []
        if a.get("value_type") == "list":
            notas.append("lista cerrada: SOLO un valor de la lista")
        if a.get("varios"):
            notas.append("admite varios valores")
        if a.get("unidades"):
            notas.append("unidades: " + ", ".join(a["unidades"][:6]))
        extra = f"; {'; '.join(notas)}" if notas else ""
        line = f"  - {a['id']} ({a['name']}, tipo: {a['value_type']}{extra})"
        if a["valid_values"]:
            vals = ", ".join(a["valid_values"][:MAX_VALORES])
            if len(a["valid_values"]) > MAX_VALORES:
                vals += f" ... ({len(a['valid_values'])} opciones total)"
            line += f"\n    Valores válidos: {vals}"
        lines += line + "\n"
    return lines


def build_prompt(nombre, alibaba_titulo, atributos_actuales, caracteristicas_clave,
                 meli_attrs, sku: str = "", variante: str = "") -> str:
    # `variante` = el bloque de `ia_variante.bloque`: si el SKU es una variante,
    # los atributos deben llevar SU valor (Cantidad = 3 piezas), no la lista de
    # la familia que traen los atributos de Woo del padre.
    bloque_variante = f"\n## Variante\n{variante}\n" if variante else ""
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
{bloque_variante}
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
13. GENDER (Genero): UNA sola opcion de la lista, y debe decir lo mismo que el TITULO.
    Si el titulo dice nina/ninas -> "Niñas"; nino/ninos -> "Niños"; bebe -> "Bebés";
    mujer/dama -> "Mujer"; hombre/caballero -> "Hombre". Si no dice ninguna, el publico
    real del producto ("Sin género" o, si es para ninos, "Sin género infantil").
    NUNCA dos valores juntos ("Mujer y Niñas" no existe en Mercado Libre).
14. NUMEROS CON UNIDAD (potencia, peso, voltaje, capacidad, diametro...): si el producto
    NO tiene ese dato (p. ej. potencia de una herramienta manual, voltaje de algo sin
    electricidad), OMITE el atributo. PROHIBIDO poner 0: Mercado Libre rechaza cualquier
    valor que no sea mayor que cero.
15. LISTA CERRADA: copia el valor EXACTO de "Valores válidos", uno solo salvo que el
    atributo diga "admite varios valores".
16. MEDIDAS EN TEXTO (grosor, ancho, largo, alto, espesor, diametro, calibre, capacidad):
    aunque el tipo diga "string", Mercado Libre las valida como NUMERO CON UNIDAD. Escribe
    UN solo numero con su unidad (ej: "0.5 mm"), sin rangos ("0.5-2 mm") ni palabras
    ("fino y grueso"). Si no conoces el numero, OMITE el atributo.

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


# ── Revisión sin IA de lo que la IA contestó ───────────────────────────────────
def _plano(texto: Any) -> str:
    """Minúsculas y sin acentos, para comparar «Niñas» con «ninas»."""
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn").strip()


# Palabras del título → género de ML. Gana la palabra que aparece PRIMERO en el título
# («Moños Niña Bebé» → Niñas).
_GENERO_PALABRAS = (
    (r"\bnin(a|as)\b", "Niñas"),
    (r"\bnin(o|os)\b", "Niños"),
    (r"\bbebe(s)?\b", "Bebés"),
    (r"\b(mujer|mujeres|dama|damas|femenin[oa])\b", "Mujer"),
    (r"\b(hombre|hombres|caballero|caballeros|masculin[oa])\b", "Hombre"),
)


def genero_del_titulo(titulo: str, validos: list[str]) -> str | None:
    """El valor de GENDER que pide el título, si el título nombra uno y la categoría lo
    tiene. None si el título no dice nada de género: no se adivina."""
    plano = _plano(titulo)
    hallados = []
    for patron, genero in _GENERO_PALABRAS:
        m = re.search(patron, plano)
        if m:
            hallados.append((m.start(), genero))
    por_plano = {_plano(v): v for v in validos}
    nombrados = {g for _, g in hallados}
    # «para niños y niñas» / «para hombre y mujer»: es de los dos, no del primero.
    if {"Niñas", "Niños"} <= nombrados and _plano("Sin género infantil") in por_plano:
        return por_plano[_plano("Sin género infantil")]
    if {"Mujer", "Hombre"} <= nombrados and _plano("Sin género") in por_plano:
        return por_plano[_plano("Sin género")]
    for _, genero in sorted(hallados):
        if _plano(genero) in por_plano:
            return por_plano[_plano(genero)]
    return None


def _numero(valor: Any) -> float | None:
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(valor or ""))
    return float(m.group(0).replace(",", ".")) if m else None


def depurar(atributos: dict[str, str], meli_attrs: dict, titulo: str = "") -> dict[str, Any]:
    """
    Lo que Mercado Libre rechazaría, fuera ANTES de que llegue al formulario. Pura.

    Devuelve {"atributos", "avisos", "rechazados": [{campo, motivo}], "faltan": [ID]}:
      · lista cerrada → el valor EXACTO de la lista; si no está, fuera (y se dice);
        varios valores juntos en un atributo de una sola opción → se queda uno;
      · número ≤ 0 (o «0 W») → fuera: ML exige mayor que cero (TEC-1439-BLN);
      · GENDER → el que dice el título, si el título nombra uno (ACC-0446-MUL);
      · `faltan`: obligatorios que quedaron sin valor;
      · un dato FISCAL (`es_fiscal`) no sale nunca, y sin aviso: no se llena ni se toca.
    """
    por_id = {a["id"]: a for a in (meli_attrs.get("principales", []) + meli_attrs.get("secundarias", []))}
    salida: dict[str, str] = {}
    avisos: list[str] = []
    rechazados: list[dict[str, str]] = []
    for aid, valor in (atributos or {}).items():
        if es_fiscal(aid):
            continue
        a = por_id.get(aid)
        valor = str(valor).strip()
        if not valor:
            continue
        if not a:
            salida[aid] = valor  # BRAND / MODEL
            continue
        nombre = a.get("name") or aid
        if a.get("value_type") in ("number", "number_unit"):
            n = _numero(valor)
            if n is not None and n <= 0:
                rechazados.append({"campo": f"{nombre} ({aid})",
                                   "motivo": f"«{valor}»: Mercado Libre solo acepta valores mayores que "
                                             "cero. Se dejó vacío (si el producto no lo tiene, no aplica)."})
                continue
        validos = a.get("valid_values") or []
        if validos and a.get("value_type") == "list":
            por_plano = {_plano(v): v for v in validos}
            exacto = por_plano.get(_plano(valor))
            if not exacto:
                partes = [x for x in re.split(r"\s*(?:,|/|\||\by\b|\bo\b)\s*", valor) if x.strip()]
                encontrados = [por_plano[_plano(x)] for x in partes if _plano(x) in por_plano]
                if encontrados:
                    if aid == "GENDER":
                        exacto = genero_del_titulo(titulo, encontrados) or encontrados[0]
                    else:
                        exacto = encontrados[0]
                    if not a.get("varios") and len(encontrados) > 1:
                        avisos.append(f"{nombre}: «{valor}» → «{exacto}». Mercado Libre acepta una sola opción.")
            if not exacto:
                rechazados.append({"campo": f"{nombre} ({aid})",
                                   "motivo": f"«{valor}» no está en la lista de Mercado Libre "
                                             f"({', '.join(validos[:8])}{'…' if len(validos) > 8 else ''})."})
                continue
            valor = exacto
        salida[aid] = valor

    gen = por_id.get("GENDER")
    if gen and gen.get("valid_values"):
        del_titulo = genero_del_titulo(titulo, gen["valid_values"])
        if del_titulo and salida.get("GENDER") != del_titulo:
            antes = salida.get("GENDER")
            salida["GENDER"] = del_titulo
            avisos.append(f"Género: «{del_titulo}» porque el título lo dice"
                          + (f" (estaba «{antes}» y Mercado Libre lo rechazaría)." if antes else "."))

    faltan = [a["id"] for a in meli_attrs.get("principales", []) if not salida.get(a["id"])]
    return {"atributos": salida, "avisos": avisos, "rechazados": rechazados, "faltan": faltan}


# ── Orquestador ────────────────────────────────────────────────────────────────
async def generar_atributos(
    cat_id: Optional[str],
    nombre: str,
    alibaba_titulo: str = "",
    atributos_actuales: str = "",
    caracteristicas_clave: str = "",
    sku: str = "",
    variante: str = "",
) -> dict[str, Any]:
    """
    Devuelve:
      { "atributos": {ID: valor}, "flags": [...], "atributos_str": str,
        "num": int, "validos": bool, "meli_attrs": {principales, secundarias} }
    """
    meli_attrs = await get_meli_all_attributes(cat_id) if cat_id else {"principales": [], "secundarias": []}
    system = "Eres un experto en e-commerce para Mexico. Respondes siempre con JSON valido."
    user = build_prompt(nombre, alibaba_titulo, atributos_actuales, caracteristicas_clave,
                        meli_attrs, sku, variante=variante)

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
    revision = depurar(atributos, meli_attrs, titulo=nombre)
    atributos = revision["atributos"]

    atributos_str = _format_atributos(atributos)
    return {
        "atributos": atributos,
        "flags": flags,
        "atributos_str": atributos_str,
        "num": len(atributos),
        "validos": _calc_atributos_validos_str(atributos_str) == "SI",
        "meli_attrs": meli_attrs,
        "avisos": revision["avisos"],
        "rechazados": revision["rechazados"],
        "faltan": revision["faltan"],
    }
