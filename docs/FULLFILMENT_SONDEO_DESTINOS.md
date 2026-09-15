> **Sondeo de destinos de salida (Odoo → FULL / FBA / WFS)** — anexo de
> `FULLFILMENT_MATRIZ.md`, documento de trabajo no versionado (filas 5, 7, 8, E-1, E-2, E-8 y
> trampa C-1).
>
> Medido el **14-sep-2026** (noche CDMX), **solo lectura**: Odoo por XML-RPC
> (`search_read`/`read`/`read_group`/`fields_get`), kubera solo `SELECT` en el 5432,
> Mercado Libre solo GET con los tokens vigentes (sin refrescar), Amazon SP-API y
> Walmart MX token + GET. No se tocó ningún archivo del repo salvo este.
>
> Horas en **CDMX (UTC−6)**. Odoo entrega en UTC sin zona. "Picking" = el OUT
> (no se suman PICK/PACK). "Piezas hechas" = `stock.move.quantity` en `done`;
> "abiertas" = `product_qty` de movimientos no hechos ni cancelados.

# Sondeo de destinos: a qué canal y cuenta va cada salida de Odoo

## 0) Respuesta corta

- **ML FULL** = socio cuyo nombre empieza con `FULL` (219 contactos sueltos, uno por
  orden). **La cuenta la da quien creó la orden de venta: Thalia → SANCORFASHION (San
  Corpe), Cinthya → BEKURA (Kubera).** Probado contra la API de ML: 48 números de envío
  caen en la cuenta esperada y **ninguno en la contraria**; por piezas, otras 71 órdenes
  cuadran en ≥2 SKUs solo en la cuenta esperada (y 33 más con 1 SKU); los 3 casos en
  contra son envíos gemelos de la otra KAM. Quedan 29 sin veredicto (21 sin datos, 8
  empates) y 8 abiertas. Además, el socio exacto `MERCADO LIBRE` (Nancy, 2 órdenes de
  agosto) también es **FULL de BEKURA**, no ventas de meli_oerp.
- **Amazon FBA** (cuenta **San Corpe**, confirmado por `storeName`) = socio `AMAZON` con
  ≥40 piezas (12 OUT, Nancy). El mismo socio con 1–7 piezas son **ventas MFN** capturadas a
  mano (34 vivos): hoy se cuentan como "envío".
- **Walmart WFS** = **un solo** envío en toda la historia: socio `WFS 0029563GDM`
  (Cinthya, 3-sep, 304 pzs, 22 SKUs), idéntico renglón por renglón en la API de WFS. Los
  otros 15 socios `WALMART #…` son **ventas S2H**, no WFS.
- Todo lo demás es "otro": ventas ML DROP de **las dos cuentas** vía meli_oerp (5,248),
  Shein (480), TikTok (222), Temu (117), salidas a PAROLERA (5) y devoluciones a proveedor.
- La regla actual por subcadena (`backend/services/odoo.py:1336, 1394-1396`) marca 1,065
  salidas como envío a fulfillment; lo son **229** (§7-1).
- En Odoo **no existe** ubicación, tipo de operación, almacén, etiqueta ni campo que diga
  canal o cuenta; la cuenta hay que pedírsela a las KAM como dato estructurado (§3).

## 1) Qué NO distingue nada (descartado con medición)

Antes de la tabla, lo que se revisó y **no sirve** para separar canal ni cuenta:

| Campo / pieza de Odoo | Qué hay | Por qué no sirve |
|---|---|---|
| `stock.location` destino | 6,387 de 6,396 OUT van a `Customers` (5 a *Traslado entre almacenes*, 4 a *Vendors*) | No existe ninguna ubicación FULL, FBA, WFS, MELI, BEKURA ni SANCOR (búsqueda por nombre: 0) |
| `stock.picking.type` | 3 tipos OUT: `TEXCO: Órdenes de entrega` (1084), `TEXCO II` (1206), `DROP OFF` (1141) | Todos los canales comparten tipo; no hay tipo por marketplace |
| `stock.warehouse` | TEXCO, DROP OFF, TEXCO II (+ PAROLERA archivado) | El almacén dice de dónde sale, no a dónde va. **0 de 232 salidas a FULL salieron de DROP OFF** |
| `create_uid` del **picking** | `OdooBot` en 221 de 231 OUT a FULL | El picking lo genera Odoo al confirmar la orden de venta. **El humano está en `sale.order.create_uid`** |
| `res.partner` del socio | 219 socios distintos llamados "FULL" para 231 pickings; 54 "AMAZON" | La KAM crea un contacto nuevo por orden, sin dirección, padre, `ref`, etiqueta ni nota. **No se puede agrupar por `partner_id`**, solo por nombre |
| Campos de módulos de integración en `stock.picking` (`meli_order`, `meli_shipment`, `amazon_feed_ref`, `amazon_sync_status`, `ifull_*`) | vacíos en todos los OUT. `meli_shipment_logistic_type` solo tiene valor en ventas ML DROP (`cross_docking` 4,936 · `xd_drop_off` 270) y `otro` en todo lo demás; **nunca `fulfillment`** | Los módulos están instalados pero no se usan en salidas a fulfillment (`mercadolibre.account` y `amazon.account`: 0 registros visibles para el usuario de integración) |
| `sale.order` (`team_id`, `tag_ids`, `source_id`, `medium_id`, `channel_marketplace`, `x_studio_logistic_type`, `pricelist_id`) | todo igual o vacío (`Sales`, sin etiquetas, `Public Pricelist`) | Cero variación entre canales |
| `carrier_tracking_ref`, `note`, `x_studio_cliente` | vacíos en FULL, FBA y WFS | — |
| `mail.message` (quién validó) | **403** para el usuario de integración | El validador solo se aproxima con `write_uid` del OUT |

