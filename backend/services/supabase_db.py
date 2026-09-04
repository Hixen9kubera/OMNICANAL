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
from core import actor

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
            # OJO: este ping NO SIRVE con psycopg2 y se deja solo por no
            # cambiar el comportamiento del pool. Sus conexiones no tienen
            # método `ping()`; DBUtils lo intenta, recibe AttributeError y
            # entonces hace `self._ping = 0` y `reconnect = False` — apaga la
            # comprobación PARA SIEMPRE en esa conexión (verificado en vivo el
            # 28-ago-2026: `_ping` vale 0 tras la primera consulta).
            # Consecuencia: el pool entrega conexiones muertas sin avisar. Quien
            # cubre eso es `_reintentar_transitorio`, más abajo.
            ping=1,
            dsn=settings.supabase_db_url,
            cursor_factory=RealDictCursor,
            connect_timeout=10,
        )
    return _pool


def _es_solo_lectura(exc: Exception) -> bool:
    """True si el error es el candado de solo-lectura heredado por el pooler."""
    return "read-only transaction" in str(exc).lower()


def _es_conexion_muerta(exc: Exception) -> bool:
    """
    True si el pool entregó una conexión que ya estaba cerrada del otro lado.

    Pasa porque el pool NO comprueba que sigan vivas (ver la nota del `ping` en
    `_get_pool`): cuando Supavisor recicla, reinicia o hace failover, la
    conexión del servidor muere y el siguiente cliente se la encuentra cerrada.
    DBUtils tiene failover propio y `InterfaceError` sí está en su lista, pero
    solo cubre la ejecución del cursor — si la conexión muere al confirmar o a
    media tanda, el error sale entero. Alerta real: "Escritura de CHANNEL cayó a
    MySQL (tanda de 75): InterfaceError: connection already closed"
    (28-ago-2026, 01:05).
    """
    t = str(exc).lower()
    return ("connection already closed" in t
            or "connection is closed" in t
            or "server closed the connection" in t
            or "ssl connection has been closed" in t
            or "connection not open" in t)


def _reiniciar_pool() -> None:
    """
    Tira el pool entero; la siguiente llamada lo reconstruye con conexiones
    nuevas. Es a lo bruto a propósito: si una del lote está muerta, lo más
    probable es que las hermanas del mismo servidor también lo estén, y
    devolver la muerta al pool solo la reparte al siguiente que pase.
    """
    global _pool
    viejo, _pool = _pool, None
    if viejo is not None:
        try:
            viejo.close()
        except Exception:  # noqa: BLE001 — cerrar el pool viejo es cortesía
            pass


def _desinfectar() -> int:
    """
    Quita el candado de solo-lectura que otro cliente dejó pegado en las
    conexiones del pooler. Devuelve cuántas destrabó.

    POR QUÉ HACE FALTA. Un script que lee producción con `set_session(readonly=
    True)` deja ese ajuste en la conexión del SERVIDOR, no en la suya: el pooler
    transaccional la recicla y la hereda el siguiente cliente. Si ese siguiente
    es el backend registrando una venta, la escritura truena con
    `ReadOnlySqlTransaction` — pasó tres veces entre el 18 y el 19-ago-2026
    (dos ventas y una tanda de 75 publicaciones de CHANNEL).

    Los scripts del repo ya usan `set transaction read only`, que muere con la
    transacción; pero cualquier script suelto fuera del repo puede volver a
    envenenar, así que el backend no puede depender de que nadie se equivoque.
    Destrabar aquí además limpia la conexión para todos los demás.
    """
    limpias = 0
    for _ in range(6):
        try:
            conn = _get_pool().connection()
        except Exception:  # noqa: BLE001
            break
        try:
            conn.rollback()
            cur = conn.cursor()
            cur.execute("show default_transaction_read_only")
            fila = cur.fetchone()
            valor = next(iter(fila.values())) if isinstance(fila, dict) else fila[0]
            if str(valor) == "on":
                cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
                conn.commit()
                limpias += 1
        except Exception:  # noqa: BLE001
            pass
        finally:
            conn.close()
    if limpias:
        log.warning("pooler: %d conexión(es) venían con el candado de "
                    "solo-lectura de otro cliente; destrabadas", limpias)
    return limpias


