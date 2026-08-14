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

from config import settings
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
    # PASO 1 del desmantelamiento (14-ago-2026): el caché vive en kubera.
    # `margenes_read` es BLOQUEANTE (psycopg2) — se conserva el envoltorio
    # `asyncio.to_thread` del llamador, que es la regla 11 de la casa.
    # MySQL solo con el flag apagado, que es el interruptor de reversa.
    if settings.supabase_write_margenes:
        from services import margenes_read
        return margenes_read.envio_leer(pares)
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


ORIGEN_REAL = "ML real"
ORIGEN_ESTIMADO = "estimado"
ORIGEN_SIN_DATO = "sin dato"


def aplicar_a_lineas(lineas: list[dict[str, Any]]) -> dict[str, int]:
    """
    Sustituye el envío ESTIMADO por el cobro REAL de ML donde lo haya, y deja
    dicho en cada línea de dónde salió el número (`envio_origen`).

    EL REPARTO. ML cobra por EMBARQUE, no por pieza: una orden con tres
    artículos tiene un solo cobro. Se reparte entre sus líneas en proporción a
    las unidades, que es la misma convención del popup de Análisis. Repartir
    por importe premiaría al artículo caro de un carrito mixto con un flete que
    no le toca.

    UN CERO REAL NO ES UN HUECO. `costo_vendedor = 0` es una respuesta legítima
    de ML (el comprador pagó el envío); `NULL` es que no lo pudimos consultar.
    Por eso se compara contra None y no por verdad/falsedad.

    Devuelve el censo {reales, estimadas, sin_dato} para poder declarar la
    cobertura en el archivo en vez de dejarla implícita.
    """
    censo = {"reales": 0, "estimadas": 0, "sin_dato": 0}
    if not lineas:
        return censo

    ml = [f for f in lineas if (f.get("canal") or "") == "mercado_libre"
          and f.get("pedido")]
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    if ml:
        pares = sorted({(str(f["cuenta"]), str(f["pedido"])) for f in ml})
        try:
            cache = leer(pares)
        except Exception as exc:  # noqa: BLE001
            # Sin MySQL (staging solo-Supabase) el reporte no se cae: se queda
            # con el estimado y lo dice.
            log.warning("envio_real: caché no disponible (%s); voy con estimado", exc)
            cache = {}

    uds_orden: dict[tuple[str, str], int] = {}
    for f in ml:
        clave = (str(f["cuenta"]), str(f["pedido"]))
        uds_orden[clave] = uds_orden.get(clave, 0) + int(f.get("cantidad") or 0)

    for f in lineas:
        est = f.get("envio_estimado")
        real = None
        if (f.get("canal") or "") == "mercado_libre" and f.get("pedido"):
            clave = (str(f["cuenta"]), str(f["pedido"]))
            fila = cache.get(clave)
            costo = fila.get("costo_vendedor") if fila else None
            if costo is not None:
                total = uds_orden.get(clave) or 0
                cant = int(f.get("cantidad") or 0)
                # Sin unidades no hay proporción que repartir; se cae al total
                # del embarque antes que inventar una división por cero.
                real = (round(float(costo) * cant / total, 2) if total > 0
                        else round(float(costo), 2))

        if real is not None:
            f["envio"], f["envio_origen"] = real, ORIGEN_REAL
            censo["reales"] += 1
        elif est is not None:
            f["envio"], f["envio_origen"] = float(est), ORIGEN_ESTIMADO
            censo["estimadas"] += 1
        else:
            f["envio"], f["envio_origen"] = None, ORIGEN_SIN_DATO
            censo["sin_dato"] += 1

        # El costo final se rearma con el envío que acabamos de resolver: si no,
        # la columna diría una cosa y el total otra.
        if f.get("costo_base") is not None:
            f["costo_final"] = round(float(f["costo_base"])
                                     + float(f.get("comision_ml") or 0)
                                     + float(f.get("envio") or 0), 2)
    return censo


