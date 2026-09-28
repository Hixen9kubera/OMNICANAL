"""Extracción de kubera para el laboratorio de precios: SOLO SELECT, todo a archivos.

Cada consulta produce `crudo/<día>/kubera_<nombre>.json` (vía `almacen`) con la
misma envoltura, para que los módulos de cálculo no tengan que adivinar:

    {"generado_at": "<iso UTC>", "fuente": "kubera", "consulta": "<nombre>",
     "meta": {...conteos, avisos, parámetros de la consulta...},
     "<llave>": [filas], ...}

La llave de las filas es `filas` salvo en los archivos que juntan varias
consultas (ventas, competencia, historial, envío), donde cada lista lleva su
propio nombre. Montos en MXN tal como vienen de la base (con IVA en precios de
venta, sin IVA en costos). Fechas ISO. `null` = sin dato, nunca un 0 inventado.

Por qué cada archivo existe y qué trampa esquiva (todo medido el 28-sep-2026,
ver los informes de `scratchpad/mapa/`):

- `kubera_publicaciones`: `channel.listings` guarda UNA fila por (sku, cuenta,
  canal). Un SKU con dos publicaciones en la misma cuenta se queda con la que
  sincronizó al último (10 activas de ML sin fila; 11 ítems ocultos vendieron
  $132,677 en 30 d). Por eso esto es la foto de kubera y el universo verdadero
  de ML sale de la API (`extraer_ml.py`, por listing_id). Las filas fantasma
  (todo en NULL: 266 de ML, 132 de Amazon) se descartan aquí.
- `kubera_contenedores`: m³ de cada contenedor reconstruido desde
  `costos_validados` (`Σ costo_cbm/7500 × cajas × piezas_por_caja`; 63 de 84
  caen en 50–80 m³). Con las dimensiones sale absurdo (hasta 29,808 m³).
- `kubera_ventas_dia`: `sales_daily_completa` empalma el histórico (≤15-jul)
  con el vivo (≥16-jul); la mediana del precio del mismo ítem entre ambos lados
  cuadra en 0.996. Se filtra `cuenta in (BEKURA, SANCORFASHION)` porque hay 44
  renglones de AMAZON bajo canal mercado_libre.
- `kubera_lineas`: el precio realizado vive en `order_items.precio_unitario`
  (la historia de `price_sale` no existe en ninguna tabla). La comisión se toma
  de la LÍNEA: el encabezado vale 0 desde el 14-ago.
- `kubera_competencia`: SERP por término y más vendidos de la hoja. Ninguna
  tiene historia (se borran en cada captura), así que guardarlas a diario aquí
  es lo que empieza la serie.
- `kubera_historial_precio`: `listing_history` es casi todo ruido de pares que
  rebotan A→B→A cada 15 min (dos publicaciones peleando por la misma fila):
  en 150 d, 38 pares hacen 165,046 de los 167,532 cambios de price (98.5%).
  Esos pares se EXCLUYEN y se listan aparte para que nadie los confunda con
  cambios de precio.
- `kubera_envio_real`: `order_shipping_cost` es lo que ML le cobró al vendedor
  por pedido; la tabla `_TARIFA_ML` cuadra con mediana 1.0 en FULL.
- `kubera_visitas_cache`: `market_listing_metrics` del mes en curso (ventana
  móvil de 30 d, se sobrescribe a diario). Es RESPALDO: la serie diaria viene
  de la API.

Nada aquí escribe en kubera: `candados.py` envuelve `fetch_all` y rechaza lo que
no sea SELECT/WITH. No se marca la sesión como read-only (regla 13 de CLAUDE.md:
el pooler 6543 comparte conexiones).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):  # corrido como script: backend/ al path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sandbox_precios import _entorno  # noqa: E402

_entorno.cargar()  # idempotente; SIEMPRE antes de tocar services.* (instala candados)
from sandbox_precios import almacen  # noqa: E402

log = logging.getLogger("laboratorio.extraer_kubera")

DIAS_VENTAS = 150         # = la ventana máxima de visitas de la API de ML
DIAS_ENVIO = 60
DIAS_OTROS_CANALES = 30
# Un par sku×cuenta "rebota" si en alguna semana tuvo ≥20 cambios del mismo campo
# y al menos la mitad regresan al valor de dos cambios atrás (A→B→A). Medido el
# 28-sep: 29 pares hicieron el 99.4% de los cambios de price de 7 días.
REBOTE_MIN_SEMANA = 20
REBOTE_FRACCION = 0.5
_CANCELADOS = "('cancelled','invalid','canceled')"   # mismo filtro que channel.sales_daily


def _sdb():
    from services import supabase_db as sdb
    return sdb


def _parametros() -> dict:
    ruta = Path(__file__).with_name("parametros.json")
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


class _Medidor:
    """Corre SELECTs midiendo filas y duración de cada uno (van al resumen)."""

    def __init__(self) -> None:
        self.consultas: list[dict] = []

    def q(self, nombre: str, sql: str, params: Any = None) -> list[dict]:
        t0 = time.monotonic()
        filas = _sdb().fetch_all(sql, params)
        dur = round(time.monotonic() - t0, 2)
        self.consultas.append({"consulta": nombre, "filas": len(filas), "duracion_s": dur})
        log.info("kubera: %s → %d filas en %.1fs", nombre, len(filas), dur)
        return filas


# ── 1. Publicaciones ──────────────────────────────────────────────────────────
_SQL_PUBLICACIONES = """
select l.canal, a.legacy_code as cuenta, l.sku::text as sku, l.listing_id, l.url,
       l.status, l.situacion, l.logistic_type, l.is_fulfillment,
       l.price, l.price_base, l.price_sale, l.price_sale_at,
       l.stock_own, l.stock_full, l.stock_fba,
       l.category_id, l.product_type, l.currency, l.store_name,
       l.date_published, l.updated_at,
       p.name as producto_nombre, p.wc_parent_id, p.status as producto_status,
       cv.contenedor, cv.costo_producto, cv.costo_cbm, cv.costo_total,
       cv.cajas, cv.piezas_por_caja, cv.peso, cv.largo, cv.ancho, cv.alto,
       cv.revisado_at, cv.revisado_por,
       cf.pct_comision as ml_pct_comision, cf.ml_cat_id,
       cf.costo_unitario as ml_costo_unitario, cf.precio_sugerido as ml_precio_sugerido,
       s.stock_odoo, s.actualizado as stock_odoo_at
  from channel.listings l
  join core.accounts a on a.id = l.account_id
  left join core.products p on p.sku = l.sku
  left join costing.costos_validados cv on cv.sku = l.sku
  left join costing.costos_finales cf on cf.sku = l.sku and cf.canal = 'mercado_libre'
  left join ops.stock_watch_photo s on s.sku = l.sku
 where l.canal <> 'general'
   and not (nullif(l.listing_id, '') is null and l.situacion is null and l.price is null
            and l.stock_own is null and l.logistic_type is null)
