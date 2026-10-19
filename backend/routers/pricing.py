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

  POST /api/pricing/precio/{id}/vista    → cómo está en ML y qué pasaría con un precio y un %
  POST /api/pricing/precio/{id}          → CAMBIA el precio en ML (normal + descuento visible)
  POST /api/pricing/precio/{id}/deshacer → la regresa a como estaba antes del último cambio
                                  (v0.665.0; SOLO ADMIN — son las únicas rutas que escriben, en ML)

SOLO LECTURA, salvo `/precio` (v0.665.0, pedido de Brandon 19-oct-2026): cambiar a
mano el precio de una publicación de ML, solo admin, con bitácora y «Deshacer». El servicio (`services/pricing_monitor.py`) solo
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

from pydantic import BaseModel

from core import actor as core_actor
from core import identidad as core_identidad
from core import rbac
from services import pricing_monitor as pm
from services import pricing_precio as pp

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


# ── CAMBIAR EL PRECIO A MANO (v0.665.0) ──────────────────────────────────────

async def solo_admin(request: Request):
    """Cambiar precios en ML es de admin: los invitados de PRICING solo ven."""
    quien = await acceso_pricing(request)
    if getattr(quien, "rol", "") != "admin":
        log.warning("PRICING 403: %s intentó cambiar un precio sin ser admin", quien.actor)
        raise HTTPException(status_code=403, detail="Solo un admin puede cambiar precios en Mercado Libre.")
    return quien


class PedidoPrecio(BaseModel):
    cuenta: str
    sku: str | None = None
    precio: float = 0
    pct: float | None = None
    salir: bool = False   # sacarla de campañas: aún sin probar en vivo, solo si se pide
    hasta: str | None = None


def _quien(quien) -> str:
    return core_actor.actual() or str(getattr(quien, "actor", "") or "")


@router.post("/precio/{item_id}/vista")
async def precio_vista(item_id: str, req: PedidoPrecio, quien=Depends(solo_admin)) -> dict:
    """Solo GET a ML: cómo está la publicación y qué pasaría. No escribe nada."""
    try:
        return await pp.vista_previa(item_id, req.cuenta, req.precio, req.pct, req.salir)
    except pp.ErrorPrecio as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/precio/{item_id}")
async def precio_aplicar(item_id: str, req: PedidoPrecio, quien=Depends(solo_admin)) -> dict:
    """ESCRIBE EN ML: precio normal y descuento individual de UNA publicación. Arranca un trabajo (1 a 5 min)
    y devuelve su id; la pantalla pregunta por él en /precio/trabajo/{id}. Queda en la bitácora."""
    try:
        pp.validar_pedido(req.precio, req.pct)
        pp._validar(item_id, req.cuenta)
    except pp.ErrorPrecio as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    q = _quien(quien)
    return pp.iniciar("aplicar", item_id, lambda pasos: pp.aplicar(item_id, req.cuenta, req.sku, req.precio, req.pct, req.salir,
                                                                    req.hasta, q, pasos))


@router.post("/precio/{item_id}/deshacer")
async def precio_deshacer(item_id: str, req: PedidoPrecio, quien=Depends(solo_admin)) -> dict:
    """ESCRIBE EN ML: la regresa a como estaba antes del último cambio hecho desde PRICING (trabajo, igual que aplicar)."""
    try:
        pp._validar(item_id, req.cuenta)
    except pp.ErrorPrecio as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    q = _quien(quien)
    return pp.iniciar("deshacer", item_id, lambda pasos: pp.deshacer(item_id, req.cuenta, req.sku, q, pasos))


@router.get("/precio/trabajo/{tid}")
async def precio_trabajo(tid: str, quien=Depends(solo_admin)) -> dict:
    """Cómo va un cambio de precio: pasos hechos, y al final el resultado o el error."""
    t = pp.trabajo(tid)
    if not t:
        raise HTTPException(status_code=404, detail="Ese cambio ya no está en memoria (¿se reinició el backend?). Revisa la publicación.")
    return t
