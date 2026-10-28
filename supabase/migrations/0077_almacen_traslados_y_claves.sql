-- ═══════════════════════════════════════════════════════════════════════════
-- C′ · 0077 (T0, SANDBOX; a producción solo con acta) — traslados, «una venta, una sola fuente»,
--      claves nuevas del libro, devolución sin OV, SKU obligatorio y la plantilla D9 (almacen.diario).
-- Es la C_0076 probada el 13-oct, SIN rediseño de los traslados (SPEC_T0 §5.1). Cambia solo:
--   · C5 tolera migrada_ov y la hace TERMINAL (§5.2) [D4 PENDIENTE DE FIRMA];
--   · C5b claves rec:, retiro_full:, devret:, devord: (se quita fmt:), stock_mov_ov_chk y devolución sin OV (§5.3);
--   · C5c el SKU tiene que existir en core.products (§5.4);
--   · C7 existencias con por_verificar y el arreglo del SKU en 0 (de la B′) más el tránsito (§5.6);
--   · C7b la plantilla D9: almacen.diario, asentar_diario, rechazar_diario y su reporte (§6).
--   · [R 21-oct] asentar_diario ya NO escribe t0_partidas (un solo escritor: @@T1 de la plantilla, con el lote de T0
--     explícito); los traslados del diario firman sus stock_mov (quien_nombre, via 'diario', nota) con el GUC almacen.firma.
--   · [H 21-oct, huecos de INVENTARIO FANOUT] devoluciones:
--       H1  una devolución FULL que llega a nuestra puerta (devret, destino seller_address) cuenta sus renglones FULL;
--       H2  tipo de diario devolucion_ov: dev:<diario.id>:recibe con ov_linea_id por el trío mp_* y el SKU; el espacio de
--           la N es almacen.diario.id (la identidad arranca después de la N más alta que ya use una clave dev:);
--       H3  devret lleva :<n> (dos cajas o un dictamen partido de la misma devolución no chocan en stock_mov_clave_uq);
--       H5/H10/H11  la conciliación filtra por la LLEGADA (no por el reembolso ni por actualizado_at) y una OV solo
--           cuenta como atendida con devolucion_estado recibida|cerrada o un dev: asentado;
--       H9  sin OV solo entran devoluciones FULL a nuestra puerta o de ventas anteriores al corte;
--       H11 una devret que llegó antes del corte ya está en la copia de Odoo: se rechaza;
--       H7/H12 recepción y corrección no aceptan devoluciones; lo que llega sin venta conocida es devolucion_sin_venta,
--           a una ubicación NO vendible (POR VERIFICAR) con clave devsv:<diario.id>.
--     diario_ov busca la espejo de una S también por el trío mp_* (las de marketplace nacen t0:<canal>:<CUENTA>:<orden>).
--     [WOO SALIDA] ninguna bodega kubera con cuenta_para_woo sin existencias cargadas (al COMMIT y en la vigía).
-- Requiere B′ (0076). C5 toca ventas.ov_ordenes (Brandon) y ops.odoo_sale_orders (odoo_ventas).
-- Marcas: [S#] revisión de seguridad · [T#] revisión técnica · [T0 §x] contrato SPEC_T0 · [D4] pendiente de firma.
-- ═══════════════════════════════════════════════════════════════════════════
begin;
set local lock_timeout = '5s';
set local statement_timeout = '120s';

do $$ begin
  if to_regclass('almacen.desprendimientos') is null
     or to_regprocedure('almacen.desprender_desde_copia(bigint[], bigint, text, jsonb, jsonb, jsonb)') is null
     or to_regprocedure('almacen.ov_espejo(jsonb, jsonb)') is null or to_regclass('almacen.t0_partidas') is null then
    raise exception using errcode = 'KB000', message = 'C′: falta la B′ (0076)';
  end if;
end $$;

-- El corte (STOCK_LIBRO_DESDE): un solo lugar para la vista, las devoluciones y la conciliación.
create or replace function almacen.t0_corte() returns timestamptz
language sql immutable security invoker set search_path = pg_catalog as $$
  select timestamptz '2026-10-15 16:48:47+00'
$$;

-- [H5/H11] La LLEGADA de una devolución de ML (lo mismo que fanout_vivo._llegada): el primer paso «recibida» de su
-- historia; sin paso, si ML la da por entregada (delivered), la hora del reembolso, salvo que reembolsara al DESPACHAR
-- (refund_at = 'shipped': ese reembolso es la salida, no la llegada). NULL = no ha llegado o llegó sin fecha.
create or replace function almacen.devolucion_llegada(p_canal text, p_cuenta text, p_ret text)
returns table (llego boolean, llegada_at timestamptz)
language sql stable security invoker set search_path = pg_catalog, public as $$
  select (h.primera is not null or r.estado_canal = 'delivered'),
         coalesce(h.primera,
                  case when r.estado_canal = 'delivered' and coalesce(r.payload -> 'returns' ->> 'refund_at', '') <> 'shipped'
                       then r.reembolsada_at end)
    from channel.returns r
    left join lateral (select min(x.changed_at) as primera from channel.return_history x
                        where x.canal = r.canal and x.cuenta = r.cuenta and x.external_return_id = r.external_return_id
                          and x.estado_nuevo = 'recibida') h on true
   where r.canal = p_canal and upper(btrim(r.cuenta)) = p_cuenta and r.external_return_id = p_ret
$$;

-- ───────────────────────────────────────────────────────────────────────────
-- C1. El puente: la ubicación NO interna de Odoo que representa a una bodega de kubera
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists almacen.puentes_odoo (
  almacen          text        not null,
  fuente           text        not null default 'kubera',
  odoo_location_id integer     not null,
  odoo_uso         text        not null,
  activo           boolean     not null default true,
  acta             text        not null,
  motivo           text,                                 -- por qué del último cambio de `activo`
  actualizado_por  text        default nullif(current_setting('app.usuario', true), ''),
  creado_at        timestamptz not null default now(),
  constraint puentes_odoo_pkey       primary key (almacen),
  constraint puentes_odoo_loc_uq     unique (odoo_location_id),
  constraint puentes_odoo_fuente_chk check (fuente = 'kubera'),
  constraint puentes_odoo_almacen_fk foreign key (almacen, fuente) references almacen.almacenes (codigo, fuente)
      on update restrict on delete restrict not deferrable,                                            -- [S6]
  constraint puentes_odoo_uso_chk    check (odoo_uso in ('transit', 'customer', 'inventory')),          -- nunca 'internal'
  constraint puentes_odoo_acta_chk   check (length(btrim(acta)) >= 10)
);
create table if not exists almacen.puentes_odoo_hist (
  id          bigint      generated always as identity primary key,
  almacen     text        not null,
  antes       jsonb,
  despues     jsonb       not null,
  motivo      text,
  quien       text,
  cambiado_at timestamptz not null default now()
);
-- Guarda [S11]: el puente define qué cuenta como «salió de Odoo». Inmutable salvo `activo`, con motivo; sin DELETE.
create or replace function almacen.tg_puentes_odoo_guarda() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_quien text := nullif(current_setting('app.usuario', true), '');
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', constraint = 'puentes_odoo_sin_borrado',
      message = format('puente de %s: no se borra; se apaga con activo = false y motivo', old.almacen);
  end if;
  perform 1 from almacen.almacenes a where a.codigo = new.almacen and a.fuente = 'kubera' for key share;   -- [S1-b]
  if not found then
    raise exception using errcode = '23503', constraint = 'puentes_odoo_fuente_always', message = format('%s no es de kubera', new.almacen);
  end if;
  if coalesce(v_quien, new.actualizado_por) is null then
    raise exception using errcode = '23514', constraint = 'puentes_odoo_quien_chk', message = 'puentes_odoo exige quién (app.usuario)';
  end if;
  if tg_op = 'UPDATE' then
    if (new.almacen, new.fuente, new.odoo_location_id, new.odoo_uso, new.acta, new.creado_at)
       is distinct from (old.almacen, old.fuente, old.odoo_location_id, old.odoo_uso, old.acta, old.creado_at) then
      raise exception using errcode = '42501', constraint = 'puentes_odoo_inmutable',
        message = format('puente de %s: almacen, ubicación de Odoo, uso y acta son fijos (otro puente = otra acta)', old.almacen);
    end if;
    if new.activo is distinct from old.activo
       and (length(btrim(coalesce(new.motivo, ''))) < 10 or new.motivo is not distinct from old.motivo) then
      raise exception using errcode = '23514', constraint = 'puentes_odoo_motivo_chk', message = 'cambiar activo exige un motivo NUEVO de 10+';
    end if;
  end if;
  new.actualizado_por := coalesce(v_quien, new.actualizado_por);
  return new;
end $$;
create or replace function almacen.tg_puentes_odoo_hist() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
begin
  insert into almacen.puentes_odoo_hist (almacen, antes, despues, motivo, quien)
  values (new.almacen, case when tg_op = 'UPDATE' then to_jsonb(old) end, to_jsonb(new), coalesce(new.motivo, new.acta),
          coalesce(nullif(current_setting('app.usuario', true), ''), new.actualizado_por));
  return null;
end $$;
create trigger puentes_odoo_guarda before insert or update or delete on almacen.puentes_odoo
  for each row execute function almacen.tg_puentes_odoo_guarda();
create trigger puentes_odoo_hist after insert or update on almacen.puentes_odoo
  for each row execute function almacen.tg_puentes_odoo_hist();
create trigger puentes_odoo_sin_truncate before truncate on almacen.puentes_odoo
  for each statement execute function ops.tg_sin_truncate();
create trigger puentes_odoo_hist_solo_agregar before update or delete on almacen.puentes_odoo_hist
  for each row execute function ops.tg_solo_agregar();
create trigger puentes_odoo_hist_sin_truncate before truncate on almacen.puentes_odoo_hist
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- C2. Traslados, líneas y eventos
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists almacen.traslados (
  id               bigint      generated always as identity,
  origen           text        not null,
  destino          text        not null,
  estado           text        not null default 'solicitado',   -- lo calcula la base
  nota             text,
  solicitado_por   text        not null default coalesce(nullif(current_setting('app.usuario', true), ''), current_user),
  solicitado_at    timestamptz not null default now(),
  cancelado_at     timestamptz,
  cancelado_por    text,
  cancelado_motivo text,
  cuadrado_at      timestamptz,
  cuadrado_por     text,
  actualizado_at   timestamptz not null default now(),
  constraint traslados_pkey            primary key (id),
  constraint traslados_origen_fk       foreign key (origen)  references almacen.almacenes (codigo),
  constraint traslados_destino_fk      foreign key (destino) references almacen.almacenes (codigo),
  constraint traslados_distintos_chk   check (origen <> destino),
  constraint traslados_estado_chk      check (estado in ('solicitado','en_transito','recibido_parcial','recibido','cuadrado','cancelado')),
  constraint traslados_cancelado_chk   check ((estado = 'cancelado') = (cancelado_at is not null)),
  constraint traslados_cancel_mot_chk  check (cancelado_at is null or length(btrim(coalesce(cancelado_motivo, ''))) >= 5),
  constraint traslados_cuadrado_chk    check ((estado = 'cuadrado') = (cuadrado_at is not null))
);
create table if not exists almacen.traslado_lineas (
  traslado_id bigint  not null,
  sku         citext  not null,
  solicitado  integer not null,
  salido      integer not null default 0,
  recibido    integer not null default 0,
  diferencia  integer not null default 0,
  en_transito integer generated always as (salido - recibido - diferencia) stored,
  constraint traslado_lineas_pkey        primary key (traslado_id, sku),
  constraint traslado_lineas_traslado_fk foreign key (traslado_id) references almacen.traslados (id),
  constraint traslado_lineas_cant_chk    check (solicitado > 0 and salido >= 0 and recibido >= 0 and diferencia >= 0)
);
create index if not exists traslado_lineas_transito_ix on almacen.traslado_lineas (sku) where en_transito <> 0;
create table if not exists almacen.traslado_eventos (
  id                       bigint      generated always as identity,
  traslado_id              bigint      not null,
  sku                      citext      not null,
  evento                   text        not null,
  sistema                  text        not null,
  cantidad                 integer     not null,
  stock_mov_id             bigint,
  odoo_move_line_id        bigint,
  odoo_picking_id          integer,
  odoo_picking_ref         text,
  odoo_origen_location_id  integer,
  odoo_destino_location_id integer,
  odoo_hecho_at            timestamptz,
  anomalia                 text,
  nota                     text,
  quien                    text        not null default coalesce(nullif(current_setting('app.usuario', true), ''), current_user),
  creado_at                timestamptz not null default now(),
  constraint traslado_eventos_pkey        primary key (id),
  constraint traslado_eventos_linea_fk    foreign key (traslado_id, sku) references almacen.traslado_lineas (traslado_id, sku),
  constraint traslado_eventos_mov_fk      foreign key (stock_mov_id) references almacen.stock_mov (id),
  constraint traslado_eventos_mov_uq      unique (stock_mov_id),
  constraint traslado_eventos_odoo_uq     unique (odoo_move_line_id),
  constraint traslado_eventos_evento_chk  check (evento in ('salida','entrada','diferencia')),
  constraint traslado_eventos_sistema_chk check (sistema in ('odoo','kubera')),
  constraint traslado_eventos_cant_chk    check (cantidad > 0),
  constraint traslado_eventos_liga_chk    check (case
      when evento = 'diferencia' then sistema = 'kubera' and stock_mov_id is null and odoo_move_line_id is null
                                      and length(btrim(coalesce(nota, ''))) >= 5
      when sistema = 'kubera'    then stock_mov_id is not null and odoo_move_line_id is null and anomalia is null
      else                            odoo_move_line_id is not null and stock_mov_id is null
                                      and odoo_picking_id is not null and odoo_hecho_at is not null
    end)
);
create index if not exists traslado_eventos_traslado_ix on almacen.traslado_eventos (traslado_id, sku);

-- Guardas de traslados y líneas: el estado y las sumas solo los pone la base (GUC 'traslado:<txid>'). [S5-a]
create or replace function almacen.tg_traslados_guarda() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_base boolean := coalesce(current_setting('app.almacen_escritor', true), '') = 'traslado:' || txid_current();
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', constraint = 'traslados_sin_borrado', message = 'un traslado no se borra: se cancela';
  end if;
  if tg_op = 'INSERT' then
    if new.estado <> 'solicitado' or new.cancelado_at is not null or new.cuadrado_at is not null then
      raise exception using errcode = '23514', constraint = 'traslados_alta_chk', message = 'un traslado nace solicitado';
    end if;
    if (select count(*) from almacen.almacenes a where a.codigo in (new.origen, new.destino) and a.fuente = 'kubera') = 0 then
      raise exception using errcode = '23514', constraint = 'traslados_odoo_a_odoo',
        message = 'Odoo↔Odoo es un traslado interno de Odoo; aquí al menos un lado es de kubera';
    end if;
    return new;
  end if;
  if (new.id, new.origen, new.destino, new.solicitado_por, new.solicitado_at)
     is distinct from (old.id, old.origen, old.destino, old.solicitado_por, old.solicitado_at) then
    raise exception using errcode = '42501', constraint = 'traslados_inmutable', message = 'origen, destino y solicitud son fijos';
  end if;
  if (new.estado, new.cancelado_at, new.cancelado_por, new.cancelado_motivo, new.cuadrado_at, new.cuadrado_por)
     is distinct from (old.estado, old.cancelado_at, old.cancelado_por, old.cancelado_motivo, old.cuadrado_at, old.cuadrado_por)
     and not v_base then
    raise exception using errcode = '42501', constraint = 'traslados_estado_lo_pone_la_base',
      message = 'el estado de un traslado lo calcula la base (eventos, cancelar_traslado, cuadrar_traslado)';
  end if;
  new.actualizado_at := now();
  return new;
end $$;
create or replace function almacen.tg_traslado_lineas_guarda() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_base boolean := coalesce(current_setting('app.almacen_escritor', true), '') = 'traslado:' || txid_current();
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', constraint = 'traslado_lineas_sin_borrado', message = 'una línea no se borra';
  end if;
  if tg_op = 'INSERT' then
    if new.salido <> 0 or new.recibido <> 0 or new.diferencia <> 0
       or not exists (select 1 from almacen.traslados t where t.id = new.traslado_id and t.estado = 'solicitado') then
      raise exception using errcode = '23514', constraint = 'traslado_lineas_alta_chk',
        message = 'una línea nace en cero y solo en un traslado solicitado';
    end if;
    return new;
  end if;
  if (new.traslado_id, new.sku) is distinct from (old.traslado_id, old.sku) then
    raise exception using errcode = '42501', constraint = 'traslado_lineas_inmutable', message = 'traslado y sku son fijos';
  end if;
  if (new.salido, new.recibido, new.diferencia) is distinct from (old.salido, old.recibido, old.diferencia) and not v_base then
    raise exception using errcode = '42501', constraint = 'traslado_lineas_sumas_las_pone_la_base',
      message = 'salido, recibido y diferencia los suma la base desde traslado_eventos';
  end if;
  if new.solicitado is distinct from old.solicitado and old.salido <> 0 then
    raise exception using errcode = '23514', constraint = 'traslado_lineas_solicitado_fijo', message = 'lo solicitado ya no cambia: algo salió';
  end if;
  return new;
end $$;
create trigger traslados_guarda before insert or update or delete on almacen.traslados
  for each row execute function almacen.tg_traslados_guarda();
create trigger traslados_sin_truncate before truncate on almacen.traslados
  for each statement execute function ops.tg_sin_truncate();
create trigger traslado_lineas_guarda before insert or update or delete on almacen.traslado_lineas
  for each row execute function almacen.tg_traslado_lineas_guarda();
create trigger traslado_lineas_sin_truncate before truncate on almacen.traslado_lineas
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- C3. La regla del evento (BEFORE INSERT) y las sumas (AFTER INSERT) [T1, S5-a, S1-b]
-- ───────────────────────────────────────────────────────────────────────────
create or replace function almacen.tg_traslado_evento_regla() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare
  t      record;
  l      record;
  m      record;
  v_lado text;
  v_f    text;
  v_esc  text := coalesce(current_setting('app.almacen_escritor', true), '');
  v_pte  integer;
begin
  select * into t from almacen.traslados where id = new.traslado_id for update;          -- un evento a la vez por traslado
  select * into l from almacen.traslado_lineas where traslado_id = new.traslado_id and sku = new.sku for update;
  if t.id is null or l.traslado_id is null then                                            -- [S1-b] la FK puede saltarse en réplica
    raise exception using errcode = '23503', constraint = 'traslado_evento_linea_always',
      message = format('evento sin su traslado/línea (%s, %s)', new.traslado_id, new.sku);
  end if;
  if t.estado in ('cancelado', 'cuadrado') then
    raise exception using errcode = '23514', constraint = 'traslado_cerrado', message = format('TRA-%s está %s', t.id, t.estado);
  end if;
  v_lado := case new.evento when 'salida' then t.origen else t.destino end;
  select a.fuente into v_f from almacen.almacenes a where a.codigo = v_lado for share;
  if new.evento <> 'diferencia' then
    if new.sistema is distinct from v_f then                                                -- REGLA 2
      raise exception using errcode = '42501', constraint = 'traslado_evento_sistema',
        message = format('TRA-%s: el lado %s es de %s; un evento de %s no lo registra', t.id, v_lado, v_f, new.sistema);
    end if;
    if v_esc <> (case new.sistema when 'odoo' then 'copia_odoo:' else 'libro:' end) || txid_current() then   -- [T1, S5-a]
      raise exception using errcode = '42501', constraint = 'traslado_evento_escritor',
        message = format('TRA-%s: un evento de %s solo lo escribe %s', t.id, new.sistema,
                         (case new.sistema when 'odoo' then 'la copia de Odoo' else 'el libro' end));
    end if;
  elsif nullif(current_setting('app.usuario', true), '') is null then
    raise exception using errcode = '23514', constraint = 'traslado_diferencia_quien', message = 'una diferencia exige app.usuario';
  end if;
  if new.sistema = 'odoo' and new.evento = 'salida' then                                    -- REGLA 3: solo al puente
    select p.odoo_location_id into v_pte from almacen.puentes_odoo p where p.almacen = t.destino and p.activo;
    if v_pte is null or new.odoo_destino_location_id is distinct from v_pte then
      raise exception using errcode = '23514', constraint = 'traslado_salida_fuera_del_puente',
        message = format('TRA-%s: la salida de Odoo fue a %s y el puente de %s es %s', t.id,
                         new.odoo_destino_location_id, t.destino, v_pte);
    end if;
  end if;
  if new.sistema = 'odoo' and new.evento = 'entrada' then                                   -- REGLA 4: se registra, la vigía grita
    select p.odoo_location_id into v_pte from almacen.puentes_odoo p where p.almacen = t.origen and p.activo;
    if v_pte is null or new.odoo_origen_location_id is distinct from v_pte then
      raise exception using errcode = '23514', constraint = 'traslado_entrada_fuera_del_puente',
        message = format('TRA-%s: la entrada de Odoo no viene del puente de %s', t.id, t.origen);
    end if;
    if l.recibido + l.diferencia + new.cantidad > l.salido then
      new.anomalia := 'entrada_odoo_antes_de_salir';
    end if;
  end if;
  if new.sistema = 'kubera' and new.evento <> 'diferencia' then
    select * into m from almacen.stock_mov where id = new.stock_mov_id;
    if m.sku is distinct from new.sku or m.almacen is distinct from v_lado or m.ref is distinct from 'TRA-' || t.id
       or m.motivo is distinct from (case new.evento when 'salida' then 'traspaso_salida' else 'traspaso_entrada' end)   -- [T1]
       or abs(m.delta) is distinct from new.cantidad then
      raise exception using errcode = '23514', constraint = 'traslado_evento_movimiento',
        message = format('TRA-%s: el evento no corresponde a su movimiento del libro %s', t.id, new.stock_mov_id);
    end if;
  end if;
  if (new.sistema = 'kubera' and new.evento = 'entrada') or new.evento = 'diferencia' then  -- REGLA 1: LA invariante
    if l.recibido + l.diferencia + new.cantidad > l.salido then
      raise exception using errcode = '23514', constraint = 'traslado_entra_sin_salir',
        message = format('TRA-%s %s: entraría %s y solo han salido %s (recibido %s, diferencia %s)',
                         t.id, new.sku, new.cantidad, l.salido, l.recibido, l.diferencia);
    end if;
  end if;
  return new;
end $$;
create or replace function almacen.tg_traslado_evento_suma() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_prev text := coalesce(current_setting('app.almacen_escritor', true), '');
begin
  perform set_config('app.almacen_escritor', 'traslado:' || txid_current(), true);
  update almacen.traslado_lineas
     set salido     = salido     + case when new.evento = 'salida'     then new.cantidad else 0 end,
         recibido   = recibido   + case when new.evento = 'entrada'    then new.cantidad else 0 end,
         diferencia = diferencia + case when new.evento = 'diferencia' then new.cantidad else 0 end
   where traslado_id = new.traslado_id and sku = new.sku;
  update almacen.traslados t
     set estado = case
           when not exists (select 1 from almacen.traslado_lineas x where x.traslado_id = t.id
                             and (x.recibido + x.diferencia) < x.solicitado) then 'recibido'
           when exists (select 1 from almacen.traslado_lineas x where x.traslado_id = t.id and x.recibido > 0) then 'recibido_parcial'
           else 'en_transito' end
   where t.id = new.traslado_id;
  perform set_config('app.almacen_escritor', v_prev, true);
  return null;
end $$;
create trigger traslado_eventos_regla before insert on almacen.traslado_eventos
  for each row execute function almacen.tg_traslado_evento_regla();
create trigger traslado_eventos_suma after insert on almacen.traslado_eventos
  for each row execute function almacen.tg_traslado_evento_suma();
create trigger traslado_eventos_solo_agregar before update or delete on almacen.traslado_eventos
  for each row execute function ops.tg_solo_agregar();
create trigger traslado_eventos_sin_truncate before truncate on almacen.traslado_eventos
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- C4. Ningún traspaso en el libro sin su traslado (al COMMIT) [T: escrito]
-- ───────────────────────────────────────────────────────────────────────────
create or replace function almacen.tg_stock_mov_tras_evento() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
begin
  if (select count(*) from almacen.traslado_eventos e where e.stock_mov_id = new.id) <> 1 then
    raise exception using errcode = '23514', constraint = 'stock_mov_tras_sin_evento',
      message = format('stock_mov %s (%s) es un traspaso de traslado sin su evento', new.id, new.clave);
  end if;
  return null;
end $$;
create constraint trigger stock_mov_tras_evento after insert on almacen.stock_mov
  deferrable initially deferred for each row when (new.clave like 'tras:%')
  execute function almacen.tg_stock_mov_tras_evento();

-- ───────────────────────────────────────────────────────────────────────────
-- C5. Una venta se descuenta en UNA sola fuente [T: escrito]
--     (a) la bitácora de odoo_ventas no puede anotar un almacén que ya no es de Odoo (el 150 tras el corte);
--     (b) una orden del marketplace la surte una OV viva O una orden viva de Odoo, nunca las dos;
--     (c) [T0 §5.2, D4] migrada_ov solo la pone t0_migrar_odoo_sale_orders (GUC t0:<txid>) y es TERMINAL:
--         el upsert de odoo_ventas_log.py ya no la puede revivir.
-- ───────────────────────────────────────────────────────────────────────────
create or replace function almacen.tg_una_venta_una_fuente() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_canal text; v_cuenta text; v_orden text;
begin
  if tg_table_name = 'odoo_sale_orders' then
    if tg_op = 'UPDATE' and old.accion = 'migrada_ov' and new.accion is distinct from old.accion then          -- [T0 §5.2]
      raise exception using errcode = '42501', constraint = 'odoo_sale_orders_migrada_fija',
        message = format('odoo_sale_orders %s/%s/%s ya migró a una OV de kubera: su acción no cambia',
                         old.canal, old.cuenta, old.external_order_id);
    end if;
    if new.accion = 'migrada_ov' and (tg_op = 'INSERT' or old.accion is distinct from 'migrada_ov')
       and coalesce(current_setting('app.almacen_escritor', true), '') <> 't0:' || txid_current() then
      raise exception using errcode = '42501', constraint = 'odoo_sale_orders_migrada_fija',
        message = 'migrada_ov solo la pone almacen.t0_migrar_odoo_sale_orders, en la transacción de T0';
    end if;
    if new.almacen_id is not null and (tg_op = 'INSERT' or new.almacen_id is distinct from old.almacen_id)
       and not exists (select 1 from almacen.almacenes a where a.odoo_warehouse_id = new.almacen_id and a.fuente = 'odoo') then
      raise exception using errcode = '23514', constraint = 'odoo_sale_orders_almacen_de_odoo',
        message = format('odoo_sale_orders: el almacén %s de Odoo no es de ninguna bodega con fuente odoo', new.almacen_id);
    end if;
    if new.accion not in ('confirmada', 'espera_guia', 'espera_reserva')
       or (tg_op = 'UPDATE' and old.accion in ('confirmada', 'espera_guia', 'espera_reserva')) then
      return new;
    end if;
    v_canal := lower(new.canal); v_cuenta := new.cuenta; v_orden := new.external_order_id;
  else  -- ventas.ov_ordenes
    if new.mp_orden is null or new.borrada_at is not null or new.estado = 'cancelada' then
      return new;
    end if;
    v_canal := lower(new.mp_canal); v_cuenta := new.mp_cuenta; v_orden := new.mp_orden;
  end if;
  perform pg_advisory_xact_lock(hashtextextended('una_venta:' || coalesce(v_canal, '') || '|' || coalesce(v_cuenta, '') || '|' || v_orden, 0));
  if tg_table_name = 'odoo_sale_orders' then
    if exists (select 1 from ventas.ov_ordenes o
                where lower(o.mp_canal) = v_canal and o.mp_cuenta is not distinct from v_cuenta and o.mp_orden = v_orden
                  and o.borrada_at is null and o.estado <> 'cancelada') then
      raise exception using errcode = '23514', constraint = 'una_venta_una_fuente',
        message = format('la orden %s/%s/%s ya la surte una OV de kubera', v_canal, v_cuenta, v_orden);
    end if;
  elsif exists (select 1 from ops.odoo_sale_orders s
                 where lower(s.canal) = v_canal and s.cuenta is not distinct from v_cuenta and s.external_order_id = v_orden
                   and s.accion in ('confirmada', 'espera_guia', 'espera_reserva')) then
    raise exception using errcode = '23514', constraint = 'una_venta_una_fuente',
      message = format('la orden %s/%s/%s ya la surte una orden de Odoo', v_canal, v_cuenta, v_orden);
  end if;
  return new;
end $$;
create trigger odoo_sale_orders_una_fuente before insert or update of almacen_id, accion on ops.odoo_sale_orders
  for each row execute function almacen.tg_una_venta_una_fuente();
create trigger ov_ordenes_una_fuente before insert or update of mp_canal, mp_cuenta, mp_orden, estado, borrada_at
  on ventas.ov_ordenes for each row execute function almacen.tg_una_venta_una_fuente();

-- ───────────────────────────────────────────────────────────────────────────
-- C5b. [T0 §5.3] Claves del libro, OV y devolución sin OV.
--   · fmt: deja de existir para filas nuevas (la 0072 borró sus tablas).
--   · rec:<id>:linea:<n> (futuro documento de recepción) y rec:<doc>:<SKU>:<n> (la plantilla D9).
--   · retiro_full:<CUENTA>:<operacion>:<SKU>:<n> (operacion es texto: ops.fulfillment_operations.operacion_id).
--   · devret:<canal>:<CUENTA>:<external_return_id>:<SKU>:<n> (channel.returns, hoy solo ML) y
--     devord:<canal>:<CUENTA>:<external_order_id>:<SKU>:<n> (canales sin channel.returns). [H3] devret también
--     lleva :<n>: una segunda caja o un dictamen partido (1 a rack, 1 a SCRAP) de la misma devolución no chocan.
--   · devsv:<diario.id> [H12]: devolución que llegó SIN venta conocida (a una ubicación no vendible).
--   La constraint entra NOT VALID y se valida solo si no hay filas viejas que la rompan:
--   producción (0 filas fmt:) queda validada; el sandbox (4 filas ZZCONC fmt:) queda NOT VALID.
-- ───────────────────────────────────────────────────────────────────────────
alter table almacen.stock_mov drop constraint stock_mov_clave_chk,
  add constraint stock_mov_clave_chk check (case motivo
      when 'entrada'          then clave ~ '^(dev:[0-9]+:recibe|rec:[0-9]+:linea:[0-9]+|rec:[A-Za-z0-9._-]+:[^:]+:[0-9]+|retiro_full:[A-Z0-9_]+:[A-Za-z0-9_-]+:[^:]+:[0-9]+)$'
      when 'salida_ov'        then clave ~ '^ov:[0-9]+:linea:[0-9]+:salida$'
      when 'traspaso_salida'  then clave ~ '^(dev:[0-9]+|tras:.+):sale$'
      when 'traspaso_entrada' then clave ~ '^(dev:[0-9]+|tras:.+):entra$'
      when 'devolucion'       then clave ~ '^(dev:[0-9]+:recibe|devret:[a-z_]+:[A-Z0-9_]+:[^:]+:[^:]+:[0-9]+|devord:[a-z_]+:[A-Z0-9_]+:[^:]+:[^:]+:[0-9]+|devsv:[0-9]+)$'
      when 'ajuste_conteo'    then clave ~ '^conteo:[^:]+:[^:]+:[^:]+$'
      when 'merma'            then clave ~ '^(dev:[0-9]+:merma|merma:.+)$'
      when 'correccion'       then clave ~ '^corr:.+$'
      when 'saldo_inicial'    then clave ~ '^ini:[0-9]+:[0-9]+:.+$'
      when 'reubicacion'      then clave ~ '^reub:.+:(sale|entra)$'
      else null end) not valid;
do $$ begin
  if not exists (select 1 from almacen.stock_mov where motivo = 'entrada' and clave ~ '^fmt:') then
    alter table almacen.stock_mov validate constraint stock_mov_clave_chk;   -- producción: 0 filas fmt: → validada
  end if;                                                                   -- sandbox: 4 filas ZZCONC → queda NOT VALID (filas viejas)
end $$;

-- Una devolución lleva ov_linea_id si y solo si es dev: (por la OV); devret/devord van sin OV.
alter table almacen.stock_mov drop constraint stock_mov_ov_chk,
  add constraint stock_mov_ov_chk check (case motivo
      when 'salida_ov'  then ov_linea_id is not null
      when 'devolucion' then (ov_linea_id is not null) = (clave like 'dev:%')
      else ov_linea_id is null end);          -- filas actuales cumplen en los dos ambientes: se valida
drop trigger stock_mov_devolucion_cuadra on almacen.stock_mov;
create constraint trigger stock_mov_devolucion_cuadra after insert on almacen.stock_mov
  deferrable initially deferred for each row when (new.motivo = 'devolucion' and new.ov_linea_id is not null)
  execute function ops.tg_devolucion_de_mas();

-- Devolución sin OV: existe la devolución (devret) o la venta (devord), la pieza volvió a la bodega (no a FULL),
-- la venta no tiene OV de kubera (si la tiene, va por la OV) y no se devuelve más de lo vendido que PUEDE volver sin OV:
--   [H1] en devret a nuestra puerta (destino seller_address, o sin destino: la caja está aquí) cuentan también los
--        renglones FULL: la pieza salió de FULL, no del libro, y entra de verdad;
--   [H9] de lo no FULL solo cuentan las ventas ANTERIORES al corte (ya restadas en la copia de Odoo). Una venta no FULL
--        posterior al corte sin OV nunca se restó del libro: su devolución no entra (doble conteo);
--   [H11] una devret que LLEGÓ antes del corte ya está en la copia de Odoo: no entra.
create or replace function almacen.tg_devolucion_sin_ov() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_p text[]; v_canal text; v_cuenta text; v_orden text; v_sku text; v_dest text; v_vendido integer; v_dev integer;
        v_full integer; v_previa integer; v_posterior integer; v_llego boolean; v_llegada timestamptz; v_corte timestamptz;
begin
  v_corte := almacen.t0_corte();
  v_p := string_to_array(new.clave, ':');
  if v_p[1] = 'devret' then
    v_canal := v_p[2]; v_cuenta := v_p[3]; v_sku := v_p[5];
    select r.destino, r.external_order_id into v_dest, v_orden from channel.returns r
     where r.canal = v_canal and upper(btrim(r.cuenta)) = v_cuenta and r.external_return_id = v_p[4];
    if not found then
      raise exception using errcode = '23503', constraint = 'stock_mov_devret_sin_return',
        message = format('%s: la devolución %s/%s/%s no existe en channel.returns', new.clave, v_canal, v_cuenta, v_p[4]);
    end if;
    if v_dest = 'warehouse' then
      raise exception using errcode = '23514', constraint = 'devret_destino_full',
        message = format('%s: la devolución fue a FULL (destino warehouse), no a la bodega', new.clave);
    end if;
    select x.llego, x.llegada_at into v_llego, v_llegada from almacen.devolucion_llegada(v_canal, v_cuenta, v_p[4]) x;
    if v_llegada < v_corte then
      raise exception using errcode = '23514', constraint = 'devret_llego_antes_del_corte',
        message = format('%s: llegó el %s, antes del corte: ya está en la copia de Odoo (si de verdad no entró a Odoo, va con un conteo)',
                         new.clave, v_llegada);
    end if;
  elsif v_p[1] = 'devord' then
    v_canal := v_p[2]; v_cuenta := v_p[3]; v_orden := v_p[4]; v_sku := v_p[5];
    if not exists (select 1 from channel.orders o where o.canal = v_canal and upper(btrim(o.cuenta)) = v_cuenta
                                                      and o.external_order_id = v_orden) then
      raise exception using errcode = '23503', constraint = 'stock_mov_devord_sin_venta',
        message = format('%s: la venta %s/%s/%s no existe en channel.orders', new.clave, v_canal, v_cuenta, v_orden);
    end if;
  else
    return new;                                                     -- otra forma: la rechaza stock_mov_clave_chk / ov_chk
  end if;
  if upper(v_sku) <> upper(new.sku::text) then
    raise exception using errcode = '23514', constraint = 'stock_devolucion_sku',
      message = format('%s: el SKU de la clave no es %s', new.clave, new.sku);
  end if;
  if exists (select 1 from ventas.ov_ordenes o
              where o.mp_canal = v_canal and o.mp_cuenta = v_cuenta and o.mp_orden = v_orden
                and o.borrada_at is null and o.estado in ('confirmada', 'entregada', 'entregada_cancelada')) then
    raise exception using errcode = '23514', constraint = 'stock_devolucion_tiene_ov',
      message = format('%s: la venta tiene OV de kubera; la devolución va por la OV (dev:)', new.clave);
  end if;
  select coalesce(sum(i.cantidad) filter (where coalesce(i.es_fulfillment, false)
                                              and v_p[1] = 'devret' and coalesce(v_dest, 'seller_address') = 'seller_address'), 0),
         coalesce(sum(i.cantidad) filter (where not coalesce(i.es_fulfillment, false) and o.creado_at < v_corte), 0),
         coalesce(sum(i.cantidad) filter (where not coalesce(i.es_fulfillment, false) and o.creado_at >= v_corte), 0)
    into v_full, v_previa, v_posterior
    from channel.order_items i
    join channel.orders o on o.canal = i.canal and o.cuenta = i.cuenta and o.external_order_id = i.external_order_id
   where i.canal = v_canal and upper(btrim(i.cuenta)) = v_cuenta and i.external_order_id = v_orden
     and upper(i.sku::text) = upper(new.sku::text);
  v_vendido := v_full + v_previa;
  if v_vendido = 0 and v_posterior > 0 then
    raise exception using errcode = '23514', constraint = 'devolucion_venta_posterior_sin_ov',
      message = format('%s: la venta %s/%s/%s es posterior al corte y no tiene OV: el libro nunca la restó, su devolución no entra '
                       || '(si salió, primero su OV espejo o venta_enviada; si no salió, nada que asentar)', new.clave, v_canal, v_cuenta, v_orden);
  end if;
  select coalesce(sum(m.delta), 0) into v_dev from almacen.stock_mov m
   where m.motivo = 'devolucion' and m.ov_linea_id is null and m.sku = new.sku
     and (left(m.clave, length(format('devord:%s:%s:%s:', v_canal, v_cuenta, v_orden))) = format('devord:%s:%s:%s:', v_canal, v_cuenta, v_orden)
          or exists (select 1 from channel.returns r
                      where r.canal = v_canal and upper(btrim(r.cuenta)) = v_cuenta and r.external_order_id = v_orden
                        and left(m.clave, length(format('devret:%s:%s:%s:%s:', r.canal, upper(btrim(r.cuenta)), r.external_return_id, upper(new.sku::text))))
                            = format('devret:%s:%s:%s:%s:', r.canal, upper(btrim(r.cuenta)), r.external_return_id, upper(new.sku::text))));
  if v_dev + new.delta > v_vendido then
    raise exception using errcode = '23514', constraint = 'stock_devolucion_sin_ov_de_mas',
      message = format('%s: devuelto %s + %s > lo que puede volver sin OV %s (FULL a la puerta %s + no FULL antes del corte %s)',
                       new.clave, v_dev, new.delta, v_vendido, v_full, v_previa);
  end if;
  return new;
end $$;
create trigger stock_mov_devolucion_sin_ov before insert on almacen.stock_mov
  for each row when (new.motivo = 'devolucion' and new.ov_linea_id is null)
  execute function almacen.tg_devolucion_sin_ov();

-- ───────────────────────────────────────────────────────────────────────────
-- C5c. [T0 §5.4] El SKU tiene que existir en core.products (filas NUEVAS de stock_almacen y stock_mov).
--      Trigger y no FK: no amarra core.products (ETL, renombres) a almacen. Las 4 ZZCONC viejas del sandbox no se rompen.
-- ───────────────────────────────────────────────────────────────────────────
create or replace function almacen.tg_sku_existe() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
begin
  if not exists (select 1 from core.products p where p.sku = new.sku) then
    raise exception using errcode = '23503', constraint = 'almacen_sku_existe',
      message = format('%s.%s: el SKU %s no existe en core.products', tg_table_schema, tg_table_name, new.sku);
  end if;
  return new;
end $$;
create trigger stock_almacen_sku_existe before insert on almacen.stock_almacen
  for each row execute function almacen.tg_sku_existe();
create trigger stock_mov_sku_existe before insert on almacen.stock_mov
  for each row execute function almacen.tg_sku_existe();

-- ───────────────────────────────────────────────────────────────────────────
-- C6. Las funciones de los traslados (solo pasan por el libro y la guarda) [S3]
-- ───────────────────────────────────────────────────────────────────────────
-- [R op] Firma opcional de la transacción (GUC almacen.firma = {"quien_nombre", "via", "nota"}): la pone asentar_diario.
create or replace function almacen.recibir_traslado(p_id bigint, p_sku citext, p_cantidad integer, p_ubicacion_id bigint default null)
returns bigint language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare t record; v_fis integer; v_n integer; v_mov bigint; v_ev bigint;
        v_quien text := nullif(current_setting('app.usuario', true), '');
        v_firma jsonb := nullif(current_setting('almacen.firma', true), '')::jsonb;
begin
  if v_quien is null then raise exception using errcode = '23514', constraint = 'traslado_quien', message = 'recibir exige app.usuario'; end if;
  set constraints all deferred;          -- libro, evento y cuadres van en sentencias separadas: se comprueban al COMMIT
  select * into t from almacen.traslados where id = p_id;
  insert into almacen.stock_almacen (sku, almacen, fisico) values (p_sku, t.destino, 0) on conflict (sku, almacen) do nothing;
  select fisico into v_fis from almacen.stock_almacen where sku = p_sku and almacen = t.destino for update;
  select count(*) + 1 into v_n from almacen.traslado_eventos where traslado_id = p_id and sku = p_sku and evento = 'entrada';
  insert into almacen.stock_mov (sku, almacen, ubicacion_id, delta, saldo_despues, motivo, ref, clave, quien, quien_nombre, via, nota)
  values (p_sku, t.destino, p_ubicacion_id, p_cantidad, v_fis + p_cantidad, 'traspaso_entrada', 'TRA-' || p_id,
          format('tras:%s:%s:%s:entra', p_id, p_sku, v_n), v_quien,
          v_firma ->> 'quien_nombre', v_firma ->> 'via', v_firma ->> 'nota') returning id into v_mov;
  update almacen.stock_almacen set fisico = fisico + p_cantidad where sku = p_sku and almacen = t.destino;
  perform set_config('app.almacen_escritor', 'libro:' || txid_current(), true);
  insert into almacen.traslado_eventos (traslado_id, sku, evento, sistema, cantidad, stock_mov_id, quien)
  values (p_id, p_sku, 'entrada', 'kubera', p_cantidad, v_mov, v_quien) returning id into v_ev;
  perform set_config('app.almacen_escritor', '', true);
  return v_ev;
end $$;
create or replace function almacen.despachar_traslado(p_id bigint, p_sku citext, p_cantidad integer, p_ubicacion_id bigint default null)
returns bigint language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare t record; sa record; v_n integer; v_mov bigint; v_ev bigint;
        v_quien text := nullif(current_setting('app.usuario', true), '');
        v_firma jsonb := nullif(current_setting('almacen.firma', true), '')::jsonb;
begin
  if v_quien is null then raise exception using errcode = '23514', constraint = 'traslado_quien', message = 'despachar exige app.usuario'; end if;
  set constraints all deferred;
  select * into t from almacen.traslados where id = p_id;
  select * into sa from almacen.stock_almacen where sku = p_sku and almacen = t.origen for update;
  if sa.sku is null or sa.fisico - sa.apartado < p_cantidad then
    raise exception using errcode = '23514', constraint = 'traslado_sin_libre',
      message = format('TRA-%s: %s no tiene %s libres en %s', p_id, p_sku, p_cantidad, t.origen);
  end if;
  select count(*) + 1 into v_n from almacen.traslado_eventos where traslado_id = p_id and sku = p_sku and evento = 'salida';
  insert into almacen.stock_mov (sku, almacen, ubicacion_id, delta, saldo_despues, motivo, ref, clave, quien, quien_nombre, via, nota)
  values (p_sku, t.origen, p_ubicacion_id, -p_cantidad, sa.fisico - p_cantidad, 'traspaso_salida', 'TRA-' || p_id,
          format('tras:%s:%s:%s:sale', p_id, p_sku, v_n), v_quien,
          v_firma ->> 'quien_nombre', v_firma ->> 'via', v_firma ->> 'nota') returning id into v_mov;
  update almacen.stock_almacen set fisico = fisico - p_cantidad where sku = p_sku and almacen = t.origen;
  perform set_config('app.almacen_escritor', 'libro:' || txid_current(), true);
  insert into almacen.traslado_eventos (traslado_id, sku, evento, sistema, cantidad, stock_mov_id, quien)
  values (p_id, p_sku, 'salida', 'kubera', p_cantidad, v_mov, v_quien) returning id into v_ev;
  perform set_config('app.almacen_escritor', '', true);
  return v_ev;
end $$;
create or replace function almacen.cerrar_diferencia(p_id bigint, p_sku citext, p_cantidad integer, p_nota text)
returns bigint language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare v_ev bigint;
begin
  insert into almacen.traslado_eventos (traslado_id, sku, evento, sistema, cantidad, nota)
  values (p_id, p_sku, 'diferencia', 'kubera', p_cantidad, p_nota) returning id into v_ev;
  return v_ev;
end $$;
create or replace function almacen.cuadrar_traslado(p_id bigint)
returns void language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare v_quien text := nullif(current_setting('app.usuario', true), '');
begin
  if v_quien is null then raise exception using errcode = '23514', constraint = 'traslado_quien', message = 'cuadrar exige app.usuario'; end if;
  if exists (select 1 from almacen.traslado_lineas where traslado_id = p_id and (en_transito <> 0 or salido = 0)) then
    raise exception using errcode = '23514', constraint = 'traslado_no_cuadra',
      message = format('TRA-%s: hay líneas en tránsito o sin salir', p_id);
  end if;
  perform set_config('app.almacen_escritor', 'traslado:' || txid_current(), true);
  update almacen.traslados set estado = 'cuadrado', cuadrado_at = now(), cuadrado_por = v_quien where id = p_id;
  perform set_config('app.almacen_escritor', '', true);
end $$;
create or replace function almacen.cancelar_traslado(p_id bigint, p_motivo text)
returns void language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare v_quien text := nullif(current_setting('app.usuario', true), '');
begin
  if v_quien is null then raise exception using errcode = '23514', constraint = 'traslado_quien', message = 'cancelar exige app.usuario'; end if;
  if exists (select 1 from almacen.traslado_lineas where traslado_id = p_id and salido <> 0) then
    raise exception using errcode = '23514', constraint = 'traslado_ya_salio',
      message = format('TRA-%s: algo ya salió; se regresa con un traslado inverso (D24)', p_id);
  end if;
  perform set_config('app.almacen_escritor', 'traslado:' || txid_current(), true);
  update almacen.traslados set estado = 'cancelado', cancelado_at = now(), cancelado_por = v_quien, cancelado_motivo = p_motivo
   where id = p_id;
  perform set_config('app.almacen_escritor', '', true);
end $$;

-- ───────────────────────────────────────────────────────────────────────────
-- C7. Vistas: el tránsito en las existencias (nunca vendible), traslados_v y la vigía ampliada.
--     security_invoker = on EXPLÍCITO en cada create or replace (sin él, se pierde: prueba 17 de seguridad). [S8]
-- ───────────────────────────────────────────────────────────────────────────
-- [T0 §5.6] existencias_bodega_v = la de la B′ (por_verificar y el arreglo del SKU en 0) MÁS el tránsito de la C_0076.
--           Nombre, columnas, tipos y orden SIN cambio (contrato con WOO SALIDA: stock_libro suma libre_vendible).
create or replace view almacen.existencias_bodega_v with (security_invoker = on) as
with g as (
  select e.sku, e.almacen, e.fuente,
         case when e.tipo in ('cuarentena', 'scrap', 'por_verificar') then e.tipo else 'bodega' end as grupo,
         sum(e.fisico)                                      as fisico,
         coalesce(sum(e.fisico) filter (where e.cuenta_para_venta), 0) as fisico_vendible,
         sum(e.reservado_ubicacion)                         as reservado_odoo,
         min(e.copia_at)                                    as copia_at
    from almacen.existencias_ubicacion_v e
   group by 1, 2, 3, 4
), r as (
  select g.*,
         case when g.fuente = 'kubera' and g.grupo = 'bodega' then coalesce(sa.apartado, 0)
              else g.reservado_odoo end as reservado
    from g left join almacen.stock_almacen sa on sa.sku = g.sku and sa.almacen = g.almacen
)
select sku, almacen, fuente, grupo, fisico, fisico_vendible, reservado,
       fisico - reservado          as libre,
       fisico_vendible - reservado as libre_vendible,
       copia_at
  from r
union all
-- Arreglo (N88): un SKU con fila en stock_almacen y SIN renglones kubera en locations aparece con 0 (y su apartado).
select sa.sku, sa.almacen, 'kubera'::text, 'bodega'::text, 0::bigint, 0::bigint, sa.apartado::bigint,
       (-sa.apartado)::bigint, (-sa.apartado)::bigint, null::timestamptz
  from almacen.stock_almacen sa
 where not exists (select 1 from almacen.locations l where l.cedis = sa.almacen and l.sku = sa.sku and l.origen = 'kubera')
union all
-- Tránsito (C_0076, sin cambio): nunca vendible.
select tl.sku, t.destino, a.fuente, 'transito',
       sum(tl.en_transito)::bigint, 0::bigint, 0::bigint, sum(tl.en_transito)::bigint, 0::bigint, null::timestamptz
  from almacen.traslado_lineas tl
  join almacen.traslados t on t.id = tl.traslado_id
  join almacen.almacenes a on a.codigo = t.destino
 where tl.en_transito <> 0
 group by tl.sku, t.destino, a.fuente;

create or replace view almacen.existencias_sku_v with (security_invoker = on) as
select b.sku,
       coalesce(sum(b.fisico) filter (where b.grupo <> 'scrap'), 0)      as total,
       coalesce(sum(b.fisico) filter (where b.grupo = 'cuarentena'), 0)  as cuarentena,
       coalesce(sum(b.fisico) filter (where b.grupo = 'scrap'), 0)       as scrap,
       sum(b.fisico_vendible)                                            as fisico_vendible,
       sum(b.reservado)                                                  as reservado,
       sum(b.libre_vendible)                                             as libre_vendible,
       coalesce(sum(b.fisico) filter (where b.grupo <> 'scrap'), 0)::text || ' = ' ||
         coalesce(string_agg(b.fisico::text || ' ' || b.almacen
                               || case when b.grupo = 'bodega' then '' else '·' || b.grupo end,
                             ' + ' order by (b.grupo <> 'bodega'), b.fisico desc)
                    filter (where b.grupo <> 'scrap'), '0')              as resumen,
       jsonb_agg(jsonb_build_object('almacen', b.almacen, 'grupo', b.grupo, 'fuente', b.fuente,
                   'fisico', b.fisico, 'reservado', b.reservado, 'libre', b.libre,
                   'libre_vendible', b.libre_vendible, 'copia_at', b.copia_at)
                 order by b.almacen, b.grupo)                            as desglose,
       min(b.copia_at)                                                   as copia_mas_vieja_at,
       array_agg(distinct b.fuente)                                      as fuentes,
       coalesce(sum(b.fisico) filter (where b.grupo = 'por_verificar'), 0) as por_verificar,   -- B′ (T0)
       coalesce(sum(b.fisico) filter (where b.grupo = 'transito'), 0)    as transito         -- nueva, al final (D18)
  from almacen.existencias_bodega_v b
 group by b.sku;

create or replace view almacen.traslados_v with (security_invoker = on) as
select 'TRA-' || t.id as folio, t.id, t.origen, t.destino, t.estado, l.sku, l.solicitado, l.salido, l.recibido,
       l.diferencia, l.en_transito, t.solicitado_at, t.solicitado_por,
       (select string_agg(distinct e.odoo_picking_ref, ', ') from almacen.traslado_eventos e
         where e.traslado_id = t.id and e.sku = l.sku and e.sistema = 'odoo') as pickings_odoo,
       (select bool_or(e.anomalia is not null) from almacen.traslado_eventos e
         where e.traslado_id = t.id and e.sku = l.sku) as con_anomalia
  from almacen.traslados t join almacen.traslado_lineas l on l.traslado_id = t.id;

create or replace view almacen.vigia_traslados_v with (security_invoker = on) as
select 'transito_viejo'::text as problema, t.destino as donde,
       format('TRA-%s %s: %s en tránsito desde %s', t.id, l.sku, l.en_transito, t.solicitado_at) as detalle
  from almacen.traslado_lineas l join almacen.traslados t on t.id = l.traslado_id
 where l.en_transito > 0 and t.actualizado_at < now() - interval '3 days'
union all
select 'entrada_odoo_antes_de_salir', t.destino, format('TRA-%s %s: evento %s (doble conteo en curso)', t.id, e.sku, e.id)
  from almacen.traslado_eventos e join almacen.traslados t on t.id = e.traslado_id
 where e.anomalia is not null;

create or replace view almacen.fuente_unica_vigia_v with (security_invoker = on) as
select * from almacen.vigia_fuente_v
union all
select * from almacen.vigia_traslados_v
union all
-- [WOO SALIDA] una bodega del libro que cuenta para Woo sin existencias cargadas deja el libro entero NO disponible
-- (stock_libro falla cerrado) o, peor, manda sus SKUs a 0: la bandera solo se enciende con la bodega ya sembrada.
select 'bodega_woo_sin_existencias'::text, a.codigo, format('%s cuenta para Woo (cuenta_para_woo) y no trae ninguna fila en stock_almacen', a.codigo)
  from almacen.almacenes a
 where a.fuente = 'kubera' and a.cuenta_para_woo
   and not exists (select 1 from almacen.stock_almacen sa where sa.almacen = a.codigo);

-- [WOO SALIDA] El mismo candado al COMMIT: encender cuenta_para_woo (o volver kubera una bodega que ya la tiene) exige
-- que la bodega traiga existencias. Diferido: el desprendimiento enciende la bandera y siembra en la misma transacción.
create or replace function almacen.tg_almacenes_woo_existencias() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
begin
  -- al COMMIT se lee la fila como quedó (no como la dejó esta sentencia)
  if exists (select 1 from almacen.almacenes a where a.codigo = new.codigo and a.fuente = 'kubera' and a.cuenta_para_woo)
     and not exists (select 1 from almacen.stock_almacen sa where sa.almacen = new.codigo) then
    raise exception using errcode = '23514', constraint = 'almacenes_woo_sin_existencias',
      message = format('%s cuenta para Woo y no trae existencias en el libro: la bandera se enciende con la bodega ya sembrada '
                       || '(si no, stock_libro deja el libro no disponible o manda sus SKUs a 0)', new.codigo);
  end if;
  return null;
end $$;
create constraint trigger almacenes_woo_existencias after insert or update of cuenta_para_woo, fuente on almacen.almacenes
  deferrable initially deferred for each row execute function almacen.tg_almacenes_woo_existencias();

-- ───────────────────────────────────────────────────────────────────────────
-- C7b. [T0 §6, D9] La plantilla de movimientos que NO son OV: almacen.diario → stock_mov.
--      Se llena desde la hoja CSV firmada (la pantalla de captura viene después) y la asienta
--      MAESTRO con asentar_diario(ids), autorizado una vez en el acta de T0, con reporte diario.
--      Ninguna venta entra como corrección ni como ajuste_conteo.
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists almacen.diario (
  id                   bigint      generated always as identity,
  lote                 text        not null,
  hoja_sha256          text        not null,
  renglon              integer     not null,
  ocurrido_at          timestamptz not null,                 -- fecha_hora_cdmx convertida (America/Mexico_City)
  almacen              text        not null,
  ubicacion_id         bigint,                               -- NULL = «SIN UBICAR» (D15)
  sku                  citext      not null,
  piezas               integer     not null,                 -- con signo: − sale, + entra
  tipo                 text        not null,
  canal                text,
  cuenta               text,
  referencia           text        not null,
  entrega_odoo         text,                                 -- S<n> o 'ninguna'
  woo_restado          integer,
  destino_almacen      text,
  destino_ubicacion_id bigint,
  quien                text        not null,                 -- quién lo hizo en el almacén (nunca un comprador)
  nota                 text,
  capturado_por        text        not null default nullif(current_setting('app.usuario', true), ''),
  capturado_at         timestamptz not null default now(),
  estado               text        not null default 'pendiente',
  asentado_ref         text,                                 -- clave del stock_mov, folio de OV o TRA-<n> (una partida: NULL)
  asentado_at          timestamptz,
  asentado_por         text,
  motivo_rechazo       text,
  constraint diario_pkey           primary key (id),
  constraint diario_renglon_uq     unique (hoja_sha256, renglon),
  constraint diario_lote_chk       check (lote ~ '^(ARRANQUE-[0-9]{8}|DIA-[0-9]{8})(-[a-z0-9]+)?$'),
  constraint diario_sha_chk        check (hoja_sha256 ~ '^[0-9a-f]{64}$'),
  constraint diario_renglon_chk    check (renglon > 0),
  constraint diario_almacen_fk     foreign key (almacen) references almacen.almacenes (codigo),
  constraint diario_ubicacion_fk   foreign key (ubicacion_id, almacen) references almacen.ubicaciones (id, almacen),
  constraint diario_destino_fk     foreign key (destino_almacen) references almacen.almacenes (codigo),
  constraint diario_destino_u_fk   foreign key (destino_ubicacion_id) references almacen.ubicaciones (id),
  constraint diario_piezas_chk     check (piezas <> 0),
  constraint diario_tipo_chk       check (tipo in ('recepcion', 'merma', 'muestra', 'traslado', 'paso_cuarentena', 'ajuste_conteo',
                                                   'correccion', 'devolucion_sin_ov', 'retiro_full', 'salida_shein', 'salida_mayoreo',
                                                   'envio_full', 'venta_enviada', 'cancelacion_repuso_woo', 'cancelacion_temu',
                                                   'devolucion_ov', 'devolucion_sin_venta')),            -- [H2] [H12]
  constraint diario_referencia_chk check (length(btrim(referencia)) >= 3),
  constraint diario_entrega_chk    check ((tipo not in ('envio_full', 'salida_mayoreo', 'salida_shein') or entrega_odoo is not null)
                                          and (entrega_odoo is null or entrega_odoo ~ '^(S[0-9]+|ninguna)$')),
  constraint diario_quien_chk      check (length(btrim(quien)) >= 2),
  constraint diario_estado_chk     check (estado in ('pendiente', 'asentado', 'partida', 'rechazado')),
  constraint diario_asentado_chk   check ((estado in ('asentado', 'partida')) = (asentado_at is not null)
                                          and (asentado_at is null) = (asentado_por is null)
                                          and (estado <> 'asentado' or asentado_ref is not null)),
  constraint diario_rechazo_chk    check ((estado = 'rechazado') = (motivo_rechazo is not null)
                                          and (motivo_rechazo is null or length(btrim(motivo_rechazo)) >= 10))
);
create index if not exists diario_pendiente_ix on almacen.diario (capturado_at) where estado = 'pendiente';
create index if not exists diario_ocurrido_ix  on almacen.diario (ocurrido_at);

-- [H2] El espacio de la N de dev:<N>: es almacen.diario.id (devolucion_ov). La identidad arranca DESPUÉS de la N más alta
-- que ya use una clave dev: del libro (las de ops.devoluciones, que la 0072 retiró), así nunca chocan con
-- stock_mov_clave_uq. Un documento futuro de devoluciones (F1) usa estos mismos ids o su propia forma de clave.
do $$
declare v_max bigint;
begin
  select max((regexp_match(m.clave, '^dev:([0-9]+):'))[1]::bigint) into v_max from almacen.stock_mov m where m.clave ~ '^dev:[0-9]+:';
  if v_max is not null and not exists (select 1 from almacen.diario) then
    execute format('alter table almacen.diario alter column id restart with %s', v_max + 1);
  end if;
end $$;

-- Guarda diario_solo_agregar: nace pendiente; sin DELETE ni TRUNCATE; UPDATE solo con 'diario:<txid>',
-- solo de pendiente a asentado | partida | rechazado y solo en estado, asentado_* y motivo_rechazo.
create or replace function almacen.tg_diario_guarda() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_cols constant text[] := array['estado', 'asentado_ref', 'asentado_at', 'asentado_por', 'motivo_rechazo'];
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', constraint = 'diario_solo_agregar', message = 'almacen.diario no se borra';
  end if;
  if tg_op = 'INSERT' then
    if new.estado <> 'pendiente' or new.asentado_at is not null or new.asentado_ref is not null or new.motivo_rechazo is not null then
      raise exception using errcode = '23514', constraint = 'diario_alta_chk', message = 'un renglón del diario nace pendiente';
    end if;
    if new.capturado_por is null then
      raise exception using errcode = '23514', constraint = 'diario_quien', message = 'el diario exige app.usuario (capturado_por)';
    end if;
    return new;
  end if;
  if coalesce(current_setting('app.almacen_escritor', true), '') <> 'diario:' || txid_current()
     or old.estado <> 'pendiente' or new.estado not in ('asentado', 'partida', 'rechazado')
     or (to_jsonb(new) - v_cols) is distinct from (to_jsonb(old) - v_cols) then
    raise exception using errcode = '42501', constraint = 'diario_solo_agregar',
      message = format('diario #%s: solo asentar_diario/rechazar_diario lo pasan de pendiente a asentado, partida o rechazado', old.id);
  end if;
  return new;
end $$;
create trigger diario_guarda before insert or update or delete on almacen.diario
  for each row execute function almacen.tg_diario_guarda();
create trigger diario_sin_truncate before truncate on almacen.diario
  for each statement execute function ops.tg_solo_agregar();          -- TRUNCATE también da diario_solo_agregar

-- Un movimiento del libro desde un renglón del diario: quién asienta (app.usuario), quién lo hizo (quien_nombre),
-- vía 'diario', nota con lote#renglón y la ubicación del renglón. p_ov_linea solo en una devolución por la OV (dev:).
create or replace function almacen.diario_mov(p_id bigint, p_motivo text, p_delta integer, p_clave text, p_ref text, p_ubic bigint,
                                              p_ov_linea bigint default null)
returns bigint language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare r_d record; v_fis integer; v_mov bigint;
begin
  select * into r_d from almacen.diario x where x.id = p_id;
  insert into almacen.stock_almacen (sku, almacen, fisico) values (r_d.sku, r_d.almacen, 0) on conflict (sku, almacen) do nothing;
  select sa.fisico into v_fis from almacen.stock_almacen sa where sa.sku = r_d.sku and sa.almacen = r_d.almacen for update;
  insert into almacen.stock_mov (sku, almacen, ubicacion_id, delta, saldo_despues, motivo, ref, clave, quien, quien_nombre, via, nota,
                                 ov_linea_id)
  values (r_d.sku, r_d.almacen, p_ubic, p_delta, v_fis + p_delta, p_motivo, p_ref, p_clave,
          current_setting('app.usuario'), r_d.quien, 'diario', format('diario %s#%s: %s', r_d.lote, r_d.renglon, coalesce(r_d.nota, '')),
          p_ov_linea)
  returning id into v_mov;
  update almacen.stock_almacen sa set fisico = sa.fisico + p_delta where sa.sku = r_d.sku and sa.almacen = r_d.almacen;
  return v_mov;
end $$;

-- La salida por OV de un grupo de renglones (misma hoja, tipo, referencia, bodega, entrega, canal y cuenta):
-- después de T0, Shein y mayoreo solo se VERIFICAN contra la OV manual del panel (no se escribe en ventas);
-- con S<n> se entrega la espejo de esa S; venta_enviada, su espejo t0:<canal>:<CUENTA>:<orden>;
-- en el arranque sin S, una OV espejo diario:<id> (Shein/Mayoreo) o tipo full (FULL, D7).
create or replace function almacen.diario_ov(p_id bigint) returns text
language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare
  r_d record; r_ov record; v_res jsonb; v_lin jsonb; v_ubi jsonb := '{}'::jsonb; v_dif integer; v_min bigint;
  v_canal text; v_tienda text;
begin
  select * into r_d from almacen.diario x where x.id = p_id;
  if to_regclass('pg_temp._diario_grupo') is null then
    create temp table _diario_grupo (id bigint, sku citext, piezas integer, ubicacion_id bigint) on commit drop;
  end if;
  truncate _diario_grupo;
  insert into _diario_grupo
  select x.id, x.sku, -x.piezas, x.ubicacion_id from almacen.diario x
   where x.hoja_sha256 = r_d.hoja_sha256 and x.tipo = r_d.tipo and x.referencia = r_d.referencia and x.almacen = r_d.almacen
     and x.entrega_odoo is not distinct from r_d.entrega_odoo and x.canal is not distinct from r_d.canal
     and x.cuenta is not distinct from r_d.cuenta;
  select min(g.id) into v_min from _diario_grupo g;
  -- 1) ¿qué OV?
  if r_d.tipo in ('salida_shein', 'salida_mayoreo') and r_d.lote like 'DIA-%' then
    select o.* into r_ov from ventas.ov_ordenes o where o.folio = r_d.referencia and o.borrada_at is null;
  elsif r_d.entrega_odoo ~ '^S[0-9]+$' then
    -- La espejo de una S de mayoreo/FULL nace t0:S:<n>; la de una venta de MARKETPLACE nace con su trío mp_*
    -- (t0:<canal>:<CUENTA>:<orden>, 0076 t0_ov_espejos). Se busca por las dos: la clave t0:S:, el trío del renglón
    -- (venta_enviada lleva canal, cuenta y la orden en referencia) y el trío que t0_migradas guardó para esa S.
    select o.* into r_ov from ventas.ov_ordenes o
     where o.borrada_at is null and o.estado <> 'cancelada'
       and (o.clave in ('t0:S:' || r_d.entrega_odoo, 't0:S:' || substr(r_d.entrega_odoo, 2))
            or (r_d.tipo = 'venta_enviada' and o.mp_canal = r_d.canal and o.mp_cuenta = r_d.cuenta and o.mp_orden = r_d.referencia)
            or exists (select 1 from almacen.t0_migradas m
                        where m.odoo_name in (r_d.entrega_odoo, substr(r_d.entrega_odoo, 2))
                          and o.mp_canal = m.canal and o.mp_cuenta = upper(btrim(m.cuenta)) and o.mp_orden = m.external_order_id))
     order by o.id limit 1;
    if r_ov.id is null then
      raise exception using errcode = '23503', constraint = 'diario_s_sin_espejo',
        message = format('%s: no hay OV espejo de %s (L4 la debió declarar viva)', r_d.tipo, r_d.entrega_odoo);
    end if;
  elsif r_d.tipo = 'venta_enviada' then
    select o.* into r_ov from ventas.ov_ordenes o
     where o.mp_canal = r_d.canal and o.mp_cuenta = r_d.cuenta and o.mp_orden = r_d.referencia and o.borrada_at is null and o.estado <> 'cancelada';
  else
    select o.* into r_ov from ventas.ov_ordenes o where o.clave = 'diario:' || v_min and o.borrada_at is null and o.estado <> 'cancelada';
  end if;
  -- 2) si no existe y le toca crearla (arranque, sin S)
  if r_ov.id is null then
    if r_d.tipo in ('salida_shein', 'salida_mayoreo') and r_d.lote like 'DIA-%' then
      raise exception using errcode = '23503', constraint = 'diario_ov_manual_no_existe',
        message = format('%s: la OV manual %s no existe (después de T0 la captura el panel)', r_d.tipo, r_d.referencia);
    end if;
    select coalesce(jsonb_agg(jsonb_build_object('linea', z.rn, 'sku', z.sku, 'cantidad', z.piezas, 'almacen', r_d.almacen) order by z.rn), '[]'::jsonb)
      into v_lin
      from (select row_number() over (order by y.primero) as rn, y.sku, y.piezas
              from (select g.sku, min(g.id) as primero, sum(g.piezas) as piezas from _diario_grupo g group by g.sku) y) z;
    if r_d.tipo = 'envio_full' then
      v_canal  := case r_d.cuenta when 'AMAZON' then 'amazon' when 'WALMART' then 'walmart' else 'mercado_libre' end;
      v_tienda := case r_d.cuenta when 'BEKURA' then 'meli:Kubera' when 'SANCORFASHION' then 'meli:San Corpe'
                                  when 'AMAZON' then 'amazon' when 'WALMART' then 'walmart' end;
      v_res := almacen.ov_espejo(jsonb_build_object('clave', 'diario:' || v_min, 'tipo', 'full', 'canal', v_canal,
                 'full_tienda', v_tienda, 'envio_ref', r_d.referencia, 'cliente', 'FULL ' || coalesce(r_d.cuenta, ''),
                 'descripcion', format('envío a FULL %s (diario %s)', r_d.referencia, r_d.lote)), v_lin);
    elsif r_d.tipo = 'venta_enviada' then
      v_res := almacen.ov_espejo(jsonb_build_object('clave', format('t0:%s:%s:%s', r_d.canal, r_d.cuenta, r_d.referencia),
                 'tipo', 'venta',
                 'canal', case when r_d.canal in ('temu', 'tiktok', 'mercado_libre', 'amazon', 'walmart', 'shein') then r_d.canal else 'otro' end,
                 'mp_canal', r_d.canal, 'mp_cuenta', r_d.cuenta, 'mp_orden', r_d.referencia), v_lin);
    else
      v_res := almacen.ov_espejo(jsonb_build_object('clave', 'diario:' || v_min, 'tipo', 'venta',
                 'canal', case r_d.tipo when 'salida_shein' then 'shein' else 'otro' end,
                 'cliente', case r_d.tipo when 'salida_shein' then 'Shein' else 'Mayoreo' end,
                 'descripcion', format('%s %s (diario %s)', r_d.tipo, r_d.referencia, r_d.lote)), v_lin);
    end if;
    select o.* into r_ov from ventas.ov_ordenes o where o.id = (v_res ->> 'id')::bigint;
  end if;
  -- 3) los renglones del grupo son las líneas de la OV: mismo SKU, cantidad y bodega
  select count(*) into v_dif from (
    (select g.sku, sum(g.piezas) as n, r_d.almacen as alm from _diario_grupo g group by g.sku
     except
     select l.sku, sum(l.cantidad), l.almacen from ventas.ov_lineas l where l.orden_id = r_ov.id group by l.sku, l.almacen)
    union all
    (select l.sku, sum(l.cantidad), l.almacen from ventas.ov_lineas l where l.orden_id = r_ov.id group by l.sku, l.almacen
     except
     select g.sku, sum(g.piezas) as n, r_d.almacen as alm from _diario_grupo g group by g.sku)) z;
  if v_dif > 0 then
    raise exception using errcode = '23514', constraint = 'diario_ov_no_cuadra',
      message = format('%s %s: los renglones de la hoja no son las líneas de %s (SKU, cantidad y bodega)', r_d.tipo, r_d.referencia, r_ov.folio);
  end if;
  -- 4) entrega: después de T0 (Shein/mayoreo) solo se verifica; si no, se entrega una vez (el primer renglón del grupo)
  if r_ov.estado = 'confirmada' then
    if r_d.tipo in ('salida_shein', 'salida_mayoreo') and r_d.lote like 'DIA-%' then
      raise exception using errcode = '23514', constraint = 'diario_ov_manual_sin_entregar',
        message = format('%s: la OV %s está confirmada; la entrega la registra el panel, no el diario', r_d.tipo, r_ov.folio);
    end if;
    select coalesce(jsonb_object_agg(l.id::text, g.ubicacion_id) filter (where g.ubicacion_id is not null), '{}'::jsonb) into v_ubi
      from ventas.ov_lineas l
      join _diario_grupo g on g.sku = l.sku and g.id = (select min(g2.id) from _diario_grupo g2 where g2.sku = l.sku)
     where l.orden_id = r_ov.id;
    v_ubi := v_ubi || jsonb_build_object('_firma', jsonb_build_object('quien_nombre', r_d.quien, 'via', 'diario',
                        'nota', format('diario %s#%s: %s', r_d.lote, r_d.renglon, coalesce(r_d.nota, ''))));
    perform almacen.ov_entregar(r_ov.id, v_ubi);
  elsif r_ov.estado <> 'entregada' then
    raise exception using errcode = '23514', constraint = 'diario_ov_estado',
      message = format('%s: la OV %s está %s', r_d.tipo, r_ov.folio, r_ov.estado);
  end if;
  return r_ov.folio;
