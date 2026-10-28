-- ═══════════════════════════════════════════════════════════════════════════
-- B′ · 0076 (T0, SANDBOX; a producción solo con acta) — una sola fuente por bodega
--     y el corte de T0 desde la COPIA FINAL de Odoo (odoo_historia), en un acto.
-- Parte de la B_0075 probada el 13-oct. Cambia solo lo que pide SPEC_T0 §4:
--   · se conservan B1 (desprendimientos + foto), B2, B3, B4, B5 congelar_bodega, B6, B7 y B8;
--   · se RETIRAN desprendimiento_libre_odoo, desprender_bodega, revertir_desprendimiento,
--     la excepción R1 de stock_mov y el GUC 'revertir:<COD>' (después de T0 la reversa es el
--     ROLLBACK de la transacción; luego, ajustes en el libro);
--   · NUEVO: desprender_desde_copia (TEXCO, TEX2 y DROP en un acto; DROP preferencia 3, D5),
--     saldo_inicial_ajustes (14 reglas cerradas), catálogo desde la copia (posicion_odoo,
--     clasificar_ubicacion_odoo), D16 fisico_no_vendible y el tipo por_verificar [D4 PENDIENTE DE FIRMA],
--     migrada_ov [D4 PENDIENTE DE FIRMA], OV espejo (ov_espejo, ov_entregar, t0_ov_espejos),
--     cuadre por partidas contra Woo (t0_cuadre), SKUs sin libro [D4 FIRMADO 20-oct] y el
--     arreglo del «SKU vendido hasta 0» en existencias_bodega_v (§4.10, N88).
--   · [D4 FIRMADO 20-oct, Eduardo] Los SKUs que Woo no gestiona (gestiona=false), los que no están en Woo
--     y los que el libro no conoce SALEN EN 0 salvo una lista aprobada. Cómo:
--       - con piezas: entran al libro en POR VERIFICAR en el desprendimiento (regla nueva
--         fuera_de_woo_por_verificar de saldo_inicial_ajustes); los de la lista aprobada conservan su vendible;
--       - sin libro: t0_conocer_skus los da de alta con 0 (a_cero);
--       - la lista vive en almacen.t0_skus_sin_libro (decision 'lista_aprobada', columna grupo), NUNCA en stock_libro;
--       - t0_cuadre falla cerrado (t0_d4_fuera_de_woo, t0_d4_sin_decision, t0_d4_ajuste_no_cuadra).
--   · [R 20/21-oct] Correcciones de la revisión de seguridad, corrección y operación (INFORME_T0.md):
--       puerta del desprendimiento atada a desprender_desde_copia y cuadrada al COMMIT; saldo_inicial solo
--       dentro del acto; el tipo de una ubicación con piezas no cruza vendible/no vendible ni POR VERIFICAR
--       (reclasificar_ubicacion + ubicaciones_hist); locations cuadra también lo no vendible; D16 deja pasar
--       mermas reales; la lista D4 firmada (conteo + sha256), su grupo y «aprobado con número» en t0_cuadre;
--       ventas posteriores a la foto de Woo fuera del cuadre; L4 sin tipo por omisión; auditoría DDL ampliada.
-- Marcas: [S#] revisión de seguridad · [T#] revisión técnica · [T0 §x] contrato SPEC_T0 · [D4] pendiente de firma.
-- ═══════════════════════════════════════════════════════════════════════════
begin;
set local lock_timeout = '5s';
set local statement_timeout = '120s';

do $$ begin
  if to_regclass('almacen.ubicaciones') is null or to_regclass('almacen.copias_odoo') is null
     or to_regclass('odoo_historia.cargas') is null then
    raise exception using errcode = 'KB000', message = 'B′: falta la A′ (0075)';
  end if;
end $$;

-- ───────────────────────────────────────────────────────────────────────────
-- B1. Desprendimientos y su foto. [T0 §4.1] Columnas nuevas carga_id, lote y fisico_no_vendible:
--     la guarda las trata como totales (solo se escriben en el paso congelada → hecha).
--     desprendimiento_libre_odoo SE RETIRA (sería tautológica: sale de la misma copia).
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists almacen.desprendimientos (
  id                 bigint      generated always as identity,
  almacen            text        not null,
  de_fuente          text        not null,
  a_fuente           text        not null,
  estado             text        not null default 'congelada',
  congelado_at       timestamptz not null,
  acta               text        not null,
  quien              text        not null default coalesce(nullif(current_setting('app.usuario', true), ''), current_user),
  odoo_warehouse_id  integer,
  revierte_a         bigint,                     -- inerte desde B′: la reversa R1 se retiró
  txid               bigint,                     -- LA PUERTA: solo se pone en el paso congelada → hecha [S4-b]
  foto_odoo_at       timestamptz,
  renglones          integer,
  skus               integer,
  fisico             bigint,
  reservado          bigint,
  hecho_at           timestamptz,
  revertido_at       timestamptz,
  odoo_vaciado_at    timestamptz,
  odoo_vaciado_ref   text,
  carga_id           bigint,                     -- [T0] odoo_historia.cargas de la que se sembró
  lote               text,                       -- [T0] lote de saldo_inicial_ajustes aplicado
  fisico_no_vendible bigint,                     -- [T0, D16] lo sembrado en ubicaciones que no cuentan para venta
  creado_at          timestamptz not null default now(),
  constraint desprendimientos_pkey       primary key (id),
  constraint desprendimientos_alm_fk     foreign key (almacen) references almacen.almacenes (codigo),
  constraint desprendimientos_rev_fk     foreign key (revierte_a) references almacen.desprendimientos (id),
  constraint desprendimientos_carga_fk   foreign key (carga_id) references odoo_historia.cargas (id),
  constraint desprendimientos_fuente_chk check (de_fuente in ('odoo','kubera') and a_fuente in ('odoo','kubera') and de_fuente <> a_fuente),
  constraint desprendimientos_estado_chk check (estado in ('congelada','hecha','cancelada','revertida')),
  constraint desprendimientos_acta_chk   check (length(btrim(acta)) >= 10),
  constraint desprendimientos_rev_chk    check ((revierte_a is null) = (de_fuente = 'odoo')),
  constraint desprendimientos_hecho_chk  check ((estado in ('hecha','revertida')) = (hecho_at is not null)),
  constraint desprendimientos_txid_chk   check ((estado in ('hecha','revertida')) = (txid is not null))
);
create unique index if not exists desprendimientos_abierto_uq on almacen.desprendimientos (almacen) where estado = 'congelada';
create index if not exists desprendimientos_txid_ix on almacen.desprendimientos (almacen, txid) where txid is not null;
create unique index if not exists desprendimientos_lote_uq on almacen.desprendimientos (lote, almacen) where lote is not null;

-- Guarda de desprendimientos [S4-b]: el txid (la llave de las puertas de B2, B3 y B4) solo nace en el
-- paso congelada → hecha, en el mismo UPDATE; después todo es inmutable salvo cancelar y el vaciado.
create or replace function almacen.tg_desprendimientos_guarda() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', constraint = 'desprendimientos_sin_borrado',
      message = format('desprendimiento %s: no se borra (se cancela)', old.id);
  end if;
  if tg_op = 'INSERT' then
    if new.revierte_a is not null then                                                     -- [T0] R1 retirada
      raise exception using errcode = '42501', constraint = 'desprendimientos_reversa_chk',
        message = 'la reversa R1 se retiró en la 0076: después de T0 se corrige con ajustes en el libro';
    end if;
    if new.estado <> 'congelada' or new.txid is not null or new.hecho_at is not null or new.revertido_at is not null
       or new.odoo_vaciado_at is not null or new.odoo_vaciado_ref is not null or new.foto_odoo_at is not null
       or new.carga_id is not null or new.lote is not null or new.fisico_no_vendible is not null then
      raise exception using errcode = '23514', constraint = 'desprendimientos_alta_chk',
        message = 'un desprendimiento nace congelada, sin txid ni totales';
    end if;
    return new;
  end if;
  -- UPDATE
  if (new.id, new.almacen, new.de_fuente, new.a_fuente, new.congelado_at, new.acta, new.quien, new.revierte_a, new.creado_at)
     is distinct from
     (old.id, old.almacen, old.de_fuente, old.a_fuente, old.congelado_at, old.acta, old.quien, old.revierte_a, old.creado_at) then
    raise exception using errcode = '42501', constraint = 'desprendimientos_inmutable',
      message = format('desprendimiento %s: almacen, fuentes, congelado_at, acta, quien y revierte_a son fijos', old.id);
  end if;
  if old.estado = 'congelada' and new.estado = 'hecha' then
    if old.txid is not null or new.txid is distinct from txid_current() then
      raise exception using errcode = '42501', constraint = 'desprendimientos_txid',
        message = format('desprendimiento %s: el txid solo se pone, con el de ESTA transacción, al pasar a hecha', old.id);
    end if;
    -- [R seg] La puerta solo la abre desprender_desde_copia: su marca de esta transacción, una carga cerrada y los
    -- totales completos. Al COMMIT, desprendimientos_hecho_cuadra exige foto = saldo_inicial = totales.
    if coalesce(current_setting('app.t0_acto', true), '') <> 'desprender_desde_copia:' || txid_current() then
      raise exception using errcode = '42501', constraint = 'desprendimientos_solo_acto',
        message = format('desprendimiento %s: solo almacen.desprender_desde_copia lo pasa a hecha', old.id);
    end if;
    if new.carga_id is null or new.lote is null or new.renglones is null or new.skus is null or new.fisico is null
       or new.fisico_no_vendible is null or new.reservado is null or new.foto_odoo_at is null or new.hecho_at is null
       or not exists (select 1 from odoo_historia.cargas c where c.id = new.carga_id and c.estado = 'cerrada') then
      raise exception using errcode = '23514', constraint = 'desprendimientos_hecha_incompleta',
        message = format('desprendimiento %s: pasar a hecha exige carga cerrada, lote y totales', old.id);
    end if;
    return new;
  end if;
  if new.txid is distinct from old.txid
     or (new.foto_odoo_at, new.renglones, new.skus, new.fisico, new.reservado, new.odoo_warehouse_id, new.hecho_at,
         new.carga_id, new.lote, new.fisico_no_vendible)
        is distinct from
        (old.foto_odoo_at, old.renglones, old.skus, old.fisico, old.reservado, old.odoo_warehouse_id, old.hecho_at,
         old.carga_id, old.lote, old.fisico_no_vendible) then
    raise exception using errcode = '42501', constraint = 'desprendimientos_txid',
      message = format('desprendimiento %s: txid y totales solo cambian en el paso congelada → hecha', old.id);
  end if;
  if new.estado is distinct from old.estado and not (old.estado = 'congelada' and new.estado = 'cancelada') then
    raise exception using errcode = '23514', constraint = 'desprendimientos_transicion',
      message = format('desprendimiento %s: %s → %s no es una transición válida', old.id, old.estado, new.estado);
  end if;
  if new.revertido_at is distinct from old.revertido_at then
    raise exception using errcode = '23514', constraint = 'desprendimientos_transicion',
      message = 'revertido_at no cambia: la reversa R1 se retiró en la 0076';
  end if;
  if (old.odoo_vaciado_at is not null and new.odoo_vaciado_at is distinct from old.odoo_vaciado_at)
     or (old.odoo_vaciado_ref is not null and new.odoo_vaciado_ref is distinct from old.odoo_vaciado_ref) then
    raise exception using errcode = '42501', constraint = 'desprendimientos_vaciado_una_vez', message = 'el vaciado se anota una vez';
  end if;
  return new;
end $$;
create trigger desprendimientos_guarda before insert or update or delete on almacen.desprendimientos
  for each row execute function almacen.tg_desprendimientos_guarda();
create trigger desprendimientos_sin_truncate before truncate on almacen.desprendimientos          -- [S7]
  for each statement execute function ops.tg_sin_truncate();

create table if not exists almacen.desprendimiento_foto (
  desprendimiento_id bigint  not null,
  sentido            text    not null,          -- saldo_inicial (Odoo → kubera); 'regreso' quedó sin escritor (R1 retirada)
  sku                citext  not null,
  ubicacion_id       bigint  not null,
  ubicacion          text    not null,
  fisico             integer not null,          -- [T0] lo SEMBRADO (crudos + ajustes) por (SKU, ubicación)
  reservado          integer not null,          -- [T0] la reserva CRUDA de Odoo: solo informativa
  odoo_quant_id      bigint,                    -- [T0] NULL: el renglón agrega quants
  odoo_product_id    integer,
  constraint desprendimiento_foto_pkey primary key (desprendimiento_id, sku, ubicacion_id),
  constraint desprendimiento_foto_d_fk foreign key (desprendimiento_id) references almacen.desprendimientos (id),
  constraint desprendimiento_foto_u_fk foreign key (ubicacion_id) references almacen.ubicaciones (id),
  constraint desprendimiento_foto_sentido_chk check (sentido in ('saldo_inicial','regreso'))
);
-- Solo la escribe desprender_desde_copia, dentro de SU transacción (la puerta con txid). [S4]
create or replace function almacen.tg_desprendimiento_foto_guarda() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_alm text;
begin
  select d.almacen into v_alm from almacen.desprendimientos d
   where d.id = new.desprendimiento_id and d.txid = txid_current();
  if v_alm is null or coalesce(current_setting('app.almacen_escritor', true), '') <> 'desprender:' || v_alm then
    raise exception using errcode = '42501', constraint = 'desprendimiento_foto_escritor',
      message = 'desprendimiento_foto solo la escribe almacen.desprender_desde_copia';
  end if;
  return new;
end $$;
create trigger desprendimiento_foto_guarda before insert on almacen.desprendimiento_foto
  for each row execute function almacen.tg_desprendimiento_foto_guarda();
create trigger desprendimiento_foto_solo_agregar before update or delete on almacen.desprendimiento_foto
  for each row execute function ops.tg_solo_agregar();
create trigger desprendimiento_foto_sin_truncate before truncate on almacen.desprendimiento_foto
  for each statement execute function ops.tg_sin_truncate();

-- [R seg] Al COMMIT, un desprendimiento hecho cuadra: foto = saldo_inicial (DESP-<id>) renglón por renglón,
-- Σ foto = fisico, renglones = filas de la foto, la bodega ya es kubera y sin copia de Odoo.
create or replace function almacen.tg_desprendimientos_hecho_cuadra() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_n bigint; v_s bigint; v_r bigint;
begin
  select count(*) into v_n
    from (select f.sku, f.ubicacion_id, f.fisico from almacen.desprendimiento_foto f where f.desprendimiento_id = new.id) f
    full join (select m.sku, m.ubicacion_id, m.almacen, sum(m.delta) as d, count(*) as n from almacen.stock_mov m
                where m.motivo = 'saldo_inicial' and m.ref = 'DESP-' || new.id group by 1, 2, 3) m
      on m.sku = f.sku and m.ubicacion_id = f.ubicacion_id
   where f.sku is null or m.sku is null or m.d <> f.fisico or m.n <> 1 or m.almacen <> new.almacen;
  select coalesce(sum(f.fisico), 0), count(*) into v_s, v_r from almacen.desprendimiento_foto f where f.desprendimiento_id = new.id;
  if v_n > 0 or v_s <> coalesce(new.fisico, -1) or v_r <> coalesce(new.renglones, -1)
     or not exists (select 1 from almacen.almacenes a where a.codigo = new.almacen and a.fuente = 'kubera')
     or exists (select 1 from almacen.copias_odoo c where c.almacen = new.almacen) then
    raise exception using errcode = '23514', constraint = 'desprendimientos_hecho_cuadra',
      message = format('desprendimiento %s (%s): foto, saldo_inicial y totales no cuadran (%s renglones distintos; foto %s pzs/%s renglones; dice %s/%s)',
                       new.id, new.almacen, v_n, v_s, v_r, new.fisico, new.renglones);
  end if;
  return null;
end $$;
create constraint trigger desprendimientos_hecho_cuadra after update on almacen.desprendimientos
  deferrable initially deferred for each row when (old.estado = 'congelada' and new.estado = 'hecha')
  execute function almacen.tg_desprendimientos_hecho_cuadra();

-- ───────────────────────────────────────────────────────────────────────────
-- B2. Guarda de ESCRITOR por fuente [S1-b, S4-a, S5-a]
--   GUC app.almacen_escritor, SIEMPRE atado a la transacción:
--     'copia_odoo:<txid>' (la copia de Odoo; inerte tras P2) · 'libro:<txid>' (lo pone el trigger de stock_mov)
--     'desprender:<COD>' (desprender_desde_copia; abre solo con su desprendimiento de ESTE txid)
--   [T0 §2.2] 'revertir:<COD>' desaparece con la reversa R1.
--   Args: columna de la bodega, columna de la fuente ('' = tomarla de almacenes).
-- ───────────────────────────────────────────────────────────────────────────
create or replace function almacen.tg_escritor_por_fuente() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare
  r        jsonb := to_jsonb(case when tg_op = 'DELETE' then old else new end);
  o        jsonb := case when tg_op = 'UPDATE' then to_jsonb(old) end;
  v_esc    text  := coalesce(current_setting('app.almacen_escritor', true), '');
  v_alm    text  := r ->> tg_argv[0];
  v_col_f  text  := nullif(tg_argv[1], '');
  v_fuente text;
  v_copia  constant text := 'copia_odoo:' || txid_current();          -- [S5-a]
  v_libro  constant text := 'libro:' || txid_current();               -- [S5-a]
begin
  v_fuente := case when v_col_f is null then (select a.fuente from almacen.almacenes a where a.codigo = v_alm)
                   else r ->> v_col_f end;
  -- La puerta del acta, atada al desprendimiento de ESTA transacción [S4-a]
  if v_esc = 'desprender:' || v_alm
     and exists (select 1 from almacen.desprendimientos d where d.almacen = v_alm and d.txid = txid_current()) then
    return case when tg_op = 'DELETE' then old else new end;
  end if;
  if tg_op = 'UPDATE' and (o ->> tg_argv[0], o ->> coalesce(v_col_f, '-'), o ->> 'sku')
                          is distinct from (v_alm, r ->> coalesce(v_col_f, '-'), r ->> 'sku') then
    raise exception using errcode = '42501', constraint = tg_table_name || '_llave_fija',
      message = format('%s: bodega, fuente y sku de un renglón son fijos', tg_table_name);
  end if;
  if v_fuente = 'odoo' and v_esc <> v_copia then
    raise exception using errcode = '42501', constraint = tg_table_name || '_solo_copiador',
      message = format('%s de %s es COPIA de Odoo: solo la escribe la copia (app.almacen_escritor = copia_odoo:<txid>)',
                       tg_table_name, v_alm);
  end if;
  if v_fuente = 'kubera' and tg_table_name = 'locations' and v_esc <> v_libro then
    raise exception using errcode = '42501', constraint = 'locations_solo_libro',
      message = format('locations de %s es el saldo de kubera: solo lo escribe el libro (stock_mov)', v_alm);
  end if;
  if v_fuente = 'kubera' and v_esc = v_copia then
    raise exception using errcode = '42501', constraint = tg_table_name || '_copiador_fuera',
      message = format('%s ya es de kubera: la copia de Odoo no la toca', v_alm);
  end if;
  -- [S1-b] La FK compuesta no resiste session_replication_role = replica: se repite aquí, en ALWAYS.
  if tg_op <> 'DELETE' then
    perform 1 from almacen.almacenes a where a.codigo = v_alm and a.fuente = v_fuente for key share;
    if not found then
      raise exception using errcode = '23503', constraint = tg_table_name || '_fuente_always',
        message = format('%s: %s no es de fuente %s (la FK pudo saltarse con session_replication_role)',
                         tg_table_name, v_alm, v_fuente);
    end if;
  end if;
  return case when tg_op = 'DELETE' then old else new end;
end $$;
create trigger locations_escritor before insert or update or delete on almacen.locations
  for each row execute function almacen.tg_escritor_por_fuente('cedis', 'origen');
create trigger ubicaciones_escritor before insert or update or delete on almacen.ubicaciones
  for each row execute function almacen.tg_escritor_por_fuente('almacen', 'fuente');
create trigger copias_odoo_escritor before insert or update or delete on almacen.copias_odoo
  for each row execute function almacen.tg_escritor_por_fuente('almacen', 'fuente');
create trigger historial_movimientos_escritor before insert or update or delete on almacen.historial_movimientos
  for each row execute function almacen.tg_escritor_por_fuente('cedis', '');

-- ───────────────────────────────────────────────────────────────────────────
-- B3. almacenes: la fuente cambia SOLO dentro de la transacción de un desprendimiento.
--     Igual a la 0064/0068 salvo el bloque de codigo/fuente. Conserva el NOMBRE
--     almacenes_codigo_fuente_fijos (verificar_0064_0065.py y la guía de la 0064) [T3].
-- ───────────────────────────────────────────────────────────────────────────
create or replace function ops.tg_almacenes_guarda() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $function$          -- [S3]
declare
  v_quien text := nullif(current_setting('app.usuario', true), '');
  v_esc   text := coalesce(current_setting('app.almacen_escritor', true), '');
begin
  if tg_op = 'INSERT' then
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
  if new.codigo is distinct from old.codigo then
    raise exception using errcode = '42501', constraint = 'almacenes_codigo_fuente_fijos',
      message = format('almacen.almacenes: el codigo de %s es fijo', old.codigo);
  end if;
  if new.fuente is distinct from old.fuente then
    if v_esc <> 'desprender:' || old.codigo
       or not exists (select 1 from almacen.desprendimientos d
                       where d.almacen = old.codigo and d.txid = txid_current()
                         and d.de_fuente = old.fuente and d.a_fuente = new.fuente) then
      raise exception using errcode = '42501', constraint = 'almacenes_codigo_fuente_fijos',
        message = format('almacen.almacenes: la fuente de %s solo cambia con almacen.desprender_desde_copia (acta de T0)', old.codigo);
    end if;
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
end $function$;

-- ───────────────────────────────────────────────────────────────────────────
-- B4. stock_mov: ubicación, saldo_inicial, reubicación y el renglón kubera de locations
-- ───────────────────────────────────────────────────────────────────────────
alter table almacen.stock_mov add column if not exists ubicacion_id bigint;
alter table almacen.stock_mov
  add constraint stock_mov_ubicacion_fk foreign key (ubicacion_id, almacen)
      references almacen.ubicaciones (id, almacen) deferrable initially immediate,
  add constraint stock_mov_ubicacion_chk check (ubicacion_id is not null) not valid;   -- los renglones viejos de ENSAYO quedan
alter table almacen.stock_mov drop constraint stock_mov_motivo_chk,
  add constraint stock_mov_motivo_chk check (motivo in ('entrada','salida_ov','traspaso_salida','traspaso_entrada',
     'devolucion','ajuste_conteo','merma','correccion','saldo_inicial','reubicacion'));
alter table almacen.stock_mov drop constraint stock_mov_signo_chk,
  add constraint stock_mov_signo_chk check (
       (motivo in ('entrada','traspaso_entrada','devolucion','saldo_inicial') and delta > 0)
    or (motivo in ('salida_ov','traspaso_salida','merma') and delta < 0)
    or  motivo in ('ajuste_conteo','correccion','reubicacion'));
-- (C′ la reemplaza: quita fmt: y agrega rec:, retiro_full:, devret: y devord:)
alter table almacen.stock_mov drop constraint stock_mov_clave_chk,                                     -- [T: ramas escritas]
  add constraint stock_mov_clave_chk check (case motivo
      when 'entrada'          then clave ~ '^(fmt:[0-9]+:.+|dev:[0-9]+:recibe)$'
      when 'salida_ov'        then clave ~ '^ov:[0-9]+:linea:[0-9]+:salida$'
      when 'traspaso_salida'  then clave ~ '^(dev:[0-9]+|tras:.+):sale$'
      when 'traspaso_entrada' then clave ~ '^(dev:[0-9]+|tras:.+):entra$'
      when 'devolucion'       then clave ~ '^dev:[0-9]+:recibe$'
      when 'ajuste_conteo'    then clave ~ '^conteo:[^:]+:[^:]+:[^:]+$'
      when 'merma'            then clave ~ '^(dev:[0-9]+:merma|merma:.+)$'
      when 'correccion'       then clave ~ '^corr:.+$'
      when 'saldo_inicial'    then clave ~ '^ini:[0-9]+:[0-9]+:.+$'
      when 'reubicacion'      then clave ~ '^reub:.+:(sale|entra)$'
      else null end),
  add constraint stock_mov_saldo_inicial_ref_chk check (motivo <> 'saldo_inicial' or ref ~ '^DESP-[0-9]+$');

-- B4.1 D16 · lo no vendible dentro de una bodega [T0 §4.2] [D4 PENDIENTE DE FIRMA]
alter table almacen.stock_almacen add column if not exists fisico_no_vendible integer not null default 0;
-- PostgreSQL 17.6: cambia la expresión sin tirar la columna (ops.stock_almacen depende de ella)
alter table almacen.stock_almacen alter column libre set expression as (fisico - fisico_no_vendible - apartado);
comment on column almacen.stock_almacen.fisico_no_vendible is
  'D16: Σ piezas del SKU en ubicaciones de la bodega cuyo tipo no cuenta para venta (cuarentena, scrap, por_verificar…). Lo mantiene el libro.';
comment on column almacen.stock_almacen.libre is
  'fisico − fisico_no_vendible − apartado (D16, 0076). Lo apartado nunca queda sobre cuarentena, scrap o por_verificar.';

-- La guarda de apartado de la 0064: cambia UNA línea (libre con D16). El resto, idéntico.
create or replace function ops.tg_stock_almacen_guarda() returns trigger
language plpgsql set search_path = pg_catalog, public, extensions as $function$
begin
  if tg_op = 'UPDATE' and (new.sku, new.almacen, new.fuente) is distinct from (old.sku, old.almacen, old.fuente) then
    raise exception using errcode = '42501', constraint = 'stock_almacen_llave_fija',
      message = format('stock_almacen %s/%s: sku y bodega son fijos (un traspaso son dos movimientos)', old.sku, old.almacen);
  end if;
  if new.apartado > coalesce(old.apartado, 0) then
    if new.fisico - new.fisico_no_vendible - new.apartado < 0 then          -- `libre` todavía no está calculado en un BEFORE (D16, 0076)
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
end $function$;

-- B4.2 Tipo por_verificar [T0 §4.3] [D4 PENDIENTE DE FIRMA]
insert into almacen.ubicacion_tipos (tipo, nombre, cuenta_para_venta, descripcion, actualizado_por, motivo) values
  ('por_verificar', 'Por verificar', false, 'Excedente dudoso de T0 y lo no contado: no se vende hasta un conteo',
   'migracion_0076', 'D4: excedente de la siembra (+24,028 pzs) y L4 sin clasificar; solo sale por conteo')
on conflict (tipo) do nothing;
alter table almacen.ubicacion_tipos add constraint ubicacion_tipos_por_verificar_chk
  check (tipo <> 'por_verificar' or not cuenta_para_venta);

-- B4.3 BEFORE: sin ubicación declarada → la «SIN UBICAR» de su bodega; de POR VERIFICAR solo se sale con conteo o merma.
--      AFTER: mantiene el renglón kubera de locations y, con D16, stock_almacen.fisico_no_vendible.
create or replace function almacen.tg_stock_mov_ubicacion() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_prev text := coalesce(current_setting('app.almacen_escritor', true), ''); v_pos text; v_vende boolean;
begin
  if tg_when = 'BEFORE' then
    if new.ubicacion_id is null then
      select u.id into new.ubicacion_id from almacen.ubicaciones u
       where u.almacen = new.almacen and u.activa and u.posicion = 'SIN UBICAR';
    end if;
    if new.delta < 0 and new.motivo not in ('ajuste_conteo', 'merma')                         -- [T0 §4.3]
       and exists (select 1 from almacen.ubicaciones u where u.id = new.ubicacion_id and u.tipo = 'por_verificar') then
      raise exception using errcode = '23514', constraint = 'por_verificar_solo_conteo',
        message = format('%s/%s: de POR VERIFICAR solo se sale con ajuste_conteo (en par, con folio) o merma; %s rechazado',
                         new.sku, new.almacen, new.motivo);
    end if;
    -- [R seg] saldo_inicial solo nace dentro del acto: DESP-<id> de un desprendimiento hecho EN ESTA transacción,
    -- de la misma bodega, con la clave ini:<id>:… del mismo desprendimiento.
    if new.motivo = 'saldo_inicial'
       and not exists (select 1 from almacen.desprendimientos d
                        where new.ref = 'DESP-' || d.id and d.almacen = new.almacen and d.estado = 'hecha'
                          and d.txid = txid_current() and split_part(new.clave, ':', 2) = d.id::text) then
      raise exception using errcode = '42501', constraint = 'stock_mov_saldo_inicial_fuera_del_acto',
        message = format('%s/%s: saldo_inicial %s solo lo escribe desprender_desde_copia en la transacción del acto', new.sku, new.almacen, new.ref);
    end if;
    return new;
  end if;
  select u.posicion, t.cuenta_para_venta into v_pos, v_vende
    from almacen.ubicaciones u join almacen.ubicacion_tipos t on t.tipo = u.tipo
   where u.id = new.ubicacion_id;
  perform set_config('app.almacen_escritor', 'libro:' || txid_current(), true);                       -- [S5-a]
  insert into almacen.locations as l (cedis, sku, ubicacion, ubicacion_id, piezas, origen)
  values (new.almacen, new.sku, v_pos, new.ubicacion_id, new.delta, 'kubera')
  on conflict (cedis, sku, ubicacion_id) where ubicacion_id is not null
  do update set piezas = l.piezas + excluded.piezas;
  delete from almacen.locations
   where cedis = new.almacen and sku = new.sku and ubicacion_id = new.ubicacion_id and origen = 'kubera' and piezas = 0;
  perform set_config('app.almacen_escritor', v_prev, true);
  if v_vende is false then                                                                             -- [T0 §4.2 D16]
    update almacen.stock_almacen set fisico_no_vendible = fisico_no_vendible + new.delta
     where sku = new.sku and almacen = new.almacen;
    if not found then
      raise exception using errcode = '23514', constraint = 'stock_almacen_sin_fila',
        message = format('%s/%s: movimiento en una ubicación no vendible sin su fila de stock_almacen', new.sku, new.almacen);
    end if;
  end if;
  return null;
end $$;
create trigger stock_mov_ubicacion_antes before insert on almacen.stock_mov
  for each row execute function almacen.tg_stock_mov_ubicacion();
create trigger stock_mov_ubicacion_despues after insert on almacen.stock_mov
  for each row execute function almacen.tg_stock_mov_ubicacion();

-- Al COMMIT, desde el libro: Σ locations kubera de (sku, bodega) = stock_almacen.fisico
-- y [T0 D16] Σ en ubicaciones no vendibles = stock_almacen.fisico_no_vendible.
create or replace function almacen.tg_ubicaciones_cuadran() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_ubi bigint; v_nv bigint; v_fis integer; v_fnv integer;
begin
  select coalesce(sum(l.piezas), 0), coalesce(sum(l.piezas) filter (where t.cuenta_para_venta is false), 0)
    into v_ubi, v_nv
    from almacen.locations l
    left join almacen.ubicaciones u     on u.id = l.ubicacion_id
    left join almacen.ubicacion_tipos t on t.tipo = u.tipo
   where l.cedis = new.almacen and l.sku = new.sku and l.origen = 'kubera';
  select s.fisico, s.fisico_no_vendible into v_fis, v_fnv
    from almacen.stock_almacen s where s.almacen = new.almacen and s.sku = new.sku;
  if v_ubi <> coalesce(v_fis, 0) then
    raise exception using errcode = '23514', constraint = 'stock_ubicaciones_cuadran',
      message = format('ubicaciones de %s/%s suman %s y el saldo dice %s', new.sku, new.almacen, v_ubi, coalesce(v_fis, 0));
  end if;
  if v_nv <> coalesce(v_fnv, 0) then
    raise exception using errcode = '23514', constraint = 'stock_almacen_no_vendible_cuadra',
      message = format('%s/%s: ubicaciones no vendibles suman %s y fisico_no_vendible dice %s', new.sku, new.almacen, v_nv, coalesce(v_fnv, 0));
  end if;
  return null;
end $$;
create constraint trigger stock_mov_ubicaciones_cuadran after insert on almacen.stock_mov
  deferrable initially deferred for each row execute function almacen.tg_ubicaciones_cuadran();

-- Al COMMIT, desde las ubicaciones [S5-b, R seg]: un cambio de locations kubera hecho a mano con 'libro:<txid>'
-- tiene que dejar (1) Σ piezas = stock_almacen.fisico y (2) Σ en ubicaciones no vendibles = fisico_no_vendible.
-- Así un traslado a mano de POR VERIFICAR (o cuarentena) a un rack falla al COMMIT. Lo que NO ve: mover piezas entre
-- dos ubicaciones vendibles sin su movimiento (no cambia ningún número publicado); eso lo reporta la vigía
-- 'ubicacion_sin_movimiento' (locations contra Σ stock_mov por ubicación).
create or replace function almacen.tg_locations_kubera_cuadra() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_cedis text; v_sku citext; v_ubi bigint; v_nv bigint; v_fis integer; v_fnv integer;
begin
  if tg_op = 'DELETE' then
    if old.origen <> 'kubera' then return null; end if;
    v_cedis := old.cedis; v_sku := old.sku;
  else
    if new.origen <> 'kubera' then return null; end if;
    v_cedis := new.cedis; v_sku := new.sku;
  end if;
  select coalesce(sum(l.piezas), 0), coalesce(sum(l.piezas) filter (where t.cuenta_para_venta is false), 0)
    into v_ubi, v_nv
    from almacen.locations l
    left join almacen.ubicaciones u     on u.id = l.ubicacion_id
    left join almacen.ubicacion_tipos t on t.tipo = u.tipo
   where l.cedis = v_cedis and l.sku = v_sku and l.origen = 'kubera';
  select s.fisico, s.fisico_no_vendible into v_fis, v_fnv from almacen.stock_almacen s where s.almacen = v_cedis and s.sku = v_sku;
  if v_ubi <> coalesce(v_fis, 0) then
    raise exception using errcode = '23514', constraint = 'locations_kubera_cuadra',
      message = format('locations kubera de %s/%s suman %s y el saldo dice %s', v_sku, v_cedis, v_ubi, coalesce(v_fis, 0));
  end if;
  if v_nv <> coalesce(v_fnv, 0) then
    raise exception using errcode = '23514', constraint = 'locations_no_vendible_cuadra',
      message = format('locations kubera de %s/%s: no vendibles suman %s y fisico_no_vendible dice %s (¿piezas movidas sin su stock_mov?)',
                       v_sku, v_cedis, v_nv, coalesce(v_fnv, 0));
  end if;
  return null;
end $$;
create constraint trigger locations_kubera_cuadra after insert or update or delete on almacen.locations
  deferrable initially deferred for each row execute function almacen.tg_locations_kubera_cuadra();

-- Al COMMIT: cada par reub:<id> suma 0, en una bodega y un SKU. [T: escrito]
create or replace function almacen.tg_reubicacion_pares() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_g text := regexp_replace(new.clave, ':(sale|entra)$', ''); v record;
begin
  select coalesce(sum(delta), 0) s, count(distinct almacen) a, count(distinct lower(sku::text)) k into v
    from almacen.stock_mov where motivo = 'reubicacion' and clave in (v_g || ':sale', v_g || ':entra');
  if v.s <> 0 or v.a <> 1 or v.k <> 1 then
    raise exception using errcode = '23514', constraint = 'stock_mov_reubicacion_pares',
      message = format('reubicación %s: suma %s, bodegas %s, SKUs %s (debe ser 0, 1, 1)', v_g, v.s, v.a, v.k);
  end if;
  return null;
end $$;
create constraint trigger stock_mov_reubicacion_pares after insert on almacen.stock_mov
  deferrable initially deferred for each row when (new.motivo = 'reubicacion')
  execute function almacen.tg_reubicacion_pares();

-- [T0 §4.1] stock_mov sigue de SOLO AGREGAR sin excepción: stock_mov_solo_agregar (ops.tg_solo_agregar) no se toca
-- (la A′ lo dejó en ALWAYS). La excepción R1 de la B_0075 se retira.

-- B4.4 [T0 D16] Invariantes al COMMIT de stock_almacen:
--   (1) fisico_no_vendible = Σ locations kubera en ubicaciones de tipo no vendible; (2) ≥ 0;
--   (3) si hay piezas no vendibles, lo vendible cubre lo apartado (fisico − fisico_no_vendible − apartado ≥ 0),
--       [R corr] SOLO cuando el cambio sube lo no vendible o lo apartado (meter piezas a cuarentena / POR VERIFICAR
--       o apartar). Una baja física en ubicación vendible (merma, conteo negativo) pasa como sin piezas no vendibles:
--       libre < 0 y la vigía avisa (no_vendible_descuadre). Si en la MISMA transacción se apartó, se juzga el final.
create or replace function almacen.tg_no_vendible_cuadra() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_fis integer; v_fnv integer; v_ap integer; v_nv bigint; v_sube boolean;
begin
  select s.fisico, s.fisico_no_vendible, s.apartado into v_fis, v_fnv, v_ap
    from almacen.stock_almacen s where s.sku = new.sku and s.almacen = new.almacen;
  if not found then
    return null;
  end if;
  v_sube := case when tg_op = 'INSERT' then new.fisico_no_vendible > 0 or new.apartado > 0
                 else new.fisico_no_vendible > old.fisico_no_vendible or new.apartado > old.apartado end;
  select coalesce(sum(l.piezas), 0) into v_nv
    from almacen.locations l
    join almacen.ubicaciones u     on u.id = l.ubicacion_id
    join almacen.ubicacion_tipos t on t.tipo = u.tipo
   where l.cedis = new.almacen and l.sku = new.sku and l.origen = 'kubera' and not t.cuenta_para_venta;
  if v_fnv <> v_nv or v_fnv < 0 or (v_sube and v_fnv <> 0 and v_fis - v_fnv - v_ap < 0) then
    raise exception using errcode = '23514', constraint = 'stock_almacen_no_vendible_cuadra',
      message = format('%s/%s: fisico %s, fisico_no_vendible %s (ubicaciones no vendibles %s), apartado %s: %s',
                       new.sku, new.almacen, v_fis, v_fnv, v_nv, v_ap,
                       case when v_fnv <> v_nv or v_fnv < 0
                            then 'fisico_no_vendible no es la suma de las ubicaciones no vendibles (¿piezas movidas sin su stock_mov?)'
                            else 'lo apartado no puede quedar sobre lo no vendible' end);
  end if;
  return null;
end $$;
create constraint trigger stock_almacen_no_vendible_cuadra after insert or update of fisico, fisico_no_vendible, apartado
  on almacen.stock_almacen deferrable initially deferred for each row execute function almacen.tg_no_vendible_cuadra();

-- B4.5 [T0 D16] Recalcular fisico_no_vendible cuando cambia la clasificación (de un tipo o de una ubicación).
create or replace function almacen.tg_ubicacion_tipos_recalcula() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
begin
  update almacen.stock_almacen sa
     set fisico_no_vendible = coalesce((select sum(l.piezas)
                                          from almacen.locations l
                                          join almacen.ubicaciones u     on u.id = l.ubicacion_id
                                          join almacen.ubicacion_tipos t on t.tipo = u.tipo
                                         where l.cedis = sa.almacen and l.sku = sa.sku and l.origen = 'kubera'
                                           and not t.cuenta_para_venta), 0)
   where exists (select 1 from almacen.locations l join almacen.ubicaciones u on u.id = l.ubicacion_id
                  where l.cedis = sa.almacen and l.sku = sa.sku and l.origen = 'kubera' and u.tipo = new.tipo);
  return null;
end $$;
create trigger ubicacion_tipos_recalcula after update of cuenta_para_venta on almacen.ubicacion_tipos
  for each row when (old.cuenta_para_venta is distinct from new.cuenta_para_venta)
  execute function almacen.tg_ubicacion_tipos_recalcula();
create or replace function almacen.tg_ubicaciones_recalcula() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
begin
  update almacen.stock_almacen sa
     set fisico_no_vendible = coalesce((select sum(l.piezas)
                                          from almacen.locations l
                                          join almacen.ubicaciones u     on u.id = l.ubicacion_id
                                          join almacen.ubicacion_tipos t on t.tipo = u.tipo
                                         where l.cedis = sa.almacen and l.sku = sa.sku and l.origen = 'kubera'
                                           and not t.cuenta_para_venta), 0)
   where exists (select 1 from almacen.locations l
                  where l.cedis = sa.almacen and l.sku = sa.sku and l.origen = 'kubera' and l.ubicacion_id = new.id);
  return null;
end $$;
create trigger ubicaciones_recalcula after update of tipo on almacen.ubicaciones
  for each row when (old.tipo is distinct from new.tipo)
  execute function almacen.tg_ubicaciones_recalcula();

-- B4.6 [R seg] El tipo de una ubicación de una bodega kubera solo cambia con almacen.reclasificar_ubicacion
--      (GUC 'ubicacion:<txid>', motivo de 10+ y quién). Con piezas, el tipo NO cruza vendible ↔ no vendible ni
--      entra o sale de por_verificar: esas piezas se mueven antes en el libro (conteo, reubicación o merma).
--      Toda alta o cambio queda en ubicaciones_hist (solo agregar). La copia de Odoo la guarda B2.
create index if not exists locations_ubicacion_ix on almacen.locations (ubicacion_id) where ubicacion_id is not null;
create table if not exists almacen.ubicaciones_hist (
  id           bigint      generated always as identity primary key,
  ubicacion_id bigint      not null,
  almacen      text        not null,
  operacion    text        not null,
  antes        jsonb,
  despues      jsonb       not null,
  piezas       bigint      not null,               -- Σ |piezas| kubera en la ubicación al momento del cambio
  motivo       text,
  quien        text        default coalesce(nullif(current_setting('app.usuario', true), ''), current_user),
  txid         bigint      not null default txid_current(),
  cambiado_at  timestamptz not null default now(),
  constraint ubicaciones_hist_op_chk check (operacion in ('INSERT', 'UPDATE'))
);
create trigger ubicaciones_hist_solo_agregar before update or delete on almacen.ubicaciones_hist
  for each row execute function ops.tg_solo_agregar();
create trigger ubicaciones_hist_sin_truncate before truncate on almacen.ubicaciones_hist
  for each statement execute function ops.tg_sin_truncate();

create or replace function almacen.tg_ubicaciones_reclasificar() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare v_p bigint; v_vo boolean; v_vn boolean;
begin
  if new.fuente <> 'kubera' then
    return new;
  end if;
  if coalesce(current_setting('app.almacen_escritor', true), '') <> 'ubicacion:' || txid_current() then
    raise exception using errcode = '42501', constraint = 'ubicaciones_tipo_solo_funcion',
      message = format('ubicación %s (%s): el tipo solo cambia con almacen.reclasificar_ubicacion(id, tipo, motivo)', old.id, old.almacen);
  end if;
  select coalesce(sum(abs(l.piezas)), 0) into v_p from almacen.locations l where l.ubicacion_id = old.id and l.origen = 'kubera';
  select t.cuenta_para_venta into v_vo from almacen.ubicacion_tipos t where t.tipo = old.tipo;
  select t.cuenta_para_venta into v_vn from almacen.ubicacion_tipos t where t.tipo = new.tipo;
  if v_p > 0 and (old.tipo = 'por_verificar' or new.tipo = 'por_verificar' or v_vo is distinct from v_vn) then
    raise exception using errcode = '23514', constraint = 'ubicacion_reclasificada_con_piezas',
      message = format('ubicación %s (%s %s): %s → %s con %s piezas; muévalas antes en el libro (conteo, reubicación o merma)',
                       old.id, old.almacen, old.posicion, old.tipo, new.tipo, v_p);
  end if;
  return new;
end $$;
create trigger ubicaciones_reclasificar before update of tipo on almacen.ubicaciones
  for each row when (old.tipo is distinct from new.tipo) execute function almacen.tg_ubicaciones_reclasificar();

create or replace function almacen.tg_ubicaciones_hist() returns trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
begin
  if tg_op = 'INSERT' then                                   -- por sentencia: el acto da de alta miles de ubicaciones
    insert into almacen.ubicaciones_hist (ubicacion_id, almacen, operacion, antes, despues, piezas, motivo)
    select n.id, n.almacen, 'INSERT', null, to_jsonb(n), 0,
           coalesce(nullif(current_setting('almacen.motivo_ubicacion', true), ''),
                    case when coalesce(current_setting('app.almacen_escritor', true), '') like 'desprender:%' then 'catálogo de T0 (desprender_desde_copia)' end)
      from nuevos n where n.fuente = 'kubera';
    return null;
  end if;
  if new.fuente = 'kubera' then
    insert into almacen.ubicaciones_hist (ubicacion_id, almacen, operacion, antes, despues, piezas, motivo)
    values (new.id, new.almacen, 'UPDATE', to_jsonb(old), to_jsonb(new),
            (select coalesce(sum(abs(l.piezas)), 0) from almacen.locations l where l.ubicacion_id = new.id and l.origen = 'kubera'),
            nullif(current_setting('almacen.motivo_ubicacion', true), ''));
  end if;
  return null;
end $$;
create trigger ubicaciones_hist_alta after insert on almacen.ubicaciones
  referencing new table as nuevos for each statement execute function almacen.tg_ubicaciones_hist();
create trigger ubicaciones_hist_cambio after update of tipo, activa, posicion, ruta on almacen.ubicaciones
  for each row execute function almacen.tg_ubicaciones_hist();

create or replace function almacen.reclasificar_ubicacion(p_id bigint, p_tipo text, p_motivo text)
returns jsonb language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare v_quien text := nullif(current_setting('app.usuario', true), ''); v_prev text := coalesce(current_setting('app.almacen_escritor', true), '');
        r_u record;
begin
  if v_quien is null then
    raise exception using errcode = '23514', constraint = 'reclasificar_ubicacion_quien', message = 'reclasificar_ubicacion exige app.usuario';
  end if;
  if length(btrim(coalesce(p_motivo, ''))) < 10 then
    raise exception using errcode = '23514', constraint = 'reclasificar_ubicacion_motivo', message = 'reclasificar_ubicacion exige un motivo de 10 caracteres o más';
  end if;
  select * into r_u from almacen.ubicaciones u where u.id = p_id for update;
  if r_u.id is null or r_u.fuente <> 'kubera' then
    raise exception using errcode = '23514', constraint = 'reclasificar_ubicacion_estado',
      message = format('ubicación %s no existe o no es de una bodega kubera', p_id);
  end if;
  perform set_config('app.almacen_escritor', 'ubicacion:' || txid_current(), true);
  perform set_config('almacen.motivo_ubicacion', p_motivo, true);
  update almacen.ubicaciones u set tipo = p_tipo where u.id = p_id;
  perform set_config('almacen.motivo_ubicacion', '', true);
  perform set_config('app.almacen_escritor', v_prev, true);
  return jsonb_build_object('id', p_id, 'almacen', r_u.almacen, 'posicion', r_u.posicion, 'de', r_u.tipo, 'a', p_tipo);
end $$;

-- Semilla: los saldos de ENSAYO entran a locations como kubera / SIN UBICAR (el cuadre nace verde).
select set_config('app.almacen_escritor', 'libro:' || txid_current(), true);                          -- [S5-a]
insert into almacen.locations (cedis, sku, ubicacion, ubicacion_id, piezas, origen)
select sa.almacen, sa.sku, 'SIN UBICAR', u.id, sa.fisico, 'kubera'
  from almacen.stock_almacen sa
  join almacen.ubicaciones u on u.almacen = sa.almacen and u.posicion = 'SIN UBICAR' and u.activa
 where sa.fisico <> 0
on conflict do nothing;
select set_config('app.almacen_escritor', '', true);                                                    -- [T2]

-- ───────────────────────────────────────────────────────────────────────────
-- B5. El corte (solo postgres, en el acta) [S3, T6]
-- ───────────────────────────────────────────────────────────────────────────
-- 1) Abre el acta: desde cuándo Odoo está quieto. Sin cambios (ya acepta una hora pasada).
create or replace function almacen.congelar_bodega(p_almacen text, p_congelado_at timestamptz, p_acta text)
returns bigint language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare v_id bigint; v_quien text := nullif(current_setting('app.usuario', true), '');
begin
  if v_quien is null then
    raise exception using errcode = '23514', constraint = 'desprender_quien', message = 'congelar exige app.usuario';
  end if;
  if not exists (select 1 from almacen.almacenes where codigo = p_almacen and fuente = 'odoo') then
    raise exception using errcode = '23514', constraint = 'desprender_fuente', message = format('%s no es de Odoo', p_almacen);
  end if;
  if p_congelado_at > now() then
    raise exception using errcode = '23514', constraint = 'desprender_congelado_futuro', message = 'congelado_at en el futuro';
  end if;
  insert into almacen.desprendimientos (almacen, de_fuente, a_fuente, congelado_at, acta, quien)
  values (p_almacen, 'odoo', 'kubera', p_congelado_at, p_acta, v_quien) returning id into v_id;
  return v_id;
