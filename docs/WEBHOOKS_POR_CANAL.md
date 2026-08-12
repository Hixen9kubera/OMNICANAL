# INFORME — Señales de VENTA e INVENTARIO por canal, para que el fan-out no sobrevenda

Fecha: 11-ago-2026 · Solo investigación (no se tocó código, Railway ni ningún marketplace) · Los veredictos de refutación mandan sobre la investigación inicial.

---

## 1. De una mirada

| Canal | (a) Push de VENTA | (b) Push de MOVIMIENTO DE INVENTARIO | Esfuerzo | Quién desbloquea |
|---|---|---|---|---|
| **Mercado Libre** *(referencia)* | **SÍ, vivo** — webhook HTTP `orders_v2` | **SÍ, vivo** — `fbm_stock_operations`, con tipo de operación | — | ya funciona |
| **TikTok Shop** | **SÍ** — webhook HTTP `ORDER_STATUS_CHANGE`. Mismo patrón que ML. Hoy **no hay ninguna suscripción registrada** (`total_count: 0`) | **NO aplica hoy** — solo existe vía FBT (almacén de TikTok) y no está verificado que FBT opere en MX. Enviamos de bodega propia ⇒ toda venta descuenta | **BAJO** (días) | **Yo** el código y el registro; **Brandon** solo si falta el scope en Partner Center |
| **Amazon** | **SÍ** — `ORDER_CHANGE`, pero **NO por HTTP**: obliga cola SQS + cuenta AWS | **SÍ, a medias** — `FBA_INVENTORY_AVAILABILITY_CHANGES`: es una **foto sin motivo**, no un evento de ingreso | **ALTO** | **Brandon**: cuenta AWS, rol en el portal, re-autorizar San Corpe (token nuevo) |
| **Temu** | **SÍ** — 4 eventos de pedido por HTTP… pero **no los damos de alta nosotros**: trámite en consola + compliance + autorización del vendedor | **NO EXISTE** — cero eventos de stock en todo el catálogo | **ALTO** (dominado por calendario ajeno, ~1 semana solo la URL) | **Brandon**: compliance, URL a aprobación, autorizar eventos, cargar IPs |
| **Walmart MX** | **NO** — el catálogo de eventos es **US only** | **NO EXISTE, y no aplica**: sin WFS no hay almacén de Walmart | **CERO** (no se construye) | nadie — se queda en sondeo |

---

## 2. Lo que NO existe (primero y sin rodeos)

1. **Walmart MX no tiene push. Punto.** El sistema de webhooks de Walmart existe, pero **todos los eventos** están documentados como *"Market availability: US only"* — incluido `PO_CREATED`, que es justo el que serviría. La tabla oficial de límites marca **MX = "NA" en las seis rutas de webhooks**, en una tabla donde MX sí trae números reales para otros endpoints (`GET /v3/orders` = 60/min). El portal de México no publica ninguna familia de Notifications. **Peligro concreto:** el CRUD de suscripciones sí dice "Global" y su header `WM_MARKET` acepta `MX`, así que es plausible que se cree la suscripción, devuelva 200 y un `subscriptionId` válido… y no llegue jamás un evento. Modo de falla **silencioso**: un fan-out que cree que tiene señal de venta y no la tiene = sobreventa garantizada.
 · https://developer.walmart.com/global-marketplace/docs/purchase-order-po-created-event · https://developer.walmart.com/global-marketplace/docs/rate-limiting

2. **Temu no tiene ningún evento de inventario.** El catálogo completo son **cuatro** eventos y los cuatro son de pedido/posventa. Recorrí el árbol de documentación entero (269 nodos en el portal GLOBAL, que es el de México): la sección Webhook tiene exactamente 2 páginas. La señal (b) en Temu **solo se puede sondear**.
 · https://partner.temu.com/documentation?menu_code=fb16b05f7a904765aac4af3a24b87d4a&sub_menu_code=0cc16b077a3343d08e87bc3c0a7593a8

3. **Amazon no tiene webhook HTTP.** Verificado contra el contrato OpenAPI oficial (la fuente de la que se genera su documentación): `DestinationResourceSpecification` admite **exactamente dos** destinos, `sqs` (un ARN, no una URL) y `eventBridge`. No existe propiedad `http`, `url` ni `webhook`. El patrón de ML (Amazon pega a nuestro endpoint) es **imposible**. Y EventBridge queda descartado porque solo soporta 7 tipos y ninguno es de pedidos ni de FBA ⇒ **SQS obligatorio ⇒ cuenta AWS obligatoria**.
 · https://raw.githubusercontent.com/amzn/selling-partner-api-models/main/models/notifications-api-model/notifications.json

4. **Amazon no tiene evento de "llegó mercancía al almacén".** Leí el catálogo completo (22 tipos) y los 23 esquemas del repo oficial: ninguno es de recepción/inbound. Lo único es `FBA_INVENTORY_AVAILABILITY_CHANGES`, que manda una **foto absoluta de cantidades sin campo de motivo y sin id de envío**. Una venta desde FBA y una recepción producen el mismo tipo de notificación. **No hay análogo del `fbm_stock_operations` de ML con operaciones tipadas.**
 · https://developer-docs.amazon.com/sp-api/docs/notification-type-values

5. **TikTok no tiene señal de inventario para nosotros.** El evento existe — *(24) FBT inventory update* — pero solo dispara si la tienda opera con Fulfilled by TikTok. Kubera envía de bodega propia, así que no hay mercancía nuestra en almacén de TikTok que rastrear. **NO VERIFICADO** si FBT siquiera opera en México (las fuentes que salieron son marketing de UK/US).

**Conclusión transversal:** de los cuatro canales nuevos, **solo Amazon tiene señal (b)**, y es la más débil de todas (foto sin motivo). En Temu, Walmart y TikTok la señal (b) **no existe porque el movimiento tampoco existe**: sin almacén del marketplace, toda venta descuenta bodega y punto. Eso simplifica el fan-out — el caso "pedido protegido" solo vive en ML FULL y Amazon FBA.

---

## 3. Canal por canal

### 3.1 TikTok Shop — el más barato y el que más valor da

**Lo que NO hay:** señal de inventario (ver punto 5 arriba). Y el evento de venta es **flaco**: trae `order_id` y `order_status`, nada más — ni SKU, ni precio, ni comisión.

**Lo que SÍ hay:** webhooks HTTP reales, POST JSON a nuestra URL, sin cola intermedia. **Es el patrón de ML copiado tal cual.**

**Eventos que nos importan** (el número es el `type` del payload):

| # | Evento | Para qué |
|---|---|---|
| 1 | `ORDER_STATUS_CHANGE` | **La venta.** Dispara al crearse la orden y en cada cambio de estado |
| 11 | `CANCELLATION_STATUS_CHANGE` | Cancelación → devolver stock |
| 12 | `RETURN_STATUS_CHANGE` | Devolución → devolver stock |
| 6 | `SELLER_DEAUTHORIZATION` | **El canal se murió.** Equivalente a los tokens caídos de ML |
| 7 | `UPCOMING_AUTHORIZATION_EXPIRATION` | Aviso 30 días antes de expirar. Vale oro: evita que el canal se caiga en silencio |
| 24 | *FBT inventory update* | Solo si algún día usamos FBT |

**Cómo se registra:** dos caminos oficiales. (A) Consola: Partner Center → App & Service → tu app → Basic information, se captura la URL y se eligen los topics. (B) API: **`PUT`** `https://open-api.tiktokglobalshop.com/event/202309/webhooks` con `address` y `event_type` en el cuerpo. Es **una llamada por topic**.

**Por qué falló nuestro intento anterior — tres causas, todas identificadas:**
1. Usamos `POST`. El método documentado es **`PUT`** (`GET` para listar, `DELETE` para quitar). El POST estaba condenado desde el principio.
2. El endpoint exige el scope **`seller.authorization.info`**. Hay que verificar en Partner Center → App & Service → Manage API si la app lo tiene.
3. Los parámetros `address` y `event_type` del **cuerpo** entran en el cálculo del `sign` de la llamada. Si nuestro firmador no mete el body, sale mal firmado.
 · https://partner.tiktokshop.com/docv2/page/update-shop-webhook · https://partner.tiktokshop.com/docv2/page/sign-your-api-request

