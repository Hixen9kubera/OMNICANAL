-- ═══════════════════════════════════════════════════════════════════════════
-- ov_fixture.sql — lo MÍNIMO de kubera que las órdenes de venta propias LEEN y
-- que las migraciones 0064 (órdenes de venta) y 0065 (inventario de kubera)
-- dan por hecho, para probar services/ordenes_venta.py en un Postgres
-- DESECHABLE (local). No es una migración y JAMÁS se corre contra kubera ni
-- contra el sandbox: tira y recrea esquemas.
--
-- Uso (lo hace solo tests/test_ordenes_venta_bd.py en su setUpModule; a mano):
--   psql -h 127.0.0.1 -p 54329 -U postgres -d ov_b1 -f backend/tests/ov_fixture.sql
--   psql … -f supabase/migrations/0064_ops_ordenes_venta.sql
--   psql … -f supabase/migrations/0065_ops_inventario_kubera.sql
--
-- QUÉ PIDE CADA MIGRACIÓN DE AQUÍ
--   · 0064: el esquema `ops`, la extensión citext y los roles de Supabase
--     (service_role, anon, authenticated) para sus grants.
--   · 0065: `ops.stock_watch_photo` (su paso 0 truena sin ella y el ALTER final
--     le agrega `stock_kubera`) y `channel.return_items` (la vista
--     ops.devoluciones_vs_canal_v lee canal, cuenta, external_return_id, sku y
--     cantidad; la crea la 0065 y la QUITA la 0067, que la prueba aplica
--     después). Por eso van aquí `channel.returns` y `channel.return_items`
--     MÍNIMAS: así la prueba no depende de aplicar la 0049, que arrastra vistas
--     y triggers de devoluciones del canal que este módulo no toca.
--   · El servicio: `ops.automatizacion_flags` (las banderas son filas),
--     `storage.buckets` (¿existe el bucket `ordenes-venta`?), `core.products`,
--     `core.usuarios`, `channel.orders` + `channel.order_items` y la bitácora de
--     Automatización (`ops.odoo_sale_orders`), que sólo se LEEN.
--
-- Las formas son las de PRODUCCIÓN medidas el 2-oct-2026 (no las de las
-- migraciones viejas del repo: ops.odoo_sale_orders lleva `cuenta` en la PK).
-- Aquí no hay ni un dato: los siembra cada prueba, con SKUs ZZPRUEBA-* y cuentas
-- como CUENTAPRUEBA (el repo es público).
-- ═══════════════════════════════════════════════════════════════════════════

create extension if not exists citext;

do $$ begin
    if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role; end if;
    if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon; end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated; end if;
end $$;

drop schema if exists ops cascade;
drop schema if exists core cascade;
drop schema if exists channel cascade;
drop schema if exists storage cascade;
create schema ops;
create schema core;
create schema channel;
create schema storage;

-- Storage: sólo la tabla de buckets. La 0064 YA NO crea el bucket
-- `ordenes-venta` (va aparte, con su retención decidida): nace vacía, y la
-- prueba que necesita el bucket inserta su fila.
create table storage.buckets (
    id text primary key, name text not null, public boolean default false,
    file_size_limit bigint, allowed_mime_types text[]
);

-- core ------------------------------------------------------------------------
create table core.channels (id text primary key);
insert into core.channels values ('general'), ('mercado_libre'), ('amazon'), ('walmart'),
                                 ('temu'), ('shein'), ('tiktok');

create table core.accounts (
    id uuid primary key default gen_random_uuid(),
    channel_id text not null references core.channels (id),
    legacy_code text unique, external_id text, label text not null,
    is_active boolean not null default true, created_at timestamptz not null default now()
);

create table core.products (
    sku citext primary key, name text, wc_id bigint
);

create table core.usuarios (
    id uuid primary key default gen_random_uuid(),
    nombre text not null, email citext unique,
    rol text not null default 'operador' check (rol in ('admin', 'operador', 'lectura')),
    activo boolean not null default true
);

-- ops (lo que ya existe en producción y el módulo lee) ---------------------------
create table ops.stock_watch_photo (
    sku citext not null primary key, stock_woo integer, stock_odoo integer,
    actualizado timestamptz not null default now()
);

create table ops.automatizacion_flags (
    flag text primary key check (flag ~ '^[a-z0-9_]+$'), valor boolean not null,
    motivo text, creado_at timestamptz default now(),
    actualizado_at timestamptz default now(), actualizado_por text
);

create table ops.odoo_sale_orders (
    canal text not null, cuenta text not null, external_order_id text not null,
    odoo_order_id bigint, odoo_name text, estado text, accion text not null,
    almacen_id integer, almacen text, cobertura text, guia text, paqueteria text,
    total numeric(12,2), motivo text,
    creado_at timestamptz not null default now(), actualizado_at timestamptz not null default now(),
    primary key (canal, cuenta, external_order_id)
);
create table ops.odoo_sale_order_items (
    canal text not null, cuenta text not null, external_order_id text not null, linea integer not null,
    sku citext, titulo text, imagen text, cantidad integer check (cantidad > 0),
    precio_unitario numeric(12,2), stock_libre jsonb, medido_at timestamptz,
    primary key (canal, cuenta, external_order_id, linea),
    foreign key (canal, cuenta, external_order_id)
        references ops.odoo_sale_orders (canal, cuenta, external_order_id) on delete cascade
);

-- channel -------------------------------------------------------------------------
create table channel.orders (
    external_order_id text not null, canal text not null references core.channels (id),
    cuenta text not null, account_id uuid, wc_order_id bigint,
    estado_canal text, estado_wc text, total numeric(14,2), comision numeric(14,2),
    es_fulfillment boolean not null default false, skus citext[],
    creado_at timestamptz, actualizado_at timestamptz,
    stock_compensado_at timestamptz, stock_revertido_at timestamptz,
    primary key (canal, cuenta, external_order_id)
);
create table channel.order_items (
    canal text not null references core.channels (id), cuenta text not null,
    external_order_id text not null, linea int not null, item_id text, sku citext,
    titulo text, cantidad int not null default 1, precio_unitario numeric(14,2),
    comision numeric(14,2), es_fulfillment boolean not null default false,
    primary key (canal, cuenta, external_order_id, linea),
    foreign key (canal, cuenta, external_order_id)
        references channel.orders (canal, cuenta, external_order_id) on delete cascade
);

-- Devoluciones del canal (de la 0049), MÍNIMAS: sólo las columnas que usa la
-- vista ops.devoluciones_vs_canal_v de la 0065 (la 0067 la quita después, pero
-- la 0065 la crea y sin estas columnas truena), y las llaves que las ligan.
create table channel.returns (
    canal text not null references core.channels (id), cuenta text not null,
    external_return_id text not null, external_order_id text, estado text,
    primary key (canal, cuenta, external_return_id)
);
create table channel.return_items (
    canal text not null references core.channels (id), cuenta text not null,
    external_return_id text not null, linea int not null,
    sku citext, cantidad int not null default 1 check (cantidad > 0),
    primary key (canal, cuenta, external_return_id, linea),
    foreign key (canal, cuenta, external_return_id)
        references channel.returns (canal, cuenta, external_return_id) on delete cascade
);
