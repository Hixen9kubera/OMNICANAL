-- ═══════════════════════════════════════════════════════════════════════════
-- 0044 — BLINDAJE de enrich.market_highlights_hist (la 0043_market_highlights_hist
--        la creó sin RLS; el workflow «Blindaje BD» volvió a ponerse rojo).
--
-- Tercera vez del mismo patrón en el mismo flujo (0040 vista, 0041 tabla, y
-- ahora ésta): el candado se agrega en una migración posterior porque la
-- original ya está empujada y aplicada. Mismo blindaje de la casa (0025/0043):
-- RLS activa + CERO políticas — solo pasa quien hace bypass (service_role y
-- postgres), que son el backend y el cron. El frontend no toca enrich con la
-- llave anon.
--
-- NOTA de numeración: hay DOS migraciones «0043_*» (blindaje_enrich y
-- market_highlights_hist) — colisión de sesiones concurrentes del 28-ago. No se
-- renombra ninguna: ambas ya están aplicadas en producción y el orden entre
-- ellas es indistinto. Ésta toma el 0044 para cortar la ambigüedad.
--
-- Idempotente.
-- ═══════════════════════════════════════════════════════════════════════════

alter table enrich.market_highlights_hist enable row level security;
grant all on enrich.market_highlights_hist to service_role;
