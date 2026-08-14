He verificado los puntos que sostienen el diseño antes de opinar. Confirmado en código:

- `pedidos_ml.py:389` `_ya_compensado` lee MySQL con `except → False`: es exactamente la familia de los 964.
- `stock_full.py:353` parsea la marca de agua de FBA desde el **texto** de `resultado` con regex `→\s*(\d+)`, y siembra de kubera (`channel_read.stock_fba_amazon`) solo para SKUs nunca vistos.
- `fanout_stock._publicaciones_para_stock` ya lee `channel.listings` (kubera), **no** MySQL. El fan-out está aguas abajo de `channel.listings`, no de la tabla MySQL directa.
- Los hits de `ml_progress` en `amazon.py`, `db.py`, `competencia_ml.py` son docstrings, no lectores nuevos. Tu lista de lectores aguanta. Y `competencia_ml.py:146` dice explícito: *"`ml_progress` no conoce las publicaciones creadas fuera del pipeline"* — dato que refuerza el grupo 4.

Con eso, la revisión.

---

# Revisión crítica de los 7 grupos

## 1. Grupo 4: NO hagas la gemela 1:1. Parte los lectores en dos destinos.

Tu propia medición ya cierra el debate, más fuerte de lo que la lees:

- `channel.listings` es superset de los **éxitos** de `ml_progress` (0 en progress y no en listings).
- 63 pares tienen `item_id` distinto y `listings` es el fresco.
- El código lo confirma: `meli.py` y `competencia_ml.py` ya tratan la **API de ML** (no `ml_progress`) como autoritativa de "qué está publicado".

Conclusión dura: **una gemela 1:1 no perpetúa "un segundo registro de la misma verdad" — perpetúa un registro que ya sabes que está EQUIVOCADO en 63 casos.** Migrar `ml_progress` tal cual sería copiar deuda con checksum. Y como los 63 divergentes prueban que *hoy* hay lectores decidiendo con un id viejo, la gemela no arregla nada; solo mueve el bug de motor.

El corte correcto es el que propones en la pregunta, y hay que hacerlo:

| Pregunta del lector | Destino |
|---|---|
| ¿está publicado? ¿con qué `item_id` vivo? ¿en qué cuenta? | `channel.listings` |
| ¿cómo salió el intento? (`success/error/gtin_error/dry_run/published_at/wc_id`, y los **269 fallidos**) | bitácora tipo `ops.channel_submissions` |

**El matiz que tu brief no dice y es crítico: `channel.listings` es superset SOLO de los éxitos.** Los 269 intentos fallidos y los SKUs que nunca publicaron **no existen** en `listings`. Cualquier lector que hoy pregunta "¿este SKU falló con `gtin_error`?" o "¿ya lo intenté?" NO puede repuntarse a `listings` — va a la bitácora. Si mapeas todo a `listings` a ciegas, esos lectores empiezan a ver "no publicado / sin intento" para SKUs que fallaron, y alguien los re-encola o los cuenta mal. Clasifica los 19 lectores uno por uno con esa pregunta explícita, no por archivo.

Riesgo de la opción-split que sí debes vigilar: son **dos** repuntes coordinados en vez de uno, sobre 19 sitios. Mitígalo haciéndolo lector-por-lector con su flag, no big-bang.

## 2. El orden: tu contra-argumento es correcto. No dejes el 4 al final.

Ordenar por costo ("el 5 es barato, va primero") optimiza la métrica equivocada. El 5 es barato **y** de bajo blast radius, pero hacerlo primero **no construye nada que desriesgue el 4**. Y el 4 es el que produce incidentes de la familia 964.

Ordena por **riesgo × dependencia**, no por costo:

1. **Grupo 5 primero, pero como ENSAYO deliberado**: es el caso limpio (1R+1W, tab-only). Úsalo para **escribir el runbook del barrido de lectores** (flag por tabla, medición de paridad previa, corte, verificación con un vigilante). Barato = bajo costo de equivocarte mientras codificas el método.
2. **Grupo 4 inmediatamente después**, con el método fresco — exactamente tu argumento. Dejarlo al final es garantizar que se haga cuando nadie recuerde el procedimiento, que es cuando vuelven los 964.
3. El resto detrás.

O sea: tu instinto de "atacar el 4 temprano" gana, pero no como *primero absoluto* sino como *segundo, tras un ensayo barato que produce el runbook*.

## 3. Lo que no estás viendo

**(a) `stock_watch_foto` está en el grupo equivocado.** Tú mismo escribes "NO es caché: es fuente", y aún así vive en "Grupo 2 — CACHÉS". Su cadena `stock_watch_foto → channel_mirror:298 → channel.listings canal general → fanout_stock (decide a qué publicaciones empuja stock)` es un decider vivo, idéntico en forma al grupo 4. **Sácala del grupo 2 y trátala con el barrido de lectores del 4.** El resto del grupo 2 (`ventas_horarias/sync`, `amazon_imagenes`, `ml_image_edit_backlog`) sí son cachés/escritura-pura y pueden ir juntas. Un grupo que mezcla una fuente load-bearing con cachés es una trampa de etiqueta.

**(b) `fanout_log` se AUTO-CREA — "archivar = drop" no lo apaga.** `fanout_stock.py:506` tiene `CREATE TABLE IF NOT EXISTS fanout_log`, y `stock_watch.py` + 4 scripts manuales le hacen INSERT. Si migras la lógica del candado a kubera pero dejas el camino de escritura apuntando a MySQL, **el próximo INSERT recrea la tabla vacía**, y `_ya_compensado` lee una tabla vacía → "no compensado" → **compensación doble → stock fantasma**. Es el 964 servido en frío. El drop de `fanout_log` tiene que ir *después* de repuntar TODOS sus escritores, incluidos los 4 scripts manuales, y de quitar el `CREATE TABLE IF NOT EXISTS`.

**(c) Los escritores manuales acoplan tablas.** `fanout_log` lo escriben `alinear_ml_drop`, `alinear_amazon_drop`, `corregir_stock_amazon`, `sincronizar_ml_huerfanas`. Tu barrido fue por `services/` + `routers/`; estos son `scripts/`. Un candado de idempotencia cuyo dataset lo puebla en parte un script que alguien corre a mano es frágil por diseño: repuntar el lector sin repuntar esos escritores parte el candado.

**(d) Superficie externa NO verificable desde este repo.** Grep aquí no ve `MLRegisterDaily`, `publicador`, ni `MonitoreoOperaciones`. CLAUDE.md ya dice que `MonitoreoOperaciones` lee `productos` de MySQL (toca `productos.stock_odoo`, tu grupo 7). **Ningún lector externo de `ml_progress`/`amazon_progress`/`fanout_log` aparece en tu barrido.** Antes de archivar cualquiera de esas, hay que grepear los repos externos por nombre de tabla — hoy es un punto ciego declarado, no una ausencia comprobada.

## 4. `fanout_log`: el candado NO debe vivir en la bitácora. Falta estado propio.

Opinión fuerte: sí, falta una tabla de estado. Un candado apoyado en (a) una bitácora append-only y (b) un regex sobre texto libre (`→ (\d+)`) es dos formas del mismo antipatrón — *"la marca de ya-lo-hice no la pone el hacerlo, la infiere leyendo prosa"*.

Dos concerns distintos, dos casas distintas:

- **Anti-doble-compensación** (`_ya_compensado`): es estado **por pedido**. Va como columna en `channel.orders` (p. ej. `stock_compensado_at timestamptz`). `channel.orders` ya es el registro de estado por orden y ya carga protecciones de este tipo. Un booleano/timestamp ahí es atómico, no se rompe si rotas el log, y no depende de que exista una fila de texto con la palabra exacta.
- **Marca de agua FBA** (`stock_full.py:353`): es estado **por SKU** ("última cantidad FBA vista"). Hoy ya tienes el fallback correcto: `channel_read.stock_fba_amazon()` de kubera. **Elimina la rama del regex y usa kubera como única fuente de la marca.** El regex existe solo por historia (evitar re-descuento cuando `canal_inventario` se refrescaba); con kubera fresca, parsear prosa es riesgo puro: un cambio de wording del log y la marca desaparece silenciosamente.

La bitácora se queda como bitácora (auditoría/panel), sin que nadie DECIDA leyéndola.

## 5. Archivar vs migrar: de acuerdo, con dos afinados.

- El criterio no es "sin lectores vivos" sino **"sin lector vivo que DECIDA"** — y la carga de la prueba es demostrar la *ausencia*, que grep no puede cerrar (externos, SQL dinámico, tablas auto-creadas). Trata cada "cero lectores" como hipótesis a verificar con un vigilante que grite si alguien la toca, no como hecho.
- **Los backlogs (246 MB, cero lectores): archivar y purgar, sí.** Postgres es para consultar, no para almacén frío; ya tienes dumps con SHA256. Cero valor forense en llevar payloads crudos a kubera.
- **Pero los 269 fallidos de `ml_progress` NO son "backlog".** Son la razón operativa de por qué ciertos SKUs no están publicados (`gtin_error`, etc.). Esos van a la bitácora `ops.channel_submissions`, no al archivo. No los tires con los payloads.

## 6. Riesgos de secuencia

- **`stock_watch_foto` es la dependencia que rompe el orden por costo** (ver 3a). Va antes de que toques nada que dependa de `channel.listings` canal general, y con barrido de lectores completo — el propio fan-out (`fanout_stock`) decide con eso.
- **`fanout_log` acopla grupo 3 con grupo 2 y con scripts**: `stock_watch` (grupo 2) escribe en `fanout_log` (grupo 3). No puedes cerrar el grupo 3 mientras un miembro del grupo 2 le siga escribiendo. Los grupos no son independientes; el orden real es un DAG, no una lista.
- **Grupo 6 y `MLRegisterDaily`**: apagar el renovador y migrar a Vault es el mismo movimiento — de acuerdo — pero eso lo pone en el **camino crítico de que los pedidos sigan entrando** (regla 8 de la casa: sin tokens, `meli.obtener_orden` no se auto-sana). No es "paralelo, depende de terceros" y ya: es "no lo apagues hasta que Vault esté sirviendo tokens verificados con `/users/me`". Trátalo con más miedo que el resto.

## Resumen accionable

1. **Grupo 4**: split en dos destinos (`channel.listings` para identidad/publicado, `ops.channel_submissions` para intentos+fallos), lector por lector con flag. **No gemela 1:1.**
2. **Reordena**: Grupo 5 como ensayo que produce el runbook → Grupo 4 con el método fresco. No dejes el 4 al final.
3. **Saca `stock_watch_foto` del grupo 2** y trátala como fuente (barrido de lectores).
4. **`fanout_log`**: mueve el candado a `channel.orders` (estado por pedido) y la marca FBA a kubera (mata el regex). Antes de dropear: repunta los 4 scripts escritores y quita el `CREATE TABLE IF NOT EXISTS`, o resucita vacía y compensa doble.
5. **Verifica la superficie externa** (`MLRegisterDaily`, `publicador`, `MonitoreoOperaciones`) por nombre de tabla antes de archivar cualquier cosa — hoy es punto ciego, no ausencia probada.
6. **Guarda los 269 fallidos** en la bitácora; purga los payloads de backlog.

El brief está bien hecho y es honesto sobre sus límites. Los dos agujeros que más me quitarían el sueño son la **auto-recreación de `fanout_log`** (3b) y **`stock_watch_foto` mal clasificada** (3a): los dos son la familia 964 esperando el momento.
