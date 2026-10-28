"""
precio_sugerido.py — «Sugerir precio» del Estudio (v0.686.0). Ver `services/precio_sugerido.py`.

  GET  /api/precio-sugerido/catalogo/{sku}  → GRATIS: lo que el catálogo maestro sabe del SKU (ML y Amazon) (v0.696.0)
  POST /api/precio-sugerido/completo        → $0.01–0.10, 30 s–2 min: arranca el trabajo y devuelve su `jid`
  GET  /api/precio-sugerido/trabajo/{jid}   → cómo va (y el resultado al terminar)
  GET  /api/precio-sugerido/ultimo/{sku}    → la última consulta del SKU, para no volver a pagar
  POST /api/precio-sugerido/medidas-amazon  → GRATIS: medidas del paquete de los que hicieron match en Amazon (v0.696.0)

Solo sugiere: ninguna ruta escribe precios ni medidas. Lo único que escribe es su bitácora en `ops.process_log`.
El modo rápido (`POST /rapido`) se quitó en v0.696.0.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import amazon_mercado, catalogo_precios
from services import precio_sugerido as ps

log = logging.getLogger("omnicanal.routers.precio_sugerido")
router = APIRouter(prefix="/api/precio-sugerido", tags=["precio-sugerido"])


class Entrada(BaseModel):
    """Lo que se ve en el Estudio manda; lo que falte sale del panel y de Woo."""
    sku: str = Field(min_length=1)
    titulo: str | None = None
    ml_cat_id: str | None = None
    costo: float | None = None
    imagen: str | None = None
    peso_kg: float | None = None
    margen: float = Field(default=ps.MARGEN_DEFAULT, ge=0, le=5)
    terminos: list[str] | None = None


class EntradaMedidas(BaseModel):
    sku: str = Field(min_length=1)
    asins: list[str] = Field(default_factory=list, max_length=amazon_mercado.MAX_MEDIDAS)
    q: str | None = Field(default=None, max_length=120)


@router.get("/catalogo/{sku:path}")
async def catalogo(sku: str) -> dict:
    return await asyncio.to_thread(catalogo_precios.por_sku, sku)


@router.post("/completo")
async def completo(req: Entrada) -> dict:
    return ps.arrancar_completo(req.model_dump())


@router.get("/trabajo/{jid}")
async def trabajo(jid: str) -> dict:
    e = ps.estado(jid)
    if not e:
        raise HTTPException(status_code=404, detail="Ese trabajo ya no existe: si terminó, su resultado quedó guardado; "
                                                    "vuelve a abrir el producto.")
    return e


@router.get("/ultimo/{sku:path}")
async def ultimo(sku: str) -> dict:
    r = await asyncio.to_thread(ps.ultimo, sku)
    return {"resultado": r}


@router.post("/medidas-amazon")
async def medidas_amazon(req: EntradaMedidas) -> dict:
    asins = [a.strip().upper() for a in req.asins if amazon_mercado.ES_ASIN.fullmatch(a.strip().upper())]
    if not asins and not (req.q or "").strip():
        raise HTTPException(status_code=422, detail="Sin publicaciones de Amazon con las cuales medir.")
    try:
        return await amazon_mercado.medidas(asins, (req.q or "").strip() or None)
    except amazon_mercado.SinAmazon as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("medidas de Amazon de %s fallaron", req.sku)
        raise HTTPException(status_code=502, detail=f"No se pudieron leer las medidas de Amazon: {exc}") from exc
