"""
ia_generadores.py — Generadores de contenido por canal (IA).

Cada marketplace tiene su propio "agente" con un prompt especializado. Desde el
estudio de producto (pestaña PRODUCTOS) se dispara un generador concreto —
Título, Bullets, Descripción, Atributos, Set de imágenes…— y este módulo:

  1) arma el contexto del producto (nombre, categoría, atributos, precio…),
  2) elige el proveedor de IA disponible (DeepSeek primero; si no, Claude),
  3) devuelve el contenido optimizado para ESE canal.

El registro `GENERADORES` es la fuente única de verdad: el frontend consume
/api/ia/generadores?canal=… para pintar los botones, así que agregar un canal o
un tipo de contenido es solo editar este diccionario.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from config import settings
from services import sin_garantia

log = logging.getLogger("omnicanal.ia")

_CLAUDE_MODEL = "claude-opus-4-8"


# ─────────────────────────────────────────────────────────────────────────────
# Proveedor de IA: DeepSeek (si hay clave) → Claude (anthropic) → error legible
# ─────────────────────────────────────────────────────────────────────────────
def _completar(system: str, user: str, max_tokens: int = 900) -> dict[str, Any]:
    """Llama al proveedor disponible y devuelve {ok, texto/modelo/motivo}."""
    # 1) DeepSeek (API compatible con OpenAI)
    if settings.deepseek_api_key:
        try:
            r = httpx.post(
                f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                json={
                    "model": settings.deepseek_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                },
                timeout=90.0,
            )
            r.raise_for_status()
            texto = r.json()["choices"][0]["message"]["content"].strip()
            return {"ok": True, "texto": texto, "modelo": settings.deepseek_model,
                    "proveedor": "deepseek"}
        except Exception as exc:  # noqa: BLE001
            log.warning("DeepSeek falló, intento Claude: %s", exc)

    # 2) Claude (anthropic)
    if settings.anthropic_api_key:
        try:
            import anthropic  # import perezoso

            cli = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            msg = cli.messages.create(
                model=_CLAUDE_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            texto = "".join(
                b.text for b in msg.content if getattr(b, "type", "") == "text"
            ).strip()
            return {"ok": True, "texto": texto, "modelo": _CLAUDE_MODEL,
                    "proveedor": "claude"}
        except Exception as exc:  # noqa: BLE001
            log.error("Claude falló: %s", exc)
            return {"ok": False, "motivo": f"IA no disponible: {exc}"}

    return {"ok": False,
            "motivo": "Configura DEEPSEEK_API_KEY o ANTHROPIC_API_KEY para generar contenido."}


# ─────────────────────────────────────────────────────────────────────────────
# Contexto del producto → texto que se le pasa al modelo
# ─────────────────────────────────────────────────────────────────────────────
def _contexto(p: dict[str, Any]) -> str:
    partes: list[str] = []
    # El bloque de VARIANTE va PRIMERO: es lo que cambia el sentido de todo lo
    # demás (el "Nombre actual" suele ser el de la familia entera). Lo usan
    # General, ML, los generadores por campo y Amazon (`amazon_ia` llama aquí).
    from services import ia_variante
    variante = ia_variante.bloque(p)
    if variante:
        partes.append(variante + "\n")
    if p.get("sku"):
        partes.append(f"SKU: {p['sku']}")
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


def _sin_html(texto: str) -> str:
    import re
    limpio = re.sub(r"<[^>]+>", " ", texto)
    return re.sub(r"\s+", " ", limpio).strip()


def _sin_acentos(texto: str) -> str:
    """Quita tildes/diéresis y convierte ñ→n (título de Amazon, regla de negocio).
    Descompone en NFKD y elimina los diacríticos combinantes."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ─────────────────────────────────────────────────────────────────────────────
# Amazon: UN SOLO PROMPT, y no vive aquí.
#
# La especificación de contenido de Amazon es `amazon_ia._SISTEMA` — el spec de
# Brandon, literal — y es la única que pasa por el validador de límites, por los
# requisitos REALES de la categoría (`channel.field_requirements`) y por el
# detector de marcas registradas.
#
# Aquí vivían SEIS prompts más (título, highlights, bullets, descripción,
# atributos y el JSON de "Mejorar con IA"), escritos contra la guía del 27-jul.
# Se BORRARON el 13-ago por decisión de Brandon: dos textos para el mismo campo
# es una invitación a editar el que no sale a producción. Los bullets son el
# ejemplo — el viejo pedía el prefijo «[CARACTERÍSTICA EN MAYÚSCULAS]:» y el
# spec vivo pide oraciones completas: el mismo producto habría salido distinto
# según qué botón se apretara.
#
# Lo único de Amazon que sobrevive en este módulo es el planificador de
# IMÁGENES, que no compite con nada: no escribe contenido del listado, arma un
# set de 5 fotos con sus prompts.
# ─────────────────────────────────────────────────────────────────────────────
_AMZ_IMAGENES = (
    "Eres un director de arte experto en imágenes de producto para Amazon. A "
    "partir de la imagen principal y los datos del producto: 1) DETECTA la "
    "categoría, 2) PLANEA un set de 5 imágenes optimizado para esa categoría, "
    "3) GENERA cada imagen con layout, texto exacto y prompt de IA.\n\n"
    "PASO 1 — Clasifica en UNA categoría: A) Moda y calzado, B) Electrónicos y "
    "gadgets, C) Hogar y cocina, D) Salud/belleza/cuidado personal, E) Mascotas, "
    "F) Deportes y fitness, G) Alimentos y bebidas, H) Bebés y maternidad, "
    "I) Herramientas y mejoras del hogar, J) Juguetes y juegos.\n\n"
    "PASO 2 — Según la categoría, define las 5 imágenes (IMG1 siempre = producto "
    "sobre fondo blanco puro #FFFFFF, sin texto, 85% del encuadre; IMG2–IMG5 "
    "según la plantilla de esa categoría: lifestyle, beneficios con iconos, "
    "medidas/compatibilidad, Q&A frecuentes, etc.).\n\n"
    "REGLAS UNIVERSALES (todas salvo IMG1): texto en español, máx 40 palabras por "
    "imagen; tipografía sans-serif (máx 2 familias); paleta de marca o "
    "blanco/negro/acento; layout distinto en cada imagen; 1:1, mínimo 1000x1000px "
    "(ideal 2000x2000); sin marcas de agua ni logos de terceros; callouts con "
    "líneas finas.\n\n"
    "REQUISITOS TÉCNICOS DE AMAZON (obligatorios para que la imagen se acepte):\n"
    "• La imagen debe servirse por protocolo HTTP o HTTPS (nunca FTP ni ruta de "
    "archivo local).\n"
    "• Formato: JPEG, TIFF, PNG o GIF NO animado — se prefiere JPEG.\n"
    "• Color: RGB o CMYK — se prefiere RGB.\n"
    "• Debe ser clara y sin pixelar, con al menos 72 ppp.\n"
    "• Entre 1,000 y 10,000 píxeles en el LADO MÁS LARGO (es lo que habilita la "
    "funcionalidad de zoom de Amazon).\n\n"
    "FORMATO DE ENTREGA por imagen:\n"
    "[IMG X — NOMBRE]\n"
    "Descripción del layout visual\n"
    "Texto exacto que debe aparecer (en español)\n"
    "Elementos visuales principales\n"
    "Prompt de generación para IA de imágenes (en inglés, estilo Midjourney/DALL·E)"
)

