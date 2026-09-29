"""
probar_cajon_amazon_sandbox.py — La tarjeta de Amazon del cajón contra el SANDBOX.

QUÉ TOCA
--------
  · Base: SOLO LECTURA del sandbox (aborta si el DSN es el de producción).
    MySQL apagado: la bitácora del publicador no contesta, que es justo el caso
    en que la tarjeta debe salir igual, con el ASIN de kubera.
  · WooCommerce: simulado (el env.staging trae el Woo de PRODUCCIÓN; aquí no
    hace falta ni leerlo).

Uso (desde la raíz, con env.staging ahí):
  python backend/scripts/probar_cajon_amazon_sandbox.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.update({"APP_ENV": "staging", "MYSQL_ENABLED": "false",
                   "SUPABASE_READ_CHANNEL": "true", "SUPABASE_READ_PUBLICACIONES": "true"})

from config import settings  # noqa: E402

_ref = (settings.supabase_db_url or "").split("postgres.")[-1].split(":")[0]
if not settings.supabase_db_url:
    sys.exit("ABORT: env.staging sin SUPABASE_DB_URL.")
if _ref[:8] == "tukwcvsi":
    sys.exit("ABORT: esto apunta a PRODUCCION.")
if settings.mysql_enabled:
    sys.exit("ABORT: MYSQL_ENABLED quedo encendido.")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import productos as ruta_prod  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

_ok = True


def check(nombre: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f" — {detalle}" if detalle else ""))


async def _woo(sku):
    return {"sku": sku, "nombre": sku}


app = FastAPI()
app.include_router(ruta_prod.router)
cli = TestClient(app)

print(f"Sandbox {_ref[:8]}… · MySQL apagado · Woo simulado\n")
for sku in ("CAM-0030-MAT", "CAM-0030", "SIL-0013-BLN"):
    fila = sdb.fetch_one("select listing_id, situacion from channel.listings "
                         "where canal='amazon' and sku=%s", (sku,))
    with mock.patch.object(ruta_prod.woocommerce, "obtener_producto_por_sku", _woo):
        r = cli.get(f"/api/productos/{sku}")
    tarjeta = next((c for c in (r.json().get("canales") or []) if c["canal"] == "amazon"), None) \
        if r.status_code == 200 else None
    print(f"{sku}: kubera {dict(fila) if fila else '—'}")
    print(f"     tarjeta {({k: tarjeta[k] for k in ('item_id', 'url', 'precio', 'stock_real', 'situacion', 'estado')} if tarjeta else None)}")
    check("el cajón contesta aunque MySQL no", r.status_code == 200, f"HTTP {r.status_code}")
    if fila and fila["listing_id"]:
        check("la tarjeta lleva el ASIN de kubera y su enlace",
              bool(tarjeta) and tarjeta["item_id"] == fila["listing_id"]
              and tarjeta["url"] == f"https://www.amazon.com.mx/dp/{fila['listing_id']}")

print("\nRESULTADO:", "TODO PASA" if _ok else "HAY FALLAS")
sys.exit(0 if _ok else 1)
