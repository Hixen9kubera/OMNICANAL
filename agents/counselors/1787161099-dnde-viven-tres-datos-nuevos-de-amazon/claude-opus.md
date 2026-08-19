# Segunda opinión — FBA: dónde viven los datos y la brecha de volumen

Leí `fba.py`, `fba_reporte.py`, la migración 0023 y la página. El diseño de lo que ya existe es sólido y coherente (`ops` = reporte en bloque reemplazado entero; el mismo parser para subida manual y SP-API es la decisión correcta). Voy directo a lo que pediste priorizar.

## Q5 primero: sí, estás resolviendo el problema equivocado (a medias)

El objetivo real que escribiste es *"decidir QUÉ MANDAR sabiendo que hay 5% libre"*. Pero el `plan de envío` que construiste responde otra pregunta: *"¿cuánto necesito para dejar 60 días de cobertura?"* — e **ignora el techo de capacidad por completo**. Por eso la pestaña sugiere 3.4 m³ reales contra 2.10 m³ libres y no se entera.

Al 94-95% de ocupación, la restricción que manda **no es la cobertura, es el espacio**. El planificador por cobertura es la herramienta correcta cuando el espacio sobra; con el almacén lleno es la herramienta equivocada. La pregunta correcta es un **problema de mochila / priorización**:

> Dado ~2 m³ que se liberan conforme sale mercancía, ¿qué SKUs se ganan ese espacio?

Ordena por algo como **margen×velocidad por m³** (o por urgencia de agotamiento) y llena hasta consumir el hueco. El `sugerido` de hoy es el candidato ideal; lo que falta es el **filtro de "¿cabe, y si no cabe, cuál entra primero?"**.

Y un corolario que contradice tu router: **el tier NO es un adorno.** La capacidad es *por tier* (estándar 0.99 m³ libres, grande 1.11 m³). Un artículo grande no cabe en el hueco estándar y viceversa. Sin tier ni siquiera puedes *expresar* el espacio libre. En un almacén al 95%, el tier es carga estructural, no decoración.

## Q4: la brecha de volumen — tu conclusión es correcta, pero por la razón equivocada

**"Tomar la ocupación dada, no calcularla" es correcto.** No muestres dos números de "volumen usado" compitiendo — eso *es* el error de dos verdades. Muestra solo el de Amazon.

Pero el diagnóstico de *por qué* no reconcilia importa, porque de ahí sale la solución. El factor 1.85× no es un factor; es la **composición de tres cosas distintas**, y por eso es irreproducible:

1. **El monitor es un PROMEDIO del período, tu foto es un instante.** Si vienes drenando inventario, el promedio del mes > el stock de hoy. Esto solo ya explica una fracción grande.
2. **Población distinta.** Amazon cuenta todo lo físicamente en el FC: `fulfillable + reserved + unsellable + inbound ya recibido`. Tú sumaste `fulfillable + reserved`. Dejaste fuera `unsellable` y los 26 SKUs sin dims (que Amazon SÍ mide y SÍ cuenta a su volumen real, no a tu 0).
3. **Volumen de almacenaje ≠ cubo del ítem** en los bordes (binning, redondeo de tier).

Ninguna de las tres es "constante", así que ningún factor único va a cerrar. Por eso reconstruirlo es fútil. **Pero el error de fondo es más simple: estás restando peras y manzanas.** El cubo del envío (en tus unidades de ítem) y el espacio libre (en unidades de utilización de Amazon) **no son la misma unidad; no se pueden restar.** Ese es el verdadero bug detrás del "corolario incómodo".

### Qué hacer con el m³ del plan

