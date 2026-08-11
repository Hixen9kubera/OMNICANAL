-- ═══════════════════════════════════════════════════════════════════════════
-- 0011 — ENRICH: inteligencia de mercado (módulo Competencia).
--
-- Estado: APLICADA (Eduardo, 2026-08-10) — sandbox yvootpbz y BD kubera
-- producción tukwcvsi. Las 5 tablas nacen VACÍAS: migrar el dato desde
-- `propuestas` (paso 3 del plan) va aparte y con el cron de captura apagado.
--
-- Pre-chequeo de colisión de PK YA CORRIDO contra producción (solo lectura):
-- CERO colisiones por la llave nueva en las 4 tablas que migran —
-- rankings_categoria 3,000 · busquedas 1,816 · terminos_categoria 5,789 ·
-- skus 1,584. El insert…select no va a perder filas.
--
-- ORIGEN: el módulo Competencia vive hoy en el esquema aislado `propuestas`
-- (7 tablas + 2 vistas, en el worktree feat/barrido-cierre). Se creó ahí para
-- no tocar los esquemas del equipo mientras se validaba. Ya está en vivo:
-- 1,584 SKUs · 3,118 publicaciones · 3,000 filas de ranking · 1,816 de búsqueda.
-- Esta migración crea su destino definitivo; `propuestas` se retira después.
--
-- REVISADO POR EL CONSEJO (claude-opus, claude-sonnet, claude-haiku; 10-ago).
-- Cinco correcciones respecto al DDL propuesto originalmente:
--
--   1. `canal` DENTRO de la PK en las 5 tablas. El diseño original no lo tenía
--      — el mismo defecto que ya retiró `atributos_ia` y `enrich.ai_attributes`
--      (ver 0010). Y el esquema `propuestas` que se va a borrar YA lo tenía
--      resuelto: "El canal está desde el principio para que un ASIN de Amazon
--      sea otra fila y no un rediseño" (competencia_esquema.sql:84).
--   2. `market_listing_metrics` en vez de 5 columnas colgadas de
--      channel.listings. Recupera `periodo`, que el diseño original colapsaba.
--      channel.listings es el ESTADO ACTUAL del listing; esto son métricas de
--      una ventana de 30 días. Además visits_30d/units_30d no tienen
--      equivalente en Amazon (no es NULL: el concepto no existe).
--   3. RLS + grants explícitos por tabla (el DDL original los omitía).
--   4. Índices parciales que el esquema viejo sí tenía y el nuevo perdía
--      (competencia_esquema.sql:157-158) — los usa la "corona" del panel.
--   5. Prefijo `market_` de dominio: `search_results` a secas es demasiado
--      genérico para un `enrich` compartido que ya tiene supplier_data,
--      ai_attributes, product_media, odoo_viability y ai_content.
--
-- `sku` es citext en todas, para empatar con core.products.sku (citext). Con
-- text la FK funciona pero las comparaciones en las vistas serían
-- case-sensitive contra una tabla que no lo es.
--
-- NO incluye: las vistas market_skus_v / market_publicaciones_v (van después
-- de migrar el dato, paso 4 del plan) ni el drop de `propuestas` (migración
-- aparte, y solo tras un rename con ventana de enfriamiento).
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Más vendidos por categoría (competidores). 19 columnas de 23 ────────────
create table if not exists enrich.market_bestsellers (
  canal        text not null default 'mercado_libre' references core.channels(id),
  categoria_id text not null,
  nivel        text not null,                -- 'raiz' | 'hoja'
  posicion     int  not null,

  externo_id text,                           -- MLM… del competidor
  id_pagina  text,
  tipo       text,
  titulo     text,
  precio       numeric,
  precio_lista numeric,
  vendidos int,
  rating   numeric,
  reviews  int,
  seller   text,
  imagen   text,
  url      text,
  visitas_30d int,

  -- item_categoria_id mueve los nichos y pos_en_raiz. Hoy solo lo llenan las
  -- categorías capturadas con el navegador local (37/3,000): la ruta de Apify
  -- no lo trae. Es un pendiente de CAPTURA, no una columna sobrante.
  item_categoria_id     text,
  item_categoria_nombre text,

  es_nuestro  boolean default false,
  sku_nuestro citext references core.products(sku),

  capturado_en timestamptz not null default now(),
  primary key (canal, categoria_id, nivel, posicion)
);

-- ── SERP por término general. 12 columnas de 17 ─────────────────────────────
create table if not exists enrich.market_search_results (
  canal      text not null default 'mercado_libre' references core.channels(id),
  termino    text not null,
  externo_id text not null,

  posicion int,
  titulo   text,
  precio   numeric,
  imagen   text,
  url      text,
  seller   text,
  rating   numeric,

  es_nuestro  boolean default false,
  sku_nuestro citext references core.products(sku),

  capturado_en timestamptz not null default now(),
  primary key (canal, termino, externo_id)
);

