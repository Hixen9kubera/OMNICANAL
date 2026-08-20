"""
fanout_read.py — La bitácora del fan-out, del lado de kubera.

Gemelas de las CUATRO lecturas de `fanout_log` que no deciden nada pero pintan
las pantallas del fan-out. Las otras dos —las que sí deciden— viven en
`candados_read` y van por otra bandera, a propósito: mover una pantalla no
necesita el dale de Brandon; mover mercancía sí.

Esa separación ES el hallazgo. `fanout_log` guardaba la marca de idempotencia y
el historial en la misma tabla, y la migración 0022 se llevó solo la marca. El
censo del 20-ago encontró los cuatro lectores huérfanos.

QUÉ SE CONSERVA AUNQUE DUELA
----------------------------
`resultado` es texto libre, y hay dos lectores que le sacan datos con `LIKE` y
partiéndolo por espacios. Es frágil — misma familia que el regex del FBA que se
mató en el paso 0 — pero **un repunte tiene que contestar IGUAL, no mejor**.
Cambiar la forma del dato aquí sería arreglar dos cosas a la vez y no poder decir
cuál rompió qué. Queda anotado como trabajo aparte.
"""
from __future__ import annotations

from typing import Any

from services import supabase_db as sdb

_COLS = ("ts, sku::text as sku, motivo, dry_run, stock_drop, objetivo, canal, "
         "cuenta, item_id, accion, stock_canal, resultado, ms")


def historial(limite: int = 100, solo_errores: bool = False) -> list[dict[str, Any]]:
    """Gemela de `fanout_stock.historial`. Lo que pinta el dashboard."""
    # El `%` va DOBLE. Con parametros, psycopg2 lee `%` como el inicio de un
    # hueco y `'ERROR%'` queda como un placeholder roto: la consulta no truena,
    # devuelve CERO. `resumen()` no lo sufre porque va SIN parametros — el mismo
    # LIKE funcionando en un lado y fallando en el otro fue lo que lo delato.
    donde = "where resultado like 'ERROR%%'" if solo_errores else ""
    return sdb.fetch_all(
        f"select {_COLS} from ops.fanout_log {donde} order by id desc limit %s",
        (int(limite),))


def resumen() -> dict[str, Any]:
    """Gemela de `fanout_stock.resumen`. Totales desde la tabla, no de memoria."""
    por_accion = sdb.fetch_all(
        "select accion, count(*) as n from ops.fanout_log group by accion")
    por_canal = sdb.fetch_all(
        """select canal, accion, count(*) as n from ops.fanout_log
            where canal is not null group by canal, accion""")
    tot = sdb.fetch_one(
        """select count(*) as eventos, count(distinct sku) as skus,
                  min(ts) as desde, max(ts) as hasta,
                  count(*) filter (where resultado like 'ERROR%') as errores
             from ops.fanout_log""") or {}
    return {"por_accion": {r["accion"]: r["n"] for r in por_accion},
            "por_canal": por_canal, **tot}


def movimientos_full(horas: int) -> list[dict[str, Any]]:
    """Gemela de `routers/fanout.py::full_observacion`.

    El llamador saca el TIPO de movimiento de ML del inicio de `resultado`
    ("TRANSFER_DELIVERY x2: …"). Se devuelve el texto igual que en MySQL para
    que ese parseo siga funcionando sin tocarlo.
    """
    return sdb.fetch_all(
        """select accion, resultado, sku::text as sku, cuenta, stock_drop,
                  objetivo, ts
             from ops.fanout_log
            where (accion like 'full\\_%%' or accion like 'fba\\_%%')
              and ts >= now() - make_interval(hours => %s)
            order by id desc""", (int(horas),))


# Las acciones del vigilante de inventario, tal cual las lista el router.
_ACCIONES_INVENTARIO = ("odoo_delta", "odoo_delta_registro", "woo_cambio",
                        "woo_cambio_registro", "stock_watch_freno")


def pendientes_inventario(limite: int) -> list[dict[str, Any]]:
    """Gemela de `routers/fanout.py::inventario_pendientes`."""
    return sdb.fetch_all(
        """select ts, sku::text as sku, accion, motivo, resultado, dry_run
             from ops.fanout_log where accion = any(%s)
            order by id desc limit %s""",
        (list(_ACCIONES_INVENTARIO), int(limite)))


def registrar(fila: dict[str, Any]) -> int:
    """Escribe un evento. Lo llama el espejo de `fanout_stock._persistir`.

    Sin `on conflict`: la bitácora no tiene llave natural — dos intentos del
    mismo SKU con el mismo resultado son DOS eventos, no uno repetido. Poner una
    llave aquí perdería justo lo que se quiere ver.
    """
    return sdb.execute(
        """insert into ops.fanout_log
             (ts, sku, motivo, dry_run, stock_drop, objetivo, canal, cuenta,
              item_id, accion, stock_canal, resultado, ms)
           values (coalesce(%(ts)s, now()), %(sku)s, %(motivo)s,
                   coalesce(%(dry_run)s, false), %(stock_drop)s, %(objetivo)s,
                   %(canal)s, %(cuenta)s, %(item_id)s, %(accion)s,
                   %(stock_canal)s, %(resultado)s, %(ms)s)""",
        {k: fila.get(k) for k in
         ("ts", "sku", "motivo", "dry_run", "stock_drop", "objetivo", "canal",
          "cuenta", "item_id", "accion", "stock_canal", "resultado", "ms")})


def censo() -> dict[str, Any]:
    """Para el arnés de paridad."""
    return sdb.fetch_one(
        """select count(*) as filas, count(distinct sku) as skus,
                  min(ts) as desde, max(ts) as hasta
             from ops.fanout_log""") or {}
