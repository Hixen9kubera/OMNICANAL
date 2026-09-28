"""API del LABORATORIO de precios: FastAPI de SOLO LECTURA sobre `ultimo/` y `snapshots/`.

Por qué un servidor propio y no una ruta más del backend: `main.py` arranca el
scheduler de producción (29 jobs que ESCRIBEN: pedidos, stock, espejos) y el
warm-up de ventas. Importarlo desde aquí sería levantar producción con otro
nombre. Este módulo no importa `main` ni `services.scheduler`: solo lee los
archivos que deja el pipeline del laboratorio en `LAB_DATOS_DIR`.

Qué garantiza:

- **Métodos**: solo GET/HEAD/OPTIONS, salvo `POST /api/lab/sesion` y
  `POST /api/lab/recalcular`. Cualquier otro método se rechaza en el middleware
  ANTES del ruteo (405), exista o no la ruta.
- **Llave compartida** `LAB_ACCESS_KEY`: cookie httpOnly `lab_sesion` (HMAC de la
  llave) o `Authorization: Bearer <llave>`. Sin llave: abierto SOLO fuera de
  Railway (modo local, con aviso en /estado). En Railway sin llave TODO responde
  503 (falla cerrado) salvo `/salud`, que contesta 200 con `cerrado: true`: si el
  healthcheck fallara, Railway mantendría vivo el deploy anterior CON llave y
  quitar la llave no apagaría el laboratorio.
- **Regla 11**: los endpoints de datos son `def` (FastAPI los corre en su pool de
  hilos) y el pipeline corre en `asyncio.to_thread`; nada de disco ni red en el loop.
- **Caché** en memoria invalidada por (mtime, tamaño). El pipeline escribe con
  `os.replace` (`almacen.escribir_json`), así que nunca se lee un archivo a medias.
- `NaN`/`Infinity` (que `json.dump` escribe por omisión) se leen como `null`: un
  `NaN` en la respuesta rompe el `JSON.parse` del navegador, y la regla del
  laboratorio es que un dato ausente es `null`, nunca un número inventado.

Cómo se corre (cwd = backend/):

    uvicorn sandbox_precios.api:app --port 8010
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import logging
import math
import mimetypes
import os
import re
import sys
import threading
import time
import unicodedata
import zlib
from array import array
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sandbox_precios import _entorno  # noqa: E402

# ANTES de cualquier `services.*`: fuerza las banderas de solo lectura e instala
# los candados (renovar token de ML, execute en kubera/MySQL, escritura en Odoo).
_entorno.cargar()

from fastapi import FastAPI, Query, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response  # noqa: E402

from sandbox_precios import almacen  # noqa: E402

log = logging.getLogger("laboratorio.api")
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

VERSION_API = "lab-api-0.1"
PREFIJO = "/api/lab"
COOKIE = "lab_sesion"
_DURACION_SESION_S = 14 * 24 * 3600
_METODOS_LECTURA = {"GET", "HEAD", "OPTIONS"}
_RUTAS_POST = {f"{PREFIJO}/sesion", f"{PREFIJO}/recalcular"}
_RUTAS_SIN_AUTH = {f"{PREFIJO}/sesion", f"{PREFIJO}/salud"}
# En Railway una llave corta es casi lo mismo que no tener llave: el servicio
# tiene URL pública y lo que protege son costos y márgenes reales.
_LLAVE_MIN_RAILWAY = 16
# Freno a la adivinanza de la llave: 10 intentos fallidos por IP cada 15 min.
_FALLOS_MAX = 10
_FALLOS_VENTANA_S = 900
# Se TOPA, no se rechaza: `web/lib/api.ts::traerTodo` pide per_page=1000 y, si el
# servidor devuelve menos, sigue pidiendo páginas con el tamaño que le llegó.
_PER_PAGE_MAX = 1000
_ARCHIVOS_ULTIMO = ("estado.json", "publicaciones.json", "precios.json", "curvas.json",
                    "historial.json", "metricas.json", "packing100.json", "contenedores.json",
                    "packing_saltados.json")


# ══════════════════════════════════════════════════════════════════════════════
#  Autenticación
# ══════════════════════════════════════════════════════════════════════════════

def _llave() -> str:
    return (os.environ.get("LAB_ACCESS_KEY") or "").strip()


def _modo_auth() -> tuple[str, str | None]:
    """("llave" | "abierto" | "cerrado", motivo). "cerrado" = todo 503."""
    llave = _llave()
    if llave:
        if _entorno.EN_RAILWAY and len(llave) < _LLAVE_MIN_RAILWAY:
            return "cerrado", f"LAB_ACCESS_KEY tiene menos de {_LLAVE_MIN_RAILWAY} caracteres"
        return "llave", None
    if _entorno.EN_RAILWAY:
        return "cerrado", "LAB_ACCESS_KEY no está definida en Railway"
    return "abierto", None


def _firma_sesion(llave: str) -> str:
    """Valor de la cookie. Determinista a propósito: rotar la llave cierra TODAS las sesiones."""
    return hmac.new(llave.encode("utf-8"), b"lab", hashlib.sha256).hexdigest()


def _iguales(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _autenticado(request: Request) -> str | None:
    """'cookie' | 'bearer' | None, comparando siempre en tiempo constante."""
    llave = _llave()
    if not llave:
        return None
    cookie = request.cookies.get(COOKIE)
    if cookie and _iguales(cookie, _firma_sesion(llave)):
        return "cookie"
    auth = request.headers.get("authorization") or ""
    if auth[:7].lower() == "bearer " and _iguales(auth[7:].strip(), llave):
        return "bearer"
    return None


def _mismo_origen(request: Request) -> bool:
    """Defensa extra contra CSRF en los POST con cookie (SameSite=Lax ya lo frena).

    Sin cabecera Origin se acepta: los navegadores la mandan en todo POST, y un
    cliente sin navegador que no la manda tampoco lleva la cookie de nadie.
    `x-forwarded-host` primero: el proxy de `next dev` (3010 → 8010) la pone.
    """
    origen = request.headers.get("origin")
    if not origen:
        return True
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return urlsplit(origen).netloc.lower() == host.split(",")[0].strip().lower()


_fallos: dict[str, deque] = defaultdict(deque)
_fallos_lock = threading.Lock()


def _ip(request: Request) -> str:
    # Con `--proxy-headers` uvicorn ya pone aquí la IP real que trae Railway.
    return request.client.host if request.client else "?"


def _bloqueado(ip: str) -> bool:
    with _fallos_lock:
        cola = _fallos[ip]
        limite = time.monotonic() - _FALLOS_VENTANA_S
        while cola and cola[0] < limite:
            cola.popleft()
        return len(cola) >= _FALLOS_MAX


def _registrar_fallo(ip: str) -> None:
    with _fallos_lock:
        _fallos[ip].append(time.monotonic())


# ══════════════════════════════════════════════════════════════════════════════
#  Lectura de archivos con caché por mtime
# ══════════════════════════════════════════════════════════════════════════════

def _nan_a_none(_constante: str) -> None:
    return None


_DECODIFICADOR = json.JSONDecoder(parse_constant=_nan_a_none)
_ESPACIOS = re.compile(r"[ \t\n\r]*")


class ArchivoIlegible(RuntimeError):
    """El JSON existe pero no se pudo leer y no hay versión anterior en memoria."""


def _cargar_json(ruta: Path) -> Any:
    with open(ruta, encoding="utf-8") as fh:
        return json.load(fh, parse_constant=_nan_a_none)


class _CacheArchivos:
    """(ruta, cargador) → objeto ya procesado; se relee solo si cambió (mtime_ns, tamaño)."""

    def __init__(self) -> None:
        self._datos: dict[tuple[str, str], tuple[tuple[int, int], Any]] = {}
        self._locks: dict[tuple[str, str], threading.Lock] = defaultdict(threading.Lock)
        self._global = threading.Lock()

    def leer(self, ruta: Path, cargador: Callable[[Path], Any] = _cargar_json) -> Any:
        clave = (str(ruta), getattr(cargador, "__name__", "c"))
        try:
            st = ruta.stat()
        except FileNotFoundError:
            self._datos.pop(clave, None)
            return None
        firma = (st.st_mtime_ns, st.st_size)
        ent = self._datos.get(clave)
        if ent is not None and ent[0] == firma:
            return ent[1]
        with self._global:
            lock = self._locks[clave]
        with lock:  # una sola lectura aunque lleguen 20 peticiones juntas tras un recálculo
            ent = self._datos.get(clave)
            if ent is not None and ent[0] == firma:
                return ent[1]
            t0 = time.monotonic()
            try:
                datos = cargador(ruta)
            except FileNotFoundError:
                self._datos.pop(clave, None)
                return None
            except (ValueError, UnicodeDecodeError) as exc:
                if ent is not None:
                    log.warning("laboratorio: %s ilegible (%s); sigo con la versión anterior", ruta.name, exc)
                    return ent[1]
                raise ArchivoIlegible(f"{ruta.name}: {exc}") from exc
            self._datos[clave] = (firma, datos)
            log.info("laboratorio: %s cargado en %.2fs (%s bytes)", ruta.name,
                     time.monotonic() - t0, st.st_size)
            return datos


_cache = _CacheArchivos()


def _ultimo(nombre: str) -> Path:
    return almacen.ultimo(nombre)


def _normalizar(texto: str) -> str:
    """minúsculas y sin acentos: 'Almohada ortopédica' se encuentra con 'ortopedica'."""
    t = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


class _Tabla:
    """Un JSON `{"generado_at", "filas": [...]}` con su texto de búsqueda precalculado."""

    __slots__ = ("generado_at", "filas", "busqueda", "extra")

    def __init__(self, generado_at: Any, filas: list[dict], busqueda: list[str], extra: dict):
        self.generado_at = generado_at
        self.filas = filas
        self.busqueda = busqueda
        self.extra = extra


def _cargar_tabla(ruta: Path) -> _Tabla:
    datos = _cargar_json(ruta)
    if isinstance(datos, list):
        filas, extra = datos, {}
    elif isinstance(datos, dict):
        filas = datos.get("filas") or []
        extra = {k: v for k, v in datos.items() if k != "filas"}
    else:
        raise ValueError("se esperaba {filas: [...]}")
    filas = [f for f in filas if isinstance(f, dict)]
    busqueda = [_normalizar(" ".join(str(f.get(k) or "") for k in ("sku", "titulo", "listing_id", "id")))
                for f in filas]
    return _Tabla(extra.get("generado_at"), filas, busqueda, extra)


def _cargar_indice_por_id(ruta: Path) -> dict[str, bytes]:
    """`{"<id>": {...}, ...}` → {id: JSON del valor comprimido}.

    curvas.json (~2.5k publicaciones × ~100 puntos) e historial.json (~5k × 150
    días) pueden pesar decenas de MB: en objetos de Python serían cientos de MB
    vivos para contestar UNA publicación a la vez. Se recorre el objeto de nivel
    superior con `raw_decode`, se guarda cada valor como texto comprimido y se
    decodifica solo el que se pide.
    """
    texto = ruta.read_text(encoding="utf-8")
    indice: dict[str, bytes] = {}
    pos = _ESPACIOS.match(texto, 0).end()
    if texto[pos:pos + 1] != "{":
        raise ValueError("se esperaba un objeto {id: ...}")
    pos = _ESPACIOS.match(texto, pos + 1).end()
    if texto[pos:pos + 1] == "}":
        return indice
    while True:
        if texto[pos:pos + 1] != '"':
            raise ValueError(f"llave inválida en la posición {pos}")
        clave, pos = json.decoder.scanstring(texto, pos + 1)
        pos = _ESPACIOS.match(texto, pos).end()
        if texto[pos:pos + 1] != ":":
            raise ValueError(f"falta ':' en la posición {pos}")
        pos = _ESPACIOS.match(texto, pos + 1).end()
        _, fin = _DECODIFICADOR.raw_decode(texto, pos)
        indice[clave] = zlib.compress(texto[pos:fin].encode("utf-8"), 1)
        pos = _ESPACIOS.match(texto, fin).end()
        sep = texto[pos:pos + 1]
        if sep == ",":
            pos = _ESPACIOS.match(texto, pos + 1).end()
            continue
        if sep == "}":
            return indice
        raise ValueError(f"se esperaba ',' o '}}' en la posición {pos}")


def _del_indice(indice: dict[str, bytes] | None, id_: str) -> Any:
    if not indice or id_ not in indice:
        return None
    return json.loads(zlib.decompress(indice[id_]).decode("utf-8"), parse_constant=_nan_a_none)


# ══════════════════════════════════════════════════════════════════════════════
#  Serie de snapshots (precio cobrado y recomendado por día)
# ══════════════════════════════════════════════════════════════════════════════

class _SerieSnapshots:
    """Índice compacto de `snapshots/<día>/{publicaciones,precios}.json`.

    Cada snapshot trae ~9k publicaciones; tenerlas como dicts sería ~1.5 MB por
    día y crece sin fin. Aquí cada id tiene una columna fija y cada día guarda
    dos `array('d')` (8 bytes por publicación): un año ≈ 50 MB en vez de ~500.
    Solo se parsea el día nuevo o el que cambió (el pipeline re-fotografía HOY
    si se recalcula).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._columna: dict[str, int] = {}
        self._dias: dict[str, tuple[tuple, array, array]] = {}
        self._revisado = 0.0  # monotonic de la última revisión de firmas

    @staticmethod
    def _firma(dia: str) -> tuple:
        base = _entorno.datos_dir() / "snapshots" / dia
        firma = []
        for nombre in ("publicaciones.json", "precios.json"):
            try:
                st = (base / nombre).stat()
                firma.append((st.st_mtime_ns, st.st_size))
            except FileNotFoundError:
                firma.append(None)
        return tuple(firma)

    def _col(self, id_: str) -> int:
        col = self._columna.get(id_)
        if col is None:
            col = self._columna[id_] = len(self._columna)
        return col

    def _cargar_dia(self, dia: str) -> tuple[array, array]:
        base = _entorno.datos_dir() / "snapshots" / dia
        valores: list[tuple[int, int, float]] = []  # (col, 0=cobrado|1=recomendado, valor)
        for nombre, campo, cual in (("publicaciones.json", "precio_cobrado", 0),
                                    ("precios.json", "precio_recomendado", 1)):
            try:
                datos = _cargar_json(base / nombre)
            except (FileNotFoundError, ValueError, UnicodeDecodeError) as exc:
                if not isinstance(exc, FileNotFoundError):
                    log.warning("laboratorio: snapshot %s/%s ilegible (%s)", dia, nombre, exc)
                continue
            filas = datos.get("filas") if isinstance(datos, dict) else datos
            for f in filas or []:
                if not isinstance(f, dict) or not f.get("id"):
                    continue
                v = f.get(campo)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                    valores.append((self._col(str(f["id"])), cual, float(v)))
        n = len(self._columna)
        cobrado, recomendado = array("d", [math.nan]) * n, array("d", [math.nan]) * n
        for col, cual, v in valores:
            (cobrado if cual == 0 else recomendado)[col] = v
        return cobrado, recomendado

    def _actualizar(self) -> None:
        # Revisar ~800 firmas (400 días × 2 archivos) en cada petición sobra: el
        # pipeline fotografía una vez al día. 30 s de retraso tras un recálculo.
        if time.monotonic() - self._revisado < 30:
            return
        self._revisado = time.monotonic()
        maximo = int(os.environ.get("LAB_HISTORIAL_MAX_DIAS") or 400)
        dias = almacen.dias_con_snapshot()[-maximo:]
        vivos = set(dias)
        for d in list(self._dias):
            if d not in vivos:
                del self._dias[d]
        for d in dias:
            firma = self._firma(d)
            ent = self._dias.get(d)
            if ent is None or ent[0] != firma:
                self._dias[d] = (firma, *self._cargar_dia(d))

    def puntos(self, id_: str) -> list[dict]:
        with self._lock:
            self._actualizar()
            col = self._columna.get(id_)
            if col is None:
                return []
            salida = []
            for dia in sorted(self._dias):
                _, cobrado, recomendado = self._dias[dia]
                c = cobrado[col] if col < len(cobrado) else math.nan
                r = recomendado[col] if col < len(recomendado) else math.nan
                if math.isnan(c) and math.isnan(r):
                    continue
                salida.append({"fecha": dia,
                               "precio_cobrado": None if math.isnan(c) else c,
                               "precio_recomendado": None if math.isnan(r) else r})
            return salida

    def dias(self) -> int:
        return len(self._dias)


