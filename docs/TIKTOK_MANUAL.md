# TIKTOK SHOP MX — manual operativo

Canal por **API propia** desde el 7-ago-2026 (antes entraba por M2E Cloud, cuya
conexión llevaba `is_valid=false` desde julio). **Esta es la única vía: no hay
respaldo detrás.**

Última verificación en vivo: **12-ago-2026**.

---

## Estado actual

| | |
|---|---|
| Tienda | **KUBERA** · shop_id `7494659908378395724` · región MX · `seller_type LOCAL` |
| App | `tiktotapi` · service_id `7670376320522307345` · app_key `6ks3s52l39pt5` |
| Catálogo vivo | **898 productos** (12-ago) |
| A la venta (`ACTIVATE`) | **282** · +8 `PENDING` en auditoría |
| En borrador | 597 |
| Rechazados por auditoría | 11 (casi todos refacciones de auto) |
| Webhooks suscritos | **5** |
| Precio y stock | verificados 44/44 contra Woo, exactos |
| ⚠️ Techo diario | **300 publicaciones/día** (probation) — ver abajo |

**Regla de oro del canal (12-ago): sólo se publica lo que tiene stock.** Un
producto a la venta sin existencia sólo cosecha cancelaciones. Se borraron 523
borradores en cero; los ~100 que la API se negó a borrar quedan para Seller
Center.

`seller_type = LOCAL` ⇒ las APIs `global_product_*` **NO sirven** (son para
vendedores intra-UE). Usar las de producto normales.

---

## El publicador

`scratchpad\tk_publicar.py` — respaldado en `Escritorio\respaldo_payloads_20260812`.

```
python tk_publicar.py --limite 5      # piloto, en borrador
python tk_publicar.py --listing       # publica de verdad
python tk_publicar.py --reintentar    # solo los que fallaron
```

El flujo, y por qué cada paso existe:

1. TikTok **recomienda** la categoría desde el título → evita mapear 1,937 hojas
2. se piden los **atributos reales de esa hoja** → nunca una lista fija
3. la IA los llena **eligiendo de las listas cerradas**
4. **el código valida** contra esas listas → la IA propone, no decide
5. se suben las imágenes (TikTok las rehospeda)
6. se crea el producto

El payload se **escribe a disco antes de mandarse** (`tk_payloads.json`): si un
producto falla se corrige el JSON y se reintenta solo ése, sin regenerar con IA.
Cada SKU se guarda al terminar, así que es reanudable. Un fallo **nunca** aborta
el lote.

---

## 🔴 EL RECOMENDADOR DE CATEGORÍA FALLA EL 49%

Medido sobre 245: **acierta 125, falla 120**. Y no con títulos raros —
*"hervidor eléctrico"*, *"funda para iPad"*, *"lentes de sol"*, *"posavasos"*.
Error: `12019064 … the product name does not match any category`.

**El respaldo de IA es obligatorio, no un lujo: es la mitad del catálogo.**

### De dónde salió cada categoría, en el lote real

| Origen | SKUs |
|---|---|
| Recomendador de TikTok | 125 |
| IA por solape de palabras | 70 |
| IA navegando ramas (marcadas APROXIMADAS) | 54 |
| Sin categoría posible | 3 |

### Los tres bugs que ya están corregidos — NO reintroducirlos

Los tres compartían la misma firma: **cuando la IA rechazaba todas las
candidatas, la IA tenía razón.** El eslabón débil era la búsqueda.

**1. Sin *stemming* no encuentra nada.** El título dice "hervidor" y la
categoría "Hervidores"; "funda" vs "Fundas". Comparando palabras exactas el
plural rompe el 100% de las coincidencias. Con raíz de 5 caracteres: solo 6 de
120 se quedan sin candidatas.

**2. Pedir UNA sola rama se atasca.** Varias ramas son delgadas: Muebles 40
hojas `AVAILABLE`, Joyería 21, Salud **1**, y *Bebés y maternidad* y
*Alimentos* **CERO**. Peor: la rama intuitiva no siempre es donde TikTok puso el
producto — los piercings parecen "Accesorios de joyería" (que es de INSUMOS) y
la hoja buena vive en "Accesorios de moda → Joyas para el cuerpo". **Pedir 3
ramas y mezclar.**

**3. ⚠️ NO reordenar la mezcla por parecido de palabras.** Es el bug más caro y
no daba error. Caso `TEC-1337-NEG` *"Smartwatch **Deportivo** Negro"*: la IA
eligió bien `Teléfonos y electrónica`, que SÍ tiene `Relojes inteligentes` —
pero *"Deportivo"* empujaba arriba "Sombreros de deportes" y la hoja correcta
**no entraba en el corte de 150**. La IA respondía *"ninguna candidata
corresponde a relojes"*, con razón.

