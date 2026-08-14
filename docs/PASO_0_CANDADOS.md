# PASO 0 — Los candados que viven en una bitácora

> Planteamiento medido contra producción el 14-ago-2026. **Todavía no se ha
> tocado una línea de código.** Orden acordado con Eduardo: planteamiento →
> pruebas en sandbox → verificación → recién entonces el cambio.

## El resumen en una frase

`fanout_log` es una **bitácora** —un registro de lo que pasó— y tres decisiones
del sistema la usan como si fuera una **tabla de estado**. Mientras esa tabla
exista y esté sana no se nota. El problema es que el desmantelamiento la va a
mover, y una bitácora movida a medias contesta mal.

## Los tres estados escondidos, con sus números reales

`fanout_log`: 7,860 filas, 2.3 MB, del 24-jul a hoy, y **sigue escribiéndose**.
De todo eso, lo que es ESTADO y no registro son **23 filas**:

| Estado | Pregunta que contesta | Filas | Última |
|---|---|---|---|
| `full_compensado` por `wc_id` | ¿ya le devolví el stock a este pedido? | **6** | 28-jul |
| `full_ingreso`/`full_retiro`/`fba_ingreso` por `operacion_id` | ¿ya apliqué este movimiento de bodega? | **17** | 27-jul |
| marca de agua FBA por `sku` | ¿en cuánto estaba el FBA la última vez que miré? | **99** (de texto libre) | 13-ago |

Migrar 23 filas de estado es trivial. Lo que no es trivial es que hoy nadie
sabría decir cuáles son sin este documento.

## Por qué cada uno importa

### Candado 1 — `pedidos_ml._ya_compensado` (línea 389)

**Corre hoy, en el flujo real de pedidos.**

Un pedido FULL/FBA no debería descontar de nuestra bodega, pero Woo lo descuenta
igual (la meta que lo protegía la filtra la REST — hallazgo del 28-jul). Así que
se COMPENSA: se lee lo que Woo descontó y se devuelve.

**Verificado hoy, y es lo que hace peligroso el candado:** compensar **no borra**
`_reduced_stock` en Woo. Esa meta solo la borra Woo cuando repone por su cuenta
al cancelar. Entonces una segunda compensación leería exactamente lo mismo y
devolvería las piezas **otra vez**.

No hay una segunda red. `_ya_compensado` es la única protección, y termina así:

```python
except Exception:
    return False        # "no lo hice"
```

Si la base parpadea, el candado no contesta *"no sé"*: contesta *"no lo hice"*, y
el sistema compensa de nuevo. Palabra por palabra el mecanismo de los 964 pedidos
fantasma — confundir **"no sé"** con **"no hay"**.

### Candado 2 — `stock_full._ya_procesada` (línea 131)

Protege movimientos reales de bodega: `full_ingreso`, `full_retiro`,
`fba_ingreso`. Mismo `except → return False`.

Este candado **ya lleva una corrección buena** que conviene no perder al mudarlo:
ignora las filas cuyo `resultado` empieza con `ERROR`, porque antes bastaba
cualquier fila `full_%` y un 502 del WAF de Hostinger sellaba el movimiento para
siempre (auditoría del 27-jul). Lo que se migre tiene que conservar esa
distinción entre *"se intentó y falló"* y *"se aplicó"*.

Hoy está contenido: `FULL_WATCH_ENABLED=true` pero `full_watch_solo_registro`
viene en `True` por defecto y no está definida en Railway, así que anota
`fba_ingreso_sim` y no mueve nada. **La contención es una variable indefinida.**

### El agravante — la tabla se resucita sola, vacía

`_ya_procesada` llama a `fanout_stock._asegurar_schema()` **antes** de leer, y ahí
vive un `CREATE TABLE IF NOT EXISTS fanout_log` (línea 597).

O sea: el día que se borre la tabla, el propio candado la crea en blanco y
después le pregunta. Una tabla vacía siempre contesta *"nunca lo hiciste"*.

**Borrar esa tabla no apaga el candado — lo pone a decir que sí a todo.** Y
borrarla es exactamente lo que el desmantelamiento va a hacer.

## ⚠️ Una corrección al plan del consejo, medida

El plan decía: *"la marca de agua se parsea del texto de `resultado`; ya existe la
fuente buena: `channel_read.stock_fba_amazon()`"*.

**Eso es falso, y seguirlo reintroduciría el bug que ese código ya arregló.**

Medido hoy sobre los 99 SKUs con marca de agua: **96 tienen un valor distinto**
al `stock_fba` de `channel.listings`.

| SKU | marca de agua | `channel.listings` |
|---|---|---|
| `ACC-0654-NEG` | 49 | 31 |
| `ACC-0648-NEG-GRI-XL` | 60 | 40 |
| `ACC-0650-MET` | 30 | 20 |

No es un desfase: **son cosas distintas**.

