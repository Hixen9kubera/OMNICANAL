"""
cargar_temu_categorias.py — El árbol de categorías de Temu, en español.

Pieza 2 de las seis. Sin esto el panel muestra `catId=26096` en vez de un
nombre, y no hay selector de categoría: el publicador tendría que adivinar o
depender de que el producto YA esté publicado.

FUENTE: `bg.local.goods.cats.get` con `parentCatId`. Se recorre por niveles —
no hay endpoint que devuelva el árbol completo ni que traduzca un `catId`
suelto a su nombre, así que la única forma de saber cómo se llama una hoja es
haber caminado hasta ella.

`language=es` SÍ funciona aquí y devuelve los nombres traducidos ("Productos de
oficina" en vez de "Office Products"). Es la misma llave que ahorra la capa de
traducción en `template.get`.

LO QUE SE GUARDA Y LO QUE NO:
  · `is_leaf` viene de la API (`leaf`). Importa porque `template.get` SOLO
    responde en hojas y el selector solo debe ofrecer hojas — ofrecer una
    intermedia es ofrecer un error.
  · `disponibilidad` guarda `availableStatus` CRUDO. La API no documenta qué
    significa y no se le inventa: el día que se verifique, se traduce aquí.
  · `path` se arma al vuelo con los nombres del camino, que es lo que el
    buscador del panel muestra ("Hogar > Cocina > Sartenes").

Uso:
    python -m scripts.cargar_temu_categorias            # dry-run: solo mide
    python -m scripts.cargar_temu_categorias --aplicar
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from psycopg2.extras import execute_values  # noqa: E402

from services import supabase_db as sdb, temu  # noqa: E402

CANAL = "temu"
CONCURRENCIA = 6


async def _hijos(padre: int | str, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    async with sem:
        for intento in (1, 2):
            try:
                r = await temu.llamar("bg.local.goods.cats.get",
                                      {"parentCatId": int(padre), "language": "es"})
                return r.get("goodsCatsList") or []
            except Exception as exc:  # noqa: BLE001
                if intento == 2:
                    print(f"    catId {padre}: {str(exc)[:80]}")
                    return []
                await asyncio.sleep(1.5)
    return []


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    if not temu.disponible():
        print("Temu no está configurado.")
        return

    sem = asyncio.Semaphore(CONCURRENCIA)
    filas: list[tuple] = []          # lo que se va a escribir
    # (catId, nombre, path, parent, rootId, rootName)
    nivel: list[tuple[int, str, str, str | None, str, str]] = []

    raices = await _hijos(0, sem)
    for c in raices:
        cid, nom = str(c["catId"]), (c.get("catName") or "").strip()
        filas.append((CANAL, cid, nom, nom, None, cid, nom,
                      bool(c.get("leaf")), str(c.get("availableStatus"))))
        if not c.get("leaf"):
            nivel.append((int(c["catId"]), nom, nom, None, cid, nom))
    print(f"raíces: {len(raices)}")

    profundidad = 1
    while nivel and profundidad < 12:
        profundidad += 1
        tareas = [_hijos(n[0], sem) for n in nivel]
        resultados = await asyncio.gather(*tareas)
        siguiente: list[tuple[int, str, str, str | None, str, str]] = []
        for (pid, _pnom, ppath, _pp, root_id, root_nom), hijos in zip(nivel, resultados):
            for c in hijos:
                cid, nom = str(c["catId"]), (c.get("catName") or "").strip()
                path = f"{ppath} > {nom}"
                filas.append((CANAL, cid, nom, path, str(pid), root_id, root_nom,
                              bool(c.get("leaf")), str(c.get("availableStatus"))))
                if not c.get("leaf"):
                    siguiente.append((int(c["catId"]), nom, path, str(pid),
                                      root_id, root_nom))
        print(f"  nivel {profundidad}: {sum(len(h) for h in resultados)} categorías "
              f"({len(siguiente)} con hijos) · acumulado {len(filas)}")
        nivel = siguiente

    hojas = sum(1 for f in filas if f[7])
    print(f"\nÁrbol completo: {len(filas)} categorías · {hojas} hojas")

    # ¿Quedan cubiertas las que ya usamos?
    usadas = {r["category_id"] for r in sdb.fetch_all(
        """select distinct category_id from channel.listings
            where canal=%(c)s and category_id is not null""", {"c": CANAL})}
    cubiertas = usadas & {f[1] for f in filas}
    print(f"Categorías en uso: {len(usadas)} · con nombre ahora: {len(cubiertas)}")

    if not args.aplicar:
        print("\nDRY-RUN: nada se escribió.")
        return

    with sdb.get_cursor() as cur:
        execute_values(
            cur,
            """insert into channel.categories
                 (channel_id, category_id, name, path, parent_id, root_id,
                  root_name, is_leaf, disponibilidad)
               values %s
               on conflict (channel_id, category_id) do update set
                 name = excluded.name, path = excluded.path,
                 parent_id = excluded.parent_id, root_id = excluded.root_id,
                 root_name = excluded.root_name, is_leaf = excluded.is_leaf,
                 disponibilidad = excluded.disponibilidad""",
            filas, page_size=500)
    print(f"\nListo: {len(filas)} categorías de Temu en channel.categories.")


if __name__ == "__main__":
    asyncio.run(main())
