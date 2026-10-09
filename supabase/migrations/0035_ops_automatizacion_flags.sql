-- ═══════════════════════════════════════════════════════════════════════════
-- 0035 — El interruptor de la automatización.
--
-- Estado: APLICADA. Sandbox (yvootpbz) y produccion (kubera) el 26-ago-2026
-- con el si de Eduardo, 8/8 en cada una dentro de la transaccion antes de
-- confirmar. Entre las verificaciones va la COMPATIBILIDAD con el codigo ya
-- desplegado: se corre el UPSERT exacto de `fijar_interruptor` y la lectura
-- exacta de `_leer_flag`, y se deshacen. `ops` paso de 13 a 14 tablas.
--
-- POR QUÉ SALE DE LA 0033
-- ------------------------
-- La `0033_ops_odoo_sale_orders.sql` crea TRES tablas: esta y las dos de la
-- bitácora de órdenes de Odoo. Se separa porque no tienen nada que ver entre sí
-- y, sobre todo, porque **sus tiempos son distintos**:
--
--   · Esta tabla no la discute nadie y su forma es compatible con el código que
--     ya está desplegado. Se puede aplicar hoy.
--   · Las dos de la bitácora necesitan correcciones que tocan Python
--     (`cuenta` en la llave, la foto de stock por warehouse_id). Ver
--     `PROPUESTA_TABLAS_ODOO.sql`.
--
-- Empaquetadas juntas, discutir la forma del log mantiene el interruptor sin
-- existir. Y sin él, `fijar_interruptor` (`odoo_ventas.py:161`) escribe contra
-- una tabla que no está: el switch del panel **no funciona en ningún sentido**,
-- ni para encender ni para apagar.
--
-- No es una emergencia — hoy la automatización está apagada de verdad: la
-- variable `ODOO_VENTAS_ENABLED` no existe en Railway y el valor por omisión en
-- `config.py` es `False`, así que no hay nada que detener. Es al revés: esta
-- tabla es el requisito para poder ENCENDERLA algún día desde el panel.
--
-- POR QUÉ NO VIVE EN UNA VARIABLE DE RAILWAY
-- -------------------------------------------
-- Dos razones, las dos correctas y las dos ya escritas en las reglas de la casa:
--   1. Cambiar una variable en Railway REINICIA el contenedor (regla 12). Un
--      apagado de emergencia no puede costar un reinicio del backend entero.
--   2. Un apagado que solo viva en memoria del proceso se DESHACE SOLO en el
--      siguiente deploy — la peor propiedad imaginable para un botón de pánico:
--      alguien lo apaga, se va tranquilo, y horas después vuelve solo.
--
-- La variable de entorno queda como VALOR POR OMISIÓN; esta tabla manda en
-- cuanto alguien toca el switch. `habilitado()` ya trae caché con TTL de 30 s,
-- así que el costo por venta está resuelto.
--
-- EL NOMBRE NO SE CAMBIA
-- -----------------------
-- `claude-opus` propuso renombrarla a `ops.feature_switches`, con el argumento
-- —correcto— de que `automatizacion_flags` viene del nombre de la pestaña y no
-- del dominio. Se rechaza: el código YA DESPLEGADO la nombra en
-- `odoo_ventas.py:116` y `:162`. Renombrarla rompe producción.
--
-- QUÉ CONTESTA Y QUÉ NO
-- ----------------------
-- Guarda el ESTADO, no la historia: una fila por flag, y cada cambio pisa al
-- anterior. Contesta "quién lo dejó así", NO "quién lo apagó el martes". Para un
-- flujo que mueve inventario y contabilidad, esa segunda es la pregunta de la
-- autopsia — y para eso el movimiento debe dejar además un renglón en
-- `ops.process_log`, que sí es append-only. Hoy solo queda un `log.warning` en
-- Railway, que se borra.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

create table if not exists ops.automatizacion_flags (
    -- El CHECK de forma no es adorno: es un KV abierto y sin él acumula llaves
    -- tecleadas a mano ('Odoo_Ventas', 'odoo-ventas') que nadie vuelve a leer.
    flag            text        primary key check (flag ~ '^[a-z0-9_]+$'),

    -- boolean y no text, a propósito: mantiene el KV honesto y evita que esto
    -- se convierta en una sopa de configuración.
    valor           boolean     not null,

    motivo          text,                      -- por qué se apagó, si se apagó
    creado_at       timestamptz not null default now(),
    actualizado_at  timestamptz not null default now(),
    actualizado_por text                       -- no es adorno: un flujo que
                                               -- mueve inventario tiene que
                                               -- poder decir quién lo apagó
);

comment on table ops.automatizacion_flags is
    'Interruptores de automatización. Mandan sobre las variables de entorno, '
    'que quedan como valor por omisión. Persistido a propósito: un apagado no '
    'puede deshacerse solo en el siguiente deploy. Guarda el ESTADO, no la '
    'historia — cada cambio pisa al anterior.';

comment on column ops.automatizacion_flags.actualizado_por is
    'Quién movió el switch. Contesta "quién lo dejó así", no "quién lo apagó el '
    'martes": para la historia va un renglón aparte a ops.process_log.';

-- Regla de la casa desde 0001 (ver 0028): RLS activa y CERO políticas. Solo
-- pasa quien hace bypass —service_role y postgres—, que es como se conecta el
-- backend. Sin esto, `verificar_rls.py` sale en rojo y `blindaje-bd.yml` marca
-- el push.
alter table ops.automatizacion_flags enable row level security;

commit;

-- ═══════════════════════════════════════════════════════════════════════════
-- CÓMO SE USA (lo hace el panel; aquí queda de referencia)
--
--   -- ver el estado
--   select flag, valor, motivo, actualizado_por, actualizado_at
--     from ops.automatizacion_flags where flag = 'odoo_ventas_enabled';
--
--   -- moverlo (es lo que hace odoo_ventas.fijar_interruptor)
--   insert into ops.automatizacion_flags (flag, valor, motivo, actualizado_por)
--   values ('odoo_ventas_enabled', false, 'se duplicaban las órdenes', 'eduardo@kubera.mx')
--   on conflict (flag) do update set
--       valor = excluded.valor, motivo = excluded.motivo,
--       actualizado_at = now(), actualizado_por = excluded.actualizado_por;
--
--   -- devolverlo al valor por omisión de la variable de entorno
--   delete from ops.automatizacion_flags where flag = 'odoo_ventas_enabled';
--
-- REVERSIÓN COMPLETA
--   drop table if exists ops.automatizacion_flags;
-- ═══════════════════════════════════════════════════════════════════════════
