"""
creacion.py — Candidatos para CREAR productos (100% WooCommerce).

Un "candidato a crear" es un producto de la tienda que TODAVÍA no está listo ni
publicado en WooCommerce (su estado NO es 'ready' ni 'publish'). Son los que
faltan por terminar / dar de alta a partir de su ficha de Alibaba.

NO usa Odoo ni la tabla cache `productos`: el índice de candidatos y todos los
datos vienen en vivo de WooCommerce (ver services/woocommerce.py).
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from services import db, odoo, woocommerce

log = logging.getLogger("omnicanal.creacion")


# ── Sincronización Odoo → WooCommerce (drafts) ─────────────────────────────────
# Primer paso del flujo de creación: todo SKU que exista en Odoo pero NO en
# WooCommerce se da de alta en Woo como `draft`. Woo es el backend de estados;
# a partir de ahí el producto avanza draft → inprogress → pending → publish.

async def plan_drafts() -> dict[str, Any]:
    """
    Calcula el diff Odoo↔WooCommerce SIN escribir nada:
    qué SKUs están en Odoo y faltan en Woo (candidatos a crear como draft).
    """
    from services import wp_db
    if wp_db.disponible():
        # Vía rápida: SKUs ocupados (productos + variaciones) directo de MySQL.
        catalogo, skus_woo = await asyncio.gather(
            asyncio.to_thread(odoo.listar_catalogo),
            asyncio.to_thread(wp_db.skus_existentes),
        )
    else:
        catalogo, skus_woo = await asyncio.gather(
            asyncio.to_thread(odoo.listar_catalogo),
            woocommerce.skus_existentes(),
        )
    if not catalogo:
        return {"ok": False, "motivo": "Odoo no devolvió catálogo (¿credenciales/conexión?)"}
    if skus_woo is None:
        return {"ok": False, "motivo": "WooCommerce no respondió el escaneo de SKUs"}

    # Dedupe por SKU (variantes de Odoo pueden repetir default_code).
    candidatos: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for p in catalogo:
        clave = p["sku"].lower()
        if clave in skus_woo or clave in vistos:
            continue
        vistos.add(clave)
        candidatos.append(p)

    if wp_db.disponible():
        # La consulta a MySQL ya incluye variaciones: no hay ocultos que verificar.
        ocultos: set[str] = set()
        faltantes = candidatos
    else:
        # El listado de /products solo muestra productos PADRE: los SKUs que viven
        # en variaciones no aparecen ahí. Verificamos los candidatos con el filtro
        # sku= (que sí encuentra variaciones) para no intentar crear duplicados.
        ocultos = await woocommerce.filtrar_skus_existentes([p["sku"] for p in candidatos])
        faltantes = [p for p in candidatos if p["sku"].lower() not in ocultos]

    return {
        "ok": True,
        "odoo_total": len(catalogo),
        "woo_total": len(skus_woo) + len(ocultos),
        "faltantes_total": len(faltantes),
        "faltantes": faltantes,
    }


async def adjuntar_imagenes_odoo(items: list[dict[str, Any]]) -> int:
    """
    Para cada item {sku,...}: baja la imagen del producto en Odoo (image_512),
    la sube a la librería de medios de WordPress y anota `imagen_media_id` en el
    item, para que el alta en Woo salga ya con imagen. Tolerante: los SKUs sin
    imagen en Odoo (o con fallo de subida) se crean sin imagen.
    Devuelve cuántas imágenes se adjuntaron.
    """
    import base64

    skus = [it["sku"] for it in items]
    imagenes = await asyncio.to_thread(odoo.imagenes_por_sku, skus)
    if not imagenes:
        return 0

    sem = asyncio.Semaphore(4)

    async def _subir(it: dict[str, Any]) -> bool:
        b64 = imagenes.get(it["sku"])
        if not b64:
            return False
        async with sem:
            try:
                subida = await woocommerce.subir_imagen_wp(it["sku"], base64.b64decode(b64))
            except Exception:  # noqa: BLE001
                return False
        if subida:
            it["imagen_media_id"] = subida[0]
            return True
        return False

    resultados = await asyncio.gather(*[_subir(it) for it in items])
    n = sum(resultados)
    log.info("adjuntar_imagenes_odoo: %d/%d con imagen", n, len(items))
    return n


async def sincronizar_drafts(limite: int = 100) -> dict[str, Any]:
    """
    Crea en WooCommerce (status=draft) hasta `limite` SKUs que están en Odoo y
    faltan en Woo. Progresivo: cada corrida toma los siguientes faltantes, así
    que se puede ejecutar varias veces hasta que el diff quede en cero.
    """
    plan = await plan_drafts()
    if not plan["ok"]:
        return plan

    lote = plan["faltantes"][:limite]
    if not lote:
        return {
            "ok": True, "creados": [], "errores": [],
            "faltantes_restantes": 0,
            "mensaje": "Nada que crear: todos los SKUs de Odoo ya existen en WooCommerce.",
        }

    await adjuntar_imagenes_odoo(lote)
    # Categoría de departamento según el prefijo del SKU (TEC→Tecnología…).
    from services import categorias
    async with woocommerce._client() as cli:
        for it in lote:
            nombre = categorias.categoria_para_sku(it["sku"])
            if nombre:
                it["categoria_wc_id"] = await categorias.asegurar_categoria(cli, nombre)
    resultado = await woocommerce.crear_borradores(lote)

    # SKUs rechazados por duplicado (huérfanos en la tabla lookup de Woo,
    # variaciones de padres borrados…): se marcan como existentes para que el
    # plan deje de proponerlos y no tapen la cola en corridas siguientes.
    _PISTAS_DUP = ("duplicado", "duplicate", "lookup table", "already present")
    bloqueados = [
        e for e in resultado["errores"]
        if any(p in (e.get("error") or "").lower() for p in _PISTAS_DUP)
    ]
    for e in bloqueados:
        woocommerce.marcar_sku_existente(e["sku"])
        e["error"] = (
            "SKU ya ocupado en WooCommerce aunque no aparece en el catálogo "
            "(producto borrado que dejó rastro en la tabla lookup, o variación). "
            "Se omitirá en las siguientes corridas."
        )

    log.info(
        "sincronizar_drafts: %d creados, %d errores (quedaban %d faltantes)",
        len(resultado["creados"]), len(resultado["errores"]), plan["faltantes_total"],
    )
    # Refresca el índice de candidatos en segundo plano para que los nuevos
    # drafts aparezcan de inmediato en la vista "Crear Productos".
    asyncio.create_task(woocommerce.indice_candidatos(refrescar=True))

    return {
        "ok": True,
        "creados": resultado["creados"],
        "errores": resultado["errores"],
        "faltantes_restantes": plan["faltantes_total"] - len(resultado["creados"]),
    }


# ── Agrupación por convención de SKU ───────────────────────────────────────────
# Convención del catálogo:  CAT-####[-DETALLE]
#   - "CAT"    → prefijo de categoría (TEC, ORG, ROP…)
#   - "####"   → id único del producto dentro de la categoría
#   - "DETALLE"→ característica (color, talla…), opcional; puede tener guiones
#                (CALZ-0134-CAF-25 → base CALZ-0134, detalle CAF-25)
#
# Si VARIOS SKUs comparten la misma base (TEC-0002-NEG, TEC-0002-AZL) son
# variantes de un PADRE conceptual "TEC-0002". Si la base tiene un solo SKU, es
# un producto ÚNICO (con o sin característica). La vista Crear Productos lista
# una fila por padre/único; las variantes se muestran dentro de la fila.

_RE_SKU = re.compile(r"^([A-Za-z]+-\d+)(?:-(.+))?$")


def _base_sku(sku: str) -> tuple[str, str | None]:
    """Separa un SKU en (base, detalle). Si no sigue la convención: (sku, None)."""
    m = _RE_SKU.match((sku or "").strip())
    if not m:
        return (sku or "").strip().upper(), None
    return m.group(1).upper(), m.group(2)


def _costos_por_sku(skus: list[str]) -> dict[str, float]:
    """
    { sku: costo_unitario } desde costos_finales (fallback costo_producto).
    Consulta en lotes para no pasarse del límite de placeholders.
    """
    salida: dict[str, float] = {}
    for i in range(0, len(skus), 800):
        chunk = skus[i:i + 800]
        ph = ",".join(["%s"] * len(chunk))
        try:
            rows = db.fetch_all(
                f"""SELECT sku, costo_unitario, costo_producto
                    FROM costos_finales WHERE sku IN ({ph})""",
                tuple(chunk),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("costos_por_sku falló: %s", exc)
            return salida
        for r in rows:
            costo = r.get("costo_unitario") or r.get("costo_producto")
            if costo:
                salida[r["sku"]] = float(costo)
    return salida


# Claves de ordenamiento de la vista Crear Productos (campo_direccion).
_CLAVES_ORDEN = {
    "valor": lambda g: g.get("valor") or 0,
    "costo": lambda g: g.get("costo") or 0,
    "stock": lambda g: g.get("stock") or 0,
    "tipo": lambda g: "padre" if len(g["miembros"]) > 1 else "unico",
}


def _armar_grupos(indice: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa filas del índice por base de SKU y calcula costo/stock/valor."""
    grupos: dict[str, dict[str, Any]] = {}
    skus_vistos: set[str] = set()
    for c in indice:
        sku = (c.get("sku") or "").strip().upper()
        if sku in skus_vistos:  # duplicados de imports viejos
            continue
        skus_vistos.add(sku)
        base, sufijo = _base_sku(sku)
        g = grupos.setdefault(base, {"base": base, "miembros": []})
        g["miembros"].append({**c, "sufijo": sufijo})

    lista = list(grupos.values())
    costos = _costos_por_sku([m["sku"] for g in lista for m in g["miembros"]])
    for g in lista:
        g["miembros"].sort(key=lambda m: m["sufijo"] is not None)
        stock_total, valor_total, costo_grupo = 0, 0.0, None
        for m in g["miembros"]:
            m["costo"] = costos.get(m["sku"])
            m["valor"] = round((m.get("stock") or 0) * (m["costo"] or 0), 2)
            stock_total += m.get("stock") or 0
            valor_total += m["valor"]
            if costo_grupo is None and m["costo"]:
                costo_grupo = m["costo"]
        g["stock"] = stock_total
        g["costo"] = costo_grupo
        g["valor"] = round(valor_total, 2)
    return lista


