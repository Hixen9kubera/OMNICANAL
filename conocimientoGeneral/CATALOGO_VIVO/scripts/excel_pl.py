"""
excel_pl.py — El inventario de la página, en un Excel con la FOTO de cada producto.

Una sola hoja, «Catálogo», armada como la pestaña «Por categoría» de la página: cada categoría se
abre en sus subcategorías, y cada subcategoría en sus productos (los SKUs y los productos de packing
list sin SKU), con los botones + y − del margen (el esquema de Excel). Arriba, los totales.

De cada producto: foto, SKU, título, piezas (comprado, salió, queda y lo que Odoo tiene libre), la
media de Mercado Libre con su leyenda, el valor y su estado.

No pregunta nada a ningún sistema: lee `datos_pl.json` y las hojas de fotos grandes que ya armaron
`fotos_grandes` y `pagina_pl` (`img/gNNN.jpg` y `img/qNNN.jpg`), de donde recorta la foto de cada fila.

Lo que no es obvio:
  · La foto va ANCLADA a su celda («mover y cambiar tamaño con las celdas»). Así, al filtrar o al
    cerrar una categoría, la foto de una fila oculta se esconde con ella en vez de quedar encimada.
  · openpyxl guarda una copia de la imagen por cada fila, aunque veinte tallas compartan la misma
    foto. Al final se reescribe el .xlsx para que las
    repetidas apunten a un solo archivo.
  · Tiene que cuadrar al 100% con la página. La media es EXACTAMENTE la que la página pone en su
    columna «media» (incluido lo estimado, que lleva su leyenda); los totales de cada categoría son
    los de su pestaña (solo lo que tiene packing list). Al terminar, el Excel se vuelve a leer y
    se compara fila por fila contra `datos_pl.json`: si algo no coincide, la etapa falla.
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from comun import aviso, leer_json

LADO = 160                       # píxeles de la foto que se guarda (se muestra a 100)
CAJA = 100                       # píxeles a los que se ve en la hoja
ANCHO_COL = 15.0                 # ancho de la columna de la foto (≈ 110 px)
ALTO_FILA = 80.0                 # puntos (≈ 107 px)

# Lo que lleva cada producto, en este orden, en las dos hojas (la lista agrega categoría y subcategoría).
DATOS = ["Comprado", "Salió", "Queda", "Odoo libre", "Media de Mercado Libre", "De dónde sale la media",
         "Valor a precio de ML", "Estado"]
CABECERA = ["Foto", "SKU", "Título", "Categoría", "Subcategoría", *DATOS]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]

AZUL, AZUL_CLARO, TINTA, GRIS, RAYA, ROJO = "1E3A5F", "E8EEF7", "111827", "6B7280", "E5E7EB", "B91C1C"


# ── lo que la página muestra de cada fila ───────────────────────────────────────────────────

def media_de_la_pagina(r: dict[str, Any]) -> tuple[float | None, str]:
    """Lo que la página muestra en la columna «media» de «Mercado Libre por pieza», y su leyenda.

    Es el mismo orden de `celdaPrecio` en la plantilla: (1) precio del archivo de Eduardo: la media de sus
    publicaciones, o el precio usado si no trae publicaciones; (2) medido por Competencia del panel;
    (3) estimado; (4) nada."""
    p = r.get("ml")
    if p and p.get("o") == "e":
        valor = p.get("me") if p.get("lo") is not None else p.get("m")
        cl = p.get("cl")
        de = ("revisado a mano" if cl == "r" else
              "mismo producto" + (" (del modelo)" if p.get("nv") == "x" else "") if cl == "f" else
              "banda de su categoría" + (f" «{p['cg']}»" if p.get("cg") else "") if cl == "b" else
              "sin publicaciones: nuestro precio")
        # Cuando bodega contó distinto que el proveedor, la página topa el precio que multiplica: se dice cuál.
        return valor, de + (" · sin revisar" if p.get("sr") else "") + (
            f" · topado a 10× FOB: se multiplica por ${p['m']:,.2f}" if p.get("tp") else "")
    firme = bool(p and not r.get("sin") and p.get("n", 0) >= 2 and not p.get("x") and p.get("o") != "c")
    if firme:
        return p.get("m"), "mismo producto (Competencia del panel)"
    if r.get("em"):
        return r["em"][0], "estimado"
    return None, ("insumo: no se valúa" if r.get("nv") else "")


def valor_de_la_pagina(r: dict[str, Any]) -> float | None:
    """El valor de la fila como lo calcula la página: piezas que quedan × el precio que usa (la media, o el
    topado, o el estimado). Lo que no tiene packing list no tiene piezas que valuar."""
    if r.get("pc") is None:
        return None
    p = r.get("ml")
    con_precio = bool(p and (p.get("o") == "e" or (not r.get("sin") and p.get("n", 0) >= 2 and not p.get("x") and p.get("o") != "c")))
    usado = p["m"] if con_precio else (r["em"][0] if r.get("em") else None)
    return round(r["pq"] * usado, 2) if usado is not None else None


def categoria_de(r: dict[str, Any], rutas_cat: list[list[str]]) -> tuple[str, str]:
    if r.get("k") is None:
        return ("Sin categoría", "")
    k = rutas_cat[r["k"]]
    return (k[0], k[1] or "")


def datos_de_la_pagina(r: dict[str, Any]) -> tuple:
    """SKU, título y los ocho datos de `DATOS`, tal como los muestra la página."""
    con_pl = r.get("pc") is not None
    # Igual que la tabla de la página: sin packing list, «comprado» es una raya, o «≈ N según costos» si la
    # base de costos lo manda a un contenedor (esas piezas no suman: ya están entre los productos sin SKU).
    comprado = r["pc"] if con_pl else (f"≈ {round(r['pe']):,} según costos" if r.get("pe") is not None else None)
    media, de = media_de_la_pagina(r)
    estado = ", ".join(x for x in (
        "sin SKU" if r.get("sin") else "",
        "insumo, no se valúa" if r.get("nv") else "",
        "SOLD OUT" if r.get("so") else "",
        "renglón no ubicado" if (not con_pl and r.get("pe") is not None) else "",
        "sin packing list" if (not con_pl and r.get("pe") is None) else "") if x)
    return ("Sin SKU" if r.get("sin") else r["s"], r.get("t") or "", comprado,
            (r.get("pz") or r.get("ps")) if r.get("ps") else 0,
            r.get("pq") if con_pl else None,
            r.get("lb") if not r.get("sin") else None,
            media, de, valor_de_la_pagina(r), estado)


def fila_de_la_pagina(r: dict[str, Any], rutas_cat: list[list[str]]) -> tuple:
    """La fila de la «Lista completa»: SKU, título, categoría, subcategoría y los ocho datos."""
    d = datos_de_la_pagina(r)
    return (d[0], d[1], *categoria_de(r, rutas_cat), *d[2:])


def totales(filas: list[dict[str, Any]]) -> dict[str, Any]:
    """Los totales de un grupo, con las reglas de la función `sumar` de la página: solo cuentan las filas con
    packing list, y lo que salió no pasa de lo comprado."""
    con = [r for r in filas if r.get("pc") is not None]
    return {
        "skus": sum(1 for r in con if not r.get("sin")), "sin": sum(1 for r in con if r.get("sin")),
        "so": sum(1 for r in con if r.get("so")), "sin_pl": len(filas) - len(con),
        "pc": round(sum(r["pc"] for r in con)),
        "ps": round(sum(min(r.get("pz") or r.get("ps") or 0, r["pc"]) for r in con)),
        "pq": round(sum(r["pq"] for r in con)),
        "lb": round(sum(r.get("lb") or 0 for r in filas)),
        "valor": round(sum(valor_de_la_pagina(r) or 0 for r in con), 2),
    }


def arbol(doc: dict[str, Any]) -> list[tuple[str, list[tuple[str, list[dict[str, Any]]]]]]:
    """Categoría → subcategoría → productos, en el orden de la página: lo que más piezas tiene, primero."""
    grupos: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for r in doc["R"]:
        c1, c2 = categoria_de(r, doc["K"])
        grupos.setdefault(c1, {}).setdefault(c2 or "(sin subcategoría)", []).append(r)
    piezas = lambda l: sum((r.get("pq") or 0) for r in l)  # noqa: E731
    salida = []
    for c1 in sorted(grupos, key=lambda k: (-piezas([r for l in grupos[k].values() for r in l]), k)):
        subs = [(c2, sorted(grupos[c1][c2], key=lambda r: (-(r.get("pq") or 0), r["s"])))
                for c2 in sorted(grupos[c1], key=lambda k: (-piezas(grupos[c1][k]), k))]
        salida.append((c1, subs))
    return salida


# ── fotos ───────────────────────────────────────────────────────────────────────────────────

def _recortes(salida: Path, filas: list[dict[str, Any]], doc: dict[str, Any]) -> dict[tuple[str, int], Path]:
    """La foto de cada fila, recortada de su hoja grande. Una por foto distinta (g = Odoo, q = packing list)."""
    from PIL import Image

    destino = salida / "cache" / "excel_img"
    destino.mkdir(parents=True, exist_ok=True)
    quiero: dict[str, set[int]] = {"g": set(), "q": set()}
    for r in filas:
        if r.get("i") is not None:
            quiero["g"].add(r["i"])
        elif r.get("j") is not None:
            quiero["q"].add(r["j"])
    rutas: dict[tuple[str, int], Path] = {}
    for pre, sprite in (("g", doc.get("spriteG") or {}), ("q", doc.get("spriteQ") or {})):
        if not sprite or not sprite.get("hojas"):
            continue
        lado, cols, por = sprite["lado"], sprite["cols"], sprite["por_hoja"]
        por_hoja: dict[int, list[int]] = {}
        for i in quiero[pre]:
            por_hoja.setdefault(i // por, []).append(i)
        for h, indices in sorted(por_hoja.items()):
            ruta_hoja = salida / "img" / f"{pre}{h:03d}.jpg"
            if not ruta_hoja.exists():
                continue
            hoja = None
            for i in indices:
                ruta = destino / f"{pre}_{i}.jpg"
                rutas[(pre, i)] = ruta
                if ruta.exists() and ruta.stat().st_mtime >= ruta_hoja.stat().st_mtime:
                    continue
                hoja = hoja or Image.open(ruta_hoja).convert("RGB")
                n = i % por
                x, y = (n % cols) * lado, (n // cols) * lado
                hoja.crop((x, y, x + lado, y + lado)).resize((LADO, LADO), Image.LANCZOS).save(
                    ruta, "JPEG", quality=82, optimize=True)
    return rutas


def _sin_repetir(ruta: Path) -> tuple[int, int]:
    """Reescribe el .xlsx para que las fotos idénticas compartan un solo archivo. Devuelve (antes, después)."""
    with zipfile.ZipFile(ruta) as z:
        nombres = z.namelist()
        contenido = {n: z.read(n) for n in nombres}
    medios = [n for n in nombres if n.startswith("xl/media/")]
    primero: dict[str, str] = {}
    cambia: dict[str, str] = {}
    for n in medios:
        h = hashlib.md5(contenido[n]).hexdigest()
        if h in primero:
            cambia[n.split("/")[-1]] = primero[h].split("/")[-1]
        else:
            primero[h] = n
    if not cambia:
        return len(medios), len(medios)
    patron = re.compile(rb'Target="(?:\.\./|/xl/)media/([^"]+)"')
    for n in nombres:
        if n.startswith("xl/drawings/_rels/") and n.endswith(".rels"):
            contenido[n] = patron.sub(
                lambda m: m.group(0).replace(m.group(1), cambia.get(m.group(1).decode(), m.group(1).decode()).encode()),
                contenido[n])
    quitar = {"xl/media/" + k for k in cambia}
    tmp = ruta.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for n in nombres:
            if n in quitar:
                continue
            # las fotos ya vienen comprimidas: guardarlas tal cual abre más rápido
            z.writestr(n, contenido[n], compress_type=zipfile.ZIP_STORED if n.startswith("xl/media/") else zipfile.ZIP_DEFLATED)
    tmp.replace(ruta)
    return len(medios), len(medios) - len(cambia)


# ── comprobación ────────────────────────────────────────────────────────────────────────────

def _norma(t: tuple) -> tuple:
    return tuple(round(float(x), 2) if isinstance(x, (int, float)) and not isinstance(x, bool)
                 else (x if x not in ("", None) else None) for x in t)


def comprobar(ruta: Path, doc: dict[str, Any]) -> dict[str, Any]:
    """Vuelve a leer el Excel ya escrito y lo compara contra los datos de la página: debe traer exactamente
    las mismas filas, cada una en su categoría y subcategoría, y cada grupo con sus mismos totales."""
    from openpyxl import load_workbook

    esperado = Counter(_norma(fila_de_la_pagina(r, doc["K"])) for r in doc["R"])
    libro = load_workbook(ruta, read_only=True)
    # La última columna (oculta) dice el nivel de cada fila: 1 categoría, 2 subcategoría, 3 producto.
    esquema: Counter = Counter()
    grupos_mal = 0
    cat = sub = ""
    por_grupo = {(c1, c2): totales(l) for c1, subs in arbol(doc) for c2, l in subs}
    por_cat = {c1: totales([r for _, l in subs for r in l]) for c1, subs in arbol(doc)}
    empieza = False
    for fila in libro["Catálogo"].iter_rows(min_col=2, max_col=12, values_only=True):
        nivel = fila[-1]
        if nivel == "Nivel":
            empieza = True
            continue
        if not empieza or nivel not in (1, 2, 3):
            continue
        if nivel == 1:
            cat = fila[0]
            t = por_cat.get(cat)
            grupos_mal += 0 if t and _norma((fila[2], fila[3], fila[4], fila[8])) == _norma((t["pc"], t["ps"], t["pq"], t["valor"])) else 1
        elif nivel == 2:
            sub = fila[0]
            t = por_grupo.get((cat, sub))
            grupos_mal += 0 if t and _norma((fila[2], fila[3], fila[4], fila[8])) == _norma((t["pc"], t["ps"], t["pq"], t["valor"])) else 1
        else:
            esquema[_norma((fila[0], fila[1], cat, "" if sub == "(sin subcategoría)" else sub, *fila[2:10]))] += 1
    skus_pag = Counter(("Sin SKU" if r.get("sin") else r["s"]) for r in doc["R"])
    return {"filas_pagina": len(doc["R"]),
            "iguales": sum((esquema & esperado).values()),
            "distintas": sum((esquema - esperado).values()) + sum((esperado - esquema).values()),
            "grupos": len(por_cat) + len(por_grupo), "grupos_mal": grupos_mal,
            "skus_iguales": skus_pag == Counter(t[0] for t in esquema.elements()),
            "ejemplo_distinto": [list(map(str, x)) for x in list((esperado - esquema))[:2] + list((esquema - esperado))[:2]]}


# ── el libro ────────────────────────────────────────────────────────────────────────────────

def construir(salida: Path) -> dict[str, Any]:
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as Foto
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils.units import pixels_to_EMU

    doc = leer_json(salida / "datos_pl.json")
    if not doc:
        raise RuntimeError("falta datos_pl.json: corre antes `pagina_pl`")
    rutas_cat = doc["K"]
    fotos = _recortes(salida, doc["R"], doc)
    aviso(f"excel: {len(doc['R'])} filas · {len(fotos)} fotos distintas recortadas")

    letra = "Calibri"
    raya = Side(style="thin", color=RAYA)
    centro = Alignment(vertical="center", wrap_text=True, indent=1)
    centro_sangria = Alignment(vertical="center", indent=2)
    derecha = Alignment(vertical="center", horizontal="right", indent=1)
    margen = pixels_to_EMU(4)

    def poner_foto(hoja: Any, r: dict[str, Any], fila: int) -> int:
        ruta = fotos.get(("g", r["i"])) if r.get("i") is not None else (
            fotos.get(("q", r["j"])) if r.get("j") is not None else None)
        if ruta is None or not ruta.exists():
            return 0
        foto = Foto(str(ruta))
        ancla = TwoCellAnchor(editAs="twoCell")                    # se mueve y cambia de tamaño con su celda
        ancla._from = AnchorMarker(col=0, colOff=margen, row=fila - 1, rowOff=margen)
        ancla.to = AnchorMarker(col=0, colOff=margen + pixels_to_EMU(CAJA), row=fila - 1, rowOff=margen + pixels_to_EMU(CAJA))
        foto.anchor = ancla
        hoja.add_image(foto)
        return 1

    def vestir_producto(hoja: Any, fila: int, primera_dato: int, estado: str) -> None:
        """El formato de una fila de producto. `primera_dato` es la columna de «Comprado»."""
        hoja.row_dimensions[fila].height = ALTO_FILA
        hoja.cell(row=fila, column=2).font = Font(name="Consolas", bold=True, size=10, color=TINTA)
        hoja.cell(row=fila, column=2).alignment = centro
        for j in range(3, primera_dato):
            hoja.cell(row=fila, column=j).alignment = centro
            hoja.cell(row=fila, column=j).font = Font(name=letra, size=10, color=TINTA)
        for j, formato in zip(range(primera_dato, primera_dato + 8),
                              ("#,##0", "#,##0", "#,##0", "#,##0", '"$"#,##0.00', None, '"$"#,##0', None)):
            c = hoja.cell(row=fila, column=j)
            c.font = Font(name=letra, size=10, color=TINTA)
            if formato:
                c.number_format, c.alignment = formato, derecha
            else:
                c.alignment = centro
        hoja.cell(row=fila, column=primera_dato + 2).font = Font(name=letra, size=10, bold=True, color=TINTA)      # queda
        hoja.cell(row=fila, column=primera_dato + 5).font = Font(name=letra, size=9, color=GRIS)                   # leyenda
        hoja.cell(row=fila, column=primera_dato + 7).font = Font(
            name=letra, size=9, bold="SOLD OUT" in estado, italic="insumo" in estado,
            color=ROJO if "SOLD OUT" in estado else GRIS)
        for j in range(1, primera_dato + 8):
            hoja.cell(row=fila, column=j).border = Border(bottom=raya)

    def encabezado(hoja: Any, fila: int, titulos: list[str]) -> None:
        for j, texto in enumerate(titulos, start=1):
            c = hoja.cell(row=fila, column=j, value=texto)
            c.font = Font(name=letra, bold=True, size=10, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor=TINTA)
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        hoja.row_dimensions[fila].height = 34

    wb = Workbook()

    # ── Catálogo: categoría → subcategoría → productos, plegable ────────────────────────────
    hoja = wb.active
    hoja.title = "Catálogo"
    hoja.sheet_view.showGridLines = False
    hoja.sheet_properties.outlinePr.summaryBelow = False
    hoja.sheet_properties.tabColor = AZUL
    general = totales(doc["R"])
    fecha = str(doc.get("generado") or "")
    try:
        from datetime import datetime

        local = datetime.fromisoformat(fecha).astimezone()          # el dato viene en UTC: se dice en hora local
        fecha = f"{local.day} de {MESES[local.month - 1]} de {local.year}"
    except ValueError:
        fecha = fecha[:10]
    hoja["A1"] = "Inventario Kubera · contado desde los packing lists"
    hoja["A1"].font = Font(name=letra, size=18, bold=True, color=AZUL)
    hoja["A2"] = (f"Corte del {fecha}  ·  {general['skus']:,} SKUs y {general['sin']:,} productos sin SKU con packing list  ·  "
                  f"Comprado {general['pc']:,}  ·  Salió {general['ps']:,}  ·  Queda {general['pq']:,} piezas  ·  "
                  f"Valor a precio de Mercado Libre ${general['valor']:,.0f}")
    hoja["A2"].font = Font(name=letra, size=11, color=TINTA)
    hoja["A3"] = ("Abre una categoría con el botón + del margen izquierdo para ver sus subcategorías, y una subcategoría para ver "
                  "sus productos. Los botones 1, 2 y 3 de la esquina abren o cierran todo de un golpe.")
    hoja["A3"].font = Font(name=letra, size=10, italic=True, color=GRIS)
    hoja.row_dimensions[1].height, hoja.row_dimensions[2].height, hoja.row_dimensions[3].height = 30, 20, 18
    hoja.row_dimensions[4].height = 8
    CAB = 5
    encabezado(hoja, CAB, ["Foto", "Categoría · Subcategoría · SKU", "Título", *DATOS, "Nivel"])
    for col, ancho in zip("ABCDEFGHIJKL", (ANCHO_COL, 30, 54, 13, 11, 12, 12, 16, 36, 18, 22, 6)):
        hoja.column_dimensions[col].width = ancho
    hoja.column_dimensions["L"].hidden = True                      # el nivel de la fila: lo usa la comprobación

    def fila_grupo(nombre: str, t: dict[str, Any], nivel: int) -> int:
        cuenta = f"{t['skus']:,} SKUs" + (f" · {t['sin']:,} sin SKU" if t["sin"] else "") + (
            f" · {t['so']:,} SOLD OUT" if t["so"] else "") + (f" · {t['sin_pl']:,} sin packing list" if t["sin_pl"] else "")
        hoja.append([None, nombre, cuenta, t["pc"], t["ps"], t["pq"], t["lb"], None, None, t["valor"], None, nivel])
        n = hoja.max_row
        claro = nivel == 2
        relleno = PatternFill("solid", fgColor=AZUL_CLARO if claro else AZUL)
        color = AZUL if claro else "FFFFFF"
        for j in range(1, 12):
            c = hoja.cell(row=n, column=j)
            c.fill = relleno
            c.font = Font(name=letra, bold=True, size=10 if claro else 11, color=color)
            c.alignment = derecha if j in (4, 5, 6, 7, 10) else (centro_sangria if (j == 2 and claro) else Alignment(vertical="center", indent=1))
        hoja.cell(row=n, column=3).font = Font(name=letra, size=9, color=GRIS if claro else "D1D5DB")
        for j in (4, 5, 6, 7):
            hoja.cell(row=n, column=j).number_format = "#,##0"
        hoja.cell(row=n, column=10).number_format = '"$"#,##0'
        hoja.row_dimensions[n].height = 22 if claro else 27
        return n

    con_foto = 0
    for c1, subs in arbol(doc):
        n = fila_grupo(c1, totales([r for _, l in subs for r in l]), 1)
        hoja.row_dimensions[n].collapsed = True
        for c2, lista in subs:
            n = fila_grupo(c2, totales(lista), 2)
            hoja.row_dimensions[n].outlineLevel, hoja.row_dimensions[n].hidden = 1, True
            hoja.row_dimensions[n].collapsed = True
            for r in lista:
                d = datos_de_la_pagina(r)
                hoja.append([None, *d, 3])
                n = hoja.max_row
                vestir_producto(hoja, n, 4, d[9])
                hoja.cell(row=n, column=2).alignment = Alignment(vertical="center", indent=3, wrap_text=True)
                hoja.row_dimensions[n].outlineLevel, hoja.row_dimensions[n].hidden = 2, True
                con_foto += poner_foto(hoja, r, n)
    hoja.append([None, "TOTAL", f"{general['skus']:,} SKUs · {general['sin']:,} sin SKU · {general['so']:,} SOLD OUT",
                 general["pc"], general["ps"], general["pq"], general["lb"], None, None, general["valor"], None, 0])
    n = hoja.max_row
    for j in range(1, 12):
        c = hoja.cell(row=n, column=j)
        c.font = Font(name=letra, bold=True, size=11, color=TINTA)
        c.border = Border(top=Side(style="medium", color=TINTA))
        c.alignment = derecha if j in (4, 5, 6, 7, 10) else Alignment(vertical="center", indent=1)
    for j in (4, 5, 6, 7):
        hoja.cell(row=n, column=j).number_format = "#,##0"
    hoja.cell(row=n, column=10).number_format = '"$"#,##0'
    hoja.row_dimensions[n].height = 27
    hoja.oddFooter.center.text = "Inventario Kubera · página &P de &N"
    hoja.freeze_panes = hoja.cell(row=CAB + 1, column=3)
    hoja.sheet_view.zoomScale = 90
    hoja.page_setup.orientation, hoja.page_setup.fitToWidth, hoja.page_setup.fitToHeight = "landscape", 1, 0
    hoja.sheet_properties.pageSetUpPr.fitToPage = True
    hoja.print_title_rows = f"{CAB}:{CAB}"

    filas = doc["R"]

    destino = salida / "inventario_kubera_skus.xlsx"
    wb.save(destino)
    antes, despues = _sin_repetir(destino)
    peso = destino.stat().st_size / 1e6
    aviso(f"excel: {destino.name} · {len(filas)} productos · {con_foto} con foto · {antes} imágenes → {despues} sin repetir · {peso:.1f} MB")
    chequeo = comprobar(destino, doc)
    aviso(f"excel: comprobación contra la página: {chequeo['iguales']} de {chequeo['filas_pagina']} filas idénticas · "
          f"{chequeo['grupos'] - chequeo['grupos_mal']} de {chequeo['grupos']} totales de categoría y subcategoría iguales · "
          f"mismos SKUs: {chequeo['skus_iguales']}")
    if chequeo["distintas"] or chequeo["grupos_mal"] or not chequeo["skus_iguales"]:
        raise RuntimeError(f"el Excel NO cuadra con la página: {chequeo}")
    return {"filas": len(filas), "con_foto": con_foto, "imagenes": despues, "mb": round(peso, 1),
            "con_media_ml": sum(1 for r in filas if media_de_la_pagina(r)[0] is not None), "comprobacion": chequeo}
