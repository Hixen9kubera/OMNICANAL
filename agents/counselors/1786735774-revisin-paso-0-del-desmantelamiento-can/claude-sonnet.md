# Revisión: PASO_0_CANDADOS.md

Leí el documento, `PLAN_31_TABLAS.md`, la migración `0022_candados_fanout.sql`, y el código completo de `pedidos_ml.py`, `stock_full.py`, `fanout_stock.py`, `stock_watch.py`, `orders_write.py`, `candados_read.py` y `migrar_candados_paso0.py`. Descubrí que **el "planteamiento" ya tiene código escrito** (la migración, la gemela `candados_read.py`, el script de migración), aunque ninguno esté aplicado ni enganchado. Eso no invalida el documento, pero la frase "todavía no se ha tocado una línea de código" es imprecisa — hay que corregirla o el próximo lector va a creer que parte desde cero.

## 2. "Propagar" — el hallazgo central, y el documento tiene razón en dudar

**No basta con quitar el `except → return False`. La posición de la llamada dentro de `_sincronizar_serializado` determina si propagar es seguro o si reintroduce el bug de los 964 fantasma por una puerta distinta.**

`_ya_compensado` se llama DOS veces en `pedidos_ml.py`:

- **Línea 469** — dentro del `try` de 452-484. Si truena, cae en el `except` de la línea 484 y `sincronizar()` devuelve `{"ok": False, ...}`. El caller (`webhooks.py:272-278`) ya maneja ese `False` con gracia: anota el motivo y responde 200 a ML. **Esta es segura.**

- **Línea 510** — `if protegido and payload["status"] != "cancelled" and not _ya_compensado(wc_id):` — **fuera de cualquier `try`**, después de que el pedido YA se creó en WooCommerce (`POST /orders`, línea 477) y **antes** del bloque que registra el pedido en `pedidos_ml`/`channel.orders` (líneas 519-597, con su propio `try/except` en 596).

Si esta segunda llamada propaga, la excepción sube sin que nada la atrape hasta `webhooks.py:281` (`except Exception as exc: resultado = f"error: {exc}"`), que sí la traga — pero para entonces **el bloque de registro en `channel.orders` (519-597) nunca corrió**. El resultado: existe un pedido en WooCommerce sin fila en `channel.orders`. En la siguiente ráfaga del mismo webhook (y ML manda ráfagas — regla 6 de CLAUDE.md), `orders_write.wc_order_id_previo` no encuentra la orden, la trata como nueva, y se crea un **segundo** pedido de Woo para la misma venta.

Es el mecanismo de los 964 fantasma, recreado por el propio arreglo, con un disparador más amplio que "kubera caída": basta un timeout puntual de ESA consulta.

**La prueba de que el proyecto ya sabe hacerlo bien, dos líneas más arriba en el mismo archivo:** `orders_write.wc_order_id_previo` (llamada en la línea 436, ANTES de crear nada en Woo) tiene exactamente esta política, documentada en `orders_write.py:72-74`:

> *"Un None equivocado CREA un pedido duplicado, así que el error se propaga en vez de asumir 'nueva': que el alta falle y se reintente es reparable; un fantasma en Woo, no."*

Y `stock_watch._foto()` (paso 2 ya migrado) hace lo mismo con una envoltura EXPLÍCITA en el call site (`stock_watch.py:274-284`): atrapa ahí mismo, aborta la pasada limpia, y **antes de que se haya escrito nada**. Ese es el patrón a copiar: **propagar significa que la EXCEPCIÓN aborta ese paso puntual con un log claro — no que se deje correr sin capturar hasta un handler genérico que está años luz del lugar donde se rompió el estado.**

`candados_read.py` ya lo dice correctamente en su docstring ("quien llame a este módulo decide qué hacer con la excepción") — pero eso deja la decisión pendiente, y el sitio exacto donde hay que decidir (línea 510 de `pedidos_ml.py`) es justo el peligroso. **Recomendación concreta: mover la comprobación de `_ya_compensado` (línea 510) a ANTES del alta en Woo — o envolverla en un `try` local que, si truena, aborte devolviendo `{"ok": False}` en vez de dejarla desnuda tras la creación del pedido.** No hace falta distinguir "propagar" de "abortar solo la compensación": hay que envolver de forma que abortar ahí NO se lleve entre las patas el registro que ya es idempotente y seguro.

`stock_full._ya_procesada` (línea 221 de `stock_full.py`) es distinta: se llama al INICIO de `procesar_operacion`, antes de cualquier escritura en Woo. Propagar ahí es seguro sin cambios adicionales — el `except Exception` de `webhooks.py:281` la atrapa después de que nada se ha tocado.

## 1. El split en tres casas

Correcto, y verificado contra el uso real:

- `stock_compensado_at` como columna en `channel.orders`: el candado busca por `wc_order_id`, que la migración indexa (parcial, `where wc_order_id is not null` — bien, evita indexar los pedidos que no vienen de estos tres canales). Timestamp en vez de boolean es la decisión correcta — ya la usan en otros lados del proyecto (`aplicada_at`, `visto_at`) precisamente para poder reconciliar contra la fuente vieja.
- `ops.fulfillment_operations` con PK `operacion_id`: no hay pedido al que pegarse, tabla propia es lo único razonable.
- `ops.fba_watermark`: hermana correcta de `ops.stock_watch_photo` (misma naturaleza — memoria de un vigilante, no inventario publicable). Buen paralelo con el razonamiento que ya usó `0021` para justificar por qué esa foto vive en `ops` y no en `channel`.