end $$;

-- Asienta en orden de id y FALLA CERRADO: si un renglón no se puede asentar, aborta todo con su id en el mensaje
-- (conserva el SQLSTATE y el nombre de la constraint). Los ya asentados o partida se saltan (ya_asentado).
create or replace function almacen.asentar_diario(p_ids bigint[]) returns jsonb
language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare
  v_quien text := nullif(current_setting('app.usuario', true), '');
  r_d record; v_ref text; v_estado text; v_n bigint; v_tra bigint; v_lin bigint; v_res jsonb := '[]'::jsonb;
  v_state text; v_con text; v_msg text; v_asent integer := 0; v_ya integer := 0;
begin
  if v_quien is null then
    raise exception using errcode = '23514', constraint = 'asentar_diario_quien', message = 'asentar_diario exige app.usuario';
  end if;
  set constraints all deferred;
  for r_d in select * from almacen.diario x where x.id = any(p_ids) order by x.id for update loop
    if r_d.estado in ('asentado', 'partida') then
      v_ya := v_ya + 1;
      v_res := v_res || jsonb_build_array(jsonb_build_object('id', r_d.id, 'resultado', 'ya_asentado', 'ref', r_d.asentado_ref));
      continue;
    end if;
    begin
      if r_d.estado = 'rechazado' then
        raise exception using errcode = '23514', constraint = 'asentar_diario_rechazado', message = 'el renglón está rechazado';
      end if;
      if r_d.tipo not in ('cancelacion_repuso_woo', 'cancelacion_temu')
         and not exists (select 1 from almacen.almacenes a where a.codigo = r_d.almacen and a.fuente = 'kubera') then
        raise exception using errcode = '23514', constraint = 'diario_bodega_no_kubera',
          message = format('%s no es una bodega del libro (fuente kubera)', r_d.almacen);
      end if;
      v_estado := 'asentado'; v_ref := null;
      if r_d.tipo in ('merma', 'muestra', 'ajuste_conteo', 'correccion', 'devolucion_sin_venta') and length(btrim(coalesce(r_d.nota, ''))) < 5 then
        raise exception using errcode = '23514', constraint = 'diario_nota', message = r_d.tipo || ' exige nota';
      end if;
      -- [H7/H12] una devolución no entra como recepción ni como corrección (no tendría tope ni evidencia):
      -- va como devolucion_ov, devolucion_sin_ov o, sin venta conocida, devolucion_sin_venta (no vendible).
      if r_d.tipo in ('recepcion', 'correccion')
         and (r_d.referencia ~ '^((ret|ord|dev|devret|devord|devsv):|DEV-[0-9]+$)'
              or exists (select 1 from channel.returns r
                          where r.external_return_id = r_d.referencia or r.external_claim_id = r_d.referencia)) then
        raise exception using errcode = '23514', constraint = 'diario_devolucion_no_es_' || r_d.tipo,
          message = format('%s: la referencia %s es una devolución: va como devolucion_ov, devolucion_sin_ov o devolucion_sin_venta',
                           r_d.tipo, r_d.referencia);
      end if;
      case r_d.tipo
      when 'recepcion' then
        if r_d.piezas <= 0 or r_d.referencia !~ '^[A-Za-z0-9._-]+$' then
          raise exception using errcode = '23514', constraint = 'diario_forma', message = 'recepcion: piezas > 0 y referencia [A-Za-z0-9._-]+';
        end if;
        if exists (select 1 from channel.orders o where o.external_order_id = r_d.referencia or o.wc_order_id::text = r_d.referencia) then
          raise exception using errcode = '23514', constraint = 'diario_devolucion_no_es_recepcion',
            message = format('recepcion: la referencia %s es una venta; su devolución va como devolucion_ov o devolucion_sin_ov', r_d.referencia);
        end if;
        select count(*) into v_n from almacen.diario x
         where x.tipo = 'recepcion' and x.referencia = r_d.referencia and x.sku = r_d.sku and x.id <= r_d.id;
        v_ref := format('rec:%s:%s:%s', r_d.referencia, upper(r_d.sku::text), v_n);
        perform almacen.diario_mov(r_d.id, 'entrada', r_d.piezas, v_ref, r_d.referencia, r_d.ubicacion_id);
      when 'merma', 'muestra' then
        v_ref := case r_d.tipo when 'merma' then format('merma:dia:%s', r_d.id) else format('merma:muestra:dia:%s', r_d.id) end;
        perform almacen.diario_mov(r_d.id, 'merma', r_d.piezas, v_ref, r_d.referencia, r_d.ubicacion_id);
      when 'correccion' then
        if exists (select 1 from channel.orders o where o.external_order_id = r_d.referencia or o.wc_order_id::text = r_d.referencia)
           or exists (select 1 from ventas.ov_ordenes o where o.folio = r_d.referencia) then
          raise exception using errcode = '23514', constraint = 'diario_venta_no_es_correccion',
            message = format('la referencia %s es una venta: va como OV espejo, nunca como corrección', r_d.referencia);
        end if;
        v_ref := format('corr:dia:%s', r_d.id);
        perform almacen.diario_mov(r_d.id, 'correccion', r_d.piezas, v_ref, r_d.referencia, r_d.ubicacion_id);
      when 'ajuste_conteo' then
        if r_d.ubicacion_id is null then
          raise exception using errcode = '23514', constraint = 'diario_forma', message = 'ajuste_conteo exige ubicacion_id';
        end if;
        v_ref := format('conteo:%s:%s:%s', r_d.referencia, r_d.ubicacion_id, upper(r_d.sku::text));
        perform almacen.diario_mov(r_d.id, 'ajuste_conteo', r_d.piezas, v_ref, r_d.referencia, r_d.ubicacion_id);
      when 'devolucion_sin_ov' then
        if r_d.canal is null or r_d.cuenta is null or r_d.piezas <= 0 or r_d.referencia !~ '^(ret|ord):[^:]+$' then
          raise exception using errcode = '23514', constraint = 'diario_forma',
            message = 'devolucion_sin_ov: canal, cuenta, piezas > 0 y referencia ret:<id> u ord:<id>';
        end if;
        -- <n> = orden del renglón entre los del diario con la misma devolución (o venta) y SKU [H3: también devret]
        select count(*) into v_n from almacen.diario x where x.tipo = 'devolucion_sin_ov' and x.canal = r_d.canal
           and x.cuenta = r_d.cuenta and x.referencia = r_d.referencia and x.sku = r_d.sku and x.id <= r_d.id;
        v_ref := format('%s:%s:%s:%s:%s:%s', case when r_d.referencia like 'ret:%' then 'devret' else 'devord' end,
                        r_d.canal, r_d.cuenta, substr(r_d.referencia, 5), upper(r_d.sku::text), v_n);
        perform almacen.diario_mov(r_d.id, 'devolucion', r_d.piezas, v_ref, 'DEV-' || r_d.id, r_d.ubicacion_id);
      when 'devolucion_ov' then
        -- [H2] la venta tiene OV de kubera (una espejo o la del panel): la devolución va por su renglón (dev:<id>:recibe).
        -- referencia = la orden del marketplace (mp_orden); el renglón se resuelve por el trío mp_* y el SKU.
        if r_d.canal is null or r_d.cuenta is null or r_d.piezas <= 0 then
          raise exception using errcode = '23514', constraint = 'diario_forma',
            message = 'devolucion_ov: canal, cuenta, piezas > 0 y referencia = la orden del marketplace';
        end if;
        select l.id into v_lin
          from ventas.ov_ordenes o join ventas.ov_lineas l on l.orden_id = o.id
         where o.mp_canal = r_d.canal and o.mp_cuenta = r_d.cuenta and o.mp_orden = r_d.referencia and o.borrada_at is null
           and o.estado in ('entregada', 'entregada_cancelada') and l.sku = r_d.sku
           and coalesce(l.entregado, 0) - (select coalesce(sum(m.delta), 0) from almacen.stock_mov m
                                            where m.motivo = 'devolucion' and m.ov_linea_id = l.id) >= r_d.piezas
         order by o.id, l.linea limit 1;
        if v_lin is null then
          raise exception using errcode = '23503', constraint = 'diario_devolucion_ov_sin_renglon',
            message = format('devolucion_ov %s/%s/%s %s ×%s: no hay OV entregada con ese SKU y piezas por devolver '
                             || '(sin OV va como devolucion_sin_ov; sin venta conocida, devolucion_sin_venta)',
                             r_d.canal, r_d.cuenta, r_d.referencia, r_d.sku, r_d.piezas);
        end if;
        v_ref := format('dev:%s:recibe', r_d.id);
        perform almacen.diario_mov(r_d.id, 'devolucion', r_d.piezas, v_ref, 'DEV-' || r_d.id, r_d.ubicacion_id, v_lin);
      when 'devolucion_sin_venta' then
        -- [H12] llegó una caja y no se sabe de qué venta: entra NO vendible (POR VERIFICAR o cuarentena) y solo se
        -- libera con un conteo (par de ajuste_conteo). Si después se identifica, NO se asienta otra vez.
        if r_d.piezas <= 0 or r_d.ubicacion_id is null
           or not exists (select 1 from almacen.ubicaciones u join almacen.ubicacion_tipos t on t.tipo = u.tipo
                           where u.id = r_d.ubicacion_id and u.almacen = r_d.almacen and not t.cuenta_para_venta) then
          raise exception using errcode = '23514', constraint = 'diario_devolucion_sin_venta_vendible',
            message = 'devolucion_sin_venta: piezas > 0 y una ubicación NO vendible de la bodega (POR VERIFICAR o cuarentena)';
        end if;
        v_ref := format('devsv:%s', r_d.id);
        perform almacen.diario_mov(r_d.id, 'devolucion', r_d.piezas, v_ref, 'DEV-' || r_d.id, r_d.ubicacion_id);
      when 'retiro_full' then
        if coalesce(r_d.cuenta, '') not in ('BEKURA', 'SANCORFASHION') or r_d.piezas <= 0 then
          raise exception using errcode = '23514', constraint = 'diario_forma', message = 'retiro_full: cuenta BEKURA o SANCORFASHION y piezas > 0';
        end if;
        select count(*) into v_n from almacen.diario x where x.tipo = 'retiro_full' and x.cuenta = r_d.cuenta
           and x.referencia = r_d.referencia and x.sku = r_d.sku and x.id <= r_d.id;
        v_ref := format('retiro_full:%s:%s:%s:%s', r_d.cuenta, r_d.referencia, upper(r_d.sku::text), v_n);
        perform almacen.diario_mov(r_d.id, 'entrada', r_d.piezas, v_ref, 'FULL-' || r_d.referencia, r_d.ubicacion_id);
      when 'paso_cuarentena' then
        if r_d.piezas <= 0 or r_d.destino_ubicacion_id is null
           or not exists (select 1 from almacen.ubicaciones u where u.id = r_d.destino_ubicacion_id and u.almacen = r_d.almacen) then
          raise exception using errcode = '23514', constraint = 'diario_forma',
            message = 'paso_cuarentena: piezas > 0 y destino_ubicacion en la misma bodega';
        end if;
        perform almacen.diario_mov(r_d.id, 'reubicacion', -r_d.piezas, format('reub:dia:%s:sale', r_d.id), r_d.referencia, r_d.ubicacion_id);
        perform almacen.diario_mov(r_d.id, 'reubicacion',  r_d.piezas, format('reub:dia:%s:entra', r_d.id), r_d.referencia, r_d.destino_ubicacion_id);
        v_ref := format('reub:dia:%s:sale', r_d.id);
      when 'traslado' then
        if r_d.piezas <= 0 or r_d.destino_almacen is null or r_d.destino_almacen = r_d.almacen then
          raise exception using errcode = '23514', constraint = 'diario_forma', message = 'traslado: piezas > 0 y destino_almacen distinto';
        end if;
        insert into almacen.traslados (origen, destino, nota)
        values (r_d.almacen, r_d.destino_almacen, format('diario %s#%s (%s): %s', r_d.lote, r_d.renglon, r_d.referencia, coalesce(r_d.nota, '')))
        returning id into v_tra;
        insert into almacen.traslado_lineas (traslado_id, sku, solicitado) values (v_tra, r_d.sku, r_d.piezas);
        perform set_config('almacen.firma', jsonb_build_object('quien_nombre', r_d.quien, 'via', 'diario',
                             'nota', format('diario %s#%s: %s', r_d.lote, r_d.renglon, coalesce(r_d.nota, '')))::text, true);
        perform almacen.despachar_traslado(v_tra, r_d.sku, r_d.piezas, r_d.ubicacion_id);
        perform almacen.recibir_traslado(v_tra, r_d.sku, r_d.piezas, r_d.destino_ubicacion_id);
        perform set_config('almacen.firma', '', true);
        perform almacen.cuadrar_traslado(v_tra);
        v_ref := 'TRA-' || v_tra;
      when 'salida_shein', 'salida_mayoreo', 'envio_full', 'venta_enviada' then
        if r_d.piezas >= 0 then
          raise exception using errcode = '23514', constraint = 'diario_forma', message = r_d.tipo || ': piezas < 0';
        end if;
        if r_d.tipo = 'venta_enviada' and not exists (select 1 from channel.orders o where o.canal = r_d.canal
             and upper(btrim(o.cuenta)) = r_d.cuenta and o.external_order_id = r_d.referencia) then
          raise exception using errcode = '23503', constraint = 'diario_venta_no_existe',
            message = format('venta_enviada: %s/%s/%s no está en channel.orders', r_d.canal, r_d.cuenta, r_d.referencia);
        end if;
        v_ref := almacen.diario_ov(r_d.id);
      when 'cancelacion_repuso_woo', 'cancelacion_temu' then
        -- PARTIDA: no mueve el libro (solo Woo cambió). [R corr] Aquí NO se escribe t0_partidas: en el acto de T0 la
        -- pasa @@T1 de la plantilla (un solo escritor, con el lote de T0 explícito en plantilla.lote_t0).
        if r_d.woo_restado is null or r_d.woo_restado = 0 then
          raise exception using errcode = '23514', constraint = 'diario_forma', message = r_d.tipo || ' exige woo_restado ≠ 0';
        end if;
        v_estado := 'partida';
      end case;
      perform set_config('app.almacen_escritor', 'diario:' || txid_current(), true);
      update almacen.diario x set estado = v_estado, asentado_ref = v_ref, asentado_at = now(), asentado_por = v_quien
       where x.id = r_d.id;
      perform set_config('app.almacen_escritor', '', true);
      v_asent := v_asent + 1;
      v_res := v_res || jsonb_build_array(jsonb_build_object('id', r_d.id, 'resultado', v_estado, 'ref', v_ref));
    exception when others then
      get stacked diagnostics v_state = returned_sqlstate, v_con = constraint_name, v_msg = message_text;
      raise exception using errcode = v_state, constraint = coalesce(nullif(v_con, ''), 'asentar_diario'),
        message = format('diario #%s (%s#%s, %s): %s', r_d.id, r_d.lote, r_d.renglon, r_d.tipo, v_msg);
    end;
  end loop;
  return jsonb_build_object('asentados', v_asent, 'ya_asentados', v_ya, 'renglones', v_res);
