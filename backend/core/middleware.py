"""
middleware.py — Puerta de identidad de TODA la API.

POR QUÉ EXISTE
--------------
Hasta hoy el backend respondía HTTP 200 a cualquiera que conociera su URL:
`/api/productos`, `/api/fanout/estado` y `/api/migracion/errores` entregaban
datos sin pedir credencial. Eso es lo que el cuestionario de seguridad de Temu
pregunta en III.1, y era la única respuesta falsa del formulario.

`core/seguridad.py::requiere_api_key` ya existía, pero como dependencia SUELTA:
solo protegía 8 endpoints de los 84. Este middleware lo aplica a TODOS de una
vez, sin tocar router por router.

LAS TRES REGLAS QUE NO SE NEGOCIAN
----------------------------------
1. **Las rutas abiertas se evalúan ANTES que el enforcement.** No existe orden
   de ejecución en que `/api/health` o el webhook de ML puedan devolver 401.

   No es paranoia. `railway.json` declara `healthcheckPath=/api/health` con
   `restartPolicyType=ON_FAILURE`: si esa ruta contesta 401, Railway da el
   deploy por muerto y entra en BUCLE DE REINICIO. Eso no tumba un endpoint,
   tumba el backend entero — webhook, scheduler (sync de 15 min, vigilante de
   Odoo, fan-out, sondeos de Amazon y M2E) y el panel. Un error en una lista de
   strings puede apagar la operación completa.

   Y el webhook de ML no puede mandar nuestro token: si le devolvemos 401, ML
   reintenta 1 hora y después DESHABILITA el topic. A partir de ahí se dejan de
   capturar ventas reales, sin error visible — simplemente dejan de entrar
   pedidos.

2. **El middleware FALLA ABIERTO.** Si algo revienta aquí dentro, la petición
   pasa. Un bug en la autenticación no puede convertirse en una caída total.
   Se registra en logs para que no pase desapercibido.

3. **OPTIONS siempre pasa.** Es el preflight de CORS: lo manda el navegador sin
   credenciales y antes de la petición real. Bloquearlo rompe el panel entero
   con un error de CORS que no dice nada útil.

ROLLOUT EN DOS TIEMPOS
----------------------
  AUTH_ENFORCED=false (default) → OBSERVACIÓN: nadie se bloquea, solo se
      registra quién habría recibido 401. Es el censo que hace segura la fase
      siguiente.
  AUTH_ENFORCED=true            → se aplica de verdad.

Revertir es cambiar la variable en Railway: 2-4 min, dentro de la ventana de
reintentos de ML (1 h), así que no se pierde ni una venta.

ESCOTILLA SIN CÓDIGO
--------------------
`AUTH_RUTAS_ABIERTAS` (CSV) agrega rutas a la lista blanca sin commit, por si
en el censo aparece un consumidor legítimo que nadie documentó.
"""
from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from config import settings
from core import actor as core_actor
from core import identidad as core_identidad
from core import rate_limit
from core import rbac

log = logging.getLogger("omnicanal.auth")

