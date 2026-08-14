-- ═══════════════════════════════════════════════════════════════════════════
-- 0020 — Las tres cachés del tab de MÁRGENES salen de MySQL.
--
-- Estado: NO APLICADA. Sandbox primero (aplicar_migraciones.py), producción
-- solo con el visto de Eduardo.
--
-- QUÉ MIGRA
-- ---------
-- `ml_envio_real` (13,735 filas) · `ml_ficha` (971) · `ml_visitas` (1,485),
-- las tres en el MySQL `u531713409_kubera_ml` que se va a retirar. Son el
-- GRUPO 5 del plan (docs/PLAN_31_TABLAS.md) y se eligieron como PRIMERAS a
-- propósito: cada una tiene exactamente UN lector y UN escritor, los dos en su
-- propio servicio, sin crons detrás. Es el caso más limpio que queda.
--
-- El producto de este paso no son las tres tablas: es el INSTRUCTIVO que se va
-- a usar para las otras 28, incluido el grupo del publicador (~19 lectores).
--
-- POR QUÉ EN `enrich` Y NO EN `channel`
-- --------------------------------------
-- `channel.*` es el estado del canal tal como el canal lo reporta (listings,
-- orders). Estas tres son datos que se le PIDEN a la API de ML bajo demanda y
-- se cachean con su propio TTL — el mismo carácter que `enrich.product_media`
-- o `enrich.market_listing_metrics`. Si vivieran en `channel` alguien las
-- leería creyéndolas parte del registro, y son un caché con fecha de consulta.
--
-- `consultado_at` NO es decorativo: es lo que decide si se vuelve a llamar a
-- ML. ML acepta UN ítem por llamada en visitas y en costos de envío, así que
-- ese campo es lo único que evita que la página cueste cientos de llamadas.
--
-- ⚠️ DEUDA CONOCIDA QUE ESTA MIGRACIÓN **NO** RESUELVE
-- ----------------------------------------------------
-- `enrich.market_listing_metrics.visits_30d` (módulo Competencia) y
-- `listing_visits` (aquí, módulo Márgenes) guardan LO MISMO pedido al MISMO
-- endpoint de ML. Medido el 14-ago: 323 publicaciones están en las dos, y 257
-- traen números distintos — pero la diferencia es esperable, no un error: son
-- ventanas MÓVILES de 30 días consultadas en fechas distintas.
--
-- Lo que sí es real es el desperdicio: ML no acepta multiget en visitas
-- (`/visits/items` con dos ids responde HTTP 400), así que cada publicación
-- duplicada cuesta una llamada de más, en los dos módulos.
--
-- Se migra 1:1 A PROPÓSITO. Converger los dos cachés cambia la semántica del
-- tab de Márgenes (ventana variable de 7/30/60 días contra la fija de 30) y eso
-- es una decisión de producto, no de migración. Queda anotado para después; lo
-- que NO se vale es que la próxima persona cree un tercer caché sin saber que
-- ya hay dos.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Costo REAL del envío, por pedido ───────────────────────────────────────
-- Lo que ML le cobró al VENDEDOR por ese embarque, ya con descuentos
-- (`GET /shipments/{id}/costs`). Existe porque el estimado
-- (`costing.costos_finales.costo_fee_envio`) miente en las dos direcciones: el
-- peso del packing list mezcla unidades (pieza / caja master / total del
-- renglón), y por eso a Malla Sombra le inventaba $200k de pérdida y a 141 SKUs
-- con venta les puso el flete en $0.
create table if not exists enrich.order_shipping_cost (
    cuenta              text        not null,
    external_order_id   text        not null,
    shipment_id         text,
    -- Puede ser NULL con `consultado_at` puesto: significa "ya le pregunté a ML
    -- y no tiene costo" (FULL sin cargo, cancelado). Distinto de "no he
    -- preguntado", que es la ausencia de fila. Esa diferencia es la que evita
    -- re-consultar en cada carga de la página.
    costo_vendedor      numeric(10,2),
    consultado_at       timestamptz not null default now(),
    primary key (cuenta, external_order_id)
);

comment on table enrich.order_shipping_cost is
  'Costo real de envío que ML cobró al vendedor, por pedido. Caché de '
  '/shipments/{id}/costs con TTL propio. Migrada de MySQL ml_envio_real '
  '(13,735 filas) el 14-ago-2026.';

-- ── Peso que la bodega de ML MIDIÓ, por publicación ────────────────────────
-- `medido` distingue el peso REAL de la báscula de ML del declarado por
-- nosotros. Su valor no es el flete: es detectar SKUs reciclados — si el mismo
-- SKU pesa 40 g en una cuenta y 60 g en la otra, no es el mismo objeto, y está
-- compartiendo costo, inventario y margen con otro producto.
create table if not exists enrich.listing_weight (
    listing_id      text        not null primary key,
    cuenta          text,
    titulo          text,
    peso_g          numeric(10,2),
    medido          boolean     not null default false,
    consultado_at   timestamptz not null default now()
);

comment on table enrich.listing_weight is
  'Peso medido por la bodega de ML por publicación. `medido`=false es peso '
  'declarado, no pesado. Migrada de MySQL ml_ficha (971 filas) el 14-ago-2026.';

-- ── Visitas por publicación y ventana ──────────────────────────────────────
-- La llave lleva `dias` porque el tab pide 7, 30 y 60 días y son mediciones
-- distintas, no derivables entre sí (ML devuelve el total de SU ventana).
-- `dias_datos` es cuántos días de datos trajo ML de verdad: una publicación
-- creada hace 3 días no tiene 30, y sin este campo su conversión se calcularía
-- contra un período que no existió.
create table if not exists enrich.listing_visits (
    listing_id      text        not null,
    dias            smallint    not null,
    cuenta          text,
    visitas         integer,
    dias_datos      smallint,
    consultado_at   timestamptz not null default now(),
    primary key (listing_id, dias)
);

comment on table enrich.listing_visits is
  'Visitas por publicación y ventana (7/30/60 días) para el tab de Márgenes. '
  'OJO: enrich.market_listing_metrics.visits_30d guarda lo mismo para el módulo '
  'de Competencia, pedido al MISMO endpoint. No crear un tercero — ver la nota '
  'de deuda en el encabezado de esta migración. Migrada de MySQL ml_visitas '
  '(1,485 filas) el 14-ago-2026.';

-- Índice para el barrido "¿qué está por vencer?" que hace el completador.
create index if not exists ix_listing_visits_consultado
    on enrich.listing_visits (consultado_at);
create index if not exists ix_order_shipping_cost_consultado
    on enrich.order_shipping_cost (consultado_at);
