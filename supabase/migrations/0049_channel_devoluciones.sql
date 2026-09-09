-- ═══════════════════════════════════════════════════════════════════════════
-- 0049 — DEVOLUCIONES (channel.returns / return_items / return_history)
--        v2.3 — CORRECCIÓN DE LA v2.2. 9-sep-2026.
--
-- ⚠️ ESTA VERSIÓN EXISTE PORQUE LA v2.1 NO SE PUEDE APLICAR.
--
-- La v2.1 se escribió el 18-ago. El 31-ago —trece días después, y sin que esa
-- propuesta estuviera commiteada— alguien creó A MANO en producción una tabla
-- `channel.returns` distinta y una vista `channel.returns_daily` distinta, las
-- pobló con 62 devoluciones de Mercado Libre y montó encima la pestaña viva
-- /analisis/rentabilidad → Devoluciones. Las dos ramas no se conocen.
--
-- QUÉ PASA SI SE APLICA LA v2.1 TAL CUAL (medido contra producción el 9-sep):
--
--   1. `create table if not exists channel.returns` → NO-OP. La tabla existe,
--      con otra llave primaria: (canal, cuenta, claim_id) en vez de
--      (canal, cuenta, external_return_id).
--   2. Acto seguido `create index ... on channel.returns (abierta_at desc)`
--      → ERROR: la columna `abierta_at` no existe. La migración ABORTA AQUÍ.
--   3. Si se saltara ese error, `channel.return_items` declara una FK contra
--      (canal, cuenta, external_return_id) → ERROR: esa columna tampoco existe.
--   4. Y `create or replace view channel.returns_daily` → ERROR de todos modos:
--      Postgres exige que la vista de reemplazo produzca las MISMAS columnas,
--      en el mismo orden y tipo (solo permite agregar al final). En la posición
--      7 la vista viva tiene `resolucion_motivo text` y la propuesta pone
--      `reintegro_woo boolean`.
--   5. Y si alguien forzara el reemplazo con un drop: la pestaña viva se cae en
--      el acto. `routers/fulfillment.py` lee de `returns_daily` cinco columnas
--      que la v2.1 no produce — `returns_count`, `value_returned`,
--      `venta_contaba`, `resolucion_motivo`, `estado_dinero`.
--
-- O sea: falla ruidosamente (bien) pero no avanza nada (mal).
--
-- QUÉ HACE ESTA v2.2
--   El diseño de la v2.1 GANA: cabecera + líneas + historia es el modelo
--   correcto y multicanal; la tabla del 31-ago es plana y solo sabe de ML (una
--   fila por claim, un solo SKU, sin historia de estados). Esta versión
--   conserva la v2.1 completa y le agrega tres cosas:
--
--   §0  PRÓLOGO DE RECONCILIACIÓN — aparta lo que hay para que la migración
--       pueda correr. Las 62 filas NO se borran: se conservan en
--       `channel.returns_ml_v0` como evidencia hasta que el backfill nuevo las
--       reponga (son 7 días de agosto y la API de ML las devuelve completas —
--       verificado el 8-sep: 771 devoluciones históricas disponibles).
--
--   §1b LO QUE LA TABLA DEL 31-AGO APRENDIÓ EN PRODUCCIÓN y la v2.1 no tiene:
--       `venta_contaba` y `estado_dinero`. No son adorno: sin la primera, el
--       KPI resta devoluciones de pedidos YA cancelados y descuenta dos veces
--       (medido: $14,734 de $49,182 en la ventana de agosto, el 30%).
--
--   §1c LAS CUATRO COLUMNAS DE `/claims/{id}/detail` — el motivo en prosa desde
--       que se abre el reclamo. Hoy solo hay motivo en las CERRADAS (19 de 62).
--
--   §5b returns_daily recupera las columnas que la pestaña ya muestra.
--
-- TODO LO DEMÁS ES LA v2.1 BYTE POR BYTE, incluidos sus comentarios: el
-- retiro del trigger sobre channel.orders, el prorrateo por devolución, el
-- tri-estado de reintegro_woo, el full outer join, y el security_invoker del
-- rescate del 9-sep.
--
-- NUMERACIÓN VERIFICADA el 9-sep contra origin/main: la última migración es
-- 0048_market_search_term_estado.sql. 0049 está libre. (Re-verificar con
-- `git fetch && git ls-tree --name-only origin/main supabase/migrations/ | tail -1`
-- antes de aplicar: ya hubo tres colisiones de número entre sesiones.)
--
-- ═══ QUÉ CORRIGE LA v2.3 ═══════════════════════════════════════════════════
-- La v2.2 se revisó contra el CÓDIGO y contra la BD antes de aplicarla. Su
-- diseño se sostiene; su ejecución tenía seis fallas, dos capaces de tirar la
-- pestaña viva en el mismo commit. Se corrigen aquí y NADA MÁS.
--
-- C1. §5b NO PRODUCÍA `resolucion_motivo`, y el backend la pide POR NOMBRE.
--     La v2.2 emitía `motivo`. No es degradación: `fulfillment.py` revienta con
--     `column "resolucion_motivo" does not exist`. Y el nombre no bastaba:
--     `motivo` es NULL "hasta tener el mapa", así que caía a `motivo_canal`,
--     que es un CÓDIGO ('PDD9963'). La prosa vive en `motivo_texto` (§1c).
--     Ahora sale de ahí, con caída al código. De paso: la v2.2 agregó
--     `estado_dinero` diciendo que el backend la lee. NO la lee. Se conserva
--     porque es dato bueno, pero su inventario estaba mal en ambos sentidos.
--
-- C2. EL BACKEND TAMBIÉN LEE LA TABLA, NO SOLO LA VISTA. A la v2.2 se le pasó
--     `fulfillment.py:3045`: `select canal, cuenta, piezas, valor,
--     resolucion_motivo, estado from channel.returns`. Ninguna de esas tres
--     existe en el modelo nuevo. Se agrega `channel.returns_cabecera` (§5e):
--     esa forma plana reconstruida, una fila por devolución con sus líneas
--     sumadas. El backend cambia una línea.
--     OJO: en la tabla del 31-ago `creado_at` era LA FECHA DEL CLAIM; en el
--     modelo nuevo es `default now()`, la hora de inserción. La fecha de
--     negocio ahora es `abierta_at`. Filtrar por `creado_at` cambia de
--     significado en silencio.
--
-- C3. `returns_count` CAMBIABA DE SEMÁNTICA SIN CAMBIAR DE NOMBRE. La tabla
--     vieja es plana —un claim = una fila = un SKU—, así que `count(*)` se
--     podía SUMAR entre SKUs. La v2.2 ponía `count(distinct
--     external_return_id)` agrupado por SKU: una devolución de 3 SKUs aporta 1
--     a cada uno de los 3 grupos, y el backend hace `sum(returns_count)` en
--     TRES consultas. El KPI se inflaba por el número medio de líneas. No
--     falla: miente. Ahora cada devolución la cuenta UNA sola de sus líneas
--     (la de `linea` mínima), así que la suma es exacta agrupada como sea y el
--     backend NO cambia.
--
-- C4. LOS ATRIBUTOS DE CABECERA IBAN COLAPSADOS CON `bool_or`/`max`.
--     `bool_or(venta_contaba)` da true si ALGUNA es true e ignora NULL: un
--     grupo que mezcla una devolución restable con una ya cancelada marcaba
--     todo restable — el doble conteo que §1b existe para evitar. `max()` sobre
--     texto elige por orden alfabético, no por relevancia. La vista VIEJA los
--     llevaba como CLAVES DE AGRUPACIÓN (ANEXO A, `group by 1..9`) y por eso
--     funcionaba. Se vuelve a eso.
--
-- C5. §0 NO ERA IDEMPOTENTE, pese a que la v2.2 lo afirmaba. En la segunda
--     corrida `channel.returns` ya es la tabla NUEVA y `returns_ml_v0` ya
--     existe: el rename aborta con "relation already exists". Y no es
--     hipotético: `aplicar_migraciones.py` NO lleva registro de lo aplicado —
--     corre `sorted(glob("*.sql"))` completo cada vez y hace `sys.exit` al
--     primer fallo, así que la segunda corrida tumbaba esta migración Y TODAS
--     LAS POSTERIORES. Ahora el §0 va dentro de un guard que mira si
--     `channel.returns` todavía tiene `claim_id`, la firma de la tabla del
--     31-ago. Si no la tiene, no hay nada que apartar y se salta entero.
--
-- C6. LA REVERSA NO CORRÍA. `drop table channel.return_items` sin `cascade`
--     falla: `returns_daily` y `returns_refunds_daily` dependen de ella. Se
--     reordena, vistas primero.
--
-- Y las vistas del §5 se TIRAN antes de recrearse: `create or replace view`
-- exige la misma lista de columnas, así que sin el drop este archivo no se
-- puede volver a correr después de editarlo.
--
-- Idempotente de verdad: verificado corriéndolo DOS VECES seguidas.
-- ═══════════════════════════════════════════════════════════════════════════


