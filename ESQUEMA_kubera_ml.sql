-- DDL kubera_ml — generado 2026-07-08

CREATE TABLE `amazon_backlog` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `sku` varchar(100) NOT NULL,
  `wc_id` int(11) DEFAULT NULL,
  `seller_id` varchar(20) DEFAULT NULL,
  `marketplace_id` varchar(20) DEFAULT NULL,
  `submission_id` varchar(60) DEFAULT NULL,
  `product_type` varchar(80) DEFAULT NULL,
  `status` varchar(20) DEFAULT NULL COMMENT 'ACCEPTED/INVALID/VALID/ERROR',
  `success` tinyint(1) NOT NULL DEFAULT 0,
  `issue_count` int(11) DEFAULT 0,
  `issues` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL COMMENT 'Lista completa de issues retornados' CHECK (json_valid(`issues`)),
  `payload` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL COMMENT 'Payload enviado a Amazon' CHECK (json_valid(`payload`)),
  `amz_response` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL COMMENT 'Respuesta completa de Amazon' CHECK (json_valid(`amz_response`)),
  `submitted_at` datetime NOT NULL DEFAULT current_timestamp(),
  `published_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_sku` (`sku`),
  KEY `idx_status` (`status`),
  KEY `idx_success` (`success`),
  KEY `idx_submitted` (`submitted_at`)
) ENGINE=InnoDB AUTO_INCREMENT=3110 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci COMMENT='Historico de submissions a Amazon — 1 fila por intento';

