# ENCARGO: diseñar dónde vive la información de cada producto para cada canal (PROPUESTA, no implementación)

Eres un arquitecto de datos trabajando sobre el proyecto **OMNICANAL de Kubera** (repo en `C:\Users\diaz2\OneDrive\Escritorio\omnicanal`). Tu trabajo en este chat es **producir una propuesta de modelo de datos con recomendación argumentada**. No vas a implementar nada.

**PROHIBIDO EN ESTA SESIÓN:**
- Cualquier `INSERT` / `UPDATE` / `DELETE` / `CREATE` / `ALTER` / `DROP` en cualquier base. Solo `SELECT` sobre `information_schema` / `pg_catalog` o `COUNT(*)` si necesitas verificar algo.
- Escribir en la BD kubera (`tukwcvsi...`, Postgres/Supabase): es **PRODUCCIÓN OPERATIVA**. Una sola fila de prueba manda un dominio a `con_deltas` en `migration.reconciliation_runs` y reinicia su racha de 14 días.
- Aplicar migraciones, tocar Railway, hacer push, o "probar" el diseño escribiendo.

**Entorno.** Intérprete `C:\Users\diaz2\OneDrive\Escritorio\omnicanal\backend\.venv\Scripts\python.exe`; variables desde `C:\Users\diaz2\OneDrive\Escritorio\omnicanal\.env` y `C:\Users\diaz2\OneDrive\Escritorio\omnicanal\backend\.env` (`SUPABASE_DB_URL`, `MYSQL_*`, `WPDB_*`). Abre la sesión de Postgres con `set_session(readonly=True)`.
**AVISO verificado:** `validar_ambiente()` (`backend/config.py:419-452`) **solo mira `SUPABASE_URL` (REST), NO `SUPABASE_DB_URL`**. En el `.env` local, `SUPABASE_URL` apunta a `xaxbkijc…` (analytics) mientras `SUPABASE_DB_URL` apunta a **kubera PRODUCCIÓN**. El candado no te va a avisar: asume que estás conectado a producción y protégete tú.

Todo lo que sigue está **VERIFICADO** contra las bases y el código al **2026-08-11** salvo donde diga **NO VERIFICADO** o **PROPUESTO**. No inventes nombres de tablas, columnas ni metas: si no lo verificaste tú, decláralo como supuesto.

---


> ⚠️ **Este documento se escribió contra el commit `21c0755` (v0.101.0).** El repo
> ya va en `c0744ee` (v0.103.0) y las migraciones **0010–0014** cambiaron varias
> conclusiones — están corregidas en el texto y marcadas así. **Haz `git pull
> --rebase` y `ls supabase/migrations/` antes de diseñar nada.**


## 1. Qué es Omnicanal y cuál es la llave

Panel de gestión de catálogo y ventas de Kubera (Brandon, brandon@kubera.mx). Backend FastAPI (`backend/`) + frontend Next.js (`frontend/`), desplegado en Railway (`Hixen9Proyects`, auto-deploy desde `main`).

- **WooCommerce (chunche.shop) es la FUENTE DE VERDAD** de producto, inventario y ventas. Corre con **HPOS** (los pedidos NO están en `wp_postmeta`, están en `wp_wc_orders` / `wp_wc_orders_meta`). WordPress vive en **otra base** (variables `WPDB_*`): la base propia `u531713409_kubera_ml` tiene 0 tablas `wp_*`.
- Canales de salida:
  | Canal | Cuentas | Estado real de publicación |
  |---|---|---|
  | Mercado Libre | **BEKURA** ("Kubera") y **SANCORFASHION** ("San Corpe") | Cableado a `/api/publicar`; 4,128 filas en `ml_progress` |
  | Amazon | San Corpe (seller `A27UV0J0KJJAOM`, marketplace `A1AM78C64UM0Y8`) | Cableado a `/api/publicar`; 1,790 SKUs en `amazon_progress` |
  | Walmart MX | **ninguna registrada en `core.accounts`** | **Script suelto** `backend/scripts/publicar_walmart.py` (787 líneas), NO cableado al panel (`marketplaces.py`: `habilitado=False`) |
  | TikTok Shop MX | tienda KUBERA (API propia, sin M2E); **ninguna en `core.accounts`** | **Publicador vive en el scratchpad** (`tk_publicar.py`), no en `backend/`. En `backend/routers/tiktok.py` solo hay OAuth, reparar-tiendas, explorar y estado |
  | Temu | vía M2E Cloud; **ninguna en `core.accounts`** | **NO existe publicador de ningún tipo**; M2E solo se usa para LEER órdenes (`pedidos_m2e.py`). Los endpoints de catálogo de M2E que menciona CLAUDE.md (`GET /catalog/product/?sku=`, `PATCH /catalog/product/`) **NO están implementados en el repo y NO se verificó que funcionen** |

- **El SKU es la llave.** PK de `core.products` (Postgres, `citext`), de `productos` (MySQL) y `_sku` de Woo. Inconsistencia real de tipo: `citext` en `core`/`channel`/`costing`/`enrich`, pero `text` en `public.packing_list_items` y en `propuestas_retirado.*`. Existe `migration.id_map` (22,152 filas) que mapea `sku_original` → `sku` canónico, y `core.products` tiene CHECK `length(sku)<=100 AND sku !~ '\s'`.

---

## 2. EL FLUJO OPERATIVO DE HOY (el modelo de datos sale del flujo, no al revés)

### 2.1 Mercado Libre
- **De dónde sale cada valor** (`backend/services/publicar_ready.py::construir_prod`, lee Woo): título = `campos.titulo` del Studio > `wp_posts.post_title`. Precio = `campos.precio_regular` > meta `_regular_price` > precio de las VARIANTES > `_price`. Stock = `_stock_odoo` > `_stock`. Peso/dims = `_weight`/`_length`/`_width`/`_height`. Imágenes = `_thumbnail_id` + `_product_image_gallery`. Atributos = **todas** las metas `ml_attr_<ID>`.
- **Categoría**: `ml_categoria_id` (picker HUMANO) > `ml_category_id` (predictor). Encima, `publisher_core.build_payload` aplica un **override**: si la categoría de WooCommerce trae `ML: MLM###` en su `description`, ésa gana. Por eso, cuando hay elección de panel, `construir_prod` manda `wc_categories: []` a propósito (caso real CAM-0034-BEI).
- **Guardas duras**: aborta si falta `category_id`, `title` o `price > 0`.
- **Obligatorios EN VIVO**: `GET /categories/{id}/attributes` (`tags.required`) y `/sale_terms`. El código propio solo aporta guardas + defaults.
- **Persistencia**: `ml_backlog` (MySQL, 1 fila por INTENTO, `payload` y `ml_response` `longtext` con `CHECK json_valid`) + `ml_progress` (MySQL, PK `'cuenta:sku'`, **estado actual**).
- **Actualizar ≠ crear**: `PUT /items/{id}` con atributos como `{id, value_name}` (no `value_id`) + `PUT/POST /items/{id}/description`. Si el ítem tiene `family_name`, ML rechaza cambiar el título (cause 374).

### 2.2 Amazon
- `PUT /listings/2021-08-01/items/{seller}/{sku}` con `{productType, requirements:'LISTING', attributes}`. Única guarda propia: título no vacío.
- **productType**: meta `amz_product_type` > `amazon_progress.product_type` > detección por las 3 primeras palabras del título > fallback `'HOME'`.
- **Obligatorios EN VIVO**: JSON Schema de `GET /definitions/2020-09-01/productTypes/{pt}`, **cacheado en disco** (`publicar_ready.py:580` escribe `{product_type}.json` en `amz_mapper.SCHEMAS_DIR`).
- Hasta **4 intentos**: lee `issues` con `MISSING_ATTRIBUTE` y rellena. Persistencia: `amazon_backlog` (`issues`+`payload`+`amz_response`, los tres **truncados a `[:65000]`** — riesgo teórico contra el `CHECK json_valid`; máximo medido 24,091 B) + `amazon_progress` (estado, PK `sku`).

