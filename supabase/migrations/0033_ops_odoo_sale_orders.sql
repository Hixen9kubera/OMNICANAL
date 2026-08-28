-- 0033_ops_odoo_sale_orders.sql — La bitácora de las ÓRDENES DE VENTA que el
-- panel crea en Odoo a partir de una venta de TikTok/Temu.
--
-- POR QUÉ UNA TABLA Y NO UNA CONSULTA A ODOO
-- -------------------------------------------
-- Casi todo lo que pinta la pantalla se podría preguntar a Odoo en vivo: el
-- nombre de la orden, su estado, sus líneas. **Una sola cosa no**, y es la que
-- justifica la tabla:
--
--     el STOCK QUE HABÍA EN EL MOMENTO EXACTO DE LA VENTA
--
-- Ese número es irrepetible. `free_qty` cambia con cada venta, cada recepción y
-- cada reserva; veinte minutos después ya no se puede reconstruir, y a los tres
-- días no queda ni rastro de cuánto había cuando el comprador apretó el botón.
-- Es justo el dato con el que se contesta "¿por qué se sobrevendió?" — y sin
-- foto, esa pregunta no tiene respuesta posible.
--
-- Lo mismo, en menor grado, con la GUÍA: TikTok la asigna al generar la etiqueta
-- y la reescribe si se cancela y se rehace. Guardarla aquí conserva cuál fue la
-- de ESTA venta.
--
-- LA FOTO SE ESCRIBE UNA VEZ Y NO SE RE-TOCA
-- -------------------------------------------
-- `stock_texco`/`stock_texco2` NO se actualizan nunca en el UPSERT. Es la misma
-- regla que ya protege `total` y `comision` en `channel.orders`: un dato
-- histórico congelado deja de servir en cuanto alguien lo "refresca" con el
-- valor de hoy. Si un día aparecen en NULL, es que la venta se registró antes
-- de poder medirlos — que es distinto de "había cero".
--
-- DOS TABLAS, NO UNA
-- -------------------
-- El encabezado es por VENTA; el stock y la imagen son por SKU. Una venta de
-- TikTok con dos productos tiene dos fotos de inventario distintas, y aplanarlas
-- en el encabezado obligaría a inventar cuál de las dos "es" la de la orden.
-- Mismo molde que `channel.orders` / `channel.order_items`.
--
-- LA LLAVE ES (canal, external_order_id)
-- ---------------------------------------
-- La misma de `channel.orders`, a propósito: así la pantalla puede unir la
-- orden de Odoo con la venta y el pedido de Woo sin inventar un id nuevo. Y el
-- UNIQUE es el candado de idempotencia del lado nuestro — el del lado de Odoo
-- es `client_order_ref`, que no tiene restricción y por eso no basta solo.

create table if not exists ops.odoo_sale_orders (
    canal              text        not null,          -- tiktok | temu
    external_order_id  text        not null,          -- la venta en el marketplace
    odoo_order_id      bigint,                        -- sale.order.id (null si no se creó)
    odoo_name          text,                          -- S36856 — "el nombre de la orden"
    estado             text,                          -- draft | sale | cancel
    accion             text        not null,           -- solo_registro | creada | confirmada
                                                       -- | cancelada | ya_existia | error
                                                       -- | sku_sin_producto
    almacen_id         integer,                       -- 135 TEXCO · 150 TEXCO II
    almacen            text,
    cobertura          text,                          -- completa | parcial  ← sobreventa a la vista
    guia               text,                          -- número de guía del marketplace
    paqueteria         text,
    total              numeric(12,2),
    motivo             text,                          -- por qué NO se creó, cuando aplica
    creado_at          timestamptz not null default now(),
    actualizado_at     timestamptz not null default now(),
    primary key (canal, external_order_id)
);

comment on table  ops.odoo_sale_orders is
    'Órdenes de venta creadas en Odoo desde una venta de marketplace. La foto de stock vive en las líneas y NO se re-toca.';
