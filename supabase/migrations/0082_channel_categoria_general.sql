-- ═══════════════════════════════════════════════════════════════════════════
-- 0082 — Categoría GENERAL de cada SKU (raíz de Mercado Libre México),
--        decidida con la foto del packing list. 4-nov-2026.
--
-- POR QUÉ
-- El 4-nov se revisó todo el catálogo (16,441 SKUs en core.products): por cada
-- modelo se miró la foto del RENGLÓN del packing list (no la de Odoo/Woo, que a
-- veces es de otro producto) junto con los títulos del packing list, Odoo, la IA
-- y Gemini, y se eligió su categoría general de una lista cerrada (las raíces de
-- ML México, nunca «Servicios»). 6,527 modelos; la decisión pasa a todas sus
-- variantes. Controles escondidos (SKUs ya revisados a mano): confianza alta
-- 110/111, media 60/77, baja 9/16. Eduardo decidió cargar solo la confianza
-- alta (y los revisados a mano) y dejar media y baja pendientes.
--
-- Hoy no había dónde guardarla: channel.product_category guarda la HOJA de ML
-- (category_id), que es la que usa el publicador, y la general sola no es una
-- categoría publicable. Esta tabla la guarda APARTE, sin tocar esa hoja.
--
-- QUÉ CREA
--   · channel.categoria_general: una fila por SKU con la raíz elegida, la
--     confianza, el estado (aplicada | pendiente), de dónde salió (fuente,
--     sku_foto = el SKU del modelo cuya foto decidió) y el porqué.
--
-- QUIÉN ESCRIBE: solo service_role (scripts de carga y, cuando exista, el
-- backend). RLS sin políticas, grant a service_role, en la misma migración
-- (patrón de la casa).
--
-- QUIÉN LEE: nadie todavía. No cambia el publicador, ni Crear, ni ninguna
-- publicación: channel.product_category queda intacta.
--
-- Estado: aplicar PRIMERO en el sandbox. Producción solo con acta (doble candado).
--
-- REVERSA (no la lee nadie):
--   drop table channel.categoria_general;
-- ═══════════════════════════════════════════════════════════════════════════

begin;

create table if not exists channel.categoria_general (
    sku            citext      primary key references core.products(sku),
    categoria      text        not null,
    confianza      text        not null,
    estado         text        not null default 'aplicada',
    fuente         text        not null default 'foto_packing_list',
    sku_foto       citext,
    por_que        text,
    lote           text,
    creado_at      timestamptz not null default now(),
    actualizado_at timestamptz not null default now(),
    constraint categoria_general_raiz_ck check (categoria in (
        'Accesorios para Vehículos', 'Agro', 'Alimentos y Bebidas', 'Animales y Mascotas',
        'Antigüedades y Colecciones', 'Arte, Papelería y Mercería', 'Bebés', 'Belleza y Cuidado Personal',
        'Cámaras y Accesorios', 'Celulares y Telefonía', 'Computación', 'Consolas y Videojuegos',
        'Construcción', 'Deportes y Fitness', 'Electrodomésticos', 'Electrónica, Audio y Video',
        'Herramientas', 'Hogar, Muebles y Jardín', 'Industrias y Oficinas', 'Instrumentos Musicales',
        'Joyas y Relojes', 'Juegos y Juguetes', 'Libros, Revistas y Comics', 'Música, Películas y Series',
        'Otras Categorías', 'Recuerdos, Cotillón y Fiestas', 'Ropa, Bolsas y Calzado',
        'Salud y Equipamiento Médico')),
    constraint categoria_general_confianza_ck check (confianza in ('revisado', 'alta', 'media', 'baja')),
    constraint categoria_general_estado_ck check (estado in ('aplicada', 'pendiente'))
);
comment on table channel.categoria_general is
  'Categoría GENERAL (raíz de ML México) de cada SKU, decidida con la foto del renglón del packing list. '
  'Aparte de channel.product_category (la hoja que usa el publicador): no la reemplaza ni la lee nadie todavía. '
  'estado=aplicada solo para confianza revisado/alta (decisión de Eduardo, 4-nov-2026); media y baja = pendiente.';
comment on column channel.categoria_general.categoria is
  'Raíz de ML México tal como la muestra el panel («Animales y Mascotas» = raíz «Mascotas» del árbol).';
comment on column channel.categoria_general.confianza is
  'revisado = revisión a mano con la foto; alta/media/baja = la que marcó la revisión por lotes de mosaicos.';
comment on column channel.categoria_general.sku_foto is
  'SKU cuya foto de packing list decidió (el representante del modelo; las variantes heredan su decisión).';

create index if not exists categoria_general_categoria_idx on channel.categoria_general (categoria);

alter table channel.categoria_general enable row level security;
grant all on channel.categoria_general to service_role;

insert into ops.migraciones (migracion, detalle)
select '0082_channel_categoria_general',
       jsonb_build_object('tablas', jsonb_build_array('channel.categoria_general'))
 where to_regclass('ops.migraciones') is not null
   and not exists (select 1 from ops.migraciones where migracion = '0082_channel_categoria_general');

commit;
