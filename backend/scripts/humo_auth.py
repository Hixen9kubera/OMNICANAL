"""
humo_auth.py — La prueba que decide si el enforcement se puede desplegar.

POR QUÉ EXISTE
--------------
Encender la autenticación tiene dos formas de apagar el negocio entero, y las
dos son silenciosas:

  1. `/api/health` es el `healthcheckPath` de `railway.json` con
     `restartPolicyType=ON_FAILURE`. Si devuelve 401, Railway da el deploy por
     muerto y entra en BUCLE DE REINICIO: se cae el webhook, el scheduler (sync
     de 15 min, vigilante de Odoo, fan-out, sondeos de Amazon y M2E) y el panel.

  2. `POST /api/webhooks/ml` no puede mandar nuestro token. Si devuelve 401, ML
     reintenta 1 hora y después DESHABILITA el topic. A partir de ahí se dejan
     de capturar ventas reales sin ningún error visible.

Esta suite levanta la app CON `AUTH_ENFORCED=true` y comprueba que esas dos
rutas siguen respondiendo 200 sin credenciales, y que las demás sí piden.

Corre contra la app en memoria (TestClient), sin red y sin tocar producción.

    python -m scripts.humo_auth          # desde backend/
    exit 0 = seguro desplegar · exit 1 = NO desplegar
"""
from __future__ import annotations

import os
import sys

# El entorno se prepara ANTES de importar la app: config.py lee las variables al
# importarse, así que esto tiene que ir arriba de todo. Por eso los dos modos
# corren en PROCESOS distintos (ver ejecutar_ambos()).
MODO_OBSERVACION = "--observacion" in sys.argv

os.environ["API_KEY"] = "llave-de-prueba-humo"
os.environ["AUTH_ENFORCED"] = "false" if MODO_OBSERVACION else "true"
os.environ["RBAC_ENFORCED"] = "false" if MODO_OBSERVACION else "true"
os.environ.setdefault("APP_ENV", "production")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

LLAVE = os.environ["API_KEY"]
fallos: list[str] = []
pasadas = 0


def revisar(nombre: str, obtenido, esperado, pista: str = "") -> None:
    global pasadas
    if obtenido == esperado:
        pasadas += 1
        print(f"  OK   {nombre}  ({obtenido})")
    else:
        fallos.append(f"{nombre}: esperado {esperado}, obtenido {obtenido}. {pista}")
        print(f"  FALLA {nombre}  esperado={esperado} obtenido={obtenido}")


def observacion() -> int:
    """
    El estado en el que se DESPLIEGA primero: nadie se bloquea.

    Es la mitad más importante de la suite. Si esto falla, el primer deploy
    rompe el panel — y sería un autogol, porque el modo observación existe
    justamente para no romper nada mientras se mide quién llama.
    """
    from fastapi.testclient import TestClient

    import main as app_module

    cliente = TestClient(app_module.app, raise_server_exceptions=False)
    print("=" * 72)
    print("HUMO EN MODO OBSERVACIÓN — AUTH_ENFORCED=false (así se despliega)")
    print("=" * 72)
    print("\nNADIE SE BLOQUEA: todo responde igual que antes del cambio")
    for ruta in ("/api/health", "/api/productos", "/api/canales",
                 "/api/fanout/estado", "/api/migracion/errores",
                 "/api/webhooks/estado", "/api/auth/me"):
        revisar(f"GET  {ruta} SIN credencial", cliente.get(ruta).status_code, 200,
                "En observación nada puede devolver 401.")
    revisar("POST /api/webhooks/ml sin credencial",
            cliente.post("/api/webhooks/ml",
                         json={"topic": "orders_v2", "resource": "/orders/1"}).status_code,
            200, "El webhook de ML jamás puede fallar.")

    print("\nEL ROL INSUFICIENTE TAMPOCO BLOQUEA (RBAC_ENFORCED=false)")
    revisar("GET  /api/auditoria sin credencial",
            cliente.get("/api/auditoria").status_code in (200, 404), True,
            "En observación un rol corto se registra pero no se bloquea.")

    print("\n" + "=" * 72)
    if fallos:
        print(f"FALLARON {len(fallos)} DE {len(fallos) + pasadas} — NO DESPLEGAR")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print(f"LAS {pasadas} PRUEBAS DE OBSERVACIÓN PASARON")
    return 0


