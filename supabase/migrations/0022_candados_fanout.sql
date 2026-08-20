-- ═══════════════════════════════════════════════════════════════════════════
-- 0022 — Los tres estados que vivían dentro de una BITÁCORA salen de ahí.
--
-- Estado: NO APLICADA a producción. Sandbox primero; producción solo con el
-- visto de Eduardo y tras la verificación descrita en docs/PASO_0_CANDADOS.md.
--
-- QUÉ MIGRA
-- ---------
-- `fanout_log` (MySQL) es un DIARIO: se anota lo que pasó. Pero tres decisiones
-- del sistema le preguntan cosas como "¿esto ya lo hice?", o sea la usan como
-- LIBRETA DE CONTROL. De sus 7,860 filas, lo que es estado son 23 — más 99
-- números que viven dentro de una frase escrita.
--
-- Los tres NO comparten solución, y eso es el punto de esta migración. Meterlos
-- juntos "porque una tabla ya existe y es cómoda" es exactamente el error que
-- los puso en un diario.
--
--   compensación por pedido      → columna en `channel.orders`
--   operación de bodega aplicada → `ops.fulfillment_operations`
--   marca de agua FBA por SKU    → `ops.fba_watermark`
--
-- ⚠️ POR QUÉ IMPORTA (no es limpieza cosmética)
-- ---------------------------------------------
-- Los dos candados que leen esto terminan en `except: return False`. Cuando la
-- base falla no contestan "no sé": contestan "no lo hice", y el sistema lo hace
-- OTRA VEZ. Es el mismo mecanismo de los 964 pedidos fantasma del 12-ago.
--
-- Y hay un agravante: `stock_full._ya_procesada` llama a
-- `fanout_stock._asegurar_schema()` ANTES de leer, donde vive un
-- `CREATE TABLE IF NOT EXISTS fanout_log`. O sea que borrar la tabla no apaga
-- el candado: hace que la recree VACÍA y apruebe todo.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1) Compensación de stock, por pedido ───────────────────────────────────
-- Un pedido FULL/FBA no debería descontar de nuestra bodega, pero Woo lo
-- descuenta igual (la meta `_order_stock_reduced` la FILTRA la REST — hallazgo
-- del 28-jul). Se compensa devolviendo lo que Woo descontó.
--
-- VERIFICADO 14-ago y es lo que hace peligroso al candado: compensar **no
-- borra** `_reduced_stock` en Woo. Esa meta solo la borra Woo cuando repone por
-- su cuenta al cancelar. Así que una segunda compensación leería exactamente lo
-- mismo y devolvería las piezas otra vez. No hay segunda red.
--
-- Va como COLUMNA y no como tabla: es estado de ESE pedido, y el pedido ya vive
-- aquí. Timestamp y no boolean — "cuándo" contesta "si" y además sirve para
-- auditar; un `true` sin fecha no se puede reconciliar contra `fanout_log`.
-- DOS columnas y no una: la compensación NO es un sí/no.
--
-- El código escribe TRES acciones distintas (`full_compensado`,
-- `full_compensado_error`, `full_compensado_revertido`, pedidos_ml.py:378-379)
-- y `_ya_compensado` solo mira la primera. Con una columna booleana —o un solo
-- timestamp— se heredaría el bug con checksum: tras una REVERSIÓN el candado
-- seguiría diciendo "ya compensado" y la próxima compensación legítima no
-- ocurriría. Lo marcó el consejo (opus) y tiene razón.
alter table channel.orders
    add column if not exists stock_compensado_at timestamptz,
    add column if not exists stock_revertido_at  timestamptz;

comment on column channel.orders.stock_compensado_at is
  'Cuándo se le devolvieron a Woo las piezas que descontó de este pedido '
  'FULL/FBA. NULL = nunca se compensó. Candado de pedidos_ml._ya_compensado, '
  'que antes vivía en fanout_log (accion=full_compensado).';

comment on column channel.orders.stock_revertido_at is
  'Cuándo se DESHIZO esa compensación (el pedido se canceló y Woo repuso por '
  'su cuenta, así que hubo que volver a restar). Compensado vale solo si es '
  'posterior a la última reversión — por eso son dos columnas y no un boolean.';

-- ⚠️ DEUDA QUE ESTA MIGRACIÓN **NO** RESUELVE, y que hay que decir en voz alta:
-- una compensación PARCIAL (unas líneas devueltas, otras con error) hoy cuenta
-- como hecha, así que las líneas fallidas NO se reintentan nunca. Ese defecto
-- YA EXISTE en `fanout_log` y se hereda tal cual: mudarlo de casa no lo cura y
-- curarlo aquí sería cambiar el comportamiento del flujo de pedidos dentro de
-- un paso que es de mudanza. Queda anotado como pendiente propio.

