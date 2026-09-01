-- ═══════════════════════════════════════════════════════════════════════════
-- 0040 — ENRICH: una fila por subcategoría ACTIVA, con lo que hace falta para
--        ordenarlas por prioridad. De aquí salen el top 5 y el bottom 5.
--
-- ── PARA QUÉ ───────────────────────────────────────────────────────────────
-- El tab de Competencia ordena las subcategorías por mediana de precio, por
-- visitas del mercado y por volumen del nicho. **Nunca por lo que nosotros
-- vendemos ahí**, que es justo la pregunta de negocio: ¿dónde vale la pena
-- mirar a la competencia?
--
-- Esta vista contesta eso sin script, sin cron y sin gasto: las dos listas de
-- cinco son un `order by … limit 5` sobre ella.
--
-- ── CINCO DECISIONES, Y SUS PORQUÉS ────────────────────────────────────────
--
-- 1. **Se agrupa por `categoria_id`, NUNCA por nombre.** Hay categorías
--    distintas que se llaman igual — dos "Soportes" — y agrupar por nombre las
--    fusiona y cambia el orden del top.
--
-- 2. **Las ventas salen de `channel.sales_daily`** (migración 0005), que ya
--    excluye `cancelled/invalid/canceled` y ya agrupa en hora de México. Se
--    alimenta del webhook de ML, que escribe en segundos: el número está al día
--    siempre. NO se usa `market_listing_metrics.units_30d`: esa foto se escribió
--    con el barrido de la API y subcontaba (ver 0038).
--
-- 3. **La categoría sale de `enrich.market_skus_v`**, que es donde se aplica el
--    `coalesce(product_category.category_id, cfg.categoria_id_real)` — la regla
--    de la casa de que la elección del PANEL manda. Reconstruir ese cruce a mano
--    pierde los SKUs cuya categoría medida difiere de la elegida.
--
-- 4. **"ACTIVO" = tiene publicación viva en ML** (`active` o `paused`, con
--    `listing_id`), NO el flag humano `market_sku_config.activo`. Son cosas
--    distintas: ese flag lo mueve una persona desde el panel. Medido el
--    1-sep-2026: 1,129 categorías activas de 1,212 en el censo — filtrar por
--    activo sólo quita el 7%, así que no es la palanca del ahorro; la palanca es
--    priorizar, que es lo que hace esta vista.
--
-- 5. ⚠️ **`vivas` agrega UNA fila por SKU, no una por publicación.** Si se deja
--    una por publicación, el SKU que está en las dos tiendas se duplica en el
--    join y **las ventas salen al doble**. Pasó en el primer borrador de esta
--    misma vista: Licuadoras dio 954 unidades cuando son 477. El conteo de
--    publicaciones viaja como columna (`publicaciones`), no como filas.
--
-- ── LAS DOS MEDIDAS DE DEMANDA, A PROPÓSITO ────────────────────────────────
-- `visitas_30d` son NUESTRAS: frescas (las refresca `competencia_visitas.py` a
-- diario) y con cobertura completa de lo medido. `visitas_mercado` es la suma
-- del top de la competencia: mide demanda con independencia de qué tan mal lo
-- estemos haciendo, pero sólo existe donde hay ranking capturado (638 de 1,129).
-- Se exponen las dos y decide quien lee, en vez de que la vista elija por él.
--
-- ── LO QUE NO TRAE, Y CUÁNDO ───────────────────────────────────────────────
-- Falta `tiene_ranking_ml` —si Mercado Libre publica lista de más vendidos de
-- esa categoría—, que es el filtro que evita pagar raspados imposibles. La da
-- `/highlights` gratis y llega con el sondeo diario. No se agrega vacía: una
-- columna que miente es peor que una que falta.
--
-- ── COSTO ──────────────────────────────────────────────────────────────────
-- 1.2 s para las 1,129 filas (mejor de 3 corridas). Es una vista normal, así que
-- siempre está al día. Si algún día estorba se materializa, pero eso pide un
-- cron de refresco y hoy no hace falta.
--
-- ADITIVA: no toca ni un objeto existente. Revertir es `drop view`.
-- ═══════════════════════════════════════════════════════════════════════════