comment on column ops.odoo_sale_orders.cobertura is
    'completa = un almacén cubría toda la orden. parcial = ninguno la cubría; la reserva no va a ocurrir y eso es sobreventa esperando.';

create table if not exists ops.odoo_sale_order_items (
    canal              text        not null,
    external_order_id  text        not null,
    linea              integer     not null,
    sku                text        not null,
    titulo             text,
    imagen             text,                          -- la imagen que vio el comprador
    cantidad           integer     not null,
    precio_unitario    numeric(12,2),
    -- LA FOTO. Se escribe una vez, al crear, y no se actualiza jamás.
    stock_texco        numeric(12,2),
    stock_texco2       numeric(12,2),
    primary key (canal, external_order_id, linea)
);

comment on column ops.odoo_sale_order_items.stock_texco is
    'Libre en TEXCO en el INSTANTE de la venta. Congelado a propósito: refrescarlo lo vuelve inútil.';

-- La pantalla lista por fecha descendente y filtra por canal; sin esto, cada
-- carga del tab hace un seq scan que crece con las ventas.
create index if not exists odoo_sale_orders_creado_idx
    on ops.odoo_sale_orders (creado_at desc);
create index if not exists odoo_sale_orders_canal_idx
    on ops.odoo_sale_orders (canal, creado_at desc);


-- ───────────────────────────────────────────────────────────────────────────
-- EL INTERRUPTOR, y por qué NO puede vivir en una variable de Railway
-- ───────────────────────────────────────────────────────────────────────────
-- El panel necesita un switch para apagar la automatización SIN esperar a nadie.
-- Las banderas `ODOO_VENTAS_*` de Railway no sirven para eso por dos razones:
--
--   1. Cambiar una variable en Railway REINICIA el contenedor. Un apagado de
--      emergencia no puede costar un reinicio de todo el backend.
--   2. Un apagado que solo viva en memoria del proceso se DESHACE SOLO en el
--      siguiente deploy, que es la peor propiedad imaginable para un botón de
--      pánico: alguien lo apaga, se va tranquilo, y horas después vuelve solo.
--
-- Por eso el estado se persiste aquí. La variable de entorno queda como VALOR
-- POR OMISIÓN —lo que aplica mientras nadie haya tocado el switch— y esta tabla
-- manda en cuanto alguien lo toca. Es el mismo reparto que ya usa el proyecto
-- entre "lo que trae el .env" y "lo que decidió una persona".
--
-- `actualizado_por` no es adorno: un flujo que mueve inventario y contabilidad
-- tiene que poder contestar quién lo apagó y cuándo.
create table if not exists ops.automatizacion_flags (
    flag            text        primary key,   -- p.ej. 'odoo_ventas_enabled'
    valor           boolean     not null,
    motivo          text,                      -- por qué se apagó, si se apagó
    actualizado_at  timestamptz not null default now(),
    actualizado_por text
);

comment on table ops.automatizacion_flags is
    'Interruptores de la pestaña Automatización. Mandan sobre las variables de entorno, que quedan como valor por omisión. Persistido a propósito: un apagado no puede deshacerse solo en el siguiente deploy.';


-- RLS ACTIVA Y SIN POLÍTICAS, que es el patrón de la casa desde 0001 (ver
-- 0028_ops_fanout_log.sql:85). Con RLS encendida y cero políticas, solo pasa
-- quien hace bypass —`service_role` y `postgres`—, que es justo como se conecta
-- el backend: los crons y la app no se enteran, y nada más entra.
--
-- Sin esto, `backend/scripts/verificar_rls.py` sale en rojo y el workflow
-- `blindaje-bd.yml` marca el push. Y el barrido del bloque D de 0025 NO las
-- salva: ese `do $$` blinda lo que YA existe y corre antes que esta migración.
alter table ops.odoo_sale_orders      enable row level security;
alter table ops.odoo_sale_order_items enable row level security;
alter table ops.automatizacion_flags  enable row level security;
