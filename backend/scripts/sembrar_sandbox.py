"""
sembrar_sandbox.py — Carga una MUESTRA real (de MySQL producción, solo lectura)
en el sandbox para poder probar lecturas/flags con datos que se parecen a los
de verdad. Es DESECHABLE: se borra recreando el sandbox.

Candado: se niega a correr contra la BD kubera o contra SUPABASE_PROD_REF.

Uso: backend/.venv/Scripts/python.exe backend/scripts/sembrar_sandbox.py [--n 300]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
socket.setdefaulttimeout(60)
REF_KUBERA_PROD = "tukwcvsi"


def _watchdog():
    def _m():
        print("WATCHDOG 10min — abort", flush=True); os._exit(2)
    t = threading.Timer(600, _m); t.daemon = True; t.start()


def env(nombre: str) -> dict[str, str]:
    d: dict[str, str] = dict(os.environ)
    p = ROOT / nombre
    if not p.exists():
        return d
    for l in p.read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    args = ap.parse_args()
    _watchdog()

    PROD, S = env(".env"), env("env.staging")
    url = S["SUPABASE_DB_URL"]
    m = re.search(r"postgres\.([a-z0-9]+):", url)
    ref = m.group(1) if m else ""
    if not ref or ref.startswith(REF_KUBERA_PROD) or ref == S.get("SUPABASE_PROD_REF", "").strip():
        sys.exit("ABORT: destino no es el sandbox.")
    print(f"Sembrando sandbox {ref[:8]}… con hasta {args.n} SKUs reales", flush=True)

    my = pymysql.connect(
        host=PROD["DB_HOST"], port=int(PROD.get("DB_PORT", 3306)), user=PROD["DB_USER"],
        password=PROD["DB_PASSWORD"], database=PROD["DB_NAME"], charset="utf8mb4",
        connect_timeout=15, read_timeout=120, cursorclass=pymysql.cursors.DictCursor)
    cur = my.cursor()
    cur.execute("""SELECT v.sku, p.nombre, v.contenedor, v.wc_id, v.wc_status, v.wc_type,
                          v.largo, v.alto, v.ancho, v.peso, v.costo_producto, v.costo_cbm,
                          v.costo_total, v.cajas, v.piezas_por_caja, v.created_at
                   FROM costos_validados v LEFT JOIN productos p ON p.sku = v.sku
                   WHERE v.sku NOT LIKE '%% %%' AND v.sku <> ''
                   ORDER BY v.created_at DESC LIMIT %s""", (args.n,))
    cv = cur.fetchall()
    skus = [r["sku"] for r in cv]
    cf = []
    if skus:
        marcas = ",".join(["%s"] * len(skus))
        cur.execute(f"""SELECT sku, costo_producto, costo_cbm, costo_unitario, ml_cat_id,
                               pct_comision, costo_comision, costo_fee_envio,
                               precio_sugerido, precio_base, peso_origen
                        FROM costos_finales WHERE sku IN ({marcas})""", tuple(skus))
        cf = cur.fetchall()
    cur.close(); my.close()

    pg = psycopg2.connect(url, connect_timeout=20)
    pg.autocommit = False
    pcur = pg.cursor()
    psycopg2.extras.execute_values(pcur, """
        insert into core.products (sku, name, wc_id, status, source)
        values %s on conflict (sku) do update set name = excluded.name
    """, [(r["sku"], r["nombre"], r["wc_id"], r["wc_status"] or "draft", "sandbox-seed")
          for r in cv], page_size=500)
    psycopg2.extras.execute_values(pcur, """
        insert into costing.costos_validados
          (sku, wc_id, wc_status, wc_type, contenedor, costo_producto, costo_cbm,
           largo, alto, ancho, peso, costo_total, cajas, piezas_por_caja, created_at)
        values %s on conflict (sku) do nothing
    """, [(r["sku"], r["wc_id"], r["wc_status"], r["wc_type"], r["contenedor"],
           r["costo_producto"], r["costo_cbm"], r["largo"], r["alto"], r["ancho"],
           r["peso"], r["costo_total"], r["cajas"], r["piezas_por_caja"], r["created_at"])
          for r in cv], page_size=500)
    psycopg2.extras.execute_values(pcur, """
        insert into costing.costos_finales
          (sku, canal, costo_producto, costo_cbm, costo_unitario, ml_cat_id, pct_comision,
           costo_comision, costo_fee_envio, precio_sugerido, precio_base, peso_origen)
        values %s on conflict (sku, canal) do nothing
    """, [(r["sku"], "mercado_libre", r["costo_producto"], r["costo_cbm"], r["costo_unitario"],
           r["ml_cat_id"], r["pct_comision"], r["costo_comision"], r["costo_fee_envio"],
           r["precio_sugerido"], r["precio_base"], r["peso_origen"]) for r in cf], page_size=500)
    pg.commit()
    pcur.execute("select (select count(*) from core.products), "
                 "(select count(*) from costing.costos_validados), "
                 "(select count(*) from costing.costos_finales)")
    n = pcur.fetchone()
    pg.close()
    print(json.dumps({"core.products": n[0], "costos_validados": n[1],
                      "costos_finales": n[2]}, indent=1))


if __name__ == "__main__":
    main()
