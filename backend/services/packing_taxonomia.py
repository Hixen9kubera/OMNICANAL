"""
packing_taxonomia.py — Catálogo de subcategorías/atributos y generación de SKU
con el formato de Kubera: ``SUBCAT-####-ATRIBUTO`` (ej. ``ELEC-0143-AZL``).

Portado de Aplicacion_Excel/app_v3.py (agente FERRAFORME), quitando la dependencia
de ``st.session_state``: aquí los contadores viven en un objeto explícito
(:class:`Contadores`) que el orquestador crea por packing list y siembra con lo
que ya existe en Odoo.

Regla central del numerador (la misma que ya se aplicó en Odoo):
    OFI-0001-NEG y OFI-0001-BEI son el MISMO producto en dos variantes; comparten
    el número 0001. El número solo avanza cuando aparece un producto nuevo.
"""
from __future__ import annotations

import re

SUBCATEGORIAS: dict[str, str] = {
    # MUEBLES - HOGAR
    "MUE": "Muebles Hogar",    "MES": "Mesa",           "SIL": "Silla",
    "CAM": "Cama",             "EST": "Estantería",     "ORG": "Organizador",
    "COM": "Comedor",          "ESCR": "Escritorio",    "BAÑ": "Mueble Baño",
    "JAR": "Mueble Jardín",    "TV": "Mueble TV",       "COC": "Mueble Cocina",
    "DEC": "Decoración Hogar", "ILUM": "Iluminación",   "TEX": "Textiles Hogar",
    # BEBÉS - INFANTIL
    "BEB": "Artículos Bebé",   "CUNA": "Cuna y Catre",  "PAS": "Paseo Bebé",
    "CORR": "Corral y Andadera", "ALIM": "Alimentación Bebé", "HIG": "Higiene Bebé",
    "ROBB": "Ropa Bebé",       "SEG": "Seguridad Bebé", "JUG": "Juguetes Bebé",
    # JUGUETES - JUEGOS
    "JUGU": "Juguetes",        "MUN": "Muñeca y Figura", "PEL": "Peluche",
    "VEH": "Vehículo Juguete", "CONS": "Juego Construcción", "JUEG": "Juego de Mesa",
    "CART": "Cartas y Mazo",   "ELEC": "Juguete Electrónico", "CAS": "Casa de Juguete",
    "MONT": "Juguete Montable", "DEPO": "Juguete Deportivo", "EDU": "Juguete Educativo",
    # MODA Y ROPA
    "ROP": "Ropa",             "CALZ": "Calzado",       "ACC": "Accesorio Moda",
    # TECNOLOGÍA
    "TEC": "Electrónica",      "CEL": "Celular y Accesorios",
    # HERRAMIENTAS Y OFICINA
    "HERR": "Herramienta",     "OFI": "Oficina",
    # MASCOTAS
    "MASC": "Mascota",
    # VARIOS
    "LIB": "Libro y Papelería", "ART": "Arte y Manualidad", "DEP": "Deporte y Fitness",
    "VIA": "Viaje y Equipaje",  "VAR": "Varios",
}

ATRIBUTOS: dict[str, str] = {
    # COLORES
    "NEG": "Negro",    "BLN": "Blanco",   "GRI": "Gris",     "ROJ": "Rojo",
    "AZL": "Azul",     "VER": "Verde",    "AMA": "Amarillo", "ROS": "Rosa",
    "NAR": "Naranja",  "MOR": "Morado",   "CAF": "Café",     "BEI": "Beige",
    "MUL": "Multicolor", "PLA": "Plateado", "DOR": "Dorado",
    # TALLAS
    "XS": "Extra Small", "S": "Small",    "M": "Medium",     "L": "Large",
    "XL": "Extra Large", "UNI": "Talla Única",
    # MATERIALES
    "MAD": "Madera",   "MET": "Metal",    "TEL": "Tela",     "CUE": "Cuero",
    # OTROS
    "EST": "Estándar", "PRE": "Premium",  "ECO": "Económico", "INF": "Infantil",
    "ADU": "Adulto",
}

