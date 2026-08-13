"""
amazon_contenido.py — Validadores del contenido que la IA genera para Amazon.

POR QUÉ ESTE ARCHIVO EXISTE

`channel.field_requirements` contesta *"¿está el campo?"*. NO contesta
*"¿está bien?"* — y eso fue una decisión deliberada (ver CONTENIDO_POR_CANAL.md:
una columna de restricciones que nace vacía es como nacieron `parent_sku` y
`has_variations`). Así que los límites viven aquí, en código, hasta que haya
suficiente medición para tabularlos.

Y tienen que existir, porque **Amazon no rebota cuando te pasas**: trunca el
título en silencio, o ignora el campo entero. La IA va a violar estos límites
—no siempre, pero sí lo suficiente— y sin validador nadie se entera.

Es el mismo contrato que ya funciona en TikTok y Temu: **la IA propone, el
código valida.** Lo que no pasa, no se manda.

⚠️ EL LÍMITE DE `backend_search_terms` ES EN BYTES, NO EN CARACTERES.
Con acentos y ñ, UTF-8 gasta 2 bytes por letra: un texto de 240 caracteres con
15 acentos son 255 bytes. **Un byte de más y Amazon ignora TODO el campo, sin
avisar.** Es la trampa más cara de este archivo.
"""
from __future__ import annotations

import re
import unicodedata

# ── Límites, de la guía de estilo de Amazon MX ───────────────────────────────
#
# El título son 75 salvo en categorías con guía propia (joyería, apparel), donde
# puede ser MENOR. Eso es un requisito POR CATEGORÍA y su casa natural es
# `channel.field_requirements`; mientras no esté ahí, este es el techo genérico.
TITULO_MAX = 75
HIGHLIGHTS_MAX = 125
BULLET_MIN, BULLET_MAX = 150, 200
DESCRIPCION_MAX = 2000
SEARCH_TERMS_MAX_BYTES = 249
BULLETS_ESPERADOS = 5

# Prohibidos por la guía de estilo. No es cosmética: un `$` o un "gratis" puede
# costar la supresión del listado.
_SIGNOS = re.compile(r"[!$*~^¡]")
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿️←-⇿⬀-⯿]")
_PROMO = {
    "oferta", "ofertas", "gratis", "mejor", "barato", "descuento", "promocion",
    "promoción", "garantizado", "envio gratis", "envío gratis", "100%",
    "el mas vendido", "el más vendido", "numero 1", "número 1", "liquidacion",
    "liquidación", "rebaja", "ahorra", "regalo",
}


def bytes_utf8(texto: str) -> int:
    """Lo que Amazon cuenta en `backend_search_terms`. NO uses len()."""
    return len((texto or "").encode("utf-8"))


# Palabras vacías: no son keywords, así que repetirlas NO desperdicia el campo.
# Sin esta lista, la comprobación de "no repitas el título" saltaba por un «de»
# y tiraba los 245 bytes de términos completos. Medido en la primera corrida en
# vivo (13-ago): 2 de 3 SKUs perdieron el campo entero, uno por «de».
_VACIAS = {
    "a", "al", "ante", "con", "contra", "de", "del", "desde", "e", "el", "en",
    "entre", "hasta", "la", "las", "lo", "los", "mas", "o", "para", "por",
    "que", "se", "sin", "sobre", "su", "sus", "tras", "un", "una", "unas",
    "uno", "unos", "y", "cm", "mm", "ml", "kg", "g", "v", "w",
}


def _palabras(texto: str) -> set[str]:
    """Palabras normalizadas, sin acentos — para comparar sin falsos negativos."""
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return set(re.findall(r"[a-z0-9]+", t)) - _VACIAS


def _promocionales(texto: str) -> list[str]:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return [p for p in _PROMO
            if re.search(rf"\b{re.escape(p)}\b",
                         "".join(c for c in unicodedata.normalize("NFKD", p.lower())
                                 if not unicodedata.combining(c)) and t)]


