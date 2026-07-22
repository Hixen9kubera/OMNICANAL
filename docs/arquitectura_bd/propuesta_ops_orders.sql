-- ═══════════════════════════════════════════════════════════════════════════
-- APLICADA ✔ (GO de Eduardo 2026-07-22): destino v4 para el GAP de pedidos
-- Ejecutada tal cual en la BD kubera (tukwcvsi) + trigger touch de
-- actualizado_at (before update). El mismo día se creó también el índice
-- único uq_product_media_sku_kind_url en enrich.product_media (tabla vacía,
-- sin duplicados) — el upsert del espejo ya puede ser atómico.
-- Decisiones confirmadas: (1) channel.orders (no esquema sales);
-- (2) ventas_horarias/ventas_sync NO migran (caché regenerable — confirmado);
-- (3) sin trigger de historial: actualizado_at basta.
-- Siguiente paso: agregar el seam en pedidos_ml.sincronizar (espejo kubera).
-- Autor: espejo kubera (misión 2026-07-22) · GO: Eduardo
--
-- El censo del espejo detectó que `pedidos_ml` (MySQL) NO tiene tabla destino
-- en ESQUEMA_kubera_v4_propuesto.sql. Es el corazón operativo del tab VENTAS:
-- cada venta de ML / Amazon / Temu / TikTok se congela como pedido de Woo y se
-- registra ahí (services/pedidos_ml.py + pedidos_amazon.py + pedidos_m2e.py,
-- ~2,900 filas al 2026-07-22, PK = orden del marketplace + cuenta).
--
-- Esta propuesta sigue las convenciones del v4 (citext, timestamptz, FKs a
-- core.*, RLS deny-by-default). Mientras no se confirme y aplique, el censo
-- del espejo la reporta como `gap_sin_destino` y NO se espeja nada de pedidos.
-- ═══════════════════════════════════════════════════════════════════════════

create table channel.orders (
  external_order_id text not null,   -- id de la orden en el marketplace (ML/Amazon/M2E)
  canal          text not null references core.channels(id),
  cuenta         text not null,      -- legacy_code (BEKURA/SANCORFASHION/AMAZON/...)
  account_id     uuid references core.accounts(id),   -- se puebla al converger cuentas
  wc_order_id    bigint,             -- pedido espejo en WooCommerce (registro congelado)
  estado_canal   text,               -- estado en el marketplace (paid/cancelled/...)
  estado_wc      text,               -- estado del pedido Woo (processing/cancelled/...)
  total          numeric(14,2),      -- precio REAL de venta (congelado)
  comision       numeric(14,2),      -- comisión del canal (Amazon: 0 hasta Finances API)
  es_fulfillment boolean not null default false,  -- FULL/FBA: no descuenta bodega
  skus           citext[],           -- SKUs de las líneas (detalle completo vive en Woo)
  creado_at      timestamptz,        -- fecha real de la VENTA en el canal
  actualizado_at timestamptz not null default now(),
  primary key (canal, cuenta, external_order_id)
);

comment on table channel.orders is
  'Registro por canal de cada venta congelada como pedido Woo (espejo de MySQL pedidos_ml). '
  'La fuente de verdad operativa sigue siendo WooCommerce; esto es la vista por canal.';

create index idx_channel_orders_creado on channel.orders (creado_at desc);
create index idx_channel_orders_wc     on channel.orders (wc_order_id);
create index idx_channel_orders_cuenta on channel.orders (cuenta, creado_at desc);

alter table channel.orders enable row level security;
grant all on channel.orders to service_role;

-- Notas de mapeo (MySQL pedidos_ml → channel.orders):
--   ml_order_id → external_order_id      cuenta → cuenta (y account_id al converger)
--   estado_ml → estado_canal             estado_wc → estado_wc
--   total/comision → total/comision      es_full → es_fulfillment
--   skus (CSV varchar 255) → skus citext[] (¡el CSV trunca a 255: pérdida real
--     en pedidos con muchas líneas — el array lo corrige!)
--   creado → creado_at (ya es la fecha de la venta, fix 1fb9f1d)
--
-- Decisiones abiertas para Eduardo:
--   1. ¿channel.orders o un esquema nuevo `sales`? (aquí se propone channel
--      porque la llave natural es canal+cuenta, igual que listings).
--   2. ventas_horarias / ventas_sync NO migran (caché regenerable de la API
--      de ML) — confirmar.
--   3. ¿Trigger de historial tipo listing_history? Para pedidos el UPDATE solo
--      mueve estado — probablemente baste `actualizado_at`.
