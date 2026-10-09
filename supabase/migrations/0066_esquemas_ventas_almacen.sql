-- ═══════════════════════════════════════════════════════════════════════════
-- 0066 — Los esquemas `ventas` y `almacen`, VACÍOS.
--        Fase 0 del reorden de esquemas (REORDEN_ESQUEMAS_VENTAS_ALMACEN §4.2-4),
--        aprobado por Eduardo el 8-oct-2026.
-- ═══════════════════════════════════════════════════════════════════════════
--
-- QUÉ HACE
--   · `create schema if not exists ventas` (órdenes de venta propias y las
--     bitácoras de Odoo y Temu) y `almacen` (bodegas, saldo, movimientos y
--     devoluciones del libro), con su comment.
--   · Los mismos permisos que `ops`, medidos en producción el 8-oct:
--       nspacl  {postgres=UC/postgres,service_role=U/postgres}
--       default r → service_role=arwdDxtm   ·   S → service_role=rwU
--     o sea `grant usage` a service_role y `alter default privileges for role
--     postgres in schema …` (tablas y secuencias), como 0001:732-737. anon y
--     authenticated no reciben NADA: deny-by-default. No se exponen en PostgREST
--     (Settings › API › Exposed schemas sigue en public, graphql_public).
--   · Su fila en ops.migraciones, como la 0064 y la 0065.
-- QUÉ NO HACE
--   No mueve ninguna tabla, no crea vistas ni funciones y no cambia el
--   search_path. Las tablas llegan en las fases 2 y 3, cada una con su acta.
--
-- Idempotente: re-correrla no cambia nada (deja otra fila en ops.migraciones,
-- que es de solo agregar: «una re-ejecución deja otra fila»).
--
-- QUIÉN LA CORRE: el SANDBOX con `backend/scripts/aplicar_migraciones.py` (que
-- quita el begin/commit de este archivo y lo envuelve en SU transacción con el
-- registro); PRODUCCIÓN con su acta. Rol: postgres (el `alter default
-- privileges for role postgres` exige serlo o ser miembro).
--
-- REVERSA (solo mientras estén vacíos; `drop schema` sin cascade truena si no):
--   begin;
--   alter default privileges for role postgres in schema ventas, almacen
--     revoke all on tables from service_role;
--   alter default privileges for role postgres in schema ventas, almacen
--     revoke all on sequences from service_role;
--   drop schema if exists ventas;  drop schema if exists almacen;
--   commit;
-- ═══════════════════════════════════════════════════════════════════════════

begin;

set local lock_timeout = '5s';

create schema if not exists ventas;
create schema if not exists almacen;

comment on schema ventas is
  'Ventas propias de kubera: órdenes de venta (OV-00001…) y las bitácoras de Odoo y Temu. Nace vacío (0066); '
  'las tablas llegan desde ops con su acta. No incluye channel.orders ni channel.returns* (D8).';
comment on schema almacen is
  'Bodegas propias de kubera y su libro: catálogo de bodegas, saldo, movimientos y devoluciones del libro. Nace '
  'vacío (0066); las tablas llegan desde ops con su acta. No incluye stock_watch_photo, fanout_log ni FBA (D8).';

-- Igual que ops: service_role entra al esquema; anon y authenticated, no.
grant usage on schema ventas, almacen to service_role;

-- Lo que nazca aquí (creado por postgres) le da todo a service_role, como en
-- ops. Ojo: un objeto MOVIDO con `alter table … set schema` conserva su propia
-- ACL; esto solo cubre lo que se cree en el esquema.
alter default privileges for role postgres in schema ventas, almacen
  grant all on tables to service_role;
alter default privileges for role postgres in schema ventas, almacen
  grant all on sequences to service_role;

-- Registro de esta aplicación.
insert into ops.migraciones (migracion, detalle)
values ('0066_esquemas_ventas_almacen',
        jsonb_build_object(
          'esquemas', (select jsonb_agg(n.nspname order by n.nspname)
                         from pg_catalog.pg_namespace n
                        where n.nspname in ('ventas', 'almacen')),
          'tablas',   (select count(*)
                         from pg_catalog.pg_class c
                         join pg_catalog.pg_namespace n on n.oid = c.relnamespace
                        where n.nspname in ('ventas', 'almacen'))));

commit;
