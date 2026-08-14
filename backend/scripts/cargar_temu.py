"""
cargar_temu.py — Trae el catálogo VIVO de Temu a `channel.listings`.

Pieza 1 de las seis que abren un canal (ver `docs/FLUJO_POR_CANAL.md`). Sin esto
el panel no sabe que Temu existe: hoy tiene 0 publicaciones cargadas y lo único
vivo son sus pedidos, que entran por el sondeo de M2E.

A DIFERENCIA DE TIKTOK, que se cargó desde CSVs exportados a mano, aquí se lee
la API en vivo: `bg.local.goods.list.query` recorriendo sus dos cubetas. Un CSV
nace viejo el día que se exporta; esto se puede volver a correr cuando sea.

DECISIONES QUE COSTARON MEDICIÓN (no cambiarlas sin volver a medir):

  · EL PRECIO. En `list.query`, `price` y `retailPrice.amount` vienen en PESOS
    con decimales (338.46), pero `marketPrice` viene en CENTAVOS (11400 = $114).
    El mismo concepto con dos formatos en la MISMA respuesta — por eso aquí solo
    se usa `price`. Confirmado el 14-ago contra 171 productos vivos.

  · EL ESTADO NO SE TRADUCE. Temu devuelve `status4VO`/`subStatus4VO` como
    números (2/8, 3/2, 4/7…) y **no hay documentación de qué significan**; los
    códigos que anotó el sondeo del 12-ago ya no son los que devuelve la API hoy.
    Se guardan CRUDOS, como "2/8". Inventarles un significado sería exactamente
    el error de TikTok, donde suponer que APPROVED = a la venta habría atado el
    fan-out a una casualidad del dato. Mientras no se verifiquen, **Temu no debe
    entrar al fan-out de stock**: no sabemos cuál de esas publicaciones vende.

  · LOS 14 SKUs DE PRUEBA (`KBTEST-*`, `KBPROBE-*`, `KBL-*`) se OMITEN. Existen
    en Temu porque se crearon para no quemar SKUs reales durante el sondeo, pero
    no son productos nuestros y no tienen por qué ensuciar el catálogo.

Uso:
    python -m scripts.cargar_temu              # dry-run: dice qué haría
    python -m scripts.cargar_temu --aplicar    # escribe en kubera
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import supabase_db as sdb, temu  # noqa: E402

CANAL = "temu"
CUENTA = "TEMU"
MALL_ID = "635517742093915"
PREFIJOS_PRUEBA = ("KBTEST", "KBPROBE", "KBL-")


def _precio(g: dict[str, Any]) -> float | None:
    """El precio en PESOS. Ver la nota del encabezado: `marketPrice` NO sirve."""
    for clave in ("price",):
        v = g.get(clave)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    retail = g.get("retailPrice") or {}
    try:
        return float(retail.get("amount"))
    except (TypeError, ValueError):
        return None


def _estado(g: dict[str, Any]) -> str:
    return f"{g.get('status4VO')}/{g.get('subStatus4VO')}"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true",
                    help="escribe de verdad (sin esto solo informa)")
    args = ap.parse_args()

    if not temu.disponible():
        print("Temu no está configurado (faltan las TEMU_* en el entorno).")
        return

    print("Leyendo el catálogo de Temu…")
    productos = await temu.listar_productos()
    pruebas = [g for g in productos
               if str(g.get("outGoodsSn") or "").upper().startswith(PREFIJOS_PRUEBA)]
    reales = [g for g in productos if g not in pruebas and g.get("outGoodsSn")]
    sin_sku = [g for g in productos if not g.get("outGoodsSn")]

    print(f"  {len(productos)} publicaciones · {len(reales)} con SKU nuestro · "
          f"{len(pruebas)} de prueba (se omiten) · {len(sin_sku)} sin outGoodsSn")

    # ── Identidad: el canal ya existe; la cuenta se crea si falta ────────────
    cuenta = sdb.fetch_one(
        "select id from core.accounts where channel_id=%s and legacy_code=%s",
        (CANAL, CUENTA))
    if not cuenta:
        print(f"  cuenta {CUENTA} de {CANAL}: NO existe, se creará")
        # En dry-run NO se crea, pero el informe SIGUE: que falte la cuenta no es
        # razón para dejar de decir qué pasaría con las 157 publicaciones.
        if args.aplicar:
            cuenta = sdb.execute_returning(
                """insert into core.accounts (channel_id, legacy_code, external_id,
                                              label, is_active)
                   values (%s, %s, %s, %s, true) returning id""",
                (CANAL, CUENTA, MALL_ID, "Kubera (Temu)"))
    else:
        print(f"  cuenta {CUENTA} de {CANAL}: ya existe")
    account_id = cuenta["id"] if cuenta else None

    # ── SKUs que el maestro no conoce ───────────────────────────────────────
    skus = sorted({str(g["outGoodsSn"]).strip() for g in reales})
    conocidos = {r["sku"] for r in sdb.fetch_all(
        "select sku::text sku from core.products where sku = any(%s)", (skus,))}
    faltantes = [s for s in skus if s not in conocidos]
    print(f"  {len(conocidos)} SKUs ya en core.products · {len(faltantes)} nuevos")
    if faltantes:
        print("    nuevos:", ", ".join(faltantes[:10]),
              "…" if len(faltantes) > 10 else "")

    if not args.aplicar:
        print("\nDRY-RUN. Se escribirían:")
        print(f"  · {len(faltantes)} filas en core.products (identidad, status draft)")
        print(f"  · {len(reales)} filas en channel.listings (canal={CANAL})")
        est: dict[str, int] = {}
        for g in reales:
            est[_estado(g)] = est.get(_estado(g), 0) + 1
        print("  estados crudos de Temu (sin traducir a propósito):")
        for k, v in sorted(est.items(), key=lambda x: -x[1]):
            print(f"      status/sub {k}: {v}")
        return

    # Identidad primero (misma regla que el espejo del DROP): un SKU que el
    # maestro no conoce se registra solo, o la llave foránea tumba la carga.
    if faltantes:
        sdb.execute(
            """insert into core.products (sku, status, source)
               select unnest(%s::text[]), 'draft', 'temu'
               on conflict (sku) do nothing""", (faltantes,))

    escritas = 0
    for g in reales:
        sdb.execute(
            """insert into channel.listings
                 (sku, account_id, canal, listing_id, status, price, stock_own,
                  category_id, currency, store_name, updated_at)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
               on conflict (sku, account_id, canal) do update set
                 listing_id  = excluded.listing_id,
                 status      = excluded.status,
                 price       = excluded.price,
                 stock_own   = excluded.stock_own,
                 category_id = excluded.category_id,
                 currency    = excluded.currency,
                 updated_at  = now()""",
            (str(g["outGoodsSn"]).strip(), account_id, CANAL,
             str(g.get("goodsId") or ""), _estado(g), _precio(g),
             int(g.get("quantity") or 0), str(g.get("catId") or "") or None,
             (g.get("currency") or "MXN"), "Temu"))
        escritas += 1
    print(f"\nListo: {escritas} publicaciones de Temu en channel.listings.")


if __name__ == "__main__":
    asyncio.run(main())
