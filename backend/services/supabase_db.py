"""
supabase_db.py — Acceso a la base de datos Postgres de Supabase.

Supabase es el NUEVO medio de consultas para las publicaciones de Mercado Libre:
un pipeline externo mantiene el dataset sincronizado a diario, así que la UI puede
leer de aquí (rápido, paginado) sin llamar a la API de ML por página.

Tablas clave (ver README / memoria):
  - products_snapshot : publicaciones ML por día (title, price, stock, status, seller_sku, raw…)
  - daily_stock       : stock por día (stock_odoo = real, stock_full = FULL) + logistic_type
  - ml_accounts       : cuentas (account_id uuid → nickname BEKURA / SANCORFASHION)
  - product_changes / sales / daily_visits / competition_cache : para el detalle 360°

Mismo patrón que services/db.py (MySQL): un POOL de conexiones que se reutiliza.
Supabase Postgres se conecta por el pooler (session 5432 / transaction 6543) sobre
TLS. Placeholders con %s (psycopg2), filas como dict (RealDictCursor).

REGLA DEL POOLER TRANSACCIONAL (6543) — aprendida en carne propia (2026-07-15):
el "cajero" de Postgres se comparte entre clientes ENTRE transacciones, así que
el estado de sesión SE FUGA a otros clientes. Por eso aquí está PROHIBIDO:
  - `SET nombre = valor`  /  `set_session(...)`  (sesión)          → usar `SET LOCAL`
    dentro de la transacción (se limpia solo al terminar).
  - prepared statements con nombre, LISTEN/NOTIFY, advisory locks de sesión.
El patrón get_cursor() de este módulo ya es compatible: cada uso es una
transacción corta con commit/rollback propio.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from config import settings

log = logging.getLogger("omnicanal.supabase")

_pool = None  # PooledDB | None (perezoso; solo si hay SUPABASE_DB_URL)


def disponible() -> bool:
    """¿Hay cadena de conexión configurada?"""
    return bool(settings.supabase_db_url)


def _get_pool():
    global _pool
    if _pool is None:
        if not settings.supabase_db_url:
            raise RuntimeError("SUPABASE_DB_URL no está configurada.")
        import psycopg2
        from psycopg2.extras import RealDictCursor
        from dbutils.pooled_db import PooledDB

        _pool = PooledDB(
            creator=psycopg2,
            maxconnections=6,
            mincached=1,
            maxcached=4,
            blocking=True,
            ping=1,  # psycopg2: 1 = ping al tomar del pool
            dsn=settings.supabase_db_url,
            cursor_factory=RealDictCursor,
            connect_timeout=10,
        )
    return _pool


@contextmanager
def get_cursor() -> Iterator[Any]:
    """Cursor de una conexión del POOL; se devuelve al pool al salir."""
    conn = _get_pool().connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()  # con PooledDB, DEVUELVE la conexión al pool


def fetch_all(sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def fetch_one(sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def fetch_scalar(sql: str, params: tuple | dict | None = None) -> Any:
    row = fetch_one(sql, params)
    if not row:
        return None
    return next(iter(row.values()))


def execute(sql: str, params: tuple | dict | None = None) -> int:
    """Ejecuta INSERT/UPDATE/DELETE. Devuelve filas afectadas (commit incluido).

    Con `INSERT ... ON CONFLICT DO NOTHING` el retorno distingue el resultado:
    1 = fila nueva insertada; 0 = era un duplicado (la base lo descartó) — es
    la base del conteo de webhooks duplicados sin lógica extra en el código.
    """
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def execute_returning(sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
    """Ejecuta una escritura con RETURNING y devuelve la fila (o None si no hubo)."""
    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def ping() -> bool:
    """Verifica conectividad con Supabase."""
    if not disponible():
        return False
    try:
        return fetch_scalar("SELECT 1") == 1
    except Exception as exc:  # noqa: BLE001
        log.warning("Supabase ping falló: %s", exc)
        return False
