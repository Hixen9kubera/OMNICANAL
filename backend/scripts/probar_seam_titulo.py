"""
probar_seam_titulo.py — ¿Por qué el ETL encontró `name` distinto en un SKU que
SÍ pasó por el seam? (caso OFI-0079-BLN, 10-ago-2026)

PARTE A (producción, SOLO LECTURA): compara el título que devuelve la REST de
  WooCommerce contra `post_title` en la BD de WordPress. Si difieren, el seam
  no puede confiar en NINGUNO de los dos sin decidir cuál manda.
PARTE B (SANDBOX): reproduce el desfase. El seam de nacimiento registra el
  título de la variable local (`titulo`) en vez del que Woo devolvió en
  `wc_prod` — asimetría con el SKU, que sí sale de la respuesta de Woo
  (crear_producto.py:993). Se prueban las dos variantes y se simula el diff
  del ETL auditor sobre CAMPOS_SEAM.

NO escribe nada en WooCommerce ni en la BD kubera de producción.
Uso: backend/.venv/Scripts/python.exe backend/scripts/probar_seam_titulo.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))

REF_KUBERA_PROD = "tukwcvsi"
SKU = "ZZZ-SEAM-TITULO"
WC = 990777
# Los dos textos reales del caso del 10-ago.
TITULO_PANEL = "Escritorio gamer en L con luz led y cajones oficina"
TITULO_WOO = "Escritorio Gamer en L con Luz LED RGB y Cajones para Oficina"
CAMPOS_SEAM = ("name", "wc_id", "status")

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


P = env(".env")
S = env("env.staging")


def guardia_sandbox(url: str) -> str:
    m = re.search(r"postgres\.([a-z0-9]+):", url or "")
    ref = m.group(1) if m else ""
    if not ref:
        sys.exit("ABORT: no pude extraer la ref del destino.")
    if ref.startswith(REF_KUBERA_PROD) or ref == S.get("SUPABASE_PROD_REF", "").strip():
        sys.exit("ABORT: el destino NO es el sandbox. Aborto.")
    return ref


# ═══════════════ PARTE A — Woo real, solo lectura ═══════════════
def parte_a() -> None:
    print("\nPARTE A · ¿REST y BD de WordPress dicen el mismo título? (solo lectura)",
          flush=True)
    base = P["WC_URL"].rstrip("/")
    prefijo = P.get("WPDB_PREFIX", "wp_")
    cn = pymysql.connect(host=P["DB_HOST"], port=int(P.get("DB_PORT", 3306)),
                         user=P["WPDB_USER"], password=P["WPDB_PASSWORD"],
                         database=P["WPDB_NAME"], connect_timeout=20)
    for wc_id, sku in ((9393, "OFI-0079-BLN"), (7499, "TEC-0324-MUL")):
        with cn.cursor() as c:
            c.execute(f"select post_title from {prefijo}posts where ID = %s", (wc_id,))
            fila = c.fetchone()
        en_bd = fila[0] if fila else None
        r = httpx.get(f"{base}/wp-json/wc/v3/products/{wc_id}",
                      auth=(P["WC_KEY"], P["WC_SECRET"]),
                      params={"_fields": "id,name,sku", "_cb": "seamtest"}, timeout=45.0)
        en_rest = r.json().get("name") if r.status_code == 200 else f"HTTP {r.status_code}"
        print(f"  {sku} (wc {wc_id})")
        print(f"     BD   : {en_bd}")
        print(f"     REST : {en_rest}")
        check(f"{sku}: REST y BD coinciden", en_bd == en_rest,
              "" if en_bd == en_rest else "la REST reporta un título distinto al de la BD")
    cn.close()


# ═══════════════ PARTE B — el seam, contra el sandbox ═══════════════
def parte_b() -> None:
    ref = guardia_sandbox(S["SUPABASE_DB_URL"])
    print(f"\nPARTE B · el seam de nacimiento contra el sandbox {ref[:8]}…", flush=True)
    os.environ["SUPABASE_DB_URL"] = S["SUPABASE_DB_URL"]
    os.environ["SUPABASE_WRITE_CORE"] = "true"
    os.environ["KUBERA_MIRROR_ENABLED"] = "false"

    from services import alertas, core_write, db
    from services import supabase_db as sdb

    db.execute = lambda sql, params=None: None
    db.fetch_one = lambda sql, params=None: None
    db.fetch_all = lambda sql, params=None: []
    alertas.avisar = lambda tipo, texto, nivel="🔴": True

    def leer() -> dict | None:
        with sdb.get_cursor() as cur:
            cur.execute("select sku, name, wc_id, status from core.products where sku = %s",
                        (SKU,))
            f = cur.fetchone()
            return dict(f) if f and isinstance(f, dict) else (
                {"sku": f[0], "name": f[1], "wc_id": f[2], "status": f[3]} if f else None)

    def limpiar() -> None:
        with sdb.get_cursor() as cur:
            cur.execute("delete from core.products where sku = %s", (SKU,))

    # Lo que _actualizar_wc devolvió: el producto TAL COMO QUEDÓ en la tienda.
    wc_prod = {"sku": SKU, "name": TITULO_WOO}
    woo_real = {"name": TITULO_WOO, "wc_id": WC, "status": "pending"}

    limpiar()
    try:
        assert core_write.activo(), "el flag SUPABASE_WRITE_CORE no quedó encendido"

        print("\n  B1. Código de HOY: el seam manda la variable local `titulo`", flush=True)
        core_write.registrar("services/crear_producto.py", "crear (nacimiento)",
                             {"sku": SKU, "name": TITULO_PANEL, "wc_id": WC,
                              "status": "pending", "source": "panel_crear"}, clave=SKU)
        fila = leer() or {}
        check("el registro civil guarda el título que el panel QUISO poner",
              fila.get("name") == TITULO_PANEL, str(fila.get("name"))[:60])
        difs = [c for c in CAMPOS_SEAM if (fila.get(c) or None) != (woo_real.get(c) or None)]
        check("el ETL auditor encontraría hueco (seam_gap=1 por 'name')",
              difs == ["name"], f"difs={difs}")

        print("\n  B2. Con el arreglo: el seam manda lo que Woo DEVOLVIÓ", flush=True)
        limpiar()
        name_real = (wc_prod or {}).get("name") or TITULO_PANEL
        core_write.registrar("services/crear_producto.py", "crear (nacimiento)",
                             {"sku": SKU, "name": name_real, "wc_id": WC,
                              "status": "pending", "source": "panel_crear"}, clave=SKU)
        fila = leer() or {}
        check("el registro civil guarda el título REAL de la tienda",
              fila.get("name") == TITULO_WOO, str(fila.get("name"))[:60])
        difs = [c for c in CAMPOS_SEAM if (fila.get(c) or None) != (woo_real.get(c) or None)]
        check("el ETL auditor NO encontraría hueco (seam_gap=0)", difs == [], f"difs={difs}")
    finally:
        limpiar()
        print("\n  (cobaya limpiada del sandbox)", flush=True)


if __name__ == "__main__":
    parte_a()
    parte_b()
    fallas = [r for r in resultados if not r[1]]
    print(f"\nRESULTADO: {len(resultados) - len(fallas)}/{len(resultados)} PASAN", flush=True)
    sys.exit(1 if fallas else 0)
