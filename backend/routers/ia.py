"""
ia.py — Endpoints de Inteligencia Artificial (Claude).

  POST /api/ia/titulo
      → genera un título optimizado para un canal concreto.

Pensado para crecer: descripción, bullets, sugerencia de categoría por canal,
con un prompt editable por marketplace (como se planteó en el pizarrón).
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from services import claude

router = APIRouter(prefix="/api/ia", tags=["ia"])


class TituloRequest(BaseModel):
    nombre: str
    canal: str = "mercado_libre"
    contexto: str = ""


@router.post("/titulo")
def generar_titulo(req: TituloRequest):
    return claude.generar_titulo(req.nombre, req.canal, req.contexto)


@router.get("/prompts")
def prompts_por_canal():
    """Devuelve los prompts base configurados por canal."""
    return claude.PROMPTS_CANAL
