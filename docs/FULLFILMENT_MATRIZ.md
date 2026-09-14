> **Matriz verificada de la pestaña FULLFILMENT** — anexo de
> [`PROMPT_DISENO_FULLFILMENT.md`](PROMPT_DISENO_FULLFILMENT.md).
>
> Generada el **14-sep-2026** por 6 lecturas independientes del código (ML FULL,
> Amazon FBA, Walmart WFS, Odoo y proceso interno, analítica comercial, sistema de
> diseño del panel). **Cada hallazgo lo revisó un escéptico** que abrió el archivo y
> la línea citados; los veredictos corregidos ya están aplicados. Sólo lectura: no
> se llamó a ninguna API ni se tocó ninguna base al generarla.
>
> Las mediciones en vivo (Odoo, base kubera, API de ML) están en el prompt, no aquí.
> **Las líneas citadas se mueven**: si una no cuadra, busca el nombre de la función.

# Pestaña FULLFILMENT: qué hay en el repo, qué falta y dónde puede mentir la pantalla

Rutas abreviadas: `svc/` = `backend/services/`, `rt/` = `backend/routers/`, `mig/` = `supabase/migrations/`, `fe/` = `frontend/`.
Estados: ✅ existe · 🟡 parcial · 🔌 API sin usar · 🔨 construir · ❔ no se sabe · — no aplica.
La columna **Odoo** cubre el proceso interno: Odoo, la lista de Andy y la validación de Bodega.

## A) Estado por requisito y canal

