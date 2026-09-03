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

- **VENTAS: WooCommerce es la fuente de verdad. INVENTARIO: ODOO es el MASTER**
  (decisión de Brandon, 20-ago-2026 — antes lo era Woo y esta línea decía lo
  contrario). La cadena es `Odoo → Woo → canales DROP`: desde el 28-ago
  `STOCK_WATCH_ABSOLUTO=true` hace que Woo COPIE `max(0, free_qty)` en vez de
  aplicar deltas. El delta divergía sin remedio (conserva la diferencia de base
  para siempre) y **no veía las RESERVAS**: `VIA-0024-NEG` tenía 30 piezas con
  29 comprometidas en borradores —1 vendible— y Woo ofrecía 14. Se vuelve a
  delta con la misma variable, sin deploy, si Odoo dejara de registrar salidas.
  `odoo_watch` (cada 30 min) sigue solo AVISANDO por la campana.
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
  Candado de cancelación: quitar la marca `_order_stock_reduced` antes de
  cancelar es un VESTIGIO (la REST de Woo no persiste esa meta — hallazgo
  v0.25); la defensa real es la REVERSIÓN de compensación
  (`pedidos_ml.py::_compensar_stock_protegido(signo=-1)`), que solo corre si
  hubo compensación previa y foto de `_reduced_stock` tomada a tiempo.
  Auditoría 18-ago: NINGUNA cancelación dispara el fan-out directo (la guarda
  exige `accion=="creado"`); la reposición de Woo la atrapa `stock_watch` como
  `woo_cambio` ~20 min después.
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
11. **En una corrutina, NADA que espere a la red o al disco se llama de forma
    síncrona.** `sdb.*`/`db.*` (psycopg2, pymysql), `httpx.get/post` sin `await`,
    `requests`, xmlrpc: todo eso detiene el backend ENTERO mientras responde, no
    solo a quien llamó. Va en `asyncio.to_thread`. Costó el apagón del 13-ago
    (v0.157.0–v0.162.0): el mismo defecto en cinco lugares — pedidos, sync de
    inventario, Análisis, webhook de FULL y vigilante de FBA. Síntoma: el panel
    no carga, la CPU al 1% y la memoria plana (no computa: **espera**).
12. **Cambiar una variable en Railway REINICIA el contenedor.** Apagar un flag y
    ver que el panel mejora NO prueba que ese flag fuera la causa — lo que
    mejoró pudo ser el reinicio. El 13-ago eso produjo dos diagnósticos
    equivocados seguidos. Para saber quién congela el backend está
    `services/vigilante_loop.py`: late dentro del loop y, desde un hilo aparte,
    vuelca la pila cuando el latido se atrasa. Buscar `EVENT LOOP ATASCADO` en
    los logs de Railway.
13. **NUNCA marcar la SESIÓN como read-only contra kubera/Supabase.** El DSN
    apunta al **pooler en modo transacción (6543)**, donde las conexiones de
    servidor **se comparten entre clientes**: un `cn.set_session(readonly=True)`
    o un `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` se queda pegado
    en la conexión y lo hereda el siguiente que la tome — que puede ser el
    backend de producción registrando una venta. Reventó dos veces
    (`ReadOnlySqlTransaction: cannot execute INSERT in a read-only transaction`):
    el 12-ago mató el ETL de categorías, y del 17 al 19-ago tiró la escritura de
    PEDIDOS y de CHANNEL a MySQL. **El script termina y el daño sigue vivo.**
    Si de verdad necesitas la garantía de solo-lectura en un diagnóstico:
    márcala **por transacción** (`BEGIN; SET TRANSACTION READ ONLY; …;
    ROLLBACK;`, muere con el commit), o conéctate al **5432** donde la conexión
    es tuya, o simplemente no marques nada si solo haces `SELECT`.
    `supabase_db` (v0.215.0) desinfecta y reintenta al detectar el error, pero
    esa es una red de seguridad, **no un permiso**: no evita envenenar el pool
    ni el ruido de alertas mientras tanto. Y si ves `ReadOnlySqlTransaction` en
    un log, no busques el bug en producción — busca qué diagnóstico corrió antes.