Lo único que discrimina: **el NOMBRE del socio**, el **`client_order_ref`** de la
orden de venta (copiado al picking en `x_studio_referencia_del_cliente` y
`x_studio_related_field_3j6_1isbutd20`), el **`origin`** y **quién creó la orden
de venta**.

## 2) Tabla maestra de destinos

Periodo con datos: 23-dic-2025 → 14-sep-2026 (6,396 OUT). Todas las filas salen por el tipo
`Órdenes de entrega` de su almacén y terminan en `Customers`; el "punto" se reconoce por
**socio + creador de la orden de venta + referencia/origin**. Fechas = creación de la orden
(CDMX). "OUT" cuenta pickings; entre paréntesis *vivos / hechos / abiertos / cancelados*.

| # | Punto (cómo reconocerlo) | Canal | Cuenta | OUT | Piezas hechas (+abiertas) | Primera → última creación | Evidencia | Confianza |
|---|---|---|---|---|---|---|---|---|
| 1 | Socio `^FULL` y orden creada por **Thalia** · 104 desde TEXCO, 11 desde TEXCO II | **ML FULL** | **SANCORFASHION (San Corpe)** | 123 (115 / 111 / 4 / 8) | 52,928 (+556) | 13-ene 12:49 → 9-sep 16:47 | 27 órdenes: la ref es un `inbound_id` recibido en SANCORFASHION, 0 en BEKURA; 34 más cuadran por piezas en ≥2 SKUs solo en SANCORFASHION; 16 a favor con 1 SKU. En contra: 2 (media), ambas explicadas por envíos gemelos de Cinthya (§3 y §7-11). Resto: 13 baja, 6 empate, 12 sin datos, 4 abiertas | **Alta** |
| 2 | Socio `^FULL` y orden creada por **Cinthya** · 81 TEXCO, 14 TEXCO II | **ML FULL** | **BEKURA (Kubera)** | 102 (95 / 91 / 4 / 7) | 63,655 (+2,965) | 14-ene 15:03 → 11-sep 11:37 | 21 órdenes con ref = `inbound_id` recibido en BEKURA (0 en SANCORFASHION); 37 cuadran por piezas en ≥2 SKUs; 17 a favor con 1 SKU. En contra: 1 con 1 SKU (gemelo de Thalia). Además sus refs dicen literal `FULL KUBERA #…` / `KUBERA ENVIO FULL #…` (6). Resto: 3 baja, 2 empate, 9 sin datos, 4 abiertas | **Alta** |
| 3 | Socio exacto `MERCADO LIBRE`, creó **Nancy Cruz**, TEXCO II | **ML FULL** | **BEKURA (Kubera)** | 2 (2 / 2 / 0 / 0) | 1,230 | 18-ago 18:24 → 18:29 | `S36019`: `ORG-1016-BLN` 100 pzs = inbound `74836894` recibido en BEKURA el 2-sep (op. `stock/fulfillment/operations`); `S36018`: 3 de 3 SKUs cuadran solo en BEKURA | **Alta** (n=2) |
| 4 | Socio `^FULL` creado por **Administrador** (`S22538`, ref `#61566618`) | ML FULL | BEKURA | 1 | 132 | 12-feb 14:59 | 3 de 3 SKUs cuadran solo en BEKURA; ref contigua a `61566619` de Cinthya (BEKURA) | Alta (n=1) |
| 5 | Socio `FULL 59126318` creado por **Liliana Blanco Reyes** (`S22977`) | ML FULL | BEKURA probable | 1 | 125 | 20-feb 14:27 | El número del socio es un inbound que BEKURA recibió el 13-ene (más de un mes antes de la orden): captura a destiempo; los SKUs no cuadran en la ventana | Media |
| 6 | Socio `^full` creado por **Vale** (`S23676` ref `#62835925`, `S24150`, `S24347`) | ML FULL | **No determinada** | 4 (3 / 3 / 0 / 1) | 180 | 5-mar 11:16 → 17-mar 10:55 | Ninguna recepción de sus SKUs en la ventana en ninguna cuenta; la ref no apareció | Canal: media · cuenta: sin evidencia |
| 7 | Socio `^AMAZON` con **≥40 piezas** · Nancy 10 (6 desde TEXCO II), Vale 2 | **Amazon FBA** | **San Corpe** (única cuenta: `marketplaceParticipations.storeName` = San Corpe, MX/US/CA) | 12 (12 / 10 / 2 / 0) | 4,296 (+335) | 1-jun 10:00 → 10-sep 13:36 | 8 de los 10 cerrados muestran subida de FBA por SKU (`fba_ingreso_sim`) tras validar; los 2 abiertos: piezas por SKU = `inboundShipped` vivo de la Inventory API (`S38241` 7 de 7 SKUs exactos; `S38342` 3 de 4). Los 2 de Vale (1-jun) son anteriores al vigilante | **Alta** (Nancy) · media (Vale) |
| 8 | Socio `WFS <shipmentId>` creado por **Cinthya** | **Walmart WFS** | Walmart MX (única cuenta; `core.accounts` la rotula "Kubera (Walmart MX)") | 1 (1 / 1 / 0 / 0) | 304 | 3-sep 12:27 | `shipmentId 0029563GDM`: 22 SKUs y 304 pzs idénticos en la API de WFS (§4) | **Alta** |
| 9 | Socio `^AMAZON` con **<40 piezas** (1–7) · Nancy 33, Thalia 1 | Otro: **venta Amazon MFN** | San Corpe | 46 (34 / 31 / 3 / 12) | 58 (+4) | 11-may 14:31 → 14-sep 11:13 | 23 de las 26 posteriores al 8-jul cruzan con `channel.orders` MFN (mismo SKU, compra 0–5 d antes). Antes del 8-jul kubera no tiene MFN para cruzar | Alta (desde 8-jul) · media (antes) |
| 10 | Socio `WALMART #<15 dígitos>` creado por **Cinthya** · 13 TEXCO, 2 TEXCO II | Otro: **venta Walmart S2H** | Walmart MX | 16 (15 / 13 / 2 / 1) | 19 (+2) | 15-ago 12:42 → 14-sep 11:02 | 15 de 15 números = `purchaseOrderId` (13) o `customerOrderId` (2) de `GET /v3/orders`, todos `isWFSEnabled=N` | **Alta** |
| 11 | `origin = "ML <order_id>"`, socio = comprador (uno por venta), creó OdooBot | Otro: **venta ML DROP** (meli_oerp) | **Ambas cuentas**, sin campo que lo diga | 5,248 (5,065 / 5,022 / 43 / 183) | 5,674 (+55) | 23-dic-2025 18:31 → 14-sep 21:02 | Muestra de 40 por `GET /orders/{id}`: 20 SANCORFASHION, 16 BEKURA, 4 (dic-2025) sin dueño. `meli_shipment_logistic_type`: cross_docking 4,936 · xd_drop_off 270 · **fulfillment 0** | Canal: alta · cuenta: solo por API |
| 12 | Socio `shein` (orden: Gabriela Ramirez 262, Vale 176, Thalia 41) · 18 desde DROP OFF | Otro: venta Shein DROP | — | 480 (474 / 431 / 43 / 6) | 1,219 (+58) | 9-mar 11:49 → 11-sep 10:25 | Nombre del socio | Alta |
| 13 | Socio TikTok, 5 grafías (orden: Gabriela Ramirez 220) | Otro: venta TikTok DROP | KUBERA (TikTok) | 222 (208 / 204 / 4 / 14) | 252 (+10,003: un OUT de 10,000, §7-17) | 13-ago 15:29 → 3-sep 22:07 | Nombre del socio; partner 1739238 = `_PARTNER["tiktok"]` (`backend/services/odoo_ventas.py:72`) | Alta |
| 14 | Socio `temu` (orden: Gabriela Ramirez 79; José Enrique 37 = automatismo, origin `Temu PO-…`) | Otro: venta Temu DROP | Kubera (Temu) | 117 (113 / 66 / 47 / 4) | 98 (+85) | 11-ago 13:04 → 14-sep 16:42 | Nombre del socio; partner 1738206 = `_PARTNER["temu"]` | Alta |
| 15 | Socio `PAROLERA` (orden: Moisés Romano) | Otro: salida a almacén PAROLERA (archivado) | — | 5 (5 / 4 / 1 / 0) | 21,023 (+300) | 14-abr 12:11 → 6-ago 11:26 | `client_order_ref` "Conteo Bodega PAROLERA …" | Alta |
| 16 | Socios `FERRAFORME MS`, `Proveedor de Ferraforme MS`, `DEVOLUCIONES`, u OUT sin socio con origin `P#####` / `Devolución de TEXCO/IN/…` | Otro: devolución a proveedor | — | 10 (8 / 5 / 3 / 2) | 12,920 (+5,313) | 31-dic-2025 → 8-jul | origin | Alta |
| 17 | Socio persona con origin `S#####` (Thalia 2, Vale 1, Denisse Jaimes 1) | Otro: venta directa capturada | — | 4 | 1,083 | 2-mar → 7-sep | Una lleva ref "Venta …" | Media |
| 18 | Socio `<persona> / Mercado Libre` (Cinthya) | Otro: envío a un comprador de ML | — | 1 | 1 | 1-abr 10:01 | Nombre del socio | Media |

