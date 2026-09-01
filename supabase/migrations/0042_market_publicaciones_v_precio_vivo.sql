-- ═══════════════════════════════════════════════════════════════════════════
-- 0042 — ENRICH: el precio y el estado del panel salen del SYNC VIVO, no de la
--        foto mensual. Y el join se hace por `account_id`, no por `store_name`.
--
-- ── LO QUE ENCONTRÓ EDUARDO ────────────────────────────────────────────────
-- Abrió dos publicaciones nuestras y las comparó con el panel:
--
--   MLM5108809642 (BEKURA)         panel $184      ML cobra  $70.61   (2.6x)
--   MLM5108968738 (SANCORFASHION)  panel `active`  ML dice   "no disponible"
--
-- Las dos estaban BIEN en `channel.listings` —el sync de 15 min las había
-- tocado ese mismo día, a las 17:40 y 06:37— y MAL en el panel.
--
-- ── LA CAUSA: LA FOTO DE AGOSTO LE GANABA AL SYNC ──────────────────────────
-- La vista resolvía así:
--
--   COALESCE(m.sale_price, l.price) AS precio     -- la FOTO primero
--   m.estado                                      -- el estado NUNCA miraba el sync
--
-- `m.*` es `market_listing_metrics`, una foto MENSUAL que escribe la captura de
-- rankings. `l.*` es `channel.listings`, que el sync refresca cada 15 minutos.
-- El `COALESCE` sólo caía al sync cuando la foto venía vacía, así que mientras
-- existiera una foto de agosto —y existe para 3,117 publicaciones— el panel
-- mostraba agosto.
--
-- MEDIDO EL 1-SEP-2026 sobre las 3,117 comparables:
--   · 436 con el ESTADO distinto al real
--   · 709 con el PRECIO distinto (>$1)
--   · 235 donde el panel muestra 30% o más POR ENCIMA de lo que ML cobra
--
-- ── Y `price` NO ES LO QUE SE COBRA ────────────────────────────────────────
-- Ésta es la parte que hace falta saber para no “arreglarlo” mal. Las tres
-- columnas de `channel.listings` no son sinónimos:
--
--   `price`       lo que escribe el sync de 15 min. NO es lo que paga el cliente.
--   `price_sale`  lo que ML COBRA de verdad (`/items/{id}/sale_price` → amount),
--                 confirmado por webhooks, el barrido y `precio_al_abrir`.
--                 Un `None` de ML jamás se escribe ahí, así que un valor no nulo
--                 SIEMPRE es una observación real.
--   `price_base`  el precio de lista, el tachado (`regular_amount`).
--
-- Verificado en los dos casos de arriba:
--   BEKURA         price=282.42  price_base=282.42  price_sale=70.61   → cobra 70.61
--   SANCORFASHION  price=270.00  price_base=399.00  price_sale=270.00  → tachado 399
--
-- Ya había mordido antes, documentado en `precio_al_abrir.py`: MLM5473713768
-- mostraba $219 en el panel mientras ML cobraba $99. Mismo defecto, otra pantalla.
--
-- ── EL JOIN, DE `store_name` A `account_id` ────────────────────────────────
-- `store_name` es texto libre y viene NULL en parte del catálogo. Medido: por
-- `store_name` el join alcanza 6,017 de 6,042 filas; por `account_id`, 6,041.
-- Son 24 publicaciones que se quedaban sin datos vivos por una llave que no es
-- llave.
--
-- ── EL ORDEN NUEVO, Y POR QUÉ ──────────────────────────────────────────────
-- El sync manda; la foto es el respaldo. Se invierte el `COALESCE`, no se borra:
-- una publicación que el sync no alcanzó a tocar sigue mostrando su foto —vieja,
-- pero real— en vez de un hueco.
--
--   estado         COALESCE(lower(l.situacion), m.estado)
--   precio         COALESCE(l.price_sale, l.price, m.sale_price)
--   precio_lista   COALESCE(l.price_base, l.price, m.list_price)
--
-- Se agrega `precio_confirmado_en` (= `l.price_sale_at`) para que la UI pueda
-- decir de cuándo es el precio en vez de afirmarlo sin fecha. Nadie la usa
-- todavía; existe para que la pantalla pueda dejar de mentir por omisión.
--
-- NO se toca la mitad de abajo del UNION salvo el precio: esa rama ya lee de
-- `channel.listings`, pero traía `l.price` —que no es lo que se cobra— en vez de
-- `l.price_sale`.
--
-- ADITIVA salvo el `create or replace`: agrega una columna al final y cambia el
-- ORIGEN de `estado`, `precio` y `precio_lista`. Revertir: reaplicar la 0039.
-- ═══════════════════════════════════════════════════════════════════════════

