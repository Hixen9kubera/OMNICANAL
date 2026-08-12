# MANUAL OPERATIVO — WALMART MÉXICO · Kubera
**Fecha de corte: 7-ago-2026 · Cuenta: Kubera (vendedor NACIONAL, 34 artículos, sin WFS, plantilla de envío $0)**

**Cero llamadas a `marketplace.walmartapis.com` para escribir este manual.**

### Cómo leer las marcas

| Marca | Significa |
|---|---|
| **[DOC]** | Texto literal de una página de Walmart, con URL. Si la página es de otro mercado se dice: **[DOC-US]**, **[DOC-GLOBAL]**, **[DOC-MX]** |
| **[MEDIDO]** | Lo vivimos en producción con esta cuenta. **Manda sobre la doc cuando chocan** |
| **[SUPUESTO]** | Razonamiento nuestro. No hay fuente. No construir nada crítico encima sin probarlo |
| **[NO CONFIRMADO]** | La doc no lo dice, o lo dice en una imagen/PDF que no se puede leer |

### Regla de oro del set de documentación
`/mx-marketplace/` y `/mx/guides/` = México, **pero legado**. `/global-marketplace/` **NO significa "no aplica a MX"** — es el destino obligatorio de MX; ahí hay que leer la línea *"Market availability"* de cada operación. `/us-marketplace/`, `/guides/` sin prefijo y `/lang-es/` = **Estados Unidos**, aunque esté en español. `/cl/`, `/ca/` = no aplica. Esta confusión ya nos costó un día completo.

---

## 1. TIEMPOS

| Operación | Cuánto tarda | Cómo se verifica |
|---|---|---|
| **Token OAuth** | Vive **900 s (15 min)** [MEDIDO, `docs/WALMART_MX_HALLAZGOS.md:19`] | `expires_in` de la respuesta. El script lo renueva por lote (`publicar_walmart.py:719`) |
| **Feed aceptado (`POST /v3/feeds`)** | Respuesta inmediata con `feedId`. **NO es veredicto** | El `feedId` que devuelve. `publicar_lote()` deliberadamente **no** espera aquí — esperar produjo los "9 feeds sin fallos" del 4-ago que en realidad fueron 0 |
| **Feed `RECEIVED` → `PROCESSED`** | **La doc MX promete 4 horas.** [DOC-MX] https://developer.walmart.com/mx-marketplace/docs/throttling (tabla: Items, *Processing Time 4 hours*). **La realidad medida es >24 h**: feed enviado 6-ago, veredicto 7-ago [MEDIDO] | `GET /v3/feeds/{feedId}` → `feedStatus` |
| ↳ ¿es avería? **No.** | [DOC-MX] https://developer.walmart.com/mx-marketplace/docs/set-up-and-manage-items (act. 3-ago-2026): *"The first items may be successfully submitted **days** before the last items get submitted."* Es la única declaración temporal de esa página y **respalda nuestra medición** | — |
| **Cadencia de sondeo oficial** | **15 min · 1 h · 2 h · cada 4 h** [DOC-MX] https://developer.walmart.com/mx-marketplace/docs/feed-overview | Es prácticamente gratis: `GET /v3/feeds` = **5000/min** en MX |
| **Ingestado → *submitted* (parseado y clasificado)** | *"typically a few hours"* [DOC-MX] https://developer.walmart.com/mx-marketplace/docs/item-management-api-overview | `GET /v3/items/{sku}` |
| **Submitted → PUBLISHED** | *"Publishing typically takes a few hours"* [DOC-MX] misma página | `GET /v3/items?publishedStatus=PUBLISHED` |
| **`PUT /v3/price` → precio vivo** | **~5 min** [MEDIDO]. **Sin SLA publicado** — verificado por ausencia en [DOC-MX] `price-overview` | Ojo: `GET /v3/items` va **un paso atrasado** [MEDIDO]. **No es fuente de verdad de un precio recién escrito** |
| **`PUT /v3/inventory`** | Sin SLA publicado [DOC-GLOBAL] `inventory-overview` (cubre *"Canada, Chile, Mexico, and the U.S."*) | `GET /v3/inventory?sku=` |
| **`DELETE /v3/items/{sku}`** | Responde 200; *"it can take up to **48 hours** for items to be retired from our catalog"* [DOC-MX] https://developer.walmart.com/mx-marketplace/reference/retireanitem | `GET /v3/items/{sku}` → `lifecycleStatus` |
| **Cuota agotada (`REQUEST_THRESHOLD_VIOLATED`)** | Se recupera en **poco más de una hora**, de forma **gradual, no de golpe** [MEDIDO] | Es un **token bucket**: [DOC-GLOBAL] https://developer.walmart.com/global-marketplace/docs/rate-limiting — *"The bucket is continuously replenished with tokens at a fixed rate"* |
| **`SYSTEM_ERROR` en ingestión** | *"wait for at least an hour and then try again"* [DOC-MX] `feed-overview` | — |
| **Pago del dinero** | **Semanal, dentro de máximo 14 días naturales posteriores a la ENTREGA** [DOC-MX] https://marketplacelearn.walmart.com/mx/guides/Pagos/Ciclo%20de%20pagos/pol-tica-de-retenci-n-de-pago-para-nuevos-vendedores- | Seller Center → Reporte Histórico de Pagos |
| **Reportes a demanda (`ITEM_MX`/`INVENTORY_MX`)** | *"You can fetch details of report requests created in the **last 30 days** only"* [DOC-MX] `generatereport` | — |

### Las tres decisiones que salen de esta tabla

1. **El poller se diseña a 24–48 h, no a 4 h.** El número de la doc está desmentido por nuestra medición **y** matizado por la propia doc MX ("days"). Un script que declare fracaso a las 4 h reporta falsos negativos.
2. **`PROCESSED` ≠ publicado ≠ comprable.** Son tres relojes distintos: feed procesado (horas–días) → submitted (horas) → published (horas). Un SKU con `ingestionStatus: SUCCESS` todavía no vende.
3. **Nunca leer `GET /v3/items` para confirmar un precio que acabas de escribir.** Va atrasado. La fuente de verdad del precio es lo que mandaste.

---

## 2. LÍMITES

### 2.1 Por endpoint — columna MÉXICO
[DOC-GLOBAL, con desglose por mercado] https://developer.walmart.com/global-marketplace/docs/rate-limiting

