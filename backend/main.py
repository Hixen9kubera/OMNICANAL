"""
main.py â€” Punto de entrada del backend FastAPI de OMNICANAL.

Crea la app, configura CORS para el frontend Next.js, registra los routers
(productos, canales, ia, auth) y expone un health check que verifica
WooCommerce, la base de datos y Odoo.

Arranque local:
    uvicorn main:app --reload --port 8000

En Railway se usa la variable PORT (ver Procfile / railway).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings, validar_ambiente
from core.marketplaces import lista_canales
from core.middleware import identidad
from models.schemas import HealthCheck
from routers import (auth, canales, competencia, crear, fanout, fulfillment, ia,
                     imagenes, migracion, productos, publicar, resolver, sync, ventas,
                     tiktok, webhooks)
from services import db, odoo, scheduler, woocommerce

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("omnicanal")

# Candado anti-mezcla de ambientes: si la config es contradictoria (p. ej.
# staging apuntando al Supabase de producciÃ³n), el proceso muere AQUÃ, antes de
# aceptar una sola peticiÃ³n. Ver config.validar_ambiente().
validar_ambiente(settings)

# Las docs interactivas solo se publican fuera de producción, salvo que se pidan
# a propósito con DOCS_PUBLICAS=true.
_DOCS_VISIBLES = settings.docs_publicas or settings.app_env != "production"

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Arranca el sync programado de inventario (cada N min).
    scheduler.iniciar()
    # Calienta el Ã­ndice de "Crear Productos" en segundo plano (escanea WooCommerce),
    # para que la primera visita a esa vista no espere la construcciÃ³n del Ã­ndice.
    # En staging no hay credenciales de Woo: sin WC_URL el warm-up no aplica.
    import asyncio
    if settings.wc_url:
        asyncio.create_task(woocommerce.indice_candidatos())
    else:
        log.info("Sin WC_URL configurada â€” omito el precalentamiento del Ã­ndice de Woo.")
    # Calienta el cachÃ© de VENTAS (Ãºltimos 14 dÃ­as por cuenta, secuencial para no
    # saturar ML): la tabla persiste entre deploys, asÃ­ que tras el primer
    # llenado esto solo refresca HOY y el tab abre al instante. Necesita MySQL
    # (en staging solo-Supabase se omite).
    async def _ventas_warmup():
        from datetime import timedelta
        from services import ventas_ml
        try:
            hoy = ventas_ml.hoy_mx()
            for c in ("BEKURA", "SANCORFASHION"):
                for i in range(14):
                    await ventas_ml.asegurar_dia(c, hoy - timedelta(days=i))
        except Exception as exc:  # noqa: BLE001
            log.warning("Warmup de ventas incompleto: %s", exc)
    if getattr(settings, "mysql_enabled", True) and settings.ventas_ml_refresh:
        asyncio.create_task(_ventas_warmup())
    else:
        log.info("Warmup de ventas omitido (MySQL off o refresco ML apagado).")
    # Contenido de Amazon al crear productos: se declara en el arranque porque
    # es la ÚNICA forma honesta de saber si está encendido. Leer el `.env` local
    # ya llevó a reportar el fan-out como apagado cuando llevaba dos semanas
    # escribiendo; la fuente buena es este log y las tablas.
    log.info("Contenido Amazon con IA al crear productos: %s (AMAZON_IA_EN_CREAR)",
             "ENCENDIDO" if settings.amazon_ia_en_crear else "apagado")
    # Los tres de TikTok, por la misma razón: un flag que solo vive en Railway no
    # se puede verificar sin abrir Railway. Y con `FANOUT_CANALES` hay un motivo
    # extra — su VALOR no se puede leer desde fuera (la API lo devuelve
    # redactado), así que aquí se imprime la lista ya RESUELTA: es la única forma
    # de saber a qué canales puede escribirles el fan-out sin adivinar.
    from services import fanout_stock as _fs
    log.info("TikTok · contenido al crear: %s · pedidos por webhook: %s · "
             "fan-out de stock: %s · canales del fan-out: %s",
             "ENCENDIDO" if settings.tiktok_ia_en_crear else "apagado",
             "ENCENDIDO" if settings.pedidos_tiktok_enabled else "apagado",
             "ENCENDIDO" if settings.fanout_tiktok else "apagado",
             sorted(_fs._canales_activos()) if _fs._canales_activos() else "TODOS")  # noqa: SLF001
    yield
    scheduler.detener()


app = FastAPI(
    title="OMNICANAL Â· Kubera",
    description=(
        "Backend del panel omnicanal: visualiza las publicaciones de WooCommerce "
        "y su estado en cada marketplace (Mercado Libre, Amazon, TikTok, Walmart, "
        "Temu, Shein)."
    ),
    version="0.147.0",
    lifespan=lifespan,
    # /docs, /redoc y /openapi.json publican el mapa COMPLETO de los 84
    # endpoints: rutas, parámetros y esquemas. Con la API abierta eso es un
    # plano para recorrerla. En producción se apagan; DOCS_PUBLICAS=true los
    # reabre sin tocar código (staging o depuración).
    docs_url="/docs" if _DOCS_VISIBLES else None,
    redoc_url="/redoc" if _DOCS_VISIBLES else None,
    openapi_url="/openapi.json" if _DOCS_VISIBLES else None,
)

# Puerta de identidad de TODA la API (cierra Temu III.1). Se registra ANTES que
# el CORS en el código para que quede DESPUÉS en la cadena de ejecución
# —Starlette invierte el orden—, de modo que el preflight de CORS se resuelva
# sin pasar por la puerta.
#
# NO reordenar sin leer las tres reglas del encabezado de core/middleware.py:
# `/api/health` es el healthcheck de Railway y el webhook de ML no puede mandar
# nuestro token. Un 401 en cualquiera de los dos apaga la operación.
app.middleware("http")(identidad)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=r"https://.*\.(railway\.app|up\.railway\.app|vercel\.app)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(productos.router)
app.include_router(imagenes.router)
app.include_router(crear.router)
app.include_router(canales.router)
app.include_router(competencia.router)
app.include_router(sync.router)
app.include_router(webhooks.router)
app.include_router(ventas.router)
app.include_router(ia.router)
app.include_router(publicar.router)
app.include_router(auth.router)
app.include_router(migracion.router)
app.include_router(fanout.router)
app.include_router(fulfillment.router)
app.include_router(tiktok.router)
# Resolver: packing list vs costos_validados (herramienta de /costos).
app.include_router(resolver.router)


@app.get("/", tags=["meta"])
def raiz():
    return {
        "app": "OMNICANAL Â· Kubera",
        "version": "0.147.0",
        "docs": "/docs",
        "canales": [c["id"] for c in lista_canales()],
    }


@app.get("/api/health", response_model=HealthCheck, tags=["meta"])
async def health():
    return HealthCheck(
        status="ok",
        woocommerce=await woocommerce.ping(),
        base_datos=db.ping(),
        odoo=odoo.ping(),
        ambiente=settings.app_env,
        # Distingue "MySQL caÃ­do" (falla real) de "MySQL apagado por config"
        # (staging, opciÃ³n A) â€” ambos reportan base_datos=false pero solo el
        # primero es un problema.
        nota=None if settings.mysql_enabled else "MySQL deshabilitado por config (staging solo-Supabase)",
    )
