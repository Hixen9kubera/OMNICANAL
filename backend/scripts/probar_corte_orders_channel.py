"""
probar_corte_orders_channel.py — Pruebas de los CORTES F6 de PEDIDOS y CHANNEL
(opción A) contra el SANDBOX de Supabase. MySQL 100% stubeado (recorder): jamás
se toca la base MySQL real ni la BD kubera de producción (guardia triple).

PEDIDOS (orders_write.guardar — el wiring de pedidos_ml.sincronizar es if/else):
  O1. Corte ON, kubera arriba → channel.orders + channel.order_items primarias
      en sandbox + espejo inverso MySQL registrado (thunk).
  O2. Paridad de semántica: total congelado (inmutable) y comisión 0 → valor
      real UNA sola vez, igual que el ON DUPLICATE de MySQL.
  O3. kubera CAÍDA → MySQL aguanta + los DOS payloads viajan por el espejo
      clásico (espejar registrado) + Slack.
CHANNEL (inventario._upsert — integración completa):
  C1. Corte ON, kubera arriba → channel.listings primaria en sandbox (con
      identidad core.products) + espejo inverso MySQL registrado.
  C2. kubera CAÍDA → el sync NO truena: MySQL registrado + Slack; el siguiente
      ciclo auto-sana (sin cola, a propósito).
  C3. Flag OFF → mundo viejo (MySQL primario; kubera quieta sin dual-write).

Cobayas: pedido ZZZ-CAOS-1 / SKUs ZZZ-CORTE-CH*; cuenta BEKURA del sandbox (se
crea si no existe y se limpia si la creamos). Todo se borra al final.
Uso: backend/.venv/Scripts/python.exe backend/scripts/probar_corte_orders_channel.py
"""
from __future__ import annotations

import os
import re
import socket
import sys
import threading
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
socket.setdefaulttimeout(30)

REF_KUBERA_PROD = "tukwcvsi"
PEDIDO = "ZZZ-CAOS-1"
SKU_CH = "ZZZ-CORTE-CH"


def _watchdog():
    def _m():
        print("WATCHDOG 10min — abort", flush=True); os._exit(2)
    t = threading.Timer(600, _m); t.daemon = True; t.start()