def validar(contenido: dict) -> tuple[dict, list[str]]:
    """
    Devuelve (contenido_limpio, problemas).

    NO corrige por su cuenta: reporta. Truncar un título en silencio sería
    repetir el pecado de Amazon dentro de nuestra propia casa — el que lo
    escribió tiene que enterarse de que no cupo.
    """
    problemas: list[str] = []
    limpio = dict(contenido or {})

    titulo = (limpio.get("titulo") or "").strip()
    if not titulo:
        problemas.append("titulo: vacío")
    elif len(titulo) > TITULO_MAX:
        problemas.append(f"titulo: {len(titulo)} caracteres, máximo {TITULO_MAX}")
    if _SIGNOS.search(titulo):
        problemas.append(f"titulo: lleva signos prohibidos ({_SIGNOS.findall(titulo)})")
    if _EMOJI.search(titulo):
        problemas.append("titulo: lleva emojis")
    for p in _promocionales(titulo):
        problemas.append(f"titulo: palabra promocional '{p}'")

    hl = (limpio.get("highlights") or limpio.get("item_highlights") or "").strip()
    if hl and len(hl) > HIGHLIGHTS_MAX:
        problemas.append(f"highlights: {len(hl)} caracteres, máximo {HIGHLIGHTS_MAX}")
    for p in _promocionales(hl):
        problemas.append(f"highlights: palabra promocional '{p}'")

    bullets = limpio.get("bullets") or []
    if isinstance(bullets, str):
        bullets = [b for b in bullets.split("\n") if b.strip()]
        limpio["bullets"] = bullets
    if len(bullets) < BULLETS_ESPERADOS:
        problemas.append(f"bullets: {len(bullets)}, se esperan {BULLETS_ESPERADOS}")
    for i, b in enumerate(bullets, 1):
        b = (b or "").strip()
        if len(b) < BULLET_MIN or len(b) > BULLET_MAX:
            problemas.append(f"bullet {i}: {len(b)} caracteres, rango {BULLET_MIN}-{BULLET_MAX}")
        if b and not b[0].isupper():
            problemas.append(f"bullet {i}: no empieza con mayúscula")
        if _EMOJI.search(b):
            problemas.append(f"bullet {i}: lleva emojis")

    desc = (limpio.get("descripcion") or "").strip()
    if len(desc) > DESCRIPCION_MAX:
        problemas.append(f"descripcion: {len(desc)} caracteres, máximo {DESCRIPCION_MAX}")

    # ── El campo de los BYTES ────────────────────────────────────────────────
    st = (limpio.get("backend_search_terms") or "").strip()
    if st:
        b = bytes_utf8(st)
        if b > SEARCH_TERMS_MAX_BYTES:
            problemas.append(
                f"backend_search_terms: {b} BYTES ({len(st)} caracteres), "
                f"máximo {SEARCH_TERMS_MAX_BYTES}. Amazon ignoraría el campo ENTERO")
        if "," in st:
            problemas.append("backend_search_terms: lleva comas (Amazon las trata como espacio; gastan bytes)")
        # Repetir lo que ya está en título o highlights desperdicia el campo.
        repes = _palabras(st) & (_palabras(titulo) | _palabras(hl))
        if repes:
            problemas.append(
                f"backend_search_terms: repite {len(repes)} palabra(s) del título/highlights "
                f"({', '.join(sorted(repes)[:6])})")

    return limpio, problemas


def cabe_en_bytes(texto: str, tope: int = SEARCH_TERMS_MAX_BYTES) -> str:
    """Recorta a `tope` BYTES sin partir un carácter a la mitad.

    Solo para cuando ya se decidió recortar. Cortar por caracteres partiría un
    acento en dos y dejaría bytes inválidos.
    """
    b = (texto or "").encode("utf-8")
    if len(b) <= tope:
        return texto or ""
    return b[:tope].decode("utf-8", errors="ignore").rstrip()
