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

LO PUBLICADO EN MERCADO LIBRE (v0.708.0)
    Lo único que NO es foto: `publicados()` dice qué SKUs están publicados HOY en ML, por cuenta, con la definición
    única del panel (`channel_read.publicados_ml_todos`: activa o pausada, la misma de Costos) y la hora del último
    sync de 15 min (`frescura_listings_ml`). Un SELECT a kubera, cinco minutos en memoria. Es para el filtro y la
    columna «Publicado» de la pestaña (Brandon, 30-oct-2026).

EL PACKING LIST PARA LA IA (v0.706.0)
    «Mejorar con IA» de Mercado Libre lee de aquí el título del renglón del packing list (`titulos_pl`: lo que se le
    compró al proveedor) y, si el producto no tiene foto en la tienda, la foto de ese renglón (`foto_pl`, recortada de
    las hojas). Solo se LEEN; no se suben a Woo, a Odoo ni al artifact (regla de Brandon, 29-oct-2026: esa foto vive
    en la pestaña).
"""
from __future__ import annotations

import gzip
import io
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

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


_indice: dict[str, Any] | None = None
_lock_indice = threading.Lock()


def _indice_pl() -> dict[str, Any]:
    """BLOQUEANTE, la primera vez: {lado, cols, por_hoja, foto: {SKU: índice}, titulos: {SKU: [..]}}. `foto` solo
    lleva las filas cuya foto ES la del packing list (`fo == "pl"`). Del catálogo abierto se queda esto, no los ~14 MB."""
    global _indice
    with _lock_indice:
        if _indice is None:
            datos = json.loads(gzip.decompress(datos_gz()))
            f = (datos.get("meta") or {}).get("fotos") or {}
            fotos: dict[str, int] = {}
            titulos: dict[str, list[str]] = {}
            for r in datos.get("R") or []:
                sku = str(r.get("s") or "").strip().upper()
                if not sku or r.get("sin"):
                    continue
                if r.get("fo") == "pl" and r.get("f") is not None:
                    fotos.setdefault(sku, int(r["f"]))
                if r.get("pt"):
                    titulos.setdefault(sku, [str(t) for t in r["pt"] if t])
            _indice = {"lado": int(f.get("lado") or 192), "cols": int(f.get("cols") or 10),
                       "por_hoja": int(f.get("por_hoja") or 100), "foto": fotos, "titulos": titulos}
        return _indice


def titulos_pl(sku: str) -> list[str]:
    """BLOQUEANTE. El título de los renglones del packing list del SKU (del proveedor: inglés o chino), o []."""
    return list(_indice_pl()["titulos"].get((sku or "").strip().upper()) or [])


def foto_pl(sku: str) -> bytes | None:
    """BLOQUEANTE. JPEG de la foto del renglón del packing list del SKU (192 px), o None si no tiene."""
    from PIL import Image

    idx = _indice_pl()
    i = idx["foto"].get((sku or "").strip().upper())
    if i is None:
        return None
    lado, cols = idx["lado"], idx["cols"]
    h, c = divmod(i, idx["por_hoja"])
    x, y = (c % cols) * lado, (c // cols) * lado
    im = Image.open(io.BytesIO(hoja(f"f{h}"))).convert("RGB").crop((x, y, x + lado, y + lado))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


PUBLICADOS_TTL = 300   # segundos: el sync que alimenta `channel.listings` corre cada 15 min
_publicados: dict[str, Any] = {"t": 0.0, "dato": None}
_lock_publicados = threading.Lock()


def publicados() -> dict[str, Any]:
    """BLOQUEANTE (kubera). {actualizado, ml: {SKU: [[cuenta, situacion]]}} de lo publicado HOY en Mercado Libre.

    `situacion` es `active` o `paused`. Un SKU que no viene no está publicado en ML. `actualizado` es la hora del
    último sync: si el sync se detiene, la pantalla lo enseña en vez de contestar con una foto vieja."""
    with _lock_publicados:
        if _publicados["dato"] is not None and time.monotonic() - _publicados["t"] < PUBLICADOS_TTL:
            return _publicados["dato"]
    from services import channel_read
    todos = channel_read.publicados_ml_todos()
    dato = {"actualizado": channel_read.frescura_listings_ml(),
            "ml": {sku: [[p["cuenta"], p["situacion"]] for p in pubs] for sku, pubs in todos.items()}}
    with _lock_publicados:
        _publicados.update(t=time.monotonic(), dato=dato)
    return dato


def olvidar() -> None:
    """Vacía la caché (pruebas, o tras dejar archivos nuevos en la carpeta)."""
    global _indice
    with _lock:
        _cache.clear()
    with _lock_indice:
        _indice = None
    with _lock_publicados:
        _publicados.update(t=0.0, dato=None)
