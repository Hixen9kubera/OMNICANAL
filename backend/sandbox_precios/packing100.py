"""packing100.py — 100 SKUs publicados en ML con su costo EXACTO desde el packing list.

Para qué: `costos_lab.py` costea TODO el catálogo con el prorrateo reconstruido
desde `costos_validados` (`prorrateo.prorrateo_kubera`). Este módulo es la
MUESTRA DE VERDAD contra la que se mide ese atajo: 100 SKUs de los que más
venden en ML, con su renglón ubicado en el packing list ORIGINAL y el CBM TOTAL
del contenedor sumado sobre TODOS sus renglones (no solo los elegidos). De ahí
salen los cinco métodos de `prorrateo.py`, la comparación contra el costo que
hoy usa el panel y el margen al precio que ML cobra.

LA CADENA (receta medida el 28-sep: 86% resuelto en 4 archivos, 98 de 100 por
Ferraforme):

  1. Universo: SKUs con publicación ML ACTIVA (`channel.listings`), FULL primero
     y luego por piezas vendidas en 30 días (`channel.sales_daily`). Los padres
     se EXPANDEN a variantes con el catálogo de Odoo: el packing list nunca trae
     el padre.
  2. Cada variante tiene que EXISTIR en Odoo y tener `container_numbers`; si no,
     se salta con su motivo y se sigue con la siguiente (no se rellena con
     kubera: la regla de la casa es que de Odoo salen la foto y el contenedor).
  3. Archivos candidatos: por la referencia de Odoo, luego la de
     `costos_validados.contenedor`, más el original donde Ferraforme ubica al SKU.
  4. Cada archivo se baja e indexa UNA vez (`packing_indice.Indice`, ~0.5 GB de
     RAM en los grandes: uno a la vez, `del` + `gc.collect()`), se resume a
     JSON y se cachea por sha256 → la siguiente corrida no baja nada.
  5. El renglón, por peldaños: Ferraforme (misma versión, o texto que coincide)
     → foto de Odoo sha256 → dHash ≤ 8/64 con margen ≥ 4 → IA (título + foto del
     anuncio, tope de llamadas). En variantes de talla/color MANDA Ferraforme:
     la foto es la misma para todas las tallas (CALZ-0119-BLN-36/38: la foto
     elegía el renglón 14 y el bueno era el 16 y el 18).

SOLO LECTURA: SELECT en kubera, search_read en Odoo, GET a Drive/ML/mlstatic y la
API de Claude en el último peldaño. Nada de `packing_publicados.iniciar/guardar`
(escriben o gastan sin tope). Todo síncrono y bloqueante: quien lo llame desde
una corrutina lo manda a `asyncio.to_thread` (regla 11).

Corre:  python -m sandbox_precios.packing100 [--n 100] [--sin-ia] [--tope-ia 30]
"""
from __future__ import annotations

import argparse
import datetime as dt
import gc
import hashlib
import json
import logging
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sandbox_precios import _entorno  # noqa: E402

_entorno.cargar()

from sandbox_precios import almacen, prorrateo  # noqa: E402
from services import costos as costos_srv  # noqa: E402  (solo funciones puras: tarifa y peso)
from services import embarques, odoo  # noqa: E402
from services import packing_comparador as comp  # noqa: E402  (_bajar_imagen: un GET)
from services import packing_drive_carpeta as carpeta  # noqa: E402
from services import packing_ferraforme, packing_indice  # noqa: E402
from services import packing_publicados as ppub  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

log = logging.getLogger("laboratorio.packing100")

PARAMS = json.loads((Path(__file__).parent / "parametros.json").read_text(encoding="utf-8"))
_C = PARAMS["contenedor"]
_ML = PARAMS["mercado_libre"]
COSTO_CONTENEDOR = float(_C["costo_mxn"])
TARIFA_M3 = float(_C["tarifa_fija_m3_referencia"])
RANGO_M3 = tuple(_C["rango_m3_normal"])
CARGA_UTIL_KG = float(_C["carga_util_kg_40hc"])

UMBRAL_DHASH = ppub.UMBRAL_DHASH          # 8/64: mismo umbral que el validador de producción
MARGEN_DHASH = ppub.MARGEN_DHASH          # 4 bits sobre el segundo candidato
MAX_ARCHIVOS_SKU = 4                      # más de eso ya no es "un contenedor", es ruido de la referencia
_VERSION_RESUMEN = 2                      # sube si cambia lo que se guarda por archivo (2: voltot por renglón)
# Discrepancia tolerada entre el CBM por caja × cajas y la columna de volumen total
# del MISMO cartón antes de creerle a la columna total (ver `_reconciliar`).
_TOLERANCIA_VOLTOT = 1.25
# Razón kg/m³ de un 40' HC lleno por peso (carga útil / 70 m³ nominales): el W/M
# "de contenedor completo", informativo junto al W/M clásico de 1,000 kg/m³.
KG_POR_M3_FCL = CARGA_UTIL_KG / 70.0
_CONFIABLES = ("alta", "media")
_PEOR = {"alta": 0, "media": 1, "baja": 2}

# Precio aproximado de la API de Claude, USD por millón de tokens (entrada, salida).
# Solo para REGISTRAR el gasto del peldaño de IA; no decide nada.
_PRECIO_IA = {"claude-sonnet-4-5": (3.0, 15.0), "claude-haiku-4-5": (1.0, 5.0)}


def _p(*a: Any) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _r(v: Any, dec: int = 4) -> float | None:
    x = _f(v)
    return None if x is None else round(x, dec)


def _peor(a: str, b: str) -> str:
    return a if _PEOR.get(a, 2) >= _PEOR.get(b, 2) else b


# ── Lecturas de kubera (solo SELECT) ─────────────────────────────────────────
_SQL_PUBS = """
select upper(l.sku::text) as sku, a.legacy_code as cuenta, l.listing_id, l.logistic_type,
       l.price_sale, l.price, l.price_sale_at, l.category_id, l.stock_full
  from channel.listings l
  join core.accounts a on a.id = l.account_id
 where l.canal = 'mercado_libre'
   and lower(coalesce(l.situacion, '')) = 'active'
   and nullif(l.listing_id, '') is not null"""

# 30 días por publicación (cuenta, item). `sales_daily` excluye canceladas.
_SQL_VENTAS = """
select cuenta, item_id, sum(units_sold) as u, sum(revenue) as rev, sum(sale_fee) as fee
  from channel.sales_daily
 where canal = 'mercado_libre' and date > current_date - 30
 group by 1, 2"""

_SQL_VALIDADOS = """
select upper(sku::text) as sku, contenedor, costo_total, costo_cbm, costo_producto,
       peso, largo, ancho, alto, cajas, piezas_por_caja, revisado_at, revisado_por, updated_at
  from costing.costos_validados"""

_SQL_TAMANOS = """
select distinct on (drive_file_id) drive_file_id, bytes
  from costing.packing_archivos
 where tipo = 'original' and drive_file_id is not null
 order by drive_file_id, drive_modified_at desc nulls last, id desc"""

_SQL_NOMBRES = "select upper(sku::text) as sku, name from core.products where upper(sku::text) = any(%s)"