# ─────────────────────────────────────────────────────────────────────────────
# Prompts Mercado Libre / General / otros
# ─────────────────────────────────────────────────────────────────────────────
_ML_TITULO = (
    "Eres experto en publicaciones de Mercado Libre México. Genera un TÍTULO de "
    "máximo 60 caracteres, con las palabras clave más buscadas al inicio, sin "
    "signos promocionales ni datos de contacto. Devuelve solo el título y, debajo, "
    "«(N caracteres)»."
)
_ML_FICHA = (
    "Eres experto en Mercado Libre México. Genera la FICHA TÉCNICA (atributos) del "
    "producto para completar la publicación: marca, modelo, color, material, "
    "tamaño, contenido del paquete y demás atributos relevantes de su categoría. "
    "Devuelve en formato «Atributo: valor», uno por línea; marca con «(sugerido)» "
    "lo que estés infiriendo."
)
_ML_DESCRIPCION = (
    "Eres experto en Mercado Libre México. Genera una DESCRIPCIÓN en texto plano "
    "(sin HTML), clara y persuasiva, con párrafos cortos y viñetas simples con «- ». "
    "No incluyas teléfonos, correos, enlaces externos ni datos de contacto "
    "(están prohibidos). Enfócate en beneficios, usos y características. "
    + sin_garantia.REGLA
)
_GEN_TITULO = (
    "Eres redactor de e-commerce. Genera un título comercial claro y atractivo "
    "para la tienda (WooCommerce), con la palabra clave principal al inicio. "
    "Devuelve solo el título."
)
_GEN_DESCRIPCION = (
    "Eres redactor de e-commerce. Genera una descripción de producto para "
    "WooCommerce en HTML simple (<p>, <ul>, <li>, <strong>): un párrafo de "
    "introducción, una lista de características/beneficios y un cierre. "
    + sin_garantia.REGLA + " Devuelve solo el HTML."
)


# ─────────────────────────────────────────────────────────────────────────────
# Registro de generadores por canal
#   icono = clave que el frontend mapea a un ícono (lucide)
#   tipo  = "texto" | "imagenes" (imagenes = plan + prompts, también texto)
# ─────────────────────────────────────────────────────────────────────────────
GENERADORES: dict[str, list[dict[str, Any]]] = {
    # AMAZON: el contenido (título, highlights, bullets, descripción, términos
    # de búsqueda y atributos) sale ENTERO de `amazon_ia.mejorar`, con su
    # validador. Los cinco generadores por campo que había aquí se borraron el
    # 13-ago para no tener dos prompts del mismo campo. Queda el de imágenes,
    # que no escribe contenido del listado.
    "amazon": [
        {"id": "imagenes", "label": "Set de imágenes", "icono": "image", "max_tokens": 1800, "tipo": "imagenes",
         "descripcion": "Plan de 5 imágenes + prompts IA", "system": _AMZ_IMAGENES},
    ],
    "mercado_libre": [
        {"id": "titulo", "label": "Título", "icono": "type", "max_tokens": 300,
         "descripcion": "Título ML ≤60 caracteres", "system": _ML_TITULO},
        {"id": "ficha", "label": "Ficha técnica", "icono": "tags", "max_tokens": 900,
         "descripcion": "Atributos de la publicación", "system": _ML_FICHA},
        {"id": "descripcion", "label": "Descripción", "icono": "align-left", "max_tokens": 1000,
         "descripcion": "Descripción en texto plano", "system": _ML_DESCRIPCION},
    ],
    "general": [
        {"id": "titulo", "label": "Título", "icono": "type", "max_tokens": 200,
         "descripcion": "Título comercial para la tienda", "system": _GEN_TITULO},
        {"id": "descripcion", "label": "Descripción", "icono": "align-left", "max_tokens": 900,
         "descripcion": "Descripción HTML para WooCommerce", "system": _GEN_DESCRIPCION},
    ],
    # TIKTOK: el contenido sale entero de `tiktok_ia.mejorar`, con su validador
    # y los atributos de la categoría real. El generador de "título viral" que
    # había aquí se borró el 13-ago junto con los de Amazon: pedía 45 caracteres
    # donde MX admite 300.
    "tiktok": [],
}

# Campos internos que NO se exponen al frontend.
_PRIVADOS = {"system"}


def definiciones(canal: str) -> list[dict[str, Any]]:
    """Metadatos de los generadores de un canal (para pintar los botones)."""
    return [
        {k: v for k, v in g.items() if k not in _PRIVADOS}
        for g in GENERADORES.get(canal, [])
    ]


def _buscar(canal: str, generador_id: str) -> dict[str, Any] | None:
    for g in GENERADORES.get(canal, []):
        if g["id"] == generador_id:
            return g
    return None


# ─────────────────────────────────────────────────────────────────────────────
# "Mejorar con IA" — un solo botón por canal que mejora VARIOS campos a la vez.
# Devuelve JSON estructurado. NO toca precio/costo/alibaba/peso/dimensiones.
# ─────────────────────────────────────────────────────────────────────────────
# Blindaje anti-residuos: hay productos CLONADOS de otro (caso real ACC-0653:
# faros de niebla con categoría y atributos de binoculares). El título y la
# descripción son la identidad; lo demás puede ser basura heredada.
_NO_CONTRADECIR = (
    "\nIMPORTANTE: el TÍTULO y la DESCRIPCIÓN actuales definen QUÉ ES el "
    "producto. Si la categoría o los atributos recibidos los contradicen "
    "(pueden ser residuos de otro producto), IGNÓRALOS por completo y NO "
    "cambies el tipo de producto."
)

