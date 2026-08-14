# TikTok Shop MX — inventario de campos, restricciones y hallazgos

> **Para quién:** quien cargue `channel.field_requirements` y quien genere
> contenido con IA para este canal.
> **Método:** todo medido contra la API en vivo el **12–13 ago 2026**, durante y
> después de publicar 970 productos. Lo que no se pudo verificar se dice.
> **Cargable:** `TIKTOK_FIELD_REQUIREMENTS.csv` (1,779 filas) en
> `Escritorio\respaldo_tiktok_20260813`.

Sigue el modelo de tres tablas de [CONTENIDO_POR_CANAL.md](CONTENIDO_POR_CANAL.md).
Operación del canal en [TIKTOK_MANUAL.md](TIKTOK_MANUAL.md).

---

## 🔴 CORRECCIÓN AL ENCARGO — antes de nada

El encargo pedía *"confirma lo que ya sabemos: los atributos son cero
obligatorios en todas las hojas medidas, y lo único que exigen las reglas de
categoría es `package_dimension`"*.

**No se puede confirmar: es falso.** Y el error tiene una causa concreta.

TikTok marca el atributo obligatorio con la llave **`is_requried`** — escrita
así, con su propia errata. Quien midió leyó `is_required`, que **no existe en la
respuesta**, así que todo salió `false`.

**Censo completo, no muestra: las 1,521 hojas AVAILABLE, 0 errores de lectura.**

| | hojas | % |
|---|---|---|
| **CON atributos obligatorios** | **759** | **49.9%** |
| Sin obligatorios | 762 | 50.1% |

Y no se ve al crear, que es lo que lo hizo invisible tanto tiempo:
**`AS_DRAFT` no valida atributos obligatorios; `LISTING` sí.** Los 249 productos
del 11-ago "entraron sin problema" porque nadie los revisó nunca.

Sobre `package_dimension`: es correcto que las **reglas de categoría** sólo
exigen eso. La trampa es que las reglas de categoría **no son la única fuente de
obligatoriedad** — los atributos son un canal aparte, y `/categories/{id}/rules`
en MX devuelve `manufacturer.is_required` vacío aunque el LISTING lo exija.

---

# 1 · Resultados de la corrida

**Fuente: `ops.channel_submissions`, agrupando por SKU y tomando el último
intento** (es bitácora de intentos: 1,091 filas para 1,063 SKUs).

```sql
select status, count(distinct sku)
  from ops.channel_submissions
 where canal = 'tiktok' and detail_ref = 'tiktok:lote:20260812'
 group by status;
```

| | SKUs |
|---|---|
| Intentados | **1,063** |
| **Publicados** | **970** |
| Fallidos | 93 |

**Intentos por SKU:** 1,035 a la primera · 28 necesitaron dos · **ninguno más de
dos**.

## Fallidos por causa

| Causa | SKUs | ¿Recuperable? |
|---|---|---|
| Sin categoría posible en TikTok MX | 92 | **No** |
| Error interno de TikTok (`36009003`) | 1 | Reintentado 2 veces, persiste |

**Los 92 irrecuperables no son un fallo del proceso.** Son productos que TikTok
MX no admite: maniquíes y exhibidores de ropa, piezas de motor (culatas,
distribuidores, módulos de transmisión), sensores automotrices, calaveras y
faros. Las 79 hojas de "Automoción y motocicletas" del catálogo son
**accesorios** —alfombrillas, cámaras, cascos—, no refacciones. Ahí el rechazo
es la respuesta correcta.

## Activación (`detail_ref = 'tiktok:activar:20260812'`)

| | SKUs |
|---|---|
| **A la venta** | **300** ← exactamente el techo diario |
| Bloqueados por cupo (`12052093`) | 90 |
| Retenidos por falta de dato | 28 |
| Fallo al fijar stock | 1 |

## Estado del catálogo (censo en vivo)

| Estado | Productos |
|---|---|
| ACTIVATE | 282 |
| PENDING | 7 |
| FAILED (auditoría) | 11 |
| DRAFT | 597 |
| **Vivo** | **898** |

---

# 2 · Campos que TikTok exige

CSV completo: **`TIKTOK_FIELD_REQUIREMENTS.csv`** — 26 comunes con centinela
`categoria_id='*'` + **1,753 obligatorios por categoría** repartidos en 759
hojas.

`obligatorio` = lo que exige **LISTING**, no lo que tolera `AS_DRAFT`. El
esquema de la API marca `package_dimensions` y `package_weight` como `req=N` y
las reglas de publicación los exigen igual: **manda la regla, no el esquema.**

