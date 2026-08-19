"""
temu_censo.py — Censo del catálogo Temu → channel.listings, por API y desde
el backend. El gemelo de `tiktok_censo.py`, nacido el mismo día que el
escritor de Temu (18-ago, sondeo canario).

POR QUÉ EXISTE. Las 352 filas de Temu en channel.listings salieron de UNA
corrida manual de `cargar_temu.py` (14-ago) y nada las refrescaba: cada alta
nueva era invisible hasta el siguiente re-run a mano (con `_reflejar` en
`publicar_temu` desde v0.207 las altas propias sí se ven; lo que sigue ciego
sin censo es todo lo demás — cambios de estado, stock movido por ventas de
Temu, productos tocados desde el Seller Center).

Corre como job del scheduler tras `TEMU_CENSO_ENABLED` (nace apagado, regla 3).
No borra filas: un goods que desaparece conserva su último estado visto (mismo
criterio que los censos de ML/Amazon/TikTok).

El `status` se guarda CRUDO ("4/7", "2/8"…), igual que `cargar_temu`: la
decodificación fina no existe y fingirla sería atar decisiones a un invento.
Con Temu DROP-only eso no bloquea el fan-out (política del 18-ago: a lo
publicado se le escribe; Incompleto/Borrador/desconocido se omiten).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("omnicanal.temu_censo")

CANAL = "temu"
CUENTA = "TEMU"


def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


async def censar() -> dict[str, Any]:
    """Una pasada completa: todas las cubetas de Temu → upsert channel.listings."""
    from services import supabase_db as sdb
    from services import temu

    if not temu.disponible():
        return {"ok": False, "motivo": "Temu no configurado (faltan TEMU_*)"}

    productos = await temu.listar_productos()
    filas_por_sku: dict[str, tuple] = {}
    ilegibles = duplicados = 0
    for g in productos:
        sku = str(g.get("outGoodsSn") or "").strip()
        gid = str(g.get("goodsId") or "")
        if not (sku and gid):
            ilegibles += 1
            continue
        status = f"{g.get('status4VO')}/{g.get('subStatus4VO')}"
        if sku in filas_por_sku:
            duplicados += 1
            continue     # listar_productos ya dedupe por goodsId; primer sku gana
        filas_por_sku[sku] = (sku, gid, status, _num(g.get("price")),
                              _int(g.get("quantity")))
    filas = list(filas_por_sku.values())
    if productos and not filas:
        log.warning("censo temu: %d productos y CERO filas legibles; llaves del "
                    "primero: %s", len(productos), sorted((productos[0] or {}).keys()))
        return {"ok": False, "motivo": "respuesta ilegible", "crudos": len(productos)}

    def _upsert() -> int:
        from psycopg2.extras import execute_values
        cuenta = sdb.fetch_one(
            "select id from core.accounts where channel_id=%s and legacy_code=%s",
            (CANAL, CUENTA))
        if not cuenta:
            raise RuntimeError("core.accounts no tiene la cuenta TEMU de temu")
        cuenta_id = cuenta["id"]
        with sdb.get_cursor() as cur:
            cur.execute("select set_config('app.via', 'temu_censo', true)")
            for i in range(0, len(filas), 300):
                lote = filas[i:i + 300]
                execute_values(cur, """
                    insert into channel.listings
                        (sku, account_id, canal, listing_id, status, price,
                         stock_own, is_fulfillment, currency, store_name,
                         updated_at)
                    values %s
                    on conflict (sku, account_id, canal) do update set
                        listing_id=excluded.listing_id,
                        status=excluded.status,
                        price=coalesce(excluded.price, channel.listings.price),
                        stock_own=coalesce(excluded.stock_own, channel.listings.stock_own),
                        updated_at=now()""",
                    [(sku, cuenta_id, CANAL, gid, status, precio, stock,
                      False, "MXN", "Temu")
                     for sku, gid, status, precio, stock in lote],
                    page_size=300,
                    # 10 valores + now() = las 11 columnas. La lección de
                    # tiktok_censo v0.207.1: sin template, Postgres cuenta de
                    # menos y truena.
                    template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())")
        return len(filas)

    escritas = await asyncio.to_thread(_upsert)
    salida = {"ok": True, "productos": len(productos), "escritas": escritas,
              "ilegibles": ilegibles, "duplicados": duplicados}
    log.info("censo temu: %s", salida)
    return salida
