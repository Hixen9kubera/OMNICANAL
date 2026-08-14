# -*- coding: utf-8 -*-
"""
walmart_contenido.py — prompts y VALIDADORES para el contenido de Walmart MX.

Hermano de `temu_contenido.py` y `tiktok_contenido.py`, con las mismas dos
reglas de la casa:

  1. **El prompt pide; el código garantiza.** Un modelo inventa un valor por
     más instrucciones que lleve. Todo lo que sale de la IA se comprueba contra
     la lista cerrada de la categoría antes de acercarse a un feed.
  2. **Un dato no confirmado NO se publica.** El prompt marca lo que le falta
     con `[FALTA DATO]`; `validar_*` lo saca del payload en vez de mandarlo.
     En Walmart un dato inventado no da error: publica y nadie se entera hasta
     que un cliente reclama.

DE DÓNDE SALEN LOS ATRIBUTOS OBLIGATORIOS
-----------------------------------------
En TikTok se piden por API. **En Walmart MX esa API no existe** — `POST
/v3/items/spec` da 404 con credenciales MX y en Global está marcada "US only",
así que no llega ni migrando. La fuente es el esquema público
`MX_MP_ITEM_INTL_SPEC.json` (3.9 MB, HTTP 200 sin credenciales): 75 categorías,
3,326 campos, **445 obligatorios**, 1,334 con lista cerrada, todos con su
etiqueta en español — que es la que Walmart usa al reclamar.

⚠️ El archivo es la **3.19** y producción corre la **3.11**. Donde chocan, manda
lo medido: `scripts/walmart_field_requirements.CORRECCIONES_MEDIDAS`.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import unicodedata
from typing import Any

# ═════════════════════════════════════════════════════════════════════════════
# LÍMITES — todos del esquema oficial salvo donde se diga
# ═════════════════════════════════════════════════════════════════════════════
TITULO_MAX = 200            # `productName.maxLength` — cierra el pendiente #7
                            # del manual, que lo tenía como [SUPUESTO]
TITULO_IDEAL = (50, 75)     # regla de negocio de Kubera, no del esquema
DESCRIPCION_MAX = 4000      # `shortDescription.maxLength`
BULLET_MAX = 50             # "oraciones breves de 50 caracteres" (literal)
BULLETS_MIN = 3             # "Recomendamos encarecidamente utilizar mínimo tres"
BULLETS_MAX = 8             # regla de negocio de Kubera
MARCA_MAX = 60              # `brand.maxLength` / `manufacturer.maxLength`
INCLUIDOS_MAX = 6000        # `itemsIncluded.maxLength`

# Marcador que el prompt usa cuando NO sabe un dato. Lo que venga marcado así
# se descarta: es la diferencia entre un hueco y una mentira.
FALTA = "[FALTA DATO"

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️←-⇿⬀-⯿]")

# ─────────────────────────────────────────────────────────────────────────────
# FRASES QUE WALMART PENALIZA — pueden INACTIVAR el producto
#
# ⚠️ PROCEDENCIA: son las que Brandon trae del equipo de contenido de Walmart.
# NO están en ninguna página pública que hayamos podido leer, así que van
# marcadas como regla de negocio, no como cita documental. Ampliar la lista es
# barato; descubrir una inactivación no lo es.
# ─────────────────────────────────────────────────────────────────────────────
_PENALIZADAS = {
    # promesa absoluta / resultado garantizado
    "garantizado", "garantizada", "100% efectivo", "100% efectiva",
    "resultados garantizados", "efecto inmediato", "resultado inmediato",
    "efecto instantaneo", "arte de magia", "milagroso", "milagrosa",
    "infalible", "sin fallas", "de por vida",
    # superlativo no comprobable — van los PLURALES y los femeninos también:
    # con solo "el mejor" en la lista, "Los Mejores Audífonos del Mercado"
    # pasaba limpio.
    "el mejor", "la mejor", "los mejores", "las mejores", "lo mejor",
    "el peor", "el unico", "la unica", "los unicos", "las unicas",
    "unico en el mercado", "unica en el mercado",
    "numero 1", "#1", "no. 1", "insuperable", "inigualable", "incomparable",
    "el mas vendido", "la mas vendida", "los mas vendidos",
    "el mas potente", "la mas potente", "el mas resistente",
    "el mas duradero", "de la mas alta calidad", "calidad insuperable",
    # claim médico
    "cura", "curativo", "sana", "adelgaza", "elimina la grasa",
    "quema grasa", "previene enfermedades", "tratamiento medico",
    "aprobado por la fda", "antibacterial 99.9%",
    # promoción / precio en el contenido
    "oferta", "descuento", "envio gratis", "gratis", "promocion",
    "liquidacion", "barato", "mas barato",
}


def _sin_acentos(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower())
                   if unicodedata.category(c) != "Mn")


_PENALIZADAS_RE = None


def _penalizadas(texto: str) -> list[str]:
    """Frases penalizadas presentes, con LÍMITE DE PALABRA.

    Buscarlas como subcadena produce falsos positivos que bloquean productos
    legítimos: "cura" caza dentro de *oscura*, *procura* y *curva de nivel*;
    "gratis" dentro de *gratis­imo*. Medido sobre los 244 de Electrónicos: la
    versión por subcadena marcaba 4 artículos, y **3 eran falsos positivos**.
    """
    global _PENALIZADAS_RE
    if _PENALIZADAS_RE is None:
        # \b no sirve con frases que llevan '#' o '.', así que el límite se
        # arma a mano: inicio/fin de cadena o algo que no sea letra ni dígito.
        alt = "|".join(sorted((re.escape(f) for f in _PENALIZADAS),
                              key=len, reverse=True))
        _PENALIZADAS_RE = re.compile(rf"(?<![a-z0-9])(?:{alt})(?![a-z0-9])")
    return sorted(set(_PENALIZADAS_RE.findall(_sin_acentos(texto))))


def _palabras(t: str) -> set[str]:
    """Palabras significativas, para comprobar que el título sigue siendo del
    MISMO producto. Se ignoran las de 3 letras o menos."""
    return {w for w in re.findall(r"[a-z0-9áéíóúñ]+", (t or "").lower())
            if len(w) > 3}


# ═════════════════════════════════════════════════════════════════════════════
# EL CATÁLOGO DE CAMPOS — leído del esquema una sola vez
# ═════════════════════════════════════════════════════════════════════════════
_CANDIDATOS = [
    os.getenv("WM_SPEC_JSON", ""),
    r"C:\Users\diaz2\OneDrive\Escritorio\respaldo_payloads_20260812\MX_MP_ITEM_INTL_SPEC.json",
    "MX_MP_ITEM_INTL_SPEC.json",
]
_cache: dict[str, Any] = {}


def _spec() -> dict:
    if "spec" not in _cache:
        for c in _CANDIDATOS:
            p = pathlib.Path(c) if c else None
            if p and str(p) and p.is_file():
                _cache["spec"] = json.loads(p.read_text(encoding="utf-8"))
                break
        else:
            raise RuntimeError(
                "Falta MX_MP_ITEM_INTL_SPEC.json — bájalo de "
                "https://developer.walmart.com/file/mp/mx/MX_MP_ITEM_INTL_SPEC.json "
                "o apunta WM_SPEC_JSON a su ruta")
    return _cache["spec"]


def campos(categoria: str) -> dict[str, dict]:
    """Todos los campos del bloque `Visible` de esa categoría."""
    vis = (_spec()["properties"]["MPItem"]["items"]["properties"]
           ["Visible"]["properties"])
    g = vis.get(categoria)
    if g is None:
        raise KeyError(
            f"'{categoria}' no existe en el esquema. Ojo con los acentos: la "
            f"clave es la etiqueta EN ESPAÑOL exacta. Si le pegas mal, Walmart "
            f"cae en un spec genérico y pide atributos absurdos — ese es el "
            f"síntoma de categoría equivocada.")
    return g.get("properties") or {}


def obligatorios(categoria: str) -> list[str]:
    """Obligatorios según el esquema MÁS los que producción exige de más."""
    vis = (_spec()["properties"]["MPItem"]["items"]["properties"]
           ["Visible"]["properties"])
    req = list((vis.get(categoria) or {}).get("required") or [])
    for (cat, campo), (veredicto, _) in _correcciones().items():
        if cat == categoria and veredicto == "OBLIGATORIO" and campo not in req:
            req.append(campo)
    return req


def rechazados(categoria: str) -> set[str]:
    """Campos que el esquema lista y producción RECHAZA. Nunca se mandan."""
    return {campo for (cat, campo), (v, _) in _correcciones().items()
            if cat == categoria and v == "RECHAZADO"}


def _correcciones() -> dict:
    if "corr" not in _cache:
        try:
            from scripts.walmart_field_requirements import CORRECCIONES_MEDIDAS
            _cache["corr"] = CORRECCIONES_MEDIDAS
        except Exception:  # noqa: BLE001 — el servicio no depende del script
            _cache["corr"] = {}
    return _cache["corr"]


def valores(categoria: str, campo: str) -> list[str]:
    """La lista cerrada de ese campo, si la tiene."""
    d = campos(categoria).get(campo) or {}
    if d.get("enum"):
        return list(d["enum"])
    it = d.get("items") or {}
    if it.get("enum"):
        return list(it["enum"])
    return []


def _describe(categoria: str, campo: str) -> str:
    d = campos(categoria).get(campo) or {}
    et = d.get("title") or campo
    tipo = d.get("type") or "string"
    vals = valores(categoria, campo)
    linea = f'  · {campo} — «{et}»'
    if vals:
        linea += f"\n      LISTA CERRADA, copia uno EXACTO: {' | '.join(map(str, vals))}"
    else:
        desc = (d.get("description") or (d.get("items") or {}).get("description") or "")
        if desc:
            linea += f"\n      {desc[:180].strip()}"
        ej = d.get("examples")
        if ej:
            linea += f"\n      ejemplo: {ej}"
        if tipo == "array":
            linea += "\n      (lista: devuelve un arreglo de textos)"
    return linea


# ═════════════════════════════════════════════════════════════════════════════
# PROMPT 1 — TÍTULO, DESCRIPCIÓN Y BENEFICIOS
# ═════════════════════════════════════════════════════════════════════════════
def build_prompt_contenido(*, sku: str, categoria: str, titulo_woo: str,
                           descripcion_woo: str, marca: str = "",
                           atributos_conocidos: dict | None = None,
                           keywords: list[str] | None = None) -> str:
    atrs = "\n".join(f"    {k}: {v}" for k, v in (atributos_conocidos or {}).items()
                     if v) or "    (ninguno confirmado)"
    kw = ", ".join(keywords or []) or "(ninguna)"
    return f"""Actúa como especialista en optimización de listados (content \
merchandising) para Walmart Marketplace México. Te doy información cruda de un \
producto y la reescribes siguiendo ESTRICTAMENTE las reglas de abajo.

