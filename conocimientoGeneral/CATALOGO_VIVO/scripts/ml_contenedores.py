"""
ml_contenedores.py — Lee el paquete «valor de los contenedores a precio de Mercado Libre».

Es el trabajo de la sesión de COMPETENCIA de Eduardo (corte 7-oct-2026): cada línea de los
packing lists originales, con sus unidades vendibles y el precio de Mercado Libre de ese
producto cotizado por la API. Aquí NO se cotiza nada: solo se leen sus archivos, que viven
FUERA de git (traen precios de compra de proveedores) en `<salida>/datos/eduardo_ml/`:

    contenedores_v2.csv      las líneas: archivo (sha256), fila de Excel, piezas, unidades, FOB, SKU
    valor_lineas.csv         la valuación de cada línea, ya con sus reglas aplicadas
    pm_precios_full.csv      precio de mercado por id (`SKU` o `desc:<hash>`): mín, media, máx, mediana…
    pm_precios_sd.csv        2ª pasada de los SKU que quedaron sin dato (ids `desc:<SKU>`)
    correcciones_top50.json  las 50 líneas de banda de mayor valor, revisadas a mano
    valor_resumen.json       sus totales, para poder comparar

Sus reglas, que aquí se respetan tal cual:
  · valor = UNIDADES de la línea × precio de NUESTRO paquete. Nunca piezas × precio por pieza.
  · La cifra principal es el DEPURADO (`valor_depurado_mxn`): exacto → `firme`; nuestro precio
    publicado → `nuestro_ml`; revisado a mano → `revisado`; mediana de los más vendidos de su
    categoría → `banda` (NO es el mismo producto). Con FOB, ninguna línea pasa de 10 × FOB × 19.
  · Las líneas con `excluir` son insumos: se cuentan, no se valúan.

Una línea se identifica con (archivo, fila de Excel). El archivo se reconoce por su sha256,
que es el mismo de `pl_indice.json`; la fila de Excel es la misma que usa `inventario_pl`.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

FUENTE = {"firme": "f", "nuestro_ml": "n", "revisado": "r", "banda": "b"}


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _n(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _csv(ruta: Path) -> list[dict[str, str]]:
    with open(ruta, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def cargar(carpeta: Path, indice: dict[str, Any]) -> dict[str, Any] | None:
    """Devuelve `None` si el paquete no está. Si está:

    lineas    {(id de archivo, fila de Excel): [líneas]}   un renglón repartido entre varios SKUs
              (caja compartida) son varias líneas con el mismo archivo y la misma fila
    por_sku   {SKU: [líneas]}
    precios   {id de precio: fila de precio de mercado}   (ya con la 2ª pasada)
    revisado  {id de precio: corrección a mano}
    resumen   sus totales
    """
    necesarios = ("contenedores_v2.csv", "valor_lineas.csv", "pm_precios_full.csv")
    if not all((carpeta / n).exists() for n in necesarios):
        return None
    id_de_sha = {a["sha256"]: a["id"] for a in indice.get("archivos") or [] if a.get("sha256")}

    precios = {p["sku"]: p for p in _csv(carpeta / "pm_precios_full.csv")}
    segunda = carpeta / "pm_precios_sd.csv"
    if segunda.exists():
        for q in _csv(segunda):
            s0 = q["sku"][5:] if q["sku"].startswith("desc:") else q["sku"]
            if s0 in precios and precios[s0]["nivel"] == "sin_dato" and q["nivel"] not in ("sin_dato", "pendiente"):
                precios[s0] = dict(q, sku=s0, precio_nuestro=precios[s0].get("precio_nuestro", ""),
                                   revisar=precios[s0].get("revisar", ""), segunda="1")
    revisado: dict[str, dict[str, Any]] = {}
    revision: dict[str, Any] = {}
    corr = carpeta / "correcciones_top50.json"
    if corr.exists():
        todas = json.loads(corr.read_text(encoding="utf-8"))
        for x in sorted(todas, key=lambda x: x.get("valor_antes") or 0):
            revisado[x["id_precio"]] = x          # si un id se revisó dos veces, manda la de mayor valor
        # Cuánto valían esas líneas con la banda y cuánto después de revisarlas: la medida de qué tan
        # calibrada está la banda. Se calcula de su archivo para no escribir la cifra en el código.
        antes = sum(_f(x.get("valor_antes")) for x in todas)
        despues = sum(_f(x.get("unidades_corregidas")) * _f(x.get("precio")) for x in todas)
        if antes > 0:
            revision = {"lineas": len(todas), "antes": round(antes), "despues": round(despues),
                        "baja": round(100 * (1 - despues / antes))}

    valor = {(v["contenedor"], v["archivo"], v["fila_excel"], v["id_precio"]): v
             for v in _csv(carpeta / "valor_lineas.csv")}
    lineas: dict[tuple[int, int], list[dict[str, Any]]] = {}
    por_sku: dict[str, list[dict[str, Any]]] = {}
    sin_archivo = 0
    for r in _csv(carpeta / "contenedores_v2.csv"):
        arch = id_de_sha.get(r.get("archivo_sha256") or "")
        v = valor.get((r["contenedor"], r["archivo"], r["fila_excel"], r["id_precio"])) or {}
        sku = (r.get("sku") or "").strip().upper()
        ln = {
            "arch": arch, "fila": int(_f(r["fila_excel"])), "cont": r["contenedor"], "sku": sku,
            "id": r["id_precio"], "desc": r.get("descripcion_original") or "",
            "pz": _f(r["piezas"]), "us": _f(r["piezas_usadas"]),
            # las unidades con que ÉL valuó (ya con el factor de la revisión); si no hay valuación, las del conteo
            "u": _f(v.get("unidades_sku")) if v else _f(r["unidades_sku"]),
            "fob": _f(r["valor_linea_usd"]), "bruto": _f(v.get("valor_mxn")), "d": _f(v.get("valor_depurado_mxn")),
            "fu": FUENTE.get(v.get("fuente_depurado") or "", ""), "ex": r.get("excluir") == "True",
        }
        if arch is None:
            sin_archivo += 1
        else:
            lineas.setdefault((arch, ln["fila"]), []).append(ln)
        if sku:
            por_sku.setdefault(sku, []).append(ln)
    resumen = {}
    if (carpeta / "valor_resumen.json").exists():
        resumen = json.loads((carpeta / "valor_resumen.json").read_text(encoding="utf-8"))
    return {"lineas": lineas, "por_sku": por_sku, "precios": precios, "revisado": revisado, "resumen": resumen,
            "revision": revision, "sin_archivo": sin_archivo}


def precio_de(lineas: list[dict[str, Any]], piezas: float, paquete: dict[str, Any], tc: float,
              piezas_cubiertas: float | None = None) -> dict[str, Any] | None:
    """El precio de Mercado Libre POR PIEZA de una fila de la página, a partir de las líneas que le tocan.

    `piezas` son las de la fila tal como las cuenta la página (bodega si hay validado, proveedor
    si no). Sus líneas cuentan UNIDADES vendibles. Para no multiplicar piezas por precio de paquete:

      1. precio por unidad suya = su valor depurado ÷ sus unidades (así entran sus topes y sus reglas);
      2. si las piezas de la fila son las de sus líneas (±25%), cada pieza vale precio × (unidades ÷ piezas):
         el valor de la fila es el suyo;
      3. si la fila ya está contada en unidades suyas (±25%), precio tal cual;
      4. si no coinciden (bodega contó otra cosa que el proveedor), se usa el precio por unidad y se
         aplica SU tope sobre el conteo de la página: la fila no vale más de 10 × el FOB de esas líneas.
         Es lo que frena el caso «el proveedor anotó 100 paquetes y bodega contó 10,000 piezas».

    El precio que se USA (`m`) es el PROMEDIO del marketplace: la media de las publicaciones (del mismo
    producto, o de los más vendidos de su categoría), llevada a pieza; si no hay publicaciones, su precio
    depurado. El depurado de Eduardo —mediana, o nuestro precio, con su tope de FOB— va aparte en `dp`.
    El tope del punto 4 se aplica a los dos: es una protección de CONTEO, no de precio.

    Devuelve `None` si esas líneas no tienen precio. `f` (su fuente): f exacto · n nuestro precio · r revisado
    · b banda. `cl` (de qué es la media): f del mismo producto · b de su categoría · r revisada a mano ·
    n no hubo publicaciones y se usa nuestro precio.
    """
    vivas = [l for l in lineas if not l["ex"]]
    con = [l for l in vivas if l["d"] > 0 and l["u"] > 0]
    if not con or piezas <= 0:
        return None
    unidades, valor = sum(l["u"] for l in con), sum(l["d"] for l in con)
    usadas = sum(l["us"] for l in con)
    por_unidad = valor / unidades
    # Se compara contra las piezas de la fila en los contenedores que SUS líneas cubren: si el SKU
    # llegó también en otro contenedor que él no tiene, eso no es contar distinto.
    base = piezas_cubiertas or piezas
    parejo = usadas > 0 and 0.8 <= base / usadas <= 1.25
    en_unidades = 0.8 <= base / unidades <= 1.25
    if parejo:
        g = unidades / usadas
    elif en_unidades:
        g = 1.0
    else:
        g = unidades / usadas if usadas > 0 else 1.0
    distinto = not parejo and not en_unidades
    precio = por_unidad * g
    peso: dict[str, float] = {}
    for l in con:
        peso[l["fu"]] = peso.get(l["fu"], 0.0) + l["d"]
    fu = max(peso, key=lambda k: peso[k])
    techo = None
    if distinto:
        fob = sum(l["fob"] for l in con)
        if fob > 0 and all(l["fob"] > 0 for l in con) and "r" not in peso:
            techo = 10.0 * fob * tc / base
            precio = min(precio, techo)

    # Las cifras de mercado (mín, media, máx…). Una fila puede juntar varios ids de precio (renglones
    # con el mismo nombre que él cotizó por separado): la media, la mediana y los cuartiles se promedian
    # por unidades; el mínimo y el máximo son los extremos. Publicaciones y categoría, las del id que más pesa.
    por_id: dict[str, float] = {}
    revisados = {l["id"] for l in con if l["fu"] == "r"}
    for l in con:
        por_id[l["id"]] = por_id.get(l["id"], 0.0) + l["u"]
    ident = max(por_id, key=lambda k: por_id[k])
    p = paquete["precios"].get(ident) or {}
    rev = paquete["revisado"].get(ident) if ident in revisados else None

    def cifras(i: str) -> dict[str, float] | None:
        r, q = paquete["revisado"].get(i), paquete["precios"].get(i) or {}
        if r and i in revisados:      # su precio y su rango ya son por unidad corregida, igual que sus unidades
            return {"me": r["precio"], "lo": min(r["min"], r["precio"]), "hi": max(r["max"], r["precio"])}
        med = _n(q.get("precio_mediana"))
        if med is None:
            return None
        return {"me": _n(q.get("media")) or med, "d": med, "lo": _n(q.get("min")) or med, "hi": _n(q.get("max")) or med,
                "a": _n(q.get("p25")) or med, "b": _n(q.get("p75")) or med}

    out: dict[str, Any] = {"o": "e", "f": fu, "dp": round(precio, 2)}
    c = lambda v: round(v * g, 2)  # noqa: E731
    juntas = [(w, x) for w, x in ((por_id[i], cifras(i)) for i in por_id) if x]
    if juntas:
        w_total = sum(w for w, _ in juntas)
        out.update(me=c(sum(w * x["me"] for w, x in juntas) / w_total), lo=c(min(x["lo"] for _, x in juntas)),
                   hi=c(max(x["hi"] for _, x in juntas)))
        for k in ("d", "a", "b"):
            if all(k in x for _, x in juntas):
                out[k] = c(sum(w * x[k] for w, x in juntas) / w_total)
    if len(por_id) > 1:
        out["ni"] = len(por_id)
    if rev:
        if rev.get("que_es"):
            out["rq"] = str(rev["que_es"])[:160]
        nominal = rev["precio"]
    else:
        med = _n(p.get("precio_mediana"))
        if med is not None and _f(p.get("n")):
            out["n"] = int(_f(p.get("n")))
            if _f(p.get("vendedores_distintos")):
                out["vd"] = int(_f(p["vendedores_distintos"]))
        nominal = _n(p.get("precio_nuestro")) if fu == "n" else med
    nivel = p.get("nivel") or ""
    out["nv"] = ("x" if nivel.startswith("exacto_modelo") else "e" if nivel.startswith("exacto")
                 else "a" if nivel == "rango_amplio" else "r" if nivel.startswith("rango") else "")
    if p.get("categoria_nombre"):
        out["cg"] = p["categoria_nombre"]
    nuestro = _n(p.get("precio_nuestro"))
    if nuestro:
        out["pn"] = c(nuestro)
    if p.get("revisar") == "true":
        out["rz"] = 1
    if p.get("segunda"):
        out["sg"] = 1
    if g < 0.97 and round(1 / g) >= 2 and abs(1 / g - round(1 / g)) <= 0.05 * (1 / g):
        out["pk"] = int(round(1 / g))
    # Su tope (10 × FOB) o su regla sin FOB (el p25) bajaron el precio contra el nominal de la fuente.
    if nominal and por_unidad < 0.97 * nominal:
        out["aj"] = 1
    # El precio que se usa: la media. Y de qué es esa media.
    media = out.get("me")
    out["cl"] = ("r" if rev else "f" if out["nv"] in ("e", "x") else "b") if media is not None else fu
    usado = media if media is not None else out["dp"]
    if techo is not None and usado > techo:
        usado, out["tp"] = techo, 1
    out["m"] = round(usado, 2)
    if distinto:
        out["ue"] = round(unidades, 1)            # él cuenta otra cantidad para lo mismo
        fob = sum(l["fob"] for l in con)
        if fob > 0 and all(l["fob"] > 0 for l in con):
            out["cf"] = round(fob * tc / base, 2)   # lo que costó cada pieza TAL COMO AQUÍ SE CUENTA
    out["ln"] = len(con)
    return out
