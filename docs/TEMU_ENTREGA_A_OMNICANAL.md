# Temu → Omnicanal: el publicador, con la evidencia que lo respalda

> **Estado:** corrió en vivo el 13 y 14-ago-2026 y dejó **201 productos
> publicados** (catálogo de Temu: 351 reales). Todo lo de aquí está medido
> contra la API, no inferido. Lo que no se pudo verificar va marcado.
>
> Hermano de [TIKTOK_ENTREGA_A_OMNICANAL.md](TIKTOK_ENTREGA_A_OMNICANAL.md).
> El detalle campo por campo vive en [TEMU_HALLAZGOS.md](TEMU_HALLAZGOS.md);
> la corrección del precio, al inicio de [TEMU_MANUAL.md](TEMU_MANUAL.md).

---

## 1 · EL ALTA QUE FUNCIONÓ — payload exacto y `goodsId`

`temu.local.goods.v3.add`. Este es el cuerpo **literal** de un alta que pasó, y
el id que devolvió. Sirve para cotejar contra cualquier otro generador:

**`ACC-0017-MUL` → `goodsId: 608007295444247`**

```jsonc
{
  "language": "es",
  "goodsBasic": {
    "goodsName": "Pinzas para cabello de metal dorado con diseño de flores, set de 6 piezas de 11 cm para mujer y niña",
    "goodsDesc": "Set de pinzas para cabello con diseño de flores…",
    "externalGoodsId": "ACC-0017-MUL",
    "extCatName": "19287",          // ← id de HOJA como STRING. `catId` NO existe en v3
    "shipmentLimitDay": 2,
    "costTemplate": "LFT-18510029444014331627",
    "productType": 1,
    "bulletPoints": ["Set de 6 pinzas con diseños florales distintos…", "…"]
  },
  "attributes": [                    // ← RAÍZ, y {name, value[]} de TEXTO (no pid/vid)
    {"name": "Material",               "value": ["Aleaciones"]},
    {"name": "Grupo de edad aplicable","value": ["3 años+"]},
    {"name": "Fuente de alimentación", "value": ["Uso sin electricidad"]}
  ],
  "skuList": [{
    "externalSkuId": "ACC-0017-MUL",
    "images": ["https://img.kwcdn.com/local-goods-image-g/…"],   // ya rehospedadas
    "price": {"basePrice": {"amount": "182.39", "currency": "MXN"}},  // DECIMAL
    "quantity": 100,
    "packageInfo": {"weight": "250", "length": "15", "width": "15", "height": "6"},
    "variations": [{"name": "Color", "value": "MUL"}]
  }]
}
```

**Las cinco diferencias contra un payload v1**, que es donde se pierde la gente
(v3 **ignora en silencio** lo que no conoce, sin dar error):

| v1 (lo que NO funciona en v3) | v3 (lo que sí) |
|---|---|
| `goodsBasic.catId` (int) | `goodsBasic.extCatName` (string) |
| `goodsProperty.goodsProperties[]` con pid/vid | `attributes[]` en la RAÍZ, `{name, value[]}` |
| `skuList[].productExpressInfo` | `skuList[].packageInfo` |
| `goodsServicePromise.costTemplateId` | `goodsBasic.costTemplate` |
| `skuList[].specIdList` | `skuList[].variations[]` |

Prueba de que los ignora: se pidió `catId` 1761 y publicó en **1769**; se mandó
500 g / 20×20×20 en `productExpressInfo` y guardó el default **100 g / 10×20×30**.

---

## 2 · LOS ERRORES, TAL CUAL

Los dos útiles son los que nombran el campo. Medidos uno por uno quitando o
rompiendo un campo a la vez:

| Código | Qué significa | Ejemplo literal |
|---|---|---|
| `150011003` | **nombra el campo** entre corchetes | `Invalid Request Parameters [basePrice]` · `[currencyCode]` |
| `150011055` | campo obligatorio ausente, lo nombra | `goodsBasic is required fields.` |
| `3000000` | error de tipo, **da la ruta completa** | `skuList[0].price.basePrice type error` |
| `150011018` | decimales de más | `Price currency MXN can have at most 2 decimal points.` |
| `150011019` | valor rechazado, lo repite entero | `The input basePrice:{"amount":"164.02",…} is incorrect` |
| `150010030` | regla de precio (bajo el mínimo 0.02) | `Price input error` |
| `150010090` | **SKU quemado, permanente** | `SKU duplicated` |
| `150010003` | parámetros inválidos, **sin decir cuál** | también sale con SKUs repetidos en `out.sn.check` |
| `3000032` | falta permiso (lista blanca) | `access_token don't have this api access…` |
| `7000000` | endpoint muerto o no habilitado | `BUSINESS_SERVICE_ERROR` |

