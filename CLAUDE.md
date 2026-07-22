# CLAUDE.md — Contexto del proyecto OMNICANAL · Kubera

> Este archivo existe para que cualquier sesión de Claude (u otra persona)
> entienda el proyecto SIN leer todo el historial. Última gran actualización:
> **2026-07-20 (v0.12.0)**. El changelog detallado versión por versión vive en
> [README.md](README.md) (sección "bitácora").

## Qué es esto

Panel omnicanal de **Kubera** (Brandon, brandon@kubera.mx): FastAPI
(`backend/`) + Next.js App Router (`frontend/`), desplegado en **Railway**
(proyecto `Hixen9Proyects`, auto-deploy desde `main`). Gestiona el catálogo de
**WooCommerce (chunche.shop)** y su presencia en **Mercado Libre (2 cuentas:
BEKURA="Kubera" y SANCORFASHION="San Corpe")**, **Amazon** (San Corpe) y, vía
**M2E Cloud**, **Temu/TikTok**. El **SKU** une todo.

## ESTADO OPERATIVO ACTUAL (la verdad desde el 17-jul-2026)

- **WooCommerce es la FUENTE DE VERDAD de ventas E inventario.** Odoo está en
  retiro: su stock se cargó a Woo el 17-jul (525 correcciones) y un vigilante
  (`odoo_watch`, cada 30 min) solo AVISA de cambios en Odoo por la campana
  (auto_push APAGADO para no pisar a Woo).
- **Cada venta se congela como PEDIDO de WooCommerce** con el precio real de
  venta, comisión y neto en metas `_ml_*` (los precios de catálogo cambian a
  diario; el pedido es el registro histórico). Tabla de control: `pedidos_ml`
  (PK = id de orden del marketplace; columna `cuenta` distingue el origen).
- **Flujos vivos ahora mismo** (todos en el scheduler del backend o webhook):
  | Flujo | Mecanismo | Frecuencia |
  |---|---|---|
  | Ventas ML → pedidos WC | Webhook `orders_v2` (app ML `8902165405612832` → `/api/webhooks/ml`) | segundos |
  | Ventas Amazon → pedidos | Sondeo SP-API Orders (`pedidos_amazon.py`) | 5 min |
  | Ventas Temu/TikTok → pedidos | Sondeo M2E `order/find` (`pedidos_m2e.py`) | 10 min |
  | Sync inventario ML+Amazon → `canal_inventario` | `scheduler._job` | 15 min |
  | Vigilante Odoo | `odoo_watch.revisar` | 30 min |
- **Stock en pedidos**: ML FULL (`logistic_type=fulfillment`) y Amazon FBA
  (canal AFN) nacen con `_order_stock_reduced=yes` → NO tocan bodega (salen del
  almacén del marketplace). No-FULL / MFN / Temu / TikTok SÍ descuentan
  (`PEDIDOS_WC_DESCUENTA_STOCK=true` desde el día 1, decisión de Brandon).
  Candado de cancelación: a un pedido protegido se le quita la marca ANTES de
  cancelarlo (si no, Woo "devolvería" stock que nunca salió).
- **Tab VENTAS del panel = 100% `pedidos_ml`** (fuente `pedidos`): General suma
  todas las cuentas; el canal filtra; comparativa semanal desde el 24-jul
  ("s/ base" antes). `?fuente=ml` conserva la vista histórica de la API de ML
  (requiere reencender `VENTAS_ML_REFRESH`). Guía de reconciliación de métricas:
  memoria de sesión + README v0.9.
- **Catálogo ML 100% Premium** (`gold_pro`) desde el 17-jul; el publicador y
  `costos.py` ya asumen comisión Premium.

## REGLAS DE LA CASA (violarlas ya causó incidentes reales)

1. **`backend/vendor/` NO SE TOCA** — es el pipeline que publicó 1,200+
   productos. Se ajustan los ADAPTADORES (`services/publicar_ready.py`,
   `services/publicar.py`). Excepción sancionada: `vendor/ml_ready/
   size_chart_mapping.py` es CONFIG (ahí se registran guías de tallas).
2. **La elección del PANEL manda sobre cualquier detector automático.**
   ML: meta `ml_categoria_id` (picker) > `ml_category_id` (predictor de Crear).
   Amazon: meta `amz_product_type` > histórico `amazon_progress` > detección
   por título. (Caso real: TEC-1812-NEG se publicó en "Máquinas de Coser"
   siendo "Máquinas Sexuales" por ignorar el panel.)
3. **Cambios que ENCIENDEN/APAGAN flujos de negocio vivos** (webhooks, pedidos,
   stock masivo, variables de producción): mostrar QUÉ se va a encender y
   esperar el dale de Brandon ANTES del push. Features de UI/lectura: deploy
   directo a `main` (regla vieja de Brandon, sigue viva para eso).
