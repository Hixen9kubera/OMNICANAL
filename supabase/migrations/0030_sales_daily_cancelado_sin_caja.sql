-- ═══════════════════════════════════════════════════════════════════════════
-- 0030 — `channel.sales_daily`: el filtro de CANCELADO deja de depender de la
--         caja (mayúsculas/minúsculas).
--
-- POR QUÉ
--   `channel.orders.estado_canal` es `text`, NO `citext`: la comparación
--   distingue caja. La vista (migración 0005, línea 62) excluía tres literales
--   exactos — 'cancelled', 'invalid', 'Canceled' — y cada canal escribe la
--   cancelación con la caja que le da su API:
--
--       mercado_libre  'cancelled'   2,765 líneas   ← excluida (coincidía)
--       amazon         'Canceled'       38 líneas   ← excluida (coincidía)
--       tiktok         'CANCELLED'      26 líneas   ← NO coincidía: ENTRABA
--       tiktok         'cancelled'       1 línea    ← excluida (M2E, histórica)
--
--   Resultado medido en producción (lectura, 2026-08-21): 26 líneas / 26 piezas
--   / $5,603.32 de ventas canceladas de TikTok se contaban como VENTA REAL.
--   `services/pedidos_tiktok.py` escribe el estado en MAYÚSCULAS tal cual llega
--   de TikTok Shop; no es un dato sucio, es la convención del canal.
--
--   Este es el mismo arreglo que ya está escrito en el backend (12 copias del
--   filtro: 10 en `routers/fulfillment.py`, 2 en `routers/fba.py`). Esta vista
--   es la copia número 13 y la única que vive en SQL. Mientras siga como estaba,
--   la vista y el panel dan números distintos para el mismo período.
--
-- QUÉ NO CAMBIA — verificado antes de escribir esto
--   * `partially_refunded` (77 líneas / 184 piezas / $51,552.94) SIGUE DENTRO,
--     igual que antes. Es una decisión abierta de Eduardo y esta migración NO
--     la toca: ese monto es el total del pedido, no lo reembolsado.
--   * NADA ENTRA al universo. El barrido de la historia completa da 13
--     combinaciones canal×estado y las únicas que cambian de lado al comparar
--     en minúsculas son las 26 de tiktok/CANCELLED, y salen. Cero filas
--     'Unfulfillable', cero 'INVALID'.
--   * `sale_fee` no se mueve ni un centavo: esas 26 líneas tienen comisión 0.
--
-- POR QUÉ `lower()` Y NO `citext`
--   Cambiar `estado_canal` a `citext` arreglaría las 13 copias de un golpe,
--   pero también volvería insensible a la caja TODA comparación de estado en
--   el resto del sistema (`= 'paid'` empataría con 'PAID'), y eso es una
--   decisión de dominio, no de esta corrección. Aquí se arregla la vista con el
--   texto idéntico al del backend, para que ambos digan lo mismo.
--
-- COSTO
--   `lower()` sobre la columna impide usar un índice en `estado_canal`. No hay
--   ninguno, y la vista se arma sobre ~22,600 líneas: irrelevante.
--
-- Idempotente (`create or replace view`). No toca tablas ni datos.
-- ═══════════════════════════════════════════════════════════════════════════

create or replace view channel.sales_daily as
select (o.creado_at at time zone 'America/Mexico_City')::date as date,
       o.canal,
       o.cuenta,
       i.item_id,
       i.sku,
       bool_or(i.es_fulfillment)                as is_full,
       sum(i.cantidad)                          as units_sold,
       sum(i.precio_unitario * i.cantidad)      as revenue,
       sum(i.comision)                          as sale_fee
from channel.order_items i
join channel.orders o using (canal, cuenta, external_order_id)
where lower(coalesce(o.estado_canal, '')) not in ('cancelled', 'invalid', 'canceled')
group by 1, 2, 3, 4, 5;

comment on view channel.sales_daily is
  'Ventas por día×cuenta×item (reemplazo de dailytrack daily_sales). Día en '
  'America/Mexico_City; cancelados excluidos SIN IMPORTAR LA CAJA (0030): '
  'estado_canal es text, no citext, y cada canal escribe la cancelación como '
  'quiere (ML cancelled, Amazon Canceled, TikTok CANCELLED). La historia previa '
  'al seam vive en analytics.sales_daily_hist (migración 0006).';

-- `create or replace view` conserva dueño y permisos, pero se re-afirman los
-- dos ajustes que otras migraciones le pusieron encima, para que esta migración
-- sea segura corriendo sola sobre una base recién creada:
--   * security_invoker — lo puso 0025_blindaje_rls.sql:123 (dominio seguridad).
--   * grant select a service_role — lo puso 0005.
alter view channel.sales_daily set (security_invoker = on);
grant select on channel.sales_daily to service_role;
