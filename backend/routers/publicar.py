"""
publicar.py — Paso 4: actualizar la publicación en el canal seleccionado.

  POST /api/publicar/preview     → arma y devuelve el payload (NO escribe nada)
  POST /api/publicar/confirmar   → ejecuta el update en vivo + registra en bitácora
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services import publicar

router = APIRouter(prefix="/api/publicar", tags=["publicar"])


class AtributoIn(BaseModel):
    nombre: str
    valor: str = ""


class CamposPublicar(BaseModel):
    titulo: str | None = None
    descripcion: str | None = None
    highlights: str | None = None
    bullets: list[str] = Field(default_factory=list)
    atributos: list[AtributoIn] = Field(default_factory=list)
    # Datos usados al CREAR en Amazon (precio y dimensiones)
    precio_regular: float | None = None
    peso: float | None = None
    largo: float | None = None
    ancho: float | None = None
    alto: float | None = None


class PublicarRequest(BaseModel):
    canal: str = "mercado_libre"
    cuenta: str | None = None
    sku: str | None = None
    wc_id: int | None = None
    item_id: str | None = None
    campos: CamposPublicar = Field(default_factory=CamposPublicar)

    def a_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        d["campos"]["atributos"] = [a for a in d["campos"]["atributos"]]
        return d


@router.post("/preview")
def preview(req: PublicarRequest) -> dict[str, Any]:
    return publicar.preview(req.a_dict())


@router.post("/confirmar")
async def confirmar(req: PublicarRequest) -> dict[str, Any]:
    return await publicar.confirmar(req.a_dict())