def env(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for l in (ROOT / nombre).read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return d


S = env("env.staging")
resultados: list[dict] = []


def check(nombre: str, paso: bool, detalle: str = ""):
    resultados.append({"prueba": nombre, "paso": paso})
    print(f"  [{'PASA' if paso else 'FALLA'}] {nombre}"
          + (f" — {detalle}" if detalle else ""), flush=True)


def guardia_sandbox(url: str) -> str:
    m = re.search(r"postgres\.([a-z0-9]+):", url or "")
    ref = m.group(1) if m else ""
    if not ref:
        sys.exit("ABORT: no pude extraer ref del sandbox.")
    if ref.startswith(REF_KUBERA_PROD) or ref == S.get("SUPABASE_PROD_REF", "").strip():
        sys.exit("ABORT: el destino no es el sandbox. Aborto.")
    return ref


def q(sql: str, params=()):
    with psycopg2.connect(S["SUPABASE_DB_URL"], connect_timeout=15) as cn:
        with cn.cursor() as c:
            c.execute(sql, params)
            if c.description:
                cols = [d[0] for d in c.description]
                return [dict(zip(cols, r)) for r in c.fetchall()]
            return None


def main() -> None:
    _watchdog()
    ref = guardia_sandbox(S["SUPABASE_DB_URL"])
    print(f"CORTES F6 PEDIDOS+CHANNEL contra sandbox {ref[:8]}…\n", flush=True)

    os.environ["SUPABASE_DB_URL"] = S["SUPABASE_DB_URL"]
    os.environ["SUPABASE_WRITE_ORDERS"] = "true"
    os.environ["SUPABASE_WRITE_CHANNEL"] = "true"
    os.environ["SUPABASE_DUAL_WRITE"] = "false"
    os.environ["SUPABASE_DUAL_WRITE_CHANNEL"] = "false"
    # censo del espejo: necesario para que el corte escriba LÍNEAS y para que
    # la caída de kubera enrute por espejar() (aquí espejar se stubea)
    os.environ["KUBERA_MIRROR_ENABLED"] = "true"
    os.environ["KUBERA_DB_URL"] = S["SUPABASE_DB_URL"]
    os.environ["KUBERA_MIRROR_TABLAS"] = "pedidos_ml,pedidos_ml_items"

    from config import settings
    from services import alertas, channel_mirror, db, inventario, kubera_mirror
    from services import orders_write
    from services import supabase_db as sdb

    # ── Stub MySQL: cursor grabador (execute/executemany) ────────────────────
    mysql_llamadas: list[tuple[str, object]] = []

    class _CurStub:
        def execute(self, sql, params=None):
            mysql_llamadas.append((" ".join(sql.split())[:160], params))

        def executemany(self, sql, seq):
            mysql_llamadas.append((" ".join(sql.split())[:160], f"x{len(list(seq))}"))

        def fetchall(self):
            return []

    class _CursorCtx:
        def __enter__(self):
            return _CurStub()

        def __exit__(self, *a):
            return False

    db.get_cursor = lambda: _CursorCtx()
    db.execute = lambda sql, params=None: mysql_llamadas.append((" ".join(sql.split())[:160], params))
    db.fetch_one = lambda sql, params=None: None
    db.fetch_all = lambda sql, params=None: []

    avisos: list[str] = []
    alertas.avisar = lambda tipo, texto, nivel="🔴": avisos.append(tipo) or True

    # cuenta BEKURA en el sandbox (para _cuenta_uuid del espejo channel)
    cuenta_creada = False
    filas = q("select legacy_code from core.accounts where legacy_code = 'BEKURA'")
    if not filas:
        q("insert into core.accounts (legacy_code, nombre, canal) values ('BEKURA','BEKURA-caos','mercado_libre')")
        cuenta_creada = True

    def limpiar():
        q("delete from channel.order_items where external_order_id = %s", (PEDIDO,))
        q("delete from channel.orders where external_order_id = %s", (PEDIDO,))
        q("delete from channel.listings where sku like %s", (SKU_CH + "%",))
        q("delete from core.products where sku like %s", (SKU_CH + "%",))
        if cuenta_creada:
            q("delete from core.accounts where legacy_code = 'BEKURA'")

    try:
        # ═══ PEDIDOS ═══
        print("O1. Corte ON, kubera arriba (encabezado + líneas)", flush=True)
        assert orders_write.activo()
        encabezado = {"external_order_id": PEDIDO, "canal": "mercado_libre",
                      "cuenta": "BEKURA", "wc_order_id": 90001,
                      "estado_canal": "paid", "estado_wc": "processing",
                      "total": 100.0, "comision": 0.0, "es_fulfillment": False,
                      "skus": ["ZZZ-A", "ZZZ-B"], "creado_at": "2026-08-06 12:00:00"}
        lineas = {**encabezado, "lineas": [
            {"linea": 1, "item_id": "MLM-ZZZ", "sku": "ZZZ-A", "titulo": "Cobaya",
             "cantidad": 2, "precio_unitario": 50.0, "comision": 0.0}]}
        mysql_llamadas.clear()
        orders_write.guardar("services/pedidos_ml.py", "sincronizar",
                             encabezado, lineas, f"BEKURA:{PEDIDO}", lambda: db.execute("INSERT INTO pedidos_ml (stub)"))
        o = q("select total, comision, estado_wc from channel.orders where external_order_id=%s", (PEDIDO,))
        it = q("select cantidad, item_id from channel.order_items where external_order_id=%s", (PEDIDO,))
        check("channel.orders primaria en kubera", bool(o) and float(o[0]["total"]) == 100.0)
        check("channel.order_items primaria (censo con líneas)",
              bool(it) and it[0]["cantidad"] == 2 and it[0]["item_id"] == "MLM-ZZZ")
        check("espejo inverso MySQL corrió",
              any("pedidos_ml" in s for s, _ in mysql_llamadas))

        print("O2. Paridad: total congelado, comisión 0→valor una vez", flush=True)
        e2 = dict(encabezado, total=999.0, comision=55.0, estado_wc="completed")
        orders_write.guardar("services/pedidos_ml.py", "sincronizar",
                             e2, dict(lineas, **{"total": 999.0}), f"BEKURA:{PEDIDO}", lambda: None)
        e3 = dict(encabezado, comision=77.0, estado_wc="cancelled")
        orders_write.guardar("services/pedidos_ml.py", "sincronizar",
                             e3, lineas, f"BEKURA:{PEDIDO}", lambda: None)
        o = q("select total, comision, estado_wc from channel.orders where external_order_id=%s", (PEDIDO,))
        check("total congelado (100, no 999)", float(o[0]["total"]) == 100.0, str(o[0]["total"]))
        check("comisión 0→55 y luego inmutable (no 77)", float(o[0]["comision"]) == 55.0,
              str(o[0]["comision"]))
        check("estados sí se mueven (última escritura gana)",
              o[0]["estado_wc"] == "cancelled", o[0]["estado_wc"])

        print("O3. kubera caída al registrar", flush=True)
        espejados: list[str] = []
        original_espejar = kubera_mirror.espejar
        kubera_mirror.espejar = (lambda origen, funcion, tm, tk, op, payload, clave=None:
                                 espejados.append(tk))
        original_cursor = sdb.get_cursor
        from contextlib import contextmanager

        @contextmanager
        def _roto():
            raise RuntimeError("caos-kubera-caida")
            yield

        sdb.get_cursor = _roto
        mysql_llamadas.clear()
        avisos.clear()
        try:
            orders_write.guardar("services/pedidos_ml.py", "sincronizar",
                                 encabezado, lineas, f"BEKURA:{PEDIDO}",
                                 lambda: db.execute("INSERT INTO pedidos_ml (stub)"))
            exploto = False
        except Exception:  # noqa: BLE001
            exploto = True
        sdb.get_cursor = original_cursor
        kubera_mirror.espejar = original_espejar
        check("el registro NO truena", not exploto)
        check("MySQL aguanta", any("pedidos_ml" in s for s, _ in mysql_llamadas))
        check("los DOS payloads viajan por el espejo clásico",
              espejados == ["channel.orders", "channel.order_items"], str(espejados))
        check("Slack avisó del fallback", "escritura_fallback:orders" in avisos)

        # ═══ CHANNEL ═══
        print("C1. Corte ON, kubera arriba (tanda del sync)", flush=True)
        assert channel_mirror.corte_activo()
        rows = [
            {"sku": SKU_CH, "canal": "mercado_libre", "cuenta": "BEKURA",
             "item_id": "MLM-CH-1", "precio": 150.0, "stock_real": 7,
             "stock_full": 3, "stock_fba": None, "es_full": 1,
             "logistica": "fulfillment", "situacion": "active", "moneda": "MXN"},
            {"sku": "SKU MALO", "canal": "mercado_libre", "cuenta": "BEKURA",
             "item_id": None, "precio": None, "stock_real": None, "stock_full": None,
             "stock_fba": None, "es_full": 0, "logistica": None, "situacion": None,
             "moneda": "MXN"},
        ]
        mysql_llamadas.clear()
        n = inventario._upsert(rows)
        li = q("select price, stock_own, stock_full, situacion from channel.listings "
               "where sku = %s", (SKU_CH,))
        malo = q("select 1 from channel.listings where sku = %s", ("SKU MALO",))
        check("tanda escrita (2 filas reportadas)", n == 2)
        check("channel.listings primaria en kubera",
              bool(li) and float(li[0]["price"]) == 150.0 and li[0]["stock_own"] == 7
              and li[0]["stock_full"] == 3 and li[0]["situacion"] == "active")
        check("SKU inválido descartado (regla del espejo)", not malo)
        check("espejo inverso MySQL corrió (executemany canal_inventario)",
              any("canal_inventario" in s for s, _ in mysql_llamadas))

        print("C2. kubera caída en la tanda", flush=True)
        sdb.get_cursor = _roto
        mysql_llamadas.clear()
        avisos.clear()
        n2 = None
        try:
            n2 = inventario._upsert([dict(rows[0], precio=222.0)])
            exploto = False
        except Exception:  # noqa: BLE001
            exploto = True
        sdb.get_cursor = original_cursor
        check("el sync NO truena", not exploto and n2 == 1)
        check("MySQL aguanta la tanda",
              any("canal_inventario" in s for s, _ in mysql_llamadas))
        check("Slack avisó del fallback", "escritura_fallback:channel" in avisos)
        li = q("select price from channel.listings where sku = %s", (SKU_CH,))
        check("kubera quedó con el valor previo (se auto-sana al ciclo)",
              float(li[0]["price"]) == 150.0)

        print("C3. Flag OFF = mundo viejo", flush=True)
        settings.supabase_write_channel = False
        mysql_llamadas.clear()
        inventario._upsert([dict(rows[0], precio=333.0)])
        li = q("select price from channel.listings where sku = %s", (SKU_CH,))
        check("MySQL primario de nuevo",
              any("canal_inventario" in s for s, _ in mysql_llamadas))
        check("kubera NO se tocó (dual_write_channel apagado)",
              float(li[0]["price"]) == 150.0)
        settings.supabase_write_channel = True
    finally:
        limpiar()
        print("\n(cobayas limpiadas del sandbox)", flush=True)

    fallas = [r for r in resultados if not r["paso"]]
    print(f"\nRESULTADO: {len(resultados) - len(fallas)}/{len(resultados)} PASAN", flush=True)
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
