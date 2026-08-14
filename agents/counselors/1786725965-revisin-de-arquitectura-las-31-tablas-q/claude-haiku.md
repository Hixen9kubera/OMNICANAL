# Revisión crítica: arquitectura de desmantelamiento de 31 tablas MySQL

## 1. GRUPO 4 (ml_progress): La respuesta es "SPLIT", no espejo 1:1

**Veredicto: rechazaría la gemela 1:1.** Los datos lo dicen: `channel.listings` es superset + fresher en 63 casos. Eso no es redundancia tolerada — es un **registro dual de verdad con inconsistencia operativa**.

Pero la propuesta de split requiere cuidado quirúrgico. Los lectores de `ml_progress` se agrupan en **3 patronales, no 2:**

| Patrón | Lectura | Destino | Ejemplos |
|---|---|---|---|
| **STATE: "¿está vivo?"** | `ml_item_id` para decisiones | `channel.listings` | `publicar._ml_publicaciones` (evita re-publicar), `inventario` (identidad sku↔item), `meli.py` (feed del panel) |
| **HISTORY: "¿qué pasó?"** | `error`, `gtin_error`, `published_at` | `ops.channel_submissions` | `studio`, paneles, debugging |
| **CENSUS: "¿cuántos éxitos?"** | contador de `success` | `channel.listings.meta` o agregado | contadores, `presencia` |

**El riesgo de la decisión**: los 19 lectores no se reparten parejamente. Buscaría:
- `inventario.py` líneas 5, ¿qué preguntan? Si es "¿en qué cuentas existe?", `listings` alcanza; si "¿por qué no se publicó X?", necesita `submissions`.
- `competencia_captura.py`, ídem.
- `publicar._product_type_amazon` (regla 2 histórico): ¿compara `amazon_progress.product_type` contra panel? Si sí, el histórico va a `submissions`; el tipo actual a `listings`.

**No elegir** una gemela 1:1 sin hacer eso. Es re-crear la condición de los 964: dos fuentes que dicen verdades distintas a la misma pregunta.

---

## 2. ORDEN: Debate válido, pero el argumento METODOLÓGICO gana — Grupo 4 PRIMERO

**Cambiaría el orden** a `4 → 5 → 2 → 3`, razones:

**En favor de 4 primero:**
- Los 19 lectores de `ml_progress` son la **complejidad máxima**. Si los pierdes ahora, pierdes la metodología mientras está fresca.
- El barrido de readers es un skill no trivial — Eduardo acaba de hacerlo (v0.117–v0.127); mantener eso caliente es prudente.
- Es la tabla **con mayor riesgo de regresión silenciosa** (como los 964: un `None` donde esperabas un id, silencio en decide).
- Los dos últimos intentos de freezing (11–12-ago) fallaron PORQUE faltó iterar sobre todos los lectores. Hay memoria institucional viva **hoy**. En 3 semanas habrá rotado.

**En contra:**
- Menos confianza: primer test del procedimiento será sobre la tabla que menos podés permitirte equivocarte.
- Pero ese test **sobre grupo 4** es inevitable; adelantarlo no aumenta riesgo global, solo la visibilidad.

**Verdict**: la ganancia de mantener la metodología viva pesa más que la ganancia de "ganar confianza con 5 primero". El costo de re-aprender el método si se olvida supera el costo de una iteración más cuidadosa ahora.

---

## 3. BÚSQUEDA INCOMPLETA: Hay lectores huérfanos

Tres blindspots en el barrido con grep:

**A) `backend/scripts/`** — aunque no son "flujos vivos", algunos se corren en prod:
```python
# Adivino estos existen pero no viste:
# alinear_ml_drop, alinear_amazon_drop, corregir_status_publicados, 
# sincronizar_ml_huerfanas → todos LEEN ml_progress / amazon_progress
```
No migrados todavía (pendientes en F8 del CLAUDE.md). **No pueden congelarse las tablas mientras estos scripts sigan en circulación.** Necesitás:
- Audit: git grep `ml_progress` en `backend/scripts/` + ejecutar esos scripts manualmente (quién los corre, con qué frecuencia)
- Repuntar ANTES de congelar, o marcarlos "ARCHIVADO" en el CLAUDE.md para que la próxima persona no los corra.

