# TEMU — hallazgos como datos (mallId 635517742093915, regionId 128)

> **Método:** todo lo de aquí está medido en vivo contra la API de Temu el
> **12 y 13-ago-2026**, o leído de la doc oficial del portal
> (`partner.temu.com/documentation`, secciones *Price*, *Add Products* y
> *Manage Products*). Lo que **no** se pudo verificar va marcado
> `NO VERIFICADO`. Donde la doc y la medición se contradicen, **gana la
> medición** y se dice cuál era la doc.
>
> Complementa a [TEMU_MANUAL.md](TEMU_MANUAL.md), que tiene la corrección del
> precio al principio. Formato de referencia:
> [`backend/services/amazon_contenido.py`](../backend/services/amazon_contenido.py)
> y [CONTENIDO_POR_CANAL.md](CONTENIDO_POR_CANAL.md).

---

## 1. RESULTADOS

### Publicador viejo (esquema equivocado) — 12-ago

| SKU | Intentos | Resultado |
|---|---|---|
| `ACC-0160-AZL` | 3 | 1 alta OK + 2 `150010090 SKU duplicated` |
| `JUGU-1158-VER` | 1 | alta OK |

**Los dos quedaron con el precio a 100×** (`MX$16,402.00` en vez de `164.02`,
`~MX$24,800.00` en vez de `248.00`) por mandar el importe en "centavos". Están
vivos y hay que corregirlos **a mano en el Seller Center**: la API no deja
(`150011019` a cualquier valor mientras el producto está en evaluación).

### Publicador nuevo (esquema v3 real) — 13-ago

| SKU | goodsId | Precio base | Estado |
|---|---|---|---|
| `JUGU-0067-MUL` | 608635434407443 | MX$255.63 | Incompleto |
| `COM-0081-ROS` | 608343376661537 | MX$119.43 | Incompleto |
| `ILUM-0089-PLA` | 608635434412704 | MX$245.65 | **Borrador** |
| `OFI-0057-NEG` | 607827980571328 | MX$200.55 | Incompleto |
| `ACC-0468-NEG` | 608309016893719 | MX$247.37 | Incompleto |
| `HERR-0374-MUL` | 607862340347700 | MX$83.85 | **Borrador** |

**6 intentados · 6 publicados · 0 fallidos.** Los 6 verificados campo por campo
contra la API. Los 2 en Borrador son los eléctricos — causa en §2.

### 🟢 CIERRE DEL 14-AGO — el lote grande

Con el publicador de 3 fases ya portado al backend
([`scripts/publicar_temu.py`](../backend/scripts/publicar_temu.py)):

```sql
select status, count(distinct sku) from ops.channel_submissions
 where canal='temu' and detail_ref='temu:lote:20260814' group by status;
```

| | |
|---|---|
| **published** | **195** |
| failed | 4 (todos vocabulario; **0 SKUs quemados**) |
| pending (aviso no bloqueante, publicaron igual) | 8 |
| filas de intento | 208 |

**Total publicado por nosotros: 201.** Catálogo de Temu: **351 productos
reales** (152 de M2E + 199 nuestros), dentro del rango pedido de 300–500.

Pipeline: 866 fichas de Woo → 269 enriquecidas → 229 listas → 201 publicadas.
40 retenidas por obligatorios sin llenar y 31 apartadas sin categoría que encaje.

> El detalle de la entrega —payload literal que pasó, tabla de errores, cómo se
> resolvieron las specs, la plantilla de flete y la auditoría de los 12 cambios
> que hice sobre la marcha— está en
> **[TEMU_ENTREGA_A_OMNICANAL.md](TEMU_ENTREGA_A_OMNICANAL.md)**.

### SKUs quemados

**Cero SKUs reales perdidos.** Borrar NO libera el `externalSkuId`
(`150010090`, permanente), pero los 3 fallos registrados fueron reintentos
sobre un SKU **que ya se había dado de alta con éxito** — no hubo pérdida.

La cuenta a vigilar es otra: **un fallo DESPUÉS de que Temu acepta el alta sí
quema el SKU para siempre.** Por eso el publicador corre `out.sn.check` antes
de cada tanda y escribe el payload a disco antes de mandarlo.

