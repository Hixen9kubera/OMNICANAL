> **Evidencia por orden** del sondeo de destinos — anexo de
> [`FULLFILMENT_SONDEO_DESTINOS.md`](FULLFILMENT_SONDEO_DESTINOS.md). Generado el
> **15-sep-2026** con los datos crudos del sondeo (14-sep, solo lectura) y una
> verificación independiente en vivo de 4 órdenes el 15-sep.

# De dónde sale cada asignación de canal y cuenta

## 1) Fuentes: qué se leyó y de dónde

Todo fue **solo lectura**: Odoo con `search_read`/`read` (un candado rechaza cualquier otro
método antes de tocar la red), Mercado Libre, Amazon y Walmart solo `GET`, kubera solo
`SELECT`.

| Sistema | Modelo / endpoint | Campos | Para qué |
|---|---|---|---|
| Odoo | `sale.order` | `name` (S#####), **`create_uid`** (quién la creó), `user_id`, **`client_order_ref`** (la referencia que teclea la KAM), `partner_id` (socio), `create_date` | La orden de venta: quién la pidió, a qué socio y con qué número de envío |
| Odoo | `stock.picking` con `picking_type_id.code = outgoing` y `origin = S#####` | `name` (TEXCO/OUT/…), `state`, `date_done`, `create_uid`, `write_uid` | La salida registrada (el OUT; PICK y PACK no se suman) |
| Odoo | `stock.move` en `done` de esos OUT | SKU (`product_id.default_code`), `quantity` | Qué SKUs y cuántas piezas salieron |
| Mercado Libre, con el token de **cada** cuenta | `GET /users/{seller_id}/items/search?seller_sku=` → `GET /items?ids=` | `inventory_id` (del ítem o de su variación) | El inventario FULL de ese SKU **en esa cuenta** (BEKURA `3072519654`, SANCORFASHION `3064478475`) |
| Mercado Libre | `GET /stock/fulfillment/operations/search?seller_id&inventory_id&date_from&date_to` | `type`, `date_created`, `detail.available_quantity`, **`external_references.inbound_id`** | Qué llegó al almacén FULL de esa cuenta, cuándo, cuántas piezas y con qué número de envío |
| Amazon SP-API | `sellers/v1/marketplaceParticipations`, `fba/inventory/v1/summaries`, `orders/v0/orders` | `storeName`, `inboundShipped`, `FulfillmentChannel` | La cuenta (San Corpe), las piezas en camino a FBA y las ventas MFN |
| Walmart MX | `GET /v3/fulfillment/inbound-shipments?inboundOrderId=`, `GET /v3/fulfillment/inbound-shipment-items`, `GET /v3/orders` | `shipmentId`, SKU, piezas, recibidas; `purchaseOrderId` | El envío WFS y las ventas S2H |
| kubera | `ops.fanout_log` (`accion LIKE 'fba_%'`), `channel.orders` | fecha, SKU, cantidad | Subidas de FBA tras validar; ventas MFN para separarlas de los envíos |

## 2) Las reglas

### 2a · Canal: por el NOMBRE del socio (mayúsculas, espacios colapsados)

| Si el socio / origin… | Canal |
|---|---|
| empieza con `FULL` | ML FULL |
| es exactamente `MERCADO LIBRE` | ML FULL (2 órdenes de Nancy, verificadas en BEKURA) |
| empieza con `AMAZON` y la salida tiene **≥ 40 piezas** | Amazon FBA |
| empieza con `AMAZON` y tiene 1–7 piezas | **venta** Amazon MFN (no es envío) |
| empieza con `WFS` + un número `\d{7}GDM` | Walmart WFS |
| `WALMART #` + 15 dígitos | **venta** Walmart S2H (no es envío) |
| `origin = "ML "` + 16 dígitos | venta ML DROP que mete meli_oerp |
| shein / tiktok (5 grafías) / temu / PAROLERA / devoluciones | otros |

### 2b · Cuenta de una orden ML FULL: dos pruebas contra la API de Mercado Libre

1. **Por número de envío (la fuerte).** Se sacan los 8 dígitos de `client_order_ref`
   (`75652884`, `Envío #70688003`, `FULL KUBERA #62377191` → el número). Si ese número
   aparece como `external_references.inbound_id` en las operaciones FULL de **una** cuenta,
   la orden es de esa cuenta.
2. **Por piezas.** Para los 3 SKUs con más piezas de la orden se buscan, en **cada** cuenta,
   los lotes que llegaron a FULL entre la validación − 7 días y la validación + 30 días
   (`INBOUND_RECEPTION` sumado por número de envío, o el tamaño de la racha de
   `TRANSFER_DELIVERY`). Un SKU «cuadra» si un lote iguala las piezas de Odoo con ±2 piezas
   o ±10%.

**Confianza** (puntaje = SKUs que cuadran, +3 si pasa la prueba por número):
- **alta (REF)**: el número de envío está en esa cuenta.
- **alta (piezas)**: ≥ 2 SKUs cuadran en esa cuenta y 0 en la otra.
- **media**: 1 SKU cuadra en esa cuenta y 0 en la otra, o ventaja de ≥ 2.
- **baja**: ventaja de 1 con coincidencias en las dos cuentas.
- **empate / sin datos**: no se asigna cuenta.

### 2c · La regla que sale de cruzar eso con quién creó la orden

| Creó la orden | Resultó Kubera | Resultó San Corpe | Empate / sin datos |
|---|---|---|---|
| **Thalia** (114 órdenes, 4 abiertas) | 3 (media — envíos gemelos de Cinthya) | **90**: 27 por número · 34 por piezas · 16 media · 13 baja | 21 |
| **Cinthya** (93 órdenes, 4 abiertas) | **79**: 21 por número · 37 por piezas · 18 media · 3 baja | 1 (media — gemelo de Thalia) | 13 |

→ **Thalia = San Corpe · Cinthya = Kubera.** Con número de envío: **27 de 27** órdenes de
Thalia llegaron a San Corpe y **21 de 21** de Cinthya a Kubera; **cero** cruzadas. Las
«en contra» solo salen por piezas y son envíos gemelos (la otra KAM mandó el mismo SKU con la
misma cantidad a su cuenta en esos días).

## 3) Verificación independiente en vivo (15-sep-2026)

Consultas nuevas, sin las cachés del sondeo (`v01_verificar_evidencia.py`): la orden en Odoo
y su número de envío buscado en las operaciones FULL de **las dos** cuentas.
- **S33830** · creó **Thalia** · socio «FULL» · referencia «72735478» · salida TEX2/OUT/00024 (done, valid. 2026-08-04) → número `72735478`: **0** operaciones en Kubera (BEKURA), **8** en San Corpe (SANCORFASHION).
- **S32443** · creó **Thalia** · socio «FULL» · referencia «71606866» · salida TEX2/OUT/00017 (done, valid. 2026-07-17) → número `71606866`: **0** operaciones en Kubera (BEKURA), **12** en San Corpe (SANCORFASHION).
- **S30942** · creó **Cinthya** · socio «FULL» · referencia «Envío #70165348» · salida TEXCO/OUT/05140 (done, valid. 2026-06-30) → número `70165348`: **6** operaciones en Kubera (BEKURA), **0** en San Corpe (SANCORFASHION).
- **S26840** · creó **Cinthya** · socio «FULL» · referencia «Envío #66039188» · salida TEXCO/OUT/03449 (done, valid. 2026-05-11), TEXCO/OUT/06183 (done, valid. 2026-09-04) → número `66039188`: **5** operaciones en Kubera (BEKURA), **0** en San Corpe (SANCORFASHION).

Lo que devolvió Mercado Libre:
- `S33830` (Thalia): 8 `INBOUND_RECEPTION` de `CAM-0030-IND` en SANCORFASHION el 1–2 ago (hora
  UTC), en lotes de 24 piezas — **antes** de que Odoo validara la salida (4 ago).
- `S32443` (Thalia): 12 operaciones en SANCORFASHION el 18 jul (`CUNA-0018-BEI`…); Odoo validó
  el 17 jul.
- `S30942` (Cinthya): 6 operaciones en BEKURA el 27–28 jun (`MUE-0073-GRI`: recepción de 27
  piezas y ajustes); Odoo validó el 30 jun.
- `S26840` (Cinthya): 5 operaciones en BEKURA el 3 may (`SIL-0019-NEG`). Ojo: esa orden tiene
  **dos** salidas hechas, `TEXCO/OUT/03449` (11 may) y `TEXCO/OUT/06183` (4 sep): una orden de
  venta puede tener varias salidas separadas por meses.

## 4) Las 214 órdenes ML FULL evaluadas (más recientes primero)

Órdenes de socio `FULL` / `MERCADO LIBRE` con más de 1 pieza. «Cuenta por evidencia» es la
que dio la API de ML, **no** la regla del creador.

| Orden | Creada (CDMX) | Creó | Socio | Referencia en Odoo | Salida(s) OUT | Piezas | Cuenta por evidencia | Confianza | Cómo se probó |
|---|---|---|---|---|---|---|---|---|---|
| S38407 | 2026-09-11 11:37 | Cinthya | FULL | — | TEXCO/OUT/06311 (waiting) | 63 | — | sin datos · abierta | sin coincidencias en la ventana |
| S38297 | 2026-09-10 00:46 | Cinthya | FULL | — | TEXCO/OUT/06287 (waiting) | 1742 | — | sin datos · abierta | sin coincidencias en la ventana |
| S38283 | 2026-09-09 16:47 | Thalia | FULL | — | TEX2/OUT/00052 (waiting) | 25 | — | sin datos · abierta | sin coincidencias en la ventana |
| S38280 | 2026-09-09 15:46 | Thalia | FULL | — | TEX2/OUT/00051 (waiting) | 160 | Kubera | media · abierta | piezas cuadran en Kubera: VAR-0670-NEG |
| S38279 | 2026-09-09 15:25 | Thalia | FULL | — | TEXCO/OUT/06281 (done, valid. 2026-09-14) | 25 | — | sin datos | sin coincidencias en la ventana |
| S38278 | 2026-09-09 15:18 | Thalia | FULL | — | TEXCO/OUT/06354 (waiting) | 57 | — | sin datos · abierta | sin coincidencias en la ventana |
| S38185 | 2026-09-08 17:14 | Thalia | FULL | — | TEX2/OUT/00050 (waiting) | 314 | — | sin datos · abierta | sin coincidencias en la ventana |
| S37750 | 2026-09-04 13:06 | Cinthya | FULL | — | TEX2/OUT/00049 (done, valid. 2026-09-11) | 410 | Kubera | media | piezas cuadran en Kubera: VAR-0670-NEG |
| S37015 | 2026-08-27 18:54 | Thalia | FULL | — | TEXCO/OUT/06132 (done, valid. 2026-09-04) | 346 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: MUE-0163-TEL, TEC-0376-BLN, TEC-0522-NEG |
| S37012 | 2026-08-27 18:50 | Thalia | FULL | — | TEXCO/OUT/06131 (done, valid. 2026-09-04) | 907 | San Corpe | baja | piezas cuadran en Kubera: MUE-0160-GRI-NEG, MUE-0160-GRI; piezas cuadran en San Corpe: MUE-0160-NEG, MUE-0160-GRI-NEG, MUE-0160-GRI |
| S36996 | 2026-08-27 13:54 | Thalia | FULL | 75652884 | TEXCO/OUT/06130 (done, valid. 2026-09-04) | 100 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: SIL-008-NEG, SIL-008-BLA, SIL-009-NEG |
| S36993 | 2026-08-27 13:34 | Cinthya | FULL | — | TEXCO/OUT/06129 (done, valid. 2026-09-04) | 365 | Kubera | alta (piezas) | piezas cuadran en Kubera: SIL-008-NEG, SIL-009-NEG, TEC-0324-MUL |
| S36990 | 2026-08-27 13:00 | Cinthya | FULL | — | TEXCO/OUT/06127 (done, valid. 2026-09-04) | 1469 | Kubera | media | piezas cuadran en Kubera: VAR-0453-BLN, MUE-0160-GRI-NEG, ORG-0319-PLA; piezas cuadran en San Corpe: MUE-0160-GRI-NEG |
| S36192 | 2026-08-19 21:30 | Cinthya | FULL | — | TEX2/OUT/00032 (done, valid. 2026-08-28) | 383 | Kubera | alta (piezas) | piezas cuadran en Kubera: CAM-0030-MAT, CAM-0030-QUE, HERR-0300-AZL |
| S36102 | 2026-08-19 10:02 | Thalia | FULL | — | TEX2/OUT/00033 (done, valid. 2026-08-28) | 745 | San Corpe | media | piezas cuadran en Kubera: MES-0086-NEG; piezas cuadran en San Corpe: CAM-0034-AZL, MES-0086-NEG, TEC-2352-GRI |
| S36019 | 2026-08-18 18:29 | Nancy Cruz | MERCADO LIBRE | — | TEX2/OUT/00031 (done, valid. 2026-09-04) | 100 | Kubera | media | piezas cuadran en Kubera: ORG-1016-BLN |
| S36018 | 2026-08-18 18:24 | Nancy Cruz | MERCADO LIBRE | — | TEX2/OUT/00030 (done, valid. 2026-08-28) | 1130 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-1813-NEG-17´, TEC-2194-NEG-128G, TEC-2352-GRI |
| S35635 | 2026-08-13 14:03 | Thalia | FULL | — | TEXCO/OUT/05726 (done, valid. 2026-08-21) | 93 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: MUE-0163-TEL, SIL-0018-BLN, CUNA-0011-GRI |
| S35628 | 2026-08-13 13:19 | Thalia | FULL | — | TEXCO/OUT/05861 (done, valid. 2026-08-26) | 981 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0410-BLN, TEC-0393-ROS |
| S35626 | 2026-08-13 11:24 | Cinthya | FULL | — | TEXCO/OUT/05724 (done, valid. 2026-08-21) | 396 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-1031-NEG, PAS-0018-AZL, PAS-0018-RAN |
| S35129 | 2026-08-06 17:56 | Cinthya | FULL | — | TEX2/OUT/00029 (done, valid. 2026-08-19) | 434 | Kubera | alta (piezas) | piezas cuadran en Kubera: JAR-0008-MET, VEH-0363-NEG, ESCR-0014-BLN |
| S34508 | 2026-07-30 00:36 | Cinthya | FULL | — | TEXCO/OUT/05543 (done, valid. 2026-08-07) | 196 | Kubera | alta (piezas) | piezas cuadran en Kubera: MUE-0074-MAD, MUE-0190-MET, MUE-0225-PLA |
| S34506 | 2026-07-30 00:14 | Cinthya | FULL | — | TEXCO/OUT/05542 (done, valid. 2026-08-07) | 1260 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0393-ROS, TEC-0552-NEG, ORG-0379-NEG-3N |
| S34102 | 2026-07-27 10:59 | Thalia | FULL | — | TEX2/OUT/00022 (done, valid. 2026-07-27) | 45 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: OFI-0221-PLA-80CM, TEC-1840-NEG |
| S33887 | 2026-07-24 09:49 | Thalia | FULL | 72718431 | TEXCO/OUT/05430 (done, valid. 2026-08-07) | 167 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-1031-NEG, JUGU-0189-NEG |
| S33834 | 2026-07-23 18:24 | Cinthya | FULL | — | TEX2/OUT/00023 (done, valid. 2026-08-04) | 545 | Kubera | alta (piezas) | piezas cuadran en Kubera: ORG-0948-NEG, CAM-0030-IND |
| S33831 | 2026-07-23 16:58 | Thalia | FULL | 72735480 | TEX2/OUT/00027 (done, valid. 2026-08-04) | 130 | San Corpe | media | piezas cuadran en San Corpe: MAN-0493-BLN |
| S33830 | 2026-07-23 16:37 | Thalia | FULL | 72735478 | TEX2/OUT/00024 (done, valid. 2026-08-04) | 150 | San Corpe | alta (REF) | número `72735478` recibido en San Corpe; piezas cuadran en San Corpe: CAM-0030-IND |
| S33751 | 2026-07-23 10:21 | Cinthya | FULL | — | TEX2/OUT/00026 (done, valid. 2026-08-04) | 1963 | Kubera | alta (piezas) | piezas cuadran en Kubera: DEC-0012-BLN, DEC-0012-ROJ, DEC-0012-ROS |
| S33122 | 2026-07-17 08:44 | Cinthya | FULL | — | TEX2/OUT/00020 (done, valid. 2026-07-25) | 163 | — | sin datos | sin coincidencias en la ventana |
| S33121 | 2026-07-17 08:27 | Cinthya | FULL | — | TEX2/OUT/00021 (done, valid. 2026-07-30) | 2119 | Kubera | alta (piezas) | piezas cuadran en Kubera: DEC-0014-BLN, DEC-0014-NAR-CLA, JUGU-0268-AZL |
| S33075 | 2026-07-16 17:21 | Thalia | FULL | — | TEX2/OUT/00018 (done, valid. 2026-07-27) | 120 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-1810-BLN, EST-0091-CAF |
| S32447 | 2026-07-08 19:43 | Cinthya | FULL | — | TEX2/OUT/00015 (done, valid. 2026-07-23) | 1874 | Kubera | alta (piezas) | piezas cuadran en Kubera: HERR-0034-AZL-127V, HERR-0035-VER, HERR-0035-AMA |
| S32446 | 2026-07-08 18:55 | Cinthya | FULL | — | TEX2/OUT/00016 (done, valid. 2026-07-23) | 713 | Kubera | media | piezas cuadran en Kubera: HERR-0032-ROJ-110V |
| S32443 | 2026-07-08 17:35 | Thalia | FULL | 71606866 | TEX2/OUT/00017 (done, valid. 2026-07-17) | 174 | San Corpe | alta (REF) | número `71606866` recibido en San Corpe; piezas cuadran en San Corpe: DEP-0016-PLA, ORG-1040-MAD |
| S32343 | 2026-07-07 00:40 | Cinthya | FULL | Envío #71421653 | TEX2/OUT/00010 (done, valid. 2026-07-17) | 96 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-2163-NEG, OFI-0223-NEG, DEP-0017-GRI |
| S32342 | 2026-07-07 00:23 | Cinthya | FULL | Envío #71421653 | TEX2/OUT/00011 (done, valid. 2026-07-17) | 2041 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-2194-NEG-128G, VEH-0062-NAR, JUGU-0274-NAR |
| S32324 | 2026-07-06 18:43 | Thalia | FULL | 71421571 | TEX2/OUT/00009 (done, valid. 2026-07-16) | 123 | San Corpe | alta (REF) | número `71421571` recibido en San Corpe; piezas cuadran en Kubera: TEC-2163-NEG |
| S32003 | 2026-07-01 15:00 | Cinthya | FULL | — | TEXCO/OUT/05200 (done, valid. 2026-08-08) | 1807 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-1114-CAF, EST-0086-NEG, TEC-1648-AZL |
| S31579 | 2026-06-26 10:41 | Thalia | FULL | 70753186 | TEXCO/OUT/05179 (done, valid. 2026-07-04) | 231 | San Corpe | alta (REF) | número `70753186` recibido en San Corpe |
| S31574 | 2026-06-26 10:19 | Thalia | FULL | 70683635 | TEXCO/OUT/05178 (done, valid. 2026-07-06) | 1773 | San Corpe | baja | piezas cuadran en Kubera: ORG-0605-BLN, TEC-1291-MUL; piezas cuadran en San Corpe: TEC-0552-NEG, ORG-0605-BLN, TEC-1291-MUL |
| S31572 | 2026-06-26 09:47 | Cinthya | FULL | — | TEXCO/OUT/05177 (done, valid. 2026-08-07) | 342 | — | sin datos | sin coincidencias en la ventana |
| S31516 | 2026-06-25 19:33 | Cinthya | FULL | Envío #70688003 | TEXCO/OUT/05174 (done, valid. 2026-07-23) | 1920 | — | sin datos | sin coincidencias en la ventana |
| S31325 | 2026-06-23 17:51 | Cinthya | FULL | Envío #70517822 | TEXCO/OUT/05167 (done, valid. 2026-07-06) | 1144 | Kubera | baja | piezas cuadran en Kubera: TEC-0631-PLA, ORG-0475-NEG, ORG-0474-MUL; piezas cuadran en San Corpe: ORG-0475-NEG, ORG-0474-MUL |
| S31324 | 2026-06-23 17:37 | Thalia | FULL | 70144710 | TEXCO/OUT/05165 (done, valid. 2026-07-04) | 824 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: ORG-0250-NEG, ORG-0407-VER, VAR-0453-BLN |
| S30989 | 2026-06-18 11:08 | Thalia | FULL | 70142872 | TEXCO/OUT/05144 (done, valid. 2026-06-26) | 776 | San Corpe | alta (REF) | número `70142872` recibido en San Corpe; piezas cuadran en San Corpe: JUGU-0100-ROJ, TEC-0794-NEG-8PZ-GUANTS |
| S30987 | 2026-06-18 11:01 | Thalia | FULL | 70144708 | TEXCO/OUT/05143 (done, valid. 2026-06-26) | 1798 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0664-BLN, TEC-0393-ROS |
| S30942 | 2026-06-18 00:27 | Cinthya | FULL | Envío #70165348 | TEXCO/OUT/05140 (done, valid. 2026-06-30) | 821 | Kubera | alta (REF) | número `70165348` recibido en Kubera; piezas cuadran en Kubera: JUGU-0100-ROJ, ORG-0384-NEG |
| S30940 | 2026-06-17 23:56 | Cinthya | FULL | Envío #70162735 | TEXCO/OUT/05139 (done, valid. 2026-06-30) | 2040 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0393-ROS, ORG-0319-PLA, TEC-0789-MET |
| S30700 | 2026-06-12 19:57 | Cinthya | FULL | — | TEX2/OUT/00004 (done, valid. 2026-06-22) | 437 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-1817-NEG-LED, ORG-0927-NEG |
| S30696 | 2026-06-12 18:01 | Thalia | FULL | 69627850 | TEX2/OUT/00003 (done, valid. 2026-06-22) | 925 | San Corpe | alta (REF) | número `69627850` recibido en San Corpe; piezas cuadran en San Corpe: ACC-0561-BLN |
| S30636 | 2026-06-11 17:21 | Cinthya | FULL | — | TEX2/OUT/00002 (done, valid. 2026-06-22) | 466 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-2195-MUL-RGB, TEC-2164-NEG, TEC-2159-PLA |
| S30635 | 2026-06-11 17:16 | Cinthya | FULL | — | TEX2/OUT/00001 (done, valid. 2026-06-22) | 2020 | Kubera | media | piezas cuadran en Kubera: TEC-1801-NEG, TEC-2165-NEG-2PZ, TEC-2162-NEG; piezas cuadran en San Corpe: TEC-1801-NEG |
| S30621 | 2026-06-11 14:00 | Thalia | FULL | — | TEXCO/OUT/05108 (done, valid. 2026-06-22) | 571 | Kubera | media | piezas cuadran en Kubera: TEC-2195-MUL-RGB, TEC-2164-NEG, TEC-1824-BLN-AZL; piezas cuadran en San Corpe: TEC-1824-BLN-AZL |
| S30620 | 2026-06-11 13:34 | Thalia | FULL | — | TEXCO/OUT/05107 (done, valid. 2026-06-22) | 1978 | San Corpe | baja | piezas cuadran en Kubera: TEC-1801-NEG, TEC-1842-DOR; piezas cuadran en San Corpe: TEC-1801-NEG, ORG-0841-AZL-L, TEC-1842-DOR |
| S30288 | 2026-06-06 00:22 | Thalia | FULL | — | TEXCO/OUT/04880 (done, valid. 2026-06-15) | 1023 | San Corpe | media | piezas cuadran en Kubera: TEC-0039-AZL; piezas cuadran en San Corpe: TEC-0767-BLN, TEC-0055-MUL, TEC-0039-AZL |
| S30287 | 2026-06-06 00:13 | Cinthya | FULL | Envío #69321580 | TEXCO/OUT/04879 (done, valid. 2026-06-15) | 855 | empate | indeterminada | piezas cuadran en Kubera: TEC-0552-NEG, TEC-0039-AZL; piezas cuadran en San Corpe: TEC-0767-BLN, TEC-0039-AZL |
| S30286 | 2026-06-06 00:01 | Thalia | FULL | — | TEXCO/OUT/04877 (done, valid. 2026-06-15) | 167 | San Corpe | baja | piezas cuadran en Kubera: MES-0045-MAD, EST-0054-NEG; piezas cuadran en San Corpe: TEC-145-TV-NEG, MES-0045-MAD, EST-0054-NEG |
| S30285 | 2026-06-06 00:00 | Cinthya | FULL | Envío #69321579 | TEXCO/OUT/04878 (done, valid. 2026-06-15) | 156 | Kubera | alta (REF) | número `69321579` recibido en Kubera; piezas cuadran en Kubera: TEC-145-TV-NEG, EST-0054-NEG, MES-0045-MAD; piezas cuadran en San Corpe: TEC-145-TV-NEG, EST-0054-NEG, MES-0045-MAD |
| S30153 | 2026-06-04 10:04 | Thalia | FULL | — | TEXCO/OUT/04798 (done, valid. 2026-06-15) | 2029 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0565-GRI-MOR, ORG-0461-NEG-22K, MUE-0252-MAD |
| S30066 | 2026-06-03 20:16 | Cinthya | FULL | Envío #69157273 | TEXCO/OUT/05725 (done, valid. 2026-08-13), TEXCO/OUT/04764 (done, valid. 2026-08-13) | 1876 | — | sin datos | sin coincidencias en la ventana |
| S29498 | 2026-05-30 14:05 | Cinthya | FULL | — | TEXCO/OUT/04357 (done, valid. 2026-06-05) | 516 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0794-NEG-8PZ-GUANTS, TEC-0793-NEG-4PZ |
| S29400 | 2026-05-29 12:41 | Thalia | FULL | — | TEXCO/OUT/04247 (done, valid. 2026-06-05) | 594 | — | sin datos | sin coincidencias en la ventana |
| S29275 | 2026-05-28 10:20 | Thalia | FULL | — | TEXCO/OUT/04142 (done, valid. 2026-06-05) | 1996 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0664-BLN, TEC-0789-MET, TEC-1519-TRANS-200PZ |
| S29170 | 2026-05-27 17:09 | Cinthya | FULL | Envío #68642132 | TEXCO/OUT/04085 (done, valid. 2026-06-05) | 1990 | Kubera | media | piezas cuadran en Kubera: TEC-0552-AZL, JUGU-0171-MUL, TEC-0894-ROJ; piezas cuadran en San Corpe: TEC-0894-ROJ |
| S28800 | 2026-05-23 10:23 | Cinthya | FULL | — | TEXCO/OUT/04248 (done, valid. 2026-05-29) | 1085 | Kubera | media | piezas cuadran en Kubera: MUE-0215-GRI |
| S28767 | 2026-05-22 15:04 | Thalia | FULL | 68238773 | TEXCO/OUT/03879 (done, valid. 2026-06-02) | 689 | San Corpe | alta (REF) | número `68238773` recibido en San Corpe |
| S28751 | 2026-05-22 11:30 | Thalia | FULL | 68238771 | TEXCO/OUT/03875 (done, valid. 2026-06-08) | 30 | San Corpe | alta (REF) | número `68238771` recibido en San Corpe; piezas cuadran en San Corpe: OFI-0077-MAD, OFI-0002-NEG |
| S28674 | 2026-05-21 11:01 | Thalia | FULL | — | TEXCO/OUT/03844 (done, valid. 2026-05-22) | 49 | — | sin datos | sin coincidencias en la ventana |
| S28662 | 2026-05-21 08:41 | Cinthya | FULL | Envío #68133064 | TEXCO/OUT/04227 (done, valid. 2026-06-02) | 2007 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0066-MOR, ORG-0385-NEG, TEC-1032-NEG-SOL |
| S28625 | 2026-05-20 17:33 | Thalia | FULL | 68111949 | TEXCO/OUT/03817 (done, valid. 2026-05-30) | 1923 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: JUGU-0039-MUL, TEC-0870-BLN, ORG-0196-VIN |
| S28537 | 2026-05-19 13:00 | Thalia | FULL | — | TEXCO/OUT/03781 (done, valid. 2026-05-25) | 474 | San Corpe | media | piezas cuadran en Kubera: TEC-0521-NEG; piezas cuadran en San Corpe: MUE-0163-TEL, TEC-0168-MET, TEC-0521-NEG |
| S28193 | 2026-05-13 13:15 | Cinthya | FULL | — | TEXCO/OUT/03647 (done, valid. 2026-05-22) | 1508 | Kubera | media | piezas cuadran en Kubera: JUEG-0012-MAD, JUEG-0012-MUL, ACC-0228-MAD; piezas cuadran en San Corpe: JUEG-0012-MUL |
| S28192 | 2026-05-13 12:51 | Thalia | FULL | 67464513 | TEXCO/OUT/03646 (done, valid. 2026-05-25) | 1692 | San Corpe | baja | piezas cuadran en Kubera: TEC-0393-ROS; piezas cuadran en San Corpe: TEC-0410-BLN, JUGU-0176-AZL-ROS |
| S28191 | 2026-05-13 11:57 | Thalia | FULL | 67191360 | TEXCO/OUT/03645 (done, valid. 2026-05-25) | 916 | San Corpe | media | piezas cuadran en Kubera: JUEG-0012-MUL; piezas cuadran en San Corpe: ORG-0476-MUL, JUEG-0012-MUL, ORG-0382-GRI-XL |
| S28161 | 2026-05-12 21:10 | Cinthya | FULL | Envío #67467752 | TEXCO/OUT/03631 (done, valid. 2026-05-26) | 1982 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0789-MET, TEC-0552-AZL |
| S28066 | 2026-05-11 10:40 | Thalia | FULL | 67191361 | TEXCO/OUT/03597 (done, valid. 2026-05-22) | 898 | San Corpe | baja | piezas cuadran en Kubera: MUE-0074-MAD; piezas cuadran en San Corpe: TEC-1032-NEG-SOL, CUNA-0011-AZL |
| S28064 | 2026-05-11 10:26 | Thalia | FULL | 67009231 | TEXCO/OUT/03595 (done, valid. 2026-05-22) | 75 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0990-NEG, TEC-0488-AMA |
| S27909 | 2026-05-08 13:58 | Cinthya | FULL | Envío #67122900 | TEXCO/OUT/03534 (done, valid. 2026-05-20) | 1040 | Kubera | media | piezas cuadran en Kubera: MUE-0074-MAD, TEC-0799-NEG, OFI-0077-MAD; piezas cuadran en San Corpe: TEC-0799-NEG |
| S27894 | 2026-05-08 11:21 | Thalia | FULL | — | TEXCO/OUT/03530 (done, valid. 2026-05-21) | 1191 | — | sin datos | sin coincidencias en la ventana |
| S27843 | 2026-05-07 15:01 | Thalia | FULL | — | TEXCO/OUT/03518 (done, valid. 2026-06-24) | 29 | — | sin datos | sin coincidencias en la ventana |
| S27831 | 2026-05-07 13:23 | Cinthya | FULL | Envío #67079920 | TEXCO/OUT/03516 (done, valid. 2026-05-22) | 2297 | Kubera | media | piezas cuadran en Kubera: TEC-1606-NEG, MUE-0135-NEG, TEC-0551-PLU; piezas cuadran en San Corpe: TEC-1606-NEG |
| S27823 | 2026-05-07 11:25 | Thalia | FULL | 67009231 | TEXCO/OUT/03512 (done, valid. 2026-05-22) | 1928 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0778-NEG, MUE-0135-NEG |
| S27764 | 2026-05-06 19:49 | Thalia | FULL | 66636679 | TEXCO/OUT/03483 (done, valid. 2026-06-24) | 106 | San Corpe | alta (REF) | número `66636679` recibido en San Corpe |
| S27629 | 2026-05-04 18:23 | Thalia | FULL | — | TEXCO/OUT/03443 (done, valid. 2026-05-05) | 162 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0012-NEG, TEC-0011-NEG, OFI-0077-MAD |
| S27507 | 2026-05-02 10:43 | Cinthya | FULL | Envío #66717767 | TEXCO/OUT/03402 (done, valid. 2026-05-15) | 1955 | Kubera | alta (REF) | número `66717767` recibido en Kubera; piezas cuadran en Kubera: TEC-0778-NEG; piezas cuadran en San Corpe: TEC-0778-NEG |
| S27506 | 2026-05-02 10:17 | Thalia | FULL | 66636679 | TEXCO/OUT/03401 (done, valid. 2026-05-15) | 1394 | San Corpe | alta (REF) | número `66636679` recibido en San Corpe |
| S27489 | 2026-05-02 01:04 | Cinthya | FULL | Envío #66568372 | TEXCO/OUT/03399 (done, valid. 2026-05-13) | 1937 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0434-AZL, TEC-0066-MOR, TEC-0066-ROJ |
| S27377 | 2026-04-30 16:49 | Thalia | FULL | 66552545 | TEXCO/OUT/03690 (done, valid. 2026-05-15) | 2113 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0664-NEG, ORG-0384-NEG |
| S27130 | 2026-04-27 13:58 | Thalia | FULL | — | TEXCO/OUT/03251 (done, valid. 2026-04-27) | 110 | San Corpe | media | piezas cuadran en San Corpe: EST-0078-TRANS-GRI |
| S26930 | 2026-04-24 16:50 | Cinthya | FULL | ENVIO #65973717 | TEXCO/OUT/03216 (done, valid. 2026-04-29) | 1795 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0410-BLN, TEC-0066-ROS |
| S26844 | 2026-04-23 19:18 | Thalia | FULL | 66024862 | TEXCO/OUT/03201 (done, valid. 2026-05-11) | 330 | San Corpe | alta (REF) | número `66024862` recibido en San Corpe; piezas cuadran en Kubera: TEC-1045-BEI-2; piezas cuadran en San Corpe: ROP-0266-DOR, JUGU-0077-MUL, TEC-1045-BEI-2 |
| S26843 | 2026-04-23 19:08 | Thalia | FULL | 66022287 | TEXCO/OUT/03200 (done, valid. 2026-05-11) | 1881 | — | sin datos | sin coincidencias en la ventana |
| S26840 | 2026-04-23 17:44 | Cinthya | FULL | Envío #66039188 | TEXCO/OUT/03449 (done, valid. 2026-05-11), TEXCO/OUT/06183 (done, valid. 2026-09-04) | 500 | Kubera | alta (REF) | número `66039188` recibido en Kubera; piezas cuadran en Kubera: JUGU-0003-MAD, MASC-0042-GRI |
| S26752 | 2026-04-22 12:37 | Thalia | FULL | — | TEXCO/OUT/03188 (done, valid. 2026-04-27) | 70 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0434-AZL, ORG-0771-MUL |
| S26447 | 2026-04-20 11:14 | Cinthya | FULL | Envío #65853249 | TEXCO/OUT/03166 (done, valid. 2026-05-11) | 379 | Kubera | alta (piezas) | piezas cuadran en Kubera: ROP-0266-DOR, JUGU-0141-ROS |
| S26441 | 2026-04-20 10:36 | Cinthya | FULL | Envío #65853250 | TEXCO/OUT/03165 (waiting) | 628 | Kubera | media · abierta | piezas cuadran en Kubera: MUE-0150-MET |
| S26309 | 2026-04-18 00:43 | Thalia | FULL | 65745173 | TEXCO/OUT/03159 (done, valid. 2026-04-27) | 36 | San Corpe | media | piezas cuadran en San Corpe: PAS-0024-NEG-TRIC |
| S26261 | 2026-04-16 20:37 | Cinthya | FULL | Envío #65640270 | TEXCO/OUT/03156 (done, valid. 2026-04-27) | 500 | Kubera | alta (REF) | número `65640270` recibido en Kubera |
| S26260 | 2026-04-16 19:31 | Thalia | FULL | 65653141 | TEXCO/OUT/03155 (done, valid. 2026-04-27) | 355 | San Corpe | media | piezas cuadran en Kubera: CAM-0021-NAR; piezas cuadran en San Corpe: OFI-0178-GRI, CAM-0021-NAR, TEC-1040-VER-IND |
| S26259 | 2026-04-16 19:26 | Thalia | FULL | 65653140 | TEXCO/OUT/03154 (done, valid. 2026-04-27) | 145 | San Corpe | baja | piezas cuadran en Kubera: TEC-1778-NEG; piezas cuadran en San Corpe: VAR-0455-EST, TEC-1778-NEG |
| S26253 | 2026-04-16 15:55 | Cinthya | FULL | Envío #65641643 | TEXCO/OUT/03150 (done, valid. 2026-04-27) | 500 | Kubera | alta (piezas) | piezas cuadran en Kubera: PEL-0006-MUL, MUE-0135-NEG, MUE-0172-NEG |
| S26240 | 2026-04-16 13:01 | Cinthya | FULL | Envío #65633589 | TEXCO/OUT/03146 (done, valid. 2026-04-27) | 816 | Kubera | alta (piezas) | piezas cuadran en Kubera: ORG-0470-NEG, TEC-0832-MET, TEC-1768-BLN |
| S26235 | 2026-04-16 11:46 | Thalia | FULL | 65562365 | TEXCO/OUT/03144 (done, valid. 2026-04-27) | 385 | San Corpe | media | piezas cuadran en Kubera: TEC-0862-NEG; piezas cuadran en San Corpe: TEC-0856-AMA, TEC-0796-NEG, TEC-0862-NEG |
| S26232 | 2026-04-16 11:14 | Cinthya | FULL | Envío #65488836 | TEXCO/OUT/03143 (done, valid. 2026-04-27) | 527 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-1018-NEG-8M, TEC-1035-NEG-8T |
| S26229 | 2026-04-16 10:38 | Cinthya | FULL | Envío #65488835 | TEXCO/OUT/03145 (done, valid. 2026-04-27) | 271 | Kubera | alta (piezas) | piezas cuadran en Kubera: VEH-0030-AZL, TEC-1783-PLA |
| S26083 | 2026-04-14 14:27 | Thalia | FULL | 65457342 | TEXCO/OUT/03129 (done, valid. 2026-04-27) | 70 | San Corpe | baja | piezas cuadran en Kubera: ORG-0771-MUL; piezas cuadran en San Corpe: ORG-0763-MET, TEC-0582-MET |
| S26080 | 2026-04-14 14:06 | Thalia | FULL | 65457341 | TEXCO/OUT/03127 (done, valid. 2026-04-22) | 378 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: ORG-0399-MET, VAR-0456-NEG, TEC-0011-NEG |
| S26078 | 2026-04-14 13:17 | Cinthya | FULL | Envío #65441407 | TEXCO/OUT/03126 (done, valid. 2026-05-11) | 528 | — | sin datos | sin coincidencias en la ventana |
| S26070 | 2026-04-14 10:45 | Cinthya | FULL | Envío #65441406 | TEXCO/OUT/03124 (done, valid. 2026-06-24) | 214 | — | sin datos | sin coincidencias en la ventana |
| S26068 | 2026-04-14 10:31 | Thalia | FULL | 65441470 | TEXCO/OUT/03123 (done, valid. 2026-04-20) | 1055 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0664-NEG, TEC-0664-BLN, TEC-0393-ROS |
| S26066 | 2026-04-14 10:14 | Thalia | FULL | 65441469 | TEXCO/OUT/03122 (done, valid. 2026-04-20) | 135 | empate | indeterminada | piezas cuadran en Kubera: TEC-0012-NEG, TEC-0881-BLN-LIL; piezas cuadran en San Corpe: TEC-0012-NEG, TEC-0881-BLN-LIL |
| S25795 | 2026-04-09 20:07 | Cinthya | FULL | Envío #65159541 | TEXCO/OUT/03093 (waiting) | 532 | Kubera | alta (REF) · abierta | número `65159541` recibido en Kubera; piezas cuadran en Kubera: TEC-0618-NEG; piezas cuadran en San Corpe: TEC-0618-NEG |
| S25794 | 2026-04-09 19:52 | Cinthya | FULL | Envío #65159540 | TEXCO/OUT/03092 (done, valid. 2026-04-20) | 2011 | Kubera | alta (piezas) | piezas cuadran en Kubera: MUE-0226-DOR, TEC-0769-AZL-NEG, TEC-1315-NEG |
| S25791 | 2026-04-09 19:07 | Thalia | FULL | 64669839 | TEXCO/OUT/03091 (done, valid. 2026-04-17) | 356 | San Corpe | alta (REF) | número `64669839` recibido en San Corpe; piezas cuadran en Kubera: TEC-1775-NEG, TEC-1787-NEG; piezas cuadran en San Corpe: TEC-1775-NEG, TEC-1787-NEG |
| S25786 | 2026-04-09 17:50 | Thalia | FULL | 64669838 | TEXCO/OUT/03090 (done, valid. 2026-04-15) | 597 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: ACC-0356-BLN, TEC-1783-PLA |
| S25770 | 2026-04-09 14:36 | Cinthya | FULL | Envío #64962064 | TEXCO/OUT/03089 (done, valid. 2026-04-17) | 846 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0434-AZL, MUE-0232-DOR, TEC-0646-NEG |
| S25769 | 2026-04-09 13:19 | Cinthya | FULL | Envío #64962063 | TEXCO/OUT/03088 (done, valid. 2026-04-17) | 150 | Kubera | alta (REF) | número `64962063` recibido en Kubera; piezas cuadran en Kubera: OFI-0002-NEG |
| S25655 | 2026-04-07 14:55 | Thalia | FULL | 64978010 | TEXCO/OUT/03084 (done, valid. 2026-04-13) | 158 | San Corpe | alta (REF) | número `64978010` recibido en San Corpe; piezas cuadran en San Corpe: ORG-0387-PLA, TEC-0011-NEG, TEC-0479-NEG |
| S25653 | 2026-04-07 14:44 | Thalia | FULL | 64978009 | TEXCO/OUT/03083 (done, valid. 2026-04-13) | 1034 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: BEB-0025-TEL, TEC-1328-NEG, CAM-0012-BEI |
| S25644 | 2026-04-07 11:46 | Cinthya | FULL | — | TEXCO/OUT/03082 (done, valid. 2026-05-11) | 879 | — | sin datos | sin coincidencias en la ventana |
| S25639 | 2026-04-07 10:08 | Thalia | FULL | 64901309 | TEXCO/OUT/03081 (done, valid. 2026-04-15) | 132 | San Corpe | alta (REF) | número `64901309` recibido en San Corpe; piezas cuadran en San Corpe: TEC-1006-BLN |
| S25634 | 2026-04-07 09:44 | Thalia | FULL | 64901308 | TEXCO/OUT/03080 (done, valid. 2026-04-13) | 327 | San Corpe | media | piezas cuadran en Kubera: TEC-0480-NEG-200A; piezas cuadran en San Corpe: TEC-0480-NEG-200A, TEC-1327-NEG, TEC-1225-NEG |
| S25187 | 2026-03-31 14:09 | Cinthya | FULL | Envío #64442765 | TEXCO/OUT/03054 (done, valid. 2026-04-07) | 30 | Kubera | alta (REF) | número `64442765` recibido en Kubera; piezas cuadran en San Corpe: TEC-1770-NEG |
| S25183 | 2026-03-31 12:37 | Thalia | FULL | 64509494 | TEXCO/OUT/03053 (done, valid. 2026-04-15) | 146 | San Corpe | alta (REF) | número `64509494` recibido en San Corpe |
| S25182 | 2026-03-31 12:31 | Thalia | FULL | 64568118 | TEXCO/OUT/03052 (done, valid. 2026-04-15) | 74 | San Corpe | alta (REF) | número `64568118` recibido en San Corpe; piezas cuadran en Kubera: TEC-0293-NEG; piezas cuadran en San Corpe: TEC-0293-NEG |
| S25180 | 2026-03-31 12:11 | Thalia | FULL | 64568117 | TEXCO/OUT/03051 (done, valid. 2026-04-09) | 286 | San Corpe | media | piezas cuadran en Kubera: TEC-0912-NEG; piezas cuadran en San Corpe: TEC-0626-NEG, TEC-0936-BLN, TEC-0912-NEG |
| S25117 | 2026-03-30 19:11 | Thalia | FULL | 64509493 | TEXCO/OUT/03042 (done, valid. 2026-04-08) | 996 | San Corpe | media | piezas cuadran en Kubera: TEC-0769-AZL-NEG; piezas cuadran en San Corpe: TEC-0777-VER, TEC-0769-AZL-NEG, TEC-0053-MUL |
| S25090 | 2026-03-30 12:49 | Cinthya | FULL | Envío #64442764 | TEXCO/OUT/03030 (done, valid. 2026-04-07) | 1060 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0434-NEG, TEC-1114-CAF |
| S25078 | 2026-03-30 10:19 | Thalia | FULL | 64369396 | TEXCO/OUT/03028 (done, valid. 2026-04-07) | 836 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: OFI-0118-NEG, TEC-1013-NEG, VAR-0446-NEG |
| S25076 | 2026-03-30 10:11 | Thalia | FULL | 64480490 | TEXCO/OUT/03025 (done, valid. 2026-04-02) | 330 | San Corpe | baja | piezas cuadran en Kubera: TEC-0434-NEG; piezas cuadran en San Corpe: TEC-0434-AZL, BEB-0004-VER |
| S25075 | 2026-03-30 10:06 | Thalia | FULL | 64480491 | TEXCO/OUT/03023 (done, valid. 2026-04-03) | 12 | — | sin datos | sin coincidencias en la ventana |
| S25071 | 2026-03-30 09:34 | Thalia | FULL | 64369397 | TEXCO/OUT/03022 (done, valid. 2026-04-03) | 30 | — | sin datos | sin coincidencias en la ventana |
| S24951 | 2026-03-27 14:29 | Cinthya | FULL | Envío #64317424 | TEXCO/OUT/02975 (done, valid. 2026-04-07) | 900 | Kubera | baja | piezas cuadran en Kubera: TEC-0674-NEG, MASC-0084-NEG; piezas cuadran en San Corpe: MASC-0084-NEG |
| S24891 | 2026-03-26 17:33 | Thalia | FULL | 64219802 | TEXCO/OUT/02950 (done, valid. 2026-04-02) | 90 | San Corpe | alta (REF) | número `64219802` recibido en San Corpe; piezas cuadran en San Corpe: TEC-0682-NEG |
| S24880 | 2026-03-26 12:22 | Cinthya | FULL | Envío #64222810 | TEXCO/OUT/02942 (done, valid. 2026-04-07) | 885 | empate | indeterminada | piezas cuadran en Kubera: MASC-0044-NEG; piezas cuadran en San Corpe: TEC-0434-NEG |
| S24875 | 2026-03-26 11:16 | Cinthya | FULL | Envío #64222811 | TEXCO/OUT/02939 (done, valid. 2026-04-09) | 575 | Kubera | alta (REF) | número `64222811` recibido en Kubera; piezas cuadran en Kubera: TEC-0492-MUL, TEC-0293-NEG; piezas cuadran en San Corpe: TEC-0492-MUL, TEC-0293-NEG |
| S24840 | 2026-03-25 18:42 | Thalia | FULL | 64219801 | TEXCO/OUT/02923 (done, valid. 2026-03-31) | 760 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0789-MET, TEC-0393-ROS, TEC-0663-BLN |
| S24826 | 2026-03-25 14:38 | Thalia | FULL | 64115463 | TEXCO/OUT/02916 (done, valid. 2026-04-09) | 125 | — | sin datos | sin coincidencias en la ventana |
| S24825 | 2026-03-25 14:34 | Thalia | FULL | 64113764 | TEXCO/OUT/02915 (done, valid. 2026-04-09) | 130 | San Corpe | media | piezas cuadran en San Corpe: TEC-0779-BLN |
| S24824 | 2026-03-25 14:30 | Thalia | FULL | 64135938 | TEXCO/OUT/02913 (done, valid. 2026-04-09) | 160 | empate | indeterminada | piezas cuadran en Kubera: TEC-0492-MUL, TEC-0618-NEG; piezas cuadran en San Corpe: TEC-0492-MUL, TEC-0618-NEG |
| S24822 | 2026-03-25 14:20 | Thalia | FULL | 64135937 | TEXCO/OUT/02912 (done, valid. 2026-03-31) | 390 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-1441-NEG, TEC-0784-MET, VEH-0021-NEG |
| S24804 | 2026-03-25 09:30 | Cinthya | FULL | Envío #64149871 | TEXCO/OUT/02907 (done, valid. 2026-03-31) | 820 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0066-ROJ, BEB-0004-AZL |
| S24744 | 2026-03-24 11:43 | Thalia | FULL | 64115462 | TEXCO/OUT/02873 (done, valid. 2026-03-31) | 105 | San Corpe | baja | piezas cuadran en Kubera: TEC-0009-PLA; piezas cuadran en San Corpe: TEC-0009-PLA, BEB-0004-VER |
| S24737 | 2026-03-24 11:20 | Thalia | FULL | 64113763 | TEXCO/OUT/02870 (done, valid. 2026-03-31) | 320 | San Corpe | media | piezas cuadran en Kubera: TEC-0589-NEG; piezas cuadran en San Corpe: TEC-1151-PLA, TEC-0589-NEG, BEB-0041-ROS |
| S24733 | 2026-03-24 10:12 | Thalia | FULL | 64070112 | TEXCO/OUT/02869 (done, valid. 2026-03-31) | 50 | San Corpe | alta (REF) | número `64070112` recibido en San Corpe; piezas cuadran en San Corpe: TEC-0591-NEG |
| S24704 | 2026-03-23 22:03 | Thalia | FULL | 64070111 | TEXCO/OUT/02865 (done, valid. 2026-03-31) | 709 | empate | indeterminada | piezas cuadran en Kubera: MUE-0135-BLN, MUE-0135-NEG; piezas cuadran en San Corpe: MUE-0135-BLN, MUE-0135-NEG |
| S24700 | 2026-03-23 19:14 | Cinthya | FULL | Envío #64057926 | TEXCO/OUT/02860 (done, valid. 2026-04-02) | 347 | Kubera | alta (REF) | número `64057926` recibido en Kubera |
| S24699 | 2026-03-23 18:58 | Cinthya | FULL | Envío #64057925 | TEXCO/OUT/02859 (done, valid. 2026-03-31) | 303 | Kubera | media | piezas cuadran en Kubera: TEC-0393-ROS, TEC-1016-MET, TEC-0010-MUL; piezas cuadran en San Corpe: TEC-0010-MUL |
| S24691 | 2026-03-23 16:54 | Thalia | FULL | 63981091 | TEXCO/OUT/02857 (done, valid. 2026-03-27) | 141 | San Corpe | alta (REF) | número `63981091` recibido en San Corpe |
| S24667 | 2026-03-23 10:50 | Thalia | FULL | 63981090 | TEXCO/OUT/02844 (done, valid. 2026-03-26) | 648 | San Corpe | media | piezas cuadran en Kubera: TEC-0631-PLA; piezas cuadran en San Corpe: BEB-0025-TEL, TEC-0631-PLA, TEC-1016-MET |
| S24539 | 2026-03-20 15:07 | Thalia | FULL | 63763840 | TEXCO/OUT/02747 (done, valid. 2026-03-31) | 210 | San Corpe | alta (REF) | número `63763840` recibido en San Corpe; piezas cuadran en San Corpe: CAM-0017-BLN, TEC-0933-NEG |
| S24536 | 2026-03-20 14:54 | Thalia | FULL | 63910199 | TEXCO/OUT/02745 (done, valid. 2026-03-26) | 431 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0664-NEG, TEC-0664-ROS, TEC-0664-BLN |
| S24462 | 2026-03-19 10:43 | Thalia | FULL | 63763839 | TEXCO/OUT/02692 (done, valid. 2026-03-25) | 635 | San Corpe | baja | piezas cuadran en Kubera: BEB-0025-TEL, TEC-0434-AZL; piezas cuadran en San Corpe: BEB-0025-TEL, TEC-0010-MUL, TEC-0434-AZL |
| S24399 | 2026-03-18 08:02 | Cinthya | FULL | KUBERA ENVIO FULL #63685882 | TEXCO/OUT/02657 (done, valid. 2026-03-31) | 60 | Kubera | alta (REF) | número `63685882` recibido en Kubera; piezas cuadran en Kubera: MUE-0168-NEG |
| S24396 | 2026-03-18 07:54 | Cinthya | FULL | KUBERA ENVIO FULL #63688282 | TEXCO/OUT/02656 (done, valid. 2026-03-25) | 300 | Kubera | media | piezas cuadran en Kubera: TEC-0434-AZL, TEC-0631-PLA, TEC-0434-NEG; piezas cuadran en San Corpe: TEC-0631-PLA |
| S24395 | 2026-03-18 07:44 | Cinthya | FULL | KUBERA ENVIO FULL #63685881 | TEXCO/OUT/02655 (done, valid. 2026-03-25) | 564 | Kubera | alta (piezas) | piezas cuadran en Kubera: ORG-0402-PLA, TEC-0107-RO-NE-4CE, ORG-0401-MUL |
| S24347 | 2026-03-17 10:55 | Vale | full | — | TEXCO/OUT/02630 (done, valid. 2026-03-19) | 30 | — | sin datos | sin coincidencias en la ventana |
| S24150 | 2026-03-13 13:05 | Vale | full | — | TEXCO/OUT/02613 (done, valid. 2026-03-17) | 10 | — | sin datos | sin coincidencias en la ventana |
| S24106 | 2026-03-12 18:54 | Thalia | FULL | 63196012 | TEXCO/OUT/02465 (done, valid. 2026-03-26) | 78 | — | sin datos | sin coincidencias en la ventana |
| S24105 | 2026-03-12 18:44 | Thalia | FULL | 63376921 | TEXCO/OUT/02464 (done, valid. 2026-03-19) | 97 | Kubera | media | piezas cuadran en Kubera: ORG-0263-MET |
| S24099 | 2026-03-12 17:00 | Thalia | FULL | 63376920 | TEXCO/OUT/02463 (done, valid. 2026-03-19) | 357 | empate | indeterminada | piezas cuadran en Kubera: TEC-1103-PLA; piezas cuadran en San Corpe: TEC-1103-PLA |
| S24087 | 2026-03-12 13:19 | Cinthya | FULL | Envío #63372414 | TEXCO/OUT/02453 (done, valid. 2026-03-26) | 350 | Kubera | media | piezas cuadran en Kubera: BEB-0025-TEL, MUE-0137-NEG, BEB-0036-PLA; piezas cuadran en San Corpe: BEB-0025-TEL |
| S24086 | 2026-03-12 13:09 | Cinthya | FULL | — | TEXCO/OUT/02452 (done, valid. 2026-03-19) | 250 | — | sin datos | sin coincidencias en la ventana |
| S23933 | 2026-03-10 11:08 | Thalia | FULL | 63196011 | TEXCO/OUT/02314 (done, valid. 2026-03-17) | 160 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: PAS-0021-BLN-NEG, JUGU-0043-MUL, TEC-0405-MET |
| S23737 | 2026-03-06 14:43 | Thalia | FULL | 62988973 | TEXCO/OUT/02032 (done, valid. 2026-03-12) | 582 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0669-BLN-ROS, TEC-0669-BLN-AZL, TEC-0669-NEG |
| S23730 | 2026-03-06 13:44 | Thalia | FULL | 62988974 | TEXCO/OUT/02029 (done, valid. 2026-03-12) | 240 | San Corpe | alta (REF) | número `62988974` recibido en San Corpe; piezas cuadran en Kubera: SIL-0021-BLN |
| S23725 | 2026-03-06 11:00 | Cinthya | FULL | Envío #62934497 | TEXCO/OUT/02011 (done, valid. 2026-03-12) | 70 | Kubera | alta (REF) | número `62934497` recibido en Kubera; piezas cuadran en Kubera: MUE-0168-NEG |
| S23724 | 2026-03-06 10:53 | Cinthya | FULL | ENVIO #62934496 | TEXCO/OUT/02010 (done, valid. 2026-03-12) | 350 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0551-PLU, BEB-0033-MUL, TEC-0646-NEG |
| S23676 | 2026-03-05 11:16 | Vale | FULL | #62835925 | TEXCO/OUT/01916 (done, valid. 2026-03-26) | 140 | — | sin datos | sin coincidencias en la ventana |
| S23616 | 2026-03-04 09:54 | Thalia | FULL | 62674389 | TEXCO/OUT/01774 (done, valid. 2026-03-10) | 426 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0434-NEG, BEB-0004-VER, TEC-0434-AZL |
| S23459 | 2026-03-02 14:01 | Thalia | FULL | 62674390 | TEXCO/OUT/01471 (done, valid. 2026-03-06) | 90 | San Corpe | alta (REF) | número `62674390` recibido en San Corpe; piezas cuadran en Kubera: MUE-0168-NEG; piezas cuadran en San Corpe: ORG-0269-NEG |
| S23457 | 2026-03-02 13:56 | Thalia | FULL | 62388708 | TEXCO/OUT/01470 (done, valid. 2026-03-06) | 386 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0524-AZL, TEC-0664-AZL, TEC-0664-BLN |
| S23452 | 2026-03-02 12:50 | Thalia | FULL | 62388707 | TEXCO/OUT/01460 (done, valid. 2026-03-06) | 210 | empate | indeterminada | piezas cuadran en Kubera: MUE-0135-NEG, MUE-0135-BLN; piezas cuadran en San Corpe: MUE-0135-NEG, MUE-0135-BLN |
| S23445 | 2026-03-02 11:33 | Cinthya | FULL | KUBERA FULL #62669141 | TEXCO/OUT/01451 (done, valid. 2026-03-06) | 250 | Kubera | alta (REF) | número `62669141` recibido en Kubera; piezas cuadran en Kubera: MUE-0168-NEG |
| S23443 | 2026-03-02 11:26 | Cinthya | FULL | FULL KUBERA #62669140 | TEXCO/OUT/01450 (done, valid. 2026-03-06) | 50 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0574-NEG, TEC-0574-AZL |
| S23440 | 2026-03-02 11:05 | Cinthya | FULL | FULL KUBERA #62377191 | TEXCO/OUT/01448 (done, valid. 2026-03-10) | 105 | Kubera | alta (REF) | número `62377191` recibido en Kubera; piezas cuadran en Kubera: ORG-0263-MET; piezas cuadran en San Corpe: OFI-0001-NEG |
| S22977 | 2026-02-20 14:27 | Liliana Blanco Reyes | FULL 59126318 | — | TEXCO/OUT/00813 (done, valid. 2026-02-24) | 125 | — | sin datos | sin coincidencias en la ventana |
| S22889 | 2026-02-18 17:18 | Cinthya | FULL | #62009377 | TEXCO/OUT/00747 (done, valid. 2026-02-21) | 215 | Kubera | alta (REF) | número `62009377` recibido en Kubera |
| S22888 | 2026-02-18 16:53 | Cinthya | FULL | #62004182 | TEXCO/OUT/00743 (done, valid. 2026-02-21) | 144 | Kubera | media | piezas cuadran en Kubera: ORG-0278-BLN |
| S22883 | 2026-02-18 16:41 | Thalia | FULL | 61940756 | TEXCO/OUT/00741 (done, valid. 2026-02-21) | 160 | San Corpe | baja | piezas cuadran en Kubera: OFI-0001-NEG; piezas cuadran en San Corpe: TEC-0059-MUL, TEC-0060-MUL |
| S22878 | 2026-02-18 14:25 | Cinthya | FULL | #61995792 | TEXCO/OUT/00733 (done, valid. 2026-02-21) | 180 | Kubera | media | piezas cuadran en Kubera: TEC-0634-ROS, TEC-0553-PLS, TEC-0647-NEG; piezas cuadran en San Corpe: TEC-0553-PLS |
| S22874 | 2026-02-18 13:29 | Thalia | FULL | 61406257 | TEXCO/OUT/00730 (done, valid. 2026-02-25) | 42 | San Corpe | alta (REF) | número `61406257` recibido en San Corpe |
| S22867 | 2026-02-18 12:18 | Thalia | FULL | 61940755 | TEXCO/OUT/00727 (done, valid. 2026-02-25) | 458 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: CALZ-0125-BLN-GRI-43, CALZ-0144-BLN-DOR-38, CALZ-0144-NEG-DOR-37 |
| S22832 | 2026-02-17 21:44 | Cinthya | FULL | #61955966 | TEXCO/OUT/00704 (done, valid. 2026-02-19) | 80 | Kubera | alta (REF) | número `61955966` recibido en Kubera; piezas cuadran en Kubera: MUN-0004-MUL, MUE-0163-TEL, TEC-0625-NEG |
| S22830 | 2026-02-17 19:05 | Cinthya | FULL | #61955967 | TEXCO/OUT/00697 (done, valid. 2026-02-19) | 160 | Kubera | media | piezas cuadran en Kubera: TEC-0639-NEG, TEC-0605-NEG-2PZ, TEC-0552-NEG; piezas cuadran en San Corpe: TEC-0552-NEG |
| S22782 | 2026-02-17 08:41 | Cinthya | FULL | 61915801 | TEXCO/OUT/00668 (done, valid. 2026-02-19) | 207 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0651-AP-NLJ-200, TEC-0633-ROS, TEC-0409-NEG |
| S22781 | 2026-02-17 08:27 | Cinthya | FULL | FULL 61915800 | TEXCO/OUT/00667 (done, valid. 2026-02-21) | 50 | Kubera | alta (REF) | número `61915800` recibido en Kubera; piezas cuadran en Kubera: OFI-0002-NEG |
| S22631 | 2026-02-14 08:16 | Cinthya | FULL | #61770040 | TEXCO/OUT/00538 (done, valid. 2026-02-18) | 246 | Kubera | baja | piezas cuadran en Kubera: TEC-0328-NEG, TEC-0393-ROS; piezas cuadran en San Corpe: TEC-0393-ROS |
| S22558 | 2026-02-12 21:11 | Thalia | FULL | 61760684 | TEXCO/OUT/00500 (done, valid. 2026-02-23) | 92 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0524-AZL, TEC-0407-MET |
| S22556 | 2026-02-12 20:25 | Cinthya | FULL | #61770039 | TEXCO/OUT/00498 (done, valid. 2026-02-19) | 54 | Kubera | alta (REF) | número `61770039` recibido en Kubera; piezas cuadran en Kubera: ORG-0277-NEG-2X50, OFI-0075-NEG, TEC-0403-MUL; piezas cuadran en San Corpe: TEC-0403-MUL |
| S22538 | 2026-02-12 14:59 | Administrador | FULL | #61566618 | TEXCO/OUT/00490 (done, valid. 2026-02-21) | 132 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0646-NEG, TEC-0398-PLA, BEB-0025-TEL |
| S22529 | 2026-02-12 13:57 | Thalia | FULL | 61760682 | TEXCO/OUT/00480 (done, valid. 2026-02-14) | 80 | San Corpe | alta (REF) | número `61760682` recibido en San Corpe; piezas cuadran en San Corpe: TEC-0013-MUL, ORG-0269-NEG |
| S22375 | 2026-02-11 10:01 | Thalia | FULL | 61406256 | TEXCO/OUT/00438 (done, valid. 2026-02-19) | 93 | San Corpe | media | piezas cuadran en San Corpe: TEC-0524-AZL |
| S22244 | 2026-02-09 18:40 | Cinthya | FULL | Envío #61566619 | TEXCO/OUT/00382 (done, valid. 2026-02-13) | 105 | Kubera | alta (REF) | número `61566619` recibido en Kubera |
| S21953 | 2026-02-03 20:03 | Cinthya | FULL | 61163498 | TEXCO/OUT/00281 (done, valid. 2026-02-07) | 165 | Kubera | alta (REF) | número `61163498` recibido en Kubera; piezas cuadran en Kubera: TEC-0331-MET |
| S21952 | 2026-02-03 19:21 | Cinthya | FULL | 61157865 | TEXCO/OUT/00279 (done, valid. 2026-02-19) | 50 | — | sin datos | sin coincidencias en la ventana |
| S21951 | 2026-02-03 18:56 | Cinthya | FULL | 61157866 | TEXCO/OUT/00277 (done, valid. 2026-02-11) | 60 | San Corpe | media | piezas cuadran en San Corpe: TEC-0013-MUL |
| S21949 | 2026-02-03 18:40 | Thalia | FULL | 61158159 | TEXCO/OUT/00275 (done, valid. 2026-02-18) | 580 | San Corpe | baja | piezas cuadran en Kubera: TEC-0552-AZL; piezas cuadran en San Corpe: TEC-0393-ROS, TEC-0552-AZL |
| S21948 | 2026-02-03 18:26 | Cinthya | FULL | — | TEXCO/OUT/00274 (done, valid. 2026-02-06) | 20 | Kubera | media | piezas cuadran en Kubera: VEH-0007-VER-NEG |
| S21947 | 2026-02-03 18:21 | Thalia | FULL | 61158160 | TEXCO/OUT/00273 (done, valid. 2026-02-10) | 120 | empate | indeterminada | piezas cuadran en Kubera: TEC-0403-MUL; piezas cuadran en San Corpe: MASC-0044-NEG |
| S21738 | 2026-01-27 14:13 | Thalia | FULL | 60638479 | TEXCO/OUT/00160 (done, valid. 2026-02-03) | 135 | San Corpe | alta (REF) | número `60638479` recibido en San Corpe; piezas cuadran en San Corpe: OFI-0076-NEG |
| S21736 | 2026-01-27 13:45 | Cinthya | FULL | 60642499 | TEXCO/OUT/00159 (done, valid. 2026-02-03) | 40 | Kubera | media | piezas cuadran en Kubera: TEC-0329-NEG |
| S21735 | 2026-01-27 13:24 | Cinthya | FULL | 60642498 | TEXCO/OUT/00158 (done, valid. 2026-02-04) | 80 | Kubera | alta (REF) | número `60642498` recibido en Kubera; piezas cuadran en Kubera: EST-0059-MAD, ORG-0278-BLN |
| S21731 | 2026-01-27 12:25 | Thalia | FULL | 60638480 | TEXCO/OUT/00156 (done, valid. 2026-02-03) | 70 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0107-RO-NE-2CE, TEC-0404-MET |
| S21505 | 2026-01-20 22:12 | Cinthya | FULL | 60142385 | TEXCO/OUT/00061 (done, valid. 2026-01-27) | 120 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0434-AZL, TEC-0434-NEG, TEC-0331-MET |
| S21503 | 2026-01-20 21:46 | Cinthya | FULL | 60142384 | TEXCO/OUT/00060 (done, valid. 2026-01-27) | 60 | Kubera | alta (piezas) | piezas cuadran en Kubera: VEH-0011-AMR, VEH-0010-NEG |
| S21495 | 2026-01-20 16:42 | Thalia | FULL | 60118849 | TEXCO/OUT/00057 (done, valid. 2026-01-27) | 70 | San Corpe | alta (REF) | número `60118849` recibido en San Corpe; piezas cuadran en San Corpe: TEC-0437-NEG |
| S21494 | 2026-01-20 16:20 | Thalia | FULL | 60118848 | TEXCO/OUT/00056 (done, valid. 2026-01-27) | 490 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0011-NEG, TEC-0012-NEG |
| S21187 | 2026-01-14 15:03 | Cinthya | FULL | 59126319 | TEXCO/OUT/00035 (done, valid. 2026-01-27) | 200 | Kubera | alta (piezas) | piezas cuadran en Kubera: TEC-0010-MUL, TEC-0009-PLA, TEC-0008-AMR |
| S21142 | 2026-01-13 16:52 | Thalia | FULL | 59527457 | TEXCO/OUT/00034 (done, valid. 2026-01-27) | 100 | — | sin datos | sin coincidencias en la ventana |
| S21141 | 2026-01-13 16:37 | Thalia | FULL | 59527458 | TEXCO/OUT/00033 (done, valid. 2026-01-27) | 155 | — | sin datos | sin coincidencias en la ventana |
| S21134 | 2026-01-13 13:05 | Thalia | FULL | 59129487 | TEXCO/OUT/00031 (done, valid. 2026-01-16) | 150 | San Corpe | alta (piezas) | piezas cuadran en San Corpe: TEC-0009-PLA, TEC-0010-MUL, TEC-0008-AMR |
| S21132 | 2026-01-13 12:49 | Thalia | FULL | 59129488 | TEXCO/OUT/00032 (done, valid. 2026-01-16) | 279 | San Corpe | alta (REF) | número `59129488` recibido en San Corpe; piezas cuadran en Kubera: TEC-0368-ROS; piezas cuadran en San Corpe: TEC-0368-NEG, TEC-0368-ROS, MES-0007-GRI |

## 5) Órdenes Amazon FBA (socio AMAZON con ≥ 40 piezas)

Cuenta: **San Corpe**, la única de la app (`marketplaceParticipations.storeName`). Prueba de
que son envíos y no ventas: subida de FBA por SKU después de validar (`ops.fanout_log`,
`fba_ingreso_sim`), o `inboundShipped` vivo igual a las piezas de Odoo. El número de envío de
Amazon (`FBA15…`) **no se captura** en Odoo y la app no tiene permiso de Inbound (403).

| Orden | Creada (CDMX) | Creó | Socio | Salida(s) OUT | Piezas hechas (pedidas) | SKUs |
|---|---|---|---|---|---|---|
| S38342 | 2026-09-10 13:36 | Nancy Cruz | AMAZON | TEX2/OUT/00054 (waiting) | 0 (124) | 4 |
| S38241 | 2026-09-09 12:22 | Nancy Cruz | AMAZON | TEXCO/OUT/06277 (waiting) | 0 (211) | 7 |
| S36885 | 2026-08-26 17:29 | Nancy Cruz | AMAZON | TEXCO/OUT/06067 (done, valid. 2026-09-01) | 149 (149) | 3 |
| S34377 | 2026-07-28 18:41 | Nancy Cruz | AMAZON | TEX2/OUT/00028 (done, valid. 2026-08-10) | 496 (496) | 14 |
| S33889 | 2026-07-24 10:19 | Nancy Cruz | AMAZON | TEXCO/OUT/05431 (done, valid. 2026-08-05) | 47 (47) | 3 |
| S33756 | 2026-07-23 12:13 | Nancy Cruz | AMAZON | TEXCO/OUT/05425 (done, valid. 2026-08-08) | 564 (564) | 24 |
| S33669 | 2026-07-22 10:59 | Nancy Cruz | AMAZON | TEX2/OUT/00025 (done, valid. 2026-08-05) | 682 (682) | 24 |
| S33586 | 2026-07-21 12:50 | Nancy Cruz | AMAZON | TEX2/OUT/00019 (done, valid. 2026-07-30) | 186 (186) | 5 |
| S32545 | 2026-07-10 12:49 | Nancy Cruz | AMAZON | TEX2/OUT/00014 (done, valid. 2026-07-30) | 180 (180) | 2 |
| S32544 | 2026-07-10 12:48 | Nancy Cruz | AMAZON | TEX2/OUT/00013 (done, valid. 2026-07-30) | 1664 (1664) | 16 |
| S29717 | 2026-06-01 10:30 | Vale | Amazon | TEXCO/OUT/04469 (done, valid. 2026-06-08) | 157 (157) | 6 |
| S29691 | 2026-06-01 10:00 | Vale | Amazon | TEXCO/OUT/04467 (done, valid. 2026-06-08) | 171 (171) | 5 |

## 6) La única orden Walmart WFS

| Orden | Creada (CDMX) | Creó | Socio | Salida | Piezas | En la API de Walmart |
|---|---|---|---|---|---|---|
| S37676 | 2026-09-03 12:27 | Cinthya | WFS 0029563GDM | TEXCO/OUT/06190 (done, valid. 2026-09-09) | 304 en 22 SKUs | `shipmentId 0029563GDM`, `inboundOrderId KUB-1788454721948`: los mismos 22 SKUs y 304 piezas; llegó el 11-sep, 289 recibidas (`ROP-0257-NEG`: 15 enviadas, 0 recibidas) |

## 7) Lo que NO sale de este sondeo

Las cifras que hoy enseña la pestaña FULLFILMENT (116,895 piezas, 204 órdenes, días de
proceso, 20,342 en FULL…) **no** vienen de aquí: son las del mockup de diseño (14-sep) y no se
re-midieron. Este sondeo cuenta **229** salidas reales a fulfillment (215 FULL + 2 MERCADO
LIBRE + 12 FBA); las cuentas son las de esta tabla.

Datos crudos y scripts: scratchpad de la sesión, `sondeo_fulfillment/`
(`out/veredicto_full_v3.json`, `out/tabla_ordenes_canal.json`, `out/v01_verificacion.json`).
