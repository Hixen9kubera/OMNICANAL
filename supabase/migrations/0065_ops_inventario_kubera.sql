-- ═══════════════════════════════════════════════════════════════════════════
-- 0065 — INVENTARIO DE KUBERA (TEXCO III): saldo, libro, formatos de Bodega
--        y devoluciones
--        ops.stock_almacen · ops.stock_mov · ops.stock_formato ·
--        ops.stock_formato_linea · ops.stock_formato_evento · ops.devoluciones ·
--        vista ops.stock_apartado_descuadre_v · ops.stock_watch_photo.stock_kubera
--        Plan v3 (5-oct-2026) + DEVOLUCIONES §4 + revisión técnica §6.
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Estado: SIN APLICAR (6-oct-2026). Probada en el SANDBOX solo dentro de una
-- transacción que termina en ROLLBACK (verificar_0064_0065.py --en-transaccion).
-- DEPENDE de la 0064 (ops.almacenes, ops.ov_lineas, ops.exigir): el paso 0
-- truena si no está. Va a producción en la MISMA acta que la 0064 y ANTES de
-- fusionar el código que la lee (SEG-01; ver el encabezado de la 0064).
--
-- EL MODELO (plan v3 §1–§3)
--   · TEX3 es nuestra: su saldo vive en ops.stock_almacen y cada cambio del
--     físico en ops.stock_mov (el libro), en la MISMA sentencia.
--   · Bodega mueve de TEX2 a TEX3 y manda un FORMATO (SKU, cantidad,
--     ubicación). Entra «por confirmar» y no se vende. Un renglón entra al libro
--     cuando Bodega confirmó Y Odoo ya registró la salida de TEX2: la PUERTA
--     (D14 = la puerta, recomendado). La puerta es dato: salida_odoo_at NULL =
--     esperando. Kubera solo LEE Odoo; nada de aquí toca Odoo.
--   · Devoluciones de ventas surtidas por OV entran a REVISION (DEVOLUCIONES §4):
--     una fila por SKU y dictamen en ops.devoluciones, y sus movimientos.
--   · stock_watch suma el libre de kubera a Odoo; la foto guarda cada mitad.
--
-- LO QUE GARANTIZA LA BASE (también al dueño `postgres`)
--   · SALDO = LIBRO, al COMMIT (constraint trigger diferido, H01):
--       fisico = Σ delta = saldo_despues del último movimiento, y cada
--       movimiento nuevo cumple saldo_despues = anterior + delta.
--     Un `update ops.stock_almacen set fisico = …` sin su movimiento NO se
--     confirma. Corrección de emergencia = otro movimiento con nota; deshabilitar
--     el trigger es su propia acta (no deja rastro sin log de DDL).
--   · APARTADO = Σ RESERVADO, al COMMIT, en UNA consulta (una sola foto, H02):
--     apartado de (sku, bodega) = Σ reservado de los renglones sin entregar de
--     OV confirmadas vivas; y ningún renglón de una OV no confirmada o borrada
--     aparta.
--   · Subir el apartado nunca deja libre < 0 y solo ocurre en una bodega de
--     kubera con admite_ov (trigger BEFORE, C8 + D11). Bajar el físico por un
--     conteo SÍ puede dejar libre < 0: es la realidad; la vista vigía avisa.
--   · Solo bodegas de kubera: FK compuesta (almacen, fuente) → ops.almacenes
--     (codigo, fuente) con fuente = 'kubera' fija (H03). Un saldo en TEXCO no
--     se puede ni escribir (23503).
--   · El libro y los eventos de formato: SOLO SE AGREGAN (UPDATE, DELETE y
--     TRUNCATE rechazados por trigger).
--   · Formato: nace por_confirmar; por_confirmar → confirmado | descartado; los
--     dos son TERMINALES (congelados). Un renglón con la puerta abierta queda
--     congelado; uno de un formato confirmado solo puede BAJAR su cantidad (con
--     nota) mientras espera, y recibir la lectura de Odoo y la puerta.
--   · Claves de idempotencia con FORMA por motivo (stock_mov_clave_chk) y una
--     sola salida por renglón de OV (stock_mov_salida_ov_uq).
--
-- DECISIONES TOMADAS POR OMISIÓN (Eduardo puede cambiarlas)
--   1. D14: la PUERTA. Sus columnas (salida_odoo_*, odoo_tex2_al_abrir) van
--      nulables; el comportamiento (la lectura de Odoo y la sentencia) es código.
--   2. D9: confirmo_bodega es TEXTO no vacío (quién de Bodega dijo «listo»).
--      Pendiente SEG-12: permisos puntuales en core y confirmo_bodega_id.
--   3. D12: REVISION es de kubera, sin admite_ov ni Woo (sembrada en la 0064);
--      ops.devoluciones va aquí. Sin origen 'sin_venta' (D-R15).
--   4. D6: saldo por SKU × bodega; `ubicacion` es informativa (la del último
--      formato o conteo). No hay vista de ubicaciones (H17) todavía.
--   5. La cantidad del formato es > 0 (como el plan): un renglón que espera
--      puede bajar, no llegar a 0. Si Bodega toma TODO el lote, se decide
--      después si se admite 0 (la puerta tendría que excluirlo).
--   6. Formatos de clave del libro (comment de stock_mov.clave). La de
--      devoluciones sale del id de su fila (DEVOLUCIONES §4d, el documento
--      posterior). La recaptura del mismo paquete la frena la sentencia de
--      recibir (Σ devuelto + n ≤ entregado), no un UNIQUE: partir un paquete por
--      dictamen es legítimo y repite (paquete, sku, renglón).
--   7. odoo_* son numeric(14,3): Odoo da float (H06). La cantidad de Bodega
--      sigue siendo entera.
--   8. NO se crean aquí: el bucket `inventario-kubera` (privado, 10 MB, xlsx/csv
--      y fotos jpeg/webp, rutas por hash, retención por decidir), la tabla del
--      vigilante de D4 (sin definir) ni cambios a ops.odoo_sale_orders.
--
-- DERIVA ANOTADA (no se toca aquí): en el sandbox y en producción,
-- ops.odoo_sale_orders tiene `cuenta` y PK (canal, cuenta, external_order_id),
-- y sus renglones stock_libre/medido_at, más ops.fn_touch_actualizado_at y su
-- trigger. Nada de eso está en las migraciones de main (0034–0036 fuera de
-- main). La sentencia de kubera de stock_watch depende de `cuenta`.
--
-- QUIÉN ESCRIBE
--   stock_almacen y stock_mov → services/ordenes_venta.py (apartar, entregar,
--     cancelar), services/inventario_libro.py (entradas, puerta, traspasos,
--     conteo, merma, corrección) y la recepción de devoluciones. SIEMPRE: bodega
--     FOR SHARE → saldo FOR UPDATE en orden (sku, almacen) → UPDATE → INSERT en
--     el libro → ops.exigir(…), en UNA sentencia.
--   stock_formato(_linea, _evento) → services/inventario_libro.py.
--   devoluciones → la pantalla «Recibir devolución» (vía inventario_libro); el
--     estado de la OV lo cambia ordenes_venta.
--
-- IDEMPOTENCIA: `if not exists`, triggers drop + create, funciones `create or
-- replace`, vista drop + create con su invoker. PASO 0 con KB000. El ALTER de
-- stock_watch_photo va AL FINAL, con lock_timeout de 3 s y solo si falta la
-- columna: aplicarla justo después de una pasada de stock_watch.
--
-- VERIFICACIÓN ESPERADA
--   · verificar_rls.py (estático): +6 tablas con RLS y +1 vista con
--     security_invoker (con la 0064: 64 migraciones · 82 tablas · 18 vistas).
--   · select * from ops.stock_apartado_descuadre_v → 0 filas.
--   · backend/scripts/verificar_0064_0065.py: todas las pruebas OK.
--
-- REVERSA. Estructural solo si no hay datos reales; se niega con movimientos
-- fuera de ENSAYO y REVISION:
--   do $$ begin if exists (select 1 from ops.stock_mov where almacen not in
--   ('ENSAYO', 'REVISION')) then raise exception 'TEX3 tiene movimientos
--   reales: la reversa es operativa (banderas), no DROP'; end if; end $$;
-- y después: quitar la vista, devoluciones, stock_mov, stock_formato_evento,
-- stock_formato_linea, stock_formato, stock_almacen, las funciones de esta
-- migración, los triggers *_apartado_cuadra de ov_lineas/ov_ordenes, y la
-- columna stock_watch_photo.stock_kubera. Después de la fase B el libro es el
-- ÚNICO registro de TEX3 (RPO de un día sin PITR): la reversa es OPERATIVA.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

