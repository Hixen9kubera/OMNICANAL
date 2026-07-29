# Notas para Lalo — cambios del 29-jul que tocan la migración

> Contexto: durante el 28 y 29 de julio se corrigieron varias cosas de inventario
> y del panel. Varias tocan tablas que alimentan tu espejo, así que las anoto
> aquí para que no te lleguen como sorpresa en las actas.

## 1. El stock de Odoo cambió de columna (v0.31.0) — **esto sí te va a mover deltas**

Estábamos leyendo **`qty_available`** ("A la mano") y ahora leemos **`free_qty`**
("Disponible"). Lo señaló el equipo de Odoo con `JUGU-0066-MUL`: 60 a la mano, 50
en Saliente, **10 realmente libres** — y publicábamos 60.

- **323 SKUs bajaron de stock en WooCommerce**, −14,112 piezas. Todas BAJADAS,
  ninguna subida.
- Eso se propagó a `canal_inventario` y de ahí a tu espejo `channel.listings`.
- **No es un error de datos: es una corrección.** Si ves un delta grande de stock
  con fecha 29-jul, viene de aquí.

Efecto de paso: esto cubre el hueco que perseguía el vigilante de FULL
(`stock_full`, que sigue apagado). Odoo ya descuenta lo que sale hacia la bodega
de ML en cuanto lo reserva.

## 2. 100 productos pasaron de `draft`/`inprogress` a `publish` (v0.29.0)

Había **125 SKUs publicados en un canal pero en borrador en Woo** — invisibles en
todo el panel. Uno de ellos (`TEC-1841-ROS`) **vendió $1,585.92 estando oculto**.

- Se corrigieron 100 productos de WooCommerce (los 125 SKUs colapsan en 100
  productos porque varias variantes comparten padre).
- El publicador ahora marca `publish` al publicar en un canal, para que no vuelva
  a pasar.
- **Impacto para ti**: cambia `post_status` en `wp_posts` y por lo tanto el
  estado que refleje tu ETL de productos.

## 3. `canal_inventario` — dos hallazgos que te conciernen

Los audité pero **NO los toqué**, justo porque alimentan tu espejo:

- **Nunca borra nada.** No existe un solo `DELETE FROM canal_inventario` en el
  repo. Los listados que Amazon da de baja se quedan dentro para siempre.
  Medido contra SP-API: **~14-17% de las 1,660 filas de Amazon devuelven HTTP
  404** (el listado ya no existe). Por eso el panel dice ~1,660 y Seller Central
  dice 1,377.
- **La `situacion` de Amazon es una copia de nuestra bitácora, no del estado
  real.** El barrido lee `amazon_progress` (nuestro log de publicación) y jamás
  le pregunta a Amazon si el listado sigue vivo. Las 1,660 filas dicen
  `PUBLISHED`, incluidas las muertas.

**Antes de tocar esto quiero coordinarlo contigo**, porque la opción natural
(marcar `situacion='closed'` en las muertas) cambiaría filas que tu espejo ya
replicó, y un `DELETE` físico te generaría deltas en las actas.

## 4. Cambio en el `ON DUPLICATE KEY` de `canal_inventario` (v0.32.0)

`item_id`, `precio` y `stock_real` ahora usan `COALESCE(VALUES(x), x)` en vez de
pisarse directo. Antes, cada vuelta del barrido masivo **borraba** el ASIN, el
precio y el stock FBM que el refresco individual sí había guardado bien.

`stock_full` y `stock_fba` se quedaron **sin** COALESCE a propósito: ahí un 0 y
un NULL sí significan cosas distintas.

Esto reduce la cantidad de campos que se van a NULL y vuelven, así que **debería
BAJAR el ruido en tu `channel.listing_history`**, no subirlo.

## 5. Tablas temporales

Siguen las dos de siempre, documentadas en
[TABLAS_TEMPORALES.md](TABLAS_TEMPORALES.md): `fanout_log` y `stock_watch_foto`.
Ambas son locales a propósito y se borran cuando Brandon dé la indicación.
Ninguna es fuente de verdad de nada.

## 6. Lo que NO toqué de tu territorio

Para que conste: no toqué esquemas de Supabase, ni los espejos
(`channel_mirror`, `costing_mirror`), ni los ETLs, ni los jobs de deltas, ni el
esquema de `canal_inventario`. El sync de 15 min sigue encendido alimentando tu
espejo.
