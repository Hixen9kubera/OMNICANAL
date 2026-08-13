# 15 pedidos duplicados vivos — para revisión de Brandon

> Levantado el **13-ago-2026**. Ningún pedido se ha tocado: esto es solo el
> diagnóstico. La limpieza espera tu visto bueno.

## Lo primero, porque cambia lo que hay que revisar

**Los 31 pedidos involucrados son FULL** (`_ml_logistica = fulfillment`), los 31.
En FULL surte Mercado Libre desde su bodega: el pedido en WooCommerce es un
REGISTRO, no una orden de surtido. **Un registro duplicado no provoca un segundo
envío**, así que no hay riesgo de haber mandado dos veces la mercancía.

Que sean todos FULL no significa nada: el 97.5% de los pedidos lo son.

Corrige una lectura equivocada de la primera pasada: la marca
`order_stock_reduced = 1` en estos pedidos **no** quiere decir que se descontó
inventario. En FULL el pedido nace con esa marca puesta justamente para que Woo
NO descuente (la pieza sale del almacén de ML). No faltan 16 piezas en bodega.

## Lo que sí causan

1. **Inflan los reportes de la tienda**: 16 pedidos de más por **$5,031.69**.
   El cliente pagó una vez en ML; ese monto es fantasma en WooCommerce.
2. **El registro apunta a la copia, no al original.** `channel.orders` guarda
   una fila por orden, así que su `wc_order_id` quedó en el duplicado. El
   próximo cambio de estado se escribiría sobre el pedido equivocado.

## Los 15

| # | Fecha (UTC) | Orden ML | Pedidos Woo | Monto | Separación | Estado |
|---|---|---|---|---|---|---|
| 1 | 19-jul 22:01 | 2000017481549524 | #102093 #102716 | $599.00 | 20.4 h | completado |
| 2 | 19-jul 22:21 | 2000017495713454 | #102116 #102117 | $99.00 | 3 s | uno ya cancelado |
| 3 | 19-jul 22:23 | 2000017479311700 | #102121 #102739 | $199.00 | 20.0 h | completado |
| 4 | 19-jul 22:56 | 2000017479740378 | #102155 #102842 | $961.97 | 19.6 h | completado |
| 5 | 19-jul 23:04 | 2000017485305664 | #102159 #102886 | $214.00 | 19.5 h | completado |
| 6 | 20-jul 00:05 | 2000017497086918 | #102232 #102233 **#102235** | $87.00 | 2 min | **tres copias** |
| 7 | 20-jul 01:14 | 2000017474896264 | #102292 #102746 | $106.10 | 17.2 h | completado |
| 8 | 30-jul 15:01 | 2000017666301578 | #110209 #110210 | $373.89 | 4 s | completado |
| 9 | 07-ago 03:51 | 2000017802673864 | #115521 #115523 | $149.53 | 48 s | completado |
| 10 | 11-ago 16:59 | 2000017879227566 | #118005 #118006 | $246.98 | 3 s | procesando |
| 11 | 11-ago 19:53 | 2000017882564136 | #118219 #118221 | $59.00 | 1 s | completado |
| 12 | 11-ago 19:53 | 2000017882564134 | #118220 #118222 | $178.54 | 2 s | completado |
| 13 | 12-ago 23:21 | 2000017905227724 | #120725 #120793 | $64.00 | 14 min | procesando |
| 14 | 12-ago 23:21 | 2000017905250926 | #120739 #120794 | $447.12 | 13 min | procesando |
| 15 | 12-ago 23:22 | 2000017905245836 | #120742 #120796 | $1,159.56 | 14 min | procesando |

En cada renglón el pedido de la IZQUIERDA es el original (el más antiguo).

## Tres causas distintas, no una

**Ráfaga (1 a 48 segundos) — 6 casos.** Mercado Libre manda el mismo aviso dos
veces casi al instante. Existe un candado en memoria para eso y no alcanza.
**Es la única causa que sigue viva.**

**Los cinco de ~20 horas — 19 y 20 de julio.** Todos de la misma noche. Algo
reprocesó los pedidos del día anterior. Evento único, no goteo; falta saber qué
fue.

**Los tres de 13-14 minutos — 12 de agosto.** Cola del incidente de los 964
fantasma: nacieron a las 23:34, después de la contención (23:29) y antes de que
el arreglo quedara desplegado (23:57). Esa causa ya está cerrada — **cero
duplicados desde entonces**.

## Los incidentes grandes, para contexto

| Día | Pedidos falsos | Estado |
|---|---|---|
| 17-jul | 90 | limpiados (papelera) |
| 18-jul | 74 | limpiados |
| 12-ago | 964 | limpiados |

Los 15 de arriba son los que se escaparon de esas limpiezas.

## Qué te toca decidir

1. **¿Se limpian?** El procedimiento sería: cancelar la copia, mandarla a
   papelera, y repuntar el registro al original. En FULL no hay que devolver
   stock, así que es más simple que la limpieza del 12-ago.
2. **Los "completados"**: los marcó completados el flujo de estados de ML, no
   una persona. Vale confirmar que ninguno haya generado factura o movimiento
   contable duplicado — eso sí no lo podemos ver desde aquí.
3. **El #2 ya tiene una copia cancelada**: alguien lo atendió a mano en su
   momento. Solo faltaría mandarla a papelera.

## Lo que ya quedó puesto

Desde v0.133.0 hay una **alerta automática**: si nace un duplicado, avisa a
Slack el mismo día. Antes no existía — la única alerta de pedidos era la de
"no hay ventas", que mide lo contrario y el 12-ago gritó "sin ventas" mientras
se creaban 964.