set local lock_timeout = '5s';

-- ───────────────────────────────────────────────────────────────────────────
-- 0) PASO 0 — la 0064 está, y lo que ya exista de la 0065 es ESTA versión
-- ───────────────────────────────────────────────────────────────────────────
do $$
declare
  e  text;
  t  text;
  c  text;
  ty text;
  d  text;
  v  regtype;
  columnas constant text[] := array[
    'stock_almacen.sku citext', 'stock_almacen.almacen text', 'stock_almacen.fuente text',
    'stock_almacen.fisico integer', 'stock_almacen.apartado integer', 'stock_almacen.libre integer',
    'stock_almacen.ubicacion text', 'stock_almacen.actualizado_at timestamptz',
    'stock_formato.id bigint', 'stock_formato.folio text', 'stock_formato.almacen text',
    'stock_formato.fuente text', 'stock_formato.estado text', 'stock_formato.archivo_nombre text',
    'stock_formato.archivo_hash text', 'stock_formato.archivo_path text',
    'stock_formato.reemplaza_a bigint', 'stock_formato.cargado_por text',
    'stock_formato.cargado_nombre text', 'stock_formato.cargado_at timestamptz',
    'stock_formato.confirmado_por text', 'stock_formato.confirmado_nombre text',
    'stock_formato.confirmado_at timestamptz', 'stock_formato.confirmo_bodega text',
    'stock_formato.descartado_por text', 'stock_formato.descartado_at timestamptz',
    'stock_formato.descartado_motivo text', 'stock_formato.rev integer',
    'stock_formato_linea.id bigint', 'stock_formato_linea.formato_id bigint',
    'stock_formato_linea.fila integer', 'stock_formato_linea.sku citext',
    'stock_formato_linea.sku_archivo text', 'stock_formato_linea.cantidad integer',
    'stock_formato_linea.cantidad_archivo integer', 'stock_formato_linea.ubicacion text',
    'stock_formato_linea.nota text', 'stock_formato_linea.odoo_tex2_al_cargar numeric',
    'stock_formato_linea.odoo_tex2_al_confirmar numeric', 'stock_formato_linea.odoo_total_al_cargar numeric',
    'stock_formato_linea.salida_odoo_at timestamptz', 'stock_formato_linea.salida_odoo_via text',
    'stock_formato_linea.salida_odoo_ref text', 'stock_formato_linea.odoo_tex2_al_abrir numeric',
    'stock_formato_linea.aviso text',
    'stock_formato_evento.id bigint', 'stock_formato_evento.formato_id bigint',
    'stock_formato_evento.linea_id bigint', 'stock_formato_evento.evento text',
    'stock_formato_evento.antes jsonb', 'stock_formato_evento.despues jsonb',
    'stock_formato_evento.nota text', 'stock_formato_evento.quien text',
    'stock_formato_evento.creado_at timestamptz',
    'stock_mov.id bigint', 'stock_mov.sku citext', 'stock_mov.almacen text', 'stock_mov.fuente text',
    'stock_mov.delta integer', 'stock_mov.saldo_despues integer', 'stock_mov.motivo text',
    'stock_mov.ref text', 'stock_mov.clave text', 'stock_mov.ov_linea_id bigint',
    'stock_mov.nota text', 'stock_mov.quien text', 'stock_mov.quien_nombre text',
    'stock_mov.via text', 'stock_mov.creado_at timestamptz',
    'devoluciones.id bigint', 'devoluciones.folio text', 'devoluciones.origen text',
    'devoluciones.sku citext', 'devoluciones.cantidad integer', 'devoluciones.ov_linea_id bigint',
    'devoluciones.canal text', 'devoluciones.cuenta text', 'devoluciones.external_order_id text',
    'devoluciones.external_return_id text', 'devoluciones.paquete_ref text',
    'devoluciones.paquete_ids text[]', 'devoluciones.empaque text', 'devoluciones.foto_path text',
    'devoluciones.dictamen text', 'devoluciones.nota text', 'devoluciones.recibido_por text',
    'devoluciones.recibido_nombre text', 'devoluciones.recibido_at timestamptz',
    'devoluciones.dictamen_por text', 'devoluciones.dictamen_nombre text',
    'devoluciones.dictamen_at timestamptz', 'devoluciones.resuelta_at timestamptz',
    'devoluciones.clave text', 'devoluciones.rev integer'];
  prohibidas constant text[] := array['stock_almacen.otorgado'];
  restricciones constant text[] := array[
    'stock_almacen|stock_almacen_almacen_fk|(almacen, fuente)',
    'stock_formato|stock_formato_conf_chk|btrim',
    'stock_formato|stock_formato_desc_q_chk|descartado_por',
    'stock_formato|stock_formato_hash_chk|0-9a-f',
    'stock_formato_linea|stock_formato_linea_salida_chk|movimiento',
    'stock_formato_linea|stock_formato_linea_uq|NULLS NOT DISTINCT',
    'stock_mov|stock_mov_almacen_fk|(almacen, fuente)',
    'stock_mov|stock_mov_signo_chk|traspaso_entrada',
    'stock_mov|stock_mov_clave_chk|conteo',
    'devoluciones|devoluciones_resuelta_chk|COALESCE',
    'devoluciones|devoluciones_paquete_ids_chk|array_position'];
  indices constant text[] := array[
    'stock_formato|stock_formato_hash_uq|descartado',
    'stock_mov|stock_mov_salida_ov_uq|salida_ov'];
