"""
inventario_pl.py — El inventario contado DESDE LO QUE SE COMPRÓ, no desde lo que Odoo dice que queda.

    piezas del packing list  −  piezas que Odoo movió hacia afuera  =  lo que debería quedar

Por qué al revés
----------------
Odoo solo conoce lo que alguien le capturó, y su historia empieza a mediados de
diciembre de 2025. Hay contenedores enteros que nunca entraron y producto que entró
con otra referencia. Partir de Odoo esconde todo eso. Los packing lists, en cambio,
son la lista completa de lo que se pagó.

Las tres cuentas
----------------
1. COMPRADO. Por contenedor, las piezas del packing list ORIGINAL del proveedor. Si
   un contenedor tiene dos archivos se suman solo cuando son listas distintas (dos
   proveedores en el mismo contenedor); si uno repite al otro, se queda uno.
2. CON SKU. El original no trae SKU. El puente es el packing list VALIDADO por
   bodega, que sí lo trae renglón por renglón. Por SKU se toma lo que bodega CONTÓ
   (es la unidad en que Odoo mueve: si el proveedor escribió 100 paquetes y bodega
   contó 10,000 plumas, Odoo vende plumas). Sin validado, las piezas salen de la base
   de costos (cajas × piezas por caja) y se marcan.
3. SALIÓ. Movimientos HECHOS de Odoo de la bodega hacia afuera (`movimientos`):
   entregas a cliente —que incluyen los envíos a Full, FBA y WFS— menos lo que el
   cliente devolvió, más lo devuelto al proveedor. Los ajustes de inventario NO se
   restan: se muestran aparte, porque son correcciones y no ventas.

   queda = comprado − salió        SOLD OUT = comprado > 0 y queda ≤ 0

   Cuando Odoo cuenta en otra unidad (recibió una fracción exacta de lo comprado: cartones
   contra piezas), sus salidas se multiplican por ese factor antes de restar.

El empate SKU ↔ renglón
-----------------------
Cada SKU dice en qué packing list y en qué renglón está, y CÓMO se supo:
  · `bodega`    bodega escribió el SKU en ese renglón del validado;
  · `foto`      el renglón del original trae la MISMA foto (mismo archivo, sha1);
  · `texto`     mismo nombre y misma cantidad que un renglón del original;
  · `foto_odoo` la foto del producto en Odoo es la de un renglón del original
                (solo entre los SKUs que Odoo o la base de costos mandan a ESE contenedor);
  · `foto_parecida` la misma imagen vuelta a guardar (dHash ≤ 8 de 64 bits, con margen);
  · `cantidad`  lo recibido en su orden de compra coincide con UN solo renglón;
  · `ia`        la IA emparejó el nombre del SKU con el del renglón dentro de su contenedor
                (etapa `empate_ia`; solo confianza alta; es inferencia, sin foto que la respalde);
  · `costos`    sin renglón: la base de costos dice cuántas cajas llegaron. Es un
                estimado y NO suma: esas piezas ya están entre los renglones sin SKU.
Lo que no empata queda en dos listas: SKUs sin renglón y renglones sin SKU.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from comun import Cfg, ahora_iso, aviso, escribir_json, leer_json, sku_norm

RE_TOK = re.compile(r"\d{3}[A-Z]\d{6}|[A-Z]{4}\d{6,8}|SZLS\d{6,9}|SZPM\d{6,9}|[A-Z]{4}[A-Z0-9]{8,14}|\d{9,12}|[A-Z]{3,5}\d{2}-\d{3}")
RE_NUM = re.compile(r"cont(?:enedor)?\.?\s*#?\s*(\d{1,3})\b", re.I)
RE_SUFIJO = re.compile(r"\s-\s*(\d{1,3})\s*$")
# id de costing.packing_archivos → número de contenedor cuando el nombre engaña (ajuste de José)
NUM_OVR = {50: 76}
# A qué bolsa va cada socio de una entrega «a cliente». El socio es un contacto NUEVO
# por orden: se clasifica por NOMBRE, nunca por id (medido en producción, 14-sep-2026).
CLASES_SOCIO = (
    ("full", re.compile(r"^\s*(full\b|mercado\s*libre\s*$)", re.I)),
    ("fba", re.compile(r"^\s*amazon", re.I)),
    ("wfs", re.compile(r"^\s*(wfs|walmart)", re.I)),
    ("parolera", re.compile(r"parolera", re.I)),
    ("canales", re.compile(r"^\s*(temu|shein|tiktok)", re.I)),
)
ROTULO_CLASE = {"full": "Mercado Libre Full", "fba": "Amazon (FBA y ventas)", "wfs": "Walmart",
                "parolera": "Parolera", "canales": "Temu · Shein · TikTok", "directa": "Venta directa"}


def tokens(t: str | None) -> set[str]:
    return set(RE_TOK.findall(re.sub(r"\s+", " ", (t or "").replace("\xa0", " ")).upper()))


def clase_socio(nombre: str) -> str:
    for clave, patron in CLASES_SOCIO:
        if patron.search(nombre or ""):
            return clave
    return "directa"


def _huella_qty(filas: list[dict[str, Any]], campo: str) -> Counter:
    return Counter(round(f[campo]) for f in filas if f.get(campo))


def _parecido(a: Counter, b: Counter) -> float:
    return sum((a & b).values()) / max(1, sum((a | b).values()))


UMBRAL_DHASH, MARGEN_DHASH = 8, 4          # los de producción (packing_publicados.py)


def _dhash(ruta: Path) -> int | None:
    """dHash de 64 bits de una miniatura. Las dos (Odoo y packing list) son de 72 px con el
    mismo lienzo blanco, así que la misma imagen vuelta a guardar da casi los mismos bits."""
    from PIL import Image

    try:
        px = list(Image.open(ruta).convert("L").resize((9, 8), Image.LANCZOS).getdata())
    except Exception:  # noqa: BLE001
        return None
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | (1 if px[y * 9 + x] > px[y * 9 + x + 1] else 0)
    return bits


def _plano(t: str) -> str:
    return re.sub(r"[^a-z0-9㐀-鿿]+", " ", (t or "").lower()).strip()


def construir(salida: Path) -> dict[str, Any]:
    d = salida / "datos"
    pl = leer_json(d / "pl.json")
    mov = leer_json(d / "movimientos.json")
    if not pl or not mov:
        raise RuntimeError("faltan datos/pl.json o datos/movimientos.json: corre `pl_leer` y `movimientos`")
    odoo = leer_json(d / "odoo.json") or {"filas": []}
    costos = (leer_json(d / "costos.json") or {}).get("filas") or {}
    fotos_odoo = leer_json(d / "odoo_fotos.json") or {}          # opcional: sha1 de la foto por SKU

    originales = [o for o in pl["originales"] if o["filas"]]
    validados = [v for v in pl["validados"] if v["filas"]]

    # ── 1 · A qué contenedor (número de Kubera) pertenece cada archivo ───────────
    tok_num: dict[str, int] = {}
    for v in validados:
        if v.get("num"):
            for t in tokens(v["archivo"]):
                tok_num.setdefault(t, v["num"])
    cont_de_sku: dict[str, set[int]] = defaultdict(set)        # dónde dicen Odoo y costos que está cada SKU
    for sku, c in costos.items():
        m = RE_SUFIJO.search(c.get("contenedor") or "")
        if m:
            n = int(m.group(1))
            cont_de_sku[sku_norm(sku)].add(n)
            for t in tokens(c["contenedor"]):
                tok_num.setdefault(t, n)
    for f in odoo["filas"]:
        m = RE_NUM.search(f.get("contenedor") or "")
        if m:
            n = int(m.group(1))
            cont_de_sku[sku_norm(f["sku"])].add(n)
            for t in tokens(f["contenedor"]):
                tok_num.setdefault(t, n)
    for o in originales:
        nums = {tok_num[t] for t in tokens(o["archivo"]) | tokens(o.get("contenedor")) if t in tok_num}
        o["num"] = NUM_OVR.get(o["id"]) or o.get("num") or (min(nums) if len(nums) == 1 else None)
        o["nums_posibles"] = sorted(nums)
    # Los que el nombre no resuelve se empatan por CONTENIDO contra los validados:
    # mismas cantidades por renglón.
    huella_v = {v["num"]: _huella_qty(v["filas"], "qty_pl") or _huella_qty(v["filas"], "piezas")
                for v in validados if v.get("num")}
    ya = {o["num"] for o in originales if o["num"]}
    for o in originales:
        if o["num"]:
            continue
        h = _huella_qty(o["filas"], "piezas")
        libres = {k: c for k, c in huella_v.items() if k not in ya}
        if not libres:
            continue
        mejor, sim = max(((k, _parecido(h, c)) for k, c in libres.items()), key=lambda kv: kv[1])
        if sim >= 0.5:
            o["num"], o["por_contenido"] = mejor, round(sim, 2)
            ya.add(mejor)

    # ── 2 · Un contenedor = sus originales (sin repetidos) + su validado ─────────
    grupos: dict[Any, dict[str, Any]] = {}

    def grupo(clave: Any) -> dict[str, Any]:
        return grupos.setdefault(clave, {"clave": clave, "orig": [], "val": [], "repetidos": []})

    for o in originales:
        grupo(o["num"] if o["num"] else f"s/n {o.get('contenedor') or o['id']}")["orig"].append(o)
    for v in validados:
        grupo(v["num"] if v.get("num") else f"s/n {v.get('contenedor') or v['id']}")["val"].append(v)
    for g in grupos.values():
        for lista, campo in ((g["orig"], "piezas"), (g["val"], "piezas")):
            lista.sort(key=lambda a: (a["archivo"].lower().startswith("copia"), -len(a["filas"])))
            quedan: list[dict[str, Any]] = []
            for a in lista:
                h = _huella_qty(a["filas"], campo)
                if any(_parecido(h, _huella_qty(b["filas"], campo)) >= 0.6 for b in quedan):
                    g["repetidos"].append(a["archivo"])
                else:
                    quedan.append(a)
            lista[:] = quedan

    # ── 3 · Lo comprado por SKU, y de qué renglón sale ───────────────────────────
    comprado: dict[str, dict[str, Any]] = {}

    def de(sku: str) -> dict[str, Any]:
        return comprado.setdefault(sku, {"pz": 0.0, "prov": 0.0, "conts": defaultdict(float), "ren": [], "como": set()})

    renglones_sin_sku: list[dict[str, Any]] = []
    for g in grupos.values():
        n = g["clave"]
        por_sha: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
        por_texto: dict[tuple[str, int], list[tuple[dict, dict]]] = defaultdict(list)
        for o in g["orig"]:
            for f in o["filas"]:
                f["_sku"] = None
                if f.get("foto"):
                    por_sha[f["foto"]["s"]].append((o, f))
                por_texto[(_plano(f["titulo"]), round(f["piezas"]))].append((o, f))
        for v in g["val"]:
            for f in v["filas"]:
                sku = f["sku"]
                if not sku:
                    # Bodega contó el renglón pero no le puso SKU: sigue siendo mercancía comprada.
                    pz = f["piezas"] or f["qty_pl"]
                    if pz:
                        renglones_sin_sku.append({"c": str(n), "a": v["id"], "f": f["fila"] + 1, "o": "v",
                                                  "t": f["titulo"][:90], "pz": round(pz),
                                                  "usd": f.get("precio_usd") or 0, "foto": 1 if f.get("foto") else 0})
                    continue
                c = de(sku)
                pz = f["piezas"] or f["qty_pl"]            # lo que contó bodega; si no contó, lo del proveedor
                c["pz"] += pz
                c["prov"] += f["qty_pl"]
                c["conts"][n] += pz
                c["como"].add("bodega")
                ren = {"c": n, "av": v["id"], "fv": f["fila"] + 1}
                pares = por_sha.get(f["foto"]["s"]) if f.get("foto") else None
                como = "foto"
                if not pares:
                    pares = por_texto.get((_plano(f["titulo"]), round(f["qty_pl"] or f["piezas"])))
                    como = "texto"
                if pares:
                    ren["ao"], ren["fo"] = pares[0][0]["id"], [x["fila"] + 1 for _, x in pares][:6]
                    ren["m"] = como
                    c["como"].add(como)
                    for _, x in pares:
                        x["_sku"] = x["_sku"] or sku
                c["ren"].append(ren)

    # Sin renglón identificado: lo que diga la base de costos (cajas × piezas por caja). Es
    # un ESTIMADO y no suma: esas piezas ya están contadas en los renglones sin SKU de su
    # contenedor; sumarlas otra vez sería contarlas dos veces.
    estimado: dict[str, tuple[int, float]] = {}
    for sku, c in costos.items():
        s = sku_norm(sku)
        if s in comprado:
            continue
        m = RE_SUFIJO.search(c.get("contenedor") or "")
        cajas, pxc = c.get("cajas"), c.get("piezas_caja")
        if m and cajas and pxc:
            estimado[s] = (int(m.group(1)), cajas * pxc)

    # Renglones del original todavía sin SKU: ¿la foto de un producto de Odoo es esa foto?
    sha_a_skus: dict[str, set[str]] = defaultdict(set)
    for sku, h in fotos_odoo.items():
        sha_a_skus[h].add(sku_norm(sku))
    recibido = {s: sum(x["recibido"] for x in lin) for s, lin in (mov.get("compras") or {}).items()}
    pares_ia = (leer_json(d / "empate_ia.json") or {}).get("contenedores") or {}
    id_odoo = {sku_norm(f["sku"]): f["id"] for f in odoo["filas"] if f.get("foto")}
    miniaturas_odoo = salida / "cache" / "img" / "odoo"
    miniaturas_pl = Path(pl["cache"]) / "thumbs" if pl.get("cache") else None
    for g in grupos.values():
        n = g["clave"]
        libres = [(o, f) for o in g["orig"] for f in o["filas"] if not f["_sku"]]
        if not isinstance(n, int) or g["val"]:
            continue                      # con validado, el contenido lo dice bodega: no se adivina
        candidatos = {s for s, ns in cont_de_sku.items() if n in ns}
        for o, f in libres:
            if not f.get("foto"):
                continue
            hits = sha_a_skus.get(f["foto"]["s"], set()) & candidatos
            if len(hits) == 1:
                sku = next(iter(hits))
                f["_sku"] = sku
                x = de(sku)
                x["pz"] += f["piezas"]
                x["prov"] += f["piezas"]
                x["conts"][n] += f["piezas"]
                x["como"].add("foto_odoo")
                x["ren"].append({"c": n, "ao": o["id"], "fo": [f["fila"] + 1], "m": "foto_odoo"})
        # …¿o es la MISMA imagen vuelta a guardar? (dHash: a lo más 8 bits de 64 de diferencia,
        # y el segundo candidato al menos 4 bits más lejos; y el renglón también lo elige a él.)
        libres = [(o, f) for o in g["orig"] for f in o["filas"] if not f["_sku"] and f.get("foto")]
        faltan = [s for s in candidatos if s not in comprado and s in id_odoo]
        if libres and faltan and miniaturas_pl and miniaturas_odoo:
            hr = [(o, f, _dhash(miniaturas_pl / f"o_{o['id']}_{f['fila']}.jpg")) for o, f in libres]
            hr = [x for x in hr if x[2] is not None]
            hs = {s: _dhash(miniaturas_odoo / f"{id_odoo[s]}.jpg") for s in faltan}
            mejor_de_renglon: dict[int, tuple[int, str]] = {}
            elegido: dict[str, tuple[int, int]] = {}
            for s, h in hs.items():
                if h is None or not hr:
                    continue
                dist = sorted(((bin(h ^ x[2]).count("1"), i) for i, x in enumerate(hr)))
                d0, i0 = dist[0]
                d1 = next((d for d, i in dist[1:] if hr[i][1]["foto"]["s"] != hr[i0][1]["foto"]["s"]), 64)
                if d0 <= UMBRAL_DHASH and d1 - d0 >= MARGEN_DHASH:
                    elegido[s] = (d0, i0)
                    if i0 not in mejor_de_renglon or d0 < mejor_de_renglon[i0][0]:
                        mejor_de_renglon[i0] = (d0, s)
            for s, (d0, i0) in elegido.items():
                if mejor_de_renglon[i0][1] != s:
                    continue
                o, f, _ = hr[i0]
                f["_sku"] = s
                x = de(s)
                x["pz"] += f["piezas"]
                x["prov"] += f["piezas"]
                x["conts"][n] += f["piezas"]
                x["como"].add("foto_parecida")
                x["ren"].append({"c": n, "ao": o["id"], "fo": [f["fila"] + 1], "m": "foto_parecida"})
        # …¿o lo recibido en su orden de compra coincide con UN solo renglón libre?
        libres = [(o, f) for o in g["orig"] for f in o["filas"] if not f["_sku"]]
        por_qty: dict[int, list[tuple[dict, dict]]] = defaultdict(list)
        for o, f in libres:
            por_qty[round(f["piezas"])].append((o, f))
        sin_renglon = [s for s in candidatos if not any(r.get("fo") for r in (comprado.get(s) or {"ren": []})["ren"])]
        qty_sku: dict[int, list[str]] = defaultdict(list)
        for s in sin_renglon:
            if recibido.get(s):
                qty_sku[round(recibido[s])].append(s)
        for q, skus in qty_sku.items():
            if len(skus) == 1 and len(por_qty.get(q, [])) == 1 and q >= 5:
                o, f = por_qty[q][0]
                sku = skus[0]
                f["_sku"] = sku
                x = de(sku)
                x["pz"] += f["piezas"]
                x["prov"] += f["piezas"]
                x["conts"][n] += f["piezas"]
                x["como"].add("cantidad")
                x["ren"].append({"c": n, "ao": o["id"], "fo": [f["fila"] + 1], "m": "cantidad"})

    # …y lo que la IA emparejó por NOMBRE dentro de cada contenedor sin validar (`empate_ia`).
    # Solo confianza alta, y solo para SKUs que siguen sin renglón. Si varias variantes
    # apuntan al mismo renglón, sus piezas se reparten según lo que Odoo recibió de cada una.
    for g in grupos.values():
        n = g["clave"]
        if g["val"] or str(n) not in pares_ia:
            continue
        donde = {(o["id"], f["fila"] + 1): (o, f) for o in g["orig"] for f in o["filas"]}
        por_renglon: dict[tuple[int, int], list[str]] = defaultdict(list)
        for par in pares_ia[str(n)]:
            llave = (par["a"], par["f"])
            if par.get("conf") == "alta" and par["sku"] not in comprado and llave in donde \
                    and not donde[llave][1]["_sku"] and par["sku"] not in por_renglon[llave]:
                por_renglon[llave].append(par["sku"])
        for llave, skus in por_renglon.items():
            if not skus:
                continue
            o, f = donde[llave]
            pesos_ = [recibido.get(s) or 1.0 for s in skus]
            for s, w in zip(skus, pesos_):
                parte = f["piezas"] * w / sum(pesos_)
                x = de(s)
                x["pz"] += parte
                x["prov"] += parte
                x["conts"][n] += parte
                x["como"].add("ia")
                x["ren"].append({"c": n, "ao": o["id"], "fo": [f["fila"] + 1], "m": "ia"})
            f["_sku"] = skus[0]

    # ── 4 · Contenedores: comprado, validado, con SKU ────────────────────────────
    conts = []
    archivos: dict[int, str] = {}
    for g in sorted(grupos.values(), key=lambda g: (not isinstance(g["clave"], int), g["clave"] if isinstance(g["clave"], int) else 999, str(g["clave"]))):
        n = g["clave"]
        for a in g["orig"] + g["val"]:
            archivos[a["id"]] = a["archivo"]
        pz_o = sum(f["piezas"] for o in g["orig"] for f in o["filas"])
        ren_o = sum(len(o["filas"]) for o in g["orig"])
        con_sku = sum(f["piezas"] for o in g["orig"] for f in o["filas"] if f["_sku"])
        ren_con = sum(1 for o in g["orig"] for f in o["filas"] if f["_sku"])
        pz_v = sum(f["piezas"] or f["qty_pl"] for v in g["val"] for f in v["filas"])
        pz_v_sku = sum(f["piezas"] or f["qty_pl"] for v in g["val"] for f in v["filas"] if f["sku"])
        prov_v = sum(f["qty_pl"] for v in g["val"] for f in v["filas"])
        codigos = sorted({t for a in g["orig"] + g["val"] for t in tokens(a["archivo"]) | tokens(a.get("contenedor"))})[:4]
        conts.append({"n": n if isinstance(n, int) else None, "clave": str(n), "codigos": codigos,
                      "orig": [o["id"] for o in g["orig"]], "val": [v["id"] for v in g["val"]],
                      "repetidos": g["repetidos"][:4],
                      "pz_orig": round(pz_o), "ren_orig": ren_o, "pz_orig_con_sku": round(con_sku),
                      "ren_orig_con_sku": ren_con, "pz_val": round(pz_v), "pz_val_con_sku": round(pz_v_sku),
                      "pz_val_prov": round(prov_v),
                      "skus": sum(1 for c in comprado.values() if n in c["conts"])})
        if not g["val"]:                  # sin validado, el contenido es el original del proveedor
            for o in g["orig"]:
                for f in o["filas"]:
                    if not f["_sku"] and f["piezas"]:
                        renglones_sin_sku.append({"c": str(n), "a": o["id"], "f": f["fila"] + 1, "o": "o",
                                                  "t": (f["titulo"] or f.get("titulo_chn") or "")[:90],
                                                  "pz": round(f["piezas"]), "usd": f.get("precio_usd") or 0,
                                                  "foto": 1 if f.get("foto") else 0})
        conts[-1]["pz"] = conts[-1]["pz_val"] if g["val"] else conts[-1]["pz_orig"]

    # ── 5 · Lo que Odoo movió hacia afuera, por SKU ──────────────────────────────
    filas: dict[str, dict[str, Any]] = {}
    libre = defaultdict(float)
    for f in odoo["filas"]:
        libre[sku_norm(f["sku"])] += max(f["libre"] or 0, 0)
    universo = set(comprado) | set(mov["skus"]) | {s for s, q in libre.items() if q > 0} | set(estimado)
    tot = defaultdict(float)
    for s in universo:
        if s.startswith("(SIN CÓDIGO"):
            continue
        c, m = comprado.get(s), mov["skus"].get(s) or {}
        clases: dict[str, float] = defaultdict(float)
        for socio, q in (m.get("socios") or {}).items():
            clases[clase_socio(socio)] += q
        sal_cliente = (m.get("sale_cliente") or 0) - (m.get("entra_cliente") or 0)
        dev_prov = m.get("sale_proveedor") or 0
        salio = max(0.0, sal_cliente) + dev_prov
        fila: dict[str, Any] = {}
        if c:
            fila["pl"] = round(c["pz"], 2)
            if round(c["prov"]) != round(c["pz"]):
                fila["prov"] = round(c["prov"], 2)
            fila["conts"] = {str(k): round(v) for k, v in sorted(c["conts"].items(), key=lambda kv: -kv[1])}
            fila["como"] = sorted(c["como"])
            fila["ren"] = c["ren"][:8]
            if len(c["ren"]) > 8:
                fila["ren_mas"] = len(c["ren"]) - 8
        if salio:
            fila["sal"] = round(salio, 2)
            fila["cls"] = {k: round(v, 2) for k, v in sorted(clases.items(), key=lambda kv: -kv[1]) if v}
            if dev_prov:
                fila["cls"]["proveedor"] = round(dev_prov, 2)
            if m.get("primera_venta"):
                fila["v1"], fila["v2"] = m["primera_venta"], m["ultima_venta"]
        entro = (m.get("entra_proveedor") or 0) + (m.get("entra_transito") or 0)
        if entro:
            fila["ent"] = round(entro, 2)                       # lo que Odoo recibió (proveedor + traslado)
        aj = (m.get("entra_ajuste") or 0) - (m.get("sale_ajuste") or 0) - (m.get("sale_merma") or 0)
        if aj:
            fila["aj"] = round(aj, 2)
        if libre.get(s):
            fila["lib"] = round(libre[s], 2)
        if "pl" not in fila and s in estimado:
            fila["est"], fila["est_c"] = round(estimado[s][1], 2), estimado[s][0]
        # ¿Odoo cuenta en OTRA unidad? Si lo que recibió es una fracción exacta de lo comprado
        # (206,600 focos en el packing list, 1,033 «piezas» recibidas: cartones de 200), sus
        # salidas también están en esa unidad. Con un múltiplo de 5 o más se llevan a piezas;
        # con 2, 3 o 4 solo se avisa: puede ser media recepción, o pares.
        # Se compara contra lo RECIBIDO (proveedor y traslados), sin ajustes, y la división
        # tiene que ser exacta: 231 comprados y 10 recibidos no es «cajas de 23», es que solo
        # entraron 10.
        tot["pz_salio_odoo"] += salio                   # tal como lo cuenta Odoo, antes de convertir
        if "pl" in fila and entro >= 5:
            razon = fila["pl"] / entro
            veces = round(razon)
            # …y lo que Odoo vendió más lo que dice tener debe CABER en lo que recibió. Si no,
            # el producto entró por ajuste de inventario y «recibido» no mide nada.
            cabe = (max(0.0, sal_cliente) + libre.get(s, 0)) <= entro * 1.15
            if veces >= 2 and abs(razon - veces) < 0.001 and cabe:
                fila["uf"] = veces
                if veces >= 5 and salio:
                    fila["sal_pz"] = round(salio * veces, 2)
                    salio = salio * veces
                    tot["skus_otra_unidad"] += 1
        if "pl" in fila:
            fila["q"] = round(max(0.0, fila["pl"] - salio), 2)
            if fila["q"] <= 0:
                fila["so"] = 1                                  # SOLD OUT
            if salio > fila["pl"] * 1.02 + 1:
                fila["de_mas"] = round(salio - fila["pl"], 2)   # salió más de lo que dice el packing list
        filas[s] = fila
        tot["skus"] += 1
        if "pl" in fila:
            tot["skus_con_pl"] += 1
            tot["pz_pl"] += fila["pl"]
            tot["pz_queda"] += fila["q"]
            tot["pz_salio_con_pl"] += min(salio, fila["pl"])
            tot["sold_out"] += fila.get("so", 0)
            tot["pz_libre_con_pl"] += fila.get("lib", 0)
        else:
            tot["skus_sin_pl"] += 1
            tot["pz_salio_sin_pl"] += salio
            tot["pz_libre_sin_pl"] += fila.get("lib", 0)
        tot["pz_salio"] += salio
        for k, v in clases.items():
            tot[f"sal_{k}"] += v
        tot["sal_proveedor"] += dev_prov

    como = Counter()
    for c in comprado.values():
        orden = ("foto", "texto", "foto_odoo", "foto_parecida", "cantidad", "ia", "bodega")
        como[next((k for k in orden if k in c["como"]), "?")] += 1
    doc = {
        "generado": ahora_iso(),
        "fuentes": {"pl": pl.get("leido"), "movimientos": mov.get("leido_hasta"), "odoo": odoo.get("leido_hasta")},
        "resumen": {
            "archivos_originales": len(originales), "archivos_validados": len(validados),
            "contenedores": len(conts), "contenedores_con_validado": sum(1 for c in conts if c["val"]),
            "contenedores_sin_original": sum(1 for c in conts if not c["orig"]),
            "pz_original": sum(c["pz_orig"] for c in conts), "renglones_original": sum(c["ren_orig"] for c in conts),
            "pz_original_con_sku": sum(c["pz_orig_con_sku"] for c in conts),
            "renglones_con_sku": sum(c["ren_orig_con_sku"] for c in conts),
            "pz_validado": sum(c["pz_val"] for c in conts), "pz_validado_con_sku": sum(c["pz_val_con_sku"] for c in conts),
            "archivos_repetidos": sum(len(c["repetidos"]) for c in conts),
            "empate": dict(como), "skus_solo_estimado": sum(1 for x in filas.values() if "est" in x),
            "pz_contenido": sum(c["pz"] for c in conts),
            "pz_contenido_validado": sum(c["pz"] for c in conts if c["val"]),
            "pz_contenido_sin_validar": sum(c["pz"] for c in conts if not c["val"]),
            "renglones_sin_sku": len(renglones_sin_sku),
            "pz_renglones_sin_sku": sum(r["pz"] for r in renglones_sin_sku),
            **{k: round(v) for k, v in tot.items()},
            "odoo_totales": mov["totales"], "odoo_socios": dict(list(mov["socios"].items())[:12]),
            "odoo_por_mes": mov["por_mes"],
        },
        "rotulos": ROTULO_CLASE, "archivos": archivos, "contenedores": conts,
        "skus": filas, "renglones_sin_sku": renglones_sin_sku,
    }
    escribir_json(d / "inventario_pl.json", doc)
    r = doc["resumen"]
    aviso(f"inventario: comprado {r['pz_original']:,} pzas en {r['contenedores']} contenedores · "
          f"con SKU {r['pz_original_con_sku']:,} · validado por bodega {r['pz_validado']:,}")
    aviso(f"   por SKU: {r.get('skus_con_pl', 0):,} SKUs con packing list, {r.get('pz_pl', 0):,} pzas · "
          f"salió {r.get('pz_salio_con_pl', 0):,} · queda {r.get('pz_queda', 0):,} · sold out {r.get('sold_out', 0):,} · "
          f"Odoo libre de esos {r.get('pz_libre_con_pl', 0):,}")
    return {k: v for k, v in r.items() if not k.startswith("odoo_")}