-- ───────────────────────────────────────────────────────────────────────────
-- 0) PRÓLOGO DE RECONCILIACIÓN — aparta el esquema del 31-ago-2026.
--
--    ORDEN OBLIGATORIO: primero la vista, luego la tabla, luego los índices.
--    Renombrar la tabla antes de tirar la vista NO rompe (la vista sigue el
--    rename), pero deja una vista viva con la forma vieja apuntando a la tabla
--    apartada — y la pestaña seguiría leyéndola sin enterarse del cambio.
--
--    LOS ÍNDICES HAY QUE RENOMBRARLOS, y esto es la trampa fina: los nombres
--    de índice son ÚNICOS POR ESQUEMA, no por tabla. Si `idx_returns_orden`
--    sigue existiendo sobre la tabla apartada, el `create index if not exists
--    idx_returns_orden` del §1 se SALTA EN SILENCIO y la tabla nueva se queda
--    sin ese índice. Nadie se entera hasta que una consulta va lenta.
-- ───────────────────────────────────────────────────────────────────────────
--    v2.3 — TODO ESTO VA DENTRO DE UN GUARD. `if exists` NO alcanza para ser
--    idempotente: en la segunda corrida `channel.returns` ya es la tabla NUEVA
--    y `returns_ml_v0` ya existe, así que el rename aborta con "relation
--    already exists" y se lleva por delante TODAS las migraciones siguientes
--    (el runner reaplica todo y hace sys.exit al primer fallo).
--
--    El guard pregunta por `claim_id`, que es la FIRMA de la tabla del 31-ago:
--    la nueva no la tiene. Si no está, no hay nada que apartar.
--
--    Los cinco nombres de índice se verificaron contra producción el 9-sep:
--    son exactamente los cinco que existen, ninguno queda fuera. Aun así van
--    con `if exists`, porque dentro del guard un nombre que faltara no debe
--    tumbar la migración entera.
do $$
begin
  if exists (select 1 from information_schema.columns
              where table_schema = 'channel' and table_name = 'returns'
                and column_name  = 'claim_id') then

    drop view if exists channel.returns_daily;

    alter table channel.returns rename to returns_ml_v0;

    alter index if exists channel.returns_pkey       rename to returns_ml_v0_pkey;
    alter index if exists channel.idx_returns_creado rename to idx_returns_ml_v0_creado;
    alter index if exists channel.idx_returns_cuenta rename to idx_returns_ml_v0_cuenta;
    alter index if exists channel.idx_returns_orden  rename to idx_returns_ml_v0_orden;
    alter index if exists channel.idx_returns_sku    rename to idx_returns_ml_v0_sku;

    raise notice 'v2.3 §0: tabla del 31-ago apartada como channel.returns_ml_v0';
  else
    raise notice 'v2.3 §0: nada que apartar (channel.returns ya es el modelo nuevo)';
  end if;
end $$;

-- v2.3: condicionado. Si el §0 no corrió (segunda vuelta) la tabla puede no
-- existir, y `comment on` no admite `if exists`.
do $$
begin
  if to_regclass('channel.returns_ml_v0') is not null then
    execute $c$comment on table channel.returns_ml_v0 is
  'CONGELADA. Esquema plano de devoluciones de ML creado a mano el 31-ago-2026 '
  '(62 filas, 24→31 de agosto). Se conserva como evidencia hasta que el '
  'backfill nuevo reponga esas devoluciones en channel.returns; entonces se '
  'borra. NO la lea nadie: su vista returns_daily ya no existe.'$c$;
  end if;
end $$;


