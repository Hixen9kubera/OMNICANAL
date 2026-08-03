"""
comparar_lecturas_channel.py — Prueba de equivalencia del flag F5 de CHANNEL.

Compara las 3 lecturas gemelas (leer_inventario / presencia / resumen) entre
MySQL (canal_inventario) y la BD kubera (channel.listings) para los canales
mercado_libre y amazon. SOLO LECTURA.

A diferencia de costos, este dominio es CALIENTE (el sync lo reescribe cada
15 min y los webhooks en segundos): los campos de precio/stock pueden divergir
legítimamente por timing. Por eso:
  - IDENTIDAD (item_id, es_full, logistica, situacion, moneda) se exige exacta.
  - CALIENTES (precio, stock_real, stock_full, stock_fba) toleran hasta 2% de
    filas distintas (se reportan como "calientes", no como fallo).
  - updated_at no se compara (el espejo solo lo toca cuando algo cambia).

Uso: backend/.venv/Scripts/python.exe backend/scripts/comparar_lecturas_channel.py [--muestra 150]
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import channel_read  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
socket.setdefaulttimeout(60)
IDENTIDAD = ("item_id", "es_full", "logistica", "situacion", "moneda")
CALIENTES = ("precio", "stock_real", "stock_full", "stock_fba")
TOL_CALIENTES = 0.02  # 2% de filas


def _watchdog():
    def _m():
        print("WATCHDOG 10min", flush=True)
        os._exit(2)
    t = threading.Timer(600, _m)
    t.daemon = True
    t.start()


def cargar_env(nombre):
    vals = dict(os.environ)
    p = ROOT / nombre
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                vals[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return vals


def _n(v):
    if isinstance(v, Decimal):
        return float(v)
    return v


def _dif(a, b):
    a, b = _n(a), _n(b)
    if a is None and b is None:
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) > 0.01
    return (str(a) if a is not None else None) != (str(b) if b is not None else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--muestra", type=int, default=150)
    args = ap.parse_args()
    _watchdog()
    prod = cargar_env(".env")
    my = pymysql.connect(host=prod["DB_HOST"], port=int(prod.get("DB_PORT", 3306)),
                         user=prod["DB_USER"], password=prod["DB_PASSWORD"],
                         database=prod["DB_NAME"], charset="utf8mb4",
                         connect_timeout=15, read_timeout=120,
                         cursorclass=pymysql.cursors.DictCursor)
    cur = my.cursor()
    rep = {}

    # muestra de SKUs (ml+amazon)
    cur.execute("SELECT DISTINCT sku FROM canal_inventario WHERE canal IN ('mercado_libre','amazon')")
    todos = [r["sku"] for r in cur.fetchall()]
    random.seed(42)
    muestra = random.sample(todos, min(args.muestra, len(todos)))

    # 1) leer_inventario
    ph = ",".join(["%s"] * len(muestra))
    cur.execute(f"SELECT * FROM canal_inventario WHERE canal IN ('mercado_libre','amazon') "
                f"AND sku IN ({ph})", tuple(muestra))
    my_lote = {}
    for r in cur.fetchall():
        my_lote.setdefault(r["sku"], {})[f"{r['canal']}|{r.get('cuenta') or ''}"] = r
    kb_lote = channel_read.leer_inventario(muestra)

    solo_my, solo_kb, dif_id, dif_hot, celdas = [], [], [], 0, 0
    for sku, canales in my_lote.items():
        for clave, rmy in canales.items():
            rkb = (kb_lote.get(sku) or {}).get(clave)
            if rkb is None:
                solo_my.append(f"{sku} {clave}")
                continue
            for c in IDENTIDAD:
                celdas += 1
                if _dif(rmy.get(c), rkb.get(c)):
                    dif_id.append({"sku": sku, "clave": clave, "campo": c,
                                   "mysql": str(rmy.get(c)), "kubera": str(rkb.get(c))})
            if any(_dif(rmy.get(c), rkb.get(c)) for c in CALIENTES):
                dif_hot += 1
    for sku, canales in kb_lote.items():
        for clave in canales:
            if clave not in (my_lote.get(sku) or {}):
                solo_kb.append(f"{sku} {clave}")
    total_filas = sum(len(c) for c in my_lote.values())
    rep["leer_inventario"] = {
        "filas": total_filas, "solo_en_mysql": len(solo_my), "solo_en_kubera": len(solo_kb),
        "identidad_diferente": len(dif_id), "ej_identidad": dif_id[:8],
        "calientes_distintas": dif_hot,
        "pct_calientes": round(dif_hot / total_filas, 4) if total_filas else 0,
        "ej_solo_mysql": solo_my[:5], "ej_solo_kubera": solo_kb[:5],
    }

    # 2) presencia
    cur.execute(f"""SELECT sku, canal, cuenta, item_id, situacion FROM canal_inventario
                    WHERE canal IN ('mercado_libre','amazon') AND sku IN ({ph})
                      AND item_id IS NOT NULL AND item_id <> ''""", tuple(muestra))
    my_pres = {(r["sku"], r["canal"], r.get("cuenta") or "", r["item_id"]) for r in cur.fetchall()}
    kb_pres = {(r["sku"], r["canal"], r["cuenta"] or "", r["item_id"])
               for r in channel_read.presencia(muestra)}
    rep["presencia"] = {"mysql": len(my_pres), "kubera": len(kb_pres),
                         "solo_mysql": len(my_pres - kb_pres), "solo_kubera": len(kb_pres - my_pres),
                         "ej_solo_mysql": [str(x) for x in list(my_pres - kb_pres)[:5]]}

    # 3) resumen (conteos por canal|cuenta; sumas de stock son calientes → se reportan)
    cur.execute("""SELECT canal, cuenta, COUNT(*) skus FROM canal_inventario
                   WHERE canal IN ('mercado_libre','amazon') GROUP BY canal, cuenta""")
    my_res = {f"{r['canal']}|{r.get('cuenta') or ''}": int(r["skus"]) for r in cur.fetchall()}
    kb_res = {f"{r['canal']}|{r['cuenta'] or ''}": int(r["skus"])
              for r in channel_read.resumen_por_canal()}
    rep["resumen_conteos"] = {"mysql": my_res, "kubera": kb_res}
    cur.close()
    my.close()

    equivalente = (not dif_id and not solo_my
                   and rep["presencia"]["solo_mysql"] == 0
                   and rep["leer_inventario"]["pct_calientes"] <= TOL_CALIENTES)
    rep["veredicto"] = ("EQUIVALENTE — el flag puede encenderse" if equivalente
                        else "CON DIFERENCIAS — revisar antes de encender")
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    if not equivalente:
        sys.exit(1)


if __name__ == "__main__":
    main()
