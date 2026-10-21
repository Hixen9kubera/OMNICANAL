"""
pricing_promos.py — Arma `backend/data/pricing/promos.json`: la PROMOCIÓN que tiene HOY cada publicación de la
foto de PRICING (columna «Promoción hoy», v0.654.0).

    python backend/scripts/pricing_promos.py [--salida backend/data/pricing] [--hilos 4]

Por publicación de `monitor.json.gz`, SOLO GET a Mercado Libre:
    GET /items/{id}/sale_price?context=channel_marketplace   → lo que se cobra y qué promoción lo pone
    GET /seller-promotions/items/{id}?app_version=v2         → todas sus promociones (vivas, programadas, cupones)
Y por cuenta, los cupones vigentes con sus publicaciones (v0.680.0: la lista por publicación a veces llega sin ellos):
    GET /seller-promotions/promotions/{id}/items?promotion_type=SELLER_COUPON_CAMPAIGN

El token sale de `services.meli._access_token`, que SOLO lee (nunca renueva). No escribe en ML, kubera ni Woo.
Es una foto con su hora (`leido`): para ponerla al día se vuelve a correr y se sube.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
CDMX = timezone(timedelta(hours=-6))
API = "https://api.mercadolibre.com"

# La lógica vive en el servicio (el servidor la usa para releer en vivo); aquí solo se reexporta.
from services.pricing_monitor import NOMBRE_TIPO, PRECIO, completar_cupones, cupones_de_campana, resumir  # noqa: E402,F401

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salida", default=str(RAIZ / "data" / "pricing"))
    ap.add_argument("--hilos", type=int, default=4)
    a = ap.parse_args()
    from services import meli  # solo LEE el token vigente; nunca lo renueva

    foto = json.loads(gzip.decompress((Path(a.salida) / "monitor.json.gz").read_bytes()))
    pubs = [(p["id"], p["c"]) for p in foto["pubs"]]
    tokens = {c: meli._access_token(c) for c in {c for _, c in pubs}}
    if not all(tokens.values()):
        sys.stderr.write("sin token de alguna cuenta: no se lee nada\n")
        return 1
    cli = httpx.Client(base_url=API, timeout=40)

    def get(ruta: str, tk: str, **params):
        for intento in range(4):
            r = cli.get(ruta, params=params, headers={"Authorization": f"Bearer {tk}"})
            if r.status_code == 429:
                time.sleep(2 + 3 * intento)
                continue
            return r.json() if r.status_code == 200 else None
        return None

    ahora = datetime.now(CDMX)

    def items_de_cupon(pid: str, tk: str) -> list[dict]:
        out, sa = [], None
        for _ in range(100):
            params = {"promotion_type": "SELLER_COUPON_CAMPAIGN", "app_version": "v2", "limit": 50}
            if sa:
                params["search_after"] = sa
            j = get(f"/seller-promotions/promotions/{pid}/items", tk, **params) or {}
            out += j.get("results") or []
            sa = (j.get("paging") or {}).get("searchAfter")
            if not j.get("results") or not sa:
                break
        return out

    campanas: dict = {}
    de_cupones: dict[str, list[dict]] = {}
    for c, tk in tokens.items():
        yo = get("/users/me", tk)
        if yo and yo.get("id"):
            for x in (get(f"/seller-promotions/users/{yo['id']}", tk, app_version="v2") or {}).get("results") or []:
                if x.get("id"):
                    campanas[x["id"]] = x
                if x.get("type") == "SELLER_COUPON_CAMPAIGN" and x.get("status") == "started":
                    for i, e in cupones_de_campana(x, items_de_cupon(x["id"], tk)).items():
                        de_cupones.setdefault(i, []).append(e)

    def uno(par):
        i, c = par
        sp = get(f"/items/{i}/sale_price", tokens[c], context="channel_marketplace") or {}
        pr = get(f"/seller-promotions/items/{i}", tokens[c], app_version="v2")
        pr = completar_cupones(pr if isinstance(pr, list) else [], de_cupones.get(i, []))
        return i, resumir(sp, pr, ahora, campanas) if sp else None

    with ThreadPoolExecutor(a.hilos) as ex:
        res = dict(ex.map(uno, pubs))
    destino = Path(a.salida) / "promos.json"
    destino.write_text(json.dumps({"leido": ahora.isoformat(timespec="minutes"), "promos": res}, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
    sys.stdout.write(f"{sum(1 for v in res.values() if v)} de {len(res)} publicaciones -> {destino}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