Aparte quedaron **14 productos de prueba con SKU inventado** (`KBTEST-*`,
`KBPROBE-*`, `KBL-*`) creados para no quemar SKUs reales. Solo 1 se pudo
borrar; el resto responde *"data under review cannot be deleted"* y hay que
reintentar cuando salgan de revisión. `NO VERIFICADO`: cuánto dura esa
revisión.

---

## 2. CAMPOS QUE TEMU EXIGE — `temu.local.goods.v3.add`

### ⚠️ Lo primero: "obligatorio" en la doc NO significa que se valide

Medido el 13-ago con 12 altas de prueba, quitando un campo cada vez. **Las 12
publicaron sin error.** La doc marca `Required=True` en `packageInfo`,
`variations`, `quantity`, `skuList[].price` y `goodsBasic`, pero el validador
solo hace respetar cinco cosas.

| Campo | Doc | ¿Se valida DE VERDAD? | Si falta |
|---|---|---|---|
| `goodsBasic.goodsName` | True | ✅ **sí** | error |
| `goodsBasic.externalGoodsId` | True | ✅ **sí** | error |
| `skuList[].externalSkuId` | True | ✅ **sí** | error |
| `skuList[].images` | True | ✅ **sí** | error |
| `skuList[].price.basePrice` | True | ✅ **sí** | `150011003 [basePrice]` |
| `skuList[].packageInfo` | True | ❌ **no** | **default 100 g / 10×20×30 cm** |
| `skuList[].variations` | True | ❌ no | Temu inventa specs del título |
| `skuList[].quantity` | True | ❌ no | publica igual |
| `goodsBasic.extCatName` | False | ❌ no | usa su recomendador |
| `goodsBasic.costTemplate` | False | ❌ no | aplica la plantilla default de la tienda |
| `goodsBasic.goodsDesc` | False | ❌ no | queda vacía |

> **El más caro es `packageInfo`.** Si falla, el producto se publica con
> **100 g y 10×20×30 cm** — y Temu cobra flete volumétrico. Es exactamente lo
> que le pasó a `JUGU-1158-VER`: mandamos 500 g / 20×20×20 dentro de
> `productExpressInfo` (nombre de v1, que v3 ignora) y quedó con el default.

### El inventario, siete columnas

| Campo | Ruta en el payload | Tipo | ¿Obligatorio real? | Origen | Formato / límite | Verificado |
|---|---|---|---|---|---|---|
| Nombre | `goodsBasic.goodsName` | STRING | **SÍ** | IA (mejora del de Woo) | ≤500, **trunca en silencio** | ✅ medido |
| SKU producto | `goodsBasic.externalGoodsId` | STRING | **SÍ** | SKU Kubera | ≤100, único para siempre | ✅ medido |
| Categoría | `goodsBasic.extCatName` | STRING | no | recomendador + IA | id de **hoja** como string | ✅ medido |
| Descripción | `goodsBasic.goodsDesc` | STRING | no | IA | texto plano; ≥11,700 aceptado | ✅ medido |
| Bullets | `goodsBasic.bulletPoints` | STRING[] | no | IA | `NO VERIFICADO` el máximo | ⚠️ |
| Días despacho | `goodsBasic.shipmentLimitDay` | INT | no | fijo `2` | 1 o 2 | doc |
| Plantilla flete | `goodsBasic.costTemplate` | STRING | no | fijo | id **o nombre** | ✅ medido |
| Tipo producto | `goodsBasic.productType` | INT | no | fijo `1` | 1 normal · 2 custom · 3 MTO · 4 usado | doc |
| Atributos | `attributes[]` | OBJ[] | depende | IA validada | `{name, value[]}` **texto, no pid/vid** | ✅ medido |
| SKU variante | `skuList[].externalSkuId` | STRING | **SÍ** | SKU Kubera | vuelve como `extCode` en pedidos | ✅ medido |
| Imágenes | `skuList[].images` | STRING[] | **SÍ** | Woo→conversor→Temu | URL `kwcdn` ya rehospedada | ✅ medido |
| **Precio base** | `skuList[].price.basePrice` | OBJ | **SÍ** | precio regular Woo | `{amount:"255.63", currency:"MXN"}` | ✅ medido |
| Precio lista | `skuList[].price.listPrice` | OBJ | no | — | **estrictamente > basePrice** | ✅ medido |
| Stock | `skuList[].quantity` | LONG | no | Woo | `[0, 999999]` | doc |
| Peso | `skuList[].packageInfo.weight` | STRING | no (pero ver arriba) | Woo | **gramos**, 1 decimal, `[0.1, 9999999.9]` | ✅ medido |
| Medidas | `packageInfo.length/width/height` | STRING | no (ídem) | Woo | **cm**, 1 decimal, `[0.1, 9999.9]` | ✅ medido |
| Variantes | `skuList[].variations` | OBJ[] | no | sufijo del SKU | `{name, value}` texto libre | ✅ medido |

