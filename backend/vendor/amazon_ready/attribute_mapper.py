"""
attribute_mapper.py - Construye el payload de atributos para Amazon Listings API
combinando datos de Woo + defaults universales + flags-disabler para deshabilitar
grupos condicionales (battery_*, hazmat_*, parentage_*).

Funcion principal:
  build_payload_attributes(wc_product, sku, marketplace_id, product_type=None) -> dict
"""
import json
import re
from pathlib import Path
from typing import Any

SCHEMAS_DIR = Path(__file__).parent / "data" / "schemas"


_EN_UNIT_VALUES = {"set", "count", "piece", "ounce", "fluid_ounce", "pound",
                   "gallon", "gram", "kilogram", "milliliter", "liter",
                   "meter", "inch", "foot", "yard", "milligram", "pair",
                   "dozen", "case", "carton", "bottle", "can"}


def _resolve_unit_count_type(product_type: str | None) -> tuple[str, str]:
    """Devuelve (value, language_tag) válidos para unit_count.type.
    Heurística: 'unidad' y otras palabras españolas usan es_MX;
    enums en inglés (set, count, ounce, etc.) usan en_US."""
    if not product_type:
        return "unidad", "es_MX"
    try:
        f = SCHEMAS_DIR / f"{product_type}.json"
        if not f.exists():
            return "unidad", "es_MX"
        with open(f, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        uc = schema.get("properties", {}).get("unit_count", {})
        type_prop = (uc.get("items", {}).get("properties") or {}).get("type", {})
        val_prop = (type_prop.get("properties") or {}).get("value", {})
        enum = val_prop.get("enum") or []
        if "unidad" in enum:
            return "unidad", "es_MX"
        if enum:
            v = enum[0]
            lt = "en_US" if v.lower() in _EN_UNIT_VALUES else "es_MX"
            return v, lt
    except Exception:
        pass
    return "unidad", "es_MX"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "", "0") else default
    except (TypeError, ValueError):
        return default


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _extract_pa_attrs(prod: dict) -> dict[str, str]:
    """Devuelve dict {attr_lower: first_value} desde prod['attributes']."""
    out = {}
    for a in prod.get("attributes") or []:
        name = (a.get("name") or "").lower().strip()
        opts = a.get("options") or []
        if name and opts:
            out[name] = str(opts[0]).strip()
            # tambien sin pa_ prefix
            if name.startswith("pa_"):
                out[name[3:]] = str(opts[0]).strip()
    return out


def _extract_color_from_sku(sku: str) -> str | None:
    """SKU formato 'XXX-NNNN-COL' donde COL es codigo de 3 letras."""
    SUFFIX_TO_COLOR = {
        "BLN": "Blanco", "NEG": "Negro", "ROJ": "Rojo", "AZL": "Azul",
        "VER": "Verde", "AMA": "Amarillo", "ROS": "Rosa", "MOR": "Morado",
        "NAR": "Naranja", "GRI": "Gris", "BEI": "Beige", "CAF": "Café",
        "MAD": "Madera", "MET": "Metal", "PLA": "Plata", "ORO": "Dorado",
        "VIN": "Vino", "LIL": "Lila", "TRA": "Transparente", "TUR": "Turquesa",
        "MUL": "Multicolor", "MIX": "Mixto",
    }
    parts = sku.upper().split("-")
    for p in reversed(parts):
        if p in SUFFIX_TO_COLOR:
            return SUFFIX_TO_COLOR[p]
    return None


def _build_bullets(name: str, color: str, material: str, tags: list[str]) -> list[str]:
    """5 bullet_points genericos. NO incluye tags Woo (Alibaba, DRAFT, etc. son internos)."""
    return [
        f"Modelo: {name[:140]}",
        f"Color: {color}",
        f"Material: {material}",
        f"Producto importado de calidad para uso diario",
        f"Diseño práctico y funcional. Listo para usar",
    ]


# ── PA_* (atributos Woo) → Amazon ────────────────────────────────────────────

PA_TO_AMAZON_TEXT = {
    # Texto plano simple
    "color":     "color",
    "size":      "size",
    "material":  "material",
    "brand":     None,           # ignorado, usamos Generic
    "model":     "model_name",
    "style":     "style",
    "pattern":   "pattern",
    "shape":     "item_shape",
}

PA_TO_AMAZON_NUMERIC_KG = {
    "machine_weight":   "item_weight",
    "max_user_weight":  "max_weight_recommendation",
    "weight":           "item_weight",
}

PA_TO_AMAZON_NUMERIC_W = {
    "wattage":         "wattage",
    "power_wattage":   "wattage",
    "power":           "wattage",
}

PA_TO_AMAZON_BOOL = {
    "is_foldable":            "is_foldable",
    "with_adjustable_handlebar": "is_adjustable",
    "includes_pedals":        "includes_pedals",
    "includes_routines_manual":"includes_user_manual",
}


def _bool_es(v: str) -> bool:
    return str(v).strip().lower() in ("si", "sí", "yes", "true", "1")


def _output_voltage_from_sku(sku: str) -> int:
    """Extrae voltaje del SKU si tiene formato '...-NNN V' o '...-12V'."""
    m = re.search(r"-(\d{1,3})V", sku.upper())
    return int(m.group(1)) if m else 12


# ── Payload builder principal ────────────────────────────────────────────────

