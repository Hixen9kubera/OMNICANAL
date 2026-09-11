-- ═══════════════════════════════════════════════════════════════════════════
-- 0051 — El modo de publicación de una familia, POR CANAL
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Un producto con variantes se puede publicar de dos maneras, y la elección no
-- es una preferencia visual: cambia QUÉ se crea en el marketplace.
--
--   agrupada   una sola publicación con selector de variante. Título,
--              descripción, categoría e imágenes se comparten.
--   individual una publicación por variante, cada una con su ficha completa.
--
-- POR QUÉ ES POR CANAL Y NO GLOBAL. Mercado Libre y Amazon admiten variantes
-- nativas; TikTok las maneja distinto. El mismo producto puede estar agrupado
-- en ML y separado en TikTok sin contradicción, así que la llave lleva canal.
--
-- POR QUÉ EN LA BASE Y NO EN localStorage. Lo eligió el diseño y tiene razón:
-- dos personas trabajando el mismo catálogo tienen que ver el MISMO modo. Un
-- modo guardado en el navegador haría que cada quien publicara distinto sin
-- enterarse — y el resultado sí es compartido: la publicación en el canal.
--
-- LA LLAVE ES EL SKU DEL PADRE. El modo es de la FAMILIA, no de la variante:
-- decir "agrupada" sobre una sola hija no significa nada. El parentesco sale
-- de `wp_posts.post_parent`, NUNCA del prefijo del SKU — tomar los dos primeros
-- segmentos ya fusionó 104 pares en WooCommerce, de los que 34 eran productos
-- distintos (un refractómetro con una hebilla de mancuerna).
--
-- ESTADO AL APLICARSE (10-sep-2026): `agrupada` todavía NO SE PUEDE EJECUTAR en
-- Mercado Libre. El publicador arma un payload plano —la llave `variations` no
-- aparece en el repositorio— y hay 0 publicaciones con variantes en las dos
-- cuentas (2,521 de SANCORFASHION + 2,486 de BEKURA, leídas el 9-sep). El panel
-- muestra el modo como «Pronto». La tabla se aplica ahora para que el día que
-- exista el agrupado no haya que migrar decisiones ya tomadas.
--
-- POR CANAL, NO POR CUENTA (decisión de Eduardo, 11-sep-2026). La tabla hermana
-- enrich.channel_content lleva `cuenta` en la llave porque en ML las dos
-- cuentas (BEKURA y SANCORFASHION) pueden tener el MISMO SKU en categorías
-- distintas (caso EST-0091). Aquí no: el modo es de la familia EN EL CANAL y
-- las dos cuentas de ML lo comparten. Si algún día una cuenta necesita agrupar
-- y la otra no, se re-llavea; mejor decidirlo con la tabla vacía que con
-- decisiones dentro.
--
-- REVISIÓN DEL 11-sep (Eduardo + consejo), antes de aplicarla:
--   · RLS + grant a service_role, como toda tabla de negocio (0016, 0049).
--     Sin esto `backend/scripts/verificar_rls.py` sale con exit 1 y el job de
--     CI "Blindaje BD" queda en rojo.
--   · `canal` y `sku` con llave foránea, como el resto del esquema v4:
--     core.channels(id) en vez de una lista escrita a mano en un check (un
--     canal nuevo se da de alta en core.channels y aquí funciona solo), y
--     core.products(sku), donde están los 1,502 padres con variantes. Ningún
--     código de producción borra ni renombra SKUs en core.products.
--   · Fuera el índice (canal, sku): la única lectura es `sku = any(...)`, sin
--     canal, y esa la resuelve la llave primaria (sku, canal).
--   · begin/commit, y bloque de verificación y reversa al pie.
-- Verificado el 11-sep: la tabla NO existía en producción, así que el
-- `create table if not exists` crea esta forma y no una anterior.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

create table if not exists channel.publication_mode (
    -- SKU del PADRE (la familia), no el de una variante.
    sku         citext      not null references core.products(sku),
    canal       text        not null references core.channels(id),
    modo        text        not null default 'individual',
    updated_at  timestamptz not null default now(),
    updated_by  text,
    primary key (sku, canal),
    constraint publication_mode_modo_chk
        check (modo in ('agrupada', 'individual'))
);

comment on table channel.publication_mode is
  'Modo de publicación (agrupada|individual) de una familia de variantes, por canal (no por cuenta). Llave: SKU del PADRE.';
comment on column channel.publication_mode.sku is
  'SKU del padre. El parentesco viene de wp_posts.post_parent, nunca del prefijo del SKU.';

-- Sin índice extra: la única lectura (modo_publicacion._leer_sync) es
-- `where sku = any(...)`, y la sirve la llave primaria (sku, canal).

-- El candado, donde nace el objeto (0016, 0049). Sin políticas: RLS activa y
-- 0 políticas = solo pasa quien hace bypass (service_role y postgres).
alter table channel.publication_mode enable row level security;
grant all on channel.publication_mode to service_role;

commit;

-- VERIFICACIÓN
--   select to_regclass('channel.publication_mode');                  -- no null
--   select relrowsecurity from pg_class
--    where oid = 'channel.publication_mode'::regclass;               -- true
--   select conname from pg_constraint
--    where conrelid = 'channel.publication_mode'::regclass order by 1;
--     -- publication_mode_canal_fkey, publication_mode_modo_chk,
--     -- publication_mode_pkey, publication_mode_sku_fkey
--   python backend/scripts/verificar_rls.py                          -- exit 0
-- REVERSA
--   drop table channel.publication_mode;   -- nace vacía y nada depende de ella