-- ───────────────────────────────────────────────────────────────────────────
-- 1) CABECERA
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists channel.returns (
  canal              text not null references core.channels(id),
  cuenta             text not null,
  external_return_id text not null,          -- SOLO id de devolución física
  account_id         uuid references core.accounts(id),

  -- Vínculo al pedido: DELIBERADAMENTE SIN FK. Ver DECISIÓN 1.
  external_order_id  text,
  wc_order_id        bigint,

  tipo               text not null default 'devolucion',
        -- 'devolucion'    = volvió física o se reembolsó tras la entrega
        -- 'no_entregado'  = nunca llegó y regresó al remitente
        -- Las CANCELACIONES quedan fuera a propósito: ya viven en
        -- channel.orders.estado_canal (2,495 en 90 días). Ver DECISIÓN 7.
  estado             text not null default 'abierta',
  estado_canal       text,                   -- el string crudo del marketplace

  motivo             text,                   -- canónico (NULL hasta tener el mapa)
  motivo_canal       text,                   -- el crudo del marketplace

  -- DINERO. Todos POSITIVOS; el signo lo pone la vista, nunca la tabla.
  monto_reembolsado    numeric(14,2),
  comision_reintegrada numeric(14,2),
  costo_envio_retorno  numeric(14,2),        -- de la devolución ENTERA, no por línea

  external_claim_id  text,                   -- reserva la costura al claim de ML.
        -- La TABLA channel.claims se difiere a F1 (DECISIÓN 3); la COLUMNA no,
        -- porque reconstruir este vínculo después, sobre datos ya cargados,
        -- exige un backfill de algo que en el momento del evento estaba a mano.

  es_fulfillment     boolean not null default false,
  destino            text,                   -- SIN check: no se ha visto un payload
  reintegro_woo      boolean,                -- TRI-ESTADO. NULL = "no se sabe".
        -- true = Woo YA repuso esta pieza (pedido cancelado que sí había
        -- descontado). El candado contra el doble conteo de F5 (DECISIÓN 8).
        -- SIN default: un `false` automático mentiría igual que el `None` de
        -- una tabla congelada, y F5 reintegraría stock que Woo ya devolvió.

  -- ── §1b · LO QUE APRENDIÓ LA TABLA DEL 31-AGO ────────────────────────────
  venta_contaba      boolean,
        -- ¿La orden estaba DENTRO de channel.sales_daily cuando se capturó la
        -- devolución? Se calcula con el MISMO filtro de la vista (migración
        -- 0030): lower(estado_canal) not in ('cancelled','invalid','canceled').
        -- SIN ESTO EL KPI MIENTE: una devolución de un pedido ya cancelado se
        -- restaría de unas ventas de las que ese pedido nunca formó parte, o
        -- sea DOS VECES. Medido en la ventana del 24→31 de agosto: $14,734 de
        -- $49,182 devueltos (el 30%) corresponden a órdenes ya canceladas.
        -- Nullable a propósito: NULL = no se pudo determinar, y entonces no se
        -- resta — misma disciplina que reintegro_woo.
  estado_dinero      text,
        -- El `status_money` crudo de ML: 'retained' (el dinero sigue con
        -- nosotros) | 'refunded' (ya se soltó). NO es lo mismo que
        -- `estado='reembolsada'`, que es NUESTRA lectura del ciclo: una
        -- devolución puede estar 'delivered' con el dinero todavía retenido.
        -- Es el dato que contesta "¿esto ya nos costó, o todavía se puede
        -- pelear?", y ninguna de las dos columnas de estado lo dice sola.

  -- ── §1c · LO QUE DA GET /post-purchase/v1/claims/{id}/detail ─────────────
  -- Verificado en vivo el 8-sep-2026 contra ambas cuentas. Este endpoint es la
  -- única fuente de motivo LEGIBLE desde que el reclamo se abre; `motivo_canal`
  -- solo trae un código (PDD9963) y `resolucion_motivo` solo llega al cerrar
  -- (19 de 62 filas en la tabla del 31-ago).
  motivo_texto       text,   -- "El comprador dijo que se arrepintió de la compra"
  estado_titulo      text,   -- "Devolución en camino" / "Devolución en preparación"
  accion_responsable text,   -- 'complainant' | 'respondent' — de quién es el turno
  fecha_limite       timestamptz,  -- due_date: hasta cuándo hay para responder

  -- Solo los dos hitos que anclan las vistas y los índices. Los demás se
  -- derivan de channel.return_history (DECISIÓN 2). Los llena el trigger, y
  -- SOLO la primera vez que se alcanza el estado: así un retroceso no miente.
  abierta_at         timestamptz,
  reembolsada_at     timestamptz,

  detectado_via      text not null default 'sondeo',  -- webhook|sondeo|manual|backfill
  payload            jsonb,
  creado_at          timestamptz not null default now(),
  actualizado_at     timestamptz not null default now(),

  primary key (canal, cuenta, external_return_id),

  constraint ck_returns_tipo   check (tipo   in ('devolucion','no_entregado')),
  constraint ck_returns_estado check (estado in
        ('abierta','en_transito','recibida','reembolsada','cerrada','rechazada')),
  constraint ck_returns_montos check (
        coalesce(monto_reembolsado,0)    >= 0 and
        coalesce(comision_reintegrada,0) >= 0 and
        coalesce(costo_envio_retorno,0)  >= 0)
);

create index if not exists idx_returns_orden   on channel.returns (canal, cuenta, external_order_id);
create index if not exists idx_returns_abierta on channel.returns (abierta_at desc);
create index if not exists idx_returns_estado  on channel.returns (estado) where estado <> 'cerrada';
create index if not exists idx_returns_wc      on channel.returns (wc_order_id);

-- Índice PARCIAL para la reconciliación de huérfanas (§3b). Sin él, el trigger
-- de channel.orders escanearía la tabla entera en cada pedido nuevo.
create index if not exists idx_returns_huerfanas
  on channel.returns (canal, cuenta, external_order_id)
  where wc_order_id is null;

create index if not exists idx_returns_claim
  on channel.returns (canal, cuenta, external_claim_id)
  where external_claim_id is not null;

