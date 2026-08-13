# CLAUDE.md — Contexto del proyecto OMNICANAL · Kubera

> Este archivo existe para que cualquier sesión de Claude (u otra persona)
> entienda el proyecto SIN leer todo el historial. Última gran actualización:
> **2026-07-23 (v0.16.3)**. El changelog detallado versión por versión vive en
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
4. **La BD kubera ES la fuente de verdad** (migración cerrada el 12-ago-2026).
   Los cinco dominios están cortados y sus espejos a MySQL RETIRADOS: escribir
   ahí ya no sirve de nada y leer de ahí devuelve datos de agosto. Ver la
   sección de migración más abajo antes de tocar `core`/`channel`/`costing`/
   `ops`/`migration`, `kubera_mirror.py`, los ETLs o el sync de 15 min (que
   alimenta `channel.listings`; `SYNC_ENABLED=false` del 17-20 jul congeló la
   observación 3 días).
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
| Espejo kubera + /migracion | `backend/services/kubera_mirror.py` + `routers/migracion.py` + `frontend/app/migracion/` | Dual-write PROPIO (v0.13.0) de los escritores sin cobertura del compañero hacia la BD kubera (esquema v4); censo hardcodeado, errores en `espejo_kubera_log`, panel en tiempo real (+racha de actas v0.14.0). Pool 6 + reproceso de errores pendientes (v0.15.2, Eduardo) y despacho por cola acotada + 2 workers (v0.15.3). GAP de pedidos CERRADO en v0.16.0: `channel.orders` aplicada por Eduardo (2026-07-22) y seam en `pedidos_ml.sincronizar`, ENCENDIDO el 23-jul (dale de Eduardo). Backfills one-shot idempotentes vía `POST /api/migracion/backfill/*`: `product-media` (254 imágenes históricas) y `channel-orders` (3,546 pedidos desde el 13-may, 0 fallos; por tandas con `offset` — la corrida completa excede el timeout del proxy). `channel.orders` está COMPLETO: histórico + flujo vivo (v0.16.1–v0.16.3) |

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
| `KUBERA_MIRROR_ENABLED` / `KUBERA_DB_URL` / `KUBERA_MIRROR_TABLAS` | **true** / definida / `crear_logs,ml_backlog,amazon_backlog,amazon_imagenes,ml_image_edit_backlog,pedidos_ml` | Espejo kubera de escritores sin cobertura → esquema v4 (6 tablas desde el 23-jul, GO de Eduardo). Sumar tabla al CSV = flujo vivo, dale de Brandon. Quedan FUERA a propósito: `webhook_eventos` campana (opcional, volumen) y `ml_tokens` (bloqueado hasta Vault). La página /migracion muestra censo, eventos, errores y racha de actas |
| Apagado de emergencia | — | Cualquier flujo se apaga con su variable, sin deploy (accept-deploy para aplicar staged) |

## MIGRACIÓN A LA BD KUBERA — cortada, y los espejos APAGADOS (13-ago-2026)

Los cinco dominios (costos, pedidos, channel, core, categorías) están cortados:
**kubera es la fuente de verdad y ahí se escribe primero.** Y desde el 13-ago a
las 04:23 UTC **los tres espejos inversos están apagados**: MySQL ya no recibe
nada. Ese fue el último paso de la fase de CORTES; lo que queda es el
desmantelamiento (F8), que es limpieza, no migración.

El retiro se intentó antes, el 11 y 12-ago, y **se revirtió** — abajo el porqué,
porque la lección es lo que hizo que la segunda vez funcionara.

### ⚠️ Antes de congelar una tabla: busca quién la LEE PARA DECIDIR

Esta sección existe por un incidente que costó dinero.

El 12-ago se congeló `pedidos_ml` (paso 1 del retiro de pedidos). Kubera quedaba
al día y el espejo dejaba de escribirse: parecía inocuo. Pero **tres consultas
del flujo de alta seguían preguntándole a esa tabla**, y una foto detenida
contesta con seguridad lo que ya no sabe:

- El candado de idempotencia respondía SIEMPRE "esta orden no existe" → cada
  aviso de ML creaba otro pedido en Woo. **964 pedidos fantasma en 4 h 17 min,
  $409,741**, 85% de todo lo creado en la ventana.
- La marca de agua de Amazon quedó fija: pedía siempre la misma ventana. **No
  duplicó por casualidad — no tuvo tráfico esa tarde.**
- El vigilante de silencio de ventas gritó "sin ventas en 4.1 h" en día récord.

Arreglado en v0.117.0 (las tres lecturas se mudan a `channel.orders` vía
`orders_write`), con contención `ORDERS_ESPEJO_INVERSO=true`, los 964 a la
papelera y los 16 que habían descontado stock cancelados primero para que Woo
devolviera la pieza.

