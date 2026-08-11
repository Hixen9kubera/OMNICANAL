-- ═══════════════════════════════════════════════════════════════════════════
-- 0012 — CHANNEL: árbol de categorías (padre / raíz).
--
-- Estado: APLICADA (Eduardo, 2026-08-10) — sandbox yvootpbz y BD kubera
-- produccion tukwcvsi. Las 2,692 filas existentes quedaron intactas; las 3
-- columnas nacen en NULL. El backfill (backfill_categories_arbol.py) va aparte.
--
-- QUÉ HACE: agrega 3 columnas nullable a channel.categories para guardar la
-- jerarquía que hoy Competencia duplica en propuestas.skus (raiz_id,
-- raiz_nombre).
--
-- POR QUÉ VA APARTE DE LA 0011: la 0011 crea tablas nuevas y aisladas en
-- `enrich` — nadie más las toca. Ésta modifica una tabla COMPARTIDA. Separadas,
-- se puede aplicar o revertir una sin la otra.
--
-- EL CURADOR EXTERNO — verificado, es seguro. El DDL de channel.categories
-- advierte "MIGRA SOLO cuando el curador externo re-apunte (P1)"
-- (0001_esquema_v4.sql:130-131), así que antes de colgarle datos se revisó
-- quién escribe. Resultado: `etl_channel_categories.py:249-256` es un upsert
-- con columnas nombradas —`insert (channel_id, category_id, name, path)` y
-- `do update set name, path`—, sin truncate ni delete, y es el ÚNICO escritor
-- (channel_mirror.py no toca esta tabla). El backfill sobrevive a sus corridas.
--
-- PERO NO ES ONE-SHOT: las categorías NUEVAS entran por la rama de `insert`
-- con las 3 columnas en NULL y nadie las llena. Re-correr el backfill después
-- de cada corrida del cron etl-core-products (06:15 UTC).
--
-- EL BACKFILL NO USA LA API DE ML: backend/scripts/backfill_categories_arbol.py
-- lee el árbol completo que ya está descargado offline en la BD de WordPress
-- (`wp_ml_categorias`: 12,256 categorías, todas con parent_id, 31 raíces).
-- Cobertura medida: 2,692 de 2,692, el 100%. Cero llamadas a Mercado Libre.
--
-- ⚠️ NO parsear `path` para sacar la raíz: esa columna usa DOS separadores
-- distintos —`›` (U+203A) en 2,612 filas y `>` en 2— y además guarda nombres,
-- no ids. `root_id` es el que carga peso (clasifica nivel='raiz'|'hoja', que es
-- parte de la PK de enrich.market_bestsellers) y no está en el path.
--
-- GANANCIA: además de matar dos columnas duplicadas en `propuestas`, resuelve
-- el cálculo de nicho sin llamadas a ML en caliente. Hoy pos_en_raiz depende de
-- que item_categoria_id esté capturado, y solo lo está en 37 de 3,000 filas.
-- ═══════════════════════════════════════════════════════════════════════════

alter table channel.categories
  add column if not exists parent_id text,
  add column if not exists root_id   text,
  add column if not exists root_name text;

comment on column channel.categories.parent_id is
  'Categoría padre inmediata. De path_from_root (GET /categories/{id}).';
comment on column channel.categories.root_id is
  'Categoría raíz del árbol. Dueño: módulo Competencia (backfill one-shot). '
  'Verificar que el ETL de categorías use upsert por columnas nombradas antes '
  'de confiar en que sobrevive.';
comment on column channel.categories.root_name is
  'Nombre de la categoría raíz, para no resolverlo por JOIN en cada consulta.';

-- Consulta del panel: "dame las hojas de esta raíz" (nichos, pos_en_raiz).
create index if not exists idx_channel_categories_root
  on channel.categories (channel_id, root_id) where root_id is not null;

-- Las columnas nuevas heredan el grant de la tabla: no hace falta re-otorgar.
-- channel.categories ya tiene RLS activo desde 0001 (línea 697).

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN
--
--   select column_name from information_schema.columns
--    where table_schema='channel' and table_name='categories'
--      and column_name in ('parent_id','root_id','root_name');   -- 3 filas
--
--   -- Avance del backfill (paso 2 del plan):
--   select channel_id, count(*) total, count(root_id) con_raiz
--     from channel.categories group by 1;
--
--   -- Que el ETL no lo haya pisado: correr después de cada corrida del cron
--   -- etl-core-products (06:15 UTC) durante la primera semana.
-- ═══════════════════════════════════════════════════════════════════════════
