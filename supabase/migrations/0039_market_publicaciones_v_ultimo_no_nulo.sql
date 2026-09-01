-- ═══════════════════════════════════════════════════════════════════════════
-- 0039 — ENRICH: la vista toma el ÚLTIMO valor NO NULO de cada columna, en vez
--        de elegir una fila entera y quedarse con sus huecos.
--
-- ── POR QUÉ, Y ES UN ERROR PROPIO ──────────────────────────────────────────
-- La 0037 resolvió el corte de mes eligiendo UNA fila por (sku, canal, cuenta)
-- con este desempate: primero las que tienen medición, después el periodo más
-- reciente. Se diseñó pensando en una fila nueva **completamente vacía**, que es
-- lo que crea el primer upsert de un mes.
--
-- No aguanta una fila nueva **PARCIALMENTE** llena, y eso pasó el 1-sep-2026 a
-- las 06:0x UTC: el refresco de visitas (`scripts/competencia_visitas.py`) cruzó
-- la medianoche UTC, así que `guardar_publicaciones` calculó
-- `periodo = 2026-09-01` e INSERTÓ 2,780 filas nuevas con **sólo `visits_30d`**.
-- Esas filas tienen medición, ganan el desempate por ser del mes más reciente, y
-- traen `title`, `estado`, `sale_price`, `list_price` y `units_30d` en NULL.
--
-- Efecto medido, inmediato: **los títulos de la vista cayeron de 3,118 a 338.**
-- El precio se salvó de casualidad, porque cae a `channel.listings` por el join.
--
-- ── EL ARREGLO ─────────────────────────────────────────────────────────────
-- Dejar de elegir una FILA y empezar a elegir un VALOR: por cada columna, el más
-- reciente que no sea nulo. Así el título de agosto y las visitas de septiembre
-- conviven, que es lo que "la última medición" quiere decir cuando cada columna
-- se mide en momentos distintos.
--
--     (array_agg(col order by periodo desc) filter (where col is not null))[1]
--
-- Esto vuelve IMPOSIBLE la clase entera de bug: cualquier escritura parcial
-- futura —de un mes nuevo o del mismo— sólo puede AGREGAR información, nunca
-- tapar la que ya había. La 0037 dejaba la puerta entreabierta; ésta la cierra.
--
-- `periodo` se expone como el más reciente que exista, que es el que describe la
-- foto más nueva. `unidades_30d` y `fuente_unidades` siguen viniendo de
-- `channel.sales_daily` en vivo (0038), así que no participan de este juego.
--
-- ── COSTO ──────────────────────────────────────────────────────────────────
-- Un `group by` sobre 5,898 filas en lugar de un `distinct on`. Medido: la vista
-- queda en el mismo rango de medio segundo.
--
-- ── LO QUE NO HACE ─────────────────────────────────────────────────────────
-- No borra ni corrige las 2,780 filas de septiembre: quedan como están, con sus
-- visitas buenas y sus huecos, y la vista ya sabe leerlas. La foto por mes de
-- `market_listing_metrics` sigue siendo la foto por mes.
--
-- REVERSIBLE: es una vista. La anterior está en la 0038.
-- ═══════════════════════════════════════════════════════════════════════════