create or replace view enrich.market_categoria_prioridad_v as
with vivas as (
  -- UNA fila por SKU. Ver decisión 5: por publicación duplicaría las ventas.
  select l.sku, count(*)::int as publicaciones
    from channel.listings l
   where l.canal = 'mercado_libre'
     and lower(l.situacion) in ('active', 'paused')
     and nullif(l.listing_id, '') is not null
   group by 1
),
ventas as (
  select s.sku, sum(s.units_sold)::int as unidades, sum(s.revenue) as pesos
    from channel.sales_daily s
   where s.canal = 'mercado_libre'
     and s.date > (now() at time zone 'America/Mexico_City')::date - 30
   group by 1
),
visitas as (
  select p.sku, sum(p.visitas_30d)::int as visitas
    from enrich.market_publicaciones_v p
   where p.visitas_30d is not null
   group by 1
),
ranking as (
  select b.categoria_id,
         count(*)::int              as n_ranking,
         max(b.capturado_en)        as capturado_en,
         percentile_cont(0.5) within group (order by b.precio) as mediana,
         sum(b.visitas_30d)::bigint as visitas_mercado,
         sum(b.vendidos)::bigint    as volumen_mercado
    from enrich.market_bestsellers b
   where b.nivel = 'hoja'
   group by 1
),
por_sku as (
  select k.categoria_id, k.categoria_nombre, k.raiz_id, k.raiz_nombre, k.sku,
         vv.publicaciones,
         coalesce(vt.unidades, 0) as unidades,
         coalesce(vt.pesos, 0)    as pesos,
         coalesce(vs.visitas, 0)  as visitas
    from (select distinct sku, categoria_id, categoria_nombre, raiz_id, raiz_nombre
            from enrich.market_skus_v
           where categoria_id is not null) k
    join vivas vv        on vv.sku = k.sku
    left join ventas vt  on vt.sku = k.sku
    left join visitas vs on vs.sku = k.sku
)
select s.categoria_id,
       s.categoria_nombre,
       s.raiz_id,
       s.raiz_nombre,
       count(*)::int                          as skus_activos,
       sum(s.publicaciones)::int              as publicaciones,
       sum(s.unidades)::int                   as unidades_30d,
       round(sum(s.pesos))::int               as pesos_30d,
       sum(s.visitas)::int                    as visitas_30d,
       r.n_ranking,
       r.capturado_en::date                   as ranking_ultimo,
       (current_date - r.capturado_en::date)  as dias_sin_captura,
       round(r.mediana::numeric, 2)           as mediana_mercado,
       r.visitas_mercado,
       r.volumen_mercado
  from por_sku s
  left join ranking r on r.categoria_id = s.categoria_id
 group by s.categoria_id, s.categoria_nombre, s.raiz_id, s.raiz_nombre,
          r.n_ranking, r.capturado_en, r.mediana, r.visitas_mercado, r.volumen_mercado;

comment on view enrich.market_categoria_prioridad_v is
  'Una fila por subcategoría ACTIVA (con publicación viva en ML) para ordenarlas '
  'por prioridad. Ventas de channel.sales_daily en vivo; categoría de '
  'market_skus_v (la elección del panel manda); visitas propias y del mercado por '
  'separado. El top 5 y el bottom 5 son un order by … limit 5 sobre esta vista.';

-- ═══════════════════════════════════════════════════════════════════════════
-- CÓMO SE USA
--
--   -- Las 5 que MÁS venden
--   select categoria_nombre, unidades_30d, pesos_30d, dias_sin_captura
--     from enrich.market_categoria_prioridad_v
--    order by unidades_30d desc limit 5;
--
--   -- Las 5 que NO venden y sí tienen tráfico (nos ven y no compran)
--   select categoria_nombre, visitas_30d, skus_activos, dias_sin_captura
--     from enrich.market_categoria_prioridad_v
--    where unidades_30d = 0 and visitas_30d > 0
--    order by visitas_30d desc limit 5;
--
--   -- Variante robusta del bottom: demanda del MERCADO, no la nuestra.
--   -- Mide si hay negocio ahí sin importar qué tan mal lo estemos haciendo.
--   select categoria_nombre, visitas_mercado, visitas_30d, skus_activos
--     from enrich.market_categoria_prioridad_v
--    where unidades_30d = 0 and visitas_mercado > 0
--    order by visitas_mercado desc limit 5;
--
--   -- Lo que el dinero dice que urge capturar
--   select categoria_nombre, pesos_30d,
--          coalesce(dias_sin_captura::text, 'nunca') as ranking
--     from enrich.market_categoria_prioridad_v
--    where unidades_30d > 0 and (dias_sin_captura is null or dias_sin_captura > 14)
--    order by pesos_30d desc limit 20;
--
-- VERIFICACIÓN (correr DESPUÉS de aplicar)
--
--   -- 1) 1,129 categorías activas, 505 con venta, 638 con ranking.
--   select count(*) categorias,
--          count(*) filter (where unidades_30d > 0) con_venta,
--          count(*) filter (where n_ranking is not null) con_ranking
--     from enrich.market_categoria_prioridad_v;
--
--   -- 2) CONTROL de la trampa 5: Licuadoras debe dar 477, NO 954.
--   select categoria_nombre, skus_activos, publicaciones, unidades_30d
--     from enrich.market_categoria_prioridad_v where categoria_id = 'MLM21171';
-- ═══════════════════════════════════════════════════════════════════════════
