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
    # Candado de arranque: la referencia (subdominio) del proyecto Supabase de
    # PRODUCCIÓN. Ver validar_ambiente().
    supabase_prod_ref: str = ""
    # Auth mínima: si api_key está definida y auth_enforced=true, los endpoints
    # de escritura/ops exigen el header X-API-Key. Con auth_enforced=false solo
    # se registra en logs quién habría sido rechazado (rollout gradual).
    api_key: str = ""
    auth_enforced: bool = False

    # Persistencia de notificaciones en MySQL (webhook_eventos). Brandon pidió
    # DESVINCULAR el webhook de la base (2026-07-17): con false, las
    # notificaciones se procesan al vuelo (stock + pedidos) sin insertarse en
    # MySQL. El espejo de Supabase (ops.webhook_events) es independiente y lo
    # gobierna supabase_dual_write. La campana deja de mostrar eventos ML salvo
    # que se encienda supabase_read_webhooks.
    webhook_guarda_mysql: bool = False

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
    odoo_watch_auto_push: bool = False

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