### Obligatorio DE VERDAD vs condicional — la distinción que importa

En los **atributos de categoría** (`bg.local.goods.template.get`), `required`
solo cuenta si **`showType == 0`**. Los de `showType == 1` son **condicionales**:
se activan cuando el atributo padre toma cierto valor.

Ejemplo real, hoja 12992 (Luces de cadena interiores): **20 atributos, 7
marcados `required`, pero solo 2 son duros.** Marcar los 7 pintaría de rojo
todo el catálogo por nada.

| Hoja | Total | `required` | **Duros (showType 0)** | Condicionales |
|---|---|---|---|---|
| 12992 Luces de cadena | 20 | 7 | **2** — Material, Fuente de alimentación | 5 |
| 5566 Termómetros IR | — | 7 | **4** | 3 |
| 26555 Platos de bebé | — | 3 | **2** | 1 |
| 31053 Disfraces niña | — | 3 | **3** | 0 |

**Y los condicionales son en cascada, recursivos.** *Capacidad de la batería
(mAh)* cuelga de *Características de la batería*, que cuelga de *Fuente de
alimentación*. Dos formas de declararlo, hay que soportar las dos:

- `templatePropertyValueParentList`: `[{parentVids:[…], vids:[…]}]` — si el
  padre tomó uno de `parentVids`, el hijo se activa **y sus valores válidos se
  restringen a `vids`**, no a toda su lista.
- `showCondition`: `[{parentRefPid, parentVids}]` — se usa con `controlType=0`,
  que es entrada numérica (sin lista: `minValue`, `maxValue`, `valueUnitList`).

> **Si un condicional se activa y va vacío, Temu lo autocompleta y manda el
> producto a BORRADOR en vez de publicarlo.** Está declarado en el Seller
> Center → *Sincronización automática de respaldo*: «las publicaciones que se
> completan automáticamente se guardan como borradores».
>
> Comprobado 3/3 el 13-ago: `ILUM-0089-PLA` eligió "Enchufe para carga" →
> activó Tipo de clavija y Voltaje → Borrador. `HERR-0374-MUL` eligió
> "Batería/alimentación de doble uso" → activó Voltaje y Tipo de clavija →
> Borrador. `COM-0081-ROS` eligió "Silicona" → no activó nada → publicó bien.

Implementación: [`backend/services/temu_contenido.py`](../backend/services/temu_contenido.py)
— `duros()`, `activados()` (itera a punto fijo) y `faltantes()`, que es el
portero antes de publicar.

---

## 3. EL PRECIO — RESUELTO

### Por dónde entra

```jsonc
"skuList": [{ "price": { "basePrice": {"amount": "255.63", "currency": "MXN"} } }]
```

**Nunca estuvo ignorado.** `basePrice` es obligatorio y se valida campo por
campo. La prueba, un caso por error:

| Lo que se manda | Respuesta |
|---|---|
| `"164.021"` | `150011018` MXN admite 2 decimales |
| `currency:"XXX"` | `150011003` Invalid Request Parameters **[currencyCode]** |
| `"0.01"` | `150010030` Price input error (el mínimo es 0.02) |
| `"-5.00"` | `150010002` |
| sin objeto `price` | `150011003` Invalid Request Parameters **[basePrice]** |

Un campo que se parsea, se valida por rango y se exige no es un campo que se
esté tirando. Lo que fallaba eran **dos errores encimados**:

1. **El cuerpo tenía forma de v1 contra un endpoint v3**, y v3 ignora en
   silencio lo que no conoce. Se pidió `catId` 1761 → publicó en **1769**; se
   mandó 500 g / 20×20×20 en `productExpressInfo` → guardó **100 g / 10×20×30**.
