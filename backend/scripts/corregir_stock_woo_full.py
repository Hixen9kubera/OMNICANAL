"""
corregir_stock_woo_full.py — Quita de WooCommerce el stock que ya está en FULL.

HALLAZGO (auditoría 2026-07-27): de los 172 SKUs donde Woo declara MÁS stock que
Odoo, hay un grupo donde la diferencia es EXACTAMENTE el stock que vive en las
bodegas FULL de Mercado Libre:

    HERR-0034-AZL-127V   Woo=600  Odoo=400  gap=200  FULL=198
    HERR-0028-MET        Woo=225  Odoo= 75  gap=150  FULL=148
    TEC-0492-MUL         Woo=307  Odoo=207  gap=100  FULL=100

Lectura: al enviar mercancía a FULL, Odoo SÍ descontó la salida del almacén
propio; Woo NO. Resultado: Woo cuenta esas piezas como disponibles en bodega
cuando físicamente están en el almacén de Mercado Libre → se ofrecen en los
canales DROP (Amazon, web) y se venden sin existencia. Es la MISMA sobreventa
que ya corregimos en Amazon, pero desde el otro lado.

Aquí Odoo es la fuente correcta, así que se alinea Woo = Odoo SOLO para ese
grupo verificado. Los demás divergentes NO se tocan:
  * 113 SKUs sin stock en FULL: casi todos `draft` nacidos en Woo (contenedor
    nuevo) que Odoo todavía no conoce — ponerlos en 0 borraría inventario real.
  * 33 SKUs con FULL que no cuadra: requieren revisión caso por caso.

USO:
    python -m scripts.corregir_stock_woo_full            # dry-run
    python -m scripts.corregir_stock_woo_full --aplicar  # escribe en Woo
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("corregir_woo_full")

from services import db, odoo, woocommerce, wp_db  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent.parent
CLASIF = RAIZ / "auditoria_clasificada.json"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    skus = json.load(open(CLASIF, encoding="utf-8"))["confirmados_full"]
    log.info("Corrección Woo = Odoo (stock que ya está en FULL) · %s · %d SKU(s)\n",
             "APLICANDO" if args.aplicar else "DRY-RUN", len(skus))

    # Odoo (verdad para este grupo)
    uid = odoo._uid()
    od: dict[str, int] = {}
    for i in range(0, len(skus), 40):
        for p in odoo._models().execute_kw(
            odoo.settings.odoo_db, uid, odoo.settings.odoo_password,
            "product.product", "search_read", [[["default_code", "in", skus[i:i + 40]]]],
            {"fields": ["default_code", "qty_available"]},
        ):
            od[(p.get("default_code") or "").strip()] = int(p.get("qty_available") or 0)

    # wc_id + tipo (producto simple o variación) + stock actual
    P = wp_db._prefix()
    ph = ",".join(["%s"] * len(skus))
    filas = wp_db._fetch_all(
        f"""SELECT s.meta_value sku, p.ID wc_id, p.post_type, p.post_parent,
                   st.meta_value stock
            FROM {P}postmeta s
            JOIN {P}posts p ON p.ID = s.post_id AND p.post_status <> 'trash'
            LEFT JOIN {P}postmeta st ON st.post_id = p.ID AND st.meta_key = '_stock'
            WHERE s.meta_key = '_sku' AND s.meta_value IN ({ph})""", tuple(skus))

    full = {r["sku"]: int(r["f"] or 0) for r in db.fetch_all(
        f"""SELECT sku, SUM(COALESCE(stock_full,0)+COALESCE(stock_fba,0)) f
            FROM canal_inventario WHERE sku IN ({ph}) GROUP BY sku""", tuple(skus))}

    ok = err = 0
    async with woocommerce._client() as cli:
        for r in filas:
            sku = r["sku"]
            destino = od.get(sku)
            actual = int(float(r["stock"])) if r["stock"] not in (None, "") else None
            if destino is None or actual is None or destino == actual:
                continue
            log.info("  %-26s Woo %s → %s   (FULL=%s)", sku, actual, destino, full.get(sku, 0))
            if not args.aplicar:
                ok += 1
                continue
            try:
                if r["post_type"] == "product_variation":
                    resp = await cli.put(f"/products/{r['post_parent']}/variations/{r['wc_id']}",
                                         json={"stock_quantity": destino}, timeout=60.0)
                else:
                    resp = await cli.put(f"/products/{r['wc_id']}",
                                         json={"stock_quantity": destino}, timeout=60.0)
                if resp.status_code in (200, 201):
                    ok += 1
                else:
                    log.info("     ERROR HTTP %s: %s", resp.status_code, resp.text[:100])
                    err += 1
            except Exception as exc:  # noqa: BLE001
                log.info("     ERROR %s", exc)
                err += 1

    log.info("\nRESUMEN: %d %s · %d errores", ok,
             "aplicados" if args.aplicar else "a aplicar (dry-run)", err)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