### 2.3 Walmart MX
- `POST /v3/feeds?feedType=MP_ITEM_INTL`, multipart. Header con `subCategory`, `version='3.11'`, `mart='WALMART_MEXICO'`, `locale='es'`, `sellingChannel='marketplace'`, `processMode='REPLACE'`.
- Por artículo: `{Orderable:{...}, Visible:{<etiqueta de categoría EN ESPAÑOL>:{...}}}`. La clave del bloque `Visible` es la etiqueta literal (`'Disfraces'`, `'Cocina, Decoración y Otros'`): si se pega mal, Walmart cae a un spec genérico.
- **Todo hardcodeado** en `CATEGORIAS_AUTORIZADAS` (`publicar_walmart.py:84-127`): `'costumes'` → Disfraces, folio de exención UPC `15728342`, clave SAT `60141401`; `'home_other'` → 'Cocina, Decoración y Otros', folio `15751007`, SAT `52151600`. La exención es **por categoría** (`productIdType:'GTIN'`, `productId:'CUSTOM'`), con ticket propio en Seller Center por cada categoría nueva.
- Trampas medidas: `measure` con **máximo 2 decimales** (6 rechazos del primer lote); **mínimo 2 imágenes** JPEG verificadas por contenido con `min(w,h)≥1000`; **`ESPERA_PROPAGACION=120 s`** entre subir a WordPress y mandar el feed (publicar 2 s después dio "We couldn't download the image" ×8 el 4-ago).
- **No hay ninguna tabla ni meta que guarde estado de Walmart por producto.**

### 2.4 TikTok Shop
- `POST /product/202309/products`. Requiere **`shop_cipher`** como query param (se obtiene con `GET /authorization/202309/shops`, campos `cipher` e `id`), firma **HMAC-SHA256** por llamada, timestamp en ventana [−5 min, +30 s], y la allowlist de IPs (solo pasa desde Railway). **TikTok responde HTTP 200 aunque falle**: el veredicto está en `code` del cuerpo.
- **Categoría**: `POST /product/202309/categories/recommend` **falla el 49% de las veces (medido)** → fallback IA en 2 etapas (rama → hoja) validado contra hojas reales, umbral 0.3; lo que entra por ahí se marca `categoria_aproximada: true`.
- **Atributos**: exige **ID de atributo Y ID de valor**. Los `SALES_PROPERTY` (Color/Talla) NO pueden ir en `product_attributes` (generan variantes).
- **Imágenes por `uri` opaco** (upload previo; TikTok rehospeda).
- **Único rastro persistido**: `ops.channel_submissions` con `detail_ref='tiktok:lote:20260811'` — **un nombre de lote, no una fila**.

### 2.5 Resumen: qué se re-genera, qué se guarda
- Se **re-genera siempre**: el payload completo de los 4 canales, en cada publicación.
- Se **guarda**: ML y Amazon (payload + respuesta + issues, en MySQL). **Walmart y TikTok: nada persistente.**
- Se **cachea en disco**: el schema de Amazon por productType.
- Se guarda **duplicado a propósito**: los atributos ML como N metas `ml_attr_<ID>` (las que lee el publisher) **y** como una meta `ml_atributos` con el JSON entero "de respaldo/trazabilidad" (`crear_producto.py:940`).

---

## 3. ESTADO REAL DEL ALMACENAMIENTO (nombres verificados al 2026-08-11)

### 3.1 Supabase — BD kubera (`tukwcvsi...`), PRODUCCIÓN
47 tablas base + 10 vistas en 9 esquemas de negocio: `core`, `channel`, `costing`, `ops`, `migration`, `enrich`, `analytics`, `public`, `propuestas_retirado`.

**`core` (4 tablas) — el registro civil:**
- `core.products` — **22,186**. `sku citext PK` · `name` · `wc_id bigint UNIQUE` · `wc_parent_id` · `odoo_id` · `status text def 'draft'` · `brand` · `has_variations bool` · `parent_sku citext` (FK auto-referencial) · `tags text[]` · `source` · `created_at/updated_at`. Trigger `trg_touch_products`.
- `core.channels` — 7: `amazon`✓, `general`✓, `mercado_libre`✓, `shein`✗, `temu`✗, `tiktok`✗, `walmart`✗. **El flag `is_active` está rancio**: `tiktok` y `temu` ya tienen pedidos y 360 submissions.
- `core.accounts` — **solo 4 filas**: `(amazon, AMAZON)`, `(general, GENERAL)`, `(mercado_libre, BEKURA)`, `(mercado_libre, SANCORFASHION)`. **`external_id` es NULL en las 4.** **NO hay cuenta para temu, tiktok ni walmart.**
- `core.usuarios` — 12, `rol` CHECK `IN ('admin','operador','lectura')`.
- **NO existe `core.categories`.**

**FKs REALES a `core.products(sku)`** (verifica antes de apoyarte en ellas): `channel.listings`, `channel.product_category`, `ops.channel_submissions`, `costing.costos_finales`, `costing.costos_validados`, `enrich.ai_attributes`, `enrich.ai_content`, `enrich.product_media`, `enrich.supplier_data`.
**NO tienen FK a `core.products`**: `channel.orders` y `channel.order_items` (solo a `core.channels`/`core.accounts`, y `order_items`→`orders` con CASCADE); los SKUs de un pedido van en el array `channel.orders.skus citext[]`. Tampoco `enrich.odoo_viability` (PK `sku` **sin FK**) ni `ops.migration_issues` (a propósito: registra huérfanos).

**`channel` (6 tablas + 3 vistas):**
- `channel.listings` — **19,679**. PK `(sku, account_id, canal)`. Cols: `listing_id, url, status, situacion, price, price_base, stock_own, stock_full, stock_fba, is_fulfillment, logistic_type, currency, product_type, category_id, store_name, updated_at`. Triggers `trg_hist_listings` + `trg_touch_listings`. Reparto: `general` 13,042 / `mercado_libre` 4,847 (sobre **2,644 SKUs**, 2 cuentas) / `amazon` 1,790.
- `channel.listing_history` — **94,014**, la llena el trigger. **Un UPDATE ciego genera historia falsa** (por eso los espejos escriben con `WHERE ... IS DISTINCT FROM`).
- `channel.categories` — **2,692, TODAS `mercado_libre`**. PK `(channel_id, category_id)` + `name, path, parent_id, root_id, root_name`. (`root_id`/`root_name` tienen DDL: migración **0012**. Y ⚠️ **NO parsees `path`**: usa DOS separadores distintos, `›` U+203A en 2,612 filas y `>` en 2.)
- `channel.product_category` — **13,723**. PK `(sku, channel_id)` + `category_id, source, updated_at`. `source`: predictor 5,277 / **panel 5,166** / costos_ml 2,340 / real 940. Hoy **solo hay filas de ML**, pero su PK admite cualquier canal, y **ya es uno de los 10 destinos de `kubera_mirror._UPSERTS`**.
- `channel.orders` 13,739 · `channel.order_items` 13,739 (coincidencia exacta: hoy 1 línea por pedido). **Tráfico caliente: +854 filas en 24 h.**
- Vistas: `sales_daily`, `sales_daily_completa`, `restock_panel`.

**`costing` (6 tablas + 2 vistas):**
- `costing.costos_finales` — 4,376. **PK `(sku, canal)`** (P4); **100% `mercado_libre`**. `ml_cat_id, pct_comision, precio_sugerido, precio_base, margen, formula_ver`. **No lleva dimensiones** (al retirar MySQL, las dims viven solo en `costos_validados`).
- `costing.costos_validados` — 15,429 (PK `sku`): `largo/alto/ancho/peso`, `costo_producto`, `costo_cbm`, `contenedor`, `cajas`, `piezas_por_caja`.
- `cost_history` 52 · `pricing_params` 5 (**PK `(key, valid_from)` = versionado por fecha, patrón ya probado**) · `fx_rates` 0 · `legacy_costos_ml` 0.
- `costing.channel_costs` está **COMENTADA a propósito** en `0001_esquema_v4.sql:363` — no existe.

**`ops` (6 tablas):**
- `ops.channel_submissions` — **22,933. ES BITÁCORA, NO ESTADO.** `id, canal, cuenta, sku, account_id, submission_id, operacion, status, success, error_resumen, detail_ref, submitted_at, published_at, created_at`. **Sin columna de payload.** ML 18,219 / amazon 4,358 / tiktok 360. Fila real: `{canal:'tiktok', cuenta:'KUBERA', sku:'TEC-1327-NEG', submission_id:'1736992138725393484', operacion:'create_product', status:'published', detail_ref:'tiktok:lote:20260811'}`.
- `ops.webhook_events` 66,769 (UNIQUE `(env,canal,topic,external_id,delivery_id)`; **retención 3 días** vía `ops.purgar_webhook_events`) · `ops.process_log` 2,226 · `ops.migration_issues` 23 · **`ops.ml_tokens` 0** (bloqueada hasta Vault) · **`ops.task_queue` 0** (cola con `payload jsonb`, `status` CHECK `pending/processing/done/error/dlq`, reintentos y `next_retry_at`: **infra existente que nadie usa**).