def build_payload_attributes(prod: dict, sku: str, marketplace_id: str,
                             product_type: str | None = None) -> dict[str, Any]:
    """
    Construye el dict de atributos para enviar a Amazon Listings API.
    Incluye:
      - Atributos universales (Woo top-level + hardcoded defaults)
      - Disabler-flags para deshabilitar grupos condicionales (battery, hazmat, parentage)
      - Atributos derivados de pa_* (color, material, size, pesos, etc.)
      - unit_count.type schema-aware (lee enum del productType cacheado)
    """
    MP = marketplace_id

    # ── Datos básicos de Woo ──────────────────────────────────────────────
    name  = (prod.get("name") or "").strip()
    desc  = _strip_html(prod.get("description") or prod.get("short_description") or "")
    if not desc:
        desc = name + " - Producto importado de calidad"
    price = _safe_float(prod.get("regular_price") or prod.get("price"))
    stock = max(_safe_int(prod.get("stock_quantity"), 1), 1)

    weight_kg = _safe_float(prod.get("weight"), 1.0)
    dims = prod.get("dimensions") or {}
    L = _safe_float(dims.get("length"), 10.0)
    W = _safe_float(dims.get("width"),  10.0)
    H = _safe_float(dims.get("height"), 10.0)

    pa  = _extract_pa_attrs(prod)
    tags = [t.get("name", "") for t in (prod.get("tags") or [])]

    # ── Color: pa_color > sufijo SKU > Multicolor ─────────────────────────
    color = pa.get("color") or _extract_color_from_sku(sku) or "Multicolor"

    # ── Material: pa_material > Sintético ─────────────────────────────────
    material = pa.get("material") or "Sintético"

    # ── Size: pa_size > Único ─────────────────────────────────────────────
    size = pa.get("size") or "Único"

    # ── Bullets ───────────────────────────────────────────────────────────
    bullets = _build_bullets(name, color, material, tags)

    attrs: dict[str, Any] = {
        # ─── IDENTIDAD ────────────────────────────────────────────────────
        "item_name":           [{"value": name[:200], "language_tag": "es_MX", "marketplace_id": MP}],
        "brand":               [{"value": "Generic", "marketplace_id": MP}],
        "manufacturer":        [{"value": "Generic", "marketplace_id": MP}],
        "model_name":          [{"value": (pa.get("model") or name)[:50], "language_tag": "es_MX", "marketplace_id": MP}],
        "model_number":        [{"value": sku, "marketplace_id": MP}],
        "part_number":         [{"value": sku, "marketplace_id": MP}],

        # GTIN exemption (Brand=Generic)
        "supplier_declared_has_product_identifier_exemption": [{"value": True, "marketplace_id": MP}],

        # ─── DESCRIPCION ──────────────────────────────────────────────────
        "product_description": [{"value": desc, "language_tag": "es_MX", "marketplace_id": MP}],
        "bullet_point": [
            {"value": b, "language_tag": "es_MX", "marketplace_id": MP}
            for b in bullets
        ],

        # ─── CLASIFICACION ────────────────────────────────────────────────
        "condition_type":      [{"value": "new_new", "marketplace_id": MP}],
        "country_of_origin":   [{"value": "CN", "marketplace_id": MP}],
        "color":               [{"value": color, "language_tag": "es_MX", "marketplace_id": MP}],
        "material":            [{"value": material, "language_tag": "es_MX", "marketplace_id": MP}],
        "size":                [{"value": size, "language_tag": "es_MX", "marketplace_id": MP}],
        "style":               [{"value": pa.get("style") or "Casual", "language_tag": "es_MX", "marketplace_id": MP}],
        "pattern":             [{"value": pa.get("pattern") or "Liso", "language_tag": "es_MX", "marketplace_id": MP}],
        "item_shape":          [{"value": pa.get("shape") or "Rectangular", "language_tag": "es_MX", "marketplace_id": MP}],
        "department":          [{"value": "unisex-adult", "marketplace_id": MP}],
        "target_gender":       [{"value": "unisex", "marketplace_id": MP}],
        "age_range_description": [{"value": "Adultos", "language_tag": "es_MX", "marketplace_id": MP}],
        "import_designation":  [{"value": "imported", "marketplace_id": MP}],

        # ─── CANTIDAD / UNIDADES ──────────────────────────────────────────
        "number_of_items":      [{"value": 1, "marketplace_id": MP}],
        "item_package_quantity":[{"value": 1, "marketplace_id": MP}],
        "unit_count": (lambda v_lt: [{
            "value": 1,
            "type":  {"value": v_lt[0], "language_tag": v_lt[1]},
            "marketplace_id": MP,
        }])(_resolve_unit_count_type(product_type)),

        # ─── DIMENSIONES Y PESO ───────────────────────────────────────────
        "item_weight": [{
            "unit": "kilograms", "value": weight_kg, "marketplace_id": MP,
        }],
        "item_length_width_height": [{
            "length": {"unit": "centimeters", "value": L},
            "width":  {"unit": "centimeters", "value": W},
            "height": {"unit": "centimeters", "value": H},
            "marketplace_id": MP,
        }],
        "item_dimensions": [{
            "length": {"unit": "centimeters", "value": L},
            "width":  {"unit": "centimeters", "value": W},
            "height": {"unit": "centimeters", "value": H},
            "marketplace_id": MP,
        }],
        "item_package_weight": [{
            "unit": "kilograms",
            "value": round(weight_kg * 1.10, 2) or 0.5,
            "marketplace_id": MP,
        }],
        "item_package_dimensions": [{
            "length": {"unit": "centimeters", "value": round(L * 1.05, 1)},
            "width":  {"unit": "centimeters", "value": round(W * 1.05, 1)},
            "height": {"unit": "centimeters", "value": round(H * 1.05, 1)},
            "marketplace_id": MP,
        }],
        # Variantes adicionales del mismo concepto (algunos productTypes usan depth/length_width)
        "item_depth_width_height": [{
            "depth":  {"unit": "centimeters", "value": L},
            "width":  {"unit": "centimeters", "value": W},
            "height": {"unit": "centimeters", "value": H},
            "marketplace_id": MP,
        }],
        "item_length_width": [{
            "length": {"unit": "centimeters", "value": L},
            "width":  {"unit": "centimeters", "value": W},
            "marketplace_id": MP,
        }],
        "item_height_thickness": [{
            "height":    {"unit": "centimeters", "value": H},
            "thickness": {"unit": "millimeters", "value": 5},
            "marketplace_id": MP,
        }],

        # ─── PRECIOS Y OFERTA ─────────────────────────────────────────────
        "list_price": [{
            "value_with_tax": price,
            "currency":       "MXN",
            "marketplace_id": MP,
        }],
        "purchasable_offer": [{
            "currency":       "MXN",
            "marketplace_id": MP,
            "our_price":      [{"schedule": [{"value_with_tax": price}]}],
        }],
        "fulfillment_availability": [{
            "fulfillment_channel_code": "DEFAULT",
            "quantity":                 stock,
            "marketplace_id":           MP,
        }],

        # ─── DISABLER-FLAGS PARA GRUPOS CONDICIONALES ─────────────────────
        # No batteries (deshabilita 9+ attrs battery_*)
        "batteries_included":  [{"value": False, "marketplace_id": MP}],
        "batteries_required":  [{"value": False, "marketplace_id": MP}],

        # No hazmat (deshabilita ghs/hazmat/safety_data_sheet_url)
        "supplier_declared_dg_hz_regulation": [{
            "value": "not_applicable", "marketplace_id": MP,
        }],

        # ─── GARANTIA Y SAFETY ────────────────────────────────────────────
        "warranty_description": [{
            "value": "Garantía del vendedor 30 días contra defectos de fábrica",
            "language_tag": "es_MX", "marketplace_id": MP,
        }],
        "manufacturer_warranty_description": [{
            "value": "Garantía limitada del fabricante",
            "language_tag": "es_MX", "marketplace_id": MP,
        }],
        "safety_warning": [{
            "value": "Mantener fuera del alcance de menores. Adulto debe supervisar uso.",
            "language_tag": "es_MX", "marketplace_id": MP,
        }],

        # ─── COMPONENTES Y USOS GENERICOS ─────────────────────────────────
        "included_components": [{
            "value": "1 unidad del producto principal",
            "language_tag": "es_MX", "marketplace_id": MP,
        }],
        "specific_uses_for_product": [{
            "value": "Uso doméstico general",
            "language_tag": "es_MX", "marketplace_id": MP,
        }],
        "target_audience_keyword": [
            {"value": "Adultos",   "language_tag": "es_MX", "marketplace_id": MP},
            {"value": "Hombres",   "language_tag": "es_MX", "marketplace_id": MP},
            {"value": "Mujeres",   "language_tag": "es_MX", "marketplace_id": MP},
        ],
        "manufacturer_minimum_age_recommended": [{
            "unit": "months", "value": 36, "marketplace_id": MP,
        }],
        # Algunos productTypes (PUZZLES, TABLETOP_GAME, TOY_FIGURE) piden _age sin _recommended
        "manufacturer_minimum_age": [{
            "unit": "months", "value": 36, "marketplace_id": MP,
        }],
        "manufacturer_maximum_age": [{
            "unit": "months", "value": 600, "marketplace_id": MP,
        }],
        # Idioma (TABLETOP_GAME, TOY_FIGURE) — value enum lowercase, type=manual
        "language": [
            {"value": "spanish", "type": "manual", "marketplace_id": MP},
        ],
        # Number of players (TABLETOP_GAME)
        "number_of_players": [{"value": 4, "marketplace_id": MP}],
        # Sport type (DARTBOARD, etc.)
        "sport_type": [{"value": "Multi-sport", "language_tag": "es_MX", "marketplace_id": MP}],
        # Measurement accuracy (WEIGH_SCALE)
        "measurement_accuracy": [{"value": "±1 g", "language_tag": "es_MX", "marketplace_id": MP}],
        # Item width x height (DARTBOARD, etc.)
        "item_width_height": [{
            "width":  {"unit": "centimeters", "value": W},
            "height": {"unit": "centimeters", "value": H},
            "marketplace_id": MP,
        }],

        # ─── ATRIBUTOS COMUNES EN MULTIPLES CATEGORIAS (de iteracion 1) ───
        # Generic keyword (busqueda) — derivado del nombre + tags
        "generic_keyword": [
            {"value": " ".join(name.split()[:5])[:100], "language_tag": "es_MX", "marketplace_id": MP},
        ],
        # Lifestyle / estilo de vida
        "lifestyle":          [{"value": pa.get("lifestyle") or "Casual", "language_tag": "es_MX", "marketplace_id": MP}],
        # Booleans defaults
        "is_heat_sensitive":  [{"value": False, "marketplace_id": MP}],
        "is_assembly_required": [{"value": False, "marketplace_id": MP}],
        "is_fragile":         [{"value": False, "marketplace_id": MP}],
        # Wattage default si no viene en pa_
        # (PA_TO_AMAZON_NUMERIC_W lo sobreescribe si pa_wattage existe)
        "wattage":            [{"unit": "watts", "value": 100, "marketplace_id": MP}],
        # Cuidado / instrucciones
        "care_instructions":  [{"value": "Mantener seco. Limpiar con paño suave.", "language_tag": "es_MX", "marketplace_id": MP}],
        # Special feature
        "special_feature": [
            {"value": "Diseño ergonómico", "language_tag": "es_MX", "marketplace_id": MP},
            {"value": "Fácil de usar", "language_tag": "es_MX", "marketplace_id": MP},
        ],
        # Power source
        "power_source_type":  [{"value": "Corded Electric", "marketplace_id": MP}],
        # Fabric type (textiles)
        "fabric_type":        [{"value": material, "language_tag": "es_MX", "marketplace_id": MP}],
        # Direcciones de uso
        "directions":         [{"value": "Seguir manual de instrucciones del fabricante.", "language_tag": "es_MX", "marketplace_id": MP}],

        # ─── ITERACION 6 — defaults para mas categorias ────────────────────
        # subject_keyword (PLAYARD, etc.)
        "subject_keyword": [
            {"value": " ".join(name.split()[:3])[:50], "language_tag": "es_MX", "marketplace_id": MP},
        ],
        # website_shipping_weight (AIR_COOLER, etc.) — usa el peso de item_package_weight
        "website_shipping_weight": [{
            "unit": "kilograms",
            "value": round(weight_kg * 1.10, 2) or 0.5,
            "marketplace_id": MP,
        }],
        # is_oem_authorized (AIR_COOLER, etc.)
        "is_oem_authorized":     [{"value": False, "marketplace_id": MP}],
        # measurement_system (ELECTRONIC_COMPONENT_TERMINAL, etc.)
        "measurement_system":    [{"value": "Metric", "marketplace_id": MP}],
        # operating_voltage (electronicos)
        "operating_voltage":     [{"unit": "volts", "value": 12, "marketplace_id": MP}],
        # specification_met (electronicos genericos)
        "specification_met":     [{"value": "Standard", "language_tag": "es_MX", "marketplace_id": MP}],
        # material_composition (PLAYARD, etc.) — array de strings
        "material_composition": [
            {"value": material, "language_tag": "es_MX", "marketplace_id": MP},
        ],

        # ─── ITERACION 7 — closure y otras defaults reincorporadas ─────────
        # closure (MEDICAL_SCRUB_SET, TENT, BAG, WALLET) — sub-prop 'type' array
        "closure": [{
            "marketplace_id": MP,
            "type": [{"language_tag": "es_MX", "value": "Cremallera"}],
        }],
        # item_form (GIFT_WRAP, etc.)
        "item_form": [{"value": "Roll", "language_tag": "es_MX", "marketplace_id": MP}],
        # capacity (THERMOS, TENT) — unidad ml por default (1000 ml = 1 L)
        "capacity": [{"unit": "milliliters", "value": 500, "marketplace_id": MP}],
        # water_resistance_level (TENT) — enum: water_resistant
        "water_resistance_level": [{"value": "water_resistant", "marketplace_id": MP}],
        # occupant_capacity (TENT) — number
        "occupant_capacity": [{"value": 2, "marketplace_id": MP}],
        # temperature_rating (TENT, ORTHOPEDIC_BRACE) — texto
        "temperature_rating": [{"value": "Ambiente (5-35°C)", "language_tag": "es_MX", "marketplace_id": MP}],
        # compartment (DUFFEL_BAG, BAGS) — sub-prop description
        "compartment": [{
            "marketplace_id": MP,
            "description": [{"language_tag": "es_MX", "value": "Compartimento principal con cierre"}],
        }],
        # keyboard_description (KEYBOARDS) — value+language_tag plano
        "keyboard_description": [{
            "value": "Teclado funcional para uso diario", "language_tag": "es_MX", "marketplace_id": MP,
        }],

        # ─── ITERACION 3: defaults adicionales ────────────────────────────
        # Browse node ID (Hogar y Cocina por defecto — Amazon re-categoriza si aplica)
        "recommended_browse_nodes": [{"value": "9482594011", "marketplace_id": MP}],

        # Eléctricos (CHARGING_ADAPTER, ELECTRONIC_DEVICE_*)
        "input_voltage":       [{"unit": "volts", "value": 110, "marketplace_id": MP}],
        "output_voltage":      [{"unit": "volts", "value": _output_voltage_from_sku(sku), "marketplace_id": MP}],
        "output_current":      [{"unit": "amps", "value": 2, "marketplace_id": MP}],
        "number_of_ports":     [{"value": 1, "marketplace_id": MP}],

    }

    # ─── PRODUCTTYPE-ESPECIFICOS ──────────────────────────────────────────
    # Los attrs siguientes solo se agregan si el productType los acepta
    # (evita ERROR 4000001 al enviarlos a categorias que no los soportan)
    if product_type == "ELECTRONIC_CABLE":
        attrs["cable"] = [{
            "length": [{"decimal_value": 1.0, "string_value": "1.0", "unit": "meters"}],
            "type":   [{"value": "USB"}],
            "marketplace_id": MP,
        }]
        attrs["connector_type"]   = [{"value": "USB-A", "marketplace_id": MP}]
        attrs["connector_gender"] = [{"value": "Male",  "marketplace_id": MP}]
    elif product_type == "HAIR_IRON":
        attrs["cable"] = [{
            "length": [{"decimal_value": 1.5, "string_value": "1.5", "unit": "meters"}],
            "marketplace_id": MP,
        }]
    elif product_type == "SANDER":
        # grit es objeto nested con type/material/number
        attrs["grit"] = [{
            "marketplace_id": MP,
            "type":     [{"value": "Aluminum Oxide"}],
            "material": [{"value": "Aluminum Oxide"}],
            "number":   [{"value": 120}],
        }]
    elif product_type == "ELECTRONIC_COMPONENT_TERMINAL":
        # 8 attrs específicos
        attrs["gauge"]            = [{"value": "12 AWG", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["connector_type"]   = [{"value": "Bullet", "marketplace_id": MP}]
        # contact/insulation tienen sub-prop 'material' (igual que outer/inner)
        attrs["contact"]          = [{
            "marketplace_id": MP,
            "material": [{"language_tag": "es_MX", "value": "Cobre"}],
        }]
        attrs["stud_size"]        = [{"value": "M6", "marketplace_id": MP}]
        attrs["insulation"]       = [{
            "marketplace_id": MP,
            "material": [{"language_tag": "es_MX", "value": "Vinilo"}],
        }]
    elif product_type == "ELECTRONIC_SWITCH":
        # 13 attrs específicos
        attrs["switch_type"]                  = [{"value": "Toggle", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["mounting_type"]                = [{"value": "Panel", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["current_rating"]               = [{"unit": "amps", "value": 10, "marketplace_id": MP}]
        attrs["number_of_positions"]          = [{"value": 2, "marketplace_id": MP}]
        attrs["international_protection_rating"] = [{"value": "IP20", "marketplace_id": MP}]
        attrs["upper_temperature_rating"]     = [{"unit": "degrees_celsius", "value": 70, "marketplace_id": MP}]
        attrs["lower_temperature_rating"]     = [{"unit": "degrees_celsius", "value": -10, "marketplace_id": MP}]
        # contact: tiene 3 sub-props (material + type + marketplace_id)
        attrs["contact"]                      = [{
            "marketplace_id": MP,
            "material": [{"language_tag": "es_MX", "value": "Cobre"}],
            "type":     [{"language_tag": "es_MX", "value": "Plateado"}],
        }]
        attrs["circuit_type"]                 = [{"value": "SPST", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["operation_mode"]               = [{"value": "Manual", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["terminal_type"]                = [{"value": "Tornillo", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["connector_type"]               = [{"value": "Tornillo", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "ORTHOPEDIC_BRACE":
        # FDA enums lowercase (i/ii/iii/iv, consumer/rx_only, otc, etc.)
        attrs["fda_premarket_approval_number_pma"]  = [{"value": "Not applicable", "marketplace_id": MP}]
        attrs["fda_premarket_approval_number_510k"] = [{"value": "Not applicable", "marketplace_id": MP}]
        attrs["fda_device_classification"]      = [{"value": "i", "marketplace_id": MP}]
        attrs["fda_label_type"]                 = [{"value": "consumer", "marketplace_id": MP}]
        attrs["fda_instructions_for_use_type"]  = [{"value": "consumer", "marketplace_id": MP}]
        attrs["fda_indication_of_use"]          = [{"value": "otc", "marketplace_id": MP}]
    elif product_type == "GIFT_WRAP":
        # Conflicto item_form='Roll' + unit_count='unidad'.
        # Forzar unit_count.type='metro cuadrado' para coherencia.
        attrs["unit_count"] = [{
            "value": 1,
            "type":  {"value": "metro cuadrado", "language_tag": "es_MX"},
            "marketplace_id": MP,
        }]
    elif product_type == "STRING_LIGHT":
        attrs["cable"] = [{
            "length": [{"decimal_value": 5.0, "string_value": "5", "unit": "meters"}],
            "marketplace_id": MP,
        }]
    elif product_type == "AIR_COMPRESSOR":
        attrs["maximum_operating_pressure"] = [{"unit": "pounds_per_square_inch", "value": 100, "marketplace_id": MP}]
    elif product_type == "STRINGED_INSTRUMENTS":
        attrs["number_of_strings"] = [{"value": 6, "marketplace_id": MP}]
        attrs["string"] = [{
            "marketplace_id": MP,
            "material": [{"language_tag": "es_MX", "value": "Acero"}],
        }]
    elif product_type in ("IGNITION_COIL", "CARGO_STRAP", "SHOCK_ABSORBER", "JUMP_STARTER"):
        attrs["automotive_fit_type"] = [{"value": "universal_fit", "marketplace_id": MP}]
    elif (product_type or "").startswith("VEHICLE_") or (product_type or "").startswith("ENGINE_") or product_type in ("UTILITY_JACK",):
        # Catchall para vehiculares: solo automotive_fit_type basico
        attrs["automotive_fit_type"] = [{"value": "universal_fit", "marketplace_id": MP}]
    elif product_type == "CHAIR":
        attrs["seat_depth"] = [{"unit": "centimeters", "value": 45, "marketplace_id": MP}]
        attrs["seat_height"] = [{"unit": "centimeters", "value": 45, "marketplace_id": MP}]
        attrs["seat_width"] = [{"unit": "centimeters", "value": 45, "marketplace_id": MP}]
        # seat con sub-prop depth (formato nested)
        attrs["seat"] = [{
            "marketplace_id": MP,
            "depth": [{"unit": "centimeters", "value": 45}],
            "width": [{"unit": "centimeters", "value": 45}],
            "height": [{"unit": "centimeters", "value": 45}],
        }]
    elif product_type == "ART_CRAFT_KIT":
        attrs["theme"] = [{"value": "Generic", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["age_range_description"] = [{"value": "Niños y Adultos", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "CLEANING_AGENT":
        attrs["contains_liquid_contents"] = [{"value": True, "marketplace_id": MP}]
        attrs["scent"] = [{"value": "Fresco", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "AROMA_DIFFUSER":
        attrs["scent"] = [{"value": "Lavanda", "language_tag": "es_MX", "marketplace_id": MP}]
        # unit_count enum: 'mililitro' o 'Unidad' (case-sensitive)
        attrs["unit_count"] = [{"value": 1, "type": {"value": "Unidad", "language_tag": "es_MX"}, "marketplace_id": MP}]
    elif product_type == "ART_CRAFT_KIT":
        attrs["theme"] = [{"value": "Generic", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["age_range_description"] = [{"value": "Niños y Adultos", "language_tag": "es_MX", "marketplace_id": MP}]
        # ART_CRAFT_KIT solo acepta 'set'
        attrs["unit_count"] = [{"value": 1, "type": {"value": "set", "language_tag": "en_US"}, "marketplace_id": MP}]
    elif product_type == "INKJET_PRINTER_INK":
        # supported_media_sizes: enum estricto (ej: '11_x_14_inches')
        attrs["supported_media_sizes"] = [{"value": "11_x_14_inches", "marketplace_id": MP}]
        attrs["mfg_series_number"] = [{"value": "Generic-001", "marketplace_id": MP}]
        # cartridge_type enum: compatible/genuine/refilled/remanufactured
        attrs["cartridge_type"] = [{"value": "compatible", "marketplace_id": MP}]
        attrs["unit_count"] = [{"value": 1, "type": {"value": "Unidad", "language_tag": "es_MX"}, "marketplace_id": MP}]
    elif product_type == "RAW_MATERIALS":
        attrs["contains_liquid_contents"] = [{"value": False, "marketplace_id": MP}]
    elif product_type == "NECKLACE":
        attrs["clasp_type"] = [{"value": "Lobster", "language_tag": "es_MX", "marketplace_id": MP}]
        # jewelry_material_categorization: enum (no texto libre)
        attrs["jewelry_material_categorization"] = [{"value": "base_metal", "marketplace_id": MP}]
        attrs["gem_type"] = [{"value": "no_gemstone", "marketplace_id": MP}]
        # stones requiere id + type + treatment_method + creation_method
        attrs["stones"] = [{
            "marketplace_id": MP,
            "id":               [{"value": "1"}],
            "type":             [{"value": "no_gemstone"}],
            "treatment_method": [{"value": "not_enhanced"}],
            "creation_method":  [{"value": "natural"}],
        }]
    elif product_type == "PRINTER":
        attrs["form_factor"] = [{"value": "Compacta", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["connectivity_technology"] = [{"value": "USB", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["printer_output"] = [{"value": "Color", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["max_printspeed_color"] = [{"value": 10, "unit": "pages_per_minute", "marketplace_id": MP}]
        attrs["max_printspeed_black_white"] = [{"value": 12, "unit": "pages_per_minute", "marketplace_id": MP}]
        attrs["additional_printer_functions"] = [{"value": "print_only", "marketplace_id": MP}]
        attrs["paper_size"] = [{"value": 21.6, "unit": "centimeters", "marketplace_id": MP}]
        attrs["wireless_communication_technology"] = [{"value": "Wi-Fi", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["print_media_type"] = [{"value": "paper_plain", "marketplace_id": MP}]
        attrs["printer_technology"] = [{"value": "Inyeccion de tinta", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["part_number"] = [{"value": sku, "marketplace_id": MP}]
        attrs["mfg_series_number"] = [{"value": sku, "marketplace_id": MP}]
        attrs["media_size_maximum"] = [{"value": "A4", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "SAUTE_FRY_PAN":
        attrs["item_width_diameter_height"] = [{
            "marketplace_id": MP,
            "width":    {"value": 24, "unit": "centimeters"},
            "diameter": {"value": 24, "unit": "centimeters"},
            "height":   {"value": 5,  "unit": "centimeters"},
        }]
    elif product_type == "TREADMILL":
        attrs["maximum_speed"] = [{"value": 16, "unit": "kilometers_per_hour", "marketplace_id": MP}]
        attrs["minimum_speed"] = [{"value": 1, "unit": "kilometers_per_hour", "marketplace_id": MP}]
        attrs["maximum_horsepower"] = [{"value": 3, "unit": "horsepower", "marketplace_id": MP}]
        attrs["maximum_weight_recommendation"] = [{"value": 120, "unit": "kilograms", "marketplace_id": MP}]
        attrs["belt"] = [{
            "marketplace_id": MP,
            "length": [{"value": 120, "unit": "centimeters"}],
            "width":  [{"value": 40,  "unit": "centimeters"}],
        }]
        attrs["power_plug_type"] = [{"value": "type_a_2pin_na", "marketplace_id": MP}]
    elif product_type == "AUTO_PART":
        attrs["automotive_fit_type"] = [{"value": "vehicle_specific_fit", "marketplace_id": MP}]
        attrs["contains_liquid_contents"] = [{"value": False, "marketplace_id": MP}]
    elif product_type == "CURTAIN":
        attrs["opacity"] = [{"value": "Blackout", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["top_style"] = [{"value": "Rod Pocket", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "VIDEO_PROJECTOR":
        attrs["minimum_throw_distance"] = [{"unit": "meters", "value": 1, "marketplace_id": MP}]
        attrs["maximum_throw_distance"] = [{"unit": "meters", "value": 6, "marketplace_id": MP}]
        attrs["white_brightness"] = [{"unit": "lumens", "value": 4000, "marketplace_id": MP}]
        attrs["refresh_rate"] = [{"unit": "hertz", "value": 60, "marketplace_id": MP}]
        attrs["aspect_ratio"] = [{"value": "16:9", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "TELEVISION":
        attrs["display"] = [{
            "marketplace_id": MP,
            "type": [{"value": "LED", "language_tag": "es_MX"}],
            "technology": [{"value": "LED", "language_tag": "es_MX"}],
            "size": [{"unit": "inches", "value": 32}],
            "resolution_maximum": [{"value": "1920 x 1080"}],
            "refresh_rate_in_hertz": [{"value": 60}],
        }]
        attrs["image_aspect_ratio"] = [{"value": "16:9", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["connectivity_technology"] = [{"value": "HDMI", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["resolution"] = [{"value": "1080p", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["model_year"] = [{"value": 2024, "marketplace_id": MP}]
        attrs["total_hdmi_ports"] = [{"value": 2, "marketplace_id": MP}]
    elif product_type == "BOLTS":
        attrs["thread"] = [{
            "marketplace_id": MP,
            "coverage": [{"value": "Fully Threaded", "language_tag": "es_MX"}],
            "style": [{"value": "Standard", "language_tag": "es_MX"}],
            "size": [{"value": "M6", "language_tag": "es_MX"}],
        }]
    elif product_type == "DRESS":
        attrs["sleeve"] = [{
            "marketplace_id": MP,
            "type": [{"value": "Short Sleeve", "language_tag": "es_MX"}],
        }]
        attrs["neck"] = [{
            "marketplace_id": MP,
            "style": [{"value": "Crew Neck", "language_tag": "es_MX"}],
        }]
        attrs["apparel_size"] = [{
            "marketplace_id": MP,
            "size":        "m",
            "size_class":  "alpha",
            "size_system": "as1",
        }]
        attrs["item_length_description"] = [{"value": "Knee Length", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "BRA":
        attrs["item_styling"] = [{"value": "Push-up", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["underwire_type"] = [{"value": "Sin aro", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["shapewear_size"] = [{
            "marketplace_id": MP,
            "size":        "m",
            "size_class":  "alpha",
            "size_system": "as1",
        }]
        attrs["strap_type"] = [{"value": "Adjustable", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "HAT":
        attrs["headwear_size"] = [{
            "marketplace_id": MP,
            "size":        "m",
            "size_class":  "alpha",
            "size_system": "as1",
        }]
    elif product_type == "SWIMWEAR":
        attrs["number_of_pieces"] = [{"value": 1, "marketplace_id": MP}]
        attrs["shapewear_size"] = [{
            "marketplace_id": MP,
            "size":        "m",
            "size_class":  "alpha",
            "size_system": "as1",
        }]
    elif product_type == "PET_BED_MAT":
        attrs["item_length_width_thickness"] = [{
            "length":    {"unit": "centimeters", "value": L},
            "width":     {"unit": "centimeters", "value": W},
            "thickness": {"unit": "centimeters", "value": H},
            "marketplace_id": MP,
        }]
    elif product_type == "DRILL":
        # voltage requerido — usar default 18V (cordless tipico)
        attrs["voltage"] = [{"unit": "volts", "value": 18, "marketplace_id": MP}]
    elif product_type == "ELECTRONIC_ADAPTER":
        # connector_type necesita language_tag (no es enum global)
        attrs["connector_type"] = [{"value": "USB", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "SPEAKERS":
        attrs["speaker_type"] = [{"value": "Bluetooth", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["speaker_amplification_type"] = [{"value": "Active", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "THERMOMETER":
        attrs["item_length"] = [{"unit": "centimeters", "value": L, "marketplace_id": MP}]
    elif product_type == "POWER_BANK":
        attrs["compatible_devices"] = [{"value": "Smartphones, Tablets, Cámaras", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["item_length_width_thickness"] = [{
            "length":    {"unit": "centimeters", "value": L},
            "width":     {"unit": "centimeters", "value": W},
            "thickness": {"unit": "centimeters", "value": H},
            "marketplace_id": MP,
        }]
    elif product_type == "BICYCLE":
        attrs["brake_style"]      = [{"value": "Disc Brake", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["bike_type"]        = [{"value": "Mountain Bike", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["suspension_type"]  = [{"value": "Front Suspension", "language_tag": "es_MX", "marketplace_id": MP}]
        # tire usa tire_type (no type)
        attrs["tire"] = [{
            "marketplace_id": MP,
            "tire_type": [{"value": "Standard", "language_tag": "es_MX"}],
        }]
    elif product_type == "BACKPACK":
        attrs["strap_type"]           = [{"value": "Adjustable", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["outer"]                = [{
            "marketplace_id": MP,
            "material": [{"language_tag": "es_MX", "value": "Poliéster"}],
        }]
        attrs["storage_volume"]       = [{"unit": "liters", "value": 25, "marketplace_id": MP}]
        attrs["lining_description"]   = [{"value": "Forro de poliéster", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["number_of_compartments"] = [{"value": 3, "marketplace_id": MP}]
    elif product_type == "RECREATION_BALL":
        attrs["item_diameter"] = [{"unit": "centimeters", "value": 22, "marketplace_id": MP}]
        attrs["outer"] = [{
            "marketplace_id": MP,
            "material": [{"language_tag": "es_MX", "value": "PVC"}],
        }]
    elif product_type == "SOCKS":
        # apparel_size: estructura flat con enums lowercase
        attrs["apparel_size"] = [{
            "marketplace_id": MP,
            "size":        "m",
            "size_class":  "alpha",
            "size_system": "as1",
        }]
    elif product_type == "SLEEPING_BAG":
        # fill_material es flat (value+language_tag), no nested material
        attrs["fill_material"] = [{"value": "Sintético", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["occupancy"] = [{"value": "Single", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["outer"] = [{
            "marketplace_id": MP,
            "material": [{"language_tag": "es_MX", "value": "Poliéster"}],
        }]
        attrs["inner"] = [{
            "marketplace_id": MP,
            "material": [{"language_tag": "es_MX", "value": "Poliéster"}],
        }]
    elif product_type == "HEADPHONES":
        attrs["connectivity_technology"] = [{"value": "Wireless", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["headphones_form_factor"] = [{"value": "Over Ear", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "WEARABLE_COMPUTER":
        attrs["connectivity_technology"] = [{"value": "Bluetooth", "language_tag": "es_MX", "marketplace_id": MP}]
        # version_for_country: enum de country codes ISO 3166 (MX, US, etc.)
        attrs["version_for_country"] = [{"value": "MX", "marketplace_id": MP}]
        attrs["target_region"] = [{"value": "MX", "marketplace_id": MP}]
    elif product_type == "STATIONARY_BICYCLE":
        attrs["number_of_resistance_levels"] = [{"value": 8, "marketplace_id": MP}]
        attrs["maximum_height_recommendation"] = [{"unit": "centimeters", "value": 200, "marketplace_id": MP}]
        # weight_capacity tiene sub-prop 'maximum' (no flat unit/value)
        attrs["weight_capacity"] = [{
            "marketplace_id": MP,
            "maximum": [{"unit": "kilograms", "value": 100}],
        }]
    elif product_type == "CABINET":
        attrs["number_of_drawers"] = [{"value": 0, "marketplace_id": MP}]
        attrs["construction_type"] = [{"value": "Standard", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "AIR_MATTRESS":
        attrs["construction_type"] = [{"value": "Standard", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["item_firmness_description"] = [{"value": "Medium", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["item_length_width_thickness"] = [{
            "length":    {"unit": "centimeters", "value": L},
            "width":     {"unit": "centimeters", "value": W},
            "thickness": {"unit": "centimeters", "value": H},
            "marketplace_id": MP,
        }]
        attrs["sub_brand"] = [{"value": "Generic", "marketplace_id": MP}]
    elif product_type == "TABLE":
        attrs["base_type"] = [{"value": "Standard", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "PITCHER":
        attrs["collection"] = [{"value": "Standard", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "TABLET_COMPUTER":
        attrs["wireless_communication_technology"] = [{"value": "Wi-Fi", "language_tag": "es_MX", "marketplace_id": MP}]
        # cpu_model: manufacturer + model_number + speed (no family/socket en TABLET)
        attrs["cpu_model"] = [{
            "marketplace_id": MP,
            "manufacturer": [{"value": "Generic", "language_tag": "es_MX"}],
            "model_number": [{"value": "Generic"}],
            "speed":        [{"unit": "gigahertz", "value": 1.5}],
            "speed_maximum":[{"unit": "gigahertz", "value": 2.0}],
        }]
        # display nested
        attrs["display"] = [{
            "marketplace_id": MP,
            "type":               [{"value": "LCD", "language_tag": "es_MX"}],
            "size":               [{"unit": "inches", "value": 10}],
            "resolution_maximum": [{"value": "1920 x 1080"}],
            "refresh_rate_in_hertz": [{"value": 60}],
        }]
        attrs["graphics_description"] = [{"value": "Integrated", "language_tag": "es_MX", "marketplace_id": MP}]
        # ram_memory en TABLET: solo installed_size
        attrs["ram_memory"] = [{
            "marketplace_id": MP,
            "installed_size": [{"unit": "GB", "value": 4}],
        }]
        attrs["flash_memory"] = [{
            "marketplace_id": MP,
            "installed_size": [{"unit": "GB", "value": 32}],
        }]
        attrs["operating_system"] = [{"value": "Android", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["sub_brand"] = [{"value": "Generic", "marketplace_id": MP}]
        attrs["item_length_width_thickness"] = [{
            "length":    {"unit": "centimeters", "value": L},
            "width":     {"unit": "centimeters", "value": W},
            "thickness": {"unit": "centimeters", "value": H},
            "marketplace_id": MP,
        }]
        attrs["cellular_technology"] = [{"value": "None", "language_tag": "es_MX", "marketplace_id": MP}]
    elif product_type == "MOTHERBOARD":
        # cpu_model.family.value es enum estricto
        attrs["cpu_model"] = [{
            "marketplace_id": MP,
            "family":   [{"value": "5x86"}],
            "socket":   [{"value": "LGA1200"}],
        }]
        attrs["memory_slots_available"] = [{"value": 4, "marketplace_id": MP}]
        attrs["motherboard_type"]       = [{"value": "ATX", "marketplace_id": MP}]
        # ram_memory unit GB
        attrs["ram_memory"] = [{
            "marketplace_id": MP,
            "maximum_size": [{"unit": "GB", "value": 16}],
            "technology":   [{"value": "DDR4"}],
        }]
    elif product_type == "CAMERA_DIGITAL":
        # sensor con sub-prop type
        attrs["sensor"] = [{
            "marketplace_id": MP,
            "type": [{"value": "CMOS", "language_tag": "es_MX"}],
        }]
        attrs["effective_still_resolution"]   = [{"unit": "megapixels", "value": 12, "marketplace_id": MP}]
        attrs["max_resolution"]               = [{"unit": "megapixels", "value": 12, "marketplace_id": MP}]
        attrs["photo_sensor"] = [{
            "marketplace_id": MP,
            "resolution": [{"unit": "megapixels", "value": 12}],
            "size":       [{"unit": "millimeters", "value": 5}],
            "technology": [{"value": "CMOS"}],
        }]
        attrs["display"] = [{
            "marketplace_id": MP,
            "type":               [{"value": "LCD", "language_tag": "es_MX"}],
            "size":               [{"unit": "inches", "value": 3}],
            "maximum_resolution": [{"value": "1920 x 1080"}],
            "fixture_type":       [{"value": "Fixed", "language_tag": "es_MX"}],
        }]
        attrs["lens"] = [{
            "marketplace_id": MP,
            "type":         [{"value": "Standard", "language_tag": "es_MX"}],
            "construction": [{"value": "Glass", "language_tag": "es_MX"}],
        }]
        # optical_zoom necesita value numerico
        attrs["optical_zoom"] = [{"value": 5, "marketplace_id": MP}]
        attrs["video_capture_resolution"] = [{"value": "1080p", "marketplace_id": MP}]
        attrs["connectivity_technology"] = [{"value": "USB", "language_tag": "es_MX", "marketplace_id": MP}]
        attrs["has_self_timer"] = [{"value": True, "marketplace_id": MP}]
        attrs["model_year"] = [{"value": 2024, "marketplace_id": MP}]
        # special_feature solo 1 entrada (sobreescribir universal que envia 2)
        attrs["special_feature"] = [
            {"value": "Diseño ergonómico", "language_tag": "es_MX", "marketplace_id": MP},
        ]

    # ── Atributos extra desde pa_* (numericos) ────────────────────────────
    for pa_key, amz_key in PA_TO_AMAZON_NUMERIC_KG.items():
        if pa_key in pa:
            v = re.search(r"([\d.]+)", pa[pa_key])
            if v:
                attrs[amz_key] = [{
                    "unit": "kilograms", "value": float(v.group(1)),
                    "marketplace_id": MP,
                }]

    for pa_key, amz_key in PA_TO_AMAZON_NUMERIC_W.items():
        if pa_key in pa:
            v = re.search(r"([\d.]+)", pa[pa_key])
            if v:
                attrs[amz_key] = [{
                    "unit": "watts", "value": float(v.group(1)),
                    "marketplace_id": MP,
                }]

    for pa_key, amz_key in PA_TO_AMAZON_BOOL.items():
        if pa_key in pa:
            attrs[amz_key] = [{
                "value": _bool_es(pa[pa_key]), "marketplace_id": MP,
            }]

    return attrs
