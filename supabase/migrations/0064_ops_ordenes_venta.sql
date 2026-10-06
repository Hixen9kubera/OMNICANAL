-- ═══════════════════════════════════════════════════════════════════════════
-- 0064 — ÓRDENES DE VENTA PROPIAS (OV-00001…) y el CATÁLOGO DE BODEGAS
--        ops.almacenes · ops.ov_folio · ops.ov_ordenes · ops.ov_lineas ·
--        ops.ov_mensajes · ops.ov_archivos · ops.almacenes_hist · ops.migraciones
--        Plan v3 (5-oct-2026) con las correcciones de la revisión técnica.
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Estado: SIN APLICAR (6-oct-2026). Probada en el SANDBOX solo dentro de una
-- transacción que termina en ROLLBACK:
--     backend/scripts/verificar_0064_0065.py --en-transaccion
-- (55 pruebas OK, 0 fallas, con la 0065 y aplicadas dos veces, tras la
-- segunda revisión de tres lentes; las 2 pruebas de concurrencia necesitan las
-- tablas CONFIRMADAS y quedan para después de aplicar en el sandbox:
-- --concurrencia-con-commit).
-- Va JUNTO con la 0065 (inventario de kubera). Llevarlas a producción es su
-- propia acta, con las tres firmas.
--
-- ORDEN DE DESPLIEGUE (SEG-01, el único hallazgo ALTO de la revisión). Railway
-- despliega en cuanto algo llega a main. Por eso:
--   1. la 0064 y la 0065 se aplican en producción, con acta, ANTES de fusionar
--      cualquier código que las lea;
--   2. ese código pregunta `to_regclass('ops.almacenes')` (con caché) y, si
--      falta, sigue el camino de hoy y avisa una vez.
-- Sin las dos cosas, una lectura de ops.almacenes que truena deja en espera
-- cada venta de TikTok/Temu y stock_watch falla cerrado: Woo se queda quieto.
--
-- DE DÓNDE VIENE
--   · Brandon, 2-oct (docs/MIGRACION_0064_ORDENES_VENTA.md): el documento OV
--     propio — folio, borrador → confirmada → entregada, chat y PDF.
--   · Plan v3, 5-oct: la OV solo existe en bodegas de KUBERA (TEXCO III) y
--     NUNCA toca Odoo. Aparta en ops.stock_almacen (0065), en la misma fila
--     que el físico.
--   · Revisión técnica, 5-oct (§6): lo que el proceso cuidaba pasa a la base —
--     triggers de D11, constraint triggers diferidos, «¿salió?», errores con
--     nombre y rastro de ops.almacenes.
--
-- QUÉ NO CREA (de la versión de Brandon) Y POR QUÉ
--   · ops.ov_stock y la vista ops.ov_stock_base_v: reservaban contra la foto
--     de Odoo; el apartado vive ahora en ops.stock_almacen (0065).
--   · ov_ordenes.almacen: la bodega vive en el renglón (ov_lineas.almacen).
--   · El bucket `ordenes-venta` de Storage: NO va aquí. Se crea aparte
--     (privado, 15 MB, solo application/pdf, sin políticas), con su retención
--     decidida. Mientras no exista, ov_archivos no tiene escritor.
--
-- LO QUE GARANTIZA LA BASE (el backend entra como `postgres`, el dueño: RLS y
-- grants NO lo frenan; triggers, CHECK y FK sí)
--   · ops.almacenes: `codigo` y `fuente` fijos; dar de alta una bodega y
--     cambiar una bandera o un id externo exigen motivo (≥ 10; nuevo en cada
--     cambio) y quién (app.usuario o actualizado_por); cada alta y cambio
--     queda en ops.almacenes_hist. No se borran filas (D13). Una bodega de
--     kubera que surte ventas admite OV (almacenes_surte_ov_chk): si no,
--     crear_auto no la encuentra y cada venta se atora en «no_alcanzo».
--   · ov_folio: el contador no se borra ni baja (ov_folio_solo_sube).
--   · ov_ordenes: transiciones válidas (borrador → confirmada | cancelada;
--     confirmada → entregada | cancelada | entregada_cancelada; entregada →
--     entregada_cancelada; cancelada → entregada_cancelada, el «¿salió?»
--     tardío de la decisión 15); nace en borrador, o confirmada solo vía
--     crear_auto (creado_via = 'automatico'); NO se borra; confirmada =
--     inmutable salvo la lista de lo PERMITIDO (estado, rev, entrega,
--     cancelación, borrado lógico, devolucion_estado y la marca «¿salió?»
--     canal_cancelo_*). Una columna nueva nace congelada. Dentro de lo
--     permitido, lo que ya se anotó no se reescribe: entregada_* y
--     cancelada_* se ponen UNA vez, `rev` no baja y devolucion_estado no
--     vuelve a NULL ni de recibida a pendiente.
--   · ov_lineas: un renglón que aparta o ya salió solo cambia reservado y
--     entregado*; no se borra; uno de una OV que ya no es borrador no se edita
--     (salvo el apartado de confirmar, en la MISMA transacción que confirmó,
--     y la salida tardía de la decisión 15); no se agregan renglones a una OV
--     que ya no es borrador, salvo en la transacción que la creó (crear_auto).
--     Los renglones de una OV borrada no cambian. La guarda BLOQUEA la fila de
--     la OV (FOR NO KEY UPDATE) antes de leer su estado: un renglón nuevo
--     espera a un confirmar en curso en vez de colarse sin apartar.
--   · ov_archivos: no se borra; una fila solo cambia para marcar borrado_at y
--     borrado_por, una vez.
--   · Al COMMIT (constraint trigger diferido): una OV confirmada viva tiene al
--     menos un renglón sin entregar y TODO renglón sin entregar apartado
--     completo; una entregada tiene todo entregado; fuera de confirmada (o
--     borrada) no hay apartado; un borrador no tiene entregas y una cancelada
--     no dejó salir piezas (si salieron, es entregada_cancelada). «Aparta
--     todo o nada» queda en la base.
--   · ov_mensajes, almacenes_hist y migraciones: SOLO SE AGREGAN (UPDATE,
--     DELETE y TRUNCATE rechazados también para el dueño).
--   · almacenes, ov_folio, ov_ordenes, ov_lineas y ov_archivos no se vacían
--     (TRUNCATE rechazado): TRUNCATE no dispara los triggers de fila ni los
--     diferidos.
--   · Las tablas de solo agregar quedan para service_role en SELECT e INSERT y
--     nada más (revoke all + grant): sin REFERENCES, TRIGGER ni MAINTAIN, que
--     el default ACL del esquema daba al nacer.
--   · El apartado = Σ reservado y el saldo = libro los agrega la 0065, donde
--     nace ops.stock_almacen.
--
-- CONTRATO DE ERRORES (para que Python distinga sin leer el texto)
--   KB000  la forma de lo que ya existe no coincide (paso 0): la migración no
--          sigue. Nunca «idempotente» sobre una versión vieja.
--   KB001  regla de negocio de una sentencia: ops.exigir(cond, motivo). El
--          motivo es el mensaje (p. ej. 'no_alcanzo'). Reemplaza el 1/0.
--   23505  único repetido: se identifica por constraint_name
--          (ov_ordenes_clave_uq, ov_ordenes_mp_uq) → releer y devolver
--          «ya_existia». El pool NO reintenta errores de integridad.
--   23514  CHECK, transición inválida o invariante al COMMIT. Los triggers
--          lanzan con CONSTRAINT = <nombre>, igual que un CHECK.
--   42501  solo agregar, inmutable o sin TRUNCATE (con CONSTRAINT = <nombre>:
--          <tabla>_solo_agregar, *_inmutable, *_sin_borrado, <tabla>_sin_truncate,
--          ov_folio_solo_sube).
--   55006  TRUNCATE con eventos diferidos pendientes en la misma transacción:
--          Postgres lo rechaza antes de llegar al trigger (igual queda vetado).
--   23503  FK: bodega que no es de kubera (FK compuesta (almacen, fuente)).
--
-- DECISIONES TOMADAS POR OMISIÓN (Eduardo puede cambiarlas; cada una es un
-- UPDATE con acta o una migración chica)
--   1. D1: el código es 'TEX3' (fijo por trigger, forma ^[A-Z0-9]{2,12}$); el
--      nombre «TEXCO III» es provisional y se edita sin acta.
--   2. TEX3 NACE APAGADO: surte_ventas = false, cuenta_para_woo = false y
--      admite_ov = false. Se aparta del plan v3 §2.1, que lo sembraba con
--      admite_ov = sí: así no hay OV en TEX3 hasta que Eduardo lo encienda.
--      Mientras tanto se practica en ENSAYO (admite_ov = true). Encenderlo:
--        select set_config('app.usuario', '<correo>', true);
--        update ops.almacenes set admite_ov = true, motivo = '<acta …>'
--         where codigo = 'TEX3';
--      y en la fase B, en otra acta: surte_ventas y cuenta_para_woo. OJO: el
--      UPDATE de la fase B que copia el plan v3 §10.1 (paso 12) solo pone
--      surte_ventas y cuenta_para_woo; si admite_ov sigue en false truena con
--      23514 (almacenes_surte_ov_chk). admite_ov = true va en ese mismo UPDATE
--      o antes: una bodega de kubera que surte ventas tiene que admitir OV,
--      porque crear_auto solo aparta en una bodega con admite_ov.
--   3. D13: la fila TEX2 se queda y sigue surtiendo (surte_ventas = true)
--      hasta que Odoo muestre TEX2 vacío; apagarla es un UPDATE con acta.
--   4. D12: REVISION se siembra aquí (kubera, sin admite_ov, sin Woo);
--      ops.devoluciones va en la 0065.
--   5. Filas de Odoo con cuenta_para_woo = true: es informativo (stock_watch
--      lee el TOTAL de Odoo, no esta columna); dice la verdad si alguien la lee.
--   6. D11: SÍ, y los triggers van aquí, donde nace cada tabla.
--   7. D10: el número de envío se exige al confirmar una OV full (decisión 5
--      de la v2). La 20 (fijarlo después) sigue abierta: envio_ref, guia y
--      paqueteria quedan congelados al confirmar.
--   8. D3: sin CHECK de temu_warehouse_id para TEX3 hasta que se cierre D3.
--   9. ov_archivos.tipo ∈ {envio_full, comprobante, factura}. Nunca etiquetas
--      con datos del comprador (D5): `tipo` no lo impide; lo cumplen la
--      pantalla y la impresión bajo pedido.
--  10. ov_mensajes.evento: catálogo cerrado (v2 §3.5 + DEVOLUCIONES +
--      canal_cancelo). Un evento nuevo = ampliar el CHECK en otra migración.
--  11. En las OV automáticas, cliente = mp_canal (SEG-06: nunca el comprador).
--  12. Solo crear_auto hace nacer una OV ya confirmada (creado_via =
--      'automatico'); Crear FULL, el panel y la API nacen en borrador (plan
--      v3 §6). Y una OV de la que salió alguna pieza no queda «cancelada»:
--      es entregada_cancelada (DEVOLUCIONES §4e), la que admite devolución.
--  13. Las banderas ov_generacion_auto e inventario_libro NO se siembran en
--      ops.automatizacion_flags (revisión §3.15): sin fila manda el valor
--      por omisión del entorno, que debe ser false. Las crea el acta que las
--      enciende, con su motivo.
--  14. Sin el trigger «la venta existe en channel.orders» para las capturas
--      manuales (H08, baja): pide resolver antes qué pasa cuando la venta
--      llega a channel.orders después que la OV.
--  15. «¿Salió?» TARDÍO (DEVOLUCIONES §4b punto 2 y §4e): si la caja de una
--      OV ya CANCELADA vuelve (el canal canceló en un estado que no estaba en
--      la lista de «ya salió», p. ej. AWAITING_SHIPMENT, y Bodega ya la había
--      entregado a la paquetería), la pantalla primero registra la salida:
--      cancelada → entregada_cancelada (solo si estuvo confirmada:
--      ov_ordenes_conf_chk), los renglones sin entrega reciben entregado > 0
--      con reservado = 0, y la salida_ov va en la MISMA sentencia con la clave
--      de entregar. Después, la devolución normal a REVISION. Se eligió esto y
--      no «cancelada es terminal y la caja vuelve al anaquel sin movimiento»
--      porque así la pieza pasa por REVISION y el libro cuenta lo que pasó.
--  16. devolucion_estado no regresa a NULL ni de 'recibida' a 'pendiente' (la
--      llegada no se deshace). Sí puede reabrirse una 'cerrada' (→ pendiente o
--      recibida): el cliente devuelve DESPUÉS otra pieza de la misma OV. La
--      revisión pedía solo hacia adelante; eso dejaba sin camino esa segunda
--      devolución.
--
-- QUIÉN ESCRIBE: solo services/ordenes_venta.py (crear_borrador, guardar,
-- confirmar, entregar, cancelar, borrar, crear_auto, marcas de devolución y
-- «¿salió?»). Las devoluciones y los canales lo LLAMAN; no escriben ov_*.
-- ops.almacenes: solo esta migración y UPDATE con acta.
--
-- IDEMPOTENCIA. Se puede correr dos veces seguidas: `if not exists`, triggers
-- con drop + create, funciones con `create or replace`, semilla con
-- `on conflict do nothing` (volver a correr NO regresa TEX3 a apagado si ya se
-- encendió). Y un PASO 0 que revisa la forma de lo que ya exista y truena con
-- KB000 si no coincide: `if not exists` no compara definiciones (SEG-07). Si
-- una migración posterior cambia algo que el paso 0 revisa, se ajusta aquí.
--
-- VERIFICACIÓN ESPERADA
--   · verificar_rls.py (estático): +8 tablas con RLS, 0 vistas.
--   · ops.almacenes: 6 filas (TEXCO, TEX2, DROP, TEX3, ENSAYO, REVISION).
--   · ops.ov_folio: (1, 0).
--   · backend/scripts/verificar_0064_0065.py: todas las pruebas OK.
--
-- REVERSA. Estructural SOLO mientras no haya datos reales (ninguna OV fuera de
-- ENSAYO); se niega sola si los hay:
--   do $$ begin if exists (select 1 from ops.ov_lineas
--   where coalesce(almacen, '') <> 'ENSAYO') then raise exception 'hay OV fuera
--   de ENSAYO: la reversa es operativa (banderas), no DROP'; end if; end $$;
-- Con la 0065 aplicada, primero la reversa de la 0065. En orden: quitar
-- ov_archivos, ov_mensajes, ov_lineas, ov_ordenes, ov_folio, almacenes_hist y
-- almacenes, y las funciones ops.exigir, ops.tg_* (incluidas tg_solo_agregar y
-- tg_sin_truncate) y ops.verificar_ov. ops.migraciones SE QUEDA: es el
-- registro de TODAS las migraciones desde la 0064 (la 0065 y las siguientes
-- también escriben ahí). Los BEFORE TRUNCATE no frenan un DROP TABLE. Con
-- datos reales la reversa es OPERATIVA: banderas apagadas, nunca DROP.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- DDL sobre tablas que pueden estar vivas (en una re-ejecución): espera corta y
-- truena, en vez de formar detrás de sí al panel. Muere con el commit.
set local lock_timeout = '5s';