begin
  if to_regclass('ops.almacenes') is null or to_regclass('ops.ov_lineas') is null
     or to_regprocedure('ops.exigir(boolean,text)') is null
     or to_regprocedure('ops.tg_solo_agregar()') is null
     or not exists (select 1 from pg_catalog.pg_attribute a
                     where a.attrelid = to_regclass('ops.almacenes') and a.attname = 'surte_ventas' and not a.attisdropped)
     or not exists (select 1 from pg_catalog.pg_constraint k
                     where k.conrelid = to_regclass('ops.almacenes') and k.conname = 'almacenes_codigo_fuente_uq') then
    raise exception using errcode = 'KB000',
      message = '0065 paso 0: falta la 0064 de esta versión (ops.almacenes con surte_ventas y (codigo, fuente), '
                'ops.ov_lineas, ops.exigir). Aplicar primero la 0064.';
  end if;
  if to_regclass('ops.stock_almacen_foto') is not null or to_regclass('ops.stock_almacen_foto_linea') is not null then
    raise exception using errcode = 'KB000',
      message = '0065 paso 0: existe ops.stock_almacen_foto(_linea), del plan v2 (corte de TEX2), que la v3 retiró.';
  end if;

  foreach e in array columnas loop
    t  := split_part(e, '.', 1);
    c  := split_part(split_part(e, '.', 2), ' ', 1);
    ty := split_part(e, ' ', 2);
    continue when to_regclass('ops.' || t) is null;
    v := null;
    select a.atttypid::regtype into v
      from pg_catalog.pg_attribute a
     where a.attrelid = to_regclass('ops.' || t) and a.attname = c
       and a.attnum > 0 and not a.attisdropped;
    if v is null then
      raise exception using errcode = 'KB000',
        message = format('0065 paso 0: ops.%s ya existe pero sin la columna %s (otra versión de la 0065)', t, c);
    end if;
    if v <> to_regtype(ty) then
      raise exception using errcode = 'KB000',
        message = format('0065 paso 0: ops.%s.%s es %s y se esperaba %s', t, c, v, ty);
    end if;
  end loop;

  foreach e in array prohibidas loop
    t := split_part(e, '.', 1);
    c := split_part(e, '.', 2);
    continue when to_regclass('ops.' || t) is null;
    if exists (select 1 from pg_catalog.pg_attribute a
                where a.attrelid = to_regclass('ops.' || t) and a.attname = c
                  and a.attnum > 0 and not a.attisdropped) then
      raise exception using errcode = 'KB000',
        message = format('0065 paso 0: ops.%s tiene la columna %s de una versión anterior', t, c);
    end if;
  end loop;

  foreach e in array restricciones loop
    t := split_part(e, '|', 1);
    c := split_part(e, '|', 2);
    d := split_part(e, '|', 3);
    continue when to_regclass('ops.' || t) is null;
    if not exists (select 1 from pg_catalog.pg_constraint k
                    where k.conrelid = to_regclass('ops.' || t) and k.conname = c
                      and strpos(pg_catalog.pg_get_constraintdef(k.oid), d) > 0) then
      raise exception using errcode = 'KB000',
        message = format('0065 paso 0: ops.%s no tiene %s con «%s» (otra versión de la 0065)', t, c, d);
    end if;
  end loop;

  foreach e in array indices loop
    t := split_part(e, '|', 1);
    c := split_part(e, '|', 2);
    d := split_part(e, '|', 3);
    continue when to_regclass('ops.' || t) is null;
    if not exists (select 1 from pg_catalog.pg_indexes i
                    where i.schemaname = 'ops' and i.tablename = t and i.indexname = c
                      and strpos(i.indexdef, d) > 0) then
      raise exception using errcode = 'KB000',
        message = format('0065 paso 0: el índice ops.%s no es el parcial de esta versión', c);
    end if;
  end loop;
end $$;

-- ───────────────────────────────────────────────────────────────────────────
-- 1) ops.stock_almacen — el saldo vivo por SKU × bodega de kubera
-- ───────────────────────────────────────────────────────────────────────────
-- Sin FK a core.products, a propósito: los SKUs desconocidos los reporta el
-- cuadre. Sin CHECK apartado <= fisico, a propósito: un conteo puede dejar
-- libre < 0 (la vista avisa). Las filas en 0 se quedan.
create table if not exists ops.stock_almacen (
    sku            citext      not null,
    almacen        text        not null,
    fuente         text        not null default 'kubera',
    fisico         integer     not null default 0,     -- piezas en la bodega: el saldo del libro
    apartado       integer     not null default 0,     -- renglones de OV confirmados sin entregar
    libre          integer     generated always as (fisico - apartado) stored,
    ubicacion      text,                               -- INFORMATIVA: la del último formato o conteo
    actualizado_at timestamptz not null default now(), -- lo mantiene el trigger
    constraint stock_almacen_pkey         primary key (sku, almacen),
    constraint stock_almacen_almacen_fk   foreign key (almacen, fuente) references ops.almacenes (codigo, fuente),
    constraint stock_almacen_fuente_chk   check (fuente = 'kubera'),
    constraint stock_almacen_fisico_chk   check (fisico >= 0),
    constraint stock_almacen_apartado_chk check (apartado >= 0)
);

-- ───────────────────────────────────────────────────────────────────────────
-- 2) ops.stock_formato — un archivo de Bodega (lo que se movió en un lote)
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists ops.stock_formato (
    id                bigint      generated always as identity,
    folio             text        generated always as ('FMT-' || lpad(id::text, greatest(5, length(id::text)), '0')) stored,
    almacen           text        not null,                 -- TEX3 (o ENSAYO para practicar)
    fuente            text        not null default 'kubera',
    estado            text        not null default 'por_confirmar',
    archivo_nombre    text        not null,
    archivo_hash      text        not null,                 -- sha256 del archivo (hex minúsculas)
    archivo_path      text,                                 -- bucket privado 'inventario-kubera'
    reemplaza_a       bigint,
    cargado_por       text        not null,
    cargado_nombre    text,
    cargado_at        timestamptz not null default now(),
    confirmado_por    text,
    confirmado_nombre text,
    confirmado_at     timestamptz,
    confirmo_bodega   text,                                 -- quién de Bodega dijo «listo para usarse» (D9)
    descartado_por    text,
    descartado_at     timestamptz,
    descartado_motivo text,
    rev               integer     not null default 1,
    constraint stock_formato_pkey          primary key (id),
    constraint stock_formato_almacen_fk    foreign key (almacen, fuente) references ops.almacenes (codigo, fuente),
    constraint stock_formato_reemplaza_fk  foreign key (reemplaza_a) references ops.stock_formato (id),
    constraint stock_formato_fuente_chk    check (fuente = 'kubera'),
    constraint stock_formato_estado_chk    check (estado in ('por_confirmar', 'confirmado', 'descartado')),
    constraint stock_formato_nombre_chk    check (length(btrim(archivo_nombre)) > 0),
    constraint stock_formato_hash_chk      check (archivo_hash ~ '^[0-9a-f]{64}$'),
    constraint stock_formato_reemplaza_chk check (reemplaza_a is distinct from id),
    constraint stock_formato_conf_chk      check ((estado = 'confirmado') = (confirmado_at is not null)
                                                  and (confirmado_at is null or coalesce(length(btrim(confirmo_bodega)), 0) > 0)),
    constraint stock_formato_conf_q_chk    check ((confirmado_at is null) = (confirmado_por is null)),
    constraint stock_formato_desc_chk      check (estado <> 'descartado' or length(coalesce(descartado_motivo, '')) >= 5),
    constraint stock_formato_desc_q_chk    check ((estado = 'descartado') = (descartado_at is not null)
                                                  and (descartado_at is null) = (descartado_por is null)),
    constraint stock_formato_rev_chk       check (rev >= 1)
);
-- El mismo archivo no entra dos veces mientras su formato siga vivo.
create unique index if not exists stock_formato_hash_uq on ops.stock_formato (archivo_hash) where estado <> 'descartado';
create index if not exists stock_formato_reemplaza_ix on ops.stock_formato (reemplaza_a) where reemplaza_a is not null;

-- ───────────────────────────────────────────────────────────────────────────
-- 3) ops.stock_formato_linea — el renglón, con la PUERTA como dato
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists ops.stock_formato_linea (
    id                     bigint        generated always as identity,
    formato_id             bigint        not null,
    fila                   integer       not null,        -- renglón del archivo, para hablar con Bodega
    sku                    citext        not null,        -- el código ACTIVO al que se mapeó
    sku_archivo            text          not null,        -- el código TAL COMO venía en el archivo (fijo)
    cantidad               integer       not null,        -- la vigente (puede bajar mientras espera)
    cantidad_archivo       integer       not null,        -- lo que decía el archivo (fijo)
    ubicacion              text,
    nota                   text,                          -- por qué cambió la cantidad
    odoo_tex2_al_cargar    numeric(14,3),                 -- registro: qty_available de TEX2 al cargar
    odoo_tex2_al_confirmar numeric(14,3),                 -- registro: y al confirmar
    odoo_total_al_cargar   numeric(14,3),                 -- registro: free_qty total de Odoo al cargar
    salida_odoo_at         timestamptz,                   -- PUERTA: cuándo se vio la salida de TEX2 (NULL = esperando)
    salida_odoo_via        text,
    salida_odoo_ref        text,                          -- referencia del movimiento en Odoo, si la hubo
    odoo_tex2_al_abrir     numeric(14,3),                 -- registro: qty_available de TEX2 al abrir la puerta
    aviso                  text,                          -- 'archivado en Odoo', 'cantidad atípica', 'riesgo D', …
    constraint stock_formato_linea_pkey        primary key (id),
    constraint stock_formato_linea_formato_fk  foreign key (formato_id) references ops.stock_formato (id),   -- sin cascade
    constraint stock_formato_linea_uq          unique nulls not distinct (formato_id, sku, ubicacion),
    constraint stock_formato_linea_fila_chk    check (fila > 0),
    constraint stock_formato_linea_cantidad_chk check (cantidad > 0),
    constraint stock_formato_linea_cant_arch_chk check (cantidad_archivo > 0),
    constraint stock_formato_linea_sku_arch_chk check (length(btrim(sku_archivo)) > 0),
    constraint stock_formato_linea_via_chk     check (salida_odoo_via in ('tex2_bajo', 'tex2_cero', 'movimiento')),
    constraint stock_formato_linea_salida_chk  check ((salida_odoo_at is null) = (salida_odoo_via is null)
                                                      and (salida_odoo_via is distinct from 'movimiento' or salida_odoo_ref is not null))
);
-- Los renglones que esperan su salida en Odoo: la puerta y `tras` (R5).
create index if not exists stock_formato_linea_espera_ix on ops.stock_formato_linea (formato_id) where salida_odoo_at is null;