end $$;

create or replace function almacen.rechazar_diario(p_id bigint, p_motivo text) returns void
language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare v_quien text := nullif(current_setting('app.usuario', true), '');
begin
  if v_quien is null then
    raise exception using errcode = '23514', constraint = 'rechazar_diario_quien', message = 'rechazar_diario exige app.usuario';
  end if;
  if length(btrim(coalesce(p_motivo, ''))) < 10 then
    raise exception using errcode = '23514', constraint = 'diario_rechazo_chk', message = 'rechazar exige un motivo de 10 caracteres o más';
  end if;
  perform set_config('app.almacen_escritor', 'diario:' || txid_current(), true);
  update almacen.diario x set estado = 'rechazado', motivo_rechazo = p_motivo where x.id = p_id;
  perform set_config('app.almacen_escritor', '', true);
end $$;

-- Reporte diario (D9): MAESTRO lo saca cada mañana de estas dos vistas. Ningún correo ni Slack en esta migración.
create or replace view almacen.diario_reporte_v with (security_invoker = on) as
select (d.ocurrido_at at time zone 'America/Mexico_City')::date as dia_cdmx, d.lote, d.almacen, d.tipo, d.estado,
       count(*) as renglones, sum(d.piezas) as piezas,
       count(*) filter (where d.estado = 'pendiente' and d.capturado_at < now() - interval '24 hours') as pendientes_24h
  from almacen.diario d
 group by 1, 2, 3, 4, 5;

