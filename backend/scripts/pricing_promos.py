"""
pricing_promos.py — Arma `backend/data/pricing/promos.json`: la PROMOCIÓN que tiene HOY cada publicación de la
foto de PRICING (columna «Promoción hoy», v0.654.0).

    python backend/scripts/pricing_promos.py [--salida backend/data/pricing] [--hilos 4]

Por publicación de `monitor.json.gz`, SOLO GET a Mercado Libre:
    GET /items/{id}/sale_price?context=channel_marketplace   → lo que se cobra y qué promoción lo pone
    GET /seller-promotions/items/{id}?app_version=v2         → todas sus promociones (vivas, programadas, cupones)

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

NOMBRE_TIPO = {
    "LIGHTNING": "Oferta relámpago", "DOD": "Oferta del día", "SMART_LIGHTNING": "Relámpago co-fondeada",
    "PRICE_DISCOUNT": "Descuento individual", "SELLER_CAMPAIGN": "Campaña propia", "DEAL": "Campaña de ML",
    "SMART": "Co-fondeada con ML", "UNHEALTHY_STOCK": "Acelera tus ventas de Full", "PRICE_MATCHING": "Ajuste con MeLi",
    "VOLUME": "Lleva más, paga menos", "SELLER_COUPON_CAMPAIGN": "Cupón",
}
PRECIO = {"LIGHTNING", "DOD", "SMART_LIGHTNING", "PRICE_DISCOUNT", "SELLER_CAMPAIGN", "DEAL", "SMART", "UNHEALTHY_STOCK", "PRICE_MATCHING"}


def _fecha(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=CDMX)


def resumir(sp: dict, promos: list[dict], ahora: datetime) -> dict:
    """PURA. De `sale_price` y la lista de promociones del ítem, lo que la pantalla muestra."""
    meta = sp.get("metadata") or {}
    vivas, programadas, cupones, volumen = [], [], [], None
    por_id = {}
    for p in promos:
        tipo, st = p.get("type"), p.get("status")
        nombre = (p.get("name") or "").strip() or NOMBRE_TIPO.get(tipo, tipo)
        if p.get("id"):
            por_id[p["id"]] = (tipo, nombre)
        ini, fin = _fecha(p.get("start_date")), _fecha(p.get("finish_date"))
        if tipo == "SELLER_COUPON_CAMPAIGN":
            # La campaña de cupón cubre a la publicación como «candidate»; vale mientras su fecha esté vigente.
            if (not ini or ini <= ahora) and (not fin or fin >= ahora):
                if p.get("fixed_amount"):
                    cupones.append({"nombre": nombre, "monto": p["fixed_amount"], "pct": None})
                elif p.get("fixed_percentage"):
                    cupones.append({"nombre": nombre, "monto": None, "pct": p["fixed_percentage"]})
            continue
        if tipo == "VOLUME" and st == "started":
            volumen = {"lleva": p.get("buy_quantity"), "pct": p.get("discount_percentage"), "nombre": nombre}
            continue
        if tipo in PRECIO and st == "started":
            vivas.append({"tipo": tipo, "etiqueta": NOMBRE_TIPO.get(tipo, tipo), "nombre": nombre, "precio": p.get("price") or None,
                          "hasta": (p.get("finish_date") or "")[:10] or None})
        elif tipo in PRECIO and st == "pending":
            programadas.append({"tipo": tipo, "etiqueta": NOMBRE_TIPO.get(tipo, tipo), "nombre": nombre, "precio": p.get("price") or None,
                                "desde": ini.astimezone(CDMX).strftime("%Y-%m-%d %H:%M") if ini else None})
    # ¿Qué promoción pone el precio que se cobra?
    tipo_meta = (meta.get("promotion_type") or "").lower()
    camp = meta.get("campaign_id")
    if tipo_meta == "lightning":
        cobra = "LIGHTNING"
    elif camp and camp in por_id:
        cobra = por_id[camp][0]
    elif meta.get("promotion_id"):
        # Sin campaña: la oferta propia del ítem (descuento individual); si no hay, la viva de menor precio.
        pd = next((v for v in vivas if v["tipo"] == "PRICE_DISCOUNT"), None)
        cobra = "PRICE_DISCOUNT" if pd else (min(vivas, key=lambda v: v["precio"] or 9e9)["tipo"] if vivas else None)
    else:
        cobra = None
    nombre_cobra = por_id[camp][1] if camp and camp in por_id else next((v["nombre"] for v in vivas if v["tipo"] == cobra), None)
    return {
        "cobra": sp.get("amount"), "normal": sp.get("regular_amount"),
        "por": {"tipo": cobra, "etiqueta": NOMBRE_TIPO.get(cobra, cobra), "nombre": nombre_cobra} if cobra else None,
        "vivas": vivas, "programadas": sorted(programadas, key=lambda x: x["desde"] or ""), "cupones": cupones, "volumen": volumen,
    }


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

    def uno(par):
        i, c = par
        sp = get(f"/items/{i}/sale_price", tokens[c], context="channel_marketplace") or {}
        pr = get(f"/seller-promotions/items/{i}", tokens[c], app_version="v2")
        return i, resumir(sp, pr if isinstance(pr, list) else [], ahora) if sp else None

    with ThreadPoolExecutor(a.hilos) as ex:
        res = dict(ex.map(uno, pubs))
    destino = Path(a.salida) / "promos.json"
    destino.write_text(json.dumps({"leido": ahora.isoformat(timespec="minutes"), "promos": res}, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
    sys.stdout.write(f"{sum(1 for v in res.values() if v)} de {len(res)} publicaciones -> {destino}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