-- ───────────────────────────────────────────────────────────────────────────
-- REVERSA DE ESTA MIGRACIÓN — en este orden, y NO solo "drop de tres tablas".
-- Las funciones plpgsql no crean dependencia rastreable: un drop de tablas
-- las deja vivas apuntando a relaciones que ya no existen.
--   v2.3 · C6 — LAS VISTAS PRIMERO. Tal como estaba en la v2.2 la reversa NO
--   corría: `drop table channel.return_items` sin cascade falla con "cannot
--   drop table because other objects depend on it", porque returns_daily,
--   returns_refunds_daily y returns_cabecera dependen de ella. Alguien
--   deshaciendo esto en un incidente se topaba con el error a media reversa.
--   drop view     if exists channel.returns_rate_sku;
--   drop view     if exists channel.returns_refunds_daily;
--   drop view     if exists channel.returns_cabecera;
--   drop view     if exists channel.returns_daily;
--   drop view     if exists channel.returns_sla;
--   drop function if exists channel.reconciliar_returns_huerfanas();
--   drop table    if exists channel.return_items;
--   drop table    if exists channel.return_history;
--   drop table    if exists channel.returns;
--   drop function if exists channel.tg_returns_touch()  cascade;
--   drop function if exists channel.fn_return_history() cascade;
-- Nada de esto toca channel.orders: v2.1 retiró el trigger que vivía ahí.
--
-- ⚠️ v2.2 — LA REVERSA DEL §0 NO ES AUTOMÁTICA. Deshacer esta migración deja
-- la pestaña de Devoluciones SIN FUENTE hasta que se restituya el esquema
-- viejo, porque su vista se tiró en el prólogo:
--   alter table channel.returns_ml_v0 rename to returns;
--   alter index channel.returns_ml_v0_pkey rename to returns_pkey;   -- y los 4 idx
--   -- y recrear channel.returns_daily con la definición vieja, que está en
--   -- este mismo archivo al final, en el bloque comentado ANEXO A.
-- ───────────────────────────────────────────────────────────────────────────

comment on table channel.returns is
  'Devoluciones por canal: lo que channel.orders NO puede expresar. Una venta '
  'devuelta DESPUÉS de la entrega no cambia estado_canal (sigue paid), así que '
  'hoy es invisible. NO tiene FK a channel.orders a propósito: el evento puede '
  'llegar antes que el pedido y una violación de FK dentro de un handler de '
  'webhook pierde justo el evento que se quiere capturar (DECISIÓN 1).';

comment on column channel.returns.reintegro_woo is
  'TRI-ESTADO. true = Woo ya repuso la pieza a _stock, de donde lee el fan-out '
  '(fanout_stock._stock_drop): reintegrar otra vez contaría DOBLE. false = Woo '
  'no repuso. NULL = NO SE SABE. Regla dura de F5: tocar stock solo si es '
  'FALSE — NULL bloquea igual que true. No se puede derivar de '
  'orders.estado_wc: un pedido FULL cancelado sale ''cancelled'' sin haber '
  'descontado nunca. Solo lo sabe quien cancela (pedidos_ml.py:447-507, que ya '
  'tiene la foto_previa de _reduced_stock en memoria).';

comment on column channel.returns.venta_contaba is
  'TRI-ESTADO igual que reintegro_woo, y por la misma razón. true = la orden '
  'SÍ estaba en sales_daily al capturar, así que esta devolución se puede '
  'restar de las ventas. false = ya estaba cancelada y su valor ya salió: '
  'restarla otra vez lo cuenta doble. NULL = no se pudo determinar → no se '
  'resta. El filtro tiene que ser IDÉNTICO al de channel.sales_daily '
  '(migración 0030, en minúsculas): si las dos listas se separan, esta columna '
  'empieza a mentir sin avisar.';

comment on column channel.returns.estado_dinero is
  'El status_money crudo del canal. En ML: retained | refunded. Ortogonal a '
  '`estado`: hay devoluciones entregadas con el dinero aún retenido. Es la '
  'columna que separa "ya nos costó" de "todavía se puede pelear".';

comment on column channel.returns.motivo_texto is
  'El motivo en prosa, tal como lo escribe el canal para humanos. En ML sale '
  'de GET /post-purchase/v1/claims/{id}/detail → campo `problem`, y está '
  'disponible DESDE QUE SE ABRE el reclamo — a diferencia de motivo_canal, que '
  'es un código, y de la resolución, que solo llega al cerrar.';

comment on column channel.returns.fecha_limite is
  'Hasta cuándo hay para responder (claims/{id}/detail.due_date). Es lo que '
  'convierte la pantalla de reporte en herramienta: cuáles vencen esta semana.';

comment on column channel.returns.external_claim_id is
  'Id del claim de ML cuando la devolución nace de uno. Nullable y sin FK: la '
  'tabla channel.claims se diseña en F1 con payloads reales. La columna existe '
  'desde F0 solo para no tirar un vínculo que después habría que reconstruir.';

comment on column channel.returns.destino is
  'SIN constraint a propósito: el catálogo de destinos se define en F1, con '
  'payloads reales a la vista. Misma disciplina que motivo_canal.';

-- ───────────────────────────────────────────────────────────────────────────
-- 2) LÍNEAS
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists channel.return_items (
  canal              text not null references core.channels(id),
  cuenta             text not null,
  external_return_id text not null,
  linea              int  not null,

  item_id            text,
  sku                citext,                  -- NULL = sin SKU mapeado (sin FK, como order_items)
  linea_pedido       int,                     -- ← channel.order_items.linea, si se pudo casar
  titulo             text,

  cantidad           int not null default 1,
  monto_unitario     numeric(14,2),
  reintegra_stock    boolean,                 -- SOLO aquí: una devolución de 3 líneas
                                              -- puede reintegrar 2 y destruir 1

  primary key (canal, cuenta, external_return_id, linea),
  foreign key (canal, cuenta, external_return_id)
    references channel.returns (canal, cuenta, external_return_id) on delete cascade,
  constraint ck_return_items_cantidad check (cantidad > 0)
);

create index if not exists idx_return_items_sku     on channel.return_items (sku);
create index if not exists idx_return_items_item_id on channel.return_items (item_id);

comment on table channel.return_items is
  'Líneas de la devolución, con CANTIDADES. Sin esto no hay tasa por SKU: una '
  'devolución de 1 pieza en un pedido de 5 no es un pedido perdido. `linea` la '
  'asigna el escritor de forma DETERMINISTA (orden del payload del canal): a '
  'diferencia de order_items, una devolución puede llegar en tandas y una '
  'renumeración crearía líneas duplicadas.';

comment on column channel.return_items.monto_unitario is
  'PRECIO DE VENTA CONGELADO de la pieza devuelta, NO lo reembolsado. Sale de '
  'channel.order_items.precio_unitario. Es lo que permite valorar la '
  'devolución el día que se abre, cuando todavía no hay reembolso: la API de '
  'ML no da el monto reembolsado hasta que suelta el dinero. El reembolso REAL '
  'vive en la cabecera (channel.returns.monto_reembolsado) y llega después. '
  'Son dos preguntas distintas: "cuánto valía lo que se devolvió" y "cuánto '
  'dinero salió". Confundirlas fue lo que hizo inservible a partially_refunded '
  'en channel.orders, donde el total es el del pedido y no el reembolsado.';

