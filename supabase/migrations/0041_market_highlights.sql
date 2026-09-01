-- ═══════════════════════════════════════════════════════════════════════════
-- 0041 — ENRICH: el sondeo GRATIS de /highlights, y su bandera en la vista de
--        prioridad. Es el filtro que evita pagar raspados imposibles.
--
-- ── EL PROBLEMA QUE RESUELVE ───────────────────────────────────────────────
-- Mercado Libre **no publica lista de más vendidos de todas las categorías**.
-- Raspar una que no la tiene cuesta lo mismo que raspar una que sí, y devuelve
-- nada. Hoy no hay forma de saberlo antes de pagar.
--
-- `GET /highlights/MLM/category/{id}` lo dice **gratis y en una llamada**: si
-- responde vacío, ahí no hay ranking y no hay nada que raspar.
--
-- Medido el 1-sep-2026 sobre las 60 categorías de más venta: **52 con ranking,
-- 8 sin él** — y entre las 8 están MLM437948 (Bombas de Agua) y MLM429635
-- (Pistolas para Limpieza Textil), dos de las que más venden y que nunca se
-- capturaron. No era descuido: **no hay qué capturar.**
--
-- ── Y DE PASO, EL DETECTOR DE CAMBIO ───────────────────────────────────────
-- La misma llamada devuelve quién está en el top y en qué orden. Guardando una
-- huella del top-10 se sabe **cuándo se movió** — que es la señal para recapturar
-- por evento en vez de por calendario, sin gastar un peso en averiguarlo.
--
-- ── POR QUÉ UNA SOLA FILA POR CATEGORÍA Y NO UN HISTÓRICO ──────────────────
-- Una foto diaria de las 1,129 pesa ~785 KB; guardarlas todas son ~280 MB al
-- año para responder preguntas que hoy nadie hace. Con `cambio_en` —la última
-- vez que la huella cambió— se contesta lo que el plan necesita ("¿se movió
-- desde la última captura?") con **1,129 filas para siempre, ~800 KB**. Si algún
-- día hace falta la serie de tiempo, esa es otra decisión y otra tabla.
--
-- ── LA DISTINCIÓN QUE IMPORTA ──────────────────────────────────────────────
-- `capturado_en` es el último INTENTO y siempre avanza. `cambio_en` sólo se mueve
-- cuando la huella cambió de verdad. Son cosas distintas y confundirlas es cómo
-- se construye un medidor que miente en verde — la lección de la 0038.
--
-- Y el script NO escribe fila cuando la llamada FALLA: `n = 0` significa "ML dice
-- que no hay ranking", no "no pude preguntar". Un cero que en realidad es un
-- error se lee como "no insistas" y manda a no capturar donde sí hay dinero.
--
-- ── LA VISTA DE PRIORIDAD GANA DOS COLUMNAS ────────────────────────────────
-- `tiene_ranking_ml` y `top_cambio_en`, por `left join`: hasta la primera corrida
-- salen en NULL, que es lo honesto — "todavía no lo he preguntado".
--
-- ADITIVA salvo por el `create or replace` de la vista, que sólo AGREGA columnas
-- al final. Revertir: `drop table enrich.market_highlights` y la 0040.
-- ═══════════════════════════════════════════════════════════════════════════

create table if not exists enrich.market_highlights (
  canal         text        not null references core.channels(id),
  categoria_id  text        not null,
  -- [{"p":1,"id":"MLM16165950","t":"P"}, …] — posición, id y tipo (P/U/I).
  -- Compacto a propósito: 20 entradas caben en ~712 bytes.
  entradas      jsonb       not null default '[]'::jsonb,
  -- 0 = ML NO publica ranking de esta categoría. Nunca se escribe por un error.
  n             int         not null default 0,
  -- sha1 corto del top-10 de ids. Cambia ⇒ el ranking se movió.
  huella        text,
  -- Último INTENTO: siempre avanza, haya o no haya ranking.
  capturado_en  timestamptz not null default now(),
  -- Última vez que la huella cambió de verdad. NULL = nunca ha cambiado.
  cambio_en     timestamptz,
  primary key (canal, categoria_id)
);

-- El filtro que va a usar la cola de captura: "las que sí tienen ranking".
create index if not exists idx_market_highlights_con_ranking
  on enrich.market_highlights (canal, n) where n > 0;

-- Para el disparo por evento: "las que se movieron hace poco".
create index if not exists idx_market_highlights_cambio
  on enrich.market_highlights (cambio_en desc nulls last);

comment on table enrich.market_highlights is
  'Sondeo GRATIS de /highlights por categoría: si ML publica ranking (n>0), '
  'quiénes están y en qué orden, y cuándo se movió por última vez. Es el filtro '
  'que evita pagar raspados de categorías sin ranking y la señal para recapturar '
  'por evento. Una fila por categoría, sin histórico: ~800 KB en total.';
comment on column enrich.market_highlights.capturado_en is
  'Último INTENTO. Siempre avanza. No confundir con cambio_en.';
comment on column enrich.market_highlights.cambio_en is
  'Última vez que la huella del top-10 cambió de verdad.';
comment on column enrich.market_highlights.n is
  '0 significa que ML no publica ranking ahí. NUNCA se escribe 0 por un error '
  'de la llamada: en ese caso no se escribe la fila.';

-- ── La vista de prioridad (0040) gana las dos columnas, al final ────────────
create or replace view enrich.market_categoria_prioridad_v as
with vivas as (
  -- UNA fila por SKU: por publicación duplicaría al SKU de dos tiendas y las
  -- ventas saldrían al doble (Licuadoras daba 954 en vez de 478).
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
       r.volumen_mercado,
       -- NUEVAS (0041). NULL = todavía no se ha sondeado esa categoría.
       (h.n > 0)                              as tiene_ranking_ml,
       h.cambio_en                            as top_cambio_en
  from por_sku s
  left join ranking r on r.categoria_id = s.categoria_id
  left join enrich.market_highlights h
         on h.categoria_id = s.categoria_id and h.canal = 'mercado_libre'
 group by s.categoria_id, s.categoria_nombre, s.raiz_id, s.raiz_nombre,
          r.n_ranking, r.capturado_en, r.mediana, r.visitas_mercado,
          r.volumen_mercado, h.n, h.cambio_en;

comment on view enrich.market_categoria_prioridad_v is
  'Una fila por subcategoría ACTIVA para ordenarlas por prioridad. Ventas de '
  'channel.sales_daily en vivo; categoría de market_skus_v (la elección del panel '
  'manda); visitas propias y del mercado por separado; y desde la 0041, si ML '
  'publica ranking ahí y cuándo se movió por última vez.';

-- ═══════════════════════════════════════════════════════════════════════════
-- CÓMO SE USA
--
--   -- Lo que SÍ vale la pena raspar: vende, y ML tiene ranking, y está viejo.
--   select categoria_nombre, pesos_30d, dias_sin_captura
--     from enrich.market_categoria_prioridad_v
--    where unidades_30d > 0 and tiene_ranking_ml
--      and (dias_sin_captura is null or dias_sin_captura > 14)
--    order by pesos_30d desc;
--
--   -- Lo que NUNCA hay que raspar, por más que venda: ML no publica ranking.
--   select categoria_nombre, pesos_30d from enrich.market_categoria_prioridad_v
--    where tiene_ranking_ml is false order by pesos_30d desc;
--
--   -- Disparo por evento: el top se movió DESPUÉS de nuestra última captura.
--   select categoria_nombre, pesos_30d from enrich.market_categoria_prioridad_v
--    where tiene_ranking_ml
--      and (ranking_ultimo is null or top_cambio_en::date > ranking_ultimo)
--    order by pesos_30d desc;
--
-- VERIFICACIÓN (después de la primera corrida del sondeo)
--
--   select count(*) sondeadas,
--          count(*) filter (where n > 0)  con_ranking,
--          count(*) filter (where n = 0)  sin_ranking,
--          min(capturado_en)::date, max(capturado_en)::date
--     from enrich.market_highlights;
-- ═══════════════════════════════════════════════════════════════════════════
