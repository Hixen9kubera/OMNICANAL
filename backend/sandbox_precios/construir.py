"""Ensamblado final del laboratorio → `ultimo/{publicaciones,historial,metricas,estado}.json`.

DISENO §6. Este módulo NO calcula nada nuevo: une lo que dejaron las etapas
anteriores (crudos de kubera y de la API de ML, `costos.json`, `precios.json`,
`elasticidades.json`) en los cuatro documentos que sirve `api.py`, con una
regla por encima de todas (DISENO §0.7): un dato ausente es ``null`` con su
aviso, nunca un 0 inventado.

    publicaciones()  todas las publicaciones de los 6 canales, una fila por
                     PUBLICACIÓN (no por SKU)
    historial()      150 días reconstruidos por publicación de ML + un punto
                     por snapshot
    metricas()       tablero por cuenta y por canal, embudo, elasticidad y
                     palancas
    estado()         frescura de cada fuente, bitácora del pipeline

Decisiones, cada una con la medición que la sostiene (28-sep-2026):

- **El universo de ML son LISTING_IDS de la API, no filas de kubera.**
  `channel.listings` guarda una fila por (sku, cuenta, canal): 79 publicaciones
  vivas no tienen fila (11 activas: TEC-1339-ROS, ORG-0265-PLA…) y 93 listing_id
  apuntan a dos SKUs (base y variante). Aquí cada ítem de `ml_universo.jsonl` es
  una fila; kubera se une por (cuenta, listing_id) y, si hay dos filas, gana la
  del SKU que el propio ítem declara (SELLER_SKU).
- **Precio cobrado**: `/sale_price` del día (las 570 activas y las 1,258 FULL
  pausadas que vendieron) > `price_sale` de kubera si se observó en 48 h >
  `item.price` de la API (en las pausadas sin medir). `item.price` NO es lo que
  se cobra: en 371 de 570 activas se cobra menos (mediana 0.85×). La fuente va
  en `fuente_precio`.
- **Visitas 30 d**: la serie diaria de la API (1,899 FULL, días completos) manda
  sobre la caché de kubera (`visits_30d`, ventana móvil que el cron de
  producción sobrescribe a las 12:00 UTC). La fuente va en `visitas_fuente`.
  Ventana = los 30 días COMPLETOS antes de hoy: la API descarta hoy y las
  ventas de hoy están a medias, así la conversión compara lo mismo con lo mismo.
- **Unidades e ingreso 30 d** salen de `channel.sales_daily` por item_id: una
  publicación sin renglones vendió 0 (el dato es completo desde el 1-may), eso
  sí es un cero medido.
- **Otros canales** (Amazon, Walmart, Temu, TikTok) salen de kubera con la
  economía `supuesto` de `canales.py`. Amazon no registra cambios desde el
  18-sep y Walmart es una foto del 17-ago: el aviso de frescura se calcula con el
  `max(updated_at)` de cada canal, no se escribe a mano.
- **Estado de Temu 4/7 = `otra` con `estado_detalle: "puede_estar_activa"`**: su
  cubeta dice literalmente "Activo o inactivo" (`services/temu.py`). Llamarla
  `activa` sería afirmar lo que Temu no dice; `metricas.por_canal.temu` la cuenta
  aparte.
- **Palancas**: la más grande no es el precio sino la disponibilidad (1,413 FULL
  pausadas, 1,387 sin stock). `reactivar_full` = pausada FULL sin stock FULL con
  piezas libres en Odoo, ordenada por la velocidad con la que vendía cuando tenía
  oferta (`elasticidades.json` → base de 28 días NO censurados).

Solo lee archivos de `LAB_DATOS_DIR` y escribe en `ultimo/` (escritura atómica
de `almacen`). Síncrono: desde una corrutina va en `asyncio.to_thread`.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sandbox_precios import _entorno  # noqa: E402

_entorno.cargar()
from sandbox_precios import almacen, canales, costos_lab, economia  # noqa: E402

log = logging.getLogger("laboratorio.construir")

VERSION = "lab-0.1"
CUENTAS_ML = ("BEKURA", "SANCORFASHION")
CANALES = ("mercado_libre", "amazon", "walmart", "temu", "tiktok")
VENTANA_DIAS = 30
DIAS_HISTORIAL = 150
MAX_LISTA_COMPETENCIA = 6        # renglones de la SERP del SKU en `competencia.lista`
MAX_LISTA_COMPETENCIA_BEST = 5   # más vendidos de la hoja (solo si no hay SERP)
# Un canal cuyo max(updated_at) tiene más de 2 días no se está leyendo: Amazon
# (18-sep) y Walmart (17-ago) el 28-sep. TikTok y Temu los reescribe el censo
# a diario, así que 48 h basta para distinguir "vivo" de "congelado".
HORAS_CANAL_CONGELADO = 48
# price_sale de kubera solo se refresca en activas (precios_venta.py, 80/h):
# en pausadas puede tener semanas. Más de 48 h = no se usa como cobrado.
HORAS_PRICE_SALE_VIGENTE = 48
BITACORA_MAX = 30
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")

# Tags de ML que dicen algo de la calidad o de una oportunidad de la publicación.
# Medido en 5,098 ítems: los demás son casi universales (immediate_payment
# 5,098, user_product_listing 5,077, good_quality_thumbnail 5,070,
# cart_eligible 4,973) o de operación (resale_enabled, standard_price_by_quantity).
_TAGS_CALIDAD = {
    "poor_quality_thumbnail", "poor_quality_picture", "incomplete_technical_specs",
    "moderation_penalty", "incomplete_compatibilities", "feedback_out_of_stock",
    "dragged_bids_and_visits", "catalog_listing_eligible", "catalog_boost",
}
_RE_TAG_CALIDAD = re.compile(r"^(poor_|incomplete_)|penalty|infraction|unhealthy")

_ESTADO_ML = {"active": "activa", "paused": "pausada", "under_review": "en_revision",
              "closed": "cerrada", "inactive": "inactiva"}
# Mismas tablas que `services/publicaciones_panel.py` (_MAPA_AMAZON, TikTok,
# Walmart y `services/temu.ESTADOS`), plegadas al vocabulario cerrado de DISENO
# (activa|pausada|en_revision|cerrada|inactiva|otra). Lo que el vocabulario no
# tiene (no comprable, borrador, rechazada, puede estar activa) va en
# `estado_detalle` en vez de aplastarse a "activa".
_ESTADO_OTROS: dict[str, dict[str, tuple[str, str | None]]] = {
    "amazon": {"BUYABLE": ("activa", None), "PUBLISHED": ("activa", None),
               "DISCOVERABLE": ("otra", "no_comprable"), "CLOSED": ("cerrada", None)},
    "tiktok": {"ACTIVATE": ("activa", None), "SELLER_DEACTIVATED": ("pausada", None),
               "PENDING": ("en_revision", None), "DELETED": ("cerrada", None),
               "DRAFT": ("otra", "borrador"), "FAILED": ("otra", "rechazada")},
    "walmart": {"PUBLISHED": ("activa", None), "UNPUBLISHED": ("pausada", None),
                "SYSTEM_PROBLEM": ("otra", "rechazada")},
    "temu": {"4/7": ("otra", "puede_estar_activa"), "5/None": ("otra", "borrador"),
             "2/8": ("pausada", "incompleta"), "3/3": ("pausada", "incompleta"),
             "3/2": ("pausada", "incompleta"), "2/4": ("pausada", "incompleta"),
             "3/1": ("pausada", "incompleta")},
}
_COLUMNA_ESTADO = {"amazon": ("situacion", "upper"), "tiktok": ("status", "upper"),
                   "walmart": ("status", "upper"), "temu": ("status", None)}


# ── utilidades ────────────────────────────────────────────────────────────────
def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _r(v: Any, d: int = 2) -> float | None:
    x = _f(v)
    return None if x is None else round(x, d)


def _sku(v: Any) -> str | None:
    s = str(v or "").strip().upper()
    return s or None


def _ts(s: Any) -> dt.datetime | None:
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        try:
            t = dt.datetime.combine(dt.date.fromisoformat(str(s)[:10]), dt.time(0))
        except ValueError:
            return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _dedup(xs: Iterable[str]) -> list[str]:
    vistos: dict[str, None] = {}
    for x in xs:
        if x:
            vistos.setdefault(x, None)
    return list(vistos)


def _mediana(xs: Iterable[float | None]) -> float | None:
    v = [x for x in xs if x is not None]
    return round(median(v), 4) if v else None


def _parametros() -> dict:
    try:
        return json.loads(Path(__file__).with_name("parametros.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _crudo(nombre: str, dias: dict[str, str], max_dias: int = 7) -> Any:
    """El crudo más reciente (≤ `max_dias`) y anota de qué día salió."""
    p = almacen.ultimo_crudo(nombre, max_dias=max_dias)
    if p is None:
        dias[nombre] = None
        return None
    dias[nombre] = p.parent.name
    return almacen.leer_jsonl(p) if nombre.endswith(".jsonl") else almacen.leer_json(p)


def frescura_canales(filas: Iterable[dict]) -> dict[str, dict[str, Any]]:
    """Por canal de kubera: ``{filas, max_updated_at, ultimo_cambio_masivo, cambiadas_48h}``.

    `max(updated_at)` NO sirve para saber si un canal se está leyendo: medido el
    28-sep, Amazon llevaba 10 días sin cambios y UNA fila editada a mano a las
    22:48 (VAC-0493-EST) la hacía ver "fresca". `updated_at` lo sella un trigger
    solo cuando un campo cambia, así que un sync vivo mueve MUCHAS filas; aquí la
    frescura es el último día con cambios en ≥1% de las filas (mínimo 10):
    Amazon → 18-sep (25 filas), Walmart → 17-ago (235), TikTok/Temu → hoy (el
    censo reescribe todas).
    """
    por: dict[str, list[dt.datetime]] = defaultdict(list)
    for f in filas:
        lista = por[str(f.get("canal") or "").lower()]
        t = _ts(f.get("updated_at"))
        if t:
            lista.append(t)
    ahora = dt.datetime.now(dt.timezone.utc)
    out: dict[str, dict[str, Any]] = {}
    for canal, ts in por.items():
        if not ts:
            out[canal] = {"filas": 0, "max_updated_at": None, "ultimo_cambio_masivo": None, "cambiadas_48h": 0}
            continue
        dias = Counter(t.date() for t in ts)
        umbral = max(10, 0.01 * len(ts))
        masivos = [d for d, n in dias.items() if n >= umbral]
        ult = max(masivos) if masivos else None
        out[canal] = {
            "filas": len(ts), "max_updated_at": max(ts).isoformat(),
            "ultimo_cambio_masivo": max(t for t in ts if t.date() == ult).isoformat() if ult else None,
            "cambiadas_48h": sum(1 for t in ts if (ahora - t).total_seconds() <= HORAS_CANAL_CONGELADO * 3600),
        }
    return out


def _aviso_congelado(canal: str, max_updated: Any, ahora: dt.datetime) -> str | None:
    """`amazon_sin_cambios_desde_18_sep` si el canal no cambia hace > 48 h.

    El texto coincide con el vocabulario de la web (`lib/vocabulario.ts`):
    amazon_sin_cambios_desde_18_sep, walmart_sin_cambios_desde_17_ago.
    """
    t = _ts(max_updated)
    if t is None or (ahora - t).total_seconds() <= HORAS_CANAL_CONGELADO * 3600:
        return None
    return f"{canal}_sin_cambios_desde_{t.day}_{MESES[t.month - 1]}"


# ── insumos ───────────────────────────────────────────────────────────────────
class _Ctx:
    """Todo lo que leen las cuatro salidas, cargado UNA vez por corrida."""

    def __init__(self) -> None:
        t0 = time.monotonic()
        self.ahora = dt.datetime.now(dt.timezone.utc)
        self.hoy = almacen.hoy_cdmx()
        self.ini_ventana = (self.hoy - dt.timedelta(days=VENTANA_DIAS)).isoformat()
        self.fin_ventana = self.hoy.isoformat()          # exclusivo: hoy está a medias
        self.dias: dict[str, str | None] = {}
        self.avisos: list[str] = []
        self.par = _parametros()
        d = self.dias

        self.universo: list[dict] = [u for u in (_crudo("ml_universo.jsonl", d) or [])
                                     if u.get("id") and not u.get("error")]
        self.ml_precios = {p["id"]: p for p in (_crudo("ml_precios.jsonl", d) or []) if p.get("id")}
        self.promos = {p["id"]: p for p in (_crudo("ml_promos.jsonl", d) or []) if p.get("id")}
        self.ptw = {p["id"]: p for p in (_crudo("ml_price_to_win.jsonl", d) or [])
                    if p.get("id") and p.get("status") == 200}
        self.vcuenta = _crudo("ml_visitas_cuenta.jsonl", d) or []
        self.res_ml = _crudo("extraer_ml_resumen.json", d) or {}
        self.res_kub = _crudo("extraer_kubera_resumen.json", d) or {}
        self.kub = (_crudo("kubera_publicaciones.json", d) or {}).get("filas") or []
        self.comp = _crudo("kubera_competencia.json", d) or {}
        ventas = _crudo("kubera_ventas_dia.json", d) or {}
        self.ventas_ml = ventas.get("ml_dia") or []
        self.ventas_otros = ventas.get("otros_canales_30d") or []
        self.vcache = (_crudo("kubera_visitas_cache.json", d) or {}).get("filas") or []
        self.series: dict[str, list] = almacen.leer_json(almacen.ruta("cache", "visitas_serie.json"), {}) or {}
        self.comisiones = almacen.leer_json(almacen.ruta("cache", "comisiones.json"), {}) or {}
        self.reales = (almacen.leer_json(almacen.ultimo("comisiones_reales.json"), {}) or {}).get("categorias") or {}
        self.costos = costos_lab.leer()
        self.precios_doc = almacen.leer_json(almacen.ultimo("precios.json"), {}) or {}
        self.elasticidades = almacen.leer_json(almacen.ultimo("elasticidades.json"), {}) or {}
        ml = self.par.get("mercado_libre") or {}
        self.respaldo = {"real_orders": self.reales, "por_tramo": ml.get("comision_respaldo_por_tramo")}
        self.costo_full = float(ml.get("costo_full_unitario_mes") or 0.0)

        for nombre, dia in d.items():
            if dia is None:
                self.avisos.append(f"falta crudo: {nombre}")
            elif dia != self.hoy.isoformat():
                self.avisos.append(f"{nombre} es del {dia}, no de hoy")
        if not self.costos:
            self.avisos.append("falta ultimo/costos.json: las publicaciones salen sin costo")
        if not self.precios_doc:
            self.avisos.append("falta ultimo/precios.json: sin recomendaciones")

        # Índices.
        self.kub_ml: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self.odoo: dict[str, float] = {}
        self.odoo_at: dict[str, str] = {}
        for f in self.kub:
            if f.get("canal") == "mercado_libre" and f.get("listing_id"):
                self.kub_ml[(f.get("cuenta"), f["listing_id"])].append(f)
            s = _sku(f.get("sku"))
            if s and f.get("stock_odoo") is not None and s not in self.odoo:
                self.odoo[s] = _f(f["stock_odoo"])
                self.odoo_at[s] = f.get("stock_odoo_at")
        self.comp_res: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for r in self.comp.get("resumen") or []:
            if r.get("listing_id"):
                self.comp_res[(r.get("cuenta"), r["listing_id"])].append(r)
        self.vc: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for r in self.vcache:
            if r.get("listing_id"):
                self.vc[(r.get("cuenta"), r["listing_id"])].append(r)
        self.precios_rec = {f["id"]: f for f in self.precios_doc.get("filas") or [] if f.get("id")}
        self.pares = Counter((_sku(u.get("sku")), u.get("cuenta")) for u in self.universo if u.get("sku"))
        self.por_sku_ml: dict[str, dict] = {}
        for u in self.universo:
            s = _sku(u.get("sku"))
            if s and (s not in self.por_sku_ml or u.get("status") == "active"):
                self.por_sku_ml[s] = u

        # Ventas ML por item (30 d) y por cuenta×día.
        self.v30: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
        self.v150: Counter = Counter()
        self.dia_cuenta: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0.0])
        for f in self.ventas_ml:
            fecha = str(f.get("fecha") or "")
            if not fecha or fecha >= self.fin_ventana:
                continue
            u_, r_ = int(f.get("units_sold") or 0), float(f.get("revenue") or 0.0)
            acc = self.dia_cuenta[(fecha, f.get("cuenta"))]
            acc[0] += u_
            acc[1] += r_
            lid = f.get("item_id")
            if lid:
                self.v150[lid] += u_
                if fecha >= self.ini_ventana:
                    self.v30[lid][0] += u_
                    self.v30[lid][1] += r_

        self.duracion_carga_s = round(time.monotonic() - t0, 2)

        self.fres_canales = frescura_canales(self.kub)

    def frescura_canal(self, canal: str) -> Any:
        """Último cambio MASIVO del canal en kubera (ver `frescura_canales`)."""
        return (self.fres_canales.get(canal) or {}).get("ultimo_cambio_masivo")


# ── publicaciones ─────────────────────────────────────────────────────────────
def _tags_calidad(tags: Iterable[str] | None) -> list[str]:
    return sorted(t for t in (tags or []) if t in _TAGS_CALIDAD or _RE_TAG_CALIDAD.search(t))


def _costo(ce: dict | None) -> dict[str, Any]:
    if not ce:
        return {"unitario": None, "fuente": "sin_costo", "contenedor": None, "validado": False,
                "revisado_por": None, "costo_panel": None, "marca": "sin_fila_costos"}
    unit = _f(ce.get("unitario")) if ce.get("fuente") != "sin_costo" else None
    return {"unitario": _r(unit, 4), "fuente": ce.get("fuente") or "sin_costo",
            "contenedor": ce.get("contenedor"), "validado": bool(ce.get("validado")),
            "revisado_por": ce.get("revisado_por"), "costo_panel": _r(ce.get("costo_panel"), 4),
            "marca": ce.get("marca")}


def _sin_nulos(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _lista_serp(serp: dict | None) -> list[dict]:
    """Los primeros resultados de la búsqueda (sin llaves nulas: la lista se repite
    en cada publicación del SKU y con 8 renglones completos pesaba 5.4 de los 19.7
    MB de publicaciones.json)."""
    res = sorted((serp or {}).get("resultados") or [], key=lambda x: x.get("pos") or 999)
    return [_sin_nulos({"titulo": (x.get("titulo") or "")[:70] or None, "precio": _r(x.get("precio")),
                        "vendedor": x.get("seller"), "visitas_30d": x.get("visitas_30d"), "id": x.get("id")})
            for x in res[:MAX_LISTA_COMPETENCIA] if _f(x.get("precio"))]


def _lista_best(best: dict | None) -> list[dict]:
    res = sorted((best or {}).get("top") or [], key=lambda x: x.get("pos") or 999)
    return [_sin_nulos({"titulo": (x.get("titulo") or "")[:70] or None, "precio": _r(x.get("precio")),
                        "vendidos": x.get("vendidos"), "visitas_30d": x.get("visitas_30d"), "id": x.get("id")})
            for x in res[:MAX_LISTA_COMPETENCIA_BEST] if _f(x.get("precio"))]


def _competencia(ctx: _Ctx, cuenta: str, lid: str, sku: str | None) -> dict | None:
    """SERP del SKU > más vendidos de la hoja > sugerido de ML (DISENO §5, mismo
    orden que `optimizador._referencia`). Los números de la SERP son la muestra
    CRUDA (n, promedio, mediana, min, max sobre los mismos resultados); la
    mediana filtrada [med/3, med×3] que usa el optimizador va aparte."""
    filas = ctx.comp_res.get((cuenta, lid)) or []
    r = next((x for x in filas if _sku(x.get("sku")) == sku), filas[0] if filas else None)
    promo = ((ctx.promos.get(lid) or {}).get("resumen") or {})
    sug = _r(promo.get("price_discount_sugerido"))
    ptw = ctx.ptw.get(lid)
    out: dict[str, Any] | None = None
    if r and (r.get("n_comp") or 0) > 0:
        t = _ts(r.get("serp_capturado_en"))
        out = {"n": r.get("n_comp"), "promedio": _r(r.get("prom")), "mediana": _r(r.get("mediana")),
               "minimo": _r(r.get("minimo")), "maximo": _r(r.get("maximo")), "fuente": "serp",
               "capturado_en": r.get("serp_capturado_en"), "sugerido_ml": sug,
               "mediana_filtrada": _r(r.get("mediana_filtrada")), "n_filtrado": r.get("n_filtrado"),
               "prom_pond_visitas": _r(r.get("prom_pond_visitas")), "termino": r.get("termino"),
               "edad_dias": (ctx.ahora - t).days if t else None,
               "lista": _lista_serp((ctx.comp.get("serp_por_sku") or {}).get(sku or ""))}
        if (r.get("n_best") or 0) > 0:
            out["bestsellers"] = {"n": r.get("n_best"), "mediana": _r(r.get("mediana_best")),
                                  "minimo": _r(r.get("min_best")), "maximo": _r(r.get("max_best")),
                                  "categoria_id": r.get("best_categoria_id"),
                                  "capturado_en": r.get("best_capturado_en")}
    elif r and (r.get("n_best") or 0) > 0:
        t = _ts(r.get("best_capturado_en"))
        out = {"n": r.get("n_best"), "promedio": None, "mediana": _r(r.get("mediana_best")),
               "minimo": _r(r.get("min_best")), "maximo": _r(r.get("max_best")), "fuente": "bestsellers",
               "capturado_en": r.get("best_capturado_en"), "sugerido_ml": sug,
               "categoria_id": r.get("best_categoria_id"), "edad_dias": (ctx.ahora - t).days if t else None,
               "nota": "mediana de los más vendidos de TODA la hoja de categoría, no del mismo producto",
               "lista": _lista_best((ctx.comp.get("best_por_categoria") or {}).get(r.get("best_categoria_id") or ""))}
    elif sug:
        out = {"n": None, "promedio": None, "mediana": sug, "minimo": _r(promo.get("price_discount_min")),
               "maximo": _r(promo.get("price_discount_max")), "fuente": "sugerido_ml",
               "capturado_en": (ctx.res_ml.get("frescura") or {}).get("ml_listings"), "sugerido_ml": sug}
    if ptw:
        p = ptw.get("price_to_win") or {}
        extra = {"precio": _r(p.get("price_to_win")), "status": p.get("status"),
                 "precio_ganador": _r((p.get("winner") or {}).get("price")),
                 "catalog_product_id": ptw.get("catalog_product_id")}
        if out is None:
            out = {"n": None, "promedio": None, "mediana": None, "minimo": None, "maximo": None,
                   "fuente": "price_to_win", "capturado_en": (ctx.res_ml.get("frescura") or {}).get("ml_listings"),
                   "sugerido_ml": sug}
        out["price_to_win"] = extra
    return out


def _visitas_30d(ctx: _Ctx, cuenta: str, lid: str) -> tuple[int | None, str | None, list[str]]:
    avisos: list[str] = []
    serie = ctx.series.get(lid)
    if serie:
        tot = sum(int(v or 0) for f, v in serie if ctx.ini_ventana <= f < ctx.fin_ventana)
        ultimo = serie[-1][0] if serie else None
        if ultimo and ultimo < (ctx.hoy - dt.timedelta(days=2)).isoformat():
            avisos.append(f"visitas_serie_hasta_{ultimo}")
        return tot, "api_serie", avisos
    filas = ctx.vc.get((cuenta, lid)) or []
    vals = [int(x["visits_30d"]) for x in filas if x.get("visits_30d") is not None]
    if vals:
        return max(vals), "cache_kubera", avisos
    return None, None, avisos


def _fila_ml(u: dict, ctx: _Ctx) -> dict[str, Any]:
    lid, cuenta = u["id"], u["cuenta"]
    rid = f"mercado_libre:{cuenta}:{lid}"
    kubs = ctx.kub_ml.get((cuenta, lid)) or []
    sku = _sku(u.get("sku"))
    kub = next((k for k in kubs if _sku(k.get("sku")) == sku), kubs[0] if kubs else {})
    sku = sku or _sku(kub.get("sku"))
    es_full = bool(u.get("es_full"))
    estado = _ESTADO_ML.get(str(u.get("status") or "").lower(), "otra")
    avisos: list[str] = []
    if not kubs:
        avisos.append("sin_fila_en_kubera")
    elif len(kubs) > 1:
        avisos.append("listing_con_dos_skus_en_kubera")
    if not sku:
        avisos.append("sin_sku")
    elif ctx.pares.get((sku, cuenta), 0) > 1:
        avisos.append("sku_con_varias_publicaciones_en_la_cuenta")

    # Precio cobrado.
    pr = ctx.ml_precios.get(lid) or {}
    p0, fuente_p = _f(pr.get("precio_cobrado")), "ml_sale_price"
    if p0 is None:
        t = _ts(kub.get("price_sale_at"))
        if _f(kub.get("price_sale")) and t and (ctx.ahora - t).total_seconds() <= HORAS_PRICE_SALE_VIGENTE * 3600:
            p0, fuente_p = _f(kub["price_sale"]), "kubera_price_sale"
    if p0 is None and _f(u.get("price")):
        p0, fuente_p = _f(u["price"]), "item_price"
        if estado == "activa":
            avisos.append("precio_cobrado_sin_medir")
    if p0 is None:
        fuente_p = None
    lista = _f(u.get("original_price")) or _f(pr.get("precio_regular")) or _f(u.get("price"))
    promo = pr.get("promo") or None
    if promo:
        promo = {"tipo": promo.get("tipo"), "fin": promo.get("fin"), "inicio": promo.get("inicio"),
                 "campaign_id": promo.get("campaign_id"), "monto": _r(promo.get("monto")),
                 "regular": _r(promo.get("regular"))}

    # Stock.
    disp = _f(u.get("available_quantity"))
    if es_full:
        stock_full, stock_propio = disp, _f(kub.get("stock_own"))
    else:
        stock_full, stock_propio = _f(kub.get("stock_full")), disp
    stock_odoo = ctx.odoo.get(sku) if sku else None

    # Costo, peso y economía a precio cobrado.
    ce = ctx.costos.get(sku or "") if sku else None
    costo = _costo(ce)
    peso, fuente_peso = costos_lab.peso_de(ctx.costos, sku, lid) if sku else (None, None)
    eco: dict[str, Any] = {}
    if p0:
        eco = economia.utilidad(p0, costo["unitario"], u.get("category_id"), peso,
                                ctx.costo_full if es_full else 0.0,
                                comisiones_cache=ctx.comisiones, respaldo=ctx.respaldo, es_full=es_full)
        avisos.extend(eco.get("faltan") or [])
        if not es_full:
            # cache/comisiones.json se pidió con logistic_type=fulfillment: fuera de
            # FULL, ≥$500 ML cobra ~3.5 puntos MÁS y el envío no es la tabla FULL.
            avisos.append("comision_y_envio_con_tabla_full")
    else:
        avisos.append("sin_precio")
    if costo["unitario"] is not None and costo["unitario"] < 1.0:
        avisos.append("costo_menor_a_1_peso")

    visitas, vfuente, av_v = _visitas_30d(ctx, cuenta, lid)
    avisos.extend(av_v)
    uu, rr = ctx.v30.get(lid, (0, 0.0))
    rec = ctx.precios_rec.get(rid) or {}
    return {
        "id": rid, "canal": "mercado_libre", "cuenta": cuenta, "listing_id": lid, "sku": sku,
        "titulo": u.get("titulo") or kub.get("producto_nombre"),
        "url": u.get("permalink") or kub.get("url"),
        "thumbnail": str(u.get("thumbnail") or "").replace("http://", "https://", 1) or None,
        "categoria_id": u.get("category_id") or kub.get("ml_cat_id"),
        "estado": estado, "estado_detalle": None, "situacion": u.get("status"),
        "sub_status": list(u.get("sub_status") or []),
        "logistica": u.get("logistic_type"), "es_full": es_full,
        "precio_cobrado": _r(p0), "fuente_precio": fuente_p, "precio_lista": _r(lista), "promo": promo,
        "stock_full": stock_full, "stock_propio": stock_propio, "stock_odoo": stock_odoo,
        "costo": costo, "peso": {"kg": _r(peso, 4), "fuente": fuente_peso},
        "comision_pct": eco.get("pct"), "fuente_comision": eco.get("fuente_pct"),
        "comision": eco.get("comision"), "envio": eco.get("envio"), "iva": eco.get("iva"),
        "utilidad": eco.get("utilidad"), "margen_pct": eco.get("margen"),
        "visitas_30d": visitas, "visitas_fuente": vfuente,
        "unidades_30d": int(uu), "ingreso_30d": round(float(rr), 2),
        "conversion_30d": round(uu / visitas, 4) if visitas else None,
        "unidades_150d": int(ctx.v150.get(lid, 0)), "vendidas_total": u.get("sold_quantity"),
        "competencia": _competencia(ctx, cuenta, lid, sku),
        "tags_calidad": _tags_calidad(u.get("tags")),
        "catalogo": bool(u.get("catalog_listing")), "fecha_creacion": u.get("date_created"),
        "precio_recomendado": rec.get("precio_recomendado"), "cambio_pct": rec.get("cambio_pct"),
        "frescura_at": u.get("leido_at"), "avisos": _dedup(avisos), "supuesto_canal": False,
    }


def _estado_otro(canal: str, situacion: Any, status: Any) -> tuple[str, str | None]:
    col, pliegue = _COLUMNA_ESTADO.get(canal, ("status", None))
    crudo = situacion if col == "situacion" else status
    v = str(crudo or "")
    v = v.upper() if pliegue == "upper" else v
    if not v:
        return "otra", "sin_estado"
    return _ESTADO_OTROS.get(canal, {}).get(v, ("otra", "desconocido"))


def _filas_otros(ctx: _Ctx) -> list[dict[str, Any]]:
    kub = [f for f in ctx.kub if str(f.get("canal") or "").lower() in canales.CANALES]
    # Ventas 30 d: se agrupan por (canal, SKU). El item_id de esas líneas es el de
    # la LÍNEA del pedido (TikTok) o '0' (Amazon), no el de la publicación, y la
    # etiqueta de cuenta difiere ("TIKTOK" en ventas, "KUBERA" en listings).
    ventas: dict[tuple[str, str], dict[str, Any]] = {}
    for r in ctx.ventas_otros:
        k = (str(r.get("canal") or "").lower(), _sku(r.get("sku")) or "")
        a = ventas.setdefault(k, {"u": 0, "r": 0.0, "con_precio": 0, "sin_precio": 0})
        a["u"] += int(r.get("unidades") or 0)
        if _f(r.get("ingreso")) is not None:
            a["r"] += float(r["ingreso"])
            a["con_precio"] += 1
        if int(r.get("lineas_sin_precio") or 0) > 0:
            a["sin_precio"] += 1
    # Si un SKU tiene varias publicaciones en el canal, la venta se asigna a UNA
    # (la activa primero) y las demás quedan en null con aviso: sumarla a todas
    # la contaría dos veces.
    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for f in kub:
        grupos[(str(f["canal"]).lower(), _sku(f.get("sku")) or "")].append(f)
    dueno: dict[int, bool] = {}
    for k, fs in grupos.items():
        orden = sorted(fs, key=lambda f: 0 if _estado_otro(k[0], f.get("situacion"), f.get("status"))[0] == "activa" else 1)
        for i, f in enumerate(orden):
            dueno[id(f)] = i == 0

    entrada = []
    for f in kub:
        e = canales.filas_desde_kubera([f])[0]
        e["_kub"] = f
        entrada.append(e)
    mref = canales.margen_ref_desde_precios(ctx.precios_doc.get("filas") or [])
    pml = canales.precio_ml_por_sku(ctx.universo, ctx.ml_precios.values())
    evaluadas = canales.evaluar(entrada, ctx.costos, ctx.par, margen_ref_por_sku=mref, precio_ml_por_sku=pml)

    congelado = {c: _aviso_congelado(c, ctx.frescura_canal(c), ctx.ahora) for c in canales.CANALES}
    salida = []
    for e in evaluadas:
        f = e.pop("_kub")
        canal = str(f["canal"]).lower()
        cuenta = f.get("cuenta")
        sku = _sku(f.get("sku"))
        lid = f.get("listing_id") or None
        rid = f"{canal}:{cuenta}:{lid}" if lid else f"{canal}:{cuenta}:sku:{sku}"
        estado, detalle = _estado_otro(canal, f.get("situacion"), f.get("status"))
        avisos = list(e.get("avisos") or [])
        propia = _ts(f.get("updated_at"))
        if congelado.get(canal) and not (propia and (ctx.ahora - propia).total_seconds() <= HORAS_CANAL_CONGELADO * 3600):
            # El aviso fijo de `canales._AVISO_CANAL` ("…_sin_cambios_desde_18_sep",
            # "walmart_foto_del_17_ago") dice lo mismo que el calculado: queda uno
            # solo, y lo que el fijo añade (el precio es el regular de Woo) aparte.
            fijo = canales._AVISO_CANAL.get(canal)
            if fijo in avisos and canal in ("amazon", "walmart"):
                avisos.remove(fijo)
                avisos.append("precio_regular_de_woo")
            avisos.insert(0, congelado[canal])
        avisos.extend(x for x in (e.get("faltan") or []) if x != "precio_sospechoso")
        if not lid:
            avisos.append("sin_listing_id")
        v = ventas.get((canal, sku or ""))
        if v is None:
            uu, rr = 0, 0.0
        elif not dueno.get(id(f)):
            uu = rr = None
            avisos.append("ventas_del_sku_asignadas_a_otra_publicacion")
        else:
            uu = v["u"]
            rr = round(v["r"], 2) if v["con_precio"] else None
            if v["sin_precio"]:
                avisos.append("ingreso_30d_parcial_lineas_sin_precio")
        ml = ctx.por_sku_ml.get(sku or "") or {}
        costo = e.get("costo") or {"unitario": None, "fuente": "sin_costo"}
        if "marca" not in costo:
            costo = {**costo, "marca": (ctx.costos.get(sku or "") or {}).get("marca")}
        salida.append({
            "id": rid, "canal": canal, "cuenta": cuenta, "listing_id": lid, "sku": sku,
            "titulo": f.get("producto_nombre") or ml.get("titulo"),
            "url": f.get("url"),
            "thumbnail": str(ml.get("thumbnail") or "").replace("http://", "https://", 1) or None,
            "categoria_id": f.get("category_id") or f.get("product_type"),
            "estado": estado, "estado_detalle": detalle,
            "situacion": f.get("situacion") if canal == "amazon" else f.get("status"),
            "sub_status": None, "logistica": f.get("logistic_type"),
            # es_full es FULL de ML. El FBA de Amazon (almacén del marketplace) va
            # en stock_full para que la columna signifique lo mismo: piezas que
            # ya están en la bodega del canal.
            "es_full": False,
            "precio_cobrado": _r(e.get("precio")), "fuente_precio": "kubera_listing",
            "precio_lista": _r(f.get("price_base")) or _r(f.get("price")), "promo": None,
            "stock_full": _f(f.get("stock_fba")) if canal == "amazon" else None,
            "stock_propio": _f(f.get("stock_own")), "stock_odoo": ctx.odoo.get(sku or ""),
            "costo": costo, "peso": None,
            "comision_pct": e.get("comision_pct"), "fuente_comision": "supuesto_canal",
            "comision": e.get("comision"), "envio": e.get("envio"), "iva": e.get("iva"),
            "utilidad": e.get("utilidad"), "margen_pct": e.get("margen_pct"),
            "precio_paridad": e.get("precio_paridad"), "paridad": e.get("paridad"),
            "visitas_30d": None, "visitas_fuente": None,
            "unidades_30d": uu, "ingreso_30d": rr, "conversion_30d": None,
            "unidades_150d": None, "vendidas_total": None,
            "competencia": None, "tags_calidad": [], "catalogo": None, "fecha_creacion": None,
            "precio_recomendado": None, "cambio_pct": None,
            "frescura_at": f.get("updated_at"), "avisos": _dedup(avisos), "supuesto_canal": True,
        })
    return salida


def publicaciones(ctx: _Ctx | None = None, escribir: bool = True) -> dict[str, Any]:
    """`ultimo/publicaciones.json`: `{generado_at, resumen, filas}`."""
    ctx = ctx or _Ctx()
    t0 = time.monotonic()
    filas_ml = [_fila_ml(u, ctx) for u in ctx.universo if u.get("cuenta") in CUENTAS_ML]
    filas_ml.sort(key=lambda f: (-(f["unidades_30d"] or 0), -(f["visitas_30d"] or 0)))
    filas_otros = _filas_otros(ctx)
    filas_otros.sort(key=lambda f: (CANALES.index(f["canal"]), -(f["unidades_30d"] or 0)))
    filas = filas_ml + filas_otros
    ids = Counter(f["id"] for f in filas)
    duplicados = [k for k, v in ids.items() if v > 1]
    if duplicados:
        ctx.avisos.append(f"{len(duplicados)} ids repetidos en publicaciones (ej. {duplicados[:3]})")
    conteo: dict[str, Counter] = defaultdict(Counter)
    for f in filas:
        conteo[f"{f['canal']}:{f['cuenta']}"][f["estado"]] += 1
    resumen = {
        "filas": len(filas), "por_canal_cuenta_estado": {k: dict(v) for k, v in sorted(conteo.items())},
        "ml_sin_fila_kubera": sum(1 for f in filas_ml if "sin_fila_en_kubera" in f["avisos"]),
        "ml_con_costo": sum(1 for f in filas_ml if f["costo"]["unitario"] is not None),
        "ml_con_margen": sum(1 for f in filas_ml if f["margen_pct"] is not None),
        "ml_con_competencia": sum(1 for f in filas_ml if f["competencia"]),
        "visitas_fuente": dict(Counter(f["visitas_fuente"] for f in filas_ml)),
        "fuente_precio": dict(Counter(f["fuente_precio"] for f in filas)),
        "ventana_30d": [ctx.ini_ventana, (ctx.hoy - dt.timedelta(days=1)).isoformat()],
        "crudos": ctx.dias, "duracion_s": None,
    }
    resumen["duracion_s"] = round(time.monotonic() - t0, 2)
    doc = {"generado_at": almacen.ahora_iso(), "version": VERSION, "resumen": resumen, "filas": filas}
    if escribir:
        almacen.escribir_json(almacen.ultimo("publicaciones.json"), doc)
    return doc


# ── historial ─────────────────────────────────────────────────────────────────
def _snapshots_compactos(ids: set[str], hoy: str, max_dias: int = DIAS_HISTORIAL) -> dict[str, dict[str, list]]:
    """{día: {id: [cobrado, recomendado]}} de `snapshots/<día>/` (sin hoy: hoy sale
    de la corrida en memoria). Cada día se lee UNA vez y se guarda compacto en
    `cache/historial_snapshots/<día>.json` con la firma (mtime, tamaño) de sus dos
    archivos: un snapshot pesa ~15 MB y re-parsear 150 cada noche no tiene caso."""
    base = _entorno.datos_dir() / "snapshots"
    out: dict[str, dict[str, list]] = {}
    for dia in almacen.dias_con_snapshot()[-max_dias:]:
        if dia >= hoy:
            continue
        firma = []
        for nombre in ("publicaciones.json", "precios.json"):
            try:
                st = (base / dia / nombre).stat()
                firma.append([st.st_mtime_ns, st.st_size])
            except FileNotFoundError:
                firma.append(None)
        ruta_c = almacen.ruta("cache", "historial_snapshots", f"{dia}.json")
        cache = almacen.leer_json(ruta_c, None) or {}
        if cache.get("firma") != firma:
            puntos: dict[str, list] = {}
            for i, (nombre, campo) in enumerate((("publicaciones.json", "precio_cobrado"),
                                                  ("precios.json", "precio_recomendado"))):
                try:
                    datos = almacen.leer_json(base / dia / nombre, {}) or {}
                except ValueError:
                    log.warning("snapshot %s/%s ilegible", dia, nombre)
                    continue
                for f in (datos.get("filas") if isinstance(datos, dict) else datos) or []:
                    if isinstance(f, dict) and str(f.get("id") or "").startswith("mercado_libre:"):
                        v = _f(f.get(campo))
                        if v is not None:
                            puntos.setdefault(f["id"], [None, None])[i] = v
            cache = {"firma": firma, "puntos": puntos}
            almacen.escribir_json(ruta_c, cache)
        out[dia] = {k: v for k, v in (cache.get("puntos") or {}).items() if k in ids}
    return out


def historial(ctx: _Ctx | None = None, pubs: list[dict] | None = None, escribir: bool = True) -> dict[str, Any]:
    """`ultimo/historial.json`: `{<id>: {"serie": [...]}}` de las publicaciones de ML.

    Por día (150, hasta AYER: hoy está a medias):
    - ``precio_realizado`` = ingreso/unidades del día (null sin venta);
    - ``unidades`` de `channel.sales_daily` (0 medido: la venta es completa);
    - ``visitas`` de la serie de la API (null antes de existir la publicación o
      fuera de las FULL, que son las únicas con serie);
    - ``precio_ofrecido`` = `item.price` del historial limpio (`stock_hist` hasta el
      15-jul, `listing_history` después, sin los 38 pares con rebote): es el
      precio de LISTA del vendedor, NO el cobrado (las promociones de ML son
      invisibles ahí; mediana cobrado/lista = 0.85 en activas). Se arrastra entre
      cambios solo si el par sku×cuenta apunta a UNA publicación;
      Desde el inicio de la promoción VIGENTE (`/prices`) el ofrecido es el monto
      de la promoción y el punto lleva ``ofrecido_de_promo: true`` (extensión);
    - ``sin_oferta: true`` (extensión) si el día se censuró en el panel de
      elasticidad (pausada, sin stock FULL o racha de 0 visitas).
    Más un punto por snapshot (``snapshot: true``, con ``precio_cobrado`` y
    ``precio_recomendado`` de ese día) y el de HOY desde esta misma corrida.
    Arranca en el primer día con serie o con venta: antes no existía la publicación.
    """
    ctx = ctx or _Ctx()
    from sandbox_precios import elasticidad  # perezoso: su panel pesa y solo aquí se usa

    t0 = time.monotonic()
    panel = elasticidad.construir_panel()
    items = panel["items"]
    fechas = [d.isoformat() for d in panel["fechas"]]
    desde, n = panel["desde"], len(panel["fechas"])
    pos = {f: k for k, f in enumerate(fechas)}
    uni = {u["id"]: u for u in ctx.universo}

    # Publicaciones de ML sin serie de visitas (no FULL) pero con ventas en la ventana.
    extra: dict[str, dict[str, Any]] = {}
    for f in ctx.ventas_ml:
        lid = f.get("item_id")
        k = pos.get(str(f.get("fecha") or ""))
        if not lid or k is None or lid in items or lid not in uni:
            continue
        it = extra.setdefault(lid, {"u": [0] * n, "r": [0.0] * n})
        it["u"][k] += int(f.get("units_sold") or 0)
        it["r"][k] += float(f.get("revenue") or 0.0)

    hist = (almacen.leer_json(almacen.ultimo_crudo("kubera_historial_precio.json") or Path("-"), {}) or {})
    por_par: dict[tuple[str, str], list[str]] = defaultdict(list)
    for u in ctx.universo:
        if u.get("sku"):
            por_par[(_sku(u["sku"]), u["cuenta"])].append(u["id"])
    todos = {**{k: None for k in items}, **{k: None for k in extra}}
    sh_por_item: dict[str, list[dict]] = defaultdict(list)
    for f in hist.get("stock_hist") or []:
        if f.get("item_id") in todos:
            sh_por_item[f["item_id"]].append(f)
    lista = elasticidad._precio_lista(todos, sh_por_item, hist.get("precio") or [], por_par, desde, n)
    rebote = {(_sku(p.get("sku")), p.get("cuenta")) for p in hist.get("pares_rebote") or []}

    def _ofrecido(lid: str, cuenta: str, sku: str | None) -> tuple[list[float | None], int | None]:
        """(precio ofrecido por día, primer día cubierto por la promoción vigente)."""
        p = list(lista.get(lid) or [None] * n)
        unico = len(por_par.get((sku, cuenta)) or []) == 1 and (sku, cuenta) not in rebote
        if unico:  # sin cambio registrado = el mismo precio (listing_history guarda TODOS los cambios)
            ult = None
            for k in range(n):
                if p[k] is not None:
                    ult = p[k]
                elif ult is not None:
                    p[k] = ult
        # La promoción VIGENTE (de `/prices`) sí dice qué se ofrecía desde su inicio:
        # MLM4746955600 tiene item.price 129 y se cobra 99 desde el 1-sep. Solo se
        # conoce la vigente; las anteriores siguen invisibles.
        promo = (ctx.ml_precios.get(lid) or {}).get("promo") or {}
        monto, ini = _f(promo.get("monto")), _ts(promo.get("inicio"))
        k_promo = None
        if monto and ini:
            k_promo = max(0, (ini.astimezone(almacen.CDMX).date() - desde).days)
            for k in range(k_promo, n):
                p[k] = monto
        return p, (k_promo if k_promo is not None and k_promo < n else None)

    salida: dict[str, dict[str, list]] = {}
    for lid, it in items.items():
        u_, r_, v_, vivo, cens = it["u"], it["r"], it["v"], it["vivo"], it.get("cens") or [False] * n
        of, k_promo = _ofrecido(lid, it["cuenta"], it["sku"])
        k0 = next((k for k in range(n) if vivo[k] or u_[k] > 0), None)
        if k0 is None:
            continue
        serie = []
        for k in range(k0, n):
            p = {"fecha": fechas[k],
                 "precio_realizado": round(r_[k] / u_[k], 2) if u_[k] > 0 else None,
                 "unidades": u_[k], "visitas": v_[k] if vivo[k] else None,
                 "precio_ofrecido": _r(of[k]), "precio_recomendado": None}
            if cens[k]:
                p["sin_oferta"] = True
            if k_promo is not None and k >= k_promo:
                p["ofrecido_de_promo"] = True
            serie.append(p)
        salida[it["id"]] = {"serie": serie}
    for lid, it in extra.items():
        u = uni[lid]
        of, k_promo = _ofrecido(lid, u["cuenta"], _sku(u.get("sku")))
        creado = str(u.get("date_created") or "")[:10]
        k0 = next((k for k in range(n) if (creado and fechas[k] >= creado) or it["u"][k] > 0), 0)
        serie = []
        for k in range(k0, n):
            p = {"fecha": fechas[k], "precio_realizado": round(it["r"][k] / it["u"][k], 2) if it["u"][k] > 0 else None,
                 "unidades": it["u"][k], "visitas": None, "precio_ofrecido": _r(of[k]), "precio_recomendado": None}
            if k_promo is not None and k >= k_promo:
                p["ofrecido_de_promo"] = True
            serie.append(p)
        salida[f"mercado_libre:{u['cuenta']}:{lid}"] = {"serie": serie}

    # Snapshots previos + el punto de hoy (lo que esta corrida cobra y recomienda).
    ids = set(salida)
    snaps = _snapshots_compactos(ids, ctx.hoy.isoformat())
    hoy_pts = {}
    for f in pubs or []:
        if f["id"] in ids:
            hoy_pts[f["id"]] = [f.get("precio_cobrado"), (ctx.precios_rec.get(f["id"]) or {}).get("precio_recomendado")]
    snaps[ctx.hoy.isoformat()] = hoy_pts
    n_puntos = 0
    for dia in sorted(snaps):
        for id_, (cob, rec) in snaps[dia].items():
            serie = salida[id_]["serie"]
            q = next((p for p in reversed(serie) if p["fecha"] == dia), None) if serie and serie[-1]["fecha"] >= dia else None
            if q is None:
                q = {"fecha": dia, "precio_realizado": None, "unidades": None, "visitas": None,
                     "precio_ofrecido": None, "precio_recomendado": None}
                serie.append(q)
            if q.get("precio_ofrecido") is None:
                q["precio_ofrecido"] = cob
            q["precio_cobrado"] = cob
            if rec is not None:
                q["precio_recomendado"] = rec
            q["snapshot"] = True
            n_puntos += 1
    for v in salida.values():
        v["serie"].sort(key=lambda p: p["fecha"])

    resumen = {"ids": len(salida), "full_con_serie": sum(1 for it in items.values() if it["id"] in salida),
               "sin_serie_con_ventas": len(extra), "dias": n, "desde": fechas[0] if fechas else None,
               "hasta": fechas[-1] if fechas else None,
               "con_precio_ofrecido_reconstruido": sum(
                   1 for v in salida.values()
                   if any(p["precio_ofrecido"] is not None and not p.get("snapshot") for p in v["serie"])),
               "dias_con_precio_ofrecido": sum(1 for v in salida.values() for p in v["serie"]
                                               if p["precio_ofrecido"] is not None and not p.get("snapshot")),
               "dias_totales": sum(len(v["serie"]) for v in salida.values()),
               "snapshots_usados": sorted(snaps), "puntos_snapshot": n_puntos,
               "duracion_s": round(time.monotonic() - t0, 2)}
    if escribir:
        almacen.escribir_json(almacen.ultimo("historial.json"), salida)
    return {"resumen": resumen}


# ── métricas ──────────────────────────────────────────────────────────────────
def _serie_cuenta(ctx: _Ctx) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in ctx.vcuenta:
        if r.get("status") == 200 and r.get("cuenta"):
            out[r["cuenta"]] = {f: int(v or 0) for f, v in r.get("serie") or []}
    return out


def _palancas(ctx: _Ctx, filas_ml: list[dict]) -> list[dict[str, Any]]:
    el = ctx.elasticidades
    cuentas_sku: dict[str, set[str]] = defaultdict(set)
    for f in filas_ml:
        if f["es_full"] and f["sku"]:
            cuentas_sku[f["sku"]].add(f["cuenta"])
    out: list[dict[str, Any]] = []
    for f in filas_ml:
        if not (f["es_full"] and f["estado"] == "pausada"):
            continue
        sin_stock = "out_of_stock" in (f["sub_status"] or []) or not f["stock_full"]
        if not sin_stock or not (f["stock_odoo"] or 0) > 0:
            continue
        base = (el.get(f["id"]) or {}).get("base") or {}
        u0 = _f(base.get("u0")) if base.get("unidades") else None
        if u0 is not None:
            vel, fuente = u0, "base_28d_con_oferta"
        elif f["unidades_150d"]:
            vel, fuente = f["unidades_150d"] / DIAS_HISTORIAL, "150d_sin_descontar_censura"
        else:
            vel, fuente = 0.0, "sin_ventas_150d"
        p_base = _f(base.get("p_base")) or f["precio_cobrado"]
        rec = ctx.precios_rec.get(f["id"]) or {}
        # La velocidad es la del precio al que VENDÍA (p_base), así que la utilidad
        # perdida se mide a ese precio y no al de lista de hoy: TEC-0552-NEG vendía
        # 14.7/día a $464 y hoy está en $1,300 — reactivarla ahí no vende eso.
        util_unit = None
        if p_base:
            util_unit = economia.utilidad(p_base, f["costo"]["unitario"], f["categoria_id"],
                                          (f.get("peso") or {}).get("kg"), ctx.costo_full,
                                          comisiones_cache=ctx.comisiones, respaldo=ctx.respaldo)["utilidad"]
        stock = f["stock_odoo"]
        partes = [f"pausada sin stock FULL; Odoo tiene {int(stock)} pzs libres"]
        if vel > 0:
            partes.append(f"vendía {vel:.2f}/día" + (f" a ${p_base:,.0f}" if p_base else "")
                          + (f" ({base.get('desde')} → {base.get('hasta')})" if base.get("desde") else ""))
            if p_base and f["precio_cobrado"] and f["precio_cobrado"] > 1.25 * p_base:
                partes.append(f"hoy está en ${f['precio_cobrado']:,.0f}: a ese precio no vendería igual")
        else:
            partes.append("sin ventas registradas en 150 d")
        if len(cuentas_sku.get(f["sku"] or "", ())) > 1:
            partes.append("el mismo SKU está en FULL en las dos cuentas: el stock de Odoo NO se suma")
        out.append({
            "tipo": "reactivar_full", "id": f["id"], "sku": f["sku"], "cuenta": f["cuenta"],
            "listing_id": f["listing_id"], "titulo": f["titulo"],
            "ventas_perdidas_dia": round(vel, 3), "fuente_velocidad": fuente,
            "ingreso_perdido_dia": round(vel * p_base, 2) if (p_base and vel) else (0.0 if vel == 0 else None),
            "utilidad_perdida_dia": round(vel * util_unit, 2) if (util_unit is not None and vel) else (0.0 if vel == 0 else None),
            "stock_odoo": stock, "dias_de_stock_odoo": round(stock / vel, 1) if vel > 0 else None,
            # lo que de verdad se recupera en un mes: no más de lo que hay en Odoo
            "piezas_recuperables_30d": round(min(stock, vel * VENTANA_DIAS), 1),
            "precio_base": _r(p_base), "utilidad_unit_base": util_unit,
            "precio_cobrado": f["precio_cobrado"], "precio_recomendado": rec.get("precio_recomendado"),
            "cuentas_con_el_sku": sorted(cuentas_sku.get(f["sku"] or "", ())),
            "motivo": "; ".join(partes),
        })
    for f in filas_ml:
        if f["estado"] != "activa" or f["utilidad"] is None or f["utilidad"] >= 0:
            continue
        rec = ctx.precios_rec.get(f["id"]) or {}
        u_dia = (f["unidades_30d"] or 0) / VENTANA_DIAS
        piso = rec.get("precio_piso")
        partes = [f"pierde ${-f['utilidad']:.2f} por pieza a ${f['precio_cobrado']:.2f}"]
        if rec.get("precio_equilibrio"):
            partes.append(f"equilibrio ${rec['precio_equilibrio']:.2f}")
        if piso:
            partes.append(f"piso 12% ${piso:.2f}")
        if f["costo"].get("fuente") in ("tarifa_7500", "sin_costo") or "costo_menor_a_1_peso" in f["avisos"]:
            partes.append(f"costo {f['costo'].get('fuente')}: revisar antes de mover el precio")
        out.append({
            "tipo": "perdiendo_dinero", "id": f["id"], "sku": f["sku"], "cuenta": f["cuenta"],
            "listing_id": f["listing_id"], "titulo": f["titulo"],
            "ventas_perdidas_dia": None, "unidades_dia": round(u_dia, 3),
            "utilidad_unit": f["utilidad"], "perdida_dia": round(-f["utilidad"] * u_dia, 2),
            "stock_odoo": f["stock_odoo"], "precio_cobrado": f["precio_cobrado"],
            "precio_equilibrio": rec.get("precio_equilibrio"), "precio_piso": piso,
            "precio_recomendado": rec.get("precio_recomendado"), "costo_fuente": f["costo"].get("fuente"),
            "motivo": "; ".join(partes),
        })
    out.sort(key=lambda p: (0 if p["tipo"] == "reactivar_full" else 1,
                            -(p.get("ventas_perdidas_dia") or 0), -(p.get("perdida_dia") or 0)))
    return out


def metricas(ctx: _Ctx | None = None, pubs: list[dict] | None = None, escribir: bool = True) -> dict[str, Any]:
    """`ultimo/metricas.json` (contrato DISENO §6 + extensiones)."""
    ctx = ctx or _Ctx()
    if pubs is None:
        pubs = (almacen.leer_json(almacen.ultimo("publicaciones.json"), {}) or {}).get("filas") or []
    t0 = time.monotonic()
    filas_ml = [f for f in pubs if f["canal"] == "mercado_libre"]
    vis_cuenta = _serie_cuenta(ctx)
    ini, fin = ctx.ini_ventana, ctx.fin_ventana

    por_cuenta: dict[str, dict[str, Any]] = {}
    for c in CUENTAS_ML:
        fs = [f for f in filas_ml if f["cuenta"] == c]
        act = [f for f in fs if f["estado"] == "activa"]
        pfull = [f for f in fs if f["es_full"] and f["estado"] == "pausada"]
        sin_stock = [f for f in pfull if "out_of_stock" in (f["sub_status"] or []) or not f["stock_full"]]
        vserie = vis_cuenta.get(c)
        v30 = sum(v for d, v in vserie.items() if ini <= d < fin) if vserie else None
        u30 = sum(x[0] for (d, cc), x in ctx.dia_cuenta.items() if cc == c and ini <= d < fin)
        r30 = sum(x[1] for (d, cc), x in ctx.dia_cuenta.items() if cc == c and ini <= d < fin)
        por_cuenta[c] = {
            "publicaciones": len(fs), "activas": len(act),
            "activas_full": sum(1 for f in act if f["es_full"]), "pausadas_full": len(pfull),
            "pausadas_full_sin_stock": len(sin_stock),
            "pausadas_full_con_stock_odoo": sum(1 for f in sin_stock if (f["stock_odoo"] or 0) > 0),
            "visitas_dia": round(v30 / VENTANA_DIAS, 1) if v30 is not None else None,
            "unidades_dia": round(u30 / VENTANA_DIAS, 2),
            "conversion": round(u30 / v30, 4) if v30 else None,
            "ingreso_30d": round(r30, 2),
            "margen_mediano": _mediana(f["margen_pct"] for f in act),
            "perdiendo_dinero": sum(1 for f in act if f["utilidad"] is not None and f["utilidad"] < 0),
            "con_costo": sum(1 for f in fs if f["costo"]["unitario"] is not None),
            "con_competencia": sum(1 for f in fs if f["competencia"]),
            # extensiones
            "visitas_30d": v30, "unidades_30d": u30,
            "activas_con_margen": sum(1 for f in act if f["margen_pct"] is not None),
            "activas_sin_costo": sum(1 for f in act if f["costo"]["unitario"] is None),
            "activas_bajo_piso": sum(1 for f in act if f["margen_pct"] is not None and f["margen_pct"] < 0.12),
            "pausadas_full_vendieron_150d": sum(1 for f in pfull if f["unidades_150d"]),
            "stock_full_activas": sum(int(f["stock_full"] or 0) for f in act if f["es_full"]),
            "sin_fila_kubera": sum(1 for f in fs if "sin_fila_en_kubera" in f["avisos"]),
            "visitas_fuente": "api_cuenta" if vserie else None,
        }

    por_canal: dict[str, dict[str, Any]] = {}
    fres = {"mercado_libre": (ctx.res_ml.get("frescura") or {}).get("ml_listings"),
            **{c: ctx.frescura_canal(c) for c in canales.CANALES}}
    for canal in CANALES:
        fs = [f for f in pubs if f["canal"] == canal]
        act = [f for f in fs if f["estado"] == "activa"]
        aviso = _aviso_congelado(canal, fres.get(canal), ctx.ahora) if canal != "mercado_libre" else None
        por_canal[canal] = {
            "publicaciones": len(fs), "activas": len(act), "frescura": fres.get(canal),
            "por_estado": dict(Counter(f["estado"] for f in fs)),
            "por_detalle": dict(Counter(f["estado_detalle"] for f in fs if f["estado_detalle"])),
            "puede_estar_activa": sum(1 for f in fs if f["estado_detalle"] == "puede_estar_activa"),
            "con_margen": sum(1 for f in fs if f["margen_pct"] is not None),
            "margen_mediano_activas": _mediana(f["margen_pct"] for f in act),
            "unidades_30d": sum(f["unidades_30d"] or 0 for f in fs),
            "ingreso_30d": round(sum(f["ingreso_30d"] or 0 for f in fs), 2),
            "supuesto_canal": canal != "mercado_libre", "aviso_frescura": aviso,
        }

    v_tot = [x["visitas_30d"] for x in por_cuenta.values()]
    embudo = {"visitas_30d": sum(v_tot) if all(v is not None for v in v_tot) else None,
              "unidades_30d": sum(x["unidades_30d"] for x in por_cuenta.values()),
              "ingreso_30d": round(sum(x["ingreso_30d"] for x in por_cuenta.values()), 2),
              "ventana": [ini, (ctx.hoy - dt.timedelta(days=1)).isoformat()],
              "nota": "visitas de toda la cuenta (API items_visits), unidades de channel.sales_daily; sin hoy"}

    el = ctx.elasticidades
    glob, grupos, diag = el.get("_global") or {}, el.get("_grupos") or {}, el.get("_diagnostico") or {}
    elasticidad = {
        "global": glob.get("beta"), "global_visitas": glob.get("beta_visitas"),
        "por_categoria": sorted(({"categoria": k, "nombre": g.get("nombre"), "beta": g.get("beta"),
                                  "beta_visitas": g.get("beta_visitas"), "n": g.get("n_items")}
                                 for k, g in grupos.items()), key=lambda x: -(x["n"] or 0)),
        "histograma": [{"desde": h.get("desde"), "hasta": h.get("hasta"), "n": h.get("finales"),
                        "crudas": h.get("crudas")} for h in diag.get("histograma") or []],
        "por_confianza": {k: v.get("n") for k, v in (diag.get("por_confianza") or {}).items()},
        "por_fuente": diag.get("por_fuente"),
        "validacion": {k: ((el.get("_validacion") or {}).get("con_cambio_precio") or {}).get(k)
                       for k in ("ingenuo", "modelo", "beta_global")},
    }

    fechas = sorted({d for d, _ in ctx.dia_cuenta} | {d for s in vis_cuenta.values() for d in s})
    serie = [{"fecha": d, "cuenta": c,
              "unidades": ctx.dia_cuenta[(d, c)][0] if (d, c) in ctx.dia_cuenta else 0,
              "ingreso": round(ctx.dia_cuenta[(d, c)][1], 2) if (d, c) in ctx.dia_cuenta else 0.0,
              "visitas": (vis_cuenta.get(c) or {}).get(d)}
             for d in fechas if d < fin for c in CUENTAS_ML]

    palancas = _palancas(ctx, filas_ml)
    res_precios = (ctx.precios_doc.get("resumen") or {})
    doc = {
        "generado_at": almacen.ahora_iso(), "version": VERSION,
        "por_cuenta": por_cuenta, "por_canal": por_canal, "embudo": embudo,
        "elasticidad": elasticidad, "palancas": palancas, "serie_diaria": serie,
        "palancas_resumen": {
            "reactivar_full": sum(1 for p in palancas if p["tipo"] == "reactivar_full"),
            "reactivar_full_con_ventas": sum(1 for p in palancas if p["tipo"] == "reactivar_full" and p["ventas_perdidas_dia"]),
            "ventas_perdidas_dia": round(sum(p["ventas_perdidas_dia"] or 0 for p in palancas if p["tipo"] == "reactivar_full"), 2),
            "ingreso_perdido_dia": round(sum(p["ingreso_perdido_dia"] or 0 for p in palancas if p["tipo"] == "reactivar_full"), 2),
            "perdiendo_dinero": sum(1 for p in palancas if p["tipo"] == "perdiendo_dinero"),
            "perdida_dia": round(sum(p.get("perdida_dia") or 0 for p in palancas if p["tipo"] == "perdiendo_dinero"), 2),
            "pausadas_full_sin_stock_sin_odoo": sum(
                1 for f in filas_ml if f["es_full"] and f["estado"] == "pausada"
                and ("out_of_stock" in (f["sub_status"] or []) or not f["stock_full"]) and not (f["stock_odoo"] or 0) > 0),
            "nota": "tipo=reactivar_full ordena por ventas_perdidas_dia; tipo=perdiendo_dinero trae ventas_perdidas_dia=null y perdida_dia",
        },
        "recomendaciones": {k: res_precios.get(k) for k in ("filas", "con_recomendacion", "clases", "por_confianza")},
    }
    doc["duracion_s"] = round(time.monotonic() - t0, 2)
    if escribir:
        almacen.escribir_json(almacen.ultimo("metricas.json"), doc)
    return doc


# ── estado ────────────────────────────────────────────────────────────────────
def _frescura() -> dict[str, Any]:
    dias: dict[str, str | None] = {}
    rk = _crudo("extraer_kubera_resumen.json", dias) or {}
    rm = _crudo("extraer_ml_resumen.json", dias) or {}
    fk = rk.get("frescura") or {}
    fm = rm.get("frescura") or {}

    def _mx(k: str) -> Any:
        v = fk.get(k)
        return v.get("max_updated_at") if isinstance(v, dict) else v

    ml_kub = [_mx(k) for k in fk if k.startswith("mercado_libre:")]
    pk = almacen.leer_json(almacen.ultimo("packing100.json"), {}) or {}
    # Canales de kubera: último cambio MASIVO (una fila editada a mano no hace
    # fresco a un canal congelado; ver `frescura_canales`). ~0.2 s por lectura.
    fc = frescura_canales((_crudo("kubera_publicaciones.json", dias) or {}).get("filas") or [])

    def _canal(c: str, clave: str) -> Any:
        return (fc.get(c) or {}).get("ultimo_cambio_masivo") or _mx(clave)

    return {
        # la universal de ML es la del scan de la API; kubera va aparte
        "ml_listings": fm.get("ml_listings") or rm.get("generado_at"),
        "ml_listings_kubera": max((x for x in ml_kub if x), default=None),
        "amazon_listings": _canal("amazon", "amazon:AMAZON"), "walmart_listings": _canal("walmart", "walmart:WALMART"),
        "temu_listings": _canal("temu", "temu:TEMU"), "tiktok_listings": _canal("tiktok", "tiktok:KUBERA"),
        "canales_max_updated_at": {c: v.get("max_updated_at") for c, v in fc.items()},
        "competencia_serp": fk.get("competencia_serp"), "competencia_best": fk.get("competencia_best"),
        "ventas": fk.get("ventas"), "visitas_api": fm.get("visitas_api"),
        "visitas_cache": fk.get("visitas_cache"), "envio_real": fk.get("envio_real"),
        "stock_odoo": fk.get("stock_odoo"), "costos_validados": fk.get("costos_validados"),
        "packing100": pk.get("generado_at"),
        "crudo_kubera": dias.get("extraer_kubera_resumen.json"), "crudo_ml": dias.get("extraer_ml_resumen.json"),
    }


def estado(etapas: dict[str, Any] | None = None, corrida: dict[str, Any] | None = None,
           escribir: bool = True) -> dict[str, Any]:
    """`ultimo/estado.json`. Conserva las etapas y la bitácora previas.

    ``etapas``: {nombre: {ok, filas, duracion_s, avisos, ...}} de esta corrida (se
    MEZCLAN sobre las anteriores: una corrida parcial no borra lo que dijo la
    última completa). ``corrida``: entrada de bitácora a añadir (máx. 30).
    """
    previo = almacen.leer_json(almacen.ultimo("estado.json"), {}) or {}
    ets = dict(previo.get("etapas") or {})
    ets.update(etapas or {})
    bit = list(previo.get("bitacora") or [])
    if corrida:
        bit = [b for b in bit if b.get("inicio") != corrida.get("inicio")] + [corrida]
    try:
        fres = _frescura()
    except Exception as exc:  # noqa: BLE001 — el estado se escribe aunque un resumen esté roto
        fres = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    rm = almacen.leer_json(almacen.ultimo_crudo("extraer_ml_resumen.json") or Path("-"), {}) or {}
    archivos = {}
    for nombre in ("publicaciones.json", "precios.json", "curvas.json", "historial.json", "metricas.json",
                   "costos.json", "elasticidades.json", "packing100.json", "contenedores.json"):
        p = almacen.ultimo(nombre)
        if p.exists():
            st = p.stat()
            archivos[nombre] = {"bytes": st.st_size, "actualizado_at": dt.datetime.fromtimestamp(
                st.st_mtime, dt.timezone.utc).isoformat(timespec="seconds")}
    doc = {
        "generado_at": almacen.ahora_iso(), "version": VERSION, "etapas": ets, "frescura": fres,
        "contadores_ml_api": rm.get("contadores_ml_api") or {}, "snapshots": almacen.dias_con_snapshot(),
        "bitacora": bit[-BITACORA_MAX:], "archivos": archivos,
    }
    if escribir:
        almacen.escribir_json(almacen.ultimo("estado.json"), doc)
    return doc


# ── principal ─────────────────────────────────────────────────────────────────
def construir() -> dict[str, Any]:
    """Arma publicaciones → historial → métricas con UNA carga de insumos.

    Cada salida se escribe por separado y de forma atómica: si una falla, las
    otras quedan escritas y la que falló conserva su versión anterior en `ultimo/`.
    """
    t0 = time.monotonic()
    ctx = _Ctx()
    res: dict[str, Any] = {"archivos": {}, "avisos": ctx.avisos, "carga_s": ctx.duracion_carga_s}
    pubs: list[dict] | None = None
    for nombre, fn in (("publicaciones", lambda: publicaciones(ctx)),
                       ("historial", lambda: historial(ctx, pubs)),
                       ("metricas", lambda: metricas(ctx, pubs))):
        t1 = time.monotonic()
        try:
            r = fn()
            if nombre == "publicaciones":
                pubs = r["filas"]
                info = {"ok": True, "filas": len(pubs), **{k: v for k, v in r["resumen"].items() if k != "crudos"}}
            elif nombre == "historial":
                info = {"ok": True, "filas": r["resumen"]["ids"], **r["resumen"]}
            else:
                info = {"ok": True, "filas": len(r["palancas"]), "palancas": r["palancas_resumen"]}
        except Exception as exc:  # noqa: BLE001 — una salida rota no tumba las demás
            if type(exc).__name__ == "EscrituraProhibida":
                raise
            log.exception("construir: %s falló", nombre)
            info = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
            if nombre == "publicaciones":  # sin filas nuevas, historial y métricas usan las del disco
                pubs = (almacen.leer_json(almacen.ultimo("publicaciones.json"), {}) or {}).get("filas") or []
                ctx.avisos.append("publicaciones falló: historial y métricas usan el publicaciones.json anterior")
        info["duracion_s"] = round(time.monotonic() - t1, 2)
        res["archivos"][nombre] = info
    res["ok"] = all(a["ok"] for a in res["archivos"].values())
    res["filas"] = (res["archivos"].get("publicaciones") or {}).get("filas")
    res["duracion_s"] = round(time.monotonic() - t0, 2)
    return res


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Ensambla ultimo/{publicaciones,historial,metricas,estado}.json")
    ap.add_argument("--solo", choices=("publicaciones", "historial", "metricas", "estado"))
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if a.solo == "estado":
        r: Any = estado()
    elif a.solo == "publicaciones":
        r = publicaciones()["resumen"]
    elif a.solo == "historial":
        c = _Ctx()
        r = historial(c, (almacen.leer_json(almacen.ultimo("publicaciones.json"), {}) or {}).get("filas"))
    elif a.solo == "metricas":
        r = {k: v for k, v in metricas().items() if k not in ("serie_diaria", "palancas")}
    else:
        r = construir()
        estado({"construir": {"ok": r["ok"], "filas": r["filas"], "duracion_s": r["duracion_s"],
                              "avisos": r["avisos"], "corrida_at": almacen.ahora_iso()}})
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str)[:20000])
