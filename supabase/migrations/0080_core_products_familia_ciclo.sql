-- ═══════════════════════════════════════════════════════════════════════════
-- 0080 — Familia y ciclo de vida del catálogo en `core.products`.
--        28-oct-2026. PLAN MAESTRO 3.2 (F0.3 del plan de salida de Woo).
--        Número 0080 PENDIENTE de que Brandon lo confirme (RESERVAS.md aún no está en main).
--
-- POR QUÉ
-- El SKU tiene que poder nacer y vivir en kubera sin Woo. Hoy la familia (padre →
-- variantes) solo existe en `wc_parent_id`, que es una llave de Woo, y el ciclo de
-- vida (borrador, publicado, baja) solo en el `post_status` de Woo:
--   · `parent_sku` y `has_variations` existen desde la 0001 pero nadie los llena
--     (0 de 16,169 filas; medido el 28-oct). El ETL de las 06:15 pone
--     has_variations=false cada noche: su cambio va en el MISMO commit.
--   · No hay `estado_ciclo` ni `procesada_at`.
--
-- QUÉ HACE
--   · `estado_ciclo` (draft | procesado | listo | publicado | archivado) y `procesada_at`.
--   · TRES funciones que son EL ÚNICO LUGAR del mapeo (para ajustarlo cuando Eduardo y
--     Brandon decidan, se reemplaza la función y se vuelve a llenar):
--       core.publicacion_abierta(canal, status, situacion) → ¿esa publicación cuenta como abierta?
--       core.sku_publicado(sku)                             → ¿el SKU, su padre o alguna variante
--                                                              tiene una publicación abierta?
--       core.estado_ciclo_inicial(status, publicada)        → el estado de arranque.
--     La publicación cuenta por FAMILIA: ML publica la familia bajo el artículo padre (las
--     variantes casi nunca tienen fila propia en channel.listings) y Amazon publica la hija.
--     Medido en el sandbox el 28-oct: sin esto, 6,829 variaciones en `publish` quedaban
--     `draft` (1,763 con el padre publicado).
--     Propuesta vigente (recomendación de MAESTRO, 28-oct): «publicado» = al menos una
--     publicación NO cerrada en channel.listings (Woo se va, no debe definir el ciclo);
--     packing_list_only y demás etiquetas sin Woo → draft.
--     `pending` → procesado (MAESTRO, 28-oct): es el paso de Woo que pone Crear al terminar
--     (`crear_producto`: status_final y `destrabar_inprogress`). Medido en producción el 28-oct:
--     1,481 en pending, todas padres o simples; 1,060 tienen publicación abierta (quedan
--     `publicado`) y de las 421 restantes, 289 tienen «completado» de Crear en ops.process_log;
--     las 132 sin rastro nacieron en kubera del 24-jul al 12-ago, antes de que la bitácora
--     estuviera completa.
--   · Relleno: familia desde `wc_parent_id` (7,477 variaciones, 1,503 padres; todos los
--     padres tienen fila) y `estado_ciclo` con las funciones. Después: default 'draft',
--     NOT NULL y el CHECK.
--   · Trigger `products_archivar`: una fila que pasa a status trash/deleted (la auditoría
--     de Crear, el seam) queda `archivado`. Así ningún código nombra la columna nueva para
--     dar de baja: migración y código se despliegan en cualquier orden. Nunca saca a nadie
--     de `archivado`.
--   · El candado de la tabla se reafirma en la misma migración (RLS + grant).
--
-- LO QUE NO HACE
--   · `procesada_at`: la meta `_crear_procesada_at` vive en Woo (142 posts); la llena 3.3
--     (`rellenar_procesada_at.py` y el seam de Crear), no esta migración.
--   · Marcar `archivado` lo que Woo ya no tiene: eso lo hace el ETL (lee Woo).
--
-- Estado: aplicar PRIMERO en el sandbox. Producción solo con acta (doble candado).
-- REVERSA (nadie lee estas columnas todavía):
--   drop trigger products_archivar on core.products;
--   drop function core.tg_products_archivar(); drop function core.estado_ciclo_inicial(text, boolean);
--   drop function core.sku_publicado(citext);
--   drop function core.publicacion_abierta(text, text, text);
--   alter table core.products drop column estado_ciclo, drop column procesada_at;
--   update core.products set parent_sku = null, has_variations = false;   -- solo si se quiere volver al vacío
-- ═══════════════════════════════════════════════════════════════════════════

begin;

alter table core.products add column if not exists estado_ciclo text;
alter table core.products add column if not exists procesada_at timestamptz;

comment on column core.products.estado_ciclo is
  'Ciclo de vida del SKU en kubera: draft | procesado | listo | publicado | archivado. Arranque por '
  'core.estado_ciclo_inicial; archivado por el trigger products_archivar (status trash/deleted) y por el ETL '
  '(lo que Woo ya no tiene). PLAN MAESTRO 3.2.';
comment on column core.products.procesada_at is
  'Cuándo el flujo de alta (Crear) procesó el SKU. Lo llena 3.3; NULL = no se sabe.';

