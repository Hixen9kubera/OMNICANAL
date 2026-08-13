# El flujo completo de un producto, canal por canal — y cómo se replica

> **Para quién:** las sesiones de TikTok, Temu y Walmart, y quien tenga que abrir
> el siguiente canal. Escrito para leerse sin el historial de ninguna
> conversación.
> **Método:** todo está levantado del código o medido contra producción el
> **13-ago-2026**. Donde no se pudo verificar, se dice.
> **Qué NO es:** el modelo de datos (eso es `CONTENIDO_POR_CANAL.md`) ni el
> manual de una API concreta (`TIKTOK_MANUAL.md`, `TEMU_MANUAL.md`,
> `WALMART_MX_MANUAL.md`).

---

## LA IDEA EN UNA FRASE

Un producto pasa por **seis etapas**, siempre las mismas. Mercado Libre las tiene
todas, Amazon las tiene todas desde hoy, y los demás canales tienen algunas
sueltas. Abrir un canal es completarle las seis, en orden.

```
1. NACE          Crear Productos: Alibaba → WooCommerce
2. SE ESCRIBE    contenido con IA por canal (título, descripción, atributos…)
3. SE CLASIFICA  categoría del canal (y de ella cuelgan sus obligatorios)
4. SE PUBLICA    el publicador arma el payload y lo manda
5. SE OBSERVA    estado por SKU: qué se publicó, con qué precio y stock
6. SE OPERA      pedidos que entran y stock que sale
```

Las etapas 2 y 3 se alimentan de datos que hay que **cargar antes** (los
requisitos del canal), y la 6 depende de que la 5 exista. Saltarse el orden es la
forma conocida de que algo falle en silencio.

---

## ETAPA 1 — NACE (igual para todos los canales)

`backend/services/crear_producto.py::_procesar`, disparado desde la pestaña
**Crear Productos**. Concurrencia: `asyncio.Semaphore(2)`.

| Paso | Qué hace | Con qué |
|---|---|---|
| 1/5 | Raspa la URL de Alibaba | Apify (`APIFY_ALIBABA_ACTOR`) |
| 2/5 | **Título y descripción con IA** | Claude (`titulo_descripcion_ia`) |
| 3/5 | Copia imágenes a WordPress | `procesar_imagenes` |
| 4/6 | Categoría de **Mercado Libre** + asegura costos | `categoria_ml` + `costos.asegurar_finales` |
| 5/6 | **Atributos de Mercado Libre con IA** | DeepSeek (`ml_atributos`) |
| 6/6 | Escribe todo en WooCommerce y lo deja en `pending` | REST de Woo |
| — | Acta de nacimiento en `core.products` | `core_write.registrar` |
| — | **Contenido de Amazon con IA** | `amazon_ia.generar_para_alta` |

⚠️ **El título que se guarda en WooCommerce está escrito para Mercado Libre.**
El prompt de `_prompt_seo` dice literalmente *"experto en copywriting para
MercadoLibre México"* y pide **máximo 60 caracteres**, que es el límite de ML.
Ese texto es el `name` del producto en Woo — o sea, el título del canal General
es un título de ML. No está mal, pero hay que saberlo: cada canal que quiera un
título propio tiene que generárselo (Amazon ya lo hace: 75 sin acentos).

⚠️ **El acta en `core.products` va ANTES de cualquier escritura por SKU.**
`enrich.channel_content` y las tablas de `channel` tienen FK contra el maestro:
escribir antes del acta da 409. Es el patrón "identidad primero".

---

## ETAPA 2 — SE ESCRIBE (el contenido con IA)

### Lo que hace Mercado Libre

- **Al crear**: título + descripción (Claude) y atributos (DeepSeek vía
  `ml_atributos.generar_atributos`).