- La marca de agua es *"cuánto vi la última vez que procesé un evento de este
  SKU"*.
- `channel.listings.stock_fba` es *"cuánto vio el sync hace ≤15 min"*.

El comentario del propio código lo explica: el sync refresca cada 15 min, así que
usarlo como referencia hacía que **el mismo ingreso se contara dos veces**.
Cambiar el regex por el valor del sync no es limpiar: es volver a meter el bug.

Lo que hay que hacer es darle a la marca de agua **su propia columna**, que es lo
que nunca tuvo. El defecto no es de dónde sale el número: es que vive dentro de
una frase.

## El diseño propuesto: tres estados, tres casas

Los tres NO comparten solución. Meterlos juntos "porque una tabla ya existe y es
cómoda" repetiría el error de origen.

| Estado | Casa | Por qué |
|---|---|---|
| compensación | columna en `channel.orders` | es estado **de ese pedido**, y el pedido ya vive en kubera |
| operación aplicada | tabla nueva `ops.fulfillment_operations` | su unidad es la **operación de bodega**; no tiene pedido al que pegarse |
| marca de agua FBA | tabla nueva `ops.fba_watermark` | su unidad es el **SKU**, y es la memoria de un vigilante — hermana de `ops.stock_watch_photo` |

Detalles ya verificados:

- `channel.orders` tiene `wc_order_id` (int8) y PK `(canal, cuenta,
  external_order_id)`. La compensación se busca por `wc_order_id`, así que la
  columna nueva viene con su índice.
- `ops.fulfillment_operations` guarda `operacion_id` (PK), `sku`, `cuenta`,
  `accion` y `aplicada_at`. **Solo se escribe cuando la operación se APLICA de
  verdad** — así la distinción "se intentó y falló" ≠ "se aplicó" deja de
  depender de parsear el texto `ERROR%`.

## Y el cambio que de verdad importa: que dejen de mentir

En los dos candados, `except → return False` se reemplaza por **propagar**.

Aquí conviene ser preciso, porque el mismo patrón **no siempre está mal**. En
`imagenes_amazon._cache_get` (paso 4) se conservó a propósito: ahí un fallo
significa "no sé si ya procesé esta imagen" y equivocarse cuesta reprocesar —
caro, no incorrecto.

**La diferencia no es el patrón: es qué pasa cuando la respuesta está mal.**

| | si se equivoca |
|---|---|
| caché de imágenes | reprocesa una imagen |
| candado de compensación | **devuelve piezas que no salieron** |
| candado de bodega | **aplica dos veces un movimiento de inventario** |

## El orden

1. **Migración** (`0022`): la columna en `channel.orders` y las dos tablas nuevas.
2. **Copiar las 23 filas de estado + las 99 marcas de agua**, con su fecha real.
3. **Gemelas** en un módulo nuevo, con flag `SUPABASE_READ_CANDADOS` apagado.
4. **Doble lectura con log de discrepancia**: los candados preguntan a los dos
   lados, **usan la respuesta vieja** y anotan cuándo difieren. Es el método que
   aportó sonnet y aquí es obligatorio: son 23 filas, cualquier diferencia es 100%
   del problema.
5. Encender la lectura. **Toca inventario: regla 3.**
6. **Al final, y no antes**: quitar el `CREATE TABLE IF NOT EXISTS`, repuntar los
   8 escritores y recién ahí `fanout_log` puede moverse (paso 5).

## Las pruebas en sandbox

Hay precedente y molde: `probar_corte_costing.py` stubea MySQL por completo y
corre contra el sandbox con guardia triple de ref. El mismo esquema aplica:

| Prueba | Qué demuestra |
|---|---|
| T1 · candado con kubera arriba | contesta lo mismo que MySQL en los 23 casos |
| T2 · **kubera caída** | **PROPAGA** — no contesta "no lo hice" |
| T3 · tabla de estado vacía | un `operacion_id` desconocido da "no aplicada" (y eso está bien: es el caso legítimo) |
| T4 · `fanout_log` borrada | el candado NO revive la tabla ni contesta que sí a todo |
| T5 · intento fallido | una operación con `ERROR` sigue siendo reintentable |
| T6 · marca de agua | el valor migrado ≠ `channel.listings.stock_fba`, y el vigilante usa el suyo |

T2 y T4 son las que hoy fallarían. Son la razón del paso.

## La verificación final, antes de encender

- Los 23 estados contestan igual por los dos caminos, uno por uno.
- Las 99 marcas de agua viajaron con su valor exacto (no el del sync).
- Días de doble lectura con **cero discrepancias** registradas.
- `full_watch_solo_registro` sigue en `True` durante toda la transición.

## La condición que no se negocia

**El día que se quite el "solo registro" del vigilante FULL, esto tiene que estar
arreglado ANTES.** Hoy el sistema está protegido por una variable que nadie
definió — no por el candado.
