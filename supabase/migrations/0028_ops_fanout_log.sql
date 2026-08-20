-- 0028_ops_fanout_log.sql — La BITÁCORA del fan-out, que se nos había quedado
-- fuera al migrar el CANDADO.
--
-- EL ERROR QUE CORRIGE, Y DE QUIÉN ES
-- ------------------------------------
-- `fanout_log` guarda DOS cosas distintas en la misma tabla:
--
--   1. la MARCA de idempotencia — "¿ya moví esta mercancía?"
--   2. la BITÁCORA — qué se intentó, con qué stock, con qué resultado, en
--      cuántos ms
--
-- La migración 0022 se llevó (1) a `ops.fulfillment_operations` y dejó (2) sin
-- casa. El censo del 20-ago lo destapó: `fanout_log` tiene **9 lectores**, y
-- solo los DOS que deciden estaban cubiertos. Los otros cuatro —las pantallas
-- de observación del fan-out— se habrían quedado en blanco el día del corte:
--
--     routers/fanout.py::full_observacion
--     routers/fanout.py::inventario_pendientes
--     services/fanout_stock.py::historial
--     services/fanout_stock.py::resumen
--
-- Y el error es exactamente el que el consejo ya nos había señalado para los dos
-- candados —*"dos conceptos distintos en una tabla porque ya existe y es
-- cómoda"*— cometido esta vez por nosotros, un nivel más arriba, mientras
-- arreglábamos justo eso.
--
-- POR QUÉ TABLA PROPIA Y NO `ops.process_log`
-- --------------------------------------------
-- El plan mandaba las bitácoras a `ops.process_log`, que guarda el detalle en un
-- JSON. Pero estos lectores no leen filas sueltas: **agrupan**.
--
--     SELECT accion, COUNT(*)            GROUP BY accion
--     SELECT canal, accion, COUNT(*)     GROUP BY canal, accion
--     WHERE resultado LIKE 'ERROR%'
--
-- Eso pide columnas, no un blob. Meterlo en `process_log` obligaría a sacar cada
-- campo del JSON en cada consulta del dashboard — y sería, otra vez, guardar dos
-- cosas distintas en el mismo lugar porque ya existe.
--
-- `id bigserial` NO es decorativo aquí: los cuatro lectores ordenan por
-- `ORDER BY id DESC` para tener "lo más reciente primero" sin depender de que
-- dos eventos del mismo segundo se desempaten bien.
--
-- IDEMPOTENTE: se puede re-aplicar sin daño.

create table if not exists ops.fanout_log (
    id          bigserial   primary key,
    ts          timestamptz not null default now(),
    sku         citext,
    motivo      text,
    dry_run     boolean     not null default false,
    stock_drop  integer,
    objetivo    integer,
    canal       text,
    cuenta      text,
    item_id     text,
    accion      text,
    stock_canal integer,
    resultado   text,
    ms          numeric(10,1)
);

-- Los tres caminos por los que se consulta, y nada más.
create index if not exists ix_fanout_log_ts      on ops.fanout_log (ts desc);
create index if not exists ix_fanout_log_accion  on ops.fanout_log (accion);
create index if not exists ix_fanout_log_sku     on ops.fanout_log (sku);

comment on table ops.fanout_log is
  'Bitacora del fan-out de stock. Reemplaza fanout_log del MySQL kubera_ml. '
  'NO confundir con ops.fulfillment_operations: aquella guarda la MARCA de '
  'idempotencia (una fila por operacion aplicada) y esta el HISTORIAL de lo '
  'intentado. Vivian juntas en MySQL y son dos cosas distintas.';

comment on column ops.fanout_log.resultado is
  'Texto libre. Los lectores filtran errores con LIKE ''ERROR%%'' y '
  'routers/fanout.py saca el tipo de movimiento de ML del inicio de la frase. '
  'Sacar un dato de un texto es fragil — misma familia que el regex del FBA que '
  'se mato en 0022 — pero se conserva porque el repunte debe contestar IGUAL, '
  'no mejor. Arreglarlo es trabajo aparte.';

-- ── RLS: la tabla nace blindada ─────────────────────────────────────────────
-- Mismo patron que `0025_blindaje_rls.sql`: RLS activada y CERO politicas =
-- deny-by-default. `service_role` tiene `rolbypassrls`, asi que el backend y los
-- crons no se enteran.
alter table ops.fanout_log enable row level security;

-- ── El ancla del respaldo: `mysql_id` ───────────────────────────────────────
-- La bitacora NO tiene llave natural: dos intentos del mismo SKU con el mismo
-- resultado son DOS eventos, no uno repetido. Poner una llave sobre el contenido
-- perderia justo lo que se quiere ver.
--
-- Pero eso deja el respaldo sin red: una segunda corrida duplicaria las 19,616
-- filas y nadie lo notaria hasta ver el dashboard contando doble.
--
-- `mysql_id` guarda el `id` de la fila original y lleva un UNICO. Con eso el
-- respaldo es idempotente (`on conflict do nothing`) y —lo que importa mas— se
-- puede correr OTRA VEZ para cubrir el hueco entre el respaldo y el momento en
-- que se encienda la escritura doble, sin miedo a repetir lo ya copiado.
--
-- Va NULL en los eventos nuevos, y en Postgres un UNICO admite varios NULL, asi
-- que la escritura doble no se estorba.
alter table ops.fanout_log add column if not exists mysql_id bigint;
create unique index if not exists ux_fanout_log_mysql_id
    on ops.fanout_log (mysql_id) where mysql_id is not null;

comment on column ops.fanout_log.mysql_id is
  'id de la fila original en el MySQL kubera_ml. Solo lo traen las filas del '
  'respaldo; los eventos nuevos van NULL. Es lo que hace el respaldo repetible.';

-- ── `ms` es DECIMAL(10,1) en MySQL, no un entero ────────────────────────────
-- Lo destapo la verificacion del respaldo: 58 de 200 filas de la muestra no
-- coincidian campo por campo, y era siempre `ms` — 1470.7 llegaba como 1471.
-- Los TOTALES cuadraban y los conteos POR ACCION tambien; solo el cotejo fila
-- por fila lo vio.
--
-- Es exactamente para lo que sirve verificar contra el ORIGEN y no contra el
-- propio contador del script: "copie 19,647 filas" era cierto y aun asi el dato
-- estaba cambiado.
--
-- Un `alter` aparte para que la 0028 se pueda re-aplicar sobre una base que ya
-- la tenia con `integer`.
alter table ops.fanout_log alter column ms type numeric(10,1);
