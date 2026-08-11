"""
backfill_categories_arbol.py — Llena parent_id / root_id / root_name en
channel.categories (migración 0012), SIN llamar a la API de Mercado Libre.

Fuente: MySQL WordPress `wp_ml_categorias` — el árbol COMPLETO de ML ya
descargado offline por un snippet de WordPress (12,256 categorías, todas con
parent_id; 31 raíces con parent_id=''). Cobertura medida el 2026-08-10 contra
las 2,692 categorías de channel.categories: 100%, cero faltantes.

Por qué no se usa el `path` de channel.categories: trae NOMBRES
('Ropa, Bolsas y Calzado > Pantalones'), no ids. root_name sí saldría de ahí,
pero root_id no — y root_id es el que carga peso: competencia_captura.py lo usa
para clasificar nivel='raiz'|'hoja', que es parte de la PK de
enrich.market_bestsellers.

Reglas (las mismas del ETL de categorías):
  - CERO TRUNCATE/DELETE. UPDATE incremental solo de lo que cambió.
  - Toca EXCLUSIVAMENTE parent_id / root_id / root_name. Nunca name ni path.
  - DRY-RUN por default; --real exige --acepto-destino <ref8>.
  - Watchdog anti-cuelgue + socket timeout global.
  - Las categorías NO cubiertas se REPORTAN, no se asumen en cero: el árbol de
    WordPress lo mantiene un proceso ajeno al panel y puede quedarse viejo.

NO es one-shot: el upsert de etl_channel_categories.py inserta categorías
nuevas con estas 3 columnas en NULL. Re-correr después de cada corrida suya.

Uso:
  backend/.venv/Scripts/python.exe backend/scripts/backfill_categories_arbol.py
  backend/.venv/Scripts/python.exe backend/scripts/backfill_categories_arbol.py --real --acepto-destino yvootpbz
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
FASE = "F2-backfill-arbol"
CANAL = "mercado_libre"          # wp_ml_categorias es el árbol de ML, solo ML
BATCH = 1000
TIMEOUT_MIN = 10

socket.setdefaulttimeout(60)

# La consola de Windows es cp1252 y revienta con cualquier caracter fuera de
# ese mapa. Los nombres de categoria de ML traen acentos y flechas.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:      # entornos donde no es un TextIOWrapper
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


PROD = cargar_env(".env")            # origen: MySQL WordPress (solo lectura)
STAGING = cargar_env("env.staging")  # destino por default en local: el sandbox


def env_destino(destino: str) -> dict[str, str]:
    """En Railway no existe env.staging, asi que STAGING ya trae os.environ (la
    BD de produccion) y el default funciona solo. En local, env.staging pisa a
    os.environ y apunta al sandbox — por eso apuntar a produccion desde una
    terminal exige --destino prod, ademas del --acepto-destino."""
    return PROD if destino == "prod" else STAGING


def ref_de(env: dict[str, str]) -> str:
    m = re.search(r"postgres\.([a-z0-9]+):", env.get("SUPABASE_DB_URL", ""))
    if not m:
        sys.exit("ABORT: no pude extraer la ref del SUPABASE_DB_URL destino.")
    return m.group(1)


def raiz_de(cid: str, arbol: dict[str, tuple[str, str]]) -> str | None:
    """Sube por parent_id hasta la raíz. Guarda contra ciclos y contra padres
    que no existen en la tabla (árbol viejo o podado)."""
    visto: set[str] = set()
    actual = cid
    while actual in arbol:
        padre = arbol[actual][0]
        if not padre or padre == actual or padre in visto:
            return actual                 # es raíz (parent_id='') o corté un ciclo
        if padre not in arbol:
            return actual                 # el padre no está: el más alto que conozco
        visto.add(actual)
        actual = padre
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--acepto-destino", default="")
    ap.add_argument("--canal", default=CANAL)
    ap.add_argument("--destino", choices=("sandbox", "prod"), default="sandbox",
                    help="En Railway da igual: alla el default ya resuelve a la "
                         "BD de produccion. Desde una terminal local, 'prod' es "
                         "el unico modo de apuntar a la BD kubera.")
    args = ap.parse_args()
    _armar_watchdog()

    DEST = env_destino(args.destino)
    ref = ref_de(DEST)
    modo = "REAL" if args.real else "DRY-RUN"
    if args.real and not ref.startswith(args.acepto_destino or "±"):
        sys.exit(f"ABORT: --real exige --acepto-destino con el prefijo de la ref "
                 f"destino ({ref[:8]}…).")
    print(f"[{modo}] destino: {ref[:8]}…  canal: {args.canal}  "
          f"(watchdog: {TIMEOUT_MIN} min)", flush=True)

    # ── EXTRACCIÓN: el árbol offline de WordPress ────────────────────────────
    my = pymysql.connect(host=PROD["DB_HOST"], user=PROD["WPDB_USER"],
                         password=PROD["WPDB_PASSWORD"], database=PROD["WPDB_NAME"],
                         charset="utf8mb4", connect_timeout=20)
    with my.cursor() as mc:
        mc.execute("select ml_cat_id, parent_id, name from wp_ml_categorias")
        arbol: dict[str, tuple[str, str]] = {r[0]: (r[1] or "", r[2] or "")
                                             for r in mc.fetchall()}
    my.close()
    n_raices = sum(1 for cid, (p, _) in arbol.items() if not p)
    print(f"wp_ml_categorias: {len(arbol)} categorías · {n_raices} raíces", flush=True)
    if not arbol:
        sys.exit("ABORT: wp_ml_categorias vino vacía. ¿Credenciales WPDB_*?")

    # ── ESTADO ACTUAL en Postgres ────────────────────────────────────────────
    pg = psycopg2.connect(DEST["SUPABASE_DB_URL"], connect_timeout=20)
    pg.autocommit = False
    pc = pg.cursor()
    pc.execute("""select category_id, parent_id, root_id, root_name
                    from channel.categories where channel_id = %s""", (args.canal,))
    actuales = {r[0]: (r[1], r[2], r[3]) for r in pc.fetchall()}
    print(f"channel.categories: {len(actuales)} filas del canal {args.canal}", flush=True)

    # ── CÁLCULO ──────────────────────────────────────────────────────────────
    cambios: list[tuple] = []
    sin_cambio = 0
    no_cubiertas: list[str] = []

    for cid, (cur_parent, cur_root, cur_root_name) in actuales.items():
        if cid not in arbol:
            no_cubiertas.append(cid)
            continue
        padre_raw = arbol[cid][0]
        padre = padre_raw or None                 # raíz: parent_id='' → NULL
        rid = raiz_de(cid, arbol)
        rname = arbol[rid][1] if rid in arbol else None
        if (cur_parent, cur_root, cur_root_name) == (padre, rid, rname):
            sin_cambio += 1
            continue
        cambios.append((args.canal, cid, padre, rid, rname))

    raices_distintas = sorted({c[3] for c in cambios if c[3]})
    reporte = {
        "canal": args.canal,
        "categorias_en_destino": len(actuales),
        "arbol_wordpress": len(arbol),
        "cubiertas": len(actuales) - len(no_cubiertas),
        "no_cubiertas": len(no_cubiertas),
        "sin_cambio": sin_cambio,
        "a_actualizar": len(cambios),
        "raices_distintas": len(raices_distintas),
    }

    # Las no cubiertas se REPORTAN. Si el árbol de WordPress se quedó viejo,
    # esto es lo único que lo delata.
    if no_cubiertas:
        print(f"\n⚠ {len(no_cubiertas)} categorías SIN cobertura en "
              f"wp_ml_categorias (el árbol offline puede estar viejo):", flush=True)
        for cid in no_cubiertas[:25]:
            print(f"    {cid}")
        if len(no_cubiertas) > 25:
            print(f"    … y {len(no_cubiertas) - 25} más")
        reporte["no_cubiertas_ids"] = no_cubiertas[:100]

    if not args.real:
        print("\n== DRY-RUN — nada escrito ==")
        print(json.dumps(reporte, ensure_ascii=False, indent=1, default=str))
        if cambios:
            print("\nmuestra de los cambios:")
            for c in cambios[:8]:
                print(f"    {c[1]:<12} padre={str(c[2] or '-'):<12} raiz={c[3]}")
        pc.close()
        pg.close()
        return

    # ── ESCRITURA (--real) ───────────────────────────────────────────────────
    # UPDATE, no upsert: las filas ya existen (las crea etl_channel_categories).
    # El `is distinct from` es cinturón sobre tirantes: ya filtramos arriba.
    if cambios:
        psycopg2.extras.execute_values(pc, """
            update channel.categories c
               set parent_id = v.parent_id,
                   root_id   = v.root_id,
                   root_name = v.root_name
              from (values %s) as v(channel_id, category_id, parent_id, root_id, root_name)
             where c.channel_id  = v.channel_id
               and c.category_id = v.category_id
               and (c.parent_id, c.root_id, c.root_name)
                   is distinct from (v.parent_id, v.root_id, v.root_name)
        """, cambios, page_size=BATCH)
        # OJO: pc.rowcount aqui reporta solo el ULTIMO lote de execute_values,
        # no el total. La verificacion buena son los conteos de abajo.

    pc.execute("""select count(*) total, count(root_id) con_raiz
                    from channel.categories where channel_id = %s""", (args.canal,))
    total, con_raiz = pc.fetchone()
    reporte["final_total"] = total
    reporte["final_con_raiz"] = con_raiz

    pc.execute("""insert into migration.reconciliation_runs
                  (dominio, descripcion, conteos, checksums, resultado)
                  values (%s, 'Backfill árbol de categorías (parent/root) desde
                  wp_ml_categorias — sin API de ML', %s, %s, %s)""",
               (FASE, json.dumps(reporte, ensure_ascii=False, default=str),
                json.dumps({}), "con_deltas" if no_cubiertas else "ok"))
    pg.commit()
    pc.close()
    pg.close()

    print("\n== APLICADO ==")
    print(json.dumps(reporte, ensure_ascii=False, indent=1, default=str))
    if no_cubiertas:
        print("\nResultado del acta: con_deltas (hay categorías sin cobertura).")


if __name__ == "__main__":
    main()
