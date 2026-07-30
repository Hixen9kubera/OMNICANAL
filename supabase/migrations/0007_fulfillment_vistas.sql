-- ═══════════════════════════════════════════════════════════════════════════
-- 0007 — FULFILLMENT: vistas del panel de reabastecimiento (F-A del proyecto).
--
-- Estado: EN VALIDACIÓN EN SANDBOX (Eduardo, 2026-07-28). Se aplica a
-- producción solo tras su visto bueno sobre datos sembrados.
--
-- Reemplaza al tablero kubera-fulfillment de José (fuentes muertas al 15-jul)
-- leyendo DIRECTO de la BD kubera — primer lector de producción de v4:
--   1. costing.pricing_params key 'RESTOCK': la fila __default__ de
--      restock_config de dailytrack (Bollinger 45/1.5, piso 14 d, techo 50 d,
--      lead 10 d), versionada como todos los parámetros.
--   2. channel.sales_daily_completa: EMPALME canónico de ventas —
--      analytics.sales_daily_hist ≤ 2026-07-15 ∪ channel.sales_daily ≥ 16-jul
--      (acta id 59: fuentes complementarias). Necesaria porque la ventana
--      Bollinger (45 d) cruza el corte hasta ~septiembre.
--   3. channel.restock_panel: el cálculo por listing — ventas 30 d/ventana,
--      media diaria y sigma CONTANDO DÍAS EN CERO (sin eso la sigma miente),
--      banda superior, días de cobertura FULL, stock mín/máx, sugerido y
--      semáforo. FÓRMULA v1 (validar contra el tablero original):
--        banda_sup   = media_diaria + k·sigma        (ventana Bollinger)
--        stock_min   = banda_sup × (piso + lead)
--        stock_max   = banda_sup × techo
--        sugerido    = max(0, stock_min − stock_full)
--        enviable    = min(sugerido, DROP)           (no se envía lo que no hay;
--                      DROP = bodega Woo real: listing canal='general' por SKU,
--                      alimentado por stock_watch_foto de Brandon v0.27.0)
--        semáforo    : sin_venta | urgente (cobertura ≤ lead) |
--                      reabastecer (< stock_min) | sano | exceso (> stock_max)
-- Derivados SIEMPRE al vuelo (regla v4: nunca tabla). Idempotente.
-- ═══════════════════════════════════════════════════════════════════════════

insert into costing.pricing_params (key, value) values
  ('RESTOCK', '{"bollinger_window": 45, "bollinger_k": 1.5, "floor_days": 14,
                "ceiling_days": 50, "lead_time_days": 10}')
on conflict do nothing;

create or replace view channel.sales_daily_completa as
select date, 'mercado_libre'::text as canal, cuenta, item_id, sku,
       is_full, units_sold, revenue, sale_fee, 'hist'::text as fuente
from analytics.sales_daily_hist
where date <= date '2026-07-15'
union all
select date, canal, cuenta, item_id, sku,
       is_full, units_sold, revenue, sale_fee, 'vivo'::text
from channel.sales_daily
where date >= date '2026-07-16';

comment on view channel.sales_daily_completa is
  'Serie de ventas SIN hueco: archivo dailytrack hasta el 15-jul + flujo vivo '
  'de order_items desde el 16-jul (empalme del acta id 59). Fuente única para '
  'ventanas móviles (Bollinger 45 d) que cruzan el corte.';