end $$;

-- 2) Catálogo desde la copia [T0 §4.5]. Replica almacen_odoo.ubicacion_de() de Brandon y odoo._rack().
create or replace function almacen.posicion_odoo(p_ruta text, out posicion text, out rack_bloque text,
                                                 out rack_fila text, out rack_nivel text)
language plpgsql immutable security invoker set search_path = pg_catalog, public as $$
declare v_partes text[]; v_rack text; v_m text[];
begin
  select coalesce(array_agg(btrim(t.x) order by t.o), '{}'::text[]) into v_partes
    from unnest(string_to_array(coalesce(p_ruta, ''), '/')) with ordinality as t(x, o)
   where btrim(t.x) <> '';
  if cardinality(v_partes) <= 1 or v_partes[2:] = array['FERRAFORME'] then
    posicion := 'SIN UBICAR';
    return;
  end if;
  if cardinality(v_partes) = 2 then
    posicion := v_partes[2];
    return;
  end if;
  v_rack := array_to_string(v_partes[3:], '-');
  posicion := v_rack;
  v_m := regexp_match(v_rack, '^BLOQUE (\S+)-FILA (\S+)-(\S+)$');                          -- TEX2
  if v_m is null then
    v_m := regexp_match(v_rack, '^([A-Z]{1,2})-([0-9]+)-(N[0-9]+)$');                        -- TEXCO y DROP
  end if;
  if v_m is not null then
    rack_bloque := v_m[1]; rack_fila := v_m[2]; rack_nivel := v_m[3];
  end if;
