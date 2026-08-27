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
import time
from urllib.parse import parse_qs, urlparse

import requests

log = logging.getLogger("omnicanal.packing.drive")

# El timeout NO puede ser uno solo para todos, y por eso va por argumento.
#
# Un packing list con las fotos embebidas pesa decenas de MB (el más grande
# medido: 113 MB) y con 120 s la descarga moría a media transferencia; el error
# que veía el usuario era "no se pudo contactar a Google Drive", que apunta al
# lado equivocado. Pero subir la constante y ya castiga a quien no lo pidió:
# `routers/resolver.py::analizar_url` —el Resolver de siempre, donde una persona
# pega UNA liga— es un handler SÍNCRONO, así que una descarga atorada retiene un
# worker del threadpool de FastAPI, y ese threadpool lo comparten TODOS los
# endpoints síncronos del panel, el listado de Costos incluido. Ahí 120 s de
# castigo es una molestia y 900 s es un panel caído.
#
# Así que el default se queda como estaba y el largo lo pide, explícitamente,
# solo quien baja la carpeta entera de packing lists.
TIMEOUT_CORTO = 120
TIMEOUT_LARGO = 900
# Drive manda HTML si el archivo es privado o si mete el interstitial de antivirus.
# Un .xlsx es un ZIP, así que siempre empieza con "PK".
_FIRMA_ZIP = b"PK"
# Sin User-Agent de navegador, Drive contesta variantes distintas del
# interstitial (y a veces ninguna página útil). No es evadir nada: la liga es
# pública y lo que se pide es exactamente lo que un navegador pediría.
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

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


def descargar(url: str, timeout: int = TIMEOUT_CORTO) -> tuple[bytes, str]:
    """
    Baja el archivo y devuelve ``(bytes, nombre_archivo)``.

    ``timeout`` es el presupuesto de TODA la bajada, no de cada GET: el
    interstitial de antivirus obliga a un segundo GET y con el mismo número en
    los dos el peor caso se duplicaba en silencio (120 → 240 s, y 900 → 1800 s).
    El reintento hereda lo que sobra.

    Lanza :class:`DriveError` con un mensaje accionable si la liga es privada, si
    no es un xlsx, o si Drive contesta cualquier otra cosa.
    """
    file_id, es_sheet = extraer_id(url)
    vence = time.monotonic() + max(1, timeout)

    def _resta() -> float:
        # Un piso de 15 s para que el reintento no nazca muerto cuando el primer
        # GET se comió el presupuesto: mejor un margen corto que un timeout=0.
        return max(15.0, vence - time.monotonic())

    if es_sheet:
        destino = f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx"
    else:
        destino = f"https://drive.google.com/uc?export=download&id={file_id}"

    ses = requests.Session()
    ses.headers.update(_UA)
    try:
        r = ses.get(destino, timeout=_resta(), allow_redirects=True)
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

    # Interstitial de antivirus (archivos grandes): Drive contesta un formulario
    # en vez del archivo. Hay DOS generaciones de esa página y aquí se cubren las
    # dos, porque justo la moderna es la que aparece con los packing lists
    # grandes — los que traen las fotos de las que depende todo el empate:
    #
    #   · la vieja llevaba el token en la propia cadena `confirm=<token>`;
    #   · la de hoy es un <form> con `name="uuid"` y `name="confirm"`, así que la
    #     cadena literal `confirm=` NO aparece, la condición no disparaba y el
    #     archivo terminaba rebotando como "lo que se descargó no es un .xlsx".
    #
    # La moderna se resuelve pidiendo el binario por `drive.usercontent` con
    # `confirm=t`, que es el endpoint al que apunta ese formulario.
    if not datos.startswith(_FIRMA_ZIP):
        cabeza = datos[:200_000]
        moderno = b"Virus scan warning" in cabeza or b'name="uuid"' in cabeza
        if moderno:
            try:
                r = ses.get(
                    f"https://drive.usercontent.google.com/download"
                    f"?id={file_id}&export=download&confirm=t",
                    timeout=_resta(), allow_redirects=True)
                datos = r.content
            except Exception as exc:  # noqa: BLE001
                raise DriveError(f"Falló la confirmación de descarga: {exc}")
        elif b"confirm=" in cabeza:
            if m := re.search(rb"confirm=([0-9A-Za-z_-]+)", datos):
                token = m.group(1).decode()
                try:
                    r = ses.get(f"{destino}&confirm={token}", timeout=_resta(),
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


def descargar_id(file_id: str, timeout: int = TIMEOUT_LARGO) -> tuple[bytes, str]:
    """
    Igual que :func:`descargar`, pero desde un FILE_ID pelón.

    El inventario de la carpeta devuelve ids, no ligas. Se prueba primero la
    exportación de Google Sheet nativo y se cae al binario: **el id no dice de
    qué tipo es el archivo**, y en esa carpeta conviven los dos (medido en la
    carpeta real: 34 .xlsx que subió el proveedor y 16 hojas nativas que alguien
    abrió y guardó en Drive).

    El default es el timeout LARGO porque a esta función solo llega el flujo de
    la carpeta —los archivos de 113 MB—; quien baja una liga pegada a mano entra
    por :func:`descargar`, que se queda con el corto. ``timeout`` es el
    presupuesto de los DOS intentos juntos, por el mismo motivo que dentro de
    :func:`descargar`: dos intentos con el mismo número duplican el peor caso.
    """
    fid = (file_id or "").strip()
    if not fid:
        raise DriveError("El FILE_ID llegó vacío.")
    vence = time.monotonic() + max(1, timeout)
    intentos = (f"https://docs.google.com/spreadsheets/d/{fid}/edit",
                f"https://drive.google.com/uc?export=download&id={fid}")
    ultimo: Exception | None = None
    for url in intentos:
        try:
            return descargar(url, timeout=int(max(15.0, vence - time.monotonic())))
        except DriveError as exc:
            ultimo = exc
    raise DriveError(str(ultimo) if ultimo else f"No se pudo bajar {fid}.")