2. **Se leía el campo equivocado** (ver abajo).

### ¿`basePrice` es el de suministro y el de venta lo fija Temu? — SÍ

La doc lo dice sin rodeos: *"this amount information will be used to calculate
the settlement amount with the merchant"*. **`basePrice` es nuestro neto.**
Temu le suma su margen y arma el precio de anaquel:

**`retailPrice` ≈ `basePrice` × 1.1325** — medido sobre los 146 productos vivos
con precio de la tienda.

### El formato al ESCRIBIR

**DECIMAL de 2 cifras. NO centavos.** Tabla de monedas de la doc: MX = MXN,
**2 decimales**, base `[0.02, 99999999.99]`.

> **Se escribe decimal y se lee en centavos.** Mandamos `"213.23"` y
> `detail.query` devuelve `"21323"`. Esa asimetría fue el origen del error: el
> manual tomó la lectura del detalle y la elevó a regla de escritura, lo que
> publica **a 100× el precio** (`JUGU-1158-VER` está en `MX$16,402.00`).

Y `retailPrice` **no es lo que mandamos**: es el precio de anaquel, y **solo
existe una vez que el producto estuvo a la venta**. Leerlo en un producto
recién creado da `0` siempre. Los 28 productos de M2E que están "no publicados"
y sí traen precio se crearon el 6-7 de ago y cambiaron de estado el 10-12:
estuvieron vivos y bajaron. **Ningún producto de la tienda nació con precio sin
publicarse.**

### ¿Hacen falta dos precios? — no

`listPrice` es opcional (comprobado: un alta con solo `basePrice` pasa) y tiene
trampa: debe ser **estrictamente mayor** que `basePrice`. Mandarlo **igual** lo
tira en silencio — `JUGU-0067-MUL` (base 255.63 = list 255.63) se leyó `0.00`,
contra `KBTEST-0812A` (base 164.02, list 213.23) que se leyó `213.23`. El
publicador **lo omite**: incluirlo obligaría a inventar un precio tachado.

### 🔴 Lo único que sigue bloqueado

`bg.local.goods.sku.list.price.query` —el único endpoint que **lee `basePrice`
de vuelta**— responde `3000032`: *"ask for seller to authorize this api in
seller center first, and share the new access token"*. **Trámite de Brandon**,
misma familia que el permiso de importes de pedidos.

Mientras tanto el precio **solo se puede verificar a ojo** en el panel:
Productos → Administrar productos → pestaña **Incompleto** → columna
**Precio base**. Ahí se confirmó `MX$255.63` en `JUGU-0067-MUL`.

### Herramienta que sí sirve y es de lectura

`temu.local.goods.baseprice.recommend` estima el precio base que conserva el
mismo neto que otra plataforma. Para `JUGU-1158-VER` propone **87.25** sobre un
precio Woo de 164.02 (**0.53×**). **No lo usa el publicador**: bajar el neto a
la mitad es decisión de negocio. Dato aparte: **no mira peso ni volumen** —
devuelve lo mismo con 100 g que con 40×40×40 cm.

---

## 4. RESTRICCIONES

| Qué | Límite | Verificado |
|---|---|---|
| `goodsName` | ≤ **500**; con 640 **truncó a 500 sin error** | ✅ medido |
| `goodsDesc` | ≥11,700 aceptado; sin máximo encontrado | ✅ medido |
| Precio MXN | 2 decimales · base `[0.02, 99999999.99]` | doc + medido |
| Peso | **gramos**, 1 decimal, `[0.1, 9999999.9]`; `500.55` → redondeó a `500.6` | ✅ medido |
| Medidas | **cm**, 1 decimal, `[0.1, 9999.9]` | doc + medido |
| Stock | `[0, 999999]` | doc |
| `out.sn.check` | máx **50** por llamada, **y repetidos dan `150010003`** | ✅ medido |
| `list.query` | `pageSize` máx 100 (200 → `1000000`) | ✅ medido |

### ¿Acepta solo volumen sin peso? — SÍ, y es una trampa

