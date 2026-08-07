"""
fulfillment.py — Panel de reabastecimiento (CLON del tablero kubera-fulfillment
de José), leyendo DIRECTO de la BD kubera v4 — primer lector de producción.

Fuentes (todas vistas/tablas de la migración):
  channel.listings              → foto viva por listing (webhook, segundos)
  channel.sales_daily_completa  → ventas sin hueco (hist dailytrack + vivo)
  channel.restock_panel         → sugerido/semáforo (Bollinger, migración 0007)
  costing.costos_finales        → costo y precio sugerido (canal ML)
  costing.costos_validados      → dimensiones → categoría de TAMAÑO

Equivalencias vs el original (documentadas para el clon):
  STOCK ODOO   → STOCK PROPIO = DROP real (bodega Woo por SKU, listing
                 canal='general'; fuente: stock_watch_foto de Brandon v0.27.0.
                 Fallback: stock_own declarado por el marketplace).
  DÍAS ODOO    → EDAD S/VENTA (días desde la última venta registrada).
  DÍAS VENTA   → COBERTURA (stock total / venta diaria del período).
  VISITAS/CR%  → SIN DATO (daily_visits quedó fuera del alcance 2026-07-28).
  TAM          → derivada de costos_validados (lado mayor): S<30, M<60,
                 L<120, XL≥120 cm; S/C sin dimensiones.

  GET /api/fulfillment/dashboard → KPIs + conteo por cuenta + serie diaria
  GET /api/fulfillment/tabla     → filas por SKU (filtros del clon) + sparkline
  GET /api/fulfillment/detalle   → serie diaria de UN SKU (modal del sparkline)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from config import settings
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.fulfillment")
router = APIRouter(prefix="/api/fulfillment", tags=["fulfillment"])

_CUENTAS = {"BEKURA", "SANCORFASHION", "AMAZON"}
_ESTADOS = {"activa", "pausada", "no_venta"}
_TIPOS = {"full", "no_full", "mixto"}
_TAMS = {"S", "M", "L", "XL", "S/C"}
# whitelist de orden → (columna, dirección NATURAL). La entrada del usuario
# JAMÁS se interpola: solo se usa para elegir de este diccionario.
#
# La dirección natural es la que responde la pregunta útil de esa columna: en
# COBERTURA lo urgente es lo que MENOS dura, así que su default es asc. Antes
# la dirección estaba pegada a la columna y no se podía invertir — la flecha de
# la cabecera dibujaba ↓ siempre, incluso ordenando ascendente (Eduardo lo
# detectó el 30-jul). Ahora `dir` la sobreescribe y la UI dibuja la real.
_ORDEN = {
    "venta": ("venta", "desc"),
    "uds": ("uds", "desc"),
    "stock_full": ("stock_full", "desc"),
    "stock_propio": ("stock_propio", "desc"),
    "cobertura": ("cobertura_d", "asc"),
    "edad": ("edad_sin_venta_d", "desc"),
    "margen": ("margen_pct", "desc"),
    # margen NETO = ya descontados los cobros del marketplace. Ordenar ascendente
    # es el "filtro" de lo que está vendiendo mal: lo peor queda arriba.
    "margen_neto": ("margen_neto_pct", "desc"),
    "crec": ("crec_7d_pct", "desc"),
    "sugerido": ("sugerido_full", "desc"),
    "sku": ("sku", "asc"),
}
_DIRS = {"asc", "desc"}

# ── ZONA HORARIA ────────────────────────────────────────────────────────────
# `current_date` es la fecha DEL SERVIDOR, que en Railway corre en UTC — pero
# las ventas estan fechadas en horario de MEXICO (asi las construye
# channel.sales_daily). Desde las 6 de la tarde de Mexico el servidor ya cambio
# de dia y la ventana se corria: "7 dias" entregaba 6 (Eduardo lo detecto el
# 2-ago comparando contra el panel de ML). No se perdia ninguna venta: se
# preguntaba por un rango equivocado, y el total cambiaba segun la HORA a la
# que abrieras el panel.
#
# Toda consulta de este router pasa por _mx(): la pregunta queda en la misma
# zona horaria que el dato. Kubera opera en Mexico y la vista ya esta fechada
# asi en duro — una variable de configuracion seria una segunda fuente de
# verdad sobre la zona horaria, o sea otro lugar donde desincronizarse.
_HOY_MX = "(now() at time zone 'America/Mexico_City')::date"


def _mx(sql: str) -> str:
    """Cambia `current_date` (UTC) por la fecha de HOY en Mexico."""
    return sql.replace("current_date", _HOY_MX)


# CTEs compartidos del clon: listings agregados POR SKU + ventas del período.
# %(dias)s = período; %(cuenta)s = filtro de cuenta (None = todas).
_BASE = _mx("""
with l as (
  select l.sku,
         array_agg(distinct a.legacy_code order by a.legacy_code) as cuentas,
         sum(l.stock_full)                          as stock_full,
         max(l.stock_own)                           as stock_propio,
         bool_or(l.is_fulfillment)                  as tiene_full,
         bool_and(l.is_fulfillment)                 as todo_full,
         -- PRECIO DE VENTA = el de la publicación ACTIVA (contrato de José:
         -- "el precio de venta real sale de la publicación ACTIVA"). max() sobre
         -- todas MIENTE en 516 de 1,908 SKUs (27%%), $861 de diferencia promedio:
         -- ACC-0001-AZL mostraba $382 de una SANCOR pausada cuando BEKURA vende
         -- a $294. Si no hay ninguna activa no hay precio de venta: NULL, y la
         -- UI muestra el de la pausada en gris con su marca.
         min(l.price) filter (where l.situacion = 'active')  as precio,
         min(l.price)                               as precio_cualquiera,
         -- desglose por canal para la celda y el modal
         jsonb_agg(distinct jsonb_build_object(
             'cuenta', a.legacy_code, 'canal', l.canal,
             'situacion', l.situacion, 'price', l.price))
           filter (where l.price is not null)       as precios,
         -- Publicaciones de MERCADO LIBRE con su cuenta: es lo que hace falta
         -- para pedir las VISITAS (ML las da por item y con el token de su
         -- cuenta). Amazon queda fuera porque no tiene equivalente.
         jsonb_agg(distinct jsonb_build_object(
             'cuenta', a.legacy_code, 'item', l.listing_id))
           filter (where l.canal = 'mercado_libre'
                     and l.listing_id is not null)  as pubs_ml,
         max(l.updated_at)                          as precio_visto_at,
         bool_or(l.situacion = 'active')            as alguna_activa,
         bool_or(l.situacion = 'paused')            as alguna_pausada,
         max(pr.name)                               as titulo
  from channel.listings l
  join core.accounts a on a.id = l.account_id
  left join core.products pr on pr.sku = l.sku
  where l.canal in ('mercado_libre', 'amazon')
    -- Publicaciones CERRADAS fuera (2026-07-29): un listado que ya no existe
    -- no se reabastece. Ver el comentario de la migración 0007.
    and lower(coalesce(l.situacion, '')) <> 'closed'
    and (%(cuenta)s::text is null or a.legacy_code = %(cuenta)s)
  group by l.sku
),
v as (
  select sku,
         sum(units_sold)                                        as uds,
         -- Unidades SOLO de Mercado Libre: es el numerador honesto de la
         -- conversión, porque las visitas también son solo de ML. Dividir las
         -- unidades totales (que incluyen Amazon) entre visitas de ML inflaría
         -- el CR%% de cualquier SKU que venda fuerte en Amazon. (El %% va
         -- escapado: psycopg2 lee un %% suelto como marcador de parámetro.)
         sum(units_sold) filter (where canal = 'mercado_libre')  as uds_ml,
         sum(revenue)                                           as venta,
         sum(units_sold) filter (where date > current_date - 7) as u7,
         sum(units_sold) filter (where date > current_date - 14
                                   and date <= current_date - 7) as u7_prev
  from channel.sales_daily_completa
  where date > current_date - %(dias)s::int and sku is not null
    and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
  group by sku
),
ult as (
  select sku, max(date) as ultima_venta
  from channel.sales_daily_completa
  where sku is not null
    and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
  group by sku
),
dr as (
  -- DROP real: bodega Woo por SKU (canal='general', una bolsa compartida —
  -- fuente stock_watch_foto). El stock_own de ML/Amazon es lo DECLARADO.
  select sku, max(stock_own) as stock_drop
  from channel.listings
  where canal = 'general'
  group by sku
),
tam as (
  select sku,
         case
           when greatest(coalesce(largo,0), coalesce(alto,0), coalesce(ancho,0)) = 0
                then 'S/C'
           when greatest(largo, alto, ancho) < 30  then 'S'
           when greatest(largo, alto, ancho) < 60  then 'M'
           when greatest(largo, alto, ancho) < 120 then 'L'
           else 'XL'
         end as tam
  from costing.costos_validados
),
sug as (
  select sku, sum(sugerido_full)::int as sugerido_full
  from channel.restock_panel
  where (%(cuenta)s::text is null or cuenta = %(cuenta)s)
  group by sku
),
com as (
  -- Comisión REAL del marketplace en el período, POR UNIDAD (Eduardo, 5-ago).
  -- Es lo único que faltaba para pasar del margen de CATÁLOGO al margen NETO:
  -- sale de los pedidos (channel.order_items.comision es el total de la línea),
  -- no de una tasa supuesta, así que ya viene con la comisión de CADA canal.
  --
  -- Solo entran líneas con comisión > 0: Amazon todavía la registra en cero
  -- (falta Finances API) y promediarla con ML abarataría el costo. Un SKU que
  -- solo vende en Amazon se queda sin margen neto — "—" es más honesto que
  -- decir que Amazon no cobra nada.
  select i.sku,
         sum(coalesce(i.comision, 0)) / nullif(sum(i.cantidad), 0) as comision_unit
  from channel.order_items i
  join channel.orders o using (canal, cuenta, external_order_id)
  where (o.creado_at at time zone 'America/Mexico_City')::date
        > current_date - %(dias)s::int
    and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
    and coalesce(i.comision, 0) > 0
    and i.sku is not null
    and (%(cuenta)s::text is null or o.cuenta = %(cuenta)s)
  group by i.sku
),
filas as (
  select l.sku, l.cuentas, l.titulo, coalesce(t.tam, 'S/C') as tam,
         case when l.alguna_activa then 'activa'
              when l.alguna_pausada then 'pausada'
              else 'otra' end as situacion_chip,
         case when coalesce(v.uds, 0) = 0 then 'no_venta'
              when l.alguna_activa then 'activa'
              else 'pausada' end as estado,
         case when l.todo_full then 'full'
              when l.tiene_full then 'mixto'
              else 'no_full' end as tipo,
         coalesce(v.uds, 0)::int        as uds,
         coalesce(v.uds_ml, 0)::int     as uds_ml,
         l.pubs_ml,
         coalesce(v.venta, 0)           as venta,
         coalesce(l.stock_full, 0)::int as stock_full,
         coalesce(d.stock_drop, l.stock_propio, 0)::int as stock_propio,
         (current_date - u.ultima_venta)::int as edad_sin_venta_d,
         case when coalesce(v.uds, 0) > 0
              then round((coalesce(l.stock_full,0)
                          + coalesce(d.stock_drop, l.stock_propio, 0))
                         / (v.uds::numeric / %(dias)s::int), 1) end as cobertura_d,
         l.precio,
         l.precio_cualquiera,
         l.precios,
         l.precio_visto_at,
         cf.precio_sugerido,
         -- COSTO: contrato único de José (prompt de Reportes, 29-jul) —
         -- costos_validados.costo_total es la fuente de verdad; NO
         -- costos_finales.costo_unitario, que nuestro propio esquema declara
         -- "derivado". Coinciden en 3,782 de 3,877 SKUs pero costos_validados
         -- tiene 15,411 filas vs 4,353: al cambiar, la cobertura de
         -- costo/margen se duplica (caso TEC-2165-NEG-2PZ, 187 uds/30d, pasa
         -- de "—" a margen 61.8%%). OJO: los porcentajes en comentarios DENTRO
         -- del SQL van escapados (%%%%) — psycopg2 los lee como marcadores.
         coalesce(cv.costo_total, cf.costo_unitario) as costo,
         case when l.precio > 0 and coalesce(cv.costo_total, cf.costo_unitario) is not null
              then round((l.precio - coalesce(cv.costo_total, cf.costo_unitario))
                         / l.precio * 100, 1)
              end as margen_pct,
         -- MARGEN NETO: el de arriba menos los cobros de Meli. El precio de
         -- referencia es el REALIZADO cuando hubo ventas (ingreso ÷ uds, ya
         -- ponderado entre cuentas) y el publicado cuando no las hubo — el
         -- mismo criterio que usa la celda de Precio de venta, para que
         -- ordenar por esta columna y leerla no se contradigan.
         co.comision_unit,
         cf.costo_fee_envio                                as envio_unit,
         case when coalesce(cv.costo_total, cf.costo_unitario) is not null
               and co.comision_unit is not null
              then round(coalesce(cv.costo_total, cf.costo_unitario)
                         + co.comision_unit
                         + coalesce(cf.costo_fee_envio, 0), 2)
              end as costo_final,
         case when coalesce(cv.costo_total, cf.costo_unitario) is not null
               and co.comision_unit is not null
               and coalesce(v.venta / nullif(v.uds, 0), l.precio) > 0
              then round((coalesce(v.venta / nullif(v.uds, 0), l.precio)
                          - coalesce(cv.costo_total, cf.costo_unitario)
                          - co.comision_unit
                          - coalesce(cf.costo_fee_envio, 0))
                         / coalesce(v.venta / nullif(v.uds, 0), l.precio) * 100, 1)
              end as margen_neto_pct,
         case when coalesce(v.u7_prev, 0) > 0
              then round((coalesce(v.u7,0) - v.u7_prev) / v.u7_prev::numeric * 100, 0)
              when coalesce(v.u7, 0) > 0 then 100
              end as crec_7d_pct,
         coalesce(s.sugerido_full, 0) as sugerido_full
  from l
  left join v   on v.sku = l.sku
  left join ult u on u.sku = l.sku
  left join dr d  on d.sku = l.sku
  left join tam t on t.sku = l.sku
  left join sug s on s.sku = l.sku
  left join com co on co.sku = l.sku
  left join costing.costos_finales cf
         on cf.sku = l.sku and cf.canal = 'mercado_libre'
  left join costing.costos_validados cv on cv.sku = l.sku
)
""")


def _params(dias: int, cuenta: str | None) -> dict[str, Any]:
    return {"dias": dias, "cuenta": cuenta}


# ── ESTRELLAS ────────────────────────────────────────────────────────────────
# Pareto ALL-TIME (no del período): "qué SKUs sostienen el negocio". El insumo
# es channel.sales_daily_completa, que empalma la historia rescatada de
# dailytrack (27-dic-2025 → 15-jul) con el flujo vivo — sin ella este análisis
# solo vería desde el 17-jul (7% del período).
#
# El % ACUMULADO depende de por cuál métrica se ordene, así que se calculan LOS
# DOS pares (uds y $) en la misma pasada: el toggle de la UI solo cambia de
# columna, no vuelve a pedir. PROM/MES divide entre MESES ACTIVOS (los que
# tuvieron al menos una venta), no entre los meses del calendario: un SKU que
# nació en junio no se castiga con los ceros de enero.
#
# Las ventas SIN SKU quedan FUERA del ranking (no son un producto: no se pueden
# ordenar ni reabastecer), pero se devuelven aparte en `sin_sku` para que los
# totales cuadren contra la vista y no parezca que se perdieron.
_SQL_ESTRELLAS = _mx("""
with v as (
  select sku,
         sum(units_sold)::bigint                       as uds,
         sum(revenue)::numeric                         as venta,
         array_agg(distinct cuenta order by cuenta)    as cuentas,
         count(distinct date_trunc('month', date))     as meses,
         min(date)                                     as primera,
         max(date)                                     as ultima
  from channel.sales_daily_completa
  where sku is not null
    and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
  group by 1
  having sum(units_sold) > 0
),
t as (select sum(uds) as uds_t, sum(venta) as venta_t from v)
select v.sku::text                                     as sku,
       coalesce(p.name, v.sku::text)                   as titulo,
       v.cuentas,
       v.uds::int                                      as uds,
       round(v.venta, 2)                               as venta,
       v.meses::int                                    as meses,
       round(v.uds::numeric / nullif(v.meses, 0), 1)   as prom_mes_uds,
       round(v.venta / nullif(v.meses, 0), 2)          as prom_mes_venta,
       round(100 * v.uds / nullif(t.uds_t, 0), 3)      as share_uds,
       round(100 * v.venta / nullif(t.venta_t, 0), 3)  as share_venta,
       round(100 * sum(v.uds) over (order by v.uds desc, v.sku)
             / nullif(t.uds_t, 0), 3)                  as acum_uds,
       round(100 * sum(v.venta) over (order by v.venta desc, v.sku)
             / nullif(t.venta_t, 0), 3)                as acum_venta,
       v.primera::text                                 as primera,
       v.ultima::text                                  as ultima