_snapshots = _SerieSnapshots()


def _fusionar_historial(serie: list[dict], puntos: list[dict]) -> list[dict]:
    """historial.json (150 días reconstruidos) + un punto por snapshot.

    El precio recomendado del snapshot es LO QUE SE RECOMENDÓ ese día: manda
    sobre el de historial.json. El cobrado solo llena `precio_ofrecido` si el
    historial no lo trae (la reconstrucción del historial limpio es mejor fuente).
    """
    por_fecha: dict[str, dict] = {}
    salida: list[dict] = []
    for p in serie:
        if not isinstance(p, dict):
            continue
        q = dict(p)
        salida.append(q)
        if q.get("fecha"):
            por_fecha.setdefault(str(q["fecha"])[:10], q)
    for s in puntos:
        q = por_fecha.get(s["fecha"])
        if q is None:
            q = {"fecha": s["fecha"], "precio_realizado": None, "unidades": None, "visitas": None,
                 "precio_ofrecido": None, "precio_recomendado": None}
            salida.append(q)
            por_fecha[s["fecha"]] = q
        if q.get("precio_ofrecido") is None:
            q["precio_ofrecido"] = s["precio_cobrado"]
        if s["precio_recomendado"] is not None:
            q["precio_recomendado"] = s["precio_recomendado"]
        q["snapshot"] = True
    salida.sort(key=lambda p: str(p.get("fecha") or ""))
    return salida