**`enrich` (10 tablas) — el esquema clave para este encargo:**
- **`enrich.ai_attributes` — 0 FILAS.** DDL exacto:
  ```sql
  create table enrich.ai_attributes (
    sku        citext primary key references core.products(sku),
    attributes jsonb,
    is_valid   boolean,
    model_used text,
    updated_at timestamptz not null default now()
  );
  ```
  Es el hueco declarado para los atributos de IA y **nunca se pobló**. PK solo `sku`: sin canal ni cuenta.
- **`enrich.ai_content` — 0 FILAS, SIN DDL en `supabase/migrations/`** (creada por SQL Editor). Cols: `sku citext` · `canal text` · `cuenta text def ''` · `account_id uuid` · `categoria text` · **`payload jsonb NOT NULL`** · **`origen jsonb`** · **`flags jsonb`** · `modelo_ia` · **`spec_version text`** · `estado text def 'pendiente'` CHECK `IN ('pendiente','ok','error','obsoleto')` · `error_texto` · **`hash_woo character(40)`** · `generado_at` · `updated_at`. **PK `(sku, canal, cuenta)`.** FKs a `core.products`, `core.channels`, `core.accounts`. Índices `gin(payload)` y `btree(canal, estado)`. **Es, en forma, casi exactamente lo que este encargo pide — y está vacía.**
- **Precedentes VIVOS con la forma que se discute** (dentro del mismo perímetro, y CON datos): **`enrich.market_sku_config` PK `(sku, canal)`, 1,584 filas** · **`enrich.market_listing_metrics` PK `(sku, canal, cuenta, periodo)`, 3,118 filas**. Ninguna tiene DDL en archivo.
- `enrich.product_media` 2,094 (VIVA, última fila 2026-08-11) · `market_terms` 5,789 · `market_bestsellers` 3,000 · `market_search_results` 1,816 · `enrich.supplier_data` 0 · `enrich.odoo_viability` 0.

**Otros:** `migration.id_map` 22,152 · `migration.reconciliation_runs` **142** (las "actas") · `costs_preview` 0 · `costs_differences` 0 · `analytics.sales_daily_hist` 17,984 · `analytics.stock_hist` 32,848 (vista con fecha `'2026-07-15'` hardcodeada) · `public.packing_lists` 1 + `packing_list_items` 20 · `propuestas_retirado.*` (7 tablas + 2 vistas con datos).

### 3.2 MySQL `u531713409_kubera_ml` — 38-39 tablas propias, **cero `wp_*`**

Estado ACTUAL por producto/canal:
- **`ml_progress`** 4,128 — PK `prog_key='cuenta:sku'` + `cuenta, sku, wc_id, ml_item_id, ml_url, success, error, gtin_error, dry_run, published_at, updated_at`. BEKURA 2,079 (1,944 ok) / SANCORFASHION 2,049 (1,915 ok).
- **`amazon_progress`** 1,790 — PK `sku` + `wc_id, seller_id, marketplace_id, asin, product_type, submission_id, status (ACCEPTED/INVALID/VALID/PUBLISHED/ERROR), success, error_label, issue_count, last_submitted, published_at`. **Aquí vive el product type real.**
- **`canal_inventario`** 6,281 — PK `(sku, canal, cuenta)`. **Esquema propiedad de la migración: leer sí, alterar no.**
- **`categorias_ml`** 12,839 — PK `sku` + `category_id, category_name, ruta, cat1..cat4, fuente` (predictor 7,576 / costos_ml 2,960 / real 1,863 / NULL 440). **Guarda id Y nombre Y ruta: precedente directo de lo que Brandon pide.**
- **`atributos_ia`** 5,380 — PK `sku` + `atributos_json longtext, flags, num_atributos, atributos_validos, modelo_ia`. **Es la salida cruda de DeepSeek, NO el artefacto que lee el publisher.**
- **`fanout_log`** 3,163, **VIVO** (última fila 2026-08-11 21:30) — `sku, motivo, canal, cuenta, item_id, accion, stock_canal, resultado, ms`. Canales: mercado_libre 1,778 / woocommerce 707 / amazon 585 / general 3. **Contradice el pendiente #6 de CLAUDE.md: el fan-out de stock SÍ está construido y corriendo.** Es un escritor de estado por `(sku, canal)` que hay que poner en el mapa. Está declarado TEMPORAL en `docs/TABLAS_TEMPORALES.md` y **al borrarlo se pierde el sello de idempotencia `full_compensado`**.

Bitácoras con blobs:
- **`ml_backlog`** 6,014 filas / 59.5 MB. `payload` avg **1,712 B**, `ml_response` avg 5,570 B. **Costo real InnoDB: 10,377 B/fila.**
- **`amazon_backlog`** 4,358 filas / **185.5 MB (la más pesada del proyecto)**. `payload` avg **6,806 B**, `amz_response` avg 10,238 B, `issues` avg 15,690 B. **Costo real: 44,637 B/fila.**
- `ml_image_edit_backlog` 12,582 · `crear_logs` 2,439 · `espejo_kubera_log` 67 (cola local de errores del espejo, **a propósito en MySQL** para sobrevivir a Supabase caído).

Datos por publicación/canal que también hay que mapear: **`ml_envio_real`** 10,994 (PK `cuenta+external_order_id`, costo real de envío) · **`ml_ficha`** 711 (PK `listing_id`: `peso_g`, `medido`) · **`ml_visitas`** 998 (PK `listing_id+dias`) · **`imagenes_producto`** 3,610 · **`amazon_imagenes`** 673 (PK `src_hash`).

Pedidos y tokens: `pedidos_ml` 13,743 (PK `ml_order_id`; BEKURA 6,904 / SANCORFASHION 6,734 / AMAZON 102 / TEMU 2 / TIKTOK 1 — **el prefijo `ml_` es engañoso, cubre todos los canales**) · `ml_tokens` 2 · `ml_tokens_dashboard` 2 · **`tiktok_tokens` 1 fila — LA ÚNICA TABLA DE TIKTOK QUE EXISTE**.
`productos` — **5,381 por `COUNT(*)` exacto** (el `table_rows` de `information_schema` dice 4,913, que es **estimación del optimizador InnoDB**; no lo uses para proyectar). **Fuente muerta** (§5.5).

**NO EXISTE ninguna tabla de catálogo, categoría, atributos, payload ni estado de publicación para Walmart, TikTok ni Temu en MySQL.**

### 3.3 WooCommerce — dónde vive HOY la elección humana

`wp_postmeta`: 796,518 filas, 1,167 `meta_key` distintas (992 son `ml_attr_*`). Posts: `product_variation publish` **7,202** · `product draft` 4,700 · `product publish` 1,997 · `product pending` 404 · `product 'ready'` 128.

| Meta | Productos | Qué es | Escritor |
|---|---|---|---|
| **`ml_categoria_id`** | **5,165** | Categoría ML del PANEL. **MANDA.** Ej. `MLM431131` | `routers/crear.py:404` (`POST /api/crear/categoria-ml`) |
| `ml_categoria_path` | 5,172 | Ruta legible `"A > B > C"` | idem |
| `ml_categoria_niveles` | 3,102 | JSON `[{id,name},…]` | idem |
| `ml_categoria_nivel_1..6` | 2,250 … 32 | **`nivel_6` no lo lee `wp_db.metadata_producto`, que solo pide 1..5** | — |
| `ml_category_id` / `ml_category_name` | 3,519 | **Predictor**. Llave DISTINTA; pierde ante `ml_categoria_id` (`publicar_ready.py:394-399`) | `crear_producto.py` |
| **`ml_attr_<ID>`** | **992 claves, 7,720 filas, 987 posts (779 productos + 208 VARIACIONES)** | Los atributos que **lee el publisher**. La clave es el ID *o el nombre*: `ml_attr_brand`(778), `ml_attr_MODEL`(699), `ml_attr_COLOR`(507), `ml_attr_Marca`(208), `ml_attr_Talla`(159), `ml_attr_Diseño de la tela`(151)… | `crear_producto.py:939`; leídas en `publicar_ready.py:432-433` |
| `ml_atributos` | 559 | El MISMO dato como JSON entero, "de respaldo" | `crear_producto.py:940` |
| `ml_dominio_id` / `ml_dominio` | 3,069 / 3,048 | **`ml_dominio` no tiene escritor identificable en el repo — NO VERIFICADO quién la llena** | `crear.py:409` (solo el `_id`) |
| **`amz_product_type`** | **1 (UN) producto** (`PROTECTIVE_GLOVE`) | Única meta `amz_*`/`amazon_*` de toda la base | `routers/publicar.py:108` |
| `_kubera_cbm` | 6,229 posts (2,998 productos + **3,232 variaciones**) | **Verificado al dígito 8/8: `_kubera_cbm == (_length × _width × _height)/1e6`.** Las dimensiones de Woo **no son medidas**: son CBM repartido hacia atrás; 458 SKUs con densidad físicamente imposible | sin escritor en este repo |
| `_kubera_size_chart` / `_status` | 8 | Guías de tallas ML (JSON `{domain, site_id, main_attribute, rows[]}`), estado `'pending_ml_dashboard'` | sin escritor en este repo |
| `_kubera_editar_imagenes` | 377 | Flags de edición IA por imagen (PHP serializado) | — |
| `_barcode` / `_gtin` | 3,193 / **0** | `_gtin` se lee (`wp_db.py:527`) pero no existe | — |
| `revision_*`, `c12_*`, `dims_ia_*`, `wc_kam_*` | hasta 3,067 | Revisión humana y costeo KAM | — |

