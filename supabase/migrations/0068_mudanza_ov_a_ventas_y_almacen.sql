-- 0068_mudanza_ov_a_ventas_y_almacen.sql
--
-- QUÉ HACE
--   Muda las nueve tablas de las órdenes de venta propias, de `ops` a los
--   esquemas que la 0066 creó vacíos para eso:
--
--     ventas  ← ov_folio, ov_ordenes, ov_lineas, ov_mensajes, ov_archivos
--     almacen ← almacenes, almacenes_hist, stock_almacen, stock_mov
--
--   Pedido de Brandon (9-oct-2026), repartidas como dice el comentario de cada
--   esquema en la 0066. NO borra nada y NO toca datos: `alter table … set
--   schema` cambia el nombre calificado; filas, índices, llaves, triggers,
--   secuencias de identidad, RLS y privilegios viajan con la tabla.
--
-- LO QUE SE QUEDA EN `ops`
--   · Las funciones (triggers y `ops.verificar_*`, `ops.exigir`): siguen en
--     `ops`; aquí sólo se vuelven a crear las 14 que nombraban las tablas
--     por su esquema, con el nombre nuevo. Copiadas tal cual de producción.
--   · `ops.devoluciones`, `ops.stock_formato*`, `ops.migraciones`,
--     `ops.automatizacion_flags` y la vista `ops.stock_apartado_descuadre_v`
--     (una vista guarda el identificador de la tabla, no su nombre: sigue
--     leyendo las tablas donde ahora viven).
--
-- EL PUENTE (lo que hay que QUITAR después)
--   En `ops` queda una VISTA con el nombre viejo de cada tabla
--   (`select * from <esquema nuevo>.<tabla>`, security_invoker). Son vistas
--   simples: se leen y se escriben como la tabla. Existen para que el código
--   que ya está desplegado y todavía dice `ops.<tabla>` no se caiga en el
--   minuto entre esta migración y el despliegue —y para el tablero de bodegas
--   (`services/fanout_bodegas.py`), que es de otro chat y no se tocó—.
--   Cuando ya nadie diga `ops.<tabla>`: `drop view ops.<tabla>` de las nueve.
--   OJO: una columna que se agregue a la tabla NO aparece sola en su vista.
--
-- IDEMPOTENTE: se puede correr dos veces. Sólo mueve lo que todavía es tabla
-- en `ops`.
--
-- REVERSA (si hiciera falta): `drop view ops.<tabla>` de las nueve,
-- `alter table <esquema>.<tabla> set schema ops`, y volver a correr las
-- funciones de la 0064/0065.

begin;

set local lock_timeout = '5s';
set local statement_timeout = '60s';

-- Los esquemas ya existen en producción (0066). Se aseguran aquí para que el
-- sandbox y las bases de prueba no dependan del orden.
create schema if not exists ventas;
create schema if not exists almacen;
grant usage on schema ventas, almacen to service_role;

do $mudanza$
declare
  movidas integer := 0;
begin
  if exists (select 1 from pg_class where oid = to_regclass('ops.almacenes') and relkind = 'r') then
    alter table ops.almacenes set schema almacen;
    movidas := movidas + 1;
  end if;
  if exists (select 1 from pg_class where oid = to_regclass('ops.almacenes_hist') and relkind = 'r') then
    alter table ops.almacenes_hist set schema almacen;
    movidas := movidas + 1;
  end if;
  if exists (select 1 from pg_class where oid = to_regclass('ops.stock_almacen') and relkind = 'r') then
    alter table ops.stock_almacen set schema almacen;
    movidas := movidas + 1;
  end if;
  if exists (select 1 from pg_class where oid = to_regclass('ops.stock_mov') and relkind = 'r') then
    alter table ops.stock_mov set schema almacen;
    movidas := movidas + 1;
  end if;
  if exists (select 1 from pg_class where oid = to_regclass('ops.ov_folio') and relkind = 'r') then
    alter table ops.ov_folio set schema ventas;
    movidas := movidas + 1;
  end if;
  if exists (select 1 from pg_class where oid = to_regclass('ops.ov_ordenes') and relkind = 'r') then
    alter table ops.ov_ordenes set schema ventas;
    movidas := movidas + 1;
  end if;
  if exists (select 1 from pg_class where oid = to_regclass('ops.ov_lineas') and relkind = 'r') then
    alter table ops.ov_lineas set schema ventas;
    movidas := movidas + 1;
  end if;
  if exists (select 1 from pg_class where oid = to_regclass('ops.ov_mensajes') and relkind = 'r') then
    alter table ops.ov_mensajes set schema ventas;
    movidas := movidas + 1;
  end if;
  if exists (select 1 from pg_class where oid = to_regclass('ops.ov_archivos') and relkind = 'r') then
    alter table ops.ov_archivos set schema ventas;
    movidas := movidas + 1;
  end if;
  insert into ops.migraciones (migracion, detalle)
  select '0068_mudanza_ov_a_ventas_y_almacen', jsonb_build_object('tablas_movidas', movidas)
  where to_regclass('ops.migraciones') is not null;
