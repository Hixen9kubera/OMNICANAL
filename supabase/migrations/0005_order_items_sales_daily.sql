-- ═══════════════════════════════════════════════════════════════════════════
-- 0005 — LÍNEAS DE PEDIDO + vista de ventas diarias (GO Eduardo 2026-07-28).
--
-- Absorción de dailytrack (daily_sales), fase F1: channel.orders.skus es un
-- array SIN cantidades ni item_id — imposible derivar ventas por día×item.
-- Esta tabla guarda las líneas que pedidos_ml.sincronizar YA tiene en memoria
-- (meli.obtener_orden / pedidos_amazon._normalizar / pedidos_m2e traen
-- item_id, sku, cantidad, precio_unitario y sale_fee): cero llamadas nuevas.
--
-- Es channel.order_items de propuesta_inventario_drop_full.sql + `item_id`
-- (MLM…/OrderItemId): sin él no se puede casar con daily_sales(date,cuenta,
-- item_id) ni con channel.listings.listing_id.
--
-- DECISIONES (Eduardo 2026-07-28):
--   * cancelados SE GUARDAN con su estado; la vista sales_daily los EXCLUYE.
--   * corte del día = America/Mexico_City.
-- Idempotente (IF NOT EXISTS / OR REPLACE).
-- ═══════════════════════════════════════════════════════════════════════════

create table if not exists channel.order_items (
  canal             text not null references core.channels(id),
  cuenta            text not null,
  external_order_id text not null,
  linea             int  not null,              -- 1..n dentro de la orden
  item_id           text,                       -- MLM… / OrderItemId / ASIN
  sku               citext,                     -- NULL = venta sin SKU mapeado (sin FK a propósito)
  titulo            text,                       -- lo que mostró el marketplace
  cantidad          int    not null default 1,
  precio_unitario   numeric(14,2),
  comision          numeric(14,2),              -- TOTAL de la línea (fee unitario × cantidad)
  es_fulfillment    boolean not null default false,
  primary key (canal, cuenta, external_order_id, linea),
  foreign key (canal, cuenta, external_order_id)
    references channel.orders (canal, cuenta, external_order_id) on delete cascade
);

create index if not exists idx_order_items_sku     on channel.order_items (sku);
create index if not exists idx_order_items_item_id on channel.order_items (item_id);

comment on table channel.order_items is
  'Líneas de cada venta con CANTIDADES e item_id. channel.orders.skus (array) '
  'no permite saber cuántas piezas movió una venta ni contra qué publicación; '
  'esto sí. Origen: seam en services/pedidos_ml.py (espejo pedidos_ml_items). '
  'Los importes quedan CONGELADOS al primer registro (regla del pedido '
  'histórico); solo comision admite el paso 0 → valor real.';

-- Vista de VENTAS DIARIAS: el equivalente 1:1 de daily_sales (dailytrack),
-- derivado al vuelo — nunca tabla. Cancelados EXCLUIDOS (se conservan en las
-- tablas base con su estado); día local America/Mexico_City.
create or replace view channel.sales_daily as
select (o.creado_at at time zone 'America/Mexico_City')::date as date,
       o.canal,
       o.cuenta,
       i.item_id,
       i.sku,
       bool_or(i.es_fulfillment)                as is_full,
       sum(i.cantidad)                          as units_sold,
       sum(i.precio_unitario * i.cantidad)      as revenue,
       sum(i.comision)                          as sale_fee
from channel.order_items i
join channel.orders o using (canal, cuenta, external_order_id)
where coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
group by 1, 2, 3, 4, 5;

comment on view channel.sales_daily is
  'Ventas por día×cuenta×item (reemplazo de dailytrack daily_sales). Día en '
  'America/Mexico_City; cancelados excluidos. La historia previa al seam vive '
  'en analytics.sales_daily_hist (migración 0006).';

alter table channel.order_items enable row level security;
grant all on channel.order_items to service_role;
grant select on channel.sales_daily to service_role;
