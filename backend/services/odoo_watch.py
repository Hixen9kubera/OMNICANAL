"""
odoo_watch.py — Vigilante de cambios de stock en Odoo.

RESPONDE A: "si alguien actualiza el stock de un SKU en Odoo, ¿cómo lo cachamos?"

Odoo no manda webhooks (configurar acciones de servidor allá es frágil y nadie
las mantiene). En su lugar, este vigilante usa la MISMA técnica que ya usamos
para detectar entradas de FULL: foto anterior vs foto nueva.

Cada N minutos (scheduler):
  1. Lee el catálogo de Odoo (qty_available por SKU — una sola llamada RPC).
  2. Compara contra la última foto guardada (columna `productos.stock_odoo`).
  3. Para cada SKU que cambió:
       - actualiza la foto en `productos.stock_odoo` (el panel lo ve fresco),
       - deja un evento en `webhook_eventos` (canal='odoo') → sale en la
         CAMPANA del frontend: "ODOO-STOCK TEC-0123: 12 → 8",
       - si ODOO_WATCH_AUTO_PUSH=true, empuja el stock nuevo a WooCommerce
         (solo los SKUs que cambiaron — no un barrido completo).

`auto_push` nace APAGADO: se enciende después de la carga inicial Odoo→Woo
(services/sync_woo.py), cuando Woo ya arranca alineado. Durante la transición
Odoo sigue siendo el maestro; tras el corte, este vigilante se apaga junto
con Odoo.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from config import settings
from services import db, odoo

log = logging.getLogger("omnicanal.odoo_watch")

_MAX_EVENTOS_CAMPANA = 40  # si cambian más SKUs, se resume en un solo evento
_ultimo: dict[str, Any] = {"estado": "sin_ejecutar"}


def estado() -> dict[str, Any]:
    return _ultimo


def _foto_anterior() -> dict[str, int]:
    filas = db.fetch_all(
        "SELECT sku, stock_odoo FROM productos WHERE sku IS NOT NULL AND sku<>''"
    )
    return {f["sku"]: int(f["stock_odoo"]) for f in filas
            if f.get("stock_odoo") is not None}


def _guardar_foto(cambios: list[tuple[str, int]]) -> None:
    with db.get_cursor() as cur:
        cur.executemany(
            "UPDATE productos SET stock_odoo=%s, updated_at=NOW() WHERE sku=%s",
            [(qty, sku) for sku, qty in cambios])


def _avisar_campana(cambios: list[tuple[str, int, int | None]]) -> None:
    """Eventos para la campana (tabla webhook_eventos, canal='odoo')."""
    ahora = datetime.now(timezone.utc)
    filas = []
    # Foto vieja (primer arranque o mucho tiempo apagado): cientos de "cambios"
    # que en realidad son el desfase acumulado. Un solo aviso-resumen, sin spam.
    if len(cambios) > 200:
        filas.append(("odoo", "stock_cambio", "baseline", None, None, None, 1,
                      f"Odoo: foto realineada ({len(cambios)} SKUs con desfase)",
                      ahora))
    else:
        for sku, nuevo, viejo in cambios[:_MAX_EVENTOS_CAMPANA]:
            flecha = f"{viejo if viejo is not None else '?'} → {nuevo}"
            filas.append(("odoo", "stock_cambio", sku, None, None, sku, 1,
                          f"Odoo: stock {flecha}", ahora))
        if len(cambios) > _MAX_EVENTOS_CAMPANA:
            filas.append(("odoo", "stock_cambio", "varios", None, None, None, 1,
                          f"Odoo: {len(cambios)} SKUs cambiaron de stock", ahora))
    try:
        with db.get_cursor() as cur:
            cur.executemany(
                """INSERT INTO webhook_eventos
                   (canal, topic, resource, user_id, cuenta, sku, procesado,
                    resultado, recibido)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", filas)
    except Exception as exc:  # noqa: BLE001
        log.warning("odoo_watch: no se pudo avisar en campana: %s", exc)


async def _empujar_a_woo(cambios: list[tuple[str, int]]) -> int:
    """Escribe el stock nuevo SOLO de los SKUs que cambiaron (batch chico)."""
    from services import woocommerce, wp_db
    if not wp_db.disponible():
        log.warning("odoo_watch: sin lookup MySQL de WP; no empujo a Woo.")
        return 0
    lookup = await asyncio.to_thread(wp_db.productos_por_sku,
                                     [s for s, _ in cambios])
    simples, variaciones = [], {}
    for sku, qty in cambios:
        d = lookup.get(sku)
        if not d:
            continue
        upd = {"id": d["wc_id"], "manage_stock": True,
               "stock_quantity": max(0, qty)}
        if d.get("tipo") == "variation" and d.get("parent_id"):
            variaciones.setdefault(d["parent_id"], []).append(upd)
        else:
            simples.append(upd)
    hechos = 0
    async with woocommerce._client() as cli:
        for i in range(0, len(simples), 50):
            r = await cli.post("/products/batch",
                               json={"update": simples[i:i + 50]}, timeout=300.0)
            if r.status_code in (200, 201):
                hechos += len(simples[i:i + 50])
            await asyncio.sleep(0.8)
        for parent, lote in variaciones.items():
            r = await cli.post(f"/products/{parent}/variations/batch",
                               json={"update": lote}, timeout=300.0)
            if r.status_code in (200, 201):
                hechos += len(lote)
            await asyncio.sleep(0.8)
    return hechos


async def revisar() -> dict[str, Any]:
    """Una pasada del vigilante. La corre el scheduler; también sirve a mano."""
    t0 = time.time()
    catalogo = await asyncio.to_thread(odoo.listar_catalogo)
    if not catalogo:
        _ultimo.update(estado="odoo_sin_respuesta", ts=time.time())
        return _ultimo
    actual = {p["sku"]: max(0, int(p.get("stock") or 0))
              for p in catalogo if p.get("sku")}
    anterior = await asyncio.to_thread(_foto_anterior)

    cambios = [(sku, qty, anterior.get(sku)) for sku, qty in actual.items()
               if sku in anterior and anterior[sku] != qty]
    if cambios:
        await asyncio.to_thread(_guardar_foto, [(s, q) for s, q, _ in cambios])
        await asyncio.to_thread(_avisar_campana, cambios)
    empujados = 0
    if cambios and settings.odoo_watch_auto_push:
        empujados = await _empujar_a_woo([(s, q) for s, q, _ in cambios])

    _ultimo.update(
        estado="ok", ts=time.time(), segundos=round(time.time() - t0, 1),
        skus_odoo=len(actual), cambios=len(cambios), empujados_woo=empujados,
        muestra=[f"{s}: {v} → {q}" for s, q, v in cambios[:8]])
    if cambios:
        log.info("odoo_watch: %d cambios de stock detectados (%s empujados a Woo)",
                 len(cambios), empujados if settings.odoo_watch_auto_push else "0/apagado")
    return _ultimo
