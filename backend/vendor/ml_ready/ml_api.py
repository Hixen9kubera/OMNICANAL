"""
ml_api.py — Wrapper para la API de MercadoLibre

VENDORIZADO desde publicaciones_ready/ml_api.py — el pipeline con el que se
lograron 1200+ publicaciones. Todas las funciones HTTP quedan intactas.

Único cambio respecto al original: la capa de tokens. El original traía el
`client_secret` escrito en el código y leía los tokens de un JSON. Aquí se
inyectan desde `services.meli` (tabla `ml_tokens`, cifrados con Fernet) vía
`configurar_tokens()`, para no versionar secretos.
"""
import json
import requests

ML_API_BASE = "https://api.mercadolibre.com"

# Proveedores de token inyectados por services/publicar_ready.py
_get_token_fn = None      # (cuenta) -> str | None
_refresh_token_fn = None  # (cuenta) -> str | None


def configurar_tokens(get_token_fn, refresh_token_fn) -> None:
    """Inyecta el acceso a `ml_tokens` sin acoplar este módulo a la BD."""
    global _get_token_fn, _refresh_token_fn
    _get_token_fn = get_token_fn
    _refresh_token_fn = refresh_token_fn


def refresh_token(cuenta: str) -> str:
    """Refresca el access_token de la cuenta vía el proveedor inyectado."""
    if _refresh_token_fn is None:
        raise RuntimeError("ml_api sin configurar: llama configurar_tokens() primero.")
    nuevo = _refresh_token_fn(cuenta)
    if not nuevo:
        raise RuntimeError(f"No se pudo refrescar el token de {cuenta}")
    print(f"  [ml_api] Token de {cuenta} refrescado OK")
    return nuevo