No hay una cuarta casa mejor. Único matiz: al insertar la política de "solo se inserta cuando se aplicó de verdad" en `ops.fulfillment_operations`, la migración pierde la distinción entre "nunca se intentó" y "se intentó y falló (no-ERROR)" — pero eso ya lo reconoce el propio comentario de la migración (línea 71: "si falló, no hay fila, y ML puede volver a avisar"), y es el comportamiento correcto para este caso de uso.

## 3. ¿Se escapó algún estado?

Rastreé las 16 referencias a `fanout_log` en el repo. Aparte de las tres decisiones ya identificadas, todo lo demás es lectura de PANTALLA (routers/fanout.py: `/full/observacion`, `/inventario/pendientes` — ambas de solo lectura para el dashboard, no deciden nada) o escritura pura (los 8 escritores ya contados: `pedidos_ml.py`, `stock_full.py`, `stock_watch.py`, `fanout_stock.py` + 4 scripts manuales). No encontré una cuarta consulta que decida algo. `stock_watch.py` en particular NO lee `fanout_log` para su propia lógica — su estado vive aparte en `stock_watch_foto`, y solo escribe en `fanout_log` para la bitácora. El barrido del documento está completo.

## 4. La fase de doble lectura, con 23 filas

Con 23 filas de estado tiene sentido, pero por una razón distinta a la que suele justificar doble lectura (volumen/tráfico): aquí el valor es que **el candado 2 tiene una regla no trivial ya migrada** (excluir `resultado LIKE 'ERROR%'`) y la única forma barata de confirmar que la migración replicó esa regla bien es comparar 1:1, no confiar en el conteo. `migrar_candados_paso0.py` ya hace gran parte de esa verificación en el propio script (líneas 172-218: compara conteos, fechas, y explícitamente confirma que las marcas de agua NO coinciden con `channel.listings` — la aserción invertida es un buen detalle). Con eso, la doble lectura EN VIVO (candado consultando los dos lados en producción) agrega poco sobre lo que el script de migración ya prueba estáticamente. Yo la recortaría a horas, no días — la señal que importa (¿la regla ERROR-filter se replicó bien?) ya se puede verificar sin tráfico real, y `full_watch_solo_registro=True` significa que candado 2 no tiene urgencia productiva. Mantendría los días de doble lectura solo para candado 1, que sí corre en el flujo real de pedidos hoy.

## 5. El orden

De acuerdo en que quitar el `CREATE TABLE IF NOT EXISTS` va al final. Invertirlo sería repetir el error que el propio documento identifica: mientras el candado 2 recree la tabla vacía y le pregunte, cualquier corte prematuro de `fanout_log` hace que TODO conteste "no lo hice". El orden propuesto (migrar → gemelas apagadas → doble lectura → encender lectura → recién ahí quitar el `CREATE TABLE` y repuntar los 8 escritores) es el único que no dispara ese modo "vacío = sí a todo" a medio camino.

Un matiz que falta explicitar: el paso "encender la lectura" (5) necesita ocurrir **candado por candado**, no junto. Dado el hallazgo de la sección 2, encender la lectura del candado 1 requiere primero mover su call site (línea 510) fuera de la zona sin `try`. Encender candado 2 no tiene ese requisito. Si el plan los trata como un solo interruptor (`SUPABASE_READ_CANDADOS`), hay que separar el flag en dos, o el candado 1 se enciende con el defecto estructural todavía sin arreglar.

## 6. La corrección sobre la marca de agua — confirmada, no contradicha

Verifiqué el código: `stock_full.revisar_fba()` usa `channel_read.stock_fba_amazon()` únicamente como **semilla** para SKUs nunca vistos (`previos.setdefault(sku, fba)`, línea 388 de `stock_full.py`) — nunca sobreescribe una marca existente. Eso es exactamente la semántica que el documento describe ("cuánto vi la última vez que procesé un evento" vs. "cuánto vio el sync hace ≤15 min"), y el comentario en el propio código (líneas 364-368) ya explica el bug de doble conteo que motivó separar las dos fuentes. La medición de 96/99 distintos es consistente con ese diseño — si coincidieran, sería señal de que algo está mal, no de que el plan viejo tenía razón. No encuentro forma de defender el plan original aquí.

## Resumen accionable

1. **Antes de tocar código real**: mover o envolver la llamada a `_ya_compensado` en `pedidos_ml.py:510` para que un fallo aborte ANTES de saltarse el registro en `channel.orders` — no después de crear el pedido en Woo. Esto es el gap real entre "propagar" y "seguro", y el documento ya intuía la pregunta correcta.
2. Corregir la frase "no se ha tocado una línea de código": ya existen `0022_candados_fanout.sql`, `services/candados_read.py` y `scripts/migrar_candados_paso0.py`, sin aplicar ni enganchar.
3. Separar el flag de lectura en dos (uno por candado), dado que sus requisitos de seguridad para "encender" son distintos.
4. El resto del planteamiento — split en tres casas, orden de los pasos, corrección de la marca de agua — se sostiene contra el código tal como está hoy.
