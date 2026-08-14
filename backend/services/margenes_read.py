"""
margenes_read.py — Las tres cachés del tab de Márgenes, en la BD kubera.

Gemelas de las lecturas y escrituras que `envio_real.py`, `ficha_ml.py` y
`visitas_ml.py` hacían contra MySQL. Traducción de nombres:

    ml_envio_real  → enrich.order_shipping_cost
    ml_ficha       → enrich.listing_weight
    ml_visitas     → enrich.listing_visits

Cada función devuelve EXACTAMENTE la misma forma que su gemela MySQL, para que
el llamador no cambie más que la línea de la consulta.

DOS COSAS QUE NO SON COSMÉTICAS
-------------------------------

1. **Todo aquí es BLOQUEANTE** (psycopg2). Ninguna de estas funciones se llama
   desde una corrutina sin `asyncio.to_thread`. La regla 11 de la casa nació
   del apagón del 13-ago: `sdb.*` llamado directo dentro de un `async def`
   detiene el backend ENTERO mientras Postgres contesta, no solo a quien llamó.
   Los tres servicios ya envuelven sus llamadas; al repuntarlos hay que
   conservar ese envoltorio.

2. **`consultado_at` NULO no existe, la FILA ausente sí.** En las tres tablas,
   una fila con el valor en NULL significa "ya le pregunté a ML y no tiene
   dato" (un envío FULL sin cargo, una publicación sin peso medido). Que no
   haya fila significa "nunca pregunté". Confundirlos hace que la página
   re-consulte en cada carga — y ML acepta UN ítem por llamada en visitas y en
   costos de envío, así que eso se paga caro.
"""
from __future__ import annotations

from datetime import timezone
from typing import Any

from services import supabase_db as sdb


# ── EL CONTRATO INCLUYE EL TIPO DE LA FECHA ────────────────────────────────
#
# En MySQL `consultado_at` era DATETIME → naive. En kubera es `timestamptz` →
# psycopg2 lo devuelve CON ZONA. Los tres consumidores calculan su TTL contra
# `datetime.utcnow()`, que es naive, y comparar aware con naive **lanza
# TypeError** en Python.
#
# Eso ya pasó en producción el 14-ago, el día que se encendió la lectura:
#
#   [WARNING] visitas no disponibles en la tabla:
#             can't compare offset-naive and offset-aware datetimes
#
# La columna «Visitas · CR%» de Análisis se vació entera y nadie se enteró por
# una excepción: el `try` del llamador la tragaba y solo quedaba el warning.
#
# Los otros dos NO fallaron, pero por casualidad y no por diseño:
#   · `envio_real` compara detrás de `costo_vendedor is None and …`, y HOY hay
#     0 filas con costo nulo, así que el `and` corta antes. La primera que
#     llegue con NULL lo dispara.
#   · `ficha_ml.completar` sí llega a la comparación, pero corre en un
#     `create_task`: falla EN SEGUNDO PLANO, sin warning visible, y la marca de
#     peso deja de converger en silencio.
#
# Por eso se normaliza AQUÍ y no en cada comparación. El docstring de este
# módulo promete "EXACTAMENTE la misma forma que su gemela MySQL" — esto es lo
# que hace que sea cierto. Parchear los tres llamadores también funcionaría,
# pero dejaría la trampa puesta para las 28 tablas que faltan del instructivo:
# cada `timestamptz` que se migre traería el mismo defecto.
def _naive_utc(fila: dict[str, Any]) -> dict[str, Any]:
    """`consultado_at` a UTC SIN zona, como lo daba MySQL."""
    v = fila.get("consultado_at")
    if v is not None and getattr(v, "tzinfo", None) is not None:
        fila["consultado_at"] = v.astimezone(timezone.utc).replace(tzinfo=None)
    return fila


# ── Costo real de envío, por pedido ─────────────────────────────────────────