**La regla que faltaba: congelar una tabla es cambiar el contrato de LECTURA,
no solo el de escritura.** Verificar que kubera quede al día y que el espejo
deje de escribir NO alcanza. Un arnés de paridad mide si los datos coinciden,
no si alguien toma decisiones con ellos. Y un `None` de una tabla detenida no
significa "no existe": significa "ya no sé".

### PROCEDIMIENTO de apagado → [docs/APAGADO_ESPEJOS_MYSQL.md](docs/APAGADO_ESPEJOS_MYSQL.md)

Qué se apaga, cómo se verifica con `vigilar_congelacion.py`, cómo se
revierte (una variable, sin deploy) y qué NO tocar mientras tanto.

### Estado real de los espejos (13-ago, 05:00 UTC)

`CHANNEL_ESPEJO_INVERSO`, `COSTING_ESPEJO_INVERSO` y `ORDERS_ESPEJO_INVERSO`
están en **`false`**. Verificado con `vigilar_congelacion.py` a las 14 h: las
tres tablas congeladas y kubera recibiendo con normalidad.

Ojo con el tiempo de efecto: inventario y costos pararon en minutos, **pedidos
tardó ~36 min** (hasta las 05:00). Lo más probable es la cola del espejo
drenando lo ya encolado — el flag corta lo que ENTRA, no lo que ya está dentro.
No asumir que un flag apagado significa "detenido ya".

`core` y `categorías` nunca tuvieron espejo inverso (el único `UPDATE` a
`productos` es `odoo_watch.py:57`, stock de Odoo; `categorias_ml` no la escribe
nadie desde el 22-jul).

**La reversa sigue siendo una variable**, por dominio y sin deploy.

### Lo que hay que repuntar antes de reintentar el retiro

El barrido del 12-ago está **CERRADO**: los diez sitios que decidían leyendo
el espejo ya miran a kubera, cada uno con su medición de paridad previa.

| Sitio | Qué decidía | Cerrado en |
|---|---|---|
| `fanout_stock.py:260` | a qué publicaciones empujar stock | v0.118.0 |
| `inventario.py:258 / 282` | qué ítems vio el sync y cuáles cerrar | v0.119.0 |
| `costos.py:65` + `costo_desde_validados` + `_preparar_base` | comisión y costo al fijar precios | v0.120.0, v0.124.0 |
| `crear_producto.py` (5) · `creacion.py` (2) | costos y categorías al crear productos | v0.125.0 |
| `competencia_captura.py` (4) | a qué SKUs medirles la competencia | v0.126.0 |
| `stock_full.py:364` · `inventario.plan_dry_run` | semilla del vigilante FBA y plan de sync | v0.127.0 |

**Orders no necesitó nada**: desde v0.117.0 sus tres lecturas solo van a MySQL
cuando kubera está CAÍDA —que es justo cuando MySQL es el fresco, porque
`guardar()` lo hace absorber— y `ventas_ml` solo con el flag apagado. La regla
está escrita en el propio archivo: *se lee de donde se está escribiendo*.

Lo que queda leyendo MySQL son **scripts de mantenimiento a mano**, no flujos
vivos: `alinear_ml_drop`, `alinear_amazon_drop`, `marcar_amazon_muertas`,
`corregir_status_publicados`, `corregir_stock_woo_full`,
`sincronizar_ml_huerfanas`, `publicar_walmart`, `sync_odoo_woo_seguro`. Dejarán
de servir cuando se retire el esquema; se repuntan o se archivan en F8. El
`etl_channel_listings` y `channel_mirror` leen MySQL POR DISEÑO (son el espejo)
y se retiran con el andamiaje.

El orden sigue siendo: repuntar los lectores → verificar → recién entonces
apagar el espejo de ese dominio.

### Estado por dominio

| Dominio | Escritura | Lectura | Espejo MySQL |
|---|---|---|---|
| Costos | kubera | kubera, sin fallback | APAGADO 13-ago |
| Pedidos | kubera | kubera, sin fallback | APAGADO 13-ago |
| Channel | kubera | kubera, sin fallback | APAGADO 13-ago |
| Core | kubera | kubera, sin fallback | nunca tuvo |
| Categorías | kubera | kubera, sin fallback | nunca tuvo |

Los flags `SUPABASE_READ_*` siguen siendo la reversa de las lecturas y los
`*_ESPEJO_INVERSO` la de las escrituras.

### Lo que sigue vivo y NO se retira

- **Los ETLs de las 06:15** (`etl-core-products`: maestro + categorías
  encadenados). Dejaron de ser compuerta de la migración y son ahora el
  **vigilante permanente del catálogo**: lo único que compara Woo contra
  kubera. Destaparon las ediciones de títulos del 11-ago. Su acta mide
  `seam_gap` (>0 = algo cambió en Woo que ningún seam cubrió).
