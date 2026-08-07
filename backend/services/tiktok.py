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

_STATE_TTL = 900  # 15 min: de sobra para que el seller apruebe la pantalla


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


# ── Persistencia ──────────────────────────────────────────────────────────────

def _a_datetime(epoch: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return None


def guardar(data: dict[str, Any]) -> int:
    """
    Persiste el token cifrado. Devuelve cuántas tiendas se guardaron.

    Una autorización puede traer VARIAS tiendas (`seller_name` con N shops); se
    guarda una fila por tienda para poder operarlas por separado.
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

    tiendas = data.get("granted_shops") or data.get("shops") or []
    if not tiendas:
        # Sin detalle de tiendas guardamos igual: el token sirve para consultarlas.
        tiendas = [{"shop_id": open_id or seller or "default", "shop_cipher": None}]

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
            (seller, open_id, str(t.get("shop_id") or t.get("id") or "default"),
             t.get("shop_cipher"), access_c, refresh_c, expira, refresh_expira, ahora),
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
