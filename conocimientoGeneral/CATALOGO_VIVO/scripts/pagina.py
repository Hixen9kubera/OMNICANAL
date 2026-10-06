"""
pagina.py — Cruza todo por SKU y arma la página.

Entra lo que dejaron los extractores en `datos/`; sale:

    index.html      la página, autocontenida (se abre con doble clic)
    pagina.html     el mismo contenido sin envoltura, para publicarlo como artefacto
    datos.json/.js  el catálogo cruzado (la página lo carga de cualquiera de los dos)
    img/hNNN.jpg    las fotos, en hojas

EL CRUCE ES POR SKU, sin distinguir mayúsculas. La fila la pone ODOO: una por cada
`product.product` con referencia interna. Una publicación cuyo SKU no existe en
Odoo no se pierde: va a la lista de «publicaciones sin producto en Odoo».

EL COSTO DE PRODUCTO (el oficial, decisión de Brandon del 6-oct-2026) sale de
`costing.costos_validados.costo_producto`, y cuando el SKU no tiene renglón propio
se busca en este orden, SIEMPRE diciendo de dónde salió:

    b  base          renglón propio en la base                        ← el oficial
    h  heredado      de una variante hermana, del padre o de las hijas en la base
    x  extrapolado   mediana del costo de producto de su contenedor en la base
    —  sin costo     no se inventa

Solo `b` entra al total oficial. Lo demás se suma APARTE, con su nombre.

LA FICHA DE ODOO NO ES FUENTE DE COSTO, y no por dogma: se midió el 6-oct-2026.
`standard_price` coincide con la base (× 19) en 2,640 de 3,636 productos, pero en
los «Productos Agente» viene en otra unidad: usarla para los 601 SKUs sin renglón
propio valuaba esos pocos productos en más que todo el resto del catálogo junto. Se
muestra en el detalle como dato de la ficha y nada más.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from comun import AQUI, ahora_iso, aviso, escribir_json, leer_json, num, sku_norm

CANALES = (
    # clave, archivo, nombre visible, comisión supuesta cuando el canal no la da por API
    ("mk", "ml", "ML Kubera", None),
    ("ms", "ml", "ML San Corpe", None),
    ("az", "amazon", "Amazon", 0.15),
    ("tt", "tiktok", "TikTok", 0.08),
    ("tm", "temu", "Temu", 0.0),
    ("wm", "walmart", "Walmart", 0.15),
)
CUENTA_ML = {"BEKURA": "mk", "SANCORFASHION": "ms"}
ESTADOS_FUERA = {"DELETED"}          # TikTok conserva lo borrado; no es una publicación

RE_COD = re.compile(r"([A-Z]{4}\d{6,7}|SZLS\d{6,9}|[A-Z]{4}[A-Z0-9]{8,14}|\d{9,12})")
RE_ORD = re.compile(r"(?:CONTENEDOR|CONTAINER|CONT)\.?\s*#?\s*(\d{1,3})\b")
RE_ORD_FIN = re.compile(r"[-\s](\d{1,3})\s*$")
RE_COPIA = re.compile(r"^X+-", re.I)


def _plano(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").replace("\xa0", " ")).strip()


def codigos(texto: str | None) -> set[str]:
    return set(RE_COD.findall(_plano(texto or "").upper()))


def ordinal(texto: str | None) -> int | None:
    """El número de contenedor («contenedor 7», «… - 7»): la llave más estable."""
    t = _plano(texto or "").upper()
    m = RE_ORD.search(t) or RE_ORD_FIN.search(t)
    return int(m.group(1)) if m else None


def _moda(valores: list[float]) -> float:
    c = Counter(round(v, 2) for v in valores)
    tope = max(c.values())
    return statistics.median(sorted(v for v, n in c.items() if n == tope))


def _archivos_drive(repo: Path) -> dict[str, str]:
    """{file_id: nombre} de la carpeta de packing lists, si el repo vecino la trae."""
    return leer_json(repo / "backend" / "services" / "data" / "packing_lists_drive.json", {}) or {}


def _fila_pub(clave: str, f: dict[str, Any], img: dict[str, int], supuesta: float | None) -> dict[str, Any]:
    precio = f.get("precio_cobrado") or f.get("precio")
    p: dict[str, Any] = {"k": clave, "id": f.get("id"), "t": f.get("titulo"), "p": precio,
                         "e": f.get("estado")}
    regular = f.get("precio_regular") or f.get("precio_original")
    if regular and precio and abs(regular - precio) > 0.005:
        p["r"] = regular
    if f.get("a_la_venta"):
        p["v"] = 1
    if f.get("link"):
        p["u"] = f["link"]
    if f.get("imagen") and f["imagen"] in img:
        p["i"] = img[f["imagen"]]
    com = f.get("comision") or {}
    if com.get("total") is not None:
        p["cm"] = round(com["total"], 2)
        det = [[d["concepto"], d["monto"]] for d in com.get("detalle") or []]
        if com.get("porcentaje") is not None:
            det.append([f"{com['porcentaje']:g}% del precio", None])
        if com.get("fijo"):
            det.append(["cargo fijo", com["fijo"]])
        if det:
            p["cx"] = det
    elif supuesta is not None and precio:
        p["cm"] = round(precio * supuesta, 2)
        p["cs"] = supuesta                      # comisión SUPUESTA: el canal no la da por API
    env = f.get("envio") or {}
    if env.get("costo") is not None:
        p["en"] = round(env["costo"], 2)
    if f.get("es_full"):
        p["lg"] = "FULL"
    elif f.get("es_fba"):
        p["lg"] = "FBA"
    if f.get("stock_canal") is not None:
        p["st"] = f["stock_canal"]
    elif f.get("stock_fba") is not None or f.get("stock_propio") is not None:
        p["st"] = f.get("stock_fba") if f.get("es_fba") else f.get("stock_propio")
    if f.get("promocion"):
        p["pm"] = f["promocion"]
    if f.get("subestado"):
        p["e"] = f"{p['e']} · {', '.join(f['subestado'])}"
    return {k: v for k, v in p.items() if v is not None}


def construir(salida: Path) -> dict[str, Any]:
    datos = salida / "datos"
    odoo = leer_json(datos / "odoo.json")
    if not odoo:
        raise RuntimeError("falta datos/odoo.json: corre primero la etapa `odoo`")
    costos = leer_json(datos / "costos.json") or {}
    base: dict[str, dict[str, Any]] = costos.get("filas") or {}
    imagenes = leer_json(datos / "imagenes.json") or {}
    img_odoo: dict[str, int] = imagenes.get("odoo") or {}
    img_url: dict[str, int] = imagenes.get("url") or {}
    filas_odoo: list[dict[str, Any]] = odoo["filas"]

    # ── Publicaciones por SKU ────────────────────────────────────────────────
    fuentes: dict[str, Any] = {
        "odoo": {"ok": True, "nombre": "Odoo", "leido": odoo.get("leido_hasta"),
                 "detalle": odoo.get("fuente"), "filas": len(filas_odoo)},
        "costos": {"ok": bool(base), "nombre": "Base de costos", "leido": costos.get("leido_at"),
                   "detalle": costos.get("fuente"), "filas": len(base)},
    }
    pubs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    conteo: dict[str, dict[str, int]] = {c[0]: {"pubs": 0, "venta": 0} for c in CANALES}
    fuera = Counter()
    sin_sku: list[dict[str, Any]] = []
    for clave, archivo, nombre, supuesta in CANALES:
        doc = leer_json(datos / f"{archivo}.json")
        estado = leer_json(datos / "_estado" / f"{archivo}.json") or {}
        if not doc:
            motivo = estado.get("error") or "no se leyó en esta corrida"
            if "NOT_IN_IP_WHITE_LIST" in motivo:
                motivo = ("Temu solo contesta desde la IP de producción (NOT_IN_IP_WHITE_LIST). "
                          "Se lee desde la página /investigacion del panel, con sesión de admin.")
            fuentes[clave] = {"ok": False, "nombre": nombre, "detalle": motivo}
            continue
        cuenta_leida = True
        if archivo == "ml":
            codigo = next(c for c, k in CUENTA_ML.items() if k == clave)
            info = (doc.get("cuentas") or {}).get(codigo) or {}
            cuenta_leida = bool(info.get("leida"))
            if not cuenta_leida:
                fuentes[clave] = {"ok": False, "nombre": nombre,
                                  "detalle": info.get("motivo") or "cuenta no leída"}
                continue
        for f in doc.get("filas") or []:
            if archivo == "ml" and CUENTA_ML.get(str(f.get("cuenta") or "").upper()) != clave:
                continue
            if f.get("error"):
                fuera["con_error"] += 1
                continue
            if str(f.get("estado") or "").split(" ")[0] in ESTADOS_FUERA:
                fuera["borradas"] += 1
                continue
            p = _fila_pub(clave, f, img_url, supuesta)
            conteo[clave]["pubs"] += 1
            conteo[clave]["venta"] += 1 if p.get("v") else 0
            skus = f.get("skus") or ([f["sku"]] if f.get("sku") else [])
            if not skus:
                sin_sku.append(p)
                continue
            for s in skus:
                pubs[sku_norm(s)].append(p)
        fuentes[clave] = {"ok": True, "nombre": nombre, "leido": doc.get("leido_hasta"),
                          "detalle": doc.get("fuente"), "filas": conteo[clave]["pubs"],
                          "venta": conteo[clave]["venta"], "avisos": (doc.get("avisos") or [])[:6]}

    # ── Índices para heredar costo ───────────────────────────────────────────
    por_plantilla: dict[Any, list[str]] = defaultdict(list)
    for f in filas_odoo:
        por_plantilla[f.get("plantilla")].append(sku_norm(f["sku"]))
    hijos: dict[str, list[str]] = defaultdict(list)
    for k in base:
        partes = k.split("-")
        for n in range(1, len(partes)):
            hijos["-".join(partes[:n])].append(k)
    cont_por_ordinal: dict[int, list[float]] = defaultdict(list)
    cont_por_codigo: dict[str, list[float]] = defaultdict(list)
    for v in base.values():
        if v.get("contenedor") and v.get("producto"):
            o = ordinal(v["contenedor"])
            if o is not None:
                cont_por_ordinal[o].append(v["producto"])
            for c in codigos(v["contenedor"]):
                cont_por_codigo[c].append(v["producto"])

    def _con_producto(skus: list[str]) -> list[str]:
        return [s for s in skus if (base.get(s) or {}).get("producto")]

    def _heredar(sku: str, plantilla: Any) -> tuple[str, str] | None:
        """(SKU de origen, parentesco) o None."""
        hermanas = _con_producto([s for s in por_plantilla.get(plantilla, []) if s != sku])
        partes = sku.split("-")
        if hermanas:
            return _elige(hermanas), "variante hermana (misma plantilla de Odoo)"
        for n in range(len(partes) - 1, 0, -1):
            padre = "-".join(partes[:n])
            if (base.get(padre) or {}).get("producto"):
                return padre, "SKU padre"
        hijas = _con_producto(hijos.get(sku, []))
        if hijas:
            return _elige(hijas), "variantes hijas"
        if len(partes) > 2:
            primas = _con_producto([s for s in hijos.get("-".join(partes[:-1]), []) if s != sku])
            if primas:
                return _elige(primas), "variante del mismo modelo"
        return None

    def _elige(candidatas: list[str]) -> str:
        objetivo = _moda([base[s]["producto"] for s in candidatas])
        return min(candidatas, key=lambda s: (abs(base[s]["producto"] - objetivo), s))

    # ── Packing lists en Drive ───────────────────────────────────────────────
    drive = _archivos_drive(AQUI.parents[2].parent / "omnicanal")
    originales = [(i, n) for i, n in drive.items() if not RE_COPIA.match((n or "").strip())]
    cod_archivo: dict[str, list[int]] = defaultdict(list)
    lista_drive: list[list[str]] = []
    for i, n in originales:
        lista_drive.append([n, i])
        for c in codigos(n):
            cod_archivo[c].append(len(lista_drive) - 1)
    cache_drive: dict[str, list[int]] = {}

    def _drive(texto: str | None) -> list[int]:
        if not texto:
            return []
        if texto not in cache_drive:
            idx: list[int] = []
            for c in codigos(texto):
                idx.extend(cod_archivo.get(c, []))
            cache_drive[texto] = sorted(set(idx))[:3]
        return cache_drive[texto]

    # ── Una fila por producto de Odoo ────────────────────────────────────────
    vistos = Counter(sku_norm(f["sku"]) for f in filas_odoo)
    productos: list[dict[str, Any]] = []
    usados: set[str] = set()
    for f in filas_odoo:
        sku = sku_norm(f["sku"])
        fila: dict[str, Any] = {"s": f["sku"], "n": f["nombre"], "q": f["libre"]}
        if f["fisico"] != f["libre"]:
            fila["f"] = f["fisico"]
        if f.get("entrante"):
            fila["qe"] = f["entrante"]
        if f.get("saliente"):
            fila["qs"] = f["saliente"]
        if f.get("por_almacen"):
            fila["a"] = f["por_almacen"]
        if f.get("categoria") and f["categoria"] != "All":
            fila["g"] = f["categoria"]
        if vistos[sku] > 1:
            fila["dup"] = 1
        if str(f["id"]) in img_odoo:
            fila["i"] = img_odoo[str(f["id"])]
        cont = _plano(f.get("contenedor") or "") or None
        b = base.get(sku)
        if cont:
            fila["c"] = cont
        elif b and b.get("contenedor"):
            fila["c"], fila["cq"] = b["contenedor"], "base"   # Odoo no lo trae: lo dice la base
        d = _drive(cont or (b or {}).get("contenedor"))
        if d:
            fila["d"] = d
        ficha = f.get("ficha") or {}
        if ficha.get("costo_ficha"):
            fila["uo"] = ficha["costo_ficha"]
        if ficha.get("piezas_caja"):
            fila["pc"] = ficha["piezas_caja"]

        # Costo de producto, con su origen
        origen = b
        if b and b.get("producto"):
            fila["cp"], fila["co"] = round(b["producto"], 2), "b"
        else:
            her = _heredar(sku, f.get("plantilla"))
            if her:
                origen = base[her[0]]
                fila["cp"], fila["co"] = round(origen["producto"], 2), "h"
                fila["cd"] = f"{her[1]}: {her[0]}"
            else:
                o = ordinal(cont)
                grupo = cont_por_ordinal.get(o) if o is not None else None
                rotulo = f"contenedor {o}" if grupo else None
                if not grupo:
                    for c in codigos(cont):
                        if cont_por_codigo.get(c):
                            grupo, rotulo = cont_por_codigo[c], f"contenedor {c}"
                            break
                if grupo and len(grupo) >= 5:
                    fila["cp"], fila["co"] = round(statistics.median(grupo), 2), "x"
                    fila["cd"] = f"mediana de {len(grupo)} SKUs del {rotulo} en la base"
        if origen:
            if origen.get("flete") is not None:
                fila["cf"] = round(origen["flete"], 2)
            if origen.get("total") is not None:
                fila["ct"] = round(origen["total"], 2)
            if origen.get("prorrateo") is not None:
                fila["pr"] = round(origen["prorrateo"], 2)
                fila["ps"] = "p" if origen.get("prorrateo_fuente") == "prorrateo_525k" else "t"
                if origen.get("inverosimil"):
                    fila["pi"] = 1
            if origen.get("contenedor") and origen["contenedor"] != fila.get("c"):
                fila["cb"] = origen["contenedor"]
            if origen.get("m3_contenedor"):
                fila["m3"] = origen["m3_contenedor"]
            if origen.get("costo_m3"):
                fila["cm3"] = origen["costo_m3"]
            if origen.get("cbm_pieza"):
                fila["vp"] = origen["cbm_pieza"]
            if b is origen and b.get("validado"):
                fila["cv"] = 1
                fila["cvp"] = b.get("validado_por")
        if sku in pubs:
            fila["L"] = pubs[sku]
            usados.add(sku)
        productos.append(fila)

    # ── Un SKU padre publicado «plano» cubre a sus variantes ─────────────────
    # Mercado Libre publica sin variaciones: la publicación lleva el SKU PADRE y
    # Odoo lleva el inventario por VARIANTE. Sin este paso, 944 variantes saldrían
    # como «sin publicar» teniendo a su padre publicado. Se les cuelga la
    # publicación del padre, MARCADA (`pa`): no es lo mismo que tener la propia.
    en_odoo = set(vistos)
    for fila in productos:
        partes = sku_norm(fila["s"]).split("-")
        for n in range(len(partes) - 1, 0, -1):
            padre = "-".join(partes[:n])
            if padre in pubs and padre not in en_odoo:
                fila.setdefault("L", []).extend(dict(x, pa=padre) for x in pubs[padre])
                break

    # ── Publicaciones cuyo SKU no existe en Odoo ─────────────────────────────
    prefijos = Counter()
    for s in en_odoo:
        partes = s.split("-")
        for n in range(1, len(partes)):
            prefijos["-".join(partes[:n])] += 1
    huerfanas: list[dict[str, Any]] = []
    for sku, lista in pubs.items():
        if sku in en_odoo:
            continue
        for p in lista:
            h = dict(p)
            h["s"] = sku
            if prefijos.get(sku):
                h["np"] = prefijos[sku]            # es SKU padre: Odoo tiene N variantes
            huerfanas.append(h)
    for p in sin_sku:
        huerfanas.append(dict(p, s=None))

    # ── Totales (los mismos que recalcula la página; aquí quedan para el reporte) ──
    def _pzas(f: dict[str, Any]) -> float:
        return max(f["q"], 0)

    tot: dict[str, Any] = {"skus": len(productos),
                           "skus_con_stock": sum(1 for f in productos if f["q"] > 0),
                           "piezas": sum(_pzas(f) for f in productos),
                           "negativos": sum(1 for f in productos if f["q"] < 0)}
    for co, nombre in (("b", "base"), ("h", "heredado"), ("x", "extrapolado")):
        sel = [f for f in productos if f.get("co") == co and f["q"] > 0]
        tot[f"valor_{nombre}"] = round(sum(_pzas(f) * f["cp"] for f in sel), 2)
        tot[f"skus_{nombre}"] = len(sel)
        tot[f"piezas_{nombre}"] = sum(_pzas(f) for f in sel)
    sin = [f for f in productos if "cp" not in f and f["q"] > 0]
    tot["skus_sin_costo"], tot["piezas_sin_costo"] = len(sin), sum(_pzas(f) for f in sin)
    tot["valor_total_base"] = round(sum(_pzas(f) * f["ct"] for f in productos
                                        if f.get("co") == "b" and f.get("ct") and f["q"] > 0), 2)
    tot["valor_prorrateo"] = round(sum(_pzas(f) * f["pr"] for f in productos
                                       if f.get("pr") and not f.get("pi") and f["q"] > 0), 2)
    tot["piezas_prorrateo"] = sum(_pzas(f) for f in productos
                                  if f.get("pr") and not f.get("pi") and f["q"] > 0)
    tot["publicados"] = sum(1 for f in productos if f.get("L"))
    tot["publicados_solo_por_padre"] = sum(
        1 for f in productos if f.get("L") and all(x.get("pa") for x in f["L"]))
    tot["a_la_venta"] = sum(1 for f in productos if any(p.get("v") for p in f.get("L") or []))
    tot["huerfanas"] = len(huerfanas)

    doc = {
        "generado": ahora_iso(),
        "canales": [{"k": c[0], "nombre": c[2], "supuesta": c[3]} for c in CANALES],
        "almacenes": odoo.get("almacenes") or [],
        "fuentes": fuentes, "conteo": conteo, "fuera": dict(fuera),
        "parametros": costos.get("parametros") or {},
        "contenedores_base": {"total": costos.get("contenedores"),
                              "en_rango": costos.get("contenedores_en_rango")},
        "sprite": {k: imagenes.get(k) for k in ("lado", "cols", "por_hoja", "hojas")},
        "drive": lista_drive, "totales": tot, "P": productos, "H": huerfanas,
    }
    escribir_json(salida / "datos.json", doc)
    texto = (salida / "datos.json").read_text(encoding="utf-8")
    (salida / "datos.js").write_text("window.CATALOGO=" + texto + ";", encoding="utf-8")

    plantilla = (AQUI / "plantilla.html").read_text(encoding="utf-8")
    (salida / "pagina.html").write_text(plantilla, encoding="utf-8")
    envuelta = ('<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n'
                '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                "<style>body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n"
                "</head>\n<body>\n" + plantilla + "\n</body>\n</html>\n")
    (salida / "index.html").write_text(envuelta, encoding="utf-8")
    aviso(f"página: {len(productos)} productos, {sum(len(v) for v in pubs.values())} publicaciones "
          f"cruzadas, {len(huerfanas)} sin producto en Odoo → index.html")
    aviso(f"   valor a costo de producto (base): ${tot['valor_base']:,.2f} en "
          f"{tot['skus_base']} SKUs / {tot['piezas_base']:,.0f} piezas")
    return tot
