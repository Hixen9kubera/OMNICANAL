Everything is verified. Here is the guide.

---

# ⚠️ CORRECCIÓN DEL 12-AGO (tarde) — EL PRECIO, RESUELTO

> Lo que sigue **deroga** lo que este manual dice sobre el precio en §4 y en el
> anexo A.1–A.3. Medido contra la API y contra la doc oficial del portal
> (`partner.temu.com/documentation`, secciones *Price* y *Add Products > V3*).

## El precio nunca estuvo "ignorado sin dar error"

`basePrice` es **obligatorio y validado campo por campo** por
`temu.local.goods.v3.add`. Comprobado en vivo, un caso por error:

| Lo que se manda | Respuesta |
|---|---|
| `amount "164.021"` | `150011018` MXN admite 2 decimales |
| `currency "XXX"` | `150011003` Invalid Request Parameters **[currencyCode]** |
| `amount "0.01"` | `150010030` Price input error (el mínimo es 0.02) |
| `amount "-5.00"` | `150010002` |
| sin objeto `price` | `150011003` Invalid Request Parameters **[basePrice]** |

Un campo que se parsea, se valida por rango y se exige **no es un campo que se
esté tirando**. Había dos errores encimados:

**1. El cuerpo tenía forma de v1 contra un endpoint v3.** v3 **ignora en
silencio** toda llave que no conoce. Medido con JUGU-1158-VER: se pidió
`catId` 1761 → Temu publicó en **1769** (usó su recomendador); se mandó
500 g / 20×20×20 en `productExpressInfo` → Temu guardó su default,
**100 g / 10×20×30**; `goodsProperties` con pid/vid → descartado entero.
El esquema REAL de v3 es otro: `extCatName` (no `catId`), `attributes`
`{name, value[]}` (no pid/vid), `packageInfo` (no `productExpressInfo`),
`variations`, y `costTemplate` dentro de `goodsBasic`.

**2. Se estaba leyendo el campo equivocado.** `retailPrice` **no es lo que
mandamos**: es el precio de **anaquel** que calcula Temu (≈ `basePrice ×
1.1325` en los 146 productos vivos con precio) y **solo existe una vez que el
producto estuvo a la venta**. Leerlo en un producto recién creado siempre da 0.

> **La contraevidencia de los 152 de M2E queda explicada.** Los 28 que están
> "no publicados" y **sí** traen precio se crearon el 6-7 de ago y cambiaron de
> estado el 10-12: **estuvieron a la venta y bajaron**; su precio es residuo de
> cuando estaban vivos. Los nuestros tienen `crtTime == goodsStatusChangeTime`
> (0.00 días): nunca se publicaron. **No existe un solo producto en la tienda
> que haya nacido con precio sin publicarse.**

## ✅ CONFIRMADO A OJO EN EL SELLER CENTER (12-ago)

Productos → Administrar productos → pestaña **"Incompleto"**, columna
**"Precio base"**. Es la única superficie donde se ve el precio que mandamos;
la API no lo devuelve. Lo que muestra:

| SKU | Precio base en el panel | Lo que mandó el publicador |
|---|---|---|
| **JUGU-0067-MUL** (payload nuevo) | **MX$255.63** ✅ | `"255.63"` decimal |
| KBTEST-0812A / KBPROBE-6 / KBPROBE-9 | MX$164.02 ✅ | `"164.02"` decimal |
| **JUGU-1158-VER** (payload viejo) | **MX$16,402.00** ☠️ | `"16402"` en "centavos" |

**El decimal entra exacto. Y el canario viejo es la prueba del otro lado: la
receta de "centavos" del manual publica a 100× el precio.** De haber lanzado
los 234 con ella, todo el catálogo quedaba multiplicado por cien.

Los tres estados vistos: el precio arranca **"En evaluación"** y por eso el
producto vive en la pestaña **"Incompleto"** (= `goodsSearchType 4`, donde
también están los 28 de M2E). No es un fallo del alta: es la sala de espera
mientras Temu evalúa el precio base. `retailPrice` de la API sigue en 0 durante
todo ese tramo.

## Cómo entra el precio, entonces

```jsonc
"skuList": [{ "price": { "basePrice": {"amount": "255.63", "currency": "MXN"} } }]
```

- **DECIMAL de 2 cifras, NO centavos.** La tabla de monedas de la doc fija
  MX = MXN, 2 decimales, base `[0.02, 99999999.99]`, peso en **g** y medidas en
  **cm** (1 decimal). Lo de "centavos" del manual venía de la LECTURA.
- **Se escribe decimal y se lee en centavos**: mandamos `"213.23"` y
  `detail.query` devuelve `"21323"`. Verificado.
- `basePrice` **es nuestro neto**, no lo que paga el cliente. La doc: *"this
  amount information will be used to calculate the settlement amount with the
  merchant"*. Temu le suma su margen para armar el anaquel.
- **`listPrice` es opcional y tiene trampa**: debe ser **estrictamente mayor**
  que `basePrice`. Si va igual, **se tira en silencio** (JUGU-0067-MUL, base
  255.63 = list 255.63 → se leyó 0.00; contra KBTEST, base 164.02 / list 213.23
  → se leyó 213.23). El publicador **lo omite**: mandarlo obligaría a inventar
  un precio tachado.

## Lo que sigue bloqueado, y es trámite de Brandon

`bg.local.goods.sku.list.price.query` — **el único endpoint que lee `basePrice`
de vuelta** — responde `3000032`: *"ask for seller to authorize this api in
seller center first, and share the new access token"*. Es la misma familia que
el bloqueador #1 (importes de pedidos). **Hasta que se autorice, se puede
verificar todo de un producto menos el precio de liquidación.**

## Otras trampas medidas hoy

- **`temu.local.goods.delete` miente en la capa de afuera**: devuelve
  `success:true / errorCode 1000000` y el veredicto real va **anidado** en
  `result.success` — que puede ser `false` con
  *"Delete failed, data under review cannot be deleted"*. Leer siempre
  `result.success`, nunca el de afuera.
- **`out.sn.check` rechaza repetidos dentro de la MISMA llamada** con
  `150010003`. Deduplicar el lote antes de mandarlo.