-- ── Términos más buscados por categoría (de /trends). 4 columnas de 6 ───────
create table if not exists enrich.market_terms (
  canal        text not null default 'mercado_libre' references core.channels(id),
  categoria_id text not null,
  posicion     int  not null,
  termino      text not null,
  capturado_en timestamptz not null default now(),
  primary key (canal, categoria_id, posicion)
);

-- ── Config del SKU para Competencia. 5 columnas de 13 ───────────────────────
create table if not exists enrich.market_sku_config (
  sku   citext not null references core.products(sku),
  canal text   not null default 'mercado_libre' references core.channels(id),

  termino_general text,
  termino_origen  text,                      -- 'ia' | 'manual'
  activo          boolean not null default true,

  -- La categoría que Competencia MIDIÓ de la publicación viva. En 128 SKUs
  -- difiere de channel.product_category. NO se escribe encima de aquélla:
  -- chocaría con la regla 2 de CLAUDE.md ("la elección del PANEL manda").
  -- La vista resuelve con coalesce(categoria_id_real, product_category.category_id).
  categoria_id_real text,

  updated_at timestamptz not null default now(),
  primary key (sku, canal)
);

-- ── Métricas de NUESTRAS publicaciones, por periodo ─────────────────────────
-- Reemplaza a propuestas.publicacion_metricas. NO va en channel.listings:
-- esa tabla es el estado actual del listing y esto es una ventana de 30 días.
create table if not exists enrich.market_listing_metrics (
  sku     citext not null references core.products(sku),
  canal   text   not null references core.channels(id),
  cuenta  text   not null default '',        -- core.accounts.legacy_code
  periodo date   not null,                   -- primer día del mes medido

  account_id uuid references core.accounts(id),
  listing_id text,                           -- MLM… | ASIN

  title      text,
  sale_price numeric,                        -- precio CON descuento
  visits_30d int,                            -- ML: /items/{id}/visits
  units_30d  int,                            -- NULL donde el canal no lo expone

  metrics_updated_at timestamptz not null default now(),
  primary key (sku, canal, cuenta, periodo)
);

comment on column enrich.market_listing_metrics.visits_30d is
  'Mercado Libre: /items/{id}/visits. Amazon NO expone visitas de competencia '
  '(el sustituto es BSR + Buy Box): ahí queda NULL porque el concepto no existe, '
  'no porque falte el dato.';

comment on column enrich.market_listing_metrics.periodo is
  'La columna no obliga a guardar histórico. Si la decisión sigue siendo que '
  'cada corrida reemplaza, el proceso borra los periodos viejos — pero deja la '
  'puerta abierta a una serie de tendencia sin rediseñar la tabla.';

-- ── Índices ────────────────────────────────────────────────────────────────
-- Parciales sobre es_nuestro: los usa la "corona" del panel en cada carga.
-- El esquema viejo los tenía (competencia_esquema.sql:157-158).
create index if not exists idx_market_bestsellers_nuestro
  on enrich.market_bestsellers (sku_nuestro) where es_nuestro;
create index if not exists idx_market_search_nuestro
  on enrich.market_search_results (sku_nuestro) where es_nuestro;
create index if not exists idx_market_sku_config_activo
  on enrich.market_sku_config (canal) where activo;
create index if not exists idx_market_listing_metrics_periodo
  on enrich.market_listing_metrics (canal, periodo desc);

-- ── RLS y grants ───────────────────────────────────────────────────────────
-- Explícitos por tabla: el `alter default privileges` de 0001 solo aplica si
-- las tablas las crea el MISMO rol que corrió aquella sentencia. Si la
-- migración corre con otro owner, nacen sin grant y el backend recibe
-- permission denied.
alter table enrich.market_bestsellers      enable row level security;
alter table enrich.market_search_results   enable row level security;
alter table enrich.market_terms            enable row level security;
alter table enrich.market_sku_config       enable row level security;
alter table enrich.market_listing_metrics  enable row level security;

grant all on enrich.market_bestsellers     to service_role;
grant all on enrich.market_search_results  to service_role;
grant all on enrich.market_terms           to service_role;
grant all on enrich.market_sku_config      to service_role;
grant all on enrich.market_listing_metrics to service_role;

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN
--
--   select table_name from information_schema.tables
--    where table_schema='enrich' and table_name like 'market_%' order by 1;
--   -- esperado: 5 filas
--
--   select count(*) from enrich.market_bestsellers;   -- 0 al inicio
--
-- ANTES de migrar el dato (paso 3 del plan) — la PK de market_bestsellers
-- cambia de (categoria_id, nivel, externo_id) a (categoria_id, nivel, posicion).
-- Si hay duplicados por la llave nueva, el insert…select los DESCARTA EN
-- SILENCIO. Pre-chequear:
--
--   select categoria_id, nivel, posicion, count(*)
--     from propuestas.rankings_categoria
--    group by 1,2,3 having count(*) > 1;          -- debe dar 0 filas
--
--   select termino, externo_id, count(*)
--     from propuestas.busquedas
--    group by 1,2 having count(*) > 1;            -- debe dar 0 filas
-- ═══════════════════════════════════════════════════════════════════════════
