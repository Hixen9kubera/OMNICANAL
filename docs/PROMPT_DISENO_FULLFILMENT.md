# Prompt — Pestaña **FULLFILMENT** (FULL de Mercado Libre · FBA de Amazon · WFS de Walmart)

> **Cómo se usa este documento**
>
> 1. **Parte 1** → pégala completa en **Claude Design**. Es autocontenida: no
>    necesita acceso al código.
> 2. Cuando el diseño esté listo, abre un chat nuevo de construcción y pégale
>    **el handoff de Claude Design + la Parte 2** de este documento. La Parte 2
>    trae la evidencia técnica (archivo:línea) y las decisiones pendientes.
> 3. La matriz verificada completa, requisito por requisito, vive en
>    [`docs/FULLFILMENT_MATRIZ.md`](FULLFILMENT_MATRIZ.md).
>
> Todo lo medido aquí es del **14-sep-2026**, contra producción (Odoo, la base
> kubera y la API de Mercado Libre) y contra el código (13 agentes, cada hallazgo
> revisado por un escéptico). **Re-mide antes de creer**: estos números cambian
> cada semana.

---

# PARTE 1 — Para Claude Design

## El encargo

Diseña una pestaña nueva del panel **Omnicanal de Kubera** llamada
**FULLFILMENT**. Rastrea el circuito completo de la mercancía que mandamos a los
almacenes de los marketplaces:

| Canal | Programa | Cuentas |
|---|---|---|
| Mercado Libre | **FULL** | **2**: *Kubera* y *San Corpe* |
| Amazon | **FBA** | 1 (San Corpe) |
| Walmart | **WFS** | 1 |

La pestaña tiene que contestar, por canal, por cuenta y por SKU:

- **Cuándo** se pidió un envío, cuándo se validó, cuándo salió y cuándo lo
  recibió el marketplace.
- **Cuánto** se pidió, cuánto se envió, cuánto **recibió** el marketplace y cuánto
  **rechazó o devolvió**.
- **Cajas y piezas** que se movieron.
- **Cuánto hay hoy en FULL** por SKU y por su publicación (el **MLM**).
- **Qué días** se procesan los envíos.

### La gráfica que es requisito, no sugerencia

**Tasa de éxito de cada envío: piezas entregadas a FULL contra piezas que nos
rechaza**, por canal, por cuenta y en el tiempo. Tiene que estar en la vista
principal.

---

## El proceso real (así funciona hoy, a mano)

Lo describió el equipo de operación:

1. **Martes — Solicitud.** Andy arma una lista con los productos que se quieren
   enviar y **a dónde**: `drop`, `meli_bekura` (FULL Kubera), `meli_sank` (FULL
   San Corpe), `fba`, `wfs`.
2. **Martes — Validación de Bodega.** Bodega revisa y dice qué **sí** se puede
   mandar y qué **no**. Aquí puede haber que reajustar lo que se quería enviar:
   **con IA, o editándolo Andy a mano**.
3. **Miércoles — Orden de salida.** Se genera la orden de salida en Odoo y se
   carga el envío a Mercado Libre.
4. La paquetería del marketplace **recoge**.
5. El marketplace **recibe**: parte entra al almacén y parte se rechaza o se
   devuelve.
6. La publicación **se prende** en FULL (el SKU estaba inactivo en ML hasta que
   hay stock allá).
7. **Primera venta** desde FULL.

Hoy todo el circuito tarda **una semana**. Quieren **hacerlo desde el sistema** y
guardar dos tasas: la de **validado** (lo que Bodega aprobó de lo que se pidió) y
la de **enviado** (lo que salió de lo que se aprobó).

### ⚠️ Lo que dicen los datos no es exactamente eso

Medido en Odoo sobre **204 órdenes de salida a FULL** (13-ene → 14-sep-2026):

