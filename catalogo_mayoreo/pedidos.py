"""
pedidos.py — Las reglas de un pedido de mayoreo, SIN red.

Todo lo que decide algo vive aquí y se puede probar sin Odoo ni Slack: qué se acepta del navegador,
cómo se reparten las piezas entre almacenes, qué se le contesta a la tienda y qué se avisa al equipo.
`servicio.py` es quien habla con Odoo y con Slack, y solo hace lo que estas funciones le dicen.

LAS REGLAS
  · Del navegador solo se cree el SKU. El precio y el tamaño del lote salen del catálogo que este
    mismo servicio publica; un precio en el cuerpo se ignora.
  · NO SE VENDEN PIEZAS SUELTAS: la cantidad de cada renglón es el lote completo del catálogo. Si no
    coincide, la página trae un catálogo viejo (409) o alguien armó el pedido a mano (422).
  · Se aparta lo que haya: min(lote del catálogo, libre en Odoo en este momento). Nunca más que el
    lote que la tienda vio.
  · Un SKU que NO existe en Odoo no se rechaza: queda «por confirmar» y se avisa en Slack para que
    alguien lo aparte a mano (Brandon, 12-oct-2026).
"""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

MAX_LINEAS = 2000                # renglones por pedido (la subcategoría más grande ronda los 1,300)
MAX_NOTAS = 500
_LLAVE = re.compile(r"^[A-Za-z0-9-]{8,80}$")
CONFIRMADA = ("sale", "done")


class Rechazo(Exception):
    """El pedido no se puede aceptar. `status` y `codigo` son lo que ve la página."""

    def __init__(self, status: int, codigo: str, mensaje: str):
        super().__init__(mensaje)
        self.status, self.codigo, self.mensaje = status, codigo, mensaje


@dataclass(frozen=True)
class Producto:
    sku: str
    titulo: str
    precio: float | None
    piezas: int
    en_odoo: bool
    categoria: str = ""
    subcategoria: str = ""


@dataclass
class Catalogo:
    version: str
    corte: str
    productos: dict[str, Producto]

    @classmethod
    def de_json(cls, doc: dict[str, Any]) -> "Catalogo":
        rutas = doc.get("K") or []
        prods = {}
        for f in doc["P"]:
            cat, sub = rutas[f[2]] if isinstance(f[2], int) and 0 <= f[2] < len(rutas) else ("", "")
            prods[f[0]] = Producto(sku=f[0], titulo=f[1], precio=f[3], piezas=int(f[5]), en_odoo=bool(f[7]),
                                   categoria=cat, subcategoria=sub)
        return cls(version=str(doc.get("version") or ""), corte=str(doc.get("corte") or ""), productos=prods)


@dataclass
class Linea:
    sku: str
    pedido: int
    precio: float
    titulo: str
    en_odoo: bool = False                                   # ¿existe el producto en Odoo AHORA?
    colocado: list[tuple[str, int]] = field(default_factory=list)       # (orden de Odoo, piezas)
    estado: str = ""
    apartado: int | None = None

    @property
    def apartable(self) -> int:
        return sum(n for _orden, n in self.colocado)


def huella_codigo(codigo: str) -> str:
    """Los códigos de tienda nunca se guardan: se guarda y se compara su SHA-256."""
    return hashlib.sha256(codigo.strip().upper().encode("utf-8")).hexdigest()


def folio_de(huella_tienda: str, llave: str) -> str:
    """El folio sale de la tienda y de la llave del pedido: reenviar el mismo pedido da el mismo folio."""
    crudo = hashlib.sha256(f"{huella_tienda}:{llave}".encode("utf-8")).digest()
    return "MAY-" + base64.b32encode(crudo)[:8].decode("ascii")


def texto(valor: Any, tope: int) -> str:
    """Un texto libre del navegador: una sola línea, sin caracteres de control y con tope."""
    if valor is None:
        return ""
    if not isinstance(valor, str):
        raise Rechazo(422, "pedido_invalido", "Uno de los datos no se pudo leer.")
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", valor)).strip()[:tope]


def leer_llave(cuerpo: Any) -> str:
    """La llave que hace que reenviar lo mismo no lo duplique."""
    if not isinstance(cuerpo, dict):
        raise Rechazo(422, "pedido_invalido", "El pedido no se pudo leer.")
    llave = cuerpo.get("llave")
    if not isinstance(llave, str) or not _LLAVE.match(llave):
        raise Rechazo(422, "pedido_invalido", "El pedido llegó sin su llave. Recarga la página y vuelve a armarlo.")
    return llave


