-- ═══════════════════════════════════════════════════════════════════════════
-- 0004 — Columnas de caché de canal que el panel usa y el modelo v4 adelgazó
-- de más (hallazgo del F5 de Channel, 2026-08-03): logistica (FBM/FBA/DBM/
-- cross_docking…), stock_fba (Amazon lo reporta aparte de FULL) y moneda.
-- Sin ellas, las lecturas gemelas no pueden responder lo que el panel muestra.
-- Idempotente.
-- ═══════════════════════════════════════════════════════════════════════════
alter table channel.listings add column if not exists logistic_type text;
alter table channel.listings add column if not exists stock_fba integer;
alter table channel.listings add column if not exists currency text;
