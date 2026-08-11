# Consolidar Competencia: eliminar el esquema `propuestas` — v2

**Revisión:** 2026-08-10 · **Cambios respecto a v1:** 5 correcciones de diseño
tras revisión del consejo (claude-opus, claude-sonnet, claude-haiku; gemini-3-pro
falló por autenticación).

> Lo que NO cambió: el orden de trabajo, el alcance del entregable, y todo el
> trabajo de medición. Los cinco cambios son de DDL, nombres y verificación.

---

## Resumen de los 5 cambios

| # | Cambio | Por qué |
|---|---|---|
| 1 | `canal` en la PK de las 4 tablas nuevas de `enrich` | Sin él, el 4º canal obliga a rehacer las tablas. Es el defecto que ya retiró `atributos_ia` y `enrich.ai_attributes` |
| 2 | Las 5 métricas salen de `channel.listings` → `enrich.market_listing_metrics` | Recupera la dimensión `periodo` que v1 perdía; `visits_30d` no existe en Amazon |
| 3 | RLS + grants explícitos, migraciones numeradas, manifiesto regenerado | El DDL de v1 los omitía; el manifiesto ya está roto hoy |
| 4 | El `drop` no va el mismo día + diff sobre todos los endpoints | El `drop cascade` es irreversible y v1 verificaba un solo endpoint |
| 5 | Prefijo `market_` en las 5 tablas y las 2 vistas | `search_results` a secas es demasiado genérico para un `enrich` compartido que va a crecer |

---

## CAMBIO 1 — `canal` en las 4 tablas de `enrich`

**El caso**, en corto: el propio esquema que se va a borrar ya lo tenía resuelto.
`competencia_esquema.sql:84-85` dice textual:

> *"Una fila por (sku, cuenta, canal). El canal está desde el principio para que
> un ASIN de Amazon sea otra fila y no un rediseño."*

Y el router que el plan **conserva** ya recibe `canal` como parámetro y documenta
que "cuando entre Amazon, sus ASINs ya vienen como filas con `canal='amazon'`".
La capa de lectura ya sabe filtrar por canal; el almacenamiento de v1 no tenía
de dónde.

Costo hoy: una columna, con las tablas vacías, sin mover una sola fila.
Costo después: `ALTER` + backfill + cambio de PK sobre tablas vivas + reescribir
las 2 vistas + cada query.

**Matiz honesto que levantó el consejo:** las 3 tablas de competidor son
legítimamente *de forma* Mercado Libre (`categoria_id` es un MLM, la posición
sale del badge de `/highlights`). Un "bestsellers de Amazon" probablemente sea
otra tabla con otras columnas, no una fila con `canal='amazon'`. Aun así, sin
`canal` no se puede ni filtrar ni coexistir, y la columna cuesta cero hoy.
`market_sku_config` es el caso sin defensa: un término de búsqueda de Amazon no
es el de ML.

```sql
-- Más vendidos por categoría (competencia). 19 columnas de 23.
create table enrich.market_bestsellers (
  canal        text not null default 'mercado_libre' references core.channels(id),
  categoria_id text not null,
  nivel        text not null,               -- 'raiz' | 'hoja'
  posicion     int  not null,
  externo_id text, id_pagina text, tipo text,
  titulo text, precio numeric, precio_lista numeric,
  vendidos int, rating numeric, reviews int, seller text,
  imagen text, url text, visitas_30d int,
  item_categoria_id text, item_categoria_nombre text,
  es_nuestro boolean default false,
  sku_nuestro citext references core.products(sku),
  capturado_en timestamptz default now(),
  primary key (canal, categoria_id, nivel, posicion)
);

-- SERP por término general. 12 columnas de 17.
create table enrich.market_search_results (
  canal      text not null default 'mercado_libre' references core.channels(id),
  termino    text not null,
  externo_id text not null,
  posicion int, titulo text, precio numeric, imagen text, url text,
  seller text, rating numeric,
  es_nuestro boolean default false,
  sku_nuestro citext references core.products(sku),
  capturado_en timestamptz default now(),
  primary key (canal, termino, externo_id)
);

-- Términos más buscados por categoría (de /trends). 4 columnas de 6.
create table enrich.market_terms (
  canal        text not null default 'mercado_libre' references core.channels(id),
  categoria_id text not null,
  posicion     int  not null,
  termino      text not null,
  capturado_en timestamptz default now(),
  primary key (canal, categoria_id, posicion)
);

-- Config del SKU para Competencia. 5 columnas de 13.
create table enrich.market_sku_config (
  sku    citext not null references core.products(sku),
  canal  text   not null default 'mercado_libre' references core.channels(id),
  termino_general   text,
  termino_origen    text,          -- 'ia' | 'manual'
  activo            boolean not null default true,
  categoria_id_real text,          -- ver nota de gobernanza
  updated_at        timestamptz default now(),
  primary key (sku, canal)
);
```

