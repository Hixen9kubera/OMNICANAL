-- ═══════════════════════════════════════════════════════════════════════════
-- 0003 — DECISIÓN P4 (Eduardo, 2026-07-27): el precio sugerido es POR CANAL.
--
-- costing.costos_finales pasa de PK (sku) a PK (sku, canal):
--   - las filas existentes quedan como canal='mercado_libre' (la fórmula
--     actual es ML-céntrica: comisión ML, fee de envío ML);
--   - cuando el motor calcule precios para amazon/general, insertará filas
--     nuevas por canal SIN pisar las de ML;
--   - costing.costos_validados NO cambia (dimensiones/costo físico: el
--     producto pesa lo mismo en todos los canales).
-- Idempotente: re-aplicable sin efecto si ya migró.
-- ═══════════════════════════════════════════════════════════════════════════

alter table costing.costos_finales
  add column if not exists canal text not null default 'mercado_libre'
  references core.channels(id);

do $$
declare pk_cols int;
begin
  select count(*) into pk_cols
  from pg_constraint
  cross join lateral unnest(conkey) as k
  where conname = 'costos_finales_pkey'
    and conrelid = 'costing.costos_finales'::regclass;
  if pk_cols = 1 then
    alter table costing.costos_finales drop constraint costos_finales_pkey;
    alter table costing.costos_finales add primary key (sku, canal);
  end if;
end $$;

comment on column costing.costos_finales.canal is
  'P4 (2026-07-27): precio sugerido POR CANAL. Hoy solo mercado_libre; la PK '
  '(sku, canal) permite precios distintos por canal sin migrar de nuevo.';