**⚠️ El bug que hay que arreglar antes de tocar nada más.** La firma entrante quedó cerrada con doc oficial: `HMAC-SHA256(app_key + cuerpo_crudo, app_secret)`, en hex, header `Authorization`. **Nuestro verificador actual omite el prefijo `app_key`.** Hoy no importa porque está en modo observar; el día que alguien lo pase a rechazar **tira el 100% de los eventos legítimos** y el síntoma es silencioso: simplemente dejan de entrar ventas.
 · https://partner.tiktokshop.com/docv2/page/tts-webhooks-overview

**Lo que falta de nuestro lado:** corregir el verificador de firma; corregir el verbo a `PUT` y firmar el body; registrar al menos `ORDER_STATUS_CHANGE`; capturar `tts_notification_id` (la entrega es *at-least-once* y la firma no lleva timestamp — ese id es la **única** defensa contra duplicados: sin él vamos derecho al incidente de los pedidos duplicados de ML); responder 200 en **menos de 3 segundos** procesando en background (ya tenemos ese patrón); y planear el tercer viaje a *Get Transactions by Order* si no queremos que TikTok nazca con **comisión 0 como Amazon**.

**El bloqueador real de hoy:** no hay ninguna suscripción registrada. El `total_count: 0` que medimos no es un bug — significa que por bien construido que esté el receptor, **jamás le va a llegar un evento**.

---

### 3.2 Amazon — hay push, pero es caro y ahorra menos de lo que parece

**Lo que NO hay:** webhook HTTP (punto 3) y evento de ingreso a FBA (punto 4).

**Lo que SÍ hay, y aplica a México** (región NA, el mismo endpoint contra el que ya operamos):

- **`ORDER_CHANGE`** — la venta. Trae `FulfillmentType` (**MFN vs AFN**, que es exactamente la regla de stock que ya implementamos), `OrderStatus`, `PurchaseDate`, `MarketplaceId` y `OrderItems` con `SellerSKU` + `Quantity`. **NO trae precios, importe, comisión ni divisa** ⇒ seguiría haciendo falta `getOrder`/`getOrderItems` por cada notificación para congelar el precio. Reemplazó a `ORDER_STATUS_CHANGE` (muerto desde el 31-dic-2023).
 · https://developer-docs.amazon.com/sp-api/docs/tutorial-subscribe-to-order-change-notification
- **`FBA_INVENTORY_AVAILABILITY_CHANGES`** — el inventario. Trae `Fulfillable` + `InboundQuantityBreakdown{Working, Shipped, Receiving}`. **Es una foto, no un evento con causa.**
- **`FBA_OUTBOUND_SHIPMENT_STATUS`** — descartado explícitamente: *"Available only for FBA Onsite shipments in Amazon Brazil store"*.

**Cómo se registra:** dos pasos por API, y aquí es donde se rompe la gente — cada paso usa **un tipo de token distinto**. (1) `POST /notifications/v1/destinations` crea el destino, una sola vez por app; es *grantless* (token de aplicación, no del vendedor). (2) `POST /notifications/v1/subscriptions/{tipo}` crea la suscripción; ese sí exige token del vendedor **más el rol**.

**Sobre el 403 que dice nuestro repo** (`backend/services/scheduler.py`, líneas 78-82: *"la Notifications API de SP-API devuelve 403 (requiere rol extra + cola SQS)"*): **acierta a medias y conviene corregirlo.** La cola SQS es cierta, pero **el tipo de destino nunca produce un 403**. La causa más probable es de autenticación: llamar `createDestination` (que es *grantless*) con el token del vendedor en vez de un token de aplicación con scope `sellingpartnerapi::notifications`. Mientras ese comentario siga como está, cualquier sesión futura va a creer que el bloqueo es mayor de lo que es. **NO VERIFICADO** en cuál de las dos llamadas ocurrió el 403 histórico — no quedó traza.

**La trampa cara:** agregar el rol en el portal **no basta**. Doc oficial, textual: *"After the role is added, generate a new LWA refresh token to make valid API calls."* El `AMAZON_REFRESH_TOKEN` que hoy vive en Railway fue emitido con el conjunto de roles viejo y **no hereda el nuevo**. Hay que re-autorizar a San Corpe y sustituirlo. **Ese mismo token alimenta el sondeo de pedidos que HOY funciona**: si se pega mal, se cae la captura de ventas de Amazon.
 · https://developer-docs.amazon.com/sp-api/docs/authorization-errors

**Lo que el push NO nos ahorra:**
- **No apaga el sondeo.** **NO VERIFICADO** si `ORDER_CHANGE` dispara al **crearse** una orden nueva o solo al cambiar una existente. La doc nunca lo dice; hay dos issues oficiales preguntando exactamente eso (`amzn/selling-partner-api-models#2662`, `amzn/selling-partner-api-docs#3278`) y **Amazon no respondió en ninguno**. Se resuelve empíricamente: suscribir, dejar entrar una venta real y ver si llega. Mientras tanto, el sondeo de 5 min se queda.
- **No ahorra llamadas**, porque el evento no trae dinero.
- **La cola SQS estándar entrega duplicados y sin orden garantizado** (*"Notifications might be delivered more than once"*). Hay que deduplicar por `notificationId` — es el mismo problema de ráfagas que ya resolvimos en ML.

**Un punto abierto antes de gastar en AWS:** la tabla oficial de tipos se trunca por longitud antes de la fila de `ORDER_CHANGE`, así que **no leí verbatim su columna de mercados para México**. Todo apunta a que sí (región NA, sin exclusiones halladas) y esa tabla sí marca restricciones cuando existen (lo hace con el evento de Brasil), pero conviene cerrarlo al 100% antes de abrir la cuenta AWS. Es media hora de lectura.

---

### 3.3 Temu — el webhook existe, pero no lo prendemos nosotros

**Lo que NO hay:** ningún evento de inventario (punto 2). Y algo más importante: **probablemente el movimiento tampoco existe**. En el modelo *local seller* la mercancía no entra a un almacén de Temu — no hay equivalente de ML FULL ni de Amazon FBA. El único flujo con almacén del marketplace es *Co-Warehouse*, marcado "*Semi-managed only*" y con almacenamiento de datos US/EU. **NO VERIFICADO** si nuestra tienda MX es *Local* o *Semi-managed* — hay que confirmarlo antes de diseñar nada.

**Lo que SÍ hay:** cuatro eventos, todos de pedido/posventa, por HTTP POST directo:
`bg_order_status_change_event` (la venta: `mallId`, `parentOrderSn`, `orderSn`, `orderStatus`, `updateTime`), `bg_trade_logistics_address_changed`, `bg_aftersales_status_change`, `bg_cancel_order_status_change`.

**El punto que hay que corregir del plan:** **no existe un endpoint con el que demos de alta una URL por API.** `bg.tmc.message.update` es un **interruptor por tienda** sobre un catálogo que ya nos aprobaron. Sus propios códigos de error lo confirman: `110020009` = la app no tiene esa suscripción de evento; `110020008` = el vendedor no autorizó el evento en Seller Center. Son **cuatro candados en serie y solo el último es código nuestro**:

1. App registrada en Partner Platform **con el "Compliance and security assessment" APROBADO**.
2. Topics y URL de callback dados de alta **en la consola**, sometidos a aprobación. Textual: *"Any configuration or modification of the callback URL requires an approval process"* — **hasta una semana**, y cualquier cambio posterior de la URL vuelve a pasar por revisión.
3. **El vendedor** autoriza esos eventos a la app desde su Seller Center.
4. Recién entonces, nosotros llamamos `bg.tmc.message.update`.

**El bloqueador de hoy es anterior a todo eso:** `5000003 NOT_IN_IP_WHITE_LIST`. Hasta que las IPs estén cargadas y aprobadas no hay ni webhooks ni sondeo. **Y una alerta de arquitectura que la doc de Temu no cubre (inferencia mía, NO VERIFICADA): Railway no garantiza IP de salida estática, así que una allowlist por IP puede romperse sola con un redeploy.** Si es así, hay que sacar las llamadas a Temu por una salida con IP fija **antes** de que Brandon cargue IPs que luego cambien. Esto se mide primero; cuesta poco y evita repetir el trámite.

**Detalles técnicos ya cerrados** (para cuando toque construir): el cuerpo llega **cifrado** — `AES/CBC/PKCS5Padding` con el app secret y sus primeros 16 bytes como IV — y la firma HMAC-SHA256 se calcula sobre el texto **ya descifrado** junto con cuatro headers. No es el patrón de ML ni el de TikTok: **hay que descifrar antes de validar**. El ACK es de **500 ms** (más duro que ML y que TikTok): validar, encolar, 200 y adiós. Reintentos: 2m, 10m, 30m, 1h, 1h, 1h, 12h, 12h, máximo 8.

