-- ═══════════════════════════════════════════════════════════════════════════
-- 0023 — ENRICH: Competencia lee las publicaciones vivas DIRECTO de
--        channel.listings. El cron de censo (v0.205.0) se retira.
--
-- POR QUÉ. La v0.205.0 resolvió el censo congelado con un cron diario que
-- COPIABA de channel.listings a las tablas de enrich. Eduardo preguntó lo
-- obvio: si channel.listings ya es el registro vivo (lo refresca el sync cada
-- 15 min), ¿por qué copiar? Esta migración quita la copia: la membresía del
-- censo y las publicaciones sin medir se DERIVAN en las vistas.
--
-- EL REPARTO DE PAPELES queda así:
--   channel.listings ......... QUÉ está publicado y cómo está AHORA (derivado)
--   market_sku_config ........ lo que decidió un HUMANO o midió Competencia:
--                              activo, término, categoria_id_real (tabla)
--   market_listing_metrics ... la foto MENSUAL medida: visitas, ventas,
--                              precio pagado (tabla; NO se toca su semántica)
--
-- VISTA 1 (market_skus_v): la base deja de ser market_sku_config a secas y
-- pasa a ser (vivos en channel.listings ∪ filas de config). Un SKU recién
-- publicado aparece en Competencia SOLO, sin alta previa; `activo` default
-- true cuando no hay fila de config. "Vivo" = active|paused, la definición de
-- channel_read.vivas_ml(). Se filtra canal='mercado_libre' a propósito: es el
-- único canal medido hoy; cuando Amazon entre, se decide su unión aparte.
-- El exists contra core.products replica el guard del cron: un listing cuyo
-- SKU no está en la maestra no puede cargar nombre/categoría y tampoco puede
-- recibir término (FK de market_sku_config) — mejor fuera que a medias.
--
-- VISTA 2 (market_publicaciones_v): UNION ALL de lo vivo SIN medición, leído
-- directo de channel.listings. Un SKU medido muestra su foto del mes (esa es
-- la semántica de metrics); uno nunca medido muestra su estado vivo — item,
-- estado y precio SIEMPRE frescos, en vez de la copia congelada del cron.
-- La cuenta sale de core.accounts.legacy_code vía account_id (poblado 100%),
-- NO de store_name (solo 4,044 de 4,955) — misma razón que en v0.205.0.
--
-- LIMPIEZA: se borran las filas de market_listing_metrics que creó el cron el
-- 18-ago (title/visits/units los tres NULL — verificado: las 3,118 medidas
-- tienen los tres poblados, ninguna cae en el filtro). Si se quedaran, el
-- NOT EXISTS de la vista 2 las preferiría sobre la lectura viva y el panel
-- mostraría el precio congelado del 18-ago para siempre.
--
-- LO QUE ESTA MIGRACIÓN NO HACE: no borra las 1,117 filas de config que dio
-- de alta la carga del 18-ago. Son redundantes con la unión (solo dicen
-- activo=true, que ya es el default) pero inofensivas, y varios scripts hacen
-- INNER JOIN a market_sku_config — borrarlas por limpieza arriesga esconder
-- SKUs si algún join se me escapó. Redundante-e-inofensivo gana a limpio-y-
-- riesgoso.
--
-- REVERSIBLE: las dos vistas anteriores están en 0015/0017 y en el commit
-- v0.205.0 (pg_get_viewdef); las filas borradas las recrearía una corrida del
-- cron retirado (el script vive en el historial de git).
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Vista 1: el árbol de SKUs — vivos ∪ config ──────────────────────────────
create or replace view enrich.market_skus_v as
with base as (
  select distinct l.sku::citext as sku, l.canal
    from channel.listings l
   where l.canal = 'mercado_libre'
     and lower(l.situacion) in ('active', 'paused')
     and nullif(l.listing_id, '') is not null
     and exists (select 1 from core.products p where p.sku = l.sku)
  union
  select sku, canal from enrich.market_sku_config
)
select b.sku,
       b.canal,
       p.name                                               as nombre,
       coalesce(pc.category_id, cfg.categoria_id_real)      as categoria_id,
       btrim(coalesce(c.name, c2.name))                     as categoria_nombre,
       coalesce(c.path, c2.path)                            as ruta,
       coalesce(c.root_id, c2.root_id)                      as raiz_id,
       btrim(coalesce(c.root_name, c2.root_name))           as raiz_nombre,
       st.termino                                           as termino_general,
       cfg.termino_origen,
       -- Sin fila de config no hay decisión humana en contra: vigilado.
       coalesce(cfg.activo, true)                           as activo,
       im.source_url                                        as imagen,
       coalesce(c.parent_id, c2.parent_id)                  as padre_id,
       btrim(rt.seg[array_length(rt.seg, 1) - 1])           as padre_nombre
  from base b
  left join enrich.market_sku_config cfg
         on cfg.sku = b.sku and cfg.canal = b.canal
  left join core.products p
         on p.sku = b.sku
  left join enrich.market_search_term st
         on st.id = cfg.termino_id
  left join channel.product_category pc
         on pc.sku = b.sku and pc.channel_id = b.canal
  left join channel.categories c
         on c.category_id = pc.category_id and c.channel_id = b.canal
  left join channel.categories c2
         on c2.category_id = cfg.categoria_id_real and c2.channel_id = b.canal
  left join lateral (
         select regexp_split_to_array(coalesce(c.path, c2.path), '\s*[›>]\s*') as seg
         ) rt on true
  left join lateral (
         select m2.source_url
           from enrich.product_media m2
          where m2.sku = b.sku and m2.kind = 'wc'
          order by m2.id
          limit 1) im on true;

