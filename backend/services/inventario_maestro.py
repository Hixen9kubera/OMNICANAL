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

ODOO MANDA (Brandon, 4-sep-2026)
--------------------------------
Título, foto, contenedor, cajas, piezas por caja, ubicación y variantes salen de
ODOO. WooCommerce entra en UN solo lugar —el descuadre— porque comparar necesita
las dos cifras por definición. Su `post_status` está **prohibido** como señal de
etapa: la escalera editorial de la tienda no dice nada del estado de la
mercancía. Y las cajas nunca salen del packing list: ése dice lo que el
proveedor EMBARCÓ, y aquí se muestra lo que HAY.

VALIDADO BODEGA · LOS CUATRO REQUISITOS (Brandon, 7-sep-2026)
--------------------------------------------------------------
Reemplazó a las cinco etapas viejas. **Un producto no está validado por bodega
si le falta UNO SOLO de los cuatro.** No es un semáforo de avance: es una
compuerta.

  1. UBICACIÓN · tenerla significa que le dieron entrada y existe. Es el punto
     que más pesa: de 1,163,459 piezas del inventario, **930,732 (el 80%) están
     en zonas de paso** y solo 221,644 en un rack designado. Estar en zona de
     paso SÍ cumple —entró—, pero se dice en el detalle.
  2. STOCK     · «a la mano» (`qty_available`) y «disponible» (`free_qty`). Con
     piezas a la mano y CERO disponibles el punto NO se cumple: está todo
     reservado y no hay nada que vender.
  3. FOTO      · la foto que manda bodega, y aplica SOLO a productos CON
     variantes: ahí una imagen genérica no distingue cuál es cuál. Un producto
     simple queda validado con la foto que ya tiene en Odoo.
  4. SPECS     · la matriz por categoría. **En espera permanente por ahora**: no
     se ha decidido el formato del Excel ni cómo llegará la información.

Los estados son `listo` · `falta` · `espera` · `na`, y la diferencia entre
`falta` y `espera` no es cosmética: `falta` culpa al producto, `espera` dice que
el sistema todavía no tiene por dónde recibir el dato (bodega manda las fotos
por Slack; el Excel de specs no existe). Mientras specs siga sin definirse,
NINGÚN producto puede quedar validado del todo — que es exactamente lo pedido.

LO QUE NO ES VALIDACIÓN DE BODEGA (`_comercial`)
------------------------------------------------
El candado `revisado_at` de la pestaña Costos y el envío a FULL (ML) / FBA
(Amazon) / WFS (Walmart) siguen mostrándose, pero APARTE: contestan preguntas
del área comercial, no de almacén, y meterlos entre los cuatro requisitos haría
que un costo sin validar pareciera un problema de bodega.

LA FILA ES EL SKU
-----------------
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
2. **Las cajas no salen del packing list.** Ése dice lo que el proveedor
   EMBARCÓ; aquí se muestra lo que HAY, derivado del físico de Odoo.
3. **El contenedor de Odoo y el de `costos_validados` discrepan en el 37%** de
   los SKUs donde ambos existen, y no solo de formato. Se devuelven LOS DOS y
   se marca `contenedor_discrepa` — pintar uno solo sería inventar.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from services import odoo, packing_cajas, supabase_db as sdb, wp_db

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

