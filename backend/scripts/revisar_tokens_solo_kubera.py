"""
revisar_tokens_solo_kubera.py — La revisión ANTES de encender TOKENS_SOLO_KUBERA.

Con el flag, la app y la clave para renovar salen del entorno (por cuenta o las
globales). Si alguna no es la que emitió el token de esa cuenta, nadie se entera
hasta que el token vence (~6 h), y entonces la cuenta se queda sin API: sin
ventas por webhook, sin stock, sin publicar. Esto lo dice ANTES.

Por cada cuenta de `ops.ml_tokens`:
  · qué app emitió su token y qué app da el entorno (y de dónde: por cuenta o
    global) — tienen que coincidir;
  · si hay MySQL a la mano, si la clave del entorno es LA MISMA que hoy renueva
    bien desde `ml_tokens_dashboard`. Se comparan huellas SHA-256 cortas: jamás
    se imprime una clave ni un token.

SOLO LEE. Sale con 0 si todo coincide y con 1 si algo no.

Uso: con las variables del servicio en el entorno del proceso.
  python backend/scripts/revisar_tokens_solo_kubera.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from services import db, meli, tokens_read  # noqa: E402


def claves_dashboard() -> dict[str, tuple[str, str]]:
    """{cuenta: (app, huella de la clave)} de lo que HOY renueva bien. Vacío sin MySQL."""
    f = meli._fernet()
    try:
        filas = db.fetch_all("SELECT cuenta, app_id, client_secret FROM ml_tokens_dashboard")
    except Exception as exc:  # noqa: BLE001
        print(f"  (sin MySQL: no se compara la clave — {type(exc).__name__})")
        return {}
    return {r["cuenta"].upper(): (meli._dec(f, r["app_id"]),
                                  tokens_read.huella(meli._dec(f, r["client_secret"])))
            for r in filas if r.get("client_secret")}


def main() -> int:
    filas = meli.revisar_apps_kubera()
    if not filas:
        print("ops.ml_tokens está vacía: no hay nada que renovar.")
        return 1
    dash = claves_dashboard()
    todo_bien = True
    print(f"{'cuenta':15} {'app del token':18} {'app del entorno':18} {'fuente':8} "
          f"{'clave = la que hoy sirve':25} edad")
    for r in filas:
        clave_ok = "—"
        app = meli._app_de_cuenta(r["cuenta"])
        if dash.get(r["cuenta"]) and app:
            app_dash, h_dash = dash[r["cuenta"]]
            clave_ok = ("sí" if (app_dash == app[0] and tokens_read.huella(app[1]) == h_dash)
                        else "NO")
        bien = r["coincide"] and clave_ok != "NO"
        todo_bien &= bien
        print(f"{r['cuenta']:15} {str(r['app_token']):18} {str(r['app_entorno']):18} "
              f"{r['fuente']:8} {clave_ok:25} {r['edad_min']} min  "
              f"{'OK' if bien else '← NO SE PODRÍA RENOVAR'}")
    print("\nListo para encender." if todo_bien else
          "\nNO encender: definir MELI_APP_ID_<CUENTA>/MELI_CLIENT_SECRET_<CUENTA> "
          "con la app que emitió el token de esa cuenta.")
    return 0 if todo_bien else 1


if __name__ == "__main__":
    sys.exit(main())
