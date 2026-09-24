-- ═══════════════════════════════════════════════════════════════════════════
-- 0055 — PACKING LISTS EN SUPABASE: bucket privado `packing-lists` +
--        índice de versiones `costing.packing_archivos`. 23-sep-2026.
--
-- POR QUÉ
-- Los packing lists vivían solo en Google Drive y el backend los leía por el
-- enlace público: la carpeta se listaba raspando su HTML (formato no
-- documentado) y cada archivo se bajaba a /tmp del contenedor, que se borra en
-- cada deploy. Tres problemas medidos el 23-sep:
--   · la carpeta se listaba y los archivos se bajaban por enlace público, sin
--     credenciales: nada garantizaba que el archivo de hoy fuera el de ayer;
--   · los archivos se siguen editando después de creados (51 de 193), así que
--     el mismo costo podía salir de versiones distintas según cuándo arrancó el
--     contenedor;
--   · `costing.caja_compartida` guarda el NOMBRE del archivo + número de fila:
--     renombrarlo rompe el vínculo, e insertar una fila lo mueve sin aviso.
--
-- QUÉ GUARDA
-- Una fila por VERSIÓN de archivo. El objeto se nombra por su sha256
-- (`<tipo>/<sha256>.<ext>`), así que una versión nunca pisa a otra y la misma
-- huella siempre es el mismo contenido. Dos tipos:
--   · 'original'   — el packing list del proveedor: de aquí sale el COSTO.
--   · 'ferraforme' — la copia homologada (columnas de SKU agregadas al frente,
--                    filas 1:1 con el original). SOLO sirve para ubicar el SKU
--                    en su packing list; sus números no son fuente de nada.
--
-- CANDADOS
-- · Bucket privado y SIN políticas en storage.objects: anon y authenticated no
--   ven nada; solo el backend con service_role (que hace bypass). OJO al
--   agregar políticas: una política "para autenticados" le abriría los
--   precios de proveedor a cualquier sesión. verificar_rls.py NO revisa
--   storage.objects.
-- · Storage NO entra en el respaldo diario y no versiona: un borrado es
--   definitivo. Nadie sube con upsert; las versiones se agregan, no se pisan.
-- · RLS + grant en la misma migración que crea la tabla (patrón de la casa).
-- ═══════════════════════════════════════════════════════════════════════════

-- 1 · Bucket ------------------------------------------------------------------
-- 150 MB por archivo: el mayor medido pesa 119.6 MB. El límite GLOBAL del
-- proyecto manda sobre este (Storage → Settings); si es menor, los archivos
-- grandes se rechazan al subir aunque el bucket diga 150.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('packing-lists', 'packing-lists', false, 157286400,
        array['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
              'application/vnd.ms-excel',
              'application/zip'])
on conflict (id) do update
   set public             = false,
       file_size_limit    = excluded.file_size_limit,
       allowed_mime_types = excluded.allowed_mime_types;

-- 2 · Índice de versiones ------------------------------------------------------
create table if not exists costing.packing_archivos (
  id                bigint generated always as identity primary key,
  tipo              text        not null check (tipo in ('original', 'ferraforme')),
  sha256            text        not null check (sha256 ~ '^[0-9a-f]{64}$'),
  ruta              text        not null,
  bytes             bigint      not null check (bytes > 0),
  nombre            text        not null,
  miembro           text,
  contenedor_base   text,
  drive_file_id     text,
  drive_mime        text,
  drive_modified_at timestamptz,
  origen            text        not null default 'drive'
                                check (origen in ('drive', 'local')),
  copiado_at        timestamptz not null default now(),
  copiado_por       text        default nullif(current_setting('app.usuario', true), '')
);

-- Idempotencia de la copia: la misma versión del mismo archivo (o del mismo
-- miembro de un zip) no se registra dos veces.
create unique index if not exists packing_archivos_version_uq
  on costing.packing_archivos (coalesce(drive_file_id, ''), coalesce(miembro, ''), sha256);
create index if not exists packing_archivos_contenedor_idx
  on costing.packing_archivos (contenedor_base);
create index if not exists packing_archivos_drive_idx
  on costing.packing_archivos (drive_file_id, drive_modified_at desc);

alter table costing.packing_archivos enable row level security;
grant all on costing.packing_archivos to service_role;

comment on table costing.packing_archivos is
  'Una fila por versión de packing list guardada en el bucket privado packing-lists. '
  'tipo=original es la fuente del costo; tipo=ferraforme solo ubica el SKU en su renglón.';
comment on column costing.packing_archivos.ruta is
  'Objeto en el bucket packing-lists: <tipo>/<sha256>.<ext>. Inmutable: nunca se sube con upsert.';
comment on column costing.packing_archivos.nombre is
  'Nombre del archivo en Drive (o del archivo local) al momento de copiarlo. Puede cambiar en Drive; la huella no.';
comment on column costing.packing_archivos.miembro is
  'Si el archivo de Drive era un zip: el nombre del xlsx dentro del zip. NULL si no venía comprimido.';
comment on column costing.packing_archivos.contenedor_base is
  'Código ISO del contenedor sin sufijo de embarque (MRKU4831449), sacado del nombre. NULL si el nombre no trae uno.';
comment on column costing.packing_archivos.drive_mime is
  'Tipo en Drive. Un Google Sheet nativo se guarda EXPORTADO a xlsx: es una conversión, no el binario original.';
comment on column costing.packing_archivos.drive_modified_at is
  'modifiedTime de Drive de la versión copiada: con él la copia sabe si hay algo nuevo sin volver a bajar el archivo.';