# CUATRO SKUs de REFERENCIA. Los diez del piloto no han llegado —cero piezas y
# cero movimientos—, así que con ellos solos la trazabilidad se ve vacía y no
# se entiende para qué sirve. Éstos se eligieron midiendo VARIEDAD, no volumen:
# entre los cuatro aparecen las siete causas (entrada, venta, envío a FULL,
# devolución, ajuste, traspaso y merma), LOS TRES ALMACENES, y los tres estados
# de la etapa «en proceso»:
#
#   MUE-0135-NEG  162 movs · recibido pero SIN RACK (ámbar) · TEXCO
#   TEC-0008-AMR  108 movs · acomodado en J-28-N1 (verde)   · TEXCO
#   TEC-0370-NEG  285 movs · acomodado en L-17-N3 (verde)   · DROP OFF, con traspaso
#   ORG-0863-ROS  50,000 pzas · en la RAÍZ del almacén       · TEXCO II
#
# El cuarto se sumó el 9-sep y NO es decorativo: hasta ese día el comentario de
# arriba decía «los tres almacenes» y era FALSO — medido, los tres primeros solo
# tocan TEXCO y DROP OFF. Como el filtro «Bodega» se llena con las bodegas que
# de verdad aparecen en las filas, TEXCO II era IMPOSIBLE de ofrecer, y TEXCO II
# es el 40% del inventario: 1,104 SKUs y 466,147 piezas. Sin este SKU la
# pantalla afirmaba tener tres almacenes enseñando dos.
#
# Los tres CUADRAN contra Odoo, así que también sirven para comprobar que el
# saldo del libro reproduce el que el panel publica.
REFERENCIA: tuple[str, ...] = ("MUE-0135-NEG", "TEC-0008-AMR", "TEC-0370-NEG",
                              "ORG-0863-ROS")

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
    pedidos = [s.strip() for s in (skus or list(PILOTO) + list(REFERENCIA))
               if s and s.strip()]
    if not pedidos:
        return []

    od = odoo.detalle_por_sku(pedidos)
    ubis = odoo.ubicaciones_por_sku(pedidos)
    hermanos = odoo.variantes_por_sku(pedidos)
    imgs = odoo.miniaturas_por_sku(pedidos)
    costos = _costos(pedidos)
    canales = _canales(pedidos)
    proceso = _ultimo_proceso(pedidos)
    # WooCommerce ya NO manda en nada de esta pestaña (decisión de Brandon,
    # 4-sep): se lee solo para el DESCUADRE, que por definición necesita las dos
    # cifras. Su `post_status` está prohibido como señal de etapa.
    woo = wp_db.maestro_por_sku(pedidos)
    # Las CAJAS leídas del renglón del packing list. NO bloquea: devuelve lo que
    # tenga en caché y calienta el resto en segundo plano, porque parsear los
    # xlsx cuesta 45 s (llevan una foto incrustada por renglón). La primera
    # carga tras un arranque en frío sale con la cifra congelada de
    # `costos_validados`; la siguiente ya trae la del renglón.
    pls = packing_cajas.por_sku(pedidos)
    recs = odoo.recibido_por_sku(pedidos)

    salida = []
    for sku in pedidos:
        salida.append(_fila(sku, woo.get(sku), od.get(sku), costos.get(sku),
                            canales.get(sku, []), proceso.get(sku),
                            imgs.get(sku), ubis.get(sku, []),
                            hermanos.get(sku, []), pls.get(sku), recs.get(sku)))
    return salida


