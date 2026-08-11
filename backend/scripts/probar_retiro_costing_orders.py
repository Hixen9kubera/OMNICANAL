"""
probar_retiro_costing_orders.py — Paso 1 del desmantelamiento de COSTOS y
PEDIDOS, en sandbox. Mismo molde que probar_retiro_channel.py.

COSTOS (costing_write._escribir vía guardar_finales):
  C1. Flag true (default) → kubera + espejo inverso MySQL.
  C2. Flag false → kubera SÍ; MySQL congelado.
  C3. Flag false + kubera caída → MySQL absorbe + evento a la cola (emergencia
      intacta: ese camino no depende del flag).
PEDIDOS (orders_write.guardar):
  O1. Flag true → kubera (encabezado+líneas) + espejo inverso.
  O2. Flag false → kubera SÍ; pedidos_ml congelada.
  O3. Flag false + kubera caída → MySQL absorbe + espejo clásico encolado.

MySQL/Slack stubeados; guardia triple de ref; cobayas limpiadas.
Uso: backend/.venv/Scripts/python.exe backend/scripts/probar_retiro_costing_orders.py
"""
from __future__ import annotations

import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REF_KUBERA_PROD = "tukwcvsi"
SKU = "ZZZ-RETIRO-CO"
ORDEN = "9900000000001"

resultados: list[tuple[str, bool]] = []


def check(nombre: str, paso: bool, detalle: str = "") -> None:
    resultados.append((nombre, paso))
    print(f"  [{'PASA' if paso else 'FALLA'}] {nombre}" + (f" — {detalle}" if detalle else ""),
          flush=True)