## Los comunes (`categoria_id = '*'`)

| campo_nativo | campo_canonico | oblig. | tipo | valores / límites | default | fuente |
|---|---|---|---|---|---|---|
| `title` | `titulo` | ✅ | string | **1–300** (MX y BR; resto 255) | | api |
| `description` | `descripcion` | ✅ | html | máx **10,000**; máx 30 `<img>` | | api |
| `category_id` | `categoria_id` | ✅ | string | hoja; 1,521 de 1,937 usables | | api |
| `main_images[].uri` | `imagenes` | ✅ | string | 1–9, `uri` de `images/upload` | | api |
| `skus[].price.amount` | `precio` | ✅ | string | decimal como string | | api |
| `skus[].price.currency` | — | ✅ | string | `MXN` | `MXN` | codigo |
| `skus[].seller_sku` | `sku` | ❌ | string | | | api |
| `skus[].inventory[].quantity` | `stock` | ✅ | int | **[1, 99999]** — el 0 no es válido | | api |
| `skus[].inventory[].warehouse_id` | — | ✅ | string | ⚠️ **SALES**, no el de devoluciones | `7647893424175580935` | codigo |
| `package_weight.value` | `dimensiones` | ✅ | string | **> 0 kg** | | api |
| `package_weight.unit` | — | ✅ | string | `KILOGRAM` | `KILOGRAM` | codigo |
| `package_dimensions.length/width/height` | `dimensiones` | ✅ | string | ⚠️ **L+A+H ≤ 160 cm** | | api |
| `package_dimensions.unit` | — | ✅ | string | `CENTIMETER` | `CENTIMETER` | codigo |
| `brand_id` | `marca` | ❌ | string | id de marca de la tienda | `7650172564119684872` (Ferrahome) | codigo |
| `product_attributes` | `atributos` | ❌\* | []object | \*obligatorio **por categoría** | | api |
| `save_mode` | — | ❌ | string | `AS_DRAFT` \| `LISTING` | `AS_DRAFT` | codigo |
| `is_cod_allowed` | — | ❌ | bool | | `false` | codigo |
| `size_chart` | — | ❌ | object | bloqueante en ropa/calzado | | api |
| `video` · `external_product_id` · `minimum_order_quantity` · `is_pre_owned` · `shipping_insurance_requirement` · `idempotency_key` | **sin canónico** | ❌ | varios | | | api |

**Sin equivalente canónico**, y se dice explícitamente: `is_cod_allowed`,
`save_mode`, `size_chart`, `video`, `external_product_id`,
`minimum_order_quantity`, `is_pre_owned`, `shipping_insurance_requirement`,
`idempotency_key`, `warehouse_id`, `currency`, y las tres `unit`. Son
mecánica del canal, no contenido del producto: el panel no los edita.

## Los obligatorios POR CATEGORÍA — la lista completa

Sólo existen **siete** en todo el catálogo de MX:

| atributo | id | hojas | tipo | valores | ¿quién lo contesta? |
|---|---|---|---|---|---|
| **Tipo de garantía** | `100107` | **694** | enum (4) | Garantía del proveedor · Sin garantía · Garantía del fabricante · Garantía internacional | constante → `1000054` |
| **Productos importados** | `102254` | **609** | enum (2) | Sí · No | constante → `1000058` |
| **Nombre de Fabricante Nacional/Importador** | `102268` | 146 | texto libre | — | ⚠️ **dato legal de Kubera** |
| **Dirección de Fabricante Nacional/Importador** | `102269` | 146 | texto libre | — | ⚠️ **dato legal de Kubera** |
| **Consumo de energía (V/W/Hz)** | `102270` | 146 | texto libre | — | IA, si el título lo dice |
| **Tipo de instalación** | `100795` | 11 | enum (14) | Independiente, Montaje en pared, … | IA |
| **Número de autorización del SENASICA** | `102514` | 1 | texto libre | — | trámite, nadie más |

**Reparto por hoja:** 762 con cero · 96 con uno · 516 con dos · 55 con tres ·
92 con cinco.

**450 filas nacen SIN `default_value`** → rojo en el semáforo, y así debe ser:
los dos del importador son datos legales de la empresa. Rellenarlos con algo
inventado sería declarar en falso ante SAT/PROFECO. **Es el único bloqueo real
del catálogo hoy: 59 SKUs publicados no pueden venderse por esto.**

## ⚠️ `SALES_PROPERTY` — campo aparte, como pediste