> Nota: `sku` pasa a `citext` (era `text` en v1) para empatar con
> `core.products.sku`, que es `citext`. Con `text` la FK funciona pero las
> comparaciones en las vistas serían case-sensitive contra una tabla que no lo es.

---

## CAMBIO 2 — las 5 métricas salen de `channel.listings`

### Lo que v1 proponía y por qué se retira

```sql
-- YA NO: alter table channel.listings add column title, sale_price,
--        visits_30d, units_30d, metrics_updated_at
```

**El análisis de seguridad de v1 es correcto y fue verificado por los tres
consejeros**: `channel_mirror.py` usa listas explícitas de columnas en el
`insert` y en el `on conflict do update set` (líneas 104-136 y 239-244), y el
trigger `fn_listing_history` solo audita `price`, `stock_own`, `stock_full`,
`is_fulfillment`, `status` y `situacion`. Las columnas nuevas sobrevivirían al
sync de 15 minutos. Eso es cierto.

Se retira por tres razones distintas:

1. **Se pierde la dimensión de periodo.** `competencia_esquema.sql:88-89` ya
   explicaba por qué no deben vivir ahí: *"son métricas de un PERIODO (30 días
   móviles) y esa tabla es el estado actual del listing. Mezclarlas obligaría a
   versionarla."* La tabla vieja tenía `periodo date` en la PK. v1 colapsaba esa
   dimensión a "último mes", de forma permanente.
2. **`visits_30d`/`units_30d` no tienen equivalente en Amazon.** El propio
   `COMPETENCIA_README.md` documenta que Amazon no expone visitas de competencia
   por ninguna vía — el sustituto es BSR + Buy Box. No es que queden en NULL: son
   conceptos que no existen. Ponerles nombre genérico en la tabla canónica
   multicanal invita a que alguien los llene mal.
3. **La convención "una fuente por campo" es prosa, no restricción.** Existe
   `backend/scripts/etl_channel_listings.py`, que hace `truncate channel.listings`
   (línea 162). Hoy **no puede** correr contra producción — su `candado_destino()`
   aborta (línea 55) y no está en ningún cron — así que el riesgo inmediato es
   bajo. Pero su lista de columnas **ya está desactualizada**: le faltan
   `category_id`, `store_name` (migración 0009) y `logistic_type`, `stock_fba`,
   `currency` (0004). Alguien agregó columnas y nadie actualizó ese escritor.
   Es la prueba de que la convención depende de que nadie se distraiga.

### Lo que va en su lugar

```sql
create table enrich.market_listing_metrics (
  sku        citext not null references core.products(sku),
  canal      text   not null references core.channels(id),
  cuenta     text   not null default '',    -- core.accounts.legacy_code
  periodo    date   not null,               -- primer día del mes medido
  account_id uuid references core.accounts(id),
  listing_id text,                          -- MLM… | ASIN
  title       text,
  sale_price  numeric,                      -- precio CON descuento
  visits_30d  int,                          -- ML: /items/{id}/visits. NULL donde no aplique
  units_30d   int,
  metrics_updated_at timestamptz default now(),
  primary key (sku, canal, cuenta, periodo)
);

create index idx_market_listing_metrics_periodo on enrich.market_listing_metrics (canal, periodo desc);
```

