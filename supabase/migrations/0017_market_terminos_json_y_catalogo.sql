-- ═══════════════════════════════════════════════════════════════════════════
-- 0017 — ENRICH: los términos de /trends viven como JSON por categoría, y los
--        términos MEDIDOS se normalizan en un catálogo con FK.
--
-- Son DOS problemas distintos y por eso llevan soluciones distintas. La
-- diferencia entre ellos es el COSTO:
--
--   · Los "más buscados" de `/trends` son GRATIS (API de ML) y masivos: 5,853
--     filas en 222 categorías, hasta 50 por categoría (medido: 37 categorías
--     topan en 50, promedio 39.8). Nadie los referencia y solo se leen en bloque,
--     por categoría. Normalizarlos no compra nada. → se EMPAQUETAN en un array
--     JSON por categoría: 222 filas en vez de 5,853.
--
--   · Los términos MEDIDOS cuestan dinero: cada uno es una corrida del buscador
--     en Apify (~$0.007). Hoy su texto se repite: 1,816 filas de
--     `market_search_results` para solo 326 términos distintos (5.6 filas por
--     término), y 401 filas de `market_sku_config` para 350 distintos — hasta 5
--     SKUs comparten el mismo término ("extractor de tornillos"). → se
--     NORMALIZAN en `market_search_term` con FK.
--
-- POR QUÉ EL FK Y NO SOLO AHORRAR TEXTO. El ahorro en bytes es menor (~35 KB).
-- Lo que compra el catálogo es (a) que "un término medido una vez sirve a todos
-- los SKUs que lo comparten" deje de ser una convención del código y sea una
-- garantía de la base, y (b) un lugar donde vivan los datos DEL TÉRMINO, que hoy
-- no tienen dónde: cuándo se midió, cuántos resultados trajo, de dónde salió.
--
-- DECISIÓN: el catálogo lleva SOLO los términos medidos (~400), no los 5,850 de
-- /trends. Los de /trends son sugerencias de ML, no cosas que hayamos corrido;
-- mezclarlos haría un catálogo 15 veces más grande donde el 95% de las filas
-- nunca se midió.
--
-- ORDEN Y ATOMICIDAD: todo va en una transacción. Los DROP de columna van
-- DESPUÉS de verificar que el backfill quedó completo (los asserts de abajo
-- revientan la transacción si no).
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- ── 1. Los "más buscados" de /trends, empaquetados por categoría ────────────
create table if not exists enrich.market_terms_json (
  canal        text not null default 'mercado_libre' references core.channels(id),
  categoria_id text not null,
  -- Array ORDENADO de strings: la posición es el índice + 1. ML no publica
  -- volumen —solo el orden— así que no hay nada más que guardar por término.
  terminos     jsonb not null default '[]'::jsonb,
  capturado_en timestamptz not null default now(),
  primary key (canal, categoria_id),
  constraint market_terms_json_es_array check (jsonb_typeof(terminos) = 'array')
);

insert into enrich.market_terms_json (canal, categoria_id, terminos, capturado_en)
select canal, categoria_id,
       jsonb_agg(termino order by posicion),
       max(capturado_en)
  from enrich.market_terms
 group by canal, categoria_id
    on conflict (canal, categoria_id) do update
   set terminos = excluded.terminos, capturado_en = excluded.capturado_en;

-- No perder términos en el empaquetado.
do $$
declare viejos int; nuevos int;
begin
  select count(*) into viejos from enrich.market_terms;
  select coalesce(sum(jsonb_array_length(terminos)), 0) into nuevos
    from enrich.market_terms_json;
  if viejos <> nuevos then
    raise exception 'Empaquetado incompleto: % términos en filas, % en JSON', viejos, nuevos;
  end if;
end $$;

drop table enrich.market_terms;
alter table enrich.market_terms_json rename to market_terms;

comment on table enrich.market_terms is
  'Los "más buscados" que ML publica por categoría (GET /trends), como array '
  'ordenado: la posición es el índice + 1. Uno por (canal, categoría) y no una '
  'fila por término: son gratis, masivos (hasta 50 por categoría) y solo se leen '
  'en bloque. Los términos que SÍ medimos con Apify viven en market_search_term.';

-- ── 2. Catálogo de términos MEDIDOS ─────────────────────────────────────────
create table if not exists enrich.market_search_term (
  id         bigserial primary key,
  canal      text not null default 'mercado_libre' references core.channels(id),
  termino    text not null,
  -- De dónde salió el término, no de dónde salió el resultado.
  origen     text,                    -- 'ia' | 'manual'
  -- Datos DEL TÉRMINO, que antes no tenían dónde vivir.
  medido_en  timestamptz,             -- cuándo se corrió el buscador
  resultados int,                     -- cuántas filas trajo esa corrida
  creado_en  timestamptz not null default now(),
  unique (canal, termino)
);

comment on table enrich.market_search_term is
  'Un término = una fila. Cada uno costó una corrida del buscador en Apify '
  '(~$0.007), así que el FK es lo que garantiza que se pague UNA vez y lo reusen '
  'todos los SKUs que lo comparten.';

-- El universo: lo que se midió + lo que está asignado a un SKU.
insert into enrich.market_search_term (canal, termino, medido_en, resultados)
select canal, termino, max(capturado_en), count(*)
  from enrich.market_search_results
 group by canal, termino
    on conflict (canal, termino) do nothing;