def leer_lineas(cuerpo: dict[str, Any], catalogo: Catalogo) -> list[Linea]:
    """Los renglones, contra el catálogo publicado: SKU que existe, con precio y por su lote completo."""
    crudas = cuerpo.get("lineas")
    if not isinstance(crudas, list) or not crudas:
        raise Rechazo(422, "pedido_invalido", "El pedido llegó vacío.")
    if len(crudas) > MAX_LINEAS:
        raise Rechazo(413, "limite_pedido", f"El máximo es {MAX_LINEAS:,} productos por pedido. Divídelo en dos.")
    viejo = str(cuerpo.get("version") or "") != catalogo.version
    lineas, vistos = [], set()
    for c in crudas:
        sku = c.get("sku") if isinstance(c, dict) else None
        cantidad = c.get("cantidad") if isinstance(c, dict) else None
        # El SKU no se valida por su forma (los hay con «*», comillas o «Ñ»): se valida porque está en el catálogo.
        if not isinstance(sku, str) or not 0 < len(sku) <= 80 or isinstance(cantidad, bool) or not isinstance(cantidad, int):
            raise Rechazo(422, "pedido_invalido", "El pedido trae un renglón que no se pudo leer.")
        p = catalogo.productos.get(sku)
        if sku in vistos or p is None or p.precio is None or cantidad != p.piezas:
            # Con el catálogo al día esto solo pasa si alguien armó el pedido a mano.
            if viejo:
                raise Rechazo(409, "catalogo_viejo", "Hay un corte de existencias más reciente. Recarga la página y revisa tu pedido antes de enviarlo.")
            raise Rechazo(422, "pedido_invalido", f"El renglón {sku} no coincide con el catálogo: aquí solo se llevan lotes completos.")
        vistos.add(sku)
        lineas.append(Linea(sku=sku, pedido=p.piezas, precio=float(p.precio), titulo=p.titulo))
    return lineas


def validar(cuerpo: Any, catalogo: Catalogo) -> tuple[str, str, list[Linea], list[str]]:
    """Del cuerpo del navegador a renglones confiables. Devuelve (llave, notas, renglones, subcategorías)."""
    llave = leer_llave(cuerpo)
    lineas = leer_lineas(cuerpo, catalogo)
    notas = texto(cuerpo.get("notas"), MAX_NOTAS)
    subs = []
    for s in (cuerpo.get("subcategorias") or [])[:60] if isinstance(cuerpo.get("subcategorias"), list) else []:
        if isinstance(s, dict) and isinstance(s.get("subcategoria"), str):
            subs.append(f"{s['subcategoria'][:80]} ({str(s.get('categoria') or '')[:80]})")
    return llave, notas, lineas, subs


def repartir(lineas: list[Linea], productos: dict[str, list[int]], libres: dict[int, dict[int, float]],
             almacenes: list[tuple[int, str]]) -> dict[int, list[tuple[Linea, int, int]]]:
    """
    Qué piezas de cada renglón salen de qué almacén. Devuelve {almacén: [(renglón, producto, piezas)]}.

    Se llena por orden de preferencia de almacén, hasta min(lote, libre). Un SKU con piezas en dos
    almacenes queda en dos órdenes: el lote completo ES todo lo que haya, esté donde esté.

    Lo que el renglón ya tiene `colocado` (órdenes de un intento anterior que se quedó a medias) se
    descuenta: reintentar el mismo pedido solo crea lo que falta.
    """
    partes: dict[int, list[tuple[Linea, int, int]]] = {}
    for ln in lineas:
        ids = productos.get(ln.sku) or []
        ln.en_odoo = bool(ids)
        falta = ln.pedido - ln.apartable
        for almacen, _nombre in almacenes:
            for pid in ids:
                n = min(falta, max(0, int(libres.get(pid, {}).get(almacen, 0) or 0)))
                if n > 0:
                    partes.setdefault(almacen, []).append((ln, pid, n))
                    falta -= n
    return {a: partes[a] for a, _n in almacenes if a in partes}


def cerrar(lineas: list[Linea], confirmar: bool, estado_por_orden: dict[str, str]) -> str:
    """Pone el estado de cada renglón con lo que Odoo DEJÓ (no con lo que se le pidió) y devuelve el del pedido."""
    for ln in lineas:
        if not ln.en_odoo:
            ln.estado, ln.apartado = "por_confirmar", None
        elif ln.apartable <= 0:
            ln.estado, ln.apartado = "sin_existencia", 0
        elif confirmar and all(estado_por_orden.get(orden) in CONFIRMADA for orden, _n in ln.colocado):
            ln.estado, ln.apartado = ("apartado" if ln.apartable >= ln.pedido else "parcial"), ln.apartable
        else:
            ln.estado, ln.apartado = "en_revision", None     # la orden está en borrador: todavía no aparta
    estados = {ln.estado for ln in lineas}
    if estados <= {"sin_existencia"}:
        return "sin_existencia"
    if "en_revision" in estados:
        return "en_revision"
    if not estados & {"apartado", "parcial"}:
        return "por_confirmar"
    return "apartado" if estados <= {"apartado", "por_confirmar"} else "parcial"


