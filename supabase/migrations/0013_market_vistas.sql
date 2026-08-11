-- ═══════════════════════════════════════════════════════════════════════════
-- 0013 — ENRICH: vistas de Competencia (paso 4 del PLAN_COMPETENCIA_v2) y las
-- 3 columnas de snapshot que el plan creyó derivables y NO lo son.
--
-- Estado: PARA SANDBOX Y PRODUCCIÓN (Eduardo, 2026-08-11).
--
-- LAS 3 COLUMNAS NUEVAS en market_listing_metrics — medido, no supuesto:
--   · estado: el plan decía "ya existe en channel.listings". FALSO en
--     semántica: l.status es el estado del PUBLICADOR ('published'/'error');
--     m.estado es el del LISTING en ML ('active'/'paused'/'under_review').
--     Difieren en 3,118 de 3,118. Es foto del periodo, como sale_price.
--   · list_price: l.price se refresca cada 15 min y ya difiere del capturado
--     en 314 filas (mismo fenómeno del conteo 785→645). El precio de lista
--     DEL PERIODO es parte de la medición.
--   · fuente_unidades: 3,118/3,118 no nulo en el origen. Sin consumidor hoy,
--     pero retirarlo rompería la fidelidad de la vista; cuesta una columna.
--
-- LAS 2 VISTAS reproducen la forma EXACTA de propuestas.competencia_skus_v y
-- competencia_publicaciones_v (verificado contra pg_get_viewdef), con UNA
-- adición deliberada: la columna `canal` en market_skus_v, para que la vista
-- no colapse canales cuando entre Amazon. El diff de verificación la ignora.
--
-- Decisiones de derivación (todas medidas contra producción):
--   · nombre = core.products.name (el fallback s.nombre se usó 0 veces)
--   · categoria = coalesce(panel, medida) — MISMA prioridad que la vista
--     vieja: el panel manda (regla 2), la medida es fallback (1 caso:
--     CAM-0030-IND, sin fila en product_category)
--   · raiz_id/raiz_nombre = root de la categoría MEDIDA (c2), no de la del
--     panel: derivar via panel daba 79 diferencias; via medida, CERO
--   · imagen = product_media kind='wc' con LATERAL determinista (order by id)
--
-- El backfill de las 3 columnas va en un DO $$ guardado por la existencia del
-- esquema `propuestas`: en sandbox (donde no existe) la migración pasa sin
-- tocar datos. Idempotente: el update filtra filas ya pobladas.
-- ═══════════════════════════════════════════════════════════════════════════

alter table enrich.market_listing_metrics
  add column if not exists estado          text,
  add column if not exists list_price      numeric,
  add column if not exists fuente_unidades text;

comment on column enrich.market_listing_metrics.estado is
  'Estado del LISTING en el marketplace al capturar (active/paused/under_review). '
  'NO es channel.listings.status: ese es el estado del PUBLICADOR (published/error). '
  'Difieren en el 100% de las filas — medido 2026-08-11.';
comment on column enrich.market_listing_metrics.list_price is
  'Precio de lista AL CAPTURAR. channel.listings.price se refresca cada 15 min '
  'y deriva (314 filas ya diferían al migrar). Foto del periodo, como sale_price.';

-- ── Backfill desde propuestas (solo donde el esquema existe) ────────────────
do $$
begin
  if exists (select 1 from information_schema.schemata
              where schema_name = 'propuestas') then
    update enrich.market_listing_metrics mm
       set estado          = m.estado,
           list_price      = m.precio_lista,
           fuente_unidades = m.fuente_unidades
      from propuestas.competencia_publicacion_metricas m
     where mm.sku    = m.sku::citext
       and mm.canal  = m.canal
       and mm.cuenta = coalesce(m.cuenta, '')
       and mm.periodo = m.periodo
       and (mm.estado is null and mm.list_price is null
            and mm.fuente_unidades is null);
  end if;
end $$;

-- ── Vista 1: el árbol de SKUs vigilados ─────────────────────────────────────
create or replace view enrich.market_skus_v as
select cfg.sku,
       cfg.canal,                                   -- adición deliberada
       p.name                                   as nombre,
       coalesce(pc.category_id, cfg.categoria_id_real) as categoria_id,
       coalesce(c.name,  c2.name)               as categoria_nombre,
       coalesce(c.path,  c2.path)               as ruta,
       c2.root_id                               as raiz_id,
       c2.root_name                             as raiz_nombre,
       cfg.termino_general,
       cfg.termino_origen,
       cfg.activo,
       im.source_url                            as imagen
  from enrich.market_sku_config cfg
  left join core.products p
         on p.sku = cfg.sku
  left join channel.product_category pc
         on pc.sku = cfg.sku and pc.channel_id = cfg.canal
  left join channel.categories c
         on c.category_id = pc.category_id and c.channel_id = cfg.canal
  left join channel.categories c2
         on c2.category_id = cfg.categoria_id_real and c2.channel_id = cfg.canal
  left join lateral (
        select m2.source_url
          from enrich.product_media m2
         where m2.sku = cfg.sku and m2.kind = 'wc'
      order by m2.id
         limit 1) im on true;

-- ── Vista 2: métricas por publicación ───────────────────────────────────────
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
         on l.sku = mm.sku and l.store_name = mm.cuenta and l.canal = mm.canal;

grant select on enrich.market_skus_v          to service_role;
grant select on enrich.market_publicaciones_v to service_role;

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN
--   select count(*) from enrich.market_skus_v;            -- prod: 1,584
--   select count(*) from enrich.market_publicaciones_v;   -- prod: 3,118
--   -- Diff fila a fila contra verificacion_competencia/antes_db_*.json
--   -- (ignorando la columna `canal` agregada): debe salir VACÍO.
-- ═══════════════════════════════════════════════════════════════════════════