# ══════════════════════════════════════════════════════════════════════════════
#  Filtros, orden y paginación en el servidor
# ══════════════════════════════════════════════════════════════════════════════

_RE_ORDEN = re.compile(r"^-?[a-z0-9_]+(\.[a-z0-9_]+){0,2}$")


def _valor(fila: dict, campo: str) -> Any:
    v: Any = fila
    for parte in campo.split("."):
        if not isinstance(v, dict):
            return None
        v = v.get(parte)
    return v


def _clave_orden(v: Any) -> tuple:
    # El primer elemento separa tipos: nunca se compara un número con un texto.
    if isinstance(v, bool):
        return (0, float(v))
    if isinstance(v, (int, float)):
        return (0, float(v))
    if isinstance(v, str):
        return (1, _normalizar(v))
    if isinstance(v, (list, dict)):
        return (2, len(v))
    return (3, str(v))


def _ordenar(filas: list[dict], orden: str, virtuales: dict[str, Callable[[dict], Any]]) -> list[dict]:
    """Los `null` van SIEMPRE al final, en ascendente y en descendente."""
    desc = orden.startswith("-")
    campo = orden.lstrip("-")
    obtener = virtuales.get(campo) or (lambda f: _valor(f, campo))
    presentes, ausentes = [], []
    for f in filas:
        v = obtener(f)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            ausentes.append(f)
        else:
            presentes.append((_clave_orden(v), f))
    presentes.sort(key=lambda x: x[0], reverse=desc)
    return [f for _, f in presentes] + ausentes


def _lista(param: str | None, mayusculas: bool = False) -> set[str] | None:
    if param is None:
        return None
    vals = {v.strip() for v in param.split(",") if v.strip()}
    if mayusculas:
        vals = {v.upper() for v in vals}
    return vals or None


def _booleano(param: str | None) -> bool | None:
    if param is None or param.strip() == "":
        return None
    p = param.strip().lower()
    if p in {"1", "true", "si", "sí", "yes"}:
        return True
    if p in {"0", "false", "no"}:
        return False
    raise ValueError(f"valor booleano inválido: {param!r}")


def _tokens(q: str | None) -> list[str]:
    return [t for t in _normalizar((q or "")[:200]).split() if t]