create or replace view almacen.diario_conciliacion_v with (security_invoker = on) as
select 'diario_pendiente_24h'::text as problema, d.lote || '#' || d.renglon as donde,
       format('%s %s %s pzs (%s)', d.tipo, d.sku, d.piezas, d.referencia) as detalle, d.capturado_at as desde
  from almacen.diario d where d.estado = 'pendiente' and d.capturado_at < now() - interval '24 hours'
union all
-- [H5/H11] Una devolución de ML a nuestra puerta entra a la lista cuando LLEGÓ (no cuando ML reembolsó: 52 de 57
-- reembolsan al despachar; ni por actualizado_at, que returns_touch reescribe) y la llegada es del corte en adelante.
-- [H10] Una OV cuenta como atendida solo con devolucion_estado recibida|cerrada o con un dev: asentado en sus renglones
-- ('pendiente' lo pone ordenes_venta al cancelar el canal: la caja todavía no llega).
select 'devolucion_ml_sin_asentar', r.canal || '/' || upper(btrim(r.cuenta)) || '/' || r.external_return_id,
       format('venta %s, estado %s, llegó %s', r.external_order_id, r.estado, coalesce(ll.llegada_at::text, 'sin fecha (abierta después del corte)')),
       coalesce(ll.llegada_at, r.abierta_at)
  from channel.returns r
  cross join lateral almacen.devolucion_llegada(r.canal, upper(btrim(r.cuenta)), r.external_return_id) ll
 where r.destino = 'seller_address' and ll.llego
   and (ll.llegada_at >= almacen.t0_corte() or (ll.llegada_at is null and r.abierta_at >= almacen.t0_corte()))
   and not exists (select 1 from almacen.stock_mov m
                    where left(m.clave, length(format('devret:%s:%s:%s:', r.canal, upper(btrim(r.cuenta)), r.external_return_id)))
                          = format('devret:%s:%s:%s:', r.canal, upper(btrim(r.cuenta)), r.external_return_id))
   and not exists (select 1 from ventas.ov_ordenes o where o.mp_canal = r.canal and o.mp_cuenta = upper(btrim(r.cuenta))
                      and o.mp_orden = r.external_order_id and o.borrada_at is null
                      and (o.devolucion_estado in ('recibida', 'cerrada')
                           or exists (select 1 from ventas.ov_lineas l join almacen.stock_mov m on m.ov_linea_id = l.id
                                       where l.orden_id = o.id and m.motivo = 'devolucion')))