- **`temu.local.goods.baseprice.recommend` sirve y es solo lectura**, pero
  **no mira peso ni volumen**: devuelve lo mismo con 100 g que con 40×40×40 cm.
  Para JUGU-1158-VER propone **87.25** sobre un precio Woo de 164.02 (**0.53×**)
  — bajar el neto a la mitad es decisión de negocio, no del publicador.
- **Plantillas de flete: solo existe UNA**, `LFT-18510029444014331627`, llamada
  **"test"**. `bg.freight.template.list.query` no devuelve ninguna otra: crear
  una de verdad es tarea de Seller Center (Brandon), no hay alternativa por API.
- **Peso/volumen de los 235**: 49 con densidad imposible (<0.05 o >2 g/cm³),
  5 cubos perfectos (la huella del CBM), y **88 de 232 facturarían más por
  volumen que por peso** (mediana 0.69×, p90 **10.93×**, máx 1600×).

---

# GUÍA DE IMPLEMENTACIÓN — TEMU (mallId 635517742093915, regionId 128)
Medido en vivo el 12-ago-2026 contra la API y la BD de Woo. Todo lo que no verifiqué va marcado.

---

## 1. EL WEBHOOK, PASO A PASO

**🟢 CAMBIÓ HOY, A TU FAVOR:** dos horas atrás `appSubscribeEventCodeList` estaba **vacío**; ahora trae los 5 códigos de evento. Alguien ya creó el webhook en la consola. Falta la mitad de abajo del trámite (autorización del vendedor), no la de arriba.

**Estado vivo, ahora mismo** (`bg.open.accesstoken.info.get`) — estos 3 campos son tu termómetro, no adivines:

| Campo | Valor hoy | Qué significa |
|---|---|---|
| `appSubscribeStatus` | **0** | La suscripción NO está activa todavía |
| `appSubscribeEventCodeList` | los 5 códigos | La APP ya declaró los eventos ✅ |
| `authEventCodeList` | **[]** | El VENDEDOR (tú) no ha autorizado ninguno ❌ |
| `expiredTime` | 1818017977 | Token vigente |
| scopes | 129, incluye `bg.tmc.message.update` | ✅ |

### Qué escribir en cada campo

| Campo de la consola | Qué poner |
|---|---|
| **App** | La app nuestra, la del `app_key` que ya usamos. Es un **selector**, no texto libre. |
| **Push website** | `https://backendomnicanal-production.up.railway.app/api/webhooks/temu` |
| **Eventos** | `Order status change event` (la venta) + `Aftersales status change event` (devoluciones → regresar stock). Opcional: `trade logistics address changed`. **NO** el de *Supply chain / cooperative warehouse*: ese es para ERP con almacén de Temu, no aplica. |

### Por qué "Sancorpe" falló
Porque **no es una URL**. Ese campo pide la dirección HTTPS completa a la que Temu va a hacer POST; "Sancorpe" es el nombre de la cuenta. La doc lo exige textualmente: la URL de callback debe usar protocolo HTTPS. Había además un **segundo motivo** que ya está resuelto: hasta ayer `POST /api/webhooks/temu` devolvía **404** — la ruta estaba abierta en el middleware pero el endpoint no existía. Si la consola prueba la URL antes de guardar, un 404 también se lee como "invalid".

### La URL ya existe y responde — verificado hace minutos
El receptor entró en **v0.104.0**, está en `main` y desplegado. Probado en vivo contra producción:

```
GET  /api/webhooks/temu → 200 {"ok":true,"canal":"temu","modo":"observacion","eventos_en_memoria":0}
POST /api/webhooks/temu → 200 {"success":true,"code":0,"message":"success"}
```

**Regla general que aplica a todos los canales:** la URL debe EXISTIR y responder **antes** de dar de alta la suscripción. No al revés.

### ¿Hay handshake de verificación?
**No.** No hay challenge/echo como Meta ni firma de prueba como Stripe. Lo que sí hay:
1. Validación de **formato** de URL al guardar.
2. Un botón **"Test"** en la consola que dispara un mensaje de prueba (úsalo: caerá en el log).
3. **Aprobación MANUAL de Temu, hasta 1 semana**, por cada alta *y por cada cambio de la Push website*. El estado se ve en el Operation Log. → **Mándalo a aprobar hoy** con la URL definitiva, para que el trámite corra en paralelo a la publicación de los 300.

### Los 2 pasos que la gente olvida (sin ellos NO llega nada)
4. **El vendedor autoriza los eventos en Seller Center.** Sale un `access_token` NUEVO. Se nota porque `authEventCodeList` deja de estar vacío.
5. **Llamar `bg.tmc.message.update`** con `permitEventCodeList=['bg_order_status_change_event','bg_aftersales_status_change']`. Es una escritura (no la hice, estaba prohibido en el sondeo). Errores propios: `110020008` = falta el paso 4; `110020009` = la app no tiene ese evento aprobado; `110020007` = más de 1 req/seg.

### Lo que el evento trae (y lo que NO)
`bg_order_status_change_event` = **solo** `mallId`, `parentOrderSn`, `orderSn`, `orderStatus`, `updateTime`. **Sin precio, sin SKU, sin cantidad.** Es un aviso, no un documento: hay que llamar después a `bg.order.detail.v2.get` (de ahí sale `productList[].extCode` = **nuestro SKU de Woo**).

**🔴 BLOQUEADOR DEL PRECIO:** `bg.order.amount.query` y `temu.order.amount.v2.query` son *sensitive APIs* y hoy devuelven `errorCode 3000032` ("access_token don't have this api access"). Probado en vivo contra un pedido real. **Sin ese permiso el pedido de Temu no se puede congelar con su precio real** como hace `pedidos_ml`. Hay que pedirlo a Temu Support (~1 día hábil) + autorizarlo en Seller Center. **Es un trámite tuyo, no lo resuelve el código.**

