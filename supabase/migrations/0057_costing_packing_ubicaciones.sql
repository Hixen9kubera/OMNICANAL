-- ═══════════════════════════════════════════════════════════════════════════
-- 0057 — costing.packing_ubicaciones: en qué renglón del packing list ORIGINAL
--        está cada SKU, según Ferraforme. 23-sep-2026.
--
-- Ferraforme (carpeta de Drive de la homologación) es la copia homologada de cada
-- packing list: el mismo archivo del proveedor con la columna "SKU ODDO"
-- agregada al frente y las filas en su lugar. Decisión de Eduardo (23-sep): es
-- SOLO una referencia para UBICAR el SKU; los números salen del original.
--
-- Hasta hoy el validador de costos encontraba el renglón comparando la foto de
-- Odoo contra las del packing list y, si no alcanzaba, preguntándole a la IA.
-- Con esta tabla lo consulta primero: un SELECT en vez de fotos y modelo.
--
-- Una fila por renglón con SKU de la versión VIGENTE de cada Ferraforme. La
-- llena scripts/indexar_ferraforme.py, que REEMPLAZA las filas de cada archivo.
-- `alineado` solo es true si se PROBÓ a qué fila del original corresponde
-- (`cotejo`): 'fila' = la misma fila dice lo mismo, y así cuadra ≥90% del
-- archivo; 'texto' = lo que dice el renglón aparece UNA sola vez en el
-- original (Alma a veces parte renglones por talla y el orden se corre). Y el
-- backend además exige que la huella del original que abre (`original_sha256`)
-- sea la cotejada; si el original cambió de versión, la fila no se usa.
--
-- RLS + grant en la misma migración (patrón de la casa). Sin políticas: solo
-- service_role.
-- ═══════════════════════════════════════════════════════════════════════════

create table if not exists costing.packing_ubicaciones (
  id                 bigint      generated always as identity primary key,
  sku                citext      not null,
  -- 'columna': la columna de SKU de Ferraforme. 'corchete': "[SKU] nombre" de
  -- Odoo pegado en otra celda (el contenedor 80 trae ahí el SKU corregido).
  fuente_sku         text        not null default 'columna'
                                 check (fuente_sku in ('columna', 'corchete')),
  contenedor_base    text,
  ferraforme_file_id text        not null,
  ferraforme_miembro text,
  ferraforme_sha256  text        not null check (ferraforme_sha256 ~ '^[0-9a-f]{64}$'),
  ferraforme_fila    integer     not null check (ferraforme_fila > 0),
  original_file_id   text,
  original_sha256    text        check (original_sha256 ~ '^[0-9a-f]{64}$'),
  original_fila      integer     check (original_fila > 0),
  alineado           boolean     not null,
  cotejo             text        check (cotejo in ('fila', 'texto')),
  codigo_proveedor   text,
  texto              text,
  indexado_at        timestamptz not null default now(),
  -- Un renglón puede dar dos SKUs: el de la columna y el de los corchetes.
  unique (ferraforme_sha256, ferraforme_fila, sku),
  -- Alineado exige saber contra qué versión, qué fila y cómo se probó.
  check (not alineado or (original_sha256 is not null and original_fila is not null
                          and cotejo is not null))
);

create index if not exists packing_ubicaciones_sku_idx
  on costing.packing_ubicaciones (sku) where alineado;
create index if not exists packing_ubicaciones_ferraforme_idx
  on costing.packing_ubicaciones (ferraforme_file_id);

alter table costing.packing_ubicaciones enable row level security;
grant all on costing.packing_ubicaciones to service_role;

comment on table costing.packing_ubicaciones is
  'Según Ferraforme, en qué renglón del packing list original está cada SKU. '
  'Solo referencia para ubicar; los números salen del original. La llena scripts/indexar_ferraforme.py.';
comment on column costing.packing_ubicaciones.alineado is
  'Se probó a qué fila del original corresponde (ver cotejo). Solo estas filas se usan.';
comment on column costing.packing_ubicaciones.cotejo is
  'fila: la misma fila dice lo mismo y el archivo cuadra fila por fila (>=90%). texto: lo que dice el renglón aparece una sola vez en el original.';
comment on column costing.packing_ubicaciones.original_sha256 is
  'Versión del original contra la que se cotejó. El backend no usa la fila si abre otra versión.';
comment on column costing.packing_ubicaciones.codigo_proveedor is
  'La columna "SKU" de Ferraforme: el código del proveedor (REBI57779), no el SKU de Kubera.';