union all
-- [H11] llegó sin fecha y se abrió antes del corte: no se sabe si ya está en la copia de Odoo; se revisa a mano.
select 'devolucion_ml_llegada_sin_fecha', r.canal || '/' || upper(btrim(r.cuenta)) || '/' || r.external_return_id,
       format('venta %s, estado %s: llegó sin fecha y se abrió antes del corte (¿ya está en la copia?)', r.external_order_id, r.estado),
       r.abierta_at
  from channel.returns r
  cross join lateral almacen.devolucion_llegada(r.canal, upper(btrim(r.cuenta)), r.external_return_id) ll
 where r.destino = 'seller_address' and ll.llego and ll.llegada_at is null
   and coalesce(r.abierta_at, '-infinity') < almacen.t0_corte()
   and not exists (select 1 from almacen.stock_mov m
                    where left(m.clave, length(format('devret:%s:%s:%s:', r.canal, upper(btrim(r.cuenta)), r.external_return_id)))
                          = format('devret:%s:%s:%s:', r.canal, upper(btrim(r.cuenta)), r.external_return_id))
union all
select 'venta_drop_sin_ov', p.canal || '/' || p.cuenta || '/' || p.orden,
       string_agg(p.sku || ' x' || p.pzs, ', ' order by p.sku),
       (select min(o.creado_at) from channel.orders o
         where o.canal = p.canal and upper(btrim(o.cuenta)) = p.cuenta and o.external_order_id = p.orden)
  from almacen.t0_pendientes(almacen.t0_corte()) p                                   -- la consulta de stock_libro
 group by p.canal, p.cuenta, p.orden