-- ───────────────────────────────────────────────────────────────────────────
-- 0) PASO 0 — la forma de lo que ya exista. Si una tabla ya está, tiene que
--    ser ESTA versión: ni la de Brandon (2-oct), ni la de la v2.
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
    'almacenes.codigo text', 'almacenes.nombre text', 'almacenes.fuente text',
    'almacenes.odoo_warehouse_id integer', 'almacenes.preferencia smallint',
    'almacenes.surte_ventas boolean', 'almacenes.admite_ov boolean',
    'almacenes.cuenta_para_woo boolean', 'almacenes.temu_warehouse_id text',
    'almacenes.actualizado_at timestamptz', 'almacenes.actualizado_por text',
    'almacenes.motivo text',
    'almacenes_hist.id bigint', 'almacenes_hist.codigo text', 'almacenes_hist.antes jsonb',
    'almacenes_hist.despues jsonb', 'almacenes_hist.motivo text', 'almacenes_hist.quien text',
    'almacenes_hist.cambiado_at timestamptz',
    'migraciones.id bigint', 'migraciones.migracion text', 'migraciones.aplicada_at timestamptz',
    'migraciones.quien text', 'migraciones.detalle jsonb',
    'ov_folio.id smallint', 'ov_folio.ultimo integer',
    'ov_ordenes.id bigint', 'ov_ordenes.folio text', 'ov_ordenes.estado text',
    'ov_ordenes.tipo text', 'ov_ordenes.rev integer', 'ov_ordenes.cliente text',
    'ov_ordenes.canal text', 'ov_ordenes.mp_canal text', 'ov_ordenes.mp_cuenta text',
    'ov_ordenes.mp_orden text', 'ov_ordenes.full_tienda text', 'ov_ordenes.envio_ref text',
    'ov_ordenes.descripcion text', 'ov_ordenes.guia text', 'ov_ordenes.paqueteria text',
    'ov_ordenes.fecha_venta timestamptz', 'ov_ordenes.entrega_limite timestamptz',
    'ov_ordenes.moneda text', 'ov_ordenes.total numeric', 'ov_ordenes.comision numeric',
    'ov_ordenes.precio_origen text', 'ov_ordenes.devolucion_estado text',
    'ov_ordenes.canal_cancelo_at timestamptz', 'ov_ordenes.canal_cancelo_ref text',
    'ov_ordenes.creado_at timestamptz', 'ov_ordenes.creado_por text',
    'ov_ordenes.creado_nombre text', 'ov_ordenes.creado_via text',
    'ov_ordenes.confirmada_at timestamptz', 'ov_ordenes.confirmada_por text',
    'ov_ordenes.confirmada_nombre text', 'ov_ordenes.entregada_at timestamptz',
    'ov_ordenes.entregada_por text', 'ov_ordenes.entregada_nombre text',
    'ov_ordenes.cancelada_at timestamptz', 'ov_ordenes.cancelada_por text',
    'ov_ordenes.cancelada_nombre text', 'ov_ordenes.cancelada_origen text',
    'ov_ordenes.cancelada_motivo text', 'ov_ordenes.borrada_at timestamptz',
    'ov_ordenes.borrada_por text', 'ov_ordenes.borrada_nombre text',
    'ov_ordenes.borrada_motivo text', 'ov_ordenes.actualizado_at timestamptz',
    'ov_ordenes.clave text',
    'ov_lineas.id bigint', 'ov_lineas.orden_id bigint', 'ov_lineas.linea integer',
    'ov_lineas.sku citext', 'ov_lineas.titulo text', 'ov_lineas.imagen text',
    'ov_lineas.cantidad integer', 'ov_lineas.precio_unitario numeric',
    'ov_lineas.almacen text', 'ov_lineas.fuente text', 'ov_lineas.reservado integer',
    'ov_lineas.entregado integer', 'ov_lineas.entregado_at timestamptz',
    'ov_lineas.entregado_por text',
    'ov_mensajes.id bigint', 'ov_mensajes.orden_id bigint', 'ov_mensajes.tipo text',
    'ov_mensajes.evento text', 'ov_mensajes.cuerpo text', 'ov_mensajes.datos jsonb',
    'ov_mensajes.autor text', 'ov_mensajes.autor_nombre text', 'ov_mensajes.via text',
    'ov_mensajes.creado_at timestamptz',
    'ov_archivos.id bigint', 'ov_archivos.orden_id bigint', 'ov_archivos.tipo text',
    'ov_archivos.nombre text', 'ov_archivos.ruta text', 'ov_archivos.sha256 text',
    'ov_archivos.bytes bigint', 'ov_archivos.subido_at timestamptz',
    'ov_archivos.subido_por text', 'ov_archivos.subido_nombre text',
    'ov_archivos.borrado_at timestamptz', 'ov_archivos.borrado_por text'];
  -- Columnas de las versiones anteriores que NO deben estar.
  prohibidas constant text[] := array[
    'ov_ordenes.almacen', 'almacenes.surte_ov', 'almacenes.corte_at', 'almacenes.corte_por'];
  -- Restricciones e índices cuyo NOMBRE se repite entre versiones con OTRA
  -- definición: se compara un fragmento de la definición.
  restricciones constant text[] := array[
    'almacenes|almacenes_codigo_fuente_uq|UNIQUE (codigo, fuente)',
    'almacenes|almacenes_woo_chk|cuenta_para_woo',
    'almacenes|almacenes_pref_uq|DEFERRABLE',
    'almacenes|almacenes_surte_ov_chk|admite_ov',
    'ov_ordenes|ov_ordenes_cancelada_origen_chk|sistema',
    'ov_ordenes|ov_ordenes_canal_cancelo_chk|canal_cancelo_at',
    'ov_ordenes|ov_ordenes_auto_cliente_chk|automatico',
    'ov_ordenes|ov_ordenes_devol_chk|entregada',
    'ov_lineas|ov_lineas_reservado_chk|(reservado = cantidad)',
    'ov_lineas|ov_lineas_almacen_fk|(almacen, fuente)',
    -- Diferidas: inmediatas, el guardar (renumerar `linea` en un UPDATE, quitar
    -- y volver a poner un SKU) truena a media sentencia.
    'ov_lineas|ov_lineas_sku_alm_uq|NULLS NOT DISTINCT (orden_id, sku, almacen) DEFERRABLE INITIALLY DEFERRED',
    'ov_lineas|ov_lineas_linea_uq|UNIQUE (orden_id, linea) DEFERRABLE INITIALLY DEFERRED',
    'ov_mensajes|ov_mensajes_evento_chk|canal_cancelo',
    'ov_archivos|ov_archivos_tipo_chk|envio_full',
    'ov_archivos|ov_archivos_borrado_chk|borrado_por'];
  indices constant text[] := array[
    'ov_ordenes|ov_ordenes_clave_uq|cancelada',
    'ov_ordenes|ov_ordenes_mp_uq|cancelada'];