end
$mudanza$;

-- ── El puente: vistas con el nombre viejo ──────────────────────────────────
create or replace view ops.almacenes with (security_invoker = true) as select * from almacen.almacenes;
comment on view ops.almacenes is 'PUENTE TEMPORAL (0068): la tabla vive en almacen.almacenes. Existe para que el código que aún dice ops.almacenes no se caiga; se quita cuando ya nadie lo use.';
grant select, insert, update, delete on ops.almacenes to service_role;
create or replace view ops.almacenes_hist with (security_invoker = true) as select * from almacen.almacenes_hist;
comment on view ops.almacenes_hist is 'PUENTE TEMPORAL (0068): la tabla vive en almacen.almacenes_hist. Existe para que el código que aún dice ops.almacenes_hist no se caiga; se quita cuando ya nadie lo use.';
grant select, insert, update, delete on ops.almacenes_hist to service_role;
create or replace view ops.stock_almacen with (security_invoker = true) as select * from almacen.stock_almacen;
comment on view ops.stock_almacen is 'PUENTE TEMPORAL (0068): la tabla vive en almacen.stock_almacen. Existe para que el código que aún dice ops.stock_almacen no se caiga; se quita cuando ya nadie lo use.';
grant select, insert, update, delete on ops.stock_almacen to service_role;
create or replace view ops.stock_mov with (security_invoker = true) as select * from almacen.stock_mov;
comment on view ops.stock_mov is 'PUENTE TEMPORAL (0068): la tabla vive en almacen.stock_mov. Existe para que el código que aún dice ops.stock_mov no se caiga; se quita cuando ya nadie lo use.';
grant select, insert, update, delete on ops.stock_mov to service_role;
create or replace view ops.ov_folio with (security_invoker = true) as select * from ventas.ov_folio;
comment on view ops.ov_folio is 'PUENTE TEMPORAL (0068): la tabla vive en ventas.ov_folio. Existe para que el código que aún dice ops.ov_folio no se caiga; se quita cuando ya nadie lo use.';
grant select, insert, update, delete on ops.ov_folio to service_role;
create or replace view ops.ov_ordenes with (security_invoker = true) as select * from ventas.ov_ordenes;
comment on view ops.ov_ordenes is 'PUENTE TEMPORAL (0068): la tabla vive en ventas.ov_ordenes. Existe para que el código que aún dice ops.ov_ordenes no se caiga; se quita cuando ya nadie lo use.';
grant select, insert, update, delete on ops.ov_ordenes to service_role;
create or replace view ops.ov_lineas with (security_invoker = true) as select * from ventas.ov_lineas;
comment on view ops.ov_lineas is 'PUENTE TEMPORAL (0068): la tabla vive en ventas.ov_lineas. Existe para que el código que aún dice ops.ov_lineas no se caiga; se quita cuando ya nadie lo use.';
grant select, insert, update, delete on ops.ov_lineas to service_role;
create or replace view ops.ov_mensajes with (security_invoker = true) as select * from ventas.ov_mensajes;
comment on view ops.ov_mensajes is 'PUENTE TEMPORAL (0068): la tabla vive en ventas.ov_mensajes. Existe para que el código que aún dice ops.ov_mensajes no se caiga; se quita cuando ya nadie lo use.';
grant select, insert, update, delete on ops.ov_mensajes to service_role;
create or replace view ops.ov_archivos with (security_invoker = true) as select * from ventas.ov_archivos;
comment on view ops.ov_archivos is 'PUENTE TEMPORAL (0068): la tabla vive en ventas.ov_archivos. Existe para que el código que aún dice ops.ov_archivos no se caiga; se quita cuando ya nadie lo use.';
grant select, insert, update, delete on ops.ov_archivos to service_role;

-- ── Las funciones que nombraban las tablas por su esquema ──────────────────
-- ops.tg_almacenes_guarda()  (6 referencias cambiadas)
CREATE OR REPLACE FUNCTION ops.tg_almacenes_guarda()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
declare
  v_quien text := nullif(current_setting('app.usuario', true), '');
