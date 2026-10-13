"""
publicaciones.py — Las publicaciones del catálogo para la pestaña Omnicanal.

  GET /api/publicaciones            → página filtrable + censo de cobertura
  GET /api/publicaciones/estados    → qué estados existen en cada canal
  GET /api/publicaciones/cobertura  → solo el censo, sin traer las filas
  GET  /api/publicaciones/lote?canal=      → las publicaciones de UN canal, para
                                             escogerlas en el Publicador (solo lee)
  POST /api/publicaciones/lote/desactivar  → {canal, skus}: las SACA DE LA VENTA en
                                             Temu o TikTok. Sólo admin. Inactiva, no borra.
  POST /api/publicaciones/lote/pausa       → {canal, en_pausa, motivo?}: pone o quita la
                                             PAUSA del canal. Sólo admin. Con la pausa, lo
                                             que reaparezca a la venta se apaga tras cada
                                             censo y el seguro de stock 0 no regresa nada.

LECTURA PURA, con DOS excepciones declaradas:

  1. `GET /api/publicaciones?refrescar=true` —y sólo con ese parámetro, que por
     default viene apagado— le pregunta a Mercado Libre el precio de las
     publicaciones del SKU exacto de `q` y lo guarda antes de leer
     (`services/precio_al_abrir.py`).
  2. `POST /api/publicaciones/lote/desactivar` (13-oct-2026) SÍ habla con el canal:
     saca de la venta las publicaciones que una persona escogió en el Publicador
     (`services/publicaciones_lote.py`). Sólo administradores con sesión, a lo mucho
     10 SKUs por llamada, y deja en `ops.process_log` quién apagó qué.

Todo lo demás de este router no escribe en kubera, no habla con ningún marketplace
y no toca ningún flujo vivo.

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
     observó antes del último cambio de PRECIO de la publicación, así que nadie
     sabe si sigue viva. Un cambio de stock ya no la tumba mientras la
     observación tenga menos de 48 h (v0.489.0). Esas NO se aplican —
     `precio_vigente` y el margen van contra
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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from routers.investigacion import solo_admin
from services import precio_al_abrir
from services import publicaciones_lote as lote
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


# ── Desactivar en lote (Publicador) ──────────────────────────────────────────

class LoteDesactivar(BaseModel):
    canal: str
    skus: list[str]


class LotePausa(BaseModel):
    canal: str
    en_pausa: bool
    motivo: str | None = None


async def _admin_del_lote(request: Request):
    """El mismo candado de las investigaciones (persona con sesión y rol admin),
    con un mensaje que dice de qué se trata."""
    try:
        return await solo_admin(request)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(
                status_code=403,
                detail="Desactivar publicaciones en lote es sólo para administradores del panel.") from exc
        raise HTTPException(status_code=exc.status_code,
                            detail="Inicia sesión en el panel para desactivar publicaciones.") from exc


@router.get("/lote")
def lote_listar(canal: str = Query(..., description="temu | tiktok")):
    """
    Las publicaciones de UN canal como las vio el último censo, para escogerlas en
    el Publicador: SKU, título, estado en palabras, lo que ofrece el canal, el stock
    de Woo y quién la tiene apagada (el seguro de stock 0 o esta misma pantalla).
    Sólo lee kubera: no le pregunta nada al canal. `def` y no `async def`: psycopg2
    es bloqueante (regla 11) y así corre en el threadpool.
    """
    try:
        return {**lote.listar(canal), "canales": lote.canales()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("publicaciones.lote_listar falló")
        raise HTTPException(status_code=502,
                            detail=f"No se pudieron leer las publicaciones: {exc}") from exc


@router.post("/lote/desactivar")
def lote_desactivar(datos: LoteDesactivar, quien=Depends(_admin_del_lote)):
    """
    SACA DE LA VENTA las publicaciones de esos SKUs en Temu o TikTok. INACTIVA, NO
    BORRA: son las mismas llamadas del seguro de stock 0 (`sale.status.set onsale:0`
    y `products/deactivate`). Por publicación lee en vivo, llama, relee para
    confirmar y lo anota con quién lo pidió. A lo mucho 10 SKUs por llamada: la
    pantalla manda las tandas y enseña el avance.

    Lo que se apaga desde aquí se queda apagado: no lo regresa el seguro (si era
    suya, suelta su marca). Volver a prender es otra decisión.
    """
    try:
        return lote.desactivar(datos.canal, datos.skus, actor=str(getattr(quien, "actor", "") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("publicaciones.lote_desactivar falló")
        raise HTTPException(status_code=502,
                            detail=f"No se pudo desactivar: {exc}") from exc


@router.post("/lote/pausa")
def lote_pausa(datos: LotePausa, quien=Depends(_admin_del_lote)):
    """
    Pone o quita la PAUSA de un canal (Temu o TikTok). Con la pausa puesta, al terminar
    cada censo se saca de la venta lo que haya reaparecido, y el seguro de stock 0 no
    regresa nada en ese canal. Queda quién y cuándo. Ponerla no apaga nada en el
    momento (para eso está «Desactivar») y quitarla no prende nada.
    """
    try:
        return lote.fijar_pausa(datos.canal, datos.en_pausa,
                                quien=str(getattr(quien, "actor", "") or ""),
                                motivo=datos.motivo or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("publicaciones.lote_pausa falló")
        raise HTTPException(status_code=502,
                            detail=f"No se pudo cambiar la pausa: {exc}") from exc


async def _a_hilo(fn, **kw):
    """
    Regla 11 de la casa: `supabase_db` es psycopg2, o sea BLOQUEANTE. Una
    lectura de ~8,700 filas dentro del event loop congelaría los webhooks de ML
    mientras dura. Sale a un hilo.
    """
    import asyncio
    return await asyncio.to_thread(lambda: fn(**kw))
