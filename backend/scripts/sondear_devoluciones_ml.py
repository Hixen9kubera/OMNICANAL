"""
sondear_devoluciones_ml.py — trae los CLAIMS de ML para ver cómo se obtienen.

SOLO LECTURA: puros GET a la API de ML. No escribe nada, en ningún sistema.
**Nunca imprime un token.**

POR QUÉ EXISTE
--------------
La subtab de Devoluciones de /analisis no tiene fuente: el barrido de las 73
tablas de kubera (31-ago-2026) no encontró ninguna columna
`return|devol|refund|claim`. Y el aviso SÍ llega — el webhook `post_purchase`
entró 2,324 veces en 3 días con `actions:["claims"]` — pero cae en el `else` de
`routers/webhooks.py:428` y `ops.webhook_events` se purga a los 3 días.

Antes de diseñar tabla o pantalla: ver con qué contesta ML de verdad.

    GET /post-purchase/v1/claims/search?type=returns&range=date_created:after:TS,before:TS

MEDIDO EL 31-AGO-2026 (BEKURA, histórico completo sin rango):
    type=returns      341   ← las DEVOLUCIONES
    type=mediations 1,667
    stage=claim       557 · stage=dispute 1,440
    status=opened     125 · status=closed 2,720
`range` por sí solo da 400: exige ir acompañado de type, stage o status.

USO
---
    python backend/scripts/sondear_devoluciones_ml.py
    python backend/scripts/sondear_devoluciones_ml.py --desde 2026-02-01 --limite 5

Imprime los primeros claims completos (JSON crudo) y guarda todo en
`reportes/`. El crudo es el punto: el esquema se descubre leyéndolo.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import httpx  # noqa: E402

from services import meli  # noqa: E402

API = "https://api.mercadolibre.com"
CUENTAS = ("BEKURA", "SANCORFASHION")


def main() -> int:
    hoy = datetime.now(timezone.utc)
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default=(hoy - timedelta(days=90)).strftime("%Y-%m-%d"))
    ap.add_argument("--hasta", default=hoy.strftime("%Y-%m-%d"))
    ap.add_argument("--tipo", default="returns",
                    help="type del claim: returns (devoluciones) | mediations | cancellations")
    ap.add_argument("--limite", type=int, default=3,
                    help="cuántos claims imprimir completos por cuenta")
    args = ap.parse_args()

    rango = (f"date_created:after:{args.desde}T00:00:00.000-06:00,"
             f"before:{args.hasta}T23:59:59.000-06:00")
    salida = RAIZ.parent / "reportes"
    salida.mkdir(exist_ok=True)
    todo = {}

    with httpx.Client(follow_redirects=True) as cli:
        for cuenta in CUENTAS:
            print("\n" + "═" * 76)
            print(f"■ {cuenta}   {args.desde} → {args.hasta}")
            print("═" * 76)
            tok = meli._access_token(cuenta)
            if not tok:
                print("  ✗ sin token vigente (regla 8)")
                continue
            cab = {"Authorization": f"Bearer {tok}"}

            # El `range` es lo que dice la doc, pero varía por país/versión: si
            # lo rechaza, se pide sin filtro para no quedarse sin muestra.
            # `range` SOLO es válido acompañado de un filtro real: sin uno de
            # type/stage/status, ML responde 400 atLeastOneFilterProvided
            # (medido el 31-ago-2026). `type=returns` es el que aísla las
            # DEVOLUCIONES del resto de los reclamos.
            par = {"limit": 50, "offset": 0, "sort": "date_created:desc",
                   "type": args.tipo, "range": rango}
            r = cli.get(f"{API}/post-purchase/v1/claims/search", params=par,
                        headers=cab, timeout=45)
            if r.status_code == 400:
                print(f"  range rechazado (400: {r.text[:150]}) → reintento sin filtro")
                par.pop("range")
                r = cli.get(f"{API}/post-purchase/v1/claims/search", params=par,
                            headers=cab, timeout=45)
            if r.status_code != 200:
                print(f"  ✗ HTTP {r.status_code}: {r.text[:400]}")
                continue

            cuerpo = r.json()
            claims = cuerpo.get("data") or cuerpo.get("results") or []
            print(f"  paging: {cuerpo.get('paging')}")
            print(f"  claims en esta página: {len(claims)}")
            if not claims:
                print(f"  cuerpo crudo: {json.dumps(cuerpo, ensure_ascii=False)[:500]}")
                continue

            fechas = sorted(str(c.get("date_created", ""))[:10] for c in claims)
            print(f"  ventana que contestó: {fechas[0]} → {fechas[-1]}")
            for campo in ("type", "stage", "status", "resource", "quantity_type"):
                cnt = Counter(str(c.get(campo)) for c in claims)
                print(f"  {campo:14} " + ", ".join(f"{k}={v}" for k, v in cnt.most_common(8)))

            print(f"\n  ── {min(args.limite, len(claims))} CLAIMS COMPLETOS ──")
            for c in claims[:args.limite]:
                print(json.dumps(c, indent=2, ensure_ascii=False, default=str))
                print("  " + "-" * 72)
            todo[cuenta] = claims

    if todo:
        f = salida / f"claims_ml_{hoy.strftime('%Y%m%d_%H%M')}.json"
        f.write_text(json.dumps(todo, indent=2, ensure_ascii=False, default=str))
        print(f"\ncrudos completos: {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