def respuesta(folio: str, estado: str, lineas: list[Linea], ordenes: list[str], vence: str | None = None) -> dict[str, Any]:
    """Lo que ve la tienda. Nada de almacenes, ids de Odoo ni existencias de otros productos."""
    return {
        "folio": folio, "estado": estado, "ordenes": ordenes, "vence": vence,
        "lineas": [{"sku": ln.sku, "pedido": ln.pedido, "apartado": ln.apartado, "precio": ln.precio, "estado": ln.estado}
                   for ln in lineas],
        "piezas": sum(ln.apartado or 0 for ln in lineas),
        "total": round(sum((ln.apartado or 0) * ln.precio for ln in lineas), 2),
        "pedido_piezas": sum(ln.pedido for ln in lineas),
        "pedido_total": round(sum(ln.pedido * ln.precio for ln in lineas), 2),
    }


def nota_orden(tienda: str, folio: str, notas: str, parte: int, de: int) -> str:
    """El texto de la orden en Odoo. Va en plano: Odoo sanea el HTML de `note` y se come los comentarios."""
    texto = f"Pedido del catálogo de mayoreo. Tienda: {tienda}. Folio {folio}."
    if de > 1:
        texto += f" Surtido en {de} almacenes: parte {parte} de {de}."
    if notas:
        texto += f" Notas de la tienda: {notas}"
    return texto


_TITULOS = {
    "apartado": ":white_check_mark: *Apartado en Odoo*",
    "parcial": ":large_orange_circle: *Apartado parcial en Odoo*",
    "en_revision": ":hourglass_flowing_sand: *En borrador en Odoo: falta CONFIRMAR la orden para que aparte*",
    "por_confirmar": ":warning: *Nada en Odoo: todo se aparta a mano*",
    "sin_existencia": ":no_entry_sign: *Sin existencia: no se creó ninguna orden*",
}


def _dinero(n: float) -> str:
    return f"${n:,.2f}"


def mensajes_slack(tienda: str, folio: str, estado: str, lineas: list[Linea], ordenes: list[str],
                   notas: str, subs: list[str], limite: int = 3500) -> list[str]:
    """
    Lo que se avisa en el canal del catálogo. Varios mensajes si no cabe en uno.

    Lo que NO existe en Odoo va completo, renglón por renglón: es lo único que alguien tiene que
    apartar a mano, y sin la lista no puede. De lo demás basta el resumen (el detalle está en la orden).
    """
    r = respuesta(folio, estado, lineas, ordenes)
    cab = [f":package: *Pedido de mayoreo {folio}* · {tienda}", _TITULOS.get(estado, estado)
           + (f" · {' + '.join(ordenes)}" if ordenes else ""),
           f"{len(lineas):,} productos · {r['pedido_piezas']:,} piezas pedidas · {_dinero(r['pedido_total'])}"]
    if estado in ("apartado", "parcial"):
        cab.append(f"Apartado: {r['piezas']:,} piezas · {_dinero(r['total'])}")
    if subs:
        cab.append("Subcategorías completas: " + "; ".join(subs))
    if notas:
        cab.append(f"Notas de la tienda: {notas}")
    cortos = [ln for ln in lineas if ln.estado in ("parcial", "sin_existencia")]
    if cortos:
        cab.append(f"*Incompletos o sin existencia* ({len(cortos):,}):")
        cab += [f"• `{ln.sku}` pidió {ln.pedido:,}, hay {ln.apartable:,}" for ln in cortos[:40]]
        if len(cortos) > 40:
            cab.append(f"… y {len(cortos) - 40:,} más (están en la respuesta guardada del folio).")
    manuales = [ln for ln in lineas if ln.estado == "por_confirmar"]
    renglones = []
    if manuales:
        renglones.append(f":warning: *NO EXISTEN EN ODOO: apartar a mano* ({len(manuales):,} productos · "
                         f"{sum(ln.pedido for ln in manuales):,} piezas)")
        renglones += [f"• `{ln.sku}` × {ln.pedido:,} · {ln.titulo[:70]}" for ln in manuales]
    mensajes, actual = [], ""
    for renglon in cab + renglones:
        if actual and len(actual) + len(renglon) + 1 > limite:
            mensajes.append(actual)
            actual = f"_(sigue {folio})_"
        actual = f"{actual}\n{renglon}" if actual else renglon
    if actual:
        mensajes.append(actual)
    return mensajes
