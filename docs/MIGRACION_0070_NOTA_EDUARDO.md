# Migración 0070 — apunte para Eduardo

> **Ya está APLICADA en producción** (kubera, `tukwcvsi`): 9-oct-2026, 18:45 UTC,
> por el chat de Inventario, con la instrucción explícita de Brandon («dale, crea
> la migración de lo que necesitas y todos los archivados no los traigas»). No
> pasó por acta. La tabla nueva **ya tiene datos** y un vigilante del backend la
> mantiene al día. **En el sandbox NO está aplicada.**

Archivo: `supabase/migrations/0070_almacen_historial_movimientos.sql`.
Quien la llena: `backend/services/almacen_odoo.py` (job `almacen_odoo` del
scheduler, cada 30 min) y, a mano, `backend/scripts/copiar_almacen_odoo.py`.

## Por qué existe

Brandon, 9-oct: TEXCO II deja de operarse desde Odoo y pasa a kubera en unas dos
semanas. Pidió traer a kubera el historial de movimientos de TEXCO II «para que
lo tengamos como fuente de información», con un delta que observe a Odoo cada
30 minutos «hasta su desactivación de bodega».

## Qué hizo la migración

**1. Tabla nueva `almacen.historial_movimientos`**: un renglón por línea de
movimiento HECHA en Odoo (`stock.move.line`), vista desde un cedis.

| Columna | Qué es |
|---|---|
| `cedis` | Código de la bodega (`TEX2`). Obligatorio, con llave a `almacen.almacenes`. |
| `sku`, `fecha`, `causa` | Qué, cuándo y por qué. La causa usa las palabras del panel (`services.odoo._causa`); no lleva CHECK con la lista. |
| `piezas`, `delta` | Cuántas se movieron y qué le hizo al cedis: + entran, − salen, 0 se movieron adentro. |
| `ubicacion_origen`, `ubicacion_destino` | Escritas como en `almacen.locations`; sólo del lado que está dentro del cedis. |
| `documento`, `referencia`, `contraparte`, `tipo_operacion`, `quien` | El picking, la orden de venta o compra, el socio, el tipo de operación y el usuario de Odoo. |
| `odoo_move_line_id`, `odoo_product_id`, `odoo_origen`, `odoo_destino` | De qué línea de Odoo salió y las rutas completas. |
| `odoo_escrito_at` | `write_date` de la línea: la marca de agua del delta. |

- Único `(odoo_move_line_id, cedis)`.
- Un CHECK amarra el signo al lado: sólo destino en el cedis = `delta = piezas`;
  sólo origen = `delta = -piezas`; los dos = `delta = 0`.
- RLS encendido sin políticas; `service_role` con todo; `anon` y
  `authenticated` sin nada.

**2. `almacen.locations` (0069) sin productos archivados.** Regla nueva de
Brandon: «archivados pueden ser error». La migración borró los 37 renglones de
productos archivados (22,250 piezas) y quitó la columna `archivado_odoo`. El
único de `(cedis, sku, ubicacion)` dejó de ser parcial y `locations_odoo_chk`
se recreó sin la columna.

**3. Una sola función para `actualizado_at`**: `almacen.tg_tocar_actualizado_at()`
(con `search_path = pg_catalog`) para las dos tablas. `almacen.locations_tocar()`
se borró.

Dejó su renglón en `ops.migraciones`.

## Cómo se aplicó

1. Ensayo en producción: la migración entera dentro de una transacción que
   terminó en `ROLLBACK`, con 17 comprobaciones y 6 rechazos esperados.
2. La migración tal cual, por el puerto 5432 (modo sesión), `lock_timeout` 5 s.
   Las mismas 17 comprobaciones después del `COMMIT`.
3. `copiar_almacen_odoo.py --destino prod`, en ensayo y luego con `--aplicar`.

Probada antes en local (Postgres 16) sobre `ov_fixture → 0064 → 0065 → 0068 →
0069` con la foto cargada como estaba en producción, dos veces.

## Lo que hay en los datos (medido el 9-oct)

- **Historial:** 6,307 movimientos de 1,412 SKUs, del 21-may al 9-oct.
  Por causa: 1,918 preparación, 1,817 ajuste, 1,618 entrada, 471 envío a canal,
  469 traspaso, 7 merma, 7 venta.
- **Foto:** 1,628 renglones, 1,225 SKUs, 454,145 piezas.
- **El historial reproduce la foto:** Σ `delta` = Σ `piezas` en 1,225 de 1,225
  SKUs, y por ubicación en 1,628 de 1,628 pares SKU + ubicación.
- **No se trajeron 290 líneas de 110 productos archivados.** En 86 de ellos
  Odoo borró la existencia sin dejar movimiento (11,501 piezas que el historial
  decía que seguían ahí).
- **21 SKUs perdieron su rack en la foto**: lo tenía sólo el producto archivado
  gemelo; el activo está en la raíz y quedó como `SIN UBICAR`.
- Toda salida a un socio que es canal (FULL, AMAZON, temu, tiktokshop) sale con
  causa `envio_full`, también las ventas de Temu y TikTok: así clasifica el panel.
  El canal está en `contraparte`.

## El vigilante

`services/almacen_odoo.py`, job `almacen_odoo` (scheduler del backend):

- Cada 30 min, primera pasada a los 4 min del arranque.
- Pide a Odoo las líneas ESCRITAS desde la mayor `odoo_escrito_at`, con 15 min
  de traslape. La primera pasada de cada arranque y una vez al día relee todo y
  concilia (borra lo que ya no viene: un producto que se archivó después).
- En la misma pasada concilia la foto de `almacen.locations` (escribe sólo lo
  que cambió).
- Mide el cuadre historial-foto y avisa en el log si un SKU lleva dos pasadas
  sin cuadrar.
- Sólo lee Odoo (`search_read`, `read`) y sólo escribe estas dos tablas.
- **No tiene variable en Railway.** Se detiene sola cuando
  `almacen.almacenes.fuente` de TEX2 deje de ser `odoo`. La foto se detiene
  antes si aparece un renglón con `origen = 'kubera'` en ese cedis. Freno de
  emergencia sin deploy: `ALMACEN_ODOO_ENABLED=false`.

## Lo que queda abierto, y es tuyo decidir

- **Sandbox:** aplicar ahí la 0069 y la 0070.
- **`supabase/schema_manifest.json`** y `ESQUEMAS_PROPIOS` de
  `aplicar_migraciones.py`: no los toqué.
- **El día del corte.** `fuente` es fija por trigger, así que pasar TEX2 a
  kubera es diseño pendiente. Ese día el vigilante se calla y el historial queda
  congelado como lo dejó Odoo; los movimientos nuevos nacen en
  `almacen.stock_mov`.
- **TEXCO I y DROP OFF:** la tabla y el vigilante sirven para cualquier cedis
  (`CEDIS_OBSERVADOS`). Todo Odoo son 63,316 líneas. No se cargaron: Brandon
  pidió TEXCO II.

## Reversa

`drop table almacen.historial_movimientos;` y `ALMACEN_ODOO_ENABLED=false` (o
revertir el commit). La columna `archivado_odoo` de `locations` no vuelve sola.
