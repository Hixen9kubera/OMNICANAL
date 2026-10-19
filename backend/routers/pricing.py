"""
pricing.py — La pestaña PRICING (v0.650.0): el «Monitor de precios FULL».

  GET /api/pricing/monitor      → la foto completa (meta, pubs, riv), comprimida
  GET /api/pricing/img/{hoja}   → una hoja de miniaturas (`p0`, `s12`…), JPEG
  GET /api/pricing/hechos       → los precios que sí se movieron en ML (v0.654.0)
                                  y lo vendido desde el cambio (kubera, SELECT)
  GET /api/pricing/vivo         → stock FULL de ML y ventas de 14/30 días de AHORA
                                  (v0.655.0; solo GET a ML, solo SELECT a kubera)
  GET /api/pricing/salud        → calidad y experiencia de compra de ML por publicación
                                  (v0.659.0; SELECT a enrich.listing_health, sin ML)
  GET /api/pricing/motivos/{id} → por qué se canceló o se devolvió cada venta de UNA
                                  publicación (v0.659.0; SELECT a kubera y GET /orders a ML)
  GET /api/pricing/serie/{id}   → ventas y visitas por día de UNA publicación, de ahora, para
                                  su gráfica (v0.663.0; SELECT a kubera, GET /orders y visitas a ML)

SOLO LECTURA. Ninguna ruta escribe: la pantalla SUGIERE qué hacer con cada
precio y no cambia ninguno. El servicio (`services/pricing_monitor.py`) solo
lee la foto de su carpeta o de GitHub, y para «Hecho» lee kubera.

ACCESO: admin con sesión del panel, y desde v0.661.0 también las personas de
`core.rbac.PRICING_INVITADOS` (Haim y Andrea Pardo, Brandon 16-oct-2026): la foto
trae ventas y visitas por publicación. Lo decide `acceso_pricing` en cada ruta;
la regla ("GET", "/api/pricing", "operador") de core/rbac.py solo deja llegar aquí.

La foto viaja ya comprimida (`Content-Encoding: gzip`, ≈0.9 MB en vez de 3.6):
el navegador la descomprime solo. Un cliente que no acepte gzip la recibe plana.
"""
from __future__ import annotations

import gzip
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from core import identidad as core_identidad
from core import rbac
from services import pricing_monitor as pm

log = logging.getLogger("omnicanal.pricing")
router = APIRouter(prefix="/api/pricing", tags=["pricing"])


async def acceso_pricing(request: Request):
    """Persona con sesión: admin o invitada (`rbac.PRICING_INVITADOS`). Si no, 401/403 — SIEMPRE."""
    quien = getattr(request.state, "identidad", None)
    if quien is None:
        quien = await core_identidad.resolver(request)
    if not getattr(quien, "autenticado", False):
        log.warning("PRICING 401: %s sin sesión", request.url.path)
        raise HTTPException(status_code=401, detail="Inicia sesión en el panel para ver Pricing.")
    if not rbac.ve_pricing(quien):
        log.warning("PRICING 403: %s (%s, %s) intentó %s", quien.actor, getattr(quien, "tipo", ""),
                    getattr(quien, "rol", ""), request.url.path)
        raise HTTPException(status_code=403, detail="Pricing no está disponible para tu cuenta.")
    return quien


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, pm.SinFoto):
        return HTTPException(status_code=404, detail=str(exc))
    log.warning("pricing: %s", exc)
    return HTTPException(status_code=502, detail=str(exc))


@router.get("/monitor")
async def monitor(request: Request, quien=Depends(acceso_pricing)) -> Response:
    try:
        gz, meta = await pm.foto()
    except (pm.SinFoto, pm.FalloFuente) as exc:
        raise _http(exc) from exc
    cab = {"Cache-Control": "private, no-store", "X-Pricing-Generado": str(meta.get("generado", ""))}
    if "gzip" in request.headers.get("accept-encoding", "").lower():
        return Response(content=gz, media_type="application/json",
                        headers={**cab, "Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(content=gzip.decompress(gz), media_type="application/json", headers=cab)


@router.get("/hechos")
async def hechos(quien=Depends(acceso_pricing)) -> dict:
    """Los precios que SÍ se movieron en ML (columna «Hecho») y lo vendido desde el cambio. Solo lee."""
    return await pm.hechos()


@router.get("/vivo")
async def vivo(quien=Depends(acceso_pricing)) -> dict:
    """Stock FULL de ML y ventas de 14/30 días de AHORA, para «Se agota» y low stock (v0.655.0). Solo lee."""
    try:
        return await pm.vivo()
    except (pm.SinFoto, pm.FalloFuente) as exc:
        raise _http(exc) from exc


@router.get("/salud")
async def salud(quien=Depends(acceso_pricing)) -> dict:
    """Calidad y experiencia de compra de ML por publicación (v0.659.0), de lo que guarda calidad_ml. Solo lee."""
    try:
        return await pm.salud()
    except (pm.SinFoto, pm.FalloFuente) as exc:
        raise _http(exc) from exc


@router.get("/motivos/{item_id}")
async def motivos(item_id: str, quien=Depends(acceso_pricing)) -> dict:
    """Por qué se canceló o se devolvió cada venta de una publicación (v0.659.0). Solo SELECT a kubera y GET a ML."""
    try:
        return await pm.motivos(item_id)
    except (pm.SinFoto, pm.FalloFuente) as exc:
        raise _http(exc) from exc


@router.get("/serie/{item_id}")
async def serie(item_id: str, cuenta: str, quien=Depends(acceso_pricing)) -> dict:
    """Ventas y visitas por día de UNA publicación, de ahora, para su gráfica (v0.663.0). SELECT a kubera y GET a ML."""
    try:
        return await pm.serie(item_id, cuenta)
    except (pm.SinFoto, pm.FalloFuente) as exc:
        raise _http(exc) from exc


@router.get("/img/{hoja}")
async def imagen(hoja: str, quien=Depends(acceso_pricing)) -> Response:
    try:
        dato = await pm.hoja(hoja)
    except (pm.SinFoto, pm.FalloFuente) as exc:
        raise _http(exc) from exc
    # La pantalla pide `?v=<generado>`: una foto nueva cambia la URL y no se
    # mezcla con la hoja vieja que el navegador tenga guardada.
    return Response(content=dato, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})
