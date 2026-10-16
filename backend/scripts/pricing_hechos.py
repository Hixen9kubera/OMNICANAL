"""
pricing_hechos.py — Arma `backend/data/pricing/hechos.json`: los precios que SÍ se movieron en ML desde la
pestaña PRICING, para la columna «Hecho» (v0.654.0).

    python backend/scripts/pricing_hechos.py <bitacora.jsonl> [<verificacion.json>] [--salida backend/data/pricing]

Lee la bitácora que deja el script que aplica los descuentos (una línea JSON por movimiento: `accion`
aplicar/revertir, `ok`, `id`, `cuenta`, `sku`, `cobraba`, `normal`, `nuevo`, `cuerpo.promotion_type`,
`cuerpo.finish_date`, `cuando`) y, si se le da, la verificación en vivo (`se_ve_descuento` por publicación).
Lo revertido no entra. NO toca ML ni kubera: solo convierte archivos.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def armar(bitacora: list[dict], verificacion: list[dict] | None = None) -> list[dict]:
    """PURA. El último movimiento vigente de cada publicación."""
    vivos: dict[str, dict] = {}
    for r in bitacora:
        if r.get("accion") == "revertir" and r.get("ok"):
            vivos.pop(r["id"], None)
        elif r.get("accion") == "aplicar" and r.get("ok"):
            cuerpo = r.get("cuerpo") or {}
            vivos[r["id"]] = {
                "id": r["id"], "cuenta": r["cuenta"], "sku": r.get("sku"),
                "antes": r["cobraba"], "nuevo": r["nuevo"], "normal": r.get("normal"),
                "promo": cuerpo.get("promotion_type", "PRICE_DISCOUNT"), "hasta": (cuerpo.get("finish_date") or "")[:10] or None,
                "desde": r["cuando"], "regla": r.get("regla"),
            }
        elif r.get("accion") == "hecho_manual":       # p. ej. otra publicación del mismo producto
            vivos[r["id"]] = {k: r.get(k) for k in ("id", "cuenta", "sku", "antes", "nuevo", "normal", "promo", "hasta", "desde", "regla", "nota")}
    ve = {v["id"]: v for v in verificacion or []}
    for h in vivos.values():
        if h["id"] in ve:
            h["se_ve"] = bool(ve[h["id"]].get("se_ve_descuento"))
    return sorted(vivos.values(), key=lambda h: (h["cuenta"], h["sku"] or "", h["id"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bitacora")
    ap.add_argument("verificacion", nargs="?")
    ap.add_argument("--salida", default=str(RAIZ / "data" / "pricing"))
    a = ap.parse_args()
    bit = [json.loads(l) for l in Path(a.bitacora).read_text(encoding="utf-8").splitlines() if l.strip()]
    ver = json.loads(Path(a.verificacion).read_text(encoding="utf-8")) if a.verificacion else None
    hechos = armar(bit, ver)
    destino = Path(a.salida) / "hechos.json"
    destino.write_text(json.dumps({"hechos": hechos}, ensure_ascii=False, indent=0), encoding="utf-8")
    sys.stdout.write(f"{len(hechos)} movimientos -> {destino}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
