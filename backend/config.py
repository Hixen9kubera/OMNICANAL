"""
config.py — Lee variables del .env y .env.amazon (API keys de WooCommerce,
Odoo, Mercado Libre, Amazon, Claude, etc.) y las expone como un objeto único.

Se cargan DOS archivos:
  - .env          → Odoo, WooCommerce, DB MySQL, IA
  - .env.amazon   → credenciales Amazon SP-API (San Corpe)

Ambos viven en la RAÍZ del proyecto (un nivel arriba de /backend).

AMBIENTE STAGING: si el proceso arranca con APP_ENV=staging (variable de
entorno del sistema, como la inyecta Railway), se carga `env.staging` en lugar
de `.env` — así el mismo código corre local contra el ambiente de pruebas sin
tocar el .env de producción. En Railway los archivos ni existen: las variables
llegan por el entorno y pisan cualquier archivo.
"""
from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# La raíz del proyecto es el padre de /backend
ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_MAIN = ROOT_DIR / ".env"
ENV_AMAZON = ROOT_DIR / ".env.amazon"
ENV_STAGING = ROOT_DIR / "env.staging"

# La selección del archivo ocurre ANTES de instanciar Settings: depende de la
# variable de entorno del sistema (no del archivo mismo, sería circular).
_ENV_FILES = (
    (ENV_STAGING,)
    if os.environ.get("APP_ENV", "").strip().lower() == "staging"
    else (ENV_MAIN, ENV_AMAZON)
)


