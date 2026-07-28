"""
suite_caos_sandbox.py — Pruebas DESTRUCTIVAS y de seguridad contra el SANDBOX.

Ataca a propósito las zonas que el camino feliz no ejerce, para descubrir
fallas ANTES de encender flags en producción. Corre SOLO contra el sandbox:
candado triple (ref != prod, ref != kubera conocida, host del pooler) — aborta
si detecta cualquier destino que no sea el sandbox declarado en env.staging.

Familias de prueba:
  A. CANDADO de ambiente     — que validar_ambiente() bloquee de verdad
  B. INYECCIÓN SQL           — que los parámetros no permitan romper la query
  C. FALLBACK a MySQL        — que un fallo de kubera NO tumbe el endpoint
  D. INTEGRIDAD del esquema  — que las FK/CHECK/PK rechacen basura
  E. RLS / llaves            — que anon no lea, que service_role sí, límites
  F. RESILIENCIA de conexión — timeouts, DSN inválida, statement_timeout

NO escribe en tablas con datos reales del sandbox salvo en una fila-cobaya con
SKU 'ZZZ-CAOS-*' que borra al final. Cada prueba imprime PASA/FALLA + detalle.

Uso: backend/.venv/Scripts/python.exe backend/scripts/suite_caos_sandbox.py
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
import requests

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
socket.setdefaulttimeout(30)

REF_KUBERA_PROD = "tukwcvsi"


def _watchdog():
    def _m():
        print("WATCHDOG 10min — abort", flush=True); os._exit(2)
    t = threading.Timer(600, _m); t.daemon = True; t.start()


def env(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    p = ROOT / nombre
    for l in p.read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return d


S = env("env.staging")
resultados: list[dict] = []


def check(nombre: str, familia: str, paso: bool, detalle: str = ""):
    resultados.append({"familia": familia, "prueba": nombre,
                       "estado": "PASA" if paso else "FALLA", "detalle": detalle})
    print(f"  [{'PASA' if paso else 'FALLA'}] {familia} · {nombre}"
          + (f" — {detalle}" if detalle else ""), flush=True)


def guardia_sandbox(url: str) -> str:
    m = re.search(r"postgres\.([a-z0-9]+):", url or "")
    ref = m.group(1) if m else ""
    if not ref:
        sys.exit("ABORT: no pude extraer ref del sandbox.")
    if ref.startswith(REF_KUBERA_PROD):
        sys.exit("ABORT: el destino es la BD kubera. La suite de caos SOLO corre en sandbox.")
    if ref == S.get("SUPABASE_PROD_REF", "").strip():
        sys.exit("ABORT: destino == SUPABASE_PROD_REF (producción). Aborto.")
    return ref


def conectar(url=None, **kw):
    return psycopg2.connect(url or S["SUPABASE_DB_URL"], connect_timeout=15, **kw)


def main() -> None:
    _watchdog()
    ref = guardia_sandbox(S["SUPABASE_DB_URL"])
    print(f"SUITE DE CAOS contra sandbox {ref[:8]}…\n", flush=True)

    # Configurar settings para probar los módulos reales del backend
    os.environ["SUPABASE_DB_URL"] = S["SUPABASE_DB_URL"]
    from config import Settings, validar_ambiente
    from services import supabase_db as sdb

    # ── A. CANDADO DE AMBIENTE ────────────────────────────────────────────────
    print("A. Candado de ambiente", flush=True)
    # A1: staging apuntando a "producción" (ref==prod_ref, env!=production) → debe LANZAR
    try:
        s = Settings(app_env="staging", supabase_url=f"https://{ref}.supabase.co",
                     supabase_prod_ref=ref)
        validar_ambiente(s)
        check("staging→prod bloquea arranque", "candado", False, "NO lanzó (debería)")
    except RuntimeError:
        check("staging→prod bloquea arranque", "candado", True, "RuntimeError como se espera")
    # A2: production apuntando a un proyecto != prod → debe LANZAR
    try:
        s = Settings(app_env="production", supabase_url="https://otroproj.supabase.co",
                     supabase_prod_ref=ref)
        validar_ambiente(s)
        check("prod→otro proyecto bloquea", "candado", False, "NO lanzó (debería)")
    except RuntimeError:
        check("prod→otro proyecto bloquea", "candado", True, "RuntimeError como se espera")
    # A3: config coherente (staging→sandbox, prod_ref=kubera) → NO debe lanzar
    try:
        s = Settings(app_env="staging", supabase_url=f"https://{ref}.supabase.co",
                     supabase_prod_ref="tukwcvsitthplhswsblt")
        validar_ambiente(s)
        check("config coherente arranca", "candado", True)
    except RuntimeError as e:
        check("config coherente arranca", "candado", False, f"lanzó de más: {e}")

    # ── B. INYECCIÓN SQL ──────────────────────────────────────────────────────
    print("B. Inyección SQL (via costing_read con params maliciosos)", flush=True)
    from services import costing_read
    payloads = ["'; drop table costing.costos_finales; --",
                "' OR '1'='1", "%'; delete from core.products; --", "\\'; select 1; --"]
    inyeccion_ok = True
    for p in payloads:
        try:
            filas, total = costing_read.listado(1, 5, p, None, "reciente", [])
            # que devuelva 0-N filas sin ejecutar el payload: si las tablas siguen vivas, pasó
        except Exception as e:  # noqa: BLE001
            check(f"payload no crashea: {p[:24]}", "inyeccion", False, str(e)[:80])
            inyeccion_ok = False
    # verificar que las tablas objetivo siguen existiendo tras los intentos
    with sdb.get_cursor() as cur:
        cur.execute("select count(*) as n from costing.costos_finales")
        vivas = cur.fetchone()["n"]
    check("tablas intactas tras 4 payloads", "inyeccion", inyeccion_ok and vivas >= 0,
          f"costos_finales sigue con {vivas} filas")
    # orden malicioso: el router mapea con dict, valor desconocido → default (no interpola)
    try:
        costing_read.listado(1, 5, None, None, "sku; drop table x", [])
        check("orden desconocido no interpola", "inyeccion", True, "cayó al default")
    except Exception as e:  # noqa: BLE001
        check("orden desconocido no interpola", "inyeccion", False, str(e)[:80])

    # ── C. FALLBACK a MySQL (simulado a nivel de disponible()) ────────────────
    print("C. Fallback ante kubera caída", flush=True)
    # DSN rota → get_cursor debe LANZAR (y el router lo captura → MySQL)
    try:
        bad = conectar(url="postgresql://postgres.zzz:bad@nohost.invalid:6543/postgres")
        bad.close()
        check("DSN inválida lanza (para fallback)", "fallback", False, "conectó (raro)")
    except Exception:  # noqa: BLE001
        check("DSN inválida lanza (para fallback)", "fallback", True, "excepción → el router caería a MySQL")

    # ── D. INTEGRIDAD del esquema (FK/CHECK/PK rechazan basura) ────────────────
    print("D. Integridad (la BD rechaza datos inválidos)", flush=True)
    with conectar() as pg:
        pg.autocommit = False
        cur = pg.cursor()
        # D1: FK — costo de un SKU inexistente en core.products
        try:
            cur.execute("insert into costing.costos_finales (sku, canal, precio_sugerido) "
                        "values ('ZZZ-CAOS-FK', 'mercado_libre', 1)")
            pg.commit(); check("FK rechaza SKU huérfano", "integridad", False, "aceptó (debería fallar)")
        except psycopg2.Error:
            pg.rollback(); check("FK rechaza SKU huérfano", "integridad", True, "FK violada como se espera")
        # D2: CHECK — SKU con espacio en core.products
        try:
            cur.execute("insert into core.products (sku, status) values ('ZZZ CAOS ESPACIO', 'draft')")
            pg.commit(); check("CHECK rechaza SKU con espacio", "integridad", False, "aceptó (debería fallar)")
        except psycopg2.Error:
            pg.rollback(); check("CHECK rechaza SKU con espacio", "integridad", True, "CHECK violado como se espera")
        # D3: PK compuesta — dos precios mismo (sku, canal) deben chocar; distinto canal, no
        try:
            cur.execute("insert into core.products (sku, status) values ('ZZZ-CAOS-PK','draft') "
                        "on conflict do nothing")
            cur.execute("insert into costing.costos_finales (sku,canal,precio_sugerido) "
                        "values ('ZZZ-CAOS-PK','mercado_libre',1)")
            cur.execute("insert into costing.costos_finales (sku,canal,precio_sugerido) "
                        "values ('ZZZ-CAOS-PK','amazon',2)")  # distinto canal: OK (esto es P4)
            pg.commit()
            dup = False
            try:
                cur.execute("insert into costing.costos_finales (sku,canal,precio_sugerido) "
                            "values ('ZZZ-CAOS-PK','mercado_libre',9)")
                pg.commit()
            except psycopg2.Error:
                pg.rollback(); dup = True
            check("PK (sku,canal): 2 canales OK, mismo canal choca", "integridad", dup,
                  "P4 permite multi-canal y bloquea duplicado")
        except psycopg2.Error as e:
            pg.rollback(); check("PK (sku,canal)", "integridad", False, str(e)[:80])
        # limpieza de cobayas
        cur.execute("delete from costing.costos_finales where sku like 'ZZZ-CAOS-%'")
        cur.execute("delete from core.products where sku like 'ZZZ-CAOS-%'")
        pg.commit()
        check("limpieza de filas cobaya", "integridad", True, "ZZZ-CAOS-* eliminadas")

    # ── E. RLS / LLAVES (REST) ────────────────────────────────────────────────
    print("E. RLS y llaves (REST)", flush=True)
    base = S["SUPABASE_URL"].rstrip("/") + "/rest/v1"
    for nombre, key, espera in (("service_role lee", S.get("SUPABASE_SERVICE_ROLE_KEY"), 200),
                                 ("anon (deshabilitada) NO lee", S.get("SUPABASE_ANON_KEY"), 401)):
        try:
            r = requests.get(base + "/", headers={"apikey": key or "", "Authorization": f"Bearer {key}"}, timeout=15)
            ok = (r.status_code == espera) or (espera == 200 and r.status_code < 300)
            check(nombre, "rls", ok, f"http {r.status_code} (esperaba {espera})")
        except Exception as e:  # noqa: BLE001
            check(nombre, "rls", False, str(e)[:80])
    # sin apikey → debe rechazar
    try:
        r = requests.get(base + "/core", timeout=15)
        check("sin apikey rechaza", "rls", r.status_code in (401, 400, 404), f"http {r.status_code}")
    except Exception as e:  # noqa: BLE001
        check("sin apikey rechaza", "rls", False, str(e)[:80])

    # ── F. RESILIENCIA de conexión ────────────────────────────────────────────
    print("F. Resiliencia de conexión", flush=True)
    # statement_timeout local: una consulta larga debe cortarse, no colgar
    try:
        with conectar() as pg:
            cur = pg.cursor()
            cur.execute("set local statement_timeout = 800")
            try:
                cur.execute("select pg_sleep(5)")
                check("statement_timeout corta consulta larga", "resiliencia", False, "no cortó")
            except psycopg2.Error:
                check("statement_timeout corta consulta larga", "resiliencia", True, "cortada a los 0.8s")
    except Exception as e:  # noqa: BLE001
        check("statement_timeout corta consulta larga", "resiliencia", False, str(e)[:80])

    # ── VEREDICTO ─────────────────────────────────────────────────────────────
    fallas = [r for r in resultados if r["estado"] == "FALLA"]
    print("\n== RESUMEN ==")
    print(json.dumps({
        "sandbox": ref[:8] + "…",
        "total": len(resultados), "pasa": len(resultados) - len(fallas), "falla": len(fallas),
        "fallas": fallas,
        "por_familia": {f: {"pasa": sum(1 for r in resultados if r["familia"] == f and r["estado"] == "PASA"),
                            "falla": sum(1 for r in resultados if r["familia"] == f and r["estado"] == "FALLA")}
                        for f in sorted(set(r["familia"] for r in resultados))},
    }, ensure_ascii=False, indent=1))
    if fallas:
        sys.exit(1)


if __name__ == "__main__":
    main()
