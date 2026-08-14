# Las 31 tablas que quedan en MySQL — plan y dictamen del consejo

> Levantado el **14-ago-2026**, después de cerrar los cortes de los 5 dominios
> y apagar los tres espejos. Esto ya NO es migración de dominios: es el
> desmantelamiento del esquema viejo `u531713409_kubera_ml`.
>
> Revisado por tres agentes independientes (claude-opus, claude-sonnet,
> claude-haiku). Informes completos en `agents/counselors/`.

## El censo, verificado

38 tablas en la base. Menos las 5 de los cortes (`pedidos_ml`,
`canal_inventario`, `costos_validados`, `costos_finales`, `costos_logs`) y las 2
ya migradas con su historia (`categorias_ml`, `crear_logs`), quedan **31**.
Conteo cuadrado contra `SHOW TABLES`: cero sin clasificar.

## Lo que el consejo cambió del plan original

### 1. `stock_watch_foto` estaba en el grupo equivocado

Yo la puse en "cachés" después de escribir en el mismo párrafo que **no es un
caché, es una fuente**. Los tres revisores lo marcaron. Verificado en
`channel_mirror.py:271-298`: es la **única** fuente del canal `general` de
`channel.listings`, sin fallback, y de ahí sale la decisión de a qué
publicaciones se les empuja stock.

**Sale del grupo de cachés. Se trata como fuente, con barrido de lectores.**

### 2. El grupo 4 va con SPLIT, no con copia idéntica

Los tres rechazaron clonar `ml_progress` a kubera. La formulación de opus:

> *"Una gemela 1:1 no perpetúa un segundo registro de la misma verdad —
> perpetúa un registro que ya sabes que está EQUIVOCADO en 63 casos. Sería
> copiar deuda con checksum."*

Medición que lo sostiene: `channel.listings` tiene 0 pares exclusivos de
`ml_progress`, 724 propios, y **63 con `ml_item_id` distinto** donde el vivo es
el de `listings` (SKUs republicados).

**El matiz que yo no había visto:** `channel.listings` es superset **solo de los
éxitos**. Los **269 intentos fallidos** no existen ahí. Un lector que pregunta
"¿este SKU falló con `gtin_error`?" NO puede repuntarse a `listings` — vería
"nunca se intentó" y alguien lo re-encolaría.

Los 19 lectores se clasifican **uno por uno** con esta pregunta:

| ¿Qué pregunta el lector? | Destino |
|---|---|
| ¿está publicado? ¿con qué `item_id` vivo? | `channel.listings` |
| ¿cómo salió el intento? (`error`, `gtin_error`, `published_at`, los 269 fallos) | bitácora tipo `ops.channel_submissions` |

### 3. Hay DOS candados en `fanout_log`, no uno

Yo encontré `pedidos_ml._ya_compensado`. Sonnet encontró el segundo:
`stock_full._ya_procesada` (línea 129), con el mismo patrón
`except → return False`, protegiendo movimientos **reales de mercancía**
(`full_ingreso`, `full_retiro`, `fba_ingreso`).

Y hay un agravante que verifiqué después: **`_ya_procesada` llama a
`_asegurar_schema()` justo antes de leer**, y `fanout_stock.py:506` tiene un
`CREATE TABLE IF NOT EXISTS fanout_log`. Si la tabla se borra, **el propio
lector la recrea vacía y después le pregunta**. Respuesta garantizada: "no lo
hice" → movimiento doble.

**Corrección al consejo (verificada contra Railway):** sonnet dijo que este
candado estaba dormido porque `FULL_WATCH_ENABLED=false`. Leyó el *default* de
`config.py`. En Railway está en **`true`**. Pero el vigilante corre en modo
**solo registro** (`full_watch_solo_registro` default `True`, no definida en
Railway): calcula, anota `fba_ingreso_sim`, y **no mueve ni una pieza**.

> **Condición de seguridad: el día que se quite el "solo registro" de ese
> vigilante, el candado tiene que estar arreglado ANTES.** Encender el modo real
> con el candado como está mueve inventario dos veces.

El primer candado (`_ya_compensado`, devolución de stock al cancelar) **sí corre
en el flujo real de pedidos hoy**.

### 4. Los dos candados NO comparten solución

Aquí sonnet corrigió a opus y a haiku, y tiene razón:

- `_ya_compensado` es estado **por pedido** → cabe como marca en `channel.orders`.
- `_ya_procesada` es estado **por operación de bodega** (`operacion_id`) — no
  tiene pedido al que pegarse → **necesita tabla propia**.

Meterlos juntos repetiría el error de origen: dos conceptos distintos en una
tabla porque "ya existe y es cómoda".

### 5. El orden: ensayo antes que memoria fresca

Haiku propuso atacar el grupo 4 primero, mientras el método del barrido está
fresco. Opus y sonnet dijeron que no, y sonnet cerró con el dato histórico:

> *"Ir directo al 4 apuesta la tabla más cara a que el procedimiento salga bien
> la primera vez — eso es lo que falló el 11 y el 12 de agosto."*

**Se adopta: ensayo barato primero, que produce el instructivo; luego el caro.**

### 6. Aporte de método que nadie había propuesto

Antes de cortar **cada** lector: ponerlo en **doble lectura con log de
discrepancia** unos días — lee de los dos lados, usa la respuesta vieja, anota
cuándo difieren. Es el arnés de paridad que ya usamos, pero por lector en vez de
por dominio.

Razón de sonnet: *"los 63 con item_id distinto ya demuestran que 'se ve limpio'
y 'está limpio' no son lo mismo en este proyecto."*

## El plan, en orden

### PASO 0 — Los candados (antes de encender el modo real del vigilante FULL)

1. Quitar el `except → return False` de los DOS candados: que el error propague.
2. `_ya_compensado` → marca en `channel.orders`.
3. `_ya_procesada` → tabla propia de operaciones aplicadas.
4. Quitar el `CREATE TABLE IF NOT EXISTS fanout_log` y repuntar sus **8
   escritores** (4 servicios + 4 scripts manuales) antes de dropear la tabla.
5. Matar el regex de `stock_full.py:353`, que lee la marca de agua del FBA
   parseando el **texto** del campo `resultado`. Ya existe la fuente buena:
   `channel_read.stock_fba_amazon()`.

### PASO 1 — Grupo 5, el ensayo (`ml_envio_real`, `ml_ficha`, `ml_visitas`)

Cachés del tab de Márgenes. Cada una: **1 lector + 1 escritor**, ambos en su
propio servicio, sin crons. El caso más limpio que queda.

El producto de este paso NO son las tres tablas: es el **instructivo de seis
pasos** (gemela → copia → comparación → escritor → lector → verificación) que se
va a usar en todo lo demás.

### PASO 2 — `stock_watch_foto` (fuente, no caché) — **CÓDIGO LISTO (v0.167.0)**

Que el vigilante escriba su foto directo en kubera. Correrlo unos días
escribiendo en los dos lados y comparando, y solo entonces apagar el viejo.
Antes de tocar nada que dependa del canal `general`.

**Hecho:** `ops.stock_watch_photo` creada (sandbox y producción), las 14,640
filas copiadas y verificadas celda por celda, los tres puntos de acceso
repuntados detrás de flags, y el `except → foto vacía` de `_foto()` arreglado
(devolvía "no sé" como "no hay foto", y la primera pasada absorbe lo pendiente
sin aplicarlo). Barrido de lectores cerrado: **1 escritor, 2 lectores**
(`stock_watch._foto` y `channel_mirror.sincronizar_drop`), ninguno fuera de este
repo.

**Falta, y es lo que decide Eduardo/Brandon:**

1. `SUPABASE_WRITE_STOCK_WATCH=true` — la foto se escribe en los dos lados.
   MySQL sigue mandando. Reversible sin costo.
2. Correr `comparar_stock_watch_foto.py` **cada mañana, varios días**. Los cinco
   bloques en verde o no se avanza.
3. `SUPABASE_READ_STOCK_WATCH=true` — la DECISIÓN pasa a kubera. **Aquí sí
   cambia lo que el vigilante le escribe a Woo: flujo vivo, regla 3.**
4. Recién entonces `stock_watch_foto` queda muerta y se archiva con el grupo 1.

El orden es al revés que en el paso 1 (allá lectura y escritura viajaron juntas)
porque esta foto no guarda un valor, guarda el estado ANTERIOR: una foto nueva
leída como buena haría que el vigilante recalculara el mundo de un golpe.

### PASO 3 — Grupo 4, el publicador

Split por intención en los 19 lectores, con doble lectura previa y flag por
lector. Los dos backlogs (246 MB, cero lectores) se archivan y se purgan —
**pero los 269 fallidos NO son backlog**: son la razón operativa de por qué
ciertos SKUs no están publicados, y van a la bitácora.

### PASO 4 — Cachés de verdad

`ventas_horarias`, `ventas_sync`, `amazon_imagenes`, `ml_image_edit_backlog`.
Un solo lector que repuntar (el de imágenes). Decisión de producto pendiente:
¿alguien usa todavía la vista `?fuente=ml` del tab Ventas?

### PASO 5 — Bitácoras

La campana (`webhook_eventos`, 3 endpoints) y `fanout_log`. **Después del paso
0**, porque el candado vive ahí.

### PASO 6 — Tokens · PASO 7 — Andamiaje

Tokens a Vault, junto con apagar `MLREgisterDaily` (que resultó ser EL renovador
de la regla 8, no un servicio muerto). El andamiaje al final siempre:
`alertas_estado` y `espejo_kubera_log` deben sobrevivir con kubera caída.

## Tarea pendiente antes de borrar cualquier tabla

Mi barrido fue `grep` sobre `backend/services/` y `backend/routers/`. Los tres
revisores marcaron el mismo hueco. Falta:

- **`backend/scripts/`** — sonnet ya confirmó que `sincronizar_ml_huerfanas`
  lee `ml_progress` para decidir.
- **Los repos externos** (`publicador`, `MonitoreoOperaciones`, `MLREgisterDaily`).
- **El frontend** y respuestas de API que embeban campos de estas tablas.

Cada "cero lectores" es una **hipótesis a verificar**, no un hecho: grep no ve
SQL dinámico, ni repos ajenos, ni tablas que se auto-crean.

## Nota sobre el consejo

`gemini-3-pro` no respondió: su CLI ya no soporta el tier de Gemini Code Assist
individual (`IneligibleTierError`, pide migrar a Antigravity). Era el único
revisor de otra familia de modelos, así que la diversidad real de esta revisión
fue menor de la buscada — vale tenerlo en cuenta al pesar el consenso.