-- ───────────────────────────────────────────────────────────────────────────
-- 4) ops.stock_formato_evento — la huella de cada operación sobre un formato
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists ops.stock_formato_evento (
    id         bigint      generated always as identity,
    formato_id bigint      not null,
    linea_id   bigint,                                -- sin FK: el renglón pudo quitarse
    evento     text        not null,
    antes      jsonb,
    despues    jsonb,
    nota       text,
    quien      text        not null,
    creado_at  timestamptz not null default now(),
    constraint stock_formato_evento_pkey       primary key (id),
    constraint stock_formato_evento_formato_fk foreign key (formato_id) references ops.stock_formato (id),
    constraint stock_formato_evento_evento_chk check (evento in (
        'cargado', 'renglon_editado', 'renglon_quitado', 'dividido', 'reemplazado',
        'descartado', 'confirmado', 'puerta_abierta', 'cantidad_bajada')),
    constraint stock_formato_evento_nota_chk   check (evento <> 'cantidad_bajada' or length(coalesce(nota, '')) >= 5)
);
create index if not exists stock_formato_evento_fmt_ix on ops.stock_formato_evento (formato_id, id);

-- ───────────────────────────────────────────────────────────────────────────
-- 5) ops.stock_mov — EL LIBRO: cada cambio del físico, solo se agrega
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists ops.stock_mov (
    id            bigint      generated always as identity,
    sku           citext      not null,
    almacen       text        not null,
    fuente        text        not null default 'kubera',
    delta         integer     not null,
    saldo_despues integer     not null,
    motivo        text        not null,
    ref           text,                                -- folio FMT-…, OV-…, DEV-…, retiro de FULL
    clave         text        not null,                -- idempotencia: forma por motivo (ver comment)
    ov_linea_id   bigint,
    nota          text,
    quien         text        not null,
    quien_nombre  text,
    via           text,
    creado_at     timestamptz not null default now(),
    constraint stock_mov_pkey        primary key (id),
    constraint stock_mov_clave_uq    unique (clave),
    constraint stock_mov_almacen_fk  foreign key (almacen, fuente) references ops.almacenes (codigo, fuente),
    constraint stock_mov_ov_linea_fk foreign key (ov_linea_id) references ops.ov_lineas (id),
    constraint stock_mov_fuente_chk  check (fuente = 'kubera'),
    constraint stock_mov_delta_chk   check (delta <> 0),
    constraint stock_mov_saldo_chk   check (saldo_despues >= 0),
    constraint stock_mov_motivo_chk  check (motivo in ('entrada', 'salida_ov', 'traspaso_salida', 'traspaso_entrada',
                                                       'devolucion', 'ajuste_conteo', 'merma', 'correccion')),
    constraint stock_mov_signo_chk   check (
        (motivo in ('entrada', 'traspaso_entrada', 'devolucion') and delta > 0)
     or (motivo in ('salida_ov', 'traspaso_salida', 'merma') and delta < 0)
     or  motivo in ('ajuste_conteo', 'correccion')),
    constraint stock_mov_nota_chk    check (motivo not in ('ajuste_conteo', 'merma', 'correccion')
                                            or length(coalesce(nota, '')) >= 5),
    constraint stock_mov_ref_chk     check (motivo <> 'entrada' or ref is not null),
    constraint stock_mov_ov_chk      check (motivo <> 'salida_ov' or ov_linea_id is not null),
    -- La clave sale del HECHO de negocio y tiene una forma por motivo (§6.1 punto 6)
    constraint stock_mov_clave_chk   check (case motivo
        when 'entrada'          then clave ~ '^(fmt:[0-9]+:.+|dev:[0-9]+:recibe)$'
        when 'salida_ov'        then clave ~ '^ov:[0-9]+:linea:[0-9]+:salida$'
        when 'traspaso_salida'  then clave ~ '^(dev:[0-9]+|tras:.+):sale$'
        when 'traspaso_entrada' then clave ~ '^(dev:[0-9]+|tras:.+):entra$'
        when 'devolucion'       then clave ~ '^dev:[0-9]+:recibe$'
        when 'ajuste_conteo'    then clave ~ '^conteo:[^:]+:[^:]+:[^:]+$'
        when 'merma'            then clave ~ '^(dev:[0-9]+:merma|merma:.+)$'
        when 'correccion'       then clave ~ '^corr:.+$'
      end)
);
-- Por patrón de lectura (R1, R7): el cuadre por llave, la liga con el renglón,
-- las entradas recientes de `entro`, y una sola salida por renglón (H10).
create index if not exists stock_mov_sku_alm_id_ix on ops.stock_mov (sku, almacen, id);
create index if not exists stock_mov_ov_linea_ix   on ops.stock_mov (ov_linea_id) where ov_linea_id is not null;
create index if not exists stock_mov_entradas_ix   on ops.stock_mov (creado_at) where motivo = 'entrada';
create unique index if not exists stock_mov_salida_ov_uq on ops.stock_mov (ov_linea_id) where motivo = 'salida_ov';