⚠️ **`success: true` no siempre significa éxito.** `temu.local.goods.delete`
devuelve `errorCode 1000000` por fuera y el veredicto real va **anidado** en
`result.success`, que puede traer *"data under review cannot be deleted"*.
Leer siempre el de adentro.

⚠️ **Campos `Required=True` que NO se validan.** Se probaron 12 altas quitando
un campo cada vez: **las 12 publicaron**. `packageInfo`, `variations` y
`quantity` están documentados como obligatorios y no lo son. Sin `packageInfo`
el producto sale con **100 g y 10×20×30 cm** — y Temu cobra volumétrico. Los
únicos que de verdad se exigen son `goodsName`, `externalGoodsId`,
`externalSkuId`, `images` y `price.basePrice`.

---

## 3 · LAS SPECS — hueco cerrado

Era el hueco abierto de la revisión anterior: los 152 productos vivos usan
`specIdList: [77556301]` ("Standard") y el payload v3 no tiene campo para
expresarlo. **No hace falta:** v3 las resuelve **por nombre** con `variations`.

```jsonc
"variations": [{"name": "Color", "value": "MUL"}]
```

Y Temu acuña o reutiliza el `specId` solo. Leído de vuelta en lo que
publicamos:

| SKU | `specIdList` | `specNameList` |
|---|---|---|
| ACC-0017-MUL | `[98686244]` | `["MUL"]` |
| ACC-0124-MUL | `[98686244]` | `["MUL"]` (reutiliza el mismo) |
| ACC-0147-CAF | `[142257355, 313460181]` | `["CAF", "As shown"]` |
| ACC-0148-NEG | `[129501332, 313460181]` | `["NEG", "As shown"]` |

`sku.list.query` confirma la jerarquía: `specId 98686244` cuelga de
`parentSpecId 1001` = **Color**.

> **NO hace falta `bg.local.goods.spec.id.get`**, que la revisión anterior marcó
> en rojo por oler a get-or-create. Se evita entero.
>
> El `313460181 "As shown"` que aparece en algunos lo agrega **Temu**, no
> nosotros: es la opción *"Especificaciones de llenado automático"* del Seller
> Center, que rellena specs vacías con `Color: como se muestra`.
>
> Diferencia con M2E: ellos metieron todo bajo una sola spec "Standard";
> nosotros generamos una por sufijo de SKU, que es la variante real.

---

## 4 · LA PLANTILLA DE FLETE — no se tocó

`bg.freight.template.list.query` devuelve **una sola**:

```json
{"templateList": [{"templateName": "test", "templateId": "LFT-18510029444014331627"}]}
```

**No hay alternativa por API.** Los 201 productos nuevos cuelgan de ella, igual
que los 152 de M2E. Crear una plantilla de verdad es tarea de Seller Center
(Brandon), no de código.

Dato aparte: `costTemplate` **es opcional** — omitiéndolo el producto se publica
igual y Temu aplica la plantilla default de la tienda, que hoy es la misma
"test". O sea, no hay forma de escaparse de ella desde el publicador.

---

## 5 · EL PRECIO Y LAS COMISIONES — para el dashboard de márgenes

```
mandamos    basePrice    ← esto es lo que NOS LIQUIDAN
Temu calcula retailPrice = basePrice × factor   ← esto paga el cliente
diferencia  = la comisión. NO hay línea aparte
```

**`amount` va en DECIMAL de 2 cifras, no en centavos.** Se escribe decimal y se
lee en centavos en `detail.query` (decimal en `list.query`). Mandar centavos
publica a **100×**: `ACC-0160-AZL` llegó a tener anaquel de **$31,251.87**.

### El factor, medido sobre nuestros propios productos