- **Los atributos NO se los inventa la IA**: salen de
  `GET /categories/{id}/attributes` de la API de ML, separando los que traen
  `tags.required` o `catalog_required` (PRINCIPALES) del resto (SECUNDARIOS), y
  descartando `hidden` y `read_only`. La IA solo rellena valores.
- **Dónde queda**: metas de WooCommerce `ml_attr_<ID>` (lo que lee el publicador)
  más una meta `ml_atributos` con el JSON entero de respaldo.
- **Botón "Mejorar con IA"**: `ia_generadores._MEJORAR["mercado_libre"]`, y
  vuelve a pedir los atributos reales de la categoría.

### Lo que hace Amazon (desde v0.137.0)

- **Un solo prompt**: `amazon_ia._SISTEMA`, el spec de Brandon literal.
- **Los obligatorios salen de `channel.field_requirements`** (64,125 filas / 553
  productTypes), consultados por el `productType` del SKU. Los que ya tienen
  `default_value` no se le piden a la IA (los pone el publicador); los que no
  tienen ni canónico ni respaldo se le piden **por su nombre nativo** — así se
  llenó `fabric_type`, que estaba declarado como "sin nadie que lo llene".
- **El código valida** (`amazon_contenido`): título 75, highlights 125, bullets
  150–200 ×5, descripción 2000 y términos de búsqueda **249 BYTES**. Una ronda de
  reparación con los problemas de vuelta.
- **Dos niveles de rechazo**: FATAL (lo que Amazon trunca, ignora o castiga) no
  se aplica; AVISO (estilo) sí se aplica y se reporta.
- **Marcas registradas**: `terminos_protegidos`, lista cerrada de 86, con guarda
  de compatibilidad.
- **Dónde queda**: `enrich.channel_content` (`sku, canal, cuenta`), `origen: ia`,
  `categoria` = el productType, más `hash_base` de la base con la que se generó.

### La diferencia que hay que copiar

| | Mercado Libre | Amazon |
|---|---|---|
| Fuente de los obligatorios | API en vivo por categoría | tabla `field_requirements` |
| Validador de límites | **no hay** | `amazon_contenido` |
| Detector de marcas | **no hay** | `terminos_protegidos` |
| Persistencia del contenido | metas de Woo | `enrich.channel_content` (+ metas) |
| Se guarda solo al generar | no (hay que darle Guardar) | **sí** |

**Para un canal nuevo, el patrón a copiar es el de Amazon**, porque es el que
sobrevive a la sesión y el que deja auditar por qué salió lo que salió.

---

## ETAPA 3 — SE CLASIFICA (la categoría, y sus obligatorios)

**Ninguna taxonomía se traduce a otra, y equivocarse NO da error**: el producto
queda vivo y mal clasificado (`TEC-1812-NEG`, publicado en "Máquinas de Coser"
siendo otra cosa).

| Canal | Qué es el id | De dónde sale | Dónde se guarda la elección |
|---|---|---|---|
| Mercado Libre | `MLM162997` | predictor o el árbol | meta `ml_categoria_id` (**panel, MANDA**) > `ml_category_id` (predictor) |
| Amazon | `productType` (`CHAINSAW`) | Definitions API | meta `amz_product_type` (**panel**) > `amazon_progress` > detección por título |
| TikTok | id numérico de HOJA | recomendador (falla el 49%) o el árbol | **no existe** |
| Temu | `catId` de hoja | recomendador o descenso | **no existe** |
| Walmart | `subCategory` + etiqueta en español | hardcodeado en el script | **no existe** |

**La regla de la casa**: la elección del PANEL gana a cualquier detector
automático, y esa precedencia se resuelve en el backend
(`publicar._pt_resuelto`, `routers/productos._categoria_del_canal`), nunca en el
frontend — duplicarla sería una segunda verdad que se desincroniza.

**Los requisitos por categoría** viven en `channel.field_requirements`
(`canal, categoria_id, campo`) y los llena un cargador por canal que **lee de la
API del canal**, nunca de documentación:

```
amazon         64,125 filas / 553 tipos   scripts/cargar_requisitos_amazon.py
mercado_libre   2,765 filas / 1,059 cats  scripts/cargar_requisitos_ml.py
tiktok/temu/walmart/shein        0 filas  ← no existe el cargador
```

⚠️ **El cruce correcto es `channel.listings.product_type` ↔
`field_requirements.categoria_id`.** `listings.category_id` existe y guarda otra
cosa: unir por ahí devuelve cero filas **sin dar error**, y el semáforo diría
"sin requisitos" en todo el catálogo teniendo 64,125 cargados.

---

## ETAPA 4 — SE PUBLICA

| | Mercado Libre | Amazon |
|---|---|---|
| Quién arma el payload | `vendor/ml_ready` (INTOCABLE) vía `publicar_ready` | `vendor/amazon_ready` vía `publicar_ready.atributos_amazon` |
| Llamada | `POST/PUT /items` + `/items/{id}/description` **aparte** | `PUT /listings/2021-08-01/items/{seller}/{sku}` |
| Cuentas | **dos** (BEKURA y SANCORFASHION) | una |
| Reintentos | 3 por cuenta | hasta **4**, leyendo `issues` con `MISSING_ATTRIBUTE` |
| Validación previa | obligatorios en vivo de la categoría | JSON Schema del productType, cacheado en disco |
| Bitácora | `ml_backlog` + `ops.channel_submissions` | `amazon_backlog` + `ops.channel_submissions` |
| Estado por SKU | `ml_progress` (PK `cuenta:sku`) | `amazon_progress` (PK `sku`) |

**El contenido guardado se usa aquí**: `publicar._rellenar_desde_guardado` llena
los campos VACÍOS del formulario con lo que haya en `enrich.channel_content`, en
`preview` **y** en `confirmar` (si solo lo hiciera el envío, el modal enseñaría
una cosa y se publicaría otra). **El formulario manda**; lo guardado solo
rellena huecos.

**Regla de la casa 1**: `backend/vendor/` no se toca. Todo ajuste va en el
ADAPTADOR (`services/publicar_ready.py`, `services/publicar.py`).

---

## ETAPA 5 — SE OBSERVA (el estado por SKU)

Sin esta etapa el canal no existe para el panel: no sale en Productos, ni en
Omnicanal, ni en Análisis, y el fan-out no lo ve.

| Tabla | Qué guarda | Quién la escribe | Medido 13-ago |
|---|---|---|---|
| `channel.listings` | estado real por `(sku, cuenta, canal)`: precio, stock, `product_type`, `situacion` | sync de inventario cada 15 min | general 13,095 · ML 4,849 · amazon 1,790 · **tiktok 0** |
| `canal_inventario` (MySQL) | espejo del anterior | mismo sync | ML 4,580 · amazon 1,680 · **tiktok 0** |
| `ops.channel_submissions` | bitácora de intentos | los publicadores | ML 18,221 · amazon 4,358 · **tiktok 1,883** · temu 6 |

⚠️ **TikTok ya publicó 1,883 veces y tiene 0 filas de estado.** Enviar no es
observar: la bitácora dice "se mandó", `channel.listings` dice "esto es lo que
hay". El panel lee lo segundo.

---

## ETAPA 6 — SE OPERA (pedidos y stock)

### Pedidos que entran

| Canal | Mecanismo | Frecuencia |
|---|---|---|
| Mercado Libre | webhook `orders_v2` → `pedidos_ml` | segundos |
| Amazon | sondeo SP-API Orders | 5 min |
| Temu / TikTok (M2E) | sondeo `order/find` | 10 min (**apagado**) |
| Temu (propio) | webhook en **observación**: registra, no escribe | — |

Cada venta se congela como **pedido de WooCommerce** con el precio real,
comisión y neto. Tabla de control `pedidos_ml` (cubre todos los canales pese al
nombre) y `channel.orders`: ML 15,283 · amazon 102 · temu 2 · **tiktok 1**.