begin
  if tg_op = 'INSERT' then
    -- Dar de alta una bodega es el mismo interruptor de negocio que cambiar
    -- sus banderas (SEG-03): con motivo y quién. La semilla trae los dos.
    if length(btrim(coalesce(new.motivo, ''))) < 10 then
      raise exception using errcode = '23514', constraint = 'almacenes_motivo_chk',
        message = format('almacen.almacenes: el alta de %s exige un motivo de 10 caracteres o más', new.codigo);
    end if;
    if coalesce(v_quien, new.actualizado_por) is null then
      raise exception using errcode = '23514', constraint = 'almacenes_quien_chk',
        message = format('almacen.almacenes: el alta de %s exige quién: set_config(''app.usuario'', …, true) o actualizado_por', new.codigo);
    end if;
    new.actualizado_por := coalesce(v_quien, new.actualizado_por);
    new.actualizado_at  := now();
    return new;
  end if;
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', constraint = 'almacenes_sin_borrado',
      message = format('almacen.almacenes: la bodega %s no se borra; se apaga con sus banderas (D13)', old.codigo);
  end if;
  if new.codigo is distinct from old.codigo or new.fuente is distinct from old.fuente then
    raise exception using errcode = '42501', constraint = 'almacenes_codigo_fuente_fijos',
      message = format('almacen.almacenes: codigo y fuente de %s son fijos', old.codigo);
  end if;
  if (new.surte_ventas, new.cuenta_para_woo, new.admite_ov, new.preferencia,
      new.odoo_warehouse_id, new.temu_warehouse_id)
     is distinct from
     (old.surte_ventas, old.cuenta_para_woo, old.admite_ov, old.preferencia,
      old.odoo_warehouse_id, old.temu_warehouse_id) then
    if new.motivo is not distinct from old.motivo or length(btrim(coalesce(new.motivo, ''))) < 10 then
      raise exception using errcode = '23514', constraint = 'almacenes_motivo_chk',
        message = format('almacen.almacenes: cambiar una bandera de %s exige un motivo NUEVO de 10 caracteres o más', old.codigo);
    end if;
    if v_quien is null and new.actualizado_por is not distinct from old.actualizado_por then
      raise exception using errcode = '23514', constraint = 'almacenes_quien_chk',
        message = format('almacen.almacenes: cambiar una bandera de %s exige quién: set_config(''app.usuario'', …, true) o actualizado_por', old.codigo);
    end if;
  end if;
  new.actualizado_por := coalesce(v_quien, new.actualizado_por);
  new.actualizado_at  := now();
  return new;
end $function$
;

-- ops.tg_almacenes_hist()  (1 referencia cambiada)
CREATE OR REPLACE FUNCTION ops.tg_almacenes_hist()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
begin
  insert into almacen.almacenes_hist (codigo, antes, despues, motivo, quien)
  values (new.codigo,
          case when tg_op = 'UPDATE' then to_jsonb(old) end,
          to_jsonb(new), new.motivo,
          coalesce(nullif(current_setting('app.usuario', true), ''), new.actualizado_por));
  return null;
end $function$
;

-- ops.tg_apartado_cuadra()  (1 referencia cambiada)
CREATE OR REPLACE FUNCTION ops.tg_apartado_cuadra()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public', 'extensions'
AS $function$
declare
  r record;
begin
  if tg_table_name = 'ov_ordenes' then
    for r in select distinct l.sku, l.almacen from ventas.ov_lineas l
              where l.orden_id = new.id and l.reservado > 0 loop
      perform ops.verificar_apartado(r.sku, r.almacen);
    end loop;
  elsif tg_table_name = 'stock_almacen' then
    if tg_op = 'DELETE' then
      perform ops.verificar_apartado(old.sku, old.almacen);
    else
      perform ops.verificar_apartado(new.sku, new.almacen);
    end if;
  else                                   -- ov_lineas: un renglón de borrador tiene 0 y no se revisa
    if tg_op in ('UPDATE', 'DELETE') and old.reservado > 0 then
      perform ops.verificar_apartado(old.sku, old.almacen);
    end if;
    if tg_op in ('INSERT', 'UPDATE') and new.reservado > 0 then
      perform ops.verificar_apartado(new.sku, new.almacen);
    end if;
  end if;
  return null;
end $function$
;

-- ops.tg_devolucion_de_mas()  (2 referencias cambiadas)
CREATE OR REPLACE FUNCTION ops.tg_devolucion_de_mas()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public', 'extensions'
AS $function$
declare
  v_dev integer;
  v_ent integer;