- **El webhook de Woo** (`/api/webhooks/woo`, v0.92 + v0.99.1): dos webhooks en
  wp-admin (`product.updated` y `product.created`) que avisan a
  `core.products` de cualquier edición, venga del panel o de wp-admin. Sin él,
  editar un título en WordPress desfasa el registro civil hasta el día
  siguiente. Se protege con firma HMAC; sin firma válida NO escribe.
- **La resiliencia**: kubera caída → MySQL absorbe y el evento se encola en
  `espejo_kubera_log` (reprocesable en /migracion). Vive en el camino de error
  y no depende de ningún flag. Se retira hasta F8.
- **`alertas_estado` y `espejo_kubera_log`** siguen en MySQL A PROPÓSITO: deben
  sobrevivir con kubera caída.

### Los crons de deltas están RETIRADOS

`deltas-costos`, `deltas-channel` y `deltas-orders` ya no comparan nada: su
`startCommand` imprime un aviso de retiro. Están en `_DOMINIOS_RETIRADOS`
(`routers/migracion.py`) para que el vigilante de ausencias no avise "Acta NO
generada hoy" a las 08:00 UTC. **Regla: el dominio se apunta como retirado en
el mismo commit que lo apaga** (a channel se le olvidó y avisó a las 2 a.m.).

Ojo con los crons de Railway: su horario y comando viven en su
`railwayConfigFile`, no en el servicio. Cambiar `cronSchedule` por API NO
funciona; solo un push re-resuelve el archivo. Y `deltas-orders` usa
`railway-deltas.json` (nombre fuera de patrón).

### F8 — lo que falta para cerrar del todo

**0. Repuntar los lectores de la tabla de arriba.** Es el paso que faltó y el
que habilita todos los demás: mientras `fanout_stock`, `inventario` y el flujo
de Crear decidan leyendo MySQL, ningún espejo se puede volver a apagar. Se hace
dominio por dominio, y cada uno se apaga solo cuando SUS lectores ya miran a
kubera.

0b. **El cron de las 06:15 ya NO abre `kubera_ml`** (v0.129.0): los dos ETLs
   leen kubera y Woo/Odoo vivos. Era el último proceso no-espejo que dependía
   del esquema viejo. Lo que queda apuntando ahí son 8 scripts de mantenimiento
   que se corren A MANO y el andamiaje del propio espejo.

1. **Bitácoras a `ops.process_log`**: `crear_logs` HECHO (v0.132.0, con los
   2,583 eventos y sus fechas corregidas). Falta solo `alertas_estado`, y va al
   FINAL a propósito: debe seguir funcionando aunque kubera esté caída, así que
   se mueve cuando ya no haya nada que rescatar.

1b. **La foto de stock de Odoo NO tiene casa en kubera.** `odoo_watch` compara
   contra `productos.stock_odoo` (MySQL) y la escribe cada 30 min — o sea que
   `productos` NO está del todo congelada. `core.products` no tiene esa columna.
   Al retirar el esquema, ese vigilante se queda sin dónde guardar. Decisión
   pendiente: darle casa o **apagarlo** (Odoo está en retiro y Woo es la fuente
   de verdad del stock; hoy solo vigila 4,786 de los 13,030 SKUs de Odoo porque
   su lista dejó de crecer el 23-jul).

1c. **Los 8 scripts de mantenimiento** que se corren a mano y todavía leen
   MySQL: `alinear_ml_drop`, `alinear_amazon_drop`, `marcar_amazon_muertas`,
   `corregir_status_publicados`, `corregir_stock_woo_full`,
   `sincronizar_ml_huerfanas`, `publicar_walmart`, `sync_odoo_woo_seguro`.
   Repuntar o archivar antes del retiro del esquema.
2. **Archivo de congelados**: tablas del robot Alibaba (`scraping_alibaba`,
   `atributos_ia`, `imagenes_producto`, `productos`), `legacy_costos_ml`, seeds
   de `fx_rates`/`pricing_params`, `marketplace_identity` y su cron.
3. **Retirar el andamiaje**: `kubera_mirror.py`, los flags `SUPABASE_WRITE_*` y
   `SUPABASE_READ_*`, la página `/migracion`, `espejo_kubera_log` y los
   arneses `comparar_lecturas_*`.
4. **Retirar el esquema MySQL `kubera_ml`** (respaldo previo:
   `Documents/respaldos_kubera_ml`, hecho el 11-ago sin tablas de tokens).
   WordPress se queda: vive en el mismo hosting y el panel lo lee directo.
