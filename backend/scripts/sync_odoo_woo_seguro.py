"""
Sync Odoo → Woo SEGURO: empuja el stock de Odoo a WooCommerce pero se salta los
SKUs cuya diferencia se explica por VENTAS recientes.

POR QUÉ EXISTE (28-jul). El sync normal (`services/sync_woo.py`) asume que Odoo es
la fuente de verdad del stock. Eso dejó de ser cierto de un lado: desde el 17-jul
**Woo es la fuente de verdad de las VENTAS**, y Odoo ya no registra esas bajas.
Resultado: si Woo tiene menos que Odoo *porque vendió*, un sync ciego le vuelve a
subir el stock — **resucita mercancía vendida** — y con el fan-out encendido esa
inflada se replica a Amazon en segundos.

Regla de exclusión: NO se sube un SKU si tuvo ventas no canceladas en los últimos
`DIAS_VENTAS` días y el hueco cabe dentro de lo vendido (`hueco <= ventas`). Un
hueco mucho mayor que las ventas SÍ es mercancía nueva y sí se aplica.

Uso:
    python -m scripts.sync_odoo_woo_seguro           # dry-run (no escribe)
    python -m scripts.sync_odoo_woo_seguro --aplicar # escribe por REST
"""
from __future__ import annotations

import asyncio
import logging
import sys

logging.disable(logging.WARNING)

DIAS_VENTAS = 30
LOTE = 50


def _ventas_recientes(skus: list[str]) -> dict[str, int]:
    """Pedidos no cancelados por SKU en la ventana (una sola consulta)."""
    from services import db
    if not skus:
        return {}
    filas = db.fetch_all(
        f"""SELECT skus FROM pedidos_ml
            WHERE creado >= NOW() - INTERVAL {DIAS_VENTAS} DAY
              AND estado_wc <> 'cancelled'""")
    conteo = {s: 0 for s in skus}
    for f in filas:
        texto = str(f.get("skus") or "")
        for s in skus:
            if s in texto:
                conteo[s] += 1
    return conteo


def calcular() -> dict:
    """Compara Odoo vs Woo y decide qué aplicar. No escribe nada."""
    from services import odoo, wp_db
    P = wp_db._prefix()

    od = {}
    for p in odoo.listar_catalogo():
        s = (p.get("sku") or "").strip()
        if s:
            od[s] = int(float(p.get("stock") or 0))

    wo = {}
    for r in wp_db._fetch_all(
        f"""SELECT sk.meta_value sku, st.meta_value stock, p.ID, p.post_type,
                   p.post_parent, p.post_status
            FROM {P}postmeta sk
            JOIN {P}posts p ON p.ID = sk.post_id
                 AND p.post_type IN ('product','product_variation')
                 AND p.post_status <> 'trash'
            LEFT JOIN {P}postmeta st ON st.post_id = p.ID AND st.meta_key = '_stock'
            WHERE sk.meta_key = '_sku' AND sk.meta_value <> ''"""):
        s = (r["sku"] or "").strip()
        if not s:
            continue
        try:
            v = int(float(r["stock"])) if r["stock"] not in (None, "") else None
        except (TypeError, ValueError):
            v = None
        wo[s] = {"stock": v, "id": r["ID"], "tipo": r["post_type"],
                 "padre": r["post_parent"], "estado": r["post_status"]}

    candidatos = []
    for s, o in od.items():
        w = wo.get(s)
        if not w or w["stock"] is None:
            continue
        objetivo = max(0, o)          # Woo no maneja negativos
        if objetivo != w["stock"]:
            candidatos.append((s, w["stock"], objetivo))

    ventas = _ventas_recientes([s for s, _, _ in candidatos])
    aplicar, excluidos = [], []
    for s, actual, objetivo in candidatos:
        hueco = objetivo - actual
        n = ventas.get(s, 0)
        # Solo las SUBIDAS pueden resucitar vendido; las bajadas siempre son seguras.
        if hueco > 0 and n > 0 and hueco <= n:
            excluidos.append((s, actual, objetivo, n))
        else:
            aplicar.append((s, actual, objetivo, wo[s]))
    return {"aplicar": aplicar, "excluidos": excluidos, "odoo": len(od), "woo": len(wo)}


async def _escribir(aplicar: list) -> dict:
    from services import woocommerce
    simples, variaciones = [], {}
    for s, actual, objetivo, w in aplicar:
        upd = {"id": w["id"], "manage_stock": True, "stock_quantity": objetivo}
        if w["tipo"] == "product_variation" and w["padre"]:
            variaciones.setdefault(w["padre"], []).append(upd)
        else:
            simples.append(upd)

    ok = err = 0
    async with woocommerce._client() as cli:
        for i in range(0, len(simples), LOTE):
            lote = simples[i:i + LOTE]
            try:
                r = await cli.post("/products/batch", json={"update": lote}, timeout=300.0)
                r.raise_for_status()
                ok += len(lote)
            except Exception as exc:  # noqa: BLE001
                err += len(lote)
                print(f"   ERROR lote simples: {str(exc)[:120]}")
            await asyncio.sleep(0.3)
        for padre, updates in variaciones.items():
            for i in range(0, len(updates), LOTE):
                lote = updates[i:i + LOTE]
                try:
                    r = await cli.post(f"/products/{padre}/variations/batch",
                                       json={"update": lote}, timeout=300.0)
                    r.raise_for_status()
                    ok += len(lote)
                except Exception as exc:  # noqa: BLE001
                    err += len(lote)
                    print(f"   ERROR lote variaciones de {padre}: {str(exc)[:120]}")
                await asyncio.sleep(0.3)
    return {"ok": ok, "error": err}


def main() -> None:
    aplicar_real = "--aplicar" in sys.argv
    r = calcular()
    ap, ex = r["aplicar"], r["excluidos"]
    sube = [a for a in ap if a[2] > a[1]]
    baja = [a for a in ap if a[2] < a[1]]

    print(f"Odoo {r['odoo']} SKUs | Woo {r['woo']} SKUs")
    print(f"\nA APLICAR: {len(ap)} SKUs")
    print(f"   suben: {len(sube)} (+{sum(b - a for _, a, b, _ in sube):,} pzas)")
    print(f"   bajan: {len(baja)} ({sum(b - a for _, a, b, _ in baja):,} pzas)")
    for s, a, b, _ in sorted(sube, key=lambda x: -(x[2] - x[1]))[:10]:
        print(f"      {s:26s} {a:6d} -> {b:6d}  (+{b - a})")
    for s, a, b, _ in sorted(baja, key=lambda x: (x[2] - x[1])):
        print(f"      {s:26s} {a:6d} -> {b:6d}  ({b - a})")

    print(f"\nEXCLUIDOS (el hueco se explica por ventas): {len(ex)}")
    for s, a, b, n in ex:
        print(f"      {s:26s} woo={a:6d} odoo={b:6d} (+{b - a})  ventas{DIAS_VENTAS}d={n}")

    if not aplicar_real:
        print("\n>>> DRY-RUN. Nada escrito. Correr con --aplicar para escribir.")
        return
    print("\n>>> APLICANDO por REST…")
    res = asyncio.run(_escribir(ap))
    print(f">>> Listo: {res['ok']} escritos, {res['error']} con error.")


if __name__ == "__main__":
    main()
