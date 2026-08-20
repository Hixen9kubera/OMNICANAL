-- ═══════════════════════════════════════════════════════════════════════════
-- 0025 — BLINDAJE: RLS en las tablas que quedaron sin ella, revocación a `anon`
--        en public.packing_*, y `security_invoker` en las vistas.
--
-- POR QUÉ
--   Auditoría del 2026-08-19 sobre la BD kubera de producción. El diseño
--   deny-by-default de 0001 se sostiene y está PROBADO EN VIVO: con la anon key
--   pública —la que va dentro del bundle del panel— los esquemas `core`,
--   `channel`, `costing`, `ops`, `enrich` y `analytics` responden PGRST106
--   ("Only the following schemas are exposed: public, graphql_public").
--
--   Pero desde la 0020 la disciplina de RLS se perdió. No es que las tablas se
--   hayan creado a mano fuera del flujo: **las creó el flujo, y el flujo dejó
--   de ponerles el candado**:
--
--     0020_enrich_margenes      → order_shipping_cost, listing_weight,
--                                 listing_visits          ...... sin RLS
--     0021_ops_stock_watch_photo→ stock_watch_photo       ...... sin RLS
--     0022_candados_fanout      → fulfillment_operations,
--                                 fba_watermark           ...... sin RLS
--     0023_ops_fba_snapshot     → fba_snapshot            ...... sin RLS
--     0024_ops_tiktok_tokens    → tiktok_tokens           ...... sin RLS
--
--   Ocho tablas en cinco migraciones. `ops.tiktok_tokens` guarda tokens de
--   OAuth de TikTok, así que ésa importa más que las demás.
--
--   Hoy nada de eso es alcanzable, y lo único que lo contiene es la lista de
--   esquemas expuestos: configuración que vive FUERA del esquema y se cambia
--   con un clic en el dashboard. Esta migración lo devuelve al esquema.
--
--   **Esto no cierra la causa.** Mientras las migraciones nuevas sigan creando
--   tablas sin `enable row level security`, el hueco se vuelve a abrir. El
--   barrido del bloque D existe justo por eso: para que re-correr este archivo
--   sirva de red. La solución de fondo es una prueba que falle en CI cuando
--   aparezca una tabla sin RLS en los esquemas de negocio.
--
-- QUÉ **NO** HACE
--   No crea ni borra tablas. No toca una sola fila de datos. No cambia ninguna
--   definición de vista. Y no agrega políticas: las tablas quedan con RLS activa
--   y CERO políticas, que es el estado de sus hermanas desde 0001 — "nadie lee,
--   salvo quien haga bypass".
--
-- POR QUÉ NO ROMPE NADA (verificado, no supuesto)
--   * `service_role` y `postgres` tienen rolbypassrls = true (pg_roles), así que
--     la RLS les es transparente. Por ahí entran el backend y los crons.
--   * Tienen grants completos en las tablas de negocio y SELECT en las retiradas
--     (information_schema.role_table_grants).
--   * `anon` y `authenticated` no tienen NINGÚN grant en `enrich`, `ops` ni
--     `propuestas_retirado`: no pierden nada que hoy tengan.
--   * Las vistas son normales (relkind='v'), no materializadas, así que
--     `security_invoker` aplica. Postgres 17.6 lo soporta desde la 15.
--
-- IDEMPOTENTE
--   Se puede volver a correr sin daño. Los bloques A y C son declarativos, el B
--   revoca lo que ya no está, y el D es un barrido con guarda.
--
-- REVERSA
--   Al pie del archivo, comentada, línea por línea.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- ───────────────────────────────────────────────────────────────────────────
-- A · RLS en las tablas que nacieron sin ella
--
-- Las 7 primeras vienen de las migraciones 0020→0023 (ver encabezado). Las 3 de
-- `propuestas_retirado` quedaron a medias en el retiro de 0014: 4 de las 7
-- tablas de ese esquema sí tienen RLS, estas 3 no.
--
-- Deliberadamente SIN políticas. Una tabla con RLS activa y 0 políticas no la
-- lee nadie que no haga bypass — que es justo lo que queremos.
-- ───────────────────────────────────────────────────────────────────────────
alter table enrich.listing_visits       enable row level security;
alter table enrich.listing_weight       enable row level security;
alter table enrich.order_shipping_cost  enable row level security;
alter table ops.fba_snapshot            enable row level security;
alter table ops.fba_watermark           enable row level security;
alter table ops.fulfillment_operations  enable row level security;
alter table ops.stock_watch_photo       enable row level security;
alter table propuestas_retirado.competencia_busquedas          enable row level security;
alter table propuestas_retirado.competencia_rankings_categoria enable row level security;
alter table propuestas_retirado.competencia_terminos_categoria enable row level security;