4. **NO tocar NADA de la migración de Eduardo/José**: esquemas Supabase
   (`core`, `channel`, `costing`, `ops`, `migration`), espejos
   (`channel_mirror.py`, `costing_mirror.py`), ETLs (`backend/scripts/etl_*`,
   `comparar_*`), jobs Railway `deltas-costos`/`deltas-channel`, ni el esquema
   de `canal_inventario`. El sync de 15 min ALIMENTA su espejo — no apagarlo
   sin coordinar (`SYNC_ENABLED=false` del 17-20 jul les congeló la observación
   3 días). Su regla de corte: 14 días de actas en cero.
5. **LiteSpeed cachea chunche.shop**: TODA lectura de galería/producto que
   alimente una escritura lleva `_cb` (cache-bust). Ya causó un revert de
   imágenes editadas.
6. **ML manda webhooks EN RÁFAGA** (misma orden, milisegundos): el candado
   `asyncio.Lock` por orden en `pedidos_ml.sincronizar` es lo único que evita
   pedidos duplicados (el 17-jul nacieron 164 duplicados antes del lock).
7. **`_order_stock_reduced` es meta interna de Woo INVISIBLE por REST** — no
   decidir nada leyéndola por API (la limpieza del 17-jul canceló de más por
   fiarse de esa lectura; Woo aguantó por su contabilidad por línea
   `_reduced_stock`).
8. **Tokens ML**: los renueva un proceso externo irregular; si mueren, el
   backend se auto-sana en `meli.obtener_orden` (401 → refresh con candado por
   cuenta). Si los pedidos paran: 1º revisar `ml_tokens_dashboard.updated_at`,
   2º probar el token con `/users/me`.
9. **Equipo activo en `main`**: siempre `git pull --rebase` antes de push.
   Commits con changelog; versión `+0.1` en `backend/main.py` (dos lugares) y
   entrada DETALLADA en README por cada feature.
10. **El repo vive en OneDrive**: los archivos pueden cambiar bajo tus pies —
    re-Read antes de Edit si hay dudas.

## Mapa rápido de piezas propias

| Pieza | Archivo | Qué hace |
|---|---|---|
| Pedidos ML→WC | `backend/services/pedidos_ml.py` | Orden ML → pedido Woo (precio congelado, idempotente, lock por orden, candado cancelación) |
| Orden completa ML | `backend/services/meli.py::obtener_orden` | Fetch + FULL por shipment + auto-refresh de token en 401 |
| Pedidos Amazon | `backend/services/pedidos_amazon.py` | Poll SP-API; FBA protegido / MFN descuenta; `creado`=PurchaseDate |
| Pedidos Temu/TikTok | `backend/services/pedidos_m2e.py` | Poll M2E `order/find`; esquema de orden se confirma con la 1ª venta real (log de crudos) |
| Tab Ventas | `backend/services/ventas_ml.py` + `frontend/app/ventas/page.tsx` | `resumen_pedidos` (fuente pedidos_ml) + vista ML histórica con caché (`ventas_horarias`/`ventas_sync`) |
| Vigilante Odoo | `backend/services/odoo_watch.py` | Foto vs foto de qty_available → campana; auto_push opcional |
| Imágenes Amazon | `backend/services/imagenes_amazon.py` | WebP→JPEG, ≥1000px (Lanczos, fallback Real-ESRGAN), caché `amazon_imagenes` |
| Editor imágenes IA | `backend/services/imagenes_editor.py` | Gemini por flags (fondo/traducir/logos/modelo); un solo PUT a la galería |
| Atributos ML (IA) | `backend/services/ml_atributos.py` | Prompt canónico + DeepSeek; guarda metas `ml_attr_<ID>` (lo que lee el publisher) |
| Tipo Amazon (picker) | `backend/routers/publicar.py` + `frontend/components/TipoAmazonPicker.tsx` | Ver/buscar/guardar product type; prioridad panel |
| Sync Odoo→Woo | `backend/services/sync_woo.py` (`POST /api/sync/woo`) | Barrido stock+costos, solo diferencias |
| Espejo kubera + /migracion | `backend/services/kubera_mirror.py` + `routers/migracion.py` + `frontend/app/migracion/` | Dual-write PROPIO (v0.13.0) de los escritores sin cobertura del compañero hacia la BD kubera (esquema v4); censo hardcodeado, errores en `espejo_kubera_log`, panel en tiempo real. GAP conocido: `pedidos_ml` sin destino v4 (propuesta en `docs/arquitectura_bd/propuesta_ops_orders.sql`, NO aplicar sin GO de Eduardo) |

**Tablas propias en MySQL (`u531713409_kubera_ml`)**: `pedidos_ml`,
`ventas_horarias`, `ventas_sync`, `webhook_eventos` (campana; los webhooks YA
NO se insertan ahí — `WEBHOOK_GUARDA_MYSQL=false`), `amazon_imagenes`,
`ml_backlog`/`ml_progress`/`amazon_progress` (bitácoras del publicador),
`canal_inventario` (espejo de canales; el esquema es de la migración — leer sí,
alterar no), `espejo_kubera_log` (errores del espejo kubera v0.13.0; local a
propósito — sobrevive con Supabase caído). Las 72 tablas `wp_*` de WordPress:
lectura directa OK, DDL/DML no.

## Variables clave en Railway (BackendOmnicanal, production)

