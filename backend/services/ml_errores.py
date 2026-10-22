"""
ml_errores.py — El rechazo de Mercado Libre, en palabras: POR QUÉ y QUÉ HACER.

POR QUÉ EXISTE. Brandon, 22-oct-2026: «en los errores puedes integrar el porqué del error
porque veo únicamente los 400, y la solución en caso de que sea posible». El pipeline
vendorizado resume cualquier rechazo como `HTTP 400: Validation error`, aunque la respuesta
de ML trae la causa exacta en `cause`. TEC-1439-BLN (22-oct) falló seis veces así; la causa
era «Attribute POWER with value 0 W was omitted» —una cortadora MANUAL con potencia 0 W—, y
solo se supo leyendo los logs de Railway.

QUÉ HACE. `explicar(respuesta, nombres)` toma la respuesta de `POST /items` y devuelve, por
cada causa de tipo `error`, `{codigo, porque, solucion}`; `mensaje(...)` lo junta en un texto.
`solucion` es None cuando no hay una conocida: se dice el porqué y no se inventa el arreglo.

DE DÓNDE SALE LA LISTA. De los rechazos REALES en los logs de producción del 23-sep al
22-oct-2026 (391 respuestas): medidas en cero (156), GTIN obligatorio (72), atributo
obligatorio faltante (61), fotos chicas (40), guía de tallas (32), precio mínimo (24), sin
fotos (6), título contra género (4), family_name (1). Un código que no esté aquí sale con el
texto de ML tal cual.

Pura: sin red ni base. `nombres` ({ID: "Nombre"}) lo pone quien llama, con los atributos de
la categoría que el pipeline ya tiene en memoria.
"""
from __future__ import annotations

import re
from typing import Any

# Avisos que ML manda junto a cualquier rechazo y no explican nada por sí solos.
_RUIDO = {"shipping.lost_me1_by_user", "item.shipping.mandatory_free_shipping",
          "shipping.lost_me2_by_intersected_logistics", "item.attributes.ignored",
          "shipping.free_shipping.cost_exceeded", "item.attribute.missing_catalog_required",
          "item.attributes.omitted"}

_DONDE = "En el Estudio, pestaña Mercado Libre, sección de atributos"


def _causas(respuesta: Any) -> list[dict[str, Any]]:
    if not isinstance(respuesta, dict):
        return []
    return [c for c in (respuesta.get("cause") or []) if isinstance(c, dict)]


def _ids(texto: str) -> list[str]:
    """«The attributes [GTIN, MODEL] are required…» → ['GTIN', 'MODEL']."""
    m = re.search(r"\[([A-Z0-9_,\s]+)\]", texto or "")
    return [x.strip() for x in m.group(1).split(",") if x.strip()] if m else []


def _nombre(aid: str, nombres: dict[str, str]) -> str:
    n = (nombres or {}).get(aid)
    return f"«{n}» ({aid})" if n and n != aid else aid


