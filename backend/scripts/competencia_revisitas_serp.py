"""Rellena `visitas_30d` en los resultados del buscador. GRATIS (API de ML).

POR QUÉ HACE FALTA
------------------
La migración 0017 retiró la columna con el criterio de "casi vacía y sin
lectores" (4 de 1,816 filas). El llenado era cierto pero la conclusión no: el
pipeline SÍ pide esas visitas —`competencia_buscar_apify.py:121` llama a
`enriquecer_visitas` sobre cada resultado— y estaban vacías porque el subidor
viejo no incluía la columna en su lista. Al quitarla, el enriquecido se seguía
pagando en tiempo y el resultado se tiraba. La 0018 la devolvió; esto llena lo
que ya estaba guardado.

Sin visitas, el bloque de BÚSQUEDA GENERAL del panel muestra los diez resultados
con su precio pero no dice cuánto tráfico se lleva cada posición, que es lo que
hace accionable la comparación.

CUOTA
-----
Una llamada por fila. Con la caché de token de `competencia_ml` esto no toca
MySQL más de una vez cada 5 min, pero conviene no correrlo junto con una captura.

USO
---
    backend/.venv/bin/python backend/scripts/competencia_revisitas_serp.py [--dry-run]
    ... --tope-posicion 10    # solo las primeras 10 de cada término
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

from services import competencia_captura, competencia_ml, supabase_db  # noqa: E402

LOTE = 40


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tope-posicion", type=int, default=0,
                    help="Solo las primeras N posiciones de cada término. El "
                         "panel pinta 10, así que con eso alcanza para la vista.")
    ap.add_argument("--limite", type=int, default=0)
    args = ap.parse_args()

    if not supabase_db.disponible():
        print("✗ Falta SUPABASE_DB_URL.")
        return 2

    # Se rellenan las NULL y también los CEROS de ids de catálogo: un `/visits`
    # sobre un id de catálogo responde 0 sin error, así que ese cero no es una
    # medición — es la señal de que faltó resolver el id real. Medido: 1,893 de
    # los 1,894 ceros venían de una URL `/p/` o de un `MLMU`.
    cond = ("externo_id IS NOT NULL AND (visitas_30d IS NULL OR "
            "(visitas_30d = 0 AND (url LIKE '%/p/%' OR externo_id LIKE 'MLMU%')))")
    if args.tope_posicion:
        cond += f" AND posicion <= {int(args.tope_posicion)}"
    filas = supabase_db.fetch_all(
        f"SELECT termino_id, externo_id, posicion, url FROM enrich.market_search_results "
        f" WHERE {cond} ORDER BY posicion, termino_id")
    if args.limite:
        filas = filas[:args.limite]

    t = supabase_db.fetch_one(
        "SELECT count(*) n, count(visitas_30d) v FROM enrich.market_search_results")
    print("═══ Competencia · visitas de los resultados del buscador ═══")
    print(f"tabla     : {t['v']} de {t['n']} filas con visitas")
    print(f"a rellenar: {len(filas)}\ngratis: /visits es API de ML\n", flush=True)
    if args.dry_run or not filas:
        if args.dry_run:
            print("[dry-run] no se escribió nada.")
        return 0

    # Sin token, cada llamada devolvería None y escribiríamos vacío por vacío.
    if competencia_ml._token("bekura") is None:
        print("✗ Sin token de ML (probablemente MySQL sin cuota). Reintenta más tarde.")
        return 3

    ok = sin = 0
    t0 = time.time()
    for i in range(0, len(filas), LOTE):
        trozo = [dict(f) for f in filas[i:i + LOTE]]
        # `enriquecer_visitas` y NO `competencia_ml.visitas_30d` a secas: es la
        # que resuelve primero el id REAL del item cuando el resultado es de
        # catálogo (`/p/` o `MLMU`). Sin ese paso, /visits contesta 0 y se
        # guardaría un cero que parece medición y no lo es.
        await competencia_captura.enriquecer_visitas(trozo)
        for f in trozo:
            v = f.get("visitas_30d")
            if not isinstance(v, int):
                sin += 1
                continue
            supabase_db.execute(
                "UPDATE enrich.market_search_results SET visitas_30d = %s "
                " WHERE termino_id = %s AND externo_id = %s",
                (v, f["termino_id"], f["externo_id"]))
            ok += 1
        print(f"  {min(i + LOTE, len(filas)):>5}/{len(filas)} · rellenadas={ok} "
              f"· sin dato={sin} · {time.time() - t0:.0f}s", flush=True)

    fin = supabase_db.fetch_one(
        "SELECT count(*) n, count(visitas_30d) v FROM enrich.market_search_results")
    print(f"\nLISTO en {time.time() - t0:.0f}s · rellenadas {ok} · sin dato {sin}")
    print(f"tabla: {fin['v']} de {fin['n']} filas con visitas")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
