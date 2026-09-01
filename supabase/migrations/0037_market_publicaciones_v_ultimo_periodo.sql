-- ═══════════════════════════════════════════════════════════════════════════
-- 0037 — ENRICH: market_publicaciones_v devuelve UNA fila por (sku, canal,
--        cuenta) — la última medición — en vez de una por cada mes guardado.
--
-- Es un delta sobre la 0023 (`0023_market_vivas_sin_cron.sql`), que fue la que
-- le puso el UNION ALL contra `channel.listings`. La rama del espejo se copia
-- de ahí SIN UN SOLO CAMBIO; lo único que cambia es la rama medida.
--
-- ── EL BUG ─────────────────────────────────────────────────────────────────
-- `enrich.market_listing_metrics` tiene PK **(sku, canal, cuenta, periodo)** y
-- `periodo = date_trunc('month', now())` (competencia_supabase.
-- guardar_publicaciones). La vista NO filtra `periodo`: devuelve TODAS las
-- filas de la tabla.
--
-- Mientras sólo hubo un mes guardado no se notó. Medido el 31-ago-2026: 3,118
-- filas, un único periodo `2026-08-01`, y la vista entera en 4,787 filas
-- (3,118 medidas + 1,669 del espejo).
--
-- El día 1 de cada mes la primera escritura crea la fila del mes NUEVO con
-- `title`, `visits_30d` y `units_30d` en NULL — el COALESCE del upsert protege
-- DENTRO de una fila, no ENTRE meses. A partir de ahí la vista devuelve **dos
-- filas por (sku, cuenta)**: el mes viejo completo y el nuevo vacío.
--
-- Simulado contra producción con un septiembre ficticio, sin escribir nada: la
-- rama medida pasa de 3,118 a 6,236 filas — exactamente el doble, con las 3,118
-- llaves duplicadas. Y eso se propaga:
--
--   · `competencia_supabase.listar_skus` arma el item de referencia con la
--     primera fila BEKURA que aparezca bajo `ORDER BY sku, cuenta`, que NO
--     desempata por periodo: queda a merced del plan de ejecución.
--   · `competencia_store.vista()` construye `f["tiendas"]` con las dos filas →
--     cada tienda se pinta dos veces en el panel.
--   · `precio_ref = min(precios)` toma el más bajo entre los dos meses, y de ahí
--     sale `brecha`, que es la columna que manda en el tab.
--
-- ── EL ARREGLO ─────────────────────────────────────────────────────────────
-- `distinct on (sku, canal, cuenta)` sobre la rama medida, con dos criterios de
-- orden, y EL ORDEN ES EL ARREGLO:
--
--   1. **primero las filas que TIENEN medición** (`title`, `visits_30d` o
--      `units_30d` no nulos). Sin este criterio, un `distinct on … order by
--      periodo desc` a secas —la corrección obvia— elegiría la fila vacía del
--      mes nuevo y dejaría el panel entero en "—" hasta que una captura
--      completa terminara. Cambiar duplicados por huecos no es arreglar.
--   2. luego el `periodo` más reciente, para que en cuanto el mes nuevo tenga
--      medición real, ésa gane.
--
-- Verificado en la simulación: las 3,118 llaves quedan en 3,118 filas y las
-- 3,118 conservan su medición.
--
-- ── HOY NO CAMBIA NADA ─────────────────────────────────────────────────────
-- La rama medida ya devuelve 3,118 filas para 3,118 llaves, así que el
-- `distinct on` es un no-op mientras exista un solo periodo. Comprobado contra
-- producción: la vista nueva y la vieja devuelven EXACTAMENTE el mismo
-- conjunto (`except` en los dos sentidos: 0 y 0). Es una vacuna, no una cirugía.
--
-- OJO CON EL JOIN: `channel.listings` tiene 307 grupos duplicados por
-- (sku, store_name, canal), pero ninguno cae en esta rama hoy. Si algún día cae,
-- el mismo `distinct on` lo absorbe — y eso es correcto: esas filas extra sólo
-- difieren en el `price`/`url` de respaldo; el `ml_item_id` sale de
-- `market_listing_metrics` y es el mismo.
--
-- ── LO QUE ESTA MIGRACIÓN **NO** HACE ──────────────────────────────────────
-- No toca la PK de `market_listing_metrics` ni el upsert: la foto mensual sigue
-- siendo mensual y el histórico por periodo queda intacto. Sólo cambia lo que el
-- PANEL ve. La alternativa —arrastrar los valores del mes anterior en la primera
-- escritura del mes— resuelve lo mismo pero escribe datos viejos con fecha
-- nueva, y eso sí falsea la foto.
--
-- REVERSIBLE: es una vista. La definición previa quedó capturada de producción
-- justo antes de aplicar, en
-- `supabase/propuestas/0037_reversa_market_publicaciones_v.sql`, y basta correr
-- ese archivo para volver.
--
-- APLICADA A PRODUCCIÓN el 31-ago-2026, por el puerto 5432 (el pooler en modo
-- transacción no sostiene DDL) y con rollback automático si fallaba cualquiera
-- de las cuatro verificaciones de abajo. Resultado: 4,787 filas, 3,118 con
-- medición, 0 llaves duplicadas, tabla base intacta.
-- ═══════════════════════════════════════════════════════════════════════════