Predicado = Callable[[int, dict], bool]


def _filtrar(tabla: _Tabla, predicados: dict[str, Predicado],
             facetas: dict[str, tuple[set[str], Callable[[dict], Iterable[str]]]]
             ) -> tuple[list[dict], dict[str, dict[str, int]]]:
    """Aplica los filtros y cuenta facetas «ignorando su propio filtro».

    Así la píldora de cada cuenta dice cuántas filas tendría al elegirla con el
    resto de filtros vigentes, en vez de mostrar 0 para las no elegidas.
    """
    elegidas: list[dict] = []
    conteos: dict[str, dict[str, int]] = {n: defaultdict(int) for n in facetas}
    dims = list(predicados)
    for i, f in enumerate(tabla.filas):
        fallan = [d for d in dims if not predicados[d](i, f)]
        if not fallan:
            elegidas.append(f)
        if len(fallan) <= 2:
            for nombre, (ignoradas, etiquetas) in facetas.items():
                if all(d in ignoradas for d in fallan):
                    for e in etiquetas(f):
                        conteos[nombre][e] += 1
    return elegidas, {n: dict(c) for n, c in conteos.items()}


def _pagina(filas: list[dict], page: int, per_page: int) -> tuple[list[dict], int]:
    paginas = max(1, math.ceil(len(filas) / per_page)) if filas else 0
    a = (page - 1) * per_page
    return filas[a:a + per_page], paginas


