"""
inventario.py — Endpoints de la pestaña INVENTARIO · Catálogo Maestro.

TODO ES DE LECTURA. No hay POST/PUT/PATCH a propósito: la pestaña nace VISOR
(ver la cabecera de `services/inventario_maestro.py` para el porqué medido), y
la captura humana de entradas —que sí escribiría stock y por tanto enciende un
flujo vivo— va aparte y con el dale de Brandon.

REGLA 11 DE LA CASA, la que costó el apagón de cinco horas del 13-ago: en una
corrutina nada que espere a la red o al disco se llama de forma síncrona.
Aquí eso aplica a TODO — `wp_db` (pymysql), `supabase_db` (psycopg2) y `odoo`
(xmlrpc) son los tres bloqueantes, y el historial además tarda ~1 s por SKU
contra Odoo. Por eso cada endpoint envuelve su trabajo en `asyncio.to_thread`:
sin eso, un solo clic en Trazabilidad congelaría el backend ENTERO —no solo a
quien lo pidió— mientras Odoo contesta.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query

from services import inventario_maestro as inv

log = logging.getLogger("omnicanal.routers.inventario")

router = APIRouter(prefix="/api/inventario", tags=["inventario"])

# Las causas que el libro de Odoo sabe distinguir. `reales` es el filtro por
# omisión: todo menos los pasos internos PICK/PACK, que son mayoría y ruido.
_CAUSAS = {"todo", "reales", "entrada", "venta", "envio_full", "devolucion",
           "ajuste", "traspaso", "preparacion", "merma", "cuarentena", "otro"}


def _skus(crudo: str | None) -> list[str] | None:
    """Convierte 'A, B , C' en ['A','B','C']. Sin nada, la sonda del piloto."""
    if not crudo or not crudo.strip():
        return None
    return [s.strip() for s in crudo.replace("\n", ",").split(",") if s.strip()]


@router.get("")
async def listar(
    skus: str | None = Query(
        None,
        description="SKUs separados por coma. Sin esto se devuelven los 10 del "
                    "piloto que Brandon fijó como sonda."),
):
    """
    La tabla del catálogo maestro: imagen, empaque, existencias y las cinco
    etapas, un renglón por SKU de WooCommerce (padres y variaciones).

    Sin paginación a propósito: hoy la sonda son 10 SKUs y cada fila cuesta una
    consulta a Odoo. Cuando se abra al catálogo completo, la paginación entra
    junto con el criterio de orden, no antes.
    """
    pedidos = _skus(skus)
    if pedidos and len(pedidos) > 200:
        raise HTTPException(
            400, "Máximo 200 SKUs por consulta: cada fila cruza Woo, Odoo y kubera "
                 "en vivo, y un lote mayor tarda más de lo que aguanta el proxy.")
    try:
        filas = await asyncio.to_thread(inv.filas, pedidos)
    except Exception as exc:  # noqa: BLE001
        log.exception("inventario.listar falló")
        raise HTTPException(502, f"No se pudo leer el inventario: {exc}") from exc
    return {
        "items": filas,
        "total": len(filas),
        "piloto": list(inv.PILOTO),
        "es_piloto": pedidos is None,
        "resumen": inv.resumen(filas),
    }


@router.get("/{sku}")
async def ficha(sku: str):
    """La ficha de un SKU — lo mismo que un renglón, pero solo. Alimenta el
    cajón lateral del diseño."""
    try:
        filas = await asyncio.to_thread(inv.filas, [sku])
    except Exception as exc:  # noqa: BLE001
        log.exception("inventario.ficha(%s) falló", sku)
        raise HTTPException(502, f"No se pudo leer el SKU: {exc}") from exc
    if not filas:
        raise HTTPException(404, f"SKU {sku} no encontrado")
    f = filas[0]
    # Un SKU que no está en Woo, ni en Odoo, ni tiene renglón de costo, no
    # existe en ninguna parte: eso es un 404. OJO con no confundirlo con el
    # caso legítimo de DEPO-0048-EST, que NO está en Woo ni en Odoo pero SÍ
    # tiene costo de packing list — ése hay que mostrarlo, porque el hueco es
    # justo lo que la pestaña tiene que hacer visible.
    if not f["existe_en_woo"] and not f["existe_en_odoo"] and not f["contenedor"]:
        raise HTTPException(404, f"SKU {sku} no existe en WooCommerce, Odoo ni costos")
    return f


@router.get("/{sku}/movimientos")
async def movimientos(
    sku: str,
    causa: str | None = Query(
        "reales", description="Filtro de causa. 'reales' (por omisión) esconde "
                              "los pasos internos PICK/PACK de Odoo."),
    limite: int = Query(200, ge=1, le=1000),
):
    """
    El historial de bodega de un SKU: entradas, ventas, envíos a FULL/FBA,
    devoluciones, ajustes, traspasos y mermas, con SALDO corriente.

    Sale de Odoo y solo de Odoo — es la única fuente de movimiento real que
    existe en la casa, y son 9 meses de historia que hoy no están copiados en
    ninguna parte. Tarda ~1 s por SKU, de ahí el `to_thread`.
    """
    if causa and causa not in _CAUSAS:
        raise HTTPException(400, f"Causa desconocida: {causa}. "
                                 f"Válidas: {', '.join(sorted(_CAUSAS))}")
    try:
        return await asyncio.to_thread(
            inv.movimientos, sku, None if causa == "todo" else causa, limite)
    except Exception as exc:  # noqa: BLE001
        log.exception("inventario.movimientos(%s) falló", sku)
        raise HTTPException(502, f"No se pudo leer el historial: {exc}") from exc
