"""
tiktok_censo.py — Censo del catálogo TikTok → channel.listings, por API y
desde el backend.

POR QUÉ EXISTE (auditoría 18-ago). Las 902 filas de TikTok en channel.listings
nacieron de un censo MANUAL de CSVs (cargar_tiktok.py, 13-ago) y desde entonces
solo `publicar_tiktok._reflejar` las toca — una por una, al publicar desde el
panel. Dos consecuencias medidas:

  1. `tk_activar.py` corre desde el escritorio (~300 activaciones/día) y NO
     refleja: había 599 DRAFT en el espejo (497 con stock) que pueden estar YA
     a la venta en TikTok. El fan-out las omite como borrador → sobreventa en
     potencia.
  2. El fan-out decide "sin_cambio" comparando contra el stock del censo viejo
     — cada veredicto era una apuesta a que nadie vendió desde el 13-ago.

Este censo pregunta a la API (`/product/202309/products/search`, paginado por
`page_token`) y upserta status + auditoría + stock + precio. Corre como job del
scheduler tras `TIKTOK_CENSO_ENABLED` (nace apagado, regla 3) y a mano por
`POST /api/tiktok/censo`.

Lo que NO hace: borrar filas. Un producto que desaparece de TikTok conserva su
fila con el último estado visto (mismo criterio que el sync de ML/Amazon: cerrar
es tarea aparte y con confirmación una por una).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("omnicanal.tiktok_censo")

CANAL = "tiktok"
CUENTA = "KUBERA"
# El almacén de VENTAS (el otro es el de devoluciones). Mismo id que usa el
# escritor del fan-out (fanout_stock._ALMACEN_VENTAS_TIKTOK).
_ALMACEN_VENTAS = "7647893424175580935"
_PAGE_SIZE = 100
_MAX_PAGINAS = 60          # cortacircuitos: 60×100 = 6,000 productos


def _parsear(p: dict[str, Any]) -> tuple | None:
    """Producto de la API → fila del upsert. None = ilegible (se cuenta)."""
    pid = str(p.get("id") or "")
    skus = p.get("skus") or []
    s0 = skus[0] if skus else {}
    seller_sku = str(s0.get("seller_sku") or "").strip()
    if not (pid and seller_sku):
        return None
    status = str(p.get("status") or "") or None
    audit = str(((p.get("audit") or {}).get("status")) or "") or None
    stock = None
    for inv in (s0.get("inventory") or []):
        if str(inv.get("warehouse_id") or "") == _ALMACEN_VENTAS:
            stock = inv.get("quantity")
            break
    if stock is None and (s0.get("inventory") or []):
        stock = (s0["inventory"][0] or {}).get("quantity")
    precio = None
    pr = s0.get("price") or {}
    for k in ("tax_exclusive_price", "sale_price", "original_price", "amount"):
        v = pr.get(k)
        if v not in (None, ""):
            try:
                precio = float(v)
                break
            except (TypeError, ValueError):
                pass
    try:
        stock = int(stock) if stock is not None else None
    except (TypeError, ValueError):
        stock = None
    return (seller_sku, pid, status, audit, precio, stock)


async def censar() -> dict[str, Any]:
    """Una pasada completa: pagina la API y upserta channel.listings."""
    from services import supabase_db as sdb
    from services import tiktok as tk

    token, ciph = tk.access_token(), tk.cipher()
    if not (token and ciph):
        return {"ok": False, "motivo": "TikTok sin token o sin shop_cipher"}

    productos: list[dict[str, Any]] = []
    page_token, paginas = None, 0
    while paginas < _MAX_PAGINAS:
        params: dict[str, Any] = {"shop_cipher": ciph, "page_size": _PAGE_SIZE}
        if page_token:
            params["page_token"] = page_token
        data = await tk.llamar("/product/202309/products/search", token,
                               params, {}, "POST")
        lote = data.get("products") or []
        productos.extend(lote)
        paginas += 1
        page_token = data.get("next_page_token") or ""
        if not page_token or not lote:
            break

    filas, ilegibles = [], 0
    for p in productos:
        f = _parsear(p)
        if f is None:
            ilegibles += 1
        else:
            filas.append(f)
    if productos and not filas:
        # Forma de respuesta inesperada: mejor un warning con las llaves reales
        # que un censo silenciosamente vacío.
        log.warning("censo tiktok: %d productos y CERO filas legibles; llaves "
                    "del primero: %s", len(productos),
                    sorted((productos[0] or {}).keys()))
        return {"ok": False, "motivo": "respuesta ilegible", "crudos": len(productos)}

    def _upsert() -> int:
        from psycopg2.extras import execute_values
        cuenta = sdb.fetch_one(
            "select id from core.accounts where channel_id=%s and legacy_code=%s",
            (CANAL, CUENTA))
        if not cuenta:
            raise RuntimeError("core.accounts no tiene la cuenta KUBERA de tiktok")
        cuenta_id = cuenta["id"]
        with sdb.get_cursor() as cur:
            cur.execute("select set_config('app.via', 'tiktok_censo', true)")
            for i in range(0, len(filas), 300):
                lote = filas[i:i + 300]
                execute_values(cur, """
                    insert into channel.listings
                        (sku, account_id, canal, listing_id, status, situacion,
                         price, stock_own, is_fulfillment, currency, store_name,
                         updated_at)
                    values %s
                    on conflict (sku, account_id, canal) do update set
                        listing_id=excluded.listing_id,
                        status=excluded.status,
                        situacion=coalesce(excluded.situacion, channel.listings.situacion),
                        price=coalesce(excluded.price, channel.listings.price),
                        stock_own=coalesce(excluded.stock_own, channel.listings.stock_own),
                        updated_at=now()""",
                    [(sku, cuenta_id, CANAL, pid, status, audit, precio, stock,
                      False, "MXN", CUENTA)
                     for sku, pid, status, audit, precio, stock in lote],
                    page_size=300,
                    # 11 valores + now() = las 12 columnas del INSERT. Sin el
                    # template, execute_values manda 11 expresiones y Postgres
                    # contesta "INSERT has more target columns than expressions"
                    # (pasó en la primera pasada real, 18-ago 18:40).
                    template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())")
        return len(filas)

    escritas = await asyncio.to_thread(_upsert)
    activate = sum(1 for f in filas if f[2] == "ACTIVATE")
    salida = {"ok": True, "productos": len(productos), "escritas": escritas,
              "activate": activate, "ilegibles": ilegibles, "paginas": paginas}
    log.info("censo tiktok: %s", salida)
    return salida
