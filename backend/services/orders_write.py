"""
orders_write.py — CORTE F6 del dominio PEDIDOS (opción A: espejo inverso).

Con SUPABASE_WRITE_ORDERS=true, el registro de cada venta (pedidos_ml.sincronizar,
por donde pasan ML, Amazon y Temu/TikTok) escribe PRIMERO en la BD kubera:
channel.orders + channel.order_items en UNA transacción, reutilizando los
upserts del seam (kubera_mirror._up_channel_orders/_up_channel_order_items —
la misma semántica que validó la racha del acta: estados se mueven, importes
congelados, 0 → valor real solo una vez).

MySQL (pedidos_ml) pasa a ser el ESPEJO INVERSO: se escribe después, en hilo y
best-effort — un fallo de MySQL jamás rompe el registro (log + issue + Slack).
El fallback de lectura F5 del tab Ventas sigue teniendo datos frescos.

RESILIENCIA (kubera caída): el negocio no se bloquea — se escribe MySQL como
en el mundo viejo y los DOS payloads viajan por el espejo clásico
(kubera_mirror.espejar → workers → espejo_kubera_log si kubera sigue caída,
reprocesable desde /migracion). Slack avisa del fallback.

Las LÍNEAS respetan el censo del espejo: solo se escriben si
`pedidos_ml_items` está en KUBERA_MIRROR_TABLAS (el mismo interruptor de
producción de siempre — el corte no enciende flujos que el censo tenga
apagados).

Revertir = SUPABASE_WRITE_ORDERS=false (vuelve el dual-write clásico).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from config import settings
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.orders_write")


def activo() -> bool:
    return settings.supabase_write_orders and sdb.disponible()


def _en_hilo(fn: Callable, *args) -> None:
    try:
        asyncio.get_running_loop().run_in_executor(None, fn, *args)
    except RuntimeError:
        fn(*args)


def _espejo_inverso_mysql(clave: str, escribir_mysql: Callable[[], None]) -> None:
    try:
        escribir_mysql()
    except Exception as exc:  # noqa: BLE001
        log.warning("espejo inverso MySQL pedidos_ml(%s) falló (la operación "
                    "continúa): %s", clave, exc)
        try:
            sdb.execute(
                "insert into ops.migration_issues (fase, tabla_origen, sku, motivo) "
                "values ('F6-corte-orders', 'pedidos_ml', %s, %s)",
                (clave[:100], f"espejo inverso MySQL fallo: {exc}"[:500]))
        except Exception:  # noqa: BLE001
            pass
        try:
            from services import alertas
            alertas.avisar(
                "espejo_inverso:orders",
                f"*Espejo inverso de PEDIDOS a MySQL falló* ({clave}): "
                f"{type(exc).__name__}: {str(exc)[:140]}. kubera SÍ guardó; "
                f"pedidos_ml quedará desfasado hasta el acta.")
        except Exception:  # noqa: BLE001
            pass


def guardar(origen_py: str, funcion: str, encabezado: dict[str, Any],
            lineas: dict[str, Any], clave: str,
            escribir_mysql: Callable[[], None]) -> None:
    """Registro del pedido bajo el corte: kubera primaria, MySQL espejo."""
    from services import kubera_mirror
    con_lineas = kubera_mirror.activo("pedidos_ml_items")
    try:
        with sdb.get_cursor() as cur:
            cur.execute("select set_config('statement_timeout', '4000', true)")
            cur.execute("select set_config('app.via', 'corte_orders', true)")
            kubera_mirror._up_channel_orders(cur, encabezado)
            if con_lineas:
                kubera_mirror._up_channel_order_items(cur, lineas)
    except Exception as exc:  # noqa: BLE001
        log.warning("escritura primaria kubera channel.orders(%s) falló — MySQL "
                    "aguanta y el evento viaja por el espejo: %s", clave, exc)
        escribir_mysql()  # si esto también truena, el error sube al llamador
        kubera_mirror.espejar(origen_py, f"{funcion} (corte→espejo)", "pedidos_ml",
                              "channel.orders", "UPSERT", encabezado, clave=clave)
        kubera_mirror.espejar(origen_py, f"{funcion} (corte→espejo líneas)",
                              "pedidos_ml_items", "channel.order_items", "UPSERT",
                              lineas, clave=clave)
        try:
            from services import alertas
            alertas.avisar(
                "escritura_fallback:orders",
                f"⚠️ Escritura de PEDIDOS cayó a MySQL ({clave}): "
                f"{type(exc).__name__}: {str(exc)[:140]}. El evento kubera viaja "
                f"por el espejo/cola.")
        except Exception:  # noqa: BLE001
            pass
        return
    _en_hilo(_espejo_inverso_mysql, clave, escribir_mysql)
