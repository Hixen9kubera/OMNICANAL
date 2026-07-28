-- ═══════════════════════════════════════════════════════════════════════════
-- 0004 — RETENCIÓN de ops.webhook_events: 3 días (Eduardo, 2026-07-28).
--
-- POR QUÉ: la tabla era el 79% de la BD kubera (154 MB de 194 MB) y crece
-- ~17,000 filas/día. Es staging idempotente de webhooks: su valor es
-- operativo (campana, reproceso, idempotencia de la ráfaga de ML), no
-- histórico — el hecho de negocio ya quedó en channel.orders / channel.listings.
-- Sin retención se repite el disco lleno (53100) que congeló a dailytrack.
--
-- Purga inicial one-shot: 129,675 filas (id <= 130513) respaldadas en
-- backups/ops_webhook_events_hasta_id_130513_2026-07-28.csv.gz + VACUUM FULL
-- (154 MB → 33 MB; base 194 MB → 74 MB).
--
-- Idempotente: re-aplicable sin efecto. En el sandbox, si pg_cron no está
-- disponible, la función se crea igual y solo se omite la programación.
-- ═══════════════════════════════════════════════════════════════════════════

do $$
begin
  create extension if not exists pg_cron;
exception when others then
  raise notice 'pg_cron no disponible en este proyecto: %', sqlerrm;
end $$;

-- Borra por LOTES (nunca un DELETE gigante: statement_timeout de Supabase) y
-- deja constancia en ops.process_log — la purga tiene que ser auditable.
create or replace function ops.purgar_webhook_events(
  dias int default 3,
  lote int default 20000
) returns bigint
language plpgsql
security definer
set search_path = ops, public, pg_catalog
as $$
declare
  corte    timestamptz := now() - make_interval(days => dias);
  n        bigint := 0;
  borradas bigint := 0;
  t0       timestamptz := clock_timestamp();
begin
  loop
    delete from ops.webhook_events
     where id in (select id from ops.webhook_events
                   where recibido_at < corte
                   order by id limit lote);
    get diagnostics n = row_count;
    borradas := borradas + n;
    exit when n = 0;
  end loop;

  insert into ops.process_log (proceso, origen, accion, estado, detalle, duracion_s)
  values ('retencion_webhooks', 'pg_cron', 'purga', 'ok',
          jsonb_build_object('dias', dias, 'borradas', borradas, 'corte', corte),
          extract(epoch from clock_timestamp() - t0));
  return borradas;
end $$;

comment on function ops.purgar_webhook_events(int, int) is
  'Retención de ops.webhook_events. Borra por lotes lo anterior a N días '
  '(default 3) y registra el resultado en ops.process_log. La programa pg_cron '
  'a diario; también se puede correr a mano: select ops.purgar_webhook_events(3);';

-- Diaria a las 08:20 UTC: FUERA de la ventana de los ETL/deltas de la
-- migración (06:15, 06:30, 06:45, 07:15) para no competir por recursos.
do $$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    perform cron.unschedule('retencion_webhook_events')
      where exists (select 1 from cron.job where jobname = 'retencion_webhook_events');
    perform cron.schedule('retencion_webhook_events', '20 8 * * *',
                          $cmd$select ops.purgar_webhook_events(3)$cmd$);
  else
    raise notice 'sin pg_cron: la función queda creada, prográmala aparte.';
  end if;
end $$;
