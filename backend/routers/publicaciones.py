"""
publicaciones.py — Las publicaciones del catálogo para la pestaña Omnicanal.

  GET /api/publicaciones            → página filtrable + censo de cobertura
  GET /api/publicaciones/estados    → qué estados existen en cada canal
  GET /api/publicaciones/cobertura  → solo el censo, sin traer las filas

LECTURA PURA, con UNA excepción declarada: `GET /api/publicaciones?refrescar=true`
—y sólo con ese parámetro, que por default viene apagado— le pregunta a Mercado
Libre el precio de las publicaciones del SKU exacto de `q` y lo guarda antes de
leer (`services/precio_al_abrir.py`). Todo lo demás de este router no escribe en
kubera, no habla con ningún marketplace y no toca ningún flujo vivo.

Toda la interpretación (qué es "activa" en cada canal, cuándo hay oferta, cuándo
se puede calcular el margen) vive en `services/publicaciones_panel.py`, con el
porqué de cada regla y el censo que la respalda.

Las seis cosas que hay que saber antes de pintar esto:

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
  5. Hay TRES precios y ninguno se llama como uno esperaría (v0.262.0):

       precio_lista   lo que ML TACHA          `listings.price_base`
       precio_ml      el precio del VENDEDOR   `listings.price`
                      tras SUS campañas
       precio_vigente  lo que el comprador PAGA `listings.price_sale` si está
                       confirmado; si no, cae a `precio_ml` y viene MARCADO

     `precio_vigente_confirmado` (bool | null) dice si ese último número se
     puede creer. `null` = no aplica (canal sin capa de promoción).
     `cobertura.precio_sin_confirmar` lleva el conteo (789 de 806 activas de ML
     el 25-ago, antes de abrir `public_offers`; baja solo con los avisos).
  6. **Un margen puede venir MARCADO** — y ésta es la combinación nueva de
     v0.262.0, la que puede tumbar a quien asuma el contrato viejo:

       margen_pct     el número. NO cambia.
       margen_motivo  por qué NO hay margen. Sigue apareciendo SOLO cuando
                      `margen_pct` es null. **Esa regla no cambió.**
       margen_aviso   NUEVO. "sí hay número, pero tómalo con reserva".
                      Vocabulario cerrado; hoy un solo valor:
                      "precio_sin_confirmar". Viene CON `margen_pct` presente.
       margen_contra  NUEVO. contra qué precio se calculó:
                      "precio_cobrado" (price_sale confirmado) o
                      "precio_ml" (el techo conocido, sin confirmar).

     Medido: `price` sobreestima lo que ML cobra en 1.44x de mediana (p90 2.95,
     máx 4.85; 60 publicaciones vivas, 25-ago-2026). Por eso se marca. El trato
     es el MISMO que ya recibe el costo implausible en `frontend/lib/margen.ts`:
     ámbar y ⚠, no el rojo/verde que se lee como un hecho. El censo trae el
     conteo por canal en `cobertura.canales[].avisos` — aparte de `motivos`,
     porque una fila marcada SÍ cuenta en `con_margen`.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from services import precio_al_abrir
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
    refrescar: bool = Query(False, description="confirma contra ML el precio de las publicaciones del SKU EXACTO que venga en `q`, antes de contestar"),
):
    """
    Una página de publicaciones + el censo de cobertura de ESE mismo filtro.

    El censo viaja junto a los datos a propósito: un margen promedio sin la
    banda de cobertura al lado se lee como un hecho sobre todo el catálogo, y
    hoy solo lo es sobre poco más de la mitad de Mercado Libre.

    `refrescar=true` — LO ÚNICO QUE ESCRIBE EN ESTE ROUTER, y sólo aquí. Antes
    de leer, le pregunta a Mercado Libre el precio que cobra por las
    publicaciones del SKU **exactamente igual** a `q` y lo guarda en
    `channel.listings.price_sale`. Es lo que hace que el cajón del producto
    muestre lo que la tienda cobra en ese momento y no una foto de hace horas.

    Tres cosas que hay que saber antes de mandarlo:

      1. **Va con el SKU exacto, no con una búsqueda.** El objetivo se arma con
         `l.sku = q` (citext, o sea sin distinguir mayúsculas). Una `q` que sea
         parte de un título o de un `listing_id` refresca CERO publicaciones,
         no todas las que la búsqueda encuentre. Es a propósito: recorrer una
         lista no puede convertirse en cientos de llamadas a ML.
      2. **Nunca rompe la pantalla.** Si ML falla, tarda o no hay token, la
         lectura sigue igual con lo guardado y el bloque `refresco` dice qué
         pasó. Un cajón que no abre es peor que uno con un dato de hace una
         hora.
      3. **Lo que hay que mirar es `refresco.al_dia`** (bool): "todo lo que se
         muestra de ML para este SKU está confirmado contra ML ahora mismo".
         Cuando es `false`, los precios de ML del cajón son los guardados y hay
         que decirlo en pantalla — `precio_vigente_confirmado` y `oferta_dias`
         siguen contando la verdad publicación por publicación. `estado` trae
         el porqué con vocabulario cerrado: `ok` (se preguntó), `piso` (se
         observó hace menos de PRECIO_AL_ABRIR_PISO_MIN, no hacía falta),
         `sin_publicaciones`, `apagado`, `no_aplica`, `sin_token`, `fallo`,
         `timeout`.
    """
    try:
        refresco = await precio_al_abrir.refrescar_sku(q) if refrescar else None
        res = await _a_hilo(
            pp.listar, canal=canal, cuenta=tienda, estado=estado,
            solo_activas=solo_activas, solo_con_oferta=solo_con_oferta,
            search=q, orden=orden, page=page, per_page=per_page)
        if refresco is not None:
            res["refresco"] = refresco
        return res
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