**HALLAZGO CRÍTICO: NO EXISTE NINGUNA META de Walmart, TikTok, Temu ni M2E en toda la base de WordPress.** Verificado dumpeando las 175 `meta_key` que no son `ml_attr_*` y las 18 de `wp_wc_orders_meta`, buscando `wm_`, `walmart`, `tiktok`, `temu`, `m2e`: cero resultados.

**Sin espejo en ninguna parte (si Woo se pierde, se pierde):** los `ml_attr_<ID>`, la familia `_kubera_*` de costeo/CBM, `_kubera_size_chart`, `_kubera_editar_imagenes`, `_ml_logistica`/`_ml_comprador`(cifrado)/`_ml_neto` de cada pedido, y el aparato de revisión humana.

**Desajuste sin explicar:** 13,937 órdenes con meta `_ml_order_id` en `wp_wc_orders_meta` vs 13,743 filas en `pedidos_ml` (**194 de diferencia**, posiblemente las 181 en papelera). Afecta cualquier promesa de paridad de conteos.

### 3.4 JSON que se generan HOY (staging fuera de toda base)

Directorio: `C:\Users\diaz2\AppData\Local\Temp\claude\C--Users-diaz2-OneDrive-Escritorio-omnicanal\3351e511-59eb-4f80-b223-5314e4a99a8e\scratchpad\`

- **`tk_payloads.json`** — 611,852 B, dict de **244 SKUs**. **La forma canónica que ya inventó el proyecto**: cada valor separa dos ramas —
  - `_meta` = `{sku, categoria (ruta con →), categoria_id, atributos_rechazados[], confianza_ia, flags_ia[]}` (media **583 B**) — **nunca se manda al canal: es la traza de por qué la IA eligió/descartó cada atributo**;
  - `payload` = lo que se envía tal cual (media **1,233 B**).
- `tk_lote.json` (252 candidatos crudos de Woo) · `tk_resultado.json` (299 veredictos; **244 ok / 55 fallos, TODOS en etapa `armado`** — ninguno falló en el envío) · `tk_categorias.json` (**2,168 categorías, 1,937 hoja, 451 `INVITE_ONLY`**, con `is_leaf`, `local_name`, `parent_id`, `permission_statuses`) · `tk_categorias_ok.json` (**caché parcial: 125 SKUs → `{id, ruta}`**) · `tk_sin_categoria.json` (120 tripletas `[sku, titulo, motivo]`) · `tk_saltados.json` (`ropa_calzado` 19, `sin_fotos` 28) · `tk_atributos_cat.json` (una categoría: `is_requried` **[sic, typo de la API de TikTok]**).
- Walmart: `elec_items.json` (343 items, media **2,795 B**) y `elec244_items.json` (244, media 2,689 B), forma `{Orderable:{…}, Visible:{<categoría en español>:{…}}}` · `wm_listos.json` (803 SKUs en 5 categorías) · `wm_categorias.json` (**75 subcategorías con `{subcategory, nombre_es, obligatorios[], confianza}`**).
- `MX_SPEC.json` y `MX_MP_ITEM_INTL_SPEC.json`: 3,958,699 B cada uno, **byte-idénticos** (3.8 MB duplicados). El `spec.json` de la raíz del repo son **143 B de gzip con un 301** — no es una spec.

**Nada de esto está persistido en base alguna, y vive en un directorio TEMPORAL de sesión** que el propio reconocimiento vio cambiar bajo sus pies (`tk_payloads.json`, `tk_resultado.json` y `censo.json` cambiaron durante el análisis: hay un proceso vivo escribiendo).

### 3.5 Costo medido de guardar payloads
- Payload de envío minificado (media medida): **TikTok 1,233 B** (n=244) · **Mercado Libre 1,712 B** (n=5,695) · **Walmart 2,742 B** (n=587) · **Amazon 6,806 B** (n=4,358). **Suma 4 canales ≈ 12.5 KB por producto.**
- Con respuesta e issues, el costo real InnoDB es **10,377 B/fila** (`ml_backlog`) y **44,637 B/fila** (`amazon_backlog`).
- **gzip comprime estos payloads 5.3×** (445,362 → 83,853 B ≈ 230 B/payload TikTok).
- **El patrón que el proyecto ya eligió**: `ops.channel_submissions` pesa **315 B/fila** porque NO guarda blobs; guarda `detail_ref` (`'mysql:ml_backlog:<id>'`) — **141× más barato**. Literal en `kubera_mirror.py:110`: *"Resumen + detail_ref; blobs NO viajan"*. La idempotencia se resuelve con ese mismo `detail_ref` (`kubera_mirror.py:379-385`).
- **En Postgres el factor cambia**: `ops.webhook_events` cuesta 1,304 B/fila para un `jsonb` de 299 B (**4.4× por índices y overhead**).
- **PERO el patrón ya está roto y su destino se muere**: para TikTok el `detail_ref` real es `'tiktok:lote:20260811'` (nombre de lote, no fila), así que **el payload de 244 productos publicados no está en ninguna base**; y MySQL está en retiro, así que `'mysql:ml_backlog:<id>'` apunta a un destino con fecha de caducidad.

---

## 4. EL PROBLEMA A RESOLVER

**El mismo producto necesita datos DISTINTOS, con forma DISTINTA e intraducible, para cada canal, y hoy eso vive disperso entre metas de Woo, JSON en un directorio temporal y tablas sueltas de dos bases.**

| Concepto | Mercado Libre | Amazon | Walmart MX | TikTok Shop |
|---|---|---|---|---|
| **Categoría** | `MLM#####` en meta `ml_categoria_id` (+ override por `'ML: MLM###'` en la description de la categoría Woo) | `productType` enum MAYÚSCULAS (`PROTECTIVE_GLOVE`) en meta `amz_product_type` | **DOS a la vez**: `subCategory` snake_case en el header Y la **etiqueta en español exacta** como clave del bloque `Visible` | `category_id` numérico de 6 dígitos, **de hoja**; recomendador falla el **49%** |
| **Atributos** | `ml_attr_<ID>` (992 claves), `{id, value_id}` de listas cerradas por categoría (al actualizar: `value_name`) | snake_case, `[{value, marketplace_id, language_tag:'es_MX'}]`, filtrados contra el JSON Schema del productType | 7 comunes + 2 por categoría; `colorCategory` ARRAY de **36 valores con acentos literales**; `gender` de 5 | `[{id, values:[{id,name}]}]` — **ID de atributo Y de valor**; `SALES_PROPERTY` genera variantes |
| **Marca** | atributo `BRAND` con TEXTO `'Ferrahome'` | `brand='Generic'` **a propósito** (habilita la exención de GTIN) | `brand`+`manufacturer` texto | **`brand_id='7650172564119684872'`**, ID de catálogo |
| **Identificador** | `GTIN` si existe + SIEMPRE `EMPTY_GTIN_REASON value_id='17055161'` | `supplier_declared_has_product_identifier_exemption=true` | `productIdType:'GTIN'` + `productId:'CUSTOM'` — **exención por CATEGORÍA con folio** (15728342/SAT 60141401; 15751007/SAT 52151600) | no lo pide |
| **Precio** | `price` + `currency_id:'MXN'` | **DOS campos**: `list_price{value_with_tax,currency}` y `purchasable_offer.our_price[0].schedule[0].value_with_tax` | `price` float en `Orderable` | `skus[].price.amount` **STRING `'%.2f'`** |
| **Stock** | `available_quantity` | `fulfillment_availability[].quantity` + `fulfillment_channel_code` | **NO va en el alta** (`PUT /v3/inventory` aparte) | `skus[].inventory[].quantity` + **`warehouse_id='7647893424175580935'`** |
| **Dimensiones** | `SELLER_PACKAGE_*` **STRING con unidad** (`'600 g'`,`'33 cm'`), enteros, **las 4 juntas o ninguna**, se OMITEN si la densidad no cae en 0.001–30 g/cm³ | objetos `{unit,value}` en **hasta 9 nombres de atributo**, más paquete inflado (peso×1.10, dims×1.05) | `{measure, unit}` **máx 2 decimales**, en **DOS juegos** (`assembledProduct*` + `Shipping*`) | strings en `package_weight`/`package_dimensions` |
| **Título** | 60; y en categorías con `catalog_domain` el campo `title` está **PROHIBIDO** (va `family_name`) | 200 | 200 (**SUPUESTO heredado, sin fuente**) | 255 |
| **Descripción** | **llamada SEPARADA** a `/items/{id}/description` (`plain_text`) | `product_description` ≤2000 | `shortDescription` ≤3900 + `keyFeatures` (≤5) | HTML dentro del mismo payload |
| **Imágenes** | pre-upload → `picture_id`, ≥500×250, máx 10 | URL pública, ≥1000 px, WEBP prohibido, 1+8 | URL pública, JPEG verificado por contenido, `min(w,h)≥1000`, **mínimo 2**, **120 s de propagación** | upload → **`uri` opaco**, hasta 5 |
| **Solo suyo** | `SIZE_GRID_ID` por cuenta+dominio+género (**15 chart_ids** en `size_chart_mapping.py`: BEKURA 6 + SANCORFASHION 9, solo calzado+bras), `listing_type_id='gold_pro'`, `shipping.mode='me2'`, `WARRANTY_TYPE value_id='6150835'` | `marketplace_id`, `seller_id`, `condition_type='new_new'`, `recommended_browse_nodes` | **`ProductTaxCode` (clave SAT)**, folio de exención, `msiEligible` STRING, `version='3.11'`, `mart='WALMART_MEXICO'` | `shop_cipher`, `save_mode`, `warehouse_id`, imagen por `uri` |

