"""
db.py — Acceso a la base de datos MySQL (cache híbrido de Mercado Libre / Amazon).

El esquema relevante (DB u531713409_kubera_ml):
  - productos        : puente maestro por SKU  (sku ↔ wc_id ↔ odoo_id, precio, stock_odoo, categorias…)
  - ml_progress      : publicaciones en Mercado Libre (ml_item_id, ml_url, published_at)
  - costos_finales   : precio sugerido + ml_cat_id + comisión por SKU
  - amazon_progress  : listings Amazon (asin, product_type, status)
  - ml_tokens        : tokens OAuth de Mercado Libre

Estrategia híbrida: leemos de aquí para que la UI sea rápida; el refresco contra
la API en vivo (ML/Amazon) actualiza estas tablas bajo demanda.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

import pymysql
from dbutils.pooled_db import PooledDB
from pymysql.cursors import DictCursor

from config import settings

log = logging.getLogger("omnicanal.db")

# ── Pool de conexiones ────────────────────────────────────────────────────────
# Hostinger limita las conexiones NUEVAS por hora (max_connections_per_hour=500).
# Abrir una conexión por consulta agota ese límite. El pool REUTILIZA un puñado de
# conexiones, así que casi no se crean conexiones nuevas y nunca se toca el límite.
_pool: PooledDB | None = None


def _get_pool() -> PooledDB:
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=6,    # tope de conexiones simultáneas del pool
            mincached=1,         # conexiones precreadas
            maxcached=4,         # conexiones ociosas que se conservan
            blocking=True,       # si se llega al tope, espera (no falla)
            ping=4,              # verifica/reconecta antes de cada consulta
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            database=settings.db_name,
            cursorclass=DictCursor,
            connect_timeout=10,
            read_timeout=20,
            charset="utf8mb4",
            autocommit=True,
        )
    return _pool


@contextmanager
def get_cursor() -> Iterator[DictCursor]:
    """Entrega un cursor de una conexión del POOL y la devuelve al pool al salir."""
    conn = _get_pool().connection()
    try:
        with conn.cursor() as cur:
            yield cur
    finally:
        conn.close()  # con PooledDB, esto DEVUELVE la conexión al pool (no la cierra)


def fetch_all(sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def fetch_one(sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetch_scalar(sql: str, params: tuple | dict | None = None) -> Any:
    row = fetch_one(sql, params)
    if not row:
        return None
    return next(iter(row.values()))


def execute(sql: str, params: tuple | dict | None = None) -> int:
    """Ejecuta INSERT/UPDATE/DELETE/DDL. Devuelve filas afectadas. (autocommit=True)."""
    with get_cursor() as cur:
        return cur.execute(sql, params)


def ping() -> bool:
    """Verifica conectividad con la base de datos."""
    try:
        return fetch_scalar("SELECT 1") == 1
    except Exception as exc:  # noqa: BLE001
        log.warning("DB ping falló: %s", exc)
        return False
