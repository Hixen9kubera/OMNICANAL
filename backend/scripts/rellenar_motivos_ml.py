# -*- coding: utf-8 -*-
"""
rellenar_motivos_ml.py — pone el motivo EN ESPAÑOL en las devoluciones que ya
están guardadas.

DRY-RUN POR DEFAULT. Sin `--aplicar` no escribe nada.

POR QUÉ EXISTE
──────────────
La captura tomaba el motivo de `/claims/{id}/detail.problem`, que solo existe
mientras el reclamo está ABIERTO: llegaba en 53 de 392 devoluciones (13%). Para
las demás la pantalla acababa mostrando `resolution.reason` —`item_returned`,
`low_cost`—, que NO es el motivo: es cómo se resolvió, no por qué la
devolvieron. Brandon lo notó mirando la pestaña.

El código del motivo (`motivo_canal`, p.ej. `PDD9939`) sí está en las 392 de
392, y `GET /post-purchase/v1/claims/reasons/{id}` lo traduce. Medido el
9-sep-2026: **12 códigos distintos, 12 traducidos**.

De aquí en adelante lo hace sola la captura (`devoluciones_ml.motivo_de`). Este
script es solo para lo ya escrito, que el barrido no vuelve a visitar porque
cae fuera de su ventana de 48 h.

POR QUÉ SE USÓ `--forzar` EL 9-SEP, Y NO ES UN DETALLE
──────────────────────────────────────────────────────
Las 53 que venían de `/detail` estaban redactadas desde el lado del vendedor
(«El comprador dijo que se arrepintió de la compra») y las del catálogo desde el
del comprador («Llegó lo que compré en buenas condiciones pero no lo quiero»).
Cada una es correcta por separado — y juntas **rompen el desglose**: el mismo
motivo salía partido en CINCO renglones (118 + 28 + 5 + 3 + 1), y un desglose
por motivo que parte el motivo no sirve para nada.

Cuando el texto es una ETIQUETA con la que se agrupa, la consistencia vale más
que el matiz. El catálogo da una sola redacción por código; se normalizó todo a
esa. El `problem` original de `/detail` sigue guardado en `payload`.

USO
───
    python backend/scripts/rellenar_motivos_ml.py            # dry-run
    python backend/scripts/rellenar_motivos_ml.py --aplicar
    python backend/scripts/rellenar_motivos_ml.py --aplicar --forzar
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import httpx  # noqa: E402

from services import devoluciones_ml as dml  # noqa: E402
from services import meli, supabase_db as sdb  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--forzar", action="store_true",
                    help="también reescribe las que ya tienen texto")
    args = ap.parse_args()

    filtro = "" if args.forzar else "and motivo_texto is null"
    codigos = await asyncio.to_thread(sdb.fetch_all, f"""
        select motivo_canal, count(*) n
        from channel.returns
        where canal = %s and motivo_canal is not null {filtro}
        group by 1 order by 2 desc""", (dml.CANAL,))
    if not codigos:
        print("No hay nada que rellenar.")
        return 0

    tok = await asyncio.to_thread(meli._access_token, "SANCORFASHION")
    if not tok:
        sys.exit("sin token vigente (regla 8)")
    cab = {"Authorization": f"Bearer {tok}"}

    plan: list[tuple[str, str, int]] = []
    sin_texto: list[str] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as cli:
        for fila in codigos:
            cod, n = fila["motivo_canal"], fila["n"]
            texto = await dml.motivo_de(cli, cab, cod)
            if texto:
                plan.append((cod, texto, n))
                print(f"  {cod}  ×{n:4d}  «{texto}»")
            else:
                sin_texto.append(cod)
                print(f"  {cod}  ×{n:4d}  → ML no lo traduce")

    total = sum(n for _, _, n in plan)
    print(f"\n  {len(plan)}/{len(codigos)} códigos traducidos · {total} devoluciones a actualizar")
    if sin_texto:
        print(f"  sin traducción: {', '.join(sin_texto)}")

    if not args.aplicar:
        print("\nDRY-RUN: no se escribió nada. Repite con --aplicar.")
        return 0

    escritas = 0
    for cod, texto, _ in plan:
        escritas += await asyncio.to_thread(sdb.execute, f"""
            update channel.returns set motivo_texto = %s
            where canal = %s and motivo_canal = %s {filtro}""",
            (texto, dml.CANAL, cod))
    print(f"\nOK — {escritas} devoluciones con motivo en español.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
