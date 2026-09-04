# -*- coding: utf-8 -*-
"""
walmart_field_requirements.py — los atributos de las 75 categorías de Walmart MX,
en una tabla, listos para `channel.field_requirements`.

POR QUÉ EXISTE
--------------
En TikTok los atributos obligatorios se piden por API
(`GET /product/202309/categories/{id}/attributes`). **En Walmart MX ESA API NO
EXISTE**: `POST /v3/items/spec` da 404 con credenciales MX y en Global está
marcada "US only", así que no llega ni migrando. La fuente es un archivo:

    https://developer.walmart.com/file/mp/mx/MX_MP_ITEM_INTL_SPEC.json
    3.9 MB · HTTP 200 SIN credenciales

Trae, por categoría: el nombre del campo, su **etiqueta en español** (que es la
que Walmart usa en los mensajes de error), la lista `required`, las listas
cerradas (`enum`), tipo, unidades, ejemplos y límites de longitud.

⚠️ EL ARCHIVO ES LA 3.19 Y PRODUCCIÓN CORRE LA 3.11
---------------------------------------------------
No coinciden, y las diferencias tumban lotes enteros. Por eso cada fila sale con
una columna `veredicto_produccion` que dice si lo MEDIDO confirma o contradice al
archivo. Lo medido manda. Las contradicciones conocidas están en
`CORRECCIONES_MEDIDAS`, abajo — se agregan conforme se miden, no se adivinan.

Uso:
    python -m scripts.walmart_field_requirements                # resumen
    python -m scripts.walmart_field_requirements --csv salida.csv
    python -m scripts.walmart_field_requirements --categoria "Electrónicos"
"""
from __future__ import annotations

import csv
import json
import os
import pathlib
import sys
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# El esquema no se versiona en el repo (3.9 MB). Se busca donde suele estar.
CANDIDATOS = [
    pathlib.Path(os.getenv("WM_SPEC_JSON", "")),
    pathlib.Path(r"C:\Users\diaz2\OneDrive\Escritorio\respaldo_payloads_20260812"
                 r"\MX_MP_ITEM_INTL_SPEC.json"),
    pathlib.Path("MX_MP_ITEM_INTL_SPEC.json"),
    pathlib.Path("MX_SPEC.json"),
]

# ═════════════════════════════════════════════════════════════════════════════
# LO MEDIDO EN PRODUCCIÓN (3.11) CONTRA LO QUE DICE EL ARCHIVO (3.19)
# ═════════════════════════════════════════════════════════════════════════════
# Cada renglón costó un lote. Formato:
#     (categoría, campo) -> (veredicto, evidencia)
#
# "RECHAZADO"  el archivo lo lista como válido y producción lo rechaza.
# "OBLIGATORIO" producción lo exige y el archivo NO lo marca required.
CORRECCIONES_MEDIDAS: dict[tuple[str, str], tuple[str, str]] = {
    ("Electrónicos", "modelNumber"):
        ("RECHAZADO", "'modelNumber' is not a valid field — 85 de 85 muertos, 7-ago"),
    ("Accesorios Electrónicos", "modelNumber"):
        ("RECHAZADO", "'modelNumber' is not a valid field — 5 feeds de 85, 7-ago"),
    ("Almacenamiento", "gender"):
        ("RECHAZADO", "'gender' is not a valid field — 83 de 83 muertos, 7-ago"),
    ("Juguetes", "countPerPack"):
        ("RECHAZADO", "'countPerPack' is not a valid field — 33 de 33 muertos, 7-ago"),
    ("Juguetes", "productLine"):
        ("OBLIGATORIO", "`Linea de Producto` is a required attribute — 31 SKUs, 7-ago"),
    ("Juguetes", "activity"):
        ("OBLIGATORIO", "`Actividad` is a required attribute — 31 SKUs, 7-ago"),
    ("Cocina, Decoración y Otros", "size"):
        ("OBLIGATORIO", "`Talla` is a required attribute — 3 SKUs de cocina, 5-ago"),
    ("Cocina, Decoración y Otros", "gender"):
        ("OBLIGATORIO", "`Género` is a required attribute — 3 SKUs de cocina, 5-ago"),
    ("Otros Electrónicos", "wattage"):
        ("OBLIGATORIO", "`Consumo en Watts` is a required attribute — sonda 7-ago"),
    # MEDIDO EN EL PILOTO DEL 4-SEP. La pijama ROP-0417-ROS pasó el filtro de
    # UPC —la exención de "Pijamas" SÍ cubre `clothing_other`— y murió en esto:
    # el esquema publicado dice que "Ropa" pide 4 obligatorios (gender,
    # material, colorCategory, countPerPack) y producción pide un quinto.
    # `productLine` NO existe en este bloque; solo `activity`.
    ("Ropa", "activity"):
        ("OBLIGATORIO", "`Actividad` is a required attribute — piloto ROP-0417-ROS, 4-sep"),
}