| # | Requisito | ML FULL | Amazon FBA | Walmart WFS | Odoo | Evidencia clave |
|---|---|---|---|---|---|---|
| 1 | Mockup de la pestaña | 🔨 | 🔨 | 🔨 | — | `fe/components/FulfillmentPendiente.tsx:1-10` (nadie lo usa); no hay ruta |
| 2 | ID de publicación y SKU (MLM / ASIN / wpid) | ✅ | ✅ | 🟡 | — | `mig/0001_esquema_v4.sql:108-124`; `svc/inventario.py:526`; `backend/scripts/cargar_walmart.py:128-143` |
| 3 | Fecha de solicitud | 🔨 | 🟡 | 🔨 | 🔨 | `svc/fanout_stock.py:279-292`; `svc/stock_full.py:511-518`; `svc/odoo.py:933-935` |
| 4 | Fecha en que se fue al marketplace | 🟡 | 🔨 | 🟡 | 🟡 | `svc/odoo.py:1263, 1394-1396`; ninguna llamada a la Inbound API |
| 5 | Cómo arman la orden las KAM (Thalía / Nancy / Cin) | ❔ | ❔ | ❔ | ❔ | `svc/odoo.py:1229, 1244-1245` |
| 6 | Unidades solicitadas | 🟡 | 🟡 | 🔨 | 🟡 | `svc/odoo.py:1223, 1225-1228` |
| 7 | Unidades enviadas | 🟡 | 🟡 | 🟡 | 🟡 | `svc/odoo.py:1265`; `svc/stock_full.py:77`; `svc/fba_reporte.py:111`; `svc/walmart.py:68-229` |
| 8 | SKU que se recibe y cantidad puntual | 🟡 | 🟡 | 🔨 | — | `rt/webhooks.py:411-419`; `svc/fba_reporte.py:112, 126` |
| 9 | Fecha en que FULL / FBA / WFS se prende | 🟡 | 🟡 | 🔨 | — | `mig/0001:149-200`; `svc/inventario.py:521`; `svc/scheduler.py:127-144` |
| 10 | Recibidas contra devueltas o rechazadas | 🟡 | 🔌 | 🔨 | — | `svc/stock_full.py:316-320`; `svc/fba_reporte.py:108` |
| 11 | FULL PRICE | ❔ | ❔ | ❔ | — | `fe/app/analisis/page.tsx:1192-1193`; `svc/pedidos_amazon.py:81`; `docs/WALMART_MX_MANUAL.md:225` |
| 12 | Fecha de primera venta | 🟡 | 🟡 | 🟡 | — | `rt/fulfillment.py:509`; `mig/0007_fulfillment_vistas.sql:46`; `backend/config.py:183-184` |
| 13 | Visitas a primera venta (disparador) | 🔨 | 🔌 | ❔ | — | `svc/pedidos_ml.py:770, 801`; `svc/competencia_ml.py:347-358` |
| 13-H | Visitas a primera venta de ventas ya ocurridas | 🔌 | 🔌 | ❔ | — | `svc/competencia_ml.py:361-376`; `README.md:9570-9573` |
| 14 | Sondeo de activas e inactivas sin venta | 🟡 | 🟡 | 🔨 | — | `rt/fulfillment.py:376-381, 213` |
| 15a | Calificación del listing | 🔌 | 🔌 | ❔ | — | `svc/inventario.py:174-188`; `svc/amazon.py:339-345` |
| 15b | Calificación de operaciones | 🔌 | 🔌 | ❔ | — | `svc/ventas_ml.py:103-106` |
| 16a | Visitas | 🟡 | 🔌 | ❔ | — | `backend/scripts/competencia_visitas.py:114-128` |
| 16b | Ventas | ✅ | ✅ | 🟡 | — | `mig/0007:37-46`; `backend/config.py:183-184` |
| 16c | Inventario (en el marketplace y propio) | ✅ | ✅ | ❔ | 🟡 | `rt/fulfillment.py:2178-2287`; `svc/fba_reporte.py:125-137`; `svc/walmart_panel.py:78-80`; `svc/odoo_ventas.py:80-82` |
| 16d | Ranking de inventario | 🟡 | 🔌 | ❔ | — | `rt/fulfillment.py:502-538` |
| JE-1 | Lista de Andy (martes) | 🔨 | 🔨 | 🔨 | — | grep `Andy\|meli_bekura\|meli_sank` sin resultados; `mig/0007:139-145` |
| JE-2 | Validación de Bodega (martes) | — | — | — | 🔨 | `svc/odoo_ventas.py:298-391`; `svc/inventario_maestro.py:436-554` (valida otra cosa) |
| JE-3a | Leer la orden de salida ya validada | — | — | — | 🟡 | `svc/odoo.py:1219-1233` |
| JE-3b | Generar la orden de salida desde el panel | — | — | — | 🔨 | `svc/odoo_ventas.py:611` (la única escritura es para TikTok y Temu) |
| JE-3c | Cargar el envío al marketplace | 🔨 | 🔨 | 🔨 | — | no hay código |
| XLS-1 | Sugerido de resurtido | 🟡 | 🟡 | 🔨 | — | `mig/0007:53-154`; `rt/fba.py:180` |
| XLS-2 | Cambio de precio: fecha y ventas | 🟡 | 🟡 | 🔨 | — | `mig/0001:172-195`; `rt/fulfillment.py:850-863`; `cargar_walmart.py:129-139` |
| E-1 | Envíos inbound e histórico (backfill) | 🔌 | 🔌 | ❔ | 🟡 | `README.md:11433-11436`; `mig/0050_retencion_webhooks_por_canal.sql:54-55`; `svc/odoo.py:1199-1277` |
| E-2 | Orden de salida abierta (sin validar) | — | — | — | 🔌 | `svc/odoo.py:921-938, 1325` |
| E-3 | Reparto propio / FULL del user product (stock-locations) | 🔌 | — | — | — | `rt/webhooks.py:420-432` |
| E-4 | Días en que se procesan los envíos | 🟡 | 🔨 | 🔨 | 🔨 | `svc/odoo.py:1263`; `mig/0028_ops_fanout_log.sql:64` |
| E-5 | Cajas y piezas por envío | 🔨 | 🔌 | ❔ | 🟡 | `svc/inventario_maestro.py:279-282, 1100-1113` |
| E-6 | Tasa de éxito validado / enviado | — | — | — | 🔨 | `svc/odoo_ventas_log.py:254-289` (molde) |
| E-7 | Reajuste con IA o edición de Andy | — | — | — | 🔨 | `svc/ia_generadores.py:34-82` |
| E-8 | Destino (meli_bekura, meli_sank, fba, wfs, drop) | 🟡 | 🟡 | 🟡 | 🟡 | `svc/odoo.py:1336`; `mig/0001:754-758` |
| E-9 | Venta surtida por el marketplace o por bodega propia | ✅ | ✅ | 🟡 | — | `svc/meli.py:377-423`; `svc/pedidos_amazon.py:67`; `svc/pedidos_walmart.py:120-130` |
| E-10 | ¿Vigilante de FULL / FBA encendido en producción? | ❔ | ❔ | — | — | `backend/config.py:626, 632`; `docs/PASO_0_CANDADOS.md:133` |
| E-11 | Conversiones a WFS (feeds OMNI_WFSCONVERT) | — | — | 🔌 | — | `svc/pedidos_walmart.py:169-171`; `backend/scripts/estado_walmart.py:66-82` |
| **Panel** | **Piezas de diseño que ya usa el panel** | | | | | |
| E-12 | Colores por canal | ✅ | ✅ | ✅ | — | `backend/core/marketplaces.py:47-127`; `fe/lib/theme.ts:16-24` |
| E-13 | Colores y nombres por cuenta | 🟡 | — | — | — | `fe/lib/canales.ts:11-26` |
| E-14 | "Sin registro" pintado distinto de cero | ✅ | ✅ | ✅ | — | `fe/app/monitoreo/page.tsx:24-38, 314-351` |
| E-15 | Estados de carga, error y vacío | ✅ | ✅ | ✅ | — | `fe/app/monitoreo/page.tsx:1327-1436` |
| E-16 | KPI y progreso contra meta | ✅ | ✅ | ✅ | — | `fe/app/monitoreo/page.tsx:579-734`; sin meta fijada (`svc/monitoreo.py:299`) |
| E-17 | Chips y filtros | ✅ | ✅ | ✅ | — | `fe/app/inventario/page.tsx:489-538`; `fe/app/automatizacion/page.tsx:1110-1136` |
| E-18 | Barra de etapas solicitud → recepción → activación | 🟡 | 🟡 | 🟡 | — | `fe/app/inventario/page.tsx:1486-1594` |
| E-19 | Error crudo del canal | ✅ | ✅ | ✅ | — | `fe/app/monitoreo/page.tsx:954-981, 1126-1166` |
| E-20 | Registro en el menú de navegación | 🔨 | 🔨 | 🔨 | — | `fe/components/AppNavbar.tsx:71-157` |
| E-21 | Permisos por rol (RBAC) | 🔨 | 🔨 | 🔨 | — | `backend/core/rbac.py:136, 227, 233-234` |
| E-22 | Ver contra mover | ✅ | ✅ | ✅ | — | `backend/core/rbac.py:105-123`; `fe/app/inventario/page.tsx:592-628` |
| E-23 | Pantallas FULL / FBA que ya existen | 🟡 | 🟡 | 🔨 | — | `fe/app/analisis/fba/page.tsx:1-15`; `svc/inventario_maestro.py:589-590` |
| E-24 | Lenguaje de la UI | ✅ | ✅ | ✅ | — | `fe/app/monitoreo/page.tsx:142-149, 292-303`; `svc/monitoreo.py:301-320` |
| E-25 | Identidad y lienzo | ✅ | ✅ | ✅ | — | `fe/app/globals.css:19-24`; `fe/package.json:11-15` |

