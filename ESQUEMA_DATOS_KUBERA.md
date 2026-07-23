# Esquema de datos — Kubera Omnicanal

Inventario completo de las **3 bases de datos** que alimentan la plataforma. Generado desde el esquema en vivo.

| Base | Motor | Host | Tablas |
|---|---|---|---|
| `u531713409_kubera_ml` | MySQL/MariaDB | Hostinger (srv1249.hstgr.io) | 25 |
| `u531713409_TiYxu` (WordPress `wp_`) | MySQL/MariaDB | Hostinger | 72 |
| Supabase `xaxbkijcxzvrwyrqnjzi` | PostgreSQL | Supabase (aws-us-west-2) | 18 |

**Cómo se conectan:** `sku` une todo · `ml_item_id`/`seller_sku` → Mercado Libre · `account_id`/`cuenta` → cuenta (BEKURA / San Corpe) · `wc_id`/`post_id` → WordPress.


---

## kubera_ml (MySQL · Hostinger) — 25 tablas

### `amazon_backlog` · 3 109 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | int(11) | PK |
| `sku` | varchar(100) |  |
| `wc_id` | int(11) |  |
| `seller_id` | varchar(20) |  |
| `marketplace_id` | varchar(20) |  |
| `submission_id` | varchar(60) |  |
| `product_type` | varchar(80) |  |
| `status` | varchar(20) |  |
| `success` | tinyint(1) |  |
| `issue_count` | int(11) |  |
| `issues` | longtext |  |
| `payload` | longtext |  |
| `amz_response` | longtext |  |
| `submitted_at` | datetime |  |
| `published_at` | datetime |  |

### `amazon_progress` · 1 442 filas

| Campo | Tipo | Llave |
|---|---|---|
| `sku` | varchar(100) | PK |
| `wc_id` | int(11) |  |
| `seller_id` | varchar(20) |  |
| `marketplace_id` | varchar(20) |  |
| `asin` | varchar(20) |  |
| `product_type` | varchar(80) |  |
| `submission_id` | varchar(60) |  |
| `status` | varchar(20) |  |
| `success` | tinyint(1) |  |
| `error_label` | varchar(200) |  |
| `issue_count` | int(11) |  |
| `last_submitted` | datetime |  |
| `published_at` | datetime |  |
| `updated_at` | datetime |  |

### `atributos_ia` · 5 381 filas

| Campo | Tipo | Llave |
|---|---|---|
| `sku` | varchar(60) | PK |
| `estado` | varchar(20) |  |
| `atributos_json` | longtext |  |
| `atributos_str` | text |  |
| `num_atributos` | int(11) |  |
| `atributos_validos` | tinyint(1) |  |
| `flags` | longtext |  |
| `modelo_ia` | varchar(60) |  |
| `procesado_at` | datetime |  |
| `updated_at` | datetime |  |

### `backlog_errores` · 1 984 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | int(11) | PK |
| `sku` | varchar(60) |  |
| `paso` | varchar(40) |  |
| `estado` | varchar(30) |  |
| `detalle` | text |  |
| `reintentado` | tinyint(1) |  |
| `reintento_ts` | datetime |  |
| `ts` | datetime |  |

### `canal_inventario` · 4 645 filas

| Campo | Tipo | Llave |
|---|---|---|
| `sku` | varchar(60) | PK |
| `canal` | varchar(20) | PK |
| `cuenta` | varchar(50) | PK |
| `item_id` | varchar(60) |  |
| `precio` | decimal(12,2) |  |
| `stock_real` | int(11) |  |
| `stock_full` | int(11) |  |
| `stock_fba` | int(11) |  |
| `es_full` | tinyint(1) |  |
| `logistica` | varchar(30) |  |
| `situacion` | varchar(30) |  |
| `moneda` | varchar(5) |  |
| `updated_at` | datetime |  |

### `categorias_ml` · 12 840 filas

| Campo | Tipo | Llave |
|---|---|---|
| `sku` | varchar(60) | PK |
| `category_id` | varchar(30) |  |
| `category_name` | varchar(255) |  |
| `ruta` | varchar(512) |  |
| `cat1` | varchar(255) |  |
| `cat2` | varchar(255) |  |
| `cat3` | varchar(255) |  |
| `cat4` | varchar(255) |  |
| `fuente` | varchar(20) |  |
| `updated_at` | datetime |  |

### `costos_finales` · 4 230 filas

| Campo | Tipo | Llave |
|---|---|---|
| `sku` | varchar(60) | PK |
| `costo_producto` | decimal(14,4) |  |
| `costo_cbm` | decimal(14,4) |  |
| `costo_unitario` | decimal(14,4) |  |
| `costo_comision` | decimal(14,4) |  |
| `costo_fee_envio` | decimal(14,4) |  |
| `precio_sugerido` | decimal(14,2) |  |
| `precio_base` | decimal(14,2) |  |
| `largo` | decimal(8,2) |  |
| `alto` | decimal(8,2) |  |
| `ancho` | decimal(8,2) |  |
| `peso` | decimal(8,3) |  |
| `ml_cat_id` | varchar(30) |  |
| `pct_comision` | decimal(6,4) |  |
| `peso_origen` | varchar(20) |  |
| `created_at` | datetime |  |
| `updated_at` | datetime |  |

### `costos_logs` · 6 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) | PK |
| `sku` | varchar(64) |  |
| `accion` | varchar(16) |  |
| `origen` | varchar(32) |  |
| `detalle` | longtext |  |
| `created_at` | timestamp |  |

### `costos_ml` · 4 832 filas

| Campo | Tipo | Llave |
|---|---|---|
| `sku` | varchar(60) | PK |
| `ml_cat_id` | varchar(30) |  |
| `ml_cat_nombre` | varchar(255) |  |
| `pct_comision` | decimal(6,4) |  |
| `fee_envio` | decimal(10,2) |  |
| `iva_mnt` | decimal(10,2) |  |
| `total_costos_ml` | decimal(10,2) |  |
| `margen` | decimal(6,4) |  |
| `precio_sugerido` | decimal(10,2) |  |
| `precio_base` | decimal(10,2) |  |
| `descuento_pct` | decimal(6,4) |  |
| `ganancia_neta` | decimal(10,2) |  |
| `roi` | decimal(6,4) |  |
| `ml_estado` | varchar(20) |  |
| `calculado_at` | datetime |  |
| `updated_at` | datetime |  |
| `precio_sugerido_original` | decimal(10,2) |  |

### `costos_validados` · 15 349 filas

