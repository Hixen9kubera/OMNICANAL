# Competencia · de dónde viene cada dato y por dónde se escribe

> Estado: **aplicado**. Los cuatro pendientes de la primera versión se cerraron
> el 13-ago: Selenium retirado, actor especializado borrado, respaldo cableado y
> SQLite eliminado.
> Escrito el 13-ago-2026 tras cerrar Deportes y Fitness, Herramientas y
> Recuerdos y Fiestas.

## La regla

**Todo lo que la API de Mercado Libre da, se pide por API.** Es gratis, no la
bloquean y no depende de que el HTML no cambie.

**Lo que la API no da, se raspa con un ACTOR DE APIFY.** Nunca con un navegador
local: ML corta la IP a las ~50 consultas y ya nos pasó dos veces a mitad de una
captura. Apify corre con proxy residencial propio.

Estas dos frases deciden todo lo demás.

---

## Qué da la API y qué no

Probado contra la API real, con token propio.

### Sí da (gratis, sin bloqueo)

| Endpoint | Qué devuelve | Dónde se usa |
|---|---|---|
| `GET /highlights/MLM/category/{id}` | Top de más vendidos: ids y posición. **Sin ficha** | Saber si ML publica ranking de una categoría |
| `GET /trends/MLM/{id}` | Hasta **50** keywords más buscadas de la categoría | `market_terms` completo |
| `GET /items/{id}/visits/time_window` | Visitas de 30 días de **cualquier** item | `visitas_30d` en las tres tablas |
| `GET /reviews/item/{id}` | Reseñas y rating de cualquier item | `reviews` en bestsellers |
| `GET /products/{id}/items` | Ofertas de un producto de catálogo | Resolver el id REAL y la subcategoría de cada fila |
| `GET /categories/{id}` | `path_from_root` | Nombre y raíz de una categoría |
| `GET /items/{id}/sale_price` | Precio **con descuento** de publicaciones PROPIAS | `sale_price` en listing_metrics |
| `GET /users/{uid}/items/search?seller_sku=` | Nuestras publicaciones por SKU | Detectar publicaciones fuera del pipeline |

### No da (403 con token válido, en las dos cuentas)

| Endpoint | Por qué importa |
|---|---|
| `GET /sites/MLM/search` | **No hay posición orgánica por API.** Es la razón de que exista el raspado del buscador |
| `GET /items/{id}` de un competidor | No da título, precio, imagen ni permalink de un ajeno |
| `GET /users/{id}/items/search` de otro seller | Ídem |

De ahí sale la frontera: **la API da la MÉTRICA, el raspado da la FICHA.**

---

## Los dos raspados, y los dos son actores de Apify

Ambos usan **`apify~playwright-scraper`** — un Chromium genérico al que le
mandamos nuestra propia `pageFunction`. No es un actor "de Mercado Libre": es el
navegador, corriendo en infraestructura de Apify con proxy residencial MX.

Si ese actor no trae nada, se reintenta solo con **`apify~puppeteer-scraper`**
(`apify_navegador_respaldo`). Mismo contrato de entrada y mismo `context.page`,
así que la `pageFunction` sirve para los dos — está escrita en el subconjunto
común de ambas APIs.

| | Página | Qué saca | Costo |
|---|---|---|---|
| **Ranking** `mas_vendidos_categorias()` | `/mas-vendidos/{cat}` | posición del badge, título, precio, precio de lista, imagen, href, seller, vendidos, rating | ~$0.007/página |
| **Buscador** `buscar_terminos()` | `listado.mercadolibre.com.mx/{término}` | posición orgánica, título, precio, imagen, href | ~$0.007/término |

**Por qué el genérico y no `piotrv1001~mercado-libre-listings-scraper`:** el
especializado cobra $0.09 por corrida más $0.003 por item, no etiqueta los
resultados con la consulta que los trajo (`searchQuery` viene vacío) y devuelve
~5 orgánicos contra ~48. Medido: 230 términos pasan de ~$24 a ~$1.61. **Borrado
el 13-ago** junto con `buscar()` y `buscar_varios()`, que eran sus únicos
llamadores y ya no los usaba nadie.

