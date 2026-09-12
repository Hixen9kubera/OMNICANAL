"""
pedidos_temu_sondeo.py — Las ventas de Temu entran por SONDEO de su propia API.

POR QUÉ EXISTE, Y POR QUÉ NO HABÍA NADA
───────────────────────────────────────
Temu no tenía NINGUNA vía de ingesta viva. Las dos que hubo se cerraron:

  · **M2E Cloud** — desinstalado y prohibido (Brandon, 1-sep-2026). Era el único
    camino que alguna vez creó un pedido de Temu: las 2 únicas ventas que vio la
    tubería, del 10 y 11 de agosto.
  · **El webhook propio** — existe y responde, pero darlo de alta depende de
    CUATRO trámites ajenos (compliance, aprobación de la URL, autorización del
    vendedor, carga de IPs). No es una bandera nuestra.

Mientras tanto Gabriela capturaba las ventas a mano en Odoo. El sondeo del
1-sep midió el hueco: **Temu declara 96 órdenes y la tubería vio 2**.

LO QUE DESTAPÓ EL SONDEO
────────────────────────
`bg.order.list.v2.get` responde desde producción, y devuelve **la misma forma**
que `bg.order.detail.v2.get` (`parentOrderMap` + `orderList[]` con
`productList[].extCode`). Por eso aquí NO se reimplanta el normalizador: se
reusa el de `pedidos_temu`, que ya sabe leer esa forma y ya resuelve el precio
de catálogo.

Además el listado trae `thumbUrl` (imagen) e `inventoryDeductionWarehouseId`
(de qué bodega descontó Temu) — dos cosas que se creían no disponibles.

LA GUÍA: EXISTE, PERO NO EN LA ORDEN (medido el 1-sep, y vale la pena guardarlo
porque la herramienta que lo descubrió ya se retiró)
──────────────────────────────────────────────────────────────────────────────
El número de rastreo NO viene dentro del pedido: se buscó en toda la respuesta,
en los dos vocabularios —`trackingNumber` y el chino `mailNo`/`waybill`— y no
aparece. Pero los endpoints de envío sí existen, y hay que leer los CÓDIGOS para
verlo, porque un "falla" a secas los confunde:

    bg.logistics.shipment.v2.get   120012016  "The parentOrder or Order is invalid"
    bg.order.shippinginfo.v2.get   180020003  "Invalid param"
    bg.logistics.shipment.get      3000037    "interface upgraded to higher version"
    bg.order.shippinginfo.get      3000004    "type has been sunset"
    bg.shipping.order.get          3000003    "type not exists"   ← este sí no existe

Los dos primeros contestan **"me faltan los parámetros"**, no "no existo": se
habían llamado con `{}`. O sea que la guía se obtiene con una SEGUNDA llamada
por orden, pasando `parentOrderSn` y `orderSn`. No está implementado todavía.

LO QUE TEMU NO DA, Y ESTÁ ASUMIDO
─────────────────────────────────
El **precio real cobrado**: `bg.order.amount.query` y `temu.order.amount.v2.query`
siguen en `3000032` ("ask for seller to authorize this api in seller center").
El pedido se crea con el precio de CATÁLOGO y así queda marcado. Dale de Brandon
el 1-sep: *"no importa si no tiene precio real"*.

LA MARCA DE AGUA, Y POR QUÉ IMPORTA MÁS QUE NADA AQUÍ
─────────────────────────────────────────────────────
Hay 96 órdenes históricas y **Gabriela ya capturó 98 a mano**. Un sondeo que
arranque sin marca las traería TODAS de golpe: 96 pedidos de Woo nuevos, 96
órdenes de Odoo duplicando las suyas, y el descuento de stock de mercancía que
salió hace semanas.

Por eso la marca sale del REGISTRO (`channel.orders`), igual que en Amazon, y
**cuando no hay nada registrado NO se va al principio de los tiempos: se queda
en AHORA**. Recuperar el histórico es una decisión aparte, deliberada, con su
propio parámetro — no algo que ocurra por el solo hecho de encender el job.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.pedidos_temu_sondeo")

CANAL = "temu"
CUENTA = "TEMU"
_MARGEN_MIN = 10          # se re-mira un poco hacia atrás por si algo cruzó justo
_PAGINA = 50

_ultimo: dict[str, Any] = {"estado": "sin_ejecutar"}


def estado() -> dict[str, Any]:
    return dict(_ultimo)


def _desde() -> datetime:
    """
    Desde cuándo mirar: SIEMPRE una ventana fija hacia atrás.

    ANTES SALÍA DE UNA MARCA DE AGUA Y ESO PERDÍA VENTAS. La marca era el último
    `actualizado_at` de `channel.orders` — o sea CUÁNDO REGISTRAMOS NOSOTROS, no
    cuándo se vendió. Cada pasada que registra algo la empuja a "ahora", así que
    la ventana se cerraba sola: en la corrida del 12-sep 00:54 el corte ya era
    `00:34` del mismo día y las 110 órdenes vistas salieron todas como "viejas".

    Y lo que queda fuera NO se reintenta, porque nunca se vio: la venta
    `PO-128-08267415736954067` (11-sep 13:13 CST) se perdió así — cayó fuera de
    la página que se leyó, y para cuando se leyeron más páginas la marca ya la
    había dejado atrás. Una marca que avanza con NUESTRO trabajo, y no con el
    del canal, convierte cualquier hueco en un hueco permanente.

    Ahora la ventana es `PEDIDOS_TEMU_SONDEO_MAX_DIAS` hacia atrás y basta con
    que una venta caiga ahí para que se recoja. Volver a ver lo mismo no cuesta
    nada: lo ya registrado se salta ANTES de tocar Woo (ver `revisar`).

    Sigue habiendo tope, y por lo mismo de siempre: Temu tiene ~96 órdenes
    históricas que Gabriela ya capturó a mano. El histórico se trae aparte, con
    `desde` explícito.
    """
    dias = int(getattr(settings, "pedidos_temu_sondeo_max_dias", 2) or 2)
    return datetime.now(timezone.utc) - timedelta(days=dias)


def _creada_en(orden: dict[str, Any]) -> datetime | None:
    """La fecha de creación del pedido, del renglón o del padre."""
    for fuente in ((orden.get("orderList") or [{}])[0], orden.get("parentOrderMap") or {}):
        for clave in ("orderCreateTime", "parentOrderTime", "createTime"):
            v = fuente.get(clave)
            if v:
                try:
                    return datetime.fromtimestamp(int(v), tz=timezone.utc)
                except (TypeError, ValueError, OSError):
                    continue
    return None


def _registrada(sn: str) -> bool:
    """¿Esta venta ya tiene pedido? ⚠️ BLOQUEA: va en `to_thread`.

    Falla ABIERTO: si el registro no contesta se devuelve False y `sincronizar`
    decide, que tiene su propio candado de idempotencia. Al revés —dar por
    registrada una venta que no lo está— la perdería.
    """
    try:
        from services import orders_write
        return orders_write.wc_order_id_previo(str(sn)) is not None
    except Exception as exc:  # noqa: BLE001
        log.warning("pedidos_temu_sondeo: no se pudo consultar el registro de %s: %s",
                    sn, str(exc)[:120])
        return False


async def _listar(pagina: int) -> list[dict[str, Any]]:
    from services import temu
    r = await temu.llamar("bg.order.list.v2.get",
                          {"pageNumber": pagina, "pageSize": _PAGINA})
    if not isinstance(r, dict):
        return []
    # El nombre de la lista no está documentado; se toma la primera lista de
    # diccionarios que traiga la respuesta. Es el mismo criterio defensivo que
    # usa `temu.listar_productos` con `goodsList`/`data`/`list`.
    for v in r.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


async def revisar(paginas: int | None = None, desde: datetime | None = None,
                  solo_registro: bool | None = None) -> dict[str, Any]:
    """
    Una pasada: órdenes nuevas de Temu → pedidos de Woo (y de ahí, el seam ya
    monta la orden de Odoo si `temu` está en `ODOO_VENTAS_CANALES`).

    `solo_registro=True` cuenta y clasifica **sin crear nada** — el modo con el
    que hay que mirarlo antes de encenderlo de verdad.

    Nunca lanza: la llama el scheduler.
    """
    if solo_registro is None:
        solo_registro = bool(getattr(settings, "pedidos_temu_sondeo_solo_registro", True))
    if paginas is None:
        paginas = int(getattr(settings, "pedidos_temu_sondeo_paginas", 3) or 3)
    corte = desde or _desde()
    from services import pedidos_temu, pedidos_ml

    vistas = nuevas = creadas = viejas = sin_sku = sin_mapear = 0
    ya_registradas = 0
    errores: list[str] = []
    # La venta MAS NUEVA descartada por vieja. Si se acerca a "ahora", la
    # ventana se esta quedando corta y hay que mirarlo: asi se vio que la lista
    # de Temu no viene ordenada por fecha.
    vieja_mas_nueva: datetime | None = None
    paginas_leidas = 0
    try:
        for pagina in range(1, max(1, paginas) + 1):
            lote = await _listar(pagina)
            if not lote:
                break
            paginas_leidas += 1
            for cruda in lote:
                vistas += 1
                padre = cruda.get("parentOrderMap") or {}
                sn = str(padre.get("parentOrderSn")
                         or (cruda.get("orderList") or [{}])[0].get("orderSn") or "")
                if not sn:
                    continue
                fecha = _creada_en(cruda)
                if fecha and fecha < corte:
                    viejas += 1
                    if vieja_mas_nueva is None or fecha > vieja_mas_nueva:
                        vieja_mas_nueva = fecha
                    continue
                nuevas += 1

                # ¿Ya la tenemos? Se pregunta al REGISTRO, que es una consulta
                # barata, en vez de dejar que `sincronizar` la "actualice": eso
                # escribiría en Woo cada 15 minutos por cada venta de la
                # ventana, sin que nada haya cambiado.
                previo = await asyncio.to_thread(
                    _registrada, sn)
                if previo:
                    ya_registradas += 1
                    continue

                orden = pedidos_temu._normalizar(sn, cruda)  # noqa: SLF001
                if not any(i["sku"] for i in orden["items"]):
                    sin_sku += 1
                    continue
                estado_num = orden.pop("_estado_num", None)
                try:
                    destino = pedidos_temu._ESTADOS_WC.get(int(estado_num))  # noqa: SLF001
                except (TypeError, ValueError):
                    destino = None
                if not destino:
                    # Mismo criterio que el webhook: un código que no conocemos
                    # NO crea pedido. Descontar stock por una venta que quizá se
                    # canceló cuesta dinero; no crearla solo cuesta reprocesar.
                    sin_mapear += 1
                    log.warning("TEMU sondeo: orden %s con orderStatus=%s SIN MAPEAR",
                                sn, estado_num)
                    continue
                if solo_registro:
                    creadas += 1     # lo que HABRÍA creado
                    continue
                r = await pedidos_ml.sincronizar(sn, forzar_estado=destino,
                                                 orden=orden, proteger_stock=False)
                if r.get("ok"):
                    creadas += 1
                else:
                    errores.append(f"{sn}: {str(r.get('motivo'))[:80]}")
        _ultimo.update(estado="ok", ts=datetime.now(timezone.utc).isoformat(),
                       vistas=vistas, nuevas=nuevas, creadas=creadas,
                       ya_registradas=ya_registradas,
                       viejas=viejas, sin_sku=sin_sku, sin_mapear=sin_mapear,
                       solo_registro=solo_registro, desde=corte.isoformat(),
                       paginas_leidas=paginas_leidas,
                       vieja_mas_nueva=(vieja_mas_nueva.isoformat()
                                        if vieja_mas_nueva else None),
                       errores=errores[:10])
        log.info("TEMU sondeo: %d vistas en %d pág · %d en ventana · %d ya estaban · "
                 "%d %s · %d fuera de ventana (la más nueva: %s) · %d sin SKU · "
                 "%d sin mapear · desde %s",
                 vistas, paginas_leidas, nuevas, ya_registradas, creadas,
                 "habría creado" if solo_registro else "creadas", viejas,
                 vieja_mas_nueva.strftime("%m-%d %H:%M") if vieja_mas_nueva else "-",
                 sin_sku, sin_mapear, corte.strftime("%m-%d %H:%M"))
    except Exception as exc:  # noqa: BLE001
        log.exception("pedidos_temu_sondeo.revisar falló")
        _ultimo.update(estado="error", ts=datetime.now(timezone.utc).isoformat(),
                       motivo=str(exc)[:300])
    return dict(_ultimo)
