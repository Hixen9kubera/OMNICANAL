-- ═══════════════════════════════════════════════════════════════════════════
-- 0006 — ARCHIVO de la historia de dailytrackMeli (GO Eduardo 2026-07-28).
--
-- Las series diarias de xaxbkijc están MUERTAS desde el 15-jul (ingest-cron
-- de José; disco lleno 53100 + timeouts 57014) y guardan la ÚNICA historia de
-- ventas previa al webhook (1–15 jul medido: daily_sales $3.19M vs
-- channel.orders $0.24M — nosotros solo vimos el 7%). Esto es el destino del
-- ETL one-shot ANTES de dar de baja el proyecto.
--
-- OPTIMIZADO EN VUELO (decisión Eduardo 2026-07-28): no se copia la foto
-- diaria cruda de daily_stock (365,542 filas, 91.1% idénticas al día previo,
-- medido con 200 series completas) sino el REGISTRO DE CAMBIOS (~9%):
-- una fila por item×cambio con vigencia [valid_from, valid_to). Cualquier día
-- se reconstruye con: valid_from <= D and (valid_to is null or valid_to > D).
-- Los atributos estáticos (title, dimensions, size_category, warehouse,
-- start_time) NO se archivan: viven en core.products / costos_validados.
-- daily_visits queda FUERA del alcance (decisión 2026-07-28); su respaldo va
-- en el dump completo previo a la baja del proyecto.
--
-- CONGELADO: nadie escribe aquí después del ETL. Lo vivo nace en
-- channel.sales_daily (vista, 0005) y en el cierre diario de stock (F2).
-- Idempotente (IF NOT EXISTS).
-- ═══════════════════════════════════════════════════════════════════════════

create schema if not exists analytics;

-- Espejo 1:1 de daily_sales (cada fila es una venta real del día — sin
-- redundancia que podar; solo se tiran columnas vacías/repetidas:
-- title, gross_revenue, node_id, created_at, updated_at).
create table if not exists analytics.sales_daily_hist (
  date       date not null,
  cuenta     text not null,               -- 'BEKURA' | 'SANCORFASHION'
  item_id    text not null,               -- MLM…
  sku        citext,                      -- SIN FK: puede traer SKUs ya inexistentes
  is_full    boolean,
  units_sold int,
  revenue    numeric(14,2),
  sale_fee   numeric(14,2),
  primary key (date, cuenta, item_id)
);

comment on table analytics.sales_daily_hist is
  'ARCHIVO CONGELADO de dailytrack daily_sales (2025-12-27 → 2026-07-15). '
  'Solo lectura tras el ETL. Lo vivo = channel.sales_daily. Fecha simple tal '
  'cual la guardó el cron origen (no re-bucketizada).';

-- daily_stock comprimido run-length: una fila por item×cambio de
-- (stock_full, stock_odoo, price, status, logistic_type).
create table if not exists analytics.stock_hist (
  cuenta        text not null,
  item_id       text not null,
  sku           citext,
  valid_from    date not null,            -- primer día observado con esta firma
  valid_to      date,                     -- primer día con firma distinta; NULL = vigente al corte
  stock_full    int,
  stock_odoo    int,                      -- histórico tal cual (Odoo era el ancla del origen)
  price         numeric(14,2),
  status        text,
  logistic_type text,
  primary key (cuenta, item_id, valid_from)
);

create index if not exists idx_stock_hist_sku on analytics.stock_hist (sku);

comment on table analytics.stock_hist is
  'ARCHIVO CONGELADO de dailytrack daily_stock (2026-04-29 → 2026-07-15) '
  'comprimido a registro de cambios (~9% de las filas originales; el resto '
  'era foto idéntica al día previo). Estado de un día D: valid_from <= D and '
  '(valid_to is null or valid_to > D). valid_to NULL = seguía vigente al '
  'corte 2026-07-15.';

-- Vista de conveniencia: reconstruye la foto de un día (uso puntual).
create or replace view analytics.stock_hist_dia as
select h.*, d.dia
from analytics.stock_hist h
join lateral (
  select generate_series(h.valid_from,
                         coalesce(h.valid_to - 1, date '2026-07-15'),
                         interval '1 day')::date as dia
) d on true;

comment on view analytics.stock_hist_dia is
  'Expansión día a día de stock_hist (equivale a la daily_stock original). '
  'Usar con filtro de fecha — expandida completa son ~365k filas.';

alter table analytics.sales_daily_hist enable row level security;
alter table analytics.stock_hist       enable row level security;

grant usage on schema analytics to service_role;
grant all on all tables in schema analytics to service_role;
alter default privileges in schema analytics grant all on tables to service_role;
grant select on analytics.stock_hist_dia to service_role;