insert into enrich.market_search_term (canal, termino, origen)
select cfg.canal, cfg.termino_general, min(cfg.termino_origen)
  from enrich.market_sku_config cfg
 where cfg.termino_general is not null and btrim(cfg.termino_general) <> ''
 group by cfg.canal, cfg.termino_general
    on conflict (canal, termino) do update
   set origen = coalesce(enrich.market_search_term.origen, excluded.origen);

-- ── 3. FK en los resultados del buscador ────────────────────────────────────
alter table enrich.market_search_results
  add column if not exists termino_id bigint;

update enrich.market_search_results r
   set termino_id = t.id
  from enrich.market_search_term t
 where t.canal = r.canal and t.termino = r.termino
   and r.termino_id is null;

do $$
declare huerfanos int;
begin
  select count(*) into huerfanos
    from enrich.market_search_results where termino_id is null;
  if huerfanos > 0 then
    raise exception '% resultados sin término en el catálogo', huerfanos;
  end if;
end $$;

alter table enrich.market_search_results
  drop constraint market_search_results_pkey,
  alter column termino_id set not null,
  add constraint market_search_results_termino_fk
      foreign key (termino_id) references enrich.market_search_term(id) on delete cascade,
  add constraint market_search_results_pkey primary key (termino_id, externo_id),
  -- `canal` y `termino` los dice ya la fila del catálogo: dejarlos era la
  -- duplicación que esta migración viene a quitar.
  drop column termino,
  drop column canal;

-- ── 4. FK en el SKU vigilado ────────────────────────────────────────────────
alter table enrich.market_sku_config
  add column if not exists termino_id bigint
      references enrich.market_search_term(id) on delete set null;

update enrich.market_sku_config cfg
   set termino_id = t.id
  from enrich.market_search_term t
 where t.canal = cfg.canal and t.termino = cfg.termino_general
   and cfg.termino_id is null;

do $$
declare huerfanos int;
begin
  select count(*) into huerfanos from enrich.market_sku_config
   where termino_general is not null and btrim(termino_general) <> ''
     and termino_id is null;
  if huerfanos > 0 then
    raise exception '% SKUs con término que no entró al catálogo', huerfanos;
  end if;
end $$;

-- ── 5. La vista vuelve a exponer termino_general, ahora por JOIN ────────────
-- El API no cambia: `termino_general` sigue siendo un string en la respuesta.
create or replace view enrich.market_skus_v as
select cfg.sku,
       cfg.canal,
       p.name as nombre,
       coalesce(pc.category_id, cfg.categoria_id_real)      as categoria_id,
       btrim(coalesce(c.name, c2.name))                     as categoria_nombre,
       coalesce(c.path, c2.path)                            as ruta,
       coalesce(c.root_id, c2.root_id)                      as raiz_id,
       btrim(coalesce(c.root_name, c2.root_name))           as raiz_nombre,
       st.termino                                           as termino_general,
       cfg.termino_origen,
       cfg.activo,
       im.source_url as imagen,
       coalesce(c.parent_id, c2.parent_id)                  as padre_id,
       btrim(rt.seg[array_length(rt.seg, 1) - 1])           as padre_nombre
  from enrich.market_sku_config cfg
  left join core.products p
         on p.sku = cfg.sku
  left join enrich.market_search_term st
         on st.id = cfg.termino_id
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

-- El DROP va DESPUÉS de recrear la vista: market_skus_v depende de
-- termino_general y Postgres se niega ("DependentObjectsStillExist"). Con la
-- vista ya apuntando al catálogo, la columna queda libre.
--
-- `termino_origen` se queda en el SKU y NO se mueve al catálogo: es una
-- propiedad de la ASIGNACIÓN (este SKU lo corrigió un humano), no del término.
-- Dos SKUs pueden compartir término y haber llegado a él por caminos distintos.
alter table enrich.market_sku_config drop column termino_general;

create index if not exists idx_market_sku_config_termino
  on enrich.market_sku_config (termino_id) where termino_id is not null;

-- ── 6. RLS y grants de la tabla nueva ───────────────────────────────────────
alter table enrich.market_search_term enable row level security;
alter table enrich.market_terms       enable row level security;
grant all on enrich.market_search_term to service_role;
grant all on enrich.market_terms      to service_role;
grant usage, select on sequence enrich.market_search_term_id_seq to service_role;

commit;

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN
--
--   -- 222 categorías con su array; la suma debe dar los 5,853 de antes
--   select count(*) categorias, sum(jsonb_array_length(terminos)) terminos
--     from enrich.market_terms;
--
--   -- ~400 términos únicos en el catálogo, ninguno repetido
--   select count(*) from enrich.market_search_term;
--
--   -- los 1,816 resultados siguen ahí, ahora por FK
--   select count(*) resultados, count(distinct termino_id) terminos
--     from enrich.market_search_results;
--
--   -- 401 SKUs conservan su término, ahora por JOIN
--   select count(termino_general) from enrich.market_skus_v;
--
--   -- el reuso, que es el punto: términos que sirven a más de un SKU
--   select st.termino, count(*) skus
--     from enrich.market_sku_config cfg
--     join enrich.market_search_term st on st.id = cfg.termino_id
--    group by 1 having count(*) > 1 order by 2 desc;
-- ═══════════════════════════════════════════════════════════════════════════
