"""
packing_sku.py — Homologación de SKU de un packing list con LLM.

El xlsx del contenedor NO trae SKU: solo descripciones en chino/inglés que el
proveedor escribe distinto en cada renglón. Este módulo convierte esas
descripciones en el SKU de Kubera (``SUBCAT-####-ATRIBUTO``), agrupando las
variantes del mismo producto bajo un mismo número.

Pipeline (portado de kubera/costos/app.py, validado contra ~60 contenedores):

  A. Deduplicar por par ``(inglés, chino)`` — un contenedor de 1000 renglones
     suele tener 150 pares únicos, así que esto solo ya baja el costo ~7x.
  B. DeepSeek clasifica cada par único (concurrencia 20): traduce, extrae el
     ``base_product`` sin modificadores, el ``variant_tag`` y la subcategoría.
  C. Cluster local por ``base_product`` normalizado → un concepto por producto.
  D. Gemini Flash compara las FOTOS de los conceptos cuyos labels se parecen
     (``difflib > 0.7``): el proveedor escribe "silla plegable" y "silla
     plegable de metal" para el mismo producto, y solo la imagen lo desambigua.
     Si dice "same" o "variant", los conceptos se fusionan.
  E. Asignación de SKU con :mod:`packing_taxonomia`: un número por concepto,
     tomado del contador sembrado con Odoo, y el atributo solo si el concepto
     tiene 2+ variantes distintas (un "NEGRO" solitario no es una variante).

Divergencias deliberadas respecto de ``costos/app.py``:

  - Aquel numera con ``{últimos 4 del contenedor}-{serial}``; aquí el SKU es el
    de Odoo (``SUBCAT-####-ATRIBUTO``), así que DeepSeek también devuelve la
    subcategoría y el numerador sale de :class:`packing_taxonomia.Contadores`.
  - Aquel usa el paquete ``openai`` y ``google-genai``; aquí se llama por HTTP
    crudo con ``httpx``, como el resto del proyecto (ver ``ia_generadores.py`` e
    ``imagenes_editor.py``), para no añadir dependencias.
  - En el merge visual se acepta ``variant`` además de ``same``: dos conceptos
    con label parecido que la foto declara "mismo producto, distinto color" son
    exactamente el caso que queremos unir bajo un número con dos atributos.
"""
from __future__ import annotations

import asyncio
import base64
import difflib
import json
import logging
import re
import unicodedata
from typing import Any, Callable, Iterable

import httpx

from config import settings
from services import packing_taxonomia as tax

log = logging.getLogger("omnicanal.packing.sku")

DEEPSEEK_MODEL = "deepseek-chat"
GEMINI_MODEL = "gemini-2.5-flash"
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

_CONCURRENCIA_DEEPSEEK = 20
_UMBRAL_SIMILITUD = 0.7   # difflib sobre los labels; debajo de esto ni se pregunta
_MAX_PARES_VISION = 20    # tope de llamadas a Gemini por packing list

Progreso = Callable[[str, int, int], None] | None


# ═══════════════════════════════════════════════════════════════════════════════
# Paso B — DeepSeek: descripción → base_product / variant_tag / subcategoría
# ═══════════════════════════════════════════════════════════════════════════════
def _catalogo_subcats() -> str:
    return "\n".join(f"  {cod} = {nombre}" for cod, nombre in tax.SUBCATEGORIAS.items())


_PROMPT_CLASIFICAR = """Analiza este producto de un packing list de importación china.

Descripción inglés: {eng}
Descripción chino: {chn}

Devuelve SOLO este JSON:
{{
  "traduccion": "<traducción literal del chino al español>",
  "nombre_es": "<nombre comercial en español, 3-8 palabras, para vender en México>",
  "base_product": "<producto base, 3-7 palabras, SIN modificadores de variante>",
  "variant_tag": "<tag en MAYÚSCULAS o null>",
  "subcategoria": "<código de la lista de abajo>",
  "rationale": "<una frase corta>"
}}

Reglas:
- base_product DEBE SER IDÉNTICO para todas las variantes del mismo producto.
  Ej: "笔记本红色S" y "笔记本蓝色L" → ambos base_product = "cuaderno"
- variant_tag se extrae de los modificadores, normalmente en CHINO:
  • Tallas:    S, M, L, XL, 大→L, 中→M, 小→S
  • Colores:   黑→BLACK, 白→WHITE, 红→RED, 蓝→BLUE, 绿→GREEN, 黄→YELLOW,
               灰→GRAY, 粉→PINK, 紫→PURPLE, 棕→BROWN, 米→BEIGE
  • Voltaje:   110V, 220V     • Capacidad: 6L, 10L, 20L
  • Modelos:   A1, A2, B款
- Si hay MÚLTIPLES modificadores, combínalos con guión: "RED-S"
- Si NO hay modificador de variante, variant_tag = null
- subcategoria: elige EXACTAMENTE UN código de esta lista. Si ninguno encaja, usa VAR.

{catalogo}"""


