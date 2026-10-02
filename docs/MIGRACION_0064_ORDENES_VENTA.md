# Migración 0064 — lo que se le pide al chat de base de datos

> Para: quien aplica migraciones en kubera (Eduardo / chat de base de datos).
> De: el chat que construye **Inventario → Órdenes de venta** (ClickUp `86bcbfnkw`).
> **El SQL completo está al final de este documento** (sección «El SQL completo»).
> El archivo `supabase/migrations/0064_ops_ordenes_venta.sql` y el diseño
> (`docs/ORDENES_VENTA.md`) llegan a `main` junto con el código del módulo; este
> documento se subió ANTES y solo, para que se pueda revisar la base de datos
> sin esperar al resto.

**Estado: SIN APLICAR en kubera y en el sandbox.** Quien la escribió no la
aplicó ni la va a aplicar. Sólo se corrió —dos veces, para probar que es
idempotente— en un Postgres 16 **desechable y local**, con un esquema mínimo
de las tablas de kubera que el módulo lee.

## Qué se pide, en una línea

Crear en el esquema `ops` **seis tablas nuevas, una vista y un bucket privado
de Storage**. No se altera, no se borra y no se le agrega nada a ninguna tabla
existente.

## Los objetos

| Objeto | Para qué | Filas esperadas |
|---|---|---|
| `ops.ov_folio` | Contador del folio `OV-00001…`. **Una sola fila** (`id = 1`). Tabla y no `SEQUENCE` porque el folio debe ser consecutivo: una secuencia deja huecos en cada rollback. | 1 |
| `ops.ov_ordenes` | Encabezado de la orden: estado, cliente, canal, **orden de marketplace** (`mp_canal`, `mp_cuenta`, `mp_orden`), descripción, precio de la venta (`total`, `comision`), y quién/cuándo de cada paso (creó, confirmó, entregó, canceló, borró). `rev` es el candado optimista. | decenas al día |
| `ops.ov_lineas` | Renglones: SKU, piezas, precio unitario, piezas reservadas. FK a `ov_ordenes` con `on delete cascade`. | 1–5 por orden |
| `ops.ov_stock` | **La reserva propia por SKU** (la que vive en Supabase y no en Odoo). `reservado` = suma de lo apartado por órdenes confirmadas. | una por SKU que alguna vez se reservó |
| `ops.ov_mensajes` | Chat y bitácora por orden (mensajes del sistema y de personas). Sólo se agrega. | ~5 por orden |
| `ops.ov_archivos` | Índice de los PDF de cada orden. El binario va al bucket. | 0–2 por orden |
| `ops.ov_stock_base_v` (vista) | **Contra qué se reserva.** Hoy: `select sku, stock_odoo as libre, actualizado as medido_at from ops.stock_watch_photo`. Con `security_invoker = on`. | — |
| bucket `ordenes-venta` | PDF de las órdenes. **Privado**, 15 MB, sólo `application/pdf`, sin políticas en `storage.objects`. | — |

## De qué depende (ya existe en producción)

- `ops.stock_watch_photo` (la vista la lee). Verificado en producción el
  2-oct-2026: 14,770 filas, se resella cada ~20 min.
- La extensión `citext` (ya instalada) y el rol `service_role`.
- `storage.buckets` (se inserta una fila, igual que hizo la `0055` con `packing-lists`).

No depende de `core.products`: `ops.ov_lineas.sku` **no lleva llave foránea a
propósito**, igual que `channel.order_items.sku` — una venta de marketplace
puede traer un SKU que el catálogo todavía no conoce y la orden tiene que
poder existir para que alguien lo vea.

## Lo que NO hace

- No toca `channel.*`, `core.*`, `costing.*` ni las tablas `ops.*` que ya existen.
- No crea triggers, funciones, secuencias, políticas, ni publica nada en Realtime.
- No agrega filas a `core.channels`.
- No usa `ops.automatizacion_flags` en el DDL (el código escribirá ahí la fila
  `ov_generacion_auto` cuando un admin mueva ese interruptor; la tabla ya la admite).

## Candados (el patrón de la casa)

- `enable row level security` en las seis tablas, **sin políticas**, y
  `grant all … to service_role`. La vista, `security_invoker = on` y `grant select`.
- `python backend/scripts/verificar_rls.py` → `OK` (63 migraciones · 74 tablas · 18 vistas).
- Idempotente: `create table if not exists`, `create index if not exists`,
  `create or replace view`, `insert … on conflict`. Envuelta en `begin; … commit;`.