def env(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for l in (ROOT / nombre).read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


S = env("env.staging")
m = re.search(r"postgres\.([a-z0-9]+):", S.get("SUPABASE_DB_URL", ""))
REF = m.group(1) if m else ""
if not REF or REF.startswith(REF_KUBERA_PROD) or REF == S.get("SUPABASE_PROD_REF", "").strip():
    sys.exit("ABORT: el destino no es el sandbox.")

os.environ["SUPABASE_DB_URL"] = S["SUPABASE_DB_URL"]
os.environ["SUPABASE_WRITE_COSTING"] = "true"
os.environ["SUPABASE_WRITE_ORDERS"] = "true"
os.environ["KUBERA_MIRROR_ENABLED"] = "true"
os.environ["KUBERA_DB_URL"] = S["SUPABASE_DB_URL"]
os.environ["KUBERA_MIRROR_TABLAS"] = "pedidos_ml,pedidos_ml_items"

from config import settings                              # noqa: E402
from services import alertas, costing_write, db, kubera_mirror, orders_write  # noqa: E402
from services import supabase_db as sdb                  # noqa: E402

mysql_llamadas: list[str] = []
db.execute = lambda sql, params=None: mysql_llamadas.append(" ".join(sql.split())[:80])
db.fetch_one = lambda sql, params=None: None
db.fetch_all = lambda sql, params=None: []
alertas.avisar = lambda tipo, texto, nivel="🔴": True
espejados: list[str] = []
espejar_real = kubera_mirror.espejar

FILA = {"costo_unitario": 100.0, "costo_comision": 16.0, "costo_fee_envio": 20.0,
        "precio_base": 250.0, "precio_sugerido": 260.0,
        "pct_comision": 16.0, "canal": "mercado_libre"}
ENC = {"external_order_id": ORDEN, "canal": "mercado_libre", "cuenta": "BEKURA",
       "estado_wc": "processing", "total": 260.0, "creado_at": "2026-08-11T12:00:00Z"}
LIN = {"external_order_id": ORDEN, "canal": "mercado_libre", "cuenta": "BEKURA",
       "estado_wc": "processing", "total": 260.0, "creado_at": "2026-08-11T12:00:00Z",
       "lineas": [{"linea": 1, "sku": SKU, "cantidad": 1, "precio_unitario": 260.0}]}


def leer(sql, params):
    """Primera columna de la primera fila (el cursor del sandbox da dicts)."""
    with sdb.get_cursor() as cur:
        cur.execute(sql, params)
        f = cur.fetchone()
        if f is None:
            return None
        return list(f.values())[0] if isinstance(f, dict) else f[0]


def limpiar():
    with sdb.get_cursor() as cur:
        cur.execute("delete from channel.order_items where external_order_id=%s", (ORDEN,))
        cur.execute("delete from channel.orders where external_order_id=%s", (ORDEN,))
        cur.execute("delete from costing.cost_history where sku=%s", (SKU,))
        cur.execute("delete from costing.costos_finales where sku=%s", (SKU,))
        cur.execute("delete from core.products where sku=%s", (SKU,))


def esperar_hilo():
    time.sleep(0.6)


def main() -> None:
    print(f"RETIRO COSTING+ORDERS (paso 1) contra sandbox {REF[:8]}…\n", flush=True)
    limpiar()
    try:
        # ═══ COSTOS ═══
        print("C1. Con espejo inverso (default)", flush=True)
        assert settings.costing_espejo_inverso and costing_write.activo()
        mysql_llamadas.clear()
        costing_write.guardar_finales(SKU, dict(FILA), lambda: mysql_llamadas.append("mysql"))
        esperar_hilo()
        check("kubera recibe costos_finales",
              bool(leer("select 1 from costing.costos_finales where sku=%s", (SKU,))))
        check("MySQL recibe el espejo inverso", "mysql" in mysql_llamadas)

        print("\nC2. Sin espejo inverso", flush=True)
        settings.costing_espejo_inverso = False
        mysql_llamadas.clear()
        costing_write.guardar_finales(SKU, {**FILA, "costo_unitario": 101.0},
                                      lambda: mysql_llamadas.append("mysql"))
        esperar_hilo()
        f = leer("select costo_unitario from costing.costos_finales where sku=%s", (SKU,))
        check("kubera SÍ recibe (costo_unitario avanza)", f is not None and float(f) == 101.0)
        check("MySQL congelado (cero escrituras)", "mysql" not in mysql_llamadas)

        print("\nC3. Sin espejo + kubera caída → emergencia intacta", flush=True)
        mysql_llamadas.clear()
        original = sdb.get_cursor

        @contextmanager
        def _roto():
            raise RuntimeError("caos-kubera-caida")
            yield

        sdb.get_cursor = _roto
        try:
            costing_write.guardar_finales(SKU, dict(FILA),
                                          lambda: mysql_llamadas.append("mysql"))
            exploto = False
        except Exception:  # noqa: BLE001
            exploto = True
        sdb.get_cursor = original
        encolo = any("espejo_kubera_log" in s for s in mysql_llamadas)
        check("MySQL absorbe + evento encolado, sin explotar",
              not exploto and "mysql" in mysql_llamadas and encolo)
        settings.costing_espejo_inverso = True

        # ═══ PEDIDOS ═══
        print("\nO1. Con espejo inverso (default)", flush=True)
        assert settings.orders_espejo_inverso and orders_write.activo()
        mysql_llamadas.clear()
        orders_write.guardar("t", "venta", dict(ENC), dict(LIN), ORDEN,
                             lambda: mysql_llamadas.append("mysql"))
        esperar_hilo()
        check("kubera recibe el pedido",
              bool(leer("select 1 from channel.orders where external_order_id=%s", (ORDEN,))))
        check("líneas también",
              bool(leer("select 1 from channel.order_items where external_order_id=%s",
                        (ORDEN,))))
        check("MySQL recibe el espejo inverso", "mysql" in mysql_llamadas)

        print("\nO2. Sin espejo inverso", flush=True)
        settings.orders_espejo_inverso = False
        mysql_llamadas.clear()
        orders_write.guardar("t", "venta", {**ENC, "estado_wc": "completed"},
                             dict(LIN), ORDEN, lambda: mysql_llamadas.append("mysql"))
        esperar_hilo()
        f = leer("select estado_wc from channel.orders where external_order_id=%s", (ORDEN,))
        check("kubera SÍ recibe (estado avanza)", f is not None and "complet" in str(f))
        check("pedidos_ml congelada (cero escrituras)", "mysql" not in mysql_llamadas)

        print("\nO3. Sin espejo + kubera caída → emergencia intacta", flush=True)
        kubera_mirror.espejar = (lambda o, fn, tm, tk, op, p, clave=None:
                                 espejados.append(tk))
        mysql_llamadas.clear()
        sdb.get_cursor = _roto
        try:
            orders_write.guardar("t", "venta", dict(ENC), dict(LIN), ORDEN,
                                 lambda: mysql_llamadas.append("mysql"))
            exploto = False
        except Exception:  # noqa: BLE001
            exploto = True
        sdb.get_cursor = original
        kubera_mirror.espejar = espejar_real
        check("MySQL absorbe + evento al espejo clásico, sin explotar",
              not exploto and "mysql" in mysql_llamadas
              and "channel.orders" in espejados)
        settings.orders_espejo_inverso = True
    finally:
        settings.costing_espejo_inverso = True
        settings.orders_espejo_inverso = True
        try:
            limpiar()
        except Exception:  # noqa: BLE001
            pass
        print("\n(cobayas limpiadas del sandbox)", flush=True)

    fallas = [r for r in resultados if not r[1]]
    print(f"\nRESULTADO: {len(resultados) - len(fallas)}/{len(resultados)} PASAN", flush=True)
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
