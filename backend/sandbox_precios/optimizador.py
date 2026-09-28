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
plan es una PROPUESTA de pasos con tope semanal (5%) o diario (2%).

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
ESCALONES_COMISION = (500.0, 1000.0)
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
    [98.99, 99.0, 109.0, 298.99, 309.0, 1000.0, 1049.0, 1099.0]
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
    [89.0, 99.0, 299.0, 509.0, 1000.0, 1099.0]
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
    reales = (almacen.leer_json(almacen.ultimo("comisiones_reales.json"), {}) or {}).get("categorias") or {}
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
def plan_pasos(p0: float, rec: float, tope: float, cada_dias: int, inicio: dt.date,
               prueba: bool = False, psico: bool = True) -> list[dict[str, Any]]:
    """Pasos de p0 a rec, ninguno mayor que `tope` (fracción) respecto al anterior.

    Cada paso intermedio cae en un precio presentable si hay uno dentro del tope;
    si no (p. ej. de $99 a $94 no hay terminación en 9 a ≤5%), en pesos enteros.
    Con ``prueba`` el primer paso es una semana a ±5% para medir antes de seguir.

    >>> [x["precio"] for x in plan_pasos(99.0, 79.0, 0.05, 7, dt.date(2026, 9, 29))]
    [95.0, 91.0, 87.0, 83.0, 79.0]
    >>> [x["precio"] for x in plan_pasos(299.0, 329.0, 0.05, 7, dt.date(2026, 9, 29))]
    [309.0, 319.0, 329.0]
    """
    pasos: list[dict[str, Any]] = []
    if rec is None or p0 is None or p0 <= 0 or abs(rec / p0 - 1) < 1e-9:
        return pasos
    p, fecha = p0, inicio
    baja = rec < p0
    if prueba and abs(rec / p0 - 1) >= PRUEBA_PCT:
        objetivo = p0 * (1 - PRUEBA_PCT) if baja else p0 * (1 + PRUEBA_PCT)
        pt = float(math.ceil(objetivo - 1e-9)) if baja else float(math.floor(objetivo + 1e-9))
        pasos.append({"fecha": fecha.isoformat(), "precio": pt, "tipo": "prueba",
                      "nota": f"{PRUEBA_DIAS} días a {'−' if baja else '+'}{int(PRUEBA_PCT * 100)}% y medir unidades "
                              "antes de seguir (elasticidad de confianza baja)"})
        p, fecha = pt, fecha + dt.timedelta(days=PRUEBA_DIAS)
    for _ in range(MAX_PASOS):
        if abs(rec / p - 1) < 1e-9 or (baja and p <= rec) or (not baja and p >= rec):
            break
        limite = p * (1 - tope) if baja else p * (1 + tope)
        if (baja and rec >= limite - 1e-9) or (not baja and rec <= limite + 1e-9):
            nxt = rec
        else:
            cand = presentable_arriba(limite, psico) if baja else presentable_abajo(limite, psico)
            # El presentable solo sirve si avanza al menos medio tope (de 99 a
            # 98.99 no es un paso).
            avanza = (cand <= p * (1 - tope / 2)) if baja else (cand >= p * (1 + tope / 2))
            if avanza and ((baja and rec < cand < p) or (not baja and p < cand < rec)):
                nxt = cand
            else:
                nxt = float(math.ceil(limite - 1e-9)) if baja else float(math.floor(limite + 1e-9))
                if nxt == p or (baja and nxt <= rec) or (not baja and nxt >= rec):
                    nxt = rec
        pasos.append({"fecha": fecha.isoformat(), "precio": round(nxt, 2)})
        p, fecha = nxt, fecha + dt.timedelta(days=cada_dias)
    return pasos