_MEJORAR: dict[str, dict[str, Any]] = {
    "mercado_libre": {
        "max_tokens": 1500,
        "system": _ML_TITULO.split(".")[0] + (
            ". Mejora la publicación de Mercado Libre México. Devuelve SOLO JSON válido:\n"
            '{"titulo": "<máx 60 caracteres, keywords al inicio>", '
            '"descripcion": "<texto plano, párrafos cortos, sin datos de contacto>", '
            '"atributos": [{"nombre": "..", "valor": ".."}]}\n'
            "En atributos incluye los NECESARIOS de la categoría (marca, modelo, color, "
            "material, tamaño…) y los secundarios que ayuden a la ficha. No inventes datos "
            "que no se puedan inferir del producto. " + sin_garantia.REGLA + _NO_CONTRADECIR
        ),
    },
    # AMAZON no está en este diccionario a propósito: `mejorar()` desvía el canal
    # a `amazon_ia.mejorar` antes de llegar aquí. El prompt que vivía en esta
    # entrada se borró el 13-ago — mientras existió, era el que un humano habría
    # editado creyendo que cambiaba el botón, cuando el que sale a producción es
    # `amazon_ia._SISTEMA`.
    "general": {
        "max_tokens": 1200,
        "system": (
            "Eres redactor de e-commerce (WooCommerce). Devuelve SOLO JSON válido:\n"
            '{"titulo": "<título comercial claro>", '
            '"descripcion": "<HTML simple: <p>, <ul>, <li>, <strong>>", '
            '"atributos": [{"nombre": "..", "valor": ".."}]}\n' + sin_garantia.REGLA
        ),
    },
    # TIKTOK tampoco está aquí desde v0.143.0: `mejorar()` lo desvía a
    # `tiktok_ia`. El prompt que vivía en esta entrada pedía un título de "máx
    # 45 caracteres" cuando MX admite **300** — 255 caracteres tirados en el
    # campo que más pesa para que a uno lo encuentren.
}


# ─────────────────────────────────────────────────────────────────────────────
# Mercado Libre: UNA FOTO, el título del packing list y las palabras de ML
# (v0.706.0, pedido de Brandon el 30-oct-2026)
#
# Hasta aquí «Mejorar con IA» de ML solo LEÍA el nombre actual, la descripción,
# la categoría y los atributos, y se los mandaba a DeepSeek, que no ve imágenes.
# Con un nombre genérico («CALZADO»), en inglés o de otro producto escribía a
# ciegas, e inventaba lo que no sabía (watts, piezas, material) en cada tanda
# del 28-oct.
#
# Ahora el mensaje lleva:
#   · UNA foto: la principal de la tienda (la que verá el comprador); si no se
#     puede bajar, la siguiente; si la tienda no tiene, la del renglón del
#     packing list de la pestaña Catálogo;
#   · el título de ese renglón del packing list (`catalogo.titulos_pl`: lo que se
#     le compró al proveedor), junto al nombre actual;
#   · las PALABRAS DE MERCADO LIBRE de la categoría: los términos más buscados
#     (`/trends`) y los títulos de los más vendidos (`/highlights`). Primero lo
#     que ya midió Competencia (`enrich.market_terms`, `market_bestsellers`); si
#     la categoría no está medida, se le preguntan a ML en vivo (gratis).
# Va a OpenRouter con los modelos que midió Crear (`OPENROUTER_MODELOS_SEO`).
# Sin llave, sin foto o sin respuesta, sigue DeepSeek → Claude con el mismo texto
# (sin la foto). Los atributos siguen en `ml_atributos`, ya con lo que se vio.
# ─────────────────────────────────────────────────────────────────────────────
MAX_FOTOS_TIENDA = 3      # de la galería se intentan, en orden, hasta bajar UNA
TOPE_TERMINOS = 15        # términos más buscados que se le pasan (ML publica hasta 50)
TOPE_TERMINOS_LEIDOS = 50  # los que se leen: los que llevan marca se descartan antes de cortar a 15
TOPE_LIDERES = 10         # títulos de los más vendidos que se le pasan

_SIN_MARCAS_NI_MODELOS = (
    "MARCAS Y MODELOS: PROHIBIDOS. Ni el título ni la descripción llevan NINGÚN nombre de marca ni de "
    "modelo: ni los que se lean en la foto (logos, texto impreso), ni los del packing list, ni los del "
    "nombre actual, ni los que traigan los títulos de los más vendidos o los términos más buscados. "
    "Tampoco como comparación o compatibilidad: nada de «tipo X», «estilo X», «compatible con X» ni "
    "«para X» cuando X es una marca o un modelo. Los códigos de modelo (como «UX-06» o «XR-500») "
    "tampoco van. Describe el producto con palabras genéricas: lo que es, para qué sirve y sus "
    "características (medidas, capacidad, potencia, color). No podemos publicar nombres de marcas ni de "
    "modelos."
)

_PALABRAS_ML = (
    "Si te paso PALABRAS DE MERCADO LIBRE (los términos que más busca la gente en la categoría y los "
    "títulos de los más vendidos), parafrasea el título con ellas: usa los términos más buscados que SÍ "
    "describan a este producto, los de más arriba primero, con la estructura de los títulos que más "
    "venden. Nunca metas un término que no le corresponda (otro material, otra medida, otro uso) solo "
    "porque se busca, ni uno que lleve una marca o un modelo: de esos toma solo las palabras genéricas."
)

_ML_CON_FOTO = (
    "Eres experto en publicaciones de Mercado Libre México. Con la FOTO, los TÍTULOS DE REFERENCIA y "
    "las PALABRAS DE MERCADO LIBRE, propón el título y la descripción para publicar el producto.\n\n"
    "CÓMO SE USAN LAS FUENTES\n"
    "- La FOTO dice qué es el producto, su color, su forma y qué piezas se ven.\n"
    "- Los TÍTULOS DE REFERENCIA —el del packing list, que es el del proveedor y casi siempre viene en "
    "inglés o chino, y el nombre actual en la tienda— dicen qué se compró y lo que la foto no muestra: "
    "medidas, cantidad, capacidad, voltaje. Pueden venir genéricos («tape»), en otro idioma o con faltas: "
    "úsalos para saber qué es, no los copies.\n"
    "- " + _PALABRAS_ML + "\n"
    "- Si la foto muestra un producto DISTINTO al de los títulos de referencia (otro producto, no otro "
    "color ni otro ángulo), no los mezcles: escribe según los títulos, pon \"coincide\": \"no\" y di en "
    "\"dudas\" qué se ve en la foto.\n\n"
    + _SIN_MARCAS_NI_MODELOS + "\n\n"
    "NO INVENTES. Afirma solo lo que se ve en la foto o lo que dicen los datos. Watts, voltaje, número "
    "de piezas, medidas, capacidad, material y certificaciones: si no están en una de las dos fuentes, no "
    "se mencionan. Si dudas de un dato, déjalo fuera y anótalo en \"dudas\".\n\n"
    "TÍTULO: máximo 60 caracteres, español de México. Empieza por el tipo de producto con las palabras "
    "con que la gente lo busca; luego lo que lo distingue (función principal, medida o cantidad si se "
    "conoce, color si se ve). Sin marcas ni modelos, sin signos promocionales y sin palabras en "
    "mayúsculas sostenidas.\n"
    "DESCRIPCIÓN: texto plano, sin HTML ni emojis, de 80 a 200 palabras: un párrafo de qué es y para "
    "qué sirve, viñetas con «- » con sus características, y un cierre con sus usos. Sin teléfonos, "
    "correos, enlaces ni datos de contacto. " + sin_garantia.REGLA + "\n"
    "El título y la descripción se escriben para el COMPRADOR: no hables de la foto, de «la imagen», de "
    "los datos, del packing list, del proveedor ni de tus dudas (nada de «la imagen muestra», «según la "
    "información», «se observa» o «revisa las medidas»). Lo que no sea seguro se queda fuera y va solo "
    "en \"dudas\".\n\n"
    "Responde SOLO con este JSON:\n"
    '{"titulo": "...", "descripcion": "...", '
    '"lo_que_se_ve": "<una o dos frases: qué producto se ve, color, material aparente y qué incluye>", '
    '"coincide": "si | parcial | no", "dudas": ["..."], '
    '"atributos": [{"nombre": "..", "valor": ".."}]}\n'
    "\"coincide\": \"si\" cuando la foto y los títulos de referencia son el mismo producto; \"parcial\" "
    "cuando los títulos son genéricos o les falta lo que la foto muestra; \"no\" cuando son productos "
    "distintos. En \"atributos\" van solo los que se ven o vienen en los datos."
)


