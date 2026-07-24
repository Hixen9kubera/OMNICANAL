-- ═══════════════════════════════════════════════════════════════════════════
-- PROPUESTA (NO APLICADA) — Inventario DROP / FULL / FBA con movimientos
-- Autor: sesión de Brandon · 2026-07-24 · Requiere GO de Eduardo antes de aplicar
--
-- CONTEXTO — lo que YA existe y funciona (no se toca):
--   * channel.listings.stock_own   → DROP  (almacén propio; el número de Woo)
--   * channel.listings.stock_full  → FULL/FBA (bodega del marketplace)
--   * channel.listings.is_fulfillment
--   * channel.listing_history      → TRIGGER que registra CADA cambio de
--                                    price/stock_own/stock_full/is_fulfillment/
--                                    status/situacion, con `detectado_via`
--   * channel.orders               → una fila por venta, con `es_fulfillment`
--   * MySQL canal_inventario       → foto por canal+cuenta (sync 15 min +
--                                    webhooks items/stock_locations/orders_v2)
--
-- LOS 3 HUECOS QUE ESTA PROPUESTA CIERRA:
--   1. `channel.orders.skus` es citext[] SIN CANTIDADES: no se puede saber
--      cuántas piezas movió una venta. Sin eso NO hay contabilidad de inventario.
--   2. No hay registro de MOVIMIENTOS: stock_own/stock_full son FOTOS (valor
--      actual). `listing_history` guarda el cambio pero no el MOTIVO ni la orden
--      que lo causó — no distingue "vendí 2" de "corregí a mano".
--   3. Las DEVOLUCIONES no tienen dónde registrarse (hoy una cancelación en Woo
--      repone stock, pero eso no queda trazado por canal/origen).
--
-- PRINCIPIO DE DISEÑO: la FOTO se conserva (stock_own/stock_full siguen siendo
-- la verdad operativa); el LEDGER es la explicación auditable de cómo llegó ahí.
-- Igual que el patrón de actas de la migración: se comparan y deben cuadrar.
-- ═══════════════════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────────────────
-- 1) LÍNEAS DE PEDIDO — cierra el hueco de las cantidades
-- ───────────────────────────────────────────────────────────────────────────
create table channel.order_items (
  canal             text not null references core.channels(id),
  cuenta            text not null,
  external_order_id text not null,
  linea             int  not null,              -- 1..n dentro de la orden
  sku               citext,                     -- puede ser NULL: venta sin SKU mapeado
  titulo            text,                       -- lo que mostró el marketplace
  cantidad          int    not null default 1,
  precio_unitario   numeric(14,2),
  comision          numeric(14,2),
  es_fulfillment    boolean not null default false,  -- FULL/FBA: no toca bodega propia
  primary key (canal, cuenta, external_order_id, linea),
  foreign key (canal, cuenta, external_order_id)
    references channel.orders (canal, cuenta, external_order_id) on delete cascade
);

create index idx_order_items_sku on channel.order_items (sku);

comment on table channel.order_items is
  'Líneas de cada venta con CANTIDADES. channel.orders.skus (array) no permite '
  'saber cuántas piezas se movieron; esto sí. Origen: services/pedidos_ml.py.';

-- ───────────────────────────────────────────────────────────────────────────
-- 2) LEDGER DE MOVIMIENTOS — append-only, explica cada cambio de inventario
-- ───────────────────────────────────────────────────────────────────────────
create table channel.inventory_moves (
  id             bigint generated always as identity primary key,
  sku            citext not null,
  canal          text references core.channels(id),   -- NULL = movimiento de bodega propia sin canal
  cuenta         text,                                -- BEKURA / SANCORFASHION / AMAZON / ...
  origen         text not null
                 check (origen in ('drop', 'full', 'fba')),
  -- drop = almacén propio (Woo) · full = bodega ML · fba = bodega Amazon
  tipo           text not null
                 check (tipo in ('venta', 'devolucion', 'cancelacion',
                                 'ajuste', 'reabastecimiento', 'sync')),
  unidades       int not null,          -- NEGATIVO resta (venta), POSITIVO suma (devolución)
  external_order_id text,               -- venta/devolución que lo causó
  wc_order_id    bigint,                -- pedido espejo en WooCommerce
  motivo         text,                  -- texto libre para ajustes manuales
  aplicado_por   text,                  -- 'webhook' | 'sondeo' | 'panel' | 'sync'
  creado_at      timestamptz not null default now()
);

create index idx_inv_moves_sku    on channel.inventory_moves (sku, creado_at desc);
create index idx_inv_moves_origen on channel.inventory_moves (origen, tipo, creado_at desc);
create index idx_inv_moves_orden  on channel.inventory_moves (external_order_id);

