# Procesos de los ETLs pendientes — BD kubera (esquema v4)

> Definición operativa de cada ETL que falta construir, con su proceso paso a
> paso. Complementa al plan maestro v4/v4.1. Autor: Eduardo (migración).
> Fecha: 2026-07-22.

## El esqueleto común (aplica a TODOS)

Todo ETL de esta migración sigue la misma liturgia de 6 pasos — es la que ya
usan `etl_core_products` y los espejos, y la que exige el plan:

1. **Línea base** — contar filas de la fuente (MySQL) y del destino (kubera)
   ANTES de tocar nada; si es carga de única vez, respaldar la fuente (dump o
   SELECT completo a archivo).
2. **Extracción con candado** — una conexión corta a MySQL (límite de
   Hostinger), lectura por lotes; el script se niega a arrancar si detecta
   ambiente equivocado (patrón `validar_ambiente`).
3. **Transformación con las reglas de identidad** — el SKU se normaliza
   (trim, colapso de espacios) y se resuelve contra `migration.id_map`
   (alias → canónico). Un SKU inválido NO se descarta en silencio: se registra
   en `ops.migration_issues` y se sigue.
4. **Carga idempotente** — SIEMPRE upsert (`ON CONFLICT ... DO UPDATE`
   solo-si-cambió, o `DO NOTHING` para bitácoras). Correr el ETL dos veces
   debe dar el mismo resultado. Los blobs nunca viajan: resumen +
   `detail_ref='mysql:<tabla>:<id>'`.
5. **Verificación** — conteos fuente vs destino, checksums de sumas para
   columnas de dinero, y muestreo de N filas al azar comparadas campo a campo.
6. **Acta** — el resultado (conteos, checksums, divergencias) se escribe en
   `migration.reconciliation_runs`. Sin acta, la corrida no existió.

Con eso de base, lo específico de cada uno:

---

## 1. ETL v2 de `core.products` (reescritura — el bloqueante)

**Fuentes:** `productos` (MySQL), Odoo (`default_code`), Woo (`_sku` vía
lectura directa a la BD de WP), packing lists (`costos_validados`).

**Proceso:**
1. Cargar `migration.id_map` COMPLETO a memoria (alias → canónico). **Jamás
   truncarla** — es persistente desde v4.1; solo se le AGREGAN alias.
2. Leer las 4 fuentes y unificar por SKU canónico: cada SKU pasa por
   normalización mecánica (trim, espacios internos según reglas A del mapa) y
   luego por `id_map`. Si la normalización produce un canónico nuevo que no
   estaba en `id_map`, se inserta el alias (no se pierde la grafía original).
3. Clasificar cada SKU con la taxonomía v4.1 (`ok`, `whitespace`,
   `colision_caso`, `colision_llave`, `nombre_no_coincide`,
   `marketplace_only`, `placeholder`) → los no-ok van a
   `ops.migration_issues` con su clasificación, PERO el registro se carga de
   todos modos con su mejor canónico (normalizar+aliasar, no rechazar).
4. **Incremental**: comparar contra lo ya cargado (hash por fila o
   updated_at) y upsert solo lo que cambió. El full-refresh queda solo como
   modo `--rebuild` explícito.
5. Verificación extra propia: 0 colisiones citext nuevas, y conteo de
   `marketplace_only` estable (si crece, algo publica SKUs fuera del maestro).

**Frecuencia:** diaria (cron Railway, después de los deltas) hasta el corte.

---

## 2a. Backfill `pedidos_ml` → `channel.orders` (única vez)

**Fuente:** `pedidos_ml` (~2,900 filas). **Destino:** `channel.orders` (ya
creada, GO 2026-07-22).

**Proceso:**
1. Respaldo de `pedidos_ml` completo a archivo.
2. Mapeo directo documentado en `propuesta_ops_orders.sql`:
   `ml_order_id→external_order_id`, `cuenta→cuenta` (+ resolver `account_id`
   por `core.accounts.legacy_code` cuando exista), `estado_ml→estado_canal`,
   `es_full→es_fulfillment`, `creado→creado_at`.