def main() -> int:
    if MODO_OBSERVACION:
        return observacion()

    from fastapi.testclient import TestClient

    import main as app_module

    cliente = TestClient(app_module.app, raise_server_exceptions=False)

    print("=" * 72)
    print("HUMO DE AUTENTICACIÓN — AUTH_ENFORCED=true (el estado final)")
    print("=" * 72)

    print("\n1) RUTAS QUE JAMÁS DEBEN PEDIR CREDENCIAL")
    print("   (un 401 aquí apaga el backend o corta la captura de ventas)")
    revisar("GET  /api/health sin credencial",
            cliente.get("/api/health").status_code, 200,
            "Railway reiniciaría el deploy en bucle.")
    revisar("GET  /api/health/ (barra final)",
            cliente.get("/api/health/").status_code, 200,
            "La normalización de la ruta no está funcionando.")
    revisar("POST /api/webhooks/ml sin credencial",
            cliente.post("/api/webhooks/ml",
                         json={"topic": "orders_v2", "resource": "/orders/1"}).status_code,
            200, "ML deshabilitaría el topic y se perderían ventas.")
    revisar("GET  /api/webhooks/ml sin credencial",
            cliente.get("/api/webhooks/ml").status_code, 200,
            "ML valida la URL con un GET al registrarla.")

    print("\n2) EL WEBHOOK RESPONDE 200 AUNQUE LE MANDEN BASURA")
    revisar("POST /api/webhooks/ml con cuerpo inválido",
            cliente.post("/api/webhooks/ml", content=b"no-es-json",
                         headers={"Content-Type": "application/json"}).status_code,
            200, "La guarda absoluta del handler no está funcionando.")
    revisar("POST /api/webhooks/ml sin cuerpo",
            cliente.post("/api/webhooks/ml").status_code, 200,
            "La guarda absoluta del handler no está funcionando.")

    print("\n3) EL PREFLIGHT DE CORS PASA SIN CREDENCIAL")
    revisar("OPTIONS /api/productos",
            cliente.options("/api/productos", headers={
                "Origin": "https://ejemplo.up.railway.app",
                "Access-Control-Request-Method": "GET"}).status_code,
            200, "Sin esto el panel muere con un error de CORS.")

    print("\n4) LO DEMÁS SÍ PIDE CREDENCIAL")
    for ruta in ("/api/productos", "/api/fanout/estado", "/api/migracion/errores",
                 "/api/canales", "/api/webhooks/ml/log", "/api/webhooks/estado"):
        revisar(f"GET  {ruta} sin credencial",
                cliente.get(ruta).status_code, 401,
                "Sigue abierto a cualquiera — es justo lo que Temu pregunta.")

    print("\n5) CON CREDENCIAL VÁLIDA SÍ PASA")
    r = cliente.get("/api/canales", headers={"X-API-Key": LLAVE})
    revisar("GET  /api/canales con X-API-Key", r.status_code, 200,
            "La llave correcta está siendo rechazada: el panel no podría entrar.")

    print("\n6) UNA LLAVE EQUIVOCADA NO PASA")
    revisar("GET  /api/canales con llave falsa",
            cliente.get("/api/canales",
                        headers={"X-API-Key": "llave-equivocada"}).status_code,
            401, "Cualquier valor está siendo aceptado.")

    print("\n7) LA ESCOTILLA AUTH_RUTAS_ABIERTAS FUNCIONA")
    antes = app_module.settings.auth_rutas_abiertas
    try:
        app_module.settings.auth_rutas_abiertas = "/api/canales"
        revisar("GET  /api/canales con la ruta en la escotilla",
                cliente.get("/api/canales").status_code, 200,
                "No se podría abrir una ruta olvidada sin hacer deploy.")
    finally:
        app_module.settings.auth_rutas_abiertas = antes

    print("\n8) LAS DOCS ESTÁN CERRADAS EN PRODUCCIÓN")
    for ruta in ("/docs", "/openapi.json"):
        revisar(f"GET  {ruta}", cliente.get(ruta).status_code, 404,
                "El mapa completo de los 84 endpoints sigue público.")

    print("\n9) /api/auth/me DICE LA VERDAD (antes mentía siempre)")
    # Con enforcement encendido ni siquiera se llega al endpoint: el middleware
    # corta antes. Eso es lo correcto — el placeholder viejo, en cambio,
    # contestaba `rol: admin` a cualquiera que preguntara.
    revisar("sin credencial -> 401 (no 'admin' regalado)",
            cliente.get("/api/auth/me").status_code, 401,
            "El placeholder viejo devolvía admin a cualquiera.")
    con = cliente.get("/api/auth/me", headers={"X-API-Key": LLAVE}).json()
    revisar("con credencial -> autenticado=True", con.get("autenticado"), True)
    revisar("con credencial -> tipo=maquina", con.get("tipo"), "maquina")

    print("\n10) LAS REGLAS DE ROL (core/rbac.py)")
    from core import rbac
    # El corte NO es mirar-vs-escribir: un KAM (rol `operador`) publica y mueve
    # precios, porque ese es su trabajo. El corte es TRABAJO COMERCIAL vs
    # INFRAESTRUCTURA. Estos casos son la evidencia de III.2, uno por pestaña.
    casos = [
        # (rol, método, ruta, ¿debe permitirse?)
        # --- lectura: ve el catálogo, NO el P&L ---
        ("lectura",  "GET",  "/api/productos", True),
        ("lectura",  "GET",  "/api/ventas/horario", True),
        ("lectura",  "POST", "/api/productos/ABC/contenido", False),
        ("lectura",  "GET",  "/api/crear/costos", False),          # márgenes
        ("lectura",  "GET",  "/api/fulfillment/dashboard", False),  # trae margen_pct
        # --- KAM: sus seis pestañas, COMPLETAS ---
        ("operador", "GET",  "/api/fulfillment/dashboard", True),  # Análisis
        ("operador", "POST", "/api/productos/ABC/contenido", True),  # Productos
        ("operador", "POST", "/api/imagenes/ABC/procesar", True),
        ("operador", "POST", "/api/canales/ml/refrescar/ABC", True),  # Omnicanal
        ("operador", "POST", "/api/crear/productos", True),          # Crear
        ("operador", "POST", "/api/ia/generar", True),
        ("operador", "POST", "/api/publicar/preview", True),
        ("operador", "POST", "/api/publicar/confirmar", True),       # SÍ publica
        ("operador", "GET",  "/api/crear/costos", True),             # Costos
        ("operador", "POST", "/api/crear/costos/ABC/recalcular", True),
        ("operador", "POST", "/api/crear/costos/bulk", True),        # SÍ precios masivos
        ("operador", "GET",  "/api/competencia/tabla", True),        # Competencia
        ("operador", "POST", "/api/competencia/correr", True),
        ("operador", "PATCH", "/api/competencia/skus/ABC", True),
        # --- ...pero NO la infraestructura que las sostiene ---
        ("operador", "POST", "/api/sync/woo", False),          # barrido de TODO el catálogo
        ("operador", "POST", "/api/fanout/encolar", False),    # empuja stock a los canales
        ("operador", "POST", "/api/webhooks/pausar", False),   # apaga la captura de ventas
        ("operador", "GET",  "/api/webhooks/estado", False),
        ("operador", "POST", "/api/migracion/backfill/channel-orders", False),
        ("operador", "GET",  "/api/migracion/estado", False),
        ("operador", "POST", "/api/crear/destrabar", False),   # más específica que /api/crear
        ("operador", "GET",  "/api/auditoria", False),         # NO ve la bitácora
        # --- admin: todo ---
        ("admin",    "POST", "/api/publicar/confirmar", True),
        ("admin",    "POST", "/api/sync/woo", True),
        ("admin",    "POST", "/api/migracion/backfill/channel-orders", True),
        ("admin",    "GET",  "/api/auditoria", True),
    ]
    for rol, metodo, ruta, esperado in casos:
        revisar(f"{rol:9} {metodo:5} {ruta}",
                rbac.permite(rol, metodo, ruta), esperado,
                "La tabla de roles no está aplicando el mínimo privilegio.")
    revisar("una ruta NO listada exige admin",
            rbac.rol_requerido("POST", "/api/algo/que/no/existe"), "admin",
            "Un endpoint nuevo debe nacer cerrado, no abierto.")
    revisar("un MÉTODO no listado (DELETE) exige admin",
            rbac.rol_requerido("DELETE", "/api/productos/ABC"), "admin",
            "Un verbo nuevo sobre una ruta conocida también nace cerrado.")

    print("\n11) EL SISTEMA AGUANTA LO RARO")
    revisar("header X-API-Key vacío",
            cliente.get("/api/canales", headers={"X-API-Key": ""}).status_code, 401)
    revisar("Bearer con basura",
            cliente.get("/api/canales",
                        headers={"Authorization": "Bearer no-es-un-token"}).status_code,
            401, "Un token inválido no puede pasar.")
    revisar("Authorization mal formado",
            cliente.get("/api/canales",
                        headers={"Authorization": "loquesea"}).status_code, 401)
    revisar("ruta inexistente sigue dando 404, no 401",
            cliente.get("/api/no-existe",
                        headers={"X-API-Key": LLAVE}).status_code, 404,
            "Con credencial válida, una ruta que no existe es 404.")

    print("\n" + "=" * 72)
    if fallos:
        print(f"FALLARON {len(fallos)} DE {len(fallos) + pasadas} — NO DESPLEGAR")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print(f"LAS {pasadas} PRUEBAS PASARON — seguro desplegar")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