# ── una publicación ───────────────────────────────────────────────────────────
class _Econ:
    """utilidad(P) de una publicación con la tabla de comisión ya resuelta."""

    def __init__(self, costo: float, categoria: str | None, peso: float, costo_full: float,
                 comisiones: dict, respaldo: dict):
        self.costo, self.peso, self.full = costo, peso, costo_full
        self.cat, self.comisiones, self.respaldo = categoria, comisiones, respaldo
        self.tabla = economia.tabla_comision(categoria, comisiones, respaldo)
        self._memo: dict[float, dict] = {}

    def __call__(self, P: float) -> dict:
        P = round(float(P), 2)
        m = self._memo.get(P)
        if m is None:
            pct, _ = self.tabla.get(economia.tramo(P), (None, None))
            m = economia.utilidad(P, self.costo, self.cat, self.peso, self.full, pct=pct,
                                  comisiones_cache=self.comisiones, respaldo=self.respaldo)
            self._memo[P] = m
        return m

    def fuente_pct(self, P: float) -> str | None:
        return self.tabla.get(economia.tramo(P), (None, None))[1]


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
    if promo and promo.get("fin") and (_ts(promo["fin"]) or ctx["ahora"]) > ctx["ahora"]:
        razones.append("promo_vigente")

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

    # Stock.
    stock_full = _f(uni.get("available_quantity")) if uni else None
    if stock_full is None:
        stock_full = _f(kub.get("stock_full"))
    sin_stock = "out_of_stock" in (uni.get("sub_status") or []) or not stock_full
    stock_odoo = ctx["odoo"].get(sku)

    def U(P: float) -> float:
        return u0 * (P / p_base) ** beta if p_base else u0

    def V(P: float) -> float | None:
        return None if v0 is None else v0 * (P / p_base) ** beta_v

    u_act = U(p0) if p0 else None
    cobertura = None
    if estado == "activa" and stock_full is not None and u_act:
        cobertura = stock_full / u_act if u_act > 0 else None
    if estado == "pausada" and sin_stock:
        razones.append("pausada_sin_stock")
    s = float(par["sacrificio_utilidad_max"])
    if estado == "activa" and stock_full is not None:
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
    if costo is None:
        razones.append("sin_costo")
    if not peso:
        razones.append("sin_peso")
    if costo is not None and costo < 1.0:
        avisos.append("costo_menor_a_1_peso: CBM sospechoso")

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
        "titulo": uni.get("titulo") or kub.get("producto_nombre"), "estado": estado,
        "categoria_id": uni.get("category_id") or kub.get("category_id"),
        "precio_actual": _r(p0), "fuente_precio_actual": fuente_p0,
        "precio_recomendado": None, "cambio_pct": None,
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
                        "n_semanas": el.get("n_semanas"), "confianza": el["confianza"]},
        "stock": {"full": stock_full, "odoo": stock_odoo, "cobertura_dias": _r(cobertura, 1)},
        "costo": {"unitario": _r(costo, 4), "fuente": ce.get("fuente") or "sin_costo"},
        "peso": {"kg": peso, "fuente": fuente_peso},
        "base": {"dias": dias_b, "desde": base.get("desde"), "hasta": base.get("hasta"),
                 "unidades": base.get("unidades"), "visitas": base.get("visitas"), "p_base": base.get("p_base"),
                 "factor_estacional_info": base.get("factor_estacional")},
        "promo": promo, "sacrificio": s, "recomendado_modelo": None,
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

    econ = _Econ(costo, fila["categoria_id"], float(peso), float(ctx["costo_full"]), ctx["comisiones"], ctx["respaldo"])
    e0 = econ(p0)
    if e0["utilidad"] is not None and e0["utilidad"] < 0:
        razones.append("perdiendo_dinero")
    kw = {"comisiones_cache": ctx["comisiones"], "respaldo": ctx["respaldo"]}
    piso_m = float(par["piso_margen"])
    p_eq = economia.precio_equilibrio(costo, fila["categoria_id"], float(peso), float(ctx["costo_full"]), **kw)
    p_piso = economia.precio_para_margen(piso_m, costo, fila["categoria_id"], float(peso), float(ctx["costo_full"]), **kw)

    lo, hi = par["rejilla_min_factor"] * p0, par["rejilla_max_factor"] * p0
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
    pi_max_p = max(evaluados, key=lambda P: pts[P]["utilidad_dia"] if pts[P]["utilidad_dia"] is not None else -1e18)
    pi_max = pts[pi_max_p]["utilidad_dia"]

    def cumple_piso(P: float) -> bool:
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
        """Menor presentable en [desde, hasta] con margen ≥ piso y Π ≥ (1−s)·Πmax,
        donde Πmax es el máximo de la curva EN ESE MISMO TRAMO. Con escasez se
        exige además P ≥ P0 y se toma el máximo (s = 0)."""
        if escasez:
            desde = max(desde, p0)
        dom = [P for P in evaluados if desde - 1e-9 <= P <= hasta + 1e-9 and pts[P]["utilidad_dia"] is not None]
        cands = [P for P in pres if desde - 1e-9 <= P <= hasta + 1e-9 and cumple_piso(P)]
        if escasez and desde - 1e-9 <= p0 <= hasta + 1e-9 and cumple_piso(p0):
            cands.append(round(p0, 2))
        if not dom or not cands:
            return None
        pmax = max(pts[P]["utilidad_dia"] for P in dom)
        if pmax <= 0:
            return None
        if escasez:
            return max(cands, key=lambda P: pts[P]["utilidad_dia"])
        ok = [P for P in cands if pts[P]["utilidad_dia"] >= (1 - s) * pmax - 1e-9]
        return min(ok) if ok else None

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
        else:
            avisos.append(f"costo_sospechoso: el precio piso ({p_piso}) es {p_piso / p0:.1f}× el actual; "
                          f"revisar costo ({ce.get('fuente')})")
    rec_modelo = rec
    piso_manda = any(a.startswith("piso_fuera_de_rejilla") for a in avisos)

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
                    r2 = arriba_con_piso(max(d, p_piso or d))
            rec = r2
    if rec is not None:
        if escasez and rec < p0:
            rec = round(p0, 2)
        if not cumple_piso(rec):
            rec = arriba_con_piso(max(rec, p_piso or rec))
    if rec is not None and rec not in pts:
        pts[rec] = punto(rec)
        if rec > hi:
            avisos.append("recomendado_sobre_rejilla_por_piso")

    fila.update({
        "precio_equilibrio": p_eq, "precio_piso": p_piso,
        "precio_max_utilidad": pi_max_p, "precio_max_volumen": max_vol,
        "recomendado_modelo": rec_modelo, "precio_recomendado": rec,
        "cambio_pct": None if rec is None else round(rec / p0 - 1, 4),
    })
    fila["margen"]["actual"] = e0["margen"]
    fila["utilidad_dia"]["actual"] = None if e0["utilidad"] is None else round(U(p0) * e0["utilidad"], 2)
    fila["utilidad_unit_actual"] = e0["utilidad"]
    fila["comision_pct_actual"] = e0["pct"]
    fila["fuente_comision"] = econ.fuente_pct(p0)
    if rec is not None:
        pr_ = pts[rec]
        fila["unidades_dia"]["recomendado"] = _r(pr_["unidades_dia"], 3)
        fila["visitas_dia"]["recomendado"] = pr_["visitas_dia"]
        fila["conversion"]["recomendado"] = pr_["conversion"]
        fila["utilidad_dia"]["recomendado"] = pr_["utilidad_dia"]
        fila["margen"]["recomendado"] = pr_["margen_pct"]
        fila["utilidad_unit_recomendado"] = pr_["utilidad_unit"]
        lo_p, hi_p = min(p0, rec), max(p0, rec)
        if (lo_p < 299 <= hi_p) or (289 <= rec < 299):
            razones.append("escalon_envio_299")
        if (lo_p < 500 <= hi_p) or rec == 500:
            razones.append("tramo_comision_500")
        # Plan: pausada → se publica directo al reactivar; activa → pasos semanales (y diario aparte).
        prueba = el["confianza"] == "baja"
        if estado == "pausada":
            fila["plan"] = {"modo": "al_reactivar",
                            "pasos": [] if abs(rec / p0 - 1) < 1e-9 else
                            [{"fecha": ctx["manana"].isoformat(), "precio": rec, "tipo": "al_reactivar"}],
                            "nota": "pausada: sin ventas que proteger, el precio se fija al volver a publicar"}
        else:
            fila["plan"] = {
                "modo": "semanal",
                "pasos": plan_pasos(p0, rec, float(par["paso_max_semana"]), 7, ctx["manana"], prueba, psico),
                "diario": {"modo": "diario", "pasos": plan_pasos(p0, rec, float(par["paso_max_dia"]), 1,
                                                                   ctx["manana"], prueba, psico)},
                "prueba": prueba and abs(rec / p0 - 1) >= PRUEBA_PCT,
            }
    marcadores = {"actual": _r(p0), "recomendado": rec, "piso": p_piso, "equilibrio": p_eq,
                  "max_utilidad": pi_max_p, "ref_competencia": _r(ref["precio"])}
    curva_precios = sorted(set(rejilla) | {x for x in (rec, max_vol) if x is not None})
    curva = {"puntos": [pts[P] if P in pts else punto(P) for P in curva_precios], "marcadores": marcadores}
    return fila, curva


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
    ctx = {
        "par": par, "elasticidades": el, "costos": costos, "universo": ins["universo"], "kub": ins["kub"],
        "odoo": ins["odoo"], "comp": ins["comp"], "promos": ins["promos"], "comisiones": ins["comisiones"],
        "respaldo": {"real_orders": ins["reales"], "por_tramo": ml.get("comision_respaldo_por_tramo")},
        "costo_full": float(ml.get("costo_full_unitario_mes") or 0.0),
        "ahora": ahora, "manana": almacen.hoy_cdmx() + dt.timedelta(days=1),
        "dias_base": int(par.get("dias_base", 28)),
    }
    filas, curvas = [], {}
    for pr in ins["precios"]:
        if not pr.get("es_full") or pr.get("cuenta") not in CUENTAS:
            continue
        fila, curva = _optimizar_fila(pr, ctx)
        filas.append(fila)
        if curva:
            curvas[fila["id"]] = curva
    filas.sort(key=lambda f: -(f["unidades_dia"]["actual"] or 0))
    resumen = resumir(filas)
    resumen["duracion_s"] = round(time.monotonic() - t0, 2)
    salida = {"generado_at": almacen.ahora_iso(), "version": "lab-0.1",
              "parametros": {**par, "costo_full_unitario_mes": ctx["costo_full"],
                             "elasticidad_tope_tau2": (el.get("_meta") or {}).get("tope_tau2"),
                             "beta_global": (el.get("_global") or {}).get("beta"),
                             "escalones": list(ESCALONES_ENVIO + ESCALONES_COMISION),
                             "mantener_si_cambio_menor_a": MANTENER_PCT},
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
                               "u_act": 0.0, "u_rec": 0.0, "pi_act": 0.0, "pi_rec": 0.0,
                               "banda_movio": 0, "razones": Counter()})
        d["n"] += 1
        d["razones"].update(f["razones"])
        d["clases"][_clase(f["cambio_pct"])] += 1
        if f["precio_recomendado"] is None:
            continue
        d["con_recomendacion"] += 1
        d["cambios"].append(f["cambio_pct"])
        if f["recomendado_modelo"] is not None and f["recomendado_modelo"] != f["precio_recomendado"]:
            d["banda_movio"] += 1
        ua, ur = f["unidades_dia"]["actual"], f["unidades_dia"]["recomendado"]
        pa, prr = f["utilidad_dia"]["actual"], f["utilidad_dia"]["recomendado"]
        if None not in (ua, ur, pa, prr) and "sin_ventas_en_base" not in " ".join(f["avisos"]):
            d["u_act"] += ua
            d["u_rec"] += ur
            d["pi_act"] += pa
            d["pi_rec"] += prr
    for k, d in por.items():
        cs = sorted(d.pop("cambios"))
        n = len(cs)
        d["cambio_pct"] = ({"p10": cs[n // 10], "p25": cs[n // 4], "mediana": median(cs),
                            "p75": cs[3 * n // 4], "p90": cs[min(n - 1, 9 * n // 10)]} if n else {})
        d["clases"] = dict(d["clases"])
        d["razones"] = dict(d["razones"])
        for x in ("u_act", "u_rec", "pi_act", "pi_rec"):
            d[x] = round(d[x], 2)
        d["delta_unidades_dia"] = round(d["u_rec"] - d["u_act"], 2)
        d["delta_utilidad_dia"] = round(d["pi_rec"] - d["pi_act"], 2)
    out["por_cuenta_estado"] = por
    out["por_confianza"] = dict(Counter(f["elasticidad"]["confianza"] for f in filas))
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
