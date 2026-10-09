-- ═══════════════════════════════════════════════════════════════════════════
-- 0036 — La bitácora de las órdenes de venta que el panel crea en Odoo.
--
-- Estado: APLICADA. Sandbox (yvootpbz) y producción (kubera) el 26-ago-2026
-- con el sí explícito de Eduardo, 15/15 en cada una dentro de la transacción
-- antes de confirmar. `ops` pasó de 14 a 16 tablas.
--
-- QUÉ ES Y DE DÓNDE SALE
-- -----------------------
-- Sustituye a las dos tablas de bitácora de `0033_ops_odoo_sale_orders.sql`,
-- que NO se aplicó nunca. Su tercera tabla, el interruptor, ya se aplicó por
-- separado en la 0035. Revisada por consejo (opus, sonnet, haiku) el
-- 26-ago-2026; reportes en `agents/counselors/1787944905-wtcaja/`.
--
-- Los tres coinciden en que las tablas SE JUSTIFICAN y no se pueden reducir:
-- meterlas como columnas en `channel.orders` dejaría 25,574 filas (98.9%) con
-- columnas de Odoo vacías por un flujo que solo toca TikTok/Temu; y guardar
-- solo la foto de stock perdería `accion = error | sku_sin_producto`, que
-- describe algo que NO pasó y por eso no se le puede preguntar a Odoo.
--
-- ⚠️ ROMPE AL ESCRITOR ACTUAL, A PROPÓSITO Y CON EL SÍ DE EDUARDO
-- ---------------------------------------------------------------
-- `odoo_ventas_log.registrar` NO puede escribir estas tablas tal como está hoy:
--
--   · no manda `cuenta`, que aquí es NOT NULL y parte de la llave
--   · usa `on conflict (canal, external_order_id)`, que ya no es una llave
--   · escribe `stock_texco` / `stock_texco2`, columnas que aquí no existen
--
-- Se aplica igual porque **hoy no escribe nada de todos modos**: estas tablas
-- no existían, así que `registrar` ya venía fallando en cada venta de TikTok
-- —en silencio, dentro de un `except` que solo hace `log.warning`—. Aplicar
-- esto no pierde ni un dato más; cambia un fallo callado por otro, y el día que
-- se parchee el escritor la forma queda correcta de una vez, en vez de nacer
-- torcida y necesitar después una migración sobre la llave primaria.
--
-- Y ojo con lo que NO es obvio: `registrar` corre en CADA venta de TikTok
-- aunque la automatización esté apagada. Con el switch en off, `crear_orden` no
-- devuelve `None` sino `{"accion": "apagado"}`, y el seam registra igual.
--
-- LOS TRES CAMBIOS QUE NECESITA `odoo_ventas_log.py`
-- ---------------------------------------------------
--   1. Mandar `cuenta` en los dos INSERT y cambiar el `on conflict` a
--      `(canal, cuenta, external_order_id)` — y a `(canal, cuenta,
--      external_order_id, linea)` en las líneas. El valor ya está disponible en
--      el sitio de la llamada (`pedidos_ml.py:851` lo devuelve como
--      `orden["cuenta"]`).
--   2. Escribir la foto como `stock_libre` jsonb llaveado por warehouse_id
--      —{"135": 0, "150": 30}— en vez de `stock_texco` / `stock_texco2`
--      buscados por NOMBRE de almacén.
--   3. `actualizar_guia` también manda `cuenta` en su `where`.
--
-- El detalle de por qué cada columna es como es va comentado abajo.
-- ═══════════════════════════════════════════════════════════════════════════

begin;


create table if not exists ops.odoo_sale_orders (
    -- Sin CHECK por la misma razón que `accion`: el día que se sume un canal,
    -- un candado aquí tira los renglones de ese canal en silencio.
    canal              text        not null,

    -- `cuenta` NO estaba en la 0033. Va porque la llave de `channel.orders` es
    -- (canal, cuenta, external_order_id) — TRES columnas — y el comentario de
    -- la 0033 afirmaba ser "la misma llave" siendo de dos. Sin ella, el día que
    -- haya una segunda tienda de TikTok el `on conflict do update` PISA EN
    -- SILENCIO la fila anterior, y con ella la foto de stock, que es lo único
    -- irrepetible de toda la tabla. Agregarla ahora es gratis; con datos dentro
    -- es una migración sobre la llave primaria.
    cuenta             text        not null,

    external_order_id  text        not null,
    odoo_order_id      bigint,
    odoo_name          text,

    -- FOTO del momento del registro, no espejo de Odoo. Si alguien cancela o
    -- factura la orden EN Odoo, esto NO se entera: solo se re-escribe cuando
    -- llega otro aviso del marketplace.
    estado             text,       -- 'draft' | 'sale' | 'cancel'. Sin CHECK, ídem.

    -- SIN CHECK, a propósito, contra la recomendación de los tres revisores.
    -- La 0033 documenta SIETE valores; un barrido del código encuentra al menos
    -- TRECE: apagado, cancelada, no_se_pudo_cancelar, simulado, sin_orden,
    -- sku_sin_producto, solo_registro_cancelar, ya_cancelada, ya_existia,
    -- creada, confirmada, solo_registro, error. Un CHECK armado desde el
    -- comentario RECHAZARÍA la mayoría.
    --
    -- Y el modo de fallo decide: `registrar` corre en el camino de una venta
    -- viva, dentro de un `except` que solo hace `log.warning`. Una fila
    -- rechazada NO avisa: se pierde, y con ella la foto de stock, que es lo
    -- único irrepetible. Para una bitácora, perder el renglón es peor que
    -- guardar un valor raro. La lista de arriba va como documentación, no como
    -- candado.
    accion             text        not null,

    -- La DECISIÓN del panel, no el estado de Odoo: `elegir_almacen` escogió
    -- éste por una razón, y el warehouse_id de la orden se puede editar después
    -- en Odoo. Por eso se guarda aunque Odoo lo sepa.
    almacen_id         integer,
    almacen            text,
    cobertura          text,       -- 'completa' | 'parcial'. Sin CHECK, ídem.

    guia               text,
    paqueteria         text,
    total              numeric(14,2),          -- 14,2 como channel.orders
    motivo             text,

    -- OJO: es cuándo se REGISTRÓ, no cuándo se vendió. En channel.orders la
    -- columna del mismo nombre significa lo otro.
    creado_at          timestamptz not null default now(),
    actualizado_at     timestamptz not null default now(),

    primary key (canal, cuenta, external_order_id)
);