**Ninguna taxonomía se traduce a otra.** Y hoy el estado por canal está repartido sin criterio único:
- **ML**: elección humana en **meta de Woo**, catálogo en `channel.categories`, asignación en `channel.product_category`, estado en `ml_progress` (MySQL) + `channel.listings` (PG), intentos en `ml_backlog` + `ops.channel_submissions`.
- **Amazon**: elección humana en **meta de Woo (1 producto)**, dato real en `amazon_progress.product_type` + `channel.listings.product_type`, schema cacheado **en disco**.
- **Walmart**: **en ningún lado**; categoría, folio y SAT **hardcodeados** en `publicar_walmart.py:84-127`.
- **TikTok**: **en ningún lado**; solo JSON del scratchpad.
- **Temu**: **no existe nada**.

Y `enrich.ai_attributes`, el hueco que la migración dejó para esto, lleva **0 filas desde que se creó**.

---

## 5. RESTRICCIONES INNEGOCIABLES

### 5.1 Perímetro de Eduardo/José
Los **6** esquemas `core`, `channel`, `costing`, **`enrich`**, `ops`, `migration` son suyos (`ESQUEMAS_PROPIOS` en `backend/scripts/aplicar_migraciones.py:57` — **CLAUDE.md omite `enrich`**, que sí está protegido y es el más grande). También intocables: `channel_mirror.py`, `costing_mirror.py`, los ETLs `backend/scripts/etl_*` y `comparar_*`, los jobs Railway `deltas-costos`/`deltas-channel`/`deltas-orders`, y el esquema de `canal_inventario` (leer sí, alterar no). **`backend/vendor/` NO SE TOCA** (excepción sancionada: `size_chart_mapping.py` es CONFIG).

**Toda tabla nueva ahí exige: DDL aplicado por Eduardo + archivo en `supabase/migrations/` + regenerar `supabase/schema_manifest.json`.** El candado de paridad (`aplicar_migraciones.py:106-147`) compara columna por columna (`nombre:tipo:nullable`) y sale con `exit 1`: crear una tabla sin eso **rompe la reconstrucción del sandbox para todo el equipo**.

Numeración: **14 archivos**, **dos `0004`** y **falta el `0008`**; el aplicador usa `sorted(glob('*.sql'))`. **El siguiente número libre es `0015`** — y está APARTADO: es el drop definitivo de `propuestas_retirado`, agendado para el lunes 18-ago. Coordina el tuyo con Eduardo antes de tomar número. Además `supabase_migrations.schema_migrations` registra **UNA sola** migración (`20260803235614`, `'competencia_vistas_publicas_sobre_propuestas'`): los 9 archivos numerados **no están ahí**, se aplicaron por SQL Editor o por el script propio.

⚠️ **CORREGIDO el 2026-08-12**: este párrafo decía que 24 objetos no tenían DDL en archivo. **Ya lo tienen** — llegaron en migraciones que este reconocimiento no vio:
`0010_enrich_ai_content.sql` · `0011_enrich_market.sql` · `0012_channel_categories_arbol.sql` · `0013_market_vistas.sql` · `0014_retiro_propuestas.sql`.

**Y `enrich.ai_content` ya trae PK `(sku, canal, cuenta)`** (línea 67 del 0010), no `(sku, canal)`. El caso que lo forzó es `EST-0091`: el MISMO SKU es dos productos distintos según la cuenta de ML, y los atributos derivan de la categoría. Lee ese archivo antes de proponer nada: puede que la tabla que ibas a diseñar ya exista.

Los scripts NO confían en `validar_ambiente()`: llevan guardia triple contra `REF_KUBERA_PROD='tukwcvsi'` con `sys.exit`, y los ETLs de producción exigen `--acepto-destino tukwcvsi`. **El `0010` propuesto debe encajar en ese mismo aparato.**

### 5.2 El seam del espejo (`backend/services/kubera_mirror.py`)
- `espejar(origen_py, funcion, tabla_mysql, tabla_kubera, operacion, payload, clave)` se invoca **DESPUÉS** del write MySQL exitoso, nunca antes. Es fire-and-forget: `put_nowait` a **2 colas acotadas de 500** con **afinidad por clave** (`hash((tabla_mysql, clave)) % 2`, para que dos eventos del mismo SKU no se apliquen invertidos), drenadas por 2 workers daemon. Nunca lanza, nunca bloquea. Cola llena = intento **descartado** pero registrado (`ColaLlenaError`).
- **Dos guardas**: `activo(tabla_mysql)` = `KUBERA_MIRROR_ENABLED` + `KUBERA_DB_URL` + tabla en el CSV `KUBERA_MIRROR_TABLAS` (**default peligroso: CSV vacío = TODAS**); y `_UPSERTS` debe tener handler o lanza `ValueError`.
- **`_UPSERTS` conoce exactamente 10 destinos** (`kubera_mirror.py:620-631`): `ops.webhook_events`, `ops.channel_submissions`, `ops.process_log`, `enrich.product_media`, `channel.orders`, `channel.order_items`, `core.products`, `costing.costos_validados`, `costing.costos_finales`, **`channel.product_category`**.
- **Agregar un destino requiere 4 cosas**: (a) entrada en `kubera_mirror.CENSO`, (b) handler idempotente en `_UPSERTS`, (c) la tabla destino existiendo en kubera (DDL de Eduardo), (d) la tabla ORIGEN en el CSV `KUBERA_MIRROR_TABLAS`. Faltando (b) o (c), **cada evento truena, se persiste en `espejo_kubera_log` (MySQL, a propósito) y dispara alerta Slack**. El paso (d) es **flujo vivo: exige el dale de Brandon**.
- Aislamiento: `PooledDB maxconnections=6, mincached=0, maxcached=3, blocking=False` (pool lleno = error registrado, no espera), `connect_timeout=4`, y dentro de la transacción `set_config('statement_timeout','4000',true)` + `set_config('app.via','kubera_mirror',true)` — **SET LOCAL porque el pooler 6543 no admite estado de sesión**. El pool subió de 3 a 6 tras perder 60 eventos por `TooManyConnections` el 23-jul.

