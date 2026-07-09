"""
publisher_core.py — Núcleo de publicación en Mercado Libre.

VENDORIZADO desde publicaciones_ready/publisher.py — el pipeline con el que se
lograron 1200+ publicaciones. `build_sale_terms`, `build_payload` y toda la
cadena de reintentos de `publish_product` están copiadas línea por línea.

Diferencias respecto al original, y solo estas:

  1. El original hacía `db.set_credentials(...)` + `sys.exit(1)` al importarse.
     Aquí no hay efectos al importar: FastAPI moriría.
  2. `save_backlog()` y `save_gtin_to_wc()` eran llamadas directas a su `db.py`
     (mysql.connector) y a su `wc_api.py` (WooCommerce REST, hoy bloqueado por
     el CDN con 403). Aquí son ganchos inyectables — ver `configurar()`.
  3. `print` está sombreado por una versión que no revienta con ✓/✗/─ en
     consolas Windows y que además manda todo al logger.

Las constantes vienen de publicaciones_ready/config.py sin cambios.
"""
from __future__ import annotations

import builtins
import logging
import time
from datetime import datetime
from typing import Any, Callable

from . import ml_api
from .attribute_mapper import build_attributes, build_secondary_attributes

log = logging.getLogger(__name__)


def print(*args: Any, **kwargs: Any) -> None:  # noqa: A001 - sombreado deliberado
    """`print` de su código → logger, sin romper por unicode en Windows."""
    texto = " ".join(str(a) for a in args)
    try:
        log.info(texto)
    except Exception:  # noqa: BLE001
        builtins.print(texto.encode("ascii", "replace").decode())


# ── Constantes (publicaciones_ready/config.py, sin cambios) ──────────────────
MAX_IMAGENES = 10
DEFAULT_CURRENCY = "MXN"
DEFAULT_LISTING_TYPE = "gold_pro"      # Premium
DEFAULT_CONDITION = "new"
DEFAULT_BUYING_MODE = "buy_it_now"
DEFAULT_QUANTITY = 1
DEFAULT_BRAND = "Ferrahome"
FREE_SHIPPING_MIN = 149.0

ML_CUENTAS = ["SANCORFASHION", "BEKURA"]


# ── Ganchos inyectables (reemplazan db.py / wc_api.py del original) ──────────
_save_backlog_fn: Callable[[str, dict], None] | None = None
_save_gtin_fn: Callable[[int, str], bool] | None = None


def configurar(save_backlog_fn=None, save_gtin_fn=None) -> None:
    """Inyecta persistencia de backlog y guardado de GTIN en WooCommerce."""
    global _save_backlog_fn, _save_gtin_fn
    if save_backlog_fn is not None:
        _save_backlog_fn = save_backlog_fn
    if save_gtin_fn is not None:
        _save_gtin_fn = save_gtin_fn


def save_backlog(sku: str, entry: dict) -> None:
    if _save_backlog_fn is None:
        return
    try:
        _save_backlog_fn(sku, entry)
    except Exception as exc:  # noqa: BLE001
        log.warning("save_backlog falló (%s): %s", sku, exc)


def save_gtin_to_wc(wc_id: int, gtin: str) -> bool:
    """WooCommerce REST está devolviendo 403 (CDN); sin gancho, no-op."""
    if _save_gtin_fn is None:
        return False
    try:
        return bool(_save_gtin_fn(wc_id, gtin))
    except Exception as exc:  # noqa: BLE001
        log.warning("save_gtin_to_wc falló (%s): %s", wc_id, exc)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# CACHÉ DE ATRIBUTOS ML (para no llamar la API por cada producto)
# ══════════════════════════════════════════════════════════════════════════════

_attr_cache: dict[str, list] = {}
_sale_terms_cache: dict[str, list] = {}


def get_category_attrs_cached(category_id: str, token: str) -> list:
    if category_id not in _attr_cache:
        attrs = ml_api.get_category_attributes(category_id, token)
        _attr_cache[category_id] = attrs
    return _attr_cache[category_id]


def get_sale_terms_cached(category_id: str, token: str) -> list:
    if category_id not in _sale_terms_cache:
        terms = ml_api.get_category_sale_terms(category_id, token)
        _sale_terms_cache[category_id] = terms
    return _sale_terms_cache[category_id]


def warranty_days_for_sku(sku: str) -> int:
    """Política Kubera: 15 días para ropa/calzado (ROP-*, CALZ-*), 30 días el resto."""
    s = (sku or '').upper()
    return 15 if (s.startswith('ROP-') or s.startswith('CALZ-')) else 30