Estos **NO son descriptivos: generan variantes (SKUs distintos)**. Meterlos en
`product_attributes` rompe el producto.

| SALES_PROPERTY | hojas | % de 1,521 |
|---|---|---|
| **Color** | **1,275** | **83.8%** |
| Talla | 257 | 16.9% |
| Especificación | 237 | 15.6% |
| Edición | 46 | 3.0% |
| Tamaño de ropa de cama · Quilate | 13 c/u | 0.9% |
| Capacidad de almacenamiento · Tipo de cristal | 8 c/u | 0.5% |

**Color aparece en 8 de cada 10 hojas.** Para el modelo de datos: `color` no va
en `contenido.atributos` — necesita su propio lugar, o el publicador crea un
producto de una sola variante y el color se pierde.
`services/tiktok_atributos.py` ya los excluye (`type != "SALES_PROPERTY"`).

---

# 3 · Restricciones

| Restricción | Valor | Verificado |
|---|---|---|
| Título | **1–300** caracteres (MX/BR) | ✅ doc Create Product |
| Descripción | **10,000** caracteres | ✅ doc |
| Descripción acepta HTML | **Sí** | ✅ doc |
| `<img>` en descripción | máx 30, **sólo URLs rehospedadas por TikTok** | ✅ doc |
| `<table>` | se acepta pero **TikTok lo convierte en imagen** | ✅ doc |
| Imágenes principales | 1–9 | ✅ doc |
| Tamaño mínimo de imagen | **ninguno al subir** (100×100 acepta) | ✅ medido |
| Stock | [1, 99999] | ✅ doc + medido |
| Peso | > 0 | ✅ medido (`12052823`) |
| Suma de dimensiones | ≤ 160 cm | ✅ medido (`12052116`) |
| **Palabras prohibidas** | **NO verificado** | ⚠️ supuesto |

⚠️ **Sobre palabras prohibidas: no hay lista publicada y no la medí.** La lista
de `tiktok_contenido.py::_PROMO` está **supuesta** por analogía con la guía de
Amazon y el sentido común de marketplace (no prometer envío, garantía ni
superlativos). **Trátala como precaución, no como requisito verificado.**

## El techo diario de 300

```
12052093  Cannot upload more products today:
          your Shop Probation Period daily upload limit is `300` products
```

- **Es de la CUENTA/tienda, no del canal.** El mensaje dice *"your **Shop**
  Probation Period"*: va atado al estado de vendedor nuevo de la tienda KUBERA,
  no es una constante de TikTok MX.
- **Crear borradores NO cuenta. Activar sí.** Se crearon ~900 borradores el
  mismo día sin una sola queja; los fallos empezaron al activar.
- **Al superarlo:** la llamada falla, **el producto se queda en `DRAFT` intacto**
  y se reintenta al día siguiente. No se pierde nada, no hay que rehacer el
  payload.
- **No hay forma de consultar el contador.** Sólo se sabe cuando pega. Se
  descubrió con 62 fallos seguidos.
- **Supuesto (no verificado):** que el tope se levanta al terminar la probation.

---

# 4 · Imágenes

```
POST /product/202309/images/upload
   multipart campo "data"  ·  use_case=MAIN_IMAGE
   ⚠️ SIN shop_cipher (con él: 36009004)
   → {uri, url, width, height}
```

| Pregunta | Respuesta | Verificado |
|---|---|---|
| ¿El `uri` se puede recalcular? | **Sí: es determinista.** La misma imagen byte a byte devuelve el MISMO `uri` — es content-addressed | ✅ medido: dos subidas idénticas → `tos-alisg-i-aphluv4xwc-sg/1466144d…` las dos |
| ¿Hay que guardarlo? | **Conviene**, para ahorrarse la subida (~1–2 s por imagen), pero **no es un dato irrecuperable** | ✅ deducido de lo anterior |
| ¿Caduca? | **No observado.** Imágenes del 11-ago siguen sirviendo HTTP 200 dos días después | ⚠️ parcial: 2 días, no más |
| ¿Tamaño mínimo? | **Ninguno al subir**: 100×100 se acepta con `code=0` | ✅ medido |
| ¿Rehospeda conservando o reescala? | **Conserva exacto.** 1600→1600, 800→800, 100→100 | ✅ medido en 6 tamaños |
| ¿Dónde acaban? | `p16-oec-sg.ibyteimg.com` — TikTok nunca entra a nuestro servidor | ✅ medido |