| SKU | base | anaquel | factor |
|---|---|---|---|
| ACC-0017-MUL | $182.39 | $249.78 | 1.3695 |
| ACC-0050-MUL | $302.39 | $413.55 | 1.3676 |
| ACC-0124-MUL | $215.26 | $294.64 | 1.3688 |
| ACC-0147-CAF | $247.71 | $338.93 | 1.3683 |
| JUGU-0156-MUL | $200.00 | $273.82 | 1.3691 |

**Mediana 1.3688, dispersión 0.2%** → Temu se queda con el **26.9% del anaquel**.

> 🔴 **El factor NO es único.** Los productos viejos de M2E salen en ~1.13
> (`MUE-0293-AZL` da 1.1431). Hay **tabla de comisiones** por categoría o por
> cohorte. Un dashboard que asuma una constante va a mentir: hay que **guardar
> el factor medido por producto**.

### Y Temu NEGOCIA el base

`bg.local.goods.priceorder.query` con `priceOrderType=2` sobre `COM-0081-ROS`:

```json
{"sourceSupplierPrice": {"amount": "119.43"},   // lo que mandamos
 "suggestSupplierPrice": {"amount": "104.53"},  // lo que Temu contrapone (−12.5%)
 "status": 201}                                  // "Through" = aprobado
```

Estados del flujo (de la *Price Management Guide*): `100` en revisión ·
**`101` esperando confirmación del vendedor** ← aquí se actúa · `201` aprobado ·
`202` rechazado pendiente de modificar · `203/204/205` confirmó/modificó/rechazó
el vendedor · `206` rechazado.

Acciones disponibles y **con scope**: `bg.local.goods.priceorder.accept`,
`temu.local.goods.priceorder.reject`, `bg.local.goods.priceorder.negotiate`.

### Cómo cachear los precios en Omnicanal

No hace falta DDL: **`channel.listings` ya tiene `price` y `price_base`**, y la
cuenta `TEMU` está dada de alta (`external_id` = mallId 635517742093915).

| Dato | De dónde | Estado |
|---|---|---|
| `price` (anaquel) | `bg.local.goods.list.query`, cubetas 1/4/5 | ✅ ya lo carga `scripts/cargar_temu.py` |
| `price_base` | `bg.local.goods.sku.list.price.query` | ❌ `3000032` — lista blanca |
| **`price_base`, puerta de atrás** | **`priceorder.query`** expone `supplierPrice` | ✅ **funciona sin permiso especial** |
| `price_base` de lo nuestro | el payload que mandamos | ✅ |

> ⚠️ **De 145 productos publicados, solo 13 tenían anaquel asignado** al
> momento de medir. Temu lo calcula poco a poco, así que el job tiene que ser
> recurrente y el panel mostrar "pendiente de tasación" mientras tanto.

---

## 6 · LO QUE SE SUBIÓ AL BACKEND

| Archivo | Qué es | Estado |
|---|---|---|
| `services/temu.py` | Cliente async con firma MD5 (tipos nativos) | ya existía |
| **`services/temu_contenido.py`** | Prompts + validadores + **la cascada** (`duros`, `activados`, `faltantes`) | **nuevo** |
| **`scripts/publicar_temu.py`** | El publicador de 3 fases | **nuevo** |
| `scripts/cargar_temu.py` | Catálogo vivo → `channel.listings` | ya existía |
| `config.py` | `settings.temu_app_key/secret/access_token/api_base` | ya existía |

```bash
python -m scripts.publicar_temu fichas --skus lista.txt
python -m scripts.publicar_temu enriquecer --tanda 1
python -m scripts.publicar_temu publicar --aplicar
```

**Por qué tres fases:** el contenido con IA son 3 llamadas a DeepSeek por
producto. Generándolo aparte y guardándolo, un producto que falla se reintenta
**sin volver a gastar IA**. Las tres son resumibles.

### La bitácora — `ops.channel_submissions`

Una fila **por intento**, pase lo que pase: alta OK, error de la API, fallo al
armar el payload, fallo de imágenes y aviso de vocabulario.

```
canal 'temu' · cuenta 'KUBERA' · operacion 'create_product'
status 'published' | 'failed' | 'pending' · detail_ref 'temu:lote:20260814'
error_resumen = errorCode + errorMsg LITERALES (2000 chars)
```

1. **Bitácora de INTENTOS, no de estado** — INSERT plano. Un SKU que falló y
   luego publicó sale dos veces; al contar, agrupar por SKU y tomar el último.