> **Cuando la IA ya eligió la rama, esa elección pesa más que cualquier
> heurística de palabras.** Rama #1 completa primero, tope 260 (la mayor,
> Deportes, tiene 215). **Y ojo con recortar DOS VECES**: el publicador volvía a
> cortar a 120 y anulaba el arreglo.

### Umbrales

- **0.6** en la búsqueda por solape
- **0.3** en la navegación de dos pasos — ahí la IA ya navegó dos veces, así que
  una confianza media significa *"es lo más próximo"*, no *"no sé"*. Decisión de
  Brandon: mejor publicado en la más próxima que sin publicar. Se marcan
  `categoria_aproximada: true` para revisión humana.

⚠️ **416 de las 1,937 hojas NO están `AVAILABLE`** para esta tienda. Ofrecerlas
como candidatas es rechazo seguro. (2,168 categorías totales; solo en hojas se
publica. Guardadas en `tk_categorias.json`.)

⚠️ `INVITE_ONLY` **no bloquea crear el borrador**: `COM-0081-ROS` publicó en una
hoja de "Bebés y maternidad". Probablemente bloquea la activación, no el draft.

---

## Lo que NO tiene solución

**TikTok MX no tiene categoría de refacciones mecánicas.** Sus 79 hojas de
*"Automoción y motocicletas"* son puros ACCESORIOS (alfombrillas, cámaras,
cascos), y en todo el árbol solo hay "piezas" para electrodomésticos y pelucas.

Quedaron fuera por eso: `TEC-1587-PLA` (filtro de cabina),
`TEC-1119-MET-TSURU` (distribuidor), `TEC-1303-AZL-12V` (relevadores Arduino) y
`OFI-0057-NEG` (pistón de silla). **Ahí el rechazo ES la respuesta correcta.**

---

## Atributos: cero obligatorios

`GET /product/202309/categories/{id}/attributes` — medido en varias hojas:
**8 atributos, CERO obligatorios**. Listas cerradas cortas (Material, Volumen,
Cantidad por paquete…). Llenarlos ayuda a los filtros; omitirlos **no** tumba el
artículo.

⚠️ **`Color` viene como `SALES_PROPERTY`, no `PRODUCT_PROPERTY`**: ese es el eje
de variantes, el que genera SKUs distintos dentro del mismo producto. No es
descriptivo.

⚠️ **La IA rellena datos que ella misma admite no saber.** Caso real: puso
`"1.5V"` y en la MISMA respuesta anotó *"voltaje no confirmado en descripción"*.
Pasaba el validador porque el atributo es texto libre. **Un dato inventado NO da
error: se publica**, y después nadie sabe cuál era mentira. Regla: lo que la IA
marque en `flags` NO se manda.

**Reglas de categoría** (`/categories/{id}/rules`): lo único obligatorio es
`package_dimension`. `manufacturer`, `epr` y las certificaciones son opcionales.
`cod` no soportado.

---

## Imágenes

```
POST /product/202309/images/upload
   multipart, campo "data"  ·  data: use_case=MAIN_IMAGE
   ⚠️ SIN shop_cipher — con él responde
      36009004 "The 'shop_cipher' query parameter is not required"
   → {uri, url, width, height}
```

El `uri` es lo que va en `main_images`. **TikTok rehospeda**: las imágenes
acaban en `p16-oec-sg.ibyteimg.com`, así que TikTok nunca entra a nuestro
servidor.

⚠️ **Hay que CONVERTIR antes de subir.** Dos cosas distintas lo obligan:

1. El catálogo trae `.webp` **de verdad** (así vinieron de Alibaba) — 3 de los 5
   primeros productos fallaron por exigir JPEG.
2. chunche.shop entrega **WEBP disfrazado de `.jpg`** según la cabecera `Accept`.

**Reusar `imagenes_amazon._descargar` + `_a_jpeg(data, 1000)`**: ya hace Lanczos
a 1000 px y trae las cabeceras que sortean el WAF de Hostinger. Escribir otro
conversor es cómo se acaba arreglando el mismo bug dos veces.

**Resultado del lote:** 42 productos con menos de 3 fotos, pero **casi todos son
así en Woo**. Fallos REALES de subida: 4 productos / 7 imágenes
(`ACC-0160-AZL`, `ILUM-0089-PLA`, `JUGU-0179-ROS`, `JUGU-0176-AZL-ROS`). El tope
de 5 por producto es decisión nuestra, no de TikTok.

---

## 🏬 DOS almacenes — el inventario va al de VENTAS