end $$;

-- Tipo por el ÚLTIMO tramo, en minúsculas y sin acentos (no hay unaccent). Cuarentena y scrap salen de las
-- raíces de p_bodegas; aquí solo cuenta scrap_location = true (scrap dentro de la bodega).
create or replace function almacen.clasificar_ubicacion_odoo(p_ruta text, p_scrap boolean) returns text
language sql immutable security invoker set search_path = pg_catalog, public as $$
  select case
    when coalesce(p_scrap, false) then 'scrap'
    else case translate(lower(coalesce((select btrim(t.x)
                                          from unnest(string_to_array(coalesce(p_ruta, ''), '/')) with ordinality as t(x, o)
                                         where btrim(t.x) <> '' order by t.o desc limit 1), '')),
                        'áéíóúüñ', 'aeiouun')
      when 'entrada'             then 'entrada'
      when 'salida'              then 'salida'
      when 'zona de empaquetado' then 'preparacion'
      when 'control de calidad'  then 'calidad'
      when 'preproduccion'       then 'calidad'
      when 'posproduccion'       then 'calidad'
      when 'devoluciones'        then 'devoluciones'
      when 'ferraforme archivar' then 'archivo'
      else 'almacenaje' end
  end
$$;

-- 3) Cada transformación de la copia, con su regla [T0 §4.4]. Se inserta dentro de la transacción de T0
--    desde el CSV firmado (su sha256 va en el acta). Solo agregar.
create table if not exists almacen.saldo_inicial_ajustes (
  id              bigint      generated always as identity,
  lote            text        not null,
  carga_id        bigint      not null,
  almacen         text        not null,
  sku_clave       text        not null,            -- SKU de core.products, o '#odoo:<product_id>' (sin código)
  ubicacion_ref   text        not null,            -- 'odoo:<location_id>' de la copia, o 'POR_VERIFICAR'
  delta           integer     not null,
  regla           text        not null,
  par             bigint,                          -- agrupa los renglones que se compensan
  quant_id        bigint,
  odoo_product_id bigint,                          -- el producto crudo (gemelos, archivados)
  referencia      text,                            -- S…, folio de conteo, SKU de Woo, lista de recodificación
  nota            text        not null,
  quien           text        not null default nullif(current_setting('app.usuario', true), ''),
  creado_at       timestamptz not null default now(),
  constraint saldo_inicial_ajustes_pkey      primary key (id),
  constraint saldo_inicial_ajustes_carga_fk  foreign key (carga_id) references odoo_historia.cargas (id),
  constraint saldo_inicial_ajustes_alm_fk    foreign key (almacen) references almacen.almacenes (codigo),
  constraint saldo_inicial_ajustes_lote_chk  check (lote ~ '^T0-[0-9]{8}(-[a-z0-9]+)?$'),
  constraint saldo_inicial_ajustes_sku_chk   check (length(btrim(sku_clave)) > 0 and sku_clave = btrim(sku_clave)),
  constraint saldo_inicial_ajustes_ubic_chk  check (ubicacion_ref ~ '^(odoo:[0-9]+|POR_VERIFICAR)$'),
  constraint saldo_inicial_ajustes_delta_chk check (delta <> 0),
  constraint saldo_inicial_ajustes_regla_chk check (regla in (
      'negativo_neteado', 'negativo_a_cero', 'excedente_por_verificar', 'gemelo_extra', 'archivado_equivalente',
      'archivado_sin_equivalente', 'sin_codigo', 'sku_desconocido', 'recodificacion', 'reserva_sin_clasificar',
      'reserva_salio', 'no_contado_por_verificar', 'ubicacion_sin_bodega',
      'fuera_de_woo_por_verificar')),                -- [D4 firmado 20-oct] gestiona=false o no está en Woo → POR VERIFICAR
  constraint saldo_inicial_ajustes_nota_chk  check (length(btrim(nota)) >= 5)
);
create index if not exists saldo_inicial_ajustes_lote_ix on almacen.saldo_inicial_ajustes (lote, carga_id);
create trigger saldo_inicial_ajustes_solo_agregar before update or delete on almacen.saldo_inicial_ajustes
  for each row execute function ops.tg_solo_agregar();
create trigger saldo_inicial_ajustes_sin_truncate before truncate on almacen.saldo_inicial_ajustes
  for each statement execute function ops.tg_sin_truncate();

-- 4) EL CORTE DE T0 [T0 §4.6]: TEXCO, TEX2 y DROP en un acto, desde la copia cruda cerrada ⊕ los ajustes del lote.
--    Todo en la transacción de quien la llama: cualquier falla = excepción = ROLLBACK de todo.
create or replace function almacen.desprender_desde_copia(
  p_ids          bigint[],   -- desprendimientos 'congelada' de odoo→kubera, UNO por bodega (creados con congelar_bodega)
  p_carga        bigint,     -- odoo_historia.cargas.id en estado 'cerrada'
  p_lote         text,       -- lote de almacen.saldo_inicial_ajustes que se aplica (una sola vez)
  p_bodegas      jsonb,      -- banderas, preferencia y raíces especiales por bodega
  p_tex2_brandon jsonb,      -- {"renglones": n, "piezas": n}: el sí escrito de borrar la copia de Brandon
  p_esperado     jsonb       -- totales firmados en el acta; deben coincidir EXACTO
) returns jsonb
language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare
  v_quien   text := nullif(current_setting('app.usuario', true), '');
  v_ini     timestamptz := clock_timestamp();
  r_ca      record;
  r_d       record;
  r_b       record;
  r_s       record;
  v_bods    text[];
  v_corte   timestamptz;
  v_foto_at timestamptz;
  v_n       bigint;
  v_txt     text;
  v_cod     text;
  v_calc    jsonb;
  v_tot     jsonb;
  v_br      record;
  v_brdif   record;
  v_res     jsonb := '[]'::jsonb;
  v_saldo   integer;
  v_pv      bigint;
  v_pref    integer[];
  v_wh      integer;
  v_crudo   numeric;
  v_ajs     bigint;
  v_mov     bigint;
  i         integer;