**Y la propia Temu lo dice por escrito: no hay que depender solo del callback, hay que mantener el sondeo periódico de órdenes.** El webhook ahorra latencia, no ahorra el sondeo ni las llamadas de detalle.

---

### 3.4 Walmart MX — cerrar el tema

**Lo que NO hay:** push, en ninguna de las dos señales (punto 1). Y la señal (b) **no es una carencia de la API**: Kubera es *seller-fulfilled* sin WFS, así que no hay almacén de Walmart que reciba mercancía. Walmart MX se comporta como ML no-FULL: **toda venta descuenta bodega, cero pedidos protegidos.** Esto solo cambiaría el día que Kubera entre a WFS — y ese día hay que rehacer este análisis entero.

**La ruta viva:** sondeo de `GET /v3/orders?statusCodeFilter=Created`. En MX el límite es **60/min**, o sea prácticamente gratis. Nota operativa: `GET /v3/orders/{purchaseOrderId}` **no existe en MX**, así que el enriquecimiento va por la misma ruta del listado.

**Dos correcciones al material que ya teníamos:**
- El manual (`docs/WALMART_MX_MANUAL.md`, línea ~277) dejaba abierta la duda de si los webhooks aplican a MX. **Queda cerrada: no.** Con citas de las páginas de eventos, que antes faltaban.
- El manual (§4.4, línea ~291) dice que la fecha "31-jul-2026" que circuló "no existe en ninguna página". **Sí existe — pero es de CANADÁ**: las APIs de Canadá se decomisionaron ese día. La conclusión no cambia (México sigue sin fecha), pero demuestra que el retiro de los sets legacy por país es real y ya se ejecutó una vez ⇒ la migración a Global de MX merece vigilancia activa, no archivo.

**Único punto abierto**, y cuesta una llamada que no crea nada: `POST /v3/webhooks/test` con `WM_MARKET: mx` apuntando a un endpoint desechable, para descartar un despliegue silencioso no documentado. Quedó fuera por la regla dura de esta investigación. **Y aunque saliera bien: no encender jamás el push de Walmart sin haber visto llegar un `PO_CREATED` real de una venta real de MX.**

---

## 4. El caso Amazon FBA — esto es lo que está roto HOY

**El mecanismo del daño, en orden:**

1. Mandamos mercancía al almacén de Amazon. Las piezas **salen físicamente de nuestra bodega**.
2. El vigilante que debería detectarlo (`stock_full.revisar_fba`) está **apagado**. Y está **doblemente apagado**: `full_watch_enabled=False` (ni siquiera se registra el job) y `full_watch_solo_registro=True` (aunque se encienda, solo anota y no escribe en Woo).
3. WooCommerce sigue creyendo que tiene esas piezas. **El stock queda inflado.**
4. **El fan-out, recién encendido, replica fielmente ese número inflado a ML, TikTok, Temu y Walmart.** El error de un canal se vuelve sobreventa en los otros cuatro.

Ese es el hueco. Y tiene precedente: el 27-jul, diez avisos tardíos de ML bajaron piezas reales — ACC-0250-NEG cayó 74→67 en 17 horas — **y el fan-out replicó fielmente cada baja a Amazon**. El mismo mecanismo, en la otra dirección.

**⚠️ El bug que hay que arreglar antes de encender el vigilante.** `revisar_fba` decide con `totalQuantity`, que la doc de Amazon define como *"The total number of units in an inbound shipment or in Amazon fulfillment centers"* — o sea **incluye `inboundWorkingQuantity`, que son piezas apenas DECLARADAS en un plan de envío y que pueden seguir físicamente en nuestra bodega**. Crear un plan de envío en Seller Central subiría `totalQuantity` y el vigilante **restaría de Woo piezas que aún no salieron** — y el fan-out lo propagaría. Es el incidente del 27-jul en espejo, esperando a ocurrir.

**Lo correcto:** pedir `details=true` y decidir con `fulfillableQuantity` + `inboundReceivingQuantity` (en la notificación: `Fulfillable` + `Receiving`), **nunca con `totalQuantity`**.

**El webhook NO resuelve esto.** `FBA_INVENTORY_AVAILABILITY_CHANGES` manda una **foto sin motivo**: una venta desde FBA y una recepción producen la misma notificación. Toda la lógica de comparar contra la foto anterior e idempotencia **sigue haciendo falta igual**. El push cambia el temporizador (segundos en vez de 15 minutos), nada más — y eso a cambio de cuenta AWS + cola SQS + rol + re-autorización del vendedor.

**Conclusión operativa, y es la más importante del informe: el vigilante FBA se puede arreglar HOY por sondeo, sin AWS, sin roles y sin tocar credenciales.** `getInventorySummaries` acepta `startDateTime` (*"all inventory summaries that have changed since then"*) y `details=true`, que desglosa `inboundWorking` / `inboundShipped` / `inboundReceiving`. Es la misma señal, con 15 minutos de retraso en vez de segundos. Para inventario que se mueve por camiones, 15 minutos es de sobra.

*(Nota aparte, ya cubierta: el otro problema de FBA — pedidos que nacen `on-hold` y al pasar a `completed` disparan la reducción de stock de Woo pese a la bandera de protección, que llevó MUE-0307-GRI a −5 — ya tiene su defensa en la compensación por `_reduced_stock`. Esa vía está resuelta; la que está abierta es la del ingreso.)*

---

## 5. Orden de implementación recomendado

El criterio: **primero se tapa la fuga que el fan-out está propagando ahora; después lo barato que da señal real; el trámite ajeno se arranca en paralelo porque es calendario, no trabajo; y lo caro va al final, cuando ya sepamos si lo necesitamos.**

**PASO 0 — Amazon FBA por sondeo, corregido. AHORA.**
No esperar al push. Arreglar `revisar_fba` para decidir con `fulfillable` + `inboundReceiving`, resolver la decisión de `full_watch_solo_registro`, y encenderlo. *Por qué primero:* es el único punto de esta lista donde **hoy se está propagando un error a cinco canales**, y es el más barato de arreglar — cero infraestructura, cero trámite, cero credenciales tocadas. Todo lo que se construya después del push de Amazon **reutiliza esta misma lógica de comparación**, así que no es trabajo tirado.

**PASO 1 — TikTok: registrar la suscripción y arreglar la firma.**
*Por qué segundo:* es el único canal donde el patrón de ML se copia **tal cual**, sin infraestructura de nube ni trámites de semanas. Máximo valor por hora invertida. Y hay un riesgo latente que conviene matar antes de que alguien lo pise: el verificador de firma incorrecto tiraría el 100% de los eventos legítimos, en silencio.

**PASO 2 — Walmart MX: cerrar y documentar.**
*Por qué:* cuesta casi nada y **evita seguir investigando** un camino que no existe. Se deja el sondeo de `GET /v3/orders` como la ruta oficial y se corrigen los dos puntos del manual. Que quede escrito para que ninguna sesión futura vuelva a gastar un día aquí.

**PASO 3 — Temu: arrancar el trámite, en paralelo con todo lo anterior.**
*Por qué ahí:* el trabajo de código es medio, pero **el calendario ajeno es de semanas** — compliance assessment + aprobación de la URL de callback (hasta una semana) + autorización del vendedor. Si se arranca hoy, el permiso llega cuando el código esté listo. **Con una precondición dura: primero medir si Railway nos da IP de salida estable.** Cargar IPs que luego cambien significa repetir la revisión desde cero. Mientras tanto Temu vive por sondeo — que además la propia Temu manda conservar.

**PASO 4 — Amazon push por SQS. Al final, y solo si hace falta.**
*Por qué último:* es el **más caro** (cuenta AWS + cola + consumidor + rol + re-autorización sobre un flujo vivo) y el que **menos ahorra** (el evento no trae dinero, así que igual hay que llamar la API; y no se puede apagar el sondeo hasta comprobar que dispara en órdenes nuevas). Su beneficio real es latencia: segundos en vez de 5 minutos en ventas, y segundos en vez de 15 minutos en inventario. Después del Paso 0 sabremos si esa latencia realmente duele. **Antes de abrir la cuenta AWS**, cerrar los dos puntos no verificados: la fila de México en la tabla de tipos, y el comportamiento en órdenes nuevas.

---

## 6. Quién desbloquea qué

### Yo, sin pedir permiso (código y lectura, nada encendido)

