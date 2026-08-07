"""
reporte_inventario_xlsx.py — Inventario ACCIONABLE, no un volcado del almacén.

DOS HOJAS, dos problemas opuestos (Eduardo, 7-ago):

  INMOVILIZADO — hay stock en FULL y no vende. Ahí no es solo capital
    detenido: paga renta a Mercado Libre todos los días. El mercado no lo
    quiere. Censo del 7-ago: 14,873 unidades en FULL sin una sola venta en 30
    días —el 39%% de todo lo que tenemos allá— y las mayores NUNCA han vendido
    una pieza (JUGU-0261-LIL: 272 en FULL + 648 en bodega, cero ventas
    históricas).

  INVISIBLE — vende, pero ninguna publicación está activa. El mercado sí lo
    quiere y no se lo estamos ofreciendo. Caso canónico TEC-0393-ROS: 291
    unidades vendidas en 30 días, 2,394 en bodega, las dos publicaciones
    pausadas.

Es el problema más barato de arreglar de los dos: no hay que comprar, mover ni
liquidar nada — solo reactivar.

SIN VALORIZAR EN DINERO, a propósito. Es la misma trampa del margen que se
retiró del otro reporte: `costo_producto` es un precio USD×19 de relleno en
~30%% del catálogo, así que un total de "inventario valuado en $X" sería
ficción con formato de hecho. Lo que sí se puede medir con datos confiables es
CUÁNTO hay, DÓNDE está y CUÁNTO TIEMPO lleva sin moverse.
"""
from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

_CAB_FILL = PatternFill("solid", fgColor="1F3864")
_AVISO_FILL = PatternFill("solid", fgColor="FFF2CC")
_GRAVE_FILL = PatternFill("solid", fgColor="FCE4E4")
_INT = "#,##0"

# WooCommerce NO va aquí a propósito: no es un canal de venta sino nuestro
# puente de registro, y la consulta ya lo excluye de la lista de cuentas. Si
# alguna vez se colara, saldría como "GENERAL" en mayúsculas — una señal
# visible, mejor que pintarlo como si fuera una tienda más.
TIENDA = {"BEKURA": "Bekura", "SANCORFASHION": "Sancor", "AMAZON": "Amazon"}


def _f(**kw) -> Font:
    return Font(name="Arial", **{"size": 10, **kw})


def _cuentas(v: Any) -> str:
    if not v:
        return ""
    return ", ".join(TIENDA.get(c, c) for c in v if c)


def _encabezar(ws, cabs: list[str], nota: str, anchos: dict[int, int]) -> None:
    """Título explicativo en la fila 1 y encabezados en la 3.

    La nota va DENTRO del archivo porque el Excel se comparte fuera del panel:
    un criterio que solo vive en la pantalla no viaja con el adjunto.
    """
    ws["A1"] = nota
    ws["A1"].font = _f(italic=True, size=9)
    ws["A1"].alignment = Alignment(vertical="top")
    for c, t in enumerate(cabs, 1):
        cel = ws.cell(3, c, t)
        cel.font = _f(bold=True, color="FFFFFF")
        cel.fill = _CAB_FILL
    ws.freeze_panes = "A4"
    ws.auto_filter.ref = f"A3:{get_column_letter(len(cabs))}3"
    for c, w in anchos.items():
        ws.column_dimensions[get_column_letter(c)].width = w