-- ───────────────────────────────────────────────────────────────────────────
-- 6) ops.devoluciones — una fila por SKU y dictamen de cada paquete en REVISION
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists ops.devoluciones (
    id                 bigint      generated always as identity,
    folio              text        generated always as ('DEV-' || lpad(id::text, greatest(5, length(id::text)), '0')) stored,
    origen             text        not null,              -- venta | retiro_full (sin 'sin_venta', D-R15)
    sku                citext      not null,
    cantidad           integer     not null,
    ov_linea_id        bigint,                            -- el renglón de la OV (origen 'venta')
    canal              text,                              -- liga con channel.returns, sin FK (como la 0049)
    cuenta             text,
    external_order_id  text,                              -- la venta en el canal (copia del mp_* de la OV)
    external_return_id text,                              -- ML: el claim; TikTok: return_id
    paquete_ref        text        not null,              -- lo que trae el paquete, tal cual: guía o id. NUNCA nombre ni dirección
    paquete_ids        text[]      not null,              -- paquete_ref normalizado: mayúsculas, sin espacios ni guiones, un id por elemento
    empaque            text        not null,
    foto_path          text,                              -- opcional; la PIEZA, nunca la etiqueta (D5)
    dictamen           text,                              -- NULL = en revisión
    nota               text,
    recibido_por       text        not null,
    recibido_nombre    text,
    recibido_at        timestamptz not null default now(),
    dictamen_por       text,
    dictamen_nombre    text,
    dictamen_at        timestamptz,
    resuelta_at        timestamptz,                       -- salió de REVISION (traspaso o merma)
    clave              text        not null,              -- anti doble clic de la pantalla (uuid al abrir)
    rev                integer     not null default 1,
    constraint devoluciones_pkey          primary key (id),
    constraint devoluciones_clave_uq      unique (clave),
    constraint devoluciones_ov_linea_fk   foreign key (ov_linea_id) references ops.ov_lineas (id),
    constraint devoluciones_origen_chk    check (origen in ('venta', 'retiro_full')),
    constraint devoluciones_cantidad_chk  check (cantidad > 0),
    constraint devoluciones_paquete_ref_chk check (length(btrim(paquete_ref)) >= 4),
    constraint devoluciones_paquete_ids_chk check (cardinality(paquete_ids) >= 1
                                                   and array_position(paquete_ids, null) is null
                                                   and array_to_string(paquete_ids, ',') ~ '^[A-Z0-9]+(,[A-Z0-9]+)*$'),
    constraint devoluciones_empaque_chk   check (empaque in ('cerrado', 'abierto', 'danado', 'sin_empaque')),
    constraint devoluciones_dictamen_v_chk check (dictamen is null or dictamen in ('vendible', 'merma', 'reparar', 'proveedor')),
    constraint devoluciones_venta_chk     check ((origen = 'venta') = (ov_linea_id is not null)),
    constraint devoluciones_retiro_chk    check (origen <> 'retiro_full' or (canal is not null and cuenta is not null)),
    constraint devoluciones_nota_chk      check (origen = 'venta' or length(coalesce(nota, '')) >= 5),
    constraint devoluciones_dictamen_chk  check ((dictamen is null) = (dictamen_at is null)
                                                 and (dictamen_at is null) = (dictamen_por is null)),
    -- coalesce: con dictamen NULL, `dictamen in (…)` da desconocido y el CHECK pasaría
    constraint devoluciones_resuelta_chk  check (resuelta_at is null
                                                 or coalesce(dictamen in ('vendible', 'merma', 'proveedor'), false)),
    constraint devoluciones_clave_chk     check (length(btrim(clave)) >= 8),
    constraint devoluciones_rev_chk       check (rev >= 1)
);
create index if not exists devoluciones_abiertas_ix on ops.devoluciones (recibido_at) where resuelta_at is null;
create index if not exists devoluciones_paquete_ix  on ops.devoluciones using gin (paquete_ids);
create index if not exists devoluciones_ov_linea_ix on ops.devoluciones (ov_linea_id) where ov_linea_id is not null;

-- ───────────────────────────────────────────────────────────────────────────
-- 7) Triggers de fila: guardas y solo agregar
-- ───────────────────────────────────────────────────────────────────────────

-- El saldo: llave fija; subir el apartado no deja libre < 0 y solo en kubera
-- con admite_ov (C8 + D11); actualizado_at por trigger.
create or replace function ops.tg_stock_almacen_guarda() returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  if tg_op = 'UPDATE' and (new.sku, new.almacen, new.fuente) is distinct from (old.sku, old.almacen, old.fuente) then
    raise exception using errcode = '42501', constraint = 'stock_almacen_llave_fija',
      message = format('stock_almacen %s/%s: sku y bodega son fijos (un traspaso son dos movimientos)', old.sku, old.almacen);
  end if;
  if new.apartado > coalesce(old.apartado, 0) then
    if new.fisico - new.apartado < 0 then          -- `libre` todavía no está calculado en un BEFORE
      raise exception using errcode = '23514', constraint = 'stock_almacen_libre_guarda',
        message = format('apartar %s/%s dejaría libre < 0 (fisico %s, apartado %s)', new.sku, new.almacen, new.fisico, new.apartado);
    end if;
    if not exists (select 1 from ops.almacenes a
                    where a.codigo = new.almacen and a.fuente = 'kubera' and a.admite_ov) then
      raise exception using errcode = '23514', constraint = 'stock_almacen_admite_ov_guarda',
        message = format('apartado en %s: solo se aparta en una bodega de kubera con admite_ov', new.almacen);
    end if;
  end if;
  new.actualizado_at := now();
  return new;
end $$;

drop trigger if exists stock_almacen_guarda on ops.stock_almacen;
create trigger stock_almacen_guarda before insert or update on ops.stock_almacen
  for each row execute function ops.tg_stock_almacen_guarda();

-- El formato: nace por_confirmar; confirmado y descartado son terminales.
create or replace function ops.tg_stock_formato_guarda() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', constraint = 'stock_formato_sin_borrado',
      message = format('formato %s: no se borra; se descarta con motivo', old.id);
  end if;
  if tg_op = 'INSERT' then
    if new.estado <> 'por_confirmar' then
      raise exception using errcode = '23514', constraint = 'stock_formato_transicion_chk',
        message = format('un formato nace por_confirmar, no %s', new.estado);
    end if;
    return new;
  end if;
  -- `folio` es generada: en un BEFORE todavía no tiene valor, se deja fuera.
  if old.estado <> 'por_confirmar' then
    if (to_jsonb(new) - 'folio') is distinct from (to_jsonb(old) - 'folio') then
      raise exception using errcode = '42501', constraint = 'stock_formato_inmutable',
        message = format('formato %s está %s: es terminal y no cambia', old.id, old.estado);
    end if;
    return new;
  end if;
  if (new.id, new.archivo_hash, new.cargado_por, new.cargado_at)
     is distinct from (old.id, old.archivo_hash, old.cargado_por, old.cargado_at) then
    raise exception using errcode = '42501', constraint = 'stock_formato_inmutable',
      message = format('formato %s: id, archivo y carga son fijos (otro archivo = reemplazar)', old.id);
  end if;
  return new;   -- por_confirmar → por_confirmar | confirmado | descartado (la CHECK de estado cierra el resto)
end $$;

drop trigger if exists stock_formato_guarda on ops.stock_formato;
create trigger stock_formato_guarda before insert or update or delete on ops.stock_formato
  for each row execute function ops.tg_stock_formato_guarda();

-- El renglón del formato. Lee el estado de su formato: en las sentencias del
-- plan el formato o no cambia (puerta, bajar cantidad) o cambia a confirmado y
-- el renglón solo recibe odoo_tex2_al_confirmar, que se admite en los dos estados.
create or replace function ops.tg_stock_formato_linea_guarda() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  permitidas constant text[] := array['cantidad', 'nota', 'odoo_tex2_al_confirmar',
                                      'salida_odoo_at', 'salida_odoo_via', 'salida_odoo_ref', 'odoo_tex2_al_abrir'];
  v_estado text;
begin
  if tg_op = 'INSERT' then
    select f.estado into v_estado from ops.stock_formato f where f.id = new.formato_id;
    -- NULL: el encabezado nace en la misma sentencia (la carga); la FK lo revisa.
    if v_estado is not null and v_estado <> 'por_confirmar' then
      raise exception using errcode = '42501', constraint = 'stock_formato_linea_inmutable',
        message = format('formato %s está %s: no se le agregan renglones', new.formato_id, v_estado);
    end if;
    return new;
  end if;

  if old.salida_odoo_at is not null then
    raise exception using errcode = '42501', constraint = 'stock_formato_linea_inmutable',
      message = format('renglón %s ya entró al libro (puerta abierta): no se edita ni se borra', old.id);
  end if;

  select f.estado into v_estado from ops.stock_formato f where f.id = old.formato_id;

  if tg_op = 'DELETE' then
    if coalesce(v_estado, 'por_confirmar') <> 'por_confirmar' then
      raise exception using errcode = '42501', constraint = 'stock_formato_linea_inmutable',
        message = format('renglón %s de un formato %s: no se borra', old.id, v_estado);
    end if;
    return old;
  end if;

  if (new.id, new.formato_id, new.fila, new.sku_archivo, new.cantidad_archivo)
     is distinct from (old.id, old.formato_id, old.fila, old.sku_archivo, old.cantidad_archivo) then
    raise exception using errcode = '42501', constraint = 'stock_formato_linea_inmutable',
      message = format('renglón %s: formato, fila y lo que decía el archivo son fijos', old.id);
  end if;

  if coalesce(v_estado, 'por_confirmar') = 'por_confirmar' then
    return new;
  end if;
  if v_estado = 'descartado' then
    raise exception using errcode = '42501', constraint = 'stock_formato_linea_inmutable',
      message = format('renglón %s de un formato descartado: no cambia', old.id);
  end if;
  -- confirmado, esperando su salida en Odoo
  if (to_jsonb(new) - permitidas) is distinct from (to_jsonb(old) - permitidas) then
    raise exception using errcode = '42501', constraint = 'stock_formato_linea_inmutable',
      message = format('renglón %s de un formato confirmado: solo cambian %s', old.id, array_to_string(permitidas, ', '));
  end if;
  if new.cantidad > old.cantidad then
    raise exception using errcode = '23514', constraint = 'stock_formato_linea_solo_baja',
      message = format('renglón %s de un formato confirmado: la cantidad solo BAJA (%s → %s)', old.id, old.cantidad, new.cantidad);
  end if;
  if new.cantidad < old.cantidad
     and (length(btrim(coalesce(new.nota, ''))) < 5 or new.nota is not distinct from old.nota) then
    raise exception using errcode = '23514', constraint = 'stock_formato_linea_nota_chk',
      message = format('renglón %s: bajar la cantidad pide una nota nueva de 5 caracteres o más', old.id);
  end if;
  return new;