class Settings(BaseSettings):
    # pydantic-settings carga ambos archivos; las claves de .env.amazon
    # se añaden encima de las de .env.
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Odoo ──────────────────────────────────────────────────
    odoo_url: str = ""
    odoo_db: str = ""
    odoo_user: str = ""
    odoo_password: str = ""

    # ── WooCommerce (centro / fuente de la vista GENERAL) ─────
    wc_url: str = ""
    wc_consumer_key: str = ""
    wc_consumer_secret: str = ""

    # ── WordPress media ───────────────────────────────────────
    wp_user: str = ""
    wp_app_password: str = ""

    # ── Base de datos de WordPress (lecturas directas) ────────
    # Sale del wp-config.php del sitio: DB_NAME/DB_USER/DB_PASSWORD/DB_HOST.
    wpdb_host: str = ""       # si queda vacío usa db_host
    wpdb_port: int = 3306
    wpdb_name: str = ""
    wpdb_user: str = ""
    wpdb_password: str = ""
    wpdb_prefix: str = "wp_"  # prefijo de tablas ($table_prefix en wp-config)

    # ── IA ────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    replicate_api_key: str = ""
    serpapi_key: str = ""
    # DeepSeek (API compatible con OpenAI). Si hay clave, los generadores de
    # contenido lo usan primero; si no, caen a Claude (anthropic).
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    # ¿El alta de productos (pestaña Crear) genera además el contenido de Amazon
    # y lo deja guardado en enrich.channel_content? Nace APAGADO: encenderlo
    # cambia lo que hace un flujo vivo (cada alta gasta 1-2 llamadas de IA y
    # escribe en producción), y eso lleva el dale de Brandon. El botón "Mejorar
    # con IA" del Estudio NO depende de esta variable: ahí lo dispara una
    # persona.
    amazon_ia_en_crear: bool = False

    # ── Packing lists en Google Drive ─────────────────────────
    # Carpeta donde viven los packing lists de los contenedores. El validador de
    # costos de publicados la LISTA para saber qué archivo le toca a cada SKU;
    # el Resolver clásico no la necesita (ahí el usuario pega la liga).
    # La carpeta está compartida como "cualquiera con el enlace": no hay service
    # account de Google en el repo, y por eso el inventario se lee de la página
    # pública — ver packing_drive_carpeta.
    pl_drive_carpeta_id: str = "1PstK1At4DwUH0QUsIOXr9Zgb2TZJHgrM"
    # Lo mismo para TikTok. Ojo: al crear, el producto todavía NO está publicado
    # en TikTok, así que no tiene categoría y sus atributos no se pueden pedir —
    # se genera el título y la descripción, y los atributos entran cuando el SKU
    # ya tenga categoría en el canal.
    tiktok_ia_en_crear: bool = False
    # Lo mismo para Temu. Mismo matiz que TikTok y más marcado: un producto
    # recién creado no está publicado en Temu, así que no tiene hoja de
    # categoría — sin hoja no hay atributos que pedir y solo sale el texto.
    temu_ia_en_crear: bool = False
    # ¿El webhook de TikTok CREA pedidos en WooCommerce? Apagado, el receptor
    # sigue en modo observar (registra y no escribe). Encenderlo toca inventario
    # y contabilidad: los pedidos de TikTok DESCUENTAN stock, porque la
    # mercancía sale de nuestra bodega.
    pedidos_tiktok_enabled: bool = False
    # ¿El webhook de Temu CREA pedidos en WooCommerce? Apagado: el receptor
    # registra, descifra y verifica la firma, pero no escribe. Ojo con lo que
    # NO se puede hacer aunque se encienda: Temu no expone el importe del
    # pedido (API bloqueada con 3000032), así que el pedido se crea con el
    # precio de CATÁLOGO y eso queda dicho en el registro.
    pedidos_temu_enabled: bool = False
    # ¿El fan-out ESCRIBE stock en TikTok? Interruptor propio, aparte de
    # `FANOUT_CANALES`, y a propósito: el valor de esa lista no se puede leer
    # desde fuera de Railway, así que si TikTok dependiera solo de ella un deploy
    # podría encender escrituras a un marketplace vivo sin que nadie lo hubiera
    # decidido. Con este flag el deploy es INERTE y encenderlo es un acto
    # explícito. Para escribir hacen falta las dos cosas.
    fanout_tiktok: bool = False
    # Mismo patrón para Temu (canal DROP-only por decisión del 18-ago).
    # ENCENDIDO el 19-ago: el sondeo canario confirmó la forma de
    # `bg.local.goods.stock.edit` (edita por DIFERENCIA) y `_escribir_temu` ya
    # vive en _ESCRITORES. El flag se queda porque sigue siendo el interruptor
    # para apagar las escrituras a Temu sin tocar código ni deploy.
    fanout_temu: bool = False

    # ── Creación de productos (Alibaba → Woo) ─────────────────
    apify_api_key: str = ""
    apify_alibaba_actor: str = "happitap~alibaba-product-scraper"

    # ── Competencia (Mercado Libre) ───────────────────────────
    # La API de ML da las VISITAS de cualquier publicación pero NO la ficha
    # (título/imagen/precio/descripción/posición) de las ajenas: /items/{id}
    # Navegador genérico para la página /mas-vendidos/{cat}: los actores
    # especializados en ML NO la parsean (uno FAILED, otro 0 items) y este sí,
    # porque ejecuta el security.js. Cobra por cómputo (~$0.007/página) en vez de
    # por item, ~93× más barato que el de listings con detalle.
    apify_navegador_actor: str = "apify~playwright-scraper"
    # RESPALDO cuando el de arriba devuelve "Crawled 0/N pages". Mismo contrato
    # de entrada (startUrls + pageFunction + proxyConfiguration) y el mismo
    # `context.page`, así que la pageFunction sirve para los dos — siempre que
    # se escriba en el subconjunto común: `page.waitForTimeout` es de Playwright
    # y en Puppeteer hay que dormir con un setTimeout envuelto en Promise.
    apify_navegador_respaldo: str = "apify~puppeteer-scraper"
    # ML sirve un interstitial de "tráfico sospechoso" a IPs de datacenter, así
    # que el scraping va por proxy residencial mexicano (igual que Alibaba).
    apify_proxy_pais: str = "MX"
    # Resultados por búsqueda. Con detalle son $0.025/item (trae descripción y
    # unidades vendidas); sin detalle $0.003 pero sin esos dos campos.
    competencia_top: int = 25
    # Pedir a ML el precio CON promoción (/items/{id}/sale_price) en cada pasada
    # del sync. SIGUE APAGADO, pero la razón CAMBIÓ — v0.261.0.
    #
    # Lo que decía esta nota hasta el 25-ago: "`price_sale` NO TIENE LECTORES".
    # Ya los tiene. El margen de la pestaña Omnicanal
    # (`publicaciones_panel._oferta`) lo lee y decide con él, y desde v0.261.0
    # además pregunta CUÁNDO se observó: una oferta más vieja que el último
    # cambio de la publicación se marca sin confirmar y NO se aplica.
    #
    # Sigue apagado porque este barrido es el instrumento equivocado: hacía
    # ~11,500 llamadas diarias a ML y ENSUCIABA el corte (volvía a tocar
    # publicaciones que el refresco ya había leído y les sellaba fecha nueva,
    # así que la foto dejaba de ser simultánea). La vía correcta es el aviso:
    # el webhook del topic `items_prices` (~413 al día) pide el precio de oferta
    # solo de la publicación que ML dice que cambió — ver
    # `inventario.refrescar_ml_item_id(con_precio_venta=True)`.
    #
    # Encenderlo tendría sentido como BARRIDO de arranque, para confirmar de
    # golpe las que llevan días sin aviso. Eso es una decisión con acta, no un
    # default.
    #
    # Y ese barrido ya existe, PERO NO ES ESTE FLAG — v0.267.0. Vive en
    # `services/precios_venta.py` y se enciende con `PRECIOS_VENTA_BARRIDO`
    # (abajo). La diferencia no es cosmética: aquel hacía ~11,500 llamadas
    # diarias porque preguntaba en CADA pasada del sync y por CADA publicación;
    # el barrido pregunta una vez por publicación cada 9.3 h, ordenando por la
    # más rancia, y con eso cuesta ~1,920/día. Este flag sigue apagado.
    ml_precio_venta: bool = False

    # ── Barrido de precios de venta de ML (v0.267.0) ──────────
    # Confirma `channel.listings.price_sale` de las publicaciones ACTIVAS de ML
    # preguntando `/items/{id}/sale_price`. Es lo que hace que el margen del
    # panel Omnicanal se calcule contra lo que ML COBRA y no contra el precio de
    # lista. Ver el encabezado de `services/precios_venta.py`.
    #
    # NACE APAGADO a propósito: toca flujo vivo (llamadas a ML + escritura a
    # channel.listings) y encenderlo es una decisión con acta y dale de Brandon.
    # Respeta SYNC_ENABLED por encima de este flag.
    #
    # Por qué hace falta aunque los webhooks de precio funcionen: medido el
    # 26-ago-2026 sobre los 3 días que retiene ops.webhook_events, 341 de las
    # 745 activas (46%) NO recibieron ningún aviso de precio, y ninguna de esas
    # 341 estaba confirmada. Un aviso solo llega cuando algo cambia; el barrido
    # es lo único que alcanza a las que nadie mueve.
    precios_venta_barrido: bool = False
    # Cuántas publicaciones pide el goteo por hora. 80 → ciclo completo de 9.3 h
    # sobre las 745 activas, ~1,920 llamadas/día (la mitad de lo que ya gastan
    # los avisos de precio). La confirmación caduca a las ~48 h —medido: 0
    # activas con updated_at de más de 48 h— así que por debajo de ~32/h el
    # ciclo (23 h) deja de alcanzar para las que cambian a diario.
    precios_venta_por_hora: int = 80
    # Pasada COMPLETA una sola vez al arrancar, para no esperar un ciclo entero
    # a que se drene el atraso acumulado mientras el backend estuvo abajo.
    precios_venta_arranque: bool = True
    competencia_con_detalle: bool = True

    # ── Base de datos MySQL (cache híbrido) ───────────────────
    db_host: str = ""
    db_port: int = 3306
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""
    # Clave Fernet con la que se cifran los tokens de Mercado Libre en ml_tokens
    db_encryption_key: str = ""

    # ── Mercado Libre ─────────────────────────────────────────
    ml_site_id: str = "MLM"  # MLM = México
    meli_app_id: str = ""
    meli_client_secret: str = ""

    # ── Supabase (Postgres) — nuevo medio de consultas de ML ──
    # Dataset ya sincronizado a diario (products_snapshot, daily_stock, ml_accounts…).
    # supabase_db_url es la cadena del POOLER (session 5432 / transaction 6543):
    #   postgresql://postgres.<ref>:<PASSWORD>@aws-1-us-west-2.pooler.supabase.com:5432/postgres
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_db_url: str = ""
    # ── Analítica (dailytrackMeli) separada de la operativa (BD kubera) ──
    # Nombres honestos tras declarar tukwcvsi producción operativa: la familia
    # SUPABASE_* queda para la BD kubera; la lectura de analítica (presencia ML,
    # products_snapshot/daily_stock vía supabase_rest) usa ANALYTICS_*.
    # FALLBACK: si están vacías, supabase_rest usa las SUPABASE_* de arriba —
    # comportamiento idéntico al actual hasta que producción defina las nuevas.
    analytics_supabase_url: str = ""
    analytics_supabase_service_role_key: str = ""

    # ── Amazon SP-API (.env.amazon) ───────────────────────────
    amazon_lwa_client_id: str = ""
    amazon_lwa_client_secret: str = ""
    amazon_refresh_token: str = ""
    amazon_seller_id: str = ""
    amazon_marketplace_id: str = "A1AM78C64UM0Y8"  # México
    amazon_sp_api_endpoint: str = "https://sellingpartnerapi-na.amazon.com"
    amazon_lwa_token_url: str = "https://api.amazon.com/auth/o2/token"

    # ── Sincronización de inventario ──────────────────────────
    # Cada cuánto corre el lector de inventario (minutos). OJO (medido 4-ago):
    # NO apagar el sondeo al pasar a webhooks sin encender antes sync_desde_ml —
    # el webhook descarta lo que no está en ml_progress (1 de cada 3 avisos) y
    # sin barrido ese punto ciego se vuelve invisible.
    sync_enabled: bool = True
    sync_interval_min: int = 15
    sync_batch: int = 80
    # F. UNIVERSO (propuesta 4-ago, fase A): el lote del sondeo sale del
    # CATÁLOGO VIVO de ML (/users/{id}/items/search, active+paused) en vez de
    # ml_progress (la bitácora del publicador). Medido: entran ~517
    # publicaciones que hoy no se recorren (186 activas vendiendo) y salen 253
    # muertas que ML ya borró y que hoy pisan la fila de su SKU cada ciclo
    # (síntoma "pausado que en realidad está activo", caso CUNA-0011-AZL).
    # Apagado = comportamiento idéntico al de siempre. Encenderlo = dale de
    # Brandon (flujo vivo, regla 3).
    sync_desde_ml: bool = False

    # ── Refresco de precio AL ABRIR el cajón de un producto ───
    # `services/precio_al_abrir.py`. Al abrir el cajón de un SKU se le pregunta
    # a ML el precio que cobra por SUS publicaciones (1 o 2 llamadas: ningún SKU
    # del catálogo tiene más de dos) y se confirma antes de contestar.
    #
    # Encendido NO significa que se llame a ML: sólo actúa si la petición trae
    # `refrescar=true` en `GET /api/publicaciones`, y hoy quien lo manda es el
    # cajón del panel y nadie más. Con el frontend sin cambiar, esto es 0
    # llamadas. Apagarlo (`PRECIO_AL_ABRIR=false`) deja el cajón exactamente
    # como estaba: lee lo guardado, marcado "sin confirmar" cuando toca.
    #
    # Respeta además `SYNC_ENABLED` — es sincronización de datos con ML, igual
    # que el refresco del webhook.
    precio_al_abrir: bool = True
    # EL PISO. Si la publicación se observó hace menos de esto, no se vuelve a
    # preguntar. 5 min sale de medir el ritmo de cambio real: el barrido en seco
    # del 26-ago vio cambiar 392 de 745 publicaciones respecto de observaciones
    # de ~5 días antes (~10%/día), así que en 5 minutos la probabilidad de que
    # lo guardado ya no sirva es del orden de 0.04%. Subirlo ahorra llamadas y
    # afloja la promesa; bajarlo no compra casi nada porque el piso duro manda.
    precio_al_abrir_piso_min: int = 5
    # PISO DURO. Ni aunque la observación esté "sin confirmar" se pregunta dos
    # veces en menos de esto. Es el tope contra el cajón reabierto en bucle.
    precio_al_abrir_piso_duro_s: int = 60
    # Tope de llamadas por apertura. Hoy no muerde (máximo 2 publicaciones por
    # SKU, medido); está para que un SKU raro no convierta una apertura en una
    # ráfaga.
    precio_al_abrir_max: int = 6
    precio_al_abrir_en_paralelo: int = 3
    # Timeout de CADA llamada y presupuesto de TODO el refresco. Cortos a
    # propósito: detrás hay una persona esperando a que abra el cajón, y un
    # cajón que no abre es peor que uno con el dato de hace una hora.
    precio_al_abrir_timeout_s: float = 4.0
    precio_al_abrir_presupuesto_s: float = 6.0

    # Guardado de notificaciones de webhooks en la tabla (se puede pausar en runtime)
    webhook_registro: bool = True

    # ── App ───────────────────────────────────────────────────
    app_env: str = "development"
    # Orígenes permitidos para el frontend Next.js
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Comodín de CORS. VACÍO a propósito: hasta la v0.239 esto estaba HARDCODEADO
    # en main.py como `https://.*\.(railway\.app|up\.railway\.app|vercel\.app)$`,
    # o sea que la API aceptaba peticiones de CUALQUIER página alojada en esos
    # dominios — y cualquiera puede publicar una en minutos, gratis. El panel
    # entraba por ahí, no por `cors_origins`, que en producción solo traía
    # localhost.
    #
    # Ahora la lista manda y el comodín es la ESCOTILLA: si al apretarlo algo
    # que nadie documentó se queda fuera, se vuelve a abrir poniendo el patrón
    # en esta variable — sin tocar código y sin desplegar, como los demás
    # candados de la casa. Y queda a la vista en Railway, en vez de escondido
    # en una línea de main.py.
    cors_origin_regex: str = ""

    # Cuántos EMBARQUES consulta a ML la tabla de /analisis por carga, para
    # cambiar el envío ESTIMADO por el cobro REAL (v0.85.0). Cada pedido cuesta
    # dos llamadas y se cachea para siempre, así que el gasto se acaba solo;
    # pero la página se refresca cada 60 s y esto es lo único de esa vista que
    # sale a un tercero. En 0 la tabla usa SOLO lo ya cacheado y no llama a ML:
    # es el apagador sin deploy si esas llamadas estorban (rate limit, incidente
    # de tokens). El desglose por cuenta del panel NO depende de esto — sale de
    # kubera.
    tabla_envio_real_presupuesto: int = 150

    # ── Flags de la migración a Supabase (piloto) ─────────────
    # Regla: el valor por default de cada flag = comportamiento actual.
    # Revertir cualquier cambio = regresar el flag a su default (sin redeploy
    # de código; solo cambiar la variable en Railway y reiniciar).
    #
    # mysql_enabled=false → el backend NO crea el pool MySQL; las rutas que lo
    # requieren responden 503. Solo staging corre así (opción A: staging sin
    # MySQL). En producción NUNCA se apaga.
    mysql_enabled: bool = True
    # supabase_dual_write=true → los webhooks escriben ADEMÁS en Supabase
    # (ops.webhook_events, idempotente). Apagarlo = solo MySQL, como siempre.
    supabase_dual_write: bool = False
    # Dual-write del dominio CHANNEL (independiente del de costos, para poder
    # apagar uno sin el otro): el sync de inventario espeja a channel.listings
    # y el trigger de la base alimenta channel.listing_history (monitoreo de
    # precio/stock/FULL por plataforma).
    supabase_dual_write_channel: bool = False
    # Flags de LECTURA por dominio (Fase 5). supabase_read_webhooks=true → la
    # campana y /ml/log leen de ops.webhook_events en vez de MySQL. También se
    # lee de Supabase cuando MYSQL_ENABLED=false (staging). Ante cualquier error
    # de lectura, se cae al camino MySQL: apagar el flag = revertir al instante.
    supabase_read_webhooks: bool = False
    # F5 costos: los GET de /api/crear/costos* leen de costing.* (BD kubera)
    # con fallback automático a MySQL ante cualquier error. Apagar = revertir.
    supabase_read_costing: bool = False
    # F5 channel: leer_inventario/presencia/resumen leen de channel.listings
    # (BD kubera) con fallback automático a MySQL. Apagar = revertir.
    supabase_read_channel: bool = False
    # ── PASO 3: el acta de nacimiento de una publicación llega a kubera YA ──
    # Hoy publicar escribe `ml_progress`/`amazon_progress` (MySQL) y kubera se
    # entera hasta el sync de 15 min — que además lee de esas mismas tablas. O
    # sea que `ml_progress` no es la bitácora del publicador: es lo ÚNICO que
    # sabe de un listing nuevo durante hasta 15 minutos.
    #
    # Mientras ese hueco exista, repuntar cualquiera de los 25 lectores del
    # grupo 4 convierte "publicado hace 30 segundos" en "sin publicar". Este
    # seam es el requisito previo, igual que Crear → core.products lo fue para
    # el corte de `core`.
    #
    # NACE APAGADO: encenderlo mete una escritura en el flujo de PUBLICAR, que
    # es negocio vivo (regla 3, dale de Brandon). Ver services/publicacion_seam.py.
    supabase_seam_publicar: bool = False
    # ── PASO 4: la caché de imágenes ya procesadas se lee de kubera ────────
    # `imagenes_amazon._cache_get` pregunta "¿ya procesé ESTA URL?" y con esto
    # la respuesta sale de `enrich.product_media` en vez de `amazon_imagenes`.
    # Las dos tablas están 1:1 (678/678 desde v0.172.0) y la escritura ya va a
    # las dos, así que encenderlo es reversible sin perder nada.
    # Equivocarse aquí NO corrompe: reprocesa una imagen. Caro, no incorrecto.
    supabase_read_media: bool = False
    # F5 pedidos: el tab Ventas (fuente=pedidos) lee de channel.orders (BD
    # kubera) con fallback automático a MySQL. Apagar = revertir.
    supabase_read_orders: bool = False
    # F5 core: los lookups SKU→wc_id (pedidos_ml.resolver_producto y la
    # categoría ML de costos.py) leen de core.products. None NO es concluyente
    # (seam Crear pendiente): se reconsulta MySQL. Apagar = revertir.
    supabase_read_core: bool = False
    # F6 costos (CORTE, opción A — espejo inverso): las ESCRITURAS de costos
    # (costos_finales / costos_validados / bitácora) van PRIMERO a costing.* /
    # ops.process_log en la BD kubera (fuente de verdad) y MySQL pasa a ser el
    # espejo best-effort. Con kubera caída el negocio no se bloquea: se escribe
    # MySQL y el evento queda encolado en espejo_kubera_log (reprocesable).
    # Apagar = volver al dual-write clásico (MySQL manda). Requiere racha de
    # actas cumplida (costing 14/14, 06-ago-2026) y dale de Brandon.
    supabase_write_costing: bool = False
    # F6 pedidos (CORTE, opción A): el registro de cada venta escribe PRIMERO
    # channel.orders(+order_items) en kubera (síncrono, misma transacción) y
    # pedidos_ml MySQL pasa a espejo inverso en hilo. Con kubera caída: MySQL
    # aguanta y el evento viaja por el espejo viejo (cola espejo_kubera_log).
    # Racha orders-deltas 15/14 (06-ago). Apagar = revertir al dual-write.
    supabase_write_orders: bool = False
    # F6 channel (CORTE, opción A): cada tanda del sync de inventario escribe
    # PRIMERO channel.listings (kubera) y canal_inventario MySQL pasa a espejo
    # inverso en hilo. Con kubera caída: MySQL aguanta y el siguiente ciclo
    # (15 min) auto-sana kubera — este dominio no necesita cola. Racha
    # channel-deltas 17/14 (06-ago). Apagar = revertir al dual-write.
    supabase_write_channel: bool = False
    # DESMANTELAMIENTO de channel (paso 1 de 4). En true, cada tanda del sync
    # sigue copiándose a canal_inventario en un hilo (espejo inverso del corte).
    # En FALSE, MySQL deja de recibir escrituras del sync y queda congelado:
    # es el primer movimiento del retiro, y se hace EN EL MISMO MOMENTO que se
    # apaga el cron deltas-channel — con MySQL congelado el acta reportaría
    # divergencia por construcción. Revertir = true; el ciclo siguiente (15 min,
    # full-refresh por tanda) repuebla canal_inventario solo. El respaldo de
    # emergencia (kubera caída → MySQL absorbe) NO se toca: sigue vivo.
    channel_espejo_inverso: bool = True
    # DESMANTELAMIENTO de costing y orders (mismo paso 1 que channel). En false,
    # el espejo inverso a MySQL (costos_*/pedidos_ml) deja de escribirse y la
    # tabla queda congelada; el cron de deltas correspondiente se retira EN EL
    # MISMO movimiento (con MySQL congelado el acta acusaría divergencia por
    # construcción). OJO, diferencia con channel: aquí el espejo es POR EVENTO,
    # no full-refresh — revertir el flag NO repuebla lo perdido; si pasaron
    # días apagado, la reversa completa necesita backfill desde kubera. La
    # resiliencia (kubera caída → MySQL absorbe + cola) NO depende de estos
    # flags: vive en el camino de error y se queda hasta F8.
    costing_espejo_inverso: bool = True
    orders_espejo_inverso: bool = True
    # F6 core (CORTE): los eventos de ciclo de vida del maestro (nacimiento en
    # Crear, publish, trash/deleted de la auditoría) escriben core.products
    # SÍNCRONO en la misma petición (primaria); si kubera falla, el evento cae
    # a la cola del espejo clásico (espejo_kubera_log, reprocesable) + Slack.
    # El ETL de las 06:15 queda de AUDITOR/respaldo: su acta pasa a medir el
    # hueco del seam (resultado con_deltas si tuvo algo que corregir). Hueco
    # medido en cero desde el 08-ago. Apagar = volver al seam encolado.
    supabase_write_core: bool = False
    # F6 categorías (CORTE): la elección de categoría ML del panel (la que
    # MANDA, regla 2) escribe channel.categories + channel.product_category
    # SÍNCRONO al guardarse — kubera se entera en vivo, no hasta el ETL.
    # Mismo fallback a cola + Slack. Apagado = solo el ETL nocturno (hoy).
    supabase_write_categorias: bool = False

    # MÁRGENES (grupo 5 del desmantelamiento, 14-ago-2026): las tres cachés del
    # tab —costo real de envío, peso medido por ML, visitas— pasan de MySQL a
    # enrich.*. Flag PROPIO y no el de channel: la reversa tiene que ser por
    # dominio, que es lo que salvó los cinco cortes. Nace apagado; se enciende
    # cuando las tablas existen en kubera Y el backfill está verificado.
    supabase_write_margenes: bool = False
    # Webhook de WooCommerce → core.products (11-ago). Los tres seams de core
    # solo ven lo que pasa POR EL PANEL, y el catálogo también se edita desde
    # wp-admin: 444 fichas guardadas ahí alguna vez y 39 de 253 SKUs con un
    # título distinto al que dejó el panel. Eso NO es cubrible con más seams en
    # nuestro código — hay que escuchar en la fuente. Woo manda `product.updated`
    # con la ficha completa y de ahí salen sku/name/wc_id/status, que es
    # exactamente lo que el registro civil necesita. Encender = crear el webhook
    # en wp-admin (Brandon) + estas dos variables. Apagar = esta en false.
    woo_webhook_enabled: bool = False
    # Secreto del webhook (el mismo que se teclea en wp-admin). SIN él el
    # endpoint queda en OBSERVACIÓN: registra lo que llega pero no escribe —
    # una firma que no se puede verificar no autoriza a tocar el maestro.
    woo_webhook_secret: str = ""
    # Candado de arranque: la referencia (subdominio) del proyecto Supabase de
    # PRODUCCIÓN. Ver validar_ambiente().
    supabase_prod_ref: str = ""
    # Auth mínima: si api_key está definida y auth_enforced=true, los endpoints
    # de escritura/ops exigen el header X-API-Key. Con auth_enforced=false solo
    # se registra en logs quién habría sido rechazado (rollout gradual).
    api_key: str = ""
    auth_enforced: bool = False
    # Escotilla del middleware (core/middleware.py): rutas EXTRA que nunca piden
    # credencial, en CSV. Existe para abrir una ruta olvidada sin hacer commit,
    # si el censo del modo observación descubre un consumidor legítimo que nadie
    # documentó. Las imprescindibles (/api/health y el webhook de ML) ya están
    # en el código: esta variable NO hace falta para el funcionamiento normal.
    auth_rutas_abiertas: str = ""
    # Segundos que se guarda en memoria la verificación de un token contra
    # Supabase Auth. Evita una llamada de red por cada petición del panel.
    auth_cache_seg: int = 300
    # RBAC (Temu III.2). Independiente de auth_enforced a propósito: se puede
    # exigir credencial sin aplicar roles todavía, y encender los roles después
    # de medir. Con false, un rol insuficiente se registra pero NO se bloquea.
    rbac_enforced: bool = False
    # /docs, /redoc y /openapi.json exponen el mapa completo de los 84 endpoints.
    # En producción se apagan; con esto se pueden reabrir sin redeploy de código.
    docs_publicas: bool = False

    # Persistencia de notificaciones en MySQL (webhook_eventos). Brandon pidió
    # DESVINCULAR el webhook de la base (2026-07-17): con false, las
    # notificaciones se procesan al vuelo (stock + pedidos) sin insertarse en
    # MySQL. El espejo de Supabase (ops.webhook_events) es independiente y lo
    # gobierna supabase_dual_write. La campana deja de mostrar eventos ML salvo
    # que se encienda supabase_read_webhooks.
    webhook_guarda_mysql: bool = False

    # ── Espejo kubera (dual-write propio, fase de descubrimiento) ─
    # Extiende el patrón del compañero (channel/costing_mirror, INTOCABLES) a
    # los escritores sin espejo (services/kubera_mirror.py). Nace APAGADO:
    # encenderlo en producción es cambio de flujo vivo (regla 3 de CLAUDE.md).
    # kubera_db_url: Postgres de la BD centralizada "kubera" (en DEV, el
    # Supabase de desarrollo). Mismo formato de pooler que supabase_db_url.
    kubera_db_url: str = ""
    kubera_mirror_enabled: bool = False
    # CSV opcional de tablas MySQL de ORIGEN para encender el espejo tabla por
    # tabla (p. ej. "ml_backlog,crear_logs"). Vacío = todas las censadas.
    kubera_mirror_tablas: str = ""

    # ── Fan-out de stock DROP hacia los canales ───────────────────
    # Tras una venta no-FULL, Woo descuenta pero los OTROS canales siguen
    # ofreciendo el número viejo (riesgo de sobreventa). El fan-out replica el
    # stock DROP a las publicaciones ACTIVAS y no-FULL (services/fanout_stock.py).
    # Nace APAGADO y en DRY-RUN: encender la escritura toca marketplaces vivos
    # (regla 3 de CLAUDE.md — dale explícito de Brandon).
    fanout_enabled: bool = False
    fanout_dry_run: bool = True
    # CSV de canales con escritura habilitada (encendido gradual). Vacío = todos.
    fanout_canales: str = ""
    # Piezas de colchón que NO se publican (cubre la ventana venta→escritura).
    fanout_reserva: int = 0

    # ── Movimientos de bodega FULL / FBA → Woo ────────────────────
    # Cuando se manda mercancía a FULL, esas piezas SALEN del almacén propio y
    # Woo no se enteraba (services/stock_full.py). ML lo avisa por el webhook
    # `fbm_stock_operations` en segundos; Amazon no tiene webhook (403) y se
    # revisa por comparación de fotos del inventario FBA.
    # Nace APAGADO: encenderlo MUEVE INVENTARIO REAL (regla 3 de CLAUDE.md).
    full_watch_enabled: bool = False
    full_watch_fba_min: int = 15   # cada cuántos minutos se revisa el FBA
    # MODO OBSERVACIÓN (default True): clasifica y ANOTA lo que haría, sin tocar
    # Woo. Nació del incidente del 27-jul: los tipos de operación se conocían por
    # muestreo y el muestreo no vio TRANSFER_RESERVATION ni WITHDRAWAL_RESERVATION.
    # Ponerlo en false = el vigilante empieza a MOVER INVENTARIO.
    full_watch_solo_registro: bool = True

    # ── Pedidos ML → WooCommerce + transición de inventario ───
    # Cada venta de ML se convierte en pedido de Woo con el precio REAL
    # congelado (services/pedidos_ml.py), disparado por el webhook orders_v2.
    # Con descuenta_stock=false el pedido nace marcado "stock ya descontado"
    # y NO toca inventario (modo REGISTRO: Odoo sigue siendo el maestro).
    # Encender descuenta_stock = el corte de inventario a Woo.
    pedidos_wc_enabled: bool = True
    pedidos_wc_descuenta_stock: bool = False
    # Refresco de VENTAS contra la API de ML (tab Ventas): con false, el tab
    # sirve solo el caché ya guardado (días cerrados) y NO le pide nada nuevo a
    # ML — modo "puros pedidos de Woo" (Brandon, 2026-07-17). Los pedidos del
    # webhook siguen vivos: obtener la orden vendida no es "sincronización".
    ventas_ml_refresh: bool = True
    # Pedidos de AMAZON por sondeo (Amazon no tiene webhook simple; con ~4
    # órdenes/día un poll de 5 min es tiempo real en la práctica). FBA nace
    # protegido (almacén de Amazon); MFN descuenta bodega en Woo.
    pedidos_amazon_enabled: bool = True
    # Refresco DIARIO del reporte FBA (pestaña /analisis/fba, Eduardo 18-ago):
    # pide "Manage FBA Inventory" a la Reports API cada mañana y reemplaza
    # ops.fba_snapshot. Nace ENCENDIDO por pedido explícito; se apaga con la
    # variable, sin deploy, como todo flujo. La hora va en UTC porque el
    # scheduler corre en UTC: 13 UTC = 07:00 de México.
    fba_refresco_auto: bool = True
    fba_refresco_hora_utc: int = 13
    pedidos_amazon_min: int = 5
    # Pedidos de Temu/TikTok vía M2E Cloud (order/find por canal). El token se
    # genera en M2E: Settings → Catalog → API. Sondeo suave (volumen ~0 aún).
    m2e_api_token: str = ""
    pedidos_m2e_enabled: bool = True
    pedidos_m2e_min: int = 10
    # ── TikTok Shop: vía PROPIA (app de ISV en el Partner Center) ─
    # M2E dejó la conexión de TikTok en is_valid=false desde julio y nunca se
    # re-autorizó. Esto abre el canal sin depender de M2E: app propia con
    # Enable API y Redirect URL a nuestro backend (/api/tiktok/callback).
    # NACE APAGADO: hablar con un marketplace vivo es cambio de flujo (regla 3).
    tiktok_enabled: bool = False
    tiktok_app_key: str = ""
    tiktok_app_secret: str = ""
    # service_id de la app (Partner Center → App & Service); arma la URL de
    # consentimiento a la que se manda al seller.
    tiktok_service_id: str = ""
    # Debe ser IDÉNTICA a la registrada en el Partner Center: una diagonal final
    # de más y TikTok rechaza el canje del code.
    tiktok_redirect_uri: str = (
        "https://backendomnicanal-production.up.railway.app/api/tiktok/callback"
    )
    # Renovación PROACTIVA del access_token (~7 días de vida). La reactiva
    # (105002 → refresh → reintento, en tiktok.llamar) va SIEMPRE encendida,
    # igual que la regla 8 de ML; este job solo evita que un canal sin tráfico
    # llegue con el token vencido a su siguiente escritura. Nace apagado
    # (regla 3); no depende de TIKTOK_ENABLED a propósito — producción escribe
    # stock con ese flag en false y atarlo repetiría el apagón del 15-ago.
    tiktok_refresh_enabled: bool = False
    tiktok_refresh_min: int = 360          # cada 6 h; renueva si faltan <24 h
    # Censo periódico del catálogo TikTok → channel.listings (status + stock).
    # Sin él, el espejo se congela en el último censo manual y el fan-out
    # decide "sin_cambio" contra una foto vieja — y las activaciones que corre
    # `tk_activar.py` desde el escritorio son invisibles (597 DRAFT que pueden
    # estar YA a la venta). Nace apagado (regla 3).
    tiktok_censo_enabled: bool = False
    tiktok_censo_min: int = 120
    # Censo gemelo para Temu (temu_censo.py). Intervalo más laxo a propósito:
    # el presupuesto de API de Temu es más estricto y su catálogo cambia menos.
    temu_censo_enabled: bool = False
    temu_censo_min: int = 240

    # ── TEMU (mallId 635517742093915, regionId 128) ──────────────────────────
    # Token de larga vida emitido en el Seller Center: Temu no usa el baile de
    # OAuth de TikTok. Si el vendedor re-autoriza eventos, sale un token NUEVO y
    # hay que reemplazarlo aquí (ver docs/TEMU_MANUAL.md §1, paso 4).
    temu_app_key: str = ""
    temu_app_secret: str = ""
    temu_access_token: str = ""
    temu_api_base: str = "https://openapi-b-global.temu.com/openapi/router"

    # ── WALMART MX (marketplace.walmartapis.com) ─────────────────────────────
    # Basic (id:secret) → /v3/token. El token dura poco, así que se pide por
    # llamada en vez de cachearlo: pedirlo es barato y un token vencido en
    # caché es un fallo intermitente de los que cuesta reproducir.
    wm_client_id: str = ""
    wm_client_secret: str = ""

    # Vigilante de Odoo: compara qty_available contra la última foto
    # (productos.stock_odoo) cada N minutos; los cambios van a la campana.
    # Con auto_push=true además empuja el stock nuevo a Woo (activar solo
    # después de la carga inicial Odoo→Woo).
    odoo_watch_enabled: bool = True
    odoo_watch_min: int = 30
    # OJO: su empuje manda el VALOR ABSOLUTO de Odoo. Desde que Woo es la fuente
    # de verdad de las ventas (17-jul) eso RESUCITA mercancía vendida. El empuje
    # correcto (por DELTA) vive en `stock_watch`; esto se queda apagado.
    odoo_watch_auto_push: bool = False

    # ── Alta automática de SKUs nuevos de Odoo (sincronizar_drafts) ───
    # El alta Odoo→Woo era el ÚNICO paso del pipeline que dependía de que
    # alguien apretara el botón "Sincronizar Odoo" de la pestaña Crear. Mientras
    # nadie lo apretaba, esos SKUs existían en Odoo y no en Woo: el ETL nocturno
    # los daba de alta como `odoo_only` y el acta salía `con_deltas` — el 13-ago
    # las TRES altas del hueco fueron eso (ACC-0816-AMA/AZL/ROS).
    sync_odoo_skus_enabled: bool = True
    sync_odoo_skus_min: int = 360          # cada 6 h: llega antes del ETL de las 00:15
    sync_odoo_skus_limite: int = 100       # mismo lote que el botón
    # SOLO IDENTIDAD: el alta NO siembra inventario (ver _borrador_wc). En true
    # vuelve el comportamiento del botón manual, que sí mandaba `free_qty`.
    sync_odoo_incluir_stock: bool = False

    # ── Vigilante de inventario Odoo → Woo → canales (stock_watch.py) ─
    # Cierra el círculo: Odoo aporta DELTAS a Woo (nunca su valor absoluto, que
    # resucitaría lo vendido) y CUALQUIER cambio de stock en Woo se replica a
    # los canales por el fan-out. Foto-contra-foto: no se puede evadir.
    # Nace APAGADO: encenderlo MUEVE INVENTARIO REAL (regla 3 de CLAUDE.md).
    stock_watch_enabled: bool = False
    stock_watch_min: int = 20
    # MODO OBSERVACIÓN (default True): anota lo que haría sin tocar nada.
    stock_watch_solo_registro: bool = True
    # CORTACIRCUITOS: si una pasada ve más cambios que esto, NO aplica nada y
    # avisa. Una edición masiva en Odoo no puede vaciar todos los canales.
    stock_watch_tope: int = 300
    # ── PASO 2 de la migración: la foto sale de MySQL (ops.stock_watch_photo).
    # DOS flags y no uno, y se encienden en ESTE orden con días de por medio:
    #
    #   1) …WRITE_STOCK_WATCH=true  → la foto se escribe en LOS DOS lados.
    #      MySQL sigue mandando; kubera solo se llena. Reversible sin costo.
    #   2) (varios días) `comparar_stock_watch_foto.py` cada mañana.
    #   3) …READ_STOCK_WATCH=true   → la DECISIÓN pasa a kubera. Aquí sí cambia
    #      lo que el vigilante le escribe a Woo: es un flujo vivo (regla 3).
    #
    # Al revés que en el paso 1 (cachés), donde lectura y escritura viajaron
    # juntas. Aquí no se puede: esta foto no guarda un valor, guarda el ESTADO
    # ANTERIOR contra el que se calcula el delta. Una foto nueva y vacía leída
    # como buena haría que el vigilante recalculara el mundo entero de un golpe.
    supabase_write_stock_watch: bool = False
    supabase_read_stock_watch: bool = False

    # ── PASO 3, BLOQUE 1: las seis lecturas de ml_progress / amazon_progress ──
    # `studio.estado_publicacion` (2) · `presencia` (2) · `publicar` (2). Las
    # seis preguntan lo mismo con formas distintas: "¿en qué canales está
    # publicado este SKU, con qué id, y de qué tipo en Amazon?".
    #
    # APAGADO = MySQL manda, igual que hasta hoy. ENCENDIDO = contestan las
    # gemelas de `channel_read` (channel.listings).
    #
    # Es LECTURA pura: no escribe en ningún canal, así que no cae en la regla 3.
    # Lo que sí cambia es de dónde sale lo que el panel MUESTRA como publicado.
    #
    # Por qué se puede ahora y no antes: hasta el 16-ago `ml_progress` era lo
    # único que conocía una publicación recién nacida durante hasta 15 min (lo
    # que tardaba el sync). Repuntar antes del seam habría convertido
    # "publicado hace 30 segundos" en "sin publicar". Con el seam midiendo 2 s
    # de mediana esa ventana desapareció.
    supabase_read_publicaciones: bool = False

    # ── PASO 0: los DOS candados que viven en `fanout_log` ───────────────────
    # APAGADO = se leen de MySQL, igual que hoy. ENCENDIDO = `channel.orders`
    # (compensación por pedido), `ops.fulfillment_operations` (operaciones de
    # bodega) y `ops.fba_watermark` (la marca de agua del FBA).
    #
    # ⚠️ ESTOS NO SON LECTURA DE PANTALLA: deciden si se MUEVE MERCANCÍA.
    # Un candado que contesta "no lo he hecho" cuando sí lo hizo mueve stock dos
    # veces. Encenderlo es regla 3 — necesita el dale de Brandon.
    #
    # Y hay un agravante que este flag no arregla solo: `fanout_stock.py` tiene
    # un `CREATE TABLE IF NOT EXISTS fanout_log`, así que si esa tabla se borra
    # **el propio lector la recrea vacía y después le pregunta**. Respuesta
    # garantizada: "no lo hice". Ese CREATE hay que quitarlo ANTES del retiro.
    #
    # Condición de seguridad heredada: el día que se le quite el "solo registro"
    # al vigilante FULL, esto tiene que estar encendido y verificado.
    supabase_read_candados: bool = False
    # ESCRITURA de las marcas, aparte de la LECTURA. Si no existiera, las
    # escrituras irian detras de la bandera de lectura y kubera nunca podria
    # ponerse al dia ANTES de encenderla — el huevo y la gallina.
    #
    # Se vio midiendo, no razonando: al comparar los tres candados antes de
    # encender, 10 SKUs tenian la marca de agua del FBA distinta. Las de kubera
    # eran del 12-ago (la copia inicial) y MySQL habia seguido avanzando, porque
    # `_marcar_agua_fba` solo escribia con la bandera de LECTURA prendida.
    #
    # Con esta, el orden vuelve a ser el de siempre: escribir en los dos lados,
    # comparar unos dias, y solo entonces mover la lectura.
    supabase_write_candados: bool = False

    # ── La BITACORA del fan-out, aparte del CANDADO ──────────────────────────
    # Bandera propia y NO la de los candados, a proposito: es el hallazgo del
    # censo del 20-ago. `fanout_log` guardaba dos cosas distintas —la marca de
    # idempotencia y el historial— y la migracion 0022 se llevo solo la marca,
    # dejando cuatro lectores huerfanos.
    #
    # Separarlas en dos banderas separa tambien el permiso: mover una PANTALLA
    # no necesita el dale de Brandon; mover MERCANCIA si. Con una sola bandera,
    # el dashboard quedaria rehen de la aprobacion del candado.
    supabase_read_fanout_log: bool = False
    # Escritura doble de la bitacora. Segura sola: es el mismo evento en dos
    # lados, y la bitacora no decide nada.
    supabase_write_fanout_log: bool = False

    # ── PASO 6: los tokens de Mercado Libre ──────────────────────────────────
    # WRITE = el par cifrado se guarda tambien en `ops.ml_tokens`. Es SEGURO
    # aunque MySQL siga mandando: es el MISMO valor, calculado una sola vez por
    # `meli.refrescar_token`. Lo que no se puede duplicar es la RENOVACION (ML
    # rota el refresh_token en cada uso y dos renovadores se invalidan), y este
    # flag no crea un segundo renovador — solo un segundo destino de escritura.
    #
    # READ = `_access_token` y `_credenciales_refresh` consideran a kubera en el
    # arbitraje por recencia. Mientras haya doble escritura empatan; el dia que
    # MySQL se apague, kubera gana sola.
    #
    # ⚠️ PRERREQUISITO del READ: `MELI_APP_ID` y `MELI_CLIENT_SECRET` definidas
    # en Railway. El `client_secret` NO se copia a kubera a proposito — es el que
    # esta expuesto en el repo `publicador` y pendiente de rotacion; copiarlo a
    # una tabla nueva seria esparcir un secreto quemado.
    supabase_write_tokens: bool = False
    supabase_read_tokens: bool = False

    # ── F2: espejo del DROP (bodega propia) → channel.listings 'general' ──
    # Lee stock_watch_foto (la que ya refresca el vigilante de arriba) y la
    # espeja a la BD kubera. NO mueve inventario: solo copia lo que Woo ya
    # dice, hacia el panel de Análisis. Nace apagado como todo flujo vivo.
    drop_mirror_enabled: bool = False
    drop_mirror_min: int = 20

    # ── Notificador de alertas a Slack (services/alertas.py) ───
    # Webhook entrante amarrado al canal #alertas-omnicanal. Sin URL, el
    # notificador entero es un no-op (apagable sin deploy). La URL es la llave
    # del canal: SOLO como variable de Railway, nunca en el repo.
    slack_webhook_url: str = ""
    # Canal APARTE, solo para las DOS revisiones diarias del costeo (margen
    # negativo · top 10 con costo sin verificar) → #avisos-costos. No son
    # incidentes: un margen negativo se lee con calma, "los pedidos pararon" se
    # atiende ya. Mezclados, el canal de incidentes se vuelve ilegible y el de
    # costos no llega a existir.
    #
    # VACÍA = CAE A `slack_webhook_url` Y NO CAMBIA NADA. Ésa es la razón de que
    # el código pueda entrar a producción ANTES de que el webhook exista: el día
    # que se ponga la variable en Railway, las dos alarmas se mudan solas sin
    # otro deploy. El ruteo vive en `alertas._WEBHOOK_POR_TIPO`.
    #
    # Misma regla que la de arriba: la URL es la LLAVE del canal — solo como
    # variable de Railway, nunca en el repo ni en el chat. Cómo se genera:
    # api.slack.com/apps → la app de Kubera → Incoming Webhooks → Add New
    # Webhook to Workspace → elegir #avisos-costos.
    slack_webhook_costos: str = ""
    # Vigilante de ausencias (actas faltantes, silencio de ventas): cada N min.
    alertas_min: int = 15

    # RECONSTRUCTOR DE PEDIDOS (Woo → kubera). La red de seguridad que reemplaza
    # a MySQL: si kubera no respondió al registrar una venta, el pedido igual
    # quedó en Woo y esto lo apunta después. NACE ENCENDIDO por decisión de
    # Eduardo (19-ago-2026) — sin él, apagar MySQL deja las ventas sin colchón y
    # el registro incompleto reabre la puerta a los pedidos duplicados.
    # Solo INSERTA lo que falta; jamás reescribe una venta ya registrada.
    # Reversa: RECONSTRUIR_ORDERS_ENABLED=false, sin deploy.
    reconstruir_orders_enabled: bool = True
    reconstruir_orders_min: int = 60
    reconstruir_orders_dias: int = 2
    # Hora UTC a partir de la cual una acta diaria ausente es alarma (los crons
    # corren 06:30/06:47/07:15 UTC; a las 07:45 ya deberían estar las 3).
    alertas_actas_hora_utc: int = 8
    # Horas sin ventas nuevas (en horario hábil CDMX) que disparan la alerta.
    alertas_silencio_horas: int = 4
    # Hora UTC a partir de la cual corren las DOS revisiones diarias del costeo
    # (margen negativo · top 10 con costo sin verificar). 15 UTC = 9 a.m. CDMX:
    # son avisos que alguien tiene que LEER y accionar, no incidentes — a las
    # 2 a.m. nadie los abre. Corren una sola vez al día (`alertas._toca_hoy`),
    # aunque el vigilante despierte cada `alertas_min`.
    alertas_costos_hora_utc: int = 15

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def supabase_ref(self) -> str:
        """Referencia (subdominio) del proyecto Supabase al que apunta SUPABASE_URL."""
        m = re.match(r"https?://([a-z0-9]+)\.supabase\.co", self.supabase_url.strip())
        return m.group(1) if m else ""