| Campo | Tipo | Llave |
|---|---|---|
| `sku` | varchar(100) | PK |
| `wc_id` | int(11) |  |
| `wc_status` | varchar(30) |  |
| `wc_type` | varchar(30) |  |
| `contenedor` | varchar(80) |  |
| `costo_producto` | decimal(14,4) |  |
| `costo_cbm` | decimal(14,4) |  |
| `largo` | decimal(8,2) |  |
| `alto` | decimal(8,2) |  |
| `ancho` | decimal(8,2) |  |
| `peso` | decimal(8,3) |  |
| `costo_total` | decimal(14,4) |  |
| `created_at` | datetime |  |
| `cajas` | decimal(12,2) |  |
| `piezas_por_caja` | decimal(12,2) |  |

### `imagenes_producto` · 3 618 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | int(11) | PK |
| `sku` | varchar(60) |  |
| `tipo` | varchar(20) |  |
| `url_alibaba` | text |  |
| `path_local` | text |  |
| `url_cdn` | text |  |
| `gemini_ok` | tinyint(1) |  |
| `gemini_fallback` | tinyint(1) |  |
| `subida_wc` | tinyint(1) |  |
| `wc_media_id` | int(11) |  |
| `subida_ml` | tinyint(1) |  |
| `ml_picture_id` | varchar(60) |  |
| `created_at` | datetime |  |
| `updated_at` | datetime |  |

### `ml_backlog` · 5 217 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | int(11) | PK |
| `run_key` | varchar(150) |  |
| `cuenta` | varchar(50) |  |
| `sku` | varchar(100) |  |
| `wc_id` | int(11) |  |
| `ml_item_id` | varchar(60) |  |
| `ml_url` | text |  |
| `success` | tinyint(1) |  |
| `error` | text |  |
| `ml_status` | smallint(6) |  |
| `desc_status` | smallint(6) |  |
| `pics_preuploaded` | tinyint(4) |  |
| `payload` | longtext |  |
| `ml_response` | longtext |  |
| `published_at` | datetime |  |
| `created_at` | datetime |  |
| `gtin_error` | tinyint(1) |  |

### `ml_estado` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `sku` | varchar(60) | PK |
| `precio_ok` | tinyint(1) |  |
| `dims_ok` | tinyint(1) |  |
| `attributes_ok` | tinyint(1) |  |
| `imagenes_ok` | tinyint(1) |  |
| `success_pct` | tinyint(4) |  |
| `workflow` | varchar(30) |  |
| `falta` | varchar(255) |  |
| `publicado_ml` | tinyint(1) |  |
| `ml_item_id` | varchar(30) |  |
| `updated_at` | datetime |  |

### `ml_image_edit_backlog` · 10 540 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | int(11) | PK |
| `run_key` | varchar(150) |  |
| `cuenta` | varchar(50) |  |
| `sku` | varchar(100) |  |
| `wc_id` | int(11) |  |
| `wc_image_id` | int(11) |  |
| `src_url` | text |  |
| `flag_quitar_fondo` | tinyint(1) |  |
| `flag_traducir_texto` | tinyint(1) |  |
| `flag_cambiar_modelo` | tinyint(1) |  |
| `action` | varchar(20) |  |
| `person_desc` | text |  |
| `prompt_used` | text |  |
| `gemini_model` | varchar(60) |  |
| `gemini_success` | tinyint(1) |  |
| `gemini_error` | text |  |
| `bytes_in` | int(11) |  |
| `bytes_out` | int(11) |  |
| `wp_media_id_new` | int(11) |  |
| `wp_url_new` | text |  |
| `ml_picture_id` | varchar(60) |  |
| `created_at` | datetime |  |

### `ml_progress` · 3 644 filas

| Campo | Tipo | Llave |
|---|---|---|
| `prog_key` | varchar(150) | PK |
| `cuenta` | varchar(50) |  |
| `sku` | varchar(100) |  |
| `wc_id` | int(11) |  |
| `ml_item_id` | varchar(60) |  |
| `ml_url` | text |  |
| `success` | tinyint(1) |  |
| `error` | text |  |
| `gtin_error` | tinyint(1) |  |
| `dry_run` | tinyint(1) |  |
| `published_at` | datetime |  |
| `updated_at` | datetime |  |

### `ml_tokens` · 2 filas

| Campo | Tipo | Llave |
|---|---|---|
| `cuenta` | varchar(50) | PK |
| `access_token` | varchar(500) |  |
| `refresh_token` | varchar(500) |  |
| `updated_at` | datetime |  |

### `ml_tokens_dashboard` · 2 filas

| Campo | Tipo | Llave |
|---|---|---|
| `cuenta` | varchar(50) | PK |
| `app_id` | varchar(50) |  |
| `access_token` | varchar(500) |  |
| `refresh_token` | varchar(500) |  |
| `client_secret` | varchar(500) |  |
| `updated_at` | datetime |  |

### `odoo_ranking` · 7 950 filas

| Campo | Tipo | Llave |
|---|---|---|
| `sku` | varchar(60) | PK |
| `nombre` | text |  |
| `stock` | int(11) |  |
| `costo` | decimal(10,2) |  |
| `flete_unit` | decimal(10,2) |  |
| `ganancia_unit` | decimal(10,2) |  |
| `ganancia_total` | decimal(10,2) |  |
| `cbm_unit` | decimal(10,6) |  |
| `cbm_total` | decimal(10,6) |  |
| `peso_kg` | decimal(8,3) |  |
| `score` | decimal(12,2) |  |
| `en_wc` | tinyint(1) |  |
| `apto` | tinyint(1) |  |
| `motivo_excluido` | varchar(255) |  |
| `updated_at` | datetime |  |

### `odoo_sync_backlog` · 1 649 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | int(11) | PK |
| `ts` | datetime |  |
| `sku` | varchar(60) |  |
| `nombre` | varchar(255) |  |
| `accion` | varchar(40) |  |
| `error` | varchar(255) |  |
| `detalle` | text |  |
| `preexistente_en_wc` | tinyint(1) |  |

### `odoo_sync_procesados` · 3 980 filas

| Campo | Tipo | Llave |
|---|---|---|
| `sku` | varchar(60) | PK |
| `synced_at` | datetime |  |

### `pipeline_runs` · 200 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | int(11) | PK |
| `run_ts` | datetime |  |
| `tag` | varchar(60) |  |
| `paso` | varchar(40) |  |
| `total` | int(11) |  |
| `ok` | int(11) |  |
| `fallback` | int(11) |  |
| `sin_datos` | int(11) |  |
| `errores` | int(11) |  |
| `duracion_s` | int(11) |  |
| `modo` | varchar(30) |  |
| `notas` | text |  |

### `productos` · 5 382 filas