def _fila(sku: str, w: dict | None, o: dict | None, c: dict | None,
          pubs: list[dict], plog: dict | None, imagen: str | None,
          ubicaciones: list[dict], hermanos: list[dict],
          pl: dict | None = None, rec: dict | None = None) -> dict[str, Any]:
    es_padre = bool(w and w["n_hijas"] > 0)

    emp_odoo = _empaque((o or {}).get("contenedor"))
    emp_costo = _empaque((c or {}).get("contenedor"))
    # El código ISO solo lo tiene kubera; el embarque, los dos. Se prefiere el
    # ISO para mostrar y el embarque para comparar. Cuando no hay ISO por
    # ningún lado se muestra la referencia del transitario CRUDA y marcada
    # (`contenedor_es_booking`): dejar la celda vacía haría creer que no se
    # sabe de dónde vino la mercancía, cuando sí se sabe a medias.
    # ODOO MANDA, `costos_validados` es el respaldo (Brandon, 4-sep), y la
    # pestaña DICE de cuál de los dos salió: sin eso, un número de contenedor es
    # un número sin dueño, y las dos fuentes no siempre coinciden.
    if emp_odoo["crudo"]:
        contenedor = emp_odoo["iso"] or emp_odoo["crudo"]
        embarque = emp_odoo["embarque"] or emp_costo["embarque"]
        es_booking = not emp_odoo["iso"]
        fuente = "odoo"
    elif emp_costo["crudo"]:
        contenedor = emp_costo["iso"] or emp_costo["crudo"]
        embarque = emp_costo["embarque"]
        es_booking = not emp_costo["iso"]
        fuente = "costos_validados"
    else:
        contenedor, embarque, es_booking, fuente = "", "", False, ""

    # El cotejo de contenedor tiene TRES resultados, no dos, y confundirlos hacía
    # que la pestaña se callara justo cuando no sabía. Caso que lo destapó,
    # ROP-0731-BLN: Odoo guarda 'SZLS50213900' (una referencia de booking, sin
    # número de embarque) y kubera 'BEAU6268641 - 97' (contenedor ISO, embarque
    # 97). Como Odoo no trae embarque, no hay nada que comparar — y la bandera
    # de discrepancia quedaba en False, que se lee como "concuerdan".
    #   discrepa      → los dos traen embarque y NO coinciden
    #   no_comparable → las dos fuentes tienen dato pero una no trae embarque
    #   (nada)        → coinciden, o solo hay una fuente
    discrepa = bool(emp_odoo["embarque"] and emp_costo["embarque"]
                    and emp_odoo["embarque"] != emp_costo["embarque"])
    no_comparable = bool(not discrepa and emp_odoo["crudo"] and emp_costo["crudo"]
                         and not (emp_odoo["embarque"] and emp_costo["embarque"]))

    full = sum(float(p.get("stock_full") or 0) for p in pubs)
    fba = sum(float(p.get("stock_fba") or 0) for p in pubs)

    fila = {
        "sku": sku,
        # Marca los tres ejemplos con movimiento real, para que no se confundan
        # con los diez SKUs que Brandon puso a prueba.
        "es_referencia": sku in REFERENCIA,
        "existe_en_woo": w is not None,
        "existe_en_odoo": o is not None,
        # El nombre y la foto salen de ODOO: es el registro exacto del producto
        # (Brandon, 4-sep). WooCommerce solo entra si Odoo no tiene la ficha.
        "nombre": (o or {}).get("nombre") or (w or {}).get("titulo") or "",
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

        # VARIANTES SEGÚN ODOO: los SKUs que comparten `product_tmpl_id`.
        # No se deduce del texto del código — `JUGU-1153-MET` y
        # `JUGU-1153-MET-B` parecen hermanos y viven en plantillas distintas.
        "variantes_odoo": hermanos,
        "n_variantes_odoo": len(hermanos),
        "odoo_tmpl_id": (o or {}).get("tmpl_id"),
        "odoo_creado": _iso((o or {}).get("creado")),
        "odoo_modificado": _iso((o or {}).get("modificado")),
        "odoo_categoria": (o or {}).get("categoria") or "",

        # empaque
        "contenedor": contenedor,
        "contenedor_fuente": fuente,
        "contenedor_es_booking": es_booking,
        "embarque": embarque,
        "contenedor_odoo": emp_odoo["crudo"],
        "contenedor_costo": emp_costo["crudo"],
        "contenedor_discrepa": discrepa,
        "contenedor_no_comparable": no_comparable,
        # LAS PIEZAS QUE MANDAN SON LAS LIBRES ("free to use"), no el "on hand"
        # (Brandon, 8-sep): el on hand es metrica de trackeo. Antes de esa
        # fecha Piezas usaba `libre` y Cajas usaba `fisico`, o sea que las dos
        # celdas de UNA MISMA FILA salian de bases distintas. Se veia: en
        # MUE-0135-NEG (fisico 1, libre 0, factor 3) la tabla decia Piezas 0 y
        # Cajas 0.33 — ni cero ni un tercio de caja describian nada.
        #
        # LAS CAJAS DE ODOO SIGUEN SIENDO DERIVADAS, NUNCA DEL PACKING LIST
        # (Brandon, 4-sep): "es inventario existente en fisico de odoo". Odoo
        # no tiene un contador de cajas y esta MEDIDO, no supuesto:
        # product.packaging tiene 0 registros en toda la base, stock.quant.package
        # tiene 3 en 36,256 quants, y 0 de 1,264 recepciones validadas traen
        # bultos. Lo unico vivo es el factor `units_per_master_box`.
        "piezas_por_caja": _num((o or {}).get("piezas_por_caja")),
        "cajas": _cajas((o or {}).get("libre"), (o or {}).get("piezas_por_caja")),
        "cajas_por_llegar": _cajas((o or {}).get("entrante"),
                                   (o or {}).get("piezas_por_caja")),
        "cbm_caja": _num((o or {}).get("cbm_caja")),
        "cotejo_cajas": _cotejo_cajas(o, c, pl),
        "recorrido": _recorrido(o, pl, rec),

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
        "recepcion_docs": (o or {}).get("recepcion_docs") or 0,

        # dónde está — a nivel de rack, y es dato que SOLO existe en Odoo
        "ubicaciones": ubicaciones,
        "bodegas": sorted({u["bodega"] for u in ubicaciones if u["vendible"]}),
        "rack": ubicaciones[0]["rack"] if ubicaciones else "",
        "bodega": ubicaciones[0]["bodega"] if ubicaciones else "",
        "n_ubicaciones": len(ubicaciones),
        "piezas_en_rack": sum(u["piezas"] for u in ubicaciones
                              if u["vendible"] and not u["es_stage"]) or None,
        "piezas_en_stage": sum(u["piezas"] for u in ubicaciones
                               if u["vendible"] and u["es_stage"]) or None,
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
    fila["validacion_bodega"] = _validacion_bodega(fila, c)
    fila["comercial"] = _comercial(fila, c, pubs)
    # El último paso registrado en el panel: no valida nada, solo dice quién
    # tocó el SKU por última vez.
    fila["ultimo_paso"] = ({
        "accion": plog.get("accion"), "actor": plog.get("actor"),
        "fecha": _iso(plog.get("created_at")),
    } if plog else None)
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

    # «Completos» pasa a significar VALIDADO BODEGA: los cuatro requisitos.
    completos = sum(1 for f in filas_ if f["validacion_bodega"]["validado"])

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
            "activo_sin_stock": sum(
                1 for f in filas_ if f["cuadre"]["etiqueta"] == "Activo sin stock"),
            "descuadre": sum(1 for f in filas_ if f["descuadre"]),
            "recepcion_vencida": sum(1 for f in filas_
                                     if (f["recepcion_dias"] or 0) > 30),
            "sin_ubicacion": sum(1 for f in filas_ if not f["n_ubicaciones"]),
            "sin_fotos": sum(1 for f in filas_ if _punto(f, "foto") == "falta"),
            "sin_costo": sum(1 for f in filas_
                             if f["comercial"]["validado"]["estado"] == "pendiente"),
            "contenedor_discrepa": sum(1 for f in filas_ if f["contenedor_discrepa"]),
            "contenedor_no_comparable": sum(
                1 for f in filas_ if f["contenedor_no_comparable"]),
            "odoo_duplicado": sum(1 for f in filas_ if f["odoo_duplicado"]),
        },
        # Cuántos SKUs cumplen cada uno de los cuatro requisitos de bodega.
        "por_punto": {
            clave: {est: sum(1 for f in filas_ if _punto(f, clave) == est)
                    for est in ("listo", "falta", "espera", "na")}
            for clave in ("ubicacion", "stock", "foto", "specs")
        },
    }


