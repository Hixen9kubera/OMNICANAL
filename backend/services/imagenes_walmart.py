"""
imagenes_walmart.py — Las fotos del feed de Walmart, desde un host que Walmart
SÍ puede descargar.

POR QUÉ EXISTE (medido el 7-oct-2026)
─────────────────────────────────────
Desde el 17-sep **ninguna** alta del panel entraba: 38 de 38 con el mismo error,

    ERR_EXT_DATA_0101312  We are not authorized to download the image or video
                          from the URL provided.

y el feed mandaba la URL cruda de la galería (`chunche.shop/wp-content/uploads/…`).
Lo que se descartó midiendo, para que nadie lo vuelva a perseguir:

  · NO es el formato. El 12-sep entraron 11 artículos con fotos WEBP de 720 px
    servidas por chunche.shop (ELEC-0145-7GS-NEG, ACC-0884-EST…).
  · NO es de dónde vino la foto. `ORG-0244-BLN` lleva las originales de Amazon
    (`…_AC_SL1500_.jpg`) y rebotó igual que las editadas.
  · NO es un bloqueo por cliente ni por IP. La misma URL contesta 200 a curl,
    Java, okhttp, Go, sin User-Agent, con Referer de Walmart, por HEAD y por
    Range; y el backend de producción (datacenter de EE. UU.) las baja con 200.
  · NO es el certificado: es del 23-ago, anterior a los envíos que sí pasaron.

Lo único anómalo del host: **`https://chunche.shop/robots.txt` contesta 503**
(la página del modo mantenimiento, encendido el 19-ago). Un descargador que
consulta robots.txt trata un 5xx como "prohibido todo", y las fechas cuadran con
uno que confía ~30 días en su copia vieja. No se pudo PROBAR —Walmart no
documenta su descargador—, así que el arreglo no depende de que sea cierto.

LO QUE HACE
───────────
El backend sirve cada foto de la tienda ya convertida, en una URL limpia:

    https://<backend>/pub/img/wm/2026/04/H677…R.webp.jpg
                              └── ruta dentro de /wp-content/uploads/ ──┘ + .jpg

  · JPEG real, RGB (la transparencia se aplana sobre blanco).
  · Lado corto ≥ 1000 px — "Mínimo sin zoom: 1000 x 1000 píxeles" (esquema).
    Se agranda hasta 2×; lo que aún falte se rellena con lienzo blanco, porque
    agrandar más solo inventa borrosidad.
  · ≤ 1 MB — "Tamaño máximo de archivo: 1 MB" (`mainImageUrl`).
  · Sin estado: la URL dice qué archivo es, así que un reinicio del backend
    entre el envío y la descarga de Walmart no rompe nada.

Es la única opción que pasa bajo CUALQUIER hipótesis (robots, CDN o formato):
el host es nuestro, su `robots.txt` contesta 200 y la respuesta es una imagen
directa, sin redirecciones ni cadena de consulta.

EL PROXY weserv (`images.weserv.nl`) publicó 200+ artículos en agosto, pero su
propio robots.txt prohíbe las URLs con `?` y no hay un solo envío posterior al
17-sep que diga si sigue pasando. Queda como respaldo, no como camino.

INTERRUPTOR, sin deploy:  WALMART_IMG_MODO = propio | weserv | directo
    propio   (omisión)  este módulo.
    weserv              el proxy de agosto.
    directo             la URL de la tienda tal cual — para el día que
                        chunche.shop vuelva a contestar su robots.txt.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx

from config import settings

log = logging.getLogger("omnicanal.imagenes_walmart")

MIN_LADO = 1000              # "Mínimo sin zoom: 1000 x 1000 píxeles"
MAX_LADO = 2000              # alcanza para el zoom y deja el archivo bajo 1 MB
MAX_BYTES = 1_000_000        # "Tamaño máximo de archivo: 1 MB"
MAX_ESCALA = 2.0             # tope de agrandado; más allá, lienzo blanco
MAX_FUENTE = 20 * 1024 * 1024
MAX_FOTOS = 5                # `mainImageUrl` + `productSecondaryImageURL[:4]`
MIN_FOTOS = 2                # la principal + 1 adicional (medido: sin la
                             # adicional Walmart contesta "'Foto adicional'
                             # requires a minimum of '1' entries")
PREFIJO_PUBLICO = "/pub/img/wm/"
RAIZ_TIENDA = "/wp-content/uploads/"
EXTENSIONES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".tif", ".tiff", ".bmp")
BACKEND_PROD = "https://backendomnicanal-production.up.railway.app"
MODOS = ("propio", "weserv", "directo")

_CACHE: "OrderedDict[str, tuple[bytes, dict[str, Any]]]" = OrderedDict()
_CACHE_MAX_ENTRADAS = 96
_CACHE_MAX_BYTES = 48 * 1024 * 1024
_cache_bytes = 0
_NO_ESTA: dict[str, float] = {}      # rutas que la tienda no entregó (TTL corto)
_TTL_NO_ESTA = 90.0
_semaforo: asyncio.Semaphore | None = None
_viva: dict[str, Any] = {"t": 0.0, "ok": None}


# ── Configuración ────────────────────────────────────────────────────────────

def modo() -> str:
    m = (os.getenv("WALMART_IMG_MODO")
         or getattr(settings, "walmart_img_modo", "") or "propio").strip().lower()
    return m if m in MODOS else "propio"


def base_publica() -> str:
    """La URL pública del backend que sirve `/pub/img/…`."""
    b = (os.getenv("WALMART_IMG_BASE")
         or getattr(settings, "walmart_img_base", "") or "").strip()
    if not b:
        dominio = (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
        b = f"https://{dominio}" if dominio else BACKEND_PROD
    return b.rstrip("/")


def host_tienda() -> str:
    h = urlsplit(getattr(settings, "wc_url", "") or "").hostname
    return (h or "chunche.shop").lower()


# ── De la URL de la tienda a la URL pública, y de vuelta ─────────────────────

def ruta_de(url: str | None) -> str | None:
    """La ruta dentro de `/wp-content/uploads/` si la foto es de la tienda."""
    if not url:
        return None
    p = urlsplit(str(url).strip())
    if (p.hostname or "").lower() != host_tienda():
        return None
    if not p.path.startswith(RAIZ_TIENDA):
        return None
    ruta = unquote(p.path[len(RAIZ_TIENDA):])
    return ruta if _ruta_valida(ruta) else None


def _ruta_valida(ruta: str) -> bool:
    """Solo archivos de imagen de la carpeta de medios. Nada de `..`, ni de
    caracteres de control, ni de otra cosa que no sea una ruta relativa."""
    if not ruta or len(ruta) > 400 or ruta.startswith("/"):
        return False
    if any(ord(c) < 32 for c in ruta) or any(c in ruta for c in "\\?#"):
        return False
    partes = ruta.split("/")
    if any(p in ("", ".", "..") for p in partes):
        return False
    return ruta.lower().endswith(EXTENSIONES)


def _url_tienda(ruta: str) -> str:
    return f"https://{host_tienda()}{RAIZ_TIENDA}{quote(ruta, safe='/')}"


def url_propia(ruta: str) -> str:
    return f"{base_publica()}{PREFIJO_PUBLICO}{quote(ruta, safe='/')}.jpg"


def url_weserv(url: str, agrandar: bool = False) -> str:
    """La receta de agosto: sin esquema, `output=jpg`. `agrandar` suma el
    lienzo de 1000×1000 para las que no llegan."""
    u = f"https://images.weserv.nl/?url={str(url).split('://', 1)[-1]}&output=jpg"
    if agrandar:
        u += f"&w={MIN_LADO}&h={MIN_LADO}&fit=contain&cbg=white&bg=white"
    return u


# ── La conversión (síncrona: se llama SIEMPRE dentro de `asyncio.to_thread`) ─

def convertir(data: bytes) -> tuple[bytes, dict[str, Any]]:
    """Cualquier imagen → JPEG RGB con lado corto ≥ 1000 px y ≤ 1 MB."""
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(data))
    formato = (im.format or "?").upper()
    if getattr(im, "is_animated", False):
        im.seek(0)                      # de un GIF/WEBP animado, el primer cuadro
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:  # noqa: BLE001 — una etiqueta EXIF rota no tumba la foto
        pass
    w0, h0 = im.size

    # La transparencia se aplana sobre BLANCO. Convertir RGBA a RGB a secas la
    # deja en negro, y un fondo negro es de las cosas que Walmart sí rechaza.
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        fondo = Image.new("RGB", im.size, (255, 255, 255))
        fondo.paste(im, mask=im.split()[-1])
        im = fondo
    else:
        im = im.convert("RGB")

    w, h = im.size
    if max(w, h) > MAX_LADO:
        f = MAX_LADO / max(w, h)
        im = im.resize((max(1, round(w * f)), max(1, round(h * f))), Image.LANCZOS)
        w, h = im.size

    agrandada = rellenada = False
    if min(w, h) < MIN_LADO:
        f = min(MIN_LADO / min(w, h), MAX_ESCALA, MAX_LADO / max(w, h))
        if f > 1.001:
            im = im.resize((round(w * f), round(h * f)), Image.LANCZOS)
            w, h = im.size
            agrandada = True
        if min(w, h) < MIN_LADO:
            lienzo = Image.new("RGB", (max(w, MIN_LADO), max(h, MIN_LADO)),
                               (255, 255, 255))
            lienzo.paste(im, ((lienzo.width - w) // 2, (lienzo.height - h) // 2))
            im = lienzo
            w, h = im.size
            rellenada = True

    salida = b""
    for calidad in (88, 82, 76, 70, 62):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=calidad, optimize=True)
        salida = buf.getvalue()
        if len(salida) <= MAX_BYTES:
            break
    else:
        # Ni con calidad 62 baja de 1 MB: se reduce el lienzo y listo.
        while len(salida) > MAX_BYTES and min(im.size) > MIN_LADO:
            im = im.resize((round(im.width * 0.85), round(im.height * 0.85)),
                           Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=70, optimize=True)
            salida = buf.getvalue()
        w, h = im.size

    return salida, {"formato_origen": formato, "ancho_origen": w0, "alto_origen": h0,
                    "ancho": w, "alto": h, "bytes": len(salida),
                    "agrandada": agrandada, "rellenada": rellenada}


# ── Caché en memoria (acotado; la URL no depende de él) ──────────────────────

def _cache_get(ruta: str) -> tuple[bytes, dict[str, Any]] | None:
    v = _CACHE.get(ruta)
    if v is not None:
        _CACHE.move_to_end(ruta)
    return v


def _cache_put(ruta: str, v: tuple[bytes, dict[str, Any]]) -> None:
    global _cache_bytes
    viejo = _CACHE.pop(ruta, None)
    if viejo is not None:
        _cache_bytes -= len(viejo[0])
    _CACHE[ruta] = v
    _cache_bytes += len(v[0])
    while _CACHE and (len(_CACHE) > _CACHE_MAX_ENTRADAS
                      or _cache_bytes > _CACHE_MAX_BYTES):
        _, sale = _CACHE.popitem(last=False)
        _cache_bytes -= len(sale[0])


async def _bajar(url: str, cli: httpx.AsyncClient | None = None) -> bytes | None:
    """Los bytes de una imagen, o None. Sin seguir redirecciones: una foto de
    la tienda se sirve directo, y seguir un 30x sería dejar que otro host
    decida de dónde lee el backend."""
    propio = cli is None
    cli = cli or httpx.AsyncClient(timeout=30.0, follow_redirects=False)
    try:
        r = await cli.get(url, headers={"Accept": "image/jpeg,image/png,image/*;q=0.8",
                                        "User-Agent": "omnicanal-kubera/imagenes"})
        if r.status_code != 200:
            log.info("imagenes_walmart: %s → HTTP %s", url, r.status_code)
            return None
        if len(r.content) > MAX_FUENTE:
            log.warning("imagenes_walmart: %s pesa %d bytes, se descarta",
                        url, len(r.content))
            return None
        return r.content
    except Exception as exc:  # noqa: BLE001
        log.info("imagenes_walmart: no se pudo bajar %s: %s", url, exc)
        return None
    finally:
        if propio:
            await cli.aclose()


async def obtener(ruta: str, cli: httpx.AsyncClient | None = None
                  ) -> tuple[bytes, dict[str, Any]] | None:
    """La foto de la tienda ya convertida. None si no existe o no es imagen."""
    global _semaforo
    if not _ruta_valida(ruta):
        return None
    listo = _cache_get(ruta)
    if listo is not None:
        return listo
    if _NO_ESTA.get(ruta, 0) > time.time():
        return None
    if _semaforo is None:
        _semaforo = asyncio.Semaphore(4)     # la conversión es CPU: de a pocas
    async with _semaforo:
        listo = _cache_get(ruta)             # otro la pudo armar mientras tanto
        if listo is not None:
            return listo
        data = await _bajar(_url_tienda(ruta), cli)
        if data is None:
            _NO_ESTA[ruta] = time.time() + _TTL_NO_ESTA
            return None
        try:
            v = await asyncio.to_thread(convertir, data)
        except Exception as exc:  # noqa: BLE001 — no era una imagen legible
            log.info("imagenes_walmart: %s no se pudo convertir: %s", ruta, exc)
            _NO_ESTA[ruta] = time.time() + _TTL_NO_ESTA
            return None
        _cache_put(ruta, v)
        return v


async def servir(ruta_publica: str) -> bytes | None:
    """Para la ruta `/pub/img/wm/{ruta}.jpg`: los bytes del JPEG o None (404)."""
    if not ruta_publica.lower().endswith(".jpg"):
        return None
    r = await obtener(ruta_publica[:-4])
    return r[0] if r else None


# ── ¿La ruta pública contesta? (una vez cada 10 min) ─────────────────────────

async def ruta_publica_viva(cli: httpx.AsyncClient | None = None) -> bool:
    """
    ¿`<backend>/robots.txt` contesta 200 y abre `/pub/img/`?

    Se pregunta desde fuera, por internet, porque es lo que va a hacer Walmart:
    comprueba de un golpe el dominio, el middleware de identidad y la ruta. Si
    no contesta (un local, un staging sin dominio, una variable mal puesta),
    `preparar` cae al proxy en vez de mandar URLs que nadie puede abrir.
    """
    ahora = time.time()
    # Un "sí" vale 10 minutos; un "no", uno solo — para no quedarse en el proxy
    # diez minutos por un parpadeo de la red.
    if _viva["ok"] is not None and ahora - _viva["t"] < (600 if _viva["ok"] else 60):
        return bool(_viva["ok"])
    propio = cli is None
    cli = cli or httpx.AsyncClient(timeout=12.0, follow_redirects=False)
    ok = False
    try:
        r = await cli.get(f"{base_publica()}/robots.txt")
        ok = r.status_code == 200 and "/pub/img/" in r.text
    except Exception as exc:  # noqa: BLE001
        log.warning("imagenes_walmart: la ruta pública no contesta: %s", exc)
    finally:
        if propio:
            await cli.aclose()
    _viva.update(t=ahora, ok=ok)
    return ok


# ── Lo que llama el publicador ───────────────────────────────────────────────

async def _medir(url: str, cli: httpx.AsyncClient) -> dict[str, Any] | None:
    """Formato y tamaño REALES de una URL cualquiera (se descarga y se abre)."""
    data = await _bajar(url, cli)
    if data is None:
        return None

    def _abrir() -> dict[str, Any] | None:
        from PIL import Image
        try:
            im = Image.open(io.BytesIO(data))
            return {"formato": (im.format or "?").upper(), "ancho": im.width,
                    "alto": im.height, "bytes": len(data)}
        except Exception:  # noqa: BLE001
            return None
    return await asyncio.to_thread(_abrir)


async def preparar(urls: list[str], sku: str = "") -> dict[str, Any]:
    """
    Las URLs que van en el feed, ya comprobadas una por una.

    Devuelve `{ok, urls, avisos, motivo, modo}`. `ok=False` cuando no queda la
    principal o no hay al menos una adicional: mandar ese feed es gastar un
    envío para leer un rechazo que ya se conoce.
    """
    m = modo()
    avisos: list[str] = []
    finales: list[str] = []
    chicas: list[tuple[int, int]] = []
    ilegibles = 0
    vistas: set[str] = set()

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as cli:
        if m == "propio" and not await ruta_publica_viva():
            avisos.append(
                f"La ruta pública de fotos ({base_publica()}{PREFIJO_PUBLICO}…) no "
                f"contesta desde internet: las fotos van por el proxy weserv. "
                f"Revisa WALMART_IMG_BASE si esto sale en producción.")
            m = "weserv"

        candidatas: list[str] = []
        for u in urls:
            u = str(u or "").strip()
            if u and u not in vistas:
                vistas.add(u)
                candidatas.append(u)
        candidatas = candidatas[:MAX_FOTOS + 3]   # margen por si alguna no abre

        # Las de la tienda se convierten JUNTAS (el semáforo de `obtener` las
        # dosifica): de una en una, cinco fotos eran varios segundos de espera
        # en la vista previa.
        listas: dict[str, tuple[bytes, dict[str, Any]] | None] = {}
        if m == "propio":
            rutas = [r for r in (ruta_de(u) for u in candidatas) if r]
            for r, v in zip(rutas, await asyncio.gather(
                    *(obtener(r, cli) for r in rutas))):
                listas[r] = v

        for u in candidatas:
            if len(finales) >= MAX_FOTOS:
                break
            ruta = ruta_de(u)

            if m == "propio" and ruta:
                r = listas.get(ruta)
                if r is None:
                    ilegibles += 1
                    continue
                info = r[1]
                if info["agrandada"] or info["rellenada"]:
                    chicas.append((info["ancho_origen"], info["alto_origen"]))
                finales.append(url_propia(ruta))
                continue

            # weserv / directo / una foto que no vive en la tienda: se mide lo
            # que hay y se comprueba lo que se va a mandar.
            origen = await _medir(u, cli)
            if origen is None:
                ilegibles += 1
                continue
            chica = min(origen["ancho"], origen["alto"]) < MIN_LADO
            if m == "weserv":
                final = url_weserv(u, agrandar=chica)
                listo = await _medir(final, cli)
                if listo is None or listo["formato"] != "JPEG":
                    ilegibles += 1
                    continue
            else:
                final = u
            if chica:
                chicas.append((origen["ancho"], origen["alto"]))
            finales.append(final)

    if chicas:
        w, h = min(chicas, key=lambda t: min(t))
        como = ("se agrandan para cumplir el mínimo de Walmart, pero se verán "
                "borrosas" if m != "directo" else
                "van tal cual y Walmart puede rechazarlas o castigar el listado")
        avisos.append(f"{len(chicas)} foto(s) miden menos de {MIN_LADO} px (la más "
                      f"chica, {w}×{h}): {como}. Sube fotos de mejor resolución.")
    if ilegibles:
        avisos.append(f"{ilegibles} foto(s) no se pudieron leer y NO van en el feed.")

    if not finales:
        return {"ok": False, "urls": [], "avisos": avisos, "modo": m,
                "motivo": (f"Ninguna foto de {sku or 'este producto'} se pudo leer "
                           f"desde la tienda. No se manda el feed: sin la principal "
                           f"Walmart rechaza el artículo.")}
    if len(finales) < MIN_FOTOS:
        return {"ok": False, "urls": finales, "avisos": avisos, "modo": m,
                "motivo": (f"{sku or 'Este producto'} tiene una sola foto utilizable y "
                           f"Walmart exige la principal MÁS una adicional ("
                           f"\"'Foto adicional' requires a minimum of '1' entries\"). "
                           f"Súbele al menos otra foto en WooCommerce y vuelve a "
                           f"intentar.")}
    return {"ok": True, "urls": finales, "avisos": avisos, "modo": m, "motivo": None}
