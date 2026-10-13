"""
ofertas.py — Las reglas de una OFERTA de mayoreo, sin red.

La tienda elige lotes completos en el catálogo y dice cuánto ofrece por todo. Aquí no se aparta nada
ni se toca Odoo: la oferta se guarda y se avisa en el canal del catálogo para que alguien la revise
y le conteste.

No pide código de tienda (Brandon, 13-oct-2026: «por el momento no lo necesitamos»), así que lo que
llega puede venir de cualquiera. Por eso:
  · del navegador solo se cree QUÉ eligió y CUÁNTO ofrece; el precio y el tamaño de cada lote salen
    del catálogo publicado;
  · los textos libres (nombre, contacto, notas) se limpian y se escapan antes de ir a Slack: un
    «<!channel>» escrito en el nombre no puede llamar a todo el canal.
"""
from __future__ import annotations

import base64
import hashlib
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pedidos as P

MAX_OFERTA = 10_000_000_000      # pesos; arriba de esto es un dedazo
MAX_SUELTOS_EN_SLACK = 60        # renglones que se listan uno por uno en el aviso


@dataclass
class Oferta:
    llave: str
    monto: float
    nombre: str
    contacto: str
    notas: str
    lineas: list[P.Linea]
    completas: list[dict[str, Any]] = field(default_factory=list)    # subcategorías que van enteras
    sueltas: list[P.Linea] = field(default_factory=list)             # lo demás, producto por producto
    fuera_de_odoo: int = 0                                           # renglones que no existen en Odoo

    @property
    def piezas(self) -> int:
        return sum(ln.pedido for ln in self.lineas)

    @property
    def total_catalogo(self) -> float:
        return round(sum(ln.pedido * ln.precio for ln in self.lineas), 2)

    @property
    def porcentaje(self) -> float:
        total = self.total_catalogo
        return round(self.monto / total * 100, 1) if total else 0.0


def folio_de(llave: str) -> str:
    """Reenviar la misma oferta da el mismo folio."""
    crudo = hashlib.sha256(f"oferta:{llave}".encode("utf-8")).digest()
    return "OFE-" + base64.b32encode(crudo)[:8].decode("ascii")


def validar(cuerpo: Any, catalogo: P.Catalogo) -> Oferta:
    """Del cuerpo del navegador a una oferta confiable."""
    llave = P.leer_llave(cuerpo)
    lineas = P.leer_lineas(cuerpo, catalogo)
    monto = cuerpo.get("oferta")
    if isinstance(monto, bool) or not isinstance(monto, (int, float)) or not math.isfinite(monto) or not 1 <= monto <= MAX_OFERTA:
        raise P.Rechazo(422, "oferta_invalida", "Escribe cuánto ofreces por todo el pedido, en pesos.")
    nombre = P.texto(cuerpo.get("nombre"), 80)
    if len(nombre) < 2:
        raise P.Rechazo(422, "falta_nombre", "Dinos tu nombre o el de tu tienda para poder responderte.")
    oferta = Oferta(llave=llave, monto=round(float(monto), 2), nombre=nombre, contacto=P.texto(cuerpo.get("contacto"), 80),
                    notas=P.texto(cuerpo.get("notas"), P.MAX_NOTAS), lineas=lineas)

    # ¿Qué subcategorías van ENTERAS? Se mide contra el catálogo, no se le cree al navegador.
    en_catalogo = Counter((p.categoria, p.subcategoria) for p in catalogo.productos.values() if p.precio is not None)
    por_sub: dict[tuple[str, str], list[P.Linea]] = {}
    for ln in lineas:
        p = catalogo.productos[ln.sku]
        por_sub.setdefault((p.categoria, p.subcategoria), []).append(ln)
        oferta.fuera_de_odoo += not p.en_odoo
    for (cat, sub), suyas in por_sub.items():
        if len(suyas) >= 2 and len(suyas) == en_catalogo[cat, sub]:
            oferta.completas.append({"categoria": cat, "subcategoria": sub, "productos": len(suyas),
                                     "piezas": sum(ln.pedido for ln in suyas),
                                     "total": round(sum(ln.pedido * ln.precio for ln in suyas), 2)})
        else:
            oferta.sueltas += suyas
    return oferta


def respuesta(folio: str, of: Oferta) -> dict[str, Any]:
    """Lo que ve quien ofertó."""
    return {"folio": folio, "estado": "en_revision", "oferta": of.monto, "productos": len(of.lineas), "piezas": of.piezas,
            "catalogo_total": of.total_catalogo, "porcentaje": of.porcentaje,
            "lineas": [{"sku": ln.sku, "piezas": ln.pedido, "precio": ln.precio} for ln in of.lineas]}


def _slack(texto: str) -> str:
    """Texto ajeno dentro de un mensaje de Slack: sin menciones, sin ligas y sin formato."""
    limpio = texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return limpio.replace("*", "∗").replace("_", "ˍ").replace("`", "'").replace("~", "∼")


def _sku(sku: str) -> str:
    """Un SKU dentro de `código`: hay SKUs con «>» o comillas, y Slack lee «<» y «>» aunque vayan entre acentos graves."""
    return sku.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("`", "'")


def _dinero(n: float) -> str:
    return f"${n:,.2f}"


def mensajes(folio: str, of: Oferta, limite: int = 3500) -> list[str]:
    """El aviso al canal del catálogo: quién, cuánto ofrece y por qué productos. Varios mensajes si no cabe."""
    total = of.total_catalogo
    renglones = [
        f":moneybag: *Oferta de mayoreo {folio}*",
        f"*{_slack(of.nombre)}* ofrece *{_dinero(of.monto)}* por *{len(of.lineas):,} productos · {of.piezas:,} piezas*",
        f"Precio de catálogo: {_dinero(total)} · la oferta es el *{of.porcentaje:.1f} %* "
        f"({_dinero(abs(total - of.monto))} {'abajo' if of.monto < total else 'arriba'} del catálogo)",
    ]
    if of.contacto:
        renglones.append(f"Contacto: {_slack(of.contacto)}")
    if of.notas:
        renglones.append(f"Notas: {_slack(of.notas)}")
    if of.completas:
        renglones.append("*Subcategorías completas:*")
        renglones += [f"• {_slack(c['subcategoria'])} ({_slack(c['categoria'])}): {c['productos']:,} productos · "
                      f"{c['piezas']:,} pzs · {_dinero(c['total'])}" for c in of.completas]
    if of.sueltas:
        renglones.append("*Productos:*")
        renglones += [f"• `{_sku(ln.sku)}` × {ln.pedido:,} · {_slack(ln.titulo[:70])} · {_dinero(ln.precio)} c/u"
                      for ln in of.sueltas[:MAX_SUELTOS_EN_SLACK]]
        if len(of.sueltas) > MAX_SUELTOS_EN_SLACK:
            renglones.append(f"… y {len(of.sueltas) - MAX_SUELTOS_EN_SLACK:,} productos más (el detalle completo está guardado con el folio).")
    if of.fuera_de_odoo:
        renglones.append(f"_{of.fuera_de_odoo:,} de estos productos no existen en Odoo: si se acepta, se apartan a mano._")
    mensajes_, actual = [], ""
    for renglon in renglones:
        if actual and len(actual) + len(renglon) + 1 > limite:
            mensajes_.append(actual)
            actual = f"_(sigue {folio})_"
        actual = f"{actual}\n{renglon}" if actual else renglon
    if actual:
        mensajes_.append(actual)
    return mensajes_
