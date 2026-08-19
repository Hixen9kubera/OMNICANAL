# Segunda opinión — datos nuevos de Amazon FBA

Leí `fba.py`, `fba_reporte.py`, la migración 0023, el frontend y grepeé quién lee `costos_finales`. Empiezo por 5 y 4 porque son las que más importan.

## 5. ¿Están resolviendo el problema equivocado?

**Sí, parcialmente.** El objetivo declarado es "decidir qué mandar a FBA sabiendo que hay 5% de espacio libre". Pero miren `fba.py`: el cálculo de `sugerido` (línea 173) es `objetivo × vel − disponible − en_camino` — **no sabe que existe una capacidad**. No hay tier, no hay m³ libres, no hay ningún tope. El plan de envío que hoy pinta la pestaña puede sugerir un envío que no cabe y la pestaña no tiene forma de saberlo, tal como ustedes mismos admiten en la sección de brecha.

Si el resultado de esta ronda es "aquí vive la capacidad, aquí vive el tier, aquí viven las tarifas" pero nadie conecta esas tres tablas nuevas al cálculo de `sugerido`, no cerraron el problema — solo lo documentaron. El diseño correcto no es solo "dónde vive el dato", es: **la capacidad debe entrar como restricción del cálculo, no como una tarjeta más al lado.** Concretamente: agrupar por tier, ordenar por urgencia (el mismo semáforo que ya tienen), y recortar `sugerido` cuando el acumulado de m³ del tier se acerque al libre — no calcularlo libre y comparar después. Eso es lo que responde "sabiendo que hay 5% libre"; lo demás es solo mostrar más números.

Priorización sugerida: el schema de capacidad y tier (preguntas 1-2) es trabajo de una tarde. Conectar esos datos al algoritmo de `sugerido` es el verdadero entregable, y no está en el alcance de esta ronda de preguntas — debería estarlo.

## 4. La brecha de volumen

**No, no me parece aceptable mostrar los dos números uno al lado del otro sin explicar por qué difieren** — eso es exactamente la enseñanza de "dos verdades" del incidente de los 964 pedidos, aplicada a una medición en vez de a un estado. Pero tampoco creo que la conclusión "tomarla dada, no calcularla" esté completa; le falta el porqué correcto.

Tienen 4 hipótesis sin verificar y dos de ellas ya casi están descartadas por su propia tabla: los 26 SKUs sin dims cargan solo 3.01 m³ — no explican una brecha de 16.55 m³. Quedan dos hipótesis reales, y **importa cuál es**, porque cambian el diseño:

- **Si Amazon mide volumen de almacenaje** (empaque + pallet/bin, no el ítem) — el factor es estructural y debería ser razonablemente estable en el tiempo. Vale la pena derivar un factor de seguridad empírico y vigilar su deriva.
- **Si la ocupación es un promedio del período** — están comparando una foto de hoy contra un promedio de N días; ningún factor será nunca estable, y no hay corrección posible sin más muestras en el tiempo.

