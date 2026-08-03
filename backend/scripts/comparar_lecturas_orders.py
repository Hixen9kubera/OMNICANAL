"""
comparar_lecturas_orders.py — Arnés de paridad de las lecturas F5 del dominio
PEDIDOS: corre las consultas gemelas (MySQL pedidos_ml vs kubera
channel.orders vía services/orders_read.py) y compara fila a fila los
agregados que consume el tab Ventas.

Reglas del dominio (heredadas de comparar_orders.py):
  - Días CERRADOS (anteriores a hoy CDMX): igualdad ESTRICTA — el pedido es
    inmutable y el espejo ya alcanzó.
  - HOY: informativo — un pedido de hace segundos puede seguir en la cola del
    espejo (2 workers); se reporta la diferencia pero no reprueba.
  - m (SUM(total)) se compara redondeado a 2 decimales (numeric(14,2) en
    ambos lados).

SOLO LECTURA. Uso:  python backend/scripts/comparar_lecturas_orders.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import db, orders_read  # noqa: E402

_TZ_MX = timezone(timedelta(hours=-6))
CUENTAS = ["BEKURA", "SANCORFASHION", "AMAZON", "TEMU", "TIKTOK"]
DIAS_CERRADOS = 14  # días completos hacia atrás que se comparan estricto


def _rango_utc(desde: date, hasta: date) -> tuple[datetime, datetime]:
    ini = datetime.combine(desde, datetime.min.time()) + timedelta(hours=6)
    fin = datetime.combine(hasta, datetime.min.time()) + timedelta(hours=30)
    return ini, fin


def _mysql_horario(ini, fin):
    marcas = ",".join(["%s"] * len(CUENTAS))
    return db.fetch_all(
        f"""SELECT HOUR(DATE_SUB(creado, INTERVAL 6 HOUR)) h, cuenta, estado_wc,
                   COUNT(*) n, SUM(total) m
            FROM pedidos_ml
            WHERE cuenta IN ({marcas}) AND creado >= %s AND creado < %s
            GROUP BY h, cuenta, estado_wc""",
        (*CUENTAS, ini, fin))


def _mysql_rango(ini, fin):
    marcas = ",".join(["%s"] * len(CUENTAS))
    return db.fetch_all(
        f"""SELECT cuenta, estado_wc, COUNT(*) n, SUM(total) m, SUM(es_full) f
            FROM pedidos_ml
            WHERE cuenta IN ({marcas}) AND creado >= %s AND creado < %s
            GROUP BY cuenta, estado_wc""",
        (*CUENTAS, ini, fin))


def _mapa(filas, llaves):
    out = {}
    for r in filas:
        k = tuple(str(r[c]) if r[c] is not None else "" for c in llaves)
        m = r.get("m")
        out[k] = (int(r["n"] or 0),
                  float(Decimal(str(m or 0)).quantize(Decimal("0.01"))),
                  int(r["f"] or 0) if "f" in r else None)
    return out


def _comparar(mysql_rows, kb_rows, llaves):
    a, b = _mapa(mysql_rows, llaves), _mapa(kb_rows, llaves)
    difs = []
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        if va != vb:
            difs.append({"clave": "|".join(k), "mysql": va, "kubera": vb})
    return difs


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    hoy = datetime.now(_TZ_MX).date()
    fallas = 0
    for i in range(DIAS_CERRADOS, -1, -1):
        d = hoy - timedelta(days=i)
        ini, fin = _rango_utc(d, d)
        difs_h = _comparar(_mysql_horario(ini, fin),
                           orders_read.horario(CUENTAS, ini, fin),
                           ("h", "cuenta", "estado_wc"))
        difs_r = _comparar(_mysql_rango(ini, fin),
                           orders_read.rango(CUENTAS, ini, fin),
                           ("cuenta", "estado_wc"))
        estricto = d < hoy
        estado = "OK" if not difs_h and not difs_r else (
            "DIF" if estricto else "DIF (hoy, informativo)")
        if estricto and (difs_h or difs_r):
            fallas += 1
        print(f"{d}  horario:{len(difs_h)} difs  rango:{len(difs_r)} difs  -> {estado}")
        for dd in (difs_h + difs_r)[:6]:
            print("   ", json.dumps(dd, ensure_ascii=False))
    # rango multi-día (como lo pide el tab con desde/hasta)
    ini, fin = _rango_utc(hoy - timedelta(days=7), hoy - timedelta(days=1))
    difs = _comparar(_mysql_rango(ini, fin), orders_read.rango(CUENTAS, ini, fin),
                     ("cuenta", "estado_wc"))
    if difs:
        fallas += 1
    print(f"rango 7 días cerrados: {len(difs)} difs")
    for dd in difs[:6]:
        print("   ", json.dumps(dd, ensure_ascii=False))
    print("VEREDICTO:", "EQUIVALENTE" if fallas == 0 else f"CON DIFERENCIAS ({fallas})")
    sys.exit(0 if fallas == 0 else 2)


if __name__ == "__main__":
    main()
