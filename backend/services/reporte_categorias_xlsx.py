"""
reporte_categorias_xlsx.py — El xlsx de "ventas por categoría" (réplica del
reporte de José, Drive 19-jul) generado EN VIVO desde la BD kubera con los
filtros que el usuario eligió en /analisis/categorias.

Dos hojas, como el original:
  - Resumen:    una fila por categoría PRINCIPAL (raíz de la ruta ML) con SKUs
                con venta, unidades, ventas $, %% del total, publicaciones y
                activas. Totales y %% con FÓRMULAS (el archivo recalcula solo).
  - Categorias: el árbol completo (todos los niveles de la ruta) con subtotales
                SUBTOTAL(9,…) por nivel y, bajo cada hoja, sus publicaciones:
                SKU, tienda, título, MLM ID, situación, uds, venta $, precio,
                1ª y última venta del período.

Diferencias declaradas vs el xlsx original (las 3 limitaciones aceptadas por
Eduardo, 04-ago): sin columna de margen (queda para después); "Días en venta"
no existe (listings no guarda la fecha de alta → va 1ª venta del período); la
venta es la REAL del período (sales_daily_completa), no sold_quantity × precio
actual como en el snapshot de ML de José.
"""
from __future__ import annotations

import io
from collections import defaultdict
from typing import Any

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

TIENDA = {"BEKURA": "Bekura", "SANCORFASHION": "Sancor", "AMAZON": "Amazon"}
_CAB_FILL = PatternFill("solid", fgColor="1F3864")
_NIVEL_FILL = ["BDD7EE", "DDEBF7", "F2F7FC", "FAFCFE"]
_MONEY, _INT = "$#,##0", "#,##0"


def _f(**kw) -> Font:
    return Font(name="Arial", **{"size": 10, **kw})


class _Nodo:
    __slots__ = ("nombre", "hijos", "hojas")

    def __init__(self, nombre: str):
        self.nombre = nombre
        self.hijos: dict[str, _Nodo] = {}
        self.hojas: list[dict] = []  # hojas del árbol ML que caen aquí


def _arbol(hojas: list[dict]) -> tuple[dict[str, _Nodo], dict[str, dict]]:
    """Raíces del árbol + acumulados (uds/venta/pubs/activas/skus) por nodo."""
    raices: dict[str, _Nodo] = {}
    acum: dict[str, dict] = defaultdict(lambda: {"uds": 0, "venta": 0.0,
                                                 "pubs": 0, "activas": 0,
                                                 "skus": 0})
    for h in hojas:
        partes = [p.strip() for p in str(h["ruta"]).split("›") if p.strip()] \
            or ["Sin categoría"]
        n = raices.setdefault(partes[0], _Nodo(partes[0]))
        clave = partes[0]
        for parte in partes[1:]:
            n = n.hijos.setdefault(parte, _Nodo(parte))
        n.hojas.append(h)
        for i in range(len(partes)):
            clave = "›".join(partes[: i + 1])
            a = acum[clave]
            a["uds"] += int(h["uds"] or 0)
            a["venta"] += float(h["venta"] or 0)
            a["pubs"] += int(h["publicaciones"] or 0)
            a["activas"] += int(h["activas"] or 0)
            a["skus"] += int(h["skus"] or 0)
    return raices, acum


