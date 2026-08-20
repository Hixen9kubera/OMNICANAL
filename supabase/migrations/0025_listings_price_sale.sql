-- El precio que el comprador PAGA, junto al de lista.
--
-- POR QUÉ. `channel.listings.price` guarda `/items/{id}.price`, que el código
-- llamaba "lo que ve el comprador". Medido el 20-ago-2026 contra las ventas
-- reales de `channel.order_items` en 265 SKUs: la mediana de `price` está en
-- 1.71x el precio efectivamente transado. `item.price` NO baja cuando la
-- promoción la monta una CAMPAÑA de ML; ese descuento solo aparece en
-- `/items/{id}/sale_price?context=channel_marketplace`.
--
-- El costo del error es de valuación: el inventario FULL de SANCORFASHION al
-- 13-ago valía $8,623,671 con `price` y $5,999,662 al precio real — 44% de
-- sobrevaluación sobre 12,263 piezas. Y ese mismo `price` alimenta el margen
-- del panel, así que el sesgo no se queda en el reporte de inventario.
--
-- `price_sale` NO reemplaza a `price`: que difieran ES el dato (mide el
-- descuento vivo). Los lectores usan coalesce(price_sale, price) para que una
-- publicación aún no observada siga valuando con lo único que hay.
alter table channel.listings
    add column if not exists price_sale numeric,
    add column if not exists price_sale_at timestamptz;

comment on column channel.listings.price_sale is
    'Precio que el comprador PAGA (/items/{id}/sale_price, context=channel_marketplace). '
    'NULL = todavía no observado, NO "sin descuento" — usar coalesce(price_sale, price).';
comment on column channel.listings.price_sale_at is
    'Cuándo se observó price_sale. Sin esto no se distingue "sin promoción hoy" '
    'de "nadie ha preguntado": ambos se ven como price_sale = price.';
