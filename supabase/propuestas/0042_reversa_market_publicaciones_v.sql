-- ═══════════════════════════════════════════════════════════════════════════
-- REVERSA de la 0042 — devuelve `market_publicaciones_v` a la 0039.
--
-- CUÁNDO CORRER ESTO. Si el precio o el estado que muestra el panel resultan
-- PEORES que la foto mensual. El síntoma sería que `channel.listings` empiece a
-- traer basura: precios en 0, `situacion` vacía, o `price_sale` de una promoción
-- que ya venció y nadie volvió a confirmar.
--
-- ⚠️ LO QUE SE PIERDE AL REVERTIR, para que la decisión sea con los ojos
-- abiertos. Medido el 1-sep-2026 al aplicar la 0042 en producción:
--
--   · 436 publicaciones vuelven a mostrar un ESTADO que ya no es el real
--   · 810 vuelven a mostrar un PRECIO que ya no es el real
--   · 235 de ésas vuelven a mostrarse 30% o más CARAS de lo que ML cobra
--
-- El caso que lo destapó: MLM5108809642 (BEKURA) volvería a decir $183.57
-- cuando ML cobra $70.61, y MLM5108968738 volvería a decir `active` cuando está
-- `paused` por falta de stock.
--
-- Se pierde también `precio_confirmado_en`. Ojo: `create or replace view` NO
-- deja QUITAR columnas, así que hay que borrar la vista primero. Nada depende de
-- ella por FK —es una vista— pero cualquier otra vista que la use hay que
-- recrearla después.
-- ═══════════════════════════════════════════════════════════════════════════

drop view if exists enrich.market_publicaciones_v;

create view enrich.market_publicaciones_v as
with ventas as (
  select item_id, cuenta, sum(units_sold)::int as unidades
    from channel.sales_daily
   where canal = 'mercado_libre'
     and date > (now() at time zone 'America/Mexico_City')::date - 30
   group by 1, 2
),
medido as (
  select mm.sku, mm.cuenta, mm.canal,
         (array_agg(mm.listing_id order by mm.periodo desc) filter (where mm.listing_id is not null))[1] as listing_id,
         (array_agg(mm.title      order by mm.periodo desc) filter (where mm.title      is not null))[1] as title,
         (array_agg(mm.estado     order by mm.periodo desc) filter (where mm.estado     is not null))[1] as estado,
         (array_agg(mm.sale_price order by mm.periodo desc) filter (where mm.sale_price is not null))[1] as sale_price,
         (array_agg(mm.list_price order by mm.periodo desc) filter (where mm.list_price is not null))[1] as list_price,
         (array_agg(mm.visits_30d order by mm.periodo desc) filter (where mm.visits_30d is not null))[1] as visits_30d,
         max(mm.periodo) as periodo
    from enrich.market_listing_metrics mm
   group by mm.sku, mm.cuenta, mm.canal
)
select m.sku, m.cuenta, m.canal,
       m.listing_id as ml_item_id,
       m.title      as titulo,
       m.estado,
       coalesce(m.sale_price, l.price) as precio,
       coalesce(m.list_price, l.price) as precio_lista,
       coalesce(
         case when m.canal = 'mercado_libre'
               and m.listing_id ~ '^MLM[0-9]{9,12}$'
              then 'https://articulo.mercadolibre.com.mx/MLM-' || substring(m.listing_id from 4) || '-_JM'
         end, l.url) as url,
       m.visits_30d            as visitas_30d,
       coalesce(v.unidades, 0) as unidades_30d,
       'pedidos'::text         as fuente_unidades,
       m.periodo
  from medido m
  left join channel.listings l
         on l.sku = m.sku and l.store_name = m.cuenta and l.canal = m.canal
  left join ventas v on v.item_id = m.listing_id and v.cuenta = m.cuenta

union all

select l.sku, a.legacy_code as cuenta, l.canal,
       l.listing_id       as ml_item_id,
       null::text         as titulo,
       lower(l.situacion) as estado,
       l.price            as precio,
       l.price_base       as precio_lista,
       coalesce(
         case when l.canal = 'mercado_libre'
               and l.listing_id ~ '^MLM[0-9]{9,12}$'
              then 'https://articulo.mercadolibre.com.mx/MLM-' || substring(l.listing_id from 4) || '-_JM'
         end, l.url)      as url,
       null::int          as visitas_30d,
       coalesce(v.unidades, 0) as unidades_30d,
       'pedidos'::text    as fuente_unidades,
       null::date         as periodo
  from channel.listings l
  join core.accounts a on a.id = l.account_id
  left join ventas v on v.item_id = l.listing_id and v.cuenta = a.legacy_code
 where l.canal = 'mercado_libre'
   and lower(l.situacion) in ('active', 'paused')
   and nullif(l.listing_id, '') is not null
   and not exists (select 1 from enrich.market_listing_metrics mm2
                    where mm2.sku = l.sku and mm2.canal = l.canal
                      and mm2.cuenta = a.legacy_code);
