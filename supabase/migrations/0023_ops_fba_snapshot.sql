-- ═══════════════════════════════════════════════════════════════════════════
-- 0023 — El reporte de inventario FBA de Seller Central tiene dónde vivir.
--
-- Estado: NO APLICADA. Sandbox primero (aplicar_migraciones.py), producción
-- solo con el visto de Eduardo.
--
-- QUÉ GUARDA
-- ----------
-- El export "Manage FBA Inventory" de Seller Central (el CSV que Amazon
-- entrega por SKU), subido a mano desde la pestaña /analisis/fba. Una fila por
-- SKU; cada subida REEMPLAZA la foto completa — es un snapshot, no un
-- historial.
--
-- POR QUÉ UNA TABLA Y NO EL SYNC
-- ------------------------------
-- El sync de inventario (scheduler._job → fba/inventory/v1/summaries) solo ve
-- lo que está en `channel.listings`, y su foto de FBA está corta: al 18-ago
-- veía 20 SKUs con 1,299 unidades, mientras el reporte de Seller Central trae
-- 101 SKUs con 2,224 en bodega MÁS 3,426 EN CAMINO (inbound), que la API de
-- summaries que usamos ni siquiera reporta por separado. El reporte además
-- trae ASIN (que no guardamos en ningún lado — era el bloqueo #1 de la
-- pestaña) y el volumen por unidad MEDIDO POR AMAZON, que sirve de segunda
-- báscula contra nuestras dimensiones del costeo (mismo papel que el peso de
-- ML en `peso_divergente`).
--
-- POR QUÉ EN `ops`
-- ----------------
-- Es una foto operativa subida a mano, del mismo carácter que
-- `ops.stock_watch_photo` (0021): describe el estado de un almacén en un
-- momento, no es un caché con TTL (enrich) ni el estado que reporta el canal
-- en vivo (channel).
--
-- `per_unit_volume` viene en cm³: verificado contra las dimensiones del
-- costeo en SKUs conocidos (ACC-0001-AZL: 502.8 vs 435 cm³ calculados; la
-- diferencia constante ~10-15%% es el empaque, Amazon mide la unidad
-- empaquetada).
-- ═══════════════════════════════════════════════════════════════════════════

create table if not exists ops.fba_snapshot (
    sku                 citext        not null primary key,
    fnsku               text,
    asin                text,
    product_name        text,
    price               numeric(12,2),
    -- unidades en la bodega de Amazon
    fulfillable         integer       not null default 0,
    reserved            integer       not null default 0,
    unsellable          integer       not null default 0,
    warehouse           integer       not null default 0,
    -- unidades EN CAMINO a la bodega (el dato que el sync no ve)
    inbound_working     integer       not null default 0,
    inbound_shipped     integer       not null default 0,
    inbound_receiving   integer       not null default 0,
    -- volumen por unidad medido por Amazon, en cm³
    per_unit_volume     numeric(12,2),
    -- de qué subida viene esta fila
    report_name         text,
    subido_at           timestamptz   not null default now()
);

comment on table ops.fba_snapshot is
    'Foto del reporte "Manage FBA Inventory" de Seller Central, subida a mano '
    'desde /analisis/fba. Cada subida reemplaza la foto completa.';