| Prueba | Resultado |
|---|---|
| volumen **sin** peso | publica; **peso = 100 g** por default, medidas respetadas |
| peso **sin** volumen | publica; peso respetado, **medidas = 10×20×30** |
| **sin `packageInfo`** | publica; **100 g y 10×20×30**, ambos default |
| peso `0` | publica; **100 g** |

**Nunca da error.** Un producto sin peso se factura como si pesara 100 g y
midiera 6,000 cm³. Es dinero, en silencio.

### De dónde salen las dimensiones — y por qué hay que decidirlo

Woo las tiene, pero **no son medidas reales**: se reconstruyeron desde
`_kubera_cbm`. Sobre los 678 SKUs del espejo de TikTok:

- **33** con las tres medidas iguales (cubo perfecto — la huella del CBM)
- sobre los 235 del lote original: **49 con densidad imposible** (<0.05 o >2 g/cm³)
- **88 de 232 facturarían más por volumen que por peso** (mediana 0.69×,
  **p90 10.93×**, máx 1600×)

Temu cobra volumétrico igual que Walmart. **Decisión de negocio pendiente**, no
bloquea publicar pero se paga en cada envío.

---

## 5. IMÁGENES

- Van **por URL**; Temu la descarga y la **rehospeda** en `img.kwcdn.com`.
- **Conserva el tamaño que recibe**: con la URL cruda de chunche.shop quedan en
  **800×800**; pasando por `imagenes_amazon._descargar` + `_a_jpeg(1000)`
  quedan en **1000×1000**. Por eso el pipeline convierte antes de mandar.
- `temu.local.goods.image.v2.upload` pide `fileUrl` (string) y `usage`
  (**entero**). `bg.local.goods.image.upload` (v1) está muerto: `150010002`.
- **La URL `kwcdn` hay que GUARDARLA**: es la que se manda en
  `skuList[].images` y no se puede recalcular — la asigna Temu al subir.
  ✅ medido: el mismo archivo subido dos veces devuelve URLs distintas.
- Mínimos de la doc: no-ropa **1:1, ≥800×800**; ropa **3:4, ≥1340×1785**;
  ≤3 MB; JPEG/JPG/PNG. `NO VERIFICADO` si rechaza por debajo del mínimo — no se
  probó una imagen chica a propósito.
- El Seller Center trae **"Formato automático de imagen" ACTIVADO**, que
  redimensiona y convierte por su cuenta. `NO VERIFICADO` si eso aplica a las
  altas por API o solo a las de ERP.

---

## 6. CATEGORÍAS

- **25 raíces**, árbol por `parentCatId` (el nodo trae `catId`, `catName`,
  `level`, `parentId`, `leaf`, `availableStatus`, `catType`).
- **El árbol completo: 48,025 nodos, 39,467 hojas.** Cacheado en
  `scratchpad/tm_arbol_cats.json` — `cats.get` solo camina **hacia abajo**, así
  que sin el árbol no hay forma de saber cómo se llama la hoja 12438.
- `template.get` **solo funciona en hojas** ("The catId not a leaf category") y
  **acepta `language=es`**, que devuelve atributos y valores en español.
- `bg.local.goods.category.check` está **muerto** (`7000000`): **no hay
  validador de categoría**.

### Tasa de acierto del recomendador

`category.recommend` devuelve **`catIdList` con 5 candidatos**, no uno — el
publicador viejo tomaba el primero a ciegas.

| Muestra | Resultado |
|---|---|
| Primera tanda, sin validador (6 SKUs) | **4 de 6 correctas.** Falló en un pistón de refacción → "Sillas de oficina", y un proyector → "Series de luces" |
| Con validador IA sobre las 5 rutas (65 SKUs) | **la IA corrigió 13 de 35** (37%) la primera opción del recomendador |

O sea: **la primera opción del recomendador se equivoca en ~1 de cada 3**. El
patrón del error es sistemático — mete la **refacción** en la categoría del
aparato completo, y el **accesorio** en la del producto principal, porque va por
palabras del título. Los títulos de Kubera son largos, en español y cargados de
keywords, que es el peor caso para él.

**Los obligatorios SÍ cambian por categoría** (ver la tabla de §2): de 0 a 4
duros por hoja, y los `vid` de "Material" son distintos en cada una. No hay
lista global reutilizable: la plantilla se pide por hoja.

---

## 7. EL WEBHOOK — ⚠️ NO ACTIVADO

