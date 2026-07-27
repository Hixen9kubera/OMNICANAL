-- =============================================================================
-- Kubera Omnicanal — esquema propuesto v4 (Postgres / Supabase)
-- =============================================================================
-- Supersede a schema_propuesto_v3.sql. Generado desde plan_maestro_supabase_v4.md
-- (2026-07-14) e incorpora la verificación en vivo del mismo día:
--
--   * ops.channel_submissions SIN blobs: solo resumen liviano + detail_ref
--     (los payloads pesados se quedan en el archivo MySQL — schema_hostinger_archivo.sql).
--   * ops.webhook_events con llave de IDEMPOTENCIA real
--     (env, canal, topic, external_id, delivery_id) — hoy hay 36% de duplicados.
--   * costing.cost_history + fx_rates + pricing_params (historial y versionado:
--     hoy el UPSERT es destructivo y el TC 18.5 vive hardcodeado en el frontend).
--   * costing.legacy_costos_ml: archivo CONGELADO de costos_ml — NO se fusionan
--     importes (96.6% de los precios difieren >1% entre ambas tablas).
--   * esquema migration: tablas sombra (id_map, costs_preview, costs_differences,
--     reconciliation_runs) para validar el cálculo nuevo sin tocar producción.
--   * enrich.supplier_data con moneda verificable (cierra el bug moneda="USD" fijo).
--   * RLS habilitada en TODAS las tablas (deny-by-default: sin políticas, solo
--     service_role pasa). Las 18 tablas analytics existentes se remedian aparte
--     (T1.5, condicionada a P11).
--
-- ORDEN DE EJECUCIÓN: de arriba a abajo (core -> channel/costing/enrich -> ops
-- -> migration -> triggers -> RLS). Es el DDL DE DESTINO, no un script de
-- migración de datos: no mueve ni borra nada de kubera_ml, WordPress ni del
-- Supabase actual.
--
-- DÓNDE CORRE: primero en el proyecto Supabase DEV (Fase 1); en producción
-- (proyecto existente, junto a las tablas analytics) solo en Fase 3, vía
-- migración versionada (supabase/migrations).
-- =============================================================================

create extension if not exists citext;
create extension if not exists pgcrypto;  -- gen_random_uuid()


-- =============================================================================
-- core — identidad y catálogo
-- =============================================================================
create schema if not exists core;

create table core.channels (
  id        text primary key,          -- 'general' | 'mercado_libre' | 'amazon' | ...
  label     text not null,
  is_active boolean not null default true
);

create table core.accounts (
  id          uuid primary key default gen_random_uuid(),
  channel_id  text not null references core.channels(id),
  legacy_code text unique,             -- 'BEKURA' | 'SANCORFASHION'
  external_id text,                    -- ml_user_id / seller_id
  label       text not null,
  is_active   boolean not null default true,
  created_at  timestamptz not null default now()
);

