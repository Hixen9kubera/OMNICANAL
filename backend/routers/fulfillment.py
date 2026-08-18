"""
fulfillment.py — Panel de reabastecimiento (CLON del tablero kubera-fulfillment
de José), leyendo DIRECTO de la BD kubera v4 — primer lector de producción.

Fuentes (todas vistas/tablas de la migración):
  channel.listings              → foto viva por listing (webhook, segundos)
  channel.sales_daily_completa  → ventas sin hueco (hist dailytrack + vivo)
  channel.order_items           → comisión REAL cobrada por el canal
  costing.costos_finales        → costo y precio sugerido (canal ML)
  costing.costos_validados      → dimensiones → categoría de TAMAÑO
  ml_envio_real (MySQL, caché)  → envío REAL por embarque (services/envio_real)

Equivalencias vs el original (documentadas para el clon):
  STOCK ODOO   → STOCK PROPIO = DROP real (bodega Woo por SKU, listing
                 canal='general'; fuente: stock_watch_foto de Brandon v0.27.0.
                 Fallback: stock_own declarado por el marketplace).
  DÍAS ODOO    → EDAD S/VENTA (días desde la última venta registrada).
  VISITAS/CR%  → SIN DATO (daily_visits quedó fuera del alcance 2026-07-28).
  TAM          → derivada de costos_validados (lado mayor): S<30, M<60,
                 L<120, XL≥120 cm; S/C sin dimensiones.

COBERTURA y SUGERIDO A FULL se RETIRARON de la tabla (Eduardo, 10-ago): la
vista pasó de "qué reabastecer" a "qué deja dinero", y en su lugar entraron las
columnas de costo del popup de márgenes. `channel.restock_panel` sigue viva en
la BD — lo que se quitó es la columna, no el cálculo.

  GET /api/fulfillment/dashboard → KPIs + conteo por cuenta + serie diaria
  GET /api/fulfillment/tabla     → filas por SKU (filtros del clon) + sparkline
  GET /api/fulfillment/detalle   → serie diaria de UN SKU (modal del sparkline)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from config import settings
from services import supabase_db as sdb


# ── Las lecturas de kubera, FUERA del event loop ────────────────────────────
# psycopg2 es BLOQUEANTE: `sdb.fetch_*` dentro de la corrutina para el backend
# entero mientras Postgres contesta (medido: 0.98 s el query base del dashboard,
# y el vigilante del event loop cachó a este archivo congelando 6 s la tabla de
# Análisis). Estas envolturas hacen el mismo query en un hilo: el que espera es
# el hilo, no el backend. Toda lectura nueva de este router va por aquí.
async def _fetch_all(sql: str, par: Any = None) -> list[dict[str, Any]]:
    return await asyncio.to_thread(sdb.fetch_all, sql, par)


async def _fetch_one(sql: str, par: Any = None) -> dict[str, Any] | None:
    return await asyncio.to_thread(sdb.fetch_one, sql, par)


async def _fetch_scalar(sql: str, par: Any = None) -> Any:
    return await asyncio.to_thread(sdb.fetch_scalar, sql, par)


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
#
# Las columnas de COSTO ordenan por el valor que calcula el SQL, que usa el
# envío ESTIMADO; la celda puede acabar mostrando el envío REAL (se resuelve
# después, por página — ver _envio_real_en_filas). Es deliberado: el orden se
# decide sobre las ~2,000 filas y el refinamiento solo alcanza a las 50 de la
# página. La diferencia mueve centavos en el margen, no el ranking.
_ORDEN = {
    "venta": ("venta", "desc"),
    "uds": ("uds", "desc"),
    "stock_full": ("stock_full", "desc"),
    "stock_propio": ("stock_propio", "desc"),
    "edad": ("edad_sin_venta_d", "desc"),
    "costo": ("costo", "desc"),
    "comision": ("comision_unit", "desc"),
    "costo_final": ("costo_final", "desc"),
    # margen NETO = ya descontados los cobros del marketplace. Ordenar ascendente
    # es el "filtro" de lo que está vendiendo mal: lo peor queda arriba.
    "margen_neto": ("margen_neto_pct", "desc"),
    "ganancia": ("ganancia_periodo", "desc"),
    "crec": ("crec_7d_pct", "desc"),
    "sku": ("sku", "asc"),
    # Medidas: desde que dejaron de vivir en la tarjeta del chip y son columnas
    # propias, ordenar por ellas es la forma de barrer el catálogo por bulto
    # (y de sacar a flote los pesos de caja capturados como pieza).
    "largo": ("largo", "desc"),
    "ancho": ("ancho", "desc"),
    "alto": ("alto", "desc"),
    "peso": ("peso", "desc"),
    # La columna Tamaño (Chico/Mediano/Grande) ordena por el lado mayor, no
    # por la letra — ver la nota de `lado_mayor` en `_BASE`.
    "tam": ("lado_mayor", "desc"),
}
_DIRS = {"asc", "desc"}

# ── ZONA HORARIA ────────────────────────────────────────────────────────────
# `current_date` es la fecha DEL SERVIDOR, que en Railway corre en UTC — pero
# las ventas estan fechadas en horario de MEXICO (asi las construye
# channel.sales_daily). Desde las 6 de la tarde de Mexico el servidor ya cambio
# de dia y la ventana se corria: "7 dias" entregaba 6 (Eduardo lo detecto el
# 2-ago comparando contra el panel de ML). No se perdia ninguna venta: se
# preguntaba por un rango equivocado, y el total cambiaba segun la HORA a la
# que abrieras el panel.
#
# Toda consulta de este router pasa por _mx(): la pregunta queda en la misma
# zona horaria que el dato. Kubera opera en Mexico y la vista ya esta fechada
# asi en duro — una variable de configuracion seria una segunda fuente de
# verdad sobre la zona horaria, o sea otro lugar donde desincronizarse.
_HOY_MX = "(now() at time zone 'America/Mexico_City')::date"


def _mx(sql: str) -> str:
    """Cambia `current_date` (UTC) por la fecha de HOY en Mexico."""
    return sql.replace("current_date", _HOY_MX)


# CTEs compartidos del clon: listings agregados POR SKU + ventas del período.
# %(dias)s = período; %(cuenta)s = filtro de cuenta (None = todas).
_BASE = _mx("""
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
         -- Publicaciones de MERCADO LIBRE con su cuenta: es lo que hace falta
         -- para pedir las VISITAS (ML las da por item y con el token de su
         -- cuenta). Amazon queda fuera porque no tiene equivalente.
         jsonb_agg(distinct jsonb_build_object(
             'cuenta', a.legacy_code, 'item', l.listing_id))
           filter (where l.canal = 'mercado_libre'
                     and l.listing_id is not null)  as pubs_ml,
         -- CENSO DE PUBLICACIONES para la tarjeta de los puntitos: en qué
         -- cuentas existe el SKU y cómo está cada una. NO reusa `precios` de
         -- arriba porque ese filtra `price is not null` y se comería 267
         -- publicaciones de ML y 115 de Amazon — una sin precio sigue siendo
         -- una publicación, y esconderla haría que la tarjeta contradiga a los
         -- puntos, que sí las cuentan.
         jsonb_agg(distinct jsonb_build_object(
             'cuenta', a.legacy_code, 'canal', l.canal,
             'situacion', l.situacion, 'item', l.listing_id,
             'price', l.price, 'full', l.is_fulfillment))
                                                    as publicaciones,
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
         -- Unidades SOLO de Mercado Libre: es el numerador honesto de la
         -- conversión, porque las visitas también son solo de ML. Dividir las
         -- unidades totales (que incluyen Amazon) entre visitas de ML inflaría
         -- el CR%% de cualquier SKU que venda fuerte en Amazon. (El %% va
         -- escapado: psycopg2 lee un %% suelto como marcador de parámetro.)
         sum(units_sold) filter (where canal = 'mercado_libre')  as uds_ml,
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
  -- El chip S/M/L/XL sale del LADO MÁS LARGO. Las medidas viajan con él para
  -- que la tarjeta pueda mostrar de dónde salió la letra: sin eso el chip es un
  -- veredicto sin sustento, y un SKU en el borde (29.9 vs 30.1 cm) cambia de
  -- categoría sin que nada lo explique.
  select sku, largo, ancho, alto, peso,
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
com_canal as (
  -- Comisión REAL del marketplace en el período, POR UNIDAD y POR CANAL/CUENTA
  -- (Eduardo, 5-ago; el desglose se agregó el 10-ago para poder explicarla al
  -- pasar el cursor). Sigue siendo el cobro REAL de Meli, no una tasa supuesta.
  --
  -- SALE DE LA MISMA VISTA QUE LAS UNIDADES (11-ago). Antes venía de
  -- channel.order_items, que solo tiene detalle orden por orden desde el 15/16
  -- de julio —cuando el webhook empezó a capturar bien—. Eso hacía dos cosas
  -- malas:
  --
  --   1. Medía la comisión sobre una MUESTRA. En TEC-1284-NEG-27" salía de 8
  --      piezas de las 175 vendidas (4.6%%), porque las otras 167 son de junio
  --      y nunca entraron a channel.orders.
  --   2. Rompía el selector de período. A 60 días 597 SKUs tenían margen; a
  --      120 días, 598 — UNO más. Pedir más historia no daba más cobertura,
  --      porque order_items no llega más atrás, y la columna simplemente se
  --      quedaba vacía sin decir por qué.
  --
  -- channel.sales_daily_completa cose analytics.sales_daily_hist (hasta el
  -- 15-jul) con channel.sales_daily (desde el 16-jul) y trae sale_fee con
  -- cobertura del 100%% en las dos ramas. Al leer de aquí, comisión y unidades
  -- salen de las MISMAS filas y la cobertura pasa de 67%% a 98.8%% a 120 días.
  --
  -- La vista ya viene NETA de cancelaciones (cero valores negativos en 18,473
  -- filas; y el conteo contra la API de ML cuadró en 429 contra 432), por eso
  -- aquí no hay filtro de estado_canal como en la versión de pedidos.
  --
  -- Solo entran líneas con comisión > 0: Amazon todavía la registra en cero
  -- (falta Finances API) y promediarla con ML abarataría el costo. Un SKU que
  -- solo vende en Amazon se queda sin margen neto — "—" es más honesto que
  -- decir que Amazon no cobra nada.
  select i.sku, o.canal, o.cuenta,
         sum(i.cantidad)::int as uds,
         sum(coalesce(i.comision, 0)) / nullif(sum(i.cantidad), 0) as comision_unit,
         'pedidos' as origen
  from channel.order_items i
  join channel.orders o using (canal, cuenta, external_order_id)
  where (o.creado_at at time zone 'America/Mexico_City')::date
        > current_date - %(dias)s::int
    and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
    and coalesce(i.comision, 0) > 0
    and i.sku is not null
    and (%(cuenta)s::text is null or o.cuenta = %(cuenta)s)
  group by 1, 2, 3
),
com_vista as (
  -- RELLENO para los SKUs que NO tienen ni una línea de pedido en el período.
  -- Estrictamente aditivo: si un SKU ya salió arriba, aquí no entra, así que
  -- ningún margen que hoy se muestra cambia de valor.
  --
  -- Por qué hace falta: order_items solo tiene detalle desde el 15/16-jul.
  -- Pedir 120 días en vez de 60 subía la cobertura de margen de 597 SKUs a
  -- 598 — UNO—, porque no hay de dónde. Con este relleno pasa a 881.
  --
  -- Por qué NO sustituye al bloque de arriba: el sale_fee del histórico es
  -- sólido en agregado (14-17%% mensual, tasa de ML creíble) pero ruidoso al
  -- repartirlo por SKU — 4.9%% de sus filas quedan por debajo del 9%%, lo que
  -- da comisiones por unidad demasiado bajas. En TEC-0664-BLN daba $4.25/u
  -- contra los $11.89/u de los pedidos, y ahí el pedido tiene la razón. Se usa
  -- solo donde la alternativa es no mostrar nada.
  select s.sku, s.canal, s.cuenta,
         sum(s.units_sold)::int as uds,
         sum(s.sale_fee) / nullif(sum(s.units_sold), 0) as comision_unit,
         'historico' as origen
  from channel.sales_daily_completa s
  where s.date > current_date - %(dias)s::int
    and s.sku is not null
    and s.units_sold > 0
    and coalesce(s.sale_fee, 0) > 0
    and (%(cuenta)s::text is null or s.cuenta = %(cuenta)s)
    and s.sku not in (select c.sku from com_canal c)
  group by 1, 2, 3
),
com_todo as (
  select * from com_canal
  union all
  select * from com_vista
),
com as (
  -- El promedio del SKU se rearma PONDERANDO por unidades: da exactamente lo
  -- mismo que promediar las líneas de golpe (suma de comisiones ÷ suma de
  -- piezas), pero de paso deja el desglose por canal que lee el panel flotante.
  select sku,
         sum(comision_unit * uds) / nullif(sum(uds), 0) as comision_unit,
         jsonb_agg(jsonb_build_object(
             'canal', canal, 'cuenta', cuenta, 'uds', uds,
             'comision_unit', round(comision_unit, 2),
             -- El panel flotante puede decir de dónde salió cada renglón:
             -- 'pedidos' es el cobro orden por orden; 'historico' es el
             -- agregado diario, que rellena lo anterior al 15-jul.
             'origen', origen)
           order by uds desc) as comisiones
  from com_todo
  group by sku
),
filas as (
  select l.sku, l.cuentas, l.titulo, coalesce(t.tam, 'S/C') as tam,
         -- Medidas y publicaciones: alimentan las dos tarjetas de la columna
         -- Producto (el chip de tamaño y los puntos de cuenta).
         t.largo, t.ancho, t.alto, t.peso,
         -- Para ordenar por TAMAÑO. No se ordena por la letra: alfabéticamente
         -- L va antes que M y que S, que es justo al revés del tamaño. Se
         -- ordena por el lado que DECIDE la letra, y de paso ordena bien
         -- dentro de una misma categoría.
         greatest(coalesce(t.largo,0), coalesce(t.ancho,0),
                  coalesce(t.alto,0))                as lado_mayor,
         l.publicaciones,
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
         coalesce(v.uds_ml, 0)::int     as uds_ml,
         l.pubs_ml,
         coalesce(v.venta, 0)           as venta,
         coalesce(l.stock_full, 0)::int as stock_full,
         coalesce(d.stock_drop, l.stock_propio, 0)::int as stock_propio,
         (current_date - u.ultima_venta)::int as edad_sin_venta_d,
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
         -- EL FLETE DE IMPORTACIÓN YA VA DENTRO DE ESE COSTO (verificado el
         -- 14-ago: costo_total = costo_producto + costo_cbm en 15,429 de 15,429
         -- filas con total, y costo_unitario igual en 4,376 de 4,376). No se
         -- suma otra vez — se expone SOLO para poder avisar cuando falta.
         --
         -- Y falta en 401 SKUs del catálogo. Importa porque el flete pesa
         -- 31.1%% del costo en promedio: un SKU sin él parece 31%% más barato de
         -- lo que es, y su margen sale optimista sin que nada lo diga. Caso
         -- MAN-0495-BLN: producto $741, flete $0, y SÍ tiene medidas
         -- (90x36x45) — no es que no se pueda calcular, es que no se capturó.
         coalesce(cv.costo_cbm, cf.costo_cbm)        as costo_flete,
         -- PRECIO DE REFERENCIA de todo el bloque de margen: el REALIZADO
         -- cuando hubo ventas (ingreso ÷ uds, ya ponderado entre cuentas) y el
         -- publicado cuando no las hubo — el mismo criterio que usa la celda de
         -- Precio de venta, para que ordenar por margen y leerlo no se
         -- contradigan. Sale como columna para que el recálculo en Python (tras
         -- resolver el envío real) parta EXACTAMENTE del mismo número.
         coalesce(v.venta / nullif(v.uds, 0), l.precio)    as precio_ref,
         -- COSTO FINAL = costo base + los cobros de Meli, igual que el popup de
         -- "Productos más vendidos" (Eduardo, 10-ago). Aquí el envío es el
         -- ESTIMADO de costing; _envio_real_en_filas lo sustituye por el cobro
         -- REAL del embarque en las filas de la página y REHACE costo_final,
         -- margen_neto_pct y ganancia_periodo con él.
         co.comision_unit,
         co.comisiones,
         cf.costo_fee_envio                                as envio_estimado,
         case when coalesce(cv.costo_total, cf.costo_unitario) is not null
               and co.comision_unit is not null
              then round(coalesce(cv.costo_total, cf.costo_unitario)
                         + co.comision_unit
                         + coalesce(cf.costo_fee_envio, 0), 2)
              end as costo_final,
         case when coalesce(cv.costo_total, cf.costo_unitario) is not null
               and co.comision_unit is not null
               and coalesce(v.venta / nullif(v.uds, 0), l.precio) > 0
              then round((coalesce(v.venta / nullif(v.uds, 0), l.precio)
                          - coalesce(cv.costo_total, cf.costo_unitario)
                          - co.comision_unit
                          - coalesce(cf.costo_fee_envio, 0))
                         / coalesce(v.venta / nullif(v.uds, 0), l.precio) * 100, 1)
              end as margen_neto_pct,
         -- GANANCIA DEL PERÍODO: lo que dejaron las piezas que SÍ se vendieron
         -- (ganancia por pieza × unidades). Sin ventas no hay ganancia que
         -- contar — queda NULL, no cero: cero diría "vendió y no dejó nada".
         case when coalesce(cv.costo_total, cf.costo_unitario) is not null
               and co.comision_unit is not null
               and coalesce(v.uds, 0) > 0
              then round((v.venta / v.uds
                          - coalesce(cv.costo_total, cf.costo_unitario)
                          - co.comision_unit
                          - coalesce(cf.costo_fee_envio, 0)) * v.uds, 2)
              end as ganancia_periodo,
         case when coalesce(v.u7_prev, 0) > 0
              then round((coalesce(v.u7,0) - v.u7_prev) / v.u7_prev::numeric * 100, 0)
              when coalesce(v.u7, 0) > 0 then 100
              end as crec_7d_pct
  from l
  left join v   on v.sku = l.sku
  left join ult u on u.sku = l.sku
  left join dr d  on d.sku = l.sku
  left join tam t on t.sku = l.sku
  left join com co on co.sku = l.sku
  left join costing.costos_finales cf
         on cf.sku = l.sku and cf.canal = 'mercado_libre'
  left join costing.costos_validados cv on cv.sku = l.sku
)
""")


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
_SQL_ESTRELLAS = _mx("""
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
""")


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
        filas = await _fetch_all(_SQL_ESTRELLAS, {"cuenta": cuenta})
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
        sin_sku = await _fetch_one(
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
        kpis = await _fetch_one(
            _BASE + """
            select count(*)::int                                   as productos,
                   count(*) filter (where estado = 'activa')::int  as activos,
                   count(*) filter (where estado = 'activa'
                                     and tiene_full_agg)::int      as activos_full,
                   coalesce(sum(stock_full), 0)::bigint            as stock_full,
                   coalesce(sum(stock_propio), 0)::bigint          as stock_propio,
                   count(*) filter (where situacion_chip = 'activa')::int as listadas_activas,
                   count(*) filter (where situacion_chip = 'activa'
                                     and stock_full = 0
                                     and stock_propio = 0)::int    as activas_sin_stock
            from (select f.*, (tipo in ('full','mixto')) as tiene_full_agg
                  from filas f) x""", p)
        skus = await _fetch_one(
            """select (select count(*)::int from core.products)          as skus_catalogo,
                      (select count(distinct sku)::int from channel.listings
                        where canal in ('mercado_libre','amazon'))       as skus_listados""")
        cuentas = await _fetch_all(
            """select a.legacy_code as cuenta, count(*)::int as listings
               from channel.listings l join core.accounts a on a.id = l.account_id
               where l.canal in ('mercado_libre','amazon')
               group by 1 order by 1""")
        serie = await _fetch_all(
            _mx("""select date, sum(units_sold)::int as unidades,
                      round(sum(revenue), 2) as venta
               from channel.sales_daily_completa
               where date > current_date - %(dias)s::int
                 and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
               group by 1 order by 1"""), p)
        # UDS/$VENTA del período se derivan de la MISMA serie que pinta la
        # gráfica — un solo dato mostrado dos veces, no dos queries que
        # "deberían" coincidir. Antes salían de `filas` (solo SKUs con
        # publicación viva) y perdían la venta de publicaciones cerradas:
        # 818 uds / $326k en la ventana de 7 días del 2-ago (Eduardo detectó
        # el KPI en 2,564 con la gráfica sumando 3,243).
        kpis["uds_periodo"] = sum(int(s["unidades"]) for s in serie)
        kpis["venta_periodo"] = round(sum(float(s["venta"]) for s in serie), 2)
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
        filas = await _fetch_all(
            _mx("""select date, sum(units_sold)::int as uds,
                      round(sum(revenue), 2) as venta
               from channel.sales_daily_completa
               where sku = %(sku)s::citext
                 and date > current_date - %(dias)s::int
                 and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
               group by 1 order by 1"""), p)
        por_cuenta = await _fetch_all(
            _mx("""select cuenta, sum(units_sold)::int as uds,
                      round(sum(revenue), 2) as venta
               from channel.sales_daily_completa
               where sku = %(sku)s::citext
                 and date > current_date - %(dias)s::int
                 and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
               group by 1 order by 1"""), p)
        ultima_global = await _fetch_scalar(
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


# Resumen por canal de UN SKU: precio REALIZADO (lo que de verdad se cobró,
# ingreso/unidades de los pedidos) — no el precio de lista de la publicación,
# que con las promos de ML puede estar ~36%% arriba de lo que entra. El costo
# sigue el contrato único de José: costos_validados.costo_total primero.
_SQL_CANALES = _mx("""
with vta as (
  select s.canal, s.cuenta,
         sum(s.units_sold)::int                                   as uds,
         round(sum(s.revenue), 2)                                 as ingreso,
         round(sum(s.revenue) / nullif(sum(s.units_sold), 0), 2)  as precio_prom,
         max(s.date)::text                                        as ultima_venta
    from channel.sales_daily_completa s
   where s.sku = %(sku)s::citext
     and s.date > current_date - %(dias)s::int
   group by 1, 2
),
com as (
  -- Comisión REAL cobrada POR CANAL Y CUENTA en el período: aquí es donde se
  -- ve que el mismo producto deja distinto según dónde se venda. Solo líneas
  -- con comisión > 0 (Amazon la registra en cero hasta tener Finances API).
  select o.canal, o.cuenta,
         sum(coalesce(i.comision, 0))  as comision,
         sum(i.cantidad)::int          as uds_com
    from channel.order_items i
    join channel.orders o using (canal, cuenta, external_order_id)
   where i.sku = %(sku)s::citext
     and (o.creado_at at time zone 'America/Mexico_City')::date
         > current_date - %(dias)s::int
     and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
     and coalesce(i.comision, 0) > 0
   group by 1, 2
),
costo as (
  select coalesce(
           (select cv.costo_total from costing.costos_validados cv
             where cv.sku = %(sku)s::citext),
           (select cf.costo_unitario from costing.costos_finales cf
             where cf.sku = %(sku)s::citext and cf.canal = 'mercado_libre')
         ) as costo,
         (select cf.costo_fee_envio from costing.costos_finales cf
           where cf.sku = %(sku)s::citext and cf.canal = 'mercado_libre') as envio
)
select v.canal, v.cuenta, v.uds, v.ingreso, v.precio_prom, v.ultima_venta,
       c.costo,
       case when c.costo is not null
            then round(v.ingreso - v.uds * c.costo, 2) end as ganancia,
       case when v.precio_prom > 0 and c.costo is not null
            then round((v.precio_prom - c.costo) / v.precio_prom * 100, 1)
            end as margen_pct,
       -- y lo mismo ya con los cobros del canal encima
       round(m.comision / nullif(m.uds_com, 0), 2)         as comision_unit,
       c.envio                                             as envio_unit,
       case when c.costo is not null and m.comision is not null
            then round(c.costo + m.comision / nullif(m.uds_com, 0)
                       + coalesce(c.envio, 0), 2)
            end as costo_final,
       case when c.costo is not null and m.comision is not null
            then round(v.ingreso - v.uds * (c.costo + m.comision / nullif(m.uds_com, 0)
                                            + coalesce(c.envio, 0)), 2)
            end as ganancia_neta,
       case when v.precio_prom > 0 and c.costo is not null and m.comision is not null
            then round((v.precio_prom - (c.costo + m.comision / nullif(m.uds_com, 0)
                                         + coalesce(c.envio, 0)))
                       / v.precio_prom * 100, 1)
            end as margen_neto_pct
  from vta v
  cross join costo c
  left join com m on m.canal = v.canal and m.cuenta = v.cuenta
 order by v.uds desc
