# -*- coding: utf-8 -*-
"""
walmart_ia.py — el adaptador de contenido con IA para Walmart MX.

Hermano de `amazon_ia`, `tiktok_ia` y `temu_ia`, con el mismo contrato: el
prompt PIDE y el código GARANTIZA. Lo que la IA propone se comprueba contra el
catálogo real de la categoría antes de acercarse a un feed.

`walmart_contenido.py` (los prompts y los validadores) llevaba escrito desde el
17-ago y NADIE lo importaba. Esto es lo que faltaba para conectarlo.

QUÉ TIENE DE DISTINTO ESTE CANAL
────────────────────────────────
1. **La lista de atributos no se pide por API: no existe.** `POST /v3/items/spec`
   da 404 con credenciales MX y en Global está marcada "US only". En TikTok y
   Temu se le pregunta al canal qué pide; aquí la fuente es
   `channel.field_requirements`, cargada del esquema público y CORREGIDA con lo
   que producción demostró (`activity` es obligatorio en Juguetes aunque el
   esquema no lo diga; `countPerPack` está prohibido aunque el esquema lo liste).

2. **Un dato inventado NO da error.** En Temu un atributo malo manda el producto
   a Borrador y se ve. En Walmart se publica, y nadie se entera hasta que un
   cliente reclama. Por eso el prompt marca lo que no sabe con `[FALTA DATO]` y
   el validador lo SACA en vez de mandarlo.

3. **Hay frases que INACTIVAN el producto.** "Garantizado", "efecto inmediato",
   promesas médicas, superlativos no comprobables. No están en ninguna página
   pública: las trae el equipo de contenido de Walmart. `validar_contenido` las
   busca sin acentos y sin distinguir mayúsculas, porque así es como se cuelan.

4. **Las viñetas topan en 50 caracteres**, no en 500 como en los otros canales.
   Es el límite más apretado de todo el panel y el que más reintentos provoca.

5. **La categoría es la del ESQUEMA, no la del panel de Walmart.** Son dos
   taxonomías distintas con cero valores en común, y pegarle mal no da error:
   Walmart cae en un spec genérico y pide atributos absurdos. Se resuelve con
   los MISMOS patrones del publicador — si el contenido se generara para una
   categoría y el feed saliera con otra, los atributos irían al bloque
   equivocado.

LO QUE ESTO **NO** HACE: no publica ni toca Walmart. Escribe en
`enrich.channel_content` y ahí se queda hasta que alguien le dé al botón.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

log = logging.getLogger("omnicanal.walmart_ia")

CANAL = "walmart"
CUENTA = ""
# La versión que de verdad se manda en el feed, no la del archivo (3.19).
SPEC_VERSION = "walmart-mx-item-intl-3.11"

_SISTEMA = "Devuelve SOLO JSON válido, sin texto alrededor."

# Obligatorios que NO le tocan a la IA: los arma `_item()` del publicador con
# datos de Woo (dimensiones, peso, SKU, talla del atributo). Pedirlos aquí solo
# invitaría a inventarlos.
_DEL_PUBLICADOR = {
    "assembledProductLength", "assembledProductWidth", "assembledProductHeight",
    "assembledProductWeight", "countPerPack", "modelNumber", "size",
}


def _categoria(sku: str, nombre: str, cats_woo: str
               ) -> tuple[str | None, str, str | None]:
    """
    (categoría del esquema, de dónde salió, motivo del no).

    LA ELECCIÓN DEL PANEL MANDA (regla 2 de la casa): primero
    `channel.product_category`, y solo si no hay nada se clasifica.
    """
    from services import supabase_db as sdb
    try:
        filas = sdb.fetch_all(
            """select category_id from channel.product_category
                where channel_id = %s and sku = %s::citext""", (CANAL, sku))
        elegida = (filas or [{}])[0].get("category_id")
        if elegida:
            return str(elegida), "panel", None
    except Exception as exc:  # noqa: BLE001 — sin elección guardada, se clasifica
        log.debug("walmart_ia: sin categoría elegida para %s: %s", sku, exc)

    from services.publicar_walmart import clasificar
    clave, cfg, motivo = clasificar(sku, nombre, cats_woo)
    if cfg:
        return cfg["clave_visible"], "clasificado", None
    return None, "", motivo


def _hash_base(producto: dict[str, Any]) -> str:
    """Huella de lo que entró, para saber si Woo cambió desde la última vez."""
    crudo = json.dumps({k: producto.get(k) for k in ("nombre", "descripcion", "sku")},
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()[:32]


def _vacio(motivo: str, cat: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "canal": CANAL, "motivo": motivo,
            "categoria_id": cat, "campos": {}, **extra}


async def _preguntar(prompt: str, tope: int = 1800) -> dict[str, Any] | None:
    from services import ia_generadores
    res = await asyncio.to_thread(
        ia_generadores._completar, _SISTEMA, prompt, tope)  # noqa: SLF001
    if not res.get("ok"):
        return None
    return ia_generadores._parse_json(res.get("texto", ""))  # noqa: SLF001


async def mejorar(producto: dict[str, Any], *, guardar: bool = True) -> dict[str, Any]:
    """Contenido de Walmart para un SKU: título, descripción, viñetas y atributos."""
    from services import walmart_contenido as wc

    sku = str(producto.get("sku") or "").strip()
    if not sku:
        return _vacio("Sin SKU.")

    nombre = str(producto.get("nombre") or "")
    cats_woo = str(producto.get("categoria") or producto.get("categorias") or "")
    categoria, cat_origen, motivo = await asyncio.to_thread(
        _categoria, sku, nombre, cats_woo)
    if not categoria:
        return _vacio(motivo or "Este SKU no cae en ninguna categoría de Walmart "
                                "con exención de UPC.")

    avisos: list[str] = []
    try:
        cat = await asyncio.to_thread(wc.catalogo, categoria)
    except Exception as exc:  # noqa: BLE001
        return _vacio(f"No se pudo leer el catálogo de «{categoria}»: {exc}",
                      categoria)
    if cat["fuente"] != "channel.field_requirements":
        avisos.append(f"Catálogo leído de {cat['fuente']} — en producción debería "
                      f"venir de channel.field_requirements.")

    atributos_woo = producto.get("atributos") or {}
    if isinstance(atributos_woo, list):
        # El Estudio manda [{nombre, valor}]; los prompts esperan un diccionario.
        atributos_woo = {a.get("nombre") or a.get("name"): a.get("valor") or a.get("value")
                         for a in atributos_woo if isinstance(a, dict)}

    # ── 1. CONTENIDO ────────────────────────────────────────────────────────
    prompt = wc.build_prompt_contenido(
        sku=sku, categoria=categoria,
        titulo_woo=nombre,
        descripcion_woo=str(producto.get("descripcion") or ""),
        marca=str(producto.get("marca") or ""),
        atributos_conocidos=atributos_woo,
    )
    data = await _preguntar(prompt, 2000)
    if not data:
        return _vacio("La IA no devolvió JSON válido para el contenido.", categoria)

    contenido, problemas = wc.validar_contenido(data, titulo_original=nombre)
    llamadas = 1
    if problemas:
        # UNA ronda de reparación: se le devuelven sus propios problemas. Con las
        # viñetas de 50 caracteres esta ronda se usa casi siempre.
        reintento = (f"{prompt}\n\nTu respuesta anterior fue:\n"
                     f"{json.dumps(data, ensure_ascii=False)[:1200]}\n\n"
                     "Tuvo estos problemas:\n"
                     + "\n".join(f"  · {p}" for p in problemas)
                     + "\n\nCorrígelos y devuelve el JSON completo otra vez.")
        segunda = await _preguntar(reintento, 2000)
        llamadas += 1
        if segunda:
            contenido, problemas = wc.validar_contenido(segunda, titulo_original=nombre)
    avisos.extend(problemas)

    campos: dict[str, Any] = {
        "titulo": contenido.get("titulo"),
        "descripcion": contenido.get("descripcion"),
        "bullets": contenido.get("beneficios"),
        "caracteristicas": contenido.get("caracteristicas"),
        "marca": contenido.get("marca"),
        "palabras_clave": contenido.get("palabras_clave"),
    }

    # ── 2. ATRIBUTOS DE LA CATEGORÍA ────────────────────────────────────────
    p2 = wc.build_prompt_atributos(
        sku=sku, categoria=categoria,
        titulo=campos.get("titulo") or nombre,
        descripcion=campos.get("descripcion") or "",
        atributos_woo=atributos_woo,
    )
    prop = await _preguntar(p2, 1800) or {}
    llamadas += 1
    validos, rechazos, pendientes = await asyncio.to_thread(
        wc.validar_atributos, prop, categoria)
    if rechazos:
        avisos.append(f"{len(rechazos)} atributo(s) descartados: "
                      + "; ".join(rechazos[:5]))
    # ⚠️ NO TODO OBLIGATORIO QUE FALTE ES UN PROBLEMA DE LA IA. Las medidas, el
    # peso, el modelo y la talla los pone el publicador desde Woo, no el
    # generador de contenido — y sin esta separación el panel gritaría
    # "Walmart RECHAZA el artículo" en TODOS los productos, por campos que
    # siempre viajan. Un aviso que sale siempre es un aviso que se ignora, y el
    # día que falte uno de verdad nadie lo va a leer.
    de_ia = [c for c in pendientes if c not in _DEL_PUBLICADOR]
    de_woo = [c for c in pendientes if c in _DEL_PUBLICADOR]
    if de_ia:
        avisos.append("OBLIGATORIOS SIN LLENAR: " + ", ".join(de_ia)
                      + ". Walmart RECHAZA el artículo si falta alguno.")
    if de_woo:
        avisos.append("Los pone el publicador desde Woo: " + ", ".join(de_woo)
                      + ". Si Woo no los tiene, salen con un valor por omisión "
                        "(10 cm / 0.3 kg) — y Walmart cobra flete volumétrico.")
    if validos:
        campos["atributos"] = validos

    # El contrato del reporte es el de Amazon/TikTok/Temu A PROPÓSITO: el panel
    # ya tiene un bloque que pinta `product_type` + `requisitos` + `rechazados`
    # + `avisos`. Inventar aquí una forma nueva habría obligado a escribir una
    # segunda vista para decir lo mismo.
    esperados = [c for c in cat["obligatorios"] if c not in cat["rechazados"]]
    cubiertos = [c for c in esperados if c in validos or c in _DEL_PUBLICADOR]
    salida: dict[str, Any] = {
        "ok": True, "canal": CANAL, "sku": sku,
        "categoria_id": categoria, "categoria_ruta": categoria,
        "product_type": categoria, "product_type_origen": cat_origen,
        "requisitos": {
            "estado": "ok" if not de_ia else "incompleto",
            "product_type": categoria, "obligatorios": len(esperados),
            "cubiertos": cubiertos, "sin_cubrir": de_ia,
        },
        "campos": {k: v for k, v in campos.items() if v},
        "atributos": validos,
        "obligatorios_faltantes": de_ia,
        # [{campo, motivo}] — los `rechazos` vienen como "campo: motivo".
        "rechazados": [
            {"campo": r.split(":", 1)[0].strip(),
             "motivo": (r.split(":", 1)[1].strip() if ":" in r else r)}
            for r in rechazos],
        "confianza": contenido.get("confianza"),
        "flags": contenido.get("flags") or [],
        "llamadas_ia": llamadas,
        "avisos": avisos,
    }

    if guardar and salida["campos"]:
        from services import channel_content as cc
        salida["guardado"] = await cc.guardar(
            sku, CANAL, salida["campos"], cuenta=CUENTA,
            origen={k: "ia" for k in salida["campos"]},
            categoria=categoria, spec_version=SPEC_VERSION,
            hash_base=_hash_base(producto))
    return salida
