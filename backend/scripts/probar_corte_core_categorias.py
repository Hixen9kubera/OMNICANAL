"""
probar_corte_core_categorias.py — Pruebas de los CORTES F6 de CORE y
CATEGORÍAS contra el SANDBOX. MySQL/Slack stubeados; guardia triple de ref.

CORE (core_write.registrar — los 3 seams llaman esto):
  N1. Nacimiento → fila en core.products (primaria síncrona).
  N2. Publish por wc_id → status se mueve, el sku del acta no se pisa.
  N3. Candado solo_por_wc_id: wc_id viejo (SKU reciclado) → NO escribe nada.
  N4. kubera caída → no explota + evento a la cola del espejo + Slack.
  N5. Flag OFF → delega en el espejo encolado (comportamiento v0.65).
CATEGORÍAS (categorias_write.registrar — seam del guardado del panel):
  C1. Elección con acta → árbol + asignación source='panel'.
  C2. Re-elección → la asignación se mueve a la categoría nueva.
  C3. wc_id sin acta → el evento queda en espejo_kubera_log (reprocesable).
  R1. Reproceso: el payload encolado en C3 se aplica cuando el acta existe.
  C4. kubera caída → no explota + cola.
  C5. Flag OFF → delega en espejar (que el censo de producción filtra).

Cobayas: ZZZ-CORTE-CORE / MLM999999-MLM888888; se limpian al final.
Uso: backend/.venv/Scripts/python.exe backend/scripts/probar_corte_core_categorias.py
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
socket.setdefaulttimeout(30)

REF_KUBERA_PROD = "tukwcvsi"
SKU = "ZZZ-CORTE-CORE"
WC = 990001


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
    print(f"CORTES F6 CORE+CATEGORÍAS contra sandbox {ref[:8]}…\n", flush=True)

    os.environ["SUPABASE_DB_URL"] = S["SUPABASE_DB_URL"]
    os.environ["SUPABASE_WRITE_CORE"] = "true"
    os.environ["SUPABASE_WRITE_CATEGORIAS"] = "true"
    os.environ["KUBERA_MIRROR_ENABLED"] = "true"
    os.environ["KUBERA_DB_URL"] = S["SUPABASE_DB_URL"]
    os.environ["KUBERA_MIRROR_TABLAS"] = "wp_posts"

    from config import settings
    from services import alertas, categorias_write, core_write, db, kubera_mirror
    from services import supabase_db as sdb

    mysql_llamadas: list[tuple[str, object]] = []
    db.execute = lambda sql, params=None: mysql_llamadas.append(
        (" ".join(sql.split())[:200], params))
    db.fetch_one = lambda sql, params=None: None
    db.fetch_all = lambda sql, params=None: []
    avisos: list[str] = []
    alertas.avisar = lambda tipo, texto, nivel="🔴": avisos.append(tipo) or True
    espejados: list[tuple[str, dict]] = []
    espejar_real = kubera_mirror.espejar

    def limpiar():
        q("delete from channel.product_category where sku = %s", (SKU,))
        q("delete from channel.categories where category_id in ('MLM999999','MLM888888')")
        q("delete from core.products where sku = %s", (SKU,))

    limpiar()
    try:
        # ═══ CORE ═══
        print("N1-N3. Ciclo de vida primario", flush=True)
        assert core_write.activo()
        core_write.registrar("t", "nacimiento", {"sku": SKU, "name": "Cobaya",
                             "wc_id": WC, "status": "draft", "source": "panel_crear"}, SKU)
        f = q("select name, status, wc_id from core.products where sku=%s", (SKU,))
        check("nacimiento en core.products", bool(f) and f[0]["status"] == "draft")
        core_write.registrar("t", "publish", {"sku": SKU, "wc_id": WC,
                             "status": "publish"}, SKU)
        f = q("select status from core.products where sku=%s", (SKU,))
        check("publish por wc_id", bool(f) and f[0]["status"] == "publish")
        core_write.registrar("t", "auditoria", {"sku": SKU, "wc_id": 880001,
                             "status": "deleted", "solo_por_wc_id": True}, SKU)
        f = q("select status from core.products where sku=%s", (SKU,))
        check("candado solo_por_wc_id (wc_id viejo NO pinta deleted)",
              bool(f) and f[0]["status"] == "publish", f[0]["status"])

        print("N4-N5. Caída y flag OFF", flush=True)
        kubera_mirror.espejar = (lambda o, fn, tm, tk, op, p, clave=None:
                                 espejados.append((tk, p)))
        original_cursor = sdb.get_cursor

        @contextmanager
        def _roto():
            raise RuntimeError("caos-kubera-caida")
            yield

        sdb.get_cursor = _roto
        avisos.clear()
        try:
            core_write.registrar("t", "nacimiento", {"sku": SKU, "wc_id": WC,
                                 "status": "draft"}, SKU)
            exploto = False
        except Exception:  # noqa: BLE001
            exploto = True
        sdb.get_cursor = original_cursor
        check("kubera caída: no explota + evento a la cola + Slack",
              not exploto and espejados and espejados[-1][0] == "core.products"
              and "escritura_fallback:core" in avisos)
        settings.supabase_write_core = False
        n_antes = len(espejados)
        core_write.registrar("t", "nacimiento", {"sku": SKU, "wc_id": WC,
                             "status": "draft"}, SKU)
        check("flag OFF delega en el espejo encolado", len(espejados) == n_antes + 1)
        settings.supabase_write_core = True
        kubera_mirror.espejar = espejar_real

        # ═══ CATEGORÍAS ═══
        print("C1-C2. Elección del panel primaria", flush=True)
        assert categorias_write.activo()
        categorias_write.registrar(WC, "MLM999999", "Cobayas",
                                   "Animales > Cobayas")
        arbol = q("select name, path from channel.categories where category_id='MLM999999'")
        asig = q("select category_id, source from channel.product_category where sku=%s", (SKU,))
        check("árbol con nombre y ruta", bool(arbol) and arbol[0]["name"] == "Cobayas")
        check("asignación source='panel'",
              bool(asig) and asig[0]["category_id"] == "MLM999999"
              and asig[0]["source"] == "panel")
        categorias_write.registrar(WC, "MLM888888", "Jaulas", "Animales > Jaulas")
        asig = q("select category_id from channel.product_category where sku=%s", (SKU,))
        check("re-elección mueve la asignación",
              bool(asig) and asig[0]["category_id"] == "MLM888888")

        print("C3+R1. Sin acta → cola → reproceso", flush=True)
        mysql_llamadas.clear()
        avisos.clear()
        try:
            categorias_write.registrar(777777, "MLM999999", "Cobayas", "Animales > Cobayas")
            exploto = False
        except Exception:  # noqa: BLE001
            exploto = True
        encolados = [(s, p) for s, p in mysql_llamadas
                     if "espejo_kubera_log" in s and "INSERT" in s]
        check("sin acta: no explota y el evento queda en la cola",
              not exploto and bool(encolados))
        payload = json.loads(encolados[0][1][-1])
        payload["wc_id"] = WC  # "el acta ya existe": el reproceso debe aplicar
        with sdb.get_cursor() as cur:
            kubera_mirror._UPSERTS["channel.product_category"](cur, payload)
        asig = q("select category_id from channel.product_category where sku=%s", (SKU,))
        check("reproceso aplica el payload encolado",
              bool(asig) and asig[0]["category_id"] == "MLM999999")

        print("C4-C5. Caída y flag OFF", flush=True)
        sdb.get_cursor = _roto
        mysql_llamadas.clear()
        try:
            categorias_write.registrar(WC, "MLM888888", "Jaulas", "Animales > Jaulas")
            exploto = False
        except Exception:  # noqa: BLE001
            exploto = True
        sdb.get_cursor = original_cursor
        check("kubera caída: no explota + cola",
              not exploto and any("espejo_kubera_log" in s for s, _ in mysql_llamadas))
        kubera_mirror.espejar = (lambda o, fn, tm, tk, op, p, clave=None:
                                 espejados.append((tk, p)))
        settings.supabase_write_categorias = False
        n_antes = len(espejados)
        categorias_write.registrar(WC, "MLM888888", "Jaulas", "Animales > Jaulas")
        check("flag OFF delega en espejar (censo decide)",
              len(espejados) == n_antes + 1
              and espejados[-1][0] == "channel.product_category")
        settings.supabase_write_categorias = True
        kubera_mirror.espejar = espejar_real
    finally:
        limpiar()
        print("\n(cobayas limpiadas del sandbox)", flush=True)

    fallas = [r for r in resultados if not r["paso"]]
    print(f"\nRESULTADO: {len(resultados) - len(fallas)}/{len(resultados)} PASAN", flush=True)
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
