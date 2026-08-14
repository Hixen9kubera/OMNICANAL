Read everything: the plan doc, `PLAN_31_TABLAS.md`, and the three code sites plus their neighbors (`sincronizar` ordering, `stock_watch`, the scripts that write `fanout_log`). Verdict first, then the six questions, then two findings the doc doesn't mention.

---

# Veredicto

El planteamiento es honesto y está bien medido. El split en tres casas es correcto y la medición del punto 6 se sostiene. **Pero el cambio estrella —"propagar en vez de `return False`"— tal como está descrito puede CREAR pedidos duplicados**, que es exactamente el desastre que dice prevenir. No por el candado en sí, sino por *dónde* vive en la secuencia de `sincronizar`. Eso es lo que hay que arreglar antes de tocar nada.

---

# 2. Propagar el candado 1 — el punto más peligroso del plan

Rastreé la secuencia real de `_sincronizar_serializado`. El orden de operaciones es:

1. `wc_order_id_previo` (lee `channel.orders`) — `pedidos_ml.py:436`
2. Crea el pedido en Woo — `pedidos_ml.py:477`
3. **Compensación, con `_ya_compensado(wc_id)` en la condición** — `pedidos_ml.py:510`
4. Escribe el registro en `channel.orders` vía `orders_write.guardar` — `pedidos_ml.py:583`

La llamada a `_ya_compensado` en la línea 510 **no está dentro de ningún `try`**. Si le pones `propagar` y la base parpadea, la excepción sube por `_sincronizar_serializado` → `sincronizar` → el handler del webhook. El pedido **ya se creó en Woo** (paso 2), pero la función revienta **antes** de escribir `channel.orders` (paso 4). ML reintenta el webhook (reintenta ante cualquier no-2xx), el reintento corre el paso 1, **no encuentra previo** (porque nunca se escribió), y **crea un segundo pedido en Woo**.

**Propagar ingenuamente el candado 1 reproduce el patrón de los 964.** La pregunta que hace el brief —"¿hay que distinguir entre propagar y abortar solo la compensación?"— la respuesta es **sí, rotundo**, y la razón es esta secuencia.

El diseño seguro no es "propagar" a secas:

- **El ancla de idempotencia es el registro en `channel.orders`, no el flag de compensación.** Ese registro tiene que persistirse **antes** de la compensación. Muévelo: paso 2 (crear Woo) → paso 4 (escribir `channel.orders`) → *después* la compensación. Así el reintento es idempotente a nivel pedido.
- Recién entonces la compensación puede propagar un error **reintentable**: el reintento encuentra el pedido, no lo duplica, y sólo reintenta la compensación.

## Y un agujero que ya existe hoy, sin tocar nada

Ese mismo desorden es una fuente de duplicados **latente hoy**. La escritura a `channel.orders` (líneas 581–595) está envuelta en `except Exception: log.warning` (línea 596). Si esa escritura falla, el pedido queda en Woo pero **no** en el registro; el lock se libera; el siguiente aviso de la ráfaga (o un reintento de ML) no encuentra previo y **crea el duplicado**. El lock sólo protege la concurrencia de la misma orden, no un reintento secuencial tras un registro fallido y tragado.

**El brief enfoca el `except` de `_ya_compensado` y se pierde que el mecanismo real de anti-duplicado es el orden de operaciones + la durabilidad del registro.** Ese `except: warning` de la línea 596 es más peligroso que el de la 396.

---

# 1. El split en tres casas — correcto, con dos matices

De acuerdo con las tres casas. El barrido de lecturas-que-deciden está **completo**: confirmé por grep que los únicos `SELECT ... FROM fanout_log` que deciden son `pedidos_ml.py:393`, `stock_full.py:137` y el watermark de `stock_full.py:372-374`. Todo lo demás (`fanout.py:47/113`, `fanout_stock.py:760+`) es dashboard.

Dos correcciones al diseño:

