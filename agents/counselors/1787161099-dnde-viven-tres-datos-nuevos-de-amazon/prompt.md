# Second Opinion Request

## Question
# Dónde viven tres datos nuevos de Amazon FBA (y una medición que no reconcilia)

Panel OMNICANAL (FastAPI + Next.js, Postgres/Supabase). La pestaña
`/analisis/fba` se construyó ayer y funciona. Ahora llegan tres datos nuevos y
hay que decidir **dónde viven bajo la arquitectura de la BD**, no cómo se
pintan.

Quiero crítica del DISEÑO y de la MEDICIÓN. No escriban código.

## La arquitectura de esquemas (esto es lo que hay que respetar)

La BD kubera es la fuente de verdad desde el corte del 12-ago. Cada esquema
tiene un carácter y meter algo en el equivocado ya causó incidentes:

- `core` — el registro civil del catálogo (`products`). Lo que un producto ES.
- `channel` — el estado del canal **tal como el canal lo reporta**
  (`listings`, `orders`, `order_items`). Se alimenta de syncs y webhooks.
- `costing` — el costeo (`costos_validados`, `costos_finales`).
  ⚠ Regla P4 de la casa: `costos_finales` tiene PK `(sku, canal)` y **hoy
  todas las filas son `canal='mercado_libre'`**. Toda consulta nueva filtra
  canal.
- `enrich` — datos que se le **PIDEN a una API bajo demanda y se cachean con
  su propio TTL**: `product_media`, `market_listing_metrics`,
  `listing_visits`, `listing_weight`, `order_shipping_cost`. El campo
  `consultado_at` es lo que decide si se vuelve a llamar.
- `ops` — **fotos operativas y bitácoras**: `stock_watch_photo`,
  `process_log`, `webhook_events`, y desde ayer `fba_snapshot`.
- `migration` — andamiaje del corte, en retiro.

Regla viva y dolorosa: *"congelar una tabla es cambiar el contrato de LECTURA,
no solo el de escritura"* — un `None` de una tabla detenida no significa "no
existe", significa "ya no sé". Costó 964 pedidos fantasma y $409,741.

## Lo que YA existe (construido ayer, en producción)

`ops.fba_snapshot`: una fila por SKU del reporte "Manage FBA Inventory" de
Seller Central. Se refresca **solo, cada mañana 13:10 UTC**, pidiéndolo a la
Reports API (`GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA`) y **reemplazando la
foto completa** (delete+insert en una transacción). Trae: `fulfillable`,
`reserved`, `unsellable`, los tres `inbound_*`, `asin`, `price` y
`per_unit_volume` (volumen por unidad **medido por Amazon**, en cm³).

Se eligió `ops` y no `enrich` porque es una foto de un almacén en un momento,
no un caché con TTL por ítem.

## Los tres datos nuevos

**1. CAPACIDAD CONTRATADA Y USADA.** La dio una persona (la CAM de Amazon),
copiada del *FBA Capacity Monitor* de Seller Central:

    Tamaño estándar: 94% usado — 15.43 de 16.42 m³
    Tamaño grande:   95% usado — 20.55 de 21.66 m³

**Busqué y no encontré ningún endpoint de SP-API que exponga esto.** Es UI de
Seller Central. O sea: dato capturado a mano, que cambia mes con mes, y que es
un **límite**, no una observación.

**2. TIER DE TAMAÑO (estándar vs grande).** La CAM dio la regla:
estándar = peso ≤ 9 kg **y** lados ≤ 18" / 14" / 8".
Pero encontré que Amazon **publica su propio veredicto**: el reporte de
tarifas de almacenamiento trae `product_size_tier` por SKU, más
`longest_side`, `median_side`, `shortest_side`, `item_volume` y `weight`
medidos por Amazon.

**3. TARIFAS POR PRODUCTO.** `getMyFeesEstimates` (Product Fees API) devuelve
fulfillment fee + referral fee por SKU/ASIN a un precio dado. Rate limit
**0.5 req/s** (1,259 SKUs ≈ 42 min). Hay versión masiva en reporte.
Contexto: el pendiente #5 del proyecto dice *"comisión de Amazon en pedidos =
0 (falta Finances API)"* — hoy el margen de Amazon sale incompleto.

## LA MEDICIÓN QUE NO RECONCILIA (esto es lo que más quiero que critiquen)

Apliqué la regla del CAM a los 96 SKUs con inventario y sumé
`per_unit_volume × (fulfillable + reserved)`:

| | SKUs | m³ que calculo | m³ que reporta Amazon |
|---|---|---|---|
| Estándar | 38 | 5.85 | **15.43** |
| Grande | 32 | 10.57 | **20.55** |
| sin dims/peso | 26 | 3.01 | — |
| **Total** | 96 | **19.43** | **35.98** |