begin
  if to_regclass('ops.ov_stock') is not null or to_regclass('ops.ov_stock_base_v') is not null
     or to_regclass('ops.ov_odoo') is not null then
    raise exception using errcode = 'KB000',
      message = '0064 paso 0: existe ops.ov_stock, ops.ov_stock_base_v u ops.ov_odoo (versiones del 2-oct o la v1). '
                'Quitar esa versión antes de aplicar esta.';
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
        message = format('0064 paso 0: ops.%s ya existe pero sin la columna %s (otra versión de la 0064)', t, c);
    end if;
    -- `is distinct from` y el NULL de to_regtype: falla CERRADO (un tipo que no
    -- se resuelve, p. ej. citext fuera del search_path, no da «coincide»).
    if to_regtype(ty) is null or v is distinct from to_regtype(ty) then
      raise exception using errcode = 'KB000',
        message = format('0064 paso 0: ops.%s.%s es %s y se esperaba %s', t, c, v, ty);
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
        message = format('0064 paso 0: ops.%s tiene la columna %s de una versión anterior', t, c);
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
        message = format('0064 paso 0: ops.%s no tiene %s con «%s» (otra versión de la 0064)', t, c, d);
    end if;
  end loop;

  foreach e in array indices loop
    t := split_part(e, '|', 1);
    c := split_part(e, '|', 2);
    d := split_part(e, '|', 3);
    continue when to_regclass('ops.' || t) is null;
    if not exists (select 1 from pg_catalog.pg_indexes i
                    where i.schemaname = 'ops' and i.tablename = t and i.indexname = c
                      and strpos(i.indexdef, d) > 0 and strpos(i.indexdef, 'COALESCE') = 0) then
      raise exception using errcode = 'KB000',
        message = format('0064 paso 0: el índice ops.%s no es el parcial de esta versión (vivas, sin coalesce)', c);
    end if;
  end loop;
end $$;

-- ───────────────────────────────────────────────────────────────────────────
-- 1) Funciones comunes
-- ───────────────────────────────────────────────────────────────────────────

-- Errores de negocio CON NOMBRE (SEG-09). Cada sentencia termina en
--     select ops.exigir((select count(*) from f) = 1, 'formato_no_esta_por_confirmar')
-- en lugar del `1 / (case … then 1 else 0 end)`. NULL cuenta como falso: falla
-- cerrado. El motivo viaja como mensaje con SQLSTATE KB001.
create or replace function ops.exigir(ok boolean, motivo text) returns integer
language plpgsql volatile
set search_path = pg_catalog
as $$
begin
  if ok then
    return 1;
  end if;
  raise exception using errcode = 'KB001',
    message = coalesce(nullif(btrim(motivo), ''), 'regla_de_negocio');
end $$;

-- Libros, bitácoras y chat: SOLO SE AGREGAN. Un grant no frena al dueño; esto sí.
create or replace function ops.tg_solo_agregar() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  raise exception using errcode = '42501', constraint = tg_table_name || '_solo_agregar',
    message = format('%s.%s es de solo agregar: %s rechazado', tg_table_schema, tg_table_name, tg_op),
    hint = 'Lo registrado no se edita: se corrige con otro registro. Una corrección de emergencia es su propia acta.';
end $$;

-- Las tablas que «no se borran» (su trigger de fila rechaza el DELETE) tampoco
-- se vacían: TRUNCATE no dispara los triggers de fila ni los diferidos, así que
-- sin esto un `truncate ops.stock_almacen` se saltaría saldo = libro (0065).
create or replace function ops.tg_sin_truncate() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  raise exception using errcode = '42501', constraint = tg_table_name || '_sin_truncate',
    message = format('%s.%s no se vacía: TRUNCATE rechazado', tg_table_schema, tg_table_name),
    hint = 'Sus filas no se borran (borrado lógico o banderas). Vaciarla es su propia acta, con respaldo.';
end $$;

-- ───────────────────────────────────────────────────────────────────────────
-- 2) ops.almacenes — el catálogo de bodegas y su historia
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists ops.almacenes_hist (
    id          bigint      generated always as identity,
    codigo      text        not null,
    antes       jsonb,                                   -- NULL en el alta
    despues     jsonb       not null,
    motivo      text,
    quien       text,
    cambiado_at timestamptz not null default now(),
    constraint almacenes_hist_pkey primary key (id)
);
create index if not exists almacenes_hist_codigo_ix on ops.almacenes_hist (codigo, id);

create table if not exists ops.almacenes (
    codigo            text        not null,
    nombre            text        not null,
    fuente            text        not null,               -- odoo | kubera: FIJA desde que nace (trigger)
    odoo_warehouse_id integer,                            -- 135 | 150 | 142; NULL en bodegas de kubera
    preferencia       smallint,                           -- orden en el planeador; NULL = nunca se planea
    surte_ventas      boolean     not null default false, -- el planeador le asigna ventas de TikTok/Temu y Crear FULL
    admite_ov         boolean     not null default false, -- ahí se capturan y confirman OV (solo kubera)
    cuenta_para_woo   boolean     not null default false, -- su libre entra a Woo (se lee en bodegas kubera)
    temu_warehouse_id text,                               -- WH-… de Temu. NO es único: dos bodegas del mismo predio
    actualizado_at    timestamptz not null default now(),
    actualizado_por   text        default nullif(current_setting('app.usuario', true), ''),
    motivo            text,                               -- por qué del ÚLTIMO cambio de bandera
    constraint almacenes_pkey            primary key (codigo),
    constraint almacenes_codigo_fuente_uq unique (codigo, fuente),     -- destino de las FK compuestas (H03)
    constraint almacenes_odoo_uq         unique (odoo_warehouse_id),
    constraint almacenes_pref_uq         unique (preferencia) deferrable initially immediate,   -- H15
    constraint almacenes_codigo_chk      check (codigo ~ '^[A-Z0-9]{2,12}$'),
    constraint almacenes_nombre_chk      check (length(btrim(nombre)) > 0),
    constraint almacenes_fuente_chk      check (fuente in ('odoo', 'kubera')),
    constraint almacenes_odoo_chk        check ((fuente = 'odoo') = (odoo_warehouse_id is not null)),
    constraint almacenes_pref_chk        check (preferencia is null or preferencia > 0),
    constraint almacenes_ov_chk          check (not admite_ov or fuente = 'kubera'),            -- R2
    constraint almacenes_surte_chk       check (not surte_ventas or preferencia is not null),
    constraint almacenes_woo_chk         check (fuente = 'odoo' or not cuenta_para_woo or surte_ventas),
                                         -- lo que kubera ofrece en Woo, el planeador lo puede surtir
    constraint almacenes_surte_ov_chk    check (fuente = 'odoo' or not surte_ventas or admite_ov)
                                         -- y lo que surte, crear_auto lo puede apartar (exige admite_ov)
);

