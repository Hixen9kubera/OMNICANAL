# Migración 0064 (propuesta del 2-oct-2026) — REEMPLAZADA

> **Este documento ya no vale. No apliques nada de lo que decía.**

El 2-oct-2026 el chat que construye **Inventario → Órdenes de venta** (ClickUp
`86bcbfnkw`) dejó aquí su propuesta de migración para que la revisara el chat
de base de datos antes de crear las tablas. Esa revisión terminó en un
**rediseño** (Eduardo, plan v3, 5-oct-2026), y lo que quedó hecho es otra cosa:

| Lo vigente | Dónde |
|---|---|
| La migración de órdenes de venta, revisada | `supabase/migrations/0064_ops_ordenes_venta.sql` |
| El inventario de kubera (bodegas, saldo y libro) | `supabase/migrations/0065_ops_inventario_kubera.sql` |
| El contrato de la base para el código | `docs/MIGRACION_0064_0065_GUIA_AGENTE.md` |
| Lo que el código hace con ese contrato | `docs/ORDENES_VENTA.md` |

## Qué cambió respecto a la propuesta

| La propuesta del 2-oct | Lo que quedó |
|---|---|
| Reserva contra la foto de stock de Odoo (`ops.ov_stock_base_v` sobre `stock_watch_photo`) | Aparta contra el saldo propio de kubera (`ops.stock_almacen`: `libre = fisico − apartado`) |
| `ops.ov_stock` (reserva propia por SKU) | No existe: el apartado vive en `ops.stock_almacen`, por SKU **y bodega** |
| Un almacén por orden | La bodega va **por renglón**, y sólo bodegas de kubera que admiten órdenes |
| Confirmar reserva lo que haya (completa / parcial / sin stock) | Apartar es **todo o nada** |
| Un admin edita una confirmada o la regresa a borrador | Fuera de borrador el contenido es **inmutable** (trigger); se cancela o se borra |
| DELIVERED de la orden entera | Entrega **por renglón**, parcial, con su movimiento en el libro (`ops.stock_mov`) |
| El bucket `ordenes-venta` se creaba en la migración | No hay bucket todavía; las guías con la dirección del comprador no se guardan |

El texto y el SQL de la propuesta original siguen en el historial de git de
este archivo (commit `a68a817`), sólo como antecedente.
