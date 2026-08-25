-- ═══════════════════════════════════════════════════════════════════════════
-- 0032 — La marca de "ya lo revisé" en el costeo.
--
-- Estado: APLICADA. Sandbox 25-ago-2026 (ciclo marcar/desmarcar probado ahí),
-- producción 25-ago-2026 con el visto de Eduardo. Va ANTES del deploy: el
-- SELECT de `costing_read.listado` ya nombra `revisado_at`, así que desplegar
-- primero tronaría la pantalla de Costos con UndefinedColumn. Agregar columnas
-- nulas no afecta al código viejo, así que este orden no deja ventana rota.
--
-- QUÉ RESUELVE
-- ------------
-- El equipo está revisando el costeo SKU por SKU contra el packing list del
-- proveedor, y hasta ahora no había dónde anotar cuáles ya pasaron. La lista
-- de pendientes vivía fuera del sistema.
--
-- Dos columnas: `revisado_at` (vacío = sin revisar) y `revisado_por` (quién).
--
-- POR QUÉ NO ES UNA TABLA APARTE, NI UN HASH, NI UN SNAPSHOT
-- ----------------------------------------------------------
-- Se diseñó primero algo mucho más grande: una tabla `revision_costeo` con el
-- snapshot en jsonb de los valores revisados, para poder contestar "¿sigue
-- siendo válida la marca?" y "¿QUÉ cambió desde entonces?". Se descartó cuando
-- quedó claro el alcance real: **el equipo no va a cambiar los costos**, solo
-- necesita distinguir lo procesado de lo pendiente. Toda esa maquinaria existía
-- para resolver la invalidación, y sin cambios que invalidar no tiene objeto.
--
-- Si algún día sí hace falta, el camino ya está estudiado y documentado en
-- `agents/counselors/1787608711-ronda-2-*` — con una conclusión que vale la
-- pena rescatar: la comparación tiene que ser por CONTENCIÓN (`snapshot <@
-- actual`), no por igualdad, o agregar una columna al costeo invalida de golpe
-- todas las marcas.
--
-- CÓMO SE SABE SI LA FILA SE MOVIÓ DESPUÉS
-- -----------------------------------------
-- Sin columna nueva: `updated_at > revisado_at`. El trigger
-- `trg_touch_costos_validados` ya mantiene `updated_at`, y el único escritor
-- vivo (`costing_mirror.upsert_validados`) trae su propio guard
-- `IS DISTINCT FROM`, así que un guardado que no cambia nada NO mueve la fecha.
--
-- Ojo con el falso positivo: `costos_validados` tiene 4 columnas que no son de
-- costo (`wc_id`, `wc_status`, `wc_type`, `contenedor`). Un backfill de
-- cualquiera de ellas mueve `updated_at` y marcaría filas como "cambió" sin que
-- un número de costo se haya movido. Para "marcar lo procesado" eso es una
-- molestia (se vuelve a marcar), no un error.
--
-- EFECTO SECUNDARIO MEDIDO EN EL SANDBOX: marcar TAMBIÉN mueve `updated_at`.
-- `trg_touch_costos_validados` es BEFORE UPDATE sin `when`, así que dispara con
-- el update de la marca. No rompe la detección — `now()` es la hora de la
-- TRANSACCIÓN, así que ambas fechas quedan idénticas y `>` da falso, que es lo
-- correcto recién marcado (probado: marcar → iguales; tocar 1.2 s después en
-- otra transacción → movida = true).
--
-- Lo que sí ensucia: `updated_at` deja de significar "cuándo cambió el costeo"
-- y pasa a ser "cuándo se tocó la fila". Quien lo use como proxy de actividad
-- de costos va a contar también las revisiones. Si eso llega a estorbar, la
-- salida es acotar el trigger con `after update of <columnas de costo>` — no se
-- hizo aquí porque tocar un trigger vivo es más riesgo que el que quita.
--
-- POR QUÉ `revisado_por` NO LLEVA VALOR POR OMISIÓN
-- --------------------------------------------------
-- Sería el patrón de 0029 (`ops.process_log.actor`), pero ahí cada fila ES un
-- evento y firmarlas todas es correcto. Aquí NO: un `insert` de costeo crea una
-- fila que nadie revisó todavía, y un default la firmaría igual. La marca la
-- pone SOLO quien revisa, en el `update` de abajo.
--
-- El valor sale del mismo cable de la v0.233.0 que ya alimenta a
-- `cost_history`: `supabase_db.get_cursor` deja `app.usuario` con la persona
-- que pidió la operación (`core/actor.py`), y `costing_mirror._atribuir` la
-- respeta por encima de la etiqueta del proceso. No hace falta tocar Python
-- para que esto quede firmado.
--
-- QUÉ **NO** HACE
-- ---------------
-- No cambia ningún costo, ninguna fórmula y ningún precio. No borra nada. Dos
-- columnas nuevas, nulas en las 15,395 filas existentes — que es la verdad:
-- ninguna ha sido revisada bajo este criterio.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- ───────────────────────────────────────────────────────────────────────────
-- A · Las dos columnas
-- ───────────────────────────────────────────────────────────────────────────
alter table costing.costos_validados
  add column if not exists revisado_at  timestamptz;

alter table costing.costos_validados
  add column if not exists revisado_por text;

comment on column costing.costos_validados.revisado_at is
  'Cuándo se verificó este costeo contra el packing list del proveedor. '
  'NULO = todavía no se revisa. Para saber si la fila se movió después de la '
  'revisión: updated_at > revisado_at.';

comment on column costing.costos_validados.revisado_por is
  'Quién marcó la revisión. Lo escribe el update que marca, leyendo el ajuste '
  'app.usuario que deja core/actor.py vía supabase_db.get_cursor. Sin valor '
  'por omisión a propósito: un insert de costeo NO es una revisión.';

-- ───────────────────────────────────────────────────────────────────────────
-- B · Índice para el filtro que se va a usar todo el día: "qué falta"
-- ───────────────────────────────────────────────────────────────────────────
-- Parcial: solo indexa lo pendiente. Hoy son las 15,395 filas, pero encoge
-- solo conforme se avanza, que es justo al revés de un índice completo.
create index if not exists idx_costos_validados_sin_revisar
  on costing.costos_validados (sku)
  where revisado_at is null;

commit;

-- ═══════════════════════════════════════════════════════════════════════════
-- CÓMO SE MARCA (lo hace el endpoint, aquí queda de referencia)
-- ═══════════════════════════════════════════════════════════════════════════
--   update costing.costos_validados
--      set revisado_at  = now(),
--          revisado_por = nullif(current_setting('app.usuario', true), '')
--    where sku = %(sku)s;
--
-- Y para quitar la marca:
--   update costing.costos_validados
--      set revisado_at = null, revisado_por = null
--    where sku = %(sku)s;
--
-- REVERSIÓN COMPLETA
--   drop index if exists costing.idx_costos_validados_sin_revisar;
--   alter table costing.costos_validados drop column if exists revisado_por;
--   alter table costing.costos_validados drop column if exists revisado_at;
-- ═══════════════════════════════════════════════════════════════════════════
