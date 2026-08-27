"""
packing_drive_carpeta.py — Qué packing list le toca a cada contenedor.

:mod:`packing_drive` sabe bajar UNA liga; lo que falta para el validador de
costos de publicados es lo anterior: dado un SKU con su referencia de
contenedor —``PCIU9532241=CI&PL contenedor 56``, y cosas peores—, ¿cuál de los
188 archivos de la carpeta de Drive es el suyo?

**No hay service account de Google en este repo** (ni una sola clave GOOGLE_*/
DRIVE_* en el .env), así que el inventario se lee de la página PÚBLICA de la
carpeta: Drive incrusta ahí un ``window['_DRIVE_ivd']`` con los archivos y un
token de paginación. Es HTML no documentado y Google lo puede cambiar sin
avisar; por eso el flujo tiene un escape —pegar la liga del packing list a
mano— y por eso el inventario se cachea. El camino bueno el día que haya
credenciales es ``files.list`` con ``parentId``, y esta función se reemplaza sin
tocar a nadie más.

DOS PASADAS para empatar referencia→archivo, en este orden:

  1. **Por nombre aplanado.** El nombre del archivo suele traer la referencia
     tal cual. Salva los contenedores cuyo código NINGÚN patrón reconoce
     (``ONEYNB5BEK841700``).
  2. **Por código extraído.** Se sacan los códigos de los dos lados con regex y
     se intersectan los conjuntos — un valor puede traer DOS códigos
     (``256059868 TRHU6215242 contenedor 1``).

En las dos se descartan los nombres que empiezan con ``X-``/``XX-``/``XXX-``:
no son originales.
"""
from __future__ import annotations

import html as htmlmod
import json
import logging
import pathlib
import re
import tempfile
import threading
import time

import requests

from config import settings
from services import packing_drive

log = logging.getLogger("omnicanal.packing.drive_carpeta")

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_TIMEOUT = 120
_TTL_INVENTARIO = 60 * 60 * 6      # 6 h: la carpeta crece con cada embarque
_MAX_PAGINAS = 30

# Copias, no originales. Cualquier número de X seguidas de guion, al INICIO.
RE_COPIA = re.compile(r"^X+-", re.I)
# Códigos de contenedor / booking en cualquiera de las formas que aparecen.
RE_COD = re.compile(
    r"([A-Z]{4}\d{6,7}|SZLS\d{6,9}|[A-Z]{4}[A-Z0-9]{8,14}|\d{9,12})")

_inventario: dict[str, str] = {}
_inventario_en: float = 0.0
_lock = threading.Lock()

# ── La semilla, y por qué existe ─────────────────────────────────────────────
# Raspar el HTML de Drive NO ALCANZA para enumerar esta carpeta. Medido el
# 27-ago-2026: la carpeta tiene 188 archivos y el barrido corto veía 76; el
# `completo=True`, con sus 26 permutaciones de orden, llegaba a 97. Cada vista
# de Drive devuelve un subconjunto y ninguna combinación los cubre todos.
#
# El síntoma es engañoso y caro: un archivo que no se ve se reporta como "SKU
# sin packing list", que es una frase distinta de "no lo encontré". Le pasó a
# TEC-1606-NEG, cuyo `KOCU4642556=INV&PL.xlsx` está en la carpeta desde siempre.
#
# Esta semilla es una FOTO del inventario tomada con el conector de Drive (que
# sí lista la carpeta completa) y sirve de piso: el raspado solo puede SUMAR
# archivos nuevos, nunca quitar los de aquí. No es la solución —un packing list
# subido después de la foto sigue dependiendo del raspado—; **la solución es
# darle al backend credenciales de la API de Drive** y listar con
# `files.list(q="'<carpeta>' in parents")`. Mientras tanto, esto evita que la
# mitad del catálogo se vea como "sin contenedor".
_SEMILLA_RUTA = pathlib.Path(__file__).parent / "data" / "packing_lists_drive.json"


def _semilla() -> dict[str, str]:
    try:
        datos = json.loads(_SEMILLA_RUTA.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in datos.items() if k and v}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        log.warning("semilla del inventario de Drive ilegible: %s", exc)
        return {}

# Los .xlsx bajados se cachean en disco por file_id: un packing list pesa hasta
# 113 MB y varios SKUs del mismo embarque comparten archivo. Cachearlos en
# memoria sería tener 113 MB por contenedor vivos 3 h.
_CACHE_DIR = pathlib.Path(tempfile.gettempdir()) / "omnicanal_packing_lists"