| | lun | mar | mié | jue | vie | sáb |
|---|---|---|---|---|---|---|
| **orden creada** | 28 | **52** | 32 | **60** | 24 | 10 |
| **salida hecha** | 45 | 40 | **23** | 35 | **50** | 13 |

- Las órdenes se crean sobre todo **martes y jueves** — parecen **dos ciclos por
  semana**, no uno.
- El **miércoles es el día con MENOS salidas**, no el del proceso.
- De la orden a la salida pasan **8 días de mediana y 15 en el peor 10%.**

**Diseña la vista de "qué días se procesan" para enseñar la distribución real**,
no el calendario ideal. Esa diferencia es justo lo que el equipo necesita ver.

---

## Lo que YA sabemos y lo que TODAVÍA NO — el diseño debe distinguirlo

Esta es la parte más importante para diseñar bien. Una pantalla que pinta **0**
donde en realidad **no hay dato** miente, y la gente le cree. El panel ya tiene un
lenguaje visual para eso (ver "La casa" más abajo) y esta pestaña lo va a usar
mucho.

### ✅ Con datos reales desde el primer día

**Stock en FULL de Mercado Libre, hoy:**

| Cuenta | Publicaciones con stock | Piezas en FULL | Marcadas FULL | **…en cero** |
|---|---|---|---|---|
| Kubera | 276 | 12,428 | 1,010 | **734** |
| San Corpe | 274 | 7,914 | 874 | **600** |

→ **1,334 de 1,884 publicaciones FULL (71%) están en cero.** Eso es una métrica
de primera, no un detalle.

**Stock en FBA de Amazon, hoy:** 1,923 disponibles · 1,568 reservadas · 1 no
vendible · en camino: 1,239 en preparación + 149 enviadas + 2 recibiéndose.

**Salidas ya hechas desde Odoo:**

| | Órdenes | Piezas enviadas | De lo pedido en la salida | Quién las arma |
|---|---|---|---|---|
| **FULL (ML)** | 204 | 116,895 | 97.9% | Thalía (111), Cinthya (90), Vale (3) |
| **FBA (Amazon)** | 40 | 4,353 | 100% | Nancy (37), Vale (2), Thalía (1) |
| **WFS (Walmart)** | **0** | — | — | **ninguna orden en Odoo** |

También hay desde el primer día: **ventas** y **primera venta** por SKU, **visitas**
de 30 días en ML, **cambios de precio** con fecha (desde el 17-jul) y un
**sugerido de resurtido** propio.

### 🟨 El aviso llega, pero hoy se tira — hay que empezar a guardarlo

- **Recepciones en FULL.** Mercado Libre avisa cada movimiento del almacén FULL en
  segundos, y el que mete mercancía nueva trae el **número de envío**. Pero hoy
  esos avisos **se borran a los 3 días**, y el desglose de lo rechazado se
  descarga y se desecha. **La gráfica de recibido vs rechazado empieza el día que
  se construya**, más lo que se pueda recuperar de la API.
- **Fecha exacta en que FULL se prende.** Hay historial de cambios, pero con
  retraso de minutos a horas (se observa por lotes).
- **Visitas al momento de la primera venta.** No se guardan con fecha: hay que
  empezar a registrarlas cuando ocurre cada venta.

### ⬜ Todavía no existe — el diseño lo enseña como "aún no medido"

- La **lista de Andy** y la **validación de Bodega**: no hay dónde vivan hoy. Esta
  pestaña es donde van a nacer.
- **Recibidas y rechazadas en FBA** (Amazon no se consulta todavía).
- **Todo WFS**: no hay ni una orden en Odoo, ni lectura de inventario ni de
  envíos de Walmart.
- **Cajas por envío**: Odoo no registra cajas; sólo se pueden **derivar** de las
  piezas y del factor de caja, y hay que rotularlas como estimadas.
- **Calificación del listing** y **calificación de operaciones**.

**Regla de diseño que se desprende:** cada KPI, columna y gráfica necesita **tres
estados visuales distintos** — *dato real*, *cero real* y *sin dato todavía* — y
las gráficas llevan una leyenda de **"datos desde \<fecha\>"**.

