"""
candados_read.py — Los tres estados que vivían dentro de la bitácora `fanout_log`.

Gemelas de las tres lecturas del PASO 0 (docs/PASO_0_CANDADOS.md):

    fanout_log accion='full_compensado'   →  channel.orders.stock_compensado_at
    fanout_log accion IN (_APLICADAS)     →  ops.fulfillment_operations
    fanout_log resultado ~ '→\\s*(\\d+)'   →  ops.fba_watermark

LA REGLA QUE HACE ESTE MÓDULO DISTINTO A TODOS LOS DEMÁS
---------------------------------------------------------
**Aquí NADA se traga un error.** Ninguna función tiene `except`, y no es un
descuido: es el punto entero del paso 0.

Las gemelas de MySQL terminaban en `except: return False` — y ese `False` no
significaba "no lo hice", significaba "no sé". El sistema lo leía como "no lo
hice" y lo volvía a hacer:

  · compensar dos veces  = devolverle a Woo piezas que nunca salieron
  · aplicar dos veces    = mover inventario real dos veces

Es el mecanismo exacto de los 964 pedidos fantasma del 12-ago.

Ojo con la sobrecorrección: el mismo patrón se CONSERVÓ a propósito en
`imagenes_amazon._cache_get` (paso 4), porque ahí equivocarse cuesta reprocesar
una imagen — caro, no incorrecto. **La diferencia no es el patrón: es qué pasa
cuando la respuesta está mal.** Quien llame a este módulo decide qué hacer con
la excepción; lo que no puede es recibir un `False` inventado.

BLOQUEANTE (psycopg2). Nada de esto se llama desde una corrutina sin
`asyncio.to_thread` — regla 11, el apagón del 13-ago.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from services import supabase_db as sdb


# ── 1) ¿Ya le devolvimos el stock a este pedido? ────────────────────────────

def ya_compensado(canal: str, cuenta: str, external_order_id: str) -> bool:
    """Gemela de `pedidos_ml._ya_compensado`. PROPAGA si la base falla.

    SE BUSCA POR LA PK, no por `wc_order_id`. Desde el RECLAMO (v0.176.0)
    `wc_order_id` es NULL a propósito mientras el pedido está reclamado y aún no
    creado, así que buscar por ahí fallaría justo en los casos revueltos: el
    relevo de contenedores de un deploy y el reintento de ML. La PK
    `(canal, cuenta, external_order_id)` la tiene `sincronizar` en mano y nunca
    es nula.

    "Compensado" es compensado Y NO revertido después. Dos columnas y no un
    boolean: tras una reversión el pedido vuelve a ser compensable, y un `bool`
    diría que no para siempre.
    """
    fila = sdb.fetch_one(
        """select stock_compensado_at, stock_revertido_at from channel.orders
            where canal = %s and cuenta = %s and external_order_id = %s""",
        (canal, cuenta, str(external_order_id)))
    if not fila or not fila.get("stock_compensado_at"):
        return False
    rev = fila.get("stock_revertido_at")
    return rev is None or rev < fila["stock_compensado_at"]


def marcar_compensado(canal: str, cuenta: str, external_order_id: str,
                      cuando: datetime | None = None) -> int:
    """Sella el pedido como compensado.

    Aquí SÍ pisa (sin `coalesce`): una compensación posterior a una reversión es
    un evento nuevo y su fecha tiene que ganarle a la reversión, o el candado de
    arriba seguiría leyendo "revertido después" y compensaría en bucle.
    """
    return sdb.execute(
        """update channel.orders set stock_compensado_at = coalesce(%s, now())
            where canal = %s and cuenta = %s and external_order_id = %s""",
        (cuando, canal, cuenta, str(external_order_id)))


def marcar_revertido(canal: str, cuenta: str, external_order_id: str,
                     cuando: datetime | None = None) -> int:
    """La compensación se deshizo: el pedido vuelve a ser compensable."""
    return sdb.execute(
        """update channel.orders set stock_revertido_at = coalesce(%s, now())
            where canal = %s and cuenta = %s and external_order_id = %s""",
        (cuando, canal, cuenta, str(external_order_id)))


# ── 2) ¿Ya aplicamos este movimiento de bodega? ─────────────────────────────

def ya_aplicada(operacion_id: str) -> bool:
    """Gemela de `stock_full._ya_procesada`. PROPAGA si la base falla.

    No hace falta el filtro `resultado NOT LIKE 'ERROR%'` de la versión MySQL:
    aquí un intento fallido simplemente NO deja fila, así que "no está" ya
    significa "se puede reintentar". Esa regla nació de un 502 del WAF que
    sellaba movimientos para siempre (auditoría 27-jul) y hay que conservarla —
    lo que cambia es que deja de depender de leer un texto.
    """
    return bool(sdb.fetch_one(
        "select 1 from ops.fulfillment_operations where operacion_id = %s limit 1",
        (str(operacion_id)[:64],)))


def marcar_aplicada(operacion_id: str, sku: str | None, cuenta: str | None,
                    accion: str, cuando: datetime | None = None) -> int:
    """Deja constancia de que la operación SE APLICÓ. Solo en el camino de éxito."""
    return sdb.execute(
        """insert into ops.fulfillment_operations
             (operacion_id, sku, cuenta, accion, aplicada_at)
           values (%s, %s, %s, %s, coalesce(%s, now()))
           on conflict (operacion_id) do nothing""",
        (str(operacion_id)[:64], sku or None, cuenta or None, accion, cuando))


# ── 3) ¿En cuánto estaba el FBA la última vez que lo vimos? ─────────────────

def marcas_fba(skus: list[str] | None = None) -> dict[str, int]:
    """{ sku: stock_fba } — gemela del bloque que parseaba `resultado`.

    NO confundir con `channel_read.stock_fba_amazon()`: medido el 14-ago, 96 de
    99 SKUs dan distinto. Aquélla es lo que vio el SYNC hace ≤15 min; ésta es lo
    que vio el VIGILANTE al procesar un evento. Usar la del sync hacía contar
    dos veces el mismo ingreso — ver la nota larga en la migración 0022.
    """
    if skus is None:
        filas = sdb.fetch_all("select sku::text as sku, stock_fba from ops.fba_watermark")
    elif not skus:
        return {}
    else:
        filas = sdb.fetch_all(
            "select sku::text as sku, stock_fba from ops.fba_watermark "
            "where sku = any(%s::citext[])", ([str(s) for s in skus],))
    return {f["sku"]: int(f["stock_fba"]) for f in filas}


def marcar_fba(sku: str, stock_fba: int, cuenta: str | None = None,
               cuando: datetime | None = None) -> int:
    """Guarda lo que el vigilante acaba de ver. Siempre pisa: es una marca de agua."""
    return sdb.execute(
        """insert into ops.fba_watermark (sku, stock_fba, cuenta, visto_at)
           values (%s, %s, %s, coalesce(%s, now()))
           on conflict (sku) do update set
             stock_fba = excluded.stock_fba,
             cuenta    = coalesce(excluded.cuenta, fba_watermark.cuenta),
             visto_at  = excluded.visto_at""",
        (str(sku), int(stock_fba), cuenta, cuando))


def censo() -> dict[str, Any]:
    """Para el arnés y la verificación final."""
    return sdb.fetch_one(
        """select (select count(*) from channel.orders
                    where stock_compensado_at is not null) as compensados,
                  (select count(*) from ops.fulfillment_operations) as operaciones,
                  (select count(*) from ops.fba_watermark) as marcas_fba""") or {}
