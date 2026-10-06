"""
pagina.py — Cruza todo por SKU y arma la página: QUÉ tenemos, CUÁNTO y CUÁNTO VALE.

Entra lo que dejaron los extractores en `datos/`; sale:

    index.html      la página, autocontenida (se abre con doble clic)
    pagina.html     el mismo contenido sin envoltura, para publicarlo como artefacto
    datos.json/.js  el catálogo cruzado (la página lo carga de cualquiera de los dos)
    img/hNNN.jpg    las fotos, en hojas

La fila la pone ODOO: una por cada `product.product` con referencia interna, con su
stock libre (`free_qty`). El cruce con todo lo demás es por SKU, sin distinguir
mayúsculas.

═══ EL PRECIO DE VENTA (lo que se pidió el 6-oct-2026: «cuánto vale en mercado») ═══

No hay UNA fuente que le ponga precio a todo el catálogo, así que se busca en orden y
cada fila dice de dónde salió:

    v  en venta      el precio MÁS BAJO al que hoy se está vendiendo en un marketplace
    p  en pausa      el más bajo de sus publicaciones pausadas (se vendía a ese precio)
    c  catálogo      el precio de WooCommerce, que tiene todo producto ya trabajado
    —  sin precio    no se inventa

A qué canal pertenece la publicación NO es el tema de esta página: solo cuenta como
evidencia del precio, y se ve en el detalle de la fila.

DOS COSAS QUE PARECEN PRECIO Y NO LO SON (medido el 6-oct-2026), y se descartan de
cualquier fuente:
  · `1.00` — el marcador de un borrador que nunca pasó por el Estudio.
  · el «Sales Price» de Odoo copiado tal cual. En el catálogo propio ese campo es el
    costo en dólares o un 1; en «Productos Agente», cualquier cosa. Contra 203
    productos con precio real de mercado no coincide en casi ninguno.

═══ LA CATEGORÍA ═══

La de Mercado Libre, que es el árbol que el mercado ya usa (31 raíces):

    directa    la de su publicación en ML, o la que el panel le guardó en WooCommerce
               (su categoría ahí lleva «ML: MLM123» en la descripción)
    n          la categoría de WooCommerce NO trae id, pero su NOMBRE existe en el árbol
               de ML; se toma si todas las candidatas cuelgan de la misma raíz, o si el
               prefijo del SKU desempata
    h          heredada de una variante del mismo modelo
    p          ESTIMADA por el prefijo del SKU (MUE-, ROP-, JUGU-…) — el prefijo se
               asignó a ojo y falla seguido, por eso va marcada y cuenta para limpieza
    —          sin categoría

═══ EL COSTO ═══

`costing.costos_validados.costo_producto`: `b` renglón propio (el oficial), `h`
heredado de una variante o del padre, `x` extrapolado (mediana de su contenedor). La
ficha de Odoo no se usa. El prorrateo de 525k viaja en la fila, pero la página lo
deja para el final, en el detalle.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from comun import AQUI, ahora_iso, aviso, escribir_json, leer_json, sku_norm

CANALES = {"mk": ("ml", "Mercado Libre (Kubera)"), "ms": ("ml", "Mercado Libre (San Corpe)"),
           "az": ("amazon", "Amazon"), "tt": ("tiktok", "TikTok"),
           "tm": ("temu", "Temu"), "wm": ("walmart", "Walmart")}
CUENTA_ML = {"BEKURA": "mk", "SANCORFASHION": "ms"}
ESTADOS_FUERA = {"DELETED"}

RE_COD = re.compile(r"([A-Z]{4}\d{6,7}|SZLS\d{6,9}|[A-Z]{4}[A-Z0-9]{8,14}|\d{9,12})")
RE_ORD = re.compile(r"(?:CONTENEDOR|CONTAINER|CONT)\.?\s*#?\s*(\d{1,3})\b")
RE_ORD_FIN = re.compile(r"[-\s](\d{1,3})\s*$")
RE_COPIA = re.compile(r"^X+-", re.I)
RE_SKU = re.compile(r"^[A-ZÑ]{2,5}-\d{2,4}(-\S+)?$")

# Prefijo del SKU → raíz de Mercado Libre. SOLO como último recurso y siempre marcado.
_HOGAR, _BEBES, _JUG = "Hogar, Muebles y Jardín", "Bebés", "Juegos y Juguetes"
PREFIJO_A_RAIZ: dict[str, str] = {
    **{p: _HOGAR for p in ("MUE", "MES", "SIL", "CAM", "EST", "ORG", "COM", "ESCR", "BAÑ", "BAN",
                           "JAR", "TV", "COC", "DEC", "ILUM", "TEX")},
    **{p: _BEBES for p in ("BEB", "CUNA", "PAS", "CORR", "ALIM", "ROBB", "JUG")},
    **{p: _JUG for p in ("JUGU", "MUN", "PEL", "CONS", "JUEG", "CART", "CAS", "MONT", "EDU")},
    **{p: "Ropa, Bolsas y Calzado" for p in ("ROP", "CALZ", "ACC")},
    **{p: "Electrónica, Audio y Video" for p in ("TEC", "ELEC")},
    "CEL": "Celulares y Telefonía", "HERR": "Herramientas", "OFI": "Industrias y Oficinas",
    "MASC": "Animales y Mascotas", "VEH": "Accesorios para Vehículos",
    "HIG": "Belleza y Cuidado Personal", "DEP": "Deportes y Fitness", "DEPO": "Deportes y Fitness",
    "LIB": "Arte, Papelería y Mercería", "ART": "Arte, Papelería y Mercería",
    "PAP": "Arte, Papelería y Mercería",
}

# Cuándo un precio queda EN REVISIÓN por desproporcionado. Medido el 6-oct-2026 sobre
# los 665 productos con costo de la base y precio de una publicación A LA VENTA (lo
# más confiable que hay): arriba de 25 veces el costo queda solo el 3% más alto. A esa
# altura ya no es un margen alto: es que el precio (o el costo) es de otra cosa — otro
# producto con el SKU reciclado, o un paquete. No dice CUÁL de los dos está mal: por
# eso es «revisar». (La distribución completa no se escribe aquí: el repo es público.
# Sale en `datos.json → veces`, que no se sube.)
VECES_MAX = 25
COSTO_MIN_PARA_VECES = 1.0      # con costos de centavos el múltiplo no dice nada
PRECIO_TOPE = 50_000.0
# Sin costo no hay múltiplo que medir: el precio se compara contra la MEDIANA de su
# subcategoría. Medido el mismo día sobre los 739 productos con precio y sin costo:
# arriba de 20 veces solo quedan errores claros (una mica de pantalla al precio de
# una computadora, unos shorts al de una motocicleta); entre 8 y 20 hay productos
# caros de verdad (un rack para servidores, una cámara termográfica). Por eso el
# corte es 20 y no menos.
VECES_CATEGORIA = 20
MIN_PARA_MEDIANA = 8

# Lo que hay que limpiar. clave → (rótulo, explicación). El orden es el de la página.
HALLAZGOS: list[tuple[str, str, str]] = [
    ("sp", "Con stock y sin precio de venta", "No tiene publicación ni precio de catálogo válido: no se puede valuar."),
    ("pk", "Precio de un paquete, stock en piezas", "La publicación vende un paquete («100 piezas») y Odoo cuenta piezas sueltas: multiplicar infla el valor. No entra al total hasta aclararlo."),
    ("pa", "Precio desproporcionado contra su costo", "Vale más de 25 veces su costo de producto, o más de $50,000 la pieza: la publicación casi seguro es de otro producto o de un paquete. No entra al total hasta aclararlo."),
    ("pg", "Precio desproporcionado contra su categoría", "No tiene costo para comparar y su precio pasa de 20 veces la mediana de su categoría. No entra al total hasta aclararlo."),
    ("pm", "Precio que no es precio", "En el catálogo trae 1.00 (marcador de borrador) o el «Sales Price» de Odoo copiado."),
    ("pc", "Precio de venta menor al costo", "Se vendería por debajo de su costo de producto."),
    ("pd", "Precios que no se parecen entre sí", "Entre sus publicaciones y el catálogo hay más de 3 veces de diferencia."),
    ("sk", "Sin categoría", "No tiene categoría en ML ni en WooCommerce, y su SKU no dice nada."),
    ("ke", "Categoría estimada por el SKU", "La categoría sale del prefijo del SKU, que se asignó a ojo: hay que confirmarla."),
    ("sc", "Con stock y sin costo", "No tiene renglón en la base de costos ni de dónde heredarlo."),
    ("ce", "Costo estimado", "El costo es heredado de otra variante o la mediana de su contenedor."),
    ("sg", "Stock muy grande en un solo SKU", "10,000 piezas o más: conviene confirmar el conteo antes de valuarlo."),
    ("sn", "Stock negativo", "Odoo tiene menos de cero piezas libres."),
    ("du", "SKU repetido en Odoo", "Dos productos distintos comparten la misma referencia interna."),
    ("sr", "SKU fuera de formato", "No sigue la forma CAT-0000-ATRIBUTO."),
    ("tg", "Título que no describe", "Una sola palabra o menos de 8 letras («CALZADO», «ROPA»)."),
    ("sf", "Sin foto", "Odoo no tiene imagen y tampoco ninguna publicación."),
    ("sl", "Sin packing list", "Ni Odoo ni la base dicen a qué contenedor pertenece."),
]


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


_UNIDADES = (r"pz|pzs|pza|pzas|pieza|piezas|pcs|unidades|uds|bolsas|hojas|pares|rollos|tiras|"
             r"globos|sobres|etiquetas|stickers|calcomanias|calcomanías|cuentas|clips|ligas")
RE_PAQ_1 = re.compile(rf"\b(\d{{2,4}})\s*(?:{_UNIDADES})\b", re.I)
RE_PAQ_2 = re.compile(r"\b(?:paquete|pack|set|kit|juego|caja|lote|bolsa)\s+(?:de\s+|con\s+)?(\d{2,4})\b", re.I)
RE_PAQ_3 = re.compile(r"\bx\s?(\d{2,4})\b", re.I)


def _paquete(titulo: str, nombre_odoo: str) -> int | None:
    """
    ¿La publicación vende un PAQUETE mientras Odoo cuenta PIEZAS?

    Devuelve cuántas piezas trae el paquete según el título de la publicación
    («Bolsas Holográficas … 100 Piezas»), SOLO si ese número no aparece también en
    el nombre de Odoo. Si aparece en los dos («100 pz PAJITAS»), la unidad de Odoo
    ya es el paquete y no hay nada que avisar. De 10 en adelante: «set de 3» es un
    producto, no una diferencia de cien veces en el valor.
    """
    for patron in (RE_PAQ_1, RE_PAQ_2, RE_PAQ_3):
        for m in patron.finditer(titulo or ""):
            n = int(m.group(1))
            if n >= 10 and not re.search(rf"(?<!\d){n}(?!\d)", nombre_odoo or ""):
                return n
    return None


def _no_es_precio(p: float | None, precio_odoo: float | None) -> str | None:
    """Por qué un número NO es un precio de venta; None si sí lo parece."""
    if p is None or p <= 0:
        return "vacío"
    if p <= 1.0:
        return "marcador 1.00"
    if precio_odoo and precio_odoo not in (0.0, 1.0) and abs(p - precio_odoo) < 0.011:
        return "copia del «Sales Price» de Odoo"
    return None


def construir(salida: Path) -> dict[str, Any]:
    datos = salida / "datos"
    odoo = leer_json(datos / "odoo.json")
    if not odoo:
        raise RuntimeError("falta datos/odoo.json: corre primero la etapa `odoo`")
    costos = leer_json(datos / "costos.json") or {}
    base: dict[str, dict[str, Any]] = costos.get("filas") or {}
    woo_doc = leer_json(datos / "woo.json") or {}
    woo: dict[str, dict[str, Any]] = woo_doc.get("filas") or {}
    woo_cat_ml: dict[str, str] = woo_doc.get("categoria_ml") or {}
    arbol: dict[str, list[str]] = leer_json(datos / "categorias.json") or {}
    imagenes = leer_json(datos / "imagenes.json") or {}
    img_odoo: dict[str, int] = imagenes.get("odoo") or {}
    img_url: dict[str, int] = imagenes.get("url") or {}
    filas_odoo: list[dict[str, Any]] = odoo["filas"]

    # ── Publicaciones por SKU: aquí solo importan como EVIDENCIA de precio ────
    pubs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    leidas = Counter()
    hora_precios: list[str] = []
    no_leidos: list[str] = []
    for clave, (archivo, nombre) in CANALES.items():
        doc = leer_json(datos / f"{archivo}.json")
        if not doc:
            no_leidos.append(nombre)
            continue
        if doc.get("leido_hasta"):
            hora_precios.append(doc["leido_hasta"])
        for f in doc.get("filas") or []:
            if archivo == "ml" and CUENTA_ML.get(str(f.get("cuenta") or "").upper()) != clave:
                continue
            if f.get("error") or str(f.get("estado") or "").split(" ")[0] in ESTADOS_FUERA:
                continue
            precio = f.get("precio_cobrado") or f.get("precio")
            p = {"k": clave, "p": precio, "v": 1 if f.get("a_la_venta") else 0,
                 "u": f.get("link"), "img": f.get("imagen"), "cat": f.get("categoria"),
                 "t": f.get("titulo") or ""}
            leidas[clave] += 1
            for s in f.get("skus") or ([f["sku"]] if f.get("sku") else []):
                pubs[sku_norm(s)].append(p)

    # ── Índices para heredar (costo y categoría) ─────────────────────────────
    vistos = Counter(sku_norm(f["sku"]) for f in filas_odoo)
    en_odoo = set(vistos)
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

    def _elige(candidatas: list[str]) -> str:
        objetivo = _moda([base[s]["producto"] for s in candidatas])
        return min(candidatas, key=lambda s: (abs(base[s]["producto"] - objetivo), s))

    def _heredar(sku: str, plantilla: Any) -> tuple[str, str] | None:
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

    def _pubs_de(sku: str) -> list[dict[str, Any]]:
        """Las publicaciones del SKU y, si es variante de un padre publicado plano, las del padre."""
        propias = list(pubs.get(sku) or [])
        partes = sku.split("-")
        for n in range(len(partes) - 1, 0, -1):
            padre = "-".join(partes[:n])
            if padre in pubs and padre not in en_odoo:
                propias.extend(dict(x, pa=padre) for x in pubs[padre])
                break
        return propias

    # ── Categoría directa de cada SKU (ML o WooCommerce) ─────────────────────
    def _ruta_ml(cat_id: str | None) -> list[str] | None:
        r = arbol.get(cat_id or "")
        return r if r else None

    candidatas: dict[str, list[str]] = woo_doc.get("categoria_candidatas") or {}

    def _por_nombre(nombre: str, sku: str) -> list[str] | None:
        """
        La categoría de WooCommerce no trae id de ML, pero su nombre sí existe en el
        árbol — varias veces. Si todas las candidatas cuelgan de la MISMA raíz, la raíz
        es segura. Si no, el prefijo del SKU desempata («Tenis» con `CALZ-` es calzado,
        no el deporte). Si tampoco, no se adivina.
        """
        rutas_c = [arbol[i] for i in candidatas.get(nombre) or [] if arbol.get(i)]
        if not rutas_c:
            return None
        if len({r[0] for r in rutas_c}) == 1:
            return rutas_c[0]                       # vienen de la más poblada a la menos
        raiz = PREFIJO_A_RAIZ.get(sku.split("-")[0])
        return next((r for r in rutas_c if r[0] == raiz), None)

    directa: dict[str, list[str]] = {}
    por_nombre: set[str] = set()
    for f in filas_odoo:
        sku = sku_norm(f["sku"])
        ruta = None
        for p in _pubs_de(sku):
            if p["k"] in ("mk", "ms") and p.get("cat"):
                ruta = _ruta_ml(p["cat"])
                if ruta:
                    break
        nombres = (woo.get(sku) or {}).get("cats") or (woo.get(sku) or {}).get("ruta") or []
        if not ruta:
            for nombre in nombres:
                ruta = _ruta_ml(woo_cat_ml.get(nombre))
                if ruta:
                    break
        if not ruta:
            for nombre in nombres:
                ruta = _por_nombre(nombre, sku)
                if ruta:
                    por_nombre.add(sku)
                    break
        if ruta:
            directa[sku] = ruta
    modelo: dict[str, list[list[str]]] = defaultdict(list)       # CAT-0000 → rutas de sus variantes
    for sku, ruta in directa.items():
        partes = sku.split("-")
        if len(partes) >= 2:
            modelo["-".join(partes[:2])].append(ruta)

    def _categoria(sku: str, plantilla: Any) -> tuple[list[str] | None, str | None]:
        if sku in directa:
            return directa[sku], ("n" if sku in por_nombre else None)
        vecinas = [directa[s] for s in por_plantilla.get(plantilla, []) if s in directa]
        partes = sku.split("-")
        if not vecinas and len(partes) >= 2:
            vecinas = modelo.get("-".join(partes[:2])) or []
        if vecinas:
            mas = Counter(tuple(r) for r in vecinas).most_common(1)[0][0]
            return list(mas), "h"
        raiz = PREFIJO_A_RAIZ.get(partes[0])
        if raiz:
            return [raiz], "p"
        return None, None

    # ── Packing lists en Drive ───────────────────────────────────────────────
    drive = leer_json(AQUI.parents[2].parent / "omnicanal" / "backend" / "services" / "data"
                      / "packing_lists_drive.json", {}) or {}
    cod_archivo: dict[str, list[int]] = defaultdict(list)
    lista_drive: list[list[str]] = []
    for i, n in drive.items():
        if RE_COPIA.match((n or "").strip()):
            continue
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
    rutas: dict[tuple, int] = {}
    lista_rutas: list[list[str]] = []
    productos: list[dict[str, Any]] = []
    for f in filas_odoo:
        sku = sku_norm(f["sku"])
        q = f["libre"]
        fila: dict[str, Any] = {"s": f["sku"], "n": f["nombre"], "q": q}
        hallazgos: list[str] = []
        if f["fisico"] != q:
            fila["f"] = f["fisico"]
        if f.get("entrante"):
            fila["qe"] = f["entrante"]
        if f.get("saliente"):
            fila["qs"] = f["saliente"]
        if f.get("por_almacen"):
            fila["a"] = f["por_almacen"]
        if f.get("categoria") and f["categoria"] != "All":
            fila["g"] = f["categoria"]
        ficha = f.get("ficha") or {}
        propias = _pubs_de(sku)

        # Foto: la de Odoo; si no hay, la de alguna publicación
        if str(f["id"]) in img_odoo:
            fila["i"] = img_odoo[str(f["id"])]
        else:
            for p in propias:
                if p.get("img") in img_url:
                    fila["i"] = img_url[p["img"]]
                    break

        # Categoría
        ruta, como = _categoria(sku, f.get("plantilla"))
        if ruta:
            corta = (ruta[0], ruta[1] if len(ruta) > 1 else "", ruta[-1] if len(ruta) > 2 else "")
            if corta not in rutas:
                rutas[corta] = len(lista_rutas)
                lista_rutas.append(list(corta))
            fila["k"] = rutas[corta]
            if como:
                fila["kq"] = como

        # Precio de venta, con su evidencia
        precio_odoo = ficha.get("precio_lista")
        evidencia: list[list[Any]] = []
        validos: dict[str, list[tuple[float, str]]] = {"v": [], "p": [], "c": []}
        basura: list[str] = []
        for p in propias:
            motivo = _no_es_precio(p.get("p"), precio_odoo)
            rotulo = CANALES[p["k"]][1] + (" · a la venta" if p["v"] else " · en pausa") + \
                (" · publicada con el SKU padre" if p.get("pa") else "")
            if motivo:
                if p.get("p"):
                    basura.append(f"{rotulo}: {p['p']:g} ({motivo})")
                continue
            validos["v" if p["v"] else "p"].append((p["p"], p.get("t") or ""))
            evidencia.append([rotulo, p["p"], p.get("u") or "", p.get("t") or ""])
        w = woo.get(sku)
        if w:
            motivo = _no_es_precio(w.get("precio"), precio_odoo)
            if motivo:
                if w.get("precio"):
                    basura.append(f"Catálogo WooCommerce: {w['precio']:g} ({motivo})")
            else:
                validos["c"].append((w["precio"], w.get("nombre") or ""))
                evidencia.append([f"Catálogo WooCommerce · {w.get('estado') or ''}".strip(" ·"),
                                  w["precio"], "", w.get("nombre") or ""])
        paquete = None
        for origen in ("v", "p", "c"):
            if validos[origen]:
                elegido = min(validos[origen])
                fila["pv"], fila["po"] = round(elegido[0], 2), origen
                paquete = _paquete(elegido[1], f["nombre"])
                break
        if evidencia:
            fila["pe"] = sorted(evidencia, key=lambda e: e[1])[:6]
        if basura:
            fila["pj"] = basura[:3]
        if paquete:
            fila["pk"] = paquete

        # Packing list
        cont = _plano(f.get("contenedor") or "") or None
        b = base.get(sku)
        if cont:
            fila["c"] = cont
        elif b and b.get("contenedor"):
            fila["c"], fila["cq"] = b["contenedor"], "base"
        d = _drive(cont or (b or {}).get("contenedor"))
        if d:
            fila["d"] = d
        if ficha.get("costo_ficha"):
            fila["uo"] = ficha["costo_ficha"]
        if ficha.get("piezas_caja"):
            fila["pc"] = ficha["piezas_caja"]

        # Costo de producto, con su origen
        origen_c = b
        if b and b.get("producto"):
            fila["cp"], fila["co"] = round(b["producto"], 2), "b"
        else:
            her = _heredar(sku, f.get("plantilla"))
            if her:
                origen_c = base[her[0]]
                fila["cp"], fila["co"] = round(origen_c["producto"], 2), "h"
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
        if origen_c:
            if origen_c.get("flete") is not None:
                fila["cf"] = round(origen_c["flete"], 2)
            if origen_c.get("total") is not None:
                fila["ct"] = round(origen_c["total"], 2)
            if origen_c.get("prorrateo") is not None:
                fila["pr"] = round(origen_c["prorrateo"], 2)
                fila["pt"] = "p" if origen_c.get("prorrateo_fuente") == "prorrateo_525k" else "t"
                if origen_c.get("inverosimil"):
                    fila["pi"] = 1
            if origen_c.get("contenedor") and origen_c["contenedor"] != fila.get("c"):
                fila["cb"] = origen_c["contenedor"]
            if origen_c.get("m3_contenedor"):
                fila["m3"] = origen_c["m3_contenedor"]
            if origen_c.get("costo_m3"):
                fila["cm3"] = origen_c["costo_m3"]
            if origen_c.get("cbm_pieza"):
                fila["vp"] = origen_c["cbm_pieza"]
            if b is origen_c and b.get("validado"):
                fila["cv"] = 1

        # Lo que hay que limpiar
        pv, cp = fila.get("pv"), fila.get("cp")
        todos = [x[0] for o in ("v", "p", "c") for x in validos[o]]
        if q > 0 and pv is None:
            hallazgos.append("sp")
        if basura:
            hallazgos.append("pm")
        if paquete:
            hallazgos.append("pk")
        if pv is not None and cp and pv < cp:
            hallazgos.append("pc")
        if pv is not None and ((cp and cp >= COSTO_MIN_PARA_VECES and pv > VECES_MAX * cp)
                               or pv > PRECIO_TOPE):
            hallazgos.append("pa")
        # EN REVISIÓN: el precio existe pero casi seguro no corresponde a UNA pieza de
        # este producto. Se muestra, y NO entra al valor de venta hasta que se aclare.
        if "pk" in hallazgos or "pa" in hallazgos:
            fila["rv"] = 1
        if len(todos) > 1 and min(todos) > 0 and max(todos) / min(todos) >= 3:
            hallazgos.append("pd")
        if "k" not in fila:
            hallazgos.append("sk")
        elif fila.get("kq") == "p":
            hallazgos.append("ke")
        if q > 0 and cp is None:
            hallazgos.append("sc")
        if fila.get("co") in ("h", "x"):
            hallazgos.append("ce")
        if q >= 10000:
            hallazgos.append("sg")
        if q < 0:
            hallazgos.append("sn")
        if vistos[sku] > 1:
            hallazgos.append("du")
        if not RE_SKU.match(sku):
            hallazgos.append("sr")
        nombre = _plano(f["nombre"])
        if len(nombre) < 8 or len(nombre.split()) < 2:
            hallazgos.append("tg")
        if "i" not in fila:
            hallazgos.append("sf")
        if "c" not in fila:
            hallazgos.append("sl")
        if hallazgos:
            fila["x"] = hallazgos
        productos.append(fila)

    # ── Segunda pasada: lo que solo se puede juzgar viendo a los demás ────────
    def _mediana_por(llave) -> dict[Any, float]:
        g: dict[Any, list[float]] = defaultdict(list)
        for f in productos:
            if "pv" in f and "k" in f and not f.get("rv"):
                g[llave(lista_rutas[f["k"]])].append(f["pv"])
        return {k: statistics.median(v) for k, v in g.items() if len(v) >= MIN_PARA_MEDIANA}

    med_sub, med_raiz = _mediana_por(lambda r: (r[0], r[1])), _mediana_por(lambda r: r[0])
    for f in productos:
        if "pv" in f and "cp" not in f and "k" in f and not f.get("rv"):
            r = lista_rutas[f["k"]]
            m = med_sub.get((r[0], r[1])) or med_raiz.get(r[0])
            if m and f["pv"] > VECES_CATEGORIA * m:
                f.setdefault("x", []).insert(0, "pg")
                f["rv"], f["pgm"] = 1, round(m, 2)

    # ESTIMADO de lo que no tiene un precio contable (sin precio o en revisión). No es
    # un precio: es «si se vendiera al múltiplo sobre costo al que HOY SE VENDE lo de su
    # categoría». El múltiplo se mide SOLO en lo que está a la venta: los precios de
    # lista de lo que no se vende son bastante más altos contra su costo, y usarlos
    # para estimar sería estimar con optimismo. Solo donde hay costo de la base o heredado;
    # para un paquete conocido, precio ÷ piezas.
    veces_raiz: dict[str, list[float]] = defaultdict(list)
    todas: list[float] = []
    for f in productos:
        if (f.get("po") == "v" and not f.get("rv") and f.get("co") == "b"
                and f["cp"] >= COSTO_MIN_PARA_VECES):
            v = f["pv"] / f["cp"]
            todas.append(v)
            if "k" in f:
                veces_raiz[lista_rutas[f["k"]][0]].append(v)
    veces_global = statistics.median(todas) if todas else None
    veces_cat = {k: statistics.median(v) for k, v in veces_raiz.items() if len(v) >= 15}
    for f in productos:
        if f["q"] <= 0 or ("pv" in f and not f.get("rv")):
            continue
        if f.get("pk") and "pv" in f:
            f["ex"] = round(f["pv"] / f["pk"], 2)
        elif f.get("co") in ("b", "h") and veces_global:
            raiz = lista_rutas[f["k"]][0] if "k" in f else None
            f["ex"] = round(f["cp"] * veces_cat.get(raiz, veces_global), 2)

    # ── Totales (los mismos que recalcula la página; aquí quedan para el reporte) ──
    def _pz(f: dict[str, Any]) -> float:
        return max(f["q"], 0)

    con = [f for f in productos if f["q"] > 0]
    tot: dict[str, Any] = {"productos": len(productos), "con_stock": len(con),
                           "piezas": sum(_pz(f) for f in con),
                           "categorias": len({lista_rutas[f["k"]][0] for f in con if "k" in f})}
    for origen, nombre in (("v", "en_venta"), ("p", "en_pausa"), ("c", "catalogo")):
        sel = [f for f in con if f.get("po") == origen and not f.get("rv")]
        tot[f"venta_{nombre}"] = round(sum(_pz(f) * f["pv"] for f in sel), 2)
        tot[f"productos_{nombre}"], tot[f"piezas_{nombre}"] = len(sel), sum(_pz(f) for f in sel)
    tot["valor_venta"] = round(sum(_pz(f) * f["pv"] for f in con if "pv" in f and not f.get("rv")), 2)
    rev = [f for f in con if f.get("rv")]
    tot["venta_en_revision"] = round(sum(_pz(f) * f["pv"] for f in rev), 2)
    tot["productos_en_revision"], tot["piezas_en_revision"] = len(rev), sum(_pz(f) for f in rev)
    sin = [f for f in con if "pv" not in f]
    tot["productos_sin_precio"], tot["piezas_sin_precio"] = len(sin), sum(_pz(f) for f in sin)
    est = [f for f in con if "ex" in f]
    tot["estimado_resto"] = round(sum(_pz(f) * f["ex"] for f in est), 2)
    tot["productos_estimados"], tot["piezas_estimadas"] = len(est), sum(_pz(f) for f in est)
    nada = [f for f in con if "ex" not in f and ("pv" not in f or f.get("rv"))]
    tot["productos_sin_estimar"], tot["piezas_sin_estimar"] = len(nada), sum(_pz(f) for f in nada)
    tot["veces_mediana"] = round(veces_global, 2) if veces_global else None
    for origen, nombre in (("b", "base"), ("h", "heredado"), ("x", "extrapolado")):
        sel = [f for f in con if f.get("co") == origen]
        tot[f"costo_{nombre}"] = round(sum(_pz(f) * f["cp"] for f in sel), 2)
        tot[f"piezas_costo_{nombre}"] = sum(_pz(f) for f in sel)
    tot["piezas_sin_costo"] = sum(_pz(f) for f in con if "cp" not in f)
    ambos = [f for f in con if "pv" in f and f.get("co") == "b" and not f.get("rv")]
    tot["venta_con_costo_base"] = round(sum(_pz(f) * f["pv"] for f in ambos), 2)
    tot["costo_de_esas"] = round(sum(_pz(f) * f["cp"] for f in ambos), 2)
    tot["categoria"] = {
        "directa": sum(1 for f in con if "k" in f and "kq" not in f),
        "por_nombre": sum(1 for f in con if f.get("kq") == "n"),
        "heredada": sum(1 for f in con if f.get("kq") == "h"),
        "estimada": sum(1 for f in con if f.get("kq") == "p"),
        "sin": sum(1 for f in con if "k" not in f)}
    tot["hallazgos"] = {c: sum(1 for f in con if c in (f.get("x") or [])) for c, _, _ in HALLAZGOS}
    tot["hallazgos"]["sn"] = sum(1 for f in productos if "sn" in (f.get("x") or []))
    por_cat: dict[str, list[float]] = defaultdict(lambda: [0, 0.0, 0.0])
    for f in con:
        c = lista_rutas[f["k"]][0] if "k" in f else "Sin categoría"
        por_cat[c][0] += 1
        por_cat[c][1] += _pz(f)
        por_cat[c][2] += 0 if f.get("rv") else _pz(f) * f.get("pv", 0)
    tot["por_categoria"] = {c: [int(v[0]), v[1], round(v[2], 2)]
                            for c, v in sorted(por_cat.items(), key=lambda kv: -kv[1][2])}

    fuentes = {
        "odoo": {"nombre": "Odoo", "leido": odoo.get("leido_hasta"), "filas": len(filas_odoo),
                 "detalle": "product.product por XML-RPC: free_qty, contenedor, foto"},
        "woo": {"nombre": "WooCommerce", "leido": woo_doc.get("leido_hasta"), "filas": len(woo),
                "detalle": "precio de catálogo y categoría de cada producto"},
        "precios": {"nombre": "Precios en marketplaces",
                    "leido": max(hora_precios) if hora_precios else None,
                    "filas": sum(leidas.values()),
                    "detalle": "publicaciones leídas en vivo, usadas como evidencia de precio"
                               + (f" (sin leer: {', '.join(no_leidos)})" if no_leidos else "")},
        "costos": {"nombre": "Base de costos", "leido": costos.get("leido_at"), "filas": len(base),
                   "detalle": "costing.costos_validados"},
    }
    doc = {
        "generado": ahora_iso(), "almacenes": odoo.get("almacenes") or [], "fuentes": fuentes,
        "parametros": costos.get("parametros") or {},
        "contenedores_base": {"total": costos.get("contenedores"),
                              "en_rango": costos.get("contenedores_en_rango")},
        "sprite": {k: imagenes.get(k) for k in ("lado", "cols", "por_hoja", "hojas")},
        "veces": {"global": round(veces_global, 2) if veces_global else None,
                  "categorias": {k: round(v, 2) for k, v in sorted(veces_cat.items())},
                  "tope_costo": VECES_MAX, "tope_categoria": VECES_CATEGORIA},
        "drive": lista_drive, "K": lista_rutas,
        "X": [[c, r, e] for c, r, e in HALLAZGOS],
        "totales": tot, "P": productos,
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
    aviso(f"página: {len(productos)} productos · {tot['con_stock']} con stock · "
          f"{tot['piezas']:,.0f} piezas · {tot['categorias']} categorías")
    aviso(f"   valor de venta: ${tot['valor_venta']:,.0f}  (en venta ${tot['venta_en_venta']:,.0f} · "
          f"en pausa ${tot['venta_en_pausa']:,.0f} · catálogo ${tot['venta_catalogo']:,.0f}) · "
          f"en revisión ${tot['venta_en_revision']:,.0f} · sin precio {tot['piezas_sin_precio']:,.0f} piezas")
    return tot
