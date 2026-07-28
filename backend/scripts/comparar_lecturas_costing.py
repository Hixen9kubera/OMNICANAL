"""
comparar_lecturas_costing.py — Prueba de equivalencia del flag F5 de costos.

Ejecuta las MISMAS lecturas por las dos rutas — MySQL (la actual del panel) y
BD kubera (services/costing_read.py) — y las compara campo por campo:

  1. contenedores           (lista completa)
  2. conteo global          (costos_validados)
  3. detalle de N SKUs      (muestra aleatoria; números con tolerancia 0.01)
  4. listado orden sku_asc  (primera página: secuencia de SKUs + nombres)

SOLO LECTURA en ambas fuentes. El flag NO se toca: este arnés llama a las
funciones directamente. Veredicto EQUIVALENTE = el flag puede encenderse.

Uso: backend/.venv/Scripts/python.exe backend/scripts/comparar_lecturas_costing.py [--muestra 150]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import socket
import sys
import threading
from decimal import Decimal
from pathlib import Path

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/
from services import costing_read  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
socket.setdefaulttimeout(60)

CAMPOS_CV = ("contenedor", "largo", "alto", "ancho", "peso",
             "costo_producto", "costo_cbm", "costo_total")
CAMPOS_CF = ("costo_unitario", "precio_base", "precio_sugerido",
             "costo_comision", "costo_fee_envio", "ml_cat_id", "pct_comision")
TOL = 0.01


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


def _num(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    return v  # texto (contenedor, ml_cat_id)


def _difieren(a, b) -> bool:
    a, b = _num(a), _num(b)
    if a is None and b is None:
        return False
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) > TOL
    return a != b


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--muestra", type=int, default=150)
    args = ap.parse_args()
    _watchdog()

    prod = cargar_env(".env")
    my = pymysql.connect(
        host=prod["DB_HOST"], port=int(prod.get("DB_PORT", 3306)), user=prod["DB_USER"],
        password=prod["DB_PASSWORD"], database=prod["DB_NAME"], charset="utf8mb4",
        connect_timeout=15, read_timeout=120, cursorclass=pymysql.cursors.DictCursor,
    )
    cur = my.cursor()
    rep: dict = {}

    # 1) contenedores
    cur.execute("SELECT contenedor, COUNT(*) AS n FROM costos_validados "
                "WHERE contenedor IS NOT NULL AND contenedor <> '' "
                "GROUP BY contenedor ORDER BY contenedor")
    cont_my = [{"contenedor": r["contenedor"], "n": int(r["n"])} for r in cur.fetchall()]
    cont_kb = costing_read.contenedores()
    rep["contenedores"] = {"mysql": len(cont_my), "kubera": len(cont_kb),
                            "identicos": cont_my == cont_kb}

    # 2) conteo global
    cur.execute("SELECT COUNT(*) n FROM costos_validados")
    n_my = int(cur.fetchone()["n"])
    _, n_kb = costing_read.listado(1, 1, None, None, "sku_asc", [])
    rep["conteo_global"] = {"mysql": n_my, "kubera": n_kb, "identicos": n_my == n_kb}

    # 3) detalle de N SKUs al azar
    cur.execute("SELECT sku FROM costos_validados")
    todos = [r["sku"] for r in cur.fetchall()]
    random.seed(42)  # muestra reproducible entre corridas
    muestra = random.sample(todos, min(args.muestra, len(todos)))
    difs = []
    for sku in muestra:
        cur.execute("SELECT * FROM costos_validados WHERE sku=%s", (sku,))
        cv_my = cur.fetchone() or {}
        cur.execute("SELECT * FROM costos_finales WHERE sku=%s", (sku,))
        cf_my = cur.fetchone() or {}
        cf_kb, cv_kb = costing_read.detalle(sku)
        cf_kb, cv_kb = cf_kb or {}, cv_kb or {}
        for campo in CAMPOS_CV:
            if _difieren(cv_my.get(campo), cv_kb.get(campo)):
                difs.append({"sku": sku, "tabla": "validados", "campo": campo,
                             "mysql": str(cv_my.get(campo)), "kubera": str(cv_kb.get(campo))})
        for campo in CAMPOS_CF:
            if _difieren(cf_my.get(campo), cf_kb.get(campo)):
                difs.append({"sku": sku, "tabla": "finales", "campo": campo,
                             "mysql": str(cf_my.get(campo)), "kubera": str(cf_kb.get(campo))})
    rep["detalle_muestra"] = {"skus_comparados": len(muestra),
                               "campos_por_sku": len(CAMPOS_CV) + len(CAMPOS_CF),
                               "diferencias": len(difs), "ejemplos": difs[:12]}

    # 4) listado primera página, orden determinista
    cur.execute(
        """SELECT v.sku, p.nombre FROM costos_validados v
           LEFT JOIN productos p ON p.sku = v.sku
           ORDER BY v.sku ASC LIMIT 50""")
    pag_my = [(r["sku"], r["nombre"]) for r in cur.fetchall()]
    filas_kb, _ = costing_read.listado(1, 50, None, None, "sku_asc", [])
    pag_kb = [(r["sku"], r["nombre"]) for r in filas_kb]
    secuencia_ok = [s for s, _ in pag_my] == [s for s, _ in pag_kb]
    nombres_dif = [
        {"sku": a[0], "mysql": a[1], "kubera": b[1]}
        for a, b in zip(pag_my, pag_kb) if a[0] == b[0] and (a[1] or "") != (b[1] or "")
    ]
    rep["listado_pagina1"] = {"secuencia_skus_identica": secuencia_ok,
                               "nombres_distintos": len(nombres_dif),
                               "ejemplos_nombres": nombres_dif[:5]}

    cur.close()
    my.close()

    equivalente = (rep["contenedores"]["identicos"] and rep["conteo_global"]["identicos"]
                   and rep["detalle_muestra"]["diferencias"] == 0
                   and rep["listado_pagina1"]["secuencia_skus_identica"])
    rep["veredicto"] = "EQUIVALENTE — el flag puede encenderse" if equivalente \
        else "CON DIFERENCIAS — revisar antes de encender"
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    if not equivalente:
        sys.exit(1)


if __name__ == "__main__":
    main()
