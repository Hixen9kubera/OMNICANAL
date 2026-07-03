"""
wp_db.py — Lecturas DIRECTAS a la base de datos de WordPress/WooCommerce.

Decisión del usuario (2026-07-02): las CONSULTAS van directo a MySQL (inmunes
al anti-bot del hosting y muchísimo más rápidas); la API REST de Woo queda solo
para ESCRITURAS (crear productos, actualizar stock/costo/status).

Se activa solo: si WPDB_NAME/WPDB_USER/WPDB_PASSWORD están en el .env, todos
los lectores (índice de drafts, catálogo general, lookups del sync) usan esta
vía; si no, cae al método por API de siempre.

Esquema WP relevante:
  {P}posts      : productos (post_type=product) y variaciones (product_variation)
  {P}postmeta   : _sku, _stock, _manage_stock, costo…
  {P}terms / {P}term_taxonomy / {P}term_relationships : categorías (product_cat)
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

import pymysql
from dbutils.pooled_db import PooledDB
from pymysql.cursors import DictCursor

from config import settings

log = logging.getLogger("omnicanal.wp_db")

_pool: PooledDB | None = None
_ok: bool | None = None
_ok_ts: float = 0.0


def _prefix() -> str:
    return settings.wpdb_prefix or "wp_"


def _get_pool() -> PooledDB:
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=4,
            mincached=0,
            maxcached=2,
            blocking=True,
            ping=4,
            host=settings.wpdb_host or settings.db_host,
            port=settings.wpdb_port,
            user=settings.wpdb_user,
            password=settings.wpdb_password,
            database=settings.wpdb_name,
            cursorclass=DictCursor,
            connect_timeout=10,
            read_timeout=60,
            charset="utf8mb4",
            autocommit=True,
        )
    return _pool


@contextmanager
def _cursor() -> Iterator[DictCursor]:
    conn = _get_pool().connection()
    try:
        with conn.cursor() as cur:
            yield cur
    finally:
        conn.close()


def _fetch_all(sql: str, params: tuple | None = None) -> list[dict[str, Any]]:
    with _cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def disponible() -> bool:
    """True si hay credenciales de la DB de WordPress y la conexión funciona."""
    global _ok, _ok_ts
    if not (settings.wpdb_name and settings.wpdb_user and settings.wpdb_password):
        return False
    if _ok is not None and (time.time() - _ok_ts) < 300:
        return _ok
    try:
        _fetch_all("SELECT 1")
        _ok = True
    except Exception as exc:  # noqa: BLE001
        log.warning("DB de WordPress no disponible: %s", exc)
        _ok = False
    _ok_ts = time.time()
    return _ok


def _categorias_por_post(ids: list[int]) -> dict[int, list[str]]:
    """{ post_id: [nombres de categoría] } para los productos dados."""
    P = _prefix()
    salida: dict[int, list[str]] = {}
    for i in range(0, len(ids), 1000):
        chunk = ids[i:i + 1000]
        ph = ",".join(["%s"] * len(chunk))
        rows = _fetch_all(
            f"""SELECT tr.object_id, t.name
                FROM {P}term_relationships tr
                JOIN {P}term_taxonomy tt
                     ON tt.term_taxonomy_id = tr.term_taxonomy_id
                    AND tt.taxonomy = 'product_cat'
                JOIN {P}terms t ON t.term_id = tt.term_id
                WHERE tr.object_id IN ({ph})""",
            tuple(chunk),
        )
        for r in rows:
            salida.setdefault(r["object_id"], []).append(r["name"])
    return salida


def indice_drafts() -> list[dict[str, Any]]:
    """
    TODOS los drafts en una consulta (reemplaza ~50 requests HTTP):
    [{wc_id, sku, nombre, estado, stock, categorias}], más recientes primero.
    """
    P = _prefix()
    rows = _fetch_all(
        f"""SELECT p.ID AS wc_id, p.post_title AS nombre, p.post_status AS estado,
                   sku.meta_value AS sku, stock.meta_value AS stock
            FROM {P}posts p
            LEFT JOIN {P}postmeta sku
                   ON sku.post_id = p.ID AND sku.meta_key = '_sku'
            LEFT JOIN {P}postmeta stock
                   ON stock.post_id = p.ID AND stock.meta_key = '_stock'
            WHERE p.post_type = 'product' AND p.post_status = 'draft'
            ORDER BY p.post_date DESC"""
    )
    cats = _categorias_por_post([r["wc_id"] for r in rows])
    salida = []
    for r in rows:
        stock = r.get("stock")
        try:
            stock = int(float(stock)) if stock not in (None, "") else None
        except (ValueError, TypeError):
            stock = None
        salida.append({
            "wc_id": r["wc_id"],
            "sku": (r.get("sku") or f"WC-{r['wc_id']}").strip(),
            "nombre": r.get("nombre") or "",
            "estado": r.get("estado"),
            "stock": stock,
            "categorias": cats.get(r["wc_id"], []),
        })
    return salida


def indice_catalogo() -> list[dict[str, Any]]:
    """
    Catálogo de productos PADRE con cualquier estado útil (para la vista
    GENERAL y derivados). Excluye basura de WP (trash, auto-draft, inherit).
    """
    P = _prefix()
    rows = _fetch_all(
        f"""SELECT p.ID AS wc_id, p.post_title AS nombre, p.post_status AS estado,
                   sku.meta_value AS sku
            FROM {P}posts p
            LEFT JOIN {P}postmeta sku
                   ON sku.post_id = p.ID AND sku.meta_key = '_sku'
            WHERE p.post_type = 'product'
              AND p.post_status NOT IN ('trash', 'auto-draft', 'inherit')
            ORDER BY p.post_date DESC"""
    )
    return [
        {
            "wc_id": r["wc_id"],
            "sku": (r.get("sku") or f"WC-{r['wc_id']}").strip(),
            "nombre": r.get("nombre") or "",
            "estado": r.get("estado"),
        }
        for r in rows
    ]


def skus_existentes() -> set[str]:
    """
    TODOS los SKUs ocupados en Woo (productos + variaciones, sin papelera),
    en minúsculas. Para el diff Odoo↔Woo del sync de drafts.
    """
    P = _prefix()
    rows = _fetch_all(
        f"""SELECT LOWER(TRIM(sku.meta_value)) AS sku
            FROM {P}posts p
            JOIN {P}postmeta sku
                 ON sku.post_id = p.ID AND sku.meta_key = '_sku'
            WHERE p.post_type IN ('product', 'product_variation')
              AND p.post_status != 'trash'
              AND sku.meta_value IS NOT NULL AND sku.meta_value != ''"""
    )
    return {r["sku"] for r in rows}


def productos_por_sku(skus: list[str]) -> dict[str, dict[str, Any]]:
    """
    Lookup masivo por SKU para el sync stock+costo (reemplaza ~270 requests):
    { sku: {wc_id, tipo, parent_id, stock, manage_stock, costo} }.
    """
    P = _prefix()
    salida: dict[str, dict[str, Any]] = {}
    for i in range(0, len(skus), 500):
        chunk = skus[i:i + 500]
        ph = ",".join(["%s"] * len(chunk))
        rows = _fetch_all(
            f"""SELECT p.ID AS wc_id, p.post_type AS tipo, p.post_parent AS parent_id,
                       sku.meta_value AS sku,
                       stock.meta_value AS stock,
                       manage.meta_value AS manage_stock,
                       costo.meta_value AS costo
                FROM {P}posts p
                JOIN {P}postmeta sku
                     ON sku.post_id = p.ID AND sku.meta_key = '_sku'
                LEFT JOIN {P}postmeta stock
                     ON stock.post_id = p.ID AND stock.meta_key = '_stock'
                LEFT JOIN {P}postmeta manage
                     ON manage.post_id = p.ID AND manage.meta_key = '_manage_stock'
                LEFT JOIN {P}postmeta costo
                     ON costo.post_id = p.ID AND costo.meta_key = 'costo'
                WHERE p.post_type IN ('product', 'product_variation')
                  AND p.post_status != 'trash'
                  AND sku.meta_value IN ({ph})""",
            tuple(chunk),
        )
        for r in rows:
            stock = r.get("stock")
            try:
                stock = int(float(stock)) if stock not in (None, "") else None
            except (ValueError, TypeError):
                stock = None
            salida[r["sku"].strip()] = {
                "wc_id": r["wc_id"],
                "tipo": "variation" if r["tipo"] == "product_variation" else "product",
                "parent_id": r.get("parent_id") or None,
                "stock": stock,
                "manage_stock": (r.get("manage_stock") == "yes"),
                "costo": r.get("costo"),
            }
    return salida