def _omitidos(causas: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """El aviso `item.attributes.omitted` dice QUÉ atributo y con qué valor:
    «Attribute POWER with value 0 W was omitted…» → [('POWER', '0 W')]."""
    salida = []
    for c in causas:
        if c.get("code") == "item.attributes.omitted":
            m = re.search(r"Attribute (\w+) with value (.+?) was omitted", c.get("message") or "")
            if m:
                salida.append((m.group(1), m.group(2).strip()))
    return salida


def _una(c: dict[str, Any], causas: list[dict[str, Any]], nombres: dict[str, str],
         omitidos_usados: set[str]) -> dict[str, Any]:
    codigo = c.get("code") or ""
    texto = (c.get("message") or "").strip()

    if codigo == "item.attribute.number_invalid_format":
        pendientes = [o for o in _omitidos(causas) if o[0] not in omitidos_usados]
        if pendientes:
            aid, valor = pendientes[0]
            omitidos_usados.add(aid)
            n = _nombre(aid, nombres)
            return {"codigo": codigo,
                    "porque": f"{n} va en {valor} y Mercado Libre solo acepta valores mayores que cero.",
                    "solucion": f"{_DONDE}: deja {n} vacío si no aplica al producto, o escribe el valor "
                                "real. Después vuelve a publicar."}
        campo = re.search(r'"([^"]+)"', texto)
        n = f"«{campo.group(1)}»" if campo else "un atributo numérico"
        return {"codigo": codigo,
                "porque": f"El valor de {n} no es válido para Mercado Libre ({texto}).",
                "solucion": f"{_DONDE}: corrige {n} con un número real mayor que cero, o déjalo vacío "
                            "si no aplica."}

    if codigo == "item.attribute.missing_conditional_required":
        ids = _ids(texto)
        if "GTIN" in ids:
            return {"codigo": codigo,
                    "porque": "Esta categoría exige el código de barras real (GTIN) del producto en esta cuenta.",
                    "solucion": "Captura el código de barras del empaque en el campo GTIN del Estudio y vuelve "
                                "a publicar. Si el producto no tiene, elige una categoría que no lo exija."}
        lista = ", ".join(_nombre(i, nombres) for i in ids) or "un atributo"
        return {"codigo": codigo,
                "porque": f"Mercado Libre pide {lista} para esta categoría.",
                "solucion": f"{_DONDE}: llénalo y vuelve a publicar."}

    if codigo == "item.attributes.missing_required":
        ids = _ids(texto)
        lista = ", ".join(_nombre(i, nombres) for i in ids) or "un atributo obligatorio"
        return {"codigo": codigo,
                "porque": f"Falta {lista}, que es obligatorio en esta categoría.",
                "solucion": f"{_DONDE}: llénalo (o usa «Mejorar con IA») y vuelve a publicar. Si ese dato "
                            "no tiene sentido para el producto, la categoría probablemente está mal: cámbiala."}

    if codigo == "item.pictures.invalid_size":
        return {"codigo": codigo,
                "porque": "Al menos una foto mide menos de 500 × 250 px, el mínimo de Mercado Libre.",
                "solucion": "Reemplaza esa foto por una más grande en WooCommerce (o pásala por el editor "
                            "de imágenes) y vuelve a publicar."}

    if codigo in ("missing.fashion_grid.grid_id.values", "invalid.fashion_grid.grid_id.values"):
        return {"codigo": codigo,
                "porque": "Es ropa o calzado y Mercado Libre exige una guía de tallas válida de esta cuenta.",
                "solucion": None}

    if codigo == "item.price.invalid":
        m = re.search(r"minimum of price (\d+(?:\.\d+)?)", texto)
        if m:
            return {"codigo": codigo,
                    "porque": f"El precio está debajo del mínimo que acepta esta categoría (${m.group(1)}).",
                    "solucion": f"Sube el precio a ${m.group(1)} o más y vuelve a publicar."}
        return {"codigo": codigo, "porque": f"Mercado Libre no acepta el precio: {texto}", "solucion": None}

    if codigo == "item.listing_type_id.requiresPictures":
        return {"codigo": codigo,
                "porque": "No llegó ninguna foto válida a Mercado Libre.",
                "solucion": "Agrega al menos una imagen al producto en WooCommerce y vuelve a publicar."}

    if codigo == "invalid.title.gender":
        return {"codigo": codigo,
                "porque": "El título no concuerda con el género del producto (por ejemplo, dice «mujer» y el "
                          "género es Hombre).",
                "solucion": "Corrige el título o el atributo Género (Estudio, pestaña Mercado Libre) para que "
                            "digan lo mismo."}

    if codigo == "body.required_fields":
        return {"codigo": codigo,
                "porque": "Falta el nombre del producto que exige esta categoría de catálogo (family_name).",
                "solucion": "Revisa que el producto tenga título y vuelve a publicar."}

    if "product_identifier.invalid_format" in codigo:
        return {"codigo": codigo,
                "porque": "El código de barras (GTIN) no tiene un formato válido.",
                "solucion": "Corrige el GTIN en el Estudio (solo dígitos, como viene en el empaque) y vuelve "
                            "a publicar."}

    return {"codigo": codigo, "porque": f"Mercado Libre dice: {texto or codigo}", "solucion": None}


def explicar(respuesta: Any, nombres: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Por cada causa de tipo `error`: {codigo, porque, solucion}. Lista vacía si no hay."""
    causas = _causas(respuesta)
    usados: set[str] = set()
    salida: list[dict[str, Any]] = []
    for c in causas:
        if c.get("type") != "error" or c.get("code") in _RUIDO:
            continue
        e = _una(c, causas, nombres or {}, usados)
        if e not in salida:  # ML repite la misma causa para cada atributo omitido
            salida.append(e)
    if not salida and any(c.get("code") == "shipping.lost_me1_by_user" for c in causas):
        salida.append({"codigo": "shipping.lost_me1_by_user",
                       "porque": "Esta categoría no admite Mercado Envíos 2: solo envío por paquetería propia.",
                       "solucion": "No hay nada que activar en la cuenta. Para publicar aquí hay que definir un "
                                   "costo de envío propio, o elegir otra categoría si esta no es la correcta."})
    return salida


def mensaje(explicacion: list[dict[str, Any]]) -> str:
    """El texto que se guarda y se enseña: «Mercado Libre la rechazó. Por qué: … Solución: …»."""
    partes = []
    for i, e in enumerate(explicacion, start=1):
        num = f"{i}) " if len(explicacion) > 1 else ""
        sol = f" Solución: {e['solucion']}" if e.get("solucion") else ""
        partes.append(f"{num}Por qué: {e['porque']}{sol}")
    return "Mercado Libre la rechazó. " + " ".join(partes) if partes else ""