NO INVENTES DATOS TÉCNICOS que no te dé (medidas, materiales, certificaciones,
potencias, capacidades). Si falta un dato, escribe "[FALTA DATO: qué falta]" en
vez de inventarlo. En Walmart un dato inventado NO da error: se publica y nadie
se entera hasta que un cliente reclama.

PRODUCTO
    SKU:                {sku}
    Categoría Walmart:  {categoria}
    Nombre/marca hoy:   {marca or '(sin marca)'}
    Título hoy:         {titulo_woo}
    Ficha cruda:        {descripcion_woo[:1800]}
    Atributos conocidos:
{atrs}
    Palabras clave:     {kw}

1 · TÍTULO
   · Entre {TITULO_IDEAL[0]} y {TITULO_IDEAL[1]} caracteres. Tope duro {TITULO_MAX}.
   · Estructura: [Marca] + [Artículo] + [Característica o material] + [Modelo/tamaño/color]
       Tecnología: "Marca Audífonos Inalámbricos Bluetooth Cancelación de Ruido Negro"
       Hogar:      "Marca Set de 4 Sillas para Comedor de Madera Tapizadas en Gris"
       Belleza:    "Marca Crema Corporal Hidratante con Vitamina E 400 ml"
   · Sin MAYÚSCULAS sostenidas, sin emojis, sin símbolos promocionales (¡Oferta!, %).
   · NO repitas la categoría dentro del título si es redundante.
   · NADA de keyword stuffing: cada palabra debe aportar información real
     (marca, atributo, tamaño, color). Repetir para "ganar" búsquedas se penaliza.
   · Si no hay marca reconocida, usa el fabricante o "Sin marca".
   · Escribe como busca un comprador mexicano, no como habla un catálogo chino.

