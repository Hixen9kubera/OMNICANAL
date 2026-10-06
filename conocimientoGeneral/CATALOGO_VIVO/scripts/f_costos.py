"""
f_costos.py — El COSTO de cada producto, de las dos maneras que pidió Brandon.

1. EL COSTO DE LA BASE (el oficial). `costing.costos_validados` de kubera es donde
   viven todos los productos con su costo: `costo_producto` (la mercancía),
   `costo_cbm` (el flete, a 7,500 $/m³) y `costo_total` (la suma). Es el número que
   usa el panel y el que Brandon definió para valuar el catálogo (6-oct-2026).
   Se lee con UN `SELECT` — nada más.

2. EL PRORRATEO DEL CONTENEDOR (lo que costaría). Cada contenedor cuesta 525,000
   MXN y se reparte entre sus piezas por volumen:

       cbm_pieza = costo_cbm / 7500                 (se deshace la tarifa)
       m3        = Σ cajas × piezas_por_caja × cbm_pieza      por contenedor
       costo     = 525000 × cbm_pieza / m3          si 50 ≤ m3 ≤ 80
                 = cbm_pieza × 7500                 si no (contenedor incompleto)

   Es la fórmula del laboratorio de precios (`sandbox_precios/prorrateo.py`,
   rama `sandbox/precios-optimos`, 28-sep-2026), copiada aquí tal cual: la regla de
   esta carpeta es copiar, no importar.

REGLA 13 DE LA CASA: el DSN apunta a un pooler que COMPARTE conexiones. Aquí no se
marca la sesión de solo lectura ni se toca ningún ajuste de sesión: una consulta,
se cierra y ya.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from comun import Cfg, ahora_iso, aviso, escribir_json, num, sku_norm

COSTO_CONTENEDOR = 525_000.0
TARIFA_M3 = 7_500.0
RANGO_M3 = (50.0, 80.0)
# Una sola pieza de más de 1.5 m³ no cabe en este catálogo: es un cartón guardado
# como pieza. El número se muestra, pero marcado, y no entra a los totales.
CBM_PIEZA_INVEROSIMIL = 1.5

_CONSULTA = """
select sku::text as sku, contenedor, costo_producto, costo_cbm, costo_total,
       cajas, piezas_por_caja, peso, largo, ancho, alto, currency, fx_rate_used,
       revisado_at, revisado_por, updated_at
  from costing.costos_validados
