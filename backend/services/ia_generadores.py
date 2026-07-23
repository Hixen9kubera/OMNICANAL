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
from typing import Any

import httpx

from config import settings

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
# Prompts Amazon (instrucciones provistas por Kubera, vigentes 27-jul-2026)
# ─────────────────────────────────────────────────────────────────────────────
_AMZ_BASE = (
    "Eres un experto en optimización de listings para Amazon México "
    "(amazon.com.mx), con dominio de los lineamientos vigentes a partir del "
    "27 de julio de 2026. Escribe TODO en español. Respeta ESTRICTAMENTE los "
    "límites de caracteres indicados y verifica el conteo exacto antes de "
    "entregar. No inventes datos que no se puedan inferir del producto."
)

_AMZ_TITULO = _AMZ_BASE + (
    "\n\nGenera el TÍTULO (máximo 75 caracteres, incluyendo espacios).\n"
    "Reglas:\n"
    "• Mayúscula en la primera letra de cada sustantivo importante.\n"
    "• Incluye: tipo de producto + característica 1 + característica 2 / tamaño; "
    "marca solo si aporta.\n"
    "• Prohibido: signos especiales (! $ * ~), palabras promocionales (oferta, "
    "gratis, mejor), emojis.\n"
    "• Formato: [Tipo de producto] + [Característica 1] + [Característica 2 / Tamaño].\n"
    "Devuelve SOLO el título en una línea y, debajo, «(N caracteres)»."
)

_AMZ_HIGHLIGHTS = _AMZ_BASE + (
    "\n\nGenera el ITEM HIGHLIGHTS (máximo 125 caracteres, incluyendo espacios).\n"
    "Es el segundo campo indexable: úsalo para palabras clave secundarias.\n"
    "Reglas:\n"
    "• Incluye materiales, casos de uso, público objetivo o ventaja competitiva.\n"
    "• Frase natural, no lista. No repitas el título. Sin palabras promocionales.\n"
    "Devuelve SOLO la frase y, debajo, «(N caracteres)»."
)

_AMZ_BULLETS = _AMZ_BASE + (
    "\n\nGenera 5 BULLET POINTS (cada uno entre 150 y 200 caracteres, incluyendo "
    "espacios).\n"
    "Estructura obligatoria:\n"
    "1) Beneficio principal (no una característica).\n"
    "2) Material / durabilidad / construcción.\n"
    "3) Compatibilidad o casos de uso específicos.\n"
    "4) Facilidad de uso / instalación / mantenimiento.\n"
    "5) Garantía, certificación o propuesta de valor diferencial.\n"
    "Formato de cada bullet: [CARACTERÍSTICA EN MAYÚSCULAS]: descripción del "
    "beneficio concreto. Oraciones completas, no listas de keywords.\n"
    "Devuelve los 5 bullets, uno por línea, y al final el conteo de caracteres de "
    "cada uno."
)

_AMZ_DESCRIPCION = _AMZ_BASE + (
    "\n\nGenera la DESCRIPCIÓN (máximo 2000 caracteres, incluyendo espacios).\n"
    "En párrafos (NO listas):\n"
    "• Párrafo 1: propuesta de valor y contexto de uso (quién lo necesita y por qué).\n"
    "• Párrafo 2: características técnicas y materiales con sus beneficios.\n"
    "• Párrafo 3: casos de uso específicos y compatibilidades.\n"
    "• Cierre: llamada a la acción natural.\n"
    "Tono informativo, profesional, orientado al beneficio. Incorpora keywords "
    "long-tail de forma natural.\n"
    "Devuelve la descripción y, al final, «(N caracteres)»."
)

_AMZ_ATRIBUTOS = _AMZ_BASE + (
    "\n\nGenera la TABLA DE ATRIBUTOS recomendada para publicar este producto en "
    "Amazon México. Detecta el tipo de producto (product_type) e infiere los "
    "atributos clave y obligatorios de esa categoría (marca, fabricante, material, "
    "color, tamaño/dimensiones, cantidad, público objetivo, país de origen, etc.).\n"
    "Devuelve en formato «Atributo: valor», uno por línea. Marca con «(sugerido)» "
    "los valores que estás infiriendo y con «(requerido)» los obligatorios que "
    "falten por completar."
)

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
    "(están prohibidos). Enfócate en beneficios, usos y características."
)
_GEN_TITULO = (
    "Eres redactor de e-commerce. Genera un título comercial claro y atractivo "
    "para la tienda (WooCommerce), con la palabra clave principal al inicio. "
    "Devuelve solo el título."
)
_GEN_DESCRIPCION = (
    "Eres redactor de e-commerce. Genera una descripción de producto para "
    "WooCommerce en HTML simple (<p>, <ul>, <li>, <strong>): un párrafo de "
    "introducción, una lista de características/beneficios y un cierre. Devuelve "
    "solo el HTML."
)
_TT_TITULO = (
    "Eres experto en TikTok Shop. Genera un título corto, llamativo y orientado a "
    "contenido viral (máx 45 caracteres) con un gancho emocional. Devuelve solo "
    "el título."
)