begin
  select coalesce(sum(m.delta), 0) into v_dev
    from almacen.stock_mov m
   where m.motivo = 'devolucion' and m.ov_linea_id = new.ov_linea_id;
  select coalesce(l.entregado, 0) into v_ent from ventas.ov_lineas l where l.id = new.ov_linea_id;
  if v_dev > coalesce(v_ent, 0) then
    raise exception using errcode = '23514', constraint = 'stock_devolucion_de_mas',
      message = format('renglón %s: devuelto %s > entregado %s', new.ov_linea_id, v_dev, coalesce(v_ent, 0));
  end if;
  return null;
end $function$
;

-- ops.tg_devoluciones_guarda()  (2 referencias cambiadas)
CREATE OR REPLACE FUNCTION ops.tg_devoluciones_guarda()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public', 'extensions'
AS $function$
declare
  v record;
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', constraint = 'devoluciones_sin_borrado',
      message = format('devolución %s: no se borra; se resuelve (resuelta_at) con su movimiento', old.id);
  end if;
  if tg_op = 'UPDATE' then
    -- `folio` es generada: en un BEFORE todavía no tiene valor, se deja fuera.
    if old.resuelta_at is not null
       and (to_jsonb(new) - 'folio') is distinct from (to_jsonb(old) - 'folio') then
      raise exception using errcode = '42501', constraint = 'devoluciones_inmutable',
        message = format('devolución %s ya salió de REVISION: no cambia', old.id);
    end if;
    if (new.id, new.origen, new.ov_linea_id, new.sku, new.recibido_at, new.recibido_por, new.clave, new.partida_de)
       is distinct from
       (old.id, old.origen, old.ov_linea_id, old.sku, old.recibido_at, old.recibido_por, old.clave, old.partida_de) then
      raise exception using errcode = '42501', constraint = 'devoluciones_inmutable',
        message = format('devolución %s: origen, renglón, SKU, recepción, clave y partida_de son fijos (el libro ya los movió)', old.id);
    end if;
  end if;
  -- paquete_ids normalizado: ordenado y sin repetidos, para que la llave de
  -- recepción no dependa del orden en que se leyeron las etiquetas. Lo vacío
  -- queda vacío y lo frena devoluciones_paquete_ids_chk.
  new.paquete_ids := coalesce((select array_agg(distinct x order by x) from unnest(new.paquete_ids) as u(x)), '{}');
  -- Una fila partida es del MISMO paquete, renglón (o retiro) y SKU que su original.
  if tg_op = 'INSERT' and new.partida_de is not null
     and not exists (select 1 from ops.devoluciones p
                      where p.id = new.partida_de and p.partida_de is null
                        and p.origen = new.origen and p.sku = new.sku
                        and p.ov_linea_id is not distinct from new.ov_linea_id
                        and p.canal is not distinct from new.canal and p.cuenta is not distinct from new.cuenta
                        and p.paquete_ids = new.paquete_ids) then
    raise exception using errcode = '23514', constraint = 'devoluciones_partida_igual_chk',
      message = format('devolución partida de %s: tiene que ser del mismo paquete, renglón (o retiro), SKU y canal que la original',
                       new.partida_de);
  end if;
  if new.ov_linea_id is not null
     and (new.canal is not null or new.cuenta is not null or new.external_order_id is not null) then
    select o.mp_canal, o.mp_cuenta, o.mp_orden into v
      from ventas.ov_lineas l
      join ventas.ov_ordenes o on o.id = l.orden_id
     where l.id = new.ov_linea_id;
    if v.mp_orden is null
       or (new.canal, new.cuenta, new.external_order_id) is distinct from (v.mp_canal, v.mp_cuenta, v.mp_orden) then
      raise exception using errcode = '23514', constraint = 'devoluciones_venta_ov_chk',
        message = format('devolución del renglón %s: canal/cuenta/venta (%s/%s/%s) no son el mp_* de su OV (%s/%s/%s)',
                         new.ov_linea_id, new.canal, new.cuenta, new.external_order_id,
                         v.mp_canal, v.mp_cuenta, v.mp_orden);
    end if;
  end if;
  return new;
end $function$
;

-- ops.tg_libro_cuadra()  (1 referencia cambiada)
CREATE OR REPLACE FUNCTION ops.tg_libro_cuadra()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public', 'extensions'
AS $function$
declare
  v_prev integer;
