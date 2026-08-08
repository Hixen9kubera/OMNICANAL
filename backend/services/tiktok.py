"""
tiktok.py — OAuth y cliente de TikTok Shop Open API (Partner Center).

CONTEXTO (2026-08-08). TikTok Shop es el único canal del omnicanal que hoy
depende por completo de un panel externo: la conexión de M2E Cloud está
`is_valid=false` desde julio y nunca se re-autorizó. Este módulo abre la vía
propia: una app de ISV en el Partner Center (`partner.tiktokshop.com`), con
Enable API encendido y Redirect URL apuntando a NUESTRO backend.

EL FLUJO, TAL COMO LO DEFINE TIKTOK
-----------------------------------
1. El seller entra a la URL de autorización del servicio (services.tiktokshop.com)
   y aprueba la app.
2. TikTok lo redirige al Redirect URL registrado con `?code=<auth_code>&state=`.
   Ese `code` es de UN SOLO USO y vive pocos minutos.
3. Cambiamos el code por `access_token` + `refresh_token` contra el servicio de
   auth. El access_token dura ~7 días; el refresh_token ~1 año.
4. A partir de ahí, cada llamada a la Open API va firmada con el access_token
   y el `shop_cipher` de la tienda.

POR QUÉ EL CALLBACK ES PÚBLICO
------------------------------
TikTok no puede mandar nuestra `X-API-Key`, igual que ML con su webhook. Por eso
`/api/tiktok/callback` está en `RUTAS_ABIERTAS` del middleware. Lo que protege
el endpoint NO es la credencial sino el `state`: se emite firmado aquí, con TTL,
y se verifica al volver. Un `code` sin `state` válido se rechaza.

TOKENS
------
Se guardan en la tabla `tiktok_tokens` de MySQL, CIFRADOS con Fernet usando la
misma `DB_ENCRYPTION_KEY` que ya cifra `ml_tokens` — no se inventa un esquema
nuevo de secretos. Sin esa llave el módulo se niega a guardar (mejor no tener
token que tenerlo en claro).

NACE APAGADO
------------
`TIKTOK_ENABLED=false` por defecto (regla 3 de CLAUDE.md: encender un flujo que
habla con un marketplace vivo requiere el dale de Brandon). Con el interruptor
apagado los endpoints responden 503 y NO se llama a TikTok.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from config import settings
from services import db

log = logging.getLogger("omnicanal.tiktok")

# Endpoints oficiales de la Open API v2. Se dejan configurables porque TikTok
# mueve estos hosts entre regiones y no queremos un redeploy para eso.
AUTH_BASE = "https://auth.tiktok-shops.com"
API_BASE = "https://open-api.tiktokglobalshop.com"
AUTORIZAR_BASE = "https://services.tiktokshop.com/open/authorize"

_DDL = """
CREATE TABLE IF NOT EXISTS tiktok_tokens (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    seller_name   VARCHAR(120),
    open_id       VARCHAR(120),
    shop_id       VARCHAR(60),
    shop_cipher   VARCHAR(160),
    access_token  TEXT       NOT NULL,
    refresh_token TEXT,
    expira        DATETIME,
    refresh_expira DATETIME,
    updated_at    DATETIME   NOT NULL,
    UNIQUE KEY uq_shop (shop_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_tabla_lista = False


def _asegurar_tabla() -> None:
    global _tabla_lista
    if _tabla_lista:
        return
    db.execute(_DDL)
    _tabla_lista = True


# ── Cifrado (mismo mecanismo que ml_tokens) ───────────────────────────────────

def _fernet():
    key = settings.db_encryption_key
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode())
    except Exception as exc:  # noqa: BLE001
        log.warning("Fernet no disponible: %s", exc)
        return None


def _cifrar(valor: str) -> str:
    f = _fernet()
    if not f:
        raise RuntimeError(
            "DB_ENCRYPTION_KEY no configurada: me niego a guardar el token de "
            "TikTok en claro (mismo criterio que ml_tokens)."
        )
    return f.encrypt(valor.encode()).decode()


def _descifrar(valor: str | None) -> str | None:
    if not valor:
        return None
    f = _fernet()
    if f and valor.startswith("gAAAAA"):
        try:
            return f.decrypt(valor.encode()).decode()
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo desencriptar token de TikTok: %s", exc)
            return None
    return valor


# ── `state` firmado: lo que realmente protege el callback ─────────────────────

# 30 min, EXACTAMENTE lo que dura el `auth_code` de TikTok.
#
# Estaba en 900 (15 min) y era una bomba de tiempo: si el seller se tardaba
# entre 16 y 29 minutos en aprobar la pantalla, TikTok mandaba un code VÁLIDO y
# nuestro propio candado lo rechazaba. El síntoma habría sido "autoricé y no
# sirvió", sin nada raro del lado de TikTok. El 7-ago funcionó solo porque
# Brandon fue rápido.
_STATE_TTL = 1800


def _secreto_state() -> str:
    """Llave para firmar el state. Reusa un secreto que ya existe en el entorno."""
    return (settings.tiktok_app_secret or settings.api_key
            or settings.db_encryption_key or "omnicanal")


def emitir_state() -> str:
    """Genera un `state` opaco y firmado, con marca de tiempo."""
    cuerpo = json.dumps({"ts": int(time.time())}, separators=(",", ":"))
    b = base64.urlsafe_b64encode(cuerpo.encode()).decode().rstrip("=")
    firma = hmac.new(_secreto_state().encode(), b.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{b}.{firma}"


def verificar_state(state: str | None) -> bool:
    """True si el state lo emitimos nosotros y no ha caducado."""
    if not state or "." not in state:
        return False
    b, _, firma = state.partition(".")
    esperada = hmac.new(_secreto_state().encode(), b.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(firma, esperada):
        return False
    try:
        relleno = "=" * (-len(b) % 4)
        datos = json.loads(base64.urlsafe_b64decode(b + relleno).decode())
        return (int(time.time()) - int(datos.get("ts", 0))) <= _STATE_TTL
    except Exception:  # noqa: BLE001
        return False


# ── Autorización ──────────────────────────────────────────────────────────────

def url_autorizacion() -> str:
    """URL a la que se manda al seller para que apruebe la app."""
    if not settings.tiktok_service_id:
        raise RuntimeError("TIKTOK_SERVICE_ID no configurado (Partner Center → App & Service).")
    return f"{AUTORIZAR_BASE}?service_id={settings.tiktok_service_id}&state={emitir_state()}"


async def intercambiar_code(code: str) -> dict[str, Any]:
    """
    Cambia el `code` del callback por access_token + refresh_token.

    TikTok responde con `code: 0` cuando todo salió bien; cualquier otro valor es
    un error de negocio que llega con HTTP 200, así que hay que mirar el cuerpo,
    no el status.
    """
    if not (settings.tiktok_app_key and settings.tiktok_app_secret):
        raise RuntimeError("Faltan TIKTOK_APP_KEY / TIKTOK_APP_SECRET.")
    params = {
        "app_key": settings.tiktok_app_key,
        "app_secret": settings.tiktok_app_secret,
        "auth_code": code,
        "grant_type": "authorized_code",
    }
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.get(f"{AUTH_BASE}/api/v2/token/get", params=params)
    r.raise_for_status()
    cuerpo = r.json()
    if cuerpo.get("code") not in (0, "0"):
        raise RuntimeError(f"TikTok rechazó el code: {cuerpo.get('message') or cuerpo}")
    return cuerpo.get("data") or {}


async def refrescar(refresh_token: str) -> dict[str, Any]:
    """Renueva el access_token. TikTok NO rota el refresh_token en cada uso."""
    params = {
        "app_key": settings.tiktok_app_key,
        "app_secret": settings.tiktok_app_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.get(f"{AUTH_BASE}/api/v2/token/refresh", params=params)
    r.raise_for_status()
    cuerpo = r.json()
    if cuerpo.get("code") not in (0, "0"):
        raise RuntimeError(f"No se pudo refrescar el token: {cuerpo.get('message') or cuerpo}")
    return cuerpo.get("data") or {}


# ── Llamadas firmadas a la Open API ───────────────────────────────────────────
#
# TikTok NO se conforma con el token: firma cada petición. Sin esto no se puede
# llamar a ningún endpoint de negocio, y era lo que faltaba para que el canal
# sirviera de algo más que guardar un token.
#
# Verificado contra la API real el 7-ago: con esta firma la respuesta fue
# `36009033 IP not in allow list` — es decir, pasó la validación de firma y se
# frenó en la capa de red. Una firma mal armada devuelve error de firma, no ese.

def _firmar(ruta: str, params: dict[str, Any], cuerpo: str = "") -> str:
    """
    HMAC-SHA256 sobre la cadena canónica de TikTok.

    La receta: los query params MENOS `sign` y `access_token`, ordenados por
    nombre y concatenados clave+valor sin separadores; se antepone la RUTA, se
    anexa el cuerpo crudo, y todo se envuelve con el app_secret a ambos lados.
    """
    secreto = settings.tiktok_app_secret or ""
    partes = "".join(f"{k}{params[k]}" for k in sorted(params)
                     if k not in ("sign", "access_token"))
    envuelto = f"{secreto}{ruta}{partes}{cuerpo}{secreto}"
    return hmac.new(secreto.encode(), envuelto.encode(), hashlib.sha256).hexdigest()


async def llamar(ruta: str, token: str, params: dict[str, Any] | None = None,
                 cuerpo: dict[str, Any] | None = None,
                 metodo: str = "GET") -> dict[str, Any]:
    """
    Una llamada firmada. Devuelve el `data` de la respuesta.

    TikTok responde HTTP 200 aunque haya fallado: el veredicto está en `code`
    del cuerpo, no en el status. Confundirlos es el error clásico con esta API.

    OJO con `timestamp`: TikTok solo acepta una ventana de [ahora−5min, ahora+30s].
    Un reloj desfasado devuelve `36009004 Invalid timestamp` y parece otra cosa.
    """
    p: dict[str, Any] = dict(params or {})
    p["app_key"] = settings.tiktok_app_key
    p["timestamp"] = str(int(time.time()))
    body = json.dumps(cuerpo, separators=(",", ":")) if cuerpo else ""
    p["sign"] = _firmar(ruta, p, body)
    cab = {"x-tts-access-token": token, "Content-Type": "application/json"}
    async with httpx.AsyncClient(base_url=API_BASE, timeout=60) as cli:
        r = (await cli.get(ruta, params=p, headers=cab) if metodo == "GET"
             else await cli.post(ruta, params=p, headers=cab, content=body))
    try:
        j = r.json()
    except Exception:  # noqa: BLE001
        raise RuntimeError(f"TikTok devolvió algo que no es JSON (HTTP {r.status_code}): "
                           f"{r.text[:200]}")
    if j.get("code") not in (0, "0"):
        raise RuntimeError(f"TikTok {ruta} → code={j.get('code')} "
                           f"{j.get('message')} (request_id={j.get('request_id')})")
    return j.get("data") or {}


async def tiendas_autorizadas(token: str) -> list[dict[str, Any]]:
    """
    Las tiendas que autorizaron la app, CON su `cipher`.

    Este paso faltaba y por eso el canal quedaba inservible: el `shop_cipher` es
    query param OBLIGATORIO en Create Product, Update Inventory, Update Price y
    la Events API. El canje del token NO lo devuelve — hay que pedirlo aparte.

    Devuelve objetos con `{cipher, code, id, name, region, seller_type}`.
    """
    data = await llamar("/authorization/202309/shops", token)
    return data.get("shops") or []


# ── Persistencia ──────────────────────────────────────────────────────────────

def _a_datetime(epoch: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return None


def guardar(data: dict[str, Any],
            tiendas: list[dict[str, Any]] | None = None) -> int:
    """
    Persiste el token cifrado. Devuelve cuántas tiendas se guardaron.

    `tiendas` son las de `tiendas_autorizadas()`, con su `cipher`. Se pasan
    aparte a propósito: el canje del token no las trae, y sin el cipher el canal
    queda inservible aunque el token sea válido.

    Una autorización puede traer VARIAS tiendas; se guarda una fila por tienda
    para poder operarlas por separado.
    """
    _asegurar_tabla()
    access = data.get("access_token")
    if not access:
        raise RuntimeError("La respuesta de TikTok no trae access_token.")

    access_c = _cifrar(access)
    refresh_c = _cifrar(data["refresh_token"]) if data.get("refresh_token") else None
    expira = _a_datetime(data.get("access_token_expire_in"))
    refresh_expira = _a_datetime(data.get("refresh_token_expire_in"))
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    seller = data.get("seller_name") or ""
    open_id = data.get("open_id") or ""

    # Las tiendas vienen de `GET /authorization/202309/shops`, NO del canje del
    # token: esa respuesta no trae ni `granted_shops` ni `shops`, así que buscarlas
    # ahí caía SIEMPRE al respaldo y se guardaba el `open_id` como si fuera un
    # shop_id, con el `cipher` en NULL. Resultado: token válido y canal inservible,
    # porque el cipher es obligatorio en Create Product, Update Inventory,
    # Update Price y la Events API.
    #
    # Los nombres de campo son `cipher` e `id` — NO `shop_cipher` ni `shop_id`.
    if not tiendas:
        log.warning("TikTok: se guarda el token SIN tiendas resueltas. El canal "
                    "no podrá publicar hasta obtener el shop_cipher.")
        tiendas = [{"id": open_id or seller or "default", "cipher": None}]

    guardadas = 0
    for t in tiendas:
        db.execute(
            """
            INSERT INTO tiktok_tokens
                (seller_name, open_id, shop_id, shop_cipher, access_token,
                 refresh_token, expira, refresh_expira, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                seller_name=VALUES(seller_name), open_id=VALUES(open_id),
                shop_cipher=VALUES(shop_cipher), access_token=VALUES(access_token),
                refresh_token=VALUES(refresh_token), expira=VALUES(expira),
                refresh_expira=VALUES(refresh_expira), updated_at=VALUES(updated_at)
            """,
            (t.get("name") or seller, open_id,
             str(t.get("id") or t.get("shop_id") or "default"),
             t.get("cipher") or t.get("shop_cipher"),
             access_c, refresh_c, expira, refresh_expira, ahora),
        )
        guardadas += 1
    log.info("TikTok: token guardado para %s (%d tienda/s)", seller or open_id, guardadas)
    return guardadas


def estado() -> dict[str, Any]:
    """Diagnóstico sin exponer el token: ¿hay conexión viva y hasta cuándo?"""
    try:
        _asegurar_tabla()
        filas = db.fetch_all(
            "SELECT seller_name, shop_id, expira, refresh_expira, updated_at "
            "FROM tiktok_tokens ORDER BY updated_at DESC"
        )
    except Exception as exc:  # noqa: BLE001
        return {"configurado": False, "error": str(exc), "tiendas": []}
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    return {
        "habilitado": settings.tiktok_enabled,
        "app_configurada": bool(settings.tiktok_app_key and settings.tiktok_app_secret),
        "service_id": bool(settings.tiktok_service_id),
        "redirect_uri": settings.tiktok_redirect_uri,
        "tiendas": [
            {
                "seller": f.get("seller_name"),
                "shop_id": f.get("shop_id"),
                "expira": str(f.get("expira") or ""),
                "vigente": bool(f.get("expira") and f["expira"] > ahora),
                "actualizado": str(f.get("updated_at") or ""),
            }
            for f in filas
        ],
    }


def access_token(shop_id: str | None = None) -> str | None:
    """Access token descifrado de una tienda (o el más reciente)."""
    try:
        _asegurar_tabla()
        if shop_id:
            fila = db.fetch_one(
                "SELECT access_token FROM tiktok_tokens WHERE shop_id=%s", (shop_id,))
        else:
            fila = db.fetch_one(
                "SELECT access_token FROM tiktok_tokens ORDER BY updated_at DESC LIMIT 1")
        return _descifrar(fila.get("access_token")) if fila else None
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer el token de TikTok: %s", exc)
        return None