| Variable | Estado | Efecto |
|---|---|---|
| `WEBHOOK_REGISTRO` | true | Recibe/procesa webhooks ML |
| `PEDIDOS_WC_ENABLED` / `PEDIDOS_WC_DESCUENTA_STOCK` | true / true | Pedidos + descuento de stock no-FULL |
| `WEBHOOK_GUARDA_MYSQL` | false | No insertar cada webhook en MySQL (espejo Supabase de José sigue, `SUPABASE_DUAL_WRITE`) |
| `SYNC_ENABLED` | true | Sync inventario 15 min (alimenta migración) |
| `VENTAS_ML_REFRESH` | false | Tab Ventas NO consulta la API de ML (modo pedidos) |
| `PEDIDOS_AMAZON_*` / `PEDIDOS_M2E_*` / `M2E_API_TOKEN` | activos | Sondeos Amazon / Temu-TikTok |
| `KUBERA_MIRROR_ENABLED` / `KUBERA_DB_URL` / `KUBERA_MIRROR_TABLAS` | false / — / — | Espejo kubera (v0.13.0) de escritores sin cobertura → esquema v4. Nace APAGADO (inerte); encenderlo = flujo vivo, dale de Brandon. La página /migracion muestra censo, eventos y errores |
| Apagado de emergencia | — | Cualquier flujo se apaga con su variable, sin deploy (accept-deploy para aplicar staged) |

## Integraciones y sus mañas

- **Apps de ML**: la `8902165405612832` (dueño: cuenta DevCenter aparte) manda
  los webhooks a nuestro Railway — es la arteria. Las apps `1446854968053102`
  y `1267116183141414` mandan a **Make.com** (dashboards viejos de José; Make
  se va a abandonar, pero NO desuscribir sin coordinar).
- **M2E Cloud** (Temu/TikTok/Woo): API `https://m2e.cloud/api/v1/api`, header
  `access-token`. `GET /catalog/product/?sku=` ✓, `PATCH /catalog/product/`
  con `{"products":[...]}` ✓, `POST /order/find/?channel=&account_token=` ✓.
  **No existe endpoint de publicar** — listar en Temu/TikTok es su panel web.
  TikTok: conexión `is_valid=false`, re-autorizar en M2E. El catálogo Woo→M2E
  se sincroniza solo (metas incluidas).
- **Amazon SP-API**: token LWA en `services/amazon.py::_access_token` (async).
  Product types con Definitions API; el payload es Listings Items
  (`PUT /listings/2021-08-01/items/{seller}/{sku}`), atributos como listas con
  `marketplace_id`, imágenes por URL pública (por eso el pipeline de ≥1000px).

## Pendientes conocidos (a 2026-07-20)

1. **403 de WooCommerce** en el listado de productos del panel (intermitente,
   WAF/CDN de Hostinger) — rompe la vista Productos a veces. Pendiente viejo.
2. **Guías de tallas ML**: 108 SKUs "Ready" bloqueados; faltan guías para ~25
   dominios de ROPA en ambas cuentas (solo hay calzado+bras). Al crearlas:
   registrar chart_ids en `size_chart_mapping.py` y relanzar. BRAS con guía
   fallan por falta de atributo GENDER en el producto.
3. **ME1 inactivo** (11 SKUs), **imágenes chicas** (5 + 82 con alerta),
   **GTIN real** (2, BEKURA).
4. **TikTok**: re-autorizar conexión en M2E (Brandon).
5. **Comisión de Amazon** en pedidos = 0 (falta Finances API).
6. **Fan-out de stock a otros canales** tras venta no-FULL (diseñado, no
   construido) y **webhook de WooCommerce** para ventas web (no construido —
   las ventas web NO aparecen en el tab aún).
7. **SKUs reciclados** con título distinto en ML vs Woo: `TEC-0492-MUL`,
   `ORG-0398-NEG`, `ORG-0579-*` (corregir a mano).
8. Seguridad heredada: API sin auth real (la de José va en rollout gradual);
   `client_secret` de ML expuesto en el repo externo `publicador` (rotación
   manual pendiente).

## Playbooks de diagnóstico exprés

- **"No se guardan pedidos"** → tokens ML (regla 8). Ver logs Railway:
  `orders_v2 → venta (modo pedidos)` SIN sufijo "pedido WC #" = fetch falló.
- **"Woo tiene más pedidos que el tab"** → duplicados: agrupar pedidos WC por
  meta `_ml_order_id` (>1 = dup). El lock debería impedirlo desde 7434aad.
- **"Las métricas no cuadran"** → README v0.9 (guía: KPI=pagados vs panel=
  todos; días pre-17-jul parciales; ML dashboard cuenta distinto).
- **"El espejo CHANNEL no escribe"** → ¿`SYNC_ENABLED`? (alimenta
  `canal_inventario`, fuente del espejo).
- **Deploys**: Railway project `66831425-3b47-4fda-8a8b-4b2b5f3df3e2`;
  BackendOmnicanal `96c29d05…`, FrontendOmnicanal `3ec32033…`. Variables vía
  agent quedan STAGED → `accept-deploy` para aplicarlas.
