"""
catalogo_mayoreo.py — Los DATOS del catálogo para tiendas de mayoreo, como archivos.

Deja en `<salida>/mayoreo/` lo que el módulo del catálogo sirve tal cual (el módulo vive aparte, en
la rama `catalogo-mayoreo`, carpeta `catalogo_mayoreo/`; esta etapa no lo toca ni le manda nada):

    catalogo.json       lo que la tienda puede ver: SKU, título, categoría, precio, piezas y foto
    fotos/mNNN.jpg      las fotos, en hojas de 32

Solo lee. De `datos_pl.json` (la página del inventario) salen título, categoría, precio y foto; de
Odoo —una lectura nueva, por XML-RPC, que se guarda en `datos/odoo_mayoreo.json` sin tocar la del
inventario— salen las piezas de hoy.

QUÉ ENTRA (decisiones de Brandon, 12-oct-2026)
  · Lo que Odoo tiene LIBRE en TEXCO y TEXCO II (`free_qty`): esas piezas se pueden apartar con una
    orden de venta. DROP OFF no cuenta: de ahí no se surten órdenes (lo mismo que `odoo_ventas`).
  · Lo que NO EXISTE en Odoo pero según los packing lists todavía queda: también se puede pedir. No
    se puede apartar en Odoo, así que el módulo lo deja «por confirmar» y avisa en Slack.
  Queda fuera, a propósito: lo que existe en Odoo con cero libre (Odoo manda en el inventario), los
  insumos, lo que no tiene SKU y cualquier cosa de costos, proveedor o contenedor.

EL PRECIO es la media de Mercado Libre de la página del inventario, la misma del Excel: del producto
cuando la hay (`p`), de su categoría cuando no (`c`), o estimada (`e`). Si existe
`datos/precios_v3/precios_catalogo_v3.json` (el paquete de Competencia que quita las marcas de
renombre y corrige categorías), ese precio manda en las filas que trae (`k`): ver `precio_v3`.

Las fotos repetidas (veinte tallas con la misma foto) se guardan una sola vez, y van en el orden del
catálogo: abrir una categoría baja pocas hojas.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from comun import Cfg, aviso, leer_json

LADO = 256                       # píxeles de cada foto (el de las hojas de origen)
COLS, FILAS = 8, 4               # 32 fotos por hoja: una categoría chica cabe en una o dos
CALIDAD = 82
ALMACENES = ("TEXCO", "TEX2")    # de dónde se surte una orden de venta; DROP queda fuera


def leer_odoo(cfg: Cfg, salida: Path) -> dict[str, Any]:
    """Una lectura nueva de Odoo (productos activos y su libre por almacén), aparte de la del inventario."""
    import f_odoo

    with tempfile.TemporaryDirectory() as tmp:
        f_odoo.extraer(cfg, Path(tmp), con_fotos=False)
        shutil.copyfile(Path(tmp) / "datos" / "odoo.json", salida / "datos" / "odoo_mayoreo.json")
    return leer_json(salida / "datos" / "odoo_mayoreo.json")


def libre_surtible(odoo: dict[str, Any]) -> dict[str, int]:
    """{sku: piezas libres en TEXCO + TEXCO II}. Están TODOS los SKUs de Odoo, también los que tienen cero."""
    libre: dict[str, int] = {}
    for f in odoo["filas"]:
        sku = (f.get("sku") or "").strip()
        if sku:
            por = f.get("por_almacen") or {}
            libre[sku] = libre.get(sku, 0) + sum(max(0, int(por.get(a) or 0)) for a in ALMACENES)
    return libre


def media_ml(r: dict[str, Any]) -> tuple[float | None, str | None]:
    """La media de Mercado Libre que la página del inventario pone en su columna «media», y de qué es."""
    p = r.get("ml")
    if p and p.get("o") == "e":
        valor = p["me"] if p.get("lo") is not None else p["m"]
        return valor, ("p" if p.get("cl") in ("f", "r", "n") else "c")
    if p and not r.get("sin") and p.get("n", 0) >= 2 and not p.get("x") and p.get("o") != "c":
        return p["m"], "p"
    if r.get("em"):
        return r["em"][0], "e"
    return None, None


def precio_v3(actual: tuple[float | None, str | None], fila: dict[str, Any] | None) -> tuple[float | None, str | None]:
    """
    El precio del paquete «precios de Mercado Libre v3» (Competencia, 12-oct-2026), si la fila viene en él.

    Ese paquete rehace el precio de mercado sin marcas de renombre y con la categoría corregida (unos
    tenis genéricos bajan de $720.97 a $249.50). Sus reglas, tal como las pide su LEEME:
      · `exacto` (precio exacto, el nuestro publicado o revisado a mano): no se toca;
      · con `precio_ml_v3`: ese es el precio (tipo `k`);
      · sin fuentes: su `estimado_costo` si lo trae (tipo `e`); si no, la fila queda SIN precio y el
        catálogo no la deja pedir.
    """
    if fila is None or fila.get("fuente") == "exacto":
        return actual
    if fila.get("precio_ml_v3"):
        return fila["precio_ml_v3"], "k"
    if fila.get("estimado_costo"):
        return fila["estimado_costo"], "e"
    return None, None


def elegir(doc: dict[str, Any], libre: dict[str, int], v3: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Los productos del catálogo: lo libre en Odoo y lo que no existe en Odoo pero queda en packing lists."""
    rutas = doc["K"]
    v3 = v3 or {}
    salida = []
    for r in doc["R"]:
        if r.get("sin") or r.get("nv"):
            continue
        sku = r["s"]
        if sku in libre:
            piezas, odoo = libre[sku], 1
        else:
            piezas, odoo = int(r.get("pq") or 0), 0
        if piezas <= 0:
            continue
        ruta = rutas[r["k"]] if r.get("k") is not None else ["Sin categoría", ""]
        precio, tipo = precio_v3(media_ml(r), v3.get(sku))
        salida.append({"sku": sku, "titulo": re.sub(r"\s+", " ", r.get("t") or sku).strip(),
                       "cat": ruta[0], "sub": ruta[1] or "(sin subcategoría)", "modelo": "-".join(sku.split("-")[:2]),
                       "precio": round(float(precio), 2) if precio else None, "tipo": tipo,
                       "piezas": piezas, "odoo": odoo,
                       "origen": ("g", r["i"]) if r.get("i") is not None else (("q", r["j"]) if r.get("j") is not None else None)})
    return salida


def ordenar(productos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Categorías y subcategorías de la más surtida a la menos; dentro, por modelo y SKU."""
    por_cat = Counter(p["cat"] for p in productos)
    por_sub = Counter((p["cat"], p["sub"]) for p in productos)
    return sorted(productos, key=lambda p: (-por_cat[p["cat"]], p["cat"], -por_sub[p["cat"], p["sub"]], p["sub"],
                                            p["modelo"], p["sku"]))


def armar_fotos(salida: Path, destino: Path, productos: list[dict[str, Any]], doc: dict[str, Any]) -> dict[str, int]:
    """Recorta la foto de cada producto de las hojas del inventario y las vuelve a acomodar en hojas propias."""
    from PIL import Image

    hojas_origen: dict[tuple[str, int], Any] = {}

    def recorte(pre: str, idx: int) -> Any:
        sp = doc["spriteG" if pre == "g" else "spriteQ"]
        n_hoja, n = divmod(idx, sp["por_hoja"])
        if (pre, n_hoja) not in hojas_origen:
            if len(hojas_origen) > 6:                       # las hojas pesan: no se quedan todas abiertas
                hojas_origen.pop(next(iter(hojas_origen)))
            hojas_origen[pre, n_hoja] = Image.open(salida / "img" / f"{pre}{n_hoja:03d}.jpg").convert("RGB")
        x, y = (n % sp["cols"]) * sp["lado"], (n // sp["cols"]) * sp["lado"]
        img = hojas_origen[pre, n_hoja].crop((x, y, x + sp["lado"], y + sp["lado"]))
        return img if sp["lado"] == LADO else img.resize((LADO, LADO), Image.LANCZOS)

    (destino / "fotos").mkdir(parents=True, exist_ok=True)
    for viejo in (destino / "fotos").glob("m*.jpg"):
        viejo.unlink()
    por_hoja = COLS * FILAS
    vistas: dict[str, int] = {}                             # huella de la foto → su lugar
    pendientes: list[Any] = []
    n_hojas = 0

    def cerrar_hoja() -> None:
        nonlocal n_hojas
        if not pendientes:
            return
        hoja = Image.new("RGB", (COLS * LADO, FILAS * LADO), (238, 239, 234))
        for i, img in enumerate(pendientes):
            hoja.paste(img, ((i % COLS) * LADO, (i // COLS) * LADO))
        hoja.save(destino / "fotos" / f"m{n_hojas:03d}.jpg", "JPEG", quality=CALIDAD, optimize=True)
        n_hojas += 1
        pendientes.clear()

    for p in productos:
        if p["origen"] is None:
            p["foto"] = -1
            continue
        img = recorte(*p["origen"])
        huella = hashlib.md5(img.tobytes()).hexdigest()
        if huella not in vistas:
            vistas[huella] = len(vistas)
            pendientes.append(img)
            if len(pendientes) == por_hoja:
                cerrar_hoja()
        p["foto"] = vistas[huella]
    cerrar_hoja()
    return {"lado": LADO, "cols": COLS, "por_hoja": por_hoja, "hojas": n_hojas, "fotos": len(vistas)}


def documento(productos: list[dict[str, Any]], fotos: dict[str, int], corte: str) -> dict[str, Any]:
    """`catalogo.json`: compacto (9 mil productos caben en ~1 MB) y sin nada que la tienda no deba ver."""
    rutas: dict[tuple[str, str], int] = {}
    filas = []
    for p in productos:
        k = rutas.setdefault((p["cat"], p["sub"]), len(rutas))
        filas.append([p["sku"], p["titulo"], k, p["precio"], p["tipo"], p["piezas"], p["foto"], p["odoo"]])
    ahora = datetime.now().astimezone()
    return {"version": ahora.strftime("%Y%m%d-%H%M%S"), "corte": corte, "generado": ahora.isoformat(timespec="seconds"),
            "moneda": "MXN", "fotos": fotos,
            "columnas": ["sku", "titulo", "k", "precio", "tipo_precio", "piezas", "foto", "en_odoo"],
            "K": [list(k) for k in rutas], "P": filas}


def comprobar(cat: dict[str, Any]) -> dict[str, Any]:
    """Lo que no puede salir mal en un archivo público: SKUs repetidos, piezas en cero y campos de más."""
    skus = [f[0] for f in cat["P"]]
    repetidos = [s for s, n in Counter(skus).items() if n > 1]
    if repetidos:
        raise RuntimeError(f"catálogo de mayoreo: SKUs repetidos ({repetidos[:5]})")
    if any(f[5] <= 0 for f in cat["P"]):
        raise RuntimeError("catálogo de mayoreo: hay productos con cero piezas")
    de_mas = set(cat) - {"version", "corte", "generado", "moneda", "fotos", "columnas", "K", "P"}
    if de_mas or any(len(f) != 8 for f in cat["P"]):
        raise RuntimeError(f"catálogo de mayoreo: el archivo trae campos que no se revisaron ({sorted(de_mas)})")
    modelos = Counter("-".join(s.split("-")[:2]) for s in skus)
    odoo = [f for f in cat["P"] if f[7]]
    return {"productos": len(skus), "piezas": sum(f[5] for f in cat["P"]),
            "apartables_en_odoo": len(odoo), "piezas_en_odoo": sum(f[5] for f in odoo),
            "por_confirmar": len(skus) - len(odoo), "piezas_por_confirmar": sum(f[5] for f in cat["P"] if not f[7]),
            "sin_precio": sum(1 for f in cat["P"] if f[3] is None), "sin_foto": sum(1 for f in cat["P"] if f[6] < 0),
            "categorias": len({k[0] for k in cat["K"]}), "subcategorias": len(cat["K"]),
            "modelos": len(modelos), "modelos_con_variantes": sum(1 for n in modelos.values() if n > 1)}


def construir(cfg: Cfg, salida: Path, releer_odoo: bool = True) -> dict[str, Any]:
    doc = leer_json(salida / "datos_pl.json")
    if not doc:
        raise RuntimeError("falta datos_pl.json: corre antes las etapas del inventario")
    odoo = leer_odoo(cfg, salida) if releer_odoo else leer_json(salida / "datos" / "odoo_mayoreo.json")
    if not odoo:
        raise RuntimeError("no hay lectura de Odoo para el catálogo de mayoreo (datos/odoo_mayoreo.json)")
    v3 = (leer_json(salida / "datos" / "precios_v3" / "precios_catalogo_v3.json") or {}).get("filas") or {}
    productos = ordenar(elegir(doc, libre_surtible(odoo), v3))
    destino = salida / "mayoreo"
    destino.mkdir(parents=True, exist_ok=True)
    fotos = armar_fotos(salida, destino, productos, doc)
    corte = datetime.fromisoformat(odoo["leido_hasta"]).astimezone().date().isoformat()
    cat = documento(productos, fotos, corte)
    resumen = comprobar(cat)
    (destino / "catalogo.json").write_text(json.dumps(cat, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    peso = sum(f.stat().st_size for f in (destino / "fotos").glob("m*.jpg"))
    resumen.update(hojas=fotos["hojas"], fotos=fotos["fotos"], mb_fotos=round(peso / 1e6, 1), corte=corte, version=cat["version"],
                   precio_v3=sum(1 for p in productos if p["tipo"] == "k"), filas_en_v3=len(v3),
                   valor_catalogo=round(sum(p["piezas"] * p["precio"] for p in productos if p["precio"])))
    aviso(f"catálogo de mayoreo → {destino} · " + " · ".join(f"{k} {v:,}" if isinstance(v, int) else f"{k} {v}" for k, v in resumen.items()))
    return resumen
