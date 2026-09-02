"""
flujo.py — La Red viva: el flujo de datos de kubera para dibujarlo en vivo.

  GET /api/flujo/topologia   → nodos y aristas (casi no cambia; se pide al cargar)
  GET /api/flujo/pulso       → caudal, salud, silencios e historial (cada ~10 s)
  GET /api/flujo/nodo/{id}   → últimos eventos de un nodo (al hacer clic)

Nació como herramienta local (flujo-vivo/servidor.py) y se portó aquí casi
literal. Dos ideas cargan todo el diseño:

1. `pg_stat_user_tables` da el caudal de escritura de TODAS las tablas sin
   saber nada de sus columnas — por eso la base entera late, no solo las
   cuatro bitácoras de ops.
2. El cableado proceso→tabla va A MANO en `CABLES` y no se deduce de nada:
   las llaves foráneas son reglas de integridad, no dicen quién escribe ni
   cuándo. El flujo vive en el código y en los crons, así que aquí se declara.

Los SILENCIOS comparan contra la cadencia propia de cada flujo (CADENCIA_MIN),
nunca contra un umbral genérico: la lección del 12-ago (964 pedidos fantasma)
es que lo grave no es el error ruidoso sino el flujo que debería sonar y calla,
y un umbral de "1 hora" enmascara a los que laten cada minuto.

Solo lecturas y agregados: nada de costos, márgenes ni filas de negocio crudas.
Por eso la entrada del navbar NO lleva soloAdmin, igual que Monitoreo.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from core.seguridad import requiere_api_key
from services import supabase_db as sdb

router = APIRouter(prefix="/api/flujo", tags=["flujo"],
                   dependencies=[Depends(requiere_api_key)])

ESQUEMAS = ("core", "channel", "costing", "enrich", "ops", "migration",
            "analytics", "public", "propuestas_retirado")

EXTERNOS = [
    ("mercado_libre", "Mercado Libre", "canal"),
    ("amazon",        "Amazon",        "canal"),
    ("tiktok",        "TikTok",        "canal"),
    ("temu",          "Temu",          "canal"),
    ("woocommerce",   "WooCommerce",   "tienda"),
    ("odoo",          "Odoo",          "erp"),
]

PROCESOS = [
    ("webhook_ml",  "/api/webhooks/ml",  "webhook"),
    ("webhook_woo", "/api/webhooks/woo", "webhook"),
    ("poll_amazon", "pedidos_amazon",    "sondeo 5 min"),
    ("poll_m2e",    "pedidos_m2e",       "sondeo 10 min"),
    ("sync_inv",    "sync inventario",   "cada 15 min"),
    ("etl_core",    "etl-core-products", "cron 06:15"),
    ("crear",       "crear producto",    "panel"),
    ("fanout",      "fan-out de stock",  "por venta"),
    ("publicar",    "publicar en canal", "panel"),
]

# (origen, destino, señal). La señal enciende la arista:
#   ("bitacora", tabla, col_tiempo, col_filtro|None, valor|None) → conteo en ventana
#   ("stat", tabla)                                              → contador de escrituras
#   None                                                         → estructural, no se anima
CABLES = [
    ("mercado_libre", "webhook_ml",  ("bitacora", "ops.webhook_events", "recibido_at", "canal", "mercado_libre")),
    ("woocommerce",   "webhook_woo", ("bitacora", "ops.webhook_events", "recibido_at", "canal", "woocommerce")),
    ("amazon",        "poll_amazon", None),
    ("tiktok",        "poll_m2e",    None),
    ("temu",          "poll_m2e",    None),
    ("odoo",          "etl_core",    None),
    ("woocommerce",   "etl_core",    None),

    ("webhook_ml",  "ops.webhook_events", ("stat", "ops.webhook_events")),
    ("webhook_woo", "ops.webhook_events", ("stat", "ops.webhook_events")),
    ("webhook_ml",  "channel.orders",     ("stat", "channel.orders")),
    ("poll_amazon", "channel.orders",     ("stat", "channel.orders")),
    ("poll_m2e",    "channel.orders",     ("stat", "channel.orders")),
    ("sync_inv",    "channel.listings",   ("stat", "channel.listings")),
    ("etl_core",    "core.products",      ("stat", "core.products")),
    ("etl_core",    "channel.categories", ("stat", "channel.categories")),
    ("crear",       "core.products",      ("stat", "core.products")),
    ("crear",       "costing.costos_finales", ("stat", "costing.costos_finales")),
    ("crear",       "ops.process_log",    ("bitacora", "ops.process_log", "created_at", "proceso", "crear")),
    ("fanout",      "ops.fanout_log",     ("bitacora", "ops.fanout_log", "ts", None, None)),
    ("publicar",    "ops.channel_submissions", ("bitacora", "ops.channel_submissions", "created_at", None, None)),

    ("ops.fanout_log",          "mercado_libre", ("bitacora", "ops.fanout_log", "ts", "canal", "mercado_libre")),
    ("ops.fanout_log",          "amazon",        ("bitacora", "ops.fanout_log", "ts", "canal", "amazon")),
    ("ops.fanout_log",          "tiktok",        ("bitacora", "ops.fanout_log", "ts", "canal", "tiktok")),
    ("ops.channel_submissions", "mercado_libre", ("bitacora", "ops.channel_submissions", "created_at", "canal", "mercado_libre")),
    ("ops.channel_submissions", "amazon",        ("bitacora", "ops.channel_submissions", "created_at", "canal", "amazon")),
]

# Minutos de silencio que son DEMASIADO, por cable. Los que no están aquí son
# esporádicos a propósito (crear, publicar) y nunca alarman.
CADENCIA_MIN = {
    ("mercado_libre", "webhook_ml"): 20,
    ("webhook_ml", "ops.webhook_events"): 20,
    ("sync_inv", "channel.listings"): 45,
    ("fanout", "ops.fanout_log"): 120,
    ("ops.fanout_log", "mercado_libre"): 150,
}

# Qué enseñar al hacer clic. Cada consulta devuelve (cuando, a, b, c) para que
# el frontend pinte sin conocer la tabla. Solo agregable/inocuo: nada de costos.
_D_WEB = ("select recibido_at::text cuando, canal a, topic b, "
          "case when procesado then 'procesado' else 'pendiente' end c "
          "from ops.webhook_events {w} order by recibido_at desc limit 8")
_D_ORD = ("select creado_at::text cuando, cuenta a, "
          "concat('$', total::numeric(12,2)) b, coalesce(estado_canal,'—') c "
          "from channel.orders {w} order by creado_at desc limit 8")
DETALLE: dict[str, str] = {
    "ops.webhook_events":      _D_WEB.format(w=""),
    "ops.fanout_log":          ("select ts::text cuando, canal a, accion b, left(resultado,60) c "
                                "from ops.fanout_log order by ts desc limit 8"),
    "ops.process_log":         ("select created_at::text cuando, proceso||' · '||origen a, "
                                "estado b, left(accion,60) c "
                                "from ops.process_log order by created_at desc limit 8"),
    "ops.channel_submissions": ("select created_at::text cuando, canal a, operacion b, "
                                "coalesce(status,'—') c from ops.channel_submissions "
                                "order by created_at desc limit 8"),
    "channel.orders":          _D_ORD.format(w=""),
    "channel.listings":        ("select updated_at::text cuando, sku a, canal b, "
                                "coalesce(status,'—') c from channel.listings "
                                "order by updated_at desc limit 8"),
    "core.products":           ("select updated_at::text cuando, sku a, left(name,40) b, "
                                "coalesce(status,'—') c from core.products "
                                "order by updated_at desc limit 8"),
    "webhook_ml":   _D_WEB.format(w="where canal='mercado_libre'"),
    "webhook_woo":  _D_WEB.format(w="where canal='woocommerce'"),
    "crear":        ("select created_at::text cuando, origen a, estado b, left(accion,60) c "
                     "from ops.process_log where proceso='crear' "
                     "order by created_at desc limit 8"),
    "fanout":       ("select ts::text cuando, canal a, accion b, left(resultado,60) c "
                     "from ops.fanout_log order by ts desc limit 8"),
    "publicar":     ("select created_at::text cuando, canal a, operacion b, "
                     "coalesce(status,'—') c from ops.channel_submissions "
                     "order by created_at desc limit 8"),
    "poll_amazon":  _D_ORD.format(w="where canal='amazon'"),
    "poll_m2e":     _D_ORD.format(w="where canal in ('tiktok','temu')"),
    "sync_inv":     ("select updated_at::text cuando, sku a, canal b, "
                     "coalesce(situacion,status,'—') c from channel.listings "
                     "order by updated_at desc limit 8"),
    "etl_core":     ("select updated_at::text cuando, sku a, left(name,40) b, "
                     "coalesce(status,'—') c from core.products "
                     "order by updated_at desc limit 8"),
    "mercado_libre": _D_ORD.format(w="where canal='mercado_libre'"),
    "amazon":        _D_ORD.format(w="where canal='amazon'"),
    "tiktok":        _D_ORD.format(w="where canal='tiktok'"),
    "temu":          _D_ORD.format(w="where canal='temu'"),
    "woocommerce":   _D_WEB.format(w="where canal='woocommerce'"),
}

_Q_RELACIONES = """
select n.nspname as esquema, c.relname as nombre,
       greatest(c.reltuples::bigint, 0) as filas
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where c.relkind in ('r','p') and n.nspname = any(%s)
order by 1, 2
"""

_Q_FKS = """
select tn.nspname||'.'||tc.relname as hija, fn.nspname||'.'||fc.relname as padre
from pg_constraint con
join pg_class tc on tc.oid = con.conrelid
join pg_namespace tn on tn.oid = tc.relnamespace
join pg_class fc on fc.oid = con.confrelid
join pg_namespace fn on fn.oid = fc.relnamespace
where con.contype = 'f' and tn.nspname = any(%s) and fn.nspname = any(%s)
"""

_Q_STAT = """
select schemaname||'.'||relname as ref,
       n_tup_ins as ins, n_tup_upd as upd, n_tup_del as del, n_live_tup as vivas