# UN SOLO LLENADO A LA VEZ. Análisis pide el suyo desde cuatro lados (la tabla,
# el dashboard, el reporte y su vista previa) y uno de ellos lo lanza sin
# esperarlo. Con la pestaña abierta se solapaban, y cada llenado abría SU PROPIA
# ráfaga contra Mercado Libre: ocho llamadas concurrentes por llenado, no ocho
# en total. El candado no pierde trabajo — el que llega tarde se va sin hacer
# nada y el que está corriendo llena el mismo caché para todos.
_llenando = asyncio.Lock()


async def completar(pares: list[tuple[str, str]], presupuesto: int = 250) -> int:
    """
    Consulta a ML las órdenes SIN costo en caché (o con NULL viejo de >24 h),
    hasta `presupuesto` órdenes por llamada — así una primera carga grande no
    se come el timeout del proxy: cada refresco del panel avanza otro tanto.
    Devuelve cuántas órdenes consultó.
    """
    if _llenando.locked():
        return 0  # ya hay un llenado en curso: este se salta, no se encola
    async with _llenando:
        return await _completar(pares, presupuesto)


async def _completar(pares: list[tuple[str, str]], presupuesto: int) -> int:
    from datetime import datetime, timedelta

    # leer() es psycopg2/MySQL SÍNCRONO y aquí llega con ~15,000 pares: medido en
    # 1.46 s. Ejecutarlo en la corrutina congela el backend ENTERO ese tiempo —
    # el mismo defecto que ya se había pagado en los webhooks (ver _procesar_ml).
    cache = await asyncio.to_thread(leer, pares)
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
    fallos: dict[int, int] = {}  # status → cuántas, para poder verlo en los logs

    async with httpx.AsyncClient(base_url=_API, timeout=20.0) as cli:

        async def una(cuenta: str, oid: str) -> None:
            if cuenta not in tokens:
                # Lee el token de la BD: síncrono, va a un hilo.
                tokens[cuenta] = await asyncio.to_thread(meli._access_token, cuenta)
            tk = tokens.get(cuenta)
            if not tk:
                return  # sin token no hay intento que registrar: es falla nuestra
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
                    # EL INTENTO SE REGISTRA. Antes se descartaba en silencio, así
                    # que la orden seguía "pendiente" y volvía a la CABEZA de la
                    # lista (que va ordenada): la misma orden se pedía otra vez en
                    # cada carga del panel, para siempre. Eran 2,241 órdenes que
                    # ML rechaza en ráfaga (403) y contesta 200 de una en una:
                    # el propio reintento era lo que las hacía fallar. Con la fila
                    # en NULL entran en la regla de reintento a las 24 h y la
                    # lista AVANZA. El COALESCE del guardado impide que un NULL
                    # pise un costo real.
                    fallos[r.status_code] = fallos.get(r.status_code, 0) + 1
                    resultados.append((cuenta, oid, None, None))
                    return
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
        if settings.supabase_write_margenes:
            from services import margenes_read
            margenes_read.envio_guardar(resultados)
            return
        # UN solo INSERT: escribir de a una costaba un viaje de red por orden
        # al MySQL de Hostinger, que era lo que hacía lento el llenado.
        vals = ", ".join(["(%s, %s, %s, %s, UTC_TIMESTAMP())"] * len(resultados))
        params: list[Any] = []
        for cuenta, oid, sid, costo in resultados:
            params += [cuenta, oid, sid, costo]
        db.execute(
            "INSERT INTO ml_envio_real (cuenta, external_order_id, shipment_id,"
            f" costo_vendedor, consultado_at) VALUES {vals}"
            " ON DUPLICATE KEY UPDATE"
            " shipment_id = COALESCE(VALUES(shipment_id), shipment_id),"
            " costo_vendedor = COALESCE(VALUES(costo_vendedor), costo_vendedor),"
            " consultado_at = UTC_TIMESTAMP()",
            tuple(params))

    if resultados:
        await asyncio.to_thread(_guardar)
    log.info("envio_real: %d órdenes consultadas (%d pendientes)%s",
             len(resultados), len(faltan) - len(lote),
             f" · sin dato por respuesta de ML: {fallos}" if fallos else "")
    return len(resultados)
