"""
catalogo.py — La pestaña CATÁLOGO (v0.703.0): el artifact del catálogo vivo —el inventario contado desde los
packing lists y valuado a precio de Mercado Libre y de Amazon— dentro del panel.

QUÉ ES
    Una FOTO del artifact (claude.ai/artifact/QGPgvZBTfBC2RMQd12n2C6, versión 18), no datos en vivo:
    `backend/data/catalogo/catalogo.json.gz` y las hojas de fotos `backend/data/catalogo/img/f*.webp`, que arma
    `scripts/catalogo_preparar.py`. El artifact NO se toca: es el respaldo de los datos (Brandon, 29-oct-2026).
    Para actualizar la pestaña se vuelve a correr el script y se despliega.

    Diferencias contra el artifact, a propósito:
      · La foto de cada fila es la ORIGINAL de su renglón del packing list (pedido de Brandon, 29-oct-2026); la de
        Odoo solo queda donde la fila no tiene renglón con foto. Cada fila dice de dónde salió (`fo`).
      · Sin costos: el repo es público. Faltan el costo por pieza, el precio del proveedor en dólares y el múltiplo
        de los precios estimados por costo (con ese múltiplo se despejaría el costo).

    El JSON se manda TAL CUAL está en disco, comprimido (`Content-Encoding: gzip`): ~13 MB abiertos que viajan en
    ~2.5 MB. El navegador lo abre y la pantalla hace las cuentas, como el artifact.

SOLO LECTURA de archivos del repo. Se leen una vez por proceso y se quedan en memoria: solo cambian con un deploy.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

CARPETA = Path(__file__).resolve().parent.parent / "data" / "catalogo"
NOMBRE_HOJA = re.compile(r"^f\d{1,4}$")


class SinCatalogo(Exception):
    """Falta el archivo (o la hoja pedida no existe)."""


_cache: dict[str, bytes] = {}
_lock = threading.Lock()


def _leer(relativa: str) -> bytes:
    """BLOQUEANTE (disco, la primera vez de cada archivo)."""
    with _lock:
        if relativa not in _cache:
            ruta = CARPETA / relativa
            if not ruta.is_file():
                raise SinCatalogo(f"falta {relativa}")
            _cache[relativa] = ruta.read_bytes()
        return _cache[relativa]


def datos_gz() -> bytes:
    """BLOQUEANTE. El catálogo completo, comprimido con gzip tal como está en el repo."""
    return _leer("catalogo.json.gz")


def hoja(nombre: str) -> bytes:
    """BLOQUEANTE. Una hoja de fotos (`f0`, `f12`…), WebP. Nombre inválido → SinCatalogo."""
    if not NOMBRE_HOJA.match(nombre or ""):
        raise SinCatalogo(f"hoja desconocida: {nombre!r}")
    return _leer(f"img/{nombre}.webp")


def olvidar() -> None:
    """Vacía la caché (pruebas, o tras dejar archivos nuevos en la carpeta)."""
    with _lock:
        _cache.clear()
