"""
amazon_ia.py — El generador de contenido de Amazon: IA que propone, código que
valida, y un documento que SOBREVIVE a la sesión.

QUÉ RESUELVE
------------
Mercado Libre ya tenía el circuito completo: se genera con IA, los atributos
salen de la categoría REAL (`ml_atributos`) y el resultado se persiste. Amazon
tenía la mitad — `ia_generadores._MEJORAR["amazon"]` pedía un JSON, le quitaba
los acentos al título y lo devolvía. Nadie comprobaba los límites, los atributos
se los inventaba la IA sin mirar lo que Amazon exige de ESA categoría, no había
términos de búsqueda, y si no publicabas en la misma sesión se perdía todo.

Este módulo es la otra mitad, y son cuatro piezas encadenadas:

  1. **Los requisitos REALES** de su `productType`, leídos de
     `channel.field_requirements` (64,125 filas, 553 tipos, cargadas el 12-ago).
  2. **El prompt de Brandon**, literal, con los cinco campos y sus límites.
  3. **El validador** (`amazon_contenido.validar`) + UNA ronda de reparación con
     los problemas de vuelta. Lo que sigue sin pasar NO SE MANDA: se devuelve
     como `rechazados` con su motivo.
  4. **El documento persistido** en `enrich.channel_content` con `origen: ia`.

⚠️ EL CRUCE QUE PARECE OBVIO Y ESTÁ MAL
---------------------------------------
Los requisitos se buscan por `listings.product_type` ↔
`field_requirements.categoria_id`. `listings.category_id` EXISTE y guarda otra
cosa: unir por ahí devuelve cero filas **sin dar error**, y el resultado sería un
"esta categoría no tiene requisitos" en todo el catálogo teniendo 64,125
cargados. La ausencia de error no es evidencia de éxito.

POR QUÉ EL VALIDADOR NO CORRIGE SOLO
------------------------------------
Amazon no rebota cuando te pasas: trunca el título en silencio o ignora el campo
entero. Truncar por nuestra cuenta sería repetir ese pecado dentro de casa. Un
título de 78 caracteres vuelve como rechazado con su motivo, y lo escribe una
persona. La única excepción es determinista y está declarada: quitar acentos del
título (regla de negocio vieja) y sustituir marcas registradas
(`terminos_protegidos`, con lista cerrada y auditable).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.amazon_ia")

CANAL = "amazon"

# La versión del contrato de contenido. Va a `enrich.channel_content.spec_version`
# para poder distinguir después lo generado con qué reglas — igual que Walmart
# distingue 3.11 de 3.19.
SPEC_VERSION = "amazon-mx-2026-07"

# Decisión de Brandon (13-ago-2026): las siguientes publicaciones van TODAS con
# `Generic`. La IA no propone marca; si la propone, se ignora. Además `Generic`
# es lo que sostiene la exención de GTIN en Amazon.
MARCA = "Generic"


# ─────────────────────────────────────────────────────────────────────────────
# El prompt — el spec de Brandon, literal
# ─────────────────────────────────────────────────────────────────────────────
_SISTEMA = (
    "Eres un experto en optimización de listings para Amazon México "
    "(amazon.com.mx), con dominio de los lineamientos vigentes en 2026. "
    "Escribe TODO en español de México. Respeta ESTRICTAMENTE los límites y "
    "cuenta los caracteres antes de entregar. No inventes datos que no se "
    "puedan inferir del producto.\n\n"
    "Devuelve SOLO JSON válido, sin markdown ni texto alrededor:\n"
    '{"titulo": "…", "highlights": "…", "bullets": ["…","…","…","…","…"], '
    '"descripcion": "…", "backend_search_terms": "…", '
    '"atributos": [{"nombre": "…", "valor": "…"}]}\n\n'
    "TÍTULO — máximo 75 caracteres. Mayúscula en cada sustantivo importante. "
    "Formato: tipo de producto + característica + material/tamaño. Prohibidos: "
    "los signos ! $ * ~, las palabras promocionales (oferta, gratis, mejor, "
    "descuento, garantizado, 100%) y los emojis.\n\n"
    "ITEM HIGHLIGHTS — máximo 125 caracteres. Es el segundo campo indexable: "
    "palabras clave SECUNDARIAS. Materiales, casos de uso, público objetivo o "
    "ventaja competitiva. Frase natural, no una lista. No repitas el título.\n\n"
    "BULLET POINTS — exactamente 5, cada uno entre 150 y 200 caracteres. En "
    "este orden: (1) beneficio principal, no una característica; (2) material, "
    "durabilidad o construcción; (3) compatibilidad o casos de uso; (4) "
    "facilidad de uso, instalación o mantenimiento; (5) garantía o propuesta "
    "diferencial. Oraciones completas, mayúscula inicial, sin emojis y sin "
    "listas de keywords.\n\n"
    "DESCRIPCIÓN — máximo 2000 caracteres, en PÁRRAFOS (no listas): "
    "(1) propuesta de valor y contexto de uso; (2) características técnicas con "
    "su beneficio; (3) casos de uso y compatibilidades; cierre con una llamada "
    "a la acción natural. Incorpora long-tail con naturalidad.\n\n"
    "BACKEND SEARCH TERMS — máximo 249 BYTES en UTF-8, que NO es lo mismo que "
    "249 caracteres: cada acento y cada ñ pesan 2 bytes. ESCRÍBELOS SIN ACENTOS "
    "para no desperdiciar espacio (Amazon los normaliza igual). Palabras "
    "separadas por espacios, SIN comas ni puntuación. Sinónimos, variaciones "
    "regionales y errores comunes de escritura. NO repitas ninguna palabra que "
    "ya esté en el título ni en los highlights: ahí se desperdicia el campo. "
    "Un byte de más y Amazon ignora el campo ENTERO, sin avisar.\n\n"
    f"MARCA — la marca es siempre «{MARCA}». No propongas ninguna otra y no "
    "menciones marcas registradas de terceros en ningún campo (ni como "
    "comparación de estilo: «tipo Pandora», «calidad Bose»). Solo se admite "
    "nombrar una marca ajena si el producto es realmente compatible con ella, "
    "y entonces escribe «compatible con …».\n\n"
    "IMPORTANTE: el TÍTULO y la DESCRIPCIÓN actuales definen QUÉ ES el "
    "producto. Si la categoría o los atributos recibidos los contradicen "
    "(pueden ser residuos de otro producto), IGNÓRALOS por completo y NO "
    "cambies el tipo de producto."
)


def _bloque_atributos(reqs: list[dict[str, Any]], pt: str | None) -> str:
    """
    La parte del prompt que sale de la BASE, no de la imaginación del modelo.

    Se piden SOLO los obligatorios que nadie más llena: los que ya tienen
    `default_value` los pone el publicador (`brand`, `country_of_origin`,
    `supplier_declared_dg_hz_regulation`…) y los canónicos de texto
    (`item_name`, `product_description`, `bullet_point`) son los campos de
    arriba. Pedirlos otra vez como atributo genera duplicados que después hay
    que limpiar a mano.
    """
    if not pt:
        return ("\n\nATRIBUTOS — no se pudo determinar el tipo de producto de "
                "Amazon. Propón los atributos técnicos evidentes del producto "
                "(material, color, tamaño, cantidad) y nada más.")
    ya_cubiertos = {"titulo", "descripcion", "bullets", "highlights", "brand"}
    pedir = [r for r in reqs
             if r["obligatorio"]
             and r.get("default_value") is None
             and (r.get("campo_canonico") or "") not in ya_cubiertos]
    if not pedir:
        return (f"\n\nATRIBUTOS — el tipo «{pt}» no exige atributos extra: los "
                f"obligatorios los cubren el título, la descripción, los bullets "
                f"y los valores por omisión del publicador. Aun así, propón los "
                f"atributos técnicos evidentes (material, color, tamaño).")
    lineas = "\n".join(f"  · {r['campo']}" for r in pedir)
    return (
        f"\n\nATRIBUTOS — el tipo de producto de Amazon es «{pt}» y estos son "
        f"sus campos OBLIGATORIOS que hoy no tiene nadie quien los llene. "
        f"Devuélvelos en `atributos` usando EXACTAMENTE ese nombre en "
        f"`nombre`:\n{lineas}\n"
        f"Si un valor no se puede inferir del producto, NO lo inventes: omite "
        f"esa entrada. Puedes añadir después otros atributos técnicos evidentes "
        f"(material, color, tamaño, cantidad) con nombre libre."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Resolver el productType con la precedencia de la casa
# ─────────────────────────────────────────────────────────────────────────────
def _product_type(sku: str | None, wc_id: int | None) -> tuple[str | None, str]:
    """
    (product_type, origen) con la regla 2 de la casa: panel > histórico > auto.

    Se reusa `publicar._pt_resuelto` a propósito en vez de reimplementarlo: si
    esa precedencia se duplica, un día dicen cosas distintas y el contenido se
    genera para una categoría y se publica en otra.
    """
    if not sku:
        return None, "auto"
    try:
        from services import publicar, studio
        if not wc_id:
            wc_id = (studio.metadata(sku, None) or {}).get("wc_id")
        return publicar._pt_resuelto(sku, wc_id)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        log.warning("amazon_ia: no se pudo resolver el productType de %s: %s", sku, exc)
        return None, "auto"


async def _detectar(nombre: str) -> tuple[str | None, str]:
    """
    El último escalón de la precedencia: preguntarle a Amazon por keywords.

    POR QUÉ HACE FALTA. `_pt_resuelto` mira la meta del panel y el histórico de
    `amazon_progress`. Un producto RECIÉN CREADO no tiene ninguna de las dos, así
    que devolvía `None` y el contenido se generaba **sin los requisitos de su
    categoría**: título, bullets y descripción salían bien, pero los atributos
    eran genéricos (Material, Color, Cantidad) en vez de los obligatorios del
    tipo. Verificado con un SKU nuevo antes de escribir esto.

    Es la MISMA llamada que hace el publicador al publicar
    (`publicar._detectar_product_type`), a propósito: si se detectara distinto,
    el contenido se escribiría para una categoría y se publicaría en otra.

    Su respaldo es `HOME` cuando ninguna keyword pega — y `HOME` tiene sus 110
    requisitos cargados, así que el respaldo tampoco deja al generador a ciegas.

    NO escribe la meta `amz_product_type`: esa es la elección HUMANA del panel y
    manda sobre todo lo demás (regla 2 de la casa). Inventarla desde aquí
    convertiría una detección automática en una decisión de persona.
    """
    if not (nombre or "").strip():
        return None, "auto"
    try:
        from services import amazon as _amz, publicar
        token = await _amz._access_token()  # noqa: SLF001
        pt = await publicar._detectar_product_type(  # noqa: SLF001
            token, nombre, settings.amazon_marketplace_id)
        return (pt or None), ("deteccion" if pt else "auto")
    except Exception as exc:  # noqa: BLE001
        log.warning("amazon_ia: no se pudo detectar el productType de «%s»: %s",
                    nombre[:60], exc)
        return None, "auto"


def hash_base(producto: dict[str, Any]) -> str:
    """
    Huella (sha1, 40 caracteres — el ancho exacto de la columna) de la BASE con
    la que se generó el contenido: el nombre, la descripción y los atributos que
    la IA recibió.

    Para qué sirve: comparar después contra la base actual y saber si el producto
    cambió en Woo desde que se generó. Guardar la huella es lo que HACE POSIBLE
    esa comparación — hoy nadie la corre automáticamente (ver README).
    """
    base = {
        "nombre": (producto.get("nombre") or "").strip(),
        "descripcion": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ",
                              str(producto.get("descripcion") or ""))).strip(),
        "atributos": sorted(
            f"{(a.get('nombre') or '').strip()}={(a.get('valor') or '').strip()}"
            for a in (producto.get("atributos") or []) if a.get("nombre")
        ),
    }
    crudo = json.dumps(base, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(crudo.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Del problema del validador al campo que lo causó
# ─────────────────────────────────────────────────────────────────────────────
_CAMPO_DE = {
    "titulo": "titulo", "highlights": "highlights", "descripcion": "descripcion",
    "bullets": "bullets", "backend_search_terms": "backend_search_terms",
}

# Las llaves canónicas del documento. `backend_search_terms` es nueva: el
# publicador la traduce a `generic_keyword`, que es como se llama en Amazon.
_LLAVES = ("titulo", "highlights", "bullets", "descripcion",
           "backend_search_terms", "atributos")


def _campo_del_problema(problema: str) -> str | None:
    """`"bullet 3: 212 caracteres…"` → `"bullets"`. Sin prefijo conocido, None."""
    prefijo = problema.split(":", 1)[0].strip()
    if prefijo.startswith("bullet "):
        return "bullets"
    return _CAMPO_DE.get(prefijo)


# NO todos los problemas cuestan lo mismo, y tratarlos igual sale caro.
#
# Medido en la primera corrida en vivo (13-ago, 3 SKUs): 2 perdieron el campo
# `backend_search_terms` COMPLETO —245 bytes de palabras clave— porque repetían
# UNA palabra del título. Uno de ellos, la palabra «de». Amazon no castiga eso:
# solo desaprovecha espacio. Tirar el campo entero es peor que la falta.
#
# Así que hay dos niveles, y el criterio es qué hace AMAZON con el campo:
#   FATAL  → Amazon lo trunca, lo ignora entero o puede suprimir el listado
#            (título fuera de límite o con signos/promos, highlights o
#            descripción pasados, términos de búsqueda pasados de BYTES, emojis).
#            Eso NO SE MANDA: vuelve como `rechazado` y lo escribe una persona.
#   AVISO  → el contenido es publicable pero se aparta del estilo pedido
#            (un bullet de 148 en vez de 150, menos de 5 bullets, repetir
#            palabras). SE MANDA y se reporta para que se vea en el panel.
_AVISOS = ("repite ", "rango ", "no empieza con mayúscula", "se esperan ")


def _es_fatal(problema: str) -> bool:
    return not any(a in problema for a in _AVISOS)


# ─────────────────────────────────────────────────────────────────────────────
# El generador
# ─────────────────────────────────────────────────────────────────────────────
async def mejorar(producto: dict[str, Any], *, guardar: bool = True,
                  cuenta: str = "") -> dict[str, Any]:
    """
    Genera el contenido de Amazon, lo valida y (si hay SKU) lo persiste.

    Devuelve siempre una forma estable, incluso al fallar:
      campos              lo que PASÓ el validador — es lo único publicable
      rechazados          [{campo, motivo}] lo FATAL: Amazon lo truncaría o lo
                          ignoraría entero, así que no se aplica
      avisos              se aparta del estilo pedido pero es publicable
      terminos_detectados marcas registradas encontradas y qué se hizo con ellas
      requisitos          qué exige su productType y qué quedó sin cubrir
      guardado            resultado de escribir en enrich.channel_content
    """
    from services import amazon_contenido, ia_generadores, terminos_protegidos

    sku = str(producto.get("sku") or "").strip()
    pt, pt_origen = await asyncio.to_thread(
        _product_type, sku or None, producto.get("wc_id"))
    # Panel > histórico > DETECCIÓN. El tercer escalón existe para el producto
    # recién creado, que no tiene ninguno de los dos primeros: sin él, su
    # contenido se generaba sin los obligatorios de su categoría.
    if not pt:
        pt, pt_origen = await _detectar(str(producto.get("nombre") or ""))

    from services import channel_content
    reqs = await channel_content.requisitos(CANAL, pt, solo_obligatorios=True)

    sistema = _SISTEMA + _bloque_atributos(reqs, pt)
    user = (f"Datos del producto:\n{ia_generadores._contexto(producto)}\n\n"  # noqa: SLF001
            "Genera el contenido y devuelve SOLO el JSON indicado.")

    res = await asyncio.to_thread(ia_generadores._completar, sistema, user, 3000)  # noqa: SLF001
    if not res.get("ok"):
        return _vacio(res.get("motivo") or "La IA no respondió.", pt, pt_origen)
    data = ia_generadores._parse_json(res.get("texto", ""))  # noqa: SLF001
    if not data:
        return _vacio("La IA no devolvió JSON válido.", pt, pt_origen,
                      crudo=res.get("texto", "")[:400])

    data = _normalizar(data)
    _, problemas = amazon_contenido.validar(data)

    # UNA ronda de reparación. No dos: si con los problemas enfrente no lo
    # arregla, insistir gasta tokens y tiempo para el mismo resultado. Lo que
    # sobreviva se devuelve rechazado y lo escribe una persona.
    if problemas:
        log.info("amazon_ia(%s): %d problema(s), reparando", sku or "?", len(problemas))
        reparado = await _reparar(sistema, user, data, problemas)
        if reparado:
            data = _normalizar(reparado)
            _, problemas = amazon_contenido.validar(data)

    # Marcas registradas: determinista, con lista, DESPUÉS de la IA y ANTES de
    # quitarle los acentos al título (el reemplazo puede traer acentos propios).
    data, terminos = terminos_protegidos.revisar_campos(data)

    # El título de Amazon va sin acentos. Regla de negocio vieja; el prompt ya lo
    # pide, pero los modelos dejan una tilde suelta y esto lo cierra.
    if data.get("titulo"):
        data["titulo"] = ia_generadores._sin_acentos(str(data["titulo"]))  # noqa: SLF001

    # La marca no se negocia (decisión del 13-ago): fuera cualquier atributo de
    # marca que haya propuesto la IA.
    data["atributos"] = [a for a in (data.get("atributos") or [])
                         if (a.get("nombre") or "").strip().lower()
                         not in ("brand", "marca", "manufacturer", "fabricante")]

    # Lo que no pasó, no se manda. Y solo viajan las llaves CANÓNICAS: lo que el
    # modelo invente de más (`seo_title`, `keywords`…) no entra al jsonb, que si
    # no se llena de llaves que nadie lee y nadie se atreve a borrar.
    campos, rechazados = {}, []
    malos: dict[str, list[str]] = {}
    avisos: list[str] = []
    for p in problemas:
        c = _campo_del_problema(p)
        if c and _es_fatal(p):
            malos.setdefault(c, []).append(p)
        else:
            avisos.append(p)
    for k in _LLAVES:
        v = data.get(k)
        if k in malos:
            rechazados.append({"campo": k, "motivo": "; ".join(malos[k])})
        elif v not in (None, "", [], {}):
            campos[k] = v

    salida: dict[str, Any] = {
        "ok": True, "canal": CANAL,
        "proveedor": res.get("proveedor"), "modelo": res.get("modelo"),
        "campos": campos,
        "rechazados": rechazados,   # fatales: NO se aplican
        "avisos": avisos,           # publicable, pero fuera del estilo pedido
        "problemas": problemas,     # la lista cruda del validador, sin clasificar
        "terminos_detectados": terminos,
        "product_type": pt, "product_type_origen": pt_origen,
        "requisitos": _cobertura(reqs, campos, pt),
        "guardado": None,
    }

    if guardar and sku and campos:
        salida["guardado"] = await channel_content.guardar(
            sku, CANAL, campos, cuenta=cuenta,
            origen={k: "ia" for k in campos},
            categoria=pt, spec_version=SPEC_VERSION,
            hash_base=hash_base(producto),
        )
    return salida


def _vacio(motivo: str, pt: str | None, pt_origen: str, **extra) -> dict[str, Any]:
    return {"ok": False, "canal": CANAL, "motivo": motivo, "campos": {},
            "rechazados": [], "avisos": [], "problemas": [],
            "terminos_detectados": [],
            "product_type": pt, "product_type_origen": pt_origen,
            "guardado": None, **extra}


def _normalizar(data: dict[str, Any]) -> dict[str, Any]:
    """Recorta espacios y tipa lo que la IA manda con formato libre."""
    d = dict(data or {})
    for k in ("titulo", "highlights", "descripcion", "backend_search_terms"):
        if d.get(k) is not None:
            d[k] = str(d[k]).strip()
    # `item_highlights` es el nombre que a veces devuelve el modelo (así se llama
    # en la guía de Amazon). La llave canónica del panel es `highlights`.
    if not d.get("highlights") and d.get("item_highlights"):
        d["highlights"] = str(d.pop("item_highlights")).strip()
    b = d.get("bullets")
    if isinstance(b, str):
        b = [x.strip() for x in b.split("\n")]
    if isinstance(b, list):
        d["bullets"] = [str(x).strip() for x in b if str(x).strip()]
    a = d.get("atributos")
    if isinstance(a, dict):                      # {"material": "PVC"} → lista
        a = [{"nombre": k, "valor": v} for k, v in a.items()]
    if isinstance(a, list):
        d["atributos"] = [
            {"nombre": str(x.get("nombre") or "").strip(),
             "valor": str(x.get("valor") or "").strip()}
            for x in a if isinstance(x, dict) and str(x.get("nombre") or "").strip()
            and str(x.get("valor") or "").strip()
        ]
    return d


async def _reparar(sistema: str, user: str, data: dict[str, Any],
                   problemas: list[str]) -> dict[str, Any] | None:
    """Le devuelve a la IA su propio JSON con la lista de fallos, en su idioma."""
    from services import ia_generadores

    lista = "\n".join(f"  · {p}" for p in problemas)
    reintento = (
        f"{user}\n\nTu respuesta anterior fue:\n"
        f"{json.dumps(data, ensure_ascii=False)}\n\n"
        f"El validador la rechazó por esto:\n{lista}\n\n"
        "Corrige ÚNICAMENTE esos campos, deja los demás idénticos y devuelve "
        "otra vez el JSON completo. Cuenta los caracteres uno por uno antes de "
        "responder; para los términos de búsqueda cuenta BYTES (los acentos "
        "pesan 2, escríbelos sin acentos)."
    )
    try:
        r = await asyncio.to_thread(ia_generadores._completar, sistema, reintento, 3000)  # noqa: SLF001
        if r.get("ok"):
            return ia_generadores._parse_json(r.get("texto", "")) or None  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        log.warning("amazon_ia: la reparación falló: %s", exc)
    return None


def _cobertura(reqs: list[dict[str, Any]], campos: dict[str, Any],
               pt: str | None) -> dict[str, Any]:
    """
    Qué obligatorios de su categoría quedaron cubiertos y cuáles no.

    Tres estados, como el semáforo del panel: sin requisitos leídos NO es lo
    mismo que "no le falta nada". De los 553 productTypes están los 553, pero un
    SKU sin productType resuelto cae igual en `sin_requisitos`.
    """
    if not pt or not reqs:
        return {"estado": "sin_requisitos", "product_type": pt,
                "obligatorios": 0, "cubiertos": [], "sin_cubrir": []}
    nombres_attr = {(a.get("nombre") or "").strip().lower()
                    for a in (campos.get("atributos") or [])}
    cubiertos, sin_cubrir = [], []
    for r in reqs:
        canonico = r.get("campo_canonico")
        if canonico and canonico in campos and campos[canonico]:
            cubiertos.append(r["campo"])
        elif r.get("default_value") is not None:
            cubiertos.append(r["campo"])          # lo pone el publicador
        elif r["campo"].lower() in nombres_attr:
            cubiertos.append(r["campo"])
        else:
            sin_cubrir.append(r["campo"])
    return {"estado": "ok" if not sin_cubrir else "incompleto",
            "product_type": pt, "obligatorios": len(reqs),
            "cubiertos": cubiertos, "sin_cubrir": sin_cubrir}


# ─────────────────────────────────────────────────────────────────────────────
# Enganche del alta de productos (pestaña Crear)
# ─────────────────────────────────────────────────────────────────────────────
async def generar_para_alta(sku: str, titulo: str, descripcion: str,
                            atributos: list[dict[str, Any]] | None = None,
                            categoria: str | None = None,
                            precio: float | None = None) -> dict[str, Any] | None:
    """
    Lo que corre al CREAR un producto: genera el contenido de Amazon y lo deja
    guardado, para que el día que se publique ya esté escrito.

    Nace APAGADO (`AMAZON_IA_EN_CREAR=false`). Encenderlo cambia lo que hace un
    flujo vivo —cada alta gasta 1-2 llamadas de IA y escribe en producción—, y
    eso lleva el dale de Brandon, no un deploy silencioso.

    Nunca lanza: crear un producto NO puede fallar porque la IA esté caída.
    """
    if not settings.amazon_ia_en_crear:
        return None
    try:
        return await mejorar({
            "sku": sku, "nombre": titulo, "descripcion": descripcion,
            "atributos": atributos or [], "categoria": categoria,
            "precio": precio, "marca": MARCA,
        })
    except Exception as exc:  # noqa: BLE001
        log.warning("amazon_ia.generar_para_alta(%s) falló: %s", sku, exc)
        return None
