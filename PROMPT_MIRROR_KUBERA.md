# MISIÓN: Espejo (dual-write) de todos los escritores `.py` hacia la BD centralizada **kubera** + panel de migración en tiempo real

## Contexto (leer antes de tocar nada)

- Repo: panel omnicanal Kubera — FastAPI (`backend/`) + Next.js (`frontend/`),
  deploy automático en Railway desde `main`. **Lee `CLAUDE.md` completo ANTES de
  empezar** (reglas de la casa: vendor intocable, OneDrive, versionado, etc.).
- **BD actual (producción)**: MySQL `u531713409_kubera_ml` (+ 72 tablas `wp_*`
  de WordPress en solo-lectura). Es la que HOY pueblan los `.py` del backend.
  Esquemas de referencia en la raíz del repo: `ESQUEMA_kubera_ml.sql`,
  `ESQUEMA_wordpress.sql`, `ESQUEMA_DATOS_KUBERA.md`.
- **BD destino (centralizada) "kubera"**: Postgres/Supabase con el esquema
  propuesto v4 — archivo `ESQUEMA_kubera_v4_propuesto.sql` en la raíz del repo.
  Esquemas: `core`, `channel`, `costing`, `enrich`, `ops`, `migration`. RLS
  deny-by-default; el backend entra por `SUPABASE_DB_URL` (rol `postgres`) o
  service_role. **Estudia ese archivo completo antes de escribir código**: PKs,
  citext en `sku`, triggers de historial, `set_config('app.via', …)`,
  idempotencia de `ops.webhook_events`, patrón resumen+`detail_ref` (los blobs
  pesados NO viajan a Postgres).
- **Ya existe trabajo de migración de un compañero (Eduardo/José)** y es
  INTOCABLE (regla 4 de CLAUDE.md): `channel_mirror.py`, `costing_mirror.py`,
  ETLs `backend/scripts/etl_*`, `comparar_*`, jobs Railway
  `deltas-costos`/`deltas-channel`, esquema de `canal_inventario` y el
  dual-write de webhooks (`SUPABASE_DUAL_WRITE`). **Tu trabajo EXTIENDE el
  mismo patrón a los escritores que aún no tienen espejo — jamás lo dupliques
  ni lo modifiques.** Si un escritor ya está espejado por ellos, se marca
  "cubierto" en el censo y NO se toca.

## Objetivo

Por cada archivo `.py` del backend que hoy ESCRIBE en la BD actual, añadir un
espejo hacia la BD kubera con estas propiedades **innegociables**:

1. En el MISMO punto del flujo donde se escribe la tabla actual, un
   **try/except ADICIONAL** puebla la tabla equivalente en kubera.
2. **Un error del espejo JAMÁS interrumpe ni degrada el flujo actual**: ni
   excepción propagada, ni latencia añadida (fire-and-forget con
   `asyncio.create_task`, timeout corto en la conexión, nunca `await` en el
   camino crítico).
3. **Cada intento (éxito Y error) se registra** — el propósito de esta fase es
   DESCUBRIR qué errores aparecen (tipos, FKs, huérfanos, colisiones) para
   resolverlos y después centralizar todo a una sola base de datos.
4. Una **interfaz en tiempo real** muestra el avance del espejo, explícita a
   nivel `archivo.py → tablas` (ver sección Interfaz).

## Paso 0 — Censo de escritores (entregable 1)

Escanea `backend/` y produce el MAPA COMPLETO escritor→tablas: archivo `.py` +
función, tabla(s) MySQL que escribe, operación (INSERT/UPDATE/UPSERT),
disparador (webhook / scheduler cada N min / acción de UI), tabla destino en
v4, y estado: `a_espejar` | `cubierto_por_compañero` | `gap_sin_destino` |
`no_aplica` (cachés regenerables). Punto de partida conocido — **verificar y
completar, no asumir**:

| `.py` | Tabla MySQL | Destino v4 | Estado esperado |
|---|---|---|---|
| `services/pedidos_ml.py` | `pedidos_ml` | **NO EXISTE en v4** | `gap_sin_destino` — reportar, proponer DDL, NO crear sin confirmación |
| `services/pedidos_amazon.py` | `pedidos_ml` | ídem | ídem |
| `services/pedidos_m2e.py` | `pedidos_ml` | ídem | ídem |
| `routers/webhooks.py` | `webhook_eventos` (campana) | `ops.webhook_events` | revisar: hay dual-write del compañero — no duplicar |
| `services/odoo_watch.py` | `webhook_eventos` (campana) | `ops.webhook_events` | a espejar (evento liviano) |
| `services/ventas_ml.py` | `ventas_horarias`, `ventas_sync` | caché regenerable | probablemente `no_aplica` — decidir y documentar |
| `services/publicar_ready.py` / `publicar.py` (adaptadores) | `ml_backlog`, `ml_progress`, `amazon_progress` | `ops.channel_submissions` (resumen+detail_ref) / `channel.listings` | listings YA cubierto por `channel_mirror` — solo submissions |
| `services/imagenes_amazon.py` | `amazon_imagenes` | `enrich.product_media` | a espejar |
| scheduler `_job` (sync 15 min) | `canal_inventario` | `channel.listings` | `cubierto_por_compañero` — NO TOCAR |
| pipeline costos | `costos_validados`/`costos_finales` | `costing.*` | `cubierto_por_compañero` — NO TOCAR |