| Campo | Tipo | Llave |
|---|---|---|
| `sku` | varchar(60) | PK |
| `wc_id` | int(11) |  |
| `odoo_id` | int(11) |  |
| `nombre` | text |  |
| `status_wc` | varchar(30) |  |
| `workflow` | varchar(30) |  |
| `tags` | varchar(255) |  |
| `tag_publicado` | tinyint(1) |  |
| `categorias` | varchar(255) |  |
| `variaciones` | tinyint(1) |  |
| `precio` | decimal(10,2) |  |
| `precio_base` | decimal(10,2) |  |
| `tiene_precio` | tinyint(1) |  |
| `tiene_sale_price` | tinyint(1) |  |
| `num_fotos` | int(11) |  |
| `num_fotos_local` | int(11) |  |
| `alerta_fotos` | varchar(20) |  |
| `peso_kg` | decimal(8,3) |  |
| `dims_cm` | varchar(60) |  |
| `stock_odoo` | int(11) |  |
| `costo_odoo` | decimal(10,4) |  |
| `procesado_ts` | datetime |  |
| `created_at` | datetime |  |
| `updated_at` | datetime |  |
| `wc_parent_id` | int(11) |  |
| `costo_unitario` | decimal(10,4) |  |
| `pieza_largo_cm` | decimal(8,2) |  |
| `pieza_ancho_cm` | decimal(8,2) |  |
| `pieza_altura_cm` | decimal(8,2) |  |

### `scraping_alibaba` · 4 911 filas

| Campo | Tipo | Llave |
|---|---|---|
| `sku` | varchar(60) | PK |
| `url_alibaba` | text |  |
| `scrape_estado` | varchar(20) |  |
| `intentos` | int(11) |  |
| `url_rota` | tinyint(1) |  |
| `alibaba_titulo` | text |  |
| `caracteristicas_clave` | text |  |
| `descripcion_proveedor` | text |  |
| `alibaba_precio_min` | decimal(10,4) |  |
| `alibaba_precio_max` | decimal(10,4) |  |
| `alibaba_moneda` | varchar(10) |  |
| `tipo_cambio` | decimal(6,2) |  |
| `min_usd_mxn` | decimal(10,2) |  |
| `peso_kg` | decimal(8,3) |  |
| `dims_cm` | varchar(60) |  |
| `peso_dims_ok` | tinyint(1) |  |
| `cbm_producto` | decimal(10,6) |  |
| `costo_unitario_cbm` | decimal(10,4) |  |
| `serpapi_urls` | longtext |  |
| `urls_intentadas` | longtext |  |
| `imagenes_urls` | longtext |  |
| `n_imagenes` | int(11) |  |
| `scraped_at` | datetime |  |
| `updated_at` | datetime |  |

### `sync_procesados` · 2 791 filas

| Campo | Tipo | Llave |
|---|---|---|
| `sku` | varchar(60) | PK |
| `wc_id` | int(11) |  |
| `accion` | varchar(20) |  |
| `synced_at` | datetime |  |

### `webhook_eventos` · 1 424 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) | PK |
| `canal` | varchar(20) |  |
| `topic` | varchar(40) |  |
| `resource` | varchar(200) |  |
| `user_id` | varchar(40) |  |
| `cuenta` | varchar(50) |  |
| `sku` | varchar(60) |  |
| `procesado` | tinyint(1) |  |
| `resultado` | varchar(255) |  |
| `recibido` | datetime |  |


---

## WordPress / WooCommerce (MySQL · Hostinger) — 72 tablas

### `wp_actionscheduler_actions` · 25 646 filas

| Campo | Tipo | Llave |
|---|---|---|
| `action_id` | bigint(20) unsigned | PK |
| `hook` | varchar(191) |  |
| `status` | varchar(20) |  |
| `scheduled_date_gmt` | datetime |  |
| `scheduled_date_local` | datetime |  |
| `priority` | tinyint(3) unsigned |  |
| `args` | varchar(191) |  |
| `schedule` | longtext |  |
| `group_id` | bigint(20) unsigned |  |
| `attempts` | int(11) |  |
| `last_attempt_gmt` | datetime |  |
| `last_attempt_local` | datetime |  |
| `claim_id` | bigint(20) unsigned |  |
| `extended_args` | varchar(8000) |  |

### `wp_actionscheduler_claims` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `claim_id` | bigint(20) unsigned | PK |
| `date_created_gmt` | datetime |  |

### `wp_actionscheduler_groups` · 16 filas

| Campo | Tipo | Llave |
|---|---|---|
| `group_id` | bigint(20) unsigned | PK |
| `slug` | varchar(255) |  |

### `wp_actionscheduler_logs` · 79 990 filas

| Campo | Tipo | Llave |
|---|---|---|
| `log_id` | bigint(20) unsigned | PK |
| `action_id` | bigint(20) unsigned |  |
| `message` | text |  |
| `log_date_gmt` | datetime |  |
| `log_date_local` | datetime |  |

### `wp_atum_order_itemmeta` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `meta_id` | bigint(20) unsigned | PK |
| `order_item_id` | bigint(20) unsigned |  |
| `meta_key` | varchar(255) |  |
| `meta_value` | longtext |  |

### `wp_atum_order_items` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `order_item_id` | bigint(20) unsigned | PK |
| `order_item_name` | text |  |
| `order_item_type` | varchar(200) |  |
| `order_id` | bigint(20) unsigned |  |

### `wp_atum_product_data` · 21 661 filas

| Campo | Tipo | Llave |
|---|---|---|
| `product_id` | bigint(20) | PK |
| `purchase_price` | double |  |
| `supplier_id` | bigint(20) |  |
| `supplier_sku` | varchar(100) |  |
| `atum_controlled` | tinyint(1) |  |
| `out_stock_date` | datetime |  |
| `out_stock_threshold` | double |  |
| `inheritable` | tinyint(1) |  |
| `inbound_stock` | double |  |
| `stock_on_hold` | double |  |
| `sold_today` | double |  |
| `sales_last_days` | double |  |
| `reserved_stock` | double |  |
| `customer_returns` | double |  |
| `warehouse_damage` | double |  |
| `lost_in_post` | double |  |
| `other_logs` | double |  |
| `out_stock_days` | int(11) |  |
| `lost_sales` | double |  |
| `has_location` | tinyint(1) |  |
| `update_date` | datetime |  |
| `atum_stock_status` | varchar(15) |  |
| `restock_status` | tinyint(1) |  |
| `is_bom` | tinyint(1) |  |
| `sales_update_date` | datetime |  |
| `barcode` | varchar(256) |  |
| `committed_to_wc` | double |  |
| `calc_backorders` | double |  |

### `wp_commentmeta` · 2 filas

| Campo | Tipo | Llave |
|---|---|---|
| `meta_id` | bigint(20) unsigned | PK |
| `comment_id` | bigint(20) unsigned |  |
| `meta_key` | varchar(255) |  |
| `meta_value` | longtext |  |

### `wp_comments` · 18 filas

