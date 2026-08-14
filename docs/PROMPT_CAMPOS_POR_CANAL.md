# ENCARGO: modelar en base de datos los campos que exige cada canal por SKU

Eres arquitecto de datos del proyecto **OMNICANAL de Kubera**. Tu trabajo es
diseñar las tablas que permitan responder, para cualquier SKU: **¿qué le falta
para poder publicarse en cada canal?** — y mostrarlo en el panel.

**No implementes nada todavía.** Entrega propuesta + DDL. Solo lectura sobre
cualquier base; la BD kubera (`tukwcvsi…`) es producción operativa.

---

## La regla de nombres que manda

**Un concepto, un nombre, en todos los canales.** Cada API lo llama distinto y
eso NO debe filtrarse al modelo:

| Nombre canónico | Cómo lo llama cada canal |
|---|---|
| `precio` | ML `price` · Amazon `purchasable_offer` · Walmart `price` · TikTok `sale_price` · Temu `basePrice.amount` |
| `stock` | ML `available_quantity` · Amazon `fulfillment_availability` · Walmart `qty` · TikTok `inventory[].quantity` · Temu `skuList[].quantity` |
| `titulo` | ML `title` · Amazon `item_name` · Walmart `productName` · TikTok `title` · Temu `goodsName` |
| `sku` | ML `seller_custom_field` · Amazon `sku` · Walmart `sku` · TikTok `seller_sku` · Temu `externalSkuId` |
| `categoria_id` | ML `category_id` · Amazon `productType` · Walmart `subCategory` · TikTok `category_id` · Temu `catId` |

El modelo guarda `precio`; el **mapeo al nombre real vive en el publicador**, no
en la tabla. Si mañana Temu renombra `basePrice`, se toca un adaptador y no una
columna.

---

## LOS CINCO REQUERIDOS COMUNES

Estos cinco los pide **todo canal**. Son el mínimo para que un SKU sea
publicable en cualquier lado:

1. **`titulo`**
2. **`descripcion`**
3. **`precio`**
4. **`dimensiones`** (largo, ancho, alto, peso)
5. **`atributos`** (los obligatorios de su categoría)

Más `sku`, `stock`, `imagenes` y `categoria_id`, que son llave o inventario.

---

# CANAL POR CANAL

## 1. MERCADO LIBRE (2 cuentas: BEKURA · SANCORFASHION)

### Requeridos
| Campo | Notas |
|---|---|
| `titulo` | máx 60 caracteres |
| `descripcion` | va en llamada aparte (`/items/{id}/description`) |
| `precio` | decimal. **Debe ser el REGULAR, no el de oferta** |
| `dimensiones` | para el cálculo de envío |
| `atributos` | de `/categories/{id}/attributes` con `tags.required` |
| `sku` | |
| `stock` | |
| `imagenes` | |
| `categoria_id` | `MLM…`. **La elección del panel MANDA** sobre el predictor |

### Extras de Mercado Libre
| Campo | Por qué existe |
|---|---|
| `listing_type_id` | `gold_pro` (Premium). Todo el catálogo es Premium desde jul-2026 |
| `sale_terms` | garantía y su tipo. Lista propia por categoría |
| `condicion` | nuevo / usado |
| `video_id` | opcional |
| `guia_de_tallas` | **bloqueante en ROPA y CALZADO**: sin `chart_id` no publica |
| `variaciones` | color/talla como publicaciones o variantes |
| `cuenta` | ⚠️ el MISMO SKU puede ser DOS productos distintos según la cuenta (caso `EST-0091`) |

---

## 2. AMAZON (San Corpe)

### Requeridos
| Campo | Notas |
|---|---|
| `titulo` | `item_name`. **Sin acentos** |
| `descripcion` | `product_description` |
| `precio` | dentro de `purchasable_offer`, estructura anidada |
| `dimensiones` | `item_dimensions` + `item_package_weight` |
| `atributos` | del JSON Schema de `productTypes/{tipo}`, que se cachea en disco |
| `sku` | es la llave de la URL: `/listings/2021-08-01/items/{seller}/{sku}` |
| `stock` | `fulfillment_availability` |
| `imagenes` | **por URL pública, mínimo 1000px** |
| `categoria_id` | `productType`. Panel > histórico > detección por título |