def _clasificacion_vacia(motivo: str) -> dict[str, Any]:
    return {"traduccion": "", "nombre_es": "", "base_product": "?",
            "variant_tag": None, "subcategoria": tax.SUBCAT_DEFAULT,
            "rationale": motivo}


def _parse_json(texto: str) -> dict[str, Any]:
    """Igual que ia_generadores._parse_json: tolera ```json ... ``` y prosa alrededor."""
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


async def _clasificar_uno(
    cli: httpx.AsyncClient, sem: asyncio.Semaphore, clave: tuple[str, str],
) -> tuple[tuple[str, str], dict[str, Any]]:
    eng, chn = clave
    prompt = _PROMPT_CLASIFICAR.format(
        eng=eng or "(vacío)", chn=chn or "(vacío)", catalogo=_catalogo_subcats(),
    )
    async with sem:
        for intento in range(3):
            try:
                r = await cli.post(
                    f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                    json={
                        "model": DEEPSEEK_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.1,
                        "max_tokens": 400,
                    },
                    timeout=90.0,
                )
                if r.status_code == 429:
                    await asyncio.sleep(2 + intento * 3)
                    continue
                r.raise_for_status()
                data = _parse_json(r.json()["choices"][0]["message"]["content"])
                if not data:
                    raise ValueError("respuesta no parseable")
                return clave, data
            except Exception as exc:  # noqa: BLE001
                if intento == 2:
                    log.warning("DeepSeek falló para %r: %s", chn[:30] or eng[:30], exc)
                    return clave, _clasificacion_vacia(f"error: {exc}")
                await asyncio.sleep(1 + intento)
    return clave, _clasificacion_vacia("sin intentos")


async def _clasificar_todos(
    claves: list[tuple[str, str]], progreso: Progreso = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    if not claves:
        return {}
    if not settings.deepseek_api_key:
        log.warning("DEEPSEEK_API_KEY no configurada: la homologación queda sin LLM.")
        return {k: _clasificacion_vacia("sin DEEPSEEK_API_KEY") for k in claves}

    sem = asyncio.Semaphore(_CONCURRENCIA_DEEPSEEK)
    salida: dict[tuple[str, str], dict[str, Any]] = {}
    async with httpx.AsyncClient() as cli:
        tareas = [asyncio.ensure_future(_clasificar_uno(cli, sem, k)) for k in claves]
        for i, fut in enumerate(asyncio.as_completed(tareas), start=1):
            clave, data = await fut
            salida[clave] = data
            if progreso:
                progreso("clasificando", i, len(claves))
    return salida


# ═══════════════════════════════════════════════════════════════════════════════
# Paso C — Cluster local por base_product
# ═══════════════════════════════════════════════════════════════════════════════
def _normalizar_label(s: str) -> str:
    """Minúsculas, sin acentos ni puntuación: 'Silla Plegable.' == 'silla plegable'."""
    s = unicodedata.normalize("NFKD", (s or "").strip().lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9一-鿿]+", " ", s).strip()


def _tag_limpio(tag: Any) -> str | None:
    if not isinstance(tag, str):
        return None
    t = tag.strip().upper()
    return t or None


def _agrupar(
    clasificacion: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """``{concepto_id: {label, subcats, variantes, miembros}}`` por base_product."""
    por_base: dict[str, str] = {}
    conceptos: dict[str, dict[str, Any]] = {}
    serial = 1
    for clave, data in clasificacion.items():
        base = data.get("base_product") or "?"
        norm = _normalizar_label(base) or "?"
        if norm not in por_base:
            cid = f"c{serial:04d}"
            serial += 1
            por_base[norm] = cid
            conceptos[cid] = {"label": base, "subcats": [], "variantes": set(),
                              "miembros": []}
        cid = por_base[norm]
        tag = _tag_limpio(data.get("variant_tag"))
        conceptos[cid]["subcats"].append(tax.normalizar_subcat(data.get("subcategoria")))
        if tag:
            conceptos[cid]["variantes"].add(tag)
        conceptos[cid]["miembros"].append((clave, tag))
    return conceptos


# ═══════════════════════════════════════════════════════════════════════════════
# Paso D — Gemini: verificación visual de conceptos con label parecido
# ═══════════════════════════════════════════════════════════════════════════════
_PROMPT_VISION = """Eres un verificador visual de productos. Recibes 2 imágenes.

DESCRIPCIÓN A: {a}
DESCRIPCIÓN B: {b}

Compara las imágenes y decide:
- "same":      mismo producto idéntico (sin variante distinguible).
- "variant":   mismo producto base con una diferencia visible (color/talla/modelo).
- "different": productos completamente distintos.

Responde SOLO con JSON:
{{"relation": "same" | "variant" | "different", "rationale": "<una frase corta>"}}"""


def _comprimir(raw: bytes, max_px: int = 512, calidad: int = 75) -> tuple[bytes, str]:
    """
    Reduce la foto antes de mandarla a Gemini. Pillow está en requirements pero
    puede no estar instalado: si falta, se manda el original (funciona igual,
    solo gasta más ancho de banda).
    """
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(raw))
        im = im.convert("RGB")
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=calidad)
        return buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001
        return raw, "image/jpeg"


