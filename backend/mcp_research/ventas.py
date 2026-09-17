"""
ventas.py — Capacidad 2: ventas por canal, según lo guardado en Supabase.

FUENTE: `channel.sales_daily_completa`, que es una VISTA y une dos mundos —por
eso trae la columna `fuente`, y por eso aquí se expone siempre:

  · `hist` = `analytics.sales_daily_hist`, el archivo histórico. **Sólo Mercado
    Libre**, sin columna `canal`.
  · `vivo` = `channel.sales_daily`, el flujo del día a día.

COBERTURA REAL, medida el 17-sep-2026 sobre la vista (NO es la del encargo, que
decía "vivo desde el 16-feb"; eso es cierto de `channel.sales_daily` a secas,
pero la vista corta en el 15-jul y desde ahí toma el flujo vivo):

    mercado_libre  hist   2025-12-27 → 2026-07-15    60,325 piezas
    mercado_libre  vivo   2026-07-16 → 2026-09-17    35,018 piezas
    amazon         vivo   2026-07-16 → 2026-09-17       451 piezas
    temu           vivo   2026-08-10 → 2026-09-17        91 piezas
    tiktok         vivo   2026-08-12 → 2026-09-16       163 piezas
    walmart               SIN UNA SOLA FILA

→ Preguntar "ventas por canal" de marzo devuelve Mercado Libre y nada más. La
herramienta lo DICE (`cobertura`, `canales_sin_datos`, avisos) en vez de
contestar 0 para Amazon, Walmart, TikTok o Temu en un periodo donde nadie los
medía. Un 0 se interpreta como "no vendió"; la verdad es "no se midió".

WALMART NO ES UN HUECO DE FECHAS, ES UN HUECO DE INGESTA. Walmart MX SÍ vende
—hay ventas desde el 14-ago sin ingerir, no hay webhook y la pieza que falta es
un sondeo—, así que su ausencia aquí no significa cero ventas: significa que
nadie las está leyendo. Nunca se reporta Walmart como 0.

EL AGUJERO DE LA VISTA. `channel.sales_daily` tiene renglones que
`sales_daily_completa` NO expone, porque la vista corta por fecha y el flujo
vivo empezó ANTES del corte: medido el 17-sep-2026, 11 renglones de Amazon
(11 piezas, 2→14-jul) y 171 de Mercado Libre (246 piezas, 16-feb→15-jul). Es
poco, pero es real, y `diagnostico=True` lo vuelve a medir en vivo en vez de
creerle a este comentario.

TRES CONFUSIONES QUE CAMBIAN EL NÚMERO
---------------------------------------
1. `creado_at` de `channel.orders` es la fecha de COMPRA del cliente, no la de
   proceso. No son lo mismo y ya causó un error de lectura. Esta herramienta
   trabaja sobre `date` de la vista diaria, que es la de la venta.
2. **Mercado Libre tiene DOS cuentas**: BEKURA = «Kubera» y SANCORFASHION =
   «San Corpe». Un total de `mercado_libre` sin separar cuenta esconde la mitad
   de la información, así que `agrupar_por` siempre ofrece la cuenta y el
   detalle la trae. Ojo con una rareza real: 44 renglones traen
   `cuenta='AMAZON'` bajo `canal='mercado_libre'`.
3. En Amazon, `item_id` es el **OrderItemId**, no el ASIN.

`es_fulfillment` discrepa 40% entre `channel.orders` y `channel.order_items`.
Aquí no se usa ninguno de los dos: la vista trae su propio `is_full`, y se
reporta como viene, diciendo de dónde salió.
"""
from __future__ import annotations

from typing import Any

from . import conexion as cx

# Agrupaciones permitidas. Es una LISTA BLANCA a propósito: el `group by` no se
# puede parametrizar con %s, así que la única forma segura de dejar elegir es
# que las piezas de SQL sean literales escritos aquí y el usuario sólo elija
# cuál. Nada de lo que escribe quien pregunta llega al texto de la consulta.
_AGRUPACIONES: dict[str, tuple[str, str]] = {
    "canal":        ("canal",                    "canal"),
    "cuenta":       ("canal, cuenta",            "canal, cuenta"),
    "sku":          ("sku::text as sku",         "sku"),
    "sku_canal":    ("sku::text as sku, canal",  "sku, canal"),
    "dia":          ("date",                     "date"),
    "canal_dia":    ("date, canal",              "date, canal"),
    "cuenta_dia":   ("date, canal, cuenta",      "date, canal, cuenta"),
}

