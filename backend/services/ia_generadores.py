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
            "que no se puedan inferir del producto." + _NO_CONTRADECIR
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
            '"atributos": [{"nombre": "..", "valor": ".."}]}'
        ),
    },
    # TIKTOK tampoco está aquí desde v0.143.0: `mejorar()` lo desvía a
    # `tiktok_ia`. El prompt que vivía en esta entrada pedía un título de "máx
    # 45 caracteres" cuando MX admite **300** — 255 caracteres tirados en el
    # campo que más pesa para que a uno lo encuentren.
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
            from services import ia_variante, ml_atributos
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
        except Exception as exc:  # noqa: BLE001
            log.warning("mejorar ML atributos: %s", exc)

    salida: dict[str, Any] = {"ok": True, "canal": canal,
                              "proveedor": res.get("proveedor"), "campos": data}
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
            previo = await asyncio.to_thread(
                channel_content._leer_sync, sku, "mercado_libre", cuenta or "")  # noqa: SLF001
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
    res.update({"canal": canal, "generador": generador_id, "label": g["label"],
                "tipo": g.get("tipo", "texto")})
    return res
