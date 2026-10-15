"""
conversion.py — API de Análisis › Conversión: ventas contra visitas por
publicación (Eduardo, 15-oct-2026; eligió el mapa de burbujas con la bandeja de
alertas como segunda vista).

  GET /api/analisis/conversion              → una fila por publicación con
                                              visitas, piezas, precio, stock y
                                              la medición anterior para comparar
  GET /api/analisis/conversion/{listing_id} → el detalle: cada venta por día y
                                              precio, visitas por día y calidad

Los cuadrantes y las alertas NO se calculan aquí: los umbrales los mueve quien
mira la pantalla (`frontend/lib/conversion.ts`). Este router entrega hechos.

DE DÓNDE SALE CADA COSA
-----------------------
- Visitas: `enrich.market_listing_metrics.visits_30d`, que el cron de
  Competencia refresca a diario. Es el TOTAL de una ventana móvil de 30 días que
  termina en `metrics_updated_at`, con una fila por mes. Hoy no hay serie diaria
  guardada en producción (la 0062, `market.item_mes`, solo vive en el sandbox).
- Piezas: `channel.order_items` × `channel.orders`, ligadas por `item_id` = MLM
  de la publicación. Se cuentan **en la misma ventana que la medición de
  visitas** (30 días hasta `metrics_updated_at`); si no, la conversión
  compararía periodos distintos. Canceladas = `estado_canal ilike 'cancel%'`.
- «Antes»: la medición del mes anterior en la misma tabla (su propia ventana de
  30 días) y las piezas de ESA ventana. No son «los 30 días anteriores» exactos:
  la fecha de la medición viaja en la respuesta para que la pantalla lo diga.
- Stock: FULL → `stock_full` (en FULL `stock_own` siempre llega en 0); el resto,
  `stock_own`. Precio: `price_sale` (lo cobrado) y, si no hay, `price`.

Solo Mercado Libre da visitas. Los demás canales devuelven piezas con
`visitas = null`: en la pantalla eso es «sin medición», nunca cero.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from services import competencia_ml
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.conversion")

router = APIRouter(prefix="/api/analisis", tags=["conversion"])

_CUENTAS = {"BEKURA", "SANCORFASHION"}
_ESTADOS = {"active", "paused", "under_review", "closed", "todas"}
_CANALES_SIN_VISITAS = {"amazon", "tiktok", "temu", "walmart"}
_TZ = "America/Mexico_City"


async def _fetch_all(sql: str, par: Any = None) -> list[dict[str, Any]]:
    # psycopg2 es bloqueante: siempre en un hilo (regla 11 de CLAUDE.md).
    return await asyncio.to_thread(sdb.fetch_all, sql, par)


def _f(x: Any) -> float | None:
    return None if x is None else float(x)


# ── Lista de publicaciones de Mercado Libre ──────────────────────────────────
# `distinct on (listing_id)`: una publicación con variaciones trae una fila por
# SKU (padre e hijo, 651 filas para 648 publicaciones activas el 15-oct). Se
# queda la de SKU menor, que es estable entre llamadas.
#
# La medición se elige por VALOR y no por fila (lección de la 0039): el primer
# día del mes la fila nueva llega a medias y no debe tapar a la anterior.
_SQL_ML = """
with pubs as (
  select distinct on (l.listing_id)
         l.listing_id, l.sku::text as sku, a.legacy_code as cuenta, l.situacion,
         l.logistic_type, l.price, l.price_sale, l.category_id,
         case when l.logistic_type = 'fulfillment' then l.stock_full else l.stock_own end as stock,
         pr.name as titulo_producto
    from channel.listings l
    join core.accounts a on a.id = l.account_id
    left join core.products pr on pr.sku = l.sku
   where l.canal = 'mercado_libre' and l.listing_id is not null
     and (%(cuenta)s::text is null or a.legacy_code = %(cuenta)s)
     and (%(estado)s::text = 'todas' or l.situacion = %(estado)s)
   order by l.listing_id, l.sku
),
por_mes as (
  select m.listing_id, m.periodo, max(m.visits_30d) as visitas,
         max(m.metrics_updated_at) as medido_at, max(m.title) as titulo
    from enrich.market_listing_metrics m
   where m.canal = 'mercado_libre'
     and m.listing_id in (select listing_id from pubs)
   group by 1, 2
),
med as (
  select listing_id,
         (array_agg(visitas order by periodo desc) filter (where visitas is not null))[1] as visitas,
         (array_agg(medido_at order by periodo desc) filter (where visitas is not null))[1] as visitas_at,
         (array_agg(visitas order by periodo desc) filter (where visitas is not null))[2] as visitas_ant,
         (array_agg(medido_at order by periodo desc) filter (where visitas is not null))[2] as visitas_ant_at,
         (array_agg(titulo order by periodo desc) filter (where titulo is not null))[1] as titulo
    from por_mes group by 1
),
lineas as (
  select i.item_id as listing_id, o.creado_at, i.cantidad, i.precio_unitario,
         o.estado_canal ilike 'cancel%%' as cancelada
    from channel.order_items i
    join channel.orders o on o.external_order_id = i.external_order_id and o.canal = i.canal
   where i.canal = 'mercado_libre'
     and o.creado_at >= now() - interval '100 days'
     and i.item_id in (select listing_id from pubs)
),
ven as (
  select p.listing_id,
         coalesce(sum(l.cantidad) filter (where not l.cancelada and l.creado_at >  coalesce(m.visitas_at, now()) - interval '30 days'
                                                              and l.creado_at <= coalesce(m.visitas_at, now())), 0) as piezas,
         coalesce(sum(l.cantidad * l.precio_unitario) filter (where not l.cancelada and l.creado_at > coalesce(m.visitas_at, now()) - interval '30 days'
                                                              and l.creado_at <= coalesce(m.visitas_at, now())), 0) as ingreso,
         coalesce(sum(l.cantidad) filter (where l.cancelada and l.creado_at > coalesce(m.visitas_at, now()) - interval '30 days'
                                                          and l.creado_at <= coalesce(m.visitas_at, now())), 0) as canceladas,
         sum(l.cantidad) filter (where not l.cancelada and m.visitas_ant_at is not null
                                   and l.creado_at >  m.visitas_ant_at - interval '30 days'
                                   and l.creado_at <= m.visitas_ant_at) as piezas_ant,
         coalesce(sum(l.cantidad) filter (where not l.cancelada and l.creado_at > now() - interval '14 days'), 0) as piezas_14d
    from pubs p
    left join med m on m.listing_id = p.listing_id
    left join lineas l on l.listing_id = p.listing_id
   group by p.listing_id
),
dia as (
  select listing_id,
         ((now() at time zone '{tz}')::date - (creado_at at time zone '{tz}')::date) as hace,
         sum(cantidad) as piezas
    from lineas
   where not cancelada and creado_at >= now() - interval '30 days'
   group by 1, 2
),
dia_arr as (
  select listing_id, array_agg(array[hace, piezas]) as por_dia from dia group by 1
)
select p.listing_id, p.sku, p.cuenta, p.situacion, p.logistic_type, p.price, p.price_sale,
       p.stock, p.category_id, t.name as categoria, t.path_names[1] as categoria_raiz,
       coalesce(m.titulo, p.titulo_producto) as titulo,
       m.visitas, m.visitas_at, m.visitas_ant, m.visitas_ant_at,
       v.piezas, v.ingreso, v.canceladas, v.piezas_ant, v.piezas_14d,
       da.por_dia
  from pubs p
  left join dia_arr da on da.listing_id = p.listing_id
  left join med m on m.listing_id = p.listing_id
  left join ven v on v.listing_id = p.listing_id
  left join channel.ml_category_tree t on t.category_id = p.category_id
