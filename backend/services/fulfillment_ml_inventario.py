"""
fulfillment_ml_inventario.py — Qué pasó con las piezas que «faltan» de un envío a
FULL, preguntándole a Mercado Libre por el inventario de esos SKUs.

POR QUÉ EXISTE. La llegada se mide con los avisos de FULL (`fulfillment_etapas`):
TRANSFER_DELIVERY / INBOUND_RECEPTION por SKU. Eso deja dos huecos, medidos el
2-oct-2026 con TEXCO/OUT/06483 (S38849, San Corpe), al que le «faltaban» 37 pzs:
  · 26 SÍ estaban en la bodega de ML, pero en RETIRO (ACC-0441-PLA-21PZ 25 y
    TEC-0796-NEG 1): ML las recibió y nunca las puso a la venta; el 29-sep a las
    20:59 alguien pidió un retiro. Al cerrar el envío el panel las habría dado por
    «no recibidas».
  · 10 SÍ llegaron (TEC-0107-RO-NE-4CE 9, TEC-1375-MET 1), pero parte de la
    recepción vino en avisos de AJUSTE, que no cuentan como llegada. El stock de
    ML más lo vendido desde la llegada lo confirma.
  · 1 no aparece (TEC-0383-MET: 30 enviadas, 29 llegadas y vendidas, 0 en FULL):
    ésa sí es para reclamar.

QUÉ HACE. Para un envío ya validado con renglones a los que les falta algo, lee
EN VIVO (sólo GET) el inventario FULL de cada SKU en su cuenta —
`/items` → `inventory_id` → `/inventories/{id}/stock/fulfillment`: vendible, no
vendible y su motivo (withdrawal = retiro, damaged, lost…)— y lo cruza con lo
vendido por FULL desde la llegada y con el stock que ya había antes del envío.
Contesta UN veredicto por renglón:
  · en_retiro         ML tiene lo que falta apartado para retiro: regresa a bodega
                      y entra por Odoo con una recepción.
  · no_vendible       ML lo tiene, pero no vendible por otro motivo (daño, etc.).
  · llego_sin_aviso   lo que hay en FULL + lo vendido − lo que ya había ≥ lo
                      enviado: llegó todo; los avisos no lo contaron.
  · no_aparece        ML no lo tiene ni lo vendió: es para reclamar.
  · sin_publicacion   el SKU no tiene publicación FULL en esa cuenta.
  · sin_dato          ML no contestó.
Si después salió OTRA orden del mismo SKU a la misma cuenta, el stock de ML ya
mezcla las dos y el veredicto va marcado `aprox`.

Nada se guarda: cada consulta es en vivo (con 10 min de memoria por publicación).
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import httpx

from services import fulfillment_etapas, full_publicaciones
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.fulfillment_ml_inventario")

_ML = "https://api.mercadolibre.com"
_CODIGO = {"Kubera": "BEKURA", "San Corpe": "SANCORFASHION"}
_TTL_S = 600
_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()

TEXTO = {
    "en_retiro": "ML las tiene en retiro: regresan a bodega y entran por Odoo con una recepción",
    "no_vendible": "ML las tiene, pero no vendibles",
    "llego_sin_aviso": "llegaron: lo que hay en FULL más lo vendido cubre lo enviado (los avisos no las contaron)",
    "no_aparece": "ML no las tiene ni las vendió: para reclamar",
    "sin_publicacion": "el SKU no tiene publicación FULL en esta cuenta",
    "sin_dato": "Mercado Libre no contestó",
}


# ── La decisión (pura: se prueba sin ML ni kubera) ──────────────────────────

def veredicto(enviadas: int, llegadas: int, inv: dict[str, Any] | None, vendidas: int,
              antes: int | None) -> dict[str, Any]:
    """El veredicto de UN renglón. `inv`: {"disponible", "no_disponible", "detalle": {motivo: n}};
    `vendidas`: ventas FULL del SKU desde la llegada; `antes`: stock FULL antes del envío."""
    faltan = max(0, int(enviadas) - int(llegadas))
    if inv is None:
        return {"veredicto": "sin_dato", "faltan": faltan}
    detalle = {k: int(v) for k, v in (inv.get("detalle") or {}).items() if v}
    retiro = detalle.get("withdrawal", 0)
    otros = sum(v for k, v in detalle.items() if k != "withdrawal")
    disponible = int(inv.get("disponible") or 0)
    base = {"faltan": faltan, "disponible": disponible, "retiro": retiro, "otros": otros,
            "detalle": detalle, "vendidas": int(vendidas), "antes": antes}
    if faltan and retiro >= faltan:
        return {**base, "veredicto": "en_retiro"}
    if faltan and retiro + otros >= faltan:
        return {**base, "veredicto": "no_vendible"}
    tiene = disponible + retiro + otros + int(vendidas) - int(antes or 0)
    if tiene >= int(enviadas):
        return {**base, "veredicto": "llego_sin_aviso", "aprox": antes is None}
    return {**base, "veredicto": "no_aparece", "no_aparecen": int(enviadas) - max(0, tiene)}


# ── Mercado Libre en vivo (sólo GET) ────────────────────────────────────────

def _token(codigo: str, renovar: bool = False) -> str | None:
    from services import meli
    return meli.refrescar_token(codigo) if renovar else meli._access_token(codigo)


def _get(ruta: str, token: str, params: dict | None = None) -> tuple[int, Any]:
    r = httpx.get(f"{_ML}{ruta}", params=params, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else None)


def inventario(codigo: str, listing_ids: list[str]) -> dict[str, dict[str, Any] | None]:
    """{listing_id: {inventario, total, disponible, no_disponible, detalle} | None}. None = ML no contestó."""
    ahora = time.time()
    salida: dict[str, dict[str, Any] | None] = {}
    faltan: list[str] = []
    with _lock:
        for lid in dict.fromkeys(i for i in listing_ids if i):
            hit = _cache.get((codigo, lid))
            if hit and ahora - hit[0] < _TTL_S:
                salida[lid] = hit[1]
            else:
                faltan.append(lid)
    if not faltan:
        return salida
    token = _token(codigo)
    if not token:
        return {**salida, **{lid: None for lid in faltan}}

    invs: dict[str, str] = {}
    for i in range(0, len(faltan), 20):
        grupo = faltan[i:i + 20]
        try:
            cod, cuerpo = _get("/items", token, {"ids": ",".join(grupo), "attributes": "id,inventory_id"})
            if cod == 401:
                token = _token(codigo, renovar=True) or token
                cod, cuerpo = _get("/items", token, {"ids": ",".join(grupo), "attributes": "id,inventory_id"})
            for x in (cuerpo or []) if cod == 200 else []:
                b = x.get("body") or {}
                if x.get("code") == 200 and b.get("inventory_id"):
                    invs[b["id"]] = b["inventory_id"]
        except Exception as exc:  # noqa: BLE001 — sin ML, el renglón dice «sin dato»
            log.warning("inventario FULL: /items falló (%s)", exc)

    def stock(lid: str) -> tuple[str, dict[str, Any] | None]:
        inv = invs.get(lid)
        if not inv:
            return lid, None
        try:
            cod, b = _get(f"/inventories/{inv}/stock/fulfillment", token)
            if cod != 200 or not isinstance(b, dict):
                return lid, None
            detalle: dict[str, int] = {}
            for d in b.get("not_available_detail") or []:
                detalle[str(d.get("status") or "?")] = detalle.get(str(d.get("status") or "?"), 0) + int(d.get("quantity") or 0)
            return lid, {"inventario": inv, "total": int(b.get("total") or 0),
                         "disponible": int(b.get("available_quantity") or 0),
                         "no_disponible": int(b.get("not_available_quantity") or 0), "detalle": detalle}
        except Exception as exc:  # noqa: BLE001
            log.warning("inventario FULL: %s falló (%s)", inv, exc)
            return lid, None

    with ThreadPoolExecutor(max_workers=4) as pool:
        for lid, dato in pool.map(stock, faltan):
            salida[lid] = dato
            if dato is not None:            # una falla de ML no se recuerda: se reintenta
                with _lock:
                    _cache[(codigo, lid)] = (ahora, dato)
    return salida


# ── Lo que llama el router (BLOQUEA: va en un hilo) ─────────────────────────

_SQL_PUBLICACION = full_publicaciones.CTE + """
    select upper(sku) as sku, listing_id from h
     where cuenta = %(c)s and cuenta_fila and upper(sku) = any(%(s)s)
