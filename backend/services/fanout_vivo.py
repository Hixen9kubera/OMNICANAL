"""
fanout_vivo.py — Lo que pinta la página del fan-out en vivo: el veredicto y la
cadena (Odoo → stock_watch → Woo → fan-out → canales), el horario de cambios,
la matriz de coincidencia por SKU y el rastro de un cambio.

SOLO LEE, y todo sale de kubera:
  · `ops.fanout_log` — una fila por canal tocado en cada cambio. `ts` es el FIN
    del cambio y `ms` mide el cambio ENTERO, no cada canal: por eso el rastro
    puede decir cuánto esperó y cuánto tardó en escribirse, pero no cuánto tardó
    cada canal por separado.
  · `ops.stock_watch_photo` — Odoo y Woo lado a lado, de la última pasada.
  · `channel.listings` — lo que cada canal dice tener. Su `updated_at` es el
    último CAMBIO de la fila, no la última lectura (en ML sobre todo): por eso
    la matriz dice «igual desde» y no «leído».
Lo único que no vive en la BD —la última pasada de stock_watch y la cola del
fan-out— se toma de la memoria del proceso cuando existe; si no (un backend que
no corre esos procesos, como el del sandbox), se deduce de la bitácora.

La página pregunta cada pocos segundos. Los agregados (24 h, 17 días, la foto)
van con caché de 20 s; los eventos nuevos van siempre frescos por `desde_id`.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from services import fanout_full, full_publicaciones
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.fanout_vivo")

_ZONA = "America/Mexico_City"
_ACC_FANOUT = ["escribir", "omitir", "sin_cambio", "sin_destinos"]
_NOMBRE = {"tiktok": "TikTok", "temu": "Temu", "mercado_libre": "Mercado Libre",
           "amazon": "Amazon", "walmart": "Walmart"}
# Columnas del horario y de la matriz. ML va por cuenta porque cada cuenta
# falla o funciona por su lado (los tokens son de cada cuenta).
COLUMNAS = [
    {"id": "tiktok", "canal": "tiktok", "cuenta": "KUBERA", "nombre": "TikTok"},
    {"id": "temu", "canal": "temu", "cuenta": "TEMU", "nombre": "Temu"},
    {"id": "ml_kubera", "canal": "mercado_libre", "cuenta": "BEKURA", "nombre": "ML Kubera"},
    {"id": "ml_sancor", "canal": "mercado_libre", "cuenta": "SANCORFASHION", "nombre": "ML San Corpe"},
]
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
_FLECHA = re.compile(r"(-?\d+)\s*->\s*(-?\d+)")


# ── Utilidades ──────────────────────────────────────────────────────────────

def reparto() -> list[str]:
    """Canales que reciben stock del fan-out, en el orden de la página."""
    from services import fanout_stock
    act = fanout_stock._canales_activos()
    base = ["tiktok", "temu", "mercado_libre"]
    if act is None:
        return base
    return [c for c in base if c in act] + sorted(c for c in act if c not in base)


def _n(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def _fecha(local: str | None, hoy: str | None = None) -> str:
    """'2026-09-17 21:42:05' → '17-sep 21:42' (u 'hoy 21:42')."""
    if not local:
        return "—"
    dia, _, hora = local.partition(" ")
    if hoy and dia == hoy:
        return f"hoy {hora[:5]}"
    a, m, d = dia.split("-")
    return f"{int(d)}-{_MESES[int(m) - 1]} {hora[:5]}"


def _el(cuando: str) -> str:
    """'hoy 11:59' → 'hoy a las 11:59'; '17-sep 21:42' → 'el 17-sep a las 21:42'."""
    if cuando.startswith("hoy "):
        return f"hoy a las {cuando[4:]}"
    dia, _, hora = cuando.partition(" ")
    return f"el {dia} a las {hora}" if hora else f"el {dia}"


def _dia(local: str | None) -> str:
    if not local:
        return "—"
    a, m, d = local[:10].split("-")
    return f"{int(d)}-{_MESES[int(m) - 1]}"


def _ahora_local() -> dict[str, Any]:
    return sdb.fetch_one(
        "select to_char(now() at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') ahora",
        {"z": _ZONA}) or {}


def _motivo_omision(r: str) -> tuple[str, str]:
    if "FULL" in r or "FBA" in r:
        return "full", "FULL"
    if "DELETED" in r:
        return "omit", "ya no existe"
    if "Borrador" in r:
        return "omit", "borrador"
    if "under_review" in r:
        return "omit", "en revisión"
    if "closed" in r:
        return "omit", "cerrada"
    if "paused" in r:
        return "omit", "pausada"
    if "inactive" in r:
        return "omit", "inactiva"
    return "omit", "omitida"


def _celda_evento(filas: list[dict]) -> dict[str, Any]:
    """Lo que pasó en UN canal/cuenta durante un cambio. Varias filas = varias
    publicaciones del mismo SKU en esa cuenta: manda la peor."""
    if not filas:
        return {"k": "nopub", "texto": "—"}
    esc = [f for f in filas if f["accion"] == "escribir"]
    mal = [f for f in esc if str(f.get("resultado") or "").startswith("ERROR")]
    if mal:
        r = str(mal[0]["resultado"])
        cod = "403" if "403" in r else "error"
        sigue = mal[0].get("stock_canal")
        return {"k": "mal", "texto": f"{cod} · sigue en {_n(sigue)}" if sigue is not None else cod,
                "detalle": r[:240]}
    ok = [f for f in esc if str(f.get("resultado") or "").lower().startswith("ok")]
    if ok:
        return {"k": "ok", "texto": f"{_n(ok[0].get('stock_canal'))} → {_n(ok[0].get('objetivo'))}"}
    if any("DRY" in str(f.get("resultado") or "") for f in esc):
        return {"k": "sim", "texto": f"simulado → {_n(esc[0].get('objetivo'))}"}
    igual = [f for f in filas if f["accion"] == "sin_cambio"]
    if igual:
        return {"k": "igual", "texto": f"ya tenía {_n(igual[0].get('stock_canal'))}"}
    om = [f for f in filas if f["accion"] == "omitir"]
    if om:
        r = str(om[0].get("resultado") or "")
        k, t = _motivo_omision(r)
        return {"k": k, "texto": t, "detalle": r[:240]}
    return {"k": "nopub", "texto": "—"}


def _tono(celdas: dict[str, dict]) -> str:
    ks = [c["k"] for c in celdas.values() if c["k"] != "nopub"]
    if "mal" in ks:
        return "mal"
    if "ok" in ks:
        return "ok"
    if ks and all(k == "full" for k in ks):
        return "full"
    return "omit"


def _origen(motivo: str, woo: dict | None) -> tuple[str, str, int | None, int | None]:
    """De dónde vino el cambio y qué se movió en Woo."""
    antes = despues = None
    if woo:
        m = _FLECHA.search(str(woo.get("resultado") or ""))
        if m:
            antes, despues = int(m.group(1)), int(m.group(2))
    mot = (motivo or "").strip()
    if mot.startswith("delta de Odoo"):
        txt = f"Odoo {antes} → {despues}" if antes is not None else "cambio en Odoo"
        return "odoo", txt, antes, despues
    if mot.startswith("cambio de stock en Woo"):
        txt = f"Woo {antes} → {despues}" if antes is not None else "cambio en Woo"
        return "woo", txt, antes, despues
    if mot.lower().startswith("venta"):
        partes = mot.split()
        cuenta = partes[1] if len(partes) > 1 else ""
        nombre = {"BEKURA": "ML Kubera", "SANCORFASHION": "ML San Corpe"}.get(cuenta, cuenta.title())
        return "venta", f"venta en {nombre}".strip(), None, None
    return "otro", mot[:60] or "manual", None, None


# ── Eventos (horario de cambios) ─────────────────────────────────────────────

def _woo_de(eventos: list[dict]) -> dict[tuple[str, Any], dict]:
    """El renglón de stock_watch (Odoo→Woo o cambio en Woo) que disparó cada cambio."""
    pares = [(e["sku"], e["ts"]) for e in eventos
             if str(e.get("motivo") or "").startswith(("delta de Odoo", "cambio de stock en Woo"))]
    if not pares:
        return {}
    filas = sdb.fetch_all(
        """select e.sku, e.ts, w.ts as woo_ts, w.accion, w.resultado,
                  to_char(w.ts at time zone %(z)s, 'HH24:MI:SS') as woo_hora,
                  extract(epoch from (e.ts - w.ts))::float as total_s
             from unnest(%(s)s::text[], %(t)s::timestamptz[]) as e(sku, ts)
             join lateral (
                   select w.ts, w.accion, w.resultado from ops.fanout_log w
                    where w.sku::text = e.sku and w.canal = 'woocommerce'
                      and w.accion in ('odoo_delta', 'woo_cambio')
                      and w.ts <= e.ts and w.ts > e.ts - interval '45 minutes'
                    order by w.ts desc limit 1) w on true""",
        {"z": _ZONA, "s": [p[0] for p in pares], "t": [p[1] for p in pares]})
    return {(f["sku"], f["ts"]): f for f in filas}


def _armar_eventos(ev: list[dict], filas: list[dict]) -> list[dict]:
    woo = _woo_de(ev)
    por_ev: dict[tuple[str, Any], list[dict]] = {}
    for f in filas:
        por_ev.setdefault((f["sku"], f["ts"]), []).append(f)
    salida = []
    for e in ev:
        clave = (e["sku"], e["ts"])
        mias = por_ev.get(clave, [])
        celdas = {}
        for col in COLUMNAS:
            celdas[col["id"]] = _celda_evento(
                [f for f in mias if f.get("canal") == col["canal"]
                 and (col["canal"] != "mercado_libre" or (f.get("cuenta") or "").upper() == col["cuenta"])])
        w = woo.get(clave)
        origen, cambio, antes, despues = _origen(e.get("motivo") or "", w)
        ms = float(e.get("ms") or 0)
        total = float(w["total_s"]) if w and w.get("total_s") is not None else None
        salida.append({
            "id": int(e["id"]), "sku": e["sku"], "fin": e["ts"].isoformat(), "hora": e["hora"],
            "origen": origen, "cambio": cambio, "woo_antes": antes, "woo_despues": despues,
            "woo_hora": w.get("woo_hora") if w else None,
            "ms": ms, "total_s": round(total, 1) if total is not None else None,
            "espera_s": round(max(0.0, total - ms / 1000), 1) if total is not None else None,
            "celdas": celdas, "tono": _tono(celdas),
            # Sin publicación en ningún canal del reparto: el cambio pasó por el
            # fan-out pero no tenía a quién escribirle. La página lo cuenta aparte.
            "toca": any(c["k"] != "nopub" for c in celdas.values()),
        })
    return salida


def eventos(desde_id: int = 0, limite: int = 14) -> list[dict]:
    """Los cambios más recientes del fan-out (los de stock_watch van aparte)."""
    ev = sdb.fetch_all(
        """select sku::text as sku, ts, max(id) as id, max(motivo) as motivo,
                  max(stock_drop) as stock_drop, max(objetivo) as objetivo, max(ms) as ms,
                  to_char(ts at time zone %(z)s, 'HH24:MI:SS') as hora
             from ops.fanout_log
            where accion = any(%(a)s) and coalesce(canal, '') <> 'woocommerce'
              and id > %(d)s and ts > now() - interval '3 days'
            group by sku, ts
            -- Solo los que tocan algún canal del reparto: los que no tenían a
            -- quién escribirle se cuentan aparte (`sin_reparto_1h`).
           having bool_or(canal = any(%(r)s))
            order by max(id) desc
            limit %(n)s""",
        {"z": _ZONA, "a": _ACC_FANOUT, "d": int(desde_id), "n": int(limite), "r": reparto()})
    if not ev:
        return []
    filas = sdb.fetch_all(
        """select sku::text as sku, ts, canal, cuenta, accion, stock_canal, objetivo, resultado
             from ops.fanout_log
            where sku::text = any(%(s)s) and ts = any(%(t)s::timestamptz[])
              and accion = any(%(a)s)
            order by id""",
        {"s": list({e["sku"] for e in ev}), "t": list({e["ts"] for e in ev}), "a": _ACC_FANOUT})
    return _armar_eventos(ev, filas)


# ── Resumen: veredicto, cadena, canales, qué atender, serie ──────────────────

_cache: dict[str, Any] = {"t": 0.0, "v": None}
_cache_lock = threading.Lock()


def _estado_canales(rep: list[str], hoy: str) -> list[dict]:
    filas = {f["canal"]: f for f in sdb.fetch_all(
        """with u as (
             select canal, max(ts) filter (where resultado ilike 'ok%%') as ultimo_ok
               from ops.fanout_log
              where accion = 'escribir' and canal = any(%(c)s) and ts > now() - interval '120 days'
              group by canal)
           select f.canal,
                  to_char(u.ultimo_ok at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as ultimo_ok,
                  count(*) filter (where f.resultado like 'ERROR%%'
                                     and f.ts > coalesce(u.ultimo_ok, '-infinity')) as racha,
                  to_char(min(f.ts) filter (where f.resultado like 'ERROR%%'
                                              and f.ts > coalesce(u.ultimo_ok, '-infinity'))
                          at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as racha_desde,
                  floor(extract(epoch from now() - min(f.ts) filter (
                        where f.resultado like 'ERROR%%' and f.ts > coalesce(u.ultimo_ok, '-infinity')))
                        / 86400)::int as racha_dias,
                  count(*) filter (where f.ts > now() - interval '24 hours') as escritos_24h,
                  count(*) filter (where f.ts > now() - interval '24 hours'
                                     and f.resultado ilike 'ok%%') as ok_24h,
                  count(*) filter (where f.ts > now() - interval '24 hours'
                                     and f.resultado like 'ERROR%%') as rech_24h,
                  to_char(max(f.ts) filter (where f.resultado ilike 'ok%%') at time zone %(z)s,
                          'YYYY-MM-DD HH24:MI:SS') as ultimo_ok_reciente
             from ops.fanout_log f join u using (canal)
            where f.accion = 'escribir' and f.ts > now() - interval '120 days'
            group by f.canal, u.ultimo_ok""", {"c": rep, "z": _ZONA})}
    errores = {f["canal"]: f for f in sdb.fetch_all(
        """select distinct on (canal) canal, sku::text as sku, ts, left(resultado, 300) as resultado
             from ops.fanout_log
            where accion = 'escribir' and resultado like 'ERROR%%' and canal = any(%(c)s)
              and ts > now() - interval '7 days'
            order by canal, ts desc, id desc""", {"c": rep})}
    salida = []
    for c in rep:
        f = filas.get(c) or {}
        racha, ok24, rech24 = int(f.get("racha") or 0), int(f.get("ok_24h") or 0), int(f.get("rech_24h") or 0)
        if racha >= 3 and ok24 == 0 and rech24 > 0:
            estado = "rechaza"
        elif rech24 > 0:
            estado = "con_errores"
        elif int(f.get("escritos_24h") or 0) == 0:
            estado = "sin_cambios"
        else:
            estado = "al_dia"
        err = errores.get(c)
        r = str(err["resultado"]) if err else ""
        salida.append({
            "canal": c, "nombre": _NOMBRE.get(c, c), "estado": estado,
            "escritos_24h": int(f.get("escritos_24h") or 0), "ok_24h": ok24, "rech_24h": rech24,
            "racha": racha, "racha_dias": f.get("racha_dias"),
            "racha_desde": _fecha(f.get("racha_desde")) if racha else None,
            "ultimo_ok": _fecha(f.get("ultimo_ok"), hoy),
            "ultimo_ok_reciente": _fecha(f.get("ultimo_ok_reciente"), hoy),
            "error_codigo": ("403" if "403" in r else ("error" if r else None)),
            "error_permiso": ("PolicyAgent" in r or "PA_UNAUTHORIZED" in r),
            "ultimo_rechazo": ({"sku": err["sku"], "fin": err["ts"].isoformat()} if err else None),
        })
    return salida


# Temu: el estado que VENDE es «2/8». Medido el 1-oct-2026 con el estado de la publicación AL
# MOMENTO de cada pedido (historial del censo): en 30 días, 344 pedidos entraron en 2/8 (26 SKUs),
# 17 en 3/3, 4 en 3/1, 1 en 3/2 y NINGUNO en 4/7 (78 publicaciones, 70 con stock). Desde el 2-oct
# `temu.VENDIBLES` dice lo mismo (antes llamaba «Incompleto» a 2/8): esta constante va en el SQL de
# `_SQL_VIVAS` como literal y una prueba exige que coincida. Los demás estados se muestran crudos.
_TEMU_A_LA_VENTA = "2/8"

# Publicaciones A LA VENTA en el reparto (sin FULL), con su valor más reciente:
# lo usan las barras de coincidencia y la lista completa de la matriz.
_SQL_VIVAS = """
        with w as (
          select distinct on (sku, canal) sku::text as sku, canal, ts, objetivo
            from ops.fanout_log
           where accion = 'escribir' and resultado ilike 'ok%%' and ts > now() - interval '3 days'
           order by sku, canal, ts desc, id desc),
        r as (
          -- Lo último que el fan-out leyó de Woo (en vivo, en cada venta o cambio).
          select distinct on (sku) sku::text as sku, ts, stock_drop
            from ops.fanout_log
           where stock_drop is not null and coalesce(canal, '') <> 'woocommerce'
             and ts > now() - interval '3 days'
           order by sku, ts desc, id desc),
        v as (
          select l.canal, l.sku::text as sku, a.label as cuenta, a.legacy_code as cuenta_codigo,
                 l.status, l.situacion,
                 -- Woo vigente: la foto de stock_watch (cada 20 min) o, si es más nueva, la
                 -- lectura del fan-out (= `_woo_vigente`).
                 case when r.ts is not null and r.ts > p.actualizado then r.stock_drop
                      else p.stock_woo end as stock_woo,
                 case when w.ts is not null and w.ts > l.updated_at then w.objetivo
                      else l.stock_own end as valor
            from channel.listings l
            join ops.stock_watch_photo p on p.sku = l.sku
            left join core.accounts a on a.id = l.account_id
            left join w on w.sku = l.sku::text and w.canal = l.canal
            left join r on r.sku = l.sku::text
           where l.canal = any(%(c)s)
             and coalesce(l.is_fulfillment, false) = false
             and coalesce(l.logistic_type, '') <> 'fulfillment'
             and l.stock_own is not null and p.stock_woo is not null
             and ((l.canal = 'tiktok' and l.status = 'ACTIVATE')
               or (l.canal = 'temu' and l.status = '2/8')   -- = _TEMU_A_LA_VENTA
               or (l.canal = 'mercado_libre' and l.situacion = 'active')))"""


def _skus_vivos(rep: list[str]) -> list[str]:
    """Todos los SKUs con al menos una publicación a la venta en el reparto."""
    return [f["sku"] for f in sdb.fetch_all(_SQL_VIVAS + " select distinct sku from v order by sku", {"c": rep})]


def _coincidencia(rep: list[str]) -> tuple[list[dict], list[dict]]:
    """Publicaciones A LA VENTA de cada canal contra Woo. El valor de cada una es
    el más reciente entre el censo del canal y la última escritura buena del
    fan-out (el censo de Temu pasa cada 4 h; sin esto, un cambio ya escrito
    parecería una diferencia). Devuelve el conteo por canal y TODAS las que no
    coinciden (de más y de menos)."""
    base = _SQL_VIVAS
    conteo = sdb.fetch_all(base + """
        select canal, count(*) as vivas,
               count(*) filter (where valor = stock_woo) as iguales,
               count(*) filter (where valor > stock_woo) as de_mas,
               count(*) filter (where valor < stock_woo) as de_menos
          from v group by canal""", {"c": rep})
    # Primero las que ofrecen de MÁS (riesgo de vender lo que no hay) y, entre
    # ellas, las que tienen Woo en 0; luego las de menos.
    distintas = sdb.fetch_all(base + """
        select canal, sku, cuenta, cuenta_codigo, status, situacion, valor, stock_woo from v
         where valor <> stock_woo
         order by (valor > stock_woo) desc, (stock_woo = 0) desc, abs(valor - stock_woo) desc""",
        {"c": rep})
    por = {f["canal"]: f for f in conteo}
    return [{"canal": c, "nombre": _NOMBRE.get(c, c),
             "vivas": int((por.get(c) or {}).get("vivas") or 0),
             "iguales": int((por.get(c) or {}).get("iguales") or 0),
             "de_mas": int((por.get(c) or {}).get("de_mas") or 0),
             "de_menos": int((por.get(c) or {}).get("de_menos") or 0)} for c in rep], distintas


# ── Ventas sin orden en Odoo ─────────────────────────────────────────────────
# En ABSOLUTO, stock_watch copia a Woo `max(0, free_qty − vendidas_sin_orden)`: las
# piezas ya vendidas cuya orden todavía no nace en Odoo (la orden nace al comprar la
# guía) se esconden de los canales. Por eso Odoo≠Woo NO es por fuerza un desfase.

def _edad(horas: float | None) -> str:
    if horas is None:
        return "—"
    h = float(horas)
    if h < 1:
        return "menos de 1 h"
    return f"{round(h)} h" if h < 48 else f"{int(h // 24)} d"


def _sin_orden(skus: list[str]) -> dict[str, dict[str, Any]]:
    """Por SKU: piezas vendidas sin orden en Odoo, cuántas ventas, de qué canal y la
    más vieja. Mismo criterio que `odoo_ventas_log.piezas_sin_orden` (lo que resta
    stock_watch) y la misma ventana. Solo lee."""
    if not skus:
        return {}
    from services import odoo_ventas_log as oul
    from services import stock_watch
    filas = sdb.fetch_all(
        """select i.sku::text as sku, sum(i.cantidad) as piezas, count(distinct o.external_order_id) as ventas,
                  string_agg(distinct o.canal, ', ') as canales,
                  extract(epoch from now() - min(o.creado_at)) / 3600.0 as horas
             from ops.odoo_sale_orders o
             join ops.odoo_sale_order_items i
               on i.canal = o.canal and i.cuenta = o.cuenta and i.external_order_id = o.external_order_id
            where ((o.odoo_order_id is null and o.accion = %(a)s) or o.accion = %(nc)s)
              and o.creado_at > now() - make_interval(days => %(d)s)
              and i.sku::text = any(%(s)s)
            group by i.sku""",
        {"a": oul.ACCION_ESPERA, "nc": oul.ACCION_SIN_RESERVA, "d": stock_watch._ventana_pendientes(),
         "s": list(skus)})
    return {f["sku"]: {"piezas": int(f["piezas"] or 0), "ventas": int(f["ventas"] or 0),
                       "canales": f["canales"] or "", "edad": _edad(f["horas"])}
            for f in filas if int(f["piezas"] or 0) > 0}


def _explicado(odoo: Any, woo: Any, so: dict | None) -> bool:
    """¿Odoo≠Woo es exactamente lo vendido sin orden? (Woo = max(0, Odoo − pendientes))."""
    if odoo is None or woo is None or not so:
        return False
    from services import stock_watch
    return stock_watch.resta_pendientes() and int(woo) == max(0, int(odoo) - int(so["piezas"]))


def _woo_leido(skus: list[str]) -> dict[str, dict[str, Any]]:
    """Por SKU: lo último que el fan-out leyó de Woo (en vivo, en cada venta o cambio;
    `stock_drop` en la bitácora), de los últimos 3 días. Solo lee."""
    if not skus:
        return {}
    return {f["sku"]: f for f in sdb.fetch_all(
        """select distinct on (sku) sku::text as sku, ts, stock_drop as valor,
                  to_char(ts at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as hora
             from ops.fanout_log
            where sku::text = any(%(s)s) and stock_drop is not null
              and coalesce(canal, '') <> 'woocommerce' and ts > now() - interval '3 days'
            order by sku, ts desc, id desc""", {"z": _ZONA, "s": list(skus)})}


def _woo_vigente(ft: dict, leido: dict | None, hoy: str) -> tuple[Any, str]:
    """El Woo contra el que se compara: el más reciente entre la foto de stock_watch
    (cada 20 min) y lo último que leyó el fan-out. Justo después de una venta la foto
    todavía no se entera y el canal, ya escrito, parecería «cambió el canal».
    Devuelve (valor, de dónde salió)."""
    foto_ts = ft.get("actualizado")
    if leido and leido.get("valor") is not None and (foto_ts is None or leido["ts"] > foto_ts):
        return int(leido["valor"]), f"leído {_fecha(leido['hora'], hoy)}"
    return ft.get("stock_woo"), (f"foto {_fecha(ft['foto_hora'], hoy)}" if ft.get("foto_hora") else "foto")


def _foto() -> dict[str, Any]:
    f = sdb.fetch_one(
        """select count(*) filter (where stock_odoo is not null) as skus_odoo,
                  count(*) filter (where stock_woo is not null) as skus_woo,
                  count(*) filter (where stock_odoo is not null and stock_woo is not null) as ambos,
                  count(*) filter (where stock_odoo is not null and stock_woo is not null
                                     and stock_odoo <> stock_woo) as distintos,
                  count(*) filter (where stock_odoo is null and stock_woo > 0) as sin_odoo_con_piezas,
                  coalesce(sum(stock_woo) filter (where stock_odoo is null and stock_woo > 0), 0)
                    as piezas_sin_odoo,
                  to_char(max(actualizado) at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as ultima
             from ops.stock_watch_photo""", {"z": _ZONA}) or {}
    # Lo que explican las ventas sin orden se separa de lo que no; `peor` es el
    # mayor SIN explicar (el que de verdad hay que mirar).
    dif = sdb.fetch_all(
        """select sku::text as sku, stock_odoo, stock_woo from ops.stock_watch_photo
            where stock_odoo is not null and stock_woo is not null and stock_odoo <> stock_woo
            limit 3000""")
    so = _sin_orden([d["sku"] for d in dif])
    sin_explicar = [d for d in dif if not _explicado(d["stock_odoo"], d["stock_woo"], so.get(d["sku"]))]
    peor = max(sin_explicar, key=lambda d: abs(int(d["stock_woo"]) - int(d["stock_odoo"])), default=None)
    return {**{k: (int(v) if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()) else v)
               for k, v in f.items()}, "peor": peor, "distintos_por_ventas": len(dif) - len(sin_explicar)}


def _stock_watch(hoy: str) -> dict[str, Any]:
    """La última pasada. En producción vive en la memoria del proceso; si este
    proceso no corre stock_watch, se deduce de la bitácora (que solo anota
    pasadas CON cambios — un silencio no dice si hubo pasada)."""
    from services import stock_watch
    est = stock_watch.estado()
    d = sdb.fetch_one(
        """with u as (select max(ts) as t from ops.fanout_log
                       where canal = 'woocommerce' and accion in ('odoo_delta', 'woo_cambio'))
           select to_char(u.t at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as ultimo,
                  (select count(*) from ops.fanout_log
                    where canal = 'woocommerce' and accion in ('odoo_delta', 'woo_cambio')
                      and ts > u.t - interval '3 minutes') as cambios
             from u""", {"z": _ZONA}) or {}
    en_memoria = bool(est.get("ts"))
    return {
        "habilitado": bool(est.get("habilitado")), "modo": est.get("modo"),
        "en_memoria": en_memoria,
        "segundos": est.get("segundos") if en_memoria else None,
        "cambios": (int(est.get("odoo_deltas") or 0) + int(est.get("woo_cambios") or 0))
                   if en_memoria else int(d.get("cambios") or 0),
        "ultima": _fecha(d.get("ultimo"), hoy),
        "pendientes_ventas": est.get("pendientes_ventas") if en_memoria else None,
        "mas_vieja_h": est.get("pendientes_espera_mas_vieja_h") if en_memoria else None,
    }


def _fanout_estado() -> dict[str, Any]:
    from services import fanout_stock
    with fanout_stock._lock:
        cola = len(fanout_stock._pendientes)
    return {"habilitado": fanout_stock.habilitado(), "dry_run": fanout_stock.dry_run(),
            "cola": cola, "debounce_s": fanout_stock.DEBOUNCE_S, "reserva": fanout_stock._reserva()}


def _full_sin_regla() -> list[dict]:
    try:
        from services import fanout_read, stock_full
        tipos: dict[str, int] = {}
        for f in fanout_read.movimientos_full(24):
            t = str(f.get("resultado") or "").split(" ")[0].split(":")[0] or "?"
            if stock_full.EFECTO_EN_WOO.get(t, "DESCONOCIDO") == "DESCONOCIDO":
                tipos[t] = tipos.get(t, 0) + 1
        return [{"tipo": t, "n": n} for t, n in sorted(tipos.items(), key=lambda kv: -kv[1])]
    except Exception as exc:  # noqa: BLE001
        log.warning("fanout_vivo: tipos FULL: %s", exc)
        return []


def _serie(rep: list[str]) -> dict[str, Any]:
    filas = sdb.fetch_all(
        """select to_char((ts at time zone %(z)s)::date, 'YYYY-MM-DD') as dia, canal,
                  count(*) filter (where resultado ilike 'ok%%') as ok,
                  count(*) filter (where resultado like 'ERROR%%') as mal
             from ops.fanout_log
            where accion = 'escribir' and canal = any(%(c)s)
              and ts >= (((now() at time zone %(z)s)::date - 16)::timestamp at time zone %(z)s)
            group by 1, 2""", {"z": _ZONA, "c": rep})
    dias = sdb.fetch_all(
        """select to_char(d, 'YYYY-MM-DD') as dia from generate_series(
             (now() at time zone %(z)s)::date - 16, (now() at time zone %(z)s)::date,
             interval '1 day') as d""", {"z": _ZONA})
    idx = {(f["dia"], f["canal"]): f for f in filas}
    return {"dias": [_dia(d["dia"]) for d in dias],
            "canales": [{"canal": c, "nombre": _NOMBRE.get(c, c),
                         "ok": [int((idx.get((d["dia"], c)) or {}).get("ok") or 0) for d in dias],
                         "mal": [int((idx.get((d["dia"], c)) or {}).get("mal") or 0) for d in dias]}
                        for c in rep]}


def _pulso_24h(rep: list[str]) -> list[dict]:
    """Escrituras por hora de cada canal en las últimas 24 h, la más vieja primero.

    La ventana es la MISMA de `ok_24h`/`rech_24h` (`ts > now() - 24 h`, cubetas de
    una hora contadas hacia atrás desde ahora): la suma de la serie es exactamente
    la cifra del día que muestran las tarjetas, no una parecida."""
    filas = sdb.fetch_all(
        """select canal, least(23, floor(extract(epoch from (now() - ts)) / 3600))::int as hace,
                  count(*) filter (where resultado ilike 'ok%%') as ok,
                  count(*) filter (where resultado like 'ERROR%%') as mal
             from ops.fanout_log
            where accion = 'escribir' and canal = any(%(c)s) and ts > now() - interval '24 hours'
            group by 1, 2""", {"c": rep})
    salida = []
    for c in rep:
        ok, mal = [0] * 24, [0] * 24
        for f in filas:
            if f["canal"] == c:
                i = 23 - int(f["hace"])
                ok[i] += int(f["ok"])
                mal[i] += int(f["mal"])
        salida.append({"canal": c, "nombre": _NOMBRE.get(c, c), "ok": ok, "mal": mal})
    return salida


def _resumen() -> dict[str, Any]:
    rep = reparto()
    ahora = _ahora_local().get("ahora") or ""
    hoy = ahora[:10]
    canales = _estado_canales(rep, hoy)
    coinc, distintas = _coincidencia(rep)
    de_mas = [d for d in distintas if d["valor"] > d["stock_woo"]]
    por_coinc = {c["canal"]: c for c in coinc}
    for c in canales:
        c["coincidencia"] = por_coinc.get(c["canal"])
    foto = _foto()
    sw = _stock_watch(hoy)
    full = _full_sin_regla()
    rechazan = [c for c in canales if c["estado"] == "rechaza"]
    llegan = [c for c in canales if c["estado"] != "rechaza"]

    # Veredicto en lenguaje llano.
    frase = f"Tu stock llega a {len(llegan)} de {len(canales)} canales."
    if not rechazan:
        frase = ("Tu stock llega a todos los canales." if len(canales) > 1
                 else "Tu stock llega a su canal.")
    partes = []
    for c in rechazan:
        dias = c.get("racha_dias")
        cuando = (f"desde hace {dias} días" if dias and dias > 1
                  else f"desde el {c['racha_desde']}")
        partes.append(f"{c['nombre']} lo rechaza {cuando}.")
    al_dia = [c["nombre"] for c in canales if c["estado"] in ("al_dia", "sin_cambios")]
    if al_dia:
        partes.append(" y ".join([", ".join(al_dia[:-1]), al_dia[-1]] if len(al_dia) > 1 else al_dia)
                      + (" están al día." if len(al_dia) > 1 else " está al día."))
    con_err = [c["nombre"] for c in canales if c["estado"] == "con_errores"]
    if con_err:
        partes.append(f"{' y '.join(con_err)} con algunos rechazos en 24 h.")

    # Qué atender, de lo más urgente a lo que puede esperar.
    atender: list[dict] = []
    for c in rechazan:
        txt = f"Ningún cambio aceptado desde el {c['ultimo_ok']}; {c['racha']:,} rechazados."
        if c.get("error_permiso"):
            txt += " La app que escribe no está autorizada en la cuenta."
        atender.append({"nivel": "urgente", "titulo": f"{c['nombre']} rechaza el stock",
                        "texto": txt, "rastro": c.get("ultimo_rechazo")})
    for f in de_mas[:1]:
        nombre = (f"ML {f['cuenta']}" if f["canal"] == "mercado_libre" and f.get("cuenta")
                  else _NOMBRE.get(f["canal"], f["canal"]))
        extra = len(de_mas) - 1
        atender.append({"nivel": "hoy",
                        "titulo": f"{nombre} ofrece {f['valor']:,} de {f['sku']} con Woo en {f['stock_woo']:,}",
                        "texto": ("Está a la venta." + (f" Hay {extra} más que ofrecen de más." if extra > 0 else "")),
                        "matriz": True})
    por_ventas = int(foto.get("distintos_por_ventas") or 0)
    sin_explicar = int(foto.get("distintos") or 0) - por_ventas
    if sin_explicar > 0:
        p = foto.get("peor") or {}
        atender.append({"nivel": "semana", "titulo": f"{sin_explicar:,} SKUs con Odoo distinto de Woo",
                        "texto": ("No lo explican ventas sin orden. "
                                  + (f"El mayor: {p.get('sku')}, {p.get('stock_odoo'):,} en Odoo y "
                                     f"{p.get('stock_woo'):,} en Woo." if p else "")),
                        "matriz": True})
    if sw.get("pendientes_ventas"):
        h = sw.get("mas_vieja_h") or 0
        atender.append({"nivel": "semana",
                        "titulo": f"{sw['pendientes_ventas']} ventas esperan su orden en Odoo",
                        "texto": ("Se restan del stock mientras tanto"
                                  + (f" (son toda la diferencia Odoo↔Woo de {por_ventas} SKUs)" if por_ventas else "")
                                  + f". La más vieja lleva {h:.0f} h."),
                        "matriz": bool(por_ventas)})
    if foto.get("sin_odoo_con_piezas"):
        atender.append({"nivel": "despues",
                        "titulo": f"{foto['sin_odoo_con_piezas']} SKUs con piezas en Woo y sin producto en Odoo",
                        "texto": f"Son {foto['piezas_sin_odoo']:,} piezas que nada actualiza."})
    if full:
        atender.append({"nivel": "semana", "titulo": f"{len(full)} tipos de movimiento FULL sin regla",
                        "texto": ", ".join(f"{t['tipo']} ×{t['n']}" for t in full[:3]) + " en 24 h.",
                        "full": True})

    # Lo que va bien.
    bien: list[str] = []
    sanos = [c for c in canales if c["estado"] == "al_dia" and c["ok_24h"] > 0]
    if sanos:
        n = sum(c["ok_24h"] for c in sanos)
        nombres = " y ".join(c["nombre"] for c in sanos)
        bien.append(f"{nombres} recibieron {n:,} cambios en 24 h sin un solo rechazo.")
    if foto.get("ambos"):
        bien.append(f"Odoo y Woo coinciden en {foto['ambos'] - foto.get('distintos', 0):,} "
                    f"de {foto['ambos']:,} productos"
                    + (f"; en otros {por_ventas} la diferencia son ventas que esperan su orden en Odoo"
                       if por_ventas else "") + ".")
    if sw.get("ultima") and sw.get("ultima") != "—":
        bien.append(f"Última pasada de Odoo con cambios: {sw['ultima']}"
                    + (f", {sw['cambios']} cambios." if sw.get("cambios") else "."))

    return {
        "ahora": _fecha(ahora, hoy), "reparto": rep,
        "fuera_reparto": [c for c in ("amazon", "walmart") if c not in rep],
        "veredicto": {"llegan": len(llegan), "total": len(canales), "frase": frase,
                      "detalle": " ".join(partes), "grave": bool(rechazan)},
        "canales": canales, "foto": foto, "stock_watch": sw,
        "atender": atender, "bien": bien, "serie": _serie(rep), "pulso": _pulso_24h(rep),
        "full_sin_regla": full,
        "columnas": [c for c in COLUMNAS if c["canal"] in rep],
    }


def _resumen_cacheado() -> dict[str, Any]:
    with _cache_lock:
        if _cache["v"] is not None and time.time() - _cache["t"] < 20:
            return _cache["v"]
        v = _resumen()
        _cache.update(t=time.time(), v=v)
        return v


def vivo(desde_id: int = 0) -> dict[str, Any]:
    r = _resumen_cacheado()
    ev = eventos(desde_id, 14 if desde_id == 0 else 60)
    sin = sdb.fetch_one(
        """select count(*) as n from (
             select 1 from ops.fanout_log
              where accion = any(%(a)s) and coalesce(canal, '') <> 'woocommerce'
                and ts > now() - interval '1 hour'
              group by sku, ts having not bool_or(canal = any(%(r)s))) x""",
        {"a": _ACC_FANOUT, "r": reparto()}) or {}
    return {**r, "fanout": _fanout_estado(), "eventos": ev, "sin_reparto_1h": int(sin.get("n") or 0),
            "ultimo_id": max([e["id"] for e in ev] + [int(desde_id)])}


# ── Matriz de coincidencia ───────────────────────────────────────────────────

_ESTADO_TXT = {
    "tiktok": {"ACTIVATE": "a la venta", "DRAFT": "borrador", "SELLER_DEACTIVATED": "desactivada",
               "DELETED": "borrada", "PENDING": "en revisión", "FAILED": "falló"},
    "mercado_libre": {"active": "a la venta", "paused": "pausada", "under_review": "en revisión",
                      "closed": "cerrada", "inactive": "inactiva"},
}


def _vendible(l: dict) -> bool:
    if l["canal"] == "tiktok":
        return l.get("status") == "ACTIVATE"
    if l["canal"] == "temu":
        return l.get("status") == _TEMU_A_LA_VENTA
    return l.get("situacion") == "active"


def _estado_listing(l: dict) -> str:
    if l["canal"] == "temu":
        s = l.get("status") or ""
        return ("a la venta" if s == _TEMU_A_LA_VENTA else "borrador" if s.startswith("5/")
                else f"estado {s or '—'}")
    clave = l.get("status") if l["canal"] == "tiktok" else l.get("situacion")
    return _ESTADO_TXT.get(l["canal"], {}).get(clave or "", (clave or "sin estado").lower())


def _celda_matriz(l: dict | None, w: dict | None, woo: int | None, hoy: str) -> dict[str, Any]:
    if l is None:
        return {"k": "nopub", "v": "—", "d": "", "s": "no publicada", "p": False}
    estado = _estado_listing(l)
    if l.get("is_fulfillment") or l.get("logistic_type") == "fulfillment":
        return {"k": "full", "v": "FULL", "d": "", "s": estado, "p": False}
    if l["canal"] == "tiktok" and l.get("status") == "DELETED":
        return {"k": "nopub", "v": "borrada", "d": "", "s": f"censo {_fecha(l.get('act'), hoy)}", "p": False}
    vend = _vendible(l)
    valor = l.get("stock_own")
    fresc = (f"igual desde {_fecha(l.get('act'), hoy)}" if l["canal"] == "mercado_libre"
             else f"censo {_fecha(l.get('act'), hoy)}")
    if w and w.get("ts") and l.get("updated_at") and w["ts"] > l["updated_at"]:
        if str(w.get("resultado") or "").lower().startswith("ok"):
            valor, fresc = w.get("objetivo"), f"escrito {_fecha(w.get('hora'), hoy)}"
        elif str(w.get("resultado") or "").startswith("ERROR"):
            cod = "403" if "403" in str(w.get("resultado")) else "error"
            return {"k": "rech", "v": _n(valor), "d": f"· {cod}",
                    "s": f"{estado} · intento {_fecha(w.get('hora'), hoy)}", "p": False}
    if valor is None or woo is None:
        return {"k": "nopub", "v": _n(valor), "d": "", "s": f"{estado} · {fresc}", "p": not vend}
    v, wv = int(valor), int(woo)
    k = "igual" if v == wv else ("mas" if v > wv else "menos")
    d = "" if v == wv else (f"+{v - wv:,}" if v > wv else f"−{wv - v:,}")
    return {"k": k, "v": _n(v), "d": d, "s": f"{estado} · {fresc}", "p": not vend}


_CAUSA_TXT = {
    "403": "rechazado (403)",
    "error": "error al escribir",
    "perdido": "cambio perdido",
    "camino": "en camino",
    "canal": "cambió el canal",
    "tarde": "sin alinear",
    "fuera": "no recibe stock",
}


def _no_destino(l: dict | None) -> str | None:
    """Por qué el fan-out NO le escribe a esta publicación (None = sí le escribe).

    Copia la política de `fanout_stock._destinos`: desde el 19-ago TikTok y Temu
    reciben TODO salvo lo borrado y los borradores de Temu (el canal rechaza
    editarlos); en ML, las activas y las pausadas no-FULL."""
    if not l:
        return "No hay publicación registrada."
    canal = l.get("canal")
    if canal == "tiktok" and str(l.get("status") or "").upper() == "DELETED":
        return "Borrada en TikTok: el fan-out ya no le escribe."
    if canal == "temu":
        from services import temu as _temu
        if _temu.ESTADOS.get(str(l.get("status") or "")) == "Borrador":
            return "Borrador de Temu: el canal no deja editarle el stock."
    if canal == "mercado_libre":
        sit = str(l.get("situacion") or "").lower()
        if sit not in ("active", "published", "publish", "paused"):
            return f"Mercado Libre la tiene «{sit or 'sin estado'}»: escribirle la reactivaría."
    return None


def _causas(celdas: list[dict], hoy: str) -> dict[tuple[str, str, str], dict]:
    """POR QUÉ una publicación no coincide con Woo. Se contesta con la bitácora:

      1. Woo cambió y el fan-out NUNCA procesó ese cambio → «cambio perdido» (la
         cola vive en memoria: un reinicio a media cola la tira; el 29-sep un
         despliegue se llevó 69 cambios), o «en camino» si fue hace < 15 min.
      2. El último reparto del SKU sí la tocó → lo que contestó el canal: 403 o
         error, omitida con su motivo, o quedó igual y DESPUÉS cambió el canal.
      3. El último reparto no la incluyó → o no recibe stock por política, o
         apareció después y nadie la alineó (el fan-out solo actúa cuando Woo
         cambia).
    `celdas`: [{sku, canal, cuenta (legacy_code), listing}]."""
    if not celdas:
        return {}
    skus = sorted({c["sku"] for c in celdas})
    ahora = (sdb.fetch_one("select now() as n") or {}).get("n")
    cambios = {f["sku"]: f for f in sdb.fetch_all(
        """select distinct on (sku) sku::text as sku, ts, resultado,
                  to_char(ts at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as hora
             from ops.fanout_log
            where sku::text = any(%(s)s) and canal = 'woocommerce'
              and accion in ('odoo_delta', 'woo_cambio') and resultado not like '%%FALLÓ%%'
            order by sku, ts desc, id desc""", {"z": _ZONA, "s": skus})}
    ultimos = {f["sku"]: f for f in sdb.fetch_all(
        """select distinct on (sku) sku::text as sku, ts,
                  to_char(ts at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as hora
             from ops.fanout_log
            where sku::text = any(%(s)s) and accion = any(%(a)s) and coalesce(canal, '') <> 'woocommerce'
            order by sku, ts desc, id desc""", {"z": _ZONA, "s": skus, "a": _ACC_FANOUT})}
    del_ev: dict[str, list[dict]] = {}
    if ultimos:
        for f in sdb.fetch_all(
            """select sku::text as sku, canal, upper(coalesce(cuenta, '')) as cuenta, accion,
                      resultado, stock_canal, objetivo
                 from ops.fanout_log
                where (sku::text, ts) in (select * from unnest(%(s)s::text[], %(t)s::timestamptz[]))
                  and accion = any(%(a)s)""",
            {"s": list(ultimos), "t": [u["ts"] for u in ultimos.values()], "a": _ACC_FANOUT}):
            del_ev.setdefault(f["sku"], []).append(f)

    ult_ml = sdb.fetch_one(
        """select resultado from ops.fanout_log where canal = 'mercado_libre' and accion = 'escribir'
            order by ts desc, id desc limit 1""") or {}
    ml_rechaza = "403" in str(ult_ml.get("resultado") or "")

    salida: dict[tuple[str, str, str], dict] = {}
    for c in celdas:
        sku, canal, cuenta = c["sku"], c["canal"], (c.get("cuenta") or "").upper()
        nombre = next((x["nombre"] for x in COLUMNAS if x["canal"] == canal
                       and (canal != "mercado_libre" or x["cuenta"] == cuenta)), _NOMBRE.get(canal, canal))
        cam, ult = cambios.get(sku), ultimos.get(sku)
        razon = _no_destino(c.get("listing")) if c.get("listing") else None
        if cam and (not ult or ult["ts"] < cam["ts"]) and razon:
            # Lo que el fan-out no escribe por política (borrador de Temu, borrada en
            # TikTok…) no «perdió» el cambio: nunca iba a recibirlo.
            codigo, texto, detalle = "fuera", _CAUSA_TXT["fuera"], razon
        elif cam and (not ult or ult["ts"] < cam["ts"]):
            m = _FLECHA.search(str(cam.get("resultado") or ""))
            mov = f" de {m.group(1)} a {m.group(2)}" if m else ""
            if ahora and (ahora - cam["ts"]).total_seconds() < 900:
                codigo, texto = "camino", _CAUSA_TXT["camino"]
                detalle = f"Woo cambió{mov} a las {cam['hora'][11:16]}; el fan-out todavía no lo procesa."
            else:
                codigo, texto = "perdido", _CAUSA_TXT["perdido"]
                detalle = f"Woo cambió{mov} {_el(_fecha(cam['hora'], hoy))} y el fan-out nunca procesó ese cambio. "
                if cam["hora"] < "2026-08-19":
                    # v0.206: hasta ahí los cambios de Odoo se escribían en Woo
                    # pero NO se encolaban al fan-out.
                    detalle += "Antes del 19-ago los cambios de Odoo no se mandaban a los canales."
                else:
                    detalle += ("Lo más probable: el backend se reinició con la cola a medias (la cola vive en "
                                "memoria; el 29-sep un despliegue se llevó 69 cambios).")
                if canal == "mercado_libre" and ml_rechaza:
                    detalle += " Aunque se procesara, hoy ML lo rechazaría (403)."

        else:
            cuando = _fecha(ult["hora"], hoy) if ult else "—"
            mias = [f for f in del_ev.get(sku, []) if f.get("canal") == canal
                    and (canal != "mercado_libre" or f.get("cuenta") == cuenta)]
            err = [f for f in mias if f["accion"] == "escribir" and str(f.get("resultado") or "").startswith("ERROR")]
            om = [f for f in mias if f["accion"] == "omitir"]
            bien = [f for f in mias if f["accion"] in ("escribir", "sin_cambio") and f not in err]
            if err:
                r = str(err[0].get("resultado") or "")
                if "403" in r:
                    codigo, texto = "403", _CAUSA_TXT["403"]
                    permiso = "PolicyAgent" in r or "PA_UNAUTHORIZED" in r
                    detalle = (f"{nombre} rechazó el cambio a {_n(err[0].get('objetivo'))} {_el(cuando)}"
                               + (": la app que escribe no está autorizada en la cuenta." if permiso else "."))
                else:
                    codigo, texto = "error", _CAUSA_TXT["error"]
                    detalle = f"{nombre} contestó un error {_el(cuando)}: {r[7:140]}"
            elif om:
                r = str(om[0].get("resultado") or "")
                motivo = _motivo_omision(r)[1]
                codigo, texto = "omitida", ("omitida" if motivo == "omitida" else f"omitida: {motivo}")
                detalle = f"El fan-out no le escribe a propósito ({cuando}): {r[:140]}"
            elif bien:
                codigo, texto = "canal", _CAUSA_TXT["canal"]
                detalle = (f"{_el(cuando)[:1].upper()}{_el(cuando)[1:]} quedó en {_n(bien[0].get('objetivo'))}, igual que Woo; después el canal "
                           "cambió por su cuenta y nada lo vuelve a revisar.")
            else:
                razon = _no_destino(c.get("listing"))
                if razon:
                    codigo, texto, detalle = "fuera", _CAUSA_TXT["fuera"], razon
                else:
                    codigo, texto = "tarde", _CAUSA_TXT["tarde"]
                    detalle = ("No estaba en el último reparto de este SKU" + (f" ({cuando})" if ult else "")
                               + "; apareció después con su propio número y, como Woo no ha vuelto a cambiar, "
                                 "nada la ha alineado.")
        salida[(sku, canal, cuenta)] = {"c": codigo, "t": texto, "d": detalle}
    return salida


def _skus_perdidos(dias: int = 14, limite: int = 60) -> list[str]:
    """SKUs cuyo ÚLTIMO cambio en Woo (de stock_watch) nunca pasó por el fan-out."""
    return [f["sku"] for f in sdb.fetch_all(
        """with c as (
             select distinct on (sku) sku::text as sku, ts from ops.fanout_log
              where canal = 'woocommerce' and accion in ('odoo_delta', 'woo_cambio')
                and resultado not like '%%FALLÓ%%' and ts > now() - make_interval(days => %(d)s)
              order by sku, ts desc, id desc),
           e as (
             select sku::text as sku, max(ts) as t from ops.fanout_log
              where accion = any(%(a)s) and coalesce(canal, '') <> 'woocommerce'
                and ts > now() - make_interval(days => %(d)s + 1)
              group by sku)
           select c.sku from c left join e using (sku)
            where c.ts < now() - interval '15 minutes' and (e.t is null or e.t < c.ts)
            order by c.ts desc limit %(n)s""", {"d": int(dias), "a": _ACC_FANOUT, "n": int(limite)})]


def matriz() -> dict[str, Any]:
    rep = reparto()
    ahora = _ahora_local().get("ahora") or ""
    hoy = ahora[:10]
    cols = [c for c in COLUMNAS if c["canal"] in rep]
    barras, distintas = _coincidencia(rep)

    # Por qué no coinciden las publicaciones A LA VENTA, canal por canal.
    vivas = _causas([{"sku": d["sku"], "canal": d["canal"], "cuenta": d.get("cuenta_codigo"),
                      "listing": d} for d in distintas], hoy)
    for b in barras:
        por: dict[str, dict] = {}
        for d in distintas:
            if d["canal"] != b["canal"]:
                continue
            ca = vivas.get((d["sku"], d["canal"], (d.get("cuenta_codigo") or "").upper()))
            if ca:
                x = por.setdefault(ca["c"], {"c": ca["c"], "t": "omitida" if ca["c"] == "omitida" else ca["t"], "n": 0})
                x["n"] += 1
        b["causas"] = sorted(por.values(), key=lambda x: -x["n"])

    # Qué SKUs entran: TODOS los que tienen algo a la venta en el reparto, más los
    # que piden revisión aunque no estén a la venta: rechazados en 24 h, con un
    # cambio perdido, distintos entre Odoo y Woo y movidos hace poco. Los topes son
    # holgados a propósito: con 20 se escondían SKUs sin avisar (1-oct: 42
    # rechazados y 86 movidos en 6 h).
    dif = sdb.fetch_all(
        """select sku::text as sku from ops.stock_watch_photo
            where stock_odoo is not null and stock_woo is not null and stock_odoo <> stock_woo
            order by abs(stock_woo - stock_odoo) desc limit 300""")
    rech = sdb.fetch_all(
        """select sku::text as sku from ops.fanout_log
            where accion = 'escribir' and resultado like 'ERROR%%' and ts > now() - interval '24 hours'
            group by sku order by max(id) desc limit 300""")
    rec = sdb.fetch_all(
        """select sku::text as sku from ops.fanout_log
            where accion = any(%(a)s) and coalesce(canal, '') <> 'woocommerce'
              and ts > now() - interval '6 hours'
            group by sku order by max(id) desc limit 300""", {"a": _ACC_FANOUT})
    perdidos = _skus_perdidos(limite=300)
    vivos = _skus_vivos(rep)
    otros = ({f["sku"] for f in distintas} | {f["sku"] for f in rech} | {f["sku"] for f in dif}
             | {f["sku"] for f in rec} | set(vivos))
    solo_perdido = set(perdidos) - otros
    skus = list(dict.fromkeys([f["sku"] for f in distintas] + [f["sku"] for f in rech] + perdidos
                              + [f["sku"] for f in dif] + [f["sku"] for f in rec] + vivos))[:2000]
    if not skus:
        return {"ahora": _fecha(ahora, hoy), "columnas": cols, "barras": barras, "filas": []}

    foto = {f["sku"]: f for f in sdb.fetch_all(
        """select sku::text as sku, stock_odoo, stock_woo, actualizado,
                  to_char(actualizado at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as foto_hora
             from ops.stock_watch_photo where sku::text = any(%(s)s)""",
        {"s": skus, "z": _ZONA})}
    leidos = _woo_leido(skus)
    sin_orden = _sin_orden(skus)
    lst = sdb.fetch_all(
        """select l.sku::text as sku, l.canal, a.legacy_code as cuenta, l.status, l.situacion,
                  l.stock_own, l.is_fulfillment, l.logistic_type, l.updated_at,
                  to_char(l.updated_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as act
             from channel.listings l left join core.accounts a on a.id = l.account_id
            where l.sku::text = any(%(s)s) and l.canal = any(%(c)s)""",
        {"z": _ZONA, "s": skus, "c": rep})
    esc = sdb.fetch_all(
        """select distinct on (sku, canal, cuenta) sku::text as sku, canal, upper(cuenta) as cuenta,
                  ts, objetivo, resultado,
                  to_char(ts at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as hora
             from ops.fanout_log
            where sku::text = any(%(s)s) and accion = 'escribir' and ts > now() - interval '3 days'
            order by sku, canal, cuenta, ts desc, id desc""", {"z": _ZONA, "s": skus})
    L = {(f["sku"], f["canal"], (f.get("cuenta") or "").upper()): f for f in lst}
    W = {(f["sku"], f["canal"], f["cuenta"]): f for f in esc}

    celdas_por_sku: dict[str, dict[str, dict]] = {}
    pendientes: list[dict] = []
    for s in skus:
        woo, _ = _woo_vigente(foto.get(s) or {}, leidos.get(s), hoy)
        celdas = {}
        for col in cols:
            clave = (s, col["canal"], col["cuenta"])
            c = _celda_matriz(L.get(clave), W.get(clave), woo, hoy)
            celdas[col["id"]] = c
            if c["k"] in ("mas", "menos", "rech"):
                pendientes.append({"sku": s, "canal": col["canal"], "cuenta": col["cuenta"],
                                   "listing": L.get(clave), "col": col["id"]})
        celdas_por_sku[s] = celdas
    causas = _causas(pendientes, hoy)
    for p in pendientes:
        celdas_por_sku[p["sku"]][p["col"]]["causa"] = causas.get((p["sku"], p["canal"], p["cuenta"].upper()))

    filas = []
    for s in skus:
        ft = foto.get(s) or {}
        odoo = ft.get("stock_odoo")
        woo, woo_de = _woo_vigente(ft, leidos.get(s), hoy)
        celdas, tags, peso = celdas_por_sku[s], set(), 0.0
        resumen = None
        for col in cols:
            c = celdas[col["id"]]
            if c["k"] == "mas" and not c["p"]:
                peso = max(peso, 100 + min(int(str(c["d"]).replace("+", "").replace(",", "") or 0), 500) / 10)
                resumen = resumen or f"{col['nombre']} la ofrece y en Woo hay {_n(woo)}"
            elif c["k"] == "rech":
                peso = max(peso, 80)
                resumen = resumen or f"{col['nombre']} rechazó el cambio"
            elif c["k"] == "menos" and not c["p"]:
                peso = max(peso, 60)
                resumen = resumen or f"{col['nombre']} ofrece menos de lo que hay en Woo"
            elif c["k"] in ("mas", "menos"):
                peso = max(peso, 20)
            if c["k"] in ("mas", "menos", "rech"):
                tags.add("distinto")
            if c["k"] == "mas":
                tags.add("demas")
            if c["k"] == "rech":
                tags.add("rech")
            if c.get("causa"):
                tags.add(f"causa:{c['causa']['c']}")
        # Odoo≠Woo que son EXACTAMENTE las vendidas sin orden no es un desfase: es la
        # resta de stock_watch haciendo su trabajo. Solo lo que no cuadra es «distinto».
        sx = sin_orden.get(s)
        explicado = odoo is not None and woo is not None and odoo != woo and _explicado(odoo, woo, sx)
        dif_ow = odoo is not None and woo is not None and odoo != woo and not explicado
        if dif_ow:
            tags.add("distinto")
            peso = max(peso, 40 + min(abs(int(woo) - int(odoo)), 50) / 5)
            resumen = resumen or (f"Odoo tiene {_n(odoo)} y Woo {_n(woo)}"
                                  + (f"; {sx['piezas']} vendidas sin orden no alcanzan a explicarlo" if sx else ""))
        if sx:
            tags.add("sin_orden")
            if explicado:
                peso = max(peso, 10)
                resumen = resumen or (f"{sx['piezas']} {'vendida' if sx['piezas'] == 1 else 'vendidas'} sin orden "
                                      f"en Odoo; la más vieja hace {sx['edad']}")
        if s in solo_perdido and not tags:
            continue   # el cambio se perdió, pero hoy sus canales coinciden con Woo
        if resumen is None:
            otra = next((col["nombre"] for col in cols if celdas[col["id"]]["k"] in ("mas", "menos")), None)
            resumen = (f"{otra} no coincide con Woo, pero no está a la venta" if otra
                       else "Igual a Woo donde está a la venta")
        filas.append({"sku": s, "que": resumen, "odoo": _n(odoo), "woo": _n(woo), "woo_de": woo_de,
                      "dif": dif_ow, "sin_orden": ({**sx, "explica": explicado} if sx else None),
                      "celdas": celdas, "tags": sorted(tags), "peso": peso})
    filas.sort(key=lambda f: -f["peso"])
    return {"ahora": _fecha(ahora, hoy), "columnas": cols, "barras": barras, "filas": filas}


# ── Rastro de un cambio ───────────────────────────────────────────────────────

def rastro(sku: str, fin: str) -> dict[str, Any]:
    ev = sdb.fetch_all(
        """select sku::text as sku, ts, max(id) as id, max(motivo) as motivo,
                  max(stock_drop) as stock_drop, max(objetivo) as objetivo, max(ms) as ms,
                  to_char(ts at time zone %(z)s, 'HH24:MI:SS') as hora
             from ops.fanout_log
            where sku::text = %(s)s and ts = %(t)s::timestamptz and accion = any(%(a)s)
            group by sku, ts""", {"z": _ZONA, "s": sku, "t": fin, "a": _ACC_FANOUT})
    if not ev:
        return {"ok": False, "motivo": "no encontré ese cambio"}
    filas = sdb.fetch_all(
        """select sku::text as sku, ts, canal, cuenta, accion, stock_canal, objetivo,
                  left(resultado, 300) as resultado
             from ops.fanout_log
            where sku::text = %(s)s and ts = %(t)s::timestamptz and accion = any(%(a)s)
            order by id""", {"s": sku, "t": fin, "a": _ACC_FANOUT})
    e = _armar_eventos(ev, filas)[0]
    # Todos los destinos, incluidos los que quedan fuera del reparto.
    destinos = []
    vistos = set()
    for f in filas:
        canal = f.get("canal") or ""
        cuenta = (f.get("cuenta") or "").upper()
        if (canal, cuenta) in vistos:
            continue
        vistos.add((canal, cuenta))
        nombre = next((c["nombre"] for c in COLUMNAS
                       if c["canal"] == canal and (canal != "mercado_libre" or c["cuenta"] == cuenta)),
                      _NOMBRE.get(canal, canal or "sin destino"))
        celda = _celda_evento([x for x in filas if (x.get("canal") or "") == canal
                               and (x.get("cuenta") or "").upper() == cuenta])
        fuera = canal in ("amazon", "walmart") and canal not in reparto()
        destinos.append({"canal": canal, "nombre": nombre, "fuera": fuera, **celda})
    orden = {c["id"]: i for i, c in enumerate(COLUMNAS)}
    destinos.sort(key=lambda d: (d["fuera"], orden.get(next((c["id"] for c in COLUMNAS if c["nombre"] == d["nombre"]), ""), 9)))

    # La fila: los demás cambios que salieron cerca de este (misma pasada).
    ventana = sdb.fetch_all(
        """select sku::text as sku, ts, to_char(ts at time zone %(z)s, 'HH24:MI:SS') as hora,
                  extract(epoch from ts - %(t)s::timestamptz)::float as dt
             from ops.fanout_log
            where accion = any(%(a)s) and coalesce(canal, '') <> 'woocommerce'
              and ts between %(t)s::timestamptz - interval '150 seconds'
                         and %(t)s::timestamptz + interval '90 seconds'
            group by sku, ts order by ts limit 80""",
        {"z": _ZONA, "t": fin, "a": _ACC_FANOUT})
    filas_v = sdb.fetch_all(
        """select sku::text as sku, ts, canal, cuenta, accion, stock_canal, objetivo, resultado
             from ops.fanout_log
            where ts between %(t)s::timestamptz - interval '150 seconds'
                         and %(t)s::timestamptz + interval '90 seconds'
              and accion = any(%(a)s) and coalesce(canal, '') <> 'woocommerce'""",
        {"t": fin, "a": _ACC_FANOUT})
    por: dict[tuple[str, Any], list[dict]] = {}
    for f in filas_v:
        por.setdefault((f["sku"], f["ts"]), []).append(f)
    fila = []
    for v in ventana:
        mias = por.get((v["sku"], v["ts"]), [])
        celdas = {c["id"]: _celda_evento([f for f in mias if f.get("canal") == c["canal"]
                                          and (c["canal"] != "mercado_libre"
                                               or (f.get("cuenta") or "").upper() == c["cuenta"])])
                  for c in COLUMNAS}
        fila.append({"sku": v["sku"], "hora": v["hora"], "dt": round(float(v["dt"]), 1),
                     "fin": v["ts"].isoformat(), "tono": _tono(celdas), "este": v["sku"] == sku and abs(float(v["dt"])) < 0.5})
    return {"ok": True, **e, "motivo": ev[0].get("motivo"), "destinos": destinos, "fila": fila}


# ── Trazabilidad de un SKU ────────────────────────────────────────────────────

_CAMPOS_TRAZA = ["stock_own", "status", "situacion"]
_VIA_TXT = {"temu_censo": "censo de Temu", "tiktok_censo": "censo de TikTok", "sync": "lectura de ML",
            "corte_channel": "lectura de ML"}


def _entero(v: Any) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _nombre_destino(canal: str, cuenta: str) -> str:
    return next((c["nombre"] for c in COLUMNAS if c["canal"] == canal
                 and (canal != "mercado_libre" or c["cuenta"] == cuenta)), _NOMBRE.get(canal, canal or "sin destino"))


def _clave_destino(canal: str, cuenta: str) -> tuple[str, str]:
    """ML va por cuenta (cada una falla o funciona por su lado); el resto, por canal."""
    return canal, (cuenta if canal == "mercado_libre" else "")


def _estado_traza(canal: str, valor: str | None) -> str:
    if valor in (None, ""):
        return "sin estado"
    if canal == "temu":
        v = str(valor)
        return ("a la venta (2/8)" if v == _TEMU_A_LA_VENTA else f"borrador ({v})" if v.startswith("5/")
                else v)        # el resto, crudo: su significado no está confirmado
    return _ESTADO_TXT.get(canal, {}).get(str(valor), str(valor).lower())


def historia(sku: str, dias: int = 14, limite: int = 400) -> dict[str, Any]:
    """La línea de trazabilidad de UN SKU: lo que le pasó en los últimos `dias`.

    Junta tres fuentes de kubera (solo lee):
      · `ops.fanout_log` con canal 'woocommerce': cada cambio de stock en Woo que
        anotó stock_watch (vino de Odoo, o lo detectó en Woo: una venta, una edición);
      · el resto de `ops.fanout_log`: cada reparto del fan-out y lo que contestó
        cada destino;
      · `channel.listing_history`: lo que cada canal del reparto REPORTÓ después
        (censo de TikTok y Temu, lectura de ML): stock, estado y situación;
      · el carril FULL: cada aviso de la bodega de ML (`full_*` en la bitácora,
        uno por operación) y la foto del stock FULL que lee el sync. FULL no toca
        Woo: va aparte para no confundirse con el reparto.
    Cada lectura de stock se compara con lo último que el fan-out le dejó a ese
    canal (escrito, o ya igual): si coincide fue nuestro; si no, cambió en el
    canal —una venta ahí, una cancelación, alguien en el Seller Center o un dato
    que el canal tarda en reflejar—."""
    sku = (sku or "").strip()
    dias = max(1, min(int(dias or 14), 60))
    rep = reparto()
    ahora = _ahora_local().get("ahora") or ""
    hoy = ahora[:10]
    foto = sdb.fetch_one(
        """select stock_odoo, stock_woo, actualizado,
                  to_char(actualizado at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as foto_hora
             from ops.stock_watch_photo where sku = %(s)s""", {"s": sku, "z": _ZONA}) or {}
    log = sdb.fetch_all(
        """select id, ts, to_char(ts at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as hora,
                  coalesce(motivo, '') as motivo, coalesce(canal, '') as canal,
                  upper(coalesce(cuenta, '')) as cuenta, accion, left(coalesce(resultado, ''), 300) as resultado,
                  stock_canal, objetivo, stock_drop, (ts > now() - interval '3 days') as reciente,
                  coalesce(item_id, '') as item_id
             from ops.fanout_log
            where sku = %(s)s and ts > now() - make_interval(days => %(d)s)
            order by ts desc, id desc limit 3000""", {"z": _ZONA, "s": sku, "d": dias})
    hist = sdb.fetch_all(
        """select h.changed_at as ts, to_char(h.changed_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as hora,
                  h.canal, upper(coalesce(a.legacy_code, '')) as cuenta, h.campo,
                  h.valor_anterior, h.valor_nuevo, coalesce(h.detectado_via, '') as via
             from channel.listing_history h left join core.accounts a on a.id = h.account_id
            where h.sku = %(s)s and h.canal = any(%(c)s) and h.campo = any(%(f)s)
              and h.changed_at > now() - make_interval(days => %(d)s)
            order by h.changed_at desc limit 2000""",
        {"z": _ZONA, "s": sku, "c": rep, "f": _CAMPOS_TRAZA + ["stock_full"], "d": dias})
    lst = sdb.fetch_all(
        """select l.sku::text as sku, l.canal, a.legacy_code as cuenta, l.status, l.situacion,
                  l.stock_own, l.stock_full, l.is_fulfillment, l.logistic_type, l.updated_at, l.listing_id,
                  to_char(l.updated_at at time zone %(z)s, 'YYYY-MM-DD HH24:MI:SS') as act
             from channel.listings l left join core.accounts a on a.id = l.account_id
            where l.sku = %(s)s and l.canal = any(%(c)s)""", {"z": _ZONA, "s": sku, "c": rep})

    # Hoy en cada canal: la misma celda (y la misma causa) que la matriz.
    cols = [c for c in COLUMNAS if c["canal"] in rep]
    leido = next(({"ts": f["ts"], "valor": f["stock_drop"], "hora": f["hora"]} for f in log
                  if f.get("stock_drop") is not None and f["canal"] not in ("", "woocommerce")), None)
    woo, woo_de = _woo_vigente(foto, leido, hoy)
    L = {(f["canal"], (f.get("cuenta") or "").upper()): f for f in lst}
    W: dict[tuple[str, str], dict] = {}
    for f in log:
        if f["accion"] == "escribir" and f["canal"] not in ("", "woocommerce") and f["reciente"]:
            W.setdefault((f["canal"], f["cuenta"]), f)
    celdas, pendientes = {}, []
    for col in cols:
        clave = (col["canal"], col["cuenta"])
        c = _celda_matriz(L.get(clave), W.get(clave), woo, hoy)
        celdas[col["id"]] = c
        if c["k"] in ("mas", "menos", "rech"):
            pendientes.append({"sku": sku, "canal": col["canal"], "cuenta": col["cuenta"],
                               "listing": L.get(clave), "col": col["id"]})
    causas = _causas(pendientes, hoy)
    for p in pendientes:
        celdas[p["col"]]["causa"] = causas.get((sku, p["canal"], p["cuenta"].upper()))

    items: list[dict] = []
    # 1) Cada cambio de stock en Woo.
    for f in log:
        if f["canal"] != "woocommerce" or f["accion"] not in ("odoo_delta", "woo_cambio"):
            continue
        m = _FLECHA.search(f["resultado"])
        items.append({"tipo": "woo", "_t": f["ts"], "ts": f["ts"].isoformat(), "hora": f["hora"],
                      "origen": "odoo" if f["accion"] == "odoo_delta" else "woo",
                      "de": int(m.group(1)) if m else None, "a": int(m.group(2)) if m else None,
                      "fallo": "FALLÓ" in f["resultado"], "motivo": f["motivo"][:140]})

    # 2) Cada reparto: sus filas comparten `ts` (el fin del cambio).
    grupos: dict[Any, list[dict]] = {}
    for f in log:
        if f["canal"] != "woocommerce" and f["accion"] in _ACC_FANOUT:
            grupos.setdefault(f["ts"], []).append(f)
    orden = {(c["canal"], c["cuenta"] if c["canal"] == "mercado_libre" else ""): i for i, c in enumerate(COLUMNAS)}
    for ts, filas in grupos.items():
        destinos, vistos = [], set()
        for f in filas:
            if not f["canal"]:
                continue
            clave = _clave_destino(f["canal"], f["cuenta"])
            if clave in vistos:
                continue
            vistos.add(clave)
            celda = _celda_evento([x for x in filas if _clave_destino(x["canal"], x["cuenta"]) == clave])
            destinos.append({"canal": f["canal"], "nombre": _nombre_destino(f["canal"], f["cuenta"]),
                             "fuera": f["canal"] not in rep, **celda, "_o": orden.get(clave, 9)})
        destinos.sort(key=lambda d: (d["fuera"], d["_o"]))
        for d in destinos:
            del d["_o"]
        ks = [d["k"] for d in destinos if not d["fuera"]]
        tono = "mal" if "mal" in ks else ("ok" if "ok" in ks else ("full" if ks and set(ks) <= {"full"} else "omit"))
        motivo = filas[0]["motivo"]
        bajo = motivo.lower()
        origen = ("venta" if bajo.startswith("venta") else "recuperado" if bajo.startswith("recuperado")
                  else "reenvio" if "reenv" in bajo else "cambio")
        items.append({"tipo": "reparto", "_t": ts, "ts": ts.isoformat(), "fin": ts.isoformat(),
                      "hora": filas[0]["hora"], "motivo": motivo[:140], "origen": origen, "tono": tono,
                      "destinos": destinos, "sin_destinos": any(f["accion"] == "sin_destinos" for f in filas)})

    # 3) Lo que reportó cada canal, contra lo último que el fan-out le dejó.
    dejado: dict[tuple[str, str], list[tuple]] = {}   # más reciente primero (como `log`)
    for f in log:
        if f["canal"] in ("", "woocommerce"):
            continue
        ok = f["accion"] == "escribir" and f["resultado"].lower().startswith("ok")
        if ok or f["accion"] == "sin_cambio":
            dejado.setdefault(_clave_destino(f["canal"], f["cuenta"]), []).append((f["ts"], f["objetivo"], f["hora"]))
    for h in hist:
        if h["campo"] == "stock_full":
            if h["canal"] == "mercado_libre":
                items.append({"tipo": "full", "_t": h["ts"], "ts": h["ts"].isoformat(), "hora": h["hora"],
                              "cuenta": h["cuenta"], "nombre": f"FULL {fanout_full.CUENTAS.get(h['cuenta'], h['cuenta'])}",
                              "ml_tipo": "FOTO", "texto": "Lectura del sync", "grupo": "foto",
                              "de": _entero(h["valor_anterior"]), "a": _entero(h["valor_nuevo"]),
                              "sig": _VIA_TXT.get(h["via"], h["via"] or "lectura")})
            continue
        clave = _clave_destino(h["canal"], h["cuenta"])
        it = {"tipo": "canal", "_t": h["ts"], "ts": h["ts"].isoformat(), "hora": h["hora"], "canal": h["canal"],
              "nombre": _nombre_destino(h["canal"], h["cuenta"]), "campo": h["campo"],
              "via": _VIA_TXT.get(h["via"], h["via"] or "lectura")}
        if h["campo"] == "stock_own":
            de, a = _entero(h["valor_anterior"]), _entero(h["valor_nuevo"])
            previo = next((p for p in dejado.get(clave, []) if p[0] <= h["ts"]), None)
            if previo is None:
                rel, ref = "sin_escritura", None
            else:
                ref = {"valor": _entero(previo[1]), "hora": previo[2]}
                rel = "coincide" if ref["valor"] is not None and ref["valor"] == a else "su_cuenta"
            it.update({"de": de, "a": a, "relacion": rel, "ref": ref})
        else:
            it.update({"relacion": "estado", "de_txt": _estado_traza(h["canal"], h["valor_anterior"]),
                       "a_txt": _estado_traza(h["canal"], h["valor_nuevo"])})
        items.append(it)

    # 4) FULL: cada aviso de la bodega de ML (uno por operación: ML reenvía, y el
    #    primero que llegó gana) y, arriba, lo que tiene hoy cada cuenta.
    full_items: list[dict] = []
    vistas: set[str] = set()
    for f in reversed(log):
        if not str(f["accion"]).startswith("full_"):
            continue
        op = f["item_id"] or f"id{f['id']}"
        if op in vistas:
            continue
        vistas.add(op)
        a = fanout_full.aviso({**f, "sku": sku})
        full_items.append({"tipo": "full", "_t": f["ts"], "ts": f["ts"].isoformat(), "hora": f["hora"],
                           "cuenta": a["cuenta"], "nombre": f"FULL {a['nombre']}", "ml_tipo": a["tipo"],
                           "texto": a["texto"], "grupo": a["grupo"], "x": a["x"], "sig": a["sig"]})
    items.extend(full_items)
    full = _full_de(sku, [f for f in lst if f["canal"] == "mercado_libre" and f.get("is_fulfillment")], full_items)

    items.sort(key=lambda x: x["_t"], reverse=True)
    resumen = {
        "cambios_woo": sum(1 for x in items if x["tipo"] == "woo"),
        "repartos": sum(1 for x in items if x["tipo"] == "reparto"),
        "con_rechazo": sum(1 for x in items if x["tipo"] == "reparto" and x["tono"] == "mal"),
        "su_cuenta": sum(1 for x in items if x.get("relacion") == "su_cuenta"),
        "full": len(full_items),
    }
    total = len(items)
    for x in items:
        x.pop("_t", None)
    sx = _sin_orden([sku]).get(sku) if sku else None
    return {"ok": True, "sku": sku, "dias": dias, "hoy": hoy, "ahora": _fecha(ahora, hoy),
            "existe": bool(foto or lst or log or hist), "odoo": _n(foto.get("stock_odoo")), "woo": _n(woo), "woo_de": woo_de,
            "sin_orden": ({**sx, "explica": _explicado(foto.get("stock_odoo"), woo, sx)} if sx else None),
            "columnas": cols, "celdas": celdas, "resumen": resumen, "full": full,
            "items": items[:limite], "total": total, "truncado": total > limite}


def _full_de(sku: str, filas: list[dict], avisos: list[dict]) -> dict[str, Any] | None:
    """El carril FULL de un SKU: cuánto tiene hoy cada cuenta, cuánto vendió por
    FULL en 14 días (le alcanza para N días) y la suma de sus avisos por grupo.
    None si el SKU no tiene publicación FULL ni avisos de FULL."""
    if not filas and not avisos:
        return None
    ventas = {r["cuenta"]: int(r["u"] or 0) for r in sdb.fetch_all(
        """select upper(cuenta) as cuenta, sum(units_sold) as u from channel.sales_daily_completa
            where sku = %(s)s and is_full and date >= (now() at time zone %(z)s)::date - 14
            group by 1""", {"s": sku, "z": _ZONA})}
    # La fila que cuenta de cada publicación (`full_publicaciones`): si la de este SKU
    # no es, la publicación declara OTRO SKU y este número es viejo.
    lids = sorted({str(f["listing_id"]) for f in filas if f.get("listing_id")})
    duenas = {(r["cuenta"], r["listing_id"]): r for r in sdb.fetch_all(
        full_publicaciones.CTE + """
        select cuenta, listing_id, sku, st from h where cuenta_fila and listing_id = any(%(l)s)""",
        {"l": lids})} if lids else {}
    cuentas = []
    for c, nombre in fanout_full.CUENTAS.items():
        fl = [f for f in filas if (f.get("cuenta") or "").upper() == c]
        if not fl and not ventas.get(c) and not any(a["cuenta"] == c for a in avisos):
            continue
        de_otro = None
        propias = []
        for f in fl:
            d = duenas.get((c, str(f.get("listing_id") or "")))
            if d and str(d["sku"]).upper() != sku.upper():
                de_otro = {"sku": d["sku"], "stock": int(d["st"] or 0), "listing": f["listing_id"]}
            else:
                propias.append(f)
        stock = sum(int(f.get("stock_full") or 0) for f in propias)
        u = ventas.get(c, 0)
        cuentas.append({"cuenta": c, "nombre": nombre, "stock": stock, "de_otro": de_otro,
                        "situacion": _estado_traza("mercado_libre", fl[0]["situacion"]) if fl else None,
                        "cambio": fl[0]["act"] if fl else None, "vendidas_14d": u,
                        "cobertura": round(stock / (u / 14), 1) if u else None})
    grupos: dict[str, dict[str, int]] = {}
    for a in avisos:
        g = grupos.setdefault(a["grupo"], {"avisos": 0, "piezas": 0})
        g["avisos"] += 1
        g["piezas"] += a["x"]
    return {"cuentas": cuentas, "grupos": grupos}