from pg_stat_user_tables where schemaname = any(%s)
"""

# ── Estado en memoria del proceso ────────────────────────────────────────────
# Vive aquí y muere con el proceso, a propósito: el historial es para "los
# últimos 15 min", no un archivo. Con varios workers cada uno lleva el suyo;
# la diferencia son segundos de sparkline, no datos de negocio.
_lock = threading.Lock()
_topo_cache: dict | None = None
_previo: dict[str, dict] = {}
_previo_t: float | None = None
_ult_mov: dict[tuple, float] = {}
_arranque = time.monotonic()
_historia: deque[dict] = deque(maxlen=180)
_salud_cache: list[dict] = []
_salud_t: float = 0.0


def _fetch_suave(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Una consulta de salud que falle (columna renombrada, tabla ausente en el
    sandbox) apaga SU fila, nunca el tablero entero."""
    try:
        return sdb.fetch_all(sql, params)
    except Exception:  # noqa: BLE001
        return []


@router.get("/topologia")
def topologia() -> dict[str, Any]:
    global _topo_cache
    if _topo_cache:
        return _topo_cache
    rel = sdb.fetch_all(_Q_RELACIONES, (list(ESQUEMAS),))
    fks = sdb.fetch_all(_Q_FKS, (list(ESQUEMAS), list(ESQUEMAS)))
    nodos = (
        [{"id": e, "etiqueta": n, "clase": "externo", "grupo": g, "filas": None}
         for e, n, g in EXTERNOS]
        + [{"id": p, "etiqueta": n, "clase": "proceso", "grupo": g, "filas": None}
           for p, n, g in PROCESOS]
        + [{"id": f"{r['esquema']}.{r['nombre']}", "etiqueta": r["nombre"],
            "clase": "tabla", "grupo": r["esquema"], "filas": int(r["filas"])}
           for r in rel]
    )
    conocidos = {n["id"] for n in nodos}
    aristas = (
        [{"de": f["padre"], "a": f["hija"], "clase": "fk"} for f in fks
         if f["padre"] in conocidos and f["hija"] in conocidos and f["padre"] != f["hija"]]
        + [{"de": o, "a": d, "clase": "flujo"} for o, d, _ in CABLES
           if o in conocidos and d in conocidos]
    )
    _topo_cache = {"nodos": nodos, "aristas": aristas,
                   "generado": datetime.now(timezone.utc).isoformat()}
    return _topo_cache


