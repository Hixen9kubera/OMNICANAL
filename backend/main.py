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

from config import settings
from core.marketplaces import lista_canales
from models.schemas import HealthCheck
from routers import auth, canales, ia, productos, sync
from services import db, odoo, scheduler, woocommerce

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("omnicanal")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Arranca el sync programado de inventario (cada N min).
    scheduler.iniciar()
    yield
    scheduler.detener()


app = FastAPI(
    title="OMNICANAL · Kubera",
    description=(
        "Backend del panel omnicanal: visualiza las publicaciones de WooCommerce "
        "y su estado en cada marketplace (Mercado Libre, Amazon, TikTok, Walmart, "
        "Temu, Shein)."
    ),
    version="0.1.0",
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
app.include_router(canales.router)
app.include_router(sync.router)
app.include_router(ia.router)
app.include_router(auth.router)


@app.get("/", tags=["meta"])
def raiz():
    return {
        "app": "OMNICANAL · Kubera",
        "version": "1.0.0",
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
    )
