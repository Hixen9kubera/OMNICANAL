"""
identidad.py — Quién está llamando a la API, y con qué rol.

DOS FORMAS DE IDENTIFICARSE
---------------------------
1. **Persona** — `Authorization: Bearer <token de Supabase Auth>`.
   El panel hace login contra Supabase Auth (proyecto `tukwcvsi…`, el mismo de
   la BD kubera) y manda ese token en cada llamada.

2. **Máquina** — `X-API-Key: <llave>`.
   Para crons y scripts, que no pueden pasar por una pantalla de login.

Sin ninguna de las dos, la identidad es `anonimo` y el middleware decide qué
hacer con ella según `AUTH_ENFORCED`.

POR QUÉ ASÍ, Y NO CON UN LOGIN PROPIO
-------------------------------------
`core.usuarios` ya venía diseñada para esto por el equipo de la migración:

    id      uuid  →  FK a auth.users(id) ON DELETE CASCADE
    rol     text  →  CHECK (rol IN ('admin','operador','lectura'))
    activo  bool

O sea: **Supabase Auth guarda la contraseña y las sesiones; `core.usuarios`
guarda el perfil y el ROL.** No hay columna de contraseña porque nunca debió
haberla. Construir un login propio habría sido reimplementar recuperación de
cuenta, expiración y bloqueo por fuerza bruta, con la ventaja de nada.

El `ON DELETE CASCADE` es además la respuesta a la pregunta III.3 del
cuestionario: al borrar el usuario, su perfil y su rol se van con él.

CÓMO SE VERIFICA EL TOKEN
-------------------------
Se le pregunta a Supabase (`GET /auth/v1/user`) en vez de validar la firma en
casa. Es más lento en la primera llamada pero no exige guardar el secreto JWT
—una credencial menos que custodiar y que rotar— y respeta al instante los
cierres de sesión. El costo se paga UNA vez: el resultado se cachea en memoria
por `AUTH_CACHE_SEG` (default 300 s), así que el token de una persona se
verifica contra Supabase como mucho cada 5 minutos.

NUNCA REVIENTA
--------------
Si Supabase no responde, `resolver()` devuelve `anonimo` en vez de lanzar. La
decisión de bloquear o no es del middleware, y así una caída de Supabase no
puede tumbar el panel entero — solo degrada a "no identificado".
"""
from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass

import httpx

from config import settings

log = logging.getLogger("omnicanal.identidad")

ANONIMO = "anonimo"
ROLES_VALIDOS = ("admin", "operador", "lectura")

# token -> (identidad, momento en que se cacheó)
_cache: dict[str, tuple["Identidad", float]] = {}


@dataclass(frozen=True)
class Identidad:
    """Quién llama. `actor` es lo que va a la bitácora de auditoría."""
    actor: str                    # correo, etiqueta de la llave, o "anonimo"
    tipo: str                     # "persona" | "maquina" | "anonimo"
    rol: str                      # admin | operador | lectura | ""
    id: str = ""                  # uuid de auth.users, solo para personas

    @property
    def autenticado(self) -> bool:
        return self.tipo != "anonimo"


_ANON = Identidad(actor=ANONIMO, tipo="anonimo", rol="")


def _cache_seg() -> int:
    return int(getattr(settings, "auth_cache_seg", 300) or 300)


def _limpiar_cache(ahora: float) -> None:
    """El caché es chico (una entrada por sesión viva); se poda al vuelo."""
    if len(_cache) < 200:
        return
    vencidos = [t for t, (_, ts) in _cache.items() if ahora - ts > _cache_seg()]
    for t in vencidos:
        _cache.pop(t, None)


def _por_api_key(recibida: str) -> Identidad | None:
    """Llave de máquina. Comparación en tiempo constante."""
    esperada = settings.api_key
    if not esperada or not recibida:
        return None
    if secrets.compare_digest(recibida, esperada):
        # Una sola llave por ahora. Cuando haya varias, aquí se busca en la
        # tabla y el actor deja de ser genérico.
        return Identidad(actor="servicio", tipo="maquina", rol="admin")
    return None


def _perfil_en_kubera(uid: str, correo: str) -> tuple[str, bool]:
    """
    Lee rol y estado desde core.usuarios. Devuelve (rol, activo).

    Si la tabla no responde o el usuario no está dado de alta, devuelve
    ("lectura", True): el rol MÁS BAJO. Un fallo de base nunca debe regalar
    permisos de admin.
    """
    try:
        from services import supabase_db as sdb
        if not sdb.disponible():
            return "lectura", True
        fila = sdb.fetch_one(
            "SELECT rol, activo FROM core.usuarios WHERE id = %s", (uid,))
        if not fila:
            log.warning("Usuario %s autenticado pero sin fila en core.usuarios; "
                        "se le da el rol mínimo.", correo or uid)
            return "lectura", True
        rol = str(fila.get("rol") or "lectura")
        return (rol if rol in ROLES_VALIDOS else "lectura"), bool(fila.get("activo"))
    except Exception:  # noqa: BLE001 — nunca romper por la base
        log.exception("No se pudo leer core.usuarios; se asume rol mínimo.")
        return "lectura", True


async def _por_token(token: str) -> Identidad | None:
    """Valida el token contra Supabase Auth y resuelve el rol."""
    ahora = time.time()
    guardada = _cache.get(token)
    if guardada and ahora - guardada[1] < _cache_seg():
        return guardada[0]

    url = (settings.supabase_url or "").rstrip("/")
    anon = settings.supabase_anon_key
    if not url or not anon:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as cx:
            r = await cx.get(f"{url}/auth/v1/user", headers={
                "Authorization": f"Bearer {token}", "apikey": anon})
        if r.status_code != 200:
            return None
        d = r.json()
        uid, correo = d.get("id") or "", d.get("email") or ""
        if not uid:
            return None
        rol, activo = _perfil_en_kubera(uid, correo)
        if not activo:
            log.warning("Usuario %s está marcado inactivo en core.usuarios.", correo)
            return None
        ident = Identidad(actor=correo or uid, tipo="persona", rol=rol, id=uid)
        _limpiar_cache(ahora)
        _cache[token] = (ident, ahora)
        return ident
    except Exception:  # noqa: BLE001 — Supabase caído no tumba el panel
        log.warning("No se pudo verificar el token contra Supabase Auth.")
        return None


async def resolver(request) -> Identidad:
    """Identidad de esta petición. Nunca lanza excepción."""
    try:
        llave = request.headers.get("x-api-key") or ""
        if llave:
            ident = _por_api_key(llave)
            if ident:
                return ident
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            ident = await _por_token(auth[7:].strip())
            if ident:
                return ident
    except Exception:  # noqa: BLE001
        log.exception("Fallo resolviendo la identidad; se trata como anónimo.")
    return _ANON


def olvidar(token: str) -> None:
    """Saca un token del caché (al cerrar sesión o revocar un usuario)."""
    _cache.pop(token, None)
