-- 0069_almacen_locations.sql
--
-- QUÉ HACE
--   Crea `almacen.locations`: DÓNDE está cada SKU dentro de un cedis, un renglón
--   por SKU y ubicación. Pedido de Brandon (9-oct-2026): TEXCO II deja de
--   operarse desde Odoo y pasa a kubera en ~2 semanas, y la ubicación de rack
--   sólo existe en Odoo (ni Woo ni kubera la tenían). Esta tabla es su casa.
--
--   Nace VACÍA. La llena `backend/scripts/cargar_locations_tex2.py` con la foto
--   de Odoo (sólo lectura allá), y se puede volver a correr mientras TEXCO II
--   siga siendo de Odoo.
--
-- OJO (0070, mismo día): los productos archivados ya NO se traen. La 0070 borró
-- sus renglones y quitó la columna `archivado_odoo` que esta migración crea, y el
-- cargador se reemplazó por `services/almacen_odoo.py` (vigilante de 30 min) y
-- `backend/scripts/copiar_almacen_odoo.py`. Para una base nueva: 0069 → 0070.
--
-- Estado: APLICADA en PRODUCCIÓN (tukwcvsi) el 9-oct-2026 07:05:58 UTC, desde el
-- chat de Inventario y por instrucción de Brandon («sube la migración, aplica a
-- producción»); sin acta. Antes, la migración entera dentro de una transacción
-- que terminó en ROLLBACK: 12 comprobaciones y 4 rechazos esperados; las mismas
-- 12 después del COMMIT. CARGADA a las 07:06 UTC: 1,663 renglones, 476,395
-- piezas. En el SANDBOX no está. Apunte para Eduardo:
-- docs/MIGRACION_0069_NOTA_EDUARDO.md.
--
-- LAS REGLAS (de Brandon, 9-oct)
--   · `cedis` es OBLIGATORIO: ningún renglón existe sin su cedis. Guarda el
--     código de la bodega (TEX2) y su nombre («TEXCO II») sale del catálogo de
--     bodegas, que ya existe — no se repite el nombre en cada renglón.
--   · El SKU que no tiene rack en Odoo entra con ubicacion = 'SIN UBICAR'. Sigue
--     perteneciendo a su cedis: eso lo dice `cedis`, no la ubicación.
--   · Se traen TODOS, también los negativos: son movimientos reales de Odoo que
--     dejaron la ubicación en menos (ver el encabezado del cargador).
--   · `piezas` se GUARDA pero es INFORMATIVA hasta nuevo aviso: nada vende, aparta
--     ni sincroniza con ella. El saldo oficial de una bodega de kubera es
--     `stock_almacen.fisico`, que tiene su libro (`stock_mov`).
--
-- LO QUE NO ES
--   No es el catálogo de racks: un rack vacío no tiene renglón. Odoo tiene
--   19,240 ubicaciones definidas en TEX2 y sólo 564 con mercancía.
--
-- ORDEN: no depende de la 0068 (la mudanza de `ops.almacenes` a `almacen`). La
-- llave a la bodega se amarra a la tabla donde viva en ese momento; una llave
-- foránea guarda el identificador de la tabla, así que sobrevive a la mudanza.
--
-- IDEMPOTENTE: se puede correr dos veces.
-- REVERSA: `drop table almacen.locations;` (no la lee nadie todavía).

begin;

set local lock_timeout = '5s';
set local statement_timeout = '60s';

-- El esquema ya existe en producción (0066). Se asegura aquí para que el sandbox
-- y las bases de prueba no dependan del orden.
create schema if not exists almacen;
grant usage on schema almacen to service_role;

