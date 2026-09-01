-- REVERSA de la 0039 (la vista que dejo la 0038), capturada de
-- produccion el 1-sep-2026 justo antes de aplicar.
create or replace view enrich.market_publicaciones_v as
 WITH ventas AS (
         SELECT sales_daily.item_id,
            sales_daily.cuenta,
            sum(sales_daily.units_sold)::integer AS unidades
           FROM channel.sales_daily
          WHERE sales_daily.canal = 'mercado_libre'::text AND sales_daily.date > ((now() AT TIME ZONE 'America/Mexico_City'::text)::date - 30)
          GROUP BY sales_daily.item_id, sales_daily.cuenta
        )
 SELECT m.sku,
    m.cuenta,
    m.canal,
    m.ml_item_id,
    m.titulo,
    m.estado,
    m.precio,
    m.precio_lista,
    m.url,
    m.visitas_30d,
    COALESCE(v.unidades, 0) AS unidades_30d,
    'pedidos'::text AS fuente_unidades,
    m.periodo
   FROM ( SELECT DISTINCT ON (mm.sku, mm.canal, mm.cuenta) mm.sku,
            mm.cuenta,
            mm.canal,
            mm.listing_id AS ml_item_id,
            mm.title AS titulo,
            mm.estado,
            COALESCE(mm.sale_price, l.price) AS precio,
            COALESCE(mm.list_price, l.price) AS precio_lista,
            COALESCE(
                CASE
                    WHEN mm.canal = 'mercado_libre'::text AND mm.listing_id ~ '^MLM[0-9]{9,12}$'::text THEN ('https://articulo.mercadolibre.com.mx/MLM-'::text || SUBSTRING(mm.listing_id FROM 4)) || '-_JM'::text
                    ELSE NULL::text
                END, l.url) AS url,
            mm.visits_30d AS visitas_30d,
            mm.periodo
           FROM enrich.market_listing_metrics mm
             LEFT JOIN channel.listings l ON l.sku = mm.sku AND l.store_name = mm.cuenta AND l.canal = mm.canal
          ORDER BY mm.sku, mm.canal, mm.cuenta, (mm.title IS NOT NULL OR mm.visits_30d IS NOT NULL OR mm.units_30d IS NOT NULL) DESC, mm.periodo DESC) m
     LEFT JOIN ventas v ON v.item_id = m.ml_item_id AND v.cuenta = m.cuenta
UNION ALL
 SELECT l.sku,
    a.legacy_code AS cuenta,
    l.canal,
    l.listing_id AS ml_item_id,
    NULL::text AS titulo,
    lower(l.situacion) AS estado,
    l.price AS precio,
    l.price_base AS precio_lista,
    COALESCE(
        CASE
            WHEN l.canal = 'mercado_libre'::text AND l.listing_id ~ '^MLM[0-9]{9,12}$'::text THEN ('https://articulo.mercadolibre.com.mx/MLM-'::text || SUBSTRING(l.listing_id FROM 4)) || '-_JM'::text
            ELSE NULL::text
        END, l.url) AS url,
    NULL::integer AS visitas_30d,
    COALESCE(v.unidades, 0) AS unidades_30d,
    'pedidos'::text AS fuente_unidades,
    NULL::date AS periodo
   FROM channel.listings l
     JOIN core.accounts a ON a.id = l.account_id
     LEFT JOIN ventas v ON v.item_id = l.listing_id AND v.cuenta = a.legacy_code
  WHERE l.canal = 'mercado_libre'::text AND (lower(l.situacion) = ANY (ARRAY['active'::text, 'paused'::text])) AND NULLIF(l.listing_id, ''::text) IS NOT NULL AND NOT (EXISTS ( SELECT 1
           FROM enrich.market_listing_metrics mm2
          WHERE mm2.sku = l.sku AND mm2.canal = l.canal AND mm2.cuenta = a.legacy_code));