### Contrato técnico (ya resuelto y reproducido en local — para quien programe)
- **Responder 200 en menos de 500 ms**, o cuenta como entrega fallida. Reintentos: 2m, 10m, 30m, 1h, 1h, 1h, 12h, 12h → y abandona el mensaje. (Presupuesto 6× más estricto que ML: todo el trabajo va a `BackgroundTasks`.)
- **Cuerpo cifrado**: `{"eventData": "<base64>"}` → **AES-128-CBC / PKCS5**, clave = IV = **los primeros 16 bytes del app_secret**. Descifrar PRIMERO, verificar firma DESPUÉS.
- **Firma**: HMAC-SHA256 (llave = app_secret) en la cabecera `x-tm-signature`, hex minúscula. Cadena base = los 4 `x-tm-*` **más el `eventData` ya descifrado**, ordenados alfabéticamente y concatenados **clave+valor SIN separadores** (ni `=` ni `&`).
- **⚠️ TRAMPA GRAVE:** el código Java de ejemplo *de la propia doc de Temu* firma con formato `key=value&` y **no reproduce ninguno de sus propios ejemplos**. Copiarlo = rechazar el 100% de los eventos legítimos. (Es exactamente el error que ya casi nos cuesta el canal en TikTok.) Reproduje los 2 ejemplos oficiales con la fórmula concatenada; un ejemplo incluye `x-tm-ext-param` y el otro no → **calcular ambas variantes y aceptar cualquiera**.
- El receptor actual **NO verifica firma ni descifra todavía**, a propósito: fase de observación, guarda absoluta, 200 siempre. Se activa cuando llegue el primer evento real y confirme el esquema.

---

## 2. QUÉ HAY PUBLICADO HOY EN TEMU

**152 productos únicos.** Los 152 traen `outGoodsSn` = nuestro SKU (los publicó M2E, con el esquema v1).

**Parámetros que sirvieron** (dos errores silenciosos costaron el censo):

```json
{"type":"bg.local.goods.list.query","goodsSearchType":1,"pageNo":1,"pageSize":100}
```

| Detalle | Valor |
|---|---|
| `goodsSearchType` | **ENTERO nativo, obligatorio.** Como string da `3000000`. Su ausencia era el `7000000` que nos frenó antes. |
| Paginación | **`pageNo`**, NO `page`. `page` se acepta **en silencio** y devuelve siempre la página 1 — ese era el bug que dejaba el censo en 100 de 124 sin dar error. |
| `pageSize` | máx **100** (200 → error `1000000`). |
| Cubetas | **1** (124 productos) + **4** (28). Disjuntas: hay que recorrer las dos y unir. |

**Composición:**

| Estado (`publish`) | Cuántos | Cubeta |
|---|---|---|
| status 1 / sub 101 | 108 | 1 |
| status 1 / sub 102 | 5 | 1 |
| status 2 / sub 201·202·203 | 2 + 6 + 3 | 1 |
| status 3 / sub 301 | 28 | 4 |

*El significado exacto de cada código es **NO VERIFICADO** — no hay doc; la correlación cubeta 4 ⇔ status 3 sí está verificada al 100%.*

- **Stock declarado:** 47,910 unidades; solo 5 productos en cero.
- **Precios:** mediana $484.15, máx $23,245.32, y **uno en $0.00** (revisar).
- **Todos comparten el mismo freight template:** `LFT-18510029444014331627`, **llamado "test"**. Revísalo antes de meter 282 productos más colgando de él.

**Cruce con los 299 SKUs candidatos** (verificado con un oráculo independiente, `bg.local.goods.out.sn.check`): **17 ya existen, 282 por publicar.** Cero productos fuera del censo.

Los 17 que ya están: `ACC-0266-ROJ`, `ACC-0267-ROS`, `MASC-0057-ROJ`, `MES-0039-NEG`, `MUE-0214-GRI`, `OFI-0093-NEG`, `OFI-0114-VER-MIL`, `ORG-0418-GRI`, `TEC-0608-BLN`, `TEC-0616-MUL`, `TEC-0783-NEG`, `TEC-0786-NEG`, `TEC-1032-NEG-SOL`, `TEC-1038-NEG`, `TEC-1039-NEG-PLEG`, `TEC-1291-MUL`, `TEC-1351-NEG`.

> `out.sn.check` (lotes de 50) es la herramienta que hace **idempotente** el alta de los 282: devuelve `isDuplicate` + `duplicateGoodsId`. Correrlo antes de cada tanda.

Datos: `scratchpad/tm_censo_productos.json` · `tmc_cruce_300.json` · cliente `tm_api.py`.

---

## 3. CAMPOS OBLIGATORIOS POR CATEGORÍA

`bg.local.goods.template.get` con `{"catId":"<hoja>"}` — **solo funciona en hojas** ("The catId not a leaf category"). **Acepta `language=es`**: devuelve categorías, atributos **y valores** ya en español. Eso ahorra una capa entera de traducción.

| Hoja (catId) | Atributos | Obligatorios | **DUROS** | Condicionales | Lista cerrada | Texto libre |
|---|---|---|---|---|---|---|
| 15479 Handsaws | 18 | 5 | **2** | 3 | 17 | 1 |
| 15310 Combo Kits | 13 | 6 | **2** | 4 | 12 | 1 |
| 15404 Glue Guns | 12 | 6 | **2** | 4 | 11 | 1 |
| 14537 Levels | 13 | 3 | **1** | 2 | 12 | 1 |
| 39118 Cutting Boards | 9 | 1 | **0** | 1 | 9 | 0 |
| 9923 Kitchen Tool Sets | 18 | 5 | **1** | 4 | 17 | 1 |
| 10099 Cookware Sets | 6 | 2 | **1** | 1 | 6 | 0 |
| 10975 Water Bottles | 20 | 7 | **3** | 4 | 19 | 1 |
| 32230 Yoga Mats | 6 | 0 | **0** | 0 | 6 | 0 |
| 32233 Yoga Starter Sets | 8 | 1 | **1** | 0 | 8 | 0 |
| 31765 Treadmills | 14 | 5 | **3** | 2 | 14 | 0 |
| 31766 Leg Exercisers | 13 | 3 | **2** | 1 | 13 | 0 |
| 4665 Earbud Headphones | 35 | 6 | **2** | 4 | 32 | 3 |
| 4667 Over-Ear Headphones | 29 | 6 | **2** | 4 | 26 | 3 |
| 3254 Video Projectors | 24 | 6 | **4** | 2 | 19 | 5 |
| 4671 Adapters | 11 | 5 | **3** | 2 | 11 | 0 |

