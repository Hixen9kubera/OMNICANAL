"""
Amazon FBA — la pestaña /analisis/fba (porta el prompt 1 de José, pieza FBA).

Dos endpoints:

  POST /api/fba/reporte   sube el export "Manage FBA Inventory" de Seller
                          Central (CSV). Cada subida REEMPLAZA la foto completa
                          en `ops.fba_snapshot` — es un snapshot, no historial.
  GET  /api/fba           el tablero: inventario FBA + venta de Amazon del
                          período + plan de envío por SKU.

POR QUÉ SE SUBE UN CSV EN VEZ DE LEER EL SYNC: la foto de FBA del sync está
corta — al 18-ago veía 20 SKUs con 1,299 unidades donde el reporte de Seller
Central trae 101 SKUs con 2,224 en bodega MÁS 3,426 en camino (inbound), que
la API de summaries que usamos no reporta por separado. El reporte además trae
el ASIN (bloqueo #1 histórico de esta pestaña) y el volumen por unidad MEDIDO
por Amazon, que funciona de segunda báscula contra nuestro costeo.

Del prompt original quedan FUERA, y a propósito:
  · capacidad contratada — no vive en ninguna tabla ni la trae este reporte;
  · tier por peso / tarifa — la tabla de tarifas de Amazon no está en el
    sistema, y sin ella el tier es un adorno.
El plan de envío sí está: (objetivo de cobertura × venta diaria) − disponible
− en camino, con el volumen del envío en m³ salido del volumen POR UNIDAD que
mide Amazon (más honesto que nuestras dimensiones, que traen los defectos de
captura conocidos del costeo).
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from services import fba_reporte
from services import supabase_db as sdb

router = APIRouter(prefix="/api/fba", tags=["fba"])
log = logging.getLogger("omnicanal.fba")

# Umbrales del semáforo de cobertura, en días — los del restock original de
# José (bollinger.py): crítico / alerta / ok, y arriba de 50 va sobrado.
UMBRAL_CRITICO = 14.0
UMBRAL_ALERTA = 30.0
UMBRAL_OK = 50.0

@router.post("/reporte")
async def subir_reporte(archivo: UploadFile = File(...)) -> dict[str, Any]:
    """Sube el export de Seller Central. Reemplaza la foto completa.

    El parseo y el guardado viven en services/fba_reporte: son EXACTAMENTE los
    mismos que usa el refresco automático por SP-API — un solo código para las
    dos puertas de entrada."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    crudo = await archivo.read()
    if len(crudo) > 20_000_000:
        raise HTTPException(413, "el archivo pasa de 20 MB — no parece el reporte")
    try:
        filas = fba_reporte.parsear(fba_reporte.decodificar(crudo),
                                    archivo.filename or "reporte.csv")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    n = await asyncio.to_thread(fba_reporte.guardar, filas)
    log.info("FBA: reporte '%s' cargado — %d SKUs", archivo.filename, n)
    return {"ok": True, "skus": n, "archivo": archivo.filename}


@router.post("/refrescar")
async def refrescar() -> dict[str, Any]:
    """Pide el reporte a Amazon por la Reports API y lo carga al terminar.

    Contesta DE INMEDIATO: Amazon tarda minutos en generar el reporte y eso
    corre en segundo plano. La página lee el avance en GET /api/fba
    (campo `refresco`)."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    return await fba_reporte.refrescar_en_fondo()


_SQL_TABLERO = """
with ventas as (
  select i.sku::text as sku, sum(i.cantidad)::int as uds,
         max((o.creado_at at time zone 'America/Mexico_City')::date) as ultima
    from channel.order_items i
    join channel.orders o using (canal, cuenta, external_order_id)
   where o.canal = 'amazon'
     and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
     and i.sku is not null
     and (o.creado_at at time zone 'America/Mexico_City')::date > current_date - %(dias)s
   group by 1)
select f.sku::text as sku, f.asin, f.product_name, f.price,
       f.fulfillable, f.reserved, f.unsellable,
       (f.inbound_working + f.inbound_shipped + f.inbound_receiving)::int as en_camino,
       f.per_unit_volume,
       coalesce(v.uds, 0)  as uds_periodo,
       v.ultima::text      as ultima_venta,
       cv.costo_total      as costo,
       case when cv.largo > 0 and cv.ancho > 0 and cv.alto > 0
            then cv.largo * cv.ancho * cv.alto end as vol_costeo
  from ops.fba_snapshot f
  left join ventas v on v.sku = f.sku::text
  left join costing.costos_validados cv on cv.sku = f.sku
 where f.fulfillable > 0 or f.reserved > 0 or f.unsellable > 0
    or (f.inbound_working + f.inbound_shipped + f.inbound_receiving) > 0
    or coalesce(v.uds, 0) > 0
"""

# Venden en Amazon y NO están en el reporte: sin listing FBA. No entran a la
# tabla principal —no hay nada de FBA que mostrarles— pero callarlos escondería
# justo a los candidatos a mandar (caso TEC-1607-NEG: vende y no tiene FBA).
_SQL_SIN_FBA = """
select i.sku::text as sku, max(i.titulo) as titulo, sum(i.cantidad)::int as uds
  from channel.order_items i
  join channel.orders o using (canal, cuenta, external_order_id)
 where o.canal = 'amazon'
   and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
   and i.sku is not null
   and (o.creado_at at time zone 'America/Mexico_City')::date > current_date - %(dias)s
   and not exists (select 1 from ops.fba_snapshot f where f.sku = i.sku)
 group by 1 order by 3 desc limit 20
