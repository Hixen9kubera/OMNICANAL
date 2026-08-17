"""
cargar_walmart.py — Trae el catálogo VIVO de Walmart MX a `channel.listings`.

Pieza 1 de las seis que abren un canal. Hasta hoy la pestaña Walmart mostraba
datos de EJEMPLO encima de una cuenta con 237 artículos reales.

DE DÓNDE SALE: `GET /v3/items` paginado con `offset`. Cada artículo trae `sku`,
`wpid`, `productName`, `productType`, `publishedStatus`, `price`, `gtin` y `upc`.

LO QUE WALMART SÍ DA Y TEMU NO
──────────────────────────────
`publishedStatus` es una PALABRA (PUBLISHED / UNPUBLISHED / SYSTEM_PROBLEM), no
un número sin documentar. Aquí el panel sí puede afirmar qué está publicado —en
Temu hubo que decodificar siete códigos cruzando totales del Seller Center.

LO QUE NO DA
────────────
**Stock.** `/v3/items` no lo devuelve; vive en el endpoint de inventario, que es
otra llamada por SKU. Se deja NULL antes que poner un cero que se leería como
"agotado". El día que se conecte el fan-out a Walmart, ese es el dato a traer.

Uso:
    python -m scripts.cargar_walmart              # dry-run
    python -m scripts.cargar_walmart --aplicar
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import supabase_db as sdb, walmart  # noqa: E402

CANAL = "walmart"
CUENTA = "WALMART"
# `productType` cuando Walmart no tiene una categoría real para el artículo. No
# es una categoría: es el hueco. Se guarda igual —es lo que Walmart dice— pero
# se cuenta aparte, porque un catálogo con la mitad en "Por Defecto" no está
# categorizado, está sin categorizar.
SIN_CATEGORIA = "Por Defecto"
# SKUs que se crearon para probar la publicación y no son productos nuestros.
# Mismo criterio que los `KBTEST-*` de Temu: existen en el canal, pero meterlos
# al catálogo del panel ensucia los conteos de todo el mundo.
PREFIJOS_PRUEBA = ("PRUEBA-", "TEST-", "KBTEST")


def _precio(it: dict[str, Any]) -> float | None:
    p = (it.get("price") or {}).get("amount")
    try:
        return float(p)
    except (TypeError, ValueError):
        return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    if not walmart.disponible():
        print("Walmart no está configurado (faltan WM_CLIENT_ID / WM_CLIENT_SECRET).")
        return

    print("Leyendo el catálogo de Walmart…")
    declarado = await walmart.total_items()
    todos = await walmart.listar_items()
    pruebas = [i for i in todos
               if str(i.get("sku") or "").upper().startswith(PREFIJOS_PRUEBA)]
    items = [i for i in todos if i not in pruebas]
    print(f"  Walmart declara {declarado} · traídos {len(todos)} · "
          f"de prueba (se omiten) {len(pruebas)} · reales {len(items)}")
    if declarado and len(todos) != declarado:
        print(f"  ⚠ faltan {declarado - len(todos)}: revisar la paginación antes "
              f"de fiarse de este censo")

    estados: dict[str, int] = {}
    sin_cat = 0
    for it in items:
        estados[str(it.get("publishedStatus"))] = estados.get(str(it.get("publishedStatus")), 0) + 1
        if (it.get("productType") or "") == SIN_CATEGORIA:
            sin_cat += 1
    print("  por estado:", estados)
    print(f"  en «{SIN_CATEGORIA}» (sin categoría real): {sin_cat}")

    skus = sorted({str(i["sku"]).strip() for i in items if i.get("sku")})
    conocidos = {r["sku"] for r in sdb.fetch_all(
        "select sku::text sku from core.products where sku = any(%s)", (skus,))}
    faltantes = [s for s in skus if s not in conocidos]
    print(f"  {len(conocidos)} SKUs ya en core.products · {len(faltantes)} nuevos")
    if faltantes:
        print("    nuevos:", ", ".join(faltantes[:8]), "…" if len(faltantes) > 8 else "")

    cuenta = sdb.fetch_one(
        "select id from core.accounts where channel_id=%s and legacy_code=%s",
        (CANAL, CUENTA))
    if not cuenta:
        print(f"  cuenta {CUENTA}: NO existe, se creará")
        if args.aplicar:
            cuenta = sdb.execute_returning(
                """insert into core.accounts (channel_id, legacy_code, external_id,
                                              label, is_active)
                   values (%s, %s, null, %s, true) returning id""",
                (CANAL, CUENTA, "Kubera (Walmart MX)"))
    else:
        print(f"  cuenta {CUENTA}: ya existe")

    if not args.aplicar:
        print(f"\nDRY-RUN. Se escribirían {len(items)} filas en channel.listings "
              f"(canal={CANAL}) y {len(faltantes)} en core.products.")
        return

    if faltantes:
        sdb.execute(
            """insert into core.products (sku, status, source)
               select unnest(%s::text[]), 'draft', 'walmart'
               on conflict (sku) do nothing""", (faltantes,))

    account_id = cuenta["id"]
    escritas = 0
    for it in items:
        sku = str(it.get("sku") or "").strip()
        if not sku:
            continue
        sdb.execute(
            """insert into channel.listings
                 (sku, account_id, canal, listing_id, status, price,
                  category_id, currency, store_name, updated_at)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
               on conflict (sku, account_id, canal) do update set
                 listing_id  = excluded.listing_id,
                 status      = excluded.status,
                 price       = excluded.price,
                 category_id = excluded.category_id,
                 currency    = excluded.currency,
                 updated_at  = now()""",
            (sku, account_id, CANAL, str(it.get("wpid") or ""),
             str(it.get("publishedStatus") or ""), _precio(it),
             str(it.get("productType") or "") or None,
             ((it.get("price") or {}).get("currency") or "MXN"), "Walmart MX"))
        escritas += 1
    print(f"\nListo: {escritas} publicaciones de Walmart en channel.listings.")


if __name__ == "__main__":
    asyncio.run(main())