### Extras de Amazon
| Campo | Por qué existe |
|---|---|
| `bullet_points` | hasta 5 viñetas. Es el campo de conversión más importante |
| `item_highlights` | destacados |
| `backend_search_terms` | palabras clave invisibles al comprador |
| `brand` / `manufacturer` | |
| `condition_type` | |
| `canal_logistico` | **AFN (FBA) o MFN.** Decide si descuenta bodega propia |
| `parent_asin` / `child_asin` | variantes: forma padre/hijo, distinta a la de ML |

---

## 3. WALMART MÉXICO

### Requeridos
| Campo | Notas |
|---|---|
| `titulo` | `productName` |
| `descripcion` | `shortDescription` |
| `precio` | `price` |
| `dimensiones` | ⚠️ **máximo 2 decimales**: más decimales = rechazo del feed |
| `atributos` | bloque `Visible`, con **la etiqueta de la categoría EN ESPAÑOL como llave** |
| `sku` | |
| `stock` | `qty` |
| `imagenes` | **mínimo 2**, JPEG real, ≥1000px |
| `categoria_id` | `subCategory` — y va en el ENCABEZADO del feed, no en el artículo |

### Extras de Walmart
| Campo | Por qué existe |
|---|---|
| `clave_sat` | **obligatoria en México.** Código del catálogo del SAT |
| `productIdType` + `productId` | UPC/GTIN real, **o `CUSTOM` con folio de exención** |
| `folio_exencion` | ⚠️ **por CATEGORÍA**, no por producto. Cada categoría nueva = ticket propio |
| `brand` | |
| `shippingWeight` | Walmart cobra **volumétrico** |
| `feed_subcategoria` | ⚠️ **un artículo por VARIANTE** (forma plana, al revés que TikTok y Temu) |

**Regla de la casa (costó feeds rechazados):** un SKU, una categoría, un feed a
la vez. El mismo SKU en dos feeds vivos da `EXT_DATA_ERROR`.

---

## 4. TIKTOK SHOP MX

### Requeridos
| Campo | Notas |
|---|---|
| `titulo` | `title` — **MX admite [1, 300] caracteres** (otras regiones 255) |
| `descripcion` | `description`, HTML, **máx 10,000 caracteres**, máx 30 `<img>` |
| `precio` | `sale_price`, string decimal |
| `dimensiones` | `package_dimension`. ⚠️ **la suma L+A+H debe ser ≤ 160 cm** |
| `peso` | `package_weight`. ⚠️ **0 kg es inválido** y tumba la publicación |
| `atributos` | ⚠️ ver la corrección de abajo — **NO son cero** |
| `sku` | `seller_sku` |
| `stock` | `inventory[].quantity`. ⚠️ rango **[1, 99999]**: el 0 no es válido |
| `imagenes` | por `uri`: se suben primero y TikTok las rehospeda |
| `categoria_id` | hoja del árbol de 1,937. **416 no están `AVAILABLE`** |

### 🔴 CORRECCIÓN (12-ago): los atributos obligatorios SÍ existen

Este documento decía "CERO obligatorios en todas las categorías medidas". **Es
falso, y la medición estaba mal hecha**: se leía la llave `is_required`, y
TikTok la escribe **`is_requried`** — con su propia errata. Con la grafía buena:

- **107 de 219** categorías medidas piden atributos obligatorios
- afectan al **51% de los SKUs**

Y no se ven al crear: **`AS_DRAFT` no los valida, `LISTING` sí.** Un lote entero
puede parecer perfecto en borrador y rebotar completo al ponerlo a la venta.

Los más frecuentes en este catálogo: *Tipo de garantía*, *Productos importados*,
*Nombre y Dirección de Fabricante Nacional/Importador*, *Consumo de energía*.
Los dos del importador **no los puede contestar una IA**: son datos legales de
la empresa y deben venir de una constante de configuración, no del modelo.

⚠️ Tampoco los predice `/categories/{id}/rules`: en MX devuelve
`manufacturer.is_required` vacío. **La fuente buena es la lista de atributos de
la categoría.**

### Extras de TikTok
| Campo | Por qué existe |
|---|---|
| `warehouse_id` | ⚠️ hay DOS almacenes: usar **SALES**, no el de devoluciones |
| `brand_id` | |
| `save_mode` | `AS_DRAFT` o `LISTING`. Los 249 actuales están en borrador |
| `SALES_PROPERTY` | ⚠️ atributos como Color NO son descriptivos: **generan variantes** |
| `certificaciones` | garantía, manual — opcionales |
| `guia_de_tallas` | bloqueante en ropa/calzado, igual que ML |