# ── Inventario de la carpeta ─────────────────────────────────────────────────
def _ivd(url: str) -> list | None:
    """El arreglo ``_DRIVE_ivd`` incrustado en la página de la carpeta."""
    try:
        r = requests.get(url, timeout=_TIMEOUT, headers=_UA)
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo leer la carpeta de Drive: %s", exc)
        return None
    m = re.search(r"window\['_DRIVE_ivd'\]\s*=\s*'(.*?)';", r.text, re.S)
    if not m:
        return None
    try:
        crudo = m.group(1).encode("utf-8").decode("unicode_escape", "ignore")
        return json.loads(crudo)
    except Exception:  # noqa: BLE001
        return None


def _token_de(datos: list | None) -> str | None:
    """El token de la siguiente página: una cadena larga en el nivel 1."""
    for e in (datos or [])[1:]:
        if isinstance(e, str) and len(e) > 20:
            return e
    return None


def _cosechar(datos: list | None, salida: dict[str, str]) -> int:
    nuevos = 0
    for it in (datos[0] if datos and datos[0] else []):
        if isinstance(it, list) and len(it) > 3 and isinstance(it[0], str):
            if it[0] not in salida:
                salida[it[0]] = str(it[2])
                nuevos += 1
    return nuevos


# Cada vista de la carpeta incrusta SOLO los primeros ~50 archivos, y no hay
# token de paginación: la lista larga la trae Drive por un RPC interno del
# JavaScript. El truco que sí funciona es pedir la MISMA carpeta con órdenes
# distintos —por nombre, por fecha, por tamaño, en las dos direcciones—: cada
# orden trae sus primeros 50, y la unión cubre la carpeta. Medido el 26-ago:
# 50 con la vista de siempre, 97 sumando los órdenes.
_ORDENES = [(s, d) for s in range(1, 14) for d in (0, 1)]
_ORDENES_RAPIDO = 6      # cuántos probar en el camino de una petición HTTP
_SIN_NUEVOS_CORTE = 6    # rondas seguidas sin aportar nada = ya está completo


def inventario(refrescar: bool = False, completo: bool = False) -> dict[str, str]:
    """
    ``{file_id: nombre}`` de la carpeta de packing lists.

    Cacheado 6 h y **ACUMULATIVO**: nunca se sustituye lo conocido por lo de la
    última lectura. Cada vista de Drive trae un subconjunto distinto, así que
    reemplazar en vez de sumar haría que la carpeta "perdiera" archivos según el
    humor de Google. Y si la lectura falla del todo, se devuelve lo viejo: un
    inventario de hace horas sirve muchísimo más que un diccionario vacío.

    ``completo=True`` barre todos los órdenes (decenas de segundos): es para el
    hilo del trabajo. El camino de una petición HTTP usa el barrido corto.
    """
    global _inventario, _inventario_en
    with _lock:
        fresco = _inventario and (time.time() - _inventario_en) < _TTL_INVENTARIO
        if fresco and not refrescar:
            return dict(_inventario)
        # La semilla va DEBAJO de lo ya conocido: es el piso, no la verdad
        # final. Un nombre que el raspado haya corregido gana sobre ella.
        conocidos = {**_semilla(), **_inventario}

    carpeta = (settings.pl_drive_carpeta_id or "").strip()
    if not carpeta:
        log.warning("PL_DRIVE_CARPETA_ID vacío: no hay carpeta que listar.")
        return conocidos

    base = f"https://drive.google.com/drive/folders/{carpeta}"
    encontrados: dict[str, str] = dict(conocidos)
    antes = len(encontrados)

    datos = _ivd(base)
    _cosechar(datos, encontrados)

    # Si algún día Drive vuelve a mandar token de paginación, se sigue: es el
    # camino barato y correcto, y no cuesta nada intentarlo.
    token, pagina = _token_de(datos), 0
    while token and pagina < _MAX_PAGINAS:
        pagina += 1
        datos = _ivd(f"{base}?pageToken={requests.utils.quote(token)}")
        _cosechar(datos, encontrados)
        nuevo = _token_de(datos)
        if nuevo == token:          # Drive repitiendo la última página
            break
        token = nuevo

    # El barrido completo NO corta por rachas secas: los órdenes que aportan
    # están repartidos (medido: el de nombre y el de tamaño aportaron, y entre
    # ellos hubo diez que no). Cortar antes de tiempo deja archivos fuera, y un
    # archivo que falta se ve como "SKU sin packing list", que es otra cosa.
    ordenes = _ORDENES if completo else _ORDENES[:_ORDENES_RAPIDO]
    secos = 0
    for s, d in ordenes:
        if _cosechar(_ivd(f"{base}?sort={s}&direction={d}"), encontrados):
            secos = 0
        else:
            secos += 1
            if not completo and secos >= _SIN_NUEVOS_CORTE:
                break

    # La vista embebida a veces trae lo que las otras no (o nada: hoy devuelve
    # vacío). Es barata y solo puede sumar.
    try:
        r = requests.get(
            f"https://drive.google.com/embeddedfolderview?id={carpeta}#list",
            timeout=_TIMEOUT, headers=_UA)
        for fid, nombre in re.findall(
                r'<div class="flip-entry" id="entry-([A-Za-z0-9_-]{20,60})">.*?'
                r'<div class="flip-entry-title">([^<]+)</div>', r.text, re.S):
            encontrados.setdefault(fid, htmlmod.unescape(nombre).strip())
    except Exception as exc:  # noqa: BLE001
        log.debug("vista embebida de la carpeta no disponible: %s", exc)

    with _lock:
        if encontrados:
            _inventario = encontrados
            _inventario_en = time.time()
        log.info("carpeta de packing lists: %d archivos (%d nuevos)",
                 len(encontrados), len(encontrados) - antes)
        return dict(_inventario)