"""


def _publicaciones(m: _Medidor) -> dict:
    filas = m.q("publicaciones", _SQL_PUBLICACIONES)
    por: dict[str, dict[str, int]] = {}
    ids: dict[tuple, set] = {}
    for f in filas:
        clave = f"{f['canal']}:{f['cuenta']}"
        por.setdefault(clave, {})
        est = (f.get("situacion") or f.get("status") or "?").lower()
        por[clave][est] = por[clave].get(est, 0) + 1
        if f.get("listing_id"):
            ids.setdefault((f["canal"], f["listing_id"]), set()).add(f["sku"])
    dup = sum(1 for v in ids.values() if len(v) > 1)
    avisos = []
    if dup:
        avisos.append(f"{dup} listing_id apuntan a más de un SKU (base y variante): "
                      "deduplicar por listing_id con el SELLER_SKU de la API")
    # `ml_*` son de costing.costos_finales (canal mercado_libre) y se unen por SKU a
    # TODAS las filas: en otros canales son referencia, no su comisión real.
    return {"meta": {"por_canal_cuenta_estado": por, "listing_ids_con_varios_skus": dup,
                     "avisos": avisos,
                     "nota": "ml_* = costing.costos_finales canal mercado_libre, unido por sku"},
            "filas": filas}


# ── 2. Contenedores ───────────────────────────────────────────────────────────
_SQL_CONTENEDORES = """
select contenedor,
       count(*) as n_skus,
       count(*) filter (where costo_cbm > 0 and cajas > 0 and piezas_por_caja > 0) as n_skus_con_volumen,
       sum(cajas * piezas_por_caja) filter (where cajas > 0 and piezas_por_caja > 0) as piezas,
       sum(cajas) filter (where cajas > 0) as cajas,
       sum((costo_cbm / 7500.0) * cajas * piezas_por_caja)
           filter (where costo_cbm > 0 and cajas > 0 and piezas_por_caja > 0) as m3,
       sum(peso * cajas * piezas_por_caja)
           filter (where peso > 0 and cajas > 0 and piezas_por_caja > 0) as kg,
       count(*) filter (where revisado_at is not null) as n_validados,
       min(created_at) as primero, max(updated_at) as ultimo
  from costing.costos_validados
 where nullif(contenedor, '') is not null
 group by contenedor
 order by contenedor
