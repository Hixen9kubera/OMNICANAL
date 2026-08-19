Now I have the full context. Here's my critical second opinion:

---

## CRITICISMO DIRECTO

### 1. La reconciliación de volumen es un problema de UNIDADES DE MEDIDA, no de dónde vive el dato

**El diagnóstico está mal.** No es que "la ocupación sea un promedio" o "Amazon mida almacenaje". Es que **estás sumando manzanas (volumen de ítems individuales) y comparándolas contra naranjas (ocupación de bodega medida por Amazon)**.

- Volumen de ítem: A = 500 cm³, B = 300 cm³ → `A + B = 800 cm³` ✓ (lineal)
- Ocupación de bodega: mismo A y B → **palet, espacios, orientación, aisles, handling** → Amazon mide ~1,500 cm³

Son **sistemas de medida distintos**. Un no reconcilia con el otro.

**Factor observed:** 35.98 ÷ 19.43 ≈ **1.85×**. Eso no es ruido — es la diferencia estructural entre "qué hay" y "cuánto espacio toma en la operación real de Amazon".

### 2. La conclusión "tomar la ocupación dada" es correcta PARA VISIBILIDAD, no para decisiones

El dashboard mostraría "35 m³ mientras que nuestros cálculos dicen 19" — eso está bien, es honesto.

**PERO**: Si usas el plan de 1.83 m³ (calculado con volúmenes de ítems) para decidir si cabe en el 5% libre (2.1 m³), **estás multiplicando unidades incomparables**:

```
Plan de envío (volumen de ítems): 1.83 m³
Factor de espacio real: ~1.85×
Impacto REAL en bodega: 1.83 × 1.85 ≈ 3.4 m³
Espacio libre disponible: 2.1 m³
─────────────────────────────────────
Conclusión: NO CABE
```

**La pestaña hoy sugiere un envío que probablemente excede la capacidad física y no lo sabe.**

### 3. ¿Estás resolviendo el problema equivocado? (pregunta 5 — acertaste la intuición)

El objetivo real **NO es "¿cuánto m³ puedo meter?"** — es **"¿debo mandar más a FBA dada la capacidad?"**.

Eso es una decisión BINARIA:
- ¿Tengo <5% libre? (SÍ → riesgo de sobrecapacidad)
- ¿El ROI de FBA sigue siendo positivo a este volumen? (data ajena a esto)
- ¿Tengo stock disponible para mandar? (ya en la BD)

El m³ es un *detalle*, no la pregunta. Hoy lo estás poniendo en el centro por inercia del prompt original.

---

## RESPUESTAS DIRECTAS A TUS PREGUNTAS

### 1. ¿Dónde vive la capacidad?

**`ops.fba_capacity`** — una ÚNICA fila por tier, reemplazada cada mes:

```sql
create table ops.fba_capacity (
    size_tier     text primary key,  -- 'standard' / 'large'
    capacity_m3   numeric(12,2) not null,
    used_m3       numeric(12,2) not null,
    updated_at    timestamptz not null,
    reported_from text  -- "FBA Capacity Monitor 2026-08-19"
);
```

**NO historializar.** Es un límite declarado, no una observación. Cuida `updated_at` para que el código sepa si está fresco — la regla "un None de tabla detenida" aplica: si `updated_at` está a más de 35 días, no decides con ella.

### 2. ¿Calcular tier o pedírselo a Amazon?

**Pedírselo.** Extiende `ops.fba_snapshot` con una columna `size_tier` que venga del reporte de tarifas de Amazon (mismo que da `per_unit_volume`). No inventes reglas de peso + dimensiones.

**Para los 26 SKUs sin dims:** podrían no tener listing en Amazon aún o no estar en su reporte de tarifas. Dejalos SIN clasificar — son candidatos a investigar, no a sumir.

### 3. ¿Tarifas a `costing.costos_finales` o `enrich`?

**`costing.costos_finales` con `canal='amazon'`.**

- Es un COSTO (pertenece a `costing`).
- La PK `(sku, canal)` existe por esto — la desaprovechas hoy.
- Rate limit de 0.5 req/s es un problema de IMPLEMENTACIÓN, no de dónde vive. Crea un job nocturno que sondee las tarifas una vez al día; `enrich` es para datos pequeños y livianos, un caché de "dame el peso de este producto", no "dame los fees de 1,259 items".

**Caveat:** Es un dato que **CAMBIA SIN AVISO** (Amazon puede cambiar fees en cualquier momento). No es como `costos_validados` que es nuestra fuente de verdad. Considera una columna `fetched_at` para saber "qué tan viejas son estas tarifas".

### 4. La brecha de volumen: ¿mostrar los dos números o es error tener dos verdades?

**Mostralos AMBOS**, pero **NO para decisiones de envío**.

El dashboard ya hace lo correcto — muestra `vol_unidad_cm3` vs `vol_costeo_cm3` y avisa cuando divergen 2×. Añade UNA línea:

> ⚠️ El plan se calcula con volúmenes de ítem individuales (5.85 m³ estándar + 10.57 grande). La ocupación real en bodega de Amazon es ~1.85× mayor (15.43 + 20.55 = 35.98). **El impacto real del envío sugerido sería ~3.4 m³, y solo hay 2.1 m³ libres — revisar antes de ejecutar.**

### 5. Hay algo que huele a problema equivocado — SÍ

**Estás intentando predecir warehouse occupancy con item volumes.** No reconcilian, nunca lo harán.

La pregunta útil no es "¿me cabe?" — es:

- **"¿Qué SKUs sin FBA venden más y deberían estar?"** (pestaña ya lo tienes: "sin_fba")
- **"¿Cuáles de los que sí están pagan almacenaje sin vender (sin_venta)?"** (ya los ves en rojo)
- **"¿La capacidad está saturada?"** (Amazon te lo dice con el % de ocupación — úsalo así)

El plan de envío es **secundario**. Hoy lo estás visualizando como si fuera la decisión principal, pero es apenas una de tres cosas que alguien necesita mirar.

---

## SÍNTESIS

| Aspecto | Decisión | Razón |
|---|---|---|
| **Capacidad** | `ops.fba_capacity` (single-row) | Es un LÍMITE, no observable. No historicizar. |
| **Tier** | Traer del reporte de tarifas de Amazon, guardar en `ops.fba_snapshot` | Regla de peso no es confiable. Amazon ya lo publica. |
| **Tarifas** | `costing.costos_finales(sku, canal='amazon')` + job nocturno | Es un COSTO, no un caché. |
| **Volumen** | **STOP calcularlo para decisiones de capacidad** | Item volumes ≠ warehouse occupancy. Usa el % de ocupación que Amazon reporta, que eso SÍ es una métrica integrada. |
| **Plan de envío** | Mantenerlo para visibilidad, pero advertir que es en unidades incomparables | El real es ~1.85× mayor. |

**Lo que necesita el panel AHORA:**
1. Capacidad (tabla simple)
2. % de ocupación (de Amazon, creíble)
3. Candidatos (sin_fba OK, sin_venta OK)
4. Aviso claro: "esto es una sugerencia bruta — validar con Amazon antes de pedir envío"

La métrica "m³ del plan" sale del panel. No aporta.