end $$;

drop trigger if exists stock_formato_linea_guarda on ops.stock_formato_linea;
create trigger stock_formato_linea_guarda before insert or update or delete on ops.stock_formato_linea
  for each row execute function ops.tg_stock_formato_linea_guarda();

drop trigger if exists stock_mov_solo_agregar on ops.stock_mov;
create trigger stock_mov_solo_agregar before update or delete on ops.stock_mov
  for each row execute function ops.tg_solo_agregar();
drop trigger if exists stock_mov_sin_truncate on ops.stock_mov;
create trigger stock_mov_sin_truncate before truncate on ops.stock_mov
  for each statement execute function ops.tg_solo_agregar();
drop trigger if exists stock_formato_evento_solo_agregar on ops.stock_formato_evento;
create trigger stock_formato_evento_solo_agregar before update or delete on ops.stock_formato_evento
  for each row execute function ops.tg_solo_agregar();
drop trigger if exists stock_formato_evento_sin_truncate on ops.stock_formato_evento;
create trigger stock_formato_evento_sin_truncate before truncate on ops.stock_formato_evento
  for each statement execute function ops.tg_solo_agregar();

-- ───────────────────────────────────────────────────────────────────────────
-- 8) Invariantes entre tablas, al COMMIT (constraint triggers diferidos)
-- ───────────────────────────────────────────────────────────────────────────
-- Por qué funcionan con las sentencias de un solo WITH: corren al COMMIT, con
-- todas las escrituras hechas. «El último movimiento por id» es válido porque
-- todo escritor bloquea antes la fila de saldo y la tiene hasta el commit: los
-- id de una misma (sku, bodega) salen en orden y nadie más la mueve mientras se
-- verifica. Para forzar la revisión antes (pruebas): SET CONSTRAINTS ALL IMMEDIATE.

-- (a) SALDO = LIBRO. Una consulta: una sola foto.
create or replace function ops.verificar_libro(p_sku citext, p_alm text) returns void
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  v record;
begin
  select coalesce((select sa.fisico from ops.stock_almacen sa where sa.sku = p_sku and sa.almacen = p_alm), 0) as fisico,
         coalesce((select sum(m.delta) from ops.stock_mov m where m.sku = p_sku and m.almacen = p_alm), 0) as suma,
         coalesce((select m.saldo_despues from ops.stock_mov m where m.sku = p_sku and m.almacen = p_alm
                    order by m.id desc limit 1), 0) as ultimo
    into v;
  if v.fisico <> v.suma or v.fisico <> v.ultimo then
    raise exception using errcode = '23514', constraint = 'stock_libro_cuadra',
      message = format('libro descuadrado %s/%s: fisico=%s suma=%s ultimo=%s', p_sku, p_alm, v.fisico, v.suma, v.ultimo);
  end if;
end $$;

create or replace function ops.tg_libro_cuadra() returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  v_prev integer;
begin
  if tg_table_name = 'stock_mov' then
    -- La cadena, movimiento por movimiento: saldo_despues = anterior + delta.
    select m.saldo_despues into v_prev from ops.stock_mov m
     where m.sku = new.sku and m.almacen = new.almacen and m.id < new.id
     order by m.id desc limit 1;
    if new.saldo_despues <> coalesce(v_prev, 0) + new.delta then
      raise exception using errcode = '23514', constraint = 'stock_libro_cadena',
        message = format('libro %s/%s, movimiento %s: saldo_despues %s <> anterior %s + delta %s',
                         new.sku, new.almacen, new.id, new.saldo_despues, coalesce(v_prev, 0), new.delta);
    end if;
    perform ops.verificar_libro(new.sku, new.almacen);
  elsif tg_op = 'DELETE' then
    perform ops.verificar_libro(old.sku, old.almacen);
  else
    perform ops.verificar_libro(new.sku, new.almacen);
  end if;
  return null;
end $$;

drop trigger if exists stock_mov_cuadra on ops.stock_mov;
create constraint trigger stock_mov_cuadra after insert on ops.stock_mov
  deferrable initially deferred for each row execute function ops.tg_libro_cuadra();
drop trigger if exists stock_almacen_cuadra on ops.stock_almacen;
create constraint trigger stock_almacen_cuadra after insert or update of fisico or delete on ops.stock_almacen
  deferrable initially deferred for each row execute function ops.tg_libro_cuadra();

-- (b) APARTADO = Σ RESERVADO. Versión de la revisión (§4, hallazgo 2): UNA
-- consulta y solo los renglones que apartan; 'entregado_at is null' deja usar
-- el índice parcial.
create or replace function ops.verificar_apartado(p_sku citext, p_alm text) returns void
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  v record;
begin
  if p_alm is null then
    return;
  end if;
  select coalesce((select sa.apartado from ops.stock_almacen sa
                    where sa.sku = p_sku and sa.almacen = p_alm), 0) as ap,
         coalesce(sum(l.reservado) filter (where o.estado = 'confirmada' and o.borrada_at is null), 0) as res,
         count(*) filter (where o.estado <> 'confirmada' or o.borrada_at is not null) as malos
    into v
    from ops.ov_lineas l
    join ops.ov_ordenes o on o.id = l.orden_id
   where l.sku = p_sku and l.almacen = p_alm
     and l.reservado > 0 and l.entregado_at is null;
  if v.ap <> v.res or v.malos > 0 then
    raise exception using errcode = '23514', constraint = 'stock_apartado_cuadra',
      message = format('apartado descuadrado %s/%s: saldo=%s renglones=%s en_no_confirmadas=%s',
                       p_sku, p_alm, v.ap, v.res, v.malos);
  end if;
end $$;

create or replace function ops.tg_apartado_cuadra() returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  r record;
begin
  if tg_table_name = 'ov_ordenes' then
    for r in select distinct l.sku, l.almacen from ops.ov_lineas l
              where l.orden_id = new.id and l.reservado > 0 loop
      perform ops.verificar_apartado(r.sku, r.almacen);
    end loop;
  elsif tg_table_name = 'stock_almacen' then
    if tg_op = 'DELETE' then
      perform ops.verificar_apartado(old.sku, old.almacen);
    else
      perform ops.verificar_apartado(new.sku, new.almacen);
    end if;
  else                                   -- ov_lineas: un renglón de borrador tiene 0 y no se revisa
    if tg_op in ('UPDATE', 'DELETE') and old.reservado > 0 then
      perform ops.verificar_apartado(old.sku, old.almacen);
    end if;
    if tg_op in ('INSERT', 'UPDATE') and new.reservado > 0 then
      perform ops.verificar_apartado(new.sku, new.almacen);
    end if;
  end if;
  return null;
end $$;

drop trigger if exists stock_almacen_apartado_cuadra on ops.stock_almacen;
create constraint trigger stock_almacen_apartado_cuadra after insert or update of apartado or delete on ops.stock_almacen
  deferrable initially deferred for each row execute function ops.tg_apartado_cuadra();