"""


def _contenedores(m: _Medidor) -> dict:
    filas = m.q("contenedores", _SQL_CONTENEDORES)
    par = _parametros().get("contenedor", {})
    costo = float(par.get("costo_mxn", 525000))
    lo, hi = (par.get("rango_m3_normal") or [55, 76])
    for f in filas:
        m3 = float(f["m3"]) if f.get("m3") else None
        f["m3"] = round(m3, 4) if m3 else None
        f["costo_m3"] = round(costo / m3, 2) if m3 else None
        f["rango_ok"] = bool(m3 and lo <= m3 <= hi)
        f["completo"] = f["n_skus"] == f["n_skus_con_volumen"]
    return {"meta": {"costo_contenedor_mxn": costo, "rango_m3_normal": [lo, hi],
                     "formula": "m3 = Σ costo_cbm/7500 × cajas × piezas_por_caja",
                     "en_rango": sum(1 for f in filas if f["rango_ok"]),
                     "completos": sum(1 for f in filas if f["completo"])},
            "filas": filas}


# ── 3. Ventas por día ─────────────────────────────────────────────────────────
_SQL_VENTAS_DIA = """
select date as fecha, cuenta, item_id, sku::text as sku, is_full,
       units_sold, revenue, sale_fee, fuente
  from channel.sales_daily_completa
 where canal = 'mercado_libre' and cuenta in ('BEKURA', 'SANCORFASHION')
   and date >= (now() at time zone 'America/Mexico_City')::date - %s
 order by fecha, cuenta, item_id
"""

_SQL_OTROS_CANALES = f"""
select o.canal, o.cuenta, i.sku::text as sku, i.item_id,
       count(distinct o.external_order_id) as pedidos,
       sum(i.cantidad) as unidades,
       sum(i.precio_unitario * i.cantidad) filter (where i.precio_unitario > 0) as ingreso,
       count(*) filter (where i.precio_unitario is null or i.precio_unitario <= 0) as lineas_sin_precio,
       sum(i.comision) as comision,
       min(o.creado_at) as primera, max(o.creado_at) as ultima
  from channel.order_items i
  join channel.orders o using (canal, cuenta, external_order_id)
 where o.canal <> 'mercado_libre'
   and o.creado_at >= now() - make_interval(days => %s)
   and lower(coalesce(o.estado_canal, '')) not in {_CANCELADOS}
 group by 1, 2, 3, 4
 order by 1, 2, 6 desc
"""


def _ventas_dia(m: _Medidor) -> dict:
    ml = m.q("ventas_dia_ml", _SQL_VENTAS_DIA, (DIAS_VENTAS,))
    otros = m.q("ventas_otros_canales_30d", _SQL_OTROS_CANALES, (DIAS_OTROS_CANALES,))
    sin_item = sum(1 for f in ml if not f.get("item_id"))
    avisos = []
    if sin_item:
        avisos.append(f"{sin_item} renglones de ML sin item_id (backfill desde Woo, jul): "
                      "se casan por SKU, no por publicación")
    sin_precio = sum(int(f.get("lineas_sin_precio") or 0) for f in otros)
    if sin_precio:
        avisos.append(f"{sin_precio} líneas de otros canales sin precio (Amazon ~80%): "
                      "su ingreso es parcial")
    fechas = [f["fecha"] for f in ml]
    return {"meta": {"dias": DIAS_VENTAS, "dias_otros_canales": DIAS_OTROS_CANALES,
                     "desde": min(fechas) if fechas else None,
                     "hasta": max(fechas) if fechas else None,
                     "dia_parcial": almacen.hoy_cdmx().isoformat(),
                     "avisos": avisos},
            "ml_dia": ml, "otros_canales_30d": otros}


# ── 4. Líneas de pedido ───────────────────────────────────────────────────────
_SQL_LINEAS = f"""
with cat as (
  select distinct on (listing_id) listing_id, category_id
    from channel.listings
   where canal = 'mercado_libre' and category_id is not null
     and nullif(listing_id, '') is not null
   order by listing_id, updated_at desc
)
select (o.creado_at at time zone 'America/Mexico_City')::date as fecha,
       to_char(o.creado_at at time zone 'America/Mexico_City', 'HH24:MI') as hora,
       o.cuenta, o.external_order_id as orden, i.linea, i.item_id, i.sku::text as sku,
       i.cantidad, i.precio_unitario, i.comision, i.es_fulfillment,
       lower(o.estado_canal) as estado, cat.category_id
  from channel.order_items i
  join channel.orders o using (canal, cuenta, external_order_id)
  left join cat on cat.listing_id = i.item_id
 where o.canal = 'mercado_libre' and o.cuenta in ('BEKURA', 'SANCORFASHION')
   and o.creado_at >= now() - make_interval(days => %s)
   and lower(coalesce(o.estado_canal, '')) not in {_CANCELADOS}
 order by o.creado_at
