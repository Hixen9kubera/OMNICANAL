-- ═══════════════════════════════════════════════════════════════════════════
-- A′ · 0075 (T0, SANDBOX; a producción solo con acta) — catálogo de ubicaciones,
--     existencias por ubicación, frescura de la copia de Odoo y la HISTORIA de
--     stock de Odoo al corte (esquema odoo_historia).
-- Es la A_0074 probada el 13-oct (93 casos) renumerada, SIN cambios de fondo
-- (SPEC_T0 §3.1). Lo nuevo es A10: odoo_historia.{cargas, stock_location,
-- product_product, stock_quant} + saldo_al_15oct_v (SPEC_T0 §3.2–§3.5).
-- Marcas: [S#] revisión de seguridad · [T#] revisión técnica · [T0] contrato T0.
-- NO cambia quién escribe (salvo el historial, que ya solo escribe TEX2 en Odoo).
-- ═══════════════════════════════════════════════════════════════════════════
begin;
set local lock_timeout = '5s';
set local statement_timeout = '120s';

-- A0. Requisitos: 0069 + 0070 aplicadas, almacen.almacenes con (codigo, fuente) único.
do $$ begin
  if to_regclass('almacen.locations') is null or to_regclass('almacen.historial_movimientos') is null
     or not exists (select 1 from pg_constraint where conrelid = 'almacen.almacenes'::regclass
                     and conname = 'almacenes_codigo_fuente_uq') then
    raise exception using errcode = 'KB000', message = 'A: faltan 0069/0070 o almacenes (codigo, fuente)';
  end if;
end $$;

-- A1. Tipos de ubicación ------------------------------------------------------
create table if not exists almacen.ubicacion_tipos (
  tipo              text        not null,
  nombre            text        not null,
  cuenta_para_venta boolean     not null,
  descripcion       text,
  actualizado_at    timestamptz not null default now(),
  actualizado_por   text        default nullif(current_setting('app.usuario', true), ''),
  motivo            text,
  constraint ubicacion_tipos_pkey       primary key (tipo),
  constraint ubicacion_tipos_tipo_chk   check (tipo ~ '^[a-z_]{3,20}$'),
  constraint ubicacion_tipos_nombre_chk check (length(btrim(nombre)) > 0),
  constraint ubicacion_tipos_scrap_chk  check (tipo <> 'scrap' or not cuenta_para_venta)   -- SCRAP nunca cuenta
);
create table if not exists almacen.ubicacion_tipos_hist (
  id          bigint      generated always as identity primary key,
  tipo        text        not null,
  antes       jsonb,
  despues     jsonb       not null,
  motivo      text,
  quien       text,
  cambiado_at timestamptz not null default now()
);
create or replace function almacen.tg_ubicacion_tipos_guarda() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$   -- [S3]
declare v_quien text := nullif(current_setting('app.usuario', true), '');
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', constraint = 'ubicacion_tipos_sin_borrado',
      message = format('almacen.ubicacion_tipos: el tipo %s no se borra', old.tipo);
  end if;
  if tg_op = 'UPDATE' and new.tipo is distinct from old.tipo then
    raise exception using errcode = '42501', constraint = 'ubicacion_tipos_tipo_fijo', message = 'el tipo es fijo';
  end if;
  if tg_op = 'INSERT' or new.cuenta_para_venta is distinct from old.cuenta_para_venta then
    if length(btrim(coalesce(new.motivo, ''))) < 10
       or (tg_op = 'UPDATE' and new.motivo is not distinct from old.motivo) then
      raise exception using errcode = '23514', constraint = 'ubicacion_tipos_motivo_chk',
        message = format('ubicacion_tipos %s: cambiar cuenta_para_venta exige un motivo NUEVO de 10+', new.tipo);
    end if;
    if coalesce(v_quien, new.actualizado_por) is null then
      raise exception using errcode = '23514', constraint = 'ubicacion_tipos_quien_chk', message = 'falta quién (app.usuario)';
    end if;
  end if;
  new.actualizado_por := coalesce(v_quien, new.actualizado_por);
  new.actualizado_at  := now();
  return new;
end $$;
create or replace function almacen.tg_ubicacion_tipos_hist() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$   -- [S3]
begin
  insert into almacen.ubicacion_tipos_hist (tipo, antes, despues, motivo, quien)
  values (new.tipo, case when tg_op = 'UPDATE' then to_jsonb(old) end, to_jsonb(new), new.motivo,
          coalesce(nullif(current_setting('app.usuario', true), ''), new.actualizado_por));
  return null;
end $$;
create trigger ubicacion_tipos_guarda before insert or update or delete on almacen.ubicacion_tipos
  for each row execute function almacen.tg_ubicacion_tipos_guarda();
create trigger ubicacion_tipos_hist after insert or update on almacen.ubicacion_tipos
  for each row execute function almacen.tg_ubicacion_tipos_hist();
create trigger ubicacion_tipos_hist_solo_agregar before update or delete on almacen.ubicacion_tipos_hist
  for each row execute function ops.tg_solo_agregar();
create trigger ubicacion_tipos_hist_sin_truncate before truncate on almacen.ubicacion_tipos_hist   -- [S7]
  for each statement execute function ops.tg_sin_truncate();
create trigger ubicacion_tipos_sin_truncate before truncate on almacen.ubicacion_tipos
  for each statement execute function ops.tg_sin_truncate();

insert into almacen.ubicacion_tipos (tipo, nombre, cuenta_para_venta, descripcion, actualizado_por, motivo) values
  ('almacenaje',   'Almacenaje',           true,  'Racks y agrupadores, raíz de la nave (SIN UBICAR), STAGE, REQUERIMENTOS, BÓVEDA', 'migracion_0075', 'Semilla: reproduce free_qty de Odoo'),
  ('entrada',      'Entrada',              true,  'Recepción dentro de la bodega',               'migracion_0075', 'Semilla: reproduce free_qty de Odoo'),
  ('preparacion',  'Preparación',          true,  'Zona de empaquetado (recolectar y empacar)',  'migracion_0075', 'Semilla: reproduce free_qty de Odoo'),
  ('salida',       'Salida',               true,  'Andén de salida',                             'migracion_0075', 'Semilla: reproduce free_qty de Odoo'),
  ('calidad',      'Calidad y producción', true,  'Control de calidad, Pre/Posproducción',       'migracion_0075', 'Semilla: reproduce free_qty de Odoo'),
  ('devoluciones', 'Devoluciones',         true,  'Devoluciones por revisar en la bodega',       'migracion_0075', 'Semilla: reproduce free_qty de Odoo'),
  ('archivo',      'Archivo',              true,  'FERRAFORME Archivar',                         'migracion_0075', 'Semilla: reproduce free_qty de Odoo'),
  ('cuarentena',   'Cuarentena',           false, 'TEXCO/FERRAFORME/CUARENTENA (470719)',        'migracion_0075', 'Semilla: Odoo la deja fuera; la enciende Eduardo'),
  ('scrap',        'Scrap',                false, 'TEXCO/FERRAFORME/SCRAP (108666)',             'migracion_0075', 'Semilla: nunca cuenta (CHECK)')
on conflict (tipo) do nothing;

-- A2. Catálogo de ubicaciones ------------------------------------------------
create table if not exists almacen.ubicaciones (
  id               bigint      generated always as identity,
  almacen          text        not null,
  fuente           text        not null,                 -- sigue a almacenes.fuente (FK en cascada)
  ruta             text        not null,                 -- complete_name de Odoo
  posicion         text        not null,                 -- como almacen.locations.ubicacion (ubicacion_de() de Brandon)
  padre_id         bigint,
  tipo             text        not null,
  rack_bloque      text,                                 -- TEX2: BLOQUE · TEXCO/DROP: letra
  rack_fila        text,                                 -- TEX2: FILA   · TEXCO/DROP: número
  rack_nivel       text,                                 -- TEX2: T#     · TEXCO/DROP: N#
  activa           boolean     not null default true,
  odoo_location_id integer,                              -- NULL = nació en kubera
  odoo_sin_almacen boolean     not null default false,   -- sin warehouse_id en Odoo (CUARENTENA, SCRAP): fuera de free_qty
  odoo_escrito_at  timestamptz,
  creado_at        timestamptz not null default now(),
  actualizado_at   timestamptz not null default now(),
  constraint ubicaciones_pkey         primary key (id),
  constraint ubicaciones_id_alm_uq    unique (id, almacen),
  constraint ubicaciones_odoo_uq      unique (odoo_location_id),
  -- CASCADE solo aquí: es catálogo, no existencias; la protege la puerta de B2 (S4/S6).
  constraint ubicaciones_almacen_fk   foreign key (almacen, fuente) references almacen.almacenes (codigo, fuente)
                                      on update cascade on delete restrict not deferrable,              -- [S6] explícito
  constraint ubicaciones_padre_fk     foreign key (padre_id) references almacen.ubicaciones (id),
  constraint ubicaciones_tipo_fk      foreign key (tipo) references almacen.ubicacion_tipos (tipo),
  constraint ubicaciones_fuente_chk   check (fuente in ('odoo', 'kubera')),
  constraint ubicaciones_odoo_chk     check (fuente = 'kubera' or odoo_location_id is not null),
  constraint ubicaciones_posicion_chk check (length(btrim(posicion)) > 0 and posicion = btrim(posicion)),
  constraint ubicaciones_ruta_chk     check (length(btrim(ruta)) > 0),
  constraint ubicaciones_padre_chk    check (padre_id is distinct from id),
  constraint ubicaciones_rack_chk     check ((rack_bloque is null and rack_fila is null and rack_nivel is null)
                                          or (rack_bloque is not null and rack_fila is not null and rack_nivel is not null))
);
create index if not exists ubicaciones_alm_pos_ix on almacen.ubicaciones (almacen, posicion);   -- NO único: Odoo repite rutas (TEXCO B1–B6)
create index if not exists ubicaciones_padre_ix   on almacen.ubicaciones (padre_id) where padre_id is not null;
create unique index if not exists ubicaciones_pos_kubera_uq on almacen.ubicaciones (almacen, posicion)
  where activa and odoo_location_id is null;
create unique index if not exists ubicaciones_sin_ubicar_uq on almacen.ubicaciones (almacen)
  where activa and posicion = 'SIN UBICAR';
create trigger ubicaciones_tocar before update on almacen.ubicaciones
  for each row execute function almacen.tg_tocar_actualizado_at();
create trigger ubicaciones_sin_truncate before truncate on almacen.ubicaciones
  for each statement execute function ops.tg_sin_truncate();
insert into almacen.ubicaciones (almacen, fuente, ruta, posicion, tipo)
select 'ENSAYO', 'kubera', 'ENSAYO', 'SIN UBICAR', 'almacenaje'
 where exists (select 1 from almacen.almacenes where codigo = 'ENSAYO' and fuente = 'kubera')
   and not exists (select 1 from almacen.ubicaciones where almacen = 'ENSAYO' and posicion = 'SIN UBICAR');

-- A3. almacen.locations: reservado, ubicación del catálogo y la llave a la FUENTE --
alter table almacen.locations
  add column if not exists reservado    integer not null default 0,     -- reserved_quantity del quant; lo mantiene la copia (T, carga)
  add column if not exists ubicacion_id bigint;                         -- lo rellena la carga inicial (T7)
alter table almacen.locations
  add constraint locations_reservado_chk check (reservado >= 0 and (origen = 'odoo' or reservado = 0)),
  -- LA REGLA: un renglón de origen X solo existe en una bodega cuya fuente es X. NUNCA cascade. [S6]
  add constraint locations_fuente_fk foreign key (cedis, origen) references almacen.almacenes (codigo, fuente)
      on update restrict on delete restrict not deferrable,
  add constraint locations_ubicacion_fk foreign key (ubicacion_id, cedis)
      references almacen.ubicaciones (id, almacen) deferrable initially deferred;
create index if not exists locations_cedis_origen_ix on almacen.locations (cedis, origen);
create unique index if not exists locations_sku_ubicacion_id_uq on almacen.locations (cedis, sku, ubicacion_id)
  where ubicacion_id is not null;
create trigger locations_sin_truncate before truncate on almacen.locations
  for each statement execute function ops.tg_sin_truncate();

-- A4. El historial se escribe solo mientras la bodega sea de Odoo ---------------
-- [S15] FOR SHARE exige UPDATE sobre almacenes: hoy el único escritor es postgres. Con roles propios (D17) se revisa.
create or replace function almacen.tg_historial_solo_odoo() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$   -- [S3]
declare v_fuente text;
begin
  select a.fuente into v_fuente from almacen.almacenes a
   where a.codigo = case when tg_op = 'DELETE' then old.cedis else new.cedis end for share;
  if v_fuente is distinct from 'odoo' then
    raise exception using errcode = '42501', constraint = 'historial_congelado',
      message = format('historial de %s: la bodega ya no es de Odoo; su historial queda congelado',
                       case when tg_op = 'DELETE' then old.cedis else new.cedis end);
  end if;
  return case when tg_op = 'DELETE' then old else new end;
end $$;
create trigger historial_movimientos_solo_odoo before insert or update or delete on almacen.historial_movimientos
  for each row execute function almacen.tg_historial_solo_odoo();
create trigger historial_movimientos_sin_truncate before truncate on almacen.historial_movimientos
  for each statement execute function ops.tg_sin_truncate();

-- A5. Frescura de la copia --------------------------------------------------
create table if not exists almacen.copias_odoo (
  almacen              text        not null,
  fuente               text        not null default 'odoo',
  foto_at              timestamptz,
  foto_renglones       integer,
  foto_fisico          bigint,
  foto_reservado       bigint,
  catalogo_at          timestamptz,
  catalogo_ubicaciones integer,
  historial_hasta      timestamptz,
  ultima_pasada_at     timestamptz not null default now(),
  ultima_pasada_ok     boolean     not null default false,
  ultimo_error         text,                    -- clase + mensaje REDACTADO; nunca argumentos de execute_kw ni la URL [S12]
  constraint copias_odoo_pkey       primary key (almacen),
  constraint copias_odoo_fuente_chk check (fuente = 'odoo'),
  constraint copias_odoo_error_chk  check (ultimo_error is null or length(ultimo_error) <= 500),          -- [S12]
  constraint copias_odoo_almacen_fk foreign key (almacen, fuente) references almacen.almacenes (codigo, fuente)
      on update restrict on delete restrict not deferrable                                                -- [S6]
);
create trigger copias_odoo_sin_truncate before truncate on almacen.copias_odoo                             -- [S7]
  for each statement execute function ops.tg_sin_truncate();
insert into almacen.copias_odoo (almacen)
select codigo from almacen.almacenes where fuente = 'odoo' on conflict do nothing;

-- A6. Vistas (security_invoker = on, explícito) ------------------------------------
create or replace view almacen.existencias_ubicacion_v with (security_invoker = on) as
select l.sku,
       l.cedis                                                   as almacen,
       a.fuente,
       l.ubicacion_id,
       coalesce(u.posicion, l.ubicacion)                         as ubicacion,
       u.ruta,
       u.tipo,                                                   -- NULL = renglón aún sin catálogo (T7: no vendible)
       (coalesce(t.cuenta_para_venta, false) and a.cuenta_para_woo) as cuenta_para_venta,
       l.piezas                                                  as fisico,
       l.reservado                                               as reservado_ubicacion,
       l.piezas - l.reservado                                    as libre_ubicacion,
       case when a.fuente = 'odoo' then c.foto_at end            as copia_at,
       l.origen
  from almacen.locations l
  join almacen.almacenes a        on a.codigo = l.cedis
  left join almacen.ubicaciones u on u.id = l.ubicacion_id
  left join almacen.ubicacion_tipos t on t.tipo = u.tipo
  left join almacen.copias_odoo c on c.almacen = l.cedis;

create or replace view almacen.existencias_bodega_v with (security_invoker = on) as
with g as (
  select e.sku, e.almacen, e.fuente,
         case when e.tipo in ('cuarentena', 'scrap') then e.tipo else 'bodega' end as grupo,
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
  from r;

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
       array_agg(distinct b.fuente)                                      as fuentes
  from almacen.existencias_bodega_v b
 group by b.sku;

create or replace view almacen.ubicaciones_v with (security_invoker = on) as
select u.*, t.cuenta_para_venta,
       coalesce(o.skus, 0) as skus, coalesce(o.piezas, 0) as piezas, (o.skus is null) as vacia
  from almacen.ubicaciones u
  join almacen.ubicacion_tipos t on t.tipo = u.tipo
  left join (select ubicacion_id, count(*) as skus, sum(piezas) as piezas
               from almacen.locations where ubicacion_id is not null group by 1) o on o.ubicacion_id = u.id;

-- A7. RLS, grants y comments [S2] -------------------------------------------------
alter table almacen.ubicacion_tipos      enable row level security;
alter table almacen.ubicacion_tipos_hist enable row level security;
alter table almacen.ubicaciones          enable row level security;
alter table almacen.copias_odoo          enable row level security;
-- service_role TAMBIÉN: el ACL por omisión de `almacen` le da arwdDxtm a toda tabla y vista nueva. [S2]
revoke all on almacen.ubicacion_tipos, almacen.ubicacion_tipos_hist, almacen.ubicaciones, almacen.copias_odoo,
              almacen.existencias_ubicacion_v, almacen.existencias_bodega_v, almacen.existencias_sku_v,
              almacen.ubicaciones_v
  from public, anon, authenticated, service_role;
grant select on almacen.ubicacion_tipos, almacen.ubicacion_tipos_hist, almacen.ubicaciones, almacen.copias_odoo,
               almacen.existencias_ubicacion_v, almacen.existencias_bodega_v, almacen.existencias_sku_v,
               almacen.ubicaciones_v to service_role;
-- Escritura a service_role: NINGUNA mientras no exista un escritor medido que entre como service_role
-- (hoy el backend, la copia y la carga entran como postgres). [S2]
-- Las extendidas (0069/0070 les dieron ALL), alineadas con la 0064/0065: [S2]
revoke all on almacen.historial_movimientos from service_role;
grant select on almacen.historial_movimientos to service_role;
revoke truncate, references, trigger, maintain on almacen.locations from service_role;
revoke all on function almacen.tg_ubicacion_tipos_guarda(), almacen.tg_ubicacion_tipos_hist(),
                       almacen.tg_historial_solo_odoo()
  from public, anon, authenticated, service_role;                                                      -- [S3]
comment on column almacen.almacenes.cuenta_para_woo is
  'Cuenta para el número vendible (el nombre es histórico; WOO SALIDA F1.3 no lo renombra).';
comment on column almacen.locations.origen is
  'Igual a la fuente de su bodega (FK locations_fuente_fk, NUNCA cascade): odoo = COPIA que solo escribe la copia de Odoo; kubera = saldo que solo escribe el libro.';
comment on constraint locations_fuente_fk on almacen.locations is
  'Una sola fuente por bodega. NUNCA on update cascade: convertiría en silencio la copia en libro (S6).';
comment on constraint copias_odoo_almacen_fk on almacen.copias_odoo is
  'Si la bodega deja de ser de Odoo, su fila de frescura no puede existir. NUNCA cascade (S6).';
comment on column almacen.locations.reservado is
  'reserved_quantity del quant de Odoo. Lo mantiene la copia de Odoo; en renglones kubera es 0 (se aparta por bodega).';

-- A8. ALWAYS en TODAS las guardas, también las que ya existen en almacen.* [S1-a, opción (a)]
--     (session_replication_role = replica apaga los triggers ORIGIN; ALWAYS resiste — prueba 9 de seguridad).
alter table almacen.ubicacion_tipos       enable always trigger ubicacion_tipos_guarda,
                                          enable always trigger ubicacion_tipos_hist,
                                          enable always trigger ubicacion_tipos_sin_truncate;
alter table almacen.ubicacion_tipos_hist  enable always trigger ubicacion_tipos_hist_solo_agregar,
                                          enable always trigger ubicacion_tipos_hist_sin_truncate;
alter table almacen.ubicaciones           enable always trigger ubicaciones_sin_truncate;
alter table almacen.locations             enable always trigger locations_sin_truncate;
alter table almacen.historial_movimientos enable always trigger historial_movimientos_solo_odoo,
                                          enable always trigger historial_movimientos_sin_truncate;
alter table almacen.copias_odoo           enable always trigger copias_odoo_sin_truncate;
alter table almacen.almacenes             enable always trigger almacenes_guarda,
                                          enable always trigger almacenes_hist,
                                          enable always trigger almacenes_sin_truncate;
alter table almacen.almacenes_hist        enable always trigger almacenes_hist_solo_agregar,
                                          enable always trigger almacenes_hist_sin_truncate;
alter table almacen.stock_almacen         enable always trigger stock_almacen_guarda,
                                          enable always trigger stock_almacen_sin_truncate,
                                          enable always trigger stock_almacen_cuadra,
                                          enable always trigger stock_almacen_apartado_cuadra;
alter table almacen.stock_mov             enable always trigger stock_mov_solo_agregar,
                                          enable always trigger stock_mov_sin_truncate,
                                          enable always trigger stock_mov_cuadra,
                                          enable always trigger stock_mov_devolucion_cuadra;

-- ═══════════════════════════════════════════════════════════════════════════
-- A10. odoo_historia: la HISTORIA de stock de Odoo al corte (copia cruda, sin PII) [T0 §3.2]
--   Solo lo que T0 necesita: cargas + stock_location + product_product + stock_quant.
--   Lista blanca columna por columna (la proyecta el cargador; lo demás no entra).
--   Inmutable: se escribe SOLO con una carga abierta en la MISMA transacción
--   (GUC odoo_historia.carga = '<carga_id>:<txid>'); UPDATE/DELETE/TRUNCATE nunca.
-- ═══════════════════════════════════════════════════════════════════════════
create schema if not exists odoo_historia;
revoke all on schema odoo_historia from public, anon, authenticated;
grant usage on schema odoo_historia to service_role;
comment on schema odoo_historia is
  'HISTORIA de Odoo al corte del 15-oct-2026 (copia cruda, inmutable, sin PII). NUNCA es existencia actual: el saldo vive en almacen.*';

-- Una fila por carga. Una sola abierta a la vez.
create table if not exists odoo_historia.cargas (
  id                bigint      generated always as identity,
  estado            text        not null default 'abierta',
  respaldo          text        not null,                 -- carpeta del respaldo, sin ruta (hoy 20261015)
  manifiesto_sha256 text        not null,                 -- sha256 de MANIFIESTO.json, el que fija el acta
  corte_utc         timestamptz not null,                 -- 2026-10-15 16:48:47+00 (de corte_bloqueo)
  corte_bloqueo     jsonb       not null,                 -- copia literal de MANIFIESTO.corte_bloqueo
  modelos           jsonb       not null default '{}'::jsonb,   -- por modelo: archivo, sha256_*, obtenidos, filas, fecha_utc, campos_*
  cargador_version  text        not null,
  acta              text        not null,
  abierta_por       text        not null default nullif(current_setting('app.usuario', true), ''),
  abierta_at        timestamptz not null default now(),
  cerrada_por       text,
  cerrada_at        timestamptz,
  motivo_fallida    text,
  constraint cargas_pkey            primary key (id),
  constraint cargas_estado_chk      check (estado in ('abierta', 'cerrada', 'fallida')),
  constraint cargas_respaldo_chk    check (respaldo ~ '^[0-9]{8}$'),
  constraint cargas_manifiesto_chk  check (manifiesto_sha256 ~ '^[0-9a-f]{64}$'),
  constraint cargas_acta_chk        check (length(btrim(acta)) >= 10),
  constraint cargas_version_chk     check (length(btrim(cargador_version)) > 0),
  constraint cargas_bloqueo_chk     check (jsonb_typeof(corte_bloqueo) = 'object'),
  constraint cargas_modelos_chk     check (jsonb_typeof(modelos) = 'object'),
  constraint cargas_cerrada_chk     check ((estado = 'abierta') = (cerrada_at is null)),
  constraint cargas_cerrada_por_chk check ((cerrada_at is null) = (cerrada_por is null)),
  constraint cargas_fallida_chk     check ((estado = 'fallida') = (motivo_fallida is not null))
);
create unique index if not exists cargas_una_abierta_uq on odoo_historia.cargas ((true)) where estado = 'abierta';

-- stock.location (lista blanca). Los many2one llegan como id crudo; false → NULL.
create table if not exists odoo_historia.stock_location (
  carga_id           bigint      not null,
  id                 bigint      not null,
  complete_name      text        not null,     -- ruta; la usa almacen.posicion_odoo()
  name               text,
  location_id        bigint,                   -- padre (CUARENTENA y SCRAP no tienen)
  parent_path        text,
  usage              text        not null,
  warehouse_id       bigint,
  active             boolean     not null,
  scrap_location     boolean,
  return_location    boolean,
  replenish_location boolean,
  company_id         bigint,                   -- id opaco
  barcode            text,
  create_date        timestamptz,              -- Odoo da UTC sin zona: se guarda como UTC
  write_date         timestamptz,
  constraint stock_location_pkey      primary key (carga_id, id),
  constraint stock_location_carga_fk  foreign key (carga_id) references odoo_historia.cargas (id),
  constraint stock_location_usage_chk check (usage in ('view', 'internal', 'supplier', 'customer', 'inventory', 'production', 'transit'))
);

-- product.product (lista blanca): ni precios ni costos ni mensajes.
create table if not exists odoo_historia.product_product (
  carga_id        bigint        not null,
  id              bigint        not null,      -- se liga con core.products.odoo_id
  product_tmpl_id bigint        not null,
  default_code    text,                        -- el SKU tal cual llegó (la siembra aplica btrim)
  name            text,                        -- título (no es PII): solo para diagnosticar sin_codigo
  barcode         text,
  active          boolean       not null,
  volume          numeric(16,6),
  weight          numeric(16,6),
  create_date     timestamptz,
  write_date      timestamptz,
  constraint product_product_pkey     primary key (carga_id, id),
  constraint product_product_carga_fk foreign key (carga_id) references odoo_historia.cargas (id)
);

-- stock.quant (lista blanca). lot_id, package_id y owner_id se VERIFICAN vacíos en el cargador y no se guardan.
create table if not exists odoo_historia.stock_quant (
  carga_id          bigint        not null,
  id                bigint        not null,
  product_id        bigint        not null,
  location_id       bigint        not null,
  quantity          numeric(18,4) not null,
  reserved_quantity numeric(18,4) not null,
  in_date           timestamptz,
  create_date       timestamptz,
  write_date        timestamptz,
  constraint stock_quant_pkey        primary key (carga_id, id),
  constraint stock_quant_carga_fk    foreign key (carga_id) references odoo_historia.cargas (id),
  constraint stock_quant_producto_fk foreign key (carga_id, product_id) references odoo_historia.product_product (carga_id, id),
  constraint stock_quant_ubicacion_fk foreign key (carga_id, location_id) references odoo_historia.stock_location (carga_id, id)
);
create index if not exists stock_quant_producto_ix  on odoo_historia.stock_quant (carga_id, product_id);
create index if not exists stock_quant_ubicacion_ix on odoo_historia.stock_quant (carga_id, location_id);
create index if not exists stock_location_padre_ix  on odoo_historia.stock_location (carga_id, location_id) where location_id is not null;

-- Candado de solo lectura de las tres tablas de datos [T0 §3.3]
create or replace function odoo_historia.tg_solo_lectura() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_estado text;
begin
  if tg_op = 'INSERT' then
    if coalesce(current_setting('odoo_historia.carga', true), '') = new.carga_id || ':' || txid_current() then
      select c.estado into v_estado from odoo_historia.cargas c where c.id = new.carga_id for share;
      if v_estado = 'abierta' then
        return new;
      end if;
    end if;
    raise exception using errcode = '42501', constraint = 'odoo_historia_solo_lectura',
      message = format('odoo_historia.%s: solo se escribe con su carga ABIERTA en esta transacción (odoo_historia.carga = <id>:<txid>)',
                       tg_table_name);
  end if;
  raise exception using errcode = '42501', constraint = 'odoo_historia_solo_lectura',
    message = format('odoo_historia.%s es historia inmutable: %s rechazado', tg_table_name, tg_op);
end $$;

-- Guarda de cargas: nace abierta; solo cambian modelos y estado (abierta → cerrada | fallida); después, inmutable.
create or replace function odoo_historia.tg_cargas_guarda() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare
  v_quien text := nullif(current_setting('app.usuario', true), '');
  v_guc   text := coalesce(current_setting('odoo_historia.carga', true), '');
  m       record;
  v_n     bigint;
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', constraint = 'cargas_sin_borrado',
      message = format('odoo_historia.cargas %s: una carga no se borra (queda cerrada o fallida)', old.id);
  end if;
  if tg_op = 'INSERT' then
    if new.estado <> 'abierta' or new.modelos <> '{}'::jsonb or new.cerrada_at is not null
       or new.cerrada_por is not null or new.motivo_fallida is not null then
      raise exception using errcode = '23514', constraint = 'cargas_alta_chk',
        message = 'una carga nace abierta, con modelos = {} y sin cierre';
    end if;
    if v_quien is null then
      raise exception using errcode = '23514', constraint = 'cargas_quien', message = 'abrir una carga exige app.usuario';
    end if;
    new.abierta_por := v_quien;
    new.abierta_at  := now();
    return new;
  end if;
  -- UPDATE
  if old.estado <> 'abierta' then
    raise exception using errcode = '42501', constraint = 'cargas_inmutable',
      message = format('odoo_historia.cargas %s está %s: es inmutable', old.id, old.estado);
  end if;
  if v_guc <> old.id || ':' || txid_current() then
    raise exception using errcode = '42501', constraint = 'cargas_escritor',
      message = format('odoo_historia.cargas %s: solo la toca el cargador, con odoo_historia.carga = %s:<txid> en esta transacción', old.id, old.id);
  end if;
  if (new.id, new.respaldo, new.manifiesto_sha256, new.corte_utc, new.corte_bloqueo, new.cargador_version,
      new.acta, new.abierta_por, new.abierta_at)
     is distinct from
     (old.id, old.respaldo, old.manifiesto_sha256, old.corte_utc, old.corte_bloqueo, old.cargador_version,
      old.acta, old.abierta_por, old.abierta_at) then
    raise exception using errcode = '42501', constraint = 'cargas_inmutable',
      message = format('odoo_historia.cargas %s: mientras está abierta solo cambian modelos y estado', old.id);
  end if;
  if new.estado = 'abierta' then
    if new.cerrada_por is not null or new.cerrada_at is not null or new.motivo_fallida is not null then
      raise exception using errcode = '42501', constraint = 'cargas_inmutable',
        message = format('odoo_historia.cargas %s: el cierre se anota solo al cerrar', old.id);
    end if;
    return new;
  end if;
  -- Cierre: abierta → cerrada | fallida (el CHECK de estado descarta lo demás)
  if v_quien is null then
    raise exception using errcode = '23514', constraint = 'cargas_quien', message = 'cerrar una carga exige app.usuario';
  end if;
  new.cerrada_por := v_quien;
  new.cerrada_at  := now();
  if new.estado = 'cerrada' then
    for m in select x.modelo, x.tabla
               from (values ('stock.location', 'stock_location'), ('product.product', 'product_product'),
                            ('stock.quant', 'stock_quant')) x(modelo, tabla) loop
      execute format('select count(*) from odoo_historia.%I where carga_id = $1', m.tabla) into v_n using old.id;
      if jsonb_typeof(new.modelos -> m.modelo -> 'filas') is distinct from 'number'
         or jsonb_typeof(new.modelos -> m.modelo -> 'obtenidos') is distinct from 'number'
         or (new.modelos -> m.modelo ->> 'filas')::numeric <> v_n
         or (new.modelos -> m.modelo ->> 'obtenidos')::numeric <> v_n then
        raise exception using errcode = '23514', constraint = 'cargas_cierre_cuadra',
          message = format('carga %s, %s: filas %s y obtenidos %s del cargador; la tabla tiene %s',
                           old.id, m.modelo, coalesce(new.modelos -> m.modelo ->> 'filas', 'NULL'),
                           coalesce(new.modelos -> m.modelo ->> 'obtenidos', 'NULL'), v_n);
      end if;
    end loop;
  end if;
  return new;
end $$;

create trigger cargas_guarda before insert or update or delete on odoo_historia.cargas
  for each row execute function odoo_historia.tg_cargas_guarda();
create trigger cargas_sin_truncate before truncate on odoo_historia.cargas
  for each statement execute function ops.tg_sin_truncate();
create trigger stock_location_solo_lectura before insert or update or delete on odoo_historia.stock_location
  for each row execute function odoo_historia.tg_solo_lectura();
create trigger stock_location_sin_truncate before truncate on odoo_historia.stock_location
  for each statement execute function ops.tg_sin_truncate();
create trigger product_product_solo_lectura before insert or update or delete on odoo_historia.product_product
  for each row execute function odoo_historia.tg_solo_lectura();
create trigger product_product_sin_truncate before truncate on odoo_historia.product_product
  for each statement execute function ops.tg_sin_truncate();
create trigger stock_quant_solo_lectura before insert or update or delete on odoo_historia.stock_quant
  for each row execute function odoo_historia.tg_solo_lectura();
create trigger stock_quant_sin_truncate before truncate on odoo_historia.stock_quant
  for each statement execute function ops.tg_sin_truncate();

-- La foto al corte (solo ubicaciones internal) de las cargas CERRADAS [R seg]: una abierta o fallida no es la foto.
-- Historia, no existencia.
create or replace view odoo_historia.saldo_al_15oct_v with (security_invoker = on) as
select c.id                  as carga_id,
       c.corte_utc,
       q.id                  as quant_id,
       q.product_id          as odoo_product_id,
       p.default_code,
       p.active              as producto_activo,
       q.location_id         as odoo_location_id,
       l.complete_name,
       l.usage,
       l.warehouse_id,
       q.quantity,
       q.reserved_quantity
  from odoo_historia.stock_quant q
  join odoo_historia.cargas c          on c.id = q.carga_id
  join odoo_historia.product_product p on p.carga_id = q.carga_id and p.id = q.product_id
  join odoo_historia.stock_location l  on l.carga_id = q.carga_id and l.id = q.location_id
 where l.usage = 'internal' and c.estado = 'cerrada';
comment on view odoo_historia.saldo_al_15oct_v is 'historia al corte, no existencia';

-- RLS (cero políticas) y grants: solo SELECT a service_role [T0 §3.4]
alter table odoo_historia.cargas          enable row level security;
alter table odoo_historia.stock_location  enable row level security;
alter table odoo_historia.product_product enable row level security;
alter table odoo_historia.stock_quant     enable row level security;
revoke all on odoo_historia.cargas, odoo_historia.stock_location, odoo_historia.product_product,
              odoo_historia.stock_quant, odoo_historia.saldo_al_15oct_v
  from public, anon, authenticated, service_role;
grant select on odoo_historia.cargas, odoo_historia.stock_location, odoo_historia.product_product,
               odoo_historia.stock_quant, odoo_historia.saldo_al_15oct_v to service_role;
revoke all on function odoo_historia.tg_solo_lectura(), odoo_historia.tg_cargas_guarda()
  from public, anon, authenticated, service_role;
alter table odoo_historia.cargas          enable always trigger cargas_guarda,
                                         enable always trigger cargas_sin_truncate;
alter table odoo_historia.stock_location  enable always trigger stock_location_solo_lectura,
                                         enable always trigger stock_location_sin_truncate;
alter table odoo_historia.product_product enable always trigger product_product_solo_lectura,
                                         enable always trigger product_product_sin_truncate;
alter table odoo_historia.stock_quant     enable always trigger stock_quant_solo_lectura,
                                         enable always trigger stock_quant_sin_truncate;
comment on table odoo_historia.cargas is
  'Una fila por carga de la copia de Odoo. Solo una abierta; cerrada o fallida es inmutable. Cerrar exige filas = obtenidos por modelo.';
comment on table odoo_historia.stock_quant is
  'stock.quant crudo al corte (lista blanca). lot_id, package_id y owner_id se verificaron vacíos y no se guardan.';

-- A9. Registro (T5) y paso final que falla cerrado (S8)
insert into ops.migraciones (migracion, detalle)
select '0075_almacen_ubicaciones_y_odoo_historia',                -- [R corr] = nombre del archivo (aplicar_migraciones.py)
       jsonb_build_object('tablas', 4, 'vistas', 4, 'tipos', 9,
                          'odoo_historia', jsonb_build_object('tablas', 4, 'vistas', 1))
 where to_regclass('ops.migraciones') is not null
   and not exists (select 1 from ops.migraciones where migracion = '0075_almacen_ubicaciones_y_odoo_historia');

do $$ begin
  -- odoo_historia [T0 §3.5]: RLS, invoker, sin grants a la API (ni USAGE del esquema), funciones sin
  -- SECURITY DEFINER ni EXECUTE para la API, triggers en ALWAYS y service_role solo con SELECT.
  if (select count(*) from pg_class c where c.relnamespace = 'odoo_historia'::regnamespace and c.relkind = 'r') <> 4
  or exists (select 1 from pg_class c where c.relnamespace = 'odoo_historia'::regnamespace and c.relkind in ('r','p') and not c.relrowsecurity)
  or exists (select 1 from pg_class c where c.relnamespace = 'odoo_historia'::regnamespace and c.relkind = 'v'
              and not coalesce(c.reloptions && array['security_invoker=on','security_invoker=true'], false))
  or exists (select 1 from information_schema.role_table_grants
              where table_schema = 'odoo_historia' and grantee in ('anon','authenticated','PUBLIC'))
  or exists (select 1 from pg_class c, unnest(array['anon','authenticated']) r(rol)
              where c.relnamespace = 'odoo_historia'::regnamespace and c.relkind in ('r','v')
                and has_table_privilege(r.rol, c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))
  or has_schema_privilege('anon', 'odoo_historia', 'USAGE') or has_schema_privilege('authenticated', 'odoo_historia', 'USAGE')
  or exists (select 1 from pg_proc p where p.pronamespace = 'odoo_historia'::regnamespace
              and (p.prosecdef or p.proacl is null
                   or exists (select 1 from aclexplode(p.proacl) x
                               where x.privilege_type = 'EXECUTE'
                                 and x.grantee in (0, 'anon'::regrole, 'authenticated'::regrole, 'service_role'::regrole))))
  or exists (select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
              where c.relnamespace = 'odoo_historia'::regnamespace and not t.tgisinternal and t.tgenabled <> 'A')
  or exists (select 1 from unnest(array['odoo_historia.cargas','odoo_historia.stock_location',
                                         'odoo_historia.product_product','odoo_historia.stock_quant']) t
              where has_table_privilege('service_role', t, 'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER'))
  then
    raise exception using errcode = 'KB000',
      message = 'A′: candado incompleto en odoo_historia (RLS / invoker / grants / funciones / ALWAYS)';
  end if;
end $$;

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
  or exists (select 1 from unnest(array['almacen.ubicacion_tipos_hist','almacen.historial_movimientos',
                                         'almacen.stock_mov','almacen.almacenes_hist']) t
              where has_table_privilege('service_role', t, 'UPDATE,DELETE,TRUNCATE,TRIGGER'))
  or exists (select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
              where c.relnamespace = 'almacen'::regnamespace and not t.tgisinternal and t.tgenabled <> 'A'
                and t.tgname <> all (array['locations_tocar_trg','historial_movimientos_tocar_trg','ubicaciones_tocar']))
  then
    raise exception using errcode = 'KB000',
      message = 'A′: candado incompleto (RLS / invoker / grants / funciones / ALWAYS); revisar S1–S8';
  end if;
end $$;
commit;