Recepciones (no son destinos, pero se cruzan con esto): **1,103** recepciones al socio
`DEVOLUCIONES` en `TEXCO II: Recepciones` (feb → sep, *Auxiliar Inventarios* 1,089), 62 con
`ifull_return_withdrawal_folio` en formatos mezclados (§6). No hay forma de separar retiros
de FULL de devoluciones de comprador con lo que está en Odoo.

## 3) Kubera vs San Corpe en una salida a FULL

**En Odoo no hay ningún campo que diga la cuenta.** Lo que la delata es **quién creó la
orden de venta**: cada KAM atiende una cuenta de ML y no se cruzan.

| Creador de la `sale.order` | Cuenta ML | Órdenes FULL cerradas evaluadas | A favor (alta / media) | En contra | No concluyente |
|---|---|---|---|---|---|
| **Thalia** | **SANCORFASHION (San Corpe)** | 110 | **61 / 16** (27 por número de envío exacto) | 2 media, ambas gemelas de Cinthya | 13 baja · 6 empate · 12 sin datos |
| **Cinthya** | **BEKURA (Kubera)** | 89 | **57 / 17** (20 por número de envío exacto) | 1 media, gemela de Thalia | 3 baja · 2 empate · 9 sin datos |
| Nancy Cruz (socio `MERCADO LIBRE`) | BEKURA | 2 | 1 / 1 | 0 | — |
| Administrador · Liliana · Vale | BEKURA · BEKURA prob. · ? | 1 · 1 · 3 | 1 · — · — | 0 | Liliana 1 · Vale 3 |

Cómo se midió (solo GET, `s12c_ml_amarre_full_v3.py` + `a04b_veredicto_full_v3.py`):
1. Por cada orden FULL de Odoo con más de 1 pieza (214), sus 3 SKUs de más piezas.
2. En **cada** cuenta: `GET /users/{id}/items/search?seller_sku=` → `GET /items` →
   `inventory_id` (del item o de la variación).
3. `GET /stock/fulfillment/operations/search?seller_id&inventory_id&date_from&date_to` en
   [validación − 7 d, validación + 30 d].