- **La compensación no es un booleano, es una máquina de estados chica.** `_ya_compensado` sólo mira `accion='full_compensado'`. Pero la compensación escribe tres valores: `full_compensado`, `full_compensado_error`, `full_compensado_revertido` (`pedidos_ml.py:378-379`). Consecuencias que una columna booleana `_compensated` heredaría mal:
  - Una compensación **parcial** (unas líneas ok, otras error) cuenta como "hecha" → las líneas fallidas **nunca reintentan**.
  - Tras una reversión (`full_compensado_revertido`), la fila `full_compensado` original sigue ahí → `_ya_compensado` sigue diciendo `True`. Si ya estás moviendo el estado a `channel.orders`, define `compensated_at` / `reverted_at` (o un enum), no un `bool`. Un `bool` copia el bug actual con checksum.
- **`channel.orders` no necesita índice nuevo por `wc_order_id`.** El doc dice que agregará uno porque la compensación busca por `wc_order_id`. Pero `sincronizar` tiene `order_id` y `cuenta` en mano → puedes buscar por la PK `(canal, cuenta, external_order_id)` y evitarte el índice. Menos superficie.

Las otras dos casas (`ops.fulfillment_operations`, `ops.fba_watermark`) bien ubicadas.

---

# 3. Estado que se te escapó — el watermark está envenenable

El inventario de **lecturas** está completo. Pero hay dos cosas en el estado del watermark que el brief no dice y que romperían la migración:

- **La consulta del watermark NO excluye ERRORES ni simulaciones.** `_ya_procesada` filtra `resultado NOT LIKE 'ERROR%'` (`stock_full.py:139`), pero el watermark (`stock_full.py:372-374`) toma `MAX(id)` de **cualquier** fila `accion LIKE 'fba_%'` —incluye `fba_ingreso_sim` y cualquier fila de error cuyo `resultado` contenga un `→ N`— y parsea el número. O sea: el watermark se define por la **última observación**, aplicada o no. Eso **está bien** funcionalmente (es "lo que vi la última vez"), pero tiene dos implicaciones para la migración que hay que escribir:
  1. **La columna `fba_watermark` se actualiza al OBSERVAR, no al APLICAR.** Si la atas a "operación aplicada" (como sí debe ser `fulfillment_operations`), en modo `solo_registro` nunca avanza, y el día que quiten el "solo registro" el vigilante re-ve todo como nuevo → **alerta/doble-ingreso fantasma**. Es la familia 964 otra vez, en el watermark, y el doc no lo marca.
  2. **La copia de las 99 marcas** debe traer la última fila `fba_%` por SKU **sea sim, aplicada o error-con-flecha**, no la última "aplicada". Si la copia sólo trae `fba_ingreso`, los SKUs cuya última observación fue un `fba_ingreso_sim` re-semillan desde `channel.listings` → fantasma. El doc dice "su valor exacto"; hay que decir *cuál* es el exacto.

- Confirmado que los scripts manuales (`corregir_stock_amazon.py`) escriben con `accion='escribir'`, **no** `fba_*`, así que **no** envenenan el watermark ni `_ya_procesada`. Ese lado está limpio.

---

# 4. La doble lectura de días — es ceremonia cara para poco

Con 23 filas casi estáticas, **días de doble-lectura con cero discrepancias prueban poco**: son filas que casi no cambian, el log de discrepancia casi nunca dispara, y "se ve limpio" ≠ "está limpio" no aplica igual aquí que en el publicador (donde el riesgo era divergencia acumulada de 724 filas).

El riesgo real por candado es distinto y no lo cubre la doble-*lectura*:

- **Compensación**: el peligro es el camino de **ESCRITURA** —¿cada compensación nueva sí escribe la columna en `channel.orders`?—. Eso se verifica con **doble-escritura + comparación**, no con doble-lectura. Ahí sí ponme el arnés.
- **`_ya_procesada` y watermark**: hoy en `solo_registro`, riesgo casi nulo. Basta la copia + comparación exhaustiva **one-shot** de los 23 + 99, más tu matriz T1-T6.

Recomendación: **cambia "días de doble-lectura" por "doble-escritura en el candado de compensación + comparación exhaustiva one-shot de los 23+99 + la matriz de tests"**. Es más barato y ataca el riesgo que de verdad existe (el write-path). Los tests T2 y T4 son los que valen; consérvalos tal cual.

---

# 5. El orden del `CREATE TABLE IF NOT EXISTS` — va antes de lo que crees, no al final