3. La columna `skus` (CSV varchar 255, TRUNCADA en pedidos largos): partir por
   coma → array `citext[]`; si la última entrada parece cortada (no cumple el
   patrón de SKU), marcar el pedido en `ops.migration_issues`
   (`skus_truncados`) para reconstruirlo desde las líneas del pedido Woo
   (`wc_order_id` → line items por REST).
4. Derivar `canal` de la cuenta (BEKURA/SANCORFASHION→mercado_libre,
   AMAZON→amazon, TEMU/TIKTOK→temu/tiktok).
5. Upsert por PK `(canal, cuenta, external_order_id)` — re-correr es inocuo.
6. Verificación: conteo por cuenta y suma de `total` por mes, fuente vs
   destino, deben cuadrar al centavo.

**Después del backfill:** entra el seam del espejo kubera en
`pedidos_ml.sincronizar` (lo construye el equipo del espejo) y el dominio
queda vivo.

## 2b. `comparar_pedidos.py` (recurrente — el auditor del dominio)

Clon de `comparar_costos.py` con 3 niveles: conteos por cuenta/mes, checksum
de `sum(total)` y `sum(comision)`, y fila-por-fila de los últimos 30 días
(campos: estados, total, comision, es_fulfillment). Excluye pedidos
"calientes" (actualizados durante la corrida). Acta con dominio
`pedidos-deltas`. Cron 06:55 UTC (tras costos y channel). Criterio: 14 días
en cero, como todos.

---

## 3. ETL de categorías → `channel.categories` + `channel.product_category`

**Fuentes:** `categorias_ml` (12,702 filas: sku→ml_cat_id curada), árbol de
categorías de Woo (`{P}terms`/`term_taxonomy` por lectura directa), product
types de Amazon (`amazon_progress.product_type` + metas `amz_product_type`).

**Proceso:**
1. **Primero el árbol, luego la asignación** (FK obliga): upsert de
   `channel.categories` por canal — ML: los `ml_cat_id` únicos usados +
   nombre/path vía API `/categories/{id}` (cache local para no golpear la
   API); Woo: el árbol completo de terms; Amazon: los product types usados.
2. Asignación `channel.product_category` (PK sku, channel_id): resolver SKU
   por `id_map`; prioridad de fuente para ML = la regla del panel
   (meta `ml_categoria_id` del picker > `categorias_ml` > histórico).
3. Huérfanos (SKU sin fila en `core.products`) → `ops.migration_issues`,
   no se cargan (aquí sí, porque la FK lo impide — la solución es que el ETL
   v2 de core corra ANTES en el mismo cron).
4. Verificación: conteo de asignaciones por canal vs fuente; muestreo de 20
   SKUs comparando la categoría en vivo del listing ML vs la cargada.

**Frecuencia:** diaria tras el ETL de core (la asignación cambia cuando el
equipo re-categoriza).

---

## 4. Backfill de bitácoras → `ops.channel_submissions` + `ops.process_log`

**Fuentes → destino:**
- `ml_backlog` (6,734), `amazon_backlog` (4,456), `ml_image_edit_backlog`
  (11,525) → `channel_submissions`
- `crear_logs` (361), `costos_logs` (135), `odoo_sync_backlog` (1,649),
  `odoo_sync_procesados` (3,980), `sync_procesados` (2,628),
  `backlog_errores` (1,904) → `process_log`

**Proceso (única vez, el mismo patrón del seam del espejo):**
1. Leer por lotes de 500 con cursor por id.
2. Por fila: construir el RESUMEN (sku, operación, éxito/error, timestamps,
   primeros 500 chars del detalle) + `detail_ref='mysql:<tabla>:<id>'`.
   **El payload/blob NO viaja** — los 221 MB de `amazon_backlog`+`ml_backlog`
   se quedan en MySQL hasta su deprecación con dumps.
3. Dedup por `detail_ref` (`ON CONFLICT DO NOTHING`) — coexiste sin chocar
   con lo que el seam del espejo ya haya escrito en vivo.
4. Verificación: conteo por tabla origen = conteo por `detail_ref` con ese
   prefijo en destino.

**Frecuencia:** única vez (lo vivo ya lo cubre el espejo kubera).

---

## 5. ETLs de enrich (cargas de única vez + upsert diario ligero)

