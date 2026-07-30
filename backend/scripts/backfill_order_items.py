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
  python backend/scripts/backfill_order_items.py --ejecutar --solo-faltantes

--solo-faltantes (30-jul): en vez de barrer TODO Woo página por página, le
pregunta a kubera qué pedidos no tienen líneas y se los pide a Woo por id
(`include`). Es el modo para tapar el hueco que deja tener `pedidos_ml_items`
apagado en KUBERA_MIRROR_TABLAS: 1,260 pedidos = 13 peticiones en vez de 58
páginas. Sirve igual cada vez que el flag se apague y se vuelva a encender.
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


def _woo(params: dict, reintentos: int = 5) -> list[dict]:
    """GET a Woo con cache-bust (regla 5) y reintentos por el 403 del WAF."""
    qs = urllib.parse.urlencode({
        "consumer_key": CK, "consumer_secret": CS,
        "status": "any", "_fields": "id,status,line_items,meta_data",
        "_cb": str(time.time()), **params,
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


def woo_pagina(pagina: int) -> list[dict]:
    """Una página de pedidos Woo (asc por id)."""
    return _woo({"per_page": POR_PAGINA, "page": pagina,
                 "orderby": "id", "order": "asc"})


def woo_por_ids(ids: list[int]) -> list[dict]:
    """Los pedidos con esos ids exactos. Woo topa `include` al per_page."""
    return _woo({"per_page": len(ids), "include": ",".join(str(i) for i in ids)})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ejecutar", action="store_true")
    ap.add_argument("--desde-pagina", type=int, default=1)
    ap.add_argument("--paginas", type=int, default=999,
                    help="tope de páginas en esta corrida (para tandas)")
    ap.add_argument("--solo-faltantes", action="store_true",
                    help="solo los pedidos de channel.orders SIN líneas")
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
    t0 = time.time()

    def procesar(pedidos: list[dict]) -> None:
        """Vuelca las líneas de estos pedidos Woo. Muta los contadores."""
        nonlocal vistos, lineas_ins, sin_mapa
        for p in pedidos:
            destino = mapa.get(int(p["id"]))
            if not destino:
                sin_mapa += 1        # pedido Woo ajeno a marketplaces (web/manual)
                continue
            canal, cuenta, ext_id, es_full, comision, _n_skus = destino
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

    if args.solo_faltantes:
        # Solo los pedidos que YA están en channel.orders pero no tienen líneas.
        cur.execute("""select o.wc_order_id
                         from channel.orders o
                         left join channel.order_items i
                           using (canal, cuenta, external_order_id)
                        where i.external_order_id is null
                          and o.wc_order_id is not null
                        order by o.wc_order_id""")
        faltantes = [int(r[0]) for r in cur.fetchall()]
        print(f"pedidos sin líneas: {len(faltantes)}", flush=True)
        for k in range(0, len(faltantes), POR_PAGINA):
            lote = faltantes[k:k + POR_PAGINA]
            procesar(woo_por_ids(lote))
            if args.ejecutar:
                pg.commit()
            print(f"  lote {k // POR_PAGINA + 1}: acumulado {vistos} pedidos / "
                  f"{lineas_ins} líneas [{time.time()-t0:.0f}s]", flush=True)
            time.sleep(PAUSA_WOO)
    else:
        pagina = args.desde_pagina
        while pagina < args.desde_pagina + args.paginas:
            pedidos = woo_pagina(pagina)
            if not pedidos:
                break
            procesar(pedidos)
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
            values ('order-items-backfill', %s, %s::jsonb, 'ok')""",
            ("Backfill líneas faltantes desde Woo" if args.solo_faltantes
             else "Backfill líneas históricas desde Woo",
             json.dumps({"pedidos": vistos, "lineas": lineas_ins,
                         "item_id_enriquecidos": enriquecidas,
                         "woo_ajenos": sin_mapa,
                         "modo": "solo-faltantes" if args.solo_faltantes else "barrido"}),))
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