def _json(contenido: Any, status: int = 200, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(contenido, status_code=status, headers=headers)


def _sin_datos(nombre: str) -> JSONResponse:
    return _json({"detail": f"aún no hay {nombre}: corre el pipeline", "sin_datos": True}, 404)


def _error_parametros(exc: Exception) -> JSONResponse:
    return _json({"detail": str(exc)}, 422)


# ══════════════════════════════════════════════════════════════════════════════
#  Pipeline programado (en un hilo, una corrida a la vez)
# ══════════════════════════════════════════════════════════════════════════════

_pipeline_lock = threading.Lock()
_pipeline: dict[str, Any] = {"corriendo": False, "inicio": None, "origen": None, "etapas": None,
                             "sin_ml": False, "ultima": None, "proxima": None}
_tareas: set[asyncio.Task] = set()
_RE_ETAPA = re.compile(r"^[a-z0-9_]{1,40}$")
_hora_avisada = False


def _auto_pipeline() -> bool:
    return (os.environ.get("LAB_AUTO_PIPELINE") or "").strip().lower() in {"1", "true", "si", "sí", "yes"}


def _hora_programada() -> tuple[int, int]:
    """LAB_PIPELINE_HORA_UTC ('HH:MM', default 09:00 = 03:00 CDMX).

    09:00 UTC queda lejos del cron de visitas de producción (12:00 UTC; el
    cliente de ML del laboratorio igual se pausa de 11:50 a 12:30) y de los ETL
    de las 06:15, y de madrugada en México casi no hay tráfico en kubera.
    """
    global _hora_avisada
    crudo = (os.environ.get("LAB_PIPELINE_HORA_UTC") or "09:00").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", crudo)
    valida = bool(m) and int(m.group(1)) <= 23 and int(m.group(2)) <= 59
    h, mi = (int(m.group(1)), int(m.group(2))) if valida else (9, 0)
    if not _hora_avisada:  # /estado la consulta en cada visita: avisar una vez basta
        _hora_avisada = True
        if not valida:
            log.warning("laboratorio: LAB_PIPELINE_HORA_UTC=%r inválida; uso 09:00", crudo)
        elif (11, 50) <= (h, mi) < (12, 30):
            log.warning("laboratorio: %02d:%02d UTC cae en la pausa de ML (11:50–12:30): "
                        "el pipeline esperará a que termine el cron de visitas", h, mi)
    return h, mi


def _siguiente(h: int, m: int) -> dt.datetime:
    ahora = dt.datetime.now(dt.timezone.utc)
    objetivo = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
    return objetivo if objetivo > ahora else objetivo + dt.timedelta(days=1)


def _resumir(res: Any) -> Any:
    """Lo que devolvió `pipeline.correr`, acotado: /estado no debe cargar megas."""
    try:
        texto = json.dumps(res, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(res)[:2000]
    if len(texto) <= 20000:
        return json.loads(texto, parse_constant=_nan_a_none)
    if isinstance(res, dict):
        return {"claves": sorted(map(str, res)), "recortado": True}
    return {"recortado": True}


def _correr_pipeline(etapas: list[str] | None, sin_ml: bool, origen: str) -> None:
    """Corre en un HILO (asyncio.to_thread). Libera el candado pase lo que pase."""
    t0 = time.monotonic()
    inicio = almacen.ahora_iso()
    ok, error, resumen = False, None, None
    try:
        from sandbox_precios import pipeline  # perezoso: lo escribe otro módulo y puede no existir aún

        res = pipeline.correr(etapas=etapas, sin_ml=sin_ml)
        ok = not (isinstance(res, dict) and res.get("ok") is False)
        resumen = _resumir(res)
        log.info("laboratorio: pipeline (%s) terminó en %.0fs ok=%s", origen, time.monotonic() - t0, ok)
    except Exception as exc:  # noqa: BLE001 — el servidor sigue vivo aunque el cálculo falle
        error = f"{type(exc).__name__}: {exc}"[:1000]
        if type(exc).__name__ == "EscrituraProhibida":
            log.error("laboratorio: ESCRITURA PROHIBIDA durante el pipeline (%s): %s — es un bug "
                      "del pipeline, no de los candados", origen, exc)
        else:
            log.exception("laboratorio: el pipeline (%s) falló", origen)
    finally:
        _pipeline.update(corriendo=False, ultima={
            "origen": origen, "inicio": inicio, "fin": almacen.ahora_iso(),
            "duracion_s": round(time.monotonic() - t0, 1), "ok": ok, "error": error,
            "etapas": etapas, "sin_ml": sin_ml, "resultado": resumen})
        _pipeline_lock.release()


def _lanzar_pipeline(etapas: list[str] | None, sin_ml: bool, origen: str) -> asyncio.Task | None:
    """Arranca una corrida si no hay otra. Se llama desde el loop; devuelve la tarea o None."""
    if not _pipeline_lock.acquire(blocking=False):
        return None
    _pipeline.update(corriendo=True, inicio=almacen.ahora_iso(), origen=origen,
                     etapas=etapas, sin_ml=sin_ml)
    try:
        tarea = asyncio.get_running_loop().create_task(
            asyncio.to_thread(_correr_pipeline, etapas, sin_ml, origen))
    except Exception:
        _pipeline["corriendo"] = False
        _pipeline_lock.release()
        raise
    _tareas.add(tarea)
    tarea.add_done_callback(_tareas.discard)
    return tarea


def _hay_estado() -> bool:
    return _ultimo("estado.json").exists()


async def _bucle_pipeline() -> None:
    h, m = _hora_programada()
    await asyncio.sleep(3)  # que el servidor termine de arrancar y conteste el healthcheck
    if not await asyncio.to_thread(_hay_estado):
        log.info("laboratorio: no hay ultimo/estado.json — primera corrida del pipeline al arrancar")
        tarea = _lanzar_pipeline(None, False, "arranque_sin_datos")
        if tarea is not None:
            await tarea
    while True:
        objetivo = _siguiente(h, m)
        _pipeline["proxima"] = objetivo.isoformat(timespec="minutes")
        while (resta := (objetivo - dt.datetime.now(dt.timezone.utc)).total_seconds()) > 0:
            await asyncio.sleep(min(resta, 300))
        tarea = _lanzar_pipeline(None, False, "programado")
        if tarea is None:
            log.info("laboratorio: tocaba la corrida programada pero ya hay una en curso; se salta")
            continue
        await tarea


def _precalentar() -> None:
    """Deja en memoria lo pesado para que la primera visita no espere el parseo."""
    t0 = time.monotonic()
    try:
        _cache.leer(_ultimo("estado.json"))
        _cache.leer(_ultimo("publicaciones.json"), _cargar_tabla)
        _cache.leer(_ultimo("precios.json"), _cargar_tabla)
        _cache.leer(_ultimo("curvas.json"), _cargar_indice_por_id)
        _cache.leer(_ultimo("historial.json"), _cargar_indice_por_id)
        _snapshots.puntos("")
        log.info("laboratorio: caché precalentada en %.1fs", time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001
        log.warning("laboratorio: precalentado incompleto (%s)", exc)


@asynccontextmanager
async def _vida(_app: FastAPI):
    modo, motivo = _modo_auth()
    tareas: list[asyncio.Task] = []
    if modo == "cerrado":
        log.error("LABORATORIO CERRADO: %s — todas las rutas responden 503 y el pipeline no corre", motivo)
    else:
        if modo == "abierto":
            log.warning("laboratorio: sin LAB_ACCESS_KEY — modo local ABIERTO (solo se permite fuera de Railway)")
        tareas.append(asyncio.create_task(asyncio.to_thread(_precalentar)))
        if _auto_pipeline():
            tareas.append(asyncio.create_task(_bucle_pipeline()))
            log.info("laboratorio: pipeline automático encendido (%02d:%02d UTC)", *_hora_programada())
    yield
    for t in tareas:
        t.cancel()
    if _pipeline["corriendo"]:
        log.warning("laboratorio: el servidor se apaga con una corrida del pipeline en curso")


# ══════════════════════════════════════════════════════════════════════════════
#  Aplicación y middleware
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(title="Laboratorio de precios (solo lectura)", version=VERSION_API, lifespan=_vida,
              docs_url=None, redoc_url=None, openapi_url=f"{PREFIJO}/openapi.json")

_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https:; font-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")


def _cabeceras_seguridad(resp: Response, ruta: str) -> None:
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("X-Robots-Tag", "noindex, nofollow")
    h.setdefault("Content-Security-Policy", _CSP)
    if _entorno.EN_RAILWAY:
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    if ruta.startswith(PREFIJO):
        # Costos y márgenes reales: que ningún proxy ni el disco del navegador los guarde.
        h.setdefault("Cache-Control", "no-store")


@app.middleware("http")
async def _portero(request: Request, call_next):
    ruta = request.url.path
    metodo = request.method.upper()
    modo, motivo = _modo_auth()
    es_api = ruta == PREFIJO or ruta.startswith(PREFIJO + "/")
    if modo == "cerrado" and ruta == f"{PREFIJO}/salud" and metodo in _METODOS_LECTURA:
        # 200 A PROPÓSITO: si el healthcheck fallara, Railway dejaría vivo el deploy
        # ANTERIOR (el que sí tenía llave) y quitar la llave no apagaría nada. Así el
        # deploy sin llave entra y lo que queda en el aire es un laboratorio cerrado.
        resp: Response = _json({"ok": False, "cerrado": True, "detail": f"laboratorio cerrado: {motivo}"})
    elif modo == "cerrado":
        resp = _json({"detail": f"laboratorio cerrado: {motivo}", "cerrado": True}, 503)
    elif metodo not in _METODOS_LECTURA and not (metodo == "POST" and ruta in _RUTAS_POST):
        permitido = "GET, HEAD, OPTIONS" + (", POST" if ruta in _RUTAS_POST else "")
        resp = _json({"detail": "laboratorio de solo lectura: método no permitido"}, 405,
                     headers={"Allow": permitido})
    elif es_api and modo == "llave" and ruta not in _RUTAS_SIN_AUTH:
        via = _autenticado(request)
        if via is None:
            resp = _json({"detail": "falta la llave del laboratorio", "requiere_llave": True}, 401,
                         headers={"WWW-Authenticate": 'Bearer realm="laboratorio"'})
        elif via == "cookie" and metodo == "POST" and not _mismo_origen(request):
            resp = _json({"detail": "origen no permitido"}, 403)
        else:
            resp = await call_next(request)
    else:
        resp = await call_next(request)
    _cabeceras_seguridad(resp, ruta)
    return resp


# ══════════════════════════════════════════════════════════════════════════════
#  Rutas /api/lab
# ══════════════════════════════════════════════════════════════════════════════

@app.get(f"{PREFIJO}/salud", include_in_schema=False)
def salud() -> JSONResponse:
    return _json({"ok": True, "version": VERSION_API, "datos": _hay_estado(),
                  "pipeline_corriendo": bool(_pipeline["corriendo"])})


@app.get(f"{PREFIJO}/sesion")
def sesion_estado(request: Request) -> JSONResponse:
    modo, _ = _modo_auth()
    return _json({"modo": modo, "requiere_llave": modo == "llave",
                  "autenticado": modo == "abierto" or _autenticado(request) is not None})


@app.post(f"{PREFIJO}/sesion")
async def sesion(request: Request) -> JSONResponse:
    """{"llave": "..."} → cookie `lab_sesion`. {"salir": true} → borra la cookie."""
    try:
        crudo = await request.body()
        cuerpo = json.loads(crudo) if crudo else {}
    except ValueError:
        return _json({"detail": "cuerpo JSON inválido"}, 422)
    if not isinstance(cuerpo, dict):
        return _json({"detail": "se esperaba un objeto JSON"}, 422)
    if cuerpo.get("salir"):
        resp = _json({"ok": True, "salir": True})
        resp.delete_cookie(COOKIE, path="/", secure=_entorno.EN_RAILWAY, httponly=True, samesite="lax")
        return resp
    modo, _ = _modo_auth()
    if modo == "abierto":
        return _json({"ok": True, "modo": "abierto",
                      "aviso": "sin LAB_ACCESS_KEY: el laboratorio está abierto (solo en local)"})
    ip = _ip(request)
    if _bloqueado(ip):
        return _json({"detail": "demasiados intentos; espera 15 minutos"}, 429,
                     headers={"Retry-After": str(_FALLOS_VENTANA_S)})
    llave = str(cuerpo.get("llave") or "")
    if not llave or not _iguales(llave, _llave()):
        _registrar_fallo(ip)
        log.warning("laboratorio: llave incorrecta desde %s", ip)
        return _json({"detail": "llave incorrecta"}, 401)
    resp = _json({"ok": True, "modo": "llave"})
    resp.set_cookie(COOKIE, _firma_sesion(_llave()), max_age=_DURACION_SESION_S, path="/",
                    httponly=True, secure=_entorno.EN_RAILWAY, samesite="lax")
    return resp


@app.get(f"{PREFIJO}/estado")
def estado() -> JSONResponse:
    base = _cache.leer(_ultimo("estado.json")) or _cache.leer(_entorno.datos_dir() / "estado.json") or {}
    res = dict(base) if isinstance(base, dict) else {}
    res.setdefault("generado_at", None)
    res.setdefault("etapas", {})
    res["snapshots"] = almacen.dias_con_snapshot()
    modo, _ = _modo_auth()
    res["modo_local"] = modo == "abierto"
    res["corriendo"] = bool(_pipeline["corriendo"])
    archivos = {}
    for nombre in _ARCHIVOS_ULTIMO:
        try:
            st = _ultimo(nombre).stat()
            archivos[nombre] = {"actualizado_at": dt.datetime.fromtimestamp(
                st.st_mtime, dt.timezone.utc).isoformat(timespec="seconds"), "bytes": st.st_size}
        except FileNotFoundError:
            archivos[nombre] = None
    h, m = _hora_programada()
    res["servidor"] = {
        "version": VERSION_API,
        "en_railway": _entorno.EN_RAILWAY,
        "modo_auth": modo,
        "aviso": ("Sin LAB_ACCESS_KEY: laboratorio ABIERTO en modo local. En Railway esto "
                  "sería 503.") if modo == "abierto" else None,
        "auto_pipeline": _auto_pipeline(),
        "hora_pipeline_utc": f"{h:02d}:{m:02d}",
        "pipeline": {k: _pipeline[k] for k in ("corriendo", "inicio", "origen", "etapas", "sin_ml",
                                               "proxima", "ultima")},
        "archivos": archivos,
        "web": str(_dir_web()) if (_dir_web() and not _entorno.EN_RAILWAY) else bool(_dir_web()),
    }
    return _json(res)


def _predicados_publicaciones(tabla: _Tabla, canal, cuenta, estado_, full, fuente_costo, q):
    canales, cuentas, estados = _lista(canal), _lista(cuenta, True), _lista(estado_)
    fuentes, completo, tokens = _lista(fuente_costo), _booleano(full), _tokens(q)
    return {
        "canal": lambda i, f: canales is None or f.get("canal") in canales,
        "cuenta": lambda i, f: cuentas is None or str(f.get("cuenta") or "").upper() in cuentas,
        "estado": lambda i, f: estados is None or f.get("estado") in estados,
        "full": lambda i, f: completo is None or bool(f.get("es_full")) == completo,
        "fuente_costo": lambda i, f: fuentes is None or (f.get("costo") or {}).get("fuente") in fuentes,
        "q": lambda i, f: not tokens or all(t in tabla.busqueda[i] for t in tokens),
    }


_FACETAS_PUBLICACIONES = {
    # conteos "canal" y "canal:cuenta" para las píldoras (ignoran canal y cuenta)
    "conteos": ({"canal", "cuenta"}, lambda f: (str(f.get("canal")),
                                               f"{f.get('canal')}:{f.get('cuenta')}")),
    "estado": ({"estado"}, lambda f: (str(f.get("estado")),)),
    "full": ({"full"}, lambda f: ("true" if f.get("es_full") else "false",)),
    "fuente_costo": ({"fuente_costo"}, lambda f: (str((f.get("costo") or {}).get("fuente")),)),
}


@app.get(f"{PREFIJO}/publicaciones")
def publicaciones(canal: str | None = None, cuenta: str | None = None, estado: str | None = None,
                  full: str | None = None, fuente_costo: str | None = None, q: str | None = None,
                  orden: str = "-unidades_30d", page: int = Query(1, ge=1),
                  per_page: int = Query(50, ge=1)) -> JSONResponse:
    """Filtros separados por coma (`cuenta=BEKURA,SANCORFASHION`); `orden` con '-' = descendente."""
    per_page = min(per_page, _PER_PAGE_MAX)
    if not _RE_ORDEN.match(orden):
        return _error_parametros(ValueError(f"orden inválido: {orden!r}"))
    tabla = _cache.leer(_ultimo("publicaciones.json"), _cargar_tabla)
    if tabla is None:
        return _json({"generado_at": None, "total": 0, "page": page, "per_page": per_page,
                      "paginas": 0, "orden": orden, "filas": [], "conteos": {}, "facetas": {},
                      "sin_datos": True})
    try:
        preds = _predicados_publicaciones(tabla, canal, cuenta, estado, full, fuente_costo, q)
    except ValueError as exc:
        return _error_parametros(exc)
    elegidas, facetas = _filtrar(tabla, preds, _FACETAS_PUBLICACIONES)
    filas, paginas = _pagina(_ordenar(elegidas, orden, {}), page, per_page)
    conteos = facetas.pop("conteos", {})
    return _json({"generado_at": tabla.generado_at, "total": len(elegidas), "page": page,
                  "per_page": per_page, "paginas": paginas, "orden": orden, "filas": filas,
                  "conteos": conteos, "facetas": facetas, "sin_datos": False})


def _delta(f: dict, campo: str) -> float | None:
    par = f.get(campo) or {}
    a, r = par.get("actual"), par.get("recomendado")
    if isinstance(a, (int, float)) and isinstance(r, (int, float)):
        return float(r) - float(a)
    return None


_VIRTUALES_PRECIOS: dict[str, Callable[[dict], Any]] = {
    "delta_utilidad_dia": lambda f: _delta(f, "utilidad_dia"),
    "delta_unidades_dia": lambda f: _delta(f, "unidades_dia"),
    "delta_visitas_dia": lambda f: _delta(f, "visitas_dia"),
}


def _predicados_precios(tabla: _Tabla, cuenta, estado_, razon, confianza, q):
    cuentas, estados, razones = _lista(cuenta, True), _lista(estado_), _lista(razon)
    confianzas, tokens = _lista(confianza), _tokens(q)
    return {
        "cuenta": lambda i, f: cuentas is None or str(f.get("cuenta") or "").upper() in cuentas,
        "estado": lambda i, f: estados is None or f.get("estado") in estados,
        "razon": lambda i, f: razones is None or bool(razones.intersection(f.get("razones") or ())),
        "confianza": lambda i, f: confianzas is None
        or (f.get("elasticidad") or {}).get("confianza") in confianzas,
        "q": lambda i, f: not tokens or all(t in tabla.busqueda[i] for t in tokens),
    }


_FACETAS_PRECIOS = {
    "conteos": ({"cuenta"}, lambda f: (str(f.get("cuenta")),)),
    "estado": ({"estado"}, lambda f: (str(f.get("estado")),)),
    "razon": ({"razon"}, lambda f: tuple(str(r) for r in (f.get("razones") or ()))),
    "confianza": ({"confianza"}, lambda f: (str((f.get("elasticidad") or {}).get("confianza")),)),
}


def _precios_filtrados(cuenta, estado_, razon, confianza, q, orden):
    tabla = _cache.leer(_ultimo("precios.json"), _cargar_tabla)
    if tabla is None:
        return None, [], {}
    elegidas, facetas = _filtrar(tabla, _predicados_precios(tabla, cuenta, estado_, razon, confianza, q),
                                 _FACETAS_PRECIOS)
    return tabla, _ordenar(elegidas, orden, _VIRTUALES_PRECIOS), facetas


@app.get(f"{PREFIJO}/precios")
def precios(cuenta: str | None = None, estado: str | None = None, razon: str | None = None,
            confianza: str | None = None, q: str | None = None, orden: str = "-unidades_dia.actual",
            page: int = Query(1, ge=1), per_page: int = Query(50, ge=1)) -> JSONResponse:
    """`razon` acepta varias (cualquiera de ellas). Orden virtual: delta_utilidad_dia, delta_unidades_dia."""
    per_page = min(per_page, _PER_PAGE_MAX)
    if not _RE_ORDEN.match(orden):
        return _error_parametros(ValueError(f"orden inválido: {orden!r}"))
    tabla, elegidas, facetas = _precios_filtrados(cuenta, estado, razon, confianza, q, orden)
    if tabla is None:
        return _json({"generado_at": None, "total": 0, "page": page, "per_page": per_page,
                      "paginas": 0, "orden": orden, "filas": [], "conteos": {}, "facetas": {},
                      "parametros": None, "sin_datos": True})
    filas, paginas = _pagina(elegidas, page, per_page)
    conteos = facetas.pop("conteos", {})
    return _json({"generado_at": tabla.generado_at, "total": len(elegidas), "page": page,
                  "per_page": per_page, "paginas": paginas, "orden": orden, "filas": filas,
                  "conteos": conteos, "facetas": facetas, "parametros": tabla.extra.get("parametros"),
                  "sin_datos": False})


@app.get(f"{PREFIJO}/curva/{{id_:path}}")
def curva(id_: str) -> JSONResponse:
    indice = _cache.leer(_ultimo("curvas.json"), _cargar_indice_por_id)
    if indice is None:
        return _sin_datos("curvas.json")
    valor = _del_indice(indice, id_)
    if valor is None:
        return _json({"detail": f"sin curva para {id_}"}, 404)
    return _json(valor)


@app.get(f"{PREFIJO}/historial/{{id_:path}}")
def historial(id_: str) -> JSONResponse:
    indice = _cache.leer(_ultimo("historial.json"), _cargar_indice_por_id)
    base = _del_indice(indice, id_)
    puntos = _snapshots.puntos(id_)
    if base is None and not puntos:
        if indice is None:
            return _sin_datos("historial.json")
        return _json({"detail": f"sin historial para {id_}"}, 404)
    serie = (base or {}).get("serie") if isinstance(base, dict) else None
    return _json({"id": id_, "serie": _fusionar_historial(list(serie or []), puntos),
                  "snapshots": puntos, "dias_snapshot": _snapshots.dias()})


def _documento(nombre: str) -> JSONResponse:
    datos = _cache.leer(_ultimo(nombre))
    return _sin_datos(nombre) if datos is None else _json(datos)


@app.get(f"{PREFIJO}/packing")
def packing() -> JSONResponse:
    datos = _cache.leer(_ultimo("packing100.json"))
    if datos is None:
        return _sin_datos("packing100.json")
    saltados = _cache.leer(_ultimo("packing_saltados.json")) or {}
    res = dict(datos) if isinstance(datos, dict) else {"filas": datos}
    res["saltados"] = saltados.get("filas", []) if isinstance(saltados, dict) else saltados
    return _json(res)


@app.get(f"{PREFIJO}/contenedores")
def contenedores() -> JSONResponse:
    return _documento("contenedores.json")


@app.get(f"{PREFIJO}/metricas")
def metricas() -> JSONResponse:
    return _documento("metricas.json")


@app.get(f"{PREFIJO}/parametros")
def parametros() -> JSONResponse:
    """parametros.json del paquete + los que usó el último cálculo (pueden diferir si se editó)."""
    datos = _cache.leer(Path(__file__).resolve().parent / "parametros.json") or {}
    res = dict(datos)
    tabla = _cache.leer(_ultimo("precios.json"), _cargar_tabla)
    res["_usados_en_ultimo_calculo"] = tabla.extra.get("parametros") if tabla else None
    return _json(res)


# ── CSV para Excel ────────────────────────────────────────────────────────────

_COLUMNAS_CSV: list[tuple[str, str]] = [
    ("id", "id"), ("sku", "sku"), ("cuenta", "cuenta"), ("listing_id", "listing_id"),
    ("titulo", "titulo"), ("estado", "estado"),
    ("precio_actual", "precio_actual"), ("precio_recomendado", "precio_recomendado"),
    ("cambio_pct", "cambio_pct"), ("precio_equilibrio", "precio_equilibrio"),
    ("precio_piso", "precio_piso"), ("precio_max_utilidad", "precio_max_utilidad"),
    ("precio_max_volumen", "precio_max_volumen"), ("precio_ref_competencia", "precio_ref_competencia"),
    ("fuente_ref", "fuente_ref"),
    ("unidades_dia_actual", "unidades_dia.actual"), ("unidades_dia_recomendado", "unidades_dia.recomendado"),
    ("visitas_dia_actual", "visitas_dia.actual"), ("visitas_dia_recomendado", "visitas_dia.recomendado"),
    ("conversion_actual", "conversion.actual"), ("conversion_recomendado", "conversion.recomendado"),
    ("utilidad_dia_actual", "utilidad_dia.actual"), ("utilidad_dia_recomendado", "utilidad_dia.recomendado"),
    ("margen_actual", "margen.actual"), ("margen_recomendado", "margen.recomendado"),
    ("elasticidad_beta", "elasticidad.beta"), ("elasticidad_beta_visitas", "elasticidad.beta_visitas"),
    ("elasticidad_fuente", "elasticidad.fuente"), ("elasticidad_confianza", "elasticidad.confianza"),
    ("elasticidad_n_semanas", "elasticidad.n_semanas"),
    ("stock_full", "stock.full"), ("stock_odoo", "stock.odoo"), ("cobertura_dias", "stock.cobertura_dias"),
    ("razones", "razones"), ("plan_modo", "plan.modo"), ("plan_pasos", "plan.pasos"),
    ("autorizacion", "autorizacion"),
]


def _celda(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            return ""
        return str(int(v)) if v.is_integer() else repr(round(v, 6))
    if isinstance(v, list):
        partes = []
        for x in v:
            if isinstance(x, dict) and "fecha" in x:  # pasos del plan: fecha=precio
                partes.append(f"{x.get('fecha')}={_celda(x.get('precio'))}")
            else:
                partes.append(_celda(x))
        v = " | ".join(partes)
    elif isinstance(v, dict):
        v = json.dumps(v, ensure_ascii=False)
    texto = str(v)
    # Excel ejecuta como fórmula lo que empieza con = + - @: un título así no debe correr nada.
    return "'" + texto if texto[:1] in ("=", "+", "-", "@", "\t", "\r") else texto


@app.get(f"{PREFIJO}/exportar/precios.csv")
def exportar_precios(cuenta: str | None = None, estado: str | None = None, razon: str | None = None,
                     confianza: str | None = None, q: str | None = None,
                     orden: str = "-unidades_dia.actual") -> Response:
    """Mismos filtros que /precios, sin paginar. UTF-8 con BOM para que Excel respete los acentos."""
    if not _RE_ORDEN.match(orden):
        return _error_parametros(ValueError(f"orden inválido: {orden!r}"))
    tabla, filas, _ = _precios_filtrados(cuenta, estado, razon, confianza, q, orden)
    if tabla is None:
        return _sin_datos("precios.json")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([c for c, _ in _COLUMNAS_CSV])
    for f in filas:
        w.writerow([_celda(_valor(f, ruta)) for _, ruta in _COLUMNAS_CSV])
    nombre = f"precios_laboratorio_{almacen.hoy_cdmx().isoformat()}.csv"
    return Response(("﻿" + buf.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@app.post(f"{PREFIJO}/recalcular")
async def recalcular(request: Request) -> JSONResponse:
    """Dispara una corrida: {"etapas": [...]|null, "sin_ml": bool}. 202 si arranca, 409 si ya corre."""
    try:
        crudo = await request.body()
        cuerpo = json.loads(crudo) if crudo else {}
    except ValueError:
        return _json({"detail": "cuerpo JSON inválido"}, 422)
    if not isinstance(cuerpo, dict):
        return _json({"detail": "se esperaba un objeto JSON"}, 422)
    etapas = cuerpo.get("etapas")
    if etapas is not None and (not isinstance(etapas, list)
                               or not all(isinstance(e, str) and _RE_ETAPA.match(e) for e in etapas)):
        return _json({"detail": "etapas debe ser una lista de nombres [a-z0-9_]"}, 422)
    sin_ml = bool(cuerpo.get("sin_ml", False))
    tarea = _lanzar_pipeline(etapas or None, sin_ml, "manual")
    estado_pipe = {k: _pipeline[k] for k in ("corriendo", "inicio", "origen", "etapas", "sin_ml")}
    if tarea is None:
        return _json({"detail": "ya hay una corrida del pipeline en curso", "pipeline": estado_pipe}, 409)
    log.info("laboratorio: recálculo manual (etapas=%s, sin_ml=%s)", etapas, sin_ml)
    return _json({"ok": True, "pipeline": estado_pipe}, 202)


# ══════════════════════════════════════════════════════════════════════════════
#  Web estática (export de Next en laboratorio/web/out)
# ══════════════════════════════════════════════════════════════════════════════

_TIPOS = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
          ".mjs": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".json": "application/json", ".txt": "text/plain; charset=utf-8", ".svg": "image/svg+xml",
          ".ico": "image/x-icon", ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
          ".woff": "font/woff", ".woff2": "font/woff2", ".map": "application/json",
          ".webmanifest": "application/manifest+json"}
_web_dir: Path | None = None


def _dir_web() -> Path | None:
    """LAB_WEB_DIR o laboratorio/web/out; se recuerda en cuanto aparece un index.html."""
    global _web_dir
    if _web_dir is not None:
        return _web_dir
    candidatos = []
    if os.environ.get("LAB_WEB_DIR"):
        candidatos.append(Path(os.environ["LAB_WEB_DIR"]))
    candidatos.append(_BACKEND.parent / "laboratorio" / "web" / "out")
    for c in candidatos:
        if (c / "index.html").is_file():
            _web_dir = c.resolve()
            return _web_dir
    return None


def _archivo(p: Path) -> FileResponse:
    tipo = _TIPOS.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    cache = ("public, max-age=31536000, immutable" if "/_next/static/" in p.as_posix()
             else "no-cache")
    return FileResponse(p, media_type=tipo, headers={"Cache-Control": cache})


@app.api_route("/{ruta:path}", methods=["GET", "HEAD"], include_in_schema=False)
def estaticos(ruta: str, request: Request) -> Response:
    if ruta == "api" or ruta.startswith("api/"):
        return _json({"detail": "no existe"}, 404)
    web = _dir_web()
    if web is None:
        if ruta in ("", "index.html"):
            return _json({"laboratorio": VERSION_API, "web": "no construida (LAB_WEB_DIR o laboratorio/web/out)",
                          "api": f"{PREFIJO}/estado"})
        return _json({"detail": "no existe"}, 404)
    try:
        destino = (web / ruta).resolve()
    except (OSError, ValueError):  # bytes nulos, nombres imposibles en el sistema de archivos
        return _json({"detail": "no existe"}, 404)
    if not destino.is_relative_to(web):
        return _json({"detail": "no existe"}, 404)
    if destino.is_file():
        return _archivo(destino)
    if destino.is_dir() and (destino / "index.html").is_file():
        if ruta and not ruta.endswith("/"):
            # El export usa trailingSlash: /precios → /precios/ para que el router del cliente cuadre.
            url = "/" + ruta + "/" + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(url, status_code=308)
        return _archivo(destino / "index.html")
    if ruta:
        html = (web / (ruta.rstrip("/") + ".html")).resolve()
        if html.is_relative_to(web) and html.is_file():
            return _archivo(html)
    if Path(ruta).suffix:  # un asset que no existe es 404, no la portada
        return Response("no existe", status_code=404, media_type="text/plain; charset=utf-8")
    return _archivo(web / "index.html")
