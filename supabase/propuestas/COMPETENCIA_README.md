# Módulo COMPETENCIA — entrega al equipo técnico

**Estado:** BETA en producción (tab `/competencia`, etiqueta BETA en el navbar).
**Fecha:** 2026-08-03 · **Autor del MVP:** José (jose@kubera.mx)
**Lo que se necesita del equipo:** aprobar el esquema `competencia` en la BD kubera
(`supabase/propuestas/competencia_esquema.sql`) para que el módulo deje de depender
de un archivo SQLite local.

Este documento existe para que puedan continuar el proyecto sin leer el historial:
de dónde sale cada dato, qué ya funciona, qué está bloqueado por Mercado Libre y
cuáles son las decisiones que hay que tomar.

---

## 1. Qué responde el módulo

Tres preguntas, por SKU y por categoría:

| Pregunta | Cómo se responde |
|---|---|
| ¿Quiénes son los más vendidos de mi categoría? | ranking oficial de ML (`/highlights` + `/mas-vendidos/`) |
| ¿Dónde estoy yo frente a ellos? | nuestras visitas, ventas y precio contra la mediana del nicho |
| ¿Me pueden encontrar? | términos más buscados (`/trends`) cruzados contra nuestros títulos |

La vista tiene tres niveles: **categoría raíz** (su top de más vendidos) →
**los 5 nichos con más chance de competir** (los dicta el top del padre, no nuestro
inventario) → **subcategorías desplegables** con el top del nicho y luego nuestros
SKUs.

---

## 2. De dónde sale cada dato — la tabla que importa

Ningún dato se inventa. Esta es la procedencia real, verificada en vivo:

| Dato | Fuente | Ruta / tabla | Notas |
|---|---|---|---|
| Ranking de más vendidos (posición) | **API de ML** | `GET /highlights/MLM/category/{id}` | gratis, autenticado. Devuelve 3 tipos de entrada: `PRODUCT`, `USER_PRODUCT`, `ITEM` |
| Título, foto, precio, precio lista, % descuento, unidades vendidas, score del competidor | **navegador local** (Selenium + BeautifulSoup) | `mercadolibre.com.mx/mas-vendidos/{cat}` | **la API los niega con 403** para publicaciones ajenas |
| Visitas 30d de **cualquier** publicación, propia o ajena | **API de ML** | `GET /items/{id}/visits/time_window?last=30&unit=day` | un id por llamada; el multiget da HTTP 400 |
| Reseñas y rating de cualquier publicación | **API de ML** | `GET /reviews/item/{id}` | funciona aunque `/items/{id}` sea 403 |
| Item real detrás de un `MLMU…` | **API de ML** | `GET /products/{id}/items` | acepta ids de catálogo **y** de user product |
| Subcategoría de una fila del ranking | **API de ML** | `GET /products/{id}/items` → `category_id` | única vía por API; en las entradas `ITEM` no se puede (403) |
| Términos más buscados por categoría | **API de ML** | `GET /trends/MLM/{cat}` | gratis. 404 en categorías sin datos |
| Nuestras publicaciones (MLM, cuenta, estado) | **API de ML** | `GET /users/{uid}/items/search?seller_sku=` | **es la autoridad**, ver §6 |
| Nuestro precio real | **API de ML** | `GET /items/{id}/sale_price?context=channel_marketplace` | ⚠️ `channel.listings.price` es el precio de LISTA; 8 de 16 publicaciones venden por debajo |
| Unidades vendidas 30d nuestras | **Supabase** (preferido) o API | `channel.orders` + `channel.order_items`; fallback = barrido de `/orders/search` | el espejo va con retraso: 514 vs 566 en la muestra |
| Categoría de un SKU nuestro | **MySQL** | `categorias_ml` (`category_id`, `ruta`, `cat1..cat4`) | separador de `ruta`: `›` (U+203A) |
| Nombre y foto del producto | **MySQL / WooCommerce** | `productos`, con fallback a `wp_postmeta._sku` + `_thumbnail_id` | Woo es la fuente de verdad del catálogo |
| SKUs por categoría (catálogo completo) | **MySQL** | `categorias_ml` | se usa para saber si tenemos con qué competir en un nicho |
| Publicaciones de SKUs no vigilados | **MySQL** | `ml_progress` (`ml_item_id`, `ml_url`, `success=1`) | bitácora del publicador |
| Todo lo medido por el módulo | **SQLite local** ⚠️ | `backend/competencia.db` | **esto es lo que hay que migrar a Supabase** |