SUBCAT_DEFAULT = "VAR"
ATTR_DEFAULT = "EST"

# SUBCAT-####-ATRIBUTO. El subcat admite la Ñ de "BAÑ".
#
# El atributo es COMPUESTO en los SKUs reales de Odoo: HERR-0032-ROJ-110V,
# HERR-0037-NEG-VER, HERR-0032-ROJ-16". La versión anterior lo limitaba a
# [A-Z0-9]{1,4} y por eso rechazaba 9 de los 10 SKUs de un contenedor real —
# con el efecto silencioso de que sku_base() devolvía el SKU entero y las
# variantes de un mismo producto dejaban de agruparse.
_RE_SKU = re.compile(r'^([A-ZÑ]{2,4})-(\d{4})(?:-(\S+))?$')
# Para leer los SKUs que ya viven en Odoo (con o sin atributo).
_RE_SKU_ODOO = re.compile(r"^([A-ZÑ]{2,4})-(\d{4})(?:-|$)")


def normalizar_subcat(cod: str | None) -> str:
    cod = (cod or "").strip().upper()
    return cod if cod in SUBCATEGORIAS else SUBCAT_DEFAULT


def normalizar_atributo(cod: str | None) -> str:
    cod = (cod or "").strip().upper()
    return cod if cod in ATRIBUTOS else ATTR_DEFAULT


def atributo_desde_valor(valor: str | None) -> str | None:
    """
    Mapea un valor libre ("Azul", "AZUL", "BLUE", "L") al código de ATRIBUTOS.
    Devuelve None si no se reconoce, para que el llamador decida el fallback.
    """
    if not valor:
        return None
    v = valor.strip().upper()
    if v in ATRIBUTOS:
        return v
    # Traducción de los tags que devuelve el LLM (en inglés) al código local.
    equivalencias = {
        "BLACK": "NEG", "WHITE": "BLN", "GRAY": "GRI", "GREY": "GRI",
        "RED": "ROJ", "BLUE": "AZL", "GREEN": "VER", "YELLOW": "AMA",
        "PINK": "ROS", "ORANGE": "NAR", "PURPLE": "MOR", "BROWN": "CAF",
        "BEIGE": "BEI", "MULTICOLOR": "MUL", "SILVER": "PLA", "GOLD": "DOR",
        "WOOD": "MAD", "METAL": "MET", "FABRIC": "TEL", "LEATHER": "CUE",
    }
    if v in equivalencias:
        return equivalencias[v]
    for cod, desc in ATRIBUTOS.items():
        if desc.upper() == v:
            return cod
    return None


class Contadores:
    """
    Numerador por subcategoría. Se siembra con el máximo que ya existe en Odoo
    para que los SKUs nuevos nunca pisen los publicados.
    """

    def __init__(self, iniciales: dict[str, int] | None = None):
        self._max: dict[str, int] = dict(iniciales or {})

    @classmethod
    def desde_odoo(cls, skus_odoo: list[str]) -> "Contadores":
        maximos: dict[str, int] = {}
        for sku in skus_odoo:
            m = _RE_SKU_ODOO.match((sku or "").strip().upper())
            if not m:
                continue
            sub, num = m.group(1), int(m.group(2))
            if maximos.get(sub, 0) < num:
                maximos[sub] = num
        return cls(maximos)

    def siguiente(self, subcat: str) -> int:
        """Reserva y devuelve el siguiente número libre de la subcategoría."""
        sub = normalizar_subcat(subcat)
        self._max[sub] = self._max.get(sub, 0) + 1
        return self._max[sub]

    def maximo(self, subcat: str) -> int:
        return self._max.get(normalizar_subcat(subcat), 0)

    def as_dict(self) -> dict[str, int]:
        return dict(self._max)