```
7647866321003104008  RETURN_WAREHOUSE  ← NO usar
7647893424175580935  SALES_WAREHOUSE   ← este
```

Tomar el primero de la lista es un error fácil: **el de devoluciones viene
antes**. Declarar stock ahí sería ponerlo donde nadie compra.

---

## Firma de las llamadas salientes

TikTok NO se conforma con el token: firma cada llamada
(`services/tiktok.py::_firmar()`):

1. query params MENOS `sign` y `access_token`
2. ordenados por nombre, concatenados `clave+valor` sin separadores
3. se antepone la RUTA, se anexa el cuerpo crudo
4. se envuelve con el `app_secret` a ambos lados
5. HMAC-SHA256 con el `app_secret`, en hex

Token en el header **`x-tts-access-token`**. `timestamp` en segundos, ventana
[ahora−5 min, ahora+30 s] — fuera de ahí, `36009004 Invalid timestamp`.

---

## Webhooks — ✅ 5 suscritos (12-ago-2026)

Estuvieron en 0 desde el 7-ago. Se dieron de alta con `tk_webhooks_alta.py`
(dale de Brandon, 12-ago). Ahora TikTok **sí** manda eventos al receptor
`/api/webhooks/tiktok`.

⚠️ **El verbo es `PUT`, no `POST`.** Con POST responde `36009010 Invalid method`
— ese error hizo concluir, mal, que no se podía por API. Sí se puede:

```
PUT /event/202309/webhooks     body: {address, event_type}
```

`shop_cipher` es **obligatorio** (sin él: `106013 Missing identifier`), hay que
**firmar el cuerpo**, y va **una llamada por topic**. El scope
`seller.authorization.info` resultó **no** ser un requisito aparte: ya lo
teníamos.

### 🔴 `code=0` NO significa que quedó suscrito

Los 4 PUT respondieron `Success` y al listar solo aparecieron **3**:
`UPCOMING_AUTHORIZATION_EXPIRATION` faltaba. Repetido el PUT **sin cambiar
nada**, entró. Es retraso de propagación de TikTok, no un permiso que falte —
y la conclusión fácil ("falta el scope") habría mandado a pedir un permiso que
ya estaba concedido.

**Por eso el script verifica contra el listado y reintenta lo que no aparezca.**
Fiarse del acuse deja el canal justo sin el aviso de expiración, que es el que
evita que se muera en silencio como M2E.

⚠️ **Suscribir es un efecto de verdad, aunque parezca un sondeo.** Probar
nombres de evento contra la URL real los DEJA VIVOS: así se colaron
`PRODUCT_STATUS_CHANGE` (borrado después) y `SELLER_DEAUTHORIZATION` (se dejó:
avisa que el canal murió). Para sondear, usar una URL inválida a propósito —
como hace `tk_webhook_probe.py`.

### Eventos que importan

| # | Evento | Para qué |
|---|---|---|
| 1 | `ORDER_STATUS_CHANGE` | **la venta** |
| 11 | `CANCELLATION_STATUS_CHANGE` | cancelación → devolver stock |
| 12 | `RETURN_STATUS_CHANGE` | devolución → devolver stock |
| 6 | `SELLER_DEAUTHORIZATION` | **el canal se murió** |
| 7 | `UPCOMING_AUTHORIZATION_EXPIRATION` | avisa **30 días antes**. Vale oro: evita que el canal se caiga en silencio, como pasó con M2E |

El evento de venta es **flaco**: trae `order_id` y `order_status`, nada más. Ni
SKU, ni precio, ni comisión — hay que pedirlos después.

### ⚠️ La firma ENTRANTE — el bug que habría tirado todo

```
HMAC-SHA256( app_key + cuerpo_crudo , app_secret )   → hex
cabecera: Authorization
```

**El prefijo `app_key` no es adorno.** La primera versión firmaba solo el
cuerpo. En modo observar solo deja un veredicto equivocado en el log, pero al
pasar a **rechazar** habría tirado **el 100% de los eventos legítimos**, con el
síntoma *"dejaron de entrar ventas"* y ningún error a la vista. Corregido en
v0.104.0.

El cuerpo tiene que ser el **crudo**, byte por byte: re-serializar el JSON
cambia espacios y orden, y la firma deja de cuadrar.

Sigue en **modo observar** a propósito: el algoritmo está confirmado contra
documentación pero **aún no contra un evento real**. Pasa a rechazar cuando el
primero valide en verde.

---

## 🔴🔴 TOPE DE 300 PUBLICACIONES AL DÍA — *Shop Probation Period*

