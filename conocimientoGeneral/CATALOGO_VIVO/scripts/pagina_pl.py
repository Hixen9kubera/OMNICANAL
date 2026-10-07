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
  ML      `e`  el paquete de Eduardo (`ml_contenedores`): la API de ML, línea por línea de los
               packing lists. Manda sobre todo lo demás. Su campo `f` dice la fuente:
               f exacto · n nuestro precio publicado · r revisado a mano · b banda de categoría
          `p`  rivales que el juez de producción marcó como el mismo producto, para ESE SKU
          `g`  lo mismo, de otra variante del mismo modelo
          `c`  catálogo de ML + juez de esta carpeta
  Amazon  `k`  búsqueda por palabra clave + caja de compra + juez
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import ml_contenedores
from comun import AQUI, ahora_iso, aviso, escribir_json, leer_json, sku_norm
from ia_titulos import clave_renglon
from inventario_pl import ROTULO_CLASE

ALIAS_RAIZ = {"Mascotas": "Animales y Mascotas"}
TC = 19.0                       # pesos por dólar del packing list (el tipo de cambio fijo de la casa)
# Contenedores cuyo contenido NO parece mercancía para vender (material de exhibición de tienda:
# tiras antirrobo, etiquetas colgantes, ganchos). Sus piezas se cuentan, pero no se valúan.
# Solo se usa si NO está el paquete de Eduardo: con él, mandan sus líneas excluidas (que dejan
# fuera los insumos de ese contenedor renglón por renglón, no el contenedor entero).
SIN_VALOR = {"NYKU4963456"}
# …y los SKUs que son EMPAQUE de otro producto (la caja de color y la caja master de un foco):
# se compraron y están en el packing list, pero no se venden solos.
RE_INSUMO = re.compile(r"^(outer box|colou?red box|inner box|caja master|caja de color)", re.I)


def _firme(p: dict[str, Any] | None, canal: str) -> bool:
    """Precio MEDIDO: dos publicaciones del mismo producto y del mismo tamaño de paquete; en ML, no de catálogo.
    Del paquete de Eduardo cuenta lo que es precio DEL PRODUCTO (exacto, nuestro o revisado), no la banda."""
    if p and p.get("o") == "e":
        return p.get("cl") in ("f", "n", "r")
    if p and p.get("dz"):                    # Amazon «medido» a más de 8 veces su precio de ML: no cuenta
        return False
    return bool(p) and p["n"] >= 2 and not p.get("x") and not (canal == "ml" and p.get("o") == "c")