5. **MIGRACION_FINAL.md**: el acta de defunción con el mapa de qué quedó dónde.
6. **Un lector externo pendiente**: `MonitoreoOperaciones` (servicio Railway)
   lee `productos` de MySQL. Cuando se retire el esquema hay que repuntarlo a
   `core.products` o avisar que ese panel se congela.

### Reglas que siguen vigentes

1. **La BD kubera (`tukwcvsi…`) es PRODUCCIÓN OPERATIVA.** NO insertar datos de
   prueba: las cobayas van al **SANDBOX (`yvootpbz…`)**, clon del esquema y
   vacío a propósito. Se recrea con `supabase/migrations/` +
   `backend/scripts/aplicar_migraciones.py`.
2. **Staging apunta al sandbox** y tiene `SUPABASE_PROD_REF=tukwcvsi…`: el
   candado `validar_ambiente()` mata el arranque si staging o un local apuntan
   a producción. No "arreglarlo" — es la protección.
3. **P4**: `costing.costos_finales` tiene PK `(sku, canal)`; hoy todo es
   `canal='mercado_libre'`. Toda consulta nueva filtra canal.
4. **`etl_core_products.py`** (v1 full-refresh) está RETIRADO con candado —
   usar `etl_core_products_v2.py` (incremental, dry-run por default).
5. **dailytrackMeli (`xaxbkijc…`) DESAPARECIÓ** (v0.88.0): su hostname dejó de
   resolver. `services/supabase_rest.py` quedó huérfano y las variables
   `ANALYTICS_SUPABASE_*` ya no apuntan a nada vivo — ojo al borrarlas sin
   quitar el módulo (el fallback le pediría `products_snapshot` a kubera, donde
   no existe).

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
   `ORG-0398-NEG`, `ORG-0579-*`, y (detectados 22-jul) `ORG-0934`,
   `MAN-0490-DOR`. Caso especial `EST-0091`: es DOS productos — cómoda en
   BEKURA (nueva) y repisa flotante VIVA en SANCORFASHION (pausada, 40 pzas
   declaradas; ML no deja eliminarla mientras su user product tenga stock).
   PLAYBOOK desde v0.15.0: dar de baja las publicaciones viejas en ML →
   botón Publicar → el panel detecta la baja y re-crea pausada por cuenta
   (ya NO se limpia `ml_progress` a mano). Regenerar en Crear NO recalcula
   costos ni toca publicaciones existentes — revisar categoría y regenerar
   costos antes de publicar un SKU reciclado. Sub-caso CLON SIN LIMPIAR
   (categoría+atributos de OTRO producto): `ACC-0653-CHE-13-16` (faros de niebla
   con categoría+atributos de binoculares; la IA regeneraba "binoculares" porque
   leía la categoría — reparado v0.12.3). Al reciclar, limpiar categorías Y
   atributos, no solo título/imágenes. **Categoría ML del panel** se guarda con
   `POST /api/crear/categoria-ml` (escribe `ml_categoria_id`+niveles — la elección
   humana que MANDA al publicar); el picker del Estudio ya persiste (v0.17.0).
   **Comisión de pedidos ML en 0** (token caído al crearse): re-consultable con
   `meli.obtener_orden` (trae `sale_fee`); el `ON DUPLICATE` de `pedidos_ml`
   rellena 0→valor solo, nunca re-toca >0 (v0.17.0). Amazon queda en 0 hasta
   Finances API (#5); los cancelados no llevan comisión neta.
8. **KuberaPipelineV1.0 SE DESCONECTA** (decisión de Eduardo, 23-jul): no correr
   más tandas del robot de Alibaba; sus tablas MySQL = legado congelado (ETL
   one-shot al corte / retiro). El `publicador` externo se retira con él.
   `core.products` pierde su fuente → el seam Crear → core.products es el
   bloqueador del corte (82 SKUs ya faltantes; ver README aviso 23-jul).
9. Seguridad heredada: API sin auth real (la de José va en rollout gradual);
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
- **"ERROR DE CONEXIÓN al publicar"** → mensaje genérico del frontend para
  CUALQUIER fallo del fetch: puede ser un 500 del backend (ver logs Railway
  de `/api/publicar/confirmar`) o el navegador del usuario bloqueando la
  petición (extensión/antivirus: probar incógnito; caso 22-jul). Desde
  v0.15.1 los errores de validación de ML sí llegan legibles al modal.
- **Errores del espejo kubera** → /migracion sección Errores; re-aplicar con
  `POST /api/migracion/errores/reprocesar` (escribe y marca resuelto).
- **Deploys**: Railway project `66831425-3b47-4fda-8a8b-4b2b5f3df3e2`;
  BackendOmnicanal `96c29d05…`, FrontendOmnicanal `3ec32033…`. Variables vía
  agent quedan STAGED → `accept-deploy` para aplicarlas.
