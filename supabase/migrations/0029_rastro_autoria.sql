-- ═══════════════════════════════════════════════════════════════════════════
-- 0029 — RASTRO DE AUTORÍA (paso 2): quién creó qué producto.
--
-- POR QUÉ
--   El paso 1 (v0.233.0) tendió el cable: el backend ya le dice a la base quién
--   pide cada operación, con `set_config('app.usuario', …, true)` en
--   `supabase_db.get_cursor`. Los costos quedaron atribuidos solos, porque el
--   trigger de `costing.cost_history` ya leía ese valor desde su primera
--   versión.
--
--   `ops.process_log` no tenía dónde guardarlo. Y ahí viven las creaciones de
--   producto: 2,821 eventos `proceso='crear'` de los 3,407 de la tabla. Era la
--   mitad que faltaba de lo que se pidió — "quién crea qué producto y quién
--   mueve qué costo".
--
-- POR QUÉ NO SE TOCA NI UNA LÍNEA DE PYTHON
--   `ops.process_log` la escriben cinco lugares distintos (`crear_producto`,
--   `costing_mirror`, `kubera_mirror`, `costos`, y el backfill), y todos listan
--   sus columnas explícitamente. Una columna con VALOR POR OMISIÓN que lee el
--   mismo ajuste que ya deja el cable se llena sola en los cinco, sin tocarlos.
--   Si mañana aparece un sexto escritor, también queda cubierto.
--
-- ⚠️ EL ORDEN DE LOS DOS `alter` NO ES ESTÉTICO — MEDIDO EN EL SANDBOX
--   Postgres evalúa el default UNA VEZ al agregar la columna y guarda ese
--   resultado como valor de las filas que ya existían. Si esta migración
--   corriera en una sesión donde `app.usuario` está puesto —y el cable lo pone
--   en toda petición del panel—, **las 3,407 filas históricas quedarían
--   firmadas por quien haya corrido la migración**. Una mentira con fecha
--   atrasada, dentro de la tabla que existe justo para no mentir.
--
--   Probado: agregando la columna CON default en una sesión marcada, las filas
--   viejas se llevan el nombre. Agregándola SIN default y poniéndolo después,
--   quedan nulas incluso en ese peor escenario. Por eso van separados.
--
--   NULL aquí significa "no se sabe", y es la verdad: de lo que pasó antes del
--   cable no hay registro de quién fue.
--
-- QUÉ **NO** HACE
--   No borra ni reescribe historia. No toca `channel.listing_history` — sus
--   178,000 filas son casi todas de procesos automáticos (`sync`,
--   `corte_channel`, censos), así que el "quién" ahí sería una máquina el 98%
--   de las veces; se deja fuera a propósito hasta que haya un motivo.
--   Y no ve a quien entra DIRECTO a la base por el dashboard: eso no lo puede
--   registrar ningún código nuestro.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- ───────────────────────────────────────────────────────────────────────────
-- A · La columna, en dos tiempos (ver el aviso del encabezado)
-- ───────────────────────────────────────────────────────────────────────────
alter table ops.process_log add column if not exists actor text;

alter table ops.process_log alter column actor
  set default nullif(current_setting('app.usuario', true), '');

comment on column ops.process_log.actor is
  'Quién pidió la operación. Lo llena solo el valor por omisión, leyendo el '
  'ajuste que deja core/actor.py vía supabase_db.get_cursor. NULO = no hubo '
  'persona detrás (cron, sondeo, webhook, backfill) o el evento es anterior '
  'al cable de v0.233.0.';

-- ───────────────────────────────────────────────────────────────────────────
-- B · Que el historial de costos diga NULO y no cadena vacía
--
-- `current_setting(…, true)` devuelve '' —no nulo— en cuanto el ajuste se tocó
-- una vez en esa conexión. Sin esto, `cambiado_por` acaba con una mezcla de ''
-- y NULL que significan lo mismo y se ven distinto: quien consulte la tabla
-- tendría que acordarse de filtrar las dos formas. Es la única diferencia
-- respecto de la versión anterior de esta función.
-- ───────────────────────────────────────────────────────────────────────────
create or replace function costing.fn_cost_history() returns trigger
language plpgsql
as $$
begin
  insert into costing.cost_history (sku, tabla, version, snapshot, cambiado_por,
                                    accion, formula_ver, currency)
  values (
    old.sku,
    tg_table_name,
    coalesce((select max(version) from costing.cost_history h
              where h.sku = old.sku and h.tabla = tg_table_name), 0) + 1,
    to_jsonb(old),
    nullif(current_setting('app.usuario', true), ''),   -- ← el único cambio
    coalesce(current_setting('app.accion', true), 'auto'),
    current_setting('app.formula_ver', true),
    'MXN'
  );
  return coalesce(new, old);
end
$$;

-- ───────────────────────────────────────────────────────────────────────────
-- C · Una sola ventanilla para preguntar "¿quién tocó este SKU?"
--
-- Sin esto, contestar esa pregunta obliga a consultar dos tablas con columnas
-- que se llaman distinto y a unirlas a mano cada vez. La vista lo deja en una
-- consulta con un `where sku = …`.
--
-- `security_invoker = on` desde que nace: atiende con la identidad de quien
-- pregunta, no con la de su dueño. Es la regla que dejó la 0025, y la prueba de
-- CI la exige.
-- ───────────────────────────────────────────────────────────────────────────
create or replace view ops.rastro_autoria as
  select 'costos'::text            as area,
         h.sku::text               as sku,
         h.created_at              as cuando,
         h.cambiado_por            as quien,
         h.accion                  as que,
         h.tabla                   as donde
    from costing.cost_history h
  union all
  select p.proceso::text,
         p.sku::text,
         p.created_at,
         p.actor,
         p.accion,
         p.origen
    from ops.process_log p;

alter view ops.rastro_autoria set (security_invoker = on);
grant select on ops.rastro_autoria to service_role;

comment on view ops.rastro_autoria is
  'Quién tocó cada SKU, juntando el historial de costos y la bitácora de '
  'procesos. `quien` NULO = sin persona detrás, o anterior al cable de '
  'v0.233.0. Uso: select * from ops.rastro_autoria where sku = ''ABC-123'' '
  'order by cuando desc;';

commit;

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN — después de aplicar
-- ═══════════════════════════════════════════════════════════════════════════
-- -- 1) Las filas históricas quedaron SIN firmar (esperado: 0 firmadas)
-- select count(*) filter (where actor is not null) as firmadas, count(*) as total
-- from ops.process_log;
--
-- -- 2) El default quedó puesto
-- select column_default from information_schema.columns
-- where table_schema = 'ops' and table_name = 'process_log' and column_name = 'actor';
--
-- -- 3) La vista responde
-- select * from ops.rastro_autoria order by cuando desc limit 5;

-- ═══════════════════════════════════════════════════════════════════════════
-- REVERSA
-- ═══════════════════════════════════════════════════════════════════════════
-- begin;
-- drop view if exists ops.rastro_autoria;
-- alter table ops.process_log drop column if exists actor;
-- -- La función vuelve a su versión anterior cambiando el `nullif(...)` por
-- -- `current_setting('app.usuario', true)` a secas. No hace falta revertirla:
-- -- el cambio solo afecta a filas NUEVAS y no rompe nada si se queda.
-- commit;