4. **Número de envío exacto**: la ref de Odoo (8 dígitos) es el `inbound_id` de una
   `INBOUND_RECEPTION` de esa cuenta. Pasó en 48 órdenes: 27 de Thalia, todas en
   SANCORFASHION; 21 de Cinthya, todas en BEKURA; **cero cruzadas**.
5. **Piezas**: el lote que llegó por SKU (suma por `inbound_id`, o el tamaño de la racha de
   `TRANSFER_DELIVERY`) iguala a Odoo (±2 pzs o ±10%). "Alta" = ≥2 SKUs cuadran en una
   cuenta y 0 en la otra.
6. Los 3 "en contra" se revisaron a mano: en los tres la otra KAM mandó **el mismo SKU con
   la misma cantidad** a su cuenta dentro de la ventana — `S30621` (Thalia) ↔ `S30636`
   (Cinthya), el mismo día, `TEC-2164-NEG` 100 y `TEC-2195-MUL-RGB` 150/148;
   `S24105` (Thalia) ↔ `S23440` (Cinthya, 10 días antes), `ORG-0263-MET` 30;
   `S21951` (Cinthya) ↔ `S22529` (Thalia, 9 días después), `TEC-0013-MUL` 30. El lote que
   "cuadra" en la cuenta contraria es el del gemelo. No hay ni un caso con número de envío
   en contra.

Casos verificados a mano además del barrido: `S36192` (Cinthya) `CAM-0030-MAT` 80 +
`CAM-0030-QUE` 80 = inbound `74957033` BEKURA 79 + 81; `S36993` (Cinthya) `EST-0054-NEG` 23 =
inbound `75649764` BEKURA 23; `S35635` (Thalia) `OFI-0077-MAD` 2 = inbound `74370353`
SANCORFASHION 2; `S36102` (Thalia) `BEB-0126-BLN` 30 = lote de 30 en SANCORFASHION;
`S21132` (Thalia) ref `59129488` = inbound recibido en SANCORFASHION el 14-ene.

**Regla operativa para el backend (hoy):**

```
socio ~ ^\s*FULL\b                      → ML FULL
  sale.order.create_uid == Thalia       → SANCORFASHION   (alta)
  sale.order.create_uid == Cinthya      → BEKURA          (alta)
  otro creador                          → cuenta = null ("sin asignar"), no adivinar
socio == "MERCADO LIBRE" (exacto)       → ML FULL, BEKURA (alta, n=2; revisar si Nancy repite)
```

Es una regla **por persona**, y eso la hace frágil: se rompe el día que una KAM cubra a la
otra (Vale, Administrador y Liliana ya capturaron 6 salidas en feb–mar) o que Nancy mande
más a ML. Sirve para la historia; para lo que viene hay que capturarlo:

**Qué pedirle a las KAM (y por qué):**
1. **El número de envío de ML en `client_order_ref`, siempre, solo dígitos.** Es la prueba
   fuerte (48 de 48 cuadran) y dejó de capturarse: en septiembre 0 de 5 órdenes de Thalia y
   0 de 3 de Cinthya lo traen.
2. **La cuenta en el socio, en vez de un contacto nuevo "FULL" por orden:** dos socios
   fijos, p. ej. `FULL BEKURA` y `FULL SANCORFASHION`, o una etiqueta. Hoy hay 219 contactos
   "FULL" sueltos. Con un socio fijo por cuenta el filtro deja de depender de quién captura.
3. En Amazon, el `ShipmentId` (`FBA15…`) en `client_order_ref`; y separar las ventas MFN
   (hoy mismo socio `AMAZON`) con otro socio.
4. En WFS, seguir con `WFS <shipmentId>` (funciona) y sumar el `inboundOrderId`.

## 4) Walmart WFS: qué hay de verdad

**Hay exactamente UN envío a WFS en toda la historia, y cuadra a la pieza.**

| | Odoo | Walmart MX (`/v3/fulfillment/inbound-shipments?inboundOrderId=…` y `/inbound-shipment-items?shipmentId=…`) |
|---|---|---|
| Documento | `S37676` → `TEXCO/OUT/06190`, socio **`WFS 0029563GDM`** | `shipmentId` **`0029563GDM`**, `inboundOrderId` `KUB-1788454721948` |
| Quién / cuándo | creó **Cinthya** jue 3-sep 12:27; validó `Calidad TEX 2` mié 9-sep 08:12 | `createdDate` jue 3-sep **12:12** (15 min antes que Odoo) |
| Piezas | 22 SKUs, **304** | 22 SKUs, `shipmentUnits` **304**, mismo SKU y cantidad renglón por renglón |
| Destino | — | *Megapark Fulfillment Center*, Tepotzotlán; transporte propio (`OC_LTL`) |
| Llegada | — | `actualDeliveryDate` vie 11-sep 12:31; estado `RECEIVING_IN_PROGRESS`, **289 recibidas**: `ROP-0257-NEG` 15 enviadas / 0 recibidas, 0 dañadas |

- `GET /v3/fulfillment/inbound-shipment-items` **sin filtro** devuelve 22 renglones, todos
  de ese envío: no hay otro inbound en la cuenta.
- `GET /v3/fulfillment/inbound-shipments` sin `inboundOrderId` (con `limit`/`offset` o con
  `shipmentId`) responde **520 Gateway Timeout**; con `inboundOrderId` responde 200. Los
  404 de la matriz (C-9) eran de la cuenta vacía: **la API de WFS en MX sí funciona**.
- `GET /v3/fulfillment/inventory` → 401 *Program Eligibility is not enabled*;
  `inventory-log` exige `gtin`. El stock WFS por SKU sigue sin fuente medida.
