-- ═══════════════════════════════════════════════════════════════════════════
-- 0061 — ops.temu_guias_compras: la BITÁCORA DURABLE de las guías de Temu que
--        compra el panel (services/temu_guias_compra.py)
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Estado: SIN APLICAR. La aplica Eduardo. Mientras no exista, la compra de
-- guías NO PUEDE comprar (falla cerrado: sin reclamo no hay shipment.create) y
-- la vista previa lo dice en cada grupo. La compra además nace apagada
-- (TEMU_COMPRA_GUIAS_ENABLED=false): encenderla es el dale de Brandon.
--
-- POR QUÉ HACE FALTA (revisión del 30-sep-2026). Una guía comprada en Temu
-- COBRA y no se puede cancelar por API. La primera versión sólo se cuidaba en
-- MEMORIA (un candado por PO y un set de "no sé si compró"), y eso no alcanza:
--   · dos compras de PO distintos del MISMO combinado tomaban candados
--     distintos y las dos compraban la misma caja;
--   · Temu compra en ASÍNCRONO (guía 37): mientras la etiqueta está "en
--     aplicación" (estado 0) sus lecturas pueden no mostrarla, y un segundo
--     intento pasaba todas las guardas y compraba otra vez;
--   · un reinicio del contenedor (cambiar una variable en Railway) o una
--     cancelación a media llamada borraban el "no sé si compró".
--
-- CÓMO SE USA (escritura ADELANTADA). Antes de llamar a shipment.create el
-- panel RECLAMA, en UNA transacción, una fila por cada PO del grupo:
--     insert … on conflict (parent_order_sn) do update … where estado in
--     ('rechazada','no_enviada') returning …
-- Si alguna no se pudo tomar, se deshace todo y no se compra. Después la fila
-- pasa a 'pendiente'/'comprada' con sus packageSn, o a 'desconocido' ante
-- CUALQUIER resultado que no sea un rechazo seguro. Toda fila que no esté en
-- 'rechazada' o 'no_enviada' BLOQUEA otra compra de ese PO hasta conciliarla
-- (temu_guias_compra.conciliar). Si el proceso muere a media compra, la fila
-- se queda en 'en_curso' — y eso también bloquea: es el punto.
--
-- 'reparto' es lo planeado y 'reparto_real' lo que devolvió
-- bg.logistics.shipment.result.get (warehouseId y piezas por caja): con eso la
-- orden de Odoo nace en los almacenes DE LA GUÍA (odoo_ventas.crear_con_guia) y
-- el emparejador resuelve el SKU repartido entre almacenes.
--
-- SIN DATOS DEL COMPRADOR: PO, orderSn, SKU, piezas, almacenes, medidas,
-- paquetería, huella y quién aprobó.
--
-- UN SOLO ESCRITOR: services/temu_guias_compra.py. Idempotente.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

create table if not exists ops.temu_guias_compras (
    parent_order_sn text        not null,
    estado          text        not null,
    reclamo         text        not null,              -- uuid de UNA compra (todas las filas del grupo)
    grupo           text[]      not null default '{}', -- los PO que se compraron juntos
    huella          text        not null,              -- la huella de la aprobación
    aprobado_por    text        not null,
    send_type       smallint,
    payload         jsonb,                             -- el de SU llamada a shipment.create
    reparto         jsonb,                             -- [{orderSn, sku, quantity, warehouse_id, almacen_id}] planeado
    reparto_real    jsonb,                             -- lo mismo, de shipment.result.get (+ packageSn)
    package_sn      text[],
    fecha_envio     date,
    horas           smallint,
    codigo          text,                              -- errorCode de Temu si lo hubo
    motivo          text,
    intentos        integer     not null default 1,
    creado_at       timestamptz not null default now(),
    actualizado_at  timestamptz not null default now(),
    constraint temu_guias_compras_pkey primary key (parent_order_sn),
    constraint temu_guias_compras_estado_chk check (estado in (
        'en_curso', 'pendiente', 'comprada', 'fallida', 'desconocido',
        'ya_solicitada', 'rechazada', 'no_enviada')),
    constraint temu_guias_compras_po_chk check (parent_order_sn like 'PO-%')
);

create index if not exists temu_guias_compras_reclamo_ix
    on ops.temu_guias_compras (reclamo);
create index if not exists temu_guias_compras_abiertas_ix
    on ops.temu_guias_compras (estado)
    where estado not in ('rechazada', 'no_enviada', 'comprada');

comment on table ops.temu_guias_compras is
  'Bitácora DURABLE de las guías de Temu que compra el panel (temu_guias_compra.py). Se escribe '
  'ANTES de shipment.create (en_curso) y toda fila fuera de rechazada/no_enviada bloquea otra compra '
  'del mismo PO hasta conciliarla. Sin datos del comprador. Escritor único: temu_guias_compra.py.';
comment on column ops.temu_guias_compras.estado is
  'en_curso: reclamada, compra en vuelo o proceso muerto a media compra (BLOQUEA). '
  'pendiente: Temu dio packageSn pero la etiqueta sigue en aplicación (0). comprada: etiqueta 1. '
  'fallida: etiqueta 2 (necesita shipment.update, a mano). desconocido: no se sabe si compró (timeout, '
  'código no documentado como rechazo, cancelación). ya_solicitada: Temu contestó 120012013. '
  'rechazada: rechazo documentado ANTES de comprar (no bloquea). no_enviada: la llamada no salió '
  'porque otra del grupo se detuvo (no bloquea).';
comment on column ops.temu_guias_compras.reparto is
  'Lo planeado para este PO: [{orderSn, sku, quantity, warehouse_id, almacen_id}].';
comment on column ops.temu_guias_compras.reparto_real is
  'Lo comprado según bg.logistics.shipment.result.get: [{packageSn, warehouseId, almacen_id, orderSn, '
  'sku, quantity}]. De aquí nace la orden de Odoo (odoo_ventas.crear_con_guia).';

-- El candado de siempre (0016, 0049, 0051, 0053, 0058): RLS activa sin
-- políticas y sólo el service_role.
alter table ops.temu_guias_compras enable row level security;
grant all on ops.temu_guias_compras to service_role;

commit;
