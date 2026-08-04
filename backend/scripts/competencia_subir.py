"""Sube la foto local del módulo de Competencia a la BD kubera, esquema `propuestas`.

POR QUÉ EXISTE
--------------
Las mediciones se hacen en local: el ranking necesita un navegador (Selenium) y
Railway no tiene Chrome. El panel de producción, en cambio, lee de Supabase. Este
script es el puente entre los dos.

POR QUÉ `propuestas` Y NO `core`/`channel`/…
--------------------------------------------
Esos esquemas son de la migración del equipo y sobre ellos corren 5 rachas de actas
que se rompen con una fila ajena. `propuestas` está aislado a propósito: es el
esquema que el equipo va a revisar y aprobar.

IDEMPOTENTE
-----------
Cada tabla se borra y se reescribe. El módulo no guarda histórico —cada corrida
reemplaza la anterior— así que esto respeta esa decisión de producto.

USO
---
    python scripts/competencia_subir.py --token sbp_xxx [--dry-run]

El token es un Personal Access Token de Supabase (Management API). No se guarda en
el repo ni se imprime.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import competencia_store  # noqa: E402

REF = "tukwcvsitthplhswsblt"
API = f"https://api.supabase.com/v1/projects/{REF}/database/query"


def ejecutar(sql: str, token: str) -> list | dict:
    tmp = Path("/tmp/competencia_q.json")
    tmp.write_text(json.dumps({"query": sql}))
    r = subprocess.run(
        ["curl", "-s", "-X", "POST", API,
         "-H", f"Authorization: Bearer {token}",
         "-H", "Content-Type: application/json", "-d", f"@{tmp}"],
        capture_output=True, text=True, timeout=300)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"crudo": r.stdout[:400]}


def lit(v) -> str:
    """Literal SQL. Los None van como NULL — nunca como '' ni 0."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def cargar(tabla: str, columnas: list[str], filas: list[tuple],
           token: str, dry: bool, lote: int = 50) -> int:
    if dry:
        print(f"  [dry-run] {tabla}: {len(filas)} filas")
        return len(filas)
    r = ejecutar(f"delete from propuestas.{tabla};", token)
    if isinstance(r, dict) and r.get("message"):
        print(f"  ! {tabla}: {r['message'][:160]}")
        return 0
    total = 0
    for i in range(0, len(filas), lote):
        trozo = filas[i:i + lote]
        vals = ",".join("(" + ",".join(lit(v) for v in f) + ")" for f in trozo)
        r = ejecutar(
            f"insert into propuestas.{tabla} ({','.join(columnas)}) values {vals};", token)
        if isinstance(r, dict) and r.get("message"):
            print(f"  ! {tabla} lote {i}: {r['message'][:200]}")
            return total
        total += len(trozo)
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="PAT de Supabase (Management API)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    periodo = competencia_store.periodo_actual()
    c = sqlite3.connect(competencia_store.RUTA_DB)
    c.row_factory = sqlite3.Row
    print(f"═══ Subir a propuestas · periodo {periodo} ═══")

    # SKUs. Nombre y categoría NO se suben: salen por JOIN de core.products y
    # channel.product_category. `raiz_id` e `imagen` sí, porque no son derivables
    # desde el esquema del equipo (ver el comentario del DDL).
    skus = [(r["sku"], r["termino_general"], r["termino_origen"], bool(r["activo"]),
             r["raiz_id"], r["raiz_nombre"], r["imagen"])
            for r in c.execute("select * from skus")]
    print(f"skus: {cargar('competencia_skus', ['sku','termino_general','termino_origen','activo','raiz_id','raiz_nombre','imagen'], skus, args.token, args.dry_run)}/{len(skus)}")

    # Publicaciones. El precio NO se sube: vive en channel.listings.price.
    pubs = [(r["sku"], r["cuenta"], r["canal"], r["ml_item_id"], periodo,
             r["visitas_30d"], r["unidades_30d"], "ml_api", r["titulo"], r["estado"])
            for r in c.execute("select * from publicaciones")]
    print(f"publicaciones: {cargar('competencia_publicacion_metricas', ['sku','cuenta','canal','listing_id','periodo','visitas_30d','unidades_30d','fuente_unidades','titulo','estado'], pubs, args.token, args.dry_run)}/{len(pubs)}")

    COLS_R = ["categoria_id", "nivel", "periodo", "posicion", "externo_id", "id_pagina",
              "tipo", "titulo", "precio", "precio_lista", "descuento", "vendidos",
              "rating", "reviews", "seller", "imagen", "url", "visitas_30d",
              "item_categoria_id", "item_categoria_nombre", "es_nuestro", "sku_nuestro"]
    rank = [tuple(r[k] if k != "es_nuestro" else bool(r[k]) for k in COLS_R)
            for r in c.execute("select * from rankings_categoria")]
    print(f"rankings: {cargar('competencia_rankings_categoria', COLS_R, rank, args.token, args.dry_run)}/{len(rank)}")

    term = [(r["categoria_id"], periodo, r["posicion"], r["termino"], r["url"])
            for r in c.execute("select * from terminos_categoria")]
    print(f"terminos: {cargar('competencia_terminos_categoria', ['categoria_id','periodo','posicion','termino','url'], term, args.token, args.dry_run)}/{len(term)}")

    if not args.dry_run:
        print("\n── conteo en Supabase ──")
        print(json.dumps(ejecutar("""
            select 'skus' t, count(*) n from propuestas.competencia_skus
            union all select 'publicaciones', count(*) from propuestas.competencia_publicacion_metricas
            union all select 'rankings', count(*) from propuestas.competencia_rankings_categoria
            union all select 'terminos', count(*) from propuestas.competencia_terminos_categoria
            order by 1
        """, args.token), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