create or replace view enrich.market_publicaciones_v as
select m.sku,
       m.cuenta,
       m.canal,
       m.ml_item_id,
       m.titulo,
       m.estado,
       m.precio,
       m.precio_lista,
       m.url,
       m.visitas_30d,
       m.unidades_30d,
       m.fuente_unidades,
       m.periodo
  from (
       select distinct on (mm.sku, mm.canal, mm.cuenta)
              mm.sku,
              mm.cuenta,
              mm.canal,
              mm.listing_id                     as ml_item_id,
              mm.title                          as titulo,
              mm.estado,
              coalesce(mm.sale_price, l.price)  as precio,
              coalesce(mm.list_price, l.price)  as precio_lista,
              coalesce(
                case when mm.canal = 'mercado_libre'
                      and mm.listing_id ~ '^MLM[0-9]{9,12}$'
                     then 'https://articulo.mercadolibre.com.mx/MLM-'
                          || substring(mm.listing_id from 4) || '-_JM'
                end, l.url)                     as url,
              mm.visits_30d                     as visitas_30d,
              mm.units_30d                      as unidades_30d,
              mm.fuente_unidades,
              mm.periodo
         from enrich.market_listing_metrics mm
         left join channel.listings l
                on l.sku = mm.sku and l.store_name = mm.cuenta and l.canal = mm.canal
        -- El orden ES el arreglo: medición primero, mes reciente después.
        order by mm.sku, mm.canal, mm.cuenta,
                 (mm.title is not null
                  or mm.visits_30d is not null
                  or mm.units_30d is not null) desc,
                 mm.periodo desc
       ) m
union all
-- ── Rama del espejo: IDÉNTICA a la de la 0023 ──────────────────────────────
select l.sku::citext,
       a.legacy_code,
       l.canal,
       l.listing_id,
       null::text,                              -- título: solo lo trae la medición
       lower(l.situacion),
       l.price,
       l.price_base,
       coalesce(
         case when l.canal = 'mercado_libre'
               and l.listing_id ~ '^MLM[0-9]{9,12}$'
              then 'https://articulo.mercadolibre.com.mx/MLM-'
                   || substring(l.listing_id from 4) || '-_JM'
         end, l.url),
       null::int,                               -- visitas: no medido ≠ 0
       null::int,
       null::text,
       null::date                               -- sin periodo: no es una foto
  from channel.listings l
  join core.accounts a on a.id = l.account_id
 where l.canal = 'mercado_libre'
   and lower(l.situacion) in ('active', 'paused')
   and nullif(l.listing_id, '') is not null
   -- Si ese par (sku, cuenta) tiene CUALQUIER medición, gana la medición:
   -- metrics es la foto del mes y esta rama es sólo el "mientras tanto". El
   -- `not exists` no mira `periodo` a propósito: basta que exista una foto de
   -- cualquier mes para que la rama de arriba se haga cargo de esa llave.
   and not exists (select 1 from enrich.market_listing_metrics mm2
                    where mm2.sku = l.sku and mm2.canal = l.canal
                      and mm2.cuenta = a.legacy_code);

comment on view enrich.market_publicaciones_v is
  'Publicaciones de ML del módulo Competencia: la ÚLTIMA medición por '
  '(sku, canal, cuenta) — market_listing_metrics guarda una foto por mes y sin '
  'el distinct on la vista devolvía una fila por cada mes desde el día 1 — '
  'unida al espejo de channel.listings para lo que nunca se midió (0023).';

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN (correr DESPUÉS de aplicar)
--
--   -- 1) Cero llaves duplicadas. Debe devolver 0 filas, hoy y el día 1.
--   select sku, canal, cuenta, count(*)
--     from enrich.market_publicaciones_v
--    group by 1,2,3 having count(*) > 1;
--
--   -- 2) El total no se movió: 4,787 el 31-ago (3,118 medidas + 1,669 espejo).
--   select count(*) total,
--          count(*) filter (where visitas_30d is not null) medidas,
--          count(*) filter (where visitas_30d is null)     espejo
--     from enrich.market_publicaciones_v;
--
--   -- 3) Ninguna medición se perdió: las 3,118 conservan título y visitas.
--   select count(*) from enrich.market_publicaciones_v where titulo is not null;
--
--   -- 4) El histórico por periodo sigue intacto en la tabla base.
--   select periodo, count(*) from enrich.market_listing_metrics group by 1 order by 1;
-- ═══════════════════════════════════════════════════════════════════════════