comment on table ops.odoo_sale_orders is
    'Órdenes de venta creadas en Odoo desde una venta de marketplace. Una fila '
    'por venta, exista o no la orden en Odoo: accion=error|sku_sin_producto con '
    'odoo_order_id nulo describe un NO-evento, que es información que solo vive '
    'aquí porque a Odoo no se le puede preguntar por algo que nunca se creó. '
    'SIN FK hacia channel.orders a propósito: esa escritura vive en un '
    'try/except, y una FK dura perdería el renglón justo cuando falló la venta.';

comment on column ops.odoo_sale_orders.creado_at is
    'Cuándo se REGISTRÓ aquí, no cuándo se vendió. No confundir con '
    'channel.orders.creado_at, que sí es la fecha de la venta.';


create table if not exists ops.odoo_sale_order_items (
    canal              text        not null,
    cuenta             text        not null,
    external_order_id  text        not null,
    linea              integer     not null,

    -- `citext`, no `text`: así son core.products.sku, channel.order_items.sku y
    -- ops.stock_watch_photo.sku. Con `text`, 'abc-123' y 'ABC-123' se parten en
    -- dos filas y el join contra el catálogo falla — ya pasó, está documentado
    -- en 0021.
    sku                citext      not null,

    titulo             text,
    imagen             text,
    cantidad           integer     not null check (cantidad > 0),
    precio_unitario    numeric(14,2),

    -- ── LA FOTO. Lo único de toda la migración que no se puede re-preguntar.
    --
    -- jsonb llaveado por warehouse_id: {"135": 0, "150": 30}.
    --
    -- La 0033 usaba dos columnas, `stock_texco` y `stock_texco2`, y el escritor
    -- las llenaba buscando por NOMBRE DE PANTALLA: f.get("TEXCO II"). O sea que
    -- renombrar el almacén en Odoo hace que devuelva None y la foto se guarde
    -- en NULL — en silencio, y disfrazado del caso legítimo que el propio DDL
    -- documenta ("se registró antes de poder medirlos"). El warehouse_id es el
    -- identificador estable, y un tercer almacén deja de pedir ALTER TABLE.
    stock_libre        jsonb,

    -- Marca de tiempo propia: la 0033 no tenía ninguna en las líneas, así que
    -- el dato irrepetible no podía decir cuándo se tomó sin ir al padre.
    medido_at          timestamptz not null default now(),

    primary key (canal, cuenta, external_order_id, linea),

    -- La 0033 no llevaba FK ni siquiera hacia su propio encabezado, aunque su
    -- molde declarado (channel.order_items) sí la lleva. Sale gratis: `registrar`
    -- mete encabezado y líneas en el MISMO cursor, padre primero.
    foreign key (canal, cuenta, external_order_id)
        references ops.odoo_sale_orders (canal, cuenta, external_order_id)
        on delete cascade
);

comment on column ops.odoo_sale_order_items.stock_libre is
    'Libre por almacén en el INSTANTE de la venta, llaveado por warehouse_id: '
    '{"135": 0, "150": 30}. Congelado a propósito: refrescarlo lo vuelve '
    'inútil. NULL = no se pudo medir, que NO es lo mismo que "había cero".';


create index if not exists odoo_sale_orders_creado_idx
    on ops.odoo_sale_orders (creado_at desc);
create index if not exists odoo_sale_orders_canal_idx
    on ops.odoo_sale_orders (canal, creado_at desc);
-- Este NO estaba: "¿en cuántas ventas se despachó este SKU sin stock libre?"
-- es la consulta que le da valor a la foto.
create index if not exists odoo_sale_order_items_sku_idx
    on ops.odoo_sale_order_items (sku);

-- Función propia y NO `core.fn_touch_updated_at()`: esa escribe en
-- `updated_at`, columna que estas tablas no tienen — el trigger reventaría en
-- cada UPDATE. `channel` tuvo que resolver lo mismo con `tg_orders_touch`
-- (0002:35) por la misma razón. Se hace una en `ops` en vez de reusar la de
-- `channel` para no colgar el esquema de la operación de otro dominio.
create or replace function ops.fn_touch_actualizado_at() returns trigger
language plpgsql as $$
begin
    new.actualizado_at := now();
    return new;
end $$;

drop trigger if exists trg_touch_odoo_sale_orders on ops.odoo_sale_orders;
create trigger trg_touch_odoo_sale_orders before update on ops.odoo_sale_orders
    for each row execute function ops.fn_touch_actualizado_at();

alter table ops.odoo_sale_orders      enable row level security;
alter table ops.odoo_sale_order_items enable row level security;

commit;
