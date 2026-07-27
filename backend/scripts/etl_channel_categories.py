"""
etl_channel_categories.py — ETL de categorías por canal (Mercado Libre, v1).

Puebla en la BD kubera:
  - channel.categories       (el ÁRBOL: category_id → name, path)   [canal ML]
  - channel.product_category (la ASIGNACIÓN sku → categoría por canal)

Fuentes (solo lectura):
  1. MySQL `categorias_ml` (12.8k filas: sku, category_id, category_name, ruta,
     fuente real/predictor) — trae nombre y ruta, así que NO hay llamadas a la
     API de ML en el backfill.
  2. wp_postmeta `ml_categoria_id` — la elección del PANEL, que MANDA sobre el
     detector (regla 2 de la casa): se carga al final con source='panel' y pisa
     la asignación de categorias_ml.

Reglas (las mismas del ETL v2 de core):
  - CERO TRUNCATE/DELETE; upserts incrementales solo de lo que cambió.
  - Identidad vía migration.id_map (append-only) + normalización mecánica.
  - SKUs sin fila en core.products → ops.migration_issues (la FK impide
    cargarlos); por eso este ETL corre DESPUÉS del de core en el mismo cron.
  - DRY-RUN por default; --real exige --acepto-destino <ref8>.
  - Anti-cuelgue: watchdog + socket timeout global (regla anti-caso-pedidos).
  - Amazon (product types) y Woo (terms) quedan para la v2 de este ETL.

Uso:
  backend/.venv/Scripts/python.exe backend/scripts/etl_channel_categories.py
  backend/.venv/Scripts/python.exe backend/scripts/etl_channel_categories.py --real --acepto-destino tukwcvsi
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
from collections import Counter
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
FASE = "categorias-etl"
CANAL = "mercado_libre"
BATCH = 1000

socket.setdefaulttimeout(60)
TIMEOUT_MIN = int(os.environ.get("ETL_TIMEOUT_MIN", "15"))


def _armar_watchdog() -> None:
    def _matar():
        print(f"WATCHDOG: {TIMEOUT_MIN} min agotados — aborto para no quedar "
              "colgado (regla anti-caso-pedidos).", flush=True)
        os._exit(2)
    t = threading.Timer(TIMEOUT_MIN * 60, _matar)
    t.daemon = True
    t.start()


def cargar_env(nombre: str) -> dict[str, str]:
    vals: dict[str, str] = dict(os.environ)
    p = ROOT / nombre
    if not p.exists():
        return vals
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            vals[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return vals


PROD = cargar_env(".env")
DEST = cargar_env("env.staging")


def ref_destino() -> str:
    m = re.search(r"postgres\.([a-z0-9]+):", DEST.get("SUPABASE_DB_URL", ""))
    if not m:
        sys.exit("ABORT: no pude extraer la ref del SUPABASE_DB_URL destino.")
    return m.group(1)


def normalizar_mecanico(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.upper().startswith("SKU "):
        s = s[4:].strip()
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"\s+", " ", s)
    return s or None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--acepto-destino", default="")
    args = ap.parse_args()
    _armar_watchdog()

    ref = ref_destino()
    modo = "REAL" if args.real else "DRY-RUN"
    if args.real and not ref.startswith(args.acepto_destino or "±"):
        sys.exit(f"ABORT: --real exige --acepto-destino con el prefijo de la ref destino ({ref[:8]}…).")
    print(f"[{modo}] destino: {ref[:8]}…  canal: {CANAL}  (watchdog: {TIMEOUT_MIN} min)", flush=True)

    # ── EXTRACCIÓN ───────────────────────────────────────────────────────────
    my = pymysql.connect(
        host=PROD["DB_HOST"], port=int(PROD.get("DB_PORT", 3306)),
        user=PROD["DB_USER"], password=PROD["DB_PASSWORD"], database=PROD["DB_NAME"],
        charset="utf8mb4", connect_timeout=15, read_timeout=120,
        cursorclass=pymysql.cursors.DictCursor,
    )
    cur = my.cursor()
    cur.execute("""SELECT sku, category_id, category_name, ruta, fuente, updated_at
                   FROM categorias_ml
                   WHERE category_id IS NOT NULL AND category_id <> ''
                   ORDER BY updated_at""")
    t_cat = cur.fetchall()
    cur.close()
    my.close()

    wp = pymysql.connect(
        host=PROD.get("WPDB_HOST") or PROD["DB_HOST"], port=int(PROD.get("WPDB_PORT", 3306)),
        user=PROD["WPDB_USER"], password=PROD["WPDB_PASSWORD"], database=PROD["WPDB_NAME"],
        charset="utf8mb4", connect_timeout=15, read_timeout=120,
        cursorclass=pymysql.cursors.DictCursor,
    )
    wcur = wp.cursor()
    # La elección del panel: meta ml_categoria_id por producto (via su _sku)
    wcur.execute("""SELECT sku.meta_value AS sku, cat.meta_value AS category_id
                    FROM wp_postmeta cat
                    JOIN wp_postmeta sku ON sku.post_id = cat.post_id AND sku.meta_key = '_sku'
                    JOIN wp_posts p ON p.ID = cat.post_id
                    WHERE cat.meta_key = 'ml_categoria_id'
                      AND cat.meta_value LIKE 'MLM%'
                      AND sku.meta_value <> ''
                      AND p.post_status NOT IN ('trash', 'auto-draft')""")
    t_panel = wcur.fetchall()
    wcur.close()
    wp.close()
    print(f"Fuentes: categorias_ml={len(t_cat)} panel(ml_categoria_id)={len(t_panel)}", flush=True)

    # ── ESTADO ACTUAL de la BD kubera ────────────────────────────────────────
    pg = psycopg2.connect(DEST["SUPABASE_DB_URL"], connect_timeout=20)
    pg.autocommit = True
    pcur = pg.cursor()
    pcur.execute("select sku from core.products")
    maestro = {str(r[0]).lower() for r in pcur.fetchall()}
    pcur.execute("select sku_original, sku from migration.id_map")
    id_map = {str(a): str(b) for a, b in pcur.fetchall()}
    id_map_ci = {k.lower(): v for k, v in id_map.items()}
    pcur.execute("select category_id, name, path from channel.categories where channel_id = %s", (CANAL,))
    arbol_actual = {str(r[0]): (r[1], r[2]) for r in pcur.fetchall()}
    pcur.execute("select sku, category_id, source from channel.product_category where channel_id = %s", (CANAL,))
    asig_actual = {str(r[0]).lower(): (str(r[1]), r[2]) for r in pcur.fetchall()}
    # Dedup contra TODO el historial (resueltas incluidas) — ver nota en el
    # ETL v2 de core: lo resuelto/aceptado no renace.
    pcur.execute("select sku, motivo from ops.migration_issues")
    issues_abiertas = {(str(s) if s else None, m) for s, m in pcur.fetchall()}
    print(f"BD kubera: maestro={len(maestro)} árbol_actual={len(arbol_actual)} "
          f"asignaciones_actuales={len(asig_actual)}", flush=True)

    def resolver(raw) -> str | None:
        via = id_map.get(str(raw)) or id_map_ci.get(str(raw).lower()) if raw else None
        if via and not re.search(r"\s", via):
            return via
        mec = normalizar_mecanico(raw)
        if mec is None or re.search(r"\s", mec) or len(mec) > 100:
            return None
        via2 = id_map.get(mec) or id_map_ci.get(mec.lower())
        return via2 if (via2 and not re.search(r"\s", via2)) else mec

    # ── ÁRBOL: category_id → (name, path); la fila más reciente manda ────────
    arbol_nuevo: dict[str, tuple] = {}
    for r in t_cat:  # viene ordenado por updated_at: el último gana
        arbol_nuevo[str(r["category_id"])] = (r["category_name"], r["ruta"])
    arbol_ins, arbol_upd = [], []
    for cid, (name, path) in arbol_nuevo.items():
        actual = arbol_actual.get(cid)
        if actual is None:
            arbol_ins.append((CANAL, cid, name, path))
        elif (actual[0] or None, actual[1] or None) != (name or None, path or None):
            arbol_upd.append((CANAL, cid, name, path))

    # ── ASIGNACIÓN: categorias_ml primero, el PANEL pisa al final ────────────
    issues_nuevas: list[tuple] = []
    tally = Counter()
    plan_asig: dict[str, tuple] = {}  # sku_canon -> (category_id, source)
    for origen, filas, source_de in (("categorias_ml", t_cat, None), ("panel", t_panel, "panel")):
        for r in filas:
            canon = resolver(r["sku"])
            if canon is None:
                tally["sku_invalido"] += 1
                continue
            if canon.lower() not in maestro:
                tally["sku_no_en_maestro"] += 1
                ci = (canon, "sku_no_en_maestro")
                if ci not in issues_abiertas:
                    issues_abiertas.add(ci)
                    issues_nuevas.append((origen, canon, "sku_no_en_maestro",
                                          json.dumps({"category_id": r["category_id"]})))
                continue
            cid = str(r["category_id"])
            if cid not in arbol_nuevo and cid not in arbol_actual:
                # categoría elegida en el panel que no está en el árbol: entra
                # al árbol sin nombre (el builder de identidad lo rellenará)
                arbol_ins.append((CANAL, cid, None, None))
                arbol_nuevo[cid] = (None, None)
            plan_asig[canon] = (cid, source_de or r["fuente"] or "real")

    asig_ins, asig_upd, asig_igual = [], [], 0
    for canon, (cid, source) in plan_asig.items():
        actual = asig_actual.get(canon.lower())
        if actual is None:
            asig_ins.append((canon, CANAL, cid, source))
        elif actual != (cid, source):
            asig_upd.append((canon, CANAL, cid, source))
        else:
            asig_igual += 1

    reporte = {
        "modo": modo, "canal": CANAL,
        "arbol": {"insertar": len(arbol_ins), "actualizar": len(arbol_upd),
                   "total_categorias": len(arbol_nuevo)},
        "asignaciones": {"insertar": len(asig_ins), "actualizar": len(asig_upd),
                          "sin_cambio": asig_igual, "del_panel": len(t_panel)},
        "descartes": dict(tally), "issues_nuevas": len(issues_nuevas),
        "muestra_asig": [{"sku": s, "cat": c, "source": f} for s, _, c, f in asig_ins[:10]],
    }

    if not args.real:
        pcur.close()
        pg.close()
        print("\n== REPORTE DRY-RUN (no se escribió nada) ==")
        print(json.dumps(reporte, ensure_ascii=False, indent=1, default=str))
        return

    # ── ESCRITURA (--real) ───────────────────────────────────────────────────
    pg.autocommit = False
    if arbol_ins or arbol_upd:
        psycopg2.extras.execute_values(pcur, """
            insert into channel.categories (channel_id, category_id, name, path)
            values %s
            on conflict (channel_id, category_id) do update set
              name = excluded.name, path = excluded.path
            where (categories.name, categories.path)
              is distinct from (excluded.name, excluded.path)
        """, arbol_ins + arbol_upd, page_size=BATCH)
    if asig_ins or asig_upd:
        psycopg2.extras.execute_values(pcur, """
            insert into channel.product_category (sku, channel_id, category_id, source)
            values %s
            on conflict (sku, channel_id) do update set
              category_id = excluded.category_id, source = excluded.source,
              updated_at = now()
            where (product_category.category_id, product_category.source)
              is distinct from (excluded.category_id, excluded.source)
        """, asig_ins + asig_upd, page_size=BATCH)
    if issues_nuevas:
        psycopg2.extras.execute_values(pcur, """
            insert into ops.migration_issues (fase, tabla_origen, sku, motivo, valor)
            values %s
        """, [(FASE, t, s, m, v) for (t, s, m, v) in issues_nuevas], page_size=BATCH)
    pcur.execute("select count(*) from channel.categories where channel_id=%s", (CANAL,))
    n_arbol = pcur.fetchone()[0]
    pcur.execute("select count(*) from channel.product_category where channel_id=%s", (CANAL,))
    n_asig = pcur.fetchone()[0]
    pcur.execute("""insert into migration.reconciliation_runs
                    (dominio, descripcion, conteos, checksums, resultado)
                    values (%s, 'ETL categorías ML: árbol + asignación (panel manda)', %s, %s, 'ok')""",
                 (FASE, json.dumps({**reporte["arbol"], **reporte["asignaciones"],
                                    "arbol_final": n_arbol, "asignaciones_final": n_asig,
                                    "issues_nuevas": len(issues_nuevas)}, default=str),
                  json.dumps({})))
    pg.commit()
    pcur.close()
    pg.close()
    print("\n== APLICADO ==")
    print(json.dumps({**reporte, "arbol_final": n_arbol, "asignaciones_final": n_asig},
                     ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