# ─────────────────────────────────────────────────────────────────────────────
# Registro de generadores por canal
#   icono = clave que el frontend mapea a un ícono (lucide)
#   tipo  = "texto" | "imagenes" (imagenes = plan + prompts, también texto)
# ─────────────────────────────────────────────────────────────────────────────
GENERADORES: dict[str, list[dict[str, Any]]] = {
    "amazon": [
        {"id": "titulo", "label": "Título", "icono": "type", "max_tokens": 300,
         "descripcion": "Título Amazon MX ≤75 caracteres", "system": _AMZ_TITULO},
        {"id": "highlights", "label": "Item Highlights", "icono": "sparkles", "max_tokens": 300,
         "descripcion": "Campo indexable ≤125 caracteres", "system": _AMZ_HIGHLIGHTS},
        {"id": "bullets", "label": "Bullet Points", "icono": "list", "max_tokens": 900,
         "descripcion": "5 bullets de 150–200 caracteres", "system": _AMZ_BULLETS},
        {"id": "descripcion", "label": "Descripción", "icono": "align-left", "max_tokens": 1200,
         "descripcion": "Descripción ≤2000 caracteres", "system": _AMZ_DESCRIPCION},
        {"id": "atributos", "label": "Atributos Amazon", "icono": "tags", "max_tokens": 900,
         "descripcion": "Tabla de atributos por categoría", "system": _AMZ_ATRIBUTOS},
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
    "tiktok": [
        {"id": "titulo", "label": "Título viral", "icono": "type", "max_tokens": 200,
         "descripcion": "Título corto y llamativo", "system": _TT_TITULO},
    ],
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
            "que no se puedan inferir del producto." + _NO_CONTRADECIR
        ),
    },
    "amazon": {
        "max_tokens": 2200,
        "system": _AMZ_BASE + (
            "\n\nMejora el listing completo. Devuelve SOLO JSON válido:\n"
            '{"titulo": "<máx 75 car.>", "highlights": "<máx 125 car.>", '
            '"bullets": ["<150-200 car.>", "..x5.."], '
            '"descripcion": "<máx 2000 car., en párrafos>", '
            '"atributos": [{"nombre": "..", "valor": ".."}]}\n'
            "Respeta ESTRICTAMENTE los límites de caracteres. En atributos infiere los "
            "obligatorios de la categoría (product_type). "
            "El TÍTULO va SIN NINGÚN ACENTO ni tilde (á→a, é→e, í→i, ó→o, ú→u, ñ→n): "
            "escribe cada palabra del título sin marcas diacríticas." + _NO_CONTRADECIR
        ),
    },
    "general": {
        "max_tokens": 1200,
        "system": (
            "Eres redactor de e-commerce (WooCommerce). Devuelve SOLO JSON válido:\n"
            '{"titulo": "<título comercial claro>", '
            '"descripcion": "<HTML simple: <p>, <ul>, <li>, <strong>>", '
            '"atributos": [{"nombre": "..", "valor": ".."}]}'
        ),
    },
    "tiktok": {
        "max_tokens": 400,
        "system": (
            "Eres experto en TikTok Shop. Devuelve SOLO JSON válido:\n"
            '{"titulo": "<corto, viral, máx 45 car.>", "descripcion": "<gancho breve>"}'
        ),
    },
}


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


async def mejorar(canal: str, producto: dict[str, Any]) -> dict[str, Any]:
    """Mejora con IA varios campos del canal en una sola llamada (JSON). En Mercado
    Libre, además reemplaza los atributos por los REALES de la categoría
    (principales + secundarios) vía el servicio ml_atributos."""
    cfg = _MEJORAR.get(canal) or _MEJORAR["mercado_libre"]
    user = (
        f"Datos del producto:\n{_contexto(producto)}\n\n"
        "Mejora el contenido y devuelve SOLO el JSON indicado."
    )
    res = await asyncio.to_thread(_completar, cfg["system"], user, cfg["max_tokens"])
    if not res.get("ok"):
        return {"ok": False, "motivo": res.get("motivo"), "canal": canal}
    data = _parse_json(res.get("texto", ""))
    if not data:
        return {"ok": False, "motivo": "La IA no devolvió JSON válido.",
                "canal": canal, "crudo": res.get("texto", "")[:400]}

    # Amazon: el título va SIN acentos (regla de negocio). El prompt ya lo pide,
    # pero los LLM a veces dejan una tilde suelta — se garantiza aquí quitándolas
    # de forma determinista. Solo afecta el TÍTULO (ñ→n incluido); el resto del
    # contenido conserva su ortografía.
    if canal == "amazon" and data.get("titulo"):
        data["titulo"] = _sin_acentos(str(data["titulo"]))

    # Mercado Libre: reemplaza los atributos por los REALES de la categoría
    # (principales + secundarios), con nombre legible + valor.
    if canal == "mercado_libre" and str(producto.get("ml_cat_id") or "").strip():
        try:
            from services import ml_atributos
            attrs_actuales = "; ".join(
                f"{a.get('nombre')}: {a.get('valor')}"
                for a in (producto.get("atributos") or []) if a.get("nombre")
            )
            r = await ml_atributos.generar_atributos(
                cat_id=str(producto["ml_cat_id"]).strip(),
                nombre=producto.get("nombre", ""),
                alibaba_titulo=producto.get("nombre", ""),
                atributos_actuales=attrs_actuales,
                caracteristicas_clave=_sin_html(str(producto.get("descripcion") or ""))[:1500],
                sku=str(producto.get("sku") or ""),
            )
            todos = r["meli_attrs"]["principales"] + r["meli_attrs"]["secundarias"]
            nombre_por_id = {a["id"]: a["name"] for a in todos}
            if r["atributos"]:
                data["atributos"] = [
                    {"nombre": nombre_por_id.get(k, k), "valor": v}
                    for k, v in r["atributos"].items()
                ]
        except Exception as exc:  # noqa: BLE001
            log.warning("mejorar ML atributos: %s", exc)

    return {"ok": True, "canal": canal, "proveedor": res.get("proveedor"),
            "campos": data}


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
    res.update({"canal": canal, "generador": generador_id, "label": g["label"],
                "tipo": g.get("tipo", "texto")})
    return res
