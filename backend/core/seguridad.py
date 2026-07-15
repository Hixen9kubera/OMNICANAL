"""
seguridad.py — Auth mínima por API key (T1.4 del plan de migración).

Hoy el backend no tiene autenticación; este módulo introduce la primera capa
con rollout gradual en dos pasos, controlado por config:

  1. API_KEY definida + AUTH_ENFORCED=false  → modo OBSERVACIÓN: las peticiones
     sin key válida se registran en logs pero SE PERMITEN (mide impacto sin
     romper a nadie).
  2. AUTH_ENFORCED=true → las peticiones sin `X-API-Key` correcta reciben 401.

Sin API_KEY configurada, todo queda abierto como siempre (default = hoy).
Se aplica como dependencia SOLO a endpoints de escritura/operación — los de
lectura que usa el frontend no se tocan en el piloto.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import Header, HTTPException

from config import settings

log = logging.getLogger("omnicanal.auth")


async def requiere_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Dependencia FastAPI: exige (u observa, según AUTH_ENFORCED) la API key."""
    if not settings.api_key:
        return  # sin key configurada: comportamiento actual (abierto)
    if x_api_key and secrets.compare_digest(x_api_key, settings.api_key):
        return  # key correcta (comparación en tiempo constante)
    if settings.auth_enforced:
        raise HTTPException(status_code=401, detail="X-API-Key ausente o inválida.")
    log.warning(
        "AUTH en observación: petición sin X-API-Key válida — se permite porque "
        "AUTH_ENFORCED=false. Con enforcement activo habría sido 401."
    )