> **Sobre `periodo` y la decisión "sin histórico":** la columna no obliga a
> guardar histórico. Si la decisión sigue siendo que cada corrida reemplaza, el
> proceso borra los periodos viejos al terminar. Pero la columna deja la puerta
> abierta a una serie de tendencia sin rediseñar nada — que es justo lo que el
> esquema viejo ya contemplaba.

**Lo que esto cuesta:** un JOIN más en las vistas
(`channel.listings` ⋈ `enrich.market_listing_metrics` por `sku, canal, cuenta`). A
cambio, Competencia deja de ser co-escritor de la tabla más caliente del esquema
del equipo, y `channel.listings` no gana 5 columnas que solo un módulo usa.

### `channel.categories` — VERIFICADO, y el backfill sale gratis

Las 3 columnas que v1 propone (`parent_id`, `root_id`, `root_name`) se
mantienen. El consejo pedía verificar cómo escribe el ETL antes de colgarles
datos, porque el DDL de esa tabla advierte que tiene un curador externo
(*"MIGRA SOLO cuando el curador externo re-apunte (P1)"*, `0001:130`).

**Verificado el 2026-08-10 — es seguro.** `etl_channel_categories.py:249-256` es
un upsert con columnas nombradas:

```sql
insert into channel.categories (channel_id, category_id, name, path)
values %s
on conflict (channel_id, category_id) do update set
  name = excluded.name, path = excluded.path
where (categories.name, categories.path) is distinct from (excluded.name, excluded.path)
```

El `insert` nombra 4 columnas —ninguna de las nuestras— y el `do update set`
solo toca `name` y `path`. Ni `truncate` ni `delete`. Y es el **único** escritor:
`channel_mirror.py` no toca esa tabla.

**Pero el backfill NO es one-shot.** Las categorías nuevas entran por la rama de
`insert` con las 3 columnas en NULL, y nadie las llena. Hay que re-correr el
backfill después de cada corrida del cron `etl-core-products` (06:15 UTC), o al
menos periódicamente.

### El backfill no necesita la API de Mercado Libre

v1 planeaba *"una pasada por `GET /categories/{id}`"* para ~988 categorías. Dos
correcciones medidas:

| | v1 estimaba | Medido |
|---|---|---|
| Categorías a resolver | ~988 | **2,692** |
| Llamadas a la API de ML | ~988 | **0** |

El árbol completo de Mercado Libre **ya está descargado offline** en la BD de
WordPress: `wp_ml_categorias`, 12,256 categorías, **todas con `parent_id`**,
31 raíces (`parent_id=''`), profundidad máxima 7. La mantiene un snippet de
WordPress, no el panel.

Cobertura medida: **2,692 de 2,692, el 100%**. `root_id` se calcula subiendo por
`parent_id` hasta la raíz, en memoria.

Script: `backend/scripts/backfill_categories_arbol.py`. Dry-run por default,
`--real` exige `--acepto-destino <ref8>`, watchdog de 10 min, solo UPDATE de lo
que cambió, y **reporta las categorías no cubiertas en vez de asumir cero** —
porque ese árbol lo mantiene un proceso ajeno y puede quedarse viejo.

Aplicado y verificado en sandbox: 2,692 filas con raíz, 30 raíces distintas,
segunda corrida idempotente (0 cambios).

> **No parsear `path` para sacar la raíz.** Se intentó y falla: esa columna usa
> **dos separadores distintos** — `›` (U+203A) en 2,612 filas y `>` en 2 —, y
> además guarda nombres, no ids. `root_id` es el que carga peso (clasifica
> `nivel='raiz'|'hoja'`, que es parte de la PK de `market_bestsellers`), y ése
> no está en el path de ninguna forma.

---

## CAMBIO 3 — RLS, grants, migraciones numeradas y el manifiesto

### RLS y grants (faltaban por completo en v1)