**La letra chica que cambia todo:** de los "obligatorios", la mayoría son **CONDICIONALES** (`showType=1`): solo se activan si el atributo padre tomó cierto valor. Los **DUROS** (`showType=0`, siempre visibles) son **0 a 4 por hoja**, y son casi siempre los mismos tres conceptos: **Material**, **Power Supply / Power Mode**, **Battery Properties**.

**Y las tres listas duras traen salida de emergencia:** `Use Without Electricity` / `Without electricity` / `Without Battery`. Contestando eso **no se dispara ningún hijo condicional** (Plug Type, Operating Voltage, Battery Capacity quedan fuera). Traducción: **un producto no eléctrico se cierra con Material + dos "no"**. El catálogo Kubera es casi todo no-eléctrico.

**Variaciones (color/talla):** `goodsSpecProperties` vino **vacío en 15 de 16 hojas**. Única excepción medida: Yoga Mats exige `Thickness`. Casi nunca son obligatorias.

**Marca:** el atributo Brand aparece **opcional** en todas las hojas y su lista trae **un solo valor: "PICOOL"**. Y los 6 productos vivos que inspeccioné tienen `goodsTrademark` **todo en null** → en la práctica **la marca no es obligatoria**.

### ¿Cuánto más trabajo es Temu vs TikTok?

| | TikTok | Temu |
|---|---|---|
| Atributos por categoría | 8 | 6 a 35 |
| Obligatorios | **cero, en todas** | 0–7 (12 de 16 hojas tienen ≥1) |
| Duros de verdad | — | **0–4** |
| Valores | `is_customizable=true` → se podía inventar texto | **vid de lista cerrada**, no se inventa |

**Veredicto: más exigente, pero mucho menos de lo que asusta.** No hace falta un prompt por categoría. ~90% de los SKUs se resuelve con reglas deterministas (Material + "sin electricidad" + "sin batería"); la IA queda para la cola de Electrónica y para los numéricos con unidad (brillo ANSI, mAh, W).

---

## 4. EL PAYLOAD DE PUBLICACIÓN

**Endpoint: `temu.local.goods.v3.add`.** Es el único de los tres que valida **campo por campo** y nombra la ruta exacta del error (`skuList[0].price.basePrice type error`). v1 y v2 dan errores genéricos. *(Que v3 sea "el oficial vigente" es **inferencia técnica**, no hay doc que lo diga.)*

```jsonc
{
  "goodsBasic": {
    "goodsName": "...",           // obligatorio (string vacío NO cuenta)
    "catId": <hoja>,              // obligatorio
    "externalGoodsId": "SKU",     // obligatorio
    "goodsDesc": "..."            // existe; nunca lo exigió
  },
  "skuList": [{
    "externalSkuId": "SKU",       // obligatorio ← este es el que vuelve como extCode en los pedidos
    "images": ["https://..."],    // obligatorio
    "price": {                    // obligatorio, OBJETO
      "basePrice": {"amount": "29899", "currency": "MXN"},
      "listPrice": {"amount": "25899", "currency": "MXN"}
    },
    "quantity": 350               // numérico
  }]
}
```

Orden en que los pide (caminata real): `goodsBasic` → `goodsName` → `externalGoodsId` → `skuList[0].externalSkuId` → `images` → `price.basePrice`. Después pasa a reglas de negocio.

### 🔴 Las tres respuestas duras

**1) ¿Precio al publicar o negociado aparte? → AL PUBLICAR**, como objeto de tres niveles. `amount` y `currency` son **STRING** (rechaza int y float). La familia `priceorder.*` es un canal **aparte y posterior**: una bandeja de auditoría/negociación sobre precios ya publicados (hoy vacía). No es por donde se fija el precio inicial.

> **⚠️ EL ERROR QUE COSTARÍA DINERO: `amount` va en CENTAVOS.** Verificado 6/6 contra productos vivos, cruzando el detalle contra el precio decimal del listado: `retailPrice.amount = "29899"` ↔ precio mostrado `298.99`; `"249856"` ↔ `2498.56`. Son dígitos, sin punto decimal. Mandar `"199.00"` (como sugiere la forma del campo) es, en el mejor caso, un rechazo — y en el peor, **un producto de $1.99**. Consistente con que `"0.01"` devuelva `150010030 Price input error`.
> **NO VERIFICADO:** el DTO de alta habla de `basePrice`/`listPrice` y el de lectura devuelve `retailPrice`/`listPrice`. **Cuál mapea a cuál no está confirmado.** Es dinero: se resuelve con **un solo SKU canario**, no con el lote.

**2) ¿Stock al publicar o por separado? → AL PUBLICAR**, campo `skuList[].quantity`, numérico, presente en las 3 versiones del alta. `bg.local.goods.stock.edit` queda para actualizaciones posteriores (ahí engancha el fan-out que ya está vivo). *NO VERIFICADO si `quantity` es obligatorio: el campo existe y es numérico, pero la caminata se detuvo en `basePrice` antes de llegar a exigirlo.*

**3) ¿Imágenes cómo? → POR URL. Temu la descarga y la rehospeda; no se sube binario.** `temu.local.goods.image.v2.upload` pide `fileUrl` (string) y `usage` (**ENTERO**, enum 1..7). Prueba de que descarga de verdad: con un dominio inexistente responde "The input fileUrl:… is incorrect". Confirmación independiente: **las fotos de los 152 productos vivos están en `https://img.kwcdn.com/local-goods-image-g/…`**, no en chunche.shop. (`bg.local.goods.image.upload` v1 está muerto: `150010002` siempre.)
> *NO VERIFICADO: qué devuelve el upload (¿imageId o URL kwcdn?), qué significa cada `usage` 1..7, y si `skuList[].images` acepta una URL nuestra directamente o exige una ya rehospedada. Lo más probable es lo segundo — para eso existe el upload.*

### 🟡 Lo que el DTO mínimo NO dice y los productos vivos SÍ
La caminata de campos obligatorios se detuvo en `basePrice`; **el validador real pide más**. Los 6 productos vivos que abrí traen, todos, a nivel SKU:

```jsonc
"productExpressInfo": {
  "weightInfo": {"unit":"g","weight":"4312"},
  "volumeInfo": {"unit":"cm","length":"28","width":"28","height":"33"}
}
```
y a nivel producto `goodsServicePromise` (`shipmentLimitDay`, **`costTemplateId`**, `fulfillmentType`) y `goodsOriginInfo` (`originRegionName1: "Mexico"`). Trátalos como obligatorios de facto.

