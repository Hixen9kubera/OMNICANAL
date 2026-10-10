-- 0071_ov_editar_confirmada.sql
--
-- QUÉ HACE
--   Permite EDITAR una orden de venta ya CONFIRMADA (pedido de Brandon,
--   9-oct-2026: «que cualquiera pueda editar una orden ya confirmada, en caso de
--   que se requiera hacer cualquier cambio»). Hasta hoy la base lo impedía: al
--   confirmar, el contenido de la OV quedaba congelado por dos guardias.
--
--   1. `ops.tg_ov_ordenes_guarda` y `ops.tg_ov_lineas_guarda` ganan UNA puerta:
--      si la transacción trae `set local app.ov_edicion = '<id de la OV>'`
--      (la puerta se abre para UNA orden, no para todas), y la OV sigue
--      `confirmada`, sin borrar y sin la marca del canal («¿salió?»), entonces
--        · del encabezado cambia lo que captura una persona (cliente, canal,
--          liga con la venta, descripción, guía, paquetería, fechas, moneda,
--          total, comisión, origen del precio);
--        · un renglón que TODAVÍA NO SALE se corrige, se quita o se agrega.
--      Las dos funciones son las de producción al 9-oct (post-0068), copiadas
--      tal cual, con esa puerta agregada. Nada más cambia en ellas.
--   2. El catálogo de `ventas.ov_mensajes.evento` gana `editada`: la sentencia
--      que edita deja ahí, en la misma transacción, quién cambió qué (antes y
--      después). Ese mensaje es el rastro que la inmutabilidad protegía.
--
-- LO QUE NO CAMBIA
--   · Sin la puerta, todo sigue exactamente igual de cerrado: confirmar,
--     entregar, cancelar y el canal no pueden tocar el contenido.
--   · Un renglón que ya salió no se edita ni se borra, con o sin puerta.
--   · Entregada, cancelada y entregada_cancelada siguen inmutables.
--   · Los diferidos (`ov_coherente`, `stock_apartado_cuadra`) y la guardia del
--     saldo (`stock_almacen_libre_guarda`) NO se tocan: una edición que deje un
--     renglón sin apartar completo, o el apartado descuadrado, truena al COMMIT
--     igual que antes. Apartar sigue siendo todo o nada.
--
-- QUIÉN ABRE LA PUERTA
--   Solo `services/ordenes_venta.py::SQL_EDITAR_CONFIRMADA` (un `execute`, con
--   CAS de `rev`). `set local` muere con la transacción: no queda nada pegado
--   en la conexión del pooler (regla 13).
--
-- REQUISITO: la 0068 (las tablas ya viven en `ventas` y `almacen`).
-- IDEMPOTENTE: se puede correr dos veces.
-- OJO CON EL ORDEN: después de ésta, la 0068 NO se vuelve a correr sola. La 0068
--   recrea las dos guardias con su texto de entonces, SIN la puerta, y lo hace
--   sin error: la edición quedaría rechazada (42501) hasta volver a correr la
--   0071. El orden de una base nueva es 0064 → 0065 → 0068 → 0071.
-- REVERSA: volver a crear las dos funciones con el texto de la 0068 y regresar
--   el CHECK del catálogo sin `editada` (falla si ya hay mensajes `editada`:
--   primero hay que decidir qué hacer con ese historial).

begin;

set local lock_timeout = '5s';
set local statement_timeout = '60s';

-- ── 1. El catálogo de eventos: + 'editada' ──────────────────────────────────
alter table ventas.ov_mensajes drop constraint if exists ov_mensajes_evento_chk;
alter table ventas.ov_mensajes add constraint ov_mensajes_evento_chk
  check (evento is null
         or evento in ('creada', 'borrador_guardado', 'descartada', 'confirmada',
                    'editada', 'no_alcanzo', 'entregada_parcial', 'entregada',
                    'cancelada', 'borrada_admin', 'canal_cancelo', 'devolucion_esperada',
                    'devolucion_recibida', 'devolucion_aprobada', 'devolucion_merma', 'devolucion_cerrada'));

