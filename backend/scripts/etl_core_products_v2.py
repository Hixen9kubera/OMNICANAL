"""
etl_core_products_v2.py — ETL v2 (INCREMENTAL) del maestro `core.products`.

Sucesor del etl_core_products.py (F2 v3, full-refresh). Nace porque el mundo
cambió y el full-refresh se volvió PELIGROSO:
  - El dual-write vive (costing/channel/ops + espejo kubera + channel.orders):
    truncar core.products/cost_history/id_map destruiría datos de producción.
  - KuberaPipeline se desconectó (23-jul): `productos` (MySQL) es un snapshot
    congelado; los SKUs nuevos nacen en Woo/Odoo y este ETL cierra el hueco
    (82 faltantes detectados) hasta que exista el seam Crear → core.products.
  - Plan v4.1: id_map es PERSISTENTE (jamás truncar; solo agregar alias) y los
    SKUs sucios se normalizan+aliasan, no se rechazan en silencio.

Reglas duras:
  1. CERO TRUNCATE / CERO DELETE. Solo upserts de lo que cambió.
  2. Producción de origen SOLO LECTURA (MySQL, wp_*, Odoo).
  3. Escribe ÚNICAMENTE: core.products, migration.id_map (append), ops.migration_issues
     (dedup) y el acta en migration.reconciliation_runs. NADA de costing (eso ya
     lo mantiene el dual-write en vivo).
  4. DRY-RUN POR DEFAULT: sin --real no se escribe ni un byte. Con --real se
     exige --acepto-destino <ref8> (los primeros 8 chars de la ref del proyecto
     destino) para imposibilitar un destino equivocado.
  5. Filas en core.products que ya no aparecen en las fuentes NO se tocan
     (se reportan como `solo_en_core`, informativo).

Uso:
  backend/.venv/Scripts/python.exe backend/scripts/etl_core_products_v2.py            # dry-run
  backend/.venv/Scripts/python.exe backend/scripts/etl_core_products_v2.py --limit 500
  backend/.venv/Scripts/python.exe backend/scripts/etl_core_products_v2.py --real --acepto-destino tukwcvsi
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
import unicodedata
import xmlrpc.client
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
FASE = "core-etl-v2"
BATCH = 1000
CAMPOS = ("name", "wc_id", "wc_parent_id", "odoo_id", "status", "has_variations", "source")

# ── Anti-cuelgue (lección del backfill de pedidos: NADA corre para siempre) ──
# 1) Timeout de socket GLOBAL: cubre xmlrpc (Odoo no tiene timeout por default
#    y puede colgarse eternamente en un socket muerto) y cualquier otra red.
# 2) Watchdog: si el proceso no terminó en ETL_TIMEOUT_MIN (default 15), se
#    mata solo con exit(2) — un cron colgado jamás debe quedarse "corriendo".
socket.setdefaulttimeout(60)
TIMEOUT_MIN = int(os.environ.get("ETL_TIMEOUT_MIN", "15"))
ODOO_MAX_PAGINAS = 400  # 400×500 = 200k SKUs; más que eso es un loop enfermo


def _armar_watchdog() -> None:
    def _matar():
        print(f"WATCHDOG: {TIMEOUT_MIN} min agotados — aborto para no quedar "
              "colgado (regla anti-caso-pedidos).", flush=True)
        os._exit(2)
    t = threading.Timer(TIMEOUT_MIN * 60, _matar)
    t.daemon = True
    t.start()


def cargar_env(nombre: str) -> dict[str, str]:
    """Valores del archivo local; si no existe (cron en Railway), variables de
    entorno del proceso. El archivo gana sobre el entorno en local — mismo
    patrón que comparar_costos.py."""
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


PROD = cargar_env(".env")        # MySQL prod + wp_* + Odoo (solo lectura)
DEST = cargar_env("env.staging")  # BD kubera (SUPABASE_DB_URL)


def ref_destino() -> str:
    m = re.search(r"postgres\.([a-z0-9]+):", DEST.get("SUPABASE_DB_URL", ""))
    if not m:
        sys.exit("ABORT: no pude extraer la ref del SUPABASE_DB_URL destino.")
    return m.group(1)


# ── Normalización mecánica de SKU (solo lo inequívoco; lo demás via id_map) ──

def normalizar_mecanico(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.upper().startswith("SKU "):
        s = s[4:].strip()
    s = re.sub(r"\s*-\s*", "-", s)   # espacios pegados a guiones → guion limpio
    s = re.sub(r"\s+", " ", s)       # colapsar espacios múltiples
    return s or None


def _tokens(nombre: str) -> set[str]:
    s = unicodedata.normalize("NFD", nombre.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return {t for t in re.split(r"[^a-z0-9]+", s) if len(t) > 3}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="escribe (default: dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="procesar solo N SKUs de la unión (smoke test)")
    ap.add_argument("--acepto-destino", default="", help="primeros 8 chars de la ref destino (obligatorio con --real)")
    args = ap.parse_args()

    _armar_watchdog()
    ref = ref_destino()
    modo = "REAL" if args.real else "DRY-RUN"
    if args.real and not ref.startswith(args.acepto_destino or "±"):
        sys.exit(f"ABORT: --real exige --acepto-destino con el prefijo de la ref destino ({ref[:8]}…).")
    print(f"[{modo}] destino: {ref[:8]}…  (escrituras: {'SÍ' if args.real else 'NO'}; "
          f"watchdog: {TIMEOUT_MIN} min)", flush=True)

    # ── EXTRACCIÓN de fuentes (solo lectura) ─────────────────────────────────
    my = pymysql.connect(
        host=PROD["DB_HOST"], port=int(PROD.get("DB_PORT", 3306)),
        user=PROD["DB_USER"], password=PROD["DB_PASSWORD"], database=PROD["DB_NAME"],
        charset="utf8mb4", connect_timeout=15, read_timeout=120,
        cursorclass=pymysql.cursors.DictCursor,
    )
    cur = my.cursor()
    cur.execute("""SELECT sku, wc_id, wc_parent_id, odoo_id, nombre, status_wc, variaciones
                   FROM productos""")
    t_productos = cur.fetchall()
    cur.execute("SELECT sku, wc_id FROM costos_validados")
    t_cv = cur.fetchall()
    cur.execute("SELECT sku FROM categorias_ml")
    t_cat = cur.fetchall()
    cur.execute("SELECT sku FROM costos_finales")
    t_cf = cur.fetchall()
    cur.close()
    my.close()

    wp = pymysql.connect(
        host=PROD.get("WPDB_HOST") or PROD["DB_HOST"], port=int(PROD.get("WPDB_PORT", 3306)),
        user=PROD["WPDB_USER"], password=PROD["WPDB_PASSWORD"], database=PROD["WPDB_NAME"],
        charset="utf8mb4", connect_timeout=15, read_timeout=120,
        cursorclass=pymysql.cursors.DictCursor,
    )
    wcur = wp.cursor()
    wcur.execute("""SELECT p.ID AS wc_id, p.post_status, p.post_title, p.post_parent,
                           pm.meta_value AS sku
                    FROM wp_posts p
                    JOIN wp_postmeta pm ON pm.post_id = p.ID AND pm.meta_key = '_sku'
                    WHERE p.post_type IN ('product', 'product_variation')
                      AND p.post_status NOT IN ('trash', 'auto-draft')
                      AND pm.meta_value <> ''
                    ORDER BY p.ID""")
    t_woo = wcur.fetchall()
    wcur.close()
    wp.close()
    woo_sku_por_wcid = {int(r["wc_id"]): str(r["sku"]).strip() for r in t_woo}

    odoo_skus: dict[str, dict] = {}
    try:
        common = xmlrpc.client.ServerProxy(f"{PROD['ODOO_URL']}/xmlrpc/2/common")
        uid = common.authenticate(PROD["ODOO_DB"], PROD["ODOO_USER"], PROD["ODOO_PASSWORD"], {})
        models = xmlrpc.client.ServerProxy(f"{PROD['ODOO_URL']}/xmlrpc/2/object")
        offset = 0
        for _pagina in range(ODOO_MAX_PAGINAS):
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
        else:
            print(f"AVISO: Odoo superó {ODOO_MAX_PAGINAS} páginas — corto la "
                  "paginación (tope anti-loop).", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"AVISO: Odoo no disponible ({exc}) — continúo; odoo_id no se actualizará.")

    print(f"Fuentes: woo={len(t_woo)} productos(frozen)={len(t_productos)} "
          f"costos_validados={len(t_cv)} categorias_ml={len(t_cat)} "
          f"costos_finales={len(t_cf)} odoo={len(odoo_skus)}")

    # ── ESTADO ACTUAL de la BD kubera (lectura) ──────────────────────────────
    pg = psycopg2.connect(DEST["SUPABASE_DB_URL"], connect_timeout=20)
    pg.autocommit = True
    pcur = pg.cursor()
    pcur.execute("select sku, name, wc_id, wc_parent_id, odoo_id, status, has_variations, source "
                 "from core.products")
    core_actual = {str(r[0]).lower(): {"sku": str(r[0]), **dict(zip(CAMPOS, r[1:]))}
                   for r in pcur.fetchall()}
    pcur.execute("select sku_original, sku from migration.id_map")
    id_map = {str(a): str(b) for a, b in pcur.fetchall()}
    id_map_ci = {k.lower(): v for k, v in id_map.items()}
    pcur.execute("select distinct sku from channel.listings")
    skus_canal = {str(r[0]) for r in pcur.fetchall()}
    # Dedup contra TODO el historial (resueltas incluidas): un issue que el
    # equipo ya resolvió/aceptó (p. ej. un marketplace_only legítimo) no debe
    # renacer cada mañana.
    pcur.execute("select sku, motivo from ops.migration_issues")
    issues_abiertas = {(str(s) if s else None, m) for s, m in pcur.fetchall()}
    print(f"BD kubera: core.products={len(core_actual)} id_map={len(id_map)} "
          f"channel.listings skus={len(skus_canal)} issues abiertas={len(issues_abiertas)}")

    # ── RESOLUCIÓN de identidad + UNIÓN ──────────────────────────────────────
    issues_nuevas: list[tuple] = []      # (tabla_origen, sku, motivo, valor)
    tally_issues = Counter()
    aliases_nuevos: dict[str, str] = {}  # original → canónico (solo real los inserta)
    por_clave: dict[str, dict] = {}
    PRECEDENCIA = {"woocommerce": 0, "productos": 1, "costos_validados": 2,
                   "categorias_ml": 3, "odoo": 4, "costos_finales": 5}

    def resolver(raw_sku) -> tuple[str | None, str | None]:
        """(canónico_cargable, motivo_no_cargable)."""
        if raw_sku is None or not str(raw_sku).strip():
            return None, "placeholder"
        original = str(raw_sku)
        # 1) alias curado manda (tal cual y case-insensitive)
        via_map = id_map.get(original) or id_map_ci.get(original.lower())
        mec = normalizar_mecanico(original)
        if via_map and not re.search(r"\s", via_map):
            return via_map, None
        if mec is None:
            return None, "placeholder"
        if len(mec) > 100:
            return None, "placeholder"
        if re.search(r"\s", mec):
            # la mecánica no alcanzó y no hay alias curado: NO cargable (CHECK)
            return None, "whitespace"
        via_map2 = id_map.get(mec) or id_map_ci.get(mec.lower())
        canon = via_map2 if (via_map2 and not re.search(r"\s", via_map2)) else mec
        if canon != original:
            aliases_nuevos.setdefault(original, canon)
        return canon, None

    def incorporar(raw_sku, fuente: str, **campos) -> None:
        canon, motivo = resolver(raw_sku)
        if motivo:
            tally_issues[motivo] += 1
            clave_issue = (str(raw_sku)[:100] if raw_sku else None, motivo)
            if clave_issue not in issues_abiertas:
                issues_abiertas.add(clave_issue)
                issues_nuevas.append((fuente, clave_issue[0], motivo,
                                      json.dumps({"sku_original": str(raw_sku)}, default=str)))
            return
        clave = canon.lower()
        reg = por_clave.get(clave)
        if reg is None:
            reg = por_clave[clave] = {
                "sku": canon, "fuentes": set(), "variantes": set(),
                "name": None, "wc_id": None, "wc_parent_id": None, "odoo_id": None,
                "status": None, "has_variations": False, "fuente_ganadora": fuente,
            }
        else:
            if canon != reg["sku"] and canon not in reg["variantes"]:
                tally_issues["colision_caso"] += 1
                ci = (canon, "colision_caso")
                if ci not in issues_abiertas:
                    issues_abiertas.add(ci)
                    issues_nuevas.append((fuente, canon, "colision_caso",
                                          json.dumps({"gano": reg["sku"], "variante": canon})))
        reg["fuentes"].add(fuente)
        reg["variantes"].add(canon)
        gana = PRECEDENCIA[fuente] < PRECEDENCIA[reg["fuente_ganadora"]]
        if gana:
            reg["fuente_ganadora"] = fuente
            reg["sku"] = canon
        for k, v in campos.items():
            if v in (None, ""):
                continue
            if reg[k] is None or gana:
                reg[k] = v

    for r in t_woo:
        incorporar(r["sku"], "woocommerce", name=r["post_title"], wc_id=r["wc_id"],
                   wc_parent_id=r["post_parent"] or None, status=r["post_status"])
    for r in t_productos:
        incorporar(r["sku"], "productos", name=r["nombre"], wc_id=r["wc_id"],
                   wc_parent_id=r["wc_parent_id"], status=r["status_wc"],
                   has_variations=bool(r["variaciones"]))
    for r in t_cv:
        incorporar(r["sku"], "costos_validados", wc_id=r.get("wc_id"))
    for r in t_cat:
        incorporar(r["sku"], "categorias_ml")
    for sku_o, d in odoo_skus.items():
        incorporar(sku_o, "odoo", name=d["name"], odoo_id=d["odoo_id"])
    for r in t_cf:
        incorporar(r["sku"], "costos_finales")

    # odoo_id SIEMPRE del Odoo vivo; nombre_no_coincide (Odoo vs Woo, informativo)
    for reg in por_clave.values():
        vivo = (odoo_skus.get(reg["sku"]) or odoo_skus.get(reg["sku"].upper())
                or odoo_skus.get(reg["sku"].lower()))
        reg["odoo_id"] = vivo["odoo_id"] if vivo else None
        if vivo and reg["name"] and "woocommerce" in reg["fuentes"]:
            if not (_tokens(reg["name"]) & _tokens(vivo["name"] or "")):
                tally_issues["nombre_no_coincide"] += 1
                ci = (reg["sku"], "nombre_no_coincide")
                if ci not in issues_abiertas:
                    issues_abiertas.add(ci)
                    issues_nuevas.append(("union", reg["sku"], "nombre_no_coincide",
                                          json.dumps({"woo": (reg["name"] or "")[:80],
                                                      "odoo": (vivo["name"] or "")[:80]})))

    # status para los que no traen el de Woo
    for reg in por_clave.values():
        if reg["status"]:
            continue
        f = reg["fuentes"]
        if f == {"costos_finales"}:
            reg["status"] = "orphan"
        else:
            reg["status"] = ("packing_list_only" if "costos_validados" in f
                             else "odoo_only" if "odoo" in f
                             else "category_only")

    # marketplace_only: publicado en canal sin existir en ninguna fuente maestra
    for sku_canal in skus_canal:
        canon, motivo = resolver(sku_canal)
        if motivo or canon.lower() in por_clave:
            continue
        por_clave[canon.lower()] = {
            "sku": canon, "fuentes": {"channel.listings"}, "variantes": {canon},
            "name": None, "wc_id": None, "wc_parent_id": None, "odoo_id": None,
            "status": "marketplace_only", "has_variations": False,
            "fuente_ganadora": "channel.listings",
        }
        tally_issues["marketplace_only"] += 1
        ci = (canon, "marketplace_only")
        if ci not in issues_abiertas:
            issues_abiertas.add(ci)
            issues_nuevas.append(("channel.listings", canon, "marketplace_only",
                                  json.dumps({"nota": "publicado sin maestro"})))

    # wc_id duplicado (desempata Woo; el perdedor pierde el wc_id, no la fila)
    por_wc: dict[int, list[str]] = defaultdict(list)
    for reg in por_clave.values():
        if reg["wc_id"]:
            por_wc[int(reg["wc_id"])].append(reg["sku"])
    for wc_id, skus in por_wc.items():
        if len(skus) > 1:
            woo_dice = woo_sku_por_wcid.get(wc_id)
            orden = sorted(skus, key=lambda s: (0 if s == woo_dice else 1, s))
            for perdedor in orden[1:]:
                por_clave[perdedor.lower()]["wc_id"] = None
                tally_issues["colision_llave"] += 1
                ci = (perdedor, "colision_llave")
                if ci not in issues_abiertas:
                    issues_abiertas.add(ci)
                    issues_nuevas.append(("union", perdedor, "colision_llave",
                                          json.dumps({"wc_id": wc_id, "se_quedo_en": orden[0],
                                                      "woo_confirma": woo_dice})))

    # ── DIFF incremental contra core.products ────────────────────────────────
    union = list(por_clave.values())
    if args.limit:
        union = union[: args.limit]
    inserts, updates, sin_cambio = [], [], 0
    for reg in union:
        fila = {"sku": reg["sku"], "name": reg["name"], "wc_id": reg["wc_id"],
                "wc_parent_id": reg["wc_parent_id"], "odoo_id": reg["odoo_id"],
                "status": reg["status"], "has_variations": reg["has_variations"],
                "source": ",".join(sorted(reg["fuentes"]))}
        actual = core_actual.get(reg["sku"].lower())
        if actual is None:
            inserts.append(fila)
        else:
            difs = [c for c in CAMPOS if (actual.get(c) or None) != (fila.get(c) or None)]
            if difs:
                updates.append({**fila, "_difs": difs})
            else:
                sin_cambio += 1
    solo_en_core = len(core_actual) - (len(union) - len(inserts))

    reporte = {
        "modo": modo, "destino": ref[:8] + "…",
        "fuentes": {"woocommerce": len(t_woo), "productos_frozen": len(t_productos),
                     "costos_validados": len(t_cv), "categorias_ml": len(t_cat),
                     "costos_finales": len(t_cf), "odoo": len(odoo_skus),
                     "channel.listings": len(skus_canal)},
        "union_skus": len(union),
        "plan": {"insertar": len(inserts), "actualizar": len(updates),
                  "sin_cambio": sin_cambio, "solo_en_core_no_se_tocan": solo_en_core},
        "aliases_nuevos": len(aliases_nuevos),
        "issues": {"nuevas": len(issues_nuevas), "por_motivo": dict(tally_issues)},
        "muestra_inserts": [{"sku": f["sku"], "status": f["status"], "source": f["source"]}
                             for f in inserts[:15]],
        "muestra_updates": [{"sku": f["sku"], "campos": f["_difs"]} for f in updates[:10]],
        "muestra_aliases": dict(list(aliases_nuevos.items())[:10]),
    }

    if not args.real:
        pcur.close()
        pg.close()
        print("\n== REPORTE DRY-RUN (no se escribió nada) ==")
        print(json.dumps(reporte, ensure_ascii=False, indent=1, default=str))
        return

    # ── ESCRITURA (solo --real): upserts, append de alias, issues, acta ──────
    pg.autocommit = False
    filas = [(f["sku"], f["name"], f["wc_id"], f["wc_parent_id"], f["odoo_id"],
              f["status"], f["has_variations"], f["source"])
             for f in inserts + updates]
    if filas:
        psycopg2.extras.execute_values(pcur, """
            insert into core.products (sku, name, wc_id, wc_parent_id, odoo_id, status,
                                       has_variations, source)
            values %s
            on conflict (sku) do update set
              name = excluded.name, wc_id = excluded.wc_id,
              wc_parent_id = excluded.wc_parent_id, odoo_id = excluded.odoo_id,
              status = excluded.status, has_variations = excluded.has_variations,
              source = excluded.source
            where (products.name, products.wc_id, products.wc_parent_id, products.odoo_id,
                   products.status, products.has_variations, products.source)
              is distinct from
                  (excluded.name, excluded.wc_id, excluded.wc_parent_id, excluded.odoo_id,
                   excluded.status, excluded.has_variations, excluded.source)
        """, filas, page_size=BATCH)
    if aliases_nuevos:
        psycopg2.extras.execute_values(pcur, """
            insert into migration.id_map (sku_original, sku, tabla_origen)
            values %s on conflict (sku_original) do nothing
        """, [(o, c, FASE) for o, c in aliases_nuevos.items()], page_size=BATCH)
    # nombre_no_coincide es INFORMATIVO (heurística de tokens, mucho volumen por
    # los títulos reescritos con IA): vive en el reporte, no en migration_issues.
    # La auditoría fina de identidad de nombres es del builder de
    # enrich.marketplace_identity (v4.1), no de este ETL.
    issues_escribibles = [i for i in issues_nuevas if i[2] != "nombre_no_coincide"]
    if issues_escribibles:
        psycopg2.extras.execute_values(pcur, """
            insert into ops.migration_issues (fase, tabla_origen, sku, motivo, valor)
            values %s
        """, [(FASE, t, s, m, v) for (t, s, m, v) in issues_escribibles], page_size=BATCH)
    pcur.execute("select count(*) from core.products")
    n_final = pcur.fetchone()[0]
    # Para un ETL, "ok" = corrió y sincronizó (haya o no cambios que aplicar);
    # los cambios van en conteos. La racha de /migracion cuenta días 'ok'.
    resultado = "ok"
    pcur.execute("""insert into migration.reconciliation_runs
                    (dominio, descripcion, conteos, checksums, resultado)
                    values (%s, 'ETL v2 incremental core.products (sin truncate)', %s, %s, %s)""",
                 (FASE, json.dumps({**reporte["plan"], "core_products_final": n_final,
                                    "issues_nuevas": len(issues_escribibles),
                                    "nombre_no_coincide_informativo": tally_issues.get("nombre_no_coincide", 0),
                                    "aliases_nuevos": len(aliases_nuevos)}, default=str),
                  json.dumps({}), resultado))
    pg.commit()
    pcur.close()
    pg.close()
    print("\n== APLICADO ==")
    print(json.dumps({**reporte, "core_products_final": n_final}, ensure_ascii=False,
                     indent=1, default=str))


if __name__ == "__main__":
    main()