def codigo_atributo_compuesto(tag: str) -> str:
    """
    Tag libre del LLM → código de atributo con la forma que ya usa Odoo.

    Los SKUs reales combinan color + especificación técnica:
    ``ROJ-110V``, ``AMA-21V``, ``AZL-127V``, ``NEG-VER``. El proveedor, en
    cambio, escribe cosas como ``RED-BLACK-A-2BAT`` o ``16.8V-RED-BLACK-2BAT``.

    Se toma el primer token que sea un color/material conocido y el primer token
    que parezca especificación (trae dígito), y se unen con guión::

        RED-BLACK-A-2BAT      → ROJ-2BAT
        RED-BLACK-12IN-B      → ROJ-12IN
        GREEN-2BAT            → VER-2BAT
        16.8V-RED-BLACK-2BAT  → ROJ-168V

    Truncar a 4 caracteres (lo que se hacía antes) colapsaba los dos primeros en
    el mismo código y había que desambiguar con un número sin significado.
    """
    tokens = [t for t in re.split(r"[-_\s/]+", (tag or "").upper()) if t]
    conocido: str | None = None
    tecnico: str | None = None
    for t in tokens:
        if conocido is None and (cod := atributo_desde_valor(t)):
            conocido = cod
            continue
        if tecnico is None and any(c.isdigit() for c in t):
            tecnico = re.sub(r"[^A-Z0-9]", "", t)[:5] or None
    partes = [p for p in (conocido, tecnico) if p]
    if partes:
        return "-".join(partes)
    # Sin color ni especificación reconocibles: el tag limpio, acotado.
    limpio = re.sub(r"[^A-Z0-9]", "", (tag or "").upper())[:6]
    return limpio or ATTR_DEFAULT


def construir_sku(subcat: str, numero: int, atributo: str | None = None) -> str:
    """
    Arma el SKU. Sin atributo devuelve ``SUBCAT-####`` (producto único); con
    atributo, ``SUBCAT-####-ATR`` (variante).
    """
    sub = normalizar_subcat(subcat)
    if atributo is None:
        return f"{sub}-{numero:04d}"
    return f"{sub}-{numero:04d}-{normalizar_atributo(atributo)}"


def partes_sku(sku: str) -> tuple[str, int, str | None] | None:
    """Descompone un SKU en (subcat, numero, atributo|None). None si no matchea."""
    m = _RE_SKU.match((sku or "").strip().upper())
    if not m:
        return None
    return m.group(1), int(m.group(2)), m.group(3)


def sku_base(sku: str) -> str:
    """``ELEC-0143-AZL`` → ``ELEC-0143``. Es la clave que agrupa variantes."""
    p = partes_sku(sku)
    return f"{p[0]}-{p[1]:04d}" if p else (sku or "").strip().upper()


def validar_vs_odoo(sku_propuesto: str, skus_odoo: set[str]) -> dict:
    """
    Verifica el SKU propuesto contra el universo de SKUs de Odoo.

    Como el número lo comparten todas las variantes de un producto, el conflicto
    se evalúa sobre ``SUBCAT-####``: si Odoo ya tiene ese número (con cualquier
    atributo), el propuesto está ocupado.

    Devuelve {conflicto, coincidencias, max_odoo}.
    """
    p = partes_sku(sku_propuesto)
    if not p:
        return {"conflicto": False, "coincidencias": [], "max_odoo": 0}
    subcat, numero, _ = p
    prefijo = f"{subcat}-{numero:04d}"
    coincidencias = [
        s for s in skus_odoo
        if s.upper() == prefijo or s.upper().startswith(prefijo + "-")
    ]
    patron_sub = re.compile(rf"^{re.escape(subcat)}-(\d{{4}})(?:-|$)")
    numeros = [int(m.group(1)) for s in skus_odoo if (m := patron_sub.match(s.upper()))]
    return {
        "conflicto": bool(coincidencias),
        "coincidencias": sorted(coincidencias),
        "max_odoo": max(numeros) if numeros else 0,
    }