comment on view enrich.market_skus_v is
  'SKUs de Competencia: publicados vivos en ML (channel.listings, derivado) ∪ '
  'market_sku_config (estado humano: activo/término/categoría medida). Un SKU '
  'recién publicado entra SOLO; activo default true sin fila de config. La '
  'categoría prefiere la del PANEL y cae a la medida; raíz/padre/ruta salen de '
  'esa misma categoría (0015).';

-- ── Vista 2: publicaciones — la foto medida, o lo vivo si no hay foto ───────
create or replace view enrich.market_publicaciones_v as
select mm.sku,
       mm.cuenta,
       mm.canal,
       mm.listing_id                            as ml_item_id,
       mm.title                                 as titulo,
       mm.estado,
       coalesce(mm.sale_price, l.price)         as precio,
       coalesce(mm.list_price, l.price)         as precio_lista,
       coalesce(
         case when mm.canal = 'mercado_libre'
               and mm.listing_id ~ '^MLM[0-9]{9,12}$'
              then 'https://articulo.mercadolibre.com.mx/MLM-'
                   || substring(mm.listing_id from 4) || '-_JM'
         end, l.url)                            as url,
       mm.visits_30d                            as visitas_30d,
       mm.units_30d                             as unidades_30d,
       mm.fuente_unidades,
       mm.periodo
  from enrich.market_listing_metrics mm
  left join channel.listings l
         on l.sku = mm.sku and l.store_name = mm.cuenta and l.canal = mm.canal
union all
select l.sku::citext,
       a.legacy_code,
       l.canal,
       l.listing_id,
       null::text,                              -- título: solo lo trae la medición
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
       null::int,
       null::text,
       null::date                               -- sin periodo: no es una foto
  from channel.listings l
  join core.accounts a on a.id = l.account_id
 where l.canal = 'mercado_libre'
   and lower(l.situacion) in ('active', 'paused')
   and nullif(l.listing_id, '') is not null
   -- Si ese par (sku, cuenta) tiene CUALQUIER medición, gana la medición:
   -- metrics es la foto del mes y esta rama es solo el "mientras tanto".
   and not exists (select 1 from enrich.market_listing_metrics mm2
                    where mm2.sku = l.sku and mm2.canal = l.canal
                      and mm2.cuenta = a.legacy_code);

comment on view enrich.market_publicaciones_v is
  'Publicaciones por (sku, cuenta): la foto MEDIDA del mes (market_listing_'
  'metrics) cuando existe; el estado VIVO de channel.listings cuando nunca se '
  'midió (título/visitas/unidades NULL = no medido, no cero).';

-- ── Limpieza: fuera las copias que dejó el cron del 18-ago ──────────────────
-- Sin esto, el NOT EXISTS de arriba preferiría la copia congelada (precio del
-- 18-ago) sobre la lectura viva. Solo caen las filas del cron: las 3,118
-- medidas tienen title, visits_30d y units_30d poblados al 100% (verificado
-- antes de escribir esta migración).
delete from enrich.market_listing_metrics
 where title is null and visits_30d is null and units_30d is null;

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN
--
--   -- 1) La vista de SKUs no pierde a nadie (18-ago: 2,701; los 2,495 vivos
--   --    ya tenían fila de config, así que la unión no agrega hoy)
--   select count(*) from enrich.market_skus_v;
--
--   -- 2) Publicaciones: 3,118 medidas + ~1,534 vivas sin medir ≈ 4,652
--   select count(*) filter (where periodo is not null) medidas,
--          count(*) filter (where periodo is null)     vivas
--     from enrich.market_publicaciones_v;
--
--   -- 3) El precio de una viva es el de channel.listings AHORA, no una copia
--   select v.sku, v.precio, l.price from enrich.market_publicaciones_v v
--     join channel.listings l on l.listing_id = v.ml_item_id
--    where v.sku = 'TEC-0407-MET';
--
--   -- 4) Un SKU vivo SIN fila de config aparece igual (hoy: 0 casos, la
--   --    prueba real es el siguiente SKU que se publique)
--   select count(*) from enrich.market_skus_v v
--    where not exists (select 1 from enrich.market_sku_config c
--                       where c.sku = v.sku and c.canal = v.canal);
-- ═══════════════════════════════════════════════════════════════════════════
