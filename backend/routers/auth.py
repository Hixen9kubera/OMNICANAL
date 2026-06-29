"""
auth.py — Autenticación (placeholder).

En esta primera versión la app es interna. Dejamos el esqueleto listo para
añadir login/JWT más adelante (p. ej. integrando con Supabase o usuarios de
Kubera). Por ahora solo expone el estado de sesión simulado.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
def me():
    return {
        "autenticado": True,
        "usuario": "kubera",
        "rol": "admin",
        "nota": "Autenticación real pendiente (JWT/Supabase).",
    }
