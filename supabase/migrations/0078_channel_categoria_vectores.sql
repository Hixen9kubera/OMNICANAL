-- ═══════════════════════════════════════════════════════════════════════════
-- 0078 — Revisión de la categoría de ML por TEXTO y FOTO antes de publicar.
--        27-oct-2026.
--
-- POR QUÉ
-- La tarjeta de categoría de la vista previa (v0.677.0) solo compara el TÍTULO
-- contra el predictor de ML (`domain_discovery`). Como Crear eligió la categoría
-- con ese mismo predictor, casi siempre sale «coincide» aunque esté mal
-- (circular). Medido el 26-oct: ~7 % de las activas en categoría equivocada.
-- La prueba del 26/27-oct (192 publicaciones revisadas a mano, gasto US$0.098
-- autorizado por Eduardo) mostró que comparar por SIGNIFICADO el título con la
-- descripción (qwen3-embedding-8b) y 2 fotos (voyage-multimodal-3.5) contra
-- todas las categorías detiene ~8 de cada 10 errores y pone la correcta entre
-- las propuestas en 47 de 50.
--
-- QUÉ CREA
--   · extensión `vector` (pgvector 0.8.2) en el esquema `extensions`.
--   · channel.ml_category_vec: la HUELLA de cada hoja publicable del árbol
--     (0059) por modelo, en halfvec(512) normalizado (512 dimensiones medían
--     igual que 1024 en la prueba). Una fila = (categoría, modelo). La llena
--     `scripts/vectorizar_arbol_ml.py`; `texto_hash` dice si la ruta cambió y
--     hay que volver a calcularla. ~10.7k hojas × 2 modelos ≈ 22 MB.
--   · channel.ml_revision_categoria: CADA revisión de la vista previa (lo que
--     dijo el texto, la foto y su fusión, el color, las propuestas y lo que costó)
--     y la DECISIÓN de quien publicó (confirmó, con qué motivo). Hoy el veredicto
--     no se guarda en ningún lado: sin esto no se puede medir ni auditar.
--     El tope diario de gasto suma `costo_usd` de las últimas 24 h.
--
-- QUIÉN ESCRIBE: solo el backend (service_role). RLS sin políticas, grant a
-- service_role, en la misma migración (patrón de la casa).
--
-- Estado: aplicar PRIMERO en el sandbox. Producción solo con acta (doble candado).
--
-- REVERSA (solo si se abandona el diseño; las dos tablas no las lee nadie con
-- CATEGORIA_VEC_ENABLED=false):
--   drop table channel.ml_revision_categoria;
--   drop table channel.ml_category_vec;
--   -- la extensión se deja: no estorba y otras pruebas la pueden usar.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

create extension if not exists vector with schema extensions;

create table if not exists channel.ml_category_vec (
    category_id text        not null,
    modelo      text        not null,
    texto_hash  text        not null,
    vec         extensions.halfvec(512) not null,
    creado_at   timestamptz not null default now(),
    primary key (category_id, modelo)
);
comment on table channel.ml_category_vec is
  'Huella (embedding normalizado, 512 dims) de cada hoja publicable de channel.ml_category_tree, por modelo. '
  'La llena scripts/vectorizar_arbol_ml.py. texto_hash = sha1 de la ruta vectorizada.';

alter table channel.ml_category_vec enable row level security;
grant all on channel.ml_category_vec to service_role;

create table if not exists channel.ml_revision_categoria (
    id            bigint generated always as identity primary key,
    sku           text        not null,
    wc_id         bigint,
    categoria_id  text,
    color         text        not null check (color in ('verde', 'ambar', 'rojo', 'sin_senal')),
    rango_texto   integer,
    rango_fotos   integer,
    rango_fusion  integer,
    n_fotos       smallint    not null default 0,
    propuestas    jsonb       not null default '[]'::jsonb,
    detalle       jsonb       not null default '{}'::jsonb,
    costo_usd     numeric(10, 6) not null default 0,
    creado_por    text,
    creado_at     timestamptz not null default now(),
    decision      text        check (decision in ('confirmada')),
    motivo        text,
    decidido_por  text,
    decidido_at   timestamptz
);
comment on table channel.ml_revision_categoria is
  'Una fila por revisión de categoría de la vista previa de un alta en ML (texto + fotos contra el árbol) '
  'y la decisión de quien publicó. color: verde/ambar/rojo; sin_senal = no se pudo calcular (se publicó con '
  'la revisión de siempre). rango_* = lugar de la categoría elegida entre todas las hojas (1 = la más parecida).';
comment on column channel.ml_revision_categoria.motivo is
  'Por qué se publicó en la categoría elegida aunque salió en rojo (lo exige CATEGORIA_VEC_CANDADO).';

create index if not exists ml_revision_categoria_creado_idx on channel.ml_revision_categoria (creado_at);
create index if not exists ml_revision_categoria_sku_idx on channel.ml_revision_categoria (sku, creado_at desc);

alter table channel.ml_revision_categoria enable row level security;
grant all on channel.ml_revision_categoria to service_role;

insert into ops.migraciones (migracion, detalle)
select '0078_channel_categoria_vectores',
       jsonb_build_object('tablas', jsonb_build_array('channel.ml_category_vec', 'channel.ml_revision_categoria'))
 where to_regclass('ops.migraciones') is not null
   and not exists (select 1 from ops.migraciones where migracion = '0078_channel_categoria_vectores');

commit;
