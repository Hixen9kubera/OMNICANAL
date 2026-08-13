"""
probar_corte_costing.py — Pruebas del CORTE F6 de COSTOS (opción A) contra el
SANDBOX de Supabase. MySQL se STUBEA por completo (recorder): este script jamás
toca la base MySQL real ni la BD kubera de producción (guardia triple de ref).

Escenarios:
  T1. Corte ON, kubera arriba  → primaria en costing.* / ops.process_log del
      sandbox + espejo inverso MySQL registrado (thunk) + identidad core.products.
  T2. Lectura bajo el corte    → costo_desde_validados lee del sandbox; si
      kubera truena, cae al stub MySQL y suena la alerta.
  T3. kubera CAÍDA al escribir → el negocio no truena: MySQL registrado +
      evento encolado en espejo_kubera_log (payload reproducible) + Slack.
  T4. Reproceso de la cola     → el handler costing.* re-aplica el payload de
      T3 en el sandbox (el valor encolado gana).
  T5. Flag OFF                 → mundo viejo intacto (solo MySQL; kubera quieta).

Fila cobaya: ZZZ-CORTE-F6 (se limpia al final, también core.products).
Uso: backend/.venv/Scripts/python.exe backend/scripts/probar_corte_costing.py
"""
from __future__ import annotations

import json
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
SKU = "ZZZ-CORTE-F6"


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