⚠️ **ML manda los webhooks en ráfaga** (la misma orden en milisegundos): el
`asyncio.Lock` por orden es lo único que evita duplicados — nacieron 164 el
17-jul antes de existir.

### Stock que sale (fan-out)

`services/fanout_stock.py`, vivo desde el 28-jul.

- **De dónde saca los destinos**: **`channel.listings`** (BD kubera), vía
  `channel_read`. **NO de `canal_inventario`** — se cambió el 12-ago, cuando
  congelar ese espejo unas horas dejó a la vista que una foto detenida hace que
  el fan-out le escriba a publicaciones cerradas creyéndolas vivas. *Se lee de
  donde se escribe.*
- **Quién sabe escribir**: `_ESCRITORES = {"mercado_libre", "amazon"}`. Un canal
  sin escritor se descarta y **se registra el descarte**, para que la pantalla
  explique por qué no recibió nada.
- Solo publicaciones **activas y no-FULL** (lo FULL/FBA sale del almacén del
  marketplace, no de la bodega).

---

## LA LISTA PARA ABRIR UN CANAL NUEVO

Nueve piezas. Las tres primeras son lectura y no rompen nada; de la cuarta en
adelante hay que ir con cuidado.

| # | Pieza | Dónde | Riesgo |
|---|---|---|---|
| 1 | `habilitado: True` y `origen` real | `backend/core/marketplaces.py` | ninguno |
| 2 | Cuenta en `core.accounts` + `core.channels.is_active` | BD kubera | ninguno |
| 3 | Estado por SKU en `channel.listings` (+ `canal_inventario`) | cargador propio | ninguno (escribe estado, no publica) |
| 4 | Árbol de categorías con `es_hoja` y disponibilidad | `channel.categories` | bajo |
| 5 | Requisitos por categoría | `channel.field_requirements` + su cargador | bajo |
| 6 | Validador de contenido | `services/<canal>_contenido.py` | bajo |
| 7 | Generador con IA + persistencia | `services/<canal>_ia.py` → `enrich.channel_content` | medio (gasta IA) |
| 8 | Enganche en el alta | `crear_producto._procesar`, tras el acta | **flujo vivo** |
| 9 | Entrada de pedidos + escritor de stock | webhook/sondeo + `_ESCRITORES` | **flujo vivo** |

---

## TIKTOK: QUÉ TIENE Y QUÉ LE FALTA (13-ago)

| # | Pieza | Estado |
|---|---|---|
| 1 | Canal en el panel | ❌ `habilitado: False`, origen `EJEMPLO`, leyenda *"pendiente de credenciales"* — **falso**: las credenciales funcionan |
| 2 | Cuenta y canal activo | ❌ nada en `core.accounts`; `core.channels.is_active = false` |
| 3 | Estado por SKU | ❌ **0 filas** en `channel.listings` con 1,883 envíos hechos |
| 4 | Árbol de categorías | ❌ en la BD no hay ninguna (`channel.categories` es 100% ML). En JSON del scratchpad sí: 2,168 categorías, 1,937 hojas, 451 `INVITE_ONLY` |
| 5 | Requisitos | ❌ **0 filas**. `tiktok_atributos.build_prompt` ya sabe leer `GET /product/202309/categories/{id}/attributes` y separa obligatorios por `is_requried` *[sic, el typo es de la API]* |
| 6 | Validador | ✅ `services/tiktok_contenido.py` (título **300** en MX, descripción HTML 10,000, suma de dimensiones 160) |
| 7 | Generador + persistencia | ❌ no existe. Lo que hay es `_MEJORAR["tiktok"]`: título y descripción, sin atributos, sin validador, sin guardar |
| 8 | Enganche en el alta | ❌ |
| 9 | Pedidos y stock | ❌ sin webhook propio; sin escritor en `_ESCRITORES` |

