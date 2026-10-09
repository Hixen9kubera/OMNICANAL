-- 0070_almacen_historial_movimientos.sql
--
-- QUÉ HACE
--   1. Crea `almacen.historial_movimientos`: TODO lo que se le movió a un SKU en
--      un cedis, un renglón por movimiento. Pedido de Brandon (9-oct-2026): pasar
--      a kubera el historial de TEXCO II que hoy sólo vive en Odoo, «para que lo
--      tengamos como fuente de información», y seguir observando a Odoo cada 30
--      minutos «hasta su desactivación de bodega».
--   2. Ajusta `almacen.locations` (0069) a la regla nueva de Brandon: «todos los
--      archivados no los traigas, ya que archivados pueden ser error». Borra los
--      renglones de productos archivados en Odoo y quita la columna
--      `archivado_odoo`, que ya no tiene nada que marcar. Con eso el único de
--      (cedis, sku, ubicacion) deja de ser parcial.
--   3. La función que mantiene `actualizado_at` pasa a ser una sola para las dos
--      tablas (`almacen.tg_tocar_actualizado_at`).
--
--   Las dos tablas las llena `services/almacen_odoo.py`: el vigilante del backend
--   cada 30 minutos, o a mano con `backend/scripts/copiar_almacen_odoo.py`.
--
-- Estado: APLICADA en PRODUCCIÓN (tukwcvsi) el 9-oct-2026 18:45:55 UTC, desde el
-- chat de Inventario y por instrucción de Brandon («dale, crea la migración de lo
-- que necesitas y todos los archivados no los traigas»); sin acta. Antes, la
-- migración entera dentro de una transacción que terminó en ROLLBACK: 17
-- comprobaciones y 6 rechazos esperados; las mismas 17 después del COMMIT. Borró
-- 37 renglones de productos archivados de `almacen.locations` (quedaron 1,626).
-- CARGADA a las 18:47 UTC: 6,307 movimientos y la foto en 1,628 renglones; el
-- historial reproduce la foto en 1,225 de 1,225 SKUs. En el SANDBOX no está.
-- Apunte para Eduardo: docs/MIGRACION_0070_NOTA_EDUARDO.md.
--
-- LA TABLA
--   · Un renglón = una línea de movimiento HECHA en Odoo (`stock.move.line`),
--     vista desde un cedis. `cedis` es obligatorio, como en `locations`.
--   · `delta` es lo que el movimiento le hizo al cedis: + entran piezas, − salen,
--     0 sólo cambiaron de lugar adentro. `piezas` es cuántas se movieron.
--   · `ubicacion_origen` / `ubicacion_destino` van escritas como en
--     `almacen.locations` y sólo se llenan del lado que está DENTRO del cedis.
--     La ruta completa de Odoo de los dos lados queda en `odoo_origen` /
--     `odoo_destino`.
--   · `causa` usa las palabras del libro de bodega del panel
--     (`services.odoo._causa`): entrada · venta · envio_full · traspaso ·
--     preparacion · ajuste · merma · devolucion · cuarentena · otro. No lleva
--     CHECK con la lista: la clasificación vive en el panel y puede crecer.
--   · Sólo productos ACTIVOS de Odoo.
--
-- LA PRUEBA DE QUE ESTÁ COMPLETA
--   Σ delta del historial de un SKU = Σ piezas de su foto en `almacen.locations`.
--   Medido contra Odoo el 9-oct-2026: cuadran 1,414 de 1,414 productos activos y
--   1,626 de 1,626 pares producto + ubicación. El vigilante lo mide en cada
--   pasada.
--
-- LO QUE NO ES
--   No es el libro de las bodegas de kubera: ése es `almacen.stock_mov`, con sus
--   garantías. Aquí queda lo que registró Odoo. Cuando TEXCO II sea de kubera,
--   sus movimientos nuevos nacen en `stock_mov` y esta tabla conserva el pasado.
--
-- DEPENDE de la 0069 (`almacen.locations`): truena si no está. No depende de la
-- 0068: la llave a la bodega se amarra a la tabla donde viva.
-- IDEMPOTENTE: se puede correr dos veces.
-- REVERSA: `drop table almacen.historial_movimientos;` La columna
-- `archivado_odoo` no vuelve sola: es volver a correr la 0069 sobre una base sin
-- la tabla, o agregarla a mano.

begin;

set local lock_timeout = '5s';
set local statement_timeout = '60s';

create schema if not exists almacen;
grant usage on schema almacen to service_role;

do $previo$
begin
  if to_regclass('almacen.locations') is null then
    raise exception '0070: falta almacen.locations. Corre antes la 0069.';
  end if;
