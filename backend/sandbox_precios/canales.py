"""Amazon, Walmart, Temu y TikTok: utilidad al precio listado y precio de paridad. PURO.

En producción NO existe fórmula de costo para estos canales
(`publicaciones_panel.CANALES_CON_COSTO = ("mercado_libre",)`), y en los pedidos
de 90 días Amazon, Temu y TikTok traen la comisión en 0 (medido el 28-sep). Así
que aquí todo sale de `parametros.canales` y se marca ``supuesto_canal: true``:
es un orden de magnitud para comparar canales, no una liquidación.

    utilidad(P) = P − P×comision − envio_unitario − iva(P) − costo
    margen(P)   = utilidad / P
    precio_paridad = (costo + envío) / (1 − comision − iva/(1+iva) − margen_objetivo)

`margen_objetivo` es el margen que el optimizador le recomienda a la misma SKU
en ML (`precios.json`, `margen.recomendado`); si la SKU no tiene recomendación
de ML se usa el piso (`optimizador.piso_margen`). Así "paridad" significa "lo
que hay que cobrar aquí para ganar lo mismo que ganaríamos en ML", que es la
pregunta que decide a qué canal mandar la mercancía que no rota en FULL.

Trampas de cada canal (informes de `mapa/`):
- **Amazon, Walmart**: publican el `regular_price` de Woo = el precio "tachado"
  de ML (sugerido/0.84). Amazon no registra cambios desde el 18-sep y Walmart es
  una foto del 17-ago: su precio puede estar viejo.
- **Temu**: `basePrice` es lo que Temu NOS PAGA (neto), por eso comisión 0. Hay
  precios absurdos (JUGU-1158-VER a $20,721 con costo de $42): se marcan
  ``precio_sospechoso`` en vez de reportar un margen de 99%.
- **TikTok**: el censo guarda `tax_exclusive_price` cuando existe, que puede ir
  SIN IVA. Con ``precio_incluye_iva: false`` en el parámetro del canal se le
  suma el IVA antes de calcular; por omisión se asume con IVA y se avisa.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Any, Iterable, Mapping

IVA = 0.16
CANALES = ("amazon", "walmart", "temu", "tiktok")
PISO_MARGEN = 0.12
# Detector de precios capturados mal. Medido el 28-sep: precio/costo_panel en
# ML tiene p99 = 43× (costos de centavos en accesorios), así que el costo solo
# delata el error grueso (>100×, JUGU-1158-VER en Temu es 500×). Contra el
# precio de ML de la MISMA SKU es más fino: la mediana es 1.0 en Amazon, Walmart
# y TikTok (1.37 en Temu) y más allá de 5× o debajo de 0.2× hay 32 casos.
FACTOR_SOSPECHOSO_COSTO = 100.0
FACTOR_SOSPECHOSO_ML = (0.2, 5.0)

_AVISO_CANAL = {
    "amazon": "amazon_precio_regular_woo_sin_cambios_desde_18_sep",
    "walmart": "walmart_foto_del_17_ago",
    "temu": "temu_basePrice_es_neto_que_paga_temu",
    # Medido el 28-sep: precio TikTok / precio ML de la misma SKU = 1.00 de
    # mediana en 1,174 publicaciones → en la práctica viene CON IVA.
    "tiktok": "tiktok_precio_puede_venir_sin_iva",
}


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _par_canal(canal: str, parametros_canales: Mapping[str, Any] | None) -> dict[str, Any]:
    p = dict((parametros_canales or {}).get(canal) or {})
    p.setdefault("comision", None)
    p.setdefault("envio_unitario", 0.0)
    p.setdefault("supuesto", True)
    p.setdefault("precio_incluye_iva", True)
    return p


def economia_canal(canal: str, precio: float | None, costo: float | None,
                   parametros_canales: Mapping[str, Any] | None, iva: float = IVA) -> dict[str, Any]:
    """Desglose de una pieza en un canal que no es ML, al precio listado.

    >>> par = {"amazon": {"comision": 0.15, "envio_unitario": 0, "supuesto": True}}
    >>> r = economia_canal("amazon", 348.0, 60.0, par)
    >>> (r["comision"], r["iva"], r["utilidad"], r["margen_pct"])
    (52.2, 48.0, 187.8, 0.5397)
    >>> economia_canal("amazon", 348.0, None, par)["faltan"]
    ['sin_costo']
    """
    p = _par_canal(canal, parametros_canales)
    faltan: list[str] = []
    P = _f(precio)
    if P is not None and not p["precio_incluye_iva"]:
        P = P * (1.0 + iva)
    com_pct = _f(p["comision"])
    if P is None or P <= 0:
        faltan.append("sin_precio")
    if costo is None:
        faltan.append("sin_costo")
    if com_pct is None:
        faltan.append("sin_comision_canal")
    out: dict[str, Any] = {"canal": canal, "precio_calculo": None if P is None else round(P, 2),
                           "comision_pct": com_pct, "comision": None, "envio": _f(p["envio_unitario"]),
                           "iva": None, "utilidad": None, "margen_pct": None, "roi": None,
                           "supuesto_canal": bool(p["supuesto"]), "faltan": faltan}
    if P is not None and P > 0:
        out["iva"] = round(P - P / (1.0 + iva), 2)
        if com_pct is not None:
            out["comision"] = round(P * com_pct, 2)
    if not faltan:
        u = P - P * com_pct - (out["envio"] or 0.0) - (P - P / (1.0 + iva)) - float(costo)
        out["utilidad"] = round(u, 2)
        out["margen_pct"] = round(u / P, 4)
        out["roi"] = round(u / float(costo), 4) if costo else None
    return out


def precio_para_margen_canal(canal: str, objetivo: float, costo: float | None,
                             parametros_canales: Mapping[str, Any] | None,
                             iva: float = IVA) -> float | None:
    """Menor precio (con IVA, al centavo) que da `objetivo` de margen en el canal.

    Sin escalones: en estos canales la comisión es un % plano (supuesto) y el
    envío un monto fijo, así que el margen es creciente en P y la solución es
    cerrada.

    >>> par = {"amazon": {"comision": 0.15, "envio_unitario": 0}}
    >>> precio_para_margen_canal("amazon", 0.12, 60.0, par)
    101.34
    """
    p = _par_canal(canal, parametros_canales)
    com = _f(p["comision"])
    if costo is None or com is None:
        return None
    den = 1.0 - com - iva / (1.0 + iva) - float(objetivo)
    if den <= 0:
        return None
    precio = math.ceil((float(costo) + (_f(p["envio_unitario"]) or 0.0)) / den * 100.0 - 1e-9) / 100.0
    if not p["precio_incluye_iva"]:
        # El canal guarda el precio sin IVA: la paridad se expresa igual que su precio.
        precio = math.ceil(precio / (1.0 + iva) * 100.0 - 1e-9) / 100.0
    return round(precio, 2)


def margen_ref_desde_precios(filas_precios: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """{SKU: {margen, precio, listing_id, cuenta}} con la recomendación de ML.

    Si la SKU tiene varias publicaciones (dos cuentas, o dos en la misma), se
    toma la del margen recomendado MÁS ALTO: la paridad no debe pedirle al otro
    canal menos de lo que la mejor publicación de ML ya gana.
    """
    out: dict[str, dict[str, Any]] = {}
    for r in filas_precios:
        sku = str(r.get("sku") or "").strip().upper()
        m = _f((r.get("margen") or {}).get("recomendado"))
        if not sku or m is None:
            continue
        if sku not in out or m > out[sku]["margen"]:
            out[sku] = {"margen": m, "precio": _f(r.get("precio_recomendado")),
                        "listing_id": r.get("listing_id"), "cuenta": r.get("cuenta")}
    return out


def evaluar(filas: Iterable[Mapping[str, Any]], costos: Mapping[str, Mapping[str, Any]] | None = None,
            parametros: Mapping[str, Any] | None = None,
            margen_ref_por_sku: Mapping[str, Any] | None = None,
            piso: float | None = None, iva: float = IVA,
            precio_ml_por_sku: Mapping[str, float] | None = None) -> list[dict[str, Any]]:
    """Utilidad, margen y precio de paridad de cada publicación fuera de ML.

    ``filas``: ``[{canal, cuenta, listing_id, sku, precio, ...}]`` (lo demás se
    conserva). ``costos``: `ultimo/costos.json`. ``parametros``: `parametros.json`
    completo. ``margen_ref_por_sku``: {SKU: margen} o la salida de
    :func:`margen_ref_desde_precios`. ``precio_ml_por_sku``: {SKU: precio cobrado
    en ML} para detectar precios capturados mal. Devuelve filas NUEVAS con las llaves del
    contrato `Publicacion` (comision_pct, comision, envio, iva, utilidad,
    margen_pct, supuesto_canal, costo) más ``precio_paridad`` y ``paridad``.
    Las filas de ML se devuelven sin tocar (su economía es `economia.py`).
    """
    par = parametros or {}
    par_canales = par.get("canales") or {}
    piso_m = _f(piso) if piso is not None else _f((par.get("optimizador") or {}).get("piso_margen"))
    piso_m = PISO_MARGEN if piso_m is None else piso_m
    refs = margen_ref_por_sku or {}
    salida: list[dict[str, Any]] = []
    for f in filas:
        canal = str(f.get("canal") or "").lower()
        if canal not in CANALES:
            salida.append(dict(f))
            continue
        sku = str(f.get("sku") or "").strip().upper()
        c = (costos or {}).get(sku) or {}
        costo = _f(c.get("unitario"))
        eco = economia_canal(canal, f.get("precio"), costo, par_canales, iva)
        avisos = list(f.get("avisos") or [])
        avisos.append(_AVISO_CANAL[canal])
        # Precio absurdo: se reporta, pero no se le cree el margen.
        pc = eco["precio_calculo"]
        # (sin ningún costo no hay contra qué comparar: solo el precio de ML)
        ref_costo = max(_f(c.get("costo_panel")) or 0.0, costo or 0.0)
        p_ml = _f((precio_ml_por_sku or {}).get(sku))
        if pc and ((ref_costo > 0 and pc > FACTOR_SOSPECHOSO_COSTO * max(ref_costo, 1.0)) or (
                p_ml and not FACTOR_SOSPECHOSO_ML[0] <= pc / p_ml <= FACTOR_SOSPECHOSO_ML[1])):
            avisos.append("precio_sospechoso")
            eco["utilidad"] = eco["margen_pct"] = eco["roi"] = None
            eco["faltan"] = eco["faltan"] + ["precio_sospechoso"]
        ref = refs.get(sku)
        m_ref = _f(ref.get("margen")) if isinstance(ref, Mapping) else _f(ref)
        if m_ref is not None:
            objetivo, fuente = max(m_ref, piso_m), "ml_recomendado"
        else:
            objetivo, fuente = piso_m, "piso_canal"
        paridad = precio_para_margen_canal(canal, objetivo, costo, par_canales, iva)
        P = _f(f.get("precio"))
        fila = dict(f)
        fila.update({
            "comision_pct": eco["comision_pct"], "comision": eco["comision"], "envio": eco["envio"],
            "iva": eco["iva"], "utilidad": eco["utilidad"], "margen_pct": eco["margen_pct"],
            "roi": eco["roi"], "supuesto_canal": eco["supuesto_canal"], "faltan": eco["faltan"],
            "costo": {k: c.get(k) for k in ("unitario", "fuente", "contenedor", "validado",
                                            "revisado_por", "costo_panel")} if c else
                     {"unitario": None, "fuente": "sin_costo"},
            "precio_paridad": paridad,
            "paridad": {"margen_objetivo": round(objetivo, 4), "fuente": fuente,
                        "margen_ml": m_ref,
                        "precio_ml_recomendado": (ref or {}).get("precio") if isinstance(ref, Mapping) else None,
                        "diferencia_pct": round(P / paridad - 1.0, 4) if (P and paridad) else None},
            "avisos": avisos,
        })
        salida.append(fila)
    return salida


def filas_desde_kubera(publicaciones: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Filas de `crudo/<día>/kubera_publicaciones.json` de los 4 canales → entrada
    de :func:`evaluar`. El precio es `price` (en estos canales `price_sale` y
    `price_base` vienen vacíos)."""
    out = []
    for f in publicaciones:
        canal = str(f.get("canal") or "").lower()
        if canal not in CANALES:
            continue
        out.append({"canal": canal, "cuenta": f.get("cuenta"), "listing_id": f.get("listing_id"),
                    "sku": f.get("sku"), "precio": _f(f.get("price")),
                    "status": f.get("status"), "situacion": f.get("situacion"),
                    "stock_propio": _f(f.get("stock_own")), "stock_fba": _f(f.get("stock_fba")),
                    "logistica": f.get("logistic_type"), "frescura_at": f.get("updated_at")})
    return out