2 · DESCRIPCIÓN
   · UN PÁRRAFO corrido. Nada de viñetas, nada de HTML, nada de saltos de línea.
   · Detalla usos, funcionalidades y características. 100% sobre el producto.
   · Mete las palabras clave de forma natural, sin relleno artificial.
   · Máximo {DESCRIPCION_MAX} caracteres.

3 · CARACTERÍSTICAS
   · Especificaciones técnicas OBJETIVAS y verificables. Frases cortas.
   · No son beneficios ni opiniones. Si no tienes el dato, no lo pongas.

4 · BENEFICIOS (van como viñetas en la página)
   · Entre {BULLETS_MIN} y {BULLETS_MAX} viñetas. **Máximo {BULLET_MAX} caracteres cada una.**
   · Qué GANA el cliente al usarlo, no la ficha técnica repetida.
   · DEBEN ser distintas del título y NO repetir la descripción (lo exige Walmart).
   · PROHIBIDO —puede INACTIVAR el producto—: "Garantizado", "Efecto inmediato",
     "Funciona como por arte de magia", cualquier promesa absoluta, médica o de
     resultado no verificable, y superlativos no comprobables ("el mejor",
     "único en el mercado"). Tampoco precios, ofertas ni envíos.

SALIDA — SOLO JSON, sin texto alrededor:
{{
  "titulo": "<{TITULO_IDEAL[0]}-{TITULO_IDEAL[1]} caracteres>",
  "descripcion": "<un párrafo>",
  "caracteristicas": ["<especificación objetiva>", "..."],
  "beneficios": ["<máx {BULLET_MAX} caracteres>", "..."],
  "palabras_clave": ["<lo que teclearía un comprador mexicano>"],
  "marca": "<marca o 'Sin marca'>",
  "confianza": 0.0,
  "flags": ["<qué dato NO pudiste confirmar>"]
}}"""


# ═════════════════════════════════════════════════════════════════════════════
# PROMPT 2 — LOS ATRIBUTOS DE LA CATEGORÍA
# ═════════════════════════════════════════════════════════════════════════════
def build_prompt_atributos(*, sku: str, categoria: str, titulo: str,
                           descripcion: str, atributos_woo: dict | None = None,
                           incluir_opcionales: int = 12) -> str:
    """Pide los obligatorios de esa categoría y hasta N opcionales.

    Los opcionales NO son adorno: `offerScore` y `contentScore` de la Listing
    Quality API miden qué tan completa está la ficha, y una ficha pelona se
    entierra en los resultados de búsqueda.
    """
    obl = obligatorios(categoria)
    veto = rechazados(categoria)
    todos = campos(categoria)
    # Los opcionales que valen la pena: los de texto/lista que describen el
    # producto. Se dejan fuera los de variante y los de imagen, que no son
    # contenido y los arma el publicador.
    fuera = {"variantGroupId", "variantAttributeNames", "isPrimaryVariant",
             "swatchImages", "productVideo"}
    opc = [c for c in todos
           if c not in obl and c not in veto and c not in fuera][:incluir_opcionales]

    bloque_obl = "\n".join(_describe(categoria, c) for c in obl if c not in veto)
    bloque_opc = "\n".join(_describe(categoria, c) for c in opc)
    atrs = "\n".join(f"    {k}: {v}" for k, v in (atributos_woo or {}).items()
                     if v) or "    (ninguno)"

    return f"""Eres catalogador de productos para Walmart México. Llenas los \
