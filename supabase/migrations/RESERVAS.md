# Reservas de números de migración

Una migración se numera **aquí antes de escribir el `.sql`**. La razón son los choques que ya pasaron:
- **Dos o más archivos con el mismo número:** 0004, 0018, 0022, 0023, 0025, 0033, 0043, 0044, 0050, 0051 (con tres), 0052, 0054 y 0070, contando las ramas.
- **Renumeradas en su rama:** varias, entre ellas la 0059 (nació como 0058), la 0062 (nació como 0061), la 0072 (nació como 0071) y la 0073 (pasó por 0070 y 0072).

## Cómo se reserva

1. `git fetch` y leer este archivo en `origin/main`, no en la copia local.
2. Tomar el **siguiente número libre** de la sección «Siguiente libre» y agregar su fila aquí con número, nombre, dueño, rama y estado `reservada`.
   - Esa fila se sube a main sola, antes que el `.sql`.
   - Así otra sesión ve la reserva aunque la rama siga local.
3. Cuando se aplique en un ambiente, actualizar el estado en el mismo commit del `.sql` o en el siguiente.
4. Si la migración se abandona, la fila se queda con estado `quemada`.
   - **Un número no se reusa nunca**, aunque nunca haya llegado a producción: el sandbox o una rama ya pueden tenerlo aplicado.
   - Ya pasó con la 0008 y la 0054.

**Fuente de verdad de lo aplicado:** `ops.migraciones`, y la de cada ambiente.
- En **producción** arranca en la 0064. Lo anterior no tiene registro y se comprueba objeto por objeto.
- En el **sandbox**, el runner también registró migraciones anteriores (las adoptó o las corrió atrasadas).

Este archivo dice **quién tiene cada número**, no qué está aplicado. Si los dos no coinciden, manda `ops.migraciones`.

**Ramas locales:** las que se marcan *(local)* no están en `origin`. Quien lea esto desde GitHub no las va a encontrar; hay que preguntarle a su dueño.

## Siguiente libre

**0086**. La 0081 sigue apartada, y la 0084 y la 0085 se apartaron para 3.3; ver abajo.

## Estado desde la 0050 (medido el 3-nov-2026 y actualizado el 6-nov; horas en UTC)