begin
  -- 1. Arranque
  if v_quien is null then
    raise exception using errcode = '23514', constraint = 'desprender_quien', message = 'desprender_desde_copia exige app.usuario';
  end if;
  if exists (select 1 from almacen.desprendimientos x where x.txid = txid_current()) then
    raise exception using errcode = '23514', constraint = 'desprender_estado',
      message = 'desprender_desde_copia se llama una sola vez por transacción';
  end if;
  set constraints all deferred;          -- libro, ubicaciones, apartado y D16 se comprueban al COMMIT
  if p_ids is null or cardinality(p_ids) = 0
     or cardinality(p_ids) <> (select count(distinct x) from unnest(p_ids) x) then
    raise exception using errcode = '23514', constraint = 'desprender_parametros', message = 'p_ids vacío o con repetidos';
  end if;
  perform 1 from almacen.desprendimientos x where x.id = any(p_ids) order by x.id for update;

  -- 2. Parámetros
  if (select count(*) from almacen.desprendimientos x
       where x.id = any(p_ids) and x.estado = 'congelada' and x.de_fuente = 'odoo' and x.a_fuente = 'kubera')
     <> cardinality(p_ids) then
    raise exception using errcode = '23514', constraint = 'desprender_estado',
      message = format('no todos los desprendimientos %s están congelada de odoo a kubera', p_ids);
  end if;
  select array_agg(x.almacen order by coalesce(array_position(array['TEXCO','TEX2','DROP'], x.almacen), 99), x.almacen)
    into v_bods from almacen.desprendimientos x where x.id = any(p_ids);
  if cardinality(v_bods) <> (select count(distinct y) from unnest(v_bods) y) then
    raise exception using errcode = '23514', constraint = 'desprender_parametros', message = 'una bodega por desprendimiento';
  end if;
  perform 1 from almacen.almacenes a where a.codigo = any(v_bods) order by a.codigo for update;
  if exists (select 1 from almacen.almacenes a where a.codigo = any(v_bods) and a.fuente <> 'odoo') then
    raise exception using errcode = '23514', constraint = 'desprender_estado', message = 'alguna bodega del acto ya no es de Odoo';
  end if;
  select * into r_ca from odoo_historia.cargas c where c.id = p_carga for share;
  if r_ca.id is null or r_ca.estado <> 'cerrada' then
    raise exception using errcode = '23514', constraint = 'desprender_carga_no_cerrada',
      message = format('la carga %s no existe o no está cerrada (%s)', p_carga, coalesce(r_ca.estado, 'NULL'));
  end if;
  if (select count(distinct x.congelado_at) from almacen.desprendimientos x where x.id = any(p_ids)) <> 1
     or (select min(x.congelado_at) from almacen.desprendimientos x where x.id = any(p_ids)) <> r_ca.corte_utc then
    raise exception using errcode = '23514', constraint = 'desprender_corte',
      message = format('los congelado_at de %s deben ser uno solo e igual al corte de la carga (%s)', p_ids, r_ca.corte_utc);
  end if;
  v_corte := r_ca.corte_utc;
  v_foto_at := nullif(r_ca.modelos -> 'stock.quant' ->> 'fecha_utc', '')::timestamptz;
  if v_foto_at is null then
    raise exception using errcode = '23514', constraint = 'desprender_carga_no_cerrada',
      message = format('la carga %s no trae modelos.stock.quant.fecha_utc', p_carga);
  end if;
  if p_bodegas is null or jsonb_typeof(p_bodegas) <> 'object'
     or (select array_agg(k order by k) from jsonb_object_keys(p_bodegas) k)
        is distinct from (select array_agg(y order by y) from unnest(v_bods) y) then
    raise exception using errcode = '23514', constraint = 'desprender_banderas',
      message = format('las llaves de p_bodegas deben ser exactamente %s', v_bods);
  end if;
  for r_b in select e.key as cod, e.value as v from jsonb_each(p_bodegas) e loop
    if jsonb_typeof(r_b.v) <> 'object'
       or jsonb_typeof(r_b.v -> 'surte_ventas') is distinct from 'boolean'
       or jsonb_typeof(r_b.v -> 'cuenta_para_woo') is distinct from 'boolean'
       or jsonb_typeof(r_b.v -> 'admite_ov') is distinct from 'boolean'
       or (r_b.v ? 'preferencia' and jsonb_typeof(r_b.v -> 'preferencia') not in ('number', 'null'))
       or coalesce(jsonb_typeof(r_b.v -> 'raices_cuarentena'), 'array') <> 'array'
       or coalesce(jsonb_typeof(r_b.v -> 'raices_scrap'), 'array') <> 'array' then
      raise exception using errcode = '23514', constraint = 'desprender_banderas',
        message = format('%s: p_bodegas mal formado (booleanos surte_ventas, cuenta_para_woo, admite_ov; preferencia número; raíces arreglo)', r_b.cod);
    end if;
    if ((r_b.v ->> 'surte_ventas')::boolean and (r_b.v ->> 'preferencia') is null)                                  -- almacenes_surte_chk
       or ((r_b.v ->> 'cuenta_para_woo')::boolean and not (r_b.v ->> 'surte_ventas')::boolean)                     -- almacenes_woo_chk
       or ((r_b.v ->> 'surte_ventas')::boolean and not (r_b.v ->> 'admite_ov')::boolean)                           -- almacenes_surte_ov_chk
       or coalesce((r_b.v ->> 'preferencia')::numeric, 1) <= 0
       or coalesce((r_b.v ->> 'preferencia')::numeric, 1) <> trunc(coalesce((r_b.v ->> 'preferencia')::numeric, 1)) then
      raise exception using errcode = '23514', constraint = 'desprender_banderas',
        message = format('%s: las banderas no cumplen los CHECK de almacenes (surte → preferencia; cuenta_para_woo → surte; surte → admite_ov; preferencia entera > 0)', r_b.cod);
    end if;
  end loop;
  select array_agg((e.value ->> 'preferencia')::integer) into v_pref
    from jsonb_each(p_bodegas) e where (e.value ->> 'preferencia') is not null;
  if coalesce(cardinality(v_pref), 0) <> (select count(distinct y) from unnest(v_pref) y)
     or exists (select 1 from almacen.almacenes a where a.codigo <> all(v_bods) and a.preferencia = any(v_pref)) then
    raise exception using errcode = '23514', constraint = 'desprender_banderas',
      message = format('preferencias repetidas o ya usadas por otra bodega: %s', v_pref);
  end if;
  if not exists (select 1 from almacen.saldo_inicial_ajustes j where j.lote = p_lote and j.carga_id = p_carga)
     or exists (select 1 from almacen.saldo_inicial_ajustes j where j.lote = p_lote and j.carga_id <> p_carga)
     or exists (select 1 from almacen.desprendimientos x where x.lote = p_lote) then
    raise exception using errcode = '23514', constraint = 'desprender_lote_usado',
      message = format('el lote %s no tiene ajustes de la carga %s, mezcla cargas o ya lo usó otro desprendimiento', p_lote, p_carga);
  end if;
  if exists (select 1 from almacen.saldo_inicial_ajustes j where j.lote = p_lote and j.almacen <> all(v_bods)) then
    raise exception using errcode = '23514', constraint = 'desprender_parametros',
      message = format('el lote %s trae ajustes de bodegas fuera del acto', p_lote);
  end if;

  -- 3. Hechos que la base sí ve
  if exists (select 1 from almacen.historial_movimientos h where h.cedis = any(v_bods) and h.fecha >= v_corte) then
    raise exception using errcode = '23514', constraint = 'desprender_odoo_se_movio',
      message = format('Odoo registró movimientos después del corte (%s)', v_corte);
  end if;
  if to_regclass('almacen.traslados') is not null then                                                   -- precondición 6 (con C′)
    execute $q$select count(*) from almacen.traslados t join almacen.traslado_lineas l on l.traslado_id = t.id
                where t.estado not in ('cancelado', 'cuadrado') and (t.origen = any($1) or t.destino = any($1))$q$
      into v_n using v_bods;
    if v_n > 0 then
      raise exception using errcode = '23514', constraint = 'desprender_traslados_pendientes',
        message = format('%s líneas de traslado abiertas tocan %s', v_n, v_bods);
    end if;
  end if;
  if exists (select 1 from almacen.ubicaciones u where u.almacen = any(v_bods)) then
    raise exception using errcode = '23514', constraint = 'desprender_catalogo_previo',
      message = format('ya hay catálogo de ubicaciones de %s: el catálogo de T0 sale de la copia', v_bods);
  end if;

  -- 4. Crudos. Las bodegas se leen ANTES del UPDATE que pone odoo_warehouse_id = NULL.
  if to_regclass('pg_temp._t0_bod') is not null or to_regclass('pg_temp._t0_semb') is not null then
    drop table if exists pg_temp._t0_bod, pg_temp._t0_raiz, pg_temp._t0_ubic, pg_temp._t0_q, pg_temp._t0_aj,
                         pg_temp._t0_semb, pg_temp._t0_skus, pg_temp._t0_cat;
  end if;
  create temp table _t0_bod on commit drop as
    select a.codigo as almacen, a.odoo_warehouse_id::bigint as wh, (a.codigo = any(v_bods)) as del_acto
      from almacen.almacenes a where a.odoo_warehouse_id is not null;
  create temp table _t0_raiz on commit drop as
    with recursive rz(id, almacen, tipo) as (
      select l.id, x.cod, x.tipo
        from (select e.key as cod, 'cuarentena'::text as tipo, jsonb_array_elements_text(coalesce(e.value -> 'raices_cuarentena', '[]'::jsonb))::bigint as raiz
                from jsonb_each(p_bodegas) e
              union all
              select e.key, 'scrap', jsonb_array_elements_text(coalesce(e.value -> 'raices_scrap', '[]'::jsonb))::bigint
                from jsonb_each(p_bodegas) e) x
        join odoo_historia.stock_location l on l.carga_id = p_carga and l.id = x.raiz
      union
      select h.id, rz.almacen, rz.tipo
        from odoo_historia.stock_location h join rz on h.location_id = rz.id
       where h.carga_id = p_carga)
    select rz.id, rz.almacen, rz.tipo from rz;
  if exists (select 1 from jsonb_each(p_bodegas) e,
                     jsonb_array_elements_text(coalesce(e.value -> 'raices_cuarentena', '[]'::jsonb)
                                               || coalesce(e.value -> 'raices_scrap', '[]'::jsonb)) z(r)
              where not exists (select 1 from odoo_historia.stock_location l where l.carga_id = p_carga and l.id = z.r::bigint))
     or exists (select 1 from _t0_raiz z group by z.id having count(*) > 1) then
    raise exception using errcode = '23514', constraint = 'desprender_parametros',
      message = 'una raíz de cuarentena o scrap no está en la carga, o una ubicación cae en dos raíces';
  end if;
  create temp table _t0_ubic on commit drop as
    select l.id as odoo_id, coalesce(bw.almacen, rz.almacen) as almacen,
           coalesce(rz.tipo, almacen.clasificar_ubicacion_odoo(l.complete_name, l.scrap_location)) as tipo,
           l.complete_name, l.active, l.warehouse_id, l.location_id,
           (bw.almacen is not null and rz.almacen is not null and bw.almacen <> rz.almacen) as conflicto,
           (bw.almacen is null and ow.almacen is not null and rz.almacen is null) as de_otra_bodega
      from odoo_historia.stock_location l
      left join _t0_bod bw  on bw.wh = l.warehouse_id and bw.del_acto
      left join _t0_bod ow  on ow.wh = l.warehouse_id and not ow.del_acto
      left join _t0_raiz rz on rz.id = l.id
     where l.carga_id = p_carga and l.usage = 'internal';
  create index on _t0_ubic (odoo_id);
  analyze _t0_ubic;
  if exists (select 1 from _t0_ubic u where u.conflicto) then
    raise exception using errcode = '23514', constraint = 'desprender_parametros',
      message = 'una ubicación con almacén de una bodega cae bajo la raíz especial de otra';
  end if;
  create temp table _t0_q on commit drop as
    select q.id as quant_id, q.product_id, q.location_id, u.almacen,
           upper(coalesce(nullif(btrim(p.default_code), ''), '#odoo:' || q.product_id)) as k,
           p.active as activo, q.quantity, q.reserved_quantity
      from odoo_historia.stock_quant q
      join _t0_ubic u on u.odoo_id = q.location_id
      join odoo_historia.product_product p on p.carga_id = q.carga_id and p.id = q.product_id
     where q.carga_id = p_carga and not u.de_otra_bodega;
  create index on _t0_q (almacen, k, location_id);
  analyze _t0_q;
  if exists (select 1 from _t0_q q where q.quantity <> trunc(q.quantity) or q.reserved_quantity <> trunc(q.reserved_quantity)) then
    select string_agg(format('quant %s (%s): %s', q.quant_id, q.k, q.quantity), '; ') into v_txt
      from (select * from _t0_q q where q.quantity <> trunc(q.quantity) or q.reserved_quantity <> trunc(q.reserved_quantity) limit 5) q;
    raise exception using errcode = '23514', constraint = 'desprender_cantidad_no_entera', message = v_txt;
  end if;
  create temp table _t0_aj on commit drop as
    select j.*, upper(j.sku_clave) as k,
           case when j.ubicacion_ref like 'odoo:%' then substr(j.ubicacion_ref, 6)::bigint end as odoo_loc
      from almacen.saldo_inicial_ajustes j where j.lote = p_lote and j.carga_id = p_carga;
  analyze _t0_aj;
  -- Quant ≠ 0 en una interna sin bodega: solo pasa si un ajuste ubicacion_sin_bodega lo deja en 0.
  select string_agg(format('%s en odoo:%s (%s)', x.k, x.location_id, x.q), '; ') into v_txt
    from (select q.k, q.location_id, sum(q.quantity) as q
            from _t0_q q where q.almacen is null group by 1, 2) x
    left join (select j.k, j.odoo_loc, sum(j.delta) as d from _t0_aj j where j.regla = 'ubicacion_sin_bodega' group by 1, 2) a
      on a.k = x.k and a.odoo_loc = x.location_id
   where x.q + coalesce(a.d, 0) <> 0;
  if v_txt is not null then
    raise exception using errcode = '23514', constraint = 'desprender_ubicacion_sin_bodega', message = left(v_txt, 900);
  end if;

  -- 5. Validación de los ajustes del lote (forma de cada regla)
  if exists (select 1 from _t0_aj j
              where (j.regla = 'ubicacion_sin_bodega'
                     and not exists (select 1 from _t0_ubic u where u.odoo_id = j.odoo_loc and u.almacen is null and not u.de_otra_bodega))
                 or (j.regla <> 'ubicacion_sin_bodega' and j.odoo_loc is not null
                     and not exists (select 1 from _t0_ubic u where u.odoo_id = j.odoo_loc and u.almacen = j.almacen))) then
    select string_agg(format('#%s %s %s', j.id, j.regla, j.ubicacion_ref), '; ') into v_txt from (
      select * from _t0_aj j
       where (j.regla = 'ubicacion_sin_bodega'
              and not exists (select 1 from _t0_ubic u where u.odoo_id = j.odoo_loc and u.almacen is null and not u.de_otra_bodega))
          or (j.regla <> 'ubicacion_sin_bodega' and j.odoo_loc is not null
              and not exists (select 1 from _t0_ubic u where u.odoo_id = j.odoo_loc and u.almacen = j.almacen)) limit 5) j;
    raise exception using errcode = '23514', constraint = 'saldo_ajustes_ubicacion_ajena',
      message = format('ajustes en una ubicación que no es de su bodega: %s', v_txt);
  end if;
  if exists (select 1 from _t0_aj j
              where j.regla in ('archivado_sin_equivalente', 'sin_codigo', 'sku_desconocido', 'reserva_salio', 'ubicacion_sin_bodega')
                and j.delta > 0) then
    raise exception using errcode = '23514', constraint = 'saldo_ajustes_regla_signo',
      message = 'las reglas de salida (archivado_sin_equivalente, sin_codigo, sku_desconocido, reserva_salio, ubicacion_sin_bodega) van con delta < 0';
  end if;
  if exists (select 1 from _t0_aj j
              where j.regla in ('negativo_neteado', 'negativo_a_cero', 'excedente_por_verificar', 'gemelo_extra',
                                'archivado_equivalente', 'recodificacion', 'reserva_sin_clasificar', 'no_contado_por_verificar',
                                'fuera_de_woo_por_verificar')
                and j.par is null)
     or exists (select 1 from _t0_aj j where j.par is not null
                 group by j.par
                having count(distinct j.regla) > 1 or count(distinct j.almacen) > 1
                    or (min(j.regla) <> 'negativo_a_cero' and sum(j.delta) <> 0)) then
    raise exception using errcode = '23514', constraint = 'saldo_ajustes_par_descuadra',
      message = 'cada par lleva par, una regla y una bodega, y suma 0 (negativo_a_cero: su clase queda en 0)';
  end if;
  if exists (select 1 from _t0_aj j                                           -- las de POR_VERIFICAR: + a POR_VERIFICAR, − en la copia
              where j.regla in ('excedente_por_verificar', 'gemelo_extra', 'archivado_equivalente',
                                'reserva_sin_clasificar', 'no_contado_por_verificar', 'fuera_de_woo_por_verificar')
                and ((j.delta > 0) <> (j.ubicacion_ref = 'POR_VERIFICAR')))
     -- [D4 firmado] fuera_de_woo_por_verificar: referencia del motivo, un SKU por par y el − en una ubicación vendible
     or exists (select 1 from _t0_aj j where j.regla = 'fuera_de_woo_por_verificar'
                 and coalesce(j.referencia, '') not in ('woo:gestiona=false', 'woo:no_esta'))
     or exists (select 1 from _t0_aj j where j.regla = 'fuera_de_woo_por_verificar'
                 group by j.par having count(distinct j.k) > 1)
     or exists (select 1 from _t0_aj j join _t0_ubic u on u.odoo_id = j.odoo_loc
                  join almacen.ubicacion_tipos t on t.tipo = u.tipo
                 where j.regla = 'fuera_de_woo_por_verificar' and j.delta < 0 and not t.cuenta_para_venta)
     or exists (select 1 from _t0_aj j                                        -- negativos: en la copia, un SKU por par
                 where j.regla in ('negativo_neteado', 'negativo_a_cero') and j.odoo_loc is null)
     or exists (select 1 from _t0_aj j where j.regla in ('negativo_neteado', 'negativo_a_cero')
                 group by j.par having count(distinct j.k) > 1)
     or exists (select 1 from _t0_aj j where j.regla in ('reserva_sin_clasificar', 'reserva_salio')
                 and nullif(btrim(j.referencia), '') is null)
     or exists (select 1 from _t0_aj j where j.regla = 'sin_codigo' and j.k not like '#ODOO:%')
     or exists (select 1 from _t0_aj j where j.regla = 'sku_desconocido'
                 and (j.k like '#ODOO:%' or exists (select 1 from core.products p where p.sku = j.k::citext)))
     or exists (select 1 from _t0_aj j where j.regla = 'recodificacion' and j.delta < 0 and j.odoo_loc is null)
     or exists (select 1 from _t0_aj j where j.regla = 'recodificacion' and j.delta > 0 and j.odoo_loc is not null
                 and (nullif(btrim(j.referencia), '') is null
                      or not exists (select 1 from _t0_aj n where n.par = j.par and n.delta < 0 and n.odoo_loc = j.odoo_loc)))
     or exists (select 1 from _t0_aj j where j.regla = 'recodificacion' group by j.par having count(distinct j.k) < 2)
     or exists (select 1 from _t0_aj j join _t0_ubic u on u.odoo_id = j.odoo_loc
                  join almacen.ubicacion_tipos t on t.tipo = u.tipo
                 where j.regla in ('negativo_neteado', 'negativo_a_cero')
                 group by j.par having count(distinct t.cuenta_para_venta) > 1) then
    raise exception using errcode = '23514', constraint = 'saldo_ajustes_forma',
      message = 'un ajuste no tiene la forma de su regla (SPEC_T0 §4.4)';
  end if;

  -- Sembrado = crudos ⊕ ajustes, por (SKU, bodega, ubicación de la copia | POR_VERIFICAR)
  create temp table _t0_semb on commit drop as
    with c as (select q.k, q.almacen, 'odoo:' || q.location_id as ref, sum(q.quantity) as q, sum(q.reserved_quantity) as r
                 from _t0_q q where q.almacen is not null group by 1, 2, 3),
         a as (select j.k, j.almacen, j.ubicacion_ref as ref, sum(j.delta) as d
                 from _t0_aj j where j.regla <> 'ubicacion_sin_bodega' group by 1, 2, 3)
    select coalesce(c.k, a.k) as k, coalesce(c.almacen, a.almacen) as almacen, coalesce(c.ref, a.ref) as ref,
           coalesce(c.q, 0)::bigint as crudo, coalesce(c.r, 0)::bigint as reservado, coalesce(a.d, 0)::bigint as ajuste,
           (coalesce(c.q, 0) + coalesce(a.d, 0))::bigint as sembrado,
           case when coalesce(c.ref, a.ref) like 'odoo:%' then substr(coalesce(c.ref, a.ref), 6)::bigint end as odoo_loc,
           null::text as tipo, null::boolean as vendible, null::citext as sku, null::bigint as ubic_id
      from c full join a on a.k = c.k and a.almacen = c.almacen and a.ref = c.ref;
  update _t0_semb s set tipo = u.tipo from _t0_ubic u where u.odoo_id = s.odoo_loc;
  update _t0_semb s set tipo = 'por_verificar' where s.ref = 'POR_VERIFICAR';
  update _t0_semb s set vendible = t.cuenta_para_venta from almacen.ubicacion_tipos t where t.tipo = s.tipo;
  update _t0_semb s set sku = p.sku from core.products p where p.sku = s.k::citext;
  create index on _t0_semb (almacen, sku);
  analyze _t0_semb;
  select string_agg(format('%s/%s %s: %s', x.k, x.almacen, x.ref, x.sembrado), '; ') into v_txt
    from (select * from _t0_semb s where s.sembrado < 0 order by s.almacen, s.k limit 10) x;
  if v_txt is not null then
    raise exception using errcode = '23514', constraint = 'desprender_sembrado_negativo',
      message = format('sembrado < 0 (negativo sin regla): %s', v_txt);
  end if;
  select string_agg(format('%s/%s: %s', x.k, x.almacen, x.sembrado), '; ') into v_txt
    from (select * from _t0_semb s where s.sembrado > 0 and s.sku is null order by s.almacen, s.k limit 10) x;
  if v_txt is not null then
    raise exception using errcode = '23514', constraint = 'desprender_sku_desconocido',
      message = format('se sembraría un SKU que no está en core.products: %s', v_txt);
  end if;
  if exists (select 1 from _t0_aj j where j.regla = 'negativo_a_cero'
              and exists (select 1 from _t0_semb s
                           where s.k = j.k and s.almacen = j.almacen and s.sembrado <> 0 and s.ref <> 'POR_VERIFICAR'
                             and s.vendible = (select t.cuenta_para_venta from _t0_ubic u join almacen.ubicacion_tipos t on t.tipo = u.tipo
                                                where u.odoo_id = j.odoo_loc))) then
    raise exception using errcode = '23514', constraint = 'saldo_ajustes_par_descuadra',
      message = 'negativo_a_cero debe dejar lo sembrado de su clase (vendible / no vendible) en 0';
  end if;
  select string_agg(format('producto %s en odoo:%s/%s: %s crudas, %s cubiertas', x.product_id, x.location_id, x.almacen, x.q, coalesce(a.d, 0)), '; ')
    into v_txt
    from (select q.product_id, q.almacen, q.location_id, sum(q.quantity) as q
            from _t0_q q where not q.activo and q.almacen is not null group by 1, 2, 3 having sum(q.quantity) > 0) x
    left join (select j.odoo_product_id, j.almacen, j.odoo_loc, -sum(j.delta) as d
                 from _t0_aj j
                where j.delta < 0 and j.regla in ('archivado_equivalente', 'archivado_sin_equivalente', 'recodificacion', 'sin_codigo', 'sku_desconocido')
                group by 1, 2, 3) a
      on a.odoo_product_id = x.product_id and a.almacen = x.almacen and a.odoo_loc = x.location_id
   where x.q <> coalesce(a.d, 0);
  if v_txt is not null then
    raise exception using errcode = '23514', constraint = 'desprender_archivado_sin_regla', message = left(v_txt, 900);
  end if;

  -- 6. Totales contra lo firmado (antes de tocar nada)
  create temp table _t0_skus on commit drop as
    select distinct q.almacen, p.sku from _t0_q q join core.products p on p.sku = q.k::citext where q.almacen is not null
    union
    select s.almacen, s.sku from _t0_semb s where s.sembrado > 0;
  select jsonb_build_object(
           'bodegas', (select jsonb_object_agg(b.cod, jsonb_build_object(
                          'renglones', (select count(*) from _t0_semb s where s.almacen = b.cod and s.sembrado > 0),
                          'skus', (select count(*) from _t0_skus x where x.almacen = b.cod),
                          'fisico', (select coalesce(sum(s.sembrado), 0) from _t0_semb s where s.almacen = b.cod),
                          'fisico_no_vendible', (select coalesce(sum(s.sembrado), 0) from _t0_semb s where s.almacen = b.cod and not s.vendible),
                          'por_verificar', (select coalesce(sum(s.sembrado), 0) from _t0_semb s where s.almacen = b.cod and s.ref = 'POR_VERIFICAR')))
                         from unnest(v_bods) b(cod)),
           'reglas', coalesce((select jsonb_object_agg(x.regla, jsonb_build_object('renglones', x.n, 'suma', x.s, 'abs', x.a))
                                 from (select j.regla, count(*) as n, sum(j.delta) as s, sum(abs(j.delta)) as a
                                         from _t0_aj j group by j.regla) x), '{}'::jsonb))
    into v_calc;
  if p_esperado is distinct from v_calc then
    raise exception using errcode = '23514', constraint = 'desprender_esperado',
      message = format('los totales no coinciden con p_esperado del acta; calculado: %s', v_calc::text);
  end if;
  select count(*) as renglones, coalesce(sum(l.piezas), 0) as piezas into v_br
    from almacen.locations l where l.cedis = any(v_bods) and l.origen = 'odoo';
  if coalesce((p_tex2_brandon ->> 'renglones')::bigint, -1) <> v_br.renglones
     or coalesce((p_tex2_brandon ->> 'piezas')::bigint, -1) <> v_br.piezas then
    raise exception using errcode = '23514', constraint = 'desprender_tex2_brandon',
      message = format('la copia de Brandon trae %s renglones y %s piezas; el acta dice %s', v_br.renglones, v_br.piezas, coalesce(p_tex2_brandon::text, 'NULL'));
  end if;
  -- «Precondición 5»: diferencia por SKU de la copia de Brandon contra la copia cruda (solo se informa).
  select count(*) filter (where coalesce(br.x, 0) <> coalesce(cr.x, 0)) as distintos,
         coalesce(sum(coalesce(cr.x, 0) - coalesce(br.x, 0)), 0) as neto
    into v_brdif
    from (select l.cedis as almacen, upper(l.sku::text) as k, sum(l.piezas) as x
            from almacen.locations l where l.cedis = any(v_bods) and l.origen = 'odoo' group by 1, 2) br
    full join (select q.almacen, q.k, sum(q.quantity) as x from _t0_q q
                where q.almacen in (select l.cedis from almacen.locations l where l.cedis = any(v_bods) and l.origen = 'odoo')
                group by 1, 2) cr
      on cr.almacen = br.almacen and cr.k = br.k;

  -- Catálogo de cada bodega, con la regla de los gemelos (D14)
  create temp table _t0_cat on commit drop as
    select u.odoo_id, u.almacen, u.tipo, u.complete_name, u.active, u.warehouse_id, u.location_id,
           case when u.tipo in ('cuarentena', 'scrap') and po.posicion = 'SIN UBICAR'
                then upper(coalesce((select btrim(t.x) from unnest(string_to_array(u.complete_name, '/')) with ordinality t(x, o)
                                      where btrim(t.x) <> '' order by t.o desc limit 1), u.tipo))
                else po.posicion end as posicion,
           po.rack_bloque, po.rack_fila, po.rack_nivel,
           coalesce(st.tiene, false) as tiene, null::boolean as activa
      from _t0_ubic u
      cross join lateral almacen.posicion_odoo(u.complete_name) po
      left join (select s.odoo_loc, bool_or(s.sembrado > 0) as tiene from _t0_semb s where s.odoo_loc is not null group by 1) st
        on st.odoo_loc = u.odoo_id
     where u.almacen is not null;
  select string_agg(format('%s/%s: %s', x.almacen, x.posicion, x.ids), '; ') into v_txt
    from (select c.almacen, c.posicion, array_agg(c.odoo_id order by c.odoo_id) as ids
            from _t0_cat c where c.tiene group by 1, 2 having count(*) > 1) x;
  if v_txt is not null then
    raise exception using errcode = '23514', constraint = 'desprender_posicion_repetida',
      message = format('ubicaciones con piezas que comparten posición (gemelos): %s', v_txt);
  end if;
  update _t0_cat c set activa = w.activa
    from (select c2.odoo_id,
                 case when not c2.active then false
                      when c2.tiene then true
                      when bool_or(c2.active and c2.tiene) over (partition by c2.almacen, c2.posicion) then false
                      else c2.odoo_id = min(c2.odoo_id) filter (where c2.active) over (partition by c2.almacen, c2.posicion)
                 end as activa
            from _t0_cat c2) w
   where w.odoo_id = c.odoo_id;

  -- 7. El corte, bodega por bodega (TEXCO, TEX2, DROP)
  perform set_config('app.t0_acto', 'desprender_desde_copia:' || txid_current(), true);   -- [R seg] la marca de la puerta
  foreach v_cod in array v_bods loop
    select * into r_d from almacen.desprendimientos x where x.id = any(p_ids) and x.almacen = v_cod;
    select a.odoo_warehouse_id into v_wh from almacen.almacenes a where a.codigo = v_cod;     -- antes de ponerlo en NULL
    v_tot := v_calc -> 'bodegas' -> v_cod;
    update almacen.desprendimientos x
       set estado = 'hecha', hecho_at = now(), txid = txid_current(), foto_odoo_at = v_foto_at,
           carga_id = p_carga, lote = p_lote,
           renglones = (v_tot ->> 'renglones')::integer, skus = (v_tot ->> 'skus')::integer,
           fisico = (v_tot ->> 'fisico')::bigint, fisico_no_vendible = (v_tot ->> 'fisico_no_vendible')::bigint,
           reservado = (select coalesce(sum(s.reservado), 0) from _t0_semb s where s.almacen = v_cod),
           odoo_warehouse_id = (select a.odoo_warehouse_id from almacen.almacenes a where a.codigo = v_cod)
     where x.id = r_d.id;
    perform set_config('app.almacen_escritor', 'desprender:' || v_cod, true);
    delete from almacen.locations l where l.cedis = v_cod and l.origen = 'odoo';             -- en TEX2, la copia de Brandon
    delete from almacen.copias_odoo c where c.almacen = v_cod;                               -- la FK restrict lo exige antes
    update almacen.almacenes a
       set fuente = 'kubera', odoo_warehouse_id = null,
           surte_ventas    = (p_bodegas -> v_cod ->> 'surte_ventas')::boolean,
           cuenta_para_woo = (p_bodegas -> v_cod ->> 'cuenta_para_woo')::boolean,
           admite_ov       = (p_bodegas -> v_cod ->> 'admite_ov')::boolean,
           preferencia     = (p_bodegas -> v_cod ->> 'preferencia')::smallint,
           motivo = left(format('T0 %s, desprendimiento %s: %s', p_lote, r_d.id, r_d.acta), 1000),
           actualizado_por = v_quien
     where a.codigo = v_cod;
    insert into almacen.ubicaciones (almacen, fuente, ruta, posicion, tipo, rack_bloque, rack_fila, rack_nivel,
                                     activa, odoo_location_id, odoo_sin_almacen)
    select c.almacen, 'kubera', c.complete_name, c.posicion, c.tipo, c.rack_bloque, c.rack_fila, c.rack_nivel,
           c.activa, c.odoo_id::integer, (c.warehouse_id is null)
      from _t0_cat c where c.almacen = v_cod order by c.odoo_id;
    update almacen.ubicaciones h set padre_id = p.id
      from _t0_cat c, almacen.ubicaciones p
     where c.almacen = v_cod and h.almacen = v_cod and h.odoo_location_id = c.odoo_id
       and c.location_id is not null and p.almacen = v_cod and p.odoo_location_id = c.location_id;
    if not exists (select 1 from almacen.ubicaciones u where u.almacen = v_cod and u.activa and u.posicion = 'SIN UBICAR') then
      insert into almacen.ubicaciones (almacen, fuente, ruta, posicion, tipo)
      values (v_cod, 'kubera', v_cod || '/SIN UBICAR', 'SIN UBICAR', 'almacenaje');
    end if;
    insert into almacen.ubicaciones (almacen, fuente, ruta, posicion, tipo)
    values (v_cod, 'kubera', v_cod || '/POR VERIFICAR', 'POR VERIFICAR', 'por_verificar') returning id into v_pv;
    update _t0_semb s set ubic_id = u.id
      from almacen.ubicaciones u where s.almacen = v_cod and s.odoo_loc is not null and u.odoo_location_id = s.odoo_loc;
    update _t0_semb s set ubic_id = v_pv where s.almacen = v_cod and s.ref = 'POR_VERIFICAR';
    if exists (select 1 from _t0_semb s where s.almacen = v_cod and s.sembrado > 0 and s.ubic_id is null) then
      raise exception using errcode = '23514', constraint = 'desprender_cuadre',
        message = format('%s: renglones sembrados sin ubicación en el catálogo', v_cod);
    end if;
    -- La foto: lo sembrado por (SKU, ubicación); la reserva cruda, informativa.
    insert into almacen.desprendimiento_foto (desprendimiento_id, sentido, sku, ubicacion_id, ubicacion, fisico, reservado,
                                              odoo_quant_id, odoo_product_id)
    select r_d.id, 'saldo_inicial', s.sku, s.ubic_id, u.posicion, s.sembrado::integer, s.reservado::integer, null,
           pr.pid::integer
      from _t0_semb s
      join almacen.ubicaciones u on u.id = s.ubic_id
      left join (select q.k, q.almacen, 'odoo:' || q.location_id as ref,
                        case when count(distinct q.product_id) = 1 then min(q.product_id) end as pid
                   from _t0_q q where q.almacen = v_cod group by 1, 2, 3) pr
        on pr.k = s.k and pr.almacen = s.almacen and pr.ref = s.ref
     where s.almacen = v_cod and s.sembrado > 0;
    -- El libro conoce TODO SKU de core.products de la copia de esta bodega, aunque quede en 0 (§4.10).
    insert into almacen.stock_almacen (sku, almacen, fisico)
    select x.sku, v_cod, 0 from _t0_skus x where x.almacen = v_cod order by x.sku;
    -- El saldo inicial, SKU por SKU y ubicación por ubicación (el trigger de B4 escribe locations kubera y D16).
    for r_s in select s.sku, array_agg(s.ubic_id order by s.ubic_id) as us, array_agg(s.sembrado order by s.ubic_id) as ns
                 from _t0_semb s where s.almacen = v_cod and s.sembrado > 0 group by s.sku order by s.sku loop
      v_saldo := 0;
      for i in 1 .. cardinality(r_s.us) loop
        v_saldo := v_saldo + r_s.ns[i];
        insert into almacen.stock_mov (sku, almacen, ubicacion_id, delta, saldo_despues, motivo, ref, clave,
                                       quien, quien_nombre, via, nota)
        values (r_s.sku, v_cod, r_s.us[i], r_s.ns[i], v_saldo, 'saldo_inicial', 'DESP-' || r_d.id,
                format('ini:%s:%s:%s', r_d.id, r_s.us[i], r_s.sku), v_quien, 'T0', 'acta_t0', left(r_d.acta, 200));
      end loop;
      update almacen.stock_almacen sa set fisico = v_saldo where sa.sku = r_s.sku and sa.almacen = v_cod;
    end loop;

    -- 8. Cuadres DENTRO de la transacción
    select count(*) into v_n
      from (select l.sku, l.ubicacion_id, l.piezas from almacen.locations l where l.cedis = v_cod and l.origen = 'kubera') l
      full join (select s.sku, s.ubic_id, s.sembrado from _t0_semb s where s.almacen = v_cod and s.sembrado > 0) s
        on s.sku = l.sku and s.ubic_id = l.ubicacion_id
      full join (select m.sku, m.ubicacion_id, sum(m.delta) as d from almacen.stock_mov m
                  where m.almacen = v_cod and m.motivo = 'saldo_inicial' and m.ref = 'DESP-' || r_d.id group by 1, 2) m
        on m.sku = coalesce(s.sku, l.sku) and m.ubicacion_id = coalesce(s.ubic_id, l.ubicacion_id)
      full join (select f.sku, f.ubicacion_id, f.fisico from almacen.desprendimiento_foto f where f.desprendimiento_id = r_d.id) f
        on f.sku = coalesce(s.sku, l.sku, m.sku) and f.ubicacion_id = coalesce(s.ubic_id, l.ubicacion_id, m.ubicacion_id)
     where not (coalesce(l.piezas, 0) = coalesce(s.sembrado, 0) and coalesce(m.d, 0) = coalesce(s.sembrado, 0)
                and coalesce(f.fisico, 0) = coalesce(s.sembrado, 0));
    if v_n > 0 then
      raise exception using errcode = '23514', constraint = 'desprender_cuadre',
        message = format('%s: %s renglones donde locations ≠ saldo_inicial ≠ foto ≠ sembrado', v_cod, v_n);
    end if;
    select count(*) into v_n
      from (select * from almacen.stock_almacen x where x.almacen = v_cod) sa
      full join (select s.sku, sum(s.sembrado) as f, coalesce(sum(s.sembrado) filter (where not s.vendible), 0) as nv
                   from _t0_semb s where s.almacen = v_cod and s.sembrado > 0 group by 1) s
        on s.sku = sa.sku
     where sa.sku is null or coalesce(s.f, 0) <> sa.fisico or coalesce(s.nv, 0) <> sa.fisico_no_vendible;
    if v_n > 0 then
      raise exception using errcode = '23514', constraint = 'desprender_cuadre',
        message = format('%s: %s SKUs con stock_almacen ≠ sembrado (fisico o fisico_no_vendible)', v_cod, v_n);
    end if;
    -- [R corr] Σ crudos + Σ ajustes = sembrado, recalculado desde las TABLAS de origen (no desde _t0_semb):
    -- quants de la carga en las internas de la bodega (su almacén de Odoo o bajo sus raíces especiales) + los
    -- ajustes del lote (sin ubicacion_sin_bodega) = Σ stock_mov saldo_inicial de DESP-<id>.
    select coalesce(sum(q.quantity), 0) into v_crudo
      from odoo_historia.stock_quant q
      join odoo_historia.stock_location l on l.carga_id = q.carga_id and l.id = q.location_id and l.usage = 'internal'
      join odoo_historia.product_product p on p.carga_id = q.carga_id and p.id = q.product_id
     where q.carga_id = p_carga
       and (l.warehouse_id = v_wh or l.id in (select z.id from _t0_raiz z where z.almacen = v_cod));
    select coalesce(sum(j.delta), 0) into v_ajs from almacen.saldo_inicial_ajustes j
     where j.lote = p_lote and j.carga_id = p_carga and j.almacen = v_cod and j.regla <> 'ubicacion_sin_bodega';
    select coalesce(sum(m.delta), 0) into v_mov from almacen.stock_mov m where m.ref = 'DESP-' || r_d.id and m.motivo = 'saldo_inicial';
    if v_crudo + v_ajs <> v_mov then
      raise exception using errcode = '23514', constraint = 'desprender_cuadre',
        message = format('%s: Σ crudos de odoo_historia (%s) + Σ ajustes del lote (%s) ≠ Σ saldo_inicial (%s)', v_cod, v_crudo, v_ajs, v_mov);
    end if;
    if (select count(*) from almacen.stock_mov m where m.almacen = v_cod and m.ref = 'DESP-' || r_d.id) <> (v_tot ->> 'renglones')::bigint
       or (select count(*) from almacen.stock_almacen sa where sa.almacen = v_cod) <> (v_tot ->> 'skus')::bigint
       or (select coalesce(sum(sa.fisico), 0) from almacen.stock_almacen sa where sa.almacen = v_cod) <> (v_tot ->> 'fisico')::bigint
       or (select coalesce(sum(sa.fisico_no_vendible), 0) from almacen.stock_almacen sa where sa.almacen = v_cod) <> (v_tot ->> 'fisico_no_vendible')::bigint
       or (select coalesce(sum(l.piezas), 0) from almacen.locations l where l.cedis = v_cod and l.ubicacion_id = v_pv) <> (v_tot ->> 'por_verificar')::bigint then
      raise exception using errcode = '23514', constraint = 'desprender_cuadre',
        message = format('%s: Σ crudos + Σ ajustes ≠ sembrado, o renglones / SKUs / piezas ≠ p_esperado', v_cod);
    end if;
    v_res := v_res || jsonb_build_array(jsonb_build_object(
               'desprendimiento', r_d.id, 'almacen', v_cod,
               'renglones', (v_tot ->> 'renglones')::bigint, 'skus', (v_tot ->> 'skus')::bigint,
               'fisico', (v_tot ->> 'fisico')::bigint, 'fisico_no_vendible', (v_tot ->> 'fisico_no_vendible')::bigint,
               'por_verificar', (v_tot ->> 'por_verificar')::bigint,
               'ubicaciones', (select count(*) from almacen.ubicaciones u where u.almacen = v_cod)));
  end loop;

  -- 9. Los GUC vuelven a '' y se regresa el resumen
  perform set_config('app.almacen_escritor', '', true);
  perform set_config('app.t0_acto', '', true);
  return jsonb_build_object(
    'lote', p_lote, 'carga', p_carga, 'bodegas', v_res, 'reglas', v_calc -> 'reglas',
    'tex2_brandon', jsonb_build_object('renglones', v_br.renglones, 'piezas', v_br.piezas,
                                       'skus_distintos_vs_copia', v_brdif.distintos, 'neto_vs_copia', v_brdif.neto),
    'ms', round(extract(epoch from (clock_timestamp() - v_ini)) * 1000));