atributos de ficha de UN producto, con lo que te doy. No inventas.

PRODUCTO
    SKU:         {sku}
    Categoría:   {categoria}
    Título:      {titulo}
    Descripción: {descripcion[:1200]}
    Atributos que ya trae el catálogo:
{atrs}

OBLIGATORIOS de esta categoría — si falta alguno, Walmart RECHAZA el artículo:
{bloque_obl or '  (ninguno propio de la categoría)'}

OPCIONALES — llénalos SOLO si el dato está en la información de arriba.
Cada uno que llenes con verdad mejora el posicionamiento del producto:
{bloque_opc or '  (ninguno)'}

REGLAS
1. Donde diga LISTA CERRADA, copia un valor EXACTO de esa lista, con sus
   acentos y mayúsculas. Un valor fuera de la lista tumba el artículo entero.
2. Si un obligatorio no se puede deducir de la información dada, ponlo en
   `flags` en vez de inventarlo. Es preferible que no publique a que publique
   mintiendo.
3. NO llenes un opcional "por llenarlo". Un dato inventado se publica sin dar
   error y nadie se entera hasta que un cliente reclama.
4. Las medidas van con su número y su unidad por separado, nunca "10 cm" junto.

SALIDA — SOLO JSON:
{{
  "atributos": {{"<campo>": "<valor>", "<campo_medida>": {{"measure": 0.0, "unit": "cm"}}}},
  "confianza": 0.0,
  "flags": ["<campo>: por qué no se pudo determinar"]
}}"""


# ═════════════════════════════════════════════════════════════════════════════
# VALIDADORES — la garantía, no el prompt
# ═════════════════════════════════════════════════════════════════════════════
def validar_contenido(contenido: dict[str, Any], titulo_original: str = ""
                      ) -> tuple[dict, list[str]]:
    """Devuelve (contenido, problemas). NO corrige por su cuenta: reporta."""
    problemas: list[str] = []
    c = dict(contenido or {})

    t = (c.get("titulo") or "").strip()
    if not t:
        problemas.append("titulo: vacío")
    else:
        if len(t) > TITULO_MAX:
            problemas.append(f"titulo: {len(t)} caracteres, tope duro {TITULO_MAX}")
        elif not (TITULO_IDEAL[0] <= len(t) <= TITULO_IDEAL[1]):
            problemas.append(
                f"titulo: {len(t)} caracteres, fuera del rango "
                f"{TITULO_IDEAL[0]}-{TITULO_IDEAL[1]}")
        if _EMOJI.search(t):
            problemas.append("titulo: lleva emojis")
        if len(re.findall(r"[A-ZÁÉÍÓÚÑ]{4,}", t)) >= 3:
            problemas.append("titulo: mayúsculas sostenidas")
        for p in _penalizadas(t):
            problemas.append(f"titulo: frase penalizada '{p}'")
        if FALTA in t:
            problemas.append("titulo: trae un [FALTA DATO] sin resolver")
        # ── LA COMPROBACIÓN QUE MÁS IMPORTA ────────────────────────────
        # Un título puede quedar impecable de forma y describir OTRO producto.
        # Caso real en TikTok: un cono veterinario acabó en "Joyas para
        # disfraces", con confianza y sin error. Si la propuesta no comparte ni
        # una palabra con el original, es más barato quedarse con el feo.
        if titulo_original and not (_palabras(t) & _palabras(titulo_original)):
            problemas.append(
                "titulo: no comparte NINGUNA palabra con el original — "
                "puede estar describiendo otro producto")

    d = (c.get("descripcion") or "").strip()
    if not d:
        problemas.append("descripcion: vacía")
    if len(d) > DESCRIPCION_MAX:
        problemas.append(f"descripcion: {len(d)} caracteres, máximo {DESCRIPCION_MAX}")
    if "<" in d and ">" in d:
        problemas.append("descripcion: trae HTML (Walmart la quiere en párrafo plano)")
    if re.search(r"^\s*[-•*]\s", d, re.M):
        problemas.append("descripcion: trae viñetas (debe ser UN párrafo)")
    for p in _penalizadas(d):
        problemas.append(f"descripcion: frase penalizada '{p}'")
    if FALTA in d:
        problemas.append("descripcion: trae un [FALTA DATO] sin resolver")

    bene = c.get("beneficios") or []
    if isinstance(bene, str):
        bene = [b.strip() for b in bene.split("\n") if b.strip()]
        c["beneficios"] = bene
    if len(bene) < BULLETS_MIN:
        problemas.append(f"beneficios: {len(bene)}, mínimo {BULLETS_MIN}")
    if len(bene) > BULLETS_MAX:
        problemas.append(f"beneficios: {len(bene)}, máximo {BULLETS_MAX}")
    for i, b in enumerate(bene, 1):
        b = (b or "").strip()
        if len(b) > BULLET_MAX:
            problemas.append(f"beneficio {i}: {len(b)} caracteres, máximo {BULLET_MAX}")
        if _EMOJI.search(b):
            problemas.append(f"beneficio {i}: lleva emojis")
        for p in _penalizadas(b):
            problemas.append(f"beneficio {i}: frase penalizada '{p}'")
        if FALTA in b:
            problemas.append(f"beneficio {i}: trae un [FALTA DATO] sin resolver")
        # Regla LITERAL del esquema: "Deben de ser diferentes al Título del
        # Producto y no repetirse en la Descripción".
        if b and t and _sin_acentos(b) == _sin_acentos(t):
            problemas.append(f"beneficio {i}: es idéntico al título")
        if b and d and _sin_acentos(b) in _sin_acentos(d):
            problemas.append(f"beneficio {i}: se repite dentro de la descripción")

    if (c.get("marca") or "") and len(c["marca"]) > MARCA_MAX:
        problemas.append(f"marca: {len(c['marca'])} caracteres, máximo {MARCA_MAX}")
    return c, problemas


def validar_atributos(propuesta: dict[str, Any], categoria: str
                      ) -> tuple[dict, list[str], list[str]]:
    """Filtra lo propuesto contra el esquema REAL de la categoría.

    Devuelve (atributos_válidos, rechazos, faltantes_obligatorios).

    ESTA FUNCIÓN ES LA GARANTÍA. El modelo puede copiar mal un valor de lista
    cerrada por más que el prompt insista; aquí se comprueba y lo que no cuadra
    NO se publica.
    """
    defs = campos(categoria)
    veto = rechazados(categoria)
    validos: dict[str, Any] = {}
    rechazos: list[str] = []

    for campo, valor in (propuesta.get("atributos") or {}).items():
        # El veto MEDIDO se comprueba ANTES que la existencia en el esquema.
        # Si no, un campo que producción rechaza y el esquema tampoco lista
        # (como `modelNumber` en Electrónicos) sale con un "no existe" genérico
        # y se pierde la evidencia de por qué está prohibido — que es la parte
        # que evita que alguien lo vuelva a meter.
        if campo in veto:
            rechazos.append(
                f"{campo}: producción lo RECHAZA "
                f"('{campo}' is not a valid field) — tumbaría el lote entero")
            continue
        if campo not in defs:
            rechazos.append(f"{campo}: no existe en «{categoria}»")
            continue
        if valor in (None, "", [], {}):
            continue
        if isinstance(valor, str) and FALTA in valor:
            rechazos.append(f"{campo}: la IA lo marcó [FALTA DATO]")
            continue

        d = defs[campo]
        cerrada = valores(categoria, campo)
        if cerrada:
            # Se aceptan escalares o listas; cada elemento debe ser EXACTO.
            crudos = valor if isinstance(valor, list) else [valor]
            buenos = [v for v in crudos if v in cerrada]
            malos = [v for v in crudos if v not in cerrada]
            for m in malos:
                rechazos.append(
                    f"{campo}: '{m}' no está en la lista cerrada "
                    f"({len(cerrada)} valores)")
            if not buenos:
                continue
            validos[campo] = buenos if d.get("type") == "array" else buenos[0]
            continue

        if d.get("type") == "object" and "measure" in (d.get("properties") or {}):
            if not isinstance(valor, dict) or "measure" not in valor:
                rechazos.append(f"{campo}: se esperaba {{measure, unit}}")
                continue
            try:
                medida = round(float(valor["measure"]), 2)   # Walmart: máx 2 decimales
            except (TypeError, ValueError):
                rechazos.append(f"{campo}: measure '{valor.get('measure')}' no es número")
                continue
            unidades = ((d["properties"].get("unit") or {}).get("enum")) or []
            unidad = valor.get("unit")
            if unidades and unidad not in unidades:
                rechazos.append(
                    f"{campo}: unidad '{unidad}' no válida ({' | '.join(unidades)})")
                continue
            validos[campo] = {"measure": medida, "unit": unidad}
            continue

        if d.get("type") == "integer":
            try:
                validos[campo] = int(valor)
            except (TypeError, ValueError):
                rechazos.append(f"{campo}: '{valor}' no es entero")
            continue

        if d.get("type") == "array":
            validos[campo] = valor if isinstance(valor, list) else [valor]
            continue

        texto = str(valor).strip()
        tope = d.get("maxLength")
        if tope and len(texto) > tope:
            rechazos.append(f"{campo}: {len(texto)} caracteres, máximo {tope}")
            continue
        validos[campo] = texto

    pendientes = [c for c in obligatorios(categoria)
                  if c not in veto and c not in validos]
    return validos, rechazos, pendientes