def _publicaciones() -> dict[str, dict[str, Any]]:
    """``{SKU publicado: {listings, unidades_30d, es_full, principal…}}`` de ML activo.

    La publicación "principal" (de donde sale el precio cobrado) es la FULL que
    más vendió: con dos cuentas el mismo SKU puede tener dos precios y el de la
    que mueve el volumen es el que decide el margen real.
    """
    ventas = {(r["cuenta"], r["item_id"]): r for r in sdb.fetch_all(_SQL_VENTAS)}
    out: dict[str, dict[str, Any]] = {}
    for r in sdb.fetch_all(_SQL_PUBS):
        v = ventas.get((r["cuenta"], r["listing_id"])) or {}
        ps, pl = _f(r["price_sale"]), _f(r["price"])
        out.setdefault(r["sku"], {"sku": r["sku"], "listings": []})["listings"].append({
            "cuenta": r["cuenta"], "listing_id": r["listing_id"],
            "logistica": r["logistic_type"], "es_full": r["logistic_type"] == "fulfillment",
            "precio_cobrado": ps or pl, "precio_fuente": "price_sale" if ps else "price",
            "precio_lista": pl, "categoria_id": r["category_id"], "stock_full": r["stock_full"],
            "u30": float(v.get("u") or 0), "rev30": float(v.get("rev") or 0),
            "fee30": float(v.get("fee") or 0)})
    for e in out.values():
        ls = e["listings"]
        e["es_full"] = any(x["es_full"] for x in ls)
        e["unidades_30d"] = sum(x["u30"] for x in ls)
        rev, fee = sum(x["rev30"] for x in ls), sum(x["fee30"] for x in ls)
        e["ingreso_30d"] = round(rev, 2)
        e["comision_real_30d"] = round(fee / rev, 4) if rev > 0 and fee > 0 else None
        e["principal"] = max(ls, key=lambda x: (x["es_full"], x["u30"], x["cuenta"] == "BEKURA"))
    return out


# ── Selección (sin bajar nada) ───────────────────────────────────────────────
def seleccion(n: int = 100, max_variantes: int = 10) -> dict[str, Any]:
    """El universo en ORDEN de prioridad, ya expandido y ruteado a archivos.

    No baja ningún packing list: es el plan. Devuelve ``candidatos`` (variantes
    con archivos donde buscarlas) y ``saltados`` (los que se caen antes de abrir
    un archivo). Cada uno lleva ``orden``: `correr` solo reporta los saltados
    que quedaron ANTES del punto donde juntó sus ``n``. ``n`` no recorta aquí —
    no se sabe cuántos se caerán en la escalera—.
    """
    pubs = _publicaciones()
    orden = sorted(pubs.values(), key=lambda e: (not e["es_full"], -e["unidades_30d"], e["sku"]))
    cat = odoo.contenedores_por_sku()
    if not cat:
        raise RuntimeError("Odoo no contestó el catálogo: sin él no se expanden padres ni hay contenedor")
    validados = {r["sku"]: r for r in sdb.fetch_all(_SQL_VALIDADOS)}
    cont_kubera = {s: (r.get("contenedor") or "") for s, r in validados.items() if r.get("contenedor")}
    inv = carpeta.inventario(completo=False)
    tamanos = {r["drive_file_id"]: int(r["bytes"] or 0) for r in sdb.fetch_all(_SQL_TAMANOS)}

    def mb(fid: str) -> float | None:
        b = tamanos.get(fid)
        if not b:
            p = carpeta._CACHE_DIR / f"pl_{fid}.xlsx"
            b = p.stat().st_size if p.exists() else 0
        return round(b / 1e6, 1) if b else None

    candidatos: list[dict[str, Any]] = []
    saltados: list[dict[str, Any]] = []
    vistos: set[str] = set()
    pos = 0
    for e in orden:
        s = e["sku"]
        vars_ = ppub.expandir(s, cat)
        padre = s if vars_ != [s] else None
        base = {"publicado": s, "padre": padre, "unidades_30d": e["unidades_30d"],
                "es_full": e["es_full"], "pub": e}
        if s not in cat and padre is None:
            saltados.append({**base, "sku": s, "orden": pos, "motivo": "sin_sku_en_odoo",
                             "detalle": "ni el SKU ni variantes suyas existen como default_code"})
            pos += 1
            continue
        if padre and len(vars_) > max_variantes:
            saltados.append({**base, "sku": s, "orden": pos, "motivo": "padre_con_muchas_variantes",
                             "detalle": f"{len(vars_)} variantes (> {max_variantes})"})
            pos += 1
            continue
        for v in vars_:
            if v in vistos:          # la variante también está publicada por su cuenta
                continue
            vistos.add(v)
            cn = (cat.get(v) or "").strip()
            if not cn:
                saltados.append({**base, "sku": v, "orden": pos, "motivo": "sin_container_numbers",
                                 "detalle": "Odoo no dice en qué contenedor llegó"})
                pos += 1
                continue
            # Odoo primero (la regla del laboratorio), kubera después; las dos se prueban.
            refs = sorted(ppub.referencias(v, cat, cont_kubera), key=lambda x: x[0] != "odoo")
            archivos: list[dict[str, Any]] = []
            for fuente, ref in refs:
                for fid, nombre in carpeta.archivos_de(ref, inv):
                    if fid not in {a["file_id"] for a in archivos}:
                        archivos.append({"file_id": fid, "nombre": nombre, "via": fuente, "mb": mb(fid)})
            candidatos.append({**base, "sku": v, "orden": pos, "container_numbers": cn,
                               "codigos": sorted(carpeta.codigos_de(cn)),
                               "numero_kubera": embarques.numero(cn),
                               "refs": refs, "archivos": archivos})
            pos += 1

    # Ferraforme en UNA consulta; su original entra como candidato aunque Odoo diga otro.
    ubic = packing_ferraforme.ubicaciones_de([c["sku"] for c in candidatos]) if candidatos else {}
    final: list[dict[str, Any]] = []
    for c in candidatos:
        c["ferraforme"] = [{"file_id": u["original_file_id"], "sha256": u["original_sha256"],
                            "fila": u["original_fila"], "texto": u.get("texto"),
                            "nombre": u.get("original_nombre"), "fuente_sku": u.get("fuente_sku")}
                           for u in ubic.get(c["sku"].upper(), [])]
        ya = {a["file_id"] for a in c["archivos"]}
        for u in c["ferraforme"]:
            if u["file_id"] not in ya:
                c["archivos"].append({"file_id": u["file_id"], "nombre": u["nombre"],
                                      "via": "ferraforme", "mb": mb(u["file_id"])})
                ya.add(u["file_id"])
        if not c["archivos"]:
            saltados.append({**c, "motivo": "sin_archivo",
                             "detalle": "referencia sin packing list en el inventario: "
                                        + ", ".join(r for _f, r in c["refs"])[:200]})
        else:
            final.append(c)
    return {"generado_at": almacen.ahora_iso(), "candidatos": final, "saltados": saltados,
            "validados": validados, "publicaciones": len(pubs), "inventario": len(inv),
            "catalogo_odoo": len(cat)}