### 5.3 El corte F6 (CLAUDE.md está desactualizado aquí)
CLAUDE.md describe la migración en fase de "dual-write/descubrimiento". **El código en `main` ya tiene los CINCO CORTES F6**: costing (v0.70.0), orders+channel (v0.71.0), core+categorías (v0.84.0). Módulos: `backend/services/{costing,orders,core,categorias}_write.py`. Con el flag encendido, **kubera es PRIMARIA** (escritura síncrona) y MySQL pasa a espejo inverso en hilo, best-effort. Revertir = apagar el flag, cero deploys.
Las 5 variables `SUPABASE_WRITE_{COSTING,ORDERS,CHANNEL,CORE,CATEGORIAS}` **existen en Railway production** (verificado por nombre; **los valores vienen redactados → qué está en `true` HOY es NO VERIFICADO**).

### 5.4 Las actas y la racha de 14 días
`migration.reconciliation_runs` (142 filas: `dominio, descripcion, conteos jsonb, checksums jsonb, resultado`). Desde v0.84.0 los ETLs pasaron de **POBLADORES a AUDITORES**: `resultado='ok'` ya no significa "corrió", significa "no tuve nada que corregir". El conteo `seam_gap` (insertar+actualizar que el seam debió cubrir) marca `con_deltas` si es >0 y **rompe la racha**. Últimos 20 días (ok/con_deltas): costing-deltas 21/0 · channel-deltas 20/2 · orders-deltas 22/6 · core-etl-v2 16/1 · categorias-etl 16/1 · analytics-hist 4/0.
**Si tu propuesta escribe datos que un ETL luego "corrige", rompe la racha del dominio aunque el dato sea correcto.**

### 5.5 `core.products` es el registro civil, y su fuente murió
**KuberaPipelineV1.0 se desconecta** (decisión de Eduardo, 23-jul): el robot de Alibaba, sus tablas MySQL y el servicio Railway `publicador` se retiran. **La tabla `productos` de MySQL — fuente única del ETL de `core.products` — queda muerta.** Los productos nacen SOLO en el panel (Crear) y suben por seam síncrono (`core_write.py` bajo `SUPABASE_WRITE_CORE`), con los tres seams de ciclo de vida: nacimiento en `crear_producto`, publish en `publicar_ready`, trash/deleted en la auditoría de `crear.py`.
**Consecuencia obligatoria**: cualquier entidad nueva por SKU debe (a) asegurar el acta en `core.products` **antes** de escribir — patrón "identidad primero" de `channel_mirror.py:96-99` — y (b) **NO nacer en MySQL esperando que un ETL la suba: ese camino murió.**

### 5.6 Triggers y semántica congelada
Triggers activos (9): `channel.listings` → `trg_hist_listings` + `trg_touch_listings`; `channel.orders` → `orders_touch`; `core.products` → `trg_touch_products`; `core.usuarios` → `trg_touch_usuarios`; `costing.costos_finales` y `costos_validados` → `trg_hist_*` + `trg_touch_*`. **No hay triggers en `enrich`, `ops`, `migration`, `analytics` ni `public`**: ahí `updated_at` lo escribe la aplicación.
Semántica congelada de `channel.orders` (`kubera_mirror.py:432-517`): estados se mueven, pero `skus` y `creado_at` quedan **congelados** al primer registro; `total` y `comision` admiten el paso `0 → valor real` **una sola vez** (patrón "0 = nunca observado"). `order_items`: importes congelados, `item_id`/`sku` solo se RELLENAN si faltaban, padre asegurado con `DO NOTHING` contra carreras de FK. **Es lo que validó las rachas: no la re-inventes.**
`costing.costos_finales` tiene **PK `(sku, canal)`**: toda consulta o escritura debe filtrar por canal o duplica filas.

### 5.7 `public` no es neutro
Es el único esquema con `INSERT` concedido a `anon`/`authenticated`, y el único expuesto por PostgREST (`db_schema=public,graphql_public`, documentado en `backend/services/competencia_supabase.py`). **Corolario de diseño: una tabla en `enrich`/`channel` NO es alcanzable por REST desde el frontend — hay que leerla por el backend con psycopg.**
**RLS: NO VERIFICADO.** Varias migraciones hacen `enable row level security` (`channel.orders`, `channel.order_items`, `analytics.*`) pero **no se censó `pg_policies`**. Toda tabla que propongas debe declarar RLS, grants y quién la consume.

### 5.8 EL PRECEDENTE que debes tomar en serio
El esquema `propuestas` (módulo Competencia, aprobado como esquema aparte "para no ensuciar los del equipo") **fue renombrado a `propuestas_retirado` en producción, con datos reales (5,789 / 3,118 / 3,000 / 1,816 / 1,584 / 295 filas), y el repo entero sigue consultando `propuestas.*`**: `backend/services/competencia_supabase.py` líneas 56, 66, 86, 105, 125, 147, 159, 170, 186, 207. `grep -rn 'propuestas_retirado'` en el repo = **0 resultados**. No hay commit ni documento que lo explique; **cuándo y por qué se retiró queda por confirmar con Eduardo**.
**Lección: un esquema propio fuera del perímetro NO es protección.** Cualquier almacenamiento nuevo necesita **dueño declarado, entrada en el manifiesto y acuerdo escrito**.

### 5.9 Otros escritores que existen y no están documentados
- **Un `pg_cron` DENTRO de la propia BD kubera**: existe el esquema `cron` con 1 job y 15 corridas. **Qué hace es NO VERIFICADO** (sospecha razonable: la retención de webhooks). Escritor invisible desde el repo: **no diseñes sobre tablas que quizá alguien más escribe** sin confirmarlo con Eduardo.
- Servicios Railway **no documentados** en CLAUDE.md: `MonitoreoOperaciones`, `MLREgisterDaily`, `Aplicacion_Excel`, `deltas-orders` (este último sin `cronSchedule` en `railway-deltas.json`; CLAUDE.md dice 07:15, **NO VERIFICADO**). Qué escriben: **NO VERIFICADO**.
- El scheduler embebido (`backend/services/scheduler.py`, APScheduler UTC, 8 jobs, `max_instances=1`): `sync_inventario` (15 min, alimenta `canal_inventario`→`channel.listings`), `pedidos_amazon`, `fba_watch`, `pedidos_m2e`, `odoo_watch`, `stock_watch`, **`drop_mirror`** (escribe `channel.listings` canal `general` en lotes de 1000 con `execute_values`) y `alertas_vigilante`.
- Webhook ML `orders_v2` **en ráfaga de milisegundos**: el `asyncio.Lock` por orden en `pedidos_ml.sincronizar` es lo único que evita duplicados (**164 duplicados reales el 17-jul antes del lock**).
- `docs/TABLAS_TEMPORALES.md` (pedido por Brandon el 28-jul) **exige anotar toda tabla nueva de MySQL en el mismo commit**, declarando si es permanente y por qué.

---

## 6. LAS PREGUNTAS DE DISEÑO QUE DEBES RESPONDER

Para cada una: presenta las opciones **con sus costos reales medidos**, elige una, argumenta, y di qué se rompe si se elige la otra.

**6.1 ¿Forma del almacenamiento?** Cuatro opciones:
- **(A) Tabla relacional por canal** — p.ej. `channel.ml_product_config`, `channel.amazon_product_config`, etc. **(NOMBRES PROPUESTOS: ninguna de esas tablas existe hoy.)** Ventaja: constraints reales, se puede indexar `ProductTaxCode` o `product_type`. Costo: N migraciones y N handlers; cada canal nuevo es DDL (y `shein` ya está declarado en `core.channels`).
- **(B) UNA tabla genérica producto×canal con `jsonb`** — el modelo que **ya existe y está vacío**: `enrich.ai_content`, PK `(sku, canal, cuenta)`. Y hay **dos precedentes VIVOS con datos y la misma forma**: `enrich.market_sku_config` PK `(sku, canal)` (1,584 filas) y `enrich.market_listing_metrics` PK `(sku, canal, cuenta, periodo)` (3,118). ¿Se reutiliza `ai_content` tal cual, se extiende, o se crea otra al lado?
- **(C) Columna `jsonb` en una tabla existente** — p.ej. `config jsonb` en `channel.listings` (PK `(sku, account_id, canal)`, ya tiene `product_type` y `category_id`). **Trampas verificadas**: `trg_hist_listings` escribe una fila de historia por campo que cambia (94,014 ya), y la tabla la escriben el sync de 15 min y `drop_mirror` — mezclarías "lo que el canal reporta" con "lo que yo decidí".
- **(D) Híbrido** — columnas tipadas para lo que se consulta/filtra (category_id, category_name, product_type, subCategory, clave SAT, estado) + `jsonb` para la cola larga de atributos.
- **(E) STATUS QUO extendido** — seguir en metas de Woo con namespace por canal (`wm_*`, `tk_*`, `tem_*`), y la tabla nueva solo como **espejo de lectura**. Es la más barata, la única que respeta literalmente "WooCommerce es la FUENTE DE VERDAD", reusa el patrón ya probado de `ml_categoria_id`/`ml_attr_*`, y sobrevive si Supabase cae. **Inclúyela en la matriz o justifica por qué la descartas.**