**B) Repos externos (es el precedente real)** — el brief menciona `MLREgisterDaily` renovando tokens:
```python
# ¿Hay otros?
# Alibababot / KuberaPipelineV1.0 (retirado, pero alguien podría re-levantarlo)
# Make.com workflows (José's dashboards — ¿leen ml_progress desde API?)
# Cualquier Zapier / IFTTT que apunte a la base
```
**Action**: buscar en `git log --all` cada repo externo mencionado, y luego git grep + curl calls en ellos.

**C) Frontend** — menos probable pero real:
- ¿El panel fetch de `/api/...` algo que termina con `SELECT ... FROM ml_progress`? 
- Algunos routers tienen lógica de lectura _implícita_ (p. ej. un `GET /api/sku/XYZ` que embebe `ml_progress` en la respuesta).
- **Grep:** buscar en routers dónde se construye JSON response que incluya fields de `ml_progress`.

---

## 4. `fanout_log._ya_compensado`: Esto es un ESTADO simulado, no una bitácora

**El patrón es tóxico.** Tres hechos:

1. La lógica es:
   ```python
   SELECT id FROM fanout_log WHERE item_id=X AND accion='full_compensado' LIMIT 1
   ```
   Estás preguntando **"¿ocurrió este evento?"** usando una bitácora como caché de estado. Es como preguntar la hora consultando el historial de cambios del reloj.

2. El `except Exception: return False` **es la bomba**: si la tabla desaparece (congelada o borrada), el código decide "no compensó" basado en NO SABER, no en SABER que no ocurrió. Ejemplo perfecto de los 964.

3. `stock_full.py:354` parsea el campo `resultado` con regex — **cero schema enforcement**, solo texto libre. Si alguien cambia el formato del mensaje o hay un typo histórico, la decisión falla silenciosamente.

**Propuesta arquitectónica:**
- Crear columna `channel.orders._compensated_stock_returned` (boolean, default false, set by `pedidos_ml.sincronizar`).
- Mover la lógica de "¿ya compensé?" de bitácora a este flag.
- `fanout_log` sigue siendo bitácora (cuándo, qué, por qué), pero no toma decisiones.
- El estado vive en donde vive el orden: `channel.orders`.

**Si eso es too much ahora**, mínimo:
- Cambia `except Exception: return False` a `except: return await read_kubera_fallback(...)`. El fallback no es "no sé", es "estoy caído".
- Documenta el formato esperado de `resultado` en un enum, no regex.

---

## 5. CRITERIO: Archive + Document + Delay = bajo riesgo

**Acuerdo con "no readers → archive"**, pero con procesal:

| Tabla | Acción | Razón | Timing |
|---|---|---|---|
| `ml_backlog`, `amazon_backlog` (246 MB) | Archive (SQL dump) | Cero lectores; reconstruible desde APIs; tamaño masivo | AHORA (libera disco) |
| `scraping_alibaba`, `atributos_ia`, etc. | Archive (ya hecho) | Ya respaldado en Drive | — |
| `stock_watch_foto` | **HOLD**, mover a kubera | Es fuente de `channel.listings`, reclasificar como MIGRAR, no archivar | después de que channel.listings lea DIRECTO desde stock_watch_foto en kubera |

**Sobre forense**: los 246 MB de payloads tiene **muy bajo valor histórico** (ML publica cada 24 h, hay histórico de publicaciones en ML). **Pero** antes de purgar, **exporta un JSON de estadísticas**:
```python
{
  "ml_backlog_stats": {
    "total_rows": 60_000,
    "size_mb": 60,
    "date_range": "2025-11-01 to 2026-08-14",
    "published_at_distribution": {...}
  }
}
```
Para poder responder "¿cuántas publicaciones fallaron el mes 7?" sin reproducir los datos.

---