_FILTRO = """
 where (%(desde)s::date      is null or date  >= %(desde)s::date)
   and (%(hasta)s::date      is null or date  <= %(hasta)s::date)
   and (%(canal)s::text      is null or canal  = %(canal)s::text)
   and (%(cuenta)s::text     is null or cuenta = %(cuenta)s::text)
   and (%(skus)s::citext[]   is null or sku    = any(%(skus)s::citext[]))
"""

_SQL_DETALLE = f"""
select date, canal, cuenta, sku::text as sku, item_id, is_full,
       units_sold, revenue, sale_fee, fuente
  from channel.sales_daily_completa
{_FILTRO}
 order by date desc, units_sold desc
 limit %(limite)s
"""

_SQL_TOTAL = f"""
select sum(units_sold)::bigint  as piezas,
       sum(revenue)             as bruto,
       sum(sale_fee)            as comision,
       count(*)                 as renglones,
       count(distinct sku)      as skus,
       min(date)                as primer_dia,
       max(date)                as ultimo_dia
  from channel.sales_daily_completa
{_FILTRO}
"""

_SQL_COBERTURA = """
select canal, fuente, min(date) as desde, max(date) as hasta,
       count(*) as renglones, sum(units_sold)::bigint as piezas
  from channel.sales_daily_completa
 group by canal, fuente
 order by canal, fuente
"""

# Los canales que la casa RECONOCE, para poder decir "este canal no tiene
# datos" en vez de simplemente no mencionarlo. Sale de `core.accounts`, que es
# el censo de cuentas, no de una lista escrita a mano que se quedaría vieja.
_SQL_CANALES = "select distinct channel_id from core.accounts order by 1"

_SQL_DIAGNOSTICO = """
select d.canal, count(*) as renglones, sum(d.units_sold)::bigint as piezas,
       min(d.date) as desde, max(d.date) as hasta
  from channel.sales_daily d
  left join channel.sales_daily_completa c
         on c.date = d.date and c.canal = d.canal and c.cuenta = d.cuenta
        and c.item_id is not distinct from d.item_id
        and c.sku     is not distinct from d.sku
 where c.date is null
 group by d.canal
 order by d.canal
"""


def _construir(agrupar_por: str) -> str:
    sel, grp = _AGRUPACIONES[agrupar_por]
    return f"""
select {sel},
       sum(units_sold)::bigint as piezas,
       sum(revenue)            as bruto,
       sum(sale_fee)           as comision,
       count(distinct sku)     as skus,
       min(date)               as primer_dia,
       max(date)               as ultimo_dia,
       array_agg(distinct fuente) as fuentes
  from channel.sales_daily_completa
{_FILTRO}
 group by {grp}
 order by piezas desc nulls last
 limit %(limite)s
"""


def _neto(bruto: Any, comision: Any) -> Any:
    """
    Bruto menos comisión. `None` si falta el bruto — restar de nada da nada.

    La comisión de Amazon vale 0 porque falta la Finances API, y los pedidos ML
    con el token caído al crearse también nacieron en 0. Un `neto` que iguale al
    bruto puede ser una venta sin comisión o una comisión que no se midió; por
    eso sale también `comision`, para que se note.
    """
    if bruto is None:
        return None
    return bruto - (comision or 0)