**Criterios de aceptación concretos** — el modelo elegido debe poder responder, y tienes que mostrar la consulta: (a) "dame todos los SKUs sin categoría de TikTok"; (b) "todos los que usan la exención de UPC de Disfraces"; (c) "todos los SKUs de ML de BEKURA con `SIZE_GRID_ID` asignado".

**6.2 ¿La PK es `(sku, canal)` o `(sku, canal, cuenta)`? ¿Y padre o variante?**
- Multi-cuenta: ML tiene **2 cuentas con configuración que diverge legítimamente** (los 15 chart_ids son distintos por cuenta: BEKURA 6, SANCORFASHION 9); la categoría no diverge. ¿Herencia canal→cuenta con override? Nota la inconsistencia existente: `channel.listings` usa `account_id uuid`, `channel.orders` usa `cuenta text`, `enrich.ai_content` tiene **las dos** (`cuenta text def ''` + `account_id uuid`).
- **Prerrequisito duro**: `core.accounts` no tiene fila para walmart/tiktok/temu. Cualquier PK con FK a `core.accounts(id)` deja fuera a 3 de los 5 canales hasta que **Eduardo inserte esas filas** — y eso es **escritura de datos en producción dentro de su perímetro**, candidata a mover un acta a `con_deltas`. Igual con el flip de `is_active` en `core.channels`.
- **Variantes**: `core.products` tiene `has_variations`/`parent_sku`/`wc_parent_id`; en Woo hay **7,202 `product_variation publish`**; **208 de los 987 posts con `ml_attr_*` son variaciones** y `_kubera_cbm` vive en 3,232 variaciones; TikTok convierte `SALES_PROPERTY` en SKUs distintos; ML sube el **PADRE**. ¿La config es a nivel padre, variante, o ambos?

**6.3 ¿El payload armado se guarda o se re-genera?** Usa los números de §3.5. Responde explícitamente:
- ¿Se persiste el payload de envío, solo la **configuración de entrada** (categoría + atributos + overrides), o **ambos con retención distinta**?
- ¿Y el **`_meta` de traza de IA** (media 583 B: `atributos_rechazados[]`, `confianza_ia`, `flags_ia[]`, `categoria_aproximada`)? Recuerda que **55 de 299 SKUs de TikTok fallaron en `armado`**, no en envío: esa traza tiene valor operativo propio.
- ¿Sigues el patrón `detail_ref`, sabiendo que **su destino (MySQL) se está retirando** y que **ya está roto para TikTok**?
- **Política de retención y proyección a 12 meses con 4 canales activos.** El proyecto ya tiene precedente (`ops.purgar_webhook_events`, 3 días, vía pg_cron) y ya tiene el contraejemplo (`amazon_backlog`, 186 MB).
- **Define el universo antes de proyectar MB**: no uses el `table_rows` estimado de `productos`. Los universos reales verificados son: `core.products` 22,186 · `productos` 5,381 (COUNT exacto) · publicable hoy por canal: ML 2,644 SKUs sobre 2 cuentas, amazon 1,790, general 13,042.

**6.4 ¿Dónde: Supabase kubera o MySQL propia?** Con §5.1 como restricción dura. Compara al menos: reusar `enrich.ai_content` · tabla nueva en `enrich` · tabla nueva en `channel` · esquema propio aparte (**§5.8 dice que eso no protege**) · MySQL (§5.5 dice que ese camino murió). **Cada opción tiene un precedente a favor o en contra: señálalo.** Di **quién es el dueño declarado** y qué acuerdo escrito hace falta. Incluye **RLS, grants y consumidor** (§5.7).

**6.5 ¿Versionado y vigencia?** Cuatro patrones ya existentes; elige y justifica:
- `costing.pricing_params` con **PK `(key, valid_from)`** — versionado por fecha, se lee el `valid_from` más reciente.
- `costing.cost_history` con `version integer` + `snapshot jsonb` + trigger.
- `enrich.ai_content` con **`hash_woo character(40)`** + `spec_version` + `estado` CHECK `('pendiente','ok','error','obsoleto')`.
- `costing.costos_finales.formula_ver` (valor real `'costos.py/v2-gold_pro-margen48-iva16-tarifaML202607'`) — los cálculos v1 y v2 no son comparables.

Preguntas concretas: **¿cómo se sabe que el producto cambió en Woo después de publicarse en el canal X?** ¿Cada cuánto se evalúa? ¿Y cuando el canal cambia su contrato bajo nuestros pies — Walmart pasó de spec 3.11 a 3.19 y **los `required` publicados NO coinciden con lo que rechazó producción** (Cocina rechazó por Talla y Género aunque el 3.19 dice que no los pide); Amazon puede cambiar el JSON Schema cacheado en disco? ¿Dónde vive el `spec_version` contra el que se armó cada envío?

**6.6 ¿Qué es estado ACTUAL y qué es BITÁCORA?** Hoy están mezclados:
- BITÁCORA (1 fila por intento): `ml_backlog` 6,014 · `amazon_backlog` 4,358 · **`ops.channel_submissions` 22,933** · `ml_image_edit_backlog` 12,582 · `crear_logs` 2,439.
- ESTADO (1 fila por sku×canal): `ml_progress` 4,128 · `amazon_progress` 1,790 · `channel.listings` 19,679 · `channel.product_category` 13,723 · `canal_inventario` 6,281 · **`fanout_log` es escritor de estado por (sku,canal) y hoy está fuera del mapa**.
- **Walmart y TikTok no tienen tabla de estado en ningún lado.** Y `ops.channel_submissions` es bitácora — **no la conviertas en estado** — pero hoy es el único registro de que TikTok publicó 360 productos.

Propuesta base a criticar: **(1) configuración deseada** (categoría, atributos, overrides humanos) · **(2) último envío / estado vivo** (listing_id, ASIN, product_id, status) · **(3) bitácora de intentos**. ¿Son 3 tablas, 2, o una con vistas? ¿Qué pasa con las que ya existen?
**Modela también el "no publicable en este canal + motivo" como dato de primera clase**: 55 SKUs de TikTok sin categoría resoluble, 108 SKUs de ML bloqueados por guías de tallas, 451 categorías de TikTok `INVITE_ONLY`, 19 saltados por ropa/calzado y 28 sin fotos. Hoy eso solo vive en `tk_sin_categoria.json` y en un texto de error.

**6.7 ¿Cómo conviven los ids del canal con los nombres legibles?**
**Brandon pidió explícitamente guardar las categorías como ID *y* como nombre** (requisito de negocio declarado por él; no es verificable en BD). Precedentes verificados:
- Woo: `ml_categoria_id` (5,165) + `ml_categoria_path` (5,172) + `ml_categoria_niveles` JSON (3,102) + 6 metas de nivel.
- `channel.categories`: `category_id, name, path, parent_id, root_id, root_name` — **pero solo mercado_libre**.
- `channel.product_category`: **solo el id** + `source`.
- `categorias_ml` (MySQL): `category_id, category_name, ruta, cat1..cat4, fuente`.
- `tk_payloads.json._meta`: `categoria_id` **y** `categoria` (ruta con ` → `). `tk_categorias_ok.json`: `{id, ruta}`.
- `wm_categorias.json`: `subcategory` **y** `nombre_es` — y en Walmart **el nombre en español ES funcional**, es la clave del bloque `Visible`.

Preguntas: ¿el nombre es dato **desnormalizado** (rápido, se pudre cuando el canal renombra) o se resuelve por **JOIN** contra un catálogo? ¿Se guarda además el nombre **como estaba al publicar** (snapshot)? ¿Se extiende `channel.categories` a los otros 3 canales (su PK `(channel_id, category_id)` ya lo permite) o cada canal lleva su catálogo? **¿Dónde vive el árbol de 2,168 categorías de TikTok** (con `is_leaf` y `permission_statuses`) **y las 75 subcategorías de Walmart con su lista de `obligatorios`?** ¿Y el **locale**: TikTok devuelve `local_name` con `locale=es-MX`, Amazon exige `language_tag='es_MX'` — ¿un solo campo `name` sin idioma aguanta?

