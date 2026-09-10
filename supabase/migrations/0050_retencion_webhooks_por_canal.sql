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
-- LA REGLA: se conserva por canal, con LISTA DE PERMITIDOS.
--   tiktok, temu, odoo, alertas ... 90 días (`dias_bajo_volumen`)
--   todo lo demás ................. 3 días  (`dias`): mercado_libre (~19,000/día)
--                                   y CUALQUIER canal que no esté en la lista
--
-- POR QUÉ LISTA DE PERMITIDOS (revisión del 10-sep, Eduardo + consejo). La
-- primera versión guardaba 90 días todo lo que no se llamara exactamente
-- 'mercado_libre'. Eso falla hacia el CRECIMIENTO: un canal nuevo (el webhook de
-- Woo, si algún día se persiste aquí, dispara product.updated con cada cambio de
-- stock) o una etiqueta de ML mal escrita heredaría 90 días sin que nadie lo
-- decidiera, y la tabla volvería a crecer sin freno: justo lo que la 0004 existe
-- para evitar (disco lleno, 53100). Con la lista, el error posible es el barato
-- y visible: un canal nuevo de bajo volumen se purga a los 3 días hasta que
-- alguien lo agregue aquí a propósito. Con los canales que existen hoy el
-- resultado es idéntico al de la primera versión.
--
-- Volumen medido el 10-sep: mercado_libre 18,714 en 24 h; odoo ~34/día;
-- alertas ~2/día; tiktok ~10 (persiste desde ese día); temu 0. A 90 días, los
-- cuatro de la lista suman unas 3,500 filas contra las ~57,000 de ML en 3 días.
--
-- COMPATIBLE HACIA ATRÁS: la firma `purgar_webhook_events(dias, lote)` se
-- conserva y `dias` sigue gobernando todo lo que no está en la lista, así que el
-- `pg_cron` que ya existe (`select ops.purgar_webhook_events(3)`) sigue siendo
-- correcto sin tocarlo.
--
-- CÓMO APLICARLA: el archivo completo, en UNA corrida (trae su propio
-- begin/commit), fuera de 08:15-08:25 UTC (el cron corre a las 08:20) y con el
-- rol dueño de la función (`postgres`): el `drop` lo exige.
--
-- ⚠️ DESDE AQUÍ LA 0004 YA NO ES RE-APLICABLE SOLA: volvería a crear la firma de
-- dos argumentos junto a la de tres y reprogramaría la llamada ambigua. Ver el
-- aviso en su cabecera.

begin;

-- ⚠️ SE BORRA LA VERSIÓN DE DOS ARGUMENTOS ANTES DE CREAR LA DE TRES.
-- `create or replace function` sólo reemplaza cuando la firma es IDÉNTICA; con
-- distinto número de argumentos SOBRECARGA, y las dos quedan vivas. El cron
-- llama `select ops.purgar_webhook_events(3)` (todos los días a las 08:20 UTC)
-- y esa llamada encajaría en las dos versiones (las dos tienen defaults para el
-- resto): Postgres respondería `function ... is not unique` y LA PURGA FALLARÍA
-- CADA NOCHE, en silencio, con la tabla creciendo a ~19,000 filas diarias.
-- Va en la misma transacción que el `create`: no hay ventana sin función.
drop function if exists ops.purgar_webhook_events(int, int);

create or replace function ops.purgar_webhook_events(
  dias int default 3,
  lote int default 20000,
  dias_bajo_volumen int default 90
) returns bigint
language plpgsql
security definer
-- pg_catalog PRIMERO y pg_temp al final. La 0004 lo tenía al revés
-- (`ops, public, pg_catalog`): en una función `security definer`, un esquema
-- escribible antes del catálogo deja sombrear now() o make_interval() y
-- ejecutarlo como el dueño. Todo lo demás del cuerpo va calificado.
set search_path = pg_catalog, pg_temp
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
        -- LISTA DE PERMITIDOS: solo estos canales guardan memoria larga. Un
        -- canal nuevo cae en el corte corto hasta que alguien lo agregue aquí.
        where recibido_at < (case when canal in ('tiktok', 'temu', 'odoo', 'alertas')
                                  then corte_bajo else corte end)
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

-- El candado va donde nace el objeto: una función `security definer` que BORRA
-- no tiene por qué ser ejecutable por PUBLIC. pg_cron la corre como su dueño.
revoke all on function ops.purgar_webhook_events(int, int, int) from public, anon, authenticated;

comment on function ops.purgar_webhook_events(int, int, int) is
  'Retención de ops.webhook_events POR CANAL, con lista de permitidos: tiktok, '
  'temu, odoo y alertas a los `dias_bajo_volumen` (default 90); todo lo demás '
  '(mercado_libre y cualquier canal nuevo) a los `dias` (default 3). Borra por '
  'lotes y registra el resultado en ops.process_log. La programa pg_cron a '
  'diario; también se puede correr a mano: select ops.purgar_webhook_events(3);';

commit;
