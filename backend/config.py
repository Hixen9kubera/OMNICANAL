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

    # ── Creación de productos (Alibaba → Woo) ─────────────────
    apify_api_key: str = ""
    apify_alibaba_actor: str = "happitap~alibaba-product-scraper"

    # ── Competencia (Mercado Libre) ───────────────────────────
    # La API de ML da las VISITAS de cualquier publicación pero NO la ficha
    # (título/imagen/precio/descripción/posición) de las ajenas: /items/{id}
    # responde 403 y /sites/MLM/search también. Esa parte la trae este actor.
    apify_ml_actor: str = "piotrv1001~mercado-libre-listings-scraper"
    # ML sirve un interstitial de "tráfico sospechoso" a IPs de datacenter, así
    # que el scraping va por proxy residencial mexicano (igual que Alibaba).
    apify_proxy_pais: str = "MX"
    # Resultados por búsqueda. Con detalle son $0.025/item (trae descripción y
    # unidades vendidas); sin detalle $0.003 pero sin esos dos campos.
    competencia_top: int = 25
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
    # Cada cuánto corre el lector de inventario (minutos). Cuando se
    # implementen webhooks, poner sync_enabled=false y depender de ellos.
    sync_enabled: bool = True
    sync_interval_min: int = 15
    sync_batch: int = 80
    # Guardado de notificaciones de webhooks en la tabla (se puede pausar en runtime)
    webhook_registro: bool = True

    # ── App ───────────────────────────────────────────────────
    app_env: str = "development"
    # Orígenes permitidos para el frontend Next.js
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

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
    # F5 pedidos: el tab Ventas (fuente=pedidos) lee de channel.orders (BD
    # kubera) con fallback automático a MySQL. Apagar = revertir.
    supabase_read_orders: bool = False
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
    pedidos_amazon_min: int = 5
    # Pedidos de Temu/TikTok vía M2E Cloud (order/find por canal). El token se
    # genera en M2E: Settings → Catalog → API. Sondeo suave (volumen ~0 aún).
    m2e_api_token: str = ""
    pedidos_m2e_enabled: bool = True
    pedidos_m2e_min: int = 10
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
    # Vigilante de ausencias (actas faltantes, silencio de ventas): cada N min.
    alertas_min: int = 15
    # Hora UTC a partir de la cual una acta diaria ausente es alarma (los crons
    # corren 06:30/06:47/07:15 UTC; a las 07:45 ya deberían estar las 3).
    alertas_actas_hora_utc: int = 8
    # Horas sin ventas nuevas (en horario hábil CDMX) que disparan la alerta.
    alertas_silencio_horas: int = 4

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