async def _pagina_directa(
    page: int,
    per_page: int,
    search: str | None,
    skus_filtro: list[str] | None,
    orden: str,
    categoria: str | None,
) -> tuple[list[dict[str, Any]], int]:
    """
    MODO DIRECTO (mientras el índice no está completo): página de 50 en 1
    request; búsquedas de SKU/categoría/comas resueltas por Woo al momento.
    El orden por columnas aplica DENTRO de la página; el orden global llega
    cuando el índice termina de cargar (o al instante con MySQL directo).
    """
    if skus_filtro:
        filas = await woocommerce.buscar_drafts([s.strip() for s in skus_filtro if s.strip()])
        total = len(filas)
    else:
        filas, total = await woocommerce.drafts_pagina(
            page, per_page, search=search, categoria=categoria,
        )
    grupos = await asyncio.to_thread(_armar_grupos, filas)

    campo, _, direccion = (orden or "valor_desc").partition("_")
    clave = _CLAVES_ORDEN.get(campo, _CLAVES_ORDEN["valor"])
    grupos.sort(key=clave, reverse=(direccion != "asc"))

    if skus_filtro:  # búsqueda por comas: paginamos localmente el resultado
        total = len(grupos)
        offset = (page - 1) * per_page
        return grupos[offset:offset + per_page], total
    return grupos, total


