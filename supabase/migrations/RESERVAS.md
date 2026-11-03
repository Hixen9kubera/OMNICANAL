# Reservas de números de migración

Una migración se numera **aquí antes de escribir el `.sql`**. La razón son los choques que ya pasaron: 0004, 0018, 0023, 0025, 0033, 0043, 0044, 0052 y 0054 tienen dos archivos, y la 0058 y la 0062 se tuvieron que renumerar en su rama.

## Cómo se reserva

1. `git fetch` y leer este archivo en `origin/main`, no en la copia local.
2. Tomar el **siguiente número libre** de la tabla «Siguiente libre» y agregar su fila en este archivo: número, nombre, dueño, rama y estado `reservada`.
   - Esa fila se sube a main sola, antes del `.sql`.
   - Así otra sesión ve la reserva aunque la rama siga local.
3. Al aplicar en un ambiente, actualizar la columna de estado en el mismo commit que lleva el `.sql`, o en el siguiente.
4. Si la migración se abandona, la fila se queda con estado `quemada`. **Un número no se reusa nunca**, aunque nunca haya llegado a producción: el sandbox o una rama ya pueden tenerlo aplicado.

**Fuente de verdad de lo aplicado:** en producción y en el sandbox, `ops.migraciones` desde la 0064. Las anteriores no tienen registro; para saber si están aplicadas se compara objeto por objeto. Este archivo dice **quién tiene cada número**, no qué está aplicado: si los dos no coinciden, manda `ops.migraciones`.

## Siguiente libre

**0081**. La 0080 está apartada; ver abajo.

## Estado desde la 0050 (medido el 3-nov-2026)

| N.º | Nombre | En main | En producción | Dónde vive / nota |
|---|---|---|---|---|
| 0050 | `retencion_webhooks_por_canal` | sí | sí | No se re-corre: borra webhooks viejos |
| 0051 | `modo_publicacion` | sí | sí | |
| 0052 | `limpieza_tablas_muertas` | **no** | sí (23-sep) | Rama `retiro/tablas-muertas-0052` y `feat/calidad-ml`. **Su commit trae datos de producción en `supabase/retiros/0052/`: a main solo va el `.sql`, nunca la carpeta `retiros/`.** La `0052_core_usuario_cuentas.sql` de `feat/productos-lee-kubera` choca: si esa rama revive, toma el siguiente libre |
| 0053 | `enrich_listing_health` | sí | sí | |
| 0054 | — | no | no | **Quemada.** Dos borradores en ramas de respaldo (`enrich_listing_quality_experiencia`, `fulfillment_solicitudes`). Si alguno revive, toma el siguiente libre |
| 0055–0057 | packing (`costing_packing_*`) | sí | sí | |
| 0058 | `ops_checklist_almacen` | sí | sí | La versión sin `on delete cascade` |
| 0059 | `channel_ml_category_tree` | sí | sí | |
| 0060 | `costing_sku_contenedor` | sí | **no** | En main sin aplicar; decidir si se aplica o se retira |
| 0061 | `ops_temu_guias_compras` | sí | sí | |
| 0062 | `market_series` | **no** | no | Rama `feat/mercado-0062-v0605`. El acta tiene 2 de 3 firmas. Apartada para `market` |
| 0063 | `enrich_market_rival_juicio` | sí | sí | |
| 0064–0071 | órdenes de venta, inventario, esquemas `ventas`/`almacen`, `locations`, historial, editar confirmada | sí | sí | En `ops.migraciones` |
| 0072 | `limpieza_fase1` | **no** | sí (14-oct) | Solo en `chore/limpieza-fase1`. Falta subir el `.sql` a main |
| 0073 | `ops_stock_watch_cuarentena` | no | no | Rama `feat/cuarentena-vendible`, solo en el sandbox. Apartada para la cuarentena vendible; no reusar |
| 0074 | `pricing_historial` | sí | sí (20-oct) | |
| 0075–0077 | almacén: ubicaciones y `odoo_historia`, fuente única, traslados y claves | sí | sí (28-oct) | Acta 1.11 (esquema del libro de T0) |
| 0078 | `channel_categoria_vectores` | sí | sí (27-oct) | |
| 0079 | `channel_orders_registrado_at` | sí | sí (28-oct) | Acta 2.2 + 2.6 |
| 0080 | `core_products_familia` (3.2) | no | no | **Reservada** para WOO SALIDA, para la 3.2 rediseñada: publicado calculado desde los canales y archivado en columnas propias. La versión vieja (`feat/catalogo-3-2`, `0080_core_products_familia_ciclo.sql`) está aplicada **solo en el sandbox**: la nueva tiene que partir de ese estado o deshacerlo primero. Número por confirmar con Brandon |

## Huecos y duplicados anteriores a la 0050

- No hay 0008.
- Hay dos archivos con el mismo número en 0004, 0018, 0023, 0025, 0033, 0043 y 0044. El runner los aplica en orden de nombre de archivo, así que no se renombran.
- La `0033_ops_odoo_sale_orders.sql` original quedó reemplazada en producción por la 0036 (`ops_odoo_bitacora`).
