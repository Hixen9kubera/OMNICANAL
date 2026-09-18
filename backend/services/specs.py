"""
specs.py — Las ESPECIFICACIONES que cada canal exige a un SKU, por categoría.

QUÉ CONTESTA
------------
«¿Qué característica necesita este producto para poder publicarse en cada
canal?» — una fila por canal, con el color del canal, diciendo qué falta.

Nace de un encargo a Brandon (17-sep-2026): *«tener una lista de Specs por
categoría que sea EDITABLE para que Bodega lo genere en su validación y las
Publicaciones lo envíen»*. Es también lo que desbloquea el cuarto punto de
VALIDADO BODEGA en la pestaña Inventario, que hasta hoy devolvía «espera» fijo
porque nunca se había definido el formato.

CASI NADA DE ESTO ES NUEVO, Y ESO ES LO IMPORTANTE
--------------------------------------------------
La matriz YA existe: `channel.field_requirements`, 74,086 filas verificadas el
17-sep (amazon 64,125 · walmart 3,331 · mercado_libre 2,765 · temu 2,086 ·
tiktok 1,779), cargada de la API de cada canal por los seis scripts de
`backend/scripts/cargar_*`. Este módulo NO la reconstruye: la lee y la cruza con
lo que el SKU ya tiene lleno en `enrich.channel_content`.

SOLO KUBERA (Brandon, 17-sep)
-----------------------------
Prohibido WordPress. Eso no es cosmético: la elección humana de categoría de
Mercado Libre y el product type de Amazon viven en metas de WP, así que aquí se
leen de `channel.product_category` y de `channel.listings`, y lo que no esté ahí
sale **gris**, nunca verde. Medido el 17-sep sobre el catálogo: ML 7,812 SKUs
con requisitos, Amazon 1,814, TikTok ~512, Temu ~177.

LAS DOS TRAMPAS MEDIDAS
-----------------------
1. **Amazon se cruza por `product_type`, NUNCA por `category_id`.** Los 1,822
   listings de Amazon tienen `category_id` NULL al 100%, así que un JOIN por esa
   columna devuelve cero filas **sin dar error** — el peor tipo de fallo.
2. **«Sin requisitos» NO es «está completo».** De las 2,161 categorías de ML en
   uso, 1,105 no tienen una sola fila cargada. Si eso se pintara en verde, el
   panel diría que un producto está listo porque nadie le preguntó a ML. Por eso
   hay un estado `sin_verificar` y **gris nunca cuenta como listo**.

WALMART Y WOO QUEDAN FUERA (decisión de Brandon, 17-sep)
--------------------------------------------------------
Walmart tiene los requisitos llaveados por 76 nombres de esquema
(«Adornos y Decoraciones») y sus 235 listings por 100 nombres de hoja
(«Licuadoras»): **cruzan CERO**. Y WooCommerce no tiene categorías en kubera
—sus 13,212 listings traen `category_id` NULL al 100%— ni concepto de atributo
obligatorio por categoría. Enseñarlos sería enseñar dos columnas grises
permanentes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.marketplaces import MARKETPLACES
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.services.specs")

# Los cuatro canales del MVP, en el orden en que se pintan. Walmart y Woo NO
# están: ver la cabecera. Añadirlos es añadirlos aquí, cuando su categoría
# cruce.
CANALES_SPECS: tuple[str, ...] = ("mercado_libre", "amazon", "tiktok", "temu")

# A partir de cuántos días la lectura de requisitos se considera vieja. No
# invalida el dato: lo rotula. Medido el 17-sep, los cinco canales llevaban
# entre 31 y 36 días sin releerse.
DIAS_FRESCO = 30


def disponible() -> bool:
    return sdb.disponible()


# ══════════════════════════════════════════════════════════════════════════════
# SKU → categoría, por canal. Solo kubera.
# ══════════════════════════════════════════════════════════════════════════════

def _categorias(skus: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """``{sku: {canal: {categoria, fuente}}}`` — de dónde sale la categoría.

    LA PRECEDENCIA RESPETA LA REGLA 2 DE LA CASA: la elección del PANEL manda
    sobre cualquier detector automático. En `channel.product_category` eso es la
    columna `source`, y el orden es panel > real > predictor > costos_ml. Si no
    hay nada ahí, se cae al `category_id` de la publicación viva, que es lo que
    el canal está usando de verdad.

    Amazon va aparte y por `product_type` — ver la trampa 1 de la cabecera.
    """
    if not skus:
        return {}
    salida: dict[str, dict[str, dict[str, Any]]] = {s: {} for s in skus}

    _ORDEN = {"panel": 0, "real": 1, "predictor": 2, "costos_ml": 3}
    try:
        for r in sdb.fetch_all(
                "select sku, channel_id, category_id, source "
                "from channel.product_category "
                "where sku = any(%s) and category_id is not null", (skus,)):
            canal = r["channel_id"]
            if canal not in CANALES_SPECS:
                continue
            actual = salida[r["sku"]].get(canal)
            nuevo = _ORDEN.get(r["source"] or "", 9)
            if actual is None or nuevo < _ORDEN.get(actual["fuente"], 9):
                salida[r["sku"]][canal] = {"categoria": r["category_id"],
                                           "fuente": r["source"] or "?"}
    except Exception as exc:  # noqa: BLE001
        log.warning("specs: product_category no disponible: %s", exc)

    try:
        for r in sdb.fetch_all(
                "select sku, canal, category_id, product_type "
                "from channel.listings where sku = any(%s)", (skus,)):
            canal = r["canal"]
            if canal not in CANALES_SPECS:
                continue
            # Amazon: SIEMPRE product_type. Su category_id está NULL en los
            # 1,822 listings y cruzar por él da cero sin error.
            valor = r["product_type"] if canal == "amazon" else r["category_id"]
            if not valor or salida[r["sku"]].get(canal):
                continue
            salida[r["sku"]][canal] = {"categoria": valor, "fuente": "publicación"}
    except Exception as exc:  # noqa: BLE001
        log.warning("specs: listings no disponible: %s", exc)
    return salida


# ══════════════════════════════════════════════════════════════════════════════
# Los requisitos de esas categorías
# ══════════════════════════════════════════════════════════════════════════════

def _requisitos(pares: set[tuple[str, str]]) -> dict[tuple[str, str], list[dict]]:
    """Los campos que cada (canal, categoría) exige.

    Se piden TODOS, no solo los obligatorios, porque quien edita la lista tiene
    que poder ver lo que hoy es opcional para decidir si lo sube a obligatorio.

    La fila comodín `categoria_id = '*'` son los requisitos que el canal exige
    siempre, caiga en la categoría que caiga (89 filas: walmart 46, tiktok 26,
    ML 12, temu 5). Se suman a los de la categoría concreta.
    """
    if not pares:
        return {}
    canales = sorted({c for c, _ in pares})
    salida: dict[tuple[str, str], list[dict]] = {}
    try:
        filas = sdb.fetch_all(
            "select canal, categoria_id, campo, campo_canonico, obligatorio, "
            "       tipo, valores_permitidos, default_value, fuente, "
            "       leido_at, updated_at "
            "from channel.field_requirements "
            "where (canal, categoria_id) in %s "
            "   or (canal = any(%s) and categoria_id = '*') "
            "order by obligatorio desc, campo",
            (tuple(pares), canales))
    except Exception as exc:  # noqa: BLE001
        log.warning("specs: field_requirements no disponible: %s", exc)
        return {}

    comodines: dict[str, list[dict]] = {}
    for r in filas:
        d = {"campo": r["campo"], "canonico": r["campo_canonico"],
             "obligatorio": bool(r["obligatorio"]), "tipo": r["tipo"],
             "valores": r["valores_permitidos"], "default": r["default_value"],
             "fuente": r["fuente"],
             "comodin": r["categoria_id"] == "*",
             # DOS FAMILIAS DISTINTAS, y mezclarlas engaña. Los `api` de una
             # categoría concreta son ATRIBUTOS que captura una persona
             # (MODEL, IS_FOLDABLE). Los `codigo` y los comodines son campos
             # del CUERPO de la publicación —title, price, pictures— que el
             # publicador arma del producto y que nadie teclea en bodega.
             # Medido en MLM81144: 5 atributos contra 12 campos de cuerpo, y
             # contarlos juntos daba «faltan 11 de 16» cuando el trabajo real
             # son 4.
             "captura": r["categoria_id"] != "*" and r["fuente"] == "api",
             "leido_at": r["leido_at"].isoformat() if r["leido_at"] else None}
        if r["categoria_id"] == "*":
            comodines.setdefault(r["canal"], []).append(d)
        else:
            salida.setdefault((r["canal"], r["categoria_id"]), []).append(d)

    for canal, cat in pares:
        extra = comodines.get(canal) or []
        if not extra:
            continue
        propios = {x["campo"] for x in salida.get((canal, cat), [])}
        salida.setdefault((canal, cat), []).extend(
            x for x in extra if x["campo"] not in propios)
    return salida


def _llenos(skus: list[str]) -> dict[tuple[str, str], set[str]]:
    """Qué atributos tiene ya capturados cada SKU, por canal.

    Sale de `enrich.channel_content`, que es donde el Estudio guarda lo que se
    edita a mano. OJO con su tamaño: 1,069 filas para 22 mil SKUs. Lo de
    Mercado Libre vive casi todo en metas `ml_attr_*` de WordPress, que están
    fuera de alcance — por eso la columna de ML va a decir «falta» en productos
    que en ML están completos. Es un hueco REAL y se rotula como tal, no se
    disimula.
    """
    if not skus:
        return {}
    salida: dict[tuple[str, str], set[str]] = {}
    try:
        filas = sdb.fetch_all(
            "select sku, canal, contenido from enrich.channel_content "
            "where sku = any(%s)", (skus,))
    except Exception as exc:  # noqa: BLE001
        log.warning("specs: channel_content no disponible: %s", exc)
        return {}
    for r in filas:
        cont = r["contenido"] or {}
        attrs = cont.get("atributos") if isinstance(cont, dict) else None
        campos: set[str] = set()
        if isinstance(attrs, dict):
            campos = {k for k, v in attrs.items() if v not in (None, "", [])}
        elif isinstance(attrs, list):
            for a in attrs:
                if isinstance(a, dict) and a.get("valor") not in (None, "", []):
                    campos.add(str(a.get("campo") or a.get("id") or ""))
        salida[(r["sku"], r["canal"])] = {c for c in campos if c}
    return salida


# ══════════════════════════════════════════════════════════════════════════════
# La respuesta
# ══════════════════════════════════════════════════════════════════════════════

def _estado(cat: str | None, reqs: list[dict],
            llenos: set[str]) -> tuple[str, list, list, list]:
    """Los CUATRO estados, y el gris nunca es verde.

      · `sin_categoria`  — el SKU no tiene categoría en ese canal. Gris.
      · `sin_verificar`  — hay categoría pero nadie le preguntó al canal qué
                           exige ESA categoría. Gris. ES DISTINTO de «no exige
                           nada», y confundirlos es decir que un producto está
                           listo porque no se hizo la pregunta.
      · `incompleto`     — faltan ATRIBUTOS de captura. Rojo.
      · `listo`          — los atributos de captura sin valor por omisión están
                           llenos.

    SOLO CUENTAN LOS DE CAPTURA. Los campos del cuerpo de la publicación
    (`title`, `price`, `pictures`…) los arma el publicador desde el producto y
    meterlos en la cuenta convertía «4 atributos por capturar» en «faltan 11 de
    16»: un número que ni es accionable ni es cierto.

    LAS FILAS COMODÍN NO CUENTAN PARA VERIFICAR. `categoria_id='*'` son los
    campos que el canal exige SIEMPRE (ML tiene 12, TikTok 26, Temu 5), y si un
    SKU solo tiene esos, de su categoría concreta no se sabe nada. Sin este
    corte, ROP-0731-BLN salía «incompleto 7 de 12» —con cara de dato— cuando lo
    único cierto es que nadie le preguntó a ML qué pide MLM431078. Y sí pide:
    su API pública contesta BRAND, MODEL y COLOR. Los comodines se siguen
    mostrando, pero no bastan para decir que la categoría está verificada.
    """
    if not cat:
        return "sin_categoria", [], [], []
    if not any(not r["comodin"] for r in reqs):
        return "sin_verificar", [], [], []
    faltan, automaticos, del_publicador = [], [], []
    for r in reqs:
        if not r["obligatorio"] or r["campo"] in llenos:
            continue
        if not r["captura"]:
            # Cuerpo de la publicación: lo arma el publicador del producto.
            del_publicador.append(r)
        elif r["default"] is not None:
            # Con valor por omisión tampoco es trabajo de bodega, y pintarlo en
            # rojo mandaría a alguien a llenar lo que se llena solo.
            automaticos.append(r)
        else:
            faltan.append(r)
    return ("listo" if not faltan else "incompleto"), faltan, automaticos, del_publicador


def _por_sku_sync(skus: list[str]) -> dict[str, dict[str, Any]]:
    skus = [s.strip() for s in (skus or []) if (s or "").strip()]
    if not skus or not disponible():
        return {}
    cats = _categorias(skus)
    pares = {(c, d["categoria"]) for m in cats.values() for c, d in m.items()}
    reqs = _requisitos(pares)
    llenos = _llenos(skus)

    salida: dict[str, dict[str, Any]] = {}
    for sku in skus:
        canales = []
        for canal in CANALES_SPECS:
            info = (cats.get(sku) or {}).get(canal) or {}
            cat = info.get("categoria")
            lista = reqs.get((canal, cat), []) if cat else []
            ya = llenos.get((sku, canal), set())
            estado, faltan, autom, del_pub = _estado(cat, lista, ya)
            cfg = MARKETPLACES.get(canal) or {}
            canales.append({
                "canal": canal,
                "etiqueta": cfg.get("label") or canal,
                # El color de marca sale del registro canónico del panel, NO de
                # una copia: `frontend/lib/theme.ts` y `monitoreo/page.tsx`
                # tienen mapas propios que ya divergen (TikTok es #000000 aquí
                # y #111827 allá).
                "color": cfg.get("color", "#64748B"),
                "color_texto": cfg.get("color_texto", "#FFFFFF"),
                "acento": cfg.get("acento", "#64748B"),
                "categoria": cat,
                "categoria_fuente": info.get("fuente"),
                "estado": estado,
                # Solo los que CAPTURA una persona. El resto no es trabajo de
                # bodega y sumarlo infla el número que se lee como pendiente.
                "obligatorios": sum(1 for r in lista
                                    if r["obligatorio"] and r["captura"]),
                "total": len(lista),
                "faltan": [{"campo": r["campo"], "canonico": r["canonico"],
                            "tipo": r["tipo"]} for r in faltan],
                "automaticos": [r["campo"] for r in autom],
                "del_publicador": [r["campo"] for r in del_pub],
                "llenos": sorted(ya & {r["campo"] for r in lista}),
                "leido_at": next((r["leido_at"] for r in lista if r["leido_at"]), None),
            })
        # El VEREDICTO para Bodega es el de MERCADO LIBRE (Brandon, 17-sep): es
        # el canal con cobertura real (7,812 SKUs contra 177 de Temu) y exigir
        # los cuatro dejaría en rojo a casi todo el catálogo. Los otros tres se
        # muestran, pero informan; no bloquean.
        ml = next(c for c in canales if c["canal"] == "mercado_libre")
        salida[sku] = {"canales": canales, "veredicto": ml["estado"],
                       "canal_veredicto": "mercado_libre"}
    return salida


async def por_sku(skus: list[str]) -> dict[str, dict[str, Any]]:
    """Las specs por canal de varios SKUs. En hilo aparte: psycopg2 BLOQUEA y
    esto corre dentro de una corrutina (regla 11 — el apagón del 13-ago)."""
    try:
        return await asyncio.to_thread(_por_sku_sync, skus)
    except Exception as exc:  # noqa: BLE001
        log.warning("specs.por_sku falló: %s", exc)
        return {}


def matriz(canal: str, categoria: str) -> list[dict[str, Any]]:
    """La lista EDITABLE de una categoría: todos sus campos, no solo los
    obligatorios. Es lo que ve quien edita la plantilla."""
    return _requisitos({(canal, categoria)}).get((canal, categoria), [])