"""

# El stock FULL que ya había antes del envío: el último cambio antes de su inicio
# o, si no lo hay, de dónde partió el primer cambio después.
_SQL_ANTES = """
    select upper(h.sku::text) as sku,
           coalesce((select x.valor_nuevo from channel.listing_history x
                      where x.sku = h.sku and x.account_id = h.account_id and x.campo = 'stock_full'
                        and x.changed_at < %(ini)s order by x.changed_at desc limit 1),
                    (select x.valor_anterior from channel.listing_history x
                      where x.sku = h.sku and x.account_id = h.account_id and x.campo = 'stock_full'
                        and x.changed_at >= %(ini)s order by x.changed_at asc limit 1)) as antes
      from channel.listings h join core.accounts a on a.id = h.account_id
     where h.canal = 'mercado_libre' and upper(a.legacy_code) = %(c)s and upper(h.sku::text) = any(%(s)s)
"""

_SQL_VENDIDAS = """
    select upper(sku::text) as sku, coalesce(sum(units_sold), 0)::int as u
      from channel.sales_daily_completa
     where is_full and upper(cuenta) = %(c)s and upper(sku::text) = any(%(s)s) and date >= %(desde)s
     group by 1
"""

# Lo vendido según los avisos de FULL de ML (ventas menos cancelaciones), una vez por
# operación. Se toma el MAYOR de las dos fuentes: las ventas por día dejan fuera alguna
# (TEC-0383-MET: 28 contra 29 avisadas) y los avisos se pierden si el token está caído.
_SQL_VENDIDAS_AVISOS = r"""
    select upper(sku) as sku,
           coalesce(sum(-n) filter (where tipo = 'SALE_CONFIRMATION'), 0)
           - coalesce(sum(n) filter (where tipo in ('SALE_CANCELATION', 'SALE_DELIVERY_CANCELATION')), 0) as u
      from (select distinct on (coalesce(item_id, id::text)) sku::text as sku, split_part(resultado, ' ', 1) as tipo,
                   (regexp_match(resultado, '^[A-Z_]+ x(-?[0-9]+)'))[1]::int as n
              from ops.fanout_log
             where accion like 'full\_%%' and upper(cuenta) = %(c)s and upper(sku::text) = any(%(s)s)
               and ts >= %(desde)s
             order by coalesce(item_id, id::text), ts) t
     group by 1
