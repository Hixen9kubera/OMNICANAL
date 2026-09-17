"""
visitas.py — Capacidad 1: visitas, según lo guardado en Supabase.

LO QUE HAY, Y LO QUE NO
-----------------------
**No existe una serie diaria de visitas.** Las dos fuentes son FOTOS que se
sobrescriben, y por eso cada cifra sale con su sello de medición pegado:

  · `enrich.listing_visits` — por PUBLICACIÓN y por ventana. `dias` es la
    ventana pedida (7 / 30 / 60 / **90**, medido el 17-sep-2026: el encargo
    sólo mencionaba tres) y `dias_datos` cuántos días trae de verdad. Devolver
    `visitas` sin esos dos campos es mentir: 2,727 visitas en una ventana de 60
    días con 32 días de datos no es lo mismo que 2,727 en 60 de 60.
  · `enrich.market_listing_metrics` — por SKU + canal + cuenta + MES. Sólo
    tiene dos periodos (2026-08-01 y 2026-09-01) y su llave
    (sku, canal, cuenta, periodo) es única: 8,165 filas, 8,165 llaves.

Si alguien necesita la historia día por día, no está aquí: hay que pedírsela a
ML (`services/competencia_ml.py::visitas_serie`) y hoy nadie la guarda.

EL SKU NO ESTÁ EN LA TABLA DE VISITAS
--------------------------------------
`listing_visits` va por `listing_id`. Para llegar al SKU hay que pasar por
`channel.listings`, y ahí aparece la trampa que obliga a la columna
`skus_del_listing`: **93 publicaciones de ML sirven a DOS SKUs** (medido el
17-sep-2026 sobre 8,667 listing_id distintos, y varias de ellas tienen
visitas). Las visitas de esa publicación NO se pueden repartir entre sus dos
SKUs — ML las cuenta una sola vez, para el anuncio. Sumarlas a cada SKU las
duplica; dividirlas a la mitad las inventa. Aquí se SUMAN y se MARCA cuánto de
ese total viene de publicaciones compartidas, para que quien lea decida.

`channel.listings` además trae 13,892 filas con `listing_id` nulo (el canal
`general`, que es WooCommerce): se excluyen, no tienen visitas que buscar.

SIN DATO NO ES CERO
-------------------
Un SKU que se pidió y no aparece sale en `sin_dato`, nunca con `visitas: 0`.
Cero visitas y "no lo medimos" son cosas distintas y se deciden distinto.
"""
from __future__ import annotations

from typing import Any

from . import conexion as cx

VENTANAS = (7, 30, 60, 90)

# La última foto de cada (publicación, ventana, cuenta). `listing_visits` guarda
# varias tomas a lo largo del tiempo —del 7-ago al 17-sep— y sin el DISTINCT ON
# la misma publicación saldría repetida con cifras de fechas distintas, que es
# la forma más fácil de sumar dos veces lo mismo.
_SQL_PUBLICACION = """
with ultima as (
    select distinct on (v.listing_id, v.dias, v.cuenta)
           v.listing_id, v.dias, v.cuenta, v.visitas, v.dias_datos, v.consultado_at
      from enrich.listing_visits v
     where (%(ventana)s::int  is null or v.dias   = %(ventana)s::int)
       and (%(cuenta)s::text  is null or v.cuenta = %(cuenta)s::text)
       and (%(listing)s::text is null or v.listing_id = %(listing)s::text)
     order by v.listing_id, v.dias, v.cuenta, v.consultado_at desc
),
pub as (
    select l.listing_id,
           array_agg(distinct l.sku::text order by l.sku::text) as skus,
           min(l.canal)  as canal,
           min(l.status) as status,
           max(l.price)  as precio
      from channel.listings l
     where l.listing_id is not null
     group by l.listing_id
)
select u.listing_id, u.dias, u.cuenta, u.visitas, u.dias_datos, u.consultado_at,
       p.skus, p.canal, p.status, p.precio
  from ultima u
  left join pub p on p.listing_id = u.listing_id
 where (%(skus)s::text[] is null or p.skus && %(skus)s::text[])
   and (%(canal)s::text  is null or p.canal = %(canal)s::text)
 order by u.visitas desc nulls last, u.listing_id
 limit %(limite)s
"""

