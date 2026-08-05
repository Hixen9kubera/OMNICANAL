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

TODO EL EQUIPO CONECTADO AL MISMO TIEMPO
----------------------------------------
Con once personas entrando a la vez aparecen dos problemas que NO se ven con un
solo usuario. Los dos están resueltos aquí:

1. **La estampida al abrir el panel.** Una sola pantalla dispara ~8 llamadas a
   la API en paralelo. Con el caché frío, las 8 verificarían el MISMO token
   contra Supabase y consultarían 8 veces `core.usuarios`. Once personas
   entrando juntas = ~88 verificaciones simultáneas contra un pool de 6
   conexiones (`services/supabase_db.py`), que además es `blocking=True`: las
   que no alcanzan conexión se quedan esperando.
   → Hay un **candado por token**: el primero verifica, los demás esperan un
   instante y leen el caché ya tibio. Es la misma cura que el candado por orden
   contra las ráfagas de webhooks de ML.

2. **La consulta a la base bloqueaba el servidor entero.** `_perfil_en_kubera`
   usa psycopg2, que es SÍNCRONO, y se llamaba directo desde una corrutina. En
   una app async eso congela el event loop: mientras esa consulta corre, NADIE
   más es atendido — ni el webhook de ML.
   → Ahora corre en un hilo (`asyncio.to_thread`).

Un token inválido también se cachea, pero solo unos segundos: sin eso, una
sesión vencida golpearía Supabase en CADA petición del panel.

NUNCA REVIENTA
--------------
Si Supabase no responde, `resolver()` devuelve `anonimo` en vez de lanzar. La
decisión de bloquear o no es del middleware, y así una caída de Supabase no
puede tumbar el panel entero — solo degrada a "no identificado".
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass

import httpx

from config import settings

log = logging.getLogger("omnicanal.identidad")

ANONIMO = "anonimo"
ROLES_VALIDOS = ("admin", "operador", "lectura")

# Un token que Supabase rechazó se recuerda solo unos segundos: lo justo para
# que una sesión vencida no golpee Supabase en cada llamada del panel, y lo
# bastante poco para no retrasar a nadie que vuelva a entrar (el login nuevo
# trae OTRO token, así que ni siquiera pasa por esta entrada).
NEGATIVO_SEG = 15

# token -> (identidad | None, momento en que se cacheó). None = token inválido.
_cache: dict[str, tuple["Identidad | None", float]] = {}

# Un candado POR TOKEN contra la estampida de la carga inicial (ver cabecera).
_candados: dict[str, asyncio.Lock] = {}
_candado_maestro = asyncio.Lock()

# Centinela: distingue "no está en caché" de "está cacheado como inválido".
_SIN_DATO = object()


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


def _vigencia(ident: "Identidad | None") -> int:
    """Cuánto vale la pena recordar este resultado."""
    return _cache_seg() if ident is not None else min(NEGATIVO_SEG, _cache_seg())


def _leer_cache(token: str):
    """La identidad cacheada, `None` si el token es inválido, `_SIN_DATO` si no hay."""
    guardada = _cache.get(token)
    if guardada is None:
        return _SIN_DATO
    ident, ts = guardada
    if time.time() - ts >= _vigencia(ident):
        return _SIN_DATO
    return ident


def _limpiar_cache(ahora: float) -> None:
    """
    El caché es chico —una entrada por sesión viva, con once personas son once—
    así que se poda al vuelo y solo cuando crece de más.
    """
    if len(_cache) < 200:
        return
    for t, (ident, ts) in list(_cache.items()):
        if ahora - ts >= _vigencia(ident):
            _cache.pop(t, None)
    # Los candados de tokens que ya no están en caché sobran. Nunca se toca uno
    # TOMADO: alguien lo está usando ahora mismo para verificar.
    for t, candado in list(_candados.items()):
        if t not in _cache and not candado.locked():
            _candados.pop(t, None)