14. **ANTES DE CONSTRUIR UN PROCESO NUEVO, BUSCA EN `conocimientoGeneral`.**
    Regla de Brandon (3-sep-2026). Vive en la **rama `conocimiento`**, en su
    propio worktree —`Escritorio\omnicanal-conocimiento`— para que varios chats
    puedan seguir en `main` sin que les cambien los archivos bajo los pies:

    ```bash
    cd C:\Users\diaz2\OneDrive\Escritorio\omnicanal-conocimiento
    cat conocimientoGeneral/INDICE.md
    ```

    Es el catálogo de lo que el panel YA sabe hacer, extraído para que el equipo
    lo reuse en tareas chicas de KAM. **Railway despliega solo desde `main`, así
    que nada de esa rama puede llegar a producción** — no es una regla que se
    pueda olvidar, es el disparador del despliegue.

    Las cuatro reglas de esa carpeta, en corto: (1) buscar ahí primero; (2)
    jamás modificar producción — si falta algo, se crea código NUEVO ahí dentro;
    (3) se puede clonar el repo, nunca tocar producción; (4) todo se comparte
    entre chats por git. Traducción técnica: **sus scripts LEEN y producen
    ARCHIVOS; no escriben en Woo, kubera, Odoo ni ningún marketplace.** Lo
    comprueba `python conocimientoGeneral/verificar_aislamiento.py`.

    ⚠️ **`conocimientoGeneral/` NO debe aparecer en `main`.** Si lo ves aquí,
    algo se mezcló: deshazlo antes de seguir.

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
| Fan-out stock DROP | `backend/services/fanout_stock.py` + `routers/fanout.py` + `docs/AUDITORIA_FANOUT.md` | Woo→canales por evento (cola+debounce). ARQUITECTURA 18-ago: ML/Amazon/Walmart = fulfillment (Amazon FUERA de FANOUT_CANALES); TikTok/Temu(/SHEIN) = DROP-only. TikTok con auto-refresh 105002 (v0.207); Temu espera sondeo de `stock.edit` (`FANOUT_TEMU` off). Alineación inicial: `POST /api/fanout/alinear` |
| Censo TikTok | `backend/services/tiktok_censo.py` (`POST /api/tiktok/censo`, job tras `TIKTOK_CENSO_ENABLED`) | status+stock vivos en channel.listings; sin él, las activaciones de `tk_activar.py` (escritorio) son invisibles → sobreventa en potencia |
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

`deltas-costos`, `deltas-channel` y `deltas-orders` ya no comparan nada. Están
en `_DOMINIOS_RETIRADOS` (`routers/migracion.py`) para que el vigilante de
ausencias no avise "Acta NO generada hoy" a las 08:00 UTC. **Regla: el dominio
se apunta como retirado en el mismo commit que lo apaga** (a channel se le
olvidó y avisó a las 2 a.m.).

⚠️ **Cambiar el `startCommand` NO retira un cron. Quitarle el `cronSchedule`,
sí.** Medido el 15-ago-2026: `deltas-orders` siguió corriendo
`comparar_orders.py` COMPLETO y escribiendo acta todos los días (07:17→07:19)
*después* de sus dos commits de retiro — el del 12-ago (`railway-deltas.json`)
y el del 13-ago (`railway.deltas-orders.json`). La razón: **un cron de Railway
re-ejecuta el último deployment EXITOSO**, y el de este servicio seguía siendo
uno del 29-jul (`913f205`), anterior a los dos retiros. Editar el config file
solo cambia lo que correría en el *próximo* deployment; mientras no haya
deployment nuevo, el cron repite el binario viejo. Los otros dos sí se
detuvieron, y por eso: `deltas-costos` se quedó sin `cronSchedule` y
`deltas-channel` tiene su deployment en `SKIPPED`.

