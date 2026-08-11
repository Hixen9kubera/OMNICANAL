-- ═══════════════════════════════════════════════════════════════════════════
-- 0010 — ENRICH: contenido generado por IA, por producto / canal / cuenta.
--
-- Estado: APLICADA (Eduardo, 2026-08-10) — sandbox yvootpbz y BD kubera
-- producción tukwcvsi. Tabla creada VACÍA: la siembra desde atributos_ia
-- (backfill en kubera_mirror.py) queda pendiente y se valida por separado.
-- Verificado en ambos destinos: enrich.ai_attributes tiene 0 filas.
--
-- ORIGEN: José pidió (Slack 7-ago) que el prompt se genere para todo el
-- catálogo de una vez y luego por producto al publicar. Ambos modos necesitan
-- dónde escribir y hoy no lo hay:
--
--   · MySQL atributos_ia (5,380 SKUs, 4,355 con JSON) — CONGELADA desde el
--     22-jul: no existe un solo INSERT/UPDATE contra ella en backend/. Guarda
--     SOLO atributos, y sin columna de canal (todo es ML implícito). Su
--     columna `flags` (el razonamiento de descarte de la IA) no está en
--     ningún otro lado.
--   · Metas de Woo ml_attributes (208) y ml_attr_<X> (777 por ID + 208 por
--     nombre en español) — el mismo dato bajo dos convenciones.
--   · enrich.ai_attributes (esta BD, migración 0001) — VACÍA y sin escritor:
--     no aparece en el mapa de UPSERTS de kubera_mirror.py ni en
--     KUBERA_MIRROR_TABLAS. Arrastra los dos mismos defectos: PK de un solo
--     campo y solo `attributes`.
--
-- QUÉ CAMBIA: el prompt genera CINCO campos (título, bullets, descripción,
-- atributos y backend_search_terms — este último se mide en BYTES, 249 máx.
-- en Amazon). Cuatro de los cinco no tenían dónde vivir. Esta tabla los
-- guarda completos, por canal y por cuenta.
--
-- POR QUÉ (sku, canal, cuenta) Y NO (sku, canal): en ML hay dos cuentas
-- (BEKURA y SANCORFASHION) que pueden publicar el MISMO SKU en categorías
-- distintas, y los atributos derivan de la categoría (caso EST-0091, ya
-- documentado como dos productos según la cuenta). Es la misma llave que ya
-- usan canal_inventario (sku, canal, cuenta) y channel.listings.
--
-- NO retira enrich.ai_attributes: ver el bloque del final.
-- ═══════════════════════════════════════════════════════════════════════════

create table if not exists enrich.ai_content (
  sku          citext not null references core.products(sku),

  -- OJO: los valores válidos son los ids de core.channels
  -- ('mercado_libre', 'amazon', 'walmart', 'temu', 'tiktok'), NO 'meli'.
  -- La FK lo obliga.
  canal        text   not null references core.channels(id),

  -- '' para canales de cuenta única (Walmart, Amazon hoy).
  -- No puede ser null: forma parte de la llave primaria.
  cuenta       text   not null default '',
  account_id   uuid   references core.accounts(id),

  categoria    text,          -- la categoría DEL CANAL, no la de Woo
  payload      jsonb not null,-- los 5 campos + atributos por ID nativo
  origen       jsonb,         -- por campo: woo | const | ia | calc
  flags        jsonb,         -- razonamiento de descarte de la IA
  modelo_ia    text,
  spec_version text,          -- versión del esquema del canal (Walmart 3.11 vs 4.X)

  estado       text not null default 'pendiente'
               check (estado in ('pendiente','ok','error','obsoleto')),
  error_texto  text,

  hash_woo     char(40),      -- huella del producto en Woo al generar
  generado_at  timestamptz,   -- cuándo lo produjo la IA
  updated_at   timestamptz not null default now(),

  primary key (sku, canal, cuenta)
);

-- El "córrelo para todos" es procesar los pendientes de un canal.
create index if not exists idx_ai_content_pendientes
  on enrich.ai_content (canal, estado);