end
$previo$;

-- ───────────────────────────────────────────────────────────────────────────
-- 1) `actualizado_at` lo mantiene la base, con UNA función para todo el esquema
-- ───────────────────────────────────────────────────────────────────────────
create or replace function almacen.tg_tocar_actualizado_at() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  new.actualizado_at := now();
  return new;
end
$$;

drop trigger if exists locations_tocar_trg on almacen.locations;
create trigger locations_tocar_trg
  before update on almacen.locations
  for each row execute function almacen.tg_tocar_actualizado_at();
drop function if exists almacen.locations_tocar();

-- ───────────────────────────────────────────────────────────────────────────
-- 2) `almacen.locations` sin productos archivados
-- ───────────────────────────────────────────────────────────────────────────
do $archivados$
declare
  borrados integer := 0;
begin
  if exists (select 1 from information_schema.columns
              where table_schema = 'almacen' and table_name = 'locations'
                and column_name = 'archivado_odoo') then
    delete from almacen.locations where archivado_odoo;
    get diagnostics borrados = row_count;
    -- El único parcial y el CHECK nombran la columna: se van antes que ella.
    drop index if exists almacen.locations_sku_ubicacion_uq;
    alter table almacen.locations drop constraint if exists locations_odoo_chk;
    alter table almacen.locations drop column archivado_odoo;
    raise notice '0070: % renglón(es) de productos archivados borrados de almacen.locations', borrados;
  end if;
end
$archivados$;

create unique index if not exists locations_sku_ubicacion_uq
    on almacen.locations (cedis, sku, ubicacion);

do $chk$
begin
  if not exists (select 1 from pg_constraint
                  where conname = 'locations_odoo_chk'
                    and conrelid = 'almacen.locations'::regclass) then
    -- Sólo lo que vino de Odoo puede decir de qué renglón de Odoo salió.
    alter table almacen.locations add constraint locations_odoo_chk check (
        origen = 'odoo' or (odoo_quant_id is null and odoo_product_id is null
                            and odoo_ubicacion is null));
  end if;
end
$chk$;

comment on table almacen.locations is
  'Dónde está cada SKU dentro de un cedis: un renglón por SKU y ubicación. cedis es obligatorio (código de la bodega; su nombre sale del catálogo de bodegas). Sin rack = ubicacion ''SIN UBICAR''. piezas es INFORMATIVA: el saldo oficial es stock_almacen.fisico. Sólo productos ACTIVOS de Odoo. La mantiene services/almacen_odoo.py mientras el cedis sea de Odoo.';

-- ───────────────────────────────────────────────────────────────────────────
-- 3) `almacen.historial_movimientos`
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists almacen.historial_movimientos (
    id                bigint      generated always as identity,
    cedis             text        not null,          -- código de la bodega: TEX2 = «TEXCO II»
    sku               citext      not null,
    fecha             timestamptz not null,          -- cuándo se hizo el movimiento en Odoo
    causa             text        not null,          -- entrada | venta | envio_full | traspaso | preparacion | ajuste | merma | …
    piezas            integer     not null,          -- cuántas se movieron
    delta             integer     not null,          -- + entran al cedis · − salen · 0 se movieron adentro
    ubicacion_origen  text,                          -- de dónde salieron, si es del cedis (como en almacen.locations)
    ubicacion_destino text,                          -- a dónde llegaron, si es del cedis
    documento         text,                          -- TEX2/OUT/00134 · «Adecuación para alta de producto»
    referencia        text,                          -- la orden de venta o de compra: S40478 · P03333
    contraparte       text,                          -- cliente, canal o proveedor del documento
    tipo_operacion    text,                          -- «TEXCO II: Órdenes de entrega»
    quien             text,                          -- usuario de Odoo que hizo la línea
    odoo_move_line_id bigint      not null,          -- de qué línea de Odoo salió
    odoo_product_id   integer     not null,
    odoo_origen       text        not null,          -- la ruta completa de Odoo, tal cual
    odoo_destino      text        not null,
    odoo_escrito_at   timestamptz not null,          -- write_date de la línea: la marca de agua del delta
    cargado_at        timestamptz not null default now(),
    actualizado_at    timestamptz not null default now(),
    constraint historial_movimientos_pkey      primary key (id),
    constraint historial_movimientos_linea_uq  unique (odoo_move_line_id, cedis),
    constraint historial_movimientos_cedis_chk check (cedis ~ '^[A-Z0-9]{2,12}$'),
    constraint historial_movimientos_sku_chk   check (length(btrim(sku::text)) > 0),
    constraint historial_movimientos_causa_chk check (length(btrim(causa)) > 0),
    constraint historial_movimientos_piezas_chk check (piezas >= 0),
    -- Un renglón es de un cedis porque al menos un lado del movimiento está en él,
    -- y el signo sale de cuál: sólo llega = entra · sólo sale = sale · los dos = adentro.
    constraint historial_movimientos_sentido_chk check (
           (ubicacion_origen is null     and ubicacion_destino is not null and delta = piezas)
        or (ubicacion_origen is not null and ubicacion_destino is null     and delta = -piezas)
        or (ubicacion_origen is not null and ubicacion_destino is not null and delta = 0))
);