def validar_ambiente(s: Settings) -> None:
    """Candado de arranque anti-mezcla de ambientes.

    Bloquea SOLO ante contradicción comprobada (peor escenario: staging
    escribiendo en el Supabase de producción). Si falta información para
    comparar, advierte en logs pero deja arrancar — bloquear por ausencia de
    config haría más daño del que evita.
    """
    log = logging.getLogger("omnicanal.config")
    env = s.app_env.strip().lower()
    ref = s.supabase_ref
    prod_ref = s.supabase_prod_ref.strip()

    if not prod_ref or not ref:
        log.warning(
            "Candado de ambiente sin datos para comparar "
            "(SUPABASE_PROD_REF=%s, SUPABASE_URL ref=%s) — arranco sin verificar.",
            "definido" if prod_ref else "VACÍO", ref or "VACÍA",
        )
        return

    if env != "production" and ref == prod_ref:
        raise RuntimeError(
            f"CANDADO DE AMBIENTE: APP_ENV={s.app_env!r} pero SUPABASE_URL apunta al "
            "proyecto de PRODUCCIÓN. Me niego a arrancar. Corrige las variables "
            "SUPABASE_* de este ambiente."
        )
    if env == "production" and ref != prod_ref:
        raise RuntimeError(
            f"CANDADO DE AMBIENTE: APP_ENV='production' pero SUPABASE_URL apunta a un "
            f"proyecto que NO es el de producción (ref detectada: {ref}). Me niego a "
            "arrancar. Corrige SUPABASE_URL o SUPABASE_PROD_REF."
        )
    log.info("Candado de ambiente OK: APP_ENV=%s, Supabase ref=%s.", s.app_env, ref)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