from v
cross join t
left join core.products p on p.sku = v.sku
order by v.uds desc, v.sku
""")


@router.get("/estrellas")
async def estrellas(cuenta: str | None = Query(None)) -> dict[str, Any]:
    """Productos estrella: ranking ALL-TIME por unidades e ingresos con su
    curva de Pareto. `cuenta=None` = vista consolidada (fusiona las cuentas
    por SKU, como el tablero original de José)."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    try:
        filas = sdb.fetch_all(_SQL_ESTRELLAS, {"cuenta": cuenta})
        # Los totales se derivan de las filas ya traídas (son ~1,000): una
        # segunda consulta solo para sumarlas no aporta y paga otro viaje.
        uds = sum(f["uds"] for f in filas)
        venta = round(sum(float(f["venta"]) for f in filas), 2)
        # "Cuántos SKUs hacen el 80%": el primero que CRUZA el 80% también
        # cuenta — es el que hace falta para llegar, no el que sobra.
        def cuantos_80(campo: str) -> int:
            n = 0
            for f in sorted(filas, key=lambda x: float(x[campo])):
                n += 1
                if float(f[campo]) >= 80:
                    break
            return n if filas else 0

        periodo = {
            "desde": min((f["primera"] for f in filas), default=None),
            "hasta": max((f["ultima"] for f in filas), default=None),
        }
        sin_sku = sdb.fetch_one(
            """select coalesce(sum(units_sold), 0)::int as uds,
                      round(coalesce(sum(revenue), 0), 2) as venta
               from channel.sales_daily_completa
               where sku is null
                 and (%(cuenta)s::text is null or cuenta = %(cuenta)s)""",
            {"cuenta": cuenta})
        return {
            "ambiente": settings.app_env,
            "cuenta": cuenta,
            "periodo": periodo,
            "totales": {
                "uds": int(uds),
                "venta": venta,
                "skus": len(filas),
                "skus_80_uds": cuantos_80("acum_uds"),
                "skus_80_venta": cuantos_80("acum_venta"),
            },
            "sin_sku": sin_sku,
            "items": filas,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("estrellas fulfillment falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc


@router.get("/meta")
async def meta() -> dict[str, Any]:
    """Metadatos baratos para el layout (ambiente, disponibilidad). Sin tocar
    la BD: lo llama CADA sección de Fulfillment, así que tiene que ser gratis."""
    return {"ambiente": settings.app_env, "bd_disponible": sdb.disponible()}


@router.get("/dashboard")
async def dashboard(
    dias: int = Query(60, ge=7, le=180),
    cuenta: str | None = Query(None),
) -> dict[str, Any]:
    """KPIs del encabezado + conteos por cuenta + serie diaria para la gráfica."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    p = _params(dias, cuenta)
    try:
        kpis = sdb.fetch_one(
            _BASE + """
            select count(*)::int                                   as productos,
                   count(*) filter (where estado = 'activa')::int  as activos,
                   count(*) filter (where estado = 'activa'
                                     and tiene_full_agg)::int      as activos_full,
                   coalesce(sum(stock_full), 0)::bigint            as stock_full,
                   coalesce(sum(stock_propio), 0)::bigint          as stock_propio,
                   count(*) filter (where situacion_chip = 'activa')::int as listadas_activas,
                   count(*) filter (where situacion_chip = 'activa'
                                     and stock_full = 0
                                     and stock_propio = 0)::int    as activas_sin_stock
            from (select f.*, (tipo in ('full','mixto')) as tiene_full_agg
                  from filas f) x""", p)
        skus = sdb.fetch_one(
            """select (select count(*)::int from core.products)          as skus_catalogo,
                      (select count(distinct sku)::int from channel.listings
                        where canal in ('mercado_libre','amazon'))       as skus_listados""")
        cuentas = sdb.fetch_all(
            """select a.legacy_code as cuenta, count(*)::int as listings
               from channel.listings l join core.accounts a on a.id = l.account_id
               where l.canal in ('mercado_libre','amazon')
               group by 1 order by 1""")
        serie = sdb.fetch_all(
            _mx("""select date, sum(units_sold)::int as unidades,
                      round(sum(revenue), 2) as venta
               from channel.sales_daily_completa
               where date > current_date - %(dias)s::int
                 and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
               group by 1 order by 1"""), p)
        # UDS/$VENTA del período se derivan de la MISMA serie que pinta la
        # gráfica — un solo dato mostrado dos veces, no dos queries que
        # "deberían" coincidir. Antes salían de `filas` (solo SKUs con
        # publicación viva) y perdían la venta de publicaciones cerradas:
        # 818 uds / $326k en la ventana de 7 días del 2-ago (Eduardo detectó
        # el KPI en 2,564 con la gráfica sumando 3,243).
        kpis["uds_periodo"] = sum(int(s["unidades"]) for s in serie)
        kpis["venta_periodo"] = round(sum(float(s["venta"]) for s in serie), 2)
        pct_activas = (round(kpis["listadas_activas"] / kpis["productos"] * 100)
                       if kpis["productos"] else 0)
        pct_sin_stock = (round(kpis["activas_sin_stock"] / kpis["listadas_activas"] * 100)
                         if kpis["listadas_activas"] else 0)
        return {"ambiente": settings.app_env, "dias": dias,
                "skus": {**skus, "pct_activas": pct_activas,
                         "pct_sin_stock": pct_sin_stock},
                "kpis": kpis, "cuentas": cuentas, "serie": serie}
    except Exception as exc:  # noqa: BLE001
        log.warning("dashboard fulfillment falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc


@router.get("/detalle")
async def detalle(
    sku: str = Query(..., max_length=100),
    dias: int = Query(60, ge=7, le=180),
    cuenta: str | None = Query(None),
) -> dict[str, Any]:
    """Detalle de ventas de UN SKU para el modal del sparkline: serie diaria
    SIN huecos (días sin venta = 0), desglose por cuenta y resumen."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    p = {"sku": sku, "dias": dias, "cuenta": cuenta}
    try:
        filas = sdb.fetch_all(
            _mx("""select date, sum(units_sold)::int as uds,
                      round(sum(revenue), 2) as venta
               from channel.sales_daily_completa
               where sku = %(sku)s::citext
                 and date > current_date - %(dias)s::int
                 and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
               group by 1 order by 1"""), p)
        por_cuenta = sdb.fetch_all(
            _mx("""select cuenta, sum(units_sold)::int as uds,
                      round(sum(revenue), 2) as venta
               from channel.sales_daily_completa
               where sku = %(sku)s::citext
                 and date > current_date - %(dias)s::int
                 and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
               group by 1 order by 1"""), p)
        ultima_global = sdb.fetch_scalar(
            "select max(date) from channel.sales_daily_completa where sku = %(sku)s::citext",
            {"sku": sku})

        # Serie SIN huecos: el modal pinta un bar por día, incluidos los ceros.
        from datetime import date, timedelta
        mapa = {str(r["date"]): r for r in filas}
        hoy = date.today()
        serie = []
        for n in range(dias - 1, -1, -1):
            d = str(hoy - timedelta(days=n))
            r = mapa.get(d)
            serie.append({"date": d, "uds": int(r["uds"]) if r else 0,
                          "venta": float(r["venta"]) if r else 0.0})

        total_uds = sum(s["uds"] for s in serie)
        total_venta = round(sum(s["venta"] for s in serie), 2)
        mejor = max(serie, key=lambda s: s["venta"], default=None)
        return {
            "sku": sku, "dias": dias, "cuenta": cuenta, "serie": serie,
            "por_cuenta": por_cuenta,
            "resumen": {
                "total_uds": total_uds,
                "total_venta": total_venta,
                "venta_diaria": round(total_uds / dias, 2) if dias else 0,
                "dias_con_venta": sum(1 for s in serie if s["uds"] > 0),
                "mejor_dia": (mejor if mejor and mejor["venta"] > 0 else None),
                "ultima_venta": str(ultima_global) if ultima_global else None,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("detalle fulfillment falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc


# Resumen por canal de UN SKU: precio REALIZADO (lo que de verdad se cobró,
# ingreso/unidades de los pedidos) — no el precio de lista de la publicación,
# que con las promos de ML puede estar ~36%% arriba de lo que entra. El costo
# sigue el contrato único de José: costos_validados.costo_total primero.
_SQL_CANALES = _mx("""
with vta as (
  select s.canal, s.cuenta,
         sum(s.units_sold)::int                                   as uds,
         round(sum(s.revenue), 2)                                 as ingreso,
         round(sum(s.revenue) / nullif(sum(s.units_sold), 0), 2)  as precio_prom,
         max(s.date)::text                                        as ultima_venta
    from channel.sales_daily_completa s
   where s.sku = %(sku)s::citext
     and s.date > current_date - %(dias)s::int
   group by 1, 2
),
com as (
  -- Comisión REAL cobrada POR CANAL Y CUENTA en el período: aquí es donde se
  -- ve que el mismo producto deja distinto según dónde se venda. Solo líneas
  -- con comisión > 0 (Amazon la registra en cero hasta tener Finances API).
  select o.canal, o.cuenta,
         sum(coalesce(i.comision, 0))  as comision,
         sum(i.cantidad)::int          as uds_com
    from channel.order_items i
    join channel.orders o using (canal, cuenta, external_order_id)
   where i.sku = %(sku)s::citext
     and (o.creado_at at time zone 'America/Mexico_City')::date
         > current_date - %(dias)s::int
     and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
     and coalesce(i.comision, 0) > 0
   group by 1, 2
),
costo as (
  select coalesce(
           (select cv.costo_total from costing.costos_validados cv
             where cv.sku = %(sku)s::citext),
           (select cf.costo_unitario from costing.costos_finales cf
             where cf.sku = %(sku)s::citext and cf.canal = 'mercado_libre')
         ) as costo,
         (select cf.costo_fee_envio from costing.costos_finales cf
           where cf.sku = %(sku)s::citext and cf.canal = 'mercado_libre') as envio
)
select v.canal, v.cuenta, v.uds, v.ingreso, v.precio_prom, v.ultima_venta,
       c.costo,
       case when c.costo is not null
            then round(v.ingreso - v.uds * c.costo, 2) end as ganancia,
       case when v.precio_prom > 0 and c.costo is not null
            then round((v.precio_prom - c.costo) / v.precio_prom * 100, 1)
            end as margen_pct,
       -- y lo mismo ya con los cobros del canal encima
       round(m.comision / nullif(m.uds_com, 0), 2)         as comision_unit,
       c.envio                                             as envio_unit,
       case when c.costo is not null and m.comision is not null
            then round(c.costo + m.comision / nullif(m.uds_com, 0)
                       + coalesce(c.envio, 0), 2)
            end as costo_final,
       case when c.costo is not null and m.comision is not null
            then round(v.ingreso - v.uds * (c.costo + m.comision / nullif(m.uds_com, 0)
                                            + coalesce(c.envio, 0)), 2)
            end as ganancia_neta,
       case when v.precio_prom > 0 and c.costo is not null and m.comision is not null
            then round((v.precio_prom - (c.costo + m.comision / nullif(m.uds_com, 0)
                                         + coalesce(c.envio, 0)))
                       / v.precio_prom * 100, 1)
            end as margen_neto_pct
  from vta v
  cross join costo c
  left join com m on m.canal = v.canal and m.cuenta = v.cuenta
 order by v.uds desc
""")

# Línea de tiempo de precios: channel.listing_history registra cada cambio que
# el sync observa (desde el 17-jul-2026 — no hay historia anterior). Es la
# trazabilidad de temporadas en crudo: qué precio había y cuándo cambió.
_SQL_CAMBIOS_PRECIO = """
select h.canal,
       case when a.legacy_code in ('AMAZON','GENERAL') then '' else a.legacy_code end as cuenta,
       h.valor_anterior, h.valor_nuevo,
       h.changed_at::date::text as fecha
  from channel.listing_history h
  left join core.accounts a on a.id = h.account_id
 where h.sku = %(sku)s::citext and h.campo = 'price'
 order by h.changed_at desc
 limit 60
"""


# ── Márgenes con COSTO FINAL (requerimientos Eduardo, 4-ago) ────────────────
#
# Definiciones del negocio:
#   Costo Base  = producto + flete de importación (costos_validados.costo_total,
#                 el contrato único de José; fallback costos_finales.costo_unitario)
#   Costo Final = Costo Base + cobros de Meli por la venta:
#                 · comisión REAL por línea (channel.order_items.comision — es
#                   TOTAL de línea, verificado: 14.5-19.5%% del importe)
#                 · envío ESTIMADO por peso/dims (costos_finales.costo_fee_envio,
#                   por unidad). FASE 2 pendiente: envío real del shipment.
#   Margen %    = (ingreso − costo_final) / ingreso  ← margen sobre venta, como
#                 el resto del panel (la alternativa ganancia/costo es cambiar
#                 una línea si negocio la prefiere).
# Limitaciones declaradas: cargos FULL (facturación mensual, no por pedido)
# fuera; Amazon con comisión 0 hasta Finances API; filas sin costo van vacías.
_SQL_MARGEN_LINEAS = _mx("""
select (o.creado_at at time zone 'America/Mexico_City')::date::text as fecha,
       o.canal, o.cuenta, o.external_order_id as pedido,
       i.sku::text as sku, i.titulo,
       i.cantidad::int as cantidad,
       i.precio_unitario,
       round(i.precio_unitario * i.cantidad, 2)          as ingreso,
       i.comision                                        as comision_ml,
       case when cf.costo_fee_envio is not null
            then round(cf.costo_fee_envio * i.cantidad, 2) end as envio_estimado,
       coalesce(cv.costo_total, cf.costo_unitario)       as costo_base_unit,
       case when coalesce(cv.costo_total, cf.costo_unitario) is not null
            then round(coalesce(cv.costo_total, cf.costo_unitario) * i.cantidad, 2)
            end                                          as costo_base,
       case when coalesce(cv.costo_total, cf.costo_unitario) is not null
            then round(coalesce(cv.costo_total, cf.costo_unitario) * i.cantidad
                       + coalesce(i.comision, 0)
                       + coalesce(cf.costo_fee_envio, 0) * i.cantidad, 2)
            end                                          as costo_final,
       i.es_fulfillment                                  as full,
       coalesce(o.estado_canal, '')                      as estado
  from channel.order_items i
  join channel.orders o using (canal, cuenta, external_order_id)
  left join costing.costos_validados cv on cv.sku = i.sku
  left join costing.costos_finales  cf on cf.sku = i.sku and cf.canal = 'mercado_libre'
 where (o.creado_at at time zone 'America/Mexico_City')::date
       between %(desde)s::date and %(hasta)s::date
   -- MISMO universo que las otras dos hojas (Eduardo, 5-ago). Antes esto no
   -- filtraba cancelados ni exigía SKU, y al volverse hoja del mismo libro el
   -- archivo se contradecía: el detalle sumaba $2.32M contra $2.04M del
   -- resumen. Un pedido cancelado no es una venta — es su reverso; y una línea
   -- sin SKU no tiene costo con el cual sacarle margen. La columna `estado`
   -- sigue sirviendo: quedan paid, Shipped, partially_refunded, Pending.
   and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
   and i.sku is not null
   and (%(cuenta)s::text is null or o.cuenta = %(cuenta)s)
   and (%(canal)s::text  is null or o.canal  = %(canal)s)
 order by o.creado_at desc, i.linea
""")


# El CSV suelto de margenes se RETIRA (Eduardo, 5-ago): era el mismo dato
# que ahora viaja como hoja "Ventas" del Excel, con otro rango de fechas y
# otro boton. _SQL_MARGEN_LINEAS sigue vivo — lo consume el Excel.


_SQL_MARGEN_TOP = _mx("""
with lineas as (
  select i.sku, max(i.titulo) as titulo,
         sum(i.cantidad)::int as uds,
         sum(i.precio_unitario * i.cantidad) as ingreso,
         sum(coalesce(i.comision, 0)) as comision
    from channel.order_items i
    join channel.orders o using (canal, cuenta, external_order_id)
   where (o.creado_at at time zone 'America/Mexico_City')::date > current_date - %(dias)s::int
     and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
     and i.sku is not null
   group by i.sku
)
select l.sku::text as sku, l.titulo, l.uds,
       round(l.ingreso, 2)                              as ingreso,
       round(l.ingreso / nullif(l.uds, 0), 2)           as precio_prom,
       coalesce(cv.costo_total, cf.costo_unitario)      as costo_base,
       round(l.comision / nullif(l.uds, 0), 2)          as comision_prom,
       cf.costo_fee_envio                               as envio_prom,
       case when coalesce(cv.costo_total, cf.costo_unitario) is not null
            then round(coalesce(cv.costo_total, cf.costo_unitario)
                       + l.comision / nullif(l.uds, 0)
                       + coalesce(cf.costo_fee_envio, 0), 2)
            end                                         as costo_final
  from lineas l
  left join costing.costos_validados cv on cv.sku = l.sku
  left join costing.costos_finales  cf on cf.sku = l.sku and cf.canal = 'mercado_libre'
 order by l.uds desc
 limit %(limite)s
""")


@router.get("/margenes-top")
async def margenes_top(
    dias: int = Query(30, ge=7, le=180),
    limite: int = Query(10, ge=3, le=50),
) -> dict[str, Any]:
    """Top de SKUs más vendidos con precio promedio realizado, Costo Base,
    cobros de Meli y margen sobre COSTO FINAL (req 1 — tarjeta de Omnicanal)."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    try:
        filas = sdb.fetch_all(_SQL_MARGEN_TOP, {"dias": dias, "limite": limite})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc
    for f in filas:
        pp, cfin = f.get("precio_prom"), f.get("costo_final")
        if pp and cfin is not None:
            f["ganancia_unit"] = round(float(pp) - float(cfin), 2)
            f["margen_pct"] = round((float(pp) - float(cfin)) / float(pp) * 100, 1)
        else:
            f["ganancia_unit"] = None
            f["margen_pct"] = None
    return {"dias": dias, "items": filas,
            "nota_envio": "envío estimado por peso/dimensiones — el real llega en fase 2"}


# ── MÁRGENES REALES (fase 0) ─────────────────────────────────────────────────
# "Márgenes en Análisis: 10 SKUs más vendidos POR CUENTA, margen sobre el Costo
# Final con TODOS los cobros de Meli" (Eduardo, 6-ago). La diferencia contra
# /margenes-top: el ENVÍO ya no es el estimado de costing (que mentía en las
# dos direcciones — ver services/envio_real.py), es lo que ML cobró de verdad
# por cada embarque, consultado a su API y cacheado en MySQL. La comisión ya
# era real (sale_fee de los pedidos); el precio también (ingreso ÷ unidades).
# Fase 1 (persistir el envío en channel.order_shipments) queda a decisión de
# Eduardo — este endpoint solo cambiaría de dónde lee.

_SQL_MARGEN_REAL_TOP = _mx("""
with est as (
  -- Estado de la publicación del SKU en ESA cuenta (Eduardo, 6-ago: "que se
  -- vean pausadas o si está activa"). Una cuenta puede tener más de una
  -- publicación del mismo SKU: manda la activa si existe. Las cerradas no
  -- cuentan — un listado que ya no existe no describe el estado de hoy.
  --
  -- Aquí también salen los precios de la publicación: `price` es lo que ve el
  -- comprador y `price_base` el de LISTA. Que difieran significa promoción
  -- montada (Malla Sombra: lista $960, venta $355 — el margen malo era
  -- decisión comercial, no misterio). Cero llamadas a ML: ya está en la BD.
  select a.legacy_code as cuenta, l.sku,
         case when bool_or(l.situacion = 'active') then 'activa'
              when bool_or(l.situacion = 'paused') then 'pausada'
              else 'otra' end                                 as estado,
         min(l.price) filter (where l.situacion = 'active')   as precio_activo,
         min(l.price)                                         as precio_cualquiera,
         max(l.price_base)                                    as precio_lista,
         -- Los item_id sirven para pedir las VISITAS: ML las da por
         -- publicación, no por SKU (services/visitas_ml.py).
         array_agg(distinct l.listing_id)
           filter (where l.listing_id is not null)            as listing_ids
    from channel.listings l
    join core.accounts a on a.id = l.account_id
   where l.canal = 'mercado_libre'
     and lower(coalesce(l.situacion, '')) <> 'closed'
   group by 1, 2
),
lineas as (
  select o.cuenta, i.sku, max(i.titulo) as titulo,
         sum(i.cantidad)::int as uds,
         sum(i.precio_unitario * i.cantidad) as ingreso,
         sum(coalesce(i.comision, 0)) as comision,
         sum(i.cantidad) filter (where coalesce(i.comision, 0) > 0)::int as uds_com
    from channel.order_items i
    join channel.orders o using (canal, cuenta, external_order_id)
   where o.canal = 'mercado_libre'
     and (o.creado_at at time zone 'America/Mexico_City')::date > current_date - %(dias)s::int
     and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
     and i.sku is not null
   group by 1, 2
),
top as (
  -- El filtro de estado va ANTES de numerar: pedir "activas" debe dar el top 10
  -- DE LAS ACTIVAS, no las que sobrevivan de un top 10 mixto.
  select l.*, e.estado, e.precio_activo, e.precio_cualquiera, e.precio_lista,
         e.listing_ids,
         row_number() over (partition by l.cuenta
                            order by l.uds desc, l.ingreso desc) as rn
    from lineas l
    left join est e on e.cuenta = l.cuenta and e.sku = l.sku
   where %(estado)s::text is null or coalesce(e.estado, 'otra') = %(estado)s
)
select t.cuenta, t.sku::text as sku, t.titulo, t.uds,
       round(t.ingreso, 2)                         as ingreso,
       round(t.comision / nullif(t.uds_com, 0), 2) as comision_unit,
       coalesce(t.estado, 'otra')                  as estado,
       coalesce(t.precio_activo, t.precio_cualquiera) as precio_pub,
       t.precio_lista,
       t.listing_ids,
       coalesce(cv.costo_total, cf.costo_unitario) as costo_base,
       cf.costo_fee_envio                          as envio_estimado
  from top t
  left join costing.costos_validados cv on cv.sku = t.sku
  left join costing.costos_finales  cf on cf.sku = t.sku and cf.canal = 'mercado_libre'
 where t.rn <= %(limite)s
 order by t.cuenta, t.uds desc
""")

# Líneas de los SKUs del top (para saber qué órdenes consultar y cuántas
# piezas del SKU van en cada una).
_SQL_MARGEN_REAL_LINEAS = _mx("""
select o.cuenta, o.external_order_id, i.sku::text as sku, sum(i.cantidad)::int as uds
  from channel.order_items i
  join channel.orders o using (canal, cuenta, external_order_id)
  join unnest(%(cuentas)s::text[], %(skus)s::text[]) as t(c, s)
    on t.c = o.cuenta and t.s = i.sku::text
 where o.canal = 'mercado_libre'
   and (o.creado_at at time zone 'America/Mexico_City')::date > current_date - %(dias)s::int
   and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
 group by 1, 2, 3
""")

# Piezas TOTALES por orden (cualquier SKU): el cobro de envío es por EMBARQUE,
# así que en un carrito mixto se prorratea por unidades — sin esto, dos SKUs
# del top en el mismo carrito contarían el mismo envío dos veces.
_SQL_MARGEN_REAL_ORDENES = """
select o.cuenta, o.external_order_id, sum(i.cantidad)::int as uds_orden
  from channel.order_items i
  join channel.orders o using (canal, cuenta, external_order_id)
 where o.canal = 'mercado_libre'
   and o.external_order_id = any(%(ids)s::text[])
 group by 1, 2
"""

_ESTADOS_PUB = {"activa", "pausada"}


@router.get("/margenes-reales")
async def margenes_reales(
    dias: int = Query(30, ge=7, le=90),
    limite: int = Query(10, ge=3, le=20),
    presupuesto: int = Query(250, ge=0, le=500),
    estado: str | None = Query(None, description="activa|pausada; omitido = ambas"),
) -> dict[str, Any]:
    """Top de SKUs más vendidos POR CUENTA con margen sobre Costo Final y los
    tres cobros de Meli REALES: comisión (pedidos), envío (API de shipments,
    caché MySQL) y el precio realizado. `estado` filtra por la situación de la
    publicación ANTES de cortar el top. `pendientes` > 0 significa que el caché
    de envíos sigue llenándose — el frontend refresca hasta que llegue a 0."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if estado and estado not in _ESTADOS_PUB:
        raise HTTPException(400, f"estado inválido: {estado}")
    try:
        top = sdb.fetch_all(_SQL_MARGEN_REAL_TOP,
                            {"dias": dias, "limite": limite, "estado": estado})
        pares_cs = [(f["cuenta"], f["sku"]) for f in top]
        lineas = sdb.fetch_all(_SQL_MARGEN_REAL_LINEAS, {
            "dias": dias,
            "cuentas": [c for c, _ in pares_cs],
            "skus": [s for _, s in pares_cs]}) if pares_cs else []
        ids = sorted({str(l["external_order_id"]) for l in lineas})
        ordenes = sdb.fetch_all(_SQL_MARGEN_REAL_ORDENES, {"ids": ids}) if ids else []
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc

    # Envío real: completar el caché (hasta `presupuesto` consultas) y leerlo.
    pares_orden = [(l["cuenta"], str(l["external_order_id"])) for l in lineas]
    pares_orden = sorted(set(pares_orden))
    consultadas = 0
    costos: dict[tuple[str, str], dict[str, Any]] = {}
    if getattr(settings, "mysql_enabled", True) and pares_orden:
        from services import envio_real
        try:
            if presupuesto:
                consultadas = await envio_real.completar(pares_orden, presupuesto)
            costos = envio_real.leer(pares_orden)
        except Exception as exc:  # noqa: BLE001
            log.warning("envío real no disponible: %s", exc)

    # VISITAS de cada publicación (ML las da por item, no por SKU) para poder
    # sacar la conversión: unidades vendidas ÷ visitas, ambas del MISMO período.
    # Solo Mercado Libre — Amazon no tiene equivalente por esta vía.
    pares_pub = sorted({(f["cuenta"], str(i))
                        for f in top for i in (f["listing_ids"] or [])})
    visitas: dict[str, dict[str, Any]] = {}
    if getattr(settings, "mysql_enabled", True) and pares_pub:
        from services import visitas_ml
        try:
            if presupuesto:
                await visitas_ml.completar(pares_pub, dias)
            visitas = visitas_ml.leer([i for _, i in pares_pub], dias)
        except Exception as exc:  # noqa: BLE001
            log.warning("visitas no disponibles: %s", exc)

    uds_orden = {(o["cuenta"], str(o["external_order_id"])): int(o["uds_orden"] or 0)
                 for o in ordenes}
    envio_acum: dict[tuple[str, str], float] = {}
    uds_cub: dict[tuple[str, str], int] = {}
    uds_sin: dict[tuple[str, str], int] = {}
    for l in lineas:
        ko = (l["cuenta"], str(l["external_order_id"]))
        ks = (l["cuenta"], l["sku"])
        fila = costos.get(ko)
        if fila and fila.get("costo_vendedor") is not None:
            total = uds_orden.get(ko) or int(l["uds"])
            parte = float(fila["costo_vendedor"]) * int(l["uds"]) / max(total, 1)
            envio_acum[ks] = envio_acum.get(ks, 0.0) + parte
            uds_cub[ks] = uds_cub.get(ks, 0) + int(l["uds"])
        else:
            uds_sin[ks] = uds_sin.get(ks, 0) + int(l["uds"])

    cuentas: dict[str, list[dict[str, Any]]] = {}
    pendientes_total = 0
    for f in top:
        ks = (f["cuenta"], f["sku"])
        uds = int(f["uds"] or 0)
        precio = round(float(f["ingreso"]) / uds, 2) if uds else None
        cub, sin = uds_cub.get(ks, 0), uds_sin.get(ks, 0)
        pendientes_total += sin
        envio_u = round(envio_acum[ks] / cub, 2) if cub else None
        costo = None if f["costo_base"] is None else float(f["costo_base"])
        com = None if f["comision_unit"] is None else float(f["comision_unit"])
        fila: dict[str, Any] = {
            "sku": f["sku"], "titulo": f["titulo"], "uds": uds,
            "ingreso": float(f["ingreso"]), "precio_prom": precio,
            "costo_base": costo, "comision_unit": com,
            "envio_unit": envio_u,
            "envio_estimado": None if f["envio_estimado"] is None
                              else float(f["envio_estimado"]),
            "cobertura_envio_pct": round(cub / uds * 100) if uds else 0,
            "uds_sin_envio": sin,
            # Situación de la publicación en ESTA cuenta: 'activa', 'pausada' u
            # 'otra' (incluye el SKU que ya no tiene publicación viva).
            "estado": f["estado"],
            "precio_pub": None if f["precio_pub"] is None else float(f["precio_pub"]),
            "precio_lista": None if f["precio_lista"] is None else float(f["precio_lista"]),
        }
        # Visitas: se suman las publicaciones del SKU en ESA cuenta. `dias_datos`
        # es cuántos días trajo ML de verdad — la ventana no siempre viene
        # completa, y presumir 30 días falsearía la conversión.
        ids_pub = [str(i) for i in (f["listing_ids"] or [])]
        vis = [visitas.get(i) for i in ids_pub]
        listas = [v for v in vis if v and v.get("visitas") is not None]
        # Todo o nada, igual que en la tabla: con una medición a medias el
        # porcentaje sale falso (ver _visitas_en_filas).
        if ids_pub and len(listas) == len(ids_pub):
            total_vis = sum(int(v["visitas"]) for v in listas)
            fila["visitas"] = total_vis
            fila["visitas_dias"] = max((int(v["dias_datos"] or 0) for v in listas),
                                       default=None) or None
            fila["cr_pct"] = round(uds / total_vis * 100, 1) if total_vis else None
        else:
            fila["visitas"] = fila["visitas_dias"] = fila["cr_pct"] = None
        if precio and costo is not None and com is not None and envio_u is not None:
            cfinal = round(costo + com + envio_u, 2)
            fila["costo_final"] = cfinal
            fila["ganancia_unit"] = round(precio - cfinal, 2)
            fila["margen_pct"] = round((precio - cfinal) / precio * 100, 1)
            fila["ganancia_total"] = round((precio - cfinal) * uds, 2)
        else:
            fila["costo_final"] = fila["ganancia_unit"] = None
            fila["margen_pct"] = fila["ganancia_total"] = None
        cuentas.setdefault(f["cuenta"], []).append(fila)

    return {
        "dias": dias,
        "estado": estado,
        "cuentas": [{"cuenta": c, "filas": filas} for c, filas in sorted(cuentas.items())],
        "pendientes": pendientes_total,
        "consultadas": consultadas,
        "nota": "envío = cobro real de ML por embarque, prorrateado por unidad "
                "en carritos mixtos; no incluye cargos de almacenamiento FULL",
    }


@router.get("/canales")
async def resumen_canales(
    sku: str = Query(..., max_length=100),
    dias: int = Query(30, ge=7, le=180),
) -> dict[str, Any]:
    """Resumen por canal de un SKU (modal de Precio/Margen): unidades, ingreso,
    precio promedio REALIZADO, ganancia y margen por canal + promedio global
    ponderado + historial de cambios de precio de la publicación."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    p = {"sku": sku, "dias": dias}
    try:
        canales = sdb.fetch_all(_SQL_CANALES, p)
        cambios = sdb.fetch_all(_SQL_CAMBIOS_PRECIO, {"sku": sku})
        uds = sum(int(c["uds"]) for c in canales)
        ingreso = round(sum(float(c["ingreso"]) for c in canales), 2)
        costo = next((float(c["costo"]) for c in canales
                      if c.get("costo") is not None), None)
        precio_prom = round(ingreso / uds, 2) if uds else None
        margen_prom = (round((precio_prom - costo) / precio_prom * 100, 1)
                       if precio_prom and costo is not None else None)
        # COSTO FINAL global: comisión ponderada por lo vendido en cada canal
        # (no el promedio simple — vender 100 en BEKURA y 2 en SANCOR no son
        # dos comisiones que pesen igual). El envío es uno por SKU.
        con_com = [c for c in canales if c.get("comision_unit") is not None]
        uds_com = sum(int(c["uds"]) for c in con_com)
        comision_unit = (round(sum(float(c["comision_unit"]) * int(c["uds"])
                                   for c in con_com) / uds_com, 2)
                         if uds_com else None)
        envio_unit = next((float(c["envio_unit"]) for c in canales
                           if c.get("envio_unit") is not None), None)
        costo_final = (round(costo + comision_unit + (envio_unit or 0), 2)
                       if costo is not None and comision_unit is not None else None)
        margen_neto = (round((precio_prom - costo_final) / precio_prom * 100, 1)
                       if precio_prom and costo_final is not None else None)
        return {
            "sku": sku, "dias": dias, "canales": canales,
            "global": {"uds": uds, "ingreso": ingreso,
                       "precio_prom": precio_prom, "costo": costo,
                       "margen_prom": margen_prom,
                       "ganancia": (round(ingreso - uds * costo, 2)
                                    if costo is not None else None),
                       "comision_unit": comision_unit, "envio_unit": envio_unit,
                       "costo_final": costo_final, "margen_neto": margen_neto,
                       "ganancia_neta": (round(ingreso - uds * costo_final, 2)
                                         if costo_final is not None else None)},
            "cambios_precio": cambios,
            # la historia de precios existe desde esta fecha; antes no hay registro
            "historia_desde": "2026-07-17",
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("resumen canales falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc


async def _visitas_en_filas(items: list[dict[str, Any]], dias: int,
                            presupuesto: int) -> None:
    """
    Agrega `visitas`, `visitas_dias` y `cr_pct` a las filas de la tabla.

    Las visitas las da ML por PUBLICACIÓN (services/visitas_ml.py), así que se
    suman las publicaciones de ML del SKU. La conversión se calcula con
    `uds_ml`, NO con las unidades totales: si un SKU vende también en Amazon,
    dividir sus ventas completas entre visitas de solo Mercado Libre daría un
    CR% inflado que no significa nada.
    """
    if not getattr(settings, "mysql_enabled", True):
        return
    pares: set[tuple[str, str]] = set()
    for f in items:
        for p in (f.get("pubs_ml") or []):
            if p.get("cuenta") and p.get("item"):
                pares.add((p["cuenta"], str(p["item"])))
    if not pares:
        return
    try:
        from services import visitas_ml
        if presupuesto:
            await visitas_ml.completar(sorted(pares), dias, presupuesto)
        medidas = visitas_ml.leer([i for _, i in pares], dias)
    except Exception as exc:  # noqa: BLE001
        log.warning("visitas no disponibles en la tabla: %s", exc)
        return
    for f in items:
        pubs = [p for p in (f.get("pubs_ml") or []) if p.get("item")]
        vis = [medidas.get(str(p["item"])) for p in pubs]
        listas = [v for v in vis if v and v.get("visitas") is not None]
        # TODO O NADA. Un SKU publicado en las dos cuentas tiene dos
        # mediciones; si solo llegó una, sumar esa mitad y dividirla entre las
        # unidades COMPLETAS da un porcentaje absurdo — MUE-0163-TEL llegó a
        # mostrar "209 visitas · 378.5%" teniendo 13,122 visitas reales. Hasta
        # que estén todas, la celda dice "—" y la siguiente carga la completa.
        if pubs and len(listas) == len(pubs):
            total = sum(int(v["visitas"]) for v in listas)
            uds_ml = int(f.get("uds_ml") or 0)
            f["visitas"] = total
            f["visitas_dias"] = max((int(v["dias_datos"] or 0) for v in listas),
                                    default=None) or None
            f["cr_pct"] = round(uds_ml / total * 100, 1) if total else None
        else:
            f["visitas"] = f["visitas_dias"] = f["cr_pct"] = None


@router.get("/tabla")
async def tabla(
    dias: int = Query(60, ge=7, le=180),
    cuenta: str | None = Query(None),
    estado: str | None = Query(None),
    tipo: str | None = Query(None),
    tam: str | None = Query(None),
    q: str | None = Query(None, max_length=80),
    orden: str = Query("venta"),
    dir: str | None = Query(None, description="asc|desc; omitido = natural"),
    limit: int = Query(50, ge=10, le=200),
    offset: int = Query(0, ge=0),
    # 150 alcanza para una página de 50 filas (≈2 publicaciones por SKU) en una
    # sola carga; con menos, la mitad de las filas se quedaría sin medir y la
    # regla de "todo o nada" las dejaría en blanco hasta el siguiente refresco.
    visitas: int = Query(150, ge=0, le=500,
                         description="cuántas publicaciones medir por carga (0 = solo caché)"),
) -> dict[str, Any]:
    """Filas por SKU (agregado de cuentas) + sparkline 14 d por fila."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    if estado and estado not in _ESTADOS:
        raise HTTPException(400, f"estado inválido: {estado}")
    if tipo and tipo not in _TIPOS:
        raise HTTPException(400, f"tipo inválido: {tipo}")
    if tam and tam not in _TAMS:
        raise HTTPException(400, f"tam inválido: {tam}")
    if dir and dir not in _DIRS:
        raise HTTPException(400, f"dir inválida: {dir}")
    p = _params(dias, cuenta)
    cond, extra = ["true"], {}
    if estado:
        cond.append("estado = %(estado)s"); extra["estado"] = estado
    if tipo:
        cond.append("tipo = %(tipo)s"); extra["tipo"] = tipo
    if tam:
        cond.append("tam = %(tam)s"); extra["tam"] = tam
    if q:
        cond.append("(sku::text ilike %(q)s or titulo ilike %(q)s)")
        extra["q"] = f"%{q}%"
    where = " and ".join(cond)
    col, dir_natural = _ORDEN.get(orden, _ORDEN["venta"])
    # `nulls last` en AMBAS direcciones: un SKU sin margen no es "el de menor
    # margen", es uno del que no sabemos — va al final se ordene como se ordene.
    orden_sql = f"{col} {dir or dir_natural} nulls last"
    try:
        total = sdb.fetch_scalar(
            _BASE + f"select count(*) from filas where {where}", {**p, **extra})
        items = sdb.fetch_all(
            _BASE + f"""select * from filas where {where}
                        order by {orden_sql}, sku limit %(limit)s offset %(offset)s""",
            {**p, **extra, "limit": limit, "offset": offset})
        # Sparkline: unidades por día (14 d) SOLO de los SKUs de esta página.
        if items:
            spark = sdb.fetch_all(
                _mx("""select sku, date, sum(units_sold)::int as u
                   from channel.sales_daily_completa
                   where date > current_date - 14 and sku = any(%(skus)s::citext[])
                     and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
                   group by 1, 2"""),
                {"skus": [str(i["sku"]) for i in items], "cuenta": cuenta})
            from collections import defaultdict
            from datetime import date, timedelta
            por_sku: dict[str, dict] = defaultdict(dict)
            for r in spark:
                por_sku[str(r["sku"]).lower()][str(r["date"])] = r["u"]
            hoy = date.today()
            fechas = [str(hoy - timedelta(days=n)) for n in range(13, -1, -1)]
            for i in items:
                m = por_sku.get(str(i["sku"]).lower(), {})
                i["spark"] = [m.get(f, 0) for f in fechas]
            await _visitas_en_filas(items, dias, visitas)
        return {"total": int(total or 0), "items": items,
                "limit": limit, "offset": offset, "dias": dias,
                "orden": orden, "dir": dir or dir_natural}
    except Exception as exc:  # noqa: BLE001
        log.warning("tabla fulfillment falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc


# ── VENTAS POR CATEGORÍA ─────────────────────────────────────────────────────
# Réplica del reporte ventas_por_categoria de José (xlsx del 19-jul) contra la
# BD kubera, en vivo y con el ÁRBOL COMPLETO: el xlsx se detiene en 4 niveles;
# channel.categories trae la ruta entera (hasta 7). El endpoint devuelve las
# HOJAS con su ruta y la UI arma el árbol con acumulados por nivel — así un
# solo query sirve para cualquier profundidad.
#
# La taxonomía es de ML pero se aplica POR SKU (channel.product_category), así
# que las ventas de Amazon también entran. Publicaciones/activas se cuentan
# sobre el catálogo listado completo de cada hoja, no solo lo vendido.
#
# FUENTE: LOS PEDIDOS (Eduardo, 5-ago). Antes esto leía
# channel.sales_daily_completa y el Excel leía los pedidos, así que la página y
# su propio reporte no cuadraban — en el sandbox se separaban casi al doble
# ($4.02M contra $2.04M), porque sales_daily_completa empalma el histórico
# rescatado de dailytrack. Ahora las dos leen lo mismo.
#
# Lo que se gana: el margen solo puede salir de los pedidos (es donde vive la
# comisión REAL de Mercado Libre), y la página cuadra con la pestaña VENTAS,
# que también es 100%% pedidos.
# Lo que se pierde: la venta anterior al backfill de channel.orders. Esta vista
# ya no ve el histórico de dailytrack — para eso está Estrellas, que sigue
# leyendo la serie completa a propósito.


# ── Consultas COMPARTIDAS: la página de Categorías y su Excel ────────────────
#
# Una sola familia, una sola fuente: los PEDIDOS. Antes la página leía
# channel.sales_daily_completa y el Excel leía los pedidos — la página y su
# propio reporte no cuadraban.
#
# El filtro de categoría es un parámetro (%%(categoria_id)s NULL = todas), así
# que el mismo query sirve para el desglose de UNA hoja del árbol en la UI y
# para el libro completo. Dos consultas que deben dar lo mismo son dos
# consultas que se van a desincronizar.
_SQL_CAT_LINEAS = """
  select i.item_id, o.cuenta, i.sku, i.cantidad, i.precio_unitario,
         i.comision, i.titulo,
         (o.creado_at at time zone 'America/Mexico_City')::date as fecha
    from channel.order_items i
    join channel.orders o using (canal, cuenta, external_order_id)
   where (o.creado_at at time zone 'America/Mexico_City')::date
         between %(desde)s::date and %(hasta)s::date
     and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
     and i.sku is not null
     and (%(cuenta)s::text is null or o.cuenta = %(cuenta)s)
"""

# Una fila por publicación vendida, con su categoría y su costo. El costo se
# multiplica por las unidades: es el costo de LO VENDIDO, no el unitario.
_SQL_CAT_PUBS = _mx(f"""
with pc as (
  select sku, category_id from channel.product_category
  where channel_id = 'mercado_libre'
),
lin as ({_SQL_CAT_LINEAS})
select pc.category_id::text            as category_id,
       l.item_id, l.cuenta,
       max(l.sku::text)                as sku,
       sum(l.cantidad)::int            as uds,
       round(sum(l.precio_unitario * l.cantidad), 2)      as venta,
       round(sum(coalesce(l.comision, 0)), 2)             as comision,
       round(coalesce(max(cf.costo_fee_envio), 0) * sum(l.cantidad), 2) as envio,
       case when coalesce(max(cv.costo_total), max(cf.costo_unitario)) is not null
            then round(coalesce(max(cv.costo_total), max(cf.costo_unitario))
                       * sum(l.cantidad), 2) end          as costo_base,
       min(l.fecha)::text              as primera_venta,
       max(l.fecha)::text              as ultima_venta,
       max(ls.situacion)               as situacion,
       max(ls.price)                   as precio,
       coalesce(max(l.titulo), max(p.name)) as titulo
  from lin l
  join pc on pc.sku = l.sku
         and (%(categoria_id)s::text is null or pc.category_id = %(categoria_id)s)
  left join channel.listings ls on ls.listing_id = l.item_id
  left join core.products p on p.sku = l.sku
  left join costing.costos_validados cv on cv.sku = l.sku
  left join costing.costos_finales  cf on cf.sku = l.sku and cf.canal = 'mercado_libre'
 group by pc.category_id, l.item_id, l.cuenta
""")

# Las hojas del árbol. Conserva el FULL OUTER JOIN del original: una categoría
# con catálogo pero sin venta en el período también viaja (uds 0).
_SQL_CAT_HOJAS = _mx(f"""
with pc as (
  select pc.sku, pc.category_id,
         coalesce(nullif(trim(c.path), ''), c.name, 'Sin categoría') as ruta
  from channel.product_category pc
  join channel.categories c
    on c.category_id = pc.category_id and c.channel_id = pc.channel_id
),
lin as ({_SQL_CAT_LINEAS}),
porsku as (
  select l.sku,
         sum(l.cantidad)::int                          as uds,
         sum(l.precio_unitario * l.cantidad)           as venta,
         sum(coalesce(l.comision, 0))                  as comision,
         coalesce(max(cf.costo_fee_envio), 0) * sum(l.cantidad) as envio,
         case when coalesce(max(cv.costo_total), max(cf.costo_unitario)) is not null
              then coalesce(max(cv.costo_total), max(cf.costo_unitario))
                   * sum(l.cantidad) end               as costo_base
    from lin l
    left join costing.costos_validados cv on cv.sku = l.sku
    left join costing.costos_finales  cf on cf.sku = l.sku and cf.canal = 'mercado_libre'
   group by l.sku
),
porsku_c as (
  -- `creible` = el costo del producto no supera 3x lo vendido. Arriba de eso
  -- ya no es una decision comercial (liquidar, error de precio): es captura.
  select p.*, (p.costo_base is not null and p.costo_base <= p.venta * 3) as creible
    from porsku p
),
ventas_cat as (
  -- El bloque de MARGEN se restringe a los SKUs con costo capturado. Sin ese
  -- filter la categoría cargaba la comisión y el envío de productos cuyo costo
  -- no conocemos: el costo final salía inflado y `venta_con_costo` contaba la
  -- venta entera de la categoría, no la medible. Con 4,968 líneas eso separaba
  -- al Resumen de la hoja Ventas en $15.7k de costo y $83.3k de venta.
  --
  -- Y ADEMAS se excluyen los COSTOS INCREIBLES (Eduardo, 6-ago). Un SKU cuyo
  -- costo capturado supera 3 veces lo que vendio no es una perdida: es un dato
  -- roto — hay 119 asi en 60 dias, 32 de ellos arriba de 3x (TEC-0406-AZL:
  -- vende en $269 con costo $30,058). Promediados dentro de la rama arrastran
  -- a los sanos: Herramientas mostraba -173.9%% por unos pocos. Al excluirlos,
  -- el porcentaje de la rama vuelve a ser legible y `venta_con_costo` baja,
  -- que es justo la senal de que la foto esta incompleta (la UI lo marca con
  -- asterisco). Mismo umbral que costoImplausible() en el frontend.
  select coalesce(pc.ruta, 'Sin categoría') as ruta, pc.category_id,
         sum(v.uds)::int                    as uds,
         round(sum(v.venta), 2)             as venta,
         round(sum(v.comision) filter (where v.creible), 2) as comision,
         round(sum(v.envio)    filter (where v.creible), 2) as envio,
         round(sum(v.costo_base) filter (where v.creible), 2) as costo_base,
         round(sum(v.venta)    filter (where v.creible), 2) as venta_con_costo,
         count(distinct v.sku)::int         as skus
    from porsku_c v left join pc on pc.sku = v.sku
   group by 1, 2
),
cuentas_cat as (
  -- Desglose por cuenta de cada hoja del árbol (la UI lo pinta al lado). Va en
  -- su propio CTE porque `porsku` agrega por SKU para poder cruzar el costo,
  -- y aquí hace falta el corte por cuenta.
  select ruta, category_id,
         jsonb_agg(jsonb_build_object('cuenta', cuenta, 'uds', uds, 'venta', venta)
                   order by cuenta) as cuentas
    from (select coalesce(pc.ruta, 'Sin categoría') as ruta, pc.category_id,
                 l.cuenta,
                 sum(l.cantidad)::int as uds,
                 round(sum(l.precio_unitario * l.cantidad), 2) as venta
            from lin l left join pc on pc.sku = l.sku
           group by 1, 2, 3) y
   group by 1, 2
),
lst as (
  select pc.category_id, pc.ruta,
         count(*)                                       as publicaciones,
         count(*) filter (where l.situacion = 'active') as activas
  from channel.listings l
  join pc on pc.sku = l.sku
  where l.canal in ('mercado_libre', 'amazon')
    and lower(coalesce(l.situacion, '')) <> 'closed'
    and (%(cuenta)s::text is null
         or exists (select 1 from core.accounts a
                    where a.id = l.account_id and a.legacy_code = %(cuenta)s))
  group by 1, 2
)
select coalesce(s.ruta, l.ruta)               as ruta,
       coalesce(s.category_id, l.category_id) as category_id,
       coalesce(s.uds, 0)::int                as uds,
       coalesce(s.venta, 0)                   as venta,
       coalesce(s.comision, 0)                as comision,
       coalesce(s.envio, 0)                   as envio,
       s.costo_base,
       coalesce(s.venta_con_costo, 0)         as venta_con_costo,
       coalesce(s.skus, 0)::int               as skus,
       coalesce(l.publicaciones, 0)::int      as publicaciones,
       coalesce(l.activas, 0)::int            as activas,
       c.cuentas
from ventas_cat s
full outer join lst l on l.category_id = s.category_id
left join cuentas_cat c on c.category_id is not distinct from s.category_id
order by venta desc
""")


def _rango_fechas(dias: int, desde: str | None, hasta: str | None) -> tuple[str, str]:
    """(desde, hasta) ISO. Sin fechas explícitas replica el período relativo
    `dias` (los últimos N días hasta hoy CDMX, como el SQL original)."""
    from datetime import date, datetime, timedelta, timezone
    hoy = datetime.now(timezone(timedelta(hours=-6))).date()
    try:
        h = min(date.fromisoformat(hasta), hoy) if hasta else hoy
        d = date.fromisoformat(desde) if desde else h - timedelta(days=dias - 1)
    except ValueError as exc:
        raise HTTPException(400, f"fecha inválida: {exc}") from exc
    if d > h:
        d, h = h, d
    if (h - d).days > 730:
        raise HTTPException(400, "rango máximo: 2 años")
    return d.isoformat(), h.isoformat()


@router.get("/categorias")
async def categorias(
    dias: int = Query(60, ge=7, le=400),
    cuenta: str | None = Query(None),
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
) -> dict[str, Any]:
    """Ventas por categoría con la ruta COMPLETA de ML: devuelve las hojas
    (ruta + category_id) y la UI arma el árbol con acumulados por nivel.
    `dias=400` cubre todo el histórico; `desde`/`hasta` (YYYY-MM-DD) fijan un
    período absoluto y mandan sobre `dias`."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    try:
        d1, d2 = _rango_fechas(dias, desde, hasta)
        filas = sdb.fetch_all(
            _SQL_CAT_HOJAS,
            {"desde": d1, "hasta": d2, "cuenta": cuenta, "categoria_id": None})
        venta_total = sum(float(f["venta"]) for f in filas)
        uds_total = sum(f["uds"] for f in filas)
        # solo las que SÍ vendieron cuentan como "categorías con venta"
        principales = {str(f["ruta"]).split("›")[0].strip()
                       for f in filas if f["uds"]}
        return {
            "ambiente": settings.app_env, "dias": dias, "cuenta": cuenta,
            "desde": d1, "hasta": d2,
            "totales": {"venta": round(venta_total, 2), "uds": int(uds_total),
                        "categorias": len(principales)},
            "hojas": filas,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("categorias fulfillment falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc


@router.get("/categorias/publicaciones")
async def categorias_publicaciones(
    categoria_id: str = Query(..., max_length=40),
    dias: int = Query(60, ge=7, le=400),
    cuenta: str | None = Query(None),
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
) -> dict[str, Any]:
    """Las publicaciones (item_id) de una hoja del árbol, como las filas del
    xlsx: cuenta, título, situación, precio y ventas del período."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    try:
        d1, d2 = _rango_fechas(dias, desde, hasta)
        filas = sdb.fetch_all(
            _SQL_CAT_PUBS,
            {"categoria_id": categoria_id, "desde": d1, "hasta": d2,
             "cuenta": cuenta})
        # el tope se aplica AQUÍ y no en el SQL: el mismo query alimenta al
        # Excel, que necesita todas las publicaciones
        filas.sort(key=lambda f: -float(f["venta"] or 0))
        return {"categoria_id": categoria_id, "dias": dias,
                "desde": d1, "hasta": d2, "items": filas[:200], "tope": 200}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("categorias/publicaciones falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc


@router.get("/categorias/excel")
async def categorias_excel(
    dias: int = Query(60, ge=7, le=400),
    cuenta: str | None = Query(None),
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
):
    """El reporte ÚNICO de ventas y márgenes (Eduardo, 5-ago): Resumen por
    categoría, árbol de Categorías con sus publicaciones y una hoja Ventas con
    una fila por línea vendida. Las tres hojas salen de los PEDIDOS, así que el
    libro cuadra consigo mismo: la comisión que descuenta el margen es la misma
    que respalda cada renglón de la hoja Ventas.

    Sustituye al CSV de márgenes, que era el mismo dato con otro rango de
    fechas y otro botón."""
    from fastapi.responses import Response

    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    d1, d2 = _rango_fechas(dias, desde, hasta)
    try:
        import asyncio

        from services import reporte_categorias_xlsx

        p = {"desde": d1, "hasta": d2, "cuenta": cuenta,
             "categoria_id": None}
        hojas = sdb.fetch_all(_SQL_CAT_HOJAS, p)
        pubs = sdb.fetch_all(_SQL_CAT_PUBS, p)
        # la hoja de detalle reusa el MISMO query del CSV que se retira
        ventas = sdb.fetch_all(
            _SQL_MARGEN_LINEAS,
            {"desde": d1, "hasta": d2, "cuenta": cuenta, "canal": None})
        datos = await asyncio.to_thread(
            reporte_categorias_xlsx.construir, hojas, pubs, ventas, d1, d2, cuenta)
        nombre = f"ventas_margenes_{d1.replace('-', '')}_{d2.replace('-', '')}.xlsx"
        return Response(
            content=datos,
            media_type=("application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet"),
            headers={"Content-Disposition": f'attachment; filename="{nombre}"'})
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("categorias/excel falló: %s", exc)
        raise HTTPException(502, f"no se pudo generar el Excel: {exc}") from exc
