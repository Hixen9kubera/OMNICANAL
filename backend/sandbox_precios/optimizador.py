"""Precio recomendado por publicación de ML FULL → `ultimo/precios.json` y `ultimo/curvas.json`.

DISENO §5 y §6. Universo: las FULL activas y las FULL pausadas CON historia (las
que vendieron en 150 d y por eso `extraer_ml` les trajo `/prices`): 1,744 el
28-sep. Llave: `mercado_libre:<CUENTA>:<listing_id>` (una SKU tiene precio,
peso y demanda distintos en cada cuenta).

Por publicación, a cada precio P de la rejilla:

    U(P) = U0·(P/p_base)^β      V(P) = V0·(P/p_base)^βv      CR = U/V
    Π(P) = U(P) · utilidad(P)   (economia.utilidad: comisión por tramo, envío FULL
                                 por peso facturable, IVA, costo 525k)

- ``P0`` = precio COBRADO vivo (`/items/{id}/sale_price` > kubera `price_sale` >
  `price`). `item.price` es precio de lista: en 371 de 570 activas se cobra menos.
- ``U0, V0, p_base`` = últimos 28 días NO censurados (`elasticidad.base_item`);
  para una pausada es su último periodo con oferta. El modelo se ancla en el
  precio al que de verdad se vendió en esa base (``p_base``), no en P0: si la
  promoción cambió el precio, "actual" ya refleja ese cambio.
- Sin ventas en la base: U0 = 0.5/días (el mismo seudoconteo del log del modelo).
  Con demanda multiplicativa el precio óptimo NO depende de U0 (escala), así que
  la recomendación sale igual; lo que no es confiable son las unidades/día, y
  se avisa.

Precios que se reportan (DISENO §5): equilibrio y piso al centavo
(`economia.precio_para_margen`, respeta los escalones), máxima utilidad (argmax
de la rejilla), máximo volumen (el menor precio PRESENTABLE de la rejilla con
margen ≥ piso) y el RECOMENDADO: el menor precio presentable con
Π ≥ (1 − s)·Πmax y margen ≥ piso. `s` = 10%; 25% si la cobertura de stock pasa
de 120 días (liquidar); 0 si baja de 10 (no regalar lo que se va a agotar).

Precio presentable = termina en 9 (<$1,000, cada $10) o en 49/99 (≥$1,000), más
los escalones: 98.99/198.99/298.99/498.99/998.99 (un centavo abajo de cada
columna de envío de `_TARIFA_ML`: 299 paga $56 y 298.99 paga $38 con 454 g) y
500/1000 (desde ahí la comisión baja: 19.5% → 16% en 353 categorías).

Banda de competencia: el recomendado se acota a [0.70, 1.15] × referencia, sin
romper el piso, SOLO con referencias por producto: SERP filtrada (n≥5 y ≤30
días) o el sugerido de ML (`PRICE_DISCOUNT.suggested_discounted_price`). La
mediana de más vendidos de la HOJA se reporta pero NO acota: es de toda la
categoría (medido: precio propio / mediana_best va de 0.44 a 2.74 entre p10 y
p90 de las activas FULL) y acotar con ella movería precios por productos
distintos. Después de la banda, el recomendado se mantiene dentro de la
rejilla (0.55–1.45 × P0): fuera de ahí la curva es extrapolación.

Nada de esto se ejecuta: cada fila sale con ``autorizacion: "pendiente"`` y el
plan es una PROPUESTA de pasos con tope semanal (5%) o diario (2%). El paso más
chico es $1: abajo de $50 el 2% diario no alcanza un peso y el paso lo pasa
(se marca ``excede_tope`` y la fila avisa ``paso_minimo_excede_tope_*``).

Pausadas: ``cambio_pct`` se mide contra su precio REALIZADO en la base
(``cambio_ref = "precio_realizado_base"``; P0 de una pausada está en mediana 1.61×
arriba de lo que vendía) y llevan la razón ``precio_reactivacion``;
``cambio_vs_actual`` guarda el cambio contra P0. Activas: ``cambio_ref =
"precio_actual"``.

Costo de la mercancía (G1): ``contenedor.incluye_mercancia`` = true (Brandon: los
525k son todo el costo) → ninguna bajada se bloquea por el costo del panel; si
con él quedaría bajo el piso, solo el aviso ``riesgo_si_se_cobra_mercancia``.
Con false/null se bloquea (``baja_bloqueada_costo_sin_confirmar``).

Escalón de $299 (demanda): si `elasticidades._escalon_299` es significativo, U y
V se multiplican por exp(δ) abajo de $299 (relativo a p_base) en publicaciones que
lo cruzaron o con P0 en $250–$350. El 28-sep NO lo es (z 1.23) y no se usa.

Solo lee archivos (`ultimo/`, `crudo/`, `cache/`). Síncrono: desde una corrutina
va en `asyncio.to_thread` (regla 11 de CLAUDE.md).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sandbox_precios import _entorno  # noqa: E402

_entorno.cargar()
from sandbox_precios import almacen, costos_lab, economia, elasticidad  # noqa: E402

log = logging.getLogger("laboratorio.optimizador")

CUENTAS = ("BEKURA", "SANCORFASHION")
# Un centavo abajo de cada columna de envío (costos._TRAMOS_PRECIO) y el inicio
# de los tramos de comisión más baja.
ESCALONES_ENVIO = (98.99, 198.99, 298.99, 498.99, 998.99)
# $1,000 NO es escalón: a $1,000.00 ML aún cobra el % de abajo (medido el 29-sep,
# `economia.CORTES_ESTRICTOS`); el primer presentable con la comisión baja es $1,049.
ESCALONES_COMISION = (500.0,)
SERP_MAX_DIAS = 30
SERP_MIN_N = 5
PRUEBA_PCT = 0.05
PRUEBA_DIAS = 7
MANTENER_PCT = 0.01   # |cambio| < 1% = mantener (un peso en un precio de $99)
MAX_PASOS = 60
# Si el precio piso pasa de 2× el actual no se recomienda: 16 de 24 activas FULL
# con el piso fuera de la rejilla lo pasan, y en ellas domina el costo mal medido.
MAX_FACTOR_PISO = 2.0
REF_COMPARABLE = (0.5, 2.0)
# El efecto del escalón de $299 (si es significativo) se aplica a publicaciones
# que ya lo cruzaron en el panel o cuyo precio actual cae en este rango.
ESCALON_RANGO_P0 = (250.0, 350.0)
UMBRAL_299 = elasticidad.UMBRAL_ESCALON


# ── utilidades ────────────────────────────────────────────────────────────────
def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _r(v: float | None, d: int = 2) -> float | None:
    return None if v is None else round(v, d)


def _sku(v: Any) -> str:
    return str(v or "").strip().upper()


def _crudo(nombre: str) -> Any:
    p = almacen.ultimo_crudo(nombre)
    if p is None:
        return None
    return almacen.leer_jsonl(p) if nombre.endswith(".jsonl") else almacen.leer_json(p)


def _parametros() -> dict:
    try:
        return json.loads(Path(__file__).with_name("parametros.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _ts(s: Any) -> dt.datetime | None:
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


# ── precios presentables ──────────────────────────────────────────────────────
def presentable_arriba(x: float, psico: bool = True) -> float:
    """El menor precio presentable ≥ x.

    >>> [presentable_arriba(x) for x in (94.05, 99, 100, 290, 299.5, 999.5, 1010, 1060)]
    [98.99, 99.0, 109.0, 298.99, 309.0, 1049.0, 1049.0, 1099.0]
    """
    if not psico:
        return float(math.ceil(x - 1e-9))
    if x <= 999:
        y = math.ceil((x - 9) / 10.0 - 1e-9) * 10 + 9
    else:
        y = math.ceil((x - 49) / 50.0 - 1e-9) * 50 + 49
    esc = [e for e in ESCALONES_ENVIO + ESCALONES_COMISION if e >= x - 1e-9]
    return float(min([y] + esc))


def presentable_abajo(x: float, psico: bool = True) -> float:
    """El mayor precio presentable ≤ x.

    >>> [presentable_abajo(x) for x in (94.05, 99, 305, 510, 1010, 1120)]
    [89.0, 99.0, 299.0, 509.0, 999.0, 1099.0]
    """
    if not psico:
        return float(math.floor(x + 1e-9))
    if x < 1049:
        y = math.floor((x - 9) / 10.0 + 1e-9) * 10 + 9
        y = min(y, 999)
    else:
        y = math.floor((x - 49) / 50.0 + 1e-9) * 50 + 49
    esc = [e for e in ESCALONES_ENVIO + ESCALONES_COMISION if e <= x + 1e-9]
    return float(max([y] + esc))


def presentables(lo: float, hi: float, psico: bool = True) -> list[float]:
    out = set()
    x = presentable_arriba(lo, psico)
    while x <= hi + 1e-9:
        out.add(round(x, 2))
        x = presentable_arriba(x + 0.01, psico)
    return sorted(out)


# ── insumos ───────────────────────────────────────────────────────────────────
def _insumos() -> dict[str, Any]:
    precios = _crudo("ml_precios.jsonl") or []
    universo = {u["id"]: u for u in (_crudo("ml_universo.jsonl") or []) if u.get("id")}
    promos = {p["id"]: p for p in (_crudo("ml_promos.jsonl") or []) if p.get("id")}
    pubs = (_crudo("kubera_publicaciones.json") or {}).get("filas") or []
    comp = _crudo("kubera_competencia.json") or {}
    comisiones = almacen.leer_json(almacen.ruta("cache", "comisiones.json"), {}) or {}
    # Documento completo: trae los `tramos` con que se calcularon las tasas reales.
    reales = almacen.leer_json(almacen.ultimo("comisiones_reales.json"), {}) or {}
    kub_listing: dict[tuple[str, str], dict] = {}
    odoo_sku: dict[str, float] = {}
    for f in pubs:
        if f.get("canal") == "mercado_libre" and f.get("listing_id"):
            kub_listing.setdefault((f.get("cuenta"), f["listing_id"]), f)
        if f.get("stock_odoo") is not None and f.get("sku"):
            odoo_sku.setdefault(_sku(f["sku"]), _f(f["stock_odoo"]))
    comp_listing = {(r.get("cuenta"), r.get("listing_id")): r for r in comp.get("resumen") or []
                    if r.get("listing_id")}
    return {"precios": precios, "universo": universo, "promos": promos, "kub": kub_listing,
            "odoo": odoo_sku, "comp": comp_listing, "comisiones": comisiones, "reales": reales}


def _referencia(comp: dict | None, promo: dict | None, ahora: dt.datetime, p0: float | None = None
                ) -> dict[str, Any]:
    """Referencia de competencia en el orden de DISENO: SERP > sugerido ML > más vendidos.

    Solo es ``confiable`` (acota el recomendado y dispara los chips de competencia)
    si viene por PRODUCTO y queda entre 0.5× y 2× el precio actual: la SERP es de
    un término de búsqueda y mezcla paquetes y tamaños (COC-0159-NEG: mediana SERP
    de $983 contra $255 propios) y acotar con ella lleva el precio al techo de la
    rejilla por un producto que no es el mismo."""
    comp = comp or {}
    res: dict[str, Any] = {"precio": None, "fuente": None, "confiable": False, "n": None,
                           "capturado_en": None, "sugerido_ml": None, "mediana_best": _f(comp.get("mediana_best"))}
    sug = _f(((promo or {}).get("resumen") or {}).get("price_discount_sugerido"))
    res["sugerido_ml"] = sug
    t = _ts(comp.get("serp_capturado_en"))
    edad = (ahora - t).days if t else None
    if (comp.get("n_filtrado") or 0) >= SERP_MIN_N and _f(comp.get("mediana_filtrada")) and edad is not None \
            and edad <= SERP_MAX_DIAS:
        res.update({"precio": _f(comp["mediana_filtrada"]), "fuente": "serp", "confiable": True,
                    "n": comp.get("n_filtrado"), "capturado_en": comp.get("serp_capturado_en")})
    elif sug and sug > 0:
        res.update({"precio": sug, "fuente": "sugerido_ml", "confiable": True})
    elif res["mediana_best"]:
        res.update({"precio": res["mediana_best"], "fuente": "bestsellers", "confiable": False,
                    "n": comp.get("n_best"), "capturado_en": comp.get("best_capturado_en")})
    if res["confiable"] and p0 and not (REF_COMPARABLE[0] <= res["precio"] / p0 <= REF_COMPARABLE[1]):
        res["confiable"] = False
        res["no_comparable"] = True
    return res


# ── plan de pasos ─────────────────────────────────────────────────────────────
PASO_MINIMO_MXN = 1.0  # nadie mueve un precio por centavos: el paso más chico es $1


def _siguiente_paso(p: float, objetivo: float, tope: float, baja: bool, psico: bool) -> float:
    """Un paso de `p` hacia `objetivo` sin pasar el tope (fracción) respecto a `p`.

    Primero un precio presentable dentro del tope (si avanza al menos medio tope);
    si no, el peso entero dentro del tope. Si ni el peso entero cabe en el tope
    (2% de $47 = $0.94), el paso es de UN PESO (`PASO_MINIMO_MXN`) aunque pase el
    tope: el que llama lo marca (`excede_tope`) y la fila lo avisa. Nunca salta
    directo al objetivo si el objetivo queda fuera del tope (antes DEC-0012-BLN iba
    de $47 a $79 en un día, +68%; numeros #4).

    >>> _siguiente_paso(47.0, 79.0, 0.02, False, True), _siguiente_paso(47.0, 30.0, 0.02, True, True)
    (48.0, 46.0)
    >>> _siguiente_paso(99.0, 79.0, 0.02, True, True), _siguiente_paso(99.0, 98.5, 0.02, True, True)
    (98.0, 98.5)
    """
    limite = p * (1 - tope) if baja else p * (1 + tope)
    if (baja and objetivo >= limite - 1e-9) or (not baja and objetivo <= limite + 1e-9):
        return objetivo
    cand = presentable_arriba(limite, psico) if baja else presentable_abajo(limite, psico)
    avanza = (cand <= p * (1 - tope / 2)) if baja else (cand >= p * (1 + tope / 2))
    if avanza and ((baja and objetivo < cand < p) or (not baja and p < cand < objetivo)):
        return cand
    ent = float(math.ceil(limite - 1e-9)) if baja else float(math.floor(limite + 1e-9))
    if (baja and ent <= p - PASO_MINIMO_MXN / 2) or (not baja and ent >= p + PASO_MINIMO_MXN / 2):
        nxt = ent
    else:
        # El tope no alcanza para un peso: paso mínimo de $1 (pasa el tope).
        nxt = float(round(p)) - PASO_MINIMO_MXN if baja else float(round(p)) + PASO_MINIMO_MXN
    if (baja and nxt <= objetivo) or (not baja and nxt >= objetivo):
        nxt = objetivo
    return round(nxt, 2)


def plan_pasos(p0: float, rec: float, tope: float, cada_dias: int, inicio: dt.date,
               prueba: bool = False, psico: bool = True) -> list[dict[str, Any]]:
    """Pasos de p0 a rec, ninguno mayor que `tope` (fracción) respecto al anterior.

    Cada paso intermedio cae en un precio presentable si hay uno dentro del tope;
    si no (p. ej. de $99 a $94 no hay terminación en 9 a ≤5%), en pesos enteros, y
    si el peso entero tampoco cabe en el tope (precios < $50 con tope diario de
    2%), en pasos de $1 marcados ``excede_tope`` (la fila lo avisa): antes saltaba
    directo al recomendado (DEC-0012-BLN iba de $47 a $79 en un día, +68%) y
    después se movía por centavos ($47.94, $48.89…), que nadie ejecuta.
    Con ``prueba`` primero se llega a ±5% (en varios pasos si el tope es menor) y
    ahí se queda `PRUEBA_DIAS` para medir antes de seguir.

    >>> [x["precio"] for x in plan_pasos(99.0, 79.0, 0.05, 7, dt.date(2026, 9, 29))]
    [95.0, 91.0, 87.0, 83.0, 79.0]
    >>> [x["precio"] for x in plan_pasos(299.0, 329.0, 0.05, 7, dt.date(2026, 9, 29))]
    [309.0, 319.0, 329.0]
    >>> ps = plan_pasos(47.0, 79.0, 0.02, 1, dt.date(2026, 9, 29))
    >>> [x["precio"] for x in ps[:3]], ps[-1]["precio"], len(ps), all(x.get("excede_tope") for x in ps[:3])
    ([48.0, 49.0, 50.0], 79.0, 32, True)
    >>> [x["precio"] for x in ps if not x.get("excede_tope")][:2]
    [51.0, 52.0]
    >>> ps = plan_pasos(100.0, 80.0, 0.02, 1, dt.date(2026, 9, 29), prueba=True)
    >>> [(x["precio"], x.get("tipo")) for x in ps[:5]], ps[5]["fecha"]
    ([(98.99, None), (98.0, None), (97.0, None), (96.0, None), (95.0, 'prueba')], '2026-10-10')
    >>> all(ps[i + 1]["precio"] / ps[i]["precio"] - 1 >= -0.02 - 1e-9 for i in range(len(ps) - 1))
    True
    >>> any(x.get("excede_tope") for x in ps)
    False
    """
    pasos: list[dict[str, Any]] = []
    if rec is None or p0 is None or p0 <= 0 or abs(rec / p0 - 1) < 1e-9:
        return pasos
    p, fecha = p0, inicio
    baja = rec < p0
    if prueba and abs(rec / p0 - 1) >= PRUEBA_PCT:
        objetivo = p0 * (1 - PRUEBA_PCT) if baja else p0 * (1 + PRUEBA_PCT)
        pt = float(math.ceil(objetivo - 1e-9)) if baja else float(math.floor(objetivo + 1e-9))
        for _ in range(MAX_PASOS):
            q = pt if tope >= PRUEBA_PCT - 1e-9 else _siguiente_paso(p, pt, tope, baja, psico)
            paso: dict[str, Any] = {"fecha": fecha.isoformat(), "precio": round(q, 2)}
            if abs(q / p - 1) > tope + 1e-9:
                paso["excede_tope"] = True
            p = q
            if abs(q - pt) < 1e-9:
                paso.update(tipo="prueba",
                            nota=f"{PRUEBA_DIAS} días a {'−' if baja else '+'}{int(PRUEBA_PCT * 100)}% y medir "
                                 "unidades antes de seguir (sin evidencia propia de elasticidad)")
                pasos.append(paso)
                fecha = fecha + dt.timedelta(days=PRUEBA_DIAS)
                break
            pasos.append(paso)
            fecha = fecha + dt.timedelta(days=cada_dias)
    for _ in range(MAX_PASOS):
        if abs(rec / p - 1) < 1e-9 or (baja and p <= rec) or (not baja and p >= rec):
            break
        nxt = _siguiente_paso(p, rec, tope, baja, psico)
        paso = {"fecha": fecha.isoformat(), "precio": round(nxt, 2)}
        if abs(nxt / p - 1) > tope + 1e-9:
            paso["excede_tope"] = True
        pasos.append(paso)
        p, fecha = nxt, fecha + dt.timedelta(days=cada_dias)
    return pasos


# ── una publicación ───────────────────────────────────────────────────────────
class _Econ:
    """utilidad(P) de una publicación con la tabla de comisión ya resuelta."""

    def __init__(self, costo: float, categoria: str | None, peso: float, costo_full: float,
                 comisiones: dict, respaldo: dict, ancla_envio: float | None = None):
        self.costo, self.peso, self.full = costo, peso, costo_full
        self.cat, self.comisiones, self.respaldo = categoria, comisiones, respaldo
        # Precio REGULAR de la promoción: abajo de $299 el envío se cobra con la
        # columna del regular (peor caso medido, `economia.envio_ml`).
        self.ancla = ancla_envio
        # Llaves = `economia.cortes_comision`: las de la tabla de la API de la
        # categoría (0/299/500/1000) + las del respaldo. Una categoría con el
        # escalón en $1,000 (las 19 de 21.5%) no se aplana al de $500.
        self.tabla = economia.tabla_comision(categoria, comisiones, respaldo)
        self._memo: dict[float, dict] = {}

    def __call__(self, P: float) -> dict:
        P = round(float(P), 2)
        m = self._memo.get(P)
        if m is None:
            pct, _ = economia.pct_de_tabla(self.tabla, P)
            m = economia.utilidad(P, self.costo, self.cat, self.peso, self.full, pct=pct,
                                  comisiones_cache=self.comisiones, respaldo=self.respaldo,
                                  ancla_envio=self.ancla)
            self._memo[P] = m
        return m

    def fuente_pct(self, P: float) -> str | None:
        return economia.pct_de_tabla(self.tabla, P)[1]


# Razones que ponen los guardarraíles (fase 2): el resumen las cuenta aparte.
RAZONES_GUARDARRAIL = ("baja_bloqueada_costo_sin_confirmar", "stock_no_alcanza_al_recomendado",
                       "sin_evidencia_cambio_acotado", "coordinado_otra_cuenta", "brecha_entre_cuentas",
                       "tope_precio_regular", "costo_sospechoso_volumen")


def _acotar(p0: float, lim: float, baja: bool, psico: bool) -> float:
    """El precio a `lim` de p0 (hacia abajo o arriba): presentable si hay uno que
    no se quede a menos de 1% de p0; si no, pesos enteros."""
    x = p0 * (1 - lim) if baja else p0 * (1 + lim)
    if baja:
        c = presentable_arriba(x, psico)
        return c if c <= p0 * (1 - MANTENER_PCT) else float(math.ceil(x - 1e-9))
    c = presentable_abajo(x, psico)
    return c if c >= p0 * (1 + MANTENER_PCT) else float(math.floor(x + 1e-9))


def _optimizar_fila(pr: dict, ctx: dict) -> tuple[dict[str, Any], dict[str, Any] | None]:
    par = ctx["par"]
    lid, cuenta = pr["id"], pr["cuenta"]
    rid = f"mercado_libre:{cuenta}:{lid}"
    uni = ctx["universo"].get(lid) or {}
    kub = ctx["kub"].get((cuenta, lid)) or {}
    sku = _sku(pr.get("sku") or uni.get("sku") or kub.get("sku"))
    estado = "activa" if (uni.get("status") or pr.get("status_item")) == "active" else "pausada"
    razones: list[str] = []
    avisos: list[str] = []

    # Precio cobrado vivo.
    p0 = _f(pr.get("precio_cobrado"))
    fuente_p0 = "ml_sale_price"
    if not p0:
        p0, fuente_p0 = _f(kub.get("price_sale")), "kubera_price_sale"
    if not p0:
        p0, fuente_p0 = _f(kub.get("price")) or _f(uni.get("price")), "precio_lista"
    promo = pr.get("promo") or None
    fin_promo = _ts((promo or {}).get("fin"))
    if promo and promo.get("fin") and (fin_promo or ctx["ahora"]) > ctx["ahora"]:
        razones.append("promo_vigente")
    # La demanda se observó al precio de PROMOCIÓN (P0); el regular es el ancla con
    # que se ejecutaría la siguiente promoción.
    regular = _f((promo or {}).get("regular")) if promo else None
    if regular is not None and p0 and regular <= p0 + 1e-9:
        regular = None
    dias_fin = ((fin_promo - ctx["ahora"]).total_seconds() / 86400.0) if (promo and fin_promo) else None
    if dias_fin is not None and dias_fin < 0:
        dias_fin = None
    ancla = regular if (ctx["envio_promo_regular"] and regular and regular >= ctx["umbral_envio"]) else None
    if dias_fin is not None and dias_fin <= float(par.get("promo_dias_para_renovar", 7)):
        avisos.append(f"promo_vence_en_{max(0, math.ceil(dias_fin))}d: la promoción ({(promo or {}).get('tipo')}) "
                      f"vence el {fin_promo.astimezone(almacen.CDMX).date().isoformat()}; sin renovarla el precio "
                      f"sube al regular (${regular if regular else '?'})")

    # Elasticidad y base.
    el = ctx["elasticidades"].get(rid)
    glob = ctx["elasticidades"].get("_global") or {}
    if el is None:
        el = {"beta": glob.get("beta") or par["elasticidad_prior_unidades"],
              "beta_visitas": glob.get("beta_visitas") or par["elasticidad_prior_visitas"],
              "fuente": "global" if glob.get("beta") else "prior", "confianza": "baja", "n_semanas": 0,
              "base": {}}
        el["beta_conversion"] = el["beta"] - el["beta_visitas"]
        avisos.append("sin_panel_de_elasticidad")
    beta, beta_v = float(el["beta"]), float(el["beta_visitas"])
    if el.get("confianza") == "baja":
        razones.append("elasticidad_baja_confianza")
    base = el.get("base") or {}
    dias_b = int(base.get("dias") or 0)
    u0 = _f(base.get("u0")) or 0.0
    v0 = _f(base.get("v0"))
    p_base = _f(base.get("p_base")) or p0
    sin_ventas = not base.get("unidades")
    if sin_ventas:
        u0 = 0.5 / max(dias_b, ctx["dias_base"])
        avisos.append("sin_ventas_en_base: unidades/día con seudoconteo 0.5; el precio recomendado no depende de U0")
    if dias_b < 7:
        avisos.append(f"base_corta: {dias_b} días con oferta")
    # Evidencia propia para moverse (guardarraíl 3): β del item con error chico,
    # ventas en la base y al menos dos semanas de base. Sin ella el cambio se
    # acota a ±10% y el plan empieza con una prueba.
    se_crudo = _f(el.get("se_item_crudo"))
    evidencia = bool(el.get("fuente") == "item" and se_crudo is not None
                     and se_crudo < float(par.get("evidencia_se_max", 0.5))
                     and not sin_ventas and dias_b >= int(par.get("evidencia_dias_base_min", 14)))

    # Stock.
    stock_full = _f(uni.get("available_quantity")) if uni else None
    if stock_full is None:
        stock_full = _f(kub.get("stock_full"))
    sin_stock = "out_of_stock" in (uni.get("sub_status") or []) or not stock_full
    stock_odoo = ctx["odoo"].get(sku)

    # Escalón de $299 (P 'codo' de la auditoría): efecto ADICIONAL de estar abajo
    # de $299 (filtros y posición de búsqueda de ML), estimado en el panel
    # (`elasticidad._efecto_escalon`). Solo si es significativo (`usar`) y solo en
    # publicaciones que ya cruzaron $299 o con P0 entre $250 y $350; relativo a
    # p_base (la base ya trae el efecto si se vendió abajo de $299).
    esc = ctx.get("escalon") or {}
    lo_e, hi_e = ctx["escalon_rango_p0"]
    aplica_esc = bool(any((esc.get(k) or {}).get("usar") for k in ("unidades", "visitas"))
                      and (el.get("cruza_299") or (p0 and lo_e <= p0 <= hi_e)))

    def _fesc(nombre: str, P: float) -> float:
        return elasticidad._factor_escalon(esc, nombre, P, p_base) if aplica_esc else 1.0

    def U(P: float) -> float:
        return (u0 * (P / p_base) ** beta if p_base else u0) * _fesc("unidades", P)

    def V(P: float) -> float | None:
        return None if v0 is None else v0 * (P / p_base) ** beta_v * _fesc("visitas", P)

    u_act = U(p0) if p0 else None
    cobertura = None
    # Sin ventas en la base U0 es el seudoconteo (0.5/28): cualquier stock de 3
    # piezas daba > 120 días y la fila caía en "liquidar" (94 bajadas activas el
    # 28-sep). Sin ventas no hay cobertura que medir.
    if estado == "activa" and stock_full is not None and u_act and not sin_ventas:
        cobertura = stock_full / u_act if u_act > 0 else None
    if estado == "pausada" and sin_stock:
        razones.append("pausada_sin_stock")
    s = float(par["sacrificio_utilidad_max"])
    if estado == "activa" and stock_full is not None and not sin_ventas:
        if (cobertura is None and stock_full > 0) or (cobertura is not None and cobertura > par["cobertura_dias_exceso"]):
            s = float(par["sacrificio_liquidacion"])
            razones.append("stock_excesivo")
        elif cobertura is not None and cobertura < par["cobertura_dias_escasez"]:
            s = 0.0
            razones.append("stock_escaso")

    # Costo y peso.
    ce = ctx["costos"].get(sku) or {}
    costo = _f(ce.get("unitario")) if ce.get("fuente") != "sin_costo" else None
    peso, fuente_peso = costos_lab.peso_de(ctx["costos"], sku, lid)
    costo_panel = _f(ce.get("costo_panel")) or _f(ce.get("costo_panel_variantes_max"))
    if costo is None:
        razones.append("sin_costo")
    if not peso:
        razones.append("sin_peso")
    if costo is not None and costo < 1.0:
        avisos.append("costo_menor_a_1_peso: CBM sospechoso")
    if ce.get("costo_bajo_fob") and costo is not None:
        avisos.append(f"costo_bajo_fob: el costo de 525k (${costo:,.2f}) es menor que la mercancía sola "
                      f"(${float(ce.get('mercancia_mxn') or 0):,.2f})")
    # Guardarraíl de volumen: ML factura max(peso real, L×A×H/5000), así que el
    # volumen del paquete no puede pasar de kg_facturable/200 m³. Un cbm_pieza 3×
    # arriba de eso es un CBM de caja maestra (ACC-0652-AZL: 0.0557 m³ con 1.28 kg
    # facturables, 8.7×; recomendaba +100%).
    cbm = _f(ce.get("cbm_pieza"))
    vol_max = float(par.get("volumen_max_sobre_peso_facturable", 3.0))
    vol_sospechoso = bool(fuente_peso == "ml_billable" and peso and cbm and cbm > vol_max * float(peso) / 200.0)
    if vol_sospechoso:
        avisos.append(f"costo_sospechoso_volumen: {cbm:.4f} m³ por pieza es {cbm / (float(peso) / 200.0):.1f}× el "
                      f"volumen que permite el peso facturable de ML ({peso} kg ÷ 200); revisar CBM/costo "
                      f"({ce.get('fuente')})")
    # El lado OPUESTO (reverificación #3): un m³ por pieza demasiado CHICO para su
    # peso de ML subestima el costo de 525k (TEC-1835-MOTO: 61,074 kg/m³, costo
    # $0.23). Solo AVISA: el peso de ML puede ser volumétrico y no es prueba; si se
    # bloquea como el costo imposible es decisión de Brandon.
    dens_max = float(par.get("densidad_max_kg_m3", 3000))
    densidad = (float(peso) / cbm) if (fuente_peso in ("ml_billable", "ml_atributos") and peso and cbm) else None
    if densidad is not None and densidad > dens_max and costo is not None and not vol_sospechoso:
        avisos.append(f"costo_densidad_alta: {peso} kg de ML en {cbm:.5f} m³ por pieza = {densidad:,.0f} kg/m³ "
                      f"(> {dens_max:,.0f}); el m³ probablemente está chico y el costo de 525k "
                      f"(${costo:,.2f}) subestimado")

    ref = _referencia(ctx["comp"].get((cuenta, lid)), ctx["promos"].get(lid), ctx["ahora"], p0)
    if ref.get("no_comparable"):
        avisos.append(f"ref_{ref['fuente']}_no_comparable: {ref['precio']} vs {p0}")
    if ref["confiable"] and p0:
        if p0 > par["banda_competencia"][1] * ref["precio"]:
            razones.append("sobre_competencia")
        elif p0 < par["banda_competencia"][0] * ref["precio"]:
            razones.append("bajo_competencia")

    fila: dict[str, Any] = {
        "id": rid, "sku": sku, "cuenta": cuenta, "listing_id": lid,
        "titulo": uni.get("titulo") or kub.get("producto_nombre"),
        # Para la tabla de Precios (miniatura y enlace sin ir a publicaciones).
        # ML todavía manda miniaturas http:// y la CSP de api.py solo deja https.
        "thumbnail": almacen.a_https(uni.get("thumbnail")),
        "url": almacen.a_https(uni.get("permalink") or kub.get("url")),
        "estado": estado,
        "categoria_id": uni.get("category_id") or kub.get("category_id"),
        "precio_actual": _r(p0), "fuente_precio_actual": fuente_p0,
        "precio_recomendado": None, "cambio_pct": None, "cambio_ref": None, "cambio_vs_actual": None,
        "cambio_vs_realizado": None,
        "precio_equilibrio": None, "precio_piso": None, "precio_max_utilidad": None, "precio_max_volumen": None,
        "precio_ref_competencia": _r(ref["precio"]), "fuente_ref": ref["fuente"],
        "ref_confiable": ref["confiable"], "sugerido_ml": ref["sugerido_ml"],
        "mediana_bestsellers": ref["mediana_best"],
        "unidades_dia": {"actual": _r(u_act, 3), "recomendado": None},
        "visitas_dia": {"actual": _r(V(p0), 2) if p0 else None, "recomendado": None},
        "conversion": {"actual": None, "recomendado": None},
        "utilidad_dia": {"actual": None, "recomendado": None},
        "margen": {"actual": None, "recomendado": None},
        "elasticidad": {"beta": el["beta"], "beta_visitas": el["beta_visitas"],
                        "beta_conversion": el.get("beta_conversion"), "fuente": el["fuente"],
                        "n_semanas": el.get("n_semanas"), "confianza": el["confianza"],
                        "se_item_crudo": se_crudo, "evidencia": evidencia,
                        # Estimado siempre se reporta; `aplica` = entra en U(P)/V(P).
                        "escalon_299": {"aplica": aplica_esc, "cruza_299": bool(el.get("cruza_299")),
                                        **{f"{c}_{k}": (esc.get(c) or {}).get(k)
                                           for c in ("unidades", "visitas") for k in ("efecto", "se", "usar")}}},
        "stock": {"full": stock_full, "odoo": stock_odoo, "cobertura_dias": _r(cobertura, 1),
                  "cobertura_dias_recomendado": None},
        "costo": {"unitario": _r(costo, 4), "fuente": ce.get("fuente") or "sin_costo",
                  "costo_panel": _r(costo_panel, 4), "costo_bajo_fob": bool(ce.get("costo_bajo_fob")),
                  "cbm_pieza": cbm, "margen_costo_panel_recomendado": None},
        "peso": {"kg": peso, "fuente": fuente_peso},
        "envio_ancla_regular": ancla,
        "base": {"dias": dias_b, "desde": base.get("desde"), "hasta": base.get("hasta"),
                 "unidades": base.get("unidades"), "visitas": base.get("visitas"), "p_base": base.get("p_base"),
                 "factor_estacional_info": base.get("factor_estacional")},
        "promo": promo, "promo_dias_para_fin": _r(dias_fin, 1), "precio_regular": regular,
        "sacrificio": s, "recomendado_modelo": None, "recomendado_sin_guardarrailes": None, "rejilla": None,
        "razones": razones, "avisos": avisos,
        "plan": {"modo": "al_reactivar" if estado == "pausada" else "semanal", "pasos": []},
        "autorizacion": "pendiente",
    }
    if fila["visitas_dia"]["actual"] and u_act is not None:
        fila["conversion"]["actual"] = _r(u_act / fila["visitas_dia"]["actual"], 4) if fila["visitas_dia"]["actual"] else None
    if not p0:
        avisos.append("sin_precio_actual")
        return fila, None
    if costo is None or not peso:
        return fila, None

    econ = _Econ(costo, fila["categoria_id"], float(peso), float(ctx["costo_full"]), ctx["comisiones"], ctx["respaldo"],
                 ancla_envio=ancla)
    e0 = econ(p0)
    if e0["utilidad"] is not None and e0["utilidad"] < 0:
        razones.append("perdiendo_dinero")
    kw = {"comisiones_cache": ctx["comisiones"], "respaldo": ctx["respaldo"], "ancla_envio": ancla}
    piso_m = float(par["piso_margen"])
    # Equilibrio y piso EXACTOS al centavo, en forma cerrada por intervalo de
    # comisión/envío (`economia.precio_para_margen`): NO salen de la rejilla y
    # pueden quedar muy abajo de 0.55×P0 (o arriba de 1.45×P0). Se reportan tal
    # cual; `rejilla.fuera` dice cuáles caen fuera del rango que dibuja la curva.
    p_eq = economia.precio_equilibrio(costo, fila["categoria_id"], float(peso), float(ctx["costo_full"]), **kw)
    p_piso = economia.precio_para_margen(piso_m, costo, fila["categoria_id"], float(peso), float(ctx["costo_full"]), **kw)

    lo, hi = par["rejilla_min_factor"] * p0, par["rejilla_max_factor"] * p0
    fila["rejilla"] = {"min": round(lo, 2), "max": round(hi, 2),
                       "fuera": [n for n, v in (("equilibrio", p_eq), ("piso", p_piso))
                                 if v is not None and not (lo - 1e-9 <= v <= hi + 1e-9)]}
    paso = par["rejilla_paso"]
    k_max = int(round((par["rejilla_max_factor"] - par["rejilla_min_factor"]) / paso))
    rejilla = sorted({round(p0 * (par["rejilla_min_factor"] + paso * k), 2) for k in range(k_max + 1)}
                     | {e for e in ESCALONES_ENVIO + ESCALONES_COMISION if lo <= e <= hi}
                     | {round(p0, 2)})
    psico = bool(par.get("terminaciones_psicologicas", True))
    pres = presentables(lo, hi, psico)
    evaluados = sorted(set(rejilla) | set(pres))

    def punto(P: float) -> dict[str, Any]:
        m = econ(P)
        u = U(P)
        v = V(P)
        util = m["utilidad"]
        return {"precio": round(P, 2), "visitas_dia": _r(v, 2), "conversion": _r(u / v, 4) if v else None,
                "unidades_dia": round(u, 4), "utilidad_unit": util,
                "utilidad_dia": None if util is None else round(u * util, 2), "margen_pct": m["margen"]}

    pts = {P: punto(P) for P in evaluados}

    def cumple_piso(P: float) -> bool:
        # El margen de `economia.utilidad` va redondeado a 4 decimales: 11.996% pasaba
        # como 12.00%. `p_piso` es el MENOR precio exacto con margen ≥ piso (forma
        # cerrada, al centavo), así que abajo de él nada lo cumple (remate:
        # MUE-0225-PLA salía en $709 con piso $709.06).
        if p_piso is not None and P < p_piso - 1e-9:
            return False
        m = pts[P]["margen_pct"] if P in pts else econ(P)["margen"]
        return m is not None and m >= piso_m - 1e-9

    rentables = [P for P in pres if cumple_piso(P)]
    max_vol = rentables[0] if rentables else None

    def arriba_con_piso(x: float) -> float | None:
        """Menor presentable ≥ x que respeta el piso (sube por la escalera de presentables)."""
        y = presentable_arriba(x, psico)
        for _ in range(400):
            if cumple_piso(y):
                return y
            y = presentable_arriba(y + 0.01, psico)
        return None

    # Escasez: la curva no sabe que el stock se acaba. Bajar el precio con 6 días
    # de cobertura vende MÁS rápido lo que ya no alcanza (caso medido: VEH-0359-NEG
    # con 13 piezas en FULL salía a −44%). Con escasez solo se evalúan precios
    # ≥ P0 y se toma el de máxima utilidad (s = 0): mantener o subir.
    escasez = "stock_escaso" in razones

    def elegir(desde: float, hasta: float) -> float | None:
        """Menor presentable en [desde, hasta] con margen ≥ piso y Π ≥ (1−s)·Πmax.

        Πmax es el máximo entre ESOS MISMOS presentables (los que se pueden
        recomendar). Antes salía de la rejilla de 1% (precios no presentables): con
        s = 0 ningún presentable lo alcanzaba, `elegir` devolvía None y la fila caía
        al piso con un aviso falso (138 de 361 activas; TEC-0115-BLN: P* = 287.1 y
        recomendaba el piso de 198.99). Con escasez se exige además P ≥ P0 y se toma
        el máximo (s = 0)."""
        if escasez:
            desde = max(desde, p0)
        cands = [P for P in pres if desde - 1e-9 <= P <= hasta + 1e-9 and cumple_piso(P)]
        if escasez and desde - 1e-9 <= p0 <= hasta + 1e-9 and cumple_piso(p0):
            cands.append(round(p0, 2))
        util = {P: (pts[P] if P in pts else punto(P))["utilidad_dia"] for P in cands}
        cands = [P for P in cands if util[P] is not None]
        if not cands:
            return None
        pmax = max(util[P] for P in cands)
        if pmax <= 0:
            return None
        if escasez:
            return max(cands, key=lambda P: util[P])
        ok = [P for P in cands if util[P] >= (1 - s) * pmax - 1e-9]
        return min(ok) if ok else None

    por_piso = False
    rec = elegir(lo, hi)
    if rec is None and p_piso:
        # Nada rentable al piso dentro de la rejilla. Hasta 2× P0 el piso manda (se
        # avisa: la demanda ahí es extrapolación). Más allá casi siempre es el COSTO
        # el que está mal (SIL-0023-NEG: tarifa_7500 de $1,737 en un producto que
        # vendía 16 piezas/día a $220): no se inventa un precio, se pide revisar.
        if p_piso <= MAX_FACTOR_PISO * p0:
            rec = arriba_con_piso(max(p_piso, lo))
            if rec is not None:
                avisos.append("piso_fuera_de_rejilla: recomendado = piso redondeado; demanda extrapolada")
                por_piso = True
        else:
            avisos.append(f"costo_sospechoso: el precio piso ({p_piso}) es {p_piso / p0:.1f}× el actual; "
                          f"revisar costo ({ce.get('fuente')})")
    rec_modelo = rec
    piso_manda = por_piso

    # Banda de competencia (solo referencias comparables por producto). NO se
    # recorta el precio del modelo contra la banda: la curva es discontinua (envío
    # en $299, comisión en $500) y el recorte puede caer en un punto peor que el
    # actual y que el del modelo (TEC-0801-NEG: $489 → $349 con menos utilidad que
    # hoy). Se vuelve a elegir DENTRO de la banda con la misma regla.
    if rec is not None and not piso_manda and ref["confiable"]:
        b_lo, b_hi = par["banda_competencia"][0] * ref["precio"], par["banda_competencia"][1] * ref["precio"]
        if rec < b_lo - 1e-9 or rec > b_hi + 1e-9:
            d, h = max(lo, b_lo), min(hi, b_hi)
            r2 = elegir(d, h) if d <= h else None
            if r2 is None:
                # Nada en la banda cumple: lo más cercano a ella sin romper la
                # escasez, la rejilla ni el piso.
                if escasez and h < p0:
                    r2 = round(p0, 2)
                elif b_lo > hi:
                    r2 = presentable_abajo(hi, psico)
                elif b_hi < lo:
                    r2 = presentable_arriba(lo, psico)
                else:
                    base_b = max(d, p_piso or d)
                    r2 = arriba_con_piso(base_b)
                    # Sube por encima de la banda POR EL PISO solo si el piso la empuja
                    # (piso arriba de la banda, o el primer presentable no lo cumple). Si
                    # solo es el redondeo a terminación en 9 no es el piso (remate:
                    # ORG-0424-BLN $102.20 → $149, +46% sin evidencia, "por el piso"
                    # con el piso en $102.20): así el G3 sí lo acota.
                    if r2 is not None and r2 > h + 1e-9 and (
                            (p_piso is not None and p_piso > d + 1e-9) or r2 > presentable_arriba(base_b, psico) + 1e-9):
                        por_piso = True
            rec = r2
    if rec is not None:
        if escasez and rec < p0:
            rec = round(p0, 2)
        if not cumple_piso(rec):
            rec = arriba_con_piso(max(rec, p_piso or rec))
            por_piso = por_piso or rec is not None
    if por_piso and rec is not None:
        razones.append("piso_margen")

    # ── Guardarraíles (fase 2): evitan recomendaciones dañinas ────────────────
    rec_libre = rec
    act = estado == "activa"
    # G0. Costo físicamente imposible (volumen vs peso facturable de ML).
    if rec is not None and vol_sospechoso:
        rec = None
        razones.append("costo_sospechoso_volumen")
    # G5a. En promoción, nunca arriba del precio regular (eso es quitar la
    # promoción y subir la lista: otra decisión). Si el piso lo exige, se avisa.
    if rec is not None and regular and rec > regular + 1e-9:
        if cumple_piso(regular):
            rec = round(regular, 2)
            razones.append("tope_precio_regular")
        else:
            avisos.append(f"piso_sobre_precio_regular: el piso de margen ({p_piso}) queda arriba del regular ({regular})")
    # G3. Evidencia mínima para moverse (activas): sin β propia confiable, ventas
    # y dos semanas de base, el cambio se acota a ±10% (y el plan empieza con una
    # prueba). No acota una subida que exige el piso: abajo del piso no se vende.
    if rec is not None and act and not evidencia:
        lim = float(par.get("cambio_max_sin_evidencia", 0.10))
        if rec < p0 * (1 - lim) - 1e-9:
            c = _acotar(p0, lim, True, psico)
            if not cumple_piso(c):
                c = arriba_con_piso(c)
            rec = round(p0, 2) if (c is None or c >= p0 * (1 - MANTENER_PCT)) else c
            razones.append("sin_evidencia_cambio_acotado")
        elif rec > p0 * (1 + lim) + 1e-9 and not por_piso:
            c = _acotar(p0, lim, False, psico)
            if cumple_piso(c):
                rec = c
                razones.append("sin_evidencia_cambio_acotado")
            else:
                # El +10% no alcanza el piso (el margen no es monótono: el envío va por
                # tramos de precio). Primero lo más alto DENTRO del +10% que lo cumple;
                # si nada, mantener si hoy lo cumple; y solo si hoy está bajo el piso,
                # la MENOR subida que lo cumple — no la subida completa del modelo
                # (remate: COC-0159-NEG $255 → $369 cuando bastaba $289; 6 activas).
                dentro = [P for P in presentables(p0 * (1 + MANTENER_PCT), c, psico) if cumple_piso(P)]
                c2 = None if (dentro or cumple_piso(p0)) else arriba_con_piso(c)
                if dentro or cumple_piso(p0):
                    rec = max(dentro) if dentro else round(p0, 2)
                    razones.append("sin_evidencia_cambio_acotado")
                elif c2 is not None and c2 <= rec + 1e-9:
                    if c2 < rec - 1e-9:
                        rec = c2
                        razones.append("sin_evidencia_cambio_acotado")
                    por_piso = True
                    if "piso_margen" not in razones:
                        razones.append("piso_margen")
    # G1. Candado de costo. Brandon (28-sep): los 525k son TODO el costo (la
    # mercancía del packing list no les aplica) → `contenedor.incluye_mercancia`
    # = true y aquí no se bloquea nada: `completar` solo AVISA
    # (`riesgo_si_se_cobra_mercancia`). Con false/null (si algún día se cobrara la
    # mercancía) una bajada solo sale si con el costo del PANEL (mercancía +
    # flete) también respeta el piso de margen.
    if rec is not None and rec < p0 * (1 - MANTENER_PCT) and ctx["incluye_mercancia"] is not True:
        m_panel = None
        if costo_panel:
            m_panel = _Econ(costo_panel, fila["categoria_id"], float(peso), float(ctx["costo_full"]),
                            ctx["comisiones"], ctx["respaldo"], ancla_envio=ancla)(rec)["margen"]
        if m_panel is None or m_panel < piso_m - 1e-9:
            avisos.append(f"baja_bloqueada_costo_sin_confirmar: a ${rec:,.2f} el margen con el costo del panel "
                          f"({'sin costo del panel' if costo_panel is None else f'${costo_panel:,.2f}'}) sería "
                          f"{'—' if m_panel is None else f'{m_panel:.1%}'} (piso {piso_m:.0%})")
            rec = round(p0, 2)
            razones.append("baja_bloqueada_costo_sin_confirmar")
    # G2. Escasez medida AL PRECIO RECOMENDADO (activas): la regla de escasez mira
    # la cobertura a P0; bajar con 20 piezas en FULL puede dejarla en 5 días
    # (TEC-0651-AP-NLJ-200: −42.6%, 5.2 días, Odoo 7). Si FULL no alcanza 21 días
    # al recomendado y Odoo no repone 21 días, no se baja.
    if rec is not None and act and rec < p0 * (1 - MANTENER_PCT) and stock_full is not None and not sin_ventas:
        u_rec = U(rec)
        cob_rec = stock_full / u_rec if u_rec > 0 else None
        dias_rep = float(par.get("dias_reabasto_full", 21))
        repone = stock_odoo is not None and u_rec > 0 and stock_odoo >= u_rec * dias_rep
        if cob_rec is not None and cob_rec < float(par.get("cobertura_minima_al_recomendado", 21)) and not repone:
            avisos.append(f"stock_no_alcanza_al_recomendado: a ${rec:,.2f} FULL alcanza {cob_rec:.1f} días "
                          f"({stock_full:g} pzas) y Odoo tiene {stock_odoo if stock_odoo is not None else '—'}")
            rec = round(p0, 2)
            razones.append("stock_no_alcanza_al_recomendado")
    if rec is not None and rec not in pts:
        pts[rec] = punto(rec)
        if rec > hi:
            avisos.append("recomendado_sobre_rejilla_por_piso")
    if rec is not None and rec <= lo * 1.02 + 1e-9:
        avisos.append("recomendado_en_suelo_de_rejilla: la curva seguiría bajando; el límite 0.55×P0 decide "
                      "(la demanda debajo es extrapolación)")
    if rec is not None and regular and rec / regular < 1 - float(par.get("descuento_aviso_sobre_regular", 0.70)):
        avisos.append(f"descuento_mayor_al_70pct_del_regular: ${rec:,.2f} es {rec / regular:.0%} del regular ${regular:,.2f}")
    # Máxima utilidad sobre TODO lo evaluado, incluido el recomendado (si quedó
    # fuera de la rejilla por el piso, la rejilla sola marcaba un máximo peor).
    pi_max_p = max(pts, key=lambda P: pts[P]["utilidad_dia"] if pts[P]["utilidad_dia"] is not None else -1e18)

    fila.update({
        "precio_equilibrio": p_eq, "precio_piso": p_piso,
        "precio_max_utilidad": pi_max_p, "precio_max_volumen": max_vol,
        "recomendado_modelo": rec_modelo,
        "recomendado_sin_guardarrailes": rec_libre if rec_libre != rec else None,
    })
    fila["margen"]["actual"] = e0["margen"]
    fila["utilidad_dia"]["actual"] = None if e0["utilidad"] is None else round(U(p0) * e0["utilidad"], 2)
    fila["utilidad_unit_actual"] = e0["utilidad"]
    fila["comision_pct_actual"] = e0["pct"]
    fila["fuente_comision"] = econ.fuente_pct(p0)
    fila["envio_actual"] = e0["envio"]
    renovar = bool(act and dias_fin is not None and regular
                   and dias_fin <= float(par.get("promo_dias_para_renovar", 7)))
    fin_dia = fin_promo.astimezone(almacen.CDMX).date() if fin_promo else None

    def completar(rec_final: float | None) -> None:
        """Llena todo lo que depende del recomendado (también lo usa
        `_coordinar_cuentas` cuando mueve el precio de una fila)."""
        for r in ("escalon_envio_299", "tramo_comision_500", "escalon_demanda_299", "precio_reactivacion",
                  "fuera_de_banda_por_piso", "fuera_de_banda_por_escasez"):
            while r in razones:
                razones.remove(r)
        avisos[:] = [a for a in avisos if not a.startswith(("riesgo_si_se_cobra_mercancia", "paso_minimo_excede_tope"))]
        fila["precio_recomendado"] = rec_final
        pb = _f(base.get("p_base"))
        # Contra qué se mide el cambio (DISENO §6 `cambio_ref`). Pausada: contra el
        # precio REALIZADO de su base (lo que de verdad pagaban), no contra P0: el P0
        # de las pausadas está en mediana 1.61× arriba de lo que vendían (auditoría
        # P5/numeros #6), y "−36%" contra P0 era, contra lo realizado, +28%.
        # Activa: contra el precio actual (es lo que se va a mover).
        ref_c, fila["cambio_ref"] = ((pb, "precio_realizado_base") if (estado == "pausada" and pb)
                                     else (p0, "precio_actual"))
        fila["cambio_pct"] = None if rec_final is None else round(rec_final / ref_c - 1, 4)
        fila["cambio_vs_actual"] = None if rec_final is None else round(rec_final / p0 - 1, 4)
        fila["cambio_vs_realizado"] = None if (rec_final is None or not pb) else round(rec_final / pb - 1, 4)
        for k in ("unidades_dia", "visitas_dia", "conversion", "utilidad_dia", "margen"):
            fila[k]["recomendado"] = None
        fila["utilidad_unit_recomendado"] = None
        fila["stock"]["cobertura_dias_recomendado"] = None
        fila["costo"]["margen_costo_panel_recomendado"] = None
        if rec_final is None:
            fila["plan"] = {"modo": "al_reactivar" if estado == "pausada" else "semanal", "pasos": []}
            return
        if estado == "pausada":
            razones.append("precio_reactivacion")
        # Riesgo de la mercancía (G1 con `incluye_mercancia` = true, decisión de
        # Brandon): la bajada NO se bloquea; si con el costo del PANEL (mercancía +
        # flete) quedaría bajo el piso, solo se avisa. Con false la bloqueó arriba.
        if costo_panel:
            m_panel = _Econ(costo_panel, fila["categoria_id"], float(peso), float(ctx["costo_full"]),
                            ctx["comisiones"], ctx["respaldo"], ancla_envio=ancla)(rec_final)["margen"]
            fila["costo"]["margen_costo_panel_recomendado"] = m_panel
        else:
            m_panel = None
        if (ctx["incluye_mercancia"] is True and rec_final < p0 * (1 - MANTENER_PCT)
                and (m_panel is None or m_panel < piso_m - 1e-9)):
            avisos.append(f"riesgo_si_se_cobra_mercancia: a ${rec_final:,.2f} (abajo del precio publicado hoy, "
                          f"${p0:,.2f}) el margen con el costo del panel "
                          f"({'sin costo del panel' if costo_panel is None else f'${costo_panel:,.2f}'}) sería "
                          f"{'—' if m_panel is None else f'{m_panel:.1%}'} (piso {piso_m:.0%}); hoy no aplica "
                          "(contenedor.incluye_mercancia = true: los 525k son todo el costo)")
        # Fuera de la banda de competencia: la razón que lo explica (numeros #10).
        if ref["confiable"]:
            b_lo, b_hi = par["banda_competencia"][0] * ref["precio"], par["banda_competencia"][1] * ref["precio"]
            if rec_final > b_hi + 1e-9 or rec_final < b_lo - 1e-9:
                if rec_final > b_hi and escasez and rec_final <= p0 + 1e-9:
                    razones.append("fuera_de_banda_por_escasez")
                elif rec_final > b_hi and (not cumple_piso(presentable_abajo(b_hi, psico))
                                           or "piso_margen" in razones):
                    razones.append("fuera_de_banda_por_piso")
        if rec_final not in pts:
            pts[rec_final] = punto(rec_final)
        pr_ = pts[rec_final]
        fila["unidades_dia"]["recomendado"] = _r(pr_["unidades_dia"], 3)
        fila["visitas_dia"]["recomendado"] = pr_["visitas_dia"]
        fila["conversion"]["recomendado"] = pr_["conversion"]
        fila["utilidad_dia"]["recomendado"] = pr_["utilidad_dia"]
        fila["margen"]["recomendado"] = pr_["margen_pct"]
        fila["utilidad_unit_recomendado"] = pr_["utilidad_unit"]
        if act and stock_full is not None and not sin_ventas and pr_["unidades_dia"]:
            fila["stock"]["cobertura_dias_recomendado"] = _r(stock_full / pr_["unidades_dia"], 1)
        lo_p, hi_p = min(p0, rec_final), max(p0, rec_final)
        # Con ancla de promoción no hay ahorro de envío modelado debajo de $299
        # (se cobra la columna del regular): el escalón no es razón.
        if ancla is None and ((lo_p < 299 <= hi_p) or (289 <= rec_final < 299)):
            razones.append("escalon_envio_299")
        if (lo_p < 500 <= hi_p) or rec_final == 500:
            razones.append("tramo_comision_500")
        # El efecto de demanda de estar abajo de $299 entra en la curva entre la base
        # (p_base), el actual y el recomendado: es parte de por qué se recomienda.
        if aplica_esc:
            xs = [x for x in (p0, rec_final, _f(base.get("p_base"))) if x]
            if min(xs) < UMBRAL_299 <= max(xs):
                razones.append("escalon_demanda_299")
        # Plan: pausada → se publica directo al reactivar; activa → pasos semanales (y diario aparte).
        if estado == "pausada":
            fila["plan"] = {"modo": "al_reactivar",
                            "pasos": [] if abs(rec_final / p0 - 1) < 1e-9 else
                            [{"fecha": ctx["manana"].isoformat(), "precio": rec_final, "tipo": "al_reactivar"}],
                            "nota": "pausada: sin ventas que proteger, el precio se fija al volver a publicar"}
            return
        prueba = not evidencia
        inicio = max(ctx["manana"], fin_dia) if (renovar and fin_dia) else ctx["manana"]
        semanal = plan_pasos(p0, rec_final, float(par["paso_max_semana"]), 7, inicio, prueba, psico)
        diario = plan_pasos(p0, rec_final, float(par["paso_max_dia"]), 1, inicio, prueba, psico)
        plan: dict[str, Any] = {"modo": "semanal", "pasos": semanal, "diario": {"modo": "diario", "pasos": diario},
                                "prueba": prueba and abs(rec_final / p0 - 1) >= PRUEBA_PCT}
        # Precio bajo: el tope (2% diario / 5% semanal) no alcanza para un peso y el
        # paso mínimo de $1 lo pasa. Se dice, no se esconde en centavos.
        for nombre, pasos_, tope_ in (("diario", diario, par["paso_max_dia"]),
                                      ("semanal", semanal, par["paso_max_semana"])):
            n_exc = sum(1 for x in pasos_ if x.get("excede_tope"))
            if n_exc:
                plan.setdefault("pasos_exceden_tope", {})[nombre] = n_exc
                avisos.append(f"paso_minimo_excede_tope_{nombre}: a ${p0:,.2f} el {float(tope_):.0%} es "
                              f"${p0 * float(tope_):,.2f}; el paso mínimo de $1 ({1 / p0:.1%}) lo pasa en "
                              f"{n_exc} de {len(pasos_)} pasos")
        if renovar and rec_final > regular + 1e-9:
            # El recomendado (el piso) queda ARRIBA del regular: una promoción no se
            # puede renovar a más que el precio regular (remate: MLM3032324939, piso
            # $266.84 contra regular $238). No es `renovar_promo`: dejar vencer la
            # promoción y subir el precio de lista; el plan es el de siempre.
            plan["nota"] = (f"la promoción vence el {fin_dia.isoformat() if fin_dia else '?'}; el recomendado "
                            f"(${rec_final:,.2f}) queda arriba del regular (${regular:,.2f}): no renovarla, "
                            f"subir el precio de lista")
            plan["promo_fin"] = fin_dia.isoformat() if fin_dia else None
            plan["precio_regular"] = regular
        elif renovar:
            # La promoción vence en ≤ 7 días: el plan se ejecuta como la SIGUIENTE
            # promoción (precio de promo sobre el regular como ancla), empezando el
            # día que vence la actual. Mantener = renovarla al mismo precio: si no,
            # el precio salta al regular (~1.8×) y la demanda cae a ~40%.
            if not semanal:
                semanal = [{"fecha": inicio.isoformat(), "precio": round(p0, 2)}]
                diario = [dict(semanal[0])]
            for x in semanal + diario:
                x["precio_promo"], x["precio_regular"] = x["precio"], regular
                x.setdefault("tipo", "renovar_promo")
            nota = (f"la promoción vence el {fin_dia.isoformat() if fin_dia else '?'}: renovarla a este precio "
                    f"sobre el regular ${regular:,.2f}")
            for lista in (semanal, diario):
                lista[0]["nota"] = f"{lista[0]['nota']} · {nota}" if lista[0].get("nota") else nota
            plan.update(modo="renovar_promo", pasos=semanal, diario={"modo": "diario", "pasos": diario},
                        promo_fin=fin_dia.isoformat() if fin_dia else None, precio_regular=regular)
        fila["plan"] = plan

    completar(rec)
    ctx["_ajustes"][rid] = {"completar": completar, "p0": p0, "cumple_piso": cumple_piso, "psico": psico,
                            "evidencia": evidencia, "punto": lambda P: pts[P] if P in pts else punto(P)}
    marcadores = {"actual": _r(p0), "recomendado": rec, "piso": p_piso, "equilibrio": p_eq,
                  "max_utilidad": pi_max_p, "ref_competencia": _r(ref["precio"])}
    curva_precios = sorted(set(rejilla) | {x for x in (rec, max_vol, pi_max_p) if x is not None})
    curva = {"puntos": [pts[P] if P in pts else punto(P) for P in curva_precios], "marcadores": marcadores,
             "rejilla": fila["rejilla"]}
    return fila, curva


def _subida_por_brecha(r: float, hi: float, tope: float, aj: dict, lim_sin_evidencia: float) -> float | None:
    """Precio al que la brecha entre cuentas obliga a subir `r` (None = no hace falta
    o no se puede). Remate de la coordinación (reverificación #2):

    - una fila que BAJABA nunca termina arriba de su P0: a lo más se MANTIENE
      (TEC-1291-MUL iba de $80 a $79 y el redondeo a terminación en 9 la volvía una
      subida a $89, +11%);
    - una fila sin evidencia no sube por la brecha más allá del ±10% del G3.
    """
    if not r or hi / r - 1 <= tope + 1e-9:
        return None
    p0, psico = aj["p0"], aj["psico"]
    x = hi / (1 + tope)
    nuevo = min(presentable_arriba(x, psico), hi)
    if r < p0 * (1 - MANTENER_PCT):
        if nuevo >= p0 * (1 - MANTENER_PCT):
            entero = float(math.ceil(x - 1e-9))
            nuevo = entero if entero < p0 * (1 - MANTENER_PCT) else round(p0, 2)
    elif not aj.get("evidencia") and nuevo > p0 * (1 + lim_sin_evidencia) + 1e-9:
        nuevo = max(r, _acotar(p0, lim_sin_evidencia, False, psico))
    if nuevo <= r + 1e-9 or not aj["cumple_piso"](nuevo):
        return None
    return round(nuevo, 2)


def _coordinar_cuentas(filas: list[dict], ctx: dict, curvas: dict) -> dict[str, int]:
    """Guardarraíl 4: nuestras dos cuentas no compiten entre sí.

    Por SKU ACTIVO en las dos cuentas (34 el 28-sep; la β propia de cada una
    incluye robarle ventas a la otra, así que la ganancia conjunta está inflada):
    - si bajan las dos, solo baja UNA cuenta: la de más unidades/día **entre las que
      pueden bajar sin abrir la brecha** (con las otras en su precio de hoy); las
      demás se mantienen (`coordinado_otra_cuenta`). Antes se elegía solo por
      unidades y la brecha le quitaba la bajada justo a esa cuenta: 5 de 9 SKUs
      quedaban sin que NADIE bajara y con un aviso falso (TEC-0519-NAR-GRI: Kubera,
      la más barata hoy, no podía bajar sin abrir la brecha; San Corpe sí);
    - la brecha entre los recomendados no crece más allá de max(10%, la brecha de
      HOY): se sube el menor (nunca se baja nada aquí), sin volver subida una
      bajada (`_subida_por_brecha`).
    """
    par = ctx["par"]
    brecha = float(par.get("brecha_max_entre_cuentas", 0.10))
    lim_se = float(par.get("cambio_max_sin_evidencia", 0.10))
    por_sku: dict[str, list[dict]] = defaultdict(list)
    for f in filas:
        if f["estado"] == "activa" and f["precio_recomendado"] is not None and f["sku"] and f["id"] in ctx["_ajustes"]:
            por_sku[f["sku"]].append(f)
    n = Counter()
    for sku, fs in por_sku.items():
        if len({f["cuenta"] for f in fs}) < 2:
            continue
        n["skus_en_dos_cuentas"] += 1
        precios_act = [f["precio_actual"] for f in fs if f["precio_actual"]]
        gap_hoy = (max(precios_act) / min(precios_act) - 1) if len(precios_act) >= 2 else 0.0
        tope = max(brecha, gap_hoy)
        bajan = [f for f in fs if (f["cambio_pct"] or 0) < -MANTENER_PCT]
        if len({f["cuenta"] for f in bajan}) >= 2:
            ids_bajan = {f["id"] for f in bajan}

            def sigue_bajando(c: dict) -> bool:
                """¿`c` sigue bajando después de la brecha, con las demás cuentas en su P0?"""
                aj = ctx["_ajustes"][c["id"]]
                otros = [round(ctx["_ajustes"][g["id"]]["p0"], 2)
                         if (g["id"] in ids_bajan and g["cuenta"] != c["cuenta"]) else g["precio_recomendado"]
                         for g in fs if g is not c]
                hi = max(otros + [c["precio_recomendado"]])
                final = _subida_por_brecha(c["precio_recomendado"], hi, tope, aj, lim_se) or c["precio_recomendado"]
                return final < aj["p0"] * (1 - MANTENER_PCT)

            orden = sorted(bajan, key=lambda f: ((f["unidades_dia"]["actual"] or 0), f["cuenta"] == "BEKURA"),
                           reverse=True)
            lider = next((c for c in orden if sigue_bajando(c)), None)
            if lider is None:
                n["ninguna_cuenta_puede_bajar"] += 1
            elif lider is not orden[0]:
                n["lider_por_brecha"] += 1
            for f in bajan:
                if lider is not None and f["cuenta"] == lider["cuenta"]:
                    continue
                if lider is None and f is orden[0]:
                    continue  # la de más unidades conserva su bajada; la brecha la acota abajo
                aj = ctx["_ajustes"][f["id"]]
                f["recomendado_sin_guardarrailes"] = f.get("recomendado_sin_guardarrailes") or f["precio_recomendado"]
                aj["completar"](round(aj["p0"], 2))
                f["razones"].append("coordinado_otra_cuenta")
                if lider is None:
                    f["avisos"].append(f"coordinado_otra_cuenta: ninguna de las dos cuentas puede bajar sin que la brecha "
                                       f"entre ellas pase de {tope:.0%}; esta se mantiene")
                else:
                    por = ("la de más unidades/día" if lider is orden[0] else
                           "la de más unidades/día que puede bajar sin abrir la brecha entre cuentas")
                    f["avisos"].append(f"coordinado_otra_cuenta: solo baja {lider['cuenta']} ({lider['listing_id']}), "
                                       f"{por}; esta se mantiene para no competir entre cuentas")
                n["bajada_coordinada"] += 1
        hi_rec = max(f["precio_recomendado"] for f in fs)
        for f in fs:
            r = f["precio_recomendado"]
            aj = ctx["_ajustes"][f["id"]]
            nuevo = _subida_por_brecha(r, hi_rec, tope, aj, lim_se)
            if nuevo is None:
                continue
            f["recomendado_sin_guardarrailes"] = f.get("recomendado_sin_guardarrailes") or r
            aj["completar"](nuevo)
            f["razones"].append("brecha_entre_cuentas")
            f["avisos"].append(f"brecha_entre_cuentas: ${r:,.2f} quedaba {hi_rec / r - 1:.0%} abajo de la otra cuenta "
                               f"(${hi_rec:,.2f}; hoy la brecha es {gap_hoy:.0%}): se sube a ${nuevo:,.2f}")
            n["brecha_acotada"] += 1
    # La curva de las filas que se movieron: marcador y punto del nuevo recomendado.
    for f in filas:
        c = curvas.get(f["id"])
        aj = ctx["_ajustes"].get(f["id"])
        if not c or not aj:
            continue
        r = f["precio_recomendado"]
        c["marcadores"]["recomendado"] = r
        if r is not None and not any(abs(p["precio"] - r) < 0.005 for p in c["puntos"]):
            c["puntos"].append(aj["punto"](r))
            c["puntos"].sort(key=lambda p: p["precio"])
    return dict(n)


# ── principal ─────────────────────────────────────────────────────────────────
def optimizar(escribir: bool = True, reestimar: bool = False) -> dict[str, Any]:
    """Recomendación para todas las ML FULL activas y pausadas con historia."""
    t0 = time.monotonic()
    par_all = _parametros()
    par = dict(par_all.get("optimizador") or {})
    ml = par_all.get("mercado_libre") or {}
    el = {} if reestimar else elasticidad.leer()
    if not el or "_global" not in el:
        el = elasticidad.estimar()
    ins = _insumos()
    costos = costos_lab.leer()
    if not costos:
        raise RuntimeError("falta ultimo/costos.json: correr costos_lab.calcular() antes")
    ahora = dt.datetime.now(dt.timezone.utc)
    cont = par_all.get("contenedor") or {}
    ctx = {
        "incluye_mercancia": cont.get("incluye_mercancia"),
        "envio_promo_regular": bool(ml.get("envio_promo_columna_regular", True)),
        "umbral_envio": float(ml.get("umbral_envio_gratis") or 299),
        "_ajustes": {},
        "par": par, "elasticidades": el, "costos": costos, "universo": ins["universo"], "kub": ins["kub"],
        "odoo": ins["odoo"], "comp": ins["comp"], "promos": ins["promos"], "comisiones": ins["comisiones"],
        "respaldo": economia.respaldo_desde_doc(ml, ins["reales"]),
        "costo_full": float(ml.get("costo_full_unitario_mes") or 0.0),
        "ahora": ahora, "manana": almacen.hoy_cdmx() + dt.timedelta(days=1),
        "dias_base": int(par.get("dias_base", 28)),
        "escalon": el.get("_escalon_299") or {},
        "escalon_rango_p0": tuple(par.get("escalon_299_rango_p0") or ESCALON_RANGO_P0),
    }
    filas, curvas = [], {}
    for pr in ins["precios"]:
        if not pr.get("es_full") or pr.get("cuenta") not in CUENTAS:
            continue
        fila, curva = _optimizar_fila(pr, ctx)
        filas.append(fila)
        if curva:
            curvas[fila["id"]] = curva
    coordinacion = _coordinar_cuentas(filas, ctx, curvas)
    filas.sort(key=lambda f: -(f["unidades_dia"]["actual"] or 0))
    resumen = resumir(filas)
    resumen["coordinacion_cuentas"] = coordinacion
    resumen["duracion_s"] = round(time.monotonic() - t0, 2)
    salida = {"generado_at": almacen.ahora_iso(), "version": "lab-0.1",
              "parametros": {**par, "costo_full_unitario_mes": ctx["costo_full"],
                             "elasticidad_tope_tau2": (el.get("_meta") or {}).get("tope_tau2"),
                             "beta_global": (el.get("_global") or {}).get("beta"),
                             "escalones": list(ESCALONES_ENVIO + ESCALONES_COMISION),
                             "mantener_si_cambio_menor_a": MANTENER_PCT,
                             "incluye_mercancia": ctx["incluye_mercancia"],
                             "envio_promo_columna_regular": ctx["envio_promo_regular"]},
              "resumen": resumen, "filas": filas}
    if escribir:
        almacen.escribir_json(almacen.ultimo("precios.json"), salida)
        almacen.escribir_json(almacen.ultimo("curvas.json"), curvas)
    return salida


def _clase(c: float | None) -> str:
    if c is None:
        return "sin_recomendacion"
    if c > MANTENER_PCT:
        return "subir"
    if c < -MANTENER_PCT:
        return "bajar"
    return "mantener"


def resumir(filas: list[dict]) -> dict[str, Any]:
    """Agregados para la interfaz y el informe (por cuenta y estado)."""
    out: dict[str, Any] = {"filas": len(filas),
                           "con_recomendacion": sum(1 for f in filas if f["precio_recomendado"] is not None)}
    por: dict[str, dict[str, Any]] = {}
    for f in filas:
        k = f"{f['cuenta']}:{f['estado']}"
        d = por.setdefault(k, {"n": 0, "con_recomendacion": 0, "clases": Counter(), "cambios": [],
                               "cambios_vs_actual": [], "pond": [0.0, 0.0],
                               "u_act": 0.0, "u_rec": 0.0, "pi_act": 0.0, "pi_rec": 0.0,
                               "banda_movio": 0, "razones": Counter(), "avisos": Counter()})
        d["n"] += 1
        d["razones"].update(f["razones"])
        d["avisos"].update({a.split(":")[0].strip() for a in f["avisos"]})
        d["clases"][_clase(f["cambio_pct"])] += 1
        if f["precio_recomendado"] is None:
            continue
        d["con_recomendacion"] += 1
        d["cambios"].append(f["cambio_pct"])
        d["cambios_vs_actual"].append(f.get("cambio_vs_actual", f["cambio_pct"]))
        # Cambio ponderado por ingreso diario modelado al precio actual (las filas
        # sin ventas en la base pesan su seudoconteo: casi nada).
        ing = (f["unidades_dia"]["actual"] or 0) * (f["precio_actual"] or 0)
        d["pond"][0] += ing * f["cambio_pct"]
        d["pond"][1] += ing
        if f["recomendado_modelo"] is not None and f["recomendado_modelo"] != f["precio_recomendado"]:
            d["banda_movio"] += 1
        ua, ur = f["unidades_dia"]["actual"], f["unidades_dia"]["recomendado"]
        pa, prr = f["utilidad_dia"]["actual"], f["utilidad_dia"]["recomendado"]
        if None not in (ua, ur, pa, prr) and "sin_ventas_en_base" not in " ".join(f["avisos"]):
            d["u_act"] += ua
            d["u_rec"] += ur
            d["pi_act"] += pa
            d["pi_rec"] += prr
    def _q(cs: list[float]) -> dict[str, float]:
        cs = sorted(cs)
        n = len(cs)
        return ({"p10": cs[n // 10], "p25": cs[n // 4], "mediana": median(cs),
                 "p75": cs[3 * n // 4], "p90": cs[min(n - 1, 9 * n // 10)]} if n else {})

    for k, d in por.items():
        d["cambio_pct"] = _q(d.pop("cambios"))
        d["cambio_vs_actual"] = _q(d.pop("cambios_vs_actual"))
        pn, pd = d.pop("pond")
        d["cambio_ponderado_ingreso"] = round(pn / pd, 4) if pd else None
        d["clases"] = dict(d["clases"])
        d["razones"] = dict(d["razones"])
        d["avisos"] = dict(d["avisos"])
        for x in ("u_act", "u_rec", "pi_act", "pi_rec"):
            d[x] = round(d[x], 2)
        d["delta_unidades_dia"] = round(d["u_rec"] - d["u_act"], 2)
        d["delta_utilidad_dia"] = round(d["pi_rec"] - d["pi_act"], 2)
    out["por_cuenta_estado"] = por
    out["por_confianza"] = dict(Counter(f["elasticidad"]["confianza"] for f in filas))
    # Cuántas recomendaciones cambió cada guardarraíl (fase 2), por estado.
    out["guardarrailes"] = {e: dict(Counter(r for f in filas if f["estado"] == e for r in f["razones"]
                                            if r in RAZONES_GUARDARRAIL)) for e in ("activa", "pausada")}
    out["clases"] = dict(Counter(_clase(f["cambio_pct"]) for f in filas))
    return out


def leer() -> dict[str, Any]:
    return almacen.leer_json(almacen.ultimo("precios.json"), {}) or {}


if __name__ == "__main__":
    import argparse
    import doctest

    ap = argparse.ArgumentParser(description="Precios recomendados del laboratorio (solo archivos).")
    ap.add_argument("--reestimar", action="store_true", help="recalcula elasticidades.json antes")
    ap.add_argument("--prueba", action="store_true", help="solo doctests")
    a = ap.parse_args()
    fallas, _ = doctest.testmod(sys.modules[__name__])
    if fallas:
        raise SystemExit(f"{fallas} doctests fallaron")
    if not a.prueba:
        logging.basicConfig(level=logging.INFO)
        s = optimizar(reestimar=a.reestimar)
        print(json.dumps(s["resumen"], ensure_ascii=False, indent=1, default=str))