- **Los otros 15 socios "WALMART …" NO son WFS.** Son ventas S2H que Cinthya captura a
  mano: los 15 números del nombre del socio cruzan 15 de 15 con `GET /v3/orders`
  (desde el 1-ago), 13 por `purchaseOrderId` (`6091…`) y 2 por `customerOrderId`
  (`6000001…`); todas con `isWFSEnabled = N`, 1–5 piezas, orden→validación mediana 0.9 d.
  El `_causa` actual las cuenta como `envio_full` (`backend/services/odoo.py:1336, 1394-1396`)
  y al único WFS real **no** lo atrapa (el nombre dice `WFS`, no `WALMART`).
- Regla operativa: WFS = socio que empieza con `WFS` + un `shipmentId` `\d{7}GDM`. El
  `inboundOrderId` (`KUB-…`) no aparece en Odoo; se consigue por la API a partir de la
  fecha o se le pide a Cin que lo capture.

## 5) Días, horas y tiempos por canal

"Creación" = `sale.order.create_date` (lo que teclea la KAM). "Validación" = `date_done`
del OUT (último paso de bodega). Solo pickings no cancelados; tiempos sobre los `done`.

| Punto | Creación: días (conteo) | Creación: horas pico | Validación: días | Validación: horas pico | Orden → validación: mediana / p90 | n |
|---|---|---|---|---|---|---|
| ML FULL · San Corpe (Thalia) | jue 32 · mar 28 · mié 22 · lun 17 · vie 12 · sáb 4 | 10–14 h | **lun 29 · vie 27** · mar 21 · jue 16 · mié 13 · sáb 5 | 09–14 h | **8.1 d / 14.8 d** | 111 |
| ML FULL · Kubera (Cinthya + 2 de Nancy) | jue 31 · mar 22 · mié 17 · vie 11 · lun 9 · sáb 7 | 11–13 h y **17–19 h y 00 h** (8 órdenes creadas a medianoche) | **vie 25** · mar 18 · jue 17 · lun 16 · mié 10 · sáb 7 | 12 h (26) y 14 h (18) | **9.7 d / 17.8 d** | 93 |
| Amazon FBA · San Corpe | vie 3 · mié 3 · lun 2 · mar 2 · jue 2 | 10–12 h | lun 3 · jue 3 · mié 2 · mar 1 · sáb 1 | 09–13 h | **12.4 d / 20.0 d** | 10 |
| Walmart WFS | jue (1) | 12 h | mié (1) | 08 h | 5.8 d | 1 |
| *Contraste:* ML DROP (meli_oerp) | todos los días, pico 17–22 h | — | jue · mié · mar | 15–16 h | 1.8 d / 6.0 d | 5,022 |
| *Contraste:* Shein / TikTok / Temu | lun (Shein) · mar (TikTok) · jue (Temu) | — | — | — | 1.1 / 1.0 / 3.7 d (p90 5.4 / 3.1 / 16.2) | 431 / 204 / 66 |
| *Contraste:* Amazon MFN / Walmart S2H | — | 09 h (Nancy) | — | — | 1.0 d / 0.9 d | 31 / 13 |

Lecturas:
- Las salidas a fulfillment se **crean** sobre todo **martes y jueves** y se **validan**
  sobre todo **lunes y viernes**: 8–10 días de mediana entre que la KAM crea la orden y
  bodega cierra el OUT, contra 1–2 días de una venta DROP.
- **La validación de Odoo NO es la salida física.** En las 43 órdenes FULL cerradas cuyo
  número cruza exacto con una `INBOUND_RECEPTION`, la primera pieza que ML recibe llega
  **mediana +0.4 d** después de que bodega valida el OUT (p10 −3.0 d, p90 +1.3 d, rango
  −6.5 a +5.0), y en **16 de 43 (37%) ML recibió ANTES** de la validación. De la creación
  de la orden a la primera recepción en ML: mediana **8.2 d**, p90 10.9 d. O sea: la
  validación en Odoo se hace "cuando ya llegó" más que "cuando salió".
- No hay domingos en FULL/FBA/WFS; los sábados son marginales.
- Validador aproximado (`write_uid` del OUT, `mail.message` da 403): ene *Administrador* /
  *Liliana Blanco Reyes* → feb *Calidad TEX 2* → mar–may *Héctor Jaimes Velasco* → may–jul
  *Alma Daniela Leyva Gutiérrez* → ago–sep *Calidad TEX 2* / *Calidad TEX 1*. Walmart S2H
  lo cierra *Calidad TEX 1* (13 de 13).

## 6) Números de envío: formatos encontrados y normalización