begin
  if tg_table_name = 'stock_mov' then
    -- La cadena, movimiento por movimiento: saldo_despues = anterior + delta.
    select m.saldo_despues into v_prev from almacen.stock_mov m
     where m.sku = new.sku and m.almacen = new.almacen and m.id < new.id
     order by m.id desc limit 1;
    if new.saldo_despues <> coalesce(v_prev, 0) + new.delta then
      raise exception using errcode = '23514', constraint = 'stock_libro_cadena',
        message = format('libro %s/%s, movimiento %s: saldo_despues %s <> anterior %s + delta %s',
                         new.sku, new.almacen, new.id, new.saldo_despues, coalesce(v_prev, 0), new.delta);
    end if;
    perform ops.verificar_libro(new.sku, new.almacen);
  elsif tg_op = 'DELETE' then
    perform ops.verificar_libro(old.sku, old.almacen);
  else
    perform ops.verificar_libro(new.sku, new.almacen);
  end if;
  return null;
end $function$
;

-- ops.tg_ov_coherente()  (0 referencias cambiadas)
CREATE OR REPLACE FUNCTION ops.tg_ov_coherente()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
begin
  if tg_table_name = 'ov_ordenes' then
    perform ops.verificar_ov(new.id);
  else
    perform ops.verificar_ov(coalesce(new.orden_id, old.orden_id));
  end if;
  return null;
end $function$
;

-- ops.tg_ov_folio_guarda()  (1 referencia cambiada)
CREATE OR REPLACE FUNCTION ops.tg_ov_folio_guarda()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
begin
  if tg_op = 'DELETE' or new.id <> old.id or new.ultimo < old.ultimo then
    raise exception using errcode = '42501', constraint = 'ov_folio_solo_sube',
      message = 'ventas.ov_folio: el contador no se borra ni baja';
  end if;
  return new;
end $function$
;

-- ops.tg_ov_lineas_guarda()  (2 referencias cambiadas)
CREATE OR REPLACE FUNCTION ops.tg_ov_lineas_guarda()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
declare
  permitidas constant text[] := array['reservado', 'entregado', 'entregado_at', 'entregado_por'];
  v_estado  text;
  v_creado  timestamptz;
  v_borrada timestamptz;
  v_conf    timestamptz;
begin
  if tg_op = 'INSERT' then
    -- Agregar renglones: en borrador, o en la MISMA transacción que creó la OV
    -- (crear_auto inserta la OV confirmada y sus renglones apartados juntos).
    -- creado_at = now() es «nació en esta transacción»: now() es la hora de inicio de la transacción.
    select o.estado, o.creado_at, o.borrada_at into v_estado, v_creado, v_borrada
      from ventas.ov_ordenes o where o.id = new.orden_id
       for no key update;
    if v_borrada is not null then
      raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
        message = format('OV %s está borrada: no se le agregan renglones', new.orden_id);
    end if;
    if v_estado is not null and v_estado <> 'borrador' and v_creado is distinct from now() then
      raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
        message = format('OV %s está %s: no se le agregan renglones', new.orden_id, v_estado);
    end if;
    return new;
  end if;

  if tg_op = 'UPDATE' and (new.id, new.orden_id) is distinct from (old.id, old.orden_id) then
    raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
      message = format('renglón %s: id y orden son fijos', old.id);
  end if;

  if old.entregado_at is not null then
    raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
      message = format('renglón %s ya salió: no se edita ni se borra', old.id);
  end if;

  if old.reservado > 0 then
    if tg_op = 'DELETE' then
      raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
        message = format('renglón %s aparta piezas: no se borra', old.id);
    end if;
    if (to_jsonb(new) - permitidas) is distinct from (to_jsonb(old) - permitidas) then
      raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
        message = format('renglón %s aparta piezas: solo cambian %s', old.id, array_to_string(permitidas, ', '));
    end if;
    return new;
  end if;

  -- Sin apartado ni entrega: depende de la OV (con su fila bloqueada).
  select o.estado, o.borrada_at, o.confirmada_at into v_estado, v_borrada, v_conf
    from ventas.ov_ordenes o where o.id = old.orden_id
     for no key update;
  if v_borrada is not null then
    raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
      message = format('renglón %s de una OV borrada: no se edita ni se borra', old.id);
  end if;
  if coalesce(v_estado, 'borrador') = 'borrador' then
    return coalesce(new, old);
  end if;
  -- Confirmar: el trigger puede ver la OV ya confirmada (otra parte del mismo
  -- WITH). Solo se admite poner bodega y apartar el renglón completo, y solo
  -- en la transacción que la confirmó (confirmada_at = now(), el mismo
  -- criterio que creado_at): días después, soltar y volver a apartar en OTRA
  -- bodega cambiaría un renglón confirmado sin rastro.
  if tg_op = 'UPDATE' and v_estado = 'confirmada' and v_conf = now() and new.reservado > 0
     and (to_jsonb(new) - array['almacen', 'reservado']) is not distinct from (to_jsonb(old) - array['almacen', 'reservado']) then
    return new;
  end if;
  -- «¿Salió?» tardío (decisión 15): la caja de una OV cancelada sí salió. Solo
  -- se anota la entrega (entregado > 0, sin apartado) en un renglón que no la
  -- tenía; la OV pasa a entregada_cancelada en la misma sentencia (si no,
  -- verificar_ov rechaza la cancelada con piezas que salieron).
  if tg_op = 'UPDATE' and v_estado in ('cancelada', 'entregada_cancelada')
     and new.reservado = 0 and new.entregado > 0 and new.entregado_at is not null
     and (to_jsonb(new) - array['entregado', 'entregado_at', 'entregado_por'])
         is not distinct from (to_jsonb(old) - array['entregado', 'entregado_at', 'entregado_por']) then
    return new;
  end if;
  raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
    message = format('renglón %s de una OV %s: no se edita ni se borra', old.id, v_estado);
end $function$
;

-- ops.tg_ov_ordenes_guarda()  (0 referencias cambiadas)
CREATE OR REPLACE FUNCTION ops.tg_ov_ordenes_guarda()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
declare
  -- Lista de lo PERMITIDO después de borrador: una columna nueva nace congelada.
  permitidas constant text[] := array[
    'estado', 'rev', 'actualizado_at',
    'entregada_at', 'entregada_por', 'entregada_nombre',
    'cancelada_at', 'cancelada_por', 'cancelada_nombre', 'cancelada_origen', 'cancelada_motivo',
    'borrada_at', 'borrada_por', 'borrada_nombre', 'borrada_motivo',
    'devolucion_estado', 'canal_cancelo_at', 'canal_cancelo_ref'];
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_sin_borrado',
      message = format('OV %s: las OV no se borran (borrado lógico: borrada_at/por/motivo, solo admin)', old.folio);
  end if;

  if tg_op = 'INSERT' then
    -- Nace en borrador. Solo crear_auto (creado_via = 'automatico') la crea ya
    -- confirmada, con sus renglones apartados en la misma sentencia; una OV del
    -- panel, la API o Crear FULL pasa por confirmar (aparta todo o nada).
    if not (new.estado = 'borrador' or (new.estado = 'confirmada' and new.creado_via = 'automatico')) then
      raise exception using errcode = '23514', constraint = 'ov_ordenes_transicion_chk',
        message = format('OV %s: nace en borrador (o confirmada solo por crear_auto), no en %s vía %s',
                         new.folio, new.estado, new.creado_via);
    end if;
    return new;
  end if;

  -- Una OV borrada ya no cambia.
  if old.borrada_at is not null
     and (to_jsonb(new) - 'actualizado_at') is distinct from (to_jsonb(old) - 'actualizado_at') then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
      message = format('OV %s está borrada: no cambia', old.folio);
  end if;

  if (new.id, new.folio, new.creado_at, new.creado_por, new.creado_via)
     is distinct from (old.id, old.folio, old.creado_at, old.creado_por, old.creado_via) then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
      message = format('OV %s: id, folio y alta son fijos', old.folio);
  end if;

  -- Transiciones (H04): la foto la validan los CHECK; la película, esto.
  -- cancelada → entregada_cancelada: el «¿salió?» tardío (decisión 15); que
  -- haya estado confirmada lo exige ov_ordenes_conf_chk.
  if new.estado is distinct from old.estado and not (
        (old.estado = 'borrador'   and new.estado in ('confirmada', 'cancelada'))
     or (old.estado = 'confirmada' and new.estado in ('entregada', 'cancelada', 'entregada_cancelada'))
     or (old.estado = 'entregada'  and new.estado = 'entregada_cancelada')
     or (old.estado = 'cancelada'  and new.estado = 'entregada_cancelada')) then
    raise exception using errcode = '23514', constraint = 'ov_ordenes_transicion_chk',
      message = format('OV %s: %s → %s no es una transición válida', old.folio, old.estado, new.estado);
  end if;

  -- «¿Salió?»: la marca se pone una vez y no se borra. Con ella, la OV solo
  -- sale a cancelada o entregada_cancelada (ov_ordenes_canal_cancelo_chk).
  if old.canal_cancelo_at is not null
     and (new.canal_cancelo_at, new.canal_cancelo_ref) is distinct from (old.canal_cancelo_at, old.canal_cancelo_ref) then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
      message = format('OV %s: la cancelación del canal ya está anotada y no cambia', old.folio);
  end if;

  -- Lo ya anotado no se reescribe (§6.1 punto 8, H19). ov_ordenes no tiene
  -- historia: quién entregó, quién canceló y por qué se ponen UNA vez
  -- (entregada → entregada_cancelada conserva entregada_at, v2 §4.1).
  if new.rev < old.rev then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
      message = format('OV %s: rev no baja (%s → %s)', old.folio, old.rev, new.rev);
  end if;
  if old.entregada_at is not null
     and (new.entregada_at, new.entregada_por, new.entregada_nombre)
         is distinct from (old.entregada_at, old.entregada_por, old.entregada_nombre) then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
      message = format('OV %s: la entrega ya está anotada y no cambia', old.folio);
  end if;
  if old.cancelada_at is not null
     and (new.cancelada_at, new.cancelada_por, new.cancelada_nombre, new.cancelada_origen, new.cancelada_motivo)
         is distinct from (old.cancelada_at, old.cancelada_por, old.cancelada_nombre, old.cancelada_origen, old.cancelada_motivo) then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
      message = format('OV %s: la cancelación ya está anotada y no cambia', old.folio);
  end if;
  -- devolucion_estado: nunca vuelve a NULL ni de recibida a pendiente (la
  -- llegada no se deshace); una cerrada sí se reabre (decisión 16).
  if new.devolucion_estado is distinct from old.devolucion_estado and old.devolucion_estado is not null
     and (new.devolucion_estado is null
          or (old.devolucion_estado = 'recibida' and new.devolucion_estado = 'pendiente')) then
    raise exception using errcode = '23514', constraint = 'ov_ordenes_transicion_chk',
      message = format('OV %s: devolucion_estado %s → %s no es válido', old.folio,
                       old.devolucion_estado, coalesce(new.devolucion_estado, 'NULL'));
  end if;

  if old.estado <> 'borrador'
     and (to_jsonb(new) - permitidas) is distinct from (to_jsonb(old) - permitidas) then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
      message = format('OV %s ya no es borrador (%s): solo cambian %s', old.folio, old.estado, array_to_string(permitidas, ', '));
  end if;

  new.actualizado_at := now();
  return new;
end $function$
;

-- ops.tg_stock_almacen_guarda()  (1 referencia cambiada)
CREATE OR REPLACE FUNCTION ops.tg_stock_almacen_guarda()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public', 'extensions'
AS $function$
begin
  if tg_op = 'UPDATE' and (new.sku, new.almacen, new.fuente) is distinct from (old.sku, old.almacen, old.fuente) then
    raise exception using errcode = '42501', constraint = 'stock_almacen_llave_fija',
      message = format('stock_almacen %s/%s: sku y bodega son fijos (un traspaso son dos movimientos)', old.sku, old.almacen);
  end if;
  if new.apartado > coalesce(old.apartado, 0) then
    if new.fisico - new.apartado < 0 then          -- `libre` todavía no está calculado en un BEFORE
      raise exception using errcode = '23514', constraint = 'stock_almacen_libre_guarda',
        message = format('apartar %s/%s dejaría libre < 0 (fisico %s, apartado %s)', new.sku, new.almacen, new.fisico, new.apartado);
    end if;
    if not exists (select 1 from almacen.almacenes a
                    where a.codigo = new.almacen and a.fuente = 'kubera' and a.admite_ov) then
      raise exception using errcode = '23514', constraint = 'stock_almacen_admite_ov_guarda',
        message = format('apartado en %s: solo se aparta en una bodega de kubera con admite_ov', new.almacen);
    end if;
  end if;
  new.actualizado_at := now();
  return new;
end $function$
;

-- ops.verificar_apartado(citext,text)  (3 referencias cambiadas)
CREATE OR REPLACE FUNCTION ops.verificar_apartado(p_sku citext, p_alm text)
 RETURNS void
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public', 'extensions'
AS $function$
declare
  v record;