---

## El enlace entre Odoo y Mercado Libre — y su hueco

Las KAM **teclean a mano el número del envío de FULL** en la referencia de la
orden de Odoo. Así se ve:

| Quién | Cómo lo escribe | Órdenes con número |
|---|---|---|
| Thalía | `75652884` | 89 de 111 |
| Cinthya | `Envío #70688003` | 50 de 90 |

Es la **llave** que une "lo que enviamos" con "lo que ML recibió". Pero **una de
cada cuatro órdenes no la tiene**, y las dos personas la escriben distinto.

→ El diseño necesita un estado **"envío sin enlazar"**: salió de bodega pero no
se sabe a qué envío de ML corresponde, así que no se puede calcular su tasa de
recepción. Y conviene que la futura solicitud **capture ese número desde el
sistema**, en vez de depender de que alguien lo teclee.

---

## Las métricas

### Obligatoria

**1. Tasa de recepción por envío.** Piezas **recibidas** ÷ **enviadas**, y
**rechazadas** ÷ **enviadas**. Por canal, cuenta y semana.

⚠️ **Mientras el marketplace sigue recibiendo, *enviadas − recibidas* NO es un
rechazo** — es trabajo en curso. El rechazo tiene que ser una cantidad
**explícita** que dé el marketplace, nunca una resta. El diseño debe separar
**en recepción** de **rechazado**.

### Propuestas (con el dato que las alimentaría)

| # | Métrica | Qué contesta | Dato |
|---|---|---|---|
| 2 | **Embudo de piezas**: solicitadas → validadas → enviadas → recibidas → vendidas | dónde se pierde la mercancía, con % de pérdida en cada escalón | incluye las dos tasas que pidió operación |
| 3 | **Tiempo por etapa** (mediana y peor 10%) | qué tramo alarga "la semana" | hoy: orden→salida 8 d / 15 d |
| 4 | **Mapa de calor día × semana** | si el calendario martes/miércoles se cumple | ya medible con Odoo |
| 5 | **Agotado en FULL** | cuánto se deja de vender por no resurtir | hoy **71%** de las publicaciones FULL en cero |
| 6 | **Días de cobertura** (stock FULL ÷ venta diaria) | cuándo hay que mandar otra vez | stock y ventas ya existen |
| 7 | **Venta desde la activación** (% del envío vendido a 7 / 14 / 30 días) | si lo que mandamos se mueve | |
| 8 | **Precisión de la solicitud** | cuánto recortó Bodega, cuánto ajustó la IA, y si el ajuste acertó contra lo vendido después | nace con la lista de Andy |
| 9 | **Inventario envejecido en FULL** (sin venta 30 / 60 / 90 días) | qué está pagando almacenaje sin venderse | |
| 10 | **Retiros de FULL** | cuánta mercancía nos regresa ML | el aviso existe: **44 retiros en 3 días** |
| 11 | **Envíos sin enlazar** por KAM | calidad de captura | hoy **~24%** |
| 12 | **Visitas a primera venta** y conversión desde la activación | qué tanto cuesta arrancar una publicación | hay que empezar a guardarlo |

---

## Qué pantallas proponemos (tú decides la forma)

1. **Tablero** — la tasa de recepción (obligatoria), el embudo, los días de
   proceso y el agotado en FULL. Filtro por canal y por cuenta.
2. **Envíos** — un renglón por envío con su **rail de etapas**: Solicitado →
   Validado → Orden en Odoo → Recolectado → Recibido → Activo → Primera venta.
   Cada etapa con su fecha o su estado "sin dato".
3. **Detalle de un envío** — por SKU: solicitadas · validadas · enviadas ·
   **en recepción** · recibidas · **rechazadas** · cajas (estimadas). El número de
   envío del marketplace, o el aviso de "sin enlazar".