"""


def _lineas(m: _Medidor) -> dict:
    filas = m.q("lineas_ml", _SQL_LINEAS, (DIAS_VENTAS,))
    sin_com = sum(1 for f in filas if not f.get("comision"))
    sin_cat = sum(1 for f in filas if not f.get("category_id"))
    return {"meta": {"dias": DIAS_VENTAS, "lineas_sin_comision": sin_com,
                     "lineas_sin_categoria": sin_cat,
                     "dia_parcial": almacen.hoy_cdmx().isoformat(),
                     "desde_real": filas[0]["fecha"] if filas else None,
                     "nota": "comision = total de la línea (no por unidad); "
                             "category_id = la de channel.listings para ese item_id"},
            "filas": filas}


# ── 5. Competencia ────────────────────────────────────────────────────────────
# Consulta probada en el informe de competencia (3.3 s, 5,112 filas el 28-sep).
_SQL_COMPETENCIA = """
with pubs as (
  select l.sku::text as sku, a.legacy_code as cuenta, l.listing_id, lower(l.situacion) as estado,
         (l.logistic_type = 'fulfillment') as es_full,
         coalesce(l.price_sale, l.price) as precio_propio, l.price_base as precio_lista,
         l.price_sale_at
    from channel.listings l
    join core.accounts a on a.id = l.account_id
   where l.canal = 'mercado_libre' and nullif(l.listing_id, '') is not null
     and lower(l.situacion) in ('active', 'paused')
),
serp_raw as (
  select c.sku::text as sku, st.termino, r.precio, r.visitas_30d, r.capturado_en
    from enrich.market_sku_config c
    join enrich.market_search_term st on st.id = c.termino_id
    join enrich.market_search_results r on r.termino_id = c.termino_id
   where c.canal = 'mercado_libre' and not r.es_nuestro and r.precio > 0
),
serp_m as (
  select sku, percentile_cont(0.5) within group (order by precio) as med0
    from serp_raw group by sku
),
serp as (
  select s.sku, max(s.termino) as termino, count(*) as n_comp,
         round(avg(s.precio), 2) as prom,
         percentile_cont(0.5) within group (order by s.precio) as mediana,
         min(s.precio) as minimo, max(s.precio) as maximo,
         count(*) filter (where s.precio between m.med0 / 3 and m.med0 * 3) as n_filtrado,
         percentile_cont(0.5) within group (order by s.precio)
           filter (where s.precio between m.med0 / 3 and m.med0 * 3) as mediana_filtrada,
         round(sum(s.precio * coalesce(s.visitas_30d, 0))
               / nullif(sum(coalesce(s.visitas_30d, 0)), 0), 2) as prom_pond_visitas,
         max(s.capturado_en) as serp_capturado_en
    from serp_raw s join serp_m m using (sku)
   group by s.sku
),
best as (
  select k.sku::text as sku, max(k.categoria_id) as categoria_id, count(*) as n_best,
         percentile_cont(0.5) within group (order by b.precio) as mediana_best,
         min(b.precio) as min_best, max(b.precio) as max_best,
         max(b.capturado_en) as best_capturado_en
    from enrich.market_skus_v k
    join enrich.market_bestsellers b on b.categoria_id = k.categoria_id and b.nivel = 'hoja'
   where k.canal = 'mercado_libre' and b.precio > 0 and not b.es_nuestro
   group by k.sku
)
select p.*, s.termino, s.n_comp, s.prom, s.mediana, s.minimo, s.maximo,
       s.n_filtrado, s.mediana_filtrada, s.prom_pond_visitas, s.serp_capturado_en,
       b.categoria_id as best_categoria_id, b.n_best, b.mediana_best, b.min_best, b.max_best,
       b.best_capturado_en,
       round(p.precio_propio / nullif(s.mediana_filtrada, 0)::numeric, 2) as brecha_serp,
       round(p.precio_propio / nullif(b.mediana_best, 0)::numeric, 2) as brecha_best
  from pubs p
  left join serp s on s.sku = p.sku
  left join best b on b.sku = p.sku
"""

_SQL_SERP_CRUDA = """
select c.sku::text as sku, st.termino, st.medido_en, r.posicion, r.externo_id, r.titulo,
       r.precio, r.visitas_30d, r.seller, r.capturado_en
  from enrich.market_sku_config c
  join enrich.market_search_term st on st.id = c.termino_id
  join enrich.market_search_results r on r.termino_id = c.termino_id
 where c.canal = 'mercado_libre' and not r.es_nuestro and r.precio > 0
   and c.sku in (select sku from channel.listings
                  where canal = 'mercado_libre' and lower(situacion) in ('active', 'paused'))
 order by c.sku, r.posicion
