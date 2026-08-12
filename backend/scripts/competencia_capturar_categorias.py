"""Captura el top de más vendidos de CATEGORÍAS PUNTUALES → `enrich.market_*`.

PARA QUÉ
--------
Traer una categoría padre nueva al panel de Competencia sin re-raspar las 26
raíces y sus ~200 hojas. El catálogo crece de a una categoría, no de golpe.

NAVEGADOR LOCAL, NO APIFY — y no es preferencia
-----------------------------------------------
El actor genérico de Apify raspa la página, pero su normalización TIRA
`id_pagina` (el id que va en el URL de la tarjeta). Ese id es la llave para
resolver `item_categoria_id`, o sea la subcategoría a la que pertenece cada
producto del top. Sin él no hay NICHOS ni `pos_en_raiz`, que son justo lo que
se quiere de una categoría padre. Es la causa medida del hueco actual: de las
3,000 filas de ranking solo 37 tienen `item_categoria_id`, y son precisamente
las que se capturaron con navegador.

Consecuencia práctica: esto abre una ventana de Chrome VISIBLE (ML detecta
`--headless=new` y sirve un 404 a todo) y hay que dejarla trabajar.

ESCRIBE DIRECTO EN LA BD KUBERA
-------------------------------
Cada categoría se reescribe acotada a su propia (canal, categoría, nivel) y en
una sola transacción. Requiere `SUPABASE_DB_URL`; sin ella revienta en vez de
escribir en un disco que nadie lee.

Gratis: el raspado no cuesta (es el navegador de casa), y las visitas, reseñas,
subcategoría de cada fila y términos más buscados salen de la API de ML.

USO
---
    # Deportes y Fitness (raíz) + Mini Bicicletas Fijas (hoja)
    backend/.venv/bin/python backend/scripts/competencia_capturar_categorias.py \
        MLM1276 MLM184769

    # Ver qué haría, sin abrir el navegador ni escribir
    ... --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from services import (  # noqa: E402
    competencia_captura, competencia_ml, competencia_store, supabase_db,
)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("categorias", nargs="+",
                    help="Ids de categoría de ML (p. ej. MLM1276 MLM184769)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Solo dice qué haría: nivel de cada categoría y si ML "
                         "publica más vendidos. No abre navegador ni escribe.")
    args = ap.parse_args()

    cats = [c.strip().upper() for c in args.categorias if c.strip()]
    print(f"═══ Competencia · capturar {len(cats)} categorías ═══")

    if not supabase_db.disponible():
        print("✗ Falta SUPABASE_DB_URL: el destino es enrich.market_* y no hay "
              "a dónde escribir.\n  (está en Railway, servicio BackendOmnicanal)")
        return 2
    print(f"destino : BD kubera · enrich.market_*  (ping {supabase_db.ping()})")

    # Nivel según nuestros SKUs, igual que lo decide la captura.
    skus = competencia_store.listar_skus()
    raices = {s["raiz_id"] for s in skus if s.get("raiz_id")}
    for c in cats:
        nivel = "raiz" if c in raices else "hoja"
        mios = sum(1 for s in skus
                   if s.get("categoria_id") == c or s.get("raiz_id") == c)
        print(f"  · {c:<12} nivel={nivel:<5} SKUs nuestros={mios}")

    if args.dry_run:
        print("\n[dry-run] ¿ML publica más vendidos de estas categorías?")
        for c in cats:
            n = len(competencia_ml.mas_vendidos_categoria(c) or [])
            print(f"  · {c:<12} /highlights → {n} entradas"
                  + ("" if n else "   ← ML no publica top de esta categoría"))
        print("\n[dry-run] no se abrió el navegador ni se escribió nada.")
        return 0

    print("\nAbriendo Chrome (VISIBLE a propósito). No lo cierres.\n", flush=True)
    t0 = time.time()
    r = await competencia_captura.capturar_rankings_categorias(solo=cats)

    print(f"\n── resultado en {time.time() - t0:.0f}s")
    if not r.get("ok"):
        print(f"✗ {r.get('motivo')}")
        return 1
    print(f"categorías pedidas : {r['categorias']}")
    print(f"con datos raspados : {r['con_datos']}")
    for cat, n in (r.get("guardados") or {}).items():
        print(f"  · {cat:<12} {n} filas de ranking")
    for cat, n in (r.get("terminos") or {}).items():
        print(f"  · {cat:<12} {n} términos más buscados")
    for a in r.get("avisos") or []:
        print(f"  aviso: {a}")

    # Lo que de verdad importa de una categoría padre: que las filas traigan
    # item_categoria_id, porque sin eso no hay nichos.
    print("\n── nichos: ¿las filas traen su subcategoría?")
    for cat in cats:
        f = supabase_db.fetch_one(
            "SELECT count(*) n, count(item_categoria_id) con_nicho "
            "FROM enrich.market_bestsellers WHERE categoria_id = %s", (cat,))
        print(f"  · {cat:<12} {f['con_nicho']}/{f['n']} filas con item_categoria_id")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