-- Sin GIN no se puede preguntar "¿qué SKUs traen bullets vacíos?" sin
-- escanear la tabla completa. Con ~10,000 filas el índice es barato.
create index if not exists idx_ai_content_payload
  on enrich.ai_content using gin (payload);

comment on table enrich.ai_content is
  'Contenido generado por IA listo para enviar, por producto/canal/cuenta. '
  'NO es el historial de envíos: eso vive en ops.channel_submissions.';

comment on column enrich.ai_content.origen is
  'Por campo: woo|const|ia|calc. Es lo que permite saber qué revisar a mano.';

comment on column enrich.ai_content.hash_woo is
  'sha1 de titulo+descripcion+precio+categorias+ids_imagenes de Woo. '
  'NO incluir el updated_at de Woo: cualquier toque irrelevante marcaría '
  'todo el catálogo como obsoleto.';

comment on column enrich.ai_content.estado is
  'pendiente=por generar · ok=vigente · error=falló la IA · '
  'obsoleto=el hash_woo ya no coincide, hay que regenerar. '
  'NO existe "publicado": ese evento vive en ml_progress/amazon_progress/'
  'walmart_progress. Dos tablas diciendo lo mismo se contradicen.';

alter table enrich.ai_content enable row level security;
grant all on enrich.ai_content to service_role;


-- ═══════════════════════════════════════════════════════════════════════════
-- PENDIENTE 1 — retiro de enrich.ai_attributes
--
-- NO se dropea en esta migración a propósito: retirar una tabla es una
-- decisión aparte de crear la nueva, y así queda un commit por cosa.
--
-- CONTEO VERIFICADO el 2026-08-10 contra ambos destinos:
--   sandbox yvootpbz  -> 0 filas
--   producción tukwcvsi -> 0 filas
-- No hay nada que migrar. Se retira en una migración 0011 con:
--
--   drop table enrich.ai_attributes;
--
-- (Volver a contar antes de correrlo: si para entonces no da 0, apareció un
-- escritor y hay que investigar antes de tocar nada.)
-- ═══════════════════════════════════════════════════════════════════════════

-- ═══════════════════════════════════════════════════════════════════════════
-- PENDIENTE 2 — siembra desde MySQL atributos_ia (4,355 filas esperadas)
--
-- No se puede hacer en SQL: origen MySQL, destino Postgres. Va como función
-- de backfill en services/kubera_mirror.py, siguiendo el patrón idempotente
-- de backfill_channel_submissions(). Mapeo:
--
--   sku          → sku
--   'mercado_libre' → canal        (NO 'meli': la FK lo rechaza)
--   ''           → cuenta          (atributos_ia no distingue cuenta)
--   {"atributos": <atributos_json>} → payload
--   flags        → flags
--   modelo_ia    → modelo_ia
--   atributos_validos=1 ? 'ok' : 'pendiente' → estado
--   procesado_at → generado_at
--
-- Siembra UNO de los cinco campos: título, bullets, descripción y
-- backend_search_terms nacen vacíos y hay que generarlos igual. El valor real
-- de la siembra son los `flags`.
-- ═══════════════════════════════════════════════════════════════════════════

-- ═══════════════════════════════════════════════════════════════════════════
-- BLOQUEO CONOCIDO — la FK a core.products
--
-- Hay 82 SKUs faltantes en el maestro (mismo bloqueo que hoy tiene el backfill
-- de ops.channel_submissions; ver CLAUDE.md #8: core.products perdió su fuente
-- al desconectarse KuberaPipelineV1.0). Esos productos NO van a poder
-- registrar contenido hasta que se resuelva. Para listarlos tras la siembra:
--
--   select sku from <origen> where sku not in (select sku from core.products);
-- ═══════════════════════════════════════════════════════════════════════════

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN tras aplicar
--
--   select count(*) from enrich.ai_content;                        -- 0 al inicio
--   select canal, estado, count(*) from enrich.ai_content
--    group by canal, estado order by 1,2;
--
--   -- Contenido que se echó a perder porque el producto cambió en Woo:
--   select sku, canal, cuenta from enrich.ai_content where estado = 'obsoleto';
-- ═══════════════════════════════════════════════════════════════════════════