""".format(tz=_TZ)

# ── Otros canales: solo ventas, por SKU ──────────────────────────────────────
# Amazon y TikTok no ligan la línea con la publicación por item_id (83 de 985 y
# 0 de 750 líneas el 15-oct); por SKU + canal ligan todas. Por eso aquí la fila
# es el SKU en ese canal, no la publicación.
_SQL_OTROS = """
with lineas as (
  select i.sku::text as sku, i.cuenta, i.titulo, o.creado_at, i.cantidad, i.precio_unitario,
         o.estado_canal ilike 'cancel%%' as cancelada
    from channel.order_items i
    join channel.orders o on o.external_order_id = i.external_order_id and o.canal = i.canal
   where i.canal = %(canal)s and o.creado_at >= now() - interval '60 days'
)
select sku, max(cuenta) as cuenta, max(titulo) as titulo,
       coalesce(sum(cantidad) filter (where not cancelada and creado_at > now() - interval '30 days'), 0) as piezas,
       coalesce(sum(cantidad * precio_unitario) filter (where not cancelada and creado_at > now() - interval '30 days'), 0) as ingreso,
       coalesce(sum(cantidad) filter (where cancelada and creado_at > now() - interval '30 days'), 0) as canceladas,
       coalesce(sum(cantidad) filter (where not cancelada and creado_at <= now() - interval '30 days'), 0) as piezas_ant
  from lineas
 group by sku
