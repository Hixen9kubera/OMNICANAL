"""
ov_storage.py — Los PDF de las órdenes de venta propias en el bucket privado
``ordenes-venta`` de Supabase.

⚠️ EL BUCKET TODAVÍA NO EXISTE. La migración 0064 revisada YA NO lo crea: va
aparte (privado, 15 MB, sólo ``application/pdf``, sin políticas), con su
retención decidida. Mientras no exista, ``ops.ov_archivos`` no tiene escritor:
:func:`services.ordenes_venta.hay_bucket` lo pregunta en ``storage.buckets`` y
el servicio contesta un 409 que lo dice ANTES de llegar aquí. Este módulo no
pregunta nada: sólo habla con Storage cuando el servicio ya decidió que puede.

QUÉ SE GUARDA: comprobantes, facturas y documentos de envío a FULL
(``ops.ov_archivos.tipo``). **Las guías con la dirección del comprador NO se
guardan en kubera** (decisión D5): se piden a la API del canal al imprimir.

Cliente mínimo de la API de Storage por HTTP crudo con ``requests``, igual que
:mod:`packing_storage`, para no añadir dependencias. Es un módulo aparte —y no
un parámetro más de aquél— porque las reglas del bucket son otras:

1. **Aquí SÍ se borra** (sólo admin, y deja rastro en
   ``ops.ov_archivos.borrado_at``); un packing list no se pierde nunca.
2. **Nunca upsert**, como allá: el objeto se nombra por su sha256
   (``<folio>/<sha256>.pdf``), así que «ya existe» significa «ya está ese mismo
   contenido» y se toma como éxito, no como error.
3. **Sólo con service_role y sólo por el backend.** El bucket es privado y no
   tiene políticas. Nada de aquí termina en una URL pública ni firmada: el PDF
   sale por ``GET /api/ordenes-venta/{id}/archivos/{archivo_id}`` con sesión.
4. **Sin subida resumible.** El bucket topa en 15 MB por archivo; un POST de ese
   tamaño pasa entero. (La TUS de los packing lists es por sus 120 MB.)

EL ORDEN CON LA BASE LO PONE QUIEN LLAMA (Storage y Postgres no comparten
transacción; guía de la 0064/0065 §3.7): **subir** el objeto y DESPUÉS insertar
la fila; marcar ``borrado_at`` y DESPUÉS **borrar** el objeto. Así lo único que
puede quedar suelto es un objeto huérfano, nunca una fila que apunta a nada. Y
ninguna de estas tres funciones se llama dentro de una transacción abierta.

Bloqueante a propósito: lo llama el servicio síncrono
(:mod:`services.ordenes_venta`), que el router ya mete en un hilo (regla 11).
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("omnicanal.ordenes_venta.storage")

BUCKET = "ordenes-venta"
MIME = "application/pdf"
TIMEOUT = 30


class StorageError(RuntimeError):
    """Storage contestó algo que no es éxito (ni «ya existe» / «ya no estaba»), o no contestó."""


def _cred() -> tuple[str, dict[str, str]]:
    """URL base y encabezados del backend (settings). Se lee al usarse, no al importar."""
    from config import settings
    url, llave = settings.supabase_url, settings.supabase_service_role_key
    if not (url and llave):
        raise StorageError("Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY.")
    return url.rstrip("/"), {"Authorization": f"Bearer {llave}", "apikey": llave}


def _ya_existe(r: requests.Response) -> bool:
    # Según la versión, Storage contesta 409 o un 400 con statusCode "409" en el cuerpo.
    return r.status_code == 409 or (
        r.status_code == 400 and ("Duplicate" in r.text or "already exists" in r.text))


def _no_estaba(r: requests.Response) -> bool:
    # Lo mismo al revés: 404, o un 400 con statusCode "404" / «not_found» en el cuerpo.
    return r.status_code == 404 or (
        r.status_code == 400 and ("not_found" in r.text or "Object not found" in r.text
                                  or '"404"' in r.text))


def _pedir(metodo: str, url: str, que: str, **kw) -> requests.Response:
    """Una petición a Storage. Que no conteste es un StorageError, no un error de red suelto."""
    try:
        return requests.request(metodo, url, timeout=TIMEOUT, **kw)
    except requests.RequestException as exc:
        raise StorageError(f"Storage no contestó al {que}: {exc}") from exc


def subir(ruta: str, datos: bytes) -> str:
    """Sube el PDF a ``ruta`` sin pisar nada. Devuelve ``'subido'`` o ``'existe'``."""
    base, h = _cred()
    r = _pedir("POST", f"{base}/storage/v1/object/{BUCKET}/{ruta}", f"subir {ruta}", data=datos,
               headers={**h, "Content-Type": MIME, "x-upsert": "false"})
    if r.ok:
        return "subido"
    if _ya_existe(r):
        return "existe"
    raise StorageError(f"HTTP {r.status_code} al subir {ruta}: {r.text[:300]}")


def bajar(ruta: str) -> bytes:
    """El PDF de ``ruta``. Lanza :class:`StorageError` si no está."""
    base, h = _cred()
    r = _pedir("GET", f"{base}/storage/v1/object/authenticated/{BUCKET}/{ruta}",
               f"bajar {ruta}", headers=h)
    if not r.ok:
        raise StorageError(f"HTTP {r.status_code} al bajar {ruta}: {r.text[:300]}")
    return r.content


def borrar(ruta: str) -> None:
    """Quita el PDF de ``ruta``. Que ya no estuviera NO es un error: el fin es que no esté."""
    base, h = _cred()
    r = _pedir("DELETE", f"{base}/storage/v1/object/{BUCKET}/{ruta}", f"borrar {ruta}", headers=h)
    if r.ok or _no_estaba(r):
        return
    raise StorageError(f"HTTP {r.status_code} al borrar {ruta}: {r.text[:300]}")