El patrón del repo es tabla por tabla — `0001_esquema_v4.sql:688-719` y
`0010_enrich_ai_content.sql:97-98`. Existe un
`alter default privileges … grant all on tables to service_role`, pero **solo
aplica si las tablas las crea el mismo rol** que corrió esa sentencia. Si la
migración corre con otro owner, las tablas nacen sin grant y el backend recibe
`permission denied`. No confiar en él:

```sql
alter table enrich.market_bestsellers enable row level security;
alter table enrich.market_search_results       enable row level security;
alter table enrich.market_terms       enable row level security;
alter table enrich.market_sku_config    enable row level security;
alter table enrich.market_listing_metrics      enable row level security;

grant all on enrich.market_bestsellers to service_role;
grant all on enrich.market_search_results       to service_role;
grant all on enrich.market_terms       to service_role;
grant all on enrich.market_sku_config    to service_role;
grant all on enrich.market_listing_metrics      to service_role;
```

Las columnas nuevas en `channel.categories` heredan el grant de su tabla: no
necesitan nada.

### Índices que v1 no reprodujo

El esquema viejo tenía índices parciales (`…_nuestro_idx where es_nuestro`,
`competencia_esquema.sql:157-158`) que las vistas usan constantemente para armar
la corona:

```sql
create index idx_market_bestsellers_nuestro on enrich.market_bestsellers (sku_nuestro)
  where es_nuestro;
create index idx_market_search_nuestro on enrich.market_search_results (sku_nuestro)
  where es_nuestro;
create index idx_market_sku_config_activo on enrich.market_sku_config (canal)
  where activo;
```

### Migraciones numeradas + manifiesto

Todo el DDL entra como **migraciones numeradas** en `supabase/migrations/`
(la 0010 ya está tomada por `enrich.ai_content`; siguen 0011 y 0012), y el
`drop schema propuestas` va en la suya, aparte.

**Advertencia medida:** `schema_manifest.json` **ya está desactualizado hoy**,
sin relación con este plan. Lo confirmé corriendo
`aplicar_migraciones.py --verificar-solo`: devuelve `CON DIFERENCIAS` y sale con
código 1. Le faltan las columnas de 0004 y 0009 en `channel.listings` y no tiene
`enrich.ai_content`. **Regenerar el manifiesto es prerrequisito del paso 1**, no
un pendiente — si no, quien reconstruya el sandbox después no va a saber si el
fallo es suyo o heredado.

**Pendiente a resolver antes del drop:** `competencia_esquema.sql:9-23` advierte
que una versión de este esquema vivió como `0008_competencia.sql` y no se
confirmó si `aplicar_migraciones.py` llegó a aplicarla. Hay que verificar si
`propuestas` está trackeada como migración antes de borrarla, o el sandbox la
va a recrear.

---

## CAMBIO 4 — el drop no va el mismo día

### Verificación ampliada

v1 verificaba `GET /vista?canal=mercado_libre`. El router expone ~18 rutas con
código de lectura propio que **no pasa por `/vista`**. Un diff de un endpoint no
prueba que `/sku/{sku}` o `/subcategoria/{id}/terminos` sigan idénticos.

Antes de migrar, capturar la referencia de **todos** los endpoints de lectura:

```bash
for ruta in "vista?canal=mercado_libre" "tabla" "detalle" "ranking-categoria"; do
  curl -s "$API/api/competencia/$ruta" > "/tmp/antes_$(echo $ruta | tr '/?=' '_').json"
done
```

Más al menos 3-5 SKUs con casos conocidos (`CAM-0030-IND` entre ellos) por
`/sku/{sku}`, y una `/subcategoria` con nichos.

### Pre-chequeo de colisión de PK — nuevo, v1 no lo tenía

La PK de `market_bestsellers` cambia de `(categoria_id, nivel, externo_id)` a
`(categoria_id, nivel, posicion)`. Si hay dos filas con la misma tripleta nueva,
el `insert … select` **las descarta en silencio**:

```sql
select categoria_id, nivel, posicion, count(*)
  from propuestas.rankings_categoria
 group by 1,2,3 having count(*) > 1;
-- debe devolver 0 filas. Ídem para search_results con (termino, externo_id).
```

### Congelar la captura durante la ventana