- Corregir el comentario equivocado del repo sobre el 403 de SP-API (`backend/services/scheduler.py`, líneas 78-82) — es documentación, y hoy desinforma a cualquier sesión futura.
- Arreglar `revisar_fba` para que decida con `fulfillable` + `inboundReceiving` en vez de `totalQuantity`, con `details=true`.
- Corregir el verificador de firma de TikTok (`app_key` + cuerpo crudo), **dejándolo en modo observar**.
- Escribir el normalizador de TikTok y su mapa de estados sin encenderlo. *(Nota: conectar un canal nuevo es literalmente eso — un normalizador y un mapa de estados. Todo lo demás — precio congelado, candado por orden, metas, PII cifrada, compensación de stock, fan-out, espejo a kubera — ya es reusable y agnóstico de canal.)*
- Medir si Railway entrega IP de salida estable (precondición de Temu).
- Prueba de humo de **solo lectura** contra Amazon para saber si nuestra app ya tiene el rol: `GET /notifications/v1/subscriptions/ORDER_CHANGE`. Un **403** = falta rol o falta re-autorizar; un **404** = el rol ya está y solo falta suscribir. No crea ni modifica nada. Es lo primero que hay que mirar antes de gastar un peso en AWS.
- Cerrar por lectura la fila de México para `ORDER_CHANGE` en la tabla de tipos de Amazon.

### Necesita acción tuya

| Qué | Dónde | Bloquea |
|---|---|---|
| Verificar/solicitar el scope `seller.authorization.info` | TikTok Partner Center → App & Service → Manage API | **Paso 1** |
| El "dale" para registrar la suscripción de TikTok | — (regla de la casa: flujo vivo) | **Paso 1** |
| Confirmar si Fulfilled by TikTok existe/se usaría en MX | TikTok / tu cuenta | señal (b) de TikTok (probablemente letra muerta) |
| Confirmar si la tienda Temu MX es *Local seller* o *Semi-managed* | Temu Seller Center | diseño de Temu |
| Compliance and security assessment + alta de URL de callback + los 4 eventos | Temu Partner Platform (~1 semana de revisión) | **Paso 3** |
| Autorizar los eventos a la app | Temu Seller Center | **Paso 3** |
| Cargar las IPs de salida en la ficha de la app (Pregunta 5 del cuestionario) | Temu Partner Platform | **Paso 3** — y **después** de medir la IP de Railway |
| **Cuenta AWS** (alta, medio de pago, dueño de las credenciales) | AWS | **Paso 4** — sin esto no hay push de Amazon, punto |
| Agregar el rol *Amazon Fulfillment* / *Inventory and Order Tracking* a la app | Amazon Solution Provider Portal | **Paso 4** |
| **Re-autorizar San Corpe y entregar el refresh token nuevo** | Amazon Seller Central | **Paso 4** — riesgo: si se pega mal, se cae el sondeo de ventas que hoy funciona |
| Acceso a Partner Center de TikTok (o login) para leer los esquemas de payload | TikTok | detalle fino de TikTok — la doc está tras login |
| El "dale" para cada encendido | — | todos los pasos |

**Nota sobre los roles de Amazon:** ni *Amazon Fulfillment* ni *Inventory and Order Tracking* son roles restringidos — no llevan revisión de PII ni de arquitectura, se piden y ya. El único restringido de esa familia (*Direct to Consumer Shipping*) **no nos hace falta**.

---

## Puntos que quedaron NO VERIFICADOS (no planear sobre ellos)

1. Si `ORDER_CHANGE` de Amazon dispara al **crearse** una orden nueva. Dos issues oficiales lo preguntan, Amazon no respondió. Solo se resuelve con una venta real.
2. La fila de México para `ORDER_CHANGE` en la tabla oficial de tipos — la página se trunca antes de esa fila.
3. Si Fulfilled by TikTok opera en México.
4. Los nombres constantes de `event_type` para los eventos de TikTok que la doc titula solo con número (24, 50, 23, 42, 17). Conozco el número y el título oficial, no la cadena exacta a mandar.
5. Si nuestra tienda de Temu MX es *Local* o *Semi-managed*. La tabla de autorización de vendedor de Temu solo trae filas para US y EU; no hay fila para GLOBAL/México — hueco documental que hay que cerrar antes de prometer fechas.
6. Si Railway entrega IP de salida estable (tema nuestro, no de Temu).
7. Qué significa exactamente "NA" en la tabla de límites de Walmart. Que sea "no disponible" es inferencia mía — pero converge con las otras cuatro evidencias.
8. **Riesgo estructural detectado de paso:** la llave primaria de la tabla `pedidos_ml` es global (`ml_order_id`), no por canal. El espejo a kubera sí usa clave compuesta, MySQL no. Dos canales que emitan el mismo texto de id se pisarían. NO VERIFICADO si es realista con los formatos de id de TikTok/Temu/Walmart; el riesgo estructural sí es real y conviene revisarlo antes de sumar el cuarto y quinto canal.

---

# ANEXO — verificación adversaria

- [❌ REFUTADA] Las notificaciones push de Walmart aplican al marketplace de MÉXICO (no solo a Estados Unidos) y podemos registrarlas con las credenciales que ya tenemos.
    Intenté tumbarla y se cayó sola: TODA página oficial que describe un EVENTO o el flujo de suscripción dice literal "Market availability: US only".

1) PO_CREATED (el evento que se quiere para VENTA) — verbatim "Market availability: US only": https://developer.walmart.com/global-marketplace/docs/purchase-order-po-created-event
2) Order management event (paraguas de PO_CREATED, PO_LINE_AUTOCANCELLED, INTENT_TO_CANCEL, FRAUD_CANCEL) — verbatim "Market availability: US only": https://developer.walmart.com/global-marketplace/docs/order-management-event
3) Guía de implementación "Subscribe to an event notification" — verbatim "Market availability: US only"; la página no menciona México ni valores de WM_MARKET en ningún lado: https://developer.walmart.com/global-marketplace/docs/subscribe-to-an-event-notification
4) Tabla oficial de rate limits — la columna MX dice "NA" en LAS SEIS rutas de webhooks (Test Notification 10/min US, All Subscriptions 50/min US, Create Subscription 200/min US, Delete 10/min US, Update 5/min US, Event Types 5/min US → todas NA en CA, MX y CL), mientras que en la misma tabla MX sí trae número real para otros endpoints (GET /v3/orders = 60/min). O sea el "NA" no es hueco de maquetación: https://developer.walmart.com/global-marketplace/docs/rate-limiting
5) El portal de México no publica ninguna familia de Notifications/Webhooks/Events/Subscriptions — solo Items, Inventory, Promotions y Ship with Walmart: https://developer.walmart.com/mx-marketplace
6) La "Notifications overview" de US no declara disponibilidad geográfica alguna (no hay ninguna línea que extienda el servicio fuera de US): https://developer.walmart.com/us-marketplace/docs/notifications-overview

LA ÚNICA SEÑAL A FAVOR, y es la débil: las páginas de referencia del CRUD (p.ej. createsubscription) dicen "Market availability: Global" y su header WM_MARKET está documentado como "Identifies the Walmart Marketplace region for the request. If not specified, US is used by default" con allowed values "US, CA, MX, CL": https://developer.walmart.com/global-marketplace/reference/createsubscription — eso es plomería compartida entre mercados, NO evidencia de que existan eventos que se disparen para MX. Nota adversarial: CA también aparece como allowed value en WM_MARKET y también sale "NA" en las seis filas de rate limits, lo que confirma que el enum del header no implica disponibilidad del servicio.

Sobre la segunda mitad de la afirmación ("con las credenciales que ya tenemos"): no encontré doc que exija rol extra, app aprobada aparte ni onboarding para webhooks (a diferencia de SP-API de Amazon), así que técnicamente las WM_CLIENT_ID/WM_CLIENT_SECRET actuales podrían firmar la llamada. Pero eso NO rescata la afirmación: es justamente lo que la vuelve peligrosa — es plausible que POST /v3/webhooks/subscriptions con WM_MARKET: mx devuelva 200 y un subscriptionId válido y no llegue jamás un evento. Modo de falla silencioso.