create or replace view enrich.market_publicaciones_v as
with ventas as (
  select item_id, cuenta, sum(units_sold)::int as unidades
    from channel.sales_daily
   where canal = 'mercado_libre'
     and date > (now() at time zone 'America/Mexico_City')::date - 30
   group by 1, 2
),
medido as (
  -- Última NO NULA por columna (0039): la fila del mes nuevo nace con sólo
  -- `visits_30d`, y un desempate por fila entero dejaría el panel sin títulos.
  select mm.sku, mm.cuenta, mm.canal,
         (array_agg(mm.listing_id order by mm.periodo desc) filter (where mm.listing_id is not null))[1] as listing_id,
         (array_agg(mm.title      order by mm.periodo desc) filter (where mm.title      is not null))[1] as title,
         (array_agg(mm.estado     order by mm.periodo desc) filter (where mm.estado     is not null))[1] as estado,
         (array_agg(mm.sale_price order by mm.periodo desc) filter (where mm.sale_price is not null))[1] as sale_price,
         (array_agg(mm.list_price order by mm.periodo desc) filter (where mm.list_price is not null))[1] as list_price,
         (array_agg(mm.visits_30d order by mm.periodo desc) filter (where mm.visits_30d is not null))[1] as visits_30d,
         max(mm.periodo) as periodo
    from enrich.market_listing_metrics mm
   group by mm.sku, mm.cuenta, mm.canal
)
select m.sku,
       m.cuenta,
       m.canal,
       m.listing_id as ml_item_id,
       m.title      as titulo,
       -- EL SYNC MANDA. La foto queda de respaldo para lo que el sync no tocó.
       coalesce(lower(l.situacion), m.estado)              as estado,
       -- `price_sale` es lo que ML COBRA; `price` no lo es. Ver el encabezado.
       coalesce(l.price_sale, l.price, m.sale_price)       as precio,
       coalesce(l.price_base, l.price, m.list_price)       as precio_lista,
       coalesce(
         case when m.canal = 'mercado_libre'
               and m.listing_id ~ '^MLM[0-9]{9,12}$'
              then 'https://articulo.mercadolibre.com.mx/MLM-' || substring(m.listing_id from 4) || '-_JM'
         end, l.url)                                       as url,
       m.visits_30d                as visitas_30d,
       coalesce(v.unidades, 0)     as unidades_30d,
       'pedidos'::text             as fuente_unidades,
       m.periodo,
       -- AL FINAL a propósito: `create or replace view` sólo deja AGREGAR
       -- columnas al final; ponerla en medio intenta RENOMBRAR `url` y falla.
       l.price_sale_at             as precio_confirmado_en
  from medido m
  -- Por `account_id`, NO por `store_name`: ese texto libre viene NULL en parte
  -- del catálogo y dejaba 24 publicaciones sin datos vivos.
  left join core.accounts a on a.legacy_code = m.cuenta
  left join channel.listings l
         on l.sku = m.sku and l.account_id = a.id and l.canal = m.canal
  left join ventas v on v.item_id = m.listing_id and v.cuenta = m.cuenta

union all

-- Las publicaciones vivas que NUNCA se midieron: no tienen foto, así que todo
-- sale del sync. Aquí el precio también pasa a `price_sale`.
select l.sku,
       a.legacy_code       as cuenta,
       l.canal,
       l.listing_id        as ml_item_id,
       null::text          as titulo,
       lower(l.situacion)  as estado,
       coalesce(l.price_sale, l.price)  as precio,
       coalesce(l.price_base, l.price)  as precio_lista,
       coalesce(
         case when l.canal = 'mercado_libre'
               and l.listing_id ~ '^MLM[0-9]{9,12}$'
              then 'https://articulo.mercadolibre.com.mx/MLM-' || substring(l.listing_id from 4) || '-_JM'
         end, l.url)       as url,
       null::int           as visitas_30d,
       coalesce(v.unidades, 0) as unidades_30d,
       'pedidos'::text     as fuente_unidades,
       null::date          as periodo,
       l.price_sale_at     as precio_confirmado_en
  from channel.listings l
  join core.accounts a on a.id = l.account_id
  left join ventas v on v.item_id = l.listing_id and v.cuenta = a.legacy_code
 where l.canal = 'mercado_libre'
   and lower(l.situacion) in ('active', 'paused')
   and nullif(l.listing_id, '') is not null
   and not exists (select 1 from enrich.market_listing_metrics mm2
                    where mm2.sku = l.sku and mm2.canal = l.canal
                      and mm2.cuenta = a.legacy_code);

comment on view enrich.market_publicaciones_v is
  'Una fila por publicación. ESTADO y PRECIO salen del sync de 15 min '
  '(channel.listings), no de la foto mensual: la foto sólo se usa como respaldo. '
  'El precio es `price_sale` —lo que ML COBRA— no `price`. Unidades en vivo de '
  'channel.sales_daily; visitas de la medición diaria.';

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN (correr DESPUÉS de aplicar)
--
--   -- 1) El caso de Eduardo: BEKURA debe decir 70.61, no 183.57;
--   --    SANCORFASHION debe decir `paused`, no `active`.
--   select cuenta, ml_item_id, estado, precio, precio_lista, precio_confirmado_en
--     from enrich.market_publicaciones_v where sku = 'TEC-1327-NEG';
--
--   -- 2) Nadie perdió su fila y no hay duplicados.
--   select count(*) filas, count(distinct (sku, cuenta)) llaves,
--          count(*) filter (where precio is null)  sin_precio,
--          count(*) filter (where estado is null)  sin_estado
--     from enrich.market_publicaciones_v;
--
--   -- 3) Cuánto se movió: cuántas filas cambian de precio o de estado.
--   --    (comparar contra la foto, que es lo que se mostraba antes)
--   select count(*) filter (where p.estado <> m.est_foto)                 estado,
--          count(*) filter (where abs(p.precio - m.precio_foto) > 1)      precio
--     from enrich.market_publicaciones_v p
--     join (select sku, cuenta,
--                  (array_agg(estado     order by periodo desc) filter (where estado     is not null))[1] est_foto,
--                  (array_agg(sale_price order by periodo desc) filter (where sale_price is not null))[1] precio_foto
--             from enrich.market_listing_metrics where canal='mercado_libre'
--            group by 1,2) m using (sku, cuenta);
-- ═══════════════════════════════════════════════════════════════════════════