| Endpoint | Cuota MX | Nota |
|---|---|---|
| `GET /v3/feeds` | **5000/min** | Compartido con `/v3/feeds/{feedId}` |
| `GET /v3/feeds/{feedId}` | **5000/min** | Sondear es gratis. Úsalo sin miedo |
| `GET /v3/items` | 300/min | |
| `GET /v3/items/{id}` | 900/min | |
| `DELETE /v3/items/{sku}` | 900/min | |
| `GET | PUT /v3/inventory` | 200/min | |
| **`PUT /v3/price`** | **30/min** | En EE.UU. son 100/**hora**. MX está mucho mejor |
| `PUT /v3/price` promocional | 60/min | |
| `GET /v3/orders` | 60/min | |
| `POST /v3/items/catalog/search` | 200/min | |
| Plantillas de envío | 100/min | |
| `POST /v3/insights/.../listingQuality/items` | **2/min** | El más apretado de todos |
| `GET /v3/reports/.../{requestId}`, `downloadReport` | 20/hora | |

**MX = `NA` (no existe)** en: `/v3/items/spec`, `/v3/items/walmart/search`, `/v3/webhooks/*`, Disputes, `/v3/utilities/apiStatus`, `/v3/feeds/{feedId}/errorReport`, `/v3/orders/released`, `/v3/inventories`.

### 2.2 Por feedType — el cuello de botella real

| feedType | Tamaño máx. MX | Cuota MX |
|---|---|---|
| **`MP_ITEM_INTL`** (el nuestro) | **25 MB** | **10 por hora** |
| `MP_MAINTENANCE` (editar vivos) | 25 MB | 10/hora |
| `MP_ITEM_MATCH` | 25 MB | 10/hora |
| `SKU_TEMPLATE_MAP` | 10 MB | 20/hora |
| `MP_INVENTORY` | 1 MB | 50/hora |
| `inventory` (**obsoleto**) | 10 MB | 10/hora |
| `PRICE_AND_PROMOTION` | — | 30/**día** |
| `price`, `promo`, `item`, `mp_item`, `lagtime` | — | **NA en MX** |

Doble fuente para el 10/hora: la tabla de arriba + [DOC-MX] https://developer.walmart.com/mx-marketplace/docs/throttling (*Items · 10 per hour · 25MB · 4 hours*).

> **La contradicción "25 MB vs 10 MB" no existe.** 25 MB es el límite duro; el *"Keep feed sizes below 10 MB"* de [DOC-MX] `bulkitemsetup` termina con *"to ensure **optimal feed processing time**"* — es un consejo de rendimiento. `LIMITE_BYTES_FEED = 9 MB` (`publicar_walmart.py:133`) **está bien y no hay que tocarlo.**

### 2.3 El límite PRÁCTICO del feed — esto es lo que importa

| Fuente | Artículos por feed |
|---|---|
| Doc: *"You can update 10,000 items at once"* [DOC-MX] `bulkitemsetup` | 10,000 |
| Doc de sandbox: *"Max SKUs per call: 50"* [DOC-MX] https://developer.walmart.com/mx-marketplace/docs/feeds-validations | 50 |
| **[MEDIDO] 343 artículos / 937 KB → `SYSTEM_ERROR.GMP_GATEWAY_API`** | **truena** |
| **[MEDIDO] 85 artículos → pasa** | **funciona** |

**El límite real está entre 85 y 343, muy lejos de los 10,000 documentados.** El gateway revienta por conteo, no por peso (937 KB contra un tope de 25 MB).

**Recomendación con tres respaldos independientes: `TAM_LOTE = 50`.**

Hoy está en **200** (`publicar_walmart.py:132`). Bajarlo a 50 alinea:
1. el único número que Walmart publica cercano a la realidad (50 SKUs/call);
2. nuestro umbral empírico (85 pasa, 343 no);
3. **el bug silencioso de la sección siguiente.**

### 2.4 ⚠️ RIESGO ACTIVO: `includeDetails=true` con lotes de 200

`estado_feed()` (`publicar_walmart.py:551-553`) llama `GET /v3/feeds/{feedId}?includeDetails=true` **sin `limit`**. Dos hechos de la doc MX que chocan con eso:

- *"Includes details of each entity in the feed. **Do not set this parameter to true.**"* [DOC-MX] https://developer.walmart.com/mx-marketplace/reference/getfeedstatus
- `limit`: *"It cannot be more than **50** entities"*, **default 20**; `nextCursor` *"Used for pagination when more than 200 items are retrieved"*.

**Consecuencia:** un feed de 200 artículos puede devolver el detalle de solo 20–50. Los otros 150 **no aparecen en `por_sku`** y el resumen los reporta como si nada. Es la misma clase de falso positivo que produjeron los "9 feeds sin fallos" del 4-ago.

**Fix mínimo:** `TAM_LOTE = 50` **y** pasar `limit=50` explícito. Sin esto, el veredicto por SKU no es confiable en lotes grandes. [MEDIDO parcialmente: el mecanismo del paginado es [DOC]; que hoy se estén perdiendo SKUs es [SUPUESTO] hasta contar `len(por_sku)` contra `len(lote)` en la próxima corrida — verificación de costo cero, ya tenemos los feedId].

### 2.5 Ritmo entre lotes

`PAUSA_ENTRE_LOTES = 20` segundos (`publicar_walmart.py:136`) **es insuficiente**: 10 feeds seguidos con 20 s de pausa consumen el presupuesto de una hora entera en 3.3 minutos. Con reposición continua tipo token bucket, el intervalo sostenible es **360 s (6 min)**.
*Matiz honesto:* que el algoritmo de tokens gobierne **feeds** (y no solo endpoints REST) es [SUPUESTO] — la doc lo describe para "Walmart APIs" y la tabla de feedTypes es otra tabla. Pero encaja exacto con lo medido (≈25 envíos antes de tronar, recuperación gradual en ~1 h).

**Presupuesto operativo real: 10 feeds/hora × 50 artículos = 500 artículos/hora como techo.**

### 2.6 Instrumentación gratis que hoy no usamos

[DOC-GLOBAL] `rate-limiting`: toda respuesta trae **`x-current-token-count`** (*"Your current number of tokens available"*) y **`X-Next-Replenishment-Time`**. Leerlas **no cuesta una llamada** — vienen en respuestas que ya recibimos. Registrarlas en `publicar_walmart.py` convierte "la cuota se agotó" en un número medible antes de agotarla.
[SUPUESTO] que el gateway legacy de MX las emita en `POST /v3/feeds`. Diseñar con fallback al sleep de 360 s si vienen vacías.

### 2.7 Límites de contenido

| Regla | Estado |
|---|---|
| **Máximo 6 imágenes por artículo** | ✅ **[DOC-MX], literal:** *"Te recomendamos no exceder 6 imágenes por artículo, esto permite que tu producto no tenga intermitencias."* https://marketplacelearn.walmart.com/mx/guides/Gestión%20de%20Catálogo/Carga%20de%20artículos/lineamientos-de-las-im-genes |
| Fondo blanco, mín. 1000 px, máx. 3000 px, margen 50 px, RGB, **peso máx. 1 MB** | ❌ **[NO CONFIRMADO].** El cuerpo de esa página son **5 PNG** y un PDF con `href` vacío. **Contraevidencia:** el snippet de búsqueda de esa misma página dice *"máximo 5 MB"*, y la página US dice 2200×2200 máx / 1500×1500 mín / *"5MB or less"*. **NO degradar el pipeline de imágenes por el "1 MB".** Ese número no coincide con MX, ni con US, ni con Chile |
| Título 100 caracteres · descripción corta con 3 atributos · separador `$$` · oraciones ≤81 caracteres | ❌ **[NO CONFIRMADO].** La Guía de Contenido MX (act. 18-oct-2024) son **22 archivos .jpg**. Cero texto. Hoy truncamos a **200** (`publicar_walmart.py:474`) — [SUPUESTO] heredado, sin fuente |
| Variantes: `variantGroupId` ≤20 caracteres, **máx. 3 atributos**, **máx. 1,000 por grupo** | ✅ [DOC-MX] https://developer.walmart.com/mx-marketplace/docs/item-object |
| **Los campos obligatorios CAMBIAN por categoría** | ✅ [MEDIDO] `condition` es obligatorio en Herramientas y **no existe** en Amplificadores y Audio. No hay juego universal de campos |
| La clave del bloque `Visible` es **la etiqueta en español** de la categoría | ✅ [MEDIDO] Si le pegas mal, Walmart cae a un spec genérico y pide atributos absurdos. **Ese es el síntoma de categoría equivocada** |
| Acentos: `"Sí"` con acento; país en español (`["China"]`); `material` es String, `colorCategory` es array | ✅ [MEDIDO] |

---

## 3. DINERO

### 3.1 Comisión por categoría
[DOC-MX] https://marketplacelearn.walmart.com/mx/guides/Pagos/Comisiones/comisiones-marketplace — página act. **31-ene-2025**, tabla **en texto** (no imágenes), ~70 categorías, verificada fila por fila.

> *"el cobro de la comisión del producto depende de la categoría donde se encuentre cargado tu artículo"*

| Categoría | Comisión | Con Premium MSI | Dónde pega en Kubera |
|---|---|---|---|
| **Disfraces** | **15 %** | 19.50 % | La exención de UPC (folio 15728342) |
| **Herramientas** | **15 %** | 19.50 % | Primer feed |
| **Amplificadores de Audio** | **10 %** | 14.50 % | `VAR-0436-NEG-6C` |
| Vinos y Licores | 8 % | 12.50 % | *(mínimo de la tabla)* |
| Joyería | 20 % | 24.50 % | *(máximo de la tabla)* |

**El diferencial de MSI es exactamente +4.5 puntos en las 70 filas, sin excepciones.** Hoy mandamos `"msiEligible": "No"` (String, no booleano — `publicar_walmart.py:480`), así que la columna Premium no nos aplica.

**Lo que esto significa para la categorización:** entre la mejor y la peor categoría hay **12 puntos de comisión**. Elegir mal la categoría no es un detalle de clasificación, es margen.

### 3.2 De dónde se saca por API

✅ **La comisión POR PEDIDO sí viene en la API.**
[DOC-MX] https://developer.walmart.com/mx-marketplace/reference/getallorders y `getallordersusingcursor`:
```
order.orderLines[].item.commission = { currency: "MXN", amount: "<string>" }
```
Descripción literal: *"The commission that Walmart will charge seller for the item"*. Y **se puebla**: la doc MX trae un ejemplo real con `"amount": "668.07"`. También aparece `null` en otros ejemplos — exactamente el comportamiento del `sale_fee` de Mercado Libre cuando el token se cae al crear el pedido.

**Arquitectura recomendada:** copiar el patrón `_ml_*` tal cual. Congelar en el pedido de WooCommerce el precio, la comisión y el neto al momento de la venta, **con el mismo `ON DUPLICATE` que rellena 0→valor y nunca re-toca un valor >0** (v0.17.0). No hace falta gastar ninguna llamada de diagnóstico: el campo existe y la primera venta real lo confirma sola.

**Bonus:** el flete cobrado al cliente también viaja en la orden — `charges[]` con `chargeType`/`chargeName` (`SHIPPING`/`SHIPPING_CH`, `DISCOUNT`/`COUPON_DISC`, `PRODUCT`/`ItemPrice`) y `tax[]` (`IVA_TAX`, `IEPS_TAX`, `STATE_TAX`). **El neto real de un pedido se reconstruye sin ningún reporte.**

### 3.3 🔴 EL HUECO DE NEGOCIO: no hay API de liquidación en México

**Esto no es un detalle técnico. Es un hueco de negocio y hay que decirlo así.**

- `POST /v3/reports/reportRequests` en MX acepta **exactamente dos** `reportType`: **`ITEM_MX`** e **`INVENTORY_MX`**. Ninguno financiero. [DOC-MX] https://developer.walmart.com/mx-marketplace/reference/generatereport
- **Cero endpoints** de Payments / Settlement / Recon / Fees en las 77 operaciones del set MX, y cero en Global para MX.
- Recon Report y Payment Statement existen **solo bajo `us-marketplace`** — y su traducción `/us-marketplace/lang-es/` **sigue siendo Estados Unidos**. Ya lo topamos: `docs/WALMART_MX_HALLAZGOS.md:37` registra `/v3/getReport` → **404** con credenciales MX.

**Consecuencias concretas:**

| Qué queremos | Se puede | Cómo |
|---|---|---|
| Comisión de un pedido | ✅ Sí, por API | `orderLines[].item.commission` |
| Flete e impuestos de un pedido | ✅ Sí, por API | `charges[]`, `tax[]` |
| **Cuánto me depositó Walmart esta semana** | ❌ **No por API** | Seller Center → **"Reporte Histórico de Pagos"** (manual, [DOC-MX] pero la página es video Vimeo + PDF, **sus columnas son [NO CONFIRMADO]**) |
| **Conciliar depósito ↔ pedidos** | ❌ **Manual** | Descarga de Seller Center + cruce contra `pedidos_ml` |
| **Detectar que Walmart cobró de más** | ❌ **Manual** | No hay reporte automatizable |

**Traducción operativa:** Walmart es el único canal de Kubera donde la conciliación de dinero **no se puede automatizar**. Con 34 artículos da igual; con 1,000 SKUs es una persona-día al mes. **Presupuestarlo ahora, no descubrirlo después.**

### 3.4 Liquidación — Kubera cobra rápido

[DOC-MX], cita literal:
> **Nacional:** *"Walmart realizará remisiones **semanales** de los Montos al Vendedor dentro de un plazo máximo de **14 (catorce) días naturales** posteriores a la entrega"*
> **Extranjero:** 28 días naturales, y *"Se considerará que un **Vendedor extranjero** ha dejado de ser nuevo cuando hayan transcurrido 90 días naturales… y haya alcanzado ventas por al menos $20,000.00 MXN"*

**El umbral de 90 días + $20,000 MXN es de vendedores EXTRANJEROS. Kubera es nacional: cobra semanal, ≤14 días tras la ENTREGA, desde el día uno.** No hay ningún "régimen lento" que superar. (Esta confusión circuló en un reporte previo; queda corregida.)

### 3.5 El costo real del envío — donde está el riesgo de margen

**Sin mensualidad, sin costo de alta.** [DOC-MX]: *"Registrarse para convertirse en vendedor de Walmart Marketplace es **completamente gratis**. Simplemente pagarás una **tarifa de referencia** por cada artículo vendido."* Estructuralmente más barato que Amazon MX.

**El costo oculto es el flete, y hoy Kubera lo absorbe al 100 %:** la plantilla por defecto cobra **$0 a todo México** [MEDIDO]. Cada venta paga comisión + flete completo.

**Cómo se cobra** — [DOC-MX] https://marketplacelearn.walmart.com/mx/guides/Pagos/Tarifas/tarifas-de-env-o-con-walmart, literal:
> *"Multiplica: **largo × ancho × alto (en cm)** Divide el resultado entre **5,000**"*
> *"Artículos de hasta **68 kg** se consideran **paquetería estándar (Parcel)**"*
> *"puede aplicarse un **cargo adicional** de entre **10 % y 35 %** sobre la tarifa base"*

Se cobra **el mayor** entre volumétrico y real. Los montos de la página se muestran con y sin IVA al 16 %.

> ### 🔴 ESTE ES EL RIESGO #1 DE MARGEN
> Las dimensiones que mandamos (`assembledProduct*` y `ShippingDimensions*`, `publicar_walmart.py:460-462, 490-493`) salen directo de `dimensions` de WooCommerce. Y **las dimensiones de Woo NO son medidas**: son `_kubera_cbm` reconstruido (L×A×H = CBM, 99.7 % verificado), con **458 SKUs de densidad físicamente imposible**. Walmart cobra volumétrico → **flete inflado hasta 3× y precio fuera de mercado**, sobre un flete que además pagamos nosotros.
> `docs/WALMART_MX_HALLAZGOS.md:91` ya lo declara: **"Arreglar `piezas_por_caja` es prerrequisito, no mejora."** Sigue pendiente.

**Modalidades de envío** — [DOC-MX] la guía de onboarding nombra **dos**, no tres:
- **Envío por Walmart**: *"Walmart te envía las guías a través de Seller Center. El saldo se descuenta directamente a tu estado de cuenta."* ⚠️ **Walmart NO paga el flete** — te lo descuenta. Un resumen automático de esa misma página lo glosó como "Walmart covers freight costs": **es falso, guardar la cita, no la glosa.**
- **Otros métodos de envío**: *"Tu administras tus propias guías…"* ← lo que hace Kubera.

*("Ship With Walmart is not available for Cross Border Trade" es una cita de EE.UU. mal atribuida a México en un reporte previo. **No usar.**)*

**Tarifas WFS** (no las usamos, para referencia): tarifa combinada por pieza embarcada, **$47.30** (0–1 kg, $0–299) a **$2,554.00** (+240 kg). Retiro de inventario son **dos tabuladores distintos**: por **antigüedad** (181+ días) $20.40–$96.00 y **voluntario** $38.22–$162.96 — el voluntario cuesta casi el doble. Re-etiquetado $9.50/etiqueta (IVA incluido). Guía de devolución $86.46 (≤25 kg) / $179.74 (>25 kg). [DOC-MX] `/mx/guides/WFS/Costos y Tarifas/tarifas-wfs`

### 3.6 Lo que NO sabemos del dinero

**[NO CONFIRMADO] La base de cálculo de la comisión:** ¿sobre precio con IVA o sin IVA? ¿el flete entra en la base? La guía "Facturación Seller MKP" son **tres capturas de pantalla y un PDF, cero texto**. **Con flete $0, esto define el margen real.** Solo se resuelve con el CFDI de la primera venta.
**[NO CONFIRMADO] Retenciones a personas físicas:** la guía existe, el cuerpo es imagen/PDF, cero porcentajes en texto.

---

## 4. QUÉ SE PUEDE Y QUÉ NO

El set `mx-marketplace` tiene **77 operaciones en 15 familias** (verificado contra el OpenAPI embebido de cada página de referencia).

### 4.1 Lo que usamos hoy

| Operación | Ruta |
|---|---|
| Token | `POST /v3/token` — **requiere `WM_MARKET: mx`, sin él da 400** [MEDIDO] |
| Publicar | `POST /v3/feeds?feedType=MP_ITEM_INTL` (multipart, campo `file`) |
| Estado del feed | `GET /v3/feeds/{feedId}` (ver §2.4 sobre `includeDetails`) |
| Catálogo | `GET /v3/items`, `GET /v3/items/{sku}` |
| Precio | `PUT /v3/price` ✅ **funciona en MX** [MEDIDO] |
| Inventario | `PUT /v3/inventory` ✅ **funciona en MX** [MEDIDO] |
| Retiro | `DELETE /v3/items/{sku}` |

> ⚠️ **`docs/WALMART_MX_HALLAZGOS.md:37` está desactualizado y es peligroso:** lista `/v3/price` entre los endpoints que "devuelven 404 en MX". Ese diagnóstico es del 31-jul **con la cuenta vacía**. Hoy `PUT /v3/price` y `PUT /v3/inventory` están probados y funcionando. **Corregir esa línea** antes de que alguien decida no usar `/v3/price` leyendo el doc.

### 4.2 Lo que SÍ se puede y hoy no usamos — palancas disponibles ya

| Palanca | Endpoint | Cuota MX | Para qué sirve |
|---|---|---|---|
| **Censo de no publicados** | `GET /v3/items?publishedStatus=UNPUBLISHED&lifecycleStatus=ACTIVE` | 300/min | **"Subí el feed y no salió"** — sin feeds y sin `includeDetails`. Enums: `PUBLISHED\|UNPUBLISHED`, `ACTIVE\|ARCHIVED\|RETIRED` [DOC-MX] `getallitems` |
| **Calidad de listado** | `POST /v3/insights/items/listingQuality/items` | 2/min | Qué le falta a cada SKU. Campos: `overAllQuality`, `offerScore`, `contentScore`, `ratingReviewScore`, `itemDefectCnt`, `defectRatio` |
| **Auditar plantilla de envío** | `POST /v3/mx/associations` | — | *"Get shipping template associated with items"* — qué SKUs cuelgan de la plantilla que cobra $0 |
| **Corregir la plantilla** | `PUT /v3/settings/shipping/templates/{templateId}` | 100/min | **La palanca directa contra el flete $0** |
| **Remapear SKUs en lote** | `POST /v3/feeds?feedType=SKU_TEMPLATE_MAP` | 20/hora, 10 MB | Campos: `sku`, `actionType="Add"`, `shippingTemplateId`, `fulfillmentCenterId` |
| **Catálogo completo en un archivo** | `POST /v3/reports/reportRequests?reportType=ITEM_MX&reportVersion=v1` → status → `downloadReport` | 20/hora | En vez de paginar de 50 en 50 |
| **Editar artículos vivos** | `POST /v3/feeds?feedType=MP_MAINTENANCE` | 25 MB, 10/hora | `processMode`: `CREATE`/`REPLACE_ALL`/`PARTIAL_UPDATE`. Sin re-mandar `MP_ITEM_INTL` |
| **Órdenes por cursor** | `GET /v3/orders/cursor` | — | Backfill histórico sin el tope de offset 1000 |

**[SUPUESTO] de alto valor:** la tabla de límites da **una fila por feedType**, lo que sugiere **buckets separados**. Si es así, editar por `MP_MAINTENANCE` **no consume la cuota de alta de `MP_ITEM_INTL`**. Cuesta **1 llamada** verificarlo. Está respaldado indirectamente: [MEDIDO] con `POST /v3/feeds` agotado, `GET /v3/items`, `GET /v3/feeds` y `DELETE /v3/items/{sku}` seguían funcionando — los buckets **por endpoint** sí están separados.

### 4.3 Lo que NO existe por API en México

| Capacidad | Estado | Y con la migración a Global… |
|---|---|---|
| **Exención de UPC/GTIN** | Sin endpoint en MX | ❌ En Global, `GET /v3/items/gtin-exemption/status` es **"US only"**. **Nunca llega.** Ticket en Seller Center, siempre |
| **Descubrir el spec de una categoría** (`POST /v3/items/spec`) | 404 en MX [MEDIDO] | ❌ **"US only"** (y 3 TPM). **No llega ni migrando.** Plantilla XLSX de Seller Center, siempre |
| **Taxonomía** (`GET /v3/items/taxonomy`) | Ausente en MX | ❌ **"US only"** las 4 rutas |
| **`GET /v3/orders/{purchaseOrderId}`** (pedido individual) | No existe | ❌ En Global: **"US \| CA \| CL"** — **sin MX**. Se usa `GET /v3/orders?purchaseOrderId=` |
| **Liquidaciones, pagos, comisiones agregadas** | Cero endpoints | ❌ Cero en Global también. **US only** |
| **Reportes de desempeño** (VTR, OTS, LSR, OTD, cancelaciones, INR, feedback) | No existen | ❌ **US only** |
| **Webhooks / notificaciones** | No en el set MX legacy | ⚠️ En Global, `POST /v3/webhooks/subscriptions` dice *"Market availability: Global"* con `WM_MARKET` **required, allowed: US CA MX CL**, y hay evento **`PO_CREATED`**. **PERO** la tabla de rate limits marca **MX = NA** en las 6 rutas. **[NO CONFIRMADO], no planear sobre esto.** Hoy: sondeo |
| **Disputas, reseñas, alta de cuenta, onboarding WFS** | Manual | US only |
| **Campañas (Hot Sale, Buen Fin)** | [NO CONFIRMADO] por ausencia | Precio promocional por SKU sí es API (`PUT /v3/price?promo=true`) |

**Cuatro cosas que un reporte previo prometió que llegarían con la migración a Global — tres son falsas:** Get Spec (US only), Taxonomía (US only), GET de pedido individual (sin MX). Solo Notificaciones queda en duda.

**Lo que sí llegaría con Global, con etiqueta y límite MX documentados:** Unpublished Items **con `unpublishedReasonCode`** (100/min), Catalog Search (200/min), conteo de items por estado (200/min), Lag Time (200/min), `GET /v3/promo/sku/{sku}` (50/min), inventario por ship node, `acknowledgeLines`.

### 4.4 La migración a Global es OBLIGATORIA — pero sin fecha

[DOC-MX] https://developer.walmart.com/mx-marketplace/docs/global-api-migration-faq, literal:
> **"United States (US): No.** Existing integrations using the current US APIs will continue to operate without changes. **Canada, Mexico, and Chile: Yes. Migration is required for these markets. The legacy country-specific APIs for Canada, Mexico, and Chile will be decommissioned."**
> **"Existing integrations will continue to function until a decommission date is announced."**

**No hay fecha anunciada.** La fecha "31-jul-2026" que circuló **no existe en ninguna página** — salió de un snippet de buscador. Global exige `WM_GLOBAL_VERSION: 3.1` y `WM_MARKET` obligatorio, y *"The same client ID and secret can typically be used"*.

**Postura recomendada: vigilar el "What's New", no migrar en pánico.** No hay reloj corriendo y la migración cambia rutas (`/ship` → `/shipping`).

### 4.5 Solo a mano en Seller Center

1. **Pedir la exención de UPC** — por categoría, con el **nombre exacto en español** [MEDIDO, folio 15728342 = Disfraces]. Sin ella: *"not authorized to set up 'CUSTOM' Product IDs"*. Cada categoría nueva es un ticket nuevo: **Almacenamiento**, **Muebles**, **Adornos y Decoraciones** NO están cubiertas por la de "Cocina, Decoración y Otros" (`publicar_walmart.py:120-126`).
2. **Descargar la plantilla XLSX** para conocer el spec de una categoría nueva (200 atributos, 52 obligatorios, hoja `Hidden_tools` con el mapeo nombre visible → **nombre XML**).
3. **Reporte Histórico de Pagos** y toda conciliación de dinero.
4. **Dar de alta una paquetería propia**: *"you need to contact Walmart seller support to map your shipping option to your orders"* [DOC-MX].
5. **Apelaciones, disputas, verificación de marca.**
6. **Los PDFs vinculantes**: T&C MX (act. 10-jul-2026) y Criterios de Productos Prohibidos.

---

## 5. ÓRDENES — el ciclo completo

### 5.1 Endpoints reales del set MX

| Paso | Ruta | Respuesta |
|---|---|---|
| Sondear | `GET /v3/orders` | 200 |
| Backfill | `GET /v3/orders/cursor` | 200 |
| **Reconocer** | `POST /v3/orders/{purchaseOrderId}/acknowledge` | **202** |
| **Embarcar** | `POST /v3/orders/{purchaseOrderId}/ship` | **202** |
| Multi-paquete | `POST /v3/orders/{purchaseOrderId}/multiPackageShipping` | 202 |
| **Entregar** | `POST /v3/orders/{purchaseOrderId}/deliver` | 202 |
| Cancelar | `POST /v3/orders/{purchaseOrderId}/cancel` | 202 |
| Guía | `GET /v3/orders/label/{trackingNumber}` · `POST /v3/orders/labels/wps/create` | — |
| Devoluciones | `GET /v3/returns` · `getallreturnsusingcursor` · `POST /v3/returns/{returnOrderId}/refund` | — |

**No existe** `GET /v3/orders/{purchaseOrderId}`. Para una orden suelta: *"The same API can be used to search a single order based on purchaseOrderId/ customerOrderId"* [DOC-MX].

### 5.2 Trampas de contrato que producen 400 garantizado

| Endpoint | Fechas | Filtros |
|---|---|---|
| `GET /v3/orders` | **epoch en SEGUNDOS**, default `NOW-180DAYS` | `limit` ≤100, `offset` ≤**1000**, `statusCodeFilter` opcional (`Created, Acknowledged, Shipped, Cancelled, OnHold, Delivered`) |
| `GET /v3/orders/cursor` | **ISO-8601 con offset**: `yyyy-MM-dd'T'HH:mm:ss.SSSXXX` (ej. `2022-01-29T10:53:12.355-09:30`), **URI-encoded**, ambas o ninguna | **`statusCodeFilter` es REQUIRED** → un backfill completo exige **una corrida por estado**. Presupuestarlo |

**Mezclar los dos formatos de fecha es un 400.**

### 5.3 Envío

**Carriers integrados en MX** [DOC-MX] https://developer.walmart.com/mx-marketplace/docs/provide-your-own-shipping-option:
`MX-FEDX, MX-DHL, Estafeta, SFC, PEXP, TRACUSA, UPS, 99MIN, 17Track, Other`

- Regla literal: *"if carrier is Other then both trackingNumber & trackingURL is Mandatory"*
- **Con carrier integrado**, el `deliver` *"is updated by Walmart from the carriers"*.
- **Con `Other`, Kubera debe mandar el `deliver`** — *"provide the delivered update once the package has reached the customer"*. **Si no lo mandas, la orden nunca cierra.**

`POST /v3/orders/{po}/ship` *"Updates the status of order lines to Shipped and **trigger the charge to the customer**"* — el cobro al cliente se dispara aquí.

**Razones de cancelación:** enum `MIRAKL_REFUND_45 | MIRAKL_REFUND_15 | SELLER_REJECTION`, **sin descripciones en el spec**. Cuál usar para qué caso: **[NO CONFIRMADO]**. *(El prefijo "MIRAKL" sugiere sobre qué corre Walmart MX, pero ninguna página lo dice: [SUPUESTO], irrelevante para operar.)*

### 5.4 🔴 Plazos obligatorios — el hueco más incómodo

**No sabemos cuántas horas tenemos para reconocer y embarcar en México.**

- Las **4 horas** que todo el mundo cita son de [DOC-US] https://developer.walmart.com/us-marketplace/docs/acknowledge-a-created-order — *"Acknowledging new orders within four hours of creation…"*. **Esa página no menciona México. SOLO-EEUU hasta prueba en contrario.**
- La guía MX correcta existe y se descarga sin bloqueo — [DOC-MX] `/mx/guides/Gestión de Órdenes/Modalidad Tradicional/tiempos-de-embarque-y-recolecci-n-modalidad-tradicional` — con los rótulos *"Tiempos de aceptación"*, *"Tiempos de embarque"*, *"Horarios para solicitar recolección"*… **pero las cifras están dentro de imágenes/PDF.**

**Postura operativa mientras tanto [SUPUESTO]: reconocer en ≤4 h y embarcar el mismo día hábil.** Es el peor caso conocido y no cuesta nada cumplirlo con 34 SKUs. Resolverlo definitivo cuesta **cero llamadas a la API**: OCR de esas imágenes o abrir el PDF desde Seller Center.

**No existe `estimatedShipDate` ni `shipByDate` en la API MX.** `shippingInfo` contiene exactamente `phone`, `estimatedDeliveryDate` y `postalAddress`. Un reloj de embarque basado en un campo de la orden **no se puede construir hoy** — hay que calcularlo desde `orderDate` con la regla que averigüemos.

### 5.5 Devoluciones

Estados [DOC-MX] `/doc/mx/mx-mp/mx-mp-returns/`: `INITIATED` *"The return has been initiated by the customer"* → `COMPLETED` *"The refund has been initiated"* → `CANCELLED`.

> *"Returns can be initiated from Walmart.com, even for items purchased from Marketplace sellers **(except for HAZMAT or FREIGHT items)**"*

- **No hay endpoint para aprobar o rechazar una devolución en MX.** Solo reembolsar.
- *"You can only refund an order line that has a status of **Shipped**"* y *"The value for the amount element in the refund must be **negative**"*.
- **[NO CONFIRMADO]**: quién paga el retorno y el plazo de 30 días. La página MX de Devoluciones y reembolsos no lo trae en texto.

### 5.6 Datos del comprador — cuidado con esto

Campos confirmados en el esquema MX: `customerEmailId`, **`rfc`** (*"The RFC for the order"*), **`cfdi`** (*"Reason code of the invoice. Example: 'P01'"*), `paymentMethod`, `shippingInfo{phone, estimatedDeliveryDate, postalAddress}`, `billingInfo{phone, postalAddress}`.

**El RFC es un identificador fiscal y no tiene equivalente en ML, Amazon ni Temu.** Recomendación firme: **no persistir RFC, CFDI, domicilio ni teléfono salvo necesidad operativa demostrada**, y si se persisten, cifrarlos como se hizo con el nombre en Temu (v0.42.2, 7,699 cifrados / 0 en claro).

### 5.7 Cadencia recomendada

Con 34 SKUs: **`GET /v3/orders?statusCodeFilter=Created` cada 10–15 min** ≈ 96–144 llamadas/día contra un techo de 60/min. Sobra cuota por dos órdenes de magnitud. **No hay webhooks: es sondeo, y punto.**

---

## 6. REPUTACIÓN

### 6.1 México NO publica umbrales de desempeño

El árbol de `/mx/guides` no tiene categoría de *Performance*. Sus 14 categorías son: Soporte a Vendedor, API's y Proveedores de Soluciones, Configuración de la cuenta, Gestión de Catálogo, Gestión de Órdenes, Pagos, Facturación al cliente, Devoluciones y Reclamos, Políticas Walmart, WFS, Publicidad, Crecimiento, Contenido, Lanzamientos.
*(Matiz honesto: el índice renderizado está incompleto — existen rutas no listadas. Es **ausencia de evidencia**, no evidencia de ausencia.)*

**Chile sí la tiene** (`/cl/guides` #11 = *"Desempeño e infracciones"*). **No confundirla con MX.**

**La única métrica MX documentada es "Tasa de respuesta al vendedor"** (ligada a Message Center, act. 10-dic-2024) — **y NO publica ningún umbral.** El "menos de 24 horas" que circuló es una cita **inventada**: esa página tiene 7 capturas y una frase de presentación.

### 6.2 Los umbrales que circulan son de Estados Unidos

[DOC-US] https://marketplacelearn.walmart.com/guides/Policies%20&%20standards/Performance/Seller-performance-standards — citas literales:

| Métrica | Umbral US |
|---|---|
| Cancelación | *"Maintain a rate of **2% or below**"* (evaluada sobre **30 o 60 días**) |
| On-Time Delivery | *"**90% or above**"* |
| Valid Tracking Rate | *"**99% or above**"* |
| Tasa de respuesta | *"**95% or above**"* |
| Late Shipment | *"**5% or below**"* |
| Devoluciones | *"**6% or below**, or 9% or below for Resold inventory"* |

**La página no se declara US-only** — dice "Walmart Marketplace" y "Walmart.com". Es **SOLO-EEUU por inferencia** (dominio + ruta sin prefijo). **No aplicarlos a MX como si fueran ley**, pero son la mejor aproximación disponible.

**Terminación: no apelable.** *"Appeals of termination aren't accepted, and your product listings will be deactivated accordingly."* Supresión y suspensión sí son apelables, con *business plan of action* y documentos: *"current images of your warehouse, distributor or supplier invoices (less than two months old) or intellectual property documents"*.

**La fuente vinculante para MX es el PDF de Términos y Condiciones** (act. **10-jul-2026**), que hay que bajar desde la cuenta. La página web solo dice *"Puedes consultar en la liga adjunta el documento…"*.

### 6.3 Lo que sí se mide por API en MX

**Listing Quality Score** existe en el set MX: `/v3/insights/items/listingQuality/score` y `POST /v3/insights/items/listingQuality/items` (**2/min**), con `overAllQuality`, **`offerScore`**, `contentScore`, `ratingReviewScore`, `itemDefectCnt`, `defectRatio` (0–100).

### 6.4 🔴 Qué nos cuesta HOY tener 34 artículos publicados con inventario en cero

**La respuesta corta: casi nada en reputación, mucho en oportunidad — y el riesgo real es lo que estamos tentados a hacer para arreglarlo.**

| Concepto | Costo hoy |
|---|---|
| **Scorecard de desempeño** | **Cero.** Sin inventario no hay órdenes; sin órdenes no hay cancelaciones, ni entregas tardías, ni tracking inválido. Ninguna de las 6 métricas se ensucia. [SUPUESTO: razonamiento sólido, sin cita documental que lo diga] |
| **`offerScore` / Listing Quality** | **Sí pega.** El componente de "oferta" incluye *"whether or not the item is published and available for sale"*. 34 artículos sin stock arrastran el score de la cuenta hacia abajo. Es lo único **confirmado** que estamos pagando |
| **Visibilidad** | *"The item search API returns only items that are currently in a published status. Items that are unpublished, such as **out-of-stock items** or items affected by publishing restrictions, do not appear in search results"* [DOC, aplicable a MX]. Un artículo sin stock **no existe para el comprador** |
| **Costo de oportunidad** | El 100 % de la venta potencial. Es el costo dominante |
| **Costo monetario directo** | **$0.** No hay mensualidad, no hay cuota de listado, no hay almacenamiento (no usamos WFS) |

> ### ⚠️ EL RIESGO REAL NO ES EL CERO — ES LA TENTACIÓN DE INFLARLO
> **Publicar stock que no tenemos y luego cancelar es la forma más rápida de matar esta cuenta.** Con 34 artículos y volumen bajo, **una sola cancelación destruye cualquier porcentaje**: 1 de 10 pedidos = 10 % de cancelación contra un estándar de 2 %.
> Y hay un agravante estructural: **sin WFS, una supresión apaga el 100 % del catálogo.** No hay inventario en bodega de Walmart que siga vendiendo. [SUPUESTO sobre un hecho conocido de la cuenta]
>
> **Regla de la casa para Walmart MX: el inventario declarado es el inventario real, siempre. Cero excepciones.**

### 6.5 Cómo bajar un artículo sin quemarse

| Quiero… | Hacer | NO hacer |
|---|---|---|
| Bajarlo temporalmente | **`PUT /v3/inventory` a 0** (200/min, reversible, bucket propio) | ❌ `DELETE /v3/items/{sku}` |
| Bajarlo definitivamente | `DELETE /v3/items/{sku}` — *"Completely deactivates and un-publishes an item from the site"*, hasta 48 h | — |

**Reversibilidad del `DELETE` y reciclaje del SKU: [NO CONFIRMADO] en los dos sets.** La página MX **no dice "permanently"** (la de US sí). El "no puedes reusar el SKU" que circula viene solo de integradores terceros. **Regla por prudencia, no por doc: nunca borrar para bajar temporalmente.**

### 6.6 Categorías prohibidas — nombres EXACTOS

38 categorías prohibidas en MX [DOC-MX] https://marketplacelearn.walmart.com/mx/guides/Políticas%20Walmart/Productos%20Prohibidos/criterios-de-productos-prohibidos (el detalle está en el **PDF**, no en imágenes).

**Las que le pegan a Kubera, con el nombre literal:**
- **Categoría 14: "Bienestar sexual"** ← *esta se le cayó a un reporte previo*
- **Categoría 33: "Potenciadores y productos sexuales"** *(NO "Potenciadores sexuales y productos sexuales")*
- **Categoría 34: "Restringidos"** *(NO "Productos restringidos")*
- **Categoría 35: "Ropa y zapatos"** *(NO "Ropa y calzado")*

**Los nombres exactos importan operativamente:** la exención de UPC se pide con el nombre exacto en español de la categoría. Pedirla como "Ropa y calzado" es pedirla mal.

*(La página **no** contiene las palabras suspensión, cancelación ni sanción. Cualquier cita de consecuencias atribuida a ella es fabricada.)*

---

## 7. LO NO CONFIRMADO — y el experimento mínimo

Ordenado por **cuánto cambia una decisión de Kubera**. La columna de costo respeta la regla dura: **0 llamadas** significa que se resuelve sin tocar la API.

| # | Lo que no sabemos | Por qué importa | Experimento mínimo | Costo |
|---|---|---|---|---|
| 1 | **Base de cálculo de la comisión**: ¿con o sin IVA? ¿incluye flete? | Con flete $0 y comisión 15 %, esto define si el margen es el que creemos | **Leer el CFDI de la primera venta.** Cruzar contra `orderLines[].item.commission` y `charges[]` de esa misma orden | **0 llamadas** |
| 2 | **Plazos de aceptación y embarque en MX** | Incumplirlos es la vía directa a la suspensión, y estamos operando a ciegas | **OCR de las imágenes** de `/mx/guides/Gestión de Órdenes/Modalidad Tradicional/tiempos-de-embarque-y-recolecci-n-modalidad-tradicional`, o abrir su PDF | **0 llamadas** |
| 3 | **¿Los lotes de 200 pierden SKUs en el veredicto?** (§2.4) | Estamos reportando éxitos que quizá no ocurrieron | Comparar `len(por_sku)` contra `len(lote)` en los feedId que **ya tenemos**. Si `por_sku` tope en 20 o 50 → confirmado | **1 llamada** (`GET /v3/feeds/{id}`, 5000/min) |
| 4 | **¿`MP_MAINTENANCE` tiene bucket propio?** | Si sí, editar artículos vivos **no consume la cuota de alta**. Cambia toda la estrategia de mantenimiento | Mandar 1 feed `MP_MAINTENANCE` trivial (cambio de `keyFeatures` en 1 SKU) y leer `x-current-token-count` de la respuesta | **1 llamada** |
| 5 | **Umbrales de desempeño reales de MX** | Los 6 números que usamos son de EE.UU. | **Bajar el PDF de T&C MX** (act. 10-jul-2026) desde Seller Center | **0 llamadas** |
| 6 | **Reglas de imágenes MX** (peso, píxeles, fondo) | Un "1 MB" falso nos haría degradar el pipeline sin motivo | **Bajar el PDF** de lineamientos de imágenes / OCR de los 5 PNG. **Mientras tanto: NO tocar el pipeline** | **0 llamadas** |
| 7 | **Reglas de título y descripción MX** | Hoy truncamos a 200 sin fuente | **OCR de los 22 .jpg** de la Guía de Contenido, o pedirla en `sellerhelp.mx.walmart.com` | **0 llamadas** |
| 8 | **¿`version: "3.11"` cae bajo la deprecación de "Item Spec v3.2"?** | Es la única amenaza con reloj sobre el publicador | **Ticket a Partner Support: "¿qué `version` debe llevar `MP_ITEM_INTL` en MX hoy?"** No hay fecha de corte publicada; no es pánico | **0 llamadas** |
| 9 | **¿Los 34 artículos están realmente PUBLISHED?** | Define si el problema es stock o clasificación | `GET /v3/items?publishedStatus=UNPUBLISHED&lifecycleStatus=ACTIVE` | **1 llamada** (300/min) |
| 10 | **¿`POST /v3/feeds` devuelve `x-current-token-count`?** | Convierte la cuota en un número medible | **Registrar las cabeceras** en la respuesta del próximo feed que ya se iba a mandar. Fallback a sleep de 360 s si vienen vacías | **0 llamadas extra** |
| 11 | **Quién paga el retorno y si el plazo es 30 días** | Define el costo real de una devolución | PDF de Devoluciones y reembolsos MX / ticket | **0 llamadas** |
| 12 | **Columnas del "Reporte Histórico de Pagos"** | Es la única vía de conciliación que tenemos | Descargarlo una vez desde Seller Center | **0 llamadas** |
| 13 | **¿Las credenciales actuales sirven para webhooks de Global?** | Eliminaría el sondeo de órdenes | Solo se resuelve llamando a la API. **Bloqueado por la regla dura.** No planear sobre esto | — |
| 14 | **Retenciones fiscales a personas físicas** | Probablemente no aplica (persona moral), confirmar | El CFDI de la primera venta lo resuelve junto con el #1 | **0 llamadas** |
| 15 | **`shelf: "UNNAV"` y `productType: "default"`** | **Ya NO son alarma.** El ejemplo oficial MX de `getallitems` muestra los cuatro artículos con `shelf:"[\"UNNAV\"]"`, `productType:"default"`, **`PUBLISHED` y `ACTIVE`**. Qué significa `UNNAV` sigue sin documentarse | Buscar 2 SKUs a mano en walmart.com.mx | **0 llamadas** |

**Once de los quince se resuelven sin gastar una sola llamada.** Cuatro de ellos —el CFDI, los dos PDFs y el OCR de los tiempos de embarque— desbloquean las decisiones de margen y de riesgo operativo. Esa es la lista corta.

---

## APÉNDICE — cambios concretos en el repo

**`C:\Users\diaz2\OneDrive\Escritorio\omnicanal\backend\scripts\publicar_walmart.py`**

| Línea | Hoy | Cambiar a | Por qué |
|---|---|---|---|
| 132 | `TAM_LOTE = 200` | **`50`** | Triple respaldo: doc (50 SKUs/call), medición (85 sí / 343 no) y el paginado de `includeDetails` (§2.4) |
| 133 | `LIMITE_BYTES_FEED = 9 MB` | **sin cambio** | Está por debajo de los 25 MB duros y de los 10 MB recomendados. No hay contradicción que arreglar |
| 136 | `PAUSA_ENTRE_LOTES = 20` | **`360`** | 10 feeds/hora es el presupuesto. 20 s lo quema en 3.3 min |
| 553 | `params={"includeDetails": "true"}` | añadir **`"limit": "50"`** | El default es 20 y el máximo 50. Sin esto el veredicto por SKU puede ser parcial y silencioso |
| 417-419 | `"version": "3.11"` | **sin cambio, con vigilancia** | Lo que mandamos es lo que Walmart ejemplifica en su propia página MX. `3.11` ≠ `3.2` |
| 480 | `"msiEligible": "No"` | **sin cambio** | Es String, no booleano — y así es como ya publica con éxito |
| 472 | `productIdType: "GTIN"` + `productId: "CUSTOM"` | **sin cambio** | Es la codificación viva de la exención, distinta de como se suele contar |
| — | Sin lectura de cabeceras de cuota | **añadir log de `x-current-token-count` / `X-Next-Replenishment-Time`** | Instrumentación gratis (§2.6) |

**`C:\Users\diaz2\OneDrive\Escritorio\omnicanal\docs\WALMART_MX_HALLAZGOS.md`**
- **Línea 37: corregir.** Lista `/v3/price` entre los endpoints que dan 404 en MX. Ese diagnóstico es del 31-jul con la cuenta vacía; hoy `PUT /v3/price` y `PUT /v3/inventory` funcionan. Dejar la línea como está invita a no usar `/v3/price`.
- Línea 148 (*"Límites: 10,000 artículos y 10 MB por feed"*): matizar con el límite práctico de §2.3.

**`C:\Users\diaz2\OneDrive\Escritorio\omnicanal\spec.json`**
- Son **143 bytes de gzip** con un `301 Moved Permanently` hacia `marketplace.walmartapis.com/.../MX_MP_ITEM_INTL_SPEC.json`. **No es la spec. Borrarlo o bajarlo desde el navegador** — el contenido útil equivalente (ejemplo completo de `MP_ITEM_INTL` con `version 3.11`, `mart: WALMART_MEXICO`, `locale: es`, `processMode: REPLACE`) ya está embebido en la página pública de `bulkitemsetup`.