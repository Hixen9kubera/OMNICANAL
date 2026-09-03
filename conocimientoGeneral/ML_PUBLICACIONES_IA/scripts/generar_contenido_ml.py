#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generar_contenido_ml.py — Contenido y atributos de Mercado Libre, con IA, para
una lista de SKUs. Deja el resultado en archivos. NO publica nada.

===============================================================================
QUÉ HACE, EN CRISTIANO
===============================================================================

Le das una lista de SKUs. Por cada uno:

  1. LEE el producto en WooCommerce (título, descripción, atributos, y las metas
     `ml_categoria_id` / `ml_category_id` que dicen en qué categoría de Mercado
     Libre va a salir).
  2. LEE de la API PÚBLICA de Mercado Libre —sin token, sin cuenta— la ruta de
     esa categoría, su dominio y la lista CERRADA de atributos que ML exige.
  3. Le PIDE A LA IA el título, la descripción y los atributos, con los MISMOS
     prompts que usa el panel hoy (copiados literales, ver abajo).
  4. VALIDA la respuesta como lo hace producción: solo sobreviven las claves de
     atributo que ML declaró para esa categoría, y la marca se fuerza.
  5. ESCRIBE dos archivos en `salidas/`: un JSON con todo el detalle y un CSV
     para abrir en Excel.

Y además hace tres cosas que producción NO hace, y que son gratis:

  · IMPRIME LOS `flags` DE LA IA. Son las cosas que la IA misma admitió no
    saber. Producción los calcula, los pasea y los tira a la basura
    (`crear_producto.py:789`, `ia_generadores.py:399-405`). Aquí se ven.
  · Dice qué atributos OBLIGATORIOS de la categoría quedaron sin llenar.
  · Avisa cuando el valor que escribió la IA NO existe en la lista cerrada de
    ML. Eso importa: al publicar, si el atributo es obligatorio y no hay match,
    el pipeline mete EL PRIMER VALOR DE LA LISTA
    (`vendor/ml_ready/attribute_mapper.py:725`). Un dato arbitrario, en
    silencio. Aquí se ve antes de gastar el intento.

===============================================================================
QUÉ **NO** HACE — Y NO ES UN DESCUIDO, ES EL PUNTO
===============================================================================

  · NO escribe en WooCommerce. Ni una meta, ni un título, nada.
  · NO escribe en la base kubera, ni en Odoo, ni en MySQL.
  · NO publica, actualiza, pausa ni toca ninguna publicación de Mercado Libre.
  · NO manda nada a Amazon, TikTok, Temu ni Walmart.

Contra WooCommerce y contra Mercado Libre solo hace peticiones GET. La única
petición que NO es GET en todo el archivo es la que va al proveedor de IA
(DeepSeek o Anthropic), que es la mitad del trabajo.

**POR ESO NO HAY `--dry-run`.** Una bandera `--dry-run` existe para distinguir
"ensayo" de "de verdad". Aquí no hay "de verdad": el script no tiene ninguna
ruta de código que escriba en un sistema vivo. Poner la bandera sería fingir
que existe un modo peligroso, y quien la viera apagada supondría —con toda
lógica— que entonces el script SÍ aplica cambios. No los aplica nunca.

Lo que sí existe es `--sin-ia`, que es otra cosa: corre los pasos 1, 2 y 5 sin
preguntarle nada a la IA (y sin gastar un peso). Sirve para ver el semáforo de
categoría, dominio y guía de tallas de una lista de SKUs.

Aplicar el resultado es una decisión, y las decisiones llevan nombre: se hace
desde el panel, que registra quién lo hizo.

===============================================================================
CÓMO SE CORRE
===============================================================================

    # 1) copia .env.ejemplo a .env y llena las llaves (el .env NO se sube)
    cp .env.ejemplo .env

    # 2) SKUs sueltos
    python generar_contenido_ml.py EST-0054-NEG TEC-0661-BLN

    # 3) o una lista en un archivo, un SKU por línea
    python generar_contenido_ml.py --archivo mis_skus.txt

    # 4) revisar que las llaves y la red están bien, sin gastar IA
    python generar_contenido_ml.py --verificar

Opciones:

    --archivo RUTA     lista de SKUs, uno por línea. `#` = comentario.
    --salidas RUTA     dónde dejar los archivos (default: ../salidas)
    --cuenta NOMBRE    BEKURA | SANCORFASHION. Solo cambia el diagnóstico de
                       guía de tallas (una guía pertenece a UNA cuenta).
                       Default: BEKURA.
    --categoria MLM…   fuerza esta categoría de ML para TODOS los SKUs. Útil
                       para probar. Sin esto, manda la del panel.
    --sin-ia           no llama a la IA. Solo lee y arma el semáforo.
    --rehacer          ignora lo ya generado y vuelve a preguntarle a la IA
                       (ojo: eso sí se paga otra vez).
    --pausa SEGUNDOS   espera entre SKUs. Default 0.
    --verificar        comprueba llaves y conectividad y termina.

REANUDABLE: cada SKU terminado se guarda en `salidas/skus/<SKU>.json`. Si
vuelves a correr el mismo SKU, se reusa ese archivo y NO se le vuelve a pagar a
la IA. Para forzar, `--rehacer`.

TOLERANTE A FALLOS: si un SKU truena (no existe en Woo, se cayó la red, la IA
devolvió basura), se anota el error en su fila y el lote SIGUE con el
siguiente. Un SKU malo nunca aborta la corrida.

===============================================================================
LAS LLAVES
===============================================================================

Salen de variables de entorno o de un `.env` propio de esta carpeta. **Este
archivo no contiene ninguna llave real y nunca debe contenerla: el repositorio
es PÚBLICO.** Ver `.env.ejemplo`.

    WC_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET   ← obligatorias (leer Woo)
    DEEPSEEK_API_KEY  ó  ANTHROPIC_API_KEY        ← una de las dos, para la IA

Mercado Libre NO necesita credenciales: `/categories/{id}` y
`/categories/{id}/attributes` son públicos (verificado 2026-09-03, HTTP 200 sin
cabecera de autorización).

===============================================================================
QUÉ SE COPIÓ DE PRODUCCIÓN, Y DE DÓNDE
===============================================================================

La regla de esta carpeta es COPIAR, no importar: si importáramos `backend/`,
esta carpeta se rompería el día que producción cambie, y peor, dependería de
que producción no cambie. Todo lo de abajo está copiado a mano del commit
`1a7da7e` (backend v0.371.0) el 2026-09-03.

  De `backend/services/ml_atributos.py`:
     MARCA (:30) · MAX_SECUNDARIAS (:31) · _SKIP_IDS (:36-41) ·
     _ATTRS_BASICOS_RE (:42) · _ATTRS_EXCLUIDOS (:44-45) ·
     _format_atributos (:48) · _calc_atributos_validos_str (:52-70) ·
     el filtro de get_meli_all_attributes (:91-108) · _fmt_attr_list (:116-128) ·
     build_prompt (:131-193) · el system (:259) · _parse_json (:197-208) ·
     el bloque de validación (:268-274)

  De `backend/services/ia_generadores.py`:
     _ML_TITULO (:185-190) · _NO_CONTRADECIR (:279-284) ·
     _MEJORAR["mercado_libre"] (:287-297) · _contexto (:88-111) ·
     _sin_html (:114-117) · el user de mejorar (:363-366)

  De `backend/vendor/ml_ready/size_chart_mapping.py`:
     CHARTS_BY_ACCOUNT (:14-35) · get_chart_id (:38-46)

  De `backend/vendor/ml_ready/attribute_mapper.py`:
     _find_value_id (:742-773) · _normalize (:776-782)

