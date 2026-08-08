"""
tiktok.py — Endpoints de autorización de TikTok Shop.

  GET /api/tiktok/autorizar → manda al seller a la pantalla de consentimiento
  GET /api/tiktok/callback  → recibe el `code` de TikTok y lo canjea (PÚBLICO)
  GET /api/tiktok/estado    → diagnóstico: ¿hay conexión viva y hasta cuándo?

El callback es la URL que se registra en el Partner Center (App & Service →
Enable API → Redirect URL):

    https://backendomnicanal-production.up.railway.app/api/tiktok/callback

Tiene que coincidir CARÁCTER POR CARÁCTER con la registrada; una diagonal final
de más y TikTok rechaza el canje.

Va abierto en el middleware porque TikTok no puede mandar nuestra X-API-Key
(mismo caso que el webhook de ML). Lo que lo protege es el `state` firmado.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from config import settings
from core.seguridad import requiere_api_key
from services import db, tiktok

log = logging.getLogger("omnicanal.tiktok")
router = APIRouter(prefix="/api/tiktok", tags=["tiktok"])


def _exigir_encendido() -> None:
    if not settings.tiktok_enabled:
        raise HTTPException(
            503,
            "La integración con TikTok Shop está apagada (TIKTOK_ENABLED=false).",
        )


def _pagina(titulo: str, detalle: str, ok: bool) -> HTMLResponse:
    """Respuesta para un humano: el seller termina el flujo en su navegador."""
    color = "#0a7d32" if ok else "#b3261e"
    return HTMLResponse(
        f"""<!doctype html><meta charset="utf-8">
<title>{titulo} · Kubera</title>
<div style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;
            padding:2rem;border:1px solid #e0e0e0;border-radius:12px">
  <h1 style="color:{color};font-size:1.25rem;margin:0 0 .75rem">{titulo}</h1>
  <p style="color:#444;line-height:1.5;margin:0">{detalle}</p>
</div>""",
        status_code=200 if ok else 400,
    )


@router.get("/autorizar")
async def autorizar(_: None = Depends(requiere_api_key)) -> RedirectResponse:
    """Redirige a la pantalla de consentimiento de TikTok con un `state` fresco."""
    _exigir_encendido()
    try:
        return RedirectResponse(tiktok.url_autorizacion(), status_code=307)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/callback")
async def callback(
    code: str | None = Query(default=None),
    auth_code: str | None = Query(default=None),
    state: str | None = Query(default=None),
) -> HTMLResponse:
    """
    Recibe la vuelta de TikTok. PÚBLICO a propósito (ver docstring del módulo).

    TikTok ha usado `code` y `auth_code` según la versión del flujo; se aceptan
    los dos para no depender de cuál mande el Partner Center hoy.
    """
    _exigir_encendido()

    recibido = code or auth_code
    if not recibido:
        return _pagina("Falta el código", "TikTok no envió ningún código de autorización.", False)

    if not tiktok.verificar_state(state):
        log.warning("TikTok callback con state inválido o caducado")
        return _pagina(
            "Enlace vencido",
            "El enlace de autorización caducó o no lo generamos nosotros. "
            "Vuelve a empezar desde el panel.",
            False,
        )

    try:
        data = await tiktok.intercambiar_code(recibido)
        # PASO OBLIGATORIO: el canje del token NO devuelve las tiendas ni su
        # `cipher`, y sin cipher el canal queda inservible (es query param
        # obligatorio en Create Product, Update Inventory y Update Price).
        # Si esta llamada falla se guarda igual — perder el token recién
        # canjeado por un fallo de red sería peor: el code ya se consumió y
        # habría que rehacer toda la autorización.
        try:
            shops = await tiktok.tiendas_autorizadas(data["access_token"])
        except Exception as exc:  # noqa: BLE001
            log.warning("TikTok: token canjeado pero no se pudieron leer las "
                        "tiendas (%s). Se guarda sin cipher; hay que reparar.", exc)
            shops = []
        tiendas = tiktok.guardar(data, shops)
    except Exception as exc:  # noqa: BLE001
        log.exception("TikTok: falló el canje del code")
        return _pagina("No se pudo conectar", str(exc), False)

    seller = data.get("seller_name") or "la cuenta"
    return _pagina(
        "Tienda conectada",
        f"TikTok Shop quedó conectado para <b>{seller}</b> "
        f"({tiendas} tienda/s). Ya puedes cerrar esta ventana.",
        True,
    )


@router.post("/reparar-tiendas", dependencies=[Depends(requiere_api_key)])
async def reparar_tiendas() -> dict:
    """
    Rellena el `shop_cipher` de una autorización que quedó sin él.

    Existe porque la primera tienda se conectó ANTES de que el canje leyera las
    tiendas: su fila guardó el `open_id` como shop_id y el cipher en NULL, y sin
    cipher no se puede crear ni un producto. Esto lo arregla SIN volver a
    autorizar — el token sigue siendo válido.

    También sirve de refresco: si el seller agrega una tienda a la misma
    autorización, vuelve a correrse y aparece.

    Corre desde Railway a propósito: la lista de IPs permitidas de TikTok deja
    fuera a las máquinas de desarrollo, así que este es el único lugar desde
    donde la llamada pasa.
    """
    _exigir_encendido()
    token = tiktok.access_token()
    if not token:
        raise HTTPException(409, "No hay ninguna tienda autorizada todavía.")
    try:
        shops = await tiktok.tiendas_autorizadas(token)
    except Exception as exc:  # noqa: BLE001
        log.warning("TikTok reparar-tiendas: %s", exc)
        raise HTTPException(502, f"TikTok rechazó la consulta: {exc}") from exc
    if not shops:
        return {"ok": False, "motivo": "TikTok no devolvió ninguna tienda.",
                "tiendas": []}
    for s in shops:
        db.execute(
            "UPDATE tiktok_tokens SET shop_id=%s, shop_cipher=%s, seller_name=%s "
            "WHERE shop_cipher IS NULL OR shop_id=%s",
            (str(s.get("id")), s.get("cipher"), s.get("name") or "",
             str(s.get("id"))),
        )
    log.info("TikTok: %d tienda(s) reparadas con su cipher", len(shops))
    return {"ok": True, "reparadas": len(shops),
            "tiendas": [{"id": s.get("id"), "name": s.get("name"),
                         "region": s.get("region"),
                         "seller_type": s.get("seller_type"),
                         "cipher": bool(s.get("cipher"))} for s in shops],
            "estado": tiktok.estado()}


@router.get("/estado")
async def estado(_: None = Depends(requiere_api_key)) -> dict:
    """Diagnóstico. No devuelve el token, solo si hay conexión y su vigencia."""
    return tiktok.estado()
