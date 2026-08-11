-- DDL Walmart México — kubera_ml (MySQL/MariaDB, Hostinger)
-- Creado 2026-08-10. Sigue el patrón amazon_backlog + amazon_progress.
--
-- Se aplica a mano contra u531713409_kubera_ml. NO forma parte de
-- supabase/migrations/ (eso es Postgres) ni de ESQUEMA_kubera_ml.sql
-- (eso es un dump generado — regenerarlo después de aplicar esto).
--
-- Por qué DOS tablas: es el par que ya usan ML y Amazon.
--   walmart_backlog  = historial, 1 fila POR INTENTO (nunca se actualiza)
--   walmart_progress = estado actual, 1 fila POR SKU (se sobrescribe)
--
-- Sin FOREIGN KEY a `productos` a propósito: amazon_backlog tampoco la tiene,
-- y una FK haría fallar el registro de SKUs que aún no están en el maestro
-- (mismo problema que hoy bloquea el backfill de ops.channel_submissions).


-- ============================================================
-- 1. Historial de envíos
-- ============================================================
CREATE TABLE IF NOT EXISTS `walmart_backlog` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `sku` varchar(100) NOT NULL,
  `wc_id` int(11) DEFAULT NULL,
  `cuenta` varchar(50) NOT NULL DEFAULT 'WALMART',

  -- Walmart MX manda por FEEDS, no por endpoints REST por recurso.
  -- OJO: /v3/price NO EXISTE en México (404) — verificado en
  -- docs/WALMART_MX_HALLAZGOS.md. El precio viaja dentro del feed de artículo.
  -- Sin esta columna no se puede saber si faltó el de inventario
  -- (causa de los artículos publicados con Inventario 0).
  `feed_type` varchar(24) NOT NULL DEFAULT 'MP_ITEM_INTL'
      COMMENT 'MP_ITEM_INTL | MP_INVENTORY | MP_MAINTENANCE | MP_ITEM_MATCH',
  `feed_id` varchar(60) DEFAULT NULL
      COMMENT 'feedId devuelto por POST /v3/feeds; con él se consulta el resultado',
  `sub_category` varchar(64) DEFAULT NULL
      COMMENT 'id de API, ej. electronics_accessories. UNO por feed',

  -- Identificadores que Walmart asigna al procesar
  `wpid` varchar(40) DEFAULT NULL COMMENT 'Walmart Product ID',
  `item_id` varchar(40) DEFAULT NULL,
  `gtin` varchar(20) DEFAULT NULL
      COMMENT 'GTIN generado por Walmart. Obligatorio guardarlo: "CUSTOM" solo sirve para el ALTA, para MODIFICAR hay que mandar este valor',

  `status` varchar(20) DEFAULT NULL
      COMMENT 'SUBMITTED | INPROGRESS | PROCESSED | ERROR',
  `success` tinyint(1) NOT NULL DEFAULT 0,
  `issue_count` int(11) DEFAULT 0,
  `error_resumen` varchar(255) DEFAULT NULL
      COMMENT 'Primer error legible; lo único que se muestra en listados',

  `issues` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL
      COMMENT 'Lista completa de issues devueltos' CHECK (json_valid(`issues`)),
  `payload` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL
      COMMENT 'JSON enviado a Walmart' CHECK (json_valid(`payload`)),
  `wm_response` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL
      COMMENT 'Respuesta completa de Walmart' CHECK (json_valid(`wm_response`)),

  `submitted_at` datetime NOT NULL DEFAULT current_timestamp(),
  `published_at` datetime DEFAULT NULL,

  PRIMARY KEY (`id`),
  KEY `idx_sku` (`sku`),
  KEY `idx_status` (`status`),
  KEY `idx_success` (`success`),
  KEY `idx_feed` (`feed_id`),
  KEY `idx_feed_type` (`feed_type`),
  KEY `idx_submitted` (`submitted_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci
  COMMENT='Historico de envios a Walmart MX — 1 fila por intento';


-- ============================================================
-- 2. Estado actual por SKU
-- ============================================================
CREATE TABLE IF NOT EXISTS `walmart_progress` (
  `sku` varchar(100) NOT NULL,
  `wc_id` int(11) DEFAULT NULL,
  `cuenta` varchar(50) NOT NULL DEFAULT 'WALMART',
  `sub_category` varchar(64) DEFAULT NULL,

  `wpid` varchar(40) DEFAULT NULL,
  `item_id` varchar(40) DEFAULT NULL,
  `gtin` varchar(20) DEFAULT NULL
      COMMENT 'El GTIN vigente del SKU; se lee de aqui para cualquier actualizacion',
  `feed_id` varchar(60) DEFAULT NULL COMMENT 'ultimo feed de tipo item',

  `status` varchar(20) DEFAULT NULL,
  `success` tinyint(1) NOT NULL DEFAULT 0,
  `error_label` varchar(200) DEFAULT NULL,
  `issue_count` int(11) DEFAULT 0,

  -- Los tres feeds se rastrean por separado: un SKU puede tener el articulo
  -- publicado y aun asi no ser comprable si nunca se le mando inventario.
  `precio_enviado` decimal(12,2) DEFAULT NULL,
  `precio_at` datetime DEFAULT NULL,
  `stock_enviado` int(11) DEFAULT NULL,
  `stock_at` datetime DEFAULT NULL,

  `last_submitted` datetime DEFAULT NULL,
  `published_at` datetime DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),

  PRIMARY KEY (`sku`),
  KEY `idx_status` (`status`),
  KEY `idx_success` (`success`),
  KEY `idx_subcat` (`sub_category`),
  KEY `idx_stock_at` (`stock_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci
  COMMENT='Estado actual de publicaciones Walmart MX — 1 fila por SKU';


-- ============================================================
-- Verificación tras aplicar
-- ============================================================
-- SHOW CREATE TABLE walmart_backlog\G
-- SHOW CREATE TABLE walmart_progress\G
--
-- SKUs publicados que NUNCA recibieron inventario (el hueco de hoy):
--   SELECT sku, published_at FROM walmart_progress
--    WHERE success = 1 AND stock_at IS NULL;
