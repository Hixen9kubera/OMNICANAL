"""
fulfillment_sku.py — La ficha de UN SKU en FULL: la ventana que se abre al
tocar un SKU en el detalle de un envío (Brandon, 24-sep-2026: "el por SKU o MLM
es solamente un POP").

LECTURA PURA: kubera (publicaciones, ventas FULL, avisos de llegada) y Odoo (lo
libre por almacén). Los envíos salen de la lectura de la pestaña, ya en caché.

Todo lo que pinta es DATO, no diseño:
  · la publicación de ML de cada cuenta, con su stock en FULL de hoy;
  · la venta FULL por semana (12 semanas) y el ritmo de 30 días;
  · cada aviso de llegada a FULL (una vez por operación, como el rail);
  · cada envío de Odoo que llevó ese SKU, con lo que llegó y lo que no;
  · lo libre en Odoo, por almacén, en este momento.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from services import odoo_ventas
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.fulfillment_sku")

_CDMX = timezone(timedelta(hours=-6))
_NOMBRE = {"BEKURA": "Kubera", "SANCORFASHION": "San Corpe"}
SEMANAS = 12

_SQL_PUBLICACIONES = """
    select a.legacy_code cuenta, l.listing_id, l.url, l.situacion, l.is_fulfillment,
           l.logistic_type, coalesce(l.stock_full, 0)::int stock_full, l.price, l.updated_at
      from channel.listings l join core.accounts a on a.id = l.account_id
     where l.sku::text = %(sku)s and l.canal = 'mercado_libre'
       and a.legacy_code in ('BEKURA', 'SANCORFASHION')
     order by l.is_fulfillment desc nulls last, l.updated_at desc
"""

_SQL_VENTAS = """
    select cuenta, date, sum(units_sold)::int unidades
      from channel.sales_daily_completa
     where sku::text = %(sku)s and canal = 'mercado_libre' and is_full
       and cuenta in ('BEKURA', 'SANCORFASHION') and date >= %(desde)s
     group by 1, 2
     order by 2
"""

# Una vez por operación de ML (ML reenvía el mismo aviso: ver fulfillment_etapas).
_SQL_LLEGADAS = r"""
    select cuenta, ts, tipo, n
      from (select distinct on (item_id) cuenta, ts,
                   (regexp_match(resultado, '^(TRANSFER_DELIVERY|INBOUND_RECEPTION) x\d+'))[1] tipo,
                   (regexp_match(resultado, '^(?:TRANSFER_DELIVERY|INBOUND_RECEPTION) x(\d+)'))[1]::int n
              from ops.fanout_log
             where motivo = 'movimiento FULL/FBA' and sku::text = %(sku)s
               and cuenta in ('BEKURA', 'SANCORFASHION') and ts >= %(desde)s
               and (resultado like 'TRANSFER_DELIVERY%%' or resultado like 'INBOUND_RECEPTION%%')
             order by item_id, ts) t
     where n is not null
     order by ts desc
