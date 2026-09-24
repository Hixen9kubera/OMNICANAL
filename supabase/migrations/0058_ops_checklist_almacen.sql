-- ═══════════════════════════════════════════════════════════════════════════
-- 0058 — CHECKLIST de almacén: el lote semanal + lo que almacén mide y cuenta
--        (ops.checklist_lote + 8 columnas almacen_* en core.products)
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Estado: NO APLICADA en producción. Sandbox primero; producción solo con el sí
-- de Eduardo (doble candado). Mientras no exista, Inventario → Checklist lo
-- DICE en pantalla («falta la migración 0058») y el Catálogo Maestro sigue igual.
--
-- REESCRITA el 24-sep (v0.565.0) a pedido de Brandon: «las dimensiones y el
-- número de cajas debes de buscarlo actualmente en la base de datos para no
-- repetir y normalizar». La primera versión (v0.560.0) creaba DOS tablas que ya
-- no existen aquí:
--   · ops.checklist_matriz  → la matriz vive en channel.field_requirements con
--     fuente = 'manual' (el CHECK ya lo admite; hay 3,331 filas así de Walmart).
--   · ops.checklist_almacen → sus datos van a core.products (abajo).
-- Si alguien aplicó la versión vieja en el sandbox, el paso 0 las quita.
--
-- DÓNDE VA CADA COSA, Y POR QUÉ NO SE REUSAN LAS COLUMNAS DE costos_validados
--   · ATRIBUTOS de ML → enrich.channel_content (canal mercado_libre, cuenta '',
--     contenido->'atributos'), el MISMO sitio que el Publicador. Sin DDL.
--   · MEDIDAS del producto empacado → core.products.almacen_*. NO van en
--     costing.costos_validados.largo/ancho/alto/peso porque esas NO son
--     medidas: son el volumen de flete reconstruido del CBM (medido el 24-sep:
--     de 15,452 filas solo el 26% cuadra con el flete guardado y 5,854 tienen
--     densidad imposible; ACC-0313-NEG dice 60×51×51 cm y 0.281 kg, que es la
--     caja máster). Y costos.py las USA: con auto_cbm (la pestaña Costos lo
--     manda encendido) el flete se recalcula de largo×ancho×alto, y la comisión
--     de envío de ML sale de ahí. Escribir la medida real en esas columnas
--     cambiaría costos y precios en el siguiente recálculo. Decisión de Brandon
--     (24-sep): columnas propias; que costos use la medida real es otra decisión.
--   · CAJAS y PIEZAS POR CAJA que cuenta almacén → core.products.almacen_*. NO
--     en costos_validados.cajas/piezas_por_caja: esas las escribe el Resolver
--     desde el PACKING LIST, y son la otra mitad de la comparación que Brandon
--     pidió el 8-sep (packing list vs almacén, «tiene más importancia lo que nos
--     da almacén»). Pisarlas borraría la comparación, y el Resolver pisaría a su
--     vez el conteo de almacén en la siguiente validación.
--   · ¿Por qué core.products y no costos_validados? Porque Costos y Fulfillment
--     deciden «sin costo» por la EXISTENCIA de la fila (`v.sku is null`,
--     costing_read.py:141): crearle fila a un SKU sin costo para guardar sus
--     medidas lo sacaría de la lista de trabajo de los KAM. En la Week 39, 46 de
--     100 SKUs no tienen fila de costo. core.products tiene fila para todos
--     (22,416), y medidas y piezas por caja son datos del PRODUCTO.
--
-- UN SOLO ESCRITOR de lo nuevo: services/checklist.py. Los demás escritores de
-- core.products (ETL de las 06:15, webhook de Woo, espejos) nombran sus
-- columnas y no tocan almacen_*.
--
-- Idempotente: `if not exists` en tabla, columnas e índices; comment/grant/rls
-- se repiten sin daño.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- ───────────────────────────────────────────────────────────────────────────
-- 0) La versión vieja de esta misma migración (v0.560.0). Nunca llegó a
--    producción; si se aplicó en el sandbox, esto la limpia. Sin datos que
--    perder: la pestaña no podía escribir sin ellas.
-- ───────────────────────────────────────────────────────────────────────────
drop table if exists ops.checklist_almacen;
drop table if exists ops.checklist_matriz;