-- Maestro de identidad. Se puebla desde la UNIÓN de fuentes
-- (productos ∪ costos_validados ∪ categorias_ml) porque `productos` en MySQL
-- NO es un maestro completo (77% de costos_validados es huérfano contra ella).
-- `status` distingue el origen: 'active' | 'draft' | 'packing_list_only' | 'orphan'.
-- `odoo_id` se llena por backfill EN VIVO contra Odoo (el campo local de MySQL
-- está desalineado — no copiarlo).
create table core.products (
  sku            citext primary key check (length(sku) <= 100 and sku !~ '\s'),
  name           text,
  wc_id          bigint unique,
  wc_parent_id   bigint,
  odoo_id        bigint,
  status         text not null default 'draft',
  brand          text,
  has_variations boolean not null default false,
  parent_sku     citext references core.products(sku),
  tags           text[],
  source         text,                 -- 'productos' | 'costos_validados' | 'categorias_ml' | 'crear'
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create index idx_core_products_parent_sku on core.products(parent_sku);
create index idx_core_products_status on core.products(status);
create index idx_core_products_odoo_id on core.products(odoo_id);

-- Identidad de PERSONAS (épica Identidad, antes del dominio costing).
-- Las credenciales viven en auth.users (Supabase Auth las administra: hash de
-- password, sesiones, recuperación). Esta tabla es el PERFIL de negocio:
-- nombre visible, rol y estado. Las bitácoras (crear_logs.usuario,
-- cost_history.cambiado_por, ops.process_log.origen) guardan esta identidad.
create table core.usuarios (
  id         uuid primary key references auth.users (id) on delete cascade,
  nombre     text not null,
  email      citext unique,
  rol        text not null default 'operador'
             check (rol in ('admin', 'operador', 'lectura')),
  activo     boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);


-- =============================================================================
-- channel — estado por canal
-- (fusiona ml_progress + amazon_progress + canal_inventario)
-- =============================================================================
create schema if not exists channel;

-- NOTA FK: durante la Fase 2 las FKs a core.products se crean NOT VALID y se
-- validan (VALIDATE CONSTRAINT) cuando el backfill de la UNIÓN termine.
create table channel.listings (
  sku            citext not null references core.products(sku),
  account_id     uuid not null references core.accounts(id),
  canal          text not null references core.channels(id),
  listing_id     text,                 -- ml_item_id / ASIN
  url            text,
  status         text,
  situacion      text,
  price          numeric(14,2),
  price_base     numeric(14,2),
  stock_own      int,                  -- stock propio (Odoo)
  stock_full     int,                  -- FULL (ML) / FBA (Amazon)
  is_fulfillment boolean not null default false,
  product_type   text,                 -- Amazon: requerido para publicar (viene de amazon_progress)
  updated_at     timestamptz not null default now(),
  primary key (sku, account_id, canal)
);

create index idx_channel_listings_sku on channel.listings(sku);
create index idx_channel_listings_canal on channel.listings(canal);
create index idx_channel_listings_listing_id on channel.listings(listing_id);

-- Árbol de categorías por canal (separado de la asignación — hoy categorias_ml
-- mezcla ambos). MIGRA SOLO cuando el curador externo re-apunte (P1).
create table channel.categories (
  channel_id  text not null references core.channels(id),
  category_id text not null,
  name        text,
  path        text,                    -- 'cat1 > cat2 > cat3 > cat4'
  primary key (channel_id, category_id)
);

create table channel.product_category (
  sku         citext not null references core.products(sku),
  channel_id  text not null references core.channels(id),
  category_id text not null,
  source      text,                    -- 'ml_ia' | 'manual' | 'woocommerce'
  updated_at  timestamptz not null default now(),
  primary key (sku, channel_id)
);

-- Historial de cambios del listing (monitoreo de precio/stock/FULL/status por
-- plataforma — requerimiento 2026-07-16). Poblado por TRIGGER: cualquier
-- escritor (sync 15 min, webhook, espejo) deja huella sin poder olvidarse.
-- Precio actual = channel.listings.price; el anterior = la última fila de aquí.
create table channel.listing_history (
  id             bigint generated always as identity primary key,
  sku            citext not null,
  account_id     uuid not null,
  canal          text not null,
  campo          text not null,        -- 'price'|'stock_own'|'stock_full'|'is_fulfillment'|'status'|'situacion'
  valor_anterior text,
  valor_nuevo    text,
  detectado_via  text not null default 'sync',   -- 'sync'|'webhook'|'manual' (set_config app.via)
  changed_at     timestamptz not null default now()
);
create index idx_lh_sku on channel.listing_history(sku, canal, changed_at desc);
create index idx_lh_campo on channel.listing_history(campo, changed_at desc);

create or replace function channel.fn_listing_history() returns trigger
language plpgsql as $$
declare
  via text := coalesce(current_setting('app.via', true), 'sync');
begin
  if old.price is distinct from new.price then
    insert into channel.listing_history (sku, account_id, canal, campo, valor_anterior, valor_nuevo, detectado_via)
    values (old.sku, old.account_id, old.canal, 'price', old.price::text, new.price::text, via);
  end if;
  if old.stock_own is distinct from new.stock_own then
    insert into channel.listing_history (sku, account_id, canal, campo, valor_anterior, valor_nuevo, detectado_via)
    values (old.sku, old.account_id, old.canal, 'stock_own', old.stock_own::text, new.stock_own::text, via);
  end if;
  if old.stock_full is distinct from new.stock_full then
    insert into channel.listing_history (sku, account_id, canal, campo, valor_anterior, valor_nuevo, detectado_via)
    values (old.sku, old.account_id, old.canal, 'stock_full', old.stock_full::text, new.stock_full::text, via);
  end if;
  if old.is_fulfillment is distinct from new.is_fulfillment then
    insert into channel.listing_history (sku, account_id, canal, campo, valor_anterior, valor_nuevo, detectado_via)
    values (old.sku, old.account_id, old.canal, 'is_fulfillment', old.is_fulfillment::text, new.is_fulfillment::text, via);
  end if;
  if old.status is distinct from new.status then
    insert into channel.listing_history (sku, account_id, canal, campo, valor_anterior, valor_nuevo, detectado_via)
    values (old.sku, old.account_id, old.canal, 'status', old.status, new.status, via);
  end if;
  if old.situacion is distinct from new.situacion then
    insert into channel.listing_history (sku, account_id, canal, campo, valor_anterior, valor_nuevo, detectado_via)
    values (old.sku, old.account_id, old.canal, 'situacion', old.situacion, new.situacion, via);
  end if;
  return new;
end $$;

create trigger trg_hist_listings after update on channel.listings
  for each row execute function channel.fn_listing_history();


-- =============================================================================
-- costing — costos con historial, FX de servidor y parámetros versionados
-- =============================================================================
create schema if not exists costing;

-- FUENTE DE VERDAD del costo base (packing list / Excel unificado).
-- + currency / fx_rate_used: hoy el TC (18.5) vive hardcodeado en el frontend
--   y no se persiste — los costos históricos son irreproducibles.
create table costing.costos_validados (
  sku             citext primary key references core.products(sku),
  wc_id           bigint,
  wc_status       text,
  wc_type         text,
  contenedor      text,
  costo_producto  numeric(14,4),
  costo_cbm       numeric(14,4),
  largo           numeric(8,2),
  alto            numeric(8,2),
  ancho           numeric(8,2),
  peso            numeric(8,3),
  costo_total     numeric(14,4),
  cajas           numeric(12,2),
  piezas_por_caja numeric(12,2),
  currency        text not null default 'MXN',
  fx_rate_used    numeric(12,6),       -- TC aplicado si la captura vino en otra moneda
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index idx_costos_validados_contenedor on costing.costos_validados(contenedor);

-- Pricing derivado (regenerable). Forma = DDL real de MySQL verificado
-- 2026-07-14 + trazabilidad nueva. Las dimensiones NO se repiten aquí
-- (viven en costos_validados); costo_unitario queda como copia declaradamente
-- derivada.
-- PENDIENTE P4: si un SKU puede tener precio distinto por cuenta
-- (BEKURA vs SANCORFASHION), la PK cambia a (sku, account_id). Mientras,
-- account_id queda nullable y fuera de la PK.
create table costing.costos_finales (
  sku                      citext primary key references core.products(sku),
  account_id               uuid references core.accounts(id),
  costo_producto           numeric(14,4),
  costo_cbm                numeric(14,4),
  costo_unitario           numeric(14,4),   -- derivado de costos_validados
  ml_cat_id                text,
  pct_comision             numeric(6,4),
  comision_estimada        boolean not null default false,  -- true si no vino de la API en vivo
  costo_comision           numeric(14,4),
  costo_fee_envio          numeric(14,4),
  precio_sugerido          numeric(14,2),
  precio_sugerido_original numeric(14,2),
  precio_base              numeric(14,2),
  margen                   numeric(6,4),
  peso_origen              text,
  formula_ver              text,            -- versión/hash de la fórmula de costos.py
  costo_validado_at        timestamptz,     -- updated_at de costos_validados usado
  comision_consultada_at   timestamptz,
  calculado_at             timestamptz not null default now(),
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

create index idx_costos_finales_ml_cat on costing.costos_finales(ml_cat_id);

-- ARCHIVO CONGELADO de costos_ml (MySQL) — solo lectura, con fecha de corte.
-- NUNCA se fusionan sus importes en costos_finales: 96.6% de los precios
-- difieren >1% [VERIFICADO 2026-07-14]. Espejo 1:1 del DDL real de MySQL.
create table costing.legacy_costos_ml (
  sku                      citext primary key,   -- SIN FK: puede contener SKUs que ya no existan
  ml_cat_id                text,
  ml_cat_nombre            text,
  pct_comision             numeric(6,4),
  fee_envio                numeric(10,2),
  iva_mnt                  numeric(10,2),
  total_costos_ml          numeric(10,2),
  margen                   numeric(6,4),
  precio_sugerido          numeric(10,2),
  precio_base              numeric(10,2),
  descuento_pct            numeric(6,4),
  ganancia_neta            numeric(10,2),
  roi                      numeric(6,4),
  ml_estado                text,                 -- columna del workflow del pipeline legado
  calculado_at             timestamptz,
  updated_at               timestamptz,
  precio_sugerido_original numeric(10,2),
  archivado_at             timestamptz not null default now(),
  fecha_corte              date not null         -- día del congelamiento
);

comment on table costing.legacy_costos_ml is
  'Archivo congelado de kubera_ml.costos_ml. Solo lectura. No fusionar importes con costos_finales.';

-- Historial append-only: NADA se sobrescribe sin rastro.
-- EXCEPCIÓN deliberada al patrón resumen/detalle: el snapshot completo SÍ vive
-- en Postgres — es la base del rollback de costos.
create table costing.cost_history (
  id           bigint generated always as identity primary key,
  sku          citext not null,
  tabla        text not null,          -- 'costos_validados' | 'costos_finales'
  version      int not null,
  snapshot     jsonb not null,         -- fila completa PREVIA al cambio
  cambiado_por text,                   -- usuario / proceso de origen
  accion       text,                   -- 'auto' | 'manual' | 'bulk' | 'migracion'
  formula_ver  text,
  fx_rate_used numeric(12,6),
  currency     text not null default 'MXN',
  created_at   timestamptz not null default now()
);

create index idx_cost_history_sku on costing.cost_history(sku, tabla, created_at desc);

-- Tipo de cambio de SERVIDOR (reemplaza el DEFAULT_TC=18.5 del frontend).
create table costing.fx_rates (
  currency   text not null,
  rate       numeric(12,6) not null,
  valid_from date not null,
  source     text,
  primary key (currency, valid_from)
);

-- Parámetros de pricing versionados: MARGEN (0.48), IVA (0.16), DESCUENTO_BASE,
-- TARIFA_CBM_M3 (7500), versión de la tabla de fees _TARIFA_ML.
-- El vigente = fila con max(valid_from) <= now() por key.
create table costing.pricing_params (
  key        text not null,
  value      jsonb not null,
  valid_from timestamptz not null default now(),
  primary key (key, valid_from)
);

-- Derivados SIEMPRE al vuelo (nunca tabla). NULL se propaga: un importe nulo
-- se reporta como "sin dato", jamás como 0. round() solo aquí (presentación).
create or replace view costing.costos_finales_detalle as
select
  f.*,
  round((f.precio_sugerido - f.costo_unitario - f.costo_comision - f.costo_fee_envio), 2)
    as ganancia_neta,
  case when f.costo_unitario > 0
    then round((f.precio_sugerido - f.costo_unitario) / f.costo_unitario * 100, 2)
    else null end as roi_pct,
  case when f.precio_base > 0
    then round((1 - f.precio_sugerido / f.precio_base) * 100, 2)
    else null end as descuento_pct
from costing.costos_finales f;

create or replace view costing.precios_desactualizados as
select f.sku, f.calculado_at, v.updated_at as costo_actualizado_en
from costing.costos_finales f
join costing.costos_validados v on v.sku = f.sku
where v.updated_at > f.costo_validado_at
   or f.costo_validado_at is null;

-- Conciliación estimado vs cobrado (fase 6+): la comisión REAL por venta ya
-- existe en analytics (daily_sales.sale_fee). Vista de referencia — se crea
-- cuando channel.listings esté poblada:
--   costing.costos_finales.costo_comision (estimado, por SKU)
--   vs analytics daily_sales.sale_fee (cobrado, por día×cuenta×item)
--   unidos por seller_sku / listing_id vía channel.listings.

-- FUTURO (épica posterior, tras P4/P5): costo por canal.
-- create table costing.channel_costs (
--   sku citext, channel_id text, concepto text, monto numeric(14,4),
--   currency text, valid_from date, ...
--   primary key (sku, channel_id, concepto, valid_from)
-- );


-- =============================================================================
-- enrich — proveedor, IA, medios y viabilidad
-- =============================================================================
create schema if not exists enrich;

-- Antes: scraping_alibaba (dueño externo — coordinar P1 antes de poblar).
-- Cierra el bug moneda="USD" fijo del scraper: price + currency verificable.
create table enrich.supplier_data (
  sku                     citext primary key references core.products(sku),
  source_url              text,
  price                   numeric(12,4),
  currency                text,                  -- 'USD' | 'MXN' | ... (NULL = sin verificar)
  price_currency_verified boolean not null default false,
  fx_rate_used            numeric(12,6),
  scrape_status           text,
  title                   text,
  title_original          text,
  images                  jsonb,
  specs                   jsonb,                 -- peso, dims, CBM del proveedor
  scraped_at              timestamptz,
  updated_at              timestamptz not null default now()
);

create table enrich.ai_attributes (
  sku        citext primary key references core.products(sku),
  attributes jsonb,
  is_valid   boolean,
  model_used text,
  updated_at timestamptz not null default now()
);

create table enrich.product_media (
  id         bigint generated always as identity primary key,
  sku        citext references core.products(sku),
  kind       text,                     -- 'gallery' | 'edited_ia' | 'amazon' | ... (genérico, no una columna por plataforma)
  source_url text,
  cdn_url    text,
  created_at timestamptz not null default now()
);

create index idx_product_media_sku on enrich.product_media(sku);

-- Antes: odoo_ranking. SNAPSHOT REGENERABLE (no vista: cruza Odoo en vivo y
-- XML-RPC pagina 500/lote). Tipos alineados al DDL real de MySQL verificado
-- 2026-07-14. Migra SOLO cuando su generador externo re-apunte (P1/P2).
create table enrich.odoo_viability (
  sku             citext primary key,  -- SIN FK inicial: 8k filas vs 5.4k productos
  nombre          text,
  stock           int default 0,
  costo           numeric(10,2),
  flete_unit      numeric(10,2),
  ganancia_unit   numeric(10,2),
  ganancia_total  numeric(10,2),
  cbm_unit        numeric(10,6),
  cbm_total       numeric(10,6),
  peso_kg         numeric(8,3),
  score           numeric(12,2) default 0,
  en_wc           boolean default false,
  apto            boolean default false,
  motivo_excluido text,
  updated_at      timestamptz not null default now()
);

create index idx_odoo_viability_score on enrich.odoo_viability(score desc);
create index idx_odoo_viability_apto on enrich.odoo_viability(apto);


-- =============================================================================
-- analytics — las 18 tablas existentes NO se redefinen (ya viven en el proyecto)
-- =============================================================================
-- products_snapshot, daily_stock, daily_sales, daily_visits, sales,
-- product_changes, ml_accounts, competition_cache, competitor_watchlist,
-- cron_runs, goals, notifications, restock_config, metrics_daily,
-- reporte_monitoreo_competencia_30day/diario, sales_weekly/monthly_archive.
-- Dueño de escritura: pipeline externo. Pendientes sobre ellas:
--   * T1.5: ACTIVAR RLS (hoy: RLS off + anon lee todo [VERIFICADO 2026-07-14]) — tras P11.
--   * §7.5 O1-O3: poda de products_snapshot.raw / attributes_map (−767 MB) — tras P1/P7/P9.
--   * restock_config.sku es text: homologar el join contra core.products (citext).


-- =============================================================================
-- ops — eventos, bitácoras livianas, tokens, cola, migración
-- =============================================================================
create schema if not exists ops;

-- Staging IDEMPOTENTE de webhooks (ML hoy + Woo por construir).
-- Payload ML (diminuto) va inline en payload; el de pedidos Woo (pesado) va al
-- archivo MySQL (archivo_webhook_detalle) y aquí solo queda detail_ref.
create table ops.webhook_events (
  id          bigint generated always as identity primary key,
  env         text not null default 'prod',   -- 'prod' | 'staging' (la llave incluye ambiente)
  canal       text not null,                  -- 'mercado_libre' | 'woocommerce'
  topic       text not null,                  -- 'orders_v2' | 'items' | 'order.created' | ...
  external_id text not null,                  -- resource / order_id / item_id
  delivery_id text not null default '',       -- X-WC-Webhook-Delivery-ID; ML: hash del payload
  cuenta      text,
  sku         citext,
  payload     jsonb,                          -- solo si es pequeño
  detail_ref  text,                           -- 'mysql:archivo_webhook_detalle:<id>'
  firma_valida boolean,
  procesado   boolean not null default false,
  resultado   text,
  intentos    int not null default 0,
  next_retry_at timestamptz,
  recibido_at timestamptz not null default now(),
  procesado_at timestamptz,
  unique (env, canal, topic, external_id, delivery_id)   -- <- idempotencia (hoy: 36% duplicados)
);

create index idx_webhook_events_pendientes on ops.webhook_events(procesado, next_retry_at)
  where not procesado;

-- Resumen LIVIANO de envíos a marketplaces (antes: ml_backlog + amazon_backlog
-- + ml_image_edit_backlog). Los blobs payload/respuesta NUNCA viajan aquí:
-- viven en el archivo MySQL (schema_hostinger_archivo.sql) vía detail_ref.
create table ops.channel_submissions (
  id            bigint generated always as identity primary key,
  canal         text not null,
  cuenta        text,
  sku           citext references core.products(sku),
  account_id    uuid references core.accounts(id),
  submission_id text,                  -- ml_item_id devuelto / submission Amazon
  operacion     text,                  -- 'alta' | 'actualizacion' | 'imagen' | ...
  status        text,
  success       boolean,
  error_resumen text,                  -- mensaje corto / primer issue: lo único que se lee en listados
  detail_ref    text,                  -- 'mysql:ml_backlog:123' | 'mysql:archivo_submission_detalle:45' | 'storage:...'
  submitted_at  timestamptz,
  published_at  timestamptz,
  created_at    timestamptz not null default now()
);

create index idx_channel_submissions_sku on ops.channel_submissions(sku);
create index idx_channel_submissions_canal on ops.channel_submissions(canal, created_at desc);

-- Bitácora unificada de procesos (antes: costos_logs, sync_procesados,
-- odoo_sync_procesados, odoo_sync_backlog, backlog_errores, pipeline_runs,
-- crear_logs, y el resumen de ml_image_edit_backlog).
create table ops.process_log (
  id         bigint generated always as identity primary key,
  proceso    text not null,            -- 'costos' | 'sync_woo' | 'odoo_sync' | 'crear' | 'imagenes' | ...
  origen     text,                     -- 'backend' | 'pipeline_ext' | 'manual'
  sku        citext,
  accion     text,
  estado     text,
  detalle    jsonb,                    -- con tope de tamaño; overflow -> archivo via detail_ref
  detail_ref text,
  duracion_s numeric(10,2),
  created_at timestamptz not null default now()
);

create index idx_process_log_proceso on ops.process_log(proceso, created_at desc);
create index idx_process_log_sku on ops.process_log(sku);

-- Tokens ML. Los SECRETOS van en Supabase Vault (vault.secrets); esta tabla
-- guarda solo referencias y metadatos. BLOQUEADA por P3: no converger sin
-- acuerdo con el dueño de ml_tokens_dashboard (sistema externo, refresca ~6 h;
-- ML rota el refresh_token en cada uso).
create table ops.ml_tokens (
  cuenta               text primary key,      -- 'BEKURA' | 'SANCORFASHION'
  vault_access_secret  uuid,                  -- id en vault.secrets
  vault_refresh_secret uuid,
  expires_at           timestamptz,
  updated_at           timestamptz not null default now()
);

-- Cola durable: reintentos con backoff y dead-letter.
create table ops.task_queue (
  id            bigint generated always as identity primary key,
  task_type     text not null,          -- 'procesar_webhook' | 'recalcular_pricing_ml' | 'dual_write_retry' | ...
  sku           citext,
  account_id    uuid references core.accounts(id),
  status        text not null default 'pending'
                check (status in ('pending','processing','done','error','dlq')),
  payload       jsonb,
  intentos      int not null default 0,
  max_intentos  int not null default 5,
  last_error    text,
  next_retry_at timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index idx_task_queue_pendientes on ops.task_queue(status, next_retry_at)
  where status in ('pending','error');

-- Trazabilidad del ETL: huérfanos, colisiones, filas descartadas, deltas de dual-write.
create table ops.migration_issues (
  id           bigint generated always as identity primary key,
  fase         text,                    -- 'F2-etl' | 'F3-dualwrite' | ...
  tabla_origen text,
  sku          citext,
  motivo       text not null,           -- 'huerfano' | 'colision_sku' | 'delta_dualwrite' | ...
  valor        jsonb,
  resuelto     boolean not null default false,
  created_at   timestamptz not null default now()
);

create index idx_migration_issues_motivo on ops.migration_issues(motivo, resuelto);


-- =============================================================================
-- migration — tablas sombra (hipótesis 10): validar sin tocar producción
-- =============================================================================
create schema if not exists migration;

-- Mapeo del ETL: bytes exactos del SKU en MySQL -> sku citext + ids resueltos.
create table migration.id_map (
  sku_original  text primary key,      -- tal cual estaba en MySQL
  sku           citext not null,
  wc_id         bigint,
  odoo_id       bigint,
  tabla_origen  text,
  created_at    timestamptz not null default now()
);

-- Resultado del cálculo NUEVO de costos (misma forma que costos_finales +
-- trazabilidad). El motor v2 escribe SOLO aquí durante la Fase 4.
create table migration.costs_preview (
  sku             citext not null,
  run_id          bigint not null,
  costo_producto  numeric(14,4),
  costo_cbm       numeric(14,4),
  costo_unitario  numeric(14,4),
  pct_comision    numeric(6,4),
  costo_comision  numeric(14,4),
  costo_fee_envio numeric(14,4),
  precio_sugerido numeric(14,2),
  precio_base     numeric(14,2),
  margen          numeric(6,4),
  formula_ver     text not null,
  fx_rate_used    numeric(12,6),
  calculado_at    timestamptz not null default now(),
  primary key (sku, run_id)
);

-- Delta por SKU y columna entre preview y producción (MySQL o costing).
create table migration.costs_differences (
  id          bigint generated always as identity primary key,
  run_id      bigint not null,
  sku         citext not null,
  columna     text not null,
  valor_prod  numeric(14,4),
  valor_nuevo numeric(14,4),
  delta_abs   numeric(14,4),
  delta_pct   numeric(10,4),
  clasificacion text not null default 'no_explicado',  -- 'explicado_bugfix' | 'explicado_formula' | 'no_explicado'
  nota        text,
  created_at  timestamptz not null default now()
);

create index idx_costs_differences_run on migration.costs_differences(run_id, clasificacion);

-- Corridas de conciliación (la línea base costos_finales vs costos_ml del
-- 2026-07-14 se registra como run #1).
create table migration.reconciliation_runs (
  id          bigint generated always as identity primary key,
  dominio     text not null,           -- 'costing' | 'channel' | 'ops' | ...
  descripcion text,
  conteos     jsonb,                   -- {tabla: {mysql: n, supabase: n}}
  checksums   jsonb,                   -- {tabla: {columna: suma}}
  resultado   text,                    -- 'ok' | 'con_deltas' | 'fallo'
  created_at  timestamptz not null default now()
);


-- =============================================================================
-- Triggers de historial: nada se sobrescribe sin rastro
-- =============================================================================
create or replace function costing.fn_cost_history() returns trigger
language plpgsql as $$
begin
  insert into costing.cost_history (sku, tabla, version, snapshot, cambiado_por, accion, formula_ver, currency)
  values (
    old.sku,
    tg_table_name,
    coalesce((select max(version) from costing.cost_history h
              where h.sku = old.sku and h.tabla = tg_table_name), 0) + 1,
    to_jsonb(old),
    current_setting('app.usuario', true),   -- el backend hace SET app.usuario = '...'
    coalesce(current_setting('app.accion', true), 'auto'),
    current_setting('app.formula_ver', true),
    'MXN'
  );
  return coalesce(new, old);
end $$;

create trigger trg_hist_costos_validados
  after update or delete on costing.costos_validados
  for each row execute function costing.fn_cost_history();

create trigger trg_hist_costos_finales
  after update or delete on costing.costos_finales
  for each row execute function costing.fn_cost_history();

-- updated_at automático en las tablas que lo declaran
create or replace function core.fn_touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

create trigger trg_touch_products before update on core.products
  for each row execute function core.fn_touch_updated_at();
create trigger trg_touch_listings before update on channel.listings
  for each row execute function core.fn_touch_updated_at();
create trigger trg_touch_costos_validados before update on costing.costos_validados
  for each row execute function core.fn_touch_updated_at();
create trigger trg_touch_costos_finales before update on costing.costos_finales
  for each row execute function core.fn_touch_updated_at();


-- =============================================================================
-- RLS — deny-by-default en TODO lo nuevo
-- (sin políticas: anon y authenticated no leen nada; service_role hace bypass.
--  El backend usa service_role desde env de Railway, NUNCA en el frontend.)
-- =============================================================================
alter table core.channels               enable row level security;
alter table core.accounts               enable row level security;
alter table core.products               enable row level security;
alter table core.usuarios               enable row level security;
-- Política mínima de identidad: cada usuario autenticado puede leer SU perfil
-- (lo necesita el frontend para mostrar nombre/rol tras el login).
create policy usuarios_leen_su_perfil on core.usuarios
  for select to authenticated using (id = auth.uid());
alter table channel.listings            enable row level security;
alter table channel.categories          enable row level security;
alter table channel.product_category    enable row level security;
alter table channel.listing_history     enable row level security;
alter table costing.costos_validados    enable row level security;
alter table costing.costos_finales      enable row level security;
alter table costing.legacy_costos_ml    enable row level security;
alter table costing.cost_history        enable row level security;
alter table costing.fx_rates            enable row level security;
alter table costing.pricing_params      enable row level security;
alter table enrich.supplier_data        enable row level security;
alter table enrich.ai_attributes        enable row level security;
alter table enrich.product_media        enable row level security;
alter table enrich.odoo_viability       enable row level security;
alter table ops.webhook_events          enable row level security;
alter table ops.channel_submissions     enable row level security;
alter table ops.process_log             enable row level security;
alter table ops.ml_tokens               enable row level security;
alter table ops.task_queue              enable row level security;
alter table ops.migration_issues        enable row level security;
alter table migration.id_map            enable row level security;
alter table migration.costs_preview     enable row level security;
alter table migration.costs_differences enable row level security;
alter table migration.reconciliation_runs enable row level security;

-- RECORDATORIO (fuera de este script): T1.5 activa RLS también en las 18 tablas
-- analytics existentes — hoy están SIN RLS y la anon key las lee [VERIFICADO
-- 2026-07-14]. Ejecutar solo tras confirmar P11 (el pipeline externo escribe
-- con service_role). (P11 confirmado 2026-07-15: MLREgisterDaily usa service_role.)

-- GRANTS: Supabase solo otorga permisos automáticos en `public`; los esquemas
-- nuevos necesitan GRANT explícito para service_role (PostgREST/REST futura).
-- `anon` y `authenticated` NO reciben nada: deny-by-default se mantiene.
-- (Verificado en DEV: sin este bloque, service_role recibe "permission denied
-- for schema"; el backend vía SUPABASE_DB_URL entra como `postgres` y no lo
-- necesita, pero la vía PostgREST sí.)
grant usage on schema core, channel, costing, enrich, ops, migration to service_role;
grant all on all tables    in schema core, channel, costing, enrich, ops, migration to service_role;
grant all on all sequences in schema core, channel, costing, enrich, ops, migration to service_role;
alter default privileges in schema core, channel, costing, enrich, ops, migration
  grant all on tables to service_role;
alter default privileges in schema core, channel, costing, enrich, ops, migration
  grant all on sequences to service_role;


-- =============================================================================
-- Datos semilla mínimos
-- =============================================================================
insert into core.channels (id, label, is_active) values
  ('general',       'General (WooCommerce)', true),
  ('mercado_libre', 'Mercado Libre',         true),
  ('amazon',        'Amazon',                true),
  ('tiktok',        'TikTok Shop',           false),
  ('walmart',       'Walmart',               false),
  ('temu',          'Temu',                  false),
  ('shein',         'Shein',                 false)
on conflict (id) do nothing;

insert into core.accounts (channel_id, legacy_code, label) values
  ('mercado_libre', 'BEKURA',        'Kubera'),
  ('mercado_libre', 'SANCORFASHION', 'San Corpe'),
  -- las tablas viejas usan cuenta='' para estos canales mono-cuenta:
  ('amazon',        'AMAZON',        'San Corpe (Amazon)'),
  ('general',       'GENERAL',       'WooCommerce chunche.shop')
on conflict (legacy_code) do nothing;

insert into costing.pricing_params (key, value) values
  ('MARGEN',        '0.48'),
  ('IVA',           '0.16'),
  ('TARIFA_CBM_M3', '7500'),
  ('TARIFA_ML_VER', '"v1-2026-07"')
on conflict do nothing;


-- =============================================================================
-- Fin del esquema propuesto v4.
--
-- Qué pasa con las tablas actuales de kubera_ml (resumen; detalle en
-- plan_maestro_supabase_v4.md §10):
--   * SE ELIMINA (con proceso de 21 pasos): ml_estado (0 filas, 0 refs).
--   * SE ARCHIVA congelada: costos_ml -> costing.legacy_costos_ml (NUNCA merge de importes).
--   * SE FUSIONAN (dueño = backend): ml_progress + amazon_progress +
--     canal_inventario -> channel.listings; costos_logs (+crear_logs, etc.) ->
--     ops.process_log; ml/amazon_backlog -> ops.channel_submissions (resumen) +
--     archivo MySQL (detalle).
--   * BLOQUEADAS por P1/P2 (dueño externo): productos*, categorias_ml,
--     odoo_ranking, scraping_alibaba, atributos_ia, imagenes_producto,
--     odoo_sync_*, sync_procesados, backlog_errores, pipeline_runs.
--     (*productos migra primero pero su ESCRITOR externo debe re-apuntar antes del corte.)
--   * NO SE TOCA: ml_tokens_dashboard (fuente de verdad de un sistema externo).
--   * POR CONFIRMAR: amazon_imagenes (tabla nueva 2026-07-14, dueño desconocido).
--
-- El archivo de detalle en MySQL Hostinger se crea con schema_hostinger_archivo.sql
-- (archivo_submission_detalle / archivo_webhook_detalle / archivo_process_detalle).
-- =============================================================================