"""


def _leer_base(cfg: Cfg) -> list[dict[str, Any]]:
    import psycopg2
    import psycopg2.extras

    dsn = cfg("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError("falta SUPABASE_DB_URL (la base kubera)")
    cn = psycopg2.connect(dsn, connect_timeout=25)
    try:
        cn.autocommit = True
        with cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_CONSULTA)
            return [dict(f) for f in cur.fetchall()]
    finally:
        cn.close()


def prorratear(filas: list[dict[str, Any]]) -> tuple[dict[str, dict], dict[str, dict]]:
    """Copia de `prorrateo_kubera` del laboratorio. Pura: no toca red ni disco."""
    por_cont: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skus: dict[str, dict[str, Any]] = {}
    for f in filas:
        sku = sku_norm(f.get("sku"))
        if not sku:
            continue
        ccbm = num(f.get("costo_cbm"))
        cbm_pz = ccbm / TARIFA_M3 if ccbm and ccbm > 0 else None
        cont = str(f.get("contenedor") or "").strip() or None
        cajas, ppc = num(f.get("cajas")), num(f.get("piezas_por_caja"))
        piezas = cajas * ppc if (cajas and ppc and cajas > 0 and ppc > 0) else None
        reg = {"cbm_pieza": cbm_pz, "piezas_embarque": piezas, "costo": None, "fuente": None,
               "marca": None, "m3_contenedor": None, "costo_m3": None, "contenedor": cont}
        skus[sku] = reg
        if cont:
            por_cont[cont].append(reg)

    conts: dict[str, dict[str, Any]] = {}
    for cont, regs in por_cont.items():
        m3 = sum((r["cbm_pieza"] or 0.0) * (r["piezas_embarque"] or 0.0) for r in regs)
        ok = RANGO_M3[0] <= m3 <= RANGO_M3[1]
        conts[cont] = {"m3": round(m3, 3), "skus": len(regs), "rango_ok": ok,
                       "sin_unidades_o_cbm": sum(1 for r in regs
                                                 if not r["piezas_embarque"] or not r["cbm_pieza"]),
                       "costo_m3": round(COSTO_CONTENEDOR / m3, 2) if (ok and m3) else None}
        for r in regs:
            r["m3_contenedor"] = round(m3, 3)
            if not r["cbm_pieza"]:
                r["marca"] = "sin_cbm"
            elif ok:
                r["costo"] = COSTO_CONTENEDOR * r["cbm_pieza"] / m3
                r["fuente"], r["costo_m3"] = "prorrateo_525k", conts[cont]["costo_m3"]
            else:
                r["costo"] = r["cbm_pieza"] * TARIFA_M3
                r["fuente"], r["marca"] = "tarifa_7500", "contenedor_fuera_de_rango"
    for r in skus.values():
        if r["contenedor"] is None:
            if r["cbm_pieza"]:
                r["costo"] = r["cbm_pieza"] * TARIFA_M3
                r["fuente"], r["marca"] = "tarifa_7500", "sin_contenedor"
            else:
                r["marca"] = "sin_cbm"
    return skus, conts


def extraer(cfg: Cfg, salida: Path) -> dict[str, Any]:
    leido = ahora_iso()
    base = _leer_base(cfg)
    pror, conts = prorratear(base)
    filas: dict[str, dict[str, Any]] = {}
    for f in base:
        sku = sku_norm(f.get("sku"))
        if not sku:
            continue
        p = pror.get(sku) or {}
        cbm = p.get("cbm_pieza")
        inverosimil = bool(cbm and cbm > CBM_PIEZA_INVEROSIMIL)
        filas[sku] = {
            "producto": num(f.get("costo_producto")),
            "flete": num(f.get("costo_cbm")),
            "total": num(f.get("costo_total")),
            "moneda": f.get("currency") or "MXN", "tc": num(f.get("fx_rate_used")),
            "contenedor": p.get("contenedor"),
            "cajas": num(f.get("cajas")), "piezas_caja": num(f.get("piezas_por_caja")),
            "peso": num(f.get("peso")),
            "validado": bool(f.get("revisado_at")),
            "validado_por": f.get("revisado_por"),
            "validado_at": str(f.get("revisado_at")) if f.get("revisado_at") else None,
            "actualizado": str(f.get("updated_at")) if f.get("updated_at") else None,
            "cbm_pieza": round(cbm, 8) if cbm else None,
            "prorrateo": round(p["costo"], 4) if p.get("costo") is not None else None,
            "prorrateo_fuente": p.get("fuente"), "prorrateo_marca": p.get("marca"),
            "m3_contenedor": p.get("m3_contenedor"), "costo_m3": p.get("costo_m3"),
            "inverosimil": inverosimil,
        }
    por_fuente: dict[str, int] = defaultdict(int)
    for f in filas.values():
        por_fuente[f["prorrateo_fuente"] or "sin_prorrateo"] += 1
    doc = {
        "fuente": "kubera · costing.costos_validados (SELECT)", "leido_at": leido,
        "skus": len(filas),
        "con_costo_producto": sum(1 for f in filas.values() if f["producto"]),
        "con_costo_total": sum(1 for f in filas.values() if f["total"]),
        "validados": sum(1 for f in filas.values() if f["validado"]),
        "prorrateo_por_fuente": dict(por_fuente),
        "inverosimiles": sum(1 for f in filas.values() if f["inverosimil"]),
        "contenedores": len(conts),
        "contenedores_en_rango": sum(1 for c in conts.values() if c["rango_ok"]),
        "parametros": {"costo_contenedor": COSTO_CONTENEDOR, "tarifa_m3": TARIFA_M3,
                       "rango_m3": list(RANGO_M3)},
        "detalle_contenedores": conts, "filas": filas,
    }
    escribir_json(salida / "datos" / "costos.json", doc)
    aviso(f"costos: {len(filas)} SKUs en la base · {doc['con_costo_producto']} con costo de "
          f"producto · {doc['validados']} validados · prorrateo {dict(por_fuente)}")
    return {k: v for k, v in doc.items() if k not in ("filas", "detalle_contenedores")}