def _punto(fila: dict[str, Any], clave: str) -> str:
    """El estado de uno de los cuatro requisitos de bodega, por su clave."""
    for p in fila["validacion_bodega"]["puntos"]:
        if p["clave"] == clave:
            return p["estado"]
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# LAS CINCO ETAPAS
# ─────────────────────────────────────────────────────────────────────────────

def _validacion_bodega(fila: dict, c: dict | None) -> dict[str, Any]:
    """
    VALIDADO BODEGA: los CUATRO requisitos que definió Brandon el 7-sep-2026.
    Un producto no está validado si le falta uno solo.

      1. UBICACIÓN · tenerla significa que le dieron entrada y existe.
      2. STOCK     · «a la mano» (`qty_available`) y «disponible» (`free_qty`).
      3. FOTO      · la que manda bodega, y SOLO aplica a productos CON
                     variantes. Un producto simple con su foto en Odoo ya
                     cuenta como válido.
      4. SPECS     · la matriz por categoría. EN ESPERA: no se ha decidido el
                     formato del Excel ni cómo llegará la información.

    Estados: `listo` · `falta` · `na` · `espera`.

    Los puntos 3 y 4 dependen de canales que HOY NO EXISTEN —bodega manda la
    foto por Slack, y el Excel de specs no tiene formato— así que se marcan
    `espera` y no `falta`. La diferencia no es cosmética: `falta` culpa al
    producto, `espera` dice que el sistema todavía no tiene por dónde recibirlo.
    """
    puntos: list[dict[str, Any]] = []

    # ── 1 · UBICACIÓN ───────────────────────────────────────────────────────
    if fila["n_ubicaciones"]:
        # Tener ubicación ya cumple: significa que entró. Que sea zona de paso
        # en vez de rack se dice como detalle, no invalida — pero importa,
        # porque el 80% del inventario vive así.
        rack = fila["rack"] or ""
        detalle = f"{rack} · {fila['bodega']}" if rack else fila["bodega"]
        if fila["piezas_en_stage"] and not fila["piezas_en_rack"]:
            detalle += " — en zona de paso, sin rack asignado"
        elif fila["n_ubicaciones"] > 1:
            detalle += f" y {fila['n_ubicaciones'] - 1} ubicación(es) más"
        puntos.append(_pto("ubicacion", "Ubicación", "listo",
                           "con ubicación", detalle, "odoo stock.quant"))
    else:
        pendiente = ""
        if fila["recepcion_piezas"]:
            pendiente = (f"{fila['recepcion_piezas']:.0f} pzas en recepción "
                         f"abierta sin validar")
        puntos.append(_pto("ubicacion", "Ubicación", "falta",
                           "sin entrada",
                           pendiente or "no se ha recibido en almacén",
                           "odoo stock.quant"))

    # ── 2 · STOCK ───────────────────────────────────────────────────────────
    # Odoo separa dos cifras y las dos importan: «a la mano» es lo que está
    # físicamente, «disponible» es lo que queda libre después de reservas.
    mano = fila["stock_fisico"] or 0
    disp = fila["stock_odoo"] or 0
    if mano > 0 and disp > 0:
        reservadas = mano - disp
        puntos.append(_pto(
            "stock", "Stock", "listo", f"{disp:.0f} disponibles",
            f"{reservadas:.0f} comprometidas en pedidos" if reservadas > 0
            else "el físico completo está libre de reservas",
            "odoo qty_available / free_qty", mano=mano, disponible=disp))
    elif mano > 0:
        # Hay mercancía pero no se puede vender: el punto NO se cumple.
        puntos.append(_pto(
            "stock", "Stock", "falta", "todo comprometido",
            "existe físicamente, pero está entero en pedidos o reservas",
            "odoo qty_available / free_qty", mano=mano, disponible=disp))
    else:
        pendiente = ""
        if fila["recepcion_piezas"]:
            pendiente = (f" — {fila['recepcion_piezas']:.0f} pzas esperan "
                         f"en recepción sin validar")
        puntos.append(_pto(
            "stock", "Stock", "falta", "sin stock",
            "ni a la mano ni disponible" + pendiente,
            "odoo qty_available / free_qty", mano=mano, disponible=disp))

    # ── 3 · FOTO ────────────────────────────────────────────────────────────
    # LA REGLA CONDICIONAL, y es la más fina de las cuatro: la foto que manda
    # bodega aplica SOLO a productos con variantes, porque ahí una foto genérica
    # no distingue cuál es cuál. Un producto simple queda validado con la foto
    # que ya tiene en Odoo.
    con_variantes = fila["n_variantes_odoo"] > 0
    hay_foto = bool(fila["imagen"])
    if not con_variantes:
        if hay_foto:
            puntos.append(_pto("foto", "Foto", "listo", "con foto",
                               "producto simple: basta la imagen de Odoo",
                               "odoo image_256"))
        else:
            puntos.append(_pto("foto", "Foto", "falta", "sin foto",
                               "producto simple sin imagen en Odoo",
                               "odoo image_256"))
    elif hay_foto:
        hermanos = ", ".join(h["sku"] for h in fila["variantes_odoo"][:3])
        puntos.append(_pto(
            "foto", "Foto", "espera", "espera foto de bodega",
            f"tiene {fila['n_variantes_odoo'] + 1} variantes ({hermanos}): la "
            f"imagen de Odoo no distingue cuál es cuál",
            "bodega (Slack) — canal no construido"))
    else:
        puntos.append(_pto("foto", "Foto", "na", "N/A · sin imagen",
                           "es variante y no tiene foto ni en Odoo ni de bodega",
                           "bodega (Slack) — canal no construido"))

    # ── 4 · SPECS ───────────────────────────────────────────────────────────
    # Siempre en espera: bodega mandará una matriz por categoría, pero el
    # formato del Excel y la vía de entrega están sin decidir (Brandon, 7-sep).
    # Mientras eso no exista, NINGÚN producto puede quedar validado del todo —
    # que es exactamente lo que se pidió.
    puntos.append(_pto("specs", "Specs", "espera", "en espera",
                       "matriz por categoría: falta definir el formato del "
                       "Excel y cómo llega",
                       "pendiente de definición"))

    cumplidos = sum(1 for p in puntos if p["estado"] == "listo")
    return {
        "puntos": puntos,
        "cumplidos": cumplidos,
        "total": len(puntos),
        "validado": cumplidos == len(puntos),
        "faltantes": [p["titulo"] for p in puntos if p["estado"] != "listo"],
    }


