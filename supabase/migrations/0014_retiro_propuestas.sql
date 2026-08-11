-- ═══════════════════════════════════════════════════════════════════════════
-- 0014 — Paso 7a del PLAN_COMPETENCIA_v2: retiro de `propuestas` SIN destruir.
--
-- Estado: APLICADA (Eduardo, 2026-08-11) — producción tukwcvsi. En sandbox es
-- un no-op: `propuestas` nunca existió ahí (no está trackeada en migraciones;
-- se creó ad-hoc durante el MVP de Competencia).
--
-- `alter schema … rename` en vez de `drop … cascade`: mismo efecto funcional
-- (nadie lo encuentra por su nombre), REVERSIBLE con un rename de vuelta.
-- El drop definitivo es la 0015, agendada ~1 semana después con el panel
-- estable — la deuda no cobra intereses por esperar; el drop sí es
-- irreversible.
--
-- Precondiciones verificadas antes de aplicar (v0.100.0 en producción):
--   · CERO lectores: la última lectura (resultados()/GET /detalle) se podó en
--     el paso 6; el backend lee 100% enrich.market_*.
--   · CERO escritores: el cron nunca existió como servicio; capturas manuales.
--   · Contenido a salvo: 15,307 filas migradas a enrich.market_* (conteos
--     exactos), 295 de competencia_resultados archivadas en JSON, línea base
--     completa (~9 MB DB + 17 respuestas HTTP) en verificacion_competencia/.
--
-- Guardado por existencia + idempotente: re-correrla no hace nada.
-- ═══════════════════════════════════════════════════════════════════════════

do $$
begin
  if exists (select 1 from information_schema.schemata
              where schema_name = 'propuestas') then
    execute 'alter schema propuestas rename to propuestas_retirado';
  end if;
end $$;

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN
--   select schema_name from information_schema.schemata
--    where schema_name like 'propuestas%';   -- solo 'propuestas_retirado'
--
-- ROLLBACK (si el panel resiente algo esta semana):
--   alter schema propuestas_retirado rename to propuestas;
--
-- 0015 (drop definitivo, agendado):
--   drop schema propuestas_retirado cascade;
-- ═══════════════════════════════════════════════════════════════════════════
