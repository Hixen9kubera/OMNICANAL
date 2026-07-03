"""
config.py — Lee variables del .env y .env.amazon (API keys de WooCommerce,
Odoo, Mercado Libre, Amazon, Claude, etc.) y las expone como un objeto único.

Se cargan DOS archivos:
  - .env          → Odoo, WooCommerce, DB MySQL, IA
  - .env.amazon   → credenciales Amazon SP-API (San Corpe)

Ambos viven en la RAÍZ del proyecto (un nivel arriba de /backend).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# La raíz del proyecto es el padre de /backend
ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_MAIN = ROOT_DIR / ".env"
ENV_AMAZON = ROOT_DIR / ".env.amazon"


class Settings(BaseSettings):
    # pydantic-settings carga ambos archivos; las claves de .env.amazon
    # se añaden encima de las de .env.
    model_config = SettingsConfigDict(
        env_file=(ENV_MAIN, ENV_AMAZON),
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

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