Todo escritor adicional que encuentres (crear, logs, etc.) entra al censo con
el mismo formato. El censo queda hardcodeado en el módulo del espejo (es lo que
alimenta la UI) y documentado en el README.

## Arquitectura del espejo (obligatoria)

1. **Módulo único** `backend/services/kubera_mirror.py`:
   - Conexión propia a Postgres (`KUBERA_DB_URL`; en DEV apunta al Supabase de
     desarrollo). Pool pequeño (2-3), `connect_timeout` 3-5 s. **Jamás
     credenciales hardcodeadas** (ya hubo limpieza de eso en este repo).
   - API: `espejar(origen_py, funcion, tabla_mysql, tabla_kubera, operacion,
     payload: dict)` — se invoca DESPUÉS de la escritura MySQL exitosa;
     internamente `asyncio.create_task(...)` + try/except total.
   - Upserts idempotentes (`INSERT … ON CONFLICT`) respetando las PKs del
     esquema v4; mapeo de tipos (sku→citext, fechas→timestamptz UTC);
     `set_config('app.via', …)` donde el esquema lo aprovecha.
2. **Flags** (leídos de env, apagables sin deploy en Railway):
   - `KUBERA_MIRROR_ENABLED` (default **false**).
   - `KUBERA_MIRROR_TABLAS` (opcional, CSV) para encender por tabla.
3. **Registro de intentos**: ring buffer en memoria (últimos ~500 eventos) +
   contadores acumulados por `(archivo_py, tabla)` + persistencia de ERRORES en
   una tabla local MySQL nueva `espejo_kubera_log` (id, ts, archivo_py,
   funcion, tabla_origen, tabla_destino, operacion, sku/clave, error_tipo,
   error_texto, payload_json). **Local a propósito**: si Supabase está caído,
   el error se tiene que poder guardar de todos modos.
4. **Prueba de inocuidad**: demostrar (test o corrida) que con el flag apagado
   y con la BD kubera INALCANZABLE el flujo actual se comporta idéntico.

## Interfaz en tiempo real (entregable 3) — página `/migracion` del panel

Explícita a nivel **archivo `.py` → tabla**; de ahí salen los errores y después
se hace la limpieza:

- **Una tarjeta por escritor `.py`**: nombre del archivo, tablas que puebla
  (`tabla MySQL → tabla kubera`), estado del espejo (activo / apagado /
  cubierto por compañero / gap), contadores ok/error de la corrida y
  acumulados, último evento con hora, latencia media del espejo.
- **Feed de eventos en vivo** (poll cada 5 s): hora, archivo.py, función,
  tabla origen→destino, operación, resultado; el error se muestra resumido y
  expandible al texto completo.
- **Vista "Errores para limpieza"**: errores agrupados por
  `(archivo.py, tabla, tipo de error)` con conteo y último ejemplo — esta
  lista ES el plan de trabajo de la limpieza posterior. Con botón para marcar
  grupo como "resuelto" (se guarda en `espejo_kubera_log`).
- Endpoints backend: `GET /api/migracion/estado` (censo + contadores + flags),
  `GET /api/migracion/eventos` (ring buffer), `GET /api/migracion/errores`
  (agrupados desde MySQL). Diseño consistente con el panel actual (Tailwind,
  tarjetas, verde=ok / rojo=error / ámbar=apagado / gris=gap).

## Reglas duras

- NO tocar `backend/vendor/` (solo adaptadores) ni NADA de la migración del
  compañero (lista de arriba + regla 4 de CLAUDE.md).
- NO crear tablas nuevas en el esquema v4 sin confirmación explícita: los GAPs
  (p. ej. `pedidos_ml`) se REPORTAN con propuesta de DDL, no se improvisan.
- El código puede subirse a `main` con el flag APAGADO (es inerte). **Encender
  `KUBERA_MIRROR_ENABLED` en producción = cambio de flujo vivo: mostrar qué se
  enciende y esperar el dale explícito de Brandon** (regla 3 de CLAUDE.md).
- Primero probar contra el Supabase DEV: filas reales verificadas por consulta,
  y al menos un error INDUCIDO (p. ej. FK huérfana) visible en `/migracion`.
- `git pull --rebase` antes de push; versión `+0.1` en `backend/main.py` (dos
  lugares); entrada detallada en README y actualización de CLAUDE.md.
- El repo vive en OneDrive: re-Read antes de Edit si hay dudas.

## Entregables

1. Censo escritor→tablas (en el módulo + README), con gaps y cubiertos.
2. `services/kubera_mirror.py` + llamadas de espejo en cada escritor censado.
3. Página `/migracion` + endpoints, funcionando con datos reales de DEV.
4. README + CLAUDE.md actualizados (versión, flags nuevos, tabla nueva).
5. Demo final: corrida real mostrando eventos OK y errores capturados SIN
   interrumpir el flujo, y la lista de errores agrupados lista para limpieza.