> **⚠️ TRAMPA HEREDADA, YA FILTRADA A TEMU: las dimensiones no son medidas.** Está verificado en el proyecto que L×A×H se reconstruyó desde `_kubera_cbm` (1,574 productos publicados lo tienen). La huella se ve en los productos que **ya están en Temu**: `VAR-0455-EST` mide **21.6 × 21.6 × 21.6** (cubo perfecto) y `DEP-0017-GRI` da **exactamente 0.0300 m³** con peso 6,000 g = **peso volumétrico 6.00 kg idéntico al real** (no es coincidencia: uno se derivó del otro). Y `ORG-0461-NEG-22K` pesa 1.94 kg pero **factura como 6.39 kg volumétricos, 3.3× de más**. Temu cobra volumétrico igual que Walmart. **Antes de subir 282 productos, decidir de dónde salen peso y volumen** — o se paga el flete inflado 282 veces.

### Códigos de error que conviene distinguir
`150011055` falta campo obligatorio (**lo nombra**) · `150011003` campo inválido · `3000000` error de tipo (**da la ruta completa**, el más útil) · `150010030` regla de precio · `150010042` categoría no disponible · `7000000` endpoint caído/no habilitado · `150010002` system error.

---

## 5. QUÉ TIENE QUE HACER LA IA

**Mismo contrato que TikTok, y por la misma razón** (`tiktok_atributos.py`, ya en producción): *la IA propone eligiendo de listas cerradas; el CÓDIGO valida contra la plantilla real de la categoría y lo que no coincide NO se manda.* Es la lección de `TEC-1812-NEG` (publicado en "Máquinas de Coser" por confiar en un detector).

Temu encaja mejor que TikTok en ese molde porque **213 de 231 atributos medidos son lista cerrada de `vid`** — no hay margen para redactar.

### Qué sale de datos que YA tenemos (sin IA)

Consultado hoy contra Woo (1,997 productos publicados, 1,935 con atributos):

| Dato de Temu | De dónde sale | Cobertura real |
|---|---|---|
| `goodsName`, `goodsDesc` | Título y descripción de Woo | 100% |
| `externalGoodsId` / `externalSkuId` | El SKU | 100% |
| `quantity` | Stock de Woo (fuente de verdad) | 100% |
| `price` | Precio **regular** de Woo (regla vieja de la casa) | 100% |
| `images` | Galería de Woo (con `_cb`, regla 5) | 100% |
| Color (spec / atributo) | Sufijo del SKU → tabla `COLOR_SKU` **ya existente** | 100% |
| Peso / volumen | `_weight` + `_length/_width/_height` | 9,523 filas — **pero ver la trampa del CBM** |
| `catId` | `bg.local.goods.category.recommend{goodsName}` | **4/4 aciertos** en primera posición |

**La categoría no la elige la IA:** `category.recommend` devuelve directamente **hojas**, que es justo lo que pide `template.get`. (`bg.local.goods.category.check` está muerto: `7000000` con las 23 formas probadas.) Y ojo con la **regla 2 de la casa**: si el panel tiene una elección humana guardada, esa manda sobre el recomendador.

### Qué necesita IA de verdad

1. **Material** — el obligatorio duro más frecuente. **No se puede mapear desde Woo**: solo 575 de 1,935 productos (~29%) tienen un atributo `material` limpio, y el resto está fragmentado en ~200 variantes hiper-específicas heredadas de ML (`material del casquillo`, `blade_material`, `bristles_material`, `materiales de la suela`…), todas **texto libre en español**, contra una lista cerrada de `vid` en Temu. → **La IA MAPEA** ese texto + título + descripción a un vid válido.
2. **Los obligatorios propios de Electrónica** (Connector Type, Native Resolution, etc.).
3. **Los numéricos con unidad** (`ANSI Brightness` en lm, `Battery Capacity` en mAh, potencia en W): son 18 de 231, con `min`/`max`/unidad declarados → la IA propone el número, el código valida el rango.
4. **Los opcionales de lista cerrada**, para que el producto aparezca en filtros. Un producto sin atributos existe pero nadie lo encuentra.

### Qué NO necesita IA (regla determinista)
- **Power Supply / Power Mode → "Use Without Electricity" / "Without electricity"**
- **Battery Properties → "Without Battery"**

Con eso se apagan todos los condicionales. Para el grueso del catálogo, **los obligatorios duros se cierran con Material (IA) + dos constantes**.

### Detalles de implementación que ya sabemos
- Pedir la plantilla **con `language=es`**: atributos y valores llegan en español → el prompt no traduce nada.
- Para el **prompt**, `temu.local.product.attributes.get` es más limpio; para el **validador**, `bg.local.goods.template.get`, porque es el único que trae `showType` y la cascada padre-hijo (`parentTemplatePid`, `templatePropertyValueParentList`, `showCondition`).
- La llave del JSON de salida es el **`vid`**, no el nombre visible (idéntico a TikTok).
- Respetar `chooseMaxNum` (1, 3 o 5 valores según el atributo).

---

## 6. PLAN

### Lo que hace Brandon (consola / trámites) — arranca hoy, corre en paralelo
1. **Terminar el alta del webhook**: Push website = `https://backendomnicanal-production.up.railway.app/api/webhooks/temu`, eventos *Order status change* + *Aftersales status change*, y **enviar a aprobación**. Hasta 1 semana; el reloj corre desde que lo mandas.
2. **Pedir a Temu Support el permiso de las APIs de importes** (`bg.order.amount.query` / `temu.order.amount.v2.query`). Dicen ~1 día hábil. **Sin esto los pedidos de Temu entran sin precio.**
3. Cuando la app quede aprobada: **autorizar los eventos en Seller Center** (sale token nuevo → actualizarlo).
4. **Revisar el freight template `LFT-18510029444014331627`, que se llama "test"**, antes de colgarle 282 productos.
5. Decidir: ¿publicamos bajo **PICOOL** (única marca de la lista) o damos de alta la marca Kubera? *(Los productos vivos van sin marca, así que se puede omitir.)*
6. **Decisión de peso/volumen** (ver la trampa del CBM). Es la que más dinero mueve.

