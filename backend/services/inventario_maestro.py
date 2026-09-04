"""
inventario_maestro.py — La pestaña INVENTARIO · Catálogo Maestro.

QUÉ ES ESTO
-----------
Una vista de LECTURA sobre el inventario: qué hay, dónde está, en qué caja
vino, y qué se movió. Nace VISOR a propósito — no escribe stock en ninguna
parte — y esa decisión está medida, no supuesta:

  · El saldo inicial ya existe y es auditable: `free_qty` de Odoo reproduce
    EXACTO el saldo reconstruido desde su propio libro de movimientos en el
    100% de una muestra de 150 SKUs al azar (medido 2026-09-02, ver
    `odoo._vendible` para la única sutileza que hacía fallar ese cuadre).
  · Contar la bodega a mano serían 1,164,329 piezas en 30,982 ubicaciones a
    nivel de rack. Eso lo hace el equipo de Inventarios en Odoo, y sus nombres
    ya firman los ajustes.
  · Y si esta pestaña fuera el master, `stock_watch` tendría que leerla a ella
    en vez de a Odoo: eso invierte la cadena `Odoo → Woo → canales` que Brandon
    fijó el 20-ago. Es un cambio de flujo vivo, no una pantalla.

Por eso aquí NO hay un solo INSERT ni un PUT a ningún canal. Cuando exista la
captura humana (entrada por packing list), va en otro módulo y con el dale de
Brandon, porque enciende un flujo.

LA FILA ES EL SKU DE WOOCOMMERCE
--------------------------------
Padres y variaciones en la misma tabla, con `es_padre`. Son 14,737 filas y cero
colisiones de SKU entre los dos tipos. Las otras opciones se midieron y pierden:
solo padres deja fuera 7,453 variaciones —que son las que tienen stock, porque
un padre variable no guarda `_stock` en Woo—; `core.products` completo mete
7,630 filas fantasma (`packing_list_only`: cero existen en Woo y cero como
producto activo en Odoo).

Y los padres NO se pueden filtrar aunque no gestionen stock: VENDEN. Medido, 30
líneas en Mercado Libre y 2 en Amazon entre el 25-jun y el 28-ago.

LAS TRES MENTIRAS QUE ESTA PESTAÑA TIENE PROHIBIDO REPETIR
-----------------------------------------------------------
1. **`incoming_qty` no es "mercancía en camino".** Lo envenenan **30 recepciones
   huérfanas** de mayo-junio que nadie cerró (11,843 renglones de producto entre
   las 30), y eso infla el entrante de 2,837 SKUs: el 30% de los padres. El
   flujo SÍ funciona —379 recepciones validadas en los últimos 30 días—, así que
   no hay nada que rediseñar: hay 30 documentos que cerrar. Aquí se pinta como
   `recepcion_abierta` con su fecha y sus días, nunca como "llegando".
2. **Una variación `publish` bajo un padre no publicado NO SE VE.** Son 4,498
   de 7,329. Se marca con `invisible_en_tienda`.
3. **El contenedor de Odoo y el de `costos_validados` discrepan en el 37%** de
   los SKUs donde ambos existen, y no solo de formato. Se devuelven LOS DOS y
   se marca `contenedor_discrepa` — pintar uno solo sería inventar.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from services import odoo, supabase_db as sdb, wp_db

log = logging.getLogger("omnicanal.inventario_maestro")

# Los 10 SKUs con los que Brandon quiere ver la pestaña funcionando antes de
# soltarla al catálogo completo. Es una SONDA, no un modelo de datos: cuando se
# decida cómo se mantienen las listas de prioridad, esto se reemplaza por su
# tabla. Mientras tanto vive aquí, a la vista, en vez de escondido en la BD.
PILOTO: tuple[str, ...] = (
    "ROP-0731-BLN", "ACC-0907-MET", "TV-0001-MET", "JUGU-1153-MET",
    "HERR-0343-MET", "ELEC-0034-EST", "OFI-0412-EST", "DEPO-0048-EST",
    "HERR-0146-EST", "VEH-0148-EST",
)

# Cuántas fotos se consideran "la etapa de fotos terminada". El catálogo tiene
# 1,649 productos con 5 o más y 208 sin ninguna imagen: 4 es el corte que
# separa "ya se trabajó" de "solo tiene la portada del scraping".
_FOTOS_COMPLETAS = 4

# La escalera de WooCommerce, de menos a más avanzado. `ready` no es de
# WordPress: la inventó el equipo el 8-feb-2026 y el panel ya la entiende.
_ESCALERA = {"draft": 0, "pending": 1, "ready": 2, "publish": 3}


# ─────────────────────────────────────────────────────────────────────────────
# LA TABLA
# ─────────────────────────────────────────────────────────────────────────────

def filas(skus: list[str] | None = None) -> list[dict[str, Any]]:
    """
    Un renglón por SKU con todo lo que pide el diseño: imagen, contenedor,
    cajas, piezas, variantes, stock por origen y las cinco etapas.

    Se consulta TODO EN VIVO. La regla de la casa es explícita: nunca cruzar
    contra `canal_inventario`, `ml_progress` ni `amazon_progress`, porque el
    caché ya escondió 754 publicaciones de Mercado Libre.
    """
    pedidos = [s.strip() for s in (skus or list(PILOTO)) if s and s.strip()]
    if not pedidos:
        return []

    woo = wp_db.maestro_por_sku(pedidos)
    od = odoo.detalle_por_sku(pedidos)
    ubis = odoo.ubicaciones_por_sku(pedidos)
    costos = _costos(pedidos)
    canales = _canales(pedidos)
    proceso = _ultimo_proceso(pedidos)
    imgs = wp_db.imagenes_por_wc_id([d["wc_id"] for d in woo.values()])

    salida = []
    for sku in pedidos:
        salida.append(_fila(sku, woo.get(sku), od.get(sku), costos.get(sku),
                            canales.get(sku, []), proceso.get(sku),
                            imgs.get((woo.get(sku) or {}).get("wc_id")),
                            ubis.get(sku, [])))
    return salida


def _fila(sku: str, w: dict | None, o: dict | None, c: dict | None,
          pubs: list[dict], plog: dict | None, imagen: str | None,
          ubicaciones: list[dict]) -> dict[str, Any]:
    es_variacion = bool(w and w["tipo"] == "variacion")
    es_padre = bool(w and w["n_hijas"] > 0)

    # Una variación publicada bajo un padre que no lo está es invisible en la
    # tienda aunque su propio status diga `publish`. Son 4,498 de 7,329.
    invisible = bool(es_variacion and w and w["status"] == "publish"
                     and w.get("parent_status") != "publish")

    emp_odoo = _empaque((o or {}).get("contenedor"))
    emp_costo = _empaque((c or {}).get("contenedor"))
    # El código ISO solo lo tiene kubera; el embarque, los dos. Se prefiere el
    # ISO para mostrar y el embarque para comparar. Cuando no hay ISO por
    # ningún lado se muestra la referencia del transitario CRUDA y marcada
    # (`contenedor_es_booking`): dejar la celda vacía haría creer que no se
    # sabe de dónde vino la mercancía, cuando sí se sabe a medias.
    contenedor = emp_costo["iso"] or emp_odoo["iso"]
    es_booking = not contenedor
    if es_booking:
        contenedor = emp_costo["crudo"] or emp_odoo["crudo"]
    embarque = emp_costo["embarque"] or emp_odoo["embarque"]
    discrepa = bool(emp_odoo["embarque"] and emp_costo["embarque"]
                    and emp_odoo["embarque"] != emp_costo["embarque"])

    full = sum(float(p.get("stock_full") or 0) for p in pubs)
    fba = sum(float(p.get("stock_fba") or 0) for p in pubs)

    fila = {
        "sku": sku,
        "existe_en_woo": w is not None,
        "existe_en_odoo": o is not None,
        "nombre": (w or {}).get("titulo") or (o or {}).get("nombre") or "",
        "imagen": imagen,
        "wc_id": (w or {}).get("wc_id"),
        "odoo_id": (o or {}).get("odoo_id"),

        # jerarquía — nunca se deduce del nombre del SKU: 5,764 de los 5,783
        # productos simples tienen la misma forma que una variante.
        "tipo": (w or {}).get("tipo") or "sin_alta",
        "es_padre": es_padre,
        "n_hijas": (w or {}).get("n_hijas") or 0,
        "padre_sku": (w or {}).get("parent_sku"),
        "padre_status": (w or {}).get("parent_status"),
        "invisible_en_tienda": invisible,

        # empaque
        "contenedor": contenedor,
        "contenedor_es_booking": es_booking,
        "embarque": embarque,
        "contenedor_odoo": emp_odoo["crudo"],
        "contenedor_costo": emp_costo["crudo"],
        "contenedor_discrepa": discrepa,
        "cajas": _num((c or {}).get("cajas")),
        "piezas_por_caja": _num((c or {}).get("piezas_por_caja")),
        "piezas_declaradas": _piezas_declaradas(c),

        # existencias
        "stock_woo": (w or {}).get("stock"),
        "stock_odoo": _num((o or {}).get("libre")),
        "stock_fisico": _num((o or {}).get("fisico")),
        "reservado": _num((o or {}).get("reservado")),
        "stock_full": full or None,
        "stock_fba": fba or None,
        "descuadre": _descuadre(w, o),

        # recepción abierta — NO es "en camino", ver la nota de cabecera
        "recepcion_piezas": _num((o or {}).get("entrante")),
        "recepcion_desde": _iso((o or {}).get("recepcion_desde")),
        "recepcion_dias": _dias((o or {}).get("recepcion_desde")),
        "recepcion_ref": (o or {}).get("recepcion_ref"),

        # dónde está — a nivel de rack, y es dato que SOLO existe en Odoo
        "ubicaciones": ubicaciones,
        "bodegas": sorted({u["bodega"] for u in ubicaciones if u["vendible"]}),
        "rack": ubicaciones[0]["rack"] if ubicaciones else "",
        "bodega": ubicaciones[0]["bodega"] if ubicaciones else "",
        "n_ubicaciones": len(ubicaciones),
        "no_vendible": sum(u["piezas"] for u in ubicaciones if not u["vendible"]) or None,

        "odoo_duplicado": bool((o or {}).get("duplicado")),
        "odoo_archivado": bool(o and not o.get("activo")),
        "status_wc": (w or {}).get("status") or "",
        "creado": _iso((w or {}).get("creado")),
        "modificado": _iso((w or {}).get("modificado")),
        "canales": [{"canal": p["canal"], "status": p.get("status"),
                     "listing_id": p.get("listing_id"),
                     "fulfillment": bool(p.get("is_fulfillment"))} for p in pubs],
    }
    fila["etapas"] = _etapas(fila, w, c, pubs, plog)
    fila["cuadre"] = _cuadre(fila, pubs)
    return fila


def _cuadre(f: dict[str, Any], pubs: list[dict]) -> dict[str, str]:
    """
    La columna «Woo ↔ físico» del diseño, resuelta a UNA píldora.

    El orden es de peor a menos malo, porque la celda solo puede decir una cosa
    y hay que decir la que duele: publicado sin stock es una sobreventa esperando
    a pasar; un descuadre es un número mal; «cuadra» es lo aburrido.
    """
    activos = [p for p in pubs
               if str(p.get("status") or "").lower() in ("published", "active")]
    if activos and not (f["stock_odoo"] or 0):
        return {"estado": "peligro", "etiqueta": "Activo sin stock",
                "detalle": f"{len(activos)} publicaciones vivas y 0 piezas"}
    if f["descuadre"]:
        d = f["descuadre"]
        return {"estado": "peligro", "etiqueta": f"Woo {d:+d}",
                "detalle": "WooCommerce no coincide con el disponible de Odoo"}
    if not f["existe_en_woo"]:
        return {"estado": "aviso", "etiqueta": "Sin alta",
                "detalle": "no existe en WooCommerce"}
    if not f["existe_en_odoo"]:
        return {"estado": "aviso", "etiqueta": "Sin Odoo",
                "detalle": "no existe en Odoo: sin existencias que comparar"}
    if f["stock_woo"] is None:
        return {"estado": "neutro", "etiqueta": "Sin gestión",
                "detalle": "WooCommerce no gestiona stock de este SKU"}
    return {"estado": "ok", "etiqueta": "Cuadra", "detalle": ""}


def resumen(filas_: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Las tarjetas KPI y la banda de alertas del diseño, calculadas sobre lo que
    se está viendo — no sobre el catálogo entero. Es deliberado: un KPI que
    cuenta otra cosa que la tabla debajo es cómo se pierde la confianza en un
    tablero.

    `avance` es el que contesta la pregunta de Brandon: de N SKUs, cuántos
    tienen las cuatro etapas de trabajo cerradas (`en_proceso` no cuenta porque
    es el reloj, no una tarea).
    """
    def suma(clave: str) -> float:
        return sum(f.get(clave) or 0 for f in filas_)

    trabajo = ("fotos", "variantes", "validado", "enviado_full")
    completos = sum(
        1 for f in filas_
        if all(f["etapas"][e]["estado"] in ("listo", "na") for e in trabajo))

    return {
        "skus": len(filas_),
        "disponible": suma("stock_odoo"),
        "fisico": suma("stock_fisico"),
        "reservado": suma("reservado"),
        "en_recepcion": suma("recepcion_piezas"),
        "full": suma("stock_full"),
        "fba": suma("stock_fba"),
        "completos": completos,
        "no_vendible": suma("no_vendible"),
        "bodegas": sorted({b for f in filas_ for b in f["bodegas"]}),
        "ultimo_empuje": _ultimo_empuje(),
        "alertas": {
            "sin_alta": sum(1 for f in filas_ if not f["existe_en_woo"]),
            "sin_odoo": sum(1 for f in filas_ if not f["existe_en_odoo"]),
            "invisibles": sum(1 for f in filas_ if f["invisible_en_tienda"]),
            "activo_sin_stock": sum(
                1 for f in filas_ if f["cuadre"]["etiqueta"] == "Activo sin stock"),
            "descuadre": sum(1 for f in filas_ if f["descuadre"]),
            "recepcion_vencida": sum(1 for f in filas_
                                     if (f["recepcion_dias"] or 0) > 30),
            "sin_fotos": sum(1 for f in filas_
                             if f["etapas"]["fotos"]["estado"] == "pendiente"),
            "sin_costo": sum(1 for f in filas_
                             if f["etapas"]["validado"]["estado"] == "pendiente"),
            "contenedor_discrepa": sum(1 for f in filas_ if f["contenedor_discrepa"]),
            "odoo_duplicado": sum(1 for f in filas_ if f["odoo_duplicado"]),
        },
        "por_etapa": {
            e: {est: sum(1 for f in filas_ if f["etapas"][e]["estado"] == est)
                for est in ("listo", "parcial", "pendiente", "bloqueado", "na")}
            for e in ("en_proceso", "fotos", "variantes", "validado", "enviado_full")
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# LAS CINCO ETAPAS
# ─────────────────────────────────────────────────────────────────────────────

def _etapas(fila: dict, w: dict | None, c: dict | None,
            pubs: list[dict], plog: dict | None) -> dict[str, dict[str, Any]]:
    """
    Las cinco etapas que pidió Brandon: en proceso (con tiempo), fotos,
    variantes, validado, enviado full.

    TODAS SE DERIVAN de dato que ya existe — no hay tabla nueva, y ese es el
    punto: el tablero funciona desde el primer minuto sin que nadie capture
    nada. Cada etapa dice de dónde salió en `fuente`, para que se pueda
    discutir el dato y no solo el color.

    Estados: `listo` · `parcial` · `pendiente` · `na` · `bloqueado`.
    """
    e: dict[str, dict[str, Any]] = {}

    # 1 · EN PROCESO (con tiempo)
    if not w:
        e["en_proceso"] = _et("bloqueado", "sin alta en WooCommerce",
                              "no existe el producto", "wp_posts")
    else:
        dias = _dias(w.get("creado")) or 0
        quieto = _dias(w.get("modificado")) or 0
        peldano = _ESCALERA.get(w["status"], 0)
        estado = "listo" if peldano >= 3 else ("parcial" if peldano >= 1 else "pendiente")
        e["en_proceso"] = _et(
            estado, w["status"],
            f"{dias} días de alta · {quieto} sin tocar", "wp_posts.post_status",
            dias=dias, dias_quieto=quieto, peldano=peldano)
        if plog:
            e["en_proceso"]["ultimo_paso"] = plog.get("accion")
            e["en_proceso"]["ultimo_actor"] = plog.get("actor")
            e["en_proceso"]["ultimo_at"] = _iso(plog.get("created_at"))

    # 2 · FOTOS
    if not w:
        e["fotos"] = _et("bloqueado", "sin producto", "", "wp_postmeta")
    else:
        n = w["n_galeria"] + (1 if w["tiene_portada"] else 0)
        if n >= _FOTOS_COMPLETAS:
            estado = "listo"
        elif n > 0:
            estado = "parcial"
        else:
            estado = "pendiente"
        e["fotos"] = _et(estado, "1 imagen" if n == 1 else f"{n} imágenes",
                         "portada + galería" if w["tiene_portada"] else "sin portada",
                         "wp_postmeta._thumbnail_id/_product_image_gallery", n=n)

    # 3 · VARIANTES
    if not w:
        e["variantes"] = _et("bloqueado", "sin producto", "", "wp_posts")
    elif fila["invisible_en_tienda"]:
        # El caso que un tablero ingenuo pintaría en verde y está roto.
        e["variantes"] = _et(
            "bloqueado", f"padre en {fila['padre_status']}",
            "la variación está publicada pero su padre no: no se ve en la tienda",
            "wp_posts.post_parent")
    elif w["tipo"] == "variacion":
        e["variantes"] = _et("listo", f"variante de {fila['padre_sku'] or '—'}",
                             "", "wp_posts.post_parent")
    elif fila["es_padre"]:
        e["variantes"] = _et("listo", f"padre con {fila['n_hijas']} variantes",
                             "", "wp_posts.post_parent")
    else:
        e["variantes"] = _et("na", "producto simple",
                             "no lleva variantes", "wp_posts.post_parent")

    # 4 · VALIDADO — el candado COSTO VALIDADO
    if not c:
        e["validado"] = _et("pendiente", "sin costo",
                            "no tiene renglón en costos_validados",
                            "costing.costos_validados")
    elif c.get("revisado_at"):
        e["validado"] = _et("listo", _iso(c["revisado_at"])[:10],
                            f"validado por {c.get('revisado_por') or '—'}",
                            "costing.costos_validados.revisado_at")
    else:
        e["validado"] = _et("parcial", "costo sin validar",
                            "tiene costo pero nadie pasó el candado",
                            "costing.costos_validados")

    # 5 · ENVIADO FULL
    ffm = [p for p in pubs
           if p.get("is_fulfillment") or (p.get("stock_full") or 0) > 0
           or (p.get("stock_fba") or 0) > 0]
    if ffm:
        e["enviado_full"] = _et(
            "listo", ", ".join(sorted({p["canal"] for p in ffm})),
            f"{fila['stock_full'] or 0:.0f} en FULL · {fila['stock_fba'] or 0:.0f} en FBA",
            "channel.listings.is_fulfillment")
    elif (fila["stock_odoo"] or 0) > 0:
        e["enviado_full"] = _et("pendiente", "hay stock, no se ha enviado",
                                "", "channel.listings")
    elif fila["recepcion_piezas"]:
        # NO se dice "en tránsito": la recepción está ABIERTA, que no es lo mismo.
        d = fila["recepcion_dias"]
        e["enviado_full"] = _et(
            "bloqueado", "esperando mercancía",
            f"{fila['recepcion_piezas']:.0f} pzas en recepción abierta"
            + (f" desde hace {d} días" if d else ""),
            "odoo stock.move pendiente")
    else:
        e["enviado_full"] = _et("pendiente", "sin stock", "", "odoo")
    return e


def _et(estado: str, etiqueta: str, detalle: str, fuente: str, **extra) -> dict[str, Any]:
    return {"estado": estado, "etiqueta": etiqueta, "detalle": detalle,
            "fuente": fuente, **extra}


# ─────────────────────────────────────────────────────────────────────────────
# EL HISTORIAL
# ─────────────────────────────────────────────────────────────────────────────

def movimientos(sku: str, causa: str | None = None, limite: int = 400,
                dias: int | None = None) -> dict[str, Any]:
    """
    El libro de bodega de un SKU, del más reciente al más viejo, con SALDO
    corriente — que es lo que pide el diseño y lo que hace auditable la lista.

    Viene de Odoo y solo de Odoo. `channel.listing_history` no sirve para esto
    (8 SKUs rotos generan el 87% de su "movimiento" en Mercado Libre) y
    `ops.fanout_log` deja `stock_drop`/`objetivo`/`stock_canal` en NULL
    justamente en las acciones que mueven inventario (`odoo_delta` 1,668 filas
    y `woo_cambio` 499, las tres columnas 100% nulas).

    Se devuelve `cuadra`: si el saldo reconstruido no coincide con el
    `qty_available` de Odoo, la pestaña LO DICE en vez de disimularlo.
    """
    sku = (sku or "").strip()
    if not sku:
        return {"sku": sku, "movimientos": [], "cuadra": None}

    movs = odoo.movimientos_por_sku(sku, limite=max(limite, 2000))
    det = odoo.detalle_por_sku([sku]).get(sku) or {}

    # El saldo se calcula sobre TODO el libro y de más viejo a más nuevo; luego
    # se invierte. Calcularlo sobre la página visible daría un saldo que empieza
    # en cero a media historia.
    saldo = 0.0
    for m in reversed(movs):
        saldo += m["delta"]
        m["saldo"] = saldo

    fisico = float(det.get("fisico") or 0)
    total = sum(m["delta"] for m in movs)

    # La ventana se aplica DESPUÉS de calcular el saldo, nunca antes: el saldo
    # corriente tiene que venir arrastrado desde el primer movimiento del SKU o
    # el renglón más viejo de la ventana arrancaría en cero y toda la columna
    # quedaría corrida.
    en_ventana = movs
    if dias and dias > 0:
        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        en_ventana = [m for m in movs
                      if (f := _fecha(m["fecha"])) is not None and f >= corte]

    if causa == "reales":
        # El filtro por omisión de la pestaña: todo menos los pasos internos.
        visibles = [m for m in en_ventana if m["causa"] not in _RUIDO]
    elif causa:
        visibles = [m for m in en_ventana if m["causa"] == causa]
    else:
        visibles = en_ventana

    return {
        "sku": sku,
        "movimientos": [_mov(m) for m in visibles[:limite]],
        "total": len(visibles),
        "total_historico": len(movs),
        "dias": dias,
        "saldo_libro": total,
        "saldo_odoo": fisico,
        # Con la regla de vendible (ver odoo._vendible) esto cuadró en 150 de
        # 150 SKUs de una muestra al azar. Si aquí sale False, es un caso real
        # que Inventarios tiene que revisar — no un bug de la pestaña.
        "cuadra": abs(total - fisico) < 0.5 if movs else None,
        # Sobre la VENTANA, no sobre el histórico: son los contadores de los
        # chips, y un chip que dice "Entradas 1" tiene que enseñar una entrada
        # al pulsarlo. Contando el histórico completo, con la ventana de 90 días
        # ese chip abría una lista vacía.
        "por_causa": {c: sum(1 for m in en_ventana if m["causa"] == c)
                      for c in sorted({m["causa"] for m in en_ventana})},
    }


# Causas cuya contraparte es una EMPRESA (proveedor o bodega de marketplace) y
# por tanto se puede mostrar. En una venta o una devolución la contraparte es
# una PERSONA, y su nombre no pinta nada en una pantalla de bodega.
_CONTRAPARTE_VISIBLE = {"envio_full", "entrada", "traspaso", "preparacion"}

# Pasos internos de la ruta de entrega de Odoo (PICK → PACK → OUT). No mueven
# mercancía entre almacenes ni cambian el saldo, y son mayoría: en TEC-0004-BLN,
# 91 de 117 renglones. La pestaña los trae pero los pliega por omisión.
_RUIDO = {"preparacion"}


def _mov(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "fecha": _iso(m["fecha"]),
        "causa": m["causa"],
        "delta": m["delta"],
        "cantidad": m["cantidad"],
        "saldo": m.get("saldo"),
        "documento": m["documento"],
        "referencia": m["referencia"],
        # PII: el `partner_id` del picking es el COMPRADOR en las ventas y
        # devoluciones. El panel ya cifró los nombres de comprador en los
        # pedidos (v0.42.2, requisito de Temu) — sacarlos de nuevo por una
        # pestaña de inventario sería deshacer eso por la puerta de atrás.
        # Aquí solo pasa la contraparte cuando es una empresa.
        "contraparte": m["contraparte"] if m["causa"] in _CONTRAPARTE_VISIBLE else "",
        "origen": m["origen"],
        "destino": m["destino"],
        "almacen": m["almacen_destino"] or m["almacen_origen"] or "",
        "quien": m["quien"],
        "interno": m["causa"] in _RUIDO,
        # Cuando lo PEDIDO y lo HECHO no coinciden hay algo que contar: es el
        # 6.2% de los movimientos, y es de donde salen los faltantes de embarque.
        # Caso real: TEC-2348-MUL, recepción con 3,548 pedidas y 496 recibidas.
        # Se exige `pedido > 0` porque los traspasos internos lo dejan en 0 y
        # "pedidas 0" no le dice nada a nadie: sería ruido en cada renglón.
        "pedido": (m["pedido"] if m["pedido"] > 0
                   and abs(m["pedido"] - m["cantidad"]) > 0.5 else None),
    }


# ─────────────────────────────────────────────────────────────────────────────
# LECTURAS DE APOYO
# ─────────────────────────────────────────────────────────────────────────────

def _ultimo_empuje() -> dict[str, Any]:
    """Cuándo corrió por última vez el vigilante que copia Odoo→Woo, y a cuántos
    SKUs les escribió.

    Es el rótulo del banner. Sin él, la pestaña muestra un número sin decir de
    cuándo es — y el de Odoo puede tener hasta 20 minutos de retraso frente a
    lo que Woo está publicando ahora mismo.
    """
    vacio = {"cuando": None, "skus": None, "escrituras": None}
    try:
        foto = sdb.fetch_one(
            "select max(actualizado) as cuando, count(*) as skus "
            "from ops.stock_watch_photo")
        esc = sdb.fetch_one(
            "select count(*) as n from ops.fanout_log "
            "where accion = 'escribir' and ts > now() - interval '24 hours'")
    except Exception as exc:  # noqa: BLE001
        log.warning("inventario: último empuje no disponible: %s", exc)
        return vacio
    return {
        "cuando": _iso((foto or {}).get("cuando")),
        "skus": (foto or {}).get("skus"),
        "escrituras": (esc or {}).get("n"),
    }


def _costos(skus: list[str]) -> dict[str, dict[str, Any]]:
    """Contenedor, cajas y piezas por caja.

    AVISO: las tres columnas están MUERTAS EN ESCRITURA desde el 13-ago —
    `costing_mirror.upsert_validados` no las nombra en su INSERT, así que las
    filas nuevas nacen en NULL. Lo que se lee aquí es una foto histórica
    migrada, no un dato vivo, y por eso el contenedor de Odoo entra como
    segunda fuente en `_fila`.
    """
    if not skus:
        return {}
    try:
        filas_ = sdb.fetch_all(
            "select sku::text as sku, contenedor, cajas, piezas_por_caja, "
            "       revisado_at, revisado_por "
            "from costing.costos_validados where sku::text = any(%s)", (skus,))
    except Exception as exc:  # noqa: BLE001
        log.warning("inventario: costos_validados falló: %s", exc)
        return {}
    return {r["sku"]: r for r in filas_}


def _canales(skus: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Publicaciones por canal. Es la foto del sync de 15 min, y se usa solo
    para SITUAR (qué canales tocan el SKU), nunca para decidir nada."""
    if not skus:
        return {}
    try:
        filas_ = sdb.fetch_all(
            "select sku::text as sku, canal, listing_id, status, situacion, "
            "       stock_own, stock_full, stock_fba, is_fulfillment, "
            "       logistic_type, date_published "
            "from channel.listings where sku::text = any(%s) "
            "order by canal, listing_id", (skus,))
    except Exception as exc:  # noqa: BLE001
        log.warning("inventario: channel.listings falló: %s", exc)
        return {}
    salida: dict[str, list[dict[str, Any]]] = {}
    for r in filas_:
        salida.setdefault(r["sku"], []).append(r)
    return salida


def _ultimo_proceso(skus: list[str]) -> dict[str, dict[str, Any]]:
    """Último paso registrado en `ops.process_log` — la única bitácora del panel
    que guarda ACTOR. Cubre poco (173 de 7,284 padres) pero cuando hay dato dice
    quién tocó el SKU y en qué paso se quedó, incluido el renglón final del
    pipeline de Crear, que literalmente dice qué falta ('… → PENDING (falta:
    precio)')."""
    if not skus:
        return {}
    try:
        filas_ = sdb.fetch_all(
            "select distinct on (sku) sku::text as sku, proceso, accion, estado, "
            "       actor, created_at "
            "from ops.process_log where sku::text = any(%s) "
            "order by sku, created_at desc", (skus,))
    except Exception as exc:  # noqa: BLE001
        log.warning("inventario: process_log falló: %s", exc)
        return {}
    return {r["sku"]: r for r in filas_}


# ─────────────────────────────────────────────────────────────────────────────
# UTILERÍAS
# ─────────────────────────────────────────────────────────────────────────────

# Un contenedor ISO son 4 letras (código de propietario + U) y 7 dígitos:
# UETU7935912, BEAU6268641, MRKU3085279. Cualquier otro código alfanumérico del
# campo es una referencia de booking del transitario, NO un contenedor.
_ISO = re.compile(r"\b([A-Z]{4}\d{7})\b")
# El número de EMBARQUE: 'cont 103', 'contenedor 3', ' - 97'. Es la llave
# estable — el mismo embarque puede traer varios contenedores.
_EMBARQUE = re.compile(r"(?:CONTENEDOR|CONT\.?|[-–])\s*(\d{1,4})\b")


def _empaque(crudo: Any) -> dict[str, str]:
    """
    Desarma el campo de contenedor en sus tres piezas: ``{iso, embarque, crudo}``.

    Existe porque las dos fuentes guardan cosas DISTINTAS y comparar los textos
    daba 'discrepan' donde no había discrepancia. Medido en el piloto:

        Odoo   'SZLS50214500 cont 103'      → booking SZLS50214500, embarque 103
        kubera 'UETU7935912 - 103'          → contenedor  UETU7935912, embarque 103

    Es el MISMO embarque. Odoo guarda la referencia del transitario y kubera el
    código ISO del contenedor: son complementarios, no contradictorios. Por eso
    la comparación se hace por EMBARQUE, que es lo que la memoria del proyecto
    ya decía que era la llave estable, y no por el código.

    El campo de Odoo es texto libre y está sucio de verdad (NBSP incluidos):
    201 de sus 350 valores distintos no caen en ningún patrón limpio. Lo que no
    se reconoce se devuelve crudo, recortado — nunca se inventa.
    """
    if not crudo:
        return {"iso": "", "embarque": "", "crudo": ""}
    texto = str(crudo).replace("\xa0", " ").strip().upper()
    iso = _ISO.search(texto)
    emb = _EMBARQUE.search(texto)
    return {"iso": iso.group(1) if iso else "",
            "embarque": emb.group(1) if emb else "",
            "crudo": texto[:60]}


def _piezas_declaradas(c: dict | None) -> float | None:
    """Cajas × piezas por caja: lo que el packing list dice que debería llegar.

    Es la cifra contra la que se cuenta al recibir. Ojo con el divisor: hay
    1,786 SKUs con `cajas = 0` y 29 con `piezas_por_caja` entre 0 y 1, y un
    factor menor que uno multiplica el flete en vez de repartirlo. Por eso se
    devuelve None en vez de un número absurdo.
    """
    if not c:
        return None
    cajas, por_caja = _num(c.get("cajas")), _num(c.get("piezas_por_caja"))
    if not cajas or not por_caja or por_caja < 1:
        return None
    return round(cajas * por_caja, 2)


def _descuadre(w: dict | None, o: dict | None) -> int | None:
    """Woo menos Odoo. Es la columna 'Woo ↔ físico' del diseño."""
    if not w or not o or w.get("stock") is None:
        return None
    return int(round(float(w["stock"]) - float(o.get("libre") or 0)))


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso(v: Any) -> str:
    if not v:
        return ""
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def _fecha(v: Any) -> datetime | None:
    """Fecha de Odoo (`'2026-08-26 17:44:51'`) o ISO, siempre en UTC."""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _dias(v: Any) -> int | None:
    """Días transcurridos desde una fecha de Odoo/MySQL, que llegan sin zona."""
    if not v:
        return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00").replace(" ", "T"))
        except ValueError:
            return None
    if not isinstance(v, datetime):
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - v).days)