**Un id, dos ids.** La tarjeta trae dos identificadores distintos y confundirlos
cuesta caro:

- **`wid`** (del `#wid=` del href) — el ITEM real. Es el que sirve para `/visits`.
- **el id de la RUTA** (`/up/MLMU…`, `/p/MLM…`, `articulo…/MLM-…`) — el que
  `/products/{id}/items` necesita para resolver la SUBCATEGORÍA de cada fila.

Sin el segundo no hay nichos ni `pos_en_raiz`. Se derivan los dos en
`_pagina_y_tipo()`.

---

## Tabla por tabla: qué campo viene de dónde

### `enrich.market_bestsellers` — 4,002 filas

| Campo | Origen | Cobertura hoy |
|---|---|---|
| `posicion`, `titulo`, `precio`, `precio_lista`, `imagen`, `url`, `seller` | **Apify** (tarjeta) | 4,002 / 4,002 |
| `vendidos`, `rating` | **Apify** (etiquetas de la tarjeta) | 3,948 · 3,978 |
| `externo_id` | Apify (`wid` del href) | 4,002 |
| `id_pagina`, `tipo` | Apify (regex sobre el href) | 4,002 en lo nuevo |
| `visitas_30d` | **API** `/visits/time_window` | 4,002 |
| `reviews` | **API** `/reviews/item` | 3,252 |
| `item_categoria_id/nombre` | **API** `/products/{id_pagina}/items` | **66** — solo aplica a `nivel='raiz'` |
| `es_nuestro`, `sku_nuestro` | Cruce con nuestras publicaciones | — |

### `enrich.market_search_results` — 2,906 filas

| Campo | Origen | Cobertura |
|---|---|---|
| `posicion` (orgánica), `titulo`, `precio`, `imagen`, `url`, `seller` | **Apify** (buscador) | 2,906 · seller 1,395 |
| `visitas_30d` | **API** `/visits` **resolviendo antes el id de catálogo** | 2,869 |
| `rating` | API `/reviews` | 2,720 |
| `termino_id` | FK a `market_search_term` | 2,906 |

> **La trampa del cero.** `/visits` sobre un id de CATÁLOGO responde `0` sin
> error. Ese cero no es una medición: es que faltó resolver el id real con
> `/products/{id}/items`. Pasó dos veces — 1,893 filas en cero de las cuales
> 1,893 venían de un `/p/` o un `MLMU`. Siempre usar
> `competencia_captura.enriquecer_visitas`, nunca `visitas_30d` a secas.

### `enrich.market_terms` — 299 categorías

Un array JSON ordenado por categoría. **100% API** (`/trends`), gratis, tope de
50 por categoría.

### `enrich.market_search_term` — 464 términos · `market_sku_config` — 1,584

El término lo propone un **LLM** (DeepSeek) a partir del nombre del producto;
`medido_en` lo marca la corrida de Apify. El FK garantiza que un término se pague
una vez y lo reusen todos los SKUs que lo comparten (medido: 1.07–1.25 SKUs por
término).

### `enrich.market_listing_metrics` — 3,118 · nuestras publicaciones

| Campo | Origen |
|---|---|
| `sale_price`, `list_price` | **API** `/items/{id}/sale_price` con el token de SU cuenta |
| `visits_30d` | **API** `/visits` |
| `units_30d` | Nuestros pedidos |
| `title`, `estado` | API de ML |
| `stock_full`, `stock_own` | `channel.listings` (espejo del equipo) |

---

## El camino de ESCRITURA: uno solo

```
captura → competencia_store (fachada) → competencia_supabase → enrich.market_*
```

`competencia_store` ya no guarda nada: rutea al remoto y **revienta si no hay
`SUPABASE_DB_URL`** en vez de caer a SQLite. Una captura escrita en un disco que
nadie lee es peor que una que no corrió, porque parece que funcionó.

