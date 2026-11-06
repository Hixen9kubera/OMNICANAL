-- ═══════════════════════════════════════════════════════════════════════════
-- 0083 — Llegadas a cuarentena ya revisadas (vigilante de cuarentena de la
--        pestaña Inventario). 6-nov-2026, pedido de Brandon.
--
-- POR QUÉ
-- Cuando bodega mueve un producto a la cuarentena de Odoo
-- (TEXCO/FERRAFORME/CUARENTENA, ubicación 470719), la pestaña Inventario lo
-- avisa con un globo rojo y una franja. La llegada sale del historial que ya se
-- copia de Odoo (almacen.historial_movimientos, 0070): un renglón cuyo destino
-- es la cuarentena y cuyo origen no lo es. Lo que faltaba es dónde apuntar que
-- alguien YA la vio: la primera persona que abre el popup la deja revisada para
-- todos, con su nombre y la hora (decisión de Brandon).
--
-- QUÉ CREA
--   · almacen.cuarentena_revisadas: una fila por llegada vista
--     (odoo_move_line_id = la línea de Odoo que llevó el producto a cuarentena),
--     cuándo y quién. PENDIENTE = llegada del historial sin fila aquí.
--   · Solo agregar: no se edita, no se borra, no se vacía (los triggers de la
--     0064). Una revisión no se «des-revisa».
--
-- QUIÉN ESCRIBE: el backend (POST /api/inventario/cuarentena/revisar), con el
-- usuario de la sesión. RLS sin políticas y service_role SOLO LEE, como el resto
-- del esquema almacen (0075).
--
-- QUIÉN LEE: GET /api/inventario/cuarentena/pendientes y /cuarentena
-- (services/cuarentena.py). No cambia ninguna otra tabla, ni Odoo, ni el stock.
--
-- Estado: aplicar PRIMERO en el sandbox. Producción con su acta.
--
-- REVERSA (solo la lee el vigilante; sin ella las llegadas vuelven a avisar
-- todas como pendientes):
--   drop table almacen.cuarentena_revisadas;
-- ═══════════════════════════════════════════════════════════════════════════

begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';

do $$
begin
  if to_regclass('almacen.historial_movimientos') is null then
    raise exception using errcode = 'KB000', message = '0083: falta almacen.historial_movimientos (0070)';
  end if;
  if to_regprocedure('ops.tg_solo_agregar()') is null or to_regprocedure('ops.tg_sin_truncate()') is null then
    raise exception using errcode = 'KB000', message = '0083: faltan ops.tg_solo_agregar / ops.tg_sin_truncate (0064)';
  end if;
end $$;

create table if not exists almacen.cuarentena_revisadas (
  odoo_move_line_id bigint      not null,
  revisado_at       timestamptz not null default now(),
  revisado_por      text        not null default nullif(current_setting('app.usuario', true), ''),
  constraint cuarentena_revisadas_pkey      primary key (odoo_move_line_id),
  constraint cuarentena_revisadas_quien_chk check (length(btrim(revisado_por)) > 0)
);

drop trigger if exists cuarentena_revisadas_solo_agregar on almacen.cuarentena_revisadas;
create trigger cuarentena_revisadas_solo_agregar before update or delete on almacen.cuarentena_revisadas
  for each row execute function ops.tg_solo_agregar();
drop trigger if exists cuarentena_revisadas_sin_truncate on almacen.cuarentena_revisadas;
create trigger cuarentena_revisadas_sin_truncate before truncate on almacen.cuarentena_revisadas
  for each statement execute function ops.tg_sin_truncate();
alter table almacen.cuarentena_revisadas enable always trigger cuarentena_revisadas_solo_agregar,
                                        enable always trigger cuarentena_revisadas_sin_truncate;

alter table almacen.cuarentena_revisadas enable row level security;
revoke all on almacen.cuarentena_revisadas from public, anon, authenticated, service_role;
grant select on almacen.cuarentena_revisadas to service_role;

comment on table almacen.cuarentena_revisadas is
  'Llegadas a la cuarentena de Odoo (TEXCO/FERRAFORME/CUARENTENA) que alguien ya vio al abrir el popup de la pestaña Inventario. Pendiente = llegada de almacen.historial_movimientos sin fila aquí. Solo agregar.';
comment on column almacen.cuarentena_revisadas.odoo_move_line_id is
  'stock.move.line de Odoo que llevó el producto a cuarentena (= almacen.historial_movimientos.odoo_move_line_id).';
comment on column almacen.cuarentena_revisadas.revisado_por is
  'Usuario de la sesión del panel que abrió el popup (app.usuario).';

insert into ops.migraciones (migracion, detalle)
select '0083_almacen_cuarentena_revisadas',
       jsonb_build_object('tablas', jsonb_build_array('almacen.cuarentena_revisadas'))
 where to_regclass('ops.migraciones') is not null
   and not exists (select 1 from ops.migraciones where migracion = '0083_almacen_cuarentena_revisadas');

commit;