# Rutas que NUNCA piden credencial. Coinciden por igualdad exacta, no por
# prefijo: `/api/webhooks/ml/log` NO hereda la apertura de `/api/webhooks/ml`
# (ese sí expone datos y debe quedar cerrado).
RUTAS_ABIERTAS: frozenset[str] = frozenset({
    "/api/health",          # healthcheck de Railway — ver regla 1
    "/api/health/detalle",
    "/api/webhooks/ml",     # ML no puede mandar nuestro token — ver regla 1
    "/api/tiktok/callback", # TikTok tampoco: es la vuelta del OAuth del seller.
                            # Lo protege el `state` firmado, no la credencial.
    "/api/webhooks/tiktok", # TikTok no puede mandar nuestro token — ver regla 1.
                            # Lo protege la firma HMAC del propio TikTok.
                            # OJO: `/api/webhooks/tiktok/log` NO hereda esta
                            # apertura (coincidencia exacta) y sigue cerrado —
                            # ese sí expone datos personales del comprador.
    "/api/webhooks/temu",   # Temu tampoco. Y aquí la apertura es DOBLEMENTE
                            # necesaria: la consola del Partner Platform VALIDA
                            # la URL al guardarla, así que un 401 no solo
                            # perdería eventos — impediría dar de alta la
                            # suscripción ("The Push website is invalid").
                            # `/api/webhooks/temu/log` sigue cerrado (exacta):
                            # los eventos traen datos del comprador.
    "/api/webhooks/woo",    # WooCommerce tampoco manda token: lo protege su
                            # firma HMAC (X-WC-Webhook-Signature) y sin firma
                            # válida el endpoint NO escribe. Faltó en v0.92 —
                            # sin esta línea, Woo recibe 401 en cada entrega y
                            # a las 5 fallas deshabilita el webhook solo.
                            # `/api/webhooks/woo/log` sigue cerrado (exacta).
    "/",                    # raíz: solo versión y lista de canales
})

# Prefijos abiertos (con cuidado): documentación y estáticos de FastAPI.
_PREFIJOS_ABIERTOS: tuple[str, ...] = ("/docs", "/redoc", "/openapi.json")


def _normalizar(ruta: str) -> str:
    """`/api/health/` y `/api/health` son la misma ruta."""
    if len(ruta) > 1 and ruta.endswith("/"):
        return ruta[:-1]
    return ruta


def _extra_abiertas() -> frozenset[str]:
    crudo = (getattr(settings, "auth_rutas_abiertas", "") or "").strip()
    if not crudo:
        return frozenset()
    return frozenset(_normalizar(r.strip()) for r in crudo.split(",") if r.strip())


def es_ruta_abierta(ruta: str) -> bool:
    """True si la ruta jamás debe pedir credencial."""
    r = _normalizar(ruta)
    return (r in RUTAS_ABIERTAS
            or r in _extra_abiertas()
            or r.startswith(_PREFIJOS_ABIERTOS))


async def _es_vendedor_ml(request: Request) -> bool:
    """
    ¿Este POST trae un `user_id` de nuestros vendedores de ML?

    Lee el cuerpo y lo RE-INYECTA para que el endpoint pueda volver a leerlo:
    en Starlette el cuerpo es un stream de un solo uso, así que sin esto el
    webhook recibiría un cuerpo vacío. Es el patrón estándar; se prueba en vivo
    que `recibir_ml` sigue viendo su payload.

    Falla hacia el carril GENERAL (más estricto): si el cuerpo no es un JSON
    legible o no trae user_id, se trata como desconocido. Nunca lanza.
    """
    if request.method != "POST":
        return False
    try:
        cuerpo = await request.body()
        # Re-inyecta el cuerpo consumido para el endpoint de abajo.
        async def _receive() -> dict:
            return {"type": "http.request", "body": cuerpo, "more_body": False}
        request._receive = _receive  # noqa: SLF001 — patrón conocido de Starlette

        if not cuerpo:
            return False
        import json
        uid = str(json.loads(cuerpo).get("user_id", "") or "")
        return uid in settings.webhook_ml_vendedores_set
    except Exception:  # noqa: BLE001 — cuerpo ilegible → carril general
        return False