def envio_leer(pares: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    """{ (cuenta, external_order_id): fila } — gemela de `envio_real.leer`."""
    if not pares:
        return {}
    res: dict[tuple[str, str], dict[str, Any]] = {}
    por_cuenta: dict[str, list[str]] = {}
    for cuenta, oid in pares:
        por_cuenta.setdefault(cuenta, []).append(str(oid))
    for cuenta, ids in por_cuenta.items():
        for f in sdb.fetch_all(
            """select external_order_id, shipment_id, costo_vendedor, consultado_at
                 from enrich.order_shipping_cost
                where cuenta = %s and external_order_id = any(%s)""",
                (cuenta, ids)):
            res[(cuenta, str(f["external_order_id"]))] = _naive_utc(f)
    return res


def envio_guardar(resultados: list[tuple[str, str, str | None, Any]]) -> int:
    """(cuenta, external_order_id, shipment_id, costo) → upsert. Un solo viaje.

    `coalesce` en los dos valores: un reintento que vuelva a traer NULL nunca
    pisa un costo real ya guardado. Mismo patrón que la comisión 0→valor de
    pedidos, y por la misma razón — ML devuelve vacío mientras el envío está en
    tránsito y luego sí trae el número.
    """
    if not resultados:
        return 0
    vals = ", ".join(["(%s, %s, %s, %s, now())"] * len(resultados))
    params: list[Any] = []
    for cuenta, oid, sid, costo in resultados:
        params += [cuenta, str(oid), sid, costo]
    sdb.execute(
        f"""insert into enrich.order_shipping_cost
              (cuenta, external_order_id, shipment_id, costo_vendedor, consultado_at)
            values {vals}
            on conflict (cuenta, external_order_id) do update set
              shipment_id    = coalesce(excluded.shipment_id, order_shipping_cost.shipment_id),
              costo_vendedor = coalesce(excluded.costo_vendedor, order_shipping_cost.costo_vendedor),
              consultado_at  = now()""",
        tuple(params))
    return len(resultados)


# ── Peso medido por la bodega de ML ─────────────────────────────────────────

def ficha_leer(listing_ids: list[str]) -> dict[str, dict[str, Any]]:
    """{ listing_id: fila } — gemela de `ficha_ml.leer`."""
    if not listing_ids:
        return {}
    return {str(f["listing_id"]): _naive_utc(f) for f in sdb.fetch_all(
        """select listing_id, cuenta, titulo, peso_g, medido, consultado_at
             from enrich.listing_weight where listing_id = any(%s)""",
        ([str(i) for i in listing_ids],))}


def ficha_guardar(filas: list[tuple]) -> int:
    """(listing_id, cuenta, titulo, peso_g, medido) → upsert."""
    if not filas:
        return 0
    vals = ", ".join(["(%s, %s, %s, %s, %s, now())"] * len(filas))
    params: list[Any] = []
    for f in filas:
        params += list(f)
    sdb.execute(
        f"""insert into enrich.listing_weight
              (listing_id, cuenta, titulo, peso_g, medido, consultado_at)
            values {vals}
            on conflict (listing_id) do update set
              cuenta        = coalesce(excluded.cuenta, listing_weight.cuenta),
              titulo        = coalesce(excluded.titulo, listing_weight.titulo),
              peso_g        = coalesce(excluded.peso_g, listing_weight.peso_g),
              -- `medido` NO lleva coalesce: es un booleano y false es
              -- informativo ("ML no lo ha pesado"), no ausencia de dato.
              medido        = excluded.medido,
              consultado_at = now()""",
        tuple(params))
    return len(filas)


# ── Visitas por publicación y ventana ───────────────────────────────────────

def visitas_leer(listing_ids: list[str], dias: int) -> dict[str, dict[str, Any]]:
    """{ listing_id: fila } para esa ventana — gemela de `visitas_ml.leer`."""
    if not listing_ids:
        return {}
    return {str(f["listing_id"]): _naive_utc(f) for f in sdb.fetch_all(
        """select listing_id, visitas, dias_datos, consultado_at
             from enrich.listing_visits
            where dias = %s and listing_id = any(%s)""",
        (int(dias), [str(i) for i in listing_ids]))}


def visitas_guardar(filas: list[tuple]) -> int:
    """(listing_id, dias, cuenta, visitas, dias_datos) → upsert."""
    if not filas:
        return 0
    vals = ", ".join(["(%s, %s, %s, %s, %s, now())"] * len(filas))
    params: list[Any] = []
    for f in filas:
        params += list(f)
    sdb.execute(
        f"""insert into enrich.listing_visits
              (listing_id, dias, cuenta, visitas, dias_datos, consultado_at)
            values {vals}
            on conflict (listing_id, dias) do update set
              cuenta        = coalesce(excluded.cuenta, listing_visits.cuenta),
              visitas       = coalesce(excluded.visitas, listing_visits.visitas),
              dias_datos    = coalesce(excluded.dias_datos, listing_visits.dias_datos),
              consultado_at = now()""",
        tuple(params))
    return len(filas)
