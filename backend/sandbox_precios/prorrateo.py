"""Prorrateo del contenedor (525,000 MXN) entre sus piezas: funciones PURAS.

Brandon (DISENO.md §3): el costo del producto del packing list NO aplica; lo que
cuesta es el CONTENEDOR, 525,000 MXN sin IVA (`lib/margen.ts:100`). La pregunta
es cómo repartirlo entre las piezas, y cada método le carga el costo a un
producto distinto. Aquí se calculan los cinco lado a lado para poder elegir con
datos, no por costumbre:

    volumetrico_real   525000 × cbm_pieza / Σ(cbm_pieza × piezas)      ← recomendado
    tarifa_fija_7500   cbm_pieza × 7500                                 (lo que usa el panel)
    peso_volumen_wm    525000 × max(cbm, t) / Σ(max(cbm, t) × piezas)  (tonelada-flete W/M)
    valor_fob          525000 × usd_pieza / Σ(usd × piezas)            (solo informativo)
    hibrido_70_30      0.7·volumétrico + 0.3·valor_fob

Nada de aquí toca la red, el disco ni la base: recibe renglones ya leídos y
devuelve números. Así se puede probar a mano y se reusa igual para UN contenedor
del packing list (`prorratear`) que para TODO el catálogo reconstruido desde
`costos_validados` (`prorrateo_kubera`).

Dos reglas de la casa que viven aquí:
- Un dato ausente es ``None``, nunca 0 inventado (DISENO §0.7). Un renglón sin
  CBM no "cuesta cero": se cuenta en ``renglones_sin_cbm`` y se avisa, porque
  su volumen falta en el denominador y encarece un poco a todos los demás.
- El denominador es el contenedor COMPLETO. Prorratear 525k entre los SKUs que
  uno eligió (en vez de entre todos los renglones del archivo) es el error que
  multiplica el costo: 100 SKUs de 1,000 renglones cargarían con todo el
  contenedor.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

COSTO_CONTENEDOR = 525_000.0
TARIFA_FIJA_M3 = 7_500.0
# Rango de un 40' HC lleno, medido: los 4 packing lists de la muestra dieron
# 69.2–72.5 m³ y la tarifa de 7,500 equivale a 70 m³. Fuera de ese rango el
# archivo suele ser un embarque parcial o consolidado (los SZLS…) y repartir los
# 525k entre pocos m³ encarece todo sin que el contenedor haya costado más.
RANGO_M3_PACKING = (55.0, 76.0)
# Para el catálogo reconstruido desde kubera el rango es más ancho a propósito:
# ahí el m³ se RECONSTRUYE (Σ cajas × piezas_por_caja × costo_cbm/7500) con
# redondeos y SKUs faltantes. Medido el 28-sep: 63 de 84 contenedores caen en
# 50–80 m³; con 55–76 se perdían casos sanos solo por el ruido de la reconstrucción.
RANGO_M3_KUBERA = (50.0, 80.0)
# Carga útil de un 40' HC (parametros.json). Un contenedor de 70 m³ se llena
# antes por PESO si su densidad media pasa de ~379 kg/m³ (26,500/70).
CARGA_UTIL_KG = 26_500.0
# Cobertura mínima del valor USD (fracción del CBM del contenedor con precio)
# para que valor_fob e híbrido signifiquen algo. Con 40% de renglones sin USD,
# repartir 525k solo entre los que traen precio le regala el flete a los demás.
MIN_COBERTURA_USD = 0.90
# Tonelada-flete W/M: 1 t "cuesta" como 1 m³ (convención de carga consolidada,
# LCL). Para un 40' HC que se llena por PESO la razón real es carga útil /
# volumen ≈ 26,500/70 ≈ 379 kg/m³; se deja como parámetro para medir ese caso.
KG_POR_M3_WM = 1000.0

METODOS = ("volumetrico_real", "tarifa_fija_7500", "peso_volumen_wm", "valor_fob", "hibrido_70_30")


def _f(v: Any) -> float | None:
    """Número o None. Acepta Decimal/str; lo no numérico o negativo es None."""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x < 0:          # NaN o negativo: dato roto, no un costo
        return None
    return x


def _r(v: float | None, dec: int = 4) -> float | None:
    return None if v is None else round(v, dec)


def totales(renglones: Iterable[dict[str, Any]], *,
            costo_contenedor: float = COSTO_CONTENEDOR,
            rango_m3: tuple[float, float] = RANGO_M3_PACKING,
            carga_util_kg: float = CARGA_UTIL_KG,
            kg_por_m3: float = KG_POR_M3_WM) -> dict[str, Any]:
    """Totales de UN contenedor: CBM, piezas, peso, USD, $/m³ y banderas.

    `limitado_por_peso` responde "¿el contenedor se llenó por kilos antes que
    por volumen?": pasa si el peso total supera el 90% de la carga útil. Es la
    condición en la que el reparto volumétrico deja de ser justo, porque los
    productos densos consumieron la capacidad real y los ligeros pagan por ellos.
    `filas_densas` cuenta los renglones cuya tonelada pesa más que su m³ (W/M):
    son los que el método de peso-volumen encarece.
    """
    rs = list(renglones)
    cbm = piezas = peso = usd = wm = cbm_con_usd = cbm_con_peso = 0.0
    sin_cbm = sin_piezas = sin_peso = sin_usd = densas = 0
    for r in rs:
        c, p = _f(r.get("cbm_pieza")), _f(r.get("piezas"))
        kg, u = _f(r.get("peso_pieza_kg")), _f(r.get("usd_pieza"))
        if not p:
            sin_piezas += 1
            continue
        if not c:
            sin_cbm += 1
        c = c or 0.0
        cbm += c * p
        piezas += p
        if kg:
            peso += kg * p
            cbm_con_peso += c * p
            if kg / kg_por_m3 > c:
                densas += 1
        else:
            sin_peso += 1
        wm += max(c, (kg or 0.0) / kg_por_m3) * p
        if u:
            usd += u * p
            cbm_con_usd += c * p
        else:
            sin_usd += 1
    rango_ok = bool(cbm) and rango_m3[0] <= cbm <= rango_m3[1]
    return {
        "renglones": len(rs),
        "renglones_sin_cbm": sin_cbm,
        "renglones_sin_piezas": sin_piezas,
        "renglones_sin_peso": sin_peso,
        "renglones_sin_usd": sin_usd,
        "total_cbm": _r(cbm, 4),
        "total_piezas": _r(piezas, 2),
        "total_peso_kg": _r(peso, 2) if peso else None,
        "total_usd": _r(usd, 2) if usd else None,
        "total_wm": _r(wm, 4),
        "costo_m3": _r(costo_contenedor / cbm, 2) if cbm else None,
        "costo_m3_wm": _r(costo_contenedor / wm, 2) if wm else None,
        "rango_m3": list(rango_m3),
        "rango_ok": rango_ok,
        "cobertura_usd": _r(cbm_con_usd / cbm, 4) if cbm else None,
        "cobertura_peso": _r(cbm_con_peso / cbm, 4) if cbm else None,
        "densidad_kg_m3": _r(peso / cbm_con_peso, 1) if cbm_con_peso else None,
        "filas_densas": densas,
        "limitado_por_peso": bool(peso) and peso >= 0.9 * carga_util_kg,
        "peso_vs_carga_util": _r(peso / carga_util_kg, 4) if peso else None,
        # Denominadores SIN redondear: con el total redondeado a 4 decimales el
        # invariante se desviaba 0.13 MXN en un contenedor de 8 renglones.
        "_crudos": {"cbm": cbm, "wm": wm, "usd": usd},
    }


def costos_pieza(renglon: dict[str, Any], tot: dict[str, Any], *,
                 costo_contenedor: float = COSTO_CONTENEDOR,
                 tarifa_m3: float = TARIFA_FIJA_M3,
                 min_cobertura_usd: float = MIN_COBERTURA_USD,
                 kg_por_m3: float = KG_POR_M3_WM) -> dict[str, float | None]:
    """Costo por pieza de UN renglón con los cinco métodos, sin redondear.

    ``tot`` es la salida de :func:`totales` del MISMO contenedor. Devuelve
    ``None`` en el método que no aplica (sin CBM, sin USD, cobertura baja), nunca
    un cero que parezca costo.
    """
    c, kg, u = _f(renglon.get("cbm_pieza")), _f(renglon.get("peso_pieza_kg")), _f(renglon.get("usd_pieza"))
    crudos = tot.get("_crudos") or {}
    cbm_tot = _f(crudos.get("cbm", tot.get("total_cbm")))
    wm_tot = _f(crudos.get("wm", tot.get("total_wm")))
    usd_tot = _f(crudos.get("usd", tot.get("total_usd")))
    cob = _f(tot.get("cobertura_usd")) or 0.0

    vol = costo_contenedor * c / cbm_tot if (c and cbm_tot) else None
    fija = c * tarifa_m3 if c else None
    wm = (costo_contenedor * max(c or 0.0, (kg or 0.0) / kg_por_m3) / wm_tot
          if (wm_tot and (c or kg)) else None)
    fob = (costo_contenedor * u / usd_tot
           if (u and usd_tot and cob >= min_cobertura_usd) else None)
    hib = 0.7 * vol + 0.3 * fob if (vol is not None and fob is not None) else None
    return {"volumetrico_real": vol, "tarifa_fija_7500": fija, "peso_volumen_wm": wm,
            "valor_fob": fob, "hibrido_70_30": hib}


def prorratear(renglones: list[dict[str, Any]], *,
               costo_contenedor: float = COSTO_CONTENEDOR,
               tarifa_m3: float = TARIFA_FIJA_M3,
               rango_m3: tuple[float, float] = RANGO_M3_PACKING,
               carga_util_kg: float = CARGA_UTIL_KG,
               min_cobertura_usd: float = MIN_COBERTURA_USD,
               kg_por_m3: float = KG_POR_M3_WM) -> dict[str, Any]:
    """UN contenedor → costo por pieza de cada renglón con los 5 métodos + totales.

    ``renglones``: TODOS los del packing list, ``[{sku|None, cbm_pieza, piezas,
    peso_pieza_kg, usd_pieza}]`` (``piezas`` = piezas TOTALES del renglón). El
    ``sku`` solo se arrastra para identificar la salida; el reparto no lo usa.

    Devuelve ``{"totales": {...}, "renglones": [{...renglón, "costos": {metodo: MXN}}],
    "invariante": verificar_invariante(...)}``. Los costos van redondeados a 4
    decimales en la salida; el invariante se mide sin redondear.
    """
    tot = totales(renglones, costo_contenedor=costo_contenedor, rango_m3=rango_m3,
                  carga_util_kg=carga_util_kg, kg_por_m3=kg_por_m3)
    salida, crudos = [], []
    for r in renglones:
        cs = costos_pieza(r, tot, costo_contenedor=costo_contenedor, tarifa_m3=tarifa_m3,
                          min_cobertura_usd=min_cobertura_usd, kg_por_m3=kg_por_m3)
        crudos.append(cs)
        salida.append({**r, "costos": {k: _r(v, 4) for k, v in cs.items()}})
    return {"totales": tot, "renglones": salida,
            "invariante": verificar_invariante(renglones, crudos, costo_contenedor=costo_contenedor)}


def verificar_invariante(renglones: list[dict[str, Any]], costos: list[dict[str, float | None]], *,
                         costo_contenedor: float = COSTO_CONTENEDOR,
                         tolerancia_rel: float = 1e-6) -> dict[str, Any]:
    """Σ(costo_pieza × piezas) == 525,000 en `volumetrico_real` y `peso_volumen_wm`.

    Es la prueba de que el reparto no crea ni pierde dinero: si el total no
    cuadra, algún renglón quedó fuera del denominador o entró dos veces (el bug
    del Resolver clásico sumaba `piezas` y perdía `unidades_totales`, y el flete
    salía 2.6× más caro). Se mide sobre los costos SIN redondear; con redondeo a
    centavos la diferencia esperada es ≤ Σpiezas × 0.005 y se reporta aparte.
    """
    out: dict[str, Any] = {}
    for m in ("volumetrico_real", "peso_volumen_wm"):
        s = sum((c.get(m) or 0.0) * (_f(r.get("piezas")) or 0.0) for r, c in zip(renglones, costos))
        s_red = sum(round(c.get(m) or 0.0, 2) * (_f(r.get("piezas")) or 0.0)
                    for r, c in zip(renglones, costos))
        out[m] = {"suma": round(s, 4), "diferencia": round(s - costo_contenedor, 4),
                  "ok": abs(s - costo_contenedor) <= costo_contenedor * tolerancia_rel,
                  "suma_redondeada_centavos": round(s_red, 2)}
    out["ok"] = all(v["ok"] for v in out.values() if isinstance(v, dict))
    return out


# ── Catálogo completo desde costos_validados ─────────────────────────────────
def prorrateo_kubera(filas: Iterable[dict[str, Any]], *,
                     costo_contenedor: float = COSTO_CONTENEDOR,
                     tarifa_m3: float = TARIFA_FIJA_M3,
                     rango_m3: tuple[float, float] = RANGO_M3_KUBERA) -> dict[str, Any]:
    """Costo de contenedor para TODO el catálogo, sin abrir un solo packing list.

    ``filas``: renglones de ``costing.costos_validados`` con ``sku, contenedor,
    costo_cbm, cajas, piezas_por_caja``. El CBM por pieza se DESHACE de la
    tarifa con la que se calculó (``costo_cbm = cbm_pieza × 7500``, fórmula de
    Brandon del 21-ago) y el m³ del contenedor se reconstruye sumando sus SKUs:

        cbm_pieza = costo_cbm / 7500
        m3        = Σ cajas × piezas_por_caja × cbm_pieza      (por `contenedor`)
        costo     = 525000 × cbm_pieza / m3                    si m3 ∈ rango
                  = cbm_pieza × 7500 (= costo_cbm)             si no

    Fuera de rango se marca ``contenedor_fuera_de_rango`` y se cae a la tarifa:
    un m³ reconstruido de 12 o de 300 dice que faltan SKUs o que hay unidades
    mal capturadas, y repartir 525k entre esos m³ inventaría un costo. Medido el
    28-sep: 63 de 84 contenedores caen en 50–80 m³; con las DIMENSIONES en vez
    de costo_cbm salían hasta 29,808 m³, por eso no se usan.

    Devuelve ``{"skus": {SKU: {...}}, "contenedores": {contenedor: {...}}}``.
    """
    por_cont: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skus: dict[str, dict[str, Any]] = {}
    for f in filas:
        sku = str(f.get("sku") or "").strip().upper()
        if not sku:
            continue
        ccbm = _f(f.get("costo_cbm"))
        cbm_pz = ccbm / tarifa_m3 if ccbm else None
        cont = str(f.get("contenedor") or "").strip()
        cajas, ppc = _f(f.get("cajas")), _f(f.get("piezas_por_caja"))
        piezas = cajas * ppc if (cajas and ppc) else None
        reg = {"sku": sku, "contenedor": cont or None, "cbm_pieza": _r(cbm_pz, 8),
               "piezas_embarque": _r(piezas, 2), "costo": None, "fuente": None,
               "marca": None, "m3_contenedor": None, "costo_m3": None}
        skus[sku] = reg
        if cont:
            por_cont[cont].append(reg)

    conts: dict[str, dict[str, Any]] = {}
    for cont, regs in por_cont.items():
        m3 = sum((r["cbm_pieza"] or 0.0) * (r["piezas_embarque"] or 0.0) for r in regs)
        faltan = sum(1 for r in regs if not r["piezas_embarque"] or not r["cbm_pieza"])
        ok = rango_m3[0] <= m3 <= rango_m3[1]
        conts[cont] = {"contenedor": cont, "m3": round(m3, 4), "skus": len(regs),
                       "skus_sin_unidades_o_cbm": faltan, "rango_ok": ok,
                       "costo_m3": round(costo_contenedor / m3, 2) if (ok and m3) else None}
        for r in regs:
            r["m3_contenedor"] = round(m3, 4)
            if not r["cbm_pieza"]:
                r["marca"] = "sin_cbm"
                continue
            if ok:
                r["costo"] = round(costo_contenedor * r["cbm_pieza"] / m3, 4)
                r["fuente"], r["costo_m3"] = "prorrateo_kubera", conts[cont]["costo_m3"]
            else:
                r["costo"] = round(r["cbm_pieza"] * tarifa_m3, 4)
                r["fuente"], r["marca"] = "tarifa_7500", "contenedor_fuera_de_rango"

    for r in skus.values():
        if r["contenedor"] is None:
            if r["cbm_pieza"]:
                r["costo"] = round(r["cbm_pieza"] * tarifa_m3, 4)
                r["fuente"], r["marca"] = "tarifa_7500", "sin_contenedor"
            else:
                r["marca"] = "sin_cbm"
    return {"skus": skus, "contenedores": conts}