-- ───────────────────────────────────────────────────────────────────────────
-- 1) ops.checklist_lote — qué SKUs se procesan cada semana
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists ops.checklist_lote (
    semana       date         not null,              -- el LUNES de la semana ISO («Week 39»)
    -- Sin ON DELETE CASCADE (Eduardo, 24-sep): la misma regla que las otras 14
    -- llaves hacia core.products. Borrar un producto que está en un lote falla
    -- en vez de llevarse el lote en silencio.
    sku          citext       not null references core.products(sku),
    comentario   text,                               -- la columna «Comentarios» de la lista semanal
    agregado_por text,
    agregado_en  timestamptz  not null default now(),
    constraint checklist_lote_pkey primary key (semana, sku),
    constraint checklist_lote_lunes_chk check (extract(isodow from semana) = 1)
);

create index if not exists checklist_lote_sku_ix on ops.checklist_lote (sku);

comment on table ops.checklist_lote is
  'Los SKUs que almacén procesa cada semana en Inventario → Checklist (~100 por '
  'semana, Brandon 24-sep). Una fila por (semana, sku); semana = el lunes de la '
  'semana ISO, la que el equipo llama «Week 39». Un SKU puede repetirse en otra '
  'semana si no se terminó. Escritor único: services/checklist.py.';

-- ───────────────────────────────────────────────────────────────────────────
-- 2) core.products — lo que almacén MIDE y CUENTA de cada SKU
-- ───────────────────────────────────────────────────────────────────────────
alter table core.products
    add column if not exists almacen_largo_cm         numeric(8,2),
    add column if not exists almacen_ancho_cm         numeric(8,2),
    add column if not exists almacen_alto_cm          numeric(8,2),
    add column if not exists almacen_peso_kg          numeric(9,3),
    add column if not exists almacen_cajas            integer,
    add column if not exists almacen_piezas_por_caja  integer,
    add column if not exists almacen_por              text,
    add column if not exists almacen_en               timestamptz;

-- Las guardas van en bloques con `if not exists` a mano: `add constraint` no
-- tiene esa forma y la migración tiene que poder correr dos veces.
do $$
begin
  if not exists (select 1 from pg_constraint
                  where conname = 'products_almacen_medidas_chk') then
    alter table core.products add constraint products_almacen_medidas_chk
      check (coalesce(almacen_largo_cm, 1) > 0 and coalesce(almacen_ancho_cm, 1) > 0
             and coalesce(almacen_alto_cm, 1) > 0 and coalesce(almacen_peso_kg, 1) > 0);
  end if;
  if not exists (select 1 from pg_constraint
                  where conname = 'products_almacen_cajas_chk') then
    alter table core.products add constraint products_almacen_cajas_chk
      check (coalesce(almacen_cajas, 0) >= 0
             and coalesce(almacen_piezas_por_caja, 1) > 0);
  end if;
end $$;

comment on column core.products.almacen_largo_cm is
  'Largo del producto EMPACADO (la unidad que se envía), MEDIDO por almacén en '
  'el Checklist. No confundir con costing.costos_validados.largo: ese es el '
  'volumen de flete reconstruido del CBM y lo usa costos.py.';
comment on column core.products.almacen_ancho_cm is
  'Ancho del producto empacado, medido por almacén (ver almacen_largo_cm).';
comment on column core.products.almacen_alto_cm is
  'Alto del producto empacado, medido por almacén (ver almacen_largo_cm).';
comment on column core.products.almacen_peso_kg is
  'Peso del producto empacado, pesado por almacén (ver almacen_largo_cm).';
comment on column core.products.almacen_cajas is
  'Cajas máster que CONTÓ almacén. La otra mitad del cotejo del Catálogo '
  'Maestro: costos_validados.cajas es lo que dice el PACKING LIST. Manda almacén '
  '(Brandon, 8-sep).';
comment on column core.products.almacen_piezas_por_caja is
  'Piezas por caja máster según almacén; costos_validados.piezas_por_caja es la '
  'del packing list.';
comment on column core.products.almacen_por is
  'Quién hizo la última captura de almacén (el usuario del panel).';
comment on column core.products.almacen_en is
  'Cuándo fue la última captura de almacén. NULL = nadie ha medido este SKU.';

-- ───────────────────────────────────────────────────────────────────────────
-- 3) El candado, donde nace el objeto (0016, 0049, 0051, 0053). RLS activa y
--    0 políticas = solo pasa quien hace bypass (service_role y postgres).
--    core.products ya lo tiene; aquí solo la tabla nueva.
-- ───────────────────────────────────────────────────────────────────────────
alter table ops.checklist_lote enable row level security;
grant all on ops.checklist_lote to service_role;

commit;