def build_sale_terms(category_id: str, token: str, sku: str = '') -> list:
    """
    Construye la lista de sale_terms usando value_id del API de ML.
    Política de garantía: 15 días para ROP-/CALZ-, 30 días para el resto.
    Fallback a IDs conocidos si el API no responde.
    """
    WARRANTY_TYPE_SELLER = '6150835'   # "Garantía del vendedor"
    days = warranty_days_for_sku(sku)
    warranty_time_value = f'{days} días'

    terms = get_sale_terms_cached(category_id, token)
    sale_terms = []

    # WARRANTY_TYPE — requiere value_id obligatorio
    wt = next((t for t in terms if t.get('id') == 'WARRANTY_TYPE'), None)
    if wt:
        seller_val = None
        for v in wt.get('values', []):
            vname = (v.get('name') or '').lower()
            if 'vendedor' in vname or 'seller' in vname:
                seller_val = v.get('id')
                break
        if not seller_val and wt.get('values'):
            seller_val = wt['values'][0].get('id')
        sale_terms.append({'id': 'WARRANTY_TYPE', 'value_id': seller_val or WARRANTY_TYPE_SELLER})
    else:
        sale_terms.append({'id': 'WARRANTY_TYPE', 'value_id': WARRANTY_TYPE_SELLER})

    # WARRANTY_TIME — preferir value_id si la categoría tiene un value que coincida
    # con los días objetivo; si no, mandar value_name como texto libre.
    wtime = next((t for t in terms if t.get('id') == 'WARRANTY_TIME'), None)
    if wtime and wtime.get('values'):
        time_val = None
        for v in wtime.get('values', []):
            vname = (v.get('name') or '').lower()
            if str(days) in vname and ('día' in vname or 'dia' in vname):
                time_val = v.get('id')
                break
        if time_val:
            sale_terms.append({'id': 'WARRANTY_TIME', 'value_id': time_val})
        else:
            sale_terms.append({'id': 'WARRANTY_TIME', 'value_name': warranty_time_value})
    else:
        sale_terms.append({'id': 'WARRANTY_TIME', 'value_name': warranty_time_value})

    return sale_terms


# ══════════════════════════════════════════════════════════════════════════════
# CONSTRUCCIÓN DEL PAYLOAD
# ══════════════════════════════════════════════════════════════════════════════