union all
select 'retiro_full_asentado', m.clave, format('%s +%s en %s (%s)', m.sku, m.delta, m.almacen, m.ref), m.creado_at
  from almacen.stock_mov m where m.clave like 'retiro_full:%'
union all
-- [H12] devoluciones sin venta conocida asentadas (no vendibles): MAESTRO las coteja cada mañana contra las que llegan
-- después con su venta, para no asentar la misma caja dos veces.
select 'devolucion_sin_venta_asentada', m.clave, format('%s +%s en %s, ubicación %s (%s)', m.sku, m.delta, m.almacen, m.ubicacion_id, m.nota), m.creado_at
  from almacen.stock_mov m where m.clave like 'devsv:%';
comment on view almacen.diario_conciliacion_v is
  'D9: pendientes > 24 h, devoluciones de ML que llegaron y no se asentaron (por llegada, H5/H11), llegadas sin fecha anteriores al corte, ventas DROP sin OV, retiros de FULL asentados (cotejar contra WITHDRAWAL_DELIVERY) y devoluciones sin venta conocida (H12).';

-- ───────────────────────────────────────────────────────────────────────────
-- C8. RLS y grants [S2, T4], funciones [S3], ALWAYS [S1-a]
-- ───────────────────────────────────────────────────────────────────────────
alter table almacen.puentes_odoo      enable row level security;
alter table almacen.puentes_odoo_hist enable row level security;
alter table almacen.traslados         enable row level security;
alter table almacen.traslado_lineas   enable row level security;
alter table almacen.traslado_eventos  enable row level security;
alter table almacen.diario            enable row level security;
revoke all on almacen.diario, almacen.diario_reporte_v, almacen.diario_conciliacion_v
  from public, anon, authenticated, service_role;
