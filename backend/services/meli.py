"""
meli.py — Servicio de Mercado Libre (canal principal).

Estrategia híbrida:
  - LECTURA RÁPIDA (UI): join en MySQL de
        productos  +  ml_progress (item_id, url, publicado)
                   +  costos_finales (precio_sugerido, ml_cat_id, comisión)
  - REFRESCO EN VIVO: con el token de `ml_tokens` consultamos la API de ML
        /items/{id}  → precio, available_quantity, logistic_type (FULL), category_id
        /categories/{id} → path_from_root (todos los niveles de categoría)

El "FULL" de Mercado Libre = logistic_type == "fulfillment".
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from config import settings
from services import db

log = logging.getLogger("omnicanal.meli")

_API = "https://api.mercadolibre.com"

# ── Lectura desde el cache (MySQL) ────────────────────────────────────────────

# Mercado Libre opera 2 cuentas: BEKURA (Kubera, default) y SANCORFASHION (San Corpe).
# La consulta parte de ml_progress (los LISTINGS reales de la cuenta) y enriquece
# con la tabla maestra `productos` y con costos_finales. Así el conteo de la
# pestaña coincide con lo realmente publicado en esa cuenta.
_SQL_LISTAR = """
    SELECT  mp.sku                                      AS sku,
            COALESCE(p.wc_id, mp.wc_id)                 AS wc_id,
            p.odoo_id                                   AS odoo_id,
            COALESCE(p.nombre, mp.sku)                  AS nombre,
            p.stock_odoo                                AS stock_odoo,
            p.categorias                                AS categorias,
            COALESCE(cf.precio_sugerido, p.precio)      AS precio,
            cf.precio_base                              AS precio_base,
            cf.ml_cat_id                                AS ml_cat_id,
            mp.cuenta                                   AS cuenta,
            mp.ml_item_id                               AS ml_item_id,
            mp.ml_url                                   AS ml_url,
            mp.success                                  AS publicado
    FROM ml_progress mp
    LEFT JOIN productos      p  ON p.sku  = mp.sku
    LEFT JOIN costos_finales cf ON cf.sku = mp.sku
    WHERE (%(cuenta)s IS NULL OR mp.cuenta = %(cuenta)s)
      AND (%(solo_publicados)s = 0 OR mp.success = 1)
      AND (%(search)s IS NULL OR p.nombre LIKE %(like)s OR mp.sku LIKE %(like)s)
      __ESTADO__
    ORDER BY __ORDEN__
    LIMIT %(limit)s OFFSET %(offset)s
"""

_SQL_COUNT = """
    SELECT COUNT(*) AS total
    FROM ml_progress mp
    LEFT JOIN productos p ON p.sku = mp.sku
    WHERE (%(cuenta)s IS NULL OR mp.cuenta = %(cuenta)s)
      AND (%(solo_publicados)s = 0 OR mp.success = 1)
      AND (%(search)s IS NULL OR p.nombre LIKE %(like)s OR mp.sku LIKE %(like)s)
      __ESTADO__