### Lo que Mercado Libre NO da, y ya se agotó la búsqueda

Documentado para que nadie vuelva a gastar tiempo aquí:

- **`GET /sites/MLM/search` → 403** en las dos apps. **No existe posición orgánica de
  búsqueda por API.** Solo raspando.
- **`GET /items/{id}` de un competidor → 403.** Probado con 7 combinaciones de
  `attributes` (incluida `attributes=id`), `include_attributes=all`, el multiget
  (responde 200 con un 403 por item dentro), `/sites/MLM/items/{id}`, `/prices`,
  `/sale_price` con 3 contextos, y con los dos tokens y sin token.
  Control: la llamada idéntica sobre un item **nuestro** devuelve
  `{"price":1026.8,"available_quantity":3}`. Es permiso, no un error transitorio.
- **`/suggestions/items/{id}/details` → 404**, tanto propios como ajenos.
- **`GET /user-products/{MLMU}`** funcionó a media sesión y luego empezó a dar 403.
  No depender de él: usar `/products/{id}/items`.
- **La imagen del competidor no existe por API.** Solo del raspado.
- **Amazon no expone visitas de competencia en ninguna API.** El sustituto es BSR +
  número de ofertas + Buy Box (`/products/pricing/v0/items/{Asin}/offers` y
  `/catalog/2022-04-01/items?includedData=salesRanks`).

---

## 3. Arquitectura

```
                    ┌──────────────────── LECTURA ────────────────────┐
                    │                                                 │
  API Mercado Libre │  Supabase (BD kubera)      MySQL u531713409_…    │  WooCommerce
  ─────────────────  │  ──────────────────       ────────────────      │  ───────────
  /highlights        │  channel.listings         categorias_ml         │  wp_postmeta
  /visits/…          │  channel.orders           ml_progress           │  wp_posts
  /trends            │  channel.order_items      productos             │
  /reviews           │  channel.categories                            │
  /products/{id}/items                                                │
                    └─────────────────────┬───────────────────────────┘
                                          │
                          ┌───────────────▼────────────────┐
                          │  backend/services/competencia_*  │
                          │  ─────────────────────────────── │
                          │  _ml.py           capa API ML     │
                          │  _mas_vendidos.py navegador local │
                          │  _captura.py      orquestador     │
                          │  _store.py        persistencia    │
                          └───────────────┬────────────────┘
                                          │
                            ┌─────────────▼─────────────┐
                            │  backend/competencia.db    │ ⚠️ SQLite LOCAL
                            │  (a migrar a Supabase)     │    gitignored
                            └─────────────┬─────────────┘
                                          │
                            ┌─────────────▼─────────────┐
                            │  routers/competencia.py    │
                            │  GET /api/competencia/…    │
                            └─────────────┬─────────────┘
                                          │
                            ┌─────────────▼─────────────┐
                            │  frontend/app/competencia  │
                            └───────────────────────────┘
```

### Archivos y su responsabilidad

| Archivo | Qué hace |
|---|---|
| `services/competencia_ml.py` | Capa de la API de ML. Su docstring lista qué funciona y qué da 403. Auto-refresca el token en 401 |
| `services/competencia_mas_vendidos.py` | Navegador local (Selenium + bs4) sobre `/mas-vendidos/{cat}`. Ritmo, detección de bloqueo, parseo de tarjetas |
| `services/competencia_captura.py` | Orquestador: siembra SKUs, captura rankings + términos, sugerencias de IA, nichos |
| `services/competencia_store.py` | Persistencia (SQLite hoy) + el agregado `vista()` que arma el árbol |
| `services/competencia_scraper.py` | Camino Apify. **Ya no se usa para el ranking** (ver §5) |
| `routers/competencia.py` | ~18 rutas bajo `/api/competencia` |
| `frontend/app/competencia/page.tsx` | La vista completa |
| `scripts/competencia_cron.py` + `railway.competencia.json` | Cron mensual (`0 8 1 * *`, `restartPolicyType: NEVER`) |

### Rutas principales

