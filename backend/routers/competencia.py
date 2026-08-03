"""
competencia.py — Competencia en Mercado Libre (MVP, 10 SKUs).

  GET    /api/competencia/estado        → qué fuentes están vivas y qué NO se puede medir
  GET    /api/competencia/skus          → los SKUs vigilados con su término general
  POST   /api/competencia/sembrar       → alta de SKUs (nombre/categoría de MySQL + término por IA)
  PATCH  /api/competencia/skus/{sku}    → corrige el término general a mano
  GET    /api/competencia/tabla         → vista por CATEGORÍA con sus SKUs y mis posiciones
  GET    /api/competencia/detalle       → los resultados de un SKU (general | titulo | categoria)
  GET    /api/competencia/corrida       → cuándo se midió por última vez y cuánto costó
  POST   /api/competencia/correr        → dispara la corrida (para pruebas; en prod es un cron)

La corrida mensual la dispara un **cron de Railway** (`backend/railway.competencia.json`
→ `scripts/competencia_cron.py`), no un scheduler embebido: el web es un solo
proceso y si se reinicia el día 1, un cron en proceso no dispara ese mes.
`POST /correr` existe para probar a mano.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings
from services import competencia_captura, competencia_scraper, competencia_store

log = logging.getLogger("omnicanal.routers.competencia")
router = APIRouter(prefix="/api/competencia", tags=["competencia"])

# Progreso de la corrida manual, en memoria (mismo patrón que sync_woo/crear).
_corrida: dict[str, Any] = {"estado": "inactivo"}

_TIPOS = ("general", "titulo", "categoria")


class SembrarReq(BaseModel):
    skus: list[str]
    con_ia: bool = True


class TerminoReq(BaseModel):
    termino_general: str


@router.get("/estado")
def estado():
    """Diagnóstico honesto: qué fuentes hay y qué NO se puede medir, con el motivo."""
    return {
        "supabase": competencia_store.disponible(),
        "scraper_apify": competencia_scraper.disponible(),
        "top_por_busqueda": settings.competencia_top,
        "con_detalle": settings.competencia_con_detalle,
        "costo_por_busqueda_usd": competencia_scraper.costo_estimado(
            1, settings.competencia_top, settings.competencia_con_detalle),
        "limites": {
            "posicion_organica": "Solo por scraper: GET /sites/MLM/search responde 403.",
            "ficha_ajena": "GET /items/{id} de un competidor responde 403; título, "
                           "precio, imagen y descripción solo vienen del scraper.",
            "descripcion": "Es la descripción CORTA derivada de atributos "
                           "('Largo: 4 m | Ancho: 6 m'), no el texto largo del "
                           "vendedor: ML no expone ese texto de publicaciones ajenas.",
            "categoria_del_scraper": "El actor devuelve categoryId nulo; la categoría "
                                     "sale de nuestra taxonomía (categorias_ml).",
            "visitas": "API de ML, funcionan para cualquier publicación, de a UN id "
                       "por llamada (dos ids → HTTP 400).",
            "vendidos": "Del scraper. La API no expone sold_quantity de items ajenos.",
            "historico": "NO se guarda: cada corrida borra la anterior y reescribe.",
        },
    }


# ── SKUs vigilados ───────────────────────────────────────────────────────────

@router.get("/skus")
def skus(solo_activos: bool = True):
    return {"skus": competencia_store.listar_skus(solo_activos)}


@router.post("/sembrar")
def sembrar(req: SembrarReq):
    """
    Alta de los SKUs del MVP. Nombre y categoría salen de MySQL; el término
    general lo propone la IA y queda editable.
    """
    if not competencia_store.disponible():
        raise HTTPException(503, "SUPABASE_DB_URL no está configurada.")
    r = competencia_captura.sembrar_skus(req.skus, con_ia=req.con_ia)
    if not r.get("ok"):
        raise HTTPException(400, r.get("motivo") or "La siembra falló.")
    return r


@router.patch("/skus/{sku}")
def corregir_termino(sku: str, req: TerminoReq):
    """
    Corrige el término general. Queda marcado como 'manual' y ninguna corrida
    posterior lo vuelve a pisar con una propuesta de la IA.
    """
    termino = req.termino_general.strip()
    if not termino:
        raise HTTPException(422, "El término general no puede ir vacío.")
    if not competencia_store.actualizar_termino(sku, termino):
        raise HTTPException(404, f"No se pudo actualizar el término de {sku}.")
    return {"ok": True, "sku": sku, "termino_general": termino,
            "termino_origen": "manual"}


# ── Vistas ───────────────────────────────────────────────────────────────────

@router.get("/tabla")
def tabla():
    """
    Vista por CATEGORÍA con sus SKUs dentro y, por SKU, mi posición en las tres
    mediciones. Es la pantalla principal del tab.
    """
    filas = competencia_store.por_categoria()
    grupos: dict[str, dict[str, Any]] = {}
    for f in filas:
        clave = f.get("categoria_id") or "sin_categoria"
        g = grupos.setdefault(clave, {
            "categoria_id": f.get("categoria_id"),
            "categoria_nombre": f.get("categoria_nombre") or "Sin categoría",
            "skus": [],
        })
        g["skus"].append(f)
    return {"categorias": list(grupos.values()),
            "corrida": competencia_store.ultima_corrida()}


@router.get("/detalle")
def detalle(sku: str, tipo: str | None = None):
    """Los resultados de un SKU. `tipo` en general | titulo | categoria."""
    if tipo and tipo not in _TIPOS:
        raise HTTPException(400, f"tipo debe ser uno de {_TIPOS}")
    filas = competencia_store.resultados(sku, tipo)
    por_tipo: dict[str, list[dict[str, Any]]] = {t: [] for t in _TIPOS}
    for f in filas:
        por_tipo.setdefault(f["tipo"], []).append(f)
    return {
        "sku": sku,
        "posiciones": competencia_store.posiciones(sku),
        "resultados": por_tipo if not tipo else {tipo: por_tipo.get(tipo, [])},
    }


@router.get("/corrida")
def corrida():
    return {"ultima": competencia_store.ultima_corrida(), "en_curso": _corrida}


# ── Corrida manual (en producción la dispara el cron de Railway) ────────────

@router.post("/correr")
async def correr(skus: str | None = None):
    """
    Dispara la corrida en segundo plano y responde de inmediato; la UI hace
    polling a /corrida. `skus` acepta una lista separada por comas para probar
    con uno solo sin gastar la corrida completa.
    """
    if _corrida.get("estado") == "corriendo":
        raise HTTPException(409, "Ya hay una corrida de competencia en curso.")
    if not competencia_store.disponible():
        raise HTTPException(503, "SUPABASE_DB_URL no está configurada.")

    lista = [s.strip() for s in (skus or "").split(",") if s.strip()] or None

    async def _tarea():
        _corrida.update(estado="corriendo", resultado=None, error=None)
        try:
            r = await competencia_captura.correr(origen="manual", skus=lista)
            _corrida.update(estado="listo" if r.get("ok") else "error",
                            resultado=r, error=r.get("motivo"))
        except Exception as exc:  # noqa: BLE001
            log.error("Corrida de competencia falló: %s", exc)
            _corrida.update(estado="error", error=str(exc)[:500])

    asyncio.create_task(_tarea())
    return {"ok": True, "estado": "corriendo", "skus": lista or "todos"}