DIFERENCIAS A PROPÓSITO con producción, para que nadie se sorprenda:

  1. Producción es asíncrona (httpx + asyncio). Aquí todo es SÍNCRONO y con
     `urllib` de la biblioteca estándar: un KAM no debería tener que instalar
     nada para correr esto. (Excepción: si usa Anthropic, hace falta el SDK
     oficial `anthropic`; el script lo dice si no está.)
  2. Producción parsea la respuesta de atributos con `json.loads` directo
     (`ml_atributos.py:241`). Aquí pasa por `_parse_json`, que además tolera
     cercas ```json. Es más laxo, nunca más estricto.
  3. El modelo de Anthropic por omisión aquí es `claude-opus-5`. Producción
     usa `claude-opus-4-8` (`ia_generadores.py:28`). Se cambia con
     ANTHROPIC_MODEL si quieres calcar producción exactamente.
  4. La caché de categorías de producción no expira nunca y muere con el
     proceso (`ml_atributos.py:74`). Aquí es la misma idea, dentro de una
     corrida.

===============================================================================
LO QUE HAY QUE SABER ANTES DE CREERLE AL RESULTADO
===============================================================================

  · La IA solo VE 15 atributos secundarios y 15 valores por atributo
    (`ml_atributos.py:31, 123-125`), aunque la categoría tenga 22 y 51. El
    prompt le exige "ortografía EXACTA" de una lista que no le enseñaron
    completa. Este script te dice cuando el valor no cuadró.
  · Nadie —ni producción ni esto— valida que el dato sea VERDADERO. Un
    "1.5 V" inventado pasa todos los filtros. Por eso se imprimen los `flags`.
  · El título de 60 caracteres de ML es una PETICIÓN del prompt. Nadie lo
    comprueba en producción (no existe `ml_contenido.py`). Aquí sí se cuenta,
    y sale como aviso.
  · La categoría que manda al publicar es `ml_categoria_id` (la del panel), NO
    `ml_category_id` (la del predictor). Medido: 185 productos tienen las dos y
    son distintas. Este script usa la del panel y te dice cuál usó.

Autor: sesión de Claude, rama `conocimiento`. Se comprueba con
`python conocimientoGeneral/verificar_aislamiento.py`.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import pathlib
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

# La consola de Windows suele ser cp1252 y los títulos traen acentos.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

AQUI = pathlib.Path(__file__).resolve().parent
CAPACIDAD = AQUI.parent
VERSION = "1.0.0"
EXTRAIDO_DE = "produccion commit 1a7da7e (backend v0.371.0), 2026-09-03"

ML_API = "https://api.mercadolibre.com"
ML_SITE = "MLM"  # México — backend/config.py:265


# ═══════════════════════════════════════════════════════════════════════════
# 0 · CONFIGURACIÓN — el .env propio de esta carpeta
# ═══════════════════════════════════════════════════════════════════════════

def cargar_env() -> list[pathlib.Path]:
    """Lee un `.env` propio (scripts/, la capacidad, o conocimientoGeneral/).

    Lo que ya esté en el entorno MANDA: así se puede correr sin archivo, con
    las variables exportadas a mano. Devuelve los archivos que sí encontró.
    """
    encontrados: list[pathlib.Path] = []
    for carpeta in (AQUI, CAPACIDAD, CAPACIDAD.parent):
        ruta = carpeta / ".env"
        if not ruta.is_file():
            continue
        encontrados.append(ruta)
        try:
            texto = ruta.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for linea in texto.splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            clave = clave.strip()
            valor = valor.strip().strip('"').strip("'")
            if clave and clave not in os.environ:
                os.environ[clave] = valor
    return encontrados


def env(nombre: str, default: str = "") -> str:
    return (os.environ.get(nombre) or default).strip()


# ═══════════════════════════════════════════════════════════════════════════
# 1 · HTTP — biblioteca estándar, para que esto corra sin instalar nada
# ═══════════════════════════════════════════════════════════════════════════

class ErrorHTTP(Exception):
    def __init__(self, codigo: int, url: str, cuerpo: str = ""):
        self.codigo = codigo
        self.url = url
        self.cuerpo = cuerpo[:500]
        super().__init__(f"HTTP {codigo} en {url}" + (f" — {self.cuerpo}" if cuerpo else ""))


def _abrir(req: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        cuerpo = ""
        try:
            cuerpo = e.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        raise ErrorHTTP(e.code, req.full_url, cuerpo) from None


def http_get_json(url: str, params: dict | None = None, cabeceras: dict | None = None,
                  timeout: float = 30.0) -> Any:
    """GET que devuelve JSON. Es lo ÚNICO que este script le hace a Woo y a ML."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", f"conocimientoGeneral/generar_contenido_ml {VERSION}")
    for k, v in (cabeceras or {}).items():
        req.add_header(k, v)
    _, cuerpo = _abrir(req, timeout)
    return json.loads(cuerpo.decode("utf-8", errors="replace"))


def http_json_a_ia(url: str, cuerpo: dict, cabeceras: dict, timeout: float) -> Any:
    """Envía un cuerpo JSON al PROVEEDOR DE IA. Ningún otro destino usa esto.

    Se llama así, y no `http_post`, a propósito: que el nombre diga a dónde va.
    Contra Woo y contra los marketplaces este archivo solo hace GET.
    """
    datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=datos, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", f"conocimientoGeneral/generar_contenido_ml {VERSION}")
    for k, v in cabeceras.items():
        req.add_header(k, v)
    _, resp = _abrir(req, timeout)
    return json.loads(resp.decode("utf-8", errors="replace"))


# ═══════════════════════════════════════════════════════════════════════════
# 2 · COPIADO DE PRODUCCIÓN — `backend/services/ml_atributos.py`
#     No se importa: se copia. Ver el encabezado del archivo.
# ═══════════════════════════════════════════════════════════════════════════

MARCA = "Ferrahome"                                   # ml_atributos.py:30
MAX_SECUNDARIAS = 15                                  # ml_atributos.py:31

# Atributos que NO se le piden a la IA (los gestiona el código aparte).
_SKIP_IDS = {                                         # ml_atributos.py:36-41
    "BRAND", "MODEL", "SELLER_SKU", "GTIN", "EMPTY_GTIN_REASON",
    "SELLER_PACKAGE_WEIGHT", "SELLER_PACKAGE_LENGTH",
    "SELLER_PACKAGE_WIDTH", "SELLER_PACKAGE_HEIGHT",
    "ORIGIN", "OEM",
}
_ATTRS_BASICOS_RE = ("peso", "dimen", "medida", "talla", "tamaño", "marca",   # :42
                     "brand", "variante", "variant")
_ATTRS_EXCLUIDOS = {"url_alibaba", "alibaba_price", "alibaba_title_original",  # :44-45
                    "ml_category_id", "categoria_meli_id"}


def _format_atributos(atributos_dict: dict) -> str:   # ml_atributos.py:48-49
    return " | ".join(f"{k}: {v}" for k, v in atributos_dict.items() if v)


def _calc_atributos_validos_str(atributos_str: str) -> str:   # ml_atributos.py:52-70
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


def _fmt_attr_list(attrs: list, label: str) -> str:   # ml_atributos.py:116-128
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
                 meli_attrs, sku: str = "") -> str:   # ml_atributos.py:131-193, LITERAL
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


SYSTEM_ATRIBUTOS = (          # ml_atributos.py:259
    "Eres un experto en e-commerce para Mexico. Respondes siempre con JSON valido."
)


def _parse_json(texto: str) -> dict:                  # ml_atributos.py:197-208
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


# ═══════════════════════════════════════════════════════════════════════════
# 3 · COPIADO DE PRODUCCIÓN — `backend/services/ia_generadores.py`
# ═══════════════════════════════════════════════════════════════════════════

_ML_TITULO = (                # ia_generadores.py:185-190
    "Eres experto en publicaciones de Mercado Libre México. Genera un TÍTULO de "
    "máximo 60 caracteres, con las palabras clave más buscadas al inicio, sin "
    "signos promocionales ni datos de contacto. Devuelve solo el título y, debajo, "
    "«(N caracteres)»."
)

# Blindaje anti-residuos. Existe por ACC-0653-CHE-13-16: faros de niebla con la
# categoría y los atributos de unos binoculares (producto clonado sin limpiar).
_NO_CONTRADECIR = (           # ia_generadores.py:279-284
    "\nIMPORTANTE: el TÍTULO y la DESCRIPCIÓN actuales definen QUÉ ES el "
    "producto. Si la categoría o los atributos recibidos los contradicen "
    "(pueden ser residuos de otro producto), IGNÓRALOS por completo y NO "
    "cambies el tipo de producto."
)

# El `system` de "Mejorar con IA" para Mercado Libre, armado igual que en
# producción por concatenación (ia_generadores.py:287-297).
SYSTEM_CONTENIDO_ML = _ML_TITULO.split(".")[0] + (
    ". Mejora la publicación de Mercado Libre México. Devuelve SOLO JSON válido:\n"
    '{"titulo": "<máx 60 caracteres, keywords al inicio>", '
    '"descripcion": "<texto plano, párrafos cortos, sin datos de contacto>", '
    '"atributos": [{"nombre": "..", "valor": ".."}]}\n'
    "En atributos incluye los NECESARIOS de la categoría (marca, modelo, color, "
    "material, tamaño…) y los secundarios que ayuden a la ficha. No inventes datos "
    "que no se puedan inferir del producto." + _NO_CONTRADECIR
)
MAX_TOKENS_CONTENIDO = 1500   # ia_generadores.py:288
MAX_TOKENS_ATRIBUTOS = 4096   # ml_atributos.py:211
# Piso solo para Claude — ver la explicación en `_ia_claude`. No aplica a
# DeepSeek, que corre con los mismos números que producción.
MIN_TOKENS_CLAUDE = 8000


def _sin_html(texto: str) -> str:                     # ia_generadores.py:114-117
    limpio = re.sub(r"<[^>]+>", " ", texto)
    return re.sub(r"\s+", " ", limpio).strip()


def _contexto(p: dict[str, Any]) -> str:              # ia_generadores.py:88-111
    partes: list[str] = []
    if p.get("nombre"):
        partes.append(f"Nombre actual: {p['nombre']}")
    if p.get("marca"):
        partes.append(f"Marca: {p['marca']}")
    if p.get("categoria"):
        partes.append(f"Categoría: {p['categoria']}")
    if p.get("precio") is not None:
        partes.append(f"Precio: ${p['precio']} MXN")
    if p.get("publico"):
        partes.append(f"Público objetivo: {p['publico']}")
    atributos = p.get("atributos") or []
    if atributos:
        attrs = "; ".join(
            f"{a.get('nombre')}: {a.get('valor')}"
            for a in atributos if a.get("nombre")
        )
        if attrs:
            partes.append(f"Atributos conocidos: {attrs}")
    if p.get("descripcion"):
        desc = _sin_html(str(p["descripcion"]))[:1500]
        partes.append(f"Descripción actual:\n{desc}")
    return "\n".join(partes) or "Sin datos del producto."


# ═══════════════════════════════════════════════════════════════════════════
# 4 · COPIADO DE PRODUCCIÓN — el vendor (`backend/vendor/ml_ready/`)
#     Es el que decide, al publicar, si el valor de la IA sobrevive.
# ═══════════════════════════════════════════════════════════════════════════

# size_chart_mapping.py:14-35 — 15 guías. La llave es "DOMINIO:GÉNERO" y es POR
# CUENTA: una guía de BEKURA no le sirve a SANCORFASHION.
CHARTS_BY_ACCOUNT = {
    "BEKURA": {
        "SANDALS_AND_CLOGS:Hombre":    "5601946",
        "SNEAKERS:Hombre":             "5601948",
        "SNEAKERS:Mujer":              "5602224",
        "BOOTS_AND_BOOTIES:Hombre":    "5602034",
        "LOAFERS_AND_OXFORDS:Hombre":  "5601950",
        "BRAS:Mujer":                  "5269931",
    },
    "SANCORFASHION": {
        "SANDALS_AND_CLOGS:Hombre":    "6009679",
        "SNEAKERS:Hombre":             "4538718",
        "SNEAKERS:Mujer":              "4821199",
        "SNEAKERS:Sin género":         "4827537",
        "BOOTS_AND_BOOTIES:Hombre":    "5601952",
        "BOOTS_AND_BOOTIES:Sin género infantil": "4572778",
        "LOAFERS_AND_OXFORDS:Hombre":  "5601954",
        "SAFETY_FOOTWEAR:Hombre":      "4859025",
        "BRAS:Mujer":                  "4922945",
    },
}
CUENTAS = tuple(CHARTS_BY_ACCOUNT.keys())


def get_chart_id(cuenta: str, domain: str, gender: str) -> str | None:  # :38-46
    if not cuenta or not domain or not gender:
        return None
    return CHARTS_BY_ACCOUNT.get(cuenta, {}).get(f"{domain}:{gender}")


def _normalize(text: str) -> str:                     # attribute_mapper.py:776-782
    t = text.lower().strip().replace("_", " ").replace("-", " ")
    t = re.sub(r"(\d)([a-z])", r"\1 \2", t)   # "120v" -> "120 v"
    t = re.sub(r"([a-z])(\d)", r"\1 \2", t)   # "v120" -> "v 120"
    return t


def _find_value_id(value: str, allowed_vals: list[str]) -> str | None:  # :742-773
    """Matcher difuso de tres pasadas. `allowed_vals` aquí son NOMBRES, porque
    es lo que guarda `get_meli_all_attributes` (`ml_atributos.py:102`); en el
    vendor son dicts con id — la lógica de comparación es la misma."""
    value_norm = _normalize(value)
    value_tokens = set(value_norm.split())
    for v in allowed_vals:                                   # 1. exacto
        if _normalize(v) == value_norm:
            return v
    for v in allowed_vals:                                   # 2. substring
        v_norm = _normalize(v)
        if value_norm in v_norm or v_norm in value_norm:
            return v
    best, best_score = None, 0.0                             # 3. tokens
    for v in allowed_vals:
        v_tokens = set(_normalize(v).split())
        if not v_tokens:
            continue
        overlap = len(v_tokens & value_tokens) / len(v_tokens)
        if overlap > best_score:
            best_score, best = overlap, v
    return best if best_score >= 1.0 else None


# ═══════════════════════════════════════════════════════════════════════════
# 5 · MERCADO LIBRE — API PÚBLICA, SIN TOKEN, SOLO GET
# ═══════════════════════════════════════════════════════════════════════════

_cache_cat: dict[str, dict] = {}


def ml_categoria(cat_id: str) -> dict:
    """`GET /categories/{id}` → ruta, dominio, si es categoría de catálogo."""
    d = http_get_json(f"{ML_API}/categories/{cat_id}", timeout=20.0)
    ajustes = d.get("settings") or {}
    dominio = ajustes.get("catalog_domain") or ""
    return {
        "id": cat_id,
        "nombre": d.get("name", ""),
        "ruta": " > ".join(p.get("name", "") for p in (d.get("path_from_root") or [])),
        "dominio": dominio,
        # Si tiene catalog_domain, el payload usa `family_name` y OMITE `title`
        # (publisher_core.py:361). De ahí el clásico "puse un título y ML
        # publicó otro".
        "es_catalogo": bool(dominio),
    }


def ml_atributos_categoria(cat_id: str) -> dict:
    """Réplica exacta del filtro de `ml_atributos.get_meli_all_attributes`
    (`ml_atributos.py:91-108`), más el crudo que producción no conserva."""
    if cat_id in _cache_cat:
        return _cache_cat[cat_id]
    crudo = http_get_json(f"{ML_API}/categories/{cat_id}/attributes", timeout=20.0)
    principales, secundarias = [], []
    for a in crudo:
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
    res = {
        "principales": principales,
        "secundarias": secundarias,
        "total_ml": len(crudo),
        # SIZE_GRID_ID aparece como atributo SOLO en las categorías que usan
        # guía de tallas. Medido 2026-09-03: está en MLM112156 (Vestidos) y
        # MLM6585 (Tenis), NO en MLM1055 (Celulares). Es el detector exacto.
        "pide_guia_tallas": any(a.get("id") == "SIZE_GRID_ID" for a in crudo),
    }
    _cache_cat[cat_id] = res
    return res


def ml_sugerir_categoria(titulo: str, n: int = 3) -> list[dict]:
    """`domain_discovery` — es un PREDICTOR, no un índice del árbol.

    ⚠️ `limit=1` devuelve `[]` (bug de ML anotado en `crear_producto.py:625`).
    Por eso el mínimo aquí es 3.
    """
    try:
        d = http_get_json(f"{ML_API}/sites/{ML_SITE}/domain_discovery/search",
                          {"q": titulo[:250], "limit": max(3, n)}, timeout=20.0)
        return d if isinstance(d, list) else []
    except Exception:  # noqa: BLE001
        return []


# ═══════════════════════════════════════════════════════════════════════════
# 6 · WOOCOMMERCE — SOLO GET. Aquí no hay ni una escritura.
# ═══════════════════════════════════════════════════════════════════════════

_CAMPOS_WC = ("id,name,sku,type,parent_id,price,regular_price,status,categories,"
              "description,short_description,attributes,meta_data,permalink")


def wc_config() -> tuple[str, str]:
    """Devuelve (base_url, cabecera_authorization). Truena legible si falta algo."""
    url = env("WC_URL")
    clave = env("WC_CONSUMER_KEY") or env("WC_KEY")
    secreto = env("WC_CONSUMER_SECRET") or env("WC_SECRET")
    faltan = [n for n, v in (("WC_URL", url), ("WC_CONSUMER_KEY", clave),
                             ("WC_CONSUMER_SECRET", secreto)) if not v]
    if faltan:
        raise SystemExit(
            "\n[FALTA CONFIGURACIÓN] No puedo leer WooCommerce.\n"
            f"  Faltan estas variables: {', '.join(faltan)}\n\n"
            f"  Ponlas en un archivo .env aquí:  {AQUI / '.env'}\n"
            f"  Hay una plantilla al lado:       {AQUI / '.env.ejemplo'}\n"
            "  (o expórtalas en la terminal antes de correr el script)\n\n"
            "  El .env NO se sube al repositorio: está en el .gitignore de la\n"
            "  carpeta, y el repo es PÚBLICO.\n"
        )
    token = base64.b64encode(f"{clave}:{secreto}".encode("utf-8")).decode("ascii")
    return url.rstrip("/") + "/wp-json/wc/v3", f"Basic {token}"


def wc_producto(sku: str) -> dict | None:
    """El producto de Woo por SKU. GET y nada más."""
    base, auth = wc_config()
    # `_cb` = cache-bust. Regla 5 de la casa: LiteSpeed cachea chunche.shop y
    # ya provocó un revert de imágenes editadas. Aquí no alimenta ninguna
    # escritura, pero leer fresco no cuesta nada.
    datos = http_get_json(f"{base}/products",
                          {"sku": sku, "_fields": _CAMPOS_WC, "_cb": str(int(time.time()))},
                          {"Authorization": auth}, timeout=45.0)
    if not isinstance(datos, list) or not datos:
        return None
    p = datos[0]
    metas = {m.get("key"): m.get("value") for m in (p.get("meta_data") or [])}
    atributos = []
    for a in (p.get("attributes") or []):
        opciones = a.get("options") or []
        valor = ", ".join(str(o) for o in opciones) if isinstance(opciones, list) else str(opciones)
        if a.get("name") and valor:
            atributos.append({"nombre": str(a["name"]), "valor": valor})
    return {
        "sku": p.get("sku") or sku,
        "wc_id": p.get("id"),
        "tipo": p.get("type"),
        "estado": p.get("status"),
        "nombre": p.get("name") or "",
        "descripcion": p.get("description") or p.get("short_description") or "",
        "atributos": atributos,
        "precio": p.get("price") or p.get("regular_price") or None,
        "url": p.get("permalink") or "",
        "metas_ml": {k: v for k, v in metas.items() if isinstance(k, str) and k.startswith("ml_")},
        "metas_attr": {k[len("ml_attr_"):]: v for k, v in metas.items()
                       if isinstance(k, str) and k.startswith("ml_attr_") and v},
    }


# ═══════════════════════════════════════════════════════════════════════════
# 7 · LA IA — DeepSeek primero, Claude de respaldo (mismo orden que producción)
# ═══════════════════════════════════════════════════════════════════════════

def proveedor_disponible() -> str:
    if env("DEEPSEEK_API_KEY"):
        return "deepseek"
    if env("ANTHROPIC_API_KEY"):
        return "claude"
    return ""


def _ia_deepseek(system: str, user: str, max_tokens: int, temperatura: float,
                 json_forzado: bool) -> dict:
    """DeepSeek — API compatible con OpenAI. Mismos parámetros que producción
    (`ml_atributos.py:220-241` para atributos, `ia_generadores.py:39-52` para
    contenido), incluido el reintento en 429 con backoff [10, 20, 10]."""
    base = env("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    modelo = env("DEEPSEEK_MODEL", "deepseek-chat")
    cuerpo: dict[str, Any] = {
        "model": modelo,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": temperatura,
    }
    if json_forzado:
        cuerpo["response_format"] = {"type": "json_object"}
    cabeceras = {"Authorization": "Bearer " + env("DEEPSEEK_API_KEY")}
    backoff = [10, 20, 10]
    for intento in range(len(backoff) + 1):
        try:
            r = http_json_a_ia(f"{base}/chat/completions", cuerpo, cabeceras, 120.0)
            eleccion = r["choices"][0]
            return {"ok": True, "texto": eleccion["message"]["content"],
                    "proveedor": "deepseek", "modelo": modelo,
                    "motivo_corte": eleccion.get("finish_reason") or ""}
        except ErrorHTTP as e:
            if e.codigo == 429 and intento < len(backoff):
                time.sleep(backoff[intento])
                continue
            raise


def _ia_claude(system: str, user: str, max_tokens: int) -> dict:
    """Claude, con el SDK oficial de Anthropic (igual que
    `ia_generadores.py:63-76`). Import perezoso: solo se necesita si de verdad
    se usa este proveedor.

    Ojo: NO se manda `temperature`. En los modelos actuales ese parámetro está
    removido y la API responde 400. Producción tampoco se lo manda a Claude.

    ⚠️ DIVERGENCIA MEDIDA CON PRODUCCIÓN, y por qué. Los `max_tokens` de
    producción (1500 para contenido) están calibrados para DeepSeek. Los
    modelos Claude actuales razonan antes de responder y ese razonamiento
    CUENTA contra `max_tokens`: con 1500 la respuesta salió CORTADA a media
    llave y `_parse_json` devolvió `{}` — medido el 2026-09-03 con
    EST-0054-NEG. Por eso aquí hay un piso de MIN_TOKENS_CLAUDE. El prompt es
    el mismo; lo único que cambia es cuánto espacio se le da para contestar.
    """
    try:
        import anthropic  # import perezoso, como en producción
    except ImportError:
        raise SystemExit(
            "\n[FALTA UNA LIBRERÍA] Vas a usar Claude y no está el SDK oficial.\n"
            "  Instálalo con:  pip install anthropic\n"
            "  (o configura DEEPSEEK_API_KEY, que no necesita ninguna librería)\n"
        ) from None
    modelo = env("ANTHROPIC_MODEL", "claude-opus-5")
    cli = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    msg = cli.messages.create(
        model=modelo,
        max_tokens=max(max_tokens, MIN_TOKENS_CLAUDE),
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    texto = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    return {"ok": True, "texto": texto, "proveedor": "claude", "modelo": modelo,
            "motivo_corte": getattr(msg, "stop_reason", "") or ""}


def pedir_json_a_la_ia(system: str, user: str, max_tokens: int,
                       temperatura: float, json_forzado: bool) -> dict:
    """Pide, parsea y devuelve `{ok, datos, proveedor, modelo, crudo}`."""
    quien = proveedor_disponible()
    if not quien:
        raise SystemExit(
            "\n[FALTA UNA LLAVE DE IA] No hay con qué generar contenido.\n"
            "  Configura UNA de las dos:\n"
            "    DEEPSEEK_API_KEY   (lo que usa producción; vive en Railway)\n"
            "    ANTHROPIC_API_KEY  (el respaldo)\n\n"
            f"  Van en:  {AQUI / '.env'}   (plantilla: .env.ejemplo)\n"
            "  Si solo quieres el semáforo de categoría y tallas, corre con --sin-ia.\n"
        )
    if quien == "deepseek":
        r = _ia_deepseek(system, user, max_tokens, temperatura, json_forzado)
    else:
        r = _ia_claude(system, user, max_tokens)
    datos = _parse_json(r["texto"])
    corte = str(r.get("motivo_corte") or "")
    return {"ok": bool(datos), "datos": datos, "proveedor": r["proveedor"],
            "modelo": r["modelo"], "crudo": r["texto"][:600],
            "motivo_corte": corte,
            # "se acabó el espacio" y "contestó basura" son dos problemas
            # distintos y se arreglan distinto. Que el mensaje lo diga.
            "cortada": corte in ("max_tokens", "length")}


# ═══════════════════════════════════════════════════════════════════════════
# 8 · EL TRABAJO POR SKU
# ═══════════════════════════════════════════════════════════════════════════

def resolver_categoria(prod: dict, forzada: str) -> tuple[str, str, str]:
    """Devuelve (cat_id, origen, nota). La del PANEL manda — regla 2 de la casa.

    Orden: --categoria > `ml_categoria_id` (panel) > `ml_category_id`
    (predictor/costeo) > `domain_discovery` en vivo sobre el título.
    """
    if forzada:
        return forzada.upper(), "forzada_en_la_linea_de_comandos", ""
    metas = prod.get("metas_ml") or {}
    panel = str(metas.get("ml_categoria_id") or "").strip()
    predictor = str(metas.get("ml_category_id") or "").strip()
    if panel:
        nota = ""
        if predictor and predictor != panel:
            nota = (f"El producto tiene DOS categorías y son distintas: panel {panel} "
                    f"vs predictor {predictor}. Se publica con la del panel.")
        return panel, "panel (ml_categoria_id)", nota
    if predictor:
        return predictor, "predictor/costeo (ml_category_id)", (
            "No hay elección humana guardada (`ml_categoria_id` vacío). Esta es la "
            "que adivinó el detector.")
    sugeridas = ml_sugerir_categoria(prod.get("nombre") or "")
    if sugeridas:
        cid = str(sugeridas[0].get("category_id") or "")
        return cid, "domain_discovery EN VIVO", (
            "El producto NO tiene categoría guardada. Esta es una ADIVINANZA del "
            "predictor de ML; el picker del panel manda sobre ella.")
    return "", "ninguna", "Sin categoría no hay atributos de ML ni comisión."


def genero_del_producto(atributos_ia: dict, prod: dict) -> str:
    """Igual que `publisher_core.py:297-302`: primero los `ml_attrs`, luego los
    atributos nativos de Woo. Con la misma limpieza defensiva (a veces llega
    como lista o con corchetes pegados)."""
    v = (atributos_ia.get("GENDER") or atributos_ia.get("gender")
         or (prod.get("metas_attr") or {}).get("GENDER")
         or (prod.get("metas_attr") or {}).get("gender") or "")
    if not v:
        for a in (prod.get("atributos") or []):
            if str(a.get("nombre", "")).strip().lower() in ("gender", "genero", "género"):
                v = a.get("valor") or ""
                break
    if isinstance(v, list) and v:
        v = v[0]
    return str(v).strip().strip("[]'\" ")


def procesar_sku(sku: str, cuenta: str, categoria_forzada: str, sin_ia: bool) -> dict:
    """Todo lo de un SKU. Nunca lanza: los problemas van en `error`/`avisos`."""
    ficha: dict[str, Any] = {
        "sku": sku,
        "generado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version_script": VERSION,
        "extraido_de": EXTRAIDO_DE,
        "error": "",
        "avisos": [],
    }

    prod = wc_producto(sku)
    if not prod:
        ficha["error"] = "No existe ese SKU en WooCommerce (o la tienda no lo devolvió)."
        return ficha
    ficha["woo"] = {
        "wc_id": prod["wc_id"], "tipo": prod["tipo"], "estado": prod["estado"],
        "nombre": prod["nombre"], "url": prod["url"], "precio": prod["precio"],
        "atributos": prod["atributos"],
        "metas_ml": prod["metas_ml"],
        "descripcion_len": len(prod["descripcion"] or ""),
    }
    if prod["tipo"] == "variable":
        ficha["avisos"].append(
            "Es un SKU PADRE (type=variable). En Mercado Libre se publica la "
            "VARIANTE, nunca el padre — revisa que sea el SKU correcto.")

    cat_id, origen, nota = resolver_categoria(prod, categoria_forzada)
    ficha["categoria"] = {"id": cat_id, "origen": origen}
    if nota:
        ficha["avisos"].append(nota)
    if not cat_id:
        ficha["error"] = ("Sin categoría de Mercado Libre. Sin ella no hay lista de "
                          "atributos que pedir ni comisión que calcular: elige una en "
                          "el picker del Estudio.")
        return ficha

    try:
        detalle = ml_categoria(cat_id)
        ficha["categoria"].update(detalle)
    except Exception as exc:  # noqa: BLE001
        ficha["error"] = f"Mercado Libre no reconoció la categoría {cat_id}: {exc}"
        return ficha

    try:
        meli_attrs = ml_atributos_categoria(cat_id)
    except Exception as exc:  # noqa: BLE001
        # Producción tampoco aborta aquí (ml_atributos.py:111-113): sigue con
        # listas vacías. La diferencia es que aquí SÍ se dice.
        meli_attrs = {"principales": [], "secundarias": [], "total_ml": 0,
                      "pide_guia_tallas": False}
        ficha["avisos"].append(
            f"No pude leer los atributos de la categoría ({exc}). La IA va a "
            "trabajar a ciegas y casi nada va a sobrevivir la validación.")

    ficha["categoria"]["atributos_ml"] = {
        "devueltos_por_ml": meli_attrs.get("total_ml", 0),
        "obligatorios": [a["id"] for a in meli_attrs["principales"]],
        "opcionales": [a["id"] for a in meli_attrs["secundarias"]],
        "opcionales_que_ve_la_ia": [a["id"] for a in meli_attrs["secundarias"][:MAX_SECUNDARIAS]],
    }
    ocultos = len(meli_attrs["secundarias"]) - MAX_SECUNDARIAS
    if ocultos > 0:
        ficha["avisos"].append(
            f"La categoría tiene {len(meli_attrs['secundarias'])} atributos opcionales "
            f"y a la IA solo se le muestran {MAX_SECUNDARIAS} (ml_atributos.py:31). "
            f"{ocultos} nunca se le piden.")
    if ficha["categoria"]["es_catalogo"]:
        ficha["avisos"].append(
            f"Categoría DE CATÁLOGO (dominio {ficha['categoria']['dominio']}): al "
            "publicar, el payload usa `family_name` y OMITE el título "
            "(publisher_core.py:361). El título que salga aquí puede no ser el que "
            "se vea en ML.")

    if sin_ia:
        ficha["modo"] = "sin-ia"
        _semaforo_tallas(ficha, meli_attrs, {}, prod, cuenta)
        return ficha

    # ── Llamada 1: contenido (título + descripción) ────────────────────────
    ctx = {
        "nombre": prod["nombre"],
        "categoria": ficha["categoria"].get("ruta") or "",
        "precio": prod["precio"],
        "atributos": prod["atributos"],
        "descripcion": prod["descripcion"],
    }
    user_contenido = (f"Datos del producto:\n{_contexto(ctx)}\n\n"
                      "Mejora el contenido y devuelve SOLO el JSON indicado.")
    try:
        r1 = pedir_json_a_la_ia(SYSTEM_CONTENIDO_ML, user_contenido,
                                MAX_TOKENS_CONTENIDO, 0.7, json_forzado=False)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        ficha["error"] = f"La IA falló al generar el contenido: {exc}"
        return ficha
    if not r1["ok"]:
        ficha["error"] = (
            "La respuesta de la IA se CORTÓ por llegar al tope de tokens, así que "
            "el JSON quedó a medias. Sube el tope (o baja el tamaño de la "
            "descripción de Woo que se le manda)."
            if r1.get("cortada") else
            "La IA no devolvió JSON válido para el contenido.")
        ficha["crudo_contenido"] = r1["crudo"]
        ficha["proveedor"] = r1["proveedor"]
        ficha["modelo"] = r1["modelo"]
        return ficha
    contenido = r1["datos"]
    ficha["proveedor"] = r1["proveedor"]
    ficha["modelo"] = r1["modelo"]
    ficha["contenido"] = {
        "titulo": str(contenido.get("titulo") or "").strip(),
        "descripcion": str(contenido.get("descripcion") or "").strip(),
        # Los atributos de ESTA llamada se descartan a propósito: producción los
        # reemplaza enteros por los reales de la categoría
        # (ia_generadores.py:401-405). Se guardan solo para poder compararlos.
        "atributos_sugeridos_descartados": contenido.get("atributos") or [],
    }
    titulo = ficha["contenido"]["titulo"]
    ficha["contenido"]["titulo_caracteres"] = len(titulo)
    if len(titulo) > 60:
        ficha["avisos"].append(
            f"El título propuesto trae {len(titulo)} caracteres y ML pide 60. El "
            "prompt lo pide pero NADIE lo valida en producción (no existe "
            "`ml_contenido.py`): se aplicaría tal cual.")
    if not titulo:
        ficha["avisos"].append("La IA no devolvió título.")

    # ── Llamada 2: atributos reales de la categoría ────────────────────────
    attrs_actuales = "; ".join(f"{a['nombre']}: {a['valor']}" for a in prod["atributos"])
    user_attrs = build_prompt(
        nombre=prod["nombre"],
        alibaba_titulo=prod["nombre"],           # ia_generadores.py:394 hace lo mismo
        atributos_actuales=attrs_actuales,
        caracteristicas_clave=_sin_html(prod["descripcion"])[:1500],
        meli_attrs=meli_attrs,
        sku=sku,
    )
    try:
        r2 = pedir_json_a_la_ia(SYSTEM_ATRIBUTOS, user_attrs,
                                MAX_TOKENS_ATRIBUTOS, 0.2, json_forzado=True)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        ficha["avisos"].append(f"La IA falló al generar los atributos: {exc}")
        ficha["atributos"] = {}
        _semaforo_tallas(ficha, meli_attrs, {}, prod, cuenta)
        return ficha

    if not r2["ok"]:
        ficha["avisos"].append(
            "La IA NO devolvió atributos usables"
            + (" (la respuesta se cortó por llegar al tope de tokens)."
               if r2.get("cortada") else " (no era JSON válido).")
            + " El producto se queda con BRAND y nada más — es exactamente lo que "
              "pasa en producción cuando esta llamada falla (ml_atributos.py:264-266).")
        ficha["crudo_atributos"] = r2["crudo"]
    crudos = (r2["datos"].get("atributos") or {}) if r2["ok"] else {}
    flags = (r2["datos"].get("flags") or []) if r2["ok"] else []

    # LA VALIDACIÓN — ml_atributos.py:271-274, literal.
    todos = meli_attrs["principales"] + meli_attrs["secundarias"]
    ids_validos = {a["id"] for a in todos} | {"BRAND", "MODEL"}
    atributos = {k: str(v) for k, v in crudos.items() if k in ids_validos and v}
    atributos["BRAND"] = MARCA                              # forzar marca
    atributos_str = _format_atributos(atributos)

    # Lo que producción tira EN SILENCIO. Aquí se dice.
    descartados = {k: str(v) for k, v in crudos.items()
                   if k not in ids_validos or not v}

    ficha["atributos"] = atributos
    ficha["atributos_str"] = atributos_str
    ficha["atributos_num"] = len(atributos)
    ficha["atributos_validos"] = _calc_atributos_validos_str(atributos_str) == "SI"
    ficha["atributos_descartados"] = descartados
    # `flags` = lo que la IA ADMITIÓ no saber. Producción lo calcula y lo tira
    # (crear_producto.py:789, ia_generadores.py:399-405). Es la información
    # más valiosa de este archivo.
    ficha["flags_ia"] = [str(f) for f in flags]

    if descartados:
        ficha["avisos"].append(
            f"{len(descartados)} atributo(s) que inventó la IA no existen en esta "
            f"categoría y se cayeron: {', '.join(sorted(descartados))}")
    faltan = [a["id"] for a in meli_attrs["principales"] if a["id"] not in atributos]
    ficha["obligatorios_sin_llenar"] = faltan
    if faltan:
        ficha["avisos"].append(
            f"Quedaron {len(faltan)} atributo(s) OBLIGATORIO(s) sin llenar: "
            f"{', '.join(faltan)}. Con lista cerrada, al publicar el pipeline mete "
            "el PRIMER valor de la lista (attribute_mapper.py:737).")
    if flags:
        ficha["avisos"].append(
            f"La IA admitió no poder determinar {len(flags)} dato(s) — revísalos a "
            "mano antes de dar por bueno el listing.")

    _revisar_valores_contra_ml(ficha, meli_attrs, atributos)
    _semaforo_tallas(ficha, meli_attrs, atributos, prod, cuenta)
    return ficha


def _revisar_valores_contra_ml(ficha: dict, meli_attrs: dict, atributos: dict) -> None:
    """Corre el matcher difuso del vendor para saber qué valores van a
    sobrevivir al publicar. Producción NO hace esta comprobación aquí: la deja
    para el momento de publicar, donde ya es tarde."""
    porid = {a["id"]: a for a in meli_attrs["principales"] + meli_attrs["secundarias"]}
    obligatorios = {a["id"] for a in meli_attrs["principales"]}
    fuera, ajustados = [], []
    for aid, valor in atributos.items():
        spec = porid.get(aid)
        if not spec or not spec.get("valid_values"):
            continue
        match = _find_value_id(valor, spec["valid_values"])
        if match is None:
            destino = (f"→ ML pondría «{spec['valid_values'][0]}» (el primero de la lista)"
                       if aid in obligatorios else "→ se omitiría")
            fuera.append(f"{aid}=«{valor}» no está en la lista cerrada {destino}")
        elif _normalize(match) != _normalize(valor):
            ajustados.append(f"{aid}: «{valor}» → «{match}»")
    ficha["valores_fuera_de_lista"] = fuera
    ficha["valores_que_el_matcher_ajusta"] = ajustados
    if fuera:
        ficha["avisos"].append("VALORES QUE NO EXISTEN EN ML: " + " · ".join(fuera))


def _semaforo_tallas(ficha: dict, meli_attrs: dict, atributos: dict,
                     prod: dict, cuenta: str) -> None:
    """¿Este SKU va a chocar con GRID_REQUERIDO? Se sabe ANTES de publicar."""
    if not meli_attrs.get("pide_guia_tallas"):
        ficha["guia_tallas"] = {"aplica": False}
        return
    dominio = str(ficha["categoria"].get("dominio") or "").replace("MLM-", "")
    genero = genero_del_producto(atributos, prod)
    por_cuenta = {c: get_chart_id(c, dominio, genero) for c in CUENTAS}
    ficha["guia_tallas"] = {
        "aplica": True, "dominio": dominio, "genero": genero or "(sin GENDER)",
        "cuenta_evaluada": cuenta,
        "chart_id": por_cuenta.get(cuenta),
        "por_cuenta": por_cuenta,
    }
    if not genero:
        ficha["avisos"].append(
            f"GUÍA DE TALLAS: la categoría la exige (dominio {dominio}) y el producto "
            "no tiene atributo GENDER. Sin género no hay llave y `get_chart_id` "
            "devuelve None (size_chart_mapping.py:43-44) → GRID_REQUERIDO.")
    elif not por_cuenta.get(cuenta):
        ficha["avisos"].append(
            f"GUÍA DE TALLAS: falta la guía «{dominio}:{genero}» en {cuenta}. Hay que "
            "crearla en el dashboard de ML de ESA cuenta y registrar su chart_id en "
            "`vendor/ml_ready/size_chart_mapping.py` (esa edición es de producción, "
            "no de aquí).")


# ═══════════════════════════════════════════════════════════════════════════
# 9 · SALIDAS
# ═══════════════════════════════════════════════════════════════════════════

_SEGURO = re.compile(r"[^A-Za-z0-9._-]+")


def ruta_cache(carpeta: pathlib.Path, sku: str) -> pathlib.Path:
    return carpeta / "skus" / (_SEGURO.sub("_", sku) + ".json")


def guardar_json(ruta: pathlib.Path, datos: Any) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


_COLUMNAS = [
    "sku", "wc_id", "estado_woo", "error",
    "categoria_id", "categoria_origen", "categoria_ruta", "dominio", "es_catalogo",
    "titulo_propuesto", "titulo_caracteres", "descripcion_propuesta",
    "atributos_num", "atributos", "obligatorios_sin_llenar",
    "atributos_descartados", "valores_fuera_de_lista", "flags_ia",
    "guia_tallas", "avisos", "proveedor", "modelo", "url_woo",
]


def fila_csv(f: dict) -> dict:
    cat = f.get("categoria") or {}
    woo = f.get("woo") or {}
    con = f.get("contenido") or {}
    gt = f.get("guia_tallas") or {}
    if gt.get("aplica"):
        gtxt = (f"{gt.get('dominio')}:{gt.get('genero')} → "
                + (f"chart {gt.get('chart_id')}" if gt.get("chart_id") else "SIN GUÍA"))
    else:
        gtxt = "no aplica"
    return {
        "sku": f.get("sku", ""),
        "wc_id": woo.get("wc_id", ""),
        "estado_woo": woo.get("estado", ""),
        "error": f.get("error", ""),
        "categoria_id": cat.get("id", ""),
        "categoria_origen": cat.get("origen", ""),
        "categoria_ruta": cat.get("ruta", ""),
        "dominio": cat.get("dominio", ""),
        "es_catalogo": "sí" if cat.get("es_catalogo") else "no",
        "titulo_propuesto": con.get("titulo", ""),
        "titulo_caracteres": con.get("titulo_caracteres", ""),
        "descripcion_propuesta": con.get("descripcion", ""),
        "atributos_num": f.get("atributos_num", ""),
        "atributos": f.get("atributos_str", ""),
        "obligatorios_sin_llenar": ", ".join(f.get("obligatorios_sin_llenar") or []),
        "atributos_descartados": ", ".join(sorted(f.get("atributos_descartados") or {})),
        "valores_fuera_de_lista": " · ".join(f.get("valores_fuera_de_lista") or []),
        "flags_ia": " · ".join(f.get("flags_ia") or []),
        "guia_tallas": gtxt,
        "avisos": " · ".join(f.get("avisos") or []),
        "proveedor": f.get("proveedor", ""),
        "modelo": f.get("modelo", ""),
        "url_woo": woo.get("url", ""),
    }


def escribir_csv(ruta: pathlib.Path, fichas: list[dict]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig para que Excel en Windows no destroce los acentos.
    with ruta.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_COLUMNAS, extrasaction="ignore")
        w.writeheader()
        for f in fichas:
            w.writerow(fila_csv(f))


# ═══════════════════════════════════════════════════════════════════════════
# 10 · CLI
# ═══════════════════════════════════════════════════════════════════════════

def leer_lista(ruta: str) -> list[str]:
    p = pathlib.Path(ruta).expanduser()
    if not p.is_file():
        raise SystemExit(f"\n[NO EXISTE] No encuentro el archivo de SKUs: {p}\n")
    skus = []
    for linea in p.read_text(encoding="utf-8", errors="replace").splitlines():
        linea = linea.split("#", 1)[0].strip().strip(",").strip('"').strip("'")
        if linea:
            skus.append(linea)
    return skus


def verificar() -> int:
    print(f"generar_contenido_ml.py v{VERSION}")
    print(f"Copiado de: {EXTRAIDO_DE}\n")
    ok = True

    print("[1/3] WooCommerce (solo lectura)…")
    try:
        base, auth = wc_config()
        http_get_json(f"{base}/products", {"per_page": 1, "_fields": "id"},
                      {"Authorization": auth}, timeout=45.0)
        print(f"      OK — {base}")
    except SystemExit as e:
        print(str(e))
        ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"      FALLA — {exc}")
        ok = False

    print("[2/3] API pública de Mercado Libre (sin token)…")
    try:
        d = ml_categoria("MLM1055")
        print(f"      OK — MLM1055 = {d['nombre']} ({d['dominio']})")
    except Exception as exc:  # noqa: BLE001
        print(f"      FALLA — {exc}")
        ok = False

    print("[3/3] Proveedor de IA…")
    quien = proveedor_disponible()
    if quien == "deepseek":
        print(f"      OK — DeepSeek, modelo {env('DEEPSEEK_MODEL', 'deepseek-chat')} "
              "(es el que usa producción)")
    elif quien == "claude":
        print(f"      OK — Claude, modelo {env('ANTHROPIC_MODEL', 'claude-opus-5')} "
              "(respaldo; producción usa claude-opus-4-8)")
        try:
            import anthropic  # noqa: F401
        except ImportError:
            print("      FALTA la librería:  pip install anthropic")
            ok = False
    else:
        print("      SIN LLAVE — configura DEEPSEEK_API_KEY o ANTHROPIC_API_KEY "
              "(o corre con --sin-ia)")
        ok = False

    print("\nRecordatorio: este script solo hace GET contra Woo y contra ML. "
          "No publica, no escribe, no cambia nada.")
    return 0 if ok else 1


def main() -> int:
    encontrados = cargar_env()

    ap = argparse.ArgumentParser(
        prog="generar_contenido_ml.py",
        description=("Genera con IA el título, la descripción y los atributos de "
                     "Mercado Libre de una lista de SKUs, y los deja en un JSON y "
                     "un CSV. NO escribe en Woo, kubera, Odoo ni ningún marketplace."),
        epilog=("No hay --dry-run porque este script nunca escribe en un sistema "
                "vivo: fingir esa bandera haría creer que existe un modo que sí "
                "aplica cambios. Para correr sin gastar IA: --sin-ia."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("skus", nargs="*", help="SKUs sueltos")
    ap.add_argument("--archivo", help="archivo con un SKU por línea (# = comentario)")
    ap.add_argument("--salidas", default=str(CAPACIDAD / "salidas"),
                    help="carpeta de salida (default: ../salidas)")
    ap.add_argument("--cuenta", default="BEKURA", choices=list(CUENTAS),
                    help="cuenta para el diagnóstico de guía de tallas")
    ap.add_argument("--categoria", default="",
                    help="fuerza esta categoría de ML (MLM…) para todos los SKUs")
    ap.add_argument("--sin-ia", action="store_true", dest="sin_ia",
                    help="no llama a la IA: solo lee Woo y ML y arma el semáforo")
    ap.add_argument("--rehacer", action="store_true",
                    help="ignora lo ya generado y vuelve a pagarle a la IA")
    ap.add_argument("--pausa", type=float, default=0.0,
                    help="segundos de espera entre SKUs")
    ap.add_argument("--verificar", action="store_true",
                    help="comprueba llaves y conectividad y termina")
    args = ap.parse_args()

    if args.verificar:
        return verificar()

    skus = list(args.skus)
    if args.archivo:
        skus.extend(leer_lista(args.archivo))
    # Deduplica conservando el orden en que los pidió el usuario.
    vistos, lista = set(), []
    for s in skus:
        s = s.strip()
        if s and s.upper() not in vistos:
            vistos.add(s.upper())
            lista.append(s)
    if not lista:
        ap.print_help()
        print("\n[FALTAN SKUS] Pásalos como argumentos o con --archivo.\n")
        return 2

    if args.categoria and not re.fullmatch(r"MLM\d{3,}", args.categoria.upper()):
        raise SystemExit(f"\n[CATEGORÍA INVÁLIDA] «{args.categoria}» no tiene forma de "
                         "categoría de ML. Se espera algo como MLM1055.\n")

    # Que falte una llave se sabe ANTES de leer el primer SKU, no a la mitad.
    wc_config()
    if not args.sin_ia and not proveedor_disponible():
        pedir_json_a_la_ia("", "", 1, 0.0, False)  # solo para lanzar el mensaje bueno

    salidas = pathlib.Path(args.salidas).expanduser().resolve()
    salidas.mkdir(parents=True, exist_ok=True)

    print(f"generar_contenido_ml.py v{VERSION} — {len(lista)} SKU(s)")
    print(f"  copiado de : {EXTRAIDO_DE}")
    print(f"  salidas    : {salidas}")
    print(f"  cuenta     : {args.cuenta} (solo para el diagnóstico de tallas)")
    print(f"  IA         : {'NO (--sin-ia)' if args.sin_ia else (proveedor_disponible() or '—')}")
    if encontrados:
        print(f"  .env leído : {', '.join(str(p) for p in encontrados)}")
    print("  ESTE SCRIPT NO ESCRIBE EN WOO, KUBERA, ODOO NI EN NINGÚN MARKETPLACE.\n")

    fichas: list[dict] = []
    reusados = fallidos = 0
    for i, sku in enumerate(lista, 1):
        cache = ruta_cache(salidas, sku)
        if cache.is_file() and not args.rehacer and not args.sin_ia:
            try:
                ficha = json.loads(cache.read_text(encoding="utf-8"))
                fichas.append(ficha)
                reusados += 1
                print(f"[{i}/{len(lista)}] {sku} — ya estaba hecho, se reusa "
                      f"(--rehacer para volver a pagarle a la IA)")
                continue
            except Exception:  # noqa: BLE001
                pass  # caché corrupta: se regenera
        print(f"[{i}/{len(lista)}] {sku} … ", end="", flush=True)
        t0 = time.time()
        try:
            ficha = procesar_sku(sku, args.cuenta, args.categoria.upper(), args.sin_ia)
        except SystemExit:
            raise           # falta una llave: eso sí para el lote
        except Exception as exc:  # noqa: BLE001
            # UN SKU MALO NUNCA ABORTA EL LOTE.
            ficha = {"sku": sku, "error": f"{type(exc).__name__}: {exc}",
                     "avisos": [], "generado_en":
                         datetime.now(timezone.utc).isoformat(timespec="seconds")}
        fichas.append(ficha)
        if ficha.get("error"):
            fallidos += 1
            print(f"ERROR ({time.time() - t0:.1f}s) — {ficha['error']}")
        else:
            # La caché SOLO guarda fichas hechas CON la IA. Guardar una ficha de
            # `--sin-ia` haría que la siguiente corrida normal la reusara y
            # nunca generara el contenido — un silencio caro.
            if not args.sin_ia:
                guardar_json(cache, ficha)
            t = (ficha.get("contenido") or {}).get("titulo", "")
            resumen = ("leído (sin IA)" if args.sin_ia
                       else f"{ficha.get('atributos_num', 0)} atributos")
            print(f"OK ({time.time() - t0:.1f}s) — {resumen}"
                  + (f" · «{t[:50]}»" if t else "")
                  + (f" · {len(ficha['avisos'])} aviso(s)" if ficha.get("avisos") else ""))
        if args.pausa and i < len(lista):
            time.sleep(args.pausa)

    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta_j = salidas / f"contenido_ml_{sello}.json"
    ruta_c = salidas / f"contenido_ml_{sello}.csv"
    guardar_json(ruta_j, {
        "generado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version_script": VERSION,
        "extraido_de": EXTRAIDO_DE,
        "cuenta_para_tallas": args.cuenta,
        "modo": "sin-ia" if args.sin_ia else "con-ia",
        "totales": {"skus": len(lista), "ok": len(lista) - fallidos,
                    "con_error": fallidos, "reusados_de_cache": reusados},
        "aviso": ("Este archivo es una PROPUESTA. Nada de esto se aplicó en "
                  "WooCommerce ni en Mercado Libre. Aplicarlo se hace desde el "
                  "panel, que registra quién lo hizo."),
        "skus": fichas,
    })
    escribir_csv(ruta_c, fichas)

    con_avisos = sum(1 for f in fichas if f.get("avisos"))
    con_flags = sum(1 for f in fichas if f.get("flags_ia"))
    print(f"\nListo. {len(lista) - fallidos} OK, {fallidos} con error, "
          f"{reusados} reusados de caché.")
    print(f"  {con_avisos} SKU(s) con avisos · {con_flags} con `flags` de la IA "
          "(datos que ella misma admitió no saber)")
    print(f"  JSON : {ruta_j}")
    print(f"  CSV  : {ruta_c}")
    print("\nNada de esto se aplicó. Para aplicarlo, el panel.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrumpido. Lo que ya se generó está en salidas/skus/.")
        raise SystemExit(130)
