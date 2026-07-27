-- ═══════════════════════════════════════════════════════════════════════════
-- 0002 — Addenda aplicada en la BD kubera DESPUÉS del DDL v4 base:
--   a) channel.orders (GO Eduardo 2026-07-22; destino del GAP de pedidos)
--      + trigger touch de actualizado_at
--   b) índice único de enrich.product_media (upsert atómico del espejo)
-- Idempotente (IF NOT EXISTS / OR REPLACE) para poder re-aplicarse.
-- ═══════════════════════════════════════════════════════════════════════════

create table if not exists channel.orders (
  external_order_id text not null,
  canal          text not null references core.channels(id),
  cuenta         text not null,
  account_id     uuid references core.accounts(id),
  wc_order_id    bigint,
  estado_canal   text,
  estado_wc      text,
  total          numeric(14,2),
  comision       numeric(14,2),
  es_fulfillment boolean not null default false,
  skus           citext[],
  creado_at      timestamptz,
  actualizado_at timestamptz not null default now(),
  primary key (canal, cuenta, external_order_id)
);

comment on table channel.orders is
  'Registro por canal de cada venta congelada como pedido Woo (espejo de MySQL '
  'pedidos_ml). La fuente de verdad operativa sigue siendo WooCommerce; esto es '
  'la vista por canal. GO Eduardo 2026-07-22.';

create index if not exists idx_channel_orders_creado on channel.orders (creado_at desc);
create index if not exists idx_channel_orders_wc     on channel.orders (wc_order_id);
create index if not exists idx_channel_orders_cuenta on channel.orders (cuenta, creado_at desc);

create or replace function channel.tg_orders_touch() returns trigger
language plpgsql as $$
begin
  new.actualizado_at := now();
  return new;
end $$;

drop trigger if exists orders_touch on channel.orders;
create trigger orders_touch before update on channel.orders
  for each row execute function channel.tg_orders_touch();

alter table channel.orders enable row level security;
grant all on channel.orders to service_role;

create unique index if not exists uq_product_media_sku_kind_url
  on enrich.product_media (sku, kind, source_url);