| Canal | Dónde vive en Odoo | Formatos vistos (conteo) | Qué es | Normalización propuesta |
|---|---|---|---|---|
| ML FULL | `sale.order.client_order_ref` (copiado al picking en `x_studio_referencia_del_cliente`); una vez en el nombre del socio (`FULL 59126318`) | `75652884`, 8 dígitos pelones (103: Thalia siempre, Cinthya en ene–feb). Solo Cinthya: `Envío #70688003` (48), `#61770039` (9), `ENVIO #62934496` (2), `FULL 61915800`, `FULL KUBERA #62377191` (2), `KUBERA FULL #62669141`, `KUBERA ENVIO FULL #63685881` (3). **Vacío en 62 de 231 OUT** | El `inbound_id` de ML: en **48 órdenes** el número es el `external_references.inbound_id` de una `INBOUND_RECEPTION` de la cuenta esperada, y **nunca** de la contraria (§3). Otras 108 refs con número no aparecieron en los 3 SKUs revisados por orden | `inbound_id = re.search(r'(?<!\d)(\d{8})(?!\d)', ref)`; guardar el texto crudo aparte; refs observadas de 59,126,318 a 75,652,884 |
| ML FULL (lado ML) | — | `inbound_id` de 8 dígitos en `INBOUND_RECEPTION` (las 455 operaciones cuyo `inbound_id` es igual a una ref de Odoo son todas de este tipo) y **muchos `inbound_id` distintos por SKU** en `TRANSFER_DELIVERY` (p. ej. 11 ids para las 30 piezas de `BEB-0126-BLN`) | Los de `TRANSFER_DELIVERY` son sub-lotes, **nunca** coincidieron con un "Envío #" de Odoo | No usar los de `TRANSFER_DELIVERY` para amarrar al "Envío #": amarrar por SKU + piezas + fecha |
| Amazon FBA | **ningún campo** (`client_order_ref`, `note`, `amazon_feed_ref` vacíos en las 67 órdenes AMAZON) | — | El `ShipmentId` (`FBA15…`) no se captura | Pedir a Nancy el `ShipmentId` en `client_order_ref`; regex `FBA[0-9A-Z]{8,10}` |
| Walmart WFS | nombre del socio | `WFS 0029563GDM` (1) | `shipmentId` WFS | `r'(\d{7}GDM)'`; el `inboundOrderId` `KUB-\d+` no está en Odoo |
| Walmart S2H (venta) | nombre del socio | `WALMART 609123772217505` (4), `WALMART #609124072844847` (10), `WALMART  #…` (doble espacio, 1) | `purchaseOrderId` (13) o `customerOrderId` (2) | `r'(\d{15})'` y probar contra ambos campos |
| ML DROP (meli_oerp) | `origin` / `client_order_ref` | `ML 2000017649101598` (5,225); guía `MEL…FMXDF01` (4,911) / `MEL…FMDOF01` (265) | `order_id` de ML (verificado por API) y guía | `r'ML (\d{16})'` |
| Temu | `client_order_ref` / `origin` | `PO-576-…` (37, solo las del automatismo); guía de 14 dígitos o `JMX…` | `parentOrderSn` | — |
| Retiros / devoluciones | `ifull_return_withdrawal_folio` en recepciones `DEVOLUCIONES` | 62 con folio: `2000014836613037`, `#2000014742952129`, `841952707-4`, `JMX…`, listas con `/` | Mezcla de orden ML, guía y folio | No normalizable sin captura estructurada |

## 7) Sorpresas y trampas nuevas (no están en la matriz)

1. **`envio_full` es 78% ruido, y aun así se le escapan dos cosas.** De los OUT no
   cancelados, la subcadena de `backend/services/odoo.py:1336` marca **1,065** como
   envío; fulfillment real son **229** (215 FULL + 2 "MERCADO LIBRE" + 12 AMAZON grandes).
   Los otros 836: Shein 474, TikTok 199, Temu 113, ventas Amazon MFN 34, ventas Walmart
   S2H 15 y 1 reposición a un comprador cuyo socio termina en "/ Mercado Libre". Y NO
   atrapa: el único envío WFS (socio `WFS …`) ni 9 salidas de TikTok con grafías sin
   `TIKTOK` literal.
2. **Los socios se teclean a mano, con variantes.** `FULL`/`full`/`FULL 59126318`;
   `AMAZON`/`Amazon`; TikTok con cinco grafías (`tiktokshop`, `tik tokshop`,
   `tiktpkshop`, `tik tok shop`, `tiktokahop`); `WALMART  #…` con doble espacio.
   Clasificar con regex normalizada (mayúsculas, espacios colapsados), nunca por igualdad.
3. **El humano no está en el picking.** `stock.picking.create_uid` = OdooBot (221 de 231
   OUT a FULL). Quién pidió la salida está en `sale.order.create_uid` / `user_id`.
   Quién validó solo se aproxima con `write_uid` (`mail.message` → 403).
4. **Un socio nuevo por orden** (219 "FULL" distintos): cualquier consulta por
   `partner_id` fijo pierde el 99%. Hay que filtrar por `partner_id.name`.
5. **"AMAZON" mezcla dos cosas.** 12 OUT de 47–1,664 piezas son envíos a FBA; 34 de 1–7
   piezas son **ventas MFN** que Nancy captura a mano (23 de las 26 posteriores al 8-jul
   cruzan con `channel.orders` MFN por SKU y fecha; antes del 8-jul kubera no tiene
   ventas MFN para cruzar). Umbral que separa hoy sin traslape: ≥40 pzs.
6. **"MERCADO LIBRE" no es meli_oerp: es un envío FULL a Kubera.** Las 2 órdenes de Nancy
   (18-ago, `TEX2/OUT/00030-31`, 1,230 pzs) llegaron a BEKURA (`ORG-1016-BLN` 100 pzs =
   inbound `74836894`; `S36018` cuadra en 3 de 3 SKUs). meli_oerp crea **un socio por
   comprador** (5,226) y trae ventas **de las dos cuentas** (muestra de 40 por API: 20
   SANCORFASHION, 16 BEKURA, 4 de dic-2025 que ninguna reconoce); el picking no dice de
   cuál: solo se sabe preguntando la orden a ML.
7. **La llegada a FULL casi nunca es `INBOUND_RECEPTION`.** La mayor parte de la mercancía
   de un envío aparece en el buscador como **`TRANSFER_DELIVERY` con
   `external_references.inbound_id`** (piezas que pasan del bucket `transfer` a
   disponibles), repartida en muchos `inbound_id` chicos. En las operaciones que bajó el
   amarre (deduplicadas, ene–sep): **431** `INBOUND_RECEPTION`, **17,496**
   `TRANSFER_DELIVERY` y solo **1,075** `TRANSFER_RESERVATION`; no es un cambio reciente,
   pasa desde enero. En `ops.fanout_log`: **38** `INBOUND_RECEPTION` (24-ago → 7-sep)
   contra **5,214** `TRANSFER_DELIVERY` (27-jul → 15-sep), y
   `backend/services/stock_full.py:77-83` solo cuenta el primero como ingreso. Contar
   "recibidas" con `INBOUND_RECEPTION` (matriz, filas 7, 8 y 10) daría casi cero. Ojo:
   `TRANSFER_DELIVERY` también se usa en barajeos internos (precedido de un
   `TRANSFER_RESERVATION` negativo en el mismo inventario), y **la operación que sube
   `result.total` al llegar no aparece en el buscador**: lo visible es la liberación. El
   tamaño del lote se lee en la primera entrega de la racha: `detail.available_quantity`
   + lo que queda en `transfer` (30 = 2 + 28 en `BEB-0126-BLN`, igual a Odoo).