Antes de diseñar la tabla, se resuelve esto barato: pidan a la CAM una segunda lectura del Capacity Monitor en otro momento (o esperen a la de la próxima semana) y comparen contra su snapshot de ese mismo día. Si el % se mueve independiente de sus propias entradas/salidas de inventario, es la hipótesis del promedio. Si se mueve en línea con sus movimientos, es volumen de almacenaje. Además: el reporte de tarifas de almacenamiento (dato #2, que ya van a traer) frecuentemente trae un campo de "cubic feet" a nivel de storage type — puede que la respuesta venga gratis con ese reporte.

Mientras tanto, mi recomendación concreta: **no comparen 1.83 m³ de plan contra 2.10 m³ libres — es un error de unidades, no solo de precisión.** Uno es volumen de producto, el otro (probablemente) es volumen de almacenaje. Etiqueten el número del plan con un factor de seguridad explícito derivado de su propia medición (1.9×–2.6× observado) y muéstrenlo como estimado con rango, no como cifra exacta: *"1.83 m³ de producto ≈ 3.4–4.8 m³ estimados de espacio, contra 2.10 m³ libres — probablemente no cabe."* Eso es honesto sobre la incertidumbre y sigue siendo accionable; mostrar dos números sueltos sin ese puente no lo es.

## 1. Capacidad contratada

`ops`, sí — pero **no como extensión de `fba_snapshot`**. `fba_snapshot` reemplaza la foto completa porque es un estado que se vuelve a pedir diario y no importa el histórico intermedio. La capacidad es lo opuesto: cambia poco, la captura una persona a mano, y **el histórico sí importa** (¿llevan meses al 94%? ¿está creciendo?). Yo la haría una tabla de **inserción, no de reemplazo** — cada captura es una fila nueva (tier, capacidad_m3, usado_m3, capturado_at, capturado_por, fuente='manual: CAM'), y "el valor vigente" es el de `capturado_at` más reciente por tier.

Sobre el riesgo del "None que no significa lo que parece": aquí el riesgo es al revés — no es una fila ausente, es una fila **vieja que se lee como si fuera de hoy**. La defensa no es en la tabla, es en el contrato de lectura: el endpoint debe devolver `dias_desde_captura` explícito, y el frontend debe pintar una advertencia visible pasado cierto umbral (yo pondría ámbar >45 días, dado que es mensual, y rojo/oculto del cálculo de `sugerido` >75). Esto sigue el mismo patrón que ya usan para `refresco` en `fba_reporte.estado()` — no es un patrón nuevo, es aplicar el que ya tienen.

No lo haría config/archivo: config es para lo que se despliega con el código; esto lo actualiza Brandon o la CAM mes a mes y necesita quedar auditado (quién, cuándo), no vivir en un commit.

## 2. Tier: calcular o pedir

**Pedir, sin duda.** Ya tienen el precedente exacto en su propio código: usan `per_unit_volume` medido por Amazon en vez de sus propias dimensiones "por traer los defectos de captura conocidos del costeo" (comentario en `fba.py` línea 25-26). El tier publicado por Amazon es la misma lógica: es la fuente autoritativa, la regla de la CAM es una aproximación de segunda mano de una regla que además tiene excepciones por categoría que Amazon sí conoce y ustedes no.

Tabla **aparte**, no extensión de `fba_snapshot`: son reportes distintos con cadencias distintas (inventario se refresca diario 13:10 UTC; tarifas de almacenamiento típicamente es mensual). Meterlos en la misma tabla obliga a que un refresco diario traiga o pise columnas que no le corresponden, rompiendo justo la garantía que `fba_reporte.py` documenta explícitamente: "el mismo código para las dos puertas de entrada" se volvería "el mismo código para dos reportes distintos", que no es la misma garantía. `ops` sí, por ser otro reporte masivo de catálogo completo, reemplazado por corrida — no `enrich`, que es para llamadas puntuales por ítem con TTL individual; esto se pide para todo el catálogo de una vez, igual que `fba_snapshot`.

Los 26 sin clasificar: no los rellenen con la regla heurística de la CAM como fallback silencioso — eso reintroduce el mismo problema de dos verdades, ahora en tier. Bucket explícito "sin clasificar", visible, fuera del cálculo de capacidad hasta que Amazon los clasifique (probablemente porque nunca tuvieron evento de storage).

## 3. Tarifas: costing o enrich

**Enrich, y no lo debatiría mucho.** Encaja letra por letra con la definición que ya escribieron: "datos que se le piden a una API bajo demanda y se cachean con su propio TTL" — es exactamente `getMyFeesEstimates`: por SKU, bajo demanda, rate-limited, cacheable. Es el mismo molde que `market_listing_metrics` o `listing_weight`.

Sobre meterlo directo en `costos_finales` con `canal='amazon'` porque la PK ya lo soporta: **la until ahora inutilizada PK no es luz verde, es una trampa.** Grepeé quién lee esa tabla — 33 archivos la tocan, y al menos uno (`costos.py:532`) hace `SELECT * FROM costos_finales WHERE sku=%s` **sin filtrar canal**. El propio código ya documenta el peligro (`costing_read.py:10-13`): *"El motor actual [asume] canal='mercado_libre' — cuando el motor sea multi-canal, el llamador pasará el canal."* Eso es una promesa a futuro, no una garantía hoy. Meter filas `canal='amazon'` ahora, antes de auditar esos 33 lectores uno por uno, es reabrir la misma clase de incidente que ya les costó $409,741 — solo que esta vez sería el motor de precios de ML leyendo por accidente un costo de Amazon.

Mi recomendación: enrich ahora; promoción a `costing.costos_finales(sku,'amazon')` como paso **deliberado y posterior**, solo después de auditar los 33 lectores (el mismo tipo de barrido que ya hicieron para el retiro de MySQL, documentado en `docs/BARRIDO_LECTORES.md` — reutilicen ese método).

Un aparte importante: no confundan esto con el pendiente #5 ("comisión de Amazon en pedidos = 0"). `getMyFeesEstimates` es una **estimación** para pricing/planeación; lo que falta en pedidos es la comisión **real cobrada** (Finances API). Son datos distintos con significados distintos — mezclarlos en la misma tabla sería, otra vez, dos verdades bajo un mismo nombre.

## Riesgo no preguntado, pero que vi en el código

`getMyFeesEstimates` recibe un **precio** como input — el fee depende de la banda de precio. La clave de caché en `enrich` no puede ser solo `sku`; necesita `(sku, precio_usado, consultado_at)`, o van a servir un fee estimado con un precio viejo sin saberlo. Y dado el rate limit (0.5 req/s, ~42 min para el catálogo completo), esto necesita el mismo patrón de job en segundo plano con candado que ya construyeron para `fba_reporte.refrescar_en_fondo` — no es un endpoint síncrono.
