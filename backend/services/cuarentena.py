"""
cuarentena.py — El vigilante de cuarentena de la pestaña Inventario.

Pedido de Brandon (6-nov-2026): cuando bodega mueve un producto a la cuarentena
de Odoo, alguien tiene que enterarse y averiguar por qué (daño, devolución,
faltante). La pestaña Inventario avisa qué SKUs llegaron y cuántas piezas; un
popup enseña todo lo que hay hoy en cuarentena y el historial de cada SKU; la
primera persona que lo abre lo deja revisado PARA TODOS.

DE DÓNDE SALE CADA COSA
  · LLEGADA = renglón de `almacen.historial_movimientos` (la copia de Odoo de
    `services/almacen_odoo.py`) cuyo destino es la cuarentena y cuyo origen no,
    desde el 6-nov-2026 a las 12:00 CDMX. La cuarentena no es de ningún almacén:
    cada llegada aparece una sola vez, en el renglón del almacén de donde salió.
    Lo que llega desde fuera de los tres almacenes (un cliente, otro socio) no
    tiene renglón en el historial y no avisa; sí sale en el popup.
  · PENDIENTE = llegada sin fila en `almacen.cuarentena_revisadas` (0083).
  · LO QUE HAY HOY = los quants de Odoo en la ubicación de cuarentena, en vivo.

LO QUE NO HACE
  No escribe en Odoo, no toca stock, Woo ni canales. No avisa salidas de
  cuarentena. No cuenta productos archivados.

Todo aquí bloquea (psycopg2 y XML-RPC): quien lo llame desde una corrutina lo
manda a un hilo (regla 11).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from services import almacen_odoo as ao
from services import odoo as od

log = logging.getLogger("omnicanal.cuarentena")

UBICACION_ID = 470719
RUTA = "TEXCO/FERRAFORME/CUARENTENA"
DESDE = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)     # 6-nov-2026, 12:00 p.m. CDMX
TIMEOUT_ODOO = 30.0

_CAMPOS = ("h.odoo_move_line_id, h.sku::text as sku, h.fecha, h.piezas, h.odoo_origen as desde, "
           "h.documento, h.quien, h.cedis")
_ES_LLEGADA = "h.odoo_destino = %(ruta)s and h.odoo_origen <> %(ruta)s"


def pendientes(cur: Any) -> list[dict[str, Any]]:
    """Las llegadas sin revisar, por SKU: ``{sku, piezas, ultima, llegadas}``, de la
    llegada más reciente a la más vieja (también dentro de cada SKU)."""
    cur.execute(
        f"select distinct on (h.odoo_move_line_id) {_CAMPOS} "
        "  from almacen.historial_movimientos h "
        f" where {_ES_LLEGADA} and h.fecha >= %(desde)s "
        "   and not exists (select 1 from almacen.cuarentena_revisadas r "
        "                    where r.odoo_move_line_id = h.odoo_move_line_id) "
        " order by h.odoo_move_line_id, h.cedis",
        {"ruta": RUTA, "desde": DESDE})
    por_sku: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in cur.fetchall():
        por_sku[r["sku"]].append(dict(r))
    salida = []
    for sku, llegadas in por_sku.items():
        llegadas.sort(key=lambda x: x["fecha"], reverse=True)
        salida.append({"sku": sku, "piezas": sum(x["piezas"] for x in llegadas),
                       "ultima": llegadas[0]["fecha"], "llegadas": llegadas})
    salida.sort(key=lambda x: x["ultima"], reverse=True)
    return salida


def ultimas_llegadas(cur: Any, skus: list[str]) -> dict[str, dict[str, Any]]:
    """La llegada más reciente de cada SKU, de cualquier fecha (la del historial
    que ya se tiene). La llave es el SKU en mayúsculas."""
    if not skus:
        return {}
    cur.execute(
        f"select {_CAMPOS} from almacen.historial_movimientos h "
        f" where {_ES_LLEGADA} and h.sku = any(%(skus)s::citext[]) "
        " order by h.fecha desc",
        {"ruta": RUTA, "skus": list(skus)})
    salida: dict[str, dict[str, Any]] = {}
    for r in cur.fetchall():
        salida.setdefault(r["sku"].upper(), dict(r))
    return salida


def copia(cur: Any) -> list[dict[str, Any]]:
    """Qué tan al día está la copia del historial de Odoo, por almacén."""
    cur.execute("select c.almacen, a.nombre, c.historial_hasta, c.ultima_pasada_at, c.ultima_pasada_ok "
                "  from almacen.copias_odoo c join almacen.almacenes a on a.codigo = c.almacen "
                " order by c.almacen")
    return [dict(r) for r in cur.fetchall()]


def en_cuarentena() -> dict[str, Any]:
    """Lo que hay HOY en la cuarentena de Odoo: ``{ok, motivo, skus: [{sku, nombre,
    piezas}]}``, de más a menos piezas. Nunca lanza: si Odoo falla o la ubicación
    ya no es la que se espera, `ok=False` con el motivo."""
    def kw(modelo: str, metodo: str, args: list[Any], kwargs: dict[str, Any] | None = None) -> Any:
        return od._kw_flujo(modelo, metodo, args, kwargs, timeout=TIMEOUT_ODOO)

    try:
        ubi = kw("stock.location", "read", [[UBICACION_ID]], {"fields": ["complete_name"]})
        nombre = (ubi[0].get("complete_name") or "") if ubi else ""
        if nombre != RUTA:
            return {"ok": False, "skus": [],
                    "motivo": (f"La ubicación {UBICACION_ID} de Odoo ahora se llama «{nombre}»" if nombre
                               else f"Odoo ya no tiene la ubicación {UBICACION_ID}")
                              + f"; el vigilante busca «{RUTA}». Hay que revisar cuál es la cuarentena."}
        quants = kw("stock.quant", "search_read",
                    [[["location_id", "child_of", UBICACION_ID], ["quantity", ">", 0]]],
                    {"fields": ["product_id", "quantity"]})
        ids = sorted({q["product_id"][0] for q in quants})
        # Sin `active in [True, False]`: Odoo calla los archivados, que no se cuentan.
        productos = {p["id"]: p for p in kw("product.product", "search_read", [[["id", "in", ids]]],
                                             {"fields": ["default_code", "name"]})} if ids else {}
    except Exception as exc:  # noqa: BLE001 — el popup sigue con lo que hay en kubera
        log.warning("Cuarentena: no se pudo leer Odoo (%s).", type(exc).__name__)
        return {"ok": False, "skus": [], "motivo": f"No se pudo leer Odoo ({type(exc).__name__})."}
    piezas: dict[str, float] = defaultdict(float)
    nombres: dict[str, str] = {}
    for q in quants:
        p = productos.get(q["product_id"][0])
        sku = ao.sku_de(p) if p else None
        if not sku:
            continue
        piezas[sku] += q["quantity"]
        nombres.setdefault(sku, p.get("name") or "")
    skus = [{"sku": s, "nombre": nombres[s], "piezas": int(round(v))} for s, v in piezas.items()]
    skus.sort(key=lambda x: (-x["piezas"], x["sku"]))
    return {"ok": True, "motivo": None, "skus": skus}


def armar_popup(pend: list[dict[str, Any]], en_cuar: list[dict[str, Any]],
                ultimas: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Los renglones del popup: ``{sku, nombre, piezas, nuevo, llegada, llegadas}``.
    Primero los NUEVOS (pendientes), de la llegada más reciente a la más vieja;
    luego los demás igual, y al final los que no tienen llegada en el historial.
    Un pendiente que ya salió de cuarentena se enseña con 0 piezas."""
    por_sku = {p["sku"].upper(): p for p in pend}
    filas = []
    for s in en_cuar:
        p = por_sku.pop(s["sku"].upper(), None)
        filas.append({"sku": s["sku"], "nombre": s["nombre"], "piezas": s["piezas"], "nuevo": p is not None,
                      "llegada": p["llegadas"][0] if p else ultimas.get(s["sku"].upper()),
                      "llegadas": p["llegadas"] if p else []})
    for p in por_sku.values():
        filas.append({"sku": p["sku"], "nombre": "", "piezas": 0, "nuevo": True,
                      "llegada": p["llegadas"][0], "llegadas": p["llegadas"]})
    viejo = datetime.min.replace(tzinfo=timezone.utc)
    filas.sort(key=lambda f: (not f["nuevo"], f["llegada"] is None,
                              -(f["llegada"]["fecha"] if f["llegada"] else viejo).timestamp(),
                              -f["piezas"]))
    return filas


def revisar(cur: Any, ids: list[int], quien: str) -> int:
    """Deja como revisadas las llegadas dadas (las que se le enseñaron a `quien`).
    Solo entran las que de verdad son llegadas desde `DESDE`; las ya revisadas se
    ignoran. Devuelve cuántas se guardaron."""
    ids = sorted({int(i) for i in ids})
    if not ids:
        return 0
    cur.execute(
        "insert into almacen.cuarentena_revisadas (odoo_move_line_id, revisado_por) "
        "select distinct h.odoo_move_line_id, %(quien)s from almacen.historial_movimientos h "
        f" where h.odoo_move_line_id = any(%(ids)s::bigint[]) and {_ES_LLEGADA} and h.fecha >= %(desde)s "
        "on conflict (odoo_move_line_id) do nothing",
        {"quien": quien, "ids": ids, "ruta": RUTA, "desde": DESDE})
    return max(cur.rowcount, 0)