**5a. `scraping_alibaba` → `enrich.supplier_data`:** mapeo directo + la regla
del bug de moneda: `price_currency_verified=false` para TODO lo histórico (el
scraper marcaba "USD" fijo sin verificar); solo el flujo nuevo podrá marcarla
true. SKU por `id_map`; huérfanos a issues.

**5b. `atributos_ia` → `enrich.ai_attributes`:** mapeo directo (sku,
atributos json, modelo/fecha si existen). Idempotente por sku.

**5c. `imagenes_producto` + `amazon_imagenes` → `enrich.product_media`:**
`kind='galeria'` para la primera, `kind='amazon'` para la segunda; upsert
atómico por el índice único `(sku, kind, source_url)` que ya existe. El seam
del espejo mantiene lo nuevo de Amazon.

**5d. `odoo_ranking` → `enrich.odoo_viability`:** copia snapshot con fecha.
**Condición previa:** decisión sobre KuberaPipeline (su dueño) — si el
pipeline se retira, esta carga es el funeral de la tabla; si se re-apunta,
el ETL debe ser recurrente.

---

## 6. Seeds de costing (única vez, 30 minutos)

**6a. `costos_ml` → `costing.legacy_costos_ml`:** copia LITERAL (sin FK, sin
normalizar importes — la regla dura del plan: NUNCA fusionar sus precios con
costos_finales; 96.6% difieren). Verificación: conteo + checksum de
`sum(precio)` idénticos. Después de esto, `costos_ml` queda lista para
rename lógico + gracia de 30-90 días.

**6b. `costing.fx_rates` + `costing.pricing_params`:** INSERT de semillas con
`valid_from` = fecha del corte de captura: TC USD 18.5, MARGEN 0.48,
IVA 0.16, DESCUENTO_BASE 0.16, TARIFA_CBM_M3 7500. A partir de ahí, cambiar
un parámetro = INSERT con nueva vigencia (nunca UPDATE) — el historial de
precios se vuelve reproducible. El backend y el frontend migran a leer de
aquí en F5 (muere el hardcode).

---

## 7. Builder de `enrich.marketplace_identity` (nuevo — no lee MySQL)

**Fuentes:** APIs vivas — ML `/users/{id}/items/search` + `/items` (ambas
cuentas: SELLER_SKU + título + status), Amazon Listings Items (seller
SKU + título), Woo (`_sku` + nombre por lectura WP).

**Proceso:**
1. Barrido paginado por cuenta/canal (respetando rate limits; el patrón de
   scan ya existe en `inventario.py`).
2. Por cada listing: upsert `(sku_marketplace, canal, cuenta, titulo,
   item_id, status, visto_at)`.
3. Cruce contra `core.products` (vía `id_map`): los `marketplace_only`
   (publicados que el maestro no conoce) y los `nombre_no_coincide` (mismo
   SKU, producto distinto — el caso TEC-0492/ORG-0398 de SKUs reciclados)
   van a `ops.migration_issues`.
4. Esta tabla es LA fuente de verdad de identidad para decisiones de
   limpieza — lo que en julio hicimos a mano consultando la API item por
   item (caso SIL), automatizado.

**Frecuencia:** diaria o cada 2 días (es la más pesada en llamadas de API;
correrla en madrugada tras el snapshot de dailytrack para no competir).

---

## 8. Cron de auditoría nocturna de identidad (v4.1)

No carga datos: **vigila**. Corre la clasificación de la taxonomía v4.1
sobre `core.products` + `marketplace_identity` + Odoo, y deja en
`ops.migration_issues` SOLO los hallazgos NUEVOS respecto a ayer (dedup por
sku+motivo). Su métrica de éxito es aburrida: 0 issues nuevos por noche.
Es el guardián de que la limpieza del 21-jul no se vuelva a ensuciar.

---

## Dependencias entre ellos (el orden no es negociable)

```
ETL v2 core ──► categorías ──┐
     │                       ├──► (todos verificados por sus comparadores)
     ├──► enrich (5a-5d)     │
     └──► backfill pedidos ──► comparar_pedidos (cron)
seeds costing (independiente)
bitácoras (independiente)
marketplace_identity ──► cron nocturno de identidad
```

Regla final: ningún dominio entra a su racha de 14 días hasta que su ETL de
backfill cuadró conteos y su comparador dejó la primera acta en cero.