def _hoja_inmovilizado(wb: Workbook, filas: list[dict], dias: int) -> None:
    ws = wb.create_sheet("Inmovilizado")
    cabs = ["SKU", "Título", "En FULL", "FULL Bekura", "FULL Sancor",
            "En bodega propia", "Publicaciones activas", "Pausadas",
            "Cuentas", "Última venta", "Días sin vender", "Diagnóstico"]
    total_uds = sum(int(f.get("full_total") or 0) for f in filas)
    nunca = sum(1 for f in filas if not f.get("ultima_venta"))
    _encabezar(ws, cabs,
               f"INMOVILIZADO — stock en FULL que NO vendió una sola pieza en "
               f"los últimos {dias} días. Ahí paga almacenaje a Mercado Libre "
               f"todos los días, venda o no. {len(filas):,} SKUs, "
               f"{total_uds:,} unidades; {nunca:,} nunca han vendido nada. "
               f"Ordenado por unidades: arriba está lo que más renta paga sin "
               f"devolver nada. No incluye el stock parado en bodega propia "
               f"(ese no paga renta) ni valor en dinero (el costo capturado no "
               f"es de fiar en ~30% del catálogo).",
               {1: 22, 2: 46, 3: 10, 4: 12, 5: 12, 6: 16, 7: 18, 8: 10,
                9: 16, 10: 12, 11: 14, 12: 62})
    for i, f in enumerate(filas):
        r = 4 + i
        dias_sin = f.get("dias_sin_vender")
        ws.cell(r, 1, f.get("sku") or "").font = _f()
        ws.cell(r, 2, (f.get("titulo") or "")[:120]).font = _f()
        for c, k in ((3, "full_total"), (4, "full_bk"), (5, "full_sc"),
                     (6, "propio"), (7, "activas"), (8, "pausadas")):
            ws.cell(r, c, int(f.get(k) or 0)).font = _f()
            ws.cell(r, c).number_format = _INT
        ws.cell(r, 9, _cuentas(f.get("cuentas"))).font = _f()
        ws.cell(r, 10, f.get("ultima_venta") or "nunca").font = _f()
        if dias_sin is not None:
            ws.cell(r, 11, int(dias_sin)).font = _f()
            ws.cell(r, 11).number_format = _INT
        # Nunca vendido es distinto de "dejó de venderse": no es que se
        # enfriara, es que la compra no tenía mercado. Se pinta distinto.
        #
        # El diagnóstico DESCRIBE, no receta (Eduardo, 7-ago): qué hacer con un
        # inmovilizado —sacarlo de FULL, liquidarlo, dejar de comprarlo— depende
        # de temporada, contrato y planes que el reporte no conoce. Aquí solo va
        # el hecho. En Invisible sí se sugiere, porque ahí la acción es una sola
        # y no admite matices: la publicación está apagada teniendo stock.
        if not f.get("ultima_venta"):
            aviso = ("NUNCA HA VENDIDO — no dejó de venderse: jamás vendió una "
                     "pieza, y aun así ocupa lugar en FULL")
            ws.cell(r, 10).fill = _GRAVE_FILL
        else:
            aviso = (f"SIN VENTA EN {dias} DÍAS — lleva {int(dias_sin):,} días "
                     f"desde su última venta y sigue ocupando FULL")
            ws.cell(r, 10).fill = _AVISO_FILL
        ws.cell(r, 12, aviso).font = _f(size=9)