def estimar(filas: list[dict[str, Any]], rutas: list[list[str]]) -> dict[str, Any]:
    """Le pone un precio ESTIMADO a lo que no tiene precio medido, para que el valor cubra todas las piezas.

    En orden, y cada fila dice cuál le tocó (`[precio, nivel, múltiplo, de dónde, topado]`):
      m  solo Amazon: su precio de MERCADO LIBRE (el del archivo de Eduardo) × lo que Amazon cobra
         de más por lo mismo, medido entre los productos de su subcategoría que tienen los dos precios.
         Va primero: el precio de ML es del producto o de su categoría; el costo trae líos de unidad.
      c  su COSTO × el múltiplo (precio de mercado ÷ costo) de los productos de su subcategoría
         que sí tienen precio medido. Está anclado en lo que costó la pieza.
    Cada tabla se busca de lo más fino a lo más grueso: la categoría hoja (`h`), la subcategoría
    (`s`), la categoría (`r`) y todo el inventario (`g`), según dónde haya base suficiente.
      i  su propio INDICIO: una sola publicación, o paquetes de otro tamaño, o el catálogo de ML.
      t  solo productos sin SKU: el precio de ese TIPO de producto.
      p  el precio PROMEDIO por pieza de su subcategoría. Último recurso, y no para granel: a una
         fila de 10,000 piezas o más sin costo ni indicio no se le inventa precio.
    Subcategoría → categoría → todo el inventario, según dónde haya base suficiente.

    Tres frenos, puestos después de ver qué inflaba el total:
      · el múltiplo de una subcategoría no se aleja más de la mitad del múltiplo general;
      · ningún precio estimado pasa del 90% más caro de los precios MEDIDOS de su subcategoría
        (un costo mal capturado —el del cartón en vez de la pieza— ya no vale millones);
      · lo que se sabe que no es mercancía (`nv`) no se valúa."""
    import statistics

    def sub(f: dict[str, Any]) -> tuple[str, str, str]:
        if f.get("k") is None:
            return ("Sin categoría", "", "")
        r = rutas[f["k"]]
        return (r[0], r[1], r[2] if len(r) > 2 else "")

    def llaves(s: tuple[str, str, str]) -> list[Any]:
        """De la más fina a la más gruesa: hoja, subcategoría, categoría, todo."""
        return ([s] if s[2] else []) + [s[:2], s[0], "*"]

    def de(tabla: dict[Any, float], s: tuple[str, str, str]) -> tuple[float | None, str | None]:
        for nivel, llave in (("h", s if s[2] else None), ("s", s[:2]), ("r", s[0]), ("g", "*")):
            if llave in tabla:
                return tabla[llave], nivel
        return None, None

    resumen: dict[str, Any] = {"tc": TC}
    for canal, campo in (("ml", "em"), ("az", "ea")):
        veces: dict[Any, list[float]] = defaultdict(list)
        precios: dict[Any, list[float]] = defaultdict(list)
        prom: dict[Any, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for f in filas:
            p = f.get(canal)
            if "pc" not in f or f.get("nv") or not p:
                continue
            firme = not f.get("sin") and _firme(p, canal)
            if not firme and p.get("o") != "e":
                continue
            precio, s = p["m"], sub(f)
            for llave in llaves(s):
                precios[llave].append(precio)
                prom[llave][0] += f["pc"] * precio
                prom[llave][1] += f["pc"]
                if firme and f.get("co", 0) >= 0.05 and 0.3 <= precio / f["co"] <= 80:
                    veces[llave].append(precio / f["co"])
        if not precios["*"]:
            resumen[canal] = {"base": 0}
            continue
        # Amazon contra Mercado Libre: la razón entre los dos precios donde se midieron los dos. Una tabla
        # para cuando el de ML es del producto y otra para cuando es banda de categoría (no valen lo mismo).
        razon: dict[str, dict[Any, float]] = {}
        if canal == "az":
            cruce: dict[str, dict[Any, list[float]]] = {"p": defaultdict(list), "b": defaultdict(list)}
            for f in filas:
                a, m = f.get("az"), f.get("ml")
                if "pc" not in f or f.get("sin") or f.get("nv") or not _firme(a, "az"):
                    continue
                if not (m and m.get("o") == "e" and m["m"] > 0):
                    continue
                r = a["m"] / m["m"]
                if 0.25 <= r <= 8:
                    for llave in llaves(sub(f)):
                        cruce["p" if m.get("cl") in ("f", "n", "r") else "b"][llave].append(r)
            for tipo, tabla in cruce.items():
                if tabla["*"]:
                    gen = statistics.median(tabla["*"])
                    razon[tipo] = {k: round(min(max(statistics.median(v), 0.5 * gen), 1.5 * gen), 3)
                                   for k, v in tabla.items() if len(v) >= 8 or k == "*"}
        general = statistics.median(veces["*"]) if veces["*"] else 0
        mult = {k: min(max(statistics.median(v), 0.5 * general), 1.5 * general)
                for k, v in veces.items() if len(v) >= 5 or k == "*"} if general else {}
        medio = {k: prom[k][0] / prom[k][1] for k, v in precios.items() if prom[k][1] > 0 and (len(v) >= 3 or k == "*")}
        tope = {k: sorted(v)[min(len(v) - 1, int(0.9 * len(v)))] for k, v in precios.items() if len(v) >= 5 or k == "*"}
        for f in filas:
            p = f.get(canal)
            if "pc" not in f or f.get("nv") or (not f.get("sin") and _firme(p, canal)):
                continue
            if p and p.get("o") == "e":          # ya trae precio de Eduardo (aunque sea banda): no se estima
                continue
            s = sub(f)
            est: list[Any] | None = None
            m = f.get("ml")
            if canal == "az":
                # El precio de ML de la fila: el de Eduardo; si su archivo no la trae, el estimado de aquí.
                de_ml, tipo = None, "b"
                if m and m.get("o") == "e" and m["m"] > 0:
                    de_ml, tipo = m["m"], ("p" if m.get("cl") in ("f", "n", "r") else "b")
                elif f.get("em"):
                    de_ml = f["em"][0]
                rz, nivel = de(razon.get(tipo) or {}, s) if de_ml else (None, None)
                if rz:
                    est = [de_ml * rz, "m", round(rz, 2), nivel, 0]
            if est is None and f.get("co", 0) >= 0.05:
                mu, nivel = de(mult, s)
                if mu:
                    est = [f["co"] * mu, "c", round(mu, 2), nivel, 0]
            if est is None and p:
                est = [p["m"], "t" if f.get("sin") and _firme(p, canal) else "i", None, None, 0]
            if est is None and f["pq"] < 10000:
                a, nivel = de(medio, s)
                if a:
                    est = [a, "p", None, nivel, 0]
            if est:
                techo, _ = de(tope, s)
                if techo and est[0] > techo:
                    est[0], est[4] = techo, 1
                est[0] = round(est[0], 2)
                f[campo] = est
        resumen[canal] = {"base": len(precios["*"]), "multiplo": round(general, 2),
                          "promedio": round(medio.get("*", 0), 2)}
        if razon:
            resumen[canal]["razon_ml"] = {t: v.get("*") for t, v in razon.items()}
            resumen[canal]["razon_base"] = {t: len(v["*"]) for t, v in cruce.items()}
    return resumen


LADO, COLS, POR_HOJA = 72, 20, 400


def _ruta(camino: list[str]) -> tuple[str, str, str]:
    c = [str(x or "").strip() for x in camino if x]
    c = [ALIAS_RAIZ.get(c[0], c[0])] + c[1:] if c else []
    return (c[0] if c else "", c[1] if len(c) > 1 else "", c[-1] if len(c) > 2 else "")


RE_PLANO = re.compile(r"[\W_]+")
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


def _limpio(x: Any) -> Any:
    """Quita el carácter de reemplazo (U+FFFD) que dejan los textos mal codificados de origen:
    un título de Amazon o un nombre de packing list con un byte roto no debe tumbar la publicación."""
    if isinstance(x, str):
        return x.replace(chr(0xFFFD), "")
    if isinstance(x, list):
        return [_limpio(v) for v in x]
    if isinstance(x, dict):
        return {k: _limpio(v) for k, v in x.items()}
    return x


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


LADO_G, COLS_G, POR_HOJA_G = 256, 8, 64


def _hojas_grandes(fotos: list[Path], grandes: Path, destino: Path, prefijo: str) -> int:
    """Las mismas fotos de `_hojas`, con el mismo índice, a 256 px: para abrirlas en grande. La de cada una
    es la que dejó `fotos_grandes` en `<caché>/fotos/` con el mismo nombre; si no está, se amplía la miniatura."""
    from PIL import Image, ImageOps

    for viejo in destino.glob(f"{prefijo}*.jpg"):
        viejo.unlink()
    n = 0
    for h in range(0, len(fotos), POR_HOJA_G):
        trozo = fotos[h:h + POR_HOJA_G]
        filas = (len(trozo) + COLS_G - 1) // COLS_G
        lienzo = Image.new("RGB", (COLS_G * LADO_G, filas * LADO_G), (255, 255, 255))
        for i, chica in enumerate(trozo):
            ruta = grandes / chica.name
            try:
                im = Image.open(ruta if ruta.exists() else chica).convert("RGB")
            except Exception:  # noqa: BLE001
                continue
            im = ImageOps.contain(im, (LADO_G, LADO_G), Image.LANCZOS)      # llena el cuadro sin deformar
            lienzo.paste(im, ((i % COLS_G) * LADO_G + (LADO_G - im.width) // 2, (i // COLS_G) * LADO_G + (LADO_G - im.height) // 2))
        lienzo.save(destino / f"{prefijo}{n:03d}.jpg", "JPEG", quality=74, optimize=True, progressive=True)
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

    # El paquete de Eduardo: precio de ML por línea de packing list. Si no está, la página sale como antes.
    edu = ml_contenedores.cargar(d / "eduardo_ml", indice)
    cont_de: dict[int, str] = {}
    for c in inv["contenedores"]:
        for i in list(c.get("orig") or []) + list(c.get("val") or []) + list(c.get("repetidos") or []):
            cont_de[i] = str(c["clave"])
    plano = lambda s: RE_PLANO.sub("", (s or "").lower())  # noqa: E731
    por_desc: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    cuenta_edu = {"sku": 0, "sku_renglon": 0, "sin": 0, "sin_nombre": 0, "insumos": 0}
    if edu:
        for (arch, _fila), del_renglon in edu["lineas"].items():
            for ln in del_renglon:
                if not ln["sku"] and arch in cont_de:
                    por_desc[(cont_de[arch], plano(ln["desc"].split(" / ")[0]))].append(ln)

    def cubiertas(ls: list[dict[str, Any]], ct: dict[Any, float] | None) -> float | None:
        """Las piezas de la fila en los contenedores que cubren esas líneas."""
        claves = {cont_de.get(a) for ln in ls for a in ln["archs"]} - {None}
        return sum(v for k, v in (ct or {}).items() if str(k) in claves) or None

    def precio_eduardo(sku: str, fila: dict[str, Any], x: dict[str, Any]) -> dict[str, Any] | None:
        """El precio de ML de un SKU: el de sus líneas; si él no ancló ese SKU, el de los renglones
        originales que aquí se le empataron (ahí su precio es por la descripción del renglón)."""
        ls = edu["por_sku"].get(sku) or []
        if ls and sum(ln["pz"] for ln in ls if ln["ex"]) > 0.5 * sum(ln["pz"] for ln in ls):
            fila["nv"] = 1
            cuenta_edu["insumos"] += 1
            return None
        via = not any(ln["d"] > 0 for ln in ls if not ln["ex"])
        if via:
            ls, vistos = [], set()
            for ren in x.get("ren") or []:
                for fo in ren.get("fo") or []:
                    for ln in edu["lineas"].get((ren.get("ao"), fo)) or []:
                        if id(ln) not in vistos:
                            vistos.add(id(ln))
                            ls.append(ln)
        if not ls:
            return None
        piezas = fila.get("pc") or sum(ln["us"] for ln in ls)
        p = ml_contenedores.precio_de(ls, piezas, edu, TC, None if via else cubiertas(ls, fila.get("ct")))
        if p:
            cf = p.pop("cf", None)
            if via:
                p["pa"] = 1
                p.pop("ue", None)        # un renglón compartido entre variantes no es «contar distinto»
            elif cf:
                # El costo de la fila venía por unidad del PROVEEDOR (el paquete de 100); la fila cuenta
                # piezas de bodega. Sin esto, el estimado de Amazon multiplica el costo del paquete por pieza.
                fila["co"] = cf
            cuenta_edu["sku_renglon" if via else "sku"] += 1
        return p

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

    precio_pl: dict[tuple, float] = {}
    crudo = leer_json(d / "pl.json") or {}
    for tipo, lista in (("o", crudo.get("originales") or []), ("v", crudo.get("validados") or [])):
        for a in lista:
            for f in a["filas"]:
                if f.get("precio_usd"):
                    precio_pl[(tipo, a["id"], f["fila"] + 1)] = f["precio_usd"]
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
        for a, b in (("sal", "ps"), ("cls", "cl"), ("lib", "lb"), ("ent", "en"), ("aj", "aj"), ("v1", "v1"), ("v2", "v2"),
                     ("uf", "uf"), ("sal_pz", "pz")):
            if x.get(a):
                fila[b] = x[a]
        if c and "pv" in c and not c.get("rv"):
            fila["v"] = c["pv"]
        if RE_INSUMO.search(nombre.strip()):
            fila["nv"] = 1
        # Lo que costó la pieza: el costo de producto de la base si tiene renglón propio; si no,
        # el precio en dólares de su renglón del packing list.
        if c and c.get("co") == "b" and c.get("cp"):
            fila["co"] = c["cp"]
        else:
            usd = 0.0
            for ren in x.get("ren") or []:
                if ren.get("av") is not None:
                    usd = usd or precio_pl.get(("v", ren["av"], ren.get("fv")), 0.0)
                for fo in ren.get("fo") or []:
                    usd = usd or precio_pl.get(("o", ren.get("ao"), fo), 0.0)
            if 0.01 <= usd <= 3000:
                fila["co"] = round(usd * TC, 2)
        entre = divisor_por_pieza(nombre, int(t.get("u") or 1))
        p = (precio_eduardo(sku, fila, x) if edu else None) or precio_ml(sku, entre)
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
                                      "rn": [], "foto": None, "usd": 0, "todos": []})
        j["todos"].append(r)
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
            if 0.01 <= j["usd"] <= 3000:
                fila["co"] = round(j["usd"] * TC, 2)
        if not edu and all(any(cod in str(cont) for cod in SIN_VALOR) for cont in j["ct"]):
            fila["nv"] = 1
        k, kq = categoria(llave, None, fila["t"])
        if k is not None:
            fila["k"], fila["kq"] = k, kq
        if j["foto"] is not None:
            fila["j"] = len(fotos_pl)
            fotos_pl.append(j["foto"])
        entre = divisor_por_pieza(j["n"], int(t.get("u") or 1))
        p = None
        if edu:
            # Cada renglón del original es una línea suya (mismo archivo, misma fila). Los del validado
            # no tienen línea: se buscan por nombre dentro de su contenedor.
            ls, vistos, pz_con, pz_ex, por_nombre = [], set(), 0.0, 0.0, 0
            for r in j["todos"]:
                suyas: list[dict[str, Any]] = []
                if r.get("o") != "v":
                    del_renglon = edu["lineas"].get((r["a"], r["f"])) or []
                    if del_renglon and (abs(sum(ln["pz"] for ln in del_renglon) - r["pz"]) < 0.6 or (
                            plano(r.get("t")) and any(plano(r.get("t")) in plano(ln["desc"]) for ln in del_renglon))):
                        suyas = list(del_renglon)
                if not suyas and plano(r.get("t")):
                    suyas = por_desc.get((str(r["c"]), plano(r.get("t")))) or []
                    por_nombre += 1 if suyas else 0
                if not suyas:
                    continue
                if all(ln["ex"] for ln in suyas):
                    pz_ex += r["pz"]
                    continue
                pz_con += r["pz"]
                for ln in suyas:
                    if id(ln) not in vistos and not ln["ex"]:
                        vistos.add(id(ln))
                        ls.append(ln)
            if pz_ex > 0.5 * j["pc"]:
                fila["nv"] = 1
                cuenta_edu["insumos"] += 1
            elif ls:
                p = ml_contenedores.precio_de(ls, pz_con, edu, TC)
                if p:
                    p.pop("cf", None)
                    if por_nombre:
                        p["pa"] = 1
                        p.pop("ue", None)
                    cuenta_edu["sin_nombre" if por_nombre else "sin"] += 1
        p = p or precio_ml(llave, entre)
        if p:
            fila["ml"] = p
        p = precio_amz(llave, entre)
        if p:
            fila["az"] = p
        filas.append(fila)
    hojas_pl = _hojas(fotos_pl, salida / "img", "p") if fotos_pl else 0
    hojas_q = _hojas_grandes(fotos_pl, cache / "fotos", salida / "img", "q") if fotos_pl else 0
    # Un «medido» de Amazon a más de 8 veces el precio de ML del MISMO producto (exacto, nuestro o
    # revisado) casi siempre es otro producto con el mismo nombre —la batería de litio para casa contra
    # la de una herramienta— o un paquete contra una pieza. Queda como indicio y se estima.
    descartados = 0
    for f in filas:
        a, m = f.get("az"), f.get("ml")
        if not (a and m and m.get("o") == "e"):
            continue
        # El precio DEL PRODUCTO en Mercado Libre: la media del mismo producto, el revisado, o si no
        # hay, nuestro propio precio publicado. La media de la categoria no sirve para esta comparacion.
        del_producto = m["m"] if m.get("cl") in ("f", "n", "r") else m.get("pn")
        if del_producto and a["m"] > 8 * del_producto:
            a["dz"] = round(a["m"] / del_producto, 1)
            descartados += 1
    estimacion = estimar(filas, lista_rutas)
    estimacion["az_descartados"] = descartados

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
        "estimacion": estimacion, "sin_valor": sorted(SIN_VALOR),
        "K": lista_rutas, "A": inv["archivos"], "C": conts,
        "sprite": cat.get("sprite") or {}, "spritePL": {"lado": LADO, "cols": COLS, "por_hoja": POR_HOJA, "hojas": hojas_pl},
        "spriteG": leer_json(d / "fotos_grandes.json") or None,      # las mismas fotos, en grande (etapa `fotos_grandes`)
        "spriteQ": {"lado": LADO_G, "cols": COLS_G, "por_hoja": POR_HOJA_G, "hojas": hojas_q},   # …y las del packing list
        "mercado": {
            "ml": {"produccion": len(prod), "produccion_leido": ml.get("produccion_leido"),
                   "grupos": len(ml.get("grupos") or {}), "actualizado": ml.get("actualizado"),
                   "edu": ({**cuenta_edu, "lineas": len(edu["lineas"]), "rev": edu["revision"],
                            "ref": {k: edu["resumen"].get(k) for k in (
                                "contenedores", "lineas", "unidades", "valor_depurado_mxn", "valor_depurado_bajo_mxn",
                                "valor_depurado_alto_mxn", "valor_mercado_mxn", "depurado_por_fuente_mxn",
                                "depurado_unidades_por_fuente", "lineas_topadas", "tope_fob")}} if edu else None)},
            "amazon": {"grupos": grupos_amz, "juzgados": hechas_amz,
                       "palabras": len(amz.get("busquedas") or {}), "actualizado": amz.get("actualizado")},
        },
        "R": filas,
    }
    escribir_json(salida / "datos_pl.json", _limpio(doc))
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
          f"{hojas_pl} hojas de fotos de packing list"
          + (f" · precio de ML de Eduardo: {cuenta_edu}" if edu else " · SIN el paquete de Eduardo"))
    return {"filas": len(filas), "sin_sku": sum(1 for f in filas if f.get("sin")), "con_precio_ml": con("ml"),
            "con_precio_amazon": con("az"), "categorias": len(lista_rutas), "hojas_pl": hojas_pl}
