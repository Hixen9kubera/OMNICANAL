-- ═══════════════════════════════════════════════════════════════════════════
-- 0016 — `enrich.channel_content`: el contenido editorial POR CANAL.
--
-- Estado: NO APLICADA. Sandbox primero (aplicar_migraciones.py), producción
-- solo con el visto de Eduardo.
--
-- QUÉ RESUELVE
-- ------------
-- El panel ya sabe GENERAR contenido por canal (`ia_generadores.GENERADORES`:
-- 6 tipos para Amazon, 3 para ML, 1 para TikTok) y NO sabe guardarlo:
-- `POST /api/ia/generar` devuelve el texto y ahí muere. El único botón de
-- guardar del Estudio (`POST /api/productos/{sku}/contenido`) escribe a
-- WooCommerce y **no recibe canal**.
--
-- Consecuencia medida: si no publicas en la misma sesión, lo que generaste se
-- pierde. Y `item_highlights` se perdía SIEMPRE (se cableó aparte, ver
-- publicar_ready.py).
--
-- POR QUÉ SE RETIRA `enrich.ai_content` EN VEZ DE REUSARLA
-- --------------------------------------------------------
-- La creé el 10-ago (migración 0010) suponiendo que este contenido sería de
-- IA. Al leer los publicadores resultó falso: el contenido es MEZCLADO —
-- copiado de Woo, escrito a mano, constante del código, o generado. Su propia
-- columna `origen` (woo|const|ia|calc) ya lo anticipaba; el nombre no.
--
-- Reusarla dejaría `flags`, `modelo_ia`, `error_texto` y `generado_at` vacías
-- en una tabla que sobre todo NO es de IA. Eso es exactamente cómo nacieron
-- `core.products.parent_sku` y `has_variations`: columnas muertas que alguien
-- usó creyéndolas vivas, y salieron 74 de 292 filas falsas en un reporte de
-- Inmovilizado (README:5320).
--
-- Verificado el 12-ago antes de escribir esto:
--   · enrich.ai_content     -> 0 filas en producción (tukwcvsi)
--   · enrich.ai_attributes  -> 0 filas
--   · 0 lectores en backend/ (grep sin resultados fuera de la migración)
-- No hay nada que migrar. El costo de corregirla es cero HOY y crece con la
-- primera fila.
-- ═══════════════════════════════════════════════════════════════════════════

drop table if exists enrich.ai_content;

-- `ai_attributes` (migración 0001) arrastra los mismos defectos y también está
-- vacía: PK de un solo campo y solo `attributes`. Nunca tuvo escritor: no
-- aparece en el mapa de UPSERTS de kubera_mirror.py. Se retira aquí.
drop table if exists enrich.ai_attributes;


create table enrich.channel_content (
  sku          citext not null references core.products(sku),

  -- Ids válidos de core.channels: amazon · general · mercado_libre · shein ·
  -- temu · tiktok · walmart. NO 'meli' — la FK lo rechaza.
  canal        text   not null references core.channels(id),

  -- '' para canales de cuenta única (Amazon, Walmart, TikTok hoy). No puede
  -- ser null: es parte de la llave.
  --
  -- POR QUÉ LA CUENTA VA EN LA LLAVE: en ML hay dos cuentas (BEKURA y
  -- SANCORFASHION) que pueden publicar el MISMO SKU en categorías distintas, y
  -- el contenido deriva de la categoría. Caso real EST-0091: es DOS productos
  -- según la cuenta (CLAUDE.md, pendiente 7).
  cuenta       text   not null default '',
  account_id   uuid   references core.accounts(id),

  categoria    text,          -- la categoría DEL CANAL, no la de Woo

  -- El documento del canal. Su forma la decide cada canal y CAMBIA con ellos:
  -- Amazon usa titulo/highlights/bullets/descripcion/atributos, ML usa
  -- titulo/ficha/descripcion, y de Walmart y TikTok todavía no se sabe. Por eso
  -- es jsonb y no columnas: no hay que decidir hoy lo que aún no se conoce.
  --
  -- Las LLAVES son los nombres CANÓNICOS del panel (titulo, descripcion,
  -- bullets, highlights, atributos…), no los nativos del canal. La traducción a
  -- `item_name` / `productName` / `goodsName` vive en el publicador.
  contenido    jsonb  not null default '{}'::jsonb,

  -- Por campo: woo | ia | manual | calc. Es lo que permite al panel decir qué
  -- revisar a mano y qué se generó solo.
  origen       jsonb,

  -- Versión del esquema del canal cuando se guardó (Walmart publica con 3.11
  -- aunque la spec publicada sea 3.19). Fuera de la PK a propósito: es un dato
  -- del guardado, no otra identidad del producto.
  spec_version text,

  -- sha1 del producto en Woo al guardar (titulo+descripcion+precio+categorias+
  -- ids_imagenes). Sirve para saber si el contenido se echó a perder porque el
  -- producto cambió. NO incluir el updated_at de Woo: cualquier toque
  -- irrelevante marcaría todo el catálogo como viejo.
  hash_base    char(40),

  updated_at   timestamptz not null default now(),

  primary key (sku, canal, cuenta)
);

-- "Dame todo lo guardado de este canal" — el barrido del publicador en lote.
create index idx_channel_content_canal on enrich.channel_content (canal);

-- Sin GIN no se puede preguntar "¿qué SKUs tienen bullets vacíos?" sin escanear
-- la tabla entera.
create index idx_channel_content_contenido
  on enrich.channel_content using gin (contenido);

comment on table enrich.channel_content is
  'Contenido editorial por producto/canal/cuenta: lo que el panel edita ANTES '
  'de publicar. NO es el historial de envíos (eso es ops.channel_submissions) '
  'ni el estado de la publicación (eso es channel.listings).';

comment on column enrich.channel_content.contenido is
  'Llaves CANÓNICAS del panel (titulo, descripcion, bullets, highlights, '
  'atributos), no las nativas del canal. La traducción vive en el publicador.';

comment on column enrich.channel_content.origen is
  'Por campo: woo|ia|manual|calc. Es lo que permite saber qué revisar a mano.';

alter table enrich.channel_content enable row level security;
grant all on enrich.channel_content to service_role;


-- ═══════════════════════════════════════════════════════════════════════════
-- LO QUE ESTA MIGRACIÓN NO HACE, A PROPÓSITO
--
-- · NO hay columna `estado`. La validez del contenido ("¿está completo para
--   publicar?") se calcula comparando contra los requisitos del canal, no se
--   guarda: un estado guardado se contradice con la realidad en cuanto el
--   canal cambia su plantilla.
-- · NO hay `actualizado_por`. La API todavía no tiene auth real (CLAUDE.md,
--   pendiente 9), así que nadie podría llenarla. Se agrega cuando haya identidad.
-- · NO hay `grano` (producto vs variante). Hoy todos los canales que publican
--   lo hacen por variante. Se agrega cuando entre Amazon parent/child, no antes.
--
-- Las tres son columnas que HOY nacerían vacías. Ver el bloque de arriba sobre
-- parent_sku.
-- ═══════════════════════════════════════════════════════════════════════════

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN tras aplicar
--   select count(*) from enrich.channel_content;                       -- 0
--   select canal, count(*) from enrich.channel_content group by 1;
--   select to_regclass('enrich.ai_content'), to_regclass('enrich.ai_attributes');
--                                                                      -- null, null
--
-- ROLLBACK
--   drop table enrich.channel_content;
--   -- y re-aplicar 0010 si se quisiera ai_content de vuelta (estaba vacía).
-- ═══════════════════════════════════════════════════════════════════════════