"""

_SQL_BEST_CRUDA = """
select b.categoria_id, b.posicion, b.externo_id, b.titulo, b.precio, b.precio_lista,
       b.vendidos, b.visitas_30d, b.capturado_en
  from enrich.market_bestsellers b
 where b.canal = 'mercado_libre' and b.nivel = 'hoja' and not b.es_nuestro and b.precio > 0
   and b.categoria_id in (select k.categoria_id from enrich.market_skus_v k
                           join channel.listings l on l.sku = k.sku and l.canal = 'mercado_libre'
                          where k.canal = 'mercado_libre' and lower(l.situacion) in ('active', 'paused'))
 order by b.categoria_id, b.posicion
"""

# Categoría curada y raíz por SKU: el grupo de encogimiento de la elasticidad.
_SQL_SKU_CATEGORIA = """
select sku::text as sku, categoria_id, categoria_nombre, raiz_id, raiz_nombre, termino_general
  from enrich.market_skus_v
 where canal = 'mercado_libre'
"""


def _competencia(m: _Medidor) -> dict:
    resumen = m.q("competencia_resumen", _SQL_COMPETENCIA)
    serp = m.q("competencia_serp_cruda", _SQL_SERP_CRUDA)
    best = m.q("competencia_best_cruda", _SQL_BEST_CRUDA)
    cats = m.q("sku_categoria", _SQL_SKU_CATEGORIA)
    serp_por_sku: dict[str, dict] = {}
    for f in serp:
        d = serp_por_sku.setdefault(f["sku"], {"termino": f["termino"], "medido_en": f["medido_en"],
                                               "capturado_en": f["capturado_en"], "resultados": []})
        d["resultados"].append({"pos": f["posicion"], "precio": f["precio"],
                                "visitas_30d": f["visitas_30d"], "titulo": f["titulo"],
                                "id": f["externo_id"], "seller": f["seller"]})
    best_por_cat: dict[str, dict] = {}
    for f in best:
        d = best_por_cat.setdefault(f["categoria_id"], {"capturado_en": f["capturado_en"], "top": []})
        if len(d["top"]) < 20:
            d["top"].append({"pos": f["posicion"], "precio": f["precio"],
                             "precio_lista": f["precio_lista"], "vendidos": f["vendidos"],
                             "visitas_30d": f["visitas_30d"], "titulo": f["titulo"],
                             "id": f["externo_id"]})
    ahora = dt.datetime.now(dt.timezone.utc)

    def _edad(x):
        return (ahora - x).days if isinstance(x, dt.datetime) else None

    act_full = [r for r in resumen if r["estado"] == "active" and r["es_full"]]
    viejas = sum(1 for r in act_full if r.get("serp_capturado_en") and (_edad(r["serp_capturado_en"]) or 0) > 30)
    return {"meta": {"filas_resumen": len(resumen),
                     "activas_full": len(act_full),
                     "activas_full_con_serp": sum(1 for r in act_full if r.get("n_comp")),
                     "activas_full_serp_filtrada_n5": sum(1 for r in act_full if (r.get("n_filtrado") or 0) >= 5),
                     "activas_full_con_best": sum(1 for r in act_full if r.get("n_best")),
                     "activas_full_serp_mas_30d": viejas,
                     "skus_con_serp": len(serp_por_sku), "categorias_con_best": len(best_por_cat),
                     "nota": "mediana_filtrada = mediana SERP de precios dentro de [med/3, med×3]; "
                             "SERP sin envío, precio visible redondeado; sin historia en la base"},
            "resumen": resumen, "serp_por_sku": serp_por_sku,
            "best_por_categoria": best_por_cat, "sku_categoria": cats}


# ── 6. Historial de precio limpio + censura ───────────────────────────────────
_SQL_PARES_REBOTE = """
with h as (
  select h.sku::text as sku, a.legacy_code as cuenta, h.campo, h.valor_nuevo,
         date_trunc('week', h.changed_at) as semana,
         lag(h.valor_anterior) over (partition by h.sku, h.account_id, h.campo
                                     order by h.changed_at, h.id) as ant_previo
    from channel.listing_history h
    join core.accounts a on a.id = h.account_id
   where h.canal = 'mercado_libre' and h.campo in ('price', 'is_fulfillment', 'situacion')
     and h.changed_at >= now() - make_interval(days => %s)
),
sem as (
  select sku, cuenta, campo, semana, count(*) as n,
         count(*) filter (where valor_nuevo is not distinct from ant_previo) as rebotes
    from h group by 1, 2, 3, 4
)
select sku, cuenta,
       array_agg(distinct campo) as campos,
       max(n) as max_cambios_semana, sum(n) as cambios, sum(rebotes) as rebotes
  from sem
 where n >= %s and rebotes >= n * %s
 group by 1, 2
 order by cambios desc
