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
    Desde cuándo mirar. Del REGISTRO, no del espejo (misma razón que Amazon:
    con `pedidos_ml` congelada la marca se quedaba fija).

    SIN REGISTRO PREVIO → AHORA, no el principio. Ver el encabezado: arrancar
    sin marca traería 96 órdenes históricas de una sentada.
    """
    ahora = datetime.now(timezone.utc)
    try:
        from services import orders_write
        m = orders_write.ultimo_actualizado(CUENTA)
    except Exception as exc:  # noqa: BLE001
        log.warning("pedidos_temu_sondeo: no se pudo leer la marca (%s); se usa AHORA", exc)
        m = None
    if not m:
        return ahora
    if m.tzinfo is None:
        m = m.replace(tzinfo=timezone.utc)
    marca = m - timedelta(minutes=_MARGEN_MIN)

    # TOPE DE RETROCESO. La marca de Temu está en el 11-AGO —la última de las 2
    # ventas que alcanzó a entrar por M2E antes de que se desinstalara—, así que
    # "desde la última registrada" significaría barrer semanas de golpe: casi
    # las 96 órdenes, cada una un pedido de Woo nuevo, una orden de Odoo
    # duplicando la que Gabriela ya capturó, y el descuento de stock de
    # mercancía que salió hace semanas.
    #
    # La marca sirve para no repetir trabajo, no para recuperar historia. Por
    # eso se acota: traer el histórico es una decisión aparte y se pide con
    # `desde` explícito.
    tope = ahora - timedelta(days=int(getattr(
        settings, "pedidos_temu_sondeo_max_dias", 2) or 2))
    if marca < tope:
        log.warning("pedidos_temu_sondeo: la marca (%s) es más vieja que el tope "
                    "de %s; se limita a %s. El histórico se trae aparte, con "
                    "`desde` explícito.", marca.date(),
                    getattr(settings, "pedidos_temu_sondeo_max_dias", 2), tope.date())
        return tope
    return marca


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


async def revisar(paginas: int = 1, desde: datetime | None = None,
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
    corte = desde or _desde()
    from services import pedidos_temu, pedidos_ml

    vistas = nuevas = creadas = viejas = sin_sku = sin_mapear = 0
    errores: list[str] = []
    try:
        for pagina in range(1, max(1, paginas) + 1):
            lote = await _listar(pagina)
            if not lote:
                break
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
                    continue
                nuevas += 1

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
                       viejas=viejas, sin_sku=sin_sku, sin_mapear=sin_mapear,
                       solo_registro=solo_registro, desde=corte.isoformat(),
                       errores=errores[:10])
        log.info("TEMU sondeo: %d vistas · %d nuevas · %d %s · %d viejas · "
                 "%d sin SKU · %d sin mapear",
                 vistas, nuevas, creadas,
                 "habría creado" if solo_registro else "creadas",
                 viejas, sin_sku, sin_mapear)
    except Exception as exc:  # noqa: BLE001
        log.exception("pedidos_temu_sondeo.revisar falló")
        _ultimo.update(estado="error", ts=datetime.now(timezone.utc).isoformat(),
                       motivo=str(exc)[:300])
    return dict(_ultimo)