```
12052093  Operation Not Allowed. Cannot upload more products today:
          your Shop Probation Period daily upload limit is `300` products
```

**Es el techo que manda sobre todo lo demás.** La tienda está en periodo de
prueba y sólo admite **300 productos A LA VENTA por día**.

| | |
|---|---|
| Crear BORRADORES | **no cuenta** — se hicieron ~900 en un día sin queja |
| Pasar a LISTING / activar | **sí cuenta** |

Se descubrió a golpes: la activación venía fina y de pronto **62 fallos
seguidos**. No hay forma de consultar el contador — sólo se sabe cuando pega.

**Cómo se planifica un lote grande, entonces:**

1. Publicar TODOS los borradores de una (no hay tope ahí).
2. Activar **en tandas de ~300 por día**, hasta agotar.
3. Al reanudar, `tk_activar.py` **es reanudable**: sólo toca los `DRAFT` con
   stock, así que basta con volver a correrlo al día siguiente.

Con ~750 productos con stock, activarlos todos son **~3 días**. Conviene
ordenar por lo que más rota, no alfabéticamente: lo que entre hoy vende hoy.

---

## 🚀 De BORRADOR a la venta — `tk_activar.py`

```
python tk_activar.py --limite 1     # piloto
python tk_activar.py                # todos los que tengan stock
python tk_activar.py --solo-stock   # solo refresca stock, no activa
```

**El orden lo pidió Brandon y no es capricho: primero el stock, después la
venta.** Al revés se publica con la cantidad del día que se creó el borrador
(los 249 traen la del 11-ago) y se vende lo que ya no hay.

### ⚠️ `/products/activate` NO sirve para esto

Esa API es para productos `Seller_deactivated` / `Platform_deactivated` — los
que YA estuvieron vivos y se ocultaron. Un **borrador nunca se publicó**, así
que se publica **reeditándolo**:

```
PUT /product/202309/products/{id}    con el payload completo + save_mode=LISTING
```

Por eso los payloads se guardan en disco desde el primer lote: sin ellos habría
que regenerar toda la IA para poder activar.

### ⚠️ El verbo cambia por endpoint

El único síntoma es `36009010 Invalid method`, y los volcados de la doc dicen
`METHOD: 1` en todos, así que ahí no se puede leer cuál es. Medido:

| endpoint | verbo |
|---|---|
| `/event/202309/webhooks` | **PUT** |
| `/products/{id}/inventory/update` | **POST** |
| `/products/{id}` (editar) | **PUT** |

### 🔴 LISTING valida lo que AS_DRAFT no

Esto es lo que hace que la activación no sea un trámite. Un borrador entra casi
con cualquier cosa; al pasar a la venta TikTok exige de verdad:

1. **Atributos obligatorios.** ⚠️ Se marcan con la llave **`is_requried`** —
   escrita así, con la errata de TikTok. Medido sobre 324 payloads: **107 de 219
   categorías** los piden, y afectan a **165 SKUs (51%)**. La creencia de que
   había "CERO obligatorios" venía de leer `is_required`, que no existe.
   (`services/tiktok_atributos.py` sí lee las dos grafías.)

   | obligatorio | SKUs | quién lo contesta |
   |---|---|---|
   | Tipo de garantía | 136 | Brandon: **Garantía del proveedor** (30 días) |
   | Productos importados | 132 | Brandon: **Sí** |
   | Nombre/Dirección de Fabricante Nacional/Importador | 59 | **nadie: dato legal de Kubera** |
   | Consumo de energía (V/W/Hz) | 59 | la IA, si el título lo dice |

2. **Suma de dimensiones ≤ 160 cm.** Solo 4 de 280 payloads la pasan de largo, y
   son productos de verdad grandes (cable de 6 m, colchón comprimido). No se
   fuerzan: declarar un bulto más chico del real es mentirle a la paquetería.

3. **Stock ≥ 1.** `Update Inventory` documenta `quantity` en [1, 99999]: el 0 no
   es válido — y un producto a la venta sin nada que vender solo cosecha
   cancelaciones.

**Lo que NO se rellena a propósito**: nombre y dirección del importador. Son
datos legales; inventarlos sería declarar en falso. Esos SKUs se quedan en
borrador y salen listados en `tk_activar_resultado.json` con
`etapa="obligatorios"`.

⚠️ **`/categories/{id}/rules` no sirve para predecir esto en MX**: devuelve
`manufacturer.is_required` vacío (la doc lo declara solo-UE) aunque el LISTING
sí lo exija. Lo que manda es la lista de **atributos** de la categoría.

### 🔴 Un atributo NO puede ir dos veces