⚠️ **El mínimo de 1000 px es NUESTRO, no de TikTok.** Lo impone
`imagenes_amazon._a_jpeg(data, 1000)`. TikTok acepta miniaturas sin protestar,
así que si alguien quita ese paso, entran imágenes de 100 px **sin un solo
error**. *(Si LISTING exige un mínimo, no lo medí — nuestras imágenes siempre
van a 1000.)*

---

# 5 · Categorías

| Dato | Valor |
|---|---|
| Total | **2,168** |
| Hojas | **1,937** |
| Hojas `AVAILABLE` | **1,521** |
| Hojas `INVITE_ONLY` | **416** |
| **Tipo del `id`** | **`string`** (ej. `"600001"`) — numérico pero se manda como texto |
| ¿Sólo se publica en hojas? | **Sí**: `12052024 Category is not final category` |

## Tasa del recomendador — confirmada

Tu medición: 49% de fallo sobre 245. **La mía, sobre 1,064:**

| | SKUs | % |
|---|---|---|
| Acertó | 491 | **46.1%** |
| **Falló** | **573** | **53.9%** |
| → rescatados por la IA | 481 | |
| → irrecuperables | 92 | |

**Confirmado y algo peor a mayor escala.** El respaldo de IA no es un lujo:
salvó 481 productos, la mitad del lote.

⚠️ **Y el fallo tiene una segunda cara que no estaba medida: el recomendador
también acierta con confianza en la categoría EQUIVOCADA.** Caso real:
*"Collar de recuperación para gato"* (cono veterinario) → **Accesorios de moda →
Joyas y accesorios para disfraces → Collares**. No da error, no se marca como
aproximado, y el producto queda vivo y mal clasificado. Los 227 marcados
`categoria_aproximada` **no cubren estos**.

## INVITE_ONLY — qué hace en la práctica

Sabíamos que no bloquea el borrador (`COM-0081-ROS` publicó). **Ahora sabemos
qué pasa al activar, y es peor que un rechazo:**

**24 productos quedaron en categorías INVITE_ONLY. Ninguno llegó a `ACTIVATE`.
Los 7 `PENDING` de toda la tienda son exactamente esos.**

| Estado | INVITE_ONLY |
|---|---|
| ACTIVATE | **0** |
| PENDING | **7** ← el 100% de los PENDING de la tienda |
| DRAFT | 17 |

**INVITE_ONLY no rebota la activación: la acepta y deja el producto parado en
`PENDING` indefinidamente.** No hay error, no hay aviso, y desde fuera parece
que está "en revisión". Ejemplos: `Utensilios para bebés`, `Pestañas postizas`,
`Tratamiento nasal`, `Iluminación`.

**Regla operativa: filtrar INVITE_ONLY ANTES de ofrecer la categoría**, que es
lo que ya hace `tk_categoria_ia.candidatas()`. Los 24 se colaron por el
recomendador de TikTok, que sí las ofrece.

---

# 6 · El prompt de IA