drop trigger if exists ov_lineas_apartado_cuadra on ops.ov_lineas;
create constraint trigger ov_lineas_apartado_cuadra after insert or delete or update of reservado, sku, almacen on ops.ov_lineas
  deferrable initially deferred for each row execute function ops.tg_apartado_cuadra();
drop trigger if exists ov_ordenes_apartado_cuadra on ops.ov_ordenes;
create constraint trigger ov_ordenes_apartado_cuadra after update of estado, borrada_at on ops.ov_ordenes
  deferrable initially deferred for each row execute function ops.tg_apartado_cuadra();

-- ───────────────────────────────────────────────────────────────────────────
-- 9) La vista vigía: solo devuelve filas cuando algo NO cuadra
-- ───────────────────────────────────────────────────────────────────────────
-- Con los constraint triggers, lo transaccional ya no puede descuadrarse: la
-- vista queda como segunda red y para lo legítimo pero raro (libre < 0 tras un
-- conteo, un formato o REVISION que no cuadran, devoluciones de más).
drop view if exists ops.stock_apartado_descuadre_v;
create view ops.stock_apartado_descuadre_v with (security_invoker = on) as
with r as (            -- lo que apartan los renglones, por (sku, bodega)
  select l.sku, l.almacen,
         sum(l.reservado)::int as en_renglones,
         coalesce(sum(l.reservado) filter (where o.borrada_at is not null or o.estado <> 'confirmada'), 0)::int
           as en_ov_no_confirmadas
    from ops.ov_lineas l
    join ops.ov_ordenes o on o.id = l.orden_id
   where l.reservado > 0
   group by l.sku, l.almacen
), sa as (
  select s.sku, s.almacen, s.fisico, s.apartado, s.libre, a.admite_ov
    from ops.stock_almacen s
    join ops.almacenes a on a.codigo = s.almacen
), dv as (             -- lo devuelto por renglón de OV
  select m.ov_linea_id, sum(m.delta)::int as devueltas
    from ops.stock_mov m
   where m.motivo = 'devolucion' and m.ov_linea_id is not null
   group by m.ov_linea_id
), fe as (             -- formato confirmado: Σ entradas con su folio contra Σ renglones con la puerta abierta
  select f.folio, f.almacen,
         coalesce((select sum(l.cantidad) from ops.stock_formato_linea l
                    where l.formato_id = f.id and l.salida_odoo_at is not null), 0)::int as abiertos,
         coalesce((select sum(m.delta) from ops.stock_mov m
                    where m.motivo = 'entrada' and m.ref = f.folio and m.almacen = f.almacen), 0)::int as entraron
    from ops.stock_formato f
   where f.estado = 'confirmado'
), rv as (             -- REVISION: físico por SKU contra las devoluciones abiertas
  select coalesce(s.sku, d.sku) as sku, coalesce(s.fisico, 0) as fisico, coalesce(d.n, 0) as abiertas
    from (select x.sku, x.fisico from ops.stock_almacen x where x.almacen = 'REVISION') s
    full join (select y.sku, sum(y.cantidad)::int as n from ops.devoluciones y
                where y.resuelta_at is null group by y.sku) d
      on d.sku = s.sku
), rc as (             -- lo recibido por devolución del canal contra lo que el canal dice que vuelve
  select d.canal, d.cuenta, d.external_return_id, d.sku, sum(d.cantidad)::int as recibido,
         (select sum(ri.cantidad) from channel.return_items ri
           where ri.canal = d.canal and ri.cuenta = d.cuenta
             and ri.external_return_id = d.external_return_id and ri.sku = d.sku)::int as del_canal
    from ops.devoluciones d
   where d.external_return_id is not null
   group by d.canal, d.cuenta, d.external_return_id, d.sku
)
select 'apartado_descuadrado'::text as problema, coalesce(sa.sku, r.sku) as sku,
       coalesce(sa.almacen, r.almacen) as almacen, null::text as ref,
       coalesce(r.en_renglones, 0) as esperado, coalesce(sa.apartado, 0) as encontrado,
       jsonb_build_object('fisico', sa.fisico, 'libre', sa.libre) as detalle
  from sa
  full join r on r.sku = sa.sku and r.almacen = sa.almacen
 where coalesce(sa.apartado, 0) <> coalesce(r.en_renglones, 0)
union all
select 'apartado_en_ov_no_confirmada', r.sku, r.almacen, null, 0, r.en_ov_no_confirmadas, null
  from r
 where r.en_ov_no_confirmadas > 0
union all
select 'libre_negativo', sa.sku, sa.almacen, null, 0, sa.libre,
       jsonb_build_object('fisico', sa.fisico, 'apartado', sa.apartado)
  from sa
 where sa.libre < 0
union all
select 'apartado_sin_admite_ov', sa.sku, sa.almacen, null, 0, sa.apartado, null
  from sa
 where not sa.admite_ov and sa.apartado > 0
union all
select 'devolucion_de_mas', l.sku, l.almacen, 'ov_linea:' || l.id, coalesce(l.entregado, 0), dv.devueltas, null
  from dv
  join ops.ov_lineas l on l.id = dv.ov_linea_id
 where dv.devueltas > coalesce(l.entregado, 0)
union all
select 'formato_sin_cuadrar', null, fe.almacen, fe.folio, fe.abiertos, fe.entraron, null
  from fe
 where fe.abiertos <> fe.entraron
union all
select 'revision_descuadrada', rv.sku, 'REVISION', null, rv.abiertas, rv.fisico, null
  from rv
 where rv.fisico <> rv.abiertas
union all
select 'devuelto_de_mas_vs_canal', rc.sku, 'REVISION', rc.canal || ':' || rc.cuenta || ':' || rc.external_return_id,
       rc.del_canal, rc.recibido, null
  from rc
 where rc.del_canal is not null and rc.recibido > rc.del_canal;

-- `create view` con su invoker inline; lo repetimos por la regla de la casa
-- (un replace futuro despoja la vista; CI lo vigila).
alter view ops.stock_apartado_descuadre_v set (security_invoker = on);

-- ───────────────────────────────────────────────────────────────────────────
-- 10) Comentarios
-- ───────────────────────────────────────────────────────────────────────────
comment on table ops.stock_almacen is
  'Saldo vivo por SKU × bodega de KUBERA: fisico (el saldo del libro), apartado (renglones de OV confirmados sin '
  'entregar) y libre = fisico − apartado (generada). Solo bodegas kubera (FK compuesta). Al COMMIT: fisico = libro '
  '(stock_libro_cuadra) y apartado = Σ reservado (stock_apartado_cuadra). Sin FK a core.products a propósito.';
comment on column ops.stock_almacen.libre is
  'fisico − apartado, calculada por la base. Puede ser < 0 tras un conteo (legítimo; la vista vigía avisa). '
  'Apartar nunca la deja < 0 (stock_almacen_libre_guarda).';
comment on column ops.stock_almacen.ubicacion is
  'INFORMATIVA: dónde buscar (la del último formato o conteo). El saldo es por SKU × bodega (D6).';
comment on table ops.stock_formato is
  'Un archivo de Bodega: lo MOVIDO en un lote (D8). por_confirmar (no se vende) → confirmado (Bodega dijo «listo») '
  'o descartado; los dos son terminales. Un renglón confirmado entra al libro cuando además Odoo registró su salida '
  'de TEX2 (la puerta). Escritor único: services/inventario_libro.py.';
comment on column ops.stock_formato.confirmo_bodega is
  'Quién de Bodega dijo «listo para usarse» (D9: texto no vacío). Pendiente SEG-12: permiso puntual y FK a core.usuarios.';
comment on column ops.stock_formato.archivo_hash is
  'sha256 del archivo en hex minúsculas. Único entre formatos no descartados (stock_formato_hash_uq). Limitación '
  'conocida (H05): Excel puede volver a guardar el mismo lote con otro hash.';
