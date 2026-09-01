-- ═══════════════════════════════════════════════════════════════════════════
-- 0038 — ENRICH: las UNIDADES del panel de Competencia salen de nuestros
--        pedidos, en vivo, y dejan de salir de una foto que estaba mal.
--
-- ── EL BUG ─────────────────────────────────────────────────────────────────
-- `competencia_captura._unidades_por_item` tiene dos fuentes y lo dice en su
-- propio docstring: **primero `channel.order_items`** —nuestros pedidos, exacto
-- y gratis— y, sólo si falta `SUPABASE_DB_URL`, un barrido de la API de ML que
-- existe "para cuando se corre en local".
--
-- Medido el 31-ago-2026: **las 3,118 filas de `market_listing_metrics` tienen
-- `fuente_unidades = 'ml_api'`. NI UNA usó nuestros pedidos.** O sea que toda la
-- medición guardada salió del respaldo, y el respaldo subcuenta.
--
-- El caso que lo destapó, TEC-2162-NEG (Licuadoras), con los MISMOS item_id que
-- la medición conoce y en la MISMA ventana que midió:
--
--     guardado:  BEKURA 23  ·  SANCORFASHION 26   =  49 unidades
--     real:      BEKURA 307 ·  SANCORFASHION 224  = 531 unidades
--
-- No es que el dato esté viejo: nació mal, y por once veces. Y no es un caso
-- aislado — de 545 SKUs comparables, **124 aparecen con menos de la mitad de lo
-- que vendieron y 60 aparecen en cero habiendo vendido**.
--
-- ── EL ARREGLO ─────────────────────────────────────────────────────────────
-- La vista deja de leer `market_listing_metrics.units_30d` y agrega
-- `channel.sales_daily` de los últimos 30 días, cruzando por
-- `(item_id, cuenta)` — la misma llave que usa `_unidades_por_item`, verificada:
-- cruza 972 filas con o sin la cuenta, y las únicas dos cuentas que aparecen en
-- ventas son BEKURA y SANCORFASHION.
--
-- `channel.sales_daily` (migración 0005) ya excluye `cancelled/invalid/canceled`
-- y ya agrupa en hora de México. Y se alimenta del webhook de ML, que escribe en
-- segundos: **el número queda siempre al día, sin cron y sin costo.**
--
-- Impacto medido antes de aplicar: de 4,803 filas, **2,587 cambian**; el total
-- pasa de 13,553 a 21,139 unidades; **460 filas que decían cero sí vendieron**.
-- Las 189 que decían venta y ahora quedan en cero no son un error: son ventas
-- que la foto vieja alcanzó y que ya salieron de la ventana de 30 días.
--
-- ── DOS CAMBIOS DE SIGNIFICADO, A PROPÓSITO ────────────────────────────────
-- 1. **`unidades_30d` ya no es NULL nunca; ahora es 0 de verdad.** El módulo
--    tiene la regla "None, NO 0: no medido ≠ sin ventas", y era correcta cuando
--    las unidades salían de una medición que podía no haber corrido. Ya no:
--    `sales_daily` cubre TODAS las ventas de ML, así que la ausencia de fila
--    significa "vendió cero", no "no sé". Ojo, **esto no aplica a las visitas**,
--    que siguen en NULL cuando nadie midió — ahí la regla sigue viva.
-- 2. **`fuente_unidades` pasa a `'pedidos'`** en todas las filas. Antes decía
--    `'ml_api'` y, tras este cambio, habría quedado mintiendo. Un número sin
--    saber de dónde viene no es un dato.
--
-- La foto histórica NO se toca: `market_listing_metrics.units_30d` sigue ahí con
-- lo que midió cada mes. Simplemente el panel ya no la lee.
--
-- ── COSTO ──────────────────────────────────────────────────────────────────
-- La vista pasa de **486 ms a 588 ms** para sus 4,803 filas (mejor de 3
-- corridas). El agregado se calcula una sola vez por consulta.
--
-- Delta sobre la 0037, que resolvió el corte de mes: el `distinct on` y la rama
-- del espejo se conservan tal cual. REVERSIBLE: es una vista; la 0037 completa
-- está en `supabase/migrations/0037_market_publicaciones_v_ultimo_periodo.sql`.
-- ═══════════════════════════════════════════════════════════════════════════