8. **ML recibe antes de que Odoo valide.** 16 de 43 envíos con número verificado (37%)
   ya tenían piezas recibidas en ML cuando bodega validó el OUT, hasta 6.5 días antes
   (`S27506`: ML recibe el 9-may, Odoo valida el 15-may). Y la reserva de `S25795` (abajo)
   corresponde a un inbound (`65159541`) que BEKURA empezó a recibir el 15-abr. `date_done` no sirve como "fecha en que
   se fue al marketplace" (matriz fila 4) ni para ordenar la secuencia salida→recepción.
9. **Reservas zombi de FULL.** `S25795` (9-abr, 498 pzs reservadas) y `S26441` (20-abr,
   548 pzs), ambas de Cinthya, llevan **147–158 días** con el PICK en `assigned`: 1,046
   piezas fuera de `free_qty` y, por `STOCK_WATCH_ABSOLUTO`, fuera de Woo y de los canales
   DROP. También `S34541` (AMAZON MFN, 1 pza, desde 30-jul).
10. **La referencia se dejó de capturar.** Thalia: 100% con número ene–mar → 1 de 6 en
    agosto y 0 de 5 en septiembre. Cinthya: `Envío #…` hasta junio → 9 de 11 vacías en
    julio, 5 de 5 en agosto, 3 de 3 en septiembre. Sin número, el amarre solo sale por
    SKU + piezas + fecha.
11. **Envíos gemelos.** Thalia y Cinthya mandan el mismo SKU con la misma cantidad, a veces
    el mismo día, a cuentas distintas (`S30621`/`S30636`, 11-jun: `TEC-2164-NEG` 100 y
    `TEC-2195-MUL-RGB` 150; `S36990`/`S37012`, 27-ago: `MUE-0160-GRI` 80 y
    `MUE-0160-GRI-NEG` 100). Un amarre por SKU + piezas sin la cuenta **se equivoca de
    cuenta**; los tres "en contra" del §3 son esto.
12. **Captura a destiempo.** `S22977` (Liliana, socio `FULL 59126318`, 20-feb, 125 pzs)
    lleva el número de un inbound que BEKURA recibió el 13-ene: órdenes que se dan de alta
    semanas después de que la mercancía llegó.
13. **Amazon no deja leer envíos inbound con la app actual.** `getShipments` (v0) y
    `listInboundPlans` (2024-03-20) → **403**; listar reportes → 403. Solo sirven
    Inventory Summaries (`inboundShipped/Receiving` en vivo) y Orders. Y el mismo desfase
    que en ML: `S38241` sigue `waiting` en Odoo mientras Amazon ya declara sus 7 SKUs como
    `inboundShipped` con **exactamente** las mismas piezas (22, 48, 40, 5, 46, 10, 40).
14. **El buscador de operaciones FULL** exige `seller_id` + `inventory_id` (400 sin ellos),
    da **429 `over_quota`** a ratos (se recupera en minutos), guarda historia al menos
    desde ene-2026, y el `inventory_id` a veces vive en la **variación** y cambia al
    relistar: una publicación nueva no ve las operaciones de la vieja.
15. **Los vigilantes FULL y FBA SÍ están encendidos en producción, en solo-registro**
    (matriz E-10): `ops.fanout_log` tiene `full_*` hasta el 15-sep 04:07 UTC y 140
    `fba_ingreso_sim` del 12-ago al 15-sep.
16. **Salidas que no son canal y pesan mucho.** 5 OUT al socio `PAROLERA` (almacén
    archivado, 21,023 pzs, "Conteo Bodega PAROLERA") y 10 devoluciones a proveedor (12,920
    pzs) viven en el mismo tipo `Órdenes de entrega` y terminan en `Customers`: sumadas
    como "ventas" o "envíos" distorsionan cualquier total.
17. **Un OUT de TikTok con 10,000 piezas** (`S36857`, `TEC-2280-NEG`, en espera desde el
    26-ago): error de captura que infla las "abiertas" de TikTok (10,003).

## 8) Lo que no se pudo determinar y qué dato faltaría