## B) Cómo se resuelve cada 🟡 / 🔌 / 🔨

- **1 🔨**
  - Página nueva `fe/app/<ruta>/page.tsx` con AppNavbar y fetchSesion (`fe/lib/api.ts:136-143`).
  - Se puede publicar por etapas con `FulfillmentPendiente` (`fe/components/FulfillmentPendiente.tsx:1-10`).
  - Hay que decidir si absorbe o enlaza `/analisis/fba` (`fe/app/analisis/fba/page.tsx:1-15`).
  - Ni la ruta ni el rótulo pueden ser "Fulfillment": chocan con `/api/fulfillment` de Análisis (`rt/fulfillment.py:64`) y con la sub-pestaña retirada (`fe/app/analisis/rentabilidad/page.tsx:6-8`).
- **2 WFS 🟡:** el wpid solo lo escribe `backend/scripts/cargar_walmart.py:128-143`, un script que se corre a mano y en dry-run por omisión (`:23-24`). La venta trae offerId (`svc/pedidos_walmart.py:132`), que no casa con el wpid: el cruce va por SKU.
- **3**
  - **ML 🔨:** la fecha de solicitud sale de `created_at` de la tabla nueva de JE-1. La fecha de la orden en Odoo sale de `stock.picking` OUT al socio FULL, clonando `svc/odoo.py:920-999` con `picking_code='outgoing'`. El "omitir por FULL" del fan-out no es una solicitud (`svc/fanout_stock.py:279-292`).
  - **FBA 🟡:** ya existe `fba_ingreso_sim` con ts (`svc/stock_full.py:511-518`). Salta al declarar el plan (`docs/WEBHOOKS_POR_CANAL.md:149`). Hay que filtrar con `accion LIKE 'fba_%'`, no por canal (`svc/stock_full.py:218, 230`). La fuente firme es listInboundPlans, con el token de `svc/amazon.py:218-241`.
  - **WFS 🔨:** solo la tabla de solicitudes. El fan-out no lee Walmart (`svc/fanout_stock.py:684-693`; `svc/channel_read.py:43`).
  - **Odoo 🔨:** la lista de Andy es construcción pura. La otra mitad, el `create_date` del OUT, es 🔌 con el mismo cliente (`svc/odoo.py:36-37`) y los mismos campos que ya se leen para recepciones (`:978-980`). No usar `scheduled_date`: en recepciones difiere hasta tres meses (`svc/odoo.py:908-912`).
- **4**
  - **ML, WFS y Odoo 🟡:**
    - Tomar `date_done` del picking OUT (hoy solo se lee en `svc/odoo.py:1044-1045` para compras) o `stock.move.date` en done (`:1263`), filtrado por `partner_id` del socio.
    - Hace falta una consulta nueva por socio para todo el catálogo: la actual tarda ~1 s por SKU (`rt/inventario.py:116-117`).
    - Rotular siempre "validación de salida en Odoo (aprox.)"; la recolección real solo se tiene capturándola a mano.
    - Del socio WALMART no hay ni una medición (`svc/odoo.py:1244-1245`).
  - **FBA 🔨:** sondear getShipments de la Inbound API v0 y guardar en una tabla propia (p. ej. `ops.fba_shipment_estado`) nuestro timestamp en cada cambio de estado.
- **6**
  - **ML, FBA y Odoo 🟡:**
    - Tomar `product_qty` (`svc/odoo.py:1225-1228`) de los OUT al socio, incluidos los estados abiertos, sumado por picking y SKU.
    - Sumar dos cifras nuevas capturadas en el panel: lo que pidió Andy y lo que validó Bodega.
    - En FBA, además, listInboundPlanItems o getShipmentItemsByShipmentId. `inbound_working` (`svc/fba_reporte.py:110`) es un saldo sin shipment.
  - **WFS 🔨:** tabla de solicitudes; `product_qty` de Odoo solo como aproximación posterior.
- **7**
  - **ML 🟡:** enviadas = Σ `quantity` de los OUT a FULL (`svc/odoo.py:1265`). Recibiendo = `INBOUND_RECEPTION` en `ops.fanout_log`, con un regex nuevo sobre `x(\d+)` y deduplicado por item_id (`svc/stock_full.py:384-389`).
  - **FBA 🟡:** getShipmentItemsByShipmentId (QuantityShipped, QuantityInCase), cruzado con Odoo por SKU y fecha.
  - **WFS 🟡:** el lado Odoo, igual que en ML. El lado Walmart va rotulado "sin fuente verificada en MX": hay que medir `/v3/fulfillment/inbound-shipments` con un envío real por `svc/walmart.py:55-84`.
  - **Odoo 🟡:** separar por `contraparte`, que ya viaja en la respuesta (`svc/inventario_maestro.py:708, 798`).
- **8**
  - **ML 🟡:** escritor nuevo en `procesar_operacion` (`svc/stock_full.py:300-330`) que guarde en una tabla propia operation_id, inventory_id, inbound_id, sku, cuenta, tipo, cantidad, result.total y la fecha de la operación.
  - **FBA 🟡:** eventos Receipts del Inventory Ledger `GET_LEDGER_DETAIL_VIEW_DATA`, por el ciclo de reportes que ya funciona (`svc/fba_reporte.py:169-223`).
  - **WFS 🔨:** inbound-shipment-items si MX lo ofrece; si no, importar el reporte de Seller Center a una tabla `ops.*` nueva.
- **9**
  - **ML 🟡:** primer `changed_at` de `channel.listing_history` después del envío (is_fulfillment de false a true, situacion active o stock_full desde 0; `mig/0001:149-200`). Más exacto: el primer INBOUND_RECEPTION del inventory_id.
  - **FBA 🟡:** `min(changed_at)` con canal amazon y stock_full de 0 a >0. Fuente firme: el primer Receipts del Ledger, o historia diaria de `ops.fba_snapshot`.
  - **WFS 🔨:** tres fuentes, ninguna conectada:
    - Feeds OMNI_WFSCONVERT: fecha de envío del feed y estado por SKU con `feed_estado` (`svc/walmart.py:183-229`), sin fecha de aceptación.
    - Un sync que marque `is_fulfillment` y deje fechar al trigger (`mig/0001:184-186`).
    - La primera venta con isWFSEnabled='Y', como cota superior.
- **10**
  - **ML 🟡:** recibidas = INBOUND_RECEPTION deduplicado. Para las rechazadas, guardar el `detail` y el desglose de `/inventories/{id}/stock/fulfillment`, que hoy se descarga y se tira (`svc/stock_full.py:316-320, 338`).
  - **FBA 🔌:** getShipmentItemsByShipmentId (enviadas − recibidas al quedar CLOSED), el Ledger y los reportes de retiros `GET_FBA_FULFILLMENT_REMOVAL_ORDER_DETAIL_DATA` / `_SHIPMENT_DETAIL_DATA`. Todo con `svc/amazon.py:218-241`.
  - **WFS 🔨:** recibidas y discrepancias de WFS (si MX las expone) o Seller Center, con la diferencia contra Odoo en una tabla `ops.*` nueva.
- **12**
  - **ML 🟡:** `min(date)` de `sales_daily_completa` por cuenta, item_id y sku con `is_full` (`mig/0007:37-46`). Con hora exacta: `channel.orders.creado_at` más `order_items.es_fulfillment`, pero solo desde el 15/16-jul (`rt/fulfillment.py:274-285`).
  - **FBA 🟡:** leer `channel.orders` directo, no la vista (`mig/0007:46`), y cruzar por SKU (`svc/pedidos_amazon.py:73`).
  - **WFS 🟡:** la misma consulta, solo si `PEDIDOS_WALMART_SONDEO_ENABLED=true` y `PEDIDOS_WALMART_SOLO_REGISTRO=false` (`backend/config.py:183-184`; `svc/pedidos_walmart.py:216-223`).
- **13**
  - **ML 🔨:**
    - El gancho va en `pedidos_ml.sincronizar` con `accion=="creado"`, después de `_registrar` (`svc/pedidos_ml.py:724`).
    - Si es la primera línea del item, llamar en `to_thread` a `competencia_ml.visitas(item_id, 'bekura'|'sancorfashion')` (`svc/competencia_ml.py:347-358`; usa requests y sleep, `:187, 210`).
    - Guardar en una tabla nueva y volver a leer al día siguiente, porque las visitas son un acumulado diario (`svc/visitas_ml.py:15-16`).
    - No reusar `visitas_ml.completar`: llama a MySQL de forma síncrona (`svc/visitas_ml.py:57, 89-90, 110`).
  - **FBA 🔌:** reporte Sales and Traffic por el ciclo de `svc/fba_reporte.py:169-223`. Es diario por ASIN y el rol de la app no está verificado.
- **13-H**
  - **ML 🔌:** con `visitas_serie` (`svc/competencia_ml.py:361-376`), sumar desde `date_published` (`mig/0031_channel_listings_date_published.sql:13-23`) hasta la primera venta. Nunca se ha pedido más de 60 días.
  - **FBA 🔌:** el mismo reporte Sales and Traffic.
- **14**
  - **ML 🟡:** `channel.listings` (active/paused) LEFT JOIN `sales_daily_completa` por item_id y cuenta, en {activa, pausada} × {con venta, sin venta, nunca}. Faltan el job y un destino (campana o tabla). No filtrar por `estado`, donde no_venta gana a activa (`rt/fulfillment.py:625-637`).
  - **FBA 🟡:** el estado de la publicación sale de `situacion` (`rt/fulfillment.py:199-208`), no del semáforo de `rt/fba.py:168`.
  - **WFS 🔨:** `walmart_panel.listar` (`svc/walmart_panel.py:113-124`) más ventas por SKU.
- **15a**
  - **ML 🔌:** endpoint de calidad de la publicación con `competencia_ml._get` (`svc/competencia_ml.py:162-218`). Probarlo antes: varios endpoints dan 403 con token válido (`supabase/propuestas/competencia_arquitectura.md:99-108`). Si lo que quiere Brandon son las estrellas del comprador, `reviews` ya existe (`svc/competencia_ml.py:652-658`).
  - **FBA 🔌:** ampliar `includedData` de Listings Items (`svc/amazon.py:339-345`).
- **15b**
  - **ML 🔌:** leer `seller_reputation` del `/users/me` que ya se llama (`svc/ventas_ml.py:103-106`). Es por cuenta, no por fila de producto.
  - **FBA 🔌:** reporte de desempeño por la Reports API (`svc/fba_reporte.py:39-41`).
- **16a**
  - **ML 🟡:** `distinct on (sku,cuenta)` de `enrich.market_listing_metrics` (`rt/metricas.py:116-121`). No hay serie diaria.
  - **FBA 🔌:** Sales and Traffic (ver fila 13).
- **16b WFS 🟡:** depende de los flags del sondeo (`backend/config.py:183-184`).
- **16c Odoo 🟡:** `ubicaciones_por_sku` y `skus_por_almacen` (`svc/odoo.py:622-686, 297-364`). `libre_por_almacen` no mide DROP OFF (`svc/odoo_ventas.py:80-82`).
- **16d**
  - **ML 🟡:** Brandon tiene que elegir cuál ranking quiere:
    - por ventas: Estrellas (`rt/fulfillment.py:502-538`);
    - por posición en ML: `backend/scripts/competencia_highlights.py:283-291`;
    - por cobertura: `restock_panel` (`mig/0007:139-154`).
  - **FBA 🔌:** `salesRanks` de la Catalog Items API con `svc/amazon.py:218`, guardado como serie diaria.
- **JE-1 🔨:**
  - Tabla nueva en kubera: lista y renglones con SKU, destino, unidades, actor y created_at.
  - Copiar el molde del código `svc/odoo_ventas_log.py:63, 115`, no el de `mig/0033`, que está desfasado.
  - Pre-llenado por destino: ML con `restock_panel.sugerido_full` (`mig/0007:143`), FBA con `rt/fba.py:180`; WFS no tiene semilla.
  - Andy no tiene cuenta en el panel (`backend/scripts/crear_usuarios.py:76-99`).
- **JE-2 🔨:**
  - Encadenar `productos_por_sku` → `libre_por_almacen` → `planear_almacenes` (`svc/odoo_ventas.py:286-391`), en `to_thread` (`:51-55`), para precalcular cabe / parcial / no cabe.
  - Guardar quién validó, cuándo y cuántas unidades (patrón de `mig/0033:115-121`).
  - Si FULL puede salir de DROP OFF, hace falta otra lectura.
- **JE-3a 🟡:**
  - Leer `stock.picking` outgoing con socio FULL, AMAZON o WALMART (name, state, create_date, create_uid, scheduled_date, date_done, origin) y sus `stock.move`, con el cliente de `svc/odoo.py:36-37`.
  - Agrupar por picking OUT sin sumar PICK/PACK (`svc/odoo.py:1369-1372`).
- **JE-3b 🔨:**
  - Hoy no hay escritura de `stock.picking`.
  - Es un flujo vivo: la salida mueve free_qty, stock_watch lo copia a Woo y el fan-out lo empuja a los canales DROP.
  - Va con `BotonBloqueado` (`fe/app/inventario/page.tsx:615-629`) hasta el dale de Brandon.
- **JE-3c 🔨:** "Cargar el FULL a Meli" no tiene código, y tampoco hay nada equivalente para FBA o WFS. Mismo trato que JE-3b.
- **XLS-1**
  - **ML 🟡:** `channel.restock_panel` (`mig/0007:53-154`), rotulado como sugerido PROPIO. Si "sugerido meli" es la cifra del centro de vendedores de ML, hay que confirmarlo fuera del código.
  - **FBA 🟡:** el plan de `rt/fba.py:180`, no `restock_panel`: en Amazon `stock_full` viene NULL (`svc/channel_read.py:51-53`).
  - **WFS 🔨:** no hay sugerido en ningún lado.
- **XLS-2**
  - **ML y FBA 🟡:** por cada `campo='price'` de `listing_history`, comparar ventas N días antes y después. Es más fiel detectar escalones en `order_items.precio_unitario`, porque `price_sale` no se audita (`svc/precios_venta.py:167-169`).
  - **WFS 🔨:** programar `cargar_walmart.py --aplicar` (`:133-139`). El trigger ya solo registra cuando el precio cambia (`mig/0001:172-175`).
- **E-1**
  - **ML 🔌:** script de solo lectura por cuenta que pagine `/stock/fulfillment/operations/search` y resuelva el SKU con `svc/stock_full.py:313-327`.
  - **FBA 🔌:** servicio calcado de `fba_reporte.py` con getShipments. Arreglo barato inmediato: conservar `inventoryDetails.inbound*`, que ya llega y se descarta (`svc/inventario.py:458, 471`); ojo con el tope de 10 páginas (`:461`).
  - **Odoo 🟡:** la historia por SKU existe (`svc/odoo.py:1199-1277`); la versión por socio hay que construirla.
- **E-2 🔌:** gemela de `recepciones_pendientes_por_sku` (`svc/odoo.py:921-938`) con `outgoing` y filtro por socio. En pendientes, la demanda es `product_qty` (`:950-953`).
- **E-3 🔌:** GET `/user-products/{id}/stock` dentro de `rt/webhooks.py:420-432`. Hace falta una columna nueva para `user_product_id`, que no existe en `mig/0001:108-124`.
- **E-4 (ML 🟡 / resto 🔨):**
  - Día de la semana en hora de México (reusar `_SEMANA_TS`, `svc/monitoreo.py:301-320`).
  - Se calcula sobre la solicitud, sobre `create_date` y `date_done` del OUT filtrado por socio, y en ML sobre el ts de INBOUND_RECEPTION.
- **E-5**
  - **Odoo 🟡:** piezas ÷ `units_per_master_box` con `_cajas` (`svc/inventario_maestro.py:1100-1113`), corrigiendo antes el caso factor 1 (`:1111`) y rotulado "derivado".
  - **ML 🔨:** no hay conteo de cajas de envío a FULL.
  - **FBA 🔌:** listShipmentBoxes (verificar en la documentación de SP-API).
- **E-6 🔨:**
  - Tasa de validado = validadas ÷ solicitadas; tasa de enviado = hechas ÷ validadas.
  - Guardar el nombre del picking OUT en cada renglón: después no se puede deducir.
  - Formato "38 / 45" de `CeldaProceso` (`fe/app/monitoreo/page.tsx:329-351`); no hay meta definida.
- **E-7 🔨:** primero el reajuste determinista (`planear_almacenes`); luego `_completar` (`svc/ia_generadores.py:34-82`) en `to_thread` y con temperatura menor a 0.7 (`:49`). Guardar la lista original, la propuesta y la final.
- **E-8 🟡:**
  - Guardar el destino explícito en la lista, ligado a `core.accounts` (`mig/0001:754-758`).
  - Medir con SELECT si el socio o el `origin` de Odoo distinguen cuenta, y si "MERCADO LIBRE" es el socio de las ventas de meli_oerp (`svc/odoo_ventas.py:6-7`).
  - La cuenta Walmart la crea `cargar_walmart.py:97-107` a mano.
  - Un traspaso a drop ya se clasifica por `warehouse_id` (`svc/odoo.py:1381-1382`).
- **E-9 WFS 🟡:** leer `fulfillmentType` además de `isWFSEnabled` (`svc/pedidos_walmart.py:10-11, 129-130`). Cambia si se descuenta bodega: requiere dale de Brandon.
- **E-11 🔌:** `/v3/feeds` leyendo `feedType` (`backend/scripts/estado_walmart.py:66-82`) y `feed_estado` por SKU, que topa en 50 artículos (`svc/walmart.py:192-194, 228`).
- **E-13 🟡:** elegir UNA sola forma de nombrar las cuentas; hoy conviven `fe/lib/canales.ts:24`, `fe/app/analisis/estrellas/page.tsx:67-68` y `fe/app/monitoreo/page.tsx:253`. No pintar FBA en sky, que ya es BEKURA (`canales.ts:12`).
- **E-18 🟡:** copiar `Recorrido` (función local, `fe/app/inventario/page.tsx:1505`) para Solicitadas → Validadas → Enviadas → Recibidas → Activa → 1ª venta. El paso sin fuente va rayado (`fe/app/monitoreo/page.tsx:314-317`).
- **E-20 🔨:** entrada beta junto a Inventario (`fe/components/AppNavbar.tsx:103`) o dentro del submenú de Análisis junto a Amazon FBA (`:89-90`). Cuidar el `startsWith` que marca la pestaña activa (`:255-256`).
- **E-21 🔨:**
  - Prefijo propio, nunca `/api/fulfillment*`: heredaría el GET de operador (`backend/core/rbac.py:136, 233-234`).
  - GET en lectura si solo trae fechas y piezas; en operador si trae costos o full price.
  - POST de la lista y de la validación: operador. POST de la orden de salida o de la carga al marketplace: admin.
  - Agregar la llave en `puede` (`rt/auth.py:49-56`).
- **E-23**
  - **ML y FBA 🟡:** separar ML de FBA, que hoy se suman (`svc/channel_mirror.py:93`; `rt/fulfillment.py:155, 213`), y absorber o enlazar `/analisis/fba`.
  - **WFS 🔨:** nada escribe `is_fulfillment` de Walmart (`svc/channel_mirror.py:118`).

**Cómo se resuelven las filas ❔:**
- **Fila 5:** entrevista con las KAM, más un SELECT en Odoo agrupando salidas por `partner_id`, `picking_type_id`, `create_uid` y prefijo de `origin`.
- **Fila 11:** Brandon tiene que definir qué es.
  - Si es el almacenaje FULL, está en la facturación de ML, que el código no consume.
  - Si es el precio de la publicación FULL, ya está en `channel.listings.price`.
  - Amazon y Walmart registran la comisión en 0 (`svc/pedidos_amazon.py:81`; `svc/pedidos_walmart.py:141-143`).
- **Walmart en 13, 15 y 16:** el cliente solo usa token, items, orders y feeds (`svc/walmart.py:78, 103, 162, 198`). Hay que sondear la API de MX.
- **16c WFS:** leer `/v3/inventory?sku=` o `INVENTORY_MX` sobre un SKU ya convertido. Si da stock WFS, guardarlo en `stock_wfs`, nunca en `stock_full`.
- **E-10:** `SELECT max(ts), count(*) FROM ops.fanout_log WHERE accion LIKE 'full_%'` (o `'fba_%'`), o revisar las variables de Railway.

## C) Trampas que harían mentir a la pantalla

1. **"Envío a FULL" en Odoo no significa envío a FULL.**
   - `envio_full` se decide por subcadena del nombre del socio: FULL, AMAZON, TIKTOK, TEMU, SHEIN, MERCADO LIBRE o WALMART (`svc/odoo.py:1336, 1394-1396`).
   - Automatización crea órdenes con socios TikTok y Temu (`svc/odoo_ventas.py:73`). Medidos: shein 886, tiktokshop 162, temu 41 movimientos (`svc/odoo.py:1244-1245`).
   - Tampoco separa BEKURA de SANCORFASHION.
2. **Las fechas de Odoo no son la recolección y no están en hora de México.**
   - `date` en done es cuando se validó el picking (`svc/odoo.py:1263`).
   - Llega en UTC sin zona (`svc/inventario_maestro.py:1140-1150`), así que un martes después de las 18 h cae en miércoles (precedente en `rt/fulfillment.py:112-120`).
   - Solo se leen movimientos done (`svc/odoo.py:1223`): la orden del miércoles que no se ha validado no existe.
3. **"Stock FULL" hoy mezcla ML con FBA, y hay tres "stock FBA" distintos.**
   - Para Amazon, `stock_fba` se escribe en `stock_full` (`svc/channel_mirror.py:93`), y Análisis suma ambos (`rt/fulfillment.py:155, 213`).
   - `/analisis/fba` rotula "Declarado en FULL" (`fe/app/analisis/fba/page.tsx:235`).
   - El reporte, la Listings API y la marca de agua difieren en 96 de 99 SKUs (`mig/0022:113-125`).
4. **Hay "ingresos" que no son ingresos.**
   - Sumar TRANSFER_* da −329 piezas fantasma (`svc/stock_full.py:78-83`; `README.md:11785-11799`).
   - `fba_ingreso_sim` salta con el plan declarado (`docs/WEBHOOKS_POR_CANAL.md:149`) y con devoluciones de clientes.
   - En solo-registro, `stock_drop` es stock de Woo, no piezas recibidas (`svc/stock_full.py:385-389`).