async def _gemini_relacion(
    cli: httpx.AsyncClient, img_a: bytes, img_b: bytes, lbl_a: str, lbl_b: str,
) -> str | None:
    da, ma = _comprimir(img_a)
    db, mb = _comprimir(img_b)
    body = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": ma, "data": base64.b64encode(da).decode()}},
            {"inline_data": {"mime_type": mb, "data": base64.b64encode(db).decode()}},
            {"text": _PROMPT_VISION.format(a=lbl_a, b=lbl_b)},
        ]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "temperature": 0.1, "maxOutputTokens": 400},
    }
    for intento in range(4):
        try:
            r = await cli.post(
                f"{_GEMINI_BASE}/{GEMINI_MODEL}:generateContent",
                params={"key": settings.gemini_api_key}, json=body, timeout=90.0,
            )
            if r.status_code in (429, 503):
                await asyncio.sleep(2 ** intento)
                continue
            r.raise_for_status()
            partes = r.json()["candidates"][0]["content"]["parts"]
            texto = "".join(p.get("text", "") for p in partes)
            rel = (_parse_json(texto).get("relation") or "").strip().lower()
            return rel or None
        except Exception as exc:  # noqa: BLE001
            if intento == 3:
                log.warning("Gemini falló comparando %r vs %r: %s", lbl_a[:25], lbl_b[:25], exc)
                return None
            await asyncio.sleep(1 + intento)
    return None


def _foto_de_concepto(
    concepto: dict[str, Any], fotos_por_clave: dict[tuple[str, str], bytes],
) -> bytes | None:
    for clave, _tag in concepto["miembros"]:
        foto = fotos_por_clave.get(clave)
        if foto:
            return foto
    return None