Las siete funciones de escritura, todas en `competencia_supabase`:

| Función | Escribe | Alcance del borrado |
|---|---|---|
| `reemplazar_ranking(cat, nivel, …)` | `market_bestsellers` | Solo esa `(canal, categoría, nivel)` |
| `reemplazar_terminos(cat, …)` | `market_terms` | Upsert de una fila |
| `reemplazar_busqueda(termino, …)` | `market_search_results` | Solo ese `termino_id` |
| `guardar_publicaciones(filas)` | `market_listing_metrics` | Upsert con COALESCE |
| `actualizar_termino` / `proponer_termino` | `market_sku_config` | Una fila |
| `activar_raiz(raiz)` | `market_sku_config` | Los SKUs de esa raíz |

**Dos invariantes que ya costaron un incidente cada una:**

1. **El borrado va acotado a la llave y en la misma transacción que el insert.**
   Recapturar Hogar no puede tocar Herramientas, y si el insert falla el dato
   viejo sigue ahí. El script viejo hacía `delete from <tabla>` completo.
2. **Los refrescos parciales usan `COALESCE`.** El paso de precios solo trae
   precio y el de visitas solo visitas; ninguno debe borrar lo del otro.

---

## Lo que se cerró el 13-ago

**Selenium retirado.** `competencia_mas_vendidos.py` y `competencia_busqueda.py`
borrados, con sus scripts (`competencia_login`, `competencia_buscar_todo`,
`competencia_medir_todo`, `competencia_capturar_categorias`). Con ellos se fue la
cadena que solo existía para el navegador: `correr`, `medir_sku`,
`_medir_busqueda`, `_medir_categoria` y los endpoints `/correr`, `/corrida` y
`/detalle`, ninguno con consumidor en el panel.

**El actor especializado, borrado.** `apify_ml_actor`
(`piotrv1001~mercado-libre-listings-scraper`) y sus dos funciones muertas. Daba
datos que no se podían atribuir a la consulta que los trajo.

**Actor de respaldo.** `apify~puppeteer-scraper` entra cuando el principal no
trae nada — que no siempre es un error visible: dos términos terminaron en
corridas `SUCCEEDED` con "Crawled 0/2 pages". Las `pageFunction` se escribieron
en el subconjunto común de las dos APIs (`page.waitForTimeout` es de Playwright;
se reemplazó por un `setTimeout` envuelto en Promise), así que la misma función
corre en los dos sin tocarla.

**SQLite eliminado.** `competencia_store` ya no tiene DDL, ni `_con()`, ni
`asegurar_schema()`, ni modo local: las 22 funciones exigen `SUPABASE_DB_URL` y
revientan sin ella. Antes las lecturas caían al archivo — en Railway el FS es
efímero, así que el tab arrancaba vacío, y cuando el archivo sí existía una
captura escrita ahí parecía haber funcionado sin que nadie la leyera.

**Nada en memoria.** `capturar_rankings_categorias` va por tandas de 20 y escribe
cada categoría en cuanto la tiene. La versión anterior raspaba todo en memoria y
guardaba al final: un bloqueo a media corrida se llevó 49 categorías ya raspadas.

### Queda abierto

- **`item_categoria_id`**: 66 de 4,002 filas. No es un bug — solo se resuelve en
  `nivel='raiz'` y hay 4 raíces capturadas. Se llena al capturar cada raíz nueva.
- **Dos términos de Herramientas** (`pistola de agua a presion`, `maquina de
  soldadura laser`) que fallaron dos veces antes de existir el respaldo. Vale
  reintentarlos ahora.

## Costos reales medidos

| Concepto | Costo |
|---|---|
| Todo lo de la API de ML | **$0** |
| Ranking de una categoría | $0.007 |
| Búsqueda de un término | $0.007 |
| Cerrar Deportes y Fitness completa | $1.36 |
| Cerrar Herramientas (solo la raíz que faltaba) | $0.01 |

El plan de Apify es STARTER, con tope de **$29 USD al mes**.
