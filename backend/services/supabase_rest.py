"""
supabase_rest.py — Lectura de publicaciones de Mercado Libre desde Supabase vía
PostgREST (API REST), usando el service_role key. NO requiere la contraseña de la
base: funciona con las API keys.

Es el nuevo medio de consultas: un pipeline externo mantiene el dataset al día, así
que la UI lee de aquí (paginado) sin llamar a la API de ML por página.

Tablas:
  - products_snapshot : publicaciones por día (title, price, stock, status, seller_sku…)
  - daily_stock       : stock por día (stock_odoo = real, stock_full = FULL)
  - ml_accounts       : account_id (uuid) → nickname (BEKURA / SANCORFASHION)

Nota: cuando haya SUPABASE_DB_URL (contraseña), se puede cambiar a SQL directo
(services/supabase_db.py) para JOINs en el servidor; la interfaz de este módulo se
mantendría igual.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from config import settings

log = logging.getLogger("omnicanal.supabase_rest")

_TTL = 60 * 30  # 30 min de cache para fechas/cuentas (cambian a diario)
_cache: dict[str, tuple[float, Any]] = {}

# Estados de nuestra UI → status de products_snapshot
_ESTADOS = {
    "publicado": "active", "activo": "active", "activos": "active", "active": "active",
    "pausado": "paused", "pausados": "paused", "inactivo": "paused",
    "inactivos": "paused", "paused": "paused", "cerrado": "closed", "closed": "closed",
}
_ORDEN = {
    "reciente": "last_updated.desc.nullslast",
    "precio_desc": "price.desc.nullslast",
    "precio_asc": "price.asc.nullslast",
    "stock_desc": "available_quantity.desc.nullslast",
    "stock_asc": "available_quantity.asc.nullslast",
}

_COLS = (
    "ml_item_id,account_id,seller_sku,title,price,original_price,"
    "available_quantity,sold_quantity,status,category_id,permalink,thumbnail,"
    "health,shipping_mode,visits_30d,visits_7d,last_updated"
)


def disponible() -> bool:
    return bool(settings.supabase_url and settings.supabase_service_role_key)


def _base() -> str:
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _headers(extra: dict | None = None) -> dict:
    h = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }
    if extra:
        h.update(extra)
    return h


def _get(tabla: str, params: dict, con_total: bool = False) -> tuple[list[dict], int | None]:
    headers = _headers({"Prefer": "count=exact"} if con_total else None)
    r = requests.get(f"{_base()}/{tabla}", params=params, headers=headers, timeout=30)
    r.raise_for_status()
    total = None
    if con_total:
        cr = r.headers.get("Content-Range", "")  # p.ej. "0-39/3926"
        if "/" in cr:
            tail = cr.split("/")[-1]
            total = int(tail) if tail.isdigit() else None
    return r.json(), total


def _cacheado(clave: str, fn):
    now = time.time()
    hit = _cache.get(clave)
    if hit and (now - hit[0]) < _TTL:
        return hit[1]
    val = fn()
    _cache[clave] = (now, val)
    return val


# ── Metadatos (cacheados) ────────────────────────────────────────────────────
def ultimo_snapshot() -> str | None:
    def _f():
        data, _ = _get("products_snapshot",
                       {"select": "snapshot_date", "order": "snapshot_date.desc", "limit": 1})
        return data[0]["snapshot_date"] if data else None
    return _cacheado("snap", _f)


def ultima_fecha_stock() -> str | None:
    def _f():
        data, _ = _get("daily_stock",
                       {"select": "date", "order": "date.desc", "limit": 1})
        return data[0]["date"] if data else None
    return _cacheado("stockdate", _f)


def cuentas() -> list[dict]:
    def _f():
        data, _ = _get("ml_accounts",
                       {"select": "id,ml_user_id,nickname,label,is_active"})
        return data
    return _cacheado("cuentas", _f)


def _account_id(cuenta: str | None) -> str | None:
    """Mapea el nickname (BEKURA / SANCORFASHION) al account_id (uuid)."""
    if not cuenta:
        return None
    for c in cuentas():
        if (c.get("nickname") or "").upper() == cuenta.upper():
            return c["id"]
    return None


def _nickname(account_id: str | None) -> str | None:
    if not account_id:
        return None
    for c in cuentas():
        if c["id"] == account_id:
            return c.get("nickname")
    return None


# ── Stock por lote de SKUs ───────────────────────────────────────────────────
def _stock_por_sku(skus: list[str]) -> dict[str, dict]:
    skus = [s for s in skus if s]
    if not skus:
        return {}
    fecha = ultima_fecha_stock()
    if not fecha:
        return {}
    # PostgREST: sku=in.("A","B") — entrecomillar por si hay caracteres raros.
    lista = ",".join('"' + s.replace('"', "") + '"' for s in set(skus))
    data, _ = _get("daily_stock", {
        "select": "sku,stock_odoo,stock_full,logistic_type,status,price,warehouse",
        "date": f"eq.{fecha}",
        "sku": f"in.({lista})",
    })
    out: dict[str, dict] = {}
    for row in data:
        out.setdefault(row["sku"], row)  # una fila por sku (la primera)
    return out


# ── Normalización a la forma de Producto ─────────────────────────────────────
def _seguro_https(url: str | None) -> str | None:
    if not url:
        return None
    return url.replace("http://", "https://")


def _normalizar(row: dict, stock: dict[str, dict]) -> dict:
    sku = row.get("seller_sku")
    st = stock.get(sku, {}) if sku else {}
    logistic = st.get("logistic_type")
    return {
        "sku": sku or row.get("ml_item_id"),
        "wc_id": None,
        "nombre": row.get("title") or "",
        "imagen": _seguro_https(row.get("thumbnail")),
        "precio": row.get("price"),
        "precio_base": row.get("original_price"),
        "stock": st.get("stock_odoo"),           # stock mostrado = real
        "stock_real": st.get("stock_odoo"),
        "stock_full": st.get("stock_full"),
        "stock_fba": None,
        "situacion": row.get("status"),
        "estado": row.get("status"),
        "categoria_id": row.get("category_id"),
        "categoria_path": [],
        "full": (logistic == "fulfillment"),
        "full_label": "FULL" if logistic == "fulfillment" else None,
        "publicado": row.get("status") == "active",
        "item_id": row.get("ml_item_id"),
        "url": row.get("permalink"),
        "cuenta": _nickname(row.get("account_id")),
        "origen": "supabase",
        # extras útiles para el resumen
        "sold_quantity": row.get("sold_quantity"),
        "available_quantity": row.get("available_quantity"),
        "health": row.get("health"),
        "visits_30d": row.get("visits_30d"),
    }


# ── Lista paginada de publicaciones ──────────────────────────────────────────
def listar_publicaciones(
    cuenta: str | None = None,
    page: int = 1,
    per_page: int = 40,
    search: str | None = None,
    estados: list[str] | None = None,
    orden: str = "reciente",
) -> tuple[list[dict], int]:
    snap = ultimo_snapshot()
    if not snap:
        return [], 0
    params: dict[str, Any] = {
        "select": _COLS,
        "snapshot_date": f"eq.{snap}",
        "order": _ORDEN.get(orden, _ORDEN["reciente"]),
        "limit": per_page,
        "offset": (page - 1) * per_page,
    }
    acct = _account_id(cuenta)
    if acct:
        params["account_id"] = f"eq.{acct}"
    if estados:
        mapped = sorted({_ESTADOS.get(e.strip().lower()) for e in estados} - {None})
        if mapped:
            params["status"] = f"in.({','.join(mapped)})"
    if search:
        s = search.strip().replace("*", "")
        params["or"] = f"(title.ilike.*{s}*,seller_sku.ilike.*{s}*)"

    filas, total = _get("products_snapshot", params, con_total=True)
    stock = _stock_por_sku([f.get("seller_sku") for f in filas])
    items = [_normalizar(f, stock) for f in filas]
    return items, (total or 0)


def presencia_ml(skus: list[str]) -> dict[str, dict[str, Any]]:
    """
    Presencia en Mercado Libre para un lote de seller_sku (fuente comprehensiva:
    products_snapshot del día). Devuelve { sku: {n, publicado, item_id, url} }.
    """
    skus = [s for s in skus if s]
    if not skus or not disponible():
        return {}
    snap = ultimo_snapshot()
    if not snap:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(skus), 100):
        chunk = skus[i:i + 100]
        lista = ",".join('"' + s.replace('"', "") + '"' for s in chunk)
        try:
            data, _ = _get("products_snapshot", {
                "select": "seller_sku,ml_item_id,account_id,status,permalink",
                "snapshot_date": f"eq.{snap}",
                "seller_sku": f"in.({lista})",
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("presencia_ml lote falló: %s", exc)
            continue
        for row in data:
            sku = row.get("seller_sku")
            if not sku:
                continue
            e = out.setdefault(sku, {"n": 0, "publicado": False, "item_id": None, "url": None})
            e["n"] += 1
            if row.get("status") == "active":
                e["publicado"] = True
            if not e["item_id"] and row.get("ml_item_id"):
                e["item_id"] = row.get("ml_item_id")
                e["url"] = _seguro_https(row.get("permalink"))
    return out


# ── Detalle / resumen de un SKU ──────────────────────────────────────────────
def detalle_sku(sku: str) -> dict | None:
    snap = ultimo_snapshot()
    if not snap:
        return None
    filas, _ = _get("products_snapshot", {
        "select": _COLS + ",attributes_map,raw",
        "seller_sku": f"eq.{sku}",
        "snapshot_date": f"eq.{snap}",
        "order": "last_updated.desc",
    })
    if not filas:
        return None
    stock = _stock_por_sku([sku])
    publicaciones = [_normalizar(f, stock) for f in filas]  # una por cuenta
    # Cambios recientes
    cambios, _ = _get("product_changes", {
        "select": "ml_item_id,field_name,old_value,new_value,detected_at",
        "ml_item_id": f"in.({','.join(chr(34)+p['item_id']+chr(34) for p in publicaciones if p.get('item_id'))})",
        "order": "detected_at.desc",
        "limit": 20,
    }) if publicaciones else ([], None)
    return {"sku": sku, "publicaciones": publicaciones, "cambios": cambios}