def get_token(cuenta: str, auto_refresh: bool = True) -> str:
    """
    Lee el access_token de la cuenta indicada.
    Si auto_refresh=True, intenta refrescarlo automáticamente si está expirado (401).
    """
    if _get_token_fn is None:
        raise RuntimeError("ml_api sin configurar: llama configurar_tokens() primero.")
    token = _get_token_fn(cuenta)
    if not token:
        return refresh_token(cuenta)

    if auto_refresh:
        # Verificar si el token es válido con un ping barato
        resp = requests.get(
            f"{ML_API_BASE}/users/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        if resp.status_code == 401:
            print(f"  [ml_api] Token de {cuenta} expirado — refrescando...")
            token = refresh_token(cuenta)

    return token


def get_category_info(category_id: str, token: str) -> dict:
    """Retorna la info completa de una categoría (incluye settings.catalog_domain)."""
    resp = requests.get(
        f"{ML_API_BASE}/categories/{category_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15
    )
    if resp.status_code == 200:
        return resp.json()
    return {}


def get_category_attributes(category_id: str, token: str) -> list:
    """
    Retorna la lista de atributos de una categoría ML.
    Incluye cuáles son obligatorios (tags.required = True).
    """
    resp = requests.get(
        f"{ML_API_BASE}/categories/{category_id}/attributes",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15
    )
    if resp.status_code == 200:
        return resp.json()
    print(f"  [ml_api] Error obteniendo atributos de {category_id}: {resp.status_code}")
    return []


def get_category_sale_terms(category_id: str, token: str) -> list:
    """
    Retorna la lista de sale_terms válidos para una categoría ML.
    Cada sale_term incluye id, name, value_type y values (con value_id).
    """
    resp = requests.get(
        f"{ML_API_BASE}/categories/{category_id}/sale_terms",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15
    )
    if resp.status_code == 200:
        return resp.json()
    print(f"  [ml_api] Error obteniendo sale_terms de {category_id}: {resp.status_code}")
    return []



def search_gtin_in_catalog(category_id: str, title: str, token: str) -> str | None:
    """
    Busca un producto similar en el catálogo ML por título+categoría.
    Retorna el primer GTIN encontrado, o None.
    """
    try:
        resp = requests.get(
            f"{ML_API_BASE}/sites/MLM/search",
            params={'category': category_id, 'q': title[:80], 'limit': 5},
            headers={'Authorization': f'Bearer {token}'},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        results = resp.json().get('results', [])
        for item in results:
            for attr in item.get('attributes', []):
                if attr.get('id') == 'GTIN' and attr.get('value_name') not in (None, '', '0000000000000'):
                    return attr['value_name']
    except Exception:
        pass
    return None


def search_gtin_upc(brand: str, query: str) -> str | None:
    """
    Busca GTIN en UPC Item DB por query (título genérico o modelo).
    Retorna el primer EAN/UPC encontrado, o None.
    """
    try:
        query = f"{brand} {query}".strip() if brand else query.strip()
        resp = requests.get(
            'https://api.upcitemdb.com/prod/trial/search',
            params={'s': query, 'match_mode': 0, 'type': 'product'},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        items = resp.json().get('items', [])
        for item in items:
            for ean in item.get('ean', []):
                if ean and ean != '0000000000000':
                    return ean
            for upc in item.get('upc', []):
                if upc and upc != '000000000000':
                    return upc
    except Exception:
        pass
    return None



def _ensure_min_size(image_bytes: bytes, min_long: int = 500, min_short: int = 250) -> bytes:
    """
    Garantiza que la imagen cumpla el mínimo de ML (long >= 500, short >= 250).
    Estrategia:
      1. Si ya cumple → devolver tal cual (convertido a JPEG si es webp/png).
      2. Si lado largo < min_long: escalar proporcionalmente.
      3. Si después de escalar el lado corto sigue < min_short:
         hacer PAD con fondo blanco para llegar al mínimo (no deforma).
    Siempre devuelve JPEG RGB válido.
    """
    from PIL import Image
    import io
    try:
        img = Image.open(io.BytesIO(image_bytes))
        # Convertir a RGB primero (JPEG no acepta RGBA/P/LA)
        if img.mode in ('RGBA', 'P', 'LA'):
            bg = Image.new('RGB', img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = bg
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        w, h = img.size
        long_side = max(w, h)
        short_side = min(w, h)

        # Escalar si lado largo < min_long
        if long_side < min_long:
            scale = min_long / long_side
            new_w = max(int(w * scale) + 1, 1)
            new_h = max(int(h * scale) + 1, 1)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            print(f"    [img] Escalada de {w}x{h} -> {new_w}x{new_h}")
            w, h = new_w, new_h
            short_side = min(w, h)

        # Pad si el lado corto sigue < min_short
        if short_side < min_short:
            tgt_w = max(w, min_short)
            tgt_h = max(h, min_short)
            canvas = Image.new('RGB', (tgt_w, tgt_h), (255, 255, 255))
            canvas.paste(img, ((tgt_w - w) // 2, (tgt_h - h) // 2))
            img = canvas
            print(f"    [img] Padding a {tgt_w}x{tgt_h} (lado corto < {min_short})")

        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=90)
        return buf.getvalue()
    except Exception as e:
        print(f"    [img] No se pudo procesar: {e}")
        return image_bytes


def preupload_picture(image_url: str, token: str) -> str | None:
    """
    Pre-sube una imagen a ML descargandola de la URL y subiendola directamente.
    Si la imagen es menor a 500px, la escala antes de subir.
    Retorna el picture_id de ML, o None si falla (ML acepta URL fallback igual).

    Retry corto: 3 intentos con backoff 2s/4s. Si WC sigue saturado caemos rápido
    al fallback URL y dejamos que ML descargue directamente — más rápido que esperar
    150s en retries inútiles que terminan en el mismo fallback.
    """
    import time as _time
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://chunche.shop/',
        'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
    }
    img_resp = None
    last_status = None
    for attempt in range(3):
        try:
            img_resp = requests.get(image_url, timeout=20, headers=headers)
            last_status = img_resp.status_code
        except Exception as e:
            last_status = f'exc:{type(e).__name__}'
            _time.sleep(1 + attempt)
            continue
        if img_resp.status_code == 200:
            break
        if img_resp.status_code == 429:
            retry_after = img_resp.headers.get('Retry-After')
            wait = int(retry_after) if (retry_after or '').isdigit() else (2 + attempt * 2)
            _time.sleep(min(wait, 10))  # cap 10s — no esperar más, mejor fallback URL
            continue
        if img_resp.status_code in (500, 502, 503, 504):
            _time.sleep(1 + attempt)
            continue
        break  # 403, 404, etc.: no insistir
    if img_resp is None or img_resp.status_code != 200:
        print(f"    [preupload] fallo descarga (status={last_status}) — caera a URL fallback")
        return None
    try:
        image_data = _ensure_min_size(img_resp.content)
        resp = requests.post(
            f"{ML_API_BASE}/pictures",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("image.jpg", image_data, "image/jpeg")},
            timeout=60
        )
        if resp.status_code == 201:
            return resp.json().get('id')
        print(f"    [preupload] ML rechazo upload: HTTP {resp.status_code} {resp.text[:120]}")
        return None
    except Exception as e:
        print(f"    [preupload] excepcion en upload: {e}")
        return None


def preupload_picture_from_bytes(
    image_bytes: bytes,
    token: str,
    filename: str = "image.jpg",
    mime: str = "image/jpeg",
) -> str | None:
    """
    Pre-sube bytes directamente a ML (sin archivo local).
    Si la imagen es menor a 500px, la escala antes de subir.
    Retorna el picture_id de ML, o None si falla.
    """
    try:
        image_data = _ensure_min_size(image_bytes)
        resp = requests.post(
            f"{ML_API_BASE}/pictures",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (filename, image_data, mime)},
            timeout=60
        )
        if resp.status_code == 201:
            return resp.json().get('id')
        return None
    except Exception:
        return None


def create_item(payload: dict, token: str) -> tuple[dict, int]:
    """
    Crea un item en MercadoLibre.
    Retorna (respuesta_json, status_code).
    El item se crea en status 'paused' para revisión antes de publicar.
    """
    resp = requests.post(
        f"{ML_API_BASE}/items",
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        timeout=30
    )
    try:
        return resp.json(), resp.status_code
    except Exception:
        return {"error": resp.text}, resp.status_code


def upload_pictures(item_id: str, image_urls: list, token: str) -> tuple[dict, int]:
    """
    Sube/actualiza las imágenes de un item existente via PUT.
    image_urls: lista de URLs de imágenes.
    Retorna (respuesta_json, status_code).
    """
    pictures = [{'source': url} for url in image_urls]
    resp = requests.put(
        f"{ML_API_BASE}/items/{item_id}",
        json={'pictures': pictures},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        timeout=30
    )
    try:
        return resp.json(), resp.status_code
    except Exception:
        return {"error": resp.text}, resp.status_code


def update_description(item_id: str, plain_text: str, token: str) -> int:
    """
    Actualiza (o crea) la descripción de un item existente.
    Usa PUT si ya existe, POST si es nuevo.
    Retorna el status_code de la respuesta.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    body = {"plain_text": plain_text}
    url  = f"{ML_API_BASE}/items/{item_id}/description"

    # Intentar PUT (actualizar)
    resp = requests.put(url, json=body, headers=headers, timeout=20)
    if resp.status_code in (200, 201):
        return resp.status_code

    # Si 404, intentar POST (crear por primera vez)
    if resp.status_code == 404:
        resp = requests.post(url, json=body, headers=headers, timeout=20)

    return resp.status_code


def pause_item(item_id: str, token: str) -> int:
    """Pausa un item activo."""
    try:
        resp = requests.put(
            f"{ML_API_BASE}/items/{item_id}",
            json={"status": "paused"},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=30
        )
        return resp.status_code
    except requests.exceptions.Timeout:
        return -1  # timeout — no crashear, el item ya fue creado


def activate_item(item_id: str, token: str) -> int:
    """Activa (publica) un item pausado."""
    resp = requests.put(
        f"{ML_API_BASE}/items/{item_id}",
        json={"status": "active"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=15
    )
    return resp.status_code