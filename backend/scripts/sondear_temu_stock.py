"""
sondear_temu_stock.py — Sonda CANARIO de `bg.local.goods.stock.edit` (Temu).

POR QUÉ EXISTE. El fan-out va a escribirle stock a Temu (canal DROP-only,
decisión 18-ago), pero el endpoint de escritura JAMÁS se ha llamado: el manual
lo nombra como enganche y su anexo adversario lo marca «parámetros, límites de
lote y semántica: NO VERIFICADO» (docs/TEMU_MANUAL.md §pendientes). No se sabe
ni siquiera si direcciona por goodsId, por skuId interno o por nuestro
outSkuId/extCode. Construir `_escribir_temu` sobre suposiciones sería repetir
el error que en TikTok se evitó leyendo el producto en vivo.

QUÉ HACE.
  1. (lectura) Resuelve el SKU → goodsId con `bg.local.goods.out.sn.check`
     (el oráculo de idempotencia del alta) y localiza la publicación en el
     censo para leer su estado y skuId si el listado lo trae.
  2. (dry-run, default) Imprime EXACTAMENTE el cuerpo que mandaría, con la
     forma elegida, y termina. Cero escrituras.
  3. (--aplicar) UNA llamada a `bg.local.goods.stock.edit` con UN SKU. Imprime
     la respuesta CRUDA (los códigos de error de Temu son la mejor doc que
     tiene esa API), relee la publicación y reporta estado antes/después — la
     lección de CAM-0030: verificar que escribir stock NO altera el estado.

FORMAS CANDIDATAS (--forma). Ninguna está verificada; por eso son opciones y
no un default silencioso:
  1  {"goodsId": …, "skuStockChangeList": [{"skuId": …, "targetStockAvailable": N}]}
  2  {"goodsId": …, "skuStockChangeList": [{"outSkuId": <SKU>, "targetStockAvailable": N}]}
  3  {"goodsId": …, "quantity": N}
Si la 1 pide un skuId que el listado no trae, el propio error 3000000 de Temu
suele decir la ruta del campo que no le cuadró — esa respuesta es el hallazgo.

USO (con dale de Brandon; escribe en tienda VIVA):
    python backend/scripts/sondear_temu_stock.py --sku ACC-0017-MUL
    python backend/scripts/sondear_temu_stock.py --sku ACC-0017-MUL \
        --cantidad 100 --forma 1 --aplicar
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import temu  # noqa: E402


async def _detalle_en_censo(goods_id: str) -> dict | None:
    """Busca la publicación en las cubetas del listado (única lectura que
    existe hoy: no hay endpoint de detalle sondeado)."""
    for g in await temu.listar_productos():
        if str(g.get("goodsId") or g.get("goods_id") or "") == goods_id:
            return g
    return None


def _pinta(titulo: str, datos) -> None:
    print(f"\n── {titulo} " + "─" * max(0, 60 - len(titulo)))
    print(json.dumps(datos, ensure_ascii=False, indent=2, default=str)[:4000])


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sku", required=True, help="SKU nuestro (outGoodsSn)")
    ap.add_argument("--cantidad", type=int, default=None,
                    help="Stock objetivo (obligatorio con --aplicar)")
    ap.add_argument("--forma", type=int, choices=(1, 2, 3), default=1)
    ap.add_argument("--aplicar", action="store_true",
                    help="SIN esto es dry-run: imprime y no llama")
    args = ap.parse_args()

    if not temu.disponible():
        print("Temu no está configurado (faltan TEMU_*).")
        return 2

    # 1) SKU → goodsId
    ya = await temu.skus_ya_publicados([args.sku])
    goods_id = ya.get(args.sku)
    if not goods_id:
        print(f"El SKU {args.sku} NO existe en Temu (sn.check no lo reporta).")
        return 2
    print(f"SKU {args.sku} → goodsId {goods_id}")

    antes = await _detalle_en_censo(goods_id)
    if antes:
        _pinta("publicación ANTES (censo)", antes)
        sku_id = None
        for k in ("skuList", "goodsSkuList", "skus"):
            lst = antes.get(k) or []
            if lst:
                sku_id = (lst[0] or {}).get("skuId") or (lst[0] or {}).get("sku_id")
                break
        print(f"\nskuId visible en el listado: {sku_id or 'NO (el listado no lo trae)'}")
    else:
        sku_id = None
        print("⚠️ La publicación no apareció en el censo (¿cubeta nueva?). "
              "Se puede sondear igual: el error de Temu dirá qué falta.")

    if args.forma == 1:
        cuerpo = {"goodsId": int(goods_id),
                  "skuStockChangeList": [{"skuId": int(sku_id) if sku_id else "«FALTA_SKU_ID»",
                                          "targetStockAvailable": args.cantidad}]}
    elif args.forma == 2:
        cuerpo = {"goodsId": int(goods_id),
                  "skuStockChangeList": [{"outSkuId": args.sku,
                                          "targetStockAvailable": args.cantidad}]}
    else:
        cuerpo = {"goodsId": int(goods_id), "quantity": args.cantidad}

    _pinta(f"cuerpo candidato (forma {args.forma}) para bg.local.goods.stock.edit",
           cuerpo)

    if not args.aplicar:
        print("\nDRY-RUN: no se llamó a Temu. Repetir con --cantidad N --aplicar "
              "para la sonda real (1 SKU, 1 llamada).")
        return 0
    if args.cantidad is None:
        print("--aplicar exige --cantidad.")
        return 2
    if "«FALTA_SKU_ID»" in json.dumps(cuerpo):
        print("La forma 1 necesita el skuId y el listado no lo trae: probar "
              "--forma 2 (outSkuId) o conseguir el skuId de otro endpoint.")
        return 2

    print("\nLlamando bg.local.goods.stock.edit …")
    try:
        res = await temu.llamar("bg.local.goods.stock.edit", cuerpo)
        _pinta("RESPUESTA (éxito por fuera — revisar result.success)", res)
    except RuntimeError as exc:
        # El error ES el hallazgo: 3000000 suele traer la ruta del campo.
        _pinta("RESPUESTA DE ERROR (esto es lo que se vino a averiguar)", str(exc))
        return 1

    despues = await _detalle_en_censo(goods_id)
    if despues:
        _pinta("publicación DESPUÉS (censo)", despues)
        c_antes = (antes or {}).get("_cubeta")
        c_despues = despues.get("_cubeta")
        print(f"\ncubeta antes/después: {c_antes} → {c_despues}"
              + ("  ⚠️ CAMBIÓ — escribir stock ALTERA el estado, documentarlo"
                 if c_antes != c_despues else "  (sin cambio de estado: bien)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