-- IDEMPOTENCIA (crítica: ML manda webhooks en ráfaga — regla 6 de CLAUDE.md).
-- Una misma orden NO puede generar dos veces el mismo movimiento del mismo SKU.
create unique index uq_inv_moves_orden_sku
  on channel.inventory_moves (canal, cuenta, external_order_id, sku, tipo)
  where external_order_id is not null;

comment on table channel.inventory_moves is
  'Ledger append-only de movimientos de inventario por origen (drop/full/fba). '
  'La FOTO sigue en channel.listings.stock_own/stock_full; esto explica y audita '
  'cómo llegó a ese valor. Nada se borra ni se actualiza: solo se agregan filas.';

-- ───────────────────────────────────────────────────────────────────────────
-- 3) VISTAS de conciliación (derivadas al vuelo — nunca tabla)
-- ───────────────────────────────────────────────────────────────────────────

-- Neto acumulado por SKU y origen según el LEDGER.
create or replace view channel.inventory_neto as
select sku, origen,
       sum(unidades)                                        as neto,
       sum(unidades) filter (where tipo = 'venta')          as vendido,
       sum(unidades) filter (where tipo = 'devolucion')     as devuelto,
       max(creado_at)                                       as ultimo_movimiento
from channel.inventory_moves
group by sku, origen;

-- ACTA: foto (listings) vs ledger (moves). Debe cuadrar; lo que no cuadra es
-- trabajo de limpieza — mismo espíritu que migration.reconciliation_runs.
create or replace view channel.inventory_deltas as
select l.sku, l.canal, l.cuenta,
       l.stock_full                                  as foto_full,
       coalesce(nf.neto, 0)                          as ledger_full,
       l.stock_full - coalesce(nf.neto, 0)           as delta_full
from channel.listings l
left join channel.inventory_neto nf
       on nf.sku = l.sku and nf.origen in ('full', 'fba')
where l.stock_full is not null;

-- ───────────────────────────────────────────────────────────────────────────
-- 4) RLS + grants (deny-by-default, igual que todo el v4)
-- ───────────────────────────────────────────────────────────────────────────
alter table channel.order_items      enable row level security;
alter table channel.inventory_moves  enable row level security;
grant all on channel.order_items     to service_role;
grant all on channel.inventory_moves to service_role;

-- ═══════════════════════════════════════════════════════════════════════════
-- CÓMO SE POBLARÍA (seams en el backend, tras el GO)
--
--   channel.order_items   ← pedidos_ml.sincronizar: ya tiene orden["items"] con
--                           sku/cantidad/precio_unitario/comision_ml. Es escribir
--                           lo que HOY se tira (el CSV `skus` trunca a 255).
--
--   channel.inventory_moves:
--     • VENTA no-FULL  → (origen='drop', tipo='venta',      unidades = -cantidad)
--     • VENTA FULL/FBA → (origen='full'|'fba', tipo='venta', unidades = -cantidad)
--     • CANCELACIÓN    → (tipo='cancelacion', unidades = +cantidad) SOLO si el
--                        pedido había descontado (no-FULL); un FULL cancelado no
--                        repone bodega propia (regla viva del candado de stock).
--     • DEVOLUCIÓN     → (tipo='devolucion', unidades = +cantidad)
--     • AJUSTE manual  → desde el panel, con `motivo` obligatorio.
--
-- TIEMPO REAL (lo que ya se puede aprovechar HOY, sin tablas nuevas):
--   * ML ya nos manda los topics `items`, `items_prices` y `stock_locations` a
--     /api/webhooks/ml, y con SYNC_ENABLED=true refrescan canal_inventario al
--     instante → el FULL se entera en segundos, no en 15 min. Solo falta que ese
--     refresco propague a channel.listings (hoy va por el sync de 15 min).
--   * Amazon FBA NO tiene webhook equivalente: requiere SP-API FBA Inventory
--     (getInventorySummaries) en el sondeo, o Notifications API vía SQS.
--
-- ORDEN SUGERIDO (bajo riesgo → alto valor):
--   1. Aplicar `order_items` y poblarlo desde pedidos_ml.sincronizar (solo
--      escribe; nada depende de él todavía).
--   2. Backfill de órdenes históricas desde los pedidos de Woo (tienen las
--      líneas completas).
--   3. Aplicar `inventory_moves` y encenderlo detrás de un flag, comparando
--      contra la foto con `channel.inventory_deltas` durante 14 días (misma
--      regla de corte que usa la migración).
-- ═══════════════════════════════════════════════════════════════════════════
