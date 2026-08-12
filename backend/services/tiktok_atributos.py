"""
tiktok_atributos.py — Atributos de producto para TikTok Shop, generados con IA.

Hermano de `ml_atributos.py`, con las MISMAS tres ideas que hacen que aquel
funcione en producción:

  1. los atributos NO se inventan: se piden a la API de la categoría real
  2. las LISTAS CERRADAS se inyectan en el prompt, así el modelo elige en vez
     de redactar
  3. la llave del JSON es el **ID**, no el nombre visible

Y tres diferencias que TikTok impone y que hay que respetar:

  · TikTok quiere el **ID del atributo Y el ID del valor**. Mercado Libre se
    conforma con el nombre; aquí mandar solo texto es un rechazo. Por eso el
    prompt devuelve `value_id` junto a `value_name`, y el código valida que ese
    par exista de verdad antes de publicar.
  · Hay dos tipos: `PRODUCT_PROPERTY` (ficha) y `SALES_PROPERTY` (eje de
    variantes — Color, Talla). Los de venta NO van en `product_attributes`:
    generan SKUs distintos. Mezclarlos rompe el producto.
  · Casi ninguna categoría tiene atributos obligatorios (medido: `600032`
    "Frascos de vacío" tiene 8 y CERO obligatorios). Eso NO significa que dé
    igual llenarlos: son los filtros con los que el comprador encuentra el
    producto. Un producto sin atributos existe pero no aparece.

Regla heredada del caso `TEC-1812-NEG` (publicado en "Máquinas de Coser" por
confiar en un detector): la IA propone, el código valida contra la lista
cerrada, y lo que no coincide NO se manda.
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("omnicanal.tiktok_atributos")

MARCA = "Ferrahome"

# Color desde el sufijo del SKU. Es la MISMA tabla que usa ml_atributos: el
# sufijo es taxonomía interna de Kubera y no cambia por canal.
COLOR_SKU: dict[str, str] = {
    "NEG": "Negro", "BLN": "Blanco", "ROJ": "Rojo", "AZL": "Azul",
    "AZU": "Azul", "VER": "Verde", "NAR": "Naranja", "GRI": "Gris",
    "MOR": "Morado", "AMA": "Amarillo", "AMR": "Amarillo", "PLA": "Plata",
    "ORO": "Dorado", "DOR": "Dorado", "MUL": "Multicolor", "ROS": "Rosa",
    "LIL": "Lila", "CAF": "Café", "BEI": "Beige", "MET": "Plateado",
    "MAD": "Madera", "TRANS": "Transparente", "VIN": "Vino",
}


def _fmt_atributos(attrs: list[dict[str, Any]], etiqueta: str,
                   max_valores: int = 30) -> str:
    """
    Los atributos tal como los verá el modelo, con sus IDs y valores válidos.

    Se imprime el ID del atributo Y el de cada valor porque TikTok los exige
    ambos. Si solo diéramos los nombres, el modelo devolvería texto que después
    habría que adivinar a qué id corresponde — y ahí es donde se cuelan errores.
    """
    if not attrs:
        return f"{etiqueta}: (ninguno)\n"
    out = f"{etiqueta}:\n"
    for a in attrs:
        out += f"  - id={a.get('id')} · \"{a.get('name')}\"\n"
        vals = a.get("values") or []
        if vals:
            muestra = vals[:max_valores]
            pares = " | ".join(f"{v.get('id')}={v.get('name')}" for v in muestra)
            extra = f"  …y {len(vals)-len(muestra)} más" if len(vals) > len(muestra) else ""
            out += f"      valores: {pares}{extra}\n"
        else:
            out += "      valores: TEXTO LIBRE\n"
    return out


def build_prompt(*, sku: str, titulo: str, descripcion: str,
                 categoria_nombre: str, atributos_woo: dict[str, Any],
                 attrs_tiktok: list[dict[str, Any]]) -> str:
    """
    El prompt canónico de atributos para TikTok Shop México.

    `attrs_tiktok` viene de `GET /product/202309/categories/{id}/attributes`,
    o sea de la categoría REAL del producto — nunca de una lista hardcodeada.
    """
    de_ficha = [a for a in attrs_tiktok if a.get("type") != "SALES_PROPERTY"]
    de_venta = [a for a in attrs_tiktok if a.get("type") == "SALES_PROPERTY"]
    oblig = [a for a in de_ficha if a.get("is_requried") or a.get("is_required")]
    opc = [a for a in de_ficha if not (a.get("is_requried") or a.get("is_required"))]

    sufijo = sku.split("-")[-1].upper() if "-" in sku else ""
    pista_color = COLOR_SKU.get(sufijo)

    return f"""Eres un especialista en catalogación de producto para TikTok Shop México.