### Lo que necesito del chat de TikTok para hacer 1–5

Con esto abro el canal sin tocar su publicador:

1. **El censo de lo publicado**, una fila por SKU: `sku`, `product_id`, `status`
   (`ACTIVATE`/`DRAFT`/…), `category_id`, precio, stock, `warehouse_id` y la URL
   si la hay. Sirve para llenar `channel.listings` — es lo que hace aparecer el
   canal en Productos, Omnicanal, Análisis y el fan-out.
2. **El árbol de categorías** con `id`, `nombre`, `ruta`, `is_leaf` y si está
   disponible para nuestra tienda (los 451 `INVITE_ONLY` importan: publicar ahí
   es rechazo seguro).
3. **La llamada exacta de atributos por categoría** y un ejemplo de respuesta
   crudo, para escribir el cargador de requisitos: nombre del campo, si es
   obligatorio, tipo, y si sus valores son de lista cerrada (TikTok exige **ID de
   atributo Y de valor**, no texto).
4. **Cuáles atributos son `SALES_PROPERTY`** (Color, Talla): esos generan
   variantes y NO pueden ir como descriptivos.
5. **El prompt de contenido** que quieran para TikTok. Yo lo meto en
   `tiktok_ia.py` con el mismo contrato que Amazon: la IA propone, el validador
   de `tiktok_contenido.py` decide, y lo que pasa se guarda en
   `enrich.channel_content`.

⚠️ **Ojo con el título**: el prompt que hay hoy en el panel pide *"máx 45
caracteres"* y `tiktok_contenido.py` documenta que **MX admite 300**. Son 255
caracteres tirados en el campo que más pesa para que te encuentren.

---

## HALLAZGOS DE LA AUDITORÍA (13-ago-2026)

1. **Un producto recién creado no tiene `productType` de Amazon, así que su
   contenido se genera SIN los requisitos de la categoría.** Verificado: con un
   SKU que no está en `amazon_progress` ni tiene meta del panel, `_pt_resuelto`
   devuelve `None` → `requisitos: sin_requisitos` → la IA propone atributos
   genéricos (Material, Color, Cantidad…) en vez de los obligatorios del tipo.
   El título, los bullets, la descripción y los términos de búsqueda **sí** se
   generan bien. *Arreglo posible*: detectar el tipo en el alta con
   `publicar._detectar_product_type` (una llamada a Definitions por keywords) y
   pasárselo al generador. **Sin decidir.**
2. **Cada alta sigue naciendo con `BRAND = "Ferrahome"`** (`ml_atributos.MARCA`,
   `crear_producto.MARCA_FIJA`), lo que contradice la decisión del 13-ago de
   publicar todo con `Generic`. En Amazon ya se cumple; en ML no, y ML es
   justamente el canal que escribe esa meta en el alta. **Un cambio de dos
   líneas, pendiente de tu palabra** porque cambia lo que sale a un marketplace
   vivo.
3. **Los atributos de ML se generan con la categoría del PREDICTOR**, porque en
   el alta todavía no hay elección humana. Si después alguien cambia la
   categoría en el panel, los atributos quedan viejos y nada lo detecta (es el
   pendiente #7 de CLAUDE.md, ahora con nombre).
4. **`enrich.channel_content` tiene 3 filas y las tres son de Amazon.** El
   contenido de ML sigue viviendo solo en metas de Woo: ML está POR DETRÁS de
   Amazon en persistencia, no por delante. Al replicar, no se copia de ML.
5. **Corrección a lo que se venía diciendo**: `_destinos()` del fan-out lee
   `channel.listings`, **no** `canal_inventario` (cambió el 12-ago). La
   conclusión para un canal nuevo es la misma —sin filas ahí el fan-out no lo
   ve, y no da error— pero la tabla que hay que llenar es `channel.listings`.