-- ───────────────────────────────────────────────────────────────────────────
-- 3) HISTORIA DE ESTADOS — patrón channel.listing_history
--    La v1 no la tenía y se apoyaba en ops.webhook_events. Error: esa tabla
--    se purga a los 3 DÍAS (ops.purgar_webhook_events, 0004), así que ningún
--    análisis a 60 días podría reconstruir un tránsito.
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists channel.return_history (
  id                 bigint generated always as identity primary key,
  canal              text not null,
  cuenta             text not null,
  external_return_id text not null,
  estado_anterior    text,
  estado_nuevo       text not null,
  estado_canal       text,
  detectado_via      text not null default 'sondeo',
  changed_at         timestamptz not null default now(),
  foreign key (canal, cuenta, external_return_id)
    references channel.returns (canal, cuenta, external_return_id) on delete cascade
);

create index if not exists idx_return_history_ret
  on channel.return_history (canal, cuenta, external_return_id, changed_at desc);

comment on table channel.return_history is
  'Append-only, una fila por transición de estado. Es la FUENTE DE VERDAD de '
  'los tiempos: aguanta retrocesos (recibida → rechazada → reabierta), que las '
  'columnas de hito no pueden representar. '
  'v2.2 — OJO CON EL BACKFILL: changed_at cae en now(). Una devolución de '
  'marzo cargada hoy deja su historia fechada hoy, y channel.returns_sla '
  'reporta tiempos falsos para ella. El escritor del backfill debe insertar la '
  'historia con las fechas reales, o el SLA solo vale para lo capturado en '
  'vivo. No hay forma de distinguirlo después: `detectado_via` es la única '
  'pista y por eso el backfill DEBE marcarse como tal.';

-- ───────────────────────────────────────────────────────────────────────────
-- 4) TRIGGERS
-- ───────────────────────────────────────────────────────────────────────────

-- 4a) touch + hitos "primera vez" + historia, en un solo lugar.
create or replace function channel.tg_returns_touch() returns trigger
language plpgsql as $$
declare
  v_wc  bigint;
  v_acc uuid;
begin
  new.actualizado_at := now();

  -- `abierta_at` = primera vez que SUPIMOS de la devolución, NO primera vez
  -- que la vimos en estado 'abierta' literal. Con sondeo cada 5-15 min es
  -- normal descubrirla ya en 'recibida': si se exigiera el estado 'abierta',
  -- esa fila quedaría con abierta_at NULL PARA SIEMPRE (la regla es "solo la
  -- primera vez" y el estado nunca vuelve atrás) y desaparecería en silencio
  -- de returns_daily, que filtra `abierta_at is not null`. Ese NULL no
  -- significaría "no existe" sino "no lo vimos a tiempo" — la ambigüedad
  -- exacta que costó los 964 pedidos fantasma.
  --
  -- ⚠️ v2.2 — ESTO ES UNA TRAMPA PARA EL BACKFILL, y el backfill es lo
  -- siguiente que va a correr (771 devoluciones históricas de ML, verificadas
  -- el 8-sep). El `if ... is null` respeta el valor que venga en el INSERT,
  -- así que el escritor TIENE que mandar `abierta_at = claim.date_created`.
  -- Si lo deja en NULL, las 771 se sellan con la fecha de hoy y toda la
  -- historia colapsa en un solo día: returns_daily mostraría 771 devoluciones
  -- hoy y cero en los siete meses anteriores. Lo mismo aplica a
  -- reembolsada_at.
  if tg_op = 'INSERT' and new.abierta_at is null then
    new.abierta_at := now();
  end if;
  if new.estado = 'reembolsada' and new.reembolsada_at is null then
    new.reembolsada_at := now();
  end if;

  -- Enlace al pedido en el caso COMÚN: la devolución llega después de la
  -- venta, así que el pedido YA existe. Se resuelve aquí, sobre la tabla
  -- nueva, y NO con un trigger colgado de channel.orders (ver §4b).
  -- OJO con el patrón: `select … into` SIN coincidencias deja los destinos en
  -- NULL. Asignar directo a new.* borraría el account_id que venía en el
  -- INSERT cada vez que el pedido todavía no existe — que es justo el caso
  -- que esto pretende tolerar. Por eso pasa por variables y `if found`.
  if new.wc_order_id is null and new.external_order_id is not null then
    select o.wc_order_id, o.account_id
      into v_wc, v_acc
      from channel.orders o
     where o.canal  = new.canal
       and o.cuenta = new.cuenta
       and o.external_order_id = new.external_order_id
     limit 1;
    if found then
      new.wc_order_id := v_wc;
      new.account_id  := coalesce(new.account_id, v_acc);
    end if;
  end if;
  return new;
end $$;

drop trigger if exists returns_touch on channel.returns;
create trigger returns_touch before insert or update on channel.returns
  for each row execute function channel.tg_returns_touch();

create or replace function channel.fn_return_history() returns trigger
language plpgsql as $$
begin
  if tg_op = 'INSERT' or old.estado is distinct from new.estado then
    insert into channel.return_history
      (canal, cuenta, external_return_id, estado_anterior, estado_nuevo,
       estado_canal, detectado_via)
    values
      (new.canal, new.cuenta, new.external_return_id,
       case when tg_op = 'INSERT' then null else old.estado end,
       new.estado, new.estado_canal, new.detectado_via);
  end if;
  return new;
end $$;

drop trigger if exists returns_history on channel.returns;
create trigger returns_history after insert or update on channel.returns
  for each row execute function channel.fn_return_history();

-- 4b) RECONCILIACIÓN de huérfanas — FUNCIÓN, NO TRIGGER sobre channel.orders.
--
--     La v2 colgaba un `after insert on channel.orders` que hacía este UPDATE.
--     Se retiró por tres razones, las tres verificadas contra el código:
--
--     1. NO CUBRÍA EL CASO COMÚN. Una devolución llega DESPUÉS de la venta, o
--        sea que el pedido ya existe y el `after insert` nunca dispara (el
--        write real es un upsert: la rama de conflicto ejecuta triggers de
--        UPDATE, no de INSERT). Resolvía la dirección rara y dejaba la
--        frecuente al aire. Esa dirección ahora la cubre tg_returns_touch.
--     2. ACOPLABA EL CAMINO CALIENTE. orders_write.guardar fija
--        statement_timeout=4000 para TODA la transacción, triggers incluidos,
--        y cualquier excepción cae al except que escribe a MySQL y dispara la
--        alerta "Escritura de PEDIDOS cayó a MySQL". Un lock en una fila
--        huérfana de devoluciones habría degradado el pipeline más crítico
--        del sistema y avisado "kubera caída" sin que kubera estuviera caída.
--     3. ROMPÍA LA REVERSA. El trigger vive en channel.orders, no en
--        channel.returns: `drop table channel.returns cascade` NO lo borra
--        (los cuerpos plpgsql no crean dependencia rastreable). Tras el
--        rollback, cada pedido nuevo abortaría con "relation channel.returns
--        does not exist" — es decir, la reversa mataba la arteria de ventas.
--
--     Esta función la corre un job periódico (F4), desacoplada de los pedidos.
create or replace function channel.reconciliar_returns_huerfanas()
  returns bigint