Completo en
[TIKTOK_PUBLICAR_DESDE_EL_PANEL.md](TIKTOK_PUBLICAR_DESDE_EL_PANEL.md#parte-3--el-prompt-para-mejorar-un-listing).
El validador vive en **`backend/services/tiktok_contenido.py`** — hermano de
`amazon_contenido.py`, mismo contrato.

**La IA propone; el código decide.** Lo que valida el código:

| Comprobación | Por qué |
|---|---|
| Título ≤ 300, descripción ≤ 10,000 | **Se rechaza, no se trunca**: cortar a media palabra queda peor que el original |
| Lista blanca de HTML | `<table>` se convierte en imagen y el texto deja de ser editable |
| `<img>` con aviso propio | sólo URLs rehospedadas; una de chunche.shop se rechaza **al publicar**, no al guardar |
| Sin emojis, sin MAYÚSCULAS sostenidas | |
| Promesas no verificables | ⚠️ lista **supuesta**, ver §3 |
| `flags` de la IA ⇒ descarte | un dato inventado **no da error**: se publica y nadie sabe cuál era mentira. Caso real en atributos: puso `"1.5V"` y anotó *"voltaje no confirmado"* |
| **El título conserva palabras del original** | La que más importa. Un título impecable de forma puede describir OTRO producto — es el modo de fallo del cono para gato |

Y `validar_publicable()` aparte, para los bloqueantes que **no** son contenido
(stock, peso, dimensiones, imágenes): vienen de Woo, no de la IA, y son los que
hacen rebotar un borrador que se veía perfecto.

---

# 7 · Trampas que NO dieron error

Las tres conocidas (el ordenamiento que tiraba *Relojes inteligentes*, el
almacén de devoluciones primero en la lista, `page` que siempre devuelve la 1)
siguen vigentes. **Éstas son nuevas:**

### 1. `is_requried` — la errata que borró la mitad de los requisitos
Leer `is_required` devuelve `false` para todo. **759 hojas con obligatorios
quedaron documentadas como si no tuvieran ninguno.** Silencioso hasta que
alguien intenta vender.

### 2. `code=0` no prueba que la escritura ocurrió
Dos veces el mismo día, con respuesta `Success` y `data:{}` vacío:
- una suscripción de webhook que **no quedó** (repetir el PUT idéntico la creó);
- **~100 borrados que no borraron nada** — y siguen sin borrarse.

Descartado midiendo: no es tope diario (7 duplicados se borraron justo después),
no es antigüedad, y `DELETE /products/{id}` no existe (`36009010`). Lo único que
distingue a los que resisten: **`has_draft=true`**.
**Verificar el estado después de escribir. Siempre.**

### 3. `AS_DRAFT` no valida lo que `LISTING` sí
La trampa más cara. Un lote entero puede verse perfecto en borrador y rebotar
completo al activarse. **Un semáforo verde para borrador NO significa
publicable.**

### 4. `products/search` devuelve los `DELETED`
Sin filtrarlos, el conteo de "vivos" sale inflado y los ya borrados reaparecen
en cada censo como si fueran pendientes.

### 5. La paginación del censo se desfasa si algo escribe a la vez
`products/search` se pagina mientras el publicador inserta: el cursor se corre y
**algunos productos no aparecen**. El publicador los cree faltantes y los manda
otra vez → **4 SKUs acabaron publicados 2–3 veces (7 copias)**. Censar con las
tandas detenidas, o dar por hecho que habrá que barrer duplicados.

### 6. Un atributo repetido pasa en borrador y rebota al vender
`12052254 … each product attribute ID must appear only once`. **40 de 1,221
payloads**: la IA propone el mismo atributo con dos valores y
`tiktok_atributos.validar` los agrega como entradas sueltas. Lo correcto para un
multi-valor es **una entrada con varios `values`**.
⚠️ **Sigue sin arreglar en el backend**: hoy sólo lo parcha
`tk_activar.py::fundir_repetidos`, así que **todo lote nuevo lo arrastra**.

### 7. El verbo cambia por endpoint y la doc no lo dice
Los volcados dicen `METHOD: 1` en todos. Medido: webhooks `PUT`,
`inventory/update` `POST`, editar producto `PUT`, `DELETE /products/{id}` no
existe. Único síntoma: `36009010`.

### 8. `/products/activate` no sirve para borradores
Es para `Seller_deactivated` / `Platform_deactivated` — productos que ya
estuvieron vivos. Un borrador se publica **reeditándolo** con
`save_mode=LISTING`, lo que obliga a **conservar el payload completo**.

### 9. TikTok no impone mínimo de imagen
Acepta 100×100 sin protestar (§4). Quien quite nuestro paso de 1000 px no verá
ni un error.

---

# 8 · El registro

Cumplido: **`registrar()` en `tk_publicar.py`**, en su propio `try` — un fallo de
Supabase nunca aborta el lote.

| detail_ref | operación | filas | SKUs |
|---|---|---|---|
| `tiktok:lote:20260811` | create_product | 369 | 252 |
| `tiktok:lote:20260812` | create_product | 1,091 | 1,063 |
| `tiktok:activar:20260812` | activate_product | 423 | 417 |

Las filas superan a los SKUs porque **es bitácora de intentos**: 28 SKUs
aparecen dos veces. Contar filas inflaría el resultado un 2.6%.

El error va **completo**, con código y mensaje literal:

```
armado: sin categoría: la IA no encontró categoría: No hay categoría para
filtros de cabina en la lista de candidatas
code=12052093 Operation Not Allowed. Cannot upload more products today: your
Shop Probation Period daily upload limit is `300` products
```

---

## Lo que falta, y a quién le toca

1. **Razón social y dirección fiscal de Kubera** como Fabricante Nacional/
   Importador → desbloquea 59 SKUs. **Brandon.** Es el único bloqueo real.
2. **Arreglar `tiktok_atributos.validar`** para que funda por `id`. Mientras no
   esté, cada lote nuevo arrastra el bug.
3. **Cargar el CSV** a `channel.field_requirements`.
4. **Detectar categorías mal asignadas con confianza** (§5): una llamada de IA
   por producto juzgando *título vs categoría*, sin tocar TikTok.
5. **Medir la lista de palabras prohibidas** en vez de suponerla (§3).