Entre migrar el dato (paso 3) y el drop (paso 7) conviven los dos esquemas. La
captura es mensual, así que el riesgo es bajo, pero si el cron cae en esa ventana
escribe en `propuestas` datos que nunca llegan a `enrich`. **Apagar el cron de
`railway.competencia.json` durante la migración.**

### El drop, en dos tiempos

En vez de `drop schema propuestas cascade` el mismo día:

```sql
-- Paso 7a: retirar sin destruir. Reversible con un rename de vuelta.
alter schema propuestas rename to propuestas_retirado;
```

Dejar el backend nuevo corriendo contra `enrich` **con una racha de actas verde**
antes de borrar. El drop real va después:

```sql
-- Paso 7b: días después, con el módulo estable
drop schema propuestas_retirado cascade;
```

La deuda no cobra intereses por una semana; el `drop cascade` sí es
irreversible.

### Rollback explícito

v1 lo dejaba implícito en el orden de los pasos. Escrito:

> Mientras no se ejecute el paso 7, `propuestas` sigue intacta. Si el paso 5
> (backend) sale mal: revertir el commit de backend y confirmar que
> `competencia_supabase.py` vuelve a apuntar a `propuestas.*_v`. Las tablas
> nuevas de `enrich` y las columnas de `channel.categories` quedan huérfanas
> pero son aditivas e inertes — no hay que deshacerlas.

---

## Otros hallazgos del consejo que hay que resolver antes de ejecutar

Estos no son de los 4 cambios, pero salieron verificados y bloquean pasos
concretos:

1. ~~**El vocabulario de `source` no cuadra.**~~ **RESUELTO (11-ago, medido).**
   El DDL declara `'ml_ia' | 'manual' | 'woocommerce'` (`0001:144`) pero eso es
   un comentario viejo, no una restricción: los valores REALES en producción son
   `predictor` 5,277 · `panel` 5,166 · `costos_ml` 2,340 · **`real` 940**.
   `'real'` es un valor en uso. Lo desactualizado es el comentario del DDL.
2. ~~**`enrich.product_media` no es idempotente.**~~ **FALSA ALARMA (11-ago).**
   El consejo leyó la 0001, que no lo trae — pero el índice único
   `uq_product_media_sku_kind_url (sku, kind, source_url)` se creó en la
   **migración 0002, línea 49**, y existe tanto en producción como en sandbox.
   El backfill usa `on conflict do nothing` y es idempotente por construcción.
3. **`competencia_store.py` no es solo legado.** `competencia_supabase.py:204`
   lo importa en tiempo de ejecución para reusar `_cubre`, y el router importa el
   store —no el módulo de Supabase— directamente. El store es la fachada y su
   `disponible()` es lo que decide SQLite vs Supabase. "Podarlo" sin mapear eso
   rompe el modo local y el cruce de términos.
4. **`categoria_id_real` necesita quién lo concilie.** El `coalesce` resuelve el
   conflicto de *escritura* y respeta la regla "el panel manda" — eso está bien.
   Pero los 128 SKUs discrepantes no se resuelven solos: o el panel está mal, o
   la medición capturó ruido. Agregar una consulta de salud (idealmente una fila
   en `/migracion`) que muestre el conteo de
   `categoria_id_real is distinct from category_id`, o en seis meses son 128
   divergencias silenciosas que nadie mira.

---

## CAMBIO 5 — prefijo de dominio en los nombres

Los tres consejeros lo levantaron por separado. Los nombres de v1
(`category_bestsellers`, `search_results`, `category_terms`) eran demasiado
genéricos para un esquema `enrich` compartido que ya tiene `supplier_data`,
`ai_attributes`, `product_media`, `odoo_viability` y `ai_content`, y que va a
seguir creciendo. `search_results` sobre todo: ¿resultados de búsqueda de qué?
El día que alguien necesite guardar resultados del catálogo propio, el nombre ya
estaba tomado.

Se adopta el prefijo `market_`, en inglés como el resto del esquema v4:

| Nombre en v1 | Nombre final |
|---|---|
| `enrich.category_bestsellers` | `enrich.market_bestsellers` |
| `enrich.search_results` | `enrich.market_search_results` |
| `enrich.category_terms` | `enrich.market_terms` |
| `enrich.sku_market_config` | `enrich.market_sku_config` |
| *(nuevo en cambio 2)* | `enrich.market_listing_metrics` |
| `enrich.competencia_skus_v` | `enrich.market_skus_v` |
| `enrich.competencia_publicaciones_v` | `enrich.market_publicaciones_v` |

Las dos vistas también cambian: v1 las nombraba `competencia_*_v`, que ya era un
prefijo de dominio válido, pero mezclar `competencia_` y `market_` en el mismo
esquema es peor que cualquiera de los dos por separado.

**Por qué `market_` y no `competencia_`:** el esquema v4 está en inglés
(`core.products`, `channel.listings`, `enrich.ai_content`). Las tablas en español
son las de MySQL (`productos`, `categorias_ml`, `pedidos_ml`), que es de donde se
está migrando. Un prefijo en español en `enrich` marcaría la tabla como venida
del lado viejo.

Costo: cero ahora, porque las tablas nacen con este DDL. Después del paso 5
(cuando el backend ya apunte) sería un `alter table … rename` más un barrido de
strings en `competencia_supabase.py` y las vistas.

---

## Orden de trabajo (sin cambios respecto a v1, salvo el 7 partido en dos)

> Los nombres son los del cambio 5 (`market_*`).

1. **DDL nuevo**: 5 tablas en `enrich` (las 4 de v1 + `market_listing_metrics`) con
   `canal`, RLS, grants e índices, más las 3 columnas de `channel.categories`.
   Aditivo, no rompe nada vivo. **Regenerar `schema_manifest.json`.**