"""


def _normalizar(row: dict[str, Any]) -> dict[str, Any]:
    publicado = bool(row.get("publicado"))
    return {
        "sku": row["sku"],
        "wc_id": row.get("wc_id"),
        "odoo_id": row.get("odoo_id"),
        "nombre": row.get("nombre") or row["sku"],
        "precio": _f(row.get("precio")),
        "precio_base": _f(row.get("precio_base")),
        "stock": row.get("stock_odoo"),
        "estado": "activo" if publicado else "sin publicar",
        "categoria_id": row.get("ml_cat_id"),
        # La ruta completa se resuelve bajo demanda vía API (cacheable);
        # de momento mostramos el id de categoría de ML.
        "categoria_path": [],
        "publicado": publicado,
        "item_id": row.get("ml_item_id"),
        "url": row.get("ml_url"),
        "cuenta": row.get("cuenta"),
        "full": None,          # se completa al refrescar contra la API
        "full_label": "FULL",
        "origen": "db",
    }


# Orden permitido (columnas del SELECT)
_ORDEN_ML = {
    "stock_desc": "p.stock_odoo DESC",
    "stock_asc": "p.stock_odoo ASC",
    "precio_desc": "precio DESC",
    "precio_asc": "precio ASC",
    "reciente": "(mp.success = 1) DESC, mp.updated_at DESC",
}


def listar(
    page: int = 1,
    per_page: int = 40,
    search: str | None = None,
    solo_publicados: bool = False,
    cuenta: str | None = None,
    orden: str = "reciente",
    estados: list[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    Devuelve (items, total) desde el cache MySQL.
    `cuenta` filtra por cuenta de ML (BEKURA=Kubera, SANCORFASHION=San Corpe).
    `orden` ordena por stock/precio. `estados` filtra publicado/inactivo.
    """
    offset = (page - 1) * per_page
    like = f"%{search}%" if search else None
    # Filtro de estado: publicado (success=1) / inactivo (success<>1)
    estado_sql = ""
    if estados:
        if "publicado" in estados and "inactivo" not in estados:
            estado_sql = " AND mp.success = 1"
        elif "inactivo" in estados and "publicado" not in estados:
            estado_sql = " AND (mp.success IS NULL OR mp.success = 0)"
    order_sql = _ORDEN_ML.get(orden, _ORDEN_ML["reciente"])
    params = {
        "limit": per_page, "offset": offset, "search": search, "like": like,
        "solo_publicados": 1 if solo_publicados else 0, "cuenta": cuenta,
    }
    sql = _SQL_LISTAR.replace("__ESTADO__", estado_sql).replace("__ORDEN__", order_sql)
    sql_count = _SQL_COUNT.replace("__ESTADO__", estado_sql)
    try:
        rows = db.fetch_all(sql, params)
        total = db.fetch_scalar(sql_count, params) or 0
        return [_normalizar(r) for r in rows], int(total)
    except Exception as exc:  # noqa: BLE001
        log.error("Error listando ML desde DB: %s", exc)
        return [], 0


def contar_publicados(cuenta: str | None = None) -> int:
    try:
        if cuenta:
            return int(db.fetch_scalar(
                "SELECT COUNT(*) FROM ml_progress WHERE success = 1 AND cuenta = %s",
                (cuenta,),
            ) or 0)
        return int(db.fetch_scalar(
            "SELECT COUNT(*) FROM ml_progress WHERE success = 1"
        ) or 0)
    except Exception:  # noqa: BLE001
        return 0


# ── Token OAuth desde DB ──────────────────────────────────────────────────────

def _fernet():
    """Fernet para desencriptar los tokens de ml_tokens (cifrados con DB_ENCRYPTION_KEY)."""
    key = settings.db_encryption_key
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode())
    except Exception as exc:  # noqa: BLE001
        log.warning("Fernet no disponible: %s", exc)
        return None


def _access_token(cuenta: str | None = None) -> str | None:
    """Lee y DESENCRIPTA el access_token vigente de ml_tokens (por cuenta)."""
    try:
        if cuenta:
            row = db.fetch_one(
                "SELECT * FROM ml_tokens WHERE cuenta = %s ORDER BY updated_at DESC LIMIT 1",
                (cuenta,),
            )
        else:
            row = db.fetch_one(
                "SELECT * FROM ml_tokens ORDER BY updated_at DESC LIMIT 1"
            )
        if not row:
            return None
        raw = row.get("access_token") or row.get("token")
        if not raw:
            return None
        # Los tokens están cifrados con Fernet (empiezan con 'gAAAAA').
        f = _fernet()
        if f and isinstance(raw, str) and raw.startswith("gAAAAA"):
            try:
                return f.decrypt(raw.encode()).decode()
            except Exception as exc:  # noqa: BLE001
                log.warning("No se pudo desencriptar token ML (%s): %s", cuenta, exc)
                return None
        return raw  # ya venía en claro
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer ml_tokens: %s", exc)
        return None