language sql as $$
  with u as (
    update channel.returns r
       set wc_order_id = o.wc_order_id,
           account_id  = coalesce(r.account_id, o.account_id)
      from channel.orders o
     where o.canal  = r.canal
       and o.cuenta = r.cuenta
       and o.external_order_id = r.external_order_id
       and r.wc_order_id is null
    returning 1
  )
  select count(*) from u;
$$;

comment on function channel.reconciliar_returns_huerfanas() is
  'Cierra las devoluciones que llegaron ANTES que su pedido. La correr un job '
  'periódico (F4), NUNCA un trigger sobre channel.orders. Devuelve cuántas '
  'reconcilió. Las que queden huérfanas >48 h son señal real y deben avisar: '
  'una huérfana a los 2 minutos es normal, a las 48 h son datos rotos.';

-- ───────────────────────────────────────────────────────────────────────────
-- 5) VISTAS — derivadas al vuelo, nunca tablas (regla de sales_daily)
-- ───────────────────────────────────────────────────────────────────────────

-- v2.3 — SE TIRAN ANTES DE RECREARSE. `create or replace view` exige la MISMA
-- lista de columnas, en el mismo orden y tipo: en cuanto alguien edite una de
-- estas vistas, el `or replace` falla y la migración deja de poder re-correrse.
-- El orden importa (returns_rate_sku depende de returns_daily) y todo esto
-- vive dentro de la transacción del archivo, así que no hay ventana sin vista.
drop view if exists channel.returns_rate_sku;
drop view if exists channel.returns_refunds_daily;
drop view if exists channel.returns_cabecera;
drop view if exists channel.returns_daily;
drop view if exists channel.returns_sla;

-- 5a) SLA robusto a retrocesos: PRIMERA vez que se alcanzó cada estado.
create or replace view channel.returns_sla as
select canal, cuenta, external_return_id,
       min(changed_at) filter (where estado_nuevo = 'abierta')      as abierta_at,
       min(changed_at) filter (where estado_nuevo = 'en_transito')  as en_transito_at,
       min(changed_at) filter (where estado_nuevo = 'recibida')     as recibida_at,
       min(changed_at) filter (where estado_nuevo = 'reembolsada')  as reembolsada_at,
       max(changed_at) filter (where estado_nuevo = 'cerrada')      as cerrada_at,
       count(*)                                                     as transiciones,
       count(*) filter (where estado_nuevo = 'rechazada')           as veces_rechazada
from channel.return_history
group by 1, 2, 3;

comment on view channel.returns_sla is
  'Tiempos por etapa. min() = primera vez que se alcanzó el estado; '
  'transiciones > 4 o veces_rechazada > 1 delatan un caso que fue y volvió. '
  'v2.2 — NO VALE para filas de backfill salvo que su historia se haya '
  'insertado con fechas reales: el default de changed_at es now().';

-- 5b) VOLUMEN por día×cuenta×item, fechado por APERTURA. Espejo de sales_daily.
--     Es la que se casa contra la venta para sacar tasa.
--
--     v2.2 — recupera CUATRO columnas que la pestaña viva ya muestra y que la
--     v2.1 no producía: returns_count (el "N devoluciones" del KPI),
--     value_returned (el importe a precio de venta), venta_contaba (lo
--     restable) y motivo/estado_dinero (el ⓘ de la tabla por SKU). Sin ellas
--     esta migración es un downgrade funcional de la pantalla.
create or replace view channel.returns_daily as
with tot as (
  select canal, cuenta, external_return_id,
         sum(coalesce(monto_unitario,0) * cantidad) as monto_total,
         sum(cantidad)                              as piezas_total,
         -- El método de reparto se decide por DEVOLUCIÓN, no por línea: si una
         -- sola línea viene sin precio, repartir por valor le asigna 0 y la
         -- suma queda POR DEBAJO del flete real. En ese caso se reparte por
         -- piezas, que siempre están.
         bool_and(monto_unitario is not null)       as todos_con_precio,
         count(*)                                   as lineas,
         -- v2.3 · C3. La línea que "lleva" el conteo de la devolución.
         min(linea)                                 as linea_ancla
  from channel.return_items
  group by 1, 2, 3
)
select (r.abierta_at at time zone 'America/Mexico_City')::date as date,
       r.canal,
       r.cuenta,
       i.item_id,
       i.sku,
       -- v2.3 · C4 — DE AQUÍ HASTA `reintegro_woo` SON CLAVES DE AGRUPACIÓN,
       -- no agregados, igual que en la vista vieja (ANEXO A, `group by 1..9`).
       -- La v2.2 los colapsaba con bool_or/max y eso rompía dos cosas:
       --   · `bool_or(venta_contaba)` da true si ALGUNA es true e ignora NULL,
       --     así que un grupo que mezclara una devolución restable con una de
       --     pedido ya cancelado marcaba TODO restable — justo el doble conteo
       --     que §1b existe para evitar;
       --   · `max()` sobre texto elige por orden alfabético, no por relevancia,
       --     y encima le quitaba al backend los valores distintos que su
       --     `array_agg(distinct resolucion_motivo)` necesita.
       -- Agrupar cuesta más filas y no miente. Son atributos de la CABECERA:
       -- todas las líneas de una devolución comparten el mismo valor, así que
       -- agrupar por ellos no parte nada que estuviera junto.
       r.es_fulfillment                                        as is_full,
       -- v2.3 · C1. El backend pide `resolucion_motivo` POR NOMBRE (2 usos).
       -- La prosa está en `motivo_texto` (§1c), disponible desde que se abre el
       -- reclamo; `motivo` sigue siendo NULL hasta que exista el mapa y
       -- `motivo_canal` es un código ('PDD9963'). El orden del coalesce es el
       -- que va de más legible a menos.
       coalesce(r.motivo_texto, r.motivo, r.motivo_canal)      as resolucion_motivo,
       r.estado_dinero,
       r.venta_contaba,
       r.reintegro_woo,
       -- v2.3 · C3. CADA DEVOLUCIÓN LA CUENTA UNA SOLA DE SUS LÍNEAS.
       -- La v2.2 ponía `count(distinct external_return_id)`, que agrupado por
       -- SKU aporta 1 a CADA grupo: una devolución de 3 SKUs se contaba 3
       -- veces, y el backend hace `sum(returns_count)` en tres consultas
       -- (total, por is_full y por cuenta). Anclando el conteo a la línea de
       -- `linea` mínima, la suma vuelve a ser exacta con cualquier
       -- agrupación —el ancla cae en un solo grupo— y el backend no cambia.
       -- Efecto colateral asumido: por SKU suelto la columna vale 0 o 1 según
       -- si ese SKU es el ancla. NO usar por SKU; para eso está units_returned.
       count(*) filter (where i.linea = t.linea_ancla)::int    as returns_count,
       sum(i.cantidad)                                         as units_returned,
       -- OJO CON EL NOMBRE: esto NO es lo reembolsado. Es el VALOR A PRECIO DE
       -- VENTA de lo que se devolvió (monto_unitario = precio congelado). Se
       -- conserva el alias `refunded` de la v2.1 por compatibilidad, y se
       -- agrega `value_returned` —el nombre honesto y el que ya usa el
       -- backend— apuntando a lo mismo. Lo REEMBOLSADO de verdad vive en
       -- channel.returns_refunds_daily.
       sum(coalesce(i.monto_unitario,0) * i.cantidad)          as refunded,
       sum(coalesce(i.monto_unitario,0) * i.cantidad)          as value_returned,
       -- PRORRATEO. El flete es de la devolución ENTERA: repetirlo por SKU con
       -- max() lo duplicaba al sumar entre SKUs. Suma bien, pero ATRIBUYE MAL
       -- por SKU (ver flete_exacto).
       sum(coalesce(r.costo_envio_retorno, 0) *
           case when t.todos_con_precio and t.monto_total > 0
                then (coalesce(i.monto_unitario,0) * i.cantidad) / t.monto_total
                else i.cantidad::numeric / nullif(t.piezas_total, 0)
           end)                                                as flete_retorno,
       -- Flete ATRIBUIBLE sin sesgo: solo el de devoluciones de UNA línea,
       -- donde la atribución al SKU es exacta porque no hay a quién repartir.
       -- Es el que debe alimentar una decisión de despublicar un SKU.
       sum(case when t.lineas = 1 then coalesce(r.costo_envio_retorno, 0)
                else 0 end)                                    as flete_exacto