CREATE TABLE `amazon_progress` (
  `sku` varchar(100) NOT NULL,
  `wc_id` int(11) DEFAULT NULL,
  `seller_id` varchar(20) DEFAULT NULL,
  `marketplace_id` varchar(20) DEFAULT NULL,
  `asin` varchar(20) DEFAULT NULL COMMENT 'ASIN asignado por Amazon (cuando lo asigna)',
  `product_type` varchar(80) DEFAULT NULL COMMENT 'ej WALLET, CADDY, HOME',
  `submission_id` varchar(60) DEFAULT NULL COMMENT 'Ultimo submissionId',
  `status` varchar(20) DEFAULT NULL COMMENT 'ACCEPTED/INVALID/VALID/PUBLISHED/ERROR',
  `success` tinyint(1) NOT NULL DEFAULT 0,
  `error_label` varchar(200) DEFAULT NULL COMMENT 'Resumen del error principal si fallo',
  `issue_count` int(11) DEFAULT 0 COMMENT '# issues en ultima submission',
  `last_submitted` datetime DEFAULT NULL,
  `published_at` datetime DEFAULT NULL COMMENT 'Cuando se confirmo visible (summaries)',
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`sku`),
  KEY `idx_status` (`status`),
  KEY `idx_success` (`success`),
  KEY `idx_product_type` (`product_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci COMMENT='Estado actual de publicaciones Amazon — 1 fila por SKU';

CREATE TABLE `atributos_ia` (
  `sku` varchar(60) NOT NULL,
  `estado` varchar(20) DEFAULT 'pendiente',
  `atributos_json` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`atributos_json`)),
  `atributos_str` text DEFAULT NULL,
  `num_atributos` int(11) DEFAULT 0,
  `atributos_validos` tinyint(1) DEFAULT 0,
  `flags` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`flags`)),
  `modelo_ia` varchar(60) DEFAULT NULL,
  `procesado_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`sku`),
  KEY `idx_estado` (`estado`),
  KEY `idx_atrib_validos` (`atributos_validos`),
  CONSTRAINT `atributos_ia_ibfk_1` FOREIGN KEY (`sku`) REFERENCES `productos` (`sku`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `backlog_errores` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `sku` varchar(60) DEFAULT NULL,
  `paso` varchar(40) DEFAULT NULL,
  `estado` varchar(30) DEFAULT NULL,
  `detalle` text DEFAULT NULL,
  `reintentado` tinyint(1) DEFAULT 0,
  `reintento_ts` datetime DEFAULT NULL,
  `ts` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_sku` (`sku`),
  KEY `idx_paso` (`paso`),
  KEY `idx_estado` (`estado`),
  KEY `idx_reintentado` (`reintentado`)
) ENGINE=InnoDB AUTO_INCREMENT=1987 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `canal_inventario` (
  `sku` varchar(60) NOT NULL,
  `canal` varchar(20) NOT NULL,
  `cuenta` varchar(50) NOT NULL DEFAULT '',
  `item_id` varchar(60) DEFAULT NULL,
  `precio` decimal(12,2) DEFAULT NULL,
  `stock_real` int(11) DEFAULT NULL,
  `stock_full` int(11) DEFAULT NULL,
  `stock_fba` int(11) DEFAULT NULL,
  `es_full` tinyint(1) NOT NULL DEFAULT 0,
  `logistica` varchar(30) DEFAULT NULL,
  `situacion` varchar(30) DEFAULT NULL,
  `moneda` varchar(5) NOT NULL DEFAULT 'MXN',
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`sku`,`canal`,`cuenta`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `categorias_ml` (
  `sku` varchar(60) NOT NULL,
  `category_id` varchar(30) DEFAULT NULL,
  `category_name` varchar(255) DEFAULT NULL,
  `ruta` varchar(512) DEFAULT NULL,
  `cat1` varchar(255) DEFAULT NULL,
  `cat2` varchar(255) DEFAULT NULL,
  `cat3` varchar(255) DEFAULT NULL,
  `cat4` varchar(255) DEFAULT NULL,
  `fuente` varchar(20) DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`sku`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `costos_finales` (
  `sku` varchar(60) NOT NULL,
  `costo_producto` decimal(14,4) DEFAULT NULL,
  `costo_cbm` decimal(14,4) DEFAULT NULL,
  `costo_unitario` decimal(14,4) DEFAULT NULL,
  `costo_comision` decimal(14,4) DEFAULT NULL,
  `costo_fee_envio` decimal(14,4) DEFAULT NULL,
  `precio_sugerido` decimal(14,2) DEFAULT NULL,
  `precio_base` decimal(14,2) DEFAULT NULL,
  `largo` decimal(8,2) DEFAULT NULL,
  `alto` decimal(8,2) DEFAULT NULL,
  `ancho` decimal(8,2) DEFAULT NULL,
  `peso` decimal(8,3) DEFAULT NULL,
  `ml_cat_id` varchar(30) DEFAULT NULL,
  `pct_comision` decimal(6,4) DEFAULT NULL,
  `peso_origen` varchar(20) DEFAULT NULL,
  `created_at` datetime DEFAULT current_timestamp(),
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`sku`),
  KEY `idx_ml_cat` (`ml_cat_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `costos_logs` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `sku` varchar(64) NOT NULL,
  `accion` varchar(16) NOT NULL,
  `origen` varchar(32) NOT NULL,
  `detalle` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`detalle`)),
  `created_at` timestamp NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_sku` (`sku`),
  KEY `idx_created` (`created_at`)
) ENGINE=InnoDB AUTO_INCREMENT=8 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `costos_ml` (
  `sku` varchar(60) NOT NULL,
  `ml_cat_id` varchar(30) DEFAULT NULL,
  `ml_cat_nombre` varchar(255) DEFAULT NULL,
  `pct_comision` decimal(6,4) DEFAULT NULL,
  `fee_envio` decimal(10,2) DEFAULT NULL,
  `iva_mnt` decimal(10,2) DEFAULT NULL,
  `total_costos_ml` decimal(10,2) DEFAULT NULL,
  `margen` decimal(6,4) DEFAULT 0.4800,
  `precio_sugerido` decimal(10,2) DEFAULT NULL,
  `precio_base` decimal(10,2) DEFAULT NULL,
  `descuento_pct` decimal(6,4) DEFAULT NULL,
  `ganancia_neta` decimal(10,2) DEFAULT NULL,
  `roi` decimal(6,4) DEFAULT NULL,
  `ml_estado` varchar(20) DEFAULT 'pendiente',
  `calculado_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `precio_sugerido_original` decimal(10,2) DEFAULT NULL COMMENT 'Precio sugerido recalculado con costo_unitario real del Excel (Validados_Con_SKU)',
  PRIMARY KEY (`sku`),
  KEY `idx_ml_estado` (`ml_estado`),
  CONSTRAINT `costos_ml_ibfk_1` FOREIGN KEY (`sku`) REFERENCES `productos` (`sku`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `costos_validados` (
  `sku` varchar(100) NOT NULL,
  `wc_id` int(11) DEFAULT NULL,
  `wc_status` varchar(30) DEFAULT NULL,
  `wc_type` varchar(30) DEFAULT NULL,
  `contenedor` varchar(80) DEFAULT NULL,
  `costo_producto` decimal(14,4) DEFAULT NULL,
  `costo_cbm` decimal(14,4) DEFAULT NULL,
  `largo` decimal(8,2) DEFAULT NULL,
  `alto` decimal(8,2) DEFAULT NULL,
  `ancho` decimal(8,2) DEFAULT NULL,
  `peso` decimal(8,3) DEFAULT NULL,
  `costo_total` decimal(14,4) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `cajas` decimal(12,2) DEFAULT NULL,
  `piezas_por_caja` decimal(12,2) DEFAULT NULL,
  PRIMARY KEY (`sku`),
  KEY `idx_cv_wc_id` (`wc_id`),
  KEY `idx_cv_status` (`wc_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci COMMENT='Costos validados WC (parents+simples) — 1 fila por SKU único';

CREATE TABLE `imagenes_producto` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `sku` varchar(60) NOT NULL,
  `tipo` varchar(20) NOT NULL,
  `url_alibaba` text DEFAULT NULL,
  `path_local` text DEFAULT NULL,
  `url_cdn` text DEFAULT NULL,
  `gemini_ok` tinyint(1) DEFAULT 0,
  `gemini_fallback` tinyint(1) DEFAULT 0,
  `subida_wc` tinyint(1) DEFAULT 0,
  `wc_media_id` int(11) DEFAULT NULL,
  `subida_ml` tinyint(1) DEFAULT 0,
  `ml_picture_id` varchar(60) DEFAULT NULL,
  `created_at` datetime DEFAULT current_timestamp(),
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_sku_tipo` (`sku`,`tipo`),
  KEY `idx_gemini_fallback` (`gemini_fallback`),
  KEY `idx_subida_wc` (`subida_wc`),
  KEY `idx_subida_ml` (`subida_ml`),
  CONSTRAINT `imagenes_producto_ibfk_1` FOREIGN KEY (`sku`) REFERENCES `productos` (`sku`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=4462 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `ml_backlog` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `run_key` varchar(150) NOT NULL COMMENT 'cuenta:sku',
  `cuenta` varchar(50) NOT NULL,
  `sku` varchar(100) NOT NULL,
  `wc_id` int(11) DEFAULT NULL,
  `ml_item_id` varchar(60) DEFAULT NULL,
  `ml_url` text DEFAULT NULL,
  `success` tinyint(1) NOT NULL DEFAULT 0,
  `error` text DEFAULT NULL,
  `ml_status` smallint(6) DEFAULT NULL COMMENT 'HTTP status de POST /items',
  `desc_status` smallint(6) DEFAULT NULL COMMENT 'HTTP status de PUT /description',
  `pics_preuploaded` tinyint(4) DEFAULT 0,
  `payload` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`payload`)),
  `ml_response` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`ml_response`)),
  `published_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `gtin_error` tinyint(1) NOT NULL DEFAULT 0 COMMENT '1 si fallo por GTIN invalido',
  PRIMARY KEY (`id`),
  KEY `idx_sku` (`sku`),
  KEY `idx_cuenta` (`cuenta`),
  KEY `idx_success` (`success`),
  KEY `idx_created` (`created_at`),
  KEY `idx_gtin_error` (`gtin_error`)
) ENGINE=InnoDB AUTO_INCREMENT=5252 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci COMMENT='Historial de publicaciones WC→ML';

CREATE TABLE `ml_estado` (
  `sku` varchar(60) NOT NULL,
  `precio_ok` tinyint(1) DEFAULT 0,
  `dims_ok` tinyint(1) DEFAULT 0,
  `attributes_ok` tinyint(1) DEFAULT 0,
  `imagenes_ok` tinyint(1) DEFAULT 0,
  `success_pct` tinyint(4) DEFAULT 0,
  `workflow` varchar(30) DEFAULT NULL,
  `falta` varchar(255) DEFAULT NULL,
  `publicado_ml` tinyint(1) DEFAULT 0,
  `ml_item_id` varchar(30) DEFAULT NULL,
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`sku`),
  KEY `idx_success_pct` (`success_pct`),
  KEY `idx_workflow` (`workflow`),
  KEY `idx_publicado` (`publicado_ml`),
  CONSTRAINT `ml_estado_ibfk_1` FOREIGN KEY (`sku`) REFERENCES `productos` (`sku`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `ml_image_edit_backlog` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `run_key` varchar(150) NOT NULL COMMENT 'cuenta:sku o sku (pre-ML)',
  `cuenta` varchar(50) DEFAULT NULL,
  `sku` varchar(100) NOT NULL,
  `wc_id` int(11) DEFAULT NULL,
  `wc_image_id` int(11) NOT NULL COMMENT 'ID original en WP Media',
  `src_url` text NOT NULL,
  `flag_quitar_fondo` tinyint(1) NOT NULL DEFAULT 0,
  `flag_traducir_texto` tinyint(1) NOT NULL DEFAULT 0,
  `flag_cambiar_modelo` tinyint(1) NOT NULL DEFAULT 0,
  `action` varchar(20) NOT NULL COMMENT 'edited|skip_no_flags|error',
  `person_desc` text DEFAULT NULL COMMENT 'respuesta describe_person (solo si cambiar_modelo=1)',
  `prompt_used` text DEFAULT NULL COMMENT 'prompt compuesto enviado a Gemini',
  `gemini_model` varchar(60) DEFAULT NULL,
  `gemini_success` tinyint(1) NOT NULL DEFAULT 0,
  `gemini_error` text DEFAULT NULL,
  `bytes_in` int(11) DEFAULT NULL,
  `bytes_out` int(11) DEFAULT NULL,
  `wp_media_id_new` int(11) DEFAULT NULL COMMENT 'ID nuevo en WP Media tras upload',
  `wp_url_new` text DEFAULT NULL,
  `ml_picture_id` varchar(60) DEFAULT NULL COMMENT 'picture_id en ML (último registrado)',
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_sku` (`sku`),
  KEY `idx_wc_id` (`wc_id`),
  KEY `idx_wc_img` (`wc_image_id`),
  KEY `idx_action` (`action`)
) ENGINE=InnoDB AUTO_INCREMENT=10541 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci COMMENT='Backlog de edición IA de imágenes por SKU';