def build_payload(prod: dict, token: str, dry_run: bool = False, cuenta: str = '') -> dict | None:
    """
    Construye el payload para POST /items de ML.

    Reglas obligatorias:
    - SIN description (se sube en paso separado)
    - listing_type_id: gold_pro (Premium)
    - shipping.mode: me2
    - free_shipping: True si precio > $149 MXN
    - BRAND: siempre Ferrahome
    - CONDITION: siempre Nuevo
    - SELLER_SKU: SKU del producto
    - sale_terms: garantía del vendedor 30 días
    """
    # Resolver categoria ML usando la categoria WC como fuente de verdad.
    cached_ml_id = prod.get('ml_category_id', '') or ''
    try:
        from .wc_category_mapping import resolve_ml_category_from_wc
        new_ml_id, motivo = resolve_ml_category_from_wc(prod.get('wc_categories', []), cached_ml_id)
    except Exception as _e:
        new_ml_id, motivo = (None, f'error:{_e}')
    if new_ml_id and motivo == 'override':
        wc_cat_name = (prod.get('wc_categories') or [{}])[0].get('name', '?')
        print(f"  [cat] WC '{wc_cat_name}' indica {new_ml_id} (override de meta {cached_ml_id!r} = '{prod.get('ml_category_name','')}')")
        category_id = new_ml_id
    else:
        category_id = cached_ml_id
        if motivo not in ('same',):
            print(f"  [cat] usando meta ml_category_id={category_id} (wc_resolve={motivo})")
    if not category_id:
        print(f"  [!] Sin ml_category_id — saltando {prod['sku']}")
        return None

    if not prod['title']:
        print(f"  [!] Sin título — saltando {prod['sku']}")
        return None

    if prod['price'] <= 0:
        print(f"  [!] Precio inválido ({prod['price']}) — saltando {prod['sku']}")
        return None

    # Stock
    stock = int(prod['stock']) if prod['stock'] else DEFAULT_QUANTITY

    # Detectar si la categoría requiere catálogo (tiene catalog_domain)
    cat_info = ml_api.get_category_info(category_id, token)
    is_catalog_category = bool(cat_info.get('settings', {}).get('catalog_domain'))
    if is_catalog_category:
        print(f"  [cat] Categoría con catalog_domain — usando family_name, omitiendo title")

    # Atributos de categoría + atributos del producto
    ml_category_attrs = get_category_attrs_cached(category_id, token)
    extra_attrs = build_attributes(prod['ml_attrs'], ml_category_attrs, prod.get('wc_attrs', {}))

    # Atributos obligatorios fijos
    fixed_ids = {a['id'] for a in extra_attrs}
    attributes = []

    if 'BRAND' not in fixed_ids:
        attributes.append({'id': 'BRAND', 'value_name': DEFAULT_BRAND})
    # CONDITION va en top-level (condition: new), no en attributes

    attributes.append({'id': 'SELLER_SKU', 'value_name': prod['sku']})
    attributes.extend(extra_attrs)

    def _attr_ids():
        return {a['id'] for a in attributes}

    # MODEL: requerido en muchas categorías — usar el valor de ml_attrs si existe, si no el título
    if 'MODEL' not in _attr_ids():
        model_val = prod['ml_attrs'].get('model') or prod['ml_attrs'].get('modelo') or prod['title'][:60]
        attributes.append({'id': 'MODEL', 'value_name': model_val})

    # PART_NUMBER: requerido en algunas categorías — usar SKU como fallback
    if 'PART_NUMBER' not in _attr_ids():
        attributes.append({'id': 'PART_NUMBER', 'value_name': prod['sku']})

    # MANUFACTURER: requerido en algunas categorías — usar valor de BRAND como fallback
    if 'MANUFACTURER' not in _attr_ids():
        cat_has_manufacturer = any(a.get('id') == 'MANUFACTURER' for a in ml_category_attrs)
        if cat_has_manufacturer:
            brand_val = next((a.get('value_name', '') for a in attributes if a.get('id') == 'BRAND'), DEFAULT_BRAND) or DEFAULT_BRAND
            attributes.append({'id': 'MANUFACTURER', 'value_name': brand_val})

    # GTIN: incluir si el producto tiene uno en _barcode (campo manual WC), ml_attrs o _gtin.
    if 'GTIN' not in _attr_ids():
        gtin_val = (prod['meta'].get('_barcode') or prod['ml_attrs'].get('gtin')
                    or prod['ml_attrs'].get('ean') or prod['ml_attrs'].get('upc')
                    or prod['meta'].get('_gtin'))
        if gtin_val:
            attributes.append({'id': 'GTIN', 'value_name': str(gtin_val)})
    if 'EMPTY_GTIN_REASON' not in _attr_ids():
        attributes.append({'id': 'EMPTY_GTIN_REASON', 'value_id': '17055161', 'value_name': 'Otra razón'})

    # Dimensiones de paquete — ML requiere enteros con unidad: "33 cm" / "600 g"
    _w = float(prod['weight']) if prod.get('weight') else 0
    _l = float(prod['length']) if prod.get('length') else 0
    _wi = float(prod['width'])  if prod.get('width')  else 0
    _h = float(prod['height']) if prod.get('height') else 0
    _dims_ok = _l > 0 and _wi > 0 and _h > 0
    # Densidad mínima: 0.001 g/cm³ (objetos muy livianos) y máxima 30 g/cm³ (metal sólido)
    if _dims_ok and _w > 0:
        _vol = _l * _wi * _h
        _density = (_w * 1000) / _vol
        _dims_ok = 0.001 <= _density <= 30
        if not _dims_ok:
            print(f"  [!] Dims paquete omitidas (densidad {_density:.2f} g/cm3 fuera de rango): {_l}x{_wi}x{_h} cm / {_w} kg")
    # ML requiere las 4 dimensiones de paquete juntas o ninguna
    if _dims_ok and _w > 0:
        _w_g = max(1, int(round(_w * 1000)))
        _l_i = max(1, int(round(_l)))
        _wi_i = max(1, int(round(_wi)))
        _h_i = max(1, int(round(_h)))
        attributes.append({'id': 'SELLER_PACKAGE_WEIGHT', 'value_name': f"{_w_g} g"})
        attributes.append({'id': 'SELLER_PACKAGE_LENGTH', 'value_name': f"{_l_i} cm"})
        attributes.append({'id': 'SELLER_PACKAGE_WIDTH',  'value_name': f"{_wi_i} cm"})
        attributes.append({'id': 'SELLER_PACKAGE_HEIGHT', 'value_name': f"{_h_i} cm"})

    # SIZE_GRID_ID: si la categoría tiene catalog_domain de calzado/ropa,
    # buscar chart_id en size_chart_mapping según (cuenta, domain, gender).
    if cuenta:
        domain = (cat_info.get('settings', {}) or {}).get('catalog_domain', '') or ''
        domain = domain.replace('MLM-', '')
        if domain:
            gender = (prod['ml_attrs'].get('gender')
                      or prod['ml_attrs'].get('GENDER')
                      or prod.get('wc_attrs', {}).get('gender', ''))
            if isinstance(gender, list) and gender:
                gender = gender[0]
            gender = str(gender).strip().strip("[]'\" ")
            try:
                from .size_chart_mapping import get_chart_id
                chart_id = get_chart_id(cuenta, domain, gender)
            except Exception as _e:
                chart_id = None
                print(f"  [size-chart] error cargando mapping: {_e}")
            if chart_id and 'SIZE_GRID_ID' not in _attr_ids():
                attributes.append({'id': 'SIZE_GRID_ID', 'value_id': chart_id})
                print(f"  [size-chart] {domain}/{gender} -> SIZE_GRID_ID={chart_id}")

    # DEPTH: requerido en algunas categorías — usar prod['length'] como fallback
    if 'DEPTH' not in _attr_ids() and _l > 0:
        cat_has_depth = any(a.get('id') == 'DEPTH' for a in ml_category_attrs)
        if cat_has_depth:
            attributes.append({'id': 'DEPTH', 'value_name': f"{_l} cm"})

    # Características secundarias — atributos opcionales de la categoría
    existing_ids = {a['id'] for a in attributes}
    secondary = build_secondary_attributes(prod, ml_category_attrs, existing_ids)
    if secondary:
        print(f"  Caracteristicas secundarias: {len(secondary)} atributo(s) encontrado(s)")
    attributes.extend(secondary)

    # Shipping — free_shipping si precio > $149
    free_shipping = prod['price'] > FREE_SHIPPING_MIN

    # Pre-subir imágenes a ML para obtener picture_ids (más rápido que URLs externas)
    raw_images = (prod.get('images_for_ml') or prod['images'])[:MAX_IMAGENES]
    picture_ids = []
    if raw_images and not dry_run:
        print(f"  Pre-subiendo {len(raw_images)} imagenes a ML...")
        for i, url in enumerate(raw_images, 1):
            pid = ml_api.preupload_picture(url, token)
            if pid:
                picture_ids.append({'id': pid})
                print(f"  [ok] Imagen {i}/{len(raw_images)} -> {pid}")
            else:
                picture_ids.append({'source': url})
                print(f"  [!] Imagen {i}/{len(raw_images)} fallo pre-upload, usando URL")
    elif raw_images:
        picture_ids = [{'source': url} for url in raw_images]

    payload = {
        'category_id':        category_id,
        'price':              prod['price'],
        'currency_id':        DEFAULT_CURRENCY,
        'available_quantity': stock,
        'buying_mode':        DEFAULT_BUYING_MODE,
        'listing_type_id':    DEFAULT_LISTING_TYPE,
        'condition':          DEFAULT_CONDITION,
        'status':             'paused',
        'pictures':           picture_ids,
        'attributes':         attributes,
        'sale_terms': build_sale_terms(category_id, token, prod.get('sku', '')),
        'shipping': {
            'mode':           'me2',
            'local_pick_up':  False,
            'free_shipping':  free_shipping,
        },
    }

    # Categorías con catalog_domain: title NO permitido, usar family_name
    if is_catalog_category:
        payload['family_name'] = prod['title'][:60]
    else:
        payload['title'] = prod['title']

    return payload