---

## 5. TEMU MX

### Requeridos
| Campo | Notas |
|---|---|
| `titulo` | `goodsBasic.goodsName` |
| `descripcion` | `goodsBasic.goodsDesc` |
| `precio` | `skuList[].price.basePrice.amount` — **string, en CENTAVOS** |
| `dimensiones` | `productExpressInfo`: `weightInfo` (g) + `volumeInfo` (cm) |
| `atributos` | `goodsProperties[]` con `pid` + `vid` de lista cerrada |
| `sku` | `externalSkuId` — es el que vuelve como `extCode` en los pedidos |
| `stock` | `skuList[].quantity`, **en el MISMO alta** (no hay segunda llamada) |
| `imagenes` | por URL; Temu descarga y rehospeda **conservando el tamaño recibido** |
| `categoria_id` | `catId`, hoja. La plantilla solo responde en hojas |

### Extras de Temu
| Campo | Por qué existe |
|---|---|
| `costTemplateId` | plantilla de flete. **Obligatoria de facto** |
| `shipmentLimitDay` | días para despachar |
| `fulfillmentType` | |
| `goodsOriginInfo` | país de origen |
| `listPrice` | precio tachado |

**⚠️ Sin resolver, y es dinero:** `basePrice` parece ser el precio de
**suministro** (lo que Temu paga), no el de venta — un canario publicado con él
salió con `retailPrice: 0`. Existe toda la familia `priceorder.*` de
negociación. **El modelo debe permitir DOS precios por canal** (el que
proponemos y el que el canal fija), no uno solo.

---

---

# LAS CATEGORÍAS — el campo más traicionero de todos

**Nunca es el mismo tipo de dato dos veces**, y equivocarse NO da error: el
producto queda vivo y mal clasificado. Es la lección de `TEC-1812-NEG`,
publicado en *"Máquinas de Coser"* siendo una máquina sexual.

| Canal | Qué es el id | Universo | Cómo se obtiene |
|---|---|---|---|
| **Mercado Libre** | `MLM162997` (string) | árbol completo | predictor `/category_predictor` o el árbol `/categories` |
| **Amazon** | `productType`: `HOME`, `SHOES` (string) | **no es árbol**, es lista plana de tipos | `/definitions/2020-09-01/productTypes` |
| **Walmart** | `subCategory`: `costumes`, `home_other` (token) | **75 subcategorías** | hardcodeadas; van en el ENCABEZADO del feed |
| **TikTok** | id numérico de hoja | 2,168 totales · **1,937 hojas** | recomendador o el árbol completo |
| **Temu** | `catId` numérico de hoja | 25 raíces, árbol por `parentCatId` | recomendador o descenso por el árbol |

### Lo que hay que guardar por categoría, y por qué

**`id` Y `nombre`, siempre los dos** (petición explícita de Brandon): el `id` es
lo que la API exige, el `nombre` es lo único que un humano puede revisar. Un id
suelto en una tabla no se puede auditar.

Además, por canal:

| Dato | Para qué |
|---|---|
| `ruta_completa` | *"Cocina → Hervidores"* desambigua muchísimo mejor que *"Hervidores"*. Y en ML ya existe `root_id`/`root_name` |
| `es_hoja` | ⚠️ **solo en hojas se publica.** TikTok y Temu rechazan las intermedias, y la plantilla de Temu responde *"The catId not a leaf category"* |
| `disponible` | ⚠️ **416 de las 1,937 hojas de TikTok NO están `AVAILABLE`** para nuestra tienda. Publicar ahí es rechazo seguro |
| `origen_de_la_elección` | recomendador del canal · IA · **elección humana del panel** |
| `aproximada` | marca las que la IA eligió como "la más próxima" y conviene revisar |

### La regla que manda sobre todo lo demás

**La elección del PANEL gana a cualquier detector automático.** En ML eso ya es
la meta `ml_categoria_id` (picker humano) por encima de `ml_category_id`
(predictor); en Amazon, `amz_product_type` por encima del histórico y de la
detección por título. **El modelo debe representar esa precedencia
explícitamente**, no dejarla implícita en el código del publicador.

### Trampas medidas que el diseño debe absorber

- **El recomendador de TikTok falla el 49%** (125 aciertos de 245). El respaldo
  de IA no es un lujo: es la mitad del catálogo.
