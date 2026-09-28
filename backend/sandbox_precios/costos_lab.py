"""Costo aterrizado y peso facturable por SKU para TODO el catálogo → `ultimo/costos.json`.

Contrato (DISENO §2 y §6): ``{SKU: {unitario, fuente, contenedor, m3_contenedor,
costo_m3, validado, revisado_por, revisado_at, costo_panel, costo_producto_excluido,
peso_kg, peso_fuente, dims, ...}}``. `unitario` es el costo SIN IVA de una pieza
con el modelo de Brandon: lo que cuesta es el CONTENEDOR (525,000 MXN), no el
precio USD del packing list. Prioridad de la fuente:

    packing_list_exacto  renglón del packing list (ultimo/packing100.json)
    prorrateo_kubera     525k × cbm_pieza / m³ del contenedor, reconstruido desde
                         costos_validados (prorrateo.prorrateo_kubera)
    tarifa_7500          cbm_pieza × 7,500 (contenedor fuera de rango o sin contenedor)
    sin_costo            no hay CBM: no se inventa

`costo_panel` es `costos_validados.costo_total` (producto USD×19 + flete) — lo que
usa hoy el panel — y `costo_producto_excluido` la parte USD que este modelo quita.

Dos ajustes sobre el contrato, cada uno con su `marca` para que se vean:
- ``m3_del_packing_list``: el m³ de kubera del contenedor sale fuera de 50–80
  (21 de 84: faltan SKUs o hay unidades mal capturadas) pero su packing list
  está indexado y sano → se prorratea contra el m³ del archivo (297 SKUs el 28-sep)
  en vez de caer a la tarifa de 7,500.
- ``derivado_de_variantes``: SKU publicado sin fila propia en costos_validados
  (padre "aplastado": ML publica 100% plano) → toma el costo MÁS ALTO de sus
  variantes (508 SKUs). Del lado seguro: no promete un margen que una variante no da.

Peso facturable por PUBLICACIÓN (una misma SKU tiene pesos distintos en sus dos
cuentas: 28 de 449 SKUs activos FULL, medido el 28-sep), en este orden:

    ml_billable      `shipping_options/free.billable_weight` (lo que ML cobra)
    ml_atributos     max(PACKAGE_WEIGHT, L×A×H/5000) de los atributos del item
    costos_validados costos._peso_efectivo(peso, l, a, h) — con peso > 0
    None             `sin_peso`: no se recomienda precio

DESVÍO de DISENO §4, medido contra 485 publicaciones con billable_weight: los
atributos PACKAGE_* de ML dan la MISMA tarifa en 410 (85%) y quedan dentro de
±20% en 426; `costos_validados` da la misma tarifa solo en 110 de 340 (32%) y su
p90 es 37× el peso real (las dimensiones son el CBM reconstruido, no medidas).
Por eso los atributos de ML van antes y `costos_validados` sale con
``peso_confianza: "baja"``.

Además valida el modelo contra lo que ML cobró de verdad (`ultimo/costos_resumen.json`):
(a) comisión predicha vs `order_items.comision` de 60 días, (b) envío predicho vs
`enrich.order_shipping_cost`, (c) cobertura de costo de las activas FULL, y el
cruce de márgenes con el costo del panel vs el de 525k.

Datos: los crudos del día (`almacen.ultimo_crudo`) y UNA lectura propia de
`costing.costos_validados` completa (solo SELECT), porque el m³ de un contenedor
se reconstruye con TODOS sus SKUs, no solo con los publicados.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
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
from sandbox_precios import almacen, economia  # noqa: E402

log = logging.getLogger("laboratorio.costos_lab")

DIAS_VALIDACION = 60
# Una categoría/tramo necesita al menos estas líneas reales para servir de respaldo.
MIN_LINEAS_TASA_REAL = 5

_SQL_VALIDADOS = """
select sku::text as sku, contenedor, costo_producto, costo_cbm, costo_total,
       cajas, piezas_por_caja, peso, largo, ancho, alto,
       revisado_at, revisado_por, updated_at
  from costing.costos_validados