Estado medido hoy con `bg.open.accesstoken.info.get`:

```
appSubscribeStatus : 0
authEventCodeList  : los 4 eventos, TODOS con permitsStatus: 0
   bg_order_status_change_event · bg_cancel_order_status_change
   bg_aftersales_status_change  · bg_trade_logistics_address_changed
```

**`bg.tmc.message.update` NO se llamó.** Es una escritura que **enciende un
flujo de negocio vivo**, y la regla 3 de la casa pide mostrar qué se va a
encender y esperar el visto bueno de Brandon antes del push. Queda pendiente de
su autorización — no de código.

Lo que ya está listo del lado nuestro: `POST /api/webhooks/temu` existe,
responde 200 y está desplegado (v0.104.0, en observación).

Lo que trae el evento (de la doc): **solo** `mallId`, `parentOrderSn`,
`orderSn`, `orderStatus`, `updateTime`. **Sin precio, sin SKU, sin cantidad** —
es un aviso, no un documento. Hay que llamar después a
`bg.order.detail.v2.get`, de donde sale `productList[].extCode` = nuestro SKU.

> **El precio del pedido sigue bloqueado**: `bg.order.amount.query` y
> `temu.order.amount.v2.query` responden `3000032`. Sin ese permiso el pedido
> de Temu no se puede congelar con su precio real como hace `pedidos_ml`.

⚠️ **El evento viene cifrado** (AES-128-CBC/PKCS5, clave = IV = los primeros 16
bytes del `app_secret`) y firmado en `x-tm-signature`. **El código de ejemplo
de la doc de Temu está mal**: firma con formato `key=value&` y no reproduce ni
sus propios ejemplos. La fórmula correcta es concatenar clave+valor **sin
separadores**, ordenados alfabéticamente, sobre los 4 `x-tm-*` más el
`eventData` ya descifrado. Un ejemplo oficial incluye `x-tm-ext-param` y el
otro no → **calcular ambas variantes y aceptar cualquiera**.

---

## 8. TRAMPAS

Las que ya estaban (campos desconocidos ignorados en silencio, `page` que no
pagina, SKU quemado al borrar, `success:true` con error) más **las nuevas de
esta corrida**:

| # | Trampa | Cómo se manifiesta |
|---|---|---|
| 1 | **`delete` miente en la capa de afuera** | Devuelve `success:true` / `errorCode 1000000` y el veredicto real va **anidado** en `result.success`, que puede ser `false` con *"data under review cannot be deleted"*. Leer siempre el de adentro. |
| 2 | **`out.sn.check` rechaza repetidos** dentro de la MISMA llamada | `150010003 Invalid Request Parameters`, sin decir cuál. Deduplicar antes. |
| 3 | **Campos `Required=True` que no se validan** | `packageInfo`, `variations`, `quantity`: se publican sin ellos y **se aplican defaults silenciosos** (100 g / 10×20×30). |
| 4 | **`goodsName` trunca a 500 sin avisar** | 640 caracteres entran, se guardan 500. Ningún error. |
| 5 | **`listPrice` igual a `basePrice` se descarta en silencio** | Debe ser **estrictamente mayor**. Igual → se lee `0.00`. |
| 6 | **`retailPrice` no es el precio que mandas** | Es el de anaquel, y vale `0` hasta que el producto sale a la venta. Leerlo para verificar el alta lleva a la conclusión contraria. |
| 7 | **El mismo campo, dos formatos según el endpoint** | `amount` se **escribe decimal** y se **lee en centavos** en `detail.query` (decimal en `list.query`). |
| 8 | **Condicionales vacíos → Borrador, no error** | El alta devuelve `success:true` con `goodsId` y el producto nunca se publica. |
| 9 | **`temu.local.goods.modifyresults.get` devuelve `null`** | Con `goodsIdList`, `goodsId` y `modifyIdList`. No sirve para auditar el alta. |
| 10 | **La categoría del payload v3 no es `catId`** | Es `extCatName` (string). Mandar `catId` no da error: se ignora y Temu usa su recomendador. |

---

## 9. EL REGISTRO — `ops.channel_submissions`

