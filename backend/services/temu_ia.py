"""
temu_ia.py — El generador de contenido de Temu, conectado al panel.

`temu_contenido.py` trae los prompts y los validadores; esto es el ADAPTADOR que
los une con el panel: resuelve la categoría, pide la plantilla real, llama a la
IA, valida y guarda en `enrich.channel_content`. Mismo papel que `tiktok_ia.py`
para TikTok y `amazon_ia.py` para Amazon.

POR QUÉ SON TRES LLAMADAS (y a veces cuatro)
────────────────────────────────────────────
No es una decisión de diseño: es el orden que impone Temu.

  1. **La categoría va primero** porque la categoría DETERMINA qué atributos
     existen. `template.get` solo responde en hojas: hasta no saber la hoja, no
     hay lista de atributos que preguntar. (Aquí la categoría no se pide a la
     IA: los 160 productos de Temu ya están publicados y traen la suya. El
     prompt de categoría hace falta para publicar SKUs nuevos — pieza 5.)

  2. **El contenido** (título, descripción, bullets), con la categoría ya sabida.

  3. **Los atributos, en dos vueltas**, porque la cascada es CIRCULAR: qué
     condicionales se activan depende de lo que se contestó en los duros. No se
     puede preguntar "¿qué voltaje?" antes de saber si el producto se enchufa.
     Meter los 20 atributos de golpe hace que el modelo llene voltajes de
     productos sin electricidad — que es justo lo que manda productos a
     Borrador.

     La segunda vuelta SOLO ocurre si algo se destrabó. Medido el 13-ago: de 89
     productos, solo 13 la necesitaron.

LO QUE NO SE NEGOCIA
────────────────────
La IA propone eligiendo de listas CERRADAS y el código valida contra la
plantilla real de la hoja. En la prueba de 89 productos el modelo **inventó 10
`vid`** pese a que el prompt lo prohíbe explícitamente; el validador los
rechazó. Sin ese cotejo se habrían publicado como datos verdaderos, sin error —
que es exactamente el caso `TEC-1812-NEG`.

EL CHEQUEO QUE EVITA EL BORRADOR
────────────────────────────────
`faltantes()` dice qué obligatorios (duros + condicionales ya activados)
quedaron sin llenar. Si eso no viene vacío, publicar deja que Temu autocomplete
y **mande el producto a Borrador** en vez de publicarlo. Por eso se devuelve al
panel como aviso y no se esconde.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

log = logging.getLogger("omnicanal.temu_ia")

CANAL = "temu"
SPEC_VERSION = "temu-v3-2026-08"

_SISTEMA = "Devuelve SOLO JSON válido, sin texto alrededor."


def _categoria(sku: str) -> tuple[str | None, str | None]:
    """(catId, ruta legible) del SKU en Temu.

    ⚠️ En Temu la categoría vive en `listings.category_id` — igual que TikTok y
    NO como Amazon, donde vive en `product_type`. Cruzar por la columna
    equivocada devuelve cero filas sin dar error.
    """
    from services import supabase_db as sdb, temu_panel
    try:
        # LA ELECCIÓN DEL PANEL MANDA (regla 2 de la casa): `categoria_de` mira
        # primero `channel.product_category` y solo después la publicación. Sin
        # esto, un producto NUEVO —que no tiene publicación— no tendría hoja y
        # no se podría ni generar contenido ni publicar.
        cid = temu_panel.categoria_de(sku)
        if not cid:
            return None, None
        filas = sdb.fetch_all(
            """select name, path from channel.categories
                where channel_id=%s and category_id=%s""", (CANAL, cid))
        f = (filas or [{}])[0]
        return str(cid), (f.get("path") or f.get("name"))
    except Exception as exc:  # noqa: BLE001
        log.warning("temu_ia._categoria(%s): %s", sku, exc)
        return None, None


async def _props(cat_id: str) -> list[dict[str, Any]]:
    """
    Los atributos de la hoja, EN VIVO y con sus `vid`.

    No se leen de `channel.field_requirements` a propósito: ahí quedan para el
    semáforo, pero publicar necesita los ids exactos y estos cambian del lado de
    Temu sin avisarnos. Sin plantilla se devuelve vacío y el generador lo dice:
    mejor sin atributos que con atributos inventados.
    """
    from services import temu
    try:
        tpl = await temu.plantilla_categoria(cat_id)
        return (tpl.get("templateInfo") or {}).get("goodsProperties") or []
    except Exception as exc:  # noqa: BLE001
        log.warning("temu_ia: plantilla de %s: %s", cat_id, exc)
        return []


def _hash_base(producto: dict[str, Any]) -> str:
    """Huella de lo que entró, para saber si Woo cambió desde la última vez."""
    crudo = json.dumps({k: producto.get(k) for k in ("nombre", "descripcion", "sku")},
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()[:32]


def _vacio(motivo: str, cat_id: str | None, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "canal": CANAL, "motivo": motivo,
            "categoria_id": cat_id, "campos": {}, **extra}


async def _preguntar(prompt: str, tope: int = 1800) -> dict[str, Any] | None:
    from services import ia_generadores
    res = await asyncio.to_thread(
        ia_generadores._completar, _SISTEMA, prompt, tope)  # noqa: SLF001
    if not res.get("ok"):
        return None
    return ia_generadores._parse_json(res.get("texto", ""))  # noqa: SLF001


async def mejorar(producto: dict[str, Any], *, guardar: bool = True) -> dict[str, Any]:
    """
    Contenido de Temu para un SKU: título, descripción, bullets y atributos.

    Devuelve siempre algo legible para el panel; los problemas viajan en
    `avisos` en vez de reventar.
    """
    from services import channel_content, temu_contenido

    sku = str(producto.get("sku") or "").strip()
    if not sku:
        return _vacio("Sin SKU.", None)

    cat_id, cat_ruta = await asyncio.to_thread(_categoria, sku)
    if not cat_id:
        return _vacio("Este SKU no tiene categoría en Temu. Publícalo una vez "
                      "o elige la categoría antes de generar contenido.", None)

    avisos: list[str] = []
    props = await _props(cat_id)
    if not props:
        avisos.append("No se pudo leer la plantilla de la categoría: se genera "
                      "el texto pero SIN atributos.")

    # ── 1. CONTENIDO ────────────────────────────────────────────────────────
    prompt = temu_contenido.build_prompt_contenido(
        sku=sku,
        titulo_woo=str(producto.get("nombre") or ""),
        descripcion_woo=str(producto.get("descripcion") or ""),
        categoria_ruta=cat_ruta or str(cat_id),
        atributos_woo=producto.get("atributos") or {},
    )
    data = await _preguntar(prompt, 2000)
    if not data:
        return _vacio("La IA no devolvió JSON válido para el contenido.", cat_id)

    contenido, problemas = temu_contenido.validar_contenido(data)
    if problemas:
        # UNA ronda de reparación, igual que Amazon: se le devuelven sus propios
        # problemas. Si insiste, se reporta en vez de publicar algo inválido.
        reintento = (f"{prompt}\n\nTu respuesta anterior fue:\n"
                     f"{json.dumps(data, ensure_ascii=False)[:1200]}\n\n"
                     "Tuvo estos problemas:\n"
                     + "\n".join(f"  · {p}" for p in problemas)
                     + "\n\nCorrígelos y devuelve el JSON completo otra vez.")
        segunda = await _preguntar(reintento, 2000)
        if segunda:
            contenido, problemas = temu_contenido.validar_contenido(segunda)
    avisos.extend(problemas)

    campos: dict[str, Any] = {
        "titulo": contenido.get("titulo"),
        "descripcion": contenido.get("descripcion"),
        "bullets": contenido.get("bullets"),
    }

    # ── 2. ATRIBUTOS, primera vuelta: los DUROS ─────────────────────────────
    atributos: list[dict[str, Any]] = []
    elegidos: dict[int, list[int]] = {}
    vueltas = 1
    if props:
        p1 = temu_contenido.build_prompt_atributos(
            sku=sku, titulo=campos.get("titulo") or "",
            descripcion=campos.get("descripcion") or "",
            categoria_ruta=cat_ruta or str(cat_id), props=props,
            atributos_woo=producto.get("atributos") or {})
        prop1 = await _preguntar(p1) or {}
        atributos, elegidos, rechazos = temu_contenido.validar_atributos(prop1, props)
        if rechazos:
            avisos.append(f"{len(rechazos)} valor(es) propuestos no existen en la "
                          f"categoría y se descartaron.")

        # ── 3. SEGUNDA VUELTA: solo si la cascada destrabó algo ─────────────
        pendientes = temu_contenido.activados(props, elegidos)
        if pendientes:
            vueltas = 2
            p2 = temu_contenido.build_prompt_atributos(
                sku=sku, titulo=campos.get("titulo") or "",
                descripcion=campos.get("descripcion") or "",
                categoria_ruta=cat_ruta or str(cat_id), props=props,
                atributos_woo=producto.get("atributos") or {},
                elegidos=elegidos)
            prop2 = await _preguntar(p2) or {}
            extra, elegidos2, rech2 = temu_contenido.validar_atributos(prop2, props)
            atributos.extend(extra)
            for pid, vids in (elegidos2 or {}).items():
                elegidos.setdefault(pid, []).extend(vids)
            if rech2:
                avisos.append(f"{len(rech2)} valor(es) de la segunda vuelta se "
                              f"descartaron por no existir.")

        campos["atributos"] = atributos

        # El chequeo que evita el Borrador.
        sin_llenar = temu_contenido.faltantes(props, elegidos)
        if sin_llenar:
            avisos.append("OBLIGATORIOS SIN LLENAR: " + ", ".join(sin_llenar)
                          + ". Publicar así deja que Temu autocomplete y el "
                            "producto cae en Borrador.")

    salida: dict[str, Any] = {
        "ok": True, "canal": CANAL, "sku": sku,
        "categoria_id": cat_id, "categoria_ruta": cat_ruta,
        "campos": {k: v for k, v in campos.items() if v},
        "atributos": atributos,
        "llamadas_ia": 1 + (1 if problemas else 0) + (vueltas if props else 0),
        "avisos": avisos,
    }

    if guardar and salida["campos"]:
        from services import channel_content as cc
        salida["guardado"] = await cc.guardar(
            sku, CANAL, salida["campos"], cuenta="",
            origen={k: "ia" for k in salida["campos"]},
            categoria=cat_id, spec_version=SPEC_VERSION,
            hash_base=_hash_base(producto))
    return salida


async def generar_para_alta(sku: str, titulo: str, descripcion: str,
                            atributos: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Gancho del alta de productos (pestaña Crear).

    Detrás de su propio flag, como Amazon y TikTok: encenderlo hace que CADA alta
    gaste llamadas de IA y escriba en producción, y eso es cambio de flujo vivo.

    OJO: un producto recién creado todavía NO está publicado en Temu, así que no
    tiene categoría y sus atributos no se pueden pedir. Aquí solo saldrá el
    texto, y los atributos entran cuando el SKU ya tenga hoja en el canal.
    """
    from config import settings
    if not getattr(settings, "temu_ia_en_crear", False):
        return {"ok": False, "motivo": "TEMU_IA_EN_CREAR apagado", "canal": CANAL}
    return await mejorar({"sku": sku, "nombre": titulo, "descripcion": descripcion,
                          "atributos": atributos or {}}, guardar=True)