"""


# ── utilidades ────────────────────────────────────────────────────────────────
def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _r(v: float | None, d: int = 2) -> float | None:
    return None if v is None else round(v, d)


def _sku(v: Any) -> str:
    return str(v or "").strip().upper()


def _parametros() -> dict:
    try:
        return json.loads(Path(__file__).with_name("parametros.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _q(xs: list[float], d: int = 4) -> dict[str, Any]:
    """Resumen de una distribución: n, p10, mediana, p90."""
    xs = sorted(x for x in xs if x is not None)
    n = len(xs)
    if not n:
        return {"n": 0}
    return {"n": n, "p10": round(xs[n // 10], d), "mediana": round(median(xs), d),
            "p90": round(xs[min(n - 1, 9 * n // 10)], d)}


# ── lecturas ─────────────────────────────────────────────────────────────────
def _crudo(nombre: str) -> Any:
    p = almacen.ultimo_crudo(nombre)
    if p is None:
        return None
    return almacen.leer_jsonl(p) if nombre.endswith(".jsonl") else almacen.leer_json(p)


def leer_validados(dia: dt.date | None = None, refrescar: bool = False) -> tuple[list[dict], str]:
    """`costing.costos_validados` completa (9,618 filas el 28-sep), con caché del día.

    Solo SELECT (el candado de `supabase_db.fetch_all` revienta cualquier otra
    cosa). Se guarda en `crudo/<día>/costos_lab_validados.json` para que una
    segunda corrida del mismo día no vuelva a la base.
    """
    destino = almacen.crudo("costos_lab_validados.json", dia)
    if destino.exists() and not refrescar:
        d = almacen.leer_json(destino) or {}
        return d.get("filas") or [], f"cache:{d.get('generado_at')}"
    from services import supabase_db as sdb

    t0 = time.monotonic()
    filas = sdb.fetch_all(_SQL_VALIDADOS)
    almacen.escribir_json(destino, {"generado_at": almacen.ahora_iso(), "fuente": "kubera",
                                    "consulta": "costos_validados",
                                    "meta": {"filas": len(filas),
                                             "duracion_s": round(time.monotonic() - t0, 2)},
                                    "filas": filas})
    return filas, "kubera_vivo"


# ── peso facturable ──────────────────────────────────────────────────────────
_NUM = re.compile(r"\s*([\d.,]+)\s*([a-zA-Z]*)")


def _medida(txt: Any, a_unidad: str) -> float | None:
    """'130 g' → kg; '15.8 cm' → cm. None si no se entiende."""
    m = _NUM.match(str(txt or ""))
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    u = m.group(2).lower()
    if a_unidad == "kg":
        f = {"g": 0.001, "gr": 0.001, "kg": 1.0, "mg": 1e-6, "lb": 0.4536}.get(u)
    else:
        f = {"cm": 1.0, "mm": 0.1, "m": 100.0, "in": 2.54, "pulgadas": 2.54}.get(u)
    return v * f if (f and v > 0) else None


def peso_atributos(item: dict) -> float | None:
    """max(PACKAGE_WEIGHT, L×A×H/5000) con los atributos del item de ML (kg)."""
    a = item.get("atributos") or {}
    kg = _medida(a.get("PACKAGE_WEIGHT"), "kg")
    if not kg:
        return None
    dims = [_medida(a.get(k), "cm") for k in ("PACKAGE_LENGTH", "PACKAGE_WIDTH", "PACKAGE_HEIGHT")]
    vol = dims[0] * dims[1] * dims[2] / 5000.0 if all(dims) else 0.0
    return max(kg, vol)


def peso_validados(fila: dict | None) -> float | None:
    """`costos._peso_efectivo` con el peso de costos_validados; None si no hay peso
    (el 0.5 kg de respaldo de producción abarata el envío)."""
    if not fila:
        return None
    peso = _f(fila.get("peso"))
    if not peso or peso <= 0:
        return None
    pe, _ = economia._costos()._peso_efectivo(
        peso, _f(fila.get("largo")) or 0.0, _f(fila.get("ancho")) or 0.0, _f(fila.get("alto")) or 0.0)
    return float(pe)


def pesos_por_listing(universo: list[dict], envio: list[dict],
                      validados: dict[str, dict]) -> dict[str, dict[str, Any]]:
    """{listing_id: {sku, kg, fuente, confianza}} para cada publicación de ML."""
    bill = {e["id"]: e["billable_weight"] / 1000.0 for e in envio
            if e.get("status") == 200 and (e.get("billable_weight") or 0) > 0}
    out: dict[str, dict[str, Any]] = {}
    for u in universo:
        lid = u.get("id")
        if not lid or u.get("error"):
            continue
        sku = _sku(u.get("sku"))
        if lid in bill:
            kg, fuente, conf = bill[lid], "ml_billable", "alta"
        elif (pa := peso_atributos(u)) is not None:
            kg, fuente, conf = pa, "ml_atributos", "media"
        elif (pv := peso_validados(validados.get(sku))) is not None:
            kg, fuente, conf = pv, "costos_validados", "baja"
        else:
            kg, fuente, conf = None, None, None
        out[lid] = {"sku": sku, "cuenta": u.get("cuenta"), "kg": _r(kg, 4), "fuente": fuente,
                    "confianza": conf, "status": u.get("status"), "es_full": bool(u.get("es_full"))}
    return out


def peso_de(costos: dict[str, dict], sku: str, listing_id: str | None = None) -> tuple[float | None, str | None]:
    """Peso facturable de una publicación (o del SKU si no hay listing)."""
    e = costos.get(_sku(sku)) or {}
    if listing_id:
        p = (e.get("pesos_listing") or {}).get(listing_id)
        if p and p.get("kg"):
            return p["kg"], p["fuente"]
    return e.get("peso_kg"), e.get("peso_fuente")


def leer() -> dict[str, dict]:
    """`ultimo/costos.json` ({} si aún no se calcula)."""
    return almacen.leer_json(almacen.ultimo("costos.json"), {}) or {}


# ── costo ────────────────────────────────────────────────────────────────────
def _prorrateo(filas: list[dict]) -> tuple[dict, str]:
    """`prorrateo.prorrateo_kubera`; si el módulo no está, la misma fórmula aquí."""
    try:
        from sandbox_precios.prorrateo import prorrateo_kubera

        return prorrateo_kubera(filas, costo_contenedor=_costo_contenedor()), "prorrateo.py"
    except ImportError:  # pragma: no cover — solo mientras prorrateo.py no existía
        return _prorrateo_local(filas), "local"


def _costo_contenedor() -> float:
    return float((_parametros().get("contenedor") or {}).get("costo_mxn") or 525_000)


def _prorrateo_local(filas: list[dict], rango: tuple[float, float] = (50.0, 80.0),
                     tarifa: float = 7500.0) -> dict:
    """Copia de `prorrateo.prorrateo_kubera` para cuando ese módulo falte.
    TODO: borrar en cuanto `prorrateo.py` esté en la rama (ya lo está el 28-sep)."""
    costo_c = _costo_contenedor()
    skus, por = {}, defaultdict(list)
    for f in filas:
        s = _sku(f.get("sku"))
        if not s:
            continue
        ccbm = _f(f.get("costo_cbm"))
        cbm = ccbm / tarifa if ccbm and ccbm > 0 else None
        cj, ppc = _f(f.get("cajas")), _f(f.get("piezas_por_caja"))
        cont = str(f.get("contenedor") or "").strip() or None
        r = {"sku": s, "contenedor": cont, "cbm_pieza": cbm,
             "piezas_embarque": cj * ppc if (cj and ppc and cj > 0 and ppc > 0) else None,
             "costo": None, "fuente": None, "marca": None, "m3_contenedor": None, "costo_m3": None}
        skus[s] = r
        if cont:
            por[cont].append(r)
    conts = {}
    for c, rs in por.items():
        m3 = sum((r["cbm_pieza"] or 0) * (r["piezas_embarque"] or 0) for r in rs)
        ok = rango[0] <= m3 <= rango[1]
        conts[c] = {"contenedor": c, "m3": round(m3, 4), "rango_ok": ok,
                    "costo_m3": round(costo_c / m3, 2) if ok and m3 else None}
        for r in rs:
            r["m3_contenedor"] = round(m3, 4)
            if not r["cbm_pieza"]:
                r["marca"] = "sin_cbm"
            elif ok:
                r.update(costo=round(costo_c * r["cbm_pieza"] / m3, 4), fuente="prorrateo_kubera",
                         costo_m3=conts[c]["costo_m3"])
            else:
                r.update(costo=round(r["cbm_pieza"] * tarifa, 4), fuente="tarifa_7500",
                         marca="contenedor_fuera_de_rango")
    for r in skus.values():
        if r["contenedor"] is None:
            if r["cbm_pieza"]:
                r.update(costo=round(r["cbm_pieza"] * tarifa, 4), fuente="tarifa_7500", marca="sin_contenedor")
            else:
                r["marca"] = "sin_cbm"
    return {"skus": skus, "contenedores": conts}


def _packing() -> tuple[dict[str, dict], dict[str, dict], str | None]:
    """{SKU: fila de packing100} + {código: contenedor} (puede no existir aún)."""
    p = almacen.leer_json(almacen.ultimo("packing100.json"))
    if not p:
        return {}, {}, None
    # Índice por huella del archivo y por CADA código (un archivo puede traer el
    # contenedor y su guía SZLS…: HPCU4441843 = SZLS5021460).
    conts: dict[str, dict] = {}
    for c in (almacen.leer_json(almacen.ultimo("contenedores.json"), {}) or {}).get("filas", []):
        for k in [c.get("sha256"), c.get("codigo"), *(c.get("codigos") or [])]:
            if k:
                conts.setdefault(k, c)
    return {_sku(f.get("sku")): f for f in p.get("filas") or [] if f.get("sku")}, conts, p.get("generado_at")


def _entrada_packing(f: dict, conts: dict[str, dict]) -> dict[str, Any] | None:
    """Costo exacto de un renglón del packing list, si el renglón es usable."""
    vol = _f((f.get("costos") or {}).get("volumetrico_real"))
    if vol is None or vol <= 0:
        return None
    cod = (f.get("archivo") or {}).get("contenedor")
    c = conts.get((f.get("archivo") or {}).get("sha256") or "") or conts.get(cod) or {}
    if c and c.get("rango_ok") is False:
        # Un embarque parcial/consolidado reparte 525k entre pocos m³: el costo
        # "exacto" sería falso. Se cae al prorrateo de kubera y se avisa.
        return None
    return {"unitario": round(vol, 4), "fuente": "packing_list_exacto", "contenedor": cod,
            "m3_contenedor": _r(_f(c.get("total_cbm")), 4),
            "costo_m3": _f(f.get("contenedor_costo_m3")) or _f(c.get("costo_m3")),
            "cbm_pieza": _f(f.get("cbm_pieza")),
            "empate": (f.get("empate") or {}).get("metodo"),
            "empate_confianza": (f.get("empate") or {}).get("confianza"),
            "costos_metodos": f.get("costos")}


def calcular(dia: dt.date | None = None, refrescar_validados: bool = False) -> dict[str, Any]:
    """Arma `ultimo/costos.json` y `ultimo/costos_resumen.json`. Devuelve el resumen."""
    t0 = time.monotonic()
    avisos: list[str] = []
    publicaciones = (_crudo("kubera_publicaciones.json") or {}).get("filas") or []
    universo = _crudo("ml_universo.jsonl") or []
    envio = _crudo("ml_envio.jsonl") or []
    precios = _crudo("ml_precios.jsonl") or []
    lineas = (_crudo("kubera_lineas.json") or {}).get("filas") or []
    envio_real = (_crudo("kubera_envio_real.json") or {}).get("filas") or []
    conts_kubera = {c["contenedor"]: c for c in (_crudo("kubera_contenedores.json") or {}).get("filas") or []}
    comisiones = almacen.leer_json(almacen.ruta("cache", "comisiones.json"), {}) or {}
    if not universo:
        avisos.append("sin ml_universo del día: los pesos por publicación quedan vacíos")

    # 1. costos_validados completa (el m³ del contenedor necesita a TODOS sus SKUs)
    try:
        filas_cv, origen_cv = leer_validados(dia, refrescar_validados)
    except Exception as exc:  # noqa: BLE001 — sin base: lo que traen las publicaciones
        log.warning("costos_validados no se pudo leer: %s", exc)
        avisos.append(f"costos_validados no se leyó ({type(exc).__name__}); prorrateo con las filas "
                      "de kubera_publicaciones — el m³ de los contenedores sale incompleto")
        vistos: dict[str, dict] = {}
        for f in publicaciones:
            if f.get("costo_cbm") is not None or f.get("costo_total") is not None:
                vistos.setdefault(_sku(f["sku"]), {k: f.get(k) for k in (
                    "sku", "contenedor", "costo_producto", "costo_cbm", "costo_total", "cajas",
                    "piezas_por_caja", "peso", "largo", "ancho", "alto", "revisado_at", "revisado_por")})
        filas_cv, origen_cv = list(vistos.values()), "kubera_publicaciones"
    cv = {_sku(f["sku"]): f for f in filas_cv if f.get("sku")}

    # 2. prorrateo de todo el catálogo + verificación del m³ contra el crudo
    pr, impl = _prorrateo(filas_cv)
    dif_m3 = [abs((_f(c.get("m3")) or 0) - (_f((conts_kubera.get(k) or {}).get("m3")) or 0))
              for k, c in pr["contenedores"].items() if k in conts_kubera]
    if dif_m3 and max(dif_m3) > 0.01:
        avisos.append(f"m³ reconstruido difiere del crudo kubera_contenedores hasta {max(dif_m3):.3f}")

    # 3. packing list exacto
    packing, conts_packing, packing_at = _packing()
    if not packing:
        avisos.append("sin ultimo/packing100.json: nadie tiene costo exacto de packing list")

    # 4. pesos por publicación
    pesos = pesos_por_listing(universo, envio, cv)
    pesos_sku: dict[str, dict[str, dict]] = defaultdict(dict)
    for lid, p in pesos.items():
        if p["sku"]:
            pesos_sku[p["sku"]][lid] = p

    # 5. universo de SKUs: catálogo de costos + todo lo publicado en cualquier canal
    todos = set(cv) | {_sku(f.get("sku")) for f in publicaciones if f.get("sku")} \
        | {p["sku"] for p in pesos.values() if p["sku"]} | set(packing)
    todos.discard("")
    # variantes por padre: un SKU publicado sin fila propia (padre "aplastado",
    # ML publica 100% plano) toma el costo MÁS ALTO de sus variantes — del lado
    # seguro: no se promete un margen que una variante no da.
    hijos: dict[str, list[str]] = defaultdict(list)
    for s in cv:
        partes = s.split("-")
        for i in range(2, len(partes)):
            hijos["-".join(partes[:i])].append(s)
    for s, f in packing.items():
        if f.get("padre"):
            hijos[_sku(f["padre"])].append(s)

    costos: dict[str, dict[str, Any]] = {}
    for sku in sorted(todos):
        f = cv.get(sku) or {}
        k = pr["skus"].get(sku) or {}
        e: dict[str, Any] = {
            "unitario": None, "fuente": "sin_costo", "contenedor": f.get("contenedor") or None,
            "m3_contenedor": k.get("m3_contenedor"), "costo_m3": k.get("costo_m3"),
            "validado": bool(f.get("revisado_at")), "revisado_por": f.get("revisado_por"),
            "revisado_at": f.get("revisado_at"),
            "movido": bool(f.get("revisado_at") and f.get("updated_at")
                           and str(f["updated_at"]) > str(f["revisado_at"])),
            "costo_panel": _r(_f(f.get("costo_total")), 4),
            "costo_producto_excluido": _r(_f(f.get("costo_producto")), 4),
            "costo_cbm_panel": _r(_f(f.get("costo_cbm")), 4),
            "cbm_pieza": k.get("cbm_pieza"),
            "costo_prorrateo_kubera": k.get("costo"),
            "fuente_prorrateo_kubera": k.get("fuente"),
            "marca": k.get("marca") if f else "sin_fila_costos_validados",
        }
        ep = _entrada_packing(packing[sku], conts_packing) if sku in packing else None
        if sku in packing and ep is None:
            e["marca"] = "packing_fuera_de_rango_o_sin_cbm"
        if ep:
            e.update(ep)
            e["marca_prorrateo_kubera"], e["marca"] = e["marca"], None
        elif k.get("costo") is not None:
            e["unitario"], e["fuente"] = k["costo"], k["fuente"]
        # dims y peso del SKU
        if f:
            e["dims"] = {"largo": _f(f.get("largo")), "ancho": _f(f.get("ancho")), "alto": _f(f.get("alto")),
                         "peso": _f(f.get("peso"))}
        else:
            e["dims"] = None
        pl = pesos_sku.get(sku) or {}
        e["pesos_listing"] = {lid: {"kg": p["kg"], "fuente": p["fuente"]} for lid, p in pl.items()}
        # peso del SKU: el de su mejor publicación (billable > atributos > validados);
        # entre dos del mismo nivel, el mayor (envío del lado seguro)
        orden = {"ml_billable": 0, "ml_atributos": 1, "costos_validados": 2}
        cands = sorted(((orden[p["fuente"]], -(p["kg"] or 0), p) for p in pl.values() if p["kg"]),
                       key=lambda t: (t[0], t[1]))
        if cands:
            e["peso_kg"], e["peso_fuente"] = cands[0][2]["kg"], cands[0][2]["fuente"]
        elif (pv := peso_validados(f)) is not None:
            e["peso_kg"], e["peso_fuente"] = _r(pv, 4), "costos_validados"
        else:
            e["peso_kg"], e["peso_fuente"] = None, None
        e["peso_confianza"] = {"ml_billable": "alta", "ml_atributos": "media",
                               "costos_validados": "baja"}.get(e["peso_fuente"])
        costos[sku] = e

    # 5b. contenedor con m³ de kubera fuera de rango, pero con packing list sano:
    # el denominador sale del archivo (Σ volumen de TODOS sus renglones) en vez
    # de la reconstrucción. Medido el 28-sep: 21 contenedores de kubera quedan
    # fuera de 50–80 m³ (FDCU0156499 da 695 m³, OOCU8248653 108 m³, FFAU5807425
    # 44 m³: faltan SKUs o hay unidades mal capturadas) y 6 de ellos tienen su
    # packing list indexado en 65.7–72 m³. El cbm_pieza de kubera cuadra con el
    # del packing list (razón mediana 1.0 en packing100), así que solo cambia el m³.
    recuperados = 0
    costo_c = _costo_contenedor()
    for sku, e in costos.items():
        if e["fuente"] != "tarifa_7500" or e.get("marca") != "contenedor_fuera_de_rango" or not e.get("cbm_pieza"):
            continue
        cod = str(e.get("contenedor") or "").split(" - ")[0].strip()
        c = conts_packing.get(cod) or {}
        tot = _f(c.get("total_cbm"))
        if not (c.get("rango_ok") and tot):
            continue
        e.update(unitario=round(costo_c * e["cbm_pieza"] / tot, 4), fuente="prorrateo_kubera",
                 m3_contenedor=round(tot, 4), costo_m3=round(costo_c / tot, 2),
                 marca="m3_del_packing_list", m3_kubera_descartado=e.get("m3_contenedor"))
        recuperados += 1

    # 6. padres sin costo propio ← variantes
    derivados = 0
    for sku, e in costos.items():
        if e["unitario"] is not None or sku not in hijos:
            continue
        vs = [costos[h] for h in set(hijos[sku]) if h in costos and costos[h]["unitario"] is not None]
        if not vs:
            continue
        peor = max(vs, key=lambda x: x["unitario"])
        e.update(unitario=peor["unitario"], fuente=peor["fuente"], contenedor=peor.get("contenedor"),
                 m3_contenedor=peor.get("m3_contenedor"), costo_m3=peor.get("costo_m3"),
                 marca="derivado_de_variantes",
                 variantes={"n": len(vs), "min": min(x["unitario"] for x in vs),
                            "max": peor["unitario"]})
        if e["costo_panel"] is None:
            e["costo_panel_variantes_max"] = max((x["costo_panel"] or 0) for x in vs) or None
        derivados += 1

    almacen.escribir_json(almacen.ultimo("costos.json"), costos)

    # 7. validación y resumen
    cat_item = {u["id"]: u.get("category_id") for u in universo if u.get("id")}
    par_ml = _parametros().get("mercado_libre") or {}
    reales = economia.tasas_reales(_ultimos_dias(lineas, DIAS_VALIDACION), min_lineas=MIN_LINEAS_TASA_REAL,
                                   categoria_de_item=cat_item)
    almacen.escribir_json(almacen.ultimo("comisiones_reales.json"),
                          {"generado_at": almacen.ahora_iso(), "dias": DIAS_VALIDACION,
                           "min_lineas": MIN_LINEAS_TASA_REAL, "solo_full": True,
                           "nota": "mediana de order_items.comision/(precio×cantidad) por categoría y tramo; "
                                   "respaldo 'real_orders' de economia.pct_comision",
                           "categorias": reales})
    respaldo = {"real_orders": reales, "por_tramo": par_ml.get("comision_respaldo_por_tramo")}

    pct_panel = {_sku(f["sku"]): _f(f.get("ml_pct_comision")) for f in publicaciones
                 if f.get("canal") == "mercado_libre" and f.get("sku")}
    fuentes = Counter(e["fuente"] for e in costos.values())
    resumen: dict[str, Any] = {
        "generado_at": almacen.ahora_iso(),
        "duracion_s": None,
        "origen_costos_validados": origen_cv, "prorrateo": impl,
        "packing100_generado_at": packing_at,
        "skus": len(costos), "por_fuente": dict(fuentes),
        "derivados_de_variantes": derivados,
        "m3_del_packing_list": recuperados,
        "marcas": dict(Counter(e["marca"] for e in costos.values() if e.get("marca"))),
        "por_fuente_peso": dict(Counter(e["peso_fuente"] for e in costos.values())),
        "contenedores": {"total": len(pr["contenedores"]),
                         "en_rango": sum(1 for c in pr["contenedores"].values() if c.get("rango_ok")),
                         "fuera_de_rango": sorted(k for k, c in pr["contenedores"].items() if not c.get("rango_ok")),
                         "dif_m3_vs_crudo_max": round(max(dif_m3), 4) if dif_m3 else None},
        "costo_vs_panel": _costo_vs_panel(costos),
        "validacion": {
            "comision": validar_comision(lineas, cat_item, comisiones, respaldo, pct_panel=pct_panel),
            "envio": validar_envio(envio_real, pesos, costos),
            "pesos": validar_pesos(universo, envio, cv),
            "cobertura_activas_full": cobertura_activas_full(universo, costos),
        },
        "margenes_activas_full": margenes_activas_full(universo, precios, costos, pesos, comisiones, respaldo),
        "avisos": avisos,
    }
    resumen["duracion_s"] = round(time.monotonic() - t0, 1)
    almacen.escribir_json(almacen.ultimo("costos_resumen.json"), resumen)
    return resumen


def _ultimos_dias(lineas: list[dict], dias: int) -> list[dict]:
    desde = (almacen.hoy_cdmx() - dt.timedelta(days=dias)).isoformat()
    hoy = almacen.hoy_cdmx().isoformat()   # el día en curso aún no trae la comisión
    return [l for l in lineas if desde <= str(l.get("fecha") or "") < hoy]


def _costo_vs_panel(costos: dict[str, dict]) -> dict[str, Any]:
    """Costo del laboratorio (525k) contra el del panel (costo_total) por fuente."""
    out: dict[str, Any] = {}
    for fuente in ("packing_list_exacto", "prorrateo_kubera", "tarifa_7500", "todas"):
        es = [e for e in costos.values() if e["unitario"] is not None and (e.get("costo_panel") or 0) > 0
              and (fuente == "todas" or e["fuente"] == fuente)]
        if not es:
            continue
        out[fuente] = {"n": len(es),
                       "unitario_mediano": _r(median(e["unitario"] for e in es)),
                       "panel_mediano": _r(median(e["costo_panel"] for e in es)),
                       "producto_excluido_mediano": _r(median((e.get("costo_producto_excluido") or 0) for e in es)),
                       "razon_mediana_lab_vs_panel": _r(median(e["unitario"] / e["costo_panel"] for e in es), 4),
                       "razon_mediana_lab_vs_cbm_panel": _r(median(
                           e["unitario"] / e["costo_cbm_panel"] for e in es if (e.get("costo_cbm_panel") or 0) > 0), 4)}
    return out


# ── validaciones contra la realidad ───────────────────────────────────────────
def validar_comision(lineas: list[dict], cat_item: dict[str, str], comisiones: dict,
                     respaldo: dict, dias: int = DIAS_VALIDACION,
                     pct_panel: dict[str, float] | None = None) -> dict[str, Any]:
    """Comisión predicha (economia) vs `order_items.comision` real, 60 días.

    La predicción usa SOLO `api` y el respaldo genérico (no `real_orders`, que
    sale de estas mismas líneas y haría la prueba circular). Se compara también
    con la fórmula del panel (`pct_panel` = pct guardado en costos_finales por
    SKU, aplicado al precio SIN IVA como hace `costos.py:284`).

    Ojo al leer el error: el % de la API es el de HOY. ML subió 19 categorías de
    19.5% a 21.5% el 13–14-sep (medido en las líneas de esas categorías), así que
    las líneas anteriores salen "sobre-predichas" sin que el modelo esté mal. Por
    eso se reporta por quincena: la última es la que valida el presente.
    """
    ls = _ultimos_dias(lineas, dias)
    sin_real = {"por_tramo": respaldo.get("por_tramo")}
    res: dict[str, list] = defaultdict(list)
    por_tramo: dict[str, list] = defaultdict(list)
    por_fuente: Counter = Counter()
    exactas = n = 0
    rel_panel: list[float] = []
    exactas_panel = n_panel = 0
    quincena: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for l in ls:
        pu, q, c = _f(l.get("precio_unitario")), _f(l.get("cantidad")), _f(l.get("comision"))
        if not pu or not q or not c or pu <= 0 or c <= 0:
            continue
        cat = l.get("category_id") or cat_item.get(l.get("item_id") or "")
        pct, fuente = economia.pct_comision(cat, pu, comisiones, sin_real)
        if pct is None:
            continue
        pred = economia.comision_de(pu, pct) * q
        err = pred - c
        clave = "full" if l.get("es_fulfillment") else "no_full"
        res[clave].append(err)
        res[f"{clave}_rel"].append(err / c)
        if clave == "full":
            por_tramo[economia.tramo(pu)].append(err / c)
            por_fuente[fuente] += 1
            n += 1
            exactas += abs(err) <= 0.011 * q
            res[f"full_rel_{fuente}"].append(err / c)
            fe = str(l.get("fecha") or "")
            qn = quincena[fe[:7] + ("-1" if fe[8:10] < "16" else "-2")]
            qn[0] += 1
            qn[1] += abs(err) <= 0.011 * q
            pp = (pct_panel or {}).get(_sku(l.get("sku")))
            if pp and pp > 0:
                pred_p = pu / 1.16 * pp * q
                rel_panel.append((pred_p - c) / c)
                n_panel += 1
                exactas_panel += abs(pred_p - c) <= 0.011 * q
    out: dict[str, Any] = {"dias": dias, "lineas_full": n,
                           "exactas_al_centavo": exactas,
                           "exactas_pct": round(exactas / n, 4) if n else None,
                           "fuente_pct": dict(por_fuente)}
    for k in ("full", "no_full"):
        if res[k]:
            out[k] = {"error_abs_mediano_mxn": round(median(abs(x) for x in res[k]), 2),
                      "error_mediano_mxn": round(median(res[k]), 2),
                      "error_rel": _q(res[f"{k}_rel"]),
                      "dentro_2pct": round(sum(1 for x in res[f"{k}_rel"] if abs(x) <= 0.02) / len(res[k]), 4)}
    out["full_por_tramo_error_rel"] = {t: _q(v) for t, v in sorted(por_tramo.items())}
    out["full_por_fuente_error_rel"] = {k.split("full_rel_")[1]: _q(v) for k, v in res.items()
                                        if k.startswith("full_rel_")}
    out["full_exactas_por_quincena"] = {k: {"lineas": v[0], "exactas_pct": round(v[1] / v[0], 4)}
                                        for k, v in sorted(quincena.items()) if v[0] >= 30}
    out["formula_panel"] = {"lineas": n_panel, "error_rel": _q(rel_panel),
                            "exactas_pct": round(exactas_panel / n_panel, 4) if n_panel else None,
                            "nota": "pct de costos_finales × precio/1.16 (lo que resta hoy el panel)"}
    return out


def validar_envio(envio_real: list[dict], pesos: dict[str, dict], costos: dict[str, dict]) -> dict[str, Any]:
    """Envío predicho a su precio mediano vs lo que ML cobró (pedidos de 1 pieza, 60 d).

    `precio_mediano` es de todos los pedidos del item y la tarifa cambia en
    $99/$199/$299/$499/$999: un item que vendió a ambos lados de un escalón
    mete ruido. Por eso se reporta también la fracción "misma columna".
    """
    por: dict[str, list[float]] = defaultdict(list)
    iguales: Counter = Counter()
    for r in envio_real:
        if not r.get("es_full") or not r.get("n_1u") or r.get("mediana_1u") is None:
            continue
        real, precio = _f(r["mediana_1u"]), _f(r.get("precio_mediano"))
        if not real or not precio:
            continue
        p = pesos.get(r["item_id"]) or {}
        kg, fuente = p.get("kg"), p.get("fuente")
        if not kg:
            por["sin_peso"].append(0.0)
            continue
        pred = economia.envio_full(precio, kg)
        por[fuente].append(pred / real)
        iguales[fuente] += abs(pred - real) <= 0.51
    out = {}
    for fuente, xs in por.items():
        if fuente == "sin_peso":
            out[fuente] = {"items": len(xs)}
            continue
        out[fuente] = {"items": len(xs), "razon_pred_real": _q(xs),
                       "igual_al_peso": iguales[fuente],
                       "dentro_10pct": sum(1 for x in xs if 0.9 <= x <= 1.1)}
    return out


def validar_pesos(universo: list[dict], envio: list[dict], cv: dict[str, dict]) -> dict[str, Any]:
    """Cada fuente de peso contra `billable_weight`: ¿da la misma tarifa?"""
    u_id = {u["id"]: u for u in universo if u.get("id")}
    out: dict[str, Any] = {}
    for nombre, fn in (("ml_atributos", lambda e: peso_atributos(u_id.get(e["id"]) or {})),
                       ("costos_validados", lambda e: peso_validados(cv.get(_sku(e.get("sku")))))):
        razones, misma = [], 0
        for e in envio:
            bw = e.get("billable_weight")
            if not bw or e.get("status") != 200:
                continue
            kg = fn(e)
            if not kg:
                continue
            razones.append(kg / (bw / 1000.0))
            misma += economia.envio_full(e["item_price"], kg) == economia.envio_full(e["item_price"], bw / 1000.0)
        out[nombre] = {"razon_vs_billable": _q(razones), "misma_tarifa": misma,
                       "dentro_20pct": sum(1 for x in razones if 0.8 <= x <= 1.25)}
    out["tabla_vs_list_cost_api"] = dict(Counter(
        economia.envio_full(e["item_price"], e["billable_weight"] / 1000.0) == _f(e.get("list_cost"))
        for e in envio if e.get("billable_weight") and e.get("list_cost") is not None))
    return {k: (dict((str(kk), vv) for kk, vv in v.items()) if isinstance(v, dict) else v) for k, v in out.items()}


def _activas_full(universo: list[dict]) -> list[dict]:
    return [u for u in universo if u.get("status") == "active" and u.get("es_full") and not u.get("error")]


def cobertura_activas_full(universo: list[dict], costos: dict[str, dict]) -> dict[str, Any]:
    act = _activas_full(universo)
    skus = {_sku(u.get("sku")) for u in act if u.get("sku")}
    return {"publicaciones": len(act), "sin_sku": sum(1 for u in act if not u.get("sku")),
            "skus": len(skus),
            "skus_por_fuente": dict(Counter((costos.get(s) or {}).get("fuente", "sin_costo") for s in skus)),
            "publicaciones_por_fuente": dict(Counter(
                (costos.get(_sku(u.get("sku"))) or {}).get("fuente", "sin_costo") for u in act)),
            "skus_validados": sum(1 for s in skus if (costos.get(s) or {}).get("validado")),
            "publicaciones_por_fuente_peso": dict(Counter(
                str(((costos.get(_sku(u.get("sku"))) or {}).get("pesos_listing") or {}).get(u["id"], {}).get("fuente"))
                for u in act))}


def margenes_activas_full(universo: list[dict], precios: list[dict], costos: dict[str, dict],
                          pesos: dict[str, dict], comisiones: dict, respaldo: dict) -> dict[str, Any]:
    """Margen al precio cobrado de cada activa FULL con el costo del panel y con 525k.

    Misma economía en los dos (comisión sobre precio con IVA por tramo, envío con
    peso facturable); solo cambia el costo. Responde "¿cuántas pasan de perder a
    ganar si el costo del producto USD no aplica?".
    """
    cobrado = {p["id"]: _f(p.get("precio_cobrado")) for p in precios if p.get("id")}
    filas, sin = [], Counter()
    for u in _activas_full(universo):
        sku = _sku(u.get("sku"))
        e = costos.get(sku) or {}
        P = cobrado.get(u["id"]) or _f(u.get("price"))
        kg = (pesos.get(u["id"]) or {}).get("kg")
        if not P:
            sin["sin_precio"] += 1
            continue
        if not kg:
            sin["sin_peso"] += 1
            continue
        lab = economia.utilidad(P, e.get("unitario"), u.get("category_id"), kg,
                                comisiones_cache=comisiones, respaldo=respaldo)
        pan = economia.utilidad(P, e.get("costo_panel") or e.get("costo_panel_variantes_max"),
                                u.get("category_id"), kg, comisiones_cache=comisiones, respaldo=respaldo)
        solo_cbm = economia.utilidad(P, e.get("costo_cbm_panel"), u.get("category_id"), kg,
                                     comisiones_cache=comisiones, respaldo=respaldo)
        filas.append({"id": u["id"], "sku": sku, "cuenta": u.get("cuenta"), "precio": P,
                      "m_lab": lab["margen"], "m_panel": pan["margen"], "m_cbm": solo_cbm["margen"],
                      "fuente": e.get("fuente"), "costo_panel": pan["costo"], "costo_lab": lab["costo"]})
    ambos = [f for f in filas if f["m_lab"] is not None and f["m_panel"] is not None]
    neg_a_pos = [f for f in ambos if f["m_panel"] < 0 <= f["m_lab"]]
    ambos_cbm = [f for f in filas if f["m_cbm"] is not None and f["m_panel"] is not None]
    return {
        "activas_full": len(_activas_full(universo)), "evaluables": len(filas), "excluidas": dict(sin),
        "con_margen_lab": sum(1 for f in filas if f["m_lab"] is not None),
        "con_margen_panel": sum(1 for f in filas if f["m_panel"] is not None),
        "con_ambos": len(ambos),
        "margen_mediano_lab": _r(median(f["m_lab"] for f in ambos), 4) if ambos else None,
        "margen_mediano_panel": _r(median(f["m_panel"] for f in ambos), 4) if ambos else None,
        "negativas_panel": sum(1 for f in ambos if f["m_panel"] < 0),
        "negativas_lab": sum(1 for f in ambos if f["m_lab"] < 0),
        "pasan_de_negativo_a_positivo": len(neg_a_pos),
        "siguen_negativas": sum(1 for f in ambos if f["m_lab"] < 0),
        "bajo_piso_lab": sum(1 for f in ambos if f["m_lab"] < 0.12),
        # costo del panel mayor que el precio cobrado: casi siempre un costo mal
        # capturado (ACC-0687-MOR: margen −1,789%), no una venta a pérdida real
        "costo_panel_mayor_que_precio": sum(1 for f in ambos if f["costo_panel"] > f["precio"]),
        "costo_lab_menor_a_1_peso": sum(1 for f in ambos if f["costo_lab"] < 1.0),
        "negativas_lab_por_fuente": dict(Counter(f["fuente"] for f in ambos if f["m_lab"] < 0)),
        "solo_quitando_producto_usd": {
            "con_ambos": len(ambos_cbm),
            "pasan_de_negativo_a_positivo": sum(1 for f in ambos_cbm if f["m_panel"] < 0 <= f["m_cbm"])},
        "ejemplos_neg_a_pos": [{k: f[k] for k in ("id", "sku", "cuenta", "precio", "m_panel", "m_lab")}
                               for f in sorted(neg_a_pos, key=lambda x: x["m_panel"])[:10]],
    }


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="Costos del laboratorio → ultimo/costos.json")
    ap.add_argument("--refrescar", action="store_true", help="volver a leer costos_validados de kubera")
    a = ap.parse_args()
    r = calcular(refrescar_validados=a.refrescar)
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
