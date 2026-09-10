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
-- ═══════════════════════════════════════════════════════════════════════════

create table if not exists channel.publication_mode (
    -- SKU del PADRE (la familia), no el de una variante.
    sku         citext      not null,
    canal       text        not null,
    modo        text        not null default 'individual',
    updated_at  timestamptz not null default now(),
    updated_by  text,
    primary key (sku, canal),
    constraint publication_mode_modo_chk
        check (modo in ('agrupada', 'individual')),
    constraint publication_mode_canal_chk
        check (canal in ('general', 'mercado_libre', 'amazon',
                         'tiktok', 'walmart', 'temu', 'shein'))
);

comment on table channel.publication_mode is
  'Modo de publicación (agrupada|individual) de una familia de variantes, por canal. Llave: SKU del PADRE.';
comment on column channel.publication_mode.sku is
  'SKU del padre. El parentesco viene de wp_posts.post_parent, nunca del prefijo del SKU.';

-- La lista del Publicador pide el modo de muchas familias de golpe para pintar
-- su chip; sin esto sería un seq scan por página.
create index if not exists publication_mode_canal_idx
    on channel.publication_mode (canal, sku);
