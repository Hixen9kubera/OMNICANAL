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

TAMBIÉN PASAN LAS PERSONAS (arreglado el 4-ago-2026, antes de encender)
----------------------------------------------------------------------
Esta dependencia nació cuando la única credencial era `X-API-Key`, y por eso
solo miraba ese header. Con el login del panel eso se volvió un agujero de
usabilidad: un ADMIN con sesión válida recibiría 401 en los 9 endpoints que la
usan —entre ellos `POST /api/migracion/errores/resolver`, que es un BOTÓN de la
página /migracion— aunque la puerta principal ya lo dejó entrar.

Ahora pasa quien cumpla UNA de las dos:
  · manda la `X-API-Key` correcta (máquinas: crons y scripts), o
  · trae sesión válida Y su rol alcanza según `core/rbac.py`.

Se consulta `rbac.permite` en vez de confiar en `RBAC_ENFORCED`: estos 9
endpoints ya estaban protegidos antes de este rollout y no pueden quedar MÁS
flojos durante la ventana en que el RBAC todavía está en observación.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import Header, HTTPException, Request

from config import settings
from core import rbac

log = logging.getLogger("omnicanal.auth")


async def requiere_api_key(request: Request,
                           x_api_key: str | None = Header(default=None)) -> None:
    """Dependencia FastAPI: exige (u observa, según AUTH_ENFORCED) la credencial."""
    if not settings.api_key:
        return  # sin key configurada: comportamiento actual (abierto)
    if x_api_key and secrets.compare_digest(x_api_key, settings.api_key):
        return  # key correcta (comparación en tiempo constante)

    # Persona con sesión: el middleware ya resolvió quién es y con qué rol.
    quien = getattr(request.state, "identidad", None)
    if (quien is not None and getattr(quien, "autenticado", False)
            and rbac.permite(quien.rol, request.method, request.url.path)):
        return

    if settings.auth_enforced:
        raise HTTPException(status_code=401, detail="X-API-Key ausente o inválida.")
    log.warning(
        "AUTH en observación: petición sin credencial válida — se permite porque "
        "AUTH_ENFORCED=false. Con enforcement activo habría sido 401."
    )
