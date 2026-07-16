"""
etl_core_products.py — Fase 2 de la migración: homologación de identidad.

Puebla `core.products` en Supabase DEV desde la UNIÓN de 4 fuentes:
  1. MySQL `productos`        (lo publicado/en workflow — congelada desde 2026-07-07)
  2. MySQL `costos_validados` (el catálogo real del packing list)
  3. MySQL `categorias_ml`    (la curaduría de categorías)
  4. Odoo en vivo             (XML-RPC; SKUs que solo existen en el ERP)

y replica `costos_validados` + `costos_finales` a `costing.*`.

REGLAS (plan maestro §15.1 / Fase 2):
  - Producción SOLO LECTURA: MySQL (1 conexión, por lotes) y Odoo (paginado).
  - TODO lo escrito va al proyecto Supabase DEV. El script se NIEGA a correr si
    SUPABASE_DB_URL apunta a la ref de producción (SUPABASE_PROD_REF).
  - Nada se inventa: campo desconocido = NULL. Nada se descarta en silencio:
    inválidos/colisiones → ops.migration_issues; traza → migration.id_map.
  - Idempotente: re-correrlo produce el mismo resultado (upserts + limpieza de
    la corrida anterior en id_map/issues de fase F2-etl).

Uso:  backend/.venv/Scripts/python.exe backend/scripts/etl_core_products.py
"""
from __future__ import annotations

import json
import re
import sys
import xmlrpc.client
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent  # raíz del repo
FASE = "F2-etl"
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


PROD = cargar_env(".env")          # MySQL prod (lectura) + Odoo
DEV = cargar_env("env.staging")    # Supabase DEV (escritura)


def candado_destino() -> str:
    """El destino DEBE ser el proyecto DEV. Si es la ref de prod, abortar."""
    m = re.search(r"postgres\.([a-z0-9]+):", DEV["SUPABASE_DB_URL"])
    ref = m.group(1) if m else ""
    prod_ref = DEV.get("SUPABASE_PROD_REF", "")
    if not ref:
        sys.exit("ABORT: no pude extraer la ref del SUPABASE_DB_URL destino.")
    if prod_ref and ref == prod_ref:
        sys.exit("ABORT: el destino es el Supabase de PRODUCCIÓN. Este ETL solo escribe en DEV.")
    return ref


def normalizar_sku(raw: str | None) -> tuple[str | None, str | None]:
    """Devuelve (sku_normalizado, motivo_invalidez). Solo recorta extremos."""
    if raw is None:
        return None, "sku nulo"
    sku = str(raw).strip()
    if not sku:
        return None, "sku vacío"
    if len(sku) > 100:
        return None, f"sku >100 chars ({len(sku)})"
    if re.search(r"\s", sku):
        return None, "sku con espacios internos"
    return sku, None


