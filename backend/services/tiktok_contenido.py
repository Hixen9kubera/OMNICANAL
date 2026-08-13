"""
tiktok_contenido.py — Validadores del contenido que la IA genera para TikTok.

Hermano de `amazon_contenido.py`, con el mismo contrato: **la IA propone, el
código valida, y lo que no pasa NO se manda.**

POR QUÉ ESTE ARCHIVO EXISTE

TikTok falla al revés que Amazon, y por eso hace falta validar distinto:

· **Amazon trunca en silencio.** Te pasas del título y lo corta sin avisar.
· **TikTok acepta en BORRADOR y rebota al VENDER.** `save_mode=AS_DRAFT` casi no
  valida nada; `LISTING` valida todo. Un lote entero puede verse perfecto en
  borrador y rebotar completo al activarse — medido el 12-ago-2026: de 970
  productos publicados, la mitad traía algo que sólo salió a la luz al pasarlos
  a la venta.

De ahí la regla de la casa para este canal: **el semáforo verde de un borrador
no significa que se pueda vender.** Este módulo valida contra lo que exige
`LISTING`, no contra lo que tolera `AS_DRAFT`.

⚠️ EL TÍTULO DE MX ADMITE 300, NO 255.
TikTok documenta [1, 255] para casi todas las regiones y **[1, 300] para BR y
MX**. Usar 255 como techo genérico desperdicia 45 caracteres en el campo que
más pesa para que a uno lo encuentren.
"""
from __future__ import annotations

import re
import unicodedata

# ── Límites, verificados contra la doc de Create Product (12-ago-2026) ───────
TITULO_MAX = 300              # MX y BR; el resto de regiones 255
DESCRIPCION_MAX = 10_000      # HTML
IMG_MAX_EN_DESCRIPCION = 30
IMAGENES_MIN, IMAGENES_MAX = 1, 9
PUNTOS_MIN, PUNTOS_MAX = 3, 6

# La suma de las tres, en cm. Es de las reglas de publicación, no del esquema.
DIMENSION_SUMA_MAX = 160
STOCK_MIN, STOCK_MAX = 1, 99_999

# Etiquetas que sobreviven bien en la ficha de TikTok. `<table>` se acepta pero
# TikTok LO CONVIERTE EN IMAGEN, así que se prohíbe: el texto deja de ser texto
# y no hay forma de editarlo después.
_HTML_PERMITIDO = {"p", "br", "ul", "ol", "li", "strong", "b", "em", "i"}
_TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)[^>]*>")

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️←-⇿⬀-⯿]")

# Promesas que no controlamos. No es cosmética: en marketplace, prometer envío o
# garantía que no existe es motivo de supresión del listado.
_PROMO = {
    "envio gratis", "envío gratis", "gratis", "oferta", "ofertas", "descuento",
    "promocion", "promoción", "liquidacion", "liquidación", "rebaja",
    "el mejor", "la mejor", "mejor precio", "barato", "100%", "garantizado",
    "garantia de por vida", "garantía de por vida", "numero 1", "número 1",
    "el mas vendido", "el más vendido", "original", "envio inmediato",
    "envío inmediato", "entrega en 24", "regalo",
}