Retirado de verdad el 15-ago quitándole el `cronSchedule` por API (que **sí**
toma efecto sin deployment) y borrándolo también del config file, para que un
rebuild futuro no lo resucite. Su `configFile` es `railway.deltas-orders.json`
desde el 13-ago; `railway-deltas.json` (el del nombre fuera de patrón) quedó
huérfano y ya no lo lee nadie.

**Cómo se verifica que un cron está muerto de verdad:** no por su
`startCommand`, sino porque dejó de aparecer su efecto — aquí, filas nuevas en
`migration.reconciliation_runs`.

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
   Al retirar el esquema, ese vigilante se queda sin dónde guardar.

   **Medido el 16-ago**: vigila **5,381 SKUs** (los 5,381 tienen foto), y está
   **vivo**: 518 avisos de campana en total, **76 en los últimos 7 días**, el
   más reciente el 15-ago 17:27. Su `auto_push` está apagado **y no se puede
   encender**: `odoo_watch.py:159` lo bloquea si `stock_watch` está encendido,
   porque este empuje manda el valor ABSOLUTO de Odoo y resucitaría mercancía
   vendida. **Su único producto son los avisos.**

   Decisión pendiente: darle casa (`ops.odoo_stock_photo`, mismo molde que
   `ops.stock_watch_photo`) o **apagarlo**. La pregunta que la decide no es
   técnica: **¿quién lee esos 76 avisos por semana y qué hace con ellos?**

1c. **Los 13 scripts de mantenimiento** que se corren a mano y todavía leen
   MySQL (eran 8 en la lista vieja; el barrido del 16-ago encontró 5 más). La
   lista completa y su clasificación por peligro está en
   [docs/BARRIDO_LECTORES.md](docs/BARRIDO_LECTORES.md).

   ✅ **Los peligrosos ya están trancados (v0.198.0).** Cuatro decidían con datos
   congelados; el candado vive en `backend/scripts/_candado_congelado.py`, mide la
   frescura real (así se quita solo si la tabla revive), **bloquea la escritura y
   deja pasar el dry-run**, y falla CERRADO si no puede medir:

   | Script | Candado |
   |---|---|
   | `sync_odoo_woo_seguro` | aborta con `--aplicar` — su exclusión por ventas **se vacía sola** con el calendario |
   | `corregir_status_publicados` | aborta con `--aplicar` (publicaba en la tienda por canales muertos) |
   | `corregir_stock_woo_full` | aborta: superado por `sync_odoo_woo_seguro` |
   | `alinear_ml_drop` | `--aplicar` ahora exige `--en-vivo` |

   Los otros cuatro que leen el caché congelado (`marcar_amazon_muertas`,
   `alinear_amazon_drop`, `publicar_walmart`, `sincronizar_ml_huerfanas`) **no
   llevan candado a propósito**: solo lo usan para armar la lista de candidatos y
   después preguntan en vivo al canal. Fallan por omisión, no actuando mal.
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
6. ~~**Un lector externo pendiente**: `MonitoreoOperaciones`~~ — **SE RETIRA**
   (decisión de Eduardo, 16-ago-2026). Lee 7 tablas, no 1
   (`ml_backlog`, `amazon_progress`, `amazon_backlog`, `scraping_alibaba`,
   `atributos_ia`, `costos_ml`, `productos`), pero **no se despliega desde el
   23-jun** y ya no opera. **No hay nada que repuntar**: se da de baja el
   servicio en Railway. Antes de apagarlo conviene avisar que tres de sus
   tablas (`scraping_alibaba`, `atributos_ia`, `costos_ml`) son del robot de
   Alibaba, desconectado desde el 23-jul — quien todavía abriera ese tablero
   llevaba meses leyendo historia congelada.

### Reglas que siguen vigentes

1. **La BD kubera (`tukwcvsi…`) es PRODUCCIÓN OPERATIVA.** Desde fuera de la
   app se toca **SOLO CON `SELECT`**: nada de INSERT/UPDATE de prueba — las
   cobayas van al sandbox.