CREATE TABLE `ml_progress` (
  `prog_key` varchar(150) NOT NULL COMMENT 'cuenta:sku',
  `cuenta` varchar(50) NOT NULL,
  `sku` varchar(100) NOT NULL,
  `wc_id` int(11) DEFAULT NULL,
  `ml_item_id` varchar(60) DEFAULT NULL,
  `ml_url` text DEFAULT NULL,
  `success` tinyint(1) NOT NULL DEFAULT 0,
  `error` text DEFAULT NULL,
  `gtin_error` tinyint(1) NOT NULL DEFAULT 0,
  `dry_run` tinyint(1) NOT NULL DEFAULT 0,
  `published_at` datetime DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`prog_key`),
  KEY `idx_prog_cuenta` (`cuenta`),
  KEY `idx_prog_success` (`success`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci COMMENT='Estado de publicaciones (reemplaza progress.json)';

CREATE TABLE `ml_tokens` (
  `cuenta` varchar(50) NOT NULL,
  `access_token` varchar(500) NOT NULL,
  `refresh_token` varchar(500) NOT NULL,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`cuenta`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci COMMENT='Tokens ML actualizados por refresh';

CREATE TABLE `ml_tokens_dashboard` (
  `cuenta` varchar(50) NOT NULL,
  `app_id` varchar(50) NOT NULL,
  `access_token` varchar(500) NOT NULL,
  `refresh_token` varchar(500) NOT NULL,
  `client_secret` varchar(500) NOT NULL,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`cuenta`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci COMMENT='Tokens ML exclusivos del dashboard';

CREATE TABLE `odoo_ranking` (
  `sku` varchar(60) NOT NULL,
  `nombre` text DEFAULT NULL,
  `stock` int(11) DEFAULT 0,
  `costo` decimal(10,2) DEFAULT NULL,
  `flete_unit` decimal(10,2) DEFAULT NULL,
  `ganancia_unit` decimal(10,2) DEFAULT NULL,
  `ganancia_total` decimal(10,2) DEFAULT NULL,
  `cbm_unit` decimal(10,6) DEFAULT NULL,
  `cbm_total` decimal(10,6) DEFAULT NULL,
  `peso_kg` decimal(8,3) DEFAULT NULL,
  `score` decimal(12,2) DEFAULT 0.00,
  `en_wc` tinyint(1) DEFAULT 0,
  `apto` tinyint(1) DEFAULT 0,
  `motivo_excluido` varchar(255) DEFAULT NULL,
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`sku`),
  KEY `idx_score` (`score`),
  KEY `idx_apto` (`apto`),
  KEY `idx_en_wc` (`en_wc`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `odoo_sync_backlog` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `ts` datetime DEFAULT current_timestamp(),
  `sku` varchar(60) DEFAULT NULL,
  `nombre` varchar(255) DEFAULT NULL,
  `accion` varchar(40) DEFAULT NULL,
  `error` varchar(255) DEFAULT NULL,
  `detalle` text DEFAULT NULL,
  `preexistente_en_wc` tinyint(1) DEFAULT 0,
  PRIMARY KEY (`id`),
  KEY `idx_sku` (`sku`),
  KEY `idx_accion` (`accion`),
  KEY `idx_ts` (`ts`),
  KEY `idx_preexistente` (`preexistente_en_wc`)
) ENGINE=InnoDB AUTO_INCREMENT=1650 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `odoo_sync_procesados` (
  `sku` varchar(60) NOT NULL,
  `synced_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`sku`),
  KEY `idx_synced_at` (`synced_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `pipeline_runs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `run_ts` datetime DEFAULT current_timestamp(),
  `tag` varchar(60) DEFAULT NULL,
  `paso` varchar(40) DEFAULT NULL,
  `total` int(11) DEFAULT 0,
  `ok` int(11) DEFAULT 0,
  `fallback` int(11) DEFAULT 0,
  `sin_datos` int(11) DEFAULT 0,
  `errores` int(11) DEFAULT 0,
  `duracion_s` int(11) DEFAULT NULL,
  `modo` varchar(30) DEFAULT NULL,
  `notas` text DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_run_ts` (`run_ts`),
  KEY `idx_tag` (`tag`),
  KEY `idx_paso` (`paso`)
) ENGINE=InnoDB AUTO_INCREMENT=201 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `productos` (
  `sku` varchar(60) NOT NULL,
  `wc_id` int(11) DEFAULT NULL,
  `odoo_id` int(11) DEFAULT NULL,
  `nombre` text DEFAULT NULL,
  `status_wc` varchar(30) DEFAULT NULL,
  `workflow` varchar(30) DEFAULT NULL,
  `tags` varchar(255) DEFAULT NULL,
  `tag_publicado` tinyint(1) DEFAULT 0,
  `categorias` varchar(255) DEFAULT NULL,
  `variaciones` tinyint(1) DEFAULT 0,
  `precio` decimal(10,2) DEFAULT NULL,
  `precio_base` decimal(10,2) DEFAULT NULL,
  `tiene_precio` tinyint(1) DEFAULT 0,
  `tiene_sale_price` tinyint(1) DEFAULT 0,
  `num_fotos` int(11) DEFAULT 0,
  `num_fotos_local` int(11) DEFAULT 0,
  `alerta_fotos` varchar(20) DEFAULT NULL,
  `peso_kg` decimal(8,3) DEFAULT NULL,
  `dims_cm` varchar(60) DEFAULT NULL,
  `stock_odoo` int(11) DEFAULT 0,
  `costo_odoo` decimal(10,4) DEFAULT NULL,
  `procesado_ts` datetime DEFAULT NULL,
  `created_at` datetime DEFAULT current_timestamp(),
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `wc_parent_id` int(11) DEFAULT NULL,
  `costo_unitario` decimal(10,4) DEFAULT NULL COMMENT 'Costo unitario MXN desde Validados_Con_SKU/unificado',
  `pieza_largo_cm` decimal(8,2) DEFAULT NULL COMMENT 'Largo pieza en cm desde Validados_Con_SKU',
  `pieza_ancho_cm` decimal(8,2) DEFAULT NULL COMMENT 'Ancho pieza en cm desde Validados_Con_SKU',
  `pieza_altura_cm` decimal(8,2) DEFAULT NULL COMMENT 'Altura pieza en cm desde Validados_Con_SKU',
  PRIMARY KEY (`sku`),
  KEY `idx_wc_id` (`wc_id`),
  KEY `idx_workflow` (`workflow`),
  KEY `idx_status` (`status_wc`),
  KEY `idx_wc_parent_id` (`wc_parent_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `scraping_alibaba` (
  `sku` varchar(60) NOT NULL,
  `url_alibaba` text DEFAULT NULL,
  `scrape_estado` varchar(20) DEFAULT 'pendiente',
  `intentos` int(11) DEFAULT 0,
  `url_rota` tinyint(1) DEFAULT 0,
  `alibaba_titulo` text DEFAULT NULL,
  `caracteristicas_clave` text DEFAULT NULL,
  `descripcion_proveedor` text DEFAULT NULL,
  `alibaba_precio_min` decimal(10,4) DEFAULT NULL,
  `alibaba_precio_max` decimal(10,4) DEFAULT NULL,
  `alibaba_moneda` varchar(10) DEFAULT NULL,
  `tipo_cambio` decimal(6,2) DEFAULT NULL,
  `min_usd_mxn` decimal(10,2) DEFAULT NULL,
  `peso_kg` decimal(8,3) DEFAULT NULL,
  `dims_cm` varchar(60) DEFAULT NULL,
  `peso_dims_ok` tinyint(1) DEFAULT 0,
  `cbm_producto` decimal(10,6) DEFAULT NULL,
  `costo_unitario_cbm` decimal(10,4) DEFAULT NULL,
  `serpapi_urls` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`serpapi_urls`)),
  `urls_intentadas` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`urls_intentadas`)),
  `imagenes_urls` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`imagenes_urls`)),
  `n_imagenes` int(11) DEFAULT 0,
  `scraped_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`sku`),
  KEY `idx_scrape_estado` (`scrape_estado`),
  KEY `idx_url_rota` (`url_rota`),
  KEY `idx_intentos` (`intentos`),
  CONSTRAINT `scraping_alibaba_ibfk_1` FOREIGN KEY (`sku`) REFERENCES `productos` (`sku`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `sync_procesados` (
  `sku` varchar(60) NOT NULL,
  `wc_id` int(11) DEFAULT NULL,
  `accion` varchar(20) DEFAULT NULL,
  `synced_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`sku`),
  KEY `idx_synced_at` (`synced_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE `webhook_eventos` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `canal` varchar(20) NOT NULL DEFAULT 'mercado_libre',
  `topic` varchar(40) DEFAULT NULL,
  `resource` varchar(200) DEFAULT NULL,
  `user_id` varchar(40) DEFAULT NULL,
  `cuenta` varchar(50) DEFAULT NULL,
  `sku` varchar(60) DEFAULT NULL,
  `procesado` tinyint(1) NOT NULL DEFAULT 0,
  `resultado` varchar(255) DEFAULT NULL,
  `recibido` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_recibido` (`recibido`)
) ENGINE=InnoDB AUTO_INCREMENT=1425 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