def _comercial(fila: dict, c: dict | None,
               pubs: list[dict]) -> dict[str, dict[str, Any]]:
    """Lo que NO es validación de bodega pero sigue haciendo falta ver: el
    candado de costo de la pestaña Costos, y si el SKU se mandó a la bodega de
    algún marketplace. Se separan a propósito de los cuatro puntos: contestan
    preguntas del área comercial, no de almacén."""
    e: dict[str, dict[str, Any]] = {}

    if not c:
        e["validado"] = _et("pendiente", "sin costo",
                            "no tiene renglón en costos_validados",
                            "costing.costos_validados")
    elif c.get("revisado_at"):
        e["validado"] = _et("listo", "validado",
                            f"{_iso(c['revisado_at'])[:10]} por "
                            f"{c.get('revisado_por') or '—'}",
                            "costing.costos_validados.revisado_at")
    else:
        e["validado"] = _et("parcial", "costo sin validar",
                            "tiene costo pero nadie pasó el candado en Costos",
                            "costing.costos_validados.revisado_at")

    def _destino(nombre: str, piezas: float | None) -> str:
        return f"{nombre} {piezas:.0f}" if piezas else nombre

    destinos: list[str] = []
    if (fila["stock_full"] or 0) > 0 or any(
            p.get("is_fulfillment") and p["canal"] == "mercado_libre" for p in pubs):
        destinos.append(_destino("FULL", fila["stock_full"]))
    if (fila["stock_fba"] or 0) > 0 or any(
            p.get("is_fulfillment") and p["canal"] == "amazon" for p in pubs):
        destinos.append(_destino("FBA", fila["stock_fba"]))
    if any(p.get("is_fulfillment") and p["canal"] == "walmart" for p in pubs):
        destinos.append("WFS")
    if destinos:
        e["enviado_full"] = _et("listo", " · ".join(destinos),
                                "en bodega del marketplace",
                                "channel.listings.is_fulfillment")
    else:
        e["enviado_full"] = _et(
            "pendiente", "no enviado",
            "hay stock en bodega propia y no se ha mandado"
            if (fila["stock_odoo"] or 0) > 0 else "sin stock que enviar",
            "channel.listings.is_fulfillment")
    return e