def main() -> None:
    _watchdog()
    ref = guardia_sandbox(S["SUPABASE_DB_URL"])
    print(f"CORTE F6 COSTOS contra sandbox {ref[:8]}…\n", flush=True)

    os.environ["SUPABASE_DB_URL"] = S["SUPABASE_DB_URL"]
    os.environ["SUPABASE_WRITE_COSTING"] = "true"
    os.environ["SUPABASE_DUAL_WRITE"] = "false"

    from config import settings
    from services import alertas, costing_write, costos, db, kubera_mirror
    from services import supabase_db as sdb

    # ── Stubs: MySQL recorder + Slack recorder (nada real se toca) ────────────
    mysql_llamadas: list[tuple[str, tuple]] = []
    filas_mysql = {
        "costos_validados": {"sku": SKU, "largo": 10.0, "alto": 5.0, "ancho": 4.0,
                             "peso": 0.5, "costo_producto": 999.0, "costo_cbm": 9.0,
                             "costo_total": 1008.0, "contenedor": "C-STUB"},
    }

    def _execute(sql, params=None):
        mysql_llamadas.append((" ".join(sql.split())[:200], params))

    def _fetch_one(sql, params=None):
        mysql_llamadas.append((" ".join(sql.split())[:200], params))
        if "costos_validados" in sql:
            return dict(filas_mysql["costos_validados"])
        return None

    db.execute = _execute
    db.fetch_one = _fetch_one
    db.fetch_all = lambda sql, params=None: []

    avisos: list[str] = []
    alertas.avisar = lambda tipo, texto, nivel="🔴": avisos.append(tipo) or True

    def limpiar():
        with psycopg2.connect(S["SUPABASE_DB_URL"], connect_timeout=15) as cn:
            with cn.cursor() as c:
                c.execute("delete from ops.process_log where sku = %s", (SKU,))
                c.execute("delete from costing.costos_finales where sku = %s", (SKU,))
                c.execute("delete from costing.costos_validados where sku = %s", (SKU,))
                c.execute("delete from core.products where sku = %s", (SKU,))

    def fila_sandbox(tabla: str):
        with psycopg2.connect(S["SUPABASE_DB_URL"], connect_timeout=15) as cn:
            with cn.cursor() as c:
                c.execute(f"select * from {tabla} where sku = %s", (SKU,))
                cols = [d[0] for d in c.description]
                r = c.fetchone()
                return dict(zip(cols, r)) if r else None

    limpiar()
    try:
        assert costing_write.activo(), "el corte debería estar activo (flag+URL)"

        # ── T1: primaria kubera + espejo inverso MySQL ───────────────────────
        print("T1. Corte ON, kubera arriba", flush=True)
        base = {"largo": 10.0, "alto": 5.0, "ancho": 4.0, "peso": 0.5,
                "costo_producto": 111.0, "costo_cbm": 39.0, "costo_unitario": 150.0}
        pricing = {"costo_comision": 50.0, "costo_fee_envio": 90.0,
                   "precio_sugerido": 500.0, "precio_base": 595.24,
                   "pct_comision": 0.155}
        mysql_llamadas.clear()
        costos._guardar_validados(SKU, base)
        costos._guardar_finales(SKU, base, pricing, "MLM1652")
        costos._log_costo(SKU, "prueba_corte", "sandbox", {"nota": "T1"})

        cv = fila_sandbox("costing.costos_validados")
        cf = fila_sandbox("costing.costos_finales")
        pl = fila_sandbox("ops.process_log")
        cp = fila_sandbox("core.products")
        check("costos_validados primaria en kubera",
              bool(cv) and float(cv["costo_total"]) == 150.0, str(cv and cv["costo_total"]))
        check("costos_finales primaria en kubera (canal ML)",
              bool(cf) and float(cf["precio_sugerido"]) == 500.0
              and cf["canal"] == "mercado_libre")
        check("bitácora primaria en ops.process_log",
              bool(pl) and pl["proceso"] == "costos" and pl["accion"] == "prueba_corte")
        check("identidad core.products asegurada", bool(cp))
        my = [s for s, _ in mysql_llamadas]
        check("espejo inverso MySQL corrió (validados+finales+log)",
              any("INSERT INTO costos_validados" in s for s in my)
              and any("INSERT INTO costos_finales" in s for s in my)
              and any("INSERT INTO costos_logs" in s for s in my))

        # ── T2: lectura bajo el corte ────────────────────────────────────────
        print("T2. Lectura bajo el corte", flush=True)
        r = costos.costo_desde_validados(SKU)
        check("lee la fila del sandbox (no el stub MySQL)",
              bool(r) and r["costo_unitario"] == 150.0, str(r and r["costo_unitario"]))
        from services import costing_read
        original_validados = costing_read.validados
        costing_read.validados = lambda s: (_ for _ in ()).throw(RuntimeError("caos-lectura"))
        avisos.clear()
        # PASO 0 (12-ago-2026): el contrato CAMBIÓ. Antes una lectura fallida
        # caía al espejo MySQL; desde que ese espejo se congela al desmantelar,
        # devolvería un costo viejo sin avisar. Ahora el error SE PROPAGA.
        #
        # Propagar es más seguro que devolver None: con None, `_preparar_base`
        # armaría el costo desde un `cf` también vacío y calcularía sobre CERO
        # — la misma familia de error que dejó 964 pedidos fantasma ese día por
        # confundir "no sé" con "no hay".
        try:
            costos.costo_desde_validados(SKU)
            propago = False
        except RuntimeError:
            propago = True
        finally:
            costing_read.validados = original_validados
        check("una lectura fallida de kubera PROPAGA (ya no cae al espejo)", propago)

        # ── T3: kubera caída al escribir ─────────────────────────────────────
        print("T3. kubera caída al escribir", flush=True)
        original_cursor = sdb.get_cursor
        from contextlib import contextmanager

        @contextmanager
        def _cursor_roto():
            raise RuntimeError("caos-kubera-caida")
            yield  # noqa: unreachable

        sdb.get_cursor = _cursor_roto
        mysql_llamadas.clear()
        avisos.clear()
        base2 = dict(base, costo_producto=222.0, costo_unitario=261.0)
        try:
            costos._guardar_validados(SKU, base2)
            exploto = False
        except Exception:  # noqa: BLE001
            exploto = True
        sdb.get_cursor = original_cursor
        my = [s for s, _ in mysql_llamadas]
        encolados = [(s, p) for s, p in mysql_llamadas if "espejo_kubera_log" in s and "INSERT" in s]
        check("el negocio NO truena", not exploto)
        check("MySQL aguanta la escritura", any("INSERT INTO costos_validados" in s for s in my))
        check("evento encolado en espejo_kubera_log", bool(encolados))
        check("Slack avisó (vía espejo)", "espejo" in avisos)

        # ── T4: reproceso de la cola ─────────────────────────────────────────
        print("T4. Reproceso de la cola", flush=True)
        sql_enc, params_enc = encolados[0]
        payload = json.loads(params_enc[-1])  # payload_json es el último VALUES
        tabla_destino = params_enc[3]
        handler = kubera_mirror._UPSERTS.get(tabla_destino)
        check("handler registrado para la tabla encolada", handler is not None, tabla_destino)
        with sdb.get_cursor() as cur:
            handler(cur, payload)
        cv2 = fila_sandbox("costing.costos_validados")
        check("el payload encolado se re-aplica (222 gana)",
              bool(cv2) and float(cv2["costo_producto"]) == 222.0,
              str(cv2 and cv2["costo_producto"]))

        # ── T5: flag OFF = mundo viejo ───────────────────────────────────────
        print("T5. Flag OFF", flush=True)
        settings.supabase_write_costing = False
        mysql_llamadas.clear()
        costos._guardar_validados(SKU, dict(base, costo_producto=333.0, costo_unitario=372.0))
        cv3 = fila_sandbox("costing.costos_validados")
        my = [s for s, _ in mysql_llamadas]
        check("MySQL primario de nuevo", any("INSERT INTO costos_validados" in s for s in my))
        check("kubera NO se tocó (dual_write apagado)",
              bool(cv3) and float(cv3["costo_producto"]) == 222.0)
        settings.supabase_write_costing = True
    finally:
        limpiar()
        print("\n(fila cobaya limpiada del sandbox)", flush=True)

    fallas = [r for r in resultados if not r["paso"]]
    print(f"\nRESULTADO: {len(resultados) - len(fallas)}/{len(resultados)} PASAN", flush=True)
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