end $$;

-- ───────────────────────────────────────────────────────────────────────────
-- B5b. migrada_ov [T0 §4.9] [D4 PENDIENTE DE FIRMA]: las ventas vivas de la bitácora de Odoo
--      salen de ella ANTES de crear cualquier OV espejo (C′ hace terminal a migrada_ov).
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists almacen.t0_migradas (
  lote              text        not null,
  canal             text        not null,
  cuenta            text        not null,
  external_order_id text        not null,
  accion_antes      text        not null,
  odoo_order_id     bigint,
  odoo_name         text,
  almacen_id        integer,
  clase             text        not null,
  quien             text        not null,
  migrada_at        timestamptz not null default now(),
  constraint t0_migradas_pkey      primary key (canal, cuenta, external_order_id),
  constraint t0_migradas_clase_chk check (clase in ('viva', 'salio', 'muerta', 'sin_clasificar', 'sin_entrega_abierta'))
);
create trigger t0_migradas_solo_agregar before update or delete on almacen.t0_migradas
  for each row execute function ops.tg_solo_agregar();
create trigger t0_migradas_sin_truncate before truncate on almacen.t0_migradas
  for each statement execute function ops.tg_sin_truncate();

create or replace function almacen.t0_migrar_odoo_sale_orders(p_lote text, p_esperado jsonb, p_clases jsonb default '[]'::jsonb)
returns jsonb language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare
  v_quien text := nullif(current_setting('app.usuario', true), '');
  v_vivo  jsonb;
  v_n     bigint;
  v_m     bigint;
  v_cl    jsonb := coalesce(p_clases, '[]'::jsonb);
begin
  if v_quien is null then
    raise exception using errcode = '23514', constraint = 't0_migrar_odoo_sale_orders_quien', message = 't0_migrar_odoo_sale_orders exige app.usuario';
  end if;
  if p_lote is null or p_lote !~ '^T0-[0-9]{8}(-[a-z0-9]+)?$' then
    raise exception using errcode = '23514', constraint = 't0_migradas_lote', message = format('lote %s no tiene la forma T0-AAAAMMDD', p_lote);
  end if;
  perform 1 from ops.odoo_sale_orders s where s.accion in ('confirmada', 'espera_guia', 'espera_reserva') for update;
  select coalesce(jsonb_object_agg(x.canal, x.acciones), '{}'::jsonb) into v_vivo
    from (select y.canal, jsonb_object_agg(y.accion, y.n) as acciones
            from (select lower(s.canal) as canal, s.accion, count(*) as n
                    from ops.odoo_sale_orders s where s.accion in ('confirmada', 'espera_guia', 'espera_reserva')
                   group by 1, 2) y group by 1) x;
  if v_vivo is distinct from coalesce(p_esperado, '{}'::jsonb) then
    raise exception using errcode = '23514', constraint = 't0_migradas_conteo',
      message = format('filas vivas %s; el acta dice %s', v_vivo, coalesce(p_esperado::text, 'NULL'));
  end if;
  if jsonb_typeof(v_cl) <> 'array'
     or exists (select 1 from jsonb_array_elements(v_cl) e
                 where coalesce(e ->> 'clase', '') not in ('viva', 'salio', 'muerta', 'sin_clasificar')
                    or not exists (select 1 from ops.odoo_sale_orders s
                                    where lower(s.canal) = lower(btrim(e ->> 'canal')) and upper(btrim(s.cuenta)) = upper(btrim(e ->> 'cuenta'))
                                      and s.external_order_id = btrim(e ->> 'external_order_id')
                                      and s.accion in ('confirmada', 'espera_guia', 'espera_reserva')))
     or (select count(*) from jsonb_array_elements(v_cl))
        <> (select count(distinct (lower(btrim(e ->> 'canal')), upper(btrim(e ->> 'cuenta')), btrim(e ->> 'external_order_id')))
              from jsonb_array_elements(v_cl) e) then
    raise exception using errcode = '23514', constraint = 't0_migradas_clases',
      message = 'p_clases: cada renglón es una venta VIVA de la bitácora, una sola vez, con clase viva | salio | muerta | sin_clasificar';
  end if;
  insert into almacen.t0_migradas (lote, canal, cuenta, external_order_id, accion_antes, odoo_order_id, odoo_name,
                                   almacen_id, clase, quien)
  select p_lote, s.canal, s.cuenta, s.external_order_id, s.accion, s.odoo_order_id, s.odoo_name, s.almacen_id,
         coalesce(c.clase, 'sin_entrega_abierta'), v_quien
    from ops.odoo_sale_orders s
    left join (select lower(btrim(e ->> 'canal')) as canal, upper(btrim(e ->> 'cuenta')) as cuenta,
                      btrim(e ->> 'external_order_id') as ext, e ->> 'clase' as clase
                 from jsonb_array_elements(v_cl) e) c
      on c.canal = lower(s.canal) and c.cuenta = upper(btrim(s.cuenta)) and c.ext = s.external_order_id
   where s.accion in ('confirmada', 'espera_guia', 'espera_reserva');
  get diagnostics v_n = row_count;
  perform set_config('app.almacen_escritor', 't0:' || txid_current(), true);
  update ops.odoo_sale_orders s
     set accion = 'migrada_ov', motivo = format('T0 %s: la venta sale de la bitácora de Odoo (acta)', p_lote)
    from almacen.t0_migradas m
   where m.lote = p_lote and m.canal = s.canal and m.cuenta = s.cuenta and m.external_order_id = s.external_order_id
     and s.accion in ('confirmada', 'espera_guia', 'espera_reserva');
  get diagnostics v_m = row_count;
  perform set_config('app.almacen_escritor', '', true);
  if v_m <> v_n then
    raise exception using errcode = '23514', constraint = 't0_migradas_conteo',
      message = format('se anotaron %s y se migraron %s', v_n, v_m);
  end if;
  return jsonb_build_object('lote', p_lote, 'migradas', v_n, 'por_canal', v_vivo,
           'por_clase', coalesce((select jsonb_object_agg(x.clase, x.n)
                                    from (select m.clase, count(*) as n from almacen.t0_migradas m where m.lote = p_lote group by 1) x), '{}'::jsonb));
end $$;

-- ───────────────────────────────────────────────────────────────────────────
-- B5c. Vistas de existencias [T0 §4.10]: grupo por_verificar y el ARREGLO del «SKU vendido hasta 0».
--      existencias_bodega_v conserva nombre, columnas, tipos y orden (contrato con WOO SALIDA).
-- ───────────────────────────────────────────────────────────────────────────
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
 where not exists (select 1 from almacen.locations l where l.cedis = sa.almacen and l.sku = sa.sku and l.origen = 'kubera');

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
       coalesce(sum(b.fisico) filter (where b.grupo = 'por_verificar'), 0) as por_verificar   -- nueva, al final [T0]
  from almacen.existencias_bodega_v b
 group by b.sku;

-- ───────────────────────────────────────────────────────────────────────────
-- B5d. OV espejo [T0 §4.11]. Replica el camino del panel (borrador → confirmada → apartar en la MISMA
--      transacción; entregar con su salida_ov) y respeta todas las guardas de Brandon. Solo SQL de MAESTRO, en acta.
-- ───────────────────────────────────────────────────────────────────────────
create or replace function almacen.ov_entregar(p_ov_id bigint, p_ubicaciones jsonb default null)
returns jsonb language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
-- p_ubicaciones: {"<ov_lineas.id>": <ubicacion_id>, …}; sin ubicación → «SIN UBICAR» (D15).
-- Llave opcional "_firma": {"quien_nombre", "via", "nota"} para los movimientos (la usa el diario).
declare
  v_quien  text := nullif(current_setting('app.usuario', true), '');
  v_nombre text := coalesce(nullif(p_ubicaciones -> '_firma' ->> 'quien_nombre', ''), 'MAESTRO (acta T0)');
  v_via    text := coalesce(nullif(p_ubicaciones -> '_firma' ->> 'via', ''), 'acta_t0');
  v_nota   text := nullif(p_ubicaciones -> '_firma' ->> 'nota', '');
  r_o      record;
  r_l      record;
  v_saldo  integer;
  v_ubic   bigint;
  v_lineas jsonb := '[]'::jsonb;
begin
  if v_quien is null then
    raise exception using errcode = '23514', constraint = 'ov_entregar_quien', message = 'ov_entregar exige app.usuario';
  end if;
  select * into r_o from ventas.ov_ordenes o where o.id = p_ov_id for update;
  if r_o.id is null or r_o.estado <> 'confirmada' or r_o.borrada_at is not null or r_o.canal_cancelo_at is not null
     or (r_o.tipo = 'full' and r_o.envio_ref is null) then
    raise exception using errcode = '23514', constraint = 'ov_entregar_estado',
      message = format('OV %s no está confirmada y entregable (estado %s)', coalesce(r_o.folio, p_ov_id::text), coalesce(r_o.estado, 'NULL'));
  end if;
  for r_l in select * from ventas.ov_lineas li where li.orden_id = p_ov_id and li.entregado_at is null
              order by li.linea, li.id for update loop
    if r_l.reservado <> r_l.cantidad or r_l.almacen is null then
      raise exception using errcode = '23514', constraint = 'ov_entregar_no_apartado',
        message = format('OV %s renglón %s: no está apartado completo', r_o.folio, r_l.linea);
    end if;
    v_ubic := case when p_ubicaciones ? r_l.id::text then (p_ubicaciones ->> r_l.id::text)::bigint end;
    perform 1 from almacen.stock_almacen sa where sa.sku = r_l.sku and sa.almacen = r_l.almacen for update;
    update almacen.stock_almacen sa set fisico = sa.fisico - r_l.cantidad, apartado = sa.apartado - r_l.cantidad
     where sa.sku = r_l.sku and sa.almacen = r_l.almacen
    returning sa.fisico into v_saldo;
    insert into almacen.stock_mov (sku, almacen, ubicacion_id, delta, saldo_despues, motivo, ref, ov_linea_id, clave,
                                   quien, quien_nombre, via, nota)
    values (r_l.sku, r_l.almacen, v_ubic, -r_l.cantidad, v_saldo, 'salida_ov', r_o.folio, r_l.id,
            format('ov:%s:linea:%s:salida', r_o.id, r_l.id), v_quien, v_nombre, v_via, v_nota);
    update ventas.ov_lineas li set reservado = 0, entregado = r_l.cantidad, entregado_at = now(), entregado_por = v_quien
     where li.id = r_l.id;
    v_lineas := v_lineas || jsonb_build_array(jsonb_build_object('id', r_l.id, 'sku', r_l.sku, 'cantidad', r_l.cantidad,
                                                                 'almacen', r_l.almacen, 'ubicacion_id', v_ubic));
  end loop;
  if jsonb_array_length(v_lineas) = 0 then
    raise exception using errcode = '23514', constraint = 'ov_entregar_estado',
      message = format('OV %s: no queda ningún renglón por entregar', r_o.folio);
  end if;
  update ventas.ov_ordenes o set estado = 'entregada', entregada_at = now(), entregada_por = v_quien,
                                 entregada_nombre = v_nombre, rev = o.rev + 1
   where o.id = p_ov_id;
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  values (p_ov_id, 'sistema', 'entregada', format('Entregada por %s: %s renglones', v_nombre, jsonb_array_length(v_lineas)),
          jsonb_build_object('clave', r_o.clave, 'lineas', v_lineas), v_quien, v_nombre, 'claude');
  return jsonb_build_object('id', r_o.id, 'folio', r_o.folio, 'estado', 'entregada', 'lineas', v_lineas);
end $$;

create or replace function almacen.ov_espejo(p_venta jsonb, p_lineas jsonb)
returns jsonb language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare
  v_quien    text := nullif(current_setting('app.usuario', true), '');
  v_nombre   text := 'MAESTRO (acta T0)';
  v_clave    text := nullif(btrim(p_venta ->> 'clave'), '');
  v_tipo     text := coalesce(nullif(btrim(p_venta ->> 'tipo'), ''), 'venta');
  v_mp_canal text := nullif(lower(btrim(p_venta ->> 'mp_canal')), '');
  v_mp_cta   text := nullif(upper(btrim(p_venta ->> 'mp_cuenta')), '');
  v_mp_orden text := nullif(btrim(p_venta ->> 'mp_orden'), '');
  v_canal    text;
  v_cliente  text;
  r_ya       record;
  r_l        record;
  v_id       bigint;
  v_lid      bigint;
  v_folio    text;
  v_ult      integer;
  v_alm      text;
  v_ubic     jsonb := '{}'::jsonb;
  v_lineas   jsonb := '[]'::jsonb;
  v_ent      jsonb;