-- Guarda: alta con acta; código y fuente fijos; banderas con motivo y quién;
-- sin DELETE.
create or replace function ops.tg_almacenes_guarda() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  v_quien text := nullif(current_setting('app.usuario', true), '');
begin
  if tg_op = 'INSERT' then
    -- Dar de alta una bodega es el mismo interruptor de negocio que cambiar
    -- sus banderas (SEG-03): con motivo y quién. La semilla trae los dos.
    if length(btrim(coalesce(new.motivo, ''))) < 10 then
      raise exception using errcode = '23514', constraint = 'almacenes_motivo_chk',
        message = format('ops.almacenes: el alta de %s exige un motivo de 10 caracteres o más', new.codigo);
    end if;
    if coalesce(v_quien, new.actualizado_por) is null then
      raise exception using errcode = '23514', constraint = 'almacenes_quien_chk',
        message = format('ops.almacenes: el alta de %s exige quién: set_config(''app.usuario'', …, true) o actualizado_por', new.codigo);
    end if;
    new.actualizado_por := coalesce(v_quien, new.actualizado_por);
    new.actualizado_at  := now();
    return new;
  end if;
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', constraint = 'almacenes_sin_borrado',
      message = format('ops.almacenes: la bodega %s no se borra; se apaga con sus banderas (D13)', old.codigo);
  end if;
  if new.codigo is distinct from old.codigo or new.fuente is distinct from old.fuente then
    raise exception using errcode = '42501', constraint = 'almacenes_codigo_fuente_fijos',
      message = format('ops.almacenes: codigo y fuente de %s son fijos', old.codigo);
  end if;
  if (new.surte_ventas, new.cuenta_para_woo, new.admite_ov, new.preferencia,
      new.odoo_warehouse_id, new.temu_warehouse_id)
     is distinct from
     (old.surte_ventas, old.cuenta_para_woo, old.admite_ov, old.preferencia,
      old.odoo_warehouse_id, old.temu_warehouse_id) then
    if new.motivo is not distinct from old.motivo or length(btrim(coalesce(new.motivo, ''))) < 10 then
      raise exception using errcode = '23514', constraint = 'almacenes_motivo_chk',
        message = format('ops.almacenes: cambiar una bandera de %s exige un motivo NUEVO de 10 caracteres o más', old.codigo);
    end if;
    if v_quien is null and new.actualizado_por is not distinct from old.actualizado_por then
      raise exception using errcode = '23514', constraint = 'almacenes_quien_chk',
        message = format('ops.almacenes: cambiar una bandera de %s exige quién: set_config(''app.usuario'', …, true) o actualizado_por', old.codigo);
    end if;
  end if;
  new.actualizado_por := coalesce(v_quien, new.actualizado_por);
  new.actualizado_at  := now();
  return new;
end $$;

create or replace function ops.tg_almacenes_hist() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  insert into ops.almacenes_hist (codigo, antes, despues, motivo, quien)
  values (new.codigo,
          case when tg_op = 'UPDATE' then to_jsonb(old) end,
          to_jsonb(new), new.motivo,
          coalesce(nullif(current_setting('app.usuario', true), ''), new.actualizado_por));
  return null;
end $$;

drop trigger if exists almacenes_guarda on ops.almacenes;
create trigger almacenes_guarda before insert or update or delete on ops.almacenes
  for each row execute function ops.tg_almacenes_guarda();
drop trigger if exists almacenes_hist on ops.almacenes;
create trigger almacenes_hist after insert or update on ops.almacenes
  for each row execute function ops.tg_almacenes_hist();
drop trigger if exists almacenes_hist_solo_agregar on ops.almacenes_hist;
create trigger almacenes_hist_solo_agregar before update or delete on ops.almacenes_hist
  for each row execute function ops.tg_solo_agregar();
drop trigger if exists almacenes_hist_sin_truncate on ops.almacenes_hist;
create trigger almacenes_hist_sin_truncate before truncate on ops.almacenes_hist
  for each statement execute function ops.tg_solo_agregar();
drop trigger if exists almacenes_sin_truncate on ops.almacenes;
create trigger almacenes_sin_truncate before truncate on ops.almacenes
  for each statement execute function ops.tg_sin_truncate();

-- La semilla: SEIS filas, sin pisar lo que una persona ya cambió (SEG-07).
-- Ninguna mueve Woo: las de kubera nacen sin saldo y apagadas.
insert into ops.almacenes
       (codigo,     nombre,                       fuente,   odoo_warehouse_id, preferencia,
        surte_ventas, admite_ov, cuenta_para_woo, temu_warehouse_id,      actualizado_por,  motivo) values
       ('TEXCO',    'TEXCO',                      'odoo',   135,  1, true,  false, true,  'WH-04038973460631627', 'migracion_0064', 'Semilla de la 0064 (plan v3)'),
       ('TEX2',     'TEXCO II',                   'odoo',   150,  2, true,  false, true,  'WH-10610291507351627', 'migracion_0064', 'Semilla de la 0064 (plan v3, D13)'),
       ('DROP',     'DROP OFF',                   'odoo',   142,  null, false, false, true, null,               'migracion_0064', 'Semilla de la 0064 (plan v3)'),
       ('TEX3',     'TEXCO III',                  'kubera', null, 3, false, false, false, null,                   'migracion_0064', 'Semilla de la 0064 (D1; nace apagada)'),
       ('ENSAYO',   'Bodega de ensayo',           'kubera', null, null, false, true, false, null,               'migracion_0064', 'Semilla de la 0064 (practica de OV y formatos)'),
       ('REVISION', 'Revisión de devoluciones',   'kubera', null, null, false, false, false, null,              'migracion_0064', 'Semilla de la 0064 (D12)')
on conflict (codigo) do nothing;

-- ───────────────────────────────────────────────────────────────────────────
-- 3) ops.migraciones — registro de cada aplicación (SEG-07)
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists ops.migraciones (
    id          bigint      generated always as identity,
    migracion   text        not null,
    aplicada_at timestamptz not null default now(),
    quien       text        not null default coalesce(nullif(current_setting('app.usuario', true), ''), current_user),
    detalle     jsonb,
    constraint migraciones_pkey primary key (id),
    constraint migraciones_nombre_chk check (migracion ~ '^[0-9]{4}_[a-z0-9_]+$')
);
drop trigger if exists migraciones_solo_agregar on ops.migraciones;
create trigger migraciones_solo_agregar before update or delete on ops.migraciones
  for each row execute function ops.tg_solo_agregar();
drop trigger if exists migraciones_sin_truncate on ops.migraciones;
create trigger migraciones_sin_truncate before truncate on ops.migraciones
  for each statement execute function ops.tg_solo_agregar();

-- ───────────────────────────────────────────────────────────────────────────
-- 4) ops.ov_folio — el contador sin huecos (de Brandon)
-- ───────────────────────────────────────────────────────────────────────────
-- Tabla y no SEQUENCE: una secuencia deja huecos en cada rollback. Se
-- incrementa en la MISMA sentencia que inserta la OV. (Los folios FMT y DEV de
-- la 0065 salen de una identidad y SÍ pueden tener huecos: no los ve un cliente.)
create table if not exists ops.ov_folio (
    id      smallint not null default 1,
    ultimo  integer  not null default 0,
    constraint ov_folio_pkey primary key (id),
    constraint ov_folio_una_fila_chk check (id = 1),
    constraint ov_folio_ultimo_chk check (ultimo >= 0)
);
insert into ops.ov_folio (id, ultimo) values (1, 0) on conflict (id) do nothing;
-- Vaciarla, borrar la fila o bajar `ultimo` regresaría el folio: el siguiente
-- alta chocaría con ov_ordenes_folio_uq (que el contrato no traduce a
-- «ya_existia») y TODAS las altas fallarían hasta reparar el contador a mano.
-- Y sin la fila, re-correr esta migración la volvería a sembrar en 0.
create or replace function ops.tg_ov_folio_guarda() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if tg_op = 'DELETE' or new.id <> old.id or new.ultimo < old.ultimo then
    raise exception using errcode = '42501', constraint = 'ov_folio_solo_sube',
      message = 'ops.ov_folio: el contador no se borra ni baja';
  end if;
  return new;
end $$;
drop trigger if exists ov_folio_guarda on ops.ov_folio;
create trigger ov_folio_guarda before update or delete on ops.ov_folio
  for each row execute function ops.tg_ov_folio_guarda();