def construir(hojas: list[dict], pubs: list[dict], desde: str, hasta: str,
              cuenta: str | None) -> bytes:
    """Arma el workbook y lo devuelve como bytes (para StreamingResponse)."""
    pubs_por_cat: dict[str, list[dict]] = defaultdict(list)
    for p in pubs:
        pubs_por_cat[str(p["category_id"])].append(p)
    raices, acum = _arbol(hojas)
    # "Sin categoría" SIEMPRE al final, venda lo que venda (Eduardo, 05-ago)
    orden_raiz = sorted(raices,
                        key=lambda r: (r == "Sin categoría", -acum[r]["venta"]))

    wb = Workbook()

    # ── hoja Categorias ──────────────────────────────────────────────────────
    ws = wb.create_sheet("Categorias")
    cabs = ["Categoría / SKU", "Tienda", "Título", "MLM ID", "Situación",
            "Ventas Uds", "Ventas $", "Precio", "1ª venta", "Últ. venta"]
    for c, t in enumerate(cabs, 1):
        cel = ws.cell(1, c, t)
        cel.font = _f(bold=True, color="FFFFFF")
        cel.fill = _CAB_FILL
    ws.freeze_panes = "A2"
    # Agrupación nativa de Excel (botones +/− al margen): el encabezado de cada
    # categoría queda ARRIBA de su bloque, así que el resumen va arriba.
    ws.sheet_properties.outlinePr.summaryBelow = False
    fila = 2
    # El archivo abre TOTALMENTE plegado: solo las categorías principales
    # (Eduardo, 05-ago); todo lo demás se abre con los +. Tope Excel: 7 niveles.
    _VISIBLE_HASTA = 0  # profundidad máxima visible al abrir

    def _outline(r: int, nivel: int, oculta: bool) -> None:
        rd = ws.row_dimensions[r]
        rd.outlineLevel = min(nivel, 7)
        if oculta:
            rd.hidden = True

    def pinta(n: _Nodo, ruta: str, depth: int) -> None:
        nonlocal fila
        r0 = fila
        a = acum[ruta]
        cel = ws.cell(r0, 1, f"{n.nombre} ({a['pubs']} pub)")
        cel.font = _f(bold=True)
        cel.alignment = Alignment(indent=depth)
        fill = PatternFill("solid", fgColor=_NIVEL_FILL[min(depth, 3)])
        for c in range(1, 11):
            ws.cell(r0, c).fill = fill
        if depth:  # las raíces (nivel 0) no pertenecen a ningún grupo
            _outline(r0, depth, depth > _VISIBLE_HASTA)
        if depth >= _VISIBLE_HASTA:  # sus hijos abren plegados
            ws.row_dimensions[r0].collapsed = True
        fila += 1
        items = sorted((p for h in n.hojas
                        for p in pubs_por_cat.get(str(h["category_id"]), [])),
                       key=lambda p: -float(p["venta"] or 0))
        for p in items:
            ws.cell(fila, 1, (p.get("sku") or "(sin SKU)")).font = _f()
            ws.cell(fila, 1).alignment = Alignment(indent=depth + 1)
            ws.cell(fila, 2, TIENDA.get(p.get("cuenta") or "", p.get("cuenta"))).font = _f()
            ws.cell(fila, 3, p.get("titulo") or "").font = _f()
            ws.cell(fila, 4, p.get("item_id") or "").font = _f()
            ws.cell(fila, 5, p.get("situacion") or "").font = _f()
            ws.cell(fila, 6, int(p.get("uds") or 0)).font = _f()
            ws.cell(fila, 6).number_format = _INT
            ws.cell(fila, 7, float(p.get("venta") or 0)).font = _f()
            ws.cell(fila, 7).number_format = _MONEY
            if p.get("precio") is not None:
                ws.cell(fila, 8, float(p["precio"])).font = _f()
                ws.cell(fila, 8).number_format = _MONEY
            ws.cell(fila, 9, p.get("primera_venta") or "").font = _f()
            ws.cell(fila, 10, p.get("ultima_venta") or "").font = _f()
            # las publicaciones cuelgan un nivel bajo su categoría
            _outline(fila, depth + 1, depth + 1 > _VISIBLE_HASTA)
            fila += 1
        for h in sorted(n.hijos.values(),
                        key=lambda x: -acum[f"{ruta}›{x.nombre}"]["venta"]):
            pinta(h, f"{ruta}›{h.nombre}", depth + 1)
        r1 = fila - 1
        if r1 > r0:
            ws.cell(r0, 6, f"=SUBTOTAL(9,F{r0 + 1}:F{r1})").font = _f(bold=True)
            ws.cell(r0, 6).number_format = _INT
            ws.cell(r0, 7, f"=SUBTOTAL(9,G{r0 + 1}:G{r1})").font = _f(bold=True)
            ws.cell(r0, 7).number_format = _MONEY
        else:  # nodo sin publicaciones desglosadas: valores del acumulado
            ws.cell(r0, 6, a["uds"]).font = _f(bold=True)
            ws.cell(r0, 6).number_format = _INT
            ws.cell(r0, 7, round(a["venta"], 2)).font = _f(bold=True)
            ws.cell(r0, 7).number_format = _MONEY

    for r in orden_raiz:
        pinta(raices[r], r, 0)
    for c, w in {1: 42, 2: 9, 3: 60, 4: 15, 5: 10, 6: 10, 7: 12, 8: 10,
                 9: 11, 10: 11}.items():
        ws.column_dimensions[get_column_letter(c)].width = w
    ult = fila - 1

    # ── hoja Resumen ─────────────────────────────────────────────────────────
    rs = wb["Sheet"]
    rs.title = "Resumen"
    rs["A1"] = "Ventas por categoría"
    rs["A1"].font = _f(bold=True, size=12)
    rs["B1"] = f"{desde} → {hasta}"
    rs["B1"].font = _f(bold=True)
    rs["C1"] = f"Cuenta: {TIENDA.get(cuenta or '', cuenta) or 'todas'}"
    rs["C1"].font = _f(bold=True)
    rs["D1"] = ("Venta real del período (pedidos + histórico dailytrack), sin "
                "comisión ni costo. Margen: pendiente (acordado 04-ago).")
    rs["D1"].font = _f(italic=True, size=9)

    cabs_r = ["Categoría Principal", "SKUs con venta", "Ventas Uds", "Ventas $",
              "% Ventas $", "Publicaciones", "Activas"]
    for c, t in enumerate(cabs_r, 1):
        cel = rs.cell(3, c, t)
        cel.font = _f(bold=True, color="FFFFFF")
        cel.fill = _CAB_FILL
    rs.freeze_panes = "A4"
    tot_row = 4 + len(orden_raiz)
    for i, r in enumerate(orden_raiz):
        rr = 4 + i
        a = acum[r]
        rs.cell(rr, 1, r).font = _f()
        rs.cell(rr, 2, a["skus"]).font = _f()
        rs.cell(rr, 3, a["uds"]).font = _f()
        rs.cell(rr, 4, round(a["venta"], 2)).font = _f()
        rs.cell(rr, 5, f"=IF($D${tot_row}=0,0,D{rr}/$D${tot_row})").font = _f()
        rs.cell(rr, 6, a["pubs"]).font = _f()
        rs.cell(rr, 7, a["activas"]).font = _f()
        for c, fmt in ((2, _INT), (3, _INT), (4, _MONEY), (5, "0.0%"),
                       (6, _INT), (7, _INT)):
            rs.cell(rr, c).number_format = fmt
    rs.cell(tot_row, 1, "TOTAL").font = _f(bold=True)
    for col, fmt in (("B", _INT), ("C", _INT), ("D", _MONEY), ("F", _INT),
                     ("G", _INT)):
        rs.cell(tot_row, ord(col) - 64,
                f"=SUM({col}4:{col}{tot_row - 1})").font = _f(bold=True)
        rs.cell(tot_row, ord(col) - 64).number_format = fmt
    rs.cell(tot_row, 5, f"=IF($D${tot_row}=0,0,1)").font = _f(bold=True)
    rs.cell(tot_row, 5).number_format = "0.0%"
    rs.cell(3, 2).comment = Comment(
        "SKUs distintos con venta en el período. En categorías con "
        "sub-clasificación doble un SKU puede contar en dos ramas (misma "
        "convención que la página).", "OMNICANAL")
    for c, w in {1: 32, 2: 13, 3: 11, 4: 13, 5: 10, 6: 13, 7: 9}.items():
        rs.column_dimensions[get_column_letter(c)].width = w

    _ = ult  # (referencia futura: filas totales de la hoja Categorias)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
