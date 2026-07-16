"""
etl_channel_listings.py — Fase 2 del dominio CHANNEL: la fusión 3→1.

Puebla `channel.listings` en Supabase DEV fusionando las 3 tablas de estado
por canal de MySQL:
  - ml_progress      (estado de publicación ML por cuenta:sku)
  - amazon_progress  (estado de listing Amazon por sku — conserva product_type)
  - canal_inventario (cache de stock/precio por sku×canal×cuenta)

Fila destino = (sku, account_id, canal). Reglas heredadas del ETL de core:
producción SOLO LECTURA, full-refresh, candado anti-prod en el destino,
inválidos/desconocidos a ops.migration_issues, acta en reconciliation_runs.

Uso:  backend/.venv/Scripts/python.exe backend/scripts/etl_channel_listings.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
FASE = "F2-etl-channel"
BATCH = 1000


def cargar_env(nombre: str) -> dict[str, str]:
    vals: dict[str, str] = {}
    p = ROOT / nombre
    if not p.exists():
        return vals
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            vals.setdefault(k.strip(), v.split("#")[0].strip().strip('"').strip("'"))
    return vals


PROD = cargar_env(".env")
DEV = cargar_env("env.staging")


def candado_destino() -> None:
    m = re.search(r"postgres\.([a-z0-9]+):", DEV["SUPABASE_DB_URL"])
    ref = m.group(1) if m else ""
    if not ref:
        sys.exit("ABORT: sin ref en el SUPABASE_DB_URL destino.")
    if DEV.get("SUPABASE_PROD_REF") and ref == DEV["SUPABASE_PROD_REF"]:
        sys.exit("ABORT: el destino es PRODUCCIÓN. Este ETL solo escribe en DEV.")


def sku_valido(raw) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or len(s) > 100 or re.search(r"\s", s):
        return None
    return s


def main() -> None:
    candado_destino()

    my = pymysql.connect(
        host=PROD["DB_HOST"], port=int(PROD.get("DB_PORT", 3306)), user=PROD["DB_USER"],
        password=PROD["DB_PASSWORD"], database=PROD["DB_NAME"], charset="utf8mb4",
        connect_timeout=15, read_timeout=120, cursorclass=pymysql.cursors.DictCursor,
    )
    cur = my.cursor()
    cur.execute("SELECT * FROM ml_progress")
    t_mlp = cur.fetchall()
    cur.execute("SELECT * FROM amazon_progress")
    t_amp = cur.fetchall()
    cur.execute("SELECT * FROM canal_inventario")
    t_ci = cur.fetchall()
    cur.close()
    my.close()
    print(f"MySQL leído: ml_progress={len(t_mlp)} amazon_progress={len(t_amp)} "
          f"canal_inventario={len(t_ci)}")

    issues: list[tuple] = []
    filas: dict[tuple, dict] = {}   # (sku_lower, cuenta_legacy, canal) -> fila

    def fila(sku_raw, canal: str, cuenta: str, origen: str) -> dict | None:
        sku = sku_valido(sku_raw)
        if sku is None:
            issues.append((origen, str(sku_raw)[:100] if sku_raw else None,
                           "sku invalido", json.dumps({"sku_original": str(sku_raw)})))
            return None
        k = (sku.lower(), cuenta, canal)
        if k not in filas:
            filas[k] = {"sku": sku, "canal": canal, "cuenta": cuenta,
                        "listing_id": None, "url": None, "status": None,
                        "situacion": None, "price": None, "price_base": None,
                        "stock_own": None, "stock_full": None,
                        "is_fulfillment": False, "product_type": None}
        return filas[k]

    # 1) ml_progress: estado de publicación por cuenta
    for r in t_mlp:
        f = fila(r["sku"], "mercado_libre", (r["cuenta"] or "").strip() or "BEKURA", "ml_progress")
        if f is None:
            continue
        f["listing_id"] = r.get("ml_item_id") or f["listing_id"]
        f["url"] = r.get("ml_url") or f["url"]
        f["status"] = ("published" if r.get("success")
                       else ("error" if (r.get("error") or r.get("gtin_error")) else "pending"))

    # 2) amazon_progress: estado del listing (cuenta única AMAZON)
    for r in t_amp:
        f = fila(r["sku"], "amazon", "AMAZON", "amazon_progress")
        if f is None:
            continue
        f["listing_id"] = r.get("asin") or f["listing_id"]
        f["product_type"] = r.get("product_type")
        f["status"] = (r.get("status") or ("published" if r.get("success") else None))

    # 3) canal_inventario: stock/precio (la cache del sync de 15 min)
    CUENTA_CI = {"mercado_libre": None, "amazon": "AMAZON", "general": "GENERAL"}
    for r in t_ci:
        canal = (r["canal"] or "").strip()
        if canal not in CUENTA_CI:
            issues.append(("canal_inventario", str(r["sku"])[:100], "canal desconocido",
                           json.dumps({"canal": canal})))
            continue
        cuenta = (r["cuenta"] or "").strip() or CUENTA_CI[canal] or "BEKURA"
        f = fila(r["sku"], canal, cuenta, "canal_inventario")
        if f is None:
            continue
        f["price"] = r.get("precio")
        f["stock_own"] = r.get("stock_real")
        # FULL de ML y FBA de Amazon son el mismo concepto: inventario del canal
        f["stock_full"] = r.get("stock_full") if canal == "mercado_libre" else r.get("stock_fba")
        f["is_fulfillment"] = bool(r.get("es_full"))
        f["situacion"] = r.get("situacion")
        f["listing_id"] = f["listing_id"] or r.get("item_id")

    print(f"FUSIÓN: {len(filas)} listings | issues: {len(issues)}")

    # ── carga a DEV ───────────────────────────────────────────────────────────
    pg = psycopg2.connect(DEV["SUPABASE_DB_URL"], connect_timeout=20)
    pg.autocommit = True
    pcur = pg.cursor()
    pcur.execute("set session characteristics as transaction read write; "
                 "set default_transaction_read_only = off;")
    pg.autocommit = False

    # cuentas legacy -> uuid
    pcur.execute("select legacy_code, id from core.accounts")
    cuentas = dict(pcur.fetchall())
    faltan = {f["cuenta"] for f in filas.values()} - set(cuentas)
    if faltan:
        sys.exit(f"ABORT: cuentas sin fila en core.accounts: {faltan} — sembrar primero.")

    # full-refresh del dominio + issues de la fase
    pcur.execute("truncate channel.listings")
    pcur.execute("delete from ops.migration_issues where fase = %s", (FASE,))

    # identidad primero: SKUs de canal que el maestro aún no conoce
    skus_dest = sorted({f["sku"] for f in filas.values()})
    psycopg2.extras.execute_values(pcur, """
        insert into core.products (sku, status, source)
        values %s on conflict (sku) do nothing
    """, [(s, "draft", "etl-channel") for s in skus_dest], page_size=BATCH)
    pcur.execute("select count(*) from core.products where source = 'etl-channel'")
    nuevos_maestro = pcur.fetchone()[0]
    if nuevos_maestro:
        issues.append(("union", None, "skus_solo_en_canal",
                       json.dumps({"agregados_al_maestro": nuevos_maestro})))

    psycopg2.extras.execute_values(pcur, """
        insert into channel.listings
          (sku, account_id, canal, listing_id, url, status, situacion,
           price, price_base, stock_own, stock_full, is_fulfillment, product_type)
        values %s
    """, [(f["sku"], cuentas[f["cuenta"]], f["canal"], f["listing_id"], f["url"],
           f["status"], f["situacion"], f["price"], f["price_base"], f["stock_own"],
           f["stock_full"], f["is_fulfillment"], f["product_type"])
          for f in filas.values()], page_size=BATCH)

    if issues:
        psycopg2.extras.execute_values(pcur, """
            insert into ops.migration_issues (fase, tabla_origen, sku, motivo, valor)
            values %s
        """, [(FASE, t, s, m, v) for (t, s, m, v) in issues], page_size=BATCH)
    pg.commit()

    # ── validación: conteos y sumas por canal ─────────────────────────────────
    pcur.execute("""select canal, count(*), coalesce(sum(stock_own),0),
                           coalesce(sum(stock_full),0), coalesce(sum(price),0)
                    from channel.listings group by canal order by canal""")
    dev_stats = {r[0]: r[1:] for r in pcur.fetchall()}

    my = pymysql.connect(
        host=PROD["DB_HOST"], port=int(PROD.get("DB_PORT", 3306)), user=PROD["DB_USER"],
        password=PROD["DB_PASSWORD"], database=PROD["DB_NAME"], charset="utf8mb4",
        connect_timeout=15, cursorclass=pymysql.cursors.DictCursor,
    )
    cur = my.cursor()
    cur.execute("""select canal, coalesce(sum(stock_real),0) so,
                          coalesce(sum(case when canal='amazon' then stock_fba
                                            else stock_full end),0) sf,
                          coalesce(sum(precio),0) p
                   from canal_inventario group by canal""")
    my_stats = {r["canal"]: (r["so"], r["sf"], r["p"]) for r in cur.fetchall()}
    cur.close()
    my.close()

    deltas = {}
    for canal, (so, sf, p) in my_stats.items():
        d = dev_stats.get(canal, (0, 0, 0, 0))
        deltas[canal] = {"stock_own": str(so - d[1]), "stock_full": str(sf - d[2]),
                         "price": str(p - d[3])}
    limpio = all(all(v == "0" for v in d.values()) for d in deltas.values())

    conteos = {"listings_total": len(filas), "por_canal": {k: v[0] for k, v in dev_stats.items()},
               "issues": len(issues), "skus_nuevos_al_maestro": nuevos_maestro}
    pcur.execute("""insert into migration.reconciliation_runs
                    (dominio, descripcion, conteos, checksums, resultado)
                    values ('channel', 'ETL fusion 3->1 a channel.listings', %s, %s, %s)""",
                 (json.dumps(conteos), json.dumps(deltas),
                  "ok" if limpio else "con_deltas"))
    pg.commit()
    pcur.close()
    pg.close()

    print("\n== RESULTADO ==")
    print("listings:", conteos["listings_total"], "| por canal:", conteos["por_canal"])
    print("SKUs que el maestro no conocía (agregados como draft/etl-channel):", nuevos_maestro)
    print("deltas de stock/precio vs canal_inventario:", deltas)
    print("issues:", len(issues), "| acta:", "ok" if limpio else "con_deltas")


if __name__ == "__main__":
    main()
