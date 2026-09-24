"""
packing_storage.py — Los packing lists en el bucket privado ``packing-lists`` de
Supabase (migración 0055).

Cliente mínimo de la API de Storage por HTTP crudo con ``requests``, como
:mod:`packing_drive`, para no añadir dependencias. Tres reglas que no se
negocian, y las tres salen de cómo funciona Storage, no de gusto:

1. **Nunca upsert.** Storage no versiona objetos y no entra en el respaldo
   diario de la base: un objeto pisado o borrado no se recupera. Por eso el
   objeto se nombra por su sha256 (``<tipo>/<sha256>.<ext>``) y "ya existe"
   significa "ya está ese mismo contenido": se toma como éxito, no como error.
2. **Solo con service_role.** El bucket es privado y no tiene políticas: anon y
   authenticated no ven nada. Nada de aquí debe terminar en una URL firmada para
   el navegador sin decidirlo antes — son precios de proveedor.
3. **Arriba de 6 MB, subida resumible (TUS)** en trozos de 6 MB exactos, que es
   lo que pide Supabase. Hay packing lists de 120 MB (casi todo el peso son las
   fotos embebidas) y un POST de ese tamaño se corta a medias.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import time

import requests

log = logging.getLogger("omnicanal.packing.storage")

BUCKET = "packing-lists"
TROZO_TUS = 6 * 1024 * 1024
TIMEOUT = 300
MIME = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "zip": "application/zip",
}


class StorageError(RuntimeError):
    """Storage contestó algo que no es éxito ni "ya existe"."""


def huella(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def ruta_de(tipo: str, sha256: str, ext: str) -> str:
    return f"{tipo}/{sha256}.{ext}"


def _cred(url: str | None, llave: str | None) -> tuple[str, dict[str, str]]:
    """URL base y encabezados. Sin argumentos, los del backend (settings)."""
    if not (url and llave):
        from config import settings
        url = url or settings.supabase_url
        llave = llave or settings.supabase_service_role_key
    if not (url and llave):
        raise StorageError("Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY.")
    return url.rstrip("/"), {"Authorization": f"Bearer {llave}", "apikey": llave}


def _ya_existe(r: requests.Response) -> bool:
    # Según la versión, Storage contesta 409 o un 400 con statusCode "409" en el cuerpo.
    return r.status_code == 409 or (
        r.status_code == 400 and ("Duplicate" in r.text or "already exists" in r.text))


def subir(ruta: str, datos: bytes, ext: str, *, url: str | None = None,
          llave: str | None = None) -> str:
    """Sube ``datos`` a ``ruta`` sin pisar nada. Devuelve ``'subido'`` o ``'existe'``."""
    base, h = _cred(url, llave)
    if len(datos) > TROZO_TUS:
        return _subir_tus(base, h, ruta, datos, MIME[ext])
    r = requests.post(f"{base}/storage/v1/object/{BUCKET}/{ruta}", data=datos,
                      headers={**h, "Content-Type": MIME[ext], "x-upsert": "false"},
                      timeout=TIMEOUT)
    if r.ok:
        return "subido"
    if _ya_existe(r):
        return "existe"
    raise StorageError(f"HTTP {r.status_code} al subir {ruta}: {r.text[:300]}")


def _subir_tus(base: str, h: dict[str, str], ruta: str, datos: bytes, mime: str) -> str:
    meta = {"bucketName": BUCKET, "objectName": ruta, "contentType": mime,
            "cacheControl": "3600"}
    r = requests.post(
        f"{base}/storage/v1/upload/resumable",
        headers={**h, "Tus-Resumable": "1.0.0", "Upload-Length": str(len(datos)),
                 "x-upsert": "false",
                 "Upload-Metadata": ",".join(
                     f"{k} {base64.b64encode(v.encode()).decode()}" for k, v in meta.items())},
        timeout=60)
    if _ya_existe(r):
        return "existe"
    if r.status_code != 201 or not r.headers.get("Location"):
        raise StorageError(f"HTTP {r.status_code} al abrir la subida de {ruta}: {r.text[:300]}")
    destino = r.headers["Location"]
    if destino.startswith("/"):
        destino = base + destino
    tus = {**h, "Tus-Resumable": "1.0.0"}

    offset, fallos = 0, 0
    while offset < len(datos):
        try:
            r = requests.patch(destino, data=datos[offset:offset + TROZO_TUS], timeout=TIMEOUT,
                               headers={**tus, "Upload-Offset": str(offset),
                                        "Content-Type": "application/offset+octet-stream"})
            if r.status_code != 204:
                raise StorageError(f"HTTP {r.status_code}: {r.text[:200]}")
            offset = int(r.headers["Upload-Offset"])
            fallos = 0
        except (requests.RequestException, StorageError) as exc:
            # Un trozo que falla se retoma desde lo que el servidor SÍ recibió.
            fallos += 1
            if fallos > 3:
                raise StorageError(f"La subida de {ruta} falló en el byte {offset}: {exc}")
            time.sleep(2 * fallos)
            try:
                offset = int(requests.head(destino, headers=tus, timeout=60)
                             .headers["Upload-Offset"])
            except Exception:  # noqa: BLE001 — se reintenta con el offset que teníamos
                pass
    return "subido"


def bajar(ruta: str, *, url: str | None = None, llave: str | None = None) -> bytes:
    """El objeto de ``ruta``. Lanza :class:`StorageError` si no está."""
    base, h = _cred(url, llave)
    r = requests.get(f"{base}/storage/v1/object/authenticated/{BUCKET}/{ruta}",
                     headers=h, timeout=TIMEOUT)
    if not r.ok:
        raise StorageError(f"HTTP {r.status_code} al bajar {ruta}: {r.text[:300]}")
    return r.content


# ── Lecturas del backend (índice costing.packing_archivos) ──────────────────
# `supabase_db` se importa adentro: el script de copia usa este módulo sin
# levantar el pool del backend.

def _bajar_verificado(fila: dict | None) -> bytes | None:
    if not fila:
        return None
    datos = bajar(fila["ruta"])
    # Un objeto que no cuadra con su huella es peor que no tenerlo: se leería
    # un costo de un archivo que nadie validó.
    if huella(datos) != fila["sha256"]:
        raise StorageError(f"{fila['ruta']}: la huella no cuadra con el índice")
    return datos


def bajar_vigente(drive_file_id: str, tipo: str = "original") -> bytes | None:
    """La última versión copiada de ese archivo de Drive; ``None`` si no hay.

    "Última" es la del ``modifiedTime`` de Drive más reciente que se copió: si
    alguien editó el archivo después de la última copia, aquí sale la copia —
    que es la que se puede reproducir— hasta que se vuelva a correr
    ``copiar_packing_lists.py``. Los miembros de zip no entran: un file_id que
    era un zip no dice cuál de sus hojas se quería.
    """
    from services import supabase_db as sdb
    return _bajar_verificado(sdb.fetch_one(
        """select ruta, sha256 from costing.packing_archivos
            where drive_file_id = %s and tipo = %s and miembro is null
            order by drive_modified_at desc nulls last, id desc
            limit 1""", (drive_file_id, tipo)))


def bajar_por_huella(sha256: str) -> bytes | None:
    """La versión EXACTA con esa huella; ``None`` si no se ha copiado."""
    from services import supabase_db as sdb
    return _bajar_verificado(sdb.fetch_one(
        "select ruta, sha256 from costing.packing_archivos where sha256 = %s limit 1",
        (sha256,)))


def inventario(tipo: str = "original") -> dict[str, str]:
    """``{drive_file_id: nombre}`` de lo copiado, con el nombre de la versión más reciente."""
    from services import supabase_db as sdb
    return {f["drive_file_id"]: f["nombre"] for f in sdb.fetch_all(
        """select distinct on (drive_file_id) drive_file_id, nombre
             from costing.packing_archivos
            where tipo = %s and drive_file_id is not null
            order by drive_file_id, drive_modified_at desc nulls last, id desc""", (tipo,))}