having sum(cantidad) filter (where not cancelada) > 0
 order by piezas desc
"""


def _fila_ml(r: dict[str, Any]) -> dict[str, Any]:
    por_dia = [0] * 30
    for hace, piezas in r.get("por_dia") or []:
        if 0 <= int(hace) < 30:
            por_dia[29 - int(hace)] = int(piezas)
    return {
        "listing_id": r["listing_id"], "sku": r["sku"], "cuenta": r["cuenta"],
        "titulo": r["titulo"], "situacion": r["situacion"],
        "full": r["logistic_type"] == "fulfillment",
        "categoria": r["categoria"], "categoria_raiz": r["categoria_raiz"],
        "precio": _f(r["price_sale"] if r["price_sale"] is not None else r["price"]),
        "precio_lista": _f(r["price"]),
        "stock": r["stock"],
        "visitas": r["visitas"], "visitas_at": r["visitas_at"],
        "visitas_ant": r["visitas_ant"], "visitas_ant_at": r["visitas_ant_at"],
        "piezas": int(r["piezas"] or 0), "ingreso": _f(r["ingreso"]) or 0.0,
        "canceladas": int(r["canceladas"] or 0),
        "piezas_ant": None if r["piezas_ant"] is None and r["visitas_ant"] is None else int(r["piezas_ant"] or 0),
        "piezas_14d": int(r["piezas_14d"] or 0),
        "piezas_por_dia": por_dia,
    }


@router.get("/conversion")
async def conversion(
    canal: str = Query("mercado_libre"),
    cuenta: str | None = Query(None),
    estado: str = Query("active"),
) -> dict[str, Any]:
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    cta = (cuenta or "").strip().upper() or None
    if cta and cta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    if estado not in _ESTADOS:
        raise HTTPException(400, f"estado inválido: {estado}")
    ahora = datetime.now(timezone.utc).isoformat()

    if canal in _CANALES_SIN_VISITAS:
        filas = await _fetch_all(_SQL_OTROS, {"canal": canal})
        return {
            "canal": canal, "tiene_visitas": False, "generado_at": ahora,
            "publicaciones": [{
                "sku": r["sku"], "cuenta": r["cuenta"], "titulo": r["titulo"],
                "piezas": int(r["piezas"]), "ingreso": _f(r["ingreso"]) or 0.0,
                "canceladas": int(r["canceladas"]), "piezas_ant": int(r["piezas_ant"]),
                "visitas": None,
            } for r in filas],
        }
    if canal != "mercado_libre":
        raise HTTPException(400, f"canal inválido: {canal}")

    filas = await _fetch_all(_SQL_ML, {"cuenta": cta, "estado": estado})
    return {
        "canal": canal, "tiene_visitas": True, "generado_at": ahora,
        "publicaciones": [_fila_ml(r) for r in filas],
    }


# ── Detalle de una publicación ───────────────────────────────────────────────
_SQL_VENTAS_DIA = """
select (o.creado_at at time zone '{tz}')::date as dia,
       round(i.precio_unitario::numeric, 2) as precio,
       o.estado_canal ilike 'cancel%%' as cancelada,
       sum(i.cantidad)::int as piezas
  from channel.order_items i
  join channel.orders o on o.external_order_id = i.external_order_id and o.canal = i.canal
 where i.canal = 'mercado_libre' and i.item_id = %(id)s
   and o.creado_at >= now() - make_interval(days => %(dias)s)
 group by 1, 2, 3
 order by 1
""".format(tz=_TZ)

_SQL_CALIDAD = """
select valor, nivel, medido_en
  from enrich.listing_health
 where canal = 'mercado_libre' and listing_id = %(id)s and metrica = 'calidad' and estado = 'medida'
 order by medido_en desc nulls last
 limit 1