"""


@router.get("")
async def tablero(
    dias: int = Query(60, ge=7, le=180),
    objetivo: int = Query(60, ge=14, le=180,
                          description="días de cobertura que el plan de envío quiere dejar"),
) -> dict[str, Any]:
    """Inventario FBA + venta del período + plan de envío por SKU."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    try:
        meta = await asyncio.to_thread(sdb.fetch_one, """
            select count(*)::int as skus, max(subido_at)::text as subido_at,
                   max(report_name) as archivo
              from ops.fba_snapshot""")
        if not meta or not meta["skus"]:
            return {"reporte": None, "dias": dias, "objetivo": objetivo,
                    "refresco": fba_reporte.estado(),
                    "kpis": None, "filas": [], "sin_fba": []}
        crudas = await asyncio.to_thread(sdb.fetch_all, _SQL_TABLERO, {"dias": dias})
        sin_fba = await asyncio.to_thread(sdb.fetch_all, _SQL_SIN_FBA, {"dias": dias})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc

    filas: list[dict[str, Any]] = []
    for r in crudas:
        disp = int(r["fulfillable"] or 0)
        en_camino = int(r["en_camino"] or 0)
        uds = int(r["uds_periodo"] or 0)
        vel = uds / dias if uds else 0.0
        cobertura = round(disp / vel, 1) if vel and disp else None
        if not uds:
            # Sin venta en el período. Con stock es el "sin venta" clásico
            # (paga almacenaje sin devolver nada); sin stock pero con inbound
            # es un envío llegando y merece verse distinto de la nada.
            semaforo = ("sin_venta" if disp > 0
                        else "en_transito" if en_camino > 0 else "sin_stock")
        elif not disp:
            semaforo = "agotado"          # vende y FBA está en CERO: lo más urgente
        elif cobertura <= UMBRAL_CRITICO:
            semaforo = "critico"
        elif cobertura <= UMBRAL_ALERTA:
            semaforo = "alerta"
        elif cobertura <= UMBRAL_OK:
            semaforo = "ok"
        else:
            semaforo = "sobrado"
        sugerido = max(0, math.ceil(objetivo * vel - disp - en_camino)) if vel else 0
        vol_u = None if r["per_unit_volume"] is None else float(r["per_unit_volume"])
        vol_costeo = None if r["vol_costeo"] is None else float(r["vol_costeo"])
        # Divergencia de volumen: Amazon midió la unidad empaquetada; ~10-15%
        # arriba del costeo es normal. 2× ya no es empaque: alguien capturó mal
        # (mismo criterio que el peso de la báscula de ML).
        div = (vol_u is not None and vol_costeo and vol_costeo > 0
               and (vol_u / vol_costeo >= 2 or vol_u / vol_costeo <= 0.5))
        filas.append({
            "sku": r["sku"], "titulo": r["product_name"], "asin": r["asin"],
            "precio": None if r["price"] is None else float(r["price"]),
            "disponible": disp, "reservado": int(r["reserved"] or 0),
            "no_vendible": int(r["unsellable"] or 0), "en_camino": en_camino,
            "uds_periodo": uds, "uds_dia": round(vel, 2) if vel else None,
            "ultima_venta": r["ultima_venta"],
            "cobertura_dias": cobertura, "semaforo": semaforo,
            "sugerido": sugerido,
            "vol_envio_m3": (round(sugerido * vol_u / 1_000_000, 3)
                             if sugerido and vol_u else None),
            "vol_unidad_cm3": vol_u, "vol_costeo_cm3": vol_costeo,
            "vol_divergente": bool(div),
            "costo": None if r["costo"] is None else float(r["costo"]),
        })
    orden = {"agotado": 0, "critico": 1, "alerta": 2, "ok": 3,
             "sobrado": 4, "sin_venta": 5, "en_transito": 6, "sin_stock": 7}
    filas.sort(key=lambda x: (orden[x["semaforo"]], -(x["uds_periodo"] or 0),
                              -(x["disponible"] or 0)))
    con_stock = [x for x in filas if x["disponible"] > 0]
    sin_venta = [x for x in con_stock if not x["uds_periodo"]]
    return {
        "reporte": {"archivo": meta["archivo"], "subido_at": meta["subido_at"],
                    "skus": int(meta["skus"])},
        "refresco": fba_reporte.estado(),
        "dias": dias, "objetivo": objetivo,
        "kpis": {
            "skus_con_stock": len(con_stock),
            "disponibles": sum(x["disponible"] for x in filas),
            "reservadas": sum(x["reservado"] for x in filas),
            "en_camino": sum(x["en_camino"] for x in filas),
            "sin_venta_skus": len(sin_venta),
            "sin_venta_uds": sum(x["disponible"] for x in sin_venta),
            "plan_uds": sum(x["sugerido"] for x in filas),
            "plan_m3": round(sum(x["vol_envio_m3"] or 0 for x in filas), 2),
        },
        "filas": filas,
        "sin_fba": [dict(r) for r in sin_fba],
        "nota": "cobertura = disponible ÷ venta diaria del período; el plan "
                "deja el objetivo de días descontando lo disponible y lo que "
                "ya viene en camino",
    }