2. **Backfills**: `channel.categories` parent/root con
   `backend/scripts/backfill_categories_arbol.py` (**cero llamadas a la API de
   ML** — ver abajo) ✅ **HECHO**, y `enrich.product_media` ✅ **HECHO**
   (`backfill_product_media_wc.py`: **1,572** imágenes, no 1,541, y **tampoco
   desde WooCommerce** — la URL ya está en `propuestas.competencia_skus.imagen`,
   así que es un `insert…select` dentro de la misma base; evita además el 403
   intermitente del WAF, pendiente conocido #1).

   **Las 2 filas sueltas se CANCELAN.** El plan v1 las necesitaba porque las
   métricas iban como columnas de `channel.listings`: sin fila no había dónde
   ponerlas. El cambio 2 las movió a `enrich.market_listing_metrics`, que **no
   tiene FK a `channel.listings`** (solo a `core.products`, `core.channels` y
   `core.accounts`), así que ya no hacen falta. Verificado en datos:

   - `TEC-0631-PLA` / BEKURA / mercado_libre es la única publicación de las
     3,118 sin fila por PK — pero está en `core.products` y sus 2 filas de
     métricas migran sin tocar `channel.listings`.
   - `CAM-0030-IND` trae su categoría (`MLM121837`, Colchones) en
     `competencia_skus`; va a `market_sku_config.categoria_id_real` y el
     `coalesce` de la vista la resuelve sin fila en `product_category`.

   Son dos escrituras menos a tablas compartidas del equipo. `channel.listings`
   además **sí está auditada** por `comparar_channel.py`, así que no tocarla es
   la opción barata.
3. **Migrar el dato** ✅ **HECHO (11-ago)** — `migrar_competencia_enrich.py`,
   conteos exactos: 3,000 + 1,816 + 5,789 + 1,584 + 3,118 = **15,307 filas,
   cero pérdidas**, segunda corrida idempotente (0 insertadas). Diagnóstico
   previo: cero huérfanos de FK, cero sin periodo, cero duplicados por la PK
   nueva. `propuestas` quedó INTACTA — sigue siendo la fuente viva hasta el
   paso 5.

   **El cron de captura NO EXISTE como servicio en Railway** (verificado
   contra el proyecto: ningún servicio usa `railway.competencia.json`; las
   capturas han sido corridas manuales). No había nada que apagar — el candado
   real durante la ventana es no correr `competencia_subir.py` ni los POST del
   panel.

   **Línea base capturada ANTES de migrar** en `verificacion_competencia/`
   (git-ignorada): las 2 vistas + las 5 tablas de `propuestas`, ~9 MB. La
   línea base HTTP de los 14 endpoints GET queda pendiente: el backend ya
   exige `X-API-Key` (el rollout de auth avanzó) y la llave no está en los
   env locales — pedirla antes del paso 5. Con los escritores parados, el
   dato congelado hace equivalente capturarla ahora o entonces.

   ⚠️ **Matiz del conteo "785 con sale_price < price"**: ese 785 se midió
   `precio < precio_lista` DENTRO de la tabla origen. Cruzado contra
   `channel.listings.price` da **645**, porque el price del listing se
   refresca cada 15 min y se movió desde la captura. Es la consecuencia
   esperada de descartar `precio_lista` por diseño; el dato migrado
   (`sale_price`) está completo e intacto — 3,118 de 3,118 con visitas.
4. **Vistas** `enrich.market_skus_v` y `enrich.market_publicaciones_v`
   con la forma exacta que hoy devuelven las de `propuestas`.
5. **Backend** ✅ **HECHO (11-ago, v0.99.0)** — `competencia_supabase.py`
   repuntado: 10 consultas a `enrich.market_*`; la ÚNICA que sigue en
   `propuestas` es `resultados()` (tabla `competencia_resultados`, 295 filas,
   lector vivo `/detalle` — resolver antes del rename del 7a). La columna
   `canal` de las tablas nuevas NO se expone al API aún (pop documentado).
   Poda: GET `/visitas-propias` retirado — llamaba a una función que nunca
   existió y respondía 500 en el 100% de los casos. La fachada
   (`competencia_store`) y `_cubre` quedaron intactos.

   Verificación pre-deploy: módulo viejo y nuevo corridos LADO A LADO contra
   producción, mismo instante — las 11 funciones equivalentes módulo los
   retiros documentados (`periodo`/`descuento` en rankings; `periodo`/
   `precio_lista`/`descuento`/`vendidos`/`visitas_30d` en búsquedas;
   `periodo`/`url` en términos).

   **Verificación post-deploy (11-ago, v0.99.0 en producción): 15 de 15.**
   8 endpoints byte-idénticos a la línea base; 6 difieren SOLO en los retiros
   documentados (más deriva de `precio_lista` vivo en tabla/vista, esperada);
   `GET /visitas-propias` pasó de 500 (roto) a **405** — el GET se podó y el
   POST del mismo path sigue vivo, por eso 405 y no 404. Cero regresiones.
   El diff quedó en `verificacion_competencia/despues_http_*.json`.
6. **Frontend**: podar tipos y llamadas muertas.
7. **7a.** `alter schema propuestas rename to propuestas_retirado`, tras el diff
   sobre todos los endpoints. **7b.** Días después, con racha verde:
   `drop schema propuestas_retirado cascade`.

Los pasos 1-3 tocan la BD de producción operativa. Van con dale explícito y se
corren desde terminal.

---

## Conteos que deben cuadrar (sin cambios)

1,584 SKUs · 3,118 publicaciones con `visits_30d` no nulo · 3,000 filas de
ranking · 1,816 de búsqueda · 785 publicaciones con `sale_price < price`.

Al final: `select count(*) from information_schema.tables where
table_schema in ('propuestas','propuestas_retirado')` → 0.

---

## Lo que este plan sigue sin resolver

Igual que v1, y hay que decirlo:

- El hueco de `item_categoria_id` (37/3,000): los nichos solo funcionan en las
  categorías capturadas con el navegador local.
- 22 subcategorías de Hogar y 23 de Recuerdos sin capturar (~$0.31 de Apify).
- 32 SKUs cuyo término general devolvió vacío por ser demasiado específico.
- **Nuevo:** `schema_manifest.json` está roto hoy por causas ajenas a este plan.
  Se arregla aquí porque estorba, no porque sea culpa de Competencia.
