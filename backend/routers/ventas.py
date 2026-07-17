"""
ventas.py — API del tab VENTAS.

  GET /api/ventas/horario → ventas por hora (00–23) del rango pedido, SIEMPRE
                            comparadas contra el mismo rango de la semana
                            anterior, con deltas en %.

Parámetros:
  canal   general | mercado_libre        (general = todas las cuentas sumadas)
  cuenta  BEKURA | SANCORFASHION | vacío (vacío = todas las cuentas del canal)
  desde   YYYY-MM-DD  (default: hoy CDMX)
  hasta   YYYY-MM-DD  (default: = desde)

Los datos salen de la API de órdenes de ML (precio REAL de cada venta) con
caché horario en MySQL — ver services/ventas_ml.py.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from services import ventas_ml

router = APIRouter(prefix="/api/ventas", tags=["ventas"])

_CUENTAS = {"BEKURA", "SANCORFASHION"}
_MAX_DIAS = 31  # tope del rango: protege a ML y a la tabla de un rango loco


@router.get("/horario")
async def horario(
    canal: str = Query("general"),
    cuenta: str | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    fuente: str = Query("pedidos"),
):
    if canal not in ("general", "mercado_libre"):
        # Amazon y demás canales de ventas vendrán después; avisamos claro.
        raise HTTPException(400, f"Canal '{canal}' aún sin ventas integradas.")
    cta = (cuenta or "").strip().upper() or None
    if cta and cta not in _CUENTAS:
        raise HTTPException(400, f"Cuenta desconocida: {cuenta}")
    if canal == "general":
        cta = None  # general SIEMPRE suma todas las cuentas

    d1 = desde or ventas_ml.hoy_mx()
    d2 = hasta or d1
    if d2 < d1:
        d1, d2 = d2, d1
    if (d2 - d1).days + 1 > _MAX_DIAS:
        raise HTTPException(400, f"Rango máximo: {_MAX_DIAS} días.")
    if d1 > ventas_ml.hoy_mx():
        raise HTTPException(400, "El rango empieza en el futuro.")

    # La operación vive de PEDIDOS de WooCommerce (Brandon, 2026-07-17):
    # General = todos los pedidos; el canal/cuenta filtra los mismos pedidos.
    # `fuente=ml` conserva la vista histórica de la API de ML (para cuando se
    # quiera volver a comparar contra lo que reporta Mercado Libre).
    if fuente == "ml":
        return await ventas_ml.resumen(cta, d1, d2)
    return await ventas_ml.resumen_pedidos(cta, d1, d2)


@router.get("/dias")
async def dias(
    canal: str = Query("general"),
    cuenta: str | None = Query(None),
    dias: int = Query(7, ge=1, le=31),
):
    """
    Serie por día (para sparkline/tendencia): total de cada uno de los últimos
    N días con su comparativo de la semana anterior.
    """
    if canal not in ("general", "mercado_libre"):
        raise HTTPException(400, f"Canal '{canal}' aún sin ventas integradas.")
    cta = (cuenta or "").strip().upper() or None
    if canal == "general":
        cta = None
    hoy = ventas_ml.hoy_mx()
    salida = []
    for i in range(dias - 1, -1, -1):
        f = hoy - timedelta(days=i)
        r = await ventas_ml.resumen(cta, f, f)
        t = r["totales"]
        salida.append({"fecha": f.isoformat(), "monto": t["monto"],
                       "pedidos": t["pedidos"],
                       "prev_monto": t["prev"]["monto"],
                       "delta_monto": t["delta"]["monto"]})
    return {"canal": canal, "cuenta": cta, "dias": salida}
