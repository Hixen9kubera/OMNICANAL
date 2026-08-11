"""
probar_webhook_woo.py — POST /api/webhooks/woo contra el SANDBOX.

El seam que faltaba: los tres avisadores de core solo ven lo que pasa por el
panel, y el catálogo también se edita en wp-admin (barrido 11-ago: 444 fichas
tocadas ahí, 39 de 253 SKUs con un título distinto al que dejó el panel).
Este webhook escucha en la fuente.

  F1. Ping de alta de Woo ({"webhook_id"}) → 200 sin tocar nada.
  F2. Firma INVÁLIDA → 200 pero NO escribe (nadie entra sin credencial).
  F3. Sin secreto configurado → observación: registra, no escribe.
  F4. Flag apagado → no escribe.
  F5. Firma válida + product.updated → core.products queda con name y status.
  F6. El MISMO evento otra vez → no repite el viaje a kubera (caché por SKU).
  F7. Título cambiado "desde wp-admin" → el registro civil lo sigue en vivo,
      que es justo lo que el ETL auditor tuvo que corregir el 11-ago.
  F8. Ficha sin sku → ignorada.
  F9. kubera caída → 200 igual (Woo no debe deshabilitar el webhook).

Cobaya: ZZZ-WEBHOOK-WOO (wc 990888); se limpia al final.
Uso: backend/.venv/Scripts/python.exe backend/scripts/probar_webhook_woo.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # consola cp1252

REF_KUBERA_PROD = "tukwcvsi"
SKU = "ZZZ-WEBHOOK-WOO"
WC = 990888
SECRETO = "secreto-de-prueba-no-es-el-de-produccion"

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
os.environ["SUPABASE_WRITE_CORE"] = "true"
os.environ["WOO_WEBHOOK_ENABLED"] = "true"
os.environ["WOO_WEBHOOK_SECRET"] = SECRETO
os.environ["KUBERA_MIRROR_ENABLED"] = "false"
os.environ["WEBHOOK_REGISTRO"] = "false"

from fastapi import FastAPI                                    # noqa: E402
from fastapi.testclient import TestClient                      # noqa: E402

from config import settings                                    # noqa: E402
from services import alertas, db                               # noqa: E402
from services import supabase_db as sdb                        # noqa: E402

db.execute = lambda sql, params=None: None
db.fetch_one = lambda sql, params=None: None
db.fetch_all = lambda sql, params=None: []
alertas.avisar = lambda tipo, texto, nivel="🔴": True

from routers import webhooks                                   # noqa: E402

app = FastAPI()
app.include_router(webhooks.router)
cli = TestClient(app)


def firmar(cuerpo: bytes, secreto: str = SECRETO) -> str:
    return base64.b64encode(hmac.new(secreto.encode(), cuerpo, hashlib.sha256).digest()).decode()


def mandar(payload: dict, topic: str = "product.updated", secreto: str | None = SECRETO):
    cuerpo = json.dumps(payload).encode()
    cab = {"Content-Type": "application/json"}
    if topic:
        cab["X-WC-Webhook-Topic"] = topic
    if secreto is not None:
        cab["X-WC-Webhook-Signature"] = firmar(cuerpo, secreto)
    return cli.post("/api/webhooks/woo", content=cuerpo, headers=cab)


def leer():
    with sdb.get_cursor() as cur:
        cur.execute("select sku, name, status, wc_id from core.products where sku = %s", (SKU,))
        f = cur.fetchone()
        if not f:
            return None
        return dict(f) if isinstance(f, dict) else {
            "sku": f[0], "name": f[1], "status": f[2], "wc_id": f[3]}


def limpiar():
    with sdb.get_cursor() as cur:
        cur.execute("delete from core.products where sku = %s", (SKU,))


FICHA = {"id": WC, "sku": SKU, "name": "Cobaya del webhook", "status": "publish"}


def main() -> None:
    print(f"WEBHOOK DE WOO contra el sandbox {REF[:8]}…\n", flush=True)
    limpiar()
    webhooks._WOO_ULTIMO.clear()
    try:
        print("F1-F4. Nadie entra sin credencial", flush=True)
        r = mandar({"webhook_id": 7}, topic="")
        check("ping de alta → 200 sin tocar nada",
              r.status_code == 200 and r.json().get("ping") is True and leer() is None)

        r = mandar(FICHA, secreto="otro-secreto")
        check("firma inválida → 200 pero NO escribe",
              r.status_code == 200 and leer() is None)

        settings.woo_webhook_secret = ""
        r = mandar(FICHA, secreto=None)
        check("sin secreto → observación, no escribe",
              r.status_code == 200 and leer() is None)
        settings.woo_webhook_secret = SECRETO

        settings.woo_webhook_enabled = False
        r = mandar(FICHA)
        check("flag apagado → no escribe", r.status_code == 200 and leer() is None)
        settings.woo_webhook_enabled = True

        print("\nF5-F8. El seam trabajando", flush=True)
        r = mandar(FICHA)
        fila = leer() or {}
        check("product.updated → el registro civil nace con name y status",
              r.status_code == 200 and fila.get("name") == "Cobaya del webhook"
              and fila.get("status") == "publish", str(fila.get("name")))

        n_antes = sum(1 for e in webhooks._WOO_LOG if e.get("aplicado"))
        mandar(FICHA)
        n_despues = sum(1 for e in webhooks._WOO_LOG if e.get("aplicado"))
        check("evento repetido idéntico → no repite el viaje a kubera",
              n_despues == n_antes,
              (webhooks._WOO_LOG[-1] or {}).get("motivo") or "")

        editado = {**FICHA, "name": "Título corregido a mano en wp-admin",
                   "status": "pending"}
        mandar(editado)
        fila = leer() or {}
        check("edición fuera del panel → el registro civil la sigue EN VIVO",
              fila.get("name") == "Título corregido a mano en wp-admin"
              and fila.get("status") == "pending", str(fila.get("name"))[:50])

        r = mandar({"id": 990999, "name": "sin sku", "status": "draft"})
        check("ficha sin sku → ignorada",
              r.status_code == 200
              and (webhooks._WOO_LOG[-1] or {}).get("motivo") == "ficha sin sku o sin id")

        print("\nF9. kubera caída", flush=True)
        original = sdb.get_cursor

        @contextmanager
        def _roto():
            raise RuntimeError("caos-kubera-caida")
            yield

        sdb.get_cursor = _roto
        webhooks._WOO_ULTIMO.clear()
        r = mandar({**FICHA, "name": "otro título"})
        sdb.get_cursor = original
        check("kubera caída → 200 igual (Woo no deshabilita la suscripción)",
              r.status_code == 200 and r.json().get("ok") is True)
    finally:
        try:
            limpiar()
        except Exception:  # noqa: BLE001
            pass
        print("\n(cobaya limpiada del sandbox)", flush=True)

    fallas = [r for r in resultados if not r[1]]
    print(f"\nRESULTADO: {len(resultados) - len(fallas)}/{len(resultados)} PASAN", flush=True)
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
