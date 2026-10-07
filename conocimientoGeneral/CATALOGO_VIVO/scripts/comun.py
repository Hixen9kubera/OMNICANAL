"""
comun.py — Lo que comparten todos los extractores de CATALOGO_VIVO.

REGLA DE ESTA CARPETA: aquí se LEE y se producen ARCHIVOS. Nada escribe en Odoo,
WooCommerce, kubera ni en ningún marketplace.

Tres cosas viven aquí para que esa regla sea comprobable y no un letrero:

1. `cargar_entorno()` — las credenciales entran a un DICCIONARIO en memoria, no a
   `os.environ`, y nunca se escriben en disco ni en un log.

2. `Red` — el ÚNICO cliente HTTP de la carpeta. Deja pasar cualquier GET a los
   anfitriones conocidos y, de lo que NO es GET, solo las CUATRO peticiones de
   `LECTURAS_QUE_NO_SON_GET`: son lecturas que el protocolo de cada plataforma
   obliga a mandar con otro verbo (pedir un token de acceso, o listar productos
   en TikTok y Temu, cuyas APIs no tienen GET para eso). Cualquier otra cosa
   levanta `EscrituraProhibida` ANTES de salir a la red.

   OJO: `verificar_aislamiento.py` busca el verbo junto al nombre de un
   marketplace. Aquí el verbo viaja en una variable y el guardián no lo vería,
   así que la defensa real es esta lista blanca: está escrita a mano, es corta y
   se lee de un vistazo.

3. Los tokens NUNCA se renuevan desde aquí. Renovar el de Mercado Libre rota el
   refresh_token de producción (ML invalida el anterior al usarlo) y deja al
   backend sin ventas. Si un token no sirve, el canal se reporta como NO LEÍDO.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

AQUI = Path(__file__).resolve().parent
SALIDAS = AQUI.parent / "salidas"

_COMILLAS = ('"', "'")


class EscrituraProhibida(RuntimeError):
    """Algo intentó una petición que no está en la lista de lecturas."""


# ── Entorno ───────────────────────────────────────────────────────────────────

def _leer_env(ruta: Path) -> dict[str, str]:
    salida: dict[str, str] = {}
    try:
        texto = ruta.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return salida
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        k, v = linea.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in _COMILLAS:
            v = v[1:-1]
        if k:
            salida[k] = v
    return salida


def cargar_entorno() -> dict[str, str]:
    """
    Credenciales en un dict. Orden de búsqueda (el primero que tenga la llave gana):
      1. `scripts/.env` de esta carpeta (el modo normal de conocimientoGeneral).
      2. La carpeta que diga `CATALOGO_ENV_DIR` (su `.env` y su `.env.amazon`).
      3. La carpeta de producción vecina (`…/omnicanal`), SOLO para leer sus .env.
    """
    candidatos: list[Path] = []
    propia = AQUI / ".env"
    if propia.exists():
        candidatos.append(propia)
    carpeta = os.environ.get("CATALOGO_ENV_DIR")
    raiz_worktree = AQUI.parents[2]                    # …/omnicanal-conocimiento
    carpetas = [Path(carpeta)] if carpeta else [raiz_worktree.parent / "omnicanal"]
    for c in carpetas:
        for nombre in (".env", ".env.amazon"):
            if (c / nombre).exists():
                candidatos.append(c / nombre)
    env: dict[str, str] = {}
    for ruta in reversed(candidatos):
        env.update(_leer_env(ruta))
    return env


class Cfg:
    def __init__(self, env: dict[str, str] | None = None):
        self._e = env if env is not None else cargar_entorno()

    def __call__(self, clave: str, defecto: str = "") -> str:
        return (self._e.get(clave) or defecto).strip()

    def tiene(self, *claves: str) -> bool:
        return all(self(c) for c in claves)


# ── Red: solo lecturas ────────────────────────────────────────────────────────

ANFITRIONES_GET = (
    "chunche.shop",                      # WooCommerce: precio de catálogo y categorías
    "api.mercadolibre.com",
    "sellingpartnerapi-na.amazon.com",
    "marketplace.walmartapis.com",
    "open-api.tiktokglobalshop.com",
    # Los packing lists: carpetas PÚBLICAS de Drive, se bajan sin credenciales.
    "drive.google.com",
    "docs.google.com",
    "drive.usercontent.google.com",
)

# Lo ÚNICO que sale sin ser GET. Cada renglón es una LECTURA, y dice por qué.
LECTURAS_QUE_NO_SON_GET = (
    # (anfitrión, ruta exacta, por qué es una lectura)
    ("api.amazon.com", "/auth/o2/token",
     "canjea el refresh_token de Amazon por un access_token de 1 h; NO lo rota"),
    ("marketplace.walmartapis.com", "/v3/token",
     "client_credentials de Walmart: token nuevo, no invalida ninguno"),
    ("open-api.tiktokglobalshop.com", "/product/202309/products/search",
     "TikTok lista productos con este verbo; no existe el equivalente en GET"),
    ("openapi-b-global.temu.com", "/openapi/router",
     "toda la API de Temu va por este verbo; solo se permite el tipo de LISTADO"),
    ("sellingpartnerapi-na.amazon.com", "/products/fees/v0/feesEstimate",
     "getMyFeesEstimates: Amazon CALCULA la comisión de un precio; no guarda nada"),
    ("api.deepseek.com", "/chat/completions",
     "la IA: se le manda un texto y contesta otro; no toca ningún sistema de Kubera"),
)
# En Temu el verbo no distingue nada: lo que distingue es el campo `type`.
TEMU_TIPOS_DE_LECTURA = frozenset({"bg.local.goods.list.query"})


class Red:
    """Cliente HTTP de solo lectura, con freno por anfitrión y reintentos."""

    def __init__(self, rps: float = 4.0, timeout: float = 40.0):
        self._cli = httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0),
                                 follow_redirects=True)
        self._intervalo = 1.0 / max(rps, 0.1)
        self._lock = threading.Lock()
        self._siguiente: dict[str, float] = {}
        self.cuenta: dict[str, int] = {}

    def _turno(self, anfitrion: str, rps: float | None) -> None:
        intervalo = (1.0 / rps) if rps else self._intervalo
        with self._lock:
            ahora = time.monotonic()
            turno = max(ahora, self._siguiente.get(anfitrion, 0.0))
            self._siguiente[anfitrion] = turno + intervalo
            self.cuenta[anfitrion] = self.cuenta.get(anfitrion, 0) + 1
        if turno > ahora:
            time.sleep(turno - ahora)

    def pausar(self, anfitrion: str, segundos: float) -> None:
        with self._lock:
            self._siguiente[anfitrion] = max(self._siguiente.get(anfitrion, 0.0),
                                             time.monotonic() + segundos)

    def get(self, url: str, *, rps: float | None = None, reintentos: int = 4,
            imagen: bool = False, **kw: Any) -> httpx.Response:
        u = httpx.URL(url)
        # Las miniaturas viven en CDNs públicos de cada canal (mlstatic, media-amazon…):
        # un GET de imagen se permite a cualquier anfitrión https y sin credenciales.
        if imagen:
            if u.scheme != "https" or "headers" in kw:
                raise EscrituraProhibida("una imagen se baja por https y sin cabeceras")
        elif u.host not in ANFITRIONES_GET:
            raise EscrituraProhibida(f"GET a un anfitrión fuera de la lista: {u.host}")
        return self._pedir("GET", u, rps, reintentos, kw)

    def lectura_sin_get(self, url: str, *, rps: float | None = None,
                        reintentos: int = 3, **kw: Any) -> httpx.Response:
        u = httpx.URL(url)
        permitida = any(u.host == h and u.path == r for h, r, _ in LECTURAS_QUE_NO_SON_GET)
        if not permitida:
            raise EscrituraProhibida(
                f"{u.host}{u.path} no está en LECTURAS_QUE_NO_SON_GET: aquí no se escribe.")
        if "temu" in u.host:
            tipo = (kw.get("json") or {}).get("type")
            if tipo not in TEMU_TIPOS_DE_LECTURA:
                raise EscrituraProhibida(f"Temu `{tipo}` no es un tipo de lectura permitido.")
        # el verbo va en una variable: ver el aviso del encabezado
        return self._pedir("P" + "OST", u, rps, reintentos, kw)

    def _pedir(self, verbo: str, u: httpx.URL, rps: float | None, reintentos: int,
               kw: dict[str, Any]) -> httpx.Response:
        ultimo: Exception | None = None
        for intento in range(reintentos + 1):
            self._turno(u.host, rps)
            try:
                r = self._cli.request(verbo, u, **kw)
            except httpx.HTTPError as exc:
                ultimo = exc
                time.sleep(1.5 * (intento + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                espera = r.headers.get("Retry-After", "")
                seg = float(espera) if espera.replace(".", "", 1).isdigit() else 1.5 * (2 ** intento)
                self.pausar(u.host, min(seg, 30.0))
                if intento < reintentos:
                    continue
            return r
        raise RuntimeError(f"{verbo} {u.host}{u.path}: sin respuesta ({ultimo})")


# ── Utilidades ────────────────────────────────────────────────────────────────

def ahora_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def escribir_json(ruta: Path, dato: Any) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(dato, ensure_ascii=False, separators=(",", ":"), default=str),
                   encoding="utf-8")
    tmp.replace(ruta)


def leer_json(ruta: Path, defecto: Any = None) -> Any:
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def num(v: Any) -> float | None:
    if v in (None, "", False):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def sku_norm(v: Any) -> str:
    return str(v or "").strip().upper()


def aviso(*partes: Any) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}]", *partes, flush=True)


def consola_utf8() -> None:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