begin
  if v_quien is null then
    raise exception using errcode = '23514', constraint = 'ov_espejo_quien', message = 'ov_espejo exige app.usuario';
  end if;
  if v_clave is null or v_clave !~ '^(t0:[a-z_]+:[A-Z0-9_]+:.+|t0:S:S[0-9]+|diario:[0-9]+)$' then
    raise exception using errcode = '23514', constraint = 'ov_espejo_clave',
      message = format('clave %s: t0:<mp_canal>:<MP_CUENTA>:<mp_orden>, t0:S:<S…> o diario:<id>', coalesce(v_clave, 'NULL'));
  end if;
  if p_lineas is null or jsonb_typeof(p_lineas) <> 'array' or jsonb_array_length(p_lineas) = 0 then
    raise exception using errcode = '23514', constraint = 'ov_espejo_lineas', message = 'ov_espejo exige renglones';
  end if;
  v_canal := coalesce(nullif(lower(btrim(p_venta ->> 'canal')), ''), v_mp_canal);
  -- SEG-06: nunca el comprador. Venta de canal → el canal; si no, Shein / Mayoreo / la tienda.
  v_cliente := case when v_mp_orden is not null then v_mp_canal else nullif(btrim(p_venta ->> 'cliente'), '') end;
  -- Idempotente: si ya hay una OV viva con esa clave o con ese trío, no se crea ni se aparta otra vez.
  select o.id, o.folio, o.estado into r_ya from ventas.ov_ordenes o
   where o.borrada_at is null and o.estado <> 'cancelada'
     and (o.clave = v_clave
          or (v_mp_orden is not null and o.mp_canal = v_mp_canal and o.mp_cuenta = v_mp_cta and o.mp_orden = v_mp_orden))
   order by o.id limit 1;
  if r_ya.id is not null then
    return jsonb_build_object('id', r_ya.id, 'folio', r_ya.folio, 'resultado', 'ya_existia', 'estado', r_ya.estado,
             'lineas', (select coalesce(jsonb_agg(jsonb_build_object('id', li.id, 'linea', li.linea, 'sku', li.sku,
                                                                     'cantidad', li.cantidad, 'almacen', li.almacen) order by li.linea), '[]'::jsonb)
                          from ventas.ov_lineas li where li.orden_id = r_ya.id));
  end if;
  -- 1. folio (mismo formato que el panel)
  update ventas.ov_folio f set ultimo = f.ultimo + 1 where f.id = 1 returning f.ultimo into v_ult;
  v_folio := 'OV-' || lpad(v_ult::text, greatest(5, length(v_ult::text)), '0');
  -- 2. borrador
  insert into ventas.ov_ordenes (folio, estado, tipo, cliente, canal, mp_canal, mp_cuenta, mp_orden, full_tienda, envio_ref,
                                 descripcion, guia, paqueteria, fecha_venta, entrega_limite, moneda, total, comision,
                                 precio_origen, creado_por, creado_nombre, creado_via, clave)
  values (v_folio, 'borrador', v_tipo, v_cliente, v_canal, v_mp_canal, v_mp_cta, v_mp_orden,
          nullif(btrim(p_venta ->> 'full_tienda'), ''), nullif(btrim(p_venta ->> 'envio_ref'), ''),
          coalesce(nullif(p_venta ->> 'descripcion', ''), nullif(p_venta ->> 'nota', '')),
          nullif(p_venta ->> 'guia', ''), nullif(p_venta ->> 'paqueteria', ''),
          nullif(p_venta ->> 'fecha_venta', '')::timestamptz, nullif(p_venta ->> 'entrega_limite', '')::timestamptz,
          coalesce(nullif(upper(btrim(p_venta ->> 'moneda')), ''), 'MXN'),
          coalesce(nullif(p_venta ->> 'total', '')::numeric, 0), coalesce(nullif(p_venta ->> 'comision', '')::numeric, 0),
          case when v_mp_orden is not null then 'marketplace' else 'manual' end,
          v_quien, v_nombre, 'claude', v_clave)
  returning id into v_id;
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  values (v_id, 'sistema', 'creada', format('Creada por %s: %s', v_nombre, v_clave),
          jsonb_build_object('clave', v_clave, 'acta', p_venta ->> 'nota'), v_quien, v_nombre, 'claude');
  -- 3. renglones (en borrador)
  for r_l in select coalesce((x ->> 'linea')::integer, t.o::integer) as linea, btrim(x ->> 'sku') as sku,
                    (x ->> 'cantidad')::integer as cantidad, nullif(upper(btrim(x ->> 'almacen')), '') as almacen,
                    nullif(x ->> 'ubicacion_id', '')::bigint as ubicacion_id,
                    coalesce(nullif(x ->> 'precio_unitario', '')::numeric, 0) as precio, nullif(x ->> 'titulo', '') as titulo
               from jsonb_array_elements(p_lineas) with ordinality as t(x, o) order by t.o loop
    insert into ventas.ov_lineas (orden_id, linea, sku, titulo, cantidad, precio_unitario)
    values (v_id, r_l.linea, r_l.sku, r_l.titulo, r_l.cantidad, r_l.precio) returning id into v_lid;
    v_lineas := v_lineas || jsonb_build_array(jsonb_build_object('id', v_lid, 'linea', r_l.linea, 'sku', r_l.sku,
                                                                 'cantidad', r_l.cantidad, 'almacen', r_l.almacen));
    if r_l.ubicacion_id is not null then
      v_ubic := v_ubic || jsonb_build_object(v_lid::text, r_l.ubicacion_id);
    end if;
  end loop;
  -- 4. confirmada (confirmada_at = now(): la guarda de ov_lineas solo deja apartar en la transacción que confirmó)
  update ventas.ov_ordenes o set estado = 'confirmada', confirmada_at = now(), confirmada_por = v_quien,
                                 confirmada_nombre = v_nombre, rev = o.rev + 1
   where o.id = v_id;
  -- 5–6. cada renglón a su bodega y apartado (la primera kubera con surte_ventas y admite_ov, por preferencia, que alcance)
  for r_l in select li.id, li.sku, li.cantidad, (e ->> 'almacen') as almacen
               from ventas.ov_lineas li
               join jsonb_array_elements(v_lineas) e on (e ->> 'id')::bigint = li.id
              order by li.linea loop
    if r_l.almacen is not null then
      select sa.almacen into v_alm
        from almacen.stock_almacen sa join almacen.almacenes a on a.codigo = sa.almacen
       where sa.sku = r_l.sku and sa.almacen = r_l.almacen and a.fuente = 'kubera' and a.admite_ov
         and sa.libre >= r_l.cantidad
         for update of sa;
    else
      select sa.almacen into v_alm
        from almacen.stock_almacen sa join almacen.almacenes a on a.codigo = sa.almacen
       where sa.sku = r_l.sku and a.fuente = 'kubera' and a.surte_ventas and a.admite_ov and sa.libre >= r_l.cantidad
       order by a.preferencia nulls last, a.codigo
       limit 1
         for update of sa;
    end if;
    if v_alm is null then
      raise exception using errcode = '23514', constraint = 'no_alcanzo',
        message = format('%s: %s pide %s y ninguna bodega %s tiene ese libre', v_folio, r_l.sku, r_l.cantidad,
                         coalesce(r_l.almacen, 'que surte'));
    end if;
    update ventas.ov_lineas li set almacen = v_alm, reservado = li.cantidad where li.id = r_l.id;
    update almacen.stock_almacen sa set apartado = sa.apartado + r_l.cantidad where sa.sku = r_l.sku and sa.almacen = v_alm;
    v_lineas := (select jsonb_agg(case when (t.e ->> 'id')::bigint = r_l.id then t.e || jsonb_build_object('almacen', v_alm) else t.e end
                                  order by t.o)
                   from jsonb_array_elements(v_lineas) with ordinality as t(e, o));
    v_alm := null;
  end loop;
  -- 7. mensaje de confirmada
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  values (v_id, 'sistema', 'confirmada', format('Confirmada: %s renglones apartados', jsonb_array_length(v_lineas)),
          jsonb_build_object('clave', v_clave, 'acta', p_venta ->> 'nota', 'lineas', v_lineas), v_quien, v_nombre, 'claude');
  if coalesce((p_venta ->> 'entregar')::boolean, false) then
    if p_venta ? '_firma' then
      v_ubic := v_ubic || jsonb_build_object('_firma', p_venta -> '_firma');
    end if;
    v_ent := almacen.ov_entregar(v_id, v_ubic);
  end if;
  return jsonb_build_object('id', v_id, 'folio', v_folio, 'resultado', 'creada', 'lineas', v_lineas, 'entrega', v_ent);
end $$;

-- Las ventas DROP sin OV desde p_desde, por orden: la MISMA consulta que _SQL_PENDIENTES de stock_libro (32a6af6).
-- [R corr] + creado_at de la venta (al final): t0_ov_espejos y t0_cuadre la topan con la hora de la foto de Woo.
create or replace function almacen.t0_pendientes(p_desde timestamptz)
returns table (canal text, cuenta text, orden text, sku text, pzs bigint, creado_at timestamptz)
language sql stable security invoker set search_path = pg_catalog, public as $$
  select o.canal, upper(btrim(o.cuenta)), o.external_order_id, upper(i.sku::text), sum(i.cantidad)::bigint, o.creado_at
    from channel.order_items i
    join channel.orders o
      on o.canal = i.canal and o.cuenta = i.cuenta and o.external_order_id = i.external_order_id
   where o.creado_at >= p_desde
     and not coalesce(i.es_fulfillment, false)
     and coalesce(o.estado_wc, '') <> 'cancelled'
     and i.sku is not null and coalesce(i.cantidad, 0) > 0
     and not exists (
           select 1 from ventas.ov_ordenes v
            where v.mp_canal = o.canal and v.mp_orden = o.external_order_id
              and v.mp_cuenta = upper(btrim(o.cuenta))
              and v.estado in ('confirmada', 'entregada'))
   group by 1, 2, 3, 4, 6
$$;

-- [R corr] p_hasta = la hora de la foto de Woo (el leido_at más viejo de t0_woo_foto del lote, o antes): solo las
-- ventas creadas hasta entonces llevan OV espejo en el acto; las de después quedan pendientes y stock_libro las resta
-- (STOCK_LIBRO_DESDE = el corte). Así una venta que entra durante el acto no lo tumba.
create or replace function almacen.t0_ov_espejos(p_lote text, p_desde timestamptz, p_posteriores jsonb, p_l4 jsonb, p_hasta timestamptz)
returns jsonb language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare
  v_quien text := nullif(current_setting('app.usuario', true), '');
  v_pos   jsonb := coalesce(p_posteriores, '[]'::jsonb);
  v_l4    jsonb := coalesce(p_l4, '[]'::jsonb);
  v_txt   text;
  v_n     bigint;
  r_x     record;
  r_osa   record;
  v_e     jsonb;
  v_res_p jsonb := '[]'::jsonb;
  v_res_l jsonb := '[]'::jsonb;
  v_name  text;
  v_tipo  text;
  v_tienda text;
  v_alm   text;
  v_lin   jsonb;
begin
  if v_quien is null then
    raise exception using errcode = '23514', constraint = 't0_ov_espejos_quien', message = 't0_ov_espejos exige app.usuario';
  end if;
  if jsonb_typeof(v_pos) <> 'array' or jsonb_typeof(v_l4) <> 'array' then
    raise exception using errcode = '23514', constraint = 't0_espejos_lista_distinta', message = 'p_posteriores y p_l4 son arreglos';
  end if;
  if p_hasta is null or p_hasta > now() or p_hasta < p_desde then
    raise exception using errcode = '23514', constraint = 't0_espejos_hasta',
      message = format('p_hasta (%s) es la hora de la foto de Woo: entre el corte y ahora', coalesce(p_hasta::text, 'NULL'));
  end if;
  -- La lista de posteriores tiene que ser IGUAL a la de stock_libro (por orden) hasta p_hasta …
  select string_agg(format('%s/%s/%s (%s)', coalesce(e.canal, g.canal), coalesce(e.cuenta, g.cuenta), coalesce(e.orden, g.orden),
                           case when e.orden is null then 'sobra en p_posteriores' else 'falta en p_posteriores' end), '; ')
    into v_txt
    from (select distinct t.canal, t.cuenta, t.orden from almacen.t0_pendientes(p_desde) t where t.creado_at <= p_hasta) e
    full join (select distinct lower(btrim(x ->> 'canal')) as canal, upper(btrim(x ->> 'cuenta')) as cuenta, btrim(x ->> 'orden') as orden
                 from jsonb_array_elements(v_pos) x) g
      on g.canal = e.canal and g.cuenta = e.cuenta and g.orden = e.orden
   where e.orden is null or g.orden is null;
  if v_txt is not null
     or (select count(*) from jsonb_array_elements(v_pos))
        <> (select count(distinct (lower(btrim(x ->> 'canal')), upper(btrim(x ->> 'cuenta')), btrim(x ->> 'orden'))) from jsonb_array_elements(v_pos) x) then
    raise exception using errcode = '23514', constraint = 't0_espejos_lista_distinta',
      message = format('las ventas posteriores al corte no son las de stock_libro: %s', left(coalesce(v_txt, 'repetidas en p_posteriores'), 800));
  end if;
  -- … y sus renglones (SKU, Σ cantidad) iguales a los de order_items.
  select string_agg(format('%s/%s/%s %s: canal %s, lista %s', coalesce(e.canal, g.canal), coalesce(e.cuenta, g.cuenta),
                           coalesce(e.orden, g.orden), coalesce(e.sku, g.sku), coalesce(e.pzs, 0), coalesce(g.pzs, 0)), '; ')
    into v_txt
    from (select * from almacen.t0_pendientes(p_desde) t where t.creado_at <= p_hasta) e
    full join (select lower(btrim(x ->> 'canal')) as canal, upper(btrim(x ->> 'cuenta')) as cuenta, btrim(x ->> 'orden') as orden,
                      upper(btrim(y ->> 'sku')) as sku, sum((y ->> 'cantidad')::bigint) as pzs
                 from jsonb_array_elements(v_pos) x, jsonb_array_elements(coalesce(x -> 'lineas', '[]'::jsonb)) y
                group by 1, 2, 3, 4) g
      on g.canal = e.canal and g.cuenta = e.cuenta and g.orden = e.orden and g.sku = e.sku
   where coalesce(e.pzs, -1) <> coalesce(g.pzs, -1);
  if v_txt is not null then
    raise exception using errcode = '23514', constraint = 't0_espejos_lineas_distintas', message = left(v_txt, 900);
  end if;
  -- Ventas posteriores al corte → OV espejo con el trío mp_* (nunca correccion ni ajuste_conteo).
  for r_x in select x from jsonb_array_elements(v_pos) with ordinality t(x, o) order by t.o loop
    select o.creado_at, o.total, o.comision into r_osa
      from channel.orders o
     where o.canal = lower(btrim(r_x.x ->> 'canal')) and upper(btrim(o.cuenta)) = upper(btrim(r_x.x ->> 'cuenta'))
       and o.external_order_id = btrim(r_x.x ->> 'orden');
    v_e := almacen.ov_espejo(
             jsonb_build_object(
               'clave', format('t0:%s:%s:%s', lower(btrim(r_x.x ->> 'canal')), upper(btrim(r_x.x ->> 'cuenta')), btrim(r_x.x ->> 'orden')),
               'tipo', 'venta', 'canal', lower(btrim(r_x.x ->> 'canal')),
               'mp_canal', lower(btrim(r_x.x ->> 'canal')), 'mp_cuenta', upper(btrim(r_x.x ->> 'cuenta')), 'mp_orden', btrim(r_x.x ->> 'orden'),
               'fecha_venta', r_osa.creado_at, 'total', greatest(coalesce(r_osa.total, 0), 0), 'comision', greatest(coalesce(r_osa.comision, 0), 0),
               'entregar', coalesce((r_x.x ->> 'entregar')::boolean, false),
               'nota', format('T0 %s: venta posterior al corte', p_lote)),
             (select jsonb_agg(jsonb_build_object('linea', y.n, 'sku', y.sku, 'cantidad', y.c) order by y.n)
                from (select row_number() over (order by upper(btrim(z ->> 'sku'))) as n, upper(btrim(z ->> 'sku')) as sku,
                             sum((z ->> 'cantidad')::integer) as c
                        from jsonb_array_elements(r_x.x -> 'lineas') z group by upper(btrim(z ->> 'sku'))) y));
    v_res_p := v_res_p || jsonb_build_array(v_e);
  end loop;
  -- Las S abiertas que L4 declaró vivas.
  for r_x in select x from jsonb_array_elements(v_l4) with ordinality t(x, o) order by t.o loop
    v_name := btrim(r_x.x ->> 'odoo_name');
    if v_name is null or v_name !~ '^S[0-9]+$' or jsonb_typeof(r_x.x -> 'lineas') is distinct from 'array'
       or jsonb_array_length(r_x.x -> 'lineas') = 0 then
      raise exception using errcode = '23514', constraint = 't0_espejos_l4',
        message = format('L4: cada S lleva odoo_name S<n> y sus renglones (%s)', coalesce(v_name, 'NULL'));
    end if;
    select count(*) into v_n from ops.odoo_sale_orders s where s.odoo_name = v_name and s.accion = 'migrada_ov';
    if v_n > 1 then
      raise exception using errcode = '23514', constraint = 't0_espejos_l4', message = format('L4: %s está en dos ventas', v_name);
    end if;
    if v_n = 1 then                                                   -- de marketplace: con su trío (ya en migrada_ov)
      select s.canal, s.cuenta, s.external_order_id, s.almacen_id into r_osa from ops.odoo_sale_orders s
       where s.odoo_name = v_name and s.accion = 'migrada_ov';
      -- [R corr] el renglón sin almacén aparta en la bodega de Odoo de la venta (135→TEXCO, 150→TEX2, 142→DROP)
      select d.almacen into v_alm from almacen.desprendimientos d
       where d.odoo_warehouse_id = r_osa.almacen_id and d.estado = 'hecha' order by d.id desc limit 1;
      select jsonb_agg(case when nullif(btrim(t.x ->> 'almacen'), '') is null and v_alm is not null
                            then t.x || jsonb_build_object('almacen', v_alm) else t.x end order by t.o)
        into v_lin from jsonb_array_elements(r_x.x -> 'lineas') with ordinality as t(x, o);
      v_e := almacen.ov_espejo(
               jsonb_build_object('clave', format('t0:%s:%s:%s', lower(r_osa.canal), upper(btrim(r_osa.cuenta)), r_osa.external_order_id),
                                  'tipo', 'venta', 'canal', lower(r_osa.canal), 'mp_canal', lower(r_osa.canal),
                                  'mp_cuenta', upper(btrim(r_osa.cuenta)), 'mp_orden', r_osa.external_order_id,
                                  'entregar', coalesce((r_x.x ->> 'entregar')::boolean, false),
                                  'nota', format('T0 %s: %s viva (L4)', p_lote, v_name)),
               v_lin);
      v_alm := null;
    else                                                              -- FULL o mayoreo: sin mp_*, clave t0:S:<S…>
      -- [R corr] Sin valor por omisión: si la S está en la bitácora con otra acción (no migrada_ov), es de marketplace
      -- y su fila no se migró (o el odoo_name está mal): falla. Si no está, el tipo full | mayoreo es explícito.
      select string_agg(distinct s.accion, ', ') into v_txt from ops.odoo_sale_orders s where s.odoo_name = v_name;
      if v_txt is not null then
        raise exception using errcode = '23514', constraint = 't0_espejos_l4',
          message = format('L4 %s: está en la bitácora con acción %s, no migrada_ov (clasifíquela en t0_migrar_odoo_sale_orders o corrija L4)', v_name, v_txt);
      end if;
      v_tipo := nullif(btrim(r_x.x ->> 'tipo'), '');
      if v_tipo = 'full' then
        v_tienda := btrim(r_x.x ->> 'full_tienda');
        v_e := almacen.ov_espejo(
                 jsonb_build_object('clave', 't0:S:' || v_name, 'tipo', 'full', 'full_tienda', v_tienda,
                                    'canal', case when v_tienda like 'meli:%' then 'mercado_libre' else v_tienda end,
                                    'cliente', v_tienda, 'envio_ref', r_x.x ->> 'envio_ref',
                                    'entregar', coalesce((r_x.x ->> 'entregar')::boolean, false),
                                    'nota', format('T0 %s: %s viva (L4, FULL)', p_lote, v_name)),
                 r_x.x -> 'lineas');
      elsif v_tipo = 'mayoreo' then
        v_e := almacen.ov_espejo(
                 jsonb_build_object('clave', 't0:S:' || v_name, 'tipo', 'venta', 'canal', coalesce(nullif(btrim(r_x.x ->> 'canal'), ''), 'otro'),
                                    'cliente', coalesce(nullif(btrim(r_x.x ->> 'cliente'), ''), 'Mayoreo'),
                                    'entregar', coalesce((r_x.x ->> 'entregar')::boolean, false),
                                    'nota', format('T0 %s: %s viva (L4)', p_lote, v_name)),
                 r_x.x -> 'lineas');
      else
        raise exception using errcode = '23514', constraint = 't0_espejos_l4',
          message = format('L4 %s: sin venta en la bitácora, tipo debe ser full o mayoreo explícito (%s)', v_name, coalesce(v_tipo, 'vacío'));
      end if;
    end if;
    v_res_l := v_res_l || jsonb_build_array(v_e || jsonb_build_object('odoo_name', v_name));
  end loop;
  select count(*) into v_n from almacen.t0_pendientes(p_desde) t where t.creado_at <= p_hasta;
  if v_n > 0 then
    raise exception using errcode = '23514', constraint = 't0_espejos_pendientes',
      message = format('quedan %s renglones de venta sin OV entre %s y %s', v_n, p_desde, p_hasta);
  end if;
  return jsonb_build_object('lote', p_lote, 'posteriores', v_res_p, 'l4', v_res_l);
end $$;

-- ───────────────────────────────────────────────────────────────────────────
-- B5e. Cuadre por partidas contra Woo [T0 §4.12]: ningún SKU sube contra Woo sin una partida con referencia.
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists almacen.t0_woo_foto (
  lote      text        not null,
  sku       citext      not null,
  stock_woo integer,
  gestiona  boolean     not null,
  leido_at  timestamptz not null,
  fuente    text        not null,                 -- archivo y sha256 (comparar_libro_vs_woo --woo vivo)
  constraint t0_woo_foto_pkey primary key (lote, sku)
);
create table if not exists almacen.t0_partidas (
  id         bigint      generated always as identity,
  lote       text        not null,
  sku        citext      not null,
  tipo       text        not null,
  cantidad   integer     not null,                -- con signo: + si Woo está arriba del libro
  referencia text        not null,
  nota       text,
  quien      text        not null default nullif(current_setting('app.usuario', true), ''),
  creado_at  timestamptz not null default now(),
  constraint t0_partidas_pkey     primary key (id),
  constraint t0_partidas_tipo_chk check (tipo in ('venta_posterior', 'reserva_viva', 'reserva_muerta', 'reserva_salio', 'envio_full',
                                                  'salida_mayoreo', 'recepcion', 'retiro_full', 'devolucion', 'conteo', 'por_verificar',
                                                  'huerfano', 'duplicado', 'recodificacion', 'cancelacion_tardia', 'explicado_t0')),
  constraint t0_partidas_cant_chk check (cantidad <> 0),
  constraint t0_partidas_ref_chk  check (length(btrim(referencia)) > 0)
);
create index if not exists t0_partidas_lote_ix on almacen.t0_partidas (lote, sku);
create trigger t0_woo_foto_solo_agregar before update or delete on almacen.t0_woo_foto
  for each row execute function ops.tg_solo_agregar();
create trigger t0_woo_foto_sin_truncate before truncate on almacen.t0_woo_foto
  for each statement execute function ops.tg_sin_truncate();
create trigger t0_partidas_solo_agregar before update or delete on almacen.t0_partidas
  for each row execute function ops.tg_solo_agregar();
create trigger t0_partidas_sin_truncate before truncate on almacen.t0_partidas
  for each statement execute function ops.tg_sin_truncate();

