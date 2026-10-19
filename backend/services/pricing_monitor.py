"""
pricing_monitor.py — La foto del «Monitor de precios FULL» que sirve la pestaña
PRICING (v0.650.0, 15-oct-2026). Reemplaza en la barra a Radar.

QUÉ ES
    El monitor que armó José como artifact (14-oct-2026): las publicaciones FULL
    de BEKURA y SANCORFASHION con su precio (`sale_price` de ML), el precio
    normal, el stock en FULL, las ventas por día de kubera, las visitas por día
    de ML, la competencia revisada con fotos y la categoría revisada. Aquí NO se
    recalcula nada de eso: se sirve la foto tal cual la dejó
    `scripts/pricing_preparar_foto.py`.

    Las reglas de «Qué hacer» viven en el frontend
    (`frontend/components/pricing/reglas.ts`), como en el artifact: sus umbrales
    se editan en la pantalla y la tabla se recalcula al instante.

DE DÓNDE SALE LA FOTO
    1. `PRICING_FOTO_DIR` (por omisión `backend/data/pricing`):
       `monitor.json.gz` + `img/p*.jpg` (nuestras) + `img/s*.jpg` (rivales).
    2. Si ahí no hay `monitor.json.gz`: el repo PRIVADO `PRICING_GITHUB_REPO`
       (`PRICING_GITHUB_RUTA`, rama `PRICING_GITHUB_REF`) con
       `PRICING_GITHUB_TOKEN`. Los datos de venta por publicación no deben vivir
       en un repo público; esta es la puerta para tenerlos fuera de OMNICANAL.

SOLO LECTURA. No toca kubera, Woo, ML ni Odoo. Los archivos locales se leen en
`asyncio.to_thread` (regla 11: el disco también espera) y GitHub con httpx
asíncrono.

CACHÉ: 10 min en memoria, por proceso. El JSON se guarda COMPRIMIDO (≈0.9 MB):
el router lo entrega con `Content-Encoding: gzip` sin volver a comprimirlo.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from config import settings

log = logging.getLogger("omnicanal.pricing")

TTL_S = 600
ARCHIVO_DATOS = "monitor.json.gz"
# Las hojas de miniaturas: `p0`… (nuestras publicaciones) y `s0`… (rivales).
# Cualquier otro nombre se rechaza ANTES de armar una ruta o una URL.
NOMBRE_HOJA = re.compile(r"^[ps]\d{1,3}$")
_BACKEND = Path(__file__).resolve().parent.parent

_cache: dict[str, tuple[float, bytes]] = {}       # hojas de miniaturas
_foto: tuple[float, bytes, dict] | None = None     # (cuándo, JSON comprimido, meta)
_candado = asyncio.Lock()


class SinFoto(Exception):
    """No hay foto en ningún lado: ni en la carpeta ni en GitHub."""


class FalloFuente(Exception):
    """La foto existe en GitHub pero no se pudo leer (red, token, permiso)."""


def _carpeta() -> Path:
    ruta = Path(settings.pricing_foto_dir)
    return ruta if ruta.is_absolute() else _BACKEND / ruta


def origen() -> str:
    """De dónde saldría la foto ahora mismo: 'local', 'github' o 'ninguno'."""
    if (_carpeta() / ARCHIVO_DATOS).is_file():
        return "local"
    return "github" if settings.pricing_github_token.strip() else "ninguno"


def validar(gz: bytes) -> dict:
    """PURA. Abre la foto y devuelve su `meta`; si no es una foto, ValueError."""
    datos = json.loads(gzip.decompress(gz))
    if not isinstance(datos, dict) or not all(k in datos for k in ("meta", "pubs", "riv")):
        raise ValueError("la foto no trae meta, pubs y riv")
    if not isinstance(datos["pubs"], list) or not isinstance(datos["riv"], dict):
        raise ValueError("pubs debe ser lista y riv un objeto")
    return datos["meta"]


async def _github(relativa: str) -> bytes:
    repo = settings.pricing_github_repo.strip().strip("/")
    ruta = settings.pricing_github_ruta.strip().strip("/")
    url = f"https://api.github.com/repos/{repo}/contents/{ruta}/{relativa}"
    cab = {"Authorization": f"Bearer {settings.pricing_github_token.strip()}",
           "Accept": "application/vnd.github.raw+json",
           "X-GitHub-Api-Version": "2022-11-28"}
    try:
        async with httpx.AsyncClient(timeout=30) as cli:
            r = await cli.get(url, params={"ref": settings.pricing_github_ref}, headers=cab)
    except httpx.HTTPError as exc:
        raise FalloFuente(f"GitHub no contestó: {type(exc).__name__}") from exc
    if r.status_code == 404:
        raise SinFoto(f"{repo}/{ruta}/{relativa} no existe (o el token no lo ve)")
    if r.status_code != 200:
        raise FalloFuente(f"GitHub contestó {r.status_code} al pedir {relativa}")
    return r.content


def _leer_local(relativa: str) -> bytes | None:
    """BLOQUEANTE (disco). None si la carpeta no tiene foto."""
    if origen() != "local":
        return None
    archivo = _carpeta() / relativa
    if not archivo.is_file():
        raise SinFoto(f"falta {relativa} en {_carpeta()}")
    return archivo.read_bytes()


async def _leer(relativa: str) -> bytes:
    """Un archivo de la foto: de la carpeta si la hay; si no, de GitHub."""
    local = await asyncio.to_thread(_leer_local, relativa)
    if local is not None:
        return local
    if settings.pricing_github_token.strip():
        return await _github(relativa)
    raise SinFoto("No hay foto del monitor: ni en PRICING_FOTO_DIR ni PRICING_GITHUB_TOKEN.")


async def _con_cache(llave: str, leer) -> bytes:
    ahora = time.monotonic()
    hit = _cache.get(llave)
    if hit and ahora - hit[0] <= TTL_S:
        return hit[1]
    dato = await leer()
    _cache[llave] = (time.monotonic(), dato)
    return dato


async def foto() -> tuple[bytes, dict]:
    """El JSON comprimido de la foto y su `meta` (validada UNA vez por lectura)."""
    global _foto
    async with _candado:   # dos pestañas que abren a la vez no la bajan dos veces
        ahora = time.monotonic()
        if _foto and ahora - _foto[0] <= TTL_S:
            return _foto[1], _foto[2]
        gz = await _leer(ARCHIVO_DATOS)
        try:
            meta = await asyncio.to_thread(validar, gz)
        except (ValueError, OSError) as exc:   # JSON roto o gzip roto
            raise FalloFuente(f"La foto no se pudo abrir: {exc}") from exc
        _foto = (time.monotonic(), gz, meta)
        return gz, meta


async def hoja(nombre: str) -> bytes:
    """Una hoja de miniaturas (`p0`, `s12`…). Nombre inválido → SinFoto."""
    if not NOMBRE_HOJA.match(nombre or ""):
        raise SinFoto(f"hoja desconocida: {nombre!r}")
    relativa = f"img/{nombre}.jpg"
    return await _con_cache(relativa, lambda: _leer(relativa))


def olvidar() -> None:
    """Vacía la caché (pruebas, o tras dejar una foto nueva en la carpeta)."""
    global _foto, _hechos, _vivo, _promos, _salud, _precios
    _foto = None
    _hechos = None
    _vivo = None
    _promos = None
    _salud = None
    _precios = None
    _motivos.clear()
    _cancel_ml.clear()
    _serie.clear()
    _bruto_ml.clear()
    _cache.clear()


# ── HECHO (v0.654.0): los precios que sí se movieron en ML ───────────────────
#
# `hechos.json` (en la misma carpeta que la foto) lo arma
# `scripts/pricing_hechos.py` con la bitácora de lo aplicado: precio antes,
# precio nuevo, precio normal, desde cuándo. Aquí se le suma lo que pasó
# DESPUÉS del cambio, leído de kubera: piezas pagadas desde ese momento y el
# precio y stock que la publicación tiene hoy (`channel.listings`, sync de
# 15 min). Solo SELECT; si kubera no está, se entregan los movimientos sin eso.

ARCHIVO_HECHOS = "hechos.json"
# La promoción que tiene HOY cada publicación (columna «Promoción hoy»): foto con
# su hora que deja `scripts/pricing_promos.py` (solo GET a ML).
ARCHIVO_PROMOS = "promos.json"
TTL_HECHOS_S = 300
_hechos: tuple[float, dict] | None = None
_candado_hechos = asyncio.Lock()


def _leer_hechos() -> list[dict]:
    """BLOQUEANTE (disco). Sin archivo = ningún precio movido."""
    archivo = _carpeta() / ARCHIVO_HECHOS
    if not archivo.is_file():
        return []
    datos = json.loads(archivo.read_text(encoding="utf-8"))
    return [h for h in (datos.get("hechos") or []) if isinstance(h, dict) and h.get("id") and h.get("desde")]


def _leer_promos() -> dict:
    """BLOQUEANTE (disco). {"leido": …, "promos": {id: …}}; sin archivo, vacío."""
    archivo = _carpeta() / ARCHIVO_PROMOS
    if not archivo.is_file():
        return {"leido": None, "promos": {}}
    datos = json.loads(archivo.read_text(encoding="utf-8"))
    return {"leido": datos.get("leido"), "promos": datos.get("promos") or {}}


def _despues(hechos: list[dict]) -> dict[str, dict]:
    """BLOQUEANTE (kubera, solo SELECT). Por publicación: lo vendido desde su cambio y su precio de hoy."""
    from services import supabase_db as sdb

    if not hechos or not sdb.disponible():
        return {}
    ids = [h["id"] for h in hechos]
    desdes = [h["desde"] for h in hechos]
    ventas = sdb.fetch_all(
        """
        select h.item_id, sum(oi.cantidad)::int as pzs, count(distinct o.external_order_id)::int as ordenes,
               round(sum(oi.cantidad * oi.precio_unitario)::numeric, 2) as pesos
          from unnest(%(ids)s::text[], %(desdes)s::timestamptz[]) as h(item_id, desde)
          join channel.order_items oi on oi.canal = 'mercado_libre' and oi.item_id = h.item_id
          join channel.orders o on o.canal = oi.canal and o.cuenta = oi.cuenta and o.external_order_id = oi.external_order_id
         where o.creado_at >= h.desde and lower(coalesce(o.estado_canal, '')) <> 'cancelled'
         group by h.item_id
        """,
        {"ids": ids, "desdes": desdes},
    )
    hoy = sdb.fetch_all(
        """
        select distinct on (l.listing_id) l.listing_id, l.price_sale, l.stock_full, l.situacion, l.updated_at
          from channel.listings l
         where l.canal = 'mercado_libre' and l.listing_id = any(%(ids)s)
         order by l.listing_id, l.updated_at desc nulls last
        """,
        {"ids": ids},
    )
    out: dict[str, dict] = {i: {"pzs": 0, "ordenes": 0, "pesos": 0.0} for i in ids}
    for v in ventas:
        out[v["item_id"]].update(pzs=v["pzs"] or 0, ordenes=v["ordenes"] or 0, pesos=float(v["pesos"] or 0))
    for l in hoy:
        out[l["listing_id"]].update(
            cobra_hoy=float(l["price_sale"]) if l["price_sale"] is not None else None,
            stock_hoy=l["stock_full"], situacion=l["situacion"],
            leido=l["updated_at"].isoformat() if l["updated_at"] else None,
        )
    return out


async def hechos() -> dict:
    """Los precios movidos, con lo vendido desde el cambio, y la promoción de hoy de cada publicación. Caché de 5 min."""
    global _hechos
    async with _candado_hechos:
        ahora = time.monotonic()
        if _hechos and ahora - _hechos[0] <= TTL_HECHOS_S:
            return _hechos[1]
        lista = await asyncio.to_thread(_leer_hechos)
        promos = await promos_actuales()
        kubera = True
        try:
            despues = await asyncio.to_thread(_despues, lista)
        except Exception as exc:  # kubera caída o lenta: los movimientos se ven igual, sin lo de después
            log.warning("pricing: no se pudo leer lo vendido desde el cambio: %s", exc)
            despues, kubera = {}, False
        datos = {"hechos": [{**h, "despues": despues.get(h["id"])} for h in lista],
                 "kubera": kubera and bool(despues or not lista),
                 "promos": promos["promos"], "promos_leido": promos["leido"], "promos_origen": promos.get("origen"),
                 "leido": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _hechos = (time.monotonic(), datos)
        return datos


# ── EN VIVO (v0.655.0): stock FULL y ritmo de venta de AHORA ─────────────────
#
# La foto es de un día; el filtro «Se agota» y el low stock tienen que ser de
# este momento. Stock: Mercado Libre directo (`GET /items?ids=…`, de 20 en 20,
# `available_quantity` = lo disponible en FULL); la copia de kubera
# (`channel.listings`) se refresca por turnos (~80 publicaciones por hora) y
# puede traer horas de atraso. Ventas: kubera (`channel.orders`), que entra al
# minuto por el webhook. Solo GET a ML (el token solo se LEE, nunca se renueva)
# y solo SELECT a kubera, cada uno sin bloquear el loop (regla 11).

TTL_VIVO_S = 300
_vivo: tuple[float, dict] | None = None
_candado_vivo = asyncio.Lock()


def _marca_ventas() -> str | None:
    """BLOQUEANTE (kubera, SELECT con el índice de creado_at). La hora de la última orden de ML: si cambia, hubo venta."""
    from services import supabase_db as sdb

    if not sdb.disponible():
        return None
    fila = sdb.fetch_one(
        "select max(creado_at) as m from channel.orders where canal = 'mercado_libre' and creado_at > now() - interval '3 days'")
    return fila["m"].isoformat() if fila and fila.get("m") else None


def _ultimas(ids: list[str]) -> list[dict]:
    """BLOQUEANTE (kubera, solo SELECT). Las 15 ventas más recientes de ML de estas publicaciones (2 días)."""
    from services import supabase_db as sdb

    if not ids or not sdb.disponible():
        return []
    filas = sdb.fetch_all(
        """
        select oi.item_id, oi.sku, oi.cuenta, oi.cantidad, o.estado_canal, o.creado_at
          from channel.orders o
          join channel.order_items oi on oi.canal = o.canal and oi.cuenta = o.cuenta and oi.external_order_id = o.external_order_id
         where o.canal = 'mercado_libre' and o.creado_at > now() - interval '2 days' and oi.item_id = any(%(ids)s)
         order by o.creado_at desc
         limit 15
        """,
        {"ids": ids},
    )
    return [{"id": f["item_id"], "sku": f["sku"], "cuenta": f["cuenta"], "pzs": f["cantidad"],
             "estado": f["estado_canal"], "cuando": f["creado_at"].isoformat()} for f in filas]


def _pubs_de_foto(gz: bytes) -> list[tuple[str, str]]:
    """BLOQUEANTE (CPU). [(id, cuenta)] de las publicaciones de la foto."""
    return [(p["id"], p["c"]) for p in json.loads(gzip.decompress(gz)).get("pubs", []) if p.get("id") and p.get("c")]


def _ventas_vivo(ids: list[str]) -> dict[str, dict]:
    """BLOQUEANTE (kubera, solo SELECT). Piezas pagadas en los últimos 14 y 30 días, al minuto."""
    from services import supabase_db as sdb

    if not ids or not sdb.disponible():
        return {}
    filas = sdb.fetch_all(
        """
        select oi.item_id,
               coalesce(sum(oi.cantidad) filter (where o.creado_at >= now() - interval '14 days'), 0)::int as u14,
               coalesce(sum(oi.cantidad), 0)::int as u30
          from channel.order_items oi
          join channel.orders o on o.canal = oi.canal and o.cuenta = oi.cuenta and o.external_order_id = oi.external_order_id
         where oi.canal = 'mercado_libre' and oi.item_id = any(%(ids)s)
           and o.creado_at >= now() - interval '30 days' and lower(coalesce(o.estado_canal, '')) <> 'cancelled'
         group by oi.item_id
        """,
        {"ids": ids},
    )
    return {r["item_id"]: {"u14": r["u14"], "u30": r["u30"]} for r in filas}


async def _stock_vivo(pubs: list[tuple[str, str]]) -> dict[str, dict]:
    """Stock FULL de ML ahora mismo. Una cuenta sin token, o un lote que falla, se queda sin dato (no inventa)."""
    from services import meli

    out: dict[str, dict] = {}
    por_cuenta: dict[str, list[str]] = {}
    for i, c in pubs:
        por_cuenta.setdefault(c, []).append(i)
    async with httpx.AsyncClient(base_url="https://api.mercadolibre.com", timeout=30) as cli:
        for cuenta, ids in por_cuenta.items():
            tk = await meli.access_token_async(cuenta)
            if not tk:
                log.warning("pricing vivo: sin token de %s", cuenta)
                continue
            lotes = [ids[k:k + 20] for k in range(0, len(ids), 20)]
            sem = asyncio.Semaphore(4)

            async def uno(lote: list[str]) -> None:
                async with sem:
                    try:
                        r = await cli.get("/items", params={"ids": ",".join(lote), "attributes": "id,available_quantity,status"},
                                          headers={"Authorization": f"Bearer {tk}"})
                    except httpx.HTTPError as exc:
                        log.warning("pricing vivo: ML no contestó un lote: %s", type(exc).__name__)
                        return
                    if r.status_code != 200:
                        log.warning("pricing vivo: ML %s en un lote de %s", r.status_code, cuenta)
                        return
                    for x in r.json():
                        b = x.get("body") or {}
                        if x.get("code") == 200 and b.get("id"):
                            out[b["id"]] = {"stock": b.get("available_quantity"), "status": b.get("status")}

            await asyncio.gather(*(uno(l) for l in lotes))
    return out


async def vivo() -> dict:
    """«En vivo» completo: stock y ventas (al minuto) + precio de venta de ML (cada 5 min) + visitas (cada 30 min)."""
    base = await _vivo_base()
    extra: dict = {}
    precios = await precios_actuales()
    if precios:
        extra.update(precios=precios["precios"], precios_leido=precios["leido"])
    promos = await promos_actuales()
    if promos.get("visitas"):
        extra.update(visitas=promos["visitas"], visitas_leido=promos.get("leido"))
    return {**base, **extra} if extra else base


async def _vivo_base() -> dict:
    """
    {"leido", "marca", "pubs": {id: {stock, status, u14, u30}}, "ultimas": [...]} de las publicaciones de la foto.

    Se recalcula en cuanto entra una venta nueva de ML (cambia la hora de la última orden en kubera, que el
    webhook escribe al momento) o, sin ventas, cada 5 min. La pantalla pregunta cada 30 s: la mayoría de las
    veces solo cuesta ese SELECT.
    """
    global _vivo
    async with _candado_vivo:
        ahora = time.monotonic()
        try:
            marca = await asyncio.to_thread(_marca_ventas)
        except Exception as exc:  # noqa: BLE001
            log.warning("pricing vivo: no se pudo leer la última venta: %s", exc)
            marca = None
        if _vivo and ahora - _vivo[0] <= TTL_VIVO_S and (marca is None or marca == _vivo[1].get("marca")):
            return _vivo[1]
        gz, _ = await foto()
        pubs = await asyncio.to_thread(_pubs_de_foto, gz)
        ids = [i for i, _ in pubs]
        stock, ventas, ultimas = await asyncio.gather(
            _stock_vivo(pubs), asyncio.to_thread(_ventas_vivo, ids), asyncio.to_thread(_ultimas, ids), return_exceptions=True)
        if isinstance(ultimas, BaseException):
            log.warning("pricing vivo: últimas ventas fallaron: %s", ultimas)
            ultimas = []
        if isinstance(stock, BaseException):
            log.warning("pricing vivo: stock de ML falló: %s", stock)
            stock = {}
        kubera = not isinstance(ventas, BaseException)
        if not kubera:
            log.warning("pricing vivo: ventas de kubera fallaron: %s", ventas)
            ventas = {}
        res = {}
        for i in ids:
            s = stock.get(i)
            v = ventas.get(i, {"u14": 0, "u30": 0}) if kubera else None
            if s is None and v is None:
                continue
            res[i] = {"stock": s["stock"] if s else None, "status": s["status"] if s else None,
                      "u14": v["u14"] if v else None, "u30": v["u30"] if v else None}
        datos = {"leido": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "marca": marca, "pubs": res, "ultimas": ultimas,
                 "con_stock": sum(1 for x in res.values() if x["stock"] is not None), "total": len(ids), "kubera": kubera}
        _vivo = (time.monotonic(), datos)
        return datos


# ── CALIDAD Y EXPERIENCIA DE COMPRA (v0.659.0) ───────────────────────────────
#
# Las mide el barrido de `services/calidad_ml.py` (cada 60 min de 11 a 23 UTC,
# CALIDAD_ML_ENABLED) y las guarda en `enrich.listing_health`: métrica
# `calidad` (`/item/{id}/performance`: puntaje 0–100 y Profesional/Estándar/
# Básica) y `experiencia` (`/reputation/items/{id}/purchase_experience`: Buena/
# Media/Mala, o «sin datos» si ML no la calcula). Aquí solo se LEEN (SELECT):
# esta ruta no le pregunta nada a ML.

TTL_SALUD_S = 600
_salud: tuple[float, dict] | None = None
_candado_salud = asyncio.Lock()


def _salud_kubera(ids: list[str]) -> dict[str, dict]:
    """BLOQUEANTE (kubera, solo SELECT). Por publicación: su calidad y su experiencia de compra en ML."""
    from services import supabase_db as sdb

    if not ids:
        return {}
    if not sdb.disponible():
        raise FalloFuente("kubera no está disponible")
    filas = sdb.fetch_all(
        """
        select distinct on (listing_id, metrica) listing_id, metrica, estado, valor, nivel, nivel_canal,
               n_pendientes, detalle, motivo, medido_en, capturado_en
          from enrich.listing_health
         where canal = 'mercado_libre' and metrica in ('calidad', 'experiencia') and listing_id = any(%(ids)s)
         order by listing_id, metrica, capturado_en desc nulls last
        """,
        {"ids": ids},
    )
    out: dict[str, dict] = {}
    for f in filas:
        det = f["detalle"] if isinstance(f["detalle"], dict) else {}
        cuando = f["medido_en"] or f["capturado_en"]
        base = {"estado": f["estado"], "nivel": f["nivel"], "texto": f["nivel_canal"],
                "puntaje": float(f["valor"]) if f["valor"] is not None else None,
                "medido": cuando.isoformat() if cuando else None}
        if f["metrica"] == "calidad":
            out.setdefault(f["listing_id"], {})["c"] = {**base, "pendientes": f["n_pendientes"], "motivo": f["motivo"]}
        else:
            razon = det.get("razon") or []
            out.setdefault(f["listing_id"], {})["e"] = {
                **base, "color": det.get("color"), "razon": razon[0] if razon else None,
                "consecuencia": det.get("consecuencia"), "por_categoria": bool(det.get("por_categoria")),
            }
    return out


async def salud() -> dict:
    """{"leido", "kubera", "pubs": {id: {"c": calidad, "e": experiencia}}} de las publicaciones de la foto. Caché de 10 min."""
    global _salud
    async with _candado_salud:
        if _salud and time.monotonic() - _salud[0] <= TTL_SALUD_S:
            return _salud[1]
        gz, _ = await foto()
        ids = [i for i, _ in await asyncio.to_thread(_pubs_de_foto, gz)]
        kubera = True
        try:
            pubs = await asyncio.to_thread(_salud_kubera, ids)
        except Exception as exc:  # kubera caída o lenta: la tabla se ve igual, sin esta columna
            log.warning("pricing salud: no se pudo leer calidad/experiencia: %s", exc)
            pubs, kubera = {}, False
        datos = {"leido": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "kubera": kubera, "pubs": pubs, "total": len(ids)}
        if kubera:
            _salud = (time.monotonic(), datos)
        return datos


# ── MOTIVOS DE CANCELACIONES Y DEVOLUCIONES (v0.659.0) ───────────────────────
#
# Para la gráfica del detalle: el porqué de cada burbuja cancelada (o devuelta)
# de UNA publicación, que se pide al abrirla. Devoluciones: lo que ya guarda
# kubera en `channel.returns` (motivo del comprador y estado). Cancelaciones:
# kubera no guarda el porqué; se le pregunta a ML `GET /orders/{id}`
# (`cancel_detail`), solo GET, con el token que ya tiene el backend (sin
# renovarlo aquí). El porqué de una orden ya cancelada no cambia: se recuerda
# mientras viva el proceso.

ITEM_ML = re.compile(r"^MLM\d{6,14}$")
MAX_ML_MOTIVOS = 120          # órdenes que se le preguntan a ML por publicación (las más recientes)
TTL_MOTIVOS_S = 600
_motivos: dict[str, tuple[float, dict]] = {}
_cancel_ml: dict[str, dict] = {}

# Lo que dice ML en `cancel_detail.code`, en palabras de la pantalla. Lo que no
# esté aquí se muestra con su grupo y la descripción de ML tal cual.
CANCEL_ES = {
    "pack_splitted": "ML separó el carrito en otra orden (no es venta perdida)",
    "expired_order": "Venció el plazo de pago",
    "shipment_not_delivered": "El envío no se entregó",
    "item_status_closed": "La publicación estaba cerrada o pausada",
    "buyer_cancel_express": "El comprador canceló la compra",
    "buyer_cancel_after_shipping": "El comprador canceló después del envío",
    "out_of_stock": "Sin stock",
    "fraud": "Riesgo de fraude",
    "payment_rejected": "Pago rechazado",
}
# Si el código no está arriba, el grupo de ML dice lo esencial.
GRUPO_ES = {
    "mediations": "Cancelada por una mediación (reclamo del comprador)",
    "buyer": "La canceló el comprador",
    "shipment": "Problema con el envío",
    "item": "Problema con la publicación",
    "internal": "La canceló Mercado Libre",
    "fraud": "Riesgo de fraude",
    "payment": "Problema con el pago",
}
QUIEN_ES = {"buyer": "comprador", "meli": "Mercado Libre", "seller": "vendedor"}


def _motivos_kubera(item_id: str) -> tuple[list[dict], dict[str, dict]]:
    """BLOQUEANTE (kubera, solo SELECT). Órdenes de la publicación canceladas o con devolución, y sus devoluciones."""
    from services import supabase_db as sdb

    if not sdb.disponible():
        raise FalloFuente("kubera no está disponible")
    ordenes = sdb.fetch_all(
        """
        select o.external_order_id as orden, o.cuenta,
               to_char(o.creado_at at time zone 'America/Mexico_City', 'YYYY-MM-DD') as dia,
               lower(coalesce(o.estado_canal, '')) = 'cancelled' as cancelada,
               sum(oi.cantidad)::int as piezas, max(oi.precio_unitario) as precio
          from channel.order_items oi
          join channel.orders o on o.canal = oi.canal and o.cuenta = oi.cuenta and o.external_order_id = oi.external_order_id
         where oi.canal = 'mercado_libre' and oi.item_id = %(id)s and o.creado_at >= now() - interval '130 days'
           and (lower(coalesce(o.estado_canal, '')) = 'cancelled'
                or exists (select 1 from channel.returns r where r.canal = o.canal and r.external_order_id = o.external_order_id))
         group by o.external_order_id, o.cuenta, o.creado_at, o.estado_canal
         order by o.creado_at desc
        """,
        {"id": item_id},
    )
    devs: dict[str, dict] = {}
    if ordenes:
        for r in sdb.fetch_all(
            """
            select distinct on (external_order_id) external_order_id, motivo_texto, estado_titulo, motivo, tipo
              from channel.returns
             where canal = 'mercado_libre' and external_order_id = any(%(ords)s)
             order by external_order_id, creado_at desc nulls last
            """,
            {"ords": [o["orden"] for o in ordenes]},
        ):
            devs[r["external_order_id"]] = r
    return ordenes, devs


def _texto_cancelacion(cd: dict | None) -> tuple[str, str | None]:
    """PURA. `cancel_detail` de ML → (motivo en palabras, quién la pidió)."""
    if not cd:
        return "ML no dio el motivo", None
    codigo = cd.get("code") or ""
    texto = (CANCEL_ES.get(codigo) or GRUPO_ES.get(cd.get("group") or "")
             or " · ".join(x for x in (cd.get("group"), cd.get("description")) if x) or codigo or "sin motivo")
    return texto, QUIEN_ES.get(cd.get("requested_by") or "", cd.get("requested_by"))


async def _cancelaciones_ml(pares: list[tuple[str, str]]) -> dict[str, dict]:
    """Solo GET a ML. `cancel_detail` de cada orden (orden, cuenta); lo que falla se queda sin dato (no inventa)."""
    from services import meli

    faltan = [(o, c) for o, c in pares if o not in _cancel_ml]
    if faltan:
        tokens = {c: await meli.access_token_async(c) for c in {c for _, c in faltan}}
        sem = asyncio.Semaphore(4)
        async with httpx.AsyncClient(base_url="https://api.mercadolibre.com", timeout=20) as cli:
            async def una(orden: str, cuenta: str) -> None:
                tk = tokens.get(cuenta)
                if not tk:
                    return
                async with sem:
                    try:
                        r = await cli.get(f"/orders/{orden}", headers={"Authorization": f"Bearer {tk}"})
                    except httpx.HTTPError as exc:
                        log.warning("pricing motivos: ML no contestó %s: %s", orden, type(exc).__name__)
                        return
                if r.status_code == 200:
                    _cancel_ml[orden] = r.json().get("cancel_detail") or {}

            await asyncio.gather(*(una(o, c) for o, c in faltan))
    return {o: _cancel_ml[o] for o, _ in pares if o in _cancel_ml}


async def motivos(item_id: str) -> dict:
    """{"item", "leido", "kubera", "ml", "sin_consultar", "eventos": [{orden, dia, piezas, precio, tipo, motivo, quien, estado}]}."""
    if not ITEM_ML.match(item_id or ""):
        raise SinFoto("publicación no válida")
    guardado = _motivos.get(item_id)
    if guardado and time.monotonic() - guardado[0] <= TTL_MOTIVOS_S:
        return guardado[1]
    try:
        ordenes, devs = await asyncio.to_thread(_motivos_kubera, item_id)
    except Exception as exc:  # kubera caída: la gráfica se ve igual, sin motivos
        log.warning("pricing motivos: no se pudo leer kubera: %s", exc)
        return {"item": item_id, "leido": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "kubera": False, "ml": False,
                "sin_consultar": 0, "eventos": []}
    a_ml = [(o["orden"], o["cuenta"]) for o in ordenes if o["cancelada"] and o["orden"] not in devs]
    sin_consultar = max(0, len(a_ml) - MAX_ML_MOTIVOS)
    ml_ok = True
    try:
        cancel = await _cancelaciones_ml(a_ml[:MAX_ML_MOTIVOS])
    except Exception as exc:  # noqa: BLE001 — ML caído: las devoluciones de kubera se ven igual
        log.warning("pricing motivos: ML falló: %s", exc)
        cancel, ml_ok = {}, False
    eventos = []
    for o in ordenes:
        base = {"orden": o["orden"], "dia": o["dia"], "piezas": o["piezas"],
                "precio": float(o["precio"]) if o["precio"] is not None else None, "cancelada": o["cancelada"]}
        d = devs.get(o["orden"])
        if d:
            eventos.append({**base, "tipo": "devolucion", "motivo": d["motivo_texto"] or "sin motivo del comprador",
                            "estado": d["estado_titulo"], "quien": "comprador"})
        elif o["orden"] in cancel:
            texto, quien = _texto_cancelacion(cancel[o["orden"]])
            eventos.append({**base, "tipo": "cancelacion", "motivo": texto, "quien": quien, "estado": None})
        else:
            eventos.append({**base, "tipo": "cancelacion", "motivo": None, "quien": None, "estado": None})
    datos = {"item": item_id, "leido": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "kubera": True, "ml": ml_ok,
             "sin_consultar": sin_consultar, "eventos": eventos}
    _motivos[item_id] = (time.monotonic(), datos)
    return datos


# ── LA GRÁFICA DEL DETALLE, EN VIVO (v0.663.0) ───────────────────────────────
#
# La gráfica pintaba las ventas (`o`) y las visitas (`vis`) de la FOTO, y la foto
# es del 14-oct: nada de lo vendido después aparecía. Caso que lo destapó
# (Brandon, 19-oct): ORG-1016-BLN decía «29 pzs en 30 d» en la tabla —lo vivo— y
# su gráfica estaba vacía, porque antes del 14-oct no había vendido nada.
#
# Esta ruta trae la serie de AHORA de UNA publicación, al abrir su detalle:
#   · ventas por día de kubera (las órdenes que escribe el webhook, 130 días)
#   · visitas por día de ML (`/items/{id}/visits/time_window`, solo GET)
#
# EL PRECIO DE CADA VENTA es lo que pagó el comprador. Las líneas de kubera con
# comisión 0 guardan el NETO (ver la nota de SANCORFASHION; en octubre también
# aparecen en BEKURA): ORG-1016-BLN cobra $219 y kubera dice $168.10. Para esas
# se le pregunta a ML `GET /orders/{id}` (`order_items[].unit_price`), las más
# recientes primero y con tope; el precio de una orden ya hecha no cambia, así
# que se recuerda mientras viva el proceso. Las que no se alcanzaron se pintan
# con el neto y se cuentan en `sin_bruto`.

CUENTAS_ML = ("BEKURA", "SANCORFASHION")
MAX_ML_BRUTOS = 80
DIAS_VISITAS = 60
TTL_SERIE_S = 120
_serie: dict[str, tuple[float, dict]] = {}
_bruto_ml: dict[tuple[str, str], float] = {}


def _serie_kubera(item_id: str) -> list[dict]:
    """BLOQUEANTE (kubera, solo SELECT). Cada línea de venta de la publicación en 130 días, de la más nueva a la más vieja."""
    from services import supabase_db as sdb

    if not sdb.disponible():
        raise FalloFuente("kubera no está disponible")
    return sdb.fetch_all(
        """
        select o.external_order_id as orden, o.cuenta,
               to_char(o.creado_at at time zone 'America/Mexico_City', 'YYYY-MM-DD') as dia,
               lower(coalesce(o.estado_canal, '')) = 'cancelled' as cancelada,
               oi.cantidad::int as piezas, oi.precio_unitario as precio, oi.comision
          from channel.order_items oi
          join channel.orders o on o.canal = oi.canal and o.cuenta = oi.cuenta and o.external_order_id = oi.external_order_id
         where oi.canal = 'mercado_libre' and oi.item_id = %(id)s and o.creado_at >= now() - interval '130 days'
         order by o.creado_at desc
        """,
        {"id": item_id},
    )


def armar_ventas(lineas: list[dict], brutos: dict[tuple[str, str], float], item_id: str) -> tuple[list[dict], int]:
    """
    PURA. Líneas de kubera → burbujas de la gráfica: [{dia, precio, piezas, cancelada}] por día, precio y
    estado, más cuántas líneas se quedaron con el precio NETO (comisión 0 y sin precio de ML).
    """
    grupos: dict[tuple[str, float, bool], int] = {}
    sin_bruto = 0
    for l in lineas:
        precio = float(l["precio"]) if l.get("precio") is not None else None
        if precio is None or precio <= 0:
            continue
        if not float(l.get("comision") or 0):
            bruto = brutos.get((str(l["orden"]), item_id))
            if bruto:
                precio = bruto
            else:
                sin_bruto += 1
        k = (l["dia"], round(precio, 2), bool(l["cancelada"]))
        grupos[k] = grupos.get(k, 0) + int(l["piezas"] or 0)
    ventas = [{"dia": d, "precio": p, "piezas": n, "cancelada": c} for (d, p, c), n in sorted(grupos.items())]
    return ventas, sin_bruto


async def _brutos_ml(pares: list[tuple[str, str]], item_id: str) -> None:
    """Solo GET a ML. Llena `_bruto_ml` con el `unit_price` de la publicación en cada orden (orden, cuenta)."""
    from services import meli

    faltan = [(o, c) for o, c in pares if (o, item_id) not in _bruto_ml]
    if not faltan:
        return
    tokens = {c: await meli.access_token_async(c) for c in {c for _, c in faltan}}
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(base_url="https://api.mercadolibre.com", timeout=20) as cli:
        async def una(orden: str, cuenta: str) -> None:
            tk = tokens.get(cuenta)
            if not tk:
                return
            async with sem:
                try:
                    r = await cli.get(f"/orders/{orden}", headers={"Authorization": f"Bearer {tk}"})
                except httpx.HTTPError as exc:
                    log.warning("pricing serie: ML no contestó %s: %s", orden, type(exc).__name__)
                    return
            if r.status_code != 200:
                return
            for it in r.json().get("order_items") or []:
                if (it.get("item") or {}).get("id") == item_id and it.get("unit_price"):
                    _bruto_ml[(orden, item_id)] = float(it["unit_price"])

        await asyncio.gather(*(una(o, c) for o, c in faltan))


async def _visitas_ml(item_id: str, cuenta: str) -> dict[str, int] | None:
    """Solo GET a ML. {"YYYY-MM-DD": visitas} de los últimos `DIAS_VISITAS` días; None si ML no contestó."""
    from services import meli

    tk = await meli.access_token_async(cuenta)
    if not tk:
        return None
    try:
        async with httpx.AsyncClient(base_url="https://api.mercadolibre.com", timeout=20) as cli:
            r = await cli.get(f"/items/{item_id}/visits/time_window", params={"last": DIAS_VISITAS, "unit": "day"},
                              headers={"Authorization": f"Bearer {tk}"})
    except httpx.HTTPError as exc:
        log.warning("pricing serie: visitas de %s fallaron: %s", item_id, type(exc).__name__)
        return None
    if r.status_code != 200:
        log.warning("pricing serie: ML %s en visitas de %s", r.status_code, item_id)
        return None
    return {(x.get("date") or "")[:10]: int(x.get("total") or 0) for x in r.json().get("results") or [] if x.get("date")}


async def serie(item_id: str, cuenta: str) -> dict:
    """{"item", "cuenta", "hoy", "leido", "kubera", "visitas_ok", "sin_bruto", "ventas": [...], "visitas": {dia: n}}."""
    if not ITEM_ML.match(item_id or ""):
        raise SinFoto("publicación no válida")
    cuenta = (cuenta or "").strip().upper()
    if cuenta not in CUENTAS_ML:
        raise SinFoto("cuenta no válida")
    guardado = _serie.get(item_id)
    if guardado and time.monotonic() - guardado[0] <= TTL_SERIE_S:
        return guardado[1]
    hoy = datetime.now(CDMX).date().isoformat()
    try:
        lineas = await asyncio.to_thread(_serie_kubera, item_id)
        kubera = True
    except Exception as exc:  # noqa: BLE001 — kubera caída: la gráfica se queda con la foto
        log.warning("pricing serie: no se pudo leer kubera: %s", exc)
        lineas, kubera = [], False
    netas = [(str(l["orden"]), l["cuenta"]) for l in lineas if not float(l.get("comision") or 0)]
    netas = list(dict.fromkeys(netas))[:MAX_ML_BRUTOS]
    try:
        _, visitas = await asyncio.gather(_brutos_ml(netas, item_id), _visitas_ml(item_id, cuenta))
    except Exception as exc:  # noqa: BLE001 — ML caído: ventas con su precio de kubera, sin visitas nuevas
        log.warning("pricing serie: ML falló: %s", exc)
        visitas = None
    ventas, sin_bruto = armar_ventas(lineas, _bruto_ml, item_id)
    datos = {"item": item_id, "cuenta": cuenta, "hoy": hoy, "leido": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "kubera": kubera, "visitas_ok": visitas is not None, "sin_bruto": sin_bruto,
             "ventas": ventas, "visitas": visitas or {}}
    if kubera:
        _serie[item_id] = (time.monotonic(), datos)
    return datos

# ── PROMOCIONES EN VIVO (v0.656.0) ───────────────────────────────────────────
#
# Qué promoción pone el precio que se cobra, cupones, «lleva más», lo
# programado y —nuevo— a qué promociones PUEDE ENTRAR cada publicación (las
# «candidate» de ML: relámpago, oferta del día, descuento individual, Buen Fin…).
# El servidor las relee de ML cada 30 min EN SEGUNDO PLANO (solo GET, ~1,300
# llamadas, 6 a la vez) y mientras tanto sirve la última lectura; si nunca ha
# leído, sirve `promos.json` (la foto que deja `scripts/pricing_promos.py`).

NOMBRE_TIPO = {
    "LIGHTNING": "Oferta relámpago", "DOD": "Oferta del día", "SMART_LIGHTNING": "Relámpago co-fondeada",
    "PRICE_DISCOUNT": "Descuento individual", "SELLER_CAMPAIGN": "Campaña propia", "DEAL": "Campaña de ML",
    "SMART": "Co-fondeada con ML", "UNHEALTHY_STOCK": "Acelera tus ventas de Full", "PRICE_MATCHING": "Ajuste con MeLi",
    "VOLUME": "Lleva más, paga menos", "SELLER_COUPON_CAMPAIGN": "Cupón",
}
PRECIO = {"LIGHTNING", "DOD", "SMART_LIGHTNING", "PRICE_DISCOUNT", "SELLER_CAMPAIGN", "DEAL", "SMART", "UNHEALTHY_STOCK", "PRICE_MATCHING"}
RELAMPAGO = {"LIGHTNING", "SMART_LIGHTNING"}
CDMX = timezone(timedelta(hours=-6))
TTL_PROMOS_S = 1800
_promos: tuple[float, dict] | None = None
_tarea_promos: asyncio.Task | None = None


def _fecha(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=CDMX)


def mejor_cupon(precio: float | None, cupones: list[dict]) -> float | None:
    """PURA. El precio más bajo que deja el mejor cupón vigente a este precio (compra mínima y tope incluidos)."""
    if not precio or not cupones:
        return None
    mejor = 0.0
    for c in cupones:
        if c.get("minimo") and precio < c["minimo"]:
            continue
        if c.get("monto"):
            d = float(c["monto"])
        elif c.get("pct"):
            d = precio * float(c["pct"]) / 100
            if c.get("tope"):
                d = min(d, float(c["tope"]))
        else:
            continue
        mejor = max(mejor, d)
    return round(precio - mejor, 2) if mejor > 0 else None


def resumir(sp: dict, promos: list[dict], ahora: datetime, campanas: dict | None = None) -> dict:
    """
    PURA. De `sale_price` y la lista de promociones del ítem, lo que la pantalla muestra.

    `campanas` ({id: campaña de /seller-promotions/users}) trae la compra mínima y el tope de cada cupón, que la
    lista del ítem no dice; sin eso, el precio con cupón se calcula como si no hubiera mínimo.
    """
    campanas = campanas or {}
    meta = sp.get("metadata") or {}
    vivas, programadas, cupones, candidatas, volumen = [], [], [], [], None
    por_id = {}
    for p in promos:
        tipo, st = p.get("type"), p.get("status")
        nombre = (p.get("name") or "").strip() or NOMBRE_TIPO.get(tipo, tipo)
        if p.get("id"):
            por_id[p["id"]] = (tipo, nombre)
        ini, fin = _fecha(p.get("start_date")), _fecha(p.get("finish_date"))
        if tipo == "SELLER_COUPON_CAMPAIGN":
            # La campaña de cupón cubre a la publicación como «candidate»; vale mientras su fecha esté vigente.
            if (not ini or ini <= ahora) and (not fin or fin >= ahora):
                camp_c = campanas.get(p.get("id") or "", {})
                regla = {"minimo": camp_c.get("min_purchase_amount"), "tope": camp_c.get("max_purchase_amount")}
                if p.get("fixed_amount"):
                    cupones.append({"nombre": nombre, "monto": p["fixed_amount"], "pct": None, **regla})
                elif p.get("fixed_percentage"):
                    cupones.append({"nombre": nombre, "monto": None, "pct": p["fixed_percentage"], **regla})
            continue
        if tipo == "VOLUME" and st == "started":
            volumen = {"lleva": p.get("buy_quantity"), "pct": p.get("discount_percentage"), "nombre": nombre}
            continue
        if tipo not in PRECIO:
            continue
        if st == "started":
            vivas.append({"tipo": tipo, "etiqueta": NOMBRE_TIPO.get(tipo, tipo), "nombre": nombre, "precio": p.get("price") or None,
                          "hasta": (p.get("finish_date") or "")[:10] or None})
        elif st == "pending":
            programadas.append({"tipo": tipo, "etiqueta": NOMBRE_TIPO.get(tipo, tipo), "nombre": nombre, "precio": p.get("price") or None,
                                "desde": ini.astimezone(CDMX).strftime("%Y-%m-%d %H:%M") if ini else None})
        elif st == "candidate":
            stock = p.get("stock") or {}
            candidatas.append({
                "tipo": tipo, "etiqueta": NOMBRE_TIPO.get(tipo, tipo), "nombre": nombre,
                "sugerido": p.get("suggested_discounted_price") or p.get("price") or None,
                "min": p.get("min_discounted_price"), "max": p.get("max_discounted_price"),
                "stock_min": stock.get("min"), "stock_max": stock.get("max"),
                "meli_pct": p.get("meli_percentage"), "seller_pct": p.get("seller_percentage"),
                "desde": ini.astimezone(CDMX).strftime("%Y-%m-%d %H:%M") if ini else None,
            })
    # ¿Qué promoción pone el precio que se cobra?
    tipo_meta = (meta.get("promotion_type") or "").lower()
    camp = meta.get("campaign_id")
    if tipo_meta == "lightning":
        cobra = "LIGHTNING"
    elif camp and camp in por_id:
        cobra = por_id[camp][0]
    elif meta.get("promotion_id"):
        # Sin campaña: la oferta propia del ítem (descuento individual); si no hay, la viva de menor precio.
        pd = next((v for v in vivas if v["tipo"] == "PRICE_DISCOUNT"), None)
        cobra = "PRICE_DISCOUNT" if pd else (min(vivas, key=lambda v: v["precio"] or 9e9)["tipo"] if vivas else None)
    else:
        cobra = None
    nombre_cobra = por_id[camp][1] if camp and camp in por_id else next((v["nombre"] for v in vivas if v["tipo"] == cobra), None)
    return {
        "cobra": sp.get("amount"), "normal": sp.get("regular_amount"),
        # Lo que paga quien tiene un cupón (seguidores, recompra, carrito): ML lo muestra como «X % OFF con cupón».
        "con_cupon": mejor_cupon(sp.get("amount"), cupones),
        "por": {"tipo": cobra, "etiqueta": NOMBRE_TIPO.get(cobra, cobra), "nombre": nombre_cobra} if cobra else None,
        "vivas": vivas, "programadas": sorted(programadas, key=lambda x: x["desde"] or ""), "cupones": cupones, "volumen": volumen,
        "candidatas": candidatas, "relampago": any(c["tipo"] in RELAMPAGO for c in candidatas),
    }


async def _refrescar_promos() -> None:
    """Relee de ML las promociones de todas las publicaciones de la foto (solo GET). Corre en segundo plano."""
    global _promos
    from services import meli

    try:
        gz, _ = await foto()
        pubs = await asyncio.to_thread(_pubs_de_foto, gz)
        tokens = {c: await meli.access_token_async(c) for c in {c for _, c in pubs}}
        ahora = datetime.now(CDMX)
        res: dict[str, dict | None] = {}
        campanas: dict[str, dict] = {}   # las campañas de cada cuenta: compra mínima y tope de los cupones
        visitas: dict[str, int] = {}     # visitas de ML de los últimos 30 días, por publicación
        sem = asyncio.Semaphore(6)
        async with httpx.AsyncClient(base_url="https://api.mercadolibre.com", timeout=30) as cli:

            async def get(ruta: str, tk: str, **params):
                for intento in range(4):
                    try:
                        r = await cli.get(ruta, params=params, headers={"Authorization": f"Bearer {tk}"})
                    except httpx.HTTPError:
                        await asyncio.sleep(1 + intento)
                        continue
                    if r.status_code == 429:
                        await asyncio.sleep(2 + 3 * intento)
                        continue
                    return r.json() if r.status_code == 200 else None
                return None

            for c, tk in tokens.items():
                if not tk:
                    continue
                yo = await get("/users/me", tk)
                if yo and yo.get("id"):
                    lista = await get(f"/seller-promotions/users/{yo['id']}", tk, app_version="v2")
                    for x in (lista or {}).get("results") or []:
                        if x.get("id"):
                            campanas[x["id"]] = x

            async def uno(i: str, c: str) -> None:
                tk = tokens.get(c)
                if not tk:
                    return
                async with sem:
                    sp = await get(f"/items/{i}/sale_price", tk, context="channel_marketplace")
                    pr = await get(f"/seller-promotions/items/{i}", tk, app_version="v2")
                    vv = await get(f"/items/{i}/visits/time_window", tk, last=30, unit="day")
                if sp:
                    res[i] = resumir(sp, pr if isinstance(pr, list) else [], ahora, campanas)
                if isinstance(vv, dict) and vv.get("total_visits") is not None:
                    visitas[i] = int(vv["total_visits"])

            await asyncio.gather(*(uno(i, c) for i, c in pubs))
        if res:
            _promos = (time.monotonic(), {"leido": ahora.isoformat(timespec="minutes"), "promos": res, "visitas": visitas,
                                          "origen": "ml"})
            log.info("pricing: promociones releídas de ML: %s de %s", len(res), len(pubs))
        else:
            log.warning("pricing: la relectura de promociones no trajo nada; se queda la anterior")
    except Exception as exc:  # noqa: BLE001 — en segundo plano: que nunca tumbe nada
        log.warning("pricing: no se pudieron releer las promociones: %s", exc)


async def promos_actuales() -> dict:
    """La última lectura de promociones; si tiene más de 30 min, pide otra en segundo plano y sirve la que hay."""
    global _tarea_promos
    viejo = _promos is None or time.monotonic() - _promos[0] > TTL_PROMOS_S
    if viejo and (_tarea_promos is None or _tarea_promos.done()):
        _tarea_promos = asyncio.create_task(_refrescar_promos())
    if _promos:
        return _promos[1]
    archivo = await asyncio.to_thread(_leer_promos)
    return {**archivo, "origen": "foto"}


# ── PRECIO DE VENTA EN VIVO (v0.662.0) ───────────────────────────────────────
#
# «Precio hoy» tiene que cuadrar con lo que se cobra HOY (Brandon, 16-oct-2026):
# la foto del 14-oct traía, p. ej., TEC-1804-BLN a $3,655.95 sin descuento cuando
# ML lo cobra a $1,599 por ALWAYS ON OCT. El servidor relee `sale_price` de todas
# las publicaciones cada 5 min EN SEGUNDO PLANO (solo GET, 8 a la vez, ~30 s) y
# sirve la última lectura con «en vivo».

TTL_PRECIOS_S = 300
_precios: tuple[float, dict] | None = None
_tarea_precios: asyncio.Task | None = None


async def _refrescar_precios() -> None:
    """Relee de ML lo que se cobra en cada publicación de la foto (solo GET). Corre en segundo plano."""
    global _precios
    from services import meli

    try:
        gz, _ = await foto()
        pubs = await asyncio.to_thread(_pubs_de_foto, gz)
        tokens = {c: await meli.access_token_async(c) for c in {c for _, c in pubs}}
        res: dict[str, dict] = {}
        sem = asyncio.Semaphore(8)
        async with httpx.AsyncClient(base_url="https://api.mercadolibre.com", timeout=30) as cli:

            async def uno(i: str, c: str) -> None:
                tk = tokens.get(c)
                if not tk:
                    return
                async with sem:
                    for intento in range(3):
                        try:
                            r = await cli.get(f"/items/{i}/sale_price", params={"context": "channel_marketplace"},
                                              headers={"Authorization": f"Bearer {tk}"})
                        except httpx.HTTPError:
                            await asyncio.sleep(1 + intento)
                            continue
                        if r.status_code == 429:
                            await asyncio.sleep(2 + 3 * intento)
                            continue
                        if r.status_code == 200:
                            sp = r.json()
                            res[i] = {"cobra": sp.get("amount"), "normal": sp.get("regular_amount")}
                        return

            await asyncio.gather(*(uno(i, c) for i, c in pubs))
        if res:
            _precios = (time.monotonic(), {"leido": datetime.now(CDMX).isoformat(timespec="seconds"), "precios": res})
        else:
            log.warning("pricing: la relectura de precios no trajo nada; se queda la anterior")
    except Exception as exc:  # noqa: BLE001 — en segundo plano: que nunca tumbe nada
        log.warning("pricing: no se pudieron releer los precios: %s", exc)


async def precios_actuales() -> dict | None:
    """La última lectura de precios; si tiene más de 5 min, pide otra en segundo plano. None si nunca ha leído."""
    global _tarea_precios
    viejo = _precios is None or time.monotonic() - _precios[0] > TTL_PRECIOS_S
    if viejo and (_tarea_precios is None or _tarea_precios.done()):
        _tarea_precios = asyncio.create_task(_refrescar_precios())
    return _precios[1] if _precios else None