# Categorías con exención de UPC PROBADA (un SKU llegó a SUCCESS por ahí).
# "Ropa" entra con prueba POSITIVA del 4-sep: el feed de ROP-0417-ROS NO trajo
# el error de PDI (`not authorized to set up 'CUSTOM' Product IDs`) sino uno de
# atributo faltante — llegó MÁS ALLÁ de la etapa donde muere una categoría sin
# exención. Es justo el tipo de evidencia que el encabezado de este archivo
# exige y que "no apareció el error de UPC" por sí solo no da.
EXENCION_PROBADA = {"Disfraces", "Cocina, Decoración y Otros", "Electrónicos",
                    "Ropa"}
EXENCION_NEGADA = {"Muebles", "Eléctricas", "Cables", "Electrodomésticos"}


def _spec() -> dict:
    for p in CANDIDATOS:
        # `Path("")` se resuelve a `.` y `.exists()` da True: hay que pedir
        # explícitamente que sea un ARCHIVO, o se intenta leer el directorio.
        if p and str(p) and p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    raise SystemExit(
        "No encuentro MX_MP_ITEM_INTL_SPEC.json.\n"
        "Bájalo de https://developer.walmart.com/file/mp/mx/MX_MP_ITEM_INTL_SPEC.json\n"
        "o apunta WM_SPEC_JSON a su ruta.")


def _tipo(d: dict) -> str:
    """Tipo legible, resolviendo los que son {measure, unit}."""
    t = d.get("type") or "?"
    if t == "object" and "measure" in (d.get("properties") or {}):
        return "medida{measure,unit}"
    if t == "array":
        it = d.get("items") or {}
        if it.get("enum"):
            return "lista[cerrada]"
        return f"lista[{it.get('type') or '?'}]"
    if d.get("enum"):
        return "cerrada"
    return t


def _valores(d: dict) -> list:
    """La lista cerrada, esté en la raíz o dentro de items."""
    if d.get("enum"):
        return d["enum"]
    it = d.get("items") or {}
    if it.get("enum"):
        return it["enum"]
    prop = (d.get("properties") or {}).get("unit") or {}
    if prop.get("enum"):
        return prop["enum"]
    return []


def filas() -> list[dict]:
    spec = _spec()
    MP = spec["properties"]["MPItem"]["items"]["properties"]
    out: list[dict] = []

    # ── el bloque Orderable es COMÚN a las 75 categorías ────────────────
    ordb = MP["Orderable"]
    req_ord = set(ordb.get("required") or [])
    for campo, d in (ordb.get("properties") or {}).items():
        out.append(_fila("(todas)", "(todas)", "Orderable", campo, d,
                         campo in req_ord))

    # ── el bloque Visible cambia POR CATEGORÍA ─────────────────────────
    for cat, g in (MP["Visible"]["properties"] or {}).items():
        req = set(g.get("required") or [])
        for campo, d in (g.get("properties") or {}).items():
            out.append(_fila(cat, cat, "Visible", campo, d, campo in req))
    return out


