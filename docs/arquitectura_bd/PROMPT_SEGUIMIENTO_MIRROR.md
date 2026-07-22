# Prompts de seguimiento — Espejo kubera (para Eduardo y su agente)

> Preparados por Brandon el 2026-07-22 tras el despliegue de v0.13.0.
> **Prompt 1**: pégalo como primer mensaje en una sesión nueva del agente
> (con el repo clonado y actualizado a `main`) para retomar el espejo.
> **Prompt 2**: úsalo de forma recurrente (diario o por corrida) para el
> monitoreo y la limpieza de errores en tiempo real.

---

## PROMPT 1 — Continuación del espejo kubera (sesión principal)

Eres el ingeniero responsable de la migración de datos del proyecto OMNICANAL
(Kubera) hacia la BD centralizada "kubera" (Postgres/Supabase, esquema v4).
Lee `CLAUDE.md` completo antes de tocar nada. Tu trabajo previo (dual-write de
webhooks/costos/channel, ETLs, crons de deltas) sigue siendo la referencia;
esta sesión continúa la fase de ESPEJO DE ESCRITORES que Brandon encargó y que
quedó desplegada en v0.13.0 (commit `c88c637`), APAGADA por flag.

### Qué se construyó en v0.13.0 (contexto completo)

1. **Censo escritor→tablas** (21 entradas): hardcodeado en
   `backend/services/kubera_mirror.py::CENSO` y documentado en el README
   (bitácora v0.13.0). Estados: `a_espejar` (7 seams nuevos),
   `cubierto_por_companero` (lo tuyo: webhooks `SUPABASE_DUAL_WRITE`,
   `canal_inventario`→channel_mirror, costos→costing_mirror, y los upserts de
   `ml_progress`/`amazon_progress` que viajan por channel.listings),
   `gap_sin_destino` (pedidos), `no_aplica` (cachés), `bloqueado` (tokens P3).
2. **Módulo `services/kubera_mirror.py`**: `espejar(origen_py, funcion,
   tabla_mysql, tabla_kubera, operacion, payload, clave)` — se invoca DESPUÉS
   de cada escritura MySQL exitosa; fire-and-forget (executor o hilo daemon),
   try/except total, pool propio de 3 conexiones a `KUBERA_DB_URL`
   (connect_timeout=4, blocking=False), `statement_timeout` y
   `set_config('app.via','kubera_mirror',true)` POR TRANSACCIÓN (compatible
   con tu doctrina del pooler 6543). Upserts idempotentes: `ON CONFLICT DO
   NOTHING` en `ops.webhook_events` (tu UNIQUE de idempotencia), dedup por
   `detail_ref` en `ops.channel_submissions` y `ops.process_log`,
   update-else-insert en `enrich.product_media`. Los blobs NUNCA viajan: va
   resumen + `detail_ref='mysql:<tabla>:<id>'`.
3. **Seams insertados** (siempre tras el éxito MySQL, jamás rompen el flujo):
   - `services/odoo_watch.py::_avisar_campana` → `ops.webhook_events` (canal='odoo')
   - `services/publicar_ready.py::_backlog_ml` y `_anotar_pausa_backlog` → `ops.channel_submissions`
   - `services/publicar.py::_guardar_backlog_ml` y `_guardar_backlog_amazon` → `ops.channel_submissions`
   - `services/imagenes_editor.py::_backlog` → `ops.channel_submissions` (operacion='imagen')
   - `services/imagenes_amazon.py::_cache_put` → `enrich.product_media` (kind='amazon')
   - `services/crear_producto.py::_persistir_log` → `ops.process_log` (proceso='crear')
4. **Registro de intentos**: ring buffer en memoria (500) + contadores por
   (archivo, función, tabla) + TODOS los errores persistidos en la tabla LOCAL
   MySQL **`espejo_kubera_log`** (se crea sola al primer error; columnas
   `resuelto`/`resuelto_ts`). Local a propósito: si Supabase está caído, el
   error se guarda de todos modos.
5. **Página `/migracion`** del panel (+ ítem "Migración" en la navbar) con
   endpoints `GET /api/migracion/estado|eventos|errores` y
   `POST /api/migracion/errores/resolver` (lleva `requiere_api_key`):
   tarjeta por escritor (estado/contadores/latencia/último evento), feed en
   vivo cada 5 s, y la vista **"Errores para limpieza"** agrupada por
   (archivo, tabla, tipo) con ejemplo + payload + botón "Marcar resuelto".
   **Esa lista ES el plan de trabajo de la limpieza.**
6. **Flags** (Railway, sin deploy): `KUBERA_MIRROR_ENABLED` (default false),
   `KUBERA_MIRROR_TABLAS` (CSV de tablas ORIGEN para encendido gradual),
   `KUBERA_DB_URL` (formato pooler, como tu `SUPABASE_DB_URL`).
7. **Pruebas ya ejecutadas** (2026-07-22): inocuidad (flag off = inerte
   0.03 ms/200 llamadas; flag on + BD inalcanzable = llamador intacto <1 ms,
   error registrado local) y corrida real contra un Postgres 16 local con el
   DDL v4 aplicado: idempotencia comprobada por re-envío, dedup por
   detail_ref, upsert de media sin duplicar, y un **error FK inducido** (SKU
   fantasma vs `core.products`) capturado sin interrumpir el flujo, visible y
   resoluble en /migracion (botón probado end-to-end).
