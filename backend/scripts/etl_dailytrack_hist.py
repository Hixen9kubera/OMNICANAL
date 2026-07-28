"""
etl_dailytrack_hist.py — ETL one-shot: dailytrackMeli (xaxbkijc) → BD kubera.

Absorción F1 (GO Eduardo 2026-07-28). Copia la ÚNICA historia de ventas/stock
previa al webhook (medido 1–15 jul: daily_sales $3.19M vs channel.orders
$0.24M) ANTES de dar de baja el proyecto origen:

  daily_sales  → analytics.sales_daily_hist   (1:1, sin columnas muertas)
  daily_stock  → analytics.stock_hist         (COMPRIMIDA run-length: solo se
                 escribe una fila cuando cambia la firma stock_full/stock_odoo/
                 price/status/logistic_type; el 91.1% de las 365,542 filas era
                 idéntica al día previo. Vigencia [valid_from, valid_to);
                 valid_to NULL = vigente al corte 2026-07-15.)

daily_visits queda FUERA (decisión 2026-07-28) — va solo en el dump de respaldo.

La instancia origen está AGONIZANDO (503/57014/53100): se lee POR FECHA, en
páginas de 1000, con reintentos exponenciales y pausa entre peticiones. La
compresión ocurre EN VUELO — las 365k filas nunca pisan la BD kubera.

Idempotente: sales upsert DO NOTHING; stock upsert por (cuenta,item_id,
valid_from) actualizando valid_to — re-correrlo converge al mismo resultado.
Al final (solo --ejecutar) deja acta en migration.reconciliation_runs.

Uso:
  python backend/scripts/etl_dailytrack_hist.py                 # DRY-RUN (no escribe)
  python backend/scripts/etl_dailytrack_hist.py --ejecutar
  python backend/scripts/etl_dailytrack_hist.py --ejecutar --solo sales
  python backend/scripts/etl_dailytrack_hist.py --ejecutar --solo stock
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

ROOT = Path(__file__).resolve().parent.parent.parent
PAUSA = 0.15          # entre peticiones al origen (gentileza: ya se cae solo)
LOTE_PG = 500
CORTE_SERIE = date(2026, 7, 15)   # último día vivo de las series origen


def cargar_env() -> dict[str, str]:
    vals: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            vals[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return vals


ENV = cargar_env()
# Origen = proyecto de analítica (en .env local SUPABASE_URL apunta a xaxbkijc;
# si algún día cambia la semántica, definir ANALYTICS_* y ganan ellas).
ORIGEN_URL = (ENV.get("ANALYTICS_SUPABASE_URL") or ENV["SUPABASE_URL"]).rstrip("/")
ORIGEN_KEY = ENV.get("ANALYTICS_SUPABASE_SERVICE_ROLE_KEY") or ENV["SUPABASE_SERVICE_ROLE_KEY"]
DESTINO_DSN = ENV["SUPABASE_DB_URL"]              # BD kubera (tukwcvsi…)

if "xaxbkijc" not in ORIGEN_URL:
    sys.exit(f"ABORT: el origen no es dailytrackMeli (xaxbkijc): {ORIGEN_URL}")
if "tukwcvsi" not in DESTINO_DSN:
    sys.exit("ABORT: el destino no es la BD kubera (tukwcvsi).")


def rest(path: str, reintentos: int = 6) -> list[dict]:
    """GET al PostgREST origen con backoff (la instancia tira 503 seguido)."""
    for i in range(reintentos):
        try:
            r = urllib.request.Request(f"{ORIGEN_URL}/rest/v1/{path}")
            r.add_header("apikey", ORIGEN_KEY)
            r.add_header("Authorization", f"Bearer {ORIGEN_KEY}")
            with urllib.request.urlopen(r, timeout=90) as x:
                return json.loads(x.read().decode())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            code = getattr(e, "code", None)
            if i < reintentos - 1 and code in (None, 500, 502, 503, 504, 520, 522):
                time.sleep(2 ** i)
                continue
            raise
    return []


def paginar(base: str) -> list[dict]:
    filas, off = [], 0
    while True:
        lote = rest(f"{base}&limit=1000&offset={off}")
        filas += lote
        time.sleep(PAUSA)
        if len(lote) < 1000:
            return filas
        off += 1000


def fechas_serie(tabla: str) -> list[str]:
    d0 = rest(f"{tabla}?select=date&order=date.asc&limit=1")[0]["date"]
    d1 = rest(f"{tabla}?select=date&order=date.desc&limit=1")[0]["date"]
    ini, fin = date.fromisoformat(d0), date.fromisoformat(d1)
    return [(ini + timedelta(days=n)).isoformat()
            for n in range((fin - ini).days + 1)]


def etl_sales(pg, ejecutar: bool) -> dict:
    fechas = fechas_serie("daily_sales")
    print(f"daily_sales: {fechas[0]} → {fechas[-1]} ({len(fechas)} días)", flush=True)
    leidas = escritas = 0
    buffer: list[tuple] = []

    def volcar():
        nonlocal escritas
        if not buffer or not ejecutar:
            buffer.clear()
            return
        with pg.cursor() as cur:
            execute_values(cur, """
                insert into analytics.sales_daily_hist
                  (date, cuenta, item_id, sku, is_full, units_sold, revenue, sale_fee)
                values %s on conflict (date, cuenta, item_id) do nothing""", buffer)
        # OJO: rowcount de execute_values solo reporta la última página — el
        # conteo REAL sale del SELECT final contra la tabla destino.
        escritas += len(buffer)
        pg.commit()
        buffer.clear()

    for f in fechas:
        filas = paginar(f"daily_sales?select=date,cuenta,item_id,sku,is_full,"
                        f"units_sold,revenue,sale_fee&date=eq.{f}&order=item_id.asc")
        leidas += len(filas)
        for r in filas:
            buffer.append((r["date"], r["cuenta"], r["item_id"],
                           (r.get("sku") or "").strip() or None, r.get("is_full"),
                           r.get("units_sold"), r.get("revenue"), r.get("sale_fee")))
        if len(buffer) >= LOTE_PG:
            volcar()
    volcar()
    print(f"daily_sales: leídas {leidas}, insertadas {escritas} "
          f"({'EJECUTAR' if ejecutar else 'dry-run'})", flush=True)
    return {"leidas": leidas, "insertadas": escritas}


def etl_stock(pg, ejecutar: bool) -> dict:
    fechas = fechas_serie("daily_stock")
    print(f"daily_stock: {fechas[0]} → {fechas[-1]} ({len(fechas)} días)", flush=True)
    # abiertos[(cuenta,item_id)] = [valid_from, firma, sku]
    abiertos: dict[tuple, list] = {}
    leidas = cambios = 0
    nuevos: list[tuple] = []       # filas a abrir
    cierres: list[tuple] = []      # (valid_to, cuenta, item_id, valid_from)

    def volcar():
        if not ejecutar:
            nuevos.clear(); cierres.clear()
            return
        with pg.cursor() as cur:
            if nuevos:
                execute_values(cur, """
                    insert into analytics.stock_hist
                      (cuenta, item_id, sku, valid_from, stock_full, stock_odoo,
                       price, status, logistic_type)
                    values %s
                    on conflict (cuenta, item_id, valid_from) do nothing""", nuevos)
            if cierres:
                cur.executemany("""
                    update analytics.stock_hist set valid_to = %s
                    where cuenta = %s and item_id = %s and valid_from = %s""", cierres)
        pg.commit()
        nuevos.clear(); cierres.clear()

    for f in fechas:
        filas = paginar(f"daily_stock?select=date,cuenta,item_id,sku,stock_full,"
                        f"stock_odoo,price,status,logistic_type&date=eq.{f}"
                        f"&order=item_id.asc")
        leidas += len(filas)
        vistos_dia: dict[str, set] = {}
        for r in filas:
            k = (r["cuenta"], r["item_id"])
            vistos_dia.setdefault(k[0], set()).add(k[1])
            firma = (r.get("stock_full"), r.get("stock_odoo"), r.get("price"),
                     r.get("status"), r.get("logistic_type"))
            ab = abiertos.get(k)
            if ab is not None and ab[1] == firma:
                continue                        # sin cambio: la fila NO viaja
            if ab is not None:
                cierres.append((f, k[0], k[1], ab[0]))
            abiertos[k] = [f, firma, (r.get("sku") or "").strip() or None]
            nuevos.append((k[0], k[1], abiertos[k][2], f, *firma))
            cambios += 1
        # CIERRE POR AUSENCIA: un item que dejó de aparecer en la serie de su
        # cuenta se cierra ese día (si no, queda "vigente" para siempre y la
        # reconstrucción devuelve de más — medido: +346 el 15-jul). OJO: solo
        # en días donde ESA cuenta sí trajo filas — un día vacío es el cron
        # caído (30-abr→4-may), no una baja masiva de items.
        for cta, vistos in vistos_dia.items():
            huerf = [k for k, ab in abiertos.items()
                     if k[0] == cta and k[1] not in vistos]
            for k in huerf:
                cierres.append((f, k[0], k[1], abiertos[k][0]))
                del abiertos[k]
        if len(nuevos) + len(cierres) >= LOTE_PG:
            volcar()
        print(f"  {f}: {len(filas)} filas, {cambios} cambios acumulados", flush=True)
    volcar()
    pct = (cambios / leidas * 100) if leidas else 0
    print(f"daily_stock: leídas {leidas}, archivadas {cambios} ({pct:.1f}%) "
          f"({'EJECUTAR' if ejecutar else 'dry-run'})", flush=True)
    return {"leidas": leidas, "archivadas": cambios, "items": len(abiertos)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ejecutar", action="store_true",
                    help="escribe en kubera (default: dry-run, solo cuenta)")
    ap.add_argument("--solo", choices=["sales", "stock"])
    args = ap.parse_args()

    pg = psycopg2.connect(DESTINO_DSN, connect_timeout=30)
    pg.autocommit = False
    t0 = time.time()
    resumen: dict = {"modo": "ejecutar" if args.ejecutar else "dry-run"}
    if args.solo in (None, "sales"):
        resumen["sales"] = etl_sales(pg, args.ejecutar)
    if args.solo in (None, "stock"):
        resumen["stock"] = etl_stock(pg, args.ejecutar)
    resumen["segundos"] = round(time.time() - t0, 1)

    if args.ejecutar:
        with pg.cursor() as cur:
            cur.execute("""
                insert into migration.reconciliation_runs
                  (dominio, descripcion, conteos, resultado)
                values ('analytics-hist',
                        'ETL one-shot dailytrack (sales+stock comprimido)',
                        %s::jsonb, 'ok')""", (json.dumps(resumen),))
        pg.commit()
    pg.close()
    print(json.dumps(resumen, ensure_ascii=False))


if __name__ == "__main__":
    main()