"""


def semanas_de_venta(filas: list[dict[str, Any]], hoy: date, n: int = SEMANAS) -> dict[str, list[dict[str, Any]]]:
    """Unidades FULL por semana ISO (CDMX), SEGUIDAS: una semana sin venta es un cero."""
    lunes_hoy = hoy - timedelta(days=hoy.weekday())
    lunes = [lunes_hoy - timedelta(weeks=i) for i in range(n - 1, -1, -1)]
    salida: dict[str, list[dict[str, Any]]] = {}
    por_cuenta: dict[str, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    for f in filas:
        d = f["date"]
        por_cuenta[f["cuenta"]][d - timedelta(days=d.weekday())] += int(f["unidades"] or 0)
    for codigo, legible in _NOMBRE.items():
        salida[legible] = [{"semana": f"S{l.isocalendar()[1]}", "lunes": l.isoformat(),
                            "unidades": por_cuenta[codigo].get(l, 0), "actual": l == lunes_hoy}
                           for l in lunes]
    return salida


def envios_del_sku(sku: str, envios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cada salida de Odoo a FULL que llevó el SKU, con lo que ML avisó de él."""
    salida = []
    for e in envios:
        if e.get("canal") != "meli":
            continue
        for r in e.get("lineas") or []:
            if r.get("sku") != sku:
                continue
            salida.append({
                "id": e.get("id"), "orden": e.get("orden"), "salida": e.get("salida"),
                "envio": e.get("envio"), "cuenta": e.get("cuenta"), "estado_odoo": e.get("estado_odoo"),
                "creada": (e["etapas"][0] or {}).get("ts"), "validada": (e["etapas"][1] or {}).get("ts"),
                "pedidas": r.get("pedidas"), "enviadas": r.get("enviadas"),
                "llegadas": r.get("llegadas"), "estado_llegada": r.get("estado_llegada"),
                "rechazadas": r.get("rechazadas"), "llegada": r.get("llegada"),
                "primera_venta": r.get("primera_venta"), "cerrado": (e.get("cobertura") or {}).get("cerrado"),
            })
    salida.sort(key=lambda x: x["creada"] or "", reverse=True)
    return salida


def ficha(sku: str, envios: list[dict[str, Any]], ahora: datetime | None = None) -> dict[str, Any]:
    """BLOQUEA (kubera + Odoo): el router la corre en un hilo."""
    ahora = ahora or datetime.now(timezone.utc)
    sku = (sku or "").strip()
    hoy = ahora.astimezone(_CDMX).date()
    desde_ventas = hoy - timedelta(days=7 * SEMANAS + 7)
    publicaciones = [{**p, "cuenta": _NOMBRE.get(p["cuenta"], p["cuenta"]),
                      "price": float(p["price"]) if p.get("price") is not None else None,
                      "updated_at": p["updated_at"].isoformat() if p.get("updated_at") else None}
                     for p in sdb.fetch_all(_SQL_PUBLICACIONES, {"sku": sku})]
    ventas = sdb.fetch_all(_SQL_VENTAS, {"sku": sku, "desde": desde_ventas})
    v30: dict[str, int] = {n: 0 for n in _NOMBRE.values()}
    for f in ventas:
        if hoy - timedelta(days=30) <= f["date"] < hoy:
            v30[_NOMBRE[f["cuenta"]]] += int(f["unidades"] or 0)
    llegadas = [{"cuenta": _NOMBRE.get(f["cuenta"], f["cuenta"]), "ts": f["ts"].isoformat(),
                 "tipo": f["tipo"], "piezas": int(f["n"])}
                for f in sdb.fetch_all(_SQL_LLEGADAS, {"sku": sku, "desde": ahora - timedelta(days=120)})]

    odoo: dict[str, Any] | None = None
    try:
        prods = odoo_ventas.productos_por_sku([sku])
        p = prods.get(sku)
        if p:
            libres = odoo_ventas.libre_por_almacen([p["id"]]).get(p["id"], {})
            odoo = {"nombre": p.get("name"),
                    "libre": {nombre: max(0, int(libres.get(wid, 0) or 0)) for wid, nombre in odoo_ventas._ALMACENES}}
        else:
            odoo = {"nombre": None, "libre": None}
    except Exception as exc:  # noqa: BLE001 — sin Odoo la ficha sigue: se dice
        log.warning("ficha de %s: Odoo no contestó (%s)", sku, exc)

    return {
        "sku": sku, "nombre": (odoo or {}).get("nombre"), "generado": ahora.isoformat(),
        "publicaciones": publicaciones,
        "ventas_semanas": semanas_de_venta(ventas, hoy),
        "v30": v30,
        "llegadas": llegadas,
        "envios": envios_del_sku(sku, envios),
        "odoo": odoo,
        "fuente": ("channel.listings (publicaciones y stock en FULL) · channel.sales_daily_completa "
                   "(ventas FULL) · ops.fanout_log (avisos de llegada de ML, una vez por operación) · "
                   "Odoo (envíos y libre por almacén)"),
    }