## Cosas que conviene que revises antes de aplicarla

1. **El número.** `0064` es el siguiente libre en `origin/main` (la última es
   `0063`; la `0062` está reservada por otro frente). Si ya tienes una `0064`
   en vuelo, se renumera el archivo: nada en el código depende del número.
2. **La vista como costura.** `ops.ov_stock_base_v` existe para que, cuando
   esté listo el **espejo de inventario en vivo de Odoo**, sólo haya que
   redefinirla (`sku`, `libre`, `medido_at`) y el código no cambie. Si ese
   espejo ya tiene tabla y nombre, dime cuál y la apunto ahí desde el inicio.
3. **`libre` es el `free_qty` TOTAL de Odoo, sin almacén.** Hoy no hay stock por
   almacén en Supabase; por eso `ops.ov_ordenes.almacen` es sólo un texto
   informativo. Si el espejo trae stock por almacén, la reserva por almacén
   sería una `0065`.
4. **PII.** Ninguna columna guarda datos del comprador (`cliente` es el canal o
   la razón social). Pero un PDF de guía sí trae la dirección: por eso el bucket
   es privado y sin políticas. Cuánto tiempo se conservan esos PDF está por
   decidirse (hoy no caducan solos).
5. **Sandbox.** Para probar con datos reales hace falta aplicarla también ahí.

## Cómo verificar que quedó

```sql
-- 1. Las seis tablas con RLS y la vista blindada
select c.relkind, c.relname, c.relrowsecurity, c.reloptions
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'ops' and c.relname like 'ov\_%' and c.relkind in ('r', 'v')
 order by 1, 2;
-- Esperado: 6 filas 'r' con relrowsecurity = t, y ov_stock_base_v con {security_invoker=on}

-- 2. El contador y el bucket
select * from ops.ov_folio;                                   -- 1 | 0
select id, public, file_size_limit, allowed_mime_types
  from storage.buckets where id = 'ordenes-venta';            -- ordenes-venta | f | 15728640 | {application/pdf}

-- 3. La vista responde
select count(*), max(medido_at) from ops.ov_stock_base_v;
```

## Cómo se deshace (si hiciera falta)

Nada más depende de estos objetos, así que la reversa es limpia. Con la
migración sin aplicar el módulo sólo dice «falta la migración 0064».

```sql
begin;
drop view  if exists ops.ov_stock_base_v;
drop table if exists ops.ov_archivos;
drop table if exists ops.ov_mensajes;
drop table if exists ops.ov_lineas;
drop table if exists ops.ov_stock;
drop table if exists ops.ov_ordenes;
drop table if exists ops.ov_folio;
commit;
-- El bucket se borra desde Storage (vacío): delete from storage.buckets where id = 'ordenes-venta';
```

## Lo que ya se probó (Postgres 16 local, 2-oct-2026)

- La migración corre dos veces sin error.
- Folio consecutivo: un alta que falla (CHECK) no consume folio.
- Confirmar reserva lo que hay: 7 de 10 piezas cuando dos SKUs no tienen foto
  de stock (`libre` NULL o sin fila → reserva 0, falla cerrado).
- **Concurrencia:** 40 órdenes confirmadas a la vez por 12 conexiones sobre dos
  SKUs con 50 y 30 piezas libres → reservado exacto 50 y 30, cero errores, y el
  invariante `ov_stock.reservado = suma de ov_lineas.reservado` se sostiene.
- Doble clic: seis confirmaciones simultáneas de la MISMA orden → una gana, las
  otras cinco no hacen nada.
- Cancelar suelta exactamente lo reservado.

Producción es Postgres 17.6; no se usa nada específico de 16 ni de 17.

## El SQL completo

Versión del 2-oct-2026. Si cambia antes de aplicarse, se actualiza aquí mismo.

