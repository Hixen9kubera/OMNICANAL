-- ═══════════════════════════════════════════════════════════════════════════
-- 0050 — La retención de webhooks deja de ser una sola cifra para todos
-- ═══════════════════════════════════════════════════════════════════════════
--
-- La 0004 puso 3 días para TODO `ops.webhook_events`, y para Mercado Libre está
-- bien: manda ~19,000 avisos al día y guardarlos más tiempo sólo engorda la
-- tabla. Pero la misma regla aplicada a TikTok y Temu borra justo la evidencia
-- que se necesita, porque esos canales mandan unos pocos avisos al día — o
-- ninguno.
--
-- POR QUÉ AHORA. El 10-sep-2026 Brandon reportó ventas de Temu que no aparecían
-- en el panel. Contestar "¿Temu nos mandó algo?" fue imposible: los avisos de
-- TikTok y Temu no se persistían en ningún lado (vivían en un `deque` de 300 en
-- memoria que se vacía en cada despliegue). Eso se corrigió en el receptor; si
-- además se purgaran a los 3 días, la pregunta volvería a quedarse sin respuesta
-- en cuanto pasara una semana — que es exactamente el plazo en el que uno se da
-- cuenta de que un canal dejó de entregar.
--
-- LA REGLA: se conserva por canal.
--   mercado_libre .... 3 días   (volumen: ~19,000/día)
--   el resto ......... 90 días  (tiktok, temu, odoo, alertas: unidades al día)
--
-- 90 días de TikTok+Temu a su ritmo actual son unos pocos miles de renglones:
-- nada contra los ~57,000 que ML sostiene en 3 días.
--
-- COMPATIBLE HACIA ATRÁS: la firma `purgar_webhook_events(dias, lote)` se
-- conserva y `dias` sigue gobernando el canal de alto volumen, así que el
-- `pg_cron` que ya existe —`select ops.purgar_webhook_events(3)`— sigue siendo
-- correcto sin tocarlo. Lo que cambia es que ya no arrastra a los demás.

create or replace function ops.purgar_webhook_events(
  dias int default 3,
  lote int default 20000,
  dias_bajo_volumen int default 90
) returns bigint
language plpgsql
security definer
set search_path = ops, public, pg_catalog
as $$
declare
  corte      timestamptz := now() - make_interval(days => dias);
  corte_bajo timestamptz := now() - make_interval(days => dias_bajo_volumen);
  n          bigint := 0;
  borradas   bigint := 0;
  t0         timestamptz := clock_timestamp();
begin
  loop
    delete from ops.webhook_events
     where id in (
       select id from ops.webhook_events
        -- El canal decide su propio corte. `mercado_libre` es el único de alto
        -- volumen; si mañana otro lo fuera, se suma a esta lista y no hay que
        -- tocar nada más.
        where recibido_at < (case when canal = 'mercado_libre'
                                  then corte else corte_bajo end)
        order by id limit lote);
    get diagnostics n = row_count;
    borradas := borradas + n;
    exit when n = 0;
  end loop;

  insert into ops.process_log (proceso, origen, accion, estado, detalle, duracion_s)
  values ('retencion_webhooks', 'pg_cron', 'purga', 'ok',
          jsonb_build_object('dias', dias, 'dias_bajo_volumen', dias_bajo_volumen,
                             'borradas', borradas, 'corte', corte,
                             'corte_bajo', corte_bajo),
          extract(epoch from clock_timestamp() - t0));
  return borradas;
end $$;

comment on function ops.purgar_webhook_events(int, int, int) is
  'Retención de ops.webhook_events POR CANAL: mercado_libre a los `dias` '
  '(default 3, por volumen: ~19,000/día) y el resto a los `dias_bajo_volumen` '
  '(default 90). Borra por lotes y registra el resultado en ops.process_log. '
  'La programa pg_cron a diario; también se puede correr a mano: '
  'select ops.purgar_webhook_events(3);';