def _salud() -> list[dict[str, Any]]:
    filas: list[dict] = []

    def poner(k: str, v: str, estado: str) -> None:
        filas.append({"k": k, "v": v, "estado": estado})

    def edad(minutos) -> str:
        if minutos is None:
            return "nunca"
        m = int(minutos)
        if m < 60:
            return f"hace {m} min"
        if m < 60 * 48:
            return f"hace {m/60:.1f} h"
        return f"hace {m/1440:.0f} d"

    for r in _fetch_suave("""select cuenta,
            extract(epoch from (expires_at - now()))/60 exp_min,
            extract(epoch from (now() - updated_at))/60 upd_min
            from ops.ml_tokens order by cuenta"""):
        e, u = r["exp_min"], r["upd_min"]
        if e is not None:
            estado = "mal" if e <= 0 else ("aviso" if e < 60 else "ok")
            v = ("vencido " + edad(-e)) if e <= 0 else f"expira en {e/60:.1f} h"
        else:
            # Sin fecha de expiración (los renueva el proceso externo): se
            # juzga por updated_at, como manda la regla 8 del playbook.
            estado = ("mal" if u is None or u > 24 * 60
                      else "aviso" if u > 8 * 60 else "ok")
            v = f"renovado {edad(u)}"
        poner(f"token ML · {r['cuenta']}", v, estado)

    for r in _fetch_suave("""select coalesce(seller_name, shop_id) cuenta,
            extract(epoch from (expira - now()))/60 exp_min from ops.tiktok_tokens"""):
        e = r["exp_min"]
        estado = "mal" if e is None or e <= 0 else ("aviso" if e < 24 * 60 else "ok")
        poner(f"token TikTok · {r['cuenta']}",
              "vencido " + edad(-e) if e is not None and e <= 0
              else f"expira en {e/1440:.1f} d" if e is not None else "sin fecha",
              estado)

    for r in _fetch_suave("""select count(*) filter (where not procesado) pend,
            coalesce(sum(intentos) filter (where not procesado),0) reint
            from ops.webhook_events where recibido_at > now() - interval '24 hours'"""):
        pend = int(r["pend"] or 0)
        poner("webhooks sin procesar (24 h)",
              f"{pend} pendientes · {int(r['reint'] or 0)} reintentos",
              "ok" if pend == 0 else ("aviso" if pend < 50 else "mal"))

    for r in _fetch_suave("""select extract(epoch from (now() - max(created_at)))/3600 h
            from ops.process_log where proceso='retencion_webhooks'"""):
        h = r["h"]
        poner("pg_cron · retención",
              "nunca ha corrido" if h is None else f"corrió {edad(h*60)}",
              "mal" if h is None or h > 50 else ("aviso" if h > 26 else "ok"))

    umbral_venta = {"mercado_libre": (240, 720), "amazon": (1440, 2880)}
    for r in _fetch_suave("""select canal,
            extract(epoch from (now() - max(creado_at)))/60 min,
            count(*) filter (where creado_at > now() - interval '24 hours') n24
            from channel.orders group by canal order by canal"""):
        lim = umbral_venta.get(r["canal"])
        m = r["min"]
        estado = "info"
        if lim and m is not None:
            estado = "ok" if m < lim[0] else ("aviso" if m < lim[1] else "mal")
        poner(f"última venta · {r['canal']}",
              f"{edad(m)} · {int(r['n24'] or 0)} en 24 h", estado)

    return filas