4. **Planeación semanal** — la lista de Andy, la validación de Bodega con su
   **reajuste por IA o a mano** (guardando la lista original, la propuesta y la
   final), y la generación de la orden. **Ojo:** esto *mueve* mercancía — ver
   "Ver no es mover".
5. **Por SKU / MLM** — la vida del producto en FULL: activación, primera venta,
   visitas, stock, sin venta, precio y sus cambios.

---

## La casa: el sistema de diseño que hay que respetar

### Colores por canal (exactos)

| Canal | Principal | Texto | Acento | Fondo suave |
|---|---|---|---|---|
| Mercado Libre | `#FFE600` | `#2D3277` | `#3483FA` | `#FFFBE0` |
| Amazon | `#FF9900` | `#131A22` | `#232F3E` | `#FFF4E0` |
| Walmart | `#0071DC` | `#FFFFFF` | `#FFC220` | `#E6F1FC` |
| General (panel) | `#4F46E5` | `#FFFFFF` | `#818CF8` | `#EEF0FF` |

Lienzo `#F6F7FB`, texto `#1F2430`. Ojo con el amarillo de ML: sobre él, el texto
es **azul marino**, no blanco, y la franja de acento es `#3483FA`.

### Las dos cuentas de Mercado Libre

Siempre **distinguibles**: un chip "Mercado Libre" que no diga cuál esconde la
mitad de la información. Hoy la tabla usa un punto **azul cielo** para Kubera y
**violeta** para San Corpe. **Nómbralas siempre "Kubera" y "San Corpe"** (el panel
tiene pantallas viejas que dicen "Bekura"/"Sancor" y hay que unificar).

### Patrones ya establecidos

- **Rayado diagonal = "sin registro / aún no medido"**, visualmente distinto de
  un cero. Con leyenda.
- **Ámbar = "en espera"**: el dato viene pero todavía no llega.
- **Rail de etapas** paso a paso, donde **no se resta** una etapa de otra para
  inventar la siguiente.
- **Error explícito**: el texto crudo del error, seleccionable, con botón
  *Copiar*.