grant select on almacen.diario, almacen.diario_reporte_v, almacen.diario_conciliacion_v to service_role;
revoke all on almacen.puentes_odoo, almacen.puentes_odoo_hist, almacen.traslados, almacen.traslado_lineas,
              almacen.traslado_eventos, almacen.traslados_v, almacen.vigia_traslados_v, almacen.fuente_unica_vigia_v,
              almacen.existencias_bodega_v, almacen.existencias_sku_v
  from public, anon, authenticated, service_role;
grant select on almacen.puentes_odoo, almacen.puentes_odoo_hist, almacen.traslados, almacen.traslado_lineas,
               almacen.traslado_eventos, almacen.traslados_v, almacen.vigia_traslados_v, almacen.fuente_unica_vigia_v,
               almacen.existencias_bodega_v, almacen.existencias_sku_v to service_role;
-- (insert en traslados/traslado_lineas a service_role SOLO si aparece un escritor medido que entre como service_role)
revoke all on function almacen.tg_puentes_odoo_guarda(), almacen.tg_puentes_odoo_hist(), almacen.tg_traslados_guarda(),
                       almacen.tg_traslado_lineas_guarda(), almacen.tg_traslado_evento_regla(), almacen.tg_traslado_evento_suma(),
                       almacen.tg_stock_mov_tras_evento(), almacen.tg_una_venta_una_fuente(),
                       almacen.recibir_traslado(bigint, citext, integer, bigint), almacen.despachar_traslado(bigint, citext, integer, bigint),
                       almacen.cerrar_diferencia(bigint, citext, integer, text), almacen.cuadrar_traslado(bigint),
                       almacen.cancelar_traslado(bigint, text),
                       almacen.tg_devolucion_sin_ov(), almacen.tg_sku_existe(), almacen.tg_diario_guarda(),
                       almacen.diario_mov(bigint, text, integer, text, text, bigint, bigint), almacen.diario_ov(bigint),
                       almacen.asentar_diario(bigint[]), almacen.rechazar_diario(bigint, text),
                       almacen.t0_corte(), almacen.devolucion_llegada(text, text, text), almacen.tg_almacenes_woo_existencias()
  from public, anon, authenticated, service_role;
