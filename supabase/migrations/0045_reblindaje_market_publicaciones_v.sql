-- ═══════════════════════════════════════════════════════════════════════════
-- 0045 — RE-BLINDAJE de enrich.market_publicaciones_v: la 0042 le arrancó el
--        security_invoker SIN QUERER, y el workflow «Blindaje BD» no lo ve.
--
-- LA TRAMPA (nueva para el flujo, documentarla es el punto de esta migración):
-- `create or replace view` RESETEA las opciones de la vista. La 0025 dejó
-- `security_invoker = on` en las dos vistas de Competencia; la 0042 (precio
-- vivo, 28-ago) recreó `market_publicaciones_v` y el candado se fue con las
-- opciones viejas — sin que la 0042 dijera una palabra de seguridad. Auditado
-- en producción el 1-sep: `market_skus_v` on (nadie la ha recreado desde la
-- 0025), `market_publicaciones_v` sin opciones.
--
-- El workflow «Blindaje BD» caza "objeto NUEVO sin candado" (0040/0041/0043),
-- pero un `create or replace` de una vista YA blindada lo despoja en silencio
-- y el diff no dispara nada. Regla de la casa que se desprende: toda migración
-- que recree una vista debe re-declarar su `security_invoker` en el mismo
-- archivo (o el workflow aprender a exigirlo).
--
-- Mismo blindaje de siempre (0025/0043/0044). Idempotente.
-- ═══════════════════════════════════════════════════════════════════════════

alter view enrich.market_publicaciones_v set (security_invoker = on);

-- El grant vive desde la 0013 y las recreaciones no lo tocan; se repite por
-- simetría con el patrón y porque repetirlo no cuesta nada.
grant select on enrich.market_publicaciones_v to service_role;

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN
--   select c.relname, c.reloptions
--     from pg_class c join pg_namespace n on n.oid = c.relnamespace
--    where n.nspname = 'enrich' and c.relkind = 'v';
--   -- esperado: las 3 vistas de enrich con security_invoker=on
-- ═══════════════════════════════════════════════════════════════════════════