async def listar_candidatos_agrupados(
    page: int = 1,
    per_page: int = 40,
    search: str | None = None,
    skus_filtro: list[str] | None = None,
    orden: str = "valor_desc",
    categoria: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    Agrupa el índice de candidatos por base de SKU, calcula costo/stock/valor y
    devuelve la página ordenada según `orden` (campo_dirección: valor_desc,
    costo_asc, stock_desc, tipo_asc…; default valor de mayor a menor).

    - stock del grupo = suma del stock de sus miembros (nivel variante).
    - valor del grupo = suma de (stock × costo) de cada miembro.
    - costo mostrado del grupo = el del primer miembro con costo en costos_finales.
    - `skus_filtro`: si viene, solo grupos cuya base o algún miembro esté en la lista.
    """
    from services import wp_db

    # MODO DIRECTO: si el índice completo no está listo y no hay MySQL, la
    # página y las búsquedas se resuelven contra Woo al momento (1-2 requests)
    # mientras el índice sigue construyéndose en segundo plano.
    if not woocommerce.drafts_completo() and not wp_db.disponible():
        asyncio.create_task(woocommerce.indice_candidatos())  # sigue cargando detrás
        return await _pagina_directa(page, per_page, search, skus_filtro, orden, categoria)

    indice = await woocommerce.indice_candidatos()
    lista = await asyncio.to_thread(_armar_grupos, indice)

    if skus_filtro:
        # Cada término separado por coma actúa como FILTRO y BUSCADOR a la vez:
        # matchea por coincidencia parcial en la base, el SKU o el nombre.
        terminos = [s.strip().upper() for s in skus_filtro if s.strip()]

        def _match(g: dict[str, Any]) -> bool:
            for t in terminos:
                if t in g["base"]:
                    return True
                for m in g["miembros"]:
                    if (
                        t in m["sku"]
                        or t in (m.get("nombre") or "").upper()
                        or any(t in c.upper() for c in (m.get("categorias") or []))
                    ):
                        return True
            return False

        lista = [g for g in lista if _match(g)]

    if search:
        s = search.strip().lower()
        lista = [
            g for g in lista
            if s in g["base"].lower() or any(
                s in (m.get("sku") or "").lower()
                or s in (m.get("nombre") or "").lower()
                or any(s in c.lower() for c in (m.get("categorias") or []))
                for m in g["miembros"]
            )
        ]

    if categoria:
        c = categoria.strip().lower()
        lista = [
            g for g in lista
            if any(
                any(c in cat.lower() for cat in (m.get("categorias") or []))
                for m in g["miembros"]
            )
        ]

    campo, _, direccion = (orden or "valor_desc").partition("_")
    clave = _CLAVES_ORDEN.get(campo, _CLAVES_ORDEN["valor"])
    lista.sort(key=clave, reverse=(direccion != "asc"))

    total = len(lista)
    offset = (page - 1) * per_page
    return lista[offset:offset + per_page], total


async def items_candidatos(grupos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Convierte los grupos de la página en items para la UI, con datos en vivo de
    WooCommerce. Grupo de 1 → producto único (o padre real si en Woo es
    variable). Grupo de varios → fila del PADRE conceptual (sku = base) con sus
    variantes; el stock mostrado es la suma de las variantes.
    """
    wc_ids = [m["wc_id"] for g in grupos for m in g["miembros"]]
    vivos: dict[int, dict[str, Any]] = {}
    for i in range(0, len(wc_ids), 100):  # `include` de Woo admite máx. 100
        for it in await woocommerce.productos_por_wc_id(wc_ids[i:i + 100]):
            vivos[it["wc_id"]] = it

    items: list[dict[str, Any]] = []
    for g in grupos:
        miembros = g["miembros"]
        rep = next((vivos[m["wc_id"]] for m in miembros if m["wc_id"] in vivos), None)
        if rep is None:
            continue  # WooCommerce no devolvió ningún miembro del grupo

        item = dict(rep)
        item["costo"] = g.get("costo")
        item["valor"] = g.get("valor")
        item["stock"] = g.get("stock")
        if len(miembros) == 1:
            # En Woo los productos "variable" ya traen sus variantes reales.
            if item.get("tipo") != "variable":
                item["tipo"] = "unico"
            items.append(item)
            continue

        variantes = []
        for m in miembros:
            vivo = vivos.get(m["wc_id"])
            stock_var = vivo.get("stock") if vivo else m.get("stock")
            variantes.append({
                "sku": m["sku"],
                "nombre": m.get("sufijo"),
                "precio": vivo.get("precio") if vivo else None,
                "costo": m.get("costo"),
                "stock": stock_var,
                "valor": round((stock_var or 0) * (m.get("costo") or 0), 2),
                "estado": vivo.get("estado") if vivo else m.get("estado"),
            })
        item.update({
            "sku": g["base"],
            "tipo": "padre",
            "variantes": variantes,
        })
        items.append(item)
    return items