create table if not exists almacen.locations (
    id              bigint      generated always as identity,
    cedis           text        not null,                       -- código de la bodega: TEX2 = «TEXCO II»
    sku             citext      not null,
    ubicacion       text        not null default 'SIN UBICAR',  -- 'BLOQUE D-FILA 1-T6' | 'REQUERIMENTOS' | 'SIN UBICAR'
    bloque          text,                                       -- 'D'   ┐ la ubicación partida, sólo cuando
    fila            text,                                       -- '1'   │ es un rack completo; sirve para
    tarima          text,                                       -- 'T6'  ┘ filtrar y ordenar el recorrido
    piezas          integer     not null default 0,             -- INFORMATIVA. Puede ser negativa (así viene de Odoo)
    origen          text        not null default 'kubera',      -- odoo = vino de la foto | kubera = capturado aquí
    archivado_odoo  boolean     not null default false,         -- el producto está ARCHIVADO en Odoo: no cuenta
    odoo_quant_id   bigint,                                     -- de qué renglón de Odoo salió (auditar la carga)
    odoo_product_id integer,
    odoo_ubicacion  text,                                       -- la ruta completa de Odoo, tal cual
    entrada_at      timestamptz,                                -- cuándo entró a esa ubicación, según Odoo
    cargado_at      timestamptz not null default now(),
    actualizado_at  timestamptz not null default now(),
    constraint locations_pkey          primary key (id),
    constraint locations_cedis_chk     check (cedis ~ '^[A-Z0-9]{2,12}$'),
    constraint locations_sku_chk       check (length(btrim(sku::text)) > 0),
    constraint locations_ubicacion_chk check (length(btrim(ubicacion)) > 0 and ubicacion = btrim(ubicacion)),
    constraint locations_origen_chk    check (origen in ('odoo', 'kubera')),
    -- Un rack es bloque + fila + tarima, o no es rack: nunca a medias.
    constraint locations_rack_chk      check (
        (bloque is null and fila is null and tarima is null)
        or (bloque is not null and fila is not null and tarima is not null)),
    -- Sólo lo que vino de Odoo puede decir de qué renglón de Odoo salió.
    constraint locations_odoo_chk      check (
        origen = 'odoo' or (odoo_quant_id is null and odoo_product_id is null
                            and odoo_ubicacion is null and not archivado_odoo))
);

-- Un SKU aparece UNA vez por ubicación de su cedis. Los renglones de productos
-- archivados en Odoo quedan fuera de la regla: 21 SKUs de TEXCO II viven en dos
-- productos de Odoo (el archivado conserva el rack; el activo, la cuenta) y en 5
-- de ellos los dos están en la misma ubicación.
create unique index if not exists locations_sku_ubicacion_uq
    on almacen.locations (cedis, sku, ubicacion) where not archivado_odoo;
create unique index if not exists locations_odoo_quant_uq
    on almacen.locations (odoo_quant_id) where odoo_quant_id is not null;
create index if not exists locations_sku_idx  on almacen.locations (sku);
create index if not exists locations_rack_idx on almacen.locations (cedis, bloque, fila, tarima);

-- La llave al catálogo de bodegas, donde viva hoy (`almacen` después de la 0068,
-- `ops` antes). Después de la 0068 `ops.almacenes` es una VISTA puente: por eso
-- se pregunta primero por `almacen` y se exige que sea tabla.
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
    raise exception '0069: no existe el catálogo de bodegas (almacen.almacenes ni ops.almacenes). Corre antes la 0064.';
  end if;
  if not exists (select 1 from pg_constraint
                  where conname = 'locations_cedis_fk'
                    and conrelid = 'almacen.locations'::regclass) then
    execute format(
      'alter table almacen.locations add constraint locations_cedis_fk '
      'foreign key (cedis) references %s (codigo)', catalogo);
  end if;
end
$fk$;

-- `actualizado_at` lo mantiene la base: quien edite un renglón no tiene que acordarse.
create or replace function almacen.locations_tocar() returns trigger
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
  for each row execute function almacen.locations_tocar();

comment on table almacen.locations is
  'Dónde está cada SKU dentro de un cedis: un renglón por SKU y ubicación. cedis es obligatorio (código de la bodega; su nombre sale del catálogo de bodegas). Sin rack = ubicacion ''SIN UBICAR''. piezas es INFORMATIVA: el saldo oficial es stock_almacen.fisico. Nace de la foto de Odoo de TEXCO II (cargar_locations_tex2.py).';
comment on column almacen.locations.cedis is
  'Código de la bodega (TEX2). OBLIGATORIO: ningún renglón existe sin cedis. El nombre («TEXCO II») sale del catálogo de bodegas.';
comment on column almacen.locations.ubicacion is
  'Como la escribe el panel: BLOQUE D-FILA 1-T6. SIN UBICAR = en el cedis, sin rack asignado.';
comment on column almacen.locations.piezas is
  'INFORMATIVA hasta nuevo aviso: nada vende, aparta ni sincroniza con ella. El saldo oficial es stock_almacen.fisico. Puede ser negativa: así la dejó un movimiento de Odoo.';
comment on column almacen.locations.archivado_odoo is
  'El producto está ARCHIVADO en Odoo. Se trae porque conserva el rack, pero sus piezas no cuentan: el producto activo del mismo SKU lleva la cuenta.';

alter table almacen.locations enable row level security;
revoke all on almacen.locations from public, anon, authenticated;
grant all on almacen.locations to service_role;
revoke all on function almacen.locations_tocar() from public, anon, authenticated;

insert into ops.migraciones (migracion, detalle)
select '0069_almacen_locations',
       jsonb_build_object('locations', (select count(*) from almacen.locations))
 where to_regclass('ops.migraciones') is not null
   and not exists (select 1 from ops.migraciones where migracion = '0069_almacen_locations');

commit;
