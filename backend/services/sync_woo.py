"""
sync_woo.py — Sincronización masiva hacia WooCommerce: STOCK y COSTO.

Un solo recorrido del catálogo hace ambas cosas (mitad de requests):

  • STOCK: Odoo (qty_available) → Woo (stock_quantity), para TODOS los
    productos sin importar su status, a NIVEL VARIANTE (las variaciones se
    actualizan una a una vía el batch de variaciones de su padre; el padre
    variable suma solo — así lo maneja Woo).
  • COSTO: costos_finales (costo_unitario, fallback costo_producto) → meta
    `costo` del producto/variación en Woo (match por SKU).

Solo se escribe lo que CAMBIÓ (se compara contra el valor actual en Woo).
Va despacio a propósito (pausas entre llamadas): el hosting bloquea por
volumen de tráfico (protección anti-bot).

Progreso en memoria → GET /api/sync/woo/progreso.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from services import db, odoo, woocommerce

log = logging.getLogger("omnicanal.sync_woo")

_LOTE_LOOKUP = 50   # SKUs por consulta sku=a,b,c
_LOTE_UPDATE = 50   # productos por batch de escritura
_PAUSA = 0.8        # segundos entre llamadas (suave con el hosting)

_progreso: dict[str, Any] = {"estado": "sin_ejecutar"}
_corriendo = False


def progreso() -> dict[str, Any]:
    return _progreso


def _set(**campos: Any) -> None:
    _progreso.update(campos, actualizado=time.time())


def _costos_finales() -> dict[str, float]:
    """{ sku: costo } de TODA la tabla costos_finales."""
    salida: dict[str, float] = {}
    try:
        rows = db.fetch_all(
            "SELECT sku, costo_unitario, costo_producto FROM costos_finales"
        )
        for r in rows:
            c = r.get("costo_unitario") or r.get("costo_producto")
            if c:
                salida[r["sku"]] = round(float(c), 2)
    except Exception as exc:  # noqa: BLE001
        log.warning("costos_finales no disponible: %s", exc)
    return salida


def _meta_costo_actual(p: dict[str, Any]) -> str | None:
    for m in p.get("meta_data") or []:
        if m.get("key") == "costo":
            return str(m.get("value"))
    return None


async def sincronizar_stock_y_costos(limite: int | None = None) -> dict[str, Any]:
    """
    Recorre todos los SKUs de Odoo (stock) ∪ costos_finales (costo), los busca
    en Woo por SKU y escribe stock/costo donde difieran. `limite` corta el
    número de SKUs procesados (para corridas de prueba).
    """
    global _corriendo
    if _corriendo:
        return {"ok": False, "motivo": "Ya hay una sincronización corriendo."}
    _corriendo = True
    try:
        return await _correr(limite)
    finally:
        _corriendo = False


async def _correr(limite: int | None) -> dict[str, Any]:
    _set(estado="preparando", paso="Leyendo stock de Odoo y costos de la DB…")
    catalogo = await asyncio.to_thread(odoo.listar_catalogo)
    stock_odoo = {p["sku"]: int(p.get("stock") or 0) for p in catalogo}
    costos = await asyncio.to_thread(_costos_finales)

    skus = sorted(set(stock_odoo) | set(costos))
    if limite:
        skus = skus[:limite]
    _set(estado="corriendo", total_skus=len(skus), revisados=0,
         stock_actualizado=0, costo_actualizado=0, sin_match_woo=0, errores=0)

    simples_pend: list[dict[str, Any]] = []
    variaciones_pend: dict[int, list[dict[str, Any]]] = {}  # parent_id → updates
    resumen = {"stock": 0, "costo": 0, "sin_match": 0, "errores": 0}

    # Lookup de productos: MySQL directo si está configurado (una consulta para
    # TODO el catálogo); si no, por API en lotes de 50.
    from services import wp_db
    lookup_db: dict[str, dict[str, Any]] | None = None
    if wp_db.disponible():
        _set(estado="corriendo", paso="Lookup masivo por MySQL…")
        lookup_db = await asyncio.to_thread(wp_db.productos_por_sku, skus)
        log.info("sync_woo: lookup MySQL directo → %d SKUs encontrados", len(lookup_db))

    campos = "id,sku,type,parent_id,stock_quantity,manage_stock,meta_data"

    async with woocommerce._client() as cli:

        async def _flush_simples() -> None:
            while simples_pend:
                lote, simples_pend[:] = simples_pend[:_LOTE_UPDATE], simples_pend[_LOTE_UPDATE:]
                try:
                    r = await cli.post("/products/batch", json={"update": lote}, timeout=300.0)
                    r.raise_for_status()
                except Exception as exc:  # noqa: BLE001
                    resumen["errores"] += len(lote)
                    log.warning("batch simples falló: %s", exc)
                await asyncio.sleep(_PAUSA)

        async def _flush_variaciones() -> None:
            pendientes = dict(variaciones_pend)
            variaciones_pend.clear()
            for parent_id, updates in pendientes.items():
                for i in range(0, len(updates), _LOTE_UPDATE):
                    lote = updates[i:i + _LOTE_UPDATE]
                    try:
                        r = await cli.post(
                            f"/products/{parent_id}/variations/batch",
                            json={"update": lote}, timeout=300.0,
                        )
                        r.raise_for_status()
                    except Exception as exc:  # noqa: BLE001
                        resumen["errores"] += len(lote)
                        log.warning("batch variaciones de %s falló: %s", parent_id, exc)
                    await asyncio.sleep(_PAUSA)

        for i in range(0, len(skus), _LOTE_LOOKUP):
            chunk = skus[i:i + _LOTE_LOOKUP]
            if lookup_db is not None:
                # Normaliza el resultado de MySQL al mismo formato que la API.
                por_sku = {
                    s: {
                        "id": d["wc_id"], "type": d["tipo"], "parent_id": d["parent_id"],
                        "stock_quantity": d["stock"], "manage_stock": d["manage_stock"],
                        "meta_data": [{"key": "costo", "value": d["costo"]}] if d["costo"] else [],
                    }
                    for s in chunk if (d := lookup_db.get(s))
                }
            else:
                try:
                    r = await cli.get("/products", params={
                        "sku": ",".join(chunk), "status": "any",
                        "per_page": 100, "_fields": campos,
                    })
                    r.raise_for_status()
                    por_sku = {(p.get("sku") or "").strip(): p for p in r.json()}
                except Exception as exc:  # noqa: BLE001
                    resumen["errores"] += len(chunk)
                    log.warning("lookup skus falló (lote %d): %s", i // _LOTE_LOOKUP + 1, exc)
                    await asyncio.sleep(_PAUSA * 3)
                    continue

            for sku in chunk:
                p = por_sku.get(sku)
                if not p:
                    resumen["sin_match"] += 1
                    continue

                update: dict[str, Any] = {"id": p["id"]}
                # STOCK (si el SKU está en Odoo y difiere)
                if sku in stock_odoo:
                    objetivo = max(0, stock_odoo[sku])
                    actual = p.get("stock_quantity")
                    if not p.get("manage_stock") or actual != objetivo:
                        update["manage_stock"] = True
                        update["stock_quantity"] = objetivo
                        resumen["stock"] += 1
                # COSTO (si hay costo en costos_finales y difiere del meta)
                if sku in costos:
                    nuevo = f"{costos[sku]:.2f}"
                    if _meta_costo_actual(p) != nuevo:
                        update["meta_data"] = [{"key": "costo", "value": nuevo}]
                        resumen["costo"] += 1

                if len(update) > 1:
                    if p.get("type") == "variation" and p.get("parent_id"):
                        variaciones_pend.setdefault(p["parent_id"], []).append(update)
                    else:
                        simples_pend.append(update)

            # Escribir en cuanto se junta un lote (memoria acotada).
            if len(simples_pend) >= _LOTE_UPDATE:
                await _flush_simples()
            if sum(len(v) for v in variaciones_pend.values()) >= _LOTE_UPDATE:
                await _flush_variaciones()

            _set(revisados=min(i + _LOTE_LOOKUP, len(skus)),
                 stock_actualizado=resumen["stock"], costo_actualizado=resumen["costo"],
                 sin_match_woo=resumen["sin_match"], errores=resumen["errores"])
            if lookup_db is None:  # con MySQL no hay requests que espaciar aquí
                await asyncio.sleep(_PAUSA)

        await _flush_simples()
        await _flush_variaciones()

    _set(estado="completado",
         paso=(f"Stock actualizado en {resumen['stock']} · costo en {resumen['costo']} · "
               f"{resumen['sin_match']} sin match en Woo · {resumen['errores']} errores"))
    log.info("sync_woo terminado: %s", resumen)
    return {"ok": True, **resumen}