def main() -> None:
    dev_ref = candado_destino()
    print(f"Destino verificado: Supabase DEV ({dev_ref[:8]}…)")

    # ── EXTRACCIÓN MySQL (1 conexión, solo SELECT) ────────────────────────────
    my = pymysql.connect(
        host=PROD["DB_HOST"], port=int(PROD.get("DB_PORT", 3306)),
        user=PROD["DB_USER"], password=PROD["DB_PASSWORD"], database=PROD["DB_NAME"],
        charset="utf8mb4", connect_timeout=15, read_timeout=120,
        cursorclass=pymysql.cursors.DictCursor,
    )
    cur = my.cursor()
    cur.execute("""SELECT sku, wc_id, wc_parent_id, odoo_id, nombre, status_wc,
                          variaciones, created_at, updated_at
                   FROM productos""")
    t_productos = cur.fetchall()
    cur.execute("SELECT * FROM costos_validados")
    t_cv = cur.fetchall()
    cur.execute("SELECT sku FROM categorias_ml")
    t_cat = cur.fetchall()
    cur.execute("""SELECT sku, costo_producto, costo_cbm, costo_unitario, costo_comision,
                          costo_fee_envio, precio_sugerido, precio_base, ml_cat_id,
                          pct_comision, peso_origen, created_at, updated_at
                   FROM costos_finales""")
    t_cf = cur.fetchall()
    # checksums de origen
    cur.execute("SELECT COUNT(*) n, COALESCE(SUM(costo_total),0) s FROM costos_validados")
    chk_cv_src = cur.fetchone()
    cur.execute("SELECT COUNT(*) n, COALESCE(SUM(precio_sugerido),0) s FROM costos_finales")
    chk_cf_src = cur.fetchone()
    cur.close()
    my.close()
    print(f"MySQL leído: productos={len(t_productos)} costos_validados={len(t_cv)} "
          f"categorias_ml={len(t_cat)} costos_finales={len(t_cf)}")

    # ── EXTRACCIÓN Odoo (XML-RPC, paginado, solo lectura) ─────────────────────
    odoo_skus: dict[str, dict] = {}
    try:
        common = xmlrpc.client.ServerProxy(f"{PROD['ODOO_URL']}/xmlrpc/2/common")
        uid = common.authenticate(PROD["ODOO_DB"], PROD["ODOO_USER"], PROD["ODOO_PASSWORD"], {})
        models = xmlrpc.client.ServerProxy(f"{PROD['ODOO_URL']}/xmlrpc/2/object")
        offset = 0
        while True:
            lote = models.execute_kw(
                PROD["ODOO_DB"], uid, PROD["ODOO_PASSWORD"],
                "product.product", "search_read",
                [[["default_code", "!=", False]]],
                {"fields": ["default_code", "name"], "offset": offset, "limit": 500},
            )
            if not lote:
                break
            for p in lote:
                odoo_skus[str(p["default_code"]).strip()] = {"odoo_id": p["id"], "name": p["name"]}
            offset += 500
        print(f"Odoo leído: {len(odoo_skus)} SKUs con default_code")
    except Exception as exc:  # noqa: BLE001
        print(f"AVISO: Odoo no disponible ({exc}) — continúo con 3 fuentes; "
              f"odoo_id quedará NULL y se backfillea después.")

    # ── UNIÓN con trazabilidad ────────────────────────────────────────────────
    issues: list[tuple] = []          # (tabla_origen, sku, motivo, valor_json)
    id_map: dict[str, tuple] = {}     # sku_original -> (sku_norm, wc_id, odoo_id, tabla_origen)
    por_clave: dict[str, dict] = {}   # lower(sku) -> registro consolidado
    # costos_finales entra como fuente de ÚLTIMO recurso: sus SKUs sin padre en
    # ninguna otra fuente son los huérfanos puros (plan F2: placeholder 'orphan').
    PRECEDENCIA = {"productos": 0, "costos_validados": 1, "categorias_ml": 2,
                   "odoo": 3, "costos_finales": 4}

    def incorporar(raw_sku, fuente: str, **campos) -> None:
        sku, motivo = normalizar_sku(raw_sku)
        if motivo:
            issues.append((fuente, str(raw_sku)[:100] if raw_sku else None, motivo,
                           json.dumps({"sku_original": str(raw_sku)}, default=str)))
            return
        clave = sku.lower()
        reg = por_clave.get(clave)
        if reg is None:
            reg = por_clave[clave] = {
                "sku": sku, "fuentes": set(), "variantes_raw": set(),
                "name": None, "wc_id": None, "wc_parent_id": None, "odoo_id": None,
                "status": None, "has_variations": False, "fuente_ganadora": fuente,
            }
        else:
            # colisión de forma (capitalización/espacios extremos) entre fuentes
            if sku != reg["sku"] and sku not in reg["variantes_raw"]:
                issues.append((fuente, sku, "colision_capitalizacion",
                               json.dumps({"gano": reg["sku"], "variante": sku})))
        reg["fuentes"].add(fuente)
        reg["variantes_raw"].add(sku)
        gana = PRECEDENCIA[fuente] < PRECEDENCIA[reg["fuente_ganadora"]]
        if gana:
            reg["fuente_ganadora"] = fuente
            reg["sku"] = sku
        for k, v in campos.items():
            if v in (None, ""):
                continue
            if reg[k] is None or gana:
                reg[k] = v
        id_map[str(raw_sku)] = (reg["sku"], campos.get("wc_id"), campos.get("odoo_id"), fuente)

    for r in t_productos:
        incorporar(r["sku"], "productos", name=r["nombre"], wc_id=r["wc_id"],
                   wc_parent_id=r["wc_parent_id"], status=r["status_wc"],
                   has_variations=bool(r["variaciones"]))
    for r in t_cv:
        incorporar(r["sku"], "costos_validados", wc_id=r.get("wc_id"), status=None)
    for r in t_cat:
        incorporar(r["sku"], "categorias_ml")
    for sku_o, d in odoo_skus.items():
        incorporar(sku_o, "odoo", name=d["name"], odoo_id=d["odoo_id"])
    for r in t_cf:
        incorporar(r["sku"], "costos_finales")

    # odoo_id: SIEMPRE del Odoo vivo (el de `productos` está desalineado — plan F2)
    for reg in por_clave.values():
        vivo = odoo_skus.get(reg["sku"]) or odoo_skus.get(reg["sku"].upper()) or odoo_skus.get(reg["sku"].lower())
        reg["odoo_id"] = vivo["odoo_id"] if vivo else None

    # status honesto según fuentes
    for reg in por_clave.values():
        if reg["status"]:
            continue  # el de Woo (fuente productos) manda
        f = reg["fuentes"]
        if f == {"costos_finales"}:
            reg["status"] = "orphan"  # tiene pricing pero no existe en ningún maestro
            issues.append(("costos_finales", reg["sku"], "huerfano_solo_en_costos_finales",
                           json.dumps({"fuentes": sorted(f)})))
        else:
            reg["status"] = ("packing_list_only" if "costos_validados" in f
                             else "odoo_only" if "odoo" in f
                             else "category_only")

    # wc_id duplicado entre SKUs distintos (la columna es UNIQUE en el DDL)
    por_wc: dict[int, list[str]] = defaultdict(list)
    for reg in por_clave.values():
        if reg["wc_id"]:
            por_wc[int(reg["wc_id"])].append(reg["sku"])
    for wc_id, skus in por_wc.items():
        if len(skus) > 1:
            skus_orden = sorted(skus)
            for perdedor in skus_orden[1:]:
                por_clave[perdedor.lower()]["wc_id"] = None
                issues.append(("union", perdedor, "wc_id_duplicado",
                               json.dumps({"wc_id": wc_id, "se_quedo_en": skus_orden[0]})))

    print(f"UNIÓN: {len(por_clave)} SKUs únicos | issues acumuladas: {len(issues)}")

    # ── CARGA a Supabase DEV ──────────────────────────────────────────────────
    pg = psycopg2.connect(DEV["SUPABASE_DB_URL"], connect_timeout=20)
    pg.autocommit = True
    pcur = pg.cursor()
    pcur.execute("set session characteristics as transaction read write; "
                 "set default_transaction_read_only = off;")
    pg.autocommit = False

    # limpieza de la corrida anterior (determinismo) — SOLO artefactos de esta fase
    pcur.execute("delete from migration.id_map")
    pcur.execute("delete from ops.migration_issues where fase = %s", (FASE,))

    filas = [(r["sku"], r["name"], r["wc_id"], r["wc_parent_id"], r["odoo_id"],
              r["status"], r["has_variations"], ",".join(sorted(r["fuentes"])))
             for r in por_clave.values()]
    psycopg2.extras.execute_values(pcur, """
        insert into core.products (sku, name, wc_id, wc_parent_id, odoo_id, status,
                                   has_variations, source)
        values %s
        on conflict (sku) do update set
          name = excluded.name, wc_id = excluded.wc_id,
          wc_parent_id = excluded.wc_parent_id, odoo_id = excluded.odoo_id,
          status = excluded.status, has_variations = excluded.has_variations,
          source = excluded.source
    """, filas, page_size=BATCH)

    psycopg2.extras.execute_values(pcur, """
        insert into migration.id_map (sku_original, sku, wc_id, odoo_id, tabla_origen)
        values %s on conflict (sku_original) do nothing
    """, [(orig, v[0], v[1], v[2], v[3]) for orig, v in id_map.items()], page_size=BATCH)

    if issues:
        psycopg2.extras.execute_values(pcur, """
            insert into ops.migration_issues (fase, tabla_origen, sku, motivo, valor)
            values %s
        """, [(FASE, t, s, m, v) for (t, s, m, v) in issues], page_size=BATCH)
    pg.commit()

    # réplica costing (histórico as-is: currency MXN, fx/formula NULL)
    def _f(r, k):
        v = r.get(k)
        return v if v is not None else None

    cv_filas = [(r["sku"].strip(), _f(r, "wc_id"), _f(r, "wc_status"), _f(r, "wc_type"),
                 _f(r, "contenedor"), _f(r, "costo_producto"), _f(r, "costo_cbm"),
                 _f(r, "largo"), _f(r, "alto"), _f(r, "ancho"), _f(r, "peso"),
                 _f(r, "costo_total"), _f(r, "cajas"), _f(r, "piezas_por_caja"),
                 _f(r, "created_at"))
                for r in t_cv if normalizar_sku(r["sku"])[0]]
    psycopg2.extras.execute_values(pcur, """
        insert into costing.costos_validados
          (sku, wc_id, wc_status, wc_type, contenedor, costo_producto, costo_cbm,
           largo, alto, ancho, peso, costo_total, cajas, piezas_por_caja, created_at)
        values %s
        on conflict (sku) do update set
          wc_id=excluded.wc_id, wc_status=excluded.wc_status, wc_type=excluded.wc_type,
          contenedor=excluded.contenedor, costo_producto=excluded.costo_producto,
          costo_cbm=excluded.costo_cbm, largo=excluded.largo, alto=excluded.alto,
          ancho=excluded.ancho, peso=excluded.peso, costo_total=excluded.costo_total,
          cajas=excluded.cajas, piezas_por_caja=excluded.piezas_por_caja
    """, cv_filas, page_size=BATCH)

    cf_filas = [(r["sku"].strip(), _f(r, "costo_producto"), _f(r, "costo_cbm"),
                 _f(r, "costo_unitario"), _f(r, "ml_cat_id"), _f(r, "pct_comision"),
                 _f(r, "costo_comision"), _f(r, "costo_fee_envio"), _f(r, "precio_sugerido"),
                 _f(r, "precio_base"), _f(r, "peso_origen"), _f(r, "created_at"))
                for r in t_cf if normalizar_sku(r["sku"])[0]]
    psycopg2.extras.execute_values(pcur, """
        insert into costing.costos_finales
          (sku, costo_producto, costo_cbm, costo_unitario, ml_cat_id, pct_comision,
           costo_comision, costo_fee_envio, precio_sugerido, precio_base, peso_origen,
           created_at)
        values %s
        on conflict (sku) do update set
          costo_producto=excluded.costo_producto, costo_cbm=excluded.costo_cbm,
          costo_unitario=excluded.costo_unitario, ml_cat_id=excluded.ml_cat_id,
          pct_comision=excluded.pct_comision, costo_comision=excluded.costo_comision,
          costo_fee_envio=excluded.costo_fee_envio, precio_sugerido=excluded.precio_sugerido,
          precio_base=excluded.precio_base, peso_origen=excluded.peso_origen
    """, cf_filas, page_size=BATCH)
    pg.commit()

    # ── VALIDACIÓN: conteos + checksums MySQL ↔ DEV ───────────────────────────
    pcur.execute("select count(*) from core.products")
    n_products = pcur.fetchone()[0]
    pcur.execute("select count(*), coalesce(sum(costo_total),0) from costing.costos_validados")
    chk_cv_dst = pcur.fetchone()
    pcur.execute("select count(*), coalesce(sum(precio_sugerido),0) from costing.costos_finales")
    chk_cf_dst = pcur.fetchone()
    pcur.execute("select status, count(*) from core.products group by status order by 2 desc")
    por_status = pcur.fetchall()
    pcur.execute("select motivo, count(*) from ops.migration_issues where fase=%s group by 1", (FASE,))
    por_motivo = pcur.fetchall()

    def _d(x) -> Decimal:
        return Decimal(str(x))

    delta_cv = _d(chk_cv_src["s"]) - _d(chk_cv_dst[1])
    delta_cf = _d(chk_cf_src["s"]) - _d(chk_cf_dst[1])
    resultado = "ok" if (delta_cv == 0 and delta_cf == 0
                         and chk_cv_src["n"] == chk_cv_dst[0]
                         and chk_cf_src["n"] == chk_cf_dst[0]) else "con_deltas"

    conteos = {"core.products": n_products,
               "costos_validados": {"mysql": chk_cv_src["n"], "dev": chk_cv_dst[0]},
               "costos_finales": {"mysql": chk_cf_src["n"], "dev": chk_cf_dst[0]},
               "por_status": {s: c for s, c in por_status},
               "issues": {m: c for m, c in por_motivo}}
    checksums = {"sum_costo_total": {"mysql": str(chk_cv_src["s"]), "dev": str(chk_cv_dst[1]),
                                     "delta": str(delta_cv)},
                 "sum_precio_sugerido": {"mysql": str(chk_cf_src["s"]), "dev": str(chk_cf_dst[1]),
                                         "delta": str(delta_cf)}}
    pcur.execute("""insert into migration.reconciliation_runs
                    (dominio, descripcion, conteos, checksums, resultado)
                    values ('core+costing', 'ETL F2: union 4 fuentes + replica costing',
                            %s, %s, %s)""",
                 (json.dumps(conteos, default=str), json.dumps(checksums), resultado))
    pg.commit()
    pcur.close()
    pg.close()

    print("\n== RESULTADO ==")
    print(f"core.products: {n_products} SKUs")
    for s, c in por_status:
        print(f"  status {s}: {c}")
    print(f"costos_validados: mysql={chk_cv_src['n']} dev={chk_cv_dst[0]} | "
          f"SUM(costo_total) delta={delta_cv}")
    print(f"costos_finales:  mysql={chk_cf_src['n']} dev={chk_cf_dst[0]} | "
          f"SUM(precio_sugerido) delta={delta_cf}")
    print(f"issues: {dict(por_motivo) or 'ninguna'}")
    print(f"reconciliation_runs: registrado con resultado = {resultado}")


if __name__ == "__main__":
    main()
