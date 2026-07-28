"""
backfill_order_items.py — Backfill one-shot: líneas de los pedidos HISTÓRICOS
de WooCommerce → channel.order_items (BD kubera).

Absorción F1 (GO Eduardo 2026-07-28). El seam vivo (`pedidos_ml_items`) llena
las líneas de cada venta NUEVA; esto rellena las ~5,600 ventas que ya existen.
Se lee de WOO (no de ML): los pedidos tienen las líneas completas con el
precio CONGELADO de la venta y no gastan tokens ni rate-limit de ML.

Limitaciones asumidas (documentadas en README v0.24.0):
  * Woo NO guarda el item_id del marketplace → se ENRIQUECE al final desde
    channel.listings (sku+cuenta → listing_id actual). Ventas de SKUs ya
    des-publicados quedan con item_id NULL (el seam vivo sí lo trae).
  * La comisión por LÍNEA solo se conoce en pedidos de UNA línea (se toma la
    del encabezado channel.orders); multi-línea queda NULL — el total sigue
    en el encabezado, nada se pierde.
  * Líneas sin producto mapeado entraron a Woo como "[SKU] título" → se
    parsea el SKU del prefijo.

Idempotente y CONGELADO: mismo upsert del seam — en conflicto solo rellena
item_id/sku faltantes y comisión 0→valor; jamás re-toca importes. Re-correrlo
converge. Respeta el candado: solo corre contra la BD kubera (tukwcvsi).

Uso:
  python backend/scripts/backfill_order_items.py                # DRY-RUN
  python backend/scripts/backfill_order_items.py --ejecutar
  python backend/scripts/backfill_order_items.py --ejecutar --paginas 10 --desde-pagina 1
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent.parent
PAUSA_WOO = 0.6          # LiteSpeed/WAF de Hostinger: sin ráfagas
POR_PAGINA = 100
RE_SIN_MAPEAR = re.compile(r"^\[([A-Za-z0-9._\-]+)\]\s*")


def cargar_env() -> dict[str, str]:
    vals: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            vals[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return vals


ENV = cargar_env()
if "tukwcvsi" not in ENV["SUPABASE_DB_URL"]:
    sys.exit("ABORT: el destino no es la BD kubera (tukwcvsi).")
WC = ENV["WC_URL"].rstrip("/") + "/wp-json/wc/v3/orders"
CK, CS = ENV["WC_CONSUMER_KEY"], ENV["WC_CONSUMER_SECRET"]


def woo_pagina(pagina: int, reintentos: int = 5) -> list[dict]:
    """Una página de pedidos Woo (asc por id, con cache-bust — regla 5)."""
    qs = urllib.parse.urlencode({
        "consumer_key": CK, "consumer_secret": CS,
        "per_page": POR_PAGINA, "page": pagina, "orderby": "id", "order": "asc",
        "status": "any", "_fields": "id,status,line_items,meta_data",
        "_cb": str(time.time()),
    })
    for i in range(reintentos):
        try:
            with urllib.request.urlopen(f"{WC}?{qs}", timeout=90) as x:
                return json.loads(x.read().decode())
        except Exception:  # noqa: BLE001 — 403 intermitente del WAF: reintentar
            if i == reintentos - 1:
                raise
            time.sleep(3 * (i + 1))
    return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ejecutar", action="store_true")
    ap.add_argument("--desde-pagina", type=int, default=1)
    ap.add_argument("--paginas", type=int, default=999,
                    help="tope de páginas en esta corrida (para tandas)")
    args = ap.parse_args()

    pg = psycopg2.connect(ENV["SUPABASE_DB_URL"], connect_timeout=30)
    pg.autocommit = False
    cur = pg.cursor()

    # wc_order_id → (canal, cuenta, external_order_id, es_full, comision, n_skus)
    cur.execute("""select wc_order_id, canal, cuenta, external_order_id,
                          es_fulfillment, comision, cardinality(coalesce(skus,'{}'))
                   from channel.orders where wc_order_id is not null""")
    mapa = {int(r[0]): r[1:] for r in cur.fetchall()}
    print(f"pedidos en channel.orders con wc_order_id: {len(mapa)}", flush=True)

    vistos = lineas_ins = sin_mapa = 0
    pagina, t0 = args.desde_pagina, time.time()
    while pagina < args.desde_pagina + args.paginas:
        pedidos = woo_pagina(pagina)
        if not pedidos:
            break
        for p in pedidos:
            destino = mapa.get(int(p["id"]))
            if not destino:
                sin_mapa += 1        # pedido Woo ajeno a marketplaces (web/manual)
                continue
            canal, cuenta, ext_id, es_full, comision, n_skus = destino
            vistos += 1
            items = p.get("line_items") or []
            for n, it in enumerate(items, start=1):
                sku = (it.get("sku") or "").strip()
                if not sku:
                    m = RE_SIN_MAPEAR.match(it.get("name") or "")
                    sku = m.group(1) if m else ""
                qty = int(it.get("quantity") or 1)
                total = float(it.get("total") or 0)
                com_linea = (float(comision) if comision is not None
                             and len(items) == 1 else None)
                if not args.ejecutar:
                    lineas_ins += 1
                    continue
                cur.execute(
                    """insert into channel.order_items
                         (canal, cuenta, external_order_id, linea, item_id, sku,
                          titulo, cantidad, precio_unitario, comision, es_fulfillment)
                       values (%s,%s,%s,%s,null,%s,%s,%s,%s,%s,%s)
                       on conflict (canal, cuenta, external_order_id, linea) do update set
                         sku      = coalesce(channel.order_items.sku, excluded.sku),
                         comision = case when coalesce(channel.order_items.comision,0)=0
                                         then excluded.comision
                                         else channel.order_items.comision end""",
                    (canal, cuenta, ext_id, n, sku or None,
                     (it.get("name") or "")[:200] or None, qty,
                     round(total / qty, 2) if qty else total, com_linea,
                     bool(es_full)))
                lineas_ins += 1
        if args.ejecutar:
            pg.commit()
        print(f"  página {pagina}: acumulado {vistos} pedidos / {lineas_ins} líneas "
              f"/ {sin_mapa} ajenos [{time.time()-t0:.0f}s]", flush=True)
        if len(pedidos) < POR_PAGINA:
            break
        pagina += 1
        time.sleep(PAUSA_WOO)

    enriquecidas = 0
    if args.ejecutar:
        # Enriquecer item_id desde channel.listings (Woo no lo guarda). La
        # cuenta de order_items ES la legacy_code de core.accounts.
        cur.execute("""
            update channel.order_items i
               set item_id = l.listing_id
              from channel.listings l
              join core.accounts a on a.id = l.account_id
             where i.item_id is null and i.sku is not null
               and l.sku = i.sku and l.canal = i.canal
               and a.legacy_code = i.cuenta
               and l.listing_id is not null""")
        enriquecidas = cur.rowcount
        cur.execute("""
            insert into migration.reconciliation_runs (dominio, descripcion, conteos, resultado)
            values ('order-items-backfill', 'Backfill líneas históricas desde Woo',
                    %s::jsonb, 'ok')""",
            (json.dumps({"pedidos": vistos, "lineas": lineas_ins,
                         "item_id_enriquecidos": enriquecidas,
                         "woo_ajenos": sin_mapa}),))
        pg.commit()
    pg.close()
    print(json.dumps({"modo": "ejecutar" if args.ejecutar else "dry-run",
                      "pedidos": vistos, "lineas": lineas_ins,
                      "item_id_enriquecidos": enriquecidas,
                      "woo_ajenos": sin_mapa,
                      "segundos": round(time.time() - t0, 1)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
