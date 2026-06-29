"""
claude.py — Generación de contenido con IA (Claude / Anthropic).

Sirve para enriquecer listings al publicar en cada marketplace: título
optimizado, bullet points, descripción y sugerencia de categoría. Cada canal
puede tener su propio prompt (ML, Amazon, etc.), tal como se planteó en el
pizarrón ("cada canal con su propio prompt editable").

En esta primera versión exponemos un generador de títulos/descripciones; queda
listo para conectarse desde el router /api/ia.
"""
from __future__ import annotations

import logging
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.claude")

_MODELO = "claude-opus-4-8"  # modelo Claude más capaz disponible

# Prompts base por canal (editables / ampliables por tienda).
PROMPTS_CANAL = {
    "mercado_libre": (
        "Eres experto en publicaciones de Mercado Libre México. Genera un título "
        "de máximo 60 caracteres, atractivo y con palabras clave de búsqueda."
    ),
    "amazon": (
        "Eres experto en listings de Amazon. Genera un título y 5 bullet points "
        "siguiendo las guías de estilo de Amazon."
    ),
    "tiktok": (
        "Eres experto en TikTok Shop. Genera un título corto y llamativo, "
        "orientado a contenido viral."
    ),
}


def _cliente():
    try:
        import anthropic  # import perezoso para no romper si no está instalado
        return anthropic.Anthropic(api_key=settings.anthropic_api_key)
    except Exception as exc:  # noqa: BLE001
        log.warning("Anthropic no disponible: %s", exc)
        return None


def generar_titulo(nombre: str, canal: str = "mercado_libre", contexto: str = "") -> dict[str, Any]:
    """Genera un título optimizado para el canal indicado."""
    cli = _cliente()
    if not cli or not settings.anthropic_api_key:
        return {"ok": False, "titulo": nombre, "motivo": "ANTHROPIC_API_KEY no configurada"}

    system = PROMPTS_CANAL.get(canal, PROMPTS_CANAL["mercado_libre"])
    try:
        msg = cli.messages.create(
            model=_MODELO,
            max_tokens=300,
            system=system,
            messages=[{
                "role": "user",
                "content": f"Producto: {nombre}\nContexto: {contexto}\n"
                           f"Devuelve solo el título optimizado.",
            }],
        )
        titulo = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        return {"ok": True, "titulo": titulo, "canal": canal, "modelo": _MODELO}
    except Exception as exc:  # noqa: BLE001
        log.error("Generación IA falló: %s", exc)
        return {"ok": False, "titulo": nombre, "motivo": str(exc)}