## 6. RIESGO DE SECUENCIA: Sí existe, y es `stock_watch_foto`

**La dependencia es real y bidireccional:**
```
stock_watch_foto (MySQL) 
  ↓ sync (channel_mirror.py:298)
channel.listings (kubera, canal "general")
  ↓ lectura
fanout_stock.py (decide qué publicaciones reciben stock)
  ↓ reabastece inventario
channel.listings (se actualiza)
```

**Riesgos concretos si la toques fuera de orden:**

1. Si congelas `stock_watch_foto` ANTES de que `channel_mirror.py` lea directo desde kubera:
   - El sync de 15 min vuelve a traer `None` cada vez.
   - El feed "stock real del DROP" muestra 0 para todo.
   - No despiertan las alertas de stock bajo.

2. Si borras antes de verificar que `channel.listings` está completo:
   - Pierdes 20 min de historia (el sync está 15 min atrás).
   - Alguien hace restock en ese gap → no se ve.

**Propuesta de secuencia ajustada:**

| Paso | Grupo | Verificación |
|---|---|---|
| 1 | 5 (márgenes) | 1 lector + 1 escritor, sencillo |
| 2 | 6 (tokens) | Paralelo, depende de MLREgisterDaily externo |
| 3 | **stock_watch_foto MIGRACIÓN** | Antes de congelar (desacoplamiento de MySQL) |
| 4 | 2 resto (caches) | Ahora sí sin dependencias vivas |
| 5 | 3 (bitácoras) | Validar que espejo kubera está completo |
| 6 | 4 (publisher) | El más costoso, metodología ya practicada |
| 7 | 7 (andamiaje) | Al final siempre |

---

## 7. LO QUE ME PREOCUPA (no me dueles, son blindspots)

1. **Test suite**: ¿hay tests que mockeaban estas tablas? Si los tests pasaban con mocks de MySQL pero el código en prod decide leyendo las tablas reales, podrías tener sorpresas silenciosas. Verifica que los tests NO estén mockeando `ml_progress` o `fanout_log`.

2. **Estados de crash/recovery**: ¿qué pasa si kubera cae DURANTE la lectura de uno de estos campos críticos (p. ej. mientras `fanout_stock` está iterando `channel.listings.stock`)? El fallback a MySQL de esa lectura es correcto, pero necesitás verificar que:
   - El flag `SUPABASE_READ_*` para cada tabla existe.
   - El fallback a MySQL no introduce un lag que luego pisa decisiones.

3. **Atómicas del doble-write**: `kubera_mirror.py` escribe kubera primero, luego espejo. ¿Si falla a mitad en la lectura dependiente (p. ej. `fanout_stock` consulta antes de que el espejo escriba)? Tiene que estar documentado que la lectura de esa tabla siempre va a kubera cuando se está escribiendo (regla que ya está en el código, pero es frágil).

---

## Resumen ejecutivo

| Pregunta | Respuesta |
|---|---|
| **1. ¿Gemela 1:1 vs split?** | Split (estado → listings, historia → submissions). Pero requiere barrido de intent en los 19 lectores. |
| **2. ¿Orden?** | 4 → 5 → 2 → 3. El group 4 primero MIENTRAS la metodología está fresca. |
| **3. ¿Qué se me escapa?** | Scripts en backend/scripts/ + repos externos + frontend responses que embeban estos campos. Audit requerido. |
| **4. fanout_log?** | Es un estado disfrazado de bitácora + except silencioso + regex frágil. Mover estado a channel.orders.\_compensated. |
| **5. Archive/migrate?** | Archivo si no hay lectores (backlog), **pero documenta estadísticas antes de purgar**. stock_watch_foto es MIGRACIÓN, no archivo. |
| **6. Dependencias?** | stock_watch_foto → channel.listings → fanout_stock. Riesgo real. Desacoplar ANTES de congelar. |

**Lo que cambiaría en la propuesta original**: orden + stock_watch_foto + audit de scripts + rewrite de fanout_log → nuevo riesgo de regresión de 0.
