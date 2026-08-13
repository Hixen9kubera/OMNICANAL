"""
tiktok_ia.py — El generador de contenido de TikTok: mismo contrato que Amazon.

QUÉ HACE, EN EL MISMO ORDEN QUE `amazon_ia`
-------------------------------------------
  1. resuelve la CATEGORÍA real del SKU (`channel.listings.category_id`),
  2. le pide a la IA el contenido con el prompt del hilo de TikTok, literal,
  3. lo pasa por `tiktok_contenido.validar` y una ronda de reparación,
  4. pide los ATRIBUTOS a la API de esa categoría y los valida contra su lista
     cerrada (`tiktok_atributos`), y
  5. guarda todo en `enrich.channel_content` con `origen: ia`.

LAS TRES DIFERENCIAS CON AMAZON QUE NO SON DE ESTILO
----------------------------------------------------
· **Amazon trunca; TikTok acepta el borrador y rebota al vender.** `AS_DRAFT`
  casi no valida, `LISTING` valida todo: un lote entero puede verse perfecto en
  borrador y caerse completo al activarse. Por eso se valida contra lo que
  exige LISTING, no contra lo que tolera el borrador.
· **El título de MX admite 300 caracteres**, no 255 ni los 45 que pedía el
  prompt viejo del panel. Son el campo que más pesa para que a uno lo
  encuentren.
· **TikTok exige ID de atributo Y ID de valor.** El nombre no sirve. Por eso
  los atributos se piden a la API de la categoría y se validan contra su lista;
  lo que no cuadra no se guarda.

Y la comprobación que no es de formato sino de sentido: **el título propuesto
tiene que conservar alguna palabra del original.** Un título puede quedar
impecable y describir otro producto — pasó el 12-ago con un collar veterinario
que acabó clasificado como joyería de disfraces, con toda confianza y sin error.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.tiktok_ia")

CANAL = "tiktok"
CUENTA = "KUBERA"
SPEC_VERSION = "tiktok-mx-2026-08"

# Llaves canónicas del panel. `descripcion_html` y `puntos_clave` son los
# nombres que usa el prompt de TikTok; aquí se traducen a `descripcion` y
# `bullets`, que es como se llaman en todos los canales. La traducción vive en
# el borde, no en la tabla.
_LLAVES = ("titulo", "descripcion", "bullets", "atributos")


# ─────────────────────────────────────────────────────────────────────────────
# El prompt — entregado por el hilo de TikTok el 13-ago-2026, literal
# ─────────────────────────────────────────────────────────────────────────────
_SISTEMA = (
    "Eres redactor de fichas de producto para TikTok Shop México.\n\n"
    "Mejoras el título y la descripción de un producto del catálogo. No "
    "inventas: sólo reescribes con lo que te doy.\n\n"
    "REGLAS\n"
    "1. NO INVENTES DATOS. Si no sabes el material, los watts, la capacidad o "
    "las medidas, no los menciones. Un dato inventado se publica sin dar error "
    "y nadie se entera hasta que un cliente reclama.\n"
    "2. El TÍTULO manda: es lo que busca la gente. Empieza por QUÉ ES el "
    "producto, después su rasgo distintivo. Hasta 300 caracteres — úsalos, pero "
    "sin rellenar con palabras vacías. Sin emojis, sin MAYÚSCULAS sostenidas, "
    "sin signos de admiración.\n"
    "3. Escribe como busca un comprador mexicano, no como habla un catálogo "
    "chino. «Hervidor eléctrico» y no «Kettle 1.7L Multifuncional Home "
    "Appliance».\n"
    "4. NADA de promesas que no controlamos: envío gratis, entrega en X días, "
    "garantía de por vida, «el mejor», «#1», precios.\n"
    "5. La DESCRIPCIÓN en HTML simple: <p>, <ul>, <li>, <strong>. Nada de "
    "<img>, <script>, <table> ni estilos.\n"
    "6. 3 a 6 puntos clave, cada uno un beneficio concreto, no un adjetivo.\n\n"
    "SALIDA — sólo JSON:\n"
    '{"titulo": "<máx 300 caracteres>", '
    '"descripcion_html": "<HTML simple>", '
    '"puntos_clave": ["<punto 1>", "…"], '
    '"palabras_clave": ["<lo que teclearía un comprador>"], '
    '"confianza": 0.0, '
    '"flags": ["<lo que NO pudiste confirmar del producto>"]}'
)


def _contexto(p: dict[str, Any], categoria: str | None) -> str:
    attrs = "; ".join(f"{a.get('nombre')}: {a.get('valor')}"
                      for a in (p.get("atributos") or []) if a.get("nombre"))
    return (
        f"PRODUCTO\n"
        f"  SKU:          {p.get('sku') or '(sin sku)'}\n"
        f"  Título hoy:   {p.get('nombre') or ''}\n"
        f"  Descripción:  {_sin_html(str(p.get('descripcion') or ''))[:1200]}\n"
        f"  Categoría:    {categoria or '(sin categoría de TikTok)'}\n"
        f"  Atributos confirmados: {attrs or '(ninguno)'}\n"
    )


def _sin_html(t: str) -> str:
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t or "")).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Categoría del canal
# ─────────────────────────────────────────────────────────────────────────────
def _categoria(sku: str) -> tuple[str | None, str | None]:
    """(category_id, ruta legible) del SKU en TikTok.

    ⚠️ En TikTok la categoría vive en `listings.category_id`; en Amazon vive en
    `product_type`. Cruzar los requisitos por la columna equivocada devuelve
    cero filas SIN dar error.
    """
    from services import supabase_db as sdb, tiktok_panel
    cid = tiktok_panel.categoria_de(sku)
    if not cid:
        return None, None
    try:
        filas = sdb.fetch_all(
            "select name, path from channel.categories where channel_id=%s and category_id=%s",
            (CANAL, cid))
        f = (filas or [{}])[0]
        return cid, (f.get("path") or f.get("name"))
    except Exception:  # noqa: BLE001
        return cid, None


async def _atributos_de_categoria(categoria_id: str) -> list[dict[str, Any]]:
    """
    Los atributos que TikTok declara para esa categoría, con sus IDs de valor.

    Se piden EN VIVO porque los IDs de valor no están en
    `channel.field_requirements` — ahí quedaron los nombres legibles, que sirven
    para que un humano revise pero no para publicar. Sin token o sin cipher se
    devuelve vacío y el generador lo dice: mejor sin atributos que con atributos
    inventados.
    """
    from services import tiktok as tk
    token, cipher = tk.access_token(), tk.cipher()
    if not (token and cipher):
        log.info("tiktok_ia: sin token o sin shop_cipher — no se piden atributos")
        return []
    try:
        data = await tk.llamar(
            f"/product/202309/categories/{categoria_id}/attributes",
            token, {"shop_cipher": cipher, "locale": "es-MX"})
        return data.get("attributes") or []
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_ia: atributos de la categoría %s: %s", categoria_id, exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# El generador
# ─────────────────────────────────────────────────────────────────────────────
async def mejorar(producto: dict[str, Any], *, guardar: bool = True) -> dict[str, Any]:
    """
    Genera el contenido de TikTok, lo valida y (si hay SKU) lo persiste.

    Misma forma de respuesta que `amazon_ia.mejorar`, para que el panel no tenga
    que distinguir de qué canal viene el parte.
    """
    from services import channel_content, ia_generadores, terminos_protegidos, tiktok_contenido

    sku = str(producto.get("sku") or "").strip()
    cat_id, cat_ruta = await asyncio.to_thread(_categoria, sku) if sku else (None, None)

    user = (f"{_contexto(producto, cat_ruta)}\n"
            "Mejora el contenido y devuelve SOLO el JSON indicado.")
    res = await asyncio.to_thread(ia_generadores._completar, _SISTEMA, user, 2000)  # noqa: SLF001
    if not res.get("ok"):
        return _vacio(res.get("motivo") or "La IA no respondió.", cat_id)
    data = ia_generadores._parse_json(res.get("texto", ""))  # noqa: SLF001
    if not data:
        return _vacio("La IA no devolvió JSON válido.", cat_id,
                      crudo=res.get("texto", "")[:400])

    # El ORIGINAL viaja al validador: es lo que permite la comprobación de
    # sentido (que el título siga hablando del mismo producto).
    original = {"titulo": producto.get("nombre") or ""}
    data, problemas = tiktok_contenido.validar(data, original)
    if problemas:
        log.info("tiktok_ia(%s): %d problema(s), reparando", sku or "?", len(problemas))
        reparado = await _reparar(user, data, problemas)
        if reparado:
            data, problemas = tiktok_contenido.validar(reparado, original)

    # Marcas registradas: determinista, con lista, después de la IA.
    campos_txt = {"titulo": data.get("titulo") or "",
                  "descripcion": data.get("descripcion_html") or "",
                  "bullets": data.get("puntos_clave") or []}
    campos_txt, terminos = terminos_protegidos.revisar_campos(campos_txt)

    # ── Atributos: de la categoría REAL, validados contra su lista cerrada ──
    atributos, attr_rechazos, attr_nota = [], [], None
    if cat_id:
        crudos = await _atributos_de_categoria(cat_id)
        if crudos:
            atributos, attr_rechazos = await _atributos(producto, cat_ruta, crudos)
        else:
            attr_nota = ("No se pudieron leer los atributos de la categoría "
                         "(sin token/cipher de TikTok o la API no contestó).")
    else:
        attr_nota = "El SKU no tiene categoría de TikTok: sin ella no hay atributos que pedir."

    # ── Lo que se aplica ─────────────────────────────────────────────────────
    # Aquí NO hay dos niveles como en Amazon: `tiktok_contenido` ya distingue lo
    # que limpia solo (HTML) de lo que reporta, y su lista de problemas es corta
    # y toda seria — un título que describe otro producto no es un matiz.
    campos: dict[str, Any] = {}
    malos = {p.split(":", 1)[0].strip() for p in problemas}
    if campos_txt.get("titulo") and "titulo" not in malos:
        campos["titulo"] = campos_txt["titulo"]
    if campos_txt.get("descripcion") and "descripcion" not in malos:
        campos["descripcion"] = campos_txt["descripcion"]
    if campos_txt.get("bullets") and not any(m.startswith("punto") for m in malos):
        campos["bullets"] = campos_txt["bullets"]
    if atributos:
        campos["atributos"] = atributos

    rechazados = [{"campo": c, "motivo": "; ".join(p for p in problemas
                                                   if p.startswith(c))}
                  for c in ("titulo", "descripcion") if c in malos]

    salida: dict[str, Any] = {
        "ok": True, "canal": CANAL,
        "proveedor": res.get("proveedor"), "modelo": res.get("modelo"),
        "campos": campos,
        "rechazados": rechazados,
        "avisos": ([f"atributos: {r}" for r in attr_rechazos] +
                   ([attr_nota] if attr_nota else [])),
        "problemas": problemas,
        "terminos_detectados": terminos,
        "palabras_clave": data.get("palabras_clave") or [],
        "confianza": data.get("confianza"),
        "product_type": cat_id, "product_type_origen": "listings" if cat_id else "auto",
        "requisitos": await _cobertura(cat_id, campos),
        "guardado": None,
    }

    if guardar and sku and campos:
        salida["guardado"] = await channel_content.guardar(
            sku, CANAL, campos, cuenta="",
            origen={k: "ia" for k in campos},
            categoria=cat_id, spec_version=SPEC_VERSION,
            hash_base=_hash_base(producto),
        )
    return salida


async def _atributos(producto: dict[str, Any], categoria: str | None,
                     crudos: list[dict[str, Any]]) -> tuple[list[dict], list[str]]:
    """
    Pide los atributos con el prompt canónico y los valida contra la categoría.

    Se guarda el `campo` NATIVO (`product_attributes.<id>`) junto al nombre
    legible: el nativo es lo que el semáforo compara contra
    `channel.field_requirements`, y el legible es lo único que un humano puede
    revisar. Guardar solo uno obliga a elegir entre que el semáforo funcione o
    que el panel se entienda.
    """
    from services import ia_generadores, tiktok_atributos

    prompt = tiktok_atributos.build_prompt(
        sku=str(producto.get("sku") or ""),
        titulo=str(producto.get("nombre") or ""),
        descripcion=_sin_html(str(producto.get("descripcion") or "")),
        categoria_nombre=categoria or "",
        atributos_woo={a.get("nombre"): a.get("valor")
                       for a in (producto.get("atributos") or []) if a.get("nombre")},
        attrs_tiktok=crudos,
    )
    res = await asyncio.to_thread(
        ia_generadores._completar,  # noqa: SLF001
        "Devuelve SOLO JSON válido, sin texto alrededor.", prompt, 1800)
    if not res.get("ok"):
        return [], [f"la IA no contestó: {res.get('motivo')}"]
    propuesta = ia_generadores._parse_json(res.get("texto", ""))  # noqa: SLF001
    validos, rechazos = tiktok_atributos.validar(propuesta or {}, crudos)

    nombres = {str(a.get("id")): a.get("name") for a in crudos}
    salida = []
    for v in validos:
        vals = v.get("values") or [{}]
        salida.append({
            "nombre": nombres.get(v["id"]) or v["id"],
            "campo": f"product_attributes.{v['id']}",
            "valor": " · ".join(x.get("name") or "" for x in vals),
            "valor_id": [x.get("id") for x in vals if x.get("id")],
        })
    # Lo que la IA marcó como no confirmado NO se guarda: un dato inventado no
    # da error, se publica, y después nadie sabe cuál era mentira.
    for f in (propuesta or {}).get("flags") or []:
        rechazos.append(f"sin confirmar → {f}")
    return salida, rechazos


async def _reparar(user: str, data: dict[str, Any],
                   problemas: list[str]) -> dict[str, Any] | None:
    from services import ia_generadores
    lista = "\n".join(f"  · {p}" for p in problemas)
    reintento = (f"{user}\n\nTu respuesta anterior fue:\n"
                 f"{json.dumps(data, ensure_ascii=False)}\n\n"
                 f"El validador la rechazó por esto:\n{lista}\n\n"
                 "Corrige ÚNICAMENTE esos campos, deja los demás idénticos y "
                 "devuelve otra vez el JSON completo.")
    try:
        r = await asyncio.to_thread(ia_generadores._completar, _SISTEMA, reintento, 2000)  # noqa: SLF001
        if r.get("ok"):
            return ia_generadores._parse_json(r.get("texto", "")) or None  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_ia: la reparación falló: %s", exc)
    return None


async def _cobertura(cat_id: str | None, campos: dict[str, Any]) -> dict[str, Any]:
    """Qué obligatorios de su categoría quedaron cubiertos, con los 1,779
    requisitos cargados el 13-ago."""
    from services import channel_content
    if not cat_id:
        return {"estado": "sin_requisitos", "product_type": None,
                "obligatorios": 0, "cubiertos": [], "sin_cubrir": []}
    reqs = await channel_content.requisitos(CANAL, cat_id, solo_obligatorios=True)
    if not reqs:
        return {"estado": "sin_requisitos", "product_type": cat_id,
                "obligatorios": 0, "cubiertos": [], "sin_cubrir": []}
    nativos = {(a.get("campo") or "").lower()
               for a in (campos.get("atributos") or [])}
    cubiertos, sin_cubrir = [], []
    for r in reqs:
        canonico = r.get("campo_canonico")
        if canonico and canonico in campos and campos[canonico]:
            cubiertos.append(r["campo"])
        elif r.get("default_value") is not None:
            cubiertos.append(r["campo"])
        elif r["campo"].lower() in nativos:
            cubiertos.append(r["campo"])
        else:
            sin_cubrir.append(r["campo"])
    return {"estado": "ok" if not sin_cubrir else "incompleto",
            "product_type": cat_id, "obligatorios": len(reqs),
            "cubiertos": cubiertos, "sin_cubrir": sin_cubrir}


def _hash_base(producto: dict[str, Any]) -> str:
    from services.amazon_ia import hash_base
    return hash_base(producto)


def _vacio(motivo: str, cat_id: str | None, **extra) -> dict[str, Any]:
    return {"ok": False, "canal": CANAL, "motivo": motivo, "campos": {},
            "rechazados": [], "avisos": [], "problemas": [],
            "terminos_detectados": [], "palabras_clave": [], "confianza": None,
            "product_type": cat_id, "product_type_origen": "listings" if cat_id else "auto",
            "guardado": None, **extra}


# ─────────────────────────────────────────────────────────────────────────────
# Enganche del alta de productos (pestaña Crear)
# ─────────────────────────────────────────────────────────────────────────────
async def generar_para_alta(sku: str, titulo: str, descripcion: str,
                            atributos: list[dict[str, Any]] | None = None,
                            precio: float | None = None) -> dict[str, Any] | None:
    """
    Lo que corre al CREAR un producto, en paralelo conceptual con Amazon.

    ⚠️ Un producto recién creado **no está publicado en TikTok todavía**, así que
    no tiene categoría en `channel.listings` y sus atributos no se pueden pedir:
    el contenido se genera igual (título y descripción son del producto, no del
    canal) y los atributos entran cuando el SKU tenga categoría. Se dice en el
    resultado en vez de dejarlo en silencio.

    Nace apagado (`TIKTOK_IA_EN_CREAR`) y nunca lanza: crear un producto no
    puede fallar porque la IA esté caída.
    """
    if not settings.tiktok_ia_en_crear:
        return None
    try:
        return await mejorar({
            "sku": sku, "nombre": titulo, "descripcion": descripcion,
            "atributos": atributos or [], "precio": precio,
        })
    except Exception as exc:  # noqa: BLE001
        log.warning("tiktok_ia.generar_para_alta(%s) falló: %s", sku, exc)
        return None