"""

# Los pares con rebote se excluyen EN la base (`<> all(...)`): son ~98% de las
# filas (167,532 → 2,486 de price el 28-sep) y no tiene caso bajarlas.
_SQL_HIST_PRECIO = """
select h.sku::text as sku, a.legacy_code as cuenta, h.changed_at,
       h.valor_anterior as anterior, h.valor_nuevo as nuevo, h.detectado_via as via
  from channel.listing_history h
  join core.accounts a on a.id = h.account_id
 where h.canal = 'mercado_libre' and h.campo = 'price'
   and h.changed_at >= now() - make_interval(days => %(dias)s)
   and (h.sku::text || '|' || a.legacy_code) <> all(%(excluir)s)
 order by h.sku, a.legacy_code, h.changed_at
"""

# Censura: cambios de situacion (pausa/activa) y de stock_full que CRUZAN el cero
# (quedarse sin stock / volver a tenerlo). Los demás cambios de stock no importan
# para saber si había oferta ese día y son la mayor parte del volumen.
_SQL_HIST_CENSURA = """
select h.sku::text as sku, a.legacy_code as cuenta, h.campo, h.changed_at,
       h.valor_anterior as anterior, h.valor_nuevo as nuevo
  from channel.listing_history h
  join core.accounts a on a.id = h.account_id
 where h.canal = 'mercado_libre'
   and h.changed_at >= now() - make_interval(days => %(dias)s)
   and (h.sku::text || '|' || a.legacy_code) <> all(%(excluir)s)
   and (h.campo = 'situacion'
        or (h.campo = 'stock_full'
            and (coalesce(nullif(h.valor_anterior, '')::numeric, -1) = 0)
                <> (coalesce(nullif(h.valor_nuevo, '')::numeric, -1) = 0)))
 order by h.sku, a.legacy_code, h.campo, h.changed_at
"""

_SQL_HIST_TOTALES = """
select count(*) filter (where campo = 'price') as price,
       count(*) filter (where campo = 'situacion') as situacion,
       count(*) filter (where campo = 'stock_full') as stock_full
  from channel.listing_history
 where canal = 'mercado_libre' and changed_at >= now() - make_interval(days => %s)
"""

_SQL_STOCK_HIST = """
select cuenta, item_id, sku::text as sku, valid_from, valid_to, price, status,
       stock_full, logistic_type
  from analytics.stock_hist
 where cuenta in ('BEKURA', 'SANCORFASHION')
 order by cuenta, item_id, valid_from
"""


def _historial_precio(m: _Medidor) -> dict:
    pares = m.q("pares_rebote", _SQL_PARES_REBOTE, (DIAS_VENTAS, REBOTE_MIN_SEMANA, REBOTE_FRACCION))
    excluir = [f"{p['sku']}|{p['cuenta']}" for p in pares] or ["-"]
    totales = (m.q("historial_totales", _SQL_HIST_TOTALES, (DIAS_VENTAS,)) or [{}])[0]
    precio = m.q("historial_price", _SQL_HIST_PRECIO, {"dias": DIAS_VENTAS, "excluir": excluir})
    censura = m.q("historial_censura", _SQL_HIST_CENSURA, {"dias": DIAS_VENTAS, "excluir": excluir})
    stock_hist = m.q("stock_hist", _SQL_STOCK_HIST)
    return {"meta": {"dias": DIAS_VENTAS,
                     "criterio_rebote": f"≥{REBOTE_MIN_SEMANA} cambios/semana en price, "
                                        f"is_fulfillment o situacion y ≥{int(REBOTE_FRACCION*100)}% "
                                        "regresando al valor de dos cambios atrás",
                     "pares_excluidos": len(pares),
                     "cambios_totales_150d": totales,
                     "cambios_price_limpios": len(precio), "cambios_censura_limpios": len(censura),
                     "censura_incluye": "situacion (todos) + stock_full solo al cruzar el cero",
                     "stock_hist_rango": "2026-04-29 → 2026-07-15 (congelada)",
                     "nota": "price = item.price (lista tras campañas del vendedor), NO price_sale; "
                             "listing_history no trae listing_id: el par es sku×cuenta"},
            "precio": precio, "censura": censura, "pares_rebote": pares, "stock_hist": stock_hist}


# ── 7. Envío real ─────────────────────────────────────────────────────────────
_SQL_ENVIO_BASE = f"""
with base as (
  select i.item_id, o.cuenta, sc.costo_vendedor, i.cantidad, i.precio_unitario, i.es_fulfillment,
         case when i.precio_unitario < 299 then 0 when i.precio_unitario < 500 then 299
              when i.precio_unitario < 1000 then 500 else 1000 end as tramo
    from enrich.order_shipping_cost sc
    join channel.orders o on o.canal = 'mercado_libre' and o.cuenta = sc.cuenta
                         and o.external_order_id = sc.external_order_id
    join channel.order_items i on i.canal = o.canal and i.cuenta = o.cuenta
                              and i.external_order_id = o.external_order_id
   where o.creado_at >= now() - make_interval(days => %s)
     and lower(coalesce(o.estado_canal, '')) not in {_CANCELADOS}
     and sc.costo_vendedor is not null and i.item_id is not null
)
"""

_SQL_ENVIO_ITEM = _SQL_ENVIO_BASE + """
select item_id, cuenta, bool_or(es_fulfillment) as es_full, count(*) as n,
       percentile_cont(0.5) within group (order by costo_vendedor) as mediana,
       round(avg(costo_vendedor), 2) as promedio,
       count(*) filter (where cantidad = 1) as n_1u,
       percentile_cont(0.5) within group (order by costo_vendedor)
         filter (where cantidad = 1) as mediana_1u,
       percentile_cont(0.5) within group (order by precio_unitario) as precio_mediano
  from base group by 1, 2
