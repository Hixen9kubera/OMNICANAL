"""
comparar_costos.py — El job de deltas del dominio COSTOS (F3).

Auditor del dual-write: lee costos_validados y costos_finales en MySQL
(fuente de verdad) y en Supabase (espejo), compara en 3 niveles — conteos,
sumas, fila por fila — y deja acta en migration.reconciliation_runs con la
lista exacta de SKUs divergentes.

SOLO LECTURA sobre las tablas comparadas (su única escritura es el acta).
Criterio de salida de la fase: 14 días consecutivos con delta = 0.

Uso:  backend/.venv/Scripts/python.exe backend/scripts/comparar_costos.py
"""
from __future__ import annotations

import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import psycopg2
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
MAX_DETALLE = 200  # SKUs divergentes que se guardan con detalle en el acta

COLS_CV = ("costo_producto", "costo_cbm", "costo_total", "largo", "alto", "ancho", "peso")
COLS_CF = ("costo_producto", "costo_cbm", "costo_unitario", "costo_comision",
           "costo_fee_envio", "precio_sugerido", "precio_base", "ml_cat_id", "pct_comision")


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


def sku_valido(raw) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or len(s) > 100 or re.search(r"\s", s):
        return None  # inválidos conocidos: no pueden existir en Supabase (citext/check)
    return s


def _norm(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v.normalize()
    if isinstance(v, float):
        return Decimal(str(v)).normalize()
    if isinstance(v, int):
        return Decimal(v).normalize()
    return str(v).strip()


def leer(tabla_my: str, cols: tuple, my_cur, pg_cur, tabla_pg: str):
    my_cur.execute(f"SELECT sku, {', '.join(cols)} FROM {tabla_my}")
    mysql_rows = {}
    excluidos = 0
    for r in my_cur.fetchall():
        s = sku_valido(r["sku"])
        if s is None:
            excluidos += 1
            continue
        mysql_rows[s.lower()] = (s, tuple(_norm(r[c]) for c in cols))
    pg_cur.execute(f"select sku, {', '.join(cols)} from {tabla_pg}")
    pg_rows = {}
    for r in pg_cur.fetchall():
        pg_rows[str(r[0]).lower()] = (str(r[0]), tuple(_norm(v) for v in r[1:]))
    return mysql_rows, pg_rows, excluidos


def comparar(nombre: str, cols: tuple, my_rows: dict, pg_rows: dict, excluidos: int):
    solo_my = sorted(set(my_rows) - set(pg_rows))
    solo_pg = sorted(set(pg_rows) - set(my_rows))
    divergentes = []
    for k in my_rows.keys() & pg_rows.keys():
        _, vm = my_rows[k]
        _, vp = pg_rows[k]
        if vm != vp:
            difs = {c: {"mysql": str(vm[i]), "supabase": str(vp[i])}
                    for i, c in enumerate(cols) if vm[i] != vp[i]}
            divergentes.append({"sku": my_rows[k][0], "columnas": difs})
    resumen = {
        "mysql": len(my_rows), "supabase": len(pg_rows),
        "excluidos_sku_invalido": excluidos,
        "solo_en_mysql": len(solo_my), "solo_en_supabase": len(solo_pg),
        "divergentes": len(divergentes),
    }
    detalle = {
        "solo_en_mysql": [my_rows[k][0] for k in solo_my[:MAX_DETALLE]],
        "solo_en_supabase": [pg_rows[k][0] for k in solo_pg[:MAX_DETALLE]],
        "divergentes": divergentes[:MAX_DETALLE],
    }
    limpio = not solo_my and not solo_pg and not divergentes
    print(f"[{nombre}] mysql={resumen['mysql']} supabase={resumen['supabase']} "
          f"(+{excluidos} inválidos excluidos) | solo_mysql={len(solo_my)} "
          f"solo_supabase={len(solo_pg)} divergentes={len(divergentes)} "
          f"-> {'DELTA = 0' if limpio else 'CON DELTAS'}")
    for d in divergentes[:5]:
        print(f"    {d['sku']}: " + "; ".join(
            f"{c} {v['mysql']} vs {v['supabase']}" for c, v in d["columnas"].items()))
    return resumen, detalle, limpio


def main() -> None:
    prod = cargar_env(".env")
    dest = cargar_env("env.staging")   # el proyecto donde escribe el espejo hoy (DEV)
    m = re.search(r"postgres\.([a-z0-9]+):", dest["SUPABASE_DB_URL"])
    print(f"Comparando MySQL prod ↔ Supabase ({(m.group(1) if m else '?')[:8]}…)")

    my = pymysql.connect(
        host=prod["DB_HOST"], port=int(prod.get("DB_PORT", 3306)), user=prod["DB_USER"],
        password=prod["DB_PASSWORD"], database=prod["DB_NAME"], charset="utf8mb4",
        connect_timeout=15, read_timeout=120, cursorclass=pymysql.cursors.DictCursor,
    )
    my_cur = my.cursor()
    pg = psycopg2.connect(dest["SUPABASE_DB_URL"], connect_timeout=20)
    pg.autocommit = True
    pg_cur = pg.cursor()

    actas = {}
    todo_limpio = True
    for nombre, tabla_my, tabla_pg, cols in (
        ("costos_validados", "costos_validados", "costing.costos_validados", COLS_CV),
        ("costos_finales", "costos_finales", "costing.costos_finales", COLS_CF),
    ):
        my_rows, pg_rows, excl = leer(tabla_my, cols, my_cur, pg_cur, tabla_pg)
        resumen, detalle, limpio = comparar(nombre, cols, my_rows, pg_rows, excl)
        actas[nombre] = {"resumen": resumen, "detalle": detalle}
        todo_limpio = todo_limpio and limpio
    my_cur.close()
    my.close()

    resultado = "ok" if todo_limpio else "con_deltas"
    pg_cur.execute("set session characteristics as transaction read write; "
                   "set default_transaction_read_only = off;")
    pg_cur.execute(
        """insert into migration.reconciliation_runs (dominio, descripcion, conteos, checksums, resultado)
           values ('costing-deltas', 'job de deltas del dual-write de costos', %s, %s, %s)""",
        (json.dumps({k: v["resumen"] for k, v in actas.items()}),
         json.dumps({k: v["detalle"] for k, v in actas.items()}, default=str)[:100000],
         resultado),
    )
    pg_cur.close()
    pg.close()
    print(f"\nActa registrada en migration.reconciliation_runs → resultado: {resultado}")
    sys.exit(0 if todo_limpio else 2)


if __name__ == "__main__":
    main()
