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


@router.get("/explorar", dependencies=[Depends(requiere_api_key)])
async def explorar(sku: str = Query(..., description="SKU de WooCommerce")) -> dict:
    """
    Reconocimiento COMPLETO de un SKU contra TikTok. NO publica nada.

    Existe porque la lista de IPs permitidas de TikTok deja fuera las máquinas
    de desarrollo (y una IP doméstica cambia sola: la de Brandon se renovó a
    media sesión). Railway tiene IPs fijas y permitidas, así que este es el
    único lugar desde donde se puede investigar.

    Cada paso captura su propio error en vez de abortar: un fallo en las marcas
    no debe impedir ver los atributos. Con una sola llamada se sabe todo lo que
    falta para armar el payload.
    """
    _exigir_encendido()
    token = tiktok.access_token()
    if not token:
        raise HTTPException(409, "No hay tienda autorizada.")
    fila = db.fetch_all("SELECT shop_cipher FROM tiktok_tokens "
                        "WHERE shop_cipher IS NOT NULL LIMIT 1")
    if not fila:
        raise HTTPException(409, "Sin shop_cipher — corre POST /api/tiktok/reparar-tiendas.")
    cipher = fila[0]["shop_cipher"]

    from services import woocommerce as wc
    salida: dict = {"sku": sku, "pasos": {}}

    async def paso(nombre, ruta, params=None, cuerpo=None, metodo="GET"):
        try:
            d = await tiktok.llamar(ruta, token, params, cuerpo, metodo)
            salida["pasos"][nombre] = {"ok": True}
            return d
        except Exception as exc:  # noqa: BLE001
            salida["pasos"][nombre] = {"ok": False, "error": str(exc)[:400]}
            return None

    # ── el producto en Woo ────────────────────────────────────────────────
    # `_cb` es cache-bust: LiteSpeed cachea chunche.shop y una lectura vieja
    # aquí se convierte en un payload con datos rancios (regla de la casa nº5).
    try:
        async with wc._client() as cli:
            r = await cli.get("/products", params={"sku": sku, "_cb": "tkexp"})
        prods = r.json() if r.status_code == 200 else []
        p = prods[0] if prods else None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, f"No se pudo leer {sku} de Woo: {exc}") from exc
    if not p:
        raise HTTPException(404, f"{sku} no existe en WooCommerce.")
    titulo = (p.get("name") or "")[:255]
    salida["producto"] = {
        "titulo": titulo,
        "precio_regular": p.get("regular_price"),
        "precio_venta": p.get("price"),
        "stock": p.get("stock_quantity"),
        "peso": p.get("weight"),
        "dimensiones": p.get("dimensions"),
        "imagenes": [i.get("src") for i in (p.get("images") or [])][:9],
        "atributos_woo": {a.get("name"): (a.get("options") or [None])[0]
                          for a in (p.get("attributes") or [])},
    }

    # ── 1. ¿TikTok recomienda categoría desde el título? ───────────────────
    rec = await paso("recomendar_categoria",
                     "/product/202309/categories/recommend",
                     {"shop_cipher": cipher},
                     {"product_title": titulo}, "POST")
    cat_id = None
    if rec:
        salida["recomendacion"] = rec
        cadena = rec.get("categories") or []
        if cadena:
            cat_id = (cadena[-1] or {}).get("id")
        cat_id = cat_id or rec.get("leaf_category_id")

    # ── 2. atributos y reglas de esa categoría ────────────────────────────
    if cat_id:
        salida["categoria_id"] = cat_id
        at = await paso("atributos", f"/product/202309/categories/{cat_id}/attributes",
                        {"shop_cipher": cipher, "locale": "es-MX"})
        if at:
            attrs = at.get("attributes") or []
            salida["atributos"] = {
                "total": len(attrs),
                "obligatorios": [
                    {"id": a.get("id"), "nombre": a.get("name"),
                     "tipo": a.get("type"),
                     "opciones": [v.get("name") for v in (a.get("values") or [])][:25]}
                    for a in attrs
                    if a.get("is_requried") or a.get("is_required")
                ],
                "opcionales": [a.get("name") for a in attrs
                               if not (a.get("is_requried") or a.get("is_required"))][:40],
            }
        rl = await paso("reglas", f"/product/202309/categories/{cat_id}/rules",
                        {"shop_cipher": cipher})
        if rl:
            salida["reglas"] = rl

    # ── 3. marcas ─────────────────────────────────────────────────────────
    br = await paso("marcas", "/product/202309/brands",
                    {"shop_cipher": cipher, "page_size": 20})
    if br:
        salida["marcas"] = {"total": br.get("total_count"),
                            "muestra": [{"id": b.get("id"), "name": b.get("name")}
                                        for b in (br.get("brands") or [])][:10]}

    salida["siguiente"] = (
        "Con los atributos obligatorios ya se puede armar el payload de "
        "createProduct. Falta confirmar cómo se suben las imágenes."
    )
    return salida


@router.get("/estado")
async def estado(_: None = Depends(requiere_api_key)) -> dict:
    """Diagnóstico. No devuelve el token, solo si hay conexión y su vigencia."""
    return tiktok.estado()