async def _fusionar_por_vision(
    conceptos: dict[str, dict[str, Any]],
    fotos_por_clave: dict[tuple[str, str], bytes],
    progreso: Progreso = None,
) -> int:
    """Fusiona in-place los conceptos que Gemini declara same/variant. Devuelve cuántos."""
    if len(conceptos) < 2 or not settings.gemini_api_key:
        if not settings.gemini_api_key:
            log.info("GEMINI_API_KEY no configurada: se omite la verificación visual.")
        return 0

    cids = list(conceptos.keys())
    candidatos: list[tuple[float, str, str]] = []
    for i in range(len(cids)):
        for j in range(i + 1, len(cids)):
            a, b = cids[i], cids[j]
            ratio = difflib.SequenceMatcher(
                None,
                _normalizar_label(conceptos[a]["label"]),
                _normalizar_label(conceptos[b]["label"]),
            ).ratio()
            if ratio >= _UMBRAL_SIMILITUD:
                candidatos.append((ratio, a, b))
    candidatos.sort(reverse=True)
    candidatos = candidatos[:_MAX_PARES_VISION]
    if not candidatos:
        return 0

    fusiones = 0
    async with httpx.AsyncClient() as cli:
        for n, (_ratio, a, b) in enumerate(candidatos, start=1):
            if progreso:
                progreso("verificando_fotos", n, len(candidatos))
            # Un concepto ya fusionado en una vuelta previa deja de existir.
            if a not in conceptos or b not in conceptos:
                continue
            foto_a = _foto_de_concepto(conceptos[a], fotos_por_clave)
            foto_b = _foto_de_concepto(conceptos[b], fotos_por_clave)
            if not foto_a or not foto_b:
                continue
            rel = await _gemini_relacion(
                cli, foto_a, foto_b, conceptos[a]["label"], conceptos[b]["label"],
            )
            if rel in ("same", "variant"):
                conceptos[a]["variantes"].update(conceptos[b]["variantes"])
                conceptos[a]["subcats"].extend(conceptos[b]["subcats"])
                conceptos[a]["miembros"].extend(conceptos[b]["miembros"])
                del conceptos[b]
                fusiones += 1
    return fusiones


# ═══════════════════════════════════════════════════════════════════════════════
# Paso E — Asignación del SKU
# ═══════════════════════════════════════════════════════════════════════════════
def _subcat_dominante(subcats: list[str]) -> str:
    """Voto mayoritario: las variantes de un producto deben caer en la misma subcat."""
    if not subcats:
        return tax.SUBCAT_DEFAULT
    conteo: dict[str, int] = {}
    for s in subcats:
        conteo[s] = conteo.get(s, 0) + 1
    # Empate → gana el que no sea el default, que aporta más información.
    return max(conteo, key=lambda s: (conteo[s], s != tax.SUBCAT_DEFAULT))


def _codigos_de_variante(tags: Iterable[str]) -> dict[str, str]:
    """
    ``{tag_original: codigo_atributo}`` sin colisiones dentro del concepto.

    El código lo arma :func:`packing_taxonomia.codigo_atributo_compuesto`, que
    respeta la forma real de Odoo (``ROJ-110V``, ``NEG-VER``). Si aun así dos
    tags distintos caen en el mismo código, al segundo se le añade un número:
    un SKU repetido rompería la consolidación de costos.
    """
    usados: set[str] = set()
    salida: dict[str, str] = {}
    for tag in sorted(tags):
        cod = tax.codigo_atributo_compuesto(tag)
        if cod in usados:
            for n in range(2, 20):
                alt = f"{cod}-{n}"
                if alt not in usados:
                    cod = alt
                    break
        usados.add(cod)
        salida[tag] = cod
    return salida


def _sku(subcat: str, numero: int, codigo_attr: str | None) -> str:
    """
    Arma el SKU respetando los códigos saneados.

    No se usa ``tax.construir_sku`` cuando el atributo es libre porque aquel
    normaliza lo desconocido a ``EST`` y eso fusionaría dos variantes distintas
    (p.ej. "110V" y "220V" quedarían ambas en ``-EST``).
    """
    sub = tax.normalizar_subcat(subcat)
    if not codigo_attr:
        return f"{sub}-{numero:04d}"
    return f"{sub}-{numero:04d}-{codigo_attr}"


# ═══════════════════════════════════════════════════════════════════════════════
# Entrada pública
# ═══════════════════════════════════════════════════════════════════════════════
def _clave(fila: dict[str, Any]) -> tuple[str, str]:
    return ((fila.get("producto") or "").lower().strip(),
            (fila.get("producto_chn") or "").strip())