def precio_ml_por_sku(universo: Iterable[Mapping[str, Any]],
                      precios: Iterable[Mapping[str, Any]] = ()) -> dict[str, float]:
    """{SKU: mediana del precio cobrado en ML} con `ml_universo` + `ml_precios`
    (precio cobrado donde se midió; `price` del item en las demás)."""
    cobrado = {p.get("id"): _f(p.get("precio_cobrado")) for p in precios}
    xs: dict[str, list[float]] = {}
    for u in universo:
        sku = str(u.get("sku") or "").strip().upper()
        p = cobrado.get(u.get("id")) or _f(u.get("price"))
        if sku and p:
            xs.setdefault(sku, []).append(p)
    return {s: median(v) for s, v in xs.items()}


def resumen(evaluadas: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Conteos por canal: con margen, negativas, bajo el piso, mediana."""
    por: dict[str, dict[str, Any]] = {}
    for f in evaluadas:
        canal = f.get("canal")
        if canal not in CANALES:
            continue
        d = por.setdefault(canal, {"publicaciones": 0, "con_margen": 0, "negativas": 0,
                                   "sospechosas": 0, "sin_costo": 0, "_m": [], "_dif": []})
        d["publicaciones"] += 1
        d["sospechosas"] += "precio_sospechoso" in (f.get("avisos") or [])
        d["sin_costo"] += "sin_costo" in (f.get("faltan") or [])
        if f.get("margen_pct") is not None:
            d["con_margen"] += 1
            d["negativas"] += f["margen_pct"] < 0
            d["_m"].append(f["margen_pct"])
        if (f.get("paridad") or {}).get("diferencia_pct") is not None and "precio_sospechoso" not in (f.get("avisos") or []):
            d["_dif"].append(f["paridad"]["diferencia_pct"])
    for d in por.values():
        m, dif = d.pop("_m"), d.pop("_dif")
        d["margen_mediano"] = round(median(m), 4) if m else None
        d["precio_vs_paridad_mediano"] = round(median(dif), 4) if dif else None
        d["bajo_paridad"] = sum(1 for x in dif if x < 0)
    return por


if __name__ == "__main__":
    import doctest

    fallas, pruebas = doctest.testmod()
    print(f"canales: {pruebas - fallas}/{pruebas} doctests OK")
