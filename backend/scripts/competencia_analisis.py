"""Análisis de oportunidad sobre las categorías ya capturadas. Los HECHOS en SQL.

TRES PREGUNTAS, y cada una tiene su tabla:

  1. QUIÉN PUEDE LLEGAR AL TOP. Nuestros SKUs cuya subcategoría ya está medida,
     comparados contra el líder y la mediana del top: brecha de precio, de
     visitas y de ventas. El orden no es por "cuál nos gusta" sino por cuál está
     MÁS CERCA — el que ya tiene tráfico y vende, y solo está caro, es otro
     problema que el que no lo ve nadie.

  2. LOS QUE NADIE VE. La pregunta original era "activas con 0 visitas", pero
     medido en las tres categorías eso es UN producto: de 541 publicaciones, 404
     están PAUSADAS. El 0 en visitas casi siempre es consecuencia de la pausa, no
     una falla de posicionamiento. Se reportan las dos cosas por separado para no
     confundir la causa.

  3. LO QUE NI SIQUIERA ESTÁ PUBLICADO. Subcategorías con mediana de precio alta
     donde SÍ tenemos producto en catálogo y NO hay publicación. El ticket alto
     es el filtro porque una venta ahí paga muchas de ticket bajo.

De todos se reporta si están publicados, en qué cuenta, y su stock FULL — sin eso
una recomendación de "súbelo al top" puede ser sobre algo que no se puede surtir.

El texto de las recomendaciones lo escribe `competencia_analisis_ia.py`; aquí
solo salen números, para que se puedan auditar sin pasar por un LLM.

USO
---
    backend/.venv/bin/python backend/scripts/competencia_analisis.py \\
        --raices MLM1276 MLM186863 MLM44011 [--json salida.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import db, supabase_db as sdb  # noqa: E402

TICKET_ALTO = 700.0


def _en(raices: list[str]) -> tuple[str, tuple]:
    return "(" + ",".join(["%s"] * len(raices)) + ")", tuple(raices)


def stock_full(skus: list[str]) -> dict[str, list[dict]]:
    """Stock FULL y propio por SKU y cuenta, desde channel.listings."""
    if not skus:
        return {}
    out: dict[str, list[dict]] = {}
    for r in sdb.fetch_all("""
        select l.sku, a.legacy_code cuenta, l.listing_id, l.status,
               l.stock_full, l.stock_own, l.is_fulfillment
          from channel.listings l
          left join core.accounts a on a.id = l.account_id
         where l.canal = 'mercado_libre' and l.sku = any(%s)""", (skus,)):
        out.setdefault(r["sku"], []).append(r)
    return out


def top_alcanzable(raices: list[str]) -> list[dict]:
    """Nuestros SKUs contra el top de su subcategoría."""
    marc, params = _en(raices)
    return sdb.fetch_all(f"""
    with top as (
      select categoria_id,
             percentile_cont(0.5) within group (order by precio)      p50,
             min(precio) filter (where posicion = 1)                  precio_lider,
             max(vendidos) filter (where posicion = 1)                vend_lider,
             max(visitas_30d) filter (where posicion = 1)             vis_lider,
             sum(vendidos)                                            vend_mercado,
             sum(visitas_30d)                                         vis_mercado,
             count(*)                                                 n_top
        from enrich.market_bestsellers
       group by 1),
      -- DOS pasos, y el orden importa.
      --
      -- 1) `ultimo`: una fila por (sku, cuenta) con el PERIODO MÁS RECIENTE.
      --    `market_listing_metrics` guarda una foto POR MES (su PK lleva
      --    `periodo`). Sin este paso, desde el día 1 de cada mes cada
      --    publicación aparece dos veces y el paso 2 podía quedarse con la foto
      --    de agosto por tener más visitas que la de septiembre a medio llenar.
      --    El 1-sep-2026 este mismo descuido tumbó el cron de visitas.
      --
      -- 2) `mio`: de las publicaciones YA colapsadas por mes, la de MÁS visitas.
      --    Sumar las dos tiendas inflaría el tráfico de un SKU por estar
      --    publicado dos veces.
      ultimo as (
        select distinct on (m.sku, m.cuenta) m.sku, m.cuenta, m.listing_id,
               m.estado, m.sale_price, m.list_price, m.visits_30d, m.units_30d,
               m.title
          from enrich.market_listing_metrics m
         order by m.sku, m.cuenta, m.periodo desc),
      mio as (
        select distinct on (u.sku) u.*
          from ultimo u
         order by u.sku, u.visits_30d desc nulls last)
    select v.sku, v.nombre, v.categoria_id, v.categoria_nombre, v.termino_general,
           mio.cuenta, mio.listing_id, mio.estado, mio.title,
           mio.sale_price::float precio, mio.visits_30d visitas, mio.units_30d unidades,
           t.p50::float mediana, t.precio_lider::float precio_lider,
           t.vend_lider, t.vis_lider, t.vend_mercado, t.vis_mercado, t.n_top,
           case when t.p50 > 0 and mio.sale_price > 0
                then round(((mio.sale_price / t.p50) - 1) * 100)::int end brecha_pct,
           case when t.vis_lider > 0 and mio.visits_30d > 0
                then round((mio.visits_30d::numeric / t.vis_lider) * 100)::int end vis_vs_lider_pct,
           case when t.vend_lider > 0 and mio.units_30d > 0
                then round((mio.units_30d::numeric / t.vend_lider) * 100)::int end vend_vs_lider_pct,
           exists (select 1 from enrich.market_bestsellers b
                    where b.categoria_id = v.categoria_id and b.sku_nuestro = v.sku) ya_en_top
      from enrich.market_skus_v v
      join mio on mio.sku = v.sku
      join top t  on t.categoria_id = v.categoria_id
     where v.raiz_id in {marc} and v.activo
     order by coalesce(mio.visits_30d,0) desc, coalesce(mio.units_30d,0) desc
    """, params)


def sin_visitas(raices: list[str]) -> list[dict]:
    """Publicaciones sin tráfico, separando la causa: pausada o invisible."""
    marc, params = _en(raices)
    return sdb.fetch_all(f"""
    with top as (select categoria_id,
                        percentile_cont(0.5) within group (order by precio) p50
                   from enrich.market_bestsellers where precio > 0 group by 1),
    -- Una fila por (sku, cuenta): la foto del mes MÁS RECIENTE. El join de abajo
    -- iba crudo contra la tabla, que es MENSUAL, así que desde el día 1 cada
    -- publicación salía repetida en el reporte — y con la fila de septiembre a
    -- medio llenar contando como "sin visitas".
    ultimo as (
      select distinct on (m.sku, m.cuenta) m.sku, m.cuenta, m.listing_id,
             m.estado, m.sale_price, m.visits_30d, m.units_30d, m.title
        from enrich.market_listing_metrics m
       order by m.sku, m.cuenta, m.periodo desc)
    select v.sku, v.nombre, v.categoria_id, v.categoria_nombre, v.termino_general,
           m.cuenta, m.listing_id, m.estado, m.title,
           m.sale_price::float precio, m.visits_30d visitas, m.units_30d unidades,
           t.p50::float mediana,
           case when t.p50 > 0 and m.sale_price > 0
                then round(((m.sale_price / t.p50) - 1) * 100)::int end brecha_pct,
           case when m.estado = 'active' then 'activa sin tráfico'
                else 'pausada' end causa
      from enrich.market_skus_v v
      join ultimo m on m.sku = v.sku
      left join top t on t.categoria_id = v.categoria_id
     where v.raiz_id in {marc} and v.activo
       and coalesce(m.visits_30d, 0) = 0
     order by (m.estado = 'active') desc, t.p50 desc nulls last
    """, params)


def no_publicados(raices: list[str]) -> list[dict]:
    """Catálogo sin publicación en subcategorías de ticket alto.

    El catálogo vive en MySQL (`categorias_ml` + `ml_progress`); el mercado en
    Supabase. Por eso el cruce se hace en Python y no en un JOIN.
    """
    marc, params = _en(raices)
    cats = sdb.fetch_all(f"""
      with top as (select categoria_id,
                          percentile_cont(0.5) within group (order by precio) p50,
                          sum(vendidos) vend, sum(visitas_30d) vis,
                          min(titulo) filter (where posicion = 1) lider
                     from enrich.market_bestsellers where precio > 0 group by 1)
      select distinct v.categoria_id, max(v.categoria_nombre) nombre,
             t.p50::float mediana, t.vend, t.vis, t.lider
        from enrich.market_skus_v v join top t on t.categoria_id = v.categoria_id
       where v.raiz_id in {marc} and v.activo and t.p50 >= {TICKET_ALTO}
       group by 1, t.p50, t.vend, t.vis, t.lider
       order by t.p50 desc""", params)
    if not cats:
        return []
    ids = [c["categoria_id"] for c in cats]
    marcas = ",".join(["%s"] * len(ids))
    try:
        filas = db.fetch_all(f"""
            SELECT c.sku, c.category_id, p.nombre,
                   MAX(mp.ml_item_id) ml_item_id
              FROM categorias_ml c
              LEFT JOIN productos p    ON p.sku = c.sku
              LEFT JOIN ml_progress mp ON mp.sku = c.sku
                    AND mp.ml_item_id IS NOT NULL AND mp.ml_item_id <> ''
             WHERE c.category_id IN ({marcas})
             GROUP BY c.sku, c.category_id, p.nombre""", tuple(ids))
    except Exception as exc:  # noqa: BLE001
        print(f"  ! catálogo MySQL no disponible: {str(exc)[:90]}", file=sys.stderr)
        return []
    porcat = {c["categoria_id"]: c for c in cats}
    out = []
    for f in filas:
        if f.get("ml_item_id"):
            continue                      # ya publicado
        c = porcat.get(f["category_id"]) or {}
        out.append({"sku": f["sku"], "nombre": f.get("nombre"),
                    "categoria_id": f["category_id"],
                    "categoria_nombre": c.get("nombre"),
                    "mediana": c.get("mediana"), "vend_mercado": c.get("vend"),
                    "vis_mercado": c.get("vis"), "lider": c.get("lider")})
    out.sort(key=lambda x: -(x.get("mediana") or 0))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raices", nargs="+", required=True)
    ap.add_argument("--json", help="Guarda el resultado crudo para el paso de IA")
    args = ap.parse_args()
    raices = [r.strip().upper() for r in args.raices]

    if not sdb.disponible():
        print("✗ Falta SUPABASE_DB_URL.")
        return 2

    t1 = top_alcanzable(raices)
    t2 = sin_visitas(raices)
    t3 = no_publicados(raices)
    stocks = stock_full(sorted({r["sku"] for r in t1 + t2}))

    def pinta_stock(sku: str) -> str:
        ls = stocks.get(sku) or []
        if not ls:
            return "sin listing"
        return " · ".join(
            f"{l['cuenta'] or '?'}:{l['status'] or '?'} FULL={l['stock_full'] if l['stock_full'] is not None else '—'}"
            f"/propio={l['stock_own'] if l['stock_own'] is not None else '—'}" for l in ls)

    print("═══ 1. CANDIDATOS A TOP DE SU SUBCATEGORÍA ═══")
    print(f"{'SKU':<20} {'estado':<8} {'precio':>8} {'med':>7} {'brecha':>7} "
          f"{'vis':>6} {'%líder':>7} {'uds':>4} {'subcategoría':<24}")
    for r in t1[:25]:
        print(f"{r['sku']:<20} {(r['estado'] or '')[:8]:<8} "
              f"{(r['precio'] or 0):>8.0f} {(r['mediana'] or 0):>7.0f} "
              f"{(str(r['brecha_pct'])+'%' if r['brecha_pct'] is not None else '—'):>7} "
              f"{(r['visitas'] or 0):>6} "
              f"{(str(r['vis_vs_lider_pct'])+'%' if r['vis_vs_lider_pct'] is not None else '—'):>7} "
              f"{(r['unidades'] or 0):>4} {(r['categoria_nombre'] or '')[:24]:<24}")
        print(f"{'':22}stock: {pinta_stock(r['sku'])}")

    print(f"\n═══ 2. SIN TRÁFICO ({len(t2)} publicaciones) ═══")
    act = [r for r in t2 if r["causa"] == "activa sin tráfico"]
    pau = [r for r in t2 if r["causa"] == "pausada"]
    print(f"  activas sin tráfico: {len(act)}   ·   pausadas: {len(pau)}")
    for r in act[:15]:
        print(f"   {r['sku']:<20} {(r['categoria_nombre'] or '')[:26]:<26} "
              f"${(r['precio'] or 0):>7.0f} vs med ${(r['mediana'] or 0):>7.0f} "
              f"({r['brecha_pct'] if r['brecha_pct'] is not None else '—'}%)")
        print(f"{'':23}término: {r['termino_general'] or '—'} · stock: {pinta_stock(r['sku'])}")

    print(f"\n═══ 3. NO PUBLICADOS EN SUBCATEGORÍAS DE TICKET ≥ ${TICKET_ALTO:.0f} "
          f"({len(t3)}) ═══")
    for r in t3[:25]:
        print(f"   {r['sku']:<20} med ${(r['mediana'] or 0):>7.0f} "
              f"vend={r['vend_mercado'] or '—':<7} vis={r['vis_mercado'] or '—':<7} "
              f"{(r['categoria_nombre'] or '')[:26]}")
        print(f"{'':23}{(r['nombre'] or '(sin nombre)')[:70]}")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"raices": raices, "top": t1, "sin_visitas": t2, "no_publicados": t3,
             "stock": {k: v for k, v in stocks.items()}},
            ensure_ascii=False, default=str, indent=1))
        print(f"\ncrudo → {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