""")

# Línea de tiempo de precios: channel.listing_history registra cada cambio que
# el sync observa (desde el 17-jul-2026 — no hay historia anterior). Es la
# trazabilidad de temporadas en crudo: qué precio había y cuándo cambió.
_SQL_CAMBIOS_PRECIO = """
select h.canal,
       case when a.legacy_code in ('AMAZON','GENERAL') then '' else a.legacy_code end as cuenta,
       h.valor_anterior, h.valor_nuevo,
       h.changed_at::date::text as fecha
  from channel.listing_history h
  left join core.accounts a on a.id = h.account_id
 where h.sku = %(sku)s::citext and h.campo = 'price'
 order by h.changed_at desc
 limit 60
"""


# ── Márgenes con COSTO FINAL (requerimientos Eduardo, 4-ago) ────────────────
#
# Definiciones del negocio:
#   Costo Base  = producto + flete de importación (costos_validados.costo_total,
#                 el contrato único de José; fallback costos_finales.costo_unitario)
#   Costo Final = Costo Base + cobros de Meli por la venta:
#                 · comisión REAL por línea (channel.order_items.comision — es
#                   TOTAL de línea, verificado: 14.5-19.5%% del importe)
#                 · envío ESTIMADO por peso/dims (costos_finales.costo_fee_envio,
#                   por unidad). FASE 2 pendiente: envío real del shipment.
#   Margen %    = (ingreso − costo_final) / ingreso  ← margen sobre venta, como
#                 el resto del panel (la alternativa ganancia/costo es cambiar
#                 una línea si negocio la prefiere).
# Limitaciones declaradas: cargos FULL (facturación mensual, no por pedido)
# fuera; Amazon con comisión 0 hasta Finances API; filas sin costo van vacías.
_SQL_MARGEN_LINEAS = _mx("""
select (o.creado_at at time zone 'America/Mexico_City')::date::text as fecha,
       o.canal, o.cuenta, o.external_order_id as pedido,
       i.item_id,                          -- para casar el envío real con la hoja Categorias
       i.sku::text as sku, i.titulo,
       i.cantidad::int as cantidad,
       i.precio_unitario,
       round(i.precio_unitario * i.cantidad, 2)          as ingreso,
       i.comision                                        as comision_ml,
       case when cf.costo_fee_envio is not null
            then round(cf.costo_fee_envio * i.cantidad, 2) end as envio_estimado,
       coalesce(cv.costo_total, cf.costo_unitario)       as costo_base_unit,
       case when coalesce(cv.costo_total, cf.costo_unitario) is not null
            then round(coalesce(cv.costo_total, cf.costo_unitario) * i.cantidad, 2)
            end                                          as costo_base,
       case when coalesce(cv.costo_total, cf.costo_unitario) is not null
            then round(coalesce(cv.costo_total, cf.costo_unitario) * i.cantidad
                       + coalesce(i.comision, 0)
                       + coalesce(cf.costo_fee_envio, 0) * i.cantidad, 2)
            end                                          as costo_final,
       i.es_fulfillment                                  as full,
       coalesce(o.estado_canal, '')                      as estado,
       -- Insumos del DIAGNÓSTICO (columna del Excel). No se pintan: alimentan
       -- reporte_categorias_xlsx.diagnosticar(), que explica POR QUÉ una celda
       -- va vacía o por qué el costo no es de fiar. Se distingue a propósito
       -- `cf.costo_fee_envio` crudo de su versión multiplicada: NULL ("no hay
       -- dato") y 0 ("el envío costó cero") son cosas distintas, y el reporte
       -- las estaba pintando iguales.
       cf.costo_fee_envio                                as fee_envio_unit,
       (cv.sku is not null)                              as tiene_validado,
       (cf.sku is not null)                              as tiene_final,
       cv.piezas_por_caja, cv.cajas, cv.costo_producto,
       cv.peso, cv.largo, cv.alto, cv.ancho
  from channel.order_items i
  join channel.orders o using (canal, cuenta, external_order_id)
  left join costing.costos_validados cv on cv.sku = i.sku
  left join costing.costos_finales  cf on cf.sku = i.sku and cf.canal = 'mercado_libre'
 where (o.creado_at at time zone 'America/Mexico_City')::date
       between %(desde)s::date and %(hasta)s::date
   -- MISMO universo que las otras dos hojas (Eduardo, 5-ago). Antes esto no
   -- filtraba cancelados ni exigía SKU, y al volverse hoja del mismo libro el
   -- archivo se contradecía: el detalle sumaba $2.32M contra $2.04M del
   -- resumen. Un pedido cancelado no es una venta — es su reverso; y una línea
   -- sin SKU no tiene costo con el cual sacarle margen. La columna `estado`
   -- sigue sirviendo: quedan paid, Shipped, partially_refunded, Pending.
   and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
   and i.sku is not null
   and (%(cuenta)s::text is null or o.cuenta = %(cuenta)s)
   and (%(canal)s::text  is null or o.canal  = %(canal)s)
 order by o.creado_at desc, i.linea
""")


# El CSV suelto de margenes se RETIRA (Eduardo, 5-ago): era el mismo dato
# que ahora viaja como hoja "Ventas" del Excel, con otro rango de fechas y
# otro boton. _SQL_MARGEN_LINEAS sigue vivo — lo consume el Excel.


_SQL_MARGEN_TOP = _mx("""
with lineas as (
  select i.sku, max(i.titulo) as titulo,
         sum(i.cantidad)::int as uds,
         sum(i.precio_unitario * i.cantidad) as ingreso,
         sum(coalesce(i.comision, 0)) as comision
    from channel.order_items i
    join channel.orders o using (canal, cuenta, external_order_id)
   where (o.creado_at at time zone 'America/Mexico_City')::date > current_date - %(dias)s::int
     and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
     and i.sku is not null
   group by i.sku
)
select l.sku::text as sku, l.titulo, l.uds,
       round(l.ingreso, 2)                              as ingreso,
       round(l.ingreso / nullif(l.uds, 0), 2)           as precio_prom,
       coalesce(cv.costo_total, cf.costo_unitario)      as costo_base,
       round(l.comision / nullif(l.uds, 0), 2)          as comision_prom,
       cf.costo_fee_envio                               as envio_prom,
       case when coalesce(cv.costo_total, cf.costo_unitario) is not null
            then round(coalesce(cv.costo_total, cf.costo_unitario)
                       + l.comision / nullif(l.uds, 0)
                       + coalesce(cf.costo_fee_envio, 0), 2)
            end                                         as costo_final
  from lineas l
  left join costing.costos_validados cv on cv.sku = l.sku
  left join costing.costos_finales  cf on cf.sku = l.sku and cf.canal = 'mercado_libre'
 order by l.uds desc
 limit %(limite)s
""")


@router.get("/margenes-top")
async def margenes_top(
    dias: int = Query(30, ge=7, le=180),
    limite: int = Query(10, ge=3, le=50),
) -> dict[str, Any]:
    """Top de SKUs más vendidos con precio promedio realizado, Costo Base,
    cobros de Meli y margen sobre COSTO FINAL (req 1 — tarjeta de Omnicanal)."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    try:
        filas = await _fetch_all(_SQL_MARGEN_TOP, {"dias": dias, "limite": limite})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc
    for f in filas:
        pp, cfin = f.get("precio_prom"), f.get("costo_final")
        if pp and cfin is not None:
            f["ganancia_unit"] = round(float(pp) - float(cfin), 2)
            f["margen_pct"] = round((float(pp) - float(cfin)) / float(pp) * 100, 1)
        else:
            f["ganancia_unit"] = None
            f["margen_pct"] = None
    return {"dias": dias, "items": filas,
            "nota_envio": "envío estimado por peso/dimensiones — el real llega en fase 2"}


# ── MÁRGENES REALES (fase 0) ─────────────────────────────────────────────────
# "Márgenes en Análisis: 10 SKUs más vendidos POR CUENTA, margen sobre el Costo
# Final con TODOS los cobros de Meli" (Eduardo, 6-ago). La diferencia contra
# /margenes-top: el ENVÍO ya no es el estimado de costing (que mentía en las
# dos direcciones — ver services/envio_real.py), es lo que ML cobró de verdad
# por cada embarque, consultado a su API y cacheado en MySQL. La comisión ya
# era real (sale_fee de los pedidos); el precio también (ingreso ÷ unidades).
# Fase 1 (persistir el envío en channel.order_shipments) queda a decisión de
# Eduardo — este endpoint solo cambiaría de dónde lee.

_SQL_MARGEN_REAL_TOP = _mx("""
with est as (
  -- Estado de la publicación del SKU en ESA cuenta (Eduardo, 6-ago: "que se
  -- vean pausadas o si está activa"). Una cuenta puede tener más de una
  -- publicación del mismo SKU: manda la activa si existe. Las cerradas no
  -- cuentan — un listado que ya no existe no describe el estado de hoy.
  --
  -- Aquí también salen los precios de la publicación: `price` es lo que ve el
  -- comprador y `price_base` el de LISTA. Que difieran significa promoción
  -- montada (Malla Sombra: lista $960, venta $355 — el margen malo era
  -- decisión comercial, no misterio). Cero llamadas a ML: ya está en la BD.
  select a.legacy_code as cuenta, l.sku,
         case when bool_or(l.situacion = 'active') then 'activa'
              when bool_or(l.situacion = 'paused') then 'pausada'
              else 'otra' end                                 as estado,
         min(l.price) filter (where l.situacion = 'active')   as precio_activo,
         min(l.price)                                         as precio_cualquiera,
         max(l.price_base)                                    as precio_lista,
         -- Los item_id sirven para pedir las VISITAS: ML las da por
         -- publicación, no por SKU (services/visitas_ml.py).
         array_agg(distinct l.listing_id)
           filter (where l.listing_id is not null)            as listing_ids
    from channel.listings l
    join core.accounts a on a.id = l.account_id
   where l.canal = 'mercado_libre'
     and lower(coalesce(l.situacion, '')) <> 'closed'
   group by 1, 2
),
lineas as (
  select o.cuenta, i.sku, max(i.titulo) as titulo,
         sum(i.cantidad)::int as uds,
         sum(i.precio_unitario * i.cantidad) as ingreso,
         sum(coalesce(i.comision, 0)) as comision,
         sum(i.cantidad) filter (where coalesce(i.comision, 0) > 0)::int as uds_com
    from channel.order_items i
    join channel.orders o using (canal, cuenta, external_order_id)
   where o.canal = 'mercado_libre'
     and (o.creado_at at time zone 'America/Mexico_City')::date > current_date - %(dias)s::int
     and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
     and i.sku is not null
   group by 1, 2
),
top as (
  -- El filtro de estado va ANTES de numerar: pedir "activas" debe dar el top 10
  -- DE LAS ACTIVAS, no las que sobrevivan de un top 10 mixto.
  select l.*, e.estado, e.precio_activo, e.precio_cualquiera, e.precio_lista,
         e.listing_ids,
         row_number() over (partition by l.cuenta
                            order by l.uds desc, l.ingreso desc) as rn
    from lineas l
    left join est e on e.cuenta = l.cuenta and e.sku = l.sku
   where %(estado)s::text is null or coalesce(e.estado, 'otra') = %(estado)s
),
g as (
  -- RANKING POR SKU, SUMANDO LAS CUENTAS (Eduardo, 14-ago). La lista "General"
  -- se armaba en el navegador fundiendo los dos top-10 por cuenta, y eso tiene
  -- dos defectos: repetía el SKU una vez por cuenta (TEC-2162-NEG salía en el
  -- 2º y el 4º lugar) y, peor, PODÍA PERDER productos — un SKU con 200 piezas
  -- en cada cuenta no entra a ningún top-10 por separado y aun así es de los
  -- más vendidos sumado. Un top que sale de fundir dos listas ya recortadas no
  -- es el top. Se ordena aquí, sobre el total del SKU.
  select sku,
         row_number() over (order by sum(uds) desc, sum(ingreso) desc) as rn_g
    from top
   group by sku
)
-- Se traen las filas por cuenta de los DOS conjuntos: las del top de cada
-- cuenta (para sus pestañas) y todas las del top general (para poder fundirlas
-- con su envío y sus visitas, que se miden por publicación y por cuenta).
select t.cuenta, t.sku::text as sku, t.titulo, t.uds,
       round(t.ingreso, 2)                         as ingreso,
       round(t.comision / nullif(t.uds_com, 0), 2) as comision_unit,
       -- Crudos ADEMÁS del promedio: fundir dos cuentas exige volver a
       -- ponderar, y promediar promedios da un número que no es de nadie.
       round(t.comision, 2)                        as comision_total,
       t.uds_com::int                              as uds_com,
       coalesce(t.estado, 'otra')                  as estado,
       coalesce(t.precio_activo, t.precio_cualquiera) as precio_pub,
       t.precio_lista,
       t.listing_ids,
       coalesce(cv.costo_total, cf.costo_unitario) as costo_base,
       -- El flete de importación YA está sumado dentro de `costo_base` (ver la
       -- nota de `_BASE`): viaja aparte solo para avisar cuando vale 0.
       coalesce(cv.costo_cbm, cf.costo_cbm)        as costo_flete,
       cf.costo_fee_envio                          as envio_estimado,
       t.rn::int                                   as rn,
       g.rn_g::int                                 as rn_g
  from top t
  join g on g.sku = t.sku
  left join costing.costos_validados cv on cv.sku = t.sku
  left join costing.costos_finales  cf on cf.sku = t.sku and cf.canal = 'mercado_libre'
 where t.rn <= %(limite)s or g.rn_g <= %(limite)s
 order by t.cuenta, t.uds desc
""")

# Líneas de los SKUs del top (para saber qué órdenes consultar y cuántas
# piezas del SKU van en cada una).
_SQL_MARGEN_REAL_LINEAS = _mx("""
select o.cuenta, o.external_order_id, i.sku::text as sku, sum(i.cantidad)::int as uds
  from channel.order_items i
  join channel.orders o using (canal, cuenta, external_order_id)
  join unnest(%(cuentas)s::text[], %(skus)s::text[]) as t(c, s)
    on t.c = o.cuenta and t.s = i.sku::text
 where o.canal = 'mercado_libre'
   and (o.creado_at at time zone 'America/Mexico_City')::date > current_date - %(dias)s::int
   and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
 group by 1, 2, 3
""")

# Piezas TOTALES por orden (cualquier SKU): el cobro de envío es por EMBARQUE,
# así que en un carrito mixto se prorratea por unidades — sin esto, dos SKUs
# del top en el mismo carrito contarían el mismo envío dos veces.
_SQL_MARGEN_REAL_ORDENES = """
select o.cuenta, o.external_order_id, sum(i.cantidad)::int as uds_orden
  from channel.order_items i
  join channel.orders o using (canal, cuenta, external_order_id)
 where o.canal = 'mercado_libre'
   and o.external_order_id = any(%(ids)s::text[])
 group by 1, 2
"""

_ESTADOS_PUB = {"activa", "pausada"}


@router.get("/margenes-reales")
async def margenes_reales(
    dias: int = Query(30, ge=7, le=90),
    limite: int = Query(10, ge=3, le=20),
    presupuesto: int = Query(250, ge=0, le=500),
    estado: str | None = Query(None, description="activa|pausada; omitido = ambas"),
) -> dict[str, Any]:
    """Top de SKUs más vendidos POR CUENTA con margen sobre Costo Final y los
    tres cobros de Meli REALES: comisión (pedidos), envío (API de shipments,
    caché MySQL) y el precio realizado. `estado` filtra por la situación de la
    publicación ANTES de cortar el top. `pendientes` > 0 significa que el caché
    de envíos sigue llenándose — el frontend refresca hasta que llegue a 0."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if estado and estado not in _ESTADOS_PUB:
        raise HTTPException(400, f"estado inválido: {estado}")
    try:
        top = await _fetch_all(_SQL_MARGEN_REAL_TOP,
                            {"dias": dias, "limite": limite, "estado": estado})
        pares_cs = [(f["cuenta"], f["sku"]) for f in top]
        lineas = await _fetch_all(_SQL_MARGEN_REAL_LINEAS, {
            "dias": dias,
            "cuentas": [c for c, _ in pares_cs],
            "skus": [s for _, s in pares_cs]}) if pares_cs else []
        ids = sorted({str(l["external_order_id"]) for l in lineas})
        ordenes = await _fetch_all(_SQL_MARGEN_REAL_ORDENES, {"ids": ids}) if ids else []
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc

    # Envío real: completar el caché (hasta `presupuesto` consultas) y leerlo.
    pares_orden = [(l["cuenta"], str(l["external_order_id"])) for l in lineas]
    pares_orden = sorted(set(pares_orden))
    consultadas = 0
    costos: dict[tuple[str, str], dict[str, Any]] = {}
    if getattr(settings, "mysql_enabled", True) and pares_orden:
        from services import envio_real
        try:
            if presupuesto:
                consultadas = await envio_real.completar(pares_orden, presupuesto)
            # leer() es MySQL síncrono con ~15,000 pares (1.46 s medidos): en la
            # corrutina congela el backend entero mientras dura.
            costos = await asyncio.to_thread(envio_real.leer, pares_orden)
        except Exception as exc:  # noqa: BLE001
            log.warning("envío real no disponible: %s", exc)

    # VISITAS de cada publicación (ML las da por item, no por SKU) para poder
    # sacar la conversión: unidades vendidas ÷ visitas, ambas del MISMO período.
    # Solo Mercado Libre — Amazon no tiene equivalente por esta vía.
    pares_pub = sorted({(f["cuenta"], str(i))
                        for f in top for i in (f["listing_ids"] or [])})
    visitas: dict[str, dict[str, Any]] = {}
    if getattr(settings, "mysql_enabled", True) and pares_pub:
        from services import visitas_ml
        try:
            if presupuesto:
                await visitas_ml.completar(pares_pub, dias)
            visitas = visitas_ml.leer([i for _, i in pares_pub], dias)
        except Exception as exc:  # noqa: BLE001
            log.warning("visitas no disponibles: %s", exc)

    uds_orden = {(o["cuenta"], str(o["external_order_id"])): int(o["uds_orden"] or 0)
                 for o in ordenes}
    envio_acum: dict[tuple[str, str], float] = {}
    uds_cub: dict[tuple[str, str], int] = {}
    uds_sin: dict[tuple[str, str], int] = {}
    for l in lineas:
        ko = (l["cuenta"], str(l["external_order_id"]))
        ks = (l["cuenta"], l["sku"])
        fila = costos.get(ko)
        if fila and fila.get("costo_vendedor") is not None:
            total = uds_orden.get(ko) or int(l["uds"])
            parte = float(fila["costo_vendedor"]) * int(l["uds"]) / max(total, 1)
            envio_acum[ks] = envio_acum.get(ks, 0.0) + parte
            uds_cub[ks] = uds_cub.get(ks, 0) + int(l["uds"])
        else:
            uds_sin[ks] = uds_sin.get(ks, 0) + int(l["uds"])

    # UNA SOLA FUNCIÓN ARMA LAS DOS VISTAS (Eduardo, 14-ago). `grupo` trae las
    # filas por cuenta de un mismo SKU: una sola para las pestañas de cuenta,
    # las dos para la lista General. Si cada vista hiciera su propia aritmética,
    # el mismo SKU acabaría con dos márgenes distintos según dónde se mire.
    #
    # Todo se RE-PONDERA sobre los crudos; nada se promedia de promedios.
    def armar(grupo: list[dict[str, Any]]) -> dict[str, Any]:
        claves = [(g["cuenta"], g["sku"]) for g in grupo]
        uds = sum(int(g["uds"] or 0) for g in grupo)
        ingreso = sum(float(g["ingreso"]) for g in grupo)
        precio = round(ingreso / uds, 2) if uds else None
        cub = sum(uds_cub.get(k, 0) for k in claves)
        sin = sum(uds_sin.get(k, 0) for k in claves)
        envio_u = round(sum(envio_acum.get(k, 0.0) for k in claves) / cub, 2) if cub else None
        # El costo base viene de costing por SKU: es el mismo en las dos cuentas.
        costo = next((float(g["costo_base"]) for g in grupo
                      if g["costo_base"] is not None), None)
        flete = next((float(g["costo_flete"]) for g in grupo
                      if g["costo_flete"] is not None), None)
        # Comisión por unidad = comisión total ÷ unidades QUE TRAEN comisión.
        com_tot = sum(float(g["comision_total"] or 0) for g in grupo)
        com_uds = sum(int(g["uds_com"] or 0) for g in grupo)
        com = round(com_tot / com_uds, 2) if com_uds else None
        # ESTADO ACROSS CUENTAS: si en una está activa, el producto SE PUEDE
        # comprar — eso es lo que describe la etiqueta. Misma regla que usa el
        # CTE `est` dentro de una cuenta, ahora aplicada entre cuentas.
        estados = {g["estado"] for g in grupo}
        est_final = ("activa" if "activa" in estados
                     else "pausada" if "pausada" in estados else "otra")
        # Los precios de la publicación se toman de una cuenta donde esté
        # ACTIVA: el precio de una pausada no es el que ve el comprador.
        viva = [g for g in grupo if g["estado"] == "activa"] or grupo
        fila: dict[str, Any] = {
            "sku": grupo[0]["sku"], "titulo": grupo[0]["titulo"], "uds": uds,
            "ingreso": round(ingreso, 2), "precio_prom": precio,
            "costo_base": costo, "costo_flete": flete, "comision_unit": com,
            "envio_unit": envio_u,
            "envio_estimado": next((float(g["envio_estimado"]) for g in grupo
                                    if g["envio_estimado"] is not None), None),
            "cobertura_envio_pct": round(cub / uds * 100) if uds else 0,
            "uds_sin_envio": sin,
            "estado": est_final,
            "precio_pub": next((float(g["precio_pub"]) for g in viva
                                if g["precio_pub"] is not None), None),
            "precio_lista": next((float(g["precio_lista"]) for g in viva
                                  if g["precio_lista"] is not None), None),
            # En qué cuentas vendió: la lista General ya no lleva una etiqueta
            # por renglón, así que el renglón tiene que decir de dónde sale.
            "cuentas": sorted({g["cuenta"] for g in grupo}),
            # …Y EN CUÁL ESTÁ ACTIVA (Eduardo, 14-ago). El estado resuelto de
            # arriba dice que se puede comprar, pero no DÓNDE: un SKU activo en
            # Sancor y pausado en Bekura se leía igual que uno activo en las
            # dos, y la acción que pide cada caso es distinta.
            "estado_cuenta": {g["cuenta"]: g["estado"] for g in grupo},
        }
        # Visitas: se suman TODAS las publicaciones del SKU en las cuentas del
        # grupo. `dias_datos` es cuántos días trajo ML de verdad — la ventana no
        # siempre viene completa, y presumir 30 días falsearía la conversión.
        ids_pub = [str(i) for g in grupo for i in (g["listing_ids"] or [])]
        listas = [v for v in (visitas.get(i) for i in ids_pub)
                  if v and v.get("visitas") is not None]
        # Todo o nada, igual que en la tabla: con una medición a medias el
        # porcentaje sale falso (ver _visitas_en_filas). Al fundir cuentas la
        # regla se endurece sola — falta UNA publicación de cualquiera y el
        # renglón se queda sin conversión, que es lo correcto.
        if ids_pub and len(listas) == len(ids_pub):
            total_vis = sum(int(v["visitas"]) for v in listas)
            fila["visitas"] = total_vis
            fila["visitas_dias"] = max((int(v["dias_datos"] or 0) for v in listas),
                                       default=None) or None
            fila["cr_pct"] = round(uds / total_vis * 100, 1) if total_vis else None
        else:
            fila["visitas"] = fila["visitas_dias"] = fila["cr_pct"] = None
        if precio and costo is not None and com is not None and envio_u is not None:
            cfinal = round(costo + com + envio_u, 2)
            fila["costo_final"] = cfinal
            fila["ganancia_unit"] = round(precio - cfinal, 2)
            fila["margen_pct"] = round((precio - cfinal) / precio * 100, 1)
            fila["ganancia_total"] = round((precio - cfinal) * uds, 2)
        else:
            fila["costo_final"] = fila["ganancia_unit"] = None
            fila["margen_pct"] = fila["ganancia_total"] = None
        return fila

    # Las pestañas por cuenta: solo el top DE ESA cuenta (`rn`), un SKU por
    # renglón y su estado en esa cuenta.
    cuentas: dict[str, list[dict[str, Any]]] = {}
    for f in top:
        if int(f["rn"]) <= limite:
            cuentas.setdefault(f["cuenta"], []).append(armar([f]))

    # La lista General: un renglón por SKU, con las cuentas fundidas, ordenada
    # por el ranking que ya calculó el SQL sobre el total del SKU.
    por_sku: dict[str, list[dict[str, Any]]] = {}
    for f in top:
        if int(f["rn_g"]) <= limite:
            por_sku.setdefault(f["sku"], []).append(f)
    general = sorted((armar(g) for g in por_sku.values()),
                     key=lambda x: (-x["uds"], -x["ingreso"]))

    # `pendientes` cuenta unidades sin envío real UNA vez por (cuenta, SKU): si
    # se sumara por vista, un SKU que sale en las dos se contaría doble y el
    # frontend refrescaría de más esperando un cero que no llega.
    pendientes_total = sum(uds_sin.values())

    return {
        "dias": dias,
        "estado": estado,
        "general": general,
        "cuentas": [{"cuenta": c, "filas": filas} for c, filas in sorted(cuentas.items())],
        "pendientes": pendientes_total,
        "consultadas": consultadas,
        "nota": "envío = cobro real de ML por embarque, prorrateado por unidad "
                "en carritos mixtos; no incluye cargos de almacenamiento FULL",
    }


@router.get("/canales")
async def resumen_canales(
    sku: str = Query(..., max_length=100),
    dias: int = Query(30, ge=7, le=180),
) -> dict[str, Any]:
    """Resumen por canal de un SKU (modal de Precio/Margen): unidades, ingreso,
    precio promedio REALIZADO, ganancia y margen por canal + promedio global
    ponderado + historial de cambios de precio de la publicación."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    p = {"sku": sku, "dias": dias}
    try:
        canales = await _fetch_all(_SQL_CANALES, p)
        cambios = await _fetch_all(_SQL_CAMBIOS_PRECIO, {"sku": sku})
        uds = sum(int(c["uds"]) for c in canales)
        ingreso = round(sum(float(c["ingreso"]) for c in canales), 2)
        costo = next((float(c["costo"]) for c in canales
                      if c.get("costo") is not None), None)
        precio_prom = round(ingreso / uds, 2) if uds else None
        margen_prom = (round((precio_prom - costo) / precio_prom * 100, 1)
                       if precio_prom and costo is not None else None)
        # COSTO FINAL global: comisión ponderada por lo vendido en cada canal
        # (no el promedio simple — vender 100 en BEKURA y 2 en SANCOR no son
        # dos comisiones que pesen igual). El envío es uno por SKU.
        con_com = [c for c in canales if c.get("comision_unit") is not None]
        uds_com = sum(int(c["uds"]) for c in con_com)
        comision_unit = (round(sum(float(c["comision_unit"]) * int(c["uds"])
                                   for c in con_com) / uds_com, 2)
                         if uds_com else None)
        envio_unit = next((float(c["envio_unit"]) for c in canales
                           if c.get("envio_unit") is not None), None)
        costo_final = (round(costo + comision_unit + (envio_unit or 0), 2)
                       if costo is not None and comision_unit is not None else None)
        margen_neto = (round((precio_prom - costo_final) / precio_prom * 100, 1)
                       if precio_prom and costo_final is not None else None)
        return {
            "sku": sku, "dias": dias, "canales": canales,
            "global": {"uds": uds, "ingreso": ingreso,
                       "precio_prom": precio_prom, "costo": costo,
                       "margen_prom": margen_prom,
                       "ganancia": (round(ingreso - uds * costo, 2)
                                    if costo is not None else None),
                       "comision_unit": comision_unit, "envio_unit": envio_unit,
                       "costo_final": costo_final, "margen_neto": margen_neto,
                       "ganancia_neta": (round(ingreso - uds * costo_final, 2)
                                         if costo_final is not None else None)},
            "cambios_precio": cambios,
            # la historia de precios existe desde esta fecha; antes no hay registro
            "historia_desde": "2026-07-17",
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("resumen canales falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc


async def _pesos_en_filas(items: list[dict[str, Any]], presupuesto: int) -> None:
    """
    Marca los SKUs que son DOS PRODUCTOS bajo una sola clave.

    La señal es el peso que midió la bodega de ML en cada cuenta: si difieren,
    no es el mismo objeto, y entonces el costo, el inventario y el margen que
    comparten no le corresponden a uno de los dos. Ver services/ficha_ml.py —
    el título NO sirve para esto (el mismo producto se describe de dos formas).
    """
    if not getattr(settings, "mysql_enabled", True):
        return
    pares: set[tuple[str, str]] = set()
    for f in items:
        for p in (f.get("pubs_ml") or []):
            if p.get("cuenta") and p.get("item"):
                pares.add((p["cuenta"], str(p["item"])))
    if not pares:
        return
    try:
        from services import ficha_ml
        fichas = ficha_ml.leer([i for _, i in pares])
        # La consulta a ML va EN SEGUNDO PLANO: esto es una marca, no una
        # columna que se lea a cada rato, y esperarla le sumaba ~5 s a cada
        # página nueva. Se responde con lo que ya está en caché y la marca
        # aparece en el siguiente refresco (la vista se recarga sola cada 60 s).
        # El TTL es de una semana, así que converge y no se vuelve a pagar.
        if presupuesto and len(fichas) < len(pares):
            import asyncio as _asyncio
            _asyncio.create_task(ficha_ml.completar(sorted(pares)))
    except Exception as exc:  # noqa: BLE001
        log.warning("fichas de ML no disponibles: %s", exc)
        return
    for f in items:
        propias = [fichas.get(str(p.get("item")))
                   for p in (f.get("pubs_ml") or []) if p.get("item")]
        try:
            f["peso_divergente"] = ficha_ml.divergencia(propias)
        except Exception:  # noqa: BLE001
            f["peso_divergente"] = None


async def _visitas_en_filas(items: list[dict[str, Any]], dias: int,
                            presupuesto: int) -> None:
    """
    Agrega `visitas`, `visitas_dias` y `cr_pct` a las filas de la tabla.

    Las visitas las da ML por PUBLICACIÓN (services/visitas_ml.py), así que se
    suman las publicaciones de ML del SKU. La conversión se calcula con
    `uds_ml`, NO con las unidades totales: si un SKU vende también en Amazon,
    dividir sus ventas completas entre visitas de solo Mercado Libre daría un
    CR% inflado que no significa nada.
    """
    if not getattr(settings, "mysql_enabled", True):
        return
    # EN ORDEN DE FILA, no en un set: ML no acepta multiget para visitas (una
    # llamada por publicación), así que una página fría costaba ~18 s. Se mide
    # bloqueando solo lo de ARRIBA —que es lo que el ojo lee primero— y el resto
    # se completa en segundo plano para el refresco siguiente.
    pares: list[tuple[str, str]] = []
    vistos: set[tuple[str, str]] = set()
    for f in items:
        for p in (f.get("pubs_ml") or []):
            if p.get("cuenta") and p.get("item"):
                k = (p["cuenta"], str(p["item"]))
                if k not in vistos:
                    vistos.add(k); pares.append(k)
    if not pares:
        return
    try:
        from services import visitas_ml
        if presupuesto:
            await visitas_ml.completar(pares, dias, presupuesto)
            if len(pares) > presupuesto:
                import asyncio as _asyncio
                _asyncio.create_task(visitas_ml.completar(pares, dias, len(pares)))
        medidas = visitas_ml.leer([i for _, i in pares], dias)
    except Exception as exc:  # noqa: BLE001
        log.warning("visitas no disponibles en la tabla: %s", exc)
        return
    for f in items:
        pubs = [p for p in (f.get("pubs_ml") or []) if p.get("item")]
        vis = [medidas.get(str(p["item"])) for p in pubs]
        listas = [v for v in vis if v and v.get("visitas") is not None]
        # TODO O NADA. Un SKU publicado en las dos cuentas tiene dos
        # mediciones; si solo llegó una, sumar esa mitad y dividirla entre las
        # unidades COMPLETAS da un porcentaje absurdo — MUE-0163-TEL llegó a
        # mostrar "209 visitas · 378.5%" teniendo 13,122 visitas reales. Hasta
        # que estén todas, la celda dice "—" y la siguiente carga la completa.
        if pubs and len(listas) == len(pubs):
            total = sum(int(v["visitas"]) for v in listas)
            uds_ml = int(f.get("uds_ml") or 0)
            f["visitas"] = total
            f["visitas_dias"] = max((int(v["dias_datos"] or 0) for v in listas),
                                    default=None) or None
            f["cr_pct"] = round(uds_ml / total * 100, 1) if total else None
        else:
            f["visitas"] = f["visitas_dias"] = f["cr_pct"] = None


# Líneas de pedido de MERCADO LIBRE de los SKUs de la página: con ellas se sabe
# qué embarques hay que consultar y cuántas piezas del SKU viajaron en cada uno.
# Amazon queda fuera (no hay API de embarque equivalente conectada).
_SQL_TABLA_ENVIO_LINEAS = _mx("""
select o.cuenta, o.external_order_id, i.sku::text as sku,
       sum(i.cantidad)::int as uds
  from channel.order_items i
  join channel.orders o using (canal, cuenta, external_order_id)
 where o.canal = 'mercado_libre'
   and i.sku = any(%(skus)s::citext[])
   and (o.creado_at at time zone 'America/Mexico_City')::date
       > current_date - %(dias)s::int
   and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
   and (%(cuenta)s::text is null or o.cuenta = %(cuenta)s)
 group by 1, 2, 3
""")


def _rehacer_costos(items: list[dict[str, Any]]) -> None:
    """
    Rehace COSTO FINAL, MARGEN y GANANCIA con el envío que quedó en la fila.

    Se corre SIEMPRE, aunque el envío siga siendo el estimado: así el número
    que se pinta sale de un solo lugar en vez de "a veces el del SQL, a veces
    el de Python". Las reglas de qué se puede calcular son las del popup de
    márgenes: sin costo base o sin comisión no hay costo final, y sin precio no
    hay margen — vacío antes que un número inventado.
    """
    for f in items:
        costo = f.get("costo")
        com = f.get("comision_unit")
        envio = f.get("envio_unit")
        precio = f.get("precio_ref")
        uds = int(f.get("uds") or 0)
        # Un costo en CERO no es un costo barato, es un costo sin capturar: da
        # un margen cercano al 100% que se lee como el mejor producto del
        # catálogo. Se trata igual que la ausencia (regla que ya tenía la celda
        # de margen antes de este cambio).
        if costo is None or float(costo) <= 0 or com is None:
            f["costo_final"] = f["margen_neto_pct"] = f["ganancia_periodo"] = None
            f["ganancia_unit"] = None
            continue
        final = round(float(costo) + float(com) + float(envio or 0), 2)
        f["costo_final"] = final
        if precio is not None and float(precio) > 0:
            p = float(precio)
            f["margen_neto_pct"] = round((p - final) / p * 100, 1)
            f["ganancia_unit"] = round(p - final, 2)
            # La ganancia del período solo existe si hubo venta EN el período:
            # el precio publicado sirve para juzgar un margen, no para inventar
            # una ganancia de piezas que nadie compró.
            f["ganancia_periodo"] = round((p - final) * uds, 2) if uds else None
        else:
            f["margen_neto_pct"] = f["ganancia_unit"] = None
            f["ganancia_periodo"] = None


def pares_de(lineas: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(cuenta, pedido) únicos y ordenados — la llave del caché de envío real."""
    return sorted({(l["cuenta"], str(l["external_order_id"])) for l in lineas})


async def _envio_real_en_filas(items: list[dict[str, Any]], dias: int,
                               cuenta: str | None, presupuesto: int) -> int:
    """
    Sustituye el envío ESTIMADO por el que Mercado Libre cobró DE VERDAD.

    Es el mismo dato del popup "Productos más vendidos", traído a la tabla: el
    estimado de `costing` miente en las dos direcciones (ver
    services/envio_real.py — $349 estimados contra $88 reales en Malla Sombra, y
    141 SKUs con flete en $0), así que un margen calculado con él no es el
    margen. El cobro es por EMBARQUE: en un carrito con varios productos se
    reparte entre las líneas en proporción a las unidades.

    Presupuesto acotado a propósito: cada pedido nuevo cuesta dos llamadas a ML
    y se cachea para siempre, así que la tabla se completa sola a lo largo de
    los refrescos (la página se recarga cada 60 s) en vez de pagar el llenado
    entero en la primera carga. Mientras tanto la fila muestra el ESTIMADO y lo
    dice — `envio_origen`.

    Devuelve cuántas PIEZAS de la página siguen sin envío real (0 = la página
    ya está completa).
    """
    for f in items:
        est = f.get("envio_estimado")
        f["envio_unit"] = None if est is None else float(est)
        f["envio_origen"] = "estimado" if est is not None else "sin dato"
        f["cobertura_envio_pct"] = 0
        f["envios"] = []
    try:
        lineas = await _fetch_all(_SQL_TABLA_ENVIO_LINEAS, {
            "skus": [str(i["sku"]) for i in items], "dias": dias, "cuenta": cuenta})
        ids = sorted({str(l["external_order_id"]) for l in lineas})
        ordenes = await _fetch_all(_SQL_MARGEN_REAL_ORDENES, {"ids": ids}) if ids else []
    except Exception as exc:  # noqa: BLE001
        log.warning("líneas de envío no disponibles en la tabla: %s", exc)
        _rehacer_costos(items)
        return 0
    if not lineas:
        _rehacer_costos(items)
        return 0

    # Las LÍNEAS salen de kubera y el desglose por cuenta se arma con ellas, así
    # que se calcula SIEMPRE — también sin MySQL (staging solo-Supabase), donde
    # no hay caché ni tokens de ML. Ahí el panel sigue diciendo qué cuenta vendió
    # y cuántas piezas, y declara que el cobro real está pendiente; solo el
    # número real es lo que falta, no el contexto.
    costos: dict[tuple[str, str], dict[str, Any]] = {}
    if getattr(settings, "mysql_enabled", True):
        try:
            from services import envio_real
            if presupuesto:
                await envio_real.completar(pares_de(lineas), presupuesto)
            costos = await asyncio.to_thread(envio_real.leer, pares_de(lineas))
        except Exception as exc:  # noqa: BLE001
            log.warning("envío real no disponible en la tabla: %s", exc)

    uds_orden = {(o["cuenta"], str(o["external_order_id"])): int(o["uds_orden"] or 0)
                 for o in ordenes}
    # Por SKU y, dentro, por CUENTA: el mismo producto puede salir de dos
    # bodegas con tarifas distintas, y ese es justo el desglose que se pide al
    # pasar el cursor.
    por_sku: dict[str, dict[str, dict[str, float]]] = {}
    for l in lineas:
        sku, cta = str(l["sku"]).lower(), str(l["cuenta"])
        uds = int(l["uds"] or 0)
        d = por_sku.setdefault(sku, {}).setdefault(cta, {"uds": 0, "cub": 0, "acum": 0.0})
        d["uds"] += uds
        clave = (l["cuenta"], str(l["external_order_id"]))
        fila = costos.get(clave)
        # `costo_vendedor = 0` es una respuesta legítima de ML (el comprador
        # pagó el envío); NULL es que no se pudo consultar. De ahí el `is None`.
        if fila and fila.get("costo_vendedor") is not None:
            del_pedido = uds_orden.get(clave) or uds
            d["acum"] += float(fila["costo_vendedor"]) * uds / max(del_pedido, 1)
            d["cub"] += uds

    pendientes = 0
    for f in items:
        cuentas = por_sku.get(str(f["sku"]).lower())
        if not cuentas:
            continue
        f["envios"] = [
            {"cuenta": c,
             "uds": int(d["uds"]),
             "cubiertas": int(d["cub"]),
             "envio_unit": round(d["acum"] / d["cub"], 2) if d["cub"] else None}
            for c, d in sorted(cuentas.items(), key=lambda kv: -kv[1]["uds"])]
        cub = sum(int(d["cub"]) for d in cuentas.values())
        tot = sum(int(d["uds"]) for d in cuentas.values())
        acum = sum(d["acum"] for d in cuentas.values())
        pendientes += max(0, tot - cub)
        if not cub:
            continue
        f["envio_unit"] = round(acum / cub, 2)
        f["envio_origen"] = "real"
        f["cobertura_envio_pct"] = round(cub / tot * 100) if tot else 0
    _rehacer_costos(items)
    return pendientes


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
    # Cuántas publicaciones se miden ESPERANDO la respuesta. 40 ≈ las 20 filas
    # de arriba, que es lo que se lee primero, y mantiene la carga en ~2 s; el
    # resto de la página se completa en segundo plano y aparece al refrescar.
    visitas: int = Query(40, ge=0, le=500,
                         description="cuántas publicaciones medir por carga (0 = solo caché)"),
    # Cuántos EMBARQUES se consultan a ML por carga para el envío real. Cada uno
    # cuesta dos llamadas y se cachea para siempre, así que el llenado se reparte
    # entre refrescos en vez de pagarse entero en la primera carga. Sin valor
    # explícito manda `TABLA_ENVIO_REAL_PRESUPUESTO` de Railway: ponerla en 0
    # apaga las llamadas a ML sin deploy (la tabla sigue con lo ya cacheado).
    envios: int | None = Query(None, ge=0, le=500,
                               description="cuántos embarques consultar por carga (0 = solo caché)"),
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
    # `tam` acepta VARIOS separados por coma: se valida cada uno, no la cadena
    # entera. Validar la cadena rechazaba "S,M" con un 400 y dejaba la tabla en
    # blanco sin explicar por qué.
    tams = [t.strip() for t in (tam or "").split(",") if t.strip()]
    malos = [t for t in tams if t not in _TAMS]
    if malos:
        raise HTTPException(400, f"tam inválido: {', '.join(malos)}")
    if dir and dir not in _DIRS:
        raise HTTPException(400, f"dir inválida: {dir}")
    p = _params(dias, cuenta)
    cond, extra = ["true"], {}
    if estado:
        cond.append("estado = %(estado)s"); extra["estado"] = estado
    if tipo:
        cond.append("tipo = %(tipo)s"); extra["tipo"] = tipo
    if tams:
        # Compatible con el valor suelto de antes: "L" llega como lista de uno.
        cond.append("tam = any(%(tam)s)"); extra["tam"] = tams
    if q:
        cond.append("(sku::text ilike %(q)s or titulo ilike %(q)s)")
        extra["q"] = f"%{q}%"
    where = " and ".join(cond)
    col, dir_natural = _ORDEN.get(orden, _ORDEN["venta"])
    # `nulls last` en AMBAS direcciones: un SKU sin margen no es "el de menor
    # margen", es uno del que no sabemos — va al final se ordene como se ordene.
    orden_sql = f"{col} {dir or dir_natural} nulls last"
    try:
        total = await _fetch_scalar(
            _BASE + f"select count(*) from filas where {where}", {**p, **extra})
        items = await _fetch_all(
            _BASE + f"""select * from filas where {where}
                        order by {orden_sql}, sku limit %(limit)s offset %(offset)s""",
            {**p, **extra, "limit": limit, "offset": offset})
        # Sparkline: unidades por día (14 d) SOLO de los SKUs de esta página.
        if items:
            spark = await _fetch_all(
                _mx("""select sku, date, sum(units_sold)::int as u
                   from channel.sales_daily_completa
                   where date > current_date - 14 and sku = any(%(skus)s::citext[])
                     and (%(cuenta)s::text is null or cuenta = %(cuenta)s)
                   group by 1, 2"""),
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
            await _visitas_en_filas(items, dias, visitas)
            await _pesos_en_filas(items, visitas)
            pendientes = await _envio_real_en_filas(
                items, dias, cuenta,
                envios if envios is not None
                else getattr(settings, "tabla_envio_real_presupuesto", 150))
        else:
            pendientes = 0
        return {"total": int(total or 0), "items": items,
                "limit": limit, "offset": offset, "dias": dias,
                "orden": orden, "dir": dir or dir_natural,
                # Piezas de ESTA página que todavía traen envío estimado. El
                # frontend lo anuncia y refresca hasta que llegue a 0, igual que
                # el popup de "Productos más vendidos".
                "envios_pendientes": pendientes}
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
# La taxonomía es de ML pero se aplica POR SKU (channel.product_category), así
# que las ventas de Amazon también entran. Publicaciones/activas se cuentan
# sobre el catálogo listado completo de cada hoja, no solo lo vendido.
#
# FUENTE: LOS PEDIDOS (Eduardo, 5-ago). Antes esto leía
# channel.sales_daily_completa y el Excel leía los pedidos, así que la página y
# su propio reporte no cuadraban — en el sandbox se separaban casi al doble
# ($4.02M contra $2.04M), porque sales_daily_completa empalma el histórico
# rescatado de dailytrack. Ahora las dos leen lo mismo.
#
# Lo que se gana: el margen solo puede salir de los pedidos (es donde vive la
# comisión REAL de Mercado Libre), y la página cuadra con la pestaña VENTAS,
# que también es 100%% pedidos.
# Lo que se pierde: la venta anterior al backfill de channel.orders. Esta vista
# ya no ve el histórico de dailytrack — para eso está Estrellas, que sigue
# leyendo la serie completa a propósito.


# ── Consultas COMPARTIDAS: la página de Categorías y su Excel ────────────────
#
# Una sola familia, una sola fuente: los PEDIDOS. Antes la página leía
# channel.sales_daily_completa y el Excel leía los pedidos — la página y su
# propio reporte no cuadraban.
#
# El filtro de categoría es un parámetro (%%(categoria_id)s NULL = todas), así
# que el mismo query sirve para el desglose de UNA hoja del árbol en la UI y
# para el libro completo. Dos consultas que deben dar lo mismo son dos
# consultas que se van a desincronizar.
_SQL_CAT_LINEAS = """
  select i.item_id, o.cuenta, i.sku, i.cantidad, i.precio_unitario,
         i.comision, i.titulo,
         (o.creado_at at time zone 'America/Mexico_City')::date as fecha
    from channel.order_items i
    join channel.orders o using (canal, cuenta, external_order_id)
   where (o.creado_at at time zone 'America/Mexico_City')::date
         between %(desde)s::date and %(hasta)s::date
     and coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
     and i.sku is not null
     and (%(cuenta)s::text is null or o.cuenta = %(cuenta)s)
"""

# Una fila por publicación vendida, con su categoría y su costo. El costo se
# multiplica por las unidades: es el costo de LO VENDIDO, no el unitario.
_SQL_CAT_PUBS = _mx(f"""
with pc as (
  select sku, category_id from channel.product_category
  where channel_id = 'mercado_libre'
),
lin as ({_SQL_CAT_LINEAS})
select pc.category_id::text            as category_id,
       l.item_id, l.cuenta,
       max(l.sku::text)                as sku,
       sum(l.cantidad)::int            as uds,
       round(sum(l.precio_unitario * l.cantidad), 2)      as venta,
       round(sum(coalesce(l.comision, 0)), 2)             as comision,
       round(coalesce(max(cf.costo_fee_envio), 0) * sum(l.cantidad), 2) as envio,
       case when coalesce(max(cv.costo_total), max(cf.costo_unitario)) is not null
            then round(coalesce(max(cv.costo_total), max(cf.costo_unitario))
                       * sum(l.cantidad), 2) end          as costo_base,
       min(l.fecha)::text              as primera_venta,
       max(l.fecha)::text              as ultima_venta,
       max(ls.situacion)               as situacion,
       max(ls.price)                   as precio,
       coalesce(max(l.titulo), max(p.name)) as titulo,
       -- Insumos del DIAGNÓSTICO — mismos que _SQL_MARGEN_LINEAS. Van con max()
       -- porque son atributos del SKU, iguales en todas las filas del grupo.
       max(cf.costo_fee_envio)         as fee_envio_unit,
       bool_or(cv.sku is not null)     as tiene_validado,
       bool_or(cf.sku is not null)     as tiene_final,
       max(cv.piezas_por_caja)         as piezas_por_caja,
       max(cv.cajas)                   as cajas,
       max(cv.costo_producto)          as costo_producto,
       max(cv.peso) as peso, max(cv.largo) as largo,
       max(cv.alto) as alto, max(cv.ancho) as ancho
  from lin l
  join pc on pc.sku = l.sku
         and (%(categoria_id)s::text is null or pc.category_id = %(categoria_id)s)
  left join channel.listings ls on ls.listing_id = l.item_id
  left join core.products p on p.sku = l.sku
  left join costing.costos_validados cv on cv.sku = l.sku
  left join costing.costos_finales  cf on cf.sku = l.sku and cf.canal = 'mercado_libre'
 group by pc.category_id, l.item_id, l.cuenta
""")

# Las hojas del árbol. Conserva el FULL OUTER JOIN del original: una categoría
# con catálogo pero sin venta en el período también viaja (uds 0).
_SQL_CAT_HOJAS = _mx(f"""
with pc as (
  select pc.sku, pc.category_id,
         coalesce(nullif(trim(c.path), ''), c.name, 'Sin categoría') as ruta
  from channel.product_category pc
  join channel.categories c
    on c.category_id = pc.category_id and c.channel_id = pc.channel_id
),
lin as ({_SQL_CAT_LINEAS}),
porsku as (
  select l.sku,
         sum(l.cantidad)::int                          as uds,
         sum(l.precio_unitario * l.cantidad)           as venta,
         sum(coalesce(l.comision, 0))                  as comision,
         coalesce(max(cf.costo_fee_envio), 0) * sum(l.cantidad) as envio,
         case when coalesce(max(cv.costo_total), max(cf.costo_unitario)) is not null
              then coalesce(max(cv.costo_total), max(cf.costo_unitario))
                   * sum(l.cantidad) end               as costo_base
    from lin l
    left join costing.costos_validados cv on cv.sku = l.sku
    left join costing.costos_finales  cf on cf.sku = l.sku and cf.canal = 'mercado_libre'
   group by l.sku
),
porsku_c as (
  -- `creible` = el costo del producto no supera 3x lo vendido. Arriba de eso
  -- ya no es una decision comercial (liquidar, error de precio): es captura.
  select p.*, (p.costo_base is not null and p.costo_base <= p.venta * 3) as creible
    from porsku p
),
ventas_cat as (
  -- El bloque de MARGEN se restringe a los SKUs con costo capturado. Sin ese
  -- filter la categoría cargaba la comisión y el envío de productos cuyo costo
  -- no conocemos: el costo final salía inflado y `venta_con_costo` contaba la
  -- venta entera de la categoría, no la medible. Con 4,968 líneas eso separaba
  -- al Resumen de la hoja Ventas en $15.7k de costo y $83.3k de venta.
  --
  -- Y ADEMAS se excluyen los COSTOS INCREIBLES (Eduardo, 6-ago). Un SKU cuyo
  -- costo capturado supera 3 veces lo que vendio no es una perdida: es un dato
  -- roto — hay 119 asi en 60 dias, 32 de ellos arriba de 3x (TEC-0406-AZL:
  -- vende en $269 con costo $30,058). Promediados dentro de la rama arrastran
  -- a los sanos: Herramientas mostraba -173.9%% por unos pocos. Al excluirlos,
  -- el porcentaje de la rama vuelve a ser legible y `venta_con_costo` baja,
  -- que es justo la senal de que la foto esta incompleta (la UI lo marca con
  -- asterisco). Mismo umbral que costoImplausible() en el frontend.
  select coalesce(pc.ruta, 'Sin categoría') as ruta, pc.category_id,
         sum(v.uds)::int                    as uds,
         round(sum(v.venta), 2)             as venta,
         round(sum(v.comision) filter (where v.creible), 2) as comision,
         round(sum(v.envio)    filter (where v.creible), 2) as envio,
         round(sum(v.costo_base) filter (where v.creible), 2) as costo_base,
         round(sum(v.venta)    filter (where v.creible), 2) as venta_con_costo,
         count(distinct v.sku)::int         as skus
    from porsku_c v left join pc on pc.sku = v.sku
   group by 1, 2
),
cuentas_cat as (
  -- Desglose por cuenta de cada hoja del árbol (la UI lo pinta al lado). Va en
  -- su propio CTE porque `porsku` agrega por SKU para poder cruzar el costo,
  -- y aquí hace falta el corte por cuenta.
  select ruta, category_id,
         jsonb_agg(jsonb_build_object('cuenta', cuenta, 'uds', uds, 'venta', venta)
                   order by cuenta) as cuentas
    from (select coalesce(pc.ruta, 'Sin categoría') as ruta, pc.category_id,
                 l.cuenta,
                 sum(l.cantidad)::int as uds,
                 round(sum(l.precio_unitario * l.cantidad), 2) as venta
            from lin l left join pc on pc.sku = l.sku
           group by 1, 2, 3) y
   group by 1, 2
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
select coalesce(s.ruta, l.ruta)               as ruta,
       coalesce(s.category_id, l.category_id) as category_id,
       coalesce(s.uds, 0)::int                as uds,
       coalesce(s.venta, 0)                   as venta,
       coalesce(s.comision, 0)                as comision,
       coalesce(s.envio, 0)                   as envio,
       s.costo_base,
       coalesce(s.venta_con_costo, 0)         as venta_con_costo,
       coalesce(s.skus, 0)::int               as skus,
       coalesce(l.publicaciones, 0)::int      as publicaciones,
       coalesce(l.activas, 0)::int            as activas,
       c.cuentas
from ventas_cat s
full outer join lst l on l.category_id = s.category_id
left join cuentas_cat c on c.category_id is not distinct from s.category_id
order by venta desc
""")


def _rango_fechas(dias: int, desde: str | None, hasta: str | None) -> tuple[str, str]:
    """(desde, hasta) ISO. Sin fechas explícitas replica el período relativo
    `dias` (los últimos N días hasta hoy CDMX, como el SQL original)."""
    from datetime import date, datetime, timedelta, timezone
    hoy = datetime.now(timezone(timedelta(hours=-6))).date()
    try:
        h = min(date.fromisoformat(hasta), hoy) if hasta else hoy
        d = date.fromisoformat(desde) if desde else h - timedelta(days=dias - 1)
    except ValueError as exc:
        raise HTTPException(400, f"fecha inválida: {exc}") from exc
    if d > h:
        d, h = h, d
    if (h - d).days > 730:
        raise HTTPException(400, "rango máximo: 2 años")
    return d.isoformat(), h.isoformat()


@router.get("/categorias")
async def categorias(
    dias: int = Query(60, ge=7, le=400),
    cuenta: str | None = Query(None),
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
) -> dict[str, Any]:
    """Ventas por categoría con la ruta COMPLETA de ML: devuelve las hojas
    (ruta + category_id) y la UI arma el árbol con acumulados por nivel.
    `dias=400` cubre todo el histórico; `desde`/`hasta` (YYYY-MM-DD) fijan un
    período absoluto y mandan sobre `dias`."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    try:
        d1, d2 = _rango_fechas(dias, desde, hasta)
        filas = await _fetch_all(
            _SQL_CAT_HOJAS,
            {"desde": d1, "hasta": d2, "cuenta": cuenta, "categoria_id": None})
        venta_total = sum(float(f["venta"]) for f in filas)
        uds_total = sum(f["uds"] for f in filas)
        # solo las que SÍ vendieron cuentan como "categorías con venta"
        principales = {str(f["ruta"]).split("›")[0].strip()
                       for f in filas if f["uds"]}
        return {
            "ambiente": settings.app_env, "dias": dias, "cuenta": cuenta,
            "desde": d1, "hasta": d2,
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
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
) -> dict[str, Any]:
    """Las publicaciones (item_id) de una hoja del árbol, como las filas del
    xlsx: cuenta, título, situación, precio y ventas del período."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    try:
        d1, d2 = _rango_fechas(dias, desde, hasta)
        filas = await _fetch_all(
            _SQL_CAT_PUBS,
            {"categoria_id": categoria_id, "desde": d1, "hasta": d2,
             "cuenta": cuenta})
        # el tope se aplica AQUÍ y no en el SQL: el mismo query alimenta al
        # Excel, que necesita todas las publicaciones
        filas.sort(key=lambda f: -float(f["venta"] or 0))
        return {"categoria_id": categoria_id, "dias": dias,
                "desde": d1, "hasta": d2, "items": filas[:200], "tope": 200}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("categorias/publicaciones falló: %s", exc)
        raise HTTPException(502, f"lectura de la BD kubera falló: {exc}") from exc


# ── Reporte de INVENTARIO: dos preguntas, dos hojas ──────────────────────────
#
# No es un volcado del inventario: son las dos poblaciones sobre las que se
# puede ACTUAR hoy, y son problemas opuestos.
#
#   INMOVILIZADO — hay stock en FULL y no vende. Ahí no solo es capital
#     detenido: paga renta a ML todos los días. Censo 7-ago: 14,873 unidades
#     en FULL sin una sola venta en 30 días (39%% de todo lo que hay en FULL),
#     y las mayores nunca han vendido una pieza.
#
#   INVISIBLE — vende, pero ninguna publicación está activa. Demanda probada
#     con el aparador cerrado. Caso canónico TEC-0393-ROS: 291 unidades
#     vendidas en 30 días, 2,394 en bodega, las dos publicaciones pausadas.
#
# EL FILTRO QUE HACE CREÍBLE A "INVISIBLE": solo entra lo pausado CON STOCK.
# Lo pausado SIN stock está agotado, que es la razón correcta para pausar, y
# pertenece a "Reponer" (todavía sin construir). Sin esa separación la hoja
# mezclaría 187 SKUs donde solo una parte es accionable.
#
# `max(stock_own)` y NO `sum`: el stock propio está espejeado en CADA
# publicación del mismo SKU (misma bodega vista desde otra publicación).
# Sumarlo por publicación cuenta la misma pieza varias veces — medido el
# 7-ago: 1,109,525 unidades en el canal `general` contra 343,045 en
# mercado_libre, que son las MISMAS piezas. FULL y FBA sí son por cuenta.
_SQL_INV_BASE = _mx("""
with padres as (
  -- LA PUBLICACIÓN VIVE EN EL PADRE, LAS VENTAS LLEGAN EN EL HIJO (Eduardo,
  -- 7-ago). Caso ORG-0841: la publicación de Sancor está registrada con el SKU
  -- padre `ORG-0841` y sus 127 piezas en FULL, mientras las 48 ventas del mes
  -- entraron como `ORG-0841-AZL-L`. Cruzados por SKU a secas, el padre parecía
  -- no haber vendido NUNCA. Eran 21 de 292 inmovilizados falsos, 1,465
  -- unidades mal clasificadas.
  --
  -- La relación buena es `wc_parent_id` (poblada en 7,299 productos).
  -- `parent_sku` y `has_variations` están VACÍAS en las 22,186 filas de
  -- core.products — no usarlas, no son la fuente.
  select h.sku::text as hijo, p.sku::text as padre
    from core.products h
    join core.products p on p.wc_id = h.wc_parent_id
   where h.wc_parent_id is not null and h.wc_parent_id <> 0),
v30 as (
  select coalesce(pa.padre, i.sku::text) as sku, sum(i.cantidad)::int as uds,
         max((o.creado_at at time zone 'America/Mexico_City')::date) as ult
    from channel.order_items i
    join channel.orders o using (canal, cuenta, external_order_id)
    left join padres pa on pa.hijo = i.sku::text
   where coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
     and i.sku is not null
     and (o.creado_at at time zone 'America/Mexico_City')::date
         > current_date - %(dias)s
   group by 1),
vh as (
  select coalesce(pa.padre, i.sku::text) as sku,
         max((o.creado_at at time zone 'America/Mexico_City')::date) as ult_hist
    from channel.order_items i
    join channel.orders o using (canal, cuenta, external_order_id)
    left join padres pa on pa.hijo = i.sku::text
   where coalesce(o.estado_canal, '') not in ('cancelled', 'invalid', 'Canceled')
     and i.sku is not null
   group by 1),
lst as (
  select coalesce(pa.padre, l.sku::text) as clave, l.sku::text as sku_real,
         (pa.padre is not null) as es_hijo,
         l.canal, l.situacion, a.legacy_code as cta, l.listing_id::text as pub_id,
         coalesce(l.stock_own, 0)  as stock_own,
         coalesce(l.stock_full, 0) as stock_full,
         coalesce(l.stock_fba, 0)  as stock_fba
    from channel.listings l
    join core.accounts a on a.id = l.account_id
    left join padres pa on pa.hijo = l.sku::text
   where lower(coalesce(l.situacion, '')) <> 'closed'
     and (%(cuenta)s::text is null or a.legacy_code = %(cuenta)s)),
pub as (
  -- UNA PUBLICACIÓN ES UNA, Y SUS PIEZAS SE CUENTAN UNA VEZ (14-ago-2026).
  --
  -- `channel.listings` guarda una fila por SKU, pero en un producto con
  -- variantes el PADRE y el HIJO comparten el MISMO `listing_id`: es la misma
  -- publicación de ML vista desde dos SKUs. Como `lst` cuelga a los dos de la
  -- misma clave, sumar por fila contaba las mismas piezas dos veces.
  -- Medido: DEC-0014 (MLM3136223689) reportaba 400 unidades en FULL — 200 en
  -- `DEC-0014` y otras 200 en `DEC-0014-BLN`, que son las MISMAS 200. Igual
  -- JUGU-0268 (398→199) y HERR-0035 (382→191). En total 40 publicaciones
  -- duplicadas: 2,024 unidades fantasma de 34,766 en FULL (5.8%%), y 522 de
  -- las 9,436 que la hoja de Inmovilizado presentaba como capital detenido.
  --
  -- `max` y no `sum` por el mismo motivo que en `prop`: las dos filas son la
  -- misma pieza. Cuando difieren (TEC-0794: 89 y 90) es el sync que las leyó
  -- en momentos distintos, no dos existencias.
  --
  -- La clave cae en `sku_real` cuando no hay `listing_id` para que esas filas
  -- sigan contándose una por SKU, como hasta hoy.
  select clave, canal, cta,
         coalesce(pub_id, 'sku:' || sku_real) as clave_pub,
         -- Con qué SKU se nombra la publicación colapsada: se PREFIERE EL HIJO.
         -- El padre de un producto con variantes no se vende —se vende la
         -- variante— así que decir "CAM-0030" donde las piezas son de
         -- `CAM-0030-IND` manda a buscar ventas de un SKU que nunca las tendrá.
         coalesce(max(sku_real) filter (where es_hijo), max(sku_real)) as sku_real,
         max(stock_full) as stock_full,
         max(stock_fba)  as stock_fba,
         -- "Viva" se dice distinto en cada canal: ML usa 'active', Amazon usa
         -- 'buyable'/'published'. Contar solo 'active' marcaba como invisibles
         -- 3 SKUs que sí estaban a la venta en Amazon.
         bool_or((canal = 'mercado_libre'
                  and lower(coalesce(situacion,'')) = 'active')
              or (canal = 'amazon'
                  and lower(coalesce(situacion,'')) in ('buyable', 'published')))
           as viva,
         bool_or(canal = 'mercado_libre'
                 and lower(coalesce(situacion,'')) = 'paused') as pausada
    from lst
   group by 1, 2, 3, 4),
prop as (
  -- STOCK PROPIO: nunca mezclar Woo con los espejos del marketplace.
  --
  -- Woo (`general`) es el almacén de registro y guarda una fila POR VARIANTE:
  -- esas son piezas distintas y se SUMAN entre hermanos de la familia.
  --
  -- El `stock_own` de una publicación de marketplace es un espejo, y en un
  -- producto con variantes el espejo del PADRE trae el acumulado de los hijos:
  -- DEC-0012 tiene 26,100 en su publicación de Sancor contra 25,410 sumando
  -- sus 8 variantes en Woo. Sumar padre + hijos contaba el mismo inventario
  -- dos veces (daba 51,510). Por eso el espejo solo entra cuando la familia no
  -- tiene NINGUNA fila en Woo, y ahí se toma el máximo, jamás la suma.
  select clave,
         coalesce(sum(woo) filter (where woo is not null),
                  max(espejo))::int as propio
    from (select clave, sku_real,
                 max(stock_own) filter (where canal = 'general')  as woo,
                 max(stock_own) filter (where canal <> 'general') as espejo
            from lst group by 1, 2) x
   group by 1),
s as (
  -- Cuenta sobre `pub` (una fila por publicación real), NO sobre `lst` (una
  -- fila por SKU): ver la nota de `pub`. `prop` sí se queda con `lst`, porque
  -- el stock propio se resuelve por variante y ya tiene su propia regla.
  select p.clave as sku,
         -- WOOCOMMERCE NO ES UN CANAL (Eduardo, 7-ago): es nuestro puente de
         -- registro, y ahí vive el almacén propio. Su fila SÍ cuenta para el
         -- stock —es la fuente buena: en 47 de 97 SKUs el valor cambia si se
         -- le excluye— pero NO como tienda ni como publicación.
         max(prop.propio)                                      as propio,
         -- FULL es un concepto de MERCADO LIBRE. Sin este filtro la columna
         -- "En FULL" no cuadraba con la suma de Bekura + Sancor (272 contra
         -- 200 en JUGU-0261-LIL): se colaba `stock_full` de publicaciones de
         -- Amazon, cuyo equivalente es `stock_fba` y va en su propia columna.
         sum(p.stock_full)
           filter (where p.canal = 'mercado_libre')::int       as full_total,
         sum(p.stock_full) filter (where p.cta = 'BEKURA')::int        as full_bk,
         sum(p.stock_full) filter (where p.cta = 'SANCORFASHION')::int as full_sc,
         sum(p.stock_fba)::int                                 as fba,
         count(*) filter (where p.viva)::int                   as activas,
         count(*) filter (where p.pausada)::int                as pausadas,
         count(*) filter (where p.canal <> 'general')::int     as pubs,
         array_agg(distinct p.cta order by p.cta)
           filter (where p.canal <> 'general')                 as cuentas,
         -- DÓNDE ESTÁ REALMENTE EL STOCK (Eduardo, 14-ago). El renglón se
         -- nombra con el SKU padre, pero el padre no tiene inventario: lo
         -- tienen sus variantes. Sin este desglose, quien revisa busca las
         -- ventas del padre, no encuentra ninguna —nunca las va a haber— y
         -- concluye que el reporte miente. Caso CAM-0030: sus 230 piezas son
         -- todas de `CAM-0030-IND` (150 en Sancor, 80 en Bekura).
         jsonb_agg(jsonb_build_object(
                     'sku', p.sku_real, 'cuenta', p.cta, 'uds', p.stock_full)
                   order by p.stock_full desc)
           filter (where p.canal = 'mercado_libre' and p.stock_full > 0)
                                                               as full_detalle
    from pub p
    join prop on prop.clave = p.clave
   group by 1)
""")

# Stock en FULL que no vendió NADA en el período. Ordenado por unidades: es lo
# que más renta paga sin devolver nada.
_SQL_INV_INMOVILIZADO = _SQL_INV_BASE + """
select s.sku, coalesce(p.name, '') as titulo,
       s.full_total, s.full_bk, s.full_sc, s.propio,
       s.activas, s.pausadas, s.cuentas,
       -- El renglón es la FAMILIA, no el SKU padre: `variantes` dice cuántas
       -- cubre (0 = producto simple) y `full_detalle` en cuál está el stock.
       (select count(*) from padres where padre = s.sku)::int as variantes,
       s.full_detalle,
       vh.ult_hist::text as ultima_venta,
       case when vh.ult_hist is not null
            then (current_date - vh.ult_hist)::int end as dias_sin_vender
  from s
  left join v30 on v30.sku = s.sku
  left join vh  on vh.sku  = s.sku
  left join core.products p on p.sku = s.sku
 where coalesce(v30.uds, 0) = 0
   and s.full_total > 0
 order by s.full_total desc, s.propio desc
"""

# Vendió y NO tiene una sola publicación activa, teniendo stock con qué surtir.
_SQL_INV_INVISIBLE = _SQL_INV_BASE + """
select s.sku, coalesce(p.name, '') as titulo,
       v30.uds as uds_periodo, v30.ult::text as ultima_venta,
       s.propio, s.full_total, s.fba,
       (s.propio + s.full_total + s.fba)::int as stock_total,
       s.pausadas, s.pubs, s.cuentas
  from s
  join v30 on v30.sku = s.sku
  left join core.products p on p.sku = s.sku
 where s.activas = 0
   and (s.propio + s.full_total + s.fba) > 0
 order by v30.uds desc, stock_total desc
"""


async def _datos_reporte(d1: str, d2: str, cuenta: str | None) -> tuple:
    """
    Los tres conjuntos del reporte, ya enriquecidos con el envío REAL de ML.

    Vive aparte porque lo usan DOS endpoints: la descarga del Excel y su vista
    previa. Si cada uno armara sus datos, la vista previa acabaría prometiendo
    un archivo distinto del que llega — que es exactamente lo que no debe pasar
    en una previsualización.
    """
    import asyncio

    from services import envio_real

    par = {"desde": d1, "hasta": d2, "cuenta": cuenta, "categoria_id": None}
    hojas = await _fetch_all(_SQL_CAT_HOJAS, par)
    pubs = await _fetch_all(_SQL_CAT_PUBS, par)
    # la hoja de detalle reusa el MISMO query del CSV que se retiró
    ventas = await _fetch_all(
        _SQL_MARGEN_LINEAS,
        {"desde": d1, "hasta": d2, "cuenta": cuenta, "canal": None})

    # ENVÍO REAL. El estimado de costing mintió en las dos direcciones (Malla
    # Sombra: fee $349 contra un cobro real de $88), así que donde ML nos diga
    # cuánto cobró, manda ML. `aplicar_a_lineas` reparte el cobro del EMBARQUE
    # entre las líneas de su orden y rearma el costo final; lo que no esté en
    # caché se queda con el estimado, marcado.
    censo = await asyncio.to_thread(envio_real.aplicar_a_lineas, ventas)

    # El árbol agrega por publicación: se suma el envío ya resuelto de sus
    # líneas en vez de volver a leer costos_finales, para que las dos hojas no
    # se contradigan. Una publicación es "ML real" solo si TODAS sus líneas lo
    # son — media medición no es una medición.
    real_pub: dict[tuple[str, str], list[float]] = {}
    origen_pub: dict[tuple[str, str], set[str]] = {}
    for f in ventas:
        if not f.get("item_id"):
            continue
        k = (str(f["item_id"]), str(f["cuenta"]))
        real_pub.setdefault(k, []).append(float(f.get("envio") or 0))
        origen_pub.setdefault(k, set()).add(f.get("envio_origen") or "")
    for pb in pubs:
        k = (str(pb.get("item_id")), str(pb.get("cuenta")))
        orgs = origen_pub.get(k)
        if not orgs:
            pb["envio_origen"] = envio_real.ORIGEN_ESTIMADO
            continue
        pb["envio"] = round(sum(real_pub.get(k) or []), 2)
        pb["envio_origen"] = (next(iter(orgs)) if len(orgs) == 1 else "mezclado")
        if pb.get("costo_base") is not None:
            pb["costo_final_real"] = round(
                float(pb["costo_base"]) + float(pb.get("comision") or 0)
                + float(pb["envio"]), 2)

    # Lo que falte se consulta en segundo plano: la siguiente descarga sale más
    # completa sin que ésta espere ~5,800 llamadas a ML.
    pend = sorted({(str(f["cuenta"]), str(f["pedido"])) for f in ventas
                   if (f.get("canal") or "") == "mercado_libre" and f.get("pedido")
                   and f.get("envio_origen") != envio_real.ORIGEN_REAL})
    if pend:
        asyncio.create_task(envio_real.completar(pend, presupuesto=400))
    log.info("reporte %s→%s: envío %d real / %d estimado / %d sin dato (%d por llenar)",
             d1, d2, censo["reales"], censo["estimadas"], censo["sin_dato"], len(pend))
    return hojas, pubs, ventas, censo


def _datos_inventario(dias: int, cuenta: str | None) -> tuple[list, list]:
    """Las dos poblaciones accionables. Compartido por la descarga y su previa,
    por lo mismo que `_datos_reporte`: una previa que arma sus propios datos
    acaba prometiendo un archivo distinto del que llega."""
    par = {"dias": dias, "cuenta": cuenta}
    return (sdb.fetch_all(_SQL_INV_INMOVILIZADO, par),
            sdb.fetch_all(_SQL_INV_INVISIBLE, par))


@router.get("/inventario/excel/preview")
async def inventario_excel_preview(
    dias: int = Query(30, ge=7, le=400),
    cuenta: str | None = Query(None),
):
    """Qué trae el reporte de inventario antes de bajarlo."""
    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    try:
        inm, inv = await asyncio.to_thread(_datos_inventario, dias, cuenta)
        return {
            "dias": dias,
            "inmovilizado": {
                "skus": len(inm),
                "unidades_full": sum(int(f.get("full_total") or 0) for f in inm),
                "nunca_vendieron": sum(1 for f in inm if not f.get("ultima_venta")),
                # `variantes` y `donde` viajan a la vista previa para que el
                # renglón se lea como lo que es —una FAMILIA— y no como el SKU
                # padre, que nunca vende por sí mismo (Eduardo, 14-ago).
                "top": [{"sku": f["sku"], "titulo": (f.get("titulo") or "")[:60],
                         "full": int(f.get("full_total") or 0),
                         "propio": int(f.get("propio") or 0),
                         "variantes": int(f.get("variantes") or 0),
                         "donde": [
                             {"sku": d.get("sku"), "cuenta": d.get("cuenta"),
                              "uds": int(d.get("uds") or 0)}
                             for d in (f.get("full_detalle") or [])][:3],
                         "ultima_venta": f.get("ultima_venta")} for f in inm[:5]],
            },
            "invisible": {
                "skus": len(inv),
                "unidades_vendidas": sum(int(f.get("uds_periodo") or 0) for f in inv),
                "stock_disponible": sum(int(f.get("stock_total") or 0) for f in inv),
                "top": [{"sku": f["sku"], "titulo": (f.get("titulo") or "")[:60],
                         "uds": int(f.get("uds_periodo") or 0),
                         "stock": int(f.get("stock_total") or 0),
                         "ultima_venta": f.get("ultima_venta")} for f in inv[:5]],
            },
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("inventario/excel/preview falló: %s", exc)
        raise HTTPException(502, f"no se pudo calcular la vista previa: {exc}") from exc


@router.get("/inventario/excel")
async def inventario_excel(
    dias: int = Query(30, ge=7, le=400),
    cuenta: str | None = Query(None),
):
    """
    Inventario ACCIONABLE en dos hojas: Inmovilizado (stock en FULL que no
    vende y paga renta) e Invisible (vende, tiene stock, y ninguna publicación
    activa). Ver el encabezado de `reporte_inventario_xlsx`.
    """
    from fastapi.responses import Response

    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    try:
        from services import reporte_inventario_xlsx

        inm, inv = await asyncio.to_thread(_datos_inventario, dias, cuenta)
        log.info("inventario: %d inmovilizados / %d invisibles (%d días)",
                 len(inm), len(inv), dias)
        datos = await asyncio.to_thread(
            reporte_inventario_xlsx.construir, inm, inv, dias, cuenta)
        nombre = f"inventario_accionable_{dias}d.xlsx"
        return Response(
            content=datos,
            media_type=("application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet"),
            headers={"Content-Disposition": f'attachment; filename="{nombre}"'})
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("inventario/excel falló: %s", exc)
        raise HTTPException(502, f"no se pudo generar el Excel: {exc}") from exc


@router.get("/categorias/excel/preview")
async def categorias_excel_preview(
    dias: int = Query(60, ge=7, le=400),
    cuenta: str | None = Query(None),
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
):
    """
    Qué trae el Excel ANTES de bajarlo: tamaño, rango con datos de verdad,
    cobertura de costo y de envío, y el censo de diagnósticos.

    Corre EXACTAMENTE la misma preparación que la descarga (`_datos_reporte`);
    lo único que no hace es armar el workbook. Así la vista previa no puede
    prometer un archivo distinto del que llega.
    """
    from collections import Counter

    from services import reporte_categorias_xlsx as rx

    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    d1, d2 = _rango_fechas(dias, desde, hasta)
    try:
        hojas, pubs, ventas, censo = await _datos_reporte(d1, d2, cuenta)

        fechas = sorted(str(v["fecha"]) for v in ventas if v.get("fecha"))
        ingreso = sum(float(v.get("ingreso") or 0) for v in ventas)
        uds = sum(int(v.get("cantidad") or 0) for v in ventas)
        con_costo = [v for v in ventas if v.get("costo_base") is not None]
        venta_con_costo = sum(float(v.get("ingreso") or 0) for v in con_costo)

        # El MISMO diagnóstico que se escribe en el archivo, contado por código.
        diag: Counter = Counter()
        for v in ventas:
            txt = rx.diagnosticar(v, v.get("ingreso"), v.get("costo_base"))
            if txt:
                diag[txt.split(" — ")[0]] += 1

        n = len(ventas)
        pct = lambda x: round(x / n * 100, 1) if n else 0.0  # noqa: E731
        return {
            "rango": {
                "desde": d1, "hasta": d2,
                "primera_venta": fechas[0] if fechas else None,
                "ultima_venta": fechas[-1] if fechas else None,
                "dias_con_venta": len(set(fechas)),
                # MISMA regla que el aviso de Resumen!A2 (rx.rango_parcial), no
                # una copia con otro umbral: la vista previa tiene que avisar
                # exactamente cuando el archivo va a avisar.
                "parcial": rx.rango_parcial(d1, d2, fechas),
            },
            "totales": {
                "lineas": n,
                "pedidos": len({(v.get("cuenta"), v.get("pedido")) for v in ventas}),
                "publicaciones": len(pubs),
                "categorias": len(hojas),
                "skus": len({v.get("sku") for v in ventas if v.get("sku")}),
                "unidades": uds,
                "ingreso": round(ingreso, 2),
            },
            "cobertura": {
                "costo": {
                    "lineas": len(con_costo), "pct": pct(len(con_costo)),
                    "venta_con_costo": round(venta_con_costo, 2),
                    "pct_venta": (round(venta_con_costo / ingreso * 100, 1)
                                  if ingreso else 0.0),
                },
                "envio": {**censo, "pct_real": pct(censo["reales"])},
            },
            "diagnosticos": [{"codigo": c, "lineas": k, "pct": pct(k)}
                             for c, k in diag.most_common()],
            "hojas": [
                {"nombre": "Resumen", "filas": len(hojas), "columnas": 10},
                {"nombre": "Categorias", "filas": len(pubs), "columnas": 16},
                {"nombre": "Ventas", "filas": n, "columnas": 17},
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("categorias/excel/preview falló: %s", exc)
        raise HTTPException(502, f"no se pudo calcular la vista previa: {exc}") from exc


@router.get("/categorias/excel")
async def categorias_excel(
    dias: int = Query(60, ge=7, le=400),
    cuenta: str | None = Query(None),
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
):
    """El reporte ÚNICO de ventas y costos (Eduardo, 5-ago): Resumen por
    categoría, árbol de Categorías con sus publicaciones y una hoja Ventas con
    una fila por línea vendida. Las tres hojas salen de los PEDIDOS, así que el
    libro cuadra consigo mismo: la comisión de cada categoría es la misma que
    respalda cada renglón de la hoja Ventas.

    SIN Ganancia ni Margen %% desde el 7-ago (Eduardo): la base de costos tiene
    defectos medidos —placeholders USD×19, peso de caja capturado como pieza,
    piezas_por_caja < 1— y un margen sacado de ahí se lee como un hecho sin
    serlo. Las columnas de costo se quedan como dato crudo.

    Sustituye al CSV de márgenes, que era el mismo dato con otro rango de
    fechas y otro botón."""
    from fastapi.responses import Response

    if not sdb.disponible():
        raise HTTPException(503, "BD kubera no configurada en este ambiente")
    if cuenta and cuenta not in _CUENTAS:
        raise HTTPException(400, f"cuenta inválida: {cuenta}")
    d1, d2 = _rango_fechas(dias, desde, hasta)
    try:
        import asyncio

        from services import reporte_categorias_xlsx

        hojas, pubs, ventas, censo = await _datos_reporte(d1, d2, cuenta)
        datos = await asyncio.to_thread(
            reporte_categorias_xlsx.construir, hojas, pubs, ventas, d1, d2,
            cuenta, censo)
        nombre = f"ventas_costos_{d1.replace('-', '')}_{d2.replace('-', '')}.xlsx"
        return Response(
            content=datos,
            media_type=("application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet"),
            headers={"Content-Disposition": f'attachment; filename="{nombre}"'})
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("categorias/excel falló: %s", exc)
        raise HTTPException(502, f"no se pudo generar el Excel: {exc}") from exc