-- ── 2. Las dos guardias, con la puerta de la edición ────────────────────────
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
  -- 0071 · EDITAR UNA CONFIRMADA. Lo que una persona puede corregir en una OV
  -- confirmada: lo que capturó. El alta, la confirmación, el tipo y el envío a
  -- FULL siguen fijos. Solo cuenta por la puerta de la edición (ver abajo).
  editables constant text[] := array[
    'cliente', 'canal', 'mp_canal', 'mp_cuenta', 'mp_orden', 'descripcion', 'guia',
    'paqueteria', 'fecha_venta', 'entrega_limite', 'moneda', 'total', 'comision',
    'precio_origen'];
  v_cambian text[];
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

  if old.estado <> 'borrador' then
    v_cambian := permitidas;
    -- La puerta de la edición (0071): `set local app.ov_edicion = '<id de ESTA OV>'`
    -- en la MISMA transacción, y solo mientras la OV sigue confirmada, sin borrar
    -- y el canal no la ha cancelado. Se abre para una orden, no para todas. Ninguna otra
    -- sentencia la abre: confirmar, entregar y cancelar siguen sin poder tocar el
    -- contenido. El rastro de cada edición es el mensaje `editada` que la misma
    -- sentencia deja en ov_mensajes.
    if old.estado = 'confirmada' and new.estado = 'confirmada'
       and old.canal_cancelo_at is null and new.canal_cancelo_at is null
       and old.borrada_at is null and new.borrada_at is null
       and coalesce(current_setting('app.ov_edicion', true), '') = old.id::text then
      v_cambian := permitidas || editables;
    end if;
    if (to_jsonb(new) - v_cambian) is distinct from (to_jsonb(old) - v_cambian) then
      raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
        message = format('OV %s ya no es borrador (%s): solo cambian %s', old.folio, old.estado, array_to_string(v_cambian, ', '));
    end if;
  end if;

  new.actualizado_at := now();
  return new;
end $function$;

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
  -- 0071 · la puerta de la edición de una confirmada (ver tg_ov_ordenes_guarda).
  v_marca   timestamptz;
  v_puerta  constant text := coalesce(current_setting('app.ov_edicion', true), '');
begin
  if tg_op = 'INSERT' then
    -- Agregar renglones: en borrador, o en la MISMA transacción que creó la OV
    -- (crear_auto inserta la OV confirmada y sus renglones apartados juntos).
    -- creado_at = now() es «nació en esta transacción»: now() es la hora de inicio de la transacción.
    select o.estado, o.creado_at, o.borrada_at, o.canal_cancelo_at
      into v_estado, v_creado, v_borrada, v_marca
      from ventas.ov_ordenes o where o.id = new.orden_id
       for no key update;
    if v_borrada is not null then
      raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
        message = format('OV %s está borrada: no se le agregan renglones', new.orden_id);
    end if;
    -- 0071 · editar una confirmada: por la puerta de la edición se le agrega un
    -- renglón que TODAVÍA NO SALE (que nazca apartado completo lo exige
    -- ov_coherente al COMMIT). Un renglón ya entregado no nace por aquí: la
    -- entrega tiene su sentencia, que es la que escribe el libro.
    if v_estado = 'confirmada' and v_marca is null and v_puerta = new.orden_id::text
       and new.entregado_at is null then
      return new;
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

  -- 0071 · EDITAR UNA CONFIRMADA. Por la puerta de la edición, un renglón que
  -- todavía no sale se corrige o se quita aunque aparte piezas: el saldo lo
  -- mueve la misma sentencia y lo revisan stock_apartado_cuadra y ov_coherente
  -- al COMMIT. Entregar NO entra por aquí (tiene su sentencia y no abre la puerta).
  if v_puerta = old.orden_id::text then
    select o.estado, o.borrada_at, o.canal_cancelo_at into v_estado, v_borrada, v_marca
      from ventas.ov_ordenes o where o.id = old.orden_id
       for no key update;
    if v_estado = 'confirmada' and v_borrada is null and v_marca is null
       and (tg_op = 'DELETE' or new.entregado_at is null) then
      return coalesce(new, old);
    end if;
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
end $function$;

insert into ops.migraciones (migracion, detalle)
values ('0071_ov_editar_confirmada',
        jsonb_build_object('puerta', 'app.ov_edicion', 'evento_nuevo', 'editada'));

commit;