| Qué | Por qué no salió | Dato que faltaría |
|---|---|---|
| Cuenta de 4 salidas FULL de Vale (mar) y la de Liliana (feb, solo "probable") | Sus SKUs no muestran llegadas en la ventana en ninguna cuenta (publicaciones relistadas: el `inventory_id` viejo ya no aparece) | Preguntarle a Vale, o el "Envío #" en ML Seller Center |
| Cuenta de 21 órdenes FULL "sin datos" y 8 "empate" de Thalia y Cinthya (con Vale y Liliana: 33; marzo 13, abril 5, mayo 4, junio 4, resto ≤3 por mes) | Mismo motivo, y gemelos | Igual; el barrido solo miró 3 SKUs por orden: ampliar a todos los SKUs resolvería parte |
| Cuenta de las 8 órdenes FULL abiertas (sep y las dos zombi de abril) | Aún no salen (o se recibieron sin validar) | Se resuelve sola por la regla del creador; confirmar al validarse |
| Números de envío de FBA y recibidas/rechazadas por envío en Amazon | La app de SP-API no tiene el rol de Inbound (403 en v0 y 2024-03-20; 403 al listar reportes) | Agregar el rol *Amazon Fulfillment* a la app en Seller Central, o que Nancy capture el `ShipmentId` |
| Si las ventas MFN de Amazon anteriores al 8-jul eran ventas | kubera no tiene MFN antes del 8-jul | `GET /orders/v0/orders?FulfillmentChannels=MFN` desde mayo (sí responde 200) |
| De qué cuenta es cada venta ML DROP en Odoo | El picking no guarda la cuenta; solo se sabe preguntando la orden a ML (muestra 40: 20 / 16 / 4 sin dueño) | Un campo de cuenta en la integración meli_oerp, o cruzar `origin` con `channel.orders` |
| Las 4 ventas ML de dic-2025 que ninguna cuenta reconoce | `GET /orders/{id}` 404 en BEKURA y SANCORFASHION | ¿Hubo una tercera cuenta conectada a meli_oerp? Preguntar a quien configuró la integración |
| Fecha real de salida del camión (a FULL, FBA o WFS) | Odoo solo tiene la validación, que ocurre ± días de la recepción | Captura en el panel (lista de Andy / Bodega) o la fecha de cita del inbound |
| La operación que sube `total` cuando llega mercancía a FULL | No aparece en `operations/search` (solo la liberación `TRANSFER_DELIVERY`) | Probar con un envío en curso qué tipo trae el webhook `fbm_stock_operations` en el instante de llegada |
| Stock WFS por SKU | `/v3/fulfillment/inventory` → 401 *Program Eligibility is not enabled*; `inventory-log` pide `gtin` | Probar `inventory-log` con el GTIN que devolvió el envío (`00451050058310`…) |
| Quién validó cada OUT | `mail.message` 403 para el usuario de integración | Permiso de lectura de mensajes, o usar `write_uid` (aproximado) |
| Si "Thalía", "Nancy" y "Cin" del equipo son los usuarios `Thalia` (152), `Nancy Cruz` y `Cinthya` (153) de Odoo, y dónde entra Andy | Solo se ven nombres de usuario. Ningún "Andy" crea ni valida salidas de canal (en Odoo existe una usuaria *Andrea Pardo*, sin actividad en estas salidas) | Confirmación de Brandon (Cin ↔ Cinthya ↔ "WFS (Cin)" es consistente: ella creó el único WFS) |

## 9) Scripts del sondeo

Fuera del repo, en el scratchpad de la sesión
(`…\scratchpad\sondeo_fulfillment\`); los datos crudos quedaron en `out/`. Todos son de
solo lectura: `_seguro.py` rechaza cualquier método de Odoo fuera de
`search/search_read/read/search_count/read_group/fields_get`, `_pg.py` rechaza todo lo que
no sea `SELECT` (puerto 5432, sin marcar la sesión), `_ml.py` solo lee tokens y hace GET.

| Script | Qué hace |
|---|---|
| `_seguro.py`, `_pg.py`, `_ml.py` | Candados de solo lectura para Odoo, kubera y ML |
| `s01_topologia.py` | Tipos de operación, almacenes, ubicaciones, campos custom |
| `s02_conteos.py` · `s03_pickings.py` · `s19_moves_out.py` | Conteos y descarga de los 6,396 OUT / 1,349 IN, socios y 11,614 movimientos |
| `s04_ordenes_canal.py` · `s05_detalle_canal.py` | Órdenes de venta, pickings y movimientos de los socios de canal |
| `a01_socios.py` · `a02_ordenes.py` · `a03_detalle.py` | Familias de socio, qué campo varía, tabla por orden |
| `a05_extras.py` · `a07_trampas_subcadena.py` · `a08_full_mensual_refs.py` | Campos ifull/amazon, lo que atrapa `_causa`, refs por mes |
| `a06_clasificar_y_tiempos.py` | Clasificación de cada OUT, piezas, días/horas y mediana/p90 |
| `s06_kubera.py` · `s25_amazon_mfn_rango.py` | `ops.fanout_log`, `fulfillment_operations`, `webhook_events`, `core.accounts`, rango de `channel.orders` |
| `s07` · `s08` · `s09` · `s10` · `s11` | Pruebas de la API FULL de ML (operación por id, rutas de inbound, buscador, profundidad) |
| `s12_ml_amarre_full.py` · `s12b_…_v2.py` | Primeras dos versiones del amarre (detenidas: solo `INBOUND_RECEPTION`) |
| `s12c_ml_amarre_full_v3.py` · `a04b_veredicto_full_v3.py` · `a10_resumen_final_full.py` | **Amarre definitivo** orden FULL → cuenta, veredicto, desfase recepción − validación |
| `a09_tipos_llegada_por_mes.py` | `INBOUND_RECEPTION` vs `TRANSFER_DELIVERY` por mes y cuenta |
| `s26_usuarios.py` | Usuarios de Odoo (Andy / Cin / Thalia) y meses de los no concluyentes |
| `s22_ml_tipos_por_envio.py` · `s23_ml_transfer_delivery.py` | Descubrimiento de `TRANSFER_DELIVERY` como llegada |
| `s13` · `s14` · `s18` · `s21` (Amazon) | Inbound (403), alternativas, partición FBA/MFN, cuenta San Corpe |
| `s15_walmart.py` · `s16_wfs_detalle.py` | Pedidos S2H y el envío WFS renglón por renglón |
| `s17_meli_oerp_cuentas.py` | De qué cuenta son las ventas ML que entran solas a Odoo |
| `s20_revisar_raros.py` · `s24_reservas_zombi.py` | OUT de 10,000 pzs y reservas de órdenes abiertas |