def _sin_vision(motivo: str) -> dict[str, Any]:
    return {"ok": False, "vision": {"ok": False, "motivo": motivo}}


def _del_catalogo(sku: str, que: str) -> Any:
    """BLOQUEANTE. `titulos_pl` o `foto_pl` del SKU en la pestaña Catálogo; vacío si no hay o si falla."""
    if not sku:
        return [] if que == "titulos_pl" else None
    try:
        from services import catalogo
        return getattr(catalogo, que)(sku)
    except Exception as exc:  # noqa: BLE001
        log.warning("mejorar ML: %s de %s no se pudo leer: %s", que, sku, exc)
        return [] if que == "titulos_pl" else None


async def _lideres_en_vivo(cat: str) -> list[str]:
    """Los títulos de los más vendidos de la categoría, preguntados a ML (`/highlights`, gratis). Solo los
    productos de catálogo: las publicaciones sueltas de otros vendedores dan 403. Nunca lanza."""
    from services import precio_sugerido
    try:
        cli = await precio_sugerido._cliente_ml()  # noqa: SLF001
        if cli is None:
            return []
        async with cli:
            top = (await precio_sugerido._get(cli, f"/highlights/MLM/category/{cat}") or {}).get("content") or []  # noqa: SLF001
            ids = [h["id"] for h in top if h.get("type") == "PRODUCT" and h.get("id")][:TOPE_LIDERES]
            prods = await asyncio.gather(*(precio_sugerido._get(cli, f"/products/{i}") for i in ids))  # noqa: SLF001
        return [str(p["name"]).strip() for p in prods if isinstance(p, dict) and p.get("name")]
    except Exception as exc:  # noqa: BLE001
        log.warning("mejorar ML: más vendidos de %s en vivo: %s", cat, exc)
        return []


async def _mercado_ml(cat: str) -> dict[str, Any] | None:
    """Las PALABRAS DE MERCADO LIBRE de la categoría: {categoria, terminos, lideres, fuente}. None sin categoría.
    Primero lo que guardó Competencia; lo que falte, en vivo. Nunca lanza."""
    if not cat:
        return None
    from services import competencia_ml, competencia_store

    def _guardado() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return (competencia_store.terminos_categoria(cat, None, TOPE_TERMINOS_LEIDOS),
                competencia_store.ranking_categoria(cat, None, TOPE_LIDERES))

    descartados: list[str] = []

    def _sin_marca(terminos: list[str]) -> list[str]:
        # Un término con marca («airpods», «redmi buds») no se puede usar: ni se le pasa
        # a la IA ni cuenta para la cobertura (30-oct: 11 de 15 en Audífonos eran marcas).
        limpios = []
        for t in terminos:
            (descartados if _marcas_en(t) else limpios).append(t)
        return limpios[:TOPE_TERMINOS]

    try:
        terminos, lideres = await asyncio.to_thread(_guardado)
    except Exception as exc:  # noqa: BLE001
        log.warning("mejorar ML: palabras guardadas de %s: %s", cat, exc)
        terminos, lideres = [], []
    out: dict[str, Any] = {
        "categoria": cat,
        "terminos": _sin_marca([str(t["termino"]).strip() for t in terminos if t.get("termino")]),
        "lideres": [str(x["titulo"]).strip() for x in sorted(lideres, key=lambda x: x.get("posicion") or 999)
                    if x.get("titulo")][:TOPE_LIDERES],
    }
    out["fuente"] = {k: "competencia" if out[k] else "sin datos" for k in ("terminos", "lideres")}
    if not out["terminos"]:
        try:
            vivo = await asyncio.to_thread(competencia_ml.tendencias, cat)
        except Exception as exc:  # noqa: BLE001
            log.warning("mejorar ML: /trends de %s en vivo: %s", cat, exc)
            vivo = None
        if vivo:
            out["terminos"] = _sin_marca([str(t["keyword"]).strip() for t in vivo if t.get("keyword")])
            out["fuente"]["terminos"] = "en vivo"
    out["con_marca"] = descartados
    if not out["lideres"]:
        out["lideres"] = await _lideres_en_vivo(cat)
        if out["lideres"]:
            out["fuente"]["lideres"] = "en vivo"
    return out


def _referencias(titulos_pl: list[str], mercado: dict[str, Any] | None) -> str:
    """El título del packing list y las palabras de ML, en texto, para el mensaje (con foto o sin ella)."""
    partes: list[str] = []
    if titulos_pl:
        partes.append("Título del packing list (del proveedor): " + " | ".join(titulos_pl))
    if mercado and mercado.get("terminos"):
        partes.append("\nPALABRAS DE MERCADO LIBRE — términos más buscados de la categoría, de más a menos:\n"
                      + "\n".join(f"{i}. {t}" for i, t in enumerate(mercado["terminos"], 1)))
    if mercado and mercado.get("lideres"):
        partes.append("\nPALABRAS DE MERCADO LIBRE — títulos de los más vendidos de la categoría:\n"
                      + "\n".join(f"#{i} {t}" for i, t in enumerate(mercado["lideres"], 1)))
    return "\n".join(partes)