comment on table ops.stock_formato_linea is
  'Renglón del formato. La PUERTA es dato: salida_odoo_at NULL = esperando la salida de TEX2 en Odoo. Las lecturas '
  'de Odoo (odoo_*) son solo registro. Con la puerta abierta el renglón se congela; en un formato confirmado solo '
  'puede BAJAR su cantidad, con nota, mientras espera.';
comment on column ops.stock_formato_linea.salida_odoo_at is
  'PUERTA: cuándo se vio en Odoo (solo lectura) la salida de TEX2. NULL = esperando: no está en stock_almacen y se '
  'sigue vendiendo por Odoo, una sola vez. Va junto con salida_odoo_via (y _ref si es ''movimiento'').';
comment on column ops.stock_formato_linea.cantidad_archivo is
  'Lo que decía el archivo. Fijo. La vigente es `cantidad`; la diferencia la explica `nota` y stock_formato_evento.';
comment on column ops.stock_formato_linea.sku_archivo is
  'El código TAL COMO venía en el archivo (p. ej. un archivado de Odoo mapeado a su código activo en `sku`). Fijo.';
comment on column ops.stock_formato_linea.odoo_tex2_al_cargar is
  'Registro: qty_available de TEX2 en Odoo al cargar. numeric porque Odoo da float (H06).';
comment on table ops.stock_formato_evento is
  'Huella de cada operación sobre un formato y sus renglones (cargar, editar, quitar, dividir, reemplazar, descartar, '
  'confirmar, puerta, bajar cantidad): quién, cuándo, antes y después. Solo se agrega.';
comment on table ops.stock_mov is
  'EL LIBRO de kubera: cada cambio del físico, con delta, saldo_despues y una clave única. Solo se agrega (trigger). '
  'Se escribe en la MISMA sentencia que cambia stock_almacen. Después de la fase B es el ÚNICO registro de TEX3: '
  'RPO de un día (respaldo diario, sin PITR).';
comment on column ops.stock_mov.clave is
  'Idempotencia, del HECHO de negocio; forma por motivo (stock_mov_clave_chk): '
  'entrada fmt:<formato_id>:<sku> | dev:<devolucion_id>:recibe (retiro de FULL); '
  'salida_ov ov:<orden_id>:linea:<linea_id>:salida (la misma para entregar y para «¿salió?»); '
  'devolucion dev:<devolucion_id>:recibe; traspaso dev:<id>:sale|entra o tras:<uuid al abrir>:sale|entra; '
  'ajuste_conteo conteo:<sesion>:<sku>:<almacen>; merma dev:<id>:merma | merma:<uuid al abrir>; '
  'correccion corr:<uuid al abrir el formulario>.';
comment on column ops.stock_mov.saldo_despues is
  'El físico de (sku, bodega) después de este movimiento. Al COMMIT: saldo_despues = anterior + delta, y el último = '
  'stock_almacen.fisico = Σ delta.';
comment on table ops.devoluciones is
  'Una fila por SKU y dictamen de cada paquete que entra a REVISION (DEVOLUCIONES §4): la liga con la OV o el retiro de '
  'FULL, el empaque, el dictamen, los plazos y las llaves para cruzar con Odoo y channel.returns. Cada evento (recibir, '
  'dictaminar, resolver) escribe esta fila, el saldo, el libro y el estado de la OV en una sentencia.';
comment on column ops.devoluciones.clave is
  'Anti doble clic: el uuid de la pantalla al abrir. Los movimientos del libro usan dev:<id>:recibe|sale|entra|merma. '
  'La recaptura del mismo paquete la frena la sentencia de recibir (Σ devuelto + n ≤ entregado).';
comment on column ops.devoluciones.paquete_ids is
  'paquete_ref normalizado (mayúsculas, sin espacios ni guiones), un id por elemento: el cruce diario contra el folio '
  'de iFull en Odoo. Solo ids, nunca nombre ni dirección.';
comment on view ops.stock_apartado_descuadre_v is
  'Vigía: solo devuelve filas cuando algo no cuadra (apartado vs renglones, apartado en OV no confirmadas, libre < 0, '
  'apartado sin admite_ov, devolución de más, formato confirmado sin cuadrar con el libro, REVISION vs devoluciones '
  'abiertas, recibido de más contra channel.return_items). Aviso cada 15 min si da ≥ 1 fila.';
comment on function ops.verificar_libro(citext, text) is
  'Saldo = libro para (sku, bodega), en una sola foto: fisico = Σ delta = saldo_despues del último. SQLSTATE 23514, '
  'constraint stock_libro_cuadra.';
comment on function ops.verificar_apartado(citext, text) is
  'Apartado = Σ reservado de renglones sin entregar de OV confirmadas vivas, y nada apartado en OV no confirmadas, '
  'para (sku, bodega), en una sola foto. SQLSTATE 23514, constraint stock_apartado_cuadra.';

-- ───────────────────────────────────────────────────────────────────────────
-- 11) El candado, donde nace el objeto
-- ───────────────────────────────────────────────────────────────────────────
alter table ops.stock_almacen        enable row level security;
alter table ops.stock_formato        enable row level security;
alter table ops.stock_formato_linea  enable row level security;
alter table ops.stock_formato_evento enable row level security;
alter table ops.stock_mov            enable row level security;
alter table ops.devoluciones         enable row level security;

grant all on ops.stock_almacen       to service_role;
grant all on ops.stock_formato       to service_role;
grant all on ops.stock_formato_linea to service_role;
grant all on ops.devoluciones        to service_role;
-- Solo se agregan (defensa para PostgREST; al dueño lo frena el trigger, SEG-04).
grant select, insert on ops.stock_mov            to service_role;
grant select, insert on ops.stock_formato_evento to service_role;
revoke update, delete, truncate on ops.stock_mov, ops.stock_formato_evento from service_role;
grant select on ops.stock_apartado_descuadre_v to service_role;
revoke all on ops.stock_almacen, ops.stock_formato, ops.stock_formato_linea, ops.stock_formato_evento,
              ops.stock_mov, ops.devoluciones, ops.stock_apartado_descuadre_v from anon, authenticated;

revoke all on function ops.verificar_libro(citext, text)    from public, anon, authenticated;
revoke all on function ops.verificar_apartado(citext, text) from public, anon, authenticated;
grant execute on function ops.verificar_libro(citext, text)    to service_role;
grant execute on function ops.verificar_apartado(citext, text) to service_role;

-- ───────────────────────────────────────────────────────────────────────────
-- 12) Registro de esta aplicación
-- ───────────────────────────────────────────────────────────────────────────
insert into ops.migraciones (migracion, detalle)
values ('0065_ops_inventario_kubera',
        jsonb_build_object('stock_almacen', (select count(*) from ops.stock_almacen),
                           'stock_mov',     (select count(*) from ops.stock_mov)));

-- ───────────────────────────────────────────────────────────────────────────
-- 13) AL FINAL: stock_watch_photo.stock_kubera (la tabla más leída)
-- ───────────────────────────────────────────────────────────────────────────
-- El ALTER pide un candado exclusivo y forma detrás de sí a stock_watch y al
-- panel: espera corta (3 s) y solo si falta la columna, para que re-correr no
-- pida el candado. Aplicar justo después de una pasada de stock_watch.
set local lock_timeout = '3s';
do $$
begin
  if not exists (select 1 from pg_catalog.pg_attribute a
                  where a.attrelid = 'ops.stock_watch_photo'::regclass
                    and a.attname = 'stock_kubera' and not a.attisdropped) then
    alter table ops.stock_watch_photo add column stock_kubera integer;
    comment on column ops.stock_watch_photo.stock_kubera is
      'La mitad de kubera de la foto (Σ libre ≥ 0 de las bodegas kubera con cuenta_para_woo), para que el freno mida '
      'cada fuente contra su propia foto. NULL SOLO antes del encendido de TEX3 (ninguna bodega kubera contaba): la '
      'primera pasada encendida explica la subida desde NULL. Ya encendida se guarda 0, NUNCA NULL (coalesce(kub, 0)).';
  end if;
end $$;

commit;