5. **Enviadas − recibidas no es un rechazo.**
   - Mientras el marketplace recibe, la diferencia es trabajo pendiente. Inventario ya prohíbe esa resta (`fe/app/inventario/page.tsx:1486-1594`).
   - La etapa "Enviado" de Inventario significa "tiene publicación fulfillment", no "se envió" (`svc/inventario_maestro.py:583-594`).
6. **El grano por publicación está roto.**
   - La PK permite un solo MLM por SKU por cuenta (`mig/0001:123`; `svc/channel_mirror.py:110-111`).
   - Padre e hijo duplicaban 2,024 piezas (`rt/fulfillment.py:2193-2201`).
   - `listing_history` no guarda listing_id (`docs/ARNESES.md:119-121`), solo dispara en UPDATE (`mig/0001:199-200`) y fecha cuándo se OBSERVÓ el cambio (lote de 80 cada 15 min, `backend/config.py:342-343`).
7. **Tomar "solicitado" de Odoo pone la tasa de validado en 100%.**
   - `product_qty` sale de la orden del miércoles, que ya trae el recorte de Bodega; la lista de Andy no deja rastro.
   - `product_qty` y `quantity` difieren en el 6.2% de los movimientos (`svc/inventario_maestro.py:804-806`).
   - Sumar PICK → PACK → OUT triplica la demanda (`svc/odoo.py:1369-1372`).
8. **La primera venta puede salir falsa.**
   - El archivo histórico empieza el 27-dic-2025 (`mig/0006_analytics_dailytrack_hist.sql:43`).
   - La vista viva empieza el 16-jul, así que Amazon sale tarde (`mig/0007:46`).
   - En Amazon el `item_id` es OrderItemId (`svc/pedidos_amazon.py:73`).
   - En ML, `es_fulfillment` de orders y de order_items discrepa en el 40% (`svc/devoluciones_ml.py:40-42`).
   - Una orden sin fecha queda con la hora de proceso (`svc/pedidos_ml.py:544-550`).
9. **WFS se estaría pintando sobre suposiciones.**
   - Nada escribe `is_fulfillment` de Walmart (`backend/scripts/cargar_walmart.py:128-143`).
   - La venta WFS se decide con `isWFSEnabled`, que nunca se ha visto en 'Y'; lo que sí se midió fue `fulfillmentType` S2H (`svc/pedidos_walmart.py:10-11, 129-130`).
   - Los 404 de MX se midieron con la cuenta vacía (`docs/WALMART_MX_HALLAZGOS.md:36`; `docs/WALMART_MX_MANUAL.md:250`).
   - El comentario del fan-out que habla de WFS no tiene código detrás (`svc/fanout_stock.py:684-693`).
10. **Cajas inventadas.**
    - `_cajas` con factor 1 devuelve cajas = piezas, contra lo que dice su docstring (`svc/inventario_maestro.py:1103-1111`).
    - Las cajas de `packing_cajas` son cartones de importación, no cajas enviadas a FULL (`svc/packing_cajas.py:3-12`).
    - Odoo no usa paquetes (`svc/inventario_maestro.py:279-282`).

## D) Piezas reutilizables confirmadas

| Pieza | Archivo | Qué da |
|---|---|---|
| `procesar_operacion` y tabla de tipos | `svc/stock_full.py:280-405, 73-98` | Operación FULL → tipo, cantidad y SKU vía inventory → item → SELLER_SKU |
| Webhook `fbm_stock_operations` | `rt/webhooks.py:411-419, 178-182` | Aviso en segundos, con la cuenta sacada del user_id |
| `ops.fanout_log` y `/api/fanout/full/observacion` | `svc/fanout_read.py:120-148`; `rt/fanout.py:36-79` | Bitácora de operaciones FULL (y FBA) con ts de proceso |
| `sincronizar_ml` / `refrescar_ml_item_id` | `svc/inventario.py:417-436, 666-741` | stock_full, is_fulfillment, situacion y date_created por MLM y cuenta |
| Trigger `fn_listing_history` | `mig/0001:149-200` | Antes y después de price, stock_full, is_fulfillment y situacion, con changed_at |
| `_SQL_INV_BASE` | `rt/fulfillment.py:2178-2287` | Stock FULL por SKU sin duplicar, con `full_bk` y `full_sc` |
| `channel.restock_panel` | `mig/0007:53-161` | Sugerido propio de resurtido por listing y cuenta (ML y Amazon) |
| `publicaciones_ml` | `svc/channel_read.py:482-506` | `{sku: [{cuenta, item_id, url, situacion}]}` |
| Ciclo de reportes y `ops.fba_snapshot` | `svc/fba_reporte.py:39-55, 125-137, 169-223` | Inventario FBA con inbound; ciclo reusable para Ledger y retiros |
| `GET /api/fba` | `rt/fba.py:134-244` | Tablero FBA con cobertura y plan de envío |
| Token LWA y Listings Items | `svc/amazon.py:218-241, 339-345` | Puerta de entrada a la Inbound API y a la calidad del listing |
| `revisar_fba` | `svc/stock_full.py:410-532` | Evento con fecha cuando sube totalQuantity |
| Pedidos AFN / MFN | `svc/pedidos_amazon.py:67, 89-99` | Venta FBA (protegida) o surtida por nosotros |
| Cliente Walmart MX | `svc/walmart.py:55-84, 183-229` | Token, cabeceras MX y veredicto de feed por SKU |
| `_normalizar` de Walmart | `svc/pedidos_walmart.py:110-181` | Marca WFS / S2H por pedido |
| `movimientos_por_sku` y `_causa` | `svc/odoo.py:1199-1398` | Libro de bodega done con pedido, hecho, contraparte y causa |
| `recepciones_pendientes_por_sku` | `svc/odoo.py:894-999` | Molde para leer salidas abiertas |
| `libre_por_almacen` y `planear_almacenes` | `svc/odoo_ventas.py:298-391` | free_qty por almacén y plan de surtido (prevalidación de Bodega) |
| `odoo_ventas_log` | `svc/odoo_ventas_log.py:38-293` | Molde de bitácora encabezado+renglones y conteo a 30 días |
| `_completar` | `svc/ia_generadores.py:34-82` | DeepSeek con Claude de respaldo (síncrono: va en `to_thread`) |
| `channel.sales_daily_completa` | `mig/0007:37-46` | Ventas por día, cuenta, item_id, sku e is_full |
| `/api/fulfillment/estrellas` | `rt/fulfillment.py:502-596` | Primera y última venta por SKU, con Pareto |
| `visitas` / `visitas_serie` / `reviews` | `svc/competencia_ml.py:347-376, 652-658` | Visitas acumuladas, serie diaria y rating por MLM |
| Gancho post-venta | `svc/pedidos_ml.py:763-775, 790-805` | Lugar para el disparador de visitas |
| `/users/me` | `svc/ventas_ml.py:100-109` | Ya autenticado por cuenta; ahí viene seller_reputation |
| `RAYADO`, `ChipSinRegistro`, `Leyenda`, `CeldaProceso`, `ErrorDeCarga`, `Vacio`, `CajaError`, `BandaMetas` | `fe/app/monitoreo/page.tsx:313-351, 579-734, 954-981, 1126-1166, 1327-1436` | "No sabemos" distinto de cero, los tres estados y el error crudo del canal (locales: hay que copiarlas) |
| `Recorrido`, `BotonBloqueado`, `Almacen` | `fe/app/inventario/page.tsx:1486-1594, 615-629, 71-91` | Barra de etapas, acción de escritura bloqueada y chip de almacén (locales) |
| `Escalon`, `puedeMover`, confirmación; "compra → proceso" | `fe/app/automatizacion/page.tsx:326-361, 924-944, 1253-1329, 229-239` | Pasos informativos, ver contra mover y fechas con rezago |
| Cobertura `completa\|parcial\|sin_datos` | `fe/app/analisis/rentabilidad/page.tsx:19-24, 48-58` | Banner "datos desde <fecha>" |
| Colores y cuentas | `backend/core/marketplaces.py:47-127`; `fe/lib/theme.ts:16-24`; `fe/lib/canales.ts:11-26` | Paleta por canal y punto, iniciales y nombre por cuenta |
| `AppNavbar.ITEMS`, `REGLAS`, `puede` | `fe/components/AppNavbar.tsx:71-157`; `backend/core/rbac.py:83-222`; `rt/auth.py:49-56` | Registro de la pestaña y permisos |
| `_SEMANA_TS` | `svc/monitoreo.py:301-320` | Semana ISO en hora de México |
| `FulfillmentPendiente` | `fe/components/FulfillmentPendiente.tsx:1-10` | Placeholder honesto para publicar por etapas |

Antes de copiar estas piezas:
- La copia local de `svc/inventario_maestro.py` usa `pl_leyendo` sin definirla (`:288-289`).
- `fe/app/inventario/page.tsx` está modificado sin commit.
- `docs/PROMPT_DISENO_PUBLICADOR.md` está sin versionar.
- En `conocimientoGeneral` no hay nada de FULL, FBA ni WFS: las únicas coincidencias son `full_url` y `fullmatch` (`conocimientoGeneral/ML_PUBLICACIONES_IA/scripts/generar_contenido_ml.py:273, 1331`).

## E) Qué se puede pintar y qué no

1. **Datos reales el primer día:**
   - MLM o ASIN con su SKU por cuenta (filas 2 y 16c).
   - Stock disponible en FULL por cuenta (`rt/fulfillment.py:2178-2287`) y la foto de FBA con "en camino" y "recibiendo" (`svc/fba_reporte.py:48-55`).
   - Ventas, primera venta por día (con aviso en las que coincidan con el 27-dic-2025), visitas de 30 días de ML y sugerido propio.
   - Cambios de precio de lista desde el 17-jul.
   - Salidas de Odoo ya validadas, separadas por contraparte y rotuladas "validación de salida (aprox.)".
   - Los INBOUND_RECEPTION de ML que ya estén en `ops.fanout_log`.
2. **"Aún no medido" (rayado, con null):**
   - Fecha y unidades de solicitud y de validación, hasta que exista la tabla de la lista.
   - Hora real de recolección.
   - Recibidas y rechazadas por envío en FBA y WFS.
   - Fecha exacta de activación y visitas a primera venta de ventas pasadas.
   - Stock y recepción en WFS, y las calificaciones.
   - Todo el histórico que hoy se tira: crudos de ML a los 3 días (`mig/0050:54-55`), `fba_snapshot` que se reemplaza en cada carga (`svc/fba_reporte.py:126`), y fecha e inbound_id de la operación (`svc/stock_full.py:214, 220`).
3. **Necesita decisión de negocio o hablar con las KAM:**
   - Qué es "full price", qué "calificación del listing" y qué "ranking".
   - Cómo se llama en Odoo el socio que usan Thalía, Nancy y Cin, si distingue BEKURA de SANCORFASHION, y si "MERCADO LIBRE" es el socio de las ventas de meli_oerp (item 5, `svc/odoo.py:1336`).
   - Si el panel generará la orden de salida o cargará el envío: es flujo vivo, dale de Brandon.
   - La meta de las tasas de validado y enviado, si FULL puede salir de DROP OFF y si la pestaña absorbe `/analisis/fba`.

**Nota:** la lista de trampas de Odoo llegó truncada y no llegaron las de analítica ni las del panel. Todo lo de Amazon FBA y varias filas de 15 y 16 venían con `revisado:false`.