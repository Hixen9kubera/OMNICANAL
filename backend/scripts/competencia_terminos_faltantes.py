"""Captura los términos más buscados de las subcategorías que se quedaron sin ellos.

POR QUÉ FALTAN
--------------
`/trends` es API de ML y necesita token; el token vive en MySQL. Cuando la
captura del 12-ago agotó la cuota de conexiones de Hostinger (500/hora), el token
dejó de leerse y `tendencias()` empezó a devolver [] — que el código interpreta,
correctamente para su contrato, como "ML no publica términos de esta categoría".
Resultado: de 158 subcategorías solo 104 quedaron con términos, y el panel decía
"Mercado Libre no publica términos de búsqueda de esta categoría" en casos donde
sí los publica. Mancuernas (MLM187612) es el ejemplo: 0 guardados, 50 en vivo.

DISTINGUIR LAS DOS CAUSAS es todo el punto de este script: pregunta a `/trends`
por cada categoría sin términos y separa las que de verdad no tienen de las que
se perdieron. Las primeras se dejan en paz; las segundas se guardan.

GRATIS: solo API de ML. Con la caché de token, una consulta a MySQL cada 5 min.

USO
---
    backend/.venv/bin/python backend/scripts/competencia_terminos_faltantes.py \\
        --raices MLM1276 MLM186863 MLM44011 [--dry-run] [--minimo 10]
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from services import competencia_ml, competencia_store, supabase_db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raices", nargs="+", required=True)
    ap.add_argument("--minimo", type=int, default=1,
                    help="Re-pide también las que tengan MENOS de N términos. "
                         "Con 10 se cubren las que el panel muestra a medias.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not supabase_db.disponible():
        print("✗ Falta SUPABASE_DB_URL.")
        return 2

    marc = "(" + ",".join(["%s"] * len(args.raices)) + ")"
    faltan = supabase_db.fetch_all(f"""
      with sub as (select distinct v.categoria_id, max(v.categoria_nombre) nombre,
                          count(*) skus
                     from enrich.market_skus_v v
                    where v.raiz_id in {marc} and v.activo
                    group by 1)
      select sub.categoria_id, sub.nombre, sub.skus,
             coalesce(jsonb_array_length(t.terminos), 0) tiene
        from sub left join enrich.market_terms t using (categoria_id)
       where coalesce(jsonb_array_length(t.terminos), 0) < %s
       order by sub.skus desc""", tuple(args.raices) + (args.minimo,))

    print("═══ Competencia · términos faltantes ═══")
    print(f"subcategorías con menos de {args.minimo} términos: {len(faltan)}")
    print("gratis: /trends es API de ML\n", flush=True)
    if not faltan:
        print("Nada que hacer.")
        return 0

    if competencia_ml._token("bekura") is None:
        print("✗ Sin token de ML (probablemente MySQL sin cuota). Reintenta más tarde.")
        return 3

    t0 = time.time()
    recuperadas = vacias = 0
    for i, f in enumerate(faltan, 1):
        t = competencia_ml.tendencias(f["categoria_id"]) or []
        if not t:
            vacias += 1
            print(f"  {i:>3}/{len(faltan)} {f['categoria_id']:<11} "
                  f"{(f['nombre'] or '')[:26]:<26} ML no publica", flush=True)
            continue
        recuperadas += 1
        print(f"  {i:>3}/{len(faltan)} {f['categoria_id']:<11} "
              f"{(f['nombre'] or '')[:26]:<26} {len(t)} términos"
              + ("  [dry-run]" if args.dry_run else ""), flush=True)
        if not args.dry_run:
            competencia_store.reemplazar_terminos(
                f["categoria_id"], competencia_store.periodo_actual(),
                [{"termino": x["keyword"], "posicion": i2}
                 for i2, x in enumerate(t, start=1)])

    print(f"\nLISTO en {time.time() - t0:.0f}s")
    print(f"  recuperadas          : {recuperadas}  ← se habían perdido")
    print(f"  ML no publica de ellas: {vacias}  ← no es un hueco nuestro")
    if not args.dry_run:
        fin = supabase_db.fetch_one(f"""
          with sub as (select distinct categoria_id from enrich.market_skus_v
                        where raiz_id in {marc} and activo)
          select count(*) subcats, count(t.categoria_id) con_terminos,
                 sum(case when jsonb_array_length(t.terminos)>=10 then 1 else 0 end) con_10
            from sub left join enrich.market_terms t using (categoria_id)""",
            tuple(args.raices))
        print(f"\n  {fin['con_terminos']} de {fin['subcats']} subcategorías con términos "
              f"· {fin['con_10']} con 10 o más")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