# ── Referencia → archivos ────────────────────────────────────────────────────
def _plano(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def codigos_de(valor: str) -> set[str]:
    """
    Los códigos de contenedor que trae un texto sucio.

    No se PARSEA el campo de Odoo: 201 de sus 350 valores distintos no caen en
    ningún patrón limpio (``PCIU9532241=CI&PL contenedor 56``, ``CI&PL=
    EMCU8725032 contenedor 3`` con el código DESPUÉS del ruido, NBSP de por
    medio). Se extrae lo que se reconoce y se tira el resto.
    """
    limpio = (valor or "").replace("\xa0", " ")
    limpio = re.sub(r"\s+", " ", limpio).upper()
    return set(RE_COD.findall(limpio))


def archivos_de(ref: str, inv: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """``[(file_id, nombre)]`` de los packing lists que corresponden a ``ref``."""
    inv = inv if inv is not None else inventario()
    if not ref or not inv:
        return []
    originales = [(f, n) for f, n in inv.items() if not RE_COPIA.match((n or "").strip())]

    rp = _plano(ref)
    if rp:
        por_nombre = [(f, n) for f, n in originales
                      if _plano(n).startswith(rp[:20]) or rp.startswith(_plano(n)[:20])]
        if por_nombre:
            return por_nombre

    cods = codigos_de(ref)
    if not cods:
        return []
    return [(f, n) for f, n in originales if codigos_de(n) & cods]


# ── Bajada con caché en disco ────────────────────────────────────────────────
def bajar(file_id: str, nombre: str = "") -> bytes:
    """
    El .xlsx del ``file_id``, cacheado en disco.

    Vuelve a bajarlo solo si no está: dentro de una corrida, varios SKUs del
    mismo embarque piden el mismo archivo, y entre corridas el packing list de
    un contenedor ya cerrado no cambia. Lanza :class:`packing_drive.DriveError`
    con el motivo cuando Drive no coopera.
    """
    fid = (file_id or "").strip()
    if not fid:
        raise packing_drive.DriveError("El FILE_ID llegó vacío.")
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        destino = _CACHE_DIR / f"pl_{fid}.xlsx"
        if destino.exists() and destino.stat().st_size > 0:
            return destino.read_bytes()
    except Exception as exc:  # noqa: BLE001
        # Sin caché se puede vivir; sin archivo, no. Se sigue y se baja.
        log.debug("caché de packing lists no disponible: %s", exc)
        destino = None

    datos, _ = packing_drive.descargar_id(fid)
    if destino is not None:
        try:
            destino.write_bytes(datos)
        except Exception as exc:  # noqa: BLE001
            log.debug("no se pudo cachear %s: %s", nombre or fid, exc)
    return datos