| Campo | Tipo | Llave |
|---|---|---|
| `comment_ID` | bigint(20) unsigned | PK |
| `comment_post_ID` | bigint(20) unsigned |  |
| `comment_author` | tinytext |  |
| `comment_author_email` | varchar(100) |  |
| `comment_author_url` | varchar(200) |  |
| `comment_author_IP` | varchar(100) |  |
| `comment_date` | datetime |  |
| `comment_date_gmt` | datetime |  |
| `comment_content` | text |  |
| `comment_karma` | int(11) |  |
| `comment_approved` | varchar(20) |  |
| `comment_agent` | varchar(255) |  |
| `comment_type` | varchar(20) |  |
| `comment_parent` | bigint(20) unsigned |  |
| `user_id` | bigint(20) unsigned |  |

### `wp_commercekit_ajs_product_index` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) | PK |
| `product_id` | bigint(20) |  |
| `title` | text |  |
| `description` | text |  |
| `short_description` | text |  |
| `product_sku` | varchar(100) |  |
| `variation_sku` | text |  |
| `product_gtin` | varchar(100) |  |
| `variation_gtin` | text |  |
| `attributes` | text |  |
| `product_url` | varchar(255) |  |
| `product_img` | text |  |
| `in_stock` | tinyint(1) |  |
| `is_visible` | tinyint(1) |  |
| `status` | varchar(100) |  |
| `lang` | varchar(50) |  |
| `other_lang` | text |  |
| `other_urls` | text |  |

### `wp_commercekit_searches` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | int(11) | PK |
| `search_term` | varchar(100) |  |
| `search_count` | int(11) |  |
| `click_count` | int(11) |  |
| `no_result_count` | int(11) |  |

### `wp_commercekit_sg_post_meta` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) | PK |
| `post_id` | bigint(20) |  |
| `active` | tinyint(1) |  |
| `sg_prod` | bigint(20) |  |
| `sg_cat` | bigint(20) |  |
| `sg_tag` | bigint(20) |  |
| `sg_attr` | varchar(255) |  |

### `wp_commercekit_swatches_cache_count` · 1 689 filas

| Campo | Tipo | Llave |
|---|---|---|
| `product_id` | bigint(20) |  |
| `cached` | tinyint(1) |  |
| `updated` | bigint(20) |  |

### `wp_commercekit_waitlist` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | int(11) | PK |
| `email` | varchar(255) |  |
| `product_id` | bigint(20) |  |
| `mail_sent` | tinyint(1) |  |
| `created` | bigint(20) |  |
| `updated` | bigint(20) |  |
| `tracked` | tinyint(1) |  |
| `optin_status` | tinyint(1) |  |
| `optin_key` | varchar(100) |  |

### `wp_commercekit_wishlist` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | int(11) | PK |
| `session_key` | varchar(100) |  |

### `wp_commercekit_wishlist_items` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | int(11) | PK |
| `user_id` | int(11) |  |
| `list_id` | int(11) |  |
| `product_id` | bigint(20) |  |
| `created` | bigint(20) |  |
| `tracked` | tinyint(1) |  |

### `wp_e_events` · 2 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) unsigned | PK |
| `event_data` | text |  |
| `created_at` | datetime |  |

### `wp_hostinger_reach_carts` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `hash` | varchar(100) | PK |
| `customer_id` | bigint(20) unsigned |  |
| `customer_email` | varchar(100) |  |
| `items` | longtext |  |
| `totals` | text |  |
| `currency` | varchar(3) |  |
| `status` | varchar(100) |  |
| `updated_at` | datetime |  |

### `wp_hostinger_reach_contact_lists` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | mediumint(9) | PK |
| `name` | varchar(255) |  |

### `wp_hostinger_reach_forms` · 3 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | mediumint(9) | PK |
| `form_id` | varchar(255) |  |
| `form_title` | varchar(255) |  |
| `post_id` | int(11) |  |
| `contact_list_id` | int(11) |  |
| `type` | varchar(255) |  |
| `is_active` | tinyint(1) |  |
| `submissions` | int(10) unsigned |  |

### `wp_kam_revision_tasks` · 2 698 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) unsigned | PK |
| `product_id` | bigint(20) unsigned |  |
| `user_id` | bigint(20) unsigned |  |
| `assigned_by` | bigint(20) unsigned |  |
| `assigned_date` | date |  |
| `status` | varchar(20) |  |
| `completed_at` | datetime |  |
| `created_at` | datetime |  |

### `wp_links` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `link_id` | bigint(20) unsigned | PK |
| `link_url` | varchar(255) |  |
| `link_name` | varchar(255) |  |
| `link_image` | varchar(255) |  |
| `link_target` | varchar(25) |  |
| `link_description` | varchar(255) |  |
| `link_visible` | varchar(20) |  |
| `link_owner` | bigint(20) unsigned |  |
| `link_rating` | int(11) |  |
| `link_updated` | datetime |  |
| `link_rel` | varchar(255) |  |
| `link_notes` | mediumtext |  |
| `link_rss` | varchar(255) |  |

### `wp_litespeed_avatar` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) unsigned | PK |
| `url` | varchar(1000) |  |
| `md5` | varchar(128) |  |
| `dateline` | int(11) |  |

### `wp_litespeed_url` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) | PK |
| `url` | varchar(500) |  |
| `cache_tags` | varchar(1000) |  |

### `wp_litespeed_url_file` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) | PK |
| `url_id` | bigint(20) |  |
| `vary` | varchar(32) |  |
| `filename` | varchar(32) |  |
| `type` | tinyint(4) |  |
| `mobile` | tinyint(4) |  |
| `webp` | tinyint(4) |  |
| `expired` | int(11) |  |

### `wp_ml_categorias` · 12 256 filas

| Campo | Tipo | Llave |
|---|---|---|
| `ml_cat_id` | varchar(20) | PK |
| `name` | varchar(255) |  |
| `name_norm` | varchar(255) |  |
| `path` | text |  |
| `path_norm` | text |  |
| `leaf` | tinyint(1) |  |
| `parent_id` | varchar(20) |  |
| `domain_id` | varchar(60) |  |
| `domain_name` | varchar(255) |  |

### `wp_options` · 50 666 filas

| Campo | Tipo | Llave |
|---|---|---|
| `option_id` | bigint(20) unsigned | PK |
| `option_name` | varchar(191) |  |
| `option_value` | longtext |  |
| `autoload` | varchar(20) |  |

### `wp_postmeta` · 612 187 filas

| Campo | Tipo | Llave |
|---|---|---|
| `meta_id` | bigint(20) unsigned | PK |
| `post_id` | bigint(20) unsigned |  |
| `meta_key` | varchar(255) |  |
| `meta_value` | longtext |  |

### `wp_posts` · 77 354 filas

