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
    # Los packing lists se leen del bucket de kubera cuando se puede.
    "PACKING_LEER_STORAGE": "true",
    "PYTHONIOENCODING": "utf-8",
}

_cargado = False


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
    if not EN_RAILWAY:
        from dotenv import dotenv_values

        for nombre in (".env", ".env.amazon"):
            ruta = REPO_PRINCIPAL / nombre
            if ruta.exists():
                for k, v in dotenv_values(ruta).items():
                    if v is not None:
                        os.environ.setdefault(k, v)
    os.environ.update(_FORZADAS)
    if str(SANDBOX_BACKEND) not in sys.path:
        sys.path.insert(0, str(SANDBOX_BACKEND))
    datos_dir().mkdir(parents=True, exist_ok=True)
    _cargado = True

    from sandbox_precios import candados

    candados.instalar()