async def _foto_para_ia(cli: httpx.AsyncClient, producto: dict[str, Any]) -> tuple[str, str] | None:
    """(de dónde salió, data-URL) de UNA foto: la primera de la galería que se pueda bajar (máx.
    `MAX_FOTOS_TIENDA` intentos) y, si la tienda no tiene, la del packing list. None si no hay ninguna."""
    import base64

    from services import crear_producto  # perezoso: módulo pesado; aquí solo su reductor de fotos

    urls = [u for u in (producto.get("imagenes") or [])
            if isinstance(u, str) and u.startswith(("http://", "https://"))][:MAX_FOTOS_TIENDA]
    for i, url in enumerate(urls):
        try:
            r = await cli.get(url, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                foto = await asyncio.to_thread(crear_producto._foto_data_url, r.content)  # noqa: SLF001
                if foto:
                    return ("la principal de la tienda" if i == 0 else
                            f"la {i + 1}ª de la tienda (la principal no se pudo bajar)"), foto
            log.warning("mejorar ML: foto %s HTTP %d", url, r.status_code)
        except httpx.HTTPError as exc:
            log.warning("mejorar ML: no se pudo bajar la foto %s: %s", url, exc)
    pl = await asyncio.to_thread(_del_catalogo, str(producto.get("sku") or "").strip(), "foto_pl")
    if pl:
        return ("la del renglón del packing list (la tienda no tiene foto)",
                "data:image/jpeg;base64," + base64.b64encode(pl).decode())
    return None


def _coincide(valor: Any) -> str:
    v = _sin_acentos(str(valor or "")).strip().lower()
    return v if v in ("si", "parcial", "no") else ""


# ── Marcas y modelos: la IA propone, el código revisa ────────────────────────
# Regla de Brandon (30-oct-2026): «no debe de usarse nombres de MARCAS, ni
# MODELOS … no podemos usar esos nombres en nuestra publicación». El prompt lo
# prohíbe (`_SIN_MARCAS_NI_MODELOS`), pero un modelo que escribe «estilo Bose»
# no siente que desobedece: describe. Así que aquí se revisa lo que devolvió.
#   · MARCAS: las de las dos listas del panel —`marcas_renombre` (las de renombre
#     de Competencia) y `terminos_protegidos` (las de Amazon)— y SIN la excepción
#     de compatibilidad que tiene Amazon: en ML tampoco va «compatible con
#     iPhone». Si aparece una, se le pide UNA corrección a la IA; si sigue, se
#     reporta para que una persona la quite antes de publicar.
#   · MODELOS: no hay lista posible. Se marcan los códigos (letras y números,
#     como «UX-06») que venían en los datos y la IA repitió. Solo se REPORTAN:
#     «GU10» o «M8» son medidas, y quitarlas a ciegas estropearía el título.
_CODIGO = re.compile(r"(?<![\w-])(?=[\w-]*[A-Za-z])(?=[\w-]*\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?![\w-])")
_MEDIDA = re.compile(r"^\d+(?:[.,]\d+)?(?:x\d+(?:[.,]\d+)?)*(?:w|v|a|ah|mah|wh|gb|tb|mb|cm|mm|m|km|kg|g|mg|l|ml|"
                     r"oz|lb|pz|pzs|pza|pzas|pcs|p|k|hz|khz|mhz|ghz|db|in|pulg|lm|btu|rpm|psi|x)?$", re.I)


def _marcas_en(texto: str) -> list[str]:
    """Las marcas de las listas del panel que aparecen en el texto, por palabras completas. Las nuestras no."""
    from services import marcas_renombre, terminos_protegidos

    halladas: list[str] = []
    alias, _, largo = marcas_renombre._indice()  # noqa: SLF001
    palabras = marcas_renombre.normalizar(texto).split()
    for i in range(len(palabras)):
        for n in range(1, largo + 1):
            if i + n > len(palabras):
                break
            m = alias.get(" ".join(palabras[i:i + n]))
            if m and m not in halladas and marcas_renombre.normalizar(m) not in terminos_protegidos.PROPIAS:
                halladas.append(m)
    ya = {marcas_renombre.normalizar(h) for h in halladas}
    for m in terminos_protegidos._PATRON.finditer(texto or ""):  # noqa: SLF001
        k = m.group(0)
        if k.lower() not in terminos_protegidos.PROPIAS and marcas_renombre.normalizar(k) not in ya:
            halladas.append(k)
            ya.add(marcas_renombre.normalizar(k))
    return halladas


def _modelos_en(texto: str, fuentes: str) -> list[str]:
    """Los códigos de modelo de `fuentes` (los datos que recibió la IA) que reaparecen en `texto`."""
    codigos = {c for c in _CODIGO.findall(fuentes or "") if len(c) >= 3 and not _MEDIDA.match(c)}
    return sorted(c for c in codigos
                  if re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", texto or "", re.IGNORECASE))


# La prueba en vivo del 30-oct: la descripción le hablaba al comprador de sus
# fuentes («La imagen muestra…», «Los datos disponibles indican…», «Revisa las
# medidas antes de comprar, ya que las fuentes…»). Eso va en "dudas", no en la
# publicación.
_FRASE_FUENTE = re.compile(
    r"\b(?:la imagen|las im[aá]genes|en la foto|la foto (?:muestra|indica)|se observan?|"
    r"los datos(?: disponibles)?|seg[uú]n (?:la informaci[oó]n|los datos)|la informaci[oó]n (?:del producto|disponible)|"
    r"packing list|el proveedor|las fuentes|revisa las medidas)\b", re.IGNORECASE)


def _frases_de_fuente(data: dict[str, Any]) -> list[str]:
    """Las frases del título o la descripción que hablan de la foto o de los datos, no del producto."""
    texto = "\n".join(str(data.get(k) or "") for k in ("titulo", "descripcion"))
    vistas: list[str] = []
    for m in _FRASE_FUENTE.finditer(texto):
        if m.group(0).lower() not in (v.lower() for v in vistas):
            vistas.append(m.group(0))
    return vistas


def _cubre(termino: str, titulo: str) -> bool:
    """¿El título trae TODAS las palabras del término, como palabras completas y sin acentos? «lg» no
    está en «pulgadas» y «audifonos» sí está en «Audífonos» (la prueba del 30-oct falló en los dos)."""
    from services import marcas_renombre
    palabras = set(marcas_renombre.normalizar(titulo).split())
    buscadas = marcas_renombre.normalizar(termino).split()
    return bool(buscadas) and all(p in palabras for p in buscadas)


def _prohibidos(data: dict[str, Any], fuentes: str) -> dict[str, list[str]]:
    """{marcas, modelos} que quedaron en lo generado. Las marcas se buscan también en los atributos."""
    texto = "\n".join(str(data.get(k) or "") for k in ("titulo", "descripcion"))
    valores = "\n".join(str(a.get("valor") or "") for a in data.get("atributos") or [] if isinstance(a, dict))
    return {"marcas": _marcas_en(f"{texto}\n{valores}"), "modelos": _modelos_en(texto, fuentes)}


async def _preguntar(cli: httpx.AsyncClient, modelo: str, mensajes: list[dict[str, Any]]) -> dict[str, Any]:
    """Una llamada a OpenRouter: {status, data, texto, usd}. `data` = {} si no trajo título."""
    from services import crear_producto  # perezoso, como arriba

    body = {
        "model": modelo, "max_tokens": 2000, "temperature": 0.3,
        "reasoning": {"effort": "low", "exclude": True},
        "response_format": {"type": "json_object"},
        "messages": mensajes,
    }
    try:
        resp = await cli.post(crear_producto._OR_URL, json=body, timeout=120, headers={  # noqa: SLF001
            "Authorization": f"Bearer {settings.openrouter_api_key}", "X-Title": "Kubera Omnicanal"})
    except httpx.HTTPError as exc:
        log.warning("mejorar ML: OpenRouter %s falló: %s", modelo, exc)
        return {"status": 0, "data": {}, "texto": "", "usd": 0.0}
    if resp.status_code != 200:
        log.warning("mejorar ML: OpenRouter %s HTTP %d: %s", modelo, resp.status_code, resp.text[:150])
        return {"status": resp.status_code, "data": {}, "texto": "", "usd": 0.0}
    try:
        d = resp.json()
        texto = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        usd = float((d.get("usage") or {}).get("cost") or 0)
    except (ValueError, AttributeError, IndexError, TypeError):
        texto, usd = "", 0.0
    data = _parse_json(texto)
    if not isinstance(data, dict) or not str(data.get("titulo") or "").strip():
        log.warning("mejorar ML: OpenRouter %s no trajo título", modelo)
        data = {}
    return {"status": 200, "data": data, "texto": texto, "usd": usd}


async def _ml_con_foto(producto: dict[str, Any], referencias: str) -> dict[str, Any]:
    """Título, descripción y lo que se ve, por OpenRouter VIENDO una foto. Nunca lanza.

    {"ok": True, "data": {titulo, descripcion, atributos}, "vision": {...}} o
    {"ok": False, "vision": {"ok": False, "motivo": ...}}: con ok=False, quien llama sigue con el texto."""
    from services import crear_producto  # perezoso, como arriba

    if not settings.openrouter_api_key:
        return _sin_vision("Sin OPENROUTER_API_KEY: la IA solo leyó el texto.")
    fuentes = f"{_contexto(producto)}\n{referencias}"
    async with httpx.AsyncClient(timeout=30) as cli:
        foto = await _foto_para_ia(cli, producto)
        if not foto:
            return _sin_vision("El producto no tiene foto que se pueda leer: la IA solo leyó el texto.")
        de_donde, url = foto
        mensajes: list[dict[str, Any]] = [
            {"role": "system", "content": _ML_CON_FOTO},
            {"role": "user", "content": [
                {"type": "text", "text": f"Foto del producto: {de_donde}."},
                {"type": "image_url", "image_url": {"url": url}},
                {"type": "text", "text": f"Datos del producto:\n{fuentes}\n\nDevuelve SOLO el JSON indicado."},
            ]},
        ]
        for modelo in crear_producto.OPENROUTER_MODELOS_SEO:
            r = await _preguntar(cli, modelo, mensajes)
            if r["status"] in (401, 402):
                # La misma cuenta para todos los modelos: no tiene caso probar el siguiente.
                return _sin_vision("OpenRouter " + ("sin crédito" if r["status"] == 402 else
                                                    "rechazó la llave") + ": la IA solo leyó el texto.")
            if not r["data"]:
                continue
            data, usd, corregidas = r["data"], r["usd"], []
            marcas = _prohibidos(data, fuentes)["marcas"]
            frases = _frases_de_fuente(data)
            if marcas or frases:
                # UNA corrección, con la misma foto y el mismo modelo.
                pedido = []
                if marcas:
                    pedido.append(f"usa marcas, y están PROHIBIDAS en la publicación: {', '.join(marcas)}; "
                                  "quita todo nombre de marca o de modelo (tampoco como «compatible con», "
                                  "«tipo» o «estilo»)")
                if frases:
                    pedido.append(f"le habla al comprador de tus fuentes ({', '.join(frases)}); escribe solo "
                                  "sobre el producto y deja lo que no sea seguro en \"dudas\"")
                otra = await _preguntar(cli, modelo, mensajes + [
                    {"role": "assistant", "content": r["texto"]},
                    {"role": "user", "content": "Tu respuesta " + "; y ".join(pedido) +
                                                ". Reescribe el JSON completo. Devuelve SOLO el JSON."},
                ])
                usd += otra["usd"]
                if otra["data"]:
                    data, corregidas = otra["data"], marcas
                    frases = _frases_de_fuente(data)
            dudas = data.pop("dudas", None)
            vision = {
                "ok": True, "modelo": modelo, "costo_usd": round(usd, 5), "foto": de_donde,
                "coincide": _coincide(data.pop("coincide", "")),
                "lo_que_se_ve": str(data.pop("lo_que_se_ve", "") or "").strip(),
                "dudas": [str(x).strip() for x in dudas if str(x).strip()] if isinstance(dudas, list) else [],
                "marcas_corregidas": corregidas,
            }
            if frases:
                vision["dudas"].append("La descripción todavía habla de la foto o de los datos («"
                                       + "», «".join(frases) + "»): edítala antes de publicar.")
            log.info("mejorar ML con foto (%s) por %s ($%.5f) para %s",
                     de_donde, modelo, usd, producto.get("sku"))
            return {"ok": True, "data": data, "vision": vision}
    return _sin_vision("OpenRouter no contestó: la IA solo leyó el texto.")


def _parse_json(texto: str) -> dict[str, Any]:
    import json
    import re
    t = re.sub(r"^```(?:json)?|```$", "", texto.strip(), flags=re.MULTILINE).strip()
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


async def mejorar(canal: str, producto: dict[str, Any], *,
                  cuenta: str = "") -> dict[str, Any]:
    """Mejora con IA varios campos del canal en una sola llamada (JSON). En Mercado
    Libre, además reemplaza los atributos por los REALES de la categoría
    (principales + secundarios) vía el servicio ml_atributos, y guarda el
    resultado en `enrich.channel_content` bajo (sku, mercado_libre, cuenta)."""
    # Amazon tiene circuito propio desde v0.137.0: los atributos salen de los
    # requisitos REALES de su productType (`channel.field_requirements`), el
    # resultado pasa por el validador de límites y se persiste en
    # `enrich.channel_content`. El prompt de aquí abajo se quedó como está para
    # los demás canales; el de Amazon vive en `amazon_ia._SISTEMA`.
    if canal == "amazon":
        from services import amazon_ia
        return await amazon_ia.mejorar(producto)
    # TikTok, desde v0.143.0: mismo contrato que Amazon — contenido validado
    # contra lo que exige LISTING (no lo que tolera el borrador), atributos de
    # la categoría REAL con sus IDs de valor, y guardado en channel_content.
    if canal == "tiktok":
        from services import tiktok_ia
        return await tiktok_ia.mejorar(producto)
    # Temu, desde v0.168.0: mismo contrato, más su cascada. Los atributos van en
    # DOS vueltas porque los condicionales dependen de lo contestado en los
    # duros — preguntarlos todos de golpe llena voltajes de productos sin
    # electricidad, y eso es lo que manda el producto a Borrador.
    if canal == "temu":
        from services import temu_ia
        return await temu_ia.mejorar(producto)
    # Walmart: mismo contrato. Su diferencia es de dónde salen los atributos —
    # no hay API que los dé (`/v3/items/spec` es 404 en MX), así que vienen de
    # `channel.field_requirements`, ya corregida con lo que producción demostró.
    if canal == "walmart":
        from services import walmart_ia
        return await walmart_ia.mejorar(producto)

    # Mercado Libre (v0.706.0): el título del packing list y las palabras de ML
    # van en los dos caminos; la foto, solo en el de OpenRouter. Sin él, el texto.
    vision: dict[str, Any] | None = None
    mercado: dict[str, Any] | None = None
    titulos_pl: list[str] = []
    referencias = ""
    data: dict[str, Any] = {}
    res: dict[str, Any] = {}
    if canal == "mercado_libre":
        titulos_pl, mercado = await asyncio.gather(
            asyncio.to_thread(_del_catalogo, str(producto.get("sku") or "").strip(), "titulos_pl"),
            _mercado_ml(str(producto.get("ml_cat_id") or "").strip()))
        referencias = _referencias(titulos_pl, mercado)
        con_foto = await _ml_con_foto(producto, referencias)
        vision = con_foto["vision"]
        if con_foto.get("ok"):
            data = con_foto["data"]
            res = {"ok": True, "proveedor": "openrouter"}
    extra_ml = ({"vision": vision, "mercado": mercado, "titulos_pl": titulos_pl}
                if canal == "mercado_libre" else {})
    if not data:
        cfg = _MEJORAR.get(canal) or _MEJORAR["mercado_libre"]
        user = (
            f"Datos del producto:\n{_contexto(producto)}\n{referencias}\n\n"
            "Mejora el contenido y devuelve SOLO el JSON indicado."
        )
        # Sin foto, en ML también valen la prohibición de marcas y modelos y las palabras de ML.
        system = cfg["system"]
        if canal == "mercado_libre":
            system += "\n" + _SIN_MARCAS_NI_MODELOS + ("\n" + _PALABRAS_ML if referencias else "")
        res = await asyncio.to_thread(_completar, system, user, cfg["max_tokens"])
        if not res.get("ok"):
            return {"ok": False, "motivo": res.get("motivo"), "canal": canal, **extra_ml}
        data = _parse_json(res.get("texto", ""))
        if not data:
            return {"ok": False, "motivo": "La IA no devolvió JSON válido.",
                    "canal": canal, "crudo": res.get("texto", "")[:400], **extra_ml}
    # La garantía la ponen las KAM en cada canal: aunque el modelo la escriba, no
    # llega a la descripción (`sin_garantia`, 28-oct-2026).
    if isinstance(data.get("descripcion"), str):
        data["descripcion"] = sin_garantia.quitar(data["descripcion"])

    # ML: qué términos más buscados cubre DE VERDAD el título (la IA lo presume
    # mal: ver `competencia_captura.sugerir_titulo`) y si se pasó de 60.
    titulo_final = str(data.get("titulo") or "").strip()
    if mercado is not None:
        mercado["cubiertos"] = [t for t in mercado["terminos"] if _cubre(t, titulo_final)]
    if canal == "mercado_libre" and len(titulo_final) > 60 and (vision or {}).get("ok"):
        vision["dudas"].append(f"El título tiene {len(titulo_final)} caracteres; Mercado Libre admite 60.")

    # Amazon: el título va SIN acentos (regla de negocio). El prompt ya lo pide,
    # pero los LLM a veces dejan una tilde suelta — se garantiza aquí quitándolas
    # de forma determinista. Solo afecta el TÍTULO (ñ→n incluido); el resto del
    # contenido conserva su ortografía.
    if canal == "amazon" and data.get("titulo"):
        data["titulo"] = _sin_acentos(str(data["titulo"]))

    # Mercado Libre: reemplaza los atributos por los REALES de la categoría
    # (principales + secundarios), con nombre legible + valor.
    reporte_ml: dict[str, Any] = {}
    if canal == "mercado_libre" and str(producto.get("ml_cat_id") or "").strip():
        try:
            from services import ia_variante, ml_atributos
            attrs_actuales = "; ".join(
                f"{a.get('nombre')}: {a.get('valor')}"
                for a in (producto.get("atributos") or []) if a.get("nombre")
            )
            caracteristicas = _sin_html(str(producto.get("descripcion") or ""))[:1500]
            if titulos_pl:
                caracteristicas = f"Título del packing list: {' | '.join(titulos_pl)}\n{caracteristicas}"
            if (vision or {}).get("lo_que_se_ve"):
                # Lo que se vio en la foto va PRIMERO: con eso los atributos dejan
                # de salir solo del título (v0.706.0).
                caracteristicas = f"Lo que se ve en la foto: {vision['lo_que_se_ve']}\n{caracteristicas}"
            caracteristicas = caracteristicas.strip()
            r = await ml_atributos.generar_atributos(
                cat_id=str(producto["ml_cat_id"]).strip(),
                # El título que SE VA A PUBLICAR: el que la IA acaba de proponer.
                # ML compara el Género contra él (`invalid.title.gender`).
                nombre=str(data.get("titulo") or producto.get("nombre", "")),
                alibaba_titulo=producto.get("nombre", ""),
                atributos_actuales=attrs_actuales,
                caracteristicas_clave=caracteristicas,
                sku=str(producto.get("sku") or ""),
                variante=ia_variante.bloque(producto),
            )
            todos = r["meli_attrs"]["principales"] + r["meli_attrs"]["secundarias"]
            nombre_por_id = {a["id"]: a["name"] for a in todos}
            if r["atributos"]:
                # `campo` = el ID de ML (COLOR, UNITS_PER_PACK…), junto al nombre
                # legible. El legible es lo que un humano revisa; el ID es lo
                # único que no se pierde al cruzar con la categoría — "Color" y
                # "Color principal" son dos atributos distintos en ML. Mismo
                # criterio que TikTok (`product_attributes.<id>`).
                data["atributos"] = [
                    {"nombre": nombre_por_id.get(k, k), "campo": k, "valor": v}
                    for k, v in r["atributos"].items()
                ]
            # El parte para el Estudio, con el MISMO contrato que Amazon y TikTok
            # (23-oct-2026): cuántos obligatorios quedaron, qué no se aplicó y por qué
            # (lo que ML rechazaría: «0 W», un valor fuera de la lista) y qué se
            # corrigió (el Género que pide el título). Ver `ml_atributos.depurar`.
            principales = r["meli_attrs"]["principales"]
            faltan = set(r.get("faltan") or [])
            reporte_ml = {
                "product_type": producto.get("categoria") or str(producto["ml_cat_id"]),
                "requisitos": {
                    "estado": "ok" if not faltan else "incompleto",
                    "obligatorios": len(principales),
                    "cubiertos": [a["id"] for a in principales if a["id"] not in faltan],
                    "sin_cubrir": [f"{nombre_por_id.get(i, i)} ({i})" for i in r.get("faltan") or []],
                } if principales else {"estado": "sin_requisitos", "obligatorios": 0,
                                       "cubiertos": [], "sin_cubrir": []},
                "rechazados": r.get("rechazados") or [],
                "avisos": r.get("avisos") or [],
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("mejorar ML atributos: %s", exc)

    salida: dict[str, Any] = {"ok": True, "canal": canal,
                              "proveedor": res.get("proveedor"), "campos": data,
                              **reporte_ml, **extra_ml}
    if canal == "mercado_libre":
        # La revisión FINAL, ya con los atributos de la categoría: marcas y modelos
        # que sigan ahí se reportan para quitarlos antes de publicar (Brandon, 30-oct).
        salida["prohibidos"] = _prohibidos(data, f"{_contexto(producto)}\n{referencias}")
    # ML guarda como los demás canales. Hasta aquí era el ÚNICO (con General)
    # que tiraba lo generado al cerrar el Estudio: Amazon, TikTok, Temu y
    # Walmart lo dejaban en `enrich.channel_content`. General NO se guarda: su
    # destino es Woo, por el botón "Guardar contenido".
    if canal == "mercado_libre":
        salida["guardado"] = await _guardar_ml(producto, data, cuenta)
    return salida


# Versión del contrato de contenido de ML que va a `spec_version`, para poder
# distinguir después lo generado con qué reglas (igual que Amazon y TikTok).
_ML_SPEC_VERSION = "ml-mx-2026-09"


async def _guardar_ml(producto: dict[str, Any], data: dict[str, Any],
                      cuenta: str) -> dict[str, Any] | None:
    """
    Persiste lo generado para ML en `enrich.channel_content`. Nunca lanza.

    La `cuenta` es parte de la llave: BEKURA y SANCORFASHION publican con textos
    distintos, y el publicador de ML lee con la cuenta de la petición
    (`publicar._rellenar_desde_guardado`).

    DOS LÍMITES, porque esta fila la LEE el publicador sin que nadie apriete
    Guardar (rellena los campos vacíos del formulario) y el Estudio siembra con
    ella la ficha:

    1. NO PISA lo que escribió otro. `channel_content.guardar` fusiona llave a
       llave sin mirar el origen, así que un solo clic "para ver qué propone"
       reemplazaba el título que dejó Crear (`crear_producto.
       _guardar_titulo_variante` — el ÚNICO lugar donde una variante tiene
       título propio, porque Woo deriva el de la variación del padre) o el que
       una persona guardó a mano. Se escribe una llave sólo si la fila no la
       tiene o si la tenía la propia IA (`origen[llave] == "ia"`). Si no se
       puede leer la fila, no se escribe nada: sin saber qué hay, no se pisa.
    2. SIN ATRIBUTOS. Los de la IA llevan `nombre` = etiqueta legible ("Color
       principal") y el ID de ML en `campo`, pero `publicar._confirmar_ml`
       manda `{"id": a["nombre"]}` y `publicar_ready.construir_prod` indexa por
       `nombre`: guardados, viajarían a ML como IDs que no existen. Se quedan en
       el formulario, como antes, hasta que el publicador lea `campo`.
    """
    sku = str(producto.get("sku") or "").strip()
    if not sku:
        return None
    campos: dict[str, Any] = {}
    for k in ("titulo", "descripcion"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            campos[k] = v.strip()
    if not campos:
        return None
    try:
        from services import channel_content
        from services.amazon_ia import hash_base
        if not channel_content.disponible():
            return {"ok": False, "sku": sku, "motivo": "KUBERA_DB_URL no configurada."}
        try:
            # CON RESPALDO (7-oct-2026): la misma vista que el Estudio y el
            # publicador. Crear guarda el título de la variante en la fila SIN
            # cuenta; leyendo sólo la de la cuenta, ese título no contaba como
            # "de otro" y un clic en «Mejorar con IA» lo tapaba para BEKURA.
            previo = await asyncio.to_thread(
                channel_content._leer_con_respaldo_sync,  # noqa: SLF001
                sku, "mercado_libre", cuenta or "")
        except Exception as exc:  # noqa: BLE001
            log.warning("mejorar ML: no se pudo leer %s, no se guarda: %s", sku, exc)
            return {"ok": False, "sku": sku,
                    "motivo": "No se pudo leer lo guardado; no se pisó nada."}
        contenido_prev = (previo or {}).get("contenido") or {}
        origen_prev = (previo or {}).get("origen") or {}
        respetados = [k for k in campos
                      if contenido_prev.get(k) and origen_prev.get(k) != "ia"]
        for k in respetados:
            campos.pop(k)
        if not campos:
            return {"ok": False, "sku": sku, "respetados": respetados,
                    "motivo": "No se guardó: " + ", ".join(respetados) +
                              " ya los había escrito una persona o Crear."}
        res = await channel_content.guardar(
            sku, "mercado_libre", campos, cuenta=cuenta or "",
            origen={k: "ia" for k in campos},
            categoria=str(producto.get("ml_cat_id") or "").strip() or None,
            spec_version=_ML_SPEC_VERSION,
            hash_base=hash_base(producto),
        )
        if respetados:
            res["respetados"] = respetados
        return res
    except Exception as exc:  # noqa: BLE001
        log.warning("mejorar ML: no se pudo guardar %s: %s", sku, exc)
        return {"ok": False, "sku": sku, "motivo": str(exc)[:300]}


def generar(canal: str, generador_id: str, producto: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta un generador concreto para un canal y devuelve el contenido."""
    g = _buscar(canal, generador_id)
    if not g:
        return {"ok": False, "motivo": f"Generador '{generador_id}' no existe para {canal}."}

    user = (
        f"Datos del producto:\n{_contexto(producto)}\n\n"
        "Genera el contenido solicitado siguiendo tus instrucciones."
    )
    res = _completar(g["system"], user, max_tokens=g.get("max_tokens", 900))
    if generador_id == "descripcion" and res.get("ok"):
        res["texto"] = sin_garantia.quitar(res.get("texto") or "")  # ver `sin_garantia`
    res.update({"canal": canal, "generador": generador_id, "label": g["label"],
                "tipo": g.get("tipo", "texto")})
    return res
