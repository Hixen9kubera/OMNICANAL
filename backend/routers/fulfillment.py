"""
fulfillment.py — Panel de reabastecimiento (CLON del tablero kubera-fulfillment
de José), leyendo DIRECTO de la BD kubera v4 — primer lector de producción.

Fuentes (todas vistas/tablas de la migración):
  channel.listings              → foto viva por listing (webhook, segundos)
  channel.sales_daily_completa  → ventas sin hueco (hist dailytrack + vivo)
  channel.restock_panel         → sugerido/semáforo (Bollinger, migración 0007)
  costing.costos_finales        → costo y precio sugerido (canal ML)
  costing.costos_validados      → dimensiones → categoría de TAMAÑO

Equivalencias vs el original (documentadas para el clon):
  STOCK ODOO   → STOCK PROPIO = DROP real (bodega Woo por SKU, listing
                 canal='general'; fuente: stock_watch_foto de Brandon v0.27.0.
                 Fallback: stock_own declarado por el marketplace).
  DÍAS ODOO    → EDAD S/VENTA (días desde la última venta registrada).
  DÍAS VENTA   → COBERTURA (stock total / venta diaria del período).
  VISITAS/CR%  → SIN DATO (daily_visits quedó fuera del alcance 2026-07-28).
  TAM          → derivada de costos_validados (lado mayor): S<30, M<60,
                 L<120, XL≥120 cm; S/C sin dimensiones.

  GET /api/fulfillment/dashboard → KPIs + conteo por cuenta + serie diaria
  GET /api/fulfillment/tabla     → filas por SKU (filtros del clon) + sparkline
  GET /api/fulfillment/detalle   → serie diaria de UN SKU (modal del sparkline)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from config import settings
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.fulfillment")
router = APIRouter(prefix="/api/fulfillment", tags=["fulfillment"])

_CUENTAS = {"BEKURA", "SANCORFASHION", "AMAZON"}
_ESTADOS = {"activa", "pausada", "no_venta"}
_TIPOS = {"full", "no_full", "mixto"}
_TAMS = {"S", "M", "L", "XL", "S/C"}
# whitelist de orden → (columna, dirección NATURAL). La entrada del usuario
# JAMÁS se interpola: solo se usa para elegir de este diccionario.
#
# La dirección natural es la que responde la pregunta útil de esa columna: en
# COBERTURA lo urgente es lo que MENOS dura, así que su default es asc. Antes
# la dirección estaba pegada a la columna y no se podía invertir — la flecha de
# la cabecera dibujaba ↓ siempre, incluso ordenando ascendente (Eduardo lo
# detectó el 30-jul). Ahora `dir` la sobreescribe y la UI dibuja la real.
_ORDEN = {
    "venta": ("venta", "desc"),
    "uds": ("uds", "desc"),
    "stock_full": ("stock_full", "desc"),
    "stock_propio": ("stock_propio", "desc"),
    "cobertura": ("cobertura_d", "asc"),
    "edad": ("edad_sin_venta_d", "desc"),
    "margen": ("margen_pct", "desc"),
    "crec": ("crec_7d_pct", "desc"),
    "sugerido": ("sugerido_full", "desc"),
    "sku": ("sku", "asc"),
}
_DIRS = {"asc", "desc"}

# CTEs compartidos del clon: listings agregados POR SKU + ventas del período.
# %(dias)s = período; %(cuenta)s = filtro de cuenta (None = todas).
_BASE = """
with l as (
  select l.sku,
         array_agg(distinct a.legacy_code order by a.legacy_code) as cuentas,
         sum(l.stock_full)                          as stock_full,
         max(l.stock_own)                           as stock_propio,
         bool_or(l.is_fulfillment)                  as tiene_full,
         bool_and(l.is_fulfillment)                 as todo_full,
         -- PRECIO DE VENTA = el de la publicación ACTIVA (contrato de José:
         -- "el precio de venta real sale de la publicación ACTIVA"). max() sobre
         -- todas MIENTE en 516 de 1,908 SKUs (27%%), $861 de diferencia promedio:
         -- ACC-0001-AZL mostraba $382 de una SANCOR pausada cuando BEKURA vende
         -- a $294. Si no hay ninguna activa no hay precio de venta: NULL, y la
         -- UI muestra el de la pausada en gris con su marca.
         min(l.price) filter (where l.situacion = 'active')  as precio,
         min(l.price)                               as precio_cualquiera,
         -- desglose por canal para la celda y el modal
         jsonb_agg(distinct jsonb_build_object(
             'cuenta', a.legacy_code, 'canal', l.canal,
             'situacion', l.situacion, 'price', l.price))
           filter (where l.price is not null)       as precios,
         max(l.updated_at)                          as precio_visto_at,
         bool_or(l.situacion = 'active')            as alguna_activa,
         bool_or(l.situacion = 'paused')            as alguna_pausada,
         max(pr.name)                               as titulo
  from channel.listings l
  join core.accounts a on a.id = l.account_id
  left join core.products pr on pr.sku = l.sku
  where l.canal in ('mercado_libre', 'amazon')
    -- Publicaciones CERRADAS fuera (2026-07-29): un listado que ya no existe
    -- no se reabastece. Ver el comentario de la migración 0007.
    and lower(coalesce(l.situacion, '')) <> 'closed'
    and (%(cuenta)s::text is null or a.legacy_code = %(cuenta)s)
  group by l.sku
),
v as (
  select sku,
         sum(units_sold)                                        as uds,
         sum(revenue)                                           as venta,
         sum(units_sold) filter (where date > current_date - 7) as u7,
         sum(units_sold) filter (where date > current_date - 14
                                   and date <= current_date - 7) as u7_prev
  from channel.sales_daily_completa
  where date > current_date - %(dias)s::int and sku is not null
    and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
  group by sku
),
ult as (
  select sku, max(date) as ultima_venta
  from channel.sales_daily_completa
  where sku is not null
    and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
  group by sku
),
dr as (
  -- DROP real: bodega Woo por SKU (canal='general', una bolsa compartida —
  -- fuente stock_watch_foto). El stock_own de ML/Amazon es lo DECLARADO.
  select sku, max(stock_own) as stock_drop
  from channel.listings
  where canal = 'general'
  group by sku
),
tam as (
  select sku,
         case
           when greatest(coalesce(largo,0), coalesce(alto,0), coalesce(ancho,0)) = 0
                then 'S/C'
           when greatest(largo, alto, ancho) < 30  then 'S'
           when greatest(largo, alto, ancho) < 60  then 'M'
           when greatest(largo, alto, ancho) < 120 then 'L'
           else 'XL'
         end as tam
  from costing.costos_validados
),
sug as (
  select sku, sum(sugerido_full)::int as sugerido_full
  from channel.restock_panel
  where (%(cuenta)s::text is null or cuenta = %(cuenta)s)
  group by sku
),
filas as (
  select l.sku, l.cuentas, l.titulo, coalesce(t.tam, 'S/C') as tam,
         case when l.alguna_activa then 'activa'
              when l.alguna_pausada then 'pausada'
              else 'otra' end as situacion_chip,
         case when coalesce(v.uds, 0) = 0 then 'no_venta'
              when l.alguna_activa then 'activa'
              else 'pausada' end as estado,
         case when l.todo_full then 'full'
              when l.tiene_full then 'mixto'
              else 'no_full' end as tipo,
         coalesce(v.uds, 0)::int        as uds,
         coalesce(v.venta, 0)           as venta,
         coalesce(l.stock_full, 0)::int as stock_full,
         coalesce(d.stock_drop, l.stock_propio, 0)::int as stock_propio,
         (current_date - u.ultima_venta)::int as edad_sin_venta_d,
         case when coalesce(v.uds, 0) > 0
              then round((coalesce(l.stock_full,0)
                          + coalesce(d.stock_drop, l.stock_propio, 0))
                         / (v.uds::numeric / %(dias)s::int), 1) end as cobertura_d,
         l.precio,
         l.precio_cualquiera,
         l.precios,
         l.precio_visto_at,
         cf.precio_sugerido,
         -- COSTO: contrato único de José (prompt de Reportes, 29-jul) —
         -- costos_validados.costo_total es la fuente de verdad; NO
         -- costos_finales.costo_unitario, que nuestro propio esquema declara
         -- "derivado". Coinciden en 3,782 de 3,877 SKUs pero costos_validados
         -- tiene 15,411 filas vs 4,353: al cambiar, la cobertura de
         -- costo/margen se duplica (caso TEC-2165-NEG-2PZ, 187 uds/30d, pasa
         -- de "—" a margen 61.8%%). OJO: los porcentajes en comentarios DENTRO
         -- del SQL van escapados (%%%%) — psycopg2 los lee como marcadores.
         coalesce(cv.costo_total, cf.costo_unitario) as costo,
         case when l.precio > 0 and coalesce(cv.costo_total, cf.costo_unitario) is not null
              then round((l.precio - coalesce(cv.costo_total, cf.costo_unitario))
                         / l.precio * 100, 1)
              end as margen_pct,
         case when coalesce(v.u7_prev, 0) > 0
              then round((coalesce(v.u7,0) - v.u7_prev) / v.u7_prev::numeric * 100, 0)
              when coalesce(v.u7, 0) > 0 then 100
              end as crec_7d_pct,
         coalesce(s.sugerido_full, 0) as sugerido_full
  from l
  left join v   on v.sku = l.sku
  left join ult u on u.sku = l.sku
  left join dr d  on d.sku = l.sku
  left join tam t on t.sku = l.sku
  left join sug s on s.sku = l.sku
  left join costing.costos_finales cf
         on cf.sku = l.sku and cf.canal = 'mercado_libre'
  left join costing.costos_validados cv on cv.sku = l.sku
)
"""


def _params(dias: int, cuenta: str | None) -> dict[str, Any]:
    return {"dias": dias, "cuenta": cuenta}


# ── ESTRELLAS ────────────────────────────────────────────────────────────────
# Pareto ALL-TIME (no del período): "qué SKUs sostienen el negocio". El insumo
# es channel.sales_daily_completa, que empalma la historia rescatada de
# dailytrack (27-dic-2025 → 15-jul) con el flujo vivo — sin ella este análisis
# solo vería desde el 17-jul (7% del período).
#
# El % ACUMULADO depende de por cuál métrica se ordene, así que se calculan LOS
# DOS pares (uds y $) en la misma pasada: el toggle de la UI solo cambia de
# columna, no vuelve a pedir. PROM/MES divide entre MESES ACTIVOS (los que
# tuvieron al menos una venta), no entre los meses del calendario: un SKU que
# nació en junio no se castiga con los ceros de enero.
#
# Las ventas SIN SKU quedan FUERA del ranking (no son un producto: no se pueden
# ordenar ni reabastecer), pero se devuelven aparte en `sin_sku` para que los
# totales cuadren contra la vista y no parezca que se perdieron.
_SQL_ESTRELLAS = """
with v as (
  select sku,
         sum(units_sold)::bigint                       as uds,
         sum(revenue)::numeric                         as venta,
         array_agg(distinct cuenta order by cuenta)    as cuentas,
         count(distinct date_trunc('month', date))     as meses,
         min(date)                                     as primera,
         max(date)                                     as ultima
  from channel.sales_daily_completa
  where sku is not null
    and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
  group by 1
  having sum(units_sold) > 0
),
t as (select sum(uds) as uds_t, sum(venta) as venta_t from v)
select v.sku::text                                     as sku,
       coalesce(p.name, v.sku::text)                   as titulo,
       v.cuentas,
       v.uds::int                                      as uds,
       round(v.venta, 2)                               as venta,
       v.meses::int                                    as meses,
       round(v.uds::numeric / nullif(v.meses, 0), 1)   as prom_mes_uds,
       round(v.venta / nullif(v.meses, 0), 2)          as prom_mes_venta,
       round(100 * v.uds / nullif(t.uds_t, 0), 3)      as share_uds,
       round(100 * v.venta / nullif(t.venta_t, 0), 3)  as share_venta,
       round(100 * sum(v.uds) over (order by v.uds desc, v.sku)
             / nullif(t.uds_t, 0), 3)                  as acum_uds,
       round(100 * sum(v.venta) over (order by v.venta desc, v.sku)
             / nullif(t.venta_t, 0), 3)                as acum_venta,
       v.primera::text                                 as primera,
       v.ultima::text                                  as ultima