| Campo | Tipo | Llave |
|---|---|---|
| `ID` | bigint(20) unsigned | PK |
| `post_author` | bigint(20) unsigned |  |
| `post_date` | datetime |  |
| `post_date_gmt` | datetime |  |
| `post_content` | longtext |  |
| `post_title` | text |  |
| `post_excerpt` | text |  |
| `post_status` | varchar(20) |  |
| `comment_status` | varchar(20) |  |
| `ping_status` | varchar(20) |  |
| `post_password` | varchar(255) |  |
| `post_name` | varchar(200) |  |
| `to_ping` | text |  |
| `pinged` | text |  |
| `post_modified` | datetime |  |
| `post_modified_gmt` | datetime |  |
| `post_content_filtered` | longtext |  |
| `post_parent` | bigint(20) unsigned |  |
| `guid` | varchar(255) |  |
| `menu_order` | int(11) |  |
| `post_type` | varchar(20) |  |
| `post_mime_type` | varchar(100) |  |
| `comment_count` | bigint(20) |  |

### `wp_sm_task_details` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) | PK |
| `task_id` | bigint(20) |  |
| `record_id` | bigint(20) |  |
| `status` | enum('in-progress','completed','scheduled') |  |
| `field` | text |  |
| `action` | varchar(255) |  |
| `prev_val` | longtext |  |
| `updated_val` | longtext |  |

### `wp_sm_tasks` · 60 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) | PK |
| `title` | text |  |
| `date` | datetime |  |
| `completed_date` | datetime |  |
| `post_type` | text |  |
| `author` | int(11) |  |
| `type` | enum('inline','bulk_edit','external','imported') |  |
| `status` | enum('in-progress','completed','scheduled') |  |
| `actions` | longtext |  |
| `record_count` | bigint(20) |  |

### `wp_term_relationships` · 36 062 filas

| Campo | Tipo | Llave |
|---|---|---|
| `object_id` | bigint(20) unsigned | PK |
| `term_taxonomy_id` | bigint(20) unsigned | PK |
| `term_order` | int(11) |  |

### `wp_term_taxonomy` · 2 654 filas

| Campo | Tipo | Llave |
|---|---|---|
| `term_taxonomy_id` | bigint(20) unsigned | PK |
| `term_id` | bigint(20) unsigned |  |
| `taxonomy` | varchar(32) |  |
| `description` | longtext |  |
| `parent` | bigint(20) unsigned |  |
| `count` | bigint(20) |  |

### `wp_termmeta` · 4 247 filas

| Campo | Tipo | Llave |
|---|---|---|
| `meta_id` | bigint(20) unsigned | PK |
| `term_id` | bigint(20) unsigned |  |
| `meta_key` | varchar(255) |  |
| `meta_value` | longtext |  |

### `wp_terms` · 2 654 filas

| Campo | Tipo | Llave |
|---|---|---|
| `term_id` | bigint(20) unsigned | PK |
| `name` | varchar(200) |  |
| `slug` | varchar(200) |  |
| `term_group` | bigint(10) |  |

### `wp_usermeta` · 563 filas

| Campo | Tipo | Llave |
|---|---|---|
| `umeta_id` | bigint(20) unsigned | PK |
| `user_id` | bigint(20) unsigned |  |
| `meta_key` | varchar(255) |  |
| `meta_value` | longtext |  |

### `wp_users` · 13 filas

| Campo | Tipo | Llave |
|---|---|---|
| `ID` | bigint(20) unsigned | PK |
| `user_login` | varchar(60) |  |
| `user_pass` | varchar(255) |  |
| `user_nicename` | varchar(50) |  |
| `user_email` | varchar(100) |  |
| `user_url` | varchar(100) |  |
| `user_registered` | datetime |  |
| `user_activation_key` | varchar(255) |  |
| `user_status` | int(11) |  |
| `display_name` | varchar(250) |  |

### `wp_wc_admin_note_actions` · 90 filas

| Campo | Tipo | Llave |
|---|---|---|
| `action_id` | bigint(20) unsigned | PK |
| `note_id` | bigint(20) unsigned |  |
| `name` | varchar(255) |  |
| `label` | varchar(255) |  |
| `query` | longtext |  |
| `status` | varchar(255) |  |
| `actioned_text` | varchar(255) |  |
| `nonce_action` | varchar(255) |  |
| `nonce_name` | varchar(255) |  |

### `wp_wc_admin_notes` · 64 filas

| Campo | Tipo | Llave |
|---|---|---|
| `note_id` | bigint(20) unsigned | PK |
| `name` | varchar(255) |  |
| `type` | varchar(20) |  |
| `locale` | varchar(20) |  |
| `title` | longtext |  |
| `content` | longtext |  |
| `content_data` | longtext |  |
| `status` | varchar(200) |  |
| `source` | varchar(200) |  |
| `date_created` | datetime |  |
| `date_reminder` | datetime |  |
| `is_snoozable` | tinyint(1) |  |
| `layout` | varchar(20) |  |
| `image` | varchar(200) |  |
| `is_deleted` | tinyint(1) |  |
| `is_read` | tinyint(1) |  |
| `icon` | varchar(200) |  |

### `wp_wc_category_lookup` · 1 610 filas

| Campo | Tipo | Llave |
|---|---|---|
| `category_tree_id` | bigint(20) unsigned | PK |
| `category_id` | bigint(20) unsigned | PK |

### `wp_wc_customer_lookup` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `customer_id` | bigint(20) unsigned | PK |
| `user_id` | bigint(20) unsigned |  |
| `username` | varchar(60) |  |
| `first_name` | varchar(255) |  |
| `last_name` | varchar(255) |  |
| `email` | varchar(100) |  |
| `date_last_active` | timestamp |  |
| `date_registered` | timestamp |  |
| `country` | char(2) |  |
| `postcode` | varchar(20) |  |
| `city` | varchar(100) |  |
| `state` | varchar(100) |  |

### `wp_wc_download_log` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `download_log_id` | bigint(20) unsigned | PK |
| `timestamp` | datetime |  |
| `permission_id` | bigint(20) unsigned |  |
| `user_id` | bigint(20) unsigned |  |
| `user_ip_address` | varchar(100) |  |

### `wp_wc_order_addresses` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) unsigned | PK |
| `order_id` | bigint(20) unsigned |  |
| `address_type` | varchar(20) |  |
| `first_name` | text |  |
| `last_name` | text |  |
| `company` | text |  |
| `address_1` | text |  |
| `address_2` | text |  |
| `city` | text |  |
| `state` | text |  |
| `postcode` | text |  |
| `country` | text |  |
| `email` | varchar(320) |  |
| `phone` | varchar(100) |  |

### `wp_wc_order_coupon_lookup` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `order_id` | bigint(20) unsigned | PK |
| `coupon_id` | bigint(20) | PK |
| `date_created` | datetime |  |
| `discount_amount` | double |  |

