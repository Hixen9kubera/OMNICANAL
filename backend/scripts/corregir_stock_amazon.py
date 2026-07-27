"""
corregir_stock_amazon.py — Corrección one-shot del stock de Amazon (DROP/MFN).

POR QUÉ EXISTE (incidente 2026-07-27): Amazon quedó ofreciendo piezas que ya no
existen en bodega. Diagnóstico con datos:

  * 91 SKUs BUYABLE en Amazon con 5,693 piezas ofrecidas y stock 0 en Woo.
  * De esos, 90 tienen 0 TAMBIÉN en Odoo → Woo está bien; el desactualizado es
    Amazon. La sincronización Odoo→Woo del 17-jul NO fue la causa.
  * Causa real: nada empujaba Woo → Amazon (el sync de 15 min solo LEE y
    `sync_woo.py` solo va de Odoo a Woo). Es justo el hueco que cierra el
    fan-out (services/fanout_stock.py), que hoy corre en DRY-RUN.

QUÉ HACE: para cada SKU pasado (o los del JSON de diagnóstico), lee el stock
real de Woo y lo escribe en Amazon vía Listings Items API. Idempotente: si
Amazon ya tiene el valor correcto, no escribe.

SEGURIDAD:
  * Por defecto corre en DRY-RUN. Requiere --aplicar para escribir.
  * Solo toca listings BUYABLE (los DISCOVERABLE están dormidos: escribirles los
    despertaría).
  * Nunca escribe si no pudo leer el estado real del listing.
  * Registra cada operación en la tabla fanout_log (misma bitácora del panel).

USO:
    python -m scripts.corregir_stock_amazon                 # dry-run, todos
    python -m scripts.corregir_stock_amazon --limite 1      # dry-run, 1 SKU
    python -m scripts.corregir_stock_amazon --aplicar       # ESCRIBE en Amazon
    python -m scripts.corregir_stock_amazon --sku ABC-123 --aplicar
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("corregir_amazon")

from services import db, fanout_stock, wp_db  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent.parent
JSON_DIAG = RAIZ / "fanout_sobreventa.json"


def _stock_woo(skus: list[str]) -> dict[str, int | None]:
    P = wp_db._prefix()
    salida: dict[str, int | None] = {}
    for i in range(0, len(skus), 400):
        ch = skus[i:i + 400]
        ph = ",".join(["%s"] * len(ch))
        for r in wp_db._fetch_all(
            f"""SELECT s.meta_value sku, st.meta_value stock, ms.meta_value gestiona
                FROM {P}postmeta s
                JOIN {P}posts p ON p.ID = s.post_id AND p.post_status <> 'trash'
                LEFT JOIN {P}postmeta st ON st.post_id = p.ID AND st.meta_key = '_stock'
                LEFT JOIN {P}postmeta ms ON ms.post_id = p.ID AND ms.meta_key = '_manage_stock'
                WHERE s.meta_key = '_sku' AND s.meta_value IN ({ph})""",
            tuple(ch),
        ):
            # Sin gestión de stock Woo lo da por infinito: no hay número que empujar.
            if (r.get("gestiona") or "").lower() != "yes":
                salida[r["sku"]] = None
                continue
            try:
                salida[r["sku"]] = int(float(r["stock"])) if r["stock"] not in (None, "") else None
            except (TypeError, ValueError):
                salida[r["sku"]] = None
    return salida


def _bitacora(sku: str, woo: int, amazon_antes, accion: str, resultado: str, ms: float) -> None:
    fanout_stock._asegurar_schema()
    try:
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO fanout_log
                   (ts, sku, motivo, dry_run, stock_drop, objetivo, canal, cuenta,
                    item_id, accion, stock_canal, resultado, ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (datetime.now(timezone.utc).replace(tzinfo=None), sku,
                 "correccion one-shot sobreventa", 0, woo, woo, "amazon", "",
                 sku[:64], accion, amazon_antes, resultado[:255], ms))
    except Exception as exc:  # noqa: BLE001
        log.warning("  (no se pudo registrar en fanout_log: %s)", exc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="ESCRIBE en Amazon (sin esto: dry-run)")
    ap.add_argument("--limite", type=int, default=None, help="máximo de SKUs a procesar")
    ap.add_argument("--sku", action="append", help="SKU concreto (repetible)")
    args = ap.parse_args()

    if args.sku:
        skus = args.sku
    else:
        if not JSON_DIAG.exists():
            log.error("No existe %s — corre primero el diagnóstico.", JSON_DIAG)
            return 1
        skus = [d["sku"] for d in json.load(open(JSON_DIAG, encoding="utf-8"))]
    if args.limite:
        skus = skus[:args.limite]

    modo = "APLICANDO (escribe en Amazon)" if args.aplicar else "DRY-RUN (no escribe)"
    log.info("Corrección de stock en Amazon · %s · %d SKU(s)\n", modo, len(skus))

    woo = _stock_woo(skus)
    stats = {"escritos": 0, "sin_cambio": 0, "omitidos": 0, "errores": 0, "piezas": 0}

    for i, sku in enumerate(skus, 1):
        t0 = time.time()
        objetivo = woo.get(sku)
        if objetivo is None:
            log.info("[%3d/%d] %-26s OMITIDO — Woo sin stock gestionado", i, len(skus), sku)
            stats["omitidos"] += 1
            continue
        vivo = fanout_stock._amazon_en_vivo(sku)
        if not vivo.get("ok"):
            log.info("[%3d/%d] %-26s OMITIDO — Amazon ilegible: %s", i, len(skus), sku, vivo.get("motivo"))
            stats["omitidos"] += 1
            continue
        if not vivo.get("vendible"):
            log.info("[%3d/%d] %-26s OMITIDO — dormido (%s)", i, len(skus), sku,
                     "/".join(vivo.get("estados") or []))
            stats["omitidos"] += 1
            continue
        actual = vivo.get("cantidad")
        if actual is None:
            log.info("[%3d/%d] %-26s OMITIDO — sin cantidad legible", i, len(skus), sku)
            stats["omitidos"] += 1
            continue
        if int(actual) == int(objetivo):
            log.info("[%3d/%d] %-26s sin cambio (ya en %s)", i, len(skus), sku, objetivo)
            stats["sin_cambio"] += 1
            continue

        if not args.aplicar:
            log.info("[%3d/%d] %-26s DRY-RUN  %s → %s  (%+d)", i, len(skus), sku,
                     actual, objetivo, objetivo - actual)
            _bitacora(sku, objetivo, actual, "escribir", "DRY-RUN (no se escribió)",
                      round((time.time() - t0) * 1000, 1))
            stats["escritos"] += 1
            stats["piezas"] += abs(objetivo - actual)
            continue

        ok, det = fanout_stock._escribir_amazon("", sku, int(objetivo))
        ms = round((time.time() - t0) * 1000, 1)
        if ok:
            log.info("[%3d/%d] %-26s OK  %s → %s", i, len(skus), sku, actual, objetivo)
            stats["escritos"] += 1
            stats["piezas"] += abs(objetivo - actual)
            _bitacora(sku, objetivo, actual, "escribir", f"ok ({det})", ms)
        else:
            log.info("[%3d/%d] %-26s ERROR: %s", i, len(skus), sku, det[:110])
            stats["errores"] += 1
            _bitacora(sku, objetivo, actual, "escribir", f"ERROR: {det}", ms)

    log.info("\nRESUMEN · %s", modo)
    log.info("  escritos/planeados: %d  (%d piezas de diferencia)", stats["escritos"], stats["piezas"])
    log.info("  sin cambio: %d · omitidos: %d · errores: %d",
             stats["sin_cambio"], stats["omitidos"], stats["errores"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