drop trigger if exists ov_folio_sin_truncate on ops.ov_folio;
create trigger ov_folio_sin_truncate before truncate on ops.ov_folio
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 5) ops.ov_ordenes — el encabezado
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists ops.ov_ordenes (
    id                bigint        generated always as identity,
    folio             text          not null,
    estado            text          not null default 'borrador',
    tipo              text          not null default 'venta',   -- venta | full (R3)
    rev               integer       not null default 1,         -- candado optimista
    -- Contenido -------------------------------------------------------------
    cliente           text,                                     -- el canal o la razón social. NUNCA el comprador
    canal             text,
    mp_canal          text,                                     -- ORDEN DE MARKETPLACE (como channel.orders): canal,
    mp_cuenta         text,                                     --   cuenta
    mp_orden          text,                                     --   y external_order_id
    full_tienda       text,                                     -- meli:Kubera | meli:San Corpe | amazon | walmart
    envio_ref         text,                                     -- número del envío o inbound del marketplace (FULL)
    descripcion       text,
    guia              text,
    paqueteria        text,
    fecha_venta       timestamptz,
    entrega_limite    timestamptz,
    -- Precio de la venta ---------------------------------------------------
    moneda            text          not null default 'MXN',
    total             numeric(14,2) not null default 0,
    comision          numeric(14,2) not null default 0,
    precio_origen     text          not null default 'manual',
    -- Devolución y «¿salió?» -----------------------------------------------
    devolucion_estado text,                                     -- pendiente → recibida → cerrada
    canal_cancelo_at  timestamptz,                              -- el canal canceló con el paquete ya enviado
    canal_cancelo_ref text,                                     -- estado del canal al cancelar: IN_TRANSIT, 4/5 de Temu, shipped
    -- Quién y cuándo -------------------------------------------------------
    creado_at         timestamptz   not null default now(),
    creado_por        text          not null,                   -- correo | 'servicio' | 'automatico'
    creado_nombre     text,
    creado_via        text          not null default 'panel',
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
    actualizado_at    timestamptz   not null default now(),     -- lo mantiene el trigger
    clave             text,                                     -- idempotencia del alta
    constraint ov_ordenes_pkey          primary key (id),
    constraint ov_ordenes_folio_uq      unique (folio),
    constraint ov_ordenes_folio_chk     check (folio ~ '^OV-[0-9]{5,}$'),
    constraint ov_ordenes_estado_chk    check (estado in ('borrador', 'confirmada', 'entregada', 'cancelada', 'entregada_cancelada')),
    constraint ov_ordenes_tipo_chk      check (tipo in ('venta', 'full')),
    constraint ov_ordenes_via_chk       check (creado_via in ('panel', 'api', 'claude', 'automatico')),
    constraint ov_ordenes_precio_origen_chk check (precio_origen in ('manual', 'marketplace')),
    constraint ov_ordenes_moneda_chk    check (moneda ~ '^[A-Z]{3}$'),
    constraint ov_ordenes_importes_chk  check (total >= 0 and comision >= 0),
    constraint ov_ordenes_rev_chk       check (rev >= 1),
    constraint ov_ordenes_canal_chk     check (canal is null or canal in
        ('temu', 'tiktok', 'mercado_libre', 'amazon', 'walmart', 'shein', 'directa', 'otro')),
    constraint ov_ordenes_mp_chk        check ((mp_orden is null     and mp_canal is null     and mp_cuenta is null)
                                            or (mp_orden is not null and mp_canal is not null and mp_cuenta is not null)),
    constraint ov_ordenes_mp_forma_chk  check (mp_canal = lower(mp_canal) and mp_cuenta = upper(mp_cuenta)),
    constraint ov_ordenes_full_chk      check ((tipo = 'full') = (full_tienda is not null)),
    constraint ov_ordenes_full_t_chk    check (full_tienda is null
                                               or full_tienda in ('meli:Kubera', 'meli:San Corpe', 'amazon', 'walmart')),
    constraint ov_ordenes_full_mp_chk   check (tipo <> 'full' or mp_orden is null),
    constraint ov_ordenes_full_canal_chk check (tipo <> 'full' or coalesce(canal, '') in ('mercado_libre', 'amazon', 'walmart')),
    constraint ov_ordenes_full_tc_chk   check (full_tienda is null
                                               or (full_tienda in ('meli:Kubera', 'meli:San Corpe') and canal is not distinct from 'mercado_libre')
                                               or (full_tienda = 'amazon'  and canal is not distinct from 'amazon')
                                               or (full_tienda = 'walmart' and canal is not distinct from 'walmart')),
    constraint ov_ordenes_full_env_chk  check (tipo <> 'full' or confirmada_at is null or envio_ref is not null),
    constraint ov_ordenes_cancelada_origen_chk check (cancelada_origen is null
                                               or cancelada_origen in ('manual', 'marketplace', 'sistema')),
    constraint ov_ordenes_conf_chk      check (estado not in ('confirmada', 'entregada', 'entregada_cancelada')
                                               or confirmada_at is not null),
    constraint ov_ordenes_borr_chk      check (estado <> 'borrador'
                                               or (confirmada_at is null and entregada_at is null and cancelada_at is null)),
    constraint ov_ordenes_entr_chk      check ((estado in ('entregada', 'entregada_cancelada')) = (entregada_at is not null)),
    constraint ov_ordenes_canc_chk      check ((estado in ('cancelada', 'entregada_cancelada')) = (cancelada_at is not null)),
    constraint ov_ordenes_canc_o_chk    check (cancelada_at is null or cancelada_origen is not null),
    constraint ov_ordenes_canc_m_chk    check (cancelada_at is null or confirmada_at is null
                                               or coalesce(length(cancelada_motivo), 0) >= 5),
    -- Quién va con cuándo (H19)
    constraint ov_ordenes_conf_q_chk    check ((confirmada_at is null) = (confirmada_por is null)),
    constraint ov_ordenes_entr_q_chk    check ((entregada_at is null) = (entregada_por is null)),
    constraint ov_ordenes_canc_q_chk    check ((cancelada_at is null) = (cancelada_por is null)),
    constraint ov_ordenes_borrada_chk   check ((borrada_at is null) = (borrada_por is null)),
    constraint ov_ordenes_borrada_m_chk check (borrada_at is null or coalesce(length(borrada_motivo), 0) >= 10),
    constraint ov_ordenes_devolucion_chk check (devolucion_estado is null
                                               or devolucion_estado in ('pendiente', 'recibida', 'cerrada')),
    constraint ov_ordenes_devol_chk     check (devolucion_estado is null or estado in ('entregada', 'entregada_cancelada')),
    -- «¿Salió?» (hallazgo 4): solo en una OV confirmada o en sus salidas
    constraint ov_ordenes_canal_cancelo_chk check (canal_cancelo_at is null
                                               or estado in ('confirmada', 'cancelada', 'entregada_cancelada')),
    constraint ov_ordenes_canal_cancelo_ref_chk check ((canal_cancelo_at is null) = (canal_cancelo_ref is null)),
    -- Las automáticas no llevan al comprador: el cliente es el canal (SEG-06)
    constraint ov_ordenes_auto_cliente_chk check (creado_via <> 'automatico' or cliente is not distinct from mp_canal)
);

-- Una venta de marketplace = UNA OV viva (sin coalesce: mp_* es todo o nada).
create unique index if not exists ov_ordenes_mp_uq on ops.ov_ordenes (mp_canal, mp_cuenta, mp_orden)
    where mp_orden is not null and borrada_at is null and estado <> 'cancelada';
-- La clave de idempotencia, PARCIAL igual que la de la venta: una OV cancelada
-- o borrada no se queda con su clave para siempre.
create unique index if not exists ov_ordenes_clave_uq on ops.ov_ordenes (clave)
    where clave is not null and borrada_at is null and estado <> 'cancelada';
create index if not exists ov_ordenes_creado_ix   on ops.ov_ordenes (creado_at desc);
create index if not exists ov_ordenes_estado_ix   on ops.ov_ordenes (estado, creado_at desc) where borrada_at is null;
create index if not exists ov_ordenes_mp_orden_ix on ops.ov_ordenes (mp_orden) where mp_orden is not null;
-- «¿Salió?»: la pantalla de almacén y el aviso diario
create index if not exists ov_ordenes_salio_ix    on ops.ov_ordenes (canal_cancelo_at)
    where estado = 'confirmada' and canal_cancelo_at is not null and borrada_at is null;

-- ───────────────────────────────────────────────────────────────────────────
-- 6) ops.ov_lineas — el renglón
-- ───────────────────────────────────────────────────────────────────────────
-- `sku` sin FK a core.products, a propósito (como channel.order_items).
-- `fuente` existe solo para la FK compuesta: el renglón solo nombra bodegas de
-- kubera (R2). Con `almacen` NULL (borrador sin bodega) la FK no se revisa.
create table if not exists ops.ov_lineas (
    id              bigint        generated always as identity,
    orden_id        bigint        not null,
    linea           integer       not null,       -- orden en pantalla; se renumera al guardar el borrador
    sku             citext        not null,
    titulo          text,
    imagen          text,                         -- URL http(s); nunca un data-URI
    cantidad        integer       not null,       -- lo pedido; inmutable una vez apartado
    precio_unitario numeric(14,2) not null default 0,
    almacen         text,                         -- opcional en borrador; obligatorio para apartar
    fuente          text          not null default 'kubera',
    reservado       integer       not null default 0,   -- piezas que ESTE renglón tiene en stock_almacen.apartado
    entregado       integer,                      -- piezas que salieron; NULL mientras no se entregue
    entregado_at    timestamptz,
    entregado_por   text,
    constraint ov_lineas_pkey          primary key (id),
    constraint ov_lineas_orden_fk      foreign key (orden_id) references ops.ov_ordenes (id),   -- SIN cascade
    constraint ov_lineas_almacen_fk    foreign key (almacen, fuente) references ops.almacenes (codigo, fuente),
    constraint ov_lineas_sku_alm_uq    unique nulls not distinct (orden_id, sku, almacen) deferrable initially deferred,
    constraint ov_lineas_linea_uq      unique (orden_id, linea) deferrable initially deferred,
    constraint ov_lineas_fuente_chk    check (fuente = 'kubera'),
    constraint ov_lineas_linea_chk     check (linea > 0),
    constraint ov_lineas_cantidad_chk  check (cantidad > 0),
    constraint ov_lineas_precio_chk    check (precio_unitario >= 0),
    constraint ov_lineas_reservado_chk check (reservado in (0, cantidad)),           -- todo o nada (H19)
    constraint ov_lineas_reserva_alm_chk check (reservado = 0 or almacen is not null),
    constraint ov_lineas_entrega_chk   check ((entregado_at is null) = (entregado_por is null)
                                          and (entregado_at is null) = (entregado is null)
                                          and (entregado is null or (entregado between 0 and cantidad
                                                                     and almacen is not null and reservado = 0)))
);
create index if not exists ov_lineas_sku_ix      on ops.ov_lineas (sku);
create index if not exists ov_lineas_alm_sku_ix  on ops.ov_lineas (almacen, sku) where entregado_at is null;
-- Los renglones que apartan: la verificación del apartado y la vista vigía (R8)
create index if not exists ov_lineas_apartan_ix  on ops.ov_lineas (sku, almacen) where reservado > 0;

