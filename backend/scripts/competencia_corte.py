"""Corte de una categoría RAÍZ: qué falta y —lo que importa— por qué falta.

Las tres cifras que se piden siempre:
    Top de subcategorías      X de Y
    Search term por SKU       X de Y   (y cuántos ya se MIDIERON)
    10 keywords por subcat    X de Y

DISTINGUIR LAS DOS CAUSAS DE UN HUECO
-------------------------------------
Una subcategoría sin ranking puede serlo por dos razones OPUESTAS:

  · **ML no publica más vendidos de ella.** Su `/highlights` viene vacío. No es
    un fallo nuestro y reintentar no cambia nada. Medido: de las 7 pendientes de
    Herramientas, 6 son de este tipo (Canteadoras, Grúas Viajeras, Fresadoras
    CNC, Hornos para Fundición y dos "Otros").
  · **No la hemos capturado.** ML sí tiene ranking. Ahí sí vale gastar.

Confundirlas hace perseguir fantasmas o dar por perdido lo que sí se puede
traer, así que este script pregunta por cada hueco a `/highlights`, que es gratis.

USO
---
    backend/.venv/bin/python backend/scripts/competencia_corte.py MLM1276
    backend/.venv/bin/python backend/scripts/competencia_corte.py MLM1276 MLM186863 MLM44011
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.ERROR)

from services import competencia_ml, supabase_db  # noqa: E402

COSTO_PAGINA = 0.007


def corte(raiz: str) -> None:
    r = supabase_db.fetch_one("""
     with s   as (select * from enrich.market_skus_v where raiz_id = %s and activo),
          sub as (select distinct categoria_id from s)
     select (select max(raiz_nombre) from s) nombre,
            (select count(*) from s)   skus,
            (select count(*) from sub) subs,
            (select count(*) from sub where exists (select 1 from enrich.market_bestsellers b
                where b.categoria_id = sub.categoria_id)) con_top,
            (select count(*) from s where termino_general is not null) con_term,
            (select count(*) from s
               join enrich.market_sku_config c on c.sku = s.sku and c.canal = s.canal
               join enrich.market_search_term st on st.id = c.termino_id
              where st.medido_en is not null) medidos,
            (select count(*) from sub join enrich.market_terms t using (categoria_id)
              where jsonb_array_length(t.terminos) >= 10) kw10
    """, (raiz,))
    if not r or not r["subs"]:
        print(f"\n{raiz}: sin SKUs activos.")
        return

    print(f"\n═══ {r['nombre']} ({raiz}) ═══")
    print(f"  Top de subcategorías     {r['con_top']:>3} de {r['subs']}")
    print(f"  Search term por SKU      {r['con_term']:>3} de {r['skus']}")
    print(f"  Search term medido       {r['medidos']:>3} de {r['skus']}")
    print(f"  10 keywords por subcat   {r['kw10']:>3} de {r['subs']}")

    faltan = supabase_db.fetch_all("""
      select distinct v.categoria_id, v.categoria_nombre, count(*) skus
        from enrich.market_skus_v v
       where v.raiz_id = %s and v.activo
         and not exists (select 1 from enrich.market_bestsellers b
                          where b.categoria_id = v.categoria_id)
       group by 1,2 order by 3 desc, 2""", (raiz,))
    if not faltan:
        print("\n  Sin huecos: todas las subcategorías tienen top.")
        return

    print(f"\n  {len(faltan)} subcategorías sin top. Preguntando a /highlights "
          f"(gratis) por cada una…", flush=True)
    no_publica, si_hay = [], []
    for f in faltan:
        n = len(competencia_ml.mas_vendidos_categoria(f["categoria_id"]) or [])
        (si_hay if n else no_publica).append((f, n))

    if no_publica:
        print(f"\n  ── {len(no_publica)}: ML NO PUBLICA más vendidos. No es un hueco "
              f"nuestro; reintentar no cambia nada.")
        for f, _ in no_publica:
            print(f"     {f['categoria_id']:<11} {f['categoria_nombre'][:34]:<34} "
                  f"{f['skus']} SKUs")
    if si_hay:
        print(f"\n  ── {len(si_hay)}: ML SÍ tiene ranking y nos falta capturarlo. "
              f"Cuesta ${len(si_hay) * COSTO_PAGINA:.2f}.")
        for f, n in si_hay:
            print(f"     {f['categoria_id']:<11} {f['categoria_nombre'][:34]:<34} "
                  f"{f['skus']} SKUs · /highlights={n}")
        ids = " ".join(f["categoria_id"] for f, _ in si_hay)
        print(f"\n     backend/.venv/bin/python backend/scripts/"
              f"competencia_rankings_apify.py \\\n       --raiz {raiz} --solo {ids} --execute")


def main() -> int:
    raices = [a.strip().upper() for a in sys.argv[1:] if a.strip()]
    if not raices:
        print(__doc__)
        return 1
    if not supabase_db.disponible():
        print("✗ Falta SUPABASE_DB_URL.")
        return 2
    for raiz in raices:
        corte(raiz)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
