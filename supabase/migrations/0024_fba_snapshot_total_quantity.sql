-- ═══════════════════════════════════════════════════════════════════════════
-- 0024 — `afn-total-quantity`: la cantidad DECLARADA en FBA.
--
-- Estado: NO APLICADA. Sandbox primero; producción con el visto de Eduardo.
--
-- QUÉ FALTABA
-- -----------
-- La 0023 leyó siete de las ocho columnas de cantidad del reporte y dejó
-- `afn-total-quantity` fuera. Eduardo lo cachó revisando las referencias
-- (18-ago). Es el total comprometido con Amazon: lo que está en la bodega MÁS
-- lo que va en camino.
--
-- LAS DOS IDENTIDADES, VERIFICADAS SKU POR SKU
-- --------------------------------------------
-- En los 1,258 SKUs del reporte, sin UNA sola excepción:
--
--     fulfillable + reserved + unsellable = afn-warehouse-quantity
--     warehouse   + inbound               = afn-total-quantity
--
-- O sea que `warehouse` es lo que HOY ocupa espacio físico y paga almacenaje,
-- y `total` es todo lo comprometido. Se guarda aunque sea derivable: si algún
-- día una de las dos identidades deja de cumplirse, tener el número original
-- de Amazon es lo único que permite darse cuenta. Derivarlo lo escondería.
--
-- ADITIVA Y CON DEFAULT: ningún lector existente cambia de comportamiento.
-- ═══════════════════════════════════════════════════════════════════════════

alter table ops.fba_snapshot
    add column if not exists total_quantity integer not null default 0;

comment on column ops.fba_snapshot.total_quantity is
    'afn-total-quantity: warehouse + inbound — todo lo comprometido con FBA. '
    'Verificado = warehouse + inbound en los 1,258 SKUs del reporte del 18-ago.';
