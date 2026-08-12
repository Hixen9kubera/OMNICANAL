-- ═══════════════════════════════════════════════════════════════════════════
-- 0018 — ENRICH: devolver `visitas_30d` a market_search_results.
--
-- LA 0017 SE LA LLEVÓ POR UNA LECTURA MAL HECHA. Se retiró con el criterio de
-- "columna casi vacía y sin lectores": 4 filas llenas de 1,816. El dato de
-- llenado era correcto; la conclusión no.
--
-- El pipeline SÍ pide esas visitas. `scripts/competencia_buscar_apify.py:121`
-- llama a `enriquecer_visitas(filas)` sobre cada resultado antes de guardarlo —
-- una llamada a `/visits` por fila, gratis pero no instantánea. Estaba vacía
-- porque el subidor viejo (`competencia_subir.py`, ya retirado) no incluía la
-- columna en su lista, no porque nadie la quisiera. Al quitarla, el enriquecido
-- se siguió pagando en tiempo y el resultado se tiraba en silencio.
--
-- Y es la columna que faltaba en el panel: el bloque de BÚSQUEDA GENERAL muestra
-- los diez resultados con su precio, pero sin visitas no se puede decir cuánto
-- tráfico se lleva cada posición — que es justo lo que hace accionable la
-- comparación contra la competencia directa.
--
-- Se agrega nullable y se rellena aparte con
-- `scripts/competencia_revisitas_serp.py` (gratis, API de ML).
-- ═══════════════════════════════════════════════════════════════════════════

alter table enrich.market_search_results
  add column if not exists visitas_30d int;

comment on column enrich.market_search_results.visitas_30d is
  'Visitas de 30 días del item (GET /items/{id}/visits/time_window). Gratis por '
  'API. La llena la captura al momento de guardar el resultado; para filas '
  'viejas, scripts/competencia_revisitas_serp.py.';

-- El panel ordena por posición y lee las visitas de las primeras filas: el
-- índice parcial evita recorrer las 2,900 para las que sí la tienen.
create index if not exists idx_market_search_results_visitas
  on enrich.market_search_results (termino_id, posicion)
  where visitas_30d is not null;

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN
--
--   select count(*) filas, count(visitas_30d) con_visitas
--     from enrich.market_search_results;
--
--   -- tras el relleno, las primeras posiciones deben traerlas casi todas:
--   select posicion, count(*) n, count(visitas_30d) v
--     from enrich.market_search_results where posicion <= 10
--    group by 1 order by 1;
-- ═══════════════════════════════════════════════════════════════════════════
