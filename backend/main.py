"""
main.py — Punto de entrada del backend FastAPI de OMNICANAL.

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
from models.schemas import HealthCheck
from routers import auth, canales, crear, ia, imagenes, productos, publicar, sync, ventas, webhooks
from services import db, odoo, scheduler, woocommerce

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("omnicanal")

# Candado anti-mezcla de ambientes: si la config es contradictoria (p. ej.
# staging apuntando al Supabase de producción), el proceso muere AQUÍ, antes de
# aceptar una sola petición. Ver config.validar_ambiente().
validar_ambiente(settings)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Arranca el sync programado de inventario (cada N min).
    scheduler.iniciar()
    # Calienta el índice de "Crear Productos" en segundo plano (escanea WooCommerce),
    # para que la primera visita a esa vista no espere la construcción del índice.
    # En staging no hay credenciales de Woo: sin WC_URL el warm-up no aplica.
    import asyncio
    if settings.wc_url:
        asyncio.create_task(woocommerce.indice_candidatos())
    else:
        log.info("Sin WC_URL configurada — omito el precalentamiento del índice de Woo.")
    # Calienta el caché de VENTAS (últimos 14 días por cuenta, secuencial para no
    # saturar ML): la tabla persiste entre deploys, así que tras el primer
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
    yield
    scheduler.detener()


app = FastAPI(
    title="OMNICANAL · Kubera",
    description=(
        "Backend del panel omnicanal: visualiza las publicaciones de WooCommerce "
        "y su estado en cada marketplace (Mercado Libre, Amazon, TikTok, Walmart, "
        "Temu, Shein)."
    ),
    version="0.10.0",
    lifespan=lifespan,
)

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
app.include_router(sync.router)
app.include_router(webhooks.router)
app.include_router(ventas.router)
app.include_router(ia.router)
app.include_router(publicar.router)
app.include_router(auth.router)


@app.get("/", tags=["meta"])
def raiz():
    return {
        "app": "OMNICANAL · Kubera",
        "version": "0.10.0",
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
        # Distingue "MySQL caído" (falla real) de "MySQL apagado por config"
        # (staging, opción A) — ambos reportan base_datos=false pero solo el
        # primero es un problema.
        nota=None if settings.mysql_enabled else "MySQL deshabilitado por config (staging solo-Supabase)",
    )
