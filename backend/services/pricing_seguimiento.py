"""
pricing_seguimiento.py — «¿Se movieron?»: semana por semana, las publicaciones a las que se les aplicó un
cambio de precio u oferta — ventas y visitas antes y después (v0.678.0, pedido de Brandon 21-oct-2026).

QUÉ CUENTA COMO CAMBIO
    · Los descuentos aplicados por lote: `hechos.json` de PRICING (los 253 del 15-oct, la columna «Hecho»).
    · Lo que se mueve desde el panel: la bitácora del esquema `pricing` (`pricing.accion` aplicada, parcial
      o deshecha: cambiar precio, deshacer, tomar y quitar ofertas; v0.673.0).
    Si una publicación tiene varios, manda el MÁS RECIENTE (el «antes / después» se mide desde ahí) y se
    dice cuántos lleva.

QUÉ SE MIDE (solo lectura)
    · Piezas vendidas por día: kubera `channel.order_items` + `channel.orders`, sin canceladas, día de
      México. Las piezas, no los pesos: en SANCORFASHION parte de las líneas guardan el neto (memoria
      sancorfashion-precio-neto) y el ingreso saldría torcido.
    · Visitas por día: Mercado Libre `/items/{id}/visits/time_window` (60 días), leídas EN SEGUNDO PLANO
      (seis a la vez, ~1 min para 250) y recordadas 3 horas. Mientras se leen, la respuesta dice
      `visitas.estado = "leyendo"` y la pantalla vuelve a preguntar.

    Semanas ISO de lunes a domingo, hora de México; la de hoy va marcada «en curso» con sus días.
    Antes / después por publicación: los 28 días previos al cambio contra los días transcurridos desde él
    (hasta 28), llevados a «por semana» para que se puedan comparar ventanas de distinto largo.

El token de ML solo se LEE (nunca se renueva) y kubera solo con SELECT, cada uno sin bloquear el loop
(regla 11).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

log = logging.getLogger("omnicanal.pricing.seguimiento")

CDMX = timezone(timedelta(hours=-6))
SEMANAS = 8               # semanas que se muestran, la de hoy incluida
VENTANA_DIAS = 28          # antes / después del cambio
DIAS_VISITAS = 60          # lo que se le pide a ML (alcanza para 8 semanas)
TTL_VISITAS_S = 3 * 3600
TTL_VENTAS_S = 300
MAS_VISITAS = 1.10         # «más visitas» = al menos 10 % arriba por semana

_visitas: dict[str, tuple[float, dict[str, int]]] = {}     # item → (cuándo, {día: visitas})
_tarea: asyncio.Task | None = None
_progreso = {"total": 0, "hechas": 0, "fallas": 0, "inicio": None}
_cache: tuple[float, dict] | None = None
_candado = asyncio.Lock()


# ── PURO ─────────────────────────────────────────────────────────────────────

def _fecha(s: Any) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=CDMX)
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=CDMX)


def semanas(hoy: date, n: int = SEMANAS) -> list[dict]:
    """PURA. Las últimas `n` semanas ISO (lunes a domingo), la de `hoy` al final y marcada en curso."""
    lunes = hoy - timedelta(days=hoy.weekday())
    out = []
    for k in range(n - 1, -1, -1):
        ini = lunes - timedelta(days=7 * k)
        fin = ini + timedelta(days=6)
        iso = ini.isocalendar()
        en_curso = k == 0
        out.append({"clave": f"{iso[0]}-W{iso[1]:02d}", "inicio": ini.isoformat(), "fin": fin.isoformat(),
                    "en_curso": en_curso, "dias": (hoy - ini).days + 1 if en_curso else 7})
    return out


def _suma(serie: dict[str, int], desde: date, hasta: date) -> int:
    """PURA. Suma de los días [desde, hasta] de una serie {«YYYY-MM-DD»: n}."""
    d, total = desde, 0
    while d <= hasta:
        total += int(serie.get(d.isoformat(), 0) or 0)
        d += timedelta(days=1)
    return total


def antes_despues(fecha_cambio: date, hoy: date, piezas: dict[str, int], visitas: dict[str, int] | None) -> dict:
    """
    PURA. Los 28 días previos al cambio contra lo transcurrido desde él (el día del cambio cuenta como
    «después»; hasta 28 días), y su ritmo por semana. Visitas None = no se pudieron leer.
    """
    ant_ini, ant_fin = fecha_cambio - timedelta(days=VENTANA_DIAS), fecha_cambio - timedelta(days=1)
    des_fin = min(hoy, fecha_cambio + timedelta(days=VENTANA_DIAS - 1))
    dias_des = max(0, (des_fin - fecha_cambio).days + 1)
    u_a = _suma(piezas, ant_ini, ant_fin)
    u_d = _suma(piezas, fecha_cambio, des_fin) if dias_des else 0
    v_a = _suma(visitas, ant_ini, ant_fin) if visitas is not None else None
    v_d = (_suma(visitas, fecha_cambio, des_fin) if dias_des else 0) if visitas is not None else None
    sem = lambda x, d: round(x * 7 / d, 2) if x is not None and d else None  # noqa: E731
    antes = {"dias": VENTANA_DIAS, "piezas": u_a, "visitas": v_a,
             "piezas_sem": sem(u_a, VENTANA_DIAS), "visitas_sem": sem(v_a, VENTANA_DIAS)}
    despues = {"dias": dias_des, "piezas": u_d, "visitas": v_d,
               "piezas_sem": sem(u_d, dias_des), "visitas_sem": sem(v_d, dias_des)}
    mov = {
        "vendio": u_d > 0,
        "vende_mas": bool(dias_des) and (despues["piezas_sem"] or 0) > (antes["piezas_sem"] or 0),
        "mas_visitas": (None if v_a is None or not dias_des
                        else (despues["visitas_sem"] or 0) > (antes["visitas_sem"] or 0) * MAS_VISITAS
                        and (despues["visitas_sem"] or 0) > 0),
    }
    mov["sin_movimiento"] = not mov["vendio"] and mov["mas_visitas"] is False
    return {"antes": antes, "despues": despues, "mov": mov}


def por_semana(serie: dict[str, int] | None, sems: list[dict]) -> list[int | None]:
    """PURA. La serie diaria sumada en cada semana; None si no hay serie."""
    if serie is None:
        return [None] * len(sems)
    return [_suma(serie, date.fromisoformat(s["inicio"]), date.fromisoformat(s["inicio"]) + timedelta(days=s["dias"] - 1))
            for s in sems]


def unir_cambios(lote: list[dict], panel: list[dict]) -> dict[str, dict]:
    """
    PURA. Un cambio por publicación: el más reciente manda; se cuentan todos. `lote` viene de hechos.json
    y `panel` de la bitácora de pricing, ya en la misma forma: {id, cuenta, sku, fecha, tipo, antes, nuevo,
    quien, grupo}.
    """
    por_id: dict[str, dict] = {}
    for c in sorted([*lote, *panel], key=lambda c: c["fecha"]):
        prev = por_id.get(c["id"])
        n = (prev or {}).get("n", 0) + 1
        por_id[c["id"]] = {**c, "n": n, "sku": c.get("sku") or (prev or {}).get("sku")}
    return por_id


# ── FUENTES (bloqueantes: en un hilo) ────────────────────────────────────────

def _lote() -> list[dict]:
    """BLOQUEANTE (disco). Los descuentos por lote de la columna «Hecho»."""
    from services import pricing_monitor as pm

    out = []
    for h in pm._leer_hechos():  # noqa: SLF001
        f = _fecha(h.get("desde"))
        if not f:
            continue
        out.append({"id": h["id"], "cuenta": h.get("cuenta"), "sku": h.get("sku"), "fecha": f,
                    "tipo": "descuento", "antes": h.get("antes"), "nuevo": h.get("nuevo"), "quien": None,
                    "grupo": f"lote:{f.astimezone(CDMX).date().isoformat()}", "regla": h.get("regla")})
    return out


def _panel() -> list[dict]:
    """BLOQUEANTE (kubera, SELECT). Lo movido desde el panel (bitácora del esquema pricing)."""
    from services import supabase_db as sdb

    if not sdb.disponible():
        return []
    filas = sdb.fetch_all(
        """
        select pu.listing_id as id, a.legacy_code as cuenta, ac.tipo, ac.estado, ac.actor, ac.terminada_at,
               ap.precio_cobrado_antes, ap.precio_cobrado_despues, ap.precio_cobrado_pedido
          from pricing.accion ac
          join pricing.accion_publicacion ap on ap.accion_id = ac.id
          join pricing.publicacion pu on pu.id = ap.publicacion_id
          join core.accounts a on a.id = pu.account_id
         where ac.estado in ('aplicada', 'parcial', 'deshecha') and ac.terminada_at is not null
           and ac.terminada_at >= now() - interval '120 days'
         order by ac.terminada_at
        """)
    tipos = {"editar_precio": "precio", "deshacer": "deshacer", "entrar_campana": "oferta", "salir_campana": "oferta"}
    out = []
    for f in filas:
        nuevo = f["precio_cobrado_despues"] if f["precio_cobrado_despues"] is not None else f["precio_cobrado_pedido"]
        out.append({"id": f["id"], "cuenta": f["cuenta"], "sku": None, "fecha": _fecha(f["terminada_at"]),
                    "tipo": tipos.get(f["tipo"], f["tipo"]), "subtipo": f["tipo"], "estado": f["estado"],
                    "antes": float(f["precio_cobrado_antes"]) if f["precio_cobrado_antes"] is not None else None,
                    "nuevo": float(nuevo) if nuevo is not None else None, "quien": f["actor"], "grupo": "panel"})
    return out


def _ventas_y_skus(ids: list[str], desde: date) -> tuple[dict[str, dict[str, int]], dict[str, str]]:
    """BLOQUEANTE (kubera, SELECT). Piezas por publicación y día (sin canceladas) y el SKU de cada una."""
    from services import supabase_db as sdb

    if not ids or not sdb.disponible():
        return {}, {}
    ventas: dict[str, dict[str, int]] = {}
    for f in sdb.fetch_all(
        """
        select oi.item_id, to_char(o.creado_at at time zone 'America/Mexico_City', 'YYYY-MM-DD') as dia,
               sum(oi.cantidad)::int as piezas
          from channel.order_items oi
          join channel.orders o on o.canal = oi.canal and o.cuenta = oi.cuenta and o.external_order_id = oi.external_order_id
         where oi.canal = 'mercado_libre' and oi.item_id = any(%(ids)s)
           and o.creado_at >= %(desde)s and lower(coalesce(o.estado_canal, '')) <> 'cancelled'
         group by 1, 2
        """, {"ids": ids, "desde": datetime.combine(desde, datetime.min.time(), CDMX)}):
        ventas.setdefault(f["item_id"], {})[f["dia"]] = int(f["piezas"] or 0)
    skus = {f["listing_id"]: f["sku"] for f in sdb.fetch_all(
        """
        select distinct on (l.listing_id) l.listing_id, l.sku::text as sku
          from channel.listings l
         where l.canal = 'mercado_libre' and l.listing_id = any(%(ids)s)
         order by l.listing_id, l.updated_at desc nulls last
        """, {"ids": ids})}
    return ventas, skus


# ── VISITAS DE ML, EN SEGUNDO PLANO ──────────────────────────────────────────

async def _leer_visitas(pares: list[tuple[str, str]]) -> None:
    """Solo GET a ML. Llena `_visitas`; las que fallan se quedan sin serie (la pantalla lo dice)."""
    from services import meli

    _progreso.update(total=len(pares), hechas=0, fallas=0, inicio=datetime.now(CDMX).isoformat(timespec="seconds"))
    tokens = {c: await meli.access_token_async(c) for c in {c for _, c in pares}}
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient(base_url="https://api.mercadolibre.com", timeout=20) as cli:

        async def una(item: str, cuenta: str) -> None:
            tk = tokens.get(cuenta)
            if not tk:
                _progreso["fallas"] += 1
                return
            async with sem:
                for intento in range(3):
                    try:
                        r = await cli.get(f"/items/{item}/visits/time_window", params={"last": DIAS_VISITAS, "unit": "day"},
                                          headers={"Authorization": f"Bearer {tk}"})
                    except httpx.HTTPError:
                        await asyncio.sleep(1 + intento)
                        continue
                    if r.status_code == 429:
                        await asyncio.sleep(2 + 3 * intento)
                        continue
                    if r.status_code == 200:
                        serie = {(x.get("date") or "")[:10]: int(x.get("total") or 0)
                                 for x in r.json().get("results") or [] if x.get("date")}
                        _visitas[item] = (time.monotonic(), serie)
                        _progreso["hechas"] += 1
                        return
                    break
            _progreso["fallas"] += 1

        await asyncio.gather(*(una(i, c) for i, c in pares))
    log.info("seguimiento: visitas de ML leídas %s de %s (fallas %s)", _progreso["hechas"], len(pares), _progreso["fallas"])


def _pedir_visitas(pares: list[tuple[str, str]]) -> str:
    """Arranca la lectura en segundo plano de las que falten o estén viejas. Devuelve el estado."""
    global _tarea
    ahora = time.monotonic()
    faltan = [(i, c) for i, c in pares if i not in _visitas or ahora - _visitas[i][0] > TTL_VISITAS_S]
    if _tarea is not None and not _tarea.done():
        return "leyendo"
    if not faltan:
        return "listo"
    _tarea = asyncio.create_task(_leer_visitas(faltan))
    return "leyendo"


# ── API DEL SERVICIO ─────────────────────────────────────────────────────────

async def seguimiento(fresco: bool = False) -> dict:
    """La comparativa semanal de lo que se cambió. Solo lee."""
    global _cache
    async with _candado:
        if _cache and not fresco and time.monotonic() - _cache[0] < TTL_VENTAS_S and _cache[1]["visitas"]["estado"] == "listo":
            return _cache[1]
        hoy = datetime.now(CDMX).date()
        sems = semanas(hoy)
        lote = await asyncio.to_thread(_lote)
        try:
            panel = await asyncio.to_thread(_panel)
            kubera = True
        except Exception as exc:  # noqa: BLE001 — sin bitácora se ven los del lote
            log.warning("seguimiento: no se pudo leer la bitácora de pricing: %s", exc)
            panel, kubera = [], False
        cambios = unir_cambios(lote, [c for c in panel if c["fecha"]])
        ids = sorted(cambios)
        desde = min([date.fromisoformat(sems[0]["inicio"])]
                    + [c["fecha"].astimezone(CDMX).date() - timedelta(days=VENTANA_DIAS) for c in cambios.values()])
        try:
            ventas, skus = await asyncio.to_thread(_ventas_y_skus, ids, desde)
        except Exception as exc:  # noqa: BLE001
            log.warning("seguimiento: kubera no contestó las ventas: %s", exc)
            ventas, skus, kubera = {}, {}, False
        estado = _pedir_visitas([(i, cambios[i]["cuenta"]) for i in ids if cambios[i].get("cuenta")])
        pubs = []
        for i in ids:
            c = cambios[i]
            vis = _visitas.get(i, (0, None))[1]
            piezas = ventas.get(i, {})
            fc = c["fecha"].astimezone(CDMX).date()
            ad = antes_despues(fc, hoy, piezas, vis)
            pubs.append({
                "id": i, "cuenta": c.get("cuenta"), "sku": c.get("sku") or skus.get(i), "grupo": c["grupo"],
                "cambio": {"fecha": c["fecha"].astimezone(CDMX).isoformat(timespec="minutes"), "tipo": c["tipo"],
                           "antes": c.get("antes"), "nuevo": c.get("nuevo"), "quien": c.get("quien"), "n": c["n"],
                           "regla": c.get("regla")},
                "semanas": {"piezas": por_semana(piezas, sems), "visitas": por_semana(vis, sems)},
                **ad,
            })
        grupos: dict[str, dict] = {}
        for p in pubs:
            g = grupos.setdefault(p["grupo"], {"clave": p["grupo"], "n": 0})
            g["n"] += 1
        for g in grupos.values():
            g["nombre"] = ("Desde el panel (cambiar precio y ofertas)" if g["clave"] == "panel"
                           else f"Descuentos del {g['clave'][5:]}")
        datos = {
            "hoy": hoy.isoformat(), "leido": datetime.now(CDMX).isoformat(timespec="seconds"), "kubera": kubera,
            "semanas": sems, "ventana_dias": VENTANA_DIAS, "grupos": sorted(grupos.values(), key=lambda g: g["clave"]),
            "visitas": {"estado": estado, "con_serie": sum(1 for p in pubs if p["semanas"]["visitas"][0] is not None),
                        "total": len(pubs), **{k: _progreso[k] for k in ("hechas", "fallas")}},
            "pubs": pubs,
        }
        _cache = (time.monotonic(), datos)
        return datos


def olvidar() -> None:
    global _cache, _tarea
    _cache, _tarea = None, None
    _visitas.clear()
