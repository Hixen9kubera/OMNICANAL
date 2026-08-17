"""
Pone en `publish` los productos de Woo que YA están publicados en algún canal.

POR QUÉ (28-jul). Auditando publicados vs status aparecieron **125 SKUs vivos en
un canal pero en `draft`/`inprogress` en WooCommerce**. Esos productos son
INVISIBLES en el panel (Productos y Omnicanal excluyen drafts), así que nadie los
ve aunque se estén vendiendo. Y no es teórico: `TEC-1841-ROS` estaba en `draft`
con stock 0 y **vendió** el 29-jul en ML FULL ($1,585.92, pedido WC #109551).

CRITERIO: el mismo del panel (`services/presencia.py`) — un SKU está publicado si
tiene `item_id` en `canal_inventario` y su situación NO es `closed`. Incluye
`paused`: una pausada existe en el canal, solo que apagada.

OJO: `publish` en Woo también lo hace visible en chunche.shop. Es la consecuencia
buscada (decisión de Brandon, 29-jul): si ya se vende en un marketplace, no hay
razón para esconderlo de la tienda.

Las variaciones NO se tocan por su cuenta: se publica el PADRE, que es quien
gobierna la visibilidad.

Uso:
    python -m scripts.corregir_status_publicados            # dry-run
    python -m scripts.corregir_status_publicados --aplicar
"""
from __future__ import annotations

import asyncio
import logging
import sys

logging.disable(logging.WARNING)

A_CORREGIR = ("draft", "inprogress")


def candidatos() -> list[dict]:
    from services import db, wp_db
    P = wp_db._prefix()
    # OJO: NO se filtra por `item_id`. En Amazon la Listings Items API direcciona
    # por SKU y sus ~1,600 filas traen `item_id` NULL; exigirlo dejaba fuera el
    # canal DROP más grande (el fan-out ya tenía esa cicatriz documentada).
    skus = [r["sku"] for r in db.fetch_all(
        """SELECT DISTINCT sku FROM canal_inventario
           WHERE LOWER(COALESCE(situacion,'')) <> 'closed'
             AND (item_id IS NOT NULL AND item_id <> '' OR canal = 'amazon')""")]
    fuera, vistos = [], set()
    for i in range(0, len(skus), 900):
        lote = "','".join(skus[i:i + 900])
        for r in wp_db._fetch_all(
            f"""SELECT sk.meta_value sku, p.ID hijo, p.post_type tipo, p.post_parent padre,
                       p.post_status est_hijo, pa.post_status est_padre, p.post_title titulo
                FROM {P}postmeta sk
                JOIN {P}posts p ON p.ID = sk.post_id AND p.post_status <> 'trash'
                LEFT JOIN {P}posts pa ON pa.ID = p.post_parent
                     AND p.post_type = 'product_variation'
                WHERE sk.meta_key = '_sku' AND sk.meta_value IN ('{lote}')"""):
            # La visibilidad la manda el padre cuando es variación.
            es_var = r["tipo"] == "product_variation" and r["padre"]
            objetivo = int(r["padre"]) if es_var else int(r["hijo"])
            estado = r["est_padre"] if es_var else r["est_hijo"]
            if estado not in A_CORREGIR or objetivo in vistos:
                continue
            vistos.add(objetivo)
            fuera.append({"sku": r["sku"], "wc_id": objetivo, "estado": estado,
                          "titulo": (r["titulo"] or "")[:44],
                          "via_variacion": bool(es_var)})
    return fuera


async def aplicar(items: list[dict]) -> dict:
    """Cambia el status por REST (nunca DML sobre wp_*)."""
    from services import woocommerce
    ok = err = 0
    async with woocommerce._client() as cli:
        for i in range(0, len(items), 50):
            lote = [{"id": x["wc_id"], "status": "publish"} for x in items[i:i + 50]]
            try:
                r = await cli.post("/products/batch", json={"update": lote}, timeout=300.0)
                r.raise_for_status()
                ok += len(lote)
                print(f"   lote {i//50 + 1}: {len(lote)} publicados")
            except Exception as exc:  # noqa: BLE001
                err += len(lote)
                print(f"   lote {i//50 + 1} FALLÓ: {str(exc)[:140]}")
            await asyncio.sleep(0.5)
    return {"ok": ok, "error": err}


def main() -> None:
    # CANDADO (16-ago): el padrón de "¿está publicado en algún canal?" sale de
    # `canal_inventario`, congelada el 13-ago 04:23. Dos maneras de equivocarse,
    # y la segunda es la que cuesta: una publicación que se CERRÓ después del
    # 13-ago sigue apareciendo como viva, y este script pondría su producto en
    # `publish` — visible en chunche.shop, según su propio docstring— por un
    # canal que ya no existe.
    from scripts import _candado_congelado
    _candado_congelado.exigir_viva(
        "canal_inventario", va_a_escribir="--aplicar" in sys.argv,
        que_decide="qué productos están publicados en algún canal y por lo "
                   "tanto deben pasar de draft a `publish` (visibles en la tienda)",
        alternativa="channel.listings, que es donde vive el estado de los "
                    "canales desde el corte (services/channel_read.py)")
    items = candidatos()
    print(f"Productos publicados en algún canal pero en draft/inprogress: {len(items)}")
    d = sum(1 for x in items if x["estado"] == "draft")
    print(f"   draft: {d}   inprogress: {len(items)-d}")
    print(f"   (de esos, {sum(1 for x in items if x['via_variacion'])} se corrigen "
          "publicando el PADRE de una variación)\n")
    for x in items[:20]:
        print(f"   {x['sku']:26s} id={x['wc_id']:6d} {x['estado']:11s} {x['titulo']}")
    if len(items) > 20:
        print(f"   … y {len(items)-20} más")
    if "--aplicar" not in sys.argv:
        print("\n>>> DRY-RUN. Nada escrito. Correr con --aplicar.")
        return
    print("\n>>> APLICANDO (esto también los hace visibles en chunche.shop)…")
    r = asyncio.run(aplicar(items))
    print(f">>> Listo: {r['ok']} publicados, {r['error']} con error.")


if __name__ == "__main__":
    main()
