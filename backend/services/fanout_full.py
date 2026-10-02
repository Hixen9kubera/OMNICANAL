"""
fanout_full.py — La pestaña FULL del fan-out: el inventario que vive en las
bodegas de Mercado Libre (Kubera y San Corpe), lo que va llegando y lo que sale,
aviso por aviso.

FULL NO TOCA WOO. Es inventario de ML: la venta FULL nace con su stock ya
descontado y Woo sigue copiando a Odoo. Con la bodega propia cruzan solo dos
cosas: las salidas de Odoo a FULL (restan en Odoo al validarse) y los retiros
(regresan con una recepción en Odoo). Esta página solo MIRA.

SOLO LEE. Fuentes:
  · `ops.fanout_log` (acciones `full_*`): cada aviso `fbm_stock_operations` que
    resolvió `services/stock_full.py`, con tipo y piezas al inicio de `resultado`
    («SALE_CONFIRMATION x-1: …»). ML reenvía avisos: cada operación (su id va en
    `item_id`) cuenta UNA vez.
  · `channel.listings`: el stock FULL que lee el sync (`stock_full`). Una
    publicación con variantes trae la fila del PADRE además de las hijas y el
    padre repite la suma: se excluye (2-oct: 795 pzs contadas dos veces en 49
    publicaciones; en otras 30 el padre no cuadra con sus hijas y se reporta).
  · `channel.listing_history` (campo `stock_full`): la FOTO del sync, día por día.
  · Las salidas de Odoo a FULL salen del caché de `/api/fulfillment/envios`
    (`fulfillment_envios.leer`, XML-RPC) y se piden APARTE (`camino`): Odoo tarda
    y puede no contestar sin tumbar el resto de la página.

EL CUADRE — medido el 2-oct en 9 días y las dos cuentas
La foto y los avisos no miden lo mismo para todos los tipos:
  · los AJUSTES de ML no mueven lo vendible: en los SKUs más ajustados van uno a
    uno con las ventas (VAR-0670-NEG: 22 ventas y 22 ajustes en 8 días);
  · los RETIROS salen de piezas ya apartadas: San Corpe retiró 1,063 pzs el 24 y
    25-sep y la foto se movió −104.
Sin esos dos grupos, lo que avisa ML explica la foto a ±20 pzs en 15 de 18
días-cuenta; contándolos, en 6. Por eso el libro trae las dos cuentas del neto.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from services import supabase_db as sdb

log = logging.getLogger("omnicanal.fanout_full")

ZONA = "America/Mexico_City"
CUENTAS = {"BEKURA": "Kubera", "SANCORFASHION": "San Corpe"}
# `fulfillment_envios` nombra la cuenta por su tienda.
_CUENTA_ENVIO = {"Kubera": "BEKURA", "San Corpe": "SANCORFASHION"}
CUADRA, REVISAR = 20, 60          # piezas de diferencia entre la foto y los avisos

GRUPOS = ["llego", "vendido", "cancelado", "traslado", "cuarentena", "ajuste", "retiro", "otro"]
# Lo que sí mueve el stock vendible (el que lee la foto). Ver «EL CUADRE».
VENDIBLE = ("llego", "vendido", "cancelado", "traslado", "cuarentena")

# Tipo de operación de ML → (grupo, cómo se dice). Un tipo que no esté aquí cae
# en «otro» y la página lo señala: es la misma alarma que TIPOS_DESCONOCIDOS de
# `/api/fanout/full/observacion`.
TIPOS: dict[str, tuple[str, str]] = {
    "TRANSFER_DELIVERY": ("llego", "Llegó, ya vendible"),
    "INBOUND_RECEPTION": ("llego", "Llegó directo a bodega"),
    "SALE_CONFIRMATION": ("vendido", "Vendido"),
    "SALE_CANCELATION": ("cancelado", "Venta cancelada"),
    "SALE_DELIVERY_CANCELATION": ("cancelado", "Venta cancelada en la entrega"),
    "ADJUSTMENT": ("ajuste", "Ajuste de ML"),
    "TRANSFER_ADJUSTMENT": ("ajuste", "Ajuste de traslado"),
    "LOST_REFUND": ("ajuste", "ML pagó una pieza perdida"),
    "DAMAGED_REMOVAL": ("ajuste", "Retirada por daño"),
    "WITHDRAWAL_RESERVATION": ("retiro", "Apartada para retiro"),
    "WITHDRAWAL_DELIVERY": ("retiro", "Retiro: sale de FULL"),
    "WITHDRAWAL_CANCELATION": ("retiro", "Retiro cancelado"),
    "TRANSFER_RESERVATION": ("traslado", "Traslado interno"),
    "QUARANTINE_RESERVATION": ("cuarentena", "A cuarentena"),
    "QUARANTINE_RESTOCK": ("cuarentena", "Sale de cuarentena"),
}
SIGNIFICA = {
    "llego": "Entra a la venta en FULL",
    "vendido": "Pedido en Woo, sin restar stock",
    "cancelado": "La pieza regresa a FULL",
    "traslado": "ML mueve piezas entre sus bodegas",
    "cuarentena": "Devolución en revisión",
    "ajuste": "Contabilidad de ML: no mueve lo vendible",
    "retiro": "Regresa a bodega: entra por Odoo",
    "otro": "Tipo de aviso nuevo: revisar",
}

_RE_AVISO = re.compile(r"^([A-Z_]+)\s+x(-?\d+)")


# ── Funciones puras (se prueban sin kubera) ──────────────────────────────────

def leer_aviso(resultado: str | None) -> tuple[str, int]:
    """«SALE_CONFIRMATION x-1: sin efecto en Woo» → ("SALE_CONFIRMATION", -1)."""
    m = _RE_AVISO.match(resultado or "")
    return (m.group(1), int(m.group(2))) if m else ("?", 0)


def grupo(tipo: str) -> str:
    return TIPOS.get(tipo, ("otro", ""))[0]


def aviso(fila: dict[str, Any]) -> dict[str, Any]:
    """Una fila de la bitácora (ya sin reenvíos) → un aviso de la página."""
    tipo, x = leer_aviso(fila.get("resultado"))
    g = grupo(tipo)
    cuenta = (fila.get("cuenta") or "").upper()
    sku = (fila.get("sku") or "").strip()
    sin_sku = fila.get("accion") == "full_sin_sku" or sku in ("", "?")
    return {"hora": fila.get("hora") or "", "dia": (fila.get("hora") or "")[:10],
            "cuenta": cuenta, "nombre": CUENTAS.get(cuenta, cuenta or "sin cuenta"),
            "sku": None if sin_sku else sku, "tipo": tipo, "texto": TIPOS.get(tipo, ("", tipo))[1],
            "grupo": g, "x": x,
            "sig": "ML no dijo de qué SKU es" if sin_sku else SIGNIFICA[g]}


def estado_dif(dif: int | float) -> str:
    a = abs(dif)
    return "cuadra" if a <= CUADRA else "revisar" if a <= REVISAR else "no_cuadra"


def sumar(avisos: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, int]]:
    """(día, cuenta) → piezas por grupo y número de avisos."""
    acc: dict[tuple[str, str], dict[str, int]] = {}
    for a in avisos:
        s = acc.setdefault((a["dia"], a["cuenta"]), {**{g: 0 for g in GRUPOS}, "avisos": 0})
        s[a["grupo"]] += a["x"]
        s["avisos"] += 1
    return acc


def libro(avisos: list[dict[str, Any]], foto: dict[tuple[str, str], dict[str, Any]],
          dias: list[str], hoy: str) -> list[dict[str, Any]]:
    """El cuadre diario por cuenta: lo que avisó ML contra lo que movió la foto.

    `foto`: (día, cuenta) → {"delta", "cambios"} de `channel.listing_history`.
    Sin cambios en la foto ese día la foto se movió 0: es un dato, no un hueco
    (el sync anota cada cambio), y por eso cuenta en la diferencia."""
    suma = sumar(avisos)
    filas = []
    for cuenta, nombre in CUENTAS.items():
        for d in dias:
            s = suma.get((d, cuenta), {**{g: 0 for g in GRUPOS}, "avisos": 0})
            f = foto.get((d, cuenta)) or {}
            delta = int(f.get("delta") or 0)
            vendible = sum(s[g] for g in VENDIBLE)
            todo = sum(s[g] for g in GRUPOS)
            filas.append({
                "dia": d, "cuenta": cuenta, "nombre": nombre, "parcial": d == hoy,
                "grupos": {g: s[g] for g in GRUPOS}, "avisos": s["avisos"],
                "vendible": vendible, "todo": todo,
                "foto": delta, "cambios_foto": int(f.get("cambios") or 0),
                "dif_vendible": delta - vendible, "dif_todo": delta - todo,
                "estado_vendible": estado_dif(delta - vendible), "estado_todo": estado_dif(delta - todo),
            })
    return filas


def camino(envios: list[dict[str, Any]], ahora: datetime | None = None) -> dict[str, Any]:
    """Las salidas de Odoo a FULL por cuenta: abiertas (bodega aún no valida), en
    camino (validadas y todavía no vendibles en FULL) y sin número de envío (su
    llegada no se puede seguir). `envios` es la lista de `/api/fulfillment/envios`."""
    ahora = ahora or datetime.now(timezone.utc)

    def base() -> dict[str, int]:
        return {"abiertas": 0, "pzs_abiertas": 0, "en_proceso": 0, "enviadas": 0,
                "llegadas": 0, "en_camino": 0, "sin_numero": 0}

    por = {"BEKURA": base(), "SANCORFASHION": base(), "": base()}
    faltan = []
    for e in envios:
        if e.get("canal") != "meli":
            continue
        c = _CUENTA_ENVIO.get(e.get("cuenta") or "", "")
        p = por[c]
        if e.get("estado") == "sinEnlazar":
            p["sin_numero"] += 1
        if e.get("estado_odoo") == "cancel":
            continue
        if e.get("estado_odoo") != "done":
            p["abiertas"] += 1
            p["pzs_abiertas"] += int(e.get("pedidas") or 0)
            continue
        cob = e.get("cobertura") or {}
        if not cob or cob.get("cerrado"):
            continue
        enviadas, llegadas = int(cob.get("piezas_enviadas") or 0), int(cob.get("piezas_llegadas") or 0)
        falta = max(0, enviadas - llegadas)
        p["en_proceso"] += 1
        p["enviadas"] += enviadas
        p["llegadas"] += llegadas
        p["en_camino"] += falta
        if falta:
            et = e.get("etapas") or []
            validada = (et[1] or {}).get("ts") if len(et) > 1 and et[1] else None
            dias = None
            if validada:
                try:
                    dias = max(0, (ahora - datetime.fromisoformat(validada)).days)
                except ValueError:
                    dias = None
            faltan.append({"salida": e.get("salida"), "cuenta": c, "nombre": CUENTAS.get(c, "sin cuenta"),
                           "enviadas": enviadas, "llegadas": llegadas, "falta": falta, "dias": dias})
    faltan.sort(key=lambda f: -f["falta"])
    return {"cuentas": por, "faltan": faltan[:8]}


# ── Lecturas de kubera ───────────────────────────────────────────────────────

# Un aviso por operación de ML: el primero que llegó (los reenvíos repiten id).
_SQL_AVISOS = r"""
    select distinct on (coalesce(item_id, id::text))
           ts, to_char(ts at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as hora,
           upper(coalesce(cuenta, '')) as cuenta, sku::text as sku, accion,
           left(coalesce(resultado, ''), 200) as resultado
      from ops.fanout_log
     where accion like 'full\_%%'
       and ts > ((now() at time zone %(z)s)::date - %(d)s)::timestamp at time zone %(z)s
     order by coalesce(item_id, id::text), ts
"""

# Las filas FULL y cuáles son el PADRE de una publicación con variantes: el que
# tiene, en la misma publicación, una hija cuyo SKU empieza con el suyo y un guion.
# Va con un JOIN por (cuenta, publicación) y no con un EXISTS correlacionado: así
# tarda décimas y no segundos.
_CTE_FULL = r"""
    with f as (select upper(a.legacy_code) as cuenta, l.listing_id, l.sku::text as sku, l.situacion,
                      coalesce(l.stock_full, 0) as st, l.updated_at, l.account_id
                 from channel.listings l join core.accounts a on a.id = l.account_id
                where l.canal = 'mercado_libre' and l.is_fulfillment
                  and a.legacy_code in ('BEKURA', 'SANCORFASHION')),
         pad as (select distinct p.cuenta, p.account_id, p.listing_id, p.sku
                   from f p join f c on c.cuenta = p.cuenta and c.listing_id = p.listing_id
                  where c.sku <> p.sku and c.sku like p.sku || '-%%'),
         h as (select f.*, (pad.sku is not null) as es_padre
                 from f left join pad on pad.cuenta = f.cuenta and pad.listing_id = f.listing_id
                                     and pad.sku = f.sku)
"""

_SQL_STOCK = _CTE_FULL + r"""
    select cuenta,
           coalesce(sum(st) filter (where not es_padre and situacion = 'active'), 0)::int as piezas,
           count(distinct listing_id) filter (where not es_padre and situacion = 'active' and st > 0) as publicaciones,
           count(*) filter (where not es_padre and situacion = 'active' and st > 0) as skus,
           coalesce(sum(st) filter (where not es_padre and situacion <> 'active'), 0)::int as piezas_no_activas,
           max(updated_at) as al
      from h group by cuenta
"""

_SQL_PADRES = _CTE_FULL + r"""
    , p as (select pad.cuenta, pad.listing_id, max(pf.st) as st, coalesce(sum(c.st), 0) as hijos
              from pad join f pf on pf.cuenta = pad.cuenta and pf.listing_id = pad.listing_id and pf.sku = pad.sku
              join f c on c.cuenta = pad.cuenta and c.listing_id = pad.listing_id and c.sku like pad.sku || '-%%'
             group by pad.cuenta, pad.listing_id, pad.sku)
    select count(*) filter (where st = hijos) as dobles, coalesce(sum(st) filter (where st = hijos), 0)::int as pzs_dobles,
           count(*) filter (where st <> hijos) as aclarar,
           coalesce(sum(greatest(st - hijos, 0)) filter (where st <> hijos), 0)::int as pzs_aclarar
      from p
"""

# La foto: cuánto movió el sync el stock FULL cada día, sin las filas padre.
_SQL_FOTO = _CTE_FULL + r"""
    select (hi.changed_at at time zone %(z)s)::date::text as dia, upper(a.legacy_code) as cuenta,
           count(*) as cambios,
           coalesce(sum(hi.valor_nuevo::numeric - hi.valor_anterior::numeric), 0)::int as delta
      from channel.listing_history hi join core.accounts a on a.id = hi.account_id
     where hi.campo = 'stock_full' and hi.canal = 'mercado_libre'
       and hi.changed_at > ((now() at time zone %(z)s)::date - %(d)s)::timestamp at time zone %(z)s
       and hi.valor_nuevo ~ '^-?[0-9]+$' and hi.valor_anterior ~ '^-?[0-9]+$'
       and a.legacy_code in ('BEKURA', 'SANCORFASHION')
       and not exists (select 1 from pad where pad.sku = hi.sku and pad.account_id = hi.account_id)
     group by 1, 2
"""

_cache: dict[str, Any] = {"t": 0.0, "v": None}
_cache_lock = threading.Lock()


def _local(ts: Any) -> str | None:
    """timestamptz → «YYYY-MM-DD HH:MM:SS» de CDMX (sin horario de verano desde 2022)."""
    if not ts:
        return None
    return ts.astimezone(timezone(timedelta(hours=-6))).strftime("%Y-%m-%d %H:%M:%S")


def _resumen(dias: int = 9) -> dict[str, Any]:
    ahora = sdb.fetch_one("select (now() at time zone %(z)s) as l", {"z": ZONA}) or {}
    local: datetime = ahora.get("l") or datetime.now(timezone(timedelta(hours=-6))).replace(tzinfo=None)
    hoy = local.date()
    dias_libro = [(hoy - timedelta(days=i)).isoformat() for i in range(dias - 1, -1, -1)]

    avisos = [aviso(f) for f in sdb.fetch_all(_SQL_AVISOS, {"z": ZONA, "d": dias - 1})]
    avisos.sort(key=lambda a: a["hora"], reverse=True)
    foto = {(f["dia"], f["cuenta"]): f for f in sdb.fetch_all(_SQL_FOTO, {"z": ZONA, "d": dias - 1})}
    stock = {f["cuenta"]: f for f in sdb.fetch_all(_SQL_STOCK)}
    padres = sdb.fetch_one(_SQL_PADRES) or {}

    h = hoy.isoformat()
    de_hoy = [a for a in avisos if a["dia"] == h]
    suma = sumar(de_hoy)
    cuentas = []
    for c, nombre in CUENTAS.items():
        s = stock.get(c) or {}
        hoy_c = suma.get((h, c), {**{g: 0 for g in GRUPOS}, "avisos": 0})
        cuentas.append({
            "cuenta": c, "nombre": nombre, "piezas": int(s.get("piezas") or 0),
            "publicaciones": int(s.get("publicaciones") or 0), "skus": int(s.get("skus") or 0),
            "piezas_no_activas": int(s.get("piezas_no_activas") or 0),
            "hoy": {g: {"avisos": sum(1 for a in de_hoy if a["cuenta"] == c and a["grupo"] == g), "piezas": hoy_c[g]}
                    for g in GRUPOS},
            "avisos_hoy": hoy_c["avisos"],
        })

    por_hora = [{"h": f"{i:02d}", "n": 0} for i in range(local.hour + 1)]
    for a in de_hoy:
        i = int(a["hora"][11:13] or 0)
        if i < len(por_hora):
            por_hora[i]["n"] += 1

    corte = (local - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    ultimas_24h = [a for a in avisos if a["hora"] >= corte]
    ayer = (hoy - timedelta(days=1)).isoformat()
    filas_libro = libro(avisos, foto, dias_libro, h)
    al_foto = max((s.get("al") for s in stock.values() if s.get("al")), default=None)
    siete = (hoy - timedelta(days=7)).isoformat()
    nuevos: dict[str, int] = {}
    for a in avisos:
        if a["grupo"] == "otro":
            nuevos[a["tipo"]] = nuevos.get(a["tipo"], 0) + 1
    return {
        "ok": True, "hoy": h, "hora": local.strftime("%H:%M"),
        "cuentas": cuentas, "avisos_hoy": len(de_hoy), "por_hora": por_hora,
        "avisos": ultimas_24h[:900], "avisos_24h": len(ultimas_24h),
        "libro": filas_libro, "umbral": {"cuadra": CUADRA, "revisar": REVISAR},
        "salud": {
            "foto_al": _local(al_foto),
            "ultimo_aviso": avisos[0]["hora"] if avisos else None,
            "sin_sku_hoy": sum(1 for a in de_hoy if a["sku"] is None),
            "sin_sku_7d": sum(1 for a in avisos if a["sku"] is None and a["dia"] > siete),
            "ayer": [{"cuenta": f["cuenta"], "nombre": f["nombre"], "dif": f["dif_vendible"],
                      "estado": f["estado_vendible"]} for f in filas_libro if f["dia"] == ayer],
            "padres": {k: int(padres.get(k) or 0) for k in ("dobles", "pzs_dobles", "aclarar", "pzs_aclarar")},
            "tipos_nuevos": [{"tipo": t, "n": n} for t, n in sorted(nuevos.items(), key=lambda kv: -kv[1])],
        },
    }


def resumen() -> dict[str, Any]:
    """Lo que pinta la pestaña FULL (todo de kubera). Caché de 20 s: la página
    pregunta cada minuto y puede haber varias abiertas."""
    with _cache_lock:
        if _cache["v"] is not None and time.time() - _cache["t"] < 20:
            return _cache["v"]
        v = _resumen()
        _cache.update(t=time.time(), v=v)
        return v