2. **El SANDBOX (`yvootpbz…`) LLEVA CLONES DE PRODUCCIÓN** (Eduardo, 12-ago;
   la regla anterior decía "vacío a propósito" y **se cambió**). Ahí se prueba
   y se verifica con datos que se parecen a los de verdad; sin eso, cualquier
   cambio de UI o de SQL se valida a ciegas. El esquema se recrea con
   `supabase/migrations/` + `backend/scripts/aplicar_migraciones.py`; los datos
   se cargan con **`backend/scripts/clonar_a_sandbox.py`** (kubera → sandbox,
   lectura en producción, dry-run por default). **Si el sandbox aparece vacío
   ya no es intencional: hay que re-sembrarlo.**

   `backend/scripts/sembrar_sandbox.py` quedó **OBSOLETO**: lee de MySQL —que
   salió de la arquitectura— y solo cubre 3 tablas, sin `channel.listings` ni
   `channel.orders`, que son las que alimentan la tabla de Análisis.

   Mañas que costaron seis intentos y están resueltas en el script nuevo: el
   DSN del `.env` apunta al pooler en **modo transacción (6543)**, que no
   sostiene cursores con nombre ni transacciones largas — hay que usar el
   **5432** del mismo host; y las 21 tablas se leen en **una sola
   `REPEATABLE READ`**, porque producción está viva y en un intento se creó una
   cuenta a media copia que rompió una llave foránea.
3. **Staging apunta al sandbox** y tiene `SUPABASE_PROD_REF=tukwcvsi…`: el
   candado `validar_ambiente()` mata el arranque si staging o un local apuntan
   a producción. No "arreglarlo" — es la protección.
4. **P4**: `costing.costos_finales` tiene PK `(sku, canal)`; hoy todo es
   `canal='mercado_libre'`. Toda consulta nueva filtra canal.
5. **`etl_core_products.py`** (v1 full-refresh) está RETIRADO con candado —
   usar `etl_core_products_v2.py` (incremental, dry-run por default).
6. **dailytrackMeli (`xaxbkijc…`) DESAPARECIÓ** (v0.88.0): su hostname dejó de
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
6. **Fan-out de stock**: CONSTRUIDO y vivo desde el 28-jul (ver fila del mapa
   y docs/AUDITORIA_FANOUT.md; auditado y reorientado a DROP el 18-ago,
   v0.207). Sigue pendiente el **webhook de WooCommerce** para ventas web (no
   construido — las ventas web NO aparecen en el tab aún).
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
9. Seguridad heredada: API sin auth real (la de José va en rollout gradual).

   ~~`client_secret` de ML expuesto en el repo `publicador`~~ — **VERIFICADO EL
   20-AGO: en el código de hoy NO hay ningún secreto.** `config.py` lee todo de
   variables de entorno y `.env.example` tiene los valores vacíos. La nota llevaba
   desde julio y estaba desactualizada; la rotación baja de "agujero abierto" a
   higiene. **Falta revisar el HISTORIAL de git**, que no se miró.

   ⚠️ **Lo que SÍ está abierto, y no lo decía ninguna nota: los SIETE repos de
   `Hixen9kubera` son PÚBLICOS** — `publicador`, `OMNICANAL`,
   `KuberaPipelineV1.0`, `MonitoreoOperaciones`, `MLREgisterDaily`,
   `MCPPruebaWOO`, `Aplicacion_Excel`. No hay un secreto filtrado hoy, pero es la
   condición que convierte cualquier descuido futuro en filtración inmediata, y
   es la razón por la que la auditoría del 19-ago tuvo que barrer el historial
   entero. Decisión pendiente de Eduardo.

   Si se rota el `client_secret`: es el de la app **`8902165405612832`** —la
   ARTERIA, la de los webhooks de ventas—, vive en claro en `MELI_CLIENT_SECRET`
   de Railway, y **la app es de una cuenta DevCenter aparte**. El riesgo está en
   el ORDEN: el secreto solo sirve para RENOVAR, así que los tokens vigentes
   aguantan unas horas; si el nuevo no está en Railway antes de que caduquen, la
   renovación falla y **paran las ventas de ML**. Secuencia: regenerar en
   DevCenter → actualizar Railway de inmediato → `verificar_tokens_ml.py`.

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