# ── Resumen por archivo (lo que se cachea) ───────────────────────────────────
def _n(v: Any) -> str:
    """Celda comparable, igual que `packing_ferraforme._norm` pero tratando "12" y 12.0 igual."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return repr(round(float(v), 6))
    s = re.sub(r"\s+", " ", str(v)).strip().lower()
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        return repr(round(float(s), 6))
    return s


def _resumir(ix: packing_indice.Indice, fid: str, nombre: str, n_bytes: int,
             segundos: float) -> dict[str, Any]:
    """Lo que hace falta del archivo sin retener el libro: números por renglón
    (`Indice.datos`, la fórmula POR CAJA), huellas de las fotos y las celdas
    normalizadas para cotejar el texto de Ferraforme cuando la versión difiere."""
    filas = []
    for f in ix.filas:
        fe = f.get("fila_excel")
        if fe is None:
            continue
        dd = ix.datos(fe)
        filas.append({
            "fe": fe, "texto": ix.texto_fila(fe),
            "piezas_fila": dd["piezas_fila"], "cajas": dd["cajas"],
            "piezas_grupo": dd["piezas_grupo"], "grupo": dd["grupo"],
            "cbm_caja": dd["cbm_caja"], "cbm_pieza": dd["cbm_por_pieza"],
            "cbm_origen": dd["cbm_origen"], "peso_caja_kg": dd["peso_total"],
            "peso_pieza_kg": dd["peso_pieza"], "precio_usd": dd["precio_usd"] or None,
            "confiable": dd["confiable"],
            # Volumen total declarado del renglón (columna 总体积/total CBM): el
            # testigo contra el que se reconcilia el CBM por caja.
            "voltot": (ix._num(fe, ix.c_voltot) or None) if ix.c_voltot else None})
    fes = {x["fe"] for x in filas}
    celdas: dict[str, list[str]] = {}
    for num, row in enumerate(ix.ws.iter_rows(values_only=True), start=1):
        if num in fes:
            celdas[str(num)] = [c[:120] for c in (_n(v) for v in row[:40] if v not in (None, "")) if c]
    fotos = [{"fe": ix.idx_de_fila.get(i), "sha": p["sha"], "dh": p["dh"]} for i, p in ix.fotos.items()]
    voltot = sum(ix._num(fe, ix.c_voltot) for fe in fes) if ix.c_voltot else None
    return {"version": _VERSION_RESUMEN, "sha256": ix.sha256, "file_id": fid, "nombre": nombre,
            "bytes": n_bytes, "indexado_at": almacen.ahora_iso(), "segundos": round(segundos, 1),
            "fila_encabezado": ix.fila_encabezado,
            "columnas": {"cbm": ix.c_cbm, "voltot": ix.c_voltot, "cajas": ix.c_cajas,
                         "pzcaja": ix.c_pzcaja, "pztot": ix.c_pztot, "precio": ix.c_precio,
                         "peso_caja": ix.c_caja, "peso_total": ix.c_total},
            "avisos": ix.avisos[:10], "voltot_columna": voltot,
            "filas": filas, "fotos": fotos, "celdas": celdas}


def _preparar(res: dict[str, Any]) -> dict[str, Any]:
    """Índices en memoria sobre el resumen (no se guardan en el JSON)."""
    res["_por_fe"] = {x["fe"]: x for x in res["filas"]}
    res["_celdas"] = {int(k): set(v) for k, v in res["celdas"].items()}
    _reconciliar(res)
    return res


def _reconciliar(res: dict[str, Any]) -> None:
    """Corrige, por CARTÓN, el CBM que contradice la columna de volumen total.

    `Indice.datos` saca el volumen de la columna POR CAJA × cajas. Medido el
    28-sep en TCKU7630759: en 4 renglones (261–264) el proveedor escribió el
    volumen TOTAL del renglón en la columna por caja (4.64 m³ "por caja" × 94
    cajas), y el contenedor sumaba 970.8 m³ contra 71.9 de su propia columna de
    total: el costo por pieza de TODO el contenedor salía 13 veces más barato.

    Regla: si la columna de volumen total del archivo es CREÍBLE (su suma cae en
    el rango de un contenedor lleno) y un cartón difiere de ella más de 25%, se
    reparte el volumen total del cartón entre sus piezas, en partes iguales por
    pieza (la regla vigente de Brandon para cajas mixtas). Diferencias chicas
    (2–7% medido en NYKU4803479, PCIU8538522) son redondeo de una u otra columna
    y NO se tocan: el reparto volumétrico solo depende de las PROPORCIONES, así
    que un sesgo parejo se cancela.
    """
    filas = res["filas"]
    vt_tot = sum((x.get("voltot") or 0.0) for x in filas)
    res["_reconciliados"] = 0
    res["_cbm_sin_reconciliar"] = sum((x["cbm_pieza"] or 0) * (x["piezas_fila"] or 0) for x in filas)
    if not (RANGO_M3[0] <= vt_tot <= RANGO_M3[1]):
        return
    por_fe, vistos = res["_por_fe"], set()
    for x in filas:
        g = tuple(sorted({x["fe"], *(x.get("grupo") or [])}))
        if g in vistos:
            continue
        vistos.add(g)
        rows = [por_fe[r] for r in g if r in por_fe]
        vt = sum((r.get("voltot") or 0.0) for r in rows)
        calc = sum((r["cbm_pieza"] or 0.0) * (r["piezas_fila"] or 0.0) for r in rows)
        pz = sum((r["piezas_fila"] or 0.0) for r in rows)
        if vt <= 0 or pz <= 0:
            continue
        if calc > 0 and 1 / _TOLERANCIA_VOLTOT <= calc / vt <= _TOLERANCIA_VOLTOT:
            continue
        for r in rows:
            r["cbm_pieza_original"], r["cbm_pieza"] = r["cbm_pieza"], vt / pz
            r["cbm_origen"], r["reconciliado"] = "volumen_total_reconciliado", True
            res["_reconciliados"] += 1


class _ArchivoLigero:
    """Lo mínimo de un `Indice` que usa `packing_publicados._ia_titulo`, sacado del
    resumen: así el peldaño de título no obliga a re-parsear el xlsx."""

    def __init__(self, res: dict[str, Any]):
        self.filas = [{"fila_excel": x["fe"]} for x in res["filas"]]
        self._t = {x["fe"]: x["texto"] for x in res["filas"]}

    def texto_fila(self, fe: int) -> str:
        return self._t.get(fe, "")


# ── IA con tope y contabilidad ───────────────────────────────────────────────
class _ContadorIA:
    """Envuelve el cliente de Anthropic que usan `_ia_titulo`/`_ia_foto`: cuenta
    llamadas, suma tokens y corta en el tope. Al pasarse del tope lanza, y esas
    funciones atrapan cualquier excepción devolviendo [] (= "la IA no decidió")."""

    def __init__(self, tope: int, original):
        self.tope, self._original = tope, original
        self.llamadas, self.tokens_in, self.tokens_out, self.usd = 0, 0, 0, 0.0
        self.errores = 0
        self._real = None
        self.messages = self

    def cliente(self):
        if self._real is None:
            self._real = self._original()
        return self if self._real is not None else None

    def create(self, **kw):
        if self.llamadas >= self.tope:
            raise RuntimeError("laboratorio: tope de llamadas de IA alcanzado")
        self.llamadas += 1
        try:
            r = self._real.messages.create(**kw)
        except Exception:
            # Se cuenta aparte: un error de la API NO es "la IA dijo que no", y
            # no debe quedar cacheado como negativo.
            self.errores += 1
            raise
        u = getattr(r, "usage", None)
        ti, to = int(getattr(u, "input_tokens", 0) or 0), int(getattr(u, "output_tokens", 0) or 0)
        pin, pout = _PRECIO_IA.get(kw.get("model", ""), (3.0, 15.0))
        self.tokens_in += ti
        self.tokens_out += to
        self.usd += ti / 1e6 * pin + to / 1e6 * pout
        return r


# ── La escalera ──────────────────────────────────────────────────────────────
def _piezas_texto(texto: str | None) -> tuple[list[str], bool]:
    if not texto:
        return [], False
    return [_n(p) for p in texto.split(" · ") if p.strip()], len(texto) >= 300


def _coincide(piezas: list[str], trunc: bool, celdas: set[str] | None) -> bool:
    if not celdas or not piezas:
        return False
    for k, p in enumerate(piezas):
        if p in celdas:
            continue
        # `texto` se guardó recortado a 300: el último trozo puede venir cortado.
        if trunc and k == len(piezas) - 1 and any(c.startswith(p) for c in celdas):
            continue
        return False
    return True


def _por_ferraforme(c: dict[str, Any], resumenes: dict[str, dict]) -> tuple[dict | None, str]:
    """Primer peldaño. Acepta la ubicación de Ferraforme si el original es la MISMA
    versión (sha256) o, si no, si el texto que Ferraforme cotejó sigue en ese
    renglón (o aparece en UN solo renglón: se movió). Los 28 originales que son
    Sheets nativos se re-exportan desde Drive con otra huella; sin el cotejo por
    texto se perdería Ferraforme justo en esos."""
    if not c["ferraforme"]:
        return None, "sin ubicación de Ferraforme"
    pref = {a["file_id"]: k for k, a in enumerate(c["archivos"])}
    via = {a["file_id"]: a["via"] for a in c["archivos"]}
    notas = []
    for u in sorted(c["ferraforme"], key=lambda u: (pref.get(u["file_id"], 99), u["fila"] or 0)):
        res = resumenes.get(u["file_id"])
        if not res:
            notas.append(f"{u['nombre']}: no se pudo leer")
            continue
        fo, filas = u["fila"], res["_por_fe"]
        out = None
        if u["sha256"] == res["sha256"] and fo in filas:
            out = {"fe": fo, "detalle": f"Ferraforme: renglón {fo} (misma versión)", "confianza": "alta"}
        else:
            piezas, trunc = _piezas_texto(u["texto"])
            if not piezas:
                notas.append("otra versión del archivo y Ferraforme no guardó texto para cotejar")
                continue
            if fo in filas and _coincide(piezas, trunc, res["_celdas"].get(fo)):
                out = {"fe": fo, "detalle": f"Ferraforme: renglón {fo} (otra versión; el texto coincide)",
                       "confianza": "alta"}
            else:
                hits = [fe for fe, cel in res["_celdas"].items() if _coincide(piezas, trunc, cel)]
                if len(hits) == 1:
                    out = {"fe": hits[0], "confianza": "media",
                           "detalle": f"Ferraforme: el renglón se movió de {fo} a {hits[0]} (texto único)"}
                else:
                    notas.append(f"otra versión y el texto {'se repite en %d renglones' % len(hits) if hits else 'ya no aparece'}")
                    continue
        if via.get(u["file_id"]) == "ferraforme":
            out["confianza"] = _peor(out["confianza"], "media")
            out["detalle"] += " · Odoo/kubera apuntan a otro contenedor"
        return {**out, "fid": u["file_id"], "metodo": "ferraforme", "distancia": None}, ""
    return None, "; ".join(notas)


def _por_foto(foto: dict | None, fids: list[str], resumenes: dict[str, dict]) -> tuple[dict | None, str]:
    """Foto de Odoo contra las fotos del packing list: sha256 exacto o dHash ≤ 8 con
    margen ≥ 4 sobre el segundo candidato de OTRO contenido.

    Corrección sobre el validador de producción: si la foto ganadora está en
    VARIOS renglones del mismo archivo (el proveedor pega la misma foto a cada
    talla), el empate es AMBIGUO. Producción deduplica por contenido para medir
    el margen y se queda con el primero: así eligió el renglón 14 para las tallas
    36 y 38 de CALZ-0119-BLN."""
    if not foto:
        return None, "sin foto en Odoo"
    pares = []
    for k, fid in enumerate(fids):
        res = resumenes.get(fid)
        for p in (res or {}).get("fotos", []):
            if p["fe"] is None:
                continue
            if p["sha"] == foto["sha"]:
                d = 0
            elif foto.get("dh") is None or p.get("dh") is None:
                d = 64
            else:
                d = packing_indice.distancia(foto["dh"], p["dh"])
            pares.append((d, k, fid, p["fe"], p["sha"]))
    if not pares:
        return None, "el packing list no trae fotos ancladas"
    pares.sort()
    d0, _k, f0, fe0, sha0 = pares[0]
    seg = next((d for d, _k2, _f2, _fe, s in pares[1:] if s != sha0), 64)
    exacto = d0 == 0 and sha0 == foto["sha"]
    if not (exacto or (d0 <= UMBRAL_DHASH and seg - d0 >= MARGEN_DHASH)):
        return None, f"foto de Odoo a {d0}/64, 2º a {seg}"
    mismas = sorted({fe for d, _k2, f, fe, s in pares if s == sha0 and f == f0})
    if len(mismas) > 1:
        return ({"ambiguo": True, "fid": f0, "filas": mismas},
                f"la misma foto está en {len(mismas)} renglones ({', '.join(map(str, mismas[:6]))})")
    return {"fid": f0, "fe": fe0, "metodo": "sha256" if exacto else "dhash", "distancia": d0,
            "segundo": seg, "confianza": "alta",
            "detalle": "misma foto (sha256)" if exacto else f"dHash {d0}/64 · 2º a {seg}"}, ""


# ── Economía por unidad (DISENO §4, sin llamar a ML) ─────────────────────────
def _pct_tramo(precio: float) -> float:
    """Comisión Premium aproximada por tramo (parametros.json): medido en 35,028
    líneas de 90 días, la tasa real baja de 19.5% a 18/16/15% en 299/500/1000."""
    tramos = sorted(_ML["tramos_comision_precio"])
    t = max((x for x in tramos if precio >= x), default=tramos[0])
    return float(_ML["comision_respaldo_por_tramo"][str(t)])


def _economia(precio: float | None, costo: float | None, peso_ef: float | None,
              es_full: bool) -> dict[str, Any] | None:
    """utilidad = P − P×pct(tramo) − envío(peso, P) − IVA − costo − costo FULL.
    La comisión va sobre el precio CON IVA (medido: el cargo fijo implícito sale
    ≈0 así, y el panel la calcula sin IVA y se infla 2–3 puntos)."""
    if not precio or costo is None or peso_ef is None:
        return None
    iva_t = float(_ML["iva"])
    pct = _pct_tramo(precio)
    com = precio * pct
    # En FULL el vendedor paga el envío también debajo de $299 (99% de los
    # pedidos FULL medidos). Fuera de FULL, debajo del umbral lo paga el comprador.
    envio = (costos_srv.calc_fee_envio_ml(peso_ef, precio)
             if (es_full or precio >= float(_ML["umbral_envio_gratis"])) else 0.0)
    iva = precio - precio / (1 + iva_t)
    full = float(_ML.get("costo_full_unitario_mes") or 0.0) if es_full else 0.0
    util = precio - com - envio - iva - costo - full
    return {"precio": round(precio, 2), "comision_pct": pct, "comision": round(com, 2),
            "envio": round(envio, 2), "iva": round(iva, 2), "costo": round(costo, 4),
            "utilidad": round(util, 2), "margen": round(util / precio, 4)}


def _peso_kubera(val: dict | None) -> float | None:
    """Peso facturable como lo calcula el panel: max(peso, L×A×H/5000) de kubera."""
    if val and (_f(val.get("peso")) or 0) > 0:
        pe, _ = costos_srv._peso_efectivo(float(val["peso"]), float(val.get("largo") or 0),
                                          float(val.get("ancho") or 0), float(val.get("alto") or 0))
        return pe
    return None


def _peso_efectivo(val: dict | None, fila: dict) -> tuple[float | None, str | None]:
    """Peso facturable: packing list → kubera → None (`sin_peso`).

    DESVÍO de DISENO §4 (que pone kubera primero), a propósito y SOLO aquí, donde
    hay renglón del packing list: las medidas de `costos_validados` son el CBM
    reconstruido, no medidas reales, y en esta muestra dieron 212 kg facturables
    a MUE-0126-NEG (el packing list dice 1.1 kg y 0.006 m³ por pieza → envío de
    $1,472 en vez de ~$60) y 0.62 kg a SIL-0008-NEG (20 kg por pieza en el
    packing list). El packing list trae peso bruto por caja ÷ piezas y el CBM de
    la caja ÷ piezas: max(kg, cbm×200) es el volumétrico /5000 de ML con el aire
    del cartón incluido (cota alta, del lado seguro). El 0.5 kg de respaldo de
    `costos._peso_efectivo` NO se usa: haría el envío barato y el margen optimista.
    """
    kg, cbm = _f(fila.get("peso_pieza_kg")) or 0.0, _f(fila.get("cbm_pieza")) or 0.0
    if kg > 0:
        return max(kg, cbm * 200.0), "packing"
    pk = _peso_kubera(val)
    if pk:
        return pk, "kubera"
    return None, None


# ── La corrida ───────────────────────────────────────────────────────────────
def correr(n: int = 100, usar_ia: bool = True, tope_ia: int = 30, max_mb: float = 60.0,
           refrescar: bool = False, max_variantes: int = 10) -> dict[str, Any]:
    """Baja, indexa y resuelve hasta juntar ``n`` SKUs con renglón confiable.

    Escribe ``ultimo/packing100.json``, ``ultimo/contenedores.json`` y
    ``ultimo/packing_saltados.json``. Devuelve el resumen de la corrida.
    ``refrescar=True`` ignora el caché de resúmenes (vuelve a indexar).
    """
    t_ini = time.time()
    _p("selección…")
    sel = seleccion(n, max_variantes=max_variantes)
    cands, validados = sel["candidatos"], sel["validados"]
    _p(f"publicaciones activas {sel['publicaciones']} · candidatos {len(cands)} · "
       f"saltados previos {len(sel['saltados'])} · inventario {sel['inventario']} archivos")

    ruta_idx = almacen.ruta("cache", "packing", "_archivos.json")
    ruta_fotos = almacen.ruta("cache", "packing", "_fotos_odoo.json")
    ruta_ia = almacen.ruta("cache", "packing", "_ia.json")
    idx_arch: dict[str, dict] = almacen.leer_json(ruta_idx, {}) or {}
    fotos: dict[str, dict | None] = almacen.leer_json(ruta_fotos, {}) or {}
    ia_cache: dict[str, dict] = almacen.leer_json(ruta_ia, {}) or {}

    resumenes: dict[str, dict] = {}
    fallas: dict[str, str] = {}
    actual: dict[str, Any] = {"fid": None, "ix": None}
    stats = Counter()

    def soltar() -> None:
        actual["fid"], actual["ix"] = None, None
        gc.collect()

    def indexar(fid: str, nombre: str) -> packing_indice.Indice | None:
        soltar()
        t0 = time.time()
        try:
            datos = carpeta.bajar(fid, nombre)
        except Exception as exc:  # noqa: BLE001
            fallas[fid] = f"no se pudo bajar: {str(exc)[:160]}"
            _p(f"  ✗ {nombre}: {fallas[fid]}")
            return None
        t1 = time.time()
        try:
            ix = packing_indice.indexar(datos, nombre, fid)
        except Exception as exc:  # noqa: BLE001
            fallas[fid] = f"no se pudo leer: {str(exc)[:160]}"
            _p(f"  ✗ {nombre}: {fallas[fid]}")
            return None
        stats["archivos_indexados"] += 1
        stats["mb_indexados"] += len(datos) / 1e6
        _p(f"  ↓ {nombre}  {len(datos)/1e6:.1f} MB  bajar {t1-t0:.0f}s  indexar {time.time()-t1:.0f}s  "
           f"{ix.n} renglones · {len(ix.fotos)} fotos")
        actual["fid"], actual["ix"], actual["bytes"] = fid, ix, len(datos)
        actual["seg"] = time.time() - t0
        del datos
        return ix

    def asegurar(fid: str, nombre: str) -> dict | None:
        if fid in resumenes:
            return resumenes[fid]
        if fid in fallas:
            return None
        ent = idx_arch.get(fid)
        if ent and not refrescar:
            res = almacen.leer_json(almacen.ruta("cache", "packing", ent["sha256"] + ".json"))
            if res and res.get("version") == _VERSION_RESUMEN:
                stats["archivos_de_cache"] += 1
                resumenes[fid] = _preparar(res)
                return resumenes[fid]
        ix = indexar(fid, nombre)
        if ix is None:
            return None
        res = _resumir(ix, fid, nombre, actual["bytes"], actual["seg"])
        almacen.escribir_json(almacen.ruta("cache", "packing", res["sha256"] + ".json"), res)
        idx_arch[fid] = {"sha256": res["sha256"], "nombre": nombre, "bytes": res["bytes"],
                         "indexado_at": res["indexado_at"]}
        almacen.escribir_json(ruta_idx, idx_arch)
        resumenes[fid] = _preparar(res)
        return resumenes[fid]

    def ix_de(fid: str) -> packing_indice.Indice | None:
        """El índice vivo para la foto de la IA (uno a la vez)."""
        if actual["fid"] == fid:
            return actual["ix"]
        nombre = (resumenes.get(fid) or {}).get("nombre") or fid
        return indexar(fid, nombre)

    def foto_de(i: int, lista: list[dict]) -> dict | None:
        """Huella de la foto de Odoo; se piden en lotes de 10 hacia adelante y se cachean."""
        sku = lista[i]["sku"]
        if sku not in fotos:
            lote = [c["sku"] for c in lista[i:i + 40] if c["sku"] not in fotos][:10]
            try:
                b = odoo.imagenes_1920_por_sku(lote)
            except Exception as exc:  # noqa: BLE001
                _p(f"  Odoo fotos falló: {exc}")
                b = {}
            for s in lote:
                crudo = b.get(s.upper())
                fotos[s] = ({"sha": hashlib.sha256(crudo).hexdigest(), "dh": packing_indice.dhash(crudo)}
                            if crudo else None)
            stats["fotos_odoo_pedidas"] += len(lote)
            almacen.escribir_json(ruta_fotos, fotos)
        return fotos.get(sku)

    contador = None
    original_ia = ppub._cliente_ia
    if usar_ia:
        contador = _ContadorIA(tope_ia, original_ia)
        ppub._cliente_ia = contador.cliente

    def por_ia(c: dict, fids: list[str]) -> tuple[dict | None, str]:
        """Último peldaño: título del anuncio contra el texto de los renglones y la
        foto del anuncio contra la de los candidatos. Solo SKUs sin variantes (el
        título es del padre: no distingue tallas) y con presupuesto."""
        if contador is None:
            return None, "IA apagada"
        if c["padre"]:
            return None, "variante: la IA no distingue tallas/colores"
        clave_base = c["sku"]
        pr = c["pub"]["principal"]
        info = None
        for fid in fids:
            res = resumenes.get(fid)
            if not res:
                continue
            clave = f"{clave_base}|{res['sha256']}"
            if clave in ia_cache:
                hit = ia_cache[clave]
                stats["ia_de_cache"] += 1
                if hit.get("fe"):
                    return {**hit, "fid": fid}, ""
                continue
            if contador.llamadas + 2 > contador.tope:
                return None, "tope de IA alcanzado"
            if info is None:
                try:
                    from sandbox_precios import ml_http
                    st, j = ml_http.get(f"/items/{pr['listing_id']}", cuenta=pr["cuenta"])
                except Exception as exc:  # noqa: BLE001
                    return None, f"ML no dio el anuncio: {str(exc)[:80]}"
                if st != 200 or not isinstance(j, dict):
                    return None, f"ML /items dio {st}"
                pics = j.get("pictures") or []
                url = (pics[0].get("secure_url") or pics[0].get("url")) if pics else None
                info = {"titulo": j.get("title") or "", "foto": comp._bajar_imagen(url) if url else None}
                if not (info["titulo"] and info["foto"]):
                    return None, "el anuncio no trae título o foto"
            err0 = contador.errores
            cs = ppub._ia_titulo(info["titulo"], _ArchivoLigero(res))
            if not cs:
                if contador.errores == err0:
                    ia_cache[clave] = {"fe": None, "nota": "la IA de título no propuso renglones"}
                continue
            ix = ix_de(fid)
            if ix is None:
                continue
            vs = ppub._ia_foto(info["titulo"], info["foto"], ix, [x.get("fila") for x in cs])
            gana = next((v for v in vs if v.get("mismo_producto") and v.get("titulo_concuerda") is not False), None)
            try:
                fe = int(gana.get("fila")) if gana else None
            except (TypeError, ValueError):
                fe = None
            if fe is None or fe not in res["_por_fe"]:
                if contador.errores == err0:
                    ia_cache[clave] = {"fe": None, "nota": "la IA de foto no confirmó"}
                continue
            conf = _peor((gana.get("confianza") or "media").lower(), "media")
            hit = {"fe": fe, "metodo": "ia_foto", "distancia": None, "confianza": conf,
                   "detalle": f"IA: {str(gana.get('por_que') or '')[:160]}"}
            ia_cache[clave] = hit
            almacen.escribir_json(ruta_ia, ia_cache)
            return {**hit, "fid": fid}, ""
        almacen.escribir_json(ruta_ia, ia_cache)
        return None, "la IA no confirmó ningún renglón"

    resueltos: list[dict[str, Any]] = []
    saltados_run: list[dict[str, Any]] = []
    pospuestos: list[dict[str, Any]] = []
    evaluados: list[int] = []
    corte: int | None = None

    def evaluar(i: int, lista: list[dict]) -> None:
        c = lista[i]
        evaluados.append(c["orden"])
        arch = c["archivos"][:MAX_ARCHIVOS_SKU]
        nombres = {a["file_id"]: a["nombre"] for a in c["archivos"]}
        ferra_fids = [u["file_id"] for u in c["ferraforme"] if u["file_id"] in nombres]
        # Fase A: solo el/los original(es) de Ferraforme. Si resuelve, no se baja nada más.
        for fid in dict.fromkeys(ferra_fids):
            asegurar(fid, nombres[fid])
        fer, nota_f = _por_ferraforme(c, resumenes)
        foto = foto_de(i, lista)
        if fer:
            fids_foto = [fer["fid"]]
        else:
            for a in arch:
                asegurar(a["file_id"], a["nombre"])
            fids_foto = [a["file_id"] for a in arch if a["file_id"] in resumenes]
        if not any(f in resumenes for f in [a["file_id"] for a in arch] + ferra_fids):
            saltados_run.append({**c, "motivo": "sin_archivo",
                                 "detalle": "; ".join(fallas.get(a["file_id"], "?") for a in arch)[:300]})
            return
        fot, nota_p = _por_foto(foto, fids_foto, resumenes)
        amb = bool(fot and fot.get("ambiguo"))

        elegido, segunda = None, None
        if fer:
            elegido = dict(fer)
            if fot and not amb:
                if (fot["fid"], fot["fe"]) == (fer["fid"], fer["fe"]):
                    segunda = "la foto coincide"
                else:
                    segunda = f"la foto dice el renglón {fot['fe']}"
                    if not c["padre"]:          # en variantes la foto es la misma para todas las tallas
                        elegido["confianza"] = _peor(elegido["confianza"], "media")
            else:
                segunda = nota_p or "la foto no decide"
        elif fot and not amb:
            elegido = dict(fot)
            if c["padre"]:
                elegido["confianza"] = "media"
                elegido["detalle"] += " · variante sin Ferraforme: la foto puede ser la de otra talla"
            segunda = nota_f
        else:
            ia, nota_ia = por_ia(c, fids_foto)
            if ia:
                elegido = ia
                segunda = f"{nota_f}; {nota_p}"
            else:
                motivo = "ambiguo" if amb else "sin_renglon"
                saltados_run.append({**c, "motivo": motivo,
                                     "detalle": f"{nota_f}; {nota_p}; {nota_ia}"[:300]})
                return

        fila = resumenes[elegido["fid"]]["_por_fe"].get(elegido["fe"])
        if not fila or not fila.get("cbm_pieza") or not fila.get("piezas_fila"):
            saltados_run.append({**c, "motivo": "sin_renglon",
                                 "detalle": f"renglón {elegido['fe']} sin CBM o sin piezas"})
            return
        if not fila.get("confiable"):
            elegido["confianza"] = "baja"
            elegido["detalle"] += " · el packing list no dice cuántas cajas trae el cartón"
        if elegido["confianza"] not in _CONFIABLES:
            saltados_run.append({**c, "motivo": "ambiguo",
                                 "detalle": f"renglón {elegido['fe']} con confianza baja: {elegido['detalle']}"[:300]})
            return
        resueltos.append({"c": c, "e": elegido, "segunda": segunda})

    lista = cands
    i = 0
    segunda_pasada = False
    sin_evaluar: list[dict] = []      # pospuestos que ni en la segunda pasada hicieron falta
    try:
        while True:
            if len(resueltos) >= n:
                if not segunda_pasada:
                    # El corte es de la PRIMERA pasada: lo que quedó después no se miró.
                    corte = lista[i]["orden"] if i < len(lista) else None
                    sin_evaluar = pospuestos
                else:
                    sin_evaluar = lista[i:]
                break
            if i >= len(lista):
                if pospuestos and not segunda_pasada:
                    _p(f"segunda pasada con {len(pospuestos)} pospuestos por archivo grande")
                    lista, pospuestos, i, segunda_pasada = pospuestos, [], 0, True
                    continue
                break
            c = lista[i]
            necesarios = ([a for a in c["archivos"] if a["file_id"] in {u["file_id"] for u in c["ferraforme"]}]
                          or c["archivos"][:MAX_ARCHIVOS_SKU])
            if (not segunda_pasada and any((a["mb"] or 0) > max_mb and a["file_id"] not in resumenes
                                           for a in necesarios)):
                pospuestos.append(c)
                i += 1
                continue
            antes = len(resueltos)
            evaluar(i, lista)
            if len(resueltos) > antes:
                e = resueltos[-1]["e"]
                _p(f"[{len(resueltos):3d}/{n}] {c['sku']:<24} {e['metodo']:<10} {e['confianza']:<5} "
                   f"fila {e['fe']}")
            else:
                s = saltados_run[-1]
                _p(f"   ·   {c['sku']:<24} saltado {s['motivo']}: {s['detalle'][:90]}")
            i += 1
    finally:
        ppub._cliente_ia = original_ia
        soltar()
        almacen.escribir_json(ruta_ia, ia_cache)

    # ── Contenedores: prorrateo sobre TODOS los renglones de cada archivo ────
    prorr: dict[str, dict] = {}
    skus_por_fid: dict[str, list[str]] = defaultdict(list)
    for r in resueltos:
        skus_por_fid[r["e"]["fid"]].append(r["c"]["sku"])
    cont_filas = []
    for fid, res in resumenes.items():
        rens = [{"sku": None, "fe": x["fe"], "cbm_pieza": x["cbm_pieza"], "piezas": x["piezas_fila"],
                 "peso_pieza_kg": x["peso_pieza_kg"], "usd_pieza": x["precio_usd"]} for x in res["filas"]]
        pr = prorrateo.prorratear(rens, costo_contenedor=COSTO_CONTENEDOR, tarifa_m3=TARIFA_M3,
                                  rango_m3=RANGO_M3, carga_util_kg=CARGA_UTIL_KG)
        pr_fcl = prorrateo.prorratear(rens, costo_contenedor=COSTO_CONTENEDOR, tarifa_m3=TARIFA_M3,
                                      rango_m3=RANGO_M3, carga_util_kg=CARGA_UTIL_KG,
                                      kg_por_m3=KG_POR_M3_FCL)
        prorr[fid] = {"totales": pr["totales"], "invariante": pr["invariante"],
                      "por_fe": {x["fe"]: x["costos"] for x in pr["renglones"]},
                      "wm_fcl": {x["fe"]: x["costos"]["peso_volumen_wm"] for x in pr_fcl["renglones"]}}
        t = pr["totales"]
        cods = sorted(carpeta.codigos_de(res["nombre"]))
        voltot = res.get("voltot_columna")
        cont_filas.append({
            "codigo": cods[0] if cods else Path(res["nombre"]).stem[:40], "codigos": cods,
            "numero_kubera": embarques.numero(res["nombre"]),
            "archivo": res["nombre"], "file_id": fid, "sha256": res["sha256"],
            "mb": round(res["bytes"] / 1e6, 1),
            "total_cbm": t["total_cbm"], "total_piezas": t["total_piezas"],
            "total_peso_kg": t["total_peso_kg"], "total_usd": t["total_usd"],
            "costo_m3": t["costo_m3"], "costo_m3_wm": t["costo_m3_wm"],
            "diferencia_vs_7500": _r((t["costo_m3"] - TARIFA_M3) / TARIFA_M3, 4) if t["costo_m3"] else None,
            "rango_ok": t["rango_ok"], "renglones": t["renglones"],
            "renglones_sin_cbm": t["renglones_sin_cbm"], "renglones_sin_piezas": t["renglones_sin_piezas"],
            "cajas_mixtas": sum(1 for x in res["filas"] if x.get("grupo")),
            "renglones_no_confiables": sum(1 for x in res["filas"] if not x.get("confiable")),
            "skus_en_lote": sorted(skus_por_fid.get(fid, [])),
            "voltot_columna": _r(voltot, 4),
            # 1%: las columnas de volumen vienen redondeadas a 2 decimales (68.0 vs 68.12).
            "cuadra_voltot": (abs(voltot - t["total_cbm"]) <= max(0.01, 0.01 * voltot)) if voltot else None,
            "desvio_voltot": _r((t["total_cbm"] - voltot) / voltot, 4) if voltot else None,
            "total_cbm_sin_reconciliar": _r(res.get("_cbm_sin_reconciliar"), 4),
            "renglones_reconciliados": res.get("_reconciliados", 0),
            "costo_m3_wm_fcl": pr_fcl["totales"]["costo_m3_wm"],
            # Valor declarado en la factura comercial (USD × TC de referencia) contra
            # los 525k: si la mercancía sola vale más que el contenedor "todo
            # incluido", o la factura no es el costo real o los 525k no incluyen producto.
            "valor_ci_mxn": _r((t["total_usd"] or 0) * float(_C["tipo_cambio_referencia"]), 2) if t["total_usd"] else None,
            "cobertura_usd": t["cobertura_usd"], "cobertura_peso": t["cobertura_peso"],
            "densidad_kg_m3": t["densidad_kg_m3"], "filas_densas": t["filas_densas"],
            "limitado_por_peso": t["limitado_por_peso"], "peso_vs_carga_util": t["peso_vs_carga_util"],
            "invariante": pr["invariante"], "avisos": res.get("avisos", [])[:5]})

    # ── Filas del contrato ──────────────────────────────────────────────────
    pk = prorrateo.prorrateo_kubera(validados.values(), costo_contenedor=COSTO_CONTENEDOR,
                                    tarifa_m3=TARIFA_M3)
    nombres = {}
    try:
        skus_n = sorted({r["c"]["sku"] for r in resueltos} | {r["c"]["publicado"] for r in resueltos})
        nombres = {x["sku"]: x["name"] for x in sdb.fetch_all(_SQL_NOMBRES, (skus_n,))} if skus_n else {}
    except Exception as exc:  # noqa: BLE001
        _p(f"nombres de core.products no disponibles: {exc}")
    filas_out = []
    for r in resueltos:
        c, e = r["c"], r["e"]
        res = resumenes[e["fid"]]
        fila = res["_por_fe"][e["fe"]]
        costos = prorr[e["fid"]]["por_fe"][e["fe"]]
        pub, pr_ = c["pub"], c["pub"]["principal"]
        val = validados.get(c["sku"])
        precio = pr_["precio_cobrado"]
        pe, pe_fuente = _peso_efectivo(val, fila)
        cods = sorted(carpeta.codigos_de(res["nombre"]))
        cod = next((x for x in cods if x in c["codigos"]), cods[0] if cods else None)
        costo_total = _f((val or {}).get("costo_total"))
        econ_525 = _economia(precio, costos["volumetrico_real"], pe, pr_["es_full"])
        econ_panel = _economia(precio, costo_total, pe, pr_["es_full"]) if costo_total else None
        avisos = []
        if pe is None:
            avisos.append("sin_peso")
        if not prorr[e["fid"]]["totales"]["rango_ok"]:
            avisos.append("contenedor_fuera_de_rango")
        if fila.get("reconciliado"):
            avisos.append("cbm_reconciliado_con_volumen_total")
        if pr_["precio_fuente"] != "price_sale":
            avisos.append("precio_sin_price_sale")
        rev_at = (val or {}).get("revisado_at")
        upd_at = (val or {}).get("updated_at")
        pkr = pk["skus"].get(c["sku"]) or {}
        filas_out.append({
            "sku": c["sku"], "padre": c["padre"], "publicado": c["publicado"], "orden": c["orden"],
            "titulo": nombres.get(c["sku"]) or nombres.get(c["publicado"]),
            "cuentas": sorted({x["cuenta"] for x in pub["listings"]}),
            "listing_ids": [x["listing_id"] for x in pub["listings"]],
            "listing_principal": pr_["listing_id"], "cuenta_principal": pr_["cuenta"],
            "es_full": pr_["es_full"], "categoria_id": pr_["categoria_id"],
            "precio_cobrado": precio, "precio_lista": pr_["precio_lista"],
            "unidades_30d": pub["unidades_30d"], "unidades_30d_de": "publicacion",
            "odoo": {"container_numbers": c["container_numbers"], "codigos": c["codigos"],
                     "numero_kubera": c["numero_kubera"]},
            "archivo": {"nombre": res["nombre"], "file_id": e["fid"], "sha256": res["sha256"],
                        "contenedor": cod,
                        "via": next((a["via"] for a in c["archivos"] if a["file_id"] == e["fid"]), None)},
            "fila": e["fe"], "texto_fila": fila["texto"],
            "empate": {"metodo": e["metodo"], "distancia": e.get("distancia"),
                       "confianza": e["confianza"], "detalle": e["detalle"],
                       "segunda_opinion": r["segunda"]},
            "cajas": _r(fila["cajas"], 3), "piezas_fila": _r(fila["piezas_fila"], 3),
            "piezas_grupo": _r(fila["piezas_grupo"], 3), "caja_mixta": bool(fila.get("grupo")),
            "cbm_caja": _r(fila["cbm_caja"], 6), "cbm_pieza": _r(fila["cbm_pieza"], 8),
            "cbm_origen": fila["cbm_origen"],
            "peso_pieza_kg": _r(fila["peso_pieza_kg"], 4), "precio_usd": _r(fila["precio_usd"], 4),
            "costos": {k: _r(v, 2) for k, v in costos.items()},
            # Informativo: W/M con la razón de un 40' HC lleno por peso (≈379 kg/m³).
            "costo_wm_fcl": _r(prorr[e["fid"]]["wm_fcl"].get(e["fe"]), 2),
            "cbm_reconciliado": bool(fila.get("reconciliado")),
            "cbm_pieza_original": _r(fila.get("cbm_pieza_original"), 8),
            "contenedor_costo_m3": prorr[e["fid"]]["totales"]["costo_m3"],
            "kubera": {"costo_total": costo_total, "costo_cbm": _f((val or {}).get("costo_cbm")),
                       "costo_producto": _f((val or {}).get("costo_producto")),
                       "contenedor": (val or {}).get("contenedor"),
                       "revisado_at": rev_at, "revisado_por": (val or {}).get("revisado_por"),
                       "validado": rev_at is not None,
                       "movido": bool(rev_at and upd_at and upd_at > rev_at)},
            "prorrateo_kubera": {"costo": pkr.get("costo"), "fuente": pkr.get("fuente"),
                                 "marca": pkr.get("marca"), "m3_contenedor": pkr.get("m3_contenedor")},
            "peso_facturable_kg": _r(pe, 3), "peso_fuente": pe_fuente,
            "peso_facturable_kubera_kg": _r(_peso_kubera(val), 3),
            "comision_real_30d": pub["comision_real_30d"],
            "economia_525k": econ_525, "economia_panel": econ_panel,
            "margen_con_525k": econ_525["margen"] if econ_525 else None,
            "margen_panel": econ_panel["margen"] if econ_panel else None,
            "margenes_por_metodo": {k: ((_economia(precio, v, pe, pr_["es_full"]) or {}).get("margen")
                                        if v is not None else None) for k, v in costos.items()},
            "avisos": avisos})

    # ── Saltados: los previos que quedaron ANTES del corte + los de la escalera ──
    tope_orden = corte if corte is not None else float("inf")
    saltados = [s for s in sel["saltados"] if s["orden"] < tope_orden] + saltados_run
    for c in sin_evaluar:
        saltados.append({**c, "motivo": "pospuesto_archivo_grande",
                         "detalle": f"archivo > {max_mb:.0f} MB; no hizo falta para llegar a {n}"})
    salt_out = [{"sku": s["sku"], "padre": s.get("padre"), "publicado": s.get("publicado"),
                 "orden": s.get("orden"), "unidades_30d": s.get("unidades_30d"),
                 "motivo": s["motivo"], "detalle": s.get("detalle")} for s in saltados]
    salt_out.sort(key=lambda s: (s["orden"] if s["orden"] is not None else 1e9))

    resumen = _resumen(filas_out, salt_out, cont_filas, stats, contador, len(evaluados),
                       time.time() - t_ini, n)
    ahora = almacen.ahora_iso()
    almacen.escribir_json(almacen.ultimo("packing100.json"),
                          {"generado_at": ahora, "contenedor_mxn": COSTO_CONTENEDOR,
                           "metodo_recomendado": _C.get("metodo_recomendado"),
                           "resumen": resumen, "filas": filas_out})
    almacen.escribir_json(almacen.ultimo("contenedores.json"), {"generado_at": ahora, "filas": cont_filas})
    almacen.escribir_json(almacen.ultimo("packing_saltados.json"), {"generado_at": ahora, "filas": salt_out})
    return resumen


def _resumen(filas: list[dict], saltados: list[dict], conts: list[dict], stats: Counter,
             ia: _ContadorIA | None, evaluados: int, segundos: float, n: int) -> dict[str, Any]:
    """Los números que se reportan: cuántos, cómo empataron, cuánto se aleja el
    costo de 525k del que usa el panel y qué contenedores se salen de rango."""
    def med(xs):
        xs = [x for x in xs if x is not None]
        return round(statistics.median(xs), 4) if xs else None

    def prom(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 4) if xs else None

    vol = [(f["costos"]["volumetrico_real"], f["kubera"]["costo_total"], f["kubera"]["costo_cbm"],
            f["prorrateo_kubera"]["costo"], f["costos"]["peso_volumen_wm"], f["costos"]["tarifa_fija_7500"])
           for f in filas]
    dif_total = [v - k for v, k, *_ in vol if v is not None and k]
    rat_total = [v / k for v, k, *_ in vol if v is not None and k]
    dif_cbm = [v - c for v, _k, c, *_ in vol if v is not None and c]
    rat_cbm = [v / c for v, _k, c, *_ in vol if v is not None and c]
    rat_pk = [v / p for v, _k, _c, p, *_ in vol if v is not None and p]
    rat_wm = [w / v for v, _k, _c, _p, w, _t in vol if v and w is not None]
    rat_fcl = [f["costo_wm_fcl"] / f["costos"]["volumetrico_real"] for f in filas
               if f["costos"]["volumetrico_real"] and f["costo_wm_fcl"] is not None]
    usados = [x for x in conts if x["skus_en_lote"]]
    return {
        "n_objetivo": n, "resueltos": len(filas), "candidatos_evaluados": evaluados,
        "saltados_por_motivo": dict(Counter(s["motivo"] for s in saltados)),
        "empate_por_metodo": dict(Counter(f["empate"]["metodo"] for f in filas)),
        "empate_por_confianza": dict(Counter(f["empate"]["confianza"] for f in filas)),
        "segunda_opinion_foto": dict(Counter(
            ("coincide" if (f["empate"]["segunda_opinion"] or "").startswith("la foto coincide")
             else "discrepa" if (f["empate"]["segunda_opinion"] or "").startswith("la foto dice")
             else "no_decide") for f in filas if f["empate"]["metodo"] == "ferraforme")),
        "variantes": sum(1 for f in filas if f["padre"]),
        "caja_mixta": sum(1 for f in filas if f["caja_mixta"]),
        "con_costo_validado": sum(1 for f in filas if f["kubera"]["validado"]),
        "archivos": {"indexados": stats["archivos_indexados"], "de_cache": stats["archivos_de_cache"],
                     "mb_indexados": round(stats["mb_indexados"], 1), "con_skus": len(usados),
                     "totales": len(conts)},
        "contenedores": {
            "rango_ok": sum(1 for x in usados if x["rango_ok"]),
            "fuera_de_rango": [x["codigo"] for x in usados if not x["rango_ok"]],
            "costo_m3": sorted(x["costo_m3"] for x in usados if x["costo_m3"]),
            "invariante_ok": sum(1 for x in usados if x["invariante"]["ok"]),
            "cuadra_voltot": dict(Counter(str(x["cuadra_voltot"]) for x in usados)),
            "limitados_por_peso": [x["codigo"] for x in usados if x["limitado_por_peso"]],
            "peso_vs_carga_util_max": max((x["peso_vs_carga_util"] or 0) for x in usados) if usados else None},
        "vs_kubera": {
            "n_con_costo_total": len(dif_total),
            "dif_media_vs_costo_total": prom(dif_total), "dif_mediana_vs_costo_total": med(dif_total),
            "razon_mediana_vs_costo_total": med(rat_total),
            "n_con_costo_cbm": len(dif_cbm),
            "dif_media_vs_costo_cbm": prom(dif_cbm), "razon_mediana_vs_costo_cbm": med(rat_cbm),
            "razon_mediana_vs_prorrateo_kubera": med(rat_pk), "n_prorrateo_kubera": len(rat_pk),
            "razon_mediana_wm_vs_vol": med(rat_wm),
            "wm_distinto_de_vol_mas_1pct": sum(1 for x in rat_wm if abs(x - 1) > 0.01),
            "razon_mediana_wm_fcl_vs_vol": med(rat_fcl),
            "wm_fcl_distinto_de_vol_mas_10pct": sum(1 for x in rat_fcl if abs(x - 1) > 0.10)},
        "cbm_reconciliados": sum(1 for f in filas if f["cbm_reconciliado"]),
        "margen_mediano_525k": med([f["margen_con_525k"] for f in filas]),
        "margen_mediano_panel": med([f["margen_panel"] for f in filas]),
        "perdiendo_con_525k": sum(1 for f in filas if (f["margen_con_525k"] or 0) < 0),
        "perdiendo_con_panel": sum(1 for f in filas if (f["margen_panel"] or 0) < 0 and f["margen_panel"] is not None),
        "sin_peso": sum(1 for f in filas if "sin_peso" in f["avisos"]),
        "ia": ({"llamadas": ia.llamadas, "errores": ia.errores, "tope": ia.tope, "tokens_in": ia.tokens_in,
                "tokens_out": ia.tokens_out, "usd_aprox": round(ia.usd, 3)} if ia else None),
        "segundos": round(segundos, 1),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Packing list exacto para N SKUs publicados en ML (solo lectura)")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--sin-ia", action="store_true")
    ap.add_argument("--tope-ia", type=int, default=30)
    ap.add_argument("--max-mb", type=float, default=60.0)
    ap.add_argument("--max-variantes", type=int, default=10)
    ap.add_argument("--refrescar", action="store_true", help="ignora el caché de resúmenes por archivo")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # Con el .env local el Storage apunta a un proyecto muerto y CADA bajada avisa
    # antes de caer a Drive: es ruido conocido (packing.md, trampa 2), no un error.
    logging.getLogger("omnicanal.packing.drive_carpeta").setLevel(logging.ERROR)
    r = correr(n=a.n, usar_ia=not a.sin_ia, tope_ia=a.tope_ia, max_mb=a.max_mb,
               refrescar=a.refrescar, max_variantes=a.max_variantes)
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