NO VERIFICADO (lo digo explícitamente): la página de rate limits no define en ninguna parte qué significa "NA"; que sea "no disponible" es inferencia mía, aunque converge con las otras cuatro evidencias. Y no probé la llamada real (regla dura de solo-investigación), así que no puedo descartar al 100% un despliegue silencioso no documentado.
    CORRECCIÓN: La versión correcta: "El SISTEMA de webhooks de Walmart existe y su CRUD de suscripciones está marcado 'Global' (WM_MARKET acepta US, CA, MX, CL), pero el CATÁLOGO DE EVENTOS es US only: PO_CREATED, PO_LINE_AUTOCANCELLED, INTENT_TO_CANCEL y FRAUD_CANCEL están documentados como 'Market availability: US only', la tabla oficial de rate limits marca MX (y CA y CL) como 'NA' en las seis rutas de webhooks, y el portal mx-marketplace no publica familia de Notifications. Suscribirse desde México sería suscribirse a nada, probablemente con un 200 engañoso. Para Walmart MX la única señal de VENTA viable hoy es el sondeo de GET /v3/orders?statusCodeFilter=Created (60/min en MX, prácticamente gratis). NO construir el fan-out sobre push de Walmart, y no encenderlo jamás sin haber visto llegar un PO_CREATED real de una venta real de MX."
- [❌ REFUTADA] Temu Open API ofrece webhooks/callbacks de pedidos que podemos registrar nosotros.
    Leí la doc oficial del portal GLOBAL (el que corresponde a México) renderizada en navegador; WebFetch devuelve vacío porque es un SPA.

QUÉ SÍ ES CIERTO (verificado verbatim):
1) Los webhooks existen y son HTTP POST directo a nuestra URL, sin cola intermedia. "Webhook Integration Guide": https://partner.temu.com/documentation?menu_code=38e79b35d2cb463d85619c1c786dd303&sub_menu_code=c4684d731f944e96b117dd8203724da2 — headers x-tm-app-key, x-tm-event-code, x-tm-timestamp, x-tm-signature, x-tm-ext-param; body {"eventData": "<base64 cifrado>"}; firma HMAC-SHA256 hex sobre los pares ordenados (incluye eventData YA DESCIFRADO); AES/CBC/PKCS5Padding con el app secret y los primeros 16 bytes del secret como IV (esto último lo confirmé leyendo el código de ejemplo de la propia página: DEFAULT_CIPHER_ALGORITHM = "AES/CBC/PKCS5Padding", ivBytes = copia de 16 bytes del key). ACK: 200 en menos de 500 ms; reintentos 2m, 10m, 30m, 1h, 1h, 1h, 12h, 12h, máximo 8 y abandona.
2) Los eventos son de PEDIDO, y son exactamente cuatro. "The event of webhook": https://partner.temu.com/documentation?menu_code=fb16b05f7a904765aac4af3a24b87d4a&sub_menu_code=0cc16b077a3343d08e87bc3c0a7593a8 — bg_order_status_change_event (mallId, parentOrderSn, orderSn, orderStatus, updateTime), bg_trade_logistics_address_changed, bg_aftersales_status_change, bg_cancel_order_status_change. La sección "Activate or deactivate event subscriptions" de la guía enumera esos mismos 4 como valores válidos de permitEventCodeList. No hay ningún evento de inventario/stock.
3) La API existe y aplica a México: bg.tmc.message.update, https://partner.temu.com/documentation?menu_code=fb16b05f7a904765aac4af3a24b87d4a&sub_menu_code=6e5817f037534093ba2dd91399aeff46 — Request URL POST https://openapi-b-global.temu.com/openapi/router, Site Region GLOBAL (el host de MX). Permission packages: Local Basic Management y Semi Basic Management, ambos para app type private y public.

POR QUÉ LA AFIRMACIÓN NO SE SOSTIENE TAL COMO ESTÁ ESCRITA — el verbo "registrar" es donde se cae:
La guía lista CUATRO condiciones acumulativas para recibir un push, y sólo la última es API nuestra:
 (a) app registrada en el Partner Platform CON el "Compliance and security assessment" APROBADO;
 (b) la app suscrita a los topics CON una push website válida — se hace en la consola del Partner Platform, no por API;
 (c) el VENDEDOR autoriza esos topics a la app desde el Seller Center;
 (d) recién entonces la app suscribe al vendedor vía bg.tmc.message.update.
Además, textual de la guía sobre la URL de callback: "Any configuration or modification of the callback URL requires an approval process", y sobre el alta de eventos en consola: la aprobación puede tardar hasta una semana (estado consultable en el Operation Log), y sólo son elegibles las apps aprobadas que pasaron el compliance assessment.
Los códigos de error de bg.tmc.message.update confirman que la API NO registra nada, sólo prende/apaga lo ya aprobado: 110020009 = la app no tiene esa suscripción de evento; 110020008 = el access_token no tiene acceso al evento, hay que pedirle al vendedor que lo autorice en Seller Center y compartir un access_token nuevo.

Es decir: bg.tmc.message.update es un interruptor por tienda sobre un catálogo que ya nos aprobaron; NO es "registrar un webhook". No existe endpoint tipo createDestination/createSubscription con el que demos de alta una URL por API. La única palanca autoservicio es (d), y las tres anteriores son trámite ajeno (consola + compliance + aprobación de URL + acción del vendedor).

CONTEXTO QUE AGRAVA: la propia guía dice que no es prudente depender sólo del callback y que hay que mantener tareas periódicas de jalar órdenes; o sea el webhook no ahorra el sondeo. Y el evento de venta trae sólo IDs y estado — hay que ir igual a bg.order.detail.v2.get para armar el pedido. Sumado al bloqueador actual de Kubera (5000003 NOT_IN_IP_WHITE_LIST), hoy no podemos ni siquiera llamar la API que sí es nuestra.

