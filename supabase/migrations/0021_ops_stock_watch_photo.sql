-- ═══════════════════════════════════════════════════════════════════════════
-- 0021 — La FOTO del vigilante de inventario sale de MySQL.
--
-- Estado: NO APLICADA. Sandbox primero (aplicar_migraciones.py), producción
-- solo con el visto de Eduardo.
--
-- QUÉ MIGRA
-- ---------
-- `stock_watch_foto` (14,640 filas) del MySQL `u531713409_kubera_ml`. Es el
-- PASO 2 del plan (docs/PLAN_31_TABLAS.md) y la tabla MÁS CALIENTE que queda:
--
--   · La reescribe COMPLETA cada 20 min `services/stock_watch.py`.
--   · La lee cada 20 min ese mismo vigilante para calcular DELTAS, y con esos
--     deltas ESCRIBE STOCK EN WOO — `STOCK_WATCH_SOLO_REGISTRO=false` desde
--     hace días: mueve inventario real (70 `odoo_delta` y 22 `woo_cambio` en
--     las últimas 48 h).
--   · La lee cada 20 min `channel_mirror.sincronizar_drop()`, ÚNICA fuente del
--     canal `general` de `channel.listings` (13,103 publicaciones).
--
-- NO ES UN CACHÉ. Un caché perdido se vuelve a pedir; esta foto perdida o
-- detenida cambia lo que el vigilante le escribe a Woo. Por eso el consejo la
-- sacó del grupo de cachés y por eso va sola, con días de doble escritura y
-- comparación antes de apagar la vieja.
--
-- ⚠️ LA TRAMPA ESPECÍFICA DE ESTA TABLA (leer antes de tocarla)
-- -------------------------------------------------------------
-- El vigilante NO guarda un valor, guarda el ESTADO ANTERIOR. Su cuenta es
--
--     delta = odoo_ahora − odoo_en_la_foto        →  Woo += delta
--
-- Si la foto se queda CONGELADA pero legible, `odoo_en_la_foto` nunca avanza y
-- el mismo delta se vuelve a aplicar CADA PASADA: Woo sube (o baja) el mismo
-- número cada 20 minutos, para siempre. No es el error de los 964 pedidos
-- fantasma —que fue un `None` mal leído— es peor: es un error que se acumula.
--
-- Por eso el orden aquí es al revés que en el paso 1: primero la ESCRITURA a
-- los dos lados, después días de comparación, y solo al final la LECTURA. La
-- foto vieja se apaga cuando ya no decide nada.
--
-- Y por eso el `except → foto vacía` de `stock_watch._foto()` se arregla en el
-- mismo commit: devolver vacío ahí no es "no hay foto", es "no sé", y el
-- vigilante lo interpretaba como "primera pasada" — que ABSORBE en la foto todo
-- lo pendiente sin aplicarlo. Un parpadeo de la base tiraba a la basura los
-- deltas de Odoo y los cambios de Woo de esa vuelta, en silencio.
--
-- POR QUÉ EN `ops` Y NO EN `channel`
-- -----------------------------------
-- `channel.*` es el estado del canal tal como el canal lo reporta. Esto no es
-- un canal: es la MEMORIA DE UN PROCESO, hermana de `ops.process_log` y
-- `ops.task_queue`. Ponerla en `channel` invitaría a leerla como si fuera
-- inventario publicable, y no lo es — es "lo que vi la vez pasada".
--
-- El inventario publicable derivado de esta foto ya vive en su lugar:
-- `channel.listings` canal `general`, que es lo que el espejo del DROP escribe
-- a partir de aquí.
--
-- NOTA PARA EL FUTURO (no se resuelve aquí)
-- -----------------------------------------
-- `odoo_watch.py` guarda SU foto de Odoo en `productos.stock_odoo` (MySQL), la
-- otra razón por la que `productos` no está del todo congelada — pendiente 1b
-- de F8 en CLAUDE.md. Esta tabla ya tiene la columna `stock_odoo` y cubre
-- 13,039 SKUs contra los 4,786 de aquélla. Cuando toque decidir si a ese
-- vigilante se le da casa o se apaga, la casa ya existe y es ésta. No se hace
-- ahora para no mezclar dos flujos vivos en un mismo cambio.
-- ═══════════════════════════════════════════════════════════════════════════

-- `citext` y no `text` NO es cosmética: la PK de MySQL usa la colación
-- `utf8mb4_uca1400_ai_ci`, que es CASE-INSENSITIVE. Con `text` una foto que
-- allá era UNA fila (`abc-123` pisando a `ABC-123`) se partiría en DOS, y la
-- primera pasada después del cambio vería un delta que no existió. Es además
-- el tipo que ya usan `core.products.sku` y las de `costing`.
create table if not exists ops.stock_watch_photo (
    sku          citext      not null primary key,
    -- Las dos pueden ser NULL y significan cosas DISTINTAS de un 0:
    --   stock_woo NULL  → Woo no gestiona stock de ese SKU (no es "cero").
    --   stock_odoo NULL → Odoo no lo conoce.
    -- El espejo del DROP se salta los `stock_woo` nulos a propósito: publicar
    -- un 0 inventado es peor que no decir nada.
    stock_woo    integer,
    stock_odoo   integer,
    -- CUÁNDO SE MIRÓ, no cuándo cambió. Se reescribe en CADA pasada aunque el
    -- número no se mueva — igual que la `actualizado` de MySQL, y a propósito.
    -- Convertirla en "cuándo cambió" (con un `where ... is distinct from`)
    -- ahorraría escrituras pero rompería la única señal de que el vigilante
    -- sigue vivo. Ya nos pasó con `channel.listings.updated_at`: esa sí es
    -- "cuándo cambió", y leerla como "cuándo se visitó" produjo tres
    -- diagnósticos equivocados seguidos.
    actualizado  timestamptz not null default now()
);

comment on table ops.stock_watch_photo is
  'Foto anterior de stock (Woo y Odoo) por SKU: la memoria de services/'
  'stock_watch.py, que calcula DELTAS contra ella y con eso ESCRIBE STOCK EN '
  'WOO. No es un caché ni inventario publicable. Si se congela, el mismo delta '
  'se re-aplica cada 20 min y el error se acumula. Migrada de MySQL '
  'stock_watch_foto (14,640 filas) el 14-ago-2026.';

comment on column ops.stock_watch_photo.actualizado is
  'Cuándo se MIRÓ (cada pasada), no cuándo cambió. Es la señal de vida del '
  'vigilante: si deja de avanzar, el vigilante está detenido.';

-- El espejo del DROP barre por "los que tienen stock de Woo".
create index if not exists ix_stock_watch_photo_woo
    on ops.stock_watch_photo (sku) where stock_woo is not null;

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
alter table ops.stock_watch_photo              enable row level security;
