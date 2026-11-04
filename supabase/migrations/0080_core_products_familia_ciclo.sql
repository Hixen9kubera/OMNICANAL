-- ═══════════════════════════════════════════════════════════════════════════
-- 0080 — Familia, preparación y BAJA del catálogo en `core.products`; «publicado» se calcula.
--        28-oct-2026, rehecha el 3-nov con el diseño de Eduardo del 2-nov. PLAN MAESTRO 3.2 (F0.3).
--        Número 0080 reservado en RESERVAS.md (rama docs/reservas-migraciones); Brandon lo confirma.
--
-- POR QUÉ
-- El SKU tiene que poder nacer y vivir en kubera sin Woo. Hoy la familia (padre → variantes)
-- solo existe en `wc_parent_id`, que es una llave de Woo, y la baja solo en el `post_status` de Woo:
--   · `parent_sku` y `has_variations` existen desde la 0001 pero nadie los llena (0 de 16,169
--     filas, medido el 28-oct). El ETL de las 06:15 pone has_variations=false cada noche: su
--     cambio va en el MISMO commit.
--
-- DISEÑO DE EDUARDO (2-nov): «publicado» y «archivado» son DOS preguntas y no comparten columna.
--                     no archivado                    archivado
--   publicado         normal: está en canales         INCOHERENTE: dado de baja y vivo en un canal
--   no publicado      pendiente: cola del Publicador  baja limpia: no aparece en ningún lado
--
--   · PUBLICADO es un hecho que se CALCULA desde channel.listings y no tiene escritor: el SKU tiene
--     al menos una publicación que existe en el canal, activa o pausada. Una cerrada, borrada, en
--     revisión o borrador no cuenta. Para Mercado Libre es la MISMA regla que la pestaña Catálogo
--     (`channel_read._SIT_PUBLICADO_ML`, v0.708.0: `listing_id` con valor y situación active o
--     paused). Para Temu y TikTok, los estados que `publicaciones_lote._ESTADOS` llama a la venta,
--     publicada, apagada o apagada por el canal (existen en el canal); un código que nadie ha visto
--     NO cuenta. La familia cuenta SOLO HACIA ARRIBA: un padre está publicado si lo está él o
--     alguna de sus variantes (el padre no se publica: es el resumen de su familia). Una variante
--     cuenta SOLO por su propia publicación: la del padre en ML es otra publicación, suelta y casi
--     siempre pausada, que no la contiene (medición de MAESTRO del 4-nov: 0 de 815 variantes).
--     `core.sku_publicado(sku, por_familia)`; con false, solo la propia.
--   · ARCHIVADO es una BAJA que decide una persona (descontinuado, duplicado, recodificado a otro
--     SKU, de prueba): fecha, quién, motivo y, si es recodificado, a qué SKU pasó. Solo una persona
--     lo quita. Mientras Woo exista, la papelera de Woo lo archiva sola (trigger) y el ETL archiva
--     lo que Woo ya no tiene.
--   · `estado_ciclo` solo lleva el AVANCE DE PREPARACIÓN: draft | procesado | listo.
--
-- QUÉ HACE
--   · Columnas: `estado_ciclo`, `procesada_at` (la llena 3.3), `archivado_at`, `archivado_por`,
--     `archivado_motivo`, `archivado_sku_destino`, con sus CHECK de coherencia.
--   · Funciones, EL ÚNICO LUGAR de cada regla (se ajustan con create or replace):
--       core.publicacion_abierta(canal, status, situacion, listing_id)
--       core.sku_publicado(sku, por_familia default true)
--       core.estado_ciclo_inicial(status)
--   · Vista `core.products_ciclo_v`: sku, preparación, baja, `publicado` y la situación de la tabla
--     de arriba. Con security_invoker: solo service_role la lee.
--   · Relleno: familia desde `wc_parent_id` (7,477 variaciones, 1,503 padres), `estado_ciclo` y
--     la baja de lo que hoy está en trash/deleted. Después: default 'draft', NOT NULL y el CHECK.
--   · Trigger `products_archivar`: una fila que pasa a status trash/deleted queda archivada (por
--     'woo', motivo 'papelera_woo') si no lo estaba. Nunca la desarchiva.
--   · El candado de la tabla se reafirma en la misma migración (RLS + grant).
--
-- LO QUE NO HACE
--   · `procesada_at`: la meta `_crear_procesada_at` vive en Woo (142 posts); la llena 3.3.
--   · Archivar lo que Woo ya no tiene: lo hace el ETL (lee Woo), con sus frenos.
--   · Guardar «publicado»: se calcula siempre.
--
-- Estado: aplicar PRIMERO en el sandbox (allá estaba la 0080 del 28-oct: el acta la rehace con
-- `aplicar_0080.py sandbox --rehacer-vieja`, que deshace la vieja y aplica esta en UNA transacción; ops.migraciones
-- es de solo agregar y queda con dos filas). Producción solo con acta (doble candado).
-- REVERSA (nadie fuera de 3.2 lee estas columnas):
--   drop view core.products_ciclo_v;
--   drop trigger products_archivar on core.products; drop function core.tg_products_archivar();
--   drop function core.estado_ciclo_inicial(text); drop function core.sku_publicado(citext, boolean);
--   drop function core.publicacion_abierta(text, text, text, text);
--   drop index core.products_archivados_idx;
--   alter table core.products drop column estado_ciclo, drop column procesada_at,
--     drop column archivado_at, drop column archivado_por, drop column archivado_motivo,
--     drop column archivado_sku_destino;
--   update core.products set parent_sku = null, has_variations = false;   -- solo si se quiere volver al vacío
--   -- ops.migraciones es de solo agregar: la reversa deja su propia fila (M2 de la verificación de 8c7dd6bb).
--   insert into ops.migraciones (migracion, detalle)
--     values ('0080_core_products_familia_ciclo', '{"modo": "revertida"}'::jsonb);
--   ANTES de la reversa: guardar las filas con archivado_at (las bajas de una persona se perderían).
-- ═══════════════════════════════════════════════════════════════════════════