-- NO se crea índice por `wc_order_id`, y la razón cambió con v0.176.0:
-- desde el RECLAMO, `wc_order_id` es NULL a propósito mientras el pedido está
-- reclamado y todavía no creado. Buscar la compensación por esa columna fallaría
-- justo en los casos revueltos (relevo de contenedores, reintento de ML). El
-- candado busca por la PK `(canal, cuenta, external_order_id)`, que `sincronizar`
-- tiene en mano y que NUNCA es nula. Menos superficie y más correcto.

-- ── 2) Operaciones de bodega ya aplicadas ──────────────────────────────────
-- Movimientos REALES de mercancía en el almacén de ML / Amazon: `full_ingreso`,
-- `full_retiro`, `fba_ingreso`. Su unidad es la OPERACIÓN, que no tiene pedido
-- al que pegarse — por eso tabla propia y no una columna en ningún lado.
--
-- REGLA QUE SE HEREDA Y NO SE PIERDE: aquí solo se inserta cuando la operación
-- se APLICÓ DE VERDAD. En `fanout_log` esa distinción dependía de que el texto
-- del campo `resultado` no empezara con 'ERROR' — un filtro que existe porque
-- antes bastaba cualquier fila `full_%` y un 502 del WAF de Hostinger sellaba
-- el movimiento para siempre (auditoría 27-jul). Con una tabla de aplicadas, la
-- distinción deja de depender de parsear texto: si falló, no hay fila, y ML
-- puede volver a avisar.
create table if not exists ops.fulfillment_operations (
    operacion_id  text        not null primary key,
    sku           citext,
    cuenta        text,
    accion        text        not null,
    aplicada_at   timestamptz not null default now()
);

comment on table ops.fulfillment_operations is
  'Operaciones de bodega FULL/FBA ya APLICADAS (candado de stock_full.'
  '_ya_procesada). Una fila aquí = el movimiento se hizo. Un intento fallido '
  'NO deja fila, para que ML pueda volver a avisarlo. Migrada de MySQL '
  'fanout_log (17 filas) el 14-ago-2026.';

-- ── 3) Marca de agua del FBA, por SKU ──────────────────────────────────────
-- ⚠️ ESTA ES LA QUE EL PLAN ORIGINAL TENÍA MAL, y la corrección es medida.
--
-- El plan decía: "el nivel de FBA se parsea del texto de `resultado`; ya existe
-- la fuente buena: `channel_read.stock_fba_amazon()`". Medido el 14-ago sobre
-- los 99 SKUs con marca: **96 dan un valor DISTINTO** al `stock_fba` de
-- `channel.listings`.
--
-- No es desfase, son cosas distintas:
--   · la marca de agua = "cuánto vi la última vez que PROCESÉ UN EVENTO de este
--     SKU" (viene de la API de operaciones de ML, en el momento del aviso)
--   · `channel.listings.stock_fba` = "cuánto vio el SYNC hace ≤15 min"
--
-- El comentario del propio `stock_full.py` explica por qué: el sync refresca
-- cada 15 min, así que usarlo como referencia hacía que el MISMO ingreso se
-- contara dos veces. Cambiar el regex por el valor del sync no es limpiar: es
-- volver a meter el bug que ese código ya arregló.
--
-- El defecto nunca fue de dónde sale el número. Es que vive dentro de una frase
-- ("FBA subió 0→24 (+24): SOLO-REGISTRO…") y se recupera con `→\s*(\d+)`. Basta
-- que alguien cambie el texto del mensaje para que la marca desaparezca en
-- silencio. Lo que necesita es COLUMNA PROPIA.
create table if not exists ops.fba_watermark (
    sku        citext      not null primary key,
    stock_fba  integer     not null,
    cuenta     text,
    visto_at   timestamptz not null default now()
);

comment on table ops.fba_watermark is
  'Último nivel de FBA que el vigilante de stock_full observó por SKU, con el '
  'que calcula el delta del siguiente aviso. NO es channel.listings.stock_fba '
  '(medido: 96 de 99 difieren) — aquélla es lo que vio el sync, ésta es lo que '
  'vio el vigilante al procesar un evento. Usar la del sync hacía contar dos '
  'veces el mismo ingreso. Migrada de MySQL fanout_log (99 marcas que estaban '
  'dentro de texto libre) el 14-ago-2026.';

-- ── RLS: la tabla nace blindada ─────────────────────────────────────────────
-- Mismo patron que `0022_blindaje_rls.sql` (auditoria del 19-ago): RLS activada
-- y CERO politicas = deny-by-default de verdad. `service_role` tiene
-- `rolbypassrls`, asi que el backend y los crons no se enteran.
--
-- Va AQUI y no en una migracion de limpieza posterior por lo que enseño esa
-- auditoria: diez tablas habian nacido sin RLS y solo estaban contenidas por la
-- lista de esquemas que expone PostgREST — configuracion que vive FUERA del
-- esquema y se cambia con un clic. Tres de esas diez las cree yo. Una tabla que
-- nace blindada no depende de que alguien se acuerde despues.
alter table ops.fulfillment_operations         enable row level security;
alter table ops.fba_watermark                  enable row level security;
