"""
metricas.py — API del tab Métricas de Análisis: KPIs de publicaciones de
Mercado Libre por cuenta (BEKURA / SANCORFASHION / consolidado).

  GET /api/analisis/metricas → activaciones (por fecha REAL de publicación,
                                date_published, migración 0031), ticket
                                promedio y visitas bajas (snapshot de
                                listings activos), con comparativo vs la
                                semana anterior donde aplica.

Semana por default = semana ISO 8601 actual (lunes-domingo, hora CDMX) si no
se pasan `desde`/`hasta` — mismo espíritu que ventas.py (rango + comparativo
fijo de 7 días), pero aquí el rango default es la semana calendario, no
"hoy".

`date_published` solo existe para canal='mercado_libre' (ver comment on
column de la migración) — este endpoint no toca Amazon/Woo.

`visitas_bajas` es un SNAPSHOT de lo que YA está capturado en
`enrich.market_listing_metrics.visits_30d` (Competencia: cron mensual +
refrescos manuales, `POST /api/competencia/visitas-propias`) — no dispara
mediciones nuevas ni depende de `desde`/`hasta`. Jose, 24-ago: sin tabla
nueva ni llamadas en vivo a ML, con los datos que ya existen en Supabase.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from services import supabase_db as sdb
from services import ventas_ml

router = APIRouter(prefix="/api/analisis", tags=["metricas"])

_CUENTAS = {"BEKURA", "SANCORFASHION"}


async def _fetch_all(sql: str, par: Any = None) -> list[dict[str, Any]]:
    # psycopg2 es bloqueante — nunca directo en la corrutina (regla 11 de
    # CLAUDE.md); mismo patrón que fulfillment.py._fetch_all.
    return await asyncio.to_thread(sdb.fetch_all, sql, par)


_SQL_ACTIVACIONES = """
select l.sku, a.legacy_code as cuenta, l.listing_id,
       (l.date_published at time zone 'America/Mexico_City') as date_published,
       l.situacion, pr.name as titulo
  from channel.listings l
  join core.accounts a on a.id = l.account_id
  left join core.products pr on pr.sku = l.sku
 where l.canal = 'mercado_libre'
   and l.date_published is not null
   and (l.date_published at time zone 'America/Mexico_City')::date
       between %(desde)s and %(hasta)s
   and (%(cuenta)s::text is null or a.legacy_code = %(cuenta)s)
 order by l.date_published desc
"""

_SQL_TICKET = """
select a.legacy_code as cuenta, l.price
  from channel.listings l
  join core.accounts a on a.id = l.account_id
 where l.canal = 'mercado_libre' and l.situacion = 'active' and l.price is not null
   and (%(cuenta)s::text is null or a.legacy_code = %(cuenta)s)
"""

# KPI "visitas bajas": SNAPSHOT con lo que YA está capturado en
# `enrich.market_listing_metrics.visits_30d` (Competencia, cron mensual +
# refrescos manuales) — Jose, 24-ago: "sin tablas nuevas, con los datos que
# tenemos en supabase". `visits_30d IS NULL` = nunca medido (no está en el
# watchlist de Competencia, o aún no le tocó captura) — eso NO cuenta como
# "0 visitas", se reporta aparte en `sin_medir`. `distinct on` toma el
# periodo MÁS RECIENTE por publicación (hoy solo hay un mes cargado, pero la
# tabla es mensual y algún día tendrá más de uno).
_SQL_VISITAS_BAJAS = """
with ultimo as (
  select distinct on (sku, cuenta) sku, cuenta, visits_30d
    from enrich.market_listing_metrics
   where canal = 'mercado_libre'
   order by sku, cuenta, periodo desc
)
select a.legacy_code as cuenta, u.visits_30d
  from channel.listings l
  join core.accounts a on a.id = l.account_id
  left join ultimo u on u.sku = l.sku and u.cuenta = a.legacy_code
 where l.canal = 'mercado_libre' and l.situacion = 'active'
   and (%(cuenta)s::text is null or a.legacy_code = %(cuenta)s)