async def identidad(request: Request, call_next):
    """
    Puerta única de la API. El orden de las guardas es deliberado y frágil:
    NO reordenar sin leer las tres reglas del encabezado.
    """
    try:
        # --- Guarda 1: preflight de CORS. Sin esto el panel muere. ---
        if request.method == "OPTIONS":
            return await call_next(request)

        # --- Guarda 0.5: freno de ráfagas para los WEBHOOKS. ---
        # Va aquí, ANTES de la lista blanca, porque el webhook vive en esa lista
        # y hay que contarlo antes de dejarlo pasar. SOLO los webhooks: el
        # healthcheck de Railway (`/api/health`) golpea sin parar y NO debe
        # frenarse nunca — un 429 ahí mete al backend en bucle de reinicio
        # (regla 1 del encabezado). Por eso el filtro es el prefijo exacto.
        #
        # DOS CARRILES: si el POST trae un `user_id` de nuestros vendedores, va
        # por el carril de ML (1200/min, inalcanzable); el resto, por el general
        # (150/min). Ver core/rate_limit.py. Apagable con WEBHOOK_RATE_LIMIT=false.
        if (settings.webhook_rate_limit
                and request.url.path.startswith("/api/webhooks/")):
            es_ml = await _es_vendedor_ml(request)
            ip = rate_limit.ip_de(request)
            if not rate_limit.permite(ip, es_ml=es_ml):
                carril = "ML" if es_ml else "general"
                log.warning("RATE-LIMIT 429 [%s]: %s floodeó %s", carril, ip,
                            request.url.path)
                return JSONResponse(status_code=429, content={
                    "detail": "Demasiadas peticiones; intenta más tarde."},
                    headers={"Retry-After": "10"})

        # --- Guarda 2: rutas abiertas. ANTES de mirar auth_enforced. ---
        if es_ruta_abierta(request.url.path):
            return await call_next(request)

        metodo, ruta = request.method, request.url.path
        quien = await core_identidad.resolver(request)
        # Se deja en el request para que los routers y la futura bitácora sepan
        # quién llamó sin volver a resolverlo.
        request.state.identidad = quien
        # Y se deja también en el contexto, que es como llega hasta el cursor:
        # `supabase_db.get_cursor` lo recoge y lo pone donde los triggers de
        # historial lo leen. La "futura bitácora" de la línea de arriba es esto.
        # Ojo: las rutas abiertas salen por la guarda 2 y nunca llegan aquí, así
        # que webhooks y healthcheck siguen escribiendo sin nombre — igual que
        # hasta hoy, y a propósito: ML no manda credencial.
        core_actor.fijar(quien.actor)

        # Sin API_KEY configurada el sistema queda abierto, como hasta hoy. Es
        # el estado por defecto para que un despliegue sin variables no rompa.
        if not settings.api_key and not quien.autenticado:
            return await call_next(request)

        # --- Guarda 3: ¿se identificó? ---
        if not quien.autenticado:
            if settings.auth_enforced:
                log.warning("AUTH 401: %s %s sin credencial válida", metodo, ruta)
                return JSONResponse(status_code=401, content={
                    "detail": "Falta la credencial. Inicia sesión o manda X-API-Key."})
            # Observación: pasa, pero deja rastro. Este log es el censo que
            # decide si ya se puede encender el enforcement.
            log.warning("AUTH observación: %s %s habría sido 401 "
                        "(AUTH_ENFORCED=false)", metodo, ruta)
            return await call_next(request)

        # --- Guarda 4: ¿su rol alcanza? (Temu III.2) ---
        if not rbac.permite(quien.rol, metodo, ruta):
            requerido = rbac.rol_requerido(metodo, ruta)
            if settings.rbac_enforced:
                log.warning("RBAC 403: %s (%s) intentó %s %s — se requiere %s",
                            quien.actor, quien.rol, metodo, ruta, requerido)
                return JSONResponse(status_code=403, content={
                    "detail": f"Tu rol ({quien.rol}) no alcanza para esta "
                              f"operación; se requiere {requerido}."})
            log.warning("RBAC observación: %s (%s) habría sido 403 en %s %s "
                        "(se requiere %s)", quien.actor, quien.rol,
                        metodo, ruta, requerido)

        return await call_next(request)

    except Exception:  # noqa: BLE001 — regla 2: falla abierto, nunca tumba el sitio
        log.exception("El middleware de identidad falló; la petición se deja pasar.")
        return await call_next(request)
