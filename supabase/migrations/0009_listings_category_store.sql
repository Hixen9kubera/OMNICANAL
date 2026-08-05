-- ============================================================================
-- 0009_listings_category_store.sql — dos columnas en channel.listings
--
--   category_id  → categoría del canal (MLM162997), desde channel.product_category
--   store_name   → tienda legible (BEKURA / SANCORFASHION), desde core.accounts
--
-- Cómo correrlo:
--   Sandbox:    backend/.venv/bin/python backend/scripts/aplicar_migraciones.py
--   Producción: Dashboard de la BD kubera → SQL Editor → pegar TODO → Run.
--
-- Idempotente: se puede volver a correr sin romper nada.
--
-- ── DE DÓNDE SALE CADA DATO ────────────────────────────────────────────────
--
-- Las dos relaciones YA EXISTEN en el esquema; estas columnas solo las hacen
-- legibles en la misma fila para poder filtrar y agrupar sin JOIN:
--
--   category_id ← channel.product_category (sku, channel_id) → category_id
--       `channel.listings` tiene PK (sku, account_id, canal) y product_category
--       tiene PK (sku, channel_id), así que listings.sku + listings.canal ya es
--       la llave completa. La fuente de verdad sigue siendo product_category,
--       que respeta la regla de la casa "el panel manda" (etl_channel_categories.py
--       carga categorias_ml y encima pisa con ml_categoria_id, source='panel').
--
--   store_name  ← core.accounts.legacy_code, vía el account_id que ya está en la fila
--       `listings.account_id` ya es FK a core.accounts(id). Y como account_id es
--       parte de la PK, para una fila dada NUNCA cambia: esta columna no puede
--       quedar rancia.
--
-- NO se llama a la API de Mercado Libre para la categoría: serían ~3.7k llamadas
-- para un dato que ya está en la base. Además la API devuelve la categoría VIVA
-- de la publicación, que puede diferir de la curada por el panel — usarla
-- invertiría esa regla.
--
-- ── AVISO SOBRE category_id ────────────────────────────────────────────────
-- Es una denormalización y SÍ puede quedar rancia: si mañana el ETL reasigna la
-- categoría de un SKU en product_category, esta columna no se enteraría sola.
-- Este archivo solo la crea y la rellena. Para mantenerla al día hay que
-- propagarla desde etl_channel_categories.py (que es quien escribe la fuente de
-- verdad) — no está incluido aquí a propósito, es un cambio a un ETL de producción.
-- ============================================================================

-- ── 1. Las columnas ────────────────────────────────────────────────────────
alter table channel.listings
    add column if not exists category_id text;

alter table channel.listings
    add column if not exists store_name text;

comment on column channel.listings.category_id is
    'Categoría del canal, denormalizada desde channel.product_category para poder '
    'agrupar sin JOIN. Fuente de verdad: product_category (respeta "el panel manda"). '
    'Nullable: un listing sin categorizar debe poder insertarse — a esta tabla le '
    'escriben el sync de 15 min, los webhooks, el espejo kubera y fanout.';

comment on column channel.listings.store_name is
    'Tienda en forma legible (BEKURA / SANCORFASHION), derivada de '
    'core.accounts.legacy_code vía account_id. Fuente de verdad: account_id, que '
    'además es parte de la PK, por lo que esta columna no puede quedar rancia.';


-- ── 2. Índices para filtrar y agrupar ──────────────────────────────────────
create index if not exists idx_channel_listings_category_id
    on channel.listings (canal, category_id)
    where category_id is not null;

create index if not exists idx_channel_listings_store_name
    on channel.listings (store_name)
    where store_name is not null;


-- ── 3. Relleno ─────────────────────────────────────────────────────────────
-- `is distinct from` en los dos UPDATE para NO tocar filas que ya están bien:
-- channel.listings tiene los triggers trg_hist_listings y trg_touch_listings,
-- que se disparan en cada UPDATE. Sin ese filtro se inventaría historial de
-- cambios y se movería updated_at de miles de filas que no cambiaron.

update channel.listings l
   set category_id = pc.category_id
  from channel.product_category pc
 where pc.sku = l.sku
   and pc.channel_id = l.canal
   and l.category_id is distinct from pc.category_id;

update channel.listings l
   set store_name = a.legacy_code
  from core.accounts a
 where a.id = l.account_id
   and a.legacy_code is not null
   and l.store_name is distinct from a.legacy_code;


-- ── 4. Verificación (para leer el resultado, no cambia nada) ───────────────
-- Cuántas filas quedaron con cada dato y cuántas se quedaron sin él.
select count(*)                                             as listings,
       count(category_id)                                   as con_category_id,
       count(*) - count(category_id)                        as sin_category_id,
       count(store_name)                                    as con_store_name,
       count(*) - count(store_name)                         as sin_store_name,
       count(distinct category_id)                          as categorias_distintas,
       count(distinct store_name)                           as tiendas_distintas
  from channel.listings;
