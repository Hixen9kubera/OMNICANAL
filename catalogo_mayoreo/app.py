"""
app.py — El módulo del catálogo de mayoreo: la página, sus archivos y UN endpoint que escribe.

    GET  /                               la página
    GET  /catalogo.json · /fotos/mNNN.jpg  el catálogo (archivos que sube `publicar.py`)
    GET  /api/mayoreo/estado             cómo está el módulo: demo, activo o en pausa
    POST /api/mayoreo/pedidos            el pedido de una tienda → orden de venta en Odoo + aviso en Slack
    PUT  /api/mayoreo/admin/archivo/…    subir un catálogo nuevo (con la llave de administración)
    POST /api/mayoreo/admin/publicar     ponerlo a la vista, todo junto o nada
    GET  /api/mayoreo/admin/pedidos      los pedidos recibidos

POR QUÉ VIVE APARTE
  Es lo único de Kubera pensado para que lo abra gente de fuera. No tiene base de datos ni llaves
  del panel: si alguien lo revienta, no hay por dónde seguir. Lo que sabe del inventario es el
  archivo que se le sube; lo que puede hacer en Odoo es crear una orden de venta.

NACE SIN ENCENDER NADA
  `MAYOREO_PEDIDOS` vale `demo` por omisión: la página arma el pedido y enseña la respuesta sin
  mandarla. Con `activo` y TODO configurado (Odoo, Slack, tiendas) recibe pedidos; si falta algo se
  queda en pausa por su cuenta. Encenderlo es un flujo de negocio vivo: lo decide Brandon.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

import pedidos as P
import servicio as S

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("mayoreo")

AQUI = Path(__file__).resolve().parent
cfg = S.Config.del_entorno()

MAX_CUERPO = 400_000            # bytes de un pedido (2,000 renglones pesan ~110 KB)
ESPERA = 25.0                   # segundos que se espera a Odoo antes de contestar «en proceso»
_FOTO = re.compile(r"m\d{3}\.jpg")
_TOPES_SUBIDA = {"catalogo.json": 12_000_000, "foto": 4_000_000}

app = FastAPI(title="Catálogo de mayoreo", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=2000)

_CABECERAS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
                                "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
}


@app.middleware("http")
async def cabeceras(request: Request, call_next):
    respuesta = await call_next(request)
    for k, v in _CABECERAS.items():
        respuesta.headers.setdefault(k, v)
    return respuesta


def _error(status: int, codigo: str, mensaje: str) -> HTTPException:
    return HTTPException(status, detail={"codigo": codigo, "mensaje": mensaje})


# ── Freno de peticiones (en memoria: una sola réplica) ────────────────────────
class Cubos:
    """Token bucket por llave. `gastar` consume una ficha y dice si había."""

    def __init__(self, tope: float, por_minuto: float):
        self._tope, self._recarga = tope, por_minuto / 60.0
        self._cubos: dict[str, list[float]] = {}
        self._candado = threading.Lock()

    def _fichas(self, llave: str, ahora: float) -> list[float]:
        if len(self._cubos) > 20_000:
            self._cubos.clear()
        cubo = self._cubos.setdefault(llave, [self._tope, ahora])
        cubo[0] = min(self._tope, cubo[0] + (ahora - cubo[1]) * self._recarga)
        cubo[1] = ahora
        return cubo

    def hay(self, llave: str) -> bool:
        with self._candado:
            return self._fichas(llave, time.monotonic())[0] >= 1.0

    def gastar(self, llave: str) -> bool:
        with self._candado:
            cubo = self._fichas(llave, time.monotonic())
            if cubo[0] < 1.0:
                return False
            cubo[0] -= 1.0
            return True


_envios = Cubos(tope=8, por_minuto=8)          # pedidos (y sus reintentos) por IP
_fallos = Cubos(tope=5, por_minuto=0.5)        # códigos equivocados por IP: 5 seguidos y luego 1 cada 2 min


def ip_de(request: Request) -> str:
    """La IP del navegador. Detrás del proxy de Railway viene en X-Real-IP."""
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real[:45]
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[-1].strip()[:45]
    return (request.client.host if request.client else "desconocida")[:45]


async def _cuerpo(request: Request, tope: int) -> bytes:
    """El cuerpo, sin pasar del tope aunque la cabecera Content-Length mienta."""
    declarado = request.headers.get("content-length", "")
    if declarado.isdigit() and int(declarado) > tope:
        raise _error(413, "limite_pedido", "El envío es demasiado grande.")
    trozos, total = [], 0
    async for trozo in request.stream():
        total += len(trozo)
        if total > tope:
            raise _error(413, "limite_pedido", "El envío es demasiado grande.")
        trozos.append(trozo)
    return b"".join(trozos)


# ── El catálogo (archivos en el volumen) ──────────────────────────────────────
_cat: dict[str, Any] = {"mtime": None, "cat": None}
_cat_candado = threading.Lock()


def catalogo() -> P.Catalogo | None:
    """El catálogo a la vista, leído una vez por versión. None si todavía no se ha subido ninguno."""
    ruta = cfg.datos / "actual" / "catalogo.json"
    try:
        mtime = ruta.stat().st_mtime_ns
    except OSError:
        return None
    with _cat_candado:
        if _cat["mtime"] != mtime:
            _cat["cat"] = P.Catalogo.de_json(json.loads(ruta.read_text(encoding="utf-8")))
            _cat["mtime"] = mtime
        return _cat["cat"]


@app.get("/health", include_in_schema=False)
def salud() -> dict[str, Any]:
    cat = catalogo()
    return {"ok": True, "catalogo": cat.version if cat else None}


@app.get("/robots.txt", include_in_schema=False)
def robots() -> PlainTextResponse:
    return PlainTextResponse("User-agent: *\nDisallow: /\n", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/", include_in_schema=False)
def pagina() -> FileResponse:
    return FileResponse(AQUI / "web" / "index.html", media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})


@app.get("/catalogo.json", include_in_schema=False)
def catalogo_json() -> FileResponse:
    ruta = cfg.datos / "actual" / "catalogo.json"
    if not ruta.is_file():
        raise _error(503, "no_disponible", "El catálogo todavía no está cargado.")
    return FileResponse(ruta, media_type="application/json", headers={"Cache-Control": "no-cache"})


@app.get("/fotos/{nombre}", include_in_schema=False)
def foto(nombre: str) -> FileResponse:
    ruta = cfg.datos / "actual" / "fotos" / nombre
    if not _FOTO.fullmatch(nombre) or not ruta.is_file():
        raise HTTPException(404)
    # La página pide cada hoja con `?v=<versión del catálogo>`: un catálogo nuevo cambia la dirección.
    return FileResponse(ruta, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800, immutable"})


@app.get("/api/mayoreo/estado")
def estado() -> dict[str, Any]:
    cat = catalogo()
    return {"pedidos": cfg.modo(), "flujo": "instante" if cfg.confirmar else "revision", "vigencia": cfg.vigencia,
            "max_lineas": P.MAX_LINEAS, "catalogo": cat.version if cat else None}


# ── El pedido ─────────────────────────────────────────────────────────────────
_en_curso: dict[str, asyncio.Task] = {}


def _al_terminar(folio: str, tarea: asyncio.Task) -> None:
    _en_curso.pop(folio, None)
    if not tarea.cancelled() and tarea.exception() is not None:
        log.error("%s: el pedido falló a medio camino: %r", folio, tarea.exception())


@app.post("/api/mayoreo/pedidos")
async def crear_pedido(request: Request):
    if cfg.modo() != "activo":
        raise _error(503, "no_disponible", "Por ahora no estamos recibiendo pedidos por aquí. Escríbele a tu ejecutivo de ventas.")
    ip = ip_de(request)
    if not _envios.gastar(ip) or not _fallos.hay(ip):
        raise _error(429, "limite_peticiones", "Espera un minuto y vuelve a enviar. Tu pedido no se duplica.")
    try:
        cuerpo = json.loads(await _cuerpo(request, MAX_CUERPO))
    except ValueError:
        raise _error(422, "pedido_invalido", "El pedido no se pudo leer.") from None
    codigo = cuerpo.get("codigo_tienda") if isinstance(cuerpo, dict) else None
    tienda = cfg.tienda_de(codigo) if isinstance(codigo, str) and 0 < len(codigo) <= 40 else None
    if tienda is None:
        _fallos.gastar(ip)
        log.warning("código de tienda no válido desde %s", ip)
        raise _error(401, "codigo_invalido", "Revisa que esté bien escrito. Si lo copiaste, quita espacios al inicio o al final.")
    if not tienda.activa:
        raise _error(403, "codigo_revocado", "Este código ya no está activo. Pide uno nuevo a tu ejecutivo de ventas.")
    cat = await asyncio.to_thread(catalogo)
    if cat is None:
        raise _error(503, "no_disponible", "El catálogo todavía no está cargado.")
    try:
        llave, notas, lineas, subs = P.validar(cuerpo, cat)
    except P.Rechazo as r:
        raise _error(r.status, r.codigo, r.mensaje) from None

    # El mismo pedido reenviado recibe lo mismo: primero lo ya contestado, luego lo que sigue en curso.
    folio = P.folio_de(tienda.huella, llave)
    previo = await asyncio.to_thread(S.guardado, cfg, folio)
    if previo is not None:
        return previo
    tarea = _en_curso.get(folio)
    if tarea is None:
        tarea = asyncio.create_task(asyncio.to_thread(S.procesar, cfg, tienda, folio, notas, lineas, subs))
        _en_curso[folio] = tarea
        tarea.add_done_callback(lambda t, f=folio: _al_terminar(f, t))
    try:
        return await asyncio.wait_for(asyncio.shield(tarea), ESPERA)
    except asyncio.TimeoutError:
        # Un pedido grande tarda minutos en Odoo. El hilo sigue; la página vuelve a preguntar con la misma llave.
        return JSONResponse({"folio": folio, "estado": "en_proceso"}, status_code=202)
    except Exception:  # noqa: BLE001 — Odoo no contestó o rechazó la orden
        log.exception("%s: no se pudo procesar", folio)
        raise _error(502, "intenta_de_nuevo", "No pudimos apartar en este momento. Vuelve a enviar en un minuto: tu pedido no se duplica.") from None


# ── Administración: subir un catálogo nuevo y ver los pedidos ─────────────────
def _admin(request: Request) -> None:
    if not cfg.admin_key:
        raise HTTPException(404)
    ip = ip_de(request)
    if not _fallos.hay(ip):
        raise HTTPException(429)
    if not hmac.compare_digest(request.headers.get("x-admin-key", "").encode(), cfg.admin_key.encode()):
        _fallos.gastar(ip)
        raise HTTPException(401)


@app.put("/api/mayoreo/admin/archivo/{ruta:path}", include_in_schema=False)
async def subir(ruta: str, request: Request) -> dict[str, Any]:
    """Deja un archivo del catálogo NUEVO en la antesala. No se ve hasta `publicar`."""
    _admin(request)
    if ruta == "catalogo.json":
        tope = _TOPES_SUBIDA["catalogo.json"]
    elif ruta.startswith("fotos/") and _FOTO.fullmatch(ruta[6:]):
        tope = _TOPES_SUBIDA["foto"]
    else:
        raise HTTPException(400, "Solo catalogo.json y fotos/mNNN.jpg.")
    datos = await _cuerpo(request, tope)
    if ruta.endswith(".jpg") and not datos.startswith(b"\xff\xd8"):
        raise HTTPException(400, "Eso no es un JPEG.")
    destino = cfg.datos / "nuevo" / ruta

    def _escribir() -> None:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(datos)

    await asyncio.to_thread(_escribir)
    return {"ok": True, "archivo": ruta, "bytes": len(datos)}


def _publicar() -> dict[str, Any]:
    nuevo, actual, anterior = cfg.datos / "nuevo", cfg.datos / "actual", cfg.datos / "anterior"
    try:
        doc = json.loads((nuevo / "catalogo.json").read_text(encoding="utf-8"))
        cat = P.Catalogo.de_json(doc)
        hojas = int(doc["fotos"]["hojas"])
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise HTTPException(409, f"El catálogo nuevo no se pudo leer: {exc}") from None
    faltan = [n for n in range(hojas) if not (nuevo / "fotos" / f"m{n:03d}.jpg").is_file()]
    if not cat.productos or faltan:
        raise HTTPException(409, f"El catálogo nuevo está incompleto: faltan {len(faltan)} hojas de fotos.")
    shutil.rmtree(anterior, ignore_errors=True)
    if actual.exists():
        actual.rename(anterior)
    nuevo.rename(actual)
    log.info("catálogo %s a la vista: %s productos, %s hojas de fotos", cat.version, len(cat.productos), hojas)
    return {"ok": True, "version": cat.version, "corte": cat.corte, "productos": len(cat.productos), "hojas": hojas}


@app.post("/api/mayoreo/admin/publicar", include_in_schema=False)
async def publicar(request: Request) -> dict[str, Any]:
    """Pone a la vista el catálogo de la antesala, si está completo. El anterior se conserva una vez."""
    _admin(request)
    return await asyncio.to_thread(_publicar)


def _pedidos(folio: str | None) -> Any:
    carpeta = cfg.datos / "pedidos"
    if folio:
        if not re.fullmatch(r"MAY-[A-Z2-7]{8}", folio):
            raise HTTPException(404)
        try:
            return json.loads((carpeta / f"{folio}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HTTPException(404) from None
    salida = []
    for ruta in sorted(carpeta.glob("MAY-*.json"), key=lambda r: r.stat().st_mtime, reverse=True)[:500]:
        try:
            d = json.loads(ruta.read_text(encoding="utf-8"))
            r = d["respuesta"]
            salida.append({"folio": d["folio"], "tienda": d["tienda"], "recibido": d["recibido"], "estado": r["estado"],
                           "ordenes": r["ordenes"], "renglones": len(r["lineas"]), "pedido_piezas": r["pedido_piezas"],
                           "pedido_total": r["pedido_total"], "piezas": r["piezas"], "total": r["total"]})
        except (OSError, ValueError, KeyError):
            continue
    return salida


@app.get("/api/mayoreo/admin/pedidos", include_in_schema=False)
async def pedidos_recibidos(request: Request, folio: str | None = None) -> Any:
    _admin(request)
    return await asyncio.to_thread(_pedidos, folio)


log.info("catálogo de mayoreo · pedidos: %s (pedido: %s) · flujo: %s · tiendas activas: %s · datos: %s",
         cfg.modo(), cfg.pedidos, "aparta al instante" if cfg.confirmar else "pasa a revisión",
         sum(1 for t in cfg.tiendas if t.activa), cfg.datos)
if cfg.pedidos == "activo" and cfg.faltantes():
    log.warning("PEDIDOS EN PAUSA aunque se pidió `activo`. Falta: %s", "; ".join(cfg.faltantes()))
