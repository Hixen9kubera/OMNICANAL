-- ═══════════════════════════════════════════════════════════════════════════
-- 0058 — OPS: el CHECKLIST de almacén
--        (ops.checklist_lote + ops.checklist_almacen + ops.checklist_matriz)
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Estado: NO APLICADA en producción. Sandbox primero; producción solo con el sí
-- de Eduardo (doble candado). Mientras no exista, la pestaña Inventario ·
-- Checklist lo DICE en pantalla («falta la migración 0058») en vez de tronar, y
-- el Catálogo Maestro sigue mostrando «Bodega —» en el cotejo de cajas.
--
-- Brandon, 24-sep-2026: una pestaña CHECKLIST dentro de Inventario, de
-- almacén, que determine si cada SKU tiene los atributos que Mercado Libre
-- exige y que además pida DIMENSIONES, CAJAS y PIEZAS. Cada semana se eligen
-- ~100 SKUs a procesar; se descarga un Excel con lo que falta, almacén lo
-- llena y se vuelve a cargar. De los atributos OPCIONALES se puede elegir
-- cuáles se vuelven obligatorios: esa es la MATRIZ.
--
-- DÓNDE VA CADA COSA — y por qué hacen falta tablas nuevas
--   · Los ATRIBUTOS de ML NO van aquí: van a `enrich.channel_content`, el mismo
--     sitio donde escribe el Publicador (vía services/specs_editor.py). Una
--     segunda copia sería una segunda verdad.
--   · Las MEDIDAS, CAJAS y PIEZAS no tienen casa en ningún sistema. Las
--     dimensiones de Woo son el CBM reconstruido (L×A×H = _kubera_cbm en el
--     99.7% medido), no medidas; y la caja que contó almacén no existe en
--     Odoo, Woo ni kubera — `inventario_maestro._cotejo_cajas` la pinta en
--     NULL desde el 8-sep por eso. Esta es la primera vez que alguien la mide.
--   · El LOTE de la semana y la MATRIZ son decisiones del equipo, no datos de
--     ningún canal.
--
-- UN SOLO ESCRITOR: `services/checklist.py`.
--
-- Idempotente: `create table if not exists` con las constraints dentro,
-- índices `if not exists`; `comment`/`grant`/`enable rls` se repiten sin daño.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- ───────────────────────────────────────────────────────────────────────────
-- 1) ops.checklist_lote — qué SKUs se procesan cada semana
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists ops.checklist_lote (
    semana       date         not null,              -- el LUNES de la semana
    sku          text         not null,
    agregado_por text,
    agregado_en  timestamptz  not null default now(),
    constraint checklist_lote_pkey primary key (semana, sku),
    constraint checklist_lote_lunes_chk check (extract(isodow from semana) = 1),
    constraint checklist_lote_sku_chk check (length(btrim(sku)) > 0)
);

create index if not exists checklist_lote_sku_ix on ops.checklist_lote (sku);

comment on table ops.checklist_lote is
  'Los SKUs que almacén procesa cada semana en Inventario · Checklist (~100 por '
  'semana, Brandon 24-sep). Una fila por (semana, sku); semana = el lunes. Un '
  'SKU puede repetirse en otra semana si no se terminó. Escritor único: '
  'services/checklist.py.';

-- ───────────────────────────────────────────────────────────────────────────
-- 2) ops.checklist_almacen — lo que almacén MIDE y CUENTA de cada SKU
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists ops.checklist_almacen (
    sku              text          primary key,
    largo_cm         numeric(8,2),
    ancho_cm         numeric(8,2),
    alto_cm          numeric(8,2),
    peso_kg          numeric(9,3),
    cajas            integer,
    piezas_por_caja  integer,
    fuente           text          not null default 'panel',
    capturado_por    text,
    capturado_en     timestamptz   not null default now(),
    constraint checklist_almacen_medidas_chk
        check (coalesce(largo_cm, 1) > 0 and coalesce(ancho_cm, 1) > 0
               and coalesce(alto_cm, 1) > 0 and coalesce(peso_kg, 1) > 0),
    constraint checklist_almacen_cajas_chk
        check (coalesce(cajas, 0) >= 0 and coalesce(piezas_por_caja, 1) > 0),
    constraint checklist_almacen_fuente_chk
        check (fuente in ('panel', 'excel', 'csv', 'mcp'))
);

comment on table ops.checklist_almacen is
  'Lo que almacén mide y cuenta de cada SKU: dimensiones del producto EMPACADO '
  '(la unidad que se envía) y cuántas cajas hay con cuántas piezas cada una. '
  'NO son las dimensiones de Woo (CBM reconstruido). Alimenta el lado «Bodega» '
  'del cotejo de cajas del Catálogo Maestro. Escritor único: services/checklist.py.';
comment on column ops.checklist_almacen.cajas is
  'Cajas máster que almacén tiene de este SKU. Es el dato que MANDA sobre el '
  'packing list en el cotejo (Brandon, 8-sep).';
comment on column ops.checklist_almacen.fuente is
  'Por dónde llegó la última captura: panel | excel | csv | mcp.';

-- ───────────────────────────────────────────────────────────────────────────
-- 3) ops.checklist_matriz — qué opcionales se vuelven obligatorios
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists ops.checklist_matriz (
    canal           text         not null default 'mercado_libre',
    categoria_id    text         not null,
    campo           text         not null,
    obligatorio     boolean      not null,
    actualizado_por text,
    actualizado_en  timestamptz  not null default now(),
    constraint checklist_matriz_pkey primary key (canal, categoria_id, campo)
);

comment on table ops.checklist_matriz is
  'La MATRIZ del checklist: por (canal, categoría), los atributos OPCIONALES del '
  'canal que el equipo decidió exigir. Los obligatorios del propio canal NO se '
  'guardan aquí (se leen en vivo de su API) y no se pueden bajar a opcionales: '
  'el canal los rechazaría igual. obligatorio=false = se quitó una promoción.';

-- ───────────────────────────────────────────────────────────────────────────
-- 4) El candado, donde nace el objeto (0016, 0049, 0051, 0053). RLS activa y
--    0 políticas = solo pasa quien hace bypass (service_role y postgres).
-- ───────────────────────────────────────────────────────────────────────────
alter table ops.checklist_lote enable row level security;
alter table ops.checklist_almacen enable row level security;
alter table ops.checklist_matriz enable row level security;
grant all on ops.checklist_lote to service_role;
grant all on ops.checklist_almacen to service_role;
grant all on ops.checklist_matriz to service_role;

commit;