2. **El error completo**, nunca un resumen propio.
3. **Registrar nunca aborta el lote** — cada escritura en su `try`.

```sql
select status, count(distinct sku) from ops.channel_submissions
 where canal='temu' and detail_ref='temu:lote:20260814' group by status;
-- published 195 · failed 4 · pending 8   (208 filas de intento)
```

---

## 7 · AUDITORÍA — qué cambié sobre la marcha y por qué

| # | Cambio | Por qué |
|---|---|---|
| 1 | Payload v1 → **esquema v3 real** | v3 ignoraba en silencio catId, atributos y medidas |
| 2 | Precio de centavos → **decimal** | publicaba a 100× |
| 3 | Se **quitó `listPrice`** | debe ser estrictamente mayor que base; igual se descarta en silencio |
| 4 | Categoría: 1ª opción → **IA elige entre las 5 de `catIdList`** | la 1ª se equivoca en ~1 de cada 3 |
| 5 | Atributos: 1 vuelta → **2 (cascada de condicionales)** | los condicionales vacíos mandan el producto a Borrador |
| 6 | Vocabulario: bloqueo duro → **solo si el aviso no dice "will not block"** | estaba rechazando altas que Temu aceptaba |
| 7 | Prompt: **lista de marcas genérizadas** (velcro, kleenex, ziploc…) | "velcro" dispara *Potentially Infringing Terms* |
| 8 | `out.sn.check`: **deduplicar** el lote | repetidos dan `150010003` |
| 9 | Filtro de negocio **antes** de la IA | enriquecer algo que se va a descartar gasta 3 llamadas |
| 10 | **Stock refrescado en vivo** desde Woo al publicar | la foto envejece; vender sin stock = cancelación |
| 11 | **Piso de precio $10** | `CTL-0520-NEG-BLN-AC110` tiene $1.00 en Woo siendo un control remoto |
| 12 | Se quitó "regalo" de las palabras promocionales | falso positivo: Temu tiene un atributo *"Ocasión para regalar"* |

### Rendimiento del pipeline (medido)

| | |
|---|---|
| Fichas de Woo | 866 |
| Enriquecidos | 269 → **229 listos**, 40 retenidos, 31 apartados |
| **Publicados** | **201** · 4 fallidos (todos vocabulario, **0 SKUs quemados**) |
| Velocidad IA | 89 productos en 6.1 min (concurrencia 5) |
| Categoría corregida por la IA | **33 de 89 = 37%** |
| `vid` inventados que el validador rechazó | **10** |

Los **31 apartados** son aciertos: un palillo de cabello que iba a *Tenedores*,
un protector de cuadro de bici a *Guardabarros*, un removedor de pelo para
muebles a *Cepillos para perro*.

---

## 8 · LO QUE FALTA, CON DUEÑO

| # | Qué | Dueño |
|---|---|---|
| 1 | 🔴 **Corregir el precio 100×** de `JUGU-1158-VER` y `ACC-0160-AZL` (este último ya con anaquel de $31,251.87) — *Incompleto → Editar* | Brandon |
| 2 | Autorizar la **Price API** (`3000032`) para leer `basePrice` | Brandon |
| 3 | Autorizar las **APIs de importes** de pedidos (`3000032`) | Brandon |
| 4 | **Plantilla de flete de verdad** — la única se llama "test" | Brandon |
| 5 | ¿`basePrice` = precio de Woo? Hoy cobramos ~26.9% menos que el anaquel | Decisión de negocio |
| 6 | De dónde salen **peso y volumen** (37 cubos perfectos = huella del CBM) | Decisión de negocio |
| 7 | Encender el webhook (`bg.tmc.message.update`) | Código, **con su dale** |
| 8 | Job de caché de `price_base` vía `priceorder.query` | Código |
| 9 | Borrar los 14 productos de prueba cuando salgan de revisión | Código |
| 10 | Afinar el 15% que se atora en *Composición* / *Composición de la fibra* | Código |

> ⚠️ **Temu NO debe entrar al fan-out de stock todavía.** `status4VO`/
> `subStatus4VO` se guardan crudos porque no hay documentación de qué significan
> y los códigos del sondeo del 12-ago ya no coinciden con los de hoy. Sin saber
> cuál de esas publicaciones vende, atar el fan-out sería el error de TikTok.