begin;

alter table core.products add column if not exists estado_ciclo text;
alter table core.products add column if not exists procesada_at timestamptz;
alter table core.products add column if not exists archivado_at timestamptz;
alter table core.products add column if not exists archivado_por text;
alter table core.products add column if not exists archivado_motivo text;
alter table core.products add column if not exists archivado_sku_destino citext
  references core.products (sku) on update cascade;

comment on column core.products.estado_ciclo is
  'Avance de PREPARACIÓN del SKU: draft | procesado | listo. No dice si está publicado (eso se calcula, '
  'core.sku_publicado) ni si está dado de baja (archivado_at). PLAN MAESTRO 3.2, diseño del 2-nov.';
comment on column core.products.procesada_at is
  'Cuándo el flujo de alta procesó el SKU. Lo llena 3.3; NULL = no se sabe.';
comment on column core.products.archivado_at is
  'La BAJA del SKU: ya no se trabaja ni se vende. NULL = no archivado. La escriben el trigger '
  'products_archivar (papelera de Woo), el ETL (lo que Woo ya no tiene) y, después, una persona. '
  'Solo una persona lo quita.';
comment on column core.products.archivado_por is 'Quién archivó: una persona (correo), ''woo'' o ''etl''.';
comment on column core.products.archivado_motivo is
  'descontinuado | duplicado | recodificado | prueba (decide una persona) · papelera_woo | ya_no_en_woo (automático).';
comment on column core.products.archivado_sku_destino is 'Si es recodificado: a qué SKU pasó.';

-- ── LAS REGLAS, en un solo lugar ─────────────────────────────────────────────
create or replace function core.publicacion_abierta(p_canal text, p_status text, p_situacion text,
                                                    p_listing_id text)
returns boolean language sql immutable as $$
  select nullif(p_listing_id, '') is not null and case p_canal
    when 'general'       then false                            -- el espejo de Woo no es un canal
    -- La MISMA de Catálogo (channel_read._SIT_PUBLICADO_ML, v0.708.0). under_review e inactive, fuera.
    when 'mercado_libre' then lower(coalesce(p_situacion, '')) in ('active', 'paused')
    when 'amazon'        then upper(coalesce(p_situacion, '')) in ('BUYABLE', 'DISCOVERABLE', 'PUBLISHED')
                              and upper(coalesce(p_status, '')) not in ('DELETED', 'INVALID')
    -- publicaciones_lote._ESTADOS: a_la_venta, publicada, apagada, apagada_canal. Fuera: en revisión
    -- (PENDING), borrador, revisión fallida, borrada.
    when 'tiktok'        then upper(coalesce(p_status, '')) in ('ACTIVATE', 'SELLER_DEACTIVATED',
                                                                'PLATFORM_DEACTIVATED', 'FREEZE')
    -- 2/8 a la venta · 3/1 agotada · 3/3 retenida · 2/4 activa sin vender · 3/2 apagada. Fuera: 4/7
    -- incompleta, 5/None borrador, 6/None sin publicar y cualquier código que nadie ha visto.
    when 'temu'          then coalesce(p_status, '') in ('2/8', '3/1', '3/3', '2/4', '3/2')
    -- Solo PUBLISHED, como el resto del código (F2 de la verificación de 8c7dd6bb). La foto de Walmart es
    -- de agosto y nadie la escribe: UNPUBLISHED no dice si existe hoy.
    when 'walmart'       then upper(coalesce(p_status, '')) = 'PUBLISHED'
    else false
  end
