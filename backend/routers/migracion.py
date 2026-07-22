"""
migracion.py — Endpoints del panel /migracion (espejo kubera en tiempo real).

Sirven la interfaz que muestra el avance del dual-write hacia la BD
centralizada "kubera" a nivel archivo.py → tabla (services/kubera_mirror.py):

  GET  /api/migracion/estado            censo + contadores + flags
  GET  /api/migracion/eventos           ring buffer (últimos 500 intentos)
  GET  /api/migracion/errores           errores agrupados (plan de limpieza)
  POST /api/migracion/errores/resolver  marca un grupo como resuelto

Todo es de LECTURA salvo el POST, que solo marca filas de la bitácora local
`espejo_kubera_log` (MySQL) como resueltas — lleva la API key del piloto.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.seguridad import requiere_api_key
from services import kubera_mirror

router = APIRouter(prefix="/api/migracion", tags=["migracion"])


@router.get("/estado")
def estado():
    return kubera_mirror.estado()


@router.get("/eventos")
def eventos(limit: int = 100):
    return {"eventos": kubera_mirror.eventos(limit)}


@router.get("/errores")
def errores(incluir_resueltos: bool = False):
    return {"grupos": kubera_mirror.errores_agrupados(incluir_resueltos)}


class ResolverGrupo(BaseModel):
    archivo_py: str
    tabla_origen: str
    error_tipo: str


@router.post("/errores/resolver", dependencies=[Depends(requiere_api_key)])
def resolver(grupo: ResolverGrupo):
    n = kubera_mirror.resolver_grupo(
        grupo.archivo_py, grupo.tabla_origen, grupo.error_tipo)
    return {"resueltos": n}