def _marcar_actor(conn, cur) -> None:
    """
    Deja el nombre de quien pide donde los triggers de historial lo leen.

    `set_config(..., true)` = LOCAL a la transacción: se limpia sola al terminar.
    Es la forma obligada aquí — un `SET` de sesión se quedaría pegado en una
    conexión COMPARTIDA del pooler 6543 y el siguiente cliente heredaría el
    nombre. O sea: la venta que registre el backend quedaría firmada por la
    última persona que usó el panel. Ese es el mismo mecanismo que ya tumbó las
    escrituras con el candado de solo-lectura, pero en vez de romper, MIENTE —
    y una bitácora que miente se consulta igual y se le cree.

    COSTO, dicho de frente: cuando SÍ hay actor, esto agrega un viaje a la base
    por cada `get_cursor()`. En Railway, que está en la misma región que el
    pooler, son ~1-2 ms. Y no lo paga lo que más consulta: los crons, sondeos,
    backfills y scripts corren sin petición detrás, así que no tienen actor y
    salen por el `return` de abajo sin mandar nada. Lo pagan las peticiones de
    una persona en el panel, que ya gastan cientos de milisegundos hablándole a
    Woo y a ML — ahí un viaje más es ruido.

    Si algún día un endpoint interactivo hace cientos de escrituras en un ciclo
    y esto se nota, el arreglo es diferir la firma hasta la primera sentencia
    que escriba (envolviendo el cursor), no quitarla. No se hizo hoy porque
    sería optimizar sin un problema medido.
    """
    quien = actor.actual()
    origen = actor.origen_actual()
    if not quien and not origen:
        return
    try:
        # Los dos en la MISMA sentencia: un viaje a la base, no dos. `set_config`
        # con `true` es LOCAL a la transacción — ver el aviso de arriba sobre por
        # qué un SET de sesión aquí sería veneno en el pooler compartido.
        cur.execute("select set_config('app.usuario', %s, true),"
                    "       set_config('app.origen',  %s, true)",
                    (quien or "", origen or ""))
    except Exception as exc:  # noqa: BLE001
        # Perder la atribución NO puede costar la operación. Se deshace la
        # transacción a medias (si no, la consulta de verdad heredaría el estado
        # abortado) y se sigue sin firma, que es como se venía trabajando.
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        log.warning("no se pudo marcar el actor '%s': %s", quien, exc)


@contextmanager
def get_cursor() -> Iterator[Any]:
    """Cursor de una conexión del POOL; se devuelve al pool al salir."""
    conn = _get_pool().connection()
    try:
        cur = conn.cursor()
        _marcar_actor(conn, cur)
        yield cur
        conn.commit()
    except Exception as exc:
        conn.rollback()
        # CANDADO HEREDADO. Si la conexión venía marcada de solo-lectura por
        # otro cliente del pooler, esta operación ya está perdida —pero la
        # SIGUIENTE no tiene por qué serlo—. Se destraban las conexiones aquí
        # mismo, así el reintento de la cola y todo lo que venga detrás pasan.
        # Sin esto, una sola conexión envenenada tumbaba escrituras durante
        # minutos: tres veces entre el 18 y el 19-ago-2026.
        # No se reintenta aquí porque el cuerpo del `with` ya corrió; el
        # reintento transparente vive en execute()/execute_returning().
        if _es_solo_lectura(exc):
            try:
                _desinfectar()
            except Exception:  # noqa: BLE001 — jamás tapa el error original
                pass
        raise
    finally:
        conn.close()  # con PooledDB, DEVUELVE la conexión al pool


