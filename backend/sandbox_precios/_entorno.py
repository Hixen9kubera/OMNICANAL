"""Arranque del LABORATORIO de precios: credenciales en memoria, nada en disco.

El laboratorio vive en su propia rama/worktree y NO lleva copia del .env:

- En la laptop, las variables se cargan en el proceso desde el .env del repo
  principal (sin escribirlas en ningún lado).
- En Railway (servicio `laboratorio-precios`), las variables ya vienen del
  entorno — por REFERENCIA a las del backend — y no se lee ningún archivo.

En ambos casos se fuerzan las banderas que apagan cualquier camino de escritura
y se instalan los candados de `candados.py`. Todo lo que corre aquí es de
LECTURA: SELECT en kubera, search_read en Odoo, GET en las APIs de los
marketplaces. Los resultados van a archivos JSON en `LAB_DATOS_DIR`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SANDBOX_BACKEND = Path(__file__).resolve().parent.parent
REPO_PRINCIPAL = Path(os.environ.get(
    "OMNICANAL_REPO", r"C:\Users\diaz2\OneDrive\Escritorio\omnicanal"))

EN_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_SERVICE_ID"))

# Banderas que apagan cualquier camino de escritura que un import pudiera
# encender (scheduler, warm-up de ventas, espejos) y que fijan las lecturas a
# kubera (no al MySQL congelado).
_FORZADAS = {
    "VENTAS_ML_REFRESH": "false",
    "SUPABASE_READ_COSTING": "true",
    "SUPABASE_WRITE_COSTING": "false",
    "KUBERA_MIRROR_ENABLED": "false",
    "SYNC_ENABLED": "false",
    # El token de ML sale SOLO de kubera (ops.ml_tokens), nunca de MySQL.
    "TOKENS_SOLO_KUBERA": "true",
    # El ÍNDICE de packing lists copiados (costing.packing_archivos, SELECT) ubica
    # a Ferraforme y completa el inventario. Los BYTES no salen del bucket: sin
    # service role, `packing_drive_carpeta.bajar` cae a Drive (mismo sha256).
    "PACKING_LEER_STORAGE": "true",
    "PYTHONIOENCODING": "utf-8",
    # El laboratorio no lee MySQL (datos congelados de agosto) ni avisa a Slack de
    # producción. Vacías = los módulos de producción las toman como apagadas.
    "MYSQL_ENABLED": "false",
    "SLACK_WEBHOOK_URL": "",
    "SLACK_WEBHOOK_COSTOS": "",
    "SLACK_WEBHOOK_MARGENES": "",
}

# En la laptop, del .env del repo principal se cargan SOLO estas variables (la
# lista del README, "Referenciadas desde BackendOmnicanal") y las de estos
# prefijos. El .env completo trae llaves de Woo, Amazon, TikTok, Temu, MySQL…
# que el laboratorio no usa: si no están en el proceso, ningún camino lateral
# puede usarlas.
# FUERA a propósito (auditoría de seguridad, fase 3):
# - `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`: la service role salta RLS en
#   Storage y PostgREST. Los packing lists bajan de Drive (público) cuando el
#   bucket no está a la mano — `packing_drive_carpeta.bajar` cae solo — y los
#   bytes son los mismos (huella sha256 comparada contra `costing.packing_archivos`).
# - `DEEPSEEK_*` / `GEMINI_*`: packing100 solo usa la IA de Anthropic, y el candado
#   HTTP no deja POST a otro anfitrión.
_LISTA_BLANCA = frozenset({
    "SUPABASE_DB_URL", "DB_ENCRYPTION_KEY",
    "ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD", "INVENTARIO_FLUJO_TIMEOUT_S",
    "ANTHROPIC_API_KEY", "TZ",
})
_PREFIJOS_BLANCOS = ("LAB_", "SUPABASE_READ_", "PL_DRIVE_", "PACKING_", "ANTHROPIC_", "OMNICANAL_")

# Lo que SE QUITA del proceso aunque venga del entorno (también en Railway, donde
# no hay lista blanca: si alguien referencia de más en el servicio, aquí se cae).
# Credenciales de escritura o de otros sistemas que el laboratorio no usa, y
# `APP_ENV` (en `staging` haría que `config.py` leyera `env.staging`).
_PROHIBIDAS = frozenset({
    "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY", "KUBERA_DB_URL",
    "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME", "APP_ENV",
})
_PREFIJOS_PROHIBIDOS = ("MELI_CLIENT_SECRET", "MELI_APP_ID", "WC_", "WPDB_", "WP_DB_", "AMAZON_",
                        "TIKTOK_", "TEMU_", "WALMART_", "SHEIN_", "M2E_", "SLACK_", "ANALYTICS_SUPABASE_",
                        "SUPABASE_WRITE_", "DEEPSEEK_", "GEMINI_", "OPENAI_", "GOOGLE_", "GITHUB_",
                        "RAILWAY_TOKEN", "RAILWAY_API")

_cargado = False


def _permitida(nombre: str) -> bool:
    return nombre in _LISTA_BLANCA or nombre.startswith(_PREFIJOS_BLANCOS)


def _prohibida(nombre: str) -> bool:
    n = nombre.upper()
    return n in _PROHIBIDAS or n.startswith(_PREFIJOS_PROHIBIDOS)


def _sin_env_propio() -> None:
    """`config.py` lee por su cuenta `<raíz del worktree>/.env`, `.env.amazon` y
    `env.staging` (pydantic-settings), sin pasar por la lista blanca. Si alguno
    aparece en el worktree del laboratorio (o en /app de la imagen), se falla
    CERRADO: esas credenciales entrarían enteras a `settings`."""
    raiz = SANDBOX_BACKEND.parent
    presentes = [n for n in (".env", ".env.amazon", "env.staging") if (raiz / n).exists()]
    if presentes:
        raise RuntimeError(
            f"laboratorio: {presentes} en {raiz}: config.py los leería completos (sin lista blanca). "
            "El laboratorio no lleva .env propio; quítalos.")


def datos_dir() -> Path:
    """Carpeta de datos del laboratorio (volumen en Railway, carpeta local en la laptop)."""
    return Path(os.environ.get("LAB_DATOS_DIR") or (SANDBOX_BACKEND / "sandbox_precios" / "datos"))


# Compatibilidad con los scripts de medición que ya usaban `_entorno.DATOS`.
DATOS = datos_dir()


def cargar() -> None:
    """Idempotente. Debe llamarse ANTES de importar `config` o cualquier `services.*`."""
    global _cargado
    if _cargado:
        return
    _sin_env_propio()
    if not EN_RAILWAY:
        from dotenv import dotenv_values

        ruta = REPO_PRINCIPAL / ".env"
        if ruta.exists():
            for k, v in dotenv_values(ruta).items():
                if v is not None and _permitida(k) and not _prohibida(k):
                    os.environ.setdefault(k, v)
    quitadas = sorted(k for k in list(os.environ) if _prohibida(k) and k not in _FORZADAS)
    for k in quitadas:
        os.environ.pop(k, None)
    if quitadas:
        # Solo los NOMBRES (nunca valores): en Railway sobra una referencia.
        print(f"laboratorio: variables fuera del proceso (no se usan aquí): {', '.join(quitadas)}",
              file=sys.stderr)
    os.environ.update(_FORZADAS)
    if str(SANDBOX_BACKEND) not in sys.path:
        sys.path.insert(0, str(SANDBOX_BACKEND))
    datos_dir().mkdir(parents=True, exist_ok=True)

    from sandbox_precios import candados

    # Falla CERRADO: si un candado no se puede instalar, la excepción sale y el
    # entorno NO queda marcado como cargado (un segundo `cargar()` lo reintenta
    # en vez de seguir sin candados).
    candados.instalar()
    _cargado = True