# ══════════════════════════════════════════════════════════════════════════════
# PUBLICAR UN PRODUCTO
# ══════════════════════════════════════════════════════════════════════════════

def publish_product(prod: dict, token: str, dry_run: bool = False, cuenta: str = '') -> dict:
    """
    Publica un producto en ML.
    Retorna dict con resultado: {success, ml_item_id, error}
    """
    sku = prod['sku']
    backlog_key = f"{cuenta}:{sku}" if cuenta else sku
    print(f"  Cuenta:   {cuenta or '-'}")
    print(f"  SKU:      {sku}")
    print(f"  Titulo:   {prod['title'][:70]}")
    print(f"  Precio:   ${prod['price']:,.2f} MXN")
    print(f"  Cat ML:   {prod['ml_category_id']} ({prod['ml_category_name']})")
    print(f"  Imagenes: {len(prod['images'])}")

    # Construir payload
    payload = build_payload(prod, token, dry_run=dry_run, cuenta=cuenta)
    if payload is None:
        return {'success': False, 'sku': sku, 'error': 'datos_insuficientes'}

    if dry_run:
        print(f"  [DRY RUN] Payload construido OK — no se envía a ML")
        result = {'success': True, 'sku': sku, 'ml_item_id': 'DRY_RUN', 'dry_run': True,
                  'payload': payload}
        return result

    timestamp = datetime.now().isoformat()

    # 1. Crear item
    print(f"  Creando item en ML...")
    response, status_code = ml_api.create_item(payload, token)

    # Retry si token expiró durante la ejecución (401) → refrescar y reintentar
    if status_code == 401:
        print(f"  [!] Token expirado (401) — refrescando token de {cuenta}...")
        try:
            token = ml_api.refresh_token(cuenta)
            print(f"  [ok] Token refrescado — reintentando create_item...")
            response, status_code = ml_api.create_item(payload, token)
        except Exception as e:
            print(f"  [x] No se pudo refrescar token: {e}")

    # Retry si ML devuelve error 5xx (timeout interno, sobrecarga, etc.)
    if status_code >= 500:
        print(f"  [!] Error {status_code} de ML — reintentando en 15s...")
        time.sleep(15)
        response, status_code = ml_api.create_item(payload, token)

    # Retry 1: si ML exige GTIN → _barcode WC → catálogo ML → UPC Item DB → placeholder
    if status_code == 400 and any(
        c.get('code') == 'item.attribute.missing_conditional_required'
        and 'GTIN' in c.get('message', '')
        for c in response.get('cause', [])
    ):
        gtin_found = None

        # Opción 0: _barcode ingresado manualmente en WooCommerce
        gtin_wc = (prod['meta'].get('_barcode') or prod['meta'].get('_gtin') or '').strip()
        if gtin_wc:
            gtin_found = gtin_wc
            print(f"  [gtin] Usando _barcode de WooCommerce: {gtin_found}")

        # Opción 1: buscar en catálogo ML por título+categoría
        if not gtin_found:
            print(f"  [!] GTIN requerido — buscando en catalogo ML...")
            gtin_found = ml_api.search_gtin_in_catalog(prod['ml_category_id'], prod['title'], token)
            if gtin_found:
                print(f"  [gtin] Encontrado en catalogo ML: {gtin_found}")

        # Opción 2: buscar en UPC Item DB por título genérico (sin marca propia)
        if not gtin_found:
            model = prod['ml_attrs'].get('MODEL', '') or prod['meta'].get('modelo', '')
            query = model if model else prod['title']
            print(f"  [!] No encontrado en ML — buscando en UPC Item DB ({query[:60]})...")
            gtin_found = ml_api.search_gtin_upc('', query)
            if gtin_found:
                print(f"  [gtin] Encontrado en UPC Item DB: {gtin_found}")

        # Opción 3: placeholder
        if not gtin_found:
            print(f"  [!] No encontrado — usando placeholder GTIN...")
            gtin_found = '0000000000000'

        # Si encontramos GTIN real, guardarlo en WC para futuros runs
        if gtin_found != '0000000000000':
            if save_gtin_to_wc(prod['wc_id'], gtin_found):
                print(f"  [gtin] Guardado en WooCommerce (_barcode)")

        payload['attributes'] = [a for a in payload['attributes'] if a.get('id') != 'GTIN']
        payload['attributes'].append({'id': 'GTIN', 'value_name': gtin_found})
        response, status_code = ml_api.create_item(payload, token)

    # Retry: SALE_FORMAT=Unidad requiere UNITS_PER_PACK → agregar con valor 1
    if status_code == 400 and any(
        c.get('code') == 'item.attribute.invalid_sale_units'
        for c in response.get('cause', [])
    ):
        print(f"  [!] UNITS_PER_PACK requerido — reintentando con valor 1...")
        payload['attributes'] = [a for a in payload['attributes'] if a.get('id') != 'UNITS_PER_PACK']
        payload['attributes'].append({'id': 'UNITS_PER_PACK', 'value_name': '1'})
        response, status_code = ml_api.create_item(payload, token)

    # Retry: imágenes demasiado pequeñas (<500px) → re-preupload con escalado
    if status_code == 400 and any(
        'item.pictures.invalid_size' in c.get('code', '')
        for c in response.get('cause', [])
    ):
        print(f"  [!] Imagenes rechazadas por tamaño — re-subiendo con escalado automatico...")
        new_pictures = []
        for pic in payload.get('pictures', []):
            if 'id' in pic:
                new_pictures.append(pic)  # ya pre-subida, conservar
            elif 'source' in pic:
                pid = ml_api.preupload_picture(pic['source'], token)
                if pid:
                    new_pictures.append({'id': pid})
                    print(f"    [ok] Re-subida con escalado -> {pid}")
        if new_pictures:
            payload['pictures'] = new_pictures
            response, status_code = ml_api.create_item(payload, token)

    # Retry: título no concuerda con el atributo GENDER
    if status_code == 400 and any(
        c.get('code') == 'invalid.title.gender'
        for c in response.get('cause', [])
    ):
        _removed = [a for a in payload['attributes'] if a.get('id') in ('GENDER', 'GENDER_NAME')]
        if _removed:
            print(f"  [!] Title/gender mismatch — quitando atributo GENDER y reintentando")
            payload['attributes'] = [a for a in payload['attributes']
                                     if a.get('id') not in ('GENDER', 'GENDER_NAME')]
            response, status_code = ml_api.create_item(payload, token)

    # Retry: SIZE_GRID_ID inválido o faltante (categorías de ropa / calzado)
    if status_code == 400 and any(
        c.get('code') in ('invalid.fashion_grid.grid_id.values',
                          'missing.fashion_grid.grid_id.values')
        for c in response.get('cause', [])
    ):
        removed = [a for a in payload['attributes'] if a.get('id') == 'SIZE_GRID_ID']
        if removed:
            print(f"  [!] SIZE_GRID_ID invalido — quitando y reintentando (valor previo: {removed[0].get('value_name')})")
            payload['attributes'] = [a for a in payload['attributes'] if a.get('id') != 'SIZE_GRID_ID']
            response, status_code = ml_api.create_item(payload, token)

    # Retry: atributo de tipo picture con value_name inválido
    if status_code == 400:
        _bad_picture_attrs = set()
        for c in response.get('cause', []):
            if c.get('code') == 'item.attribute.value_name.invalid' and 'type picture' in c.get('message', ''):
                import re as _re
                m = _re.search(r'Attribute (\w+)', c.get('message', ''))
                if m:
                    _bad_picture_attrs.add(m.group(1))
        if _bad_picture_attrs:
            print(f"  [!] Atributos tipo picture con valor invalido — quitando: {_bad_picture_attrs}")
            payload['attributes'] = [a for a in payload['attributes'] if a.get('id') not in _bad_picture_attrs]
            response, status_code = ml_api.create_item(payload, token)

    # Retry: SALE_FORMAT=Pack conflicts con UNITS_PER_PACK=1
    if status_code == 400 and any(
        c.get('code') == 'item.attribute.invalid_sale_units'
        for c in response.get('cause', [])
    ):
        _conflict_ids = {'SALE_FORMAT', 'UNITS_PER_PACK', 'UNITS_PER_PACKAGE'}
        before = [a['id'] for a in payload['attributes'] if a['id'] in _conflict_ids]
        if before:
            print(f"  [!] invalid_sale_units (Pack vs UNITS=1) — quitando {before} para vender como unidad")
            payload['attributes'] = [a for a in payload['attributes'] if a.get('id') not in _conflict_ids]
            response, status_code = ml_api.create_item(payload, token)

    # Retry: dimensiones de paquete inválidas (formato/valor) → defaults
    if status_code == 400 and any(
        'invalid.seller.package.dimensions' in c.get('code', '') or
        'invalid.format.seller.package.dimensions' in c.get('code', '')
        for c in response.get('cause', [])
    ):
        _pkg_ids = {'SELLER_PACKAGE_WEIGHT', 'SELLER_PACKAGE_LENGTH', 'SELLER_PACKAGE_WIDTH', 'SELLER_PACKAGE_HEIGHT'}
        payload['attributes'] = [a for a in payload['attributes'] if a.get('id') not in _pkg_ids]
        payload['attributes'].append({'id': 'SELLER_PACKAGE_WEIGHT', 'value_name': '1000 g'})
        payload['attributes'].append({'id': 'SELLER_PACKAGE_LENGTH', 'value_name': '30 cm'})
        payload['attributes'].append({'id': 'SELLER_PACKAGE_WIDTH',  'value_name': '20 cm'})
        payload['attributes'].append({'id': 'SELLER_PACKAGE_HEIGHT', 'value_name': '15 cm'})
        print(f"  [!] Dims paquete con formato/valor invalido — reintentando con defaults (1kg, 30x20x15cm)...")
        response, status_code = ml_api.create_item(payload, token)

    # Retry: dimensiones de paquete FALTANTES → defaults
    if status_code == 400 and any(
        'missing.seller.package.dimensions' in c.get('code', '')
        for c in response.get('cause', [])
    ):
        _pkg_ids = {'SELLER_PACKAGE_WEIGHT', 'SELLER_PACKAGE_LENGTH', 'SELLER_PACKAGE_WIDTH', 'SELLER_PACKAGE_HEIGHT'}
        payload['attributes'] = [a for a in payload['attributes'] if a.get('id') not in _pkg_ids]
        payload['attributes'].append({'id': 'SELLER_PACKAGE_WEIGHT', 'value_name': '1000 g'})
        payload['attributes'].append({'id': 'SELLER_PACKAGE_LENGTH', 'value_name': '30 cm'})
        payload['attributes'].append({'id': 'SELLER_PACKAGE_WIDTH',  'value_name': '20 cm'})
        payload['attributes'].append({'id': 'SELLER_PACKAGE_HEIGHT', 'value_name': '15 cm'})
        print(f"  [!] Dims paquete requeridas pero no disponibles — reintentando con defaults (1kg, 30x20x15cm)...")
        response, status_code = ml_api.create_item(payload, token)

    # Retry: sale_term WARRANTY_TYPE inválido → obtener value_id correcto del error
    if status_code == 400 and any(
        c.get('code') in ('sale_term.invalid_value_id', 'sale_term.value_id_required')
        for c in response.get('cause', [])
    ):
        print(f"  [!] sale_terms invalidos — corrigiendo value_id y reintentando...")
        for cause in response.get('cause', []):
            msg = cause.get('message', '')
            if 'WARRANTY_TYPE' in msg and 'Allowed values are' in msg:
                import re
                match = re.search(r'\[(\d+)\]', msg)
                if match:
                    correct_id = match.group(1)
                    print(f"  [!] Usando WARRANTY_TYPE value_id={correct_id} del error de ML")
                    for st in payload['sale_terms']:
                        if st['id'] == 'WARRANTY_TYPE':
                            st.pop('value_name', None)
                            st['value_id'] = correct_id
                            break
        for st in payload['sale_terms']:
            if st['id'] == 'WARRANTY_TYPE' and 'value_id' not in st:
                st.pop('value_name', None)
                st['value_id'] = '6150835'
        response, status_code = ml_api.create_item(payload, token)

    # Retry: GTIN con formato inválido (placeholder rechazado)
    _gtin_placeholder_rejected = False
    if status_code == 400 and any(
        'product_identifier.invalid_format' in c.get('code', '')
        for c in response.get('cause', [])
    ):
        _gtin_placeholder_rejected = True
        _saved_gtin = next((a for a in payload['attributes'] if a.get('id') == 'GTIN'), None)
        print(f"  [!] GTIN rechazado por formato invalido — reintentando sin GTIN (solo EMPTY_GTIN_REASON)...")
        payload['attributes'] = [a for a in payload['attributes'] if a.get('id') != 'GTIN']
        if not any(a.get('id') == 'EMPTY_GTIN_REASON' for a in payload['attributes']):
            payload['attributes'].append({'id': 'EMPTY_GTIN_REASON', 'value_id': '17055161', 'value_name': 'Otra razón'})
        response, status_code = ml_api.create_item(payload, token)
        if status_code == 400 and any(
            c.get('code') == 'item.attribute.missing_conditional_required'
            and 'GTIN' in c.get('message', '')
            for c in response.get('cause', [])
        ):
            print(f"  [!] Categoria requiere GTIN obligatorio — se necesita codigo de barras real")
            if _saved_gtin:
                payload['attributes'].append(_saved_gtin)

    if status_code != 201:
        error_msg = response.get('message', str(response))[:200]
        is_gtin_error = _gtin_placeholder_rejected or any(
            'product_identifier.invalid_format' in c.get('code', '') or
            (c.get('code') == 'item.attribute.missing_conditional_required'
             and 'GTIN' in c.get('message', '')) or
            ('GTIN' in c.get('message', '') and 'invalid' in c.get('message', '').lower())
            for c in response.get('cause', [])
        )
        needs_manual = any(
            c.get('code') in ('missing.fashion_grid.grid_id.values',
                              'invalid.fashion_grid.grid_id.values',
                              'shipping.lost_me1_by_user',
                              'invalid.title.gender',
                              'item.pictures.invalid_size')
            for c in response.get('cause', [])
        )
        manual_reasons = []
        for c in response.get('cause', []):
            code = c.get('code', '')
            if code in ('missing.fashion_grid.grid_id.values', 'invalid.fashion_grid.grid_id.values'):
                manual_reasons.append('GRID_REQUERIDO (configurar guía de tallas en ML)')
            elif code == 'shipping.lost_me1_by_user':
                manual_reasons.append('ME1_INACTIVO (activar Mercado Envíos 1 en dashboard ML)')
            elif code == 'invalid.title.gender':
                manual_reasons.append('TITLE_GENDER_MISMATCH (revisar título y atributo GENDER del producto en WC)')
            elif code == 'item.pictures.invalid_size':
                manual_reasons.append('IMAGES_TOO_SMALL (subir imágenes ≥500x250 px al producto en WC)')
        if is_gtin_error:
            print(f"  [x] Error GTIN — la cuenta {cuenta} requiere codigo de barras real para {sku}")
        elif needs_manual:
            print(f"  [x] Requiere configuracion manual en cuenta {cuenta}: {', '.join(manual_reasons)}")
        else:
            print(f"  [x] Error {status_code}: {error_msg}")
        print(f"  Detalle: {response.get('error', '')} | Causes: {response.get('cause', [])}")
        if is_gtin_error:
            error_label = f"GTIN_INVALIDO: cuenta {cuenta} requiere código de barras real"
        elif needs_manual:
            error_label = f"NEEDS_MANUAL_CONFIG: {' | '.join(manual_reasons)}"
        else:
            error_label = f"HTTP {status_code}: {error_msg}"
        result = {'success': False, 'sku': sku, 'error': error_label,
                  'gtin_error': is_gtin_error, 'needs_manual_config': needs_manual,
                  'ml_status': status_code, 'ml_response': response}
        save_backlog(backlog_key, {
            'timestamp':    timestamp,
            'cuenta':       cuenta,
            'wc_id':        prod['wc_id'],
            'payload':      payload,
            'ml_response':  response,
            'ml_status':    status_code,
            'result':       result,
        })
        return result

    ml_item_id = response.get('id', '')
    print(f"  [ok] Item creado: {ml_item_id}")

    # 2. Pausar explícitamente (ML ignora status:paused en categorías de catálogo)
    pause_status = ml_api.pause_item(ml_item_id, token)
    if pause_status == 200:
        print(f"  [ok] Publicacion pausada")
    elif pause_status == -1:
        print(f"  [!] Timeout al pausar — pausar manualmente desde ML")
    else:
        print(f"  [!] No se pudo pausar (HTTP {pause_status}) — quedo activa")

    # 3. Agregar descripción
    desc_status = None
    if prod['description']:
        print(f"  Subiendo descripcion ({len(prod['description'])} chars)...")
        desc_status = ml_api.update_description(ml_item_id, prod['description'], token)
        if desc_status in (200, 201):
            print(f"  [ok] Descripcion actualizada")
        else:
            print(f"  [!] Descripcion fallo (HTTP {desc_status}) — item creado de todas formas")

    ml_url = f"https://articulo.mercadolibre.com.mx/{ml_item_id.replace('MLM', 'MLM-')}"
    result = {
        'success':      True,
        'sku':          sku,
        'wc_id':        prod['wc_id'],
        'ml_item_id':   ml_item_id,
        'ml_url':       ml_url,
        'ml_status':    status_code,
        'desc_status':  desc_status,
        'published_at': timestamp,
    }

    save_backlog(backlog_key, {
        'timestamp':        timestamp,
        'cuenta':           cuenta,
        'wc_id':            prod['wc_id'],
        'title':            prod['title'],
        'price':            prod['price'],
        'category_id':      prod['ml_category_id'],
        'payload':          payload,
        'ml_response':      response,
        'ml_status':        status_code,
        'pics_preuploaded': len([p for p in payload.get('pictures', []) if 'id' in p]),
        'desc_status':      desc_status,
        'ml_item_id':       ml_item_id,
        'ml_url':           ml_url,
        'result':           result,
    })

    return result
