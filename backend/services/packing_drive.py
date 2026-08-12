"""
packing_drive.py — Descarga un packing list desde una liga de Google Drive.

Sirve para los archivos compartidos como **"cualquiera con el enlace"**: se saca
el FILE_ID de la URL y se baja por el endpoint público de Drive, sin credenciales
ni OAuth. Un archivo privado NO se puede bajar así — Drive devuelve la página de
"solicitar acceso" en vez del xlsx, y este módulo lo detecta y lo dice claro en
vez de dejar que el parser reviente con un error incomprensible.

Si algún día hacen falta los privados o procesar una carpeta entera, eso ya
requiere la Drive API con service account, que es otro módulo.

Formas de URL que se aceptan::

    https://drive.google.com/file/d/<ID>/view?usp=sharing
    https://drive.google.com/open?id=<ID>
    https://drive.google.com/uc?export=download&id=<ID>
    https://docs.google.com/spreadsheets/d/<ID>/edit        (Google Sheet → se exporta a xlsx)
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

import requests

log = logging.getLogger("omnicanal.packing.drive")

_TIMEOUT = 120
# Drive manda HTML si el archivo es privado o si mete el interstitial de antivirus.
# Un .xlsx es un ZIP, así que siempre empieza con "PK".
_FIRMA_ZIP = b"PK"

_PATRONES = [
    re.compile(r"/file/d/([A-Za-z0-9_-]{10,})"),
    re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{10,})"),
    re.compile(r"/document/d/([A-Za-z0-9_-]{10,})"),
]


class DriveError(RuntimeError):
    """La liga no se pudo resolver o el archivo no es accesible."""


def es_url_drive(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host.endswith("drive.google.com") or host.endswith("docs.google.com")


def extraer_id(url: str) -> tuple[str, bool]:
    """
    ``(file_id, es_google_sheet)``.

    Un Google Sheet nativo no es un archivo binario: hay que pedirle a Drive que
    lo exporte a xlsx, que es un endpoint distinto.
    """
    u = (url or "").strip()
    if not u:
        raise DriveError("La liga llegó vacía.")

    es_sheet = "/spreadsheets/" in u
    for patron in _PATRONES:
        if m := patron.search(u):
            return m.group(1), es_sheet

    # ?id=<ID> de las formas /open y /uc
    qs = parse_qs(urlparse(u).query)
    if ids := qs.get("id"):
        return ids[0], es_sheet

    raise DriveError(
        "No se reconoció el FILE_ID en la liga. Usa la liga de 'Compartir' de "
        "Drive, del tipo https://drive.google.com/file/d/…/view"
    )


def _nombre_desde_headers(r: requests.Response, file_id: str) -> str:
    cd = r.headers.get("Content-Disposition") or ""
    # filename*=UTF-8''… tiene prioridad: es el que trae los nombres con acentos
    # y caracteres chinos, que en estos archivos es lo normal.
    if m := re.search(r"filename\*=UTF-8''([^;]+)", cd):
        from urllib.parse import unquote
        return unquote(m.group(1)).strip('"')
    if m := re.search(r'filename="?([^";]+)"?', cd):
        return m.group(1).strip()
    return f"drive-{file_id}.xlsx"


def descargar(url: str) -> tuple[bytes, str]:
    """
    Baja el archivo y devuelve ``(bytes, nombre_archivo)``.

    Lanza :class:`DriveError` con un mensaje accionable si la liga es privada, si
    no es un xlsx, o si Drive contesta cualquier otra cosa.
    """
    file_id, es_sheet = extraer_id(url)

    if es_sheet:
        destino = f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx"
    else:
        destino = f"https://drive.google.com/uc?export=download&id={file_id}"

    ses = requests.Session()
    try:
        r = ses.get(destino, timeout=_TIMEOUT, allow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        raise DriveError(f"No se pudo contactar a Google Drive: {exc}")

    if r.status_code == 404:
        raise DriveError("Drive dice que el archivo no existe (404). Revisa la liga.")
    if r.status_code in (401, 403):
        raise DriveError(
            "Drive negó el acceso. El archivo tiene que estar compartido como "
            "'Cualquier persona con el enlace'."
        )
    if not r.ok:
        raise DriveError(f"Drive respondió {r.status_code}.")

    datos = r.content

    # Interstitial de antivirus (archivos grandes): viene un formulario con un
    # token de confirmación. Se reintenta una vez con el token.
    if not datos.startswith(_FIRMA_ZIP) and b"confirm=" in datos[:60000]:
        if m := re.search(rb"confirm=([0-9A-Za-z_-]+)", datos):
            token = m.group(1).decode()
            try:
                r = ses.get(f"{destino}&confirm={token}", timeout=_TIMEOUT,
                            allow_redirects=True)
                datos = r.content
            except Exception as exc:  # noqa: BLE001
                raise DriveError(f"Falló la confirmación de descarga: {exc}")

    if not datos:
        raise DriveError("Drive devolvió un archivo vacío.")

    if not datos.startswith(_FIRMA_ZIP):
        # Casi siempre es la página de "solicitar acceso": el archivo es privado.
        pista = ""
        if b"<html" in datos[:2000].lower() or b"<!doctype" in datos[:2000].lower():
            pista = (" Drive devolvió una página web en vez del archivo, que es lo "
                     "que pasa cuando el archivo NO está compartido como "
                     "'Cualquier persona con el enlace'.")
        raise DriveError(f"Lo que se descargó no es un .xlsx.{pista}")

    nombre = _nombre_desde_headers(r, file_id)
    if not nombre.lower().endswith((".xlsx", ".xlsm")):
        nombre = f"{nombre}.xlsx"

    log.info("Drive: bajado %s (%d bytes) de %s", nombre, len(datos), file_id)
    return datos, nombre