- **`channel.categories.path` usa DOS separadores distintos**: `›` (U+203A) en
  2,612 filas y `>` en 2. **No lo parsees** — usa `root_id`/`root_name`.
- **Walmart mete la categoría en el encabezado del feed**, no en el artículo, y
  la etiqueta española de esa categoría es además **la llave del bloque
  `Visible`**. Un solo feed, una sola subcategoría.
- **Los atributos obligatorios cuelgan de la categoría**, así que cambiar de
  categoría invalida los atributos ya llenados. El modelo debe poder detectarlo.

---

# LAS IMÁGENES — cada canal las recibe distinto

`imagenes` es un requerido común, pero **la forma de entregarlas cambia por
canal** y eso decide qué guarda la base: unos necesitan una URL pública viva,
otros un identificador que el canal devolvió.

| Canal | Cómo se envían | Qué guarda la BD |
|---|---|---|
| **Mercado Libre** | Por **URL**. ML descarga y rehospeda | la URL de origen |
| **Amazon** | Por **URL pública**, mínimo 1000px. Amazon la descarga | la URL, ya convertida a JPEG ≥1000px |
| **Walmart** | Por **URL**, mínimo 2 imágenes, JPEG real verificado por contenido | la URL + esperar **120 s** de propagación antes del feed |
| **TikTok** | **Se sube el binario** (`multipart`) y devuelve un `uri` — eso va en el producto | el `uri` que devolvió TikTok |
| **Temu** | Por **URL** a `image.v2.upload`; devuelve una URL `kwcdn` que va en el producto | la URL `kwcdn` devuelta |

### Las dos trampas medidas

**1. chunche.shop entrega WEBP disfrazado de `.jpg`.** Sirve la imagen según la
cabecera `Accept` del cliente, y los marketplaces no piden JPEG. Además parte
del catálogo son `.webp` de verdad (así vinieron de Alibaba). **Toda entrega de
imagen pasa por el conversor** (`imagenes_amazon._descargar` + `_a_jpeg`), que
hace Lanczos a 1000 px y sortea el WAF de Hostinger.

**2. El canal conserva el tamaño que recibe.** Medido en Temu: con la URL cruda
la foto queda en **800×800**; con la nuestra convertida, en **1000×1000**. El
conversor no es opcional aunque el canal acepte WEBP.

Consecuencia para el modelo: guardar **la URL de origen** (la de Woo) y, aparte,
**el identificador que cada canal devolvió** — porque el `uri` de TikTok y la
URL `kwcdn` de Temu no se pueden recalcular, solo recuperar.

---

## LO QUE DEBE RESOLVER EL MODELO

1. **Un SKU no tiene UN estado, tiene uno POR CANAL Y CUENTA.** `EST-0091` es
   dos productos distintos según la cuenta de ML. La PK mínima es
   `(sku, canal, cuenta)`.
2. **Los atributos obligatorios dependen de la CATEGORÍA, que depende del
   CANAL.** No son columnas: son filas o `jsonb`.
3. **Las categorías se guardan como `id` Y como `nombre`** (pedido explícito de
   Brandon): el id es lo que la API exige, el nombre es lo que el humano lee.
4. **Distinguir tres cosas que se confunden:** lo que el canal EXIGE (plantilla,
   cambia sola), lo que NOSOTROS tenemos (dato del SKU) y lo que YA SE MANDÓ
   (bitácora de intentos, `ops.channel_submissions`).
5. **La pregunta que el panel debe contestar en una consulta:** *"para el SKU X
   y el canal Y, ¿qué campos faltan?"* — eso es lo que se pinta en Productos.

## QUÉ ENTREGAR

1. Propuesta comparada (tabla relacional por canal · una genérica · `jsonb`) con
   **recomendación argumentada**
2. DDL concreto: tipos, PK, FKs verificadas, índices, RLS
3. Cómo se llena desde lo que YA existe (metas de Woo, `ml_progress`,
   `amazon_progress`, los JSON del scratchpad)
4. La consulta que responde "¿qué le falta a este SKU?"
5. Qué se deprecaría y en qué orden

⚠️ Antes de diseñar: `git pull --rebase` y `ls supabase/migrations/`. Ya existe
`enrich.ai_content` con PK `(sku, canal, cuenta)` y 0 filas — **verifica si la
tabla que ibas a crear ya está hecha.**