from v
cross join t
left join core.products p on p.sku = v.sku
order by v.uds desc, v.sku
"""


@router.get("/estrellas")
async def estrellas(cuenta: str | None = Query(None)) -> dict[str, Any]:
    """Productos estrella: ranking ALL-TIME por unidades e ingresos con su
    curva de Pareto. `cuenta=None` = vista consolidada (fusiona las cuentas
    por SKU, como el tablero original de José)."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    try:
        filas = sdb.fetch_all(_SQL_ESTRELLAS, {"cuenta": cuenta})
        # Los totales se derivan de las filas ya traídas (son ~1,000): una
        # segunda consulta solo para sumarlas no aporta y paga otro viaje.
        uds = sum(f["uds"] for f in filas)
        venta = round(sum(float(f["venta"]) for f in filas), 2)
        # "Cuántos SKUs hacen el 80%": el primero que CRUZA el 80% también
        # cuenta — es el que hace falta para llegar, no el que sobra.
        def cuantos_80(campo: str) -> int:
            n = 0
            for f in sorted(filas, key=lambda x: float(x[campo])):
                n += 1
                if float(f[campo]) >= 80:
                    break
            return n if filas else 0

        periodo = {
            "desde": min((f["primera"] for f in filas), default=None),
            "hasta": max((f["ultima"] for f in filas), default=None),
        }
        sin_sku = sdb.fetch_one(
            """select coalesce(sum(units_sold), 0)::int as uds,
                      round(coalesce(sum(revenue), 0), 2) as venta
               from channel.sales_daily_completa
               where sku is null
                 and (%(cuenta)s::text is null or cuenta = %(cuenta)s)""",
            {"cuenta": cuenta})
        return {
            "ambiente": settings.app_env,
            "cuenta": cuenta,
            "periodo": periodo,
            "totales": {
                "uds": int(uds),
                "venta": venta,
                "skus": len(filas),
                "skus_80_uds": cuantos_80("acum_uds"),
                "skus_80_venta": cuantos_80("acum_venta"),
            },
            "sin_sku": sin_sku,
            "items": filas,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("estrellas fulfillment falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc


@router.get("/meta")
async def meta() -> dict[str, Any]:
    """Metadatos baratos para el layout (ambiente, disponibilidad). Sin tocar
    la BD: lo llama CADA sección de Fulfillment, así que tiene que ser gratis."""
    return {"ambiente": settings.app_env, "bd_disponible": sdb.disponible()}


@router.get("/dashboard")
async def dashboard(
    dias: int = Query(60, ge=7, le=180),
    cuenta: str | None = Query(None),
) -> dict[str, Any]:
    """KPIs del encabezado + conteos por cuenta + serie diaria para la gráfica."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    p = _params(dias, cuenta)
    try:
        kpis = sdb.fetch_one(
            _BASE + """
            select count(*)::int                                   as productos,
                   count(*) filter (where estado = 'activa')::int  as activos,
                   count(*) filter (where estado = 'activa'
                                     and tiene_full_agg)::int      as activos_full,
                   coalesce(sum(stock_full), 0)::bigint            as stock_full,
                   coalesce(sum(stock_propio), 0)::bigint          as stock_propio,
                   coalesce(sum(uds), 0)::bigint                   as uds_periodo,
                   round(coalesce(sum(venta), 0), 2)               as venta_periodo,
                   count(*) filter (where situacion_chip = 'activa')::int as listadas_activas,
                   count(*) filter (where situacion_chip = 'activa'
                                     and stock_full = 0
                                     and stock_propio = 0)::int    as activas_sin_stock
            from (select f.*, (tipo in ('full','mixto')) as tiene_full_agg
                  from filas f) x""", p)
        skus = sdb.fetch_one(
            """select (select count(*)::int from core.products)          as skus_catalogo,
                      (select count(distinct sku)::int from channel.listings
                        where canal in ('mercado_libre','amazon'))       as skus_listados""")
        cuentas = sdb.fetch_all(
            """select a.legacy_code as cuenta, count(*)::int as listings
               from channel.listings l join core.accounts a on a.id = l.account_id
               where l.canal in ('mercado_libre','amazon')
               group by 1 order by 1""")
        serie = sdb.fetch_all(
            """select date, sum(units_sold)::int as unidades,
                      round(sum(revenue), 2) as venta
               from channel.sales_daily_completa
               where date > current_date - %(dias)s::int
                 and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
               group by 1 order by 1""", p)
        pct_activas = (round(kpis["listadas_activas"] / kpis["productos"] * 100)
                       if kpis["productos"] else 0)
        pct_sin_stock = (round(kpis["activas_sin_stock"] / kpis["listadas_activas"] * 100)
                         if kpis["listadas_activas"] else 0)
        return {"ambiente": settings.app_env, "dias": dias,
                "skus": {**skus, "pct_activas": pct_activas,
                         "pct_sin_stock": pct_sin_stock},
                "kpis": kpis, "cuentas": cuentas, "serie": serie}
    except Exception as exc:  # noqa: BLE001
        log.warning("dashboard fulfillment falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc


@router.get("/detalle")
async def detalle(
    sku: str = Query(..., max_length=100),
    dias: int = Query(60, ge=7, le=180),
    cuenta: str | None = Query(None),
) -> dict[str, Any]:
    """Detalle de ventas de UN SKU para el modal del sparkline: serie diaria
    SIN huecos (días sin venta = 0), desglose por cuenta y resumen."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    p = {"sku": sku, "dias": dias, "cuenta": cuenta}
    try:
        filas = sdb.fetch_all(
            """select date, sum(units_sold)::int as uds,
                      round(sum(revenue), 2) as venta
               from channel.sales_daily_completa
               where sku = %(sku)s::citext
                 and date > current_date - %(dias)s::int
                 and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
               group by 1 order by 1""", p)
        por_cuenta = sdb.fetch_all(
            """select cuenta, sum(units_sold)::int as uds,
                      round(sum(revenue), 2) as venta
               from channel.sales_daily_completa
               where sku = %(sku)s::citext
                 and date > current_date - %(dias)s::int
                 and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
               group by 1 order by 1""", p)
        ultima_global = sdb.fetch_scalar(
            "select max(date) from channel.sales_daily_completa where sku = %(sku)s::citext",
            {"sku": sku})

        # Serie SIN huecos: el modal pinta un bar por día, incluidos los ceros.
        from datetime import date, timedelta
        mapa = {str(r["date"]): r for r in filas}
        hoy = date.today()
        serie = []
        for n in range(dias - 1, -1, -1):
            d = str(hoy - timedelta(days=n))
            r = mapa.get(d)
            serie.append({"date": d, "uds": int(r["uds"]) if r else 0,
                          "venta": float(r["venta"]) if r else 0.0})

        total_uds = sum(s["uds"] for s in serie)
        total_venta = round(sum(s["venta"] for s in serie), 2)
        mejor = max(serie, key=lambda s: s["venta"], default=None)
        return {
            "sku": sku, "dias": dias, "cuenta": cuenta, "serie": serie,
            "por_cuenta": por_cuenta,
            "resumen": {
                "total_uds": total_uds,
                "total_venta": total_venta,
                "venta_diaria": round(total_uds / dias, 2) if dias else 0,
                "dias_con_venta": sum(1 for s in serie if s["uds"] > 0),
                "mejor_dia": (mejor if mejor and mejor["venta"] > 0 else None),
                "ultima_venta": str(ultima_global) if ultima_global else None,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("detalle fulfillment falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc


@router.get("/tabla")
async def tabla(
    dias: int = Query(60, ge=7, le=180),
    cuenta: str | None = Query(None),
    estado: str | None = Query(None),
    tipo: str | None = Query(None),
    tam: str | None = Query(None),
    q: str | None = Query(None, max_length=80),
    orden: str = Query("venta"),
    dir: str | None = Query(None, description="asc|desc; omitido = natural"),
    limit: int = Query(50, ge=10, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Filas por SKU (agregado de cuentas) + sparkline 14 d por fila."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    if estado and estado not in _ESTADOS:
        raise HTTPException(400, f"estado inválido: {estado}")
    if tipo and tipo not in _TIPOS:
        raise HTTPException(400, f"tipo inválido: {tipo}")
    if tam and tam not in _TAMS:
        raise HTTPException(400, f"tam inválido: {tam}")
    if dir and dir not in _DIRS:
        raise HTTPException(400, f"dir inválida: {dir}")
    p = _params(dias, cuenta)
    cond, extra = ["true"], {}
    if estado:
        cond.append("estado = %(estado)s"); extra["estado"] = estado
    if tipo:
        cond.append("tipo = %(tipo)s"); extra["tipo"] = tipo
    if tam:
        cond.append("tam = %(tam)s"); extra["tam"] = tam
    if q:
        cond.append("(sku::text ilike %(q)s or titulo ilike %(q)s)")
        extra["q"] = f"%{q}%"
    where = " and ".join(cond)
    col, dir_natural = _ORDEN.get(orden, _ORDEN["venta"])
    # `nulls last` en AMBAS direcciones: un SKU sin margen no es "el de menor
    # margen", es uno del que no sabemos — va al final se ordene como se ordene.
    orden_sql = f"{col} {dir or dir_natural} nulls last"
    try:
        total = sdb.fetch_scalar(
            _BASE + f"select count(*) from filas where {where}", {**p, **extra})
        items = sdb.fetch_all(
            _BASE + f"""select * from filas where {where}
                        order by {orden_sql}, sku limit %(limit)s offset %(offset)s""",
            {**p, **extra, "limit": limit, "offset": offset})
        # Sparkline: unidades por día (14 d) SOLO de los SKUs de esta página.
        if items:
            spark = sdb.fetch_all(
                """select sku, date, sum(units_sold)::int as u
                   from channel.sales_daily_completa
                   where date > current_date - 14 and sku = any(%(skus)s::citext[])
                     and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
                   group by 1, 2""",
                {"skus": [str(i["sku"]) for i in items], "cuenta": cuenta})
            from collections import defaultdict
            from datetime import date, timedelta
            por_sku: dict[str, dict] = defaultdict(dict)
            for r in spark:
                por_sku[str(r["sku"]).lower()][str(r["date"])] = r["u"]
            hoy = date.today()
            fechas = [str(hoy - timedelta(days=n)) for n in range(13, -1, -1)]
            for i in items:
                m = por_sku.get(str(i["sku"]).lower(), {})
                i["spark"] = [m.get(f, 0) for f in fechas]
        return {"total": int(total or 0), "items": items,
                "limit": limit, "offset": offset, "dias": dias,
                "orden": orden, "dir": dir or dir_natural}
    except Exception as exc:  # noqa: BLE001
        log.warning("tabla fulfillment falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc


# ── VENTAS POR CATEGORÍA ─────────────────────────────────────────────────────
# Réplica del reporte ventas_por_categoria de José (xlsx del 19-jul) contra la
# BD kubera, en vivo y con el ÁRBOL COMPLETO: el xlsx se detiene en 4 niveles;
# channel.categories trae la ruta entera (hasta 7). El endpoint devuelve las
# HOJAS con su ruta y la UI arma el árbol con acumulados por nivel — así un
# solo query sirve para cualquier profundidad.
#
# La taxonomía es de ML pero se aplica POR SKU (channel.product_category,
# 13,689 mapeos; 99.9%% de lo vendido clasifica), así que las ventas de Amazon
# también entran. Publicaciones/activas se cuentan sobre el catálogo listado
# completo de cada hoja, no solo lo vendido — como el xlsx.
_SQL_CAT_HOJAS = """
with pc as (
  select pc.sku, pc.category_id,
         coalesce(nullif(trim(c.path), ''), c.name, 'Sin categoría') as ruta
  from channel.product_category pc
  join channel.categories c
    on c.category_id = pc.category_id and c.channel_id = pc.channel_id
),
v as (
  select s.sku, s.cuenta,
         sum(s.units_sold)::int as uds,
         sum(s.revenue)         as venta
  from channel.sales_daily_completa s
  where s.date > current_date - %(dias)s::int and s.sku is not null
    and (%(cuenta)s::text is null or s.cuenta = %(cuenta)s)
  group by 1, 2
  having sum(s.units_sold) > 0
),
vc as (
  select coalesce(pc.ruta, 'Sin categoría') as ruta,
         pc.category_id, v.cuenta, v.sku, v.uds, v.venta
  from v left join pc on pc.sku = v.sku
),
ventas_cat as (
  select ruta, category_id,
         sum(uds)::int            as uds,
         round(sum(venta), 2)     as venta,
         count(distinct sku)::int as skus,
         (select jsonb_agg(jsonb_build_object('cuenta', x.cuenta,
                                              'uds', x.uds, 'venta', x.venta)
                           order by x.cuenta)
            from (select cuenta, sum(uds)::int as uds, round(sum(venta),2) as venta
                    from vc i where i.ruta = o.ruta
                      and i.category_id is not distinct from o.category_id
                    group by 1) x) as cuentas
  from vc o
  group by ruta, category_id
),
lst as (
  select pc.category_id, pc.ruta,
         count(*)                                       as publicaciones,
         count(*) filter (where l.situacion = 'active') as activas
  from channel.listings l
  join pc on pc.sku = l.sku
  where l.canal in ('mercado_libre', 'amazon')
    and lower(coalesce(l.situacion, '')) <> 'closed'
    and (%(cuenta)s::text is null
         or exists (select 1 from core.accounts a
                    where a.id = l.account_id and a.legacy_code = %(cuenta)s))
  group by 1, 2
)
-- FULL JOIN a propósito (31-jul, Eduardo): una categoría CON catálogo pero SIN
-- venta en el período también viaja (uds 0) — si no, buscar "Caminadoras" en
-- 60 días respondía "no existe" cuando la verdad era "existe y no vendió".
-- La UI las esconde del árbol por defecto y solo las enseña al buscar.
select coalesce(s.ruta, l.ruta)                 as ruta,
       coalesce(s.category_id, l.category_id)   as category_id,
       coalesce(s.uds, 0)::int                  as uds,
       coalesce(s.venta, 0)                     as venta,
       coalesce(s.skus, 0)::int                 as skus,
       coalesce(l.publicaciones, 0)::int        as publicaciones,
       coalesce(l.activas, 0)::int              as activas,
       s.cuentas
from ventas_cat s
full outer join lst l on l.category_id = s.category_id
order by venta desc
"""

# Publicaciones de UNA hoja del árbol, como las filas del xlsx: por item_id
# (MLM…), con cuenta, título, situación, precio y ventas del período.
# El título sale de order_items (congelado en la venta) con respaldo en el
# maestro; situación/precio, del listing vivo. "Días en venta" del xlsx NO se
# puede replicar: listings no guarda la fecha de creación de la publicación —
# se da la PRIMERA VENTA registrada, que es lo que sí sabemos.
_SQL_CAT_PUBS = """
with skus_hoja as (
  select sku from channel.product_category
  where category_id = %(categoria_id)s and channel_id = 'mercado_libre'
)
select s.item_id,
       s.cuenta,
       max(s.sku::text)                as sku,
       sum(s.units_sold)::int          as uds,
       round(sum(s.revenue), 2)        as venta,
       min(s.date)::text               as primera_venta,
       max(s.date)::text               as ultima_venta,
       max(l.situacion)                as situacion,
       max(l.price)                    as precio,
       coalesce(max(oi.titulo), max(p.name)) as titulo
from channel.sales_daily_completa s
left join channel.listings l
       on l.listing_id = s.item_id and l.canal = s.canal
left join core.products p on p.sku = s.sku
left join lateral (
  select titulo from channel.order_items oi
  where oi.item_id = s.item_id and oi.titulo is not null limit 1
) oi on true
where s.sku in (select sku from skus_hoja)
  and s.date > current_date - %(dias)s::int
  and (%(cuenta)s::text is null or s.cuenta = %(cuenta)s)
group by s.item_id, s.cuenta
order by venta desc
limit 200
"""


@router.get("/categorias")
async def categorias(
    dias: int = Query(60, ge=7, le=400),
    cuenta: str | None = Query(None),
) -> dict[str, Any]:
    """Ventas por categoría con la ruta COMPLETA de ML: devuelve las hojas
    (ruta + category_id) y la UI arma el árbol con acumulados por nivel.
    `dias=400` cubre todo el histórico."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    try:
        filas = sdb.fetch_all(_SQL_CAT_HOJAS, {"dias": dias, "cuenta": cuenta})
        venta_total = sum(float(f["venta"]) for f in filas)
        uds_total = sum(f["uds"] for f in filas)
        # solo las que SÍ vendieron cuentan como "categorías con venta"
        principales = {str(f["ruta"]).split("›")[0].strip()
                       for f in filas if f["uds"]}
        return {
            "ambiente": settings.app_env, "dias": dias, "cuenta": cuenta,
            "totales": {"venta": round(venta_total, 2), "uds": int(uds_total),
                        "categorias": len(principales)},
            "hojas": filas,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("categorias fulfillment falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc


@router.get("/categorias/publicaciones")
async def categorias_publicaciones(
    categoria_id: str = Query(..., max_length=40),
    dias: int = Query(60, ge=7, le=400),
    cuenta: str | None = Query(None),
) -> dict[str, Any]:
    """Las publicaciones (item_id) de una hoja del árbol, como las filas del
    xlsx: cuenta, título, situación, precio y ventas del período."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    try:
        filas = sdb.fetch_all(
            _SQL_CAT_PUBS,
            {"categoria_id": categoria_id, "dias": dias, "cuenta": cuenta})
        return {"categoria_id": categoria_id, "dias": dias,
                "items": filas, "tope": 200}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("categorias/publicaciones falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc
