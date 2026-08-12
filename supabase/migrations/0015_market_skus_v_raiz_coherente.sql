-- ═══════════════════════════════════════════════════════════════════════════
-- 0015 — ENRICH: la raíz de market_skus_v sale de la MISMA categoría que la ruta,
--        y la vista expone la categoría PADRE con su id.
--
-- EL BUG. La 0013 dejó las tres columnas de categoría con orígenes distintos:
--
--     COALESCE(pc.category_id, cfg.categoria_id_real) AS categoria_id
--     COALESCE(c.name,  c2.name)                      AS categoria_nombre
--     COALESCE(c.path,  c2.path)                       AS ruta
--     c2.root_id                                       AS raiz_id      ← solo c2
--
-- `c` es la categoría del PANEL (channel.product_category, la elección humana
-- que manda al publicar, regla 2 de CLAUDE.md) y `c2` la que MIDIÓ Competencia
-- de la publicación viva (market_sku_config.categoria_id_real). Nombre y ruta
-- prefieren `c`; la raíz salía únicamente de `c2`. Cuando las dos categorías
-- pertenecen a raíces distintas, la fila mostraba el nombre y la ruta de una y
-- se agrupaba bajo la otra.
--
-- MEDIDO en producción antes del arreglo: 79 de las 1,584 filas así. Se veía en
-- el panel como subcategorías de Hogar ("Licoreras", "Veladores y Lámparas de
-- Mesa") colgadas de Deportes y Fitness. Con el arreglo, 26 SKUs cambian de
-- raíz —los 5 ajenos salen de MLM1276, que queda en 143— y las filas cuya ruta
-- concuerda con su raíz pasan de 0 a 1,567 de 1,584.
--
-- EL ARREGLO es una línea de criterio: la raíz se toma de la MISMA categoría de
-- la que ya salen el nombre y la ruta. No se decide cuál categoría manda —eso
-- ya estaba decidido por el COALESCE— solo se deja de mezclar.
--
-- LO QUE SE AGREGA. `padre_id` y `padre_nombre`: la categoría padre INMEDIATA
-- (channel.categories.parent_id, del backfill de la 0012) con su id, para poder
-- auditar a simple vista de qué cuelga cada subcategoría. Van al FINAL de la
-- lista de columnas, que es lo único que `create or replace view` permite
-- agregar sin dropear.
--
-- btrim EN LOS NOMBRES: 260 filas de channel.categories traen `root_name` con
-- espacio al final ('Deportes y Fitness '), herencia del backfill desde
-- wp_ml_categorias. Se limpia EN LA VISTA y no con un UPDATE, porque
-- channel.categories es tabla compartida del equipo y su escritor es
-- etl_channel_categories.py.
--
-- REVERSIBLE: es una vista. La definición anterior está en el commit de la 0013
-- (pg_get_viewdef) y basta un create or replace de vuelta.
--
-- LO QUE ESTA MIGRACIÓN **NO** ARREGLA — 17 filas quedan incoherentes porque el
-- conflicto está en channel.categories, no aquí: para esas categorías su propio
-- `root_id` y su propio `path` se contradicen (p. ej. root_id=Herramientas con
-- path='Construcción › Aberturas › Portones'). `root_id` viene del árbol
-- completo y fresco (wp_ml_categorias, 12,256 categorías) y `path` del snapshot
-- del ETL, así que lo más probable es que ML haya reclasificado la categoría y
-- el path esté viejo. Se deja a la vista para NO inventar el dato; hay que
-- resolverlo en el origen. Una de esas 17 es de Deportes y Fitness (root_id
-- MLM1276 con path de 'Accesorios para Vehículos').
-- ═══════════════════════════════════════════════════════════════════════════

create or replace view enrich.market_skus_v as
select cfg.sku,
       cfg.canal,
       p.name as nombre,
       coalesce(pc.category_id, cfg.categoria_id_real)      as categoria_id,
       btrim(coalesce(c.name, c2.name))                     as categoria_nombre,
       coalesce(c.path, c2.path)                            as ruta,
       -- La raíz, de la MISMA categoría que dio el nombre y la ruta.
       coalesce(c.root_id, c2.root_id)                      as raiz_id,
       btrim(coalesce(c.root_name, c2.root_name))           as raiz_nombre,
       cfg.termino_general,
       cfg.termino_origen,
       cfg.activo,
       im.source_url as imagen,
       -- Columnas NUEVAS, al final: padre inmediato con su id.
       coalesce(c.parent_id, c2.parent_id)                  as padre_id,
       -- El NOMBRE del padre NO se puede sacar de channel.categories: esa tabla
       -- solo tiene las categorías HOJA que usan nuestros productos, no las
       -- intermedias, así que el join por parent_id devolvía NULL en las 1,584
       -- filas. Sale del penúltimo segmento del `path`, que es el mismo criterio
       -- con el que el panel agrupa. El split acepta los DOS separadores que la
       -- columna trae: '›' (U+203A, 2,612 filas) y '>' (2 filas).
       btrim(rt.seg[array_length(rt.seg, 1) - 1])            as padre_nombre
  from enrich.market_sku_config cfg
  left join core.products p
         on p.sku = cfg.sku
  left join channel.product_category pc
         on pc.sku = cfg.sku and pc.channel_id = cfg.canal
  left join channel.categories c
         on c.category_id = pc.category_id and c.channel_id = cfg.canal
  left join channel.categories c2
         on c2.category_id = cfg.categoria_id_real and c2.channel_id = cfg.canal
  left join lateral (
         select regexp_split_to_array(coalesce(c.path, c2.path), '\s*[›>]\s*') as seg
         ) rt on true
  left join lateral (
         select m2.source_url
           from enrich.product_media m2
          where m2.sku = cfg.sku and m2.kind = 'wc'
          order by m2.id
          limit 1) im on true;

comment on view enrich.market_skus_v is
  'SKUs vigilados por Competencia con su categoría resuelta. La categoría '
  'prefiere la del PANEL (channel.product_category) y cae a la que midió '
  'Competencia (market_sku_config.categoria_id_real); raiz_id, padre_id y la '
  'ruta salen SIEMPRE de esa misma categoría — mezclarlas fue el bug de la 0013.';

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN
--
--   -- 1) Columnas nuevas presentes y en orden (las 12 viejas + 2)
--   select column_name, ordinal_position from information_schema.columns
--    where table_schema='enrich' and table_name='market_skus_v' order by 2;
--
--   -- 2) Coherencia raíz ↔ ruta: debe subir de 0 a ~1,567 de 1,584
--   select count(*) total,
--          sum(case when ruta like raiz_nombre || '%' then 1 else 0 end) coherentes
--     from enrich.market_skus_v;
--
--   -- 3) Las 17 que siguen mal son conflicto de channel.categories, no de aquí
--   select raiz_id, raiz_nombre, categoria_id, ruta from enrich.market_skus_v
--    where ruta not like raiz_nombre || '%';
--
--   -- 4) Deportes y Fitness queda en 143 (salen los 5 ajenos)
--   select count(*) from enrich.market_skus_v where raiz_id='MLM1276';
-- ═══════════════════════════════════════════════════════════════════════════