from channel.return_items i
join channel.returns r using (canal, cuenta, external_return_id)
join tot t            using (canal, cuenta, external_return_id)
where r.estado <> 'rechazada'
  and r.abierta_at is not null
group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10;

comment on view channel.returns_daily is
  'VOLUMEN de devoluciones por día×cuenta×item, fechado por apertura — la '
  'fecha que se puede casar contra la venta. Para DINERO REEMBOLSADO usar '
  'channel.returns_refunds_daily. '
  'ADVERTENCIA sobre flete_retorno: está PRORRATEADO por valor de línea. Suma '
  'exacto en agregados, pero SESGA por SKU — al SKU caro co-devuelto con uno '
  'barato le carga más flete aunque la caja sea la misma. Para decidir sobre '
  'un SKU usar flete_exacto, no flete_retorno. '
  'v2.2: `refunded` y `value_returned` son la MISMA columna (valor a precio de '
  'venta). `venta_contaba` marca las que se pueden restar de las ventas sin '
  'contarlas dos veces.';

-- 5c) DINERO por día, fechado por REEMBOLSO. Otra pregunta de negocio.
create or replace view channel.returns_refunds_daily as
with tot as (
  select canal, cuenta, external_return_id,
         sum(coalesce(monto_unitario,0) * cantidad) as monto_total,
         sum(cantidad)                              as piezas_total,
         bool_and(monto_unitario is not null)       as todos_con_precio
  from channel.return_items
  group by 1, 2, 3
)
select (r.reembolsada_at at time zone 'America/Mexico_City')::date as date,
       r.canal,
       r.cuenta,
       i.item_id,
       i.sku,
       sum(coalesce(i.monto_unitario,0) * i.cantidad)  as refunded,
       -- PRORRATEADA, no max(). comision_reintegrada es de la devolución
       -- entera: con max() una devolución de 2 SKUs emitía dos filas con la
       -- comisión COMPLETA cada una y sumarlas la contaba doble — el mismo
       -- bug que DECISIÓN 9 arregló para el flete y que aquí sobrevivía.
       sum(coalesce(r.comision_reintegrada, 0) *
           case when t.todos_con_precio and t.monto_total > 0
                then (coalesce(i.monto_unitario,0) * i.cantidad) / t.monto_total
                else i.cantidad::numeric / nullif(t.piezas_total, 0)
           end)                                        as comision_reintegrada
from channel.return_items i
join channel.returns r using (canal, cuenta, external_return_id)
join tot t            using (canal, cuenta, external_return_id)
where r.reembolsada_at is not null
group by 1, 2, 3, 4, 5;

comment on view channel.returns_refunds_daily is
  'Flujo de caja de devoluciones, fechado por reembolso. Los importes de '
  'cabecera (comision_reintegrada) van PRORRATEADOS entre líneas: suman exacto '
  'en agregados y sesgan por SKU, igual que flete_retorno. '
  'PENDIENTE CONOCIDO: una devolución que llega solo con cabecera (sin líneas '
  'todavía) no aparece aquí — el join a return_items es interno. El escritor de '
  'F1 debe aterrizar al menos una línea, o reconciliar monto_reembolsado de '
  'cabecera contra la suma de líneas.';

-- 5d) Tasa de devolución por SKU×canal, 60 días, en PIEZAS.
--     FULL OUTER JOIN: un SKU devuelto que ya NO se vende (despublicado por
--     devolverse, o vendido hace 61+ días) debe seguir siendo visible. Con
--     LEFT JOIN desaparecía entero — no solo su tasa.
create or replace view channel.returns_rate_sku as
with v as (
  select sku, canal, sum(units_sold) as vendidas, sum(revenue) as ingreso
  from channel.sales_daily
  where date >= (now() at time zone 'America/Mexico_City')::date - 60
  group by 1, 2
), d as (
  select sku, canal,
         sum(units_returned)               as devueltas,
         sum(refunded)                     as reembolsado,
         sum(coalesce(flete_retorno, 0))   as flete_retorno
  from channel.returns_daily
  -- v2.2 — MISMO UNIVERSO QUE EL DENOMINADOR. channel.sales_daily excluye los
  -- pedidos cancelados; sin este filtro el numerador incluía devoluciones de
  -- pedidos que el denominador nunca contó, y la tasa podía pasar de 100%.
  -- `is not false` y no `= true`: NULL significa "no se sabe" y se mantiene
  -- visible, igual que en el resto del esquema.
  where date >= (now() at time zone 'America/Mexico_City')::date - 60
    and venta_contaba is not false
  group by 1, 2
)
select coalesce(v.sku,   d.sku)                                   as sku,
       coalesce(v.canal, d.canal)                                 as canal,
       coalesce(v.vendidas, 0)                                    as vendidas,
       coalesce(d.devueltas, 0)                                   as devueltas,
       round(100.0 * coalesce(d.devueltas,0)
             / nullif(v.vendidas, 0), 2)                          as tasa_pct,
       coalesce(v.ingreso, 0)                                     as ingreso,
       coalesce(d.reembolsado,0) + coalesce(d.flete_retorno,0)    as costo_devolucion