$$;
comment on function core.publicacion_abierta(text, text, text, text) is
  '¿La publicación EXISTE en el canal, activa o pausada? (PLAN MAESTRO 3.2, diseño del 2-nov). ML = la regla de '
  'Catálogo v0.708.0. Para cambiarla: create or replace; publicado se calcula, no hay que rellenar nada.';

create or replace function core.sku_publicado(p_sku citext, p_por_familia boolean default true)
returns boolean language sql stable as $$
  -- Dos EXISTS por separado, cada uno por índice (channel.listings.sku y parent_sku). Unidos por OR
  -- en un solo EXISTS, cada fila recorría channel.listings completa (sandbox, 28-oct).
  -- La variante NO hereda la publicación de su padre: en ML es otra publicación, suelta, que no la
  -- contiene (medición de MAESTRO del 4-nov: 0 de 815).
  select exists (select 1 from channel.listings l
                  where l.sku = p_sku and core.publicacion_abierta(l.canal, l.status, l.situacion, l.listing_id))
      or (p_por_familia and exists (
            select 1 from core.products h join channel.listings l on l.sku = h.sku
             where h.parent_sku = p_sku and core.publicacion_abierta(l.canal, l.status, l.situacion, l.listing_id)))
$$;
comment on function core.sku_publicado(citext, boolean) is
  '¿El SKU está publicado (una publicación que existe, activa o pausada)? Cuenta la suya; con por_familia '
  '(por omisión), un padre también por la de cualquiera de sus variantes. Una variante nunca por la de su padre.';

create or replace function core.estado_ciclo_inicial(p_status text)
returns text language sql immutable as $$
  select case
    when p_status in ('ready', 'publish') then 'listo'                    -- terminó la preparación
    when p_status = 'pending'             then 'procesado'                -- Crear lo terminó (medido 28-oct)
    else 'draft'                                                          -- incluye packing_list_only y demás
  end
$$;
comment on function core.estado_ciclo_inicial(text) is
  'Preparación de arranque desde el post_status de Woo (PLAN MAESTRO 3.2). La baja va aparte (archivado_at).';

-- ── Relleno (solo lo que está vacío: re-ejecutable) ─────────────────────────
-- Llenar columnas nuevas no es un cambio del producto: con trg_touch_products encendido, las
-- 16 mil filas saldrían «recién actualizadas» (la red viva de /flujo ordena por updated_at). Se
-- apaga solo para el relleno; la tabla ya está bloqueada por los alter de arriba y se vuelve a
-- encender antes del commit.
alter table core.products disable trigger trg_touch_products;

update core.products h set parent_sku = p.sku
  from core.products p
 where h.wc_parent_id is not null and p.wc_id = h.wc_parent_id
   and h.parent_sku is distinct from p.sku;

update core.products p set has_variations = true
 where not p.has_variations and p.wc_id is not null
   and exists (select 1 from core.products h where h.wc_parent_id = p.wc_id);

update core.products set estado_ciclo = core.estado_ciclo_inicial(status) where estado_ciclo is null;

update core.products
   set archivado_at = coalesce(updated_at, now()), archivado_por = 'relleno_0080', archivado_motivo = 'papelera_woo'
 where status in ('trash', 'deleted') and archivado_at is null;

alter table core.products enable trigger trg_touch_products;

alter table core.products alter column estado_ciclo set default 'draft';
alter table core.products alter column estado_ciclo set not null;
alter table core.products drop constraint if exists products_estado_ciclo_chk;
alter table core.products add constraint products_estado_ciclo_chk
  check (estado_ciclo in ('draft', 'procesado', 'listo'));