NO VERIFICADO / DUDA ADICIONAL PARA MÉXICO: en el "Seller Authorization Guide" (https://partner.temu.com/documentation?menu_code=38e79b35d2cb463d85619c1c786dd303&sub_menu_code=1e91f4383a8340c2b98780acc34fa6ec) la tabla de autorización de vendedor sólo trae filas para US y EU (Crossborder y Local); no hay fila para GLOBAL/México. La URL de Seller Center para MX no está documentada ahí. Como el paso (c) depende de que Brandon autorice los eventos desde SU Seller Center, ese hueco documental hay que cerrarlo antes de prometer fechas.
    CORRECCIÓN: Versión correcta: "Temu Open API SÍ tiene webhooks HTTP de PEDIDO (exactamente 4 eventos: bg_order_status_change_event, bg_trade_logistics_address_changed, bg_aftersales_status_change, bg_cancel_order_status_change; ninguno de inventario), pero NO los registramos nosotros por API. La API bg.tmc.message.update sólo ACTIVA/DESACTIVA por tienda eventos que la app ya tiene aprobados. El alta real de la URL de callback y de los topics se hace en la consola del Partner Platform, exige el Compliance and security assessment aprobado y pasa por una revisión que puede tardar hasta una semana, y además requiere que el vendedor autorice cada evento en su Seller Center. Son cuatro candados en serie, tres de ellos fuera de nuestro código. Y aun con el webhook vivo, la propia doc de Temu manda conservar el sondeo periódico de órdenes."
- [❌ REFUTADA] Amazon SP-API Notifications puede entregar a un endpoint HTTP nuestro SIN montar SQS ni EventBridge.
    REFUTADA con la fuente más dura que existe: el contrato OpenAPI oficial de Amazon (repo amzn/selling-partner-api-models), que es de donde se genera la doc pública. Fetch verbatim de https://raw.githubusercontent.com/amzn/selling-partner-api-models/main/models/notifications-api-model/notifications.json:

(1) "DestinationResourceSpecification": { "type":"object", "properties": { "sqs": {"description":"The information required to create an Amazon Simple Queue Service (SQS) queue destination.","$ref":"#/definitions/SqsResource"}, "eventBridge": {"description":"The information required to create an Amazon EventBridge destination.","$ref":"#/definitions/EventBridgeResourceSpecification"} }, "description":"The information required to create a destination resource. Applications should use one resource type (sqs or eventBridge) per destination." }

(2) "DestinationResource" (el objeto que DEVUELVE la API): exactamente las mismas dos propiedades, sqs y eventBridge. "description":"The destination resource types."

(3) "SqsResource": requiere "arn" con pattern "^arn:aws:sqs:\\S+:\\S+:\\S+" — o sea, un ARN de AWS, no una URL. "EventBridgeResourceSpecification": requiere accountId + region, donde accountId es "The identifier for the AWS account that is responsible for charges related to receiving notifications".

(4) Barrido del documento completo buscando http / https / url / webhook / endpoint como tipo de destino: NO existe ninguna propiedad de destino distinta de sqs y eventBridge. El único createDestination del modelo es POST /notifications/v1/destinations y su body solo admite resourceSpecification con esas dos llaves.

(5) No hay una versión más nueva escondida: el listado del directorio del modelo (https://api.github.com/repos/amzn/selling-partner-api-models/contents/models/notifications-api-model) contiene UN solo archivo, notifications.json, versión "v1". No hay v2 que agregue destinos HTTP.

(6) Búsquedas dirigidas a encontrar un anuncio de soporte de webhooks HTTP (2025-2026) no arrojaron NINGUNA página oficial; todo el material oficial y de AWS describe solo los dos flujos: SQS (https://aws.amazon.com/cn/blogs/china/detailed-tutorial-on-the-use-of-notifications-api-in-sp-api-with-amazon-sqs) y EventBridge (https://aws.amazon.com/cn/blogs/china/detailed-tutorial-on-the-use-of-notifications-api-with-amazon-eventbridge-in-sp-api).

LIMITACIÓN HONESTA DE ESTA VERIFICACIÓN: developer-docs.amazon.com estuvo devolviendo 301 a un host roto (developer-docs.amazon, sin .com) durante toda la sesión, así que NO pude releer verbatim las páginas de prosa (set-up-notifications, set-up-notifications-with-amazon-sqs, createDestination reference). Eso no debilita el veredicto: el schema OpenAPI del repo oficial de Amazon es normativo y es la misma fuente que alimenta esas páginas, y ahí el enum de destinos es cerrado.

Un matiz que NO rescata la afirmación: sí se puede terminar recibiendo un POST HTTP en Railway, pero SIEMPRE con AWS de por medio — Lambda leyendo la cola SQS y reenviando, o una EventBridge API Destination. Eso es precisamente "montar SQS o EventBridge", que es lo que la afirmación niega. Y EventBridge además está descartado para nuestro caso porque ORDER_CHANGE y FBA_INVENTORY_AVAILABILITY_CHANGES no están entre los tipos soportados por ese flujo.
    CORRECCIÓN: La realidad: Amazon SP-API Notifications NO ofrece entrega a un endpoint HTTP propio. El esquema oficial DestinationResourceSpecification admite exactamente dos destinos — sqs (ARN de cola SQS, y debe ser cola ESTÁNDAR, no FIFO) y eventBridge (accountId + region) — y ninguno es una URL. Por lo tanto el patrón de Mercado Libre (Amazon pega a POST /api/webhooks/amazon con guarda absoluta y 200 siempre) es IMPOSIBLE de replicar: Amazon deposita en una cola/bus de AWS y nosotros jalamos. Consecuencia dura para el fan-out: cuenta AWS OBLIGATORIA (cola SQS estándar con política que permita SendMessage al principal arn:aws:iam::437568002678:root, más un consumidor corriendo; la cola no empuja sola). Si se quiere un POST HTTP a Railway, hay que construirlo nosotros (Lambda/consumidor que lea SQS y reenvíe), y eso es infraestructura AWS, no una alternativa a ella. El comentario del repo en backend/services/scheduler.py acierta en que hace falta cola SQS; lo que ese comentario atribuye mal es el 403 (el tipo de destino nunca produce 403; eso es rol o token).
- [❌ REFUTADA] "Existe un notificationType de SP-API que avisa cuando llega mercancía NUEVA al almacén FBA (un ingreso), no solo cuando cambia la disponibilidad."
    REFUTADA con el catálogo COMPLETO leído hoy (11-ago-2026) en la página oficial https://developer-docs.amazon.com/sp-api/docs/notification-type-values (nota: WebFetch rebota por un 301 mal formado hacia "developer-docs.amazon"; la leí con el navegador y extraje los encabezados H2 del <article>, o sea el índice literal de tipos, no un resumen).

(1) CATÁLOGO VERBATIM, los 22 notificationType que publica la página, en orden: ACCOUNT_STATUS_CHANGED, ANY_OFFER_CHANGED, B2B_ANY_OFFER_CHANGED, BRANDED_ITEM_CONTENT_CHANGE, DETAIL_PAGE_TRAFFIC_EVENT, FBA_INVENTORY_AVAILABILITY_CHANGES, EXTERNAL_FULFILLMENT_SHIPMENT_STATUS_CHANGE, FBA_OUTBOUND_SHIPMENT_STATUS, FEE_PROMOTION, FEED_PROCESSING_FINISHED, FULFILLMENT_ORDER_STATUS, ITEM_INVENTORY_EVENT_CHANGE, ITEM_SALES_EVENT_CHANGE, ITEM_PRODUCT_TYPE_CHANGE, LISTINGS_ITEM_STATUS_CHANGE, LISTINGS_ITEM_ISSUES_CHANGE, LISTINGS_ITEM_MFN_QUANTITY_CHANGE, ORDER_CHANGE, PRICING_HEALTH, PRODUCT_TYPE_DEFINITIONS_CHANGE, REPORT_PROCESSING_FINISHED, SHIPMENT_TRACKING_MILESTONE_CHANGED, TRANSACTION_UPDATE. NINGUNO es de recepción/ingreso inbound. (Ojo: este listado corrige el reporte previo, que no había visto EXTERNAL_FULFILLMENT_SHIPMENT_STATUS_CHANGE.)

(2) BÚSQUEDA DE TEXTO EN TODA LA PÁGINA por "inbound|receiv|receipt": los ÚNICOS aciertos operativos son campos de CANTIDAD dentro de FBA_INVENTORY_AVAILABILITY_CHANGES — "InboundQuantityBreakdown: Details of the affected item's inbound units, which are either still in WORKING status or on the way to be received in Amazon warehouses" y "Receiving: Number of units of the affected item that arrived and are in progress to be received in Amazon warehouses". Son cubetas de una FOTO de disponibilidad, no un evento de ingreso. El otro acierto, "ReceivedDateTime — The date and time when the returned item was received by the Amazon fulfillment center", pertenece a FULFILLMENT_ORDER_STATUS y es de DEVOLUCIONES de MCF, no de mercancía nueva.

(3) EL DISPARADOR DECLARADO ES EXPLÍCITAMENTE "cambio de cantidades", no "ingreso": "The FBA_INVENTORY_AVAILABILITY_CHANGES notification is sent whenever there is a change in the Fulfillment By Amazon (FBA) inventory quantities. This notification includes a snapshot of the FBA inventory in all eligible Amazon stores in a particular region." (misma página).

(4) EL ESQUEMA OFICIAL CONFIRMA QUE NO HAY MOTIVO NI SHIPMENT ID: https://raw.githubusercontent.com/amzn/selling-partner-api-models/main/schemas/notifications/FBAInventoryAvailabilityChangeNotification.json — no existe campo de evento, de razón/reason ni de shipmentId; solo SellerId, FNSKU, ASIN, SKU y cantidades por marketplace.

(5) EL REPO DE MODELOS NO TIENE ESQUEMA DE RECEPCIÓN: https://api.github.com/repos/amzn/selling-partner-api-models/contents/schemas/notifications devuelve 23 archivos (AnyOfferChanged, B2bAnyOfferChanged, BrandedItemContentChange, DataKioskQueryProcessingFinished, DetailPageTrafficEvent, FBAInventoryAvailabilityChange, FBAOutboundShipmentStatus, FeePromotion, FeedProcessingFinished, FulfillmentOrderStatus, ItemInventoryEventChange, ItemProductTypeChange, ItemSalesEventChange, ListingsItemIssuesChange (+_2023-12-13), ListingsItemMfnQuantityChange, ListingsItemStatusChange, OrderChange, PricingHealth, ProductTypeDefinitionsChange, ReportProcessingFinished, ShipmentTrackingMilestoneChanged, TransactionUpdate). Ninguno inbound/receipt.

(6) TRAMPAS DESCARTADAS UNA POR UNA, con su descripción oficial leída: ITEM_INVENTORY_EVENT_CHANGE — el nombre engaña, es un feed HORARIO por ASIN ("sent five minutes after the beginning of each hour... ASINs are included if the number of units available for purchase by customers has changed") con un solo número, highlyAvailableInventory: es disponibilidad agregada, ni ingreso ni causa, y encima con retraso de hasta 24 h. EXTERNAL_FULFILLMENT_SHIPMENT_STATUS_CHANGE — es de SALIDA: "change in the order status for a warehouse integration order", estados ACCEPTED/CONFIRMED/PACKAGE_CREATED/.../SHIPPED/DELIVERED/CANCELLED. FBA_OUTBOUND_SHIPMENT_STATUS — outbound y además "Brazil only" (FBA Onsite). FULFILLMENT_ORDER_STATUS — MCF, pedidos que TÚ mandas surtir.

(7) La comunidad preguntó exactamente esto y Amazon no ofreció ningún evento: https://github.com/amzn/selling-partner-api-models/issues/2078 ("Getting FBA inbound shipment availability with SP-API"), sin respuesta sustantiva de Amazon y cerrado.

Nota de honestidad: no puedo probar un negativo absoluto (podría existir algo no documentado o en beta cerrada). Pero para lo que decide inversión, la doc oficial no ofrece tal evento, y planear sobre él sería planear sobre nada.
    CORRECCIÓN: La versión correcta: NO existe ningún notificationType de SP-API que avise de un INGRESO de mercancía a FBA. El único push relacionado es FBA_INVENTORY_AVAILABILITY_CHANGES, que se dispara por "a change in the FBA inventory quantities" y entrega una FOTO ABSOLUTA por SKU/marketplace (Fulfillable, Unfulfillable, Researching, InboundQuantityBreakdown{Working, Shipped, Receiving}, ReservedQuantityBreakdown) SIN campo de motivo y SIN id de envío. Una venta desde FBA y una recepción en el centro logístico producen exactamente el mismo tipo de notificación; el ingreso solo se INFIERE diferenciando contra la foto anterior (típicamente Receiving baja y Fulfillable sube). Es decir: Amazon NO tiene análogo del 'fbm_stock_operations' de ML con operaciones tipadas (INBOUND_RECEPTION, TRANSFER_DELIVERY, WITHDRAWAL_*). Consecuencia de diseño para el fan-out: el webhook NO sustituye la lógica de diff e idempotencia de stock_full.revisar_fba — solo sustituye el TEMPORIZADOR (segundos en vez de 15 min), y eso a cambio de cuenta AWS + cola SQS estándar + rol 'Amazon Fulfillment' + re-autorización del seller. Si lo que se quiere es saber POR QUÉ se movió el inventario (ingresos con su motivo), eso NO vive en notificaciones: vive en el reporte asíncrono GET_LEDGER_DETAIL_VIEW_DATA con eventType=Receipts, y en el sondeo de Fulfillment Inbound / getInventorySummaries(details=true) — horas de latencia, sirve para conciliar, no para tiempo real. Y una advertencia que sigue viva del análisis anterior: no decidir con totalQuantity (incluye inboundWorkingQuantity, o sea piezas apenas DECLARADAS en un plan de envío que aún están en bodega propia); decidir con Fulfillable + Receiving.

---

# ANEXO — crítica del plan

## REVISIÓN ADVERSARIA — problemas concretos del plan

Verifiqué contra el repo (solo lectura). Numeración por severidad dentro de cada grupo.

---

### A. Afirmaciones que el propio código desmiente

**A1. La frase que sostiene TODA la priorización es falsa.** El plan dice (§4 paso 4, §1, §5) que el fan-out "replica ese número inflado a ML, TikTok, Temu y Walmart" y que "el error de un canal se vuelve sobreventa en los otros cuatro". El fan-out escribe a **dos** canales:
`backend/services/fanout_stock.py:431` → `_ESCRITORES = {"mercado_libre": _escribir_ml, "amazon": _escribir_amazon}`, con el comentario de la línea 432-433: *"temu / tiktok: se suman aquí cuando su escritura por M2E esté probada"*.
Y aunque hubiera escritores, `_destinos()` (línea 257) solo lee `canal_inventario`, que puebla exclusivamente `scheduler._job` con `sincronizar_ml` + `sincronizar_amazon` — no hay ni una fila de tiktok/temu/walmart. "Cinco canales" es retórica, son dos. Corregir el número y rehacer el argumento de por qué PASO 0 va primero.

**A2. El plan afirma que el fan-out "acaba de encenderse" sin decir en qué modo.** Defaults del código: `fanout_enabled=False`, `fanout_dry_run=True`, `fanout_canales=""` (`backend/config.py:336-341`). En dry-run **no escribe nada** y la urgencia de PASO 0 se desinfla; con dry-run apagado y `FANOUT_CANALES` vacío escribe a todos los canales implementados. El orden de trabajo completo está construido sobre un estado de variable que el plan nunca leyó. Leer `FANOUT_ENABLED/DRY_RUN/CANALES/RESERVA` en Railway ANTES de ordenar los pasos.

**A3. La corrección de métrica de PASO 0 está incompleta y encendería con un falso positivo garantizado en la primera vuelta.** El plan dice "decidir con `fulfillable` + `inboundReceiving`, nunca con `totalQuantity`". Pero la línea base con la que compara viene de otro sitio y de otra métrica:
- `stock_full.py:385` lee `totalQuantity` de la API;
- `stock_full.py:364` siembra `previos` desde `canal_inventario.stock_fba`;
- `inventario.py:390` guarda en esa columna `det.get("fulfillableQuantity", s.get("totalQuantity", 0))` → **fulfillableQuantity**.

O sea: hoy compara `totalQuantity` (que incluye inboundWorking/Shipped/Receiving/reserved) contra un baseline de `fulfillableQuantity`. Para todo SKU con inbound o reservado, `ahora > antes_fba` en la **primera** ejecución → resta a Woo piezas que nunca se movieron. Cambiar la métrica a `fulfillable+receiving` tampoco lo cuadra (el seed es `fulfillable` a secas). El plan no menciona la siembra ni una sola vez.

**A4. El sondeo de FBA no es "la misma señal con 15 min de retraso" — es lossy por construcción.** `revisar_fba` solo actúa si `ahora > antes_fba` (`stock_full.py:395`) y solo escribe una referencia nueva cuando aplica/simula una subida. Tras cualquier **venta** desde FBA la referencia se queda en el pico viejo, así que los ingresos posteriores por debajo de ese pico se pierden en silencio. El plan vende PASO 0 como "la misma señal, 15 minutos tarde"; no lo es. (Y el push de Amazon tampoco lo arregla: la lógica de diff es la misma.)

**A5. El modo solo-registro CONSUME los ingresos: encender el flag después no los recupera.** `previos` se reconstruye con `WHERE accion LIKE 'fba_%%'` (`stock_full.py:355`), y el modo simulación escribe filas `fba_ingreso_sim` (línea 401). Es decir: observar **avanza la línea base sin tocar Woo**. Todo ingreso visto durante la observación queda sellado y jamás se aplicará al voltear `FULL_WATCH_SOLO_REGISTRO`. PASO 0 dice "resolver la decisión de solo_registro y encenderlo" sin ningún paso de reconciliación → esas piezas quedan infladas en Woo para siempre.

**A6. El comentario equivocado del 403 está en DOS lugares; el plan solo nombra uno.** Además de `backend/services/scheduler.py:78-82`, la misma afirmación vive en el docstring de `revisar_fba`: `backend/services/stock_full.py:331-334` ("*hoy devuelven 403: requieren un rol extra…*"). Si solo se corrige el scheduler, la desinformación sobrevive justo en el archivo que alguien va a abrir para arreglar el vigilante.

---

### B. Nombres de evento/endpoint que se usan como verificados y no lo están

**B1. Ningún `event_type` de TikTok está verificado como cadena.** §3.1 pone en tipografía de código `ORDER_STATUS_CHANGE`, `CANCELLATION_STATUS_CHANGE`, `RETURN_STATUS_CHANGE`, `SELLER_DEAUTHORIZATION`, `UPCOMING_AUTHORIZATION_EXPIRATION` — pero la ficha solo verificó **títulos de página y números de type**; ninguna página de doc oficial leída muestra el string que va en el cuerpo del PUT. PASO 1 ("registrar al menos `ORDER_STATUS_CHANGE`") depende de un valor inventado por convención — exactamente la misma clase de error que el POST-en-vez-de-PUT que ya nos costó el intento anterior. Falta un paso previo: descubrir el enum real (`GET /event/202309/webhooks` o la consola) antes de firmar ningún PUT.

**B2. Doble rasero entre Amazon y TikTok.** A Amazon se le exige NO VERIFICADO por "¿dispara al crearse la orden?" (correcto). A TikTok se le concede "Dispara al crearse la orden y en cada cambio de estado" como hecho — cuando la ficha declara que las páginas están tras login y que todo se reconstruyó de "títulos oficiales, slugs de URL y snippets indexados". Misma duda, mismo tratamiento: marcarlo NO VERIFICADO o el plan asume que TikTok avisa altas cuando quizá solo avisa cambios.

**B3. La tabla de "una mirada" contradice los NO VERIFICADOS.** §1 pone Amazon (a) = "**SÍ**" sin asterisco, cuando el punto abierto #1 dice que no se sabe si `ORDER_CHANGE` dispara en orden nueva. La tabla es lo que se lee de un vistazo y lo que se cita después.

**B4. La regla diagnóstica 403/404 de Amazon es inferencia presentada como procedimiento.** §6: "un **403** = falta rol o falta re-autorizar; un **404** = el rol ya está". No hay doc que lo respalde; SP-API devuelve 403 por token, por throttle y por autorización genérica. Y de esa lectura cuelga la decisión de abrir (o no) una cuenta AWS.

---

### C. Pasos que dejan ventanas SIN entrada de pedidos

**C1. TikTok no captura ni una venta hoy, y PASO 1 tampoco la captura.** Hoy: el receptor propio solo hace `append` a un `deque` en memoria y declara explícitamente que no toca la base (`backend/routers/webhooks.py:434-452` y `483-523`); y el sondeo M2E **salta TikTok** porque filtra `a.get("is_valid")` (`backend/services/pedidos_m2e.py:112`) y esa conexión está inválida. Resultado: cada venta de TikTok deja Woo intacto → Woo inflado → el fan-out empuja el número inflado a ML y Amazon. Ese es un camino de sobreventa **vivo** que el plan no lista en ninguna parte.
Y PASO 1 tal como está redactado (registrar suscripción + arreglar firma) **no lo cierra**: después de PASO 1 los eventos siguen cayendo en un buffer de 300 en memoria y no nace ningún pedido. Falta el paso explícito "TikTok → `pedidos_ml` + descuento de bodega" con su fecha.

**C2. El dedupe por `tts_notification_id` no tiene dónde vivir.** El plan lo pide como "única defensa contra duplicados", pero el receptor no tiene tabla y el anillo se pierde en cada deploy de Railway. Dedupe real = tabla nueva + escritura + índice. No está presupuestado en "BAJO (días)".

**C3. Walmart: el plan cierra el tema y deja el agujero abierto, etiquetado como hecho.** §1 le pone esfuerzo "CERO (no se construye)" y "nadie desbloquea", y §3.4 llama al sondeo "la ruta viva". **No existe ningún poller de pedidos de Walmart en el repo** (solo `backend/scripts/publicar_walmart.py` y `backend/scripts/estado_walmart.py`, ambos scripts manuales), Walmart no aparece en `scheduler.iniciar()`, y las credenciales `WM_*` no están guardadas en ningún lado (memoria del proyecto). Si hay publicaciones vivas en Walmart MX, sus ventas son tan invisibles como las de TikTok. PASO 2 no es "cerrar y documentar": es "construir la única entrada de pedidos que este canal puede tener", y eso no cuesta cero.

**C4. Temu: el plan ignora la ruta que SÍ está viva y crea un riesgo de doble ingesta.** Hoy los pedidos de Temu entran por **M2E Cloud** (`backend/services/pedidos_m2e.py`, `POST /order/find/?channel=temu`), no por la Open API — el plan nunca menciona M2E y dice "mientras tanto Temu vive por sondeo" como si fuera el sondeo de la Open API (que está bloqueado por `NOT_IN_IP_WHITE_LIST`). Consecuencia que el plan no ve: si algún día se enciende el webhook de la Open API **sin apagar M2E**, la misma venta entra dos veces con identificadores distintos (`id` de M2E vs `parentOrderSn`/`orderSn` de Temu) → dos pedidos de Woo → **doble descuento de bodega**. Y la idempotencia no lo atrapa: la PK es `ml_order_id VARCHAR(30) PRIMARY KEY` a secas (`backend/services/pedidos_ml.py:85`), sin canal.

**C5. El riesgo de PK está mal clasificado.** El plan lo entierra como punto #8 de "NO VERIFICADO / de paso". Con C4 arriba, es **precondición de PASO 1 y de PASO 3**, no un pendiente de fondo: en cuanto TikTok o Temu-OpenAPI empiecen a insertar en `pedidos_ml`, el espacio de ids es compartido y no hay columna que desempate en la PK.

**C6. La re-autorización de Amazon no tiene procedimiento, solo una advertencia.** PASO 4 dice "riesgo: si se pega mal, se cae el sondeo". Falta todo lo que hace que eso no pase: ¿la re-autorización invalida el refresh token actual? (no verificado, y el plan ni lo pregunta); orden exacto de las operaciones; ventana; verificación previa; rollback. Mientras dure esa ventana quedan ciegos a la vez `pedidos_amazon.revisar`, `stock_full.revisar_fba` y las lecturas/escrituras de Amazon del fan-out — los tres cuelgan del mismo `amazon._access_token`.

---

### D. Esfuerzo subestimado y dependencias mal atribuidas

**D1. TikTok "BAJO (días)" no cuadra con su propia lista.** Lo que el plan mismo enumera o implica: tabla de persistencia + dedupe (C2), segundo viaje a Get Order Detail, tercer viaje a Get Transactions by Order, cifrado de PII (política de la casa), espejo kubera, normalizador + mapa de estados, PK por canal (C5), 200 en <3 s con proceso en background, y acceso al Partner Center para leer los esquemas (que están tras login). Eso no son "días".

**D2. "Yo el código y el registro; Brandon solo si falta el scope" es una atribución falsa.** Registrar la suscripción cambia la configuración viva de la app de un canal en producción → regla 3 (dale de Brandon), no "sin pedir permiso". Y Brandon (o su login) hace falta además para: el scope, leer los esquemas de payload, y confirmar FBT MX. En §1 aparecen como si fueran nuestras.

**D3. PASO 0 rotulado "AHORA. No esperar" termina encendiendo un escritor de stock.** Eso es flujo vivo (regla 3). Peor: propone saltarse el modo observación que nació precisamente del incidente del 27-jul (docstring `stock_full.py:32-41`), sin ventana de sombra, sin tope de cordura más allá de `max(0, …)` y —por A3/A5— con el baseline roto. El paso más barato del plan es también el que más se parece al incidente que dice evitar.

**D4. La lista de "cerrar antes de abrir AWS" está incompleta.** El plan nombra dos puntos. Las fichas dejan al menos seis más: el JSON de la política de acceso de la cola (su página oficial dio 404), la región AWS obligatoria de la cola, el límite de destinos por app, retención/visibility timeout, qué roles tiene HOY la app, y en cuál de las dos llamadas ocurrió el 403 histórico. Súmese lo no costeado: un **consumidor corriendo 24/7** en Railway y credenciales IAM guardadas ahí (superficie de secretos nueva). "ALTO" es correcto; el desglose no.

**D5. La tabla de §1 vende de más el push de inventario de Amazon.** Dice "(b) SÍ, a medias". El veredicto de refutación es más duro y §4 lo repite: es una **foto sin motivo**, no aporta señal nueva, solo cambia el temporizador. La tabla es lo que va a justificar el gasto en AWS; que diga lo mismo que el cuerpo.

**D6. Temu (PASO 3) no tiene criterio de aborto.** Todo el camino crítico es calendario ajeno (compliance, aprobación de URL hasta una semana, autorización del vendedor). El plan dice "arrancar en paralelo" pero no fija fecha de corte ni qué pasa si sigue bloqueado: ¿M2E se queda para siempre? ¿quién decide y cuándo?

---

### E. Contradicciones internas

**E1. Walmart está a la vez cerrado y abierto.** §1: esfuerzo "CERO (no se construye)", desbloquea "nadie". §3.4: describe la prueba pendiente (`POST /v3/webhooks/test`) y las condiciones para encenderlo, y §2 advierte del 200 engañoso. Si no se construye, esas dos secciones sobran; si queda un punto abierto, el esfuerzo no es cero. Decidir.

**E2. FBT de TikTok se lista como duda de Brandon, no como compuerta de diseño.** Si FBT MX existe y Brandon lo usa, aparece el caso "pedido protegido" en TikTok y cambia el normalizador de PASO 1. Debe ser precondición de escribir el normalizador, no una fila suelta de la tabla de §6.

**E3. La corrección de la firma de TikTok no se puede validar en el orden propuesto.** §6 la pone en "yo, sin pedir permiso", pero no hay ni un evento en el log contra el cual comprobarla — precisamente porque no hay suscripción (`total_count: 0`). Es circular: la corrección solo se puede verificar después de registrar, y registrar es el mismo paso. Falta el orden explícito: registrar → capturar un evento real → validar la firma contra ese cuerpo crudo → recién entonces considerar pasar de observar a rechazar.