_SQL_MENSUAL = """
select m.sku::text as sku, m.canal, m.cuenta, m.periodo, m.listing_id, m.title,
       m.visits_30d, m.units_30d, m.sale_price, m.list_price, m.estado,
       m.fuente_unidades, m.metrics_updated_at
  from enrich.market_listing_metrics m
 where (%(skus)s::citext[] is null or m.sku   = any(%(skus)s::citext[]))
   and (%(canal)s::text    is null or m.canal = %(canal)s::text)
   and (%(cuenta)s::text   is null or m.cuenta = %(cuenta)s::text)
   and (%(periodo)s::date  is null or m.periodo = %(periodo)s::date)
 order by m.periodo desc, m.visits_30d desc nulls last
 limit %(limite)s
"""

_SQL_COBERTURA = """
select 'listing_visits' as tabla, min(consultado_at) as desde, max(consultado_at) as hasta,
       count(*) as filas, array_agg(distinct dias order by dias) as ventanas
  from enrich.listing_visits
union all
select 'market_listing_metrics', min(metrics_updated_at), max(metrics_updated_at),
       count(*), null
  from enrich.market_listing_metrics
"""


async def consultar_visitas(skus: list[str] | None = None,
                            listing_id: str | None = None,
                            canal: str | None = None,
                            cuenta: str | None = None,
                            ventana_dias: int | None = None,
                            periodo: str | None = None,
                            limite: int | None = None) -> dict[str, Any]:
    skus = cx.normalizar_skus(skus)
    limite = cx.limitar(limite)
    avisos: list[str] = []

    if ventana_dias and int(ventana_dias) not in VENTANAS:
        avisos.append(
            f"La ventana {ventana_dias} no existe en listing_visits; las medidas "
            f"son {VENTANAS}. Se ignora el filtro."
        )
        ventana_dias = None

    p = {"skus": skus or None, "listing": listing_id, "canal": canal,
         "cuenta": cuenta, "ventana": ventana_dias, "limite": limite}
    filas = await cx.consultar(_SQL_PUBLICACION, p)

    por_publicacion: list[dict[str, Any]] = []
    for f in filas:
        del_listing = list(f["skus"] or [])
        por_publicacion.append({
            "listing_id": f["listing_id"],
            "canal": f["canal"],
            "cuenta": f["cuenta"],
            "cuenta_nombre": await cx.nombre_cuenta(f["cuenta"], f["canal"]),
            "sku": del_listing[0] if len(del_listing) == 1 else None,
            "skus_del_listing": del_listing or None,
            "compartida": len(del_listing) > 1,
            "status": f["status"],
            "precio": f["precio"],
            "visitas": f["visitas"],
            "ventana_dias": f["dias"],
            "dias_con_datos": f["dias_datos"],
            "consultado_at": cx.iso(f["consultado_at"]),
        })

    # Truncar en silencio es cómo se saca una conclusión de media tabla creyendo
    # que era la tabla entera. Si se topó el límite, se dice.
    if len(filas) >= limite:
        avisos.append(
            f"Se topó el límite de {limite} publicaciones y hay más: los totales "
            f"por SKU son de ESTA tanda, no del universo. Sube `limite` (máx. "
            f"{cx.LIMITE_MAXIMO}) o filtra por sku/cuenta/ventana.")

    huerfanas = [r["listing_id"] for r in por_publicacion if not r["skus_del_listing"]]
    if huerfanas:
        avisos.append(
            f"{len(huerfanas)} publicación(es) con visitas no aparecen en "
            f"channel.listings, así que se quedan sin SKU: {huerfanas[:5]}"
        )

    # ── Total por SKU, con la parte compartida separada ─────────────────────
    acum: dict[str, dict[str, Any]] = {}
    for r in por_publicacion:
        for sku in (r["skus_del_listing"] or []):
            d = acum.setdefault(sku, {
                "sku": sku, "visitas": 0, "visitas_en_listings_compartidos": 0,
                "publicaciones": 0, "cuentas": [], "ventanas": [],
                "medido_mas_reciente": None, "dias_con_datos": [],
            })
            if r["visitas"] is not None:
                d["visitas"] += r["visitas"]
                if r["compartida"]:
                    d["visitas_en_listings_compartidos"] += r["visitas"]
            d["publicaciones"] += 1
            if r["cuenta"] and r["cuenta"] not in d["cuentas"]:
                d["cuentas"].append(r["cuenta"])
            if r["ventana_dias"] and r["ventana_dias"] not in d["ventanas"]:
                d["ventanas"].append(r["ventana_dias"])
            if r["dias_con_datos"] is not None:
                d["dias_con_datos"].append(r["dias_con_datos"])
            if r["consultado_at"] and (d["medido_mas_reciente"] is None
                                       or r["consultado_at"] > d["medido_mas_reciente"]):
                d["medido_mas_reciente"] = r["consultado_at"]

    por_sku = sorted(acum.values(), key=lambda d: d["visitas"], reverse=True)
    for d in por_sku:
        d["ventanas"].sort()
        # Mezclar ventanas distintas en un total es sumar peras con manzanas; si
        # pasa, se dice, en vez de devolver una cifra que nadie puede interpretar.
        if len(d["ventanas"]) > 1:
            d["aviso"] = ("Suma ventanas distintas " + str(d["ventanas"]) +
                          ": fija `ventana_dias` para un total comparable.")
        if d["visitas_en_listings_compartidos"]:
            d["aviso_compartido"] = (
                "Parte de estas visitas vienen de publicaciones que sirven a más "
                "de un SKU; ML las cuenta una vez, para el anuncio.")
        d["dias_con_datos"] = (min(d["dias_con_datos"]) if d["dias_con_datos"] else None)

    mensual = await cx.consultar(_SQL_MENSUAL, {
        "skus": skus or None, "canal": canal, "cuenta": cuenta,
        "periodo": periodo, "limite": limite})
    mensual = [{
        "sku": m["sku"], "canal": m["canal"], "cuenta": m["cuenta"],
        "cuenta_nombre": await cx.nombre_cuenta(m["cuenta"], m["canal"]),
        "periodo": cx.iso(m["periodo"]),
        "listing_id": m["listing_id"], "titulo": m["title"],
        "visitas_30d": m["visits_30d"], "unidades_30d": m["units_30d"],
        "precio_venta": m["sale_price"], "precio_lista": m["list_price"],
        "estado": m["estado"], "fuente_unidades": m["fuente_unidades"],
        "medido_en": cx.iso(m["metrics_updated_at"]),
    } for m in mensual]

    if len(mensual) >= limite:
        avisos.append(f"La foto mensual también se topó en {limite} renglones.")

    con_dato = {r["sku"] for r in por_sku} | {m["sku"] for m in mensual}
    sin_dato = [s for s in skus if s not in con_dato]

    cob = await cx.consultar(_SQL_COBERTURA)
    return {
        "pregunta": "visitas",
        "fuente": {
            "por_publicacion": "enrich.listing_visits × channel.listings (kubera)",
            "mensual": "enrich.market_listing_metrics (kubera)",
        },
        "cobertura": [{
            "tabla": c["tabla"], "desde": cx.iso(c["desde"]),
            "hasta": cx.iso(c["hasta"]), "filas": c["filas"],
            "ventanas": c["ventanas"],
        } for c in cob],
        "medido_en": cx.ahora(),
        "limite_aplicado": limite,
        "por_publicacion": por_publicacion,
        "por_sku": por_sku,
        "mensual": mensual,
        "sin_dato": sin_dato,
        "avisos": avisos + [
            "No hay serie diaria de visitas: las dos fuentes son fotos que se "
            "sobrescriben. Cada cifra trae su sello de medición.",
            "`visitas` va con `ventana_dias` y `dias_con_datos`: una ventana de "
            "60 días con 32 días de datos no es una ventana de 60.",
        ],
    }
