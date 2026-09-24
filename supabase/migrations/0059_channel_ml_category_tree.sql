-- ═══════════════════════════════════════════════════════════════════════════
-- 0059 — channel.ml_category_tree: el árbol COMPLETO de categorías de Mercado
--        Libre México, para buscar por texto. 24-sep-2026.
--
-- POR QUÉ UNA TABLA APARTE (decisión de Eduardo, 24-sep)
-- channel.categories ya existe, pero para ML guarda solo las ~2,700 categorías
-- que usa algún SKU (la llena el ETL a partir de las asignaciones). Hay código
-- que la lee con ese significado. Meterle las 12,263 del árbol cambiaría lo que
-- quiere decir una fila; aquí, en cambio, una fila = "esta categoría existe en
-- ML hoy", nada más.
--
-- POR QUÉ HACE FALTA
-- El picker del Estudio buscaba solo con `domain_discovery`, que es un
-- PREDICTOR ("si un producto se llamara así, ¿dónde lo pondría?"), no un
-- buscador del árbol. Medido en septiembre: Lavabos (Hogar › Baños), Bocinas
-- (Audio) y los dos "Otros" de cosmetología NUNCA salen, busques lo que busques,
-- y cuando dos categorías se llaman igual sale primero la de otro árbol.
--
-- QUIÉN LA LLENA
-- scripts/cargar_arbol_ml.py, desde GET /sites/MLM/categories/all (una sola
-- llamada: ~2 s, 1.5 MB comprimido). Reemplaza el contenido en una transacción:
-- las que ML retiró se borran. La migración no siembra nada.
--
-- name_norm y path_norm los calcula el cargador con la MISMA función con la que
-- el backend normaliza lo que se teclea (services/categorias_arbol.normalizar):
-- minúsculas, sin acentos, solo letras y números. Así la búsqueda compara
-- texto con texto sin depender de extensiones (unaccent) ni de su collation.
--
-- RLS + grant en la misma migración (patrón de la casa). Sin políticas: solo
-- service_role (backend y crons).
-- ═══════════════════════════════════════════════════════════════════════════

create table if not exists channel.ml_category_tree (
  category_id     text        primary key,
  name            text        not null,
  parent_id       text,
  root_id         text        not null,
  -- Ruta de la raíz a la categoría, INCLUIDA ella. En arreglos y no en un texto
  -- con separador: channel.categories ya carga dos separadores distintos
  -- ('›' del ETL y '>' del panel) y aquí no hay por qué elegir.
  path_ids        text[]      not null,
  path_names      text[]      not null,
  is_leaf         boolean     not null,
  -- settings.listing_allowed de ML. Las ramas vienen en false: ML rechaza
  -- publicar en "Equipos de Cosmetología", hay que bajar a una de sus hojas.
  listing_allowed boolean     not null,
  total_items     integer,
  catalog_domain  text,
  name_norm       text        not null,
  path_norm       text        not null,
  leido_at        timestamptz not null default now(),
  check (cardinality(path_ids) = cardinality(path_names)),
  check (path_ids[cardinality(path_ids)] = category_id)
);

-- "Las hojas publicables debajo de X" (pegar el ID de una rama).
create index if not exists ml_category_tree_path_ids_idx
  on channel.ml_category_tree using gin (path_ids);

alter table channel.ml_category_tree enable row level security;
grant all on channel.ml_category_tree to service_role;

comment on table channel.ml_category_tree is
  'Árbol completo de categorías de ML México (GET /sites/MLM/categories/all). Lo llena scripts/cargar_arbol_ml.py; lo lee el buscador del picker del Estudio.';
comment on column channel.ml_category_tree.listing_allowed is
  'settings.listing_allowed de ML: false en las ramas. El buscador solo ofrece las que están en true.';
comment on column channel.ml_category_tree.name_norm is
  'name normalizado por services/categorias_arbol.normalizar (minúsculas, sin acentos, solo [a-z0-9] y espacios).';
comment on column channel.ml_category_tree.path_norm is
  'La ruta completa (path_names) normalizada igual que name_norm. Es donde busca el picker.';
comment on column channel.ml_category_tree.leido_at is
  'Cuándo se leyó de ML en la última carga. Toda la tabla comparte el mismo valor.';
