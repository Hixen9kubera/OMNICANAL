-- ============================================================================
-- 0008_competencia.sql — Monitoreo de competencia en Mercado Libre (MVP)
--
-- Esquema NUEVO `competencia`, aparte de core/channel/costing/ops: esto no es
-- parte de la migración de dailytrack, es un módulo propio del panel.
--
-- Cómo correrlo:
--   Sandbox:     backend/.venv/bin/python backend/scripts/aplicar_migraciones.py
--   Producción:  Dashboard de la BD kubera → SQL Editor → pegar TODO → Run.
--
-- Es idempotente: se puede volver a correr sin romper nada.
--
-- ── SIN HISTÓRICO, A PROPÓSITO ──────────────────────────────────────────────
-- Decisión de producto: no se guarda la serie mensual. Cada corrida BORRA los
-- resultados del SKU y los reescribe. `competencia.resultados` es una FOTO del
-- mes vigente, no un historial. Si algún día se quiere la serie, se agrega una
-- tabla aparte; no se cambia el significado de esta.
--
-- ── QUÉ SE PUEDE MEDIR Y QUÉ NO (verificado contra las APIs) ────────────────
--   visitas_30d   → API de ML `/visits/items`. Funciona para publicaciones
--                   AJENAS, de a UN id por llamada. Es el dato más confiable.
--   vendidos      → NO lo da la API para items ajenos; lo trae el scraper.
--   titulo/precio/
--   imagen/url    → NO los da la API para items ajenos (`/items/{id}` = 403).
--                   Solo el scraper.
--   descripcion   → solo con el detalle del scraper, y es la descripción CORTA
--                   derivada de atributos ("Largo: 4 m | Ancho: 6 m"), no el
--                   texto largo del vendedor. No hay fuente para el largo.
--   categoria     → el scraper NO la devuelve (categoryId viene null). Sale de
--                   nuestra taxonomía (categorias_ml) o del producto de catálogo.
--   posicion      → orgánica solo por scraper (`/sites/MLM/search` = 403);
--                   de ranking de categoría por API (`/highlights`).
-- ============================================================================

create schema if not exists competencia;


-- ── Los SKUs bajo vigilancia (el MVP son 10 de Mercado Libre) ──────────────
-- `termino_general` es el eje del módulo: la búsqueda genérica con la que
-- compites por descubrimiento ("lona para exterior"), distinta del título
-- completo con el que compites de frente. Lo propone la IA y el usuario lo
-- corrige; por eso vive aquí y no se recalcula en cada corrida.
create table if not exists competencia.skus (
    sku               text primary key,
    nombre            text        not null,

    categoria_id      text,                       -- MLM162997
    categoria_nombre  text,                       -- "Tapetes"

    -- Nuestra publicación en ML. Hay una por cuenta; se guarda la de referencia
    -- para la comparación (normalmente BEKURA).
    ml_item_id        text,
    cuenta            text,

    termino_general   text,                       -- "lona para exterior"
    termino_origen    text        not null default 'ia'
                      check (termino_origen in ('ia', 'manual')),

    activo            boolean     not null default true,
    creado_en         timestamptz not null default now(),
    actualizado_en    timestamptz not null default now()
);

create index if not exists competencia_skus_categoria_idx
    on competencia.skus (categoria_id) where activo;


-- ── Corridas: una fila por corrida mensual (para saber cuándo se midió) ────
-- No es historial de resultados: es la bitácora de la ejecución del cron.
create table if not exists competencia.corridas (
    id                uuid primary key default gen_random_uuid(),
    periodo           date        not null,       -- primer día del mes medido
    origen            text        not null default 'cron'
                      check (origen in ('cron', 'manual')),
    estado            text        not null default 'corriendo'
                      check (estado in ('corriendo', 'listo', 'error')),

    skus_medidos      integer     not null default 0,
    resultados        integer     not null default 0,
    visitas_ok        integer     not null default 0,
    costo_apify_usd   numeric(10,4),

    error             text,
    avisos            jsonb       not null default '[]'::jsonb,
    creado_en         timestamptz not null default now(),
    terminado_en      timestamptz
);

create index if not exists competencia_corridas_periodo_idx
    on competencia.corridas (periodo desc);


-- ── Resultados de la foto vigente ─────────────────────────────────────────
-- Tres tipos de medición por SKU, que son las tres preguntas del módulo:
--   'general'   → ¿dónde estoy en la búsqueda genérica? (descubrimiento)
--   'titulo'    → ¿dónde estoy contra mi competencia directa?
--   'categoria' → ¿quiénes son los mejores de mi categoría? (ranking oficial)
create table if not exists competencia.resultados (
    id                uuid primary key default gen_random_uuid(),

    sku               text        not null
                      references competencia.skus (sku) on delete cascade,
    tipo              text        not null
                      check (tipo in ('general', 'titulo', 'categoria')),
    termino           text,                       -- el término buscado; null en 'categoria'
    periodo           date        not null,

    posicion          integer,                    -- 1-based dentro de su tipo
    externo_id        text        not null,       -- MLM123456789
    titulo            text,
    descripcion       text,                       -- corta, derivada de atributos
    precio            numeric(14,2),
    moneda            text        not null default 'MXN',
    imagen            text,
    url               text,
    seller            text,
    marca             text,
    categoria_id      text,
    categoria_nombre  text,

    visitas_30d       integer,                    -- API de ML
    vendidos          integer,                    -- del scraper
    reviews           integer,
    rating            numeric(3,2),
    envio_gratis      boolean,
    es_full           boolean,

    -- ¿Es una publicación NUESTRA? Se marca cruzando contra ml_progress. Es lo
    -- que responde "¿dónde estoy en el ranking?".
    es_nuestro        boolean     not null default false,
    sku_nuestro       text,                       -- qué SKU nuestro es, si aplica

    capturado_en      timestamptz not null default now()
);

-- Una publicación no se repite dentro de la misma medición de un SKU.
create unique index if not exists competencia_resultados_uq
    on competencia.resultados (sku, tipo, externo_id);

create index if not exists competencia_resultados_sku_idx
    on competencia.resultados (sku, tipo, posicion);

create index if not exists competencia_resultados_nuestro_idx
    on competencia.resultados (sku, tipo) where es_nuestro;


-- ── Vista: "¿dónde estoy?" resumido por SKU y tipo ────────────────────────
-- Responde de un query la pregunta del tab: mi posición, cuántos hay, y cómo
-- se ve mi precio contra la mediana de esa búsqueda.
create or replace view competencia.posiciones as
select
    r.sku,
    r.tipo,
    r.termino,
    r.periodo,
    count(*)                                            as total_resultados,
    min(r.posicion) filter (where r.es_nuestro)         as mi_posicion,
    max(r.precio)   filter (where r.es_nuestro)         as mi_precio,
    max(r.visitas_30d) filter (where r.es_nuestro)      as mis_visitas_30d,
    percentile_cont(0.5) within group (order by r.precio)
        filter (where not r.es_nuestro)                 as precio_mediana_rivales,
    max(r.visitas_30d) filter (where not r.es_nuestro)  as visitas_max_rival
from competencia.resultados r
group by r.sku, r.tipo, r.termino, r.periodo;