### `wp_wc_order_operational_data` · 4 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) unsigned | PK |
| `order_id` | bigint(20) unsigned |  |
| `created_via` | varchar(100) |  |
| `woocommerce_version` | varchar(20) |  |
| `prices_include_tax` | tinyint(1) |  |
| `coupon_usages_are_counted` | tinyint(1) |  |
| `download_permission_granted` | tinyint(1) |  |
| `cart_hash` | varchar(100) |  |
| `new_order_email_sent` | tinyint(1) |  |
| `order_key` | varchar(100) |  |
| `order_stock_reduced` | tinyint(1) |  |
| `date_paid_gmt` | datetime |  |
| `date_completed_gmt` | datetime |  |
| `shipping_tax_amount` | decimal(26,8) |  |
| `shipping_total_amount` | decimal(26,8) |  |
| `discount_tax_amount` | decimal(26,8) |  |
| `discount_total_amount` | decimal(26,8) |  |
| `recorded_sales` | tinyint(1) |  |

### `wp_wc_order_product_lookup` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `order_item_id` | bigint(20) unsigned | PK |
| `order_id` | bigint(20) unsigned | PK |
| `product_id` | bigint(20) unsigned |  |
| `variation_id` | bigint(20) unsigned |  |
| `customer_id` | bigint(20) unsigned |  |
| `date_created` | datetime |  |
| `product_qty` | int(11) |  |
| `product_net_revenue` | double |  |
| `product_gross_revenue` | double |  |
| `coupon_amount` | double |  |
| `tax_amount` | double |  |
| `shipping_amount` | double |  |
| `shipping_tax_amount` | double |  |

### `wp_wc_order_stats` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `order_id` | bigint(20) unsigned | PK |
| `parent_id` | bigint(20) unsigned |  |
| `date_created` | datetime |  |
| `date_created_gmt` | datetime |  |
| `date_paid` | datetime |  |
| `date_completed` | datetime |  |
| `num_items_sold` | int(11) |  |
| `total_sales` | double |  |
| `tax_total` | double |  |
| `shipping_total` | double |  |
| `net_total` | double |  |
| `returning_customer` | tinyint(1) |  |
| `status` | varchar(20) |  |
| `customer_id` | bigint(20) unsigned |  |

### `wp_wc_order_tax_lookup` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `order_id` | bigint(20) unsigned | PK |
| `tax_rate_id` | bigint(20) unsigned | PK |
| `date_created` | datetime |  |
| `shipping_tax` | double |  |
| `order_tax` | double |  |
| `total_tax` | double |  |

### `wp_wc_orders` · 4 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) unsigned | PK |
| `status` | varchar(20) |  |
| `currency` | varchar(10) |  |
| `type` | varchar(20) |  |
| `tax_amount` | decimal(26,8) |  |
| `total_amount` | decimal(26,8) |  |
| `customer_id` | bigint(20) unsigned |  |
| `billing_email` | varchar(320) |  |
| `date_created_gmt` | datetime |  |
| `date_updated_gmt` | datetime |  |
| `parent_order_id` | bigint(20) unsigned |  |
| `payment_method` | varchar(100) |  |
| `payment_method_title` | text |  |
| `transaction_id` | varchar(100) |  |
| `ip_address` | varchar(100) |  |
| `user_agent` | text |  |
| `customer_note` | text |  |

### `wp_wc_orders_meta` · 12 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint(20) unsigned | PK |
| `order_id` | bigint(20) unsigned |  |
| `meta_key` | varchar(255) |  |
| `meta_value` | text |  |

### `wp_wc_product_attributes_lookup` · 3 filas

| Campo | Tipo | Llave |
|---|---|---|
| `product_id` | bigint(20) | PK |
| `product_or_parent_id` | bigint(20) | PK |
| `taxonomy` | varchar(32) | PK |
| `term_id` | bigint(20) | PK |
| `is_variation_attribute` | tinyint(1) |  |
| `in_stock` | tinyint(1) |  |

### `wp_wc_product_download_directories` · 2 filas

| Campo | Tipo | Llave |
|---|---|---|
| `url_id` | bigint(20) unsigned | PK |
| `url` | varchar(256) |  |
| `enabled` | tinyint(1) |  |

### `wp_wc_product_meta_lookup` · 15 425 filas

| Campo | Tipo | Llave |
|---|---|---|
| `product_id` | bigint(20) | PK |
| `sku` | varchar(100) |  |
| `global_unique_id` | varchar(100) |  |
| `virtual` | tinyint(1) |  |
| `downloadable` | tinyint(1) |  |
| `min_price` | decimal(19,4) |  |
| `max_price` | decimal(19,4) |  |
| `onsale` | tinyint(1) |  |
| `stock_quantity` | double |  |
| `stock_status` | varchar(100) |  |
| `rating_count` | bigint(20) |  |
| `average_rating` | decimal(3,2) |  |
| `total_sales` | bigint(20) |  |
| `tax_status` | varchar(100) |  |
| `tax_class` | varchar(100) |  |

### `wp_wc_rate_limits` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `rate_limit_id` | bigint(20) unsigned | PK |
| `rate_limit_key` | varchar(200) |  |
| `rate_limit_expiry` | bigint(20) unsigned |  |
| `rate_limit_remaining` | smallint(10) |  |

### `wp_wc_reserved_stock` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `order_id` | bigint(20) | PK |
| `product_id` | bigint(20) | PK |
| `stock_quantity` | double |  |
| `timestamp` | datetime |  |
| `expires` | datetime |  |

### `wp_wc_tax_rate_classes` · 2 filas

| Campo | Tipo | Llave |
|---|---|---|
| `tax_rate_class_id` | bigint(20) unsigned | PK |
| `name` | varchar(200) |  |
| `slug` | varchar(200) |  |

### `wp_wc_webhooks` · 8 filas

| Campo | Tipo | Llave |
|---|---|---|
| `webhook_id` | bigint(20) unsigned | PK |
| `status` | varchar(200) |  |
| `name` | text |  |
| `user_id` | bigint(20) unsigned |  |
| `delivery_url` | text |  |
| `secret` | text |  |
| `topic` | varchar(200) |  |
| `date_created` | datetime |  |
| `date_created_gmt` | datetime |  |
| `date_modified` | datetime |  |
| `date_modified_gmt` | datetime |  |
| `api_version` | smallint(4) |  |
| `failure_count` | smallint(10) |  |
| `pending_delivery` | tinyint(1) |  |

### `wp_woocommerce_api_keys` · 6 filas

| Campo | Tipo | Llave |
|---|---|---|
| `key_id` | bigint(20) unsigned | PK |
| `user_id` | bigint(20) unsigned |  |
| `description` | varchar(200) |  |
| `permissions` | varchar(10) |  |
| `consumer_key` | char(64) |  |
| `consumer_secret` | char(43) |  |
| `nonces` | longtext |  |
| `truncated_key` | char(7) |  |
| `last_access` | datetime |  |

