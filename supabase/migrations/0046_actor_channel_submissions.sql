-- ═══════════════════════════════════════════════════════════════════════════
-- 0046 · QUIÉN publicó, en la tabla que guarda QUÉ se publicó
-- ═══════════════════════════════════════════════════════════════════════════
--
-- POR QUÉ
--   `ops.channel_submissions` guarda 26,341 envíos a los cinco marketplaces y
--   NO tiene una sola columna de usuario. Se sabe que un SKU se mandó a BEKURA
--   a las 14:32; no si fue Andrea o Cinthya.
--
--   Hasta hoy el "quién" sólo existía en `ops.process_log`, y sólo lo escriben
--   DOS sitios del backend (`routers/publicar.py` y `routers/resolver.py`).
--   Medido el 4-sep: de 90 días de publicaciones —ML 11,399 · Amazon 3,244 ·
--   TikTok 2,048 · Temu 325 · Walmart 127— había persona en 62. Y cruzar las dos
--   tablas por SKU y ventana de tiempo es una heurística: si dos personas tocan
--   el mismo SKU en la misma ventana, acredita a quien no fue.
--
--   Esta migración no pide editar NINGÚN publicador. El cable ya está tendido
--   desde la v0.233.0: `supabase_db.get_cursor` hace
--   `set_config('app.usuario', …, true)` en la misma transacción del INSERT
--   (`_marcar_actor`, supabase_db.py:162), y `kubera_mirror.py:381` hace lo
--   mismo en la suya. Con la columna y su valor por omisión, la firma aparece
--   sola.
--
-- ⚠️ EL ORDEN DE LOS DOS PASOS NO ES ESTILO, ES EL PUNTO
--   Postgres evalúa el default UNA VEZ al agregar la columna y guarda ese
--   resultado como valor de las filas que ya existían. Si esta migración
--   corriera en una sesión donde `app.usuario` está puesto —y el cable lo pone
--   en toda petición del panel—, **las 26,341 filas históricas quedarían
--   firmadas por quien haya corrido la migración**. Una mentira con fecha
--   atrasada, dentro de la tabla que existe justo para no mentir.
--
--   Agregándola SIN default y poniéndolo después, quedan nulas incluso en ese
--   peor escenario. Es exactamente lo que hizo la 0029 y por lo mismo.
--
--   NULL aquí significa **"no se sabe"**, y es la verdad. La pantalla de
--   Monitoreo lo pinta RAYADO, que es distinto de un cero: "no lo sabemos" y
--   "no lo hizo" no pueden verse iguales.
--
-- QUÉ QUEDA FIRMADO Y QUÉ NO (medido leyendo cada sitio que inserta, no supuesto)
--
--   SÍ, desde el primer envío después de aplicar esto — pasan por
--   `supabase_db` o por el espejo, que marcan al actor:
--     services/publicar_ready.py  → altas de Mercado Libre
--     services/publicar.py        → actualizaciones de ML y altas de Amazon
--     services/publicar_tiktok.py → TikTok desde el panel
--     services/publicar_temu.py   → Temu desde el panel (v0.398.0)
--     services/publicar_walmart.py→ Walmart desde el panel
--     services/kubera_mirror.py   → el espejo
--
--   NO, y hay que decirlo en voz alta — son procesos SIN sesión, así que
--   `app.usuario` viene vacío y la fila nace NULA:
--     scripts/publicar_temu.py       (las 307 altas de los lotes)
--     scripts/publicar_walmart.py    (las 127 altas)
--     scripts/rescatar_bajas_ip_amazon.py, rescatar_historia_imagenes.py
--     y los scripts de escritorio de TikTok, que ni siquiera están en el repo
--
--   Esos son el siguiente trabajo: que un script declare quién lo corre. Esta
--   migración no lo resuelve y no pretende hacerlo.
--
-- QUÉ **NO** HACE
--   No borra ni reescribe historia. No toca las filas viejas. No inventa un
--   actor para lo que hizo un cron: un sondeo, un backfill o el fan-out deben
--   quedar en NULL, porque no los hizo nadie.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- ───────────────────────────────────────────────────────────────────────────
-- A · La columna, en dos tiempos (ver el aviso del encabezado)
-- ───────────────────────────────────────────────────────────────────────────
alter table ops.channel_submissions add column if not exists actor text;

alter table ops.channel_submissions alter column actor
  set default nullif(current_setting('app.usuario', true), '');

comment on column ops.channel_submissions.actor is
  'Quién pidió el envío. Lo llena solo el valor por omisión, leyendo el ajuste '
  'que deja core/actor.py vía supabase_db.get_cursor. NULO = no hubo persona '
  'detrás (script, cron, backfill, espejo) o el envío es anterior al 4-sep-2026. '
  'NULO NO es cero: la pantalla de Monitoreo lo pinta rayado, "no lo sabemos".';

-- ───────────────────────────────────────────────────────────────────────────
-- B · El índice que va a usar Monitoreo
-- ───────────────────────────────────────────────────────────────────────────
-- La consulta que viene es "qué publicó esta persona, en este canal, en esta
-- semana". Parcial sobre `actor is not null` porque hoy la inmensa mayoría de
-- las 26,341 filas son nulas y seguirán siéndolo: indexarlas sería pagar por
-- guardar el hueco.
create index if not exists ix_channel_submissions_actor_fecha
  on ops.channel_submissions (actor, created_at desc)
  where actor is not null;

commit;

-- ═══════════════════════════════════════════════════════════════════════════
-- CÓMO SE COMPRUEBA QUE QUEDÓ BIEN
--
--   -- 1) Las históricas NO quedaron firmadas. Tiene que dar 0.
--   select count(*) from ops.channel_submissions
--    where actor is not null and created_at < now() - interval '1 hour';
--
--   -- 2) El default está puesto.
--   select column_default from information_schema.columns
--    where table_schema='ops' and table_name='channel_submissions'
--      and column_name='actor';
--
--   -- 3) Y que de verdad firma: publica algo desde el panel y mira la última.
--   select created_at, canal, sku, operacion, actor
--     from ops.channel_submissions order by created_at desc limit 5;
-- ═══════════════════════════════════════════════════════════════════════════