def _fila(cat: str, etiqueta_cat: str, bloque: str, campo: str,
          d: dict, obligatorio: bool) -> dict:
    vals = _valores(d)
    veredicto, evidencia = CORRECCIONES_MEDIDAS.get((cat, campo), ("", ""))
    # Lo medido MANDA sobre el archivo.
    if veredicto == "OBLIGATORIO":
        obligatorio = True
    return {
        "canal": "walmart",
        "categoria": cat,
        "bloque": bloque,
        "campo": campo,
        "etiqueta_es": d.get("title") or "",
        "obligatorio": "SI" if obligatorio else "",
        "obligatorio_segun_archivo": "SI" if obligatorio and veredicto != "OBLIGATORIO" else "",
        "tipo": _tipo(d),
        "lista_cerrada": " | ".join(str(v) for v in vals) if vals else "",
        "n_valores": len(vals) or "",
        "min": (d.get("minLength") if d.get("minLength") is not None
                else (d.get("properties", {}).get("measure", {}).get("minimum", ""))),
        "max": (d.get("maxLength") if d.get("maxLength") is not None
                else (d.get("properties", {}).get("measure", {}).get("maximum", ""))),
        "ejemplos": str(d.get("examples") or "")[:120],
        "descripcion": (d.get("description")
                        or (d.get("items") or {}).get("description") or "")[:300],
        "veredicto_produccion": veredicto,
        "evidencia_produccion": evidencia,
        "exencion_upc": ("PROBADA" if cat in EXENCION_PROBADA
                         else "NEGADA" if cat in EXENCION_NEGADA
                         else "" if cat == "(todas)" else "SIN EVIDENCIA"),
    }


def main() -> None:
    fs = filas()
    args = sys.argv[1:]

    if "--categoria" in args:
        cat = args[args.index("--categoria") + 1]
        fs_cat = [f for f in fs if f["categoria"] in (cat, "(todas)")]
        print(f"\n### {cat}\n")
        obl = [f for f in fs_cat if f["obligatorio"] and f["bloque"] == "Visible"]
        print(f"OBLIGATORIOS del bloque Visible ({len(obl)}):")
        for f in obl:
            marca = "  ⚠ producción lo exige y el archivo no" \
                if f["veredicto_produccion"] == "OBLIGATORIO" else ""
            print(f"   · {f['campo']:26} «{f['etiqueta_es']}»  [{f['tipo']}]{marca}")
            if f["lista_cerrada"]:
                print(f"       valores: {f['lista_cerrada'][:150]}")
        mal = [f for f in fs_cat if f["veredicto_produccion"] == "RECHAZADO"]
        if mal:
            print(f"\nRECHAZADOS por producción aunque el archivo los liste ({len(mal)}):")
            for f in mal:
                print(f"   · {f['campo']:26} {f['evidencia_produccion']}")
        opc = [f for f in fs_cat
               if not f["obligatorio"] and f["bloque"] == "Visible"
               and f["veredicto_produccion"] != "RECHAZADO"]
        print(f"\nOpcionales que la IA PODRÍA llenar ({len(opc)}):")
        for f in opc[:30]:
            print(f"   · {f['campo']:26} «{f['etiqueta_es']}»  [{f['tipo']}]")
        return

    if "--csv" in args:
        destino = pathlib.Path(args[args.index("--csv") + 1])
        with destino.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(fs[0].keys()))
            w.writeheader()
            w.writerows(fs)
        print(f">>> {len(fs)} filas -> {destino}")
        return

    cats = {f["categoria"] for f in fs} - {"(todas)"}
    obl = [f for f in fs if f["obligatorio"]]
    print("=" * 78)
    print("ATRIBUTOS DE WALMART MX — desde MX_MP_ITEM_INTL_SPEC.json")
    print("=" * 78)
    print(f"   categorías              : {len(cats)}")
    print(f"   filas totales           : {len(fs)}")
    print(f"   obligatorios            : {len(obl)}")
    print(f"     · comunes (Orderable) : {len([f for f in obl if f['bloque']=='Orderable'])}")
    print(f"     · por categoría       : {len([f for f in obl if f['bloque']=='Visible'])}")
    print(f"   con lista cerrada       : {len([f for f in fs if f['lista_cerrada']])}")
    print(f"   correcciones medidas    : {len(CORRECCIONES_MEDIDAS)}")
    print()
    print("   Categorías con MÁS obligatorios del bloque Visible:")
    c = Counter(f["categoria"] for f in fs
                if f["obligatorio"] and f["bloque"] == "Visible")
    for k, n in c.most_common(8):
        print(f"      {k[:40]:42} {n:>3}")


if __name__ == "__main__":
    main()
