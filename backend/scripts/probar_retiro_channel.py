"""
probar_retiro_channel.py — Paso 1 del desmantelamiento de CHANNEL, en sandbox.

R1. Flag en true (default) → la tanda va a kubera Y al espejo inverso MySQL.
R2. Flag en false → kubera SÍ recibe; MySQL NO (congelado a propósito).
R3. Flag en false + kubera caída → el respaldo de emergencia SIGUE VIVO:
    MySQL absorbe la tanda igual que siempre (ese camino no se desmantela).

MySQL stubeado (recorder); guardia triple de ref; cobayas limpiadas.
Uso: backend/.venv/Scripts/python.exe backend/scripts/probar_retiro_channel.py
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
SKU = "ZZZ-RETIRO-CHANNEL"

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
os.environ["SUPABASE_WRITE_CHANNEL"] = "true"

from config import settings                       # noqa: E402
from services import channel_mirror               # noqa: E402
from services import supabase_db as sdb           # noqa: E402

FILA = {"sku": SKU, "canal": "mercado_libre", "cuenta": "BEKURA",
        "publicado": 1, "stock_canal": 5, "precio_canal": 100.0,
        "situacion": "active", "item_id_canal": "MLM000TEST"}


def leer():
    with sdb.get_cursor() as cur:
        cur.execute("select sku, status from channel.listings where sku = %s", (SKU,))
        return cur.fetchone()


def limpiar():
    with sdb.get_cursor() as cur:
        cur.execute("delete from channel.listing_history where sku = %s", (SKU,))
        cur.execute("delete from channel.listings where sku = %s", (SKU,))


def esperar_hilo():
    time.sleep(0.6)  # el espejo inverso corre en executor


def main() -> None:
    print(f"RETIRO CHANNEL (paso 1) contra sandbox {REF[:8]}…\n", flush=True)
    mysql_llamadas: list[str] = []
    limpiar()
    try:
        print("R1. Con espejo inverso (default)", flush=True)
        assert settings.channel_espejo_inverso, "el default debe ser true"
        channel_mirror.escribir_primario([dict(FILA)],
                                         lambda: mysql_llamadas.append("tanda"))
        esperar_hilo()
        check("kubera recibe la tanda", bool(leer()))
        check("MySQL recibe el espejo inverso", mysql_llamadas == ["tanda"])

        print("\nR2. Sin espejo inverso (flag en false)", flush=True)
        limpiar()
        mysql_llamadas.clear()
        settings.channel_espejo_inverso = False
        channel_mirror.escribir_primario([dict(FILA)],
                                         lambda: mysql_llamadas.append("tanda"))
        esperar_hilo()
        check("kubera SÍ recibe", bool(leer()))
        check("MySQL queda congelado (cero escrituras)", mysql_llamadas == [])

        print("\nR3. Flag en false + kubera caída → el respaldo sigue vivo", flush=True)
        mysql_llamadas.clear()
        original = sdb.get_cursor

        @contextmanager
        def _roto():
            raise RuntimeError("caos-kubera-caida")
            yield

        sdb.get_cursor = _roto
        try:
            channel_mirror.escribir_primario([dict(FILA)],
                                             lambda: mysql_llamadas.append("tanda"))
            exploto = False
        except Exception:  # noqa: BLE001
            exploto = True
        sdb.get_cursor = original
        check("no explota y MySQL ABSORBE la tanda (emergencia intacta)",
              not exploto and mysql_llamadas == ["tanda"])
    finally:
        settings.channel_espejo_inverso = True
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