### Lo que se programa
7. **`settings.temu_*` en config.py + variables en Railway.** Hoy **no existe ningún `settings.temu_*`** y las 4 credenciales `TEMU_*` solo viven en el `.env` local. Encender el receptor con firma es **flujo vivo → regla 3: tu dale antes del push.**
8. **Cliente Temu en el backend** portando `tm_api.py`, con la corrección que costó el censo: **firmar con tipos nativos** (números planos, listas/objetos en JSON compacto). La receta original hacía `json.dumps()` de todo, así que un entero viajaba como `"1"` y nunca se podía mandar un número real.
9. **Completar el receptor**: descifrado AES → verificación de firma (las 2 variantes) → seguir en **observación** hasta ver el primer evento real. Después: `bg.order.detail.v2.get` → `extCode` → pedido de Woo, con el mismo `asyncio.Lock` por orden que salvó a ML de 164 duplicados.
10. **Llamar `bg.tmc.message.update`** con `permitEventCodeList` (paso 5 del alta; sin él no llega nada).
11. **Motor de publicación**, en este orden: `out.sn.check` (idempotencia, lotes de 50) → `category.recommend` → `template.get?language=es` → reglas + IA → **validador contra la lista cerrada** → `temu.local.goods.v3.add`.
12. **🔒 CANARIO OBLIGATORIO: 1 SKU, verificado a mano**, antes de cualquier lote. Confirma las dos incógnitas de dinero: `amount` en centavos y el mapeo `basePrice` vs `listPrice`. Después tandas de 20–50 con `out.sn.check` delante.
13. Sumar Temu al fan-out de stock (ya vivo) vía `bg.local.goods.stock.edit`.

---

### Bloqueadores abiertos, resumidos
| # | Bloqueador | Dueño |
|---|---|---|
| 1 | Permiso de las APIs de importes (`3000032`) → **pedidos sin precio** | Brandon / Temu Support |
| 2 | Aprobación manual del webhook, hasta 1 semana | Temu |
| 3 | `authEventCodeList` vacío + `appSubscribeStatus=0` | Brandon (Seller Center) |
| 4 | Peso/volumen reconstruidos desde CBM → flete inflado | Decisión de negocio |
| 5 | Sin `settings.temu_*` ni variables en Railway | Código + tu dale (regla 3) |

**Nada se creó, modificó ni borró en Temu.** Todas las llamadas fueron `.get` / `.query` / `.check` / `.recommend`. Las únicas escrituras pendientes (`goods.v3.add`, `tmc.message.update`, `stock.edit`) quedan sujetas a tu autorización.

---

# ANEXO — crítica adversaria

## REVISIÓN ADVERSARIA — GUÍA TEMU (verificaciones propias marcadas [COMPROBADO HOY])

### A. DINERO — el error más caro está en la propia guía

**1. "`amount` va en CENTAVOS — verificado 6/6" es FALSO tal como está escrito.** El mismo campo tiene DOS formatos según el endpoint. [COMPROBADO HOY, cruzando `tm_censo_productos.json` (crudo del listado) contra los 6 `tmc_detalle_*.json`]:

| SKU | `detail.query` retailPrice.amount | `list.query` retailPrice.amount | list listPrice.amount | list marketPrice |
|---|---|---|---|---|
| ORG-0461-NEG-22K | `"29899"` | `"298.99"` | `"258.99"` | `25899` (int) |
| ORG-0769-AZL | `"19329"` | `"193.29"` | `"170.99"` | `17099` |
| DEP-0017-GRI | `"249856"` | `"2498.56"` | `"0.00"` | `0` |
| +3 más | centavos | decimal | decimal | int centavos |

La guía tomó la lectura del **detalle** y la elevó a regla universal. La evidencia que cita ("6/6 productos vivos") contiene simultáneamente la refutación. Conclusión correcta: **el formato de `amount` en el DTO de ESCRITURA es DESCONOCIDO** (el probe verificó únicamente que es STRING y que `"0.01"` viola el mínimo — dato compatible con decimal, no con centavos).

**2. El payload de ejemplo mezcla tres campos distintos y no existe evidencia de `basePrice`.** `basePrice:29899` / `listPrice:25899` son exactamente el retail y el list de ORG-0461 en centavos del *detalle*; `25899` es además el `marketPrice` entero del *listado*. Ningún producto vivo tiene un campo llamado `basePrice` (las lecturas exponen `price`, `retailPrice`, `listPrice`, `marketPrice`). Como la guía misma admite que el mapeo no está verificado, el ejemplo **no debería llevar números**: un lector lo copia. Riesgo real: si `basePrice` es el precio de suministro (lo que Temu paga al vendedor) y se le mete el retail, se publican 282 SKUs con el precio equivocado en el lado que factura.

**3. El canario no basta como está definido.** "1 SKU verificado a mano" sin decir **cómo** se verifica: hay que leerlo de vuelta por **AMBOS** endpoints (`list.query` y `detail.query`) y contra el Seller Center, porque los dos formatos coexisten. Y no hay plan de reversión: en todo el sondeo no se exploró ningún `.delete`, `.offline` ni `sale.status.set`, así que **el canario es hoy irreversible con lo que se sabe**. Nombrar la vía de baja ANTES de autorizar la primera escritura.

**4. Mediana no reproducible.** La guía dice mediana $484.15; sobre los 152 del censo da **$477.24** [COMPROBADO HOY]. Máximo $23,245.32 ✓, un producto en $0.00 ✓ (es **SIL-0015**, y además con stock 0 — dato que la guía omite).

### B. CAMPOS Y `type` CITADOS SIN VERIFICAR

**5. `productExpressInfo` / `goodsServicePromise` / `goodsOriginInfo` como "obligatorios de facto" — cero evidencia contra `v3.add`.** [COMPROBADO HOY] La cadena `productExpressInfo` **no aparece en ningún archivo de sondeo**, solo en los detalles de lectura. Peor: `tm_walk_temu_local_goods_v3_add.json` muestra que añadir `goodsServicePromise`, `goodsProperty`, `goodsTemplate`, `bulletPoints`, `carouselImages`, `goodsSaleInfo`, `goodsShipmentLimit` a la raíz **no cambió el error ni una vez** → v3 **ignora en silencio** las llaves que no conoce. Y `tm_campos_out.json` lista `weight`, `length`, `width`, `height`, `packageWeight` como NO reconocidas a nivel SKU. Traducción: se pueden publicar 282 productos sin peso, sin volumen y sin plantilla de flete **sin un solo error**. Es lo contrario de "trátalos como obligatorios de facto".