| Ruta | Qué devuelve |
|---|---|
| `GET /vista?canal=` | El árbol completo de un golpe: raíz → nichos → subcategorías → SKUs. Los filtros de la UI se aplican en el navegador sobre esto |
| `GET /sku/{sku}` | Competencia directa de un SKU: líderes del nicho, nuestras publicaciones por tienda, términos |
| `GET /subcategoria/{cat}/skus` | Todos nuestros SKUs de esa categoría (catálogo completo, no solo los vigilados) |
| `GET /subcategoria/{cat}/terminos` | Términos de la categoría con la cobertura de nuestros títulos |
| `POST /subcategoria/{cat}/sugerir` | Palabras clave sugeridas por IA para el nicho |
| `POST /sku/{sku}/sugerir` | UN título de ≤60 caracteres basado en la competencia directa |
| `POST /rankings` | Corre la captura de rankings + términos (requiere navegador local) |
| `POST /sembrar` | Da de alta SKUs a vigilar |
| `GET /estado` | Diagnóstico: qué fuentes hay y qué no se puede medir, con el motivo |

---

## 4. Lo que hay que aprobar

`supabase/propuestas/competencia_esquema.sql` — **6 tablas** en un esquema nuevo
`competencia`.

> ⚠️ **Primero verifiquen si el esquema ya existe.** Una versión anterior de ese
> archivo vivió como `supabase/migrations/0008_competencia.sql` (commit `ece6306`)
> y **no pudimos confirmar si `aplicar_migraciones.py` la aplicó** en la BD kubera
> (el token de MCP disponible está caído). Si `competencia` ya existe, los
> `create table if not exists` son NO-OP y las columnas nuevas de
> `rankings_categoria` (`id_pagina`, `tipo`, `item_categoria_id`,
> `item_categoria_nombre`, `reviews`) **no se agregarían**: van por `ALTER TABLE`.
> Comprobar con
> `select table_name from information_schema.tables where table_schema='competencia';`

Vive en `propuestas/` y no en `migrations/` **a propósito**:
`backend/scripts/aplicar_migraciones.py` hace `glob("*.sql")` sobre `migrations/` y
lo aplicaría sin revisión. Al aprobarse se mueve a `0010_competencia.sql`.

### Qué LEE de lo que ya existe (no duplica nada)

`core.products`, `core.accounts` (`legacy_code` = BEKURA / SANCORFASHION),
`channel.listings` (con `category_id` y `store_name`, agregadas en la migración
**0009**, ya aplicada), `channel.categories` (`path`), `channel.product_category`,
`channel.orders` / `channel.order_items`.

Deliberadamente **no** se copian nombre, categoría ni ruta del SKU: salen por JOIN.
Duplicarlos los haría divergir.

### Qué es nuevo y por qué no cabe en lo existente

1. **Publicaciones ajenas.** Los competidores no son nuestros listings; no
   pertenecen a `channel.listings`.
2. **Métricas de un PERIODO** (30 días móviles). `channel.listings` es el estado
   actual del listing; meterle métricas de ventana obligaría a versionarla.
3. **El ranking por categoría** y **los términos de búsqueda** no tienen hogar hoy.

### Las 6 tablas

| Tabla | Filas hoy (local) | Contenido |
|---|---|---|
| `competencia.skus` | 8 | los SKUs vigilados y su `termino_general` |
| `competencia.publicacion_metricas` | 16 | visitas y unidades 30d por (sku, cuenta, canal) |
| `competencia.rankings_categoria` | 53 | el top de más vendidos por categoría |
| `competencia.terminos_categoria` | 245 | términos más buscados por categoría |
| `competencia.resultados` | 0 | las tres mediciones por SKU (aún no en uso) |
| `competencia.corridas` | 0 | bitácora de cada captura |

### Decisiones que conviene discutir antes de aplicar

1. **Sin histórico** (decisión de producto): cada corrida **borra** la anterior. Es
   la foto del mes, no una serie. Cambiarlo es un método del store, pero hay que
   decidirlo **antes** de acumular volumen.
2. **Periodicidad mensual** por cron de Railway, no por el scheduler embebido.
3. `termino_origen` (`ia` | `manual`) **protege la corrección humana** de ser pisada
   por la siguiente corrida. No quitarlo.
4. **`costing.costos_finales` tiene PK `(sku, canal)`** y hoy todo es
   `canal='mercado_libre'`: cualquier consulta nueva debe filtrar por canal.

---

## 5. El raspado: por qué navegador local y no Apify

Se probaron los dos. **Decisión de José (3-ago): navegador local con proxy, sin
actores de Apify.**