| N.º | Nombre | En main | En producción | Dónde vive / nota |
|---|---|---|---|---|
| 0050 | `retencion_webhooks_por_canal` | sí | sí | Cambia la función de purga: 90 días para TikTok, Temu, Odoo y alertas, y 3 días para lo demás. La purga la corre el pg_cron diario. Re-correrla no hace daño. **La que no se re-corre sola es la 0004**: reviviría la firma vieja y la purga fallaría cada noche. Choca con `0050_ops_permisos` (rama de respaldo de Accesos) |
| 0051 | `modo_publicacion` | sí | sí | Choca con dos archivos de Accesos: `0051_ops_permisos` en `feat/productos-lee-kubera` *(local)* y `0051_core_usuario_cuentas` en su respaldo. Si Accesos revive, toma números nuevos |
| 0052 | `limpieza_tablas_muertas` | **no** | sí (antes del 23-sep; sin registro de la fecha exacta) | En `retiro/tablas-muertas-0052` y `feat/calidad-ml`, las dos *(local)*. **Su commit trae datos de producción en `supabase/retiros/0052/`: a main va solo el `.sql`, nunca la carpeta `retiros/`.** También la usa `0052_core_usuario_cuentas.sql` de `feat/productos-lee-kubera` *(local)*, que tiene que renumerarse |
| 0053 | `enrich_listing_health` | sí | sí | |
| 0054 | — | no | no | **Quemada.** `0054_fulfillment_solicitudes` estuvo en main de la v0.556.0 a la v0.566.0 y se retiró en la v0.567.0; sigue en las puntas de `feat/packing-lists-supabase`, `feat/tokens-clave-cifrada` y `feat/tokens-solo-kubera`. `0054_enrich_listing_quality_experiencia` solo quedó en la historia de `feat/calidad-ml`. Si alguna revive, toma el siguiente libre |
| 0055 | `costing_packing_archivos` | sí | sí | |
| 0056 | `costing_caja_compartida_huella` | sí | sí | Solo agrega `archivo_sha256` a `costing.caja_compartida` |
| 0057 | `costing_packing_ubicaciones` | sí | sí | |
| 0058 | `ops_checklist_almacen` | sí | sí | La versión sin `on delete cascade` |
| 0059 | `channel_ml_category_tree` | sí | sí | Nació como 0058 y se renumeró antes de su primer commit |
| 0060 | `costing_sku_contenedor` | sí | **no** | Está en main sin aplicar. Decidir si se aplica o se retira |
| 0061 | `ops_temu_guias_compras` | sí | sí | |
| 0062 | `market_series` | **no** | no | En `feat/mercado-0062-v0605` *(local)*. Nació como 0061. El acta tiene 2 de 3 firmas. Apartada para `market` |
| 0063 | `enrich_market_rival_juicio` | sí | sí | |
| 0064–0071 | órdenes de venta, inventario, esquemas `ventas` y `almacen`, `locations`, historial, editar confirmada | sí | sí | En `ops.migraciones`. La 0070 choca con un borrador de cuarentena en una rama de respaldo |
| 0072 | `limpieza_fase1` | **no** | sí (14-oct, 01:33) | En `chore/limpieza-fase1` y debajo de la 0073 en `feat/cuarentena-vendible`, las dos *(local)*. Nació como 0071. Falta subir el `.sql` a main |
| 0073 | `ops_stock_watch_cuarentena` | no | no | En `feat/cuarentena-vendible` *(local)*; solo en el sandbox. Apartada para la cuarentena vendible; no se reusa |
| 0074 | `pricing_historial` | sí | sí (20-oct) | |
| 0075–0077 | almacén: ubicaciones y `odoo_historia`, fuente única, traslados y claves | sí | sí (28-oct, de 04:19 a 05:04) | Acta 1.11, el esquema del libro de T0 |
| 0078 | `channel_categoria_vectores` | sí | sí (27-oct) | |
| 0079 | `channel_orders_registrado_at` | sí | sí (28-oct, 17:40) | Acta 2.2 + 2.6 |
| 0080 | `core_products_familia_ciclo` (3.2, diseño del 2-nov) | sí (v0.723.0, en el mismo push que esta fila) | **sí** (6-nov, 16:42, `ops.migraciones` id 21, variante d2) | De WOO SALIDA, para la 3.2 rediseñada. Publicado calculado desde los canales y archivado en columnas propias. La aplicó su acta (`_paso_prod_catalogo_3_2`, paso 2, con el sí de Eduardo), no el push. En el sandbox, la nueva deshizo a la vieja y se reaplicó; `ops.migraciones` tiene las filas «rehecha» y «reaplicada». La vieja sigue en `feat/catalogo-3-2` y `feat/catalogo-3-3` *(local)*. Número por confirmar con Brandon |
| 0081 | `channel_orders_ultimo_evento` (nombre tentativo) | no | no | **Reservada** para WOO SALIDA: diseño del job de 2.15 (`ultimo_evento_at`), **sin construir**. Solo DDL, sin relleno. Se construye si Eduardo la decide junto con T0, después de que MAESTRO revise el diseño. Si se abandona, queda `quemada` |
| 0082 | `channel_categoria_general` | sí (v0.727.0) | **sí** (4-nov, 22:13, `ops.migraciones` id 20, su acta `_paso_prod_categoria_general`) | Tabla aparte con la categoría GENERAL (raíz de ML) por SKU, decidida con la foto del packing list; no toca `channel.product_category`. En el sandbox sí (4-nov, 22:05 UTC, 10,391 filas). Para saber si ya está en producción manda `ops.migraciones` |
| 0083 | `almacen_cuarentena_revisadas` | sí (vigilante de cuarentena, 6-nov) | no al subir este push; la aplica Eduardo con su acta | Tabla nueva, solo agregar: qué llegadas a la cuarentena de Odoo ya vio alguien en el popup de Inventario. No toca otras tablas. Probada en una base local (dos corridas, candados de update/delete/truncate y de usuario). Nota: `docs/MIGRACION_0083_NOTA_EDUARDO.md` |
| 0084 | `core_products_ciclo_reglas` (nombre tentativo) | no | no | **Reservada** para WOO SALIDA, 3.3 fase B; era la 0083, que tomó Brandon el 6-nov para `almacen_cuarentena_revisadas` (diseño `_paso_prod_catalogo_3_3`, decisiones de Eduardo del 6-nov): trigger de transiciones de `estado_ciclo`, `core.products_ciclo_historial` (solo agregar) y el relleno por familia de D1 (4,891 filas medidas el 6-nov). Sin construir. Número por confirmar con Brandon |
| 0085 | `ops_woo_copia_cola` (nombre tentativo) | no | no | **Reservada** para WOO SALIDA, 3.3 fase C: `core.woo_status_deseado` y la cola durable de copia a Woo. Nace vacía. Sin construir. Número por confirmar con Brandon |

## Antes de la 0050

- **0008:** se quemó dos veces. `0008_competencia` y `0008_temporadas` se crearon el 3-ago en líneas distintas y se borraron el 3 y el 5-ago. Ninguna dejó objetos.
- **Números con dos archivos:** 0004, 0018, 0022 (por una rama), 0023, 0025, 0033, 0043 y 0044. El runner los aplica en orden de nombre de archivo, así que no se renombran.
- **0033:** la `0033_ops_odoo_sale_orders.sql` original **nunca se aplicó en producción**. El 26-ago la reemplazaron la 0035 (`ops_automatizacion_flags`) y la 0036 (`ops_odoo_bitacora`). En una base nueva, `0033_ops_odoo_sale_orders_forma_prod` quita la forma vieja para que la 0035 y la 0036 creen la de producción.