**6. Variaciones/color: la guía los da por resueltos y no lo están.** [COMPROBADO HOY] 151 de los 152 vivos traen `specIdList:[77556301]`, `specNameList:["Standard"]`; solo 1 tiene >1 SKU. El payload v3 de la guía **no tiene ningún campo de spec**, y el probe reporta `specIdList`/`specList`/`specName`/`specValue` como no reconocidas en v3. El único endpoint que acuña spec ids (`bg.local.goods.spec.id.get`) **se dejó incompleto a propósito por oler a get-or-create = ESCRITURA**. Sin embargo §5 declara "Color … 100%". Es un hueco de diseño, no un dato resuelto.

**7. `bg.local.goods.stock.edit` (paso 13) nunca se llamó** — parámetros, límites de lote y semántica: NO VERIFICADO. Se presenta como enganche listo.

**8. "Los publicó M2E, con el esquema v1"** — inferencia. El propio sondeo dice que la relación entre estos 152 y los 96 anuncios que M2E reportaba el 7-ago **no se cruzó**. (Sí se sostiene: `trademarkId`/`brandId` null en los 152 [COMPROBADO HOY], y `costTemplateId` LFT-18510029444014331627 en los 152 con `costTemplateName:"test"` en los 6 detalles.)

**9. "Presupuesto 6× más estricto que ML"** — no hay ninguna medición del presupuesto de ML en ningún lado. Número inventado.

**10. "¿Hay handshake? **No.**"** — el sondeo dice "no se documenta". Ausencia de documentación ≠ verificación. Escribirlo como negativa rotunda es exactamente el tipo de afirmación que después cuesta un intento de alta.

**11. `bg.tmc.message.update` (paso 10): forma del cuerpo NO VERIFICADA** (el probe registró la ambigüedad envoltorio `request` vs. plano) y hay límite de 1 req/seg (110020007). La guía lo da por trivial.

**12. El enum de `orderStatus` del evento NO está verificado** (el sondeo lo marca explícitamente). El ingestor tiene que decidir pagado/cancelado con él y la guía ni lo menciona.

### C. PASOS QUE ESCRIBEN EN LA TIENDA VIVA SIN QUERER

**13. Falta el paso de imágenes en el pipeline (paso 11) y ese paso ESCRIBE.** §4 dice que lo más probable es que Temu exija URL ya rehospedada, pero la secuencia `out.sn.check → recommend → template.get → reglas+IA → validador → v3.add` **no incluye `temu.local.goods.image.v2.upload`**. Quien implemente lo llamará "para probar" y estará creando activos reales en la galería de la tienda — el sondeo lo evitó por eso mismo. Debe quedar como escritura explícita, con dale, y probada con 1 imagen.

**14. `bg.local.goods.spec.id.get` (ver 6)**: quien intente resolver color lo va a llamar. Marcarlo en rojo como probable get-or-create.

**15. El orden del plan pone escrituras antes de cualquier prueba.** Paso 10 (`tmc.message.update`) enciende un flujo vivo y aparece como ítem de programación; la salvedad de regla 3 está enterrada en el párrafo final. Debe ir inline en los pasos 10, 11, 12 y 13.

**16. No hay instrucción de tandas seguras:** "tandas de 20–50" sin decir qué se hace si la tanda 3 falla a medias (¿reintento? ¿`out.sn.check` de nuevo? ¿producto huérfano a medio crear?).

### D. WEBHOOK Y PEDIDOS

**17. El supuesto del precio está bien resuelto (el evento NO trae precio), pero falta la consecuencia operativa.** El plan enciende el ingestor sin decir **qué precio se escribe mientras dure el bloqueador #1**. El tab VENTAS es 100% `pedidos_ml`: pedidos de Temu en 0 contaminan KPIs de ingreso, y el precedente de la casa (comisión 0 rellenable 0→valor) no cubre precio 0. Hace falta una decisión explícita: retener pedidos, escribir precio de catálogo Woo, o no encender hasta tener el permiso.

**18. No hay red de seguridad de sondeo.** Temu reintenta 8 veces y **abandona el mensaje**; no existe API de pull (verificado: el único `tmc` es `message.update`); la propia doc recomienda tarea periódica. Todos los canales del proyecto sondean (Amazon 5 min, M2E 10 min). El plan **no incluye ningún job de conciliación con `bg.order.list.v2.get` por `updateAtStart/updateAtEnd`**. Un deploy de Railway durante una ráfaga = venta perdida sin rastro.

**19. Falta el evento `bg_cancel_order_status_change` en la selección recomendada.** La guía manda marcar solo *Order status change* + *Aftersales*. Con `PEDIDOS_WC_DESCUENTA_STOCK=true`, las cancelaciones que viajen por ese evento no llegan → stock fantasma. Está en los 5 códigos que la app YA declaró [COMPROBADO HOY], así que no marcarlo es una decisión, no un límite.

**20. "Aftersales → devoluciones → regresar stock" no tiene fuente de datos verificada.** El payload de aftersales trae `parentAfterSalesSn`/`parentOrderSn`/estado, **sin SKU ni cantidad**, y en todo el sondeo no se identificó ningún endpoint de detalle de posventa. La promesa de reponer stock está en el aire.

**21. No se define la llave del pedido.** El evento trae `parentOrderSn` **y** `orderSn`; el detalle devuelve `parentOrderMap` + `orderList[]` con un `orderSn` por línea. `pedidos_ml` tiene PK = id de orden del marketplace. La guía nunca dice cuál es la PK de Temu. Elegir mal = pedidos duplicados o colapsados — la misma familia de bug que los 164 duplicados de ML.

