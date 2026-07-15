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
import re
import time
from typing import Any

import httpx

from config import settings
from services import costos, db, meli, woocommerce

log = logging.getLogger("omnicanal.crear_producto")

MAX_IMAGENES = 10
CLAUDE_MODEL = "claude-opus-4-8"
GEMINI_MODEL = "gemini-3-pro-image-preview"  # edición de imágenes ("Nano Banana")

_APIFY = "https://api.apify.com/v2"
_ML_API = "https://api.mercadolibre.com"

# ── Cola / progreso en memoria ──────────────────────────────────────────────────
_progreso: dict[str, dict[str, Any]] = {}
_sem = asyncio.Semaphore(2)  # productos procesándose a la vez

# ── Bitácora persistente (crear_logs) ───────────────────────────────────────────
# Los logs de Railway se purgan con cada deploy; esta tabla conserva el rastro
# completo de cada creación (encolado → pasos → completado/error) para auditar
# después qué se creó, cuándo y en qué terminó. La consulta el endpoint
# GET /api/crear/historial y la auditoría GET /api/crear/auditoria.
_DDL_LOGS = """
CREATE TABLE IF NOT EXISTS crear_logs (
    id      BIGINT AUTO_INCREMENT PRIMARY KEY,
    sku     VARCHAR(60)  NOT NULL,
    wc_id   BIGINT NULL,
    estado  VARCHAR(20)  NOT NULL,
    paso    VARCHAR(255),
    detalle TEXT NULL,
    creado  DATETIME NOT NULL,
    INDEX idx_sku (sku),
    INDEX idx_estado (estado),
    INDEX idx_creado (creado)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""
_schema_logs_ok = False


def asegurar_schema_logs() -> None:
    """Crea `crear_logs` si no existe. La usan también los endpoints de LECTURA
    (/historial, /auditoria): sin esto, consultan una tabla inexistente y dan
    500 en un deploy donde aún no ha corrido ninguna creación. [Bug detectado
    en el primer deploy a producción, 2026-07-15.]"""
    global _schema_logs_ok
    if _schema_logs_ok:
        return
    try:
        db.execute(_DDL_LOGS)
        _schema_logs_ok = True
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo asegurar el schema de crear_logs: %s", exc)


def _persistir_log(sku: str, estado: str, paso: str, extra: dict[str, Any]) -> None:
    try:
        asegurar_schema_logs()
        if not _schema_logs_ok:
            return
        detalle = {k: v for k, v in extra.items() if k != "wc_id"}
        detalle_json = json.dumps(detalle, ensure_ascii=False, default=str) if detalle else None
        # Salvaguarda de almacenamiento: `detalle` es contexto de depuración, no
        # un payload — se trunca a 4 KB para que ningún paso futuro con un JSON
        # inesperadamente grande convierta esta bitácora en otro amazon_backlog
        # (161 MB por guardar blobs completos). El resumen siempre cabe.
        if detalle_json and len(detalle_json) > 4000:
            detalle_json = detalle_json[:4000] + '…(truncado)"'
        db.execute(
            "INSERT INTO crear_logs (sku, wc_id, estado, paso, detalle, creado) "
            "VALUES (%s,%s,%s,%s,%s,UTC_TIMESTAMP())",
            (sku, extra.get("wc_id"), estado, (paso or "")[:255], detalle_json),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo escribir crear_logs(%s): %s", sku, exc)


def _set(sku: str, estado: str, paso: str, **extra: Any) -> None:
    _progreso[sku] = {
        "sku": sku, "estado": estado, "paso": paso,
        "actualizado": time.time(), **extra,
    }
    log.info("crear[%s] %s: %s", sku, estado, paso)
    try:
        # sin bloquear el event loop (la escritura va a MySQL en Hostinger)
        asyncio.get_running_loop().run_in_executor(
            None, _persistir_log, sku, estado, paso, extra)
    except RuntimeError:
        _persistir_log(sku, estado, paso, extra)


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
        _set(sku, "en_cola", "En cola…", wc_id=it.get("wc_id"),
             alibaba_url=it["alibaba_url"])
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
    # El actor mutila el campo price ({min:1}); el precio REAL viene en
    # prices[].range / priceText como "$187$2.20" (=$1.87 actual + $2.20 orig,
    # sin punto). Se reconstruye desde ahí.
    precio_min, precio_max = _precio_alibaba_real(it)
    moneda = "USD"
    imagenes = [u for u in (it.get("images") or []) if u][:MAX_IMAGENES]
    specs = it.get("specifications") or {}

    # Paso 6: extraer TODAS las variables de las specifications de Alibaba.
    peso_kg = _spec_peso(specs.get("Single gross weight") or specs.get("Gross weight"))
    dims = _spec_dims(specs.get("Single package size") or specs.get("Package size"))
    cbm = None
    if dims:
        cbm = round(dims["largo"] * dims["ancho"] * dims["alto"] / 1_000_000, 5)  # cm³→m³
    unidades = specs.get("Selling units") or specs.get("Minimum order quantity")

    return {
        "titulo": it.get("title") or "",
        "precio_min": precio_min,
        "precio_max": precio_max,
        "moneda": moneda or None,
        "imagenes": imagenes,
        "n_imagenes": len(imagenes),
        "descripcion_proveedor": it.get("description") or "",
        "caracteristicas": it.get("details") or {},
        "specs": specs,
        # variables del paso 6
        "peso_kg": peso_kg,
        "dims_cm": dims,          # {largo, ancho, alto} en cm, o None
        "cbm": cbm,               # m³ por unidad
        "unidades_venta": unidades,
        "producto_id": it.get("productId"),
        "url": url,
    }


def _precio_alibaba_real(it: dict[str, Any]) -> tuple[float | None, float | None]:
    """
    Reconstruye el precio REAL de Alibaba desde prices[].range / priceText.
    El actor devuelve el precio actual sin punto: "$187$2.20" = $1.87 (actual,
    reconstruido /100) + $2.20 (original). Devuelve (min, max) del rango real.
    """
    texto = str(it.get("priceText") or "")
    for p in (it.get("prices") or []):
        texto += " " + str(p.get("range", ""))
    reales: list[float] = []
    for n in re.findall(r"\$(\d+\.\d+)", texto):        # con decimal (originales)
        reales.append(float(n))
    for n in re.findall(r"\$(\d{3,})(?!\.\d)", texto):  # sin decimal → /100 (actual)
        reales.append(int(n) / 100)
    if not reales:
        # último recurso: el campo price crudo (aunque suele venir mal)
        pr = it.get("price") or {}
        mn, mx = pr.get("min"), pr.get("max")
        return (mn if isinstance(mn, (int, float)) and mn > 1 else None,
                mx if isinstance(mx, (int, float)) and mx > 1 else None)
    reales = sorted(set(reales))
    return reales[0], reales[-1]


def _num(s: Any) -> float | None:
    m = re.search(r"[\d]+(?:\.\d+)?", str(s or ""))
    return float(m.group()) if m else None


def _spec_peso(v: Any) -> float | None:
    """'0.325 kg' / '325 g' → kg."""
    if not v:
        return None
    n = _num(v)
    if n is None:
        return None
    return round(n / 1000, 4) if "g" in str(v).lower() and "kg" not in str(v).lower() else n


def _spec_dims(v: Any) -> dict[str, float] | None:
    """'39X31X58 cm' / '39*31*58' → {largo, ancho, alto} en cm."""
    if not v:
        return None
    nums = re.findall(r"\d+(?:\.\d+)?", str(v))
    if len(nums) >= 3:
        l, a, h = float(nums[0]), float(nums[1]), float(nums[2])
        return {"largo": l, "ancho": a, "alto": h}
    return None


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
        "Eres un experto en copywriting para MercadoLibre México.\n"
        "Observa la imagen del producto y usa el título de referencia de Alibaba para crear\n"
        "un título y descripción optimizados para Mercado Libre México.\n\n"
        f"TÍTULO DE REFERENCIA (Alibaba): {titulo_alibaba}\n"
        f"URL ALIBABA: {url}\n\n"
        "REGLAS TÍTULO: 100% español mexicano, máx 60 caracteres, palabras cotidianas "
        "generales con las que las personas buscan productos\n"
        "               (tenis / lentes / chamarra), función principal, sin mayúsculas excesivas.\n\n"
        "REGLAS DESCRIPCIÓN: español mexicano, 100-250 palabras, HTML básico (<p><strong><ul><li>),\n"
        "                    beneficios prácticos, tono cercano. Estructura clara con saltos de línea \\n."
        "NO usar ningún emoji, ícono, símbolo especial o carácter Unicode. Agregar caracteristicas principales.\n\n"
        "Responde EXACTAMENTE (sin markdown):\n"
        '{"titulo": "...", "descripcion": "..."}'
    )


def _parse_seo_json(texto: str) -> dict[str, str] | None:
    """Extrae {titulo, descripcion} del texto de Claude (JSON con o sin ruido)."""
    if not texto:
        return None
    try:
        m = re.search(r"\{.*\}", texto, re.DOTALL)
        data = json.loads(m.group()) if m else json.loads(texto)
        t = (data.get("titulo") or "").strip()
        d = (data.get("descripcion") or "").strip()
        return {"titulo": t, "descripcion": d} if t else None
    except Exception:  # noqa: BLE001
        return None


async def titulo_descripcion_ia(
    titulo_alibaba: str, imagen_url: str | None, url: str
) -> dict[str, str] | None:
    """
    Genera {titulo, descripcion} con Claude usando el título de Alibaba (+ imagen).
    NO usa output_config (no existe en SDK anthropic viejos como el de Railway):
    se pide el JSON en el prompt y se parsea con regex. Compatible con cualquier
    versión del SDK. A prueba de fallos: si falla con imagen, reintenta texto.
    """
    from anthropic import AsyncAnthropic

    cli = AsyncAnthropic(api_key=settings.anthropic_api_key)
    prompt = _prompt_seo(titulo_alibaba, url)

    async def _llamar(con_imagen: bool) -> dict[str, str] | None:
        contenido: list[dict[str, Any]] = []
        if con_imagen and imagen_url:
            contenido.append({"type": "image",
                              "source": {"type": "url", "url": imagen_url}})
        contenido.append({"type": "text", "text": prompt})
        resp = await cli.messages.create(
            model=CLAUDE_MODEL, max_tokens=2048,
            messages=[{"role": "user", "content": contenido}],
        )
        if getattr(resp, "stop_reason", None) == "refusal":
            return None
        texto = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return _parse_seo_json(texto)

    # 1) con imagen (si hay). 2) sin imagen. 3) 2º intento sin imagen.
    intentos = [True, False, False] if imagen_url else [False, False]
    ultimo = None
    for con_img in intentos:
        try:
            r = await _llamar(con_img)
            if r:
                return r
        except Exception as exc:  # noqa: BLE001
            ultimo = exc
            log.warning("SEO (imagen=%s) falló para %s: %s", con_img, url, exc)
    log.error("Claude SEO no produjo resultado para %s: %s", url, ultimo)
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

def _categoria_curada(sku: str) -> dict[str, str] | None:
    """
    Categoría de Mercado Libre CURADA (tabla categorias_ml, 12.8k SKUs).
    Busca por SKU exacto y, si no, por PREFIJO PADRE (CATEG-####). Trae
    category_id + category_name listos. `fuente` real=confirmada, predictor=predicha.
    """
    if not sku:
        return None
    try:
        r = db.fetch_one(
            "SELECT category_id, category_name, fuente FROM categorias_ml "
            "WHERE sku=%s AND category_id IS NOT NULL AND category_id != '' LIMIT 1",
            (sku,),
        )
        if not (r and r.get("category_id")):
            # fallback: prefijo padre (mismos primeros 2 segmentos)
            base = "-".join(sku.split("-")[:2])
            r = db.fetch_one(
                "SELECT category_id, category_name, fuente FROM categorias_ml "
                "WHERE sku LIKE %s AND category_id IS NOT NULL AND category_id != '' LIMIT 1",
                (base + "%",),
            )
    except Exception:  # noqa: BLE001
        return None
    if r and r.get("category_id"):
        return {"category_id": r["category_id"],
                "category_name": r.get("category_name") or "",
                "fuente": r.get("fuente") or "categorias_ml"}
    return None


async def get_or_create_wc_categoria(cli, nombre: str, ml_id: str = "") -> int | None:
    """
    Busca (o crea) en WooCommerce una categoría con el nombre de la categoría ML,
    guardando 'ML: {ml_id}' en la descripción. Devuelve su id de WC.
    """
    if not nombre:
        return None
    try:
        r = await cli.get("/products/categories",
                          params={"search": nombre, "per_page": 100, "_fields": "id,name"})
        if r.status_code == 200:
            for c in r.json():
                if c["name"].strip().lower() == nombre.strip().lower():
                    return c["id"]
        rc = await cli.post("/products/categories",
                           json={"name": nombre, "description": f"ML: {ml_id}"})
        if rc.status_code in (200, 201):
            return rc.json().get("id")
        # término ya existe con otro case
        data = rc.json() if rc.headers.get("content-type","").startswith("application/json") else {}
        return (data.get("data") or {}).get("resource_id")
    except Exception as exc:  # noqa: BLE001
        log.warning("get_or_create_wc_categoria(%s) falló: %s", nombre, exc)
    return None


async def categoria_ml(sku: str, titulo: str) -> dict[str, str] | None:
    """
    Devuelve {"category_id", "category_name"}. Jerarquía de fuentes:
      1. categorias_ml  (curada por SKU — id + nombre listos, sin llamar a ML)
      2. costos_finales.ml_cat_id  (fallback; resuelve el nombre contra ML)
      3. domain_discovery de MELI con el título nuevo  (fallback final)
    """
    # 1. Categoría curada (preferida)
    curada = await asyncio.to_thread(_categoria_curada, sku)
    if curada:
        return curada

    # 2. ml_cat_id de costos_finales
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
            # 3. domain_discovery con el título
            token = await asyncio.to_thread(meli._access_token, None)
            if not token:
                return None
            r = await cli.get(
                f"/sites/{settings.ml_site_id}/domain_discovery/search",
                params={"limit": 3, "q": titulo},  # limit=1 devuelve [] en ML (bug); 3 sí trae
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


async def _estado_wc(wc_id: int, status: str) -> None:
    # Los status custom (inprogress/ready) y pending solo entran por la batch
    # API (el PUT normal los rechaza). Mismo truco que el pipeline original.
    async with woocommerce._client() as cli:
        r = await cli.post(
            "/products/batch",
            json={"update": [{"id": wc_id, "status": status}]},
            timeout=60.0,
        )
        r.raise_for_status()


def _fmt(v: Any) -> str | None:
    if v in (None, ""):
        return None
    return f"{float(v):.2f}"


# ── Paso 8: Atributos ML con IA (Claude) ────────────────────────────────────────

MARCA_FIJA = "Ferrahome"  # BRAND siempre nuestra, nunca la del proveedor


async def atributos_ml(cat_id: str | None, titulo: str, sku: str,
                       scrape: dict[str, Any]) -> dict[str, str]:
    """
    Genera los atributos de Mercado Libre (PRINCIPALES + SECUNDARIOS) de la
    categoría con IA, usando el servicio canónico `ml_atributos` (DeepSeek). Devuelve
    { "BRAND": "Ferrahome", "MODEL": ..., "COLOR": ... } con IDs de ML como clave.
    {} si no hay categoría o falla.
    """
    if not cat_id:
        return {}
    from services import ml_atributos
    specs = scrape.get("specs") or {}
    caracteristicas = (
        scrape.get("caracteristicas_clave")
        or (json.dumps(specs, ensure_ascii=False)[:1500] if specs else "")
    )
    try:
        r = await ml_atributos.generar_atributos(
            cat_id=cat_id,
            nombre=titulo,
            alibaba_titulo=scrape.get("titulo") or "",
            atributos_actuales="",
            caracteristicas_clave=caracteristicas,
            sku=sku,
        )
        return r.get("atributos", {})
    except Exception as exc:  # noqa: BLE001
        log.warning("atributos ML para %s falló: %s", sku, exc)
        return {}


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
            _set(sku, "procesando", "4/6 · Categoría de Mercado Libre…", wc_id=wc_id)
            cat = await categoria_ml(sku, titulo)
            # Si el SKU no tiene precio en costos_finales, se calcula aquí desde
            # costos_validados + la comisión ML de la categoría (y se persiste + logea).
            cat_id_ml = cat.get("category_id") if cat else ""
            await asyncio.to_thread(costos.asegurar_finales, sku, cat_id_ml)
            dinero = await asyncio.to_thread(datos_dinero, sku)

            # Paso 8: atributos ML con IA (BRAND/MODEL/COLOR…)
            _set(sku, "procesando", "5/6 · Atributos de Mercado Libre (IA)…", wc_id=wc_id)
            atributos = await atributos_ml(cat.get("category_id") if cat else None,
                                           titulo, sku, scrape)

            _set(sku, "procesando", "6/6 · Actualizando WooCommerce…", wc_id=wc_id)
            meta = [
                {"key": "url_alibaba", "value": url},
                {"key": "alibaba_title_original", "value": scrape["titulo"]},
            ]
            if scrape.get("precio_min") is not None:
                meta.append({"key": "alibaba_price", "value": str(scrape["precio_min"])})
            if cat:
                meta.append({"key": "ml_category_id", "value": cat["category_id"]})
                meta.append({"key": "ml_category_name", "value": cat["category_name"]})
            if dinero.get("costo_unitario") is not None:
                meta.append({"key": "costo", "value": _fmt(dinero["costo_unitario"])})
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
            # Categoría ML → categoría de WooCommerce (reemplaza el departamento).
            if cat and cat.get("category_name"):
                async with woocommerce._client() as _c:
                    wc_cat_id = await get_or_create_wc_categoria(
                        _c, cat["category_name"], cat.get("category_id", ""))
                if wc_cat_id:
                    payload["categories"] = [{"id": wc_cat_id}]
            if _fmt(dinero.get("precio_base")):
                payload["regular_price"] = _fmt(dinero["precio_base"])
            if _fmt(dinero.get("precio_sugerido")):
                payload["sale_price"] = _fmt(dinero["precio_sugerido"])
            # Peso/dims: primero costos_finales/validados; si faltan, lo scrapeado
            # de Alibaba (paso 6). El SKU real gana sobre lo scrapeado.
            sd = scrape.get("dims_cm") or {}
            peso = dinero.get("peso") or scrape.get("peso_kg")
            largo = dinero.get("largo") or sd.get("largo")
            ancho = dinero.get("ancho") or sd.get("ancho")
            alto = dinero.get("alto") or sd.get("alto")
            if peso:
                payload["weight"] = str(peso)
            if largo and ancho and alto:
                payload["dimensions"] = {
                    "length": str(largo), "width": str(ancho), "height": str(alto),
                }
            # metas de trazabilidad del scrape (precio Alibaba, CBM, unidades)
            if scrape.get("precio_min") is not None:
                meta.append({"key": "alibaba_precio_min", "value": str(scrape["precio_min"])})
            if scrape.get("precio_max") is not None:
                meta.append({"key": "alibaba_precio_max", "value": str(scrape["precio_max"])})
            if scrape.get("cbm") is not None:
                meta.append({"key": "cbm_producto", "value": str(scrape["cbm"])})
            if scrape.get("unidades_venta"):
                meta.append({"key": "alibaba_unidades_venta", "value": str(scrape["unidades_venta"])})
            # Atributos ML → metas `ml_attr_<ID>` (lo que LEE el publisher en
            # construir_prod) + un `ml_atributos` JSON de respaldo/trazabilidad.
            if atributos:
                for _aid, _aval in atributos.items():
                    meta.append({"key": f"ml_attr_{_aid}", "value": str(_aval)})
                meta.append({"key": "ml_atributos", "value": json.dumps(atributos, ensure_ascii=False)})
            payload["meta_data"] = meta

            await _actualizar_wc(wc_id, payload)

            # Paso 9: completitud → pending (100%) o inprogress (parcial).
            tiene_precio = bool(_fmt(dinero.get("precio_sugerido")) or _fmt(dinero.get("precio_base")))
            tiene_imgs = bool(imagenes)
            tiene_attrs = len(atributos) >= 2  # BRAND + al menos 1 más
            completo = bool(cat) and tiene_precio and tiene_imgs and tiene_attrs
            status_final = "pending" if completo else "inprogress"
            await _estado_wc(wc_id, status_final)

            faltan = []
            if not cat: faltan.append("categoría")
            if not tiene_precio: faltan.append("precio")
            if not tiene_imgs: faltan.append("imágenes")
            if not tiene_attrs: faltan.append("atributos")
            resumen = (f"{len(imagenes)} imgs · {len(atributos)} atributos · "
                       f"{('categoría ' + cat['category_id']) if cat else 'sin categoría'}")
            resumen += f" → {status_final.upper()}"
            if faltan:
                resumen += f" (falta: {', '.join(faltan)})"
            _set(sku, "completado", resumen, wc_id=wc_id, titulo=titulo, status_wc=status_final)
        except Exception as exc:  # noqa: BLE001
            log.exception("crear[%s] falló", sku)
            _set(sku, "error", str(exc)[:200], wc_id=wc_id)

    # El producto pasó a inprogress: sale de Crear Productos al instante (sin
    # re-escanear). La vista Omnicanal lo mostrará cuando su cache expire.
    if wc_id and _progreso.get(sku, {}).get("estado") == "completado":
        woocommerce.quitar_de_drafts(wc_id)
