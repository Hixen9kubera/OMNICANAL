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

import logging
from datetime import timezone
from typing import Any, Callable

from config import settings
from core import actor
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.orders_write")


def activo() -> bool:
    return settings.supabase_write_orders and sdb.disponible()


# ── LECTURAS DEL REGISTRO ─────────────────────────────────────────────────────
# Tras el corte, el registro de pedidos es channel.orders; `pedidos_ml` es el
# espejo. Los sondeos y el alta seguían PREGUNTÁNDOLE a MySQL, y el 12-ago-2026
# el paso 1 del desmantelamiento la congeló: las tres consultas empezaron a
# contestar desde una foto detenida.
#
#   · el candado de idempotencia devolvía SIEMPRE "no existe" → cada vuelta
#     creaba otro pedido en Woo: 964 fantasma en 4 h 17 min ($409,741), 85% de
#     todo lo creado en la ventana. Solo ML, porque Amazon no tuvo tráfico —
#     su marca de agua estaba igual de rota y se salvó por casualidad.
#   · el dedupe de los sondeos veía "nada cambió" al revés y reprocesaba.
#   · la marca de agua se quedaba fija, pidiendo siempre la misma ventana.
#
# Regla de la fuente: se lee de DONDE SE ESTÁ ESCRIBIENDO. Con kubera arriba,
# channel.orders; si kubera está caída, `guardar()` hace que MySQL absorba, así
# que ahí sí es la fresca. Nunca al revés.


def _mysql_previo(external_order_id: str) -> int | None:
    from services import db
    f = db.fetch_one("SELECT wc_order_id FROM pedidos_ml WHERE ml_order_id=%s",
                     (str(external_order_id),))
    return int(f["wc_order_id"]) if f and f.get("wc_order_id") else None


def wc_order_id_previo(external_order_id: str) -> int | None:
    """`wc_order_id` ya registrado para esta orden, o None si de verdad es nueva.

    Un None equivocado CREA un pedido duplicado, así que el error se propaga en
    vez de asumir "nueva": que el alta falle y se reintente es reparable; un
    fantasma en Woo, no.
    """
    if not sdb.disponible():
        return _mysql_previo(external_order_id)
    f = sdb.fetch_one(
        "select wc_order_id from channel.orders where external_order_id = %(id)s",
        {"id": str(external_order_id)})
    return int(f["wc_order_id"]) if f and f.get("wc_order_id") else None


def estados_wc(cuentas: tuple[str, ...]) -> dict[str, str]:
    """{ external_order_id: estado_wc } de esas cuentas — dedupe de los sondeos."""
    if not sdb.disponible():
        from services import db
        ph = ",".join(["%s"] * len(cuentas))
        return {f["ml_order_id"]: f["estado_wc"] for f in db.fetch_all(
            f"SELECT ml_order_id, estado_wc FROM pedidos_ml WHERE cuenta IN ({ph})",
            tuple(cuentas))}
    return {f["external_order_id"]: f["estado_wc"] for f in sdb.fetch_all(
        "select external_order_id, estado_wc from channel.orders "
        "where cuenta = any(%(c)s)", {"c": list(cuentas)})}


def ultimo_actualizado(cuenta: str):
    """Marca de agua del sondeo: último `actualizado_at` de la cuenta (naive UTC).

    None si la cuenta aún no tiene pedidos — el llamador decide su ventana
    inicial.
    """
    if not sdb.disponible():
        from services import db
        f = db.fetch_one(
            "SELECT MAX(actualizado) m FROM pedidos_ml WHERE cuenta=%s", (cuenta,))
        return (f or {}).get("m")
    f = sdb.fetch_one(
        "select max(actualizado_at) m from channel.orders where cuenta = %(c)s",
        {"c": cuenta})
    ts = (f or {}).get("m")
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts else None


def _en_hilo(fn: Callable, *args) -> None:
    # Delega en core.actor: `run_in_executor` NO se lleva los contextvars.
    actor.en_hilo(fn, *args)


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


def reclamar(canal: str, cuenta: str, external_order_id: str) -> bool:
    """
    Reserva el derecho a CREAR el pedido en Woo. True = lo ganamos nosotros.

    POR QUÉ. El candado de ráfaga (`pedidos_ml._locks`) vive en la memoria de UN
    proceso, así que no sirve en el relevo de contenedores de un deploy: el
    14-ago-2026 dos avisos de la orden 2000017937146172 cayeron uno en el
    proceso viejo y otro en el nuevo, con 3 segundos de diferencia y 1 segundo
    después del cambio. Cada uno se creyó el primero y Woo terminó con
    #123068 y #123069 — y como NO era FULL, la pieza se descontó dos veces.

    El registro anterior tampoco alcanzaba: se escribía DESPUÉS de crear en Woo,
    así que al proceso viejo lo mataron con el pedido ya creado y sin rastro en
    kubera. El nuevo no tenía cómo enterarse.

    Aquí el reclamo va ANTES, y es atómico: la PK (canal, cuenta,
    external_order_id) hace que solo un proceso pueda insertar la fila. El
    perdedor no crea; consulta. `wc_order_id` queda NULL hasta que el ganador
    complete — ese NULL es la señal de "reclamado, aún sin pedido".

    Si kubera no responde, devuelve True: es preferible arriesgar un duplicado
    (detectable y reparable) a perder la venta.
    """
    if not activo():
        return True
    try:
        fila = sdb.fetch_one(
            "insert into channel.orders (canal, cuenta, external_order_id, "
            "                            creado_at, actualizado_at) "
            "values (%(ca)s, %(cu)s, %(id)s, now(), now()) "
            "on conflict (canal, cuenta, external_order_id) do nothing "
            "returning external_order_id",
            {"ca": canal, "cu": cuenta, "id": str(external_order_id)})
        return fila is not None
    except Exception as exc:  # noqa: BLE001
        log.warning("reclamo de %s falló (%s); se sigue como si lo ganáramos: "
                    "perder la venta es peor que un duplicado reparable",
                    external_order_id, exc)
        return True


def liberar(canal: str, cuenta: str, external_order_id: str) -> None:
    """
    Suelta un reclamo que NO llegó a pedido (la creación en Woo falló).

    Sin esto, un fallo dejaría la fila con `wc_order_id` NULL para siempre y el
    siguiente aviso de esa orden vería "ya reclamada" y no la crearía nunca:
    la venta se perdería en silencio, que es peor que el duplicado que se
    intenta evitar. Solo borra si sigue sin pedido.
    """
    if not activo():
        return
    try:
        sdb.execute(
            "delete from channel.orders where canal = %(ca)s and cuenta = %(cu)s "
            "  and external_order_id = %(id)s and wc_order_id is null",
            {"ca": canal, "cu": cuenta, "id": str(external_order_id)})
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo liberar el reclamo de %s: %s",
                    external_order_id, exc)


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
    # Desmantelamiento (paso 1): sin espejo inverso, pedidos_ml queda congelada
    # a propósito. kubera ya guardó el pedido completo (encabezado + líneas).
    # El camino de emergencia de arriba NO depende de este flag.
    if not settings.orders_espejo_inverso:
        return
    _en_hilo(_espejo_inverso_mysql, clave, escribir_mysql)
