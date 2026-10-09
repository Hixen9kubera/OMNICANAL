-- ═══════════════════════════════════════════════════════════════════════════
-- 0033_ops_odoo_sale_orders_forma_prod — En una base NUEVA, retira la forma
-- vieja de las tres tablas de la 0033 para que la 0035 y la 0036 las creen con
-- la forma de PRODUCCIÓN. En producción y en el sandbox NO HACE NADA.
--
-- Fase 0 del reorden de esquemas (8-oct-2026), al regularizar en main las
-- 0034-0036 (rama feat/caja-compartida, a66bd54).
--
-- POR QUÉ HACE FALTA
-- ------------------
-- `0033_ops_odoo_sale_orders.sql` (main) crea `ops.odoo_sale_orders`,
-- `ops.odoo_sale_order_items` y `ops.automatizacion_flags` con una forma que
-- NUNCA llegó a producción: el 26-ago se aplicaron en su lugar la 0035 (el
-- interruptor, con el CHECK del nombre de flag y `creado_at`) y la 0036 (la
-- bitácora con `cuenta` en la llave, `sku` citext, `stock_libre` jsonb, FK de
-- las líneas a su encabezado y `medido_at`). Las dos usan `create table if not
-- exists`, así que en una base nueva, donde la 0033 corre antes:
--   · la 0035 no crea nada: el interruptor se queda sin CHECK y sin creado_at;
--   · la 0036 no crea nada y TRUENA en `comment on column
--     ops.odoo_sale_order_items.stock_libre` (42703): la columna no existe en la
--     forma de la 0033. El runner aborta ahí y la base queda a medias.
-- No se edita la 0033 ni la 0036 (las dos son historia: la 0036 está aplicada
-- en producción desde el 26-ago). Este archivo se ordena JUSTO DESPUÉS de la
-- 0033 (`sorted()`: «0033_ops_odoo_sale_orders.sql» < «0033_ops_odoo_sale_
-- orders_forma_prod.sql» < «0034_…»), como las otras numeraciones repetidas
-- (0004, 0018, 0023, 0025, 0043, 0044).
--
-- QUÉ HACE, TABLA POR TABLA (solo si encuentra la forma de la 0033)
-- -----------------------------------------------------------------
--   · ops.odoo_sale_order_items sin `cuenta` y ops.odoo_sale_orders sin
--     `cuenta`: si están VACÍAS, las tira (primero las líneas). La 0036 las
--     vuelve a crear con la forma de producción, con sus índices, su trigger y
--     su RLS. Si tienen filas, TRUENA: convertirlas exige inventar `cuenta` y
--     cambiar la llave, y eso es un acta, no una migración de forma.
--   · ops.automatizacion_flags sin `creado_at`: si está vacía, la tira y la 0035
--     la crea igual que en producción (mismo orden de columnas). Si tiene filas
--     (alguien movió un interruptor), NO las tira: le agrega `creado_at` y el
--     CHECK del nombre de flag en su lugar. Las columnas quedan en otro orden
--     que en producción (creado_at al final) y la paridad del runner lo va a
--     decir: es el precio de no perder un interruptor.
-- Con la forma de producción (producción, sandbox) no toca nada: solo lee el
-- catálogo, sin candados sobre las tablas.
--
-- Verificado (8-oct-2026) dentro de transacciones que terminan en ROLLBACK en el
-- sandbox: sobre sus tablas reales no cambia nada; y en un esquema de prueba,
-- 0033 → este archivo → 0035 → 0036 deja las tres tablas con las columnas, los
-- constraints, los índices y el trigger medidos en producción el 8-oct.
--
-- REVERSA: no hay nada que revertir en producción ni en el sandbox. En una base
-- nueva, la forma de la 0033 no se quiere de vuelta.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

set local lock_timeout = '3s';

do $$
declare
  hay boolean;
begin
  -- 1) Las líneas de la bitácora (van primero: en la forma nueva apuntan al encabezado).
  if to_regclass('ops.odoo_sale_order_items') is not null
     and not exists (select 1 from pg_catalog.pg_attribute
                      where attrelid = to_regclass('ops.odoo_sale_order_items')
                        and attname = 'cuenta' and attnum > 0 and not attisdropped) then
    execute 'select exists (select 1 from ops.odoo_sale_order_items)' into hay;
    if hay then
      raise exception using errcode = 'P0001',
        message = 'ops.odoo_sale_order_items tiene filas con la forma de la 0033 (sin cuenta)',
        hint = 'Convertirla a la forma de la 0036 cambia la llave: es un acta, no esta migración.';
    end if;
    drop table ops.odoo_sale_order_items;
    raise notice 'ops.odoo_sale_order_items: forma de la 0033, vacía: retirada (la 0036 la crea).';
  end if;

  -- 2) El encabezado de la bitácora.
  if to_regclass('ops.odoo_sale_orders') is not null
     and not exists (select 1 from pg_catalog.pg_attribute
                      where attrelid = to_regclass('ops.odoo_sale_orders')
                        and attname = 'cuenta' and attnum > 0 and not attisdropped) then
    execute 'select exists (select 1 from ops.odoo_sale_orders)' into hay;
    if hay then
      raise exception using errcode = 'P0001',
        message = 'ops.odoo_sale_orders tiene filas con la forma de la 0033 (sin cuenta)',
        hint = 'Convertirla a la forma de la 0036 cambia la llave: es un acta, no esta migración.';
    end if;
    drop table ops.odoo_sale_orders;
    raise notice 'ops.odoo_sale_orders: forma de la 0033, vacía: retirada (la 0036 la crea).';
  end if;

  -- 3) El interruptor.
  if to_regclass('ops.automatizacion_flags') is not null
     and not exists (select 1 from pg_catalog.pg_attribute
                      where attrelid = to_regclass('ops.automatizacion_flags')
                        and attname = 'creado_at' and attnum > 0 and not attisdropped) then
    execute 'select exists (select 1 from ops.automatizacion_flags)' into hay;
    if hay then
      alter table ops.automatizacion_flags
        add column creado_at timestamptz not null default now();
      if not exists (select 1 from pg_catalog.pg_constraint
                      where conrelid = to_regclass('ops.automatizacion_flags')
                        and conname = 'automatizacion_flags_flag_check') then
        alter table ops.automatizacion_flags
          add constraint automatizacion_flags_flag_check check (flag ~ '^[a-z0-9_]+$');
      end if;
      raise notice 'ops.automatizacion_flags: forma de la 0033 CON filas: se le agregan creado_at y el CHECK (columnas en otro orden que producción).';
    else
      drop table ops.automatizacion_flags;
      raise notice 'ops.automatizacion_flags: forma de la 0033, vacía: retirada (la 0035 la crea).';
    end if;
  end if;
end $$;

commit;
