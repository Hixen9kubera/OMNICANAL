# -*- coding: utf-8 -*-
"""
reporte_sync_desde_ml.py — Paso 2 del plan de encendido de SYNC_DESDE_ML.

SOLO LECTURA: muestra qué cambiaría el universo del sondeo si la bandera se
enciende, SIN escribir una sola fila. Para cada cuenta:

  · ENTRAN: publicaciones vivas en ML que ml_progress no conoce (con el SKU
    resuelto del propio item — las que no resuelven se marcan).
  · SALEN: publicaciones de ml_progress que ML ya no devuelve (muertas) y que
    hoy siguen pisando la fila de su SKU cada ciclo.

Uso:
  backend/.venv/Scripts/python.exe backend/scripts/reporte_sync_desde_ml.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from services import db, inventario, meli  # noqa: E402

CUENTAS = ("BEKURA", "SANCORFASHION")


async def main() -> None:
    print("[reporte] universo del sondeo: ml_progress vs catálogo vivo de ML\n")
    tot_entran = tot_salen = tot_sin_sku = 0
    for cuenta in CUENTAS:
        token = meli._access_token(cuenta)
        if not token:
            print(f"{cuenta}: sin token — se omite")
            continue
        conocidos = {
            str(r["ml_item_id"]): r["sku"] for r in db.fetch_all(
                "SELECT sku, ml_item_id FROM ml_progress "
                "WHERE cuenta=%s AND success=1 AND ml_item_id IS NOT NULL", (cuenta,))
        }
        async with httpx.AsyncClient(base_url=inventario._ML_API, timeout=40.0) as cli:
            vivos = await inventario._universo_ml(cli, cuenta, token)
            entran = [i for i in vivos if i not in conocidos]
            salen = sorted(set(conocidos) - set(vivos))
            print(f"{cuenta}: vivos en ML={len(vivos)}  en ml_progress={len(conocidos)}  "
                  f"ENTRAN={len(entran)}  SALEN(muertas)={len(salen)}")
            sin_sku = []
            for i in range(0, len(entran), 20):
                lote = entran[i:i + 20]
                r = await cli.get("/items", headers={"Authorization": f"Bearer {token}"},
                                  params={"ids": ",".join(lote)})
                if r.status_code != 200:
                    continue
                for envuelto in r.json():
                    it = envuelto.get("body") or {}
                    if not it.get("id"):
                        continue
                    sku = inventario._sku_de_item(it)
                    if sku:
                        print(f"   + {it['id']}  {sku:<24} {it.get('status'):<8} "
                              f"${it.get('price')}  stock={it.get('available_quantity')}")
                    else:
                        sin_sku.append(it["id"])
            for iid in salen[:10]:
                print(f"   - {iid}  (muerta; hoy pisa la fila de {conocidos[iid]})")
            if len(salen) > 10:
                print(f"   … y {len(salen) - 10} muertas más")
            if sin_sku:
                print(f"   ! sin SKU legible (no se escribirian): {', '.join(sin_sku)}")
            tot_entran += len(entran)
            tot_salen += len(salen)
            tot_sin_sku += len(sin_sku)
    print(f"\nTOTAL: entran {tot_entran} · salen {tot_salen} muertas · "
          f"sin SKU {tot_sin_sku} (se omitirían con incidencia)")
    print("Nada se escribió. Encender = SYNC_DESDE_ML=true en Railway (dale de Brandon).")


if __name__ == "__main__":
    asyncio.run(main())