def _pto(clave: str, titulo: str, estado: str, etiqueta: str, detalle: str,
         fuente: str, **extra) -> dict[str, Any]:
    return {"clave": clave, "titulo": titulo, "estado": estado,
            "etiqueta": etiqueta, "detalle": detalle, "fuente": fuente, **extra}


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
    # Las recepciones ABIERTAS no son movimientos —nada se movió— pero sin ellas
    # un SKU que aún no llega enseña un historial vacío teniendo cientos de
    # piezas prometidas. Entran como UNA FILA POR DOCUMENTO y con delta 0.
    pendientes = [_pendiente(p) for p in odoo.recepciones_pendientes_por_sku(sku)]
    # Y el histórico de COMPRA: qué se pidió alguna vez y cuánto llegó. Es la
    # otra mitad de la pregunta — sin esto, un SKU cuyas órdenes ya se
    # recibieron enteras no tiene forma de enseñarlo.
    compras = [_compra(c) for c in odoo.ordenes_compra_por_sku(sku)]

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

    # Van ARRIBA y fuera del corte por causa: son el contexto de por qué el
    # historial de abajo está vacío o corto. El filtro sí las respeta cuando se
    # pide una causa concreta distinta de «entrada».
    muestra_pend = (not causa or causa in ("reales", "todo", "entrada"))
    return {
        "sku": sku,
        "movimientos": [_mov(m) for m in visibles[:limite]],
        "pendientes": pendientes if muestra_pend else [],
        "compras": compras if muestra_pend else [],
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


def _pendiente(p: dict[str, Any]) -> dict[str, Any]:
    """Una recepción abierta, lista para pintarse junto al historial.

    Trae las DOS fechas porque no dicen lo mismo y la diferencia importa:
    `TEXCO/IN/01208` se creó el 28-ago con fecha programada del 26-may. Decir
    «103 días vencida» describe mal un documento que tiene diez días de vida, y
    decir «creado hace 10 días» esconde que promete mercancía de mayo. Se dan
    las dos y que quien lo lea saque su conclusión.
    """
    return {
        "documento": p["documento"],
        "piezas": p["piezas"],
        "renglones": p["renglones"],
        "creado": _iso(p.get("creado")),
        "creado_dias": _dias(p.get("creado")),
        "creado_por": p.get("creado_por") or "",
        "programado": _iso(p.get("programado")),
        "programado_dias": _dias(p.get("programado")),
        "socio": p.get("socio") or "",
        "orden_compra": p.get("orden_compra") or "",
        "destino": p.get("destino") or "",
        "estado": p.get("estado") or "",
        # ¿La ORDEN tuvo recepción parcial, y entró ESTE SKU en ella? Son dos
        # preguntas distintas y hay que contestarlas por separado: P03364 sí
        # recibió parcial (TEXCO/IN/00419 el 28-ago) y de JUGU-1153-MET no entró
        # ni una pieza. El encabezado avanzó; el renglón no.
        "oc_recepciones": p.get("oc_recepciones") or 0,
        "oc_validadas": p.get("oc_validadas") or 0,
        "oc_parcial": bool(p.get("oc_parcial")),
        "oc_docs_validados": [
            {"documento": d.get("documento") or "", "validado": _iso(d.get("validado"))}
            for d in (p.get("oc_docs_validados") or [])
        ],
        "sku_pedido": p.get("sku_pedido"),
        "sku_recibido": p.get("sku_recibido"),
        "sku_en_parcial": bool(p.get("sku_en_parcial")),
    }


def _compra(c: dict[str, Any]) -> dict[str, Any]:
    """Una orden de compra del SKU, lista para pintarse.

    `veredicto` tiene cuatro valores porque los cuatro pasan de verdad:
    `completa`, `parcial`, `nada` y `sobre` — `TEC-0008-AMR` recibió 201 de 200
    pedidas. Redondear eso a «completa» escondería una sobre-recepción, que es
    justo el tipo de descuadre que alguien tendría que revisar.
    """
    return {
        "orden": c["orden"],
        "estado": c.get("estado") or "",
        "fecha": _iso(c.get("fecha")),
        "dias": _dias(c.get("fecha")),
        "proveedor": c.get("proveedor") or "",
        "pedido": c.get("pedido") or 0,
        "recibido": c.get("recibido") or 0,
        "faltante": c.get("faltante") or 0,
        "renglones": c.get("renglones") or 0,
        "veredicto": c.get("veredicto") or "nada",
        "recepciones": c.get("recepciones") or 0,
        "recepciones_validadas": c.get("recepciones_validadas") or 0,
        "documentos": [
            {"documento": d.get("documento") or "", "estado": d.get("estado") or "",
             "validado": _iso(d.get("validado"))}
            for d in (c.get("documentos") or [])
        ],
    }


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

    Medido el 8-sep-2026 sobre las 15,849 filas de la tabla: `cajas` viene de
    DOS cargas masivas (21-may: 11,806 filas, y 3-jun: 3,537) y todo lo creado
    después nace en NULL — las 401 filas del 13-ago traen cero, y también 4 de
    los 13 SKUs piloto, dados de alta el 4-sep. Llenas 15,343 (96.8%), pero
    1,786 valen 0, así que ÚTILES son 13,557 (85.5%). Por eso `cajas` se usa
    como COTEJO y jamás como la cifra principal: es la foto del embarque.
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


def _recorrido(o: dict | None, pl: dict | None,
               rec: dict | None) -> dict[str, Any]:
    """
    LAS TRES CIFRAS DE UNA PIEZA (Brandon, 9-sep): «cuántas debieron llegar
    según el packing list, cuántas llegaron realmente y cuántas hay disponibles».

      · DEBIÓ LLEGAR · las piezas de los renglones del packing list.
      · LLEGÓ        · entradas VALIDADAS en Odoo. No es «lo que hay».
      · DISPONIBLE   · `free_qty` de hoy, lo vendible.

    POR QUÉ NO SE RESTAN, y es lo que más importa de este bloque: la diferencia
    entre lo recibido y lo disponible **casi nunca es una merma, son ventas**.
    `TEC-0370-NEG` recibió 168 piezas en 8 documentos desde diciembre y hoy tiene
    8: perfectamente normal. Un panel que pinte «−160» ahí está acusando un
    faltante que no existe. Por eso aquí se dan las tres cifras rotuladas y la
    única resta que sí se hace es la del packing list contra lo recibido, que sí
    es «lo que falta por entrar».

    Y LA COBERTURA SE DICE, NO SE ESCONDE. El packing list solo cubre los
    renglones que se pudieron empatar. Medido el 9-sep en los 9 SKUs del piloto
    con renglón: seis cuadran EXACTO contra lo que Odoo pidió —lo que valida el
    método— pero tres se quedan cortos (`HERR-0343-MET` cubre el 60%,
    `JUGU-1153-MET` el 21%, `ROP-0731-BLN` el 19%). En esos, «debió llegar» es
    un piso, no el total, y así se rotula: decir que faltan piezas cuando lo que
    falta es el renglón sería inventar un descuadre.
    """
    debio = _num((pl or {}).get("piezas_fila"))
    llego = _num((rec or {}).get("piezas")) or 0.0
    pendiente = _num((o or {}).get("entrante")) or 0.0
    disponible = _num((o or {}).get("libre")) or 0.0
    mano = _num((o or {}).get("fisico")) or 0.0

    # Lo pedido a Odoo = lo ya recibido + lo que sigue en recepciones abiertas.
    # Sirve para medir si el packing list cubre el embarque completo.
    pedido = llego + pendiente
    cobertura = (round(debio / pedido, 4) if debio and pedido else None)
    completo = bool(cobertura is not None and abs(cobertura - 1) < 0.01)

    return {
        "debio_llegar": debio,
        "llego": llego,
        "documentos": (rec or {}).get("documentos") or 0,
        "primera_entrada": _iso((rec or {}).get("primera")),
        "ultima_entrada": _iso((rec or {}).get("ultima")),
        "pendiente": pendiente,
        "pedido_odoo": pedido or None,
        "disponible": disponible,
        "a_la_mano": mano,
        # Qué parte del embarque cubren los renglones que se pudieron empatar.
        # `None` = no hay renglón; 1.0 = cuadra exacto contra Odoo.
        "cobertura_pl": cobertura,
        "pl_completo": completo,
        # Lo vendido/salido: se NOMBRA, nunca se pinta como faltante.
        "salido": round(llego - mano, 2) if llego and llego > mano else None,
    }


def _cotejo_cajas(o: dict | None, c: dict | None,
                  pl: dict | None = None) -> dict[str, Any]:
    """
    Las TRES cajas de un SKU, que son tres preguntas distintas (Brandon, 8-sep).

      1. BODEGA       · cuantas cajas conto el almacen al recibir. MANDA sobre
                        las otras dos, y HOY NO EXISTE en ningun sistema.
      2. PACKING LIST · cuantas cajas dijo el proveedor que embarco.
      3. ODOO         · cuantas cajas llenarian las piezas LIBRES de hoy.

    Por que no se pueden restar entre si: la del packing list es una foto del
    EMBARQUE y la de Odoo es el PISO de hoy. TEC-0008-AMR dice 200 cajas en el
    packing list y tiene 5 piezas fisicas: no es un descuadre, es que ya se
    vendieron. Por eso esto es un COTEJO con tres numeros rotulados y no una
    resta con un veredicto.

    LA DEL PACKING LIST TIENE DOS ORÍGENES Y NO VALEN LO MISMO (Brandon, 9-sep).

      1. `renglon` — se ABRE el packing list y se lee la columna de cartones
         (箱数 / CTNS) del renglón exacto del SKU. Es el dato bueno. El renglón
         no se vuelve a buscar: lo dejó registrado en `costing.caja_compartida`
         la escalera de detección de imagen de la pestaña Costos (foto de Odoo
         → dHash → título → foto de ML + IA), y hasta hoy nadie leía esa tabla.
      2. `costos_validados` — la columna `cajas`, un CONGELADO de dos cargas
         masivas (21-may y 3-jun-2026). Útiles 13,557 de 15,849 (85.5%), y TODO
         lo creado después del 3-jun nace en NULL: por eso `ACC-0907-MET` salía
         con «PL —» teniendo su renglón perfectamente identificado.

    Cuando hay las dos y DIFIEREN se guardan las dos, porque la diferencia es la
    noticia. Medido el 9-sep en el piloto: coinciden en ELEC-0034-EST (59),
    OFI-0412-EST (22) y HERR-0146-EST (50), y discrepan en VEH-0148-EST (el
    renglón dice 7, el congelado 15) y ROP-0731-BLN (1 contra 16).

    OJO CON LA CAJA COMPARTIDA: cuando varios renglones comparten cartón, cada
    uno reporta el MISMO número de cartones. Sumarlos multiplica la caja. Eso lo
    resuelve `packing_cajas`, que nunca suma dentro de un grupo.
    """
    pc = pl or {}
    congelado = _num((c or {}).get("cajas"))
    if congelado is not None and congelado <= 0:
        congelado = None
    del_renglon = _num(pc.get("cajas"))
    pl = del_renglon if del_renglon is not None else congelado
    odoo_ = _cajas((o or {}).get("libre"), (o or {}).get("piezas_por_caja"))

    if pl is not None and odoo_ is not None:
        estado, nota = "cotejable", "embarque contra piso; no se restan"
    elif pl is not None:
        estado, nota = "solo_pl", "solo hay la del embarque: no queda piso libre"
    elif odoo_ is not None:
        estado, nota = "solo_odoo", "sin cajas en el packing list de kubera"
    else:
        estado, nota = "sin_dato", "ni packing list ni piso libre"

    return {
        # El que manda y el que falta son el mismo: ver la nota de arriba.
        "bodega": None,
        "packing_list": pl,
        # De dónde salió la cifra de arriba, para poder discutirla.
        "pl_fuente": ("renglon" if del_renglon is not None
                      else "costos_validados" if congelado is not None else None),
        "pl_archivo": pc.get("archivo"),
        "pl_renglones": pc.get("renglones") or None,
        "pl_compartida": bool(pc.get("compartida")),
        "pl_renglones_carton": pc.get("renglones_carton"),
        "pl_piezas": pc.get("piezas_fila"),
        # El congelado se conserva SOLO cuando discrepa del renglón: si coincide
        # es ruido, y si es el único que hay ya está arriba.
        "pl_congelado": (congelado if del_renglon is not None
                         and congelado is not None
                         and abs(congelado - del_renglon) > 0.01 else None),
        "piezas_por_caja_pl": _num((c or {}).get("piezas_por_caja")),
        "odoo": odoo_,
        "piezas_por_caja_odoo": _num((o or {}).get("piezas_por_caja")),
        "manda": "bodega",
        "estado": estado,
        "nota": nota,
    }


def _cajas(piezas: Any, por_caja: Any) -> float | None:
    """Cuántas cajas son esas piezas, según el factor de Odoo.

    Las cajas NO son un campo en Odoo: se derivan. Y se devuelve `None` —no
    cero— cuando el factor falta o es 1, por dos motivos distintos:
    `units_per_master_box` está poblado en el 75.3% del catálogo activo (9,926
    de 13,189), y de ésos **644 valen 1**, que no describe una caja sino la
    ausencia de dato. Un divisor inventado convierte una celda vacía en un
    número que alguien va a creer.
    """
    p, f = _num(piezas), _num(por_caja)
    if not p or not f or f < 1:
        return None
    return round(p / f, 2)


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