- **Estados vacíos que dicen la causa real** ("no hubo envíos esta semana" ≠ "no
  se pudo leer Odoo").
- **Tarjetas de meta** con valor, meta y comparación contra la semana anterior.
- **Chips que filtran** sin reemplazar la vista.

### Ver no es mover

Todo el equipo (KAM y admin) **ve** la pestaña. Las acciones que **mueven
mercancía** — generar la orden de salida en Odoo, cargar el envío al
marketplace — son de **admin**, y quien no puede verlas las ve **deshabilitadas
con su motivo**, no escondidas ni con un error al tocarlas.

### Lenguaje

- Todo en **español**.
- Fechas: "hace 3 h", "02 sep 11:02". Semanas **ISO, de lunes**, en hora de
  **Ciudad de México**.
- **No uses "en tránsito" ni "en camino"** para una orden de Odoo que sólo existe
  en papel: esas palabras prometen movimiento físico. Úsalas sólo cuando el
  marketplace confirma que la mercancía viaja.
- "DROP" es el término de la casa para nuestro almacén propio.

### Dónde vive

El menú superior **ya tiene 10 entradas y se desborda**. Propón si FULLFILMENT va
arriba o dentro de un submenú.

---

# PARTE 2 — Para el chat que lo construya

> Pégala junto con el handoff de Claude Design. Antes de escribir código, lee
> [`docs/FULLFILMENT_MATRIZ.md`](FULLFILMENT_MATRIZ.md) (el estado de cada
> requisito por canal, con evidencia) y **busca primero en `conocimientoGeneral`**
> (regla 14 de CLAUDE.md). *Nota: ahí no hay nada de FULL, FBA ni WFS.*

## 1 · Choque de nombre — resuélvelo primero

**`/api/fulfillment` ya existe** y es el panel de **Análisis / márgenes**
(`backend/routers/fulfillment.py:64`), no este circuito. El RBAC empareja por
**prefijo** (`startswith`), así que una ruta nueva bajo ese prefijo heredaría sus
permisos sin que nadie lo decida. Además hubo una sub-pestaña "Fulfillment"
retirada (`frontend/app/analisis/rentabilidad/page.tsx:6-8`). **La ruta tiene que
ser otra** (p. ej. `/api/envios-full`), aunque el rótulo diga FULLFILMENT.

## 2 · Las diez trampas medidas

1. **"Envío a FULL" en Odoo no significa envío a FULL.** `_causa`
   (`services/odoo.py:1336, 1394-1396`) etiqueta `envio_full` si el **nombre del
   socio** contiene FULL, AMAZON, TIKTOK, TEMU, SHEIN, MERCADO LIBRE o WALMART.
   Automatización crea órdenes con socios TikTok y Temu → **las ventas DROP caen
   como envíos a FULL.** En Odoo, envío y venta terminan los dos en `Customers`:
   **sólo el socio del picking los separa**. Socios reales de salidas hechas
   (14-sep): `shein` 425, **`FULL` 204**, `tiktokshop` 194, `temu` 65, **`AMAZON`
   37**, `Amazon` 3, `full` 2, `MERCADO LIBRE` 2, `tiktokahop` 2 (con errata). **No
   hay ningún socio Walmart/WFS.** Y el socio `FULL` **no distingue Kubera de San
   Corpe**. Ojo con coincidencias por apellido ("Bertha … Mercado").
2. **Odoo entrega en tres pasos (PICK → PACK → OUT)**: una orden deja tres
   pickings. **Sumar los tres triplica las piezas** — medido: 352,909 contra las
   116,895 reales. Cuenta sólo el paso de salida (`picking_type_id.code =
   'outgoing'`).
3. **Las fechas de Odoo** llegan en UTC sin zona (un martes después de las 18 h
   cae en miércoles), `date_done` es cuando se **validó** el picking y no la
   recolección, y **sólo se leen movimientos `done`**: una orden creada pero no
   validada hoy no existe para el panel (`odoo.py:1223`).
4. **Tomar lo "solicitado" de Odoo pone la tasa de validado en 100%.** La
   cantidad de la orden **ya trae el recorte de Bodega**; la lista original no deja
   rastro. La tasa de validado sólo existe si la lista de Andy se guarda **antes**.
5. **Enviadas − recibidas no es un rechazo** mientras se recibe. La pestaña
   Inventario ya prohíbe esa resta (`frontend/app/inventario/page.tsx:1486-1594`).
6. **Hay "ingresos" que no son ingresos.** Sumar `TRANSFER_*` da −329 piezas
   fantasma: es un barajeo interno de ML (`stock_full.py:78-83`). Sólo
   `INBOUND_RECEPTION` mete mercancía nueva. Y en modo solo-registro, `stock_drop`
   es stock de Woo, **no piezas recibidas**.
7. **"Stock FULL" hoy mezcla ML con FBA**: para Amazon se escribe `stock_fba` en
   `stock_full` (`channel_mirror.py:93`) y Análisis suma ambos. Hay tres "stock
   FBA" distintos que difieren en 96 de 99 SKUs.
8. **El grano por publicación está roto**: la PK permite **un MLM por SKU por
   cuenta**; padre e hijo llegaron a duplicar 2,024 piezas; `listing_history` no
   guarda `listing_id` y fecha **cuándo se observó** el cambio (lotes de 80 cada
   15 min), no cuándo ocurrió.
9. **La primera venta puede salir falsa**: el histórico empieza el 27-dic-2025
   (todo lo anterior parece "primera venta" ese día), la vista viva empieza el
   16-jul, y en ML `es_fulfillment` de orders y de order_items discrepa en el 40%.
10. **Cajas inventadas**: `_cajas` con factor 1 devuelve cajas = piezas, contra lo
    que dice su docstring (`inventario_maestro.py:1103-1111`); las cajas de
    `packing_cajas` son cartones de **importación**, no de envío a FULL; Odoo no usa
    paquetes.

## 3 · Lo que se está tirando y hay que empezar a guardar (el primer día)

- **`ops.webhook_events` retiene 3 días** (migración 0050). Medido: todos los
  tópicos van del 11 al 14-sep. Llegan **~620 `fbm_stock_operations` al día**
  (2,253 en 3.6 días: 1,566 de una cuenta y 690 de la otra).
- `ops.fanout_log` anota recepciones **sin `inbound_id`**, con la cantidad dentro
  de un texto ("INBOUND_RECEPTION x15"), y sólo tiene **38 filas** `full_ingreso_sim`
  desde el 24-jul — no sirve como historia.
- `stock_full.procesar_operacion` (`:300-330`) resuelve la operación, **descarga el
  desglose de `/inventories/{id}/stock/fulfillment` y lo desecha** (`:316-320, 338`).
  → Un escritor nuevo ahí debe guardar `operation_id`, `inventory_id`, `inbound_id`,
  SKU, cuenta, tipo, cantidad, total resultante y **la fecha de la operación**.
- `ops.fba_snapshot` hace **DELETE + INSERT** en cada carga: no hay historia de FBA.
  Tampoco se conserva `inventoryDetails.inbound*`, que ya llega y se descarta
  (`inventario.py:458, 471`).
- **Recuperar lo pasado**: `/stock/fulfillment/operations/search` de ML y la
  Fulfillment Inbound API + Inventory Ledger de Amazon **nunca se han llamado**
  (🔌). Hay que medir hasta dónde dejan ir atrás.

⚠️ **No verificado**: si una `INBOUND_RECEPTION` trae un desglose explícito de
rechazadas. En los tres días retenidos no hubo ninguna recepción (sí 44 retiros,
45 ventas, 18 ajustes). **La primera recepción real hay que abrirla y leerla antes
de diseñar la tabla.**

## 4 · Piezas que ya existen (no las reimplementes)

| Pieza | Dónde | Da |
|---|---|---|
| Webhook `fbm_stock_operations` + `procesar_operacion` | `routers/webhooks.py:411-419` · `services/stock_full.py:280-405` | la operación de FULL en segundos, con su tipo y SKU |
| `_SQL_INV_BASE` | `routers/fulfillment.py:2178-2287` | stock FULL por SKU **sin duplicar**, con `full_bk` y `full_sc` |
| `fn_listing_history` | migración 0001:149-200 | antes/después de `stock_full`, `is_fulfillment`, `price`, con `changed_at` |
| `channel.restock_panel` | migración 0007:53-161 | sugerido propio de resurtido (ML y Amazon) |
| `fba_reporte` + `ops.fba_snapshot` | `services/fba_reporte.py:169-223` | ciclo de reportes SP-API, reusable para Ledger y retiros |
| `movimientos_por_sku` | `services/odoo.py:1199-1398` | libro de bodega con pedido, hecho, socio y causa |
| `recepciones_pendientes_por_sku` | `services/odoo.py:894-999` | **molde** para leer salidas abiertas (hoy no se leen) |
| `libre_por_almacen` / `planear_almacenes` | `services/odoo_ventas.py:298-391` | `free_qty` por almacén: la prevalidación de Bodega |
| `odoo_ventas_log` | `services/odoo_ventas_log.py` | molde de bitácora con foto congelada y conteos |
| `_completar` | `services/ia_generadores.py:34-82` | DeepSeek con Claude de respaldo (síncrono → `to_thread`) |
| `/api/fulfillment/estrellas` | `routers/fulfillment.py:502-596` | primera y última venta por SKU |
| `competencia_ml.visitas` / `visitas_serie` | `services/competencia_ml.py:347-376` | visitas por MLM |
| gancho post-venta | `services/pedidos_ml.py:763-805` | dónde cuelga el disparador de visitas a primera venta |
| `RAYADO`, `ChipSinRegistro`, `BandaMetas`, `CajaError`, `ErrorDeCarga` | `frontend/app/monitoreo/page.tsx` | los tres estados y el error crudo (son locales: copiarlos) |
| `Recorrido`, `BotonBloqueado` | `frontend/app/inventario/page.tsx` | rail de etapas y acción bloqueada |
| `_SEMANA_TS` | `services/monitoreo.py:301-320` | semana ISO en hora de México |
| `FulfillmentPendiente` | `frontend/components/FulfillmentPendiente.tsx` | placeholder honesto, hoy sin usar |

## 5 · Cómo reproducir las mediciones de Odoo

```python
# Sólo la SALIDA final, filtrada por el socio. Nunca PICK/PACK.
outs = search_read("stock.picking",
    [["partner_id.name", "=ilike", "FULL"], ["state", "=", "done"],
     ["picking_type_id.code", "=", "outgoing"]],
    ["origin", "date_done", "create_date"])
# origin = la orden de venta (S#####). En sale.order: date_order, user_id,
# client_order_ref (ahí va el número de envío de ML), warehouse_id.
# Fechas en UTC → convertir a America/Mexico_City antes del día de la semana.
```

`ir.model.fields` **no es legible** con el usuario de la API: usa
`fields_get`.

## 6 · Reglas de la casa que aplican

1. **Generar la orden de salida o cargar el envío al marketplace es un flujo de
   negocio vivo** (regla 3): no sube a `main` sin el **dale explícito de Brandon**.
   Las pantallas de lectura sí van directo.
2. **Odoo es el MASTER del inventario** → `free_qty`, no `qty_available`.
3. **Regla 11**: nada síncrono en una corrutina — xmlrpc de Odoo, psycopg2 e
   `ia_generadores._completar` van en `asyncio.to_thread`.
4. **Regla 13**: nunca marques la sesión read-only contra kubera (pooler 6543).
5. **Nunca publicar un SKU padre.**
6. Tres sesiones comparten el repo: saca el trabajo por `git worktree add
   --detach <tmp> origin/main` y revisa `git diff origin/main HEAD | grep '^-'`
   antes de empujar.

## 7 · Lo que hay que resolver con Brandon y las KAM (item 5 de su lista)

1. **¿Quién hace WFS?** La lista dice *WFS (Cin)*, pero **no hay ni una orden de
   Walmart en Odoo**, y **Cinthya creó 90 órdenes con socio `FULL`** usando números
   de envío de Mercado Libre (mismo rango de 8 dígitos que Thalía; hay pares casi
   consecutivos: `71421571` / `71421653`). ¿Cinthya hace FULL de la otra cuenta?
   ¿WFS se opera fuera de Odoo? Hubo feeds `OMNI_WFSCONVERT` enviados el 4-sep por
   alguien fuera del código.
2. **¿Kubera o San Corpe?** El socio `FULL` no lo dice. Hipótesis a comprobar:
   **Thalía = una cuenta, Cinthya = la otra** — se verifica consultando el número
   de envío con el token de cada cuenta.
3. **"Full price"**: nadie en el código lo tiene. ¿Precio de venta en FULL, tarifa
   de almacenaje, o costo por unidad enviada?
4. **"Calificación del listing" y "de operaciones"**: ¿cuáles exactamente? En ML,
   `/users/me` ya trae `seller_reputation` y se descarta; la calidad del item
   también se descarta.
5. **"Ranking de inventario"**: no existe con ese nombre.
6. **¿El panel generará la orden de salida y cargará el envío?** Es flujo vivo.
7. **Metas** de las tasas de validado y enviado.
8. ¿La pestaña **absorbe `/analisis/fba`** o sólo enlaza?
9. ¿FULL puede salir desde **DROP OFF**, o sólo desde TEXCO / TEXCO II?
10. El rótulo: **"FULLFILMENT"** (así se escribió en la petición) o "Fulfillment".