create index if not exists historial_movimientos_sku_idx
    on almacen.historial_movimientos (cedis, sku, fecha desc);
create index if not exists historial_movimientos_fecha_idx
    on almacen.historial_movimientos (cedis, fecha desc);
create index if not exists historial_movimientos_escrito_idx
    on almacen.historial_movimientos (cedis, odoo_escrito_at desc);

-- La llave al catálogo de bodegas, donde viva hoy (`almacen` después de la 0068,
-- `ops` antes; después de la 0068 `ops.almacenes` es una VISTA puente).
do $fk$
declare
  catalogo regclass;
begin
  select c.oid::regclass into catalogo
    from pg_class c
   where c.relkind = 'r'
     and c.oid in (to_regclass('almacen.almacenes'), to_regclass('ops.almacenes'))
   order by (c.oid = to_regclass('almacen.almacenes')) desc
   limit 1;
  if catalogo is null then
    raise exception '0070: no existe el catálogo de bodegas (almacen.almacenes ni ops.almacenes). Corre antes la 0064.';
  end if;
  if not exists (select 1 from pg_constraint
                  where conname = 'historial_movimientos_cedis_fk'
                    and conrelid = 'almacen.historial_movimientos'::regclass) then
    execute format(
      'alter table almacen.historial_movimientos add constraint historial_movimientos_cedis_fk '
      'foreign key (cedis) references %s (codigo)', catalogo);
  end if;
end
$fk$;

drop trigger if exists historial_movimientos_tocar_trg on almacen.historial_movimientos;
create trigger historial_movimientos_tocar_trg
  before update on almacen.historial_movimientos
  for each row execute function almacen.tg_tocar_actualizado_at();

comment on table almacen.historial_movimientos is
  'Todo lo que se le movió a un SKU en un cedis, un renglón por línea de movimiento HECHA en Odoo. cedis obligatorio. delta = lo que le hizo al cedis (+ entra, − sale, 0 se movió adentro); Σ delta de un SKU = Σ piezas de su foto en almacen.locations. Sólo productos ACTIVOS de Odoo. No es el libro de las bodegas de kubera (ése es stock_mov). La mantiene services/almacen_odoo.py mientras el cedis sea de Odoo.';
comment on column almacen.historial_movimientos.delta is
  'Lo que el movimiento le hizo al cedis: + entran piezas, − salen, 0 cambiaron de lugar adentro.';
comment on column almacen.historial_movimientos.causa is
  'Las palabras del libro de bodega del panel (services.odoo._causa): entrada, venta, envio_full, traspaso, preparacion, ajuste, merma, devolucion, cuarentena, otro. Toda salida a un socio que es canal (FULL, AMAZON, temu, tiktokshop…) sale como envio_full: el canal está en contraparte.';
comment on column almacen.historial_movimientos.ubicacion_origen is
  'De dónde salieron las piezas, escrito como en almacen.locations. NULL si el origen está fuera del cedis (la ruta completa queda en odoo_origen).';
comment on column almacen.historial_movimientos.odoo_escrito_at is
  'write_date de la línea en Odoo. El vigilante pide a Odoo lo escrito desde la mayor de estas fechas, con traslape.';

alter table almacen.historial_movimientos enable row level security;
revoke all on almacen.historial_movimientos from public, anon, authenticated;
grant all on almacen.historial_movimientos to service_role;
revoke all on function almacen.tg_tocar_actualizado_at() from public, anon, authenticated;

insert into ops.migraciones (migracion, detalle)
select '0070_almacen_historial_movimientos',
       jsonb_build_object('historial_movimientos', (select count(*) from almacen.historial_movimientos),
                          'locations', (select count(*) from almacen.locations))
 where to_regclass('ops.migraciones') is not null
   and not exists (select 1 from ops.migraciones
                    where migracion = '0070_almacen_historial_movimientos');

commit;