"""

_SQL_ENVIO_TRAMO = _SQL_ENVIO_BASE + """
select item_id, cuenta, tramo, count(*) as n,
       percentile_cont(0.5) within group (order by costo_vendedor) as mediana
  from base where cantidad = 1 group by 1, 2, 3
"""


def _envio_real(m: _Medidor) -> dict:
    items = m.q("envio_item", _SQL_ENVIO_ITEM, (DIAS_ENVIO,))
    tramos = m.q("envio_tramo", _SQL_ENVIO_TRAMO, (DIAS_ENVIO,))
    por: dict[tuple, dict] = {}
    for t in tramos:
        por.setdefault((t["item_id"], t["cuenta"]), {})[str(t["tramo"])] = {"n": t["n"], "mediana": t["mediana"]}
    for f in items:
        f["por_tramo_1u"] = por.get((f["item_id"], f["cuenta"]), {})
    return {"meta": {"dias": DIAS_ENVIO,
                     "nota": "costo_vendedor por PEDIDO (ML: una línea por pedido); "
                             "mediana_1u y por_tramo_1u solo con pedidos de 1 pieza; "
                             "tramos de precio [0,299) [299,500) [500,1000) [1000,∞)"},
            "filas": items}


# ── 8. Visitas (caché de la base, respaldo) ───────────────────────────────────
_SQL_VISITAS_CACHE = """
select sku::text as sku, cuenta, listing_id, visits_30d, units_30d, sale_price, estado,
       metrics_updated_at, periodo
  from enrich.market_listing_metrics
 where canal = 'mercado_libre'
   and periodo = (select max(periodo) from enrich.market_listing_metrics where canal = 'mercado_libre')
"""


def _visitas_cache(m: _Medidor) -> dict:
    filas = m.q("visitas_cache", _SQL_VISITAS_CACHE)
    return {"meta": {"periodo": filas[0]["periodo"] if filas else None,
                     "nota": "visits_30d = ventana móvil de 30 d sobrescrita a diario "
                             "(cron competencia-visitas 12:00 UTC); units_30d y sale_price vienen "
                             "vacíos desde septiembre. Es respaldo de la serie de la API"},
            "filas": filas}


# ── Frescura (va al resumen y de ahí a estado.json) ───────────────────────────
_SQL_FRESCURA_LISTINGS = """
select l.canal, a.legacy_code as cuenta, count(*) as filas,
       max(l.updated_at) as max_updated_at, max(l.price_sale_at) as max_price_sale_at
  from channel.listings l join core.accounts a on a.id = l.account_id
 where l.canal <> 'general'
 group by 1, 2
"""

_SQL_FRESCURA_OTRAS = """
select
  (select max(date) from channel.sales_daily_completa where canal = 'mercado_libre') as ventas,
  (select max(capturado_en) from enrich.market_search_results) as competencia_serp,
  (select max(capturado_en) from enrich.market_bestsellers) as competencia_best,
  (select max(metrics_updated_at) from enrich.market_listing_metrics) as visitas_cache,
  (select max(consultado_at) from enrich.order_shipping_cost) as envio_real,
  (select max(actualizado) from ops.stock_watch_photo) as stock_odoo,
  (select max(updated_at) from costing.costos_validados) as costos_validados
