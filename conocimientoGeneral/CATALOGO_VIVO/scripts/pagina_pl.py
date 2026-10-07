"""
pagina_pl.py — Arma la página del inventario visto desde los packing lists.

Junta lo que dejaron las etapas (`inventario`, `titulos`, `categorias_ml`, `mercado_ml`,
`mercado_amazon`) en un solo `datos_pl.json`, y escribe la página. No pregunta nada a
ningún sistema: solo lee archivos de `datos/`.

Una FILA es una de dos cosas:
  · un SKU (con o sin packing list ubicado), o
  · un producto de packing list al que nadie le puso SKU: todos los renglones con el
    mismo nombre, juntos. Su llave empieza con `PL:`.

El precio de mercado de cada fila dice de dónde salió:
  ML      `p`  rivales que el juez de producción marcó como el mismo producto, para ESE SKU
          `g`  lo mismo, de otra variante del mismo modelo
          `c`  catálogo de ML + juez de esta carpeta
  Amazon  `k`  búsqueda por palabra clave + caja de compra + juez
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from comun import AQUI, ahora_iso, aviso, escribir_json, leer_json, sku_norm
from ia_titulos import clave_renglon
from inventario_pl import ROTULO_CLASE

ALIAS_RAIZ = {"Mascotas": "Animales y Mascotas"}
LADO, COLS, POR_HOJA = 72, 20, 400


def _ruta(camino: list[str]) -> tuple[str, str, str]:
    c = [str(x or "").strip() for x in camino if x]
    c = [ALIAS_RAIZ.get(c[0], c[0])] + c[1:] if c else []
    return (c[0] if c else "", c[1] if len(c) > 1 else "", c[-1] if len(c) > 2 else "")


RE_PAQUETE = re.compile(r"\b(set|juego|kit|paquete|paq|pack|par|pares|caja de|bolsa de)\b", re.I)


def divisor_por_pieza(nombre: str, unidades: int) -> int:
    """Entre cuánto se parte el precio de referencia para que quede POR PIEZA de inventario.

    El precio de referencia es el de NUESTRO paquete tal como se publica («Paquete 5 focos»).
    Si el nombre con que se compró y se cuenta no habla de paquete ni trae ese número («A
    bulb»), el inventario cuenta piezas sueltas: multiplicar piezas por el precio del
    paquete lo infla N veces. Se parte entre N. Si el nombre sí dice «set de 3», el
    inventario cuenta sets y no se toca."""
    if unidades <= 1:
        return 1
    n = nombre or ""
    if RE_PAQUETE.search(n) or re.search(rf"(?<!\d){unidades}(?!\d)", n):
        return 1
    return unidades


def _precio(g: dict[str, Any] | None, origen: str, entre: int = 1) -> dict[str, Any] | None:
    if not g or not g.get("n"):
        return None
    c = lambda v: round(v / entre, 2)  # noqa: E731
    p = {"m": c(g["media"]), "d": c(g["mediana"]), "a": c(g["p25"]), "b": c(g["p75"]), "lo": c(g["min"]),
         "hi": c(g["max"]), "n": g["n"], "o": origen}
    if entre > 1:
        p["pk"] = entre
    if g.get("aprox"):
        p["x"] = 1
    if g.get("ej"):
        p["ej"] = [[e["t"], e["p"], e.get("id") or ""] for e in g["ej"][:3]]
    if g.get("q"):
        p["q"] = g["q"]
    return p


def _hojas(fotos: list[Path], destino: Path, prefijo: str) -> int:
    """Hojas de mosaicos de 72 px. Devuelve cuántas escribió."""
    from PIL import Image

    destino.mkdir(parents=True, exist_ok=True)
    for viejo in destino.glob(f"{prefijo}*.jpg"):
        viejo.unlink()
    n = 0
    for h in range(0, len(fotos), POR_HOJA):
        trozo = fotos[h:h + POR_HOJA]
        filas = (len(trozo) + COLS - 1) // COLS
        lienzo = Image.new("RGB", (COLS * LADO, filas * LADO), (255, 255, 255))
        for i, ruta in enumerate(trozo):
            try:
                im = Image.open(ruta).convert("RGB").resize((LADO, LADO))
            except Exception:  # noqa: BLE001
                continue
            lienzo.paste(im, ((i % COLS) * LADO, (i // COLS) * LADO))
        lienzo.save(destino / f"{prefijo}{n:03d}.jpg", "JPEG", quality=70, optimize=True, progressive=True)
        n += 1
    return n


def construir(salida: Path) -> dict[str, Any]:
    d = salida / "datos"
    inv = leer_json(d / "inventario_pl.json")
    if not inv:
        raise RuntimeError("falta datos/inventario_pl.json: corre la etapa `inventario`")
    titulos = (leer_json(d / "titulos.json") or {}).get("skus") or {}
    pred = (leer_json(d / "categorias_pred.json") or {}).get("por_titulo") or {}
    ml = leer_json(d / "mercado_ml.json") or {"grupos": {}, "sku": {}, "produccion": {}}
    amz = leer_json(d / "mercado_amazon.json") or {"grupos": {}, "sku": {}}
    cat = leer_json(salida / "datos.json") or {"P": [], "K": []}
    indice = leer_json(d / "pl_indice.json") or {}
    de_cat = {sku_norm(r["s"]): r for r in cat["P"]}
    rutas_cat = cat.get("K") or []

    rutas: dict[tuple[str, str, str], int] = {}
    lista_rutas: list[list[str]] = []

    def k_de(camino: list[str]) -> int | None:
        r = _ruta(camino)
        if not r[0]:
            return None
        if r not in rutas:
            rutas[r] = len(lista_rutas)
            lista_rutas.append(list(r))
        return rutas[r]

    def categoria(llave: str, fila_cat: dict[str, Any] | None, titulo: str) -> tuple[int | None, str]:
        """(índice, calidad): d directa · n por nombre · h heredada · r predictor de ML · p por prefijo."""
        if fila_cat and "k" in fila_cat and fila_cat.get("kq") != "p":
            return k_de(rutas_cat[fila_cat["k"]]), fila_cat.get("kq") or "d"
        p = pred.get(re.sub(r"\s+", " ", titulo or "").strip().lower())
        if p and p.get("ruta"):
            return k_de(p["ruta"]), "r"
        if fila_cat and "k" in fila_cat:
            return k_de(rutas_cat[fila_cat["k"]]), "p"
        return None, ""

    # Precio de ML por modelo: si una variante tiene rivales medidos por producción, sirven a sus hermanas.
    prod = ml.get("produccion") or {}
    por_grupo_prod: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sku, clave in (ml.get("sku") or {}).items():
        if sku_norm(sku) in prod:
            por_grupo_prod[clave].append(prod[sku_norm(sku)])

    def precio_ml(llave: str, entre: int = 1) -> dict[str, Any] | None:
        if llave in prod:
            return _precio(prod[llave], "p", entre)
        clave = (ml.get("sku") or {}).get(llave)
        hermanas = por_grupo_prod.get(clave) if clave else None
        if hermanas:
            n = sum(h["n"] for h in hermanas)
            media = sum(h["media"] * h["n"] for h in hermanas) / n
            junto = {"media": media, "mediana": sorted(h["mediana"] for h in hermanas)[len(hermanas) // 2],
                     "p25": min(h["p25"] for h in hermanas), "p75": max(h["p75"] for h in hermanas),
                     "min": min(h["min"] for h in hermanas), "max": max(h["max"] for h in hermanas), "n": n}
            return _precio(junto, "g", entre)
        return _precio((ml.get("grupos") or {}).get(clave), "c", entre) if clave else None

    def precio_amz(llave: str, entre: int = 1) -> dict[str, Any] | None:
        clave = (amz.get("sku") or {}).get(llave)
        return _precio((amz.get("grupos") or {}).get(clave), "k", entre) if clave else None

    filas: list[dict[str, Any]] = []
    for sku, x in inv["skus"].items():
        c = de_cat.get(sku)
        t = titulos.get(sku) or {}
        fila: dict[str, Any] = {"s": sku, "t": t.get("t") or (c or {}).get("n") or sku}
        nombre = (c or {}).get("n") or ""
        if nombre and nombre != fila["t"]:
            fila["n"] = nombre[:110]
        if t.get("f"):
            fila["tf"] = t["f"]
        if t.get("d"):
            fila["td"] = 1
        k, kq = categoria(sku, c, fila["t"])
        if k is not None:
            fila["k"], fila["kq"] = k, kq
        if c and "i" in c:
            fila["i"] = c["i"]
        if "pl" in x:
            fila["pc"] = x["pl"]
            if "prov" in x:
                fila["pp"] = x["prov"]
            fila["pq"] = x["q"]
            if x.get("so"):
                fila["so"] = 1
            if x.get("de_mas"):
                fila["dm"] = x["de_mas"]
            fila["ct"], fila["cm"], fila["rn"] = x["conts"], x["como"], x["ren"]
            if x.get("ren_mas"):
                fila["rm"] = x["ren_mas"]
        elif "est" in x:
            fila["pe"], fila["ec"] = x["est"], x["est_c"]
        for a, b in (("sal", "ps"), ("cls", "cl"), ("lib", "lb"), ("ent", "en"), ("aj", "aj"), ("v1", "v1"), ("v2", "v2")):
            if x.get(a):
                fila[b] = x[a]
        if c and "pv" in c and not c.get("rv"):
            fila["v"] = c["pv"]
        entre = divisor_por_pieza(nombre, int(t.get("u") or 1))
        p = precio_ml(sku, entre)
        if p:
            fila["ml"] = p
        p = precio_amz(sku, entre)
        if p:
            fila["az"] = p
        filas.append(fila)

    # Productos de packing list sin SKU: los renglones con el mismo nombre, juntos.
    cache = Path(indice.get("cache") or "")
    juntos: dict[str, dict[str, Any]] = {}
    for r in inv["renglones_sin_sku"]:
        llave = clave_renglon(r.get("t") or "") or f"PL:SIN-NOMBRE-{r['c']}"
        j = juntos.setdefault(llave, {"s": llave, "n": r.get("t") or "(renglón sin nombre)", "pc": 0, "ct": {}, "nr": 0,
                                      "rn": [], "foto": None, "usd": 0})
        j["pc"] += r["pz"]
        j["ct"][r["c"]] = j["ct"].get(r["c"], 0) + r["pz"]
        j["nr"] += 1
        if len(j["rn"]) < 6:
            j["rn"].append({"c": r["c"], ("av" if r.get("o") == "v" else "ao"): r["a"],
                            ("fv" if r.get("o") == "v" else "fo"): r["f"] if r.get("o") == "v" else [r["f"]]})
        if not j["usd"] and r.get("usd"):
            j["usd"] = r["usd"]
        if j["foto"] is None and r.get("foto") and cache:
            ruta = cache / "thumbs" / f"{r.get('o', 'o')}_{r['a']}_{r['f'] - 1}.jpg"
            if ruta.exists():
                j["foto"] = ruta
    fotos_pl: list[Path] = []
    for llave, j in juntos.items():
        t = titulos.get(llave) or {}
        fila = {"s": llave, "t": t.get("t") or j["n"], "sin": 1, "pc": j["pc"], "pq": j["pc"], "ct": j["ct"],
                "nr": j["nr"], "rn": j["rn"]}
        if j["n"] != fila["t"]:
            fila["n"] = j["n"][:110]
        if t.get("d"):
            fila["td"] = 1
        if j["usd"]:
            fila["usd"] = j["usd"]
        k, kq = categoria(llave, None, fila["t"])
        if k is not None:
            fila["k"], fila["kq"] = k, kq
        if j["foto"] is not None:
            fila["j"] = len(fotos_pl)
            fotos_pl.append(j["foto"])
        entre = divisor_por_pieza(j["n"], int(t.get("u") or 1))
        p = precio_ml(llave, entre)
        if p:
            fila["ml"] = p
        p = precio_amz(llave, entre)
        if p:
            fila["az"] = p
        filas.append(fila)
    hojas_pl = _hojas(fotos_pl, salida / "img", "p") if fotos_pl else 0

    # Lo que salió, repartido a cada contenedor en proporción a las piezas que cada uno trajo.
    sal_cont: dict[str, float] = defaultdict(float)
    for f in filas:
        if f.get("ps") and f.get("ct"):
            total = sum(f["ct"].values()) or 1
            for n, pz in f["ct"].items():
                sal_cont[str(n)] += min(f["ps"], f.get("pc", 0)) * pz / total
    conts = []
    for c in inv["contenedores"]:
        conts.append({**{k: c[k] for k in ("n", "clave", "codigos", "orig", "val", "pz", "pz_orig", "pz_val", "skus")},
                      "sin": sum(f["ct"].get(c["clave"], 0) for f in filas if f.get("sin")),
                      "sal": round(sal_cont.get(c["clave"], 0))})

    r = inv["resumen"]
    grupos_amz, hechas_amz = len(set((amz.get("sku") or {}).values())), len(amz.get("grupos") or {})
    doc = {
        "generado": ahora_iso(), "fuentes": inv["fuentes"], "resumen": r, "rot": ROTULO_CLASE,
        "K": lista_rutas, "A": inv["archivos"], "C": conts,
        "sprite": cat.get("sprite") or {}, "spritePL": {"lado": LADO, "cols": COLS, "por_hoja": POR_HOJA, "hojas": hojas_pl},
        "mercado": {
            "ml": {"produccion": len(prod), "produccion_leido": ml.get("produccion_leido"),
                   "grupos": len(ml.get("grupos") or {}), "actualizado": ml.get("actualizado")},
            "amazon": {"grupos": grupos_amz, "juzgados": hechas_amz,
                       "palabras": len(amz.get("busquedas") or {}), "actualizado": amz.get("actualizado")},
        },
        "R": filas,
    }
    escribir_json(salida / "datos_pl.json", doc)
    (salida / "datos_pl.js").write_text(
        "window.INVENTARIO=" + (salida / "datos_pl.json").read_text(encoding="utf-8") + ";", encoding="utf-8")

    base = (AQUI / "plantilla.html").read_text(encoding="utf-8")
    estilo = base[:base.index("</style>") + len("</style>")]        # título, tipografías y el mismo sistema visual
    pagina = estilo + "\n" + (AQUI / "plantilla_pl.html").read_text(encoding="utf-8")
    (salida / "pagina_pl.html").write_text(pagina, encoding="utf-8")
    envuelta = ('<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n'
                '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                "<style>body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n"
                "</head>\n<body>\n" + pagina + "\n</body>\n</html>\n")
    (salida / "index_pl.html").write_text(envuelta, encoding="utf-8")
    con = lambda campo: sum(1 for f in filas if campo in f)  # noqa: E731
    aviso(f"página PL: {len(filas)} filas ({sum(1 for f in filas if f.get('sin'))} sin SKU) · "
          f"con precio ML {con('ml')} · con precio Amazon {con('az')} · {len(lista_rutas)} categorías · "
          f"{hojas_pl} hojas de fotos de packing list")
    return {"filas": len(filas), "sin_sku": sum(1 for f in filas if f.get("sin")), "con_precio_ml": con("ml"),
            "con_precio_amazon": con("az"), "categorias": len(lista_rutas), "hojas_pl": hojas_pl}