-- ───────────────────────────────────────────────────────────────────────────
-- 7) ops.ov_mensajes — el chat y la bitácora (solo se agrega)
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists ops.ov_mensajes (
    id           bigint      generated always as identity,
    orden_id     bigint      not null,
    tipo         text        not null,
    evento       text,                                    -- NULL en los mensajes de personas
    cuerpo       text        not null,
    datos        jsonb,
    autor        text        not null,                    -- correo | 'servicio' | 'automatico'
    autor_nombre text,
    via          text        not null default 'panel',
    creado_at    timestamptz not null default now(),
    constraint ov_mensajes_pkey     primary key (id),
    constraint ov_mensajes_orden_fk foreign key (orden_id) references ops.ov_ordenes (id),
    constraint ov_mensajes_tipo_chk check (tipo in ('sistema', 'usuario')),
    constraint ov_mensajes_via_chk  check (via in ('panel', 'api', 'claude', 'automatico')),
    constraint ov_mensajes_cuerpo_chk check (length(cuerpo) between 1 and 4000),
    constraint ov_mensajes_evento_chk check (evento is null or evento in (
        'creada', 'borrador_guardado', 'descartada', 'confirmada', 'no_alcanzo',
        'entregada_parcial', 'entregada', 'cancelada', 'borrada_admin', 'canal_cancelo',
        'devolucion_esperada', 'devolucion_recibida', 'devolucion_aprobada',
        'devolucion_merma', 'devolucion_cerrada'))
);
create index if not exists ov_mensajes_orden_ix on ops.ov_mensajes (orden_id, id);

drop trigger if exists ov_mensajes_solo_agregar on ops.ov_mensajes;
create trigger ov_mensajes_solo_agregar before update or delete on ops.ov_mensajes
  for each row execute function ops.tg_solo_agregar();
drop trigger if exists ov_mensajes_sin_truncate on ops.ov_mensajes;
create trigger ov_mensajes_sin_truncate before truncate on ops.ov_mensajes
  for each statement execute function ops.tg_solo_agregar();

-- ───────────────────────────────────────────────────────────────────────────
-- 8) ops.ov_archivos — índice de los PDF (el binario va a Storage)
-- ───────────────────────────────────────────────────────────────────────────
-- Orden fijo con Storage (no comparten transacción): subir el objeto y DESPUÉS
-- insertar la fila; marcar borrado_at y DESPUÉS borrar el objeto. Así lo único
-- que puede quedar suelto es un objeto huérfano, nunca una fila sin archivo.
create table if not exists ops.ov_archivos (
    id             bigint      generated always as identity,
    orden_id       bigint      not null,
    tipo           text        not null,                  -- sin default a propósito
    nombre         text        not null,
    ruta           text        not null,                  -- <folio>/<sha256>.pdf dentro del bucket
    sha256         text        not null,
    bytes          bigint      not null,
    subido_at      timestamptz not null default now(),
    subido_por     text        not null,
    subido_nombre  text,
    borrado_at     timestamptz,
    borrado_por    text,
    constraint ov_archivos_pkey      primary key (id),
    constraint ov_archivos_orden_fk  foreign key (orden_id) references ops.ov_ordenes (id),
    constraint ov_archivos_tipo_chk  check (tipo in ('envio_full', 'comprobante', 'factura')),
    constraint ov_archivos_sha_chk   check (sha256 ~ '^[0-9a-f]{64}$'),
    constraint ov_archivos_bytes_chk check (bytes > 0),
    constraint ov_archivos_borrado_chk check ((borrado_at is null) = (borrado_por is null))
);
create unique index if not exists ov_archivos_vivo_uq on ops.ov_archivos (orden_id, sha256) where borrado_at is null;
create index if not exists ov_archivos_orden_ix on ops.ov_archivos (orden_id);

-- Borrado LÓGICO y fila fija: sin DELETE ni TRUNCATE (el objeto en Storage
-- quedaría huérfano sin rastro de su OV) y un UPDATE solo pasa borrado_at y
-- borrado_por de NULL a valor, una vez (ni revivir un archivo cuyo objeto ya
-- se borró, ni cambiar a qué apunta).
create or replace function ops.tg_ov_archivos_guarda() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', constraint = 'ov_archivos_sin_borrado',
      message = format('archivo %s: se marca borrado_at, no se borra', old.id);
  end if;
  if old.borrado_at is not null
     or (to_jsonb(new) - array['borrado_at', 'borrado_por']) is distinct from (to_jsonb(old) - array['borrado_at', 'borrado_por']) then
    raise exception using errcode = '42501', constraint = 'ov_archivos_inmutable',
      message = format('archivo %s: solo se marca borrado (borrado_at y borrado_por), una vez', old.id);
  end if;
  return new;
end $$;
drop trigger if exists ov_archivos_guarda on ops.ov_archivos;
create trigger ov_archivos_guarda before update or delete on ops.ov_archivos
  for each row execute function ops.tg_ov_archivos_guarda();
drop trigger if exists ov_archivos_sin_truncate on ops.ov_archivos;
create trigger ov_archivos_sin_truncate before truncate on ops.ov_archivos
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 9) D11 — la OV confirmada es inmutable y sus estados son una máquina
-- ───────────────────────────────────────────────────────────────────────────
create or replace function ops.tg_ov_ordenes_guarda() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  -- Lista de lo PERMITIDO después de borrador: una columna nueva nace congelada.
  permitidas constant text[] := array[
    'estado', 'rev', 'actualizado_at',
    'entregada_at', 'entregada_por', 'entregada_nombre',
    'cancelada_at', 'cancelada_por', 'cancelada_nombre', 'cancelada_origen', 'cancelada_motivo',
    'borrada_at', 'borrada_por', 'borrada_nombre', 'borrada_motivo',
    'devolucion_estado', 'canal_cancelo_at', 'canal_cancelo_ref'];
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_sin_borrado',
      message = format('OV %s: las OV no se borran (borrado lógico: borrada_at/por/motivo, solo admin)', old.folio);
  end if;

  if tg_op = 'INSERT' then
    -- Nace en borrador. Solo crear_auto (creado_via = 'automatico') la crea ya
    -- confirmada, con sus renglones apartados en la misma sentencia; una OV del
    -- panel, la API o Crear FULL pasa por confirmar (aparta todo o nada).
    if not (new.estado = 'borrador' or (new.estado = 'confirmada' and new.creado_via = 'automatico')) then
      raise exception using errcode = '23514', constraint = 'ov_ordenes_transicion_chk',
        message = format('OV %s: nace en borrador (o confirmada solo por crear_auto), no en %s vía %s',
                         new.folio, new.estado, new.creado_via);
    end if;
    return new;
  end if;

  -- Una OV borrada ya no cambia.
  if old.borrada_at is not null
     and (to_jsonb(new) - 'actualizado_at') is distinct from (to_jsonb(old) - 'actualizado_at') then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
      message = format('OV %s está borrada: no cambia', old.folio);
  end if;

  if (new.id, new.folio, new.creado_at, new.creado_por, new.creado_via)
     is distinct from (old.id, old.folio, old.creado_at, old.creado_por, old.creado_via) then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
      message = format('OV %s: id, folio y alta son fijos', old.folio);
  end if;

  -- Transiciones (H04): la foto la validan los CHECK; la película, esto.
  -- cancelada → entregada_cancelada: el «¿salió?» tardío (decisión 15); que
  -- haya estado confirmada lo exige ov_ordenes_conf_chk.
  if new.estado is distinct from old.estado and not (
        (old.estado = 'borrador'   and new.estado in ('confirmada', 'cancelada'))
     or (old.estado = 'confirmada' and new.estado in ('entregada', 'cancelada', 'entregada_cancelada'))
     or (old.estado = 'entregada'  and new.estado = 'entregada_cancelada')
     or (old.estado = 'cancelada'  and new.estado = 'entregada_cancelada')) then
    raise exception using errcode = '23514', constraint = 'ov_ordenes_transicion_chk',
      message = format('OV %s: %s → %s no es una transición válida', old.folio, old.estado, new.estado);
  end if;

  -- «¿Salió?»: la marca se pone una vez y no se borra. Con ella, la OV solo
  -- sale a cancelada o entregada_cancelada (ov_ordenes_canal_cancelo_chk).
  if old.canal_cancelo_at is not null
     and (new.canal_cancelo_at, new.canal_cancelo_ref) is distinct from (old.canal_cancelo_at, old.canal_cancelo_ref) then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
      message = format('OV %s: la cancelación del canal ya está anotada y no cambia', old.folio);
  end if;

  -- Lo ya anotado no se reescribe (§6.1 punto 8, H19). ov_ordenes no tiene
  -- historia: quién entregó, quién canceló y por qué se ponen UNA vez
  -- (entregada → entregada_cancelada conserva entregada_at, v2 §4.1).
  if new.rev < old.rev then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
      message = format('OV %s: rev no baja (%s → %s)', old.folio, old.rev, new.rev);
  end if;
  if old.entregada_at is not null
     and (new.entregada_at, new.entregada_por, new.entregada_nombre)
         is distinct from (old.entregada_at, old.entregada_por, old.entregada_nombre) then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
      message = format('OV %s: la entrega ya está anotada y no cambia', old.folio);
  end if;
  if old.cancelada_at is not null
     and (new.cancelada_at, new.cancelada_por, new.cancelada_nombre, new.cancelada_origen, new.cancelada_motivo)
         is distinct from (old.cancelada_at, old.cancelada_por, old.cancelada_nombre, old.cancelada_origen, old.cancelada_motivo) then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
      message = format('OV %s: la cancelación ya está anotada y no cambia', old.folio);
  end if;
  -- devolucion_estado: nunca vuelve a NULL ni de recibida a pendiente (la
  -- llegada no se deshace); una cerrada sí se reabre (decisión 16).
  if new.devolucion_estado is distinct from old.devolucion_estado and old.devolucion_estado is not null
     and (new.devolucion_estado is null
          or (old.devolucion_estado = 'recibida' and new.devolucion_estado = 'pendiente')) then
    raise exception using errcode = '23514', constraint = 'ov_ordenes_transicion_chk',
      message = format('OV %s: devolucion_estado %s → %s no es válido', old.folio,
                       old.devolucion_estado, coalesce(new.devolucion_estado, 'NULL'));
  end if;

  if old.estado <> 'borrador'
     and (to_jsonb(new) - permitidas) is distinct from (to_jsonb(old) - permitidas) then
    raise exception using errcode = '42501', constraint = 'ov_ordenes_inmutable',
      message = format('OV %s ya no es borrador (%s): solo cambian %s', old.folio, old.estado, array_to_string(permitidas, ', '));
  end if;

  new.actualizado_at := now();
  return new;