"""

# Si la 0062 está aplicada (hoy: solo el sandbox), las visitas diarias se leen de
# kubera y no se le pregunta nada a ML. `to_regclass` evita que la consulta
# reviente donde el esquema no existe.
_SQL_HAY_MARKET = "select to_regclass('market.item_dia_v') is not null as hay"
_SQL_VISITAS_MARKET = """
select d.dia, d.visitas
  from market.item_dia_v d
  join market.item i on i.item_key = d.item_key
 where i.canal = 'mercado_libre' and i.externo_id = %(id)s
   and d.metrica = 'visitas'
   and d.mes >= date_trunc('month', current_date - %(dias)s)::date
   and d.dia >= current_date - %(dias)s
 order by d.dia
"""

# Las visitas de un día no cambian: con 6 h de caché, abrir el detalle diez
# veces cuesta una sola llamada a ML (mismo criterio que visitas_ml.TTL_HORAS).
_TTL_VISITAS = 6 * 3600
_cache_visitas: dict[tuple[str, int], tuple[float, list[dict[str, Any]]]] = {}


def _visitas_ml(listing_id: str, dias: int, cuenta: str) -> list[dict[str, Any]] | None:
    clave = (listing_id, dias)
    hit = _cache_visitas.get(clave)
    if hit and time.time() - hit[0] < _TTL_VISITAS:
        return hit[1]
    serie = competencia_ml.visitas_serie(listing_id, dias, cuenta)
    if serie is None:
        return None
    # El día de ML es el día de CDMX aunque la etiqueta diga T00:00:00Z, y HOY
    # llega a medias: se quita para no pintar una caída falsa al final.
    hoy = datetime.now(ZoneInfo(_TZ)).date().isoformat()
    puntos = [p for p in serie["dias"] if p["fecha"] and p["fecha"] < hoy]
    _cache_visitas[clave] = (time.time(), puntos)
    return puntos


@router.get("/conversion/{listing_id}")
async def conversion_detalle(listing_id: str, dias: int = Query(90, ge=7, le=150)) -> dict[str, Any]:
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    lid = listing_id.strip().upper()
    filas = await _fetch_all(_SQL_ML.replace("where l.canal = 'mercado_libre' and l.listing_id is not null",
                                             "where l.canal = 'mercado_libre' and l.listing_id = %(id)s"),
                             {"cuenta": None, "estado": "todas", "id": lid})
    if not filas:
        raise HTTPException(404, f"No hay una publicación de Mercado Libre {lid}")
    pub = _fila_ml(filas[0])

    ventas, calidad, hay_market = await asyncio.gather(
        _fetch_all(_SQL_VENTAS_DIA, {"id": lid, "dias": dias}),
        _fetch_all(_SQL_CALIDAD, {"id": lid}),
        _fetch_all(_SQL_HAY_MARKET),
    )

    visitas: list[dict[str, Any]] | None = None
    fuente, aviso = None, None
    if hay_market and hay_market[0]["hay"]:
        rows = await _fetch_all(_SQL_VISITAS_MARKET, {"id": lid, "dias": dias})
        if rows:
            visitas = [{"fecha": r["dia"].isoformat(), "visitas": r["visitas"]} for r in rows]
            fuente = "kubera"
    if visitas is None:
        try:
            visitas = await asyncio.to_thread(_visitas_ml, lid, dias, pub["cuenta"])
            fuente = "mercado_libre" if visitas is not None else None
        except Exception as e:  # noqa: BLE001 — sin visitas el detalle igual sirve
            log.warning("conversión: visitas de %s fallaron: %s", lid, e)
        if visitas is None:
            aviso = "Mercado Libre no devolvió las visitas por día de esta publicación."

    return {
        "publicacion": pub,
        "dias": dias,
        "desde": (datetime.now(timezone.utc) - timedelta(days=dias)).date().isoformat(),
        "ventas": [{"dia": r["dia"].isoformat(), "precio": _f(r["precio"]),
                    "cancelada": bool(r["cancelada"]), "piezas": r["piezas"]} for r in ventas],
        "visitas": visitas, "visitas_fuente": fuente, "visitas_aviso": aviso,
        "calidad": ({"valor": _f(calidad[0]["valor"]), "nivel": calidad[0]["nivel"],
                     "medido_en": calidad[0]["medido_en"]} if calidad else None),
    }
