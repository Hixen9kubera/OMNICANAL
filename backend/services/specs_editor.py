"""
specs_editor.py — Los ATRIBUTOS de un SKU por canal, EDITABLES por Bodega.

Brandon, 18-sep-2026: *«estos campos deben ser editables para que bodega pueda
poner los datos correctos. Trae todas las características obligatorias o
principales y las secundarias como opcionales de llenarse. Mercado Libre
obligatorio y los demás marketplaces opcionales por el momento»*.

`services/specs.py` contesta «¿qué le FALTA?». Este módulo contesta «¿qué
campos tiene y qué vale cada uno?», y los guarda.

DE DÓNDE SALE LA LISTA — y por qué NO se copia la del Publicador
-----------------------------------------------------------------
El apartado Atributos del Publicador parece el modelo obvio, pero su lista sale
de WordPress: `studio.metadata` → `wp_db.metadata_producto` → la postmeta
`_product_attributes` (studio.py:226-242, wp_db.py:736-739). Son los atributos
que tenga el producto en Woo, no los que EXIGE el canal para su categoría, y
leerlos viola la regla de Brandon del 17-sep: solo Supabase. Así que aquí la
lista sale de fuentes permitidas:

  · MERCADO LIBRE — la API PÚBLICA de ML, `GET /categories/{id}/attributes`.
    No es WordPress ni escribe nada. Hace falta porque kubera NO tiene los
    opcionales de ML: `channel.field_requirements` guarda 2,765 filas de ML y
    las 2,765 son obligatorias (el cargador solo toma `tags.required`). Medido
    en MLM81144: kubera tiene 5 campos y la API da 26 visibles —5 obligatorios
    y 21 opcionales (COLOR, VOLTAGE, WEIGHT, TREADMILL_TYPE…)—, con etiqueta en
    español, tipo, valores cerrados y unidades. Se cachea una hora por
    categoría.
  · AMAZON, TIKTOK, TEMU — `channel.field_requirements` (kubera), que para
    estos sí trae opcionales (Amazon 60,771, Temu 1,540).

DÓNDE SE GUARDA — el mismo sitio que el Publicador, sin tocar WordPress
-----------------------------------------------------------------------
`enrich.channel_content.contenido->'atributos'`, vía `channel_content.guardar`:
exactamente donde escribe el botón de cada canal en el Publicador
(PUT /api/productos/{sku}/canal/{canal}/contenido). Lo que capture Bodega
aparece en el Publicador y `publicar._rellenar_desde_guardado` lo recoge.

Tres cuidados que el guardado del Publicador ya enseñó por las malas:
  1. SE MANDA LA LISTA COMPLETA. El UPSERT fusiona con `||` jsonb a PRIMER
     nivel: mandar solo los campos que tocó Bodega pisaría la lista entera y
     borraría los atributos que ya estaban. Aquí se lee lo guardado, se fusiona
     por campo y se escribe todo.
  2. SE MANDA SIEMPRE LA CATEGORÍA. Si no llega, el router del Publicador la
     resuelve leyendo WordPress (productos.py:796-801). Aquí se resuelve desde
     kubera con `specs._categorias` y se pasa explícita.
  3. EN ML, `nombre` = EL ID DEL ATRIBUTO. Al ACTUALIZAR una publicación viva,
     `publicar._confirmar_ml` usa `nombre` como id de ML (publicar.py:705-709);
     un `nombre` con etiqueta humana («Formato de venta») viaja con un id
     inválido. Medido: 38 de los 111 atributos de ML guardados hoy tienen ese
     defecto. Aquí se guarda {campo: ID, nombre: ID, etiqueta, valor}.

LO QUE ESTO NO RESUELVE, Y HAY QUE DECIRLO
-------------------------------------------
Al CREAR una publicación nueva de ML, el adaptador `publicar_ready.construir_prod`
toma los atributos PRIMERO de las metas `ml_attr_*` de WordPress y lo de kubera
solo como respaldo (publicar_ready.py:451-456 y 496-497 → vendor
attribute_mapper.py:697-715). O sea: en altas nuevas, lo que capture Bodega NO
gana a una meta de WP que ya exista. Cambiar esa prioridad es tocar el flujo de
publicación vivo (regla 3 de la casa) y espera el visto bueno de Brandon.
Al ACTUALIZAR una publicación existente sí viaja lo de kubera.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from typing import Any

from services import channel_content, specs

log = logging.getLogger("omnicanal.services.specs_editor")

# Mercado Libre obligatorio, el resto opcional por ahora (Brandon, 18-sep). Es
# una ETIQUETA de la pantalla, no un filtro: los cuatro canales se editan.
OBLIGATORIO_POR_CANAL = {"mercado_libre": True, "amazon": False,
                         "tiktok": False, "temu": False}

# AMAZON MEZCLA EN LA MISMA LISTA lo que es del producto (color, material,
# medidas, peso) con lo que es de la PUBLICACIÓN: el título, las viñetas, las
# imágenes, el precio, la oferta, las variaciones y el cumplimiento. Medido en
# FLAT_SCREEN_DISPLAY_MOUNT: 103 campos, y sus tres «obligatorios» eran
# `item_name`, `bullet_point` y `product_description` — contenido que arma el
# Publicador, no un dato que Bodega mida en el almacén. Se quitan aquí por
# nombre exacto y por prefijo.
_AMAZON_NO_BODEGA = frozenset({
    "item_name", "bullet_point", "product_description", "generic_keyword",
    "list_price", "purchasable_offer", "fulfillment_availability",
    "externally_assigned_product_identifier", "recommended_browse_nodes",
    "product_tax_code", "map_policy", "max_order_quantity", "gift_options",
    "skip_offer", "condition_note", "supplemental_condition_information",
    "child_parent_sku_relationship", "parentage_level", "variation_theme",
    "package_level", "package_contains_sku", "title_differentiation",
    "dsa_responsible_party_address", "ships_globally", "compliance_media",
    "safety_data_sheet_url", "hazmat", "regulatory_compliance_certification",
    "is_this_product_subject_to_buyer_age_restrictions",
    "handmade_classification", "league_name", "team_name",
    "product_site_launch_date",
})
_AMAZON_NO_BODEGA_PREFIJOS = ("merchant_", "main_", "other_", "swatch_",
                              "gpsr_", "ghs")

_ML_TTL = 3600.0
_ml_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_ml_candado = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# La lista de campos
# ══════════════════════════════════════════════════════════════════════════════

def _campos_ml(categoria: str) -> list[dict[str, Any]]:
    """Los atributos VISIBLES de una categoría de ML, de su API pública.

    Se descartan los `hidden` y `read_only`: ML los calcula o no deja tocarlos
    (en MLM81144 son 56 de 82). Obligatorio = `required` o `catalog_required`,
    los dos niveles que ML hace respetar. `conditional_required` (GTIN,
    EMPTY_GTIN_REASON) queda opcional: depende de otro campo.
    """
    with _ml_candado:
        guardado = _ml_cache.get(categoria)
        if guardado and time.monotonic() - guardado[0] < _ML_TTL:
            return guardado[1]
    try:
        with urllib.request.urlopen(
                f"https://api.mercadolibre.com/categories/{categoria}/attributes",
                timeout=20) as r:
            crudos = json.load(r)
    except Exception as exc:  # noqa: BLE001
        log.warning("specs_editor: atributos de ML %s no disponibles: %s",
                    categoria, exc)
        # Lo último bueno, si lo hay: mejor una lista de hace un rato que dejar
        # a Bodega sin campos.
        return guardado[1] if guardado else []

    salida: list[dict[str, Any]] = []
    for a in crudos or []:
        tags = a.get("tags") or {}
        if "hidden" in tags or "read_only" in tags:
            continue
        salida.append({
            "campo": a.get("id"),
            "etiqueta": a.get("name") or a.get("id"),
            "obligatorio": "required" in tags or "catalog_required" in tags,
            "tipo": a.get("value_type"),
            # Los valores de ML son SUGERENCIAS en casi todos los campos de
            # texto: se ofrecen, pero se deja escribir otro.
            "valores": [v.get("name") for v in (a.get("values") or []) if v.get("name")],
            "unidades": [u.get("name") for u in (a.get("allowed_units") or []) if u.get("name")],
        })
    # Obligatorios arriba, en el orden en que ML los da.
    salida.sort(key=lambda x: not x["obligatorio"])
    with _ml_candado:
        _ml_cache[categoria] = (time.monotonic(), salida)
    return salida


def _valores_de(fila: dict[str, Any]) -> list[str]:
    """`valores_permitidos` viene en tres formas según el canal: lista de
    textos (TikTok), objeto {pid, valores:[{vid, valor}]} (Temu), u objeto con
    lista_cerrada (Walmart). Aquí se aplana a lista de textos."""
    v = fila.get("valores")
    if isinstance(v, list):
        return [str(x.get("valor") if isinstance(x, dict) else x) for x in v if x]
    if isinstance(v, dict):
        lista = v.get("valores") or v.get("lista_cerrada") or []
        if isinstance(lista, list):
            return [str(x.get("valor") if isinstance(x, dict) else x) for x in lista if x]
    return []


def _campos_kubera(canal: str, categoria: str) -> list[dict[str, Any]]:
    """Los campos de un canal desde `channel.field_requirements`.

    Solo los de CAPTURA: se quitan los comodines y los de fuente `codigo`,
    que son el cuerpo de la publicación (title, price, pictures) y los arma el
    publicador. Es el mismo corte que `specs._estado`.
    """
    salida = []
    for r in specs.matriz(canal, categoria):
        if r.get("comodin") or r.get("fuente") == "codigo":
            continue
        if canal == "amazon" and (
                r["campo"] in _AMAZON_NO_BODEGA
                or r["campo"].startswith(_AMAZON_NO_BODEGA_PREFIJOS)):
            continue
        salida.append({
            "campo": r["campo"],
            # field_requirements no tiene columna de etiqueta. En Temu el
            # propio `campo` ya es el nombre en español; en Amazon es el nombre
            # nativo de SP-API (snake_case) y en TikTok un id. Se enseña lo que
            # hay en vez de inventar una traducción.
            "etiqueta": r["campo"],
            "obligatorio": bool(r["obligatorio"]),
            "tipo": r.get("tipo"),
            "valores": _valores_de(r),
            "unidades": [],
        })
    salida.sort(key=lambda x: not x["obligatorio"])
    return salida


# ══════════════════════════════════════════════════════════════════════════════
# Lo que ya está capturado
# ══════════════════════════════════════════════════════════════════════════════

def _clave(a: dict[str, Any]) -> str:
    """Con qué campo se identifica un atributo guardado. Hay tres formas en la
    tabla: {campo, nombre, valor} (ML/TikTok), {nombre, valor} (Amazon) y
    {name, value[]} (Temu)."""
    return str(a.get("campo") or a.get("nombre") or a.get("name") or "")


def _valor(a: dict[str, Any]) -> str:
    v = a.get("valor", a.get("value"))
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if x not in (None, ""))
    return "" if v is None else str(v)


def _guardados_sync(sku: str, canal: str) -> list[dict[str, Any]]:
    fila = channel_content._leer_sync(sku, canal, "")
    cont = (fila or {}).get("contenido") or {}
    attrs = cont.get("atributos") if isinstance(cont, dict) else None
    return attrs if isinstance(attrs, list) else []


# ══════════════════════════════════════════════════════════════════════════════
# La API del módulo
# ══════════════════════════════════════════════════════════════════════════════

def editor_sync(sku: str, canal: str) -> dict[str, Any]:
    """Los campos de un SKU en un canal, con su valor actual."""
    if canal not in OBLIGATORIO_POR_CANAL:
        return {"ok": False, "motivo": f"Canal {canal} fuera del editor de specs."}
    info = (specs._categorias([sku]).get(sku) or {}).get(canal) or {}
    cat = info.get("categoria")
    base = {"ok": True, "sku": sku, "canal": canal, "categoria": cat,
            "categoria_fuente": info.get("fuente"),
            "canal_obligatorio": OBLIGATORIO_POR_CANAL[canal]}
    if not cat:
        return {**base, "campos": [], "fuente_lista": None,
                "aviso": "Este SKU no tiene categoría en el canal: sin ella no se "
                         "sabe qué campos pide."}

    if canal == "mercado_libre":
        campos = _campos_ml(cat)
        fuente = "API pública de Mercado Libre"
    else:
        campos = _campos_kubera(canal, cat)
        fuente = "channel.field_requirements"

    ya = {_clave(a): _valor(a) for a in _guardados_sync(sku, canal)}
    for c in campos:
        c["valor"] = ya.get(c["campo"], "")
    return {**base, "campos": campos, "fuente_lista": fuente,
            "aviso": None if campos else
            "Nadie ha leído qué exige esta categoría; no hay lista contra qué capturar."}


def _formato(canal: str, campo: str, etiqueta: str, valor: str) -> dict[str, Any]:
    """Cada canal guarda los atributos con su propia forma, y los publicadores
    la leen así. Se respeta la de cada uno para no romperles la lectura."""
    if canal == "temu":
        return {"name": campo, "value": [valor]}
    if canal == "amazon":
        return {"campo": campo, "nombre": etiqueta or campo, "valor": valor}
    # ML y TikTok: `nombre` = el ID. En ML es obligatorio porque
    # `_confirmar_ml` lo usa como id al actualizar (ver la cabecera).
    return {"campo": campo, "nombre": campo, "etiqueta": etiqueta, "valor": valor}


def guardar_sync(sku: str, canal: str, valores: dict[str, str],
                 etiquetas: dict[str, str] | None = None) -> dict[str, Any]:
    """Fusiona lo que capturó Bodega con lo ya guardado y escribe la lista
    COMPLETA. Ver los tres cuidados de la cabecera."""
    if canal not in OBLIGATORIO_POR_CANAL:
        return {"ok": False, "motivo": f"Canal {canal} fuera del editor de specs."}
    info = (specs._categorias([sku]).get(sku) or {}).get(canal) or {}
    cat = info.get("categoria")
    if not cat:
        return {"ok": False,
                "motivo": "El SKU no tiene categoría en ese canal: no se guarda "
                          "para no dejar atributos sin contra qué validarse."}

    etiquetas = etiquetas or {}
    actuales = _guardados_sync(sku, canal)
    por_clave = {_clave(a): a for a in actuales if _clave(a)}
    orden = [_clave(a) for a in actuales if _clave(a)]

    for campo, valor in (valores or {}).items():
        campo = (campo or "").strip()
        if not campo:
            continue
        valor = "" if valor is None else str(valor).strip()
        if not valor:
            # Vaciar un campo lo QUITA de la lista: un atributo con valor vacío
            # viajaría al canal como dato basura.
            por_clave.pop(campo, None)
            continue
        if campo not in por_clave:
            orden.append(campo)
        por_clave[campo] = _formato(canal, campo, etiquetas.get(campo, ""), valor)

    lista = [por_clave[k] for k in orden if k in por_clave]
    # Llamada directa al servicio, NO al router del Publicador: el router lee
    # WordPress para resolver la categoría si no llega, y aquí se pasa
    # explícita desde kubera.
    # `_guardar_sync` es la versión interna, SIN la red de `guardar` (que
    # «nunca lanza»): se envuelve aquí para que un fallo de la BD llegue a la
    # pantalla como un motivo legible y no como un 500.
    try:
        res = channel_content._guardar_sync(
            sku, canal, "", {"atributos": lista},
            {"atributos": "manual"}, cat, None, None, False)
    except Exception as exc:  # noqa: BLE001
        log.warning("specs_editor.guardar(%s,%s) falló: %s", sku, canal, exc)
        return {"ok": False, "motivo": f"No se pudo guardar: {exc}"}
    return {"ok": True, "guardados": len(lista), "categoria": cat,
            **(res if isinstance(res, dict) else {})}