end $$;

drop trigger if exists ov_ordenes_guarda on ops.ov_ordenes;
create trigger ov_ordenes_guarda before insert or update or delete on ops.ov_ordenes
  for each row execute function ops.tg_ov_ordenes_guarda();
drop trigger if exists ov_ordenes_sin_truncate on ops.ov_ordenes;
create trigger ov_ordenes_sin_truncate before truncate on ops.ov_ordenes
  for each statement execute function ops.tg_sin_truncate();

-- El renglón. La regla sale de la PROPIA fila mientras se pueda, para no
-- depender de qué ve el trigger dentro del WITH de confirmar (las partes de un
-- WITH no tienen orden garantizado). Solo lee la OV cuando la fila no basta, y
-- entonces BLOQUEA su fila (FOR NO KEY UPDATE): sin el candado, un renglón
-- insertado mientras otra transacción confirma ve 'borrador', la FK no espera
-- (FOR KEY SHARE es compatible) y los dos verificar_ov diferidos pueden pasar
-- sin verse (write skew): una OV confirmada con un renglón sin apartar. Con
-- él, o espera al confirmar y ve 'confirmada' (42501), o el confirmar espera y
-- su verificar_ov ve el renglón (23514). FOR NO KEY UPDATE y no FOR SHARE: dos
-- guardar del mismo borrador hacen fila en vez de caer en deadlock al subir rev.
create or replace function ops.tg_ov_lineas_guarda() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  permitidas constant text[] := array['reservado', 'entregado', 'entregado_at', 'entregado_por'];
  v_estado  text;
  v_creado  timestamptz;
  v_borrada timestamptz;
  v_conf    timestamptz;
begin
  if tg_op = 'INSERT' then
    -- Agregar renglones: en borrador, o en la MISMA transacción que creó la OV
    -- (crear_auto inserta la OV confirmada y sus renglones apartados juntos).
    -- creado_at = now() es «nació en esta transacción»: now() es la hora de inicio de la transacción.
    select o.estado, o.creado_at, o.borrada_at into v_estado, v_creado, v_borrada
      from ops.ov_ordenes o where o.id = new.orden_id
       for no key update;
    if v_borrada is not null then
      raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
        message = format('OV %s está borrada: no se le agregan renglones', new.orden_id);
    end if;
    if v_estado is not null and v_estado <> 'borrador' and v_creado is distinct from now() then
      raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
        message = format('OV %s está %s: no se le agregan renglones', new.orden_id, v_estado);
    end if;
    return new;
  end if;

  if tg_op = 'UPDATE' and (new.id, new.orden_id) is distinct from (old.id, old.orden_id) then
    raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
      message = format('renglón %s: id y orden son fijos', old.id);
  end if;

  if old.entregado_at is not null then
    raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
      message = format('renglón %s ya salió: no se edita ni se borra', old.id);
  end if;

  if old.reservado > 0 then
    if tg_op = 'DELETE' then
      raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
        message = format('renglón %s aparta piezas: no se borra', old.id);
    end if;
    if (to_jsonb(new) - permitidas) is distinct from (to_jsonb(old) - permitidas) then
      raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
        message = format('renglón %s aparta piezas: solo cambian %s', old.id, array_to_string(permitidas, ', '));
    end if;
    return new;
  end if;

  -- Sin apartado ni entrega: depende de la OV (con su fila bloqueada).
  select o.estado, o.borrada_at, o.confirmada_at into v_estado, v_borrada, v_conf
    from ops.ov_ordenes o where o.id = old.orden_id
     for no key update;
  if v_borrada is not null then
    raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
      message = format('renglón %s de una OV borrada: no se edita ni se borra', old.id);
  end if;
  if coalesce(v_estado, 'borrador') = 'borrador' then
    return coalesce(new, old);
  end if;
  -- Confirmar: el trigger puede ver la OV ya confirmada (otra parte del mismo
  -- WITH). Solo se admite poner bodega y apartar el renglón completo, y solo
  -- en la transacción que la confirmó (confirmada_at = now(), el mismo
  -- criterio que creado_at): días después, soltar y volver a apartar en OTRA
  -- bodega cambiaría un renglón confirmado sin rastro.
  if tg_op = 'UPDATE' and v_estado = 'confirmada' and v_conf = now() and new.reservado > 0
     and (to_jsonb(new) - array['almacen', 'reservado']) is not distinct from (to_jsonb(old) - array['almacen', 'reservado']) then
    return new;
  end if;
  -- «¿Salió?» tardío (decisión 15): la caja de una OV cancelada sí salió. Solo
  -- se anota la entrega (entregado > 0, sin apartado) en un renglón que no la
  -- tenía; la OV pasa a entregada_cancelada en la misma sentencia (si no,
  -- verificar_ov rechaza la cancelada con piezas que salieron).
  if tg_op = 'UPDATE' and v_estado in ('cancelada', 'entregada_cancelada')
     and new.reservado = 0 and new.entregado > 0 and new.entregado_at is not null
     and (to_jsonb(new) - array['entregado', 'entregado_at', 'entregado_por'])
         is not distinct from (to_jsonb(old) - array['entregado', 'entregado_at', 'entregado_por']) then
    return new;
  end if;
  raise exception using errcode = '42501', constraint = 'ov_lineas_inmutable',
    message = format('renglón %s de una OV %s: no se edita ni se borra', old.id, v_estado);
end $$;

drop trigger if exists ov_lineas_guarda on ops.ov_lineas;
create trigger ov_lineas_guarda before insert or update or delete on ops.ov_lineas
  for each row execute function ops.tg_ov_lineas_guarda();
drop trigger if exists ov_lineas_sin_truncate on ops.ov_lineas;
create trigger ov_lineas_sin_truncate before truncate on ops.ov_lineas
  for each statement execute function ops.tg_sin_truncate();

-- Al COMMIT: la OV y sus renglones cuentan la misma historia. UNA consulta
-- (una sola foto).
create or replace function ops.verificar_ov(p_orden_id bigint) returns void
language plpgsql
set search_path = pg_catalog
as $$
declare
  v record;
begin
  select o.folio, o.estado, o.borrada_at,
         count(l.id)                                                                    as renglones,
         count(l.id) filter (where l.entregado_at is null)                              as sin_entregar,
         count(l.id) filter (where l.entregado_at is null and l.reservado <> l.cantidad) as sin_apartar,
         count(l.id) filter (where l.reservado > 0)                                     as con_apartado,
         count(l.id) filter (where l.entregado_at is not null)                          as con_entrega,
         count(l.id) filter (where l.entregado > 0)                                     as con_salida
    into v
    from ops.ov_ordenes o
    left join ops.ov_lineas l on l.orden_id = o.id
   where o.id = p_orden_id
   group by o.id;
  if not found then
    return;
  end if;
  if v.estado = 'confirmada' and v.borrada_at is null then
    if v.sin_entregar = 0 or v.sin_apartar > 0 then
      raise exception using errcode = '23514', constraint = 'ov_coherente',
        message = format('OV %s confirmada: %s renglones sin entregar, %s sin apartar completos (aparta todo o nada)',
                         v.folio, v.sin_entregar, v.sin_apartar);
    end if;
  elsif v.con_apartado > 0 then
    raise exception using errcode = '23514', constraint = 'ov_coherente',
      message = format('OV %s (%s%s) tiene %s renglones con apartado', v.folio, v.estado,
                       case when v.borrada_at is not null then ', borrada' else '' end, v.con_apartado);
  end if;
  if v.estado = 'entregada' and v.sin_entregar > 0 then
    raise exception using errcode = '23514', constraint = 'ov_coherente',
      message = format('OV %s entregada con %s renglones sin entregar', v.folio, v.sin_entregar);
  end if;
  -- Un borrador no ha entregado nada; una cancelada no dejó salir piezas: si
  -- algo salió, la cancelación es entregada_cancelada (DEVOLUCIONES §4e), que
  -- es la única que admite devolucion_estado.
  if v.estado = 'borrador' and v.con_entrega > 0 then
    raise exception using errcode = '23514', constraint = 'ov_coherente',
      message = format('OV %s en borrador con %s renglones entregados', v.folio, v.con_entrega);
  end if;
  if v.estado = 'cancelada' and v.con_salida > 0 then
    raise exception using errcode = '23514', constraint = 'ov_coherente',
      message = format('OV %s cancelada con %s renglones de los que salieron piezas: es entregada_cancelada',
                       v.folio, v.con_salida);
  end if;
end $$;

create or replace function ops.tg_ov_coherente() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if tg_table_name = 'ov_ordenes' then
    perform ops.verificar_ov(new.id);
  else
    perform ops.verificar_ov(coalesce(new.orden_id, old.orden_id));
  end if;
  return null;
end $$;

drop trigger if exists ov_ordenes_coherente on ops.ov_ordenes;
create constraint trigger ov_ordenes_coherente after insert or update on ops.ov_ordenes
  deferrable initially deferred for each row execute function ops.tg_ov_coherente();
drop trigger if exists ov_lineas_coherente on ops.ov_lineas;
create constraint trigger ov_lineas_coherente after insert or update or delete on ops.ov_lineas
  deferrable initially deferred for each row execute function ops.tg_ov_coherente();