async def homologar(
    filas: list[dict[str, Any]],
    imagenes: dict[int, bytes] | None = None,
    contadores: tax.Contadores | None = None,
    skus_odoo: set[str] | None = None,
    usar_vision: bool = True,
    progreso: Progreso = None,
) -> dict[str, Any]:
    """
    Asigna SKU a cada fila del packing list.

    ``filas`` son las que devuelve :func:`packing_parser.leer`; ``imagenes`` es su
    ``{fila_idx: bytes}``. ``contadores`` debe venir sembrado con
    :meth:`packing_taxonomia.Contadores.desde_odoo` para no pisar SKUs vivos.

    Devuelve ``{filas, conceptos, stats}``: ``filas`` es una lista paralela a la
    de entrada con ``sku``, ``sku_base``, ``nombre``, ``variante`` y
    ``conflicto_odoo`` añadidos.
    """
    imagenes = imagenes or {}
    contadores = contadores or tax.Contadores()
    skus_odoo = skus_odoo or set()

    # ── A. Pares únicos ──
    claves = list(dict.fromkeys(_clave(f) for f in filas))
    log.info("Homologando %d filas → %d pares únicos.", len(filas), len(claves))

    # ── B. DeepSeek ──
    clasificacion = await _clasificar_todos(claves, progreso)

    # ── C. Cluster ──
    conceptos = _agrupar(clasificacion)

    # ── D. Gemini (una foto representativa por par único) ──
    fotos_por_clave: dict[tuple[str, str], bytes] = {}
    for f in filas:
        k = _clave(f)
        if k not in fotos_por_clave:
            foto = imagenes.get(f.get("fila_idx"))
            if foto:
                fotos_por_clave[k] = foto
    fusiones = 0
    if usar_vision:
        fusiones = await _fusionar_por_vision(conceptos, fotos_por_clave, progreso)

    # ── E. SKU por concepto ──
    sku_por_clave: dict[tuple[str, str], str] = {}
    base_por_clave: dict[tuple[str, str], str] = {}
    variante_por_clave: dict[tuple[str, str], str | None] = {}
    resumen_conceptos: list[dict[str, Any]] = []

    for cid, c in conceptos.items():
        subcat = _subcat_dominante(c["subcats"])
        numero = contadores.siguiente(subcat)
        base = _sku(subcat, numero, None)
        # Un solo tag no es una variante: es un adjetivo suelto del proveedor.
        con_variantes = len(c["variantes"]) >= 2
        codigos = _codigos_de_variante(c["variantes"]) if con_variantes else {}

        for clave, tag in c["miembros"]:
            cod = codigos.get(tag) if (tag and con_variantes) else None
            sku_por_clave[clave] = _sku(subcat, numero, cod)
            base_por_clave[clave] = base
            variante_por_clave[clave] = tag if con_variantes else None

        resumen_conceptos.append({
            "concepto_id": cid,
            "label": c["label"],
            "subcategoria": subcat,
            "sku_base": base,
            "variantes": sorted(c["variantes"]) if con_variantes else [],
            "n_filas": len(c["miembros"]),
        })

    # ── Salida por fila ──
    salida: list[dict[str, Any]] = []
    conflictos = 0
    for f in filas:
        k = _clave(f)
        data = clasificacion.get(k, {})
        sku = sku_por_clave.get(k, "")
        chequeo = tax.validar_vs_odoo(sku, skus_odoo) if sku else {"conflicto": False}
        if chequeo["conflicto"]:
            conflictos += 1
        salida.append({
            **f,
            "sku": sku,
            "sku_base": base_por_clave.get(k, ""),
            "variante": variante_por_clave.get(k),
            "nombre": (data.get("nombre_es") or data.get("traduccion")
                       or f.get("producto") or "").strip(),
            "traduccion": (data.get("traduccion") or "").strip(),
            "subcategoria": tax.normalizar_subcat(data.get("subcategoria")),
            "conflicto_odoo": bool(chequeo["conflicto"]),
        })

    stats = {
        "filas": len(filas),
        "pares_unicos": len(claves),
        "conceptos": len(conceptos),
        "fusiones_vision": fusiones,
        "skus_con_variante": sum(1 for s in salida if s["variante"]),
        "conflictos_odoo": conflictos,
        "sin_clasificar": sum(1 for d in clasificacion.values()
                              if d.get("base_product") == "?"),
    }
    log.info("Homologación lista: %s", stats)
    return {"filas": salida, "conceptos": resumen_conceptos, "stats": stats}


def homologar_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Envoltura síncrona, para llamarla desde el hilo del orquestador."""
    return asyncio.run(homologar(*args, **kwargs))
