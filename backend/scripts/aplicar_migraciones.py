"""
aplicar_migraciones.py — Crea/actualiza el SANDBOX aplicando supabase/migrations/
en orden, y valida la paridad del esquema contra supabase/schema_manifest.json
(la foto de la BD kubera de producción).

CANDADO DURO: se NIEGA a correr si el destino (SUPABASE_DB_URL de env.staging)
es el proyecto de producción — comparando contra SUPABASE_PROD_REF y contra la
ref conocida de la BD kubera. Este script existe para el sandbox, punto.

Uso:
  backend/.venv/Scripts/python.exe backend/scripts/aplicar_migraciones.py            # aplica + verifica
  backend/.venv/Scripts/python.exe backend/scripts/aplicar_migraciones.py --verificar-solo
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
MIGRACIONES = ROOT / "supabase" / "migrations"
MANIFIESTO = ROOT / "supabase" / "schema_manifest.json"
REF_KUBERA_PROD = "tukwcvsi"  # ref conocida de la BD kubera (producción operativa)

socket.setdefaulttimeout(60)


def _watchdog():
    def _matar():
        print("WATCHDOG: 10 min — aborto.", flush=True)
        os._exit(2)
    t = threading.Timer(600, _matar)
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verificar-solo", action="store_true")
    args = ap.parse_args()
    _watchdog()

    env = cargar_env("env.staging")
    url = env.get("SUPABASE_DB_URL", "")
    m = re.search(r"postgres\.([a-z0-9]+):", url)
    if not m:
        sys.exit("ABORT: env.staging no tiene SUPABASE_DB_URL válida (¿ya pegaste las llaves del sandbox?).")
    ref = m.group(1)
    prod_ref = env.get("SUPABASE_PROD_REF", "").strip()
    if ref.startswith(REF_KUBERA_PROD) or (prod_ref and ref == prod_ref):
        sys.exit(f"ABORT: el destino ({ref[:8]}…) es la BD kubera de PRODUCCIÓN. "
                 "Este script solo aplica migraciones al SANDBOX.")
    print(f"Destino sandbox verificado: {ref[:8]}…", flush=True)

    pg = psycopg2.connect(url, connect_timeout=20)
    pg.autocommit = False
    cur = pg.cursor()

    if not args.verificar_solo:
        archivos = sorted(MIGRACIONES.glob("*.sql"))
        if not archivos:
            sys.exit(f"ABORT: no hay migraciones en {MIGRACIONES}")
        for f in archivos:
            print(f"Aplicando {f.name}…", flush=True)
            try:
                cur.execute(f.read_text(encoding="utf-8"))
                pg.commit()
            except Exception as exc:  # noqa: BLE001
                pg.rollback()
                sys.exit(f"FALLÓ {f.name}: {exc}")
        print("Migraciones aplicadas.", flush=True)

    # ── Paridad contra el manifiesto de producción ────────────────────────────
    man = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    esquemas = list(man["esquemas"].keys())
    cur.execute(
        "select table_schema, table_name, column_name, data_type, is_nullable "
        "from information_schema.columns where table_schema = any(%s) "
        "order by table_schema, table_name, ordinal_position", (esquemas,))
    sandbox: dict = {}
    for sch, tab, col, dt, nul in cur.fetchall():
        sandbox.setdefault(sch, {}).setdefault(tab, []).append(
            f"{col}:{dt}:{'null' if nul == 'YES' else 'notnull'}")
    pg.close()

    faltantes, extras, columnas_dif = [], [], []
    for sch, tablas in man["esquemas"].items():
        for tab, det in tablas.items():
            cols_prod = det["columnas"]
            cols_sand = sandbox.get(sch, {}).get(tab)
            if cols_sand is None:
                faltantes.append(f"{sch}.{tab}")
            elif cols_sand != cols_prod:
                columnas_dif.append({
                    "tabla": f"{sch}.{tab}",
                    "solo_en_prod": sorted(set(cols_prod) - set(cols_sand)),
                    "solo_en_sandbox": sorted(set(cols_sand) - set(cols_prod)),
                })
    for sch, tablas in sandbox.items():
        for tab in tablas:
            if tab not in man["esquemas"].get(sch, {}):
                extras.append(f"{sch}.{tab}")

    veredicto = "PARIDAD OK" if not (faltantes or columnas_dif) else "CON DIFERENCIAS"
    print(json.dumps({
        "veredicto": veredicto,
        "tablas_manifiesto": sum(len(t) for t in man["esquemas"].values()),
        "tablas_sandbox": sum(len(t) for t in sandbox.values()),
        "faltantes_en_sandbox": faltantes,
        "extras_en_sandbox": extras,
        "columnas_diferentes": columnas_dif,
    }, ensure_ascii=False, indent=1))
    if veredicto != "PARIDAD OK":
        sys.exit(1)


if __name__ == "__main__":
    main()