### `wp_woocommerce_attribute_taxonomies` · 406 filas

| Campo | Tipo | Llave |
|---|---|---|
| `attribute_id` | bigint(20) unsigned | PK |
| `attribute_name` | varchar(200) |  |
| `attribute_label` | varchar(200) |  |
| `attribute_type` | varchar(20) |  |
| `attribute_orderby` | varchar(20) |  |
| `attribute_public` | int(1) |  |

### `wp_woocommerce_downloadable_product_permissions` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `permission_id` | bigint(20) unsigned | PK |
| `download_id` | varchar(36) |  |
| `product_id` | bigint(20) unsigned |  |
| `order_id` | bigint(20) unsigned |  |
| `order_key` | varchar(200) |  |
| `user_email` | varchar(200) |  |
| `user_id` | bigint(20) unsigned |  |
| `downloads_remaining` | varchar(9) |  |
| `access_granted` | datetime |  |
| `access_expires` | datetime |  |
| `download_count` | bigint(20) unsigned |  |

### `wp_woocommerce_log` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `log_id` | bigint(20) unsigned | PK |
| `timestamp` | datetime |  |
| `level` | smallint(4) |  |
| `source` | varchar(200) |  |
| `message` | longtext |  |
| `context` | longtext |  |

### `wp_woocommerce_order_itemmeta` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `meta_id` | bigint(20) unsigned | PK |
| `order_item_id` | bigint(20) unsigned |  |
| `meta_key` | varchar(255) |  |
| `meta_value` | longtext |  |

### `wp_woocommerce_order_items` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `order_item_id` | bigint(20) unsigned | PK |
| `order_item_name` | text |  |
| `order_item_type` | varchar(200) |  |
| `order_id` | bigint(20) unsigned |  |

### `wp_woocommerce_payment_tokenmeta` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `meta_id` | bigint(20) unsigned | PK |
| `payment_token_id` | bigint(20) unsigned |  |
| `meta_key` | varchar(255) |  |
| `meta_value` | longtext |  |

### `wp_woocommerce_payment_tokens` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `token_id` | bigint(20) unsigned | PK |
| `gateway_id` | varchar(200) |  |
| `token` | text |  |
| `user_id` | bigint(20) unsigned |  |
| `type` | varchar(200) |  |
| `is_default` | tinyint(1) |  |

### `wp_woocommerce_sessions` · 309 filas

| Campo | Tipo | Llave |
|---|---|---|
| `session_id` | bigint(20) unsigned | PK |
| `session_key` | char(32) |  |
| `session_value` | longtext |  |
| `session_expiry` | bigint(20) unsigned |  |

### `wp_woocommerce_shipping_zone_locations` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `location_id` | bigint(20) unsigned | PK |
| `zone_id` | bigint(20) unsigned |  |
| `location_code` | varchar(200) |  |
| `location_type` | varchar(40) |  |

### `wp_woocommerce_shipping_zone_methods` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `zone_id` | bigint(20) unsigned |  |
| `instance_id` | bigint(20) unsigned | PK |
| `method_id` | varchar(200) |  |
| `method_order` | bigint(20) unsigned |  |
| `is_enabled` | tinyint(1) |  |

### `wp_woocommerce_shipping_zones` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `zone_id` | bigint(20) unsigned | PK |
| `zone_name` | varchar(200) |  |
| `zone_order` | bigint(20) unsigned |  |

### `wp_woocommerce_tax_rate_locations` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `location_id` | bigint(20) unsigned | PK |
| `location_code` | varchar(200) |  |
| `tax_rate_id` | bigint(20) unsigned |  |
| `location_type` | varchar(40) |  |

### `wp_woocommerce_tax_rates` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `tax_rate_id` | bigint(20) unsigned | PK |
| `tax_rate_country` | varchar(2) |  |
| `tax_rate_state` | varchar(200) |  |
| `tax_rate` | varchar(8) |  |
| `tax_rate_name` | varchar(200) |  |
| `tax_rate_priority` | bigint(20) unsigned |  |
| `tax_rate_compound` | int(1) |  |
| `tax_rate_shipping` | int(1) |  |
| `tax_rate_order` | bigint(20) unsigned |  |
| `tax_rate_class` | varchar(200) |  |

### `wp_wp_phpmyadmin_extension__errors_log` · 0 filas

| Campo | Tipo | Llave |
|---|---|---|
| `id` | int(50) | PK |
| `gmdate` | datetime |  |
| `function_name` | longtext |  |
| `function_args` | longtext |  |
| `message` | longtext |  |


---

## Supabase (PostgreSQL) — 18 tablas

### `competition_cache`

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint | PK |
| `ml_item_id` | text |  |
| `keyword` | text |  |
| `items` | jsonb |  |
| `fetched_at` | timestamp with time zone |  |

### `competitor_watchlist`

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint | PK |
| `our_ml_item_id` | text |  |
| `competitor_ml_id` | text |  |
| `competitor_url` | text |  |
| `title` | text |  |
| `thumbnail` | text |  |
| `seller` | text |  |
| `brand` | text |  |
| `initial_price` | numeric |  |
| `current_price` | numeric |  |
| `current_status` | text |  |
| `is_active` | boolean |  |
| `paused_streak_days` | integer |  |
| `last_checked_at` | timestamp with time zone |  |
| `created_at` | timestamp with time zone |  |
| `updated_at` | timestamp with time zone |  |
| `catalog_id` | text |  |

### `cron_runs`

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint | PK |
| `job_name` | text |  |
| `started_at` | timestamp with time zone |  |
| `finished_at` | timestamp with time zone |  |
| `status` | text |  |
| `accounts_processed` | integer |  |
| `products_processed` | integer |  |
| `changes_detected` | integer |  |
| `sales_inserted` | integer |  |
| `error_message` | text |  |

### `daily_sales`

| Campo | Tipo | Llave |
|---|---|---|
| `date` | date | PK |
| `cuenta` | text | PK |
| `item_id` | text | PK |
| `sku` | text |  |
| `title` | text |  |
| `is_full` | boolean |  |
| `units_sold` | integer |  |
| `revenue` | numeric |  |
| `gross_revenue` | numeric |  |
| `sale_fee` | numeric |  |
| `created_at` | timestamp with time zone |  |
| `updated_at` | timestamp with time zone |  |
| `node_id` | text |  |

### `daily_stock`

| Campo | Tipo | Llave |
|---|---|---|
| `date` | date | PK |
| `cuenta` | text | PK |
| `item_id` | text | PK |
| `sku` | text |  |
| `status` | text |  |
| `logistic_type` | text |  |
| `stock_full` | integer |  |
| `stock_odoo` | integer |  |
| `price` | numeric |  |
| `size_category` | text |  |
| `created_at` | timestamp with time zone |  |
| `title` | text |  |
| `dimensions` | text |  |
| `start_time` | timestamp with time zone |  |
| `warehouse` | text |  |

