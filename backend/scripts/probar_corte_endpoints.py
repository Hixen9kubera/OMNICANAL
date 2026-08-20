"""
probar_corte_endpoints.py — El corte de golpe, medido contra TODAS las pantallas
en vez de 14 funciones.

POR QUÉ ESTE Y NO `probar_corte_total.py`
-----------------------------------------
Aquél sondea 14 caminos elegidos a mano. Es útil y es una MUESTRA — y en esta
migración ya nos pasó tres veces que una muestra dijera que estaba todo y el
censo dijera otra cosa: los lectores del grupo 4 eran 25 y no 19,
`MonitoreoOperaciones` leía 7 tablas y no 1, los scripts de mantenimiento eran
13 y no 8. **Ningún conteo se movió a la baja.**

Así que aquí no se eligen caminos: se levanta la app COMPLETA con
`MYSQL_ENABLED=false` —el mundo de después del retiro— y se llama a **todas** las
rutas GET que no necesitan parámetros, más un puñado con parámetros reales
sacados del sandbox. Lo que el panel usa, tal como lo usa.

CÓMO SE LEE
-----------
  200 + datos   el endpoint vive sin MySQL
  200 + VACIO   contesta, pero sin nada  ← hay que mirarlo uno por uno
  5xx           falla ruidoso: es el caso BUENO, se ve y se arregla
  503           el propio guardia de MySQL, honesto: "no disponible aqui"

**El renglón que importa es 200 + VACIO.** Un endpoint que truena se arregla
porque se ve; uno que contesta una lista vacía se ve igual que "no hay nada", y
ahí es donde este proyecto perdió dinero.

Ojo con el falso positivo: hay endpoints que están vacíos EN EL SANDBOX porque
el clon no tiene esos datos, no porque el corte los rompa. Por eso cada VACIO
se compara contra la misma llamada CON MySQL disponible — si también sale vacía
ahí, es el sandbox y no el corte.

SOLO GET. Nada de POST/PUT/DELETE: este script no dispara publicaciones,
sincronizaciones ni escrituras a canales.

Uso:
  ...python backend/scripts/probar_corte_endpoints.py
  ...python backend/scripts/probar_corte_endpoints.py --ruta /api/productos
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    p = ROOT / nombre
    if not p.exists():
        return d
    for l in p.read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


_ST = cargar("env.staging")
if not _ST.get("SUPABASE_DB_URL"):
    sys.exit("ABORT: env.staging sin SUPABASE_DB_URL.")
os.environ["SUPABASE_DB_URL"] = _ST["SUPABASE_DB_URL"]
os.environ["APP_ENV"] = "staging"
os.environ["MYSQL_ENABLED"] = "false"
os.environ["AUTH_OFF"] = "true"
os.environ["API_KEY"] = ""
# El corte total: todas las banderas de lectura, mas la escritura de tokens.
for _n in ("SUPABASE_READ_CHANNEL", "SUPABASE_READ_COSTING", "SUPABASE_READ_CORE",
           "SUPABASE_READ_ORDERS", "SUPABASE_READ_MEDIA", "SUPABASE_READ_WEBHOOKS",
           "SUPABASE_READ_STOCK_WATCH", "SUPABASE_READ_PUBLICACIONES",
           "SUPABASE_READ_CANDADOS", "SUPABASE_READ_TOKENS",
           "SUPABASE_WRITE_TOKENS"):
    os.environ[_n] = "true"
# Nada de flujos vivos mientras corre la prueba.
for _n in ("SYNC_ENABLED", "WEBHOOK_REGISTRO", "PEDIDOS_WC_ENABLED",
           "STOCK_WATCH_ENABLED", "FULL_WATCH_ENABLED", "KUBERA_MIRROR_ENABLED",
           "TIKTOK_ENABLED", "PEDIDOS_AMAZON_ENABLED", "PEDIDOS_M2E_ENABLED"):
    os.environ[_n] = "false"

from config import settings  # noqa: E402

_ref = (settings.supabase_db_url or "").split("postgres.")[-1].split(":")[0]
if _ref[:8] == "tukwcvsi":
    sys.exit("ABORT: esto apunta a PRODUCCION.")

_VACIOS = ({}, [], None, "", {"items": []}, {"productos": []})


def _es_vacio(cuerpo) -> bool:
    if cuerpo in ({}, [], None, ""):
        return True
    if isinstance(cuerpo, dict):
        # Un dict cuyas unicas colecciones estan vacias tambien es "no hay nada".
        cols = [v for v in cuerpo.values() if isinstance(v, (list, dict))]
        if cols and all(not v for v in cols):
            return True
        if cuerpo.get("total") == 0 and any(k in cuerpo for k in ("items", "productos", "filas")):
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ruta", default="")
    args = ap.parse_args()

    from fastapi.testclient import TestClient
    import main as app_main

    print(f"CORTE DE GOLPE contra TODAS las rutas GET")
    print(f"  sandbox {_ref[:8]}...  ·  MYSQL_ENABLED={settings.mysql_enabled}\n")

    # `raise_server_exceptions=True` A PROPOSITO: sin eso, todo fallo llega
    # como un 500 generico y no se puede distinguir "el corte lo rompio" de "al
    # sandbox le faltan credenciales de WooCommerce". La primera version marco
    # 4 rutas como rotas y las 4 eran lo segundo — una falsa alarma es tan
    # inutil como un falso verde.
    cli = TestClient(app_main.app, raise_server_exceptions=True)

    # Las rutas salen del OPENAPI de la propia app, no de `app.routes`.
    # Esta version de FastAPI envuelve cada router incluido en un
    # `_IncludedRouter` en vez de aplanarlo, asi que recorrer `app.routes`
    # devuelve 6 GET cuando en realidad hay decenas. La primera version de este
    # script reporto "2 rutas" y dio todo en verde — un censo que no censa es
    # peor que no tenerlo, porque tranquiliza.
    esquema = app_main.app.openapi()
    rutas = []
    for camino, ops in (esquema.get("paths") or {}).items():
        if "get" not in {m.lower() for m in ops}:
            continue
        if "{" in camino or camino.startswith(("/docs", "/openapi", "/redoc")):
            continue
        if args.ruta and not camino.startswith(args.ruta):
            continue
        rutas.append(camino)
    rutas = sorted(set(rutas))
    print("  " + str(len(rutas)) + " rutas GET sin parametros de camino")
    print()

    res = []
    # Lo que NO es culpa del corte: el sandbox no tiene WooCommerce ni Odoo ni
    # credenciales de canal. Esos fallos se apartan con nombre propio en vez de
    # contarse como rotos.
    _AMBIENTE = ("missing an 'http://'", "unsupported xml-rpc",
                 "nodename nor servname", "name or service not known",
                 "getaddrinfo failed", "connection refused")

    for camino in rutas:
        try:
            r = cli.get(camino, timeout=90)
            try:
                cuerpo = r.json()
            except Exception:  # noqa: BLE001
                cuerpo = r.text
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if any(p in msg for p in _AMBIENTE):
                res.append((camino, "SIN-WOO", f"{type(exc).__name__} — falta"
                                               " credencial de canal en el sandbox"))
            else:
                res.append((camino, "TRUENA",
                            f"{type(exc).__name__}: {str(exc)[:70]}"))
            continue
        if r.status_code == 503:
            res.append((camino, "503", "el guardia de MySQL contesta honesto"))
        elif r.status_code >= 500:
            res.append((camino, "TRUENA", f"{r.status_code} {str(cuerpo)[:60]}"))
        elif r.status_code >= 400:
            res.append((camino, "4xx", f"{r.status_code} (falta parametro o auth)"))
        elif _es_vacio(cuerpo):
            res.append((camino, "VACIO", str(cuerpo)[:70]))
        else:
            n_ = len(cuerpo) if hasattr(cuerpo, "__len__") else "ok"
            res.append((camino, "VIVE", f"{n_}"))

    orden = {"TRUENA": 0, "VACIO": 1, "SIN-WOO": 2, "503": 3, "4xx": 4, "VIVE": 5}
    print("=" * 78)
    for camino, estado, det in sorted(res, key=lambda x: (orden[x[1]], x[0])):
        print(f"  [{estado:7s}] {camino:44s} {det}")
    print("=" * 78)

    from collections import Counter
    c = Counter(e for _, e, _ in res)
    print("\n  " + "   ·   ".join(f"{k} {v}" for k, v in
                                  sorted(c.items(), key=lambda x: orden[x[0]])))
    malos = c.get("TRUENA", 0)
    print(f"\n  Rutas que TRUENAN: {malos}   ·   que contestan VACIO: {c.get('VACIO', 0)}")
    print("\n  Los VACIO hay que abrirlos uno por uno: algunos estan vacios porque")
    print("  el CLON del sandbox no tiene esos datos, no porque el corte los rompa.")
    print("  Los 4xx casi siempre son parametros obligatorios, no fallas.")
    sys.exit(0 if malos == 0 else 1)


if __name__ == "__main__":
    main()