"""


def _visitas_bajas(rows: list[dict[str, Any]]) -> dict[str, Any]:
    por_cuenta: dict[str, dict[str, int]] = {}
    total = medidas = bajas = 0
    for r in rows:
        cta = r["cuenta"]
        stat = por_cuenta.setdefault(cta, {"total": 0, "medidas": 0, "bajas": 0})
        total += 1
        stat["total"] += 1
        v = r["visits_30d"]
        if v is None:
            continue
        medidas += 1
        stat["medidas"] += 1
        if 0 <= v <= 100:
            bajas += 1
            stat["bajas"] += 1

    def _armar(stat: dict[str, int]) -> dict[str, Any]:
        return {**stat, "pct": round(stat["bajas"] / stat["total"] * 100, 1) if stat["total"] else None}

    return {
        "total": total, "medidas": medidas, "sin_medir": total - medidas,
        "bajas": bajas,
        "pct": round(bajas / total * 100, 1) if total else None,
        "por_cuenta": {c: _armar(s) for c, s in por_cuenta.items()},
    }


def _semana_iso_actual() -> tuple[date, date]:
    hoy = ventas_ml.hoy_mx()
    _, _, isodow = hoy.isocalendar()  # 1=lunes … 7=domingo
    lunes = hoy - timedelta(days=isodow - 1)
    return lunes, lunes + timedelta(days=6)


def _por_cuenta(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r["cuenta"]] = out.get(r["cuenta"], 0) + 1
    return out


def _delta_pct(actual: int, previo: int) -> float | None:
    if previo == 0:
        return None
    return round((actual - previo) / previo * 100, 1)


@router.get("/metricas")
async def metricas(
    cuenta: str | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> dict[str, Any]:
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    cta = (cuenta or "").strip().upper() or None
    if cta and cta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")

    if desde and hasta:
        d1, d2 = desde, hasta
    else:
        d1, d2 = _semana_iso_actual()

    d1_prev, d2_prev = d1 - timedelta(days=7), d2 - timedelta(days=7)

    (activaciones, activaciones_prev, ticket_rows,
     visitas_rows) = await asyncio.gather(
        _fetch_all(_SQL_ACTIVACIONES, {"desde": d1, "hasta": d2, "cuenta": cta}),
        _fetch_all(_SQL_ACTIVACIONES, {"desde": d1_prev, "hasta": d2_prev, "cuenta": cta}),
        _fetch_all(_SQL_TICKET, {"cuenta": cta}),
        _fetch_all(_SQL_VISITAS_BAJAS, {"cuenta": cta}),
    )
    visitas_bajas = _visitas_bajas(visitas_rows)

    # Ticket promedio: se deriva de las filas ya traídas (mismo criterio que
    # estrellas — pocas filas, una segunda consulta agregada no aporta).
    precios_por_cuenta: dict[str, list[float]] = {}
    for r in ticket_rows:
        precios_por_cuenta.setdefault(r["cuenta"], []).append(float(r["price"]))
    ticket_por_cuenta = {
        c: round(sum(precios) / len(precios), 2) for c, precios in precios_por_cuenta.items()
    }
    todos = [p for precios in precios_por_cuenta.values() for p in precios]
    ticket_consolidado = round(sum(todos) / len(todos), 2) if todos else None

    iso_year, iso_week, _ = d1.isocalendar()

    return {
        "periodo": {
            "desde": d1.isoformat(), "hasta": d2.isoformat(),
            "semana_iso": iso_week, "anio_iso": iso_year,
        },
        "activaciones": {
            "total": len(activaciones),
            "por_cuenta": _por_cuenta(activaciones),
            "delta_pct": _delta_pct(len(activaciones), len(activaciones_prev)),
            "items": activaciones,
        },
        "ticket_promedio": {
            "consolidado": ticket_consolidado,
            "por_cuenta": ticket_por_cuenta,
            # Snapshot de HOY, no reconstrucción histórica del precio en el
            # rango — decisión validada con Jose (ver plan del tab Métricas).
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
        },
        "visitas_bajas": {
            **visitas_bajas,
            # Snapshot de HOY con lo que ya se sabe (no dispara medición
            # nueva) — mismo espíritu que ticket_promedio.
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
        },
    }