**6.8 ¿Dónde vive lo que hoy está HARDCODEADO?** `CATEGORIAS_AUTORIZADAS` de `publicar_walmart.py:84-127` (folio + clave SAT por categoría), las 15 entradas de `CHARTS_BY_ACCOUNT` en `size_chart_mapping.py`, `MARCA_ID='7650172564119684872'` y `ALMACEN_VENTAS='7647893424175580935'` de TikTok. ¿Config de código o dato de base? Argumenta.

**6.9 ¿Qué se hace con lo que hoy SOLO vive en Woo?** `ml_attr_<ID>` (987 posts / 992 claves), `_kubera_size_chart` (8), `_kubera_editar_imagenes` (377), la familia `_kubera_*` de costeo/CBM, las metas `revision_*` (2,401). `enrich.ai_attributes` existe **vacía** para esto desde el día uno. ¿Migran, se espejan, o se declaran fuente permanente en Woo?

**6.10 Contrato de acceso, lectura en caliente y concurrencia.**
- **Escritura humana**: hoy solo hay camino para ML (`POST /api/crear/categoria-ml`) y Amazon (`POST /api/publicar/amazon/tipo`). ¿Quién y desde dónde edita la categoría/atributos de Walmart, TikTok y Temu? ¿Qué significa `source='panel'` en `channel.product_category` para esos canales?
- **Lectura en caliente**: hoy publicar lee de Woo y **no depende de Supabase**. Si la config pasa a kubera, publicar queda atado al pooler 6543 (pool 6, `blocking=False`, `connect_timeout=4`, `statement_timeout=4000`, precedente de `TooManyConnections`). **¿Qué se hace si kubera no responde al armar el payload: caché local, degradar a Woo, o abortar?**
- **Idempotencia y concurrencia**: define la clave de idempotencia y el comportamiento ante escrituras simultáneas (sync de 15 min + `drop_mirror` en lotes de 1000 + script de lote + panel + espejo con afinidad de cola). El proyecto ya pagó ese impuesto: 164 pedidos duplicados el 17-jul.
- **Canal 5**: `shein` ya está declarado. ¿Cómo entra un canal nuevo **sin DDL**? ¿El diseño asume que los canales convergen a `/api/publicar`, o soporta que uno se llene desde un script suelto?

---

## 7. QUÉ DEBES ENTREGAR

**(0) Verificación previa.** Reconecta en solo lectura y **confirma o corrige** los conteos y estructuras de §3 que uses como base de una decisión. Lista qué cambió respecto a este documento y qué sigues sin poder verificar. Intenta cerrar al menos estos NO VERIFICADOS: (a) qué `SUPABASE_WRITE_*` están en `true` hoy, (b) el CSV real de `KUBERA_MIRROR_TABLAS`, (c) qué hace el job de `pg_cron` dentro de kubera, (d) las políticas de `pg_policies`, (e) si el módulo Competencia está roto hoy en producción, (f) si hay un `0008` aplicado sin versionar. Si no puedes, dilo y declara el supuesto.

**(1) Inventario del estado actual, en una sola tabla.** Filas = cada pieza de información por producto y canal (categoría id, categoría nombre, atributos, product type, subCategory, clave SAT, folio de exención, brand_id, warehouse_id, chart_id, imágenes, precio, stock, estado de publicación, payload, resultado, motivo de no-publicable). Columnas = `Mercado Libre | Amazon | Walmart | TikTok | Temu`. Celdas = **dónde vive HOY exactamente** (tabla.columna, nombre de meta, o ruta de archivo) o `NO EXISTE`; añade **quién lo escribe (archivo:línea), quién lo lee, y si tiene espejo**. Marca `[NV]` lo que no verificaste tú.

**(2) Las opciones (A–E de §6.1) comparadas** en una matriz sobre criterios explícitos: costo de agregar un canal nuevo, costo de agregar un campo nuevo, capacidad de consultar/filtrar (**muestra las 3 consultas de §6.1**), integridad referencial, peso en disco (**números medidos de §3.5, con el universo definido en §6.3**), fricción con el perímetro de Eduardo, resiliencia si Supabase cae, y **plan de reversión de cada una**.

**(3) LA RECOMENDACIÓN, una sola, argumentada**, con los **tres contras que asumes conscientemente**. Incluye un **veredicto explícito sobre `enrich.ai_content`**: usarla tal cual, extenderla o sustituirla — y si no la usas, **por qué no** (recordando que adoptarla NO evita escribir su DDL retroactivo).

**(4) DDL concreto y completo**, listo para que Eduardo lo revise: `CREATE TABLE` con tipos exactos, PK, FKs (contra `core.products(sku)` / `core.channels(id)` / `core.accounts(id)` — **y solo esas, verifica que la FK que invocas existe**), CHECKs, índices (di cuáles son GIN sobre jsonb y por qué), triggers si los hay, **RLS y grants**, y el **`DROP`/rollback**. Numerado a partir de `0016` (el `0015` está apartado para el drop del 18-ago; **confírmalo con `ls supabase/migrations/` antes de fijarlo**), con la nota de regenerar `supabase/schema_manifest.json` y de encajar en la guardia `--acepto-destino tukwcvsi`. **No lo ejecutes.**

**(5) Plan de llenado desde lo que YA existe**, fuente por fuente y con conteo esperado: `ml_categoria_id`+`path`+`niveles` (5,165/5,172/3,102) · `ml_attr_<ID>` (987 posts, 992 claves) · `amazon_progress.product_type` (1,790) · `categorias_ml` (12,839) · `channel.product_category` (13,723) · `atributos_ia` (5,380) · `tk_payloads.json` (244) y `tk_categorias_ok.json` (125) · `elec_items.json`+`elec244_items.json` (587) · `wm_categorias.json` (75) · `tk_categorias.json` (2,168) · el dict hardcodeado de Walmart (2 categorías). Di qué es **backfill one-shot** y qué es **seam vivo**, qué SKUs quedan **sin origen** (todo Walmart salvo el dict; todo Temu; TikTok fuera de los 244 del lote), y **si el backfill genera actas con `seam_gap>0`** (rompe rachas).
**Marca con URGENCIA lo que hay que rescatar ANTES de que el scratchpad desaparezca**: es un directorio temporal de sesión con los 244 payloads de TikTok publicados, los 587 de Walmart y el mapa de 125 categorías.

**(6) Qué se deprecaría y en qué orden**, con la condición de retiro y **quién lo lee hoy (archivo:línea)**: metas de Woo que dejarían de ser fuente · `atributos_ia` · los JSON del scratchpad · `ml_progress`/`amazon_progress` · **`enrich.ai_attributes` (0 filas — ¿se usa, se extiende o se retira?)** · **`enrich.ai_content` (0 filas, DDL en `0010`, PK `(sku, canal, cuenta)` — decide explícitamente si es tu tabla o si estorba)** · los 3.8 MB duplicados de `MX_SPEC.json`/`MX_MP_ITEM_INTL_SPEC.json` + el `spec.json` de 143 B que es un 301.

**(7) Cambios de código necesarios, archivo por archivo, SIN escribirlos**: qué adaptador deja de leer la meta de Woo, qué handler nuevo necesita `kubera_mirror._UPSERTS`, qué entrada nueva necesita el `CENSO`, qué endpoint expone la escritura humana por canal, y qué variable de Railway habría que tocar (**marcando que eso es flujo vivo**).

**(8) Riesgos y qué NO resuelve la propuesta.** Incluye explícitamente: la contradicción de spec Walmart 3.11 vs 3.19; que las dimensiones de Woo son CBM reconstruido y no medidas (Walmart cobra el mayor entre volumétrico y real, y Kubera absorbe el flete); el precedente `propuestas_retirado`; qué pasa si `SUPABASE_WRITE_*` está apagado; los escritores no documentados de §5.9; y el desajuste de 194 órdenes.

**(9) Preguntas abiertas, en DOS listas separadas**: las que necesitan el dale de **Brandon** (negocio, encender flujo, `KUBERA_MIRROR_TABLAS`, `SUPABASE_WRITE_*`, cualquier escritor de producción) y las que necesitan a **Eduardo** (DDL, esquema, manifiesto, altas en `core.accounts`/`core.channels`, actas, y qué hace su `pg_cron`).

---

**Reglas de salida:** español, directo, sin adornos. Nombres de tablas y columnas exactos. Cuando cites un número, di de dónde salió. Cuando supongas algo, escribe `SUPUESTO:` delante; cuando no lo hayas verificado tú, `NO VERIFICADO:`; cuando sea un nombre que estás inventando, `PROPUESTO:`. **No ejecutes ningún cambio: esto es una propuesta para que Brandon y Eduardo decidan.**