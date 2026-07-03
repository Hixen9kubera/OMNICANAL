"""
crear_producto.py — Flujo REAL de creación de productos (canal Crear Productos).

Al dar "Crear productos" en Omnicanal, por cada SKU (draft en Woo + URL de
Alibaba) se ejecuta en segundo plano:

  1. Apify        → scrape de la URL de Alibaba (título, precio, specs, imágenes)
  2. Claude       → título SEO + descripción (usa título de Alibaba + imagen)
  3. Gemini       → limpia logos/textos de hasta 10 imágenes → WordPress Media
  4. Mercado Libre→ categoría (ml_cat_id de costos_finales, o domain_discovery)
  5. MySQL        → precios (costos_finales) y costos (costos_validados)
  6. WooCommerce  → update completo + status draft → inprogress (batch API)

El producto desaparece de "Crear Productos" y aparece en la pestaña Omnicanal.
El avance se consulta en GET /api/crear/progreso (cola en memoria).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

import httpx

from config import settings
from services import db, meli, woocommerce

log = logging.getLogger("omnicanal.crear_producto")

MAX_IMAGENES = 10
CLAUDE_MODEL = "claude-opus-4-8"
GEMINI_MODEL = "gemini-3-pro-image-preview"  # edición de imágenes ("Nano Banana")

_APIFY = "https://api.apify.com/v2"
_ML_API = "https://api.mercadolibre.com"

# ── Cola / progreso en memoria ──────────────────────────────────────────────────
_progreso: dict[str, dict[str, Any]] = {}
_sem = asyncio.Semaphore(2)  # productos procesándose a la vez


def _set(sku: str, estado: str, paso: str, **extra: Any) -> None:
    _progreso[sku] = {
        "sku": sku, "estado": estado, "paso": paso,
        "actualizado": time.time(), **extra,
    }
    log.info("crear[%s] %s: %s", sku, estado, paso)


def progreso() -> list[dict[str, Any]]:
    """Estado de todos los SKUs encolados/procesados (más recientes primero)."""
    return sorted(_progreso.values(), key=lambda x: x["actualizado"], reverse=True)


def encolar(items: list[dict[str, Any]]) -> int:
    """
    Encola items {sku, wc_id, alibaba_url} para creación en segundo plano.
    Ignora SKUs que ya están en cola o procesándose. Devuelve cuántos encoló.
    """
    n = 0
    for it in items:
        sku = it["sku"]
        if _progreso.get(sku, {}).get("estado") in ("en_cola", "procesando"):
            continue
        _set(sku, "en_cola", "En cola…")
        asyncio.create_task(_procesar(sku, it.get("wc_id"), it["alibaba_url"]))
        n += 1
    return n


# ── Paso 1: Apify (scrape Alibaba) ──────────────────────────────────────────────

_COOKIES_USD = [
    {"name": "_curr_code", "value": "USD", "domain": ".alibaba.com", "path": "/"},
    {"name": "sc_g_cfg_f", "value": "sc_b_currency=USD", "domain": ".alibaba.com", "path": "/"},
]


async def scrape_alibaba(url: str) -> dict[str, Any] | None:
    """Corre el actor de Apify sobre la URL y devuelve los datos normalizados."""
    if not settings.apify_api_key:
        raise RuntimeError("APIFY_API_KEY no configurada en .env")
    actor = settings.apify_alibaba_actor.replace("/", "~")
    payload = {
        "startUrls": [{"url": url, "cookies": _COOKIES_USD}],
        "maxConcurrency": 1,
        "timeoutSecs": 270,
        "debugLog": False,
        "currency": "USD",
        "cookies": _COOKIES_USD,
        "proxyConfig": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
            "apifyProxyCountry": "US",
        },
    }
    token = {"token": settings.apify_api_key}
    async with httpx.AsyncClient(timeout=60.0) as cli:
        r = await cli.post(
            f"{_APIFY}/acts/{actor}/runs",
            params={**token, "memory": 1024}, json=payload,
        )
        r.raise_for_status()
        run_id = r.json()["data"]["id"]

        estado, datos_run = "RUNNING", {}
        for _ in range(72):  # hasta ~6 min
            await asyncio.sleep(5)
            rs = await cli.get(f"{_APIFY}/actor-runs/{run_id}", params=token)
            datos_run = rs.json()["data"]
            estado = datos_run.get("status")
            if estado in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break
        if estado != "SUCCEEDED":
            log.warning("Apify run %s terminó en %s", run_id, estado)
            return None

        di = await cli.get(
            f"{_APIFY}/datasets/{datos_run['defaultDatasetId']}/items", params=token,
        )
        items = di.json()

    if not items:
        return None
    it = items[0]
    precio = it.get("price") or {}
    moneda = (precio.get("currency") or "").upper()
    if moneda and moneda not in ("USD", "MXN", "US$", "$"):
        log.warning("scrape %s: moneda inesperada %s, se ignora precio", url, moneda)
        precio = {}
    imagenes = [u for u in (it.get("images") or []) if u][:MAX_IMAGENES]
    return {
        "titulo": it.get("title") or "",
        "precio_min": precio.get("min"),
        "precio_max": precio.get("max"),
        "moneda": moneda or None,
        "imagenes": imagenes,
        "descripcion_proveedor": it.get("description") or "",
        "specs": it.get("specifications") or {},
        "url": url,
    }


# ── Paso 2: Claude (título SEO + descripción) ───────────────────────────────────

_ESQUEMA_SEO = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string", "description": "Título para Mercado Libre México, máx 60 caracteres"},
        "descripcion": {"type": "string", "description": "Descripción HTML (100-250 palabras)"},
    },
    "required": ["titulo", "descripcion"],
    "additionalProperties": False,
}


def _prompt_seo(titulo_alibaba: str, url: str) -> str:
    return (
        "Eres un experto en copywriting para Mercado Libre México.\n"
        "Observa la imagen del producto y usa el título de referencia de Alibaba "
        "para crear un título y una descripción optimizados para Mercado Libre México.\n\n"
        f"TÍTULO DE REFERENCIA (Alibaba): {titulo_alibaba}\n"
        f"URL ALIBABA: {url}\n\n"
        "REGLAS TÍTULO: 100% español mexicano, máximo 60 caracteres, palabras "
        "cotidianas de México (tenis, lentes, chamarra…), incluye palabras clave "
        "de búsqueda, sin nombre de marca del proveedor.\n"
        "REGLAS DESCRIPCIÓN: español mexicano, 100-250 palabras, HTML básico "
        "(<p><strong><ul><li>), destaca beneficios y características visibles en "
        "la imagen. NO usar emojis. NO inventar especificaciones que no veas."
    )


async def titulo_descripcion_ia(
    titulo_alibaba: str, imagen_url: str | None, url: str
) -> dict[str, str] | None:
    """Genera {titulo, descripcion} con Claude (imagen por URL + título Alibaba)."""
    from anthropic import AsyncAnthropic

    cli = AsyncAnthropic(api_key=settings.anthropic_api_key)
    contenido: list[dict[str, Any]] = []
    if imagen_url:
        contenido.append({"type": "image", "source": {"type": "url", "url": imagen_url}})
    contenido.append({"type": "text", "text": _prompt_seo(titulo_alibaba, url)})

    try:
        resp = await cli.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            output_config={"format": {"type": "json_schema", "schema": _ESQUEMA_SEO}},
            messages=[{"role": "user", "content": contenido}],
        )
    except Exception as exc:  # noqa: BLE001
        # La imagen remota puede no ser accesible para la API: reintento sin imagen.
        if imagen_url:
            log.warning("Claude con imagen falló (%s); reintento solo texto", exc)
            return await titulo_descripcion_ia(titulo_alibaba, None, url)
        log.error("Claude SEO falló: %s", exc)
        return None

    if resp.stop_reason == "refusal":
        log.warning("Claude rechazó la solicitud SEO para %s", url)
        return None
    texto = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(texto)
        return {"titulo": data["titulo"].strip(), "descripcion": data["descripcion"].strip()}
    except Exception as exc:  # noqa: BLE001
        log.error("Claude SEO devolvió JSON inválido: %s", exc)
        return None


# ── Paso 3: Gemini (limpiar logos) + WordPress Media ────────────────────────────

# Apagado por el usuario (2026-07-02): las imágenes se copian TAL CUAL de
# Alibaba (Woo las descarga solo); el procesamiento de imagen vendrá después
# como proceso aparte. Poner en True para reactivar la limpieza con Gemini.
LIMPIAR_CON_IA = False

# Regla del usuario: SOLO quitar logos/marcas/textos chinos y optimizar la
# imagen. NO tocar el fondo, el encuadre ni el producto.
_PROMPT_IMG_PRINCIPAL = (
    "Remove all logos, brand marks, watermarks and any Chinese or foreign text "
    "from this product image. Do NOT change the background, framing or the "
    "product itself. Enhance the image quality: sharpness, lighting and color "
    "balance. Return the edited image."
)
_PROMPT_IMG_SECUNDARIA = _PROMPT_IMG_PRINCIPAL


async def _gemini_limpiar(data: bytes, mime: str, principal: bool) -> bytes | None:
    """Edita una imagen con Gemini (quita logos/fondo). None si falla."""
    if not settings.gemini_api_key:
        return None
    body = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
                {"text": _PROMPT_IMG_PRINCIPAL if principal else _PROMPT_IMG_SECUNDARIA},
            ],
        }],
    }
    # La edición de imagen puede tardar bastante: timeout amplio + 1 reintento.
    for intento in range(2):
        try:
            async with httpx.AsyncClient(timeout=240.0) as cli:
                r = await cli.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                    params={"key": settings.gemini_api_key}, json=body,
                )
            if r.status_code != 200:
                log.warning("Gemini HTTP %d: %s", r.status_code, r.text[:150])
                return None
            for cand in r.json().get("candidates", []):
                for part in (cand.get("content") or {}).get("parts", []):
                    inline = part.get("inlineData") or part.get("inline_data") or {}
                    if inline.get("data"):
                        return base64.b64decode(inline["data"])
            return None  # respondió sin imagen (p. ej. rechazo): no reintentar
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Gemini edición falló (intento %d): %s: %s",
                intento + 1, type(exc).__name__, exc,
            )
    return None


async def procesar_imagenes(sku: str, urls: list[str]) -> tuple[list[dict[str, Any]], int]:
    """
    Descarga cada imagen de Alibaba, la limpia con Gemini (si falla, usa la
    original) y la sube a WordPress. Devuelve (images para Woo, cuántas limpió).
    Preserva el orden; la primera es la principal.
    """
    if not LIMPIAR_CON_IA:
        # Copia directa: se pasan las URLs de Alibaba y WooCommerce las descarga
        # él mismo al hacer el update (segundos en vez de minutos).
        return [{"src": u} for u in urls], 0

    sem = asyncio.Semaphore(3)
    listas = 0

    def _avance() -> None:
        nonlocal listas
        listas += 1
        if _progreso.get(sku, {}).get("estado") == "procesando":
            _set(sku, "procesando",
                 f"3/5 · Imágenes: {listas}/{len(urls)} procesadas…",
                 wc_id=_progreso[sku].get("wc_id"))

    async def _una(i: int, url: str) -> tuple[dict[str, Any] | None, bool]:
        async with sem:
            try:
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as cli:
                    r = await cli.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code != 200 or not r.content:
                    _avance()
                    return {"src": url}, False  # que Woo intente descargarla
                mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
                if not mime.startswith("image/"):
                    mime = "image/jpeg"
                editada = await _gemini_limpiar(r.content, mime, principal=(i == 0))
                final = editada if editada else r.content
                subida = await woocommerce.subir_imagen_wp(f"{sku}-{i + 1}", final)
                _avance()
                if subida:
                    return {"id": subida[0]}, bool(editada)
                return {"src": url}, False
            except Exception as exc:  # noqa: BLE001
                log.warning("imagen %d de %s falló: %s", i + 1, sku, exc)
                _avance()
                return None, False

    resultados = await asyncio.gather(*[_una(i, u) for i, u in enumerate(urls)])
    imagenes = [r[0] for r in resultados if r[0]]
    limpiadas = sum(1 for r in resultados if r[1])
    return imagenes, limpiadas


# ── Paso 4: Categoría de Mercado Libre ──────────────────────────────────────────

async def categoria_ml(sku: str, titulo: str) -> dict[str, str] | None:
    """
    Devuelve {"category_id", "category_name"}. Primero intenta el ml_cat_id ya
    calculado en costos_finales; si no hay, usa domain_discovery con el título.
    """
    try:
        row = await asyncio.to_thread(
            db.fetch_one, "SELECT ml_cat_id FROM costos_finales WHERE sku=%s", (sku,)
        )
    except Exception:  # noqa: BLE001
        row = None
    cat_id = (row or {}).get("ml_cat_id")

    try:
        async with httpx.AsyncClient(base_url=_ML_API, timeout=20.0) as cli:
            if cat_id:
                r = await cli.get(f"/categories/{cat_id}")
                nombre = r.json().get("name") if r.status_code == 200 else None
                return {"category_id": cat_id, "category_name": nombre or ""}
            token = await asyncio.to_thread(meli._access_token, None)
            if not token:
                return None
            r = await cli.get(
                f"/sites/{settings.ml_site_id}/domain_discovery/search",
                params={"limit": 1, "q": titulo},
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 200 and r.json():
                d = r.json()[0]
                return {
                    "category_id": d.get("category_id"),
                    "category_name": d.get("category_name") or "",
                }
    except Exception as exc:  # noqa: BLE001
        log.warning("categoría ML para %s falló: %s", sku, exc)
    return None


# ── Paso 5: Precios y costos desde MySQL ────────────────────────────────────────

def datos_dinero(sku: str) -> dict[str, Any]:
    """Lee precio (costos_finales) y costos/dimensiones (costos_validados)."""
    salida: dict[str, Any] = {}
    try:
        cf = db.fetch_one("SELECT * FROM costos_finales WHERE sku=%s", (sku,))
        if cf:
            salida.update({
                "precio_base": cf.get("precio_base"),
                "precio_sugerido": cf.get("precio_sugerido"),
                "peso": cf.get("peso"),
                "largo": cf.get("largo"), "alto": cf.get("alto"), "ancho": cf.get("ancho"),
                "costo_unitario": cf.get("costo_unitario"),
                "costo_comision": cf.get("costo_comision"),
                "costo_fee_envio": cf.get("costo_fee_envio"),
            })
        if not salida.get("peso") or not salida.get("largo"):
            cv = db.fetch_one("SELECT * FROM costos_validados WHERE sku=%s", (sku,))
            if cv:
                salida.setdefault("peso", cv.get("peso"))
                salida.setdefault("largo", cv.get("largo"))
                salida.setdefault("alto", cv.get("alto"))
                salida.setdefault("ancho", cv.get("ancho"))
    except Exception as exc:  # noqa: BLE001
        log.warning("datos_dinero(%s) falló: %s", sku, exc)
    return salida


# ── Paso 6: Update a WooCommerce + status inprogress ────────────────────────────

async def _actualizar_wc(wc_id: int, payload: dict[str, Any]) -> None:
    async with woocommerce._client() as cli:
        r = await cli.put(f"/products/{wc_id}", json=payload, timeout=180.0)
        r.raise_for_status()


async def _estado_inprogress(wc_id: int) -> None:
    # El status custom "inprogress" solo entra por la batch API (el PUT normal
    # lo rechaza). Mismo truco que usa el pipeline original.
    async with woocommerce._client() as cli:
        r = await cli.post(
            "/products/batch",
            json={"update": [{"id": wc_id, "status": "inprogress"}]},
            timeout=60.0,
        )
        r.raise_for_status()


def _fmt(v: Any) -> str | None:
    if v in (None, ""):
        return None
    return f"{float(v):.2f}"


# ── Orquestador por producto ────────────────────────────────────────────────────

async def _procesar(sku: str, wc_id: int | None, url: str) -> None:
    async with _sem:
        try:
            if not wc_id:
                p = await woocommerce.obtener_producto_por_sku(sku)
                wc_id = p.get("wc_id") if p else None
            if not wc_id:
                _set(sku, "error", "No se encontró el producto en WooCommerce")
                return

            _set(sku, "procesando", "1/5 · Scrapeando Alibaba…", wc_id=wc_id)
            scrape = await scrape_alibaba(url)
            if not scrape or not scrape["titulo"]:
                _set(sku, "error", "Alibaba no devolvió datos (¿URL correcta?)", wc_id=wc_id)
                return

            _set(sku, "procesando", "2/5 · Mejorando título y descripción con IA…", wc_id=wc_id)
            primera = scrape["imagenes"][0] if scrape["imagenes"] else None
            ia = await titulo_descripcion_ia(scrape["titulo"], primera, url)

            n_imgs = len(scrape["imagenes"])
            paso3 = (
                f"3/5 · Limpiando y subiendo {n_imgs} imágenes…"
                if LIMPIAR_CON_IA else f"3/5 · Copiando {n_imgs} imágenes de Alibaba…"
            )
            _set(sku, "procesando", paso3, wc_id=wc_id)
            imagenes, limpiadas = await procesar_imagenes(sku, scrape["imagenes"])

            titulo = (ia or {}).get("titulo") or scrape["titulo"]
            _set(sku, "procesando", "4/5 · Categoría de Mercado Libre…", wc_id=wc_id)
            cat = await categoria_ml(sku, titulo)
            dinero = await asyncio.to_thread(datos_dinero, sku)

            _set(sku, "procesando", "5/5 · Actualizando WooCommerce…", wc_id=wc_id)
            meta = [
                {"key": "url_alibaba", "value": url},
                {"key": "alibaba_title_original", "value": scrape["titulo"]},
            ]
            if scrape.get("precio_min") is not None:
                meta.append({"key": "alibaba_price", "value": str(scrape["precio_min"])})
            if cat:
                meta.append({"key": "ml_category_id", "value": cat["category_id"]})
                meta.append({"key": "ml_category_name", "value": cat["category_name"]})
            if dinero.get("costo_fee_envio") is not None:
                meta.append({"key": "wc_kam_costo_envio", "value": str(dinero["costo_fee_envio"])})
            if dinero.get("costo_comision") is not None:
                meta.append({"key": "wc_kam_costo_comision", "value": str(dinero["costo_comision"])})

            payload: dict[str, Any] = {"name": titulo, "meta_data": meta}
            if (ia or {}).get("descripcion"):
                payload["description"] = ia["descripcion"]
            elif scrape["descripcion_proveedor"]:
                payload["description"] = scrape["descripcion_proveedor"]
            if imagenes:
                payload["images"] = imagenes
            if _fmt(dinero.get("precio_base")):
                payload["regular_price"] = _fmt(dinero["precio_base"])
            if _fmt(dinero.get("precio_sugerido")):
                payload["sale_price"] = _fmt(dinero["precio_sugerido"])
            if dinero.get("peso"):
                payload["weight"] = str(dinero["peso"])
            if dinero.get("largo") and dinero.get("alto") and dinero.get("ancho"):
                payload["dimensions"] = {
                    "length": str(dinero["largo"]),
                    "width": str(dinero["ancho"]),
                    "height": str(dinero["alto"]),
                }

            await _actualizar_wc(wc_id, payload)
            await _estado_inprogress(wc_id)

            partes = [f"{len(imagenes)} imágenes ({limpiadas} limpiadas con IA)"]
            partes.append(f"categoría {cat['category_id']}" if cat else "SIN categoría ML")
            if not dinero.get("precio_sugerido"):
                partes.append("SIN precio (falta en costos_finales)")
            _set(sku, "completado", " · ".join(partes), wc_id=wc_id, titulo=titulo)
        except Exception as exc:  # noqa: BLE001
            log.exception("crear[%s] falló", sku)
            _set(sku, "error", str(exc)[:200], wc_id=wc_id)

    # El producto pasó a inprogress: sale de Crear Productos al instante (sin
    # re-escanear). La vista Omnicanal lo mostrará cuando su cache expire.
    if wc_id and _progreso.get(sku, {}).get("estado") == "completado":
        woocommerce.quitar_de_drafts(wc_id)