def _hoja_invisible(wb: Workbook, filas: list[dict], dias: int) -> None:
    ws = wb.create_sheet("Invisible")
    cabs = ["SKU", "Título", f"Vendió ({dias}d)", "Uds/día", "En bodega propia",
            "En FULL", "En FBA", "Stock total", "Cobertura (días)",
            "Publicaciones pausadas", "Cuentas", "Última venta", "Diagnóstico"]
    uds = sum(int(f.get("uds_periodo") or 0) for f in filas)
    _encabezar(ws, cabs,
               f"INVISIBLE — vendió en los últimos {dias} días y hoy NO tiene "
               f"una sola publicación activa, teniendo stock con qué surtir. "
               f"Demanda probada con el aparador cerrado. {len(filas):,} SKUs, "
               f"{uds:,} unidades vendidas que hoy no se pueden repetir. "
               f"Lo pausado SIN stock queda FUERA a propósito: está agotado, "
               f"que es la razón correcta para pausar, y pertenece a Reponer. "
               f"Es el problema más barato de arreglar: no hay que comprar ni "
               f"mover nada, solo reactivar.",
               {1: 22, 2: 46, 3: 12, 4: 9, 5: 16, 6: 10, 7: 9, 8: 12, 9: 15,
                10: 20, 11: 16, 12: 12, 13: 64})
    for i, f in enumerate(filas):
        r = 4 + i
        u = int(f.get("uds_periodo") or 0)
        stock = int(f.get("stock_total") or 0)
        vel = u / dias if dias else 0
        ws.cell(r, 1, f.get("sku") or "").font = _f()
        ws.cell(r, 2, (f.get("titulo") or "")[:120]).font = _f()
        ws.cell(r, 3, u).font = _f(bold=True)
        ws.cell(r, 4, round(vel, 2)).font = _f()
        ws.cell(r, 4).number_format = "0.00"
        for c, k in ((5, "propio"), (6, "full_total"), (7, "fba")):
            ws.cell(r, c, int(f.get(k) or 0)).font = _f()
        ws.cell(r, 8, stock).font = _f()
        # Cobertura = cuánto duraría el stock al ritmo al que SE VENDÍA. Solo
        # tiene sentido aquí porque estas filas sí tienen ventas; en
        # Inmovilizado la división sería entre cero.
        if vel > 0:
            ws.cell(r, 9, round(stock / vel)).font = _f()
            ws.cell(r, 9).number_format = _INT
        ws.cell(r, 10, int(f.get("pausadas") or 0)).font = _f()
        ws.cell(r, 11, _cuentas(f.get("cuentas"))).font = _f()
        ws.cell(r, 12, f.get("ultima_venta") or "").font = _f()
        for c in (3, 5, 6, 7, 8, 10):
            ws.cell(r, c).number_format = _INT
        ws.cell(r, 13,
                f"PAUSADA CON STOCK — vendió {u:,} unidades en {dias} días y "
                f"hoy tiene {stock:,} disponibles sin ninguna publicación "
                f"activa; reactivar o entender por qué se pausó"
                ).font = _f(size=9)
        ws.cell(r, 8).fill = _AVISO_FILL


def construir(inmovilizado: list[dict], invisible: list[dict],
              dias: int, cuenta: str | None) -> bytes:
    wb = Workbook()
    portada = wb["Sheet"]
    portada.title = "Cómo leer"
    portada["A1"] = "Inventario accionable"
    portada["A1"].font = _f(bold=True, size=12)
    portada["A2"] = (f"Período: últimos {dias} días · "
                     f"Cuenta: {TIENDA.get(cuenta or '', cuenta) or 'todas'}")
    portada["A2"].font = _f(bold=True)
    lineas = [
        "",
        "Este libro NO es el inventario completo: son las dos poblaciones "
        "sobre las que se puede actuar hoy, y son problemas opuestos.",
        "",
        f"INMOVILIZADO ({len(inmovilizado):,} SKUs) — el mercado no lo quiere. "
        "Hay stock en FULL y no vende, así que paga renta todos los días sin "
        "devolver nada. La acción es sacarlo de FULL, liquidarlo o dejar de "
        "comprarlo.",
        "",
        f"INVISIBLE ({len(invisible):,} SKUs) — el mercado sí lo quiere y no se "
        "lo estamos ofreciendo. Vendió, tiene stock, y ninguna publicación "
        "está activa. La acción es reactivar, o entender por qué se pausó.",
        "",
        "SIN VALOR EN DINERO, a propósito: el costo capturado es un precio de "
        "lista en dólares en cerca de un tercio del catálogo, así que "
        "valorizar el inventario daría una cifra inventada. Lo que sí es "
        "medible: cuánto hay, dónde está y cuánto lleva sin moverse.",
        "",
        "El stock propio se cuenta UNA vez por SKU, no por publicación: la "
        "misma bodega se ve desde cada publicación, y sumarlas contaría la "
        "misma pieza varias veces. FULL y FBA sí son por cuenta.",
    ]
    for i, t in enumerate(lineas, start=4):
        portada.cell(i, 1, t).font = _f(size=10)
        portada.cell(i, 1).alignment = Alignment(wrap_text=True, vertical="top")
    portada.column_dimensions["A"].width = 118

    _hoja_inmovilizado(wb, inmovilizado, dias)
    _hoja_invisible(wb, invisible, dias)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
