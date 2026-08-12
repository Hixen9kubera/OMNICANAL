"""Propone con IA el término general de los SKUs que no lo tienen → `enrich.market_*`.

QUÉ ES EL TÉRMINO GENERAL
-------------------------
La búsqueda AMPLIA con la que un comprador descubre la categoría ("lona para
exterior"), no el título del producto. Es lo que después se mide en el buscador
para saber si existimos o no para ese comprador. No se puede derivar del título
con reglas —quitar palabras no da "lona para exterior" a partir de "Lona Sombra
Reforzada 4x6m Protección Uv Beige"— así que lo propone un LLM.

GRATIS. Esto solo llama al LLM. Lo que cuesta es MEDIR el término después
(~$0.007 por término en Apify, `competencia_buscar_apify.py`), y por eso importa
el paso siguiente: términos repetidos se miden y se pagan UNA vez, porque desde
la migración 0016 viven en `enrich.market_search_term` y los SKUs los referencian
por FK. Medido en las raíces ya trabajadas: 1.07 a 1.19 SKUs por término.

NO PISA CORRECCIONES HUMANAS
----------------------------
Se escribe con `proponer_termino`, que solo toca las filas cuyo `termino_origen`
no es 'manual'. Una corrección de una persona gana sobre cualquier propuesta
posterior.

USO
---
    # Ver qué propondría, sin escribir (sí llama al LLM)
    backend/.venv/bin/python backend/scripts/competencia_proponer_terminos.py \\
        --raiz MLM1276 --dry-run

    # Escribir
    ... --raiz MLM1276

    # Incluir también los que ya tienen término propuesto por IA (los re-propone)
    ... --raiz MLM1276 --rehacer
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

from services import (  # noqa: E402
    competencia_store, competencia_supabase, competencia_terminos, supabase_db,
)

# Productos por llamada al LLM. El prompt lleva título + categoría de cada uno;
# en lotes grandes la calidad se cae y una respuesta mal formada tira el lote.
LOTE = 25
COSTO_APIFY_POR_TERMINO = 0.007


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raiz", required=True, help="Raíz a trabajar (p. ej. MLM1276)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Propone y muestra, pero NO escribe.")
    ap.add_argument("--rehacer", action="store_true",
                    help="Incluye los que ya tienen término de IA. Los 'manual' "
                         "quedan fuera siempre.")
    ap.add_argument("--limite", type=int, default=0)
    args = ap.parse_args()

    raiz = args.raiz.strip().upper()
    if not supabase_db.disponible():
        print("✗ Falta SUPABASE_DB_URL: el término se guarda en "
              "enrich.market_sku_config y no hay a dónde.")
        return 2

    cond = ("" if args.rehacer
            else " AND cfg.termino_id IS NULL")
    filas = supabase_db.fetch_all(f"""
        SELECT v.sku, v.nombre, v.categoria_nombre, v.ruta
          FROM enrich.market_skus_v v
          JOIN enrich.market_sku_config cfg ON cfg.sku = v.sku AND cfg.canal = v.canal
         WHERE v.raiz_id = %s AND v.activo
           AND cfg.termino_origen IS DISTINCT FROM 'manual'
           AND v.nombre IS NOT NULL
               {cond}
         ORDER BY v.sku""", (raiz,))
    if args.limite:
        filas = filas[:args.limite]

    print(f"═══ Términos por IA · raíz {raiz} ═══")
    print(f"SKUs a proponer: {len(filas)}"
          + ("  (incluye los ya propuestos por IA)" if args.rehacer else ""))
    if not filas:
        print("Nada que hacer.")
        return 0

    propuestos: dict[str, str] = {}
    t0 = time.time()
    for i in range(0, len(filas), LOTE):
        trozo = filas[i:i + LOTE]
        try:
            r = competencia_terminos.proponer([
                {"sku": f["sku"], "nombre": f["nombre"],
                 "categoria": f.get("categoria_nombre") or ""} for f in trozo])
        except Exception as exc:  # noqa: BLE001
            print(f"  ! lote {i // LOTE + 1} falló: {type(exc).__name__}: {exc}")
            continue
        propuestos.update(r or {})
        print(f"  lote {i // LOTE + 1}/{-(-len(filas) // LOTE)}: "
              f"{len(r or {})} de {len(trozo)}", flush=True)

    print(f"\npropuestos: {len(propuestos)} de {len(filas)} en {time.time() - t0:.0f}s")
    distintos = sorted(set(propuestos.values()))
    print(f"términos DISTINTOS: {len(distintos)} "
          f"({len(propuestos) / len(distintos):.2f} SKUs por término)" if distintos else "")

    # Lo que de verdad importa antes de pagar: cuántos ya están medidos.
    ya = competencia_supabase.terminos_medidos()
    nuevos = [t for t in distintos if t not in ya]
    print(f"ya medidos (gratis)  : {len(distintos) - len(nuevos)}")
    print(f"por medir en Apify   : {len(nuevos)}  ≈ ${len(nuevos) * COSTO_APIFY_POR_TERMINO:.2f} USD")

    print("\nmuestra:")
    for f in filas[:8]:
        t = propuestos.get(f["sku"])
        print(f"   {f['sku']:<20} {str(f['nombre'])[:40]:<40} → {t!r}")

    if args.dry_run:
        print("\n[dry-run] no se escribió nada.")
        return 0

    escritos = 0
    for sku, termino in propuestos.items():
        if competencia_store.proponer_termino(sku, termino):
            escritos += 1
    print(f"\n✓ {escritos} SKUs con término guardado en enrich.market_sku_config")
    print(f"  catálogo de términos: "
          f"{supabase_db.fetch_scalar('select count(*) from enrich.market_search_term')} filas")
    print(f"\nSiguiente: medirlos con Apify (~${len(nuevos) * COSTO_APIFY_POR_TERMINO:.2f})\n"
          f"  backend/.venv/bin/python backend/scripts/competencia_buscar_apify.py --raiz {raiz}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