-- ───────────────────────────────────────────────────────────────────────────
-- B · Quitarle a `anon` y `authenticated` los permisos sobre public.packing_*
--
-- Estas dos tablas se crearon desde el editor del dashboard, que reparte
-- GRANT ALL a anon y authenticated por defecto. Son las ÚNICAS tablas de
-- negocio que viven en `public`, el único esquema expuesto por PostgREST.
--
-- Hoy están contenidas por su RLS (activa, 0 políticas) — verificado: anon
-- GET devuelve 200 con []. Pero entre esos permisos viene TRUNCATE, y TRUNCATE
-- es el único que la RLS **no** filtra: es un comando de tabla, no de fila.
-- Que hoy no sea alcanzable por REST es una propiedad de PostgREST, no una
-- garantía nuestra. Se quitan.
--
-- `service_role` y `postgres` conservan todo: no se tocan.
-- ───────────────────────────────────────────────────────────────────────────
revoke all on public.packing_lists      from anon, authenticated;
revoke all on public.packing_list_items from anon, authenticated;

-- ───────────────────────────────────────────────────────────────────────────
-- C · `security_invoker` en las vistas
--
-- Una vista, por omisión, se ejecuta con los permisos de su DUEÑO. Éstas son de
-- `postgres`, que tiene rolbypassrls — o sea que hoy atienden saltándose la RLS
-- de todo lo que hay debajo, sin importar quién pregunte.
--
-- Hoy no hay fuga porque solo `service_role` tiene grant sobre ellas, y ese ya
-- hace bypass por su cuenta. El problema es el futuro: `costos_finales_detalle`
-- y `restock_panel` son EXACTAMENTE las vistas que uno le da a un usuario de
-- solo-lectura para armar un tablero, y ese día la vista le entregaría todo lo
-- que hay detrás, ignorando la RLS de las tablas base.
--
-- Con `security_invoker = on` la vista se ejecuta con la identidad de quien
-- consulta. Es el requisito previo del rol de lectura que viene después.
-- ───────────────────────────────────────────────────────────────────────────
alter view analytics.stock_hist_dia        set (security_invoker = on);
alter view channel.restock_panel           set (security_invoker = on);
alter view channel.sales_daily             set (security_invoker = on);
alter view channel.sales_daily_completa    set (security_invoker = on);
alter view costing.costos_finales_detalle  set (security_invoker = on);
alter view costing.precios_desactualizados set (security_invoker = on);
alter view enrich.market_publicaciones_v   set (security_invoker = on);
alter view enrich.market_skus_v            set (security_invoker = on);
alter view propuestas_retirado.competencia_publicaciones_v set (security_invoker = on);
alter view propuestas_retirado.competencia_skus_v          set (security_invoker = on);

-- ───────────────────────────────────────────────────────────────────────────
-- D · Barrido — la red debajo de la lista explícita
--
-- La lista de arriba es la foto del 2026-08-19. Este bloque atrapa lo que haya
-- llegado después (por ejemplo `ops.tiktok_tokens` de la 0024, que al momento de
-- escribir esto todavía no estaba aplicada en producción) y lo que llegue
-- mañana si alguien vuelve a olvidar el candado.
--
-- Anuncia por NOTICE cada tabla que atrapa. Si anuncia algo, es señal de que
-- una migración nueva se saltó su RLS: vale la pena ir a arreglarla en origen,
-- no solo aquí.
--
-- Hace lo mismo con las vistas, por la misma razón.
-- ───────────────────────────────────────────────────────────────────────────
do $$
declare
  r        record;
  tablas   int := 0;
  vistas   int := 0;
  esquemas text[] := array['core','channel','costing','enrich','ops','analytics',
                           'migration','public','propuestas_retirado'];
