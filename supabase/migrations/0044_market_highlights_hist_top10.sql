-- ═══════════════════════════════════════════════════════════════════════════
-- 0044 — ENRICH: la bitácora pasa de top 5 a top 10, y las columnas dejan de
--        llevar el número en el nombre.
--
-- ── POR QUÉ 10 Y NO 5 ──────────────────────────────────────────────────────
-- No es "por tener más": es que **5 no medía lo mismo que el aviso que ya está
-- en pantalla**.
--
-- `competencia_highlights.TOPE_HUELLA` vale **10** desde que existe, con su
-- propio comentario: *"la huella mira el top 10: abajo el orden es ruido"*. Esa
-- huella es la que mueve `market_highlights.cambio_en`, y `cambio_en` es la que
-- pinta la pastilla **"ML ya se movió"** en el tab.
--
-- Con la bitácora guardando 5, se habría medido durante dos semanas una ventana
-- DISTINTA de la que dispara el aviso: un movimiento en la posición 7 habría
-- prendido la pastilla y no habría aparecido en la serie. Dos números que
-- contestan la misma pregunta y no coinciden — exactamente lo que esta sesión
-- estuvo desenredando todo el día.
--
-- Ahora el script guarda `entradas[:TOPE_HUELLA]`: **la misma constante**, así
-- que no se pueden volver a separar sin que alguien lo decida.
--
-- ── EL COSTO ───────────────────────────────────────────────────────────────
-- ~74 bytes por fila → ~148. Medido sobre Licuadoras: los 5 ids ocupaban 74
-- bytes, los 10 ocupan 148. Para 947 categorías son **~140 KB/día en vez de
-- ~70 KB** — sigue siendo nada contra los 280 MB/año de la foto completa que la
-- 0041 rechazó, y contra los ~50 MB/año de esta bitácora.
--
-- ── Y POR QUÉ SE RENOMBRAN LAS COLUMNAS ────────────────────────────────────
-- `top5` guardando diez entradas es una columna que miente por su nombre, y la
-- próxima persona que la lea va a creerle al nombre antes que al comentario.
-- Pasan a `top` y `huella_top`: cuántas hay se sabe con
-- `jsonb_array_length(top)`, que no puede desfasarse del contenido.
--
-- SEGURO: la tabla se creó hoy (0043) y está VACÍA en producción — no hay dato
-- que migrar. Revertir: renombrar de vuelta y volver a `[:5]` en el script.
-- ═══════════════════════════════════════════════════════════════════════════

alter table enrich.market_highlights_hist
  rename column top5 to top;

alter table enrich.market_highlights_hist
  rename column huella5 to huella_top;

comment on table enrich.market_highlights_hist is
  'Bitácora DIARIA del top de cada categoría: sólo los ids en orden, sin ficha. '
  'La ventana es `competencia_highlights.TOPE_HUELLA` (10), LA MISMA que usa la '
  'huella de market_highlights — si midieran ventanas distintas, la serie y la '
  'pastilla "ML ya se movió" se contradirían. ~140 KB/día. No la lee ninguna '
  'pantalla; se apaga cuando la decisión de cadencia esté tomada.';
comment on column enrich.market_highlights_hist.top is
  'Lista ORDENADA de ids del top (10 hoy). Sin título, precio ni foto: eso vive '
  'en market_bestsellers. Cuántos hay: jsonb_array_length(top).';
comment on column enrich.market_highlights_hist.huella_top is
  'sha1 corto de `top`. Dos días con la misma huella son dos días sin movimiento.';

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN
--
--   select column_name from information_schema.columns
--    where table_schema='enrich' and table_name='market_highlights_hist'
--    order by ordinal_position;
--   -- espera: canal, categoria_id, dia, top, huella_top, n, capturado_en
--
--   -- Después de la primera corrida: que guarde 10, no 5
--   select categoria_id, jsonb_array_length(top) cuantos, n
--     from enrich.market_highlights_hist order by dia desc limit 5;
-- ═══════════════════════════════════════════════════════════════════════════