```sql
-- ═══════════════════════════════════════════════════════════════════════════
-- 0064 — ops.ov_*: las ÓRDENES DE VENTA PROPIAS del panel (Inventario →
--        Órdenes de venta). El documento que hasta hoy sólo existía en Odoo.
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Estado: SIN APLICAR. La aplica Eduardo. Mientras no exista, la pestaña lo
-- dice («falta la migración 0064») y no truena; nada más depende de ella. El
-- módulo además nace apagado (ORDENES_VENTA_ENABLED=false): con la variable
-- apagada sólo se pueden crear BORRADORES de prueba, sin confirmar ni reservar.
--
-- POR QUÉ HACE FALTA (Brandon, 2-oct-2026, ClickUp 86bcbfnkw). La orden de
-- venta —lo que le dice al almacén qué surtir— vive en Odoo. El panel sólo la
-- MANDA allá (services/odoo_ventas.py) y guarda una bitácora de lo que mandó
-- (ops.odoo_sale_orders). Para dejar de depender de Odoo hace falta el
-- documento propio: folio OV-00001, borrador → confirmada (reserva stock) →
-- entregada a la paquetería (DELIVERED), con su cancelación, su chat de
-- movimientos, su PDF y quién hizo cada cosa.
--
-- QUÉ CREA
--   ops.ov_folio      contador del folio (una fila). Sin huecos: se incrementa
--                     en la MISMA sentencia que inserta la orden.
--   ops.ov_ordenes    el encabezado: estado, cliente, canal, ORDEN DE
--                     MARKETPLACE, descripción, precio de la venta, y quién y
--                     cuándo de cada paso.
--   ops.ov_lineas     los renglones: SKU, piezas, precio, piezas reservadas.
--   ops.ov_stock      la RESERVA PROPIA por SKU (la que no vive en Odoo).
--   ops.ov_mensajes   el chat/bitácora por orden (sistema y personas).
--   ops.ov_archivos   índice de los PDF de cada orden (bucket `ordenes-venta`).
--   ops.ov_stock_base_v   CONTRA QUÉ se reserva: hoy, la foto del libre de Odoo
--                     que ya vive en Supabase (ops.stock_watch_photo). Es una
--                     VISTA a propósito: cuando exista el espejo de inventario
--                     en vivo (tarea de Eduardo) se redefine la vista y el
--                     código no cambia.
--
-- CÓMO SE USA (y por qué así)
-- · CADA transición es UNA sola sentencia SQL (CTE con escrituras): el estado,
--   la reserva, los renglones y el mensaje de bitácora viajan juntos. No es
--   gusto: el pool (SteadyDB) re-ejecuta la sentencia que falló en OTRA
--   conexión a media transacción, y una transición repartida en varias
--   sentencias podía confirmar media orden.
-- · `rev` es el candado optimista: toda escritura exige la `rev` que leyó y la
--   sube en uno. Dos personas (o el doble clic) sobre la misma orden: la
--   segunda no hace nada y se le dice que recargue.
-- · La reserva es aritmética sobre ops.ov_stock con bloqueo de fila: dos
--   confirmaciones simultáneas del mismo SKU no pueden reservar la misma pieza.
--   `otorgado` guarda cuánto concedió la ÚLTIMA sentencia a ese SKU (Postgres
--   17 no devuelve el valor viejo en RETURNING).
-- · Las órdenes NO se borran: `borrada_at/por/motivo` (sólo admin). El folio
--   no se recicla jamás.
--
-- SIN DATOS DEL COMPRADOR. `cliente` es el canal o la razón social («temu»,
-- «AMAZON»), igual que el partner fijo de Odoo. Ojo con el PDF: una guía trae
-- la dirección del comprador; por eso el bucket es PRIVADO, sin políticas, y
-- el archivo sólo sale por el backend con sesión.
--
-- UN SOLO ESCRITOR: services/ordenes_venta.py (y services/ov_auto.py, que
-- llama a sus funciones). Idempotente: se puede correr dos veces.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- 1 · Folio -------------------------------------------------------------------
-- Tabla contador y no SEQUENCE: una secuencia deja huecos en cada rollback, y
-- el folio de un documento que ve el almacén tiene que ser consecutivo.
create table if not exists ops.ov_folio (
    id      smallint not null default 1,
    ultimo  integer  not null default 0,
    constraint ov_folio_pkey primary key (id),
    constraint ov_folio_una_fila_chk check (id = 1),
    constraint ov_folio_ultimo_chk check (ultimo >= 0)
);
insert into ops.ov_folio (id, ultimo) values (1, 0) on conflict (id) do nothing;

comment on table ops.ov_folio is
  'Contador del folio de las órdenes de venta propias (OV-00001…). Una sola fila. Se incrementa '
  'en la misma sentencia que inserta la orden (services/ordenes_venta.crear): sin huecos.';

-- 2 · Encabezado ----------------------------------------------------------------
create table if not exists ops.ov_ordenes (
    id                bigint      generated always as identity,
    folio             text        not null,
    estado            text        not null default 'borrador',
    rev               integer     not null default 1,     -- candado optimista
    -- Contenido (lo esencial de un sale.order de Odoo) ----------------------
    cliente           text,                               -- «temu», «AMAZON», razón social. NUNCA el comprador.
    canal             text,                               -- temu | tiktok | mercado_libre | amazon | walmart | shein | directa | otro
    mp_canal          text,                               -- ORDEN DE MARKETPLACE: canal (como channel.orders.canal)
    mp_cuenta         text,                               --   cuenta (BEKURA, SANCORFASHION, TEMU…)
    mp_orden          text,                               --   id de la venta en el marketplace
    descripcion       text,
    almacen           text,                               -- TEXCO | TEXCO II | DROP OFF
    guia              text,
    paqueteria        text,
    fecha_venta       timestamptz,
    entrega_limite    timestamptz,                        -- «Entregar a la paquetería: …»
    -- Precio de la venta ---------------------------------------------------
    moneda            text          not null default 'MXN',
    total             numeric(14,2) not null default 0,
    comision          numeric(14,2) not null default 0,
    precio_origen     text          not null default 'manual',
    -- Devolución -------------------------------------------------------------
    -- (El estado de la RESERVA no se guarda: se deriva de los renglones —
    --  suma de `reservado` contra suma de `cantidad`—, así nunca se desfasa.)
    devolucion_estado text,
    -- Quién y cuándo ---------------------------------------------------------
    creado_at         timestamptz not null default now(),
    creado_por        text        not null,               -- correo | 'servicio' | 'automatico'
    creado_nombre     text,
    creado_via        text        not null default 'panel',
    confirmada_at     timestamptz,
    confirmada_por    text,
    confirmada_nombre text,
    entregada_at      timestamptz,
    entregada_por     text,
    entregada_nombre  text,
    cancelada_at      timestamptz,
    cancelada_por     text,
    cancelada_nombre  text,
    cancelada_origen  text,
    cancelada_motivo  text,
    borrada_at        timestamptz,
    borrada_por       text,
    borrada_nombre    text,
    borrada_motivo    text,
    actualizado_at    timestamptz not null default now(),
    clave             text,                               -- idempotencia del alta (anti doble clic)
    constraint ov_ordenes_pkey primary key (id),
    constraint ov_ordenes_folio_uq unique (folio),
    constraint ov_ordenes_folio_chk check (folio ~ '^OV-[0-9]{5,}$'),
    constraint ov_ordenes_estado_chk check (estado in (
        'borrador', 'confirmada', 'entregada', 'cancelada', 'entregada_cancelada')),
    constraint ov_ordenes_via_chk check (creado_via in ('panel', 'api', 'claude', 'automatico')),
    constraint ov_ordenes_precio_origen_chk check (precio_origen in ('manual', 'marketplace')),
    constraint ov_ordenes_cancelada_origen_chk check (
        cancelada_origen is null or cancelada_origen in ('manual', 'marketplace')),
    constraint ov_ordenes_devolucion_chk check (
        devolucion_estado is null or devolucion_estado in ('pendiente', 'recibida', 'cerrada')),
    constraint ov_ordenes_importes_chk check (total >= 0 and comision >= 0),
    constraint ov_ordenes_rev_chk check (rev >= 1)
);

-- Una venta de marketplace = UNA orden viva. Las canceladas y las borradas no
-- cuentan, para que un error se pueda rehacer sin pelearse con el índice.
create unique index if not exists ov_ordenes_mp_uq
    on ops.ov_ordenes (mp_canal, coalesce(mp_cuenta, ''), mp_orden)
    where mp_orden is not null and borrada_at is null and estado <> 'cancelada';
create unique index if not exists ov_ordenes_clave_uq
    on ops.ov_ordenes (clave) where clave is not null;
create index if not exists ov_ordenes_creado_ix
    on ops.ov_ordenes (creado_at desc);
create index if not exists ov_ordenes_estado_ix
    on ops.ov_ordenes (estado, creado_at desc) where borrada_at is null;
create index if not exists ov_ordenes_mp_orden_ix
    on ops.ov_ordenes (mp_orden) where mp_orden is not null;

comment on table ops.ov_ordenes is
  'Órdenes de venta PROPIAS del panel (folio OV-00001…). Estados: borrador → confirmada (reserva '
  'stock en ops.ov_stock) → entregada (DELIVERED: almacén la entregó a la paquetería); cancelada, o '
  'entregada_cancelada («DELIVERED but CANCELLED»: pide devolución). No se borran: borrada_at/por/'
  'motivo. Sin datos del comprador. Escritor único: services/ordenes_venta.py.';
comment on column ops.ov_ordenes.rev is
  'Candado optimista: toda escritura exige la rev que leyó y la sube en uno.';
comment on column ops.ov_ordenes.mp_orden is
  'ORDEN DE MARKETPLACE: el id de la venta en el canal (mismo valor que channel.orders.external_order_id). '
  'Con mp_canal y mp_cuenta es la llave para cachar la cancelación del marketplace.';
comment on column ops.ov_ordenes.creado_via is
  'Por dónde nació: panel (persona con sesión), api (X-API-Key), claude (X-API-Key + X-Origen: claude), '
  'automatico (el barrido de services/ov_auto.py).';
comment on column ops.ov_ordenes.devolucion_estado is
  'Sólo en entregada_cancelada: pendiente → recibida → cerrada. El proceso operativo de devoluciones '
  'está por definirse (ClickUp, Bodega); hoy sólo se escribe ''pendiente''.';

-- 3 · Renglones ------------------------------------------------------------------
-- `sku` SIN llave foránea a core.products, igual que channel.order_items: una
-- venta de marketplace puede traer un SKU que el catálogo todavía no conoce, y
-- la orden tiene que poder existir para que alguien lo vea.
create table if not exists ops.ov_lineas (
    id              bigint        generated always as identity,
    orden_id        bigint        not null,
    linea           integer       not null,
    sku             citext        not null,
    titulo          text,
    imagen          text,                                 -- URL http(s); nunca un data-URI
    cantidad        integer       not null,
    precio_unitario numeric(14,2) not null default 0,
    reservado       integer       not null default 0,
    constraint ov_lineas_pkey primary key (id),
    constraint ov_lineas_orden_fk foreign key (orden_id)
        references ops.ov_ordenes (id) on delete cascade,
    constraint ov_lineas_orden_sku_uq unique (orden_id, sku),
    constraint ov_lineas_cantidad_chk check (cantidad > 0),
    constraint ov_lineas_precio_chk check (precio_unitario >= 0),
    constraint ov_lineas_reservado_chk check (reservado >= 0 and reservado <= cantidad)
);
-- `linea` es sólo el ORDEN en pantalla. No es única a propósito: la edición
-- de un borrador hace upsert por (orden_id, sku) y puede reordenar, y una
-- unicidad inmediata sobre `linea` tronaría a media sentencia.
create index if not exists ov_lineas_sku_ix on ops.ov_lineas (sku);

comment on table ops.ov_lineas is
  'Renglones de la orden de venta propia. Un SKU por orden (las repetidas se suman al guardar). '
  'reservado = piezas que ESTA orden tiene apartadas en ops.ov_stock (0 en borrador y al entregar).';

-- 4 · Reserva propia por SKU -------------------------------------------------------
create table if not exists ops.ov_stock (
    sku            citext      not null,
    reservado      integer     not null default 0,
    otorgado       integer     not null default 0,
    actualizado_at timestamptz not null default now(),
    constraint ov_stock_pkey primary key (sku),
    constraint ov_stock_reservado_chk check (reservado >= 0),
    constraint ov_stock_otorgado_chk check (otorgado >= 0)
);

comment on table ops.ov_stock is
  'Piezas RESERVADAS por las órdenes de venta propias, por SKU. Es la reserva que vive en Supabase '
  'y no en Odoo. Invariante: reservado = suma de ops.ov_lineas.reservado de las órdenes confirmadas. '
  'Disponible para una orden nueva = ops.ov_stock_base_v.libre − reservado.';
comment on column ops.ov_stock.otorgado is
  'Cuántas piezas concedió la ÚLTIMA sentencia de reserva a este SKU. Dato de paso: lo lee la misma '
  'sentencia en su RETURNING para repartirlo a los renglones. No es un saldo.';

-- 5 · Contra qué se reserva ----------------------------------------------------------
-- HOY: el libre de Odoo (free_qty total, sin almacén) que stock_watch copia a
-- Supabase en cada pasada. `libre` NULL = «no se sabe», y nunca es un cero.
-- MAÑANA: el espejo de inventario en vivo. Se cambia esta vista y nada más.
create or replace view ops.ov_stock_base_v as
select f.sku,
       f.stock_odoo  as libre,
       f.actualizado as medido_at
  from ops.stock_watch_photo f;

-- `create or replace view` borra las opciones: hay que volver a blindarla.
alter view ops.ov_stock_base_v set (security_invoker = on);

comment on view ops.ov_stock_base_v is
  'Stock base contra el que reservan las órdenes de venta propias: sku, libre (NULL = no se sabe), '
  'medido_at. Hoy lee ops.stock_watch_photo.stock_odoo (free_qty total de Odoo). Punto único de '
  'cambio cuando exista el espejo de inventario en vivo.';

-- 6 · Chat / bitácora por orden --------------------------------------------------------
-- Sin ON DELETE CASCADE: la historia de una orden no se va con un DELETE suelto.
create table if not exists ops.ov_mensajes (
    id           bigint      generated always as identity,
    orden_id     bigint      not null,
    tipo         text        not null,
    evento       text,
    cuerpo       text        not null,
    datos        jsonb,
    autor        text        not null,                    -- correo | 'servicio' | 'automatico'
    autor_nombre text,
    via          text        not null default 'panel',
    creado_at    timestamptz not null default now(),
    constraint ov_mensajes_pkey primary key (id),
    constraint ov_mensajes_orden_fk foreign key (orden_id) references ops.ov_ordenes (id),
    constraint ov_mensajes_tipo_chk check (tipo in ('sistema', 'usuario')),
    constraint ov_mensajes_via_chk check (via in ('panel', 'api', 'claude', 'automatico')),
    constraint ov_mensajes_cuerpo_chk check (length(cuerpo) between 1 and 4000)
);
create index if not exists ov_mensajes_orden_ix on ops.ov_mensajes (orden_id, id);

comment on table ops.ov_mensajes is
  'Chat y bitácora de cada orden de venta propia. tipo=sistema: lo escribe la MISMA sentencia que '
  'cambia el estado (creada, confirmada con sus piezas, entregada, cancelada…). tipo=usuario: lo que '
  'escribe una persona. autor = quién (correo), via = por dónde. Sólo se agrega; no se edita.';

-- 7 · PDF por orden ------------------------------------------------------------------------
create table if not exists ops.ov_archivos (
    id             bigint      generated always as identity,
    orden_id       bigint      not null,
    nombre         text        not null,
    ruta           text        not null,                  -- <folio>/<sha256>.pdf dentro del bucket
    sha256         text        not null,
    bytes          bigint      not null,
    subido_at      timestamptz not null default now(),
    subido_por     text        not null,
    subido_nombre  text,
    borrado_at     timestamptz,
    borrado_por    text,
    constraint ov_archivos_pkey primary key (id),
    constraint ov_archivos_orden_fk foreign key (orden_id) references ops.ov_ordenes (id),
    constraint ov_archivos_sha_chk check (sha256 ~ '^[0-9a-f]{64}$'),
    constraint ov_archivos_bytes_chk check (bytes > 0)
);
create unique index if not exists ov_archivos_vivo_uq
    on ops.ov_archivos (orden_id, sha256) where borrado_at is null;
create index if not exists ov_archivos_orden_ix on ops.ov_archivos (orden_id);

comment on table ops.ov_archivos is
  'Índice de los PDF de cada orden de venta propia (guía, factura…). El binario vive en el bucket '
  'privado `ordenes-venta` de Storage. borrado_at = se quitó (y se borró del bucket). Cuánto tiempo '
  'se conservan está por definirse (ClickUp, Bodega): hoy no caducan solos.';

-- 8 · Bucket privado para los PDF -----------------------------------------------------------
-- 15 MB por archivo, sólo PDF. Privado y SIN políticas en storage.objects: una
-- guía trae la dirección del comprador. Sólo el backend (service_role) lo lee.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('ordenes-venta', 'ordenes-venta', false, 15728640, array['application/pdf'])
on conflict (id) do update
   set public             = false,
       file_size_limit    = excluded.file_size_limit,
       allowed_mime_types = excluded.allowed_mime_types;

-- 9 · El candado de siempre (0016, 0049, 0051, 0053, 0058, 0061) ----------------------------
-- RLS activa sin políticas y sólo el service_role.
alter table ops.ov_folio    enable row level security;
alter table ops.ov_ordenes  enable row level security;
alter table ops.ov_lineas   enable row level security;
alter table ops.ov_stock    enable row level security;
alter table ops.ov_mensajes enable row level security;
alter table ops.ov_archivos enable row level security;

grant all on ops.ov_folio    to service_role;
grant all on ops.ov_ordenes  to service_role;
grant all on ops.ov_lineas   to service_role;
grant all on ops.ov_stock    to service_role;
grant all on ops.ov_mensajes to service_role;
grant all on ops.ov_archivos to service_role;
grant select on ops.ov_stock_base_v to service_role;

commit;
```
