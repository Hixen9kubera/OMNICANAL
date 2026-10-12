"""
odoo.py — Lo ÚNICO de este módulo que habla con Odoo.

XML-RPC, bloqueante: se usa desde un hilo, nunca dentro de una corrutina. Un cliente por pedido (los
`ServerProxy` no se comparten entre hilos).

Lo que hace: buscar productos por SKU, leer el libre por almacén, y crear / confirmar / releer
órdenes de venta. No toca nada más de Odoo.
"""
from __future__ import annotations

import xmlrpc.client
from typing import Any

LOTE = 500


class _ConPlazo(xmlrpc.client.SafeTransport):
    def __init__(self, plazo: float):
        super().__init__()
        self._plazo = plazo

    def make_connection(self, host):  # noqa: ANN001 — firma de la biblioteca
        conexion = super().make_connection(host)
        conexion.timeout = self._plazo
        return conexion


class _ConPlazoHttp(xmlrpc.client.Transport):
    def __init__(self, plazo: float):
        super().__init__()
        self._plazo = plazo

    def make_connection(self, host):  # noqa: ANN001
        conexion = super().make_connection(host)
        conexion.timeout = self._plazo
        return conexion


class Odoo:
    def __init__(self, url: str, db: str, usuario: str, clave: str, plazo: float = 300.0):
        self._db, self._usuario, self._clave = db, usuario, clave
        base = url.rstrip("/")
        transporte = (_ConPlazo if base.startswith("https") else _ConPlazoHttp)
        self._comun = xmlrpc.client.ServerProxy(f"{base}/xmlrpc/2/common", transport=transporte(plazo))
        self._objeto = xmlrpc.client.ServerProxy(f"{base}/xmlrpc/2/object", transport=transporte(plazo))
        self._uid: int | None = None

    def _kw(self, modelo: str, metodo: str, args: list, kwargs: dict | None = None) -> Any:
        if self._uid is None:
            self._uid = self._comun.authenticate(self._db, self._usuario, self._clave, {})
            if not self._uid:
                raise RuntimeError("Odoo: no se pudo autenticar")
        return self._objeto.execute_kw(self._db, self._uid, self._clave, modelo, metodo, args, kwargs or {})

    def productos_por_sku(self, skus: list[str]) -> dict[str, list[int]]:
        """{sku: [ids]} de los productos ACTIVOS con esa referencia. El que no existe no aparece."""
        salida: dict[str, list[int]] = {}
        for i in range(0, len(skus), LOTE):
            filas = self._kw("product.product", "search_read", [[["default_code", "in", skus[i:i + LOTE]]]],
                             {"fields": ["default_code"], "order": "id asc"})
            for f in filas:
                salida.setdefault((f["default_code"] or "").strip(), []).append(f["id"])
        return salida

    def libre_por_almacen(self, ids: list[int], almacenes: list[tuple[int, str]]) -> dict[int, dict[int, float]]:
        """{producto: {almacén: libre}}. `free_qty` ya descuenta lo apartado por otras órdenes."""
        salida: dict[int, dict[int, float]] = {i: {} for i in ids}
        for almacen, _nombre in almacenes:
            for i in range(0, len(ids), LOTE):
                for f in self._kw("product.product", "read", [ids[i:i + LOTE], ["free_qty"]],
                                  {"context": {"warehouse": almacen}}):
                    salida[f["id"]][almacen] = float(f.get("free_qty") or 0)
        return salida

    def ordenes_del_folio(self, folio: str) -> list[dict[str, Any]]:
        """Las órdenes que ya nacieron de este folio (su referencia es `FOLIO#n`), sin las canceladas."""
        return self._kw("sale.order", "search_read",
                        [[["client_order_ref", "=like", f"{folio}#%"], ["state", "!=", "cancel"]]],
                        {"fields": ["name", "state", "client_order_ref"], "order": "id asc"})

    def lineas_de(self, orden_ids: list[int]) -> list[dict[str, Any]]:
        return self._kw("sale.order.line", "search_read", [[["order_id", "in", orden_ids]]],
                        {"fields": ["order_id", "product_id", "product_uom_qty"]})

    def crear_orden(self, valores: dict[str, Any]) -> int:
        return self._kw("sale.order", "create", [valores])

    def confirmar(self, orden_id: int) -> None:
        self._kw("sale.order", "action_confirm", [[orden_id]])

    def leer_orden(self, orden_id: int) -> dict[str, Any]:
        return self._kw("sale.order", "read", [[orden_id], ["name", "state", "amount_total"]])[0]
