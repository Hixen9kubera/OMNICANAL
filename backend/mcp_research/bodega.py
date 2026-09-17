"""
bodega.py — Capacidades 3 y 4: stock libre y cajas. Las dos salen de ODOO.

ESTO NO ESTÁ EN SUPABASE, Y NO ES UN DESCUIDO
----------------------------------------------
`free_qty` vive sólo en Odoo (`product.product`, por XML-RPC). Es exactamente
lo que se pidió: el físico MENOS lo reservado. Medido el 17-sep-2026:

    productos con código en Odoo .................. 13,189
    con free_qty > 0 ............................... 8,283
    donde free_qty ≠ qty_available (hay reservas) .... 160

Y esos 160 son el motivo del encargo:

    ACC-0069-BEI-VER-XL    físico 100  ·  libre   0
    TEC-1355-NEG           físico 100  ·  libre   2
    JUGU-0089-PLA          físico 354  ·  libre 203

LAS TRES TRAMPAS, ESCRITAS PARA QUE NADIE LAS VUELVA A PISAR
-------------------------------------------------------------
1. **NO se usa `odoo.stock_por_sku`**, que existe y sería lo cómodo: devuelve
   `qty_available`, sin descontar reservas. Aquí se usa
   `odoo.detalle_por_sku`, que pide `free_qty` explícito (el mismo molde que
   `backend/scripts/publicar_amazon.py::stock_odoo`). El caso `VIA-0024-NEG`
   tenía 30 piezas con 29 comprometidas —UNA vendible— y Woo ofrecía 14.

2. **NO se lee el stock de WooCommerce ni de `channel.listings.stock_own`.**
   Son copias. El 17-sep-2026 se midió que 58 SKUs tienen `_stock` vacío en
   Woo, 36 de ellos con piezas en Odoo (4,564 piezas), porque el vigilante se
   salta a los que tienen ese campo vacío. Una respuesta basada en Woo habría
   contestado "0" con 98 piezas en bodega — ése es literalmente el incidente
   que destapó todo.

3. **Por almacén**, `free_qty` global NO distingue almacén: hay que pedirlo con
   `context={"warehouse": id}`. Aquí los almacenes se leen VIVOS de Odoo en vez
   de reusar `services/odoo_ventas.py::libre_por_almacen`, y no por gusto: esa
   función tiene la lista clavada en `_ALMACENES = [(135,"TEXCO"),(150,"TEXCO
   II")]` y **deja DROP OFF fuera a propósito** (para planear envíos eso está
   bien). Este MCP tiene que poder enseñar DROP OFF, que existe y tiene
   mercancía. Verificado el 17-sep-2026: activos son TEXCO (135), DROP OFF
   (142) y TEXCO II (150); PAROLERA (143) está archivado.

CAJAS: SON UN DERIVADO, NO UN CAMPO
------------------------------------
Odoo NO registra cajas. Lo único vivo es el factor `units_per_master_box`
(«Unidades por caja master», entero), y su compañero `cbm_master_box` (el
volumen de esa caja). Medido sobre los 13,189 productos:

    con piezas por caja .... 9,926 (75.3%)
    sin el dato ............ 3,263
    …y de los que lo tienen, **644 valen 1**

Un factor de 1 no describe una caja: describe la FALTA del dato. Aquí se trata
como ausente y `cajas` sale `null`, rotulado `estimado`.

⚠️ `inventario_maestro._cajas` hace la misma cuenta pero su guarda dice `f < 1`,
así que con factor **1** pasa de largo y devuelve `piezas / 1` = las piezas —
al revés de lo que promete su propio docstring. Aquí la guarda es `f <= 1`. No
se copió el bug, y no se tocó aquel archivo: es del panel y tiene sus pruebas.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from config import settings
from services import odoo

from . import conexion as cx

# Foto del catálogo entero, medida el 17-sep-2026. Se deja escrita para que la
# respuesta pueda decir "tu SKU es de los que no tienen factor, como el 24.7%
# del catálogo" sin barrer 13 mil productos en cada llamada.
COBERTURA_CATALOGO = {
    "medido_el": "2026-09-17",
    "productos_con_codigo": 13189,
    "con_piezas_por_caja": 9926,
    "sin_piezas_por_caja": 3263,
    "con_factor_1_tratados_como_sin_dato": 644,
}

_CATALOGO: tuple[float, list[dict[str, Any]]] | None = None
_TTL = 600.0     # 10 min: un barrido completo son ~27 vueltas a Odoo


def cajas(piezas: Any, por_caja: Any) -> float | None:
    """
    Cuántas cajas son esas piezas, según el factor de Odoo. `None` si no se sabe.

    `None` —no cero, y no las piezas— cuando el factor falta o vale 1. Un
    divisor inventado convierte una celda vacía en un número que alguien va a
    creer. Ver el aviso sobre `inventario_maestro._cajas` en el encabezado.
    """
    try:
        p = float(piezas) if piezas is not None else None
        f = float(por_caja) if por_caja is not None else None
    except (TypeError, ValueError):
        return None
    if p is None or not f or f <= 1:
        return None
    return round(p / f, 2)


def _almacenes_sync() -> list[dict[str, Any]]:
    uid = odoo._uid()
    if not uid:
        raise RuntimeError("Odoo: no se pudo autenticar")
    return odoo._models().execute_kw(
        settings.odoo_db, uid, settings.odoo_password,
        "stock.warehouse", "search_read", [[["active", "=", True]]],
        {"fields": ["id", "name", "code"], "order": "id asc"})


def _libre_por_almacen_sync(product_ids: list[int],
                            almacenes: list[dict[str, Any]]) -> dict[int, dict[str, float]]:
    """
    `{product_id: {nombre_almacen: libres}}`.

    Se pide almacén por almacén porque el `context` es UNO por lectura: no hay
    forma de traer los tres de un viaje.
    """
    uid = odoo._uid()
    if not uid or not product_ids:
        return {}
    salida: dict[int, dict[str, float]] = {p: {} for p in product_ids}
    for w in almacenes:
        filas = odoo._models().execute_kw(
            settings.odoo_db, uid, settings.odoo_password,
            "product.product", "read", [product_ids, ["free_qty"]],
            {"context": {"warehouse": w["id"]}})
        for f in filas:
            salida.setdefault(f["id"], {})[w["name"]] = float(f.get("free_qty") or 0)
    return salida


def _catalogo_sync() -> list[dict[str, Any]]:
    """Catálogo completo con `free_qty`, cacheado. `listar_catalogo` ya usa el libre."""
    global _CATALOGO
    if _CATALOGO and (time.monotonic() - _CATALOGO[0]) < _TTL:
        return _CATALOGO[1]
    datos = odoo.listar_catalogo()
    _CATALOGO = (time.monotonic(), datos)
    return datos


async def _fila(sku: str, d: dict[str, Any] | None) -> dict[str, Any]:
    if not d:
        return {"sku": sku, "en_odoo": False, "libre": None, "fisico": None,
                "reservado": None, "nota": "Sin producto en Odoo con ese código."}
    factor = d.get("piezas_por_caja")
    factor_util = bool(factor and float(factor) > 1)
    return {
        "sku": sku,
        "en_odoo": True,
        "nombre": d.get("nombre"),
        "libre": d.get("libre"),
        "fisico": d.get("fisico"),
        "reservado": d.get("reservado"),
        "hay_reservas": bool((d.get("reservado") or 0) > 0),
        "entrante": d.get("entrante"),
        "saliente": d.get("saliente"),
        "activo": d.get("activo"),
        "duplicado_en_odoo": d.get("duplicado"),
        "piezas_por_caja": float(factor) if factor_util else None,
        "piezas_por_caja_cruda": float(factor) if factor else None,
        "cbm_caja": d.get("cbm_caja"),
        "cajas_libres_estimadas": cajas(d.get("libre"), factor),
        "cajas_fisicas_estimadas": cajas(d.get("fisico"), factor),
        "recepcion_abierta_desde": cx.iso(d.get("recepcion_desde")),
    }


async def consultar_stock(skus: list[str] | None = None,
                          por_almacen: bool = False,
                          todos: bool = False,
                          orden: str = "libre_desc",
                          limite: int | None = None) -> dict[str, Any]:
    skus = cx.normalizar_skus(skus)
    limite = cx.limitar(limite)
    avisos: list[str] = []

    if not skus and not todos:
        return {"pregunta": "stock libre en bodega",
                "error": "Hace falta `skus`, o `todos=true` para barrer el "
                         "catálogo completo (más lento: lee los ~13,200 "
                         "productos de Odoo y los cachea 10 minutos).",
                "medido_en": cx.ahora()}

    if todos and not skus:
        cat = await asyncio.to_thread(_catalogo_sync)
        rev = (orden != "libre_asc")
        cat = sorted(cat, key=lambda c: (c.get("stock") or 0), reverse=rev)
        skus = [c["sku"] for c in cat[:limite]]
        avisos.append(
            f"Barrido del catálogo completo: {len(cat)} productos en Odoo, se "
            f"devuelven los {len(skus)} de más stock libre"
            + (" (ascendente)" if not rev else "") + ".")

    detalle = await cx.odoo_detalle(skus)
    filas = [await _fila(s, detalle.get(s)) for s in skus]

    if por_almacen:
        almacenes = await asyncio.to_thread(_almacenes_sync)
        ids = [d["odoo_id"] for d in detalle.values() if d.get("odoo_id")]
        por_id = await asyncio.to_thread(_libre_por_almacen_sync, ids, almacenes)
        for f in filas:
            d = detalle.get(f["sku"])
            f["libre_por_almacen"] = por_id.get(d["odoo_id"]) if d else None
        avisos.append(
            "`libre_por_almacen` son almacenes VIVOS de Odoo: "
            + ", ".join(f"{w['name']} ({w['code']})" for w in almacenes)
            + ". DROP OFF se suele excluir a propósito al planear envíos; aquí "
              "se muestra porque tiene mercancía de verdad.")

    sin_odoo = [f["sku"] for f in filas if not f["en_odoo"]]
    if sin_odoo:
        avisos.append(f"Sin producto en Odoo: {sin_odoo[:10]}"
                      + (f" (+{len(sin_odoo) - 10})" if len(sin_odoo) > 10 else ""))
    con_reservas = [f["sku"] for f in filas if f.get("hay_reservas")]

    # El libre puede salir NEGATIVO, y con `orden='libre_asc'` esos son los
    # primeros que devuelve. No significa "a punto de agotarse": significa que
    # en Odoo se descontaron piezas que nunca se dieron de alta. Es un error de
    # captura, y confundirlo con escasez lleva a recomprar lo que no falta.
    negativos = [f["sku"] for f in filas if (f.get("libre") or 0) < 0]
    if negativos:
        avisos.append(
            f"{len(negativos)} producto(s) con `libre` NEGATIVO: {negativos[:10]}"
            + (f" (+{len(negativos) - 10})" if len(negativos) > 10 else "")
            + ". Un negativo no es escasez, es un descuadre de captura en Odoo: "
              "se descontaron piezas que nunca se dieron de alta.")

    return {
        "pregunta": "stock libre en bodega (free_qty, sin las reservas)",
        "fuente": "Odoo product.product.free_qty, EN VIVO por XML-RPC. "
                  "No es Supabase, no es WooCommerce, no es channel.listings.",
        "medido_en": cx.ahora(),
        "limite_aplicado": limite,
        "productos": filas,
        "con_reservas": con_reservas,
        "sin_producto_en_odoo": sin_odoo,
        "avisos": avisos + [
            "`libre` = free_qty: el físico MENOS lo comprometido en órdenes y "
            "borradores. `fisico` = qty_available. Publicar con el físico es "
            "prometer mercancía que ya tiene dueño.",
            "Las cajas son un DERIVADO (piezas ÷ factor), no un campo de Odoo: "
            "salen `null` cuando el factor falta o vale 1.",
        ],
    }


async def consultar_cajas(skus: list[str] | None = None,
                          limite: int | None = None) -> dict[str, Any]:
    skus = cx.normalizar_skus(skus)
    limite = cx.limitar(limite)
    if not skus:
        return {"pregunta": "cajas y piezas por caja",
                "error": "Hace falta `skus`.", "medido_en": cx.ahora()}

    detalle = await cx.odoo_detalle(skus[:limite])
    filas = []
    for sku in skus[:limite]:
        d = detalle.get(sku)
        if not d:
            filas.append({"sku": sku, "en_odoo": False, "piezas_por_caja": None,
                          "cajas_libres_estimadas": None,
                          "nota": "Sin producto en Odoo con ese código."})
            continue
        crudo = d.get("piezas_por_caja")
        util = bool(crudo and float(crudo) > 1)
        filas.append({
            "sku": sku,
            "en_odoo": True,
            "nombre": d.get("nombre"),
            "piezas_por_caja": float(crudo) if util else None,
            "piezas_por_caja_cruda": float(crudo) if crudo else None,
            "cbm_caja": d.get("cbm_caja"),
            "piezas_libres": d.get("libre"),
            "piezas_fisicas": d.get("fisico"),
            "cajas_libres_estimadas": cajas(d.get("libre"), crudo),
            "cajas_fisicas_estimadas": cajas(d.get("fisico"), crudo),
            "cajas_por_llegar_estimadas": cajas(d.get("entrante"), crudo),
            "nota": None if util else (
                "Factor = 1: en este catálogo eso no describe una caja, describe "
                "la falta del dato (644 productos así). Se trata como ausente."
                if crudo else "Sin `units_per_master_box` en Odoo."),
        })

    sin_factor = [f["sku"] for f in filas if f["piezas_por_caja"] is None]
    return {
        "pregunta": "cajas y piezas por caja",
        "fuente": "Odoo product.product.units_per_master_box / cbm_master_box, "
                  "EN VIVO por XML-RPC.",
        "medido_en": cx.ahora(),
        "limite_aplicado": limite,
        "cobertura_catalogo": COBERTURA_CATALOGO,
        "productos": filas,
        "sin_factor_de_caja": sin_factor,
        "avisos": [
            "Las cajas son ESTIMADAS: Odoo no cuenta cajas, se derivan de "
            "piezas ÷ units_per_master_box. `null` cuando el factor falta o "
            "vale 1.",
            "Esto NO es el packing list. `services/packing_cajas.py` son "
            "cartones de importación —lo que el proveedor embarcó— y contestan "
            "otra pregunta. Aquí manda Odoo, que dice lo que HAY.",
            "`entrante` (y con él `cajas_por_llegar_estimadas`) sale de "
            "`incoming_qty`, que NO es 'mercancía en camino': lo inflan 30 "
            "recepciones huérfanas de mayo-junio que nadie cerró, en el 30% de "
            "los padres. Se da con `recepcion_abierta_desde` en la herramienta "
            "de stock para poder distinguirlo.",
        ],
    }