Dos razones, la segunda es la que decide:

1. Apify se paga por cómputo y el costo crece con los SKUs.
2. **La normalización del actor tira `id_pagina`** — el id que va en el URL, que es
   exactamente el que devuelve `/highlights`. Sin él no se puede unir la ficha
   raspada con la posición oficial ni resolver la subcategoría de cada fila.

### Trampas verificadas del raspado

- **El actor de listados de ML acepta `categoryUrls`/`startUrls` y los ignora en
  silencio**: pedimos una URL de tapetes y devolvió iPhone 15. **No falla, MIENTE.**
  Solo `searchQueries` es confiable.
- **Rate limiting por IP, no azar.** Medido: 2 categorías seguidas pasan; 8 seguidas
  solo dejaron pasar 2. La cura es ir más despacio (`_PAUSA_ENTRE = 8s`,
  `_PAUSA_BLOQUEO = 25s`), no reintentar más rápido.
- **Tres estados de bloqueo** distintos: `suspicious-traffic`,
  `account-verification` y `/captcha/wall`. Detectar solo uno produce salida
  activamente engañosa (nos hizo reportar "ML no publica ranking" cuando sí lo
  publicaba).
- **El `href` de la tarjeta trae `#wid=` con el item_id REAL.** Con eso no hace falta
  resolver el `MLMU…` por API: una llamada de visitas por fila y ya.
- **Los ids del ranking se caen si la regex no cubre las tres formas de URL**
  (`/up/`, `/p/`, `articulo…-_JM`). Faltaba la tercera y `MLM1747` devolvía 7 de 8
  filas **sin avisar**.
- **El ranking se mueve durante el día.** Entre dos corridas separadas por minutos
  cambiaron ids y posiciones. La foto mensual captura un momento; no leerla como
  promedio.

### Escala real

**988 subcategorías únicas** con listings publicados + **26 raíces** = **1,014
páginas**. A ~12 s por página: ~3.4 h en serie, ~50 min con 4 navegadores.
Con bloqueo de imágenes y fuentes, ~0.5 GB por corrida completa → **menos de $5 USD
al mes** con cualquier proveedor de proxy residencial.

---

## 6. Hallazgos de calidad de datos (para el equipo)

Cosas que el módulo destapó y que **no son del módulo**:

1. **`ml_progress` y `channel.listings` no conocían las publicaciones vivas de
   `MUE-0163-TEL`**, que es el SKU con ~98% del tráfico del piloto. La autoridad
   sobre nuestras publicaciones es `GET /users/{uid}/items/search?seller_sku=`, no
   nuestras tablas. El store tiene un candado anti-degradación por esto.
2. **`channel.listings.price` es el precio de LISTA.** 8 de 16 publicaciones venden
   por debajo: `MUE-0163-TEL` en BEKURA a $290 contra $989 de lista (−71%).
   El precio real está en `/items/{id}/sale_price?context=channel_marketplace`.
3. **El espejo de pedidos en Supabase va con retraso** (514 contra 566 unidades en
   la muestra). Por eso hay fallback al barrido de la API de ML, y la corrida
   reporta qué fuente usó.
4. **`channel.product_category` solo cubre `mercado_libre`**: 13,694 filas, 0 para
   general y 0 para amazon.
5. **El separador de `channel.categories.path` es `›` (U+203A)**, no `>`.
6. **Publicaciones pausadas que son las que venden.** Patrón en al menos dos SKUs:
   la publicación PAUSADA tiene más visitas y ventas que la activa
   (`TEC-1539-AZL-XL`: pausada 121 visitas / 12 ventas, activa 11 / 0). La vista lo
   marca en rojo.
7. **Los títulos difieren por tienda** para el mismo SKU: `MUE-0163-TEL` es
   "Malla De Tela 6x4m…" en BEKURA y "Lona Sombra Reforzada 4x6m…" en
   SANCORFASHION. Por eso la cobertura de términos se mide **por tienda**.

---

## 7. La IA, y por qué todo lo que dice se verifica

Se usa `ia_generadores._completar` (DeepSeek) para dos cosas, y **ninguna decide qué
tiene demanda** — eso siempre sale de datos medidos:

- **Título sugerido** (≤60 caracteres) a partir de los títulos de los líderes del
  nicho y los términos no cubiertos.
- **Palabras clave** por subcategoría, a partir de los términos de `/trends` y de
  los títulos líderes.