"""


def _frescura(m: _Medidor) -> dict:
    fr: dict[str, Any] = {}
    for f in m.q("frescura_listings", _SQL_FRESCURA_LISTINGS):
        fr[f"{f['canal']}:{f['cuenta']}"] = {"filas": f["filas"], "max_updated_at": f["max_updated_at"],
                                            "max_price_sale_at": f["max_price_sale_at"]}
    otras = m.q("frescura_otras", _SQL_FRESCURA_OTRAS)
    if otras:
        fr.update(otras[0])
    return fr


# ── Orquestación ──────────────────────────────────────────────────────────────
EXTRACTORES: dict[str, Callable[[_Medidor], dict]] = {
    "kubera_publicaciones.json": _publicaciones,
    "kubera_contenedores.json": _contenedores,
    "kubera_ventas_dia.json": _ventas_dia,
    "kubera_lineas.json": _lineas,
    "kubera_competencia.json": _competencia,
    "kubera_historial_precio.json": _historial_precio,
    "kubera_envio_real.json": _envio_real,
    "kubera_visitas_cache.json": _visitas_cache,
}


def extraer(dia: dt.date | None = None, solo: list[str] | None = None) -> dict:
    """Corre las extracciones y escribe `crudo/<día>/kubera_*.json`. Devuelve el resumen.

    `solo`: nombres de archivo (con o sin `kubera_` / `.json`) para correr un
    subconjunto. Un archivo que falla no detiene a los demás: queda con
    `ok: false` y su error en el resumen.
    """
    dia = dia or almacen.hoy_cdmx()
    t0 = time.monotonic()
    elegidos = EXTRACTORES
    if solo:
        norm = {s.replace("kubera_", "").replace(".json", "") for s in solo}
        elegidos = {k: v for k, v in EXTRACTORES.items()
                    if k.replace("kubera_", "").replace(".json", "") in norm}
    archivos: dict[str, dict] = {}
    for nombre, fn in elegidos.items():
        m = _Medidor()
        t1 = time.monotonic()
        try:
            contenido = fn(m)
            envoltura = {"generado_at": almacen.ahora_iso(), "fuente": "kubera",
                         "consulta": nombre.replace(".json", ""), **contenido}
            destino = almacen.escribir_json(almacen.crudo(nombre, dia), envoltura)
            archivos[nombre] = {"ok": True, "filas": sum(c["filas"] for c in m.consultas),
                                "duracion_s": round(time.monotonic() - t1, 2),
                                "bytes": destino.stat().st_size, "consultas": m.consultas,
                                "avisos": (contenido.get("meta") or {}).get("avisos", [])}
        except Exception as exc:  # noqa: BLE001 — una consulta rota no tumba las demás
            log.exception("kubera: %s falló", nombre)
            archivos[nombre] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500],
                                "duracion_s": round(time.monotonic() - t1, 2), "consultas": m.consultas}
    if solo:  # corrida parcial: se conserva lo que ya había del día en el resumen
        previo = almacen.leer_json(almacen.crudo("extraer_kubera_resumen.json", dia), {}) or {}
        archivos = {**(previo.get("archivos") or {}), **archivos}
    m = _Medidor()
    try:
        frescura = _frescura(m)
    except Exception as exc:  # noqa: BLE001
        frescura = {"error": str(exc)[:300]}
    resumen = {"etapa": "extraer_kubera", "dia": dia.isoformat(), "generado_at": almacen.ahora_iso(),
               "ok": all(a["ok"] for a in archivos.values()),
               "filas": sum(a.get("filas", 0) for a in archivos.values()),
               "duracion_s": round(time.monotonic() - t0, 2),
               "archivos": archivos, "frescura": frescura,
               "avisos": [f"{k}: {a['error']}" for k, a in archivos.items() if not a["ok"]]}
    almacen.escribir_json(almacen.crudo("extraer_kubera_resumen.json", dia), resumen)
    return resumen


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="Extracción de kubera (solo SELECT) a crudo/<día>/")
    ap.add_argument("--solo", nargs="*", help="p. ej. publicaciones lineas historial_precio")
    args = ap.parse_args()
    r = extraer(solo=args.solo)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "consultas"} if isinstance(v, dict) else v
                      for k, v in r["archivos"].items()}, ensure_ascii=False, indent=1, default=str))
    print("ok:", r["ok"], "filas:", r["filas"], "duración:", r["duracion_s"], "s")
