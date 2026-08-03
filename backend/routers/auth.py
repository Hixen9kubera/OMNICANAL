"""
auth.py — Quién soy, según el backend.

ANTES ESTO MENTÍA. La versión anterior devolvía siempre
`{"autenticado": true, "usuario": "kubera", "rol": "admin"}` sin verificar nada
— un maniquí puesto "para después" que ningún endpoint usaba. Es justo el tipo
de placeholder que hace creer que hay autenticación cuando no la hay, y por eso
el cuestionario de Temu (III.1) se contestó mal.

Ahora refleja la identidad REAL que resolvió `core/middleware.py`: Bearer de
Supabase Auth para personas, `X-API-Key` para máquinas, y el rol sale de
`core.usuarios` en la BD kubera.

El frontend lo usa para saber si hay sesión y esconder los botones que el rol no
alcanza. Esconder botones es cosmética — **la autoridad vive en el backend**
(`core/rbac.py`), que es quien devuelve 403.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from config import settings
from core import rbac

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
def me(request: Request):
    """Identidad de quien llama. Nunca inventa: si no hay sesión, lo dice."""
    quien = getattr(request.state, "identidad", None)
    if quien is None or not quien.autenticado:
        return {
            "autenticado": False,
            "usuario": None,
            "rol": None,
            "auth_activa": bool(settings.api_key) and settings.auth_enforced,
            "rbac_activo": settings.rbac_enforced,
        }
    return {
        "autenticado": True,
        "usuario": quien.actor,
        "tipo": quien.tipo,
        "rol": quien.rol,
        "id": quien.id or None,
        "auth_activa": settings.auth_enforced,
        "rbac_activo": settings.rbac_enforced,
        # Para que el panel sepa qué esconder sin replicar la tabla de reglas.
        "puede": {
            "publicar": rbac.permite(quien.rol, "POST", "/api/publicar/confirmar"),
            "precios_masivos": rbac.permite(quien.rol, "POST", "/api/crear/costos/bulk"),
            "sync_masivo": rbac.permite(quien.rol, "POST", "/api/sync/woo"),
            "ver_costos": rbac.permite(quien.rol, "GET", "/api/crear/costos"),
            "editar_contenido": rbac.permite(quien.rol, "POST", "/api/productos/x/contenido"),
            "ver_auditoria": rbac.permite(quien.rol, "GET", "/api/auditoria"),
        },
    }