Tu tarea: llenar el MÁXIMO de atributos del producto, eligiendo SIEMPRE de las
listas que te doy. Un producto bien atribuido aparece en los filtros de
búsqueda; uno sin atributos existe pero nadie lo encuentra.

## EL PRODUCTO
- SKU: {sku}
- Título: {titulo}
- Descripción: {(descripcion or 'sin descripción')[:900]}
- Categoría de TikTok: {categoria_nombre}
- Atributos que ya tiene en WooCommerce: {json.dumps(atributos_woo, ensure_ascii=False) or '{{}}'}
{f'- Pista de color por el sufijo del SKU ({sufijo}): {pista_color}' if pista_color else ''}

## {_fmt_atributos(oblig, 'ATRIBUTOS OBLIGATORIOS — llénalos TODOS')}
## {_fmt_atributos(opc, 'ATRIBUTOS OPCIONALES — llena todos los que puedas inferir con seguridad')}
## {_fmt_atributos(de_venta, 'ATRIBUTOS DE VENTA (variantes) — NO los devuelvas aquí, solo para tu contexto')}

## REGLAS DURAS

1. **Devuelve el ID del atributo Y el ID del valor.** TikTok exige los dos.
   Nunca inventes un id: si el valor que quieres no está en la lista, elige el
   más cercano de la lista o deja el atributo fuera y explica por qué.

2. **Los de TEXTO LIBRE** (los que dicen `valores: TEXTO LIBRE`) llevan
   `value_id: null` y un `value_name` corto, en español, sin unidades raras.

3. **PROHIBIDO inventar atributos.** Las únicas llaves permitidas son los `id`
   listados arriba. Un atributo inventado hace fallar la publicación entera.

4. **Los SALES_PROPERTY no van en tu respuesta.** Esos generan variantes
   (Color, Talla) y los arma el código, no tú.

5. **No fuerces.** Si un atributo no se deduce del título, la descripción o los
   atributos de Woo, déjalo fuera y anótalo en `flags`. Es preferible un
   producto con 4 atributos ciertos que con 8 inventados: un dato falso no da
   error, se publica — y después nadie sabe cuál era mentira.

6. **Marca**: siempre "{MARCA}", nunca la del proveedor.

7. **Colores**: usa la pista del sufijo del SKU cuando exista y coincida con
   algún valor de la lista.

## SALIDA — SOLO este JSON, sin texto alrededor

{{
  "atributos": [
    {{"id": "<id del atributo>", "name": "<nombre para leerlo>",
      "value_id": "<id del valor o null si es texto libre>",
      "value_name": "<el valor>"}}
  ],
  "flags": ["<id o nombre>: por qué no se pudo determinar"],
  "confianza": 0.0
}}

`confianza` es tu propia estimación de 0 a 1 sobre qué tan seguro estás del
conjunto. Sé honesto: un 0.5 sincero vale más que un 0.9 inflado, porque el
panel usa ese número para decidir qué revisar a mano."""


def validar(propuesta: dict[str, Any],
            attrs_tiktok: list[dict[str, Any]]) -> tuple[list[dict], list[str]]:
    """
    Filtra lo que la IA propuso contra la categoría REAL. Devuelve (válidos, rechazos).

    ESTA FUNCIÓN ES LA GARANTÍA, no el prompt. El modelo puede alucinar un id o
    un valor por más instrucciones que le demos; aquí se comprueba contra la
    lista cerrada y lo que no cuadra NO se publica. Es la lección de
    `TEC-1812-NEG`, que acabó en "Máquinas de Coser" por confiar en un detector.
    """
    por_id = {str(a.get("id")): a for a in attrs_tiktok
              if a.get("type") != "SALES_PROPERTY"}
    validos: list[dict] = []
    rechazos: list[str] = []

    for prop in (propuesta.get("atributos") or []):
        aid = str(prop.get("id") or "")
        attr = por_id.get(aid)
        if not attr:
            rechazos.append(f"{aid or '(sin id)'}: no existe en esta categoría")
            continue
        permitidos = attr.get("values") or []
        vid, vname = prop.get("value_id"), (prop.get("value_name") or "").strip()

        if not permitidos:                      # texto libre
            if not vname:
                rechazos.append(f"{attr.get('name')}: texto libre vacío")
                continue
            validos.append({"id": aid, "values": [{"name": vname[:120]}]})
            continue

        # lista cerrada: el id manda; si no vino, se intenta por nombre exacto
        cal = next((v for v in permitidos if str(v.get("id")) == str(vid)), None)
        if not cal and vname:
            cal = next((v for v in permitidos
                        if (v.get("name") or "").lower() == vname.lower()), None)
        if not cal:
            rechazos.append(
                f"{attr.get('name')}: '{vname or vid}' no está entre sus "
                f"{len(permitidos)} valores válidos")
            continue
        validos.append({"id": aid,
                        "values": [{"id": str(cal.get("id")),
                                    "name": cal.get("name")}]})
    return validos, rechazos
