-- ═══════════════════════════════════════════════════════════════════════════
-- 0043 — ENRICH: la bitácora del TOP 5, para medir cuánto rota de verdad.
--
-- ── LA PREGUNTA QUE NO SE PUEDE CONTESTAR HOY ──────────────────────────────
-- "¿Cada cuánto conviene refrescar el ranking?" depende de cuánto se mueve, y
-- eso **no lo sabemos**. `enrich.market_highlights` (0041) guarda UNA fila por
-- categoría: dice CUÁNDO cambió por última vez, no cuántas veces ni cuánto.
--
-- El 1-sep-2026 el sondeo reportó 583 categorías movidas en su primera corrida
-- y 137 en la segunda del mismo día. Ese salto es la prueba de que sin serie no
-- se puede distinguir rotación real de ruido de arranque — y sobre esa duda
-- está apoyada una decisión de gasto ($1.37 al mes contra $41).
--
-- ── POR QUÉ ESTA TABLA SÍ Y LA DE LA 0041 NO ───────────────────────────────
-- La 0041 rechazó guardar histórico y tenía razón con lo que se le pedía: la
-- foto COMPLETA de las 1,129 categorías pesa ~785 KB al día — **280 MB al año**
-- para contestar preguntas que nadie hacía.
--
-- Ésta guarda **sólo el top 5 y sólo los ids**, sin ficha:
--
--   785 KB/día (20 entradas con todo)  →  ~140 KB/día (5 ids)
--   280 MB/año                         →  ~50 MB/año
--
-- Y contesta exactamente lo que se preguntó: cuántas posiciones se mueven al
-- día, si el #1 aguanta, y en qué categorías.
--
-- ── QUÉ SE GUARDA, Y QUÉ NO ────────────────────────────────────────────────
-- `top5` es la lista ORDENADA de ids, nada más: ["MLM48477205","MLM6088703",…].
-- No lleva título, precio ni foto — eso ya vive en `market_bestsellers` y
-- duplicarlo aquí sería pagar el mismo dato dos veces.
--
-- `huella5` es el sha1 corto de esa lista. Dos días con la misma huella son dos
-- días sin movimiento: comparar huellas es más barato que comparar arreglos.
--
-- ── UNA FILA POR DÍA, SIEMPRE — no sólo cuando cambia ──────────────────────
-- Escribir sólo los cambios haría el análisis ambiguo: un hueco significaría
-- "no cambió" o "no se sondeó", y son cosas distintas. Con una fila por día la
-- ausencia es informativa por sí sola.
--
-- ── ESTO ES UNA MEDICIÓN, NO UNA FUNCIÓN ───────────────────────────────────
-- Existe para decidir la cadencia. Cuando la decisión esté tomada, o se apaga
-- (dejar de escribirla; los datos quedan) o se recorta a las categorías que de
-- verdad importan. No la lee ninguna pantalla.
--
-- ADITIVA: no toca ningún objeto existente. Revertir es `drop table`.
-- ═══════════════════════════════════════════════════════════════════════════

create table if not exists enrich.market_highlights_hist (
  canal        text        not null references core.channels(id),
  categoria_id text        not null,
  -- El DÍA, no el instante: el sondeo corre una vez al día y dos filas del
  -- mismo día serían la misma medición contada dos veces.
  dia          date        not null default (now() at time zone 'America/Mexico_City')::date,
  -- La lista ORDENADA de ids del top 5. Sin ficha, a propósito.
  top5         jsonb       not null default '[]'::jsonb,
  -- sha1 corto de `top5`. Misma huella dos días = no se movió.
  huella5      text,
  -- Cuántas entradas publica ML en total (no sólo las 5 guardadas).
  n            int         not null default 0,
  capturado_en timestamptz not null default now(),
  primary key (canal, categoria_id, dia)
);

-- Para la consulta que importa: "todas las filas de esta categoría, en orden".
create index if not exists idx_mh_hist_cat_dia
  on enrich.market_highlights_hist (canal, categoria_id, dia desc);

-- Para "qué se movió el día X" sin recorrer la tabla entera.
create index if not exists idx_mh_hist_dia
  on enrich.market_highlights_hist (dia desc);

comment on table enrich.market_highlights_hist is
  'Bitácora DIARIA del top 5 de cada categoría: sólo los ids en orden, sin '
  'ficha. Existe para MEDIR cuánto rota el ranking y decidir cada cuánto pagar '
  'por raspar. ~140 KB/día. No la lee ninguna pantalla; se apaga cuando la '
  'decisión de cadencia esté tomada.';
comment on column enrich.market_highlights_hist.top5 is
  'Lista ORDENADA de ids del top 5. Sin título, precio ni foto: eso vive en '
  'market_bestsellers y duplicarlo sería pagar el mismo dato dos veces.';
comment on column enrich.market_highlights_hist.huella5 is
  'sha1 corto de top5. Dos días con la misma huella son dos días sin movimiento.';
comment on column enrich.market_highlights_hist.dia is
  'El DÍA en hora de México, no el instante: el sondeo corre una vez al día.';

-- ═══════════════════════════════════════════════════════════════════════════
-- CÓMO SE LEE (correr después de unos días)
--
--   -- 1) ¿Cuánto se mueve el top 5, día con día?
--   with pares as (
--     select categoria_id, dia, huella5,
--            lag(huella5) over (partition by categoria_id order by dia) as ayer
--       from enrich.market_highlights_hist where canal = 'mercado_libre'
--   )
--   select dia,
--          count(*)                                        categorias,
--          count(*) filter (where ayer is not null
--                             and huella5 is distinct from ayer) se_movieron
--     from pares where ayer is not null
--    group by dia order by dia;
--
--   -- 2) ¿El #1 aguanta? (lo que decide si el top sirve de referencia)
--   with p as (
--     select categoria_id, dia, top5->>0 as primero,
--            lag(top5->>0) over (partition by categoria_id order by dia) as ayer
--       from enrich.market_highlights_hist where canal = 'mercado_libre'
--   )
--   select dia, count(*) filter (where ayer is not null and primero is distinct from ayer) cambio_el_1,
--          count(*) filter (where ayer is not null) comparables
--     from p group by dia order by dia;
--
--   -- 3) Las categorías MÁS volátiles: donde el mensual duele más
--   with p as (
--     select categoria_id, huella5,
--            lag(huella5) over (partition by categoria_id order by dia) as ayer
--       from enrich.market_highlights_hist where canal = 'mercado_libre'
--   )
--   select h.categoria_id, v.categoria_nombre, v.pesos_30d,
--          count(*) filter (where ayer is not null and huella5 is distinct from ayer) dias_que_cambio,
--          count(*) filter (where ayer is not null) dias_medidos
--     from p h
--     left join enrich.market_categoria_prioridad_v v on v.categoria_id = h.categoria_id
--    group by 1,2,3 having count(*) filter (where ayer is not null) >= 3
--    order by dias_que_cambio desc, v.pesos_30d desc nulls last limit 20;
--
--   -- 4) Cuánto pesa la bitácora
--   select count(*) filas, min(dia), max(dia),
--          pg_size_pretty(pg_total_relation_size('enrich.market_highlights_hist')) peso
--     from enrich.market_highlights_hist;
-- ═══════════════════════════════════════════════════════════════════════════