# Las LECTURAS también se reintentan, pero solo por conexión muerta: el candado
# de solo-lectura no las afecta (leer es justo lo que sí deja hacer). Repetir un
# SELECT no tiene efectos secundarios, así que el reintento es gratis.
def fetch_all(sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
    def _hacer() -> list[dict[str, Any]]:
        with get_cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    return _reintentar_transitorio(_hacer)


def fetch_one(sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
    def _hacer() -> dict[str, Any] | None:
        with get_cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None
    return _reintentar_transitorio(_hacer)


def fetch_scalar(sql: str, params: tuple | dict | None = None) -> Any:
    row = fetch_one(sql, params)
    if not row:
        return None
    return next(iter(row.values()))


def _reintentar_transitorio(fn):
    """
    Corre `fn`; si truena por un fallo TRANSITORIO de la conexión, lo cura y
    reintenta UNA vez. Dos fallos distintos, misma respuesta:

      · CANDADO HEREDADO de solo-lectura → `_desinfectar()` y reintentar.
        Seguro: Postgres rechazó la sentencia ANTES de aplicarla, así que `fn`
        no llegó a escribir nada.

      · CONEXIÓN MUERTA que el pool entregó → `_reiniciar_pool()` y reintentar.
        Seguro por la misma razón: si la conexión estaba cerrada al tomarla, no
        se aplicó nada. Y si murió a media transacción, Postgres la deshace al
        perder la sesión — no queda media escritura.

    UNA sola vez. Si el segundo intento también truena, no es transitorio y el
    error sube: el llamador cae a MySQL y avisa, que es el comportamiento que ya
    existía.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        if _es_solo_lectura(exc):
            _desinfectar()
        elif _es_conexion_muerta(exc):
            log.warning("conexión muerta del pool; se reconstruye y reintenta: %s", exc)
            _reiniciar_pool()
        else:
            raise
        return fn()


# Nombre viejo: lo usaba el arreglo del candado (v0.215.0) antes de que el
# reintento cubriera también la conexión muerta.
_reintentar_si_solo_lectura = _reintentar_transitorio

# API PÚBLICA del reintento (28-ago-2026). El hueco que dejó la v0.285.0: el
# reintento solo envolvía execute()/execute_returning()/fetch_*(), pero los
# escritores por TANDA (channel_mirror, costing_mirror, core_write,
# costing_write) abren su cursor DIRECTO con `with sdb.get_cursor()` para
# encadenar set_config + upserts en una transacción — y por ahí el reintento
# nunca los vio. Resultado real: la alerta "Escritura de CHANNEL cayó a MySQL
# (tanda de 75): InterfaceError: connection already closed" volvió a sonar a
# las ~06:10 del 28-ago con la v0.285.0 YA desplegada. El reintento es seguro
# para esas tandas por la misma razón de siempre: todo el `with` es UNA
# transacción — si murió a medias, Postgres la deshizo, y el segundo intento
# parte de cero.
reintentar_transitorio = _reintentar_transitorio


def execute(sql: str, params: tuple | dict | None = None) -> int:
    """Ejecuta INSERT/UPDATE/DELETE. Devuelve filas afectadas (commit incluido).

    Con `INSERT ... ON CONFLICT DO NOTHING` el retorno distingue el resultado:
    1 = fila nueva insertada; 0 = era un duplicado (la base lo descartó) — es
    la base del conteo de webhooks duplicados sin lógica extra en el código.
    """
    def _hacer() -> int:
        with get_cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount
    return _reintentar_transitorio(_hacer)


def execute_returning(sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
    """Ejecuta una escritura con RETURNING y devuelve la fila (o None si no hubo)."""
    def _hacer() -> dict[str, Any] | None:
        with get_cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None
    return _reintentar_transitorio(_hacer)


def ping() -> bool:
    """Verifica conectividad con Supabase."""
    if not disponible():
        return False
    try:
        return fetch_scalar("SELECT 1") == 1
    except Exception as exc:  # noqa: BLE001
        log.warning("Supabase ping falló: %s", exc)
        return False
