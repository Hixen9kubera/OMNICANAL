# Competencia · arquitectura de datos (propuesta para el equipo)

Estado: **MVP corriendo en local**, 8 SKUs de Mercado Libre. Este documento es el
contrato de dónde sale cada dato y qué pedimos crear en la BD kubera.

La regla que seguimos: **si el dato ya existe en Supabase, se LEE de ahí** (porque
es de donde el panel leerá en producción). **Si el módulo lo CREA, hoy vive en
SQLite local** (`backend/competencia.db`, gitignored) y aquí proponemos su destino.

---

## 1. Lo que el módulo LEE de lo que ya existe

| Dato | Fuente | Notas |
|---|---|---|
| Identidad del SKU | `core.products` | — |
| Tienda | `core.accounts.legacy_code` | BEKURA / SANCORFASHION |
| Nuestras publicaciones | `channel.listings` | +`category_id`/`store_name` de la migración 0009 |
| Árbol de categorías | `channel.categories` | `path` agrupa la tabla por nivel |
| Categoría del SKU | `channel.product_category` | respeta "el panel manda" |
| **Unidades vendidas 30d** | `channel.order_items.cantidad` ⋈ `channel.orders.creado_at` | única fuente con cantidad |
| **Precio por publicación** | `channel.listings.price` | poblado y coincide con la API; se une por `(sku, store_name, canal)` |
| ID de publicación | `channel.listings.listing_id` | MLM… hoy, ASIN cuando entre Amazon |

## 2. Lo que el módulo CREA (hoy en SQLite → propuesta de destino)

| Dato | Origen real | Destino propuesto |
|---|---|---|
| **Visitas 30d** por publicación | API ML `/items/{id}/visits/time_window` | `competencia.publicacion_metricas` |
| Término general de búsqueda | LLM + corrección humana | `competencia.skus` |
| Posición en búsqueda general | scraper Apify | `competencia.resultados` |
| Posición en competencia directa | scraper Apify | `competencia.resultados` |
| Top de más vendidos de categoría | API ML `/highlights` | `competencia.resultados` |
| Ficha del competidor (título, precio, foto, descripción, vendidos) | scraper Apify | `competencia.resultados` |
| Bitácora de cada corrida | el propio módulo | `competencia.corridas` |

**Las tablas YA EXISTEN en la BD kubera, en el esquema `propuestas`** — para que
el equipo las inspeccione con datos reales en vez de leer un DDL:

| Tabla | Columnas |
|---|---|
| `propuestas.competencia_skus` | 6 |
| `propuestas.competencia_publicacion_metricas` | 9 |
| `propuestas.competencia_corridas` | 13 |
| `propuestas.competencia_resultados` | 23 |

Por qué un esquema aparte y no `core`/`channel`: **nada de `propuestas` participa
en las rachas de actas de `/migracion`** ni lo escriben los ETLs, así que un
borrador no puede romper un dominio de producción. Las 4 tienen **RLS activo** sin
políticas, igual que las 16 tablas v4: solo entra la `service_role` del backend.

Al aprobarse se mueven al esquema definitivo con su migración numerada. El DDL de
referencia queda en [`competencia_esquema.sql`](competencia_esquema.sql), fuera de
`migrations/` porque `aplicar_migraciones.py` lo tomaría con su `glob("*.sql")`.

### Dos decisiones que necesitan visto bueno

1. **Sin histórico.** Cada corrida borra la anterior; `resultados` es la foto del
   mes. Si se quiere serie hay que decidirlo *antes* de acumular: son ~25 filas ×
   3 mediciones × N SKUs por mes.
2. **La conversión no se guarda.** Es `unidades/visitas`, se calcula al leer. Con
   0 visitas es **indefinida**, no 0% — guardarla invita a pintar un cero falso
   que se lee como "convierte mal".

---

## 3. Hallazgos de calidad de datos (esto es lo que hay que revisar)

**El precio y el `listing_id` vienen de `channel.listings`, no se duplican.** La
muestra sale completa (8/8 con `price`, `listing_id`, `status` y `category_id`) y
los valores empatan con la API de ML. La única excepción es el hueco de abajo.