def _sin_acentos(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _promocionales(texto: str) -> list[str]:
    t = _sin_acentos(texto)
    return [p for p in _PROMO if re.search(rf"\b{re.escape(_sin_acentos(p))}\b", t)]


def _palabras(texto: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{4,}", _sin_acentos(texto)))


def validar(contenido: dict, original: dict | None = None) -> tuple[dict, list[str]]:
    """
    Devuelve (contenido_limpio, problemas).

    NO corrige por su cuenta salvo el HTML, que sí se limpia porque una etiqueta
    prohibida es un error mecánico y no una decisión de redacción. Todo lo demás
    se REPORTA: truncar un título en silencio es exactamente el pecado que
    hacemos notar de Amazon, y no vamos a repetirlo dentro de casa.

    `original` es el contenido de partida (el de Woo). Sirve para la única
    comprobación que no es de formato sino de sentido: que la propuesta siga
    hablando del mismo producto.
    """
    problemas: list[str] = []
    limpio = dict(contenido or {})

    # ── título ───────────────────────────────────────────────────────────────
    titulo = (limpio.get("titulo") or "").strip()
    if not titulo:
        problemas.append("titulo: vacío")
    elif len(titulo) > TITULO_MAX:
        problemas.append(f"titulo: {len(titulo)} caracteres, máximo {TITULO_MAX}")
    if _EMOJI.search(titulo):
        problemas.append("titulo: lleva emojis")
    if titulo and sum(c.isupper() for c in titulo) > len(titulo) * 0.6:
        problemas.append("titulo: casi todo en MAYÚSCULAS")
    for p in _promocionales(titulo):
        problemas.append(f"titulo: promesa no verificable '{p}'")

    # ⚠️ LA COMPROBACIÓN QUE IMPORTA. Un título "mejorado" puede quedar
    # impecable de forma y hablar de otro producto. Caso real del 12-ago: el
    # recomendador mandó un "Collar de recuperación para gato" (cono
    # veterinario) a la categoría de joyería de disfraces — con toda confianza y
    # sin dar error. Si la propuesta no conserva NADA del sustantivo original,
    # se descarta: es más barato quedarse con el título feo que vender otra cosa.
    if original and titulo:
        base = _palabras(original.get("titulo") or "")
        if base and not (base & _palabras(titulo)):
            problemas.append(
                "titulo: no comparte ninguna palabra con el original — "
                "puede estar describiendo otro producto")

    # ── descripción ──────────────────────────────────────────────────────────
    desc = (limpio.get("descripcion_html") or limpio.get("descripcion") or "").strip()
    if len(desc) > DESCRIPCION_MAX:
        problemas.append(f"descripcion: {len(desc)} caracteres, máximo {DESCRIPCION_MAX}")
    prohibidas = {t.lower() for t in _TAG.findall(desc)} - _HTML_PERMITIDO
    # El aviso de <img> va ANTES de limpiar: después ya no queda ninguna que
    # encontrar y el consejo útil —"súbelas primero"— no se daría nunca.
    if "img" in prohibidas:
        problemas.append("descripcion: lleva <img>; sólo se admiten URLs ya "
                         "rehospedadas por TikTok, súbelas primero")
    if prohibidas:
        problemas.append(f"descripcion: etiquetas no permitidas {sorted(prohibidas)}")
        desc = _TAG.sub(lambda m: m.group(0)
                        if m.group(1).lower() in _HTML_PERMITIDO else "", desc)
    for p in _promocionales(desc):
        problemas.append(f"descripcion: promesa no verificable '{p}'")
    if desc:
        limpio["descripcion_html"] = desc

    # ── puntos clave ─────────────────────────────────────────────────────────
    puntos = limpio.get("puntos_clave") or []
    if isinstance(puntos, str):
        puntos = [p for p in puntos.split("\n") if p.strip()]
        limpio["puntos_clave"] = puntos
    if puntos and not (PUNTOS_MIN <= len(puntos) <= PUNTOS_MAX):
        problemas.append(f"puntos_clave: {len(puntos)}, se esperan "
                         f"{PUNTOS_MIN}-{PUNTOS_MAX}")
    for i, p in enumerate(puntos, 1):
        if _EMOJI.search(p or ""):
            problemas.append(f"punto {i}: lleva emojis")

    # ── lo que la IA misma marcó como dudoso ─────────────────────────────────
    # Un dato inventado NO da error: se publica y nadie sabe cuál era mentira.
    # Si el modelo anota que no lo confirmó, no va. Es la misma regla que ya
    # aplica `tk_publicar.py` con los atributos.
    if limpio.get("flags"):
        problemas.append(f"la IA marcó {len(limpio['flags'])} dato(s) sin "
                         f"confirmar: {limpio['flags']}")

    return limpio, problemas


def validar_publicable(producto: dict) -> list[str]:
    """
    Los bloqueantes de LISTING que NO son contenido. Se comprueban aparte porque
    vienen del producto en Woo, no de la IA — y porque son los que hacen rebotar
    un borrador que se veía perfecto.
    """
    problemas: list[str] = []
    try:
        stock = int(float(producto.get("stock") or 0))
    except (TypeError, ValueError):
        stock = 0
    if not (STOCK_MIN <= stock <= STOCK_MAX):
        problemas.append(f"stock: {stock} fuera de [{STOCK_MIN}, {STOCK_MAX}] — "
                         f"el 0 no es un valor válido en TikTok")
    try:
        peso = float(producto.get("peso") or 0)
    except (TypeError, ValueError):
        peso = 0
    if peso <= 0:
        problemas.append("peso: 0 kg no es válido")

    dims = [producto.get("largo"), producto.get("ancho"), producto.get("alto")]
    try:
        suma = sum(float(d or 0) for d in dims)
    except (TypeError, ValueError):
        suma = 0
    if suma > DIMENSION_SUMA_MAX:
        problemas.append(f"dimensiones: L+A+H = {suma:.0f} cm, máximo "
                         f"{DIMENSION_SUMA_MAX} cm")

    n = len(producto.get("imagenes") or [])
    if not (IMAGENES_MIN <= n <= IMAGENES_MAX):
        problemas.append(f"imagenes: {n}, se admiten {IMAGENES_MIN}-{IMAGENES_MAX}")
    return problemas
