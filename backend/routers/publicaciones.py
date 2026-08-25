"""
publicaciones.py — Las publicaciones del catálogo para la pestaña Omnicanal.

  GET /api/publicaciones            → página filtrable + censo de cobertura
  GET /api/publicaciones/estados    → qué estados existen en cada canal
  GET /api/publicaciones/cobertura  → solo el censo, sin traer las filas

LECTURA PURA. No escribe en kubera, no habla con ningún marketplace y no toca
ningún flujo vivo. Toda la interpretación (qué es "activa" en cada canal, cuándo
hay oferta, cuándo se puede calcular el margen) vive en
`services/publicaciones_panel.py`, con el porqué de cada regla y el censo que la
respalda.

Las cuatro cosas que hay que saber antes de pintar esto:

  1. El margen es PROSPECTIVO (contra el precio que la publicación cobra hoy),
     no realizado. El panel de Análisis contesta la otra pregunta.
  2. `margen_pct: null` significa "no se puede saber", con `margen_motivo` al
     lado. NUNCA se devuelve 0 por falta de datos.
  3. `oferta_estado` tiene TRES valores. `desconocida` no es `sin_oferta`.
  4. Una oferta puede estar SIN CONFIRMAR (`oferta_confirmada: false`): se
     observó antes del último cambio de la publicación, así que nadie sabe si
     sigue viva. Esas NO se aplican — `precio_vigente` y el margen van contra
     el precio de lista, y `oferta_precio` / `oferta_desc_pct` llegan en null.
     Lo observado no se pierde: viaja en `oferta_precio_visto` /
     `oferta_desc_pct_visto` para pintarlo marcado. El censo trae el conteo en
     `cobertura.ofertas_sin_confirmar` (665 de 665 el 25-ago-2026).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from services import publicaciones_panel as pp

log = logging.getLogger("omnicanal.routers.publicaciones")
router = APIRouter(prefix="/api/publicaciones", tags=["publicaciones"])


@router.get("")
async def listar(
    canal: str | None = Query(None, description="mercado_libre | amazon | tiktok | temu | walmart | general"),
    tienda: str | None = Query(None, description="legacy_code de la cuenta: BEKURA, SANCORFASHION, AMAZON…"),
    estado: str | None = Query(None, description="activa | puede_estar_activa | no_comprable | pausada | en_revision | borrador | rechazada | cerrada | sin_estado | desconocido"),
    solo_activas: bool = Query(False, description="atajo: estado in (activa, puede_estar_activa)"),
    solo_con_oferta: bool = Query(False),
    q: str | None = Query(None, description="busca en sku, título y listing_id"),
    orden: str = Query("sku", description="sku | precio_desc | precio_asc | margen_desc | margen_asc | descuento_desc | reciente"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
):
    """
    Una página de publicaciones + el censo de cobertura de ESE mismo filtro.

    El censo viaja junto a los datos a propósito: un margen promedio sin la
    banda de cobertura al lado se lee como un hecho sobre todo el catálogo, y
    hoy solo lo es sobre poco más de la mitad de Mercado Libre.
    """
    try:
        return await _a_hilo(
            pp.listar, canal=canal, cuenta=tienda, estado=estado,
            solo_activas=solo_activas, solo_con_oferta=solo_con_oferta,
            search=q, orden=orden, page=page, per_page=per_page)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("publicaciones.listar falló")
        raise HTTPException(status_code=502,
                            detail=f"No se pudo leer publicaciones: {exc}") from exc


@router.get("/estados")
async def estados(canal: str | None = Query(None)):
    """
    El censo de estados por canal, con el valor CRUDO de cada uno al lado.

    Existe para que la pestaña nunca pinte un 0 sin explicación: si un canal no
    reporta estado utilizable —Temu contesta códigos, Walmart solo publica en
    `status`— aquí sale `sin_estado`/`puede_estar_activa` con su `nota`, en vez
    de una lista vacía que se leería como "no hay publicaciones activas".
    """
    try:
        return {"canales": await _a_hilo(pp.censo_estados, canal=canal)}
    except Exception as exc:  # noqa: BLE001
        log.exception("publicaciones.estados falló")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/cobertura")
async def cobertura(canal: str | None = Query(None),
                    tienda: str | None = Query(None)):
    """El censo solo, sin las filas. Para el encabezado de la pestaña."""
    try:
        res = await _a_hilo(pp.listar, canal=canal, cuenta=tienda,
                            page=1, per_page=1)
        return res["cobertura"]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("publicaciones.cobertura falló")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _a_hilo(fn, **kw):
    """
    Regla 11 de la casa: `supabase_db` es psycopg2, o sea BLOQUEANTE. Una
    lectura de ~8,700 filas dentro del event loop congelaría los webhooks de ML
    mientras dura. Sale a un hilo.
    """
    import asyncio
    return await asyncio.to_thread(lambda: fn(**kw))
