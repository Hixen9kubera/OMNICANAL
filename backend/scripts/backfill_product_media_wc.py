"""
backfill_product_media_wc.py — Copia las imágenes de Competencia a
enrich.product_media con kind='wc' (paso 2 del PLAN_COMPETENCIA_v2).

Origen: propuestas.competencia_skus.imagen — 1,572 de 1,584 SKUs la traen.
SIN llamadas a WooCommerce: la URL ya está capturada en la BD, así que es un
insert…select dentro de la misma base. (El plan decía "desde WooCommerce";
medido el 2026-08-11, no hace falta — y evita el 403 intermitente del WAF de
Hostinger, pendiente conocido #1.)

Idempotente por construcción: enrich.product_media tiene el índice único
uq_product_media_sku_kind_url (sku, kind, source_url), creado en la migración
0002. El insert usa `on conflict do nothing`, así que correrlo de más no
duplica.

El namespace kind='wc' está libre: hoy product_media solo tiene 'amazon'.

Verificado antes de escribir:
  - ningún comparar_*.py audita product_media → no mueve ninguna acta
  - la tabla no tiene triggers
  - cero SKUs con imagen fuera de core.products → la FK no rechaza ninguno

Uso:
  backend/.venv/Scripts/python.exe backend/scripts/backfill_product_media_wc.py
  backend/.venv/Scripts/python.exe backend/scripts/backfill_product_media_wc.py --destino prod --real --acepto-destino tukwcvsi
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

ROOT = Path(__file__).resolve().parent.parent.parent
FASE = "F2-backfill-media-wc"
KIND = "wc"
TIMEOUT_MIN = 10

socket.setdefaulttimeout(60)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def _armar_watchdog() -> None:
    def _matar():
        print(f"WATCHDOG: {TIMEOUT_MIN} min agotados — aborto.", flush=True)
        os._exit(2)
    t = threading.Timer(TIMEOUT_MIN * 60, _matar)
    t.daemon = True
    t.start()


def cargar_env(nombre: str) -> dict[str, str]:
    vals: dict[str, str] = dict(os.environ)
    p = ROOT / nombre
    if not p.exists():
        return vals
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            vals[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return vals


PROD = cargar_env(".env")
STAGING = cargar_env("env.staging")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--acepto-destino", default="")
    ap.add_argument("--destino", choices=("sandbox", "prod"), default="sandbox")
    args = ap.parse_args()
    _armar_watchdog()

    env = PROD if args.destino == "prod" else STAGING
    m = re.search(r"postgres\.([a-z0-9]+):", env.get("SUPABASE_DB_URL", ""))
    if not m:
        sys.exit("ABORT: no pude extraer la ref del SUPABASE_DB_URL destino.")
    ref = m.group(1)
    modo = "REAL" if args.real else "DRY-RUN"
    if args.real and not ref.startswith(args.acepto_destino or "±"):
        sys.exit(f"ABORT: --real exige --acepto-destino con el prefijo de la ref "
                 f"destino ({ref[:8]}…).")
    print(f"[{modo}] destino: {ref[:8]}…  kind='{KIND}'", flush=True)

    pg = psycopg2.connect(env["SUPABASE_DB_URL"], connect_timeout=20)
    pg.autocommit = False
    cur = pg.cursor()

    cur.execute("select 1 from information_schema.schemata where schema_name='propuestas'")
    if not cur.fetchone():
        pg.close()
        sys.exit("ABORT: el esquema 'propuestas' no existe en este destino. "
                 "Este backfill solo corre donde vive el origen.")

    # ── Diagnóstico ──────────────────────────────────────────────────────────
    cur.execute("""
        select count(*) filter (where imagen is not null)                  con_imagen,
               count(*) filter (where imagen is null)                      sin_imagen,
               count(*) filter (where imagen is not null
                                  and not exists (select 1 from core.products p
                                                   where p.sku = s.sku))   fuera_del_maestro
          from propuestas.competencia_skus s
    """)
    con_img, sin_img, huerfanos = cur.fetchone()
    cur.execute("select count(*) from enrich.product_media where kind = %s", (KIND,))
    ya_en_destino = cur.fetchone()[0]
    cur.execute("""
        select count(*) from propuestas.competencia_skus s
         where s.imagen is not null
           and exists (select 1 from core.products p where p.sku = s.sku)
           and not exists (select 1 from enrich.product_media m
                            where m.sku = s.sku and m.kind = %s and m.source_url = s.imagen)
    """, (KIND,))
    a_insertar = cur.fetchone()[0]

    reporte = {
        "origen": "propuestas.competencia_skus.imagen",
        "con_imagen": con_img,
        "sin_imagen": sin_img,
        "fuera_de_core_products": huerfanos,
        "ya_en_product_media_wc": ya_en_destino,
        "a_insertar": a_insertar,
    }

    if huerfanos:
        print(f"\n[AVISO] {huerfanos} SKUs con imagen NO estan en core.products: "
              "la FK los va a rechazar y quedan fuera.", flush=True)

    if not args.real:
        print("\n== DRY-RUN — nada escrito ==")
        print(json.dumps(reporte, ensure_ascii=False, indent=1))
        pg.close()
        return

    # ── Escritura ────────────────────────────────────────────────────────────
    # `on conflict do nothing` sobre uq_product_media_sku_kind_url: correrlo de
    # mas no duplica. cdn_url queda NULL a proposito — la URL de Woo ES la
    # fuente; el cdn_url lo llena el pipeline de imagenes cuando procesa.
    cur.execute("""
        insert into enrich.product_media (sku, kind, source_url)
        select s.sku, %s, s.imagen
          from propuestas.competencia_skus s
         where s.imagen is not null
           and exists (select 1 from core.products p where p.sku = s.sku)
        on conflict (sku, kind, source_url) do nothing
    """, (KIND,))
    reporte["insertadas"] = cur.rowcount

    cur.execute("select count(*) from enrich.product_media where kind = %s", (KIND,))
    reporte["final_kind_wc"] = cur.fetchone()[0]
    cur.execute("select kind, count(*) from enrich.product_media group by 1 order by 2 desc")
    reporte["por_kind"] = dict(cur.fetchall())

    cur.execute("""insert into migration.reconciliation_runs
                   (dominio, descripcion, conteos, checksums, resultado)
                   values (%s, 'Backfill imagenes Competencia -> enrich.product_media
                   kind=wc (sin llamadas a WooCommerce)', %s, %s, %s)""",
                (FASE, json.dumps(reporte, ensure_ascii=False), json.dumps({}),
                 "con_deltas" if huerfanos else "ok"))
    pg.commit()
    pg.close()

    print("\n== APLICADO ==")
    print(json.dumps(reporte, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