create or replace view enrich.market_publicaciones_v as
with ventas as (
  -- Nuestras ventas reales de 30 días, por publicación. Una sola pasada.
  select item_id, cuenta, sum(units_sold)::int as unidades
    from channel.sales_daily
   where canal = 'mercado_libre'
     and date > (now() at time zone 'America/Mexico_City')::date - 30
   group by 1, 2
)
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
       coalesce(v.unidades, 0)                  as unidades_30d,
       'pedidos'::text                          as fuente_unidades,
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
              mm.periodo
         from enrich.market_listing_metrics mm
         left join channel.listings l
                on l.sku = mm.sku and l.store_name = mm.cuenta and l.canal = mm.canal
        -- 0037: medición primero, mes reciente después.
        order by mm.sku, mm.canal, mm.cuenta,
                 (mm.title is not null
                  or mm.visits_30d is not null
                  or mm.units_30d is not null) desc,
                 mm.periodo desc
       ) m
  left join ventas v
         on v.item_id = m.ml_item_id and v.cuenta = m.cuenta
union all
-- ── Rama del espejo (0023): publicaciones vivas que nunca se midieron ───────
-- El título y las visitas siguen en NULL —eso sólo lo trae una medición—, pero
-- las UNIDADES ya no: se saben, y son las de abajo.
select l.sku::citext,
       a.legacy_code,
       l.canal,
       l.listing_id,
       null::text,                              -- título: sólo lo trae la medición
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
       coalesce(v.unidades, 0),
       'pedidos'::text,
       null::date                               -- sin periodo: no es una foto
  from channel.listings l
  join core.accounts a on a.id = l.account_id
  left join ventas v
         on v.item_id = l.listing_id and v.cuenta = a.legacy_code
 where l.canal = 'mercado_libre'
   and lower(l.situacion) in ('active', 'paused')
   and nullif(l.listing_id, '') is not null
   and not exists (select 1 from enrich.market_listing_metrics mm2
                    where mm2.sku = l.sku and mm2.canal = l.canal
                      and mm2.cuenta = a.legacy_code);

comment on view enrich.market_publicaciones_v is
  'Publicaciones de ML del módulo Competencia. Las UNIDADES salen de '
  'channel.sales_daily (nuestros pedidos, en vivo, sin cancelados) y ya no de la '
  'foto de market_listing_metrics, que se había escrito con el barrido de la API '
  'de ML y subcontaba. Las VISITAS siguen viniendo de la medición, y siguen en '
  'NULL cuando nadie midió. Una fila por (sku, canal, cuenta): la última medición.';

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN (correr DESPUÉS de aplicar)
--
--   -- 1) El caso que lo destapó: debe dar ~427 entre las dos tiendas, no 49.
--   select sku, cuenta, ml_item_id, unidades_30d, fuente_unidades
--     from enrich.market_publicaciones_v where sku::text = 'TEC-2162-NEG';
--
--   -- 2) Cero llaves duplicadas (la garantía de la 0037 sigue en pie).
--   select sku, canal, cuenta, count(*) from enrich.market_publicaciones_v
--    group by 1,2,3 having count(*) > 1;
--
--   -- 3) El total de filas no se movió y las unidades suben a ~21,139.
--   select count(*) filas, sum(unidades_30d) unidades,
--          count(*) filter (where visitas_30d is null) sin_medir
--     from enrich.market_publicaciones_v;
--
--   -- 4) La foto histórica sigue intacta en la tabla base.
--   select periodo, count(*), sum(units_30d) from enrich.market_listing_metrics
--    group by 1 order by 1;
-- ═══════════════════════════════════════════════════════════════════════════