@router.get("/pulso")
def pulso(ventana_min: int = 15) -> dict[str, Any]:
    global _previo, _previo_t, _salud_cache, _salud_t
    with _lock:
        stat = sdb.fetch_all(_Q_STAT, (list(ESQUEMAS),))

        # Bitácoras agrupadas por (tabla, col_tiempo, col_filtro) para no
        # escanear dos veces la misma tabla.
        grupos: dict[tuple, None] = {}
        for _, _, s in CABLES:
            if s and s[0] == "bitacora":
                grupos[(s[1], s[2], s[3])] = None
        lecturas: dict[tuple, dict] = {}
        for tabla, colt, colf in grupos:
            if colf:
                sql = (f"select {colf} as k, count(*) n, max({colt})::text ultimo "
                       f"from {tabla} where {colt} > now() - %s::interval group by 1")
            else:
                sql = (f"select '*' as k, count(*) n, max({colt})::text ultimo "
                       f"from {tabla} where {colt} > now() - %s::interval")
            for fila in _fetch_suave(sql, (f"{ventana_min} minutes",)):
                lecturas[(tabla, colf, fila["k"])] = {"n": int(fila["n"]),
                                                      "ultimo": fila["ultimo"]}

        errores = _fetch_suave("""select proceso, origen, estado, left(accion, 90) accion,
                created_at::text cuando from ops.process_log
                where estado = 'error' order by created_at desc limit 5""")

        ahora = time.monotonic()
        transcurrido = (ahora - _previo_t) if _previo_t else None
        actual = {r["ref"]: r for r in stat}
        tablas: dict[str, dict] = {}
        for ref, r in actual.items():
            p = _previo.get(ref)
            escrituras = None
            if p and transcurrido and transcurrido > 0.5:
                escrituras = max(0, (r["ins"] - p["ins"]) + (r["upd"] - p["upd"])
                                 + (r["del"] - p["del"]))
            tablas[ref] = {"vivas": int(r["vivas"] or 0), "escrituras": escrituras}
        _previo, _previo_t = actual, ahora

        flujos, silencios, fb = [], [], []
        for origen, destino, senal in CABLES:
            info = {"de": origen, "a": destino, "n": None, "ultimo": None,
                    "bit": bool(senal and senal[0] == "bitacora")}
            if senal and senal[0] == "bitacora":
                m = lecturas.get((senal[1], senal[3], senal[4] or "*"))
                if m:
                    info.update(n=m["n"], ultimo=m["ultimo"])
            elif senal and senal[0] == "stat":
                t = tablas.get(senal[1])
                if t and t["escrituras"] is not None:
                    info["n"] = t["escrituras"]
            flujos.append(info)

            llave = (origen, destino)
            cad = CADENCIA_MIN.get(llave)
            if senal and senal[0] == "stat" and info["n"]:
                _ult_mov[llave] = ahora
            if not cad or info["n"]:
                continue
            if senal and senal[0] == "bitacora":
                fb.append((llave, cad, senal))
            else:
                visto = _ult_mov.get(llave)
                mins = (ahora - (visto or _arranque)) / 60
                if mins > cad:
                    silencios.append({"de": origen, "a": destino, "min": round(mins),
                                      "cadencia": cad, "desde_arranque": visto is None})

        # El último evento REAL (sin ventana) solo se paga cuando hay silencio.
        for llave, cad, s in fb:
            cond = f"where {s[3]} = %s" if s[3] else ""
            filas_fb = _fetch_suave(
                f"select extract(epoch from (now()-max({s[2]})))/60 m from {s[1]} {cond}",
                (s[4],) if s[3] else ())
            mins = filas_fb[0]["m"] if filas_fb and filas_fb[0]["m"] is not None else None
            if mins is None or mins > cad:
                silencios.append({"de": llave[0], "a": llave[1],
                                  "min": round(mins) if mins is not None else None,
                                  "cadencia": cad, "desde_arranque": False})

        esc_total = sum(t["escrituras"] or 0 for t in tablas.values())
        epm = round(esc_total / transcurrido * 60) if transcurrido else None
        por_llave = {(f["de"], f["a"]): (f["n"] or 0) for f in flujos}
        _historia.append({
            "t": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "epm": epm,
            "web": por_llave.get(("mercado_libre", "webhook_ml"), 0),
            "fan": por_llave.get(("fanout", "ops.fanout_log"), 0),
        })

        if time.monotonic() - _salud_t > 30 or not _salud_cache:
            _salud_cache = _salud()
            _salud_t = time.monotonic()

        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ventana_min": ventana_min,
            "intervalo_s": round(transcurrido, 1) if transcurrido else None,
            "tablas": tablas,
            "flujos": flujos,
            "errores": errores,
            "silencios": silencios,
            "historia": list(_historia),
            "salud": _salud_cache,
        }


@router.get("/nodo/{nodo_id:path}")
def nodo(nodo_id: str) -> dict[str, Any]:
    sql = DETALLE.get(nodo_id)
    if not sql:
        return {"id": nodo_id, "eventos": None,
                "nota": "este nodo no tiene bitácora consultable"}
    return {"id": nodo_id, "eventos": _fetch_suave(sql)}
