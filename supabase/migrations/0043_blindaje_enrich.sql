-- ═══════════════════════════════════════════════════════════════════════════
-- 0043 — BLINDAJE de enrich: lo que la 0040 y la 0041 dejaron sin candado.
--
-- Lo cazó el workflow «Blindaje BD» (rojo en main desde que entraron):
--   · enrich.market_highlights        nació en la 0041 sin RLS.
--   · enrich.market_categoria_prioridad_v  nació en la 0040 sin
--     security_invoker: la vista atendía con el gafete de su dueño
--     (postgres, BYPASSRLS) y le entregaba todo a quien preguntara.
--
-- Mismo patrón que la 0025 (que cerró el lote 0020–0024): RLS activa y CERO
-- políticas — solo pasa quien hace bypass (service_role y postgres), que es
-- exactamente quien debe leer esto. El backend y el cron de Competencia entran
-- como postgres; el frontend no toca enrich con la llave anon (verificado:
-- ninguna referencia en frontend/).
--
-- Idempotente: los tres ALTER se pueden correr dos veces sin daño.
-- ═══════════════════════════════════════════════════════════════════════════

alter table enrich.market_highlights enable row level security;
grant all on enrich.market_highlights to service_role;

alter view enrich.market_categoria_prioridad_v set (security_invoker = on);