create or replace view channel.restock_panel as
with p as (
  select (value->>'bollinger_window')::int   as ventana,
         (value->>'bollinger_k')::numeric    as k,
         (value->>'floor_days')::int         as piso,
         (value->>'ceiling_days')::int       as techo,
         (value->>'lead_time_days')::int     as lead
  from costing.pricing_params
  where key = 'RESTOCK'
  order by valid_from desc
  limit 1
),
spine as (
  select generate_series(current_date - (select ventana from p) + 1,
                         current_date, interval '1 day')::date as d
),
v as (
  select cuenta, sku, date, sum(units_sold) as u, sum(revenue) as r
  from channel.sales_daily_completa
  where sku is not null
    and date > current_date - (select ventana from p)
  group by 1, 2, 3
),
llaves as (select distinct cuenta, sku from v),
serie as (
  -- días SIN venta cuentan como 0: sin esto la media y la sigma se inflan
  select k.cuenta, k.sku, s.d, coalesce(v.u, 0) as u
  from llaves k
  cross join spine s
  left join v on v.cuenta = k.cuenta and v.sku = k.sku and v.date = s.d
),
stats as (
  select cuenta, sku,
         avg(u)                                        as media,
         coalesce(stddev_samp(u), 0)                   as sigma,
         sum(u) filter (where d > current_date - 30)   as u30
  from serie
  group by 1, 2
),
rev30 as (
  select cuenta, sku, sum(r) as r30
  from v where date > current_date - 30
  group by 1, 2
),
dr as (
  -- DROP = bodega propia (Woo) por SKU: el listing canal='general'. Una sola
  -- bolsa compartida por todas las cuentas — no confundir con el stock_own
  -- que DECLARA cada marketplace en su publicación.
  select sku, max(stock_own) as stock_drop
  from channel.listings
  where canal = 'general'
  group by sku
),
base as (
  select l.canal, a.legacy_code as cuenta, l.sku,
         l.listing_id as item_id, pr.name as titulo,
         l.status, l.situacion, l.is_fulfillment,
         l.price, coalesce(d.stock_drop, l.stock_own) as stock_own, l.stock_full,
         cf.costo_unitario, cf.precio_sugerido,
         coalesce(st.u30, 0)                 as unidades_30d,
         coalesce(rv.r30, 0)                 as revenue_30d,
         round(coalesce(st.media, 0), 3)     as venta_diaria,
         round(coalesce(st.sigma, 0), 3)     as sigma,
         round(coalesce(st.media, 0) + p.k * coalesce(st.sigma, 0), 3) as banda_sup,
         ceil((coalesce(st.media, 0) + p.k * coalesce(st.sigma, 0))
              * (p.piso + p.lead))::int      as stock_min,
         ceil((coalesce(st.media, 0) + p.k * coalesce(st.sigma, 0))
              * p.techo)::int                as stock_max,
         p.lead as lead_days
  from channel.listings l
  join core.accounts a  on a.id = l.account_id
  left join core.products pr on pr.sku = l.sku
  left join costing.costos_finales cf
         on cf.sku = l.sku and cf.canal = l.canal
  left join stats st on st.cuenta = a.legacy_code and st.sku = l.sku
  left join rev30 rv on rv.cuenta = a.legacy_code and rv.sku = l.sku
  left join dr d on d.sku = l.sku
  cross join p
  where l.canal in ('mercado_libre', 'amazon')
    -- PUBLICACIONES MUERTAS FUERA (2026-07-29): Brandon marcó `closed` las 293
    -- filas de Amazon que SP-API responde 404 (17.6% de 1,666), y ML trae 61
    -- más. Un listado cerrado NO se puede reabastecer, así que sugerirle
    -- mercancía es ruido accionable falso. `closed` es el único estado
    -- terminal: `paused` e `INVALID` sí se recuperan y se quedan dentro.
    and lower(coalesce(l.situacion, '')) <> 'closed'
)
select b.*,
       case when b.venta_diaria > 0
            then round(coalesce(b.stock_full, 0) / b.venta_diaria, 1)
       end as dias_full,
       greatest(0, b.stock_min - coalesce(b.stock_full, 0)) as sugerido_full,
       least(greatest(0, b.stock_min - coalesce(b.stock_full, 0)),
             coalesce(b.stock_own, 0))                      as enviable_hoy,
       case
         when b.venta_diaria = 0                                  then 'sin_venta'
         when coalesce(b.stock_full, 0) <= b.venta_diaria * b.lead_days
                                                                  then 'urgente'
         when coalesce(b.stock_full, 0) < b.stock_min             then 'reabastecer'
         when coalesce(b.stock_full, 0) > b.stock_max             then 'exceso'
         else 'sano'
       end as semaforo
from base b;

comment on view channel.restock_panel is
  'Panel de reabastecimiento FULL/FBA por listing (reemplazo de '
  'kubera-fulfillment leyendo directo de v4). Fórmula v1: banda de Bollinger '
  'sobre la venta diaria (días sin venta = 0) con parámetros versionados en '
  'pricing_params key RESTOCK. Semáforo: sin_venta/urgente/reabastecer/sano/'
  'exceso. Visitas/conversión FUERA del alcance (decisión 2026-07-28).';

grant select on channel.sales_daily_completa to service_role;
grant select on channel.restock_panel       to service_role;