"""


def explicar(envio: dict[str, Any], envios: list[dict[str, Any]]) -> dict[str, Any]:
    """Los renglones a los que les falta algo, con lo que dice ML de cada uno."""
    codigo = _CODIGO.get(envio.get("cuenta") or "")
    if envio.get("canal") != "meli" or not codigo:
        return {"ok": False, "motivo": "sólo envíos a FULL de Mercado Libre con cuenta"}
    if envio.get("estado_odoo") != "done":
        return {"ok": False, "motivo": "la salida no se ha validado: todavía no falta nada"}
    ini = fulfillment_etapas._inicio(envio)
    lineas = [r for r in envio.get("lineas") or []
              if int(r.get("enviadas") or 0) > int(r.get("llegadas") or 0)]
    if not lineas or not ini:
        return {"ok": True, "lineas": {}, "consultado": datetime.now(timezone.utc).isoformat()}
    skus = sorted({r["sku"].upper() for r in lineas})
    pubs = {f["sku"]: f["listing_id"] for f in sdb.fetch_all(_SQL_PUBLICACION, {"c": codigo, "s": skus})}
    antes = {f["sku"]: f["antes"] for f in sdb.fetch_all(_SQL_ANTES, {"c": codigo, "s": skus, "ini": ini})}
    desde = min((datetime.fromisoformat(r["llegada"]) for r in lineas if r.get("llegada")), default=ini)
    vend = {f["sku"]: int(f["u"]) for f in sdb.fetch_all(
        _SQL_VENDIDAS, {"c": codigo, "s": skus, "desde": desde.astimezone(fulfillment_etapas._CDMX).date()})}
    for f in sdb.fetch_all(_SQL_VENDIDAS_AVISOS, {"c": codigo, "s": skus, "desde": desde}):
        vend[f["sku"]] = max(vend.get(f["sku"], 0), int(f["u"] or 0))
    inv = inventario(codigo, [pubs[s] for s in skus if pubs.get(s)])
    # Otra orden del mismo SKU, a la misma cuenta, que ya pudo salir después de ésta: su
    # mercancía también está (o estuvo) en FULL y el número de ML las mezcla.
    despues = {r["sku"].upper() for e in envios
               if e is not envio and e.get("canal") == "meli" and e.get("cuenta") == envio.get("cuenta")
               and fulfillment_etapas._corta(e) and (fulfillment_etapas._inicio(e) or ini) > ini
               for r in e.get("lineas") or []}
    salida: dict[str, Any] = {}
    for r in lineas:
        s = r["sku"].upper()
        if not pubs.get(s):
            salida[r["sku"]] = {"veredicto": "sin_publicacion",
                                "faltan": int(r["enviadas"]) - int(r.get("llegadas") or 0)}
        else:
            a = antes.get(s)
            v = veredicto(int(r["enviadas"]), int(r.get("llegadas") or 0), inv.get(pubs[s]), vend.get(s, 0),
                          int(float(a)) if a not in (None, "") else None)
            if s in despues:
                v["aprox"] = True
            salida[r["sku"]] = {**v, "publicacion": pubs[s]}
        salida[r["sku"]]["texto"] = TEXTO[salida[r["sku"]]["veredicto"]]
    return {"ok": True, "lineas": salida, "consultado": datetime.now(timezone.utc).isoformat(),
            "fuente": "Mercado Libre en vivo: /inventories/{id}/stock/fulfillment (vendible y no vendible con su "
                      "motivo) + ventas FULL desde la llegada (kubera) + stock FULL antes del envío (historial)"}