-- ── EL MAPEO, en un solo lugar ───────────────────────────────────────────────
create or replace function core.publicacion_abierta(p_canal text, p_status text, p_situacion text)
returns boolean language sql immutable as $$
  select case p_canal
    when 'general'       then false                                         -- el espejo de Woo no es un canal
    when 'mercado_libre' then coalesce(p_situacion, '') <> 'closed'
    when 'amazon'        then coalesce(p_situacion, '') <> 'closed' and coalesce(p_status, '') <> 'DELETED'
    when 'tiktok'        then coalesce(p_status, '') <> 'DELETED'
    when 'temu'          then coalesce(p_status, '') <> '5/None'               -- borrador: nunca se envió
    else true
  end
$$;
comment on function core.publicacion_abierta(text, text, text) is
  '¿La publicación cuenta como NO cerrada para el ciclo del catálogo? Propuesta del 28-oct (PLAN MAESTRO 3.2); '
  'la decide Eduardo con Brandon. Para cambiarla: create or replace + volver a llenar estado_ciclo.';

create or replace function core.sku_publicado(p_sku citext)
returns boolean language sql stable as $$
  -- Tres EXISTS por separado, cada uno por índice (channel.listings.sku, core.products.pk y
  -- parent_sku). Con los tres en un solo EXISTS unidos por OR, cada fila recorría
  -- channel.listings completa: el relleno de 16k filas no cabía en 3 min (sandbox, 28-oct).
  select exists (select 1 from channel.listings l
                  where l.sku = p_sku and core.publicacion_abierta(l.canal, l.status, l.situacion))
      or exists (select 1 from core.products pr join channel.listings l on l.sku = pr.parent_sku
                  where pr.sku = p_sku and core.publicacion_abierta(l.canal, l.status, l.situacion))
      or exists (select 1 from core.products h join channel.listings l on l.sku = h.sku
                  where h.parent_sku = p_sku and core.publicacion_abierta(l.canal, l.status, l.situacion))
$$;
comment on function core.sku_publicado(citext) is
  '¿El SKU, su padre o alguna de sus variantes tiene una publicación abierta? La publicación cuenta por familia '
  '(PLAN MAESTRO 3.2, propuesta del 28-oct).';

create or replace function core.estado_ciclo_inicial(p_status text, p_publicada boolean)
returns text language sql immutable as $$
  select case
    when p_status in ('trash', 'deleted') then 'archivado'
    when p_publicada                      then 'publicado'
    when p_status = 'ready'               then 'listo'
    when p_status = 'pending'             then 'procesado'                  -- Crear lo terminó (ver arriba)
    else 'draft'                                                            -- incluye packing_list_only y demás
  end
$$;
comment on function core.estado_ciclo_inicial(text, boolean) is
  'Estado de arranque de estado_ciclo. Propuesta del 28-oct (PLAN MAESTRO 3.2); la decide Eduardo con Brandon.';

-- ── Relleno (solo lo que está vacío: re-ejecutable) ─────────────────────────
update core.products h set parent_sku = p.sku
  from core.products p
 where h.wc_parent_id is not null and p.wc_id = h.wc_parent_id
   and h.parent_sku is distinct from p.sku;

update core.products p set has_variations = true
 where not p.has_variations and p.wc_id is not null
   and exists (select 1 from core.products h where h.wc_parent_id = p.wc_id);

update core.products p
   set estado_ciclo = core.estado_ciclo_inicial(p.status, core.sku_publicado(p.sku))
 where p.estado_ciclo is null;

alter table core.products alter column estado_ciclo set default 'draft';
alter table core.products alter column estado_ciclo set not null;
alter table core.products drop constraint if exists products_estado_ciclo_chk;
alter table core.products add constraint products_estado_ciclo_chk
  check (estado_ciclo in ('draft', 'procesado', 'listo', 'publicado', 'archivado'));

-- ── Bajas: status trash/deleted → archivado ────────────────────────────────
create or replace function core.tg_products_archivar() returns trigger
language plpgsql as $$
begin
  if new.status in ('trash', 'deleted') then
    new.estado_ciclo := 'archivado';
  end if;
  return new;
end $$;

drop trigger if exists products_archivar on core.products;
create trigger products_archivar before insert or update of status on core.products
  for each row execute function core.tg_products_archivar();

-- ── Candado (patrón de la casa) ─────────────────────────────────────────────
alter table core.products enable row level security;
grant all on core.products to service_role;
revoke all on function core.publicacion_abierta(text, text, text) from public, anon, authenticated;
revoke all on function core.estado_ciclo_inicial(text, boolean) from public, anon, authenticated;
revoke all on function core.sku_publicado(citext) from public, anon, authenticated;
revoke all on function core.tg_products_archivar() from public, anon, authenticated;

insert into ops.migraciones (migracion, detalle)
select '0080_core_products_familia_ciclo',
       jsonb_build_object('tabla', 'core.products', 'columnas', jsonb_build_array('estado_ciclo', 'procesada_at'),
                          'relleno', jsonb_build_array('parent_sku', 'has_variations', 'estado_ciclo'),
                          'plan_maestro', '3.2')
 where to_regclass('ops.migraciones') is not null
   and not exists (select 1 from ops.migraciones where migracion = '0080_core_products_familia_ciclo');

commit;