-- p_lista [R seg/corr, D4 firmado]: la huella de la lista aprobada FIRMADA en el acta, calculada por el runner desde el
-- CSV firmado: {"lista_aprobada": <n>, "sha256": "<hex>", "aprobadores": ["correo", …]}.
--   sha256 = sha256 UTF-8 de los renglones lista_aprobada «SKU_EN_MAYÚSCULAS|grupo|aprobado_por», ordenados por SKU
--   (orden de bytes, collate "C") y unidos con '\n', sin salto final (lista vacía: sha256 de '').
-- La hora de la foto de Woo (t0_woo_foto.leido_at, por SKU) topa las ventas pendientes: una venta creada después de
-- leer el SKU en Woo no entra al cuadre (Woo tampoco la restó) y queda pendiente para stock_libro.
create or replace function almacen.t0_cuadre(p_lote text, p_desde timestamptz, p_lista jsonb)
returns jsonb language plpgsql stable security invoker set search_path = pg_catalog, public as $$
declare v_txt text; v_res jsonb; v_max timestamptz; v_n bigint; v_sha text; v_mal text;
begin
  -- 0. [D4 firmado] La lista aprobada es la firmada: conteo, sha256 y aprobadores del acta.
  if p_lista is null or jsonb_typeof(p_lista) <> 'object' or jsonb_typeof(p_lista -> 'lista_aprobada') <> 'number'
     or coalesce(p_lista ->> 'sha256', '') !~ '^[0-9a-f]{64}$' or jsonb_typeof(p_lista -> 'aprobadores') <> 'array' then
    raise exception using errcode = '23514', constraint = 't0_d4_lista_no_firmada',
      message = 'p_lista: {"lista_aprobada": n, "sha256": "<64 hex>", "aprobadores": [...]} del acta';
  end if;
  select count(*), encode(sha256(convert_to(coalesce(string_agg(upper(t.sku::text) || '|' || t.grupo || '|' || t.aprobado_por, E'\n'
                                                              order by upper(t.sku::text) collate "C"), ''), 'UTF8')), 'hex'),
         string_agg(distinct t.aprobado_por, ', ') filter (where not (to_jsonb(t.aprobado_por) <@ (p_lista -> 'aprobadores')))
    into v_n, v_sha, v_mal
    from almacen.t0_skus_sin_libro t where t.lote = p_lote and t.decision = 'lista_aprobada';
  if v_n <> (p_lista ->> 'lista_aprobada')::bigint or v_sha <> p_lista ->> 'sha256' or v_mal is not null then
    raise exception using errcode = '23514', constraint = 't0_d4_lista_no_firmada',
      message = format('la lista aprobada del lote no es la firmada: %s renglones (acta %s), sha256 %s (acta %s)%s',
                       v_n, p_lista ->> 'lista_aprobada', left(v_sha, 12), left(p_lista ->> 'sha256', 12),
                       coalesce('; aprobado_por fuera de los aprobadores del acta: ' || v_mal, ''));
  end if;
  v_max := coalesce((select max(w.leido_at) from almacen.t0_woo_foto w where w.lote = p_lote), 'infinity'::timestamptz);
  if exists (select 1 from almacen.t0_partidas p
              where p.lote = p_lote and p.tipo in ('conteo', 'por_verificar')
                and (exists (select 1 from channel.orders o where o.external_order_id = p.referencia or o.wc_order_id::text = p.referencia)
                     or exists (select 1 from ventas.ov_ordenes v where v.folio = p.referencia))) then
    raise exception using errcode = '23514', constraint = 't0_partida_venta_mal_tipada',
      message = 'una venta no se explica como conteo ni por_verificar: va como venta_posterior con su OV espejo';
  end if;
  select string_agg(p.referencia, ', ') into v_txt
    from almacen.t0_partidas p
   where p.lote = p_lote and p.tipo = 'venta_posterior'
     and not exists (select 1 from ventas.ov_ordenes v where v.folio = p.referencia and v.clave like 't0:%'
                      and v.borrada_at is null and v.estado <> 'cancelada');
  if v_txt is not null then
    raise exception using errcode = '23514', constraint = 't0_partida_venta_sin_espejo',
      message = format('venta_posterior sin OV espejo: %s', left(v_txt, 800));
  end if;
  -- [R corr] Ninguna OV espejo de marketplace por una venta creada después de que Woo leyó ese SKU.
  select string_agg(distinct format('%s %s (venta %s, foto %s)', o.folio, l.sku, c.creado_at, coalesce(w.leido_at, v_max)), '; ') into v_txt
    from ventas.ov_ordenes o
    join ventas.ov_lineas l on l.orden_id = o.id
    join channel.orders c on c.canal = o.mp_canal and upper(btrim(c.cuenta)) = o.mp_cuenta and c.external_order_id = o.mp_orden
    left join almacen.t0_woo_foto w on w.lote = p_lote and upper(w.sku::text) = upper(l.sku::text)
   where o.clave like 't0:%' and o.mp_orden is not null and o.borrada_at is null and o.estado <> 'cancelada'
     and c.creado_at > coalesce(w.leido_at, v_max);
  if v_txt is not null then
    raise exception using errcode = '23514', constraint = 't0_espejo_posterior_a_la_foto',
      message = format('OV espejo de ventas posteriores a la foto de Woo (quedan pendientes, sin espejo en el acto): %s', left(v_txt, 800));
  end if;
  -- [R corr, D4] lo no clasificado en L4 va a POR VERIFICAR: cada migrada con clase sin_clasificar lleva su ajuste.
  select string_agg(coalesce(m.odoo_name, m.external_order_id), ', ') into v_txt
    from almacen.t0_migradas m
   where m.lote = p_lote and m.clase = 'sin_clasificar'
     and not exists (select 1 from almacen.saldo_inicial_ajustes j
                      where j.lote = p_lote and j.regla = 'reserva_sin_clasificar' and j.referencia in (m.odoo_name, m.external_order_id));
  if v_txt is not null then
    raise exception using errcode = '23514', constraint = 't0_sin_clasificar_sin_ajuste',
      message = format('ventas sin clasificar en L4 sin su ajuste reserva_sin_clasificar a POR VERIFICAR: %s', left(v_txt, 800));
  end if;
  -- Un solo recorrido: el número que publicaría stock_libro, la foto de Woo, las partidas y la decisión D4.
  with libro as (
         select upper(b.sku::text) as sku, sum(b.libre_vendible)::bigint as libre, coalesce(sum(b.fisico_vendible), 0)::bigint as fv
           from almacen.existencias_bodega_v b
           join almacen.almacenes a on a.codigo = b.almacen
          where a.fuente = 'kubera' and a.cuenta_para_woo and b.fuente = 'kubera'
          group by 1),
       woo  as (select upper(w.sku::text) as sku, w.stock_woo, w.gestiona, w.leido_at from almacen.t0_woo_foto w where w.lote = p_lote),
       pend as (select t.sku, sum(t.pzs)::bigint as pzs
                  from almacen.t0_pendientes(p_desde) t left join woo w on w.sku = t.sku
                 where t.creado_at <= coalesce(w.leido_at, v_max)
                 group by 1),
       part as (select upper(p.sku::text) as sku, sum(p.cantidad)::bigint as s from almacen.t0_partidas p where p.lote = p_lote group by 1),
       -- [D4 firmado] la lista (y lo decidido) vive en t0_skus_sin_libro, nunca en stock_libro
       dec  as (select upper(t.sku::text) as sku, bool_or(t.decision = 'lista_aprobada') as aprobado, min(t.grupo) as grupo_lista
                  from almacen.t0_skus_sin_libro t where t.lote = p_lote group by 1),
       aj   as (select upper(j.sku_clave) as sku, bool_or(j.referencia = 'woo:gestiona=false') as aj_ng,
                       bool_or(j.referencia = 'woo:no_esta') as aj_ne
                  from almacen.saldo_inicial_ajustes j where j.lote = p_lote and j.regla = 'fuera_de_woo_por_verificar' group by 1),
       -- lo que la copia de Odoo trajo con piezas a este acto (la foto del desprendimiento del lote)
       cop  as (select distinct upper(f.sku::text) as sku
                  from almacen.desprendimiento_foto f join almacen.desprendimientos d on d.id = f.desprendimiento_id
                 where d.lote = p_lote),
       u    as (select woo.sku from woo union select libro.sku from libro union select part.sku from part union select aj.sku from aj
                union select dec.sku from dec),
       x    as (select u.sku, w.gestiona, (w.sku is not null) as en_woo, (l.sku is not null) as en_libro, (c.sku is not null) as de_copia,
                       coalesce(w.stock_woo, 0)::bigint as woo,
                       greatest(0, coalesce(l.libre, 0) - coalesce(pe.pzs, 0)) as libro_vendible,
                       coalesce(l.fv, 0) as fisico_vendible,
                       coalesce(pt.s, 0) as partidas,
                       (d.sku is not null) as decidido, coalesce(d.aprobado, false) as aprobado, d.grupo_lista,
                       coalesce(a.aj_ng, false) as aj_ng, coalesce(a.aj_ne, false) as aj_ne
                  from u
                  left join woo w   on w.sku = u.sku
                  left join libro l on l.sku = u.sku
                  left join pend pe on pe.sku = u.sku
                  left join part pt on pt.sku = u.sku
                  left join dec d   on d.sku = u.sku
                  left join aj a    on a.sku = u.sku
                  left join cop c   on c.sku = u.sku),
       y    as (select x.*,
                       -- grupos disjuntos: en Woo sin libro / con libro y Woo no lo gestiona / con libro y fuera de Woo
                       case when x.en_woo and not x.en_libro then 'sin_libro'
                            when x.en_woo and x.gestiona is false then 'no_gestiona'
                            when not x.en_woo and x.en_libro then 'fuera_de_woo' end as grupo_d4,
                       -- [R corr] el grupo por el que se decide: sin_libro = Woo lo tiene y la copia no le dio piezas
                       -- (aunque después entre por el diario); no_gestiona = de la copia y Woo no lo gestiona;
                       -- fuera_de_woo = en el libro y no en Woo
                       case when x.en_woo and not x.de_copia then 'sin_libro'
                            when x.en_woo and x.gestiona is false then 'no_gestiona'
                            when not x.en_woo and x.en_libro then 'fuera_de_woo' end as grupo_origen,
                       -- contra Woo va lo que Woo gestiona, más lo que no está en Woo y no está aprobado
                       (x.gestiona is distinct from false and not (not x.en_woo and x.aprobado)) as comparado
                  from x)
  select jsonb_build_object(
           'skus', count(*) filter (where comparado),
           'iguales', count(*) filter (where comparado and woo - libro_vendible = 0),
           'explicados', count(*) filter (where comparado and woo - libro_vendible <> 0 and partidas = woo - libro_vendible),
           'excluidos_no_gestiona', count(*) filter (where gestiona is false),
           'excluidos_lista_aprobada', count(*) filter (where not en_woo and aprobado),
           'd4', jsonb_build_object(
                   'no_gestiona',  jsonb_build_object('skus', count(*) filter (where grupo_d4 = 'no_gestiona'),
                                                      'en_0', count(*) filter (where grupo_d4 = 'no_gestiona' and libro_vendible = 0),
                                                      'lista_aprobada', count(*) filter (where grupo_d4 = 'no_gestiona' and aprobado)),
                   'fuera_de_woo', jsonb_build_object('skus', count(*) filter (where grupo_d4 = 'fuera_de_woo'),
                                                      'en_0', count(*) filter (where grupo_d4 = 'fuera_de_woo' and libro_vendible = 0),
                                                      'lista_aprobada', count(*) filter (where grupo_d4 = 'fuera_de_woo' and aprobado)),
                   'sin_libro',    jsonb_build_object('skus', count(*) filter (where grupo_d4 = 'sin_libro'),
                                                      'decididos', count(*) filter (where grupo_d4 = 'sin_libro' and decidido),
                                                      'lista_aprobada', count(*) filter (where grupo_d4 = 'sin_libro' and aprobado),
                                                      'aprobados_con_numero', count(*) filter (where grupo_lista = 'sin_libro' and aprobado and fisico_vendible > 0)),
                   'lista', jsonb_build_object('lista_aprobada', v_n, 'sha256', v_sha)),
           'n_d4_fuera', count(*) filter (where grupo_d4 in ('no_gestiona', 'fuera_de_woo') and libro_vendible > 0 and not aprobado),
           'm_d4_fuera', (select string_agg(format('%s (%s, libro %s)', z.sku, z.grupo_d4, z.libro_vendible), '; ')
                            from (select * from y y2 where y2.grupo_d4 in ('no_gestiona', 'fuera_de_woo') and y2.libro_vendible > 0
                                     and not y2.aprobado order by y2.sku limit 20) z),
           'n_d4_sin_decision', count(*) filter (where grupo_d4 = 'sin_libro' and not decidido),
           'm_d4_sin_decision', (select string_agg(z.sku, ', ')
                                   from (select * from y y2 where y2.grupo_d4 = 'sin_libro' and not y2.decidido order by y2.sku limit 20) z),
           'n_d4_ajuste', count(*) filter (where (aj_ng and grupo_d4 is distinct from 'no_gestiona')
                                              or (aj_ne and en_woo) or (aprobado and (aj_ng or aj_ne))),
           'm_d4_ajuste', (select string_agg(format('%s (en Woo %s, gestiona %s, aprobado %s)', z.sku, z.en_woo,
                                                    coalesce(z.gestiona::text, '-'), z.aprobado), '; ')
                             from (select * from y y2 where (y2.aj_ng and y2.grupo_d4 is distinct from 'no_gestiona')
                                      or (y2.aj_ne and y2.en_woo) or (y2.aprobado and (y2.aj_ng or y2.aj_ne))
                                    order by y2.sku limit 20) z),
           'n_d4_grupo', count(*) filter (where aprobado and grupo_origen is distinct from grupo_lista),
           'm_d4_grupo', (select string_agg(format('%s (lista %s, real %s)', z.sku, z.grupo_lista, coalesce(z.grupo_origen, 'ninguno')), '; ')
                            from (select * from y y2 where y2.aprobado and y2.grupo_origen is distinct from y2.grupo_lista
                                   order by y2.sku limit 20) z),
           'n_d4_sin_numero', count(*) filter (where aprobado and grupo_lista = 'sin_libro' and fisico_vendible <= 0),
           'm_d4_sin_numero', (select string_agg(z.sku, ', ')
                                 from (select * from y y2 where y2.aprobado and y2.grupo_lista = 'sin_libro' and y2.fisico_vendible <= 0
                                        order by y2.sku limit 20) z),
           'sin_explicar', count(*) filter (where comparado and partidas <> woo - libro_vendible),
           'muestra', (select string_agg(format('%s (woo %s, libro %s, partidas %s)', z.sku, z.woo, z.libro_vendible, z.partidas), '; ')
                         from (select * from y y2 where y2.comparado and y2.partidas <> y2.woo - y2.libro_vendible
                                order by y2.sku limit 20) z))
    into v_res
    from y;
  -- [D4 firmado] primero lo decidido; después los números
  if (v_res ->> 'n_d4_ajuste')::bigint > 0 then
    raise exception using errcode = '23514', constraint = 't0_d4_ajuste_no_cuadra',
      message = format('%s SKUs con fuera_de_woo_por_verificar que no cuadran con la foto de Woo o que están en la lista aprobada: %s',
                       v_res ->> 'n_d4_ajuste', v_res ->> 'm_d4_ajuste');
  end if;
  if (v_res ->> 'n_d4_grupo')::bigint > 0 then
    raise exception using errcode = '23514', constraint = 't0_d4_grupo_distinto',
      message = format('%s SKUs de la lista aprobada con un grupo que no es el suyo (la aprobación no aplica): %s',
                       v_res ->> 'n_d4_grupo', v_res ->> 'm_d4_grupo');
  end if;
  if (v_res ->> 'n_d4_fuera')::bigint > 0 then
    raise exception using errcode = '23514', constraint = 't0_d4_fuera_de_woo',
      message = format('%s SKUs que Woo no gestiona o que no están en Woo saldrían con número sin estar en la lista aprobada (D4: van a POR VERIFICAR): %s',
                       v_res ->> 'n_d4_fuera', v_res ->> 'm_d4_fuera');
  end if;
  if (v_res ->> 'n_d4_sin_decision')::bigint > 0 then
    raise exception using errcode = '23514', constraint = 't0_d4_sin_decision',
      message = format('%s SKUs de Woo que el libro no conoce, sin decisión en t0_skus_sin_libro (D4: a_cero o lista_aprobada): %s',
                       v_res ->> 'n_d4_sin_decision', v_res ->> 'm_d4_sin_decision');
  end if;
  if (v_res ->> 'n_d4_sin_numero')::bigint > 0 then
    raise exception using errcode = '23514', constraint = 't0_d4_aprobado_sin_numero',
      message = format('%s SKUs sin libro de la lista aprobada no entraron al libro con piezas vendibles (conteo o recepción en el diario de arranque): %s',
                       v_res ->> 'n_d4_sin_numero', v_res ->> 'm_d4_sin_numero');
  end if;
  if (v_res ->> 'sin_explicar')::bigint > 0 then
    raise exception using errcode = '23514', constraint = 't0_cuadre_sin_explicar',
      message = format('%s SKUs suben o bajan contra Woo sin partida: %s', v_res ->> 'sin_explicar', v_res ->> 'muestra');
  end if;
  return (v_res - 'sin_explicar' - 'muestra' - 'n_d4_fuera' - 'm_d4_fuera' - 'n_d4_sin_decision' - 'm_d4_sin_decision'
                - 'n_d4_ajuste' - 'm_d4_ajuste' - 'n_d4_grupo' - 'm_d4_grupo' - 'n_d4_sin_numero' - 'm_d4_sin_numero')
         || jsonb_build_object('por_tipo', coalesce((select jsonb_object_agg(z.tipo, jsonb_build_object('partidas', z.n, 'cantidad', z.s))
                                                       from (select p.tipo, count(*) as n, sum(p.cantidad) as s
                                                               from almacen.t0_partidas p where p.lote = p_lote group by 1) z), '{}'::jsonb));
end $$;

-- ───────────────────────────────────────────────────────────────────────────
-- B5f. SKUs que el libro no conoce [T0 §4.13] [D4 FIRMADO 20-oct]: a 0 salvo lista aprobada.
--      Desde la firma la tabla es LA lista de D4 para los tres grupos (columna grupo):
--        sin_libro    → a_cero: t0_conocer_skus lo da de alta con 0; lista_aprobada: [R corr] TIENE que entrar
--                       al libro con número (conteo o recepción en el diario de arranque); si no, t0_cuadre
--                       falla con t0_d4_aprobado_sin_numero (solo piezas en el libro dan un número);
--        no_gestiona  → (gestiona=false en Woo) con piezas: a_cero = sus piezas a POR VERIFICAR por
--                       fuera_de_woo_por_verificar; lista_aprobada = conserva su vendible;
--        fuera_de_woo → (no está en Woo) igual que no_gestiona.
--      t0_cuadre falla cerrado si uno de los tres grupos sale con número sin lista_aprobada, si la lista no es la
--      firmada (conteo + sha256 + aprobadores, p_lista) o si el grupo de un aprobado no es el suyo (t0_d4_grupo_distinto).
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists almacen.t0_skus_sin_libro (
  lote         text        not null,
  sku          citext      not null,
  canales      jsonb       not null,               -- publicaciones DROP con stock (channel.listings)
  decision     text        not null,
  aprobado_por text,
  motivo       text        not null,
  quien        text        not null default nullif(current_setting('app.usuario', true), ''),
  creado_at    timestamptz not null default now(),
  grupo        text        not null default 'sin_libro',   -- [D4 firmado] sin_libro | no_gestiona | fuera_de_woo
  constraint t0_skus_sin_libro_pkey         primary key (lote, sku),
  constraint t0_skus_sin_libro_decision_chk check (decision in ('a_cero', 'lista_aprobada')),
  constraint t0_skus_sin_libro_aprobado_chk check ((decision = 'lista_aprobada') = (aprobado_por is not null)
                                                   and (aprobado_por is null or aprobado_por ~ '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')),
  constraint t0_skus_sin_libro_motivo_chk   check (length(btrim(motivo)) >= 10),          -- [R seg]
  constraint t0_skus_sin_libro_grupo_chk    check (grupo in ('sin_libro', 'no_gestiona', 'fuera_de_woo'))
);
create trigger t0_skus_sin_libro_solo_agregar before update or delete on almacen.t0_skus_sin_libro
  for each row execute function ops.tg_solo_agregar();
create trigger t0_skus_sin_libro_sin_truncate before truncate on almacen.t0_skus_sin_libro
  for each statement execute function ops.tg_sin_truncate();

create or replace function almacen.t0_conocer_skus(p_lote text)
returns jsonb language plpgsql volatile security invoker set search_path = pg_catalog, public as $$
declare v_quien text := nullif(current_setting('app.usuario', true), ''); v_bod text; v_n bigint;
begin
  if v_quien is null then
    raise exception using errcode = '23514', constraint = 't0_conocer_skus_quien', message = 't0_conocer_skus exige app.usuario';
  end if;
  select a.codigo into v_bod from almacen.almacenes a
   where a.fuente = 'kubera' and a.cuenta_para_woo and a.surte_ventas
   order by a.preferencia nulls last, a.codigo limit 1;
  if v_bod is null then
    raise exception using errcode = '23514', constraint = 't0_conocer_sin_bodega', message = 'no hay bodega kubera que cuente para la venta';
  end if;
  insert into almacen.stock_almacen (sku, almacen, fisico)
  select p.sku, v_bod, 0
    from almacen.t0_skus_sin_libro t join core.products p on p.sku = t.sku
   where t.lote = p_lote and t.decision = 'a_cero'
     and not exists (select 1 from almacen.stock_almacen sa join almacen.almacenes a on a.codigo = sa.almacen
                      where sa.sku = p.sku and a.fuente = 'kubera' and a.cuenta_para_woo)
   order by p.sku
  on conflict do nothing;
  get diagnostics v_n = row_count;
  return jsonb_build_object(
    'lote', p_lote, 'bodega', v_bod, 'a_cero_insertados', v_n,
    'a_cero_ya_conocidos', (select count(*) from almacen.t0_skus_sin_libro t
                             where t.lote = p_lote and t.decision = 'a_cero'
                               and exists (select 1 from core.products p where p.sku = t.sku)) - v_n,
    'lista_aprobada', (select count(*) from almacen.t0_skus_sin_libro t where t.lote = p_lote and t.decision = 'lista_aprobada'),
    'no_existen', coalesce((select jsonb_agg(t.sku order by t.sku) from almacen.t0_skus_sin_libro t
                             where t.lote = p_lote and not exists (select 1 from core.products p where p.sku = t.sku)), '[]'::jsonb));
end $$;

-- ───────────────────────────────────────────────────────────────────────────
-- B6. Auditoría de DDL [S9 — OBLIGATORIO ANTES DE PRODUCCIÓN]
--     Falla ABIERTA: nunca bloquea DDL ajeno. Ciega a session_replication_role (eso lo cubre S1).
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists almacen.ddl_auditoria (
  id          bigint      generated always as identity primary key,
  ocurrido_at timestamptz not null default now(),
  txid        bigint      not null default txid_current(),
  rol         text        not null default session_user,
  usuario     text        default nullif(current_setting('app.usuario', true), ''),
  app         text        default current_setting('application_name', true),
  comando     text        not null,
  objeto      text,
  tipo_objeto text
);
create trigger ddl_auditoria_solo_agregar before update or delete on almacen.ddl_auditoria
  for each row execute function ops.tg_solo_agregar();
create trigger ddl_auditoria_sin_truncate before truncate on almacen.ddl_auditoria
  for each statement execute function ops.tg_sin_truncate();
-- [R seg] Cubre también odoo_historia, las tablas de ops que toca T0 (odoo_sale_orders, automatizacion_flags),
-- políticas (su identidad es «<nombre> on <esquema>.<tabla>»), vistas, tablas y GRANT/REVOKE (sin identidad de
-- objeto: se reconocen por el texto de la sentencia). ALTER EVENT TRIGGER no dispara event triggers (límite de
-- PostgreSQL): apagar la auditoría lo ve la vigía (auditoria_ddl_apagada) y el reporte diario (@@R8).
create or replace function almacen.tg_ddl_auditoria() returns event_trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare r record;
begin
  for r in select command_tag, object_identity, object_type from pg_event_trigger_ddl_commands()
            where schema_name in ('almacen', 'ventas', 'odoo_historia')
               or object_identity ~ '(^|\s)(almacen|ventas|odoo_historia)\.'
               or object_identity ~ '^(almacen|ventas|ops)\.tg_'
               or object_identity ~ '(^|\s)ops\.(odoo_sale_orders|automatizacion_flags)$'
               or (command_tag in ('GRANT', 'REVOKE', 'ALTER DEFAULT PRIVILEGES')
                   and current_query() ~* '(almacen|ventas|odoo_historia)|ops\.(odoo_sale_orders|automatizacion_flags)') loop
    insert into almacen.ddl_auditoria (comando, objeto, tipo_objeto)
    values (r.command_tag, coalesce(r.object_identity, left(regexp_replace(current_query(), '\s+', ' ', 'g'), 300)), r.object_type);
  end loop;
