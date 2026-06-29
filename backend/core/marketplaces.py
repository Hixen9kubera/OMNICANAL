"""
marketplaces.py — Registro central de canales (marketplaces).

Fuente única de verdad para:
  - el id interno del canal (usado en la API y la DB)
  - la etiqueta visible
  - el color de marca (el frontend cambia de tema según el canal activo)
  - si está habilitado (con credenciales) o es "próximamente"
  - el origen de datos (woocommerce | db | ejemplo)

El frontend consume /api/canales para pintar las pestañas, así que cualquier
cambio aquí se refleja automáticamente en la interfaz.
"""
from __future__ import annotations

from enum import Enum
from typing import TypedDict


class Canal(str, Enum):
    GENERAL = "general"
    MERCADO_LIBRE = "mercado_libre"
    AMAZON = "amazon"
    TIKTOK = "tiktok"
    WALMART = "walmart"
    TEMU = "temu"
    SHEIN = "shein"


class Origen(str, Enum):
    WOOCOMMERCE = "woocommerce"  # vista GENERAL: productos en vivo
    DB = "db"                    # cache híbrido (MySQL) con refresco por API
    EJEMPLO = "ejemplo"          # datos de muestra (sin credenciales aún)


class CanalConfig(TypedDict):
    id: str
    label: str
    color: str        # color principal de marca
    color_texto: str  # color de texto legible sobre el principal
    acento: str       # color de acento secundario
    habilitado: bool
    origen: str
    descripcion: str


# Colores de marca oficiales de cada marketplace.
MARKETPLACES: dict[str, CanalConfig] = {
    Canal.GENERAL: {
        "id": Canal.GENERAL,
        "label": "General",
        "color": "#4F46E5",       # índigo Kubera
        "color_texto": "#FFFFFF",
        "acento": "#818CF8",
        "habilitado": True,
        "origen": Origen.WOOCOMMERCE,
        "descripcion": "Todas las publicaciones de WooCommerce (chunche.shop), tu centro.",
    },
    Canal.MERCADO_LIBRE: {
        "id": Canal.MERCADO_LIBRE,
        "label": "Mercado Libre",
        "color": "#FFE600",       # amarillo ML
        "color_texto": "#2D3277",  # azul ML
        "acento": "#3483FA",
        "habilitado": True,
        "origen": Origen.DB,
        "descripcion": "Publicaciones en Mercado Libre México (MLM).",
    },
    Canal.AMAZON: {
        "id": Canal.AMAZON,
        "label": "Amazon",
        "color": "#FF9900",       # naranja Amazon
        "color_texto": "#131A22",  # navy Amazon
        "acento": "#232F3E",
        "habilitado": True,
        "origen": Origen.DB,
        "descripcion": "Listings en Amazon México (San Corpe) vía SP-API.",
    },
    Canal.TIKTOK: {
        "id": Canal.TIKTOK,
        "label": "TikTok Shop",
        "color": "#000000",
        "color_texto": "#FFFFFF",
        "acento": "#FE2C55",      # rojo/rosa TikTok
        "habilitado": False,
        "origen": Origen.EJEMPLO,
        "descripcion": "Próximamente — pendiente de credenciales.",
    },
    Canal.WALMART: {
        "id": Canal.WALMART,
        "label": "Walmart",
        "color": "#0071DC",       # azul Walmart
        "color_texto": "#FFFFFF",
        "acento": "#FFC220",      # amarillo Walmart
        "habilitado": False,
        "origen": Origen.EJEMPLO,
        "descripcion": "Próximamente — pendiente de credenciales.",
    },
    Canal.TEMU: {
        "id": Canal.TEMU,
        "label": "Temu",
        "color": "#FB7701",       # naranja Temu
        "color_texto": "#FFFFFF",
        "acento": "#FF5000",
        "habilitado": False,
        "origen": Origen.EJEMPLO,
        "descripcion": "Próximamente — pendiente de credenciales.",
    },
    Canal.SHEIN: {
        "id": Canal.SHEIN,
        "label": "Shein",
        "color": "#000000",
        "color_texto": "#FFFFFF",
        "acento": "#7C3AED",
        "habilitado": False,
        "origen": Origen.EJEMPLO,
        "descripcion": "Próximamente — pendiente de credenciales.",
    },
}

class SubCuenta(TypedDict):
    id: str
    label: str
    es_default: bool


# Cuentas/sub-canales por marketplace. Mercado Libre opera 2 cuentas:
#   BEKURA        → "Kubera"   (cuenta principal / default)
#   SANCORFASHION → "San Corpe"
CUENTAS: dict[str, list[SubCuenta]] = {
    Canal.MERCADO_LIBRE: [
        {"id": "BEKURA", "label": "Kubera", "es_default": True},
        {"id": "SANCORFASHION", "label": "San Corpe", "es_default": False},
    ],
}


def subcuentas(canal: str) -> list[SubCuenta]:
    return CUENTAS.get(canal, [])


def cuenta_default(canal: str) -> str | None:
    for c in CUENTAS.get(canal, []):
        if c.get("es_default"):
            return c["id"]
    return None


# Orden en que aparecen las pestañas en la interfaz.
ORDEN_CANALES: list[str] = [
    Canal.GENERAL,
    Canal.MERCADO_LIBRE,
    Canal.AMAZON,
    Canal.TIKTOK,
    Canal.WALMART,
    Canal.TEMU,
    Canal.SHEIN,
]


def lista_canales() -> list[CanalConfig]:
    """Devuelve la config de canales en orden de presentación."""
    return [MARKETPLACES[c] for c in ORDEN_CANALES]


def es_canal_valido(canal: str) -> bool:
    return canal in MARKETPLACES


def config_canal(canal: str) -> CanalConfig | None:
    return MARKETPLACES.get(canal)