alter table almacen.almacenes         enable always trigger almacenes_woo_existencias;          -- [WOO SALIDA]
alter table almacen.puentes_odoo      enable always trigger puentes_odoo_guarda, enable always trigger puentes_odoo_hist,
                                      enable always trigger puentes_odoo_sin_truncate;
alter table almacen.puentes_odoo_hist enable always trigger puentes_odoo_hist_solo_agregar,
                                      enable always trigger puentes_odoo_hist_sin_truncate;
alter table almacen.traslados         enable always trigger traslados_guarda, enable always trigger traslados_sin_truncate;
alter table almacen.traslado_lineas   enable always trigger traslado_lineas_guarda, enable always trigger traslado_lineas_sin_truncate;
alter table almacen.traslado_eventos  enable always trigger traslado_eventos_regla, enable always trigger traslado_eventos_suma,
                                      enable always trigger traslado_eventos_solo_agregar,
                                      enable always trigger traslado_eventos_sin_truncate;
alter table almacen.stock_mov         enable always trigger stock_mov_tras_evento,
                                      enable always trigger stock_mov_devolucion_cuadra,          -- recreada aquí (§5.3)
                                      enable always trigger stock_mov_devolucion_sin_ov,
                                      enable always trigger stock_mov_sku_existe;
alter table almacen.stock_almacen     enable always trigger stock_almacen_sku_existe;
alter table almacen.diario            enable always trigger diario_guarda, enable always trigger diario_sin_truncate;
alter table ops.odoo_sale_orders      enable always trigger odoo_sale_orders_una_fuente;
alter table ventas.ov_ordenes         enable always trigger ov_ordenes_una_fuente;

-- C9. Registro (T5) y paso final que falla cerrado (S8)
insert into ops.migraciones (migracion, detalle)
select '0077_almacen_traslados_y_claves',                          -- [R corr] = nombre del archivo (aplicar_migraciones.py)
       jsonb_build_object('tablas', 6, 'vistas', 7, 'funciones', 16,
                          'clave_chk_validada', (select c.convalidated from pg_constraint c
                                                  where c.conrelid = 'almacen.stock_mov'::regclass and c.conname = 'stock_mov_clave_chk'),
                          'd4_pendiente_de_firma', true,
                          'huecos_fanout_21oct', jsonb_build_array('H1 FULL a la puerta', 'H2 devolucion_ov', 'H3 devret :<n>',
                                                                   'H5/H10/H11 conciliación por llegada', 'H9 venta posterior sin OV',
                                                                   'H7/H12 devolucion_sin_venta', 'diario_ov por el trío',
                                                                   'cuenta_para_woo con existencias'))
 where to_regclass('ops.migraciones') is not null
   and not exists (select 1 from ops.migraciones where migracion = '0077_almacen_traslados_y_claves');

do $$ begin
  if exists (select 1 from pg_class c where c.relnamespace = 'almacen'::regnamespace and c.relkind in ('r','p') and not c.relrowsecurity)
  or exists (select 1 from pg_class c where c.relnamespace = 'almacen'::regnamespace and c.relkind = 'v'
              and not coalesce(c.reloptions && array['security_invoker=on','security_invoker=true'], false))
  or exists (select 1 from information_schema.role_table_grants
              where table_schema = 'almacen' and grantee in ('anon','authenticated','PUBLIC'))
  or exists (select 1 from pg_proc p where p.pronamespace = 'almacen'::regnamespace
              and (p.prosecdef or p.proacl is null
                   or exists (select 1 from aclexplode(p.proacl) x
                               where x.privilege_type = 'EXECUTE'
                                 and x.grantee in (0, 'anon'::regrole, 'authenticated'::regrole, 'service_role'::regrole))))
  or exists (select 1 from unnest(array['almacen.ubicacion_tipos_hist','almacen.historial_movimientos','almacen.stock_mov',
                                         'almacen.almacenes_hist','almacen.desprendimientos','almacen.desprendimiento_foto',
                                         'almacen.ddl_auditoria','almacen.puentes_odoo',
                                         'almacen.puentes_odoo_hist','almacen.traslados','almacen.traslado_lineas',
                                         'almacen.traslado_eventos','almacen.diario','almacen.saldo_inicial_ajustes',
                                         'almacen.t0_migradas','almacen.t0_woo_foto','almacen.t0_partidas','almacen.t0_skus_sin_libro']) t
              where has_table_privilege('service_role', t, 'UPDATE,DELETE,TRUNCATE,TRIGGER'))
  or exists (select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
              where c.relnamespace = 'almacen'::regnamespace and not t.tgisinternal and t.tgenabled <> 'A'
                and t.tgname <> all (array['locations_tocar_trg','historial_movimientos_tocar_trg','ubicaciones_tocar']))
  or exists (select 1 from pg_trigger t
              where t.tgname in ('odoo_sale_orders_una_fuente', 'ov_ordenes_una_fuente') and t.tgenabled <> 'A')
  -- [T0 §5.7] la clave existe (validada si no hay filas fmt:), los triggers nuevos en ALWAYS y diario de solo agregar
  or not exists (select 1 from pg_constraint c where c.conrelid = 'almacen.stock_mov'::regclass and c.conname = 'stock_mov_clave_chk'
                  and pg_get_constraintdef(c.oid) like '%retiro_full%' and pg_get_constraintdef(c.oid) not like '%fmt:%'
                  and pg_get_constraintdef(c.oid) like '%devsv:%'                                   -- [H12]
                  and (c.convalidated or exists (select 1 from almacen.stock_mov m where m.motivo = 'entrada' and m.clave ~ '^fmt:')))
  or exists (select 1 from unnest(array['stock_mov_devolucion_sin_ov', 'stock_mov_sku_existe', 'stock_mov_devolucion_cuadra',
                                         'stock_almacen_sku_existe', 'diario_guarda', 'diario_sin_truncate',
                                         'almacenes_woo_existencias']) n(tg)
              where not exists (select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
                                 where c.relnamespace = 'almacen'::regnamespace and t.tgname = n.tg and t.tgenabled = 'A'))
  -- [H2/H12] los tipos nuevos del diario y la función de llegada (H5/H11)
  or not exists (select 1 from pg_constraint c where c.conrelid = 'almacen.diario'::regclass and c.conname = 'diario_tipo_chk'
                  and pg_get_constraintdef(c.oid) like '%devolucion_ov%' and pg_get_constraintdef(c.oid) like '%devolucion_sin_venta%')
  or to_regprocedure('almacen.devolucion_llegada(text, text, text)') is null
  or to_regprocedure('almacen.diario_mov(bigint, text, integer, text, text, bigint)') is not null
  or not exists (select 1 from pg_trigger t where t.tgrelid = 'almacen.diario'::regclass and t.tgname = 'diario_guarda'
                  and (t.tgtype & 24) = 24)                                   -- BEFORE UPDATE y DELETE
  or not exists (select 1 from pg_attribute a where a.attrelid = 'almacen.existencias_sku_v'::regclass
                  and a.attname = 'transito' and a.attnum = (select max(b.attnum) from pg_attribute b
                                                              where b.attrelid = 'almacen.existencias_sku_v'::regclass and b.attnum > 0))
  then
    raise exception using errcode = 'KB000',
      message = 'C′: candado incompleto (RLS / invoker / grants / funciones / ALWAYS / claves / diario); revisar S1–S11 y SPEC_T0 §5.7';
  end if;
end $$;
commit;