exception when others then null;     -- auditoría, no candado
end $$;
create or replace function almacen.tg_ddl_auditoria_drop() returns event_trigger
language plpgsql security invoker set search_path = pg_catalog, public as $$
declare r record;
begin
  for r in select object_identity, object_type, schema_name from pg_event_trigger_dropped_objects()
            where schema_name in ('almacen', 'ventas', 'odoo_historia')
               or object_identity ~ '(^|\s)(almacen|ventas|odoo_historia)\.'
               or object_identity ~ '^(almacen|ventas|ops)\.tg_'
               or object_identity ~ '(^|\s)ops\.(odoo_sale_orders|automatizacion_flags)$' loop
    insert into almacen.ddl_auditoria (comando, objeto, tipo_objeto) values (tg_tag, r.object_identity, r.object_type);
  end loop;
exception when others then null;
end $$;
create event trigger almacen_ddl_auditoria on ddl_command_end
  when tag in ('ALTER TABLE', 'CREATE FUNCTION', 'ALTER FUNCTION', 'CREATE TRIGGER', 'ALTER TRIGGER',
               'CREATE TABLE', 'CREATE VIEW', 'ALTER VIEW', 'CREATE POLICY', 'ALTER POLICY',
               'GRANT', 'REVOKE', 'ALTER DEFAULT PRIVILEGES')
  execute function almacen.tg_ddl_auditoria();
create event trigger almacen_ddl_auditoria_drop on sql_drop
  execute function almacen.tg_ddl_auditoria_drop();
alter event trigger almacen_ddl_auditoria enable always;
alter event trigger almacen_ddl_auditoria_drop enable always;

-- ───────────────────────────────────────────────────────────────────────────
-- B7. La vigía [S10]. [T0 §4.1] Sin odoo_sin_vaciar (Odoo ya no se vacía: la copia es final);
--     con no_vendible_descuadre, por_verificar_con_piezas (informativa), carga_odoo_abierta y
--     t0_espejo_sin_entregar. C′ la amplía con los traslados (fuente_unica_vigia_v se recrea allá).
-- ───────────────────────────────────────────────────────────────────────────
create or replace view almacen.vigia_fuente_v with (security_invoker = on) as
select 'copia_vieja'::text as problema, c.almacen as donde, format('foto_at %s', coalesce(c.foto_at::text, 'NULL')) as detalle
  from almacen.copias_odoo c where c.foto_at is null or c.foto_at < now() - interval '90 minutes'
union all
select 'copia_sin_fila', a.codigo, 'bodega de Odoo sin fila en copias_odoo'
  from almacen.almacenes a left join almacen.copias_odoo c on c.almacen = a.codigo
 where a.fuente = 'odoo' and c.almacen is null
union all
select 'guarda_no_always', c.relnamespace::regnamespace::text || '.' || c.relname::text, t.tgname || ' = ' || t.tgenabled::text
  from pg_trigger t join pg_class c on c.oid = t.tgrelid
 where not t.tgisinternal and t.tgenabled <> 'A'
   and (c.relnamespace in ('almacen'::regnamespace, 'odoo_historia'::regnamespace)                       -- [R seg]
        or t.tgname in ('odoo_sale_orders_una_fuente', 'ov_ordenes_una_fuente'))                         -- los de T0 en ops y ventas (C′)
   and t.tgname <> all (array['locations_tocar_trg', 'historial_movimientos_tocar_trg', 'ubicaciones_tocar'])
union all
select 'desprendimiento_abierto', d.almacen, format('#%s congelada desde %s', d.id, d.congelado_at)
  from almacen.desprendimientos d where d.estado = 'congelada' and d.creado_at < now() - interval '1 hour'
union all
select 'desprendimiento_sin_carga', d.almacen, format('#%s hecha sin carga, lote o totales', d.id)          -- [R seg]
  from almacen.desprendimientos d
 where d.estado = 'hecha' and (d.carga_id is null or d.lote is null or d.fisico is null or d.renglones is null)
union all
select 'ubicacion_reclasificada_con_piezas', h.almacen,                                                       -- [R seg]
       format('ubicación %s: %s → %s con %s piezas (%s, %s)', h.ubicacion_id, h.antes ->> 'tipo', h.despues ->> 'tipo', h.piezas, h.quien, h.cambiado_at)
  from almacen.ubicaciones_hist h
  join almacen.ubicacion_tipos ta on ta.tipo = h.antes ->> 'tipo'
  join almacen.ubicacion_tipos td on td.tipo = h.despues ->> 'tipo'
 where h.operacion = 'UPDATE' and h.piezas > 0 and ta.tipo <> td.tipo
   and (ta.cuenta_para_venta <> td.cuenta_para_venta or 'por_verificar' in (ta.tipo, td.tipo))
union all
select 'ubicacion_sin_movimiento', coalesce(l.cedis, m.almacen),                                              -- [R seg]
       format('%s en %s: locations %s, Σ stock_mov %s', coalesce(l.sku, m.sku), coalesce(l.ubicacion_id, m.ubicacion_id),
              coalesce(l.piezas, 0), coalesce(m.s, 0))
  from (select y.cedis, y.sku, y.ubicacion_id, y.piezas from almacen.locations y where y.origen = 'kubera') l
  full join (select x.almacen, x.sku, x.ubicacion_id, sum(x.delta) as s from almacen.stock_mov x group by 1, 2, 3) m
    on m.almacen = l.cedis and m.sku = l.sku and m.ubicacion_id = l.ubicacion_id
 where coalesce(l.piezas, 0) <> coalesce(m.s, 0)
   and exists (select 1 from almacen.desprendimientos d where d.almacen = coalesce(l.cedis, m.almacen) and d.estado = 'hecha')
union all
select 'desprendimiento_sin_foto', d.almacen, format('#%s: foto %s ≠ %s', d.id, coalesce(f.s, 0), d.fisico)
  from almacen.desprendimientos d
  left join (select desprendimiento_id, sum(fisico) as s from almacen.desprendimiento_foto
              where sentido = 'saldo_inicial' group by 1) f on f.desprendimiento_id = d.id
 where d.estado in ('hecha', 'revertida') and d.revierte_a is null and coalesce(f.s, 0) <> coalesce(d.fisico, 0)
union all
select 'negativo_kubera', l.cedis, format('%s renglones', count(*))
  from almacen.locations l where l.origen = 'kubera' and l.piezas < 0 group by l.cedis
union all
select 'libro_vs_ubicaciones', sa.almacen, format('%s: libro %s, ubicaciones %s', sa.sku, sa.fisico, coalesce(u.s, 0))
  from almacen.stock_almacen sa
  left join (select cedis, sku, sum(piezas) as s from almacen.locations where origen = 'kubera' group by 1, 2) u
    on u.cedis = sa.almacen and u.sku = sa.sku
 where sa.fisico <> coalesce(u.s, 0)
union all
select 'no_vendible_descuadre', sa.almacen,
       format('%s: fisico_no_vendible %s, ubicaciones no vendibles %s, libre %s', sa.sku, sa.fisico_no_vendible, coalesce(n.s, 0), sa.libre)
  from almacen.stock_almacen sa
  left join (select l.cedis, l.sku, sum(l.piezas) as s
               from almacen.locations l
               join almacen.ubicaciones u     on u.id = l.ubicacion_id
               join almacen.ubicacion_tipos t on t.tipo = u.tipo
              where l.origen = 'kubera' and not t.cuenta_para_venta group by 1, 2) n
    on n.cedis = sa.almacen and n.sku = sa.sku
 where sa.fisico_no_vendible <> coalesce(n.s, 0) or sa.fisico_no_vendible < 0 or (sa.fisico_no_vendible <> 0 and sa.libre < 0)
union all
select 'por_verificar_con_piezas', l.cedis, format('%s SKUs, %s piezas (informativa: salen por conteo)', count(distinct l.sku), sum(l.piezas))
  from almacen.locations l join almacen.ubicaciones u on u.id = l.ubicacion_id
 where u.tipo = 'por_verificar' and l.origen = 'kubera' and l.piezas <> 0
 group by l.cedis
union all
select 'carga_odoo_abierta', 'odoo_historia', format('carga %s abierta desde %s', c.id, c.abierta_at)
  from odoo_historia.cargas c where c.estado = 'abierta' and c.abierta_at < now() - interval '2 hours'
union all
select 't0_espejo_sin_entregar', o.folio, format('%s confirmada desde %s', o.clave, o.confirmada_at)
  from ventas.ov_ordenes o
 where o.clave like 't0:%' and o.estado = 'confirmada' and o.borrada_at is null and o.creado_at < now() - interval '7 days'
union all
select 'renglon_sin_catalogo', l.cedis, format('%s renglones', count(*))
  from almacen.locations l where l.ubicacion_id is null group by l.cedis
union all
select 'ddl_reciente', coalesce(split_part(x.objeto, '.', 1), '?'), format('%s %s (%s) %s', x.comando, x.objeto, x.rol, x.ocurrido_at)
  from almacen.ddl_auditoria x where x.ocurrido_at > now() - interval '24 hours'
union all
select 'auditoria_ddl_apagada', 'almacen', 'falta un event trigger de auditoría o no está en ALWAYS'
 where (select count(*) from pg_event_trigger
         where evtname in ('almacen_ddl_auditoria', 'almacen_ddl_auditoria_drop') and evtenabled = 'A') < 2;

create or replace view almacen.fuente_unica_vigia_v with (security_invoker = on) as
select * from almacen.vigia_fuente_v;

-- ───────────────────────────────────────────────────────────────────────────
-- B8. RLS y grants [S2, T4], funciones [S3], ALWAYS [S1-a]
-- ───────────────────────────────────────────────────────────────────────────
-- [28-oct, ventana de prod] Las comprobaciones diferidas de la semilla de ENSAYO (locations_kubera_cuadra y
-- locations_ubicacion_fk) corren AQUÍ y no al COMMIT: con renglones sembrados quedaban eventos pendientes en
-- almacen.locations y el ALTER TABLE de abajo fallaba (55006, «pending trigger events»). Son las mismas
-- comprobaciones, antes; después de este punto el archivo no escribe en tablas de almacen.
set constraints all immediate;

alter table almacen.desprendimientos      enable row level security;
alter table almacen.desprendimiento_foto  enable row level security;
alter table almacen.ddl_auditoria         enable row level security;
alter table almacen.saldo_inicial_ajustes enable row level security;
alter table almacen.t0_migradas           enable row level security;
alter table almacen.t0_woo_foto           enable row level security;
alter table almacen.t0_partidas           enable row level security;
alter table almacen.t0_skus_sin_libro     enable row level security;
alter table almacen.ubicaciones_hist      enable row level security;
-- service_role TAMBIÉN: el ACL por omisión de `almacen` le da arwdDxtm a toda tabla y vista nueva. [S2]
revoke all on almacen.desprendimientos, almacen.desprendimiento_foto, almacen.ddl_auditoria, almacen.saldo_inicial_ajustes,
              almacen.t0_migradas, almacen.t0_woo_foto, almacen.t0_partidas, almacen.t0_skus_sin_libro,
              almacen.ubicaciones_hist,
              almacen.vigia_fuente_v, almacen.fuente_unica_vigia_v, almacen.existencias_bodega_v, almacen.existencias_sku_v
  from public, anon, authenticated, service_role;
grant select on almacen.desprendimientos, almacen.desprendimiento_foto, almacen.ddl_auditoria, almacen.saldo_inicial_ajustes,
               almacen.t0_migradas, almacen.t0_woo_foto, almacen.t0_partidas, almacen.t0_skus_sin_libro,
               almacen.ubicaciones_hist,
               almacen.vigia_fuente_v, almacen.fuente_unica_vigia_v, almacen.existencias_bodega_v, almacen.existencias_sku_v
  to service_role;
-- [R seg 21-oct] service_role SOLO LEE lo que decide el número (herencia de 0064–0071): ningún escritor medido entra
-- como service_role (el backend entra como postgres; la API REST con la llave service_role solo lee tablas de public).
-- Si algún día aparece uno, se le da el permiso exacto en su propia migración.
revoke insert, update, delete, truncate, references, trigger, maintain
    on almacen.almacenes, almacen.locations, almacen.stock_almacen, almacen.stock_mov from service_role;
revoke all on function almacen.tg_desprendimientos_guarda(), almacen.tg_desprendimiento_foto_guarda(),
                       almacen.tg_desprendimientos_hecho_cuadra(), almacen.tg_ubicaciones_reclasificar(),
                       almacen.tg_ubicaciones_hist(), almacen.reclasificar_ubicacion(bigint, text, text),
                       almacen.tg_escritor_por_fuente(), ops.tg_almacenes_guarda(), ops.tg_stock_almacen_guarda(),
                       almacen.tg_stock_mov_ubicacion(), almacen.tg_ubicaciones_cuadran(), almacen.tg_locations_kubera_cuadra(),
                       almacen.tg_reubicacion_pares(), almacen.tg_no_vendible_cuadra(),
                       almacen.tg_ubicacion_tipos_recalcula(), almacen.tg_ubicaciones_recalcula(),
                       almacen.congelar_bodega(text, timestamptz, text),
                       almacen.posicion_odoo(text), almacen.clasificar_ubicacion_odoo(text, boolean),
                       almacen.desprender_desde_copia(bigint[], bigint, text, jsonb, jsonb, jsonb),
                       almacen.t0_migrar_odoo_sale_orders(text, jsonb, jsonb),
                       almacen.ov_espejo(jsonb, jsonb), almacen.ov_entregar(bigint, jsonb),
                       almacen.t0_pendientes(timestamptz), almacen.t0_ov_espejos(text, timestamptz, jsonb, jsonb, timestamptz),
                       almacen.t0_cuadre(text, timestamptz, jsonb), almacen.t0_conocer_skus(text),
                       almacen.tg_ddl_auditoria(), almacen.tg_ddl_auditoria_drop()
  from public, anon, authenticated, service_role;
alter table almacen.desprendimientos      enable always trigger desprendimientos_guarda,
                                          enable always trigger desprendimientos_hecho_cuadra,
                                          enable always trigger desprendimientos_sin_truncate;
alter table almacen.desprendimiento_foto  enable always trigger desprendimiento_foto_guarda,
                                          enable always trigger desprendimiento_foto_solo_agregar,
                                          enable always trigger desprendimiento_foto_sin_truncate;
alter table almacen.locations             enable always trigger locations_escritor,
                                          enable always trigger locations_kubera_cuadra;
alter table almacen.ubicaciones           enable always trigger ubicaciones_escritor,
                                          enable always trigger ubicaciones_recalcula,
                                          enable always trigger ubicaciones_reclasificar,
                                          enable always trigger ubicaciones_hist_alta,
                                          enable always trigger ubicaciones_hist_cambio;
alter table almacen.ubicaciones_hist      enable always trigger ubicaciones_hist_solo_agregar,
                                          enable always trigger ubicaciones_hist_sin_truncate;
alter table almacen.ubicacion_tipos       enable always trigger ubicacion_tipos_recalcula;
alter table almacen.copias_odoo           enable always trigger copias_odoo_escritor;
alter table almacen.historial_movimientos enable always trigger historial_movimientos_escritor;
alter table almacen.stock_mov             enable always trigger stock_mov_solo_agregar,           -- sin excepción (R1 retirada)
                                          enable always trigger stock_mov_ubicacion_antes,
                                          enable always trigger stock_mov_ubicacion_despues,
                                          enable always trigger stock_mov_ubicaciones_cuadran,
                                          enable always trigger stock_mov_reubicacion_pares;
alter table almacen.stock_almacen         enable always trigger stock_almacen_no_vendible_cuadra;
alter table almacen.ddl_auditoria         enable always trigger ddl_auditoria_solo_agregar,
                                          enable always trigger ddl_auditoria_sin_truncate;
alter table almacen.saldo_inicial_ajustes enable always trigger saldo_inicial_ajustes_solo_agregar,
                                          enable always trigger saldo_inicial_ajustes_sin_truncate;
alter table almacen.t0_migradas           enable always trigger t0_migradas_solo_agregar,
                                          enable always trigger t0_migradas_sin_truncate;
alter table almacen.t0_woo_foto           enable always trigger t0_woo_foto_solo_agregar,
                                          enable always trigger t0_woo_foto_sin_truncate;
alter table almacen.t0_partidas           enable always trigger t0_partidas_solo_agregar,
                                          enable always trigger t0_partidas_sin_truncate;
alter table almacen.t0_skus_sin_libro     enable always trigger t0_skus_sin_libro_solo_agregar,
                                          enable always trigger t0_skus_sin_libro_sin_truncate;
comment on column almacen.copias_odoo.historial_hasta is
  'Instante de la última LECTURA del historial en Odoo (no la marca de agua). En la pasada final se lee DESPUÉS de la foto (S14).';
comment on table almacen.saldo_inicial_ajustes is
  'T0: cada transformación de la copia cruda de Odoo, con su regla (SPEC_T0 §4.4). Σ crudos + Σ ajustes = sembrado. Solo agregar.';
comment on function almacen.desprender_desde_copia(bigint[], bigint, text, jsonb, jsonb, jsonb) is
  'T0: TEXCO, TEX2 y DROP pasan al libro en un acto desde odoo_historia (carga cerrada) ⊕ saldo_inicial_ajustes. Solo postgres, en el acta.';

-- B9. Registro (T5) y paso final que falla cerrado (S8)
insert into ops.migraciones (migracion, detalle)
select '0076_almacen_fuente_unica_copia',                          -- [R corr] = nombre del archivo (aplicar_migraciones.py)
       jsonb_build_object('tablas', 7, 'vistas', 4, 'funciones', 11, 'd16', true,
                          'retiradas', jsonb_build_array('almacen.desprendimiento_libre_odoo', 'almacen.desprender_bodega(bigint)',
                                                         'almacen.revertir_desprendimiento(bigint, text)', 'stock_mov R1'),
                          'd4_pendiente_de_firma', true,
                          'd4_skus_a_cero_firmado', '20-oct: gestiona=false, fuera de Woo y sin libro salen en 0 salvo lista aprobada',
                          'revision_21oct', jsonb_build_array('puerta del desprendimiento', 'saldo_inicial en el acto', 'reclasificar_ubicacion',
                                                              'locations no vendible', 'D16 mermas', 'lista D4 firmada', 'foto de Woo topa ventas',
                                                              'L4 sin tipo por omisión', 'auditoría DDL ampliada', 'service_role solo lee'))
 where to_regclass('ops.migraciones') is not null
   and not exists (select 1 from ops.migraciones where migracion = '0076_almacen_fuente_unica_copia');

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
                                         'almacen.ddl_auditoria','almacen.saldo_inicial_ajustes','almacen.t0_migradas',
                                         'almacen.t0_woo_foto','almacen.t0_partidas','almacen.t0_skus_sin_libro',
                                         'almacen.ubicaciones_hist']) t
              where has_table_privilege('service_role', t, 'UPDATE,DELETE,TRUNCATE,TRIGGER'))
  -- [R seg 21-oct] service_role solo lee almacenes, locations, stock_almacen y stock_mov
  or exists (select 1 from unnest(array['almacen.almacenes','almacen.locations','almacen.stock_almacen','almacen.stock_mov']) t
              where has_table_privilege('service_role', t, 'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER'))
  or exists (select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
              where c.relnamespace = 'almacen'::regnamespace and not t.tgisinternal and t.tgenabled <> 'A'
                and t.tgname <> all (array['locations_tocar_trg','historial_movimientos_tocar_trg','ubicaciones_tocar']))
  or (select count(*) from pg_event_trigger where evtname in ('almacen_ddl_auditoria','almacen_ddl_auditoria_drop') and evtenabled = 'A') < 2
  -- [T0 §4.14] lo retirado no existe y libre ya es D16
  or to_regclass('almacen.desprendimiento_libre_odoo') is not null
  or to_regprocedure('almacen.desprender_bodega(bigint)') is not null
  or to_regprocedure('almacen.revertir_desprendimiento(bigint, text)') is not null
  or to_regprocedure('almacen.tg_stock_mov_solo_agregar()') is not null
  or (select regexp_replace(pg_get_expr(d.adbin, d.adrelid), '[\s()]', '', 'g')
        from pg_attrdef d join pg_attribute a on a.attrelid = d.adrelid and a.attnum = d.adnum
       where d.adrelid = 'almacen.stock_almacen'::regclass and a.attname = 'libre') is distinct from 'fisico-fisico_no_vendible-apartado'
  or not exists (select 1 from pg_trigger t where t.tgrelid = 'almacen.stock_mov'::regclass and t.tgname = 'stock_mov_solo_agregar'
                  and t.tgfoid = 'ops.tg_solo_agregar()'::regprocedure and t.tgenabled = 'A')
  or not exists (select 1 from almacen.ubicacion_tipos where tipo = 'por_verificar' and not cuenta_para_venta)
  -- [D4 firmado] la regla nueva y la columna grupo de la lista
  or not exists (select 1 from pg_constraint c where c.conrelid = 'almacen.saldo_inicial_ajustes'::regclass
                  and c.conname = 'saldo_inicial_ajustes_regla_chk' and pg_get_constraintdef(c.oid) like '%fuera_de_woo_por_verificar%')
  or not exists (select 1 from pg_attribute a where a.attrelid = 'almacen.t0_skus_sin_libro'::regclass and a.attname = 'grupo' and not a.attisdropped)
  -- [R 21-oct] las puertas nuevas existen y están en ALWAYS
  or exists (select 1 from unnest(array['desprendimientos_hecho_cuadra', 'ubicaciones_reclasificar', 'ubicaciones_hist_alta',
                                         'ubicaciones_hist_cambio', 'ubicaciones_hist_solo_agregar']) n(tg)
              where not exists (select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
                                 where c.relnamespace = 'almacen'::regnamespace and t.tgname = n.tg and t.tgenabled = 'A'))
  or to_regprocedure('almacen.t0_cuadre(text, timestamptz, jsonb)') is null
  or to_regprocedure('almacen.t0_cuadre(text, timestamptz)') is not null
  or not exists (select 1 from pg_constraint c where c.conrelid = 'almacen.t0_skus_sin_libro'::regclass and c.conname = 't0_skus_sin_libro_motivo_chk')
  then
    raise exception using errcode = 'KB000',
      message = 'B′: candado incompleto (RLS / invoker / grants / funciones / ALWAYS / auditoría / retiradas / D16 / D4); revisar S1–S10 y SPEC_T0 §4.14';
  end if;
end $$;
commit;
