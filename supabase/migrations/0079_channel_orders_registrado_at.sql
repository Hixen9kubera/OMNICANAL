-- ═══════════════════════════════════════════════════════════════════════════
-- 0079 — `channel.orders.registrado_at`: la hora en que KUBERA registró la venta.
--        28-oct-2026. PLAN MAESTRO 2.2 (F2.1 del plan de salida de Woo).
--
-- POR QUÉ
-- La planeación de OV y el vigilante de «reclamado sin completar» (F2.3) necesitan
-- la hora en que NOSOTROS registramos la venta, que no es la del canal. Hoy no hay
-- columna para eso: desde el reclamo de v0.176.0 (14-ago 19:43 UTC) `creado_at`
-- nace con la hora del reclamo y se queda así (el ON CONFLICT no la toca), y antes
-- era la fecha de la venta. Una sola columna con dos significados según la fecha.
-- El libro (`stock_libro`), la tanda de T0 (0076: `t0_pendientes`, `t0_cuadre`) y
-- `fanout_bodegas` leen HOY ese `creado_at`; esta migración no lo toca.
--
-- QUÉ HACE
--   · Agrega `registrado_at timestamptz` y DESPUÉS le pone `default now()`: las
--     filas que ya existen quedan en NULL («no se sabe»), no con la hora de la
--     migración. Cada fila nueva nace con la hora de su INSERT, sea el esqueleto
--     de `orders_write.reclamar`, el registro directo o el reproceso de la cola.
--   · Ningún código la nombra al escribir: el default basta, así que esta
--     migración y el código se pueden desplegar en cualquier orden. Ningún UPDATE
--     la toca (el ON CONFLICT de `kubera_mirror._up_channel_orders` no la incluye).
--   · El candado de la tabla se reafirma en la misma migración (patrón de la casa:
--     RLS sin políticas + grant a service_role; la tabla ya lo tenía desde la 0002).
--
-- LO QUE NO HACE: rellenar las filas viejas. Eso es un cambio de datos en
-- producción y va con su acta (propuesta: `registrado_at = creado_at` en las filas
-- reclamadas, desde el 14-ago 19:43:52 UTC; las anteriores quedan en NULL).
--
-- Estado: aplicar PRIMERO en el sandbox. Producción solo con acta (doble candado).
--
-- REVERSA (nadie la lee todavía):
--   alter table channel.orders drop column registrado_at;
-- ═══════════════════════════════════════════════════════════════════════════

begin;

alter table channel.orders add column if not exists registrado_at timestamptz;
alter table channel.orders alter column registrado_at set default now();

comment on column channel.orders.registrado_at is
  'Cuándo kubera registró la venta (el INSERT de la fila: el reclamo de orders_write.reclamar o el registro '
  'directo), no la fecha del canal. Nace con default now() y ningún UPDATE la toca. NULL = fila anterior a la '
  '0079 (no se sabe).';

alter table channel.orders enable row level security;
grant all on channel.orders to service_role;

insert into ops.migraciones (migracion, detalle)
select '0079_channel_orders_registrado_at',
       jsonb_build_object('tabla', 'channel.orders', 'columna', 'registrado_at', 'plan_maestro', '2.2')
 where to_regclass('ops.migraciones') is not null
   and not exists (select 1 from ops.migraciones where migracion = '0079_channel_orders_registrado_at');

commit;
