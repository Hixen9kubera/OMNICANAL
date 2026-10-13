"""
servicio.py — La configuración del módulo y el camino de un pedido (Odoo + Slack + su archivo).

No hay base de datos, a propósito: el módulo es público y lo único que guarda son archivos en su
propio volumen (`DATOS/pedidos/<folio>.json`). Las tiendas autorizadas y sus códigos viven en una
variable; las órdenes, en Odoo; el aviso, en Slack.

`procesar` BLOQUEA (xmlrpc, httpx): se llama desde un hilo.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import ofertas as O
import pedidos as P
from odoo import Odoo

log = logging.getLogger("mayoreo")

# Los mismos que `backend/services/odoo_ventas.py::_ALMACENES`, en su orden de preferencia.
# DROP OFF (142) queda fuera: de ahí no se surten órdenes de venta.
ALMACENES_OMISION = "135:TEXCO,150:TEXCO II"


@dataclass(frozen=True)
class Tienda:
    nombre: str
    huella: str
    partner_id: int | None = None
    activa: bool = True


@dataclass
class Config:
    datos: Path
    ofertas: str = ""                     # "" = solas (activas si hay webhook de Slack) | demo | pausa
    pedidos: str = "demo"                 # demo | activo | pausa   (el camino con código de tienda y Odoo)
    confirmar: bool = False               # True = la orden se confirma sola y Odoo aparta al instante
    vigencia: str = ""
    tiendas: list[Tienda] = field(default_factory=list)
    partner_id: int | None = None
    admin_key: str = ""
    odoo_url: str = ""
    odoo_db: str = ""
    odoo_user: str = ""
    odoo_password: str = ""
    slack: str = ""
    almacenes: list[tuple[int, str]] = field(default_factory=list)

    @classmethod
    def del_entorno(cls, env: Mapping[str, str] = os.environ) -> "Config":
        def v(clave: str, defecto: str = "") -> str:
            return (env.get(clave) or defecto).strip()

        tiendas = []
        try:
            for t in json.loads(v("MAYOREO_TIENDAS", "[]")):
                tiendas.append(Tienda(nombre=str(t["nombre"]), huella=str(t["hash"]).lower(),
                                      partner_id=int(t["partner_id"]) if t.get("partner_id") else None,
                                      activa=bool(t.get("activa", True))))
        except (ValueError, KeyError, TypeError) as exc:
            log.error("MAYOREO_TIENDAS no se pudo leer (%s): el módulo arranca SIN tiendas autorizadas", exc)
            tiendas = []
        almacenes = []
        for trozo in v("MAYOREO_ALMACENES", ALMACENES_OMISION).split(","):
            ident, _, nombre = trozo.partition(":")
            if ident.strip().isdigit():
                almacenes.append((int(ident), nombre.strip() or ident.strip()))
        modo = v("MAYOREO_PEDIDOS", "demo").lower()
        ofertas = v("MAYOREO_OFERTAS").lower()
        return cls(datos=Path(v("MAYOREO_DATOS", "datos")).resolve(),
                   ofertas=ofertas if ofertas in ("", "demo", "pausa") else "pausa",
                   pedidos=modo if modo in ("demo", "activo", "pausa") else "pausa",
                   confirmar=v("MAYOREO_CONFIRMAR").lower() in ("1", "true", "si", "sí"),
                   vigencia=v("MAYOREO_VIGENCIA")[:60], tiendas=tiendas,
                   partner_id=int(v("MAYOREO_PARTNER_ID")) if v("MAYOREO_PARTNER_ID").isdigit() else None,
                   admin_key=v("MAYOREO_ADMIN_KEY"), odoo_url=v("ODOO_URL"), odoo_db=v("ODOO_DB"),
                   odoo_user=v("ODOO_USER"), odoo_password=v("ODOO_PASSWORD"), slack=v("SLACK_WEBHOOK_CATALOGO"),
                   almacenes=almacenes)

    def faltantes(self) -> list[str]:
        """Lo que falta para poder recibir pedidos de verdad. Con algo pendiente, NO se reciben."""
        f = []
        if not all((self.odoo_url, self.odoo_db, self.odoo_user, self.odoo_password)):
            f.append("las credenciales de Odoo (ODOO_URL, ODOO_DB, ODOO_USER, ODOO_PASSWORD)")
        if not self.slack:
            f.append("el webhook del canal de Slack (SLACK_WEBHOOK_CATALOGO)")
        if not any(t.activa for t in self.tiendas):
            f.append("al menos una tienda autorizada (MAYOREO_TIENDAS)")
        if any(t.activa and not (t.partner_id or self.partner_id) for t in self.tiendas):
            f.append("el cliente de Odoo de alguna tienda (su partner_id o MAYOREO_PARTNER_ID)")
        if not self.almacenes:
            f.append("los almacenes (MAYOREO_ALMACENES)")
        return f

    def modo(self) -> str:
        """`activo` solo si no falta nada; pedirlo a medias configurar deja el módulo en pausa."""
        if self.pedidos == "activo" and self.faltantes():
            return "pausa"
        return self.pedidos

    def modo_ofertas(self) -> str:
        """
        `activo` en cuanto hay a quién avisar: una oferta solo sirve si alguien la ve, así que sin el
        webhook de Slack la página se queda en demostración. `MAYOREO_OFERTAS=pausa` las detiene.
        """
        if self.ofertas in ("demo", "pausa"):
            return self.ofertas
        return "activo" if self.slack else "demo"

    def tienda_de(self, codigo: str) -> Tienda | None:
        """La tienda de ese código, esté activa o no. Compara todas las huellas, sin atajos de tiempo."""
        huella, hallada = P.huella_codigo(codigo), None
        for t in self.tiendas:
            if hmac.compare_digest(huella, t.huella):
                hallada = t
        return hallada


def ruta_pedido(cfg: Config, folio: str) -> Path:
    return cfg.datos / "pedidos" / f"{folio}.json"


def guardado(cfg: Config, folio: str) -> dict[str, Any] | None:
    """La respuesta que ya se le dio a este folio, si el pedido terminó de procesarse."""
    try:
        return json.loads(ruta_pedido(cfg, folio).read_text(encoding="utf-8"))["respuesta"]
    except (OSError, ValueError, KeyError):
        return None


def _guardar(cfg: Config, folio: str, tienda: Tienda, notas: str, subs: list[str], resp: dict[str, Any]) -> None:
    ruta = ruta_pedido(cfg, folio)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    doc = {"folio": folio, "tienda": tienda.nombre, "recibido": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "notas": notas, "subcategorias": subs, "respuesta": resp}
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ruta)


def avisar(cfg: Config, mensajes: list[str]) -> bool:
    """Los mensajes al canal del catálogo. Nunca lanza: un aviso roto no tumba un pedido ya apartado."""
    import httpx

    bien = True
    for texto in mensajes:
        try:
            r = httpx.post(cfg.slack, json={"text": texto}, timeout=10)
            if r.status_code != 200:
                bien = False
                log.warning("Slack respondió %s: %s", r.status_code, r.text[:120])
        except Exception as exc:  # noqa: BLE001
            bien = False
            log.warning("no se pudo avisar a Slack: %s", exc)
    return bien


def oferta_guardada(cfg: Config, folio: str) -> dict[str, Any] | None:
    """Lo que ya se le contestó a esta oferta, si ya se avisó y se guardó."""
    try:
        return json.loads((cfg.datos / "ofertas" / f"{folio}.json").read_text(encoding="utf-8"))["respuesta"]
    except (OSError, ValueError, KeyError):
        return None


def procesar_oferta(cfg: Config, folio: str, of: "O.Oferta") -> dict[str, Any]:
    """
    Una oferta ya validada: se avisa en Slack y se guarda. No toca Odoo ni aparta nada.

    Primero el aviso y después el archivo: si Slack no la recibió, la oferta NO se da por recibida
    (truena, la página pide reintentar y el reintento vuelve a avisar). Decirle «recibida» a una tienda
    cuya oferta nadie vio es peor que pedirle que la mande otra vez.
    """
    resp = O.respuesta(folio, of)
    if not avisar(cfg, O.mensajes(folio, of)):
        raise RuntimeError("Slack no recibió el aviso de la oferta")
    ruta = cfg.datos / "ofertas" / f"{folio}.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    doc = {"folio": folio, "recibido": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "nombre": of.nombre, "contacto": of.contacto,
           "notas": of.notas, "subcategorias_completas": of.completas, "fuera_de_odoo": of.fuera_de_odoo, "respuesta": resp}
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ruta)
    log.info("%s · oferta de %s por %s renglones (%.1f %% del catálogo)", folio, of.nombre, len(of.lineas), of.porcentaje)
    return resp


def procesar(cfg: Config, tienda: Tienda, folio: str, notas: str, lineas: list[P.Linea], subs: list[str],
             odoo: Odoo | None = None) -> dict[str, Any]:
    """
    Un pedido ya validado: aparta en Odoo lo que exista, deja lo demás por confirmar, avisa y guarda.

    Se puede repetir sin miedo: las órdenes que ya nacieron de este folio se cuentan y solo se crea
    lo que falte. Y A ODOO SE LE RELEE: una orden se da por confirmada cuando Odoo la deja en `sale`,
    no cuando se le pidió (lección de `odoo_ventas`, v0.385.0: una orden en borrador no aparta).
    """
    o = odoo or Odoo(cfg.odoo_url, cfg.odoo_db, cfg.odoo_user, cfg.odoo_password)
    productos = o.productos_por_sku([ln.sku for ln in lineas])
    de_producto = {pid: sku for sku, ids in productos.items() for pid in ids}
    por_sku = {ln.sku: ln for ln in lineas}

    # 1 · Lo que un intento anterior de este mismo folio ya dejó en Odoo.
    estado_por_orden: dict[str, str] = {}
    previas = o.ordenes_del_folio(folio)
    if previas:
        nombre = {p["id"]: p["name"] for p in previas}
        estado_por_orden.update({p["name"]: p["state"] for p in previas})
        for ol in o.lineas_de(list(nombre)):
            ln = por_sku.get(de_producto.get((ol.get("product_id") or [None])[0], ""))
            if ln is not None:
                ln.colocado.append((nombre[ol["order_id"][0]], int(ol.get("product_uom_qty") or 0)))

    # 2 · Lo que falta, contra el libre de ESTE momento.
    ids = [pid for ln in lineas for pid in productos.get(ln.sku, [])]
    partes = P.repartir(lineas, productos, o.libre_por_almacen(ids, cfg.almacenes), cfg.almacenes)

    # 3 · Una orden por almacén con piezas. Cada una con su referencia: si la segunda falla, la
    #     primera ya quedó y el reintento crea solo la que falta.
    cliente = tienda.partner_id or cfg.partner_id
    total = len(previas) + len(partes)
    for n, (almacen, renglones) in enumerate(partes.items(), len(previas) + 1):
        orden_id = o.crear_orden({
            "partner_id": cliente, "warehouse_id": almacen, "client_order_ref": f"{folio}#{n}",
            "origin": f"Mayoreo {folio} · {tienda.nombre}"[:120],
            "note": P.nota_orden(tienda.nombre, folio, notas, n, total),
            "order_line": [(0, 0, {"product_id": pid, "product_uom_qty": piezas, "price_unit": ln.precio,
                                   "name": f"[{ln.sku}] {ln.titulo}"[:400]}) for ln, pid, piezas in renglones],
        })
        if cfg.confirmar:
            try:
                o.confirmar(orden_id)
            except Exception as exc:  # noqa: BLE001 — la orden existe; se reporta como quedó
                log.warning("%s: Odoo no confirmó la orden %s (%s)", folio, orden_id, exc)
        leida = o.leer_orden(orden_id)
        estado_por_orden[leida["name"]] = leida["state"]
        for ln, _pid, piezas in renglones:
            ln.colocado.append((leida["name"], piezas))

    # 4 · Qué pasó de verdad, el aviso y el archivo.
    estado = P.cerrar(lineas, cfg.confirmar, estado_por_orden)
    ordenes = list(estado_por_orden)
    resp = P.respuesta(folio, estado, lineas, ordenes)
    log.info("%s · %s · %s · %s renglones · órdenes %s", folio, tienda.nombre, estado, len(lineas), ordenes or "ninguna")
    avisar(cfg, P.mensajes_slack(tienda.nombre, folio, estado, lineas, ordenes, notas, subs))
    _guardar(cfg, folio, tienda, notas, subs, resp)
    return resp
