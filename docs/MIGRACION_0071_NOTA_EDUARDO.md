# Migración 0071 — apunte para Eduardo

> **Cambia una decisión tuya, así que léela con calma.** Desde la 0064, una orden
> de venta confirmada tenía el contenido congelado por dos guardias de la base.
> Brandon pidió el 9-oct-2026 que **cualquiera que escribe pueda editar una orden
> ya confirmada** («en caso de que se requiera hacer cualquier cambio») y que
> también **cualquiera pueda cancelarla** antes de entregar. La 0071 abre una
> puerta en esas guardias —una sola, y estrecha— para que eso sea posible.
>
> Aplicada en producción por el chat de Órdenes de venta, con el dale explícito
> de Brandon, sin acta. **En el sandbox NO está aplicada.**

Archivo: `supabase/migrations/0071_ov_editar_confirmada.sql`.
Requiere la 0068 (las tablas ya viven en `ventas` y `almacen`).

## Qué cambia en la base

1. **`ops.tg_ov_ordenes_guarda` y `ops.tg_ov_lineas_guarda`** — las dos son el
   texto de producción al 9-oct (post-0068), copiado tal cual, con una puerta
   agregada. Sólo cuenta si la transacción trae

   ```sql
   set local app.ov_edicion = '<id de la OV>';
   ```

   y esa OV sigue `confirmada`, sin borrar y sin la marca del canal
   (`canal_cancelo_at is null`). Entonces:

   - del encabezado cambian `cliente, canal, mp_canal, mp_cuenta, mp_orden,
     descripcion, guia, paqueteria, fecha_venta, entrega_limite, moneda, total,
     comision, precio_origen` — y nada más (el alta, la confirmación, `tipo`,
     `full_tienda`, `envio_ref` siguen fijos);
   - un renglón que **todavía no sale** se actualiza, se borra o se inserta.

2. **`ventas.ov_mensajes.evento`** gana el valor **`editada`**.

## Qué NO cambia

- **Sin la puerta todo sigue igual de cerrado.** Confirmar, entregar, cancelar y
  las sentencias del canal no la abren: si alguna intentara tocar el contenido,
  recibe el mismo `42501 ov_ordenes_inmutable` / `ov_lineas_inmutable` de antes.
  La puerta es **por orden**: abrirla para la 12 no deja tocar la 13.
- **Un renglón que ya salió no se edita ni se borra**, con o sin puerta.
- **Entregada, cancelada y entregada_cancelada siguen inmutables.**
- **Los diferidos no se tocaron**: `ov_coherente` (una confirmada tiene al menos
  un renglón por entregar y todos apartados completos), `stock_apartado_cuadra`
  y `stock_libro_cuadra`, ni la guardia del saldo. Una edición que deje el
  apartado descuadrado truena al COMMIT igual que antes.
- **Apartar sigue siendo todo o nada.**

## Cómo lo usa el código

Un solo escritor abre la puerta: `SQL_EDITAR_CONFIRMADA` en
`backend/services/ordenes_venta.py` (la llama `guardar()` cuando la orden está
confirmada). Es **una sentencia**, con el orden de candados de tu guía:

1. la fila de la orden, con CAS de `rev` (y `estado = 'confirmada'`, sin borrar,
   sin marca del canal, `tipo <> 'full'`);
2. `almacen.almacenes` `FOR SHARE` (bodegas viejas y nuevas);
3. `almacen.stock_almacen` `FOR UPDATE` en orden `(sku, almacen)`, sólo las
   filas cuyo apartado cambia;
4. mueve el apartado por la **diferencia** de cada (SKU, bodega): suelta lo que
   baja y aparta lo que sube, esto último sólo si `libre >= diferencia`;
5. borra, actualiza e inserta los renglones por entregar (con `reservado =
   cantidad`);
6. escribe el mensaje `editada` (quién, y el antes y después de cada campo y
   renglón);
7. `ops.exigir`: si un solo (SKU, bodega) no alcanza → `KB001 no_alcanzo` y
   **nada** cambia, ni `rev`.

`set local` muere con la transacción: no queda nada pegado en la conexión del
pooler (regla 13).

**El rastro**, que era tu objeción a editar una confirmada («cambiaría un renglón
confirmado sin rastro»): cada edición deja en `ventas.ov_mensajes` (sólo
agregar) un mensaje `editada` escrito por la misma sentencia, con `datos.cambios`
(campo: antes → después), `datos.renglones` (agregados, quitados, cambiados) y
`datos.apartado` (cuánto se movió por SKU y bodega).

## Lo que conviene que revises

- **Si la puerta te parece bien como mecanismo.** Es una variable de transacción,
  no un permiso: cualquier código que escriba `set local app.ov_edicion` la abre.
  La alternativa sería una función `security definer` que haga la edición; no la
  hice para no meter lógica de negocio en la base sin ti.
- **La lista de columnas editables** (arriba). `mp_*` está incluida: corregir la
  liga con la venta de una confirmada es posible. Si prefieres que no, es quitar
  tres nombres del arreglo `editables`.
- **Las sentencias del canal ya no van «sólo por estado»** (tu guía, §4.2 y
  §4.9). Con el contenido de una confirmada pudiendo cambiar, una cancelación del
  canal armada con la lectura de antes de una edición soltaba un apartado que ya
  no era el leído (la base lo deshacía, pero salía como error). Ahora
  `SQL_CANCELAR_CANAL`, `SQL_CANCELAR_CON_SALIDA_CANAL` y `SQL_CANAL_MARCA` exigen
  también la `rev` **de su propia lectura** y, si cambió, releen y reintentan. No
  es la `rev` de un usuario: el proceso sigue sin traer una. Y el barrido pasa la
  venta que leyó, para no cancelar una orden que alguien acaba de religar a otra.
- **Cancelar una confirmada ya no es sólo de admin** (decisión de Brandon). Eso
  es del código, no de la base: la base nunca distinguió roles.
- **El sandbox**: aplicar 0068 y 0071.
- **Tu guía y el verificador** siguen diciendo que fuera de borrador el contenido
  es inmutable. Ya no es exacto para `confirmada`. No los edité.

## Ojo con el orden de las migraciones

Después de la 0071, **la 0068 no se vuelve a correr sola**: recrea las dos
guardias con su texto de entonces, sin la puerta, y lo hace sin error. La
edición quedaría rechazada (`42501`) hasta volver a correr la 0071, que sí es
idempotente. El código no se fía del registro `ops.migraciones` para saber si
puede editar: pregunta si las dos guardias traen la puerta y si el catálogo
conoce `editada`; si no, la orden no ofrece editar y dice por qué.

## Reversa

Volver a crear las dos funciones con el texto de la 0068 y quitar `editada` del
CHECK del catálogo. Lo segundo falla si ya hay mensajes `editada`: primero hay
que decidir qué se hace con ese historial. El código de la versión que trae la
edición contesta entonces «falta la migración 0071» y no ofrece editar.

## Aparte: órdenes de prueba en producción (temporal)

Por instrucción de Brandon, tras aplicar la 0071 se sembraron en producción
órdenes de venta de PRUEBA (cliente «PRUEBA», creadas por Claude) y saldo
ficticio en la bodega **ENSAYO**, para revisar los estados y la edición. Brandon
indicó que al terminar se eliminan y los valores vuelven a su estado inicial.
Eso exige apagar las guardias de sólo-agregar dentro de una transacción de
limpieza; el detalle queda anotado en la tarea de ClickUp del módulo.