create or replace view enrich.market_publicaciones_v as
with ventas as (
  -- Nuestras ventas reales de 30 días, por publicación (0038). Una sola pasada.
  select item_id, cuenta, sum(units_sold)::int as unidades
    from channel.sales_daily
   where canal = 'mercado_libre'
     and date > (now() at time zone 'America/Mexico_City')::date - 30
   group by 1, 2
),
medido as (
  -- El último valor NO NULO de cada columna, sin importar de qué mes venga.
  select mm.sku,
         mm.cuenta,
         mm.canal,
         (array_agg(mm.listing_id order by mm.periodo desc)
            filter (where mm.listing_id is not null))[1] as listing_id,
         (array_agg(mm.title      order by mm.periodo desc)
            filter (where mm.title      is not null))[1] as title,
         (array_agg(mm.estado     order by mm.periodo desc)
            filter (where mm.estado     is not null))[1] as estado,
         (array_agg(mm.sale_price order by mm.periodo desc)
            filter (where mm.sale_price is not null))[1] as sale_price,
         (array_agg(mm.list_price order by mm.periodo desc)
            filter (where mm.list_price is not null))[1] as list_price,
         (array_agg(mm.visits_30d order by mm.periodo desc)
            filter (where mm.visits_30d is not null))[1] as visits_30d,
         max(mm.periodo)                                  as periodo
    from enrich.market_listing_metrics mm
   group by mm.sku, mm.cuenta, mm.canal
)
select m.sku,
       m.cuenta,
       m.canal,
       m.listing_id                                       as ml_item_id,
       m.title                                            as titulo,
       m.estado,
       coalesce(m.sale_price, l.price)                    as precio,
       coalesce(m.list_price, l.price)                    as precio_lista,
       coalesce(
         case when m.canal = 'mercado_libre'
               and m.listing_id ~ '^MLM[0-9]{9,12}$'
              then 'https://articulo.mercadolibre.com.mx/MLM-'
                   || substring(m.listing_id from 4) || '-_JM'
         end, l.url)                                      as url,
       m.visits_30d                                       as visitas_30d,
       coalesce(v.unidades, 0)                            as unidades_30d,
       'pedidos'::text                                    as fuente_unidades,
       m.periodo
  from medido m
  left join channel.listings l
         on l.sku = m.sku and l.store_name = m.cuenta and l.canal = m.canal
  left join ventas v
         on v.item_id = m.listing_id and v.cuenta = m.cuenta
union all
-- ── Rama del espejo (0023): publicaciones vivas que nunca se midieron ───────
select l.sku::citext,
       a.legacy_code,
       l.canal,
       l.listing_id,
       null::text,                              -- título: sólo lo trae la medición
       lower(l.situacion),
       l.price,
       l.price_base,
       coalesce(
         case when l.canal = 'mercado_libre'
               and l.listing_id ~ '^MLM[0-9]{9,12}$'
              then 'https://articulo.mercadolibre.com.mx/MLM-'
                   || substring(l.listing_id from 4) || '-_JM'
         end, l.url),
       null::int,                               -- visitas: no medido ≠ 0
       coalesce(v.unidades, 0),
       'pedidos'::text,
       null::date                               -- sin periodo: no es una foto
  from channel.listings l
  join core.accounts a on a.id = l.account_id
  left join ventas v
         on v.item_id = l.listing_id and v.cuenta = a.legacy_code
 where l.canal = 'mercado_libre'
   and lower(l.situacion) in ('active', 'paused')
   and nullif(l.listing_id, '') is not null
   and not exists (select 1 from enrich.market_listing_metrics mm2
                    where mm2.sku = l.sku and mm2.canal = l.canal
                      and mm2.cuenta = a.legacy_code);

comment on view enrich.market_publicaciones_v is
  'Publicaciones de ML del módulo Competencia. Una fila por (sku, canal, cuenta) '
  'armada con el ÚLTIMO valor NO NULO de cada columna, así una escritura parcial '
  'nunca tapa lo que ya había. Las UNIDADES salen de channel.sales_daily en vivo '
  '(0038); las VISITAS de la medición, y siguen en NULL cuando nadie midió.';

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN (correr DESPUÉS de aplicar)
--
--   -- 1) Los títulos vuelven: de 338 a ~3,118.
--   select count(*) filas, count(titulo) con_titulo, count(visitas_30d) con_visitas
--     from enrich.market_publicaciones_v;
--
--   -- 2) Y las visitas nuevas de septiembre siguen ahí, no se pierden.
--   select sku, cuenta, titulo is not null tiene_titulo, visitas_30d, periodo
--     from enrich.market_publicaciones_v where sku::text = 'TEC-2162-NEG';
--
--   -- 3) Cero llaves duplicadas.
--   select sku, canal, cuenta, count(*) from enrich.market_publicaciones_v
--    group by 1,2,3 having count(*) > 1;
-- ═══════════════════════════════════════════════════════════════════════════