**`ml_progress` y `channel.listings` no conocen publicaciones vivas.**
`MUE-0163-TEL` está activo en las DOS tiendas (`MLM4702363498` en BEKURA,
`MLM4700224434` en SANCORFASHION) y **ninguna de las dos tablas lo tiene** — en
`channel.listings` solo aparece con `canal='general'` y `listing_id` NULL. El panel
lo reportaba como "sin publicar" cuando es el SKU con **9,697 visitas y 566
unidades vendidas**, el 98% del tráfico del piloto. La autoridad tuvo que ser
`GET /users/{uid}/items/search?seller_sku=`. Vale correr esa comparación contra
los 3,698 SKUs para medir el hueco completo (~7,400 llamadas, gratis).

**El espejo de pedidos va atrás.** Misma ventana de 30 días, mismo SKU:
`channel.order_items` da **514** unidades y la API de ML **566**. Por eso el módulo
reporta de qué fuente salió el número.

**El separador de `channel.categories.path` es `›` (U+203A), no `' > '`** como dice
el comentario del DDL de `0001_esquema_v4.sql`. Partir por `' > '` devuelve 1 solo
nivel para las 2,613 filas con path. Los niveles reales van de 2 a 7.

**`channel.product_category` solo cubre Mercado Libre**: 13,694 filas, todas
`channel_id='mercado_libre'`. Ni una de `general` ni de `amazon`. Por eso el
`category_id` que agregamos en 0009 llena **3,907 de 18,762** listings (96.6%
dentro de ML, 0% en los otros dos canales). Depende de la v2 del ETL de categorías.

---

## 4. Techos de las APIs (ya medidos, no supuestos)

| Endpoint | Resultado |
|---|---|
| `/visits/items?ids=A,B` | **400** — un solo id por llamada, incluso con items propios |
| `/users/{uid}/items_visits` | 400 con `date_from`; `time_window` sí responde pero da el **total de la cuenta** e **ignora `ids`** |
| `/items/{id}` de un competidor | **403** — sin título, precio, imagen ni descripción |
| `/sites/MLM/search` | **403** en las dos apps → no hay posición orgánica por API |
| `/user-products/{MLMU…}` ajeno | **403** (funcionaba más temprano el mismo día; ML lo cerró) |
| `/orders/search` con `seller_sku` o `q=` | filtros **ignorados**; sí funciona `item=MLM…` |
| `/highlights/{site}/category/{id}` | ✅ gratis, top 20 con posición — pero solo da id+tipo |
| `/items/{id}/visits/time_window` | ✅ por item, ventana de 30 días |

Del `/highlights`, qué se puede resolver de cada entrada:

| tipo | ficha | visitas |
|---|---|---|
| `ITEM` | ❌ 403 | ✅ |
| `PRODUCT` | ✅ `/products/{id}` | ❌ |
| `USER_PRODUCT` | ❌ 403 | ❌ |

En *Malla Sombra* el ranking son 6 `PRODUCT` + 4 `USER_PRODUCT` y **cero `ITEM`**:
nombres sí, visitas ninguna.

---

## 5. Costo del scraper (medido, no estimado)

La ficha del competidor solo la da el scraper. `includeProductDetail` cuesta
**$0.025/item contra $0.003 — 8.3×**, y es lo único que trae `descripcion` y
`vendidos`.

| Config | 8 SKUs | ~1,000 SKUs |
|---|---|---|
| 25 items + detalle | $8.75 | **$875/mes** |
| 25 items sin detalle | $1.05 | $105/mes |
| 15 items sin detalle | $0.63 | $63/mes |
| solo término general, sin detalle | $0.53 | $30/mes |

Cambiar de actor no ayuda: de 16 actores de ML en Apify, el que usamos ya es de los
más baratos y varios cobran **$0.09 de arranque por corrida** (a 1,400 corridas,
$126 solo en arrancar).

---

## 6. Lo que falta para salir de local

1. **Que el backend pueda hablar con `propuestas`.** Hoy no puede, por ninguna de
   las dos vías:
   - `SUPABASE_DB_URL` no está configurada (psycopg2). Dato útil: kubera está en
     **`us-east-1`**, no en us-west-2 como asume el comentario de `config.py`.
   - PostgREST solo expone `public, graphql_public` → `propuestas` responde
     **406 PGRST106**. Exponerla es un `PATCH /v1/projects/{ref}/postgrest`
     (`db_schema`), pero cambia la superficie REST de TODO el proyecto: decisión
     del equipo, no de esta sesión. Riesgo bajo porque las 4 tablas tienen RLS sin
     políticas, así que `anon` no leería nada.
2. **Aprobar el esquema** y moverlo al definitivo con migración numerada.
3. **Decidir la configuración de costo** del scraper antes de escalar a 1,000 SKUs.
