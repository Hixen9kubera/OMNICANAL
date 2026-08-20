"""
probar_cors.py — Comprueba el CORS sobre la app REAL, en tres configuraciones.

POR QUE
-------
Hasta la v0.239 el comodin de CORS estaba hardcodeado en main.py y aceptaba
CUALQUIER pagina alojada en vercel.app o en un subdominio de Railway — que se
consiguen gratis y en minutos — con allow_credentials=True. Y el panel entraba
POR AHI, no por la lista: en produccion CORS_ORIGINS solo traia localhost.

El escenario B es el que hay que respetar al desplegar: si se aprieta el comodin
sin haber puesto antes el dominio real en CORS_ORIGINS, el panel se queda fuera.
La prueba lo deja demostrado en vez de dicho.

USO
---
    export SUPABASE_DB_URL=...        # el del sandbox; no se conecta, pero config lo pide
    export APP_ENV=staging MYSQL_ENABLED=false
    export BACKEND="$(pwd)/backend"
    backend/.venv/Scripts/python.exe backend/scripts/probar_cors.py
"""
import os, sys, importlib

from pathlib import Path
sys.path.insert(0, os.environ.get("BACKEND")
                or str(Path(__file__).resolve().parent.parent))
PANEL = "https://frontendomnicanal-production.up.railway.app"
ATACANTE = "https://sitio-de-cualquiera.vercel.app"
ATACANTE2 = "https://loquesea.up.railway.app"

fallas = 0
def check(t, got, want):
    global fallas
    ok = got == want
    if not ok: fallas += 1
    print(f"    [{'OK ' if ok else 'MAL'}] {t}: permitido={got} (esperado {want})")

def permitido(app, origen):
    from fastapi.testclient import TestClient
    c = TestClient(app)
    # Preflight: es lo que el navegador manda antes de la peticion real.
    r = c.options("/api/health", headers={
        "Origin": origen, "Access-Control-Request-Method": "GET"})
    return r.headers.get("access-control-allow-origin") == origen

def cargar(cors_origins, regex):
    os.environ["CORS_ORIGINS"] = cors_origins
    os.environ["CORS_ORIGIN_REGEX"] = regex
    for m in ("main", "config"):
        sys.modules.pop(m, None)
    return importlib.import_module("main").app

print("=== A) como quedaria en produccion: lista con el panel, sin comodin ===")
app = cargar(f"{PANEL},http://localhost:3000", "")
check("el panel entra", permitido(app, PANEL), True)
check("una pagina en vercel.app NO entra", permitido(app, ATACANTE), False)
check("un subdominio cualquiera de railway NO entra", permitido(app, ATACANTE2), False)
check("localhost sigue entrando", permitido(app, "http://localhost:3000"), True)

print("\n=== B) el error que hay que evitar: desplegar sin poner el dominio ===")
app = cargar("http://localhost:3000", "")
check("el panel se queda FUERA (por eso el orden importa)",
      permitido(app, PANEL), False)

print("\n=== C) la escotilla: reabrir con la variable, sin desplegar ===")
app = cargar("http://localhost:3000",
             r"https://.*\.(railway\.app|up\.railway\.app|vercel\.app)$")
check("con el comodin puesto, el panel vuelve a entrar",
      permitido(app, PANEL), True)

print(f"\nRESULTADO: {'TODO BIEN' if not fallas else f'{fallas} FALLA(S)'}")
sys.exit(1 if fallas else 0)
