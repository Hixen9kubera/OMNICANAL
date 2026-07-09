"""
wc_category_mapping.py — Mapeo WC category -> ML category (categoria como fuente de verdad).

Las KAMs editan la categoria del producto en el admin de WooCommerce. Cada categoria WC
tiene su `ml_category_id` correspondiente guardado en el campo `description` de la
categoria con el patron "ML: MLM###" (ej. "ML: MLM435356").

Este modulo:
1. Descarga TODAS las categorias WC vía REST y extrae el ML ID del description.
2. Cachea el mapeo en memoria por TTL.
3. Expone `get_ml_id_for_wc_category(wc_cat_id)` para resolver la categoria ML
   correcta cuando la KAM cambió la categoria visible en WC.

Asi el publisher puede preferir la categoria que la KAM eligio en WC (la fuente de
verdad humana) por encima del meta `ml_category_id` cacheado (que solo se actualiza
en el sync inicial ML->WC).
"""
import os
import re
import time
import requests

_PATTERN_ML_ID = re.compile(r'\bML[:\s]*\s*(MLM\d+)', re.IGNORECASE)

# Cache en memoria: dict[wc_cat_id] -> ml_category_id  (o None si no tiene mapeo)
_CACHE: dict[int, str | None] = {}
_CACHE_LOADED_AT: float = 0
_CACHE_TTL_SECONDS: int = 3600  # 1 hora


def _load_all_categories(wc_url: str, auth: tuple) -> dict[int, str | None]:
    """Descarga todas las categorias WC y extrae el ml_id de description."""
    mapping: dict[int, str | None] = {}
    page = 1
    while True:
        try:
            r = requests.get(
                f'{wc_url}/wp-json/wc/v3/products/categories',
                params={'per_page': 100, 'page': page},
                auth=auth, timeout=20,
            )
        except Exception as e:
            print(f'  [wc_cat_map] error pagina {page}: {e}')
            break
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        for c in data:
            desc = c.get('description', '') or ''
            m = _PATTERN_ML_ID.search(desc)
            mapping[int(c['id'])] = m.group(1) if m else None
        if len(data) < 100:
            break
        page += 1
    return mapping


def load_mapping(force: bool = False) -> dict[int, str | None]:
    """Carga el mapeo (cacheado, recarga si pasa TTL o force=True)."""
    global _CACHE, _CACHE_LOADED_AT
    now = time.time()
    if (not force) and _CACHE and (now - _CACHE_LOADED_AT) < _CACHE_TTL_SECONDS:
        return _CACHE
    wc_url = os.environ.get('WC_URL', '')
    wc_key = os.environ.get('WC_KEY', '')
    wc_secret = os.environ.get('WC_SECRET', '')
    if not (wc_url and wc_key and wc_secret):
        print('  [wc_cat_map] WC_URL/KEY/SECRET no configurados — cache vacio')
        return {}
    _CACHE = _load_all_categories(wc_url, (wc_key, wc_secret))
    _CACHE_LOADED_AT = now
    print(f'  [wc_cat_map] cargado: {len(_CACHE)} categorias WC ({sum(1 for v in _CACHE.values() if v)} con ml_id)')
    return _CACHE


def get_ml_id_for_wc_category(wc_cat_id: int) -> str | None:
    """
    Devuelve el ml_category_id correspondiente a una categoria WC, o None si no
    hay mapeo configurado en el description de esa categoria WC.
    """
    if wc_cat_id is None:
        return None
    try:
        wc_cat_id = int(wc_cat_id)
    except (TypeError, ValueError):
        return None
    mapping = load_mapping()
    return mapping.get(wc_cat_id)


def resolve_ml_category_from_wc(wc_categories: list, cached_ml_id: str = '') -> tuple[str | None, str | None]:
    """
    Politica de resolucion:
    - Iterar las categorias WC del producto en orden (la primera que tenga mapeo gana).
    - Devolver (ml_id, motivo) donde motivo es 'override' si se cambio,
      'same' si coincide con cached, o 'no_mapping' si no hay info.
    Si ninguna categoria WC tiene mapeo, devuelve (None, 'no_mapping') y el
    publisher debe usar el cached_ml_id (sin cambios).
    """
    if not wc_categories:
        return (None, 'no_wc_categories')
    for wc_cat in wc_categories:
        wc_cat_id = wc_cat.get('id') if isinstance(wc_cat, dict) else None
        ml_id = get_ml_id_for_wc_category(wc_cat_id)
        if ml_id:
            if cached_ml_id and ml_id == cached_ml_id:
                return (ml_id, 'same')
            return (ml_id, 'override')
    return (None, 'no_mapping')