alter table core.products drop constraint if exists products_archivado_chk;
alter table core.products add constraint products_archivado_chk check (
      (archivado_at is null) = (archivado_motivo is null)
  and (archivado_at is null) = (archivado_por is null)
  and (archivado_motivo is null or archivado_motivo in ('descontinuado', 'duplicado', 'recodificado', 'prueba',
                                                        'papelera_woo', 'ya_no_en_woo'))
  -- el destino va si y solo si es recodificado
  and (archivado_sku_destino is null or archivado_motivo = 'recodificado')
  and (archivado_motivo is distinct from 'recodificado' or archivado_sku_destino is not null)
  -- y no es el mismo SKU (M4). Cadenas y ciclos (A→B con B archivado) no los ve un CHECK: los cuida quien archiva.
  and archivado_sku_destino is distinct from sku);
create index if not exists products_archivados_idx on core.products (sku) where archivado_at is not null;

-- ── Bajas: status trash/deleted → archivado (si no lo estaba). Nunca desarchiva ──
create or replace function core.tg_products_archivar() returns trigger
language plpgsql as $$
begin
  if new.status in ('trash', 'deleted') and new.archivado_at is null then
    new.archivado_at := now();
    new.archivado_por := 'woo';
    new.archivado_motivo := 'papelera_woo';
  end if;
  return new;
end $$;

drop trigger if exists products_archivar on core.products;
create trigger products_archivar before insert or update of status on core.products
  for each row execute function core.tg_products_archivar();

-- ── La vista: las dos preguntas juntas ───────────────────────────────────────
create or replace view core.products_ciclo_v with (security_invoker = true) as
select p.sku, p.estado_ciclo, p.procesada_at,
       p.archivado_at, p.archivado_por, p.archivado_motivo, p.archivado_sku_destino,
       core.sku_publicado(p.sku) as publicado,
       case when p.archivado_at is not null and core.sku_publicado(p.sku) then 'incoherente'
            when p.archivado_at is not null                               then 'baja_limpia'
            when core.sku_publicado(p.sku)                                then 'normal'
            else 'pendiente' end as situacion
  from core.products p;
comment on view core.products_ciclo_v is
  'Publicado (calculado) y archivado (decisión) del SKU, con la situación de la tabla de Eduardo del 2-nov. '
  'publicado = core.sku_publicado(sku): su propia publicación y, si es padre, la de cualquiera de sus variantes. '
  'Para el universo entero es pesada (sku_publicado por fila): filtrar antes (p. ej. archivado_at is not null).';

-- ── Candado (patrón de la casa) ─────────────────────────────────────────────
alter table core.products enable row level security;
grant all on core.products to service_role;
revoke all on core.products_ciclo_v from public, anon, authenticated;
grant select on core.products_ciclo_v to service_role;
revoke all on function core.publicacion_abierta(text, text, text, text) from public, anon, authenticated;
revoke all on function core.estado_ciclo_inicial(text) from public, anon, authenticated;
revoke all on function core.sku_publicado(citext, boolean) from public, anon, authenticated;
revoke all on function core.tg_products_archivar() from public, anon, authenticated;
-- La vista es security_invoker: quien la lee ejecuta estas funciones. Sin el grant, service_role daba «permission
-- denied» (M1 de la verificación de 8c7dd6bb; el mismo patrón que 0064, 0065 y 0074).
grant execute on function core.publicacion_abierta(text, text, text, text) to service_role;
grant execute on function core.estado_ciclo_inicial(text) to service_role;
grant execute on function core.sku_publicado(citext, boolean) to service_role;

insert into ops.migraciones (migracion, detalle)
select '0080_core_products_familia_ciclo',
       jsonb_build_object('tabla', 'core.products',
                          'columnas', jsonb_build_array('estado_ciclo', 'procesada_at', 'archivado_at',
                                                        'archivado_por', 'archivado_motivo', 'archivado_sku_destino'),
                          'vista', 'core.products_ciclo_v',
                          'relleno', jsonb_build_array('parent_sku', 'has_variations', 'estado_ciclo', 'archivado_at'),
                          'plan_maestro', '3.2', 'diseno', '2-nov')
 where to_regclass('ops.migraciones') is not null
   and not exists (select 1 from ops.migraciones where migracion = '0080_core_products_familia_ciclo');

commit;