from v full outer join d using (sku, canal);

comment on view channel.returns_rate_sku is
  'Tasa por SKU×canal a 60 días, en PIEZAS. tasa_pct es NULL si el SKU no '
  'vendió en la ventana (indeterminado, no cero) pero devueltas y '
  'costo_devolucion SIGUEN VISIBLES. ADVERTENCIA: numerador y denominador son '
  'cohortes desalineadas — la devolución de hoy corresponde a una venta de '
  'hace semanas. Es indicador de TENDENCIA, no tasa contable. SOLO LECTURA '
  'para costing (DECISIÓN 5).';

-- 5e) v2.3 · C2 — LA FORMA PLANA, RECONSTRUIDA. Una fila por DEVOLUCIÓN.
--
--     Existe porque el backend no solo lee la vista: `fulfillment.py:3045` lee
--     la TABLA directo y le pide `piezas`, `valor` y `resolucion_motivo`, tres
--     columnas que el modelo nuevo no tiene (las dos primeras se fueron a
--     `return_items` con otro nombre). A la v2.2 se le pasó por completo.
--
--     LEFT JOIN a propósito: una devolución que llegó solo con cabecera —sin
--     líneas todavía— tiene que seguir contándose. Con join interno
--     desaparecería, que es el mismo hueco que `returns_refunds_daily` declara
--     como pendiente conocido.
--
--     FECHADA POR `abierta_at`, NO POR `creado_at`. En la tabla del 31-ago
--     `creado_at` era la fecha del claim; aquí es `default now()`, la hora de
--     inserción. Quien migre esa consulta tiene que cambiar el filtro de
--     `creado_at` a `date`, o el rango de fechas dejará de significar lo mismo
--     sin dar ningún error.
create or replace view channel.returns_cabecera as
select r.canal,
       r.cuenta,
       r.external_return_id,
       (r.abierta_at at time zone 'America/Mexico_City')::date  as date,
       r.es_fulfillment,
       r.estado,
       r.estado_dinero,
       r.venta_contaba,
       coalesce(r.motivo_texto, r.motivo, r.motivo_canal)       as resolucion_motivo,
       coalesce(sum(i.cantidad), 0)::bigint                     as piezas,
       coalesce(sum(coalesce(i.monto_unitario,0) * i.cantidad), 0)::numeric as valor
from channel.returns r
left join channel.return_items i using (canal, cuenta, external_return_id)
where r.abierta_at is not null
group by 1, 2, 3, 4, 5, 6, 7, 8, 9;

comment on view channel.returns_cabecera is
  'Una fila por DEVOLUCIÓN, con sus líneas ya sumadas: la forma plana que tenía '
  'la tabla del 31-ago, reconstruida sobre el modelo nuevo. Es la fuente para '
  'preguntas de CABECERA (cuántas devoluciones, cuántas descartadas); para '
  'preguntas por SKU va channel.returns_daily. LEFT JOIN: una devolución sin '
  'líneas todavía sigue apareciendo, con piezas y valor en 0. Fechada por '
  'abierta_at — `creado_at` en este modelo es la hora de inserción, no la del '
  'claim.';

-- ───────────────────────────────────────────────────────────────────────────
-- 6) RLS y permisos — convención de 0002 y 0005
-- ───────────────────────────────────────────────────────────────────────────
alter table channel.returns        enable row level security;
alter table channel.return_items   enable row level security;
alter table channel.return_history enable row level security;

grant all    on channel.returns                 to service_role;
grant all    on channel.return_items            to service_role;
grant all    on channel.return_history          to service_role;
-- 0005 sí otorga sobre la vista (grant select on channel.sales_daily). Sin
-- esto el backend leería vacío con RLS deny-by-default.
grant select on channel.returns_sla             to service_role;
grant select on channel.returns_daily           to service_role;
grant select on channel.returns_refunds_daily   to service_role;
grant select on channel.returns_rate_sku        to service_role;
grant select on channel.returns_cabecera       to service_role;   -- v2.3 · C2

-- v2.2 — la tabla apartada también queda bajo RLS. Sin esto se quedaría
-- legible por cualquiera que llegue con el rol anónimo.
alter table if exists channel.returns_ml_v0 enable row level security;
grant all on channel.returns_ml_v0 to service_role;


-- ── BLINDAJE DE VISTAS (añadido en el rescate del 9-sep; lección 0042→0045) ──
-- Sin esto pasan el filtro de RLS con el gafete del dueño. Y ojo hacia
-- adelante: un `create or replace view` sobre cualquiera de éstas RESETEA
-- esta opción — re-declararla en la misma migración que la recree.
alter view channel.returns_daily set (security_invoker = on);
alter view channel.returns_rate_sku set (security_invoker = on);
alter view channel.returns_refunds_daily set (security_invoker = on);
alter view channel.returns_sla set (security_invoker = on);
alter view channel.returns_cabecera set (security_invoker = on);   -- v2.3 · C2


-- ═══════════════════════════════════════════════════════════════════════════
-- ANEXO A — definición de la vista VIEJA, para la reversa del §0.
-- Es la que produjo la pantalla viva entre el 31-ago y el 9-sep. Se guarda
-- aquí porque no existe en ninguna migración: se creó a mano.
--
--   create or replace view channel.returns_daily as
--   select (creado_at at time zone 'America/Mexico_City')::date as date,
--          canal, cuenta, item_id, sku,
--          es_fulfillment as is_full,
--          resolucion_motivo, estado_dinero, venta_contaba,
--          count(*)     as returns_count,
--          sum(piezas)  as units_returned,
--          sum(valor)   as value_returned
--     from channel.returns
--    group by 1,2,3,4,5,6,7,8,9;
-- ═══════════════════════════════════════════════════════════════════════════