8. **Reportes para ti (decisiones pendientes)**:
   - **GAP pedidos**: `pedidos_ml` (ventas de ML/Amazon/Temu/TikTok → pedidos
     Woo) NO tiene destino en el v4. Propuesta de DDL lista en
     `docs/arquitectura_bd/propuesta_ops_orders.sql` (`channel.orders`) — NO
     está aplicada; decide esquema/nombre y aplícala tú en DEV cuando dés el
     GO, y entonces se agrega el seam en `pedidos_ml.sincronizar`.
   - **`enrich.product_media`** no tiene UNIQUE natural: un índice único
     `(sku, kind, source_url)` volvería atómico el upsert del espejo.
   - `ventas_horarias`/`ventas_sync` se clasificaron `no_aplica` (caché
     regenerable de la API de ML) — confirma o corrige en el CENSO.

### Tu misión en esta sesión

1. **Encendido en DEV/staging**: define `KUBERA_DB_URL` apuntando al Supabase
   de DESARROLLO (mismo proyecto de tu `SUPABASE_DB_URL` de staging) y
   `KUBERA_MIRROR_ENABLED=true` en el ambiente staging de Railway. Verifica en
   `/migracion` que las tarjetas pasan a "activo".
2. **Encendido gradual en producción** (REQUIERE el dale de Brandon — regla 3
   de CLAUDE.md): empieza con `KUBERA_MIRROR_TABLAS=crear_logs` (volumen bajo)
   y ve sumando tablas (`amazon_imagenes`, `ml_image_edit_backlog`,
   `ml_backlog`, `amazon_backlog`, `webhook_eventos`) conforme cada una quede
   limpia de errores.
3. **Cachar y limpiar errores**: los grupos de la vista "Errores para
   limpieza" son tu backlog. Para cada grupo: diagnostica la causa raíz
   (FK huérfana → falta el SKU en `core.products` (¿ETL v2? ¿alias en
   `migration.id_map`?); tipo/encoding → corrige el mapeo en
   `kubera_mirror.py`; colisión → decide canónico), aplica el fix, verifica
   que los intentos nuevos salgan OK y ENTONCES marca el grupo resuelto.
4. **No rompas las reglas de la casa**: `backend/vendor/` intocable; los
   flujos vivos de Brandon (pedidos, webhooks, stock) no se tocan; los seams
   de espejar son ADITIVOS — si un espejo estorba, se apaga por flag, no se
   borra el seam; `git pull --rebase` antes de push; versión +0.1 y README
   por cada cambio.
5. **Sin credenciales en el chat ni en el repo** (`espejo_kubera_log` no
   guarda payloads >4 KB y nunca tokens; mantenlo así).

### MUY IMPORTANTE — Cierre de la migración (compromiso con Brandon)

La página `/migracion` y el espejo son ANDAMIAJE TEMPORAL de esta fase.
Cuando la migración termine (corte ejecutado y estable), hay que:

1. **Borrar la interfaz y el andamiaje**: quitar `/migracion` del frontend y
   la navbar, `routers/migracion.py`, las llamadas `espejar()` de los 6
   escritores y `services/kubera_mirror.py`; archivar (dump) y luego dropear
   `espejo_kubera_log`; eliminar los flags `KUBERA_*` de Railway y config.
2. **Entregar la documentación FINAL de la migración** en
   `docs/arquitectura_bd/MIGRACION_FINAL.md` con, como mínimo:
   - **Cómo se hizo**: cronología por fases (F0→F9), quién ejecutó qué, los
     mecanismos usados (ETLs, dual-writes, espejo kubera, deltas) y las
     decisiones tomadas (con fecha y por quién).
   - **Qué se migró**: inventario tabla por tabla ORIGEN→DESTINO con conteos
     de filas al corte, qué se archivó congelado (`legacy_costos_ml`), qué se
     descartó y por qué (cachés), y los GAPs que se crearon (pedidos).
   - **Errores que salieron**: el catálogo COMPLETO de errores capturados por
     el espejo (exporta `espejo_kubera_log` ANTES de droparla) y por tu
     dual-write (`ops.migration_issues`), agrupados por tipo, con causa raíz
     y resolución de cada grupo; y el histórico de actas de
     `migration.reconciliation_runs` (los 14 días en cero de cada dominio).
   Esa documentación es el entregable de cierre: cualquier persona debe poder
   entender qué pasó sin leer el historial de chats.

Al terminar la lectura de este prompt: confirma qué vas a hacer primero,
revisa `/migracion` en staging, y NO enciendas nada en producción sin el dale.

---

## PROMPT 2 — Monitoreo recurrente del espejo (pégalo cada día o tras cada corrida)

Monitorea el espejo kubera del panel OMNICANAL (repo actualizado a `main`,
`CLAUDE.md` leído). SOLO lectura + diagnóstico; cualquier fix va con las
reglas de la casa (rebase, versión, README) y los flujos vivos no se tocan.

1. Lee `GET /api/migracion/estado` (o la página /migracion): anota flags,
   totales ok/error de la corrida y qué tablas están activas.
2. Lee `GET /api/migracion/errores`: para cada grupo NUEVO o creciente
   (archivo, tabla, error_tipo): resume causa probable con el ejemplo y el
   payload; clasifica: FK huérfana / tipo-encoding / colisión / conexión.
3. Cruza con lo existente: ¿el SKU del error está en `core.products`? ¿tiene
   alias en `migration.id_map`? ¿el acta de deltas de hoy
   (`migration.reconciliation_runs`, crons 6:30/6:45 UTC) salió en cero?
4. Entrega un parte con: (a) salud del espejo (ok/error por escritor y
   latencia), (b) grupos de error nuevos con causa y fix propuesto, (c) qué
   grupos ya se pueden marcar resueltos (solo si el fix está verificado),
   (d) siguiente tabla candidata a encender en `KUBERA_MIRROR_TABLAS`.
5. Si el espejo lleva >24 h sin errores nuevos en las tablas encendidas,
   propon el siguiente paso del plan (más tablas, o avanzar fase del corte).
