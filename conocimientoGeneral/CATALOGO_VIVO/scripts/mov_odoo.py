"""
mov_odoo.py — Lo que Odoo MOVIÓ de verdad, por SKU. Solo lecturas (`read_group`, `search_read`).

Por qué movimientos y no ventas registradas
-------------------------------------------
Una venta «registrada» (un pedido de un canal, una orden de venta) dice lo que se
pidió. Un movimiento hecho (`stock.move.line` en estado `done`) dice lo que SALIÓ
de la bodega. Para descontar piezas de lo que se compró, cuenta lo segundo.

Qué se lee
----------
Solo los movimientos que cruzan la frontera de la bodega (un lado es una
ubicación interna y el otro no). Los traslados de rack a rack no cambian cuánto
hay y son la mitad del historial.

  entra de…   proveedor · ajuste de inventario · tránsito entre almacenes · devolución de cliente
  sale a…     cliente · ajuste de inventario · merma (scrap) · proveedor · tránsito

«Cliente» incluye los envíos a bodegas de marketplace (Full, FBA, WFS): en Odoo
son entregas a un socio, igual que una venta. Por eso se guarda también el
reparto por socio, para poder separarlos.

El historial se junta por SKU sumando TODOS los productos que llevan esa
referencia, activos o archivados: aquí no se cuenta existencia, se cuenta
historia, y una venta hecha con un producto que después se archivó sigue siendo
una venta.

También lee las compras (`purchase.order.line`): cuántas piezas se pidieron y
cuántas se recibieron de cada SKU, y en qué orden. Sirve para empatar un SKU con
su renglón del packing list cuando ni la foto ni el título alcanzan.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from comun import Cfg, ahora_iso, aviso, escribir_json, sku_norm
from f_odoo import Odoo

HECHO = ["state", "=", "done"]
FRONTERA = ["|", ["location_id.usage", "!=", "internal"], ["location_dest_id.usage", "!=", "internal"]]


def _clase(u: dict[str, Any] | None) -> str:
    """A qué bolsa va una ubicación."""
    if not u:
        return "otro"
    uso = u["usage"]
    if uso == "inventory":
        return "merma" if "scrap" in (u["complete_name"] or "").lower() else "ajuste"
    return {"internal": "bodega", "customer": "cliente", "supplier": "proveedor",
            "transit": "transito", "production": "produccion"}.get(uso, "otro")


def _paginas(o: Odoo, modelo: str, dominio: list, campos: list[str], lote: int = 2000,
             **kw: Any) -> list[dict]:
    todo: list[dict] = []
    while True:
        trozo = o.leer(modelo, dominio, campos, limit=lote, offset=len(todo), order="id asc", **kw)
        todo.extend(trozo)
        if len(trozo) < lote:
            return todo


def extraer(cfg: Cfg, salida: Path) -> dict[str, Any]:
    o = Odoo(cfg)
    inicio = ahora_iso()
    todos = {"active_test": False}

    ubic = {u["id"]: u for u in _paginas(o, "stock.location", [["active", "in", [True, False]]],
                                         ["complete_name", "usage"], lote=5000, context=todos)}
    aviso(f"movimientos: {len(ubic)} ubicaciones")
    productos = {p["id"]: p for p in _paginas(
        o, "product.product", [["active", "in", [True, False]]],
        ["default_code", "active", "name", "product_tmpl_id"], context=todos)}
    aviso(f"movimientos: {len(productos)} productos (activos y archivados)")

    # La HUELLA de la foto de cada producto (sha1 del archivo, sin bajarlo): la foto que
    # Odoo guarda suele ser el mismo archivo que viene incrustado en el packing list, y
    # con eso se empata un SKU con su renglón. Los adjuntos cuelgan de la plantilla; si
    # la variante tiene foto propia, gana la de la variante.
    por_plantilla = {a["res_id"]: a["checksum"] for a in _paginas(
        o, "ir.attachment", [["res_model", "=", "product.template"], ["res_field", "=", "image_1920"]],
        ["res_id", "checksum"], lote=5000)}
    por_variante = {a["res_id"]: a["checksum"] for a in _paginas(
        o, "ir.attachment", [["res_model", "=", "product.product"], ["res_field", "=", "image_variant_1920"]],
        ["res_id", "checksum"], lote=5000)}
    fotos: dict[str, str] = {}
    for pid, p in productos.items():
        sku = sku_norm(p.get("default_code"))
        if not sku or not p.get("active"):
            continue
        h = por_variante.get(pid) or por_plantilla.get((p.get("product_tmpl_id") or [None])[0])
        if h:
            fotos[sku] = h
    escribir_json(salida / "datos" / "odoo_fotos.json", fotos)
    aviso(f"movimientos: huella de la foto de {len(fotos)} SKUs")

    def sku_de(pid: int) -> str:
        p = productos.get(pid) or {}
        return sku_norm(p.get("default_code")) or f"(SIN CÓDIGO {pid})"

    # 1 · Lo que cruzó la frontera de la bodega, por producto y par de ubicaciones.
    grupos = o.agrupar("stock.move.line", [HECHO, *FRONTERA],
                       ["quantity:sum", "date:min", "date:max"],
                       ["product_id", "location_id", "location_dest_id"])
    aviso(f"movimientos: {len(grupos)} grupos producto × origen × destino")
    por_sku: dict[str, dict[str, Any]] = defaultdict(lambda: defaultdict(float))
    fechas: dict[str, list[str | None]] = defaultdict(lambda: [None, None])
    total: dict[str, float] = defaultdict(float)
    for g in grupos:
        if not g.get("product_id"):
            continue
        origen = _clase(ubic.get(g["location_id"][0]))
        destino = _clase(ubic.get(g["location_dest_id"][0]))
        q = float(g.get("quantity") or 0)
        if origen == "bodega" and destino != "bodega":
            clave = f"sale_{destino}"
        elif destino == "bodega" and origen != "bodega":
            clave = f"entra_{origen}"
        else:
            clave = f"fuera_{origen}_{destino}"      # nunca tocó la bodega: se anota y no cuenta
        s = sku_de(g["product_id"][0])
        por_sku[s][clave] += q
        total[clave] += q
        if clave == "sale_cliente":
            f = fechas[s]
            a, b = g.get("date_min") or g.get("date"), g.get("date_max") or g.get("date")
            if a and (f[0] is None or str(a) < f[0]):
                f[0] = str(a)[:10]
            if b and (f[1] is None or str(b) > f[1]):
                f[1] = str(b)[:10]

    # 2 · Las entregas a «cliente», por socio: aquí se separan Full / FBA / WFS de la venta suelta.
    por_socio = o.agrupar("stock.move", [HECHO, ["location_dest_id.usage", "=", "customer"],
                                         ["location_id.usage", "=", "internal"]],
                          ["quantity:sum"], ["product_id", "partner_id"])
    socios: dict[str, float] = defaultdict(float)
    socio_sku: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for g in por_socio:
        if not g.get("product_id"):
            continue
        nombre = (g["partner_id"][1] if g.get("partner_id") else "(sin socio)").strip()
        q = float(g.get("quantity") or 0)
        socios[nombre] += q
        socio_sku[sku_de(g["product_id"][0])][nombre] += q

    # 3 · Las entregas a cliente por mes: para ver desde cuándo hay historia.
    por_mes = o.agrupar("stock.move.line", [HECHO, ["location_dest_id.usage", "=", "customer"],
                                            ["location_id.usage", "=", "internal"]],
                        ["quantity:sum"], ["date:month"])
    meses = [{"mes": g.get("date:month"), "piezas": float(g.get("quantity") or 0),
              "lineas": g.get("__count")} for g in por_mes]

    # 4 · Compras: qué se pidió y qué se recibió de cada SKU, y en qué orden.
    lineas = _paginas(o, "purchase.order.line", [["product_id", "!=", False]],
                      ["product_id", "order_id", "product_qty", "qty_received", "state", "date_planned"])
    ordenes = {x["id"]: x for x in _paginas(o, "purchase.order", [],
                                            ["name", "partner_id", "date_order", "origin", "partner_ref", "state"])}
    compras: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for li in lineas:
        oc = ordenes.get(li["order_id"][0]) if li.get("order_id") else None
        compras[sku_de(li["product_id"][0])].append({
            "oc": (oc or {}).get("name"), "ref": (oc or {}).get("partner_ref") or (oc or {}).get("origin") or None,
            "fecha": str((oc or {}).get("date_order") or "")[:10] or None,
            "estado": li.get("state"), "pedido": float(li.get("product_qty") or 0),
            "recibido": float(li.get("qty_received") or 0)})
    aviso(f"movimientos: {len(lineas)} renglones de compra en {len(ordenes)} órdenes")

    filas = {}
    for s, d in por_sku.items():
        fila = {k: round(v, 3) for k, v in d.items() if v}
        if fechas[s][0]:
            fila["primera_venta"], fila["ultima_venta"] = fechas[s]
        if s in socio_sku:
            fila["socios"] = {k: round(v, 3) for k, v in sorted(socio_sku[s].items(), key=lambda kv: -kv[1])}
        filas[s] = fila
    doc = {
        "fuente": "Odoo · stock.move.line / stock.move en estado done (read_group) y purchase.order.line",
        "leido_desde": inicio, "leido_hasta": ahora_iso(),
        "totales": {k: round(v) for k, v in sorted(total.items())},
        "socios": {k: round(v) for k, v in sorted(socios.items(), key=lambda kv: -kv[1])},
        "por_mes": meses, "skus": filas,
        "compras": dict(compras),
    }
    escribir_json(salida / "datos" / "movimientos.json", doc)
    aviso("movimientos: " + " · ".join(f"{k} {v:,.0f}" for k, v in sorted(total.items(), key=lambda kv: -kv[1])))
    return {"skus_con_movimiento": len(filas), "totales": doc["totales"],
            "socios_distintos": len(socios), "skus_con_compra": len(compras),
            "meses": [m["mes"] for m in meses]}
