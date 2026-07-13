"""
ia.py — Endpoints de Inteligencia Artificial (generación de contenido por canal).

  GET  /api/ia/generadores?canal=amazon
      → lista de generadores disponibles para el canal (para pintar los botones
        del estudio de producto: Título, Bullets, Descripción, Atributos,
        Set de imágenes…).

  POST /api/ia/generar
      → ejecuta un generador concreto (canal + generador) sobre un producto y
        devuelve el contenido optimizado para ESE canal. Cada canal tiene su
        propio prompt especializado (Amazon con las guías vigentes, ML, etc.).

  POST /api/ia/titulo   → atajo histórico (título ML). Se mantiene por compat.
  GET  /api/ia/prompts  → prompts base (compat).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services import claude, competencia, ia_generadores

router = APIRouter(prefix="/api/ia", tags=["ia"])


class AtributoIn(BaseModel):
    nombre: str
    valor: str = ""


class ProductoCtx(BaseModel):
    """Contexto del producto que se le pasa al generador."""
    nombre: str = ""
    marca: str | None = None
    modelo: str | None = None
    categoria: str | None = None      # ruta legible "A › B › C"
    ml_cat_id: str | None = None      # id de categoría ML (para traer atributos reales)
    sku: str | None = None
    descripcion: str | None = None
    precio: float | None = None
    costo: float | None = None
    publico: str | None = None
    atributos: list[AtributoIn] = Field(default_factory=list)


class GenerarRequest(BaseModel):
    canal: str = "amazon"
    generador: str = "titulo"
    producto: ProductoCtx = Field(default_factory=ProductoCtx)


@router.get("/generadores")
def generadores(canal: str = "amazon") -> dict[str, Any]:
    """Generadores de contenido disponibles para el canal indicado."""
    return {"canal": canal, "generadores": ia_generadores.definiciones(canal)}


@router.post("/generar")
def generar(req: GenerarRequest) -> dict[str, Any]:
    """Genera contenido para un canal usando su prompt especializado."""
    producto = req.producto.model_dump()
    producto["atributos"] = [a.model_dump() for a in req.producto.atributos]
    return ia_generadores.generar(req.canal, req.generador, producto)


class MejorarRequest(BaseModel):
    canal: str = "mercado_libre"
    producto: ProductoCtx = Field(default_factory=ProductoCtx)


@router.post("/mejorar")
async def mejorar(req: MejorarRequest) -> dict[str, Any]:
    """Mejora con IA varios campos del canal a la vez (título, descripción,
    atributos y —en Amazon— highlights y bullets)."""
    producto = req.producto.model_dump()
    producto["atributos"] = [a.model_dump() for a in req.producto.atributos]
    return await ia_generadores.mejorar(req.canal, producto)


class CompetenciaRequest(BaseModel):
    producto: ProductoCtx = Field(default_factory=ProductoCtx)
    con_lista: bool = True


@router.post("/precio-competencia")
def precio_competencia(req: CompetenciaRequest) -> dict[str, Any]:
    """Precio de competencia sugerido (solo para mostrar; no cambia campos)."""
    producto = req.producto.model_dump()
    producto["atributos"] = [a.model_dump() for a in req.producto.atributos]
    return competencia.precio_competencia(producto, con_lista=req.con_lista)


# ── Compatibilidad con la versión anterior ───────────────────────────────────
class TituloRequest(BaseModel):
    nombre: str
    canal: str = "mercado_libre"
    contexto: str = ""


@router.post("/titulo")
def generar_titulo(req: TituloRequest):
    return claude.generar_titulo(req.nombre, req.canal, req.contexto)


@router.get("/prompts")
def prompts_por_canal():
    """Devuelve los prompts base configurados por canal (compat)."""
    return claude.PROMPTS_CANAL
