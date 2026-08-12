"""Rellena las visitas que faltan en `enrich.market_bestsellers`. GRATIS.

POR QUÉ EXISTE
--------------
El 12-ago, a media captura de Deportes y Fitness, MySQL empezó a rechazar
conexiones («User has exceeded the max_connections_per_hour resource», límite
500/h del plan de Hostinger). El token de ML vive en MySQL, así que sin conexión
no hay token y sin token no hay `/visits`: **537 de 964 filas se guardaron sin
visitas**. La causa está corregida (caché de token en `competencia_ml`), pero las
filas ya escritas hay que rellenarlas.

No hay que volver a raspar ni pagar Apify: las visitas salen de la API de ML, que
es gratis. Solo se piden las que faltan.

CUIDADO CON LA CUOTA
--------------------
Con la caché de token, esto hace ~1 consulta a MySQL cada 5 minutos en vez de 2
por fila. Aun así conviene no correrlo junto con una captura.

USO
---
    backend/.venv/bin/python backend/scripts/competencia_revisitas_ranking.py --raiz MLM1276
    ... --dry-run          # solo dice cuántas faltan
    ... --limite 100       # corta después de N filas
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from services import competencia_ml, supabase_db  # noqa: E402

LOTE = 40


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raiz", help="Solo las subcategorías de esta raíz")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limite", type=int, default=0)
    args = ap.parse_args()

    if not supabase_db.disponible():
        print("✗ Falta SUPABASE_DB_URL.")
        return 2

    cond, params = "b.visitas_30d IS NULL AND b.externo_id IS NOT NULL", []
    if args.raiz:
        cond += (" AND EXISTS (SELECT 1 FROM enrich.market_skus_v v "
                 "WHERE v.categoria_id = b.categoria_id AND v.raiz_id = %s)")
        params.append(args.raiz)
    filas = supabase_db.fetch_all(
        f"SELECT b.canal, b.categoria_id, b.nivel, b.posicion, b.externo_id "
        f"  FROM enrich.market_bestsellers b WHERE {cond} "
        f" ORDER BY b.categoria_id, b.posicion", tuple(params))
    if args.limite:
        filas = filas[:args.limite]

    total = supabase_db.fetch_one(
        "SELECT count(*) n, count(visitas_30d) v FROM enrich.market_bestsellers")
    print("═══ Competencia · rellenar visitas del ranking ═══")
    print(f"tabla completa : {total['v']} de {total['n']} filas con visitas")
    print(f"a rellenar     : {len(filas)}"
          + (f"  (raíz {args.raiz})" if args.raiz else ""))
    print("gratis: /visits es API de ML\n", flush=True)
    if args.dry_run or not filas:
        if args.dry_run:
            print("[dry-run] no se escribió nada.")
        return 0

    # Prueba de vida ANTES de gastar tiempo: si el token no se puede leer, todas
    # las llamadas van a devolver None y el barrido escribiría ceros disfrazados.
    if competencia_ml._token("bekura") is None:
        print("✗ No hay token de ML (MySQL sin cuota, probablemente). "
              "La cuota se libera al cambio de hora — vuelve a intentar.")
        return 3

    ok = fallos = 0
    t0 = time.time()
    for i in range(0, len(filas), LOTE):
        trozo = filas[i:i + LOTE]
        vis = await asyncio.gather(
            *(asyncio.to_thread(competencia_ml.visitas_30d, f["externo_id"])
              for f in trozo), return_exceptions=True)
        for f, v in zip(trozo, vis):
            if not isinstance(v, int):
                fallos += 1
                continue
            supabase_db.execute(
                "UPDATE enrich.market_bestsellers SET visitas_30d = %s "
                " WHERE canal=%s AND categoria_id=%s AND nivel=%s AND posicion=%s",
                (v, f["canal"], f["categoria_id"], f["nivel"], f["posicion"]))
            ok += 1
        print(f"  {min(i + LOTE, len(filas)):>4}/{len(filas)} · rellenadas={ok} "
              f"· sin dato={fallos} · {time.time() - t0:.0f}s", flush=True)

    fin = supabase_db.fetch_one(
        "SELECT count(*) n, count(visitas_30d) v FROM enrich.market_bestsellers")
    print(f"\nLISTO en {time.time() - t0:.0f}s · rellenadas {ok} · sin dato {fallos}")
    print(f"tabla completa: {fin['v']} de {fin['n']} filas con visitas")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