**Falta casi la mitad del volumen, y el factor NO es constante**: 2.64× en
estándar, 1.94× en grande. Mis hipótesis, ninguna verificada:

- Amazon mide volumen de ALMACENAJE (con empaque de bodega), no el del ítem.
- La ocupación del monitor es un PROMEDIO del período, no una foto.
- El monitor incluye algo que no estoy sumando (¿unsellable? ¿inbound
  recibido? ¿inventario en tránsito dentro de la red?).
- Los 26 SKUs sin dims/peso arrastran 3.01 m³ sin clasificar.

Mi conclusión provisional: **la ocupación hay que TOMARLA DADA, no
calcularla**; si la calculáramos, la pestaña diría 19 m³ donde Seller Central
dice 36 y nadie sabría a cuál creerle.

Corolario incómodo: el plan de envío de la pestaña dice **1.83 m³**, calculado
con esas mismas unidades. Si el factor real anda cerca de 1.85×, el envío
verdadero pesaría ~3.4 m³ — y **solo quedan 2.10 m³ libres** (0.99 + 1.11) al
94-95% de ocupación. **El plan que hoy sugiere la pestaña probablemente no
cabe**, y la pestaña no lo sabe.

## Lo que quiero de ustedes

1. **¿Dónde vive la capacidad?** Es capturada a mano, cambia mensualmente, es
   un LÍMITE y no una observación, y viene por tier. ¿`ops`? ¿Tabla con
   historial o una fila que se pisa? ¿O ni siquiera tabla — configuración?
   ¿Cómo se evita que un número capturado hace tres meses se lea como vigente
   (la lección del "None de una tabla detenida")?

2. **¿Calcular el tier o pedírselo a Amazon?** Si se pide: ¿extender
   `ops.fba_snapshot` (mismo reporte, otra fuente) o tabla aparte? ¿`enrich`,
   por ser otra llamada con su propio TTL? ¿Y qué hacer con los 26 SKUs que
   hoy no se pueden clasificar?

3. **¿Las tarifas de Amazon van a `costing.costos_finales` con
   `canal='amazon'`** —que es para lo que existe la PK `(sku, canal)`, hoy
   desaprovechada— **o a `enrich` por ser un caché de API con TTL?** El dato
   es un COSTO pero se obtiene como un CACHÉ. ¿Qué gana y qué pierde cada
   opción? Ojo: `costos_finales` la escribe el motor de precios de ML.

4. **La brecha de volumen: ¿es aceptable mostrar los dos números** (el nuestro
   y el de Amazon) **o eso es exactamente el error de tener dos verdades?**
   ¿Y qué hacemos con el m³ del plan de envío, que está en unidades que no
   coinciden con las de la capacidad contra la que hay que compararlo?

5. **¿Hay algo en este diseño que huela a que estoy resolviendo el problema
   equivocado?** El objetivo real es que alguien decida QUÉ MANDAR a FBA
   sabiendo que hay 5% de espacio libre.

Sean críticos y directos. Si el planteamiento está mal, díganlo.

## Archivos para leer

- @backend/routers/fba.py — el tablero: semáforo de cobertura, plan de envío,
  divergencia de volumen contra el costeo.
- @backend/services/fba_reporte.py — parseo, guardado (delete+insert en una
  transacción) y el refresco por Reports API con su candado.
- @supabase/migrations/0023_ops_fba_snapshot.sql — la tabla que ya existe y el
  razonamiento de por qué quedó en `ops` y no en `enrich`.
- @CLAUDE.md — reglas de la casa y la sección de MIGRACIÓN, donde está el
  carácter de cada esquema y el incidente de los 964 pedidos fantasma.
- @frontend/app/analisis/fba/page.tsx — la pantalla que consume todo esto.

## Instrucciones

Están dando una revisión INDEPENDIENTE de una decisión de arquitectura de datos.
- Lean los archivos antes de opinar; el razonamiento de por qué cada cosa está
  donde está vive en los comentarios, no en la documentación.
- Sean críticos y opinen: nada de "depende". Si hay una opción mejor, díganla.
- Prioricen la pregunta 4 y la 5: la brecha de volumen y si estoy resolviendo
  el problema equivocado.
- Si creen que la conclusión provisional ("tomar la ocupación dada, no
  calcularla") está mal, díganlo con su razón.

## Instructions
You are providing an independent second opinion. Be critical and thorough.
- Analyze the question in the context provided
- Identify risks, tradeoffs, and blind spots
- Suggest alternatives if you see better approaches
- Be direct and opinionated — don't hedge
- Structure your response with clear headings
- Keep your response focused and actionable
