"""
estado_walmart.py — Foto del estado real en Walmart MX, con POCAS llamadas.

POR QUÉ IMPORTA EL "POCAS": Walmart corta con REQUEST_THRESHOLD_VIOLATED cuando
se le pega seguido, y ese corte tumbó 19 de 24 productos del segundo lote sin
que hubiera nada malo en sus datos. Este script hace 3 llamadas en total.

Uso (desde backend/):  python -m scripts.estado_walmart
"""
from __future__ import annotations

import base64
import collections
import json
import os
import sys
import uuid

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import httpx

HOST = "https://marketplace.walmartapis.com"


def _h(tk: str | None = None) -> dict:
    d = {"WM_SVC.NAME": "Walmart Marketplace",
         "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
         "WM_MARKET": "mx", "Accept": "application/json"}
    if tk:
        d["WM_SEC.ACCESS_TOKEN"] = tk
    return d


def main() -> int:
    cid, sec = os.environ["WM_CLIENT_ID"], os.environ["WM_CLIENT_SECRET"]
    h = _h()
    h["Authorization"] = "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode()
    h["Content-Type"] = "application/x-www-form-urlencoded"
    tk = httpx.post(f"{HOST}/v3/token", headers=h,
                    data={"grant_type": "client_credentials"},
                    timeout=30).json()["access_token"]

    print("=" * 76)
    print("1) EL CATÁLOGO — lo que YA está arriba")
    print("=" * 76)
    r = httpx.get(f"{HOST}/v3/items", headers=_h(tk),
                  params={"limit": "50"}, timeout=60)
    if r.status_code == 200:
        j = r.json()
        print(f"   totalItems = {j.get('totalItems')}")
        for it in j.get("ItemResponse", []):
            print(f"   {str(it.get('sku')):24} wpid={it.get('wpid')} "
                  f"{it.get('publishedStatus')}/{it.get('lifecycleStatus')} "
                  f"${(it.get('price') or {}).get('amount')}")
    else:
        print(f"   HTTP {r.status_code} — el catálogo sigue vacío "
              f"(ningún artículo terminó de publicarse todavía)")

    print("\n" + "=" * 76)
    print("2) LOS ENVÍOS — resumen de todos los feeds")
    print("=" * 76)
    f = httpx.get(f"{HOST}/v3/feeds", headers=_h(tk),
                  params={"limit": "50"}, timeout=60).json()
    feeds = (f.get("results") or {}).get("feed", [])
    print(f"   feeds totales en la cuenta: {f.get('totalResults')}")
    print(f"   consultados: {len(feeds)}\n")

    estados = collections.Counter()
    en_cola, exitosos = [], []
    for x in feeds:
        estados[x.get("feedStatus")] += 1
        recibidos = x.get("itemsReceived") or 0
        fallidos = x.get("itemsFailed") or 0
        if recibidos and not fallidos:
            (en_cola if x.get("feedStatus") == "INPROGRESS" else exitosos).append(x)

    for k, v in estados.most_common():
        print(f"   {str(k):14} {v}")
    print(f"\n   feeds SIN fallos: {len(en_cola) + len(exitosos)}")
    print(f"      · aún en cola de Walmart : {len(en_cola)}")
    print(f"      · procesados             : {len(exitosos)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