def refrescar_token(cuenta: str) -> str | None:
    """
    Renueva el access_token de una cuenta ML usando su refresh_token + las
    credenciales de la app (MELI_APP_ID / MELI_CLIENT_SECRET). Guarda el nuevo
    token cifrado en ml_tokens. Devuelve el nuevo access_token (o None).

    Requiere configurar MELI_APP_ID y MELI_CLIENT_SECRET. Si no están, no se puede
    renovar (los tokens expiran a las ~6 h) y la cuenta saldrá vacía hasta que el
    proceso externo los actualice.
    """
    if not settings.meli_app_id or not settings.meli_client_secret:
        log.warning("No hay MELI_APP_ID/SECRET; no se puede renovar token de %s", cuenta)
        return None
    try:
        row = db.fetch_one(
            "SELECT refresh_token FROM ml_tokens WHERE cuenta=%s LIMIT 1", (cuenta,)
        )
        if not row or not row.get("refresh_token"):
            return None
        f = _fernet()
        rt = row["refresh_token"]
        if f and isinstance(rt, str) and rt.startswith("gAAAAA"):
            rt = f.decrypt(rt.encode()).decode()
        import httpx as _httpx
        r = _httpx.post(f"{_API}/oauth/token", data={
            "grant_type": "refresh_token",
            "client_id": settings.meli_app_id,
            "client_secret": settings.meli_client_secret,
            "refresh_token": rt,
        }, timeout=20)
        if r.status_code != 200:
            log.warning("Refresh token ML %s falló: %s %s", cuenta, r.status_code, r.text[:120])
            return None
        tok = r.json()
        nuevo = tok["access_token"]
        nuevo_rt = tok.get("refresh_token", rt)
        # Guardar cifrado
        if f:
            enc_at = f.encrypt(nuevo.encode()).decode()
            enc_rt = f.encrypt(nuevo_rt.encode()).decode()
        else:
            enc_at, enc_rt = nuevo, nuevo_rt
        with db.get_cursor() as cur:
            cur.execute(
                "UPDATE ml_tokens SET access_token=%s, refresh_token=%s, updated_at=NOW() WHERE cuenta=%s",
                (enc_at, enc_rt, cuenta),
            )
        log.info("Token ML %s renovado.", cuenta)
        return nuevo
    except Exception as exc:  # noqa: BLE001
        log.warning("Refresh token ML %s error: %s", cuenta, exc)
        return None


# ── Refresco en vivo contra la API de Mercado Libre ───────────────────────────

async def refrescar_item(item_id: str, cuenta: str | None = None) -> dict[str, Any] | None:
    """Consulta /items/{id} + categoría para obtener precio, stock, FULL y ruta."""
    token = _access_token(cuenta)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(base_url=_API, timeout=20.0) as cli:
            r = await cli.get(f"/items/{item_id}", headers=headers)
            r.raise_for_status()
            item = r.json()
            cat_path = await _category_path(cli, item.get("category_id"), headers)
    except Exception as exc:  # noqa: BLE001
        log.warning("Refresco ML %s falló: %s", item_id, exc)
        return None

    logistic = (item.get("shipping") or {}).get("logistic_type")
    return {
        "item_id": item_id,
        "precio": item.get("price"),
        "stock": item.get("available_quantity"),
        "estado": item.get("status"),
        "categoria_id": item.get("category_id"),
        "categoria_path": cat_path,
        "full": logistic == "fulfillment",
        "full_label": "FULL" if logistic == "fulfillment" else (logistic or ""),
        "url": item.get("permalink"),
    }


async def _category_path(
    cli: httpx.AsyncClient, cat_id: str | None, headers: dict
) -> list[dict[str, Any]]:
    if not cat_id:
        return []
    try:
        r = await cli.get(f"/categories/{cat_id}", headers=headers)
        r.raise_for_status()
        data = r.json()
        return [
            {"id": n.get("id"), "nombre": n.get("name")}
            for n in data.get("path_from_root", [])
        ]
    except Exception:  # noqa: BLE001
        return [{"id": cat_id, "nombre": cat_id}]


def _f(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
