-- ═══════════════════════════════════════════════════════════════════════════
-- 0047 · POR DÓNDE se publicó, además de QUIÉN
-- ═══════════════════════════════════════════════════════════════════════════
--
-- POR QUÉ
--   La 0046 le dio a `ops.channel_submissions` la columna `actor`: el QUIÉN.
--   Falta el POR DÓNDE, y son dos preguntas distintas.
--
--   Brandon lo pidió con un caso concreto (4-sep): publicó tres cosas a Walmart
--   —una con el botón del panel y dos de prueba corriendo código desde un chat—
--   y el tablero no podía distinguirlas. Textual: *"debe aparecer mi nombre y
--   que los realicé con Claude, y no fue con función del botón publicar"*.
--
--   Sin esta columna la única salida es la mala, y ya se vio en la práctica:
--   meter la explicación DENTRO del nombre. El 4-sep aparecieron nueve filas con
--   `actor = 'eduardo@kubera.mx (vía Claude, carga del 4-sep)'`, y el efecto fue
--   que Eduardo salía DOS VECES en la pantalla, con sus números partidos. Es el
--   mismo defecto que `crear` tenía guardando el mensaje de error dentro de
--   `accion`: una columna que significa una cosa, usada para otra.
--
--   `ops.process_log` YA resolvió esto: tiene `origen` desde su primera versión
--   y hoy guarda `panel`, `backfill`, `crear_producto`, `recalculo`, `apify`,
--   `pg_cron`. Esta migración le da la misma columna a la tabla de envíos, con
--   el mismo mecanismo de la 0046 — el valor por omisión leyendo un ajuste que
--   el backend deja puesto, así que ningún publicador cambia.
--
-- ⚠️ LOS DOS PASOS, OTRA VEZ, Y POR LO MISMO
--   Postgres evalúa el default UNA VEZ al agregar la columna y se lo guarda a
--   las filas que ya existían. Si corriera con `app.origen` puesto, las 26 mil
--   filas históricas dirían todas que vinieron del panel. Se agrega SIN default
--   y se pone después. NULL = no se sabe por dónde entró, que es la verdad.
--
-- QUÉ NO HACE
--   No inventa el origen de lo viejo. No toca `detail_ref`, que sigue siendo la
--   referencia al detalle (el `feedId` de Walmart, la fila de MySQL) y no una
--   procedencia — son cosas distintas y mezclarlas sería repetir el error que
--   esta migración viene a arreglar.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

alter table ops.channel_submissions add column if not exists origen text;

alter table ops.channel_submissions alter column origen
  set default nullif(current_setting('app.origen', true), '');

comment on column ops.channel_submissions.origen is
  'POR DÓNDE entró el envío: panel, script, claude, cron… Lo llena solo el '
  'valor por omisión, leyendo el ajuste que deja core/actor.py vía '
  'supabase_db.get_cursor. NULO = no se sabe. Es el compañero de `actor` '
  '(QUIÉN) y no debe confundirse con `detail_ref`, que apunta al detalle.';

commit;

-- ═══════════════════════════════════════════════════════════════════════════
-- COMPROBACIÓN
--   -- Las históricas NO se firmaron. Tiene que dar 0.
--   select count(*) from ops.channel_submissions
--    where origen is not null and created_at < now() - interval '1 hour';
--
--   -- Y el reparto, cuando ya haya datos:
--   select canal, origen, count(*) from ops.channel_submissions
--    where origen is not null group by 1,2 order by 3 desc;
-- ═══════════════════════════════════════════════════════════════════════════