`12052254 … each product attribute ID must appear only once`. Pasó en **40 de
1,221** payloads: la IA propone el mismo atributo con dos valores y
`tiktok_atributos.validar` los agrega como entradas sueltas. `AS_DRAFT` lo
tragaba, `LISTING` no.

Lo correcto para un multi-valor es **una entrada con varios `values`**, así que
se funden (descartar el segundo perdería un valor que la IA sí acertó).
`tk_activar.py::fundir_repetidos` lo hace al vuelo; **el arreglo de fondo va en
`services/tiktok_atributos.py`** — mientras no esté, todo lote nuevo arrastra el
problema hasta que se intente activar.

---

## 🗑️ Borrar productos — la mitad se resiste

```
DELETE /product/202309/products     body: {product_ids: [...]}   máx 20
```

`tk_borrar_sin_stock.py` (nace en seco; `--confirmar` para ejecutar).

⚠️ **`code=0` + `"Success"` NO significa que se borró.** Verificado producto por
producto: de ~523 intentos, **~324 se borraron y ~100 siguieron en `DRAFT`**,
con respuesta de éxito idéntica y `data: {}` vacío. Repetir la llamada no
cambia nada.

Lo que se descartó midiendo, para que nadie lo vuelva a probar:

| hipótesis | resultado |
|---|---|
| tope diario / throttle global | ❌ borrar 7 duplicados justo después funcionó |
| productos demasiado recientes | ❌ siguen sin borrarse horas más tarde |
| `DELETE /products/{id}` (uno a uno) | ❌ `36009010 Invalid method` — no existe |
| errores por-item en la respuesta | ❌ `data` viene vacío, sin `errors` |

Lo único que distingue a los que resisten: **`has_draft=true`**. Encaja con que
el endpoint borre el producto PUBLICADO, y estos solo existan como borrador.
**Salida práctica**: borrarlos a mano en Seller Center. No corren prisa — son
borradores, invisibles al comprador.

⚠️ `products/search` **devuelve los ya borrados** (`status=DELETED`). Sin
filtrarlos, reaparecen en cada censo y el conteo de "vivos" sale inflado.

---

## ⚠️ Los duplicados nacen del censo, no de TikTok

4 SKUs acabaron publicados 2-3 veces (7 copias de más, ya borradas). El origen:
`products/search` se **pagina mientras las tandas insertan**, así que el cursor
se desfasa y algunos productos no aparecen en el censo — el publicador los cree
faltantes y los manda otra vez.

Al cruzar contra un censo para decidir qué falta, **hacerlo con las tandas
detenidas**, o dar por hecho que habrá que barrer duplicados después.

---

## Registro de intentos

Cada SKU va a **`ops.channel_submissions`** (Supabase kubera) pase lo que pase:
`canal='tiktok'`, `operacion='create_product'`, error completo en
`error_resumen`, `detail_ref` para agrupar el lote.

Es bitácora de **INTENTOS**, no estado actual: un SKU que falló y luego publicó
aparece dos veces. **Al contar, agrupar por SKU.**

---

## Pendientes

1. **Seguir activando ~300/día** hasta agotar los 597 borradores con stock
   (`python tk_activar.py`, es reanudable). Son ~2 días más.
2. **Dar de alta 59 SKUs**: necesitan razón social y dirección fiscal de Kubera
   como Fabricante Nacional/Importador. Sin ese dato no pueden venderse.
3. **Arreglar en el backend el atributo repetido** (`tiktok_atributos.validar`
   debe fundir por `id`, no agregar entradas sueltas) — hoy sólo lo parcha
   `tk_activar.py` y todo lote nuevo lo arrastra.
4. **18 productos con peso inválido en Woo** (0 kg, y algunos de 188 y 871 kg):
   corregir en el origen, no inventarlo aquí. Son datos malos del mismo lote que
   las dimensiones reconstruidas del CBM.
5. **Borrar a mano en Seller Center** los ~100 borradores sin stock que la API
   se niega a borrar.
6. **Revisar 227 categorías APROXIMADAS** (`tk_aproximadas.json`). Ojo: el
   recomendador **también acierta con confianza en la categoría equivocada** —
   un "Collar de recuperación para gato" acabó en *Joyas para disfraces* — y
   esos NO llevan marca de aproximados.
7. Reprocesar imágenes de los 4 productos con subidas fallidas
8. Pasar la firma entrante de observar a rechazar, tras el primer evento real
5. TikTok/Temu tienen **0 filas en `canal_inventario`**, así que **no participan
   del fan-out** — y falla en silencio, sin dar error
6. Quitar la IP de desarrollo de la allowlist al terminar
7. Rotar el `app_secret`: pasó por el chat
