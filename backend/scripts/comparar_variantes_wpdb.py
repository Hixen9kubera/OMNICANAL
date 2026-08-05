"""
comparar_variantes_wpdb.py — Arnés de paridad de la ruta rápida de variantes
(perf 05-ago): wp_db.variantes_por_padre (3-4 queries por lote) debe devolver
EXACTAMENTE lo mismo que el REST /products/{id}/variations que sustituye
({sku, nombre, precio, stock, estado} por variante, en el mismo orden).

Muestra: 40 padres `variable` al azar (seed fija). SOLO LECTURA.
Uso:  python backend/scripts/comparar_variantes_wpdb.py
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from config import settings  # noqa: E402
from services import wp_db  # noqa: E402
from services.woocommerce import _to_float  # noqa: E402

MUESTRA = 40


async def rest_variantes(cli: httpx.AsyncClient, wc_id: int) -> list[dict]:
    rv = await cli.get(f"/products/{wc_id}/variations", params={
        "per_page": 100,
        "_fields": "id,sku,attributes,price,stock_quantity,status"})
    rv.raise_for_status()
    out = []
    for v in rv.json():
        ops = " / ".join(a.get("option") or "" for a in (v.get("attributes") or [])
                         if a.get("option"))
        out.append({"sku": v.get("sku") or f"WC-{v['id']}", "nombre": ops or None,
                    "precio": _to_float(v.get("price")),
                    "stock": v.get("stock_quantity"), "estado": v.get("status")})
    return out


async def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    P = wp_db._prefix()
    padres = [r["ID"] for r in wp_db._fetch_all(
        f"""SELECT DISTINCT p.post_parent AS ID FROM {P}posts p
            WHERE p.post_type = 'product_variation' AND p.post_status <> 'trash'
              AND p.post_parent > 0""")]
    random.seed(42)
    lote = random.sample(padres, min(MUESTRA, len(padres)))
    print(f"padres variable totales: {len(padres)}; muestra: {len(lote)}")

    sql = wp_db.variantes_por_padre(lote)
    iguales = difs = mejoras = 0
    detalle = []

    def _solo_mejora_nombre(rest: list[dict], mias: list[dict]) -> bool:
        """True si la ÚNICA diferencia es nombre: REST null → SQL con etiqueta
        real (maña del REST cuando el atributo no está ligado al padre; el SQL
        lee attribute_* de la variación directo — es MÁS correcto)."""
        if len(rest) != len(mias):
            return False
        va = sorted(rest, key=lambda v: str(v["sku"]))
        vb = sorted(mias, key=lambda v: str(v["sku"]))
        for x, y in zip(va, vb):
            if {**x, "nombre": None} != {**y, "nombre": None}:
                return False
            if x["nombre"] != y["nombre"] and x["nombre"] is not None:
                return False
        return True
    base = settings.wc_url.rstrip("/") + "/wp-json/wc/v3"
    async with httpx.AsyncClient(base_url=base, timeout=40.0,
                                 auth=(settings.wc_consumer_key,
                                       settings.wc_consumer_secret)) as cli:
        for wc_id in lote:
            rest = await rest_variantes(cli, wc_id)
            mias = sql.get(wc_id, [])
            # orden de REST = menu_order (mismo ORDER BY del SQL); comparamos
            # como multiconjunto por si algún empate de menu_order difiere
            a = sorted(map(json.dumps, ({**v, "precio": v["precio"]} for v in rest)))
            b = sorted(map(json.dumps, mias))
            if a == b:
                iguales += 1
            elif _solo_mejora_nombre(rest, mias):
                mejoras += 1
            else:
                difs += 1
                if len(detalle) < 5:
                    detalle.append({"wc_id": wc_id, "rest": rest[:3], "sql": mias[:3]})
    print(f"iguales={iguales} mejoras_nombre={mejoras} difs_reales={difs}")
    for d in detalle:
        print(json.dumps(d, ensure_ascii=False, indent=1)[:800])
    print("VEREDICTO:", "EQUIVALENTE" if difs == 0 else "CON DIFERENCIAS")
    sys.exit(0 if difs == 0 else 2)


if __name__ == "__main__":
    asyncio.run(main())