De acuerdo en que el DROP va al final. Pero **quitar el `CREATE TABLE IF NOT EXISTS` es lo que convierte una resurrección silenciosa en un fallo ruidoso** — y con `propagar` en su lugar, un fallo ruidoso es **seguro** (aborta, no actúa doble). Así que no lo dejes "al final por default": quítalo **en cuanto los tres lectores lean de kubera y los 8 escritores estén repuntados**. Su única razón de existir después de eso es tapar un DROP accidental — que es precisamente el booby-trap.

Orden que yo defiendo:

1. Reordenar `sincronizar` (registro antes de compensar) + propagar — *esto primero, sin él nada es seguro*.
2. Migración `0022` + copia de estado.
3. Lecturas a kubera detrás de flag + verificación.
4. Repuntar los 8 escritores.
5. **Quitar el `CREATE TABLE IF NOT EXISTS`** (ya nadie lo necesita legítimamente; ahora un DROP falla ruidoso en vez de resucitar mentiroso).
6. DROP.

Ojo: `_asegurar_schema` lo llaman **cinco** sitios (`pedidos_ml._compensar`, `stock_full._ya_procesada` y `_registrar`, `stock_watch._anotar`, `fanout_stock._persistir`) más los scripts. Mientras cualquiera de esos escritores siga vivo, quitar el guard los hace escribir en una tabla zombi o fallar. Por eso el guard se quita **después** de repuntar escritores, no antes — pero **antes** del DROP, nunca junto.

Esto es en realidad territorio de **PASO 5**, no PASO 0. El PASO 0 termina cuando los tres candados leen kubera y propagan (pasos 1-3 de arriba). No metas el repunte de 8 escritores + DROP dentro del PASO 0; son orthogonales al riesgo de lectura y sólo agregan superficie a un paso que ya es delicado.

---

# 6. Lo medido — no puedo contradecirlo, pero tu evidencia es la equivocada

El código **corrobora** tu medición. `stock_full.py:386-391` ya usa `channel_read.stock_fba_amazon()` **sólo como semilla** (`setdefault`) para SKUs nunca vistos; la referencia primaria es el watermark. El comentario de `stock_full.py:364-368` explica exactamente por qué: el sync refresca cada 15 min → el mismo ingreso se contaba dos veces. **Reemplazar el regex por el valor del sync reintroduce el bug. Confirmado, no contradicho.**

Pero tu **argumento** es flojo, y alguien crítico lo tumbaría:

- **Los 96/99 distintos son casi una tautología.** Un "último valor que vi al procesar este SKU" contra un "snapshot global de ≤15 min" **por construcción** casi siempre difieren. Esa tabla no prueba el bug: prueba que dos relojes distintos marcan horas distintas.
- **Lo que prueba el bug es el MECANISMO**, no la divergencia: el sync **sobreescribe la referencia entre pasadas**. Cita el comentario del código y la mecánica, no la tabla de 3 SKUs.

Un revisor te diría: *"entonces snapshotea el valor del sync en el momento del evento"* — y la respuesta es: **eso ES el watermark**. Es la memoria propia de un vigilante, no derivable de ninguna tabla compartida. Tu conclusión (columna propia) es correcta; cámbiale la evidencia de "96/99 difieren" a "el sync pisa la referencia entre pasadas — el mismo defecto que `_foto()` en `stock_watch`". Es la misma familia de bug que ya arreglaste en PASO 2, y decirlo así lo hace inatacable.

---

# Resumen accionable

1. **Bloqueante**: reordena `sincronizar` para que `channel.orders` se persista **antes** de la compensación; sólo entonces `_ya_compensado` puede propagar sin crear duplicados. Y arregla el `except: warning` de la línea 596 — es un generador de duplicados latente hoy.
2. La casa de la compensación necesita estado tri-valor (compensado/revertido/parcial), no un `bool`.
3. Escribe explícitamente que `fba_watermark` se actualiza **al observar, no al aplicar**, y que la copia de las 99 trae la última fila `fba_%` sea sim/aplicada.
4. Cambia "días de doble-lectura" por doble-**escritura** en compensación + compare one-shot + T1-T6.
5. Quita el `CREATE TABLE` tras repuntar escritores y antes del DROP; eso es PASO 5, no PASO 0.
6. Tu medición del watermark es correcta; reemplaza la evidencia 96/99 por el argumento del mecanismo.