async def consultar_ventas(skus: list[str] | None = None,
                           canal: str | None = None,
                           cuenta: str | None = None,
                           desde: str | None = None,
                           hasta: str | None = None,
                           agrupar_por: str = "canal",
                           detalle: bool = False,
                           diagnostico: bool = False,
                           limite: int | None = None) -> dict[str, Any]:
    skus = cx.normalizar_skus(skus)
    limite = cx.limitar(limite)
    avisos: list[str] = []

    if agrupar_por not in _AGRUPACIONES:
        avisos.append(f"`agrupar_por={agrupar_por}` no existe; se usa 'canal'. "
                      f"Opciones: {sorted(_AGRUPACIONES)}")
        agrupar_por = "canal"

    p = {"skus": skus or None, "canal": canal, "cuenta": cuenta,
         "desde": desde, "hasta": hasta, "limite": limite}

    filas = await cx.consultar(_construir(agrupar_por), p)
    grupos = []
    for f in filas:
        g = {k: cx.iso(v) for k, v in f.items()}
        g["neto"] = _neto(f["bruto"], f["comision"])
        if "cuenta" in g:
            g["cuenta_nombre"] = await cx.nombre_cuenta(f.get("cuenta"), f.get("canal"))
        grupos.append(g)

    # `resumen` SÍ es del universo filtrado (el SQL agrega sin límite), pero
    # `grupos` puede venir cortado. Decirlo evita la conclusión al revés: que
    # los grupos sumen menos que el resumen y parezca un error de cuentas.
    if len(grupos) >= limite:
        avisos.append(
            f"Se topó el límite de {limite} grupos: `grupos` está cortado, "
            f"`resumen` no. Por eso pueden no cuadrar. Sube `limite` (máx. "
            f"{cx.LIMITE_MAXIMO}) o agrupa más grueso.")

    total = (await cx.consultar(_SQL_TOTAL, p))[0]
    resumen = {k: cx.iso(v) for k, v in total.items()}
    resumen["neto"] = _neto(total["bruto"], total["comision"])

    cobertura = [{
        "canal": c["canal"], "fuente": c["fuente"],
        "desde": cx.iso(c["desde"]), "hasta": cx.iso(c["hasta"]),
        "renglones": c["renglones"], "piezas": c["piezas"],
    } for c in await cx.consultar(_SQL_COBERTURA)]

    con_ventas = {c["canal"] for c in cobertura}
    conocidos = {r["channel_id"] for r in await cx.consultar(_SQL_CANALES)}
    sin_datos = sorted(conocidos - con_ventas)

    # ¿La ventana pedida empieza antes de que el canal existiera en los datos?
    primer_dia = {}
    for c in cobertura:
        d = c["desde"]
        if d and (c["canal"] not in primer_dia or d < primer_dia[c["canal"]]):
            primer_dia[c["canal"]] = d
    if desde:
        tempranos = [f"{k} (desde {v})" for k, v in sorted(primer_dia.items())
                     if v > str(desde)]
        if tempranos:
            avisos.append(
                f"La ventana empieza el {desde}, antes del primer dato de: "
                f"{', '.join(tempranos)}. Ahí la ausencia es falta de medición, "
                f"no falta de ventas.")

    if sin_datos:
        avisos.append(
            f"Canales reconocidos SIN una sola fila de ventas: {sin_datos}. "
            f"Walmart MX en particular SÍ vende — lo que falta es la ingesta, "
            f"no las ventas. No se reportan como 0.")

    salida: dict[str, Any] = {
        "pregunta": "ventas por canal",
        "fuente": "channel.sales_daily_completa (kubera) — vista que une "
                  "analytics.sales_daily_hist (hist, sólo Mercado Libre) con "
                  "channel.sales_daily (vivo)",
        "filtros": {"skus": skus or None, "canal": canal, "cuenta": cuenta,
                    "desde": desde, "hasta": hasta, "agrupar_por": agrupar_por},
        "medido_en": cx.ahora(),
        "limite_aplicado": limite,
        "resumen": resumen,
        "grupos": grupos,
        "cobertura": cobertura,
        "canales_sin_datos": sin_datos,
        "avisos": avisos + [
            "`bruto` es revenue; `neto` es revenue − sale_fee. La comisión de "
            "Amazon vale 0 (falta la Finances API), así que ahí neto = bruto.",
            "Mercado Libre son DOS cuentas (BEKURA=Kubera, SANCORFASHION=San "
            "Corpe): un total de 'mercado_libre' las mezcla.",
            "En Amazon, `item_id` es el OrderItemId, no el ASIN.",
        ],
    }

    if detalle:
        salida["detalle"] = [{
            **{k: cx.iso(v) for k, v in d.items()},
            "neto": _neto(d["revenue"], d["sale_fee"]),
            "cuenta_nombre": await cx.nombre_cuenta(d["cuenta"], d["canal"]),
        } for d in await cx.consultar(_SQL_DETALLE, p)]

    if diagnostico:
        fuera = await cx.consultar(_SQL_DIAGNOSTICO)
        salida["fuera_de_la_vista"] = [{
            "canal": f["canal"], "renglones": f["renglones"], "piezas": f["piezas"],
            "desde": cx.iso(f["desde"]), "hasta": cx.iso(f["hasta"]),
        } for f in fuera]
        if fuera:
            salida["avisos"].append(
                "`fuera_de_la_vista`: renglones que channel.sales_daily SÍ tiene "
                "y sales_daily_completa no expone, porque la vista corta por "
                "fecha y el flujo vivo empezó antes del corte.")

    return salida
