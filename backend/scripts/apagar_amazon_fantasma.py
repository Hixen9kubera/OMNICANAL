"""
apagar_amazon_fantasma.py — One-shot: apagar (qty 0) los listings MFN de Amazon
que venden piezas que la bodega ya no tiene.

CONTEXTO (auditoría + decisión de Brandon, 18-ago-2026). Amazon SALE del
fan-out: el canal se maneja con FBA y no se le alimenta stock. Pero la
auditoría encontró 5 listings BUYABLE por MFN ofreciendo 878 piezas con Woo en
0 — sobreventa activa, herencia de cuando el fan-out se congeló (29-jul, bug de
vocabulario BUYABLE/PUBLISHED). En Amazon «apagar» un listing FBM ES escribir
cantidad 0: no hay atributo de status, y DELETE borra la oferta (descartado).
Las 419 DISCOVERABLE con cantidad declarada NO se tocan (decisión explícita).

LOS 5 (medidos el 18-ago contra kubera + SP-API):
    HERR-0033-ROJ       400 vs Woo 0
    HERR-0032-ROJ-110V  398 vs Woo 0
    ORG-0205-GRI         49 vs Woo 24  ← se apaga SOLO si Woo llega a 0; si
                                          Woo>0 el ajuste fino ya no es de este
                                          script (ver abajo)
    MUE-0225-PLA         16 vs Woo 0
    CONS-0016-EST         1 vs Woo 0

REGLAS (cada una verificada EN VIVO al momento de correr, nunca contra caché):
  * Solo escribe si el listing está BUYABLE con cantidad > 0 Y Woo tiene 0.
    Con Woo > 0 no es fantasma puro: se reporta y NO se toca (bajarlo a la
    cifra de Woo sería re-alimentar el canal, justo lo que se decidió no
    hacer; el caso ORG-0205-GRI se decide a mano con ese reporte).
  * DISCOVERABLE / ilegible / cantidad None (FBA) → no se toca.
  * Ya en 0 → idempotente, no escribe.
  * DRY-RUN por defecto; --aplicar para escribir. Todo queda en fanout_log
    (accion 'apagar_mfn').

USO:
    python -m scripts.apagar_amazon_fantasma              # dry-run, los 5
    python -m scripts.apagar_amazon_fantasma --aplicar    # ESCRIBE qty 0
    python -m scripts.apagar_amazon_fantasma --sku HERR-0033-ROJ --aplicar
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("apagar_amazon")

from services import db, fanout_stock  # noqa: E402

# Los 5 de la auditoría del 18-ago. Lista CERRADA a propósito: Brandon aprobó
# exactamente estos; para otros candidatos se corre otra auditoría, no se
# amplía la lista a mano.
FANTASMAS = ["HERR-0033-ROJ", "HERR-0032-ROJ-110V", "ORG-0205-GRI",
             "MUE-0225-PLA", "CONS-0016-EST"]


def _registrar(sku: str, dry: bool, woo: int | None, antes: int | None,
               resultado: str, ms: float) -> None:
    try:
        fanout_stock._asegurar_schema()
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO fanout_log
                   (ts, sku, motivo, dry_run, stock_drop, objetivo, canal,
                    cuenta, item_id, accion, stock_canal, resultado, ms)
                   VALUES (%s,%s,%s,%s,%s,0,'amazon','',%s,'apagar_mfn',%s,%s,%s)""",
                (datetime.now(timezone.utc).replace(tzinfo=None), sku,
                 "apagado one-shot MFN fantasma (decisión 18-ago)",
                 1 if dry else 0, woo, sku, antes, resultado[:255], ms))
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo registrar %s en fanout_log: %s", sku, exc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sku", action="append",
                    help="Solo estos SKUs (de la lista cerrada); repetible")
    ap.add_argument("--aplicar", action="store_true",
                    help="Sin esto es DRY-RUN: verifica y reporta, no escribe")
    args = ap.parse_args()

    skus = args.sku or FANTASMAS
    fuera = [s for s in skus if s not in FANTASMAS]
    if fuera:
        log.error("SKUs fuera de la lista aprobada: %s — este script no acepta "
                  "candidatos nuevos (correr otra auditoría).", fuera)
        return 2

    apagados = errores = 0
    for sku in skus:
        t0 = time.time()
        woo = fanout_stock._stock_drop(sku)
        vivo = fanout_stock._amazon_en_vivo(sku)
        ms = round((time.time() - t0) * 1000, 1)
        pre = f"{sku:22s} Woo={woo!s:>4s} Amazon="

        if not vivo.get("ok"):
            log.info("%s?    → SKIP (ilegible: %s)", pre, vivo.get("motivo"))
            _registrar(sku, not args.aplicar, woo, None,
                       f"skip: ilegible ({vivo.get('motivo')})", ms)
            continue
        antes = vivo.get("cantidad")
        estados = "/".join(vivo.get("estados") or ["?"])
        if not vivo.get("vendible"):
            log.info("%s%-4s → SKIP (%s: dormido, no se toca)", pre, antes, estados)
            _registrar(sku, not args.aplicar, woo, antes, f"skip: {estados}", ms)
            continue
        if antes is None:
            log.info("%s?    → SKIP (sin fulfillment_availability legible — ¿FBA?)", pre)
            _registrar(sku, not args.aplicar, woo, None, "skip: cantidad ilegible", ms)
            continue
        if int(antes) == 0:
            log.info("%s0    → ya está apagado (idempotente)", pre)
            _registrar(sku, not args.aplicar, woo, 0, "ya en 0", ms)
            continue
        if woo is None or int(woo) > 0:
            log.info("%s%-4s → SKIP (Woo=%s > 0: no es fantasma puro — decidir "
                     "a mano, este script NO re-alimenta el canal)", pre, antes, woo)
            _registrar(sku, not args.aplicar, woo, antes,
                       f"skip: Woo={woo} — no es fantasma puro", ms)
            continue

        if not args.aplicar:
            log.info("%s%-4s → APAGARÍA (qty 0)  [DRY-RUN]", pre, antes)
            _registrar(sku, True, woo, antes, "DRY-RUN: apagaría (qty 0)", ms)
            continue

        ok, det = fanout_stock._escribir_amazon("", sku, 0)
        ms = round((time.time() - t0) * 1000, 1)
        if ok:
            apagados += 1
            log.info("%s%-4s → APAGADO (qty 0): %s", pre, antes, det)
            _registrar(sku, False, woo, antes, f"ok: {antes}→0 ({det})", ms)
        else:
            errores += 1
            log.error("%s%-4s → ERROR: %s", pre, antes, det)
            _registrar(sku, False, woo, antes, f"ERROR: {det}", ms)
        time.sleep(1.2)   # respiro entre PATCHes

    log.info("\n%s — apagados: %d · errores: %d",
             "APLICADO" if args.aplicar else "DRY-RUN", apagados, errores)
    return 1 if errores else 0


if __name__ == "__main__":
    raise SystemExit(main())