**22. §1 se contradice consigo misma y no da criterio de "listo".** El encabezado dice que la mitad de arriba del trámite ya está hecha; el paso 1 le pide a Brandon hacer el alta y el bloqueador #2 dice que la aprobación está pendiente. Estado real [COMPROBADO HOY con `bg.open.accesstoken.info.get`]: `appSubscribeStatus=0`, `appSubscribeEventCodeList` = los 5 códigos, `authEventCodeList=[]`, 129 scopes, `bg.tmc.message.update` presente, **cero scopes con "amount"**. Nadie verificó **qué valor de `appSubscribeStatus` significa "activo"** (¿1?), así que el paso 1 no tiene prueba de terminación. Además: cambiar la Push website vuelve a disparar la aprobación — si el alta ya se mandó con otra URL, el paso 1 la reinicia y eso no se advierte.

### E. ATRIBUTOS POR CATEGORÍA — subestimación cuantificada

**23. La muestra de 16 hojas no representa el catálogo.** [COMPROBADO HOY] Los 152 productos vivos se reparten en **135 catId distintos**, y de las 16 hojas medidas **solo 2 aparecen** (4665 y 10099). Los 282 por publicar caerán en ~250 hojas prácticamente ninguna medida. Toda la conclusión ("0–4 duros por hoja", "existe siempre el valor de escape *Without Battery*", "213/231 lista cerrada") es un muestreo del 6% de las hojas relevantes. El "~90% con reglas deterministas" no tiene base.

**24. `category.recommend` "4/4 aciertos" no aplica a nuestros títulos.** Los 4 aciertos fueron títulos **inventados en inglés y cortos**; la verdad de terreno la puso el propio revisor. Las dos pruebas en español del otro sondeo (`Termo de acero inoxidable 14 oz` → 12702, `Cuerda de escalada 100 m` → 33098) **no tienen verificación**, y 12702 ≠ 10975 (la hoja que devolvió el equivalente en inglés). Los títulos de Kubera son largos, en español y cargados de palabras clave. Con `category.check` muerto **no hay validador** y `recommend` no devuelve confianza. Poner el recomendador en automático sobre 282 SKUs es la regla 2 de la casa al revés y el caso TEC-1812 multiplicado.

**25. Compliance y tax code no aparecen en la guía.** El propio sondeo listó `bg.local.goods.compliance.rules.get` (devuelve `checkInfoList`, `goodsCertList`, `mustHaveActualPhoto`) y `bg.local.goods.tax.code.get` (GEN_STANDARD/ZERORATE/EXEMPT) y los dejó sin explorar. Ni el payload ni el plan los mencionan. Categorías de electrónica, juguetes o contacto con alimentos suelen exigir certificado; `mustHaveActualPhoto` puede invalidar el pipeline de fotos de estudio. Trabajo de tamaño desconocido, tratado como inexistente.

**26. El mapeo de Material es trabajo POR CATEGORÍA, que es justo lo que la guía dice que no hace falta.** Los `vid` de Material son distintos en cada hoja; con ~250 hojas hace falta una tabla de validación por hoja, o sea el "validador por categoría" negado en §5. Además el 71% de los productos **no tiene atributo material**: la IA no mapea, **adivina** desde título/descripción hacia una lista cerrada, y no se verificó que exista un valor de escape tipo "Otros" en todas las listas de Material.

**27. Sin medir: límites de título, de `goodsDesc`, mínimo/máximo de imágenes por SKU, si acepta HTML, `bulletPoints`, `goodsSizeChartList`.** Todos aparecen en los detalles vivos y ninguno se acotó.

**28. `shipmentLimitDay: 1` se copia sin señalar que es un compromiso.** Copiar el `goodsServicePromise` de los vivos ata 282 SKUs a despacho en 1 día, colgados de una plantilla de flete llamada "test".

### F. INSTRUCCIONES QUE BRANDON NO PUEDE SEGUIR

**29.** "App: la app nuestra, la del `app_key` que ya usamos. Es un selector" — el selector muestra **nombres**, no app_keys. La guía no da el nombre de la app ni dónde leer el app_key.

**30.** No hay **ninguna URL de consola**. Y el sondeo verificó que la dirección del Seller Center donde el vendedor autoriza eventos **solo está documentada para US y EU, no para MX/GLOBAL**. El paso 3 es literalmente inejecutable como está escrito.

**31.** Paso 3: "sale token nuevo → actualizarlo". Brandon no puede: el token solo vive en el `.env` local, no existe `settings.temu_*` ni variable en Railway (paso 7 de la propia guía). El orden es circular — 7 tiene que ir antes que 3, y alguien tiene que estar presente para capturar el token en el momento.

**32.** Paso 2: "pedir a Temu Support" sin canal, sin ruta de ticket y sin plantilla. Debe incluir los dos `type` exactos + mallId 635517742093915 + regionId 128.

**33.** Paso 4: "revisar el freight template" sin ruta en la consola y sin criterio. Nadie leyó sus zonas ni sus tarifas; "revisar" no es una instrucción.

**34.** Paso 6: "decisión de peso/volumen" sin opciones ni magnitud. No dice cuántos de los 282 tienen dimensiones reales vs. reconstruidas de `_kubera_cbm`, ni cuáles son las 2 o 3 salidas posibles. Es la decisión que "más dinero mueve" y es la peor especificada.

**35.** "Usa el botón Test": no se dice dónde está ni si funciona antes de la aprobación.

**36.** Paso 5 (PICOOL vs. alta de Kubera): sin procedimiento — `brand.trademark.get` nunca se probó — y sin consecuencia declarada.

### G. HUECOS DE PLAN

**37. M2E sigue siendo el otro escritor del mismo canal.** CLAUDE.md dice que el catálogo Woo→M2E se sincroniza solo, y los 17 existentes salieron de ahí. La guía no dice qué se hace con los 17 (¿saltar? ¿resincronizar precio/stock?) ni plantea apagar o acotar M2E. Con el fan-out de stock apuntando también a Temu, quedan dos procesos peleando por los mismos productos.

**38. Sin plan B de endpoint.** Que `v3.add` sea el vigente es inferencia (la guía lo admite) pero el paso 11 lo fija sin alternativa: si v3 no permite expresar specs o `productExpressInfo` (ver 5 y 6), hay que caer a v2/v1 — y v1 sí tiene `specIdList`, `weight`, `length`, `width`, `height` a nivel SKU.

**39. El "282" caduca.** El propio sondeo advierte que un censo por dos cubetas en corrida no atómica puede duplicar o perder. `out.sn.check` antes de cada tanda lo cubre; la cifra publicada en la guía no.