**Hueco cerrado.** El publicador anterior solo dejaba JSON local; el de ahora
([`scratchpad/tm_publicar3.py`](../../AppData/Local/Temp/claude)) escribe una
fila **por cada intento**, pase lo que pase.

```
canal          'temu'
cuenta         'KUBERA'
sku
submission_id  goodsId devuelto por v3.add
operacion      'create_product'
status         'published' | 'failed' | 'pending'
success        true / false
error_resumen  errorCode + errorMsg LITERALES, hasta 2000 caracteres
detail_ref     'temu:lote:20260813'
submitted_at · published_at
```

Se registran **los cuatro** tipos de intento, no solo los de la API: alta OK,
error de la API, **fallo al armar el payload** y **fallo de imágenes**. Además
se llama `temu.local.goods.illegal.vocabulary.check` **antes** del alta —
rechazar ahí **no quema el SKU**; rechazar después sí.

Las tres reglas, implementadas:

1. **Bitácora de INTENTOS, no de estado.** `INSERT` plano, sin `where not
   exists`. Un SKU que falló y luego publicó aparece dos veces; el resumen
   agrupa por SKU y toma el último.
2. **El error va completo y literal.** Nada de resúmenes propios.
3. **Registrar nunca aborta el lote.** Cada escritura en su propio `try`, con
   `autocommit`; los fallos se acumulan y se reportan al final.

```sql
select status, count(distinct sku)
  from ops.channel_submissions
 where canal = 'temu' and detail_ref = 'temu:lote:20260813'
 group by status;
```

Primera fila ya escrita y verificada en la BD:

```
temu | JUGU-0067-MUL | 608635434407443 | create_product | PUBLICADO | true
```

> ⚠️ Los 6 productos del 13-ago se registraron con `status` en
> **`PUBLICADO`/`FALLIDO`** (nomenclatura del publicador v2). De la tanda de
> 100 en adelante se usa **`published`/`failed`**, que es el contrato de este
> documento. Al consultar el histórico, contar las dos formas.

---

## 10. EL PROMPT DE TEMU — y por qué son TRES

Implementación:
[`backend/services/temu_contenido.py`](../backend/services/temu_contenido.py)
(prompts + validadores) y `scratchpad/tm_enriquecer.py` (el de categoría).
Mismo contrato que `ml_atributos.py`, `tiktok_atributos.py` y
`amazon_contenido.py`: **la IA propone eligiendo de listas cerradas, el CÓDIGO
valida contra la plantilla real, y lo que no coincide NO se manda.**

### Por qué no es un solo prompt

Dos de los tres no son una decisión de diseño, es el orden que impone Temu:

1. **La categoría va primero** porque *la categoría determina qué atributos
   existen*. `template.get` solo responde en hojas: hasta no saber la hoja, no
   hay lista de atributos que preguntar.
2. **Los atributos van en dos vueltas** porque la cascada es circular: qué
   condicionales se activan depende de lo que se contestó en los duros. No se
   puede preguntar "Voltaje" antes de saber si el producto dijo "se enchufa".
   Meter los 20 atributos de golpe hace que el modelo llene voltajes de
   productos sin electricidad — que es justo lo que manda productos a Borrador.

El único fusionable es **contenido con la primera vuelta de atributos**
(ahorraría ~25% de llamadas). Están separados porque los atributos se piden con
el título YA mejorado, no con el de Woo. Es un refinamiento, no una necesidad.

> **Lo austero NO se recomienda:** un prompt único con las 5 rutas candidatas y
> los atributos de las 5 hojas crece a ~6,000 tokens por producto (hay hojas con
> 35 atributos de 40 valores) y el modelo elige peor con cinco taxonomías
> revueltas. `NO VERIFICADO` — es criterio, no medición.

### Los tres, y la regla que cada uno carga

| # | Prompt | Qué decide | La regla que lo justifica |
|---|---|---|---|
| 1 | `PROMPT_CAT` | 1 de las 5 rutas de `catIdList`, **o ninguna** | *"Fíjate en QUÉ ES el producto, no en las palabras del título. Una REFACCIÓN no va en la categoría del aparato completo."* Salió de dos errores reales: un pistón → "Sillas de oficina", un proyector → "Series de luces" |
| 2 | `build_prompt_contenido` | título, descripción, bullets | *"El comprador llega por búsqueda y decide con la foto y el título. No hay marca que lo respalde."* Más las prohibiciones que tumban listings |
| 3 | `build_prompt_atributos` | pid + vid, en dos vueltas | *"Di la verdad sobre la energía. Contestar 'sin electricidad' para ahorrarte preguntas mete un dato falso al catálogo."* |

**Poder decir "ninguna"** (catId 0) es lo que convierte al prompt 1 en un
portero en vez de un adivino: sin esa salida, el modelo siempre elige algo.

⚠️ **La regla 3 contradice a propósito al manual viejo**, que proponía contestar
siempre *"sin electricidad" / "sin batería"* para que no se activaran
condicionales. Es cómodo y mete datos falsos. Aquí se dice la verdad y se busca
**el valor más preciso**, que de paso cierra ramas legítimamente: "Batería no
recargable" es cierto Y evita que se pida la capacidad en mAh.

### Rendimiento medido (89 productos, 13-ago)

| Métrica | Valor |
|---|---|
| Llamadas por producto | **3** (4 si hay condicionales) |
| Velocidad | 89 productos en **6.1 min**, concurrencia 5 |
| Proyección 678 productos | ~46 min · ~2,400 llamadas |
| **Categoría: la IA corrigió la 1ª opción** | **33 de 89 = 37%** |
| Título: mediana Woo → IA | 53 → **97** caracteres (rango 80–121) |
| Atributos generados | 490 en total, mediana 5, máx 14 |
| **`vid` inventados que el validador rechazó** | **10** |
| Con condicionales activados | 13 |
| Retenidos por obligatorios sin llenar | 13 (**no se publican**) |
| Apartados sin categoría que encaje | 11 |
| Confianza media autoreportada | 0.74 |

**Los 10 `vid` inventados son la prueba de que el validador hace falta.** El
prompt dice "PROHIBIDO inventar un vid" y aun así el modelo lo hizo 10 veces en
89 productos. Sin el cotejo contra la lista cerrada, esos 10 se habrían
publicado como datos falsos sin dar error — que es exactamente el caso
`TEC-1812-NEG`.

**Los 11 apartados son aciertos, no fallos.** Muestra de lo que rechazó: un
palillo para cabello que el recomendador ponía en *Tenedores*; un kit de broches
en *Tornillos*; un protector de cuadro de bici en *Guardabarros*; un removedor
de pelo para muebles en *Cepillos para perro*. Todos se habrían publicado mal.

**Punto débil conocido:** los 13 retenidos se atoran casi siempre en los mismos
atributos textiles — *Composición*, *Composición de la fibra*, *Material
principal*. Es el 15% y está **retenido, no publicado mal**, pero conviene
afinar la segunda vuelta antes de las tandas grandes.

### El pipeline de 3 fases

Separado a propósito, misma receta que TikTok: **el contenido con IA se genera
UNA vez y se guarda en JSON local**; publicar lee de ahí. Un producto que falla
se reintenta sin volver a gastar IA.

| Fase | Script | Salida |
|---|---|---|
| 1 · Woo (sin IA) | `tm_fichas.py` | `tm_fichas.json` — precio regular, stock, peso, medidas, galería |
| 2 · IA, resumible, por tandas de 100 | `tm_enriquecer.py` | `tm_enriquecido.json` + `tm_apartados.json` |
| 3 · Publicar + bitácora | `tm_publicar3.py` | `tm_payloads3.json` + `ops.channel_submissions` |

---

## Pendientes, con dueño

| # | Qué | Dueño |
|---|---|---|
| 1 | **Corregir el precio 100×** de `JUGU-1158-VER` y `ACC-0160-AZL` en el panel | Brandon |
| 2 | Autorizar la **Price API** en Seller Center (`3000032`) → poder leer `basePrice` | Brandon |
| 3 | Autorizar las **APIs de importes** de pedidos (`3000032`) | Brandon |
| 4 | Llamar `bg.tmc.message.update` para encender el webhook | Código, **con su dale** |
| 5 | Plantilla de flete de verdad: hoy solo existe una y se llama **"test"** | Brandon (Seller Center) |
| 6 | **De dónde salen peso y volumen** (§4) | Decisión de negocio |
| 7 | Borrar los 13 productos de prueba cuando salgan de revisión | Código |