No lo calcules en tus unidades. **Calíbralo con la piedra de Rosetta que Amazon te da gratis:** Amazon te dice utilización% *y* (con el dato #2, reporte de tarifas de almacenaje) el volumen de almacenaje por SKU que él asigna. Deriva el factor por tier empíricamente —que es justo lo que ya hiciste: 2.64× y 1.94×— y **construye el plan de envío en unidades de Amazon**, no en las tuyas.

Salvedad honesta que debe quedar en la UI: ese factor está contaminado por el efecto-promedio, que **no aplica igual a un envío fresco** (un envío nuevo pesa distinto en el instante que en el promedio del período según cuánto se quede). Conclusión operativa: al 95% **no puedes predecir el ajuste con precisión, solo acotarlo**. Trata el hueco como ~0 y planifica envíos que **reemplacen volumen que sale**, no que sumen. El KPI "Volumen del plan · 1.83 m³" tal como está hoy es activamente engañoso: subestima ~1.85×. Mínimo etiquétalo "sin factor de bodega" o aplícale el factor.

## Q1: dónde vive la capacidad

**No es `enrich`** (no hay API, no hay TTL — bien que lo intuiste). **No es config muerta** tampoco. Es un límite, mensual, por tier, capturado a mano.

Mi recomendación concreta: **tabla append-only en `ops`, con clave `(year_month, tier)`**, columnas `usado_m3`, `contratado_m3`, `medido_at`, `capturado_por`, `fuente`. Nunca se pisa la fila; una fila por mes.

Por qué append-only y no una fila que se pisa: **la fila sobrescrita es exactamente la trampa del "None de tabla detenida"** — se ve vigente para siempre. Una fila fechada por su mes se auto-data. Y como Amazon **resetea los límites cada mes**, la regla de lectura es dura: toma `MAX(year_month)`; si es anterior al mes actual, la pestaña lo muestra como *"capturado en [mes], puede no ser vigente"* y **no alimenta ningún cálculo de espacio libre en silencio**. Congelar aquí no es cambiar contrato de escritura, es evitar que un número de hace tres meses maneje una decisión de envío.

## Q2: pídele el tier a Amazon, no lo calcules

**Pídelo.** La regla del CAM (≤9kg Y lados ≤18/14/8) es una simplificación que fallará en los bordes, y **no puedes aplicarla a los 26 SKUs sin dims/peso** de todos modos. Amazon publica `product_size_tier` por SKU en el reporte de tarifas de almacenaje — es su veredicto autoritativo, **y es el mismo que define los buckets de capacidad contra los que comparas**. Si calculas el tier tú, tu split no reconciliará con la capacidad por tier de Amazon: una *segunda* divergencia encima de la de volumen. No lo hagas. Bonus: los 26 desconocidos dejan de serlo, porque Amazon sí los midió.

**Dónde: tabla propia en `ops`, hermana de `fba_snapshot` — NO la extiendas.** Viene de otro reporte, con otra cadencia (mensual vs. diaria) y otras columnas (`longest_side`, `item_volume`, `weight`, `size_tier`). El contrato de `fba_snapshot` es "un reporte, reemplazado entero en una transacción". Meterle un segundo reporte con otra cadencia significa que refrescar uno borra el otro o deja filas parciales. `ops.fba_storage_fees` (o `..._size_tier`), mismo molde delete+insert. Sigue siendo `ops`, no `enrich`: es un reporte en bloque, no una llamada por ítem con TTL.

## Q3: las tarifas — separa el CACHÉ del COSTO

Esta es la más filosa y la respuesta corta es: **ni una ni otra, las dos, en capas.**

- El **raw** de `getMyFeesEstimates` es enrich de manual: llamada por SKU, con rate limit, **a un precio dado** (la tarifa depende del precio → la clave del caché debe incluir el precio, o invalidas cuando el precio cambia). Va a `enrich.amazon_fee_estimate` con `consultado_at` y `price_quoted`. Es exactamente la forma de enrich.
- El **costo decidido** que otros leen va a `costing.costos_finales(sku, 'amazon')` — para eso existe la PK `(sku, canal)` hoy desaprovechada, y por fin habría un segundo canal real (respeta P4: filtrar canal).

**No dejes que el caché de la API SEA la fila de costeo.** El comentario dice que `costos_finales` la escribe el motor de precios de ML. Dos escritores con semánticas distintas peleando por la misma tabla es precisamente el tipo de landmine que ya te costó caro. Si el refrescador de tarifas escribe directo en `costos_finales`, un caché vencido se vuelve "el costo" en silencio, y el motor de ML y el de Amazon se pisan. Mantén lo con-forma-de-API en `enrich` (vencible, refetcheable sin culpa) y **promueve** deliberadamente un valor decidido a `costing`.

Detalle operativo: para el refresco completo periódico usa el **reporte masivo de tarifas** (no el crawl de 42 min a 0.5 req/s); reserva `getMyFeesEstimates` por-SKU para lo on-demand (precio de un listing nuevo). Eso mapea limpio: reporte masivo → snapshot estilo `ops` que alimenta `costing`; API por-SKU → caché `enrich`.

## Resumen de veredictos

| Dato | Dónde | Por qué |
|---|---|---|
| Capacidad contratada/usada | `ops`, append-only `(year_month, tier)`, lectura que rechaza lo viejo | límite mensual capturado a mano; historial = defensa anti-staleness |
| Tier de tamaño | `ops.fba_storage_fees` (tabla nueva, pídelo a Amazon) | veredicto autoritativo; reconcilia con la capacidad por tier; cierra los 26 |
| Tarifas | raw en `enrich` (clave incluye precio), costo decidido promovido a `costing.costos_finales(sku,'amazon')` | tiene forma de caché pero ES un costo; no mezclar escritores |

**Lo que cambiaría hoy, sin datos nuevos:**
1. Deja de mostrar tu m³ como si fuera comparable a la capacidad; muestra solo el de Amazon como ocupación.
2. Aplica (o al menos etiqueta) el factor de bodega al "Volumen del plan" — hoy engaña al 95%.
3. Reencuadra el plan de envío de "cobertura ideal" a "qué cabe en el hueco por tier, priorizado" — ese es el problema real.

Lo demás del diseño existente (ops para el snapshot, un solo parser, refresco en fondo con candado) está bien y no lo tocaría.