**Falla en las tres cosas y el backend las recalcula:**

1. **Presume cobertura.** Declaró cubrir 22 términos; el título cubría **2**. Se
   devuelve `cubre_verificado` recalculado y la UI avisa si difiere de lo declarado.
2. **Se pasa de 60 caracteres.** El largo se cuenta en el backend.
3. **Recomendó evitar la demanda.** Puso `tapetes carro` y `tapetes de carro` —los
   términos **#1 y #2 más buscados**— en la lista de "evitar". Hay un candado: nada
   que ML publique como término puede caer ahí; se descarta y se reporta el intento.

Cada palabra sugerida lleva `respaldada`: si no aparece en los términos medidos ni
en los títulos líderes, la IA la inventó y la UI la pinta distinto.

Un intento **fallido** que conviene no repetir: derivar el título del competidor con
IA desde `/items/{id}/description`. Alucina marcas y materiales ("Tunix", "PVC Fibra
Carbono" donde era "Metalizado"). **El título real está en el slug del permalink** y
en el DOM de la tarjeta.

---

## 8. Convenciones que el código respeta (no romperlas)

- **`null` no es `0`.** Conversión con 0 visitas es **indefinida**, no 0% — pintar un
  cero se lee como "convierte mal" cuando nadie vio la publicación. Igual con
  visitas/ventas de SKUs no medidos: `—`, no `0`. Al ordenar, los `null` van al final
  en **ambas** direcciones.
- **Distinguir "ML no tiene el dato" de "no lo pudimos traer".** Llevan a acciones
  opuestas. `Bujías` (MLM179785) y `Cartuchos de Turbo` (MLM458946) dan `/highlights`
  vacío **y** `/trends` 404: ML no publica nada de esas categorías. Reintentar no
  cambia nada, y la UI lo dice.
- **Los "+50mil vendidos" son cota inferior**: ML redondea. Sirve para ordenar
  nichos, no como cifra. La UI lo marca con `+`.
- **La brecha se calcula contra la MEDIANA**, no el promedio: un producto de $1,189
  en el top 20 de Tapetes movería el promedio y taparía el problema.
- **`/products/` nunca con un id de tipo `ITEM`.** Los espacios de nombres chocan:
  `MLM2050204991` como producto son bocinas de Thinkpad; como item es un tapete con
  14,922 visitas.
- Reglas de la casa del repo: `backend/vendor/` no se toca; la elección del panel
  manda sobre cualquier detector; LiteSpeed cachea `chunche.shop` (cache-bust `_cb`
  en toda lectura que alimente una escritura).

---

## 9. Bloqueadores para que el BETA muestre datos en producción

Esto es lo primero que hay que resolver:

1. **⚠️ Los datos viven en `backend/competencia.db`, que está gitignored y es
   local.** Railway tiene sistema de archivos efímero. **En producción el tab
   arranca vacío** hasta que se apruebe el esquema y se cablee la lectura a
   Supabase. La UI muestra su estado vacío honesto, no un error.
2. **El navegador local no existe en Railway.** Sin Chrome, `POST /rankings` no puede
   raspar. `competencia_mas_vendidos.disponible()` lo guarda y `/estado` lo reporta.
   Opciones: imagen con Chrome, un worker aparte, o correr la captura desde una
   máquina del equipo y escribir a Supabase.
3. **Falta contratar el proxy residencial MX** para las 1,014 páginas del cron
   mensual. Sin él el rate limiting por IP deja fuera la mayoría de categorías.
4. **Falta `SUPABASE_DB_URL`** (Session pooler, us-east-1) para leer precio y
   unidades de Supabase en vez del fallback por API.

## 10. Pendientes de producto

- **Amazon**: el tab ya tiene el filtro de canal y el esquema lleva `canal` desde el
  principio, pero no hay captura de Amazon. Sustituto de visitas: BSR + ofertas +
  Buy Box.
- **`resultados` y `corridas` están en 0**: las tres mediciones por SKU
  (`general` / `titulo` / `categoria`) quedaron diseñadas pero la vista actual se
  apoya en `rankings_categoria` y `terminos_categoria`. Decidir si se completan o se
  retiran del esquema antes de aplicarlo.
- **La imagen del competidor** solo existe raspando; hoy se guarda la URL de
  `http2.mlstatic.com` que ML puede rotar.
