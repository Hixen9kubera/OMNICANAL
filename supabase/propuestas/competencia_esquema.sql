-- ============================================================================
-- PROPUESTA · esquema `competencia` — PENDIENTE DE APROBACIÓN DEL EQUIPO
--
-- NO APLICAR todavía. Este archivo es el borrador que sale del MVP que corre en
-- local sobre SQLite (`backend/competencia.db`). Se propone para que el módulo de
-- Competencia deje de vivir en un archivo local y pase a la BD kubera, que es de
-- donde el panel leerá en producción.
--
-- ⚠️ ANTES DE APLICAR, VERIFICAR SI EL ESQUEMA YA EXISTE.
--    Una versión anterior de este archivo vivió como
--    `supabase/migrations/0008_competencia.sql` (commit ece6306) y NO se pudo
--    confirmar si `aplicar_migraciones.py` la alcanzó a aplicar en la BD kubera.
--    Si el esquema `competencia` YA existe, todos los `create table if not exists`
--    de abajo son NO-OP y las columnas nuevas (id_pagina, tipo,
--    item_categoria_id/nombre, reviews en rankings_categoria) NO se agregarían:
--    habría que aplicarlas con ALTER TABLE. Comprobar con:
--      select table_name from information_schema.tables
--       where table_schema = 'competencia';
--
-- Vive en `supabase/propuestas/` y NO en `supabase/migrations/` a propósito:
-- `scripts/aplicar_migraciones.py` hace `glob("*.sql")` sobre migrations/ y
-- aplicaría este archivo sin revisión si estuviera ahí. Al aprobarse se mueve a
-- `supabase/migrations/0010_competencia.sql`.
--
-- ── QUÉ LEE DE LO QUE YA EXISTE (no duplica nada) ───────────────────────────
--   core.products            → identidad del SKU
--   core.accounts            → la tienda (legacy_code: BEKURA / SANCORFASHION)
--   channel.listings         → nuestras publicaciones (+ category_id/store_name,
--                              las columnas agregadas en 0009)
--   channel.categories       → el árbol; `path` agrupa la tabla por nivel
--   channel.product_category → la asignación sku → categoría ("el panel manda")
--   channel.orders/order_items → unidades vendidas del periodo (cantidad + fecha)
--   channel.listings.price     → el precio de LISTA de la publicación
--   ⚠️ listings.price es el precio de LISTA. 8 de 16 publicaciones de la muestra
--      venden por debajo (MUE-0163-TEL: $290 contra $989 de lista, −71%). El precio
--      real está en /items/{id}/sale_price?context=channel_marketplace.
--
-- ── QUÉ ES NUEVO Y POR QUÉ NO CABE EN LO EXISTENTE ──────────────────────────
--   El módulo mide TRES cosas por SKU que hoy nadie guarda:
--     'general'   → posición en la búsqueda genérica (descubrimiento)
--     'titulo'    → posición contra el mismo producto (competencia directa)
--     'categoria' → el top de más vendidos de la categoría
--   Y guarda publicaciones AJENAS (de competidores), que no son nuestras y por eso
--   no pertenecen a channel.listings.
--
-- ── DECISIONES QUE CONVIENE DISCUTIR ────────────────────────────────────────
--   1. SIN HISTÓRICO (decisión de producto): cada corrida BORRA la anterior.
--      `resultados` es la foto del mes, no una serie. Si el equipo quiere serie,
--      el cambio es un solo método del store, pero hay que decidirlo antes de
--      acumular volumen: ~25 filas × 3 mediciones × N SKUs por mes.
--   2. Periodicidad MENSUAL, disparada por cron de Railway
--      (`backend/railway.competencia.json`), no por el scheduler embebido.
--   3. El raspado corre con NAVEGADOR LOCAL (Selenium + BeautifulSoup), no con
--      actores de Apify (decisión de José, 3-ago): el actor se paga por cómputo y
--      su normalización tira `id_pagina`, la llave para unir la ficha raspada con
--      la posición oficial de /highlights. Escala: 988 subcategorías únicas + 26
--      raíces = 1,014 páginas al mes, <$5 USD de proxy residencial.
--   4. Hoy `resultados` y `corridas` están VACÍAS: la vista se apoya en
--      rankings_categoria y terminos_categoria. Decidir si se completan las tres
--      mediciones por SKU o se retiran del esquema antes de aplicarlo.
-- ============================================================================

create schema if not exists competencia;


-- ── Los SKUs bajo vigilancia ────────────────────────────────────────────────
-- `termino_general` es el eje del módulo: la búsqueda amplia con la que compites
-- por descubrimiento ("lona para exterior"), distinta del título con el que
-- compites de frente. Lo propone un LLM y una persona lo corrige; `termino_origen`
-- protege la corrección humana de ser pisada por la siguiente corrida.
create table if not exists competencia.skus (
    sku              citext primary key references core.products (sku),
    termino_general  text,
    termino_origen   text not null default 'ia' check (termino_origen in ('ia','manual')),
    activo           boolean not null default true,
    creado_en        timestamptz not null default now(),
    actualizado_en   timestamptz not null default now()
);
-- Nombre, categoría y ruta NO se copian aquí: salen de core.products y
-- channel.product_category/categories por JOIN. Duplicarlos los haría divergir.


-- ── Métricas de NUESTRAS publicaciones, por publicación ─────────────────────
-- Una fila por (sku, cuenta, canal). El canal está desde el principio para que un
-- ASIN de Amazon sea otra fila y no un rediseño.
--
-- Por qué no viven en channel.listings: son métricas de un PERIODO (30 días
-- móviles) y esa tabla es el estado actual del listing. Mezclarlas obligaría a
-- versionarla.
create table if not exists competencia.publicacion_metricas (
    sku            citext not null references core.products (sku),
    cuenta         text   not null,          -- core.accounts.legacy_code
    canal          text   not null default 'mercado_libre' references core.channels (id),
    listing_id     text   not null,          -- MLM… | ASIN
    periodo        date   not null,          -- primer día del mes medido
    -- precio NO se guarda aquí: ya vive en channel.listings.price, que está
    -- poblado (8/8 en la muestra) y empata con lo que devuelve la API. Se lee por
    -- JOIN (sku, store_name, canal). Duplicarlo lo haría divergir del listing.
    visitas_30d    integer,                  -- API de ML, funciona para cualquier item
    unidades_30d   integer,                  -- de channel.orders/order_items
    -- conversion NO se guarda: es unidades/visitas y se calcula al leer. Con 0
    -- visitas es INDEFINIDA, no 0% — guardarla invitaría a pintar un cero falso.
    actualizado_en timestamptz not null default now(),
    primary key (sku, cuenta, canal, periodo)
);


-- ── Corridas: la bitácora de cada medición ─────────────────────────────────
create table if not exists competencia.corridas (
    id             uuid primary key default gen_random_uuid(),
    periodo        date not null,
    origen         text not null default 'cron' check (origen in ('cron','manual')),
    estado         text not null default 'corriendo'
                   check (estado in ('corriendo','listo','error')),
    skus_medidos   integer not null default 0,
    resultados     integer not null default 0,
    visitas_ok     integer not null default 0,
    costo_apify_usd numeric(10,4),
    fuente_unidades text,                    -- 'supabase' | 'ml_api' | 'ninguna'
    error          text,
    avisos         jsonb not null default '[]'::jsonb,
    creado_en      timestamptz not null default now(),
    terminado_en   timestamptz
);


-- ── Resultados: la foto vigente (publicaciones nuestras Y ajenas) ──────────
create table if not exists competencia.resultados (
    id           uuid primary key default gen_random_uuid(),
    sku          citext not null references competencia.skus (sku) on delete cascade,
    tipo         text   not null check (tipo in ('general','titulo','categoria')),
    termino      text,                       -- lo buscado; null en 'categoria'
    periodo      date   not null,
    posicion     integer,
    externo_id   text   not null,            -- MLM… del competidor
    titulo       text,
    descripcion  text,                       -- CORTA, derivada de atributos
    precio       numeric(14,2),
    imagen       text,
    url          text,
    seller       text,
    marca        text,
    visitas_30d  integer,
    vendidos     integer,
    reviews      integer,
    rating       numeric(3,2),
    envio_gratis boolean,
    es_full      boolean,
    es_nuestro   boolean not null default false,
    sku_nuestro  citext,
    capturado_en timestamptz not null default now(),
    unique (sku, tipo, externo_id)
);

create index if not exists competencia_resultados_sku_idx
    on competencia.resultados (sku, tipo, posicion);
create index if not exists competencia_resultados_nuestro_idx
    on competencia.resultados (sku, tipo) where es_nuestro;


-- ── Ranking de más vendidos POR CATEGORÍA ───────────────────────────────────
-- Va por CATEGORÍA y no por SKU: los 3 SKUs de Tapetes comparten MLM162997 y las
-- 7 autopartes comparten la raíz MLM1747. Guardarlo por SKU lo duplicaría.
--
-- `nivel` distingue la categoría RAÍZ del path (Accesorios para Vehículos) de la
-- HOJA (Tapetes): son los dos niveles que pinta la vista.
create table if not exists competencia.rankings_categoria (
    categoria_id   text not null,             -- MLM… (channel.categories.id)
    nivel          text not null check (nivel in ('raiz','hoja')),
    periodo        date not null,
    posicion       integer not null,          -- del badge oficial "1º MÁS VENDIDO"
    -- El item REAL de la publicación mostrada. Sale del `#wid=` del href de la
    -- tarjeta, y es el id con el que SÍ funcionan /visits y /reviews.
    externo_id     text not null,
    -- El id que va en el URL (/up/MLMU…, /p/MLM…, articulo…/MLM-…): es EXACTAMENTE
    -- el que devuelve /highlights, así que es la llave para unir la ficha raspada
    -- con la posición oficial de la API.
    id_pagina      text,
    tipo           text check (tipo in ('ITEM','PRODUCT','USER_PRODUCT')),
    titulo         text,
    precio         numeric(14,2),             -- el que se paga (con descuento)
    precio_lista   numeric(14,2),             -- precio base, antes del descuento
    descuento      text,                      -- '58% OFF'
    -- COTA INFERIOR: ML redondea ("+50mil vendidos" → 50000). Sirve para ordenar
    -- nichos, no como cifra exacta. No sumarla como si fuera precisa.
    vendidos       integer,
    rating         numeric(3,2),
    reviews        integer,
    seller         text,
    imagen         text,
    url            text,
    visitas_30d    integer,                   -- /items/{id}/visits/time_window
    -- La SUBCATEGORÍA de esta fila. Solo se llena en el ranking de la raíz, y es lo
    -- que permite decir "la subcategoría X es la #1 de toda la categoría padre".
    -- Sale de /products/{id}/items; en las entradas de tipo ITEM queda NULL porque
    -- /items de un ajeno responde 403 y no hay otra ruta.
    item_categoria_id     text,
    item_categoria_nombre text,
    es_nuestro     boolean not null default false,
    sku_nuestro    citext,
    capturado_en   timestamptz not null default now(),
    primary key (categoria_id, nivel, externo_id)
);

create index if not exists competencia_rank_cat_idx
    on competencia.rankings_categoria (categoria_id, nivel, posicion);


-- ── Términos que la gente ESCRIBE en el buscador, por categoría ─────────────
-- De GET /trends/MLM/{cat}, ordenados por volumen. Es el insumo del "término
-- general": cruzados contra nuestros títulos dicen qué palabras nos faltan para
-- capturar tráfico.
--
-- ML NO publica términos de toda categoría: Bujías (MLM179785) y Cartuchos de
-- Turbo (MLM458946) responden 404, y son exactamente las mismas cuyo /highlights
-- también viene vacío. AUSENCIA DE FILAS = ML no tiene el dato, NO es un fallo de
-- captura. Quien lea esta tabla debe distinguir los dos casos: llevan a acciones
-- opuestas (reintentar vs. no hay nada que traer).
create table if not exists competencia.terminos_categoria (
    categoria_id text not null,
    periodo      date not null,
    posicion     integer not null,            -- 1 = el más buscado
    termino      text not null,
    url          text,
    capturado_en timestamptz not null default now(),
    primary key (categoria_id, termino)
);

create index if not exists competencia_term_cat_idx
    on competencia.terminos_categoria (categoria_id, posicion);