### `daily_visits`

| Campo | Tipo | Llave |
|---|---|---|
| `date` | date | PK |
| `cuenta` | text | PK |
| `item_id` | text | PK |
| `sku` | text |  |
| `title` | text |  |
| `is_full` | boolean |  |
| `visits` | integer |  |
| `created_at` | timestamp with time zone |  |
| `updated_at` | timestamp with time zone |  |
| `start_time` | timestamp with time zone |  |

### `goals`

| Campo | Tipo | Llave |
|---|---|---|
| `id` | uuid | PK |
| `week_start` | date |  |
| `target_amount` | numeric |  |
| `currency` | text |  |
| `note` | text |  |
| `created_at` | timestamp with time zone |  |
| `updated_at` | timestamp with time zone |  |
| `account_id` | uuid |  |

### `metrics_daily`

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint | PK |
| `account_id` | uuid |  |
| `metric_date` | date |  |
| `visits_total` | integer |  |
| `conversion_rate` | numeric |  |
| `questions_count` | integer |  |
| `items_active` | integer |  |
| `items_paused` | integer |  |
| `raw` | jsonb |  |
| `captured_at` | timestamp with time zone |  |

### `ml_accounts`

| Campo | Tipo | Llave |
|---|---|---|
| `id` | uuid | PK |
| `ml_user_id` | bigint |  |
| `nickname` | text |  |
| `label` | text |  |
| `is_active` | boolean |  |
| `created_at` | timestamp with time zone |  |
| `updated_at` | timestamp with time zone |  |

### `notifications`

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint | PK |
| `kind` | text |  |
| `payload` | jsonb |  |
| `is_read` | boolean |  |
| `created_at` | timestamp with time zone |  |
| `created_date` | date |  |

### `product_changes`

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint | PK |
| `account_id` | uuid |  |
| `ml_item_id` | text |  |
| `snapshot_date` | date |  |
| `field_name` | text |  |
| `old_value` | text |  |
| `new_value` | text |  |
| `detected_at` | timestamp with time zone |  |

### `products_snapshot`

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint | PK |
| `account_id` | uuid |  |
| `ml_item_id` | text |  |
| `snapshot_date` | date |  |
| `title` | text |  |
| `price` | numeric |  |
| `original_price` | numeric |  |
| `available_quantity` | integer |  |
| `sold_quantity` | integer |  |
| `status` | text |  |
| `listing_type_id` | text |  |
| `condition` | text |  |
| `permalink` | text |  |
| `thumbnail` | text |  |
| `category_id` | text |  |
| `health` | numeric |  |
| `raw` | jsonb |  |
| `captured_at` | timestamp with time zone |  |
| `seller_sku` | text |  |
| `inventory_id` | text |  |
| `family_id` | text |  |
| `domain_id` | text |  |
| `sub_status` | jsonb |  |
| `tags` | jsonb |  |
| `attributes_map` | jsonb |  |
| `accepts_mercadopago` | boolean |  |
| `free_shipping` | boolean |  |
| `shipping_mode` | text |  |
| `pictures_count` | integer |  |
| `variations_count` | integer |  |
| `variations` | jsonb |  |
| `last_updated` | timestamp with time zone |  |
| `start_time` | timestamp with time zone |  |
| `stop_time` | timestamp with time zone |  |
| `warranty` | text |  |
| `visits_30d` | integer |  |
| `visits_7d` | integer |  |
| `visits_1d` | integer |  |

### `reporte_monitoreo_competencia_30day`

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint | PK |
| `competitor_watchlist_id` | bigint |  |
| `competitor_ml_id` | text |  |
| `period_year` | integer |  |
| `period_month` | integer |  |
| `days_observed` | integer |  |
| `changes_count` | integer |  |
| `last_change_date` | date |  |
| `price_first` | numeric |  |
| `price_last` | numeric |  |
| `price_min` | numeric |  |
| `price_max` | numeric |  |
| `price_avg` | numeric |  |
| `changes_history` | jsonb |  |
| `created_at` | timestamp with time zone |  |

### `reporte_monitoreo_competencia_diario`

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint | PK |
| `competitor_watchlist_id` | bigint |  |
| `competitor_ml_id` | text |  |
| `price` | numeric |  |
| `original_price` | numeric |  |
| `status` | text |  |
| `available_quantity` | integer |  |
| `sold_quantity` | integer |  |
| `recorded_date` | date |  |
| `recorded_at` | timestamp with time zone |  |
| `raw` | jsonb |  |

### `restock_config`

| Campo | Tipo | Llave |
|---|---|---|
| `sku` | text | PK |
| `bollinger_window` | integer |  |
| `bollinger_k` | numeric |  |
| `floor_days` | integer |  |
| `ceiling_days` | integer |  |
| `lead_time_days` | integer |  |
| `override_min` | integer |  |
| `override_max` | integer |  |
| `notes` | text |  |
| `created_at` | timestamp with time zone |  |
| `updated_at` | timestamp with time zone |  |

### `sales`

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint | PK |
| `account_id` | uuid |  |
| `ml_order_id` | bigint |  |
| `ml_item_id` | text |  |
| `title` | text |  |
| `quantity` | integer |  |
| `unit_price` | numeric |  |
| `total_amount` | numeric |  |
| `currency_id` | text |  |
| `status` | text |  |
| `sold_at` | timestamp with time zone |  |
| `buyer_id` | bigint |  |
| `raw` | jsonb |  |
| `inserted_at` | timestamp with time zone |  |
| `paid_amount` | numeric |  |

### `sales_monthly_archive`

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint | PK |
| `account_id` | uuid |  |
| `period_year` | integer |  |
| `period_month` | integer |  |
| `orders_count` | integer |  |
| `units` | integer |  |
| `gross_total` | numeric |  |
| `net_total` | numeric |  |
| `cancelled_total` | numeric |  |
| `cancelled_orders` | integer |  |
| `weekly_breakdown` | jsonb |  |
| `created_at` | timestamp with time zone |  |

### `sales_weekly_archive`

| Campo | Tipo | Llave |
|---|---|---|
| `id` | bigint | PK |
| `account_id` | uuid |  |
| `week_start` | date |  |
| `week_end` | date |  |
| `iso_year` | integer |  |
| `iso_week` | integer |  |
| `orders_count` | integer |  |
| `units` | integer |  |
| `gross_total` | numeric |  |
| `net_total` | numeric |  |
| `cancelled_total` | numeric |  |
| `cancelled_orders` | integer |  |
| `daily_breakdown` | jsonb |  |
| `created_at` | timestamp with time zone |  |