begin
  for r in
    select n.nspname as sch, c.relname as obj
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and not c.relrowsecurity
      and n.nspname = any (esquemas)
    order by 1, 2
  loop
    execute format('alter table %I.%I enable row level security', r.sch, r.obj);
    raise notice 'BARRIDO · RLS activada en %.% — no estaba en la lista; revisa su migración de origen.',
                 r.sch, r.obj;
    tablas := tablas + 1;
  end loop;

  for r in
    select n.nspname as sch, c.relname as obj
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'v'
      and n.nspname = any (esquemas)
      and coalesce(array_to_string(c.reloptions, ','), '') not like '%security_invoker=on%'
    order by 1, 2
  loop
    execute format('alter view %I.%I set (security_invoker = on)', r.sch, r.obj);
    raise notice 'BARRIDO · security_invoker activado en %.% — no estaba en la lista.',
                 r.sch, r.obj;
    vistas := vistas + 1;
  end loop;

  if tablas = 0 and vistas = 0 then
    raise notice 'BARRIDO · nada que atrapar: la lista explícita cubrió todo.';
  end if;
end $$;

commit;

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN — correr después de aplicar. Las tres deben devolver 0 filas.
-- ═══════════════════════════════════════════════════════════════════════════
-- -- 1) Ninguna tabla de negocio sin RLS
-- select n.nspname || '.' || c.relname
-- from pg_class c join pg_namespace n on n.oid = c.relnamespace
-- where c.relkind = 'r' and not c.relrowsecurity
--   and n.nspname in ('core','channel','costing','enrich','ops','analytics',
--                     'migration','public','propuestas_retirado');
--
-- -- 2) Ningún grant a anon/authenticated fuera de storage y realtime
-- select table_schema || '.' || table_name, grantee
-- from information_schema.role_table_grants
-- where grantee in ('anon','authenticated')
--   and table_schema not in ('storage','realtime','extensions');
--
-- -- 3) Ninguna vista sin security_invoker
-- select n.nspname || '.' || c.relname
-- from pg_class c join pg_namespace n on n.oid = c.relnamespace
-- where c.relkind = 'v'
--   and n.nspname in ('core','channel','costing','enrich','ops','analytics',
--                     'migration','public','propuestas_retirado')
--   and coalesce(array_to_string(c.reloptions, ','), '')
--       not like '%security_invoker=on%';

-- ═══════════════════════════════════════════════════════════════════════════
-- REVERSA — simétrica y completa. Pegar y correr si algo se comporta raro.
-- ═══════════════════════════════════════════════════════════════════════════
-- begin;
-- alter table enrich.listing_visits      disable row level security;
-- alter table enrich.listing_weight      disable row level security;
-- alter table enrich.order_shipping_cost disable row level security;
-- alter table ops.fba_snapshot           disable row level security;
-- alter table ops.fba_watermark          disable row level security;
-- alter table ops.fulfillment_operations disable row level security;
-- alter table ops.stock_watch_photo      disable row level security;
-- alter table propuestas_retirado.competencia_busquedas          disable row level security;
-- alter table propuestas_retirado.competencia_rankings_categoria disable row level security;
-- alter table propuestas_retirado.competencia_terminos_categoria disable row level security;
-- grant all on public.packing_lists      to anon, authenticated;
-- grant all on public.packing_list_items to anon, authenticated;
-- alter view analytics.stock_hist_dia        set (security_invoker = off);
-- alter view channel.restock_panel           set (security_invoker = off);
-- alter view channel.sales_daily             set (security_invoker = off);
-- alter view channel.sales_daily_completa    set (security_invoker = off);
-- alter view costing.costos_finales_detalle  set (security_invoker = off);
-- alter view costing.precios_desactualizados set (security_invoker = off);
-- alter view enrich.market_publicaciones_v   set (security_invoker = off);
-- alter view enrich.market_skus_v            set (security_invoker = off);
-- alter view propuestas_retirado.competencia_publicaciones_v set (security_invoker = off);
-- alter view propuestas_retirado.competencia_skus_v          set (security_invoker = off);
-- -- OJO: la reversa NO deshace el barrido del bloque D (no sabe qué atrapó).
-- -- Si hace falta, léelo de los NOTICE de la corrida.
-- commit;