-- ───────────────────────────────────────────────────────────────────────────
-- 10) Comentarios: las reglas que eran `--` quedan en el catálogo (SEG-16)
-- ───────────────────────────────────────────────────────────────────────────
comment on table ops.almacenes is
  'Catálogo único de bodegas: quién lleva el saldo (fuente, FIJA), a cuál se le planean ventas (surte_ventas, '
  'preferencia), dónde existe una OV (admite_ov, solo kubera) y cuál suma a Woo (cuenta_para_woo; en bodegas de '
  'Odoo es informativa: se lee el total de Odoo). Sin DELETE. Cada cambio de bandera exige motivo y quién, y queda en '
  'ops.almacenes_hist. TEX3 nace apagada (0064).';
comment on column ops.almacenes.cuenta_para_woo is
  'Su libre entra a Woo. Solo se LEE en bodegas de kubera (stock_watch suma el free_qty total de Odoo aparte). '
  'Regla de la base: una bodega de kubera que cuenta para Woo tiene que surtir ventas (almacenes_woo_chk).';
comment on column ops.almacenes.temu_warehouse_id is
  'WH-… de la dirección registrada en Temu. No es único: dos bodegas del mismo predio comparten dirección. '
  'TEX3 no se enciende sin él (D3).';
comment on column ops.almacenes.motivo is
  'Por qué del ÚLTIMO cambio de bandera o de id externo (10 caracteres o más, nuevo en cada cambio). '
  'La historia completa está en ops.almacenes_hist.';
comment on table ops.almacenes_hist is
  'Historia de ops.almacenes: una fila por alta y por cambio, con antes/después en jsonb, motivo y quién '
  '(app.usuario o actualizado_por). Solo se agrega.';
comment on table ops.migraciones is
  'Registro de cada APLICACIÓN de una migración que lo escribe (desde la 0064): cuándo y quién. Solo se agrega; '
  'una re-ejecución deja otra fila.';
comment on table ops.ov_folio is
  'Contador del folio de las OV propias (OV-00001…). Una sola fila. Se incrementa en la misma sentencia que '
  'inserta la OV (services/ordenes_venta.py): sin huecos. No se borra, no se vacía y `ultimo` no baja '
  '(ov_folio_solo_sube, 42501).';
comment on table ops.ov_ordenes is
  'Órdenes de venta PROPIAS de nuestras bodegas (folio OV-00001…). Nunca tocan Odoo. borrador (editable, no aparta) '
  '→ confirmada (inmutable; aparta en ops.stock_almacen) → entregada; cancelada (con motivo si estaba confirmada) o '
  'entregada_cancelada. tipo venta | full. No se borran: borrada_at/por/motivo, solo admin. Transiciones e '
  'inmutabilidad por trigger. Escritor único: services/ordenes_venta.py.';
comment on column ops.ov_ordenes.rev is
  'Candado optimista para PERSONAS: toda escritura exige la rev que leyó y la sube en uno. Los procesos (canal, '
  'devoluciones) usan compare-and-set por estado.';
comment on column ops.ov_ordenes.mp_orden is
  'ORDEN DE MARKETPLACE: el external_order_id de channel.orders. Con mp_canal (minúsculas) y mp_cuenta (mayúsculas) '
  'es la llave de la venta: una sola OV viva por venta (ov_ordenes_mp_uq).';
comment on column ops.ov_ordenes.clave is
  'Idempotencia del alta. OV de una venta: mp:<canal>:<cuenta>:<orden>. OV de Crear FULL: '
  'full:<solicitud>:<tienda>:<bodega>. Alta del panel: el uuid de la pantalla. Única solo entre OV vivas '
  '(ov_ordenes_clave_uq). Un 23505 de ov_ordenes_clave_uq u ov_ordenes_mp_uq = ya existía: releer.';
comment on column ops.ov_ordenes.cliente is
  'El canal o la razón social («temu», «AMAZON»). NUNCA el comprador. En las automáticas es igual a mp_canal.';
comment on column ops.ov_ordenes.devolucion_estado is
  'Solo en entregada o entregada_cancelada: pendiente (el canal avisó, canceló después de entregar, «¿salió?» '
  'resuelto en salió, o la KAM marcó) → recibida (entró a REVISION la primera pieza) → cerrada. NULL = no se espera.';
comment on column ops.ov_ordenes.canal_cancelo_at is
  '«¿Salió?»: el canal canceló una OV confirmada con el paquete ya enviado. La OV sigue confirmada y no suelta el '
  'apartado hasta que Bodega diga si salió (→ entregada_cancelada) o no (→ cancelada). Se pone una vez.';
comment on column ops.ov_ordenes.canal_cancelo_ref is
  'Estado del canal al cancelar: IN_TRANSIT / AWAITING_COLLECTION (TikTok), 4 o 5 (Temu), shipped (ML).';
comment on column ops.ov_ordenes.creado_via is
  'Por dónde nació: panel (persona con sesión), api (X-API-Key), claude (X-API-Key + X-Origen: claude), '
  'automatico (crear_auto: una venta que sale de una bodega de kubera).';
comment on table ops.ov_lineas is
  'Renglones de la OV. reservado = piezas que ESTE renglón tiene en ops.stock_almacen.apartado: 0 en borrador, '
  '= cantidad al confirmar, 0 al entregar o cancelar. La bodega es de kubera (FK compuesta con fuente). Un renglón '
  'que aparta o ya salió solo cambia reservado y entregado* (trigger).';
comment on column ops.ov_lineas.fuente is
  'Siempre ''kubera''. Existe para la FK compuesta (almacen, fuente) → ops.almacenes (codigo, fuente): un renglón '
  'nunca nombra una bodega de Odoo (R2).';
comment on table ops.ov_mensajes is
  'Chat y bitácora de cada OV. tipo=sistema: lo escribe la MISMA sentencia que cambia el estado, con evento del '
  'catálogo. tipo=usuario: lo que escribe una persona (evento NULL). Solo se agrega (trigger). Sin datos del comprador.';
comment on table ops.ov_archivos is
  'Índice de los PDF de cada OV (envío a FULL, comprobante, factura); el binario vive en el bucket privado '
  '`ordenes-venta`. Nunca etiquetas con la dirección del comprador (D5). Subir el objeto y luego la fila; marcar '
  'borrado_at y luego borrar el objeto. Sin DELETE ni TRUNCATE; una fila solo cambia para marcar borrado_at/por, '
  'una vez (ov_archivos_inmutable, 42501).';
comment on function ops.exigir(boolean, text) is
  'Errores de negocio con nombre: devuelve 1 si ok; si no (o si es NULL), lanza SQLSTATE KB001 con el motivo como '
  'mensaje. Va al final de cada sentencia de escritura en lugar del 1/0.';
comment on function ops.verificar_ov(bigint) is
  'Invariante de la OV, revisada al COMMIT: confirmada viva ⇒ ≥1 renglón sin entregar y todo renglón sin entregar '
  'apartado completo; entregada ⇒ todo entregado; cualquier otro estado (o borrada) ⇒ sin apartado; borrador ⇒ sin '
  'entregas; cancelada ⇒ no salió ninguna pieza (si salió, es entregada_cancelada). SQLSTATE 23514, ov_coherente.';
comment on function ops.tg_sin_truncate() is
  'BEFORE TRUNCATE de las tablas cuyas filas no se borran: TRUNCATE no dispara los triggers de fila ni los diferidos. '
  'SQLSTATE 42501, constraint <tabla>_sin_truncate.';
comment on function ops.tg_solo_agregar() is
  'BEFORE UPDATE/DELETE (por fila) y TRUNCATE (por sentencia) de las tablas de solo agregar: los grants no frenan al '
  'dueño `postgres` con el que entra el backend; esto sí. SQLSTATE 42501, constraint <tabla>_solo_agregar.';

-- ───────────────────────────────────────────────────────────────────────────
-- 11) El candado, donde nace el objeto: RLS sin políticas, solo service_role
-- ───────────────────────────────────────────────────────────────────────────
alter table ops.almacenes      enable row level security;
alter table ops.almacenes_hist enable row level security;
alter table ops.migraciones    enable row level security;
alter table ops.ov_folio       enable row level security;
alter table ops.ov_ordenes     enable row level security;
alter table ops.ov_lineas      enable row level security;
alter table ops.ov_mensajes    enable row level security;
alter table ops.ov_archivos    enable row level security;

grant all on ops.almacenes   to service_role;
grant all on ops.ov_folio    to service_role;
grant all on ops.ov_ordenes  to service_role;
grant all on ops.ov_lineas   to service_role;
grant all on ops.ov_archivos to service_role;
-- Solo se agregan: SELECT e INSERT y NADA más (defensa para PostgREST; al
-- dueño lo frena el trigger). `revoke all` primero: el default ACL del esquema
-- da todo al nacer, y un revoke de update/delete/truncate dejaba REFERENCES,
-- TRIGGER y MAINTAIN (con TRIGGER, una ruta de SQL dinámico podría colgarle un
-- trigger al chat o al registro).
revoke all on ops.ov_mensajes, ops.almacenes_hist, ops.migraciones from service_role;
grant select, insert on ops.ov_mensajes, ops.almacenes_hist, ops.migraciones to service_role;
revoke all on ops.almacenes, ops.almacenes_hist, ops.migraciones, ops.ov_folio, ops.ov_ordenes,
              ops.ov_lineas, ops.ov_mensajes, ops.ov_archivos from anon, authenticated;

revoke all on function ops.exigir(boolean, text)      from public, anon, authenticated;
revoke all on function ops.verificar_ov(bigint)       from public, anon, authenticated;
grant execute on function ops.exigir(boolean, text)   to service_role;
grant execute on function ops.verificar_ov(bigint)    to service_role;

-- ───────────────────────────────────────────────────────────────────────────
-- 12) Registro de esta aplicación
-- ───────────────────────────────────────────────────────────────────────────
insert into ops.migraciones (migracion, detalle)
values ('0064_ops_ordenes_venta',
        jsonb_build_object('almacenes', (select count(*) from ops.almacenes),
                           'ov_folio',  (select ultimo from ops.ov_folio where id = 1)));

commit;