begin
  if p_alm is null then
    return;
  end if;
  select coalesce((select sa.apartado from almacen.stock_almacen sa
                    where sa.sku = p_sku and sa.almacen = p_alm), 0) as ap,
         coalesce(sum(l.reservado) filter (where o.estado = 'confirmada' and o.borrada_at is null), 0) as res,
         count(*) filter (where o.estado <> 'confirmada' or o.borrada_at is not null) as malos
    into v
    from ventas.ov_lineas l
    join ventas.ov_ordenes o on o.id = l.orden_id
   where l.sku = p_sku and l.almacen = p_alm
     and l.reservado > 0 and l.entregado_at is null;
  if v.ap <> v.res or v.malos > 0 then
    raise exception using errcode = '23514', constraint = 'stock_apartado_cuadra',
      message = format('apartado descuadrado %s/%s: saldo=%s renglones=%s en_no_confirmadas=%s',
                       p_sku, p_alm, v.ap, v.res, v.malos);
  end if;
end $function$
;

-- ops.verificar_libro(citext,text)  (3 referencias cambiadas)
CREATE OR REPLACE FUNCTION ops.verificar_libro(p_sku citext, p_alm text)
 RETURNS void
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public', 'extensions'
AS $function$
declare
  v record;
begin
  select coalesce((select sa.fisico from almacen.stock_almacen sa where sa.sku = p_sku and sa.almacen = p_alm), 0) as fisico,
         coalesce((select sum(m.delta) from almacen.stock_mov m where m.sku = p_sku and m.almacen = p_alm), 0) as suma,
         coalesce((select m.saldo_despues from almacen.stock_mov m where m.sku = p_sku and m.almacen = p_alm
                    order by m.id desc limit 1), 0) as ultimo
    into v;
  if v.fisico <> v.suma or v.fisico <> v.ultimo then
    raise exception using errcode = '23514', constraint = 'stock_libro_cuadra',
      message = format('libro descuadrado %s/%s: fisico=%s suma=%s ultimo=%s', p_sku, p_alm, v.fisico, v.suma, v.ultimo);
  end if;
end $function$
;

-- ops.verificar_ov(bigint)  (2 referencias cambiadas)
CREATE OR REPLACE FUNCTION ops.verificar_ov(p_orden_id bigint)
 RETURNS void
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
declare
  v record;
begin
  select o.folio, o.estado, o.borrada_at,
         count(l.id)                                                                    as renglones,
         count(l.id) filter (where l.entregado_at is null)                              as sin_entregar,
         count(l.id) filter (where l.entregado_at is null and l.reservado <> l.cantidad) as sin_apartar,
         count(l.id) filter (where l.reservado > 0)                                     as con_apartado,
         count(l.id) filter (where l.entregado_at is not null)                          as con_entrega,
         count(l.id) filter (where l.entregado > 0)                                     as con_salida
    into v
    from ventas.ov_ordenes o
    left join ventas.ov_lineas l on l.orden_id = o.id
   where o.id = p_orden_id
   group by o.id;
  if not found then
    return;
  end if;
  if v.estado = 'confirmada' and v.borrada_at is null then
    if v.sin_entregar = 0 or v.sin_apartar > 0 then
      raise exception using errcode = '23514', constraint = 'ov_coherente',
        message = format('OV %s confirmada: %s renglones sin entregar, %s sin apartar completos (aparta todo o nada)',
                         v.folio, v.sin_entregar, v.sin_apartar);
    end if;
  elsif v.con_apartado > 0 then
    raise exception using errcode = '23514', constraint = 'ov_coherente',
      message = format('OV %s (%s%s) tiene %s renglones con apartado', v.folio, v.estado,
                       case when v.borrada_at is not null then ', borrada' else '' end, v.con_apartado);
  end if;
  if v.estado = 'entregada' and v.sin_entregar > 0 then
    raise exception using errcode = '23514', constraint = 'ov_coherente',
      message = format('OV %s entregada con %s renglones sin entregar', v.folio, v.sin_entregar);
  end if;
  -- Un borrador no ha entregado nada; una cancelada no dejó salir piezas: si
  -- algo salió, la cancelación es entregada_cancelada (DEVOLUCIONES §4e), que
  -- es la única que admite devolucion_estado.
  if v.estado = 'borrador' and v.con_entrega > 0 then
    raise exception using errcode = '23514', constraint = 'ov_coherente',
      message = format('OV %s en borrador con %s renglones entregados', v.folio, v.con_entrega);
  end if;
  if v.estado = 'cancelada' and v.con_salida > 0 then
    raise exception using errcode = '23514', constraint = 'ov_coherente',
      message = format('OV %s cancelada con %s renglones de los que salieron piezas: es entregada_cancelada',
                       v.folio, v.con_salida);
  end if;
end $function$
;

commit;