async def _candado_de(token: str) -> asyncio.Lock:
    """El candado de este token, creándolo si es el primero en pedirlo."""
    async with _candado_maestro:
        candado = _candados.get(token)
        if candado is None:
            candado = asyncio.Lock()
            _candados[token] = candado
        return candado


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


def _perfil_en_kubera(uid: str, correo: str) -> tuple[str, bool, str]:
    """
    Lee rol y estado desde core.usuarios. Devuelve (rol, activo, origen).

    `origen` distingue tres situaciones que NO se pueden tratar igual:

      "fila"      → la persona está dada de alta; manda su rol.
      "sin_fila"  → la base contestó bien y esta persona NO está en la lista.
                    Se RECHAZA (ver `_verificar`): el acceso es exclusivo de
                    quien esté dado de alta, aunque Google lo haya validado.
      "sin_base"  → no se pudo consultar. Aquí NO se rechaza: una caída de
                    Supabase dejaría al equipo entero fuera del panel. Se cae al
                    rol MÍNIMO ("lectura"), que degrada pero no bloquea.

    Confundir "sin_fila" con "sin_base" es el error caro: por un lado dejaría
    entrar a cualquiera con correo de la empresa, por el otro convertiría un
    hipo de la base en una caída total.
    """
    try:
        from services import supabase_db as sdb
        if not sdb.disponible():
            return "lectura", True, "sin_base"
        fila = sdb.fetch_one(
            "SELECT rol, activo FROM core.usuarios WHERE id = %s", (uid,))
        if not fila:
            return "lectura", True, "sin_fila"
        rol = str(fila.get("rol") or "lectura")
        return (rol if rol in ROLES_VALIDOS else "lectura"), bool(fila.get("activo")), "fila"
    except Exception:  # noqa: BLE001 — nunca romper por la base
        log.exception("No se pudo leer core.usuarios; se asume rol mínimo.")
        return "lectura", True, "sin_base"


async def _verificar(token: str) -> Identidad | None:
    """Le pregunta a Supabase quién es este token. Sin caché ni candados."""
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
        # psycopg2 es SÍNCRONO: llamarlo directo desde aquí congelaría el event
        # loop —y con él a todos los demás usuarios y al webhook de ML— mientras
        # la consulta corre o espera una conexión del pool (que es de 6).
        rol, activo, origen = await asyncio.to_thread(_perfil_en_kubera, uid, correo)

        # EL PANEL ES EXCLUSIVO DE QUIEN ESTÉ DADO DE ALTA. Google (o Supabase)
        # solo prueba QUIÉN es; que además le toque entrar lo decide
        # `core.usuarios`. Sin esto, cualquier cuenta del dominio —un empleado
        # nuevo, una cuenta de servicio— entraría con rol de lectura.
        if origen == "sin_fila":
            log.warning("ACCESO NEGADO: %s se autenticó bien pero no está dado "
                        "de alta en core.usuarios.", correo or uid)
            return None
        if not activo:
            log.warning("Usuario %s está marcado inactivo en core.usuarios.", correo)
            return None
        return Identidad(actor=correo or uid, tipo="persona", rol=rol, id=uid)
    except Exception:  # noqa: BLE001 — Supabase caído no tumba el panel
        log.warning("No se pudo verificar el token contra Supabase Auth.")
        return None


async def _por_token(token: str) -> Identidad | None:
    """
    Valida el token y resuelve el rol, verificando UNA sola vez por token aunque
    lleguen veinte peticiones a la vez (ver "TODO EL EQUIPO CONECTADO").
    """
    cacheada = _leer_cache(token)
    if cacheada is not _SIN_DATO:
        return cacheada  # type: ignore[return-value]

    async with await _candado_de(token):
        # Mientras se esperaba el candado, otro ya pudo haber verificado.
        cacheada = _leer_cache(token)
        if cacheada is not _SIN_DATO:
            return cacheada  # type: ignore[return-value]

        ident = await _verificar(token)
        ahora = time.time()
        _limpiar_cache(ahora)
        _cache[token] = (ident, ahora)
        return ident


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
    candado = _candados.get(token)
    if candado is not None and not candado.locked():
        _candados.pop(token, None)
