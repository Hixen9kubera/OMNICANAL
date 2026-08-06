"""
envio_real.py — Costo de ENVÍO real por embarque, directo de Mercado Libre.

FASE 0 de "Márgenes en Análisis" (Eduardo, 6-ago). El envío estimado
(`costing.costos_finales.costo_fee_envio`) demostró mentir en las dos
direcciones: el peso del packing list mezcla unidades (pieza / caja master /
total del renglón), así que a Malla Sombra le inventaba una pérdida de $200k
(fee $349 con peso de caja) y a 141 SKUs con venta les puso el flete en $0,
inflando el top de márgenes.

El número VERDADERO existe y es consultable: `GET /shipments/{id}/costs`
devuelve lo que ML le cobró al VENDEDOR por ese embarque, ya con descuentos.
Verificado contra embarques reales antes de escribir esto: $88.00 (SANCOR) y
$82.40 (BEKURA) por la malla que el estimado ponía en $349.

Cada consulta se cachea en MySQL — tabla NUESTRA (`ml_envio_real`), mismo
terreno que `amazon_imagenes` — así que la primera carga consulta a ML solo lo
que falta y las siguientes solo las órdenes nuevas. Cuando exista
`channel.order_shipments` en la BD kubera (fase 1, decisión de Eduardo), el
backfill bebe de este caché y la tabla se retira sin tirar trabajo.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from services import db, meli

log = logging.getLogger("omnicanal.envio_real")

_API = "https://api.mercadolibre.com"

# Una fila por orden (no por pieza): el cobro de ML es por embarque. La orden
# con costo_vendedor NULL registra el intento — se reintenta pasadas 24 h
# (los webhooks a veces llegan antes de que el shipment tenga costo asignado).
_DDL = """
CREATE TABLE IF NOT EXISTS ml_envio_real (
  cuenta            VARCHAR(32)  NOT NULL,
  external_order_id VARCHAR(40)  NOT NULL,
  shipment_id       VARCHAR(40)  NULL,
  costo_vendedor    DECIMAL(10,2) NULL,
  consultado_at     DATETIME     NOT NULL,
  PRIMARY KEY (cuenta, external_order_id)
) CHARACTER SET utf8mb4
"""
_tabla_lista = False


def _asegurar_tabla() -> None:
    global _tabla_lista
    if not _tabla_lista:
        db.execute(_DDL)
        _tabla_lista = True


def leer(pares: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Filas del caché para (cuenta, external_order_id). Solo lectura."""
    if not pares:
        return {}
    _asegurar_tabla()
    res: dict[tuple[str, str], dict[str, Any]] = {}
    por_cuenta: dict[str, list[str]] = {}
    for cuenta, oid in pares:
        por_cuenta.setdefault(cuenta, []).append(str(oid))
    for cuenta, ids in por_cuenta.items():
        marcas = ",".join(["%s"] * len(ids))
        filas = db.fetch_all(
            f"SELECT external_order_id, shipment_id, costo_vendedor, consultado_at "
            f"FROM ml_envio_real WHERE cuenta=%s AND external_order_id IN ({marcas})",
            (cuenta, *ids))
        for f in filas:
            res[(cuenta, str(f["external_order_id"]))] = f
    return res


async def completar(pares: list[tuple[str, str]], presupuesto: int = 250) -> int:
    """
    Consulta a ML las órdenes SIN costo en caché (o con NULL viejo de >24 h),
    hasta `presupuesto` órdenes por llamada — así una primera carga grande no
    se come el timeout del proxy: cada refresco del panel avanza otro tanto.
    Devuelve cuántas órdenes consultó.
    """
    from datetime import datetime, timedelta

    _asegurar_tabla()
    cache = leer(pares)
    limite_retry = datetime.utcnow() - timedelta(hours=24)
    faltan = [
        (c, str(o)) for (c, o) in pares
        if (c, str(o)) not in cache
        or (cache[(c, str(o))]["costo_vendedor"] is None
            and cache[(c, str(o))]["consultado_at"] < limite_retry)
    ]
    lote = faltan[: max(0, presupuesto)]
    if not lote:
        return 0

    import httpx

    tokens: dict[str, str | None] = {}
    sem = asyncio.Semaphore(8)
    resultados: list[tuple[str, str, str | None, float | None]] = []

    async with httpx.AsyncClient(base_url=_API, timeout=20.0) as cli:

        async def una(cuenta: str, oid: str) -> None:
            if cuenta not in tokens:
                tokens[cuenta] = meli._access_token(cuenta)
            tk = tokens.get(cuenta)
            if not tk:
                return
            async with sem:
                cab = {"Authorization": f"Bearer {tk}"}
                r = await cli.get(f"/orders/{oid}", headers=cab)
                if r.status_code == 401:
                    nuevo = await meli._renovar_con_candado(cuenta)
                    if not nuevo:
                        return
                    tokens[cuenta] = nuevo
                    cab = {"Authorization": f"Bearer {nuevo}"}
                    r = await cli.get(f"/orders/{oid}", headers=cab)
                if r.status_code != 200:
                    return  # sin fila: se reintenta en la siguiente carga
                sid = (r.json().get("shipping") or {}).get("id")
                costo = None
                if sid:
                    rc = await cli.get(f"/shipments/{sid}/costs", headers=cab)
                    if rc.status_code == 200:
                        senders = rc.json().get("senders") or []
                        if senders and senders[0].get("cost") is not None:
                            costo = float(senders[0]["cost"])
                resultados.append((cuenta, oid, str(sid) if sid else None, costo))

        await asyncio.gather(*(una(c, o) for c, o in lote))

    # Las escrituras van en un hilo aparte para no bloquear el event loop.
    # COALESCE: un reintento que vuelva a traer NULL nunca pisa un costo real
    # (mismo patrón que la comisión 0→valor de pedidos_ml).
    def _guardar() -> None:
        for cuenta, oid, sid, costo in resultados:
            db.execute(
                "INSERT INTO ml_envio_real (cuenta, external_order_id, shipment_id,"
                " costo_vendedor, consultado_at) VALUES (%s, %s, %s, %s, UTC_TIMESTAMP())"
                " ON DUPLICATE KEY UPDATE"
                " shipment_id = COALESCE(VALUES(shipment_id), shipment_id),"
                " costo_vendedor = COALESCE(VALUES(costo_vendedor), costo_vendedor),"
                " consultado_at = UTC_TIMESTAMP()",
                (cuenta, oid, sid, costo))

    if resultados:
        await asyncio.to_thread(_guardar)
    log.info("envio_real: %d órdenes consultadas (%d pendientes)",
             len(resultados), len(faltan) - len(lote))
    return len(resultados